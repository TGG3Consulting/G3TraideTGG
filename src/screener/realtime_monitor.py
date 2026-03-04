# -*- coding: utf-8 -*-
"""
Real-Time Monitor - мониторинг пар через WebSocket.
"""

import asyncio
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Optional
import structlog

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError:
    websockets = None
    ConnectionClosed = Exception

try:
    import aiohttp
except ImportError:
    aiohttp = None

from config.settings import settings
from .models import SymbolState, Trade


logger = structlog.get_logger(__name__)


# Type alias for async callback
AsyncStateCallback = Callable[[SymbolState], Any]


class RealTimeMonitor:
    """
    Мониторинг уязвимых пар в реальном времени через WebSocket.

    Streams:
    - @trade: все сделки
    - @depth20@100ms: snapshot топ-20 уровней стакана
    - @kline_1m: минутные свечи

    Использование:
        monitor = RealTimeMonitor(on_state_update=my_callback)
        await monitor.warmup_baselines(["BTCUSDT", "ETHUSDT"])  # Прогрев!
        await monitor.start(["BTCUSDT", "ETHUSDT"])
        ...
        await monitor.stop()
    """

    # =========================================================================
    # ПАРАМЕТРЫ ЗАГРУЖАЮТСЯ ИЗ config/settings.py (settings.websocket.*)
    # Для изменения — редактировать config/config.yaml
    # =========================================================================

    # FIX-SPREAD-2: буфер для effective_depth когда spread > ATR
    # effective_depth = max(atr_pct, spread_pct * DEPTH_SPREAD_BUFFER)
    DEPTH_SPREAD_BUFFER: float = 1.2

    # FIX-ATR-RAW: clamp для ATR используемого в orderbook depth
    ATR_DEPTH_MIN_PCT: Decimal = Decimal("0.5")
    ATR_DEPTH_MAX_PCT: Decimal = Decimal("20")

    def __init__(
        self,
        on_state_update: Optional[Callable[[SymbolState], None]] = None,
        on_trade: Optional[Callable[[str, Trade], None]] = None,
    ):
        """
        Args:
            on_state_update: Callback при обновлении состояния (для детекции)
            on_trade: Callback для каждого трейда (опционально)
        """
        if websockets is None:
            raise ImportError("websockets package is required. Install: pip install websockets")

        self._symbols: list[str] = []
        self._states: dict[str, SymbolState] = {}
        self._connections: list = []
        self._tasks: list[asyncio.Task] = []
        self._running = False

        # FIX-TASK-1: tracking set для fire-and-forget callback tasks
        # Предотвращает GC до завершения + логирует exceptions
        self._callback_tasks: set[asyncio.Task] = set()

        self._on_state_update = on_state_update
        self._on_trade = on_trade

        # Counters for minute/hour resets
        self._last_minute_reset: dict[str, int] = defaultdict(int)
        self._last_5min_reset: dict[str, int] = defaultdict(int)
        self._last_hour_reset: dict[str, int] = defaultdict(int)

        # HTTP session for REST calls (warmup)
        self._http_session: Optional[aiohttp.ClientSession] = None

    def _spawn_callback_task(self, coro) -> asyncio.Task:
        """
        FIX-TASK-1: Безопасный fire-and-forget для callback coroutines.

        Паттерн решает три проблемы:
        1. Task reference сохраняется → GC не соберёт до завершения
        2. Exceptions логируются через structlog (не пропадают молча)
        3. Completed tasks автоматически удаляются из set (no memory leak)
        """
        task = asyncio.create_task(coro)
        self._callback_tasks.add(task)

        def _on_task_done(t: asyncio.Task) -> None:
            self._callback_tasks.discard(t)
            # FIX-TASK-2: порядок зафиксирован short-circuit evaluation —
            # cancelled() проверяется ДО exception(), иначе exception() бросит CancelledError
            if not t.cancelled() and (exc := t.exception()) is not None:
                logger.error(
                    "callback_task_exception",
                    task_name=t.get_name(),
                    error=str(exc),
                    exc_info=exc,
                )

        task.add_done_callback(_on_task_done)
        return task

    async def warmup_baselines(self, symbols: list[str]) -> int:
        """
        Прогреть baselines перед стартом мониторинга.
        Загружает последний час свечей для расчёта avg_volume.

        КРИТИЧНО: Без прогрева система не детектирует volume spikes первый час!

        Args:
            symbols: Список пар для прогрева

        Returns:
            Количество успешно прогретых пар
        """
        if not symbols:
            return 0

        if aiohttp is None:
            logger.warning("aiohttp not installed, skipping warmup")
            return 0

        logger.info("warming_up_baselines", symbols=len(symbols))

        # Инициализировать состояния
        for symbol in symbols:
            if symbol not in self._states:
                self._states[symbol] = SymbolState(symbol=symbol)

        # Загрузить klines параллельно с ограничением
        semaphore = asyncio.Semaphore(settings.websocket.warmup_concurrent)
        tasks = [
            self._fetch_klines_safe(symbol, semaphore)
            for symbol in symbols
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Подсчёт успешных
        success_count = sum(1 for r in results if r is True)
        fail_count = sum(1 for r in results if isinstance(r, Exception))

        logger.info(
            "baseline_warmup_complete",
            success=success_count,
            failed=fail_count,
            total=len(symbols)
        )

        return success_count

    async def _fetch_klines_safe(
        self,
        symbol: str,
        semaphore: asyncio.Semaphore
    ) -> bool:
        """Загрузить klines с ограничением параллельности."""
        async with semaphore:
            return await self._fetch_klines(symbol)

    async def _fetch_klines(self, symbol: str) -> bool:
        """
        Загрузить исторические klines для расчёта baseline.
        """
        url = f"{settings.websocket.rest_url}/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": "1m",
            "limit": settings.websocket.warmup_klines,  # Последний час
        }

        try:
            session = await self._get_http_session()
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()

            if not data:
                return False

            state = self._states.get(symbol)
            if not state:
                return False

            # Расчёт baseline из klines
            # Формат kline: [open_time, open, high, low, close, volume, close_time, quote_volume, ...]
            volumes = []
            hlc_data = []  # (high, low, close) для ATR
            for kline in data:
                try:
                    # Quote volume (в USDT)
                    quote_vol = Decimal(str(kline[7]))
                    volumes.append(quote_vol)

                    # HLC для ATR calculation
                    high = Decimal(str(kline[2]))
                    low = Decimal(str(kline[3]))
                    close = Decimal(str(kline[4]))
                    hlc_data.append((high, low, close))
                except (IndexError, ValueError):
                    continue

            if volumes:
                # CALC-1 FIX: Средний объём за минуту (не сумма!)
                total_volume = sum(volumes)
                state.avg_volume_1h = total_volume / len(volumes)  # БЫЛО: total_volume (неправильно!)

                # Инициализировать цены
                if data:
                    last_kline = data[-1]
                    state.last_price = Decimal(str(last_kline[4]))  # close
                    state.price_1m_ago = state.last_price
                    state.price_5m_ago = state.last_price
                    state.price_1h_ago = Decimal(str(data[0][1]))  # open первой свечи

                # Инициализировать klines для ATR
                if hlc_data:
                    state.klines_1h = hlc_data[-60:]  # Последние 60 свечей
                    # Рассчитать ATR сразу
                    if len(state.klines_1h) >= 15:
                        # FIX-ATR-RAW: сохраняем raw и clamped отдельно
                        raw_atr = self._calculate_atr_pct(state.klines_1h)
                        state.atr_1h_pct_raw = raw_atr  # реальный ATR для SL/TP
                        state.atr_1h_pct = max(
                            self.ATR_DEPTH_MIN_PCT,
                            min(self.ATR_DEPTH_MAX_PCT, raw_atr)
                        )  # clamped для orderbook depth
                        state.atr_is_real = True

                logger.debug(
                    "baseline_initialized",
                    symbol=symbol,
                    avg_volume_1h=float(state.avg_volume_1h),
                    last_price=float(state.last_price),
                    atr_pct_raw=float(state.atr_1h_pct_raw),
                    atr_pct_clamped=float(state.atr_1h_pct),
                    klines_count=len(state.klines_1h),
                )
                return True

            return False

        except Exception as e:
            logger.debug("klines_fetch_error", symbol=symbol, error=str(e))
            return False

    async def _get_http_session(self) -> aiohttp.ClientSession:
        """Получить или создать HTTP сессию."""
        if self._http_session is None or self._http_session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self._http_session = aiohttp.ClientSession(timeout=timeout)
        return self._http_session

    async def start(self, symbols: list[str]):
        """Запустить мониторинг указанных пар."""
        if not symbols:
            logger.warning("no_symbols_to_monitor")
            return

        self._symbols = symbols
        self._running = True

        logger.info("starting_realtime_monitor", symbols=len(symbols))

        # LEAK-2 FIX: Удаляем состояния для символов которых нет в новом списке
        new_symbols_set = set(symbols)
        old_symbols = [s for s in self._states.keys() if s not in new_symbols_set]
        for symbol in old_symbols:
            del self._states[symbol]
        if old_symbols:
            logger.debug("states_cleaned", removed=len(old_symbols))

        # Инициализировать состояния ТОЛЬКО если ещё не существуют
        # (warmup_baselines может уже создать их с baseline данными)
        for symbol in symbols:
            if symbol not in self._states:
                self._states[symbol] = SymbolState(symbol=symbol)

        # Разбить на группы (лимит streams на соединение)
        # Каждый символ = 3 streams (trade, depth, kline)
        symbols_per_connection = settings.websocket.max_streams_per_connection // 3

        for i in range(0, len(symbols), symbols_per_connection):
            batch = symbols[i:i + symbols_per_connection]
            task = asyncio.create_task(self._connect_batch(batch))
            self._tasks.append(task)

        logger.info(
            "websocket_connections_started",
            connections=len(self._tasks),
            symbols_per_connection=symbols_per_connection
        )

    async def stop(self):
        """Остановить мониторинг."""
        logger.info("stopping_realtime_monitor")
        self._running = False

        # Отменить все задачи
        for task in self._tasks:
            task.cancel()

        # Ждать завершения
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        # Закрыть HTTP сессию
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None

        self._tasks.clear()
        self._connections.clear()
        # LEAK-2 FIX: Очищаем состояния при полной остановке
        self._states.clear()
        self._symbols.clear()
        logger.info("realtime_monitor_stopped")

    def get_state(self, symbol: str) -> Optional[SymbolState]:
        """Получить текущее состояние пары."""
        return self._states.get(symbol)

    def get_all_states(self) -> dict[str, SymbolState]:
        """Получить состояния всех пар."""
        return self._states.copy()

    async def _connect_batch(self, symbols: list[str]):
        """Подключиться к WebSocket для группы символов."""

        # Формируем streams
        streams = []
        for s in symbols:
            s_lower = s.lower()
            streams.append(f"{s_lower}@trade")
            streams.append(f"{s_lower}@depth20@100ms")  # Snapshot top 20 levels, not diff!
            streams.append(f"{s_lower}@kline_1m")

        url = f"{settings.websocket.ws_url}?streams={'/'.join(streams)}"

        reconnect_attempts = 0

        while self._running:
            try:
                logger.debug("connecting_websocket", symbols=len(symbols))

                async with websockets.connect(
                    url,
                    ping_interval=settings.websocket.ping_interval_sec,
                    ping_timeout=settings.websocket.ping_timeout_sec,
                    close_timeout=settings.websocket.close_timeout_sec,
                ) as ws:
                    self._connections.append(ws)
                    reconnect_attempts = 0  # Reset on successful connect

                    logger.info("websocket_connected", symbols=len(symbols))

                    try:
                        async for message in ws:
                            if not self._running:
                                break

                            try:
                                data = json.loads(message)
                                await self._process_message(data)
                            except json.JSONDecodeError as e:
                                logger.warning("json_decode_error", error=str(e))
                            except Exception as e:
                                logger.warning("message_processing_error", error=str(e))
                    finally:
                        # LEAK-1 FIX: Удаляем соединение из списка при закрытии
                        if ws in self._connections:
                            self._connections.remove(ws)
                            logger.debug("websocket_removed_from_connections")

            except ConnectionClosed as e:
                logger.warning("websocket_closed", code=e.code, reason=str(e.reason))
            except asyncio.CancelledError:
                logger.debug("websocket_task_cancelled")
                break
            except Exception as e:
                logger.error("websocket_error", error=str(e))

            # Reconnect logic
            if self._running:
                reconnect_attempts += 1
                if reconnect_attempts > settings.websocket.max_reconnect_attempts:
                    logger.error("max_reconnect_attempts_reached")
                    break

                delay = min(settings.websocket.reconnect_delay_sec * reconnect_attempts, 60)
                logger.info("reconnecting", attempt=reconnect_attempts, delay=delay)
                await asyncio.sleep(delay)

    async def _process_message(self, data: dict):
        """Обработать сообщение WebSocket."""
        stream = data.get("stream", "")
        payload = data.get("data", {})

        if not stream or not payload:
            return

        if "@trade" in stream:
            await self._process_trade(payload)
        elif "@depth" in stream:
            # Для depth20 символ не в payload, извлекаем из stream name
            # Stream format: "btcusdt@depth20@100ms" или "btcusdt@depth@100ms"
            if "s" not in payload:
                symbol = stream.split("@")[0].upper()
                payload["s"] = symbol
            await self._process_depth(payload)
        elif "@kline" in stream:
            await self._process_kline(payload)

    async def _process_trade(self, data: dict):
        """Обработать трейд."""
        symbol = data.get("s")
        if not symbol:
            return

        state = self._states.get(symbol)
        if not state:
            return

        try:
            trade = Trade(
                price=Decimal(str(data["p"])),
                qty=Decimal(str(data["q"])),
                time=int(data["T"]),
                is_buyer_maker=bool(data["m"]),
            )
        except (KeyError, ValueError) as e:
            logger.debug("trade_parse_error", symbol=symbol, error=str(e))
            return

        # Update state
        # RACE-1: Эти операции атомарны в asyncio (нет await между ними)
        # Lock НЕ нужен — cooperative multitasking гарантирует атомарность
        state.last_price = trade.price
        state.last_trade_time = trade.time
        state.trades_1m.append(trade)
        state.trades_5m.append(trade)

        # Calculate volumes
        trade_value = trade.price * trade.qty
        state.volume_1m += trade_value
        state.volume_5m += trade_value
        state.volume_1h += trade_value

        # Cleanup old trades
        now_ms = trade.time
        self._cleanup_old_trades(state, now_ms)

        # Check minute boundaries for resets
        self._check_time_resets(state, now_ms)

        state.last_update = datetime.now()

        # Callbacks (async to not block websocket processing)
        # FIX-TASK-1: используем _spawn_callback_task для safe fire-and-forget
        if self._on_trade:
            try:
                result = self._on_trade(symbol, trade)
                if asyncio.iscoroutine(result):
                    self._spawn_callback_task(result)
            except Exception as e:
                logger.debug("trade_callback_error", error=str(e))

        if self._on_state_update:
            try:
                result = self._on_state_update(state)
                if asyncio.iscoroutine(result):
                    self._spawn_callback_task(result)
            except Exception as e:
                logger.debug("state_callback_error", error=str(e))

    async def _process_depth(self, data: dict):
        """
        Обработать обновление стакана.

        Поддерживает оба формата:
        - @depth20@100ms (snapshot): bids/asks, lastUpdateId
        - @depth@100ms (diff): b/a, E (legacy, не используется)
        """
        # Для depth20 символ приходит из stream name, не из payload
        # Payload содержит только lastUpdateId, bids, asks
        symbol = data.get("s")

        # depth20 не содержит "s", получаем из raw_bids первого уровня не получится
        # Символ должен быть извлечён из stream name в _route_message
        if not symbol:
            # Для depth20 символ передаётся через wrapper
            return

        state = self._states.get(symbol)
        if not state:
            return

        try:
            # Поддержка обоих форматов: depth20 (bids/asks) и diff (b/a)
            bids = data.get("bids") or data.get("b", [])
            asks = data.get("asks") or data.get("a", [])

            if bids:
                _bid_price = Decimal(str(bids[0][0])) if bids[0] else Decimal("0")
                if _bid_price > 0:  # FIX-C-1: защита от нулевой цены → ZeroDivisionError
                    state.best_bid = _bid_price
                # Legacy: top 20 levels
                state.bid_volume_20 = sum(
                    Decimal(str(p)) * Decimal(str(q))
                    for p, q in bids[:20]
                )
                # Store raw bids for ATR-based calculation
                state.raw_bids = [(float(p), float(q)) for p, q in bids
                                  if float(p) > 0 and float(q) > 0]  # FIX-C-6: float для производительности

            if asks:
                _ask_price = Decimal(str(asks[0][0])) if asks[0] else Decimal("0")
                if _ask_price > 0:  # FIX-C-1: защита от нулевой цены
                    state.best_ask = _ask_price
                # Legacy: top 20 levels
                state.ask_volume_20 = sum(
                    Decimal(str(p)) * Decimal(str(q))
                    for p, q in asks[:20]
                )
                # Store raw asks for ATR-based calculation
                state.raw_asks = [(float(p), float(q)) for p, q in asks
                                  if float(p) > 0 and float(q) > 0]  # FIX-C-6: float для производительности

            # Calculate ATR-based orderbook volumes
            self._calculate_atr_volumes(state)

            state.last_depth_time = data.get("lastUpdateId") or data.get("E", 0)
            state.last_update = datetime.now()

        except (KeyError, ValueError, IndexError) as e:
            logger.debug("depth_parse_error", symbol=symbol, error=str(e))

    async def _process_kline(self, data: dict):
        """Обработать свечу."""
        symbol = data.get("s")
        if not symbol:
            return

        state = self._states.get(symbol)
        if not state:
            return

        try:
            kline = data.get("k", {})
            if not kline:
                return

            high_price = Decimal(str(kline["h"]))
            low_price = Decimal(str(kline["l"]))
            close_price = Decimal(str(kline["c"]))

            # Update price
            state.last_price = close_price

            # Store in history for baseline (last 60 candles)
            if kline.get("x"):  # Candle closed
                state.price_history.append(close_price)
                if len(state.price_history) > 60:
                    state.price_history.pop(0)

                # Store HLC for ATR calculation
                state.klines_1h.append((high_price, low_price, close_price))
                if len(state.klines_1h) > 60:
                    state.klines_1h.pop(0)

                # Recalculate ATR when we have enough data
                if len(state.klines_1h) >= 14:
                    # FIX-ATR-RAW: сохраняем raw и clamped отдельно
                    raw_atr = self._calculate_atr_pct(state.klines_1h)
                    state.atr_1h_pct_raw = raw_atr
                    state.atr_1h_pct = max(
                        self.ATR_DEPTH_MIN_PCT,
                        min(self.ATR_DEPTH_MAX_PCT, raw_atr)
                    )
                    state.atr_is_real = True

            state.last_update = datetime.now()

        except (KeyError, ValueError) as e:
            logger.debug("kline_parse_error", symbol=symbol, error=str(e))

    def _cleanup_old_trades(self, state: SymbolState, now_ms: int):
        """Удалить старые трейды из списков."""
        # Keep last 1 minute
        state.trades_1m = [t for t in state.trades_1m if now_ms - t.time < 60_000]
        # Keep last 5 minutes
        state.trades_5m = [t for t in state.trades_5m if now_ms - t.time < 300_000]

    def _check_time_resets(self, state: SymbolState, now_ms: int):
        """Проверить и выполнить сбросы по времени."""
        symbol = state.symbol
        now_minute = now_ms // 60_000
        now_5min = now_ms // 300_000
        now_hour = now_ms // 3_600_000

        # Minute reset
        if now_minute > self._last_minute_reset[symbol]:
            state.price_1m_ago = state.last_price
            state.volume_1m = Decimal("0")
            self._last_minute_reset[symbol] = now_minute

        # 5-minute reset
        if now_5min > self._last_5min_reset[symbol]:
            state.price_5m_ago = state.last_price
            state.volume_5m = Decimal("0")
            self._last_5min_reset[symbol] = now_5min

        # Hour reset
        if now_hour > self._last_hour_reset[symbol]:
            state.price_1h_ago = state.last_price
            # Update baseline before reset
            if state.volume_1h > 0:
                if state.avg_volume_1h == 0:
                    state.avg_volume_1h = state.volume_1h
                else:
                    # Exponential moving average
                    state.avg_volume_1h = state.avg_volume_1h * Decimal("0.8") + state.volume_1h * Decimal("0.2")
            state.volume_1h = Decimal("0")
            self._last_hour_reset[symbol] = now_hour

    def _calculate_atr_volumes(self, state: "SymbolState"):
        """
        Рассчитать объёмы стакана в пределах ±ATR% от mid price.

        Использует ATR как адаптивную глубину анализа стакана.
        """
        mid = float(state.mid_price)
        if mid == 0:
            return
        if state.atr_1h_pct == 0:
            logger.debug("spot_atr_zero_skipping", symbol=state.symbol)
            return
        if not getattr(state, 'atr_is_real', False):  # FIX-H-5: не считаем на дефолтном ATR
            return

        # FIX-L-2: используем дневной ATR для глубины стакана
        if getattr(state, 'atr_daily_is_real', False):
            atr_pct = float(state.atr_daily_pct_depth) / 100
        else:
            atr_pct = float(state.atr_1h_pct) / 100  # fallback до загрузки дневных

        # FIX-SPREAD-2: effective_depth адаптируется к реальному spread
        # Когда spread > ATR, bids находятся ниже lower_bound и выпадают из фильтра
        # Решение: effective_depth = max(atr, spread * 1.2) гарантирует захват с буфером
        spread_pct = float(state.spread_pct) / 100 if state.best_bid > 0 else 0.0
        effective_depth = max(atr_pct, spread_pct * self.DEPTH_SPREAD_BUFFER) if spread_pct > 0 else atr_pct

        if effective_depth != atr_pct:
            logger.debug(
                "atr_depth_spread_adjusted",
                symbol=state.symbol,
                atr_pct=round(atr_pct * 100, 4),
                spread_pct=round(spread_pct * 100, 4),
                effective_pct=round(effective_depth * 100, 4),
            )

        lower_bound = mid * (1 - effective_depth)
        upper_bound = mid * (1 + effective_depth)

        # FIX-1: добавлена верхняя граница для bid и нижняя для ask
        state.bid_volume_atr = Decimal(str(sum(
            p * q for p, q in state.raw_bids
            if lower_bound <= p <= mid
        )))  # FIX-C-6: результат обратно в Decimal для совместимости с остальным кодом

        state.ask_volume_atr = Decimal(str(sum(
            p * q for p, q in state.raw_asks
            if mid <= p <= upper_bound
        )))  # FIX-C-6

    def _calculate_atr_pct(self, klines: list, period: int = 14) -> Decimal:
        """
        Рассчитать RAW ATR как процент от цены (без clamp).

        FIX-ATR-RAW: возвращает реальное значение ATR.
        Clamp для orderbook depth применяется в месте вызова.

        Args:
            klines: Список [(high, low, close), ...]
            period: Период ATR (по умолчанию 14)

        Returns:
            RAW ATR как процент от цены (например 0.4 = 0.4%)
        """
        if len(klines) < period + 1:
            return Decimal("0")  # FIX-ATR-RAW: 0 означает "нет данных"

        # Calculate True Range for each candle
        true_ranges = []
        for i in range(1, len(klines)):
            high, low, close = klines[i]
            prev_close = klines[i - 1][2]

            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            tr = max(tr1, tr2, tr3)
            true_ranges.append(tr)

        # Calculate ATR using EMA
        if not true_ranges:
            return Decimal("0")

        # Simple EMA calculation
        multiplier = Decimal(2) / (Decimal(period) + 1)
        atr = true_ranges[0]

        for tr in true_ranges[1:]:
            atr = (tr - atr) * multiplier + atr

        # Convert to percentage of current price
        current_close = klines[-1][2]
        if current_close > 0:
            atr_pct = (atr / current_close) * 100
            # FIX-ATR-RAW: возвращаем RAW значение, clamp не применяем
            return Decimal(str(round(float(atr_pct), 4)))

        return Decimal("0")

    # =========================================================================
    # HISTORY LOADER INTEGRATION
    # =========================================================================

    async def _cache_klines_history(self, symbol: str, data: list):
        """
        Кэшировать историю klines из HistoryLoader.

        Используется для инициализации baseline объёма при старте.

        Args:
            symbol: Символ пары
            data: Список klines от /api/v3/klines API
                  Формат: [[open_time, open, high, low, close, volume, close_time,
                           quote_volume, trades, taker_buy_volume, taker_buy_quote_volume, ignore], ...]
        """
        state = self._states.get(symbol)
        if not state:
            state = SymbolState(symbol=symbol)
            self._states[symbol] = state

        if not data:
            return

        # Расчёт baseline из klines
        volumes = []
        for kline in data:
            try:
                # Index 7 = quote volume (в USDT)
                quote_vol = Decimal(str(kline[7]))
                volumes.append(quote_vol)
            except (IndexError, ValueError):
                continue

        if volumes:
            # Средний объём за минуту
            total_volume = sum(volumes)
            state.avg_volume_1h = total_volume / len(volumes)

            # Инициализировать цены из последней свечи
            if data:
                last_kline = data[-1]
                try:
                    state.last_price = Decimal(str(last_kline[4]))  # close
                    state.price_1m_ago = state.last_price
                    state.price_5m_ago = state.last_price
                    state.price_1h_ago = Decimal(str(data[0][1]))  # open первой свечи
                except (IndexError, ValueError):
                    pass

            logger.debug(
                "klines_history_cached",
                symbol=symbol,
                candles=len(data),
                avg_volume=float(state.avg_volume_1h),
            )

    async def _cache_daily_klines_history(self, symbol: str, data: list):
        """
        FIX-L-2: Кэшировать дневные klines для расчёта daily ATR.

        Args:
            symbol: Символ пары
            data: Список klines от /api/v3/klines?interval=1d
        """
        state = self._states.get(symbol)
        if not state:
            state = SymbolState(symbol=symbol)
            self._states[symbol] = state

        if not data:
            return

        klines = []
        for k in data:
            try:
                high = Decimal(str(k[2]))
                low = Decimal(str(k[3]))
                close = Decimal(str(k[4]))
                klines.append((high, low, close))
            except (IndexError, ValueError):
                continue

        state.klines_1d = klines  # FIX-L-2

        if len(klines) >= 14:
            raw_daily = self._calculate_atr_pct(klines)
            # RAW — для SL/TP в risk_calculator (без clamp!)
            state.atr_daily_pct = raw_daily
            # Clamped — для orderbook depth (строка 668)
            state.atr_daily_pct_depth = max(
                self.ATR_DEPTH_MIN_PCT,
                min(self.ATR_DEPTH_MAX_PCT, raw_daily)
            )
            state.atr_daily_is_real = True
            logger.debug(
                "spot_daily_atr_calculated",
                symbol=symbol,
                atr_daily_pct_raw=float(state.atr_daily_pct),
                atr_daily_pct_depth=float(state.atr_daily_pct_depth),
            )

    async def _cache_trades_history(self, symbol: str, data: list):
        """
        Кэшировать историю сделок из HistoryLoader.

        Используется для инициализации trades_5m при старте,
        чтобы паттерны (wash trading, coordinated buys) работали сразу.

        Args:
            symbol: Символ пары
            data: Список aggTrades от /api/v3/aggTrades API
                  Формат: [{"a": id, "p": price, "q": qty, "f": first_trade_id,
                           "l": last_trade_id, "T": timestamp, "m": is_buyer_maker}, ...]
        """
        state = self._states.get(symbol)
        if not state:
            state = SymbolState(symbol=symbol)
            self._states[symbol] = state

        if not data:
            return

        from .models import Trade

        # Текущее время для фильтрации (только последние 5 минут)
        now_ms = int(datetime.now().timestamp() * 1000)
        cutoff_5m = now_ms - 300_000  # 5 минут назад
        cutoff_1m = now_ms - 60_000   # 1 минута назад

        trades_added_5m = 0
        trades_added_1m = 0

        for item in data:
            try:
                trade_time = int(item["T"])

                # Пропускаем слишком старые
                if trade_time < cutoff_5m:
                    continue

                trade = Trade(
                    price=Decimal(str(item["p"])),
                    qty=Decimal(str(item["q"])),
                    time=trade_time,
                    is_buyer_maker=bool(item["m"]),
                )

                # Добавляем в 5-минутный список
                state.trades_5m.append(trade)
                trades_added_5m += 1

                # Также в 1-минутный если достаточно свежий
                if trade_time >= cutoff_1m:
                    state.trades_1m.append(trade)
                    trades_added_1m += 1

                # Обновить последнюю цену
                state.last_price = trade.price

            except (KeyError, ValueError, TypeError):
                continue

        # Обновить объёмы из загруженных сделок
        if state.trades_5m:
            total_vol_5m = sum(t.price * t.qty for t in state.trades_5m)
            state.volume_5m = total_vol_5m

        if state.trades_1m:
            total_vol_1m = sum(t.price * t.qty for t in state.trades_1m)
            state.volume_1m = total_vol_1m

        logger.debug(
            "trades_history_cached",
            symbol=symbol,
            trades_5m=trades_added_5m,
            trades_1m=trades_added_1m,
            total_raw=len(data),
        )
