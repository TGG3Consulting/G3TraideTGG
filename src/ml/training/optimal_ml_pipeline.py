# -*- coding: utf-8 -*-
"""
Optimal ML Pipeline - полный пайплайн обучения.

Обучает РЕГРЕССОРЫ для предсказания оптимальных SL/TP/Lifetime.

Процесс:
1. Загрузить данные (klines, OI, funding) через MLDataLoader
2. Для каждого сигнала рассчитать optimal params из реальной истории цен
3. Извлечь features (условия рынка в момент сигнала)
4. Обучить 3 регрессора: SL, TP, Lifetime
5. Сохранить модели

Usage:
    python -m src.ml.training.optimal_ml_pipeline
"""

import asyncio
import json
import pickle
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import structlog

from src.ml.data.ml_data_loader import MLDataLoader, SymbolData
from src.ml.training.optimal_params_calculator import OptimalParamsCalculator, OptimalParams
from backtester.models import Kline, MLFeatures
from backtester.log_parser import LogParser
from backtester.config import BacktestConfig
from dataclasses import fields


logger = structlog.get_logger(__name__)


# All MLFeatures field names (116 fields)
ML_FEATURE_NAMES = [f.name for f in fields(MLFeatures) if not f.name.startswith('_')]


@dataclass
class TrainingConfig:
    """Configuration for training pipeline."""
    # Paths
    signals_path: str = "logs/signals.jsonl"
    models_dir: str = "models/optimal"

    # Training
    test_size: float = 0.2
    random_state: int = 42

    # Data limits (for testing)
    signal_limit: Optional[int] = None

    # Model params
    n_estimators: int = 100
    max_depth: int = 5


