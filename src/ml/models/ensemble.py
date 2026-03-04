# -*- coding: utf-8 -*-
"""
Model Ensemble for ML System.

Combines Direction Classifier, Level Regressors, and Confidence Calibrator
into a unified prediction interface.

Usage:
    ensemble = ModelEnsemble()
    ensemble.load_models()  # Load pre-trained models

    prediction = ensemble.predict(features)
    # Returns: PredictionResult with direction, confidence, SL, TP levels
"""

import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import structlog

from config.settings import settings
from src.ml.data.schemas import Direction, PredictionResult
from .direction import DirectionClassifier
from .levels import SLRegressor, TPRegressor, MultiTargetLevelRegressor
from .confidence import ConfidenceCalibrator, DirectionalConfidenceCalibrator


logger = structlog.get_logger(__name__)


class ModelEnsemble:
    """
    Unified interface for all ML models.

    Combines:
    - Direction prediction (long/short/neutral)
    - Stop-loss level prediction
    - Take-profit level prediction
    - Confidence calibration
    """

    def __init__(self):
        """Initialize model ensemble."""
        self._config = settings.ml.models
        self._opt_config = settings.ml.optimization

        # Models
        self._direction_clf: Optional[DirectionClassifier] = None
        self._sl_regressor: Optional[SLRegressor] = None
        self._tp_regressor: Optional[TPRegressor] = None
        self._calibrator: Optional[ConfidenceCalibrator] = None
        self._dir_calibrator: Optional[DirectionalConfidenceCalibrator] = None

        self._is_loaded = False
        self._models_version: Optional[str] = None

        logger.info("model_ensemble_init")

    @property
    def is_loaded(self) -> bool:
        """Whether models are loaded."""
        return self._is_loaded

    def set_models(
        self,
        direction_clf: DirectionClassifier,
        sl_regressor: SLRegressor,
        tp_regressor: TPRegressor,
        calibrator: Optional[ConfidenceCalibrator] = None,
    ) -> "ModelEnsemble":
        """
        Set models directly (for training pipeline).

        Args:
            direction_clf: Fitted direction classifier
            sl_regressor: Fitted SL regressor
            tp_regressor: Fitted TP regressor
            calibrator: Optional confidence calibrator

        Returns:
            Self
        """
        self._direction_clf = direction_clf
        self._sl_regressor = sl_regressor
        self._tp_regressor = tp_regressor
        self._calibrator = calibrator

        self._is_loaded = all([
            direction_clf.is_fitted,
            sl_regressor.is_fitted,
            tp_regressor.is_fitted,
        ])

        logger.info(
            "models_set",
            direction_fitted=direction_clf.is_fitted,
            sl_fitted=sl_regressor.is_fitted,
            tp_fitted=tp_regressor.is_fitted,
            has_calibrator=calibrator is not None,
        )

        return self

    def load_models(self, model_dir: Optional[str] = None) -> "ModelEnsemble":
        """
        Load all models from directory.

        Args:
            model_dir: Optional model directory

        Returns:
            Self
        """
        model_dir = Path(model_dir or self._config.save_dir)

        if not model_dir.exists():
            logger.error("model_dir_not_found", path=str(model_dir))
            raise FileNotFoundError(f"Model directory not found: {model_dir}")

        # Load direction classifier
        direction_path = model_dir / "direction_classifier.pkl"
        if direction_path.exists():
            self._direction_clf = DirectionClassifier()
            self._direction_clf.load(str(direction_path))
        else:
            logger.warning("direction_model_not_found")

        # Load SL regressor
        sl_path = model_dir / "sl_regressor.pkl"
        if sl_path.exists():
            self._sl_regressor = SLRegressor()
            self._sl_regressor.load(str(sl_path))
        else:
            logger.warning("sl_model_not_found")

        # Load TP regressor
        tp_path = model_dir / "tp_regressor.pkl"
        if tp_path.exists():
            self._tp_regressor = TPRegressor()
            self._tp_regressor.load(str(tp_path))
        else:
            logger.warning("tp_model_not_found")

        # Load calibrator
        cal_path = model_dir / "confidence_calibrator.pkl"
        if cal_path.exists():
            self._calibrator = ConfidenceCalibrator()
            self._calibrator.load(str(cal_path))

        # Load directional calibrator
        dir_cal_path = model_dir / "directional_calibrator.pkl"
        if dir_cal_path.exists():
            self._dir_calibrator = DirectionalConfidenceCalibrator()
            self._dir_calibrator.load(str(dir_cal_path))

        # Load version info
        version_path = model_dir / "version.txt"
        if version_path.exists():
            self._models_version = version_path.read_text().strip()

        self._is_loaded = all([
            self._direction_clf is not None and self._direction_clf.is_fitted,
            self._sl_regressor is not None and self._sl_regressor.is_fitted,
            self._tp_regressor is not None and self._tp_regressor.is_fitted,
        ])

        logger.info(
            "models_loaded",
            model_dir=str(model_dir),
            version=self._models_version,
            is_loaded=self._is_loaded,
        )

        return self

    def save_models(self, model_dir: Optional[str] = None) -> str:
        """
        Save all models to directory.

        Args:
            model_dir: Optional model directory

        Returns:
            Directory where models were saved
        """
        model_dir = Path(model_dir or self._config.save_dir)
        model_dir.mkdir(parents=True, exist_ok=True)

        if self._direction_clf:
            self._direction_clf.save(str(model_dir / "direction_classifier.pkl"))

        if self._sl_regressor:
            self._sl_regressor.save(str(model_dir / "sl_regressor.pkl"))

        if self._tp_regressor:
            self._tp_regressor.save(str(model_dir / "tp_regressor.pkl"))

        if self._calibrator:
            self._calibrator.save(str(model_dir / "confidence_calibrator.pkl"))

        if self._dir_calibrator:
            self._dir_calibrator.save(str(model_dir / "directional_calibrator.pkl"))

        # Save version
        version = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        (model_dir / "version.txt").write_text(version)
        self._models_version = version

        logger.info("models_saved", model_dir=str(model_dir), version=version)
        return str(model_dir)

    def predict(
        self,
        X: Union[np.ndarray, pd.DataFrame],
        symbols: Optional[List[str]] = None,
    ) -> List[PredictionResult]:
        """
        Make predictions using all models.

        Args:
            X: Feature matrix
            symbols: Optional symbol names for each row

        Returns:
            List of PredictionResult objects
        """
        if not self._is_loaded:
            raise ValueError("Models not loaded. Call load_models() first.")

        if isinstance(X, pd.DataFrame):
            X_arr = X.values
        else:
            X_arr = np.asarray(X)

        n_samples = X_arr.shape[0]

        if symbols is None:
            symbols = ["UNKNOWN"] * n_samples

        # Get direction predictions with probabilities
        directions, raw_confidences = self._direction_clf.predict_direction_with_confidence(X_arr)
        dir_probs = self._direction_clf.get_directional_probabilities(X_arr)

        # Calibrate confidences
        if self._dir_calibrator and self._dir_calibrator.is_fitted:
            calibrated_conf = self._dir_calibrator.calibrate(raw_confidences, directions)
        elif self._calibrator and self._calibrator.is_fitted:
            calibrated_conf = self._calibrator.calibrate(raw_confidences)
        else:
            calibrated_conf = raw_confidences

        # Get SL/TP predictions
        sl_predictions = self._sl_regressor.predict(X_arr)
        tp_predictions = self._tp_regressor.predict(X_arr)

        # Build results
        results = []
        timestamp = datetime.now(timezone.utc)

        for i in range(n_samples):
            direction = directions[i]
            if direction == 1:
                dir_enum = Direction.LONG
            elif direction == -1:
                dir_enum = Direction.SHORT
            else:
                dir_enum = Direction.NEUTRAL

            result = PredictionResult(
                symbol=symbols[i],
                timestamp=timestamp,
                direction=dir_enum,
                confidence=float(calibrated_conf[i]),
                raw_confidence=float(raw_confidences[i]),
                predicted_sl_pct=float(sl_predictions[i]),
                predicted_tp1_pct=float(tp_predictions[i]),
                predicted_tp2_pct=float(tp_predictions[i] * 1.5),  # Approximate
                predicted_tp3_pct=float(tp_predictions[i] * 2.0),  # Approximate
                long_probability=float(dir_probs["long"][i]),
                short_probability=float(dir_probs["short"][i]),
                neutral_probability=float(dir_probs["neutral"][i]),
            )
            results.append(result)

        logger.debug(
            "ensemble_predictions",
            n_samples=n_samples,
            long_count=sum(1 for d in directions if d == 1),
            short_count=sum(1 for d in directions if d == -1),
            neutral_count=sum(1 for d in directions if d == 0),
        )

        return results

    def predict_single(
        self,
        X: Union[np.ndarray, pd.DataFrame],
        symbol: str = "UNKNOWN",
    ) -> PredictionResult:
        """
        Make prediction for single sample.

        Args:
            X: Feature vector (1D or 2D with 1 row)
            symbol: Symbol name

        Returns:
            PredictionResult
        """
        if isinstance(X, pd.DataFrame):
            X_arr = X.values
        else:
            X_arr = np.asarray(X)

        if X_arr.ndim == 1:
            X_arr = X_arr.reshape(1, -1)

        results = self.predict(X_arr, [symbol])
        return results[0]

    def should_trade(self, prediction: PredictionResult) -> bool:
        """
        Check if prediction meets trading criteria.

        Args:
            prediction: PredictionResult to evaluate

        Returns:
            True if signal should be traded
        """
        # Skip neutral
        if prediction.direction == Direction.NEUTRAL:
            return False

        # Check minimum confidence
        if prediction.confidence < self._opt_config.min_confidence:
            return False

        # Check predicted R:R
        predicted_rr = prediction.predicted_tp1_pct / prediction.predicted_sl_pct
        if predicted_rr < self._opt_config.min_predicted_rr:
            return False

        return True

    def get_feature_importance(self) -> Dict[str, Dict[str, float]]:
        """
        Get feature importance from all models.

        Returns:
            Dict mapping model name to feature importance dict
        """
        importance = {}

        if self._direction_clf:
            importance["direction"] = self._direction_clf.get_feature_importance()

        if self._sl_regressor:
            importance["sl"] = self._sl_regressor.get_feature_importance()

        if self._tp_regressor:
            importance["tp"] = self._tp_regressor.get_feature_importance()

        return importance

    def get_aggregated_importance(self) -> Dict[str, float]:
        """
        Get aggregated feature importance across all models.

        Returns:
            Dict mapping feature names to average importance
        """
        all_importance = self.get_feature_importance()

        aggregated = {}
        counts = {}

        for model_name, importance in all_importance.items():
            for feature, value in importance.items():
                if feature not in aggregated:
                    aggregated[feature] = 0
                    counts[feature] = 0
                aggregated[feature] += value
                counts[feature] += 1

        # Average
        for feature in aggregated:
            if counts[feature] > 0:
                aggregated[feature] /= counts[feature]

        # Sort by importance
        return dict(sorted(aggregated.items(), key=lambda x: x[1], reverse=True))
