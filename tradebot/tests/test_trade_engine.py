# -*- coding: utf-8 -*-
"""
Tests for engine/trade_engine.py

Testing:
- execute_signal() success path
- execute_signal() with regime_action=OFF/DYN
- Entry order failure handling
- SL placement failure -> emergency close
- TP placement failure -> continues with warning
- Partial fill handling
- Critical error propagation
- close_position() manual close
- Statistics tracking
"""

import pytest
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tradebot.engine.trade_engine import TradeEngine
from tradebot.core.models import (
    Position,
    PositionSide,
    PositionStatus,
    OrderSide,
)
from tradebot.core.exceptions import (
    BinanceError,
    ErrorCategory,
    InsufficientBalanceError,
    LiquidationError,
    IPBanError,
    AuthError,
)


# =============================================================================
# MOCK SIGNAL FIXTURE
# =============================================================================

class MockSignal:
    """Mock Signal object mimicking strategies.Signal."""

    def __init__(
        self,
        signal_id: str = "SIG_TEST_001",
        symbol: str = "BTCUSDT",
        direction: str = "LONG",
        entry: float = 50000.0,
        stop_loss: float = 48000.0,
        take_profit: float = 55000.0,
        metadata: Dict[str, Any] = None,
    ):
        self.signal_id = signal_id
        self.symbol = symbol
        self.direction = direction
        self.entry = entry
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.metadata = metadata or {"strategy": "test_strategy"}


@pytest.fixture
def mock_signal_long():
    """Long signal for testing."""
    return MockSignal(
        signal_id="SIG_LONG_001",
        symbol="BTCUSDT",
        direction="LONG",
        entry=50000.0,
        stop_loss=48000.0,
        take_profit=55000.0,
    )


@pytest.fixture
def mock_signal_short():
    """Short signal for testing."""
    return MockSignal(
        signal_id="SIG_SHORT_001",
        symbol="ETHUSDT",
        direction="SHORT",
        entry=3000.0,
        stop_loss=3200.0,
        take_profit=2700.0,
    )


# =============================================================================
# TEST EXECUTE_SIGNAL SUCCESS
# =============================================================================

class TestExecuteSignalSuccess:
    """Test successful signal execution."""

    @pytest.fixture
    def trade_engine(self, mock_exchange):
        """Create TradeEngine with mock exchange."""
        return TradeEngine(
            exchange=mock_exchange,
            default_order_size_usd=10.0,
            default_leverage=10,
            max_hold_days=14,
        )

    @pytest.mark.asyncio
    async def test_execute_long_signal_success(self, trade_engine, mock_exchange, mock_signal_long):
        """Executing LONG signal should open position with SL and TP."""
        position = await trade_engine.execute_signal(mock_signal_long)

        assert position is not None
        assert position.symbol == "BTCUSDT"
        assert position.side == PositionSide.LONG
        assert position.status == PositionStatus.OPEN
        assert position.entry_price == 50000.0  # From mock avgPrice
        assert position.sl_order_id != ""
        assert position.tp_order_id != ""

        # Verify exchange calls
        mock_exchange.get_price.assert_called_once_with("BTCUSDT")
        mock_exchange.set_leverage.assert_called_once_with("BTCUSDT", 10)
        mock_exchange.place_market_order.assert_called_once()
        mock_exchange.place_stop_order.assert_called_once()
        mock_exchange.place_take_profit_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_short_signal_success(self, trade_engine, mock_exchange, mock_signal_short):
        """Executing SHORT signal should set correct sides."""
        position = await trade_engine.execute_signal(mock_signal_short)

        assert position is not None
        assert position.side == PositionSide.SHORT

        # Check that exit side is BUY for SHORT position
        sl_call = mock_exchange.place_stop_order.call_args
        assert sl_call.kwargs["side"] == OrderSide.BUY
        assert sl_call.kwargs["position_side"] == PositionSide.SHORT

    @pytest.mark.asyncio
    async def test_position_registered_in_engine(self, trade_engine, mock_exchange, mock_signal_long):
        """Position should be stored in trade_engine.positions."""
        position = await trade_engine.execute_signal(mock_signal_long)

        assert position.position_id in trade_engine.positions
        assert trade_engine.positions[position.position_id] is position

    @pytest.mark.asyncio
    async def test_statistics_updated_on_success(self, trade_engine, mock_exchange, mock_signal_long):
        """Statistics should be updated after successful execution."""
        initial_received = trade_engine.signals_received
        initial_executed = trade_engine.signals_executed

        await trade_engine.execute_signal(mock_signal_long)

        assert trade_engine.signals_received == initial_received + 1
        assert trade_engine.signals_executed == initial_executed + 1


