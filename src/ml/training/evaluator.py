# -*- coding: utf-8 -*-
"""
Model Evaluator for ML System.

Evaluates trained models with various metrics:
- Classification metrics (accuracy, precision, recall, F1)
- Regression metrics (MAE, RMSE, R2)
- Calibration metrics (ECE, reliability)
- Trading metrics (Sharpe, profit factor, win rate)

Usage:
    evaluator = Evaluator()
    metrics = evaluator.evaluate(ensemble, test_df)
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import structlog

from config.settings import settings
from src.ml.models import ModelEnsemble, DirectionClassifier
from src.ml.data.schemas import Direction


logger = structlog.get_logger(__name__)


class Evaluator:
    """
    Evaluates ML model performance.

    Computes comprehensive metrics for:
    - Direction prediction quality
    - SL/TP prediction accuracy
    - Confidence calibration
    - Simulated trading performance
    """

    def __init__(self):
        """Initialize evaluator with config."""
        self._config = settings.ml.metrics

        logger.info("evaluator_init")

    def evaluate(
        self,
        ensemble: ModelEnsemble,
        test_df: pd.DataFrame,
        feature_cols: Optional[List[str]] = None,
    ) -> Dict:
        """
        Comprehensive evaluation of the model ensemble.

        Args:
            ensemble: Trained ModelEnsemble
            test_df: Test data with features and labels
            feature_cols: Optional feature column list

        Returns:
            Dictionary with all metrics
        """
        logger.info("evaluating_model", test_rows=len(test_df))

        # Prepare data
        X, y_dir, y_sl, y_tp = self._prepare_data(test_df, feature_cols)

        # Get predictions
        predictions = ensemble.predict(X)

        pred_directions = np.array([p.direction.value for p in predictions])
        pred_confidences = np.array([p.confidence for p in predictions])
        pred_sl = np.array([p.predicted_sl_pct for p in predictions])
        pred_tp = np.array([p.predicted_tp1_pct for p in predictions])

        # Compute metrics
        metrics = {}

        # Direction metrics
        metrics["direction"] = self._evaluate_direction(
            pred_directions, y_dir, pred_confidences
        )

        # Level metrics
        metrics["sl"] = self._evaluate_regression(pred_sl, y_sl, "sl")
        metrics["tp"] = self._evaluate_regression(pred_tp, y_tp, "tp")

        # Calibration metrics
        if "label_outcome" in test_df.columns:
            outcomes = test_df["label_outcome"].values
            metrics["calibration"] = self._evaluate_calibration(
                pred_confidences, pred_directions, outcomes
            )

        # Trading metrics
        if all(c in test_df.columns for c in ["label_outcome", "label_pnl_pct"]):
            metrics["trading"] = self._evaluate_trading(
                predictions, test_df, pred_directions
            )

        # Validation against thresholds
        metrics["validation"] = self._validate_metrics(metrics)

        logger.info("evaluation_complete", metrics_summary={
            "direction_accuracy": metrics["direction"]["accuracy"],
            "validation_passed": metrics["validation"]["passed"],
        })

        return metrics

    def _prepare_data(
        self,
        df: pd.DataFrame,
        feature_cols: Optional[List[str]] = None,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Prepare features and targets."""
        if feature_cols is None:
            exclude_cols = ["symbol", "timestamp", "price", "open_interest"]
            exclude_cols += [c for c in df.columns if c.startswith("label_")]
            feature_cols = [c for c in df.columns if c not in exclude_cols]

        X = df[feature_cols].values.astype(np.float32)

        y_dir = df["label_direction"].values if "label_direction" in df.columns else np.zeros(len(df))
        y_sl = df["label_sl_pct"].values if "label_sl_pct" in df.columns else np.ones(len(df))
        y_tp = df["label_tp_pct"].values if "label_tp_pct" in df.columns else np.ones(len(df)) * 2

        return X, y_dir, y_sl, y_tp

    def _evaluate_direction(
        self,
        predictions: np.ndarray,
        actuals: np.ndarray,
        confidences: np.ndarray,
    ) -> Dict:
        """Evaluate direction prediction quality."""
        # Map Direction enum values to ints if needed
        if hasattr(predictions[0], 'value'):
            predictions = np.array([p.value for p in predictions])

        # Overall accuracy
        accuracy = (predictions == actuals).mean()

        # Per-class metrics
        classes = [-1, 0, 1]
        class_names = ["short", "neutral", "long"]

        precision = {}
        recall = {}
        f1 = {}

        for cls, name in zip(classes, class_names):
            pred_cls = predictions == cls
            actual_cls = actuals == cls

            tp = (pred_cls & actual_cls).sum()
            fp = (pred_cls & ~actual_cls).sum()
            fn = (~pred_cls & actual_cls).sum()

            precision[name] = tp / (tp + fp) if (tp + fp) > 0 else 0
            recall[name] = tp / (tp + fn) if (tp + fn) > 0 else 0
            f1[name] = 2 * precision[name] * recall[name] / (precision[name] + recall[name]) if (precision[name] + recall[name]) > 0 else 0

        # Confusion matrix
        confusion = np.zeros((3, 3), dtype=int)
        for i, cls_actual in enumerate(classes):
            for j, cls_pred in enumerate(classes):
                confusion[i, j] = ((actuals == cls_actual) & (predictions == cls_pred)).sum()

        # Confidence analysis
        high_conf_mask = confidences > 0.7
        high_conf_accuracy = (predictions[high_conf_mask] == actuals[high_conf_mask]).mean() if high_conf_mask.any() else 0

        return {
            "accuracy": float(accuracy),
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "confusion_matrix": confusion.tolist(),
            "high_confidence_accuracy": float(high_conf_accuracy),
            "mean_confidence": float(confidences.mean()),
        }

    def _evaluate_regression(
        self,
        predictions: np.ndarray,
        actuals: np.ndarray,
        name: str,
    ) -> Dict:
        """Evaluate regression quality for SL/TP."""
        errors = predictions - actuals

        mae = np.abs(errors).mean()
        rmse = np.sqrt((errors ** 2).mean())
        mape = (np.abs(errors) / np.maximum(actuals, 0.1)).mean() * 100

        # R2 score
        ss_res = (errors ** 2).sum()
        ss_tot = ((actuals - actuals.mean()) ** 2).sum()
        r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

        # Directional accuracy (is prediction in same direction relative to some baseline)
        baseline = 1.0 if name == "sl" else 2.0
        directional_acc = (
            (predictions > baseline) == (actuals > baseline)
        ).mean()

        return {
            "mae": float(mae),
            "rmse": float(rmse),
            "mape": float(mape),
            "r2": float(r2),
            "directional_accuracy": float(directional_acc),
            "mean_prediction": float(predictions.mean()),
            "mean_actual": float(actuals.mean()),
        }

    def _evaluate_calibration(
        self,
        confidences: np.ndarray,
        directions: np.ndarray,
        outcomes: np.ndarray,
    ) -> Dict:
        """Evaluate confidence calibration."""
        # Only consider trades (not neutral)
        trade_mask = directions != 0
        if not trade_mask.any():
            return {"ece": 1.0, "bins": []}

        conf = confidences[trade_mask]
        wins = (outcomes[trade_mask] == 1).astype(float)

        # Expected Calibration Error
        n_bins = 10
        bin_edges = np.linspace(0, 1, n_bins + 1)

        ece = 0.0
        bins_data = []

        for i in range(n_bins):
            mask = (conf >= bin_edges[i]) & (conf < bin_edges[i + 1])
            if mask.sum() > 0:
                bin_conf = conf[mask].mean()
                bin_acc = wins[mask].mean()
                bin_count = mask.sum()

                ece += mask.sum() * abs(bin_conf - bin_acc)

                bins_data.append({
                    "bin": i,
                    "confidence": float(bin_conf),
                    "accuracy": float(bin_acc),
                    "count": int(bin_count),
                })

        ece /= len(conf)

        return {
            "ece": float(ece),
            "bins": bins_data,
            "mean_confidence": float(conf.mean()),
            "actual_accuracy": float(wins.mean()),
        }

    def _evaluate_trading(
        self,
        predictions: List,
        test_df: pd.DataFrame,
        pred_directions: np.ndarray,
    ) -> Dict:
        """Evaluate simulated trading performance."""
        outcomes = test_df["label_outcome"].values
        pnls = test_df["label_pnl_pct"].values

        # Only consider non-neutral predictions
        trade_mask = pred_directions != 0

        if not trade_mask.any():
            return {
                "trades": 0,
                "win_rate": 0,
                "profit_factor": 0,
                "sharpe_ratio": 0,
            }

        trade_outcomes = outcomes[trade_mask]
        trade_pnls = pnls[trade_mask]

        # Basic metrics
        n_trades = len(trade_outcomes)
        wins = (trade_outcomes == 1).sum()
        losses = (trade_outcomes == -1).sum()
        win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0

        # Profit factor
        gross_profit = trade_pnls[trade_pnls > 0].sum() if (trade_pnls > 0).any() else 0
        gross_loss = abs(trade_pnls[trade_pnls < 0].sum()) if (trade_pnls < 0).any() else 0.01
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # Sharpe ratio (simplified, daily)
        if len(trade_pnls) > 1:
            mean_return = trade_pnls.mean()
            std_return = trade_pnls.std()
            sharpe_ratio = (mean_return / std_return * np.sqrt(252)) if std_return > 0 else 0
        else:
            sharpe_ratio = 0

        # Max drawdown
        cumulative = np.cumsum(trade_pnls)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = running_max - cumulative
        max_drawdown = drawdown.max() if len(drawdown) > 0 else 0

        # Average trade PnL
        avg_pnl = trade_pnls.mean()
        avg_win = trade_pnls[trade_pnls > 0].mean() if (trade_pnls > 0).any() else 0
        avg_loss = trade_pnls[trade_pnls < 0].mean() if (trade_pnls < 0).any() else 0

        return {
            "trades": int(n_trades),
            "wins": int(wins),
            "losses": int(losses),
            "win_rate": float(win_rate),
            "profit_factor": float(profit_factor),
            "sharpe_ratio": float(sharpe_ratio),
            "max_drawdown_pct": float(max_drawdown),
            "avg_pnl_pct": float(avg_pnl),
            "avg_win_pct": float(avg_win),
            "avg_loss_pct": float(avg_loss),
            "total_pnl_pct": float(trade_pnls.sum()),
        }

    def _validate_metrics(self, metrics: Dict) -> Dict:
        """Validate metrics against configured thresholds."""
        validation = {
            "passed": True,
            "failures": [],
        }

        # Check Sharpe ratio
        if "trading" in metrics:
            sharpe = metrics["trading"].get("sharpe_ratio", 0)
            if sharpe < self._config.min_sharpe_ratio:
                validation["passed"] = False
                validation["failures"].append(
                    f"sharpe_ratio {sharpe:.2f} < {self._config.min_sharpe_ratio}"
                )

            # Check profit factor
            pf = metrics["trading"].get("profit_factor", 0)
            if pf < self._config.min_profit_factor:
                validation["passed"] = False
                validation["failures"].append(
                    f"profit_factor {pf:.2f} < {self._config.min_profit_factor}"
                )

            # Check win rate
            wr = metrics["trading"].get("win_rate", 0)
            if wr < self._config.min_win_rate:
                validation["passed"] = False
                validation["failures"].append(
                    f"win_rate {wr:.2f} < {self._config.min_win_rate}"
                )

            # Check max drawdown
            dd = metrics["trading"].get("max_drawdown_pct", 100) / 100
            if dd > self._config.max_max_drawdown:
                validation["passed"] = False
                validation["failures"].append(
                    f"max_drawdown {dd:.2f} > {self._config.max_max_drawdown}"
                )

        return validation

    def generate_report(self, metrics: Dict) -> str:
        """Generate human-readable evaluation report."""
        lines = []
        lines.append("=" * 60)
        lines.append("MODEL EVALUATION REPORT")
        lines.append("=" * 60)
        lines.append("")

        # Direction metrics
        if "direction" in metrics:
            d = metrics["direction"]
            lines.append("DIRECTION PREDICTION")
            lines.append("-" * 40)
            lines.append(f"  Accuracy:            {d['accuracy']:.4f}")
            lines.append(f"  High-conf Accuracy:  {d['high_confidence_accuracy']:.4f}")
            lines.append(f"  Mean Confidence:     {d['mean_confidence']:.4f}")
            lines.append("")
            lines.append("  Per-class F1:")
            for cls, score in d['f1'].items():
                lines.append(f"    {cls:10s}: {score:.4f}")
            lines.append("")

        # Level metrics
        for level in ["sl", "tp"]:
            if level in metrics:
                m = metrics[level]
                lines.append(f"{level.upper()} REGRESSION")
                lines.append("-" * 40)
                lines.append(f"  MAE:   {m['mae']:.4f}")
                lines.append(f"  RMSE:  {m['rmse']:.4f}")
                lines.append(f"  R2:    {m['r2']:.4f}")
                lines.append("")

        # Calibration
        if "calibration" in metrics:
            c = metrics["calibration"]
            lines.append("CALIBRATION")
            lines.append("-" * 40)
            lines.append(f"  ECE (Expected Calibration Error): {c['ece']:.4f}")
            lines.append(f"  Mean Confidence: {c['mean_confidence']:.4f}")
            lines.append(f"  Actual Accuracy: {c['actual_accuracy']:.4f}")
            lines.append("")

        # Trading metrics
        if "trading" in metrics:
            t = metrics["trading"]
            lines.append("TRADING PERFORMANCE")
            lines.append("-" * 40)
            lines.append(f"  Total Trades:    {t['trades']}")
            lines.append(f"  Win Rate:        {t['win_rate']:.2%}")
            lines.append(f"  Profit Factor:   {t['profit_factor']:.2f}")
            lines.append(f"  Sharpe Ratio:    {t['sharpe_ratio']:.2f}")
            lines.append(f"  Max Drawdown:    {t['max_drawdown_pct']:.2f}%")
            lines.append(f"  Avg Trade PnL:   {t['avg_pnl_pct']:.2f}%")
            lines.append(f"  Total PnL:       {t['total_pnl_pct']:.2f}%")
            lines.append("")

        # Validation
        if "validation" in metrics:
            v = metrics["validation"]
            lines.append("VALIDATION")
            lines.append("-" * 40)
            status = "PASSED" if v['passed'] else "FAILED"
            lines.append(f"  Status: {status}")
            if v['failures']:
                lines.append("  Failures:")
                for f in v['failures']:
                    lines.append(f"    - {f}")
            lines.append("")

        lines.append("=" * 60)

        return "\n".join(lines)
