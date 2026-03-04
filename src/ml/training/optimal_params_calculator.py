# -*- coding: utf-8 -*-
"""
Optimal Parameters Calculator.

Calculates OPTIMAL SL/TP/Lifetime from REAL price history AFTER entry.

For each trade:
1. Look at klines AFTER entry
2. Find max_profit and max_drawdown BEFORE max_profit
3. Calculate optimal parameters that would make trade profitable
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import numpy as np
import structlog

from backtester.models import Kline


logger = structlog.get_logger(__name__)


@dataclass
class OptimalParams:
    """Optimal parameters calculated from real price history."""

    # Optimal parameters (what SHOULD have been used)
    optimal_sl_pct: float
    optimal_tp_pct: float
    optimal_lifetime_hours: float

    # Raw metrics from price history
    max_profit_pct: float
    max_drawdown_pct: float
    time_to_max_profit_minutes: int

    # Would trade be profitable with optimal params?
    would_be_profitable: bool

    # Quality of the opportunity
    risk_reward_optimal: float  # optimal_tp / optimal_sl


class OptimalParamsCalculator:
    """
    Calculates optimal SL/TP/Lifetime from price history AFTER entry.

    IMPORTANT: This looks at what ACTUALLY happened, not predictions!
    """

    def __init__(
        self,
        sl_buffer: float = 1.2,      # 20% buffer on SL
        tp_discount: float = 0.8,    # Take 80% of max profit
        lifetime_buffer: float = 1.5, # 50% buffer on time
        min_profit_threshold: float = 0.5,  # Minimum 0.5% profit to consider
        max_lookback_hours: int = 48,
    ):
        """
        Initialize calculator.

        Args:
            sl_buffer: Multiply max_drawdown by this for SL (>1 = wider SL)
            tp_discount: Multiply max_profit by this for TP (<1 = closer TP)
            lifetime_buffer: Multiply time_to_profit by this (>1 = more time)
            min_profit_threshold: Minimum profit % to consider opportunity valid
            max_lookback_hours: Maximum hours to look after entry
        """
        self._sl_buffer = sl_buffer
        self._tp_discount = tp_discount
        self._lifetime_buffer = lifetime_buffer
        self._min_profit_threshold = min_profit_threshold
        self._max_lookback_hours = max_lookback_hours

        logger.info(
            "optimal_params_calculator_init",
            sl_buffer=sl_buffer,
            tp_discount=tp_discount,
            lifetime_buffer=lifetime_buffer,
        )

    def calculate(
        self,
        entry_price: float,
        direction: str,
        klines_after_entry: List[Kline],
    ) -> Optional[OptimalParams]:
        """
        Calculate optimal parameters from price history AFTER entry.

        Args:
            entry_price: Entry price of the trade
            direction: "LONG" or "SHORT"
            klines_after_entry: Klines AFTER entry time (1m or 5m)

        Returns:
            OptimalParams or None if not enough data
        """
        if not klines_after_entry:
            return None

        # Limit to max lookback
        max_klines = self._max_lookback_hours * 60  # Assuming 1m klines
        klines = klines_after_entry[:max_klines]

        if len(klines) < 10:
            return None

        # Extract prices
        highs = [float(k.high) for k in klines]
        lows = [float(k.low) for k in klines]

        if direction.upper() == "LONG":
            return self._calculate_long(entry_price, highs, lows)
        else:
            return self._calculate_short(entry_price, highs, lows)

    def _calculate_long(
        self,
        entry_price: float,
        highs: List[float],
        lows: List[float],
    ) -> OptimalParams:
        """Calculate optimal params for LONG position."""

        # Find maximum profit point (highest high)
        max_profit_price = max(highs)
        max_profit_idx = highs.index(max_profit_price)

        # Find max drawdown BEFORE max profit
        lows_before_max = lows[:max_profit_idx + 1]
        max_drawdown_price = min(lows_before_max) if lows_before_max else entry_price

        # Calculate percentages
        max_profit_pct = (max_profit_price - entry_price) / entry_price * 100
        max_drawdown_pct = (entry_price - max_drawdown_price) / entry_price * 100

        # Ensure non-negative
        max_profit_pct = max(0, max_profit_pct)
        max_drawdown_pct = max(0, max_drawdown_pct)

        # Calculate optimal parameters
        optimal_sl_pct = max_drawdown_pct * self._sl_buffer
        optimal_tp_pct = max_profit_pct * self._tp_discount

        # Minimum values
        optimal_sl_pct = max(0.5, optimal_sl_pct)  # At least 0.5% SL
        optimal_tp_pct = max(0.3, optimal_tp_pct)  # At least 0.3% TP

        # Time to max profit (in minutes, assuming 1m klines)
        time_to_max_profit = max_profit_idx

        # Optimal lifetime
        optimal_lifetime_hours = (time_to_max_profit / 60) * self._lifetime_buffer
        optimal_lifetime_hours = max(1, optimal_lifetime_hours)  # At least 1 hour

        # Would be profitable?
        # Need TP > SL and actual profit > threshold
        would_be_profitable = (
            optimal_tp_pct > optimal_sl_pct and
            max_profit_pct > self._min_profit_threshold
        )

        # Risk/Reward ratio
        risk_reward = optimal_tp_pct / optimal_sl_pct if optimal_sl_pct > 0 else 0

        return OptimalParams(
            optimal_sl_pct=round(optimal_sl_pct, 4),
            optimal_tp_pct=round(optimal_tp_pct, 4),
            optimal_lifetime_hours=round(optimal_lifetime_hours, 2),
            max_profit_pct=round(max_profit_pct, 4),
            max_drawdown_pct=round(max_drawdown_pct, 4),
            time_to_max_profit_minutes=time_to_max_profit,
            would_be_profitable=would_be_profitable,
            risk_reward_optimal=round(risk_reward, 2),
        )

    def _calculate_short(
        self,
        entry_price: float,
        highs: List[float],
        lows: List[float],
    ) -> OptimalParams:
        """Calculate optimal params for SHORT position."""

        # For SHORT: profit = price going DOWN, drawdown = price going UP
        max_profit_price = min(lows)
        max_profit_idx = lows.index(max_profit_price)

        # Find max drawdown (highest high) BEFORE max profit
        highs_before_max = highs[:max_profit_idx + 1]
        max_drawdown_price = max(highs_before_max) if highs_before_max else entry_price

        # Calculate percentages
        max_profit_pct = (entry_price - max_profit_price) / entry_price * 100
        max_drawdown_pct = (max_drawdown_price - entry_price) / entry_price * 100

        # Ensure non-negative
        max_profit_pct = max(0, max_profit_pct)
        max_drawdown_pct = max(0, max_drawdown_pct)

        # Calculate optimal parameters
        optimal_sl_pct = max_drawdown_pct * self._sl_buffer
        optimal_tp_pct = max_profit_pct * self._tp_discount

        # Minimum values
        optimal_sl_pct = max(0.5, optimal_sl_pct)
        optimal_tp_pct = max(0.3, optimal_tp_pct)

        # Time to max profit
        time_to_max_profit = max_profit_idx

        # Optimal lifetime
        optimal_lifetime_hours = (time_to_max_profit / 60) * self._lifetime_buffer
        optimal_lifetime_hours = max(1, optimal_lifetime_hours)

        # Would be profitable?
        would_be_profitable = (
            optimal_tp_pct > optimal_sl_pct and
            max_profit_pct > self._min_profit_threshold
        )

        # Risk/Reward ratio
        risk_reward = optimal_tp_pct / optimal_sl_pct if optimal_sl_pct > 0 else 0

        return OptimalParams(
            optimal_sl_pct=round(optimal_sl_pct, 4),
            optimal_tp_pct=round(optimal_tp_pct, 4),
            optimal_lifetime_hours=round(optimal_lifetime_hours, 2),
            max_profit_pct=round(max_profit_pct, 4),
            max_drawdown_pct=round(max_drawdown_pct, 4),
            time_to_max_profit_minutes=time_to_max_profit,
            would_be_profitable=would_be_profitable,
            risk_reward_optimal=round(risk_reward, 2),
        )
