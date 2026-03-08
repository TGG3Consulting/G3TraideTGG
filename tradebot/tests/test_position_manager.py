# -*- coding: utf-8 -*-
"""
Tests for engine/position_manager.py

Testing:
- WebSocket ORDER_TRADE_UPDATE handling (SL/TP fill)
- WebSocket ACCOUNT_UPDATE handling (fallback close detection)
- Position registration/unregistration
- Timeout check (max_hold_days)
- Missing TP monitoring
- REST sync with exchange
- Callback invocation
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tradebot.engine.position_manager import PositionManager
from tradebot.engine.trade_engine import TradeEngine
from tradebot.core.models import (
    Position,
    PositionSide,
    PositionStatus,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def mock_trade_engine(mock_exchange):
    """Create mock TradeEngine."""
    engine = TradeEngine(exchange=mock_exchange)
    return engine


@pytest.fixture
def position_manager(mock_exchange, mock_trade_engine):
    """Create PositionManager with mocks."""
    manager = PositionManager(
        exchange=mock_exchange,
        trade_engine=mock_trade_engine,
    )
    return manager


@pytest.fixture
def open_long_position():
    """Create an open LONG position for testing."""
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
        opened_at=datetime.utcnow(),
    )


# =============================================================================
# TEST POSITION REGISTRATION
# =============================================================================

class TestPositionRegistration:
    """Test position registration and unregistration."""

    def test_register_position_adds_order_mapping(self, position_manager, open_long_position):
        """register_position() should add SL/TP order IDs to mapping."""
        position_manager.register_position(open_long_position)

        assert open_long_position.sl_order_id in position_manager._order_to_position
        assert open_long_position.tp_order_id in position_manager._order_to_position
        assert position_manager._order_to_position["SL_001"] == "POS_TEST_001"
        assert position_manager._order_to_position["TP_001"] == "POS_TEST_001"

    def test_unregister_position_removes_order_mapping(self, position_manager, open_long_position):
        """unregister_position() should remove SL/TP order IDs from mapping."""
        position_manager.register_position(open_long_position)
        position_manager.unregister_position(open_long_position)

        assert "SL_001" not in position_manager._order_to_position
        assert "TP_001" not in position_manager._order_to_position

    def test_unregister_removes_from_missing_tp(self, position_manager, open_long_position):
        """unregister_position() should also remove from missing_tp tracking."""
        position_manager.register_position(open_long_position)
        position_manager.register_missing_tp(open_long_position)

        assert open_long_position.position_id in position_manager._missing_tp_positions

        position_manager.unregister_position(open_long_position)

        assert open_long_position.position_id not in position_manager._missing_tp_positions


# =============================================================================
# TEST ORDER_TRADE_UPDATE HANDLING
# =============================================================================

class TestOrderTradeUpdateHandling:
    """Test WebSocket ORDER_TRADE_UPDATE event handling."""

    def test_sl_filled_closes_position(self, position_manager, mock_trade_engine, open_long_position):
        """SL fill should close position with exit_reason='SL'."""
        # Register position
        mock_trade_engine.positions[open_long_position.position_id] = open_long_position
        position_manager.register_position(open_long_position)

        # Simulate SL fill event
        event = {
            "e": "ORDER_TRADE_UPDATE",
            "T": 1234567890123,
            "o": {
                "s": "BTCUSDT",
                "i": "SL_001",  # SL order ID
                "X": "FILLED",
                "o": "STOP_MARKET",
                "ap": "48000.00",
                "rp": "-2.00",  # Realized PnL
                "z": "0.001",
            }
        }

        position_manager._handle_order_update(event)

        assert open_long_position.status == PositionStatus.CLOSED
        assert open_long_position.exit_reason == "SL"
        assert open_long_position.exit_price == 48000.0
        assert open_long_position.realized_pnl == -2.0
        assert position_manager._stats["positions_closed_sl"] == 1

    def test_tp_filled_closes_position(self, position_manager, mock_trade_engine, open_long_position):
        """TP fill should close position with exit_reason='TP'."""
        mock_trade_engine.positions[open_long_position.position_id] = open_long_position
        position_manager.register_position(open_long_position)

        event = {
            "e": "ORDER_TRADE_UPDATE",
            "T": 1234567890123,
            "o": {
                "s": "BTCUSDT",
                "i": "TP_001",  # TP order ID
                "X": "FILLED",
                "o": "TAKE_PROFIT_MARKET",
                "ap": "55000.00",
                "rp": "5.00",
                "z": "0.001",
            }
        }

        position_manager._handle_order_update(event)

        assert open_long_position.status == PositionStatus.CLOSED
        assert open_long_position.exit_reason == "TP"
        assert open_long_position.exit_price == 55000.0
        assert open_long_position.realized_pnl == 5.0
        assert position_manager._stats["positions_closed_tp"] == 1

    def test_partial_fill_does_not_close(self, position_manager, mock_trade_engine, open_long_position):
        """PARTIALLY_FILLED should NOT close position."""
        mock_trade_engine.positions[open_long_position.position_id] = open_long_position
        position_manager.register_position(open_long_position)

        event = {
            "e": "ORDER_TRADE_UPDATE",
            "T": 1234567890123,
            "o": {
                "s": "BTCUSDT",
                "i": "SL_001",
                "X": "PARTIALLY_FILLED",  # Not fully filled
                "o": "STOP_MARKET",
                "ap": "48000.00",
                "z": "0.0005",
                "q": "0.001",
                "L": "48000.00",
            }
        }

        position_manager._handle_order_update(event)

        assert open_long_position.status == PositionStatus.OPEN  # Still open!

    def test_untracked_order_ignored(self, position_manager, mock_trade_engine):
        """Orders not in our mapping should be ignored."""
        event = {
            "e": "ORDER_TRADE_UPDATE",
            "o": {
                "i": "UNKNOWN_ORDER",
                "X": "FILLED",
            }
        }

        position_manager._handle_order_update(event)

        assert position_manager._stats["positions_closed_sl"] == 0
        assert position_manager._stats["positions_closed_tp"] == 0

    def test_already_closed_position_ignored(self, position_manager, mock_trade_engine, open_long_position):
        """Already closed position should be ignored."""
        open_long_position.status = PositionStatus.CLOSED
        mock_trade_engine.positions[open_long_position.position_id] = open_long_position
        position_manager.register_position(open_long_position)

        event = {
            "e": "ORDER_TRADE_UPDATE",
            "o": {
                "i": "SL_001",
                "X": "FILLED",
                "ap": "48000.00",
                "rp": "-2.00",
            }
        }

        # Should not error, should just skip
        position_manager._handle_order_update(event)

        # No additional closes counted
        assert position_manager._stats["positions_closed_sl"] == 0

    def test_position_closed_callback_invoked(self, position_manager, mock_trade_engine, open_long_position):
        """on_position_closed callback should be called."""
        mock_trade_engine.positions[open_long_position.position_id] = open_long_position
        position_manager.register_position(open_long_position)

        callback_data = []
        position_manager.on_position_closed = lambda pos, reason, pnl: callback_data.append((pos, reason, pnl))

        event = {
            "e": "ORDER_TRADE_UPDATE",
            "o": {
                "i": "SL_001",
                "X": "FILLED",
                "ap": "48000.00",
                "rp": "-2.00",
            }
        }

        position_manager._handle_order_update(event)

        assert len(callback_data) == 1
        assert callback_data[0][0] is open_long_position
        assert callback_data[0][1] == "SL"
        assert callback_data[0][2] == -2.0


# =============================================================================
# TEST ACCOUNT_UPDATE HANDLING
# =============================================================================

class TestAccountUpdateHandling:
    """Test WebSocket ACCOUNT_UPDATE event handling."""

    def test_closed_position_detected_via_account_update(
        self, position_manager, mock_trade_engine, open_long_position
    ):
        """Position with amount=0 in ACCOUNT_UPDATE should be closed."""
        mock_trade_engine.positions[open_long_position.position_id] = open_long_position
        position_manager.register_position(open_long_position)

        event = {
            "e": "ACCOUNT_UPDATE",
            "a": {
                "P": [
                    {
                        "s": "BTCUSDT",
                        "pa": "0",  # Position amount = 0 (closed)
                        "ep": "0.00",
                        "cr": "-2.00",
                        "ps": "LONG"
                    }
                ]
            }
        }

        position_manager._handle_account_update(event)

        assert open_long_position.status == PositionStatus.CLOSED
        assert open_long_position.exit_reason == "ACCOUNT_UPDATE"

    def test_stats_updated_on_account_update(self, position_manager):
        """account_updates_received stat should increment."""
        event = {
            "e": "ACCOUNT_UPDATE",
            "a": {"P": []}
        }

        position_manager._handle_account_update(event)

        assert position_manager._stats["account_updates_received"] == 1


# =============================================================================
# TEST TIMEOUT CHECK (MAX HOLD DAYS)
# =============================================================================

class TestTimeoutCheck:
    """Test max_hold_days timeout checking."""

    @pytest.fixture
    def expired_position(self):
        """Position that exceeded max_hold_days."""
        return Position(
            position_id="POS_EXPIRED_001",
            signal_id="SIG_EXPIRED_001",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=0.001,
            entry_price=50000.0,
            stop_loss=48000.0,
            take_profit=55000.0,
            status=PositionStatus.OPEN,
            opened_at=datetime.utcnow() - timedelta(days=15),  # 15 days ago
            max_hold_days=14,
        )

    @pytest.mark.asyncio
    async def test_expired_position_closed(
        self, position_manager, mock_trade_engine, mock_exchange, expired_position
    ):
        """Expired position should be closed by _close_position_timeout."""
        mock_trade_engine.positions[expired_position.position_id] = expired_position
        position_manager.register_position(expired_position)

        # Reset mock
        mock_exchange.place_market_order.reset_mock()
        mock_exchange.place_market_order.return_value = {
            "orderId": "CLOSE_123",
            "avgPrice": "49000.00",
        }

        result = await position_manager._close_position_timeout(expired_position)

        assert result is True
        assert expired_position.status == PositionStatus.CLOSED
        assert expired_position.exit_reason == "TIMEOUT"
        assert position_manager._stats["positions_closed_timeout"] == 1

    @pytest.mark.asyncio
    async def test_timeout_close_cancels_orders(
        self, position_manager, mock_trade_engine, mock_exchange, expired_position
    ):
        """Timeout close should cancel SL and TP orders."""
        expired_position.sl_order_id = "SL_EXP"
        expired_position.tp_order_id = "TP_EXP"
        mock_trade_engine.positions[expired_position.position_id] = expired_position

        await position_manager._close_position_timeout(expired_position)

        # Should have called cancel_order for SL and TP
        assert mock_exchange.cancel_order.call_count >= 2


# =============================================================================
# TEST MISSING TP MONITORING
# =============================================================================

class TestMissingTPMonitoring:
    """Test missing TP monitoring functionality."""

    def test_register_missing_tp_adds_to_tracking(self, position_manager, open_long_position):
        """register_missing_tp() should add position to tracking."""
        position_manager.register_missing_tp(open_long_position)

        assert open_long_position.position_id in position_manager._missing_tp_positions

    @pytest.mark.asyncio
    async def test_missing_tp_position_closed_after_timeout(
        self, position_manager, mock_trade_engine, mock_exchange, open_long_position
    ):
        """Position without TP should be closed after _missing_tp_max_wait."""
        open_long_position.tp_order_id = ""  # No TP
        mock_trade_engine.positions[open_long_position.position_id] = open_long_position

        mock_exchange.place_market_order.return_value = {
            "orderId": "CLOSE_123",
            "avgPrice": "51000.00",
        }

        result = await position_manager._close_position_missing_tp(open_long_position)

        assert result is True
        assert open_long_position.status == PositionStatus.CLOSED
        assert open_long_position.exit_reason == "MISSING_TP"
        assert position_manager._stats["positions_closed_missing_tp"] == 1

    @pytest.mark.asyncio
    async def test_check_tp_exists_on_exchange(
        self, position_manager, mock_exchange, open_long_position
    ):
        """_check_tp_exists_on_exchange should detect TP order."""
        # Mock exchange returns TP order
        mock_exchange.get_open_orders = AsyncMock(return_value=[
            {
                "orderId": "TP_FROM_EXCHANGE",
                "type": "TAKE_PROFIT_MARKET",
                "side": "SELL",
                "positionSide": "LONG",
                "symbol": "BTCUSDT",
            }
        ])
        open_long_position.tp_order_id = ""  # Originally no TP

        result = await position_manager._check_tp_exists_on_exchange(open_long_position)

        assert result is True
        assert open_long_position.tp_order_id == "TP_FROM_EXCHANGE"


# =============================================================================
# TEST REST SYNC
# =============================================================================

class TestRESTSync:
    """Test REST synchronization with exchange."""

    @pytest.mark.asyncio
    async def test_rest_sync_detects_closed_position(
        self, position_manager, mock_trade_engine, mock_exchange, open_long_position
    ):
        """REST sync should detect position closed on exchange but not locally."""
        mock_trade_engine.positions[open_long_position.position_id] = open_long_position
        position_manager.register_position(open_long_position)

        # Exchange returns NO positions (position was closed)
        mock_exchange.get_all_positions = AsyncMock(return_value=[])
        mock_exchange.get_open_orders = AsyncMock(return_value=[])
        mock_exchange.get_price = AsyncMock(return_value=Decimal("49000"))

        await position_manager._perform_rest_sync()

        assert open_long_position.status == PositionStatus.CLOSED
        assert open_long_position.exit_reason == "SYNC_FIX"
        assert position_manager._stats["rest_sync_positions_fixed"] == 1

    @pytest.mark.asyncio
    async def test_rest_sync_detects_missing_sl_order(
        self, position_manager, mock_trade_engine, mock_exchange, open_long_position
    ):
        """REST sync should detect SL order filled (missing) on exchange."""
        mock_trade_engine.positions[open_long_position.position_id] = open_long_position
        position_manager.register_position(open_long_position)

        # Exchange returns position but NO orders (SL was filled)
        mock_exchange.get_all_positions = AsyncMock(return_value=[
            {"symbol": "BTCUSDT", "positionAmt": "0.001"}  # Position exists
        ])
        mock_exchange.get_open_orders = AsyncMock(return_value=[])  # No orders!
        mock_exchange.get_price = AsyncMock(return_value=Decimal("48000"))

        await position_manager._perform_rest_sync()

        assert open_long_position.status == PositionStatus.CLOSED
        assert position_manager._stats["rest_sync_orders_fixed"] == 1

    @pytest.mark.asyncio
    async def test_rest_sync_in_sync_no_changes(
        self, position_manager, mock_trade_engine, mock_exchange, open_long_position
    ):
        """REST sync should not change anything if already in sync."""
        mock_trade_engine.positions[open_long_position.position_id] = open_long_position
        position_manager.register_position(open_long_position)

        # Exchange returns matching state
        mock_exchange.get_all_positions = AsyncMock(return_value=[
            {"symbol": "BTCUSDT", "positionAmt": "0.001"}
        ])
        mock_exchange.get_open_orders = AsyncMock(return_value=[
            {"orderId": "SL_001"},
            {"orderId": "TP_001"},
        ])

        await position_manager._perform_rest_sync()

        assert open_long_position.status == PositionStatus.OPEN
        assert position_manager._stats["rest_sync_positions_fixed"] == 0


# =============================================================================
# TEST START/STOP
# =============================================================================

class TestStartStop:
    """Test PositionManager start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_begins_user_data_stream(self, position_manager, mock_exchange):
        """start() should call exchange.start_user_data_stream()."""
        result = await position_manager.start()

        assert result is True
        assert position_manager._running is True
        mock_exchange.start_user_data_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_stops_user_data_stream(self, position_manager, mock_exchange):
        """stop() should call exchange.stop_user_data_stream()."""
        await position_manager.start()
        await position_manager.stop()

        assert position_manager._running is False
        mock_exchange.stop_user_data_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_creates_background_tasks(self, position_manager, mock_exchange):
        """start() should create timeout check and missing TP tasks."""
        await position_manager.start()

        assert position_manager._timeout_check_task is not None
        assert position_manager._missing_tp_check_task is not None
        assert position_manager._rest_sync_task is not None

        await position_manager.stop()

    @pytest.mark.asyncio
    async def test_double_start_returns_true(self, position_manager, mock_exchange):
        """Calling start() twice should return True without error."""
        await position_manager.start()
        result = await position_manager.start()  # Second call

        assert result is True
        # Should only have called start_user_data_stream once
        assert mock_exchange.start_user_data_stream.call_count == 1

        await position_manager.stop()


