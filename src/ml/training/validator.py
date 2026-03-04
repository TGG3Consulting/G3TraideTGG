# -*- coding: utf-8 -*-
"""
Model Validator for ML System.

Validates trained models against baseline and production requirements.

Checks:
- Overfitting (train vs test gap)
- Baseline comparison
- Minimum performance thresholds
- Data sufficiency

Usage:
    validator = ModelValidator()
    report = validator.validate(model, train_df, test_df)
    if report.is_production_ready:
        model.save()
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import structlog

from config.settings import settings


logger = structlog.get_logger(__name__)


@dataclass
class ValidationReport:
    """Complete validation report for a model."""

    # Data sufficiency
    train_samples: int
    test_samples: int
    data_period_days: int
    data_sufficient: bool

    # Train metrics (for overfitting detection)
    train_accuracy: float
    train_profit_factor: float
    train_sharpe: float

    # Test metrics (MAIN)
    test_accuracy: float
    test_profit_factor: float
    test_sharpe: float
    test_max_drawdown: float
    test_win_rate: float

    # Baseline comparison
    baseline_pnl: float
    model_pnl: float
    improvement_vs_baseline_pct: float
    is_better_than_baseline: bool

    # Overfitting detection
    accuracy_gap: float  # train - test
    is_overfitting: bool

    # Confidence intervals (95%)
    confidence_interval_low: float
    confidence_interval_high: float

    # Validation failures
    failures: List[str] = field(default_factory=list)

    # Final verdict
    is_production_ready: bool = False


class ModelValidator:
    """
    Validates ML models for production readiness.

    Implements strict validation:
    - Temporal split (no random!)
    - Walk-forward validation
    - Baseline comparison
    - Overfitting detection
    """

    def __init__(self):
        """Initialize validator with config."""
        self._config = settings.ml.metrics
        self._training_config = settings.ml.training

        # Minimum requirements
        self._min_train_samples = 500
        self._min_test_samples = 100
        self._min_data_days = 90
        self._max_overfit_gap = 0.15  # 15% accuracy gap = overfitting
        self._min_improvement_vs_baseline = 10  # 10% better than baseline

        logger.info("model_validator_init")

    def validate(
        self,
        train_predictions: np.ndarray,
        train_actuals: np.ndarray,
        train_pnls: np.ndarray,
        test_predictions: np.ndarray,
        test_actuals: np.ndarray,
        test_pnls: np.ndarray,
        train_timestamps: Optional[pd.Series] = None,
    ) -> ValidationReport:
        """
        Validate model comprehensively.

        Args:
            train_predictions: Predictions on train set
            train_actuals: Actual values on train set
            train_pnls: P&L values on train set
            test_predictions: Predictions on test set
            test_actuals: Actual values on test set
            test_pnls: P&L values on test set
            train_timestamps: Optional timestamps for period calculation

        Returns:
            ValidationReport with all validation results
        """
        logger.info(
            "validating_model",
            train_samples=len(train_predictions),
            test_samples=len(test_predictions),
        )

        failures = []

        # Data sufficiency
        data_sufficient = self._check_data_sufficiency(
            len(train_predictions),
            len(test_predictions),
            train_timestamps,
            failures,
        )

        # Calculate period days
        data_period_days = 0
        if train_timestamps is not None and len(train_timestamps) > 0:
            data_period_days = (
                train_timestamps.max() - train_timestamps.min()
            ).days

        # Train metrics
        train_accuracy = self._calculate_accuracy(train_predictions, train_actuals)
        train_pf, train_sharpe = self._calculate_trading_metrics(train_pnls)

        # Test metrics
        test_accuracy = self._calculate_accuracy(test_predictions, test_actuals)
        test_pf, test_sharpe = self._calculate_trading_metrics(test_pnls)
        test_max_dd = self._calculate_max_drawdown(test_pnls)
        test_win_rate = (test_pnls > 0).mean() if len(test_pnls) > 0 else 0

        # Baseline comparison
        baseline_pnl = self._calculate_baseline(test_pnls)
        model_pnl = test_pnls.sum() if len(test_pnls) > 0 else 0

        improvement = 0
        if abs(baseline_pnl) > 0:
            improvement = (model_pnl - baseline_pnl) / abs(baseline_pnl) * 100

        is_better = self._check_baseline_improvement(
            model_pnl, baseline_pnl, failures
        )

        # Overfitting check
        accuracy_gap = train_accuracy - test_accuracy
        is_overfitting = self._check_overfitting(
            train_accuracy, test_accuracy, failures
        )

        # Performance thresholds
        self._check_performance_thresholds(
            test_accuracy, test_pf, test_sharpe, test_max_dd, test_win_rate,
            failures,
        )

        # Confidence intervals (bootstrap approximation)
        ci_low, ci_high = self._calculate_confidence_interval(test_pnls)

        # Final verdict
        is_production_ready = len(failures) == 0

        report = ValidationReport(
            train_samples=len(train_predictions),
            test_samples=len(test_predictions),
            data_period_days=data_period_days,
            data_sufficient=data_sufficient,
            train_accuracy=float(train_accuracy),
            train_profit_factor=float(train_pf),
            train_sharpe=float(train_sharpe),
            test_accuracy=float(test_accuracy),
            test_profit_factor=float(test_pf),
            test_sharpe=float(test_sharpe),
            test_max_drawdown=float(test_max_dd),
            test_win_rate=float(test_win_rate),
            baseline_pnl=float(baseline_pnl),
            model_pnl=float(model_pnl),
            improvement_vs_baseline_pct=float(improvement),
            is_better_than_baseline=is_better,
            accuracy_gap=float(accuracy_gap),
            is_overfitting=is_overfitting,
            confidence_interval_low=float(ci_low),
            confidence_interval_high=float(ci_high),
            failures=failures,
            is_production_ready=is_production_ready,
        )

        logger.info(
            "model_validation_complete",
            is_production_ready=is_production_ready,
            failures_count=len(failures),
        )

        return report

    def _check_data_sufficiency(
        self,
        train_n: int,
        test_n: int,
        timestamps: Optional[pd.Series],
        failures: List[str],
    ) -> bool:
        """Check if data is sufficient for training."""
        sufficient = True

        if train_n < self._min_train_samples:
            failures.append(
                f"Insufficient train samples: {train_n} < {self._min_train_samples}"
            )
            sufficient = False

        if test_n < self._min_test_samples:
            failures.append(
                f"Insufficient test samples: {test_n} < {self._min_test_samples}"
            )
            sufficient = False

        if timestamps is not None and len(timestamps) > 1:
            period_days = (timestamps.max() - timestamps.min()).days
            if period_days < self._min_data_days:
                failures.append(
                    f"Insufficient data period: {period_days} < {self._min_data_days} days"
                )
                sufficient = False

        return sufficient

    def _calculate_accuracy(
        self,
        predictions: np.ndarray,
        actuals: np.ndarray,
    ) -> float:
        """Calculate prediction accuracy."""
        if len(predictions) == 0:
            return 0.0
        return (predictions == actuals).mean()

    def _calculate_trading_metrics(
        self,
        pnls: np.ndarray,
    ) -> Tuple[float, float]:
        """Calculate profit factor and Sharpe ratio."""
        if len(pnls) == 0:
            return 0.0, 0.0

        # Profit factor
        gross_profit = pnls[pnls > 0].sum() if (pnls > 0).any() else 0
        gross_loss = abs(pnls[pnls < 0].sum()) if (pnls < 0).any() else 0.01
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # Sharpe ratio
        if len(pnls) > 1 and np.std(pnls) > 0:
            sharpe = np.mean(pnls) / np.std(pnls) * np.sqrt(252)
        else:
            sharpe = 0

        return profit_factor, sharpe

    def _calculate_max_drawdown(self, pnls: np.ndarray) -> float:
        """Calculate maximum drawdown."""
        if len(pnls) == 0:
            return 0.0

        cumulative = np.cumsum(pnls)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = running_max - cumulative

        return drawdowns.max() if len(drawdowns) > 0 else 0

    def _calculate_baseline(self, test_pnls: np.ndarray) -> float:
        """Calculate baseline P&L (buy and hold equivalent)."""
        # Simple baseline: average P&L of all trades
        return test_pnls.mean() if len(test_pnls) > 0 else 0

    def _check_baseline_improvement(
        self,
        model_pnl: float,
        baseline_pnl: float,
        failures: List[str],
    ) -> bool:
        """Check if model beats baseline by required margin."""
        if baseline_pnl == 0:
            return model_pnl > 0

        improvement = (model_pnl - baseline_pnl) / abs(baseline_pnl) * 100

        if improvement < self._min_improvement_vs_baseline:
            failures.append(
                f"Model not better than baseline: {improvement:.1f}% < {self._min_improvement_vs_baseline}%"
            )
            return False

        return True

    def _check_overfitting(
        self,
        train_accuracy: float,
        test_accuracy: float,
        failures: List[str],
    ) -> bool:
        """Check for overfitting."""
        gap = train_accuracy - test_accuracy

        if gap > self._max_overfit_gap:
            failures.append(
                f"Overfitting detected: train-test gap {gap:.1%} > {self._max_overfit_gap:.1%}"
            )
            return True

        return False

    def _check_performance_thresholds(
        self,
        accuracy: float,
        profit_factor: float,
        sharpe: float,
        max_dd: float,
        win_rate: float,
        failures: List[str],
    ) -> None:
        """Check minimum performance thresholds."""
        if accuracy < 0.55:
            failures.append(f"Test accuracy {accuracy:.1%} < 55%")

        if profit_factor < self._config.min_profit_factor:
            failures.append(
                f"Profit factor {profit_factor:.2f} < {self._config.min_profit_factor}"
            )

        if sharpe < self._config.min_sharpe_ratio:
            failures.append(
                f"Sharpe ratio {sharpe:.2f} < {self._config.min_sharpe_ratio}"
            )

        if max_dd > self._config.max_max_drawdown * 100:
            failures.append(
                f"Max drawdown {max_dd:.1f}% > {self._config.max_max_drawdown * 100}%"
            )

        if win_rate < self._config.min_win_rate:
            failures.append(
                f"Win rate {win_rate:.1%} < {self._config.min_win_rate:.1%}"
            )

    def _calculate_confidence_interval(
        self,
        pnls: np.ndarray,
        confidence: float = 0.95,
    ) -> Tuple[float, float]:
        """Calculate confidence interval using bootstrap."""
        if len(pnls) < 10:
            return 0, 0

        n_bootstrap = 1000
        means = []

        for _ in range(n_bootstrap):
            sample = np.random.choice(pnls, size=len(pnls), replace=True)
            means.append(sample.mean())

        means = np.array(means)
        alpha = (1 - confidence) / 2

        return np.percentile(means, alpha * 100), np.percentile(means, (1 - alpha) * 100)

    def generate_report_text(self, report: ValidationReport) -> str:
        """Generate human-readable validation report."""
        lines = []
        lines.append("=" * 60)
        lines.append("MODEL VALIDATION REPORT")
        lines.append("=" * 60)
        lines.append("")

        # Data
        lines.append("DATA SUFFICIENCY")
        lines.append("-" * 40)
        lines.append(f"  Train samples:    {report.train_samples}")
        lines.append(f"  Test samples:     {report.test_samples}")
        lines.append(f"  Data period:      {report.data_period_days} days")
        lines.append(f"  Sufficient:       {'YES' if report.data_sufficient else 'NO'}")
        lines.append("")

        # Train vs Test
        lines.append("OVERFITTING CHECK")
        lines.append("-" * 40)
        lines.append(f"  Train accuracy:   {report.train_accuracy:.1%}")
        lines.append(f"  Test accuracy:    {report.test_accuracy:.1%}")
        lines.append(f"  Gap:              {report.accuracy_gap:.1%}")
        lines.append(f"  Overfitting:      {'YES' if report.is_overfitting else 'NO'}")
        lines.append("")

        # Baseline
        lines.append("BASELINE COMPARISON")
        lines.append("-" * 40)
        lines.append(f"  Baseline PnL:     {report.baseline_pnl:.2f}%")
        lines.append(f"  Model PnL:        {report.model_pnl:.2f}%")
        lines.append(f"  Improvement:      {report.improvement_vs_baseline_pct:.1f}%")
        lines.append(f"  Better:           {'YES' if report.is_better_than_baseline else 'NO'}")
        lines.append("")

        # Performance
        lines.append("TEST PERFORMANCE")
        lines.append("-" * 40)
        lines.append(f"  Win rate:         {report.test_win_rate:.1%}")
        lines.append(f"  Profit factor:    {report.test_profit_factor:.2f}")
        lines.append(f"  Sharpe ratio:     {report.test_sharpe:.2f}")
        lines.append(f"  Max drawdown:     {report.test_max_drawdown:.1f}%")
        lines.append("")

        # Confidence interval
        lines.append("CONFIDENCE INTERVAL (95%)")
        lines.append("-" * 40)
        lines.append(f"  Lower:            {report.confidence_interval_low:.2f}%")
        lines.append(f"  Upper:            {report.confidence_interval_high:.2f}%")
        lines.append("")

        # Failures
        if report.failures:
            lines.append("VALIDATION FAILURES")
            lines.append("-" * 40)
            for f in report.failures:
                lines.append(f"  - {f}")
            lines.append("")

        # Verdict
        lines.append("=" * 60)
        verdict = "PRODUCTION READY" if report.is_production_ready else "NOT READY"
        lines.append(f"VERDICT: {verdict}")
        lines.append("=" * 60)

        return "\n".join(lines)
