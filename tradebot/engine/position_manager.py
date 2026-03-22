# -*- coding: utf-8 -*-
"""
Position Manager - Мониторинг открытых позиций через WebSocket.

Слушает User Data Stream от Binance и обновляет статус позиций
когда SL/TP ордера исполняются.

События:
- ORDER_TRADE_UPDATE: ордер исполнен/отменён
- ACCOUNT_UPDATE: позиция изменилась
"""

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Dict, Any, Callable, TYPE_CHECKING

from ..core.models import Position, PositionStatus, PositionSide

if TYPE_CHECKING:
    from .trade_engine import TradeEngine
    from ..adapters.binance import BinanceFuturesAdapter

logger = logging.getLogger(__name__)

# Callback для уведомлений о закрытии позиции
PositionClosedCallback = Callable[[Position, str, float], None]

# Callback для warnings (missing TP, etc.)
# Args: (level: str, message: str, position: Position, details: Dict)
WarningCallback = Callable[[str, str, Position, Dict[str, Any]], None]


def _safe_int_order_id(order_id: str) -> Optional[int]:
    """
    Безопасно преобразовать order_id/algo_id в int.

    FIX: Защита от ValueError при нечисловых или пустых значениях.

    Args:
        order_id: Строковое представление ID ордера

    Returns:
        int если успешно, None если невозможно преобразовать
    """
    if not order_id or not order_id.strip():
        return None
    try:
        return int(order_id)
    except (ValueError, TypeError):
        logger.warning(f"Invalid order_id format: '{order_id}' (expected numeric string)")
        return None


