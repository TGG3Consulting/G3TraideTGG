# -*- coding: utf-8 -*-
"""
Pairs Configuration Loader.

Loads and resolves per-pair configuration from config/pairs.json.
Supports merging defaults with pair-specific overrides and CLI overrides.

Usage:
    loader = PairsConfigLoader()
    loader.load()

    # Get config for specific pair
    btc_config = loader.get_pair_config("BTCUSDT")

    # Get all enabled pairs
    enabled = loader.get_enabled_symbols(["BTCUSDT", "ETHUSDT", "DOGEUSDT"])
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

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

logger = logging.getLogger(__name__)

# Default config path
CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
PAIRS_CONFIG_FILE = CONFIG_DIR / "pairs.json"


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Deep merge two dictionaries.
    Override values take precedence over base values.

    Args:
        base: Base dictionary
        override: Override dictionary (values take precedence)

    Returns:
        Merged dictionary
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _dict_to_trading_params(d: dict) -> TradingParams:
    """Convert dict to TradingParams."""
    return TradingParams(
        order_size_usd=d.get("order_size_usd", 100.0),
        leverage=d.get("leverage", 10),
        sl_pct=d.get("sl_pct", 4.0),
        tp_pct=d.get("tp_pct", 10.0),
        max_hold_days=d.get("max_hold_days", 14),
    )


def _dict_to_trailing_stop(d: dict) -> TrailingStopParams:
    """Convert dict to TrailingStopParams."""
    return TrailingStopParams(
        enabled=d.get("enabled", False),
        callback_rate=d.get("callback_rate", 1.0),
        activation_pct=d.get("activation_pct"),
        with_tp=d.get("with_tp", False),
    )


def _dict_to_strategy_thresholds(d: dict) -> StrategyThresholds:
    """Convert dict to StrategyThresholds."""
    return StrategyThresholds(
        momentum_threshold=d.get("momentum_threshold", 5.0),
        ls_extreme=d.get("ls_extreme", 0.65),
        oversold_threshold=d.get("oversold_threshold", -10.0),
        overbought_threshold=d.get("overbought_threshold", 15.0),
        crowd_bearish=d.get("crowd_bearish", 0.55),
        crowd_bullish=d.get("crowd_bullish", 0.60),
        ls_confirm=d.get("ls_confirm", 0.60),
    )


def _dict_to_filters(d: dict) -> FilterParams:
    """Convert dict to FilterParams."""
    return FilterParams(
        coin_regime_enabled=d.get("coin_regime_enabled", False),
        coin_regime_lookback=d.get("coin_regime_lookback", 14),
        vol_filter_low_enabled=d.get("vol_filter_low_enabled", False),
        vol_filter_high_enabled=d.get("vol_filter_high_enabled", False),
        vol_filter_low_threshold=d.get("vol_filter_low_threshold"),  # None = use strategy default
        vol_filter_high_threshold=d.get("vol_filter_high_threshold"),  # None = use strategy default
        regime_filter_enabled=d.get("regime_filter_enabled", True),  # True = participate in regime filter
        dedup_days=d.get("dedup_days", 3),
        position_mode=d.get("position_mode", "single"),
        late_signal_skip_after=d.get("late_signal_skip_after", 3),
    )


def _dict_to_time_filters(d: dict) -> TimeFilterParams:
    """Convert dict to TimeFilterParams."""
    return TimeFilterParams(
        month_off_dd=d.get("month_off_dd"),
        month_off_pnl=d.get("month_off_pnl"),
        day_off_dd=d.get("day_off_dd"),
        day_off_pnl=d.get("day_off_pnl"),
    )


def _dict_to_dynamic_size(d: dict) -> DynamicSizeParams:
    """Convert dict to DynamicSizeParams."""
    return DynamicSizeParams(
        enabled=d.get("enabled", False),
        protected_size=d.get("protected_size", 10.0),
    )


def _dict_to_ml_filter(d: dict) -> MLFilterParams:
    """Convert dict to MLFilterParams."""
    return MLFilterParams(
        enabled=d.get("enabled", False),
        model_dir=d.get("model_dir", "models"),
    )


def _resolve_pair_config(symbol: str, merged: dict) -> PairConfig:
    """
    Convert merged dict to PairConfig object.

    Args:
        symbol: Trading pair symbol
        merged: Merged configuration dict (defaults + pair overrides)

    Returns:
        Resolved PairConfig
    """
    # Parse strategy overrides
    strategy_overrides = {}
    if "strategy_overrides" in merged:
        for strat_name, override_dict in merged["strategy_overrides"].items():
            strategy_overrides[strat_name] = StrategyOverride(
                enabled=override_dict.get("enabled", True),
                thresholds={k: v for k, v in override_dict.items() if k != "enabled"},
            )

    return PairConfig(
        symbol=symbol,
        enabled=merged.get("enabled", True),
        trading=_dict_to_trading_params(merged.get("trading", {})),
        trailing_stop=_dict_to_trailing_stop(merged.get("trailing_stop", {})),
        strategies=merged.get("strategies", [
            'ls_fade', 'momentum', 'reversal', 'mean_reversion', 'momentum_ls'
        ]),
        strategy_thresholds=_dict_to_strategy_thresholds(merged.get("strategy_thresholds", {})),
        filters=_dict_to_filters(merged.get("filters", {})),
        time_filters=_dict_to_time_filters(merged.get("time_filters", {})),
        dynamic_size=_dict_to_dynamic_size(merged.get("dynamic_size", {})),
        ml_filter=_dict_to_ml_filter(merged.get("ml_filter", {})),
        strategy_overrides=strategy_overrides,
    )


class PairsConfigLoader:
    """
    Loads and manages per-pair configuration.

    Supports:
    - Loading from config/pairs.json
    - Merging defaults with pair-specific overrides
    - CLI argument overrides (highest priority)
    - Filtering enabled/disabled pairs

    Priority: CLI args > pairs.json[pairs][SYMBOL] > pairs.json[default] > hardcoded defaults
    """

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize loader.

        Args:
            config_path: Path to pairs.json (default: config/pairs.json)
        """
        self.config_path = config_path or PAIRS_CONFIG_FILE
        self._raw_config: dict = {}
        self._default_config: dict = {}
        self._pair_configs: Dict[str, PairConfig] = {}
        self._loaded = False

    def load(self) -> bool:
        """
        Load configuration from file.

        Returns:
            True if loaded successfully, False otherwise
        """
        if not self.config_path.exists():
            logger.info(f"pairs.json not found at {self.config_path}, using defaults")
            self._loaded = True
            return True

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self._raw_config = json.load(f)

            self._default_config = self._raw_config.get("default", {})

            # Pre-resolve all pair configs
            pairs_section = self._raw_config.get("pairs", {})
            for symbol, pair_override in pairs_section.items():
                merged = _deep_merge(self._default_config, pair_override)
                self._pair_configs[symbol] = _resolve_pair_config(symbol, merged)

            self._loaded = True
            logger.info(f"Loaded pairs config: {len(self._pair_configs)} pairs configured")
            return True

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in pairs.json: {e}")
            self._loaded = True
            return False
        except Exception as e:
            logger.error(f"Failed to load pairs.json: {e}")
            self._loaded = True
            return False

    def get_pair_config(self, symbol: str) -> PairConfig:
        """
        Get resolved configuration for a symbol.

        If symbol has specific config, returns merged config.
        Otherwise returns default config with symbol set.

        Args:
            symbol: Trading pair symbol (e.g., "BTCUSDT")

        Returns:
            PairConfig for the symbol
        """
        if not self._loaded:
            self.load()

        if symbol in self._pair_configs:
            return self._pair_configs[symbol]

        # Return default config for unknown symbols
        # But apply CLI overrides if any were set!
        config = _resolve_pair_config(symbol, self._default_config)

        # Apply CLI overrides to this new config
        if hasattr(self, '_cli_overrides') and self._cli_overrides:
            self._apply_overrides_to_config(
                config,
                self._cli_overrides.get("order_size"),
                self._cli_overrides.get("leverage"),
                self._cli_overrides.get("sl"),
                self._cli_overrides.get("tp"),
                self._cli_overrides.get("max_hold"),
                self._cli_overrides.get("trailing_stop"),
                self._cli_overrides.get("trailing_callback"),
                self._cli_overrides.get("trailing_activation"),
                self._cli_overrides.get("trailing_with_tp"),
                self._cli_overrides.get("coin_regime"),
                self._cli_overrides.get("coin_regime_lookback"),
                self._cli_overrides.get("vol_filter_low"),
                self._cli_overrides.get("vol_filter_high"),
                self._cli_overrides.get("regime_filter"),
                self._cli_overrides.get("dedup_days"),
                self._cli_overrides.get("position_mode"),
                self._cli_overrides.get("late_signal_skip_after"),
                self._cli_overrides.get("dynamic_size"),
                self._cli_overrides.get("protected_size"),
                self._cli_overrides.get("ml"),
                self._cli_overrides.get("ml_model_dir"),
                self._cli_overrides.get("month_off_dd"),
                self._cli_overrides.get("month_off_pnl"),
                self._cli_overrides.get("day_off_dd"),
                self._cli_overrides.get("day_off_pnl"),
            )
            # Cache this config for future calls
            self._pair_configs[symbol] = config

        return config

    def get_enabled_symbols(self, symbols: List[str]) -> List[str]:
        """
        Filter symbols list to only enabled ones.

        Args:
            symbols: List of symbols to filter

        Returns:
            List of enabled symbols
        """
        result = []
        for symbol in symbols:
            config = self.get_pair_config(symbol)
            if config.enabled:
                result.append(symbol)
            else:
                logger.debug(f"SKIP {symbol}: disabled in pairs.json")
        return result

    def apply_cli_overrides(
        self,
        order_size: Optional[float] = None,
        leverage: Optional[int] = None,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        max_hold: Optional[int] = None,
        trailing_stop: Optional[bool] = None,
        trailing_callback: Optional[float] = None,
        trailing_activation: Optional[float] = None,
        trailing_with_tp: Optional[bool] = None,
        coin_regime: Optional[bool] = None,
        coin_regime_lookback: Optional[int] = None,
        vol_filter_low: Optional[bool] = None,
        vol_filter_high: Optional[bool] = None,
        regime_filter: Optional[bool] = None,
        dedup_days: Optional[int] = None,
        position_mode: Optional[str] = None,
        late_signal_skip_after: Optional[int] = None,
        dynamic_size: Optional[bool] = None,
        protected_size: Optional[float] = None,
        ml: Optional[bool] = None,
        ml_model_dir: Optional[str] = None,
        month_off_dd: Optional[float] = None,
        month_off_pnl: Optional[float] = None,
        day_off_dd: Optional[float] = None,
        day_off_pnl: Optional[float] = None,
    ) -> None:
        """
        Apply CLI argument overrides to all pair configs.

        CLI args have highest priority and override both defaults and pair-specific settings.
        Only non-None values are applied.
        """
        # Apply to all existing pair configs
        for config in self._pair_configs.values():
            self._apply_overrides_to_config(
                config, order_size, leverage, sl, tp, max_hold,
                trailing_stop, trailing_callback, trailing_activation, trailing_with_tp,
                coin_regime, coin_regime_lookback, vol_filter_low, vol_filter_high,
                regime_filter, dedup_days, position_mode, late_signal_skip_after,
                dynamic_size, protected_size, ml, ml_model_dir,
                month_off_dd, month_off_pnl, day_off_dd, day_off_pnl,
            )

        # Store overrides for new symbols created later
        self._cli_overrides = {
            "order_size": order_size,
            "leverage": leverage,
            "sl": sl,
            "tp": tp,
            "max_hold": max_hold,
            "trailing_stop": trailing_stop,
            "trailing_callback": trailing_callback,
            "trailing_activation": trailing_activation,
            "trailing_with_tp": trailing_with_tp,
            "coin_regime": coin_regime,
            "coin_regime_lookback": coin_regime_lookback,
            "vol_filter_low": vol_filter_low,
            "vol_filter_high": vol_filter_high,
            "regime_filter": regime_filter,
            "dedup_days": dedup_days,
            "position_mode": position_mode,
            "late_signal_skip_after": late_signal_skip_after,
            "dynamic_size": dynamic_size,
            "protected_size": protected_size,
            "ml": ml,
            "ml_model_dir": ml_model_dir,
            "month_off_dd": month_off_dd,
            "month_off_pnl": month_off_pnl,
            "day_off_dd": day_off_dd,
            "day_off_pnl": day_off_pnl,
        }

    def _apply_overrides_to_config(
        self, config: PairConfig,
        order_size, leverage, sl, tp, max_hold,
        trailing_stop, trailing_callback, trailing_activation, trailing_with_tp,
        coin_regime, coin_regime_lookback, vol_filter_low, vol_filter_high,
        regime_filter, dedup_days, position_mode, late_signal_skip_after,
        dynamic_size, protected_size, ml, ml_model_dir,
        month_off_dd, month_off_pnl, day_off_dd, day_off_pnl,
    ) -> None:
        """Apply override values to a single PairConfig."""
        # Trading params
        if order_size is not None:
            config.trading.order_size_usd = order_size
        if leverage is not None:
            config.trading.leverage = leverage
        if sl is not None:
            config.trading.sl_pct = sl
        if tp is not None:
            config.trading.tp_pct = tp
        if max_hold is not None:
            config.trading.max_hold_days = max_hold

        # Trailing stop
        if trailing_stop is not None:
            config.trailing_stop.enabled = trailing_stop
        if trailing_callback is not None:
            config.trailing_stop.callback_rate = trailing_callback
        if trailing_activation is not None:
            config.trailing_stop.activation_pct = trailing_activation
        if trailing_with_tp is not None:
            config.trailing_stop.with_tp = trailing_with_tp

        # Filters
        if coin_regime is not None:
            config.filters.coin_regime_enabled = coin_regime
        if coin_regime_lookback is not None:
            config.filters.coin_regime_lookback = coin_regime_lookback
        if vol_filter_low is not None:
            config.filters.vol_filter_low_enabled = vol_filter_low
        if vol_filter_high is not None:
            config.filters.vol_filter_high_enabled = vol_filter_high
        if regime_filter is not None:
            config.filters.regime_filter_enabled = regime_filter
        if dedup_days is not None:
            config.filters.dedup_days = dedup_days
        if position_mode is not None:
            config.filters.position_mode = position_mode
        if late_signal_skip_after is not None:
            config.filters.late_signal_skip_after = late_signal_skip_after

        # Time filters
        if month_off_dd is not None:
            config.time_filters.month_off_dd = month_off_dd
        if month_off_pnl is not None:
            config.time_filters.month_off_pnl = month_off_pnl
        if day_off_dd is not None:
            config.time_filters.day_off_dd = day_off_dd
        if day_off_pnl is not None:
            config.time_filters.day_off_pnl = day_off_pnl

        # Dynamic size
        if dynamic_size is not None:
            config.dynamic_size.enabled = dynamic_size
        if protected_size is not None:
            config.dynamic_size.protected_size = protected_size

        # ML filter
        if ml is not None:
            config.ml_filter.enabled = ml
        if ml_model_dir is not None:
            config.ml_filter.model_dir = ml_model_dir

    def reload(self) -> bool:
        """Reload configuration from file."""
        self._pair_configs.clear()
        self._default_config.clear()
        self._raw_config.clear()
        self._loaded = False
        return self.load()

    def get_all_configured_symbols(self) -> List[str]:
        """Get list of all symbols that have specific configuration."""
        return list(self._pair_configs.keys())

    def has_config(self, symbol: str) -> bool:
        """Check if symbol has specific configuration (not just defaults)."""
        return symbol in self._pair_configs


# Module-level singleton for convenience
_pairs_loader: Optional[PairsConfigLoader] = None


def load_pairs_config(config_path: Optional[Path] = None) -> PairsConfigLoader:
    """
    Load or get cached pairs configuration.

    Args:
        config_path: Optional path to config file

    Returns:
        PairsConfigLoader instance
    """
    global _pairs_loader
    if _pairs_loader is None:
        _pairs_loader = PairsConfigLoader(config_path)
        _pairs_loader.load()
    return _pairs_loader


def get_pair_config(symbol: str) -> PairConfig:
    """
    Convenience function to get config for a symbol.

    Args:
        symbol: Trading pair symbol

    Returns:
        PairConfig for the symbol
    """
    loader = load_pairs_config()
    return loader.get_pair_config(symbol)


def reset_pairs_config() -> None:
    """Reset the singleton loader (for testing)."""
    global _pairs_loader
    _pairs_loader = None
