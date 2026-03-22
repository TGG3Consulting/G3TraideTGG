# -*- coding: utf-8 -*-
"""
Tradebot Configuration Module.

Provides per-pair configuration support for trading parameters.
"""

from .pair_config import (
    PairConfig,
    TradingParams,
    TrailingStopParams,
    StrategyThresholds,
    FilterParams,
    TimeFilterParams,
    DynamicSizeParams,
    MLFilterParams,
    StrategyOverride,
)

from .pairs_loader import (
    PairsConfigLoader,
    load_pairs_config,
    get_pair_config,
    reset_pairs_config,
)

__all__ = [
    # Dataclasses
    "PairConfig",
    "TradingParams",
    "TrailingStopParams",
    "StrategyThresholds",
    "FilterParams",
    "TimeFilterParams",
    "DynamicSizeParams",
    "MLFilterParams",
    "StrategyOverride",
    # Loader
    "PairsConfigLoader",
    "load_pairs_config",
    "get_pair_config",
    "reset_pairs_config",
]