class PositionManager:
    """
    Менеджер позиций - отслеживает SL/TP через WebSocket.

    Использование:
        manager = PositionManager(exchange, trade_engine)
        manager.on_position_closed = my_callback  # Опционально
        await manager.start()
        ...
        await manager.stop()
    """

    def __init__(
        self,
        exchange: "BinanceFuturesAdapter",
        trade_engine: "TradeEngine",
    ):
        """
        Инициализация Position Manager.

        Args:
            exchange: Адаптер биржи с поддержкой User Data Stream
            trade_engine: Trade Engine с позициями для мониторинга
        """
        self.exchange = exchange
        self.trade_engine = trade_engine

        # Callback для уведомления о закрытии позиции
        self.on_position_closed: Optional[PositionClosedCallback] = None

        # Callback для warnings (missing TP, etc.)
        self.on_warning: Optional[WarningCallback] = None

        # Маппинг order_id -> position_id для быстрого поиска
        self._order_to_position: Dict[str, str] = {}

        # Позиции без TP (для мониторинга)
        # position_id -> timestamp когда зарегистрировали
        self._missing_tp_positions: Dict[str, float] = {}

        # Статистика
        self._stats = {
            "order_updates_received": 0,
            "account_updates_received": 0,
            "positions_closed_sl": 0,
            "positions_closed_tp": 0,
            "positions_closed_timeout": 0,
            "positions_closed_missing_tp": 0,
            "positions_closed_manual": 0,
            "missing_tp_alerts_sent": 0,
            # REST sync статистика
            "rest_sync_runs": 0,
            "rest_sync_positions_fixed": 0,
            "rest_sync_orders_fixed": 0,
            # Orphan cleanup статистика
            "orphans_cleaned": 0,
        }

        self._running = False
        self._timeout_check_task: Optional[asyncio.Task] = None
        self._timeout_check_interval: int = 3600  # 1 час
        self._missing_tp_check_task: Optional[asyncio.Task] = None
        self._missing_tp_check_interval: int = 600  # 10 минут
        self._missing_tp_max_wait: int = 3600  # 1 час - максимум ждём TP

        # REST синхронизация (защита от пропущенных WS событий)
        self._rest_sync_task: Optional[asyncio.Task] = None
        self._rest_sync_interval: int = 600  # 10 минут

        # Очередь неудачных отмен ордеров для повторной попытки
        # List of (symbol, order_id, is_algo, retry_count)
        self._pending_cancels: list = []
        self._cancel_retry_max: int = 5
        self._cancel_retry_task: Optional[asyncio.Task] = None
        self._cancel_retry_interval: int = 60  # 1 минута

        # Orphan orders cleanup (TradeAI1 style)
        # symbol -> timestamp when position was closed
        self._recently_closed_symbols: Dict[str, datetime] = {}
        self._orphan_grace_period: int = 60  # seconds

    def _create_task_with_handler(self, coro, name: str = "") -> asyncio.Task:
        """
        Create background task with exception handling.

        FIX: asyncio.create_task exceptions are now logged instead of lost.
        """
        task = asyncio.create_task(coro, name=name)
        task.add_done_callback(self._handle_task_exception)
        return task

    def _handle_task_exception(self, task: asyncio.Task) -> None:
        """Handle exceptions from background tasks."""
        try:
            exc = task.exception()
            if exc:
                logger.error(
                    f"Background task '{task.get_name()}' failed with exception: {exc}"
                )
        except asyncio.CancelledError:
            pass  # Task was cancelled, not an error

    async def start(self) -> bool:
        """
        Запустить мониторинг позиций.

        Returns:
            True если успешно запущен
        """
        if self._running:
            logger.warning("Position Manager already running")
            return True

        # Строим маппинг существующих позиций
        self._rebuild_order_mapping()

        # Запускаем User Data Stream
        success = await self.exchange.start_user_data_stream(
            on_order_update=self._handle_order_update,
            on_account_update=self._handle_account_update,
        )

        if success:
            self._running = True

            # FIX: Используем helper с exception handling
            # Запускаем проверку max_hold_days (таймаут позиций)
            self._timeout_check_task = self._create_task_with_handler(
                self._timeout_check_loop(), name="timeout_check"
            )

            # Запускаем проверку позиций без TP
            self._missing_tp_check_task = self._create_task_with_handler(
                self._missing_tp_check_loop(), name="missing_tp_check"
            )

            # Запускаем периодическую REST синхронизацию
            self._rest_sync_task = self._create_task_with_handler(
                self._rest_sync_loop(), name="rest_sync"
            )

            # Запускаем обработку очереди неудачных отмен
            self._cancel_retry_task = self._create_task_with_handler(
                self._cancel_retry_loop(), name="cancel_retry"
            )

            logger.info(
                f"Position Manager started (timeout + missing TP + REST sync every "
                f"{self._rest_sync_interval // 60} min)"
            )

        return success

    async def stop(self) -> None:
        """Остановить мониторинг позиций."""
        self._running = False

        # Останавливаем проверку таймаутов
        if self._timeout_check_task and not self._timeout_check_task.done():
            self._timeout_check_task.cancel()
            try:
                await self._timeout_check_task
            except asyncio.CancelledError:
                pass

        # Останавливаем проверку missing TP
        if self._missing_tp_check_task and not self._missing_tp_check_task.done():
            self._missing_tp_check_task.cancel()
            try:
                await self._missing_tp_check_task
            except asyncio.CancelledError:
                pass

        # Останавливаем REST синхронизацию
        if self._rest_sync_task and not self._rest_sync_task.done():
            self._rest_sync_task.cancel()
            try:
                await self._rest_sync_task
            except asyncio.CancelledError:
                pass

        # Останавливаем обработку неудачных отмен
        if self._cancel_retry_task and not self._cancel_retry_task.done():
            self._cancel_retry_task.cancel()
            try:
                await self._cancel_retry_task
            except asyncio.CancelledError:
                pass

        await self.exchange.stop_user_data_stream()

        logger.info(f"Position Manager stopped. Stats: {self._stats}")

    def _rebuild_order_mapping(self) -> None:
        """Перестроить маппинг order_id -> position_id."""
        self._order_to_position.clear()

        for pos_id, position in self.trade_engine.positions.items():
            if position.is_open:
                if position.sl_order_id:
                    self._order_to_position[position.sl_order_id] = pos_id
                if position.tp_order_id:
                    self._order_to_position[position.tp_order_id] = pos_id
                if position.trailing_stop_order_id:
                    self._order_to_position[position.trailing_stop_order_id] = pos_id

        logger.debug(f"Order mapping rebuilt: {len(self._order_to_position)} orders")

    def register_position(self, position: Position) -> None:
        """
        Зарегистрировать новую позицию для мониторинга.

        Вызывается из TradeEngine после открытия позиции.
        """
        if position.sl_order_id:
            self._order_to_position[position.sl_order_id] = position.position_id
        if position.tp_order_id:
            self._order_to_position[position.tp_order_id] = position.position_id
        if position.trailing_stop_order_id:
            self._order_to_position[position.trailing_stop_order_id] = position.position_id

        logger.debug(f"Registered position {position.position_id} for monitoring")

    def unregister_position(self, position: Position) -> None:
        """
        Удалить позицию из мониторинга.

        Вызывается после закрытия позиции.
        """
        if position.sl_order_id and position.sl_order_id in self._order_to_position:
            del self._order_to_position[position.sl_order_id]
        if position.tp_order_id and position.tp_order_id in self._order_to_position:
            del self._order_to_position[position.tp_order_id]
        if position.trailing_stop_order_id and position.trailing_stop_order_id in self._order_to_position:
            del self._order_to_position[position.trailing_stop_order_id]

        logger.debug(f"Unregistered position {position.position_id}")

        # Удаляем из missing TP если была там
        if position.position_id in self._missing_tp_positions:
            del self._missing_tp_positions[position.position_id]

        # Set grace period for orphan cleanup (TradeAI1 style)
        # Предотвращает race condition при отмене ордеров сразу после закрытия позиции
        self._recently_closed_symbols[position.symbol] = datetime.now(timezone.utc)

    async def _clean_orphan_orders(
        self,
        exchange_positions: list,
        exchange_orders: list,
        exchange_algo_orders: list,
    ) -> None:
        """
        Чистка осиротевших ордеров (TradeAI1 style).

        Логика: нет живой позиции по symbol+positionSide → ордер осиротел → cancel.
        Без AI — чистая программная логика.

        ВАЖНО: Grace period 60 секунд для недавно закрытых позиций.
        Это предотвращает race condition когда SL сработал, но биржа
        ещё не успела отменить TP/Trail ордера автоматически.

        Args:
            exchange_positions: Позиции с биржи (уже загружены в _perform_rest_sync)
            exchange_orders: Обычные ордера с биржи
            exchange_algo_orders: Algo ордера с биржи (SL, Trailing)
        """
        # 1. Очистка старых записей из grace period (> 60 секунд)
        # ВАЖНО: Делаем ДО early return, чтобы память чистилась всегда
        now = datetime.now(timezone.utc)
        expired_symbols = [
            sym for sym, closed_time in self._recently_closed_symbols.items()
            if (now - closed_time).total_seconds() > self._orphan_grace_period
        ]
        for sym in expired_symbols:
            del self._recently_closed_symbols[sym]

        # Early return если нет ордеров для проверки
        if not exchange_orders and not exchange_algo_orders:
            return

        # 2. Множество живых позиций: {("SOLUSDT", "LONG"), ("DOGEUSDT", "SHORT")}
        live_positions = set()
        for pos in exchange_positions:
            symbol = pos.get("symbol", "")
            position_side = pos.get("positionSide", "BOTH")
            live_positions.add((symbol, position_side))

        orphan_count = 0
        skipped_grace = 0

        # 3. Проверяем обычные ордера (TP = LIMIT)
        for order in exchange_orders:
            symbol = order.get("symbol", "")
            raw_order_id = order.get("orderId")

            # Skip invalid orderId (None, empty, etc.)
            if raw_order_id is None or raw_order_id == "":
                continue
            order_id = str(raw_order_id)
            if not order_id or order_id == "None":
                continue

            # Grace period: не трогаем ордера недавно закрытых позиций
            if symbol in self._recently_closed_symbols:
                skipped_grace += 1
                continue

            # Определяем positionSide ордера
            pos_side = order.get("positionSide", "BOTH")
            if pos_side == "BOTH":
                # Hedge mode fallback: определяем сторону из side ордера
                # SL/TP для LONG = SELL, SL/TP для SHORT = BUY
                order_side = order.get("side", "")
                if order_side == "SELL":
                    pos_side = "LONG"
                else:
                    pos_side = "SHORT"

            # Есть ли живая позиция для этого ордера?
            if (symbol, pos_side) not in live_positions:
                # Осиротевший ордер — отменяем
                try:
                    success = await self.exchange.cancel_order(symbol, order_id)
                    if success:
                        orphan_count += 1
                        order_type = order.get("type", "UNKNOWN")
                        logger.info(
                            f"🧹 Orphan cancelled: {symbol} {order_type} "
                            f"order_id={order_id}"
                        )
                except Exception as e:
                    logger.warning(f"Failed to cancel orphan order {order_id}: {e}")

        # 4. Проверяем Algo ордера (SL, Trailing Stop)
        for order in exchange_algo_orders:
            symbol = order.get("symbol", "")
            algo_id = order.get("algoId")

            # Grace period check
            if symbol in self._recently_closed_symbols:
                skipped_grace += 1
                continue

            # Определяем positionSide ордера
            pos_side = order.get("positionSide", "BOTH")
            if pos_side == "BOTH":
                order_side = order.get("side", "")
                if order_side == "SELL":
                    pos_side = "LONG"
                else:
                    pos_side = "SHORT"

            # Есть ли живая позиция для этого ордера?
            if (symbol, pos_side) not in live_positions:
                # Осиротевший Algo ордер — отменяем
                try:
                    # algo_id уже int из API, но проверяем
                    if algo_id is not None:
                        success = await self.exchange.cancel_algo_order(
                            symbol, algo_id=int(algo_id)
                        )
                        if success:
                            orphan_count += 1
                            order_type = order.get("orderType", "ALGO")
                            logger.info(
                                f"🧹 Orphan Algo cancelled: {symbol} {order_type} "
                                f"algo_id={algo_id}"
                            )
                except Exception as e:
                    logger.warning(f"Failed to cancel orphan Algo {algo_id}: {e}")

        # 5. Статистика
        if orphan_count > 0:
            logger.info(f"🧹 Cleaned {orphan_count} orphaned order(s)")
            self._stats["orphans_cleaned"] += orphan_count
        if skipped_grace > 0:
            logger.debug(f"🛡️ Skipped {skipped_grace} order(s) in grace period")

    def register_missing_tp(self, position: Position) -> None:
        """
        Зарегистрировать позицию без TP ордера для мониторинга.

        Будет проверяться каждые 10 минут:
        - Если TP появился (вручную поставлен) - убираем из мониторинга
        - Если через 1 час TP так и нет - закрываем позицию по MARKET

        Args:
            position: Позиция без TP
        """
        import time as time_module
        self._missing_tp_positions[position.position_id] = time_module.time()
        logger.warning(
            f"Position {position.position_id} registered for missing TP monitoring "
            f"(will close in {self._missing_tp_max_wait // 60} min if TP not set)"
        )

    def _handle_order_update(self, order_info: Dict[str, Any]) -> None:
        """
        Обработать ORDER_TRADE_UPDATE или ALGO_UPDATE событие.

        Формат order_info (плоский dict от binance.py):
        {
            "orderId": "123456789",    # orderId или algoId
            "algoId": 123456789,       # только для ALGO_UPDATE
            "symbol": "BTCUSDT",
            "status": "FILLED",        # NEW, PARTIALLY_FILLED, FILLED, CANCELED, EXPIRED
            "type": "STOP_MARKET",     # Order type
            "avgPrice": "50000.00",    # Average price (filled)
            "executedQty": "0.001",    # Executed quantity
            "origQty": "0.001",        # Original quantity
            "eventType": "ORDER_TRADE_UPDATE" или "ALGO_UPDATE",
        }
        """
        self._stats["order_updates_received"] += 1

        # Плоский формат от binance.py
        order_id = str(order_info.get("orderId", ""))
        order_status = order_info.get("status", "")
        order_type = order_info.get("type", "")
        event_type = order_info.get("eventType", "")

        # FIX #7: Обрабатываем PARTIALLY_FILLED - обновляем position.exit_filled_qty
        if order_status == "PARTIALLY_FILLED":
            position_id = self._order_to_position.get(order_id)
            if position_id:
                position = self.trade_engine.positions.get(position_id)
                if position and position.is_open:
                    filled_qty = float(order_info.get("executedQty", "0"))
                    orig_qty = order_info.get("origQty", "0")
                    avg_price = order_info.get("avgPrice", "0")

                    # Обновляем exit_filled_qty - это кумулятивное значение
                    position.exit_filled_qty = filled_qty

                    logger.info(
                        f"PARTIAL FILL: order {order_id} for position {position_id} - "
                        f"filled {filled_qty}/{orig_qty} @ {avg_price} "
                        f"(exit_filled_qty={position.exit_filled_qty})"
                    )
            return

        # FIX #7: Обрабатываем CANCELLED - если был partial fill, позиция частично закрыта
        if order_status in ("CANCELED", "CANCELLED", "EXPIRED"):
            position_id = self._order_to_position.get(order_id)
            if position_id:
                position = self.trade_engine.positions.get(position_id)
                if position and position.is_open and position.exit_filled_qty > 0:
                    # Partial fill + cancel = позиция частично закрыта
                    logger.warning(
                        f"EXIT ORDER CANCELLED after partial fill: "
                        f"position {position_id} partially closed "
                        f"(filled_qty={position.exit_filled_qty}, remaining={position.quantity - position.exit_filled_qty})"
                    )

                    # Определяем тип ордера для причины
                    if order_id == position.sl_order_id:
                        exit_reason = "SL_PARTIAL"
                    elif order_id == position.tp_order_id:
                        exit_reason = "TP_PARTIAL"
                    elif order_id == position.trailing_stop_order_id:
                        exit_reason = "TRAILING_PARTIAL"
                    else:
                        exit_reason = "PARTIAL"

                    # Если исполнено >= 99% - считаем полностью закрытой
                    fill_ratio = position.exit_filled_qty / position.quantity if position.quantity > 0 else 0
                    if fill_ratio >= 0.99:
                        # Полностью закрыта (с погрешностью округления)
                        exit_price = float(order_info.get("avgPrice", 0)) or position.entry_price
                        self._close_position_partial(position, exit_reason, exit_price, position.exit_filled_qty)
                    else:
                        # Частично закрыта - обновляем quantity и регистрируем для missing TP
                        remaining_qty = position.quantity - position.exit_filled_qty
                        logger.warning(
                            f"Position {position_id} partially closed: "
                            f"{position.exit_filled_qty}/{position.quantity} filled, "
                            f"remaining {remaining_qty}"
                        )
                        # Обновляем quantity на оставшийся размер
                        position.quantity = remaining_qty
                        position.exit_filled_qty = 0.0  # Сбрасываем для следующего exit order

                        # Очищаем отменённый ордер
                        if order_id == position.tp_order_id:
                            position.tp_order_id = ""
                        elif order_id == position.trailing_stop_order_id:
                            position.trailing_stop_order_id = ""
                            position.trailing_stop_enabled = False

                        # Регистрируем для missing TP мониторинга
                        # (SL остаётся активным на оставшуюся позицию)
                        if not position.tp_order_id and not position.trailing_stop_order_id:
                            self.register_missing_tp(position)
                            logger.warning(
                                f"Position {position_id} needs new TP for remaining {remaining_qty}"
                            )
                else:
                    # FIX #9: CANCELLED без partial fill
                    # Trailing stop отменён биржей (REJECTED, EXPIRED, etc.)
                    # Нужно поставить fallback TP или зарегистрировать missing TP
                    if order_id == position.trailing_stop_order_id:
                        logger.warning(
                            f"TRAILING STOP CANCELLED for position {position_id} "
                            f"(order {order_id}) - registering for fallback TP"
                        )
                        position.trailing_stop_order_id = ""
                        position.trailing_stop_enabled = False

                        # Если нет TP - регистрируем для missing TP мониторинга
                        if not position.tp_order_id:
                            self.register_missing_tp(position)
                            # Warning callback
                            if self.on_warning:
                                try:
                                    self.on_warning(
                                        "WARNING",
                                        f"Trailing stop cancelled, position needs TP",
                                        position,
                                        {
                                            "order_id": order_id,
                                            "order_status": order_status,
                                            "note": "Position registered for missing TP monitoring",
                                        }
                                    )
                                except Exception as e:
                                    logger.error(f"Warning callback error: {e}")
            return

        # Для закрытия позиции нужен статус FILLED
        if order_status != "FILLED":
            return

        # Проверяем, это наш SL/TP ордер?
        position_id = self._order_to_position.get(order_id)
        if not position_id:
            logger.debug(f"Order {order_id} not tracked (not our SL/TP)")
            return

        # Получаем позицию
        position = self.trade_engine.positions.get(position_id)
        if not position:
            logger.warning(f"Position {position_id} not found for order {order_id}")
            return

        if not position.is_open:
            logger.debug(f"Position {position_id} already closed")
            return

        # Определяем причину закрытия
        if order_id == position.sl_order_id:
            exit_reason = "SL"
            self._stats["positions_closed_sl"] += 1
        elif order_id == position.tp_order_id:
            exit_reason = "TP"
            self._stats["positions_closed_tp"] += 1
        elif order_id == position.trailing_stop_order_id:
            exit_reason = "TRAILING_STOP"
            # Используем счётчик TP для trailing stop (оба - take profit)
            self._stats["positions_closed_tp"] += 1
        else:
            exit_reason = "UNKNOWN"

        # Получаем цену исполнения
        exit_price = float(order_info.get("avgPrice", 0))

        # Рассчитываем realized PnL
        # Для ALGO_UPDATE (SL/Trailing) нет поля rp, считаем вручную
        if event_type == "ALGO_UPDATE":
            # КРИТИЧНО: executedQty может быть "0" или отсутствовать до финального события
            # Используем position.quantity как fallback если executedQty <= 0
            raw_qty = float(order_info.get("executedQty", 0))
            qty = raw_qty if raw_qty > 0 else float(position.quantity)
            if position.side == PositionSide.LONG:
                realized_pnl = (exit_price - position.entry_price) * qty
            else:  # SHORT
                realized_pnl = (position.entry_price - exit_price) * qty
        else:
            # ORDER_TRADE_UPDATE (TP) — берём из события если есть
            realized_pnl = float(order_info.get("realizedPnl", 0))

        # Thread-safe закрытие позиции
        was_closed = position.close_safe(
            exit_reason=exit_reason,
            exit_price=exit_price,
            realized_pnl=realized_pnl,
        )

        if not was_closed:
            logger.warning(f"Position {position_id} was already closed by another source")
            return

        logger.info(
            f"Position {position_id} CLOSED by {exit_reason} @ {exit_price:.6f} "
            f"(PnL: {realized_pnl:+.2f} USDT)"
        )

        # Удаляем из мониторинга
        self.unregister_position(position)

        # Отменяем противоположный ордер (если SL сработал - отменяем TP, и наоборот)
        # Это делается асинхронно, но мы в sync callback, поэтому создаём task
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._cancel_remaining_order(position, exit_reason))
        except RuntimeError:
            # Нет запущенного event loop (например, в тестах)
            # Пропускаем отмену ордера - REST sync подберёт это позже
            logger.warning(
                f"No event loop - skipping cancel of remaining order for {position.position_id}"
            )

        # Вызываем callback
        if self.on_position_closed:
            try:
                self.on_position_closed(position, exit_reason, realized_pnl)
            except Exception as e:
                logger.error(f"Position closed callback error: {e}")

    async def _cancel_order_with_retry(
        self,
        symbol: str,
        order_id: str,
        is_algo: bool,
        order_type: str,
        retries: int = 3,
    ) -> bool:
        """
        Отменить ордер с retry логикой.

        Args:
            symbol: Торговая пара
            order_id: ID ордера (orderId или algoId)
            is_algo: True если Algo ордер (SL, Trailing), False если обычный (TP)
            order_type: Тип ордера для логирования (SL, TP, TRAILING)
            retries: Количество попыток

        Returns:
            True если успешно, False если все попытки неудачны
        """
        for attempt in range(retries):
            try:
                if is_algo:
                    # FIX: Безопасное преобразование order_id в int
                    algo_id = _safe_int_order_id(order_id)
                    if algo_id is None:
                        logger.warning(f"Invalid algo order_id: {order_id}, skipping cancel")
                        return True  # Nothing to cancel
                    await self.exchange.cancel_algo_order(symbol, algo_id=algo_id)
                else:
                    await self.exchange.cancel_order(symbol, order_id)
                logger.info(f"Cancelled {order_type} order {order_id}")
                return True
            except Exception as e:
                error_str = str(e).lower()
                # Ордер уже отменён или не существует - это успех
                if "unknown" in error_str or "not found" in error_str or "not exist" in error_str:
                    logger.debug(f"{order_type} order {order_id} already cancelled/not found")
                    return True

                logger.warning(
                    f"Cancel {order_type} order {order_id} failed (attempt {attempt + 1}/{retries}): {e}"
                )
                if attempt < retries - 1:
                    await asyncio.sleep(1)  # Пауза перед retry

        # Все попытки неудачны - добавляем в очередь
        self._pending_cancels.append((symbol, order_id, is_algo, order_type, 0))
        logger.error(
            f"Failed to cancel {order_type} order {order_id} after {retries} attempts, added to retry queue"
        )
        return False

    async def _cancel_remaining_order(
        self,
        position: Position,
        exit_reason: str,
    ) -> None:
        """
        Отменить оставшиеся ордера после закрытия позиции.

        ВАЖНО:
        - TP = обычный LIMIT ордер → cancel_order (orderId)
        - SL = Algo ордер → cancel_algo_order (algoId)
        - Trailing Stop = Algo ордер → cancel_algo_order (algoId)

        Использует retry логику и добавляет в очередь при неудаче.
        """
        if exit_reason == "SL":
            # SL сработал - отменяем TP и trailing stop
            if position.tp_order_id:
                await self._cancel_order_with_retry(
                    position.symbol, position.tp_order_id, is_algo=False, order_type="TP"
                )
            if position.trailing_stop_order_id:
                await self._cancel_order_with_retry(
                    position.symbol, position.trailing_stop_order_id, is_algo=True, order_type="TRAILING"
                )

        elif exit_reason == "TP":
            # TP сработал - отменяем SL и trailing stop
            if position.sl_order_id:
                await self._cancel_order_with_retry(
                    position.symbol, position.sl_order_id, is_algo=True, order_type="SL"
                )
            if position.trailing_stop_order_id:
                await self._cancel_order_with_retry(
                    position.symbol, position.trailing_stop_order_id, is_algo=True, order_type="TRAILING"
                )

        elif exit_reason == "TRAILING_STOP":
            # Trailing Stop сработал - отменяем SL и TP
            if position.sl_order_id:
                await self._cancel_order_with_retry(
                    position.symbol, position.sl_order_id, is_algo=True, order_type="SL"
                )
            if position.tp_order_id:
                await self._cancel_order_with_retry(
                    position.symbol, position.tp_order_id, is_algo=False, order_type="TP"
                )

    async def _cancel_all_position_orders(self, position: Position) -> None:
        """
        Отменить ВСЕ ордера позиции (SL, TP, Trailing Stop).

        Используется при закрытии через REST sync fix когда неизвестно
        какой ордер сработал.
        """
        # SL = Algo ордер
        if position.sl_order_id:
            await self._cancel_order_with_retry(
                position.symbol, position.sl_order_id, is_algo=True, order_type="SL"
            )

        # TP = обычный LIMIT ордер
        if position.tp_order_id:
            await self._cancel_order_with_retry(
                position.symbol, position.tp_order_id, is_algo=False, order_type="TP"
            )

        # Trailing Stop = Algo ордер
        if position.trailing_stop_order_id:
            await self._cancel_order_with_retry(
                position.symbol, position.trailing_stop_order_id, is_algo=True, order_type="TRAILING"
            )

    def _close_position_partial(
        self,
        position: Position,
        exit_reason: str,
        exit_price: float,
        filled_qty: float,
    ) -> None:
        """
        FIX #7: Закрыть позицию после partial fill когда >= 99% исполнено.

        Args:
            position: Позиция для закрытия
            exit_reason: Причина (SL_PARTIAL, TP_PARTIAL, etc)
            exit_price: Цена исполнения
            filled_qty: Исполненное количество
        """
        # Рассчитываем PnL на исполненный объём
        if position.side == PositionSide.LONG:
            realized_pnl = (exit_price - position.entry_price) * filled_qty
        else:  # SHORT
            realized_pnl = (position.entry_price - exit_price) * filled_qty

        # Thread-safe закрытие позиции
        was_closed = position.close_safe(
            exit_reason=exit_reason,
            exit_price=exit_price,
            realized_pnl=realized_pnl,
        )

        if not was_closed:
            logger.warning(f"Position {position.position_id} was already closed")
            return

        logger.info(
            f"Position {position.position_id} CLOSED by {exit_reason} @ {exit_price:.6f} "
            f"(filled_qty={filled_qty}, PnL: {realized_pnl:+.2f} USDT)"
        )

        # Удаляем из мониторинга
        self.unregister_position(position)

        # Вызываем callback
        if self.on_position_closed:
            try:
                self.on_position_closed(position, exit_reason, realized_pnl)
            except Exception as e:
                logger.error(f"Position closed callback error: {e}")

    async def _cancel_retry_loop(self) -> None:
        """
        Периодически обрабатывать очередь неудачных отмен ордеров.

        Если отмена не удалась с первого раза, пробуем снова через интервал.
        """
        while self._running:
            try:
                await asyncio.sleep(self._cancel_retry_interval)

                if not self._pending_cancels:
                    continue

                logger.info(f"Processing {len(self._pending_cancels)} pending order cancels")

                # Обрабатываем копию списка
                pending = self._pending_cancels.copy()
                self._pending_cancels.clear()

                for symbol, order_id, is_algo, order_type, retry_count in pending:
                    if retry_count >= self._cancel_retry_max:
                        logger.error(
                            f"Giving up on cancelling {order_type} order {order_id} "
                            f"after {retry_count} retries"
                        )
                        continue

                    try:
                        if is_algo:
                            # FIX: Безопасное преобразование order_id в int
                            algo_id = _safe_int_order_id(order_id)
                            if algo_id is None:
                                logger.debug(f"Retry: Invalid algo order_id {order_id}, skipping")
                                continue
                            await self.exchange.cancel_algo_order(symbol, algo_id=algo_id)
                        else:
                            await self.exchange.cancel_order(symbol, order_id)
                        logger.info(f"Retry: Cancelled {order_type} order {order_id}")
                    except Exception as e:
                        error_str = str(e).lower()
                        if "unknown" in error_str or "not found" in error_str:
                            logger.debug(f"Retry: {order_type} order {order_id} already gone")
                        else:
                            # Добавляем обратно в очередь
                            self._pending_cancels.append(
                                (symbol, order_id, is_algo, order_type, retry_count + 1)
                            )
                            logger.warning(
                                f"Retry failed for {order_type} order {order_id}: {e}"
                            )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cancel retry loop: {e}")

    def _handle_account_update(self, event: Dict[str, Any]) -> None:
        """
        Обработать ACCOUNT_UPDATE событие.

        Используется для дополнительной проверки - если позиция закрылась
        но ORDER_TRADE_UPDATE не пришёл.

        Структура:
        {
            "e": "ACCOUNT_UPDATE",
            "a": {
                "P": [  # Positions
                    {
                        "s": "BTCUSDT",
                        "pa": "0",      # Position amount (0 = closed)
                        "ep": "0.00",   # Entry price
                        "cr": "10.5",   # Accumulated realized
                        "ps": "LONG"    # Position side
                    }
                ]
            }
        }
        """
        self._stats["account_updates_received"] += 1

        # Получаем позиции из события
        account_data = event.get("a", {})
        positions_data = account_data.get("P", [])

        for pos_data in positions_data:
            symbol = pos_data.get("s", "")
            position_amt = Decimal(str(pos_data.get("pa", "0")))
            # FIX #2: Use positionSide to correctly identify position in Hedge Mode
            position_side = pos_data.get("ps", "BOTH")  # LONG/SHORT/BOTH

            # Если позиция закрылась (amount = 0)
            if position_amt == 0:
                # Ищем нашу позицию по символу И positionSide
                for position in self.trade_engine.get_open_positions():
                    # FIX: Match by symbol AND positionSide (Hedge Mode support)
                    if (position.symbol == symbol and
                        position.side.value == position_side and
                        position.is_open):
                        # Позиция должна была быть закрыта через ORDER_TRADE_UPDATE
                        # Но если нет - закрываем здесь как fallback
                        logger.warning(
                            f"Position {position.position_id} ({position_side}) closed via ACCOUNT_UPDATE "
                            f"(ORDER_TRADE_UPDATE missed?)"
                        )
                        was_closed = position.close_safe(exit_reason="ACCOUNT_UPDATE")
                        if was_closed:
                            self.unregister_position(position)

    def get_stats(self) -> Dict[str, Any]:
        """Получить статистику."""
        return {
            **self._stats,
            "tracked_orders": len(self._order_to_position),
            "open_positions": len(self.trade_engine.get_open_positions()),
        }

    # =========================================================================
    # MAX HOLD DAYS - Автозакрытие по таймауту
    # =========================================================================

    async def _timeout_check_loop(self) -> None:
        """
        Периодическая проверка позиций на превышение max_hold_days.

        Интервал проверки: self._timeout_check_interval (по умолчанию 1 час).
        Если позиция открыта дольше max_hold_days - закрываем по MARKET.
        """
        logger.info(
            f"Timeout check loop started (interval: {self._timeout_check_interval}s)"
        )

        while self._running:
            try:
                await asyncio.sleep(self._timeout_check_interval)

                if not self._running:
                    break

                # Проверяем все открытые позиции
                expired_positions = []
                for position in self.trade_engine.get_open_positions():
                    if position.is_expired():
                        expired_positions.append(position)
                        logger.warning(
                            f"Position {position.position_id} EXPIRED: "
                            f"held {position.get_hold_days():.1f} days > "
                            f"max {position.max_hold_days} days"
                        )

                # Закрываем просроченные позиции
                for position in expired_positions:
                    success = await self._close_position_timeout(position)
                    if not success:
                        # Retry через следующий цикл проверки
                        logger.error(
                            f"Failed to close timeout position {position.position_id}, "
                            f"will retry in {self._timeout_check_interval}s"
                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Timeout check error: {e}")
                # Продолжаем работу

        logger.info("Timeout check loop stopped")

    async def _close_position_timeout(self, position: Position) -> bool:
        """
        Закрыть позицию по таймауту (max_hold_days превышен).

        1. Отменяем SL и TP ордера
        2. Закрываем позицию MARKET ордером
        3. Обновляем статус и вызываем callback

        Args:
            position: Позиция для закрытия

        Returns:
            True если успешно закрыта
        """
        if not position.is_open:
            return False

        logger.info(
            f"Closing position {position.position_id} by TIMEOUT "
            f"(held {position.get_hold_days():.1f} days)"
        )

        try:
            # 1. Отменяем SL, TP и Trailing Stop ордера
            if position.sl_order_id:
                # FIX: Безопасное преобразование order_id в int
                sl_algo_id = _safe_int_order_id(position.sl_order_id)
                if sl_algo_id is not None:
                    try:
                        # SL = Algo ордер
                        await self.exchange.cancel_algo_order(
                            position.symbol, algo_id=sl_algo_id
                        )
                        logger.info(f"Cancelled SL Algo order {position.sl_order_id}")
                    except Exception as e:
                        logger.warning(f"Failed to cancel SL Algo order: {e}")

            if position.tp_order_id:
                try:
                    # TP = обычный LIMIT ордер
                    await self.exchange.cancel_order(position.symbol, position.tp_order_id)
                    logger.info(f"Cancelled TP order {position.tp_order_id}")
                except Exception as e:
                    logger.warning(f"Failed to cancel TP order: {e}")

            if position.trailing_stop_order_id:
                # FIX: Безопасное преобразование order_id в int
                ts_algo_id = _safe_int_order_id(position.trailing_stop_order_id)
                if ts_algo_id is not None:
                    try:
                        # Trailing Stop = Algo ордер
                        await self.exchange.cancel_algo_order(
                            position.symbol, algo_id=ts_algo_id
                        )
                        logger.info(f"Cancelled Trailing Stop Algo order {position.trailing_stop_order_id}")
                    except Exception as e:
                        logger.warning(f"Failed to cancel Trailing Stop Algo order: {e}")

            # 2. Закрываем позицию MARKET ордером
            from ..core.models import OrderSide

            exit_side = OrderSide.SELL if position.is_long else OrderSide.BUY

            close_result = await self.exchange.place_market_order(
                symbol=position.symbol,
                side=exit_side,
                quantity=Decimal(str(position.quantity)),
                position_side=position.side,
                reduce_only=True,
            )

            if not close_result:
                logger.error(f"Failed to close position {position.position_id}")
                return False

            # 3. Получаем цену закрытия и PnL
            exit_price = float(close_result.get("avgPrice", 0))
            # PnL из ордера (если есть) или рассчитываем
            realized_pnl = float(close_result.get("realizedPnl", 0))
            if realized_pnl == 0 and exit_price > 0:
                # Рассчитываем примерный PnL
                if position.is_long:
                    realized_pnl = (exit_price - position.entry_price) * position.quantity
                else:
                    realized_pnl = (position.entry_price - exit_price) * position.quantity

            # 4. Thread-safe закрытие позиции
            was_closed = position.close_safe(
                exit_reason="TIMEOUT",
                exit_price=exit_price,
                realized_pnl=realized_pnl,
            )

            if not was_closed:
                logger.warning(f"Position {position.position_id} was already closed")
                return

            # 5. Удаляем из мониторинга
            self.unregister_position(position)

            self._stats["positions_closed_timeout"] += 1

            logger.info(
                f"Position {position.position_id} CLOSED by TIMEOUT @ {exit_price:.6f} "
                f"(PnL: {realized_pnl:+.2f} USDT, held {position.get_hold_days():.1f} days)"
            )

            # 6. Вызываем callback
            if self.on_position_closed:
                try:
                    self.on_position_closed(position, "TIMEOUT", realized_pnl)
                except Exception as e:
                    logger.error(f"Position closed callback error: {e}")

            return True

        except Exception as e:
            logger.exception(f"Error closing position {position.position_id} by timeout: {e}")
            return False

    # =========================================================================
    # MISSING TP - Мониторинг позиций без Take Profit
    # =========================================================================

    async def _missing_tp_check_loop(self) -> None:
        """
        Периодическая проверка позиций без TP ордера.

        Интервал: каждые 10 минут
        - Отправляет alert
        - Проверяет не поставил ли пользователь TP вручную
        - Через 1 час закрывает позицию если TP так и нет
        """
        import time as time_module

        logger.info(
            f"Missing TP check loop started (interval: {self._missing_tp_check_interval}s, "
            f"max wait: {self._missing_tp_max_wait}s)"
        )

        first_check = True  # Первая проверка сразу, без ожидания

        while self._running:
            try:
                # Первую проверку делаем сразу (через 5 сек для стабилизации)
                # Последующие - каждые 10 минут
                if first_check:
                    await asyncio.sleep(5)  # Небольшая пауза для инициализации
                    first_check = False
                else:
                    await asyncio.sleep(self._missing_tp_check_interval)

                if not self._running:
                    break

                now = time_module.time()
                positions_to_close = []
                positions_to_remove = []

                for position_id, registered_at in list(self._missing_tp_positions.items()):
                    position = self.trade_engine.positions.get(position_id)

                    if not position or not position.is_open:
                        # Позиция закрыта - убираем из мониторинга
                        positions_to_remove.append(position_id)
                        continue

                    # Проверяем появился ли TP (пользователь поставил вручную)
                    if position.tp_order_id:
                        logger.info(
                            f"Position {position_id} now has TP order {position.tp_order_id} - "
                            f"removed from missing TP monitoring"
                        )
                        # Регистрируем TP в order mapping
                        self._order_to_position[position.tp_order_id] = position_id
                        positions_to_remove.append(position_id)
                        continue

                    # Проверяем есть ли TP ордер на бирже (может пользователь поставил через UI)
                    tp_exists = await self._check_tp_exists_on_exchange(position)
                    if tp_exists:
                        logger.info(f"Position {position_id} has TP on exchange - updating")
                        positions_to_remove.append(position_id)
                        continue

                    # Сколько прошло времени
                    elapsed = now - registered_at

                    if elapsed >= self._missing_tp_max_wait:
                        # Прошёл 1 час - закрываем
                        logger.warning(
                            f"Position {position_id} missing TP for {elapsed/60:.0f} min - "
                            f"CLOSING by MARKET"
                        )
                        positions_to_close.append(position)
                    else:
                        # Отправляем alert каждые 10 минут
                        remaining = self._missing_tp_max_wait - elapsed
                        self._stats["missing_tp_alerts_sent"] += 1

                        logger.warning(
                            f"Position {position_id} still missing TP! "
                            f"Will close in {remaining/60:.0f} min if not set."
                        )

                        # Callback для warning
                        if self.on_warning:
                            try:
                                self.on_warning(
                                    "WARNING",
                                    f"Position missing TP order",
                                    position,
                                    {
                                        "elapsed_min": elapsed / 60,
                                        "remaining_min": remaining / 60,
                                        "max_wait_min": self._missing_tp_max_wait / 60,
                                    }
                                )
                            except Exception as e:
                                logger.error(f"Warning callback error: {e}")

                # Удаляем из мониторинга
                for pos_id in positions_to_remove:
                    if pos_id in self._missing_tp_positions:
                        del self._missing_tp_positions[pos_id]

                # Закрываем просроченные
                for position in positions_to_close:
                    success = await self._close_position_missing_tp(position)
                    if not success:
                        # Возвращаем в мониторинг для retry
                        logger.error(
                            f"Failed to close missing TP position {position.position_id}, "
                            f"will retry in {self._missing_tp_check_interval}s"
                        )
                        # НЕ удаляем из _missing_tp_positions - попробуем снова

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Missing TP check error: {e}")

        logger.info("Missing TP check loop stopped")

    async def _check_tp_exists_on_exchange(self, position: Position) -> bool:
        """
        Проверить есть ли TP ордер на бирже для позиции.

        Пользователь мог поставить TP через UI биржи.
        """
        try:
            # Получаем открытые ордера для символа
            orders = await self.exchange.get_open_orders(symbol=position.symbol)

            # Ищем TAKE_PROFIT_MARKET ордер для нашей позиции
            expected_side = "SELL" if position.is_long else "BUY"

            for order in orders:
                if order.get("type") != "TAKE_PROFIT_MARKET":
                    continue
                if order.get("side") != expected_side:
                    continue
                if order.get("positionSide") != position.side.value:
                    continue

                # Нашли TP ордер - обновляем position
                tp_order_id = str(order.get("orderId", ""))
                position.tp_order_id = tp_order_id
                self._order_to_position[tp_order_id] = position.position_id
                logger.info(f"Found TP order {tp_order_id} on exchange for {position.position_id}")
                return True

            return False

        except Exception as e:
            logger.warning(f"Error checking TP on exchange: {e}")
            return False

    async def _close_position_missing_tp(self, position: Position) -> bool:
        """
        Закрыть позицию из-за отсутствия TP ордера в течение 1 часа.

        Args:
            position: Позиция для закрытия

        Returns:
            True если успешно закрыта
        """
        if not position.is_open:
            return False

        logger.warning(
            f"Closing position {position.position_id} due to MISSING TP "
            f"(no TP set for {self._missing_tp_max_wait // 60} minutes)"
        )

        try:
            # 1. Отменяем SL и Trailing Stop ордера
            if position.sl_order_id:
                # FIX: Безопасное преобразование order_id в int
                sl_algo_id = _safe_int_order_id(position.sl_order_id)
                if sl_algo_id is not None:
                    try:
                        # SL = Algo ордер
                        await self.exchange.cancel_algo_order(
                            position.symbol, algo_id=sl_algo_id
                        )
                        logger.info(f"Cancelled SL Algo order {position.sl_order_id}")
                    except Exception as e:
                        logger.warning(f"Failed to cancel SL Algo order: {e}")

            if position.trailing_stop_order_id:
                # FIX: Безопасное преобразование order_id в int
                ts_algo_id = _safe_int_order_id(position.trailing_stop_order_id)
                if ts_algo_id is not None:
                    try:
                        # Trailing Stop = Algo ордер
                        await self.exchange.cancel_algo_order(
                            position.symbol, algo_id=ts_algo_id
                        )
                        logger.info(f"Cancelled Trailing Stop Algo order {position.trailing_stop_order_id}")
                    except Exception as e:
                        logger.warning(f"Failed to cancel Trailing Stop Algo order: {e}")

            # 2. Закрываем позицию MARKET ордером
            from ..core.models import OrderSide

            exit_side = OrderSide.SELL if position.is_long else OrderSide.BUY

            close_result = await self.exchange.place_market_order(
                symbol=position.symbol,
                side=exit_side,
                quantity=Decimal(str(position.quantity)),
                position_side=position.side,
                reduce_only=True,
            )

            if not close_result:
                logger.error(f"Failed to close position {position.position_id}")
                return False

            # 3. Получаем цену закрытия и PnL
            exit_price = float(close_result.get("avgPrice", 0))
            realized_pnl = float(close_result.get("realizedPnl", 0))
            if realized_pnl == 0 and exit_price > 0:
                if position.is_long:
                    realized_pnl = (exit_price - position.entry_price) * position.quantity
                else:
                    realized_pnl = (position.entry_price - exit_price) * position.quantity

            # 4. Thread-safe закрытие позиции
            was_closed = position.close_safe(
                exit_reason="MISSING_TP",
                exit_price=exit_price,
                realized_pnl=realized_pnl,
            )

            if not was_closed:
                logger.warning(f"Position {position.position_id} was already closed")
                return False

            # 5. Удаляем из мониторинга
            self.unregister_position(position)
            if position.position_id in self._missing_tp_positions:
                del self._missing_tp_positions[position.position_id]

            self._stats["positions_closed_missing_tp"] += 1

            logger.warning(
                f"Position {position.position_id} CLOSED due to MISSING TP @ {exit_price:.6f} "
                f"(PnL: {realized_pnl:+.2f} USDT)"
            )

            # 6. Вызываем callback
            if self.on_position_closed:
                try:
                    self.on_position_closed(position, "MISSING_TP", realized_pnl)
                except Exception as e:
                    logger.error(f"Position closed callback error: {e}")

            return True

        except Exception as e:
            logger.exception(f"Error closing position {position.position_id} due to missing TP: {e}")
            return False

    # =========================================================================
    # REST SYNC - Периодическая синхронизация с биржей
    # =========================================================================

    async def _rest_sync_loop(self) -> None:
        """
        Периодическая REST синхронизация с биржей.

        Защита от пропущенных WebSocket событий:
        - Получает позиции и ордера с биржи через REST API
        - Сравнивает с нашим состоянием
        - Исправляет расхождения

        Интервал: self._rest_sync_interval (по умолчанию 10 минут).
        """
        logger.info(
            f"REST sync loop started (interval: {self._rest_sync_interval}s)"
        )

        while self._running:
            try:
                await asyncio.sleep(self._rest_sync_interval)

                if not self._running:
                    break

                await self._perform_rest_sync()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"REST sync error: {e}")

        logger.info("REST sync loop stopped")

    async def _perform_rest_sync(self) -> None:
        """
        Выполнить одну итерацию REST синхронизации.

        1. Получить позиции с биржи
        2. Получить открытые ордера с биржи
        3. Сравнить с нашими tracked позициями
        4. Исправить расхождения
        """
        self._stats["rest_sync_runs"] += 1

        logger.debug("REST sync: fetching exchange state...")

        # 1. Получаем позиции с биржи
        exchange_positions = await self.exchange.get_all_positions()

        # 2. Получаем открытые ордера с биржи
        exchange_orders = await self.exchange.get_open_orders()

        # 2.1. Получаем Algo ордера (SL STOP_MARKET, Trailing Stop)
        # ВАЖНО: get_open_orders() НЕ возвращает Algo ордера!
        exchange_algo_orders = await self.exchange.get_open_algo_orders()

        # FIX #3: Use (symbol, positionSide) to correctly identify positions in Hedge Mode
        # Previously used only symbol, which could close wrong position when LONG and SHORT exist
        exchange_position_keys = set()  # (symbol, positionSide)
        for pos in exchange_positions:
            symbol = pos.get("symbol", "")
            position_side = pos.get("positionSide", "BOTH")  # LONG/SHORT/BOTH
            position_amt = Decimal(str(pos.get("positionAmt", "0")))
            if position_amt != 0:
                exchange_position_keys.add((symbol, position_side))

        # Создаём dict order_id -> order для быстрого поиска
        exchange_order_ids = {str(o.get("orderId", "")): o for o in exchange_orders}

        # Добавляем Algo ордера по algoId (SL и Trailing Stop хранят algoId, не orderId)
        for algo_order in exchange_algo_orders:
            algo_id = str(algo_order.get("algoId", ""))
            if algo_id:
                exchange_order_ids[algo_id] = algo_order

        # 3. Проверяем каждую нашу tracked позицию
        positions_to_close = []

        # ВАЖНО: Копируем список чтобы избежать race condition
        # Во время итерации позиция может закрыться через WebSocket callback
        open_positions_snapshot = list(self.trade_engine.get_open_positions())

        # FIX: Позиции подозрительные на закрытие - требуют retry проверки
        suspicious_positions = []

        for position in open_positions_snapshot:
            # Проверка: позиция ещё существует на бирже?
            # FIX #3: Check by (symbol, positionSide) for Hedge Mode support
            position_key = (position.symbol, position.side.value)
            if position_key not in exchange_position_keys:
                # Позиция не найдена - помечаем как подозрительную для retry
                suspicious_positions.append((position, "POSITION_NOT_FOUND"))
                continue

            # Проверка: SL ордер ещё существует?
            if position.sl_order_id and position.sl_order_id not in exchange_order_ids:
                # SL ордер не найден - помечаем как подозрительную
                suspicious_positions.append((position, "SL_NOT_FOUND"))
                continue

            # Проверка: TP ордер ещё существует? (только если был)
            if position.tp_order_id and position.tp_order_id not in exchange_order_ids:
                # TP ордер не найден - помечаем как подозрительную
                suspicious_positions.append((position, "TP_NOT_FOUND"))
                continue

        # FIX: Retry логика для подозрительных позиций
        # Защита от phantom close при временных ошибках API
        if suspicious_positions:
            logger.info(
                f"REST sync: {len(suspicious_positions)} suspicious positions, "
                f"performing retry check in 3 seconds..."
            )
            await asyncio.sleep(3)

            # Повторно получаем данные с биржи
            retry_positions = await self.exchange.get_all_positions()
            retry_orders = await self.exchange.get_open_orders()
            retry_algo_orders = await self.exchange.get_open_algo_orders()

            # Строим retry sets
            retry_position_keys = set()
            for pos in retry_positions:
                symbol = pos.get("symbol", "")
                position_side = pos.get("positionSide", "BOTH")
                position_amt = Decimal(str(pos.get("positionAmt", "0")))
                if position_amt != 0:
                    retry_position_keys.add((symbol, position_side))

            retry_order_ids = {str(o.get("orderId", "")): o for o in retry_orders}
            for algo_order in retry_algo_orders:
                algo_id = str(algo_order.get("algoId", ""))
                if algo_id:
                    retry_order_ids[algo_id] = algo_order

            # Проверяем подозрительные позиции повторно
            for position, reason in suspicious_positions:
                position_key = (position.symbol, position.side.value)

                if reason == "POSITION_NOT_FOUND":
                    if position_key not in retry_position_keys:
                        # Подтверждено: позиция закрыта
                        logger.warning(
                            f"REST sync: Position {position.position_id} ({position.symbol} {position.side.value}) "
                            f"confirmed CLOSED after retry (WS event missed)"
                        )
                        positions_to_close.append(position)
                    else:
                        logger.info(
                            f"REST sync: Position {position.position_id} found on retry - "
                            f"temporary API glitch avoided"
                        )

                elif reason == "SL_NOT_FOUND":
                    if position.sl_order_id not in retry_order_ids:
                        # Подтверждено: SL исполнился
                        logger.warning(
                            f"REST sync: SL order {position.sl_order_id} for position "
                            f"{position.position_id} confirmed FILLED after retry (WS missed)"
                        )
                        positions_to_close.append(position)
                        self._stats["rest_sync_orders_fixed"] += 1
                    else:
                        logger.info(
                            f"REST sync: SL order {position.sl_order_id} found on retry - "
                            f"temporary API glitch avoided"
                        )

                elif reason == "TP_NOT_FOUND":
                    if position.tp_order_id not in retry_order_ids:
                        # Подтверждено: TP исполнился
                        logger.warning(
                            f"REST sync: TP order {position.tp_order_id} for position "
                            f"{position.position_id} confirmed FILLED after retry (WS missed)"
                        )
                        positions_to_close.append(position)
                        self._stats["rest_sync_orders_fixed"] += 1
                    else:
                        logger.info(
                            f"REST sync: TP order {position.tp_order_id} found on retry - "
                            f"temporary API glitch avoided"
                        )

        # 4. Закрываем позиции с расхождениями
        for position in positions_to_close:
            await self._close_position_sync_fix(position)

        if positions_to_close:
            logger.info(
                f"REST sync: fixed {len(positions_to_close)} positions "
                f"(total runs: {self._stats['rest_sync_runs']})"
            )
        else:
            logger.debug(
                f"REST sync: all {len(self.trade_engine.get_open_positions())} "
                f"positions in sync"
            )

        # 5. Очистка памяти: удаляем старые закрытые позиции (старше 7 дней)
        # Вызываем каждые 10 итераций (примерно раз в 100 минут при 10-мин интервале)
        if self._stats["rest_sync_runs"] % 10 == 0:
            cleaned = self.trade_engine.cleanup_old_positions(max_age_days=7)
            if cleaned > 0:
                self._stats["positions_cleaned"] = self._stats.get("positions_cleaned", 0) + cleaned

        # 6. Очистка осиротевших ордеров (TradeAI1 style)
        # Ордера без позиций = orphans, нужно отменить
        await self._clean_orphan_orders(
            exchange_positions,
            exchange_orders,
            exchange_algo_orders,
        )

    async def _close_position_sync_fix(self, position: Position) -> None:
        """
        Закрыть позицию обнаруженную через REST sync.

        Позиция уже закрыта на бирже, но мы не получили WebSocket событие.
        Просто обновляем наше состояние.

        FIX: Теперь пытаемся получить реальную цену закрытия из истории ордеров,
        а не используем текущую рыночную цену.
        """
        if not position.is_open:
            return

        # FIX: Пытаемся определить причину закрытия и получить реальную цену
        exit_reason = "SYNC_FIX"
        exit_price = 0.0
        price_source = "unknown"

        # 1. Проверяем SL ордер (Algo Order)
        if position.sl_order_id:
            # FIX: Безопасное преобразование order_id в int
            sl_algo_id = _safe_int_order_id(position.sl_order_id)
            if sl_algo_id is not None:
                try:
                    sl_details = await self.exchange.get_algo_order_details(
                        position.symbol, sl_algo_id
                    )
                    if sl_details:
                        status = sl_details.get("algoStatus", "")
                        if status == "FILLED":
                            exit_reason = "SL"
                            # avgPrice для Algo ордеров
                            exit_price = float(sl_details.get("avgPrice", 0))
                            price_source = "SL order"
                            logger.debug(f"Got exit price from SL order: {exit_price}")
                except Exception as e:
                    logger.debug(f"Could not get SL order details: {e}")

        # 2. Проверяем TP ордер (обычный LIMIT)
        if exit_price == 0 and position.tp_order_id:
            try:
                tp_details = await self.exchange.get_order_details(
                    position.symbol, position.tp_order_id
                )
                if tp_details:
                    status = tp_details.get("status", "")
                    if status == "FILLED":
                        exit_reason = "TP"
                        exit_price = float(tp_details.get("avgPrice", 0))
                        price_source = "TP order"
                        logger.debug(f"Got exit price from TP order: {exit_price}")
            except Exception as e:
                logger.debug(f"Could not get TP order details: {e}")

        # 3. Проверяем Trailing Stop ордер (Algo Order)
        if exit_price == 0 and position.trailing_stop_order_id:
            # FIX: Безопасное преобразование order_id в int
            ts_algo_id = _safe_int_order_id(position.trailing_stop_order_id)
            if ts_algo_id is not None:
                try:
                    ts_details = await self.exchange.get_algo_order_details(
                        position.symbol, ts_algo_id
                    )
                    if ts_details:
                        status = ts_details.get("algoStatus", "")
                        if status == "FILLED":
                            exit_reason = "TRAILING_STOP"
                            exit_price = float(ts_details.get("avgPrice", 0))
                            price_source = "Trailing stop order"
                            logger.debug(f"Got exit price from Trailing Stop: {exit_price}")
                except Exception as e:
                    logger.debug(f"Could not get Trailing Stop order details: {e}")

        # 4. Fallback: используем текущую цену если ордера не найдены
        if exit_price == 0:
            try:
                current_price = await self.exchange.get_price(position.symbol)
                exit_price = float(current_price)
                price_source = "current market (fallback)"
                logger.debug(f"Using current market price as fallback: {exit_price}")
            except Exception:
                exit_price = position.entry_price
                price_source = "entry price (last fallback)"
                logger.debug(f"Using entry price as last fallback: {exit_price}")

        # Рассчитываем PnL
        if position.is_long:
            realized_pnl = (exit_price - position.entry_price) * position.quantity
        else:
            realized_pnl = (position.entry_price - exit_price) * position.quantity

        # Thread-safe закрытие позиции
        was_closed = position.close_safe(
            exit_reason=exit_reason,
            exit_price=exit_price,
            realized_pnl=realized_pnl,
        )

        if not was_closed:
            logger.warning(f"Position {position.position_id} was already closed by another source")
            return

        # Удаляем из мониторинга
        self.unregister_position(position)

        # Отменяем оставшиеся ордера (если есть)
        # REST sync обнаружил что позиция закрыта, но остальные ордера могут висеть
        await self._cancel_all_position_orders(position)

        self._stats["rest_sync_positions_fixed"] += 1

        logger.warning(
            f"Position {position.position_id} CLOSED via REST sync ({exit_reason}) @ {exit_price:.6f} "
            f"(PnL: {realized_pnl:+.2f} USDT, price source: {price_source})"
        )

        # Вызываем callback
        if self.on_position_closed:
            try:
                self.on_position_closed(position, exit_reason, realized_pnl)
            except Exception as e:
                logger.error(f"Position closed callback error: {e}")
