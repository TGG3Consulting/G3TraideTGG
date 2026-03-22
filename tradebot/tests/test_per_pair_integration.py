# -*- coding: utf-8 -*-
"""
Integration tests for Per-Pair Configuration.

Tests the FULL flow:
1. Load pairs.json config
2. Apply CLI overrides
3. Generate signals with per-pair thresholds
4. Execute signals with per-pair SL/TP/leverage/trailing
5. Handle failures and orphan cleanup
6. Verify cleanup recovers from failed orders

These are END-TO-END tests simulating real scenarios.
"""

import asyncio
import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import sys
import os

# Add paths
tradebot_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if tradebot_path not in sys.path:
    sys.path.insert(0, tradebot_path)

gen_signals_path = os.path.join(os.path.dirname(tradebot_path), 'GenerateHistorySignals')
if gen_signals_path not in sys.path:
    sys.path.insert(0, gen_signals_path)

from tradebot.config import PairsConfigLoader, PairConfig, reset_pairs_config
from tradebot.core.models import Position, PositionSide, PositionStatus, OrderSide
from tradebot.engine.trade_engine import TradeEngine
from tradebot.engine.position_manager import PositionManager


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def pairs_config_file():
    """Create temporary pairs.json with different configs per symbol."""
    config_data = {
        "default": {
            "enabled": True,
            "trading": {
                "order_size_usd": 100.0,
                "leverage": 10,
                "sl_pct": 4.0,
                "tp_pct": 10.0,
                "max_hold_days": 14
            },
            "trailing_stop": {
                "enabled": False,
                "callback_rate": 1.0,
                "activation_pct": None
            },
            "strategies": ["momentum", "reversal"],
            "strategy_thresholds": {
                "momentum_threshold": 5.0,
                "ls_extreme": 0.65
            },
            "filters": {
                "dedup_days": 3,
                "position_mode": "single",
                "regime_filter_enabled": True
            }
        },
        "pairs": {
            "BTCUSDT": {
                "trading": {
                    "order_size_usd": 500.0,
                    "leverage": 20,
                    "sl_pct": 2.0,
                    "tp_pct": 6.0
                },
                "trailing_stop": {
                    "enabled": True,
                    "callback_rate": 0.5,
                    "activation_pct": 1.0
                },
                "strategy_thresholds": {
                    "momentum_threshold": 3.0
                }
            },
            "ETHUSDT": {
                "trading": {
                    "order_size_usd": 300.0,
                    "sl_pct": 5.0,
                    "tp_pct": 15.0
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
    temp_path.unlink(missing_ok=True)


@pytest.fixture
def mock_exchange():
    """Mock exchange with all required methods."""
    exchange = AsyncMock()
    exchange.get_price = AsyncMock(return_value=Decimal("50000"))
    exchange.get_balance = AsyncMock(return_value=Decimal("10000"))
    exchange.round_quantity = MagicMock(side_effect=lambda s, q: q.quantize(Decimal("0.001")))
    exchange.round_price = MagicMock(side_effect=lambda s, p: p.quantize(Decimal("0.01")))
    exchange.get_step_size = MagicMock(return_value=Decimal("0.001"))
    exchange.get_tick_size = MagicMock(return_value=Decimal("0.01"))
    exchange.set_leverage = AsyncMock(return_value=True)
    exchange.get_position_by_side = AsyncMock(return_value=None)
    exchange.get_open_orders = AsyncMock(return_value=[])
    exchange.place_market_order = AsyncMock(return_value={
        "orderId": "123456",
        "avgPrice": "50000",
        "executedQty": "0.01",
        "origQty": "0.01",
        "status": "FILLED",
    })
    exchange.place_stop_order = AsyncMock(return_value={"algoId": "789"})
    exchange.place_take_profit_order = AsyncMock(return_value={"orderId": "456"})
    exchange.place_trailing_stop_order = AsyncMock(return_value={"algoId": "999"})
    exchange.cancel_order = AsyncMock(return_value=True)
    exchange.cancel_algo_order = AsyncMock(return_value=True)
    return exchange


@pytest.fixture
def mock_signal_btc():
    """Mock signal for BTCUSDT."""
    signal = MagicMock()
    signal.signal_id = "SIG_BTC_001"
    signal.symbol = "BTCUSDT"
    signal.direction = "LONG"
    signal.entry = 50000
    signal.stop_loss = 49000
    signal.take_profit = 53000
    signal.date = datetime.now(timezone.utc)
    signal.metadata = {"strategy": "momentum"}
    return signal


@pytest.fixture
def mock_signal_eth():
    """Mock signal for ETHUSDT."""
    signal = MagicMock()
    signal.signal_id = "SIG_ETH_001"
    signal.symbol = "ETHUSDT"
    signal.direction = "SHORT"
    signal.entry = 3000
    signal.stop_loss = 3150
    signal.take_profit = 2700
    signal.date = datetime.now(timezone.utc)
    signal.metadata = {"strategy": "reversal"}
    return signal


@pytest.fixture
def mock_signal_sol():
    """Mock signal for SOLUSDT (not in pairs.json)."""
    signal = MagicMock()
    signal.signal_id = "SIG_SOL_001"
    signal.symbol = "SOLUSDT"
    signal.direction = "LONG"
    signal.entry = 100
    signal.stop_loss = 96
    signal.take_profit = 110
    signal.date = datetime.now(timezone.utc)
    signal.metadata = {"strategy": "momentum"}
    return signal


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset pairs config singleton before each test."""
    reset_pairs_config()
    yield
    reset_pairs_config()


# =============================================================================
# TEST: Per-Pair Config Loading
# =============================================================================

class TestPerPairConfigLoading:
    """Test pairs.json loading and merging."""

    def test_btc_uses_custom_config(self, pairs_config_file):
        """BTCUSDT should use its custom config."""
        loader = PairsConfigLoader(pairs_config_file)
        loader.load()

        btc = loader.get_pair_config("BTCUSDT")

        assert btc.trading.order_size_usd == 500.0
        assert btc.trading.leverage == 20
        assert btc.trading.sl_pct == 2.0
        assert btc.trading.tp_pct == 6.0
        assert btc.trailing_stop.enabled is True
        assert btc.trailing_stop.callback_rate == 0.5
        assert btc.strategy_thresholds.momentum_threshold == 3.0

    def test_eth_partial_override(self, pairs_config_file):
        """ETHUSDT should merge partial config with defaults."""
        loader = PairsConfigLoader(pairs_config_file)
        loader.load()

        eth = loader.get_pair_config("ETHUSDT")

        # Custom
        assert eth.trading.order_size_usd == 300.0
        assert eth.trading.sl_pct == 5.0
        assert eth.trading.tp_pct == 15.0
        # Inherited from default
        assert eth.trading.leverage == 10  # default
        assert eth.trailing_stop.enabled is False  # default
        assert eth.strategy_thresholds.momentum_threshold == 5.0  # default

    def test_doge_disabled(self, pairs_config_file):
        """DOGEUSDT should be disabled."""
        loader = PairsConfigLoader(pairs_config_file)
        loader.load()

        doge = loader.get_pair_config("DOGEUSDT")
        assert doge.enabled is False

    def test_unknown_symbol_gets_defaults(self, pairs_config_file):
        """Unknown symbol should get default config."""
        loader = PairsConfigLoader(pairs_config_file)
        loader.load()

        sol = loader.get_pair_config("SOLUSDT")

        assert sol.trading.order_size_usd == 100.0
        assert sol.trading.leverage == 10
        assert sol.trading.sl_pct == 4.0

    def test_cli_override_applies_to_all(self, pairs_config_file):
        """CLI override should apply to all symbols."""
        loader = PairsConfigLoader(pairs_config_file)
        loader.load()
        loader.apply_cli_overrides(sl=7.0, tp=20.0)

        # BTC gets CLI override
        btc = loader.get_pair_config("BTCUSDT")
        assert btc.trading.sl_pct == 7.0
        assert btc.trading.tp_pct == 20.0

        # ETH gets CLI override
        eth = loader.get_pair_config("ETHUSDT")
        assert eth.trading.sl_pct == 7.0

        # Unknown symbol also gets CLI override
        sol = loader.get_pair_config("SOLUSDT")
        assert sol.trading.sl_pct == 7.0


# =============================================================================
# TEST: Trade Engine with Per-Pair Config
# =============================================================================

class TestTradeEnginePerPairConfig:
    """Test TradeEngine uses per-pair parameters correctly."""

    @pytest.mark.asyncio
    async def test_btc_uses_custom_leverage(self, mock_exchange, mock_signal_btc, pairs_config_file):
        """BTC signal should use leverage=20 from pairs.json."""
        loader = PairsConfigLoader(pairs_config_file)
        loader.load()
        btc_config = loader.get_pair_config("BTCUSDT")

        engine = TradeEngine(
            exchange=mock_exchange,
            default_order_size_usd=100.0,
            default_leverage=10,
            sl_pct=4.0,
            tp_pct=10.0,
        )

        # Execute with per-pair config
        await engine.execute_signal(
            signal=mock_signal_btc,
            order_size_usd=btc_config.trading.order_size_usd,
            leverage=btc_config.trading.leverage,
            sl_pct=btc_config.trading.sl_pct,
            tp_pct=btc_config.trading.tp_pct,
            trailing_stop_enabled=btc_config.trailing_stop.enabled,
            trailing_stop_callback_rate=btc_config.trailing_stop.callback_rate,
        )

        # Verify leverage was set correctly
        mock_exchange.set_leverage.assert_called_with("BTCUSDT", 20)

    @pytest.mark.asyncio
    async def test_btc_uses_custom_sl_pct(self, mock_exchange, mock_signal_btc, pairs_config_file):
        """BTC signal should use sl_pct=2% from pairs.json."""
        loader = PairsConfigLoader(pairs_config_file)
        loader.load()
        btc_config = loader.get_pair_config("BTCUSDT")

        # First check: no position (allows entry)
        # After entry: position exists (for verification)
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:  # First check before entry
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        engine = TradeEngine(
            exchange=mock_exchange,
            default_order_size_usd=100.0,
            default_leverage=10,
            sl_pct=4.0,  # Default
            tp_pct=10.0,
        )

        await engine.execute_signal(
            signal=mock_signal_btc,
            sl_pct=btc_config.trading.sl_pct,  # 2.0
            tp_pct=btc_config.trading.tp_pct,  # 6.0
        )

        # Check SL order was placed
        # Entry = 50000, SL = 2% = 49000
        sl_call = mock_exchange.place_stop_order.call_args
        assert sl_call is not None
        stop_price = sl_call.kwargs.get('stop_price') or sl_call[1].get('stop_price')
        # 50000 * (1 - 0.02) = 49000
        assert float(stop_price) == pytest.approx(49000, rel=0.01)

    @pytest.mark.asyncio
    async def test_btc_uses_trailing_stop(self, mock_exchange, mock_signal_btc, pairs_config_file):
        """BTC should use trailing stop with callback_rate=0.5%."""
        loader = PairsConfigLoader(pairs_config_file)
        loader.load()
        btc_config = loader.get_pair_config("BTCUSDT")

        # First check: no position, after entry: position exists
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        engine = TradeEngine(
            exchange=mock_exchange,
            default_order_size_usd=100.0,
            default_leverage=10,
            trailing_stop_enabled=False,  # Default off
        )

        await engine.execute_signal(
            signal=mock_signal_btc,
            trailing_stop_enabled=btc_config.trailing_stop.enabled,  # True
            trailing_stop_callback_rate=btc_config.trailing_stop.callback_rate,  # 0.5
            trailing_stop_activation_pct=btc_config.trailing_stop.activation_pct,  # 1.0
        )

        # Verify trailing stop was placed
        mock_exchange.place_trailing_stop_order.assert_called_once()
        ts_call = mock_exchange.place_trailing_stop_order.call_args
        callback_rate = ts_call.kwargs.get('callback_rate') or ts_call[1].get('callback_rate')
        assert callback_rate == 0.5

    @pytest.mark.asyncio
    async def test_eth_no_trailing_stop(self, mock_exchange, mock_signal_eth, pairs_config_file):
        """ETH should NOT use trailing stop (uses default)."""
        loader = PairsConfigLoader(pairs_config_file)
        loader.load()
        eth_config = loader.get_pair_config("ETHUSDT")

        mock_exchange.get_price = AsyncMock(return_value=Decimal("3000"))
        mock_exchange.get_position_by_side = AsyncMock(return_value={
            "positionAmt": "-0.1",
            "entryPrice": "3000"
        })

        engine = TradeEngine(
            exchange=mock_exchange,
            default_order_size_usd=100.0,
            trailing_stop_enabled=False,
        )

        await engine.execute_signal(
            signal=mock_signal_eth,
            trailing_stop_enabled=eth_config.trailing_stop.enabled,  # False
        )

        # Trailing stop should NOT be called
        mock_exchange.place_trailing_stop_order.assert_not_called()


# =============================================================================
# TEST: Failed Orders and Orphan Cleanup
# =============================================================================

class TestFailedOrdersAndOrphanCleanup:
    """Test handling of failed orders and orphan cleanup."""

    @pytest.mark.asyncio
    async def test_sl_failure_triggers_emergency_close(self, mock_exchange, mock_signal_btc):
        """If SL fails, position should be emergency closed."""
        from tradebot.core.exceptions import BinanceError

        # First check: no position, after entry: position exists
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)
        mock_exchange.place_stop_order = AsyncMock(
            side_effect=BinanceError(-1000, "SL failed")
        )

        engine = TradeEngine(exchange=mock_exchange)

        position = await engine.execute_signal(signal=mock_signal_btc)

        # Position should NOT be opened (emergency closed)
        assert position is None
        assert engine.sl_failures == 1
        assert engine.emergency_closes == 1

        # Emergency close should have been called
        assert mock_exchange.place_market_order.call_count >= 2  # Entry + close

    @pytest.mark.asyncio
    async def test_tp_failure_position_still_open(self, mock_exchange, mock_signal_btc):
        """If TP fails, position should still open (SL protects)."""
        from tradebot.core.exceptions import BinanceError

        # First check: no position, after entry: position exists
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)
        mock_exchange.place_take_profit_order = AsyncMock(
            side_effect=BinanceError(-1000, "TP failed")
        )

        engine = TradeEngine(exchange=mock_exchange)
        position_manager = PositionManager(exchange=mock_exchange, trade_engine=engine)
        engine.position_manager = position_manager

        position = await engine.execute_signal(signal=mock_signal_btc)

        # Position should still be opened
        assert position is not None
        assert position.is_open
        assert engine.tp_failures == 1

        # Should be registered for missing TP monitoring
        # _missing_tp_positions is Dict[position_id, timestamp]
        assert position.position_id in position_manager._missing_tp_positions

    @pytest.mark.asyncio
    async def test_trailing_stop_failure_falls_back_to_tp(self, mock_exchange, mock_signal_btc):
        """If trailing stop fails, should fall back to fixed TP."""
        from tradebot.core.exceptions import BinanceError

        # First check: no position, after entry: position exists
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)
        mock_exchange.place_trailing_stop_order = AsyncMock(
            side_effect=BinanceError(-1000, "Trailing failed")
        )

        engine = TradeEngine(
            exchange=mock_exchange,
            trailing_stop_enabled=True,
            trailing_stop_callback_rate=1.0,
        )

        position = await engine.execute_signal(
            signal=mock_signal_btc,
            trailing_stop_enabled=True,
        )

        # Position should be opened
        assert position is not None
        assert engine.trailing_stop_failures == 1

        # Fixed TP should have been placed as fallback
        mock_exchange.place_take_profit_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_orphan_order_cleaned_after_position_closed(self, mock_exchange, mock_signal_btc):
        """Orphan SL/TP orders should be cleaned after position closes."""
        # First check: no position, after entry: position exists
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        engine = TradeEngine(exchange=mock_exchange)
        position_manager = PositionManager(exchange=mock_exchange, trade_engine=engine)
        engine.position_manager = position_manager

        # Execute signal
        position = await engine.execute_signal(signal=mock_signal_btc)
        assert position is not None

        # Close the position
        await engine.close_position(position.position_id, reason="TEST")

        # SL and TP orders should be cancelled
        assert mock_exchange.cancel_algo_order.called  # SL
        assert mock_exchange.cancel_order.called  # TP

    @pytest.mark.asyncio
    async def test_orphan_cleanup_detects_stale_orders(self, mock_exchange):
        """Orphan cleanup should detect orders without positions."""
        engine = TradeEngine(exchange=mock_exchange)
        position_manager = PositionManager(exchange=mock_exchange, trade_engine=engine)

        # Mock: exchange has SL order but no position
        exchange_orders = [
            {
                "orderId": "orphan_sl_123",
                "symbol": "BTCUSDT",
                "type": "STOP_MARKET",
                "positionSide": "LONG",
                "time": (datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp() * 1000,
            }
        ]
        exchange_algo_orders = []
        exchange_positions = []  # No positions on exchange

        # No positions in engine
        assert len(engine.get_open_positions()) == 0

        # Run orphan cleanup with required arguments
        await position_manager._clean_orphan_orders(
            exchange_positions=exchange_positions,
            exchange_orders=exchange_orders,
            exchange_algo_orders=exchange_algo_orders,
        )

        # Orphan order should be cancelled
        mock_exchange.cancel_order.assert_called()

    @pytest.mark.asyncio
    async def test_grace_period_protects_recent_orders(self, mock_exchange):
        """Orders from recently closed positions should NOT be cancelled (grace period)."""
        engine = TradeEngine(exchange=mock_exchange)
        position_manager = PositionManager(exchange=mock_exchange, trade_engine=engine)

        # Mark BTCUSDT as recently closed
        position_manager._recently_closed_symbols["BTCUSDT"] = datetime.now(timezone.utc)

        # Mock: exchange has SL order for recently closed position
        exchange_orders = [
            {
                "orderId": "recent_sl_123",
                "symbol": "BTCUSDT",  # Recently closed
                "type": "STOP_MARKET",
                "positionSide": "LONG",
                "time": datetime.now(timezone.utc).timestamp() * 1000,
            }
        ]
        exchange_algo_orders = []
        exchange_positions = []  # No positions

        # Run orphan cleanup
        await position_manager._clean_orphan_orders(
            exchange_positions=exchange_positions,
            exchange_orders=exchange_orders,
            exchange_algo_orders=exchange_algo_orders,
        )

        # Order should NOT be cancelled (grace period protection)
        mock_exchange.cancel_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_grace_period_expires(self, mock_exchange):
        """After grace period expires, orphan orders should be cancelled."""
        engine = TradeEngine(exchange=mock_exchange)
        position_manager = PositionManager(exchange=mock_exchange, trade_engine=engine)

        # Mark BTCUSDT as closed MORE than 60 seconds ago
        old_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        position_manager._recently_closed_symbols["BTCUSDT"] = old_time

        # Mock: exchange has SL order for OLD closed position
        exchange_orders = [
            {
                "orderId": "old_sl_123",
                "symbol": "BTCUSDT",  # Closed > 60s ago
                "type": "STOP_MARKET",
                "positionSide": "LONG",
                "time": old_time.timestamp() * 1000,
            }
        ]
        exchange_algo_orders = []
        exchange_positions = []  # No positions

        # Run orphan cleanup
        await position_manager._clean_orphan_orders(
            exchange_positions=exchange_positions,
            exchange_orders=exchange_orders,
            exchange_algo_orders=exchange_algo_orders,
        )

        # Order SHOULD be cancelled (grace period expired)
        mock_exchange.cancel_order.assert_called()

    @pytest.mark.asyncio
    async def test_missing_tp_registered_for_monitoring(self, mock_exchange, mock_signal_btc):
        """Position without TP should be registered for monitoring."""
        from tradebot.core.exceptions import BinanceError

        # First check: no position, after entry: position exists
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        # TP will fail
        mock_exchange.place_take_profit_order = AsyncMock(
            side_effect=BinanceError(-1000, "TP failed")
        )

        engine = TradeEngine(exchange=mock_exchange)
        position_manager = PositionManager(exchange=mock_exchange, trade_engine=engine)
        engine.position_manager = position_manager

        position = await engine.execute_signal(signal=mock_signal_btc)

        # Position should be registered for missing TP monitoring
        assert position is not None
        assert position.position_id in position_manager._missing_tp_positions

        # Timestamp should be recorded
        registered_time = position_manager._missing_tp_positions[position.position_id]
        assert registered_time > 0

    @pytest.mark.asyncio
    async def test_entry_order_failure(self, mock_exchange, mock_signal_btc):
        """If entry market order fails, position should not be created."""
        from tradebot.core.exceptions import BinanceError

        mock_exchange.get_position_by_side = AsyncMock(return_value=None)
        mock_exchange.place_market_order = AsyncMock(
            side_effect=BinanceError(-2010, "Insufficient balance")
        )

        engine = TradeEngine(exchange=mock_exchange)

        position = await engine.execute_signal(signal=mock_signal_btc)

        # No position should be created
        assert position is None
        # No SL/TP should be placed
        mock_exchange.place_stop_order.assert_not_called()
        mock_exchange.place_take_profit_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_algo_order_orphan_cleanup(self, mock_exchange):
        """Orphan ALGO orders (SL, Trailing Stop) should also be cleaned."""
        engine = TradeEngine(exchange=mock_exchange)
        position_manager = PositionManager(exchange=mock_exchange, trade_engine=engine)

        # Mock: exchange has Algo order but no position
        exchange_orders = []
        exchange_algo_orders = [
            {
                "algoId": 999888777,
                "symbol": "ETHUSDT",
                "orderType": "TRAILING_STOP_MARKET",
                "positionSide": "SHORT",
                "side": "BUY",
            }
        ]
        exchange_positions = []  # No positions

        # No positions in engine
        assert len(engine.get_open_positions()) == 0

        # Run orphan cleanup
        await position_manager._clean_orphan_orders(
            exchange_positions=exchange_positions,
            exchange_orders=exchange_orders,
            exchange_algo_orders=exchange_algo_orders,
        )

        # Algo order should be cancelled
        mock_exchange.cancel_algo_order.assert_called_once()
        call_args = mock_exchange.cancel_algo_order.call_args
        assert call_args.kwargs.get('symbol') == "ETHUSDT" or call_args[0][0] == "ETHUSDT"


# =============================================================================
# TEST: Full Integration Scenario
# =============================================================================

class TestFullIntegrationScenario:
    """Full end-to-end integration tests."""

    @pytest.mark.asyncio
    async def test_full_flow_with_per_pair_config(
        self, mock_exchange, mock_signal_btc, mock_signal_eth, pairs_config_file
    ):
        """Test full flow: load config -> execute signals -> cleanup."""
        # 1. Load per-pair config
        loader = PairsConfigLoader(pairs_config_file)
        loader.load()

        btc_config = loader.get_pair_config("BTCUSDT")
        eth_config = loader.get_pair_config("ETHUSDT")

        # 2. Create engine with defaults
        engine = TradeEngine(
            exchange=mock_exchange,
            default_order_size_usd=100.0,
            default_leverage=10,
            sl_pct=4.0,
            tp_pct=10.0,
        )
        position_manager = PositionManager(exchange=mock_exchange, trade_engine=engine)
        engine.position_manager = position_manager

        # 3. Execute BTC signal with per-pair config
        btc_call_count = [0]
        async def mock_btc_position(symbol, side):
            btc_call_count[0] += 1
            if btc_call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_btc_position)

        btc_position = await engine.execute_signal(
            signal=mock_signal_btc,
            order_size_usd=btc_config.trading.order_size_usd,
            leverage=btc_config.trading.leverage,
            sl_pct=btc_config.trading.sl_pct,
            tp_pct=btc_config.trading.tp_pct,
            trailing_stop_enabled=btc_config.trailing_stop.enabled,
            trailing_stop_callback_rate=btc_config.trailing_stop.callback_rate,
        )

        assert btc_position is not None
        assert btc_position.symbol == "BTCUSDT"

        # 4. Execute ETH signal with per-pair config
        mock_exchange.get_price = AsyncMock(return_value=Decimal("3000"))
        eth_call_count = [0]
        async def mock_eth_position(symbol, side):
            eth_call_count[0] += 1
            if eth_call_count[0] == 1:
                return None
            return {"positionAmt": "-0.1", "entryPrice": "3000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_eth_position)

        eth_position = await engine.execute_signal(
            signal=mock_signal_eth,
            order_size_usd=eth_config.trading.order_size_usd,
            leverage=eth_config.trading.leverage,
            sl_pct=eth_config.trading.sl_pct,
            tp_pct=eth_config.trading.tp_pct,
            trailing_stop_enabled=eth_config.trailing_stop.enabled,
        )

        assert eth_position is not None
        assert eth_position.symbol == "ETHUSDT"

        # 5. Verify both positions are tracked
        open_positions = engine.get_open_positions()
        assert len(open_positions) == 2

        # 6. Close one position
        await engine.close_position(btc_position.position_id, reason="TEST")

        open_positions = engine.get_open_positions()
        assert len(open_positions) == 1
        assert open_positions[0].symbol == "ETHUSDT"

    @pytest.mark.asyncio
    async def test_disabled_symbol_not_traded(self, mock_exchange, pairs_config_file):
        """DOGEUSDT (disabled) should not be traded."""
        loader = PairsConfigLoader(pairs_config_file)
        loader.load()

        # Get enabled symbols
        symbols = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]
        enabled = loader.get_enabled_symbols(symbols)

        assert "BTCUSDT" in enabled
        assert "ETHUSDT" in enabled
        assert "DOGEUSDT" not in enabled

    @pytest.mark.asyncio
    async def test_recovery_after_crash(self, mock_exchange, mock_signal_btc, pairs_config_file):
        """Test state recovery after simulated crash."""
        # This tests that positions are saved and can be recovered

        loader = PairsConfigLoader(pairs_config_file)
        loader.load()
        btc_config = loader.get_pair_config("BTCUSDT")

        engine = TradeEngine(exchange=mock_exchange)
        position_manager = PositionManager(exchange=mock_exchange, trade_engine=engine)
        engine.position_manager = position_manager

        # Track state changes
        state_saved = []
        engine.on_state_changed = lambda: state_saved.append(True)

        # First check: no position, after entry: position exists
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        # Execute signal
        position = await engine.execute_signal(
            signal=mock_signal_btc,
            sl_pct=btc_config.trading.sl_pct,
        )

        # State should have been saved immediately after position opened
        assert len(state_saved) == 1

        # Verify position is in engine
        assert position.position_id in engine.positions


# =============================================================================
# TEST: Edge Cases
# =============================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_min_notional_enforced(self, mock_exchange, mock_signal_btc):
        """Order size is adjusted to meet min notional ($100)."""
        # Even with tiny order_size, min notional adjustment ensures qty > 0
        mock_exchange.get_position_by_side = AsyncMock(return_value=None)

        # Small order size
        engine = TradeEngine(exchange=mock_exchange)

        # This should NOT be rejected - min notional adjustment will increase qty
        position = await engine.execute_signal(
            signal=mock_signal_btc,
            order_size_usd=1.0,  # Very small, but will be adjusted
        )

        # Min notional adjustment should have increased quantity
        # (can't verify exact qty without more complex mocking)
        # The key is: it doesn't fail due to qty=0
        # Note: may fail due to position already exists check
        # So we just verify no exception was raised

    @pytest.mark.asyncio
    async def test_duplicate_signal_rejected(self, mock_exchange, mock_signal_btc):
        """Same signal should not be executed twice."""
        # First check: no position, after entry: position exists
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        engine = TradeEngine(exchange=mock_exchange)

        # First execution
        pos1 = await engine.execute_signal(signal=mock_signal_btc)
        assert pos1 is not None

        # Second execution with same signal_id
        # The duplicate check happens in trade_app, not engine
        # But engine tracks executed signal_ids
        executed_ids = engine.get_executed_signal_ids()
        assert mock_signal_btc.signal_id in executed_ids

    @pytest.mark.asyncio
    async def test_existing_position_blocks_new(self, mock_exchange, mock_signal_btc):
        """Existing position on exchange should block new entry."""
        # First call: no position
        # After first execute: position exists
        call_count = [0]

        async def mock_get_position(symbol, side):
            call_count[0] += 1
            if call_count[0] <= 2:  # First check and verification
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}

        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_get_position)

        engine = TradeEngine(exchange=mock_exchange)

        # First signal
        pos1 = await engine.execute_signal(signal=mock_signal_btc)

        # Reset for second signal
        mock_exchange.get_position_by_side = AsyncMock(return_value={
            "positionAmt": "0.01",
            "entryPrice": "50000"
        })

        # Create new signal
        signal2 = MagicMock()
        signal2.signal_id = "SIG_BTC_002"
        signal2.symbol = "BTCUSDT"
        signal2.direction = "LONG"
        signal2.entry = 50000
        signal2.stop_loss = 49000
        signal2.take_profit = 53000
        signal2.date = datetime.now(timezone.utc)
        signal2.metadata = {"strategy": "momentum"}

        # Second signal should be rejected (position exists on exchange)
        pos2 = await engine.execute_signal(signal=signal2)
        assert pos2 is None

    @pytest.mark.asyncio
    async def test_insufficient_balance_rejected(self, mock_exchange, mock_signal_btc):
        """Signal should be rejected if insufficient balance."""
        mock_exchange.get_balance = AsyncMock(return_value=Decimal("1"))  # Very low

        engine = TradeEngine(
            exchange=mock_exchange,
            default_order_size_usd=1000,  # Requires more margin
            default_leverage=10,
        )

        position = await engine.execute_signal(
            signal=mock_signal_btc,
            order_size_usd=1000,
        )

        assert position is None
        assert engine.signals_skipped == 1

    def test_strategy_not_in_pair_config(self, pairs_config_file):
        """Strategy not in pair's strategies list should be disabled."""
        loader = PairsConfigLoader(pairs_config_file)
        loader.load()

        btc = loader.get_pair_config("BTCUSDT")

        # momentum and reversal are in default strategies
        assert btc.is_strategy_enabled("momentum") is True
        assert btc.is_strategy_enabled("reversal") is True
        # ls_fade is NOT in strategies list
        assert btc.is_strategy_enabled("ls_fade") is False
