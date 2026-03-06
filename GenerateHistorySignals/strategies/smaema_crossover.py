# -*- coding: utf-8 -*-
"""Crossover detection for SMAEMA strategy.

Copied from G:\BinanceFriend\strategy\crossover.py (193 lines)
Detects Golden Cross (bullish) and Death Cross (bearish) patterns.
"""

from decimal import Decimal
from enum import Enum
from typing import Union

# Type alias for numeric values (supports both Decimal and float modes)
Numeric = Union[Decimal, float]


class CrossoverType(str, Enum):
    """Type of MA crossover."""

    BULLISH = "BULLISH"   # Fast MA crosses slow MA from below (Golden Cross)
    BEARISH = "BEARISH"   # Fast MA crosses slow MA from above (Death Cross)
    NONE = "NONE"         # No crossover


class CrossoverDetector:
    """Detects crossovers between two moving averages.

    Rules (per REQ-032):
    - Crossover occurs when fast MA crosses slow MA
    - If values are EQUAL, this is NOT a crossover
    - Signal candle = candle where crossover occurred
    - If crossover between two candles, signal candle = newer one
    """

    def __init__(self) -> None:
        """Initialize crossover detector."""
        self._prev_fast: Numeric | None = None
        self._prev_slow: Numeric | None = None

    @property
    def has_previous(self) -> bool:
        """Check if we have previous values stored."""
        return self._prev_fast is not None and self._prev_slow is not None

    @staticmethod
    def detect(
        fast_ma_prev: Numeric,
        fast_ma_curr: Numeric,
        slow_ma_prev: Numeric,
        slow_ma_curr: Numeric,
    ) -> CrossoverType:
        """Detect crossover between two candles.

        Args:
            fast_ma_prev: Fast MA value on previous candle.
            fast_ma_curr: Fast MA value on current candle.
            slow_ma_prev: Slow MA value on previous candle.
            slow_ma_curr: Slow MA value on current candle.

        Returns:
            CrossoverType indicating the type of crossover.

        Note:
            If current values are equal (fast == slow), returns NONE.
            This is per REQ-032: equal values are NOT a crossover.
        """
        # If current values are equal, no crossover (REQ-032)
        if fast_ma_curr == slow_ma_curr:
            return CrossoverType.NONE

        # Calculate previous and current differences
        prev_diff = fast_ma_prev - slow_ma_prev
        curr_diff = fast_ma_curr - slow_ma_curr

        # BULLISH: fast was below slow, now fast is above slow
        # prev_diff < 0 and curr_diff > 0 (strict: prev_diff==0 is NOT a crossover)
        if prev_diff < 0 and curr_diff > 0:
            return CrossoverType.BULLISH

        # BEARISH: fast was above slow, now fast is below slow
        # prev_diff > 0 and curr_diff < 0 (strict: prev_diff==0 is NOT a crossover)
        if prev_diff > 0 and curr_diff < 0:
            return CrossoverType.BEARISH

        return CrossoverType.NONE

    def update(
        self,
        fast_ma: Numeric,
        slow_ma: Numeric,
    ) -> CrossoverType:
        """Update with new MA values and detect crossover.

        Stores values for next comparison.

        Args:
            fast_ma: Current fast MA value.
            slow_ma: Current slow MA value.

        Returns:
            CrossoverType if crossover occurred, NONE otherwise.
        """
        result = CrossoverType.NONE

        # Detect crossover if we have previous values
        if self._prev_fast is not None and self._prev_slow is not None:
            result = self.detect(
                self._prev_fast,
                fast_ma,
                self._prev_slow,
                slow_ma,
            )

        # Store current values for next update
        self._prev_fast = fast_ma
        self._prev_slow = slow_ma

        return result

    def reset(self) -> None:
        """Reset stored values."""
        self._prev_fast = None
        self._prev_slow = None

    def set_previous(self, fast_ma: Numeric, slow_ma: Numeric) -> None:
        """Set previous MA values (for initialization).

        Args:
            fast_ma: Previous fast MA value.
            slow_ma: Previous slow MA value.
        """
        self._prev_fast = fast_ma
        self._prev_slow = slow_ma


def is_golden_cross(
    fast_ma_prev: Numeric,
    fast_ma_curr: Numeric,
    slow_ma_prev: Numeric,
    slow_ma_curr: Numeric,
) -> bool:
    """Check if a golden cross (bullish crossover) occurred.

    Golden Cross: Fast MA crosses above Slow MA.

    Args:
        fast_ma_prev: Fast MA on previous candle.
        fast_ma_curr: Fast MA on current candle.
        slow_ma_prev: Slow MA on previous candle.
        slow_ma_curr: Slow MA on current candle.

    Returns:
        True if golden cross occurred.
    """
    return CrossoverDetector.detect(
        fast_ma_prev, fast_ma_curr, slow_ma_prev, slow_ma_curr
    ) == CrossoverType.BULLISH


def is_death_cross(
    fast_ma_prev: Numeric,
    fast_ma_curr: Numeric,
    slow_ma_prev: Numeric,
    slow_ma_curr: Numeric,
) -> bool:
    """Check if a death cross (bearish crossover) occurred.

    Death Cross: Fast MA crosses below Slow MA.

    Args:
        fast_ma_prev: Fast MA on previous candle.
        fast_ma_curr: Fast MA on current candle.
        slow_ma_prev: Slow MA on previous candle.
        slow_ma_curr: Slow MA on current candle.

    Returns:
        True if death cross occurred.
    """
    return CrossoverDetector.detect(
        fast_ma_prev, fast_ma_curr, slow_ma_prev, slow_ma_curr
    ) == CrossoverType.BEARISH
