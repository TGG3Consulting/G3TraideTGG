# -*- coding: utf-8 -*-
"""
Signal Training Pipeline for ML System.

Trains models on REAL backtest results, NOT on fake data!

Flow:
1. Run REAL backtest on signals.jsonl
2. Create labels from REAL outcomes
3. Extract features
4. Train models
5. Evaluate on test set
6. Save models

Usage:
    pipeline = SignalTrainingPipeline()
    result = pipeline.run("logs/signals.jsonl")
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import structlog

from config.settings import settings
from src.ml.integration.backtester_integration import MLBacktesterIntegration
from src.ml.training.signal_labeler import SignalLabeler
from src.ml.models import ModelEnsemble, DirectionClassifier
from .trainer import Trainer, TimeSeriesSplit
from .evaluator import Evaluator
from .validator import ModelValidator


logger = structlog.get_logger(__name__)


class SignalTrainingPipeline:
    """
    Training pipeline using REAL backtest results.

    Unlike the old pipeline that used lookahead on raw prices,
    this uses actual trading outcomes from PositionSimulator.
    """

    def __init__(self):
        """Initialize pipeline components."""
        self._backtester = MLBacktesterIntegration()
        self._labeler = SignalLabeler()
        self._trainer = Trainer()
        self._evaluator = Evaluator()
        self._validator = ModelValidator()

        self._config = settings.ml

        logger.info("signal_training_pipeline_init")

    def run(
        self,
        signals_path: str = "logs/signals.jsonl",
        test_size: float = 0.2,
        save_models: bool = True,
    ) -> Dict:
        """
        Run full training pipeline.

        Args:
            signals_path: Path to signals log
            test_size: Fraction for test set (temporal split!)
            save_models: Whether to save trained models

        Returns:
            Dictionary with training results
        """
        start_time = datetime.now(timezone.utc)

        logger.info(
            "signal_pipeline_start",
            signals_path=signals_path,
            test_size=test_size,
        )

        result = {
            "status": "running",
            "start_time": start_time.isoformat(),
            "steps": {},
        }

        try:
            # Step 1: Run REAL backtest
            logger.info("step_1_running_backtest")
            backtest_results = self._backtester.run_backtest(signals_path)

            result["steps"]["backtest"] = {
                "total_signals": len(backtest_results),
                "filled": sum(1 for r in backtest_results if r.entry_filled),
                "status": "complete",
            }

            if len(backtest_results) < 100:
                raise ValueError(
                    f"Not enough signals: {len(backtest_results)} < 100 minimum"
                )

            # Step 2: Create training data with REAL labels
            logger.info("step_2_creating_training_data")
            df = self._labeler.create_training_data(backtest_results)

            # Filter to filled signals only
            df = df[df["entry_filled"] == True].copy()

            label_stats = self._labeler.get_label_statistics(df)
            result["steps"]["labeling"] = {
                "samples": len(df),
                "win_rate": label_stats.get("win_rate", 0),
                "avg_pnl": label_stats.get("avg_pnl_pct", 0),
                "status": "complete",
            }

            # Step 3: Temporal split (NEVER random!)
            logger.info("step_3_splitting_data")
            df = df.sort_values("timestamp").reset_index(drop=True)

            split_idx = int(len(df) * (1 - test_size))
            train_df = df.iloc[:split_idx].copy()
            test_df = df.iloc[split_idx:].copy()

            result["steps"]["split"] = {
                "train_samples": len(train_df),
                "test_samples": len(test_df),
                "status": "complete",
            }

            # Check minimum requirements
            if len(train_df) < 80:
                raise ValueError(f"Not enough training data: {len(train_df)} < 80")
            if len(test_df) < 20:
                raise ValueError(f"Not enough test data: {len(test_df)} < 20")

            # Step 4: Prepare features and labels
            logger.info("step_4_preparing_features")
            feature_cols = self._get_feature_columns(train_df)

            X_train = train_df[feature_cols].values.astype(np.float32)
            y_train = train_df["label_win"].values.astype(np.int32)

            X_test = test_df[feature_cols].values.astype(np.float32)
            y_test = test_df["label_win"].values.astype(np.int32)

            result["steps"]["features"] = {
                "feature_count": len(feature_cols),
                "features": feature_cols,
                "status": "complete",
            }

            # Step 5: Train model
            logger.info("step_5_training_model")
            classifier = DirectionClassifier(name="signal_classifier")
            classifier.fit(X_train, y_train)

            # Cross-validation scores
            cv_splitter = TimeSeriesSplit(n_splits=5)
            cv_scores = []
            for train_idx, val_idx in cv_splitter.split(X_train):
                clf = DirectionClassifier(name="cv_fold")
                clf.fit(X_train[train_idx], y_train[train_idx])
                preds = clf.predict(X_train[val_idx])
                acc = (preds == y_train[val_idx]).mean()
                cv_scores.append(acc)

            result["steps"]["training"] = {
                "cv_mean_accuracy": float(np.mean(cv_scores)),
                "cv_std_accuracy": float(np.std(cv_scores)),
                "status": "complete",
            }

            # Step 6: Evaluate on test set
            logger.info("step_6_evaluating")
            test_preds = classifier.predict(X_test)
            test_accuracy = (test_preds == y_test).mean()

            # Calculate more metrics
            wins_predicted = test_preds == 1
            actual_wins = y_test == 1

            precision = (
                (wins_predicted & actual_wins).sum() / wins_predicted.sum()
                if wins_predicted.sum() > 0 else 0
            )
            recall = (
                (wins_predicted & actual_wins).sum() / actual_wins.sum()
                if actual_wins.sum() > 0 else 0
            )

            result["steps"]["evaluation"] = {
                "test_accuracy": float(test_accuracy),
                "test_precision": float(precision),
                "test_recall": float(recall),
                "train_accuracy": float(np.mean(cv_scores)),
                "overfit_gap": float(np.mean(cv_scores) - test_accuracy),
                "status": "complete",
            }

            # Check for overfitting
            is_overfitting = (np.mean(cv_scores) - test_accuracy) > 0.15

            # Step 7: Validate
            logger.info("step_7_validating")
            passed = (
                test_accuracy >= 0.52 and  # Better than random
                not is_overfitting and
                len(train_df) >= 80
            )

            result["steps"]["validation"] = {
                "passed": passed,
                "is_overfitting": is_overfitting,
                "status": "complete",
            }

            # Step 8: Save if valid
            if save_models and passed:
                logger.info("step_8_saving_model")
                model_dir = Path(self._config.models.save_dir)
                model_dir.mkdir(parents=True, exist_ok=True)

                # Save classifier
                classifier.save(str(model_dir / "signal_classifier.pkl"))

                # Save feature columns
                import json
                with open(model_dir / "feature_columns.json", "w") as f:
                    json.dump(feature_cols, f)

                result["steps"]["save"] = {
                    "model_dir": str(model_dir),
                    "status": "complete",
                }

            # Final status
            result["status"] = "success" if passed else "validation_failed"
            result["end_time"] = datetime.now(timezone.utc).isoformat()
            result["passed"] = passed

            logger.info(
                "signal_pipeline_complete",
                status=result["status"],
                test_accuracy=test_accuracy,
                passed=passed,
            )

        except Exception as e:
            logger.error("signal_pipeline_error", error=str(e))
            result["status"] = "error"
            result["error"] = str(e)
            raise

        return result

    def _get_feature_columns(self, df: pd.DataFrame) -> List[str]:
        """Get numeric feature columns for training."""
        # Exclude non-feature columns
        exclude = {
            "signal_id", "symbol", "timestamp", "direction",
            "signal_type", "confidence", "entry_filled",
            "label_profitable", "label_pnl_pct", "label_net_pnl_pct",
            "label_exit_reason", "label_tp1_hit", "label_tp2_hit",
            "label_tp3_hit", "label_sl_hit", "label_hold_hours", "label_win",
        }

        feature_cols = []
        for col in df.columns:
            if col in exclude:
                continue
            if df[col].dtype in [np.float64, np.float32, np.int64, np.int32]:
                feature_cols.append(col)

        return feature_cols
