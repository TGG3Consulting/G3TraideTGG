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
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Any, Callable, TYPE_CHECKING

from ..core.models import Position, PositionStatus

if TYPE_CHECKING:
    from .trade_engine import TradeEngine
    from ..adapters.binance import BinanceFuturesAdapter

logger = logging.getLogger(__name__)

# Callback для уведомлений о закрытии позиции
PositionClosedCallback = Callable[[Position, str, float], None]

# Callback для warnings (missing TP, etc.)
# Args: (level: str, message: str, position: Position, details: Dict)
WarningCallback = Callable[[str, str, Position, Dict[str, Any]], None]


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

            # Запускаем проверку max_hold_days (таймаут позиций)
            self._timeout_check_task = asyncio.create_task(self._timeout_check_loop())

            # Запускаем проверку позиций без TP
            self._missing_tp_check_task = asyncio.create_task(self._missing_tp_check_loop())

            # Запускаем периодическую REST синхронизацию
            self._rest_sync_task = asyncio.create_task(self._rest_sync_loop())

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

    def _handle_order_update(self, event: Dict[str, Any]) -> None:
        """
        Обработать ORDER_TRADE_UPDATE событие.

        Структура события:
        {
            "e": "ORDER_TRADE_UPDATE",
            "T": 1234567890123,  # Transaction time
            "o": {
                "s": "BTCUSDT",      # Symbol
                "i": 123456789,      # Order ID
                "X": "FILLED",       # Order status (NEW, PARTIALLY_FILLED, FILLED, CANCELED, EXPIRED)
                "o": "STOP_MARKET",  # Order type
                "ap": "50000.00",    # Average price (filled)
                "rp": "10.5",        # Realized profit
                "z": "0.001",        # Order Filled Accumulated Quantity
                "l": "0.001",        # Order Last Filled Quantity
                "L": "50000.00",     # Last Filled Price
                "q": "0.001",        # Original Quantity
                ...
            }
        }
        """
        self._stats["order_updates_received"] += 1

        order_data = event.get("o", {})
        order_id = str(order_data.get("i", ""))
        order_status = order_data.get("X", "")
        order_type = order_data.get("o", "")

        # Обрабатываем PARTIALLY_FILLED - логируем но не закрываем позицию
        if order_status == "PARTIALLY_FILLED":
            position_id = self._order_to_position.get(order_id)
            if position_id:
                filled_qty = order_data.get("z", "0")  # Accumulated filled
                orig_qty = order_data.get("q", "0")    # Original qty
                last_price = order_data.get("L", "0")  # Last fill price
                logger.info(
                    f"PARTIAL FILL: order {order_id} for position {position_id} - "
                    f"filled {filled_qty}/{orig_qty} @ {last_price}"
                )
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

        # Получаем цену исполнения и realized PnL
        exit_price = float(order_data.get("ap", 0))  # Average price
        realized_pnl = float(order_data.get("rp", 0))  # Realized profit

        # Обновляем позицию
        position.status = PositionStatus.CLOSED
        position.exit_reason = exit_reason
        position.exit_price = exit_price
        position.realized_pnl = realized_pnl
        position.closed_at = datetime.utcnow()

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

    async def _cancel_remaining_order(
        self,
        position: Position,
        exit_reason: str,
    ) -> None:
        """Отменить оставшиеся ордера после закрытия позиции."""
        try:
            if exit_reason == "SL":
                # SL сработал - отменяем TP и trailing stop
                if position.tp_order_id:
                    await self.exchange.cancel_order(position.symbol, position.tp_order_id)
                    logger.info(f"Cancelled TP order {position.tp_order_id}")
                if position.trailing_stop_order_id:
                    await self.exchange.cancel_order(position.symbol, position.trailing_stop_order_id)
                    logger.info(f"Cancelled trailing stop order {position.trailing_stop_order_id}")

            elif exit_reason == "TP" or exit_reason == "TRAILING_STOP":
                # TP/Trailing сработал - отменяем SL и другой exit ордер
                if position.sl_order_id:
                    await self.exchange.cancel_order(position.symbol, position.sl_order_id)
                    logger.info(f"Cancelled SL order {position.sl_order_id}")
                if exit_reason == "TP" and position.trailing_stop_order_id:
                    await self.exchange.cancel_order(position.symbol, position.trailing_stop_order_id)
                    logger.info(f"Cancelled trailing stop order {position.trailing_stop_order_id}")
                elif exit_reason == "TRAILING_STOP" and position.tp_order_id:
                    await self.exchange.cancel_order(position.symbol, position.tp_order_id)
                    logger.info(f"Cancelled TP order {position.tp_order_id}")

        except Exception as e:
            logger.error(f"Failed to cancel remaining order: {e}")

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

            # Если позиция закрылась (amount = 0)
            if position_amt == 0:
                # Ищем нашу позицию по символу
                for position in self.trade_engine.get_open_positions():
                    if position.symbol == symbol and position.is_open:
                        # Позиция должна была быть закрыта через ORDER_TRADE_UPDATE
                        # Но если нет - закрываем здесь как fallback
                        if position.status == PositionStatus.OPEN:
                            logger.warning(
                                f"Position {position.position_id} closed via ACCOUNT_UPDATE "
                                f"(ORDER_TRADE_UPDATE missed?)"
                            )
                            position.status = PositionStatus.CLOSED
                            position.exit_reason = "ACCOUNT_UPDATE"
                            position.closed_at = datetime.utcnow()
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
                    await self._close_position_timeout(position)

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
            # 1. Отменяем SL и TP ордера
            if position.sl_order_id:
                try:
                    await self.exchange.cancel_order(position.symbol, position.sl_order_id)
                    logger.info(f"Cancelled SL order {position.sl_order_id}")
                except Exception as e:
                    logger.warning(f"Failed to cancel SL order: {e}")

            if position.tp_order_id:
                try:
                    await self.exchange.cancel_order(position.symbol, position.tp_order_id)
                    logger.info(f"Cancelled TP order {position.tp_order_id}")
                except Exception as e:
                    logger.warning(f"Failed to cancel TP order: {e}")

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

            # 4. Обновляем позицию
            position.status = PositionStatus.CLOSED
            position.exit_reason = "TIMEOUT"
            position.exit_price = exit_price
            position.realized_pnl = realized_pnl
            position.closed_at = datetime.utcnow()

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
                    await self._close_position_missing_tp(position)

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
            # 1. Отменяем SL ордер
            if position.sl_order_id:
                try:
                    await self.exchange.cancel_order(position.symbol, position.sl_order_id)
                    logger.info(f"Cancelled SL order {position.sl_order_id}")
                except Exception as e:
                    logger.warning(f"Failed to cancel SL order: {e}")

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

            # 4. Обновляем позицию
            position.status = PositionStatus.CLOSED
            position.exit_reason = "MISSING_TP"
            position.exit_price = exit_price
            position.realized_pnl = realized_pnl
            position.closed_at = datetime.utcnow()

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

        # Создаём set символов с открытыми позициями на бирже
        exchange_position_symbols = set()
        for pos in exchange_positions:
            symbol = pos.get("symbol", "")
            position_amt = Decimal(str(pos.get("positionAmt", "0")))
            if position_amt != 0:
                exchange_position_symbols.add(symbol)

        # Создаём dict order_id -> order для быстрого поиска
        exchange_order_ids = {str(o.get("orderId", "")): o for o in exchange_orders}

        # 3. Проверяем каждую нашу tracked позицию
        positions_to_close = []

        for position in self.trade_engine.get_open_positions():
            # Проверка: позиция ещё существует на бирже?
            if position.symbol not in exchange_position_symbols:
                # Позиция закрылась, но мы не получили WebSocket событие
                logger.warning(
                    f"REST sync: Position {position.position_id} ({position.symbol}) "
                    f"not found on exchange - marking as CLOSED (WS event missed)"
                )
                positions_to_close.append(position)
                continue

            # Проверка: SL ордер ещё существует?
            if position.sl_order_id and position.sl_order_id not in exchange_order_ids:
                # SL ордер исполнился, но мы не получили WebSocket событие
                logger.warning(
                    f"REST sync: SL order {position.sl_order_id} for position "
                    f"{position.position_id} not found on exchange - SL was FILLED (WS missed)"
                )
                # Позиция должна была закрыться по SL
                positions_to_close.append(position)
                self._stats["rest_sync_orders_fixed"] += 1
                continue

            # Проверка: TP ордер ещё существует? (только если был)
            if position.tp_order_id and position.tp_order_id not in exchange_order_ids:
                # TP ордер исполнился, но мы не получили WebSocket событие
                logger.warning(
                    f"REST sync: TP order {position.tp_order_id} for position "
                    f"{position.position_id} not found on exchange - TP was FILLED (WS missed)"
                )
                # Позиция должна была закрыться по TP
                positions_to_close.append(position)
                self._stats["rest_sync_orders_fixed"] += 1
                continue

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

    async def _close_position_sync_fix(self, position: Position) -> None:
        """
        Закрыть позицию обнаруженную через REST sync.

        Позиция уже закрыта на бирже, но мы не получили WebSocket событие.
        Просто обновляем наше состояние.
        """
        if not position.is_open:
            return

        # Определяем причину (SL или TP)
        # Если SL ордер пропал - закрылось по SL
        # Если TP ордер пропал - закрылось по TP
        # Если оба пропали или позиция пропала - неизвестно
        exit_reason = "SYNC_FIX"

        # Пытаемся получить актуальную цену как exit_price
        try:
            current_price = await self.exchange.get_price(position.symbol)
            exit_price = float(current_price)
        except Exception:
            exit_price = position.entry_price  # fallback

        # Рассчитываем примерный PnL
        if position.is_long:
            realized_pnl = (exit_price - position.entry_price) * position.quantity
        else:
            realized_pnl = (position.entry_price - exit_price) * position.quantity

        # Обновляем позицию
        position.status = PositionStatus.CLOSED
        position.exit_reason = exit_reason
        position.exit_price = exit_price
        position.realized_pnl = realized_pnl
        position.closed_at = datetime.utcnow()

        # Удаляем из мониторинга
        self.unregister_position(position)

        self._stats["rest_sync_positions_fixed"] += 1

        logger.warning(
            f"Position {position.position_id} CLOSED via REST sync fix @ ~{exit_price:.6f} "
            f"(approx PnL: {realized_pnl:+.2f} USDT)"
        )

        # Вызываем callback
        if self.on_position_closed:
            try:
                self.on_position_closed(position, exit_reason, realized_pnl)
            except Exception as e:
                logger.error(f"Position closed callback error: {e}")
