# -*- coding: utf-8 -*-
"""
Tests for core/models.py

Testing:
- Position properties (is_open, is_long)
- Position methods (calculate_pnl_pct, is_expired, get_hold_days)
- TradeOrder creation
- Edge cases and boundary conditions
"""

import pytest
from datetime import datetime, timedelta, timezone
from tradebot.core.models import (
    Position,
    PositionSide,
    PositionStatus,
    TradeOrder,
    OrderSide,
    OrderType,
    OrderStatus,
)


class TestPositionProperties:
    """Test Position property methods."""

    def test_is_open_returns_true_for_open_position(self, sample_long_position):
        """Position.is_open should return True for OPEN status."""
        assert sample_long_position.status == PositionStatus.OPEN
        assert sample_long_position.is_open is True

    def test_is_open_returns_false_for_closed_position(self, closed_position):
        """Position.is_open should return False for CLOSED status."""
        assert closed_position.status == PositionStatus.CLOSED
        assert closed_position.is_open is False

    def test_is_open_returns_false_for_pending_position(self):
        """Position.is_open should return False for PENDING status."""
        position = Position(
            position_id="TEST",
            signal_id="SIG",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=0.001,
            entry_price=50000.0,
            stop_loss=48000.0,
            take_profit=55000.0,
            status=PositionStatus.PENDING,
        )
        assert position.is_open is False

    def test_is_long_returns_true_for_long_position(self, sample_long_position):
        """Position.is_long should return True for LONG side."""
        assert sample_long_position.side == PositionSide.LONG
        assert sample_long_position.is_long is True

    def test_is_long_returns_false_for_short_position(self, sample_short_position):
        """Position.is_long should return False for SHORT side."""
        assert sample_short_position.side == PositionSide.SHORT
        assert sample_short_position.is_long is False


class TestPositionPnLCalculation:
    """Test Position.calculate_pnl_pct() method."""

    def test_long_position_profit(self, sample_long_position):
        """LONG position should show profit when price goes UP."""
        # Entry: 50000, Current: 55000 -> +10%
        pnl = sample_long_position.calculate_pnl_pct(55000.0)
        assert pnl == pytest.approx(10.0, rel=1e-3)

    def test_long_position_loss(self, sample_long_position):
        """LONG position should show loss when price goes DOWN."""
        # Entry: 50000, Current: 45000 -> -10%
        pnl = sample_long_position.calculate_pnl_pct(45000.0)
        assert pnl == pytest.approx(-10.0, rel=1e-3)

    def test_short_position_profit(self, sample_short_position):
        """SHORT position should show profit when price goes DOWN."""
        # Entry: 3000, Current: 2700 -> +10%
        pnl = sample_short_position.calculate_pnl_pct(2700.0)
        assert pnl == pytest.approx(10.0, rel=1e-3)

    def test_short_position_loss(self, sample_short_position):
        """SHORT position should show loss when price goes UP."""
        # Entry: 3000, Current: 3300 -> -10%
        pnl = sample_short_position.calculate_pnl_pct(3300.0)
        assert pnl == pytest.approx(-10.0, rel=1e-3)

    def test_zero_entry_price_returns_zero(self):
        """Should return 0 if entry_price is 0 (avoid division by zero)."""
        position = Position(
            position_id="TEST",
            signal_id="SIG",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=0.001,
            entry_price=0.0,  # Zero entry price
            stop_loss=48000.0,
            take_profit=55000.0,
        )
        pnl = position.calculate_pnl_pct(50000.0)
        assert pnl == 0.0

    def test_same_price_returns_zero(self, sample_long_position):
        """Should return 0 when current price equals entry price."""
        pnl = sample_long_position.calculate_pnl_pct(sample_long_position.entry_price)
        assert pnl == 0.0


