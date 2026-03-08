# -*- coding: utf-8 -*-
"""
Tests for engine/state_manager.py

Testing:
- save_state() - serialization of positions and stats
- load_state() - deserialization from file
- restore_and_sync() - full sync with exchange
- Position matching logic
- SL/TP order detection
- Missing SL/TP creation
- Expired position closing
- Metrics restoration
"""

import pytest
import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tradebot.engine.state_manager import StateManager
from tradebot.engine.trade_engine import TradeEngine
from tradebot.engine.position_manager import PositionManager
from tradebot.engine.metrics import MetricsTracker
from tradebot.core.models import (
    Position,
    PositionSide,
    PositionStatus,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def temp_state_file():
    """Create temporary state file."""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    yield path
    # Cleanup
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def mock_trade_engine(mock_exchange):
    """Create mock TradeEngine."""
    engine = TradeEngine(exchange=mock_exchange)
    return engine


@pytest.fixture
def mock_position_manager(mock_exchange, mock_trade_engine):
    """Create mock PositionManager."""
    manager = PositionManager(
        exchange=mock_exchange,
        trade_engine=mock_trade_engine,
    )
    return manager


@pytest.fixture
def mock_metrics_tracker():
    """Create mock MetricsTracker."""
    return MetricsTracker(initial_balance=1000.0)


@pytest.fixture
def state_manager(
    mock_trade_engine,
    mock_position_manager,
    mock_exchange,
    mock_metrics_tracker,
    temp_state_file,
):
    """Create StateManager with mocks."""
    return StateManager(
        trade_engine=mock_trade_engine,
        position_manager=mock_position_manager,
        exchange=mock_exchange,
        metrics_tracker=mock_metrics_tracker,
        state_file=temp_state_file,
    )


@pytest.fixture
def open_position():
    """Create an open position for testing."""
    return Position(
        position_id="POS_TEST_001",
        signal_id="SIG_TEST_001",
        symbol="BTCUSDT",
        side=PositionSide.LONG,
        quantity=0.001,
        entry_price=50000.0,
        stop_loss=48000.0,
        take_profit=55000.0,
        status=PositionStatus.OPEN,
        entry_order_id="ENTRY_001",
        sl_order_id="SL_001",
        tp_order_id="TP_001",
        opened_at=datetime.utcnow() - timedelta(days=2),
        strategy="test_strategy",
        regime_action="FULL",
        max_hold_days=14,
    )


# =============================================================================
# TEST SAVE_STATE
# =============================================================================

class TestSaveState:
    """Test state saving functionality."""

    def test_save_state_creates_file(self, state_manager, temp_state_file):
        """save_state() should create a JSON file."""
        result = state_manager.save_state()

        assert result is True
        assert os.path.exists(temp_state_file)

    def test_save_state_includes_version(self, state_manager, temp_state_file):
        """Saved state should include version."""
        state_manager.save_state()

        with open(temp_state_file, "r") as f:
            state = json.load(f)

        assert "version" in state
        assert state["version"] == "1.1"

    def test_save_state_includes_positions(
        self, state_manager, mock_trade_engine, open_position, temp_state_file
    ):
        """Saved state should include all positions."""
        mock_trade_engine.positions[open_position.position_id] = open_position
        state_manager.save_state()

        with open(temp_state_file, "r") as f:
            state = json.load(f)

        assert "positions" in state
        assert open_position.position_id in state["positions"]

        saved_pos = state["positions"][open_position.position_id]
        assert saved_pos["symbol"] == "BTCUSDT"
        assert saved_pos["side"] == "LONG"
        assert saved_pos["entry_price"] == 50000.0

    def test_save_state_includes_stats(self, state_manager, temp_state_file):
        """Saved state should include trade engine and position manager stats."""
        state_manager.save_state()

        with open(temp_state_file, "r") as f:
            state = json.load(f)

        assert "trade_engine_stats" in state
        assert "position_manager_stats" in state

    def test_save_state_includes_missing_tp(
        self, state_manager, mock_position_manager, open_position, temp_state_file
    ):
        """Saved state should include missing TP positions."""
        import time
        mock_position_manager._missing_tp_positions[open_position.position_id] = time.time()

        state_manager.save_state()

        with open(temp_state_file, "r") as f:
            state = json.load(f)

        assert "missing_tp_positions" in state
        assert open_position.position_id in state["missing_tp_positions"]

    def test_save_state_includes_metrics(
        self, state_manager, mock_metrics_tracker, temp_state_file
    ):
        """Saved state should include metrics tracker data."""
        mock_metrics_tracker.record_trade(
            symbol="BTCUSDT",
            direction="LONG",
            entry_price=50000.0,
            exit_price=55000.0,
            quantity=0.001,
            realized_pnl=5.0,
            exit_reason="TP",
            strategy="test",
        )

        state_manager.save_state()

        with open(temp_state_file, "r") as f:
            state = json.load(f)

        assert "metrics" in state
        assert state["metrics"] is not None
        assert len(state["metrics"]["trades"]) == 1


# =============================================================================
# TEST LOAD_STATE
# =============================================================================

class TestLoadState:
    """Test state loading functionality."""

    def test_load_state_returns_none_if_no_file(self, state_manager):
        """load_state() should return None if file doesn't exist."""
        # Use a path that doesn't exist
        state_manager.state_file = "/nonexistent/path/state.json"

        result = state_manager.load_state()

        assert result is None

    def test_load_state_parses_json(self, state_manager, temp_state_file):
        """load_state() should parse saved JSON."""
        # Create a valid state file
        test_state = {
            "saved_at": "2024-01-01T00:00:00",
            "version": "1.1",
            "positions": {"POS_1": {"symbol": "BTCUSDT"}},
        }
        with open(temp_state_file, "w") as f:
            json.dump(test_state, f)

        result = state_manager.load_state()

        assert result is not None
        assert result["version"] == "1.1"
        assert "POS_1" in result["positions"]

    def test_load_state_handles_corrupt_json(self, state_manager, temp_state_file):
        """load_state() should return None for corrupt JSON."""
        with open(temp_state_file, "w") as f:
            f.write("not valid json {{{")

        result = state_manager.load_state()

        assert result is None


# =============================================================================
# TEST RESTORE_AND_SYNC
# =============================================================================

class TestRestoreAndSync:
    """Test full state restoration and exchange sync."""

    @pytest.mark.asyncio
    async def test_restore_with_no_saved_state(
        self, state_manager, mock_exchange
    ):
        """restore_and_sync() should work with no saved state."""
        # No state file exists
        mock_exchange.get_all_positions = AsyncMock(return_value=[])
        mock_exchange.get_open_orders = AsyncMock(return_value=[])

        stats = await state_manager.restore_and_sync()

        assert stats["positions_restored"] == 0
        assert stats["positions_from_exchange"] == 0

    @pytest.mark.asyncio
    async def test_restore_syncs_exchange_position(
        self, state_manager, mock_exchange, mock_trade_engine, mock_position_manager
    ):
        """restore_and_sync() should sync position from exchange."""
        # Exchange has one position
        mock_exchange.get_all_positions = AsyncMock(return_value=[
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.001",
                "entryPrice": "50000.0",
                "positionSide": "LONG",
            }
        ])
        mock_exchange.get_open_orders = AsyncMock(return_value=[
            {
                "orderId": "SL_FROM_EXCHANGE",
                "symbol": "BTCUSDT",
                "type": "STOP_MARKET",
                "side": "SELL",
                "positionSide": "LONG",
                "stopPrice": "48000.0",
                "reduceOnly": True,
            }
        ])

        stats = await state_manager.restore_and_sync()

        assert stats["positions_from_exchange"] == 1
        assert stats["sl_orders_found"] == 1
        assert len(mock_trade_engine.positions) == 1

    @pytest.mark.asyncio
    async def test_restore_creates_missing_sl(
        self, state_manager, mock_exchange, mock_trade_engine, temp_state_file
    ):
        """restore_and_sync() should create missing SL order."""
        # Create saved state with SL price
        saved_state = {
            "saved_at": datetime.utcnow().isoformat(),
            "version": "1.1",
            "positions": {
                "POS_001": {
                    "position_id": "POS_001",
                    "signal_id": "SIG_001",
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": 0.001,
                    "entry_price": 50000.0,
                    "stop_loss": 48000.0,
                    "take_profit": 55000.0,
                    "status": "OPEN",
                    "opened_at": (datetime.utcnow() - timedelta(days=2)).isoformat(),
                    "max_hold_days": 14,
                }
            },
            "trade_engine_stats": {},
            "position_manager_stats": {},
            "missing_tp_positions": {},
        }
        with open(temp_state_file, "w") as f:
            json.dump(saved_state, f)

        # Exchange has position but NO SL order
        mock_exchange.get_all_positions = AsyncMock(return_value=[
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.001",
                "entryPrice": "50000.0",
                "positionSide": "LONG",
            }
        ])
        mock_exchange.get_open_orders = AsyncMock(return_value=[])  # No orders!

        mock_exchange.place_stop_order = AsyncMock(return_value={
            "orderId": "NEW_SL_001"
        })

        stats = await state_manager.restore_and_sync()

        assert stats["sl_orders_created"] == 1
        mock_exchange.place_stop_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_restore_closes_expired_position(
        self, state_manager, mock_exchange, mock_trade_engine, temp_state_file
    ):
        """restore_and_sync() should close expired positions."""
        # Create saved state with OLD position
        saved_state = {
            "saved_at": datetime.utcnow().isoformat(),
            "version": "1.1",
            "positions": {
                "POS_EXPIRED": {
                    "position_id": "POS_EXPIRED",
                    "signal_id": "SIG_001",
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": 0.001,
                    "entry_price": 50000.0,
                    "stop_loss": 48000.0,
                    "take_profit": 55000.0,
                    "status": "OPEN",
                    "opened_at": (datetime.utcnow() - timedelta(days=20)).isoformat(),  # 20 days ago!
                    "max_hold_days": 14,
                }
            },
            "trade_engine_stats": {},
            "position_manager_stats": {},
            "missing_tp_positions": {},
        }
        with open(temp_state_file, "w") as f:
            json.dump(saved_state, f)

        # Exchange has the position
        mock_exchange.get_all_positions = AsyncMock(return_value=[
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.001",
                "entryPrice": "50000.0",
                "positionSide": "LONG",
            }
        ])
        mock_exchange.get_open_orders = AsyncMock(return_value=[])

        stats = await state_manager.restore_and_sync()

        assert stats["positions_closed_expired"] == 1
        # Position should NOT be registered (was closed)
        assert len(mock_trade_engine.positions) == 0

    @pytest.mark.asyncio
    async def test_restore_removes_state_file_after_sync(
        self, state_manager, mock_exchange, temp_state_file
    ):
        """State file should be removed after successful sync."""
        # Create a state file
        with open(temp_state_file, "w") as f:
            json.dump({"version": "1.1", "positions": {}}, f)

        mock_exchange.get_all_positions = AsyncMock(return_value=[])
        mock_exchange.get_open_orders = AsyncMock(return_value=[])

        await state_manager.restore_and_sync()

        assert not os.path.exists(temp_state_file)


