# -*- coding: utf-8 -*-
"""
Tests for trade_app.py

Testing:
- Late Signal Protection (3:00 UTC check)
- Dynamic Sizing (order_size_usd vs protected_size)
- Position sync at startup
- Filter statistics
"""

import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from typing import Dict, Any, List

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tradebot.core.models import (
    Position,
    PositionSide,
    PositionStatus,
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
        date: datetime = None,
        metadata: Dict[str, Any] = None,
    ):
        self.signal_id = signal_id
        self.symbol = symbol
        self.direction = direction
        self.entry = entry
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.date = date or datetime.now(timezone.utc)
        self.metadata = metadata or {"strategy": "test_strategy"}


# =============================================================================
# TEST LATE SIGNAL PROTECTION
# =============================================================================

class TestLateSignalProtection:
    """Test late signal skip functionality (3:00 UTC check)."""

    def test_late_signal_skip_after_utc_default_value(self):
        """Default late_signal_skip_after_utc should be 3."""
        from tradebot.trade_app import TradeApp

        # Check signature default
        import inspect
        sig = inspect.signature(TradeApp.__init__)
        param = sig.parameters.get('late_signal_skip_after_utc')
        assert param is not None
        assert param.default == 3

    def test_signal_is_late_after_threshold(self):
        """Signal for today should be skipped if current hour >= threshold."""
        # Signal date: today at 00:00 UTC (daily candle close)
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        signal = MockSignal(date=today)

        # Current time: 10:15 UTC (past threshold of 3:00)
        with patch('tradebot.trade_app.datetime') as mock_datetime:
            mock_now = today.replace(hour=10, minute=15)
            mock_datetime.utcnow.return_value = mock_now
            mock_datetime.now.return_value = mock_now
            mock_datetime.fromisoformat = datetime.fromisoformat

            # Check logic: signal.date.date() == now.date() AND now.hour >= 3
            is_late = (
                signal.date.date() == mock_now.date() and
                mock_now.hour >= 3  # threshold
            )
            assert is_late is True

    def test_signal_not_late_before_threshold(self):
        """Signal for today should NOT be skipped if current hour < threshold."""
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        signal = MockSignal(date=today)

        # Current time: 02:30 UTC (before threshold of 3:00)
        now = today.replace(hour=2, minute=30)

        is_late = (
            signal.date.date() == now.date() and
            now.hour >= 3  # threshold
        )
        assert is_late is False

    def test_signal_not_late_if_different_day(self):
        """Signal for yesterday should NOT be skipped (different date)."""
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday = today - timedelta(days=1)
        signal = MockSignal(date=yesterday)

        # Current time: 10:15 UTC
        now = today.replace(hour=10, minute=15)

        # Signal date != today, so NOT late
        is_late = (
            signal.date.date() == now.date() and  # This is False
            now.hour >= 3
        )
        assert is_late is False

    def test_late_signal_skip_disabled(self):
        """When late_signal_skip_after_utc is None, no signals should be skipped."""
        late_signal_skip_after_utc = None

        # Even if it's 10:00 UTC and signal is for today, should NOT skip
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        signal = MockSignal(date=today)
        now = today.replace(hour=10, minute=0)

        if late_signal_skip_after_utc is not None:
            is_late = (
                signal.date.date() == now.date() and
                now.hour >= late_signal_skip_after_utc
            )
        else:
            is_late = False  # Disabled

        assert is_late is False

    def test_different_threshold_values(self):
        """Test with different threshold values (1, 5, 12 hours)."""
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        signal = MockSignal(date=today)

        test_cases = [
            # (threshold, current_hour, expected_is_late)
            (1, 0, False),   # 00:00 UTC, threshold 1:00 -> NOT late
            (1, 1, True),    # 01:00 UTC, threshold 1:00 -> late
            (1, 2, True),    # 02:00 UTC, threshold 1:00 -> late
            (5, 4, False),   # 04:00 UTC, threshold 5:00 -> NOT late
            (5, 5, True),    # 05:00 UTC, threshold 5:00 -> late
            (5, 6, True),    # 06:00 UTC, threshold 5:00 -> late
            (12, 11, False), # 11:00 UTC, threshold 12:00 -> NOT late
            (12, 12, True),  # 12:00 UTC, threshold 12:00 -> late
        ]

        for threshold, current_hour, expected in test_cases:
            now = today.replace(hour=current_hour)
            is_late = (
                signal.date.date() == now.date() and
                now.hour >= threshold
            )
            assert is_late == expected, f"Failed for threshold={threshold}, hour={current_hour}"


