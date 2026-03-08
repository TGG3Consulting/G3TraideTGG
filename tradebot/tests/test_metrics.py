# -*- coding: utf-8 -*-
"""
Tests for engine/metrics.py

Testing:
- MetricsTracker.record_trade()
- MetricsTracker.get_dashboard()
- MetricsTracker.to_dict() / from_dict() (persistence)
- PeriodStats calculations
- Drawdown tracking
- Equity curve
"""

import pytest
from datetime import datetime, timezone, timedelta
from tradebot.core.models import Position, PositionSide, PositionStatus
from tradebot.engine.metrics import MetricsTracker, PeriodStats, TradeRecord


class TestPeriodStats:
    """Test PeriodStats class."""

    def test_empty_stats(self):
        """Empty stats should have zero values."""
        stats = PeriodStats()
        assert stats.trades == 0
        assert stats.wins == 0
        assert stats.losses == 0
        assert stats.total_pnl == 0.0
        assert stats.win_rate == 0.0
        assert stats.profit_factor == 0.0

    def test_add_winning_trade(self):
        """Adding a winning trade should update stats correctly."""
        stats = PeriodStats()
        stats.add_trade(10.0)  # +$10 profit

        assert stats.trades == 1
        assert stats.wins == 1
        assert stats.losses == 0
        assert stats.total_pnl == 10.0
        assert stats.total_win_pnl == 10.0
        assert stats.total_loss_pnl == 0.0
        assert stats.max_win == 10.0

    def test_add_losing_trade(self):
        """Adding a losing trade should update stats correctly."""
        stats = PeriodStats()
        stats.add_trade(-5.0)  # -$5 loss

        assert stats.trades == 1
        assert stats.wins == 0
        assert stats.losses == 1
        assert stats.total_pnl == -5.0
        assert stats.total_win_pnl == 0.0
        assert stats.total_loss_pnl == -5.0
        assert stats.max_loss == -5.0

    def test_win_rate_calculation(self):
        """Win rate should be calculated correctly."""
        stats = PeriodStats()
        stats.add_trade(10.0)  # win
        stats.add_trade(-5.0)  # loss
        stats.add_trade(15.0)  # win
        stats.add_trade(8.0)   # win

        # 3 wins out of 4 trades = 75%
        assert stats.win_rate == pytest.approx(75.0, rel=1e-3)

    def test_profit_factor_calculation(self):
        """Profit factor should be gross_profit / gross_loss."""
        stats = PeriodStats()
        stats.add_trade(20.0)   # win
        stats.add_trade(-10.0)  # loss
        stats.add_trade(30.0)   # win
        stats.add_trade(-5.0)   # loss

        # Total wins: 50, Total losses: 15
        # Profit factor: 50 / 15 = 3.33
        assert stats.profit_factor == pytest.approx(3.33, rel=0.01)

    def test_profit_factor_no_losses(self):
        """Profit factor should be inf if no losses."""
        stats = PeriodStats()
        stats.add_trade(10.0)
        stats.add_trade(20.0)

        assert stats.profit_factor == float('inf')

    def test_profit_factor_no_wins(self):
        """Profit factor should be 0 if no wins."""
        stats = PeriodStats()
        stats.add_trade(-10.0)
        stats.add_trade(-20.0)

        assert stats.profit_factor == 0.0

    def test_avg_win_avg_loss(self):
        """Average win and loss should be calculated correctly."""
        stats = PeriodStats()
        stats.add_trade(10.0)
        stats.add_trade(20.0)
        stats.add_trade(-5.0)
        stats.add_trade(-15.0)

        assert stats.avg_win == pytest.approx(15.0)   # (10+20)/2
        assert stats.avg_loss == pytest.approx(-10.0)  # (-5-15)/2

    def test_expectancy(self):
        """Expectancy should be calculated correctly."""
        stats = PeriodStats()
        stats.add_trade(20.0)   # win
        stats.add_trade(10.0)   # win
        stats.add_trade(-5.0)   # loss
        stats.add_trade(-5.0)   # loss

        # Win rate: 50%, Avg win: 15, Loss rate: 50%, Avg loss: -5
        # Expectancy = (0.5 * 15) + (0.5 * -5) = 7.5 - 2.5 = 5.0
        assert stats.expectancy == pytest.approx(5.0)


