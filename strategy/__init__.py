"""Strategy module: moving average calculations and signal generation.

This module provides:
- Technical indicators (SMA, EMA)
- Crossover detection
- Trading signal generation

Example usage:
    from src.strategy import SMA, EMA, CrossoverDetector, SignalGenerator

    # Calculate SMA
    sma = SMA.calculate(prices, period=20)

    # Calculate EMA
    ema = EMA.calculate(prices, period=20)

    # Detect crossover
    crossover = CrossoverDetector.detect(
        fast_prev, fast_curr, slow_prev, slow_curr
    )

    # Generate signals
    generator = SignalGenerator(bot_config)
    signal = generator.on_candle_close(candle)
"""

from src.strategy.crossover import (
    CrossoverDetector,
    CrossoverType,
    is_death_cross,
    is_golden_cross,
)
from src.strategy.indicators import EMA, SMA, calculate_ma
from src.strategy.signals import SignalGenerator

__all__ = [
    # Indicators
    "SMA",
    "EMA",
    "calculate_ma",
    # Crossover
    "CrossoverDetector",
    "CrossoverType",
    "is_golden_cross",
    "is_death_cross",
    # Signals
    "SignalGenerator",
]