# =============================================================================
# TEST POSITION MATCHING
# =============================================================================

class TestPositionMatching:
    """Test saved position matching logic."""

    def test_find_matching_saved_position_exact_match(self, state_manager):
        """Should match position with same symbol, side, and ~entry price."""
        saved_positions = {
            "POS_001": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "status": "OPEN",
                "entry_price": 50000.0,
            }
        }

        result = state_manager._find_matching_saved_position(
            symbol="BTCUSDT",
            position_side=PositionSide.LONG,
            quantity=0.001,
            entry_price=50000.0,
            saved_positions=saved_positions,
        )

        assert result is not None
        assert result["symbol"] == "BTCUSDT"

    def test_find_matching_saved_position_close_price(self, state_manager):
        """Should match position with entry price within 1%."""
        saved_positions = {
            "POS_001": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "status": "OPEN",
                "entry_price": 50000.0,
            }
        }

        # Price within 1%
        result = state_manager._find_matching_saved_position(
            symbol="BTCUSDT",
            position_side=PositionSide.LONG,
            quantity=0.001,
            entry_price=50300.0,  # 0.6% difference
            saved_positions=saved_positions,
        )

        assert result is not None

    def test_find_matching_saved_position_no_match_wrong_symbol(self, state_manager):
        """Should not match position with different symbol."""
        saved_positions = {
            "POS_001": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "status": "OPEN",
                "entry_price": 50000.0,
            }
        }

        result = state_manager._find_matching_saved_position(
            symbol="ETHUSDT",  # Different symbol
            position_side=PositionSide.LONG,
            quantity=0.001,
            entry_price=50000.0,
            saved_positions=saved_positions,
        )

        assert result is None

    def test_find_matching_saved_position_no_match_closed(self, state_manager):
        """Should not match closed positions."""
        saved_positions = {
            "POS_001": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "status": "CLOSED",  # Closed!
                "entry_price": 50000.0,
            }
        }

        result = state_manager._find_matching_saved_position(
            symbol="BTCUSDT",
            position_side=PositionSide.LONG,
            quantity=0.001,
            entry_price=50000.0,
            saved_positions=saved_positions,
        )

        assert result is None