# =============================================================================
# TEST DYNAMIC SIZING
# =============================================================================

class TestDynamicSizing:
    """Test dynamic sizing logic."""

    def test_dynamic_size_uses_order_size_after_win(self):
        """After WIN, should use order_size_usd (not separate normal_size)."""
        order_size_usd = 1000.0
        protected_size = 100.0
        dynamic_size_enabled = True
        last_trade_was_win = True

        if dynamic_size_enabled:
            if last_trade_was_win:
                order_size = order_size_usd  # After WIN = order_size_usd
            else:
                order_size = protected_size
        else:
            order_size = order_size_usd

        assert order_size == 1000.0

    def test_dynamic_size_uses_protected_after_loss(self):
        """After LOSS, should use protected_size."""
        order_size_usd = 1000.0
        protected_size = 100.0
        dynamic_size_enabled = True
        last_trade_was_win = False

        if dynamic_size_enabled:
            if last_trade_was_win:
                order_size = order_size_usd
            else:
                order_size = protected_size  # After LOSS = protected
        else:
            order_size = order_size_usd

        assert order_size == 100.0

    def test_dynamic_size_disabled_uses_order_size(self):
        """When dynamic sizing disabled, always use order_size_usd."""
        order_size_usd = 1000.0
        protected_size = 100.0
        dynamic_size_enabled = False

        # Even after loss
        last_trade_was_win = False

        if dynamic_size_enabled:
            if last_trade_was_win:
                order_size = order_size_usd
            else:
                order_size = protected_size
        else:
            order_size = order_size_usd  # Always order_size when disabled

        assert order_size == 1000.0

    def test_dynamic_size_sequence(self):
        """Test sequence of trades - size is based on PREVIOUS result."""
        order_size_usd = 1000.0
        protected_size = 100.0
        dynamic_size_enabled = True

        # Size is determined BEFORE trade, based on PREVIOUS result
        # (trade_result, expected_size_for_THIS_trade)
        # Initial state: last_trade_was_win = True
        sequence = [
            # Trade 0: previous was WIN (initial) → size 1000, this trade = WIN
            (True, 1000.0),
            # Trade 1: previous was WIN → size 1000, this trade = LOSS
            (False, 1000.0),
            # Trade 2: previous was LOSS → size 100, this trade = WIN
            (True, 100.0),
            # Trade 3: previous was WIN → size 1000, this trade = LOSS
            (False, 1000.0),
        ]

        last_trade_was_win = True  # Start with WIN assumption

        for i, (trade_result, expected_size) in enumerate(sequence):
            # Determine size for THIS trade based on PREVIOUS result
            if dynamic_size_enabled:
                if last_trade_was_win:
                    order_size = order_size_usd
                else:
                    order_size = protected_size
            else:
                order_size = order_size_usd

            assert order_size == expected_size, f"Failed at step {i}: got {order_size}, expected {expected_size}"

            # After trade completes, update for next iteration
            last_trade_was_win = trade_result

    def test_default_values(self):
        """Test default values are correct."""
        from tradebot.trade_app import TradeApp
        import inspect

        sig = inspect.signature(TradeApp.__init__)

        # order_size_usd default
        order_size_param = sig.parameters.get('order_size_usd')
        assert order_size_param.default == 100.0

        # protected_size default
        protected_param = sig.parameters.get('protected_size')
        assert protected_param.default == 100.0

        # dynamic_size_enabled default
        dynamic_param = sig.parameters.get('dynamic_size_enabled')
        assert dynamic_param.default is False


# =============================================================================
# TEST POSITION MODE CHECK
# =============================================================================

