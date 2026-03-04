# -*- coding: utf-8 -*-
"""
Futures Monitor - мониторинг Open Interest и фьючерсных данных.

Это КЛЮЧЕВОЙ модуль для детекции китов и предсказания пампов.
Binance Futures API предоставляет данные, которых нет на споте:
- Open Interest (сколько контрактов открыто)
- Funding Rate (плата за удержание позиции)
- Long/Short Ratio (соотношение лонгов/шортов)

Паттерны детекции:
1. Whale Accumulation: OI↑ + цена стабильная = кто-то набирает позицию
2. Pre-Pump Setup: OI↑ + Funding negative = лонги дешёвые, готовятся
3. Dump Warning: OI↓ резко + цена падает = киты выходят
4. Squeeze Alert: Funding extreme + высокий OI = каскад ликвидаций скоро

ВАЖНО: Единицы измерения Binance API:
- Funding Rate: возвращается как 0.0001 = 0.01% (умножаем * 100 для процентов)
- Long/Short Account: возвращается как 0.5523 = 55.23% (умножаем * 100 для хранения как 55.23)
- Open Interest: количество контрактов (умножаем на mark_price для USD)

Пороги загружаются из config/settings.py (settings.futures.*)

КРИТИЧНО: Использует BinanceApiClient для обработки 418/429!
"""

import asyncio
import hashlib
import json
import time  # FIX-L-1
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Callable, Any
import aiohttp
import structlog

from config.settings import settings
from .models import AlertSeverity
from .binance_api import (
    BinanceApiClient,
    BinanceBannedError,
    BinanceRateLimitError,
    get_futures_client,
    get_ban_state,
)


logger = structlog.get_logger(__name__)


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class OpenInterestData:
    """
    Данные по Open Interest.

    open_interest: количество контрактов (в базовой валюте, напр. BTC)
    mark_price: текущая mark price для расчёта USD value
    """
    symbol: str
    open_interest: Decimal          # Количество контрактов
    mark_price: Decimal = Decimal("0")  # Mark price для расчёта USD
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def open_interest_usd(self) -> Decimal:
        """Стоимость OI в USD."""
        return self.open_interest * self.mark_price


@dataclass
class FundingRateData:
    """
    Данные по Funding Rate.

    funding_rate: raw значение (0.0001 = 0.01%)
    funding_rate_percent: в процентах (0.01 = 0.01%)
    """
    symbol: str
    funding_rate: Decimal           # Raw funding rate (0.0001 = 0.01%)
    funding_time: int               # Время следующего funding (timestamp ms)
    mark_price: Decimal             # Mark price для расчёта OI в USD
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def funding_rate_percent(self) -> Decimal:
        """Funding rate в процентах (0.0001 -> 0.01)."""
        return self.funding_rate * 100


@dataclass
class LongShortRatioData:
    """
    Данные по соотношению Long/Short.

    ВАЖНО: Binance API возвращает longAccount/shortAccount как доли (0.5523 = 55.23%)
    Мы храним в процентах для удобства (55.23).
    """
    symbol: str
    long_short_ratio: Decimal       # Соотношение (например 1.23 = лонгов в 1.23 раза больше)
    long_account_pct: Decimal       # % аккаунтов в лонге (55.23 = 55.23%)
    short_account_pct: Decimal      # % аккаунтов в шорте (44.77 = 44.77%)
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def is_extremely_long(self) -> bool:
        """Экстремально много лонгов (>70%)."""
        return self.long_account_pct > Decimal("70")

    @property
    def is_extremely_short(self) -> bool:
        """Экстремально много шортов (>55%). Порог ниже т.к. рынок обычно long-biased."""
        return self.short_account_pct > Decimal("55")


@dataclass
class FuturesState:
    """Полное состояние фьючерсов по паре."""
    symbol: str

    # Текущие данные
    current_oi: Optional[OpenInterestData] = None
    current_funding: Optional[FundingRateData] = None
    current_ls_ratio: Optional[LongShortRatioData] = None

    # История OI (для расчёта изменений)
    oi_history: list[OpenInterestData] = field(default_factory=list)

    # История цен (mark_price из funding, для OI+Price divergence)
    price_history: list[tuple[datetime, Decimal]] = field(default_factory=list)

    # История funding (для gradient расчёта)
    funding_history: list[FundingRateData] = field(default_factory=list)

    # Расчётные метрики (изменение OI в процентах)
    oi_change_1h_pct: Decimal = Decimal("0")   # Изменение за последний час
    oi_change_5m_pct: Decimal = Decimal("0")   # Изменение за последние 5 минут
    oi_change_1m_pct: Decimal = Decimal("0")   # Изменение за последнюю минуту

    # Метрики цены (для divergence)
    price_change_1h_pct: Decimal = Decimal("0")
    price_change_5m_pct: Decimal = Decimal("0")

    # ========== FUTURES ORDERBOOK (WebSocket depth) ==========
    # Best bid/ask
    futures_best_bid: Decimal = Decimal("0")
    futures_best_ask: Decimal = Decimal("0")

    # ATR-based orderbook volumes
    futures_bid_volume_atr: Decimal = Decimal("0")
    futures_ask_volume_atr: Decimal = Decimal("0")

    # Raw orderbook для расчёта ATR volumes
    futures_raw_bids: list = field(default_factory=list)  # [(price, qty), ...]
    futures_raw_asks: list = field(default_factory=list)  # [(price, qty), ...]

    # Klines для ATR расчёта (из futures WebSocket)
    futures_klines_1h: list = field(default_factory=list)  # [(high, low, close), ...]
    futures_atr_1h_pct: Decimal = Decimal("5")  # ATR как % от цены, clamped для orderbook
    futures_atr_1h_pct_raw: Decimal = Decimal("0")  # FIX-ATR-RAW: реальный ATR без clamp
    futures_atr_is_real: bool = False  # FIX-D-2: флаг что ATR рассчитан реально (не default)

    # FIX-L-1-PATCH: дневные klines и ATR для глубины стакана
    futures_klines_1d: list = field(default_factory=list)
    futures_atr_daily_pct: Decimal = Decimal("5")
    futures_atr_daily_is_real: bool = False

    last_depth_time: int = 0  # Timestamp последнего depth update

    last_update: datetime = field(default_factory=datetime.now)

    @property
    def has_futures(self) -> bool:
        """Есть ли фьючерсы для этой пары."""
        return self.current_oi is not None

    @property
    def futures_mid_price(self) -> Decimal:
        """Средняя цена между bid и ask для futures."""
        if self.futures_best_bid == 0 or self.futures_best_ask == 0:
            # Fallback to mark_price from funding
            if self.current_funding:
                return self.current_funding.mark_price
            return Decimal("0")
        return (self.futures_best_bid + self.futures_best_ask) / 2

    @property
    def futures_spread_pct(self) -> Decimal:
        """Спред futures стакана в процентах."""
        if self.futures_best_bid == 0:
            return Decimal("0")
        raw = (self.futures_best_ask - self.futures_best_bid) / self.futures_best_bid * 100
        return Decimal(str(round(float(raw), 4)))

    @property
    def futures_book_imbalance_atr(self) -> Optional[Decimal]:
        """
        Дисбаланс futures стакана (ATR-based).

        FIX-IMBALANCE-1: семантически различные возвраты:
        - None = нет данных для анализа (total=0 или volume<$100)
        - Decimal в диапазоне [-1, +1] = реальный imbalance
        """
        bid = self.futures_bid_volume_atr
        ask = self.futures_ask_volume_atr
        total = bid + ask

        # FIX-IMBALANCE-1: нет данных вообще
        if total == 0:
            return None

        # FIX-IMBALANCE-1: данные есть но недостаточны
        if bid < 100 or ask < 100:
            return None

        raw_imbalance = (bid - ask) / total
        return Decimal(str(round(float(raw_imbalance), 4)))


@dataclass
class FuturesDetection:
    """Детекция на основе фьючерсных данных."""
    symbol: str
    timestamp: datetime
    detection_type: str
    severity: AlertSeverity
    score: int
    details: dict
    evidence: list[str]


# =============================================================================
# FUTURES MONITOR
# =============================================================================

