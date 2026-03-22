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
from datetime import datetime, timedelta, timezone
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
        opened_at=datetime.now(timezone.utc),
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

        # Simulate SL fill event (flat format as provided by binance.py)
        # SL uses ALGO_UPDATE event type
        order_info = {
            "orderId": "SL_001",
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "type": "STOP_MARKET",
            "avgPrice": "48000.00",
            "executedQty": "0.001",
            "origQty": "0.001",
            "realizedPnl": "-2.00",
            "eventType": "ALGO_UPDATE",
        }

        position_manager._handle_order_update(order_info)

        assert open_long_position.status == PositionStatus.CLOSED
        assert open_long_position.exit_reason == "SL"
        assert open_long_position.exit_price == 48000.0
        assert open_long_position.realized_pnl == -2.0
        assert position_manager._stats["positions_closed_sl"] == 1

    def test_tp_filled_closes_position(self, position_manager, mock_trade_engine, open_long_position):
        """TP fill should close position with exit_reason='TP'."""
        mock_trade_engine.positions[open_long_position.position_id] = open_long_position
        position_manager.register_position(open_long_position)

        # TP uses ORDER_TRADE_UPDATE (LIMIT order)
        order_info = {
            "orderId": "TP_001",
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "type": "LIMIT",
            "avgPrice": "55000.00",
            "executedQty": "0.001",
            "origQty": "0.001",
            "realizedPnl": "5.00",
            "eventType": "ORDER_TRADE_UPDATE",
        }

        position_manager._handle_order_update(order_info)

        assert open_long_position.status == PositionStatus.CLOSED
        assert open_long_position.exit_reason == "TP"
        assert open_long_position.exit_price == 55000.0
        assert open_long_position.realized_pnl == 5.0
        assert position_manager._stats["positions_closed_tp"] == 1

    def test_partial_fill_does_not_close(self, position_manager, mock_trade_engine, open_long_position):
        """PARTIALLY_FILLED should NOT close position."""
        mock_trade_engine.positions[open_long_position.position_id] = open_long_position
        position_manager.register_position(open_long_position)

        order_info = {
            "orderId": "SL_001",
            "symbol": "BTCUSDT",
            "status": "PARTIALLY_FILLED",
            "type": "STOP_MARKET",
            "avgPrice": "48000.00",
            "executedQty": "0.0005",
            "origQty": "0.001",
            "eventType": "ALGO_UPDATE",
        }

        position_manager._handle_order_update(order_info)

        assert open_long_position.status == PositionStatus.OPEN  # Still open!

    def test_untracked_order_ignored(self, position_manager, mock_trade_engine):
        """Orders not in our mapping should be ignored."""
        order_info = {
            "orderId": "UNKNOWN_ORDER",
            "status": "FILLED",
            "eventType": "ORDER_TRADE_UPDATE",
        }

        position_manager._handle_order_update(order_info)

        assert position_manager._stats["positions_closed_sl"] == 0
        assert position_manager._stats["positions_closed_tp"] == 0

    def test_already_closed_position_ignored(self, position_manager, mock_trade_engine, open_long_position):
        """Already closed position should be ignored."""
        open_long_position.status = PositionStatus.CLOSED
        mock_trade_engine.positions[open_long_position.position_id] = open_long_position
        position_manager.register_position(open_long_position)

        order_info = {
            "orderId": "SL_001",
            "status": "FILLED",
            "avgPrice": "48000.00",
            "realizedPnl": "-2.00",
            "eventType": "ALGO_UPDATE",
        }

        # Should not error, should just skip
        position_manager._handle_order_update(order_info)

        # No additional closes counted
        assert position_manager._stats["positions_closed_sl"] == 0

    def test_position_closed_callback_invoked(self, position_manager, mock_trade_engine, open_long_position):
        """on_position_closed callback should be called."""
        mock_trade_engine.positions[open_long_position.position_id] = open_long_position
        position_manager.register_position(open_long_position)

        callback_data = []
        position_manager.on_position_closed = lambda pos, reason, pnl: callback_data.append((pos, reason, pnl))

        # SL uses ALGO_UPDATE
        order_info = {
            "orderId": "SL_001",
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "type": "STOP_MARKET",
            "avgPrice": "48000.00",
            "executedQty": "0.001",
            "realizedPnl": "-2.00",
            "eventType": "ALGO_UPDATE",
        }

        position_manager._handle_order_update(order_info)

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
            opened_at=datetime.now(timezone.utc) - timedelta(days=15),  # 15 days ago
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
        """Timeout close should cancel SL (algo) and TP orders."""
        # SL uses Algo Order API - algoId must be numeric
        expired_position.sl_order_id = "987654321"
        expired_position.tp_order_id = "TP_EXP"
        mock_trade_engine.positions[expired_position.position_id] = expired_position

        await position_manager._close_position_timeout(expired_position)

        # SL uses cancel_algo_order, TP uses cancel_order
        assert mock_exchange.cancel_algo_order.call_count >= 1  # SL Algo
        assert mock_exchange.cancel_order.call_count >= 1  # TP regular


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
        # FIX #3: Include positionSide for Hedge Mode support
        mock_exchange.get_all_positions = AsyncMock(return_value=[
            {"symbol": "BTCUSDT", "positionAmt": "0.001", "positionSide": "LONG"}  # Position exists
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
        # FIX #3: Include positionSide for Hedge Mode support
        mock_exchange.get_all_positions = AsyncMock(return_value=[
            {"symbol": "BTCUSDT", "positionAmt": "0.001", "positionSide": "LONG"}
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
            "orphans_cleaned",
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

        # Use flat format (as provided by binance.py)
        order_info = {
            "orderId": "SL_001",
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "type": "STOP_MARKET",
            "avgPrice": "48000.00",
            "executedQty": "0.001",
            "realizedPnl": "-2.00",
            "eventType": "ALGO_UPDATE",
        }

        # Should not raise
        position_manager._handle_order_update(order_info)

        # Position should still be closed
        assert open_long_position.status == PositionStatus.CLOSED


# =============================================================================
# TEST ORPHAN ORDER CLEANUP (TradeAI1 style)
# =============================================================================

class TestOrphanOrderCleanup:
    """Test orphan order cleanup functionality."""

    @pytest.mark.asyncio
    async def test_orphan_order_cancelled(self, position_manager, mock_exchange):
        """Orphan order (no position) should be cancelled."""
        # Setup: order exists but no position
        mock_exchange.get_all_positions = AsyncMock(return_value=[])
        mock_exchange.get_open_orders = AsyncMock(return_value=[
            {
                "orderId": "123456",
                "symbol": "BTCUSDT",
                "side": "SELL",
                "positionSide": "LONG",
                "type": "TAKE_PROFIT_MARKET",
            }
        ])
        mock_exchange.get_open_algo_orders = AsyncMock(return_value=[])
        mock_exchange.cancel_order = AsyncMock(return_value=True)

        # Execute
        await position_manager._clean_orphan_orders([], [
            {
                "orderId": "123456",
                "symbol": "BTCUSDT",
                "side": "SELL",
                "positionSide": "LONG",
                "type": "TAKE_PROFIT_MARKET",
            }
        ], [])

        # Verify
        mock_exchange.cancel_order.assert_called_once_with("BTCUSDT", "123456")
        assert position_manager._stats["orphans_cleaned"] == 1

    @pytest.mark.asyncio
    async def test_orphan_algo_order_cancelled(self, position_manager, mock_exchange):
        """Orphan algo order (no position) should be cancelled."""
        mock_exchange.cancel_algo_order = AsyncMock(return_value=True)

        # Execute
        await position_manager._clean_orphan_orders([], [], [
            {
                "algoId": 789012,
                "symbol": "ETHUSDT",
                "side": "BUY",
                "positionSide": "SHORT",
                "orderType": "STOP_MARKET",
            }
        ])

        # Verify
        mock_exchange.cancel_algo_order.assert_called_once_with("ETHUSDT", algo_id=789012)
        assert position_manager._stats["orphans_cleaned"] == 1

    @pytest.mark.asyncio
    async def test_order_with_position_not_cancelled(self, position_manager, mock_exchange):
        """Order with existing position should NOT be cancelled."""
        mock_exchange.cancel_order = AsyncMock(return_value=True)

        # Position exists
        positions = [
            {"symbol": "BTCUSDT", "positionSide": "LONG", "positionAmt": "0.001"}
        ]
        orders = [
            {
                "orderId": "123456",
                "symbol": "BTCUSDT",
                "side": "SELL",
                "positionSide": "LONG",
                "type": "TAKE_PROFIT_MARKET",
            }
        ]

        # Execute
        await position_manager._clean_orphan_orders(positions, orders, [])

        # Verify - should NOT be cancelled
        mock_exchange.cancel_order.assert_not_called()
        assert position_manager._stats["orphans_cleaned"] == 0

    @pytest.mark.asyncio
    async def test_grace_period_prevents_cancel(self, position_manager, mock_exchange):
        """Order in grace period should NOT be cancelled."""
        mock_exchange.cancel_order = AsyncMock(return_value=True)

        # Set grace period for BTCUSDT
        position_manager._recently_closed_symbols["BTCUSDT"] = datetime.now(timezone.utc)

        # Execute - position is gone but in grace period
        await position_manager._clean_orphan_orders([], [
            {
                "orderId": "123456",
                "symbol": "BTCUSDT",
                "side": "SELL",
                "positionSide": "LONG",
                "type": "TAKE_PROFIT_MARKET",
            }
        ], [])

        # Verify - should NOT be cancelled due to grace period
        mock_exchange.cancel_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_expired_grace_period_allows_cancel(self, position_manager, mock_exchange):
        """Order after grace period expired should be cancelled."""
        mock_exchange.cancel_order = AsyncMock(return_value=True)

        # Set EXPIRED grace period (61 seconds ago)
        position_manager._recently_closed_symbols["BTCUSDT"] = (
            datetime.now(timezone.utc) - timedelta(seconds=61)
        )

        # Execute
        await position_manager._clean_orphan_orders([], [
            {
                "orderId": "123456",
                "symbol": "BTCUSDT",
                "side": "SELL",
                "positionSide": "LONG",
                "type": "TAKE_PROFIT_MARKET",
            }
        ], [])

        # Verify - should be cancelled (grace expired)
        mock_exchange.cancel_order.assert_called_once()
        # Grace period entry should be cleaned up
        assert "BTCUSDT" not in position_manager._recently_closed_symbols

    def test_unregister_sets_grace_period(self, position_manager, open_long_position):
        """unregister_position should set grace period."""
        position_manager.register_position(open_long_position)

        # Unregister
        position_manager.unregister_position(open_long_position)

        # Verify grace period is set
        assert open_long_position.symbol in position_manager._recently_closed_symbols
        grace_time = position_manager._recently_closed_symbols[open_long_position.symbol]
        assert (datetime.now(timezone.utc) - grace_time).total_seconds() < 2


# =============================================================================
# EDGE CASE TESTS - Реальные сценарии которые могут сломать код
# =============================================================================

class TestOrphanCleanupEdgeCases:
    """Edge cases that could break orphan cleanup in production."""

    @pytest.mark.asyncio
    async def test_long_short_same_symbol_only_long_exists(
        self, position_manager, mock_exchange
    ):
        """
        BUG SCENARIO: BTCUSDT has LONG position, but orphan order is for SHORT.
        Should cancel the SHORT order, keep LONG order intact.
        """
        mock_exchange.cancel_order = AsyncMock(return_value=True)

        # Only LONG position exists
        positions = [
            {"symbol": "BTCUSDT", "positionSide": "LONG", "positionAmt": "0.001"}
        ]
        # Two orders: one for LONG (valid), one for SHORT (orphan)
        orders = [
            {
                "orderId": "111",
                "symbol": "BTCUSDT",
                "side": "SELL",  # Exit LONG
                "positionSide": "LONG",
                "type": "TAKE_PROFIT",
            },
            {
                "orderId": "222",
                "symbol": "BTCUSDT",
                "side": "BUY",  # Exit SHORT - but no SHORT position!
                "positionSide": "SHORT",
                "type": "STOP_MARKET",
            },
        ]

        await position_manager._clean_orphan_orders(positions, orders, [])

        # Should cancel ONLY the SHORT order
        mock_exchange.cancel_order.assert_called_once_with("BTCUSDT", "222")

    @pytest.mark.asyncio
    async def test_position_side_both_fallback_logic(
        self, position_manager, mock_exchange
    ):
        """
        BUG SCENARIO: Order has positionSide="BOTH" (non-hedge mode).
        Must derive position side from order side correctly.
        SELL order -> LONG position, BUY order -> SHORT position.
        """
        mock_exchange.cancel_order = AsyncMock(return_value=True)

        # LONG position exists
        positions = [
            {"symbol": "ETHUSDT", "positionSide": "LONG", "positionAmt": "1.0"}
        ]

        # Order with positionSide="BOTH", side="SELL" -> should map to LONG
        orders_valid = [
            {
                "orderId": "333",
                "symbol": "ETHUSDT",
                "side": "SELL",
                "positionSide": "BOTH",  # Fallback case
                "type": "LIMIT",
            }
        ]

        await position_manager._clean_orphan_orders(positions, orders_valid, [])
        # Should NOT cancel - SELL maps to LONG, LONG position exists
        mock_exchange.cancel_order.assert_not_called()

        # Now test orphan case: BUY order (maps to SHORT), but only LONG exists
        orders_orphan = [
            {
                "orderId": "444",
                "symbol": "ETHUSDT",
                "side": "BUY",
                "positionSide": "BOTH",  # Maps to SHORT
                "type": "LIMIT",
            }
        ]

        await position_manager._clean_orphan_orders(positions, orders_orphan, [])
        # Should cancel - BUY maps to SHORT, no SHORT position
        mock_exchange.cancel_order.assert_called_once_with("ETHUSDT", "444")

    @pytest.mark.asyncio
    async def test_cancel_failure_does_not_crash(
        self, position_manager, mock_exchange
    ):
        """
        BUG SCENARIO: cancel_order raises exception.
        Should NOT crash, should continue processing other orders.
        """
        call_count = 0

        async def mock_cancel(symbol, order_id):
            nonlocal call_count
            call_count += 1
            if order_id == "111":
                raise Exception("Network timeout")
            return True

        mock_exchange.cancel_order = mock_cancel

        orders = [
            {"orderId": "111", "symbol": "BTCUSDT", "side": "SELL", "positionSide": "LONG", "type": "TP"},
            {"orderId": "222", "symbol": "ETHUSDT", "side": "SELL", "positionSide": "LONG", "type": "TP"},
            {"orderId": "333", "symbol": "SOLUSDT", "side": "SELL", "positionSide": "LONG", "type": "TP"},
        ]

        # Should NOT raise, should continue
        await position_manager._clean_orphan_orders([], orders, [])

        # All 3 cancels should have been attempted
        assert call_count == 3
        # Only 2 successful (111 failed)
        assert position_manager._stats["orphans_cleaned"] == 2

    @pytest.mark.asyncio
    async def test_algo_cancel_failure_does_not_crash(
        self, position_manager, mock_exchange
    ):
        """
        BUG SCENARIO: cancel_algo_order raises exception.
        Should NOT crash, should continue.
        """
        mock_exchange.cancel_algo_order = AsyncMock(side_effect=Exception("API Error"))

        algo_orders = [
            {"algoId": 111, "symbol": "BTCUSDT", "side": "SELL", "positionSide": "LONG", "orderType": "SL"},
        ]

        # Should NOT raise
        await position_manager._clean_orphan_orders([], [], algo_orders)
        # Should have 0 cleaned (failed)
        assert position_manager._stats["orphans_cleaned"] == 0

    @pytest.mark.asyncio
    async def test_missing_order_id_skipped(self, position_manager, mock_exchange):
        """
        BUG SCENARIO: Order has no orderId or empty orderId.
        Should skip without crashing.
        """
        mock_exchange.cancel_order = AsyncMock(return_value=True)

        orders = [
            {"orderId": None, "symbol": "BTCUSDT", "side": "SELL", "positionSide": "LONG"},
            {"orderId": "", "symbol": "ETHUSDT", "side": "SELL", "positionSide": "LONG"},
            {"symbol": "SOLUSDT", "side": "SELL", "positionSide": "LONG"},  # No orderId key
            {"orderId": "123", "symbol": "XRPUSDT", "side": "SELL", "positionSide": "LONG"},
        ]

        await position_manager._clean_orphan_orders([], orders, [])

        # Should only cancel the valid one
        mock_exchange.cancel_order.assert_called_once_with("XRPUSDT", "123")

    @pytest.mark.asyncio
    async def test_algo_id_none_skipped(self, position_manager, mock_exchange):
        """
        BUG SCENARIO: Algo order has algoId=None.
        Should skip without crashing.
        """
        mock_exchange.cancel_algo_order = AsyncMock(return_value=True)

        algo_orders = [
            {"algoId": None, "symbol": "BTCUSDT", "side": "SELL", "positionSide": "LONG"},
            {"algoId": 789, "symbol": "ETHUSDT", "side": "SELL", "positionSide": "LONG"},
        ]

        await position_manager._clean_orphan_orders([], [], algo_orders)

        # Should only cancel the valid one
        mock_exchange.cancel_algo_order.assert_called_once_with("ETHUSDT", algo_id=789)

    @pytest.mark.asyncio
    async def test_algo_id_string_converted_to_int(self, position_manager, mock_exchange):
        """
        BUG SCENARIO: API returns algoId as string "12345" instead of int.
        Should convert and work.
        """
        mock_exchange.cancel_algo_order = AsyncMock(return_value=True)

        algo_orders = [
            {"algoId": "12345", "symbol": "BTCUSDT", "side": "SELL", "positionSide": "LONG"},
        ]

        await position_manager._clean_orphan_orders([], [], algo_orders)

        # Should convert to int
        mock_exchange.cancel_algo_order.assert_called_once_with("BTCUSDT", algo_id=12345)

    @pytest.mark.asyncio
    async def test_grace_period_exact_boundary_60_seconds(
        self, position_manager, mock_exchange
    ):
        """
        BUG SCENARIO: Grace period exactly at 60 seconds.
        Should NOT cancel (boundary inclusive).
        """
        mock_exchange.cancel_order = AsyncMock(return_value=True)

        # Exactly 60 seconds ago
        position_manager._recently_closed_symbols["BTCUSDT"] = (
            datetime.now(timezone.utc) - timedelta(seconds=60)
        )

        orders = [
            {"orderId": "123", "symbol": "BTCUSDT", "side": "SELL", "positionSide": "LONG"},
        ]

        await position_manager._clean_orphan_orders([], orders, [])

        # At exactly 60 seconds, should still be protected (> not >=)
        mock_exchange.cancel_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_orphans_all_cancelled(self, position_manager, mock_exchange):
        """
        STRESS TEST: 5 orphan orders, all should be cancelled.
        Stats should accumulate correctly.
        """
        mock_exchange.cancel_order = AsyncMock(return_value=True)
        mock_exchange.cancel_algo_order = AsyncMock(return_value=True)

        orders = [
            {"orderId": "1", "symbol": "BTCUSDT", "side": "SELL", "positionSide": "LONG"},
            {"orderId": "2", "symbol": "ETHUSDT", "side": "SELL", "positionSide": "LONG"},
            {"orderId": "3", "symbol": "SOLUSDT", "side": "BUY", "positionSide": "SHORT"},
        ]
        algo_orders = [
            {"algoId": 100, "symbol": "XRPUSDT", "side": "SELL", "positionSide": "LONG"},
            {"algoId": 200, "symbol": "DOGEUSDT", "side": "BUY", "positionSide": "SHORT"},
        ]

        await position_manager._clean_orphan_orders([], orders, algo_orders)

        assert mock_exchange.cancel_order.call_count == 3
        assert mock_exchange.cancel_algo_order.call_count == 2
        assert position_manager._stats["orphans_cleaned"] == 5

    @pytest.mark.asyncio
    async def test_stats_accumulate_across_calls(self, position_manager, mock_exchange):
        """
        BUG SCENARIO: Stats should accumulate, not reset.
        """
        mock_exchange.cancel_order = AsyncMock(return_value=True)

        orders1 = [{"orderId": "1", "symbol": "BTCUSDT", "side": "SELL", "positionSide": "LONG"}]
        orders2 = [{"orderId": "2", "symbol": "ETHUSDT", "side": "SELL", "positionSide": "LONG"}]

        await position_manager._clean_orphan_orders([], orders1, [])
        assert position_manager._stats["orphans_cleaned"] == 1

        await position_manager._clean_orphan_orders([], orders2, [])
        assert position_manager._stats["orphans_cleaned"] == 2  # Accumulated, not reset!

    @pytest.mark.asyncio
    async def test_empty_orders_no_crash(self, position_manager, mock_exchange):
        """Edge case: Empty orders lists."""
        mock_exchange.cancel_order = AsyncMock(return_value=True)

        # Should not crash with empty lists
        await position_manager._clean_orphan_orders([], [], [])
        mock_exchange.cancel_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_grace_period_cleanup_happens(self, position_manager, mock_exchange):
        """
        Verify old grace period entries are cleaned up.
        """
        # Add expired entry
        position_manager._recently_closed_symbols["OLDUSDT"] = (
            datetime.now(timezone.utc) - timedelta(seconds=120)
        )
        # Add fresh entry
        position_manager._recently_closed_symbols["NEWUSDT"] = datetime.now(timezone.utc)

        await position_manager._clean_orphan_orders([], [], [])

        # Old one should be removed
        assert "OLDUSDT" not in position_manager._recently_closed_symbols
        # Fresh one should remain
        assert "NEWUSDT" in position_manager._recently_closed_symbols

    @pytest.mark.asyncio
    async def test_same_symbol_different_position_sides(
        self, position_manager, mock_exchange
    ):
        """
        COMPLEX SCENARIO: Same symbol, both LONG and SHORT positions exist.
        Should NOT cancel any orders.
        """
        mock_exchange.cancel_order = AsyncMock(return_value=True)

        # Both LONG and SHORT exist for BTCUSDT
        positions = [
            {"symbol": "BTCUSDT", "positionSide": "LONG", "positionAmt": "0.001"},
            {"symbol": "BTCUSDT", "positionSide": "SHORT", "positionAmt": "-0.001"},
        ]
        # Orders for both sides
        orders = [
            {"orderId": "1", "symbol": "BTCUSDT", "side": "SELL", "positionSide": "LONG"},
            {"orderId": "2", "symbol": "BTCUSDT", "side": "BUY", "positionSide": "SHORT"},
        ]

        await position_manager._clean_orphan_orders(positions, orders, [])

        # Neither should be cancelled
        mock_exchange.cancel_order.assert_not_called()


# =============================================================================
# INTEGRATION TEST - Verify REST sync actually calls orphan cleanup
# =============================================================================

class TestOrphanCleanupIntegration:
    """Integration tests for orphan cleanup in REST sync."""

    @pytest.mark.asyncio
    async def test_perform_rest_sync_calls_clean_orphans(
        self, position_manager, mock_exchange, mock_trade_engine
    ):
        """
        CRITICAL: Verify _perform_rest_sync actually calls _clean_orphan_orders
        with the correct data.
        """
        # Mock all exchange methods
        mock_exchange.get_all_positions = AsyncMock(return_value=[
            {"symbol": "BTCUSDT", "positionSide": "LONG", "positionAmt": "0.001"}
        ])
        mock_exchange.get_open_orders = AsyncMock(return_value=[
            {"orderId": "123", "symbol": "ETHUSDT", "side": "SELL", "positionSide": "LONG"}
        ])
        mock_exchange.get_open_algo_orders = AsyncMock(return_value=[
            {"algoId": 456, "symbol": "SOLUSDT", "side": "SELL", "positionSide": "LONG"}
        ])
        mock_exchange.cancel_order = AsyncMock(return_value=True)
        mock_exchange.cancel_algo_order = AsyncMock(return_value=True)

        # Mock trade_engine to have no positions (so no tracked positions to check)
        mock_trade_engine.get_open_positions = MagicMock(return_value=[])

        # Run REST sync
        await position_manager._perform_rest_sync()

        # Verify orphans were cleaned
        # ETHUSDT order should be cancelled (no position for ETHUSDT LONG)
        mock_exchange.cancel_order.assert_called_once_with("ETHUSDT", "123")
        # SOLUSDT algo should be cancelled (no position for SOLUSDT LONG)
        mock_exchange.cancel_algo_order.assert_called_once_with("SOLUSDT", algo_id=456)

    @pytest.mark.asyncio
    async def test_rest_sync_preserves_valid_orders(
        self, position_manager, mock_exchange, mock_trade_engine
    ):
        """
        Verify REST sync does NOT cancel orders for existing positions.
        """
        # Position exists
        mock_exchange.get_all_positions = AsyncMock(return_value=[
            {"symbol": "BTCUSDT", "positionSide": "LONG", "positionAmt": "0.001"}
        ])
        # Order for that position
        mock_exchange.get_open_orders = AsyncMock(return_value=[
            {"orderId": "123", "symbol": "BTCUSDT", "side": "SELL", "positionSide": "LONG"}
        ])
        mock_exchange.get_open_algo_orders = AsyncMock(return_value=[])
        mock_exchange.cancel_order = AsyncMock(return_value=True)

        mock_trade_engine.get_open_positions = MagicMock(return_value=[])

        await position_manager._perform_rest_sync()

        # Should NOT cancel - position exists
        mock_exchange.cancel_order.assert_not_called()