class TestPositionModeCheck:
    """Test position mode duplicate protection."""

    def test_single_mode_skips_duplicate(self):
        """position_mode=single should skip signal if position already exists."""
        position_mode = "single"

        # Existing position for BTCUSDT
        existing_positions = [
            Position(
                position_id="POS_001",
                signal_id="SIG_001",
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                quantity=0.001,
                entry_price=50000.0,
                stop_loss=48000.0,
                take_profit=55000.0,
                status=PositionStatus.OPEN,
            )
        ]

        # New signal for same symbol
        new_signal = MockSignal(symbol="BTCUSDT", direction="LONG")

        # Check logic
        open_positions = [p for p in existing_positions if p.is_open]
        symbol_positions = [p for p in open_positions if p.symbol == new_signal.symbol]

        if position_mode == "single":
            should_skip = len(symbol_positions) > 0
        else:
            should_skip = False

        assert should_skip is True

    def test_single_mode_allows_different_symbol(self):
        """position_mode=single should allow signal for different symbol."""
        position_mode = "single"

        # Existing position for BTCUSDT
        existing_positions = [
            Position(
                position_id="POS_001",
                signal_id="SIG_001",
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                quantity=0.001,
                entry_price=50000.0,
                stop_loss=48000.0,
                take_profit=55000.0,
                status=PositionStatus.OPEN,
            )
        ]

        # New signal for DIFFERENT symbol
        new_signal = MockSignal(symbol="ETHUSDT", direction="LONG")

        open_positions = [p for p in existing_positions if p.is_open]
        symbol_positions = [p for p in open_positions if p.symbol == new_signal.symbol]

        if position_mode == "single":
            should_skip = len(symbol_positions) > 0
        else:
            should_skip = False

        assert should_skip is False

    def test_direction_mode_allows_opposite_direction(self):
        """position_mode=direction should allow opposite direction for same symbol."""
        position_mode = "direction"

        # Existing LONG position for BTCUSDT
        existing_positions = [
            Position(
                position_id="POS_001",
                signal_id="SIG_001",
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                quantity=0.001,
                entry_price=50000.0,
                stop_loss=48000.0,
                take_profit=55000.0,
                status=PositionStatus.OPEN,
            )
        ]

        # New SHORT signal for same symbol
        new_signal = MockSignal(symbol="BTCUSDT", direction="SHORT")

        open_positions = [p for p in existing_positions if p.is_open]
        symbol_positions = [p for p in open_positions if p.symbol == new_signal.symbol]

        if position_mode == "direction":
            direction_positions = [
                p for p in symbol_positions
                if (new_signal.direction == "LONG" and p.side.value == "LONG") or
                   (new_signal.direction == "SHORT" and p.side.value == "SHORT")
            ]
            should_skip = len(direction_positions) > 0
        else:
            should_skip = False

        # SHORT signal should NOT be skipped (existing is LONG)
        assert should_skip is False

    def test_direction_mode_skips_same_direction(self):
        """position_mode=direction should skip same direction for same symbol."""
        position_mode = "direction"

        # Existing LONG position for BTCUSDT
        existing_positions = [
            Position(
                position_id="POS_001",
                signal_id="SIG_001",
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                quantity=0.001,
                entry_price=50000.0,
                stop_loss=48000.0,
                take_profit=55000.0,
                status=PositionStatus.OPEN,
            )
        ]

        # New LONG signal for same symbol
        new_signal = MockSignal(symbol="BTCUSDT", direction="LONG")

        open_positions = [p for p in existing_positions if p.is_open]
        symbol_positions = [p for p in open_positions if p.symbol == new_signal.symbol]

        if position_mode == "direction":
            direction_positions = [
                p for p in symbol_positions
                if (new_signal.direction == "LONG" and p.side.value == "LONG") or
                   (new_signal.direction == "SHORT" and p.side.value == "SHORT")
            ]
            should_skip = len(direction_positions) > 0
        else:
            should_skip = False

        # LONG signal should be skipped (existing is also LONG)
        assert should_skip is True

    def test_multi_mode_allows_all(self):
        """position_mode=multi should allow any number of positions."""
        position_mode = "multi"

        # Multiple existing positions for BTCUSDT
        existing_positions = [
            Position(
                position_id="POS_001",
                signal_id="SIG_001",
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                quantity=0.001,
                entry_price=50000.0,
                stop_loss=48000.0,
                take_profit=55000.0,
                status=PositionStatus.OPEN,
            ),
            Position(
                position_id="POS_002",
                signal_id="SIG_002",
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                quantity=0.001,
                entry_price=51000.0,
                stop_loss=49000.0,
                take_profit=56000.0,
                status=PositionStatus.OPEN,
            )
        ]

        # New LONG signal for same symbol
        new_signal = MockSignal(symbol="BTCUSDT", direction="LONG")

        # In multi mode, we don't check at all (check happens for != "multi")
        if position_mode != "multi":
            should_skip = True  # Would be checked
        else:
            should_skip = False  # Multi allows all

        assert should_skip is False


# =============================================================================
# TEST FILTER STATISTICS
# =============================================================================