class TestMetricsTracker:
    """Test MetricsTracker class."""

    @pytest.fixture
    def tracker(self):
        """Create fresh MetricsTracker for each test."""
        return MetricsTracker()

    @pytest.fixture
    def sample_closed_position(self):
        """Create a sample closed position."""
        return Position(
            position_id="POS_001",
            signal_id="SIG_001",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=0.001,
            entry_price=50000.0,
            stop_loss=48000.0,
            take_profit=55000.0,
            status=PositionStatus.CLOSED,
            exit_price=55000.0,
            opened_at=datetime.now(timezone.utc) - timedelta(hours=5),
            closed_at=datetime.now(timezone.utc),
            strategy="momentum",
        )

    def test_initial_state(self, tracker):
        """Tracker should start with empty state."""
        assert len(tracker.trades) == 0
        assert tracker.total_stats.trades == 0
        assert tracker._current_equity == 0.0
        assert tracker._peak_equity == 0.0
        assert tracker._max_drawdown == 0.0

    def test_record_winning_trade(self, tracker, sample_closed_position):
        """Recording a winning trade should update all stats."""
        tracker.record_trade(sample_closed_position, "TP", 10.0)

        assert len(tracker.trades) == 1
        assert tracker.total_stats.trades == 1
        assert tracker.total_stats.wins == 1
        assert tracker.total_stats.total_pnl == 10.0
        assert tracker._current_equity == 10.0

    def test_record_losing_trade(self, tracker, sample_closed_position):
        """Recording a losing trade should update all stats."""
        tracker.record_trade(sample_closed_position, "SL", -5.0)

        assert len(tracker.trades) == 1
        assert tracker.total_stats.trades == 1
        assert tracker.total_stats.losses == 1
        assert tracker.total_stats.total_pnl == -5.0
        assert tracker._current_equity == -5.0

    def test_strategy_stats_updated(self, tracker, sample_closed_position):
        """Strategy stats should be updated correctly."""
        tracker.record_trade(sample_closed_position, "TP", 10.0)

        assert "momentum" in tracker.strategy_stats
        assert tracker.strategy_stats["momentum"].trades == 1
        assert tracker.strategy_stats["momentum"].total_pnl == 10.0

    def test_symbol_stats_updated(self, tracker, sample_closed_position):
        """Symbol stats should be updated correctly."""
        tracker.record_trade(sample_closed_position, "TP", 10.0)

        assert "BTCUSDT" in tracker.symbol_stats
        assert tracker.symbol_stats["BTCUSDT"].trades == 1
        assert tracker.symbol_stats["BTCUSDT"].total_pnl == 10.0

    def test_exit_reason_stats_updated(self, tracker, sample_closed_position):
        """Exit reason stats should be updated correctly."""
        tracker.record_trade(sample_closed_position, "TP", 10.0)

        assert "TP" in tracker.exit_reason_stats
        assert tracker.exit_reason_stats["TP"].trades == 1

    def test_direction_stats_updated(self, tracker, sample_closed_position):
        """Direction stats should be updated correctly."""
        tracker.record_trade(sample_closed_position, "TP", 10.0)

        assert "LONG" in tracker.direction_stats
        assert tracker.direction_stats["LONG"].trades == 1

    def test_equity_curve_updated(self, tracker, sample_closed_position):
        """Equity curve should track cumulative PnL."""
        tracker.record_trade(sample_closed_position, "TP", 10.0)
        tracker.record_trade(sample_closed_position, "SL", -3.0)
        tracker.record_trade(sample_closed_position, "TP", 5.0)

        assert len(tracker.equity_curve) == 3
        # Check cumulative values
        assert tracker.equity_curve[0][1] == 10.0
        assert tracker.equity_curve[1][1] == 7.0  # 10 - 3
        assert tracker.equity_curve[2][1] == 12.0  # 7 + 5

    def test_drawdown_tracking(self, tracker, sample_closed_position):
        """Drawdown should be tracked correctly."""
        tracker.record_trade(sample_closed_position, "TP", 20.0)  # Peak = 20
        tracker.record_trade(sample_closed_position, "SL", -5.0)  # DD = 5
        tracker.record_trade(sample_closed_position, "SL", -10.0) # DD = 15

        assert tracker._peak_equity == 20.0
        assert tracker._current_equity == 5.0  # 20 - 5 - 10
        assert tracker._max_drawdown == 15.0  # Peak - Current

    def test_drawdown_percentage(self, tracker, sample_closed_position):
        """Drawdown percentage should be calculated correctly."""
        tracker.record_trade(sample_closed_position, "TP", 100.0)  # Peak = 100
        tracker.record_trade(sample_closed_position, "SL", -25.0)  # Current = 75

        # DD% = (100 - 75) / 100 * 100 = 25%
        assert tracker._max_drawdown_pct == pytest.approx(25.0, rel=0.01)

    def test_daily_pnl_tracking(self, tracker, sample_closed_position):
        """Daily PnL should be accumulated correctly."""
        tracker.record_trade(sample_closed_position, "TP", 10.0)
        tracker.record_trade(sample_closed_position, "SL", -3.0)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert today in tracker.daily_pnl
        assert tracker.daily_pnl[today] == 7.0  # 10 - 3


