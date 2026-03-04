# -*- coding: utf-8 -*-
"""
Model Trainer for ML System.

Handles model training with:
- Time-series cross-validation
- Early stopping
- Hyperparameter optimization
- Model selection

Usage:
    trainer = Trainer()
    trained_ensemble = trainer.train(train_df, val_df)
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import structlog

from config.settings import settings
from src.ml.models import (
    DirectionClassifier,
    SLRegressor,
    TPRegressor,
    ConfidenceCalibrator,
    ModelEnsemble,
)


logger = structlog.get_logger(__name__)


class TimeSeriesSplit:
    """
    Time-series aware cross-validation splitter.

    Unlike sklearn's KFold, this respects temporal ordering
    and uses expanding or sliding window approach.
    """

    def __init__(
        self,
        n_splits: int = 5,
        test_size: Optional[int] = None,
        gap: int = 0,
    ):
        """
        Initialize splitter.

        Args:
            n_splits: Number of splits
            test_size: Size of test set (default: auto)
            gap: Gap between train and test to avoid leakage
        """
        self.n_splits = n_splits
        self.test_size = test_size
        self.gap = gap

    def split(self, X: np.ndarray) -> List[Tuple[np.ndarray, np.ndarray]]:
        """
        Generate train/test indices.

        Args:
            X: Feature array

        Yields:
            Tuple of (train_indices, test_indices)
        """
        n_samples = len(X)
        test_size = self.test_size or (n_samples // (self.n_splits + 1))

        splits = []

        for i in range(self.n_splits):
            # Test set starts after train
            test_start = n_samples - (self.n_splits - i) * test_size
            test_end = test_start + test_size

            # Train ends before gap
            train_end = test_start - self.gap

            if train_end <= 0 or test_end > n_samples:
                continue

            train_indices = np.arange(0, train_end)
            test_indices = np.arange(test_start, test_end)

            splits.append((train_indices, test_indices))

        return splits


class Trainer:
    """
    Trains ML models for signal optimization.

    Handles the full training pipeline:
    1. Feature/target preparation
    2. Cross-validation
    3. Model fitting
    4. Calibration
    5. Ensemble creation
    """

    def __init__(self):
        """Initialize trainer with config."""
        self._config = settings.ml.training
        self._model_config = settings.ml.models

        logger.info(
            "trainer_init",
            cv_type=self._config.cv_type,
            cv_folds=self._config.cv_folds,
        )

    def train(
        self,
        train_df: pd.DataFrame,
        val_df: Optional[pd.DataFrame] = None,
        feature_cols: Optional[List[str]] = None,
    ) -> ModelEnsemble:
        """
        Train all models and create ensemble.

        Args:
            train_df: Training data with features and labels
            val_df: Optional validation data
            feature_cols: Optional list of feature columns

        Returns:
            Trained ModelEnsemble
        """
        logger.info(
            "training_start",
            train_rows=len(train_df),
            val_rows=len(val_df) if val_df is not None else 0,
        )

        # Prepare features and targets
        X_train, y_direction, y_sl, y_tp = self._prepare_data(train_df, feature_cols)

        X_val, y_val_dir, y_val_sl, y_val_tp = None, None, None, None
        if val_df is not None and len(val_df) > 0:
            X_val, y_val_dir, y_val_sl, y_val_tp = self._prepare_data(val_df, feature_cols)

        # Train direction classifier
        direction_clf = self._train_direction_classifier(
            X_train, y_direction, X_val, y_val_dir
        )

        # Train level regressors
        sl_regressor = self._train_level_regressor(
            X_train, y_sl, X_val, y_val_sl, "sl"
        )
        tp_regressor = self._train_level_regressor(
            X_train, y_tp, X_val, y_val_tp, "tp"
        )

        # Train calibrator
        calibrator = self._train_calibrator(
            direction_clf, X_train, y_direction, train_df
        )

        # Create ensemble
        ensemble = ModelEnsemble()
        ensemble.set_models(
            direction_clf=direction_clf,
            sl_regressor=sl_regressor,
            tp_regressor=tp_regressor,
            calibrator=calibrator,
        )

        logger.info("training_complete")
        return ensemble

    def _prepare_data(
        self,
        df: pd.DataFrame,
        feature_cols: Optional[List[str]] = None,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Prepare features and targets from DataFrame."""
        # Determine feature columns
        if feature_cols is None:
            exclude_cols = ["symbol", "timestamp", "price", "open_interest"]
            exclude_cols += [c for c in df.columns if c.startswith("label_")]
            feature_cols = [c for c in df.columns if c not in exclude_cols]

        X = df[feature_cols].values.astype(np.float32)

        # Get targets
        y_direction = df["label_direction"].values if "label_direction" in df.columns else np.zeros(len(df))
        y_sl = df["label_sl_pct"].values if "label_sl_pct" in df.columns else np.ones(len(df))
        y_tp = df["label_tp_pct"].values if "label_tp_pct" in df.columns else np.ones(len(df)) * 2

        return X, y_direction, y_sl, y_tp

    def _train_direction_classifier(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_val: Optional[np.ndarray],
        y_val: Optional[np.ndarray],
    ) -> DirectionClassifier:
        """Train direction classifier with CV."""
        logger.info("training_direction_classifier")

        classifier = DirectionClassifier()

        if self._config.cv_type == "time_series" and X_val is None:
            # Use time-series CV
            cv_scores = self._cross_validate_classifier(classifier, X_train, y_train)
            logger.info(
                "direction_cv_scores",
                mean_accuracy=float(np.mean(cv_scores)),
                std_accuracy=float(np.std(cv_scores)),
            )

        # Final fit on all training data
        classifier.fit(X_train, y_train, X_val, y_val)

        return classifier

    def _train_level_regressor(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_val: Optional[np.ndarray],
        y_val: Optional[np.ndarray],
        level_type: str,
    ) -> Union[SLRegressor, TPRegressor]:
        """Train SL or TP regressor."""
        logger.info(f"training_{level_type}_regressor")

        if level_type == "sl":
            regressor = SLRegressor()
        else:
            regressor = TPRegressor()

        regressor.fit(X_train, y_train, X_val, y_val)

        return regressor

    def _train_calibrator(
        self,
        classifier: DirectionClassifier,
        X: np.ndarray,
        y_direction: np.ndarray,
        df: pd.DataFrame,
    ) -> ConfidenceCalibrator:
        """Train confidence calibrator."""
        logger.info("training_calibrator")

        # Get raw probabilities
        proba = classifier.predict_proba(X)

        # For calibration, we use the probability of the true class
        # This requires knowing outcomes, so we use label_outcome
        if "label_outcome" not in df.columns:
            # Fallback: calibrate on direction correctness
            predictions = classifier.predict(X)
            outcomes = (predictions == y_direction).astype(int)
            confidences = np.max(proba, axis=1)
        else:
            # Use actual trade outcomes
            outcomes = (df["label_outcome"].values == 1).astype(int)
            confidences = np.max(proba, axis=1)

        calibrator = ConfidenceCalibrator(method=self._model_config.calibration_method)
        calibrator.fit(confidences, outcomes)

        return calibrator

    def _cross_validate_classifier(
        self,
        classifier: DirectionClassifier,
        X: np.ndarray,
        y: np.ndarray,
    ) -> List[float]:
        """Perform time-series cross-validation."""
        splitter = TimeSeriesSplit(n_splits=self._config.cv_folds)
        scores = []

        for train_idx, test_idx in splitter.split(X):
            X_train_cv = X[train_idx]
            y_train_cv = y[train_idx]
            X_test_cv = X[test_idx]
            y_test_cv = y[test_idx]

            # Create fresh classifier for each fold
            clf = DirectionClassifier(name=f"cv_fold_{len(scores)}")
            clf.fit(X_train_cv, y_train_cv)

            predictions = clf.predict(X_test_cv)
            accuracy = (predictions == y_test_cv).mean()
            scores.append(accuracy)

        return scores

    def train_with_hyperopt(
        self,
        train_df: pd.DataFrame,
        val_df: pd.DataFrame,
        n_trials: int = 50,
    ) -> Tuple[ModelEnsemble, Dict]:
        """
        Train with hyperparameter optimization.

        Args:
            train_df: Training data
            val_df: Validation data
            n_trials: Number of optimization trials

        Returns:
            Tuple of (trained_ensemble, best_params)
        """
        try:
            import optuna
        except ImportError:
            logger.warning("optuna_not_installed_using_defaults")
            return self.train(train_df, val_df), {}

        logger.info("starting_hyperopt", n_trials=n_trials)

        X_train, y_dir, y_sl, y_tp = self._prepare_data(train_df)
        X_val, y_val_dir, y_val_sl, y_val_tp = self._prepare_data(val_df)

        def objective(trial):
            # Sample hyperparameters
            n_estimators = trial.suggest_int("n_estimators", 100, 1000)
            max_depth = trial.suggest_int("max_depth", 3, 12)
            learning_rate = trial.suggest_float("learning_rate", 0.01, 0.3, log=True)
            min_samples_leaf = trial.suggest_int("min_samples_leaf", 10, 100)

            # Train classifier with sampled params
            clf = DirectionClassifier()
            clf.fit(
                X_train, y_dir, X_val, y_val_dir,
                n_estimators=n_estimators,
                max_depth=max_depth,
                learning_rate=learning_rate,
                min_samples_leaf=min_samples_leaf,
            )

            # Evaluate on validation
            predictions = clf.predict(X_val)
            accuracy = (predictions == y_val_dir).mean()

            return accuracy

        # Optimize
        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

        best_params = study.best_params
        logger.info("hyperopt_complete", best_params=best_params)

        # Train final model with best params
        ensemble = self._train_with_params(
            train_df, val_df, best_params
        )

        return ensemble, best_params

    def _train_with_params(
        self,
        train_df: pd.DataFrame,
        val_df: pd.DataFrame,
        params: Dict,
    ) -> ModelEnsemble:
        """Train ensemble with specific hyperparameters."""
        X_train, y_dir, y_sl, y_tp = self._prepare_data(train_df)
        X_val, y_val_dir, y_val_sl, y_val_tp = self._prepare_data(val_df)

        # Direction classifier
        clf = DirectionClassifier()
        clf.fit(X_train, y_dir, X_val, y_val_dir, **params)

        # Level regressors
        sl_reg = SLRegressor()
        sl_reg.fit(X_train, y_sl, X_val, y_val_sl, **params)

        tp_reg = TPRegressor()
        tp_reg.fit(X_train, y_tp, X_val, y_val_tp, **params)

        # Calibrator
        calibrator = self._train_calibrator(clf, X_train, y_dir, train_df)

        # Ensemble
        ensemble = ModelEnsemble()
        ensemble.set_models(
            direction_clf=clf,
            sl_regressor=sl_reg,
            tp_regressor=tp_reg,
            calibrator=calibrator,
        )

        return ensemble