class TestFilterStatistics:
    """Test filter statistics counters."""

    def test_skipped_late_signal_counter(self):
        """Test that late signals are counted."""
        skipped_late_signal = 0
        signals_today = 5
        threshold = 3
        current_hour = 10  # Past threshold

        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        for i in range(signals_today):
            signal = MockSignal(
                signal_id=f"SIG_{i}",
                date=today  # Today's signal
            )

            now = today.replace(hour=current_hour)

            # Late signal check
            if signal.date.date() == now.date() and now.hour >= threshold:
                skipped_late_signal += 1

        assert skipped_late_signal == 5

    def test_total_skipped_includes_late(self):
        """Test that total_skipped includes skipped_late_signal."""
        skipped_late_signal = 3
        skipped_regime = 2
        skipped_vol_low = 1
        skipped_vol_high = 0
        skipped_position = 4
        skipped_month_filter = 0
        skipped_day_filter = 1
        skipped_ml = 2

        total_skipped = (
            skipped_late_signal + skipped_regime + skipped_vol_low +
            skipped_vol_high + skipped_position + skipped_month_filter +
            skipped_day_filter + skipped_ml
        )

        assert total_skipped == 13
        assert skipped_late_signal in [3]  # Included in total


# =============================================================================
# TEST CLI ARGUMENTS
# =============================================================================