# =============================================================================
# TEST REGIME ACTION
# =============================================================================

class TestRegimeAction:
    """Test regime_action parameter handling."""

    @pytest.fixture
    def trade_engine(self, mock_exchange):
        return TradeEngine(
            exchange=mock_exchange,
            default_order_size_usd=10.0,
            default_leverage=10,
        )

    @pytest.mark.asyncio
    async def test_regime_off_skips_signal(self, trade_engine, mock_exchange, mock_signal_long):
        """regime_action=OFF should skip signal without placing orders."""
        position = await trade_engine.execute_signal(mock_signal_long, regime_action="OFF")

        assert position is None
        assert trade_engine.signals_skipped == 1
        mock_exchange.place_market_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_regime_dyn_uses_small_size(self, trade_engine, mock_exchange, mock_signal_long):
        """regime_action=DYN should use $1 position size, but min notional applies."""
        await trade_engine.execute_signal(mock_signal_long, regime_action="DYN")

        # With $1 size and 50000 price, quantity would be 0.00002
        # BUT min notional is $100, so quantity is adjusted to meet min notional
        # $100 / $50000 = 0.002, plus step_size adjustment = 0.002+
        call_args = mock_exchange.place_market_order.call_args
        quantity = call_args.kwargs["quantity"]
        # Quantity adjusted for min notional ($100)
        assert float(quantity) >= 0.002  # At least $100 notional

    @pytest.mark.asyncio
    async def test_custom_order_size_used(self, trade_engine, mock_exchange, mock_signal_long):
        """Custom order_size_usd should override default."""
        await trade_engine.execute_signal(mock_signal_long, order_size_usd=100.0)

        call_args = mock_exchange.place_market_order.call_args
        quantity = call_args.kwargs["quantity"]
        # Quantity = $100 / $50000 = 0.002
        assert float(quantity) == pytest.approx(0.002, rel=0.01)


# =============================================================================
# TEST ENTRY ORDER FAILURES
# =============================================================================

class TestEntryOrderFailures:
    """Test handling of entry order failures."""

    @pytest.fixture
    def trade_engine(self, mock_exchange):
        return TradeEngine(exchange=mock_exchange)

    @pytest.mark.asyncio
    async def test_insufficient_balance_skips_signal(self, trade_engine, mock_exchange, mock_signal_long):
        """InsufficientBalanceError should skip signal without crash."""
        mock_exchange.place_market_order = AsyncMock(
            side_effect=InsufficientBalanceError("Margin is insufficient")
        )

        position = await trade_engine.execute_signal(mock_signal_long)

        assert position is None
        assert trade_engine.signals_skipped == 1

    @pytest.mark.asyncio
    async def test_liquidation_error_propagates(self, trade_engine, mock_exchange, mock_signal_long):
        """LiquidationError should propagate up (critical error)."""
        mock_exchange.place_market_order = AsyncMock(
            side_effect=LiquidationError("User in liquidation mode")
        )

        with pytest.raises(LiquidationError):
            await trade_engine.execute_signal(mock_signal_long)

    @pytest.mark.asyncio
    async def test_auth_error_propagates(self, trade_engine, mock_exchange, mock_signal_long):
        """AuthError should propagate up (critical error)."""
        mock_exchange.place_market_order = AsyncMock(
            side_effect=AuthError("Invalid API key")
        )

        with pytest.raises(AuthError):
            await trade_engine.execute_signal(mock_signal_long)

    @pytest.mark.asyncio
    async def test_ip_ban_error_propagates(self, trade_engine, mock_exchange, mock_signal_long):
        """IPBanError should propagate up (critical error)."""
        mock_exchange.place_market_order = AsyncMock(
            side_effect=IPBanError("IP banned", retry_after=3600)
        )

        with pytest.raises(IPBanError):
            await trade_engine.execute_signal(mock_signal_long)

    @pytest.mark.asyncio
    async def test_generic_binance_error_skips_signal(self, trade_engine, mock_exchange, mock_signal_long):
        """Generic BinanceError should skip signal."""
        mock_exchange.place_market_order = AsyncMock(
            side_effect=BinanceError(
                code=-1000,
                message="Unknown error",
                category=ErrorCategory.UNKNOWN,
            )
        )

        position = await trade_engine.execute_signal(mock_signal_long)

        assert position is None
        assert trade_engine.signals_skipped == 1

    @pytest.mark.asyncio
    async def test_empty_entry_result_skips_signal(self, trade_engine, mock_exchange, mock_signal_long):
        """Empty entry result should skip signal."""
        mock_exchange.place_market_order = AsyncMock(return_value=None)

        position = await trade_engine.execute_signal(mock_signal_long)

        assert position is None
        assert trade_engine.signals_skipped == 1


