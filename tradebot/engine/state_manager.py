# -*- coding: utf-8 -*-
"""
State Manager - Сохранение и восстановление состояния TradeBot.

При shutdown:
- Сохраняет позиции, статистику, missing_tp в JSON

При startup:
- Загружает сохранённое состояние
- Синхронизирует с биржей (получает реальные позиции/ордера)
- Идентифицирует SL/TP ордера
- Ставит недостающие SL/TP
- Проверяет max_hold_days - закрывает просроченные
- Берёт всё как "родное" и продолжает мониторинг
"""

import json
import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .trade_engine import TradeEngine
    from .position_manager import PositionManager
    from .metrics import MetricsTracker
    from ..adapters.binance import BinanceFuturesAdapter

from ..core.models import (
    Position,
    PositionSide,
    PositionStatus,
    OrderSide,
)

logger = logging.getLogger(__name__)

# Путь к файлу состояния по умолчанию
DEFAULT_STATE_FILE = "tradebot_state.json"


class StateManager:
    """
    Менеджер состояния - сохранение/восстановление/синхронизация.

    Usage:
        state_mgr = StateManager(trade_engine, position_manager, exchange)

        # При shutdown
        state_mgr.save_state()

        # При startup
        await state_mgr.restore_and_sync()
    """

    def __init__(
        self,
        trade_engine: "TradeEngine",
        position_manager: "PositionManager",
        exchange: "BinanceFuturesAdapter",
        metrics_tracker: "MetricsTracker" = None,
        state_file: str = DEFAULT_STATE_FILE,
    ):
        """
        Инициализация State Manager.

        Args:
            trade_engine: TradeEngine с позициями
            position_manager: PositionManager для мониторинга
            exchange: Адаптер биржи
            metrics_tracker: MetricsTracker для сохранения статистики
            state_file: Путь к файлу состояния
        """
        self.trade_engine = trade_engine
        self.position_manager = position_manager
        self.exchange = exchange
        self.metrics_tracker = metrics_tracker
        self.state_file = state_file

        # Статистика синхронизации
        self._sync_stats = {
            "positions_restored": 0,
            "positions_from_exchange": 0,
            "closed_positions_restored": 0,  # Закрытые позиции для защиты от дублей
            "sl_orders_found": 0,
            "tp_orders_found": 0,
            "trailing_orders_found": 0,
            "sl_orders_created": 0,
            "tp_orders_created": 0,
            "positions_closed_expired": 0,
        }

    def save_state(self) -> bool:
        """
        Сохранить текущее состояние в файл.

        Сохраняет:
        - Все позиции (открытые и закрытые)
        - Статистику TradeEngine
        - Статистику PositionManager
        - Missing TP позиции

        Returns:
            True если успешно сохранено
        """
        try:
            state = {
                "saved_at": datetime.utcnow().isoformat(),
                "version": "1.1",

                # Позиции
                "positions": {},

                # Статистика
                "trade_engine_stats": self.trade_engine.get_stats(),
                "position_manager_stats": self.position_manager.get_stats(),

                # Missing TP (position_id -> registered_at timestamp)
                "missing_tp_positions": dict(self.position_manager._missing_tp_positions),

                # Metrics (PnL tracking, dashboard data)
                "metrics": self.metrics_tracker.to_dict() if self.metrics_tracker else None,
            }

            # Сериализуем позиции
            for pos_id, position in self.trade_engine.positions.items():
                state["positions"][pos_id] = self._serialize_position(position)

            # Сохраняем в файл
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)

            logger.info(
                f"State saved to {self.state_file}: "
                f"{len(state['positions'])} positions"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to save state: {e}")
            return False

    def load_state(self) -> Optional[Dict[str, Any]]:
        """
        Загрузить состояние из файла.

        Returns:
            Словарь с состоянием или None если файла нет
        """
        if not os.path.exists(self.state_file):
            logger.info(f"No state file found: {self.state_file}")
            return None

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                state = json.load(f)

            logger.info(
                f"State loaded from {self.state_file}: "
                f"saved at {state.get('saved_at')}, "
                f"{len(state.get('positions', {}))} positions"
            )
            return state

        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            return None

    async def restore_and_sync(self) -> Dict[str, int]:
        """
        Восстановить состояние и синхронизировать с биржей.

        Алгоритм:
        1. Загрузить сохранённое состояние (если есть)
        2. Получить реальные позиции с биржи
        3. Получить все открытые ордера с биржи
        4. Для каждой позиции на бирже:
           - Найти соответствие в сохранённом состоянии
           - Идентифицировать SL/TP ордера
           - Если SL нет - создать
           - Если TP нет - создать
           - Проверить max_hold_days - если истёк, закрыть
           - Зарегистрировать для мониторинга

        Returns:
            Статистика синхронизации
        """
        logger.info("=" * 60)
        logger.info("RESTORING STATE AND SYNCING WITH EXCHANGE")
        logger.info("=" * 60)

        # Reset stats
        self._sync_stats = {k: 0 for k in self._sync_stats}

        # 1. Загружаем сохранённое состояние
        saved_state = self.load_state()
        saved_positions: Dict[str, Dict] = {}
        if saved_state:
            saved_positions = saved_state.get("positions", {})
            self._sync_stats["positions_restored"] = len(saved_positions)

            # Восстанавливаем MetricsTracker
            saved_metrics = saved_state.get("metrics")
            if saved_metrics and self.metrics_tracker:
                from .metrics import MetricsTracker
                restored_metrics = MetricsTracker.from_dict(saved_metrics)
                # Копируем данные в существующий metrics_tracker
                self.metrics_tracker.trades = restored_metrics.trades
                self.metrics_tracker.total_stats = restored_metrics.total_stats
                self.metrics_tracker.strategy_stats = restored_metrics.strategy_stats
                self.metrics_tracker.symbol_stats = restored_metrics.symbol_stats
                self.metrics_tracker.exit_reason_stats = restored_metrics.exit_reason_stats
                self.metrics_tracker.direction_stats = restored_metrics.direction_stats
                self.metrics_tracker.equity_curve = restored_metrics.equity_curve
                self.metrics_tracker.daily_pnl = restored_metrics.daily_pnl
                self.metrics_tracker._peak_equity = restored_metrics._peak_equity
                self.metrics_tracker._current_equity = restored_metrics._current_equity
                self.metrics_tracker._max_drawdown = restored_metrics._max_drawdown
                self.metrics_tracker._max_drawdown_pct = restored_metrics._max_drawdown_pct
                self.metrics_tracker._start_time = restored_metrics._start_time
                logger.info(f"MetricsTracker restored: {len(self.metrics_tracker.trades)} trades")

        # 2. Получаем реальные позиции с биржи
        exchange_positions = await self.exchange.get_all_positions()
        logger.info(f"Found {len(exchange_positions)} positions on exchange")

        # 3. Получаем все открытые ордера с биржи (обычные + Algo)
        # ВАЖНО: SL через Algo API НЕ возвращаются через get_open_orders()!
        all_orders = await self.exchange.get_open_orders()
        all_algo_orders = await self.exchange.get_open_algo_orders()
        logger.info(f"Found {len(all_orders)} regular orders + {len(all_algo_orders)} Algo orders")

        # Группируем ордера по символу для быстрого поиска
        orders_by_symbol: Dict[str, List[Dict]] = {}
        for order in all_orders:
            symbol = order.get("symbol", "")
            if symbol not in orders_by_symbol:
                orders_by_symbol[symbol] = []
            orders_by_symbol[symbol].append(order)

        # Группируем Algo ордера
        algo_orders_by_symbol: Dict[str, List[Dict]] = {}
        for order in all_algo_orders:
            symbol = order.get("symbol", "")
            if symbol not in algo_orders_by_symbol:
                algo_orders_by_symbol[symbol] = []
            algo_orders_by_symbol[symbol].append(order)

        # 4. Обрабатываем каждую позицию с биржи
        for ex_pos in exchange_positions:
            await self._process_exchange_position(
                ex_pos, saved_positions, orders_by_symbol, algo_orders_by_symbol
            )

        # 4.5. КРИТИЧНО: Восстанавливаем ЗАКРЫТЫЕ позиции из saved_positions
        # Это нужно для защиты от повторного исполнения сигналов после перезапуска бота.
        # Если позиция закрылась по SL/TP, а потом бот перезапустился -
        # мы должны помнить что этот signal_id уже был исполнен!
        restored_position_ids = set(self.trade_engine.positions.keys())
        for pos_id, saved_data in saved_positions.items():
            # Пропускаем уже восстановленные (открытые на бирже)
            if pos_id in restored_position_ids:
                continue

            # Восстанавливаем ТОЛЬКО закрытые позиции
            if saved_data.get("status") == "CLOSED":
                position = self._deserialize_position(saved_data)
                if position:
                    self.trade_engine.positions[position.position_id] = position
                    self._sync_stats["closed_positions_restored"] += 1
                    logger.debug(
                        f"Restored CLOSED position: {position.symbol} "
                        f"signal_id={position.signal_id} (for deduplication)"
                    )

        if self._sync_stats["closed_positions_restored"] > 0:
            logger.info(
                f"Restored {self._sync_stats['closed_positions_restored']} CLOSED positions "
                f"for signal deduplication protection"
            )

        # 5. Удаляем файл состояния после успешной синхронизации
        # (чтобы не восстанавливать повторно)
        if os.path.exists(self.state_file):
            try:
                os.remove(self.state_file)
                logger.info(f"State file removed: {self.state_file}")
            except Exception as e:
                logger.warning(f"Failed to remove state file: {e}")

        logger.info("=" * 60)
        logger.info("SYNC COMPLETE")
        logger.info(f"Positions from exchange: {self._sync_stats['positions_from_exchange']}")
        logger.info(f"Closed positions restored: {self._sync_stats['closed_positions_restored']}")
        logger.info(f"SL orders found: {self._sync_stats['sl_orders_found']}")
        logger.info(f"TP orders found: {self._sync_stats['tp_orders_found']}")
        logger.info(f"Trailing orders found: {self._sync_stats['trailing_orders_found']}")
        logger.info(f"SL orders created: {self._sync_stats['sl_orders_created']}")
        logger.info(f"TP orders created: {self._sync_stats['tp_orders_created']}")
        logger.info(f"Positions closed (expired): {self._sync_stats['positions_closed_expired']}")
        logger.info("=" * 60)

        return self._sync_stats

    async def _process_exchange_position(
        self,
        ex_pos: Dict[str, Any],
        saved_positions: Dict[str, Dict],
        orders_by_symbol: Dict[str, List[Dict]],
        algo_orders_by_symbol: Dict[str, List[Dict]] = None,
    ) -> None:
        """
        Обработать позицию с биржи.

        Args:
            ex_pos: Позиция с биржи (от get_all_positions)
            saved_positions: Сохранённые позиции
            orders_by_symbol: Обычные ордера (LIMIT, MARKET) по символу
            algo_orders_by_symbol: Algo ордера (SL STOP_MARKET, Trailing) по символу
        """
        if algo_orders_by_symbol is None:
            algo_orders_by_symbol = {}
        symbol = ex_pos.get("symbol", "")
        position_amt = Decimal(str(ex_pos.get("positionAmt", "0")))
        entry_price = float(ex_pos.get("entryPrice", "0"))
        position_side_str = ex_pos.get("positionSide", "BOTH")

        # Пропускаем пустые позиции
        if position_amt == 0:
            return

        self._sync_stats["positions_from_exchange"] += 1

        logger.info(f"Processing position: {symbol} {position_side_str} qty={position_amt}")

        # Определяем направление
        if position_side_str == "LONG" or (position_side_str == "BOTH" and position_amt > 0):
            position_side = PositionSide.LONG
            is_long = True
        else:
            position_side = PositionSide.SHORT
            is_long = False

        quantity = abs(float(position_amt))

        # Ищем соответствие в сохранённых позициях
        matched_saved = self._find_matching_saved_position(
            symbol, position_side, quantity, entry_price, saved_positions
        )

        # Получаем ордера для этого символа
        symbol_orders = orders_by_symbol.get(symbol, [])
        symbol_algo_orders = algo_orders_by_symbol.get(symbol, [])

        # Ищем SL, TP и Trailing Stop ордера
        # SL: проверяем Algo ордера (STOP_MARKET через Algo API)
        sl_order = self._find_sl_algo_order(symbol_algo_orders, position_side, quantity)
        # TP: проверяем обычные ордера (LIMIT)
        tp_order = self._find_tp_limit_order(symbol_orders, position_side, quantity)
        # Trailing Stop: проверяем Algo ордера (TRAILING_STOP_MARKET через Algo API)
        trailing_order = self._find_trailing_stop_order(symbol_algo_orders, position_side, quantity)

        if sl_order:
            self._sync_stats["sl_orders_found"] += 1
            logger.debug(f"Found SL Algo order: algoId={sl_order.get('algoId')}")
        if tp_order:
            self._sync_stats["tp_orders_found"] += 1
            logger.debug(f"Found TP LIMIT order: orderId={tp_order.get('orderId')}")
        if trailing_order:
            self._sync_stats["trailing_orders_found"] += 1
            logger.debug(f"Found Trailing Stop order: algoId={trailing_order.get('algoId')} callbackRate={trailing_order.get('callbackRate')}")

        # Определяем параметры позиции
        if matched_saved:
            # Берём данные из сохранённой позиции
            position_id = matched_saved.get("position_id", f"RESTORED_{symbol}_{position_side.value}")
            signal_id = matched_saved.get("signal_id", "")
            strategy = matched_saved.get("strategy", "")
            regime_action = matched_saved.get("regime_action", "FULL")
            max_hold_days = matched_saved.get("max_hold_days", self.trade_engine.max_hold_days)
            opened_at_str = matched_saved.get("opened_at")
            opened_at = datetime.fromisoformat(opened_at_str) if opened_at_str else datetime.utcnow()
            sl_price = matched_saved.get("stop_loss", 0)
            tp_price = matched_saved.get("take_profit", 0)
        else:
            # Новая позиция - создаём с дефолтами
            import uuid
            position_id = f"SYNCED_{symbol}_{uuid.uuid4().hex[:8]}"
            signal_id = ""
            strategy = "SYNCED"
            regime_action = "FULL"
            max_hold_days = self.trade_engine.max_hold_days
            opened_at = datetime.utcnow()  # Не знаем когда открылась
            sl_price = 0
            tp_price = 0

        # === ВОССТАНОВЛЕНИЕ signal_id и strategy из clientAlgoId ===
        # Если signal_id пустой, пробуем извлечь из clientAlgoId ордеров
        # Формат: SL_20260308_ETHUSDT_SHORT_ls_fade или TS_20260308_ETHUSDT_SHORT_ls_fade
        if not signal_id or strategy == "SYNCED":
            recovered = self._recover_signal_from_orders(sl_order, trailing_order)
            if recovered:
                recovered_signal_id, recovered_strategy = recovered
                if not signal_id:
                    signal_id = recovered_signal_id
                    # Обновляем position_id чтобы отразить восстановление
                    position_id = f"RECOVERED_{symbol}_{uuid.uuid4().hex[:8]}"
                    logger.info(f"Recovered signal_id from clientAlgoId: {signal_id}")
                if strategy == "SYNCED" and recovered_strategy:
                    strategy = recovered_strategy
                    logger.info(f"Recovered strategy from clientAlgoId: {strategy}")

        # Берём SL/TP/Trailing из ордеров если есть
        sl_order_id = ""
        tp_order_id = ""
        trailing_stop_order_id = ""
        trailing_stop_callback_rate = 0.0
        trailing_stop_activation_price = 0.0

        if sl_order:
            # Algo ордер использует algoId и triggerPrice
            sl_order_id = str(sl_order.get("algoId", ""))
            sl_price = float(sl_order.get("triggerPrice", sl_price))

        if tp_order:
            # LIMIT ордер использует orderId и price
            tp_order_id = str(tp_order.get("orderId", ""))
            tp_price = float(tp_order.get("price", tp_price))

        if trailing_order:
            # Trailing Stop Algo ордер: algoId, callbackRate, activatePrice
            trailing_stop_order_id = str(trailing_order.get("algoId", ""))
            trailing_stop_callback_rate = float(trailing_order.get("callbackRate", 0) or 0)
            activate_price = trailing_order.get("activatePrice")
            trailing_stop_activation_price = float(activate_price) if activate_price else 0.0

        # Проверяем max_hold_days - если истёк, закрываем
        from datetime import timedelta
        if (datetime.utcnow() - opened_at) >= timedelta(days=max_hold_days):
            logger.warning(
                f"Position {position_id} EXPIRED: held > {max_hold_days} days - CLOSING"
            )
            await self._close_expired_position(
                symbol, position_side, Decimal(str(quantity)),
                sl_order_id, tp_order_id
            )
            self._sync_stats["positions_closed_expired"] += 1
            return

        # Создаём недостающие SL/TP
        exit_side = OrderSide.SELL if is_long else OrderSide.BUY

        if not sl_order_id and sl_price > 0:
            # Нужно создать SL через Algo Order API
            try:
                sl_result = await self.exchange.place_stop_order(
                    symbol=symbol,
                    side=exit_side,
                    quantity=Decimal(str(quantity)),
                    stop_price=Decimal(str(sl_price)),
                    position_side=position_side,
                    reduce_only=True,
                )
                # Algo API возвращает algoId, не orderId
                sl_order_id = str(sl_result.get("algoId", "")) if sl_result else ""
                self._sync_stats["sl_orders_created"] += 1
                logger.info(f"Created SL Algo order algoId={sl_order_id} for {position_id}")
            except Exception as e:
                logger.error(f"Failed to create SL order: {e}")

        if not tp_order_id and tp_price > 0:
            # Нужно создать TP
            try:
                tp_result = await self.exchange.place_take_profit_order(
                    symbol=symbol,
                    side=exit_side,
                    quantity=Decimal(str(quantity)),
                    stop_price=Decimal(str(tp_price)),
                    position_side=position_side,
                    reduce_only=True,
                )
                tp_order_id = str(tp_result.get("orderId", "")) if tp_result else ""
                self._sync_stats["tp_orders_created"] += 1
                logger.info(f"Created TP order {tp_order_id} for {position_id}")
            except Exception as e:
                logger.error(f"Failed to create TP order: {e}")

        # Создаём Position объект
        position = Position(
            position_id=position_id,
            signal_id=signal_id,
            symbol=symbol,
            side=position_side,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=sl_price,
            take_profit=tp_price,
            status=PositionStatus.OPEN,
            entry_order_id="",  # Не знаем
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
            trailing_stop_order_id=trailing_stop_order_id,
            trailing_stop_enabled=bool(trailing_stop_order_id),
            trailing_stop_callback_rate=trailing_stop_callback_rate,
            trailing_stop_activation_price=trailing_stop_activation_price,
            opened_at=opened_at,
            strategy=strategy,
            regime_action=regime_action,
            max_hold_days=max_hold_days,
        )

        # Регистрируем в TradeEngine
        self.trade_engine.positions[position_id] = position

        # Регистрируем в PositionManager для мониторинга
        self.position_manager.register_position(position)

        # Если нет ни TP ни Trailing Stop - регистрируем для missing TP мониторинга
        # ВАЖНО: trailing stop ЗАМЕНЯЕТ TP, поэтому если есть trailing - это НЕ missing TP!
        has_exit_order = bool(tp_order_id) or bool(trailing_stop_order_id)
        if not has_exit_order:
            self.position_manager.register_missing_tp(position)

        logger.info(
            f"Position {position_id} registered: {symbol} {position_side.value} "
            f"qty={quantity} entry={entry_price} SL={sl_order_id or 'NONE'} TP={tp_order_id or 'NONE'}"
        )

    def _find_matching_saved_position(
        self,
        symbol: str,
        position_side: PositionSide,
        quantity: float,
        entry_price: float,
        saved_positions: Dict[str, Dict],
    ) -> Optional[Dict]:
        """
        Найти соответствие в сохранённых позициях.

        Критерии:
        - Тот же символ
        - То же направление
        - Примерно та же entry price (±1%)
        """
        for pos_id, saved in saved_positions.items():
            if saved.get("symbol") != symbol:
                continue
            if saved.get("side") != position_side.value:
                continue
            if saved.get("status") != "OPEN":
                continue

            saved_entry = saved.get("entry_price", 0)
            if saved_entry > 0 and entry_price > 0:
                diff_pct = abs(saved_entry - entry_price) / entry_price * 100
                if diff_pct <= 1.0:
                    logger.info(f"Matched saved position {pos_id} for {symbol}")
                    return saved

        return None

    def _find_sl_order(
        self,
        orders: List[Dict],
        position_side: PositionSide,
        quantity: float,
    ) -> Optional[Dict]:
        """
        Найти SL ордер для позиции.

        Критерии:
        - type = STOP_MARKET
        - reduceOnly = true
        - Противоположная сторона (если LONG позиция, то SELL SL)
        - positionSide совпадает
        """
        expected_side = "SELL" if position_side == PositionSide.LONG else "BUY"

        for order in orders:
            if order.get("type") != "STOP_MARKET":
                continue
            if order.get("side") != expected_side:
                continue
            if order.get("positionSide") != position_side.value:
                continue
            if not order.get("reduceOnly", False):
                continue

            return order

        return None

    def _find_tp_order(
        self,
        orders: List[Dict],
        position_side: PositionSide,
        quantity: float,
    ) -> Optional[Dict]:
        """
        Найти TP ордер для позиции.

        Критерии:
        - type = TAKE_PROFIT_MARKET
        - reduceOnly = true
        - Противоположная сторона
        - positionSide совпадает
        """
        expected_side = "SELL" if position_side == PositionSide.LONG else "BUY"

        for order in orders:
            if order.get("type") != "TAKE_PROFIT_MARKET":
                continue
            if order.get("side") != expected_side:
                continue
            if order.get("positionSide") != position_side.value:
                continue
            if not order.get("reduceOnly", False):
                continue

            return order

        return None

    def _find_sl_algo_order(
        self,
        algo_orders: List[Dict],
        position_side: PositionSide,
        quantity: float,
    ) -> Optional[Dict]:
        """
        Найти SL Algo ордер для позиции.

        ВАЖНО: SL размещаются через Algo Order API (/fapi/v1/algoOrder).
        Поля отличаются от обычных ордеров:
        - orderType (не type)
        - algoId (не orderId)
        - triggerPrice (не stopPrice)

        Критерии:
        - orderType = STOP_MARKET
        - Противоположная сторона (если LONG позиция, то SELL SL)
        - positionSide совпадает
        - algoStatus = NEW (активный)
        """
        expected_side = "SELL" if position_side == PositionSide.LONG else "BUY"

        for order in algo_orders:
            order_type = order.get("orderType", "")
            if order_type != "STOP_MARKET":
                continue
            if order.get("side") != expected_side:
                continue
            if order.get("positionSide") != position_side.value:
                continue
            # Проверяем что ордер активен
            if order.get("algoStatus") not in ("NEW", "PARTIALLY_FILLED"):
                continue

            logger.debug(f"Found SL Algo: algoId={order.get('algoId')} triggerPrice={order.get('triggerPrice')}")
            return order

        return None

    def _find_trailing_stop_order(
        self,
        algo_orders: List[Dict],
        position_side: PositionSide,
        quantity: float,
    ) -> Optional[Dict]:
        """
        Найти Trailing Stop Algo ордер для позиции.

        ВАЖНО: Trailing Stop размещаются через Algo Order API (/fapi/v1/algoOrder).
        Официальная документация Binance API:
        https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/Current-All-Algo-Open-Orders

        Поля ответа:
        - orderType: TRAILING_STOP_MARKET
        - algoId: уникальный ID
        - clientAlgoId: наш ID (TS_{signal_id})
        - callbackRate: процент отката (например 3.0 = 3%)
        - activatePrice: цена активации (может быть пустой)
        - algoStatus: NEW/PARTIALLY_FILLED/etc

        Критерии поиска:
        - orderType = TRAILING_STOP_MARKET
        - Противоположная сторона (если LONG позиция, то SELL trailing)
        - positionSide совпадает
        - algoStatus = NEW (активный)
        """
        expected_side = "SELL" if position_side == PositionSide.LONG else "BUY"

        for order in algo_orders:
            order_type = order.get("orderType", "")
            if order_type != "TRAILING_STOP_MARKET":
                continue
            if order.get("side") != expected_side:
                continue
            if order.get("positionSide") != position_side.value:
                continue
            # Проверяем что ордер активен
            if order.get("algoStatus") not in ("NEW", "PARTIALLY_FILLED"):
                continue

            logger.debug(
                f"Found Trailing Stop: algoId={order.get('algoId')} "
                f"callbackRate={order.get('callbackRate')} "
                f"activatePrice={order.get('activatePrice')}"
            )
            return order

        return None

    def _recover_signal_from_orders(
        self,
        sl_order: Optional[Dict],
        trailing_order: Optional[Dict],
    ) -> Optional[tuple]:
        """
        Восстановить signal_id и strategy из clientAlgoId ордеров.

        Когда state файл утерян, но ордера на бирже остались,
        мы можем восстановить информацию из clientAlgoId:
        - SL: clientAlgoId = "SL_20260308_ETHUSDT_SHORT_ls_fade"
        - Trailing: clientAlgoId = "TS_20260308_ETHUSDT_SHORT_ls_fade"

        Формат signal_id: {date}_{symbol}_{direction}_{strategy}

        Returns:
            Tuple (signal_id, strategy) или None если не удалось восстановить
        """
        # Пробуем сначала Trailing Stop, потом SL
        for order, prefix in [(trailing_order, "TS_"), (sl_order, "SL_")]:
            if not order:
                continue

            client_algo_id = order.get("clientAlgoId", "")
            if not client_algo_id:
                continue

            # Проверяем что это наш ордер (начинается с SL_ или TS_)
            if not client_algo_id.startswith(prefix):
                continue

            # Извлекаем signal_id: убираем префикс
            signal_id = client_algo_id[len(prefix):]

            # Извлекаем strategy: последняя часть после последнего _
            # Формат: 20260308_ETHUSDT_SHORT_ls_fade
            parts = signal_id.split("_")
            if len(parts) >= 4:
                # strategy = все части после direction (SHORT/LONG)
                # Находим индекс LONG или SHORT
                direction_idx = None
                for i, part in enumerate(parts):
                    if part in ("LONG", "SHORT"):
                        direction_idx = i
                        break

                if direction_idx is not None and direction_idx + 1 < len(parts):
                    strategy = "_".join(parts[direction_idx + 1:])
                    logger.info(f"Recovered from clientAlgoId: signal_id={signal_id}, strategy={strategy}")
                    return (signal_id, strategy)

        return None

    def _find_tp_limit_order(
        self,
        orders: List[Dict],
        position_side: PositionSide,
        quantity: float,
    ) -> Optional[Dict]:
        """
        Найти TP LIMIT ордер для позиции.

        ВАЖНО: TP размещаются как LIMIT ордера (не TAKE_PROFIT_MARKET).
        В Hedge Mode reduceOnly НЕ используется - направление определяет цель.

        Критерии:
        - type = LIMIT
        - Противоположная сторона
        - positionSide совпадает
        - timeInForce = GTC
        """
        expected_side = "SELL" if position_side == PositionSide.LONG else "BUY"

        for order in orders:
            if order.get("type") != "LIMIT":
                continue
            if order.get("side") != expected_side:
                continue
            if order.get("positionSide") != position_side.value:
                continue
            # GTC = Good Till Cancelled - типичный для TP
            if order.get("timeInForce") != "GTC":
                continue

            logger.debug(f"Found TP LIMIT: orderId={order.get('orderId')} price={order.get('price')}")
            return order

        return None

    async def _close_expired_position(
        self,
        symbol: str,
        position_side: PositionSide,
        quantity: Decimal,
        sl_order_id: str,
        tp_order_id: str,
    ) -> None:
        """
        Закрыть просроченную позицию (max_hold_days превышен).
        """
        # Отменяем SL (Algo Order) если есть
        if sl_order_id:
            try:
                # SL - это Algo Order, нужно cancel_algo_order
                await self.exchange.cancel_algo_order(symbol, algo_id=int(sl_order_id))
            except Exception as e:
                logger.warning(f"Failed to cancel SL algo order {sl_order_id}: {e}")

        # Отменяем TP (LIMIT Order) если есть
        if tp_order_id:
            try:
                await self.exchange.cancel_order(symbol, tp_order_id)
            except Exception as e:
                logger.warning(f"Failed to cancel TP order {tp_order_id}: {e}")

        # Закрываем по MARKET
        exit_side = OrderSide.SELL if position_side == PositionSide.LONG else OrderSide.BUY

        try:
            await self.exchange.place_market_order(
                symbol=symbol,
                side=exit_side,
                quantity=quantity,
                position_side=position_side,
                reduce_only=True,
            )
            logger.info(f"Closed expired position: {symbol} {position_side.value}")
        except Exception as e:
            logger.error(f"Failed to close expired position: {e}")

    def _serialize_position(self, position: Position) -> Dict[str, Any]:
        """Сериализовать позицию в dict для JSON."""
        return {
            "position_id": position.position_id,
            "signal_id": position.signal_id,
            "symbol": position.symbol,
            "side": position.side.value,
            "quantity": position.quantity,
            "entry_price": position.entry_price,
            "stop_loss": position.stop_loss,
            "take_profit": position.take_profit,
            "status": position.status.value,
            "entry_order_id": position.entry_order_id,
            "sl_order_id": position.sl_order_id,
            "tp_order_id": position.tp_order_id,
            "trailing_stop_order_id": position.trailing_stop_order_id,
            "trailing_stop_enabled": position.trailing_stop_enabled,
            "trailing_stop_callback_rate": position.trailing_stop_callback_rate,
            "trailing_stop_activation_price": position.trailing_stop_activation_price,
            "realized_pnl": position.realized_pnl,
            "exit_price": position.exit_price,
            "exit_reason": position.exit_reason,
            "opened_at": position.opened_at.isoformat() if position.opened_at else None,
            "closed_at": position.closed_at.isoformat() if position.closed_at else None,
            "strategy": position.strategy,
            "regime_action": position.regime_action,
            "max_hold_days": position.max_hold_days,
            "requested_quantity": position.requested_quantity,
            "is_partial_fill": position.is_partial_fill,
        }

    def _deserialize_position(self, data: Dict[str, Any]) -> Optional[Position]:
        """
        Десериализовать позицию из словаря.

        КРИТИЧНО: Используется для восстановления ЗАКРЫТЫХ позиций
        для защиты от повторного исполнения сигналов.

        Args:
            data: Словарь с данными позиции

        Returns:
            Position или None если десериализация не удалась
        """
        try:
            # Парсим даты
            opened_at = None
            if data.get("opened_at"):
                opened_at = datetime.fromisoformat(data["opened_at"])

            closed_at = None
            if data.get("closed_at"):
                closed_at = datetime.fromisoformat(data["closed_at"])

            # Парсим enum'ы
            side = PositionSide(data.get("side", "LONG"))
            status = PositionStatus(data.get("status", "CLOSED"))

            position = Position(
                position_id=data.get("position_id", ""),
                signal_id=data.get("signal_id", ""),
                symbol=data.get("symbol", ""),
                side=side,
                quantity=data.get("quantity", 0.0),
                entry_price=data.get("entry_price", 0.0),
                stop_loss=data.get("stop_loss", 0.0),
                take_profit=data.get("take_profit", 0.0),
                status=status,
                entry_order_id=data.get("entry_order_id", ""),
                sl_order_id=data.get("sl_order_id", ""),
                tp_order_id=data.get("tp_order_id", ""),
                trailing_stop_order_id=data.get("trailing_stop_order_id", ""),
                trailing_stop_enabled=data.get("trailing_stop_enabled", False),
                trailing_stop_callback_rate=data.get("trailing_stop_callback_rate", 0.0),
                trailing_stop_activation_price=data.get("trailing_stop_activation_price", 0.0),
                realized_pnl=data.get("realized_pnl", 0.0),
                exit_price=data.get("exit_price", 0.0),
                exit_reason=data.get("exit_reason", ""),
                opened_at=opened_at,
                closed_at=closed_at,
                strategy=data.get("strategy", ""),
                regime_action=data.get("regime_action", "FULL"),
                max_hold_days=data.get("max_hold_days", 14),
                requested_quantity=data.get("requested_quantity", 0.0),
                is_partial_fill=data.get("is_partial_fill", False),
            )
            return position

        except Exception as e:
            logger.warning(f"Failed to deserialize position: {e}")
            return None

    def get_sync_stats(self) -> Dict[str, int]:
        """Получить статистику последней синхронизации."""
        return self._sync_stats.copy()
