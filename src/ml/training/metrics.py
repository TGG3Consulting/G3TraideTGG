# -*- coding: utf-8 -*-
"""
Evaluation Metrics for ML System.

Comprehensive metrics for model evaluation:
- Classification metrics
- Regression metrics
- Trading metrics
- Calibration metrics

Usage:
    metrics = EvaluationMetrics()
    results = metrics.calculate_all(predictions, actuals, pnls)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import structlog


logger = structlog.get_logger(__name__)


@dataclass
class ClassificationMetrics:
    """Classification performance metrics."""

    accuracy: float
    precision: Dict[str, float]
    recall: Dict[str, float]
    f1: Dict[str, float]
    confusion_matrix: List[List[int]]
    balanced_accuracy: float
    cohen_kappa: float


@dataclass
class RegressionMetrics:
    """Regression performance metrics."""

    mae: float  # Mean Absolute Error
    rmse: float  # Root Mean Squared Error
    mape: float  # Mean Absolute Percentage Error
    r2: float  # R-squared
    directional_accuracy: float  # % of correct direction predictions


@dataclass
class TradingMetrics:
    """Trading-specific performance metrics."""

    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float

    gross_profit: float
    gross_loss: float
    net_profit: float
    profit_factor: float

    avg_win: float
    avg_loss: float
    avg_trade: float
    largest_win: float
    largest_loss: float

    max_consecutive_wins: int
    max_consecutive_losses: int

    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float

    max_drawdown_pct: float
    max_drawdown_duration: int
    recovery_factor: float

    expectancy: float
    payoff_ratio: float


@dataclass
class CalibrationMetrics:
    """Probability calibration metrics."""

    expected_calibration_error: float
    maximum_calibration_error: float
    brier_score: float
    log_loss: float
    reliability_data: List[Dict]


class EvaluationMetrics:
    """
    Calculates comprehensive evaluation metrics.

    Provides detailed analysis of model performance
    across multiple dimensions.
    """

    def __init__(self):
        """Initialize metrics calculator."""
        logger.info("evaluation_metrics_init")

    def calculate_classification(
        self,
        predictions: np.ndarray,
        actuals: np.ndarray,
        classes: List[int] = None,
    ) -> ClassificationMetrics:
        """
        Calculate classification metrics.

        Args:
            predictions: Predicted classes
            actuals: Actual classes
            classes: List of class labels (default: unique values)

        Returns:
            ClassificationMetrics
        """
        if classes is None:
            classes = sorted(set(np.concatenate([predictions, actuals])))

        n_classes = len(classes)
        class_to_idx = {c: i for i, c in enumerate(classes)}

        # Confusion matrix
        confusion = np.zeros((n_classes, n_classes), dtype=int)
        for pred, actual in zip(predictions, actuals):
            if pred in class_to_idx and actual in class_to_idx:
                confusion[class_to_idx[actual], class_to_idx[pred]] += 1

        # Per-class metrics
        precision = {}
        recall = {}
        f1 = {}

        for cls in classes:
            idx = class_to_idx[cls]
            tp = confusion[idx, idx]
            fp = confusion[:, idx].sum() - tp
            fn = confusion[idx, :].sum() - tp

            p = tp / (tp + fp) if (tp + fp) > 0 else 0
            r = tp / (tp + fn) if (tp + fn) > 0 else 0
            f = 2 * p * r / (p + r) if (p + r) > 0 else 0

            precision[str(cls)] = p
            recall[str(cls)] = r
            f1[str(cls)] = f

        # Overall metrics
        accuracy = np.trace(confusion) / confusion.sum() if confusion.sum() > 0 else 0

        # Balanced accuracy
        recalls = [recall[str(cls)] for cls in classes]
        balanced_accuracy = np.mean(recalls) if recalls else 0

        # Cohen's Kappa
        total = confusion.sum()
        if total > 0:
            po = np.trace(confusion) / total
            pe = sum(
                (confusion[i, :].sum() / total) * (confusion[:, i].sum() / total)
                for i in range(n_classes)
            )
            cohen_kappa = (po - pe) / (1 - pe) if pe < 1 else 0
        else:
            cohen_kappa = 0

        return ClassificationMetrics(
            accuracy=float(accuracy),
            precision=precision,
            recall=recall,
            f1=f1,
            confusion_matrix=confusion.tolist(),
            balanced_accuracy=float(balanced_accuracy),
            cohen_kappa=float(cohen_kappa),
        )

    def calculate_regression(
        self,
        predictions: np.ndarray,
        actuals: np.ndarray,
    ) -> RegressionMetrics:
        """
        Calculate regression metrics.

        Args:
            predictions: Predicted values
            actuals: Actual values

        Returns:
            RegressionMetrics
        """
        errors = predictions - actuals

        mae = np.abs(errors).mean()
        rmse = np.sqrt((errors ** 2).mean())

        # MAPE (avoid division by zero)
        non_zero = actuals != 0
        mape = (np.abs(errors[non_zero]) / np.abs(actuals[non_zero])).mean() * 100 if non_zero.any() else 0

        # R2
        ss_res = (errors ** 2).sum()
        ss_tot = ((actuals - actuals.mean()) ** 2).sum()
        r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

        # Directional accuracy
        direction_pred = np.sign(predictions)
        direction_actual = np.sign(actuals)
        directional_accuracy = (direction_pred == direction_actual).mean()

        return RegressionMetrics(
            mae=float(mae),
            rmse=float(rmse),
            mape=float(mape),
            r2=float(r2),
            directional_accuracy=float(directional_accuracy),
        )

    def calculate_trading(
        self,
        pnls: np.ndarray,
    ) -> TradingMetrics:
        """
        Calculate trading performance metrics.

        Args:
            pnls: Array of trade P&L values (in %)

        Returns:
            TradingMetrics
        """
        pnls = np.asarray(pnls)

        if len(pnls) == 0:
            return self._empty_trading_metrics()

        # Basic stats
        total_trades = len(pnls)
        winning_trades = (pnls > 0).sum()
        losing_trades = (pnls < 0).sum()
        win_rate = winning_trades / total_trades if total_trades > 0 else 0

        # P&L stats
        gross_profit = pnls[pnls > 0].sum() if (pnls > 0).any() else 0
        gross_loss = abs(pnls[pnls < 0].sum()) if (pnls < 0).any() else 0
        net_profit = pnls.sum()
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf') if gross_profit > 0 else 0

        # Trade stats
        avg_win = pnls[pnls > 0].mean() if (pnls > 0).any() else 0
        avg_loss = pnls[pnls < 0].mean() if (pnls < 0).any() else 0
        avg_trade = pnls.mean()
        largest_win = pnls.max() if len(pnls) > 0 else 0
        largest_loss = pnls.min() if len(pnls) > 0 else 0

        # Consecutive wins/losses
        max_consec_wins, max_consec_losses = self._calculate_consecutive_runs(pnls)

        # Risk-adjusted returns
        sharpe = self._calculate_sharpe(pnls)
        sortino = self._calculate_sortino(pnls)
        calmar, max_dd, max_dd_duration = self._calculate_calmar(pnls)

        # Expectancy
        expectancy = avg_trade
        payoff_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf') if avg_win > 0 else 0

        # Recovery factor
        recovery_factor = net_profit / max_dd if max_dd > 0 else 0

        return TradingMetrics(
            total_trades=int(total_trades),
            winning_trades=int(winning_trades),
            losing_trades=int(losing_trades),
            win_rate=float(win_rate),
            gross_profit=float(gross_profit),
            gross_loss=float(gross_loss),
            net_profit=float(net_profit),
            profit_factor=float(profit_factor),
            avg_win=float(avg_win),
            avg_loss=float(avg_loss),
            avg_trade=float(avg_trade),
            largest_win=float(largest_win),
            largest_loss=float(largest_loss),
            max_consecutive_wins=int(max_consec_wins),
            max_consecutive_losses=int(max_consec_losses),
            sharpe_ratio=float(sharpe),
            sortino_ratio=float(sortino),
            calmar_ratio=float(calmar),
            max_drawdown_pct=float(max_dd),
            max_drawdown_duration=int(max_dd_duration),
            recovery_factor=float(recovery_factor),
            expectancy=float(expectancy),
            payoff_ratio=float(payoff_ratio),
        )

    def calculate_calibration(
        self,
        probabilities: np.ndarray,
        outcomes: np.ndarray,
        n_bins: int = 10,
    ) -> CalibrationMetrics:
        """
        Calculate calibration metrics.

        Args:
            probabilities: Predicted probabilities (0-1)
            outcomes: Binary outcomes (0 or 1)
            n_bins: Number of bins for reliability diagram

        Returns:
            CalibrationMetrics
        """
        probabilities = np.clip(probabilities, 1e-7, 1 - 1e-7)

        # ECE and MCE
        bin_edges = np.linspace(0, 1, n_bins + 1)
        ece = 0.0
        mce = 0.0
        reliability_data = []

        for i in range(n_bins):
            mask = (probabilities >= bin_edges[i]) & (probabilities < bin_edges[i + 1])
            if mask.sum() > 0:
                bin_conf = probabilities[mask].mean()
                bin_acc = outcomes[mask].mean()
                bin_size = mask.sum()

                gap = abs(bin_conf - bin_acc)
                ece += bin_size * gap
                mce = max(mce, gap)

                reliability_data.append({
                    "bin": i,
                    "confidence": float(bin_conf),
                    "accuracy": float(bin_acc),
                    "count": int(bin_size),
                    "gap": float(gap),
                })

        ece /= len(probabilities) if len(probabilities) > 0 else 1

        # Brier score
        brier_score = ((probabilities - outcomes) ** 2).mean()

        # Log loss
        log_loss = -(
            outcomes * np.log(probabilities) +
            (1 - outcomes) * np.log(1 - probabilities)
        ).mean()

        return CalibrationMetrics(
            expected_calibration_error=float(ece),
            maximum_calibration_error=float(mce),
            brier_score=float(brier_score),
            log_loss=float(log_loss),
            reliability_data=reliability_data,
        )

    def _calculate_consecutive_runs(
        self,
        pnls: np.ndarray,
    ) -> Tuple[int, int]:
        """Calculate max consecutive wins and losses."""
        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0

        for pnl in pnls:
            if pnl > 0:
                current_wins += 1
                current_losses = 0
                max_wins = max(max_wins, current_wins)
            elif pnl < 0:
                current_losses += 1
                current_wins = 0
                max_losses = max(max_losses, current_losses)
            else:
                current_wins = 0
                current_losses = 0

        return max_wins, max_losses

    def _calculate_sharpe(self, pnls: np.ndarray) -> float:
        """Calculate Sharpe ratio."""
        if len(pnls) < 2 or np.std(pnls) == 0:
            return 0

        return np.mean(pnls) / np.std(pnls) * np.sqrt(252)

    def _calculate_sortino(self, pnls: np.ndarray) -> float:
        """Calculate Sortino ratio."""
        if len(pnls) < 2:
            return 0

        downside = pnls[pnls < 0]
        if len(downside) == 0 or np.std(downside) == 0:
            return float('inf') if np.mean(pnls) > 0 else 0

        return np.mean(pnls) / np.std(downside) * np.sqrt(252)

    def _calculate_calmar(
        self,
        pnls: np.ndarray,
    ) -> Tuple[float, float, int]:
        """Calculate Calmar ratio, max drawdown, and duration."""
        cumulative = np.cumsum(pnls)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = running_max - cumulative

        max_dd = drawdowns.max() if len(drawdowns) > 0 else 0

        # Duration
        if max_dd > 0:
            in_drawdown = drawdowns > 0
            max_duration = 0
            current_duration = 0
            for dd in in_drawdown:
                if dd:
                    current_duration += 1
                    max_duration = max(max_duration, current_duration)
                else:
                    current_duration = 0
        else:
            max_duration = 0

        # Calmar
        total_return = cumulative[-1] if len(cumulative) > 0 else 0
        calmar = total_return / max_dd if max_dd > 0 else float('inf') if total_return > 0 else 0

        return calmar, max_dd, max_duration

    def _empty_trading_metrics(self) -> TradingMetrics:
        """Return empty trading metrics."""
        return TradingMetrics(
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0,
            gross_profit=0,
            gross_loss=0,
            net_profit=0,
            profit_factor=0,
            avg_win=0,
            avg_loss=0,
            avg_trade=0,
            largest_win=0,
            largest_loss=0,
            max_consecutive_wins=0,
            max_consecutive_losses=0,
            sharpe_ratio=0,
            sortino_ratio=0,
            calmar_ratio=0,
            max_drawdown_pct=0,
            max_drawdown_duration=0,
            recovery_factor=0,
            expectancy=0,
            payoff_ratio=0,
        )
