# -*- coding: utf-8 -*-
"""
Per-Pair Configuration Models.

Provides PairConfig dataclass and related types for per-pair trading settings.
Each trading pair can have its own configuration that overrides global defaults.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class TradingParams:
    """Trading parameters for a pair."""
    order_size_usd: float = 100.0
    leverage: int = 10
    sl_pct: float = 4.0
    tp_pct: float = 10.0
    max_hold_days: int = 14


@dataclass
class TrailingStopParams:
    """Trailing stop parameters."""
    enabled: bool = False
    callback_rate: float = 1.0
    activation_pct: Optional[float] = None
    with_tp: bool = False


@dataclass
class StrategyThresholds:
    """Strategy-specific thresholds."""
    momentum_threshold: float = 5.0
    ls_extreme: float = 0.65
    oversold_threshold: float = -10.0
    overbought_threshold: float = 15.0
    crowd_bearish: float = 0.55
    crowd_bullish: float = 0.60
    ls_confirm: float = 0.60


@dataclass
class FilterParams:
    """Filter parameters."""
    coin_regime_enabled: bool = False
    coin_regime_lookback: int = 14
    vol_filter_low_enabled: bool = False
    vol_filter_high_enabled: bool = False
    vol_filter_low_threshold: Optional[float] = None  # Per-pair vol low threshold (None = use strategy default)
    vol_filter_high_threshold: Optional[float] = None  # Per-pair vol high threshold (None = use strategy default)
    regime_filter_enabled: bool = True  # True = participate in global regime filter, False = excluded
    dedup_days: int = 3
    position_mode: str = "single"
    late_signal_skip_after: Optional[int] = 3


@dataclass
class TimeFilterParams:
    """Month/Day filter parameters."""
    month_off_dd: Optional[float] = None
    month_off_pnl: Optional[float] = None
    day_off_dd: Optional[float] = None
    day_off_pnl: Optional[float] = None


@dataclass
class DynamicSizeParams:
    """Dynamic sizing parameters."""
    enabled: bool = False
    protected_size: float = 10.0


@dataclass
class MLFilterParams:
    """ML filter parameters."""
    enabled: bool = False
    model_dir: str = "models"


@dataclass
class StrategyOverride:
    """Per-strategy override for a specific pair."""
    enabled: bool = True
    thresholds: Dict[str, float] = field(default_factory=dict)


@dataclass
class PairConfig:
    """
    Complete configuration for a trading pair.

    All fields are resolved (defaults merged with pair-specific overrides).

    Usage:
        config = get_pair_config("BTCUSDT")
        order_size = config.trading.order_size_usd
        if config.trailing_stop.enabled:
            callback = config.trailing_stop.callback_rate
    """
    symbol: str
    enabled: bool = True

    trading: TradingParams = field(default_factory=TradingParams)
    trailing_stop: TrailingStopParams = field(default_factory=TrailingStopParams)
    strategies: List[str] = field(default_factory=lambda: [
        'ls_fade', 'momentum', 'reversal', 'mean_reversion', 'momentum_ls'
    ])
    strategy_thresholds: StrategyThresholds = field(default_factory=StrategyThresholds)
    filters: FilterParams = field(default_factory=FilterParams)
    time_filters: TimeFilterParams = field(default_factory=TimeFilterParams)
    dynamic_size: DynamicSizeParams = field(default_factory=DynamicSizeParams)
    ml_filter: MLFilterParams = field(default_factory=MLFilterParams)

    # Per-strategy overrides: strategy_name -> StrategyOverride
    strategy_overrides: Dict[str, StrategyOverride] = field(default_factory=dict)

    def is_strategy_enabled(self, strategy_name: str) -> bool:
        """
        Check if a strategy is enabled for this pair.

        Args:
            strategy_name: Name of strategy (e.g., "momentum", "ls_fade")

        Returns:
            True if strategy is enabled for this pair
        """
        if strategy_name not in self.strategies:
            return False
        if strategy_name in self.strategy_overrides:
            return self.strategy_overrides[strategy_name].enabled
        return True

    def get_strategy_threshold(self, strategy_name: str, param_name: str) -> float:
        """
        Get threshold for a strategy, with per-strategy override support.

        Priority: strategy_overrides > strategy_thresholds

        Args:
            strategy_name: Name of strategy
            param_name: Name of threshold parameter

        Returns:
            Threshold value
        """
        # Check strategy-specific override first
        if strategy_name in self.strategy_overrides:
            override = self.strategy_overrides[strategy_name]
            if param_name in override.thresholds:
                return override.thresholds[param_name]

        # Fall back to pair-level threshold
        return getattr(self.strategy_thresholds, param_name, 0.0)

    def to_strategy_config_params(self, strategy_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Convert thresholds to StrategyConfig.params format.

        Args:
            strategy_name: If provided, include per-strategy overrides

        Returns:
            Dict compatible with StrategyConfig.params
        """
        params = {
            "ls_extreme": self.strategy_thresholds.ls_extreme,
            "momentum_threshold": self.strategy_thresholds.momentum_threshold,
            "oversold_threshold": self.strategy_thresholds.oversold_threshold,
            "overbought_threshold": self.strategy_thresholds.overbought_threshold,
            "crowd_bearish": self.strategy_thresholds.crowd_bearish,
            "crowd_bullish": self.strategy_thresholds.crowd_bullish,
            "ls_confirm": self.strategy_thresholds.ls_confirm,
        }

        # Apply per-strategy overrides if available
        if strategy_name and strategy_name in self.strategy_overrides:
            override = self.strategy_overrides[strategy_name]
            params.update(override.thresholds)

        return params

    def __repr__(self) -> str:
        return (
            f"PairConfig(symbol={self.symbol!r}, enabled={self.enabled}, "
            f"order_size={self.trading.order_size_usd}, "
            f"sl={self.trading.sl_pct}%, tp={self.trading.tp_pct}%)"
        )