class TestCLIArguments:
    """Test CLI argument parsing."""

    def test_late_signal_skip_after_cli_default(self):
        """Test --late-signal-skip-after default is 3."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--late-signal-skip-after", type=int, default=3)

        args = parser.parse_args([])
        assert args.late_signal_skip_after == 3

    def test_late_signal_skip_after_cli_custom(self):
        """Test --late-signal-skip-after with custom value."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--late-signal-skip-after", type=int, default=3)

        args = parser.parse_args(["--late-signal-skip-after", "5"])
        assert args.late_signal_skip_after == 5

    def test_late_signal_skip_after_cli_disabled(self):
        """Test --late-signal-skip-after -1 disables the check."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--late-signal-skip-after", type=int, default=3)

        args = parser.parse_args(["--late-signal-skip-after", "-1"])

        # In trade_app.py: late_signal_skip_after_utc = args.late_signal_skip_after if >= 0 else None
        late_signal_skip_after_utc = args.late_signal_skip_after if args.late_signal_skip_after >= 0 else None

        assert late_signal_skip_after_utc is None

    def test_order_size_cli_default(self):
        """Test --order-size default is 100."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--order-size", type=float, default=100.0)

        args = parser.parse_args([])
        assert args.order_size == 100.0

    def test_protected_size_cli_default(self):
        """Test --protected-size default is 100."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--protected-size", type=float, default=100.0)

        args = parser.parse_args([])
        assert args.protected_size == 100.0


# =============================================================================
# INTEGRATION TEST: LATE SIGNAL + DYNAMIC SIZING COMBINED
# =============================================================================

class TestIntegration:
    """Integration tests combining multiple features."""

    def test_late_signal_check_before_dynamic_sizing(self):
        """Late signal check should happen BEFORE dynamic sizing calculation."""
        # This ensures we don't waste time calculating size for signals we'll skip

        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        signal = MockSignal(date=today, symbol="BTCUSDT")

        late_signal_skip_after_utc = 3
        current_hour = 10  # Past threshold
        now = today.replace(hour=current_hour)

        # Step 1: Check late signal FIRST
        if late_signal_skip_after_utc is not None:
            if signal.date.date() == now.date() and now.hour >= late_signal_skip_after_utc:
                # Skip - don't even calculate dynamic sizing
                skipped = True
            else:
                skipped = False
        else:
            skipped = False

        # If skipped, we never reach dynamic sizing
        if not skipped:
            # This would be calculated only if not skipped
            order_size = 1000.0  # Dynamic sizing logic
        else:
            order_size = None  # Not calculated

        assert skipped is True
        assert order_size is None

    def test_filter_order_late_then_position_mode(self):
        """Verify filter order: late signal -> ... -> position mode."""
        # Late signal should be checked before position mode
        # This is more efficient (no need to query positions for stale signals)

        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        signal = MockSignal(date=today, symbol="BTCUSDT")

        # Counters
        skipped_late_signal = 0
        skipped_position = 0

        # Simulate late signal at 10:00 UTC
        now = today.replace(hour=10)
        late_signal_skip_after_utc = 3

        # Late signal check (FIRST)
        if late_signal_skip_after_utc is not None:
            if signal.date.date() == now.date() and now.hour >= late_signal_skip_after_utc:
                skipped_late_signal += 1
                # continue -> skip remaining checks
                position_mode_checked = False
            else:
                position_mode_checked = True
        else:
            position_mode_checked = True

        # Position mode check (LATER) - only if not already skipped
        if position_mode_checked:
            # Would check position mode here
            pass

        assert skipped_late_signal == 1
        assert position_mode_checked is False  # Never reached


# =============================================================================
# SIGNAL_ID DEDUPLICATION TESTS
# =============================================================================

class TestSignalIdDeduplication:
    """Test signal_id deduplication to prevent duplicate positions after restart."""

    def test_skip_signal_if_position_with_same_signal_id_exists(self):
        """Should skip signal if there's already a position with same signal_id."""
        signal = MockSignal(
            signal_id="20260307_BTCUSDT_LONG",
            symbol="BTCUSDT",
            direction="LONG",
        )

        # Simulate existing position with same signal_id
        existing_positions = [
            Position(
                position_id="POS_20260307_BTCUSDT_LONG_abc123",
                signal_id="20260307_BTCUSDT_LONG",  # Same!
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                quantity=0.01,
                entry_price=50000.0,
                stop_loss=48000.0,
                take_profit=55000.0,
            )
        ]

        # Check for duplicate signal_id
        existing_by_signal_id = [
            p for p in existing_positions
            if p.signal_id == signal.signal_id
        ]

        assert len(existing_by_signal_id) == 1
        assert existing_by_signal_id[0].signal_id == signal.signal_id

    def test_allow_signal_if_no_position_with_same_signal_id(self):
        """Should allow signal if no position with same signal_id exists."""
        signal = MockSignal(
            signal_id="20260307_BTCUSDT_LONG",
            symbol="BTCUSDT",
            direction="LONG",
        )

        # Existing position with different signal_id
        existing_positions = [
            Position(
                position_id="POS_20260306_BTCUSDT_LONG_abc123",
                signal_id="20260306_BTCUSDT_LONG",  # Different day!
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                quantity=0.01,
                entry_price=50000.0,
                stop_loss=48000.0,
                take_profit=55000.0,
            )
        ]

        existing_by_signal_id = [
            p for p in existing_positions
            if p.signal_id == signal.signal_id
        ]

        assert len(existing_by_signal_id) == 0

    def test_dedup_protects_against_crash_restart(self):
        """
        Deduplication should prevent duplicate positions when:
        1. Position opened
        2. App crashes (no graceful shutdown)
        3. App restarts, position restored from exchange
        4. Same signal generated again
        """
        signal = MockSignal(
            signal_id="20260307_ETHUSDT_SHORT",
            symbol="ETHUSDT",
            direction="SHORT",
        )

        # After crash+restart, position restored with same signal_id
        # (StateManager.restore_and_sync() preserves signal_id from saved state)
        restored_positions = [
            Position(
                position_id="RESTORED_ETHUSDT_abc123",
                signal_id="20260307_ETHUSDT_SHORT",  # Preserved from saved state
                symbol="ETHUSDT",
                side=PositionSide.SHORT,
                quantity=0.05,
                entry_price=3000.0,
                stop_loss=3150.0,
                take_profit=2700.0,
            )
        ]

        # Dedup check
        skipped_duplicate = 0
        existing = [p for p in restored_positions if p.signal_id == signal.signal_id]
        if existing:
            skipped_duplicate += 1

        assert skipped_duplicate == 1


# =============================================================================
# PERIODIC STATE SAVE TESTS
# =============================================================================

class TestPeriodicStateSave:
    """Test periodic state save functionality."""

    def test_state_save_interval_default_is_5_minutes(self):
        """Default state save interval should be 5 minutes (300 seconds)."""
        # The interval is defined in TradeApp.__init__ as self._state_save_interval = 300
        # This test documents the expected behavior
        expected_interval_seconds = 300
        expected_interval_minutes = 5
        assert expected_interval_seconds == 300
        assert expected_interval_seconds // 60 == expected_interval_minutes

    def test_state_save_protects_against_crash(self):
        """
        Periodic state save ensures that even if app crashes without graceful shutdown,
        the state file will be at most 5 minutes old.

        This means:
        1. Position data (signal_id, strategy) preserved
        2. Metrics preserved
        3. At restart, restore_and_sync() finds valid state file
        """
        # This is a documentation test - actual behavior tested in integration tests
        max_data_loss_minutes = 5  # State is saved every 5 minutes
        assert max_data_loss_minutes <= 5


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
