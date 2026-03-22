# -*- coding: utf-8 -*-
"""
Tests for config/pair_config.py and config/pairs_loader.py

Testing:
- PairConfig dataclass creation and defaults
- PairsConfigLoader loading and merging
- CLI overrides priority
- get_pair_config for known and unknown symbols
- is_strategy_enabled and get_strategy_threshold methods
"""

import pytest
import json
import tempfile
from pathlib import Path

from tradebot.config import (
    PairConfig,
    TradingParams,
    TrailingStopParams,
    StrategyThresholds,
    FilterParams,
    TimeFilterParams,
    DynamicSizeParams,
    MLFilterParams,
    StrategyOverride,
    PairsConfigLoader,
    load_pairs_config,
    get_pair_config,
    reset_pairs_config,
)


class TestPairConfigDataclass:
    """Test PairConfig dataclass and defaults."""

    def test_default_trading_params(self):
        """TradingParams should have correct defaults."""
        params = TradingParams()
        assert params.order_size_usd == 100.0
        assert params.leverage == 10
        assert params.sl_pct == 4.0
        assert params.tp_pct == 10.0
        assert params.max_hold_days == 14

    def test_default_trailing_stop_params(self):
        """TrailingStopParams should have correct defaults."""
        params = TrailingStopParams()
        assert params.enabled is False
        assert params.callback_rate == 1.0
        assert params.activation_pct is None
        assert params.with_tp is False

    def test_default_filter_params(self):
        """FilterParams should have correct defaults."""
        params = FilterParams()
        assert params.coin_regime_enabled is False
        assert params.vol_filter_low_enabled is False
        assert params.vol_filter_high_enabled is False
        assert params.vol_filter_low_threshold is None
        assert params.vol_filter_high_threshold is None
        assert params.regime_filter_enabled is True  # Default is True
        assert params.dedup_days == 3
        assert params.position_mode == "single"
        assert params.late_signal_skip_after == 3

    def test_pair_config_creation(self):
        """PairConfig should be creatable with symbol."""
        config = PairConfig(symbol="BTCUSDT")
        assert config.symbol == "BTCUSDT"
        assert config.enabled is True
        assert config.trading.order_size_usd == 100.0
        assert config.trailing_stop.enabled is False

    def test_is_strategy_enabled_in_list(self):
        """is_strategy_enabled returns True for strategies in the list."""
        config = PairConfig(
            symbol="BTCUSDT",
            strategies=["momentum", "reversal"]
        )
        assert config.is_strategy_enabled("momentum") is True
        assert config.is_strategy_enabled("reversal") is True
        assert config.is_strategy_enabled("ls_fade") is False

    def test_is_strategy_enabled_with_override(self):
        """is_strategy_enabled respects strategy_overrides."""
        config = PairConfig(
            symbol="BTCUSDT",
            strategies=["momentum", "reversal"],
            strategy_overrides={
                "momentum": StrategyOverride(enabled=False)
            }
        )
        assert config.is_strategy_enabled("momentum") is False
        assert config.is_strategy_enabled("reversal") is True

    def test_get_strategy_threshold_default(self):
        """get_strategy_threshold returns from strategy_thresholds."""
        config = PairConfig(
            symbol="BTCUSDT",
            strategy_thresholds=StrategyThresholds(momentum_threshold=7.0)
        )
        assert config.get_strategy_threshold("momentum", "momentum_threshold") == 7.0

    def test_get_strategy_threshold_with_override(self):
        """get_strategy_threshold respects per-strategy overrides."""
        config = PairConfig(
            symbol="BTCUSDT",
            strategy_thresholds=StrategyThresholds(momentum_threshold=5.0),
            strategy_overrides={
                "momentum": StrategyOverride(
                    enabled=True,
                    thresholds={"momentum_threshold": 10.0}
                )
            }
        )
        # Override takes precedence
        assert config.get_strategy_threshold("momentum", "momentum_threshold") == 10.0
        # Other strategies use default
        assert config.get_strategy_threshold("reversal", "momentum_threshold") == 5.0

    def test_to_strategy_config_params(self):
        """to_strategy_config_params returns dict with thresholds."""
        config = PairConfig(
            symbol="BTCUSDT",
            strategy_thresholds=StrategyThresholds(
                momentum_threshold=5.0,
                ls_extreme=0.70
            )
        )
        params = config.to_strategy_config_params()
        assert params["momentum_threshold"] == 5.0
        assert params["ls_extreme"] == 0.70