# =============================================================================
# TEST SL PLACEMENT FAILURE
# =============================================================================

class TestSLPlacementFailure:
    """Test SL placement failure -> emergency close."""

    @pytest.fixture
    def trade_engine(self, mock_exchange):
        engine = TradeEngine(exchange=mock_exchange)
        engine.sl_max_retries = 3
        return engine

    @pytest.mark.asyncio
    async def test_sl_failure_triggers_emergency_close(self, trade_engine, mock_exchange, mock_signal_long):
        """Failed SL placement should trigger emergency close."""
        # Entry succeeds, SL fails
        mock_exchange.place_stop_order = AsyncMock(
            side_effect=BinanceError(
                code=-2010,
                message="Order would immediately trigger",
                category=ErrorCategory.ORDER_REJECTED,
            )
        )

        position = await trade_engine.execute_signal(mock_signal_long)

        assert position is None
        assert trade_engine.sl_failures == 1
        assert trade_engine.emergency_closes == 1

        # Should have called place_market_order twice: entry + emergency close
        assert mock_exchange.place_market_order.call_count == 2

    @pytest.mark.asyncio
    async def test_sl_failure_counts_as_skipped(self, trade_engine, mock_exchange, mock_signal_long):
        """SL failure should count as skipped signal."""
        mock_exchange.place_stop_order = AsyncMock(
            side_effect=BinanceError(
                code=-2010,
                message="Order would immediately trigger",
                category=ErrorCategory.ORDER_REJECTED,
            )
        )

        await trade_engine.execute_signal(mock_signal_long)

        assert trade_engine.signals_skipped == 1
        assert trade_engine.signals_executed == 0


# =============================================================================
# TEST TP PLACEMENT FAILURE
# =============================================================================

class TestTPPlacementFailure:
    """Test TP placement failure -> position continues with warning."""

    @pytest.fixture
    def trade_engine(self, mock_exchange):
        return TradeEngine(exchange=mock_exchange)

    @pytest.mark.asyncio
    async def test_tp_failure_position_still_opens(self, trade_engine, mock_exchange, mock_signal_long):
        """Failed TP should not prevent position opening (SL protects)."""
        mock_exchange.place_take_profit_order = AsyncMock(
            side_effect=BinanceError(
                code=-2010,
                message="Order would immediately trigger",
                category=ErrorCategory.ORDER_REJECTED,
            )
        )

        position = await trade_engine.execute_signal(mock_signal_long)

        # Position should still be created
        assert position is not None
        assert position.status == PositionStatus.OPEN
        assert position.sl_order_id != ""
        assert position.tp_order_id == ""  # TP failed

        assert trade_engine.tp_failures == 1
        assert trade_engine.signals_executed == 1

    @pytest.mark.asyncio
    async def test_tp_failure_registers_for_monitoring(self, trade_engine, mock_exchange, mock_signal_long):
        """Failed TP should register position for missing TP monitoring."""
        mock_exchange.place_take_profit_order = AsyncMock(return_value=None)

        # Add mock position manager
        mock_position_manager = MagicMock()
        trade_engine.position_manager = mock_position_manager

        position = await trade_engine.execute_signal(mock_signal_long)

        mock_position_manager.register_missing_tp.assert_called_once_with(position)


