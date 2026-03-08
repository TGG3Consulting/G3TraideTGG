# -*- coding: utf-8 -*-
"""
Trade Engine - Исполнение торговых сигналов.

Это ЗАМЕНА backtest_signals() для LIVE торговли.

backtest_signals():
    - Симулирует торговлю на историческим данных
    - Проверяет SL/TP по историческим свечам

TradeEngine.execute_signal():
    - Отправляет РЕАЛЬНЫЕ ордера на биржу
    - Ставит SL/TP как отдельные ордера
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Any, List
import uuid

# Импорт из strategies (Signal используется напрямую!)
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'GenerateHistorySignals'))

from strategies import Signal

from typing import Callable

from ..core.models import (
    Position,
    OrderSide,
    PositionSide,
    PositionStatus,
)
from ..core.interfaces import ExchangeInterface
from ..core.exceptions import (
    BinanceError,
    InsufficientBalanceError,
    LiquidationError,
    IPBanError,
    AuthError,
)

logger = logging.getLogger(__name__)

# Callback типы для алертов
AlertCallback = Callable[[str, str, Dict[str, Any]], None]  # (level, message, details)


class TradeEngine:
    """
    Trade Engine - исполняет сигналы на реальной бирже.

    Аналог backtest_signals(), но для LIVE.
    """

    def __init__(
        self,
        exchange: ExchangeInterface,
        # !!! НЕ МЕНЯТЬ БЕЗ ЯВНОГО УКАЗАНИЯ ПОЛЬЗОВАТЕЛЯ !!!
        default_order_size_usd: float = 10.0,
        default_leverage: int = 10,
        max_hold_days: int = 14,
        # === TRAILING STOP CONFIG ===
        trailing_stop_enabled: bool = False,
        trailing_stop_callback_rate: float = 1.0,
        trailing_stop_activation_pct: Optional[float] = None,
        trailing_stop_use_instead_of_tp: bool = True,
    ):
        """
        Инициализация Trade Engine.

        Args:
            exchange: Адаптер биржи (реализует ExchangeInterface)
            default_order_size_usd: Размер ордера по умолчанию
            default_leverage: Плечо по умолчанию
            max_hold_days: Автозакрытие позиции по таймауту (дни)
            trailing_stop_enabled: Включить trailing stop
            trailing_stop_callback_rate: Процент отката (0.1-5.0, default 1.0 = 1%)
            trailing_stop_activation_pct: Активация при X% профита (None = сразу)
            trailing_stop_use_instead_of_tp: True = заменить TP, False = в дополнение к TP
        """
        self.exchange = exchange
        self.default_order_size_usd = default_order_size_usd
        self.default_leverage = default_leverage
        self.max_hold_days = max_hold_days

        # Trailing Stop Config
        self.trailing_stop_enabled = trailing_stop_enabled
        self.trailing_stop_callback_rate = trailing_stop_callback_rate
        self.trailing_stop_activation_pct = trailing_stop_activation_pct
        self.trailing_stop_use_instead_of_tp = trailing_stop_use_instead_of_tp

        # Хранилище позиций
        self.positions: Dict[str, Position] = {}  # position_id -> Position

        # Position Manager (устанавливается извне)
        self.position_manager = None

        # Callback для алертов (устанавливается извне)
        # on_alert(level, message, details) где level = "INFO", "WARNING", "ERROR", "CRITICAL"
        self.on_alert: Optional[AlertCallback] = None

        # Retry конфигурация
        self.sl_max_retries = 3  # Попытки для SL ордера
        self.tp_max_retries = 3  # Попытки для TP ордера
        self.trailing_stop_max_retries = 3  # Попытки для trailing stop

        # Статистика
        self.signals_received = 0
        self.signals_executed = 0
        self.signals_skipped = 0
        self.sl_failures = 0     # SL не удалось поставить
        self.tp_failures = 0     # TP не удалось поставить
        self.trailing_stop_failures = 0  # Trailing stop не удалось поставить
        self.emergency_closes = 0  # Экстренные закрытия
        self.partial_fills = 0   # Частичные исполнения entry

    async def execute_signal(
        self,
        signal: Signal,
        order_size_usd: Optional[float] = None,
        regime_action: str = "FULL",
    ) -> Optional[Position]:
        """
        Исполнить торговый сигнал.

        ЭТО ЗАМЕНА backtest_signals() для LIVE!

        Error Recovery:
        - Entry fail: пропускаем сигнал
        - SL fail: retry 3 раза, если всё равно fail → закрываем позицию + alert
        - TP fail: retry 3 раза, если fail → оставляем без TP, регистрируем для мониторинга

        Args:
            signal: Signal объект из StrategyRunner.generate_signals()
            order_size_usd: Размер позиции в USD (или из regime_action)
            regime_action: FULL/DYN/OFF - влияет на размер

        Returns:
            Position если успешно, None если ошибка или пропущен
        """
        self.signals_received += 1

        # Определяем размер позиции
        if regime_action == "OFF":
            logger.info(f"SKIP signal {signal.signal_id}: regime_action=OFF")
            self.signals_skipped += 1
            return None

        if regime_action == "DYN":
            size_usd = 1.0  # Динамический размер = $1
        else:  # FULL
            size_usd = order_size_usd or self.default_order_size_usd

        logger.info("=" * 60)
        logger.info(f"EXECUTING SIGNAL: {signal.signal_id}")
        logger.info("=" * 60)
        logger.info(f"Symbol:     {signal.symbol}")
        logger.info(f"Direction:  {signal.direction}")
        logger.info(f"Entry:      {signal.entry}")
        logger.info(f"SL:         {signal.stop_loss}")
        logger.info(f"TP:         {signal.take_profit}")
        logger.info(f"Size USD:   ${size_usd}")
        logger.info(f"Action:     {regime_action}")

        # Переменные для tracking
        entry_result = None
        entry_price = Decimal("0")
        entry_order_id = ""
        quantity = Decimal("0")
        exit_side = OrderSide.SELL
        position_side = PositionSide.LONG
        sl_price = Decimal("0")
        tp_price = Decimal("0")

        try:
            # 1. Получаем текущую цену
            current_price = await self.exchange.get_price(signal.symbol)
            logger.info(f"Current price: {current_price}")

            # 2. Рассчитываем quantity
            quantity = Decimal(str(size_usd)) / current_price
            quantity = self.exchange.round_quantity(signal.symbol, quantity)
            logger.info(f"Quantity: {quantity}")

            # 3. Устанавливаем leverage
            await self.exchange.set_leverage(signal.symbol, self.default_leverage)

            # 4. Определяем стороны
            if signal.direction == "LONG":
                entry_side = OrderSide.BUY
                exit_side = OrderSide.SELL
                position_side = PositionSide.LONG
            else:
                entry_side = OrderSide.SELL
                exit_side = OrderSide.BUY
                position_side = PositionSide.SHORT

            # ===================================================================
            # ЗАЩИТА ОТ ДУБЛИКАТОВ: Проверяем биржу ПЕРЕД отправкой ордера
            # ===================================================================

            # 4.1. Проверяем есть ли уже позиция на бирже
            logger.info(f"Checking exchange for existing position: {signal.symbol} {position_side.value}")
            existing_position = await self.exchange.get_position_by_side(
                signal.symbol, position_side
            )
            logger.info(f"Exchange position check result: {existing_position is not None}")
            if existing_position:
                existing_qty = abs(Decimal(str(existing_position.get("positionAmt", 0))))
                if existing_qty > 0:
                    logger.warning(
                        f"SKIP {signal.symbol}: position already exists on exchange "
                        f"(positionSide={position_side.value}, qty={existing_qty})"
                    )
                    self.signals_skipped += 1
                    return None

            # 4.2. Проверяем есть ли уже SL/TP ордера на бирже (признак открытой позиции)
            open_orders = await self.exchange.get_open_orders(signal.symbol)
            sl_tp_orders = [
                o for o in open_orders
                if o.get("positionSide") == position_side.value
                and o.get("type") in ("STOP_MARKET", "TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET")
            ]
            if sl_tp_orders:
                logger.warning(
                    f"SKIP {signal.symbol}: SL/TP orders already exist on exchange "
                    f"(positionSide={position_side.value}, orders={len(sl_tp_orders)})"
                )
                self.signals_skipped += 1
                return None

            # 5. Открываем позицию (MARKET ордер)
            try:
                entry_result = await self.exchange.place_market_order(
                    symbol=signal.symbol,
                    side=entry_side,
                    quantity=quantity,
                    position_side=position_side,
                )
            except InsufficientBalanceError as e:
                self._send_alert("WARNING", f"Insufficient balance for {signal.symbol}", {
                    "signal_id": signal.signal_id,
                    "error": str(e),
                    "required_usd": size_usd,
                })
                self.signals_skipped += 1
                return None

            except (LiquidationError, AuthError, IPBanError) as e:
                # Критические ошибки - пробрасываем наверх
                raise

            except BinanceError as e:
                logger.error(f"Entry order failed: [{e.code}] {e.message}")
                self._send_alert("ERROR", f"Entry order failed for {signal.symbol}", {
                    "signal_id": signal.signal_id,
                    "error_code": e.code,
                    "error_message": e.message,
                })
                self.signals_skipped += 1
                return None

            if not entry_result:
                logger.error(f"Failed to place entry order (empty result)")
                self.signals_skipped += 1
                return None

            entry_order_id = str(entry_result.get("orderId", ""))
            entry_price_from_api = Decimal(str(entry_result.get("avgPrice", 0)))
            order_status = entry_result.get("status", "UNKNOWN")
            orig_qty = Decimal(str(entry_result.get("origQty", quantity)))
            executed_qty_from_api = Decimal(str(entry_result.get("executedQty", 0)))

            logger.info(
                f"Entry order API response: orderId={entry_order_id}, status={order_status}, "
                f"executed={executed_qty_from_api}/{orig_qty}, avgPrice={entry_price_from_api}"
            )

            # ===================================================================
            # ВЕРИФИКАЦИЯ: Проверяем РЕАЛЬНУЮ позицию на бирже
            # Binance ACK ответ может содержать executedQty=0 даже если ордер исполнился!
            # ===================================================================
            await asyncio.sleep(0.3)  # Даём бирже время на обновление

            # Получаем реальную позицию с биржи
            real_position = await self.exchange.get_position_by_side(
                signal.symbol, position_side
            )

            if real_position:
                # Позиция есть на бирже - используем реальные данные
                real_qty = abs(Decimal(str(real_position.get("positionAmt", 0))))
                real_entry_price = Decimal(str(real_position.get("entryPrice", 0)))

                logger.info(
                    f"Position VERIFIED on exchange: qty={real_qty}, entryPrice={real_entry_price}"
                )

                # Используем данные с биржи
                executed_qty = real_qty
                entry_price = real_entry_price if real_entry_price > 0 else Decimal(str(signal.entry))
            else:
                # Позиции нет на бирже - используем данные из API ответа
                executed_qty = executed_qty_from_api
                entry_price = entry_price_from_api

                if executed_qty == 0:
                    logger.error(
                        f"Entry order NOT FILLED: {signal.symbol} status={order_status}, "
                        f"orderId={entry_order_id}. No position found on exchange."
                    )
                    self._send_alert("ERROR", f"Entry order not filled: {signal.symbol}", {
                        "signal_id": signal.signal_id,
                        "order_id": entry_order_id,
                        "status": order_status,
                        "requested_qty": float(orig_qty),
                        "executed_qty": 0,
                        "note": "MARKET order failed. No position on exchange.",
                    })
                    self.signals_skipped += 1
                    return None

            # Используем фактическую цену (fallback на сигнал если нет)
            if entry_price == 0:
                entry_price = Decimal(str(signal.entry))
                logger.warning(f"No entry price available, using signal entry: {entry_price}")

            # Проверяем partial fill
            is_partial_fill = False
            if executed_qty < orig_qty:
                is_partial_fill = True
                self.partial_fills += 1
                logger.warning(
                    f"PARTIAL FILL detected: executed {executed_qty} / {orig_qty} "
                    f"({float(executed_qty / orig_qty * 100):.1f}%)"
                )
                self._send_alert("WARNING", f"Partial fill on entry: {signal.symbol}", {
                    "signal_id": signal.signal_id,
                    "requested": float(orig_qty),
                    "executed": float(executed_qty),
                    "fill_pct": float(executed_qty / orig_qty * 100),
                    "entry_price": float(entry_price),
                })

            # Используем фактическое количество для SL/TP
            quantity = executed_qty

            logger.info(f"Entry CONFIRMED: {entry_order_id} @ {entry_price} (qty: {executed_qty})")

            # ===================================================================
            # ENTRY УСПЕШЕН - теперь позиция ОТКРЫТА
            # Если дальше что-то пойдёт не так - нужно закрыть позицию!
            # ===================================================================

            # 6. Ставим SL ордер через Algo Order API (КРИТИЧНО)
            sl_price = self.exchange.round_price(
                signal.symbol, Decimal(str(signal.stop_loss))
            )
            sl_algo_id = ""  # Algo Order возвращает algoId, не orderId
            sl_client_id = f"SL_{signal.signal_id}"
            sl_success = False

            try:
                sl_result = await self.exchange.place_stop_order(
                    symbol=signal.symbol,
                    side=exit_side,
                    quantity=quantity,
                    stop_price=sl_price,
                    position_side=position_side,
                    reduce_only=True,
                    max_retries=self.sl_max_retries,
                    client_order_id=sl_client_id,
                )
                # Algo Order API возвращает algoId
                sl_algo_id = str(sl_result.get("algoId", "")) if sl_result else ""
                sl_success = bool(sl_algo_id)
                logger.info(f"SL Algo order placed: algoId={sl_algo_id} triggerPrice={sl_price}")

            except BinanceError as e:
                logger.error(f"SL Algo order FAILED: [{e.code}] {e.message}")
                self.sl_failures += 1

            # Если SL не удалось поставить - ЗАКРЫВАЕМ ПОЗИЦИЮ
            if not sl_success:
                logger.critical(f"SL FAILED - EMERGENCY CLOSE position!")
                await self._emergency_close_position(
                    symbol=signal.symbol,
                    side=exit_side,
                    quantity=quantity,
                    position_side=position_side,
                    reason="SL_PLACEMENT_FAILED",
                    signal=signal,
                    entry_price=float(entry_price),
                )
                self.signals_skipped += 1
                return None

            # 7. Ставим TP / Trailing Stop (менее критично - SL защищает)
            tp_price = self.exchange.round_price(
                signal.symbol, Decimal(str(signal.take_profit))
            )
            tp_order_id = ""
            tp_client_id = f"TP_{signal.signal_id}"
            tp_success = False
            trailing_stop_algo_id = ""  # Trailing Stop через Algo API
            trailing_stop_success = False

            # === TRAILING STOP (через Algo Order API) ===
            if self.trailing_stop_enabled:
                # Рассчитываем activation price если задан процент
                activation_price = None
                if self.trailing_stop_activation_pct is not None:
                    if signal.direction == "LONG":
                        activation_price = entry_price * (
                            Decimal("1") + Decimal(str(self.trailing_stop_activation_pct / 100))
                        )
                    else:
                        activation_price = entry_price * (
                            Decimal("1") - Decimal(str(self.trailing_stop_activation_pct / 100))
                        )
                    activation_price = self.exchange.round_price(signal.symbol, activation_price)

                trailing_client_id = f"TS_{signal.signal_id}"

                try:
                    trailing_result = await self.exchange.place_trailing_stop_order(
                        symbol=signal.symbol,
                        side=exit_side,
                        quantity=quantity,
                        callback_rate=self.trailing_stop_callback_rate,
                        activation_price=activation_price,
                        position_side=position_side,
                        reduce_only=True,
                        max_retries=self.trailing_stop_max_retries,
                        client_order_id=trailing_client_id,
                    )
                    # Algo API возвращает algoId
                    trailing_stop_algo_id = str(trailing_result.get("algoId", "")) if trailing_result else ""
                    trailing_stop_success = bool(trailing_stop_algo_id)

                    activation_info = f" (activation @ {activation_price})" if activation_price else " (immediate)"
                    logger.info(
                        f"Trailing stop Algo order placed: algoId={trailing_stop_algo_id} "
                        f"callback={self.trailing_stop_callback_rate}%{activation_info}"
                    )

                except BinanceError as e:
                    logger.error(f"Trailing stop Algo order FAILED: [{e.code}] {e.message}")
                    self.trailing_stop_failures += 1
                    self._send_alert("WARNING", f"Trailing stop failed for {signal.symbol}", {
                        "signal_id": signal.signal_id,
                        "error_code": e.code,
                        "error_message": e.message,
                        "callback_rate": self.trailing_stop_callback_rate,
                        "note": "Will try to place regular TP instead.",
                    })

                except ValueError as e:
                    logger.error(f"Trailing stop validation error: {e}")
                    self.trailing_stop_failures += 1

            # === FIXED TP как LIMIT ордер ===
            place_fixed_tp = (
                not self.trailing_stop_enabled or
                not trailing_stop_success or
                not self.trailing_stop_use_instead_of_tp
            )

            if place_fixed_tp:
                try:
                    tp_result = await self.exchange.place_take_profit_order(
                        symbol=signal.symbol,
                        side=exit_side,
                        quantity=quantity,
                        stop_price=tp_price,
                        position_side=position_side,
                        reduce_only=True,
                        max_retries=self.tp_max_retries,
                        client_order_id=tp_client_id,
                    )
                    tp_order_id = str(tp_result.get("orderId", "")) if tp_result else ""
                    tp_success = bool(tp_order_id)
                    logger.info(f"TP LIMIT order placed: orderId={tp_order_id} price={tp_price}")

                except BinanceError as e:
                    logger.error(f"TP order FAILED: [{e.code}] {e.message}")
                    self.tp_failures += 1
                    # Отправляем alert но НЕ закрываем - SL защищает
                    self._send_alert("WARNING", f"TP order failed for {signal.symbol}", {
                        "signal_id": signal.signal_id,
                        "error_code": e.code,
                        "error_message": e.message,
                        "entry_price": float(entry_price),
                        "sl_price": float(sl_price),
                        "tp_price": float(tp_price),
                        "note": "Position protected by SL, but no TP. Will monitor for 1 hour.",
                    })

            # 8. Создаём Position
            # ВАЖНО: SL и Trailing Stop используют algoId (Algo Order API)
            #        TP использует orderId (обычный LIMIT ордер)
            position = Position(
                position_id=f"POS_{signal.signal_id}_{uuid.uuid4().hex[:8]}",
                signal_id=signal.signal_id,
                symbol=signal.symbol,
                side=position_side,
                quantity=float(quantity),  # Фактически исполненное количество
                entry_price=float(entry_price),
                stop_loss=float(sl_price),
                take_profit=float(tp_price),
                status=PositionStatus.OPEN,
                entry_order_id=entry_order_id,
                sl_order_id=sl_algo_id,  # algoId для SL (Algo Order API)
                tp_order_id=tp_order_id if tp_success else "",  # orderId для TP (LIMIT)
                trailing_stop_order_id=trailing_stop_algo_id if trailing_stop_success else "",  # algoId
                trailing_stop_enabled=trailing_stop_success,
                trailing_stop_callback_rate=self.trailing_stop_callback_rate if trailing_stop_success else 0.0,
                trailing_stop_activation_price=float(activation_price) if (trailing_stop_success and activation_price) else 0.0,
                opened_at=datetime.utcnow(),
                strategy=signal.metadata.get("strategy", ""),
                regime_action=regime_action,
                max_hold_days=self.max_hold_days,
                requested_quantity=float(orig_qty),  # Запрошенное количество
                is_partial_fill=is_partial_fill,     # Флаг partial fill
            )

            self.positions[position.position_id] = position
            self.signals_executed += 1

            # Регистрируем в Position Manager для мониторинга SL/TP/TrailingStop
            if self.position_manager:
                self.position_manager.register_position(position)

                # Если ни TP ни trailing stop не удалось - регистрируем для мониторинга
                has_exit_order = tp_success or trailing_stop_success
                if not has_exit_order:
                    self.position_manager.register_missing_tp(position)

            logger.info(f"Position opened: {position.position_id}")
            if trailing_stop_success:
                logger.info(
                    f"Position {position.position_id} protected by TRAILING STOP "
                    f"(callback={self.trailing_stop_callback_rate}%)"
                )
            if tp_success:
                logger.info(f"Position {position.position_id} protected by FIXED TP @ {tp_price}")
            if not tp_success and not trailing_stop_success:
                logger.warning(f"Position {position.position_id} has NO TP/TRAILING ORDER - monitoring enabled")
            logger.info("=" * 60)

            return position

        except (LiquidationError, AuthError, IPBanError) as e:
            # Критические ошибки - пробрасываем для остановки бота
            logger.critical(f"CRITICAL ERROR in execute_signal: [{e.code}] {e.message}")
            self._send_alert("CRITICAL", f"Critical error: {e.message}", {
                "signal_id": signal.signal_id,
                "error_code": e.code,
                "error_category": e.category.value,
            })
            raise

        except BinanceError as e:
            logger.error(f"BinanceError executing signal {signal.signal_id}: [{e.code}] {e.message}")
            self._send_alert("ERROR", f"Signal execution failed: {e.message}", {
                "signal_id": signal.signal_id,
                "symbol": signal.symbol,
                "error_code": e.code,
            })
            self.signals_skipped += 1

        except Exception as e:
            logger.exception(f"Unexpected error executing signal {signal.signal_id}: {e}")
            self._send_alert("ERROR", f"Unexpected error: {str(e)[:100]}", {
                "signal_id": signal.signal_id,
                "symbol": signal.symbol,
                "error_type": type(e).__name__,
            })
            self.signals_skipped += 1

        return None

    def _send_alert(
        self,
        level: str,
        message: str,
        details: Dict[str, Any],
    ) -> None:
        """
        Отправить alert через callback.

        Args:
            level: INFO, WARNING, ERROR, CRITICAL
            message: Сообщение
            details: Детали
        """
        if self.on_alert:
            try:
                self.on_alert(level, message, details)
            except Exception as e:
                logger.error(f"Alert callback error: {e}")

        # Логируем в зависимости от уровня
        if level == "CRITICAL":
            logger.critical(f"ALERT [{level}]: {message}")
        elif level == "ERROR":
            logger.error(f"ALERT [{level}]: {message}")
        elif level == "WARNING":
            logger.warning(f"ALERT [{level}]: {message}")
        else:
            logger.info(f"ALERT [{level}]: {message}")

    async def _emergency_close_position(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        position_side: PositionSide,
        reason: str,
        signal: Signal,
        entry_price: float,
    ) -> None:
        """
        Экстренное закрытие позиции когда SL не удалось поставить.

        Args:
            symbol: Торговая пара
            side: Сторона закрытия (противоположная entry)
            quantity: Количество
            position_side: LONG/SHORT
            reason: Причина
            signal: Оригинальный сигнал
            entry_price: Цена входа
        """
        self.emergency_closes += 1

        logger.critical(f"EMERGENCY CLOSE: {symbol} {reason}")

        exit_price = 0.0

        try:
            close_result = await self.exchange.place_market_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                position_side=position_side,
                reduce_only=True,
                max_retries=5,  # Больше попыток для экстренного закрытия
            )

            if close_result:
                exit_price = float(close_result.get("avgPrice", 0))
                logger.info(f"Emergency close executed @ {exit_price}")

        except Exception as e:
            logger.critical(f"EMERGENCY CLOSE FAILED: {e}")

        # Рассчитываем PnL
        pnl = 0.0
        if exit_price > 0 and entry_price > 0:
            if signal.direction == "LONG":
                pnl = (exit_price - entry_price) * float(quantity)
            else:
                pnl = (entry_price - exit_price) * float(quantity)

        # Отправляем детальный alert
        self._send_alert("CRITICAL", f"EMERGENCY CLOSE: {symbol}", {
            "signal_id": signal.signal_id,
            "symbol": symbol,
            "direction": signal.direction,
            "reason": reason,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": float(quantity),
            "pnl": pnl,
            "note": "Position closed because SL order could not be placed. Position was unprotected.",
        })

    async def close_position(
        self,
        position_id: str,
        reason: str = "MANUAL",
    ) -> bool:
        """
        Закрыть позицию вручную.

        Args:
            position_id: ID позиции
            reason: Причина закрытия

        Returns:
            True если успешно
        """
        position = self.positions.get(position_id)
        if not position or not position.is_open:
            logger.warning(f"Position {position_id} not found or already closed")
            return False

        try:
            # Отменяем SL ордер (Algo Order API)
            if position.sl_order_id:
                await self.exchange.cancel_algo_order(
                    symbol=position.symbol,
                    algo_id=int(position.sl_order_id)
                )

            # Отменяем TP ордер (обычный LIMIT ордер)
            if position.tp_order_id:
                await self.exchange.cancel_order(position.symbol, position.tp_order_id)

            # Отменяем Trailing Stop если есть (Algo Order API)
            if position.trailing_stop_order_id:
                await self.exchange.cancel_algo_order(
                    symbol=position.symbol,
                    algo_id=int(position.trailing_stop_order_id)
                )

            # Закрываем позицию MARKET ордером
            exit_side = OrderSide.SELL if position.is_long else OrderSide.BUY

            await self.exchange.place_market_order(
                symbol=position.symbol,
                side=exit_side,
                quantity=Decimal(str(position.quantity)),
                position_side=position.side,
                reduce_only=True,
            )

            position.status = PositionStatus.CLOSED
            position.exit_reason = reason
            position.closed_at = datetime.utcnow()

            logger.info(f"Position {position_id} closed: {reason}")
            return True

        except Exception as e:
            logger.exception(f"Error closing position {position_id}: {e}")
            return False

    def get_open_positions(self) -> List[Position]:
        """Получить список открытых позиций."""
        return [p for p in self.positions.values() if p.is_open]

    def get_executed_signal_ids(self) -> set:
        """
        Получить все signal_id из всех позиций (открытых и закрытых).

        КРИТИЧНО: Используется для защиты от повторного исполнения сигналов.
        После закрытия позиции (SL/TP/Trailing) тот же сигнал НЕ должен
        исполняться повторно в тот же день.
        """
        return {p.signal_id for p in self.positions.values() if p.signal_id}

    def get_stats(self) -> Dict[str, Any]:
        """Получить статистику."""
        return {
            "signals_received": self.signals_received,
            "signals_executed": self.signals_executed,
            "signals_skipped": self.signals_skipped,
            "open_positions": len(self.get_open_positions()),
            "total_positions": len(self.positions),
            "sl_failures": self.sl_failures,
            "tp_failures": self.tp_failures,
            "trailing_stop_failures": self.trailing_stop_failures,
            "emergency_closes": self.emergency_closes,
            "partial_fills": self.partial_fills,
            "trailing_stop_enabled": self.trailing_stop_enabled,
        }