class OptimalMLPipeline:
    """
    Полный пайплайн обучения ML для оптимальных параметров.
    """

    def __init__(self, config: Optional[TrainingConfig] = None):
        self._config = config or TrainingConfig()
        self._data_loader = MLDataLoader()
        self._calculator = OptimalParamsCalculator()

        # Models
        self._sl_model = None
        self._tp_model = None
        self._lifetime_model = None
        self._scaler = None
        self._feature_columns = []

        logger.info("optimal_ml_pipeline_init")

    async def run(self) -> Dict:
        """
        Run full training pipeline.

        Returns:
            Dictionary with training results
        """
        start_time = datetime.now()
        result = {
            "status": "running",
            "start_time": start_time.isoformat(),
            "steps": {},
        }

        try:
            # Step 1: Load all data
            logger.info("step_1_loading_data")
            symbol_data = await self._data_loader.load_for_signals(
                self._config.signals_path,
                limit=self._config.signal_limit,
            )

            result["steps"]["data_loading"] = {
                "symbols_loaded": len(symbol_data),
                "cache_stats": self._data_loader.get_cache_stats(),
                "status": "complete",
            }

            # Step 2: Create training data
            logger.info("step_2_creating_training_data")
            df = self._create_training_data(symbol_data)

            result["steps"]["training_data"] = {
                "total_samples": len(df),
                "would_be_profitable": int(df["label_would_be_profitable"].sum()) if "label_would_be_profitable" in df.columns else 0,
                "status": "complete",
            }

            if len(df) < 100:
                raise ValueError(f"Not enough training data: {len(df)} < 100")

            # Step 3: Temporal split
            logger.info("step_3_splitting_data")
            df = df.sort_values("timestamp").reset_index(drop=True)

            split_idx = int(len(df) * (1 - self._config.test_size))
            train_df = df.iloc[:split_idx].copy()
            test_df = df.iloc[split_idx:].copy()

            result["steps"]["split"] = {
                "train_samples": len(train_df),
                "test_samples": len(test_df),
                "status": "complete",
            }

            # Step 4: Prepare features
            logger.info("step_4_preparing_features")
            feature_cols = self._get_feature_columns(train_df)
            self._feature_columns = feature_cols

            X_train = train_df[feature_cols].fillna(0).values.astype(np.float32)
            X_test = test_df[feature_cols].fillna(0).values.astype(np.float32)

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

            # Step 5: Scale features
            logger.info("step_5_scaling")
            from sklearn.preprocessing import StandardScaler

            self._scaler = StandardScaler()
            X_train_scaled = self._scaler.fit_transform(X_train)
            X_test_scaled = self._scaler.transform(X_test)

            # Step 6: Train models
            logger.info("step_6_training_models")
            from sklearn.ensemble import GradientBoostingRegressor

            self._sl_model = GradientBoostingRegressor(
                n_estimators=self._config.n_estimators,
                max_depth=self._config.max_depth,
                random_state=self._config.random_state,
            )
            self._sl_model.fit(X_train_scaled, y_train_sl)

            self._tp_model = GradientBoostingRegressor(
                n_estimators=self._config.n_estimators,
                max_depth=self._config.max_depth,
                random_state=self._config.random_state,
            )
            self._tp_model.fit(X_train_scaled, y_train_tp)

            self._lifetime_model = GradientBoostingRegressor(
                n_estimators=self._config.n_estimators,
                max_depth=self._config.max_depth,
                random_state=self._config.random_state,
            )
            self._lifetime_model.fit(X_train_scaled, y_train_lifetime)

            result["steps"]["training"] = {
                "models": ["sl", "tp", "lifetime"],
                "status": "complete",
            }

            # Step 7: Evaluate
            logger.info("step_7_evaluating")
            from sklearn.metrics import mean_absolute_error, r2_score

            pred_sl_test = self._sl_model.predict(X_test_scaled)
            pred_tp_test = self._tp_model.predict(X_test_scaled)
            pred_lifetime_test = self._lifetime_model.predict(X_test_scaled)

            result["steps"]["evaluation"] = {
                "sl_model": {
                    "test_mae": float(mean_absolute_error(y_test_sl, pred_sl_test)),
                    "test_r2": float(r2_score(y_test_sl, pred_sl_test)),
                },
                "tp_model": {
                    "test_mae": float(mean_absolute_error(y_test_tp, pred_tp_test)),
                    "test_r2": float(r2_score(y_test_tp, pred_tp_test)),
                },
                "lifetime_model": {
                    "test_mae": float(mean_absolute_error(y_test_lifetime, pred_lifetime_test)),
                    "test_r2": float(r2_score(y_test_lifetime, pred_lifetime_test)),
                },
                "status": "complete",
            }

            # Step 8: Save models
            logger.info("step_8_saving_models")
            model_dir = Path(self._config.models_dir)
            model_dir.mkdir(parents=True, exist_ok=True)

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
            result["end_time"] = datetime.now().isoformat()
            result["duration_sec"] = (datetime.now() - start_time).total_seconds()

            logger.info(
                "optimal_ml_pipeline_complete",
                status="success",
                duration=result["duration_sec"],
            )

        except Exception as e:
            logger.error("optimal_ml_pipeline_error", error=str(e))
            result["status"] = "error"
            result["error"] = str(e)
            raise

        return result

    def _create_training_data(self, symbol_data: Dict[str, SymbolData]) -> pd.DataFrame:
        """
        Create training DataFrame from loaded data.
        Uses LogParser to extract ALL MLFeatures (116 fields).
        """
        rows = []
        signals_path = self._config.signals_path

        # Use LogParser to get full MLFeatures
        logger.info("parsing_signals_with_log_parser", path=signals_path)

        config = BacktestConfig(signals_file=Path(signals_path), verbose=False)
        parser = LogParser(config)
        parsed_signals = parser.parse_all_signals()

        logger.info("parsed_signals", count=len(parsed_signals))

        # Create lookup by signal_id for MLFeatures
        ml_features_lookup = {s.signal_id: s.ml_features for s in parsed_signals}

        with open(signals_path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    json_data = json.loads(line.strip())
                    # Сигналы вложены в объект "signal"
                    signal = json_data.get("signal", json_data)
                    symbol = signal.get("symbol")
                    signal_id = signal.get("signal_id", "")

                    if symbol not in symbol_data:
                        continue

                    sym_data = symbol_data[symbol]

                    # Parse signal timestamp
                    ts_str = signal.get("timestamp")
                    signal_ts = self._parse_timestamp(ts_str)
                    if not signal_ts:
                        continue

                    # Get entry price
                    entry_price = self._get_entry_price(signal)
                    if not entry_price:
                        continue

                    # Get direction
                    direction = signal.get("direction", "LONG")

                    # Filter klines AFTER entry
                    klines_after = [
                        k for k in sym_data.klines
                        if k.timestamp >= signal_ts
                    ]

                    if len(klines_after) < 60:  # Need at least 1 hour
                        continue

                    # Calculate optimal params
                    optimal = self._calculator.calculate(
                        entry_price=entry_price,
                        direction=direction,
                        klines_after_entry=klines_after,
                    )

                    if not optimal:
                        continue

                    # Get MLFeatures from parser
                    ml_features = ml_features_lookup.get(signal_id)

                    # Create row with ALL features and labels
                    row = self._create_row(signal, signal_ts, sym_data, optimal, ml_features)
                    rows.append(row)

                except Exception as e:
                    continue

        df = pd.DataFrame(rows)

        logger.info(
            "training_data_created",
            rows=len(df),
            feature_columns=len([c for c in df.columns if c.startswith('feat_')]),
        )

        return df

    def _create_row(
        self,
        signal: Dict,
        signal_ts: datetime,
        data: SymbolData,
        optimal: OptimalParams,
        ml_features: Optional[MLFeatures] = None,
    ) -> Dict:
        """Create a training row with ALL MLFeatures fields and labels."""
        row = {
            # Identification
            "signal_id": signal.get("signal_id", ""),
            "symbol": signal.get("symbol"),
            "timestamp": signal_ts,
            "direction": signal.get("direction", "LONG"),
        }

        # ===== ALL MLFeatures (116 fields) =====
        if ml_features:
            # Extract all fields from MLFeatures dataclass
            for field_name in ML_FEATURE_NAMES:
                value = getattr(ml_features, field_name, None)
                # Convert to numeric for ML (skip string fields for numeric model)
                if isinstance(value, str):
                    # Convert string to numeric where possible
                    if field_name in ['trigger_type']:
                        row[f"feat_{field_name}"] = self._encode_trigger_type(value)
                    elif field_name in ['scenario_bullish', 'scenario_bearish', 'evidence_text',
                                        'logged_at', 'futures_last_update', 'spot_last_update',
                                        'oi_timestamp', 'funding_time', 'ls_ratio_timestamp']:
                        # Skip text/timestamp fields for numeric ML model
                        continue
                    else:
                        row[f"feat_{field_name}"] = 0
                elif value is None:
                    row[f"feat_{field_name}"] = 0
                else:
                    row[f"feat_{field_name}"] = float(value) if not isinstance(value, int) else value
        else:
            # Fallback: extract from raw signal dict (legacy mode)
            self._extract_features_from_dict(row, signal, data, signal_ts)

        # ===== LABELS (from optimal params) =====
        row["label_optimal_sl_pct"] = optimal.optimal_sl_pct
        row["label_optimal_tp_pct"] = optimal.optimal_tp_pct
        row["label_optimal_lifetime_hours"] = optimal.optimal_lifetime_hours
        row["label_max_profit_pct"] = optimal.max_profit_pct
        row["label_max_drawdown_pct"] = optimal.max_drawdown_pct
        row["label_would_be_profitable"] = optimal.would_be_profitable

        return row

    def _encode_trigger_type(self, trigger_type: str) -> int:
        """Encode trigger type to numeric."""
        mapping = {
            "": 0,
            "VOLUME_SPIKE": 1,
            "OI_SURGE": 2,
            "FUNDING_EXTREME": 3,
            "LS_RATIO_EXTREME": 4,
            "PRICE_BREAKOUT": 5,
            "ORDERBOOK_IMBALANCE": 6,
            "COORDINATED_BUYING": 7,
            "COORDINATED_SELLING": 8,
        }
        return mapping.get(trigger_type.upper() if trigger_type else "", 0)

    def _extract_features_from_dict(
        self,
        row: Dict,
        signal: Dict,
        data: SymbolData,
        signal_ts: datetime,
    ) -> None:
        """Legacy: Extract features from raw signal dict."""
        # Basic signal features
        row["feat_probability"] = signal.get("probability", 65)
        row["feat_risk_reward"] = signal.get("risk_reward", 2.0)
        row["feat_stop_loss_pct"] = signal.get("stop_loss_pct", 5.0)
        row["feat_confidence"] = self._confidence_to_num(signal.get("confidence", "MEDIUM"))
        row["feat_direction_num"] = 1 if signal.get("direction") == "LONG" else 0

        # TP levels
        take_profits = signal.get("take_profits", [])
        if take_profits:
            row["feat_tp1_pct"] = take_profits[0].get("percent", 0) if len(take_profits) > 0 else 0
            row["feat_tp2_pct"] = take_profits[1].get("percent", 0) if len(take_profits) > 1 else 0
            row["feat_tp3_pct"] = take_profits[2].get("percent", 0) if len(take_profits) > 2 else 0

        # Details
        details = signal.get("details", {}) or {}
        row["feat_oi_change_1h"] = self._parse_pct(details.get("oi_change_1h", "0"))
        row["feat_oi_change_5m"] = self._parse_pct(details.get("oi_change_5m", "0"))
        row["feat_funding"] = self._parse_pct(details.get("funding", "0"))
        row["feat_volume_ratio"] = self._parse_float(details.get("volume_ratio", "1.0"))

        # Accumulation score
        acc_score = signal.get("accumulation_score", {}) or {}
        row["feat_score_total"] = acc_score.get("total", 0)

        # Market conditions
        oi_at_signal = self._find_oi_at_time(data.oi_history, signal_ts)
        if oi_at_signal:
            row["feat_oi_value"] = float(oi_at_signal.open_interest_value)

        funding_at_signal = self._find_funding_at_time(data.funding_history, signal_ts)
        if funding_at_signal:
            row["feat_funding_rate"] = float(funding_at_signal.funding_rate) * 100

    def _get_entry_price(self, signal: Dict) -> Optional[float]:
        """Get entry price from signal."""
        # Try entry_limit first
        entry = signal.get("entry_limit")
        if entry:
            return float(entry)

        # Try entry_zone
        entry_zone = signal.get("entry_zone_low")
        if entry_zone:
            return float(entry_zone)

        # Try current_price
        current = signal.get("current_price")
        if current:
            return float(current)

        return None

    def _parse_timestamp(self, ts_str: str) -> Optional[datetime]:
        """Parse timestamp string."""
        if not ts_str:
            return None
        try:
            if 'T' in ts_str:
                ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
            else:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts
        except:
            return None

    def _confidence_to_num(self, conf: str) -> int:
        """Convert confidence to numeric."""
        mapping = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4}
        return mapping.get(conf, 2)

    def _parse_pct(self, value: str) -> float:
        """Parse percentage string."""
        if not value:
            return 0.0
        try:
            return float(str(value).replace("%", "").replace("+", "").strip())
        except:
            return 0.0

    def _parse_float(self, value: str) -> float:
        """Parse float string."""
        if not value:
            return 0.0
        try:
            return float(str(value).replace("x", "").strip())
        except:
            return 0.0

    def _find_oi_at_time(self, oi_history, target_time: datetime):
        """Find OI data closest to target time."""
        if not oi_history:
            return None
        closest = min(oi_history, key=lambda x: abs((x.timestamp - target_time).total_seconds()))
        return closest

    def _find_funding_at_time(self, funding_history, target_time: datetime):
        """Find funding data closest to target time."""
        if not funding_history:
            return None
        closest = min(funding_history, key=lambda x: abs((x.timestamp - target_time).total_seconds()))
        return closest

    def _get_feature_columns(self, df: pd.DataFrame) -> List[str]:
        """Get feature columns."""
        return [col for col in df.columns if col.startswith("feat_")]

    def predict(self, features: Dict) -> Dict:
        """
        Predict optimal parameters for a new signal.
        """
        if not self._sl_model:
            raise ValueError("Models not trained")

        X = np.array([[features.get(col, 0) for col in self._feature_columns]])
        X_scaled = self._scaler.transform(X)

        return {
            "optimal_sl_pct": float(self._sl_model.predict(X_scaled)[0]),
            "optimal_tp_pct": float(self._tp_model.predict(X_scaled)[0]),
            "optimal_lifetime_hours": float(self._lifetime_model.predict(X_scaled)[0]),
        }

    def load_models(self, model_dir: str) -> None:
        """Load trained models."""
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


async def main():
    """Run training pipeline."""
    import sys

    # Parse args
    config = TrainingConfig()

    # Check for limit arg
    for arg in sys.argv[1:]:
        if arg.startswith("--limit="):
            config.signal_limit = int(arg.split("=")[1])
        elif arg.startswith("--signals="):
            config.signals_path = arg.split("=")[1]

    # Run pipeline
    pipeline = OptimalMLPipeline(config)
    result = await pipeline.run()

    # Print result
    print("\n" + "=" * 60)
    print("TRAINING RESULT")
    print("=" * 60)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