# =============================================================================
# TEST PARTIAL FILL
# =============================================================================

class TestPartialFill:
    """Test partial fill handling."""

    @pytest.fixture
    def trade_engine(self, mock_exchange):
        return TradeEngine(exchange=mock_exchange)

    @pytest.mark.asyncio
    async def test_partial_fill_detected(self, trade_engine, mock_exchange, mock_signal_long):
        """Partial fill should be detected and tracked."""
        # Mock partial fill response
        mock_exchange.place_market_order = AsyncMock(return_value={
            "orderId": "123",
            "status": "PARTIALLY_FILLED",
            "avgPrice": "50000.0",
            "origQty": "0.001",
            "executedQty": "0.0005",  # Only half filled
        })

        position = await trade_engine.execute_signal(mock_signal_long)

        assert position is not None
        assert position.is_partial_fill is True
        assert position.quantity == 0.0005  # Actual filled quantity
        assert position.requested_quantity == 0.001  # Original quantity
        assert trade_engine.partial_fills == 1

    @pytest.mark.asyncio
    async def test_partial_fill_uses_executed_qty_for_sltp(self, trade_engine, mock_exchange, mock_signal_long):
        """SL/TP should use executed quantity, not requested."""
        mock_exchange.place_market_order = AsyncMock(return_value={
            "orderId": "123",
            "status": "PARTIALLY_FILLED",
            "avgPrice": "50000.0",
            "origQty": "0.001",
            "executedQty": "0.0005",
        })

        await trade_engine.execute_signal(mock_signal_long)

        # Check SL order used correct quantity
        sl_call = mock_exchange.place_stop_order.call_args
        assert float(sl_call.kwargs["quantity"]) == pytest.approx(0.0005, rel=0.01)


# =============================================================================
# TEST ALERT CALLBACK
# =============================================================================

class TestAlertCallback:
    """Test alert callback functionality."""

    @pytest.fixture
    def trade_engine(self, mock_exchange):
        return TradeEngine(exchange=mock_exchange)

    @pytest.mark.asyncio
    async def test_alert_sent_on_insufficient_balance(self, trade_engine, mock_exchange, mock_signal_long):
        """Alert should be sent on insufficient balance."""
        alert_received = []
        trade_engine.on_alert = lambda level, msg, details: alert_received.append((level, msg, details))

        mock_exchange.place_market_order = AsyncMock(
            side_effect=InsufficientBalanceError("Margin is insufficient")
        )

        await trade_engine.execute_signal(mock_signal_long)

        assert len(alert_received) == 1
        assert alert_received[0][0] == "WARNING"
        assert "Insufficient balance" in alert_received[0][1]

    @pytest.mark.asyncio
    async def test_alert_sent_on_partial_fill(self, trade_engine, mock_exchange, mock_signal_long):
        """Alert should be sent on partial fill."""
        alert_received = []
        trade_engine.on_alert = lambda level, msg, details: alert_received.append((level, msg, details))

        mock_exchange.place_market_order = AsyncMock(return_value={
            "orderId": "123",
            "status": "PARTIALLY_FILLED",
            "avgPrice": "50000.0",
            "origQty": "0.001",
            "executedQty": "0.0005",
        })

        await trade_engine.execute_signal(mock_signal_long)

        # Should have partial fill alert
        partial_alerts = [a for a in alert_received if "Partial fill" in a[1]]
        assert len(partial_alerts) == 1
        assert partial_alerts[0][0] == "WARNING"


# =============================================================================
# TEST CLOSE POSITION
# =============================================================================