class TestMetricsTrackerDashboard:
    """Test MetricsTracker.get_dashboard() method."""

    @pytest.fixture
    def tracker_with_trades(self):
        """Create tracker with some trades."""
        tracker = MetricsTracker()
        positions = [
            Position(
                position_id=f"POS_{i}",
                signal_id=f"SIG_{i}",
                symbol="BTCUSDT" if i % 2 == 0 else "ETHUSDT",
                side=PositionSide.LONG if i % 3 == 0 else PositionSide.SHORT,
                quantity=0.001,
                entry_price=50000.0,
                stop_loss=48000.0,
                take_profit=55000.0,
                status=PositionStatus.CLOSED,
                opened_at=datetime.now(timezone.utc) - timedelta(hours=i),
                strategy="momentum" if i % 2 == 0 else "reversal",
            )
            for i in range(5)
        ]
        pnls = [10.0, -5.0, 15.0, -3.0, 8.0]  # 3 wins, 2 losses
        for pos, pnl in zip(positions, pnls):
            exit_reason = "TP" if pnl > 0 else "SL"
            tracker.record_trade(pos, exit_reason, pnl)

        return tracker

    def test_dashboard_total_trades(self, tracker_with_trades):
        """Dashboard should show correct total trades."""
        dashboard = tracker_with_trades.get_dashboard()
        assert dashboard["total_trades"] == 5

    def test_dashboard_total_pnl(self, tracker_with_trades):
        """Dashboard should show correct total PnL."""
        dashboard = tracker_with_trades.get_dashboard()
        # 10 - 5 + 15 - 3 + 8 = 25
        assert dashboard["total_pnl"] == 25.0

    def test_dashboard_win_rate(self, tracker_with_trades):
        """Dashboard should show correct win rate."""
        dashboard = tracker_with_trades.get_dashboard()
        # 3 wins out of 5 = 60%
        assert dashboard["win_rate"] == pytest.approx(60.0, rel=0.01)

    def test_dashboard_by_strategy(self, tracker_with_trades):
        """Dashboard should include strategy breakdown."""
        dashboard = tracker_with_trades.get_dashboard()
        assert "by_strategy" in dashboard
        assert "momentum" in dashboard["by_strategy"]
        assert "reversal" in dashboard["by_strategy"]

    def test_dashboard_by_exit_reason(self, tracker_with_trades):
        """Dashboard should include exit reason breakdown."""
        dashboard = tracker_with_trades.get_dashboard()
        assert "by_exit_reason" in dashboard
        assert "TP" in dashboard["by_exit_reason"]
        assert "SL" in dashboard["by_exit_reason"]