# =============================================================================
# TEST SL/TP ORDER DETECTION
# =============================================================================

class TestOrderDetection:
    """Test SL/TP order detection from exchange."""

    def test_find_sl_order_for_long(self, state_manager):
        """Should find STOP_MARKET SELL order for LONG position."""
        orders = [
            {
                "orderId": "SL_001",
                "type": "STOP_MARKET",
                "side": "SELL",
                "positionSide": "LONG",
                "reduceOnly": True,
            }
        ]

        result = state_manager._find_sl_order(orders, PositionSide.LONG, 0.001)

        assert result is not None
        assert result["orderId"] == "SL_001"

    def test_find_sl_order_for_short(self, state_manager):
        """Should find STOP_MARKET BUY order for SHORT position."""
        orders = [
            {
                "orderId": "SL_002",
                "type": "STOP_MARKET",
                "side": "BUY",
                "positionSide": "SHORT",
                "reduceOnly": True,
            }
        ]

        result = state_manager._find_sl_order(orders, PositionSide.SHORT, 0.001)

        assert result is not None
        assert result["orderId"] == "SL_002"

    def test_find_sl_order_no_match(self, state_manager):
        """Should return None if no matching SL order."""
        orders = [
            {
                "orderId": "TP_001",
                "type": "TAKE_PROFIT_MARKET",  # Wrong type
                "side": "SELL",
                "positionSide": "LONG",
                "reduceOnly": True,
            }
        ]

        result = state_manager._find_sl_order(orders, PositionSide.LONG, 0.001)

        assert result is None

    def test_find_tp_order_for_long(self, state_manager):
        """Should find TAKE_PROFIT_MARKET SELL order for LONG position."""
        orders = [
            {
                "orderId": "TP_001",
                "type": "TAKE_PROFIT_MARKET",
                "side": "SELL",
                "positionSide": "LONG",
                "reduceOnly": True,
            }
        ]

        result = state_manager._find_tp_order(orders, PositionSide.LONG, 0.001)

        assert result is not None
        assert result["orderId"] == "TP_001"


