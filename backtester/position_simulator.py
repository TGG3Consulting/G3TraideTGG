# -*- coding: utf-8 -*-
"""
ManipBackTester - Симулятор позиции.

Симулирует торговлю по сигналу на исторических данных.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional, Tuple

from .models import (
    ParsedSignal, Kline, BacktestResult, PartialClose,
    Direction, ExitReason, BinanceFees
)
from .config import BacktestConfig


def make_naive(dt: datetime) -> datetime:
    """Convert datetime to naive (no timezone) for comparison."""
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


class PositionSimulator:
    """
    Симулятор позиции.

    Логика:
    1. Ждём пока цена войдёт в entry zone → открываем по limit цене
    2. Следим за ценой каждую минуту:
       - SL сработал → закрываем ВСЁ с убытком
       - TP1 сработал → закрываем 30% (portion из сигнала)
       - TP2 сработал → закрываем 40%
       - TP3 сработал → закрываем 30%
       - Timeout → закрываем остаток по рынку
    3. Учитываем комиссии и funding
    """

    def __init__(self, config: BacktestConfig = None):
        self.config = config or BacktestConfig()
        self.fees = BinanceFees(
            maker=self.config.maker_fee,
            taker=self.config.taker_fee,
            avg_funding_rate=self.config.avg_funding_rate
        )

    def simulate(
        self,
        signal: ParsedSignal,
        klines: List[Kline]
    ) -> BacktestResult:
        """
        Симулировать один сигнал.

        Args:
            signal: Торговый сигнал
            klines: Исторические свечи

        Returns:
            Результат бэктеста
        """
        result = BacktestResult(
            signal=signal,
            entry_filled=False,
            exit_reason=ExitReason.NOT_FILLED
        )

        # 1. Найти точку входа
        entry_kline, entry_price = self._find_entry(signal, klines)

        if entry_kline is None:
            return result

        result.entry_filled = True
        result.actual_entry_price = entry_price
        result.actual_entry_time = entry_kline.timestamp

        # 2. Проверить SL на свече входа!
        # Если на той же свече где мы вошли цена пробила SL - это убыток
        if self._check_stop_loss(signal, entry_kline):
            return self._immediate_stop_loss(signal, entry_kline, entry_price, result)

        # 3. Симулировать позицию начиная со СЛЕДУЮЩЕЙ свечи
        return self._simulate_position(signal, klines, entry_kline, entry_price, result)

    def _find_entry(
        self,
        signal: ParsedSignal,
        klines: List[Kline]
    ) -> Tuple[Optional[Kline], Optional[Decimal]]:
        """
        Найти момент входа в позицию.

        Вход происходит когда цена входит в entry_zone сигнала.
        """
        signal_time = make_naive(signal.timestamp)
        max_entry_time = signal_time + timedelta(hours=signal.max_hold_hours)

        for kline in klines:
            kline_time = make_naive(kline.timestamp)

            # Только после сигнала
            if kline_time < signal_time:
                continue

            # Timeout - нет смысла искать вход после max_hold_hours
            if kline_time > max_entry_time:
                return None, None

            # Проверить что цена дошла до entry zone
            if signal.direction == Direction.LONG:
                # LONG: цена должна опуститься до entry limit
                if kline.low <= signal.entry_limit:
                    return kline, signal.entry_limit
            else:
                # SHORT: цена должна подняться до entry limit
                if kline.high >= signal.entry_limit:
                    return kline, signal.entry_limit

        return None, None

    def _immediate_stop_loss(
        self,
        signal: ParsedSignal,
        entry_kline: Kline,
        entry_price: Decimal,
        result: BacktestResult
    ) -> BacktestResult:
        """
        Обработать случай когда SL сработал на свече входа.

        Это происходит когда цена на одной свече пробила и entry и SL.
        Консервативно считаем это убытком.
        """
        position_size = Decimal("1.0")
        entry_notional = entry_price * position_size

        # Комиссия за вход (maker)
        entry_fee = self.fees.entry_fee(entry_notional, is_limit=True)

        # Комиссия за выход по SL (taker)
        exit_fee = self.fees.exit_fee(signal.stop_loss * position_size, is_limit=False)

        # PnL
        gross_pnl = self._calc_pnl(signal.direction, entry_price, signal.stop_loss, position_size)

        result.exit_reason = ExitReason.STOP_LOSS
        result.final_exit_price = signal.stop_loss
        result.final_exit_time = entry_kline.timestamp
        result.sl_hit = True
        result.gross_pnl = gross_pnl
        result.total_fees = entry_fee + exit_fee
        result.total_funding = Decimal("0")
        result.net_pnl = gross_pnl - result.total_fees
        result.hold_time_hours = 0.0

        if entry_notional > 0:
            result.pnl_percent = (gross_pnl / entry_notional) * 100
            result.net_pnl_percent = (result.net_pnl / entry_notional) * 100

        result.partial_closes.append(PartialClose(
            timestamp=entry_kline.timestamp,
            price=signal.stop_loss,
            portion_pct=100,
            pnl=gross_pnl,
            fee=entry_fee + exit_fee,
            tp_label="SL"
        ))

        return result

    def _simulate_position(
        self,
        signal: ParsedSignal,
        klines: List[Kline],
        entry_kline: Kline,
        entry_price: Decimal,
        result: BacktestResult
    ) -> BacktestResult:
        """Симулировать открытую позицию."""

        position_size = Decimal("1.0")
        remaining_size = position_size

        # Комиссия за вход
        entry_notional = entry_price * position_size
        entry_fee = self.fees.entry_fee(entry_notional, is_limit=self.config.assume_limit_entry)
        result.total_fees = entry_fee

        # TP portions
        tp1_portion = Decimal(str(signal.tp1.portion)) / 100
        tp2_portion = Decimal(str(signal.tp2.portion)) / 100
        tp3_portion = Decimal(str(signal.tp3.portion)) / 100

        tp1_hit = tp2_hit = tp3_hit = False

        entry_time = make_naive(entry_kline.timestamp)
        max_time = make_naive(signal.timestamp) + timedelta(hours=signal.max_hold_hours)

        exit_kline = None
        exit_price = None
        exit_reason = None

        for kline in klines:
            kline_time = make_naive(kline.timestamp)

            # Пропустить свечи до и включая вход (entry candle уже проверена на SL)
            if kline_time <= entry_time:
                continue

            # Timeout
            if kline_time > max_time:
                exit_reason = ExitReason.TIMEOUT
                exit_price = kline.close
                exit_kline = kline

                if remaining_size > 0:
                    pnl = self._calc_pnl(signal.direction, entry_price, exit_price, remaining_size)
                    fee = self.fees.exit_fee(exit_price * remaining_size, is_limit=False)
                    result.total_fees += fee
                    result.gross_pnl += pnl
                    result.partial_closes.append(PartialClose(
                        timestamp=kline.timestamp,
                        price=exit_price,
                        portion_pct=int(remaining_size * 100),
                        pnl=pnl,
                        fee=fee,
                        tp_label="TIMEOUT"
                    ))
                break

            # Проверить SL (приоритет над TP!)
            if self._check_stop_loss(signal, kline):
                exit_reason = ExitReason.STOP_LOSS
                exit_price = signal.stop_loss
                exit_kline = kline
                result.sl_hit = True

                pnl = self._calc_pnl(signal.direction, entry_price, exit_price, remaining_size)
                fee = self.fees.exit_fee(exit_price * remaining_size, is_limit=False)
                result.total_fees += fee
                result.gross_pnl += pnl
                result.partial_closes.append(PartialClose(
                    timestamp=kline.timestamp,
                    price=exit_price,
                    portion_pct=int(remaining_size * 100),
                    pnl=pnl,
                    fee=fee,
                    tp_label="SL"
                ))
                break

            # TP1
            if not tp1_hit and signal.tp1.price > 0:
                if self._check_take_profit(signal, kline, signal.tp1.price):
                    tp1_hit = True
                    result.tp1_hit = True
                    close_size = position_size * tp1_portion

                    pnl = self._calc_pnl(signal.direction, entry_price, signal.tp1.price, close_size)
                    fee = self.fees.exit_fee(signal.tp1.price * close_size, is_limit=self.config.assume_limit_tp)
                    result.total_fees += fee
                    result.gross_pnl += pnl
                    remaining_size -= close_size

                    result.partial_closes.append(PartialClose(
                        timestamp=kline.timestamp,
                        price=signal.tp1.price,
                        portion_pct=signal.tp1.portion,
                        pnl=pnl,
                        fee=fee,
                        tp_label="TP1"
                    ))

            # TP2
            if not tp2_hit and signal.tp2.price > 0 and tp1_hit:
                if self._check_take_profit(signal, kline, signal.tp2.price):
                    tp2_hit = True
                    result.tp2_hit = True
                    close_size = position_size * tp2_portion

                    pnl = self._calc_pnl(signal.direction, entry_price, signal.tp2.price, close_size)
                    fee = self.fees.exit_fee(signal.tp2.price * close_size, is_limit=self.config.assume_limit_tp)
                    result.total_fees += fee
                    result.gross_pnl += pnl
                    remaining_size -= close_size

                    result.partial_closes.append(PartialClose(
                        timestamp=kline.timestamp,
                        price=signal.tp2.price,
                        portion_pct=signal.tp2.portion,
                        pnl=pnl,
                        fee=fee,
                        tp_label="TP2"
                    ))

            # TP3
            if not tp3_hit and signal.tp3.price > 0 and tp2_hit:
                if self._check_take_profit(signal, kline, signal.tp3.price):
                    tp3_hit = True
                    result.tp3_hit = True
                    exit_reason = ExitReason.TP3
                    exit_price = signal.tp3.price
                    exit_kline = kline

                    close_size = remaining_size
                    pnl = self._calc_pnl(signal.direction, entry_price, signal.tp3.price, close_size)
                    fee = self.fees.exit_fee(signal.tp3.price * close_size, is_limit=self.config.assume_limit_tp)
                    result.total_fees += fee
                    result.gross_pnl += pnl
                    remaining_size = Decimal("0")

                    result.partial_closes.append(PartialClose(
                        timestamp=kline.timestamp,
                        price=signal.tp3.price,
                        portion_pct=signal.tp3.portion,
                        pnl=pnl,
                        fee=fee,
                        tp_label="TP3"
                    ))
                    break

        # Определить exit_reason
        if exit_reason is None:
            if tp3_hit:
                exit_reason = ExitReason.TP3
            elif tp2_hit:
                exit_reason = ExitReason.PARTIAL_TP2
            elif tp1_hit:
                exit_reason = ExitReason.PARTIAL_TP1
            else:
                exit_reason = ExitReason.TIMEOUT

        result.exit_reason = exit_reason
        result.final_exit_price = exit_price
        result.final_exit_time = exit_kline.timestamp if exit_kline else None

        # Hold time
        if exit_kline:
            exit_time_naive = make_naive(exit_kline.timestamp)
            hold_delta = exit_time_naive - entry_time
            result.hold_time_hours = hold_delta.total_seconds() / 3600
        else:
            result.hold_time_hours = 0

        # Funding
        if result.hold_time_hours > 0:
            result.total_funding = self.fees.funding_cost(
                entry_notional,
                result.hold_time_hours,
                signal.direction
            )

        # Net PnL
        result.net_pnl = result.gross_pnl - result.total_fees - result.total_funding

        # Процентный PnL
        if entry_notional > 0:
            result.pnl_percent = (result.gross_pnl / entry_notional) * 100
            result.net_pnl_percent = (result.net_pnl / entry_notional) * 100

        return result

    def _calc_pnl(
        self,
        direction: Direction,
        entry: Decimal,
        exit: Decimal,
        size: Decimal
    ) -> Decimal:
        """Рассчитать PnL для части позиции."""
        if direction == Direction.LONG:
            return (exit - entry) * size
        else:
            return (entry - exit) * size

    def _check_stop_loss(self, signal: ParsedSignal, kline: Kline) -> bool:
        """Проверить сработал ли Stop Loss."""
        if signal.direction == Direction.LONG:
            # LONG: SL срабатывает когда цена падает до/ниже SL
            return kline.low <= signal.stop_loss
        else:
            # SHORT: SL срабатывает когда цена растёт до/выше SL
            return kline.high >= signal.stop_loss

    def _check_take_profit(
        self,
        signal: ParsedSignal,
        kline: Kline,
        tp_price: Decimal
    ) -> bool:
        """Проверить сработал ли Take Profit."""
        if signal.direction == Direction.LONG:
            # LONG: TP срабатывает когда цена растёт до/выше TP
            return kline.high >= tp_price
        else:
            # SHORT: TP срабатывает когда цена падает до/ниже TP
            return kline.low <= tp_price