class FuturesMonitor:
    """
    Мониторинг фьючерсных данных для детекции китов.

    КРИТИЧНО: Использует BinanceApiClient для обработки 418/429!

    Использование:
        monitor = FuturesMonitor(on_detection=my_callback)
        await monitor.start(["BTCUSDT", "ETHUSDT"])
        ...
        await monitor.stop()
    """

    # Binance Futures API
    FUTURES_URL = "https://fapi.binance.com"

    # Binance Futures WebSocket для depth
    FUTURES_WS_URL = "wss://fstream.binance.com/stream"

    # Лимиты подписки (Binance ограничивает 200 streams на соединение)
    MAX_STREAMS_PER_CONNECTION = 180  # С запасом

    # FIX-SPREAD-2: буфер для effective_depth когда spread > ATR
    DEPTH_SPREAD_BUFFER: float = 1.2

    # FIX-ATR-RAW: clamp для ATR используемого в orderbook depth
    # Минимум 0.5% гарантирует захват ордеров даже для низковолатильных активов
    ATR_DEPTH_MIN_PCT: Decimal = Decimal("0.5")
    ATR_DEPTH_MAX_PCT: Decimal = Decimal("20")

    # =========================================================================
    # ПОРОГИ ЗАГРУЖАЮТСЯ ИЗ config/settings.py (settings.futures.*)
    # Для изменения — редактировать config/config.yaml
    # =========================================================================

    def __init__(
        self,
        on_detection: Optional[Callable[[FuturesDetection], Any]] = None,
    ):
        """
        Args:
            on_detection: Callback при детекции
        """
        self._client = get_futures_client()
        self._symbols: list[str] = []
        self._states: dict[str, FuturesState] = {}
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._daily_klines_loaded_at: float = 0.0  # FIX-L-1

        # FIX-TASK-1: tracking set для fire-and-forget callback tasks
        self._callback_tasks: set[asyncio.Task] = set()

        self._on_detection = on_detection
        self._semaphore = asyncio.Semaphore(settings.rate_limit.futures_max_concurrent)

        # Кэш: какие пары имеют фьючерсы
        self._futures_symbols: set[str] = set()

        # Дедупликация: {(symbol, detection_type): (timestamp, fingerprint)}
        # fingerprint = хэш всех параметров детекции
        self._last_detections: dict[tuple[str, str], tuple[datetime, str]] = {}

        # WebSocket соединения для depth
        self._depth_ws_connections: list[aiohttp.ClientWebSocketResponse] = []
        self._depth_ws_session: Optional[aiohttp.ClientSession] = None

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

    async def start(self, symbols: list[str]):
        """Запустить мониторинг фьючерсов."""
        if not symbols:
            return

        self._running = True
        logger.info("starting_futures_monitor", symbols=len(symbols))

        # Проверить какие пары имеют фьючерсы
        await self._check_futures_availability(symbols)

        if not self._futures_symbols:
            logger.warning("no_futures_pairs_found")
            return

        logger.info(
            "futures_pairs_found",
            count=len(self._futures_symbols),
            pairs=list(self._futures_symbols)[:10]  # Первые 10
        )

        # Инициализировать состояния
        for symbol in self._futures_symbols:
            self._states[symbol] = FuturesState(symbol=symbol)

        # Первоначальная загрузка funding (нужна для mark_price в OI)
        logger.info("loading_initial_funding_data")
        await self._update_all_funding()

        # Загрузить историческую funding за последние 8 периодов (для gradient анализа)
        logger.info("loading_historical_funding_data")
        await self._load_historical_funding()

        # Загрузить историческую OI за последний час (для oi_change_1h с первой минуты)
        logger.info("loading_historical_oi_data")
        await self._load_historical_oi()

        # Первоначальная загрузка текущей OI (для baseline)
        logger.info("loading_initial_oi_data")
        await self._update_all_oi()

        # Запустить задачи мониторинга
        self._tasks.append(asyncio.create_task(self._oi_monitor_loop()))
        self._tasks.append(asyncio.create_task(self._funding_monitor_loop()))
        self._tasks.append(asyncio.create_task(self._ls_ratio_monitor_loop()))

        # Запустить WebSocket для depth (orderbook)
        self._tasks.append(asyncio.create_task(self._depth_ws_loop()))

        # Запустить klines для ATR расчёта
        self._tasks.append(asyncio.create_task(self._klines_monitor_loop()))

        logger.info("futures_monitor_started")

    async def stop(self):
        """Остановить мониторинг."""
        logger.info("stopping_futures_monitor")
        self._running = False

        for task in self._tasks:
            task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

        # Закрыть depth WebSocket соединения
        for ws in self._depth_ws_connections:
            if not ws.closed:
                await ws.close()
        self._depth_ws_connections.clear()

        if self._depth_ws_session and not self._depth_ws_session.closed:
            await self._depth_ws_session.close()
            self._depth_ws_session = None

        # Очистка состояний
        self._tasks.clear()
        self._states.clear()
        self._futures_symbols.clear()
        self._last_detections.clear()

        logger.info("futures_monitor_stopped")

    def get_state(self, symbol: str) -> Optional[FuturesState]:
        """Получить состояние фьючерсов для пары."""
        return self._states.get(symbol)

    def has_futures(self, symbol: str) -> bool:
        """Проверить есть ли фьючерсы для пары."""
        return symbol in self._futures_symbols

    # =========================================================================
    # MONITORING LOOPS
    # =========================================================================

    async def _oi_monitor_loop(self):
        """Цикл мониторинга Open Interest."""
        while self._running:
            try:
                await self._update_all_oi()
                await asyncio.sleep(settings.futures.oi_update_interval_sec)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("oi_monitor_error", error=str(e))
                await asyncio.sleep(30)

    async def _funding_monitor_loop(self):
        """Цикл мониторинга Funding Rate."""
        while self._running:
            try:
                await self._update_all_funding()
                await asyncio.sleep(settings.futures.funding_update_interval_sec)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("funding_monitor_error", error=str(e))
                await asyncio.sleep(60)

    async def _ls_ratio_monitor_loop(self):
        """Цикл мониторинга Long/Short Ratio."""
        while self._running:
            try:
                await self._update_all_ls_ratio()
                await asyncio.sleep(settings.futures.ls_ratio_update_interval_sec)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("ls_ratio_monitor_error", error=str(e))
                await asyncio.sleep(60)

    # =========================================================================
    # DEPTH WEBSOCKET (FUTURES ORDERBOOK)
    # =========================================================================

    async def _depth_ws_loop(self):
        """
        Цикл WebSocket для получения futures orderbook (depth).

        Подписывается на {symbol}@depth20@100ms для каждого символа (snapshot, не diff!).
        Разбивает на несколько соединений если символов больше MAX_STREAMS_PER_CONNECTION.
        """
        if not self._futures_symbols:
            logger.info("depth_ws_no_symbols")
            return

        # Ждём пока funding загрузит mark_price (нужен для ATR расчёта)
        await asyncio.sleep(5)

        symbols_list = list(self._futures_symbols)
        logger.info("depth_ws_starting", symbols=len(symbols_list))

        # Разбить символы на группы по MAX_STREAMS_PER_CONNECTION
        chunks = [
            symbols_list[i:i + self.MAX_STREAMS_PER_CONNECTION]
            for i in range(0, len(symbols_list), self.MAX_STREAMS_PER_CONNECTION)
        ]

        # Создать WebSocket для каждой группы
        ws_tasks = []
        for i, chunk in enumerate(chunks):
            ws_tasks.append(
                asyncio.create_task(self._depth_ws_connection(chunk, i))
            )

        # Ждать все WebSocket соединения
        await asyncio.gather(*ws_tasks, return_exceptions=True)

    async def _depth_ws_connection(self, symbols: list[str], connection_id: int):
        """
        Одно WebSocket соединение для группы символов.
        """
        # Формируем stream names: symbol@depth20@100ms (snapshot, не diff!)
        streams = [f"{s.lower()}@depth20@100ms" for s in symbols]
        stream_param = "/".join(streams)
        ws_url = f"{self.FUTURES_WS_URL}?streams={stream_param}"

        while self._running:
            try:
                if not self._depth_ws_session:
                    self._depth_ws_session = aiohttp.ClientSession()

                logger.info(
                    "depth_ws_connecting",
                    connection_id=connection_id,
                    symbols=len(symbols),
                )

                async with self._depth_ws_session.ws_connect(
                    ws_url,
                    heartbeat=30,
                    receive_timeout=60,
                ) as ws:
                    self._depth_ws_connections.append(ws)
                    logger.info("depth_ws_connected", connection_id=connection_id)

                    async for msg in ws:
                        if not self._running:
                            break

                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                await self._process_depth_message(data)
                            except json.JSONDecodeError:
                                continue
                            except Exception as e:
                                logger.debug("depth_ws_process_error", error=str(e))

                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            logger.warning("depth_ws_closed", connection_id=connection_id)
                            break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("depth_ws_error", connection_id=connection_id, error=str(e))
                await asyncio.sleep(5)

    async def _process_depth_message(self, message: dict):
        """
        Обработать depth сообщение от WebSocket.

        Format:
        {
            "stream": "btcusdt@depth20@100ms",
            "data": {
                "e": "depthUpdate",
                "E": 1234567890123,  # Event time
                "s": "BTCUSDT",
                "b": [[price, qty], ...],  # Bids
                "a": [[price, qty], ...]   # Asks
            }
        }
        """
        try:
            stream = message.get("stream", "")
            data = message.get("data", {})

            if not data:
                return

            # depth20 может не содержать "e": "depthUpdate"
            # Проверяем наличие bids/asks
            if data.get("e") and data.get("e") != "depthUpdate":
                return

            # Символ из data или из stream name
            symbol = data.get("s", "").upper()
            if not symbol:
                symbol = stream.split("@")[0].upper()

            state = self._states.get(symbol)
            if not state:
                return

            # Поддержка обоих форматов: b/a (futures) или bids/asks (snapshot)
            bids = data.get("b") or data.get("bids", [])
            asks = data.get("a") or data.get("asks", [])

            if not bids and not asks:
                return

            # Сохраняем raw orderbook (только если есть данные!)
            if bids:
                state.futures_raw_bids = [
                    (float(p), float(q)) for p, q in bids
                    if float(p) > 0 and float(q) > 0  # FIX-C-7: float для производительности
                ]
                _fbid = Decimal(str(bids[0][0]))
                if _fbid > 0:  # FIX-C-2: защита от нулевой цены
                    state.futures_best_bid = _fbid

            if asks:
                state.futures_raw_asks = [
                    (float(p), float(q)) for p, q in asks
                    if float(p) > 0 and float(q) > 0  # FIX-C-7: float для производительности
                ]
                _fask = Decimal(str(asks[0][0]))
                if _fask > 0:  # FIX-C-2: защита от нулевой цены
                    state.futures_best_ask = _fask

            state.last_depth_time = data.get("E") or data.get("lastUpdateId", 0)

            # Рассчитываем ATR-based volumes
            self._calculate_futures_atr_volumes(state)

            # Логируем первые данные для диагностики
            if state.futures_bid_volume_atr > 0 or state.futures_ask_volume_atr > 0:
                logger.debug(
                    "futures_depth_processed",
                    symbol=symbol,
                    bid_volume=float(state.futures_bid_volume_atr),
                    ask_volume=float(state.futures_ask_volume_atr),
                    atr_pct=float(state.futures_atr_1h_pct),
                    raw_bids_count=len(state.futures_raw_bids),
                    raw_asks_count=len(state.futures_raw_asks),
                )

            # Проверяем детекции на основе futures orderbook
            await self._check_futures_orderbook_detections(state)

        except Exception as e:
            logger.debug("depth_process_error", error=str(e))

    def _calculate_futures_atr_volumes(self, state: FuturesState):
        """
        Рассчитать объёмы futures стакана в пределах ±ATR% от mid price.
        """
        mid = float(state.futures_mid_price)  # FIX-C-7: совместимость с float
        if mid == 0:
            return
        if state.futures_atr_1h_pct == 0:  # FIX-C-3: ATR=0 даёт нулевой диапазон
            logger.debug("futures_atr_zero_skipping", symbol=state.symbol)
            return

        # FIX-D-2: Пропускаем пока ATR не рассчитан реально (default 5% искажает volumes)
        if not state.futures_atr_is_real:
            logger.debug("futures_atr_not_real_skipping", symbol=state.symbol)
            return

        # FIX-L-1-PATCH: дневной ATR для глубины стакана
        if getattr(state, 'futures_atr_daily_is_real', False):
            atr_pct = float(state.futures_atr_daily_pct) / 100
        else:
            atr_pct = float(state.futures_atr_1h_pct) / 100

        # FIX-SPREAD-2: effective_depth адаптируется к реальному spread
        spread_pct = float(state.futures_spread_pct) / 100 if state.futures_best_bid > 0 else 0.0
        effective_depth = max(atr_pct, spread_pct * self.DEPTH_SPREAD_BUFFER) if spread_pct > 0 else atr_pct

        if effective_depth > atr_pct:
            logger.debug(
                "futures_effective_depth_spread_adjusted",
                symbol=state.symbol,
                atr_pct=round(atr_pct * 100, 4),
                spread_pct=round(spread_pct * 100, 4),
                effective_depth_pct=round(effective_depth * 100, 4),
            )

        lower_bound = mid * (1 - effective_depth)
        upper_bound = mid * (1 + effective_depth)

        # FIX-2: добавлена верхняя граница для bid и нижняя для ask
        bid_volume = sum(
            p * q for p, q in state.futures_raw_bids
            if lower_bound <= p <= mid
        )

        ask_volume = sum(
            p * q for p, q in state.futures_raw_asks
            if mid <= p <= upper_bound
        )

        state.futures_bid_volume_atr = Decimal(str(bid_volume))  # FIX-C-7: обратно в Decimal
        state.futures_ask_volume_atr = Decimal(str(ask_volume))  # FIX-C-7

    # =========================================================================
    # KLINES FOR ATR CALCULATION
    # =========================================================================

    async def _klines_monitor_loop(self):
        """
        Периодически загружает 1m klines для расчёта ATR.
        Запускается раз в минуту.
        """
        # Первоначальная загрузка klines для всех символов
        await asyncio.sleep(3)  # Ждём инициализации states
        await self._load_all_klines()

        while self._running:
            try:
                # Обновляем klines каждую минуту
                await asyncio.sleep(60)
                await self._load_all_klines()
                # FIX-L-1: дневные обновляем раз в час
                if time.time() - self._daily_klines_loaded_at > 3600:
                    await self._load_all_daily_klines()
                    self._daily_klines_loaded_at = time.time()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("klines_monitor_error", error=str(e))
                await asyncio.sleep(30)

    async def _load_all_klines(self):
        """Загрузить klines для всех futures символов."""
        logger.info("loading_futures_klines", count=len(self._futures_symbols))
        tasks = [
            self._fetch_klines_safe(symbol)
            for symbol in self._futures_symbols
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Посчитать успешные загрузки
        loaded = sum(1 for s in self._futures_symbols if self._states.get(s) and float(self._states[s].futures_atr_1h_pct) != 5.0)
        logger.info("futures_klines_loaded", total=len(self._futures_symbols), with_atr=loaded)

    async def _fetch_klines_safe(self, symbol: str):
        """Загрузить klines с rate limiting."""
        async with self._semaphore:
            await asyncio.sleep(settings.rate_limit.futures_request_delay_sec)
            await self._fetch_klines(symbol)

    async def _fetch_klines(self, symbol: str):
        """
        Загрузить 1m klines для расчёта ATR.

        Endpoint: /fapi/v1/klines
        Interval: 1m
        Limit: 60 (1 hour)
        """
        params = {
            "symbol": symbol,
            "interval": "1m",
            "limit": 60,
        }

        try:
            data = await self._client.get("/fapi/v1/klines", params=params)

            if not data:
                return

            state = self._states.get(symbol)
            if not state:
                return

            # Парсим klines: [open_time, open, high, low, close, ...]
            klines = []
            for k in data:
                try:
                    high = Decimal(str(k[2]))
                    low = Decimal(str(k[3]))
                    close = Decimal(str(k[4]))
                    klines.append((high, low, close))
                except (IndexError, ValueError):
                    continue

            state.futures_klines_1h = klines

            # Рассчитываем ATR
            if len(klines) >= 14:
                # FIX-ATR-RAW: сохраняем raw и clamped отдельно
                raw_atr = self._calculate_atr_pct(klines)
                state.futures_atr_1h_pct_raw = raw_atr  # реальный ATR для SL/TP
                state.futures_atr_1h_pct = max(
                    self.ATR_DEPTH_MIN_PCT,
                    min(self.ATR_DEPTH_MAX_PCT, raw_atr)
                )  # clamped для orderbook depth
                state.futures_atr_is_real = True  # FIX-D-2: ATR рассчитан реально
                logger.debug(
                    "futures_atr_calculated",
                    symbol=symbol,
                    atr_pct_raw=float(raw_atr),
                    atr_pct_clamped=float(state.futures_atr_1h_pct),
                )

        except BinanceBannedError:
            logger.warning("klines_fetch_banned", symbol=symbol)
        except BinanceRateLimitError:
            logger.warning("klines_fetch_rate_limited", symbol=symbol)
        except Exception as e:
            logger.warning("klines_fetch_error", symbol=symbol, error=str(e))

    # =========================================================================
    # FIX-L-1: DAILY KLINES FOR DAILY ATR
    # =========================================================================

    async def _load_all_daily_klines(self):
        """FIX-L-1: Загрузить дневные klines для всех futures символов."""
        logger.info("loading_futures_daily_klines", count=len(self._futures_symbols))
        tasks = [
            self._fetch_daily_klines_safe(symbol)
            for symbol in self._futures_symbols
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("futures_daily_klines_loaded")

    async def _fetch_daily_klines_safe(self, symbol: str):
        """FIX-L-1: Загрузить дневные klines с rate limiting."""
        async with self._semaphore:
            await asyncio.sleep(settings.rate_limit.futures_request_delay_sec)
            await self._fetch_daily_klines(symbol)

    async def _fetch_daily_klines(self, symbol: str):
        """
        FIX-L-1: Загрузить дневные klines для расчёта daily ATR.

        Endpoint: /fapi/v1/klines
        Interval: 1d
        Limit: 20 (для ATR-14)
        """
        params = {
            "symbol": symbol,
            "interval": "1d",
            "limit": 20,  # FIX-L-1: 20 дней для ATR-14
        }

        try:
            data = await self._client.get("/fapi/v1/klines", params=params)

            if not data:
                return

            state = self._states.get(symbol)
            if not state:
                return

            # Парсим klines: [open_time, open, high, low, close, ...]
            klines = []
            for k in data:
                try:
                    high = Decimal(str(k[2]))
                    low = Decimal(str(k[3]))
                    close = Decimal(str(k[4]))
                    klines.append((high, low, close))
                except (IndexError, ValueError):
                    continue

            # Храним в futures_klines_1h (переиспользуем для daily ATR)
            # Но НЕ перезаписываем minute klines - они нужны для entry zone
            # Рассчитываем daily ATR отдельно
            if len(klines) >= 14:
                # FIX-ATR-RAW: daily ATR используется только для depth, применяем clamp
                raw_daily = self._calculate_atr_pct(klines)
                state.futures_klines_1d = klines
                state.futures_atr_daily_pct = max(
                    self.ATR_DEPTH_MIN_PCT,
                    min(self.ATR_DEPTH_MAX_PCT, raw_daily)
                )
                state.futures_atr_daily_is_real = True
                logger.debug(
                    "futures_daily_atr_calculated",
                    symbol=symbol,
                    atr_daily_pct=float(state.futures_atr_daily_pct),
                )

        except BinanceBannedError:
            logger.warning("daily_klines_fetch_banned", symbol=symbol)
        except BinanceRateLimitError:
            logger.warning("daily_klines_fetch_rate_limited", symbol=symbol)
        except Exception as e:
            logger.warning("daily_klines_fetch_error", symbol=symbol, error=str(e))

    def _calculate_atr_pct(self, klines: list, period: int = 14) -> Decimal:
        """
        Рассчитать RAW ATR как процент от цены (без clamp).

        FIX-ATR-RAW: возвращает реальное значение ATR.
        Clamp для orderbook depth применяется в месте вызова.

        Args:
            klines: Список (high, low, close) кортежей
            period: Период ATR (default 14)

        Returns:
            RAW ATR как процент от цены (например 0.4 = 0.4%)
        """
        if len(klines) < period + 1:
            return Decimal("0")  # FIX-ATR-RAW: 0 означает "нет данных"

        # Рассчитываем True Range для каждой свечи
        true_ranges = []
        for i in range(1, len(klines)):
            high, low, close = klines[i]
            _, _, prev_close = klines[i - 1]

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr)

        if not true_ranges:
            return Decimal("0")

        # EMA of True Range
        atr = true_ranges[0]
        multiplier = Decimal("2") / (Decimal(period) + Decimal("1"))
        for tr in true_ranges[1:]:
            atr = (tr - atr) * multiplier + atr

        # ATR как % от последней цены
        last_close = klines[-1][2]
        if last_close == 0:
            return Decimal("0")

        atr_pct = (atr / last_close) * 100

        # FIX-ATR-RAW: возвращаем RAW значение, clamp не применяем
        return Decimal(str(round(float(atr_pct), 4)))

    # =========================================================================
    # DATA FETCHING
    # =========================================================================

    async def _check_futures_availability(self, symbols: list[str]):
        """
        Проверить какие пары имеют фьючерсы.

        КРИТИЧНО: Использует BinanceApiClient для обработки 418/429!
        """
        try:
            data = await self._client.get("/fapi/v1/exchangeInfo")

            futures_symbols = set()
            for s in data.get("symbols", []):
                if s.get("status") == "TRADING" and s.get("contractType") == "PERPETUAL":
                    futures_symbols.add(s["symbol"])

            # Пересечение с запрошенными парами
            self._futures_symbols = futures_symbols.intersection(set(symbols))

        except BinanceBannedError as e:
            logger.error("futures_availability_check_banned", retry_after=e.retry_after)
        except BinanceRateLimitError as e:
            logger.warning("futures_availability_check_rate_limited", retry_after=e.retry_after)
        except Exception as e:
            logger.error("futures_availability_check_error", error=str(e))

    async def _update_all_oi(self):
        """Обновить OI для всех пар."""
        tasks = [
            self._fetch_oi_safe(symbol)
            for symbol in self._futures_symbols
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _load_historical_funding(self):
        """
        Загрузить историческую funding rate за последние 8 периодов при старте.

        Использует endpoint /fapi/v1/fundingRate с limit=8.
        Funding обновляется каждые 8 часов, 8 записей = ~2.5 дня истории.
        Нужно для funding gradient анализа с первой минуты работы.
        """
        logger.info("loading_historical_funding", symbols_count=len(self._futures_symbols))

        # Batch processing с retry - разбиваем на группы по 10 для уменьшения rate limit pressure
        symbols_list = list(self._futures_symbols)
        batch_size = 10
        total_success = 0

        for i in range(0, len(symbols_list), batch_size):
            batch = symbols_list[i:i + batch_size]
            tasks = [
                self._fetch_historical_funding_safe(symbol)
                for symbol in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            total_success += sum(1 for r in results if r is True)

            # Пауза между batch'ами для снижения rate limit
            if i + batch_size < len(symbols_list):
                await asyncio.sleep(1.0)

        logger.info(
            "historical_funding_loaded",
            success=total_success,
            total=len(self._futures_symbols)
        )

    async def _fetch_historical_funding_safe(self, symbol: str) -> bool:
        """Загрузить историческую funding с rate limiting и retry."""
        max_retries = 2
        for attempt in range(max_retries + 1):
            async with self._semaphore:
                await asyncio.sleep(settings.rate_limit.futures_request_delay_sec)
                result = await self._fetch_historical_funding(symbol)
                if result:
                    return True
                # Exponential backoff при неудаче
                if attempt < max_retries:
                    await asyncio.sleep(1.0 * (attempt + 1))
        return False

    async def _fetch_historical_funding(self, symbol: str) -> bool:
        """
        Загрузить историческую funding rate для пары.

        Endpoint: /fapi/v1/fundingRate
        Limit: 8 (последние 8 периодов = ~2.5 дня)
        """
        params = {
            "symbol": symbol,
            "limit": 8,
        }

        try:
            data = await self._client.get("/fapi/v1/fundingRate", params=params)

            if not data:
                return False

            state = self._states.get(symbol)
            if not state:
                return False

            # Парсим и добавляем в историю
            for item in data:
                try:
                    funding_data = FundingRateData(
                        symbol=symbol,
                        funding_rate=Decimal(str(item.get("fundingRate", "0"))),
                        funding_time=int(item.get("fundingTime", 0)),
                        mark_price=Decimal(str(item.get("markPrice", "0"))),
                        timestamp=datetime.fromtimestamp(int(item.get("fundingTime", 0)) / 1000),
                    )
                    # Избегаем дубликатов по timestamp
                    if not any(f.timestamp == funding_data.timestamp for f in state.funding_history):
                        state.funding_history.append(funding_data)
                except (KeyError, ValueError) as e:
                    logger.debug("funding_history_item_parse_error", symbol=symbol, error=str(e))
                    continue

            # Сортируем по времени
            state.funding_history.sort(key=lambda x: x.timestamp)

            # Ограничиваем размер (24 записи = 8 дней)
            if len(state.funding_history) > 24:
                state.funding_history = state.funding_history[-24:]

            logger.debug(
                "historical_funding_fetched",
                symbol=symbol,
                records=len(state.funding_history),
            )
            return True

        except BinanceBannedError:
            logger.warning("historical_funding_banned", symbol=symbol)
            return False
        except BinanceRateLimitError:
            logger.warning("historical_funding_rate_limited", symbol=symbol)
            return False
        except Exception as e:
            logger.warning("historical_funding_fetch_error", symbol=symbol, error=str(e))
            return False

    async def _load_historical_oi(self):
        """
        Загрузить историческую OI за последний час при старте.

        Использует endpoint /futures/data/openInterestHist с period=5m.
        Это даёт 12 точек данных за час, достаточно для расчёта oi_change_1h.
        """
        logger.info("loading_historical_oi", symbols_count=len(self._futures_symbols))

        # Batch processing - разбиваем на группы для уменьшения rate limit pressure
        symbols_list = list(self._futures_symbols)
        batch_size = 10
        total_success = 0

        for i in range(0, len(symbols_list), batch_size):
            batch = symbols_list[i:i + batch_size]
            tasks = [
                self._fetch_historical_oi_safe(symbol)
                for symbol in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            total_success += sum(1 for r in results if r is True)

            # Пауза между batch'ами
            if i + batch_size < len(symbols_list):
                await asyncio.sleep(1.0)

        logger.info(
            "historical_oi_loaded",
            success=total_success,
            total=len(self._futures_symbols)
        )

    async def _fetch_historical_oi_safe(self, symbol: str) -> bool:
        """Загрузить историческую OI с rate limiting и retry."""
        max_retries = 2
        for attempt in range(max_retries + 1):
            async with self._semaphore:
                await asyncio.sleep(settings.rate_limit.futures_request_delay_sec)
                result = await self._fetch_historical_oi(symbol)
                if result:
                    return True
                # Exponential backoff при неудаче
                if attempt < max_retries:
                    await asyncio.sleep(1.0 * (attempt + 1))
        return False

    async def _fetch_historical_oi(self, symbol: str) -> bool:
        """
        Загрузить историческую OI для пары.

        КРИТИЧНО: Использует BinanceApiClient для обработки 418/429!

        Endpoint: /futures/data/openInterestHist
        Period: 5m (12 записей = 1 час)
        """
        params = {
            "symbol": symbol,
            "period": "5m",
            "limit": 13,  # 13 * 5min = 65 min (чуть больше часа)
        }

        try:
            data = await self._client.get("/futures/data/openInterestHist", params=params)

            if not data:
                return False

            state = self._states.get(symbol)
            if not state:
                return False

            # Получить mark price для конвертации (используем текущий если есть)
            mark_price = Decimal("0")
            if state.current_funding:
                mark_price = state.current_funding.mark_price

            # Добавить исторические записи в oi_history
            for item in data:
                # API возвращает:
                # - sumOpenInterest: OI в базовом активе
                # - sumOpenInterestValue: OI в USD
                # - timestamp: время в ms
                oi_data = OpenInterestData(
                    symbol=symbol,
                    open_interest=Decimal(str(item.get("sumOpenInterest", "0"))),
                    mark_price=mark_price,
                    timestamp=datetime.fromtimestamp(item["timestamp"] / 1000),
                )
                state.oi_history.append(oi_data)

            # Сортируем по времени (старые записи первыми)
            state.oi_history.sort(key=lambda x: x.timestamp)

            logger.debug(
                "historical_oi_fetched",
                symbol=symbol,
                records=len(data),
                oldest=state.oi_history[0].timestamp.isoformat() if state.oi_history else None
            )
            return True

        except BinanceBannedError:
            logger.warning("historical_oi_banned", symbol=symbol)
            return False
        except BinanceRateLimitError:
            logger.warning("historical_oi_rate_limited", symbol=symbol)
            return False
        except Exception as e:
            logger.warning("historical_oi_fetch_error", symbol=symbol, error=str(e))
            return False

    async def _fetch_oi_safe(self, symbol: str):
        """Загрузить OI с rate limiting."""
        async with self._semaphore:
            await asyncio.sleep(settings.rate_limit.futures_request_delay_sec)
            await self._fetch_oi(symbol)

    async def _fetch_oi(self, symbol: str):
        """
        Загрузить Open Interest для пары.

        КРИТИЧНО: Использует BinanceApiClient для обработки 418/429!
        """
        try:
            data = await self._client.get("/fapi/v1/openInterest", params={"symbol": symbol})

            # Получить mark price из уже загруженных данных funding
            mark_price = Decimal("0")
            state = self._states.get(symbol)
            if state and state.current_funding:
                mark_price = state.current_funding.mark_price

            oi_data = OpenInterestData(
                symbol=symbol,
                open_interest=Decimal(str(data["openInterest"])),
                mark_price=mark_price,
                timestamp=datetime.now(),
            )

            state = self._states.get(symbol)
            if state:
                # Сохранить в историю
                state.oi_history.append(oi_data)

                # CALC-2 FIX: Оставить 75 минут (было 65) для tolerance=2мин
                # Нужно: 60 мин ± 2 мин tolerance + 10 мин буфер на задержки
                cutoff = datetime.now() - timedelta(hours=1, minutes=15)
                state.oi_history = [
                    oi for oi in state.oi_history
                    if oi.timestamp > cutoff
                ]

                # Расчёт изменений
                old_oi = state.current_oi
                state.current_oi = oi_data

                # 1-минутное изменение (от предыдущего значения)
                if old_oi and old_oi.open_interest > 0:
                    state.oi_change_1m_pct = (
                        (oi_data.open_interest - old_oi.open_interest)
                        / old_oi.open_interest * 100
                    )

                # 5-минутное изменение (ищем запись ~5 минут назад)
                oi_5m_ago = self._find_oi_at_time(
                    state.oi_history,
                    datetime.now() - timedelta(minutes=5)
                )
                if oi_5m_ago and oi_5m_ago.open_interest > 0:
                    state.oi_change_5m_pct = (
                        (oi_data.open_interest - oi_5m_ago.open_interest)
                        / oi_5m_ago.open_interest * 100
                    )

                # Часовое изменение (ищем запись ~60 минут назад)
                oi_1h_ago = self._find_oi_at_time(
                    state.oi_history,
                    datetime.now() - timedelta(hours=1)
                )
                if oi_1h_ago and oi_1h_ago.open_interest > 0:
                    state.oi_change_1h_pct = (
                        (oi_data.open_interest - oi_1h_ago.open_interest)
                        / oi_1h_ago.open_interest * 100
                    )

                state.last_update = datetime.now()

                # Обновить price_history из mark_price (для divergence детектора)
                if oi_data.mark_price > 0:
                    state.price_history.append((datetime.now(), oi_data.mark_price))
                    price_cutoff = datetime.now() - timedelta(hours=1, minutes=5)
                    state.price_history = [
                        (ts, price) for ts, price in state.price_history
                        if ts > price_cutoff
                    ]
                    self._calculate_price_changes(state, oi_data.mark_price)

                # Проверить детекции
                await self._check_oi_detections(state)

        except BinanceBannedError:
            logger.warning("oi_fetch_banned", symbol=symbol)
        except BinanceRateLimitError:
            logger.warning("oi_fetch_rate_limited", symbol=symbol)
        except Exception as e:
            logger.debug("oi_fetch_error", symbol=symbol, error=str(e))

    async def _update_all_funding(self):
        """
        Обновить Funding Rate для всех пар.

        КРИТИЧНО: Использует BinanceApiClient для обработки 418/429!
        """
        try:
            # Используем batch endpoint для эффективности
            data = await self._client.get("/fapi/v1/premiumIndex")

            for item in data:
                symbol = item.get("symbol")
                if symbol not in self._futures_symbols:
                    continue

                try:
                    funding_data = FundingRateData(
                        symbol=symbol,
                        funding_rate=Decimal(str(item.get("lastFundingRate", "0"))),
                        funding_time=int(item.get("nextFundingTime", 0)),
                        mark_price=Decimal(str(item.get("markPrice", "0"))),
                    )

                    state = self._states.get(symbol)
                    if state:
                        # Сохранить в историю funding (для gradient)
                        state.funding_history.append(funding_data)
                        # CALC-3 FIX: 24 записи = 8 дней (было 10 = 3.3 дня)
                        # Больше данных = стабильнее gradient, меньше ложных срабатываний
                        if len(state.funding_history) > 24:
                            state.funding_history = state.funding_history[-24:]

                        # Сохранить в историю цен (mark_price)
                        state.price_history.append((datetime.now(), funding_data.mark_price))
                        # Оставить последний час + запас
                        price_cutoff = datetime.now() - timedelta(hours=1, minutes=5)
                        state.price_history = [
                            (ts, price) for ts, price in state.price_history
                            if ts > price_cutoff
                        ]

                        # Рассчитать изменение цены
                        self._calculate_price_changes(state, funding_data.mark_price)

                        state.current_funding = funding_data
                        state.last_update = datetime.now()

                        # Проверить детекции (существующие + новые)
                        await self._check_funding_detections(state)
                        await self._check_funding_gradient(state)
                        await self._check_accumulation(state)

                except (KeyError, ValueError) as e:
                    logger.debug("funding_parse_error", symbol=symbol, error=str(e))

        except BinanceBannedError as e:
            logger.error("funding_fetch_banned", retry_after=e.retry_after)
        except BinanceRateLimitError as e:
            logger.warning("funding_fetch_rate_limited", retry_after=e.retry_after)
        except Exception as e:
            logger.error("funding_fetch_error", error=str(e))

    async def _update_all_ls_ratio(self):
        """Обновить Long/Short Ratio для всех пар."""
        tasks = [
            self._fetch_ls_ratio_safe(symbol)
            for symbol in self._futures_symbols
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_ls_ratio_safe(self, symbol: str):
        """Загрузить L/S ratio с rate limiting."""
        async with self._semaphore:
            await asyncio.sleep(settings.rate_limit.futures_request_delay_sec)
            await self._fetch_ls_ratio(symbol)

    async def _fetch_ls_ratio(self, symbol: str):
        """
        Загрузить Long/Short Ratio для пары.

        КРИТИЧНО: Использует BinanceApiClient для обработки 418/429!
        """
        params = {"symbol": symbol, "period": "5m", "limit": 1}

        try:
            data = await self._client.get("/futures/data/globalLongShortAccountRatio", params=params)

            if not data:
                return

            item = data[0]
            # API возвращает доли: longAccount=0.5523 означает 55.23%
            # Умножаем на 100 чтобы получить проценты
            long_raw = Decimal(str(item.get("longAccount", "0.5")))
            short_raw = Decimal(str(item.get("shortAccount", "0.5")))

            ls_data = LongShortRatioData(
                symbol=symbol,
                long_short_ratio=Decimal(str(item.get("longShortRatio", "1"))),
                long_account_pct=long_raw * 100,   # 0.5523 -> 55.23
                short_account_pct=short_raw * 100,  # 0.4477 -> 44.77
            )

            state = self._states.get(symbol)
            if state:
                state.current_ls_ratio = ls_data
                state.last_update = datetime.now()

                # Проверить детекции
                await self._check_ls_ratio_detections(state)

        except BinanceBannedError:
            logger.warning("ls_ratio_fetch_banned", symbol=symbol)
        except BinanceRateLimitError:
            logger.warning("ls_ratio_fetch_rate_limited", symbol=symbol)
        except Exception as e:
            logger.debug("ls_ratio_fetch_error", symbol=symbol, error=str(e))

    # =========================================================================
    # DETECTIONS
    # =========================================================================

    async def _check_oi_detections(self, state: FuturesState):
        """Проверить детекции на основе OI."""
        if not state.current_oi:
            return

        detections = []

        # 1. Резкий рост OI (накопление позиций)
        if state.oi_change_1h_pct >= settings.futures.oi_spike_critical:
            oi_usd = state.current_oi.open_interest_usd if state.current_oi else Decimal("0")
            detections.append(FuturesDetection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                detection_type="WHALE_ACCUMULATION_CRITICAL",
                severity=AlertSeverity.CRITICAL,
                score=95,
                details={
                    "oi_change_1h_pct": float(state.oi_change_1h_pct),
                    "oi_change_5m_pct": float(state.oi_change_5m_pct),
                    "current_oi_usd": float(oi_usd),
                },
                evidence=[
                    f"🐋 WHALE ACCUMULATION DETECTED",
                    f"Open Interest grew {state.oi_change_1h_pct:.1f}% in 1 hour",
                    f"5-min change: {state.oi_change_5m_pct:+.1f}%",
                    f"Current OI: ${oi_usd:,.0f}" if oi_usd > 0 else "OI value: calculating...",
                    "Large players are building positions - PUMP LIKELY SOON",
                ]
            ))
        elif state.oi_change_1h_pct >= settings.futures.oi_spike_alert:
            detections.append(FuturesDetection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                detection_type="OI_SPIKE_HIGH",
                severity=AlertSeverity.ALERT,
                score=75,
                details={"oi_change_1h_pct": float(state.oi_change_1h_pct)},
                evidence=[
                    f"Open Interest grew {state.oi_change_1h_pct:.1f}% in 1 hour",
                    "Significant position building detected",
                ]
            ))
        elif state.oi_change_1h_pct >= settings.futures.oi_spike_warning:
            detections.append(FuturesDetection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                detection_type="OI_SPIKE",
                severity=AlertSeverity.WARNING,
                score=55,
                details={"oi_change_1h_pct": float(state.oi_change_1h_pct)},
                evidence=[f"Open Interest grew {state.oi_change_1h_pct:.1f}% in 1 hour"]
            ))

        # 2. Резкое падение OI (выход из позиций)
        if state.oi_change_5m_pct <= settings.futures.oi_drop_alert:
            detections.append(FuturesDetection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                detection_type="MASS_EXIT_DETECTED",
                severity=AlertSeverity.ALERT,
                score=80,
                details={
                    "oi_change_5m_pct": float(state.oi_change_5m_pct),
                },
                evidence=[
                    f"🚨 MASS EXIT: OI dropped {abs(state.oi_change_5m_pct):.1f}% in 5 min",
                    "Large players are closing positions rapidly",
                    "DUMP MAY BE IN PROGRESS",
                ]
            ))
        elif state.oi_change_5m_pct <= settings.futures.oi_drop_warning:
            detections.append(FuturesDetection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                detection_type="OI_DROP",
                severity=AlertSeverity.WARNING,
                score=60,
                details={"oi_change_5m_pct": float(state.oi_change_5m_pct)},
                evidence=[f"OI dropped {abs(state.oi_change_5m_pct):.1f}% in 5 min"]
            ))

        # Отправить детекции
        for d in detections:
            await self._emit_detection(d)

        # Дополнительная проверка: OI + Price Divergence
        await self._check_oi_price_divergence(state)

    async def _check_funding_detections(self, state: FuturesState):
        """Проверить детекции на основе Funding Rate."""
        if not state.current_funding:
            return

        funding = state.current_funding
        detections = []

        # 1. Экстремально положительный funding (лонги переплачивают)
        if funding.funding_rate_percent >= settings.futures.funding_extreme_positive:
            detections.append(FuturesDetection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                detection_type="FUNDING_EXTREME_LONG",
                severity=AlertSeverity.ALERT,
                score=70,
                details={
                    "funding_rate_pct": float(funding.funding_rate_percent),
                },
                evidence=[
                    f"⚠️ Extreme funding rate: {funding.funding_rate_percent:.3f}%",
                    "Longs are paying high premium",
                    "Market is overcrowded with longs - correction risk",
                ]
            ))

        # 2. Экстремально отрицательный funding (шорты переплачивают)
        elif funding.funding_rate_percent <= settings.futures.funding_extreme_negative:
            detections.append(FuturesDetection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                detection_type="FUNDING_EXTREME_SHORT",
                severity=AlertSeverity.ALERT,
                score=70,
                details={
                    "funding_rate_pct": float(funding.funding_rate_percent),
                },
                evidence=[
                    f"⚠️ Negative funding: {funding.funding_rate_percent:.3f}%",
                    "Shorts are paying premium - longs are cheap",
                    "PRE-PUMP SETUP: Smart money accumulating longs cheaply",
                ]
            ))

        for d in detections:
            await self._emit_detection(d)

    async def _check_ls_ratio_detections(self, state: FuturesState):
        """Проверить детекции на основе Long/Short Ratio."""
        if not state.current_ls_ratio:
            return

        ls = state.current_ls_ratio
        detections = []

        # Экстремальный перекос в лонги (>70%)
        if ls.is_extremely_long:
            detections.append(FuturesDetection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                detection_type="EXTREME_LONG_POSITIONING",
                severity=AlertSeverity.WARNING,
                score=60,
                details={
                    "long_account_pct": float(ls.long_account_pct),
                    "short_account_pct": float(ls.short_account_pct),
                },
                evidence=[
                    f"⚠️ {ls.long_account_pct:.1f}% of accounts are LONG",
                    "Crowd is extremely bullish",
                    "Contrarian signal: correction possible",
                ]
            ))

        # Экстремальный перекос в шорты (>55%)
        elif ls.is_extremely_short:
            # Это часто PRE-PUMP сигнал!
            detections.append(FuturesDetection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                detection_type="EXTREME_SHORT_POSITIONING",
                severity=AlertSeverity.ALERT,
                score=75,
                details={
                    "long_account_pct": float(ls.long_account_pct),
                    "short_account_pct": float(ls.short_account_pct),
                },
                evidence=[
                    f"🎯 {ls.short_account_pct:.1f}% of accounts are SHORT",
                    "Crowd is extremely bearish",
                    "SHORT SQUEEZE SETUP: Pump can liquidate shorts!",
                ]
            ))

        for d in detections:
            await self._emit_detection(d)

    async def _check_futures_orderbook_detections(self, state: FuturesState):
        """
        Проверить детекции на основе FUTURES orderbook (ATR-based).

        Аналогично SPOT ORDERBOOK_IMBALANCE, но для фьючерсного стакана.
        """
        # Проверить что есть данные
        bid_vol = state.futures_bid_volume_atr
        ask_vol = state.futures_ask_volume_atr

        if bid_vol == 0 and ask_vol == 0:
            return

        # FIX-IMBALANCE-1: None = нет данных для детекции
        _raw_imbalance = state.futures_book_imbalance_atr
        if _raw_imbalance is None:
            return
        imbalance = float(_raw_imbalance)
        detections = []

        # Пороги (аналогично settings.spot.imbalance_alert)
        IMBALANCE_ALERT = 0.5    # 50%
        IMBALANCE_WARNING = 0.3  # 30%

        abs_imbalance = abs(imbalance)

        # ALERT: сильный перекос
        if abs_imbalance >= IMBALANCE_ALERT:
            side = "BUY" if imbalance > 0 else "SELL"
            detections.append(FuturesDetection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                detection_type="FUTURES_ORDERBOOK_IMBALANCE",
                severity=AlertSeverity.ALERT,
                score=70,
                details={
                    "imbalance": round(imbalance, 4),
                    "futures_bid_volume_atr": round(float(bid_vol), 2),
                    "futures_ask_volume_atr": round(float(ask_vol), 2),
                    "futures_atr_pct": round(float(state.futures_atr_1h_pct), 2),
                    "dominant_side": side,
                },
                evidence=[
                    f"FUTURES orderbook {abs_imbalance:.0%} imbalanced to {side}",
                    f"ATR depth: ±{float(state.futures_atr_1h_pct):.1f}%",
                    f"Bid: ${float(bid_vol):,.0f} | Ask: ${float(ask_vol):,.0f}",
                ]
            ))
        # WARNING: умеренный перекос
        elif abs_imbalance >= IMBALANCE_WARNING:
            side = "BUY" if imbalance > 0 else "SELL"
            detections.append(FuturesDetection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                detection_type="FUTURES_ORDERBOOK_IMBALANCE_ELEVATED",
                severity=AlertSeverity.WARNING,
                score=50,
                details={
                    "imbalance": round(imbalance, 4),
                    "dominant_side": side,
                    "futures_atr_pct": round(float(state.futures_atr_1h_pct), 2),
                },
                evidence=[
                    f"FUTURES orderbook {abs_imbalance:.0%} imbalanced to {side}",
                ]
            ))

        for d in detections:
            await self._emit_detection(d)

    # =========================================================================
    # НОВЫЕ ДЕТЕКТОРЫ (OI+Price Divergence, Funding Gradient, Accumulation)
    # =========================================================================

    def _calculate_price_changes(self, state: FuturesState, current_price: Decimal):
        """Рассчитать изменение цены за 5m и 1h."""
        if current_price <= 0:
            return

        now = datetime.now()

        # Найти цену 5 минут назад
        price_5m = self._find_price_at_time(state.price_history, now - timedelta(minutes=5))
        if price_5m and price_5m > 0:
            state.price_change_5m_pct = (current_price - price_5m) / price_5m * 100

        # Найти цену 1 час назад
        price_1h = self._find_price_at_time(state.price_history, now - timedelta(hours=1))
        if price_1h and price_1h > 0:
            state.price_change_1h_pct = (current_price - price_1h) / price_1h * 100

    @staticmethod
    def _find_price_at_time(
        history: list[tuple[datetime, Decimal]],
        target_time: datetime,
        tolerance_minutes: int = 3
    ) -> Optional[Decimal]:
        """Найти цену ближайшую к указанному времени."""
        if not history:
            return None

        best_price = None
        best_diff = timedelta(minutes=tolerance_minutes + 1)

        for ts, price in history:
            diff = abs(ts - target_time)
            if diff < best_diff:
                best_diff = diff
                best_price = price

        if best_diff <= timedelta(minutes=tolerance_minutes):
            return best_price

        # Fallback: вернуть самую старую запись если она старше target
        if history and history[0][0] <= target_time:
            return history[0][1]

        return None

    async def _check_oi_price_divergence(self, state: FuturesState):
        """
        ДЕТЕКТОР 1: OI + Price Divergence

        Логика:
        - Цена ↑ + OI ↓ = WEAK_PUMP (short covering, не настоящий тренд) → сигнал SHORT
        - Цена ↓ + OI ↓ = WEAK_DUMP (long liquidation закончилась) → сигнал LONG

        Это классический институциональный паттерн.
        """
        # CALC-5 FIX: Убрана избыточная пред-проверка с abs()
        # Основные условия ниже уже проверяют величину И направление
        # abs() отсекал валидные случаи когда пороги разные для price и oi

        detections = []

        # WEAK PUMP: Цена растёт, но OI падает
        # Это значит: шорты закрываются, новых лонгов нет
        if (state.price_change_5m_pct > settings.futures.divergence_price_threshold and
                state.oi_change_5m_pct < -settings.futures.divergence_oi_threshold):
            detections.append(FuturesDetection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                detection_type="WEAK_PUMP_DIVERGENCE",
                severity=AlertSeverity.ALERT,
                score=75,
                details={
                    "price_change_5m_pct": float(state.price_change_5m_pct),
                    "oi_change_5m_pct": float(state.oi_change_5m_pct),
                },
                evidence=[
                    f"📉 WEAK PUMP: Price ↑{state.price_change_5m_pct:.1f}% but OI ↓{abs(state.oi_change_5m_pct):.1f}%",
                    "This is SHORT COVERING, not real buying",
                    "No new longs entering - pump is exhausting",
                    "🎯 SHORT SIGNAL: High probability of reversal",
                ]
            ))

        # WEAK DUMP: Цена падает и OI падает
        # Это значит: лонги ликвидируются, дамп выдыхается
        elif (state.price_change_5m_pct < -settings.futures.divergence_price_threshold and
              state.oi_change_5m_pct < -settings.futures.divergence_oi_threshold):
            detections.append(FuturesDetection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                detection_type="WEAK_DUMP_DIVERGENCE",
                severity=AlertSeverity.ALERT,
                score=70,
                details={
                    "price_change_5m_pct": float(state.price_change_5m_pct),
                    "oi_change_5m_pct": float(state.oi_change_5m_pct),
                },
                evidence=[
                    f"📈 DUMP EXHAUSTION: Price ↓{abs(state.price_change_5m_pct):.1f}% and OI ↓{abs(state.oi_change_5m_pct):.1f}%",
                    "Longs are liquidating, selling pressure decreasing",
                    "Dump is running out of fuel",
                    "🎯 LONG SIGNAL: Bounce likely after liquidations complete",
                ]
            ))

        for d in detections:
            await self._emit_detection(d)

    async def _check_funding_gradient(self, state: FuturesState):
        """
        ДЕТЕКТОР 2: Funding Gradient

        Логика:
        - Если funding резко растёт за последние 3 периода → толпа массово входит в лонги
        - Это contrarian сигнал: скоро разворот вниз

        Формула: (funding_now - funding_3_periods_ago) > threshold
        """
        if len(state.funding_history) < 3:
            return

        # Последние 3 записи funding
        recent = state.funding_history[-3:]
        oldest_funding = recent[0].funding_rate_percent
        newest_funding = recent[-1].funding_rate_percent

        gradient = newest_funding - oldest_funding

        detections = []

        # Резкий рост funding = толпа в лонгах = готовится dump
        if gradient >= settings.futures.funding_gradient_threshold:
            detections.append(FuturesDetection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                detection_type="FUNDING_GRADIENT_SPIKE",
                severity=AlertSeverity.WARNING,
                score=65,
                details={
                    "funding_gradient": float(gradient),
                    "funding_now": float(newest_funding),
                    "funding_before": float(oldest_funding),
                },
                evidence=[
                    f"⚠️ FUNDING SPIKE: Gradient +{gradient:.3f}% over 3 periods",
                    f"Funding jumped from {oldest_funding:.3f}% to {newest_funding:.3f}%",
                    "Retail is piling into longs aggressively",
                    "Contrarian signal: Correction risk increasing",
                ]
            ))

        # Резкое падение funding = толпа в шортах = готовится pump
        elif gradient <= -settings.futures.funding_gradient_threshold:
            detections.append(FuturesDetection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                detection_type="FUNDING_GRADIENT_DROP",
                severity=AlertSeverity.WARNING,
                score=65,
                details={
                    "funding_gradient": float(gradient),
                    "funding_now": float(newest_funding),
                    "funding_before": float(oldest_funding),
                },
                evidence=[
                    f"📊 FUNDING DROP: Gradient {gradient:.3f}% over 3 periods",
                    f"Funding dropped from {oldest_funding:.3f}% to {newest_funding:.3f}%",
                    "Retail is piling into shorts",
                    "Contrarian signal: Bounce/squeeze risk increasing",
                ]
            ))

        for d in detections:
            await self._emit_detection(d)

    async def _check_accumulation(self, state: FuturesState):
        """
        ДЕТЕКТОР 3: Whale Accumulation

        Логика институциональная:
        - Цена в узком диапазоне (< 2% за час) = нет направленного движения
        - OI растёт (> 5% за час) = кто-то строит позицию
        - Funding нейтральный = нет перекоса толпы

        Это классический паттерн набора позиции крупным игроком.
        """
        if not state.current_funding:
            return

        funding_pct = state.current_funding.funding_rate_percent

        # Проверяем все условия
        price_range_ok = abs(state.price_change_1h_pct) < settings.futures.accumulation_price_range
        oi_growth_ok = state.oi_change_1h_pct >= settings.futures.accumulation_oi_growth
        funding_neutral = (settings.futures.accumulation_funding_min <= funding_pct <= settings.futures.accumulation_funding_max)

        if not (price_range_ok and oi_growth_ok and funding_neutral):
            return

        oi_usd = Decimal("0")
        if state.current_oi:
            oi_usd = state.current_oi.open_interest_usd

        detection = FuturesDetection(
            symbol=state.symbol,
            timestamp=datetime.now(),
            detection_type="WHALE_ACCUMULATION_STEALTH",
            severity=AlertSeverity.ALERT,
            score=80,
            details={
                "price_change_1h_pct": float(state.price_change_1h_pct),
                "oi_change_1h_pct": float(state.oi_change_1h_pct),
                "funding_rate_pct": float(funding_pct),
                "oi_usd": float(oi_usd),
            },
            evidence=[
                f"🐋 STEALTH ACCUMULATION DETECTED",
                f"Price stable: only {abs(state.price_change_1h_pct):.1f}% move in 1 hour",
                f"OI growing: +{state.oi_change_1h_pct:.1f}% (positions being built)",
                f"Funding neutral: {funding_pct:.3f}% (no retail FOMO)",
                f"Current OI: ${oi_usd:,.0f}" if oi_usd > 0 else "",
                "Large player is accumulating quietly",
                "🚀 PUMP LIKELY: Watch for breakout!",
            ]
        )

        await self._emit_detection(detection)

    # =========================================================================
    # SMART DEDUPLICATION
    # - Полный дубль (все параметры 1 в 1) → 5 минут
    # - Тот же тип, но другие параметры → 3 секунды
    # =========================================================================

    DEDUP_EXACT_MATCH_SEC = 300   # 5 минут для полного дубля
    DEDUP_SAME_TYPE_SEC = 3       # 3 секунды для того же типа с другими параметрами

    def _compute_fingerprint(self, detection: FuturesDetection) -> str:
        """
        Вычислить уникальный fingerprint детекции на основе всех параметров.

        Включает: symbol, detection_type, score, все значения из details.
        """
        # Собираем все значимые параметры
        data = {
            "symbol": detection.symbol,
            "type": detection.detection_type,
            "score": detection.score,
            "details": detection.details,
        }
        # Сериализуем в JSON с сортировкой ключей для консистентности
        serialized = json.dumps(data, sort_keys=True, default=str)
        # Возвращаем короткий хэш
        return hashlib.md5(serialized.encode()).hexdigest()[:16]

    def _is_duplicate(self, detection: FuturesDetection) -> bool:
        """
        Умная проверка дубликатов.

        Returns:
            True если детекция является дубликатом и должна быть пропущена
        """
        key = (detection.symbol, detection.detection_type)
        last_record = self._last_detections.get(key)

        if last_record is None:
            return False

        last_time, last_fingerprint = last_record
        elapsed = (datetime.now() - last_time).total_seconds()
        current_fingerprint = self._compute_fingerprint(detection)

        # Полный дубль (все параметры совпадают) → 5 минут
        if current_fingerprint == last_fingerprint:
            if elapsed < self.DEDUP_EXACT_MATCH_SEC:
                logger.debug(
                    "dedup_exact_match",
                    symbol=detection.symbol,
                    type=detection.detection_type,
                    elapsed=f"{elapsed:.1f}s",
                    threshold=f"{self.DEDUP_EXACT_MATCH_SEC}s",
                )
                return True
        # Тот же тип, но другие параметры → 3 секунды
        else:
            if elapsed < self.DEDUP_SAME_TYPE_SEC:
                logger.debug(
                    "dedup_same_type",
                    symbol=detection.symbol,
                    type=detection.detection_type,
                    elapsed=f"{elapsed:.1f}s",
                    threshold=f"{self.DEDUP_SAME_TYPE_SEC}s",
                )
                return True

        return False

    def _record_detection(self, detection: FuturesDetection):
        """Записать детекцию для дедупликации."""
        key = (detection.symbol, detection.detection_type)
        fingerprint = self._compute_fingerprint(detection)
        self._last_detections[key] = (datetime.now(), fingerprint)

        # Очистка старых записей (>1 час)
        cutoff = datetime.now() - timedelta(hours=1)
        self._last_detections = {
            k: (t, fp) for k, (t, fp) in self._last_detections.items()
            if t > cutoff
        }

    async def _emit_detection(self, detection: FuturesDetection):
        """Отправить детекцию через callback с умной дедупликацией."""
        # Проверить дубликат
        if self._is_duplicate(detection):
            return

        # Записать для дедупликации
        self._record_detection(detection)

        logger.info(
            "futures_detection",
            symbol=detection.symbol,
            type=detection.detection_type,
            severity=detection.severity.name,
            score=detection.score,
        )

        # FIX-TASK-1: используем _spawn_callback_task для safe fire-and-forget
        if self._on_detection:
            try:
                result = self._on_detection(detection)
                if asyncio.iscoroutine(result):
                    self._spawn_callback_task(result)
            except Exception as e:
                logger.debug("detection_callback_error", error=str(e))

    # =========================================================================
    # HELPERS
    # =========================================================================

    async def _get_session(self) -> aiohttp.ClientSession:
        """Получить HTTP сессию (legacy, использует клиент)."""
        return await self._client.get_session()

    @staticmethod
    def _find_oi_at_time(
        history: list[OpenInterestData],
        target_time: datetime,
        tolerance_minutes: int = 2
    ) -> Optional[OpenInterestData]:
        """
        Найти запись OI ближайшую к указанному времени.

        Args:
            history: История OI
            target_time: Целевое время
            tolerance_minutes: Допустимое отклонение в минутах

        Returns:
            Ближайшая запись или None если нет подходящей
        """
        if not history:
            return None

        best_match = None
        best_diff = timedelta(minutes=tolerance_minutes + 1)

        for oi in history:
            diff = abs(oi.timestamp - target_time)
            if diff < best_diff:
                best_diff = diff
                best_match = oi

        # Проверить что нашли в пределах допуска
        if best_match and best_diff <= timedelta(minutes=tolerance_minutes):
            return best_match

        # Если точного нет - вернуть самую старую запись если она старше target
        if history and history[0].timestamp <= target_time:
            return history[0]

        return None

    # =========================================================================
    # HISTORY LOADER INTEGRATION
    # =========================================================================

    async def _cache_funding_history(self, symbol: str, data: list[dict]):
        """
        Кэшировать историю funding rate из HistoryLoader.

        Args:
            symbol: Символ пары
            data: Список записей от /fapi/v1/fundingRate API
        """
        state = self._states.get(symbol)
        if not state:
            # Создаём state если нет
            state = FuturesState(symbol=symbol)
            self._states[symbol] = state

        for item in data:
            try:
                # API возвращает:
                # - fundingRate: string (например "0.0001")
                # - fundingTime: int (timestamp ms)
                # - symbol: string
                # - markPrice: может быть в некоторых ответах
                funding_data = FundingRateData(
                    symbol=symbol,
                    funding_rate=Decimal(str(item.get("fundingRate", "0"))),
                    funding_time=int(item.get("fundingTime", 0)),
                    mark_price=Decimal(str(item.get("markPrice", "0"))),
                    timestamp=datetime.fromtimestamp(int(item.get("fundingTime", 0)) / 1000),
                )
                state.funding_history.append(funding_data)
            except (KeyError, ValueError) as e:
                logger.debug("funding_history_parse_error", symbol=symbol, error=str(e))

        # Сортируем по времени (старые первыми)
        state.funding_history.sort(key=lambda x: x.timestamp)

        # Оставляем последние 24 записи (8 дней при 8h интервале)
        if len(state.funding_history) > 24:
            state.funding_history = state.funding_history[-24:]

        logger.debug(
            "funding_history_cached",
            symbol=symbol,
            records=len(state.funding_history),
        )

    async def _cache_oi_history(self, symbol: str, data: list[dict]):
        """
        Кэшировать историю OI из HistoryLoader.

        Args:
            symbol: Символ пары
            data: Список записей от /futures/data/openInterestHist API
        """
        state = self._states.get(symbol)
        if not state:
            state = FuturesState(symbol=symbol)
            self._states[symbol] = state

        # Получить mark price для конвертации
        mark_price = Decimal("0")
        if state.current_funding:
            mark_price = state.current_funding.mark_price

        for item in data:
            try:
                # API возвращает:
                # - sumOpenInterest: OI в базовом активе
                # - sumOpenInterestValue: OI в USD
                # - timestamp: время в ms
                oi_data = OpenInterestData(
                    symbol=symbol,
                    open_interest=Decimal(str(item.get("sumOpenInterest", "0"))),
                    mark_price=mark_price,
                    timestamp=datetime.fromtimestamp(int(item.get("timestamp", 0)) / 1000),
                )
                state.oi_history.append(oi_data)
            except (KeyError, ValueError) as e:
                logger.debug("oi_history_parse_error", symbol=symbol, error=str(e))

        # Сортируем по времени (старые первыми)
        state.oi_history.sort(key=lambda x: x.timestamp)

        logger.debug(
            "oi_history_cached",
            symbol=symbol,
            records=len(state.oi_history),
        )

    def get_combined_signal(self, symbol: str) -> dict:
        """
        Получить комбинированный сигнал по паре.
        Используется для корреляции с spot детекциями.
        """
        state = self._states.get(symbol)
        if not state or not state.has_futures:
            return {"has_data": False}

        signal = {
            "has_data": True,
            "symbol": symbol,
            "oi_change_1h_pct": float(state.oi_change_1h_pct),
            "oi_change_5m_pct": float(state.oi_change_5m_pct),
        }

        if state.current_funding:
            signal["funding_rate_pct"] = float(state.current_funding.funding_rate_percent)
            signal["funding_negative"] = state.current_funding.funding_rate < 0

        if state.current_ls_ratio:
            signal["long_pct"] = float(state.current_ls_ratio.long_account_pct)
            signal["short_pct"] = float(state.current_ls_ratio.short_account_pct)
            signal["crowd_bearish"] = state.current_ls_ratio.is_extremely_short

        # Комбинированный score риска пампа (0-100)
        pump_risk = 0

        # OI растёт = +30
        if state.oi_change_1h_pct > 15:
            pump_risk += 30
        elif state.oi_change_1h_pct > 10:
            pump_risk += 20

        # Funding negative = +25 (лонги дешёвые)
        if state.current_funding and state.current_funding.funding_rate < 0:
            pump_risk += 25

        # Crowd bearish = +30 (short squeeze setup)
        if state.current_ls_ratio and state.current_ls_ratio.is_extremely_short:
            pump_risk += 30

        # OI недавно падал но остановился = +15 (накопление после сброса)
        if state.oi_change_1h_pct > 5 and state.oi_change_5m_pct > 0:
            pump_risk += 15

        signal["pump_risk_score"] = min(100, pump_risk)

        return signal