# =============================================================================
# TEST POSITION SERIALIZATION
# =============================================================================

class TestPositionSerialization:
    """Test position serialization for JSON."""

    def test_serialize_position_includes_all_fields(self, state_manager, open_position):
        """Serialized position should include all important fields."""
        result = state_manager._serialize_position(open_position)

        assert result["position_id"] == "POS_TEST_001"
        assert result["signal_id"] == "SIG_TEST_001"
        assert result["symbol"] == "BTCUSDT"
        assert result["side"] == "LONG"
        assert result["quantity"] == 0.001
        assert result["entry_price"] == 50000.0
        assert result["stop_loss"] == 48000.0
        assert result["take_profit"] == 55000.0
        assert result["status"] == "OPEN"
        assert result["strategy"] == "test_strategy"
        assert result["max_hold_days"] == 14

    def test_serialize_position_converts_datetime(self, state_manager, open_position):
        """Datetime fields should be converted to ISO format."""
        result = state_manager._serialize_position(open_position)

        assert result["opened_at"] is not None
        # Should be parseable as datetime
        datetime.fromisoformat(result["opened_at"])

    def test_serialize_closed_position(self, state_manager, closed_position):
        """Closed position should include exit fields."""
        result = state_manager._serialize_position(closed_position)

        assert result["status"] == "CLOSED"
        assert result["exit_price"] == 55000.0
        assert result["exit_reason"] == "TP"
        assert result["realized_pnl"] == 5.0


