# -*- coding: utf-8 -*-
"""
Opportunity Cost Metrics for ML System.

Measures the cost of holding positions vs taking other opportunities.
Helps evaluate if a trade is worth the capital lockup.

Usage:
    calculator = OpportunityCostCalculator()
    metrics = calculator.calculate(trade, missed_signals)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, TYPE_CHECKING

import numpy as np
import structlog

from config.settings import settings

if TYPE_CHECKING:
    from src.signals.models import TradeSignal


logger = structlog.get_logger(__name__)


@dataclass
class MissedSignal:
    """A signal that was missed due to capital lockup."""

    symbol: str
    direction: str
    timestamp: datetime
    potential_pnl_pct: float
    confidence: float


@dataclass
class OpportunityCostMetrics:
    """
    Metrics for opportunity cost analysis.

    Helps understand if a trade was worth the capital lockup.
    """

    # Trade details
    symbol: str
    hold_time_hours: float
    actual_pnl_pct: float

    # Missed opportunities
    missed_signals_count: int = 0
    missed_signals_avg_pnl: float = 0.0
    missed_signals_total_pnl: float = 0.0
    best_missed_pnl: float = 0.0

    # Adjusted metrics
    opportunity_cost_pct: float = 0.0
    adjusted_pnl_pct: float = 0.0

    # Time-adjusted return
    hourly_return: float = 0.0
    missed_hourly_return: float = 0.0

    # Efficiency score
    efficiency_score: float = 1.0  # actual / potential

    # Missed signals details
    missed_signals: List[MissedSignal] = field(default_factory=list)

    @property
    def was_worth_it(self) -> bool:
        """Whether the trade was worth the opportunity cost."""
        return self.adjusted_pnl_pct > 0

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "hold_time_hours": self.hold_time_hours,
            "actual_pnl_pct": self.actual_pnl_pct,
            "missed_signals_count": self.missed_signals_count,
            "missed_signals_avg_pnl": self.missed_signals_avg_pnl,
            "opportunity_cost_pct": self.opportunity_cost_pct,
            "adjusted_pnl_pct": self.adjusted_pnl_pct,
            "hourly_return": self.hourly_return,
            "efficiency_score": self.efficiency_score,
            "was_worth_it": self.was_worth_it,
        }


class OpportunityCostCalculator:
    """
    Calculates opportunity cost for trades.

    Compares actual trade performance against missed alternatives.
    """

    def __init__(
        self,
        min_signal_confidence: float = 0.6,
        avg_signal_pnl_pct: float = 2.0,
    ):
        """
        Initialize calculator.

        Args:
            min_signal_confidence: Minimum confidence for missed signals
            avg_signal_pnl_pct: Average expected signal PnL (for estimation)
        """
        self._min_confidence = min_signal_confidence
        self._avg_signal_pnl = avg_signal_pnl_pct

        # Historical signal performance (for estimation)
        self._signal_history: List[Dict] = []

        logger.info(
            "opportunity_cost_calculator_init",
            min_confidence=min_signal_confidence,
        )

    def calculate(
        self,
        symbol: str,
        entry_time: datetime,
        exit_time: datetime,
        actual_pnl_pct: float,
        missed_signals: Optional[List["TradeSignal"]] = None,
    ) -> OpportunityCostMetrics:
        """
        Calculate opportunity cost metrics for a trade.

        Args:
            symbol: Traded symbol
            entry_time: Trade entry time
            exit_time: Trade exit time
            actual_pnl_pct: Actual trade P&L percentage
            missed_signals: Optional list of missed signals during hold

        Returns:
            OpportunityCostMetrics
        """
        hold_time = (exit_time - entry_time).total_seconds() / 3600  # hours

        metrics = OpportunityCostMetrics(
            symbol=symbol,
            hold_time_hours=hold_time,
            actual_pnl_pct=actual_pnl_pct,
        )

        # Calculate hourly return
        if hold_time > 0:
            metrics.hourly_return = actual_pnl_pct / hold_time

        # Process missed signals
        if missed_signals:
            missed = self._process_missed_signals(missed_signals, entry_time, exit_time)
            metrics.missed_signals = missed
            metrics.missed_signals_count = len(missed)

            if missed:
                pnls = [s.potential_pnl_pct for s in missed]
                metrics.missed_signals_avg_pnl = np.mean(pnls)
                metrics.missed_signals_total_pnl = sum(pnls)
                metrics.best_missed_pnl = max(pnls)

                # Calculate opportunity cost
                # Simple: average of missed signals
                metrics.opportunity_cost_pct = metrics.missed_signals_avg_pnl

                # Adjusted P&L
                metrics.adjusted_pnl_pct = actual_pnl_pct - metrics.opportunity_cost_pct

                # Missed hourly return
                avg_hold = 12  # Assume 12 hour average hold for missed
                metrics.missed_hourly_return = metrics.missed_signals_avg_pnl / avg_hold

        else:
            # Estimate opportunity cost based on historical average
            estimated_signals = self._estimate_missed_signals(hold_time)
            metrics.missed_signals_count = estimated_signals

            if estimated_signals > 0:
                metrics.missed_signals_avg_pnl = self._avg_signal_pnl
                metrics.opportunity_cost_pct = self._avg_signal_pnl * estimated_signals * 0.5
                metrics.adjusted_pnl_pct = actual_pnl_pct - metrics.opportunity_cost_pct

        # Calculate efficiency score
        potential = actual_pnl_pct + metrics.opportunity_cost_pct
        if potential > 0:
            metrics.efficiency_score = max(0, actual_pnl_pct / potential)

        logger.debug(
            "opportunity_cost_calculated",
            symbol=symbol,
            actual_pnl=actual_pnl_pct,
            missed_count=metrics.missed_signals_count,
            adjusted_pnl=metrics.adjusted_pnl_pct,
        )

        return metrics

    def _process_missed_signals(
        self,
        signals: List["TradeSignal"],
        entry_time: datetime,
        exit_time: datetime,
    ) -> List[MissedSignal]:
        """Process and filter missed signals."""
        missed = []

        for signal in signals:
            # Skip if signal was before entry or after exit
            if signal.timestamp < entry_time or signal.timestamp > exit_time:
                continue

            # Skip low confidence signals
            if signal.probability / 100 < self._min_confidence:
                continue

            # Estimate potential P&L (simplified)
            # In reality, would need to simulate the trade
            potential_pnl = self._estimate_signal_pnl(signal)

            missed.append(
                MissedSignal(
                    symbol=signal.symbol,
                    direction=signal.direction.value,
                    timestamp=signal.timestamp,
                    potential_pnl_pct=potential_pnl,
                    confidence=signal.probability / 100,
                )
            )

        return missed

    def _estimate_signal_pnl(self, signal: "TradeSignal") -> float:
        """
        Estimate potential P&L for a signal.

        Simplified estimation based on confidence and R:R.
        """
        confidence = signal.probability / 100
        rr = signal.risk_reward_ratio if signal.risk_reward_ratio > 0 else 2.0

        # Expected value = P(win) * win_amount - P(lose) * lose_amount
        win_pct = confidence
        lose_pct = 1 - confidence

        # Assume SL hit = -SL%, TP hit = SL% * R:R
        sl_pct = signal.stop_loss_pct
        tp_pct = sl_pct * rr

        expected_pnl = win_pct * tp_pct - lose_pct * sl_pct

        return expected_pnl

    def _estimate_missed_signals(self, hold_time_hours: float) -> int:
        """
        Estimate number of missed signals based on hold time.

        Based on historical signal frequency.
        """
        # Assume average of 1 signal per 4 hours on monitored symbols
        signals_per_hour = 0.25

        return int(hold_time_hours * signals_per_hour)

    def record_signal_result(
        self,
        symbol: str,
        pnl_pct: float,
        hold_hours: float,
    ) -> None:
        """
        Record a signal result for future estimation.

        Args:
            symbol: Signal symbol
            pnl_pct: Actual P&L
            hold_hours: Hold time in hours
        """
        self._signal_history.append({
            "symbol": symbol,
            "pnl_pct": pnl_pct,
            "hold_hours": hold_hours,
            "timestamp": datetime.now(timezone.utc),
        })

        # Keep last 1000 signals
        if len(self._signal_history) > 1000:
            self._signal_history = self._signal_history[-1000:]

        # Update average
        if self._signal_history:
            self._avg_signal_pnl = np.mean([s["pnl_pct"] for s in self._signal_history])

    def get_portfolio_opportunity_cost(
        self,
        open_positions: List[Dict],
        available_signals: List["TradeSignal"],
    ) -> float:
        """
        Calculate total opportunity cost of current portfolio.

        Args:
            open_positions: List of open position dicts with entry_time
            available_signals: Signals that could be taken

        Returns:
            Total opportunity cost percentage
        """
        total_cost = 0.0
        now = datetime.now(timezone.utc)

        for pos in open_positions:
            entry_time = pos.get("entry_time", now)
            hold_hours = (now - entry_time).total_seconds() / 3600

            # Simple estimation
            estimated_missed = self._estimate_missed_signals(hold_hours)
            cost = estimated_missed * self._avg_signal_pnl * 0.5  # 50% capture rate
            total_cost += cost

        return total_cost

    def should_hold_or_exit(
        self,
        current_pnl_pct: float,
        hold_hours: float,
        remaining_target_pct: float,
        estimated_time_to_target_hours: float,
    ) -> str:
        """
        Advise whether to hold or exit based on opportunity cost.

        Args:
            current_pnl_pct: Current unrealized P&L
            hold_hours: How long already held
            remaining_target_pct: Remaining distance to TP
            estimated_time_to_target_hours: Estimated time to reach TP

        Returns:
            "HOLD" or "EXIT" with reason
        """
        # Calculate hourly returns
        current_hourly = current_pnl_pct / hold_hours if hold_hours > 0 else 0

        # Estimated future hourly return if we wait
        if estimated_time_to_target_hours > 0:
            future_hourly = remaining_target_pct / estimated_time_to_target_hours
        else:
            future_hourly = 0

        # Average signal hourly return
        avg_signal_hours = 12
        avg_hourly = self._avg_signal_pnl / avg_signal_hours

        # Decision logic
        if future_hourly > avg_hourly * 1.5:
            return "HOLD - expected return exceeds alternatives"
        elif current_pnl_pct > self._avg_signal_pnl and current_hourly < avg_hourly * 0.5:
            return "EXIT - already profitable, slow progress"
        elif remaining_target_pct < self._avg_signal_pnl * 0.5:
            return "HOLD - close to target"
        else:
            return "HOLD - default"
