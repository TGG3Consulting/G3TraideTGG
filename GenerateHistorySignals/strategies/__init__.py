# -*- coding: utf-8 -*-
"""
Strategies Module

Provides trading strategies for signal generation.

Usage:
    from strategies import get_strategy, list_strategies

    # List all available strategies
    for name, desc in list_strategies():
        print(f"{name}: {desc}")

    # Get a strategy by name
    strategy = get_strategy("ls_fade")

    # Or with custom config
    from strategies import LSFadeStrategy, StrategyConfig
    config = StrategyConfig(sl_pct=4.0, tp_pct=10.0, params={"ls_extreme": 0.65})
    strategy = LSFadeStrategy(config)

    # Generate signals
    signals = strategy.generate_signals(data)
"""

from typing import Dict, Type, List, Tuple, Optional

# Base classes
from .base import (
    BaseStrategy,
    StrategyConfig,
    StrategyData,
    Signal,
    DailyCandle,
)

# Strategy implementations
from .ls_fade import LSFadeStrategy
from .momentum import MomentumStrategy
from .reversal import ReversalStrategy
from .momentum_ls import MomentumLSStrategy
from .mean_reversion import MeanReversionStrategy


# Registry of all available strategies
STRATEGY_REGISTRY: Dict[str, Type[BaseStrategy]] = {
    "ls_fade": LSFadeStrategy,
    "momentum": MomentumStrategy,
    "reversal": ReversalStrategy,
    "momentum_ls": MomentumLSStrategy,
    "mean_reversion": MeanReversionStrategy,
}


def list_strategies() -> List[Tuple[str, str]]:
    """
    List all available strategies.

    Returns:
        List of (name, description) tuples
    """
    return [(name, cls.description) for name, cls in STRATEGY_REGISTRY.items()]


def get_strategy(
    name: str,
    config: Optional[StrategyConfig] = None
) -> BaseStrategy:
    """
    Get a strategy instance by name.

    Args:
        name: Strategy name (e.g., "ls_fade", "momentum")
        config: Optional custom configuration

    Returns:
        Strategy instance

    Raises:
        ValueError: If strategy name is not found
    """
    if name not in STRATEGY_REGISTRY:
        available = ", ".join(STRATEGY_REGISTRY.keys())
        raise ValueError(f"Unknown strategy '{name}'. Available: {available}")

    strategy_class = STRATEGY_REGISTRY[name]
    return strategy_class(config) if config else strategy_class()


def register_strategy(name: str, strategy_class: Type[BaseStrategy]) -> None:
    """
    Register a custom strategy.

    Args:
        name: Strategy name
        strategy_class: Strategy class (must inherit from BaseStrategy)
    """
    if not issubclass(strategy_class, BaseStrategy):
        raise TypeError(f"{strategy_class} must inherit from BaseStrategy")
    STRATEGY_REGISTRY[name] = strategy_class


__all__ = [
    # Base classes
    "BaseStrategy",
    "StrategyConfig",
    "StrategyData",
    "Signal",
    "DailyCandle",
    # Strategy implementations
    "LSFadeStrategy",
    "MomentumStrategy",
    "ReversalStrategy",
    "MomentumLSStrategy",
    "MeanReversionStrategy",
    # Factory functions
    "get_strategy",
    "list_strategies",
    "register_strategy",
    "STRATEGY_REGISTRY",
]