# =============================================================================
# TEST METRICS RESTORATION
# =============================================================================

class TestMetricsRestoration:
    """Test MetricsTracker restoration from state."""

    @pytest.mark.asyncio
    async def test_metrics_restored_from_state(
        self, state_manager, mock_exchange, mock_metrics_tracker, temp_state_file
    ):
        """MetricsTracker should be restored from saved state."""
        # Create saved state with metrics
        saved_state = {
            "saved_at": datetime.utcnow().isoformat(),
            "version": "1.1",
            "positions": {},
            "trade_engine_stats": {},
            "position_manager_stats": {},
            "missing_tp_positions": {},
            "metrics": {
                "initial_balance": 1000.0,
                "start_time": datetime.utcnow().isoformat(),
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "direction": "LONG",
                        "entry_price": 50000.0,
                        "exit_price": 55000.0,
                        "quantity": 0.001,
                        "realized_pnl": 5.0,
                        "exit_reason": "TP",
                        "strategy": "test",
                        "opened_at": datetime.utcnow().isoformat(),
                        "closed_at": datetime.utcnow().isoformat(),
                        "hold_duration_hours": 1.0,
                    }
                ],
                "total_stats": {
                    "total_trades": 1,
                    "winning_trades": 1,
                    "losing_trades": 0,
                    "total_pnl": 5.0,
                    "gross_profit": 5.0,
                    "gross_loss": 0.0,
                },
            },
        }
        with open(temp_state_file, "w") as f:
            json.dump(saved_state, f)

        mock_exchange.get_all_positions = AsyncMock(return_value=[])
        mock_exchange.get_open_orders = AsyncMock(return_value=[])

        await state_manager.restore_and_sync()

        # Check metrics were restored
        assert len(mock_metrics_tracker.trades) == 1
        assert mock_metrics_tracker.total_stats.total_trades == 1
        assert mock_metrics_tracker.total_stats.total_pnl == 5.0


# =============================================================================
# TEST GET SYNC STATS
# =============================================================================

class TestGetSyncStats:
    """Test sync statistics retrieval."""

    def test_get_sync_stats_returns_copy(self, state_manager):
        """get_sync_stats() should return a copy of stats."""
        stats = state_manager.get_sync_stats()

        # Modify the returned dict
        stats["test_field"] = 999

        # Original should be unchanged
        assert "test_field" not in state_manager._sync_stats

    def test_get_sync_stats_includes_all_fields(self, state_manager):
        """get_sync_stats() should include all expected fields."""
        stats = state_manager.get_sync_stats()

        expected_fields = [
            "positions_restored",
            "positions_from_exchange",
            "sl_orders_found",
            "tp_orders_found",
            "sl_orders_created",
            "tp_orders_created",
            "positions_closed_expired",
        ]

        for field in expected_fields:
            assert field in stats
