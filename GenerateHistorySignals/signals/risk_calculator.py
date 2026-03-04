# -*- coding: utf-8 -*-
"""
Калькулятор риска для торговых сигналов.
Адаптированная версия для исторической генерации.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import List, Optional

from .models import TakeProfit, SignalDirection, SignalConfig
from state_builder import SymbolState, FuturesState


@dataclass
class RiskLevels:
    """Рассчитанные уровни риска."""
    entry_zone_low: Decimal
    entry_zone_high: Decimal
    entry_limit: Decimal
    stop_loss: Decimal
    stop_loss_pct: float
    take_profits: List[TakeProfit]
    risk_reward_ratio: float


class RiskCalculator:
    """
    Калькулятор уровней риска.
    Адаптирован для исторического анализа.
    """

    MIN_WALL_VOLUME_USD = 50000

    def __init__(self, config: SignalConfig = None):
        self.config = config or SignalConfig()

    def calculate(
        self,
        symbol: str,
        direction: SignalDirection,
        current_price: Decimal,
        spot_state: Optional[SymbolState] = None,
        futures_state: Optional[FuturesState] = None,
        valid_hours: int = 4,
        accumulation_score: int = 70,
    ) -> RiskLevels:
        """Calculate risk levels for a signal."""
        volatility_pct = self._estimate_volatility(spot_state, futures_state, valid_hours=valid_hours)

        entry_zone_low, entry_zone_high, entry_limit = self._calculate_entry_zone(
            direction, current_price, volatility_pct, spot_state, futures_state
        )

        stop_loss, sl_pct = self._calculate_stop_loss(
            direction, entry_limit, volatility_pct, spot_state, futures_state,
            valid_hours=valid_hours
        )

        take_profits = self._calculate_take_profits(
            direction, entry_limit, stop_loss,
            accumulation_score=accumulation_score,
        )

        risk_reward = self._calculate_risk_reward(
            direction, entry_limit, stop_loss, take_profits
        )

        return RiskLevels(
            entry_zone_low=entry_zone_low,
            entry_zone_high=entry_zone_high,
            entry_limit=entry_limit,
            stop_loss=stop_loss,
            stop_loss_pct=sl_pct,
            take_profits=take_profits,
            risk_reward_ratio=risk_reward,
        )

    def _estimate_volatility(
        self,
        spot_state: Optional[SymbolState],
        futures_state: Optional[FuturesState],
        valid_hours: int = 4,
    ) -> float:
        """Estimate asset volatility."""
        volatility = 5.0

        # PRIORITY 0: Daily ATR for signals >= 4h
        if valid_hours >= 4:
            if spot_state and getattr(spot_state, 'atr_daily_is_real', False):
                daily_atr = float(spot_state.atr_daily_pct)
                if daily_atr > 0:
                    return max(0.5, min(20.0, daily_atr))

        # PRIORITY 1: RAW ATR from spot_state
        if spot_state and getattr(spot_state, 'atr_is_real', False):
            raw_atr = float(getattr(spot_state, 'atr_1h_pct_raw', 0))
            if raw_atr > 0:
                volatility = raw_atr
            else:
                volatility = float(spot_state.atr_1h_pct)

        # PRIORITY 2: RAW ATR from futures_state
        elif futures_state and getattr(futures_state, 'futures_atr_is_real', False):
            raw_atr = float(getattr(futures_state, 'futures_atr_1h_pct_raw', 0))
            if raw_atr > 0:
                volatility = raw_atr
            else:
                volatility = float(futures_state.futures_atr_1h_pct)

        # FALLBACK: historical volatility
        elif spot_state and spot_state.price_history and len(spot_state.price_history) >= 10:
            prices = [float(p) for p in spot_state.price_history[-60:]]
            if prices:
                high = max(prices)
                low = min(prices)
                avg = sum(prices) / len(prices)
                if avg > 0:
                    volatility = (high - low) / avg * 100

        # OI change adjustment
        if futures_state:
            oi_change = abs(float(futures_state.oi_change_1h_pct))
            if oi_change > 10:
                volatility *= 1.2

        return max(0.5, min(20.0, volatility))

    def _calculate_entry_zone(
        self,
        direction: SignalDirection,
        current_price: Decimal,
        volatility_pct: float,
        spot_state: Optional[SymbolState],
        futures_state: Optional[FuturesState] = None
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Calculate entry zone with orderbook."""
        zone_pct = Decimal(str(min(2.0, volatility_pct / 3)))

        entry_limit = current_price
        half_zone = zone_pct / 200
        entry_zone_low = entry_limit * (1 - half_zone)
        entry_zone_high = entry_limit * (1 + half_zone)

        # ORDERBOOK ADJUSTMENTS
        spot_imbalance: Optional[Decimal] = None
        futures_imbalance: Optional[Decimal] = None

        if spot_state:
            spot_imbalance = spot_state.book_imbalance_atr

        if futures_state:
            futures_imbalance = futures_state.futures_book_imbalance_atr

        if spot_imbalance is None and futures_imbalance is None:
            combined_imbalance = Decimal("0")
        elif spot_imbalance is not None and futures_imbalance is None:
            combined_imbalance = spot_imbalance
        elif spot_imbalance is None and futures_imbalance is not None:
            combined_imbalance = futures_imbalance
        else:
            combined_imbalance = (spot_imbalance + futures_imbalance) / 2

        if direction == SignalDirection.LONG:
            if combined_imbalance > Decimal("0.4"):
                entry_limit = current_price
            elif combined_imbalance > Decimal("0.2"):
                entry_limit = entry_zone_low + (entry_zone_high - entry_zone_low) * Decimal("0.5")
        else:
            if combined_imbalance < Decimal("-0.4"):
                entry_limit = current_price
            elif combined_imbalance < Decimal("-0.2"):
                entry_limit = entry_zone_high - (entry_zone_high - entry_zone_low) * Decimal("0.5")

        return (
            self._round_price(entry_zone_low),
            self._round_price(entry_zone_high),
            self._round_price(entry_limit)
        )

    def _calculate_stop_loss(
        self,
        direction: SignalDirection,
        entry_price: Decimal,
        volatility_pct: float,
        spot_state: Optional[SymbolState],
        futures_state: Optional[FuturesState] = None,
        valid_hours: int = 4,
    ) -> tuple[Decimal, float]:
        """Calculate stop loss for DAILY timeframe."""
        # Дневной таймфрейм: SL 7-12% (дневная волатильность альткоинов)
        atr_sl = volatility_pct * 1.5
        sl_pct = min(12.0, max(7.0, atr_sl))  # 7-12% для дневных сделок
        sl_decimal = Decimal(str(sl_pct / 100))

        if direction == SignalDirection.LONG:
            stop_loss = entry_price * (1 - sl_decimal)
        else:
            stop_loss = entry_price * (1 + sl_decimal)

        return self._round_price(stop_loss), round(sl_pct, 1)

    def _calculate_take_profits(
        self,
        direction: SignalDirection,
        entry_price: Decimal,
        stop_loss: Decimal,
        accumulation_score: int = 70,
    ) -> List[TakeProfit]:
        """Calculate take profit levels."""
        risk = abs(entry_price - stop_loss)

        # TP multiplier based on score (оригинальная формула)
        # Высокий score = более агрессивные TP цели
        score_clamped = max(65, min(100, accumulation_score))
        tp_multiplier = 1.0 + (score_clamped - 65) / 35 * 0.5  # 1.0 to 1.5 range
        tp_multiplier = round(tp_multiplier, 2)

        if risk == 0:
            risk = entry_price * Decimal("0.05")

        take_profits = []

        # TP1: 1.2x risk - быстрая фиксация для дневного таймфрейма (~8-14%)
        tp1_distance = risk * Decimal(str(round(1.2 * tp_multiplier, 2)))
        if direction == SignalDirection.LONG:
            tp1_price = entry_price + tp1_distance
        else:
            tp1_price = entry_price - tp1_distance
        tp1_pct = float((tp1_price - entry_price) / entry_price * 100)
        take_profits.append(TakeProfit(
            price=self._round_price(tp1_price),
            percent=round(abs(tp1_pct), 1),
            portion=35,
            label="TP1"
        ))

        # TP2: 2.0x risk - основная цель для дневного таймфрейма (~14-24%)
        tp2_distance = risk * Decimal(str(round(2.0 * tp_multiplier, 2)))
        if direction == SignalDirection.LONG:
            tp2_price = entry_price + tp2_distance
        else:
            tp2_price = entry_price - tp2_distance
        tp2_pct = float((tp2_price - entry_price) / entry_price * 100)
        take_profits.append(TakeProfit(
            price=self._round_price(tp2_price),
            percent=round(abs(tp2_pct), 1),
            portion=40,
            label="TP2"
        ))

        # TP3: 3.0x risk - амбициозная цель для дневного таймфрейма (~21-36%)
        tp3_distance = risk * Decimal(str(round(3.0 * tp_multiplier, 2)))
        if direction == SignalDirection.LONG:
            tp3_price = entry_price + tp3_distance
        else:
            tp3_price = entry_price - tp3_distance
        tp3_pct = float((tp3_price - entry_price) / entry_price * 100)
        take_profits.append(TakeProfit(
            price=self._round_price(tp3_price),
            percent=round(abs(tp3_pct), 1),
            portion=25,
            label="TP3"
        ))

        return take_profits

    def _calculate_risk_reward(
        self,
        direction: SignalDirection,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profits: List[TakeProfit]
    ) -> float:
        """Calculate risk:reward ratio."""
        if not take_profits:
            return 0.0

        risk = abs(float(entry_price - stop_loss))
        if risk == 0:
            return 0.0

        weighted_reward = sum(
            float(abs(tp.price - entry_price)) * tp.portion / 100
            for tp in take_profits
        )

        return round(weighted_reward / risk, 2)

    def _round_price(self, price: Decimal) -> Decimal:
        """Round price to reasonable precision."""
        if price <= 0:
            return Decimal("0")
        if price >= 10000:
            return price.quantize(Decimal("1"), rounding=ROUND_DOWN)
        elif price >= 1000:
            return price.quantize(Decimal("0.1"), rounding=ROUND_DOWN)
        elif price >= 100:
            return price.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        elif price >= 1:
            return price.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        else:
            return price.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