class TestPairsConfigLoader:
    """Test PairsConfigLoader loading and merging."""

    @pytest.fixture
    def temp_config_file(self):
        """Create a temporary pairs.json file."""
        config_data = {
            "default": {
                "enabled": True,
                "trading": {
                    "order_size_usd": 100.0,
                    "leverage": 10,
                    "sl_pct": 4.0,
                    "tp_pct": 10.0
                },
                "trailing_stop": {
                    "enabled": False,
                    "callback_rate": 1.0
                },
                "strategies": ["momentum", "reversal"],
                "strategy_thresholds": {
                    "momentum_threshold": 5.0
                },
                "filters": {
                    "dedup_days": 3
                }
            },
            "pairs": {
                "BTCUSDT": {
                    "trading": {
                        "order_size_usd": 1500,
                        "leverage": 20
                    },
                    "trailing_stop": {
                        "enabled": True,
                        "callback_rate": 0.3
                    },
                    "strategy_thresholds": {
                        "momentum_threshold": 7.0
                    }
                },
                "DOGEUSDT": {
                    "enabled": False
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config_data, f)
            temp_path = Path(f.name)

        yield temp_path

        # Cleanup
        temp_path.unlink(missing_ok=True)

    def test_load_config_file(self, temp_config_file):
        """Loader should load config from file."""
        loader = PairsConfigLoader(temp_config_file)
        result = loader.load()
        assert result is True
        assert loader.has_config("BTCUSDT")
        assert loader.has_config("DOGEUSDT")
        assert not loader.has_config("ETHUSDT")

    def test_deep_merge_trading_params(self, temp_config_file):
        """Pair-specific trading params should override defaults."""
        loader = PairsConfigLoader(temp_config_file)
        loader.load()

        btc_config = loader.get_pair_config("BTCUSDT")

        # Overridden
        assert btc_config.trading.order_size_usd == 1500
        assert btc_config.trading.leverage == 20
        # Inherited from default
        assert btc_config.trading.sl_pct == 4.0
        assert btc_config.trading.tp_pct == 10.0

    def test_deep_merge_trailing_stop(self, temp_config_file):
        """Pair-specific trailing stop should override defaults."""
        loader = PairsConfigLoader(temp_config_file)
        loader.load()

        btc_config = loader.get_pair_config("BTCUSDT")

        # Overridden
        assert btc_config.trailing_stop.enabled is True
        assert btc_config.trailing_stop.callback_rate == 0.3
        # Inherited (with_tp wasn't in override)
        assert btc_config.trailing_stop.with_tp is False

    def test_unknown_symbol_gets_defaults(self, temp_config_file):
        """Unknown symbol should get default config."""
        loader = PairsConfigLoader(temp_config_file)
        loader.load()

        eth_config = loader.get_pair_config("ETHUSDT")

        assert eth_config.symbol == "ETHUSDT"
        assert eth_config.trading.order_size_usd == 100.0
        assert eth_config.trading.leverage == 10
        assert eth_config.trailing_stop.enabled is False

    def test_disabled_pair(self, temp_config_file):
        """Disabled pair should have enabled=False."""
        loader = PairsConfigLoader(temp_config_file)
        loader.load()

        doge_config = loader.get_pair_config("DOGEUSDT")
        assert doge_config.enabled is False

    def test_get_enabled_symbols(self, temp_config_file):
        """get_enabled_symbols should filter out disabled pairs."""
        loader = PairsConfigLoader(temp_config_file)
        loader.load()

        symbols = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]
        enabled = loader.get_enabled_symbols(symbols)

        assert "BTCUSDT" in enabled
        assert "ETHUSDT" in enabled  # Unknown = uses default (enabled)
        assert "DOGEUSDT" not in enabled  # Explicitly disabled

    def test_get_all_configured_symbols(self, temp_config_file):
        """get_all_configured_symbols returns symbols with custom config."""
        loader = PairsConfigLoader(temp_config_file)
        loader.load()

        configured = loader.get_all_configured_symbols()
        assert "BTCUSDT" in configured
        assert "DOGEUSDT" in configured
        assert "ETHUSDT" not in configured

    def test_cli_overrides(self, temp_config_file):
        """CLI overrides should have highest priority."""
        loader = PairsConfigLoader(temp_config_file)
        loader.load()

        # Apply CLI override
        loader.apply_cli_overrides(
            order_size=500.0,
            leverage=5,
            trailing_stop=False
        )

        btc_config = loader.get_pair_config("BTCUSDT")

        # CLI overrides pair-specific config
        assert btc_config.trading.order_size_usd == 500.0
        assert btc_config.trading.leverage == 5
        assert btc_config.trailing_stop.enabled is False

    def test_cli_overrides_apply_to_unknown_symbols(self, temp_config_file):
        """CLI overrides should also apply to symbols NOT in pairs.json."""
        loader = PairsConfigLoader(temp_config_file)
        loader.load()

        # Apply CLI override
        loader.apply_cli_overrides(
            order_size=999.0,
            sl=7.5,
        )

        # ETHUSDT is NOT in pairs.json - but should get CLI overrides
        eth_config = loader.get_pair_config("ETHUSDT")

        assert eth_config.trading.order_size_usd == 999.0
        assert eth_config.trading.sl_pct == 7.5
        # Other params should be from defaults
        assert eth_config.trading.leverage == 10

    def test_no_config_file(self):
        """Loader should work without config file (use hardcoded defaults)."""
        loader = PairsConfigLoader(Path("/nonexistent/path.json"))
        result = loader.load()

        assert result is True  # Should succeed with defaults

        btc_config = loader.get_pair_config("BTCUSDT")
        assert btc_config.symbol == "BTCUSDT"
        assert btc_config.enabled is True
        assert btc_config.trading.order_size_usd == 100.0

    def test_reload_config(self, temp_config_file):
        """reload() should refresh config from file."""
        loader = PairsConfigLoader(temp_config_file)
        loader.load()

        # Modify the file
        with open(temp_config_file, 'r') as f:
            data = json.load(f)
        data['pairs']['BTCUSDT']['trading']['order_size_usd'] = 9999
        with open(temp_config_file, 'w') as f:
            json.dump(data, f)

        # Reload
        loader.reload()
        btc_config = loader.get_pair_config("BTCUSDT")
        assert btc_config.trading.order_size_usd == 9999


class TestModuleLevelFunctions:
    """Test module-level convenience functions."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before each test."""
        reset_pairs_config()
        yield
        reset_pairs_config()

    def test_reset_pairs_config(self):
        """reset_pairs_config should clear the singleton."""
        # Load once
        load_pairs_config()
        # Reset
        reset_pairs_config()
        # Next load should create new instance
        loader = load_pairs_config()
        assert loader is not None


class TestVolFilterThresholds:
    """Test vol_filter_low_threshold and vol_filter_high_threshold."""

    def test_vol_thresholds_in_filter_params(self):
        """FilterParams should have vol threshold fields."""
        params = FilterParams(
            vol_filter_low_threshold=2.0,
            vol_filter_high_threshold=8.0
        )
        assert params.vol_filter_low_threshold == 2.0
        assert params.vol_filter_high_threshold == 8.0

    def test_vol_thresholds_from_json(self):
        """Vol thresholds should be loaded from JSON."""
        config_data = {
            "default": {
                "filters": {
                    "vol_filter_low_threshold": 1.5,
                    "vol_filter_high_threshold": 10.0
                }
            },
            "pairs": {}
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config_data, f)
            temp_path = Path(f.name)

        try:
            loader = PairsConfigLoader(temp_path)
            loader.load()

            config = loader.get_pair_config("ANYUSDT")
            assert config.filters.vol_filter_low_threshold == 1.5
            assert config.filters.vol_filter_high_threshold == 10.0
        finally:
            temp_path.unlink(missing_ok=True)
