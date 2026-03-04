# -*- coding: utf-8 -*-
"""
Training Pipeline for ML System.

Orchestrates the full training workflow:
1. Data collection
2. Preprocessing
3. Feature engineering
4. Labeling
5. Training
6. Evaluation
7. Model saving

Usage:
    pipeline = TrainingPipeline()
    result = await pipeline.run(symbols=["BTCUSDT", "ETHUSDT"])
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import structlog

from config.settings import settings
from src.ml.data import HistoricalDataCollector, DataPreprocessor, MarketSnapshot
from src.ml.features import FeatureEngineer
from src.ml.models import ModelEnsemble
from .labeler import Labeler
from .trainer import Trainer
from .evaluator import Evaluator


logger = structlog.get_logger(__name__)


class TrainingPipeline:
    """
    End-to-end training pipeline.

    Collects data, creates features and labels, trains models,
    evaluates performance, and saves the trained ensemble.
    """

    def __init__(
        self,
        collector: Optional[HistoricalDataCollector] = None,
        preprocessor: Optional[DataPreprocessor] = None,
        feature_engineer: Optional[FeatureEngineer] = None,
    ):
        """
        Initialize training pipeline.

        Args:
            collector: Optional data collector (created if not provided)
            preprocessor: Optional preprocessor (created if not provided)
            feature_engineer: Optional feature engineer (created if not provided)
        """
        self._collector = collector or HistoricalDataCollector()
        self._preprocessor = preprocessor or DataPreprocessor()
        self._feature_engineer = feature_engineer or FeatureEngineer()
        self._labeler = Labeler()
        self._trainer = Trainer()
        self._evaluator = Evaluator()

        self._config = settings.ml

        logger.info("training_pipeline_init")

    async def run(
        self,
        symbols: Optional[List[str]] = None,
        save_models: bool = True,
        hyperopt: bool = False,
    ) -> Dict:
        """
        Run the full training pipeline.

        Args:
            symbols: List of symbols to train on (default: top by volume)
            save_models: Whether to save trained models
            hyperopt: Whether to run hyperparameter optimization

        Returns:
            Dictionary with training results
        """
        start_time = datetime.now(timezone.utc)

        logger.info(
            "training_pipeline_start",
            symbols=symbols,
            hyperopt=hyperopt,
        )

        result = {
            "status": "running",
            "start_time": start_time.isoformat(),
            "symbols": symbols or [],
            "steps": {},
        }

        try:
            # Step 1: Collect data
            logger.info("step_1_collecting_data")
            snapshots = await self._collect_data(symbols)
            result["steps"]["collect"] = {
                "snapshots": len(snapshots),
                "status": "complete",
            }

            if len(snapshots) < self._config.data.min_samples_per_symbol:
                raise ValueError(
                    f"Insufficient data: {len(snapshots)} < {self._config.data.min_samples_per_symbol}"
                )

            # Step 2: Preprocess
            logger.info("step_2_preprocessing")
            df = self._preprocessor.snapshots_to_dataframe(snapshots)
            df = self._preprocessor.clean_data(df)
            result["steps"]["preprocess"] = {
                "rows": len(df),
                "status": "complete",
            }

            # Step 3: Feature engineering
            logger.info("step_3_feature_engineering")
            df = self._feature_engineer.extract_features(df)
            result["steps"]["features"] = {
                "feature_count": len(df.columns),
                "status": "complete",
            }

            # Step 4: Create labels
            logger.info("step_4_labeling")
            df = self._labeler.create_labels(df)
            label_stats = self._labeler.get_label_statistics(df)
            result["steps"]["labeling"] = {
                "stats": label_stats,
                "status": "complete",
            }

            # Step 5: Split data
            logger.info("step_5_splitting_data")
            train_df, val_df, test_df = self._preprocessor.split_time_series(df)

            # Normalize (fit on train only)
            train_df = self._preprocessor.normalize_features(train_df, fit=True)
            val_df = self._preprocessor.normalize_features(val_df, fit=False)
            test_df = self._preprocessor.normalize_features(test_df, fit=False)

            result["steps"]["split"] = {
                "train_rows": len(train_df),
                "val_rows": len(val_df),
                "test_rows": len(test_df),
                "status": "complete",
            }

            # Step 6: Train models
            logger.info("step_6_training")
            if hyperopt:
                ensemble, best_params = self._trainer.train_with_hyperopt(
                    train_df, val_df
                )
                result["steps"]["training"] = {
                    "hyperopt": True,
                    "best_params": best_params,
                    "status": "complete",
                }
            else:
                ensemble = self._trainer.train(train_df, val_df)
                result["steps"]["training"] = {
                    "hyperopt": False,
                    "status": "complete",
                }

            # Step 7: Evaluate
            logger.info("step_7_evaluating")
            metrics = self._evaluator.evaluate(ensemble, test_df)
            result["steps"]["evaluation"] = {
                "metrics": metrics,
                "passed": metrics.get("validation", {}).get("passed", False),
                "status": "complete",
            }

            # Generate report
            report = self._evaluator.generate_report(metrics)
            logger.info("evaluation_report", report=report)

            # Step 8: Save models
            if save_models:
                logger.info("step_8_saving_models")
                model_dir = ensemble.save_models()

                # Save scalers
                scaler_path = Path(model_dir) / "scalers.json"
                self._preprocessor.save_scalers(str(scaler_path))

                result["steps"]["save"] = {
                    "model_dir": model_dir,
                    "status": "complete",
                }

            # Final status
            result["status"] = "success" if metrics["validation"]["passed"] else "validation_failed"
            result["end_time"] = datetime.now(timezone.utc).isoformat()
            result["duration_seconds"] = (
                datetime.now(timezone.utc) - start_time
            ).total_seconds()

            logger.info(
                "training_pipeline_complete",
                status=result["status"],
                duration=result["duration_seconds"],
            )

        except Exception as e:
            logger.error("training_pipeline_error", error=str(e))
            result["status"] = "error"
            result["error"] = str(e)
            result["end_time"] = datetime.now(timezone.utc).isoformat()
            raise

        return result

    async def _collect_data(
        self,
        symbols: Optional[List[str]] = None,
    ) -> List[MarketSnapshot]:
        """Collect historical data for training."""
        if symbols is None:
            # Get top symbols by volume
            symbols = await self._collector.get_top_symbols(
                min_volume=self._config.data.min_volume_usd_24h,
                limit=20,
            )

        all_snapshots = []

        for symbol in symbols:
            try:
                snapshots = await self._collector.collect_symbol(
                    symbol=symbol,
                    days=self._config.data.history_days,
                )
                all_snapshots.extend(snapshots)
                logger.debug(
                    "symbol_collected",
                    symbol=symbol,
                    snapshots=len(snapshots),
                )
            except Exception as e:
                logger.warning(
                    "symbol_collection_failed",
                    symbol=symbol,
                    error=str(e),
                )

        return all_snapshots

    def run_sync(
        self,
        symbols: Optional[List[str]] = None,
        save_models: bool = True,
        hyperopt: bool = False,
    ) -> Dict:
        """
        Synchronous wrapper for run().

        Args:
            symbols: List of symbols
            save_models: Whether to save models
            hyperopt: Whether to run hyperopt

        Returns:
            Training results
        """
        return asyncio.run(self.run(symbols, save_models, hyperopt))

    def train_from_dataframe(
        self,
        df: pd.DataFrame,
        save_models: bool = True,
    ) -> Tuple[ModelEnsemble, Dict]:
        """
        Train from pre-loaded DataFrame.

        Useful for testing or when data is already available.

        Args:
            df: DataFrame with features (labels will be created)
            save_models: Whether to save models

        Returns:
            Tuple of (ensemble, metrics)
        """
        logger.info("training_from_dataframe", rows=len(df))

        # Feature engineering
        df = self._feature_engineer.extract_features(df)

        # Create labels
        df = self._labeler.create_labels(df)

        # Split
        train_df, val_df, test_df = self._preprocessor.split_time_series(df)

        # Normalize
        train_df = self._preprocessor.normalize_features(train_df, fit=True)
        val_df = self._preprocessor.normalize_features(val_df, fit=False)
        test_df = self._preprocessor.normalize_features(test_df, fit=False)

        # Train
        ensemble = self._trainer.train(train_df, val_df)

        # Evaluate
        metrics = self._evaluator.evaluate(ensemble, test_df)

        # Save
        if save_models:
            ensemble.save_models()

        return ensemble, metrics

    def retrain_if_needed(
        self,
        current_ensemble: ModelEnsemble,
        new_data_df: pd.DataFrame,
    ) -> Tuple[bool, Optional[ModelEnsemble]]:
        """
        Check if retraining is needed and retrain if so.

        Args:
            current_ensemble: Currently deployed ensemble
            new_data_df: New data since last training

        Returns:
            Tuple of (was_retrained, new_ensemble_or_none)
        """
        config = self._config.training

        # Check if enough new data
        if len(new_data_df) < config.min_new_samples:
            logger.info(
                "retraining_skipped_insufficient_data",
                new_samples=len(new_data_df),
                required=config.min_new_samples,
            )
            return False, None

        # Check model age
        # (Would need to track model creation time - simplified here)

        logger.info("retraining_model", new_samples=len(new_data_df))

        # Retrain
        new_ensemble, metrics = self.train_from_dataframe(new_data_df)

        # Validate new model is better
        if not metrics["validation"]["passed"]:
            logger.warning("new_model_failed_validation")
            return False, None

        return True, new_ensemble