# =============================================================================
# TEST GET STATS
# =============================================================================

class TestGetStats:
    """Test statistics retrieval."""

    def test_get_stats_returns_all_fields(self, position_manager, mock_trade_engine):
        """get_stats() should return all expected statistics."""
        stats = position_manager.get_stats()

        expected_fields = [
            "order_updates_received",
            "account_updates_received",
            "positions_closed_sl",
            "positions_closed_tp",
            "positions_closed_timeout",
            "positions_closed_missing_tp",
            "positions_closed_manual",
            "missing_tp_alerts_sent",
            "rest_sync_runs",
            "rest_sync_positions_fixed",
            "rest_sync_orders_fixed",
            "tracked_orders",
            "open_positions",
        ]

        for field in expected_fields:
            assert field in stats


# =============================================================================
# TEST CALLBACK ERROR HANDLING
# =============================================================================

class TestCallbackErrorHandling:
    """Test that callback errors don't crash the manager."""

    def test_on_position_closed_error_handled(
        self, position_manager, mock_trade_engine, open_long_position
    ):
        """Error in on_position_closed callback should be caught."""
        mock_trade_engine.positions[open_long_position.position_id] = open_long_position
        position_manager.register_position(open_long_position)

        def bad_callback(pos, reason, pnl):
            raise ValueError("Callback error!")

        position_manager.on_position_closed = bad_callback

        event = {
            "e": "ORDER_TRADE_UPDATE",
            "o": {
                "i": "SL_001",
                "X": "FILLED",
                "ap": "48000.00",
                "rp": "-2.00",
            }
        }

        # Should not raise
        position_manager._handle_order_update(event)

        # Position should still be closed
        assert open_long_position.status == PositionStatus.CLOSED