class TestClosePosition:
    """Test manual position close."""

    @pytest.fixture
    def trade_engine(self, mock_exchange):
        return TradeEngine(exchange=mock_exchange)

    @pytest.mark.asyncio
    async def test_close_position_success(self, trade_engine, mock_exchange, mock_signal_long):
        """Manual close should cancel orders and close position."""
        # First open a position
        position = await trade_engine.execute_signal(mock_signal_long)
        assert position is not None

        # Reset mocks
        mock_exchange.cancel_order.reset_mock()
        mock_exchange.cancel_algo_order.reset_mock()
        mock_exchange.place_market_order.reset_mock()

        # Close the position
        result = await trade_engine.close_position(position.position_id, reason="TEST_CLOSE")

        assert result is True
        assert position.status == PositionStatus.CLOSED
        assert position.exit_reason == "TEST_CLOSE"
        assert position.closed_at is not None

        # Should have cancelled SL (Algo) and TP (regular)
        # SL uses cancel_algo_order, TP uses cancel_order
        assert mock_exchange.cancel_algo_order.call_count >= 1  # SL Algo
        assert mock_exchange.cancel_order.call_count >= 1  # TP regular

    @pytest.mark.asyncio
    async def test_close_nonexistent_position_fails(self, trade_engine, mock_exchange):
        """Closing nonexistent position should return False."""
        result = await trade_engine.close_position("NONEXISTENT_ID")

        assert result is False
        mock_exchange.place_market_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_already_closed_position_fails(self, trade_engine, mock_exchange, mock_signal_long):
        """Closing already closed position should return False."""
        position = await trade_engine.execute_signal(mock_signal_long)
        position.status = PositionStatus.CLOSED

        result = await trade_engine.close_position(position.position_id)

        assert result is False


# =============================================================================
# TEST GET STATS
# =============================================================================

class TestGetStats:
    """Test statistics retrieval."""

    @pytest.fixture
    def trade_engine(self, mock_exchange):
        return TradeEngine(exchange=mock_exchange)

    @pytest.mark.asyncio
    async def test_get_stats_returns_all_fields(self, trade_engine, mock_exchange, mock_signal_long):
        """get_stats() should return all expected fields."""
        await trade_engine.execute_signal(mock_signal_long)

        stats = trade_engine.get_stats()

        assert "signals_received" in stats
        assert "signals_executed" in stats
        assert "signals_skipped" in stats
        assert "open_positions" in stats
        assert "total_positions" in stats
        assert "sl_failures" in stats
        assert "tp_failures" in stats
        assert "emergency_closes" in stats
        assert "partial_fills" in stats

        assert stats["signals_received"] == 1
        assert stats["signals_executed"] == 1
        assert stats["open_positions"] == 1


# =============================================================================
# TEST POSITION MANAGER INTEGRATION
# =============================================================================

class TestPositionManagerIntegration:
    """Test integration with PositionManager."""

    @pytest.fixture
    def trade_engine(self, mock_exchange):
        return TradeEngine(exchange=mock_exchange)

    @pytest.mark.asyncio
    async def test_position_registered_with_manager(self, trade_engine, mock_exchange, mock_signal_long):
        """Position should be registered with PositionManager if set."""
        mock_position_manager = MagicMock()
        trade_engine.position_manager = mock_position_manager

        position = await trade_engine.execute_signal(mock_signal_long)

        mock_position_manager.register_position.assert_called_once_with(position)

    @pytest.mark.asyncio
    async def test_no_error_without_position_manager(self, trade_engine, mock_exchange, mock_signal_long):
        """Should work fine without PositionManager set."""
        trade_engine.position_manager = None

        position = await trade_engine.execute_signal(mock_signal_long)

        assert position is not None


# =============================================================================
# TEST CRITICAL ERRORS IN EXECUTE_SIGNAL
# =============================================================================

class TestCriticalErrorsInExecuteSignal:
    """Test critical error handling and propagation."""

    @pytest.fixture
    def trade_engine(self, mock_exchange):
        return TradeEngine(exchange=mock_exchange)

    @pytest.mark.asyncio
    async def test_critical_error_sends_alert(self, trade_engine, mock_exchange, mock_signal_long):
        """Critical error should send CRITICAL level alert."""
        alert_received = []
        trade_engine.on_alert = lambda level, msg, details: alert_received.append((level, msg, details))

        mock_exchange.place_market_order = AsyncMock(
            side_effect=LiquidationError("User in liquidation mode")
        )

        with pytest.raises(LiquidationError):
            await trade_engine.execute_signal(mock_signal_long)

        critical_alerts = [a for a in alert_received if a[0] == "CRITICAL"]
        assert len(critical_alerts) == 1
