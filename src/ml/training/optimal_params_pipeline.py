# -*- coding: utf-8 -*-
"""
Optimal Parameters Training Pipeline.

Trains REGRESSION models to predict optimal SL/TP/Lifetime.
NOT classification! We predict actual values, not WIN/LOSS.

Usage:
    pipeline = OptimalParamsPipeline()
    result = await pipeline.run("logs/signals.jsonl")

After training:
    sl_pct = pipeline.predict_sl(features)
    tp_pct = pipeline.predict_tp(features)
    lifetime = pipeline.predict_lifetime(features)
"""

import json
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import structlog

from config.settings import settings

from .optimal_params_labeler import OptimalParamsLabeler


logger = structlog.get_logger(__name__)


class OptimalParamsPipeline:
    """
    Training pipeline for optimal parameters prediction.

    Trains 3 REGRESSION models:
    - SL model: predicts optimal stop-loss %
    - TP model: predicts optimal take-profit %
    - Lifetime model: predicts optimal position lifetime in hours
    """

    def __init__(self):
        """Initialize pipeline."""
        self._labeler = OptimalParamsLabeler()

        self._sl_model = None
        self._tp_model = None
        self._lifetime_model = None
        self._scaler = None
        self._feature_columns = []

        logger.info("optimal_params_pipeline_init")

    async def run(
        self,
        signals_path: str = "logs/signals.jsonl",
        test_size: float = 0.2,
        save_models: bool = True,
        limit: Optional[int] = None,
    ) -> Dict:
        """
        Run full training pipeline.

        Args:
            signals_path: Path to signals log
            test_size: Fraction for test set (temporal split!)
            save_models: Whether to save trained models
            limit: Optional limit on signals

        Returns:
            Dictionary with training results
        """
        start_time = datetime.now(timezone.utc)

        logger.info(
            "optimal_params_pipeline_start",
            signals_path=signals_path,
            test_size=test_size,
        )

        result = {
            "status": "running",
            "start_time": start_time.isoformat(),
            "steps": {},
        }

        try:
            # Step 1: Create training data with optimal params labels
            logger.info("step_1_creating_training_data")
            df = await self._labeler.create_training_data(signals_path, limit)

            result["steps"]["labeling"] = {
                "total_samples": len(df),
                "would_be_profitable": int(df["label_would_be_profitable"].sum()),
                "status": "complete",
            }

            if len(df) < 100:
                raise ValueError(f"Not enough data: {len(df)} < 100")

            # Step 2: Temporal split (NEVER random!)
            logger.info("step_2_temporal_split")
            df = df.sort_values("timestamp").reset_index(drop=True)

            split_idx = int(len(df) * (1 - test_size))
            train_df = df.iloc[:split_idx].copy()
            test_df = df.iloc[split_idx:].copy()

            result["steps"]["split"] = {
                "train_samples": len(train_df),
                "test_samples": len(test_df),
                "status": "complete",
            }

            # Step 3: Prepare features
            logger.info("step_3_preparing_features")
            feature_cols = self._get_available_features(train_df)
            self._feature_columns = feature_cols

            X_train = train_df[feature_cols].fillna(0).values.astype(np.float32)
            X_test = test_df[feature_cols].fillna(0).values.astype(np.float32)

            # Labels (what we predict)
            y_train_sl = train_df["label_optimal_sl_pct"].values
            y_train_tp = train_df["label_optimal_tp_pct"].values
            y_train_lifetime = train_df["label_optimal_lifetime_hours"].values

            y_test_sl = test_df["label_optimal_sl_pct"].values
            y_test_tp = test_df["label_optimal_tp_pct"].values
            y_test_lifetime = test_df["label_optimal_lifetime_hours"].values

            result["steps"]["features"] = {
                "feature_count": len(feature_cols),
                "features": feature_cols,
                "status": "complete",
            }

            # Step 4: Scale features
            logger.info("step_4_scaling")
            from sklearn.preprocessing import StandardScaler

            self._scaler = StandardScaler()
            X_train_scaled = self._scaler.fit_transform(X_train)
            X_test_scaled = self._scaler.transform(X_test)

            # Step 5: Train REGRESSION models
            logger.info("step_5_training_models")
            from sklearn.ensemble import GradientBoostingRegressor

            # SL Model
            self._sl_model = GradientBoostingRegressor(
                n_estimators=100,
                max_depth=5,
                learning_rate=0.1,
                random_state=42,
            )
            self._sl_model.fit(X_train_scaled, y_train_sl)

            # TP Model
            self._tp_model = GradientBoostingRegressor(
                n_estimators=100,
                max_depth=5,
                learning_rate=0.1,
                random_state=42,
            )
            self._tp_model.fit(X_train_scaled, y_train_tp)

            # Lifetime Model
            self._lifetime_model = GradientBoostingRegressor(
                n_estimators=100,
                max_depth=5,
                learning_rate=0.1,
                random_state=42,
            )
            self._lifetime_model.fit(X_train_scaled, y_train_lifetime)

            result["steps"]["training"] = {
                "models": ["sl", "tp", "lifetime"],
                "status": "complete",
            }

            # Step 6: Evaluate
            logger.info("step_6_evaluating")

            # Predictions
            pred_sl_train = self._sl_model.predict(X_train_scaled)
            pred_sl_test = self._sl_model.predict(X_test_scaled)

            pred_tp_train = self._tp_model.predict(X_train_scaled)
            pred_tp_test = self._tp_model.predict(X_test_scaled)

            pred_lifetime_train = self._lifetime_model.predict(X_train_scaled)
            pred_lifetime_test = self._lifetime_model.predict(X_test_scaled)

            # Calculate metrics
            from sklearn.metrics import mean_absolute_error, r2_score

            result["steps"]["evaluation"] = {
                "sl_model": {
                    "train_mae": float(mean_absolute_error(y_train_sl, pred_sl_train)),
                    "test_mae": float(mean_absolute_error(y_test_sl, pred_sl_test)),
                    "train_r2": float(r2_score(y_train_sl, pred_sl_train)),
                    "test_r2": float(r2_score(y_test_sl, pred_sl_test)),
                },
                "tp_model": {
                    "train_mae": float(mean_absolute_error(y_train_tp, pred_tp_train)),
                    "test_mae": float(mean_absolute_error(y_test_tp, pred_tp_test)),
                    "train_r2": float(r2_score(y_train_tp, pred_tp_train)),
                    "test_r2": float(r2_score(y_test_tp, pred_tp_test)),
                },
                "lifetime_model": {
                    "train_mae": float(mean_absolute_error(y_train_lifetime, pred_lifetime_train)),
                    "test_mae": float(mean_absolute_error(y_test_lifetime, pred_lifetime_test)),
                    "train_r2": float(r2_score(y_train_lifetime, pred_lifetime_train)),
                    "test_r2": float(r2_score(y_test_lifetime, pred_lifetime_test)),
                },
                "status": "complete",
            }

            # Feature importance
            result["steps"]["feature_importance"] = {
                "sl_model": dict(zip(feature_cols, self._sl_model.feature_importances_.tolist())),
                "tp_model": dict(zip(feature_cols, self._tp_model.feature_importances_.tolist())),
                "lifetime_model": dict(zip(feature_cols, self._lifetime_model.feature_importances_.tolist())),
            }

            # Step 7: Save models
            if save_models:
                logger.info("step_7_saving_models")
                model_dir = Path(settings.ml.models.save_dir)
                model_dir.mkdir(parents=True, exist_ok=True)

                # Save models
                with open(model_dir / "optimal_sl_model.pkl", "wb") as f:
                    pickle.dump(self._sl_model, f)

                with open(model_dir / "optimal_tp_model.pkl", "wb") as f:
                    pickle.dump(self._tp_model, f)

                with open(model_dir / "optimal_lifetime_model.pkl", "wb") as f:
                    pickle.dump(self._lifetime_model, f)

                with open(model_dir / "optimal_scaler.pkl", "wb") as f:
                    pickle.dump(self._scaler, f)

                with open(model_dir / "optimal_feature_columns.json", "w") as f:
                    json.dump(self._feature_columns, f)

                result["steps"]["save"] = {
                    "model_dir": str(model_dir),
                    "files": [
                        "optimal_sl_model.pkl",
                        "optimal_tp_model.pkl",
                        "optimal_lifetime_model.pkl",
                        "optimal_scaler.pkl",
                        "optimal_feature_columns.json",
                    ],
                    "status": "complete",
                }

            result["status"] = "success"
            result["end_time"] = datetime.now(timezone.utc).isoformat()

            logger.info(
                "optimal_params_pipeline_complete",
                status="success",
                sl_test_mae=result["steps"]["evaluation"]["sl_model"]["test_mae"],
                tp_test_mae=result["steps"]["evaluation"]["tp_model"]["test_mae"],
            )

        except Exception as e:
            logger.error("optimal_params_pipeline_error", error=str(e))
            result["status"] = "error"
            result["error"] = str(e)
            raise

        return result

    def _get_available_features(self, df: pd.DataFrame) -> List[str]:
        """Get available feature columns from dataframe."""
        feature_cols = []

        for col in df.columns:
            if col.startswith("feat_"):
                if df[col].dtype in [np.float64, np.float32, np.int64, np.int32]:
                    feature_cols.append(col)

        return feature_cols

    def predict(self, features: Dict) -> Dict:
        """
        Predict optimal parameters for a new signal.

        Args:
            features: Dictionary with feature values

        Returns:
            Dictionary with predicted optimal SL, TP, Lifetime
        """
        if not self._sl_model:
            raise ValueError("Models not trained. Run pipeline first.")

        # Create feature array
        X = np.array([[features.get(col, 0) for col in self._feature_columns]])
        X_scaled = self._scaler.transform(X)

        return {
            "optimal_sl_pct": float(self._sl_model.predict(X_scaled)[0]),
            "optimal_tp_pct": float(self._tp_model.predict(X_scaled)[0]),
            "optimal_lifetime_hours": float(self._lifetime_model.predict(X_scaled)[0]),
        }

    def load_models(self, model_dir: str):
        """Load trained models from directory."""
        model_dir = Path(model_dir)

        with open(model_dir / "optimal_sl_model.pkl", "rb") as f:
            self._sl_model = pickle.load(f)

        with open(model_dir / "optimal_tp_model.pkl", "rb") as f:
            self._tp_model = pickle.load(f)

        with open(model_dir / "optimal_lifetime_model.pkl", "rb") as f:
            self._lifetime_model = pickle.load(f)

        with open(model_dir / "optimal_scaler.pkl", "rb") as f:
            self._scaler = pickle.load(f)

        with open(model_dir / "optimal_feature_columns.json", "r") as f:
            self._feature_columns = json.load(f)

        logger.info("models_loaded", model_dir=str(model_dir))
