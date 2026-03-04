# -*- coding: utf-8 -*-
"""
Monitoring Utilities for ML System.

Production monitoring and tail risk management:
- TailRiskManager: Black swan protection
- ModelMonitor: Drift detection
- DrawdownMetrics: Comprehensive drawdown analysis

Usage:
    tail_risk = TailRiskManager()
    if tail_risk.check_anomalies(symbol, data):
        # Safe to trade

    monitor = ModelMonitor()
    monitor.log_prediction(prediction, actual)
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set

import numpy as np
import structlog

from config.settings import settings


logger = structlog.get_logger(__name__)


@dataclass
class DrawdownMetrics:
    """Comprehensive drawdown metrics."""

    max_drawdown_pct: float
    max_drawdown_duration_days: int
    avg_recovery_time_days: float
    longest_drawdown_days: int
    underwater_pct_time: float  # % of time in drawdown
    current_drawdown_pct: float
    current_drawdown_days: int
    drawdown_periods: List[Dict]  # List of drawdown events


class TailRiskManager:
    """
    Manages tail risk and black swan protection.

    Prevents trading in dangerous conditions:
    - Extreme price movements
    - Liquidity crises
    - Delisting risk
    - Extreme funding
    """

    def __init__(self):
        """Initialize tail risk manager."""
        # Hard limits
        self._max_loss_per_position_pct = 10.0  # Even if SL is further

        # Toxic symbols (don't trade)
        self._toxic_symbols: Set[str] = {
            "LUNAUSDT", "USTUSDT", "FTTUSDT",  # Known failures
        }

        # Thresholds for anomalies
        self._max_24h_drop_pct = 50.0
        self._max_funding_rate = 0.01  # 1%
        self._min_volume_drop_pct = 80.0

        logger.info("tail_risk_manager_init")

    def check_anomalies(
        self,
        symbol: str,
        price_24h_change_pct: float,
        volume_24h_change_pct: float,
        funding_rate: float,
        is_delisting: bool = False,
    ) -> tuple[bool, Optional[str]]:
        """
        Check for trading anomalies.

        Args:
            symbol: Trading pair
            price_24h_change_pct: 24h price change %
            volume_24h_change_pct: 24h volume change %
            funding_rate: Current funding rate
            is_delisting: Whether delisting announced

        Returns:
            Tuple of (is_safe, risk_reason)
        """
        # Toxic symbol
        if symbol in self._toxic_symbols:
            return False, f"Toxic symbol: {symbol}"

        # Delisting
        if is_delisting:
            return False, "Delisting announced"

        # Extreme price drop
        if price_24h_change_pct < -self._max_24h_drop_pct:
            return False, f"Extreme price drop: {price_24h_change_pct:.1f}%"

        # Volume collapse
        if volume_24h_change_pct < -self._min_volume_drop_pct:
            return False, f"Volume collapse: {volume_24h_change_pct:.1f}%"

        # Extreme funding
        if abs(funding_rate) > self._max_funding_rate:
            return False, f"Extreme funding: {funding_rate:.4f}"

        return True, None

    def get_hard_stop(
        self,
        entry_price: float,
        direction: int,
    ) -> float:
        """
        Get hard stop price regardless of configured SL.

        Args:
            entry_price: Entry price
            direction: 1 for long, -1 for short

        Returns:
            Hard stop price
        """
        if direction == 1:  # Long
            return entry_price * (1 - self._max_loss_per_position_pct / 100)
        else:  # Short
            return entry_price * (1 + self._max_loss_per_position_pct / 100)

    def add_toxic_symbol(self, symbol: str) -> None:
        """Add symbol to toxic list."""
        self._toxic_symbols.add(symbol)
        logger.warning("symbol_marked_toxic", symbol=symbol)

    def remove_toxic_symbol(self, symbol: str) -> None:
        """Remove symbol from toxic list."""
        self._toxic_symbols.discard(symbol)
        logger.info("symbol_unmarked_toxic", symbol=symbol)

    def get_toxic_symbols(self) -> Set[str]:
        """Get set of toxic symbols."""
        return self._toxic_symbols.copy()


class ModelMonitor:
    """
    Monitors model performance in production.

    Detects:
    - Performance degradation
    - Distribution drift
    - Calibration drift
    """

    def __init__(
        self,
        window_size: int = 100,
        degradation_threshold: float = 0.05,
    ):
        """
        Initialize model monitor.

        Args:
            window_size: Rolling window for metrics
            degradation_threshold: Accuracy drop to trigger alert
        """
        self._window_size = window_size
        self._degradation_threshold = degradation_threshold

        # Rolling windows
        self._predictions = deque(maxlen=window_size)
        self._actuals = deque(maxlen=window_size)
        self._confidences = deque(maxlen=window_size)
        self._pnls = deque(maxlen=window_size)

        # Baseline (set during validation)
        self._baseline_accuracy: Optional[float] = None
        self._baseline_sharpe: Optional[float] = None

        # Alerts
        self._alerts: List[Dict] = []
        self._is_healthy = True

        logger.info(
            "model_monitor_init",
            window_size=window_size,
        )

    def set_baseline(
        self,
        accuracy: float,
        sharpe: float,
    ) -> None:
        """
        Set baseline metrics from validation.

        Args:
            accuracy: Baseline accuracy
            sharpe: Baseline Sharpe ratio
        """
        self._baseline_accuracy = accuracy
        self._baseline_sharpe = sharpe

        logger.info(
            "baseline_set",
            accuracy=accuracy,
            sharpe=sharpe,
        )

    def log_prediction(
        self,
        prediction: int,
        actual: int,
        confidence: float,
        pnl: Optional[float] = None,
    ) -> None:
        """
        Log a prediction and its outcome.

        Args:
            prediction: Predicted class
            actual: Actual class
            confidence: Prediction confidence
            pnl: Optional P&L if trade was taken
        """
        self._predictions.append(prediction)
        self._actuals.append(actual)
        self._confidences.append(confidence)

        if pnl is not None:
            self._pnls.append(pnl)

        # Check drift periodically
        if len(self._predictions) >= self._window_size:
            if len(self._predictions) % 10 == 0:  # Every 10 predictions
                self._check_drift()

    def _check_drift(self) -> None:
        """Check for performance drift."""
        if self._baseline_accuracy is None:
            return

        predictions = np.array(self._predictions)
        actuals = np.array(self._actuals)

        # Current accuracy
        current_accuracy = (predictions == actuals).mean()

        # Check degradation
        if current_accuracy < self._baseline_accuracy - self._degradation_threshold:
            self._is_healthy = False
            alert = {
                "type": "ACCURACY_DRIFT",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "current": current_accuracy,
                "baseline": self._baseline_accuracy,
                "degradation": self._baseline_accuracy - current_accuracy,
            }
            self._alerts.append(alert)

            logger.error(
                "model_drift_detected",
                current_accuracy=current_accuracy,
                baseline=self._baseline_accuracy,
            )
        else:
            self._is_healthy = True

        # Check calibration
        self._check_calibration()

    def _check_calibration(self) -> None:
        """Check confidence calibration."""
        predictions = np.array(self._predictions)
        actuals = np.array(self._actuals)
        confidences = np.array(self._confidences)

        correct = predictions == actuals

        # High confidence should be more accurate
        high_conf_mask = confidences > 0.7
        if high_conf_mask.sum() > 10:
            high_conf_accuracy = correct[high_conf_mask].mean()

            # If high confidence predictions aren't better, calibration is off
            overall_accuracy = correct.mean()
            if high_conf_accuracy < overall_accuracy:
                alert = {
                    "type": "CALIBRATION_DRIFT",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "high_conf_accuracy": float(high_conf_accuracy),
                    "overall_accuracy": float(overall_accuracy),
                }
                self._alerts.append(alert)

                logger.warning(
                    "calibration_drift",
                    high_conf_accuracy=high_conf_accuracy,
                    overall_accuracy=overall_accuracy,
                )

    @property
    def is_healthy(self) -> bool:
        """Whether model is healthy."""
        return self._is_healthy

    def get_current_metrics(self) -> Dict:
        """Get current rolling metrics."""
        if len(self._predictions) < 10:
            return {"status": "insufficient_data"}

        predictions = np.array(self._predictions)
        actuals = np.array(self._actuals)

        accuracy = (predictions == actuals).mean()

        pnl_metrics = {}
        if len(self._pnls) > 0:
            pnls = np.array(self._pnls)
            pnl_metrics = {
                "mean_pnl": float(pnls.mean()),
                "win_rate": float((pnls > 0).mean()),
                "sharpe": float(
                    pnls.mean() / pnls.std() * np.sqrt(252)
                    if pnls.std() > 0 else 0
                ),
            }

        return {
            "status": "healthy" if self._is_healthy else "degraded",
            "sample_count": len(self._predictions),
            "accuracy": float(accuracy),
            "baseline_accuracy": self._baseline_accuracy,
            **pnl_metrics,
        }

    def get_alerts(self, since: Optional[datetime] = None) -> List[Dict]:
        """Get alerts, optionally filtered by time."""
        if since is None:
            return self._alerts.copy()

        return [
            a for a in self._alerts
            if datetime.fromisoformat(a["timestamp"]) > since
        ]

    def should_use_ml(self) -> bool:
        """
        Determine if ML should be used.

        Returns False if model is degraded, suggesting
        fallback to original signals.
        """
        return self._is_healthy

    def reset(self) -> None:
        """Reset monitor state."""
        self._predictions.clear()
        self._actuals.clear()
        self._confidences.clear()
        self._pnls.clear()
        self._alerts.clear()
        self._is_healthy = True

        logger.info("model_monitor_reset")


class DrawdownAnalyzer:
    """
    Analyzes drawdown patterns.

    Provides detailed drawdown metrics beyond simple max drawdown.
    """

    def analyze(self, equity_curve: np.ndarray) -> DrawdownMetrics:
        """
        Analyze drawdown from equity curve.

        Args:
            equity_curve: Array of equity values

        Returns:
            DrawdownMetrics
        """
        if len(equity_curve) < 2:
            return self._empty_metrics()

        # Calculate drawdown series
        running_max = np.maximum.accumulate(equity_curve)
        drawdown = (running_max - equity_curve) / running_max * 100

        # Max drawdown
        max_dd = drawdown.max()

        # Drawdown periods
        in_drawdown = drawdown > 0
        periods = []
        current_period = None

        for i, dd in enumerate(in_drawdown):
            if dd and current_period is None:
                current_period = {"start": i, "max_dd": drawdown[i]}
            elif dd and current_period is not None:
                current_period["max_dd"] = max(current_period["max_dd"], drawdown[i])
            elif not dd and current_period is not None:
                current_period["end"] = i - 1
                current_period["duration"] = current_period["end"] - current_period["start"]
                periods.append(current_period)
                current_period = None

        # Handle ongoing drawdown
        if current_period is not None:
            current_period["end"] = len(drawdown) - 1
            current_period["duration"] = current_period["end"] - current_period["start"]
            periods.append(current_period)

        # Calculate metrics
        durations = [p["duration"] for p in periods]
        max_duration = max(durations) if durations else 0
        avg_recovery = np.mean(durations) if durations else 0
        longest_dd = max_duration

        # Underwater time
        underwater_pct = in_drawdown.mean() * 100

        # Current state
        current_dd = drawdown[-1]
        current_dd_days = 0
        if current_dd > 0:
            # Count days in current drawdown
            for i in range(len(drawdown) - 1, -1, -1):
                if drawdown[i] > 0:
                    current_dd_days += 1
                else:
                    break

        return DrawdownMetrics(
            max_drawdown_pct=float(max_dd),
            max_drawdown_duration_days=int(max_duration),
            avg_recovery_time_days=float(avg_recovery),
            longest_drawdown_days=int(longest_dd),
            underwater_pct_time=float(underwater_pct),
            current_drawdown_pct=float(current_dd),
            current_drawdown_days=int(current_dd_days),
            drawdown_periods=periods,
        )

    def _empty_metrics(self) -> DrawdownMetrics:
        """Return empty metrics."""
        return DrawdownMetrics(
            max_drawdown_pct=0,
            max_drawdown_duration_days=0,
            avg_recovery_time_days=0,
            longest_drawdown_days=0,
            underwater_pct_time=0,
            current_drawdown_pct=0,
            current_drawdown_days=0,
            drawdown_periods=[],
        )