class TestMetricsTrackerPersistence:
    """Test MetricsTracker.to_dict() / from_dict() methods."""

    @pytest.fixture
    def tracker_with_data(self):
        """Create tracker with test data."""
        tracker = MetricsTracker()
        position = Position(
            position_id="POS_001",
            signal_id="SIG_001",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=0.001,
            entry_price=50000.0,
            stop_loss=48000.0,
            take_profit=55000.0,
            status=PositionStatus.CLOSED,
            exit_price=55000.0,
            opened_at=datetime.now(timezone.utc) - timedelta(hours=5),
            strategy="momentum",
        )
        tracker.record_trade(position, "TP", 10.0)
        tracker.record_trade(position, "SL", -3.0)
        tracker.record_trade(position, "TP", 15.0)
        return tracker

    def test_to_dict_contains_trades(self, tracker_with_data):
        """to_dict should include all trades."""
        data = tracker_with_data.to_dict()
        assert "trades" in data
        assert len(data["trades"]) == 3

    def test_to_dict_contains_drawdown(self, tracker_with_data):
        """to_dict should include drawdown info."""
        data = tracker_with_data.to_dict()
        assert "peak_equity" in data
        assert "current_equity" in data
        assert "max_drawdown" in data
        assert "max_drawdown_pct" in data

    def test_from_dict_restores_trades(self, tracker_with_data):
        """from_dict should restore all trades."""
        data = tracker_with_data.to_dict()
        restored = MetricsTracker.from_dict(data)

        assert len(restored.trades) == 3

    def test_from_dict_restores_total_stats(self, tracker_with_data):
        """from_dict should restore total stats."""
        data = tracker_with_data.to_dict()
        restored = MetricsTracker.from_dict(data)

        assert restored.total_stats.trades == 3
        assert restored.total_stats.total_pnl == pytest.approx(22.0)  # 10-3+15

    def test_from_dict_restores_equity(self, tracker_with_data):
        """from_dict should restore equity values."""
        data = tracker_with_data.to_dict()
        restored = MetricsTracker.from_dict(data)

        assert restored._current_equity == pytest.approx(22.0)
        assert restored._peak_equity == data["peak_equity"]
        assert restored._max_drawdown == data["max_drawdown"]

    def test_from_dict_rebuilds_equity_curve_correctly(self, tracker_with_data):
        """from_dict should rebuild equity curve with incremental values."""
        data = tracker_with_data.to_dict()
        restored = MetricsTracker.from_dict(data)

        # Equity curve should have 3 points with cumulative values
        assert len(restored.equity_curve) == 3

        # Check that values are cumulative (not same value repeated!)
        values = [point[1] for point in restored.equity_curve]
        assert values[0] == pytest.approx(10.0)   # First trade: +10
        assert values[1] == pytest.approx(7.0)    # Second: 10 - 3
        assert values[2] == pytest.approx(22.0)   # Third: 7 + 15

    def test_from_dict_restores_strategy_stats(self, tracker_with_data):
        """from_dict should restore per-strategy stats."""
        data = tracker_with_data.to_dict()
        restored = MetricsTracker.from_dict(data)

        assert "momentum" in restored.strategy_stats
        assert restored.strategy_stats["momentum"].trades == 3

    def test_from_dict_restores_daily_pnl(self, tracker_with_data):
        """from_dict should restore daily PnL."""
        data = tracker_with_data.to_dict()
        restored = MetricsTracker.from_dict(data)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert today in restored.daily_pnl

    def test_round_trip_preserves_data(self, tracker_with_data):
        """to_dict -> from_dict should preserve all data."""
        original_dashboard = tracker_with_data.get_dashboard()

        data = tracker_with_data.to_dict()
        restored = MetricsTracker.from_dict(data)
        restored_dashboard = restored.get_dashboard()

        assert original_dashboard["total_trades"] == restored_dashboard["total_trades"]
        assert original_dashboard["total_pnl"] == pytest.approx(
            restored_dashboard["total_pnl"], rel=0.001
        )
        assert original_dashboard["win_rate"] == pytest.approx(
            restored_dashboard["win_rate"], rel=0.001
        )


class TestTradeRecord:
    """Test TradeRecord dataclass."""

    def test_trade_record_creation(self):
        """TradeRecord should be created with all fields."""
        record = TradeRecord(
            position_id="POS_001",
            symbol="BTCUSDT",
            direction="LONG",
            strategy="momentum",
            entry_price=50000.0,
            exit_price=55000.0,
            quantity=0.001,
            realized_pnl=5.0,
            exit_reason="TP",
            opened_at=datetime.now(timezone.utc) - timedelta(hours=5),
            closed_at=datetime.now(timezone.utc),
            hold_time_hours=5.0,
        )

        assert record.position_id == "POS_001"
        assert record.symbol == "BTCUSDT"
        assert record.direction == "LONG"
        assert record.realized_pnl == 5.0
        assert record.hold_time_hours == 5.0
