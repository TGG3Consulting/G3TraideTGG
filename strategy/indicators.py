"""Technical indicators for strategy calculations.

SMA - Simple Moving Average
EMA - Exponential Moving Average

Supports two arithmetic modes:
- Decimal mode (default): Uses Decimal with quantize to 8 decimal places
- Tester mode: Uses numpy.float32 (32-bit) matching C++ tester behavior exactly
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence, Union

import numpy as np

from src.core.logging import get_logger

logger = get_logger(__name__)

# Type alias for MA values - can be Decimal or float depending on mode
MAValue = Union[Decimal, float, None]


class SMA:
    """Simple Moving Average calculator.

    SMA = Sum(prices) / period

    Supports both batch calculation and incremental updates.
    Supports tester_arithmetic mode for float-based calculation.
    """

    def __init__(self, period: int, tester_arithmetic: bool = False) -> None:
        """Initialize SMA calculator.

        Args:
            period: Number of periods for averaging.
            tester_arithmetic: If True, use float arithmetic without rounding.

        Raises:
            ValueError: If period < 1.
        """
        if period < 1:
            raise ValueError(f"Period must be >= 1, got {period}")
        self._period = period
        self._tester_arithmetic = tester_arithmetic

        # Storage types depend on mode
        if tester_arithmetic:
            self._values: list = []  # list[np.float32]
            self._current_sum: np.float32 = np.float32(0.0)
            self._current_value: np.float32 | None = None
        else:
            self._values: list = []  # list[Decimal]
            self._current_sum: Decimal = Decimal("0")
            self._current_value: Decimal | None = None

    @property
    def period(self) -> int:
        """Get the period."""
        return self._period

    @property
    def value(self) -> MAValue:
        """Get current SMA value."""
        return self._current_value

    @property
    def is_ready(self) -> bool:
        """Check if enough data for calculation."""
        return len(self._values) >= self._period

    @staticmethod
    def calculate(
        prices: Sequence[Decimal | float | str],
        period: int,
        tester_arithmetic: bool = False,
    ) -> MAValue:
        """Calculate SMA from price sequence.

        Args:
            prices: Sequence of prices (must have at least 'period' values).
            period: Number of periods for averaging.
            tester_arithmetic: If True, use float arithmetic without rounding.

        Returns:
            SMA value or None if not enough data.
        """
        if len(prices) < period:
            return None

        # Use last 'period' prices
        recent_prices = prices[-period:]

        if tester_arithmetic:
            # Float32 mode: matches C++ tester float type exactly
            total = np.float32(0.0)
            for p in recent_prices:
                total = np.float32(total + np.float32(p))
            return np.float32(total / np.float32(period))
        else:
            # Decimal mode: quantize to 8 decimal places
            total = sum(Decimal(str(p)) for p in recent_prices)
            return (total / period).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)

    def update(self, price: Decimal | float | str) -> MAValue:
        """Update SMA with new price (incremental calculation).

        Args:
            price: New price value.

        Returns:
            Updated SMA value or None if not enough data.
        """
        if self._tester_arithmetic:
            # Float32 mode (matches C++ tester float type exactly)
            price_f32 = np.float32(price)
            self._values.append(price_f32)
            self._current_sum = np.float32(self._current_sum + price_f32)

            # Remove oldest if we have more than period
            if len(self._values) > self._period:
                old_price = self._values.pop(0)
                self._current_sum = np.float32(self._current_sum - old_price)

            # Calculate SMA if we have enough values
            if len(self._values) >= self._period:
                self._current_value = np.float32(
                    self._current_sum / np.float32(self._period)
                )
            else:
                self._current_value = None
        else:
            # Decimal mode (original behavior)
            price_decimal = Decimal(str(price))
            self._values.append(price_decimal)
            self._current_sum += price_decimal

            # Remove oldest if we have more than period
            if len(self._values) > self._period:
                old_price = self._values.pop(0)
                self._current_sum -= old_price

            # Calculate SMA if we have enough values
            if len(self._values) >= self._period:
                self._current_value = (self._current_sum / self._period).quantize(
                    Decimal("0.00000001"), rounding=ROUND_HALF_UP
                )
            else:
                self._current_value = None

        return self._current_value

    def reset(self) -> None:
        """Reset calculator state."""
        self._values.clear()
        if self._tester_arithmetic:
            self._current_sum = np.float32(0.0)
            self._current_value = None
        else:
            self._current_sum = Decimal("0")
            self._current_value = None


class EMA:
    """Exponential Moving Average calculator.

    EMA = Price * multiplier + EMA_prev * (1 - multiplier)
    multiplier = 2 / (period + 1)

    Supports both batch calculation and incremental updates.
    Supports tester_arithmetic mode for float-based calculation.
    """

    def __init__(self, period: int, tester_arithmetic: bool = False) -> None:
        """Initialize EMA calculator.

        Args:
            period: Number of periods for averaging.
            tester_arithmetic: If True, use float arithmetic without rounding.

        Raises:
            ValueError: If period < 1.
        """
        if period < 1:
            raise ValueError(f"Period must be >= 1, got {period}")
        self._period = period
        self._tester_arithmetic = tester_arithmetic

        if tester_arithmetic:
            # Float32 mode: matches C++ tester exactly (ma.cpp:66)
            self._multiplier: np.float32 = np.float32(2.0) / np.float32(period + 1)
            self._values: list = []  # list[np.float32]
            self._current_value: np.float32 | None = None
            self._init_prices: list = []  # buffer for SMA initialization
        else:
            self._multiplier: Decimal = Decimal("2") / (Decimal(str(period)) + Decimal("1"))
            self._values: list = []  # list[Decimal]
            self._current_value: Decimal | None = None

        self._initialized = False

    @property
    def period(self) -> int:
        """Get the period."""
        return self._period

    @property
    def multiplier(self) -> Decimal | float:
        """Get the EMA multiplier."""
        return self._multiplier

    @property
    def value(self) -> MAValue:
        """Get current EMA value."""
        return self._current_value

    @property
    def is_ready(self) -> bool:
        """Check if enough data for calculation."""
        return self._initialized and self._current_value is not None

    @staticmethod
    def calculate(
        prices: Sequence[Decimal | float | str],
        period: int,
        tester_arithmetic: bool = False,
    ) -> MAValue:
        """Calculate EMA from price sequence.

        EMA0 = prices[0], then iterate over prices[1:].
        Formula: EMA = price * α + EMA_prev * (1 - α), where α = 2/(period+1)

        Args:
            prices: Sequence of prices (must have at least 1 value).
            period: Number of periods for averaging.
            tester_arithmetic: If True, use float arithmetic without rounding.

        Returns:
            EMA value or None if not enough data.
        """
        if len(prices) < 1:
            return None

        if tester_arithmetic:
            # Float32 mode: matches C++ tester exactly (ma.cpp:50-73)
            if len(prices) < period:
                return None  # Need at least 'period' prices

            f32_prices = [np.float32(p) for p in prices]
            multiplier = np.float32(2.0) / np.float32(period + 1)

            # EMA[0] = SMA of first 'period' prices (ma.cpp:58-63)
            sma_sum = np.float32(0.0)
            for i in range(period):
                sma_sum = np.float32(sma_sum + f32_prices[i])
            ema = np.float32(sma_sum / np.float32(period))

            # Apply EMA formula to remaining prices (ma.cpp:68-72)
            for price in f32_prices[period:]:
                # Formula: (close - ema) * alpha + ema (ma.cpp:70)
                ema = np.float32(
                    np.float32(price - ema) * multiplier + ema
                )

            return ema
        else:
            # Decimal mode
            decimal_prices = [Decimal(str(p)) for p in prices]
            multiplier = Decimal("2") / (Decimal(str(period)) + Decimal("1"))

            # First EMA is first price
            ema = decimal_prices[0]

            # Apply EMA formula to remaining prices
            for price in decimal_prices[1:]:
                ema = price * multiplier + ema * (Decimal("1") - multiplier)

            return ema.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)

    def update(self, price: Decimal | float | str) -> MAValue:
        """Update EMA with new price (incremental calculation).

        First call initializes EMA0 = price.
        Subsequent calls use EMA formula: EMA = price * α + EMA_prev * (1 - α)

        Args:
            price: New price value.

        Returns:
            Updated EMA value.
        """
        if self._tester_arithmetic:
            # Float32 mode: matches C++ tester exactly (ma.cpp:50-73)
            price_f32 = np.float32(price)
            self._values.append(price_f32)

            if not self._initialized:
                # Accumulate first 'period' prices for SMA initialization (ma.cpp:58-63)
                self._init_prices.append(price_f32)
                if len(self._init_prices) < self._period:
                    # Not enough prices yet
                    self._current_value = None
                    return self._current_value
                else:
                    # EMA[0] = SMA of first period prices (exactly like ma.cpp:58-63)
                    sma_sum = np.float32(0.0)
                    for p in self._init_prices:
                        sma_sum = np.float32(sma_sum + p)
                    self._current_value = np.float32(sma_sum / np.float32(self._period))
                    self._initialized = True
            else:
                # EMA update: (close - ema) * alpha + ema (exactly like ma.cpp:70)
                self._current_value = np.float32(
                    np.float32(price_f32 - self._current_value) * self._multiplier
                    + self._current_value
                )
        else:
            # Decimal mode (original behavior)
            price_decimal = Decimal(str(price))
            self._values.append(price_decimal)

            if not self._initialized:
                # Initialize EMA with first price
                self._current_value = price_decimal.quantize(
                    Decimal("0.00000001"), rounding=ROUND_HALF_UP
                )
                self._initialized = True
            else:
                # Normal EMA update
                self._current_value = (
                    price_decimal * self._multiplier +
                    self._current_value * (Decimal("1") - self._multiplier)
                ).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)

        return self._current_value

    def reset(self) -> None:
        """Reset calculator state."""
        self._values.clear()
        self._current_value = None
        self._initialized = False
        if self._tester_arithmetic:
            self._init_prices.clear()


def calculate_ma(
    prices: Sequence[Decimal | float | str],
    period: int,
    ma_type: str = "sma",
    tester_arithmetic: bool = False,
) -> MAValue:
    """Calculate moving average.

    Args:
        prices: Sequence of prices.
        period: MA period.
        ma_type: "sma" or "ema".
        tester_arithmetic: If True, use float arithmetic without rounding.

    Returns:
        MA value or None if not enough data.

    Raises:
        ValueError: If invalid ma_type.
    """
    ma_type = ma_type.lower()
    if ma_type == "sma":
        return SMA.calculate(prices, period, tester_arithmetic)
    elif ma_type == "ema":
        return EMA.calculate(prices, period, tester_arithmetic)
    else:
        raise ValueError(f"Invalid ma_type: {ma_type}. Use 'sma' or 'ema'.")