class TestPositionExpiry:
    """Test Position.is_expired() and get_hold_days() methods."""

    def test_expired_position_returns_true(self, expired_position):
        """is_expired should return True when max_hold_days exceeded."""
        assert expired_position.is_expired() is True

    def test_fresh_position_not_expired(self, sample_long_position):
        """is_expired should return False for fresh position."""
        assert sample_long_position.is_expired() is False

    def test_position_without_opened_at_not_expired(self):
        """is_expired should return False if opened_at is None."""
        position = Position(
            position_id="TEST",
            signal_id="SIG",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=0.001,
            entry_price=50000.0,
            stop_loss=48000.0,
            take_profit=55000.0,
            status=PositionStatus.OPEN,
            opened_at=None,  # Not opened
        )
        assert position.is_expired() is False

    def test_closed_position_not_expired(self, closed_position):
        """is_expired should return False for closed position."""
        assert closed_position.is_expired() is False

    def test_get_hold_days_returns_correct_value(self):
        """get_hold_days should return correct number of days."""
        opened_at = datetime.now(timezone.utc) - timedelta(days=5, hours=12)
        position = Position(
            position_id="TEST",
            signal_id="SIG",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=0.001,
            entry_price=50000.0,
            stop_loss=48000.0,
            take_profit=55000.0,
            opened_at=opened_at,
        )
        hold_days = position.get_hold_days()
        assert hold_days == pytest.approx(5.5, abs=0.1)  # ~5.5 days

    def test_get_hold_days_returns_zero_without_opened_at(self):
        """get_hold_days should return 0 if opened_at is None."""
        position = Position(
            position_id="TEST",
            signal_id="SIG",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=0.001,
            entry_price=50000.0,
            stop_loss=48000.0,
            take_profit=55000.0,
            opened_at=None,
        )
        assert position.get_hold_days() == 0.0

    def test_exactly_max_hold_days_is_expired(self):
        """Position exactly at max_hold_days should be expired."""
        opened_at = datetime.now(timezone.utc) - timedelta(days=14)
        position = Position(
            position_id="TEST",
            signal_id="SIG",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=0.001,
            entry_price=50000.0,
            stop_loss=48000.0,
            take_profit=55000.0,
            status=PositionStatus.OPEN,
            opened_at=opened_at,
            max_hold_days=14,
        )
        assert position.is_expired() is True

    def test_one_second_before_max_hold_not_expired(self):
        """Position one second before max_hold_days should NOT be expired."""
        opened_at = datetime.now(timezone.utc) - timedelta(days=14) + timedelta(seconds=1)
        position = Position(
            position_id="TEST",
            signal_id="SIG",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=0.001,
            entry_price=50000.0,
            stop_loss=48000.0,
            take_profit=55000.0,
            status=PositionStatus.OPEN,
            opened_at=opened_at,
            max_hold_days=14,
        )
        assert position.is_expired() is False


class TestPositionCreation:
    """Test Position dataclass creation and defaults."""

    def test_created_at_auto_set(self):
        """created_at should be auto-set if not provided."""
        before = datetime.now(timezone.utc)
        position = Position(
            position_id="TEST",
            signal_id="SIG",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=0.001,
            entry_price=50000.0,
            stop_loss=48000.0,
            take_profit=55000.0,
        )
        after = datetime.now(timezone.utc)

        assert position.created_at is not None
        assert before <= position.created_at <= after

    def test_default_status_is_pending(self):
        """Default status should be PENDING."""
        position = Position(
            position_id="TEST",
            signal_id="SIG",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=0.001,
            entry_price=50000.0,
            stop_loss=48000.0,
            take_profit=55000.0,
        )
        assert position.status == PositionStatus.PENDING

    def test_default_max_hold_days_is_14(self):
        """Default max_hold_days should be 14."""
        position = Position(
            position_id="TEST",
            signal_id="SIG",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=0.001,
            entry_price=50000.0,
            stop_loss=48000.0,
            take_profit=55000.0,
        )
        assert position.max_hold_days == 14


class TestTradeOrderCreation:
    """Test TradeOrder dataclass creation and defaults."""

    def test_created_at_auto_set(self):
        """created_at should be auto-set if not provided."""
        before = datetime.now(timezone.utc)
        order = TradeOrder(
            order_id="TEST",
            signal_id="SIG",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.001,
        )
        after = datetime.now(timezone.utc)

        assert order.created_at is not None
        assert before <= order.created_at <= after

    def test_default_status_is_pending(self):
        """Default status should be PENDING."""
        order = TradeOrder(
            order_id="TEST",
            signal_id="SIG",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.001,
        )
        assert order.status == OrderStatus.PENDING

    def test_default_position_side_is_both(self):
        """Default position_side should be BOTH."""
        order = TradeOrder(
            order_id="TEST",
            signal_id="SIG",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.001,
        )
        assert order.position_side == PositionSide.BOTH


class TestEnumValues:
    """Test that enum values match expected strings."""

    def test_order_side_values(self):
        """OrderSide enum values should match Binance API."""
        assert OrderSide.BUY.value == "BUY"
        assert OrderSide.SELL.value == "SELL"

    def test_position_side_values(self):
        """PositionSide enum values should match Binance API."""
        assert PositionSide.LONG.value == "LONG"
        assert PositionSide.SHORT.value == "SHORT"
        assert PositionSide.BOTH.value == "BOTH"

    def test_order_type_values(self):
        """OrderType enum values should match Binance API."""
        assert OrderType.MARKET.value == "MARKET"
        assert OrderType.LIMIT.value == "LIMIT"
        assert OrderType.STOP_MARKET.value == "STOP_MARKET"
        assert OrderType.TAKE_PROFIT_MARKET.value == "TAKE_PROFIT_MARKET"

    def test_position_status_values(self):
        """PositionStatus enum values should be correct."""
        assert PositionStatus.PENDING.value == "PENDING"
        assert PositionStatus.OPEN.value == "OPEN"
        assert PositionStatus.CLOSED.value == "CLOSED"
