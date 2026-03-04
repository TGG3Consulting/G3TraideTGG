# -*- coding: utf-8 -*-
"""
All Strategies Trainer

Trains ONE set of ML models on ALL strategies combined.
Use this when you want a single universal model.

For per-strategy models, use trainer_per_strategy.py instead.
"""

import pickle
import warnings
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    roc_auc_score,
    mean_absolute_error,
)

warnings.filterwarnings('ignore')

try:
    import lightgbm as lgb
    HAS_LIGHTGBM = True
except ImportError:
    HAS_LIGHTGBM = False

from .adapter import BacktestDataAdapter


class AllStrategiesTrainer:
    """
    Trains ML models on ALL strategies combined.

    Models:
    1. Signal Filter (binary: trade/skip)
    2. Confidence Scorer (probability of win)
    3. SL Regressor (optimal stop-loss %)
    4. TP Regressor (optimal take-profit %)
    """

    def __init__(self, model_dir: str = 'models/all_strategies'):
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)

        self.scaler: Optional[StandardScaler] = None
        self.filter_model = None
        self.confidence_model = None
        self.sl_model = None
        self.tp_model = None

        self.feature_names: List[str] = []
        self.metrics: Dict = {}

    def train(self, data_dir: str, test_size: float = 0.2) -> Dict[str, float]:
        """Train all models on combined data."""
        print("=" * 60)
        print("ALL STRATEGIES TRAINER")
        print("=" * 60)

        print("\n[1/5] Loading data...")
        adapter = BacktestDataAdapter(data_dir)
        X_df, y_df = adapter.load_all()

        X = X_df.values.astype(np.float32)
        self.feature_names = X_df.columns.tolist()

        y_win = y_df['target_win'].values
        y_direction = y_df['target_direction'].values
        y_sl = y_df['target_sl'].values
        y_tp = y_df['target_tp'].values

        print(f"  Total samples: {len(X)}")
        print(f"  Features: {len(self.feature_names)}")

        print("\n[2/5] Splitting data...")
        X_train, X_test, y_win_train, y_win_test = train_test_split(
            X, y_win, test_size=test_size, random_state=42, stratify=y_win
        )
        _, _, y_dir_train, y_dir_test = train_test_split(
            X, y_direction, test_size=test_size, random_state=42
        )
        _, _, y_sl_train, y_sl_test = train_test_split(
            X, y_sl, test_size=test_size, random_state=42
        )
        _, _, y_tp_train, y_tp_test = train_test_split(
            X, y_tp, test_size=test_size, random_state=42
        )
        print(f"  Train: {len(X_train)}, Test: {len(X_test)}")

        print("\n[3/5] Scaling features...")
        self.scaler = StandardScaler()
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)

        print("\n[4/5] Training models...")
        self.filter_model = self._train_classifier(
            X_train_scaled, y_dir_train, X_test_scaled, y_dir_test, "filter"
        )
        self.confidence_model = self._train_classifier(
            X_train_scaled, y_win_train, X_test_scaled, y_win_test, "confidence"
        )
        self.sl_model = self._train_regressor(
            X_train_scaled, y_sl_train, X_test_scaled, y_sl_test, "sl"
        )
        self.tp_model = self._train_regressor(
            X_train_scaled, y_tp_train, X_test_scaled, y_tp_test, "tp"
        )

        print("\n[5/5] Saving models...")
        self._save_models()

        print("\n" + "=" * 60)
        print("TRAINING COMPLETE")
        print("=" * 60)
        self._print_metrics()

        return self.metrics

    def _train_classifier(self, X_train, y_train, X_test, y_test, name):
        print(f"  Training {name}...")
        if HAS_LIGHTGBM:
            model = lgb.LGBMClassifier(
                n_estimators=200, max_depth=6, learning_rate=0.05,
                class_weight='balanced', random_state=42, verbose=-1, n_jobs=-1,
            )
        else:
            from sklearn.ensemble import GradientBoostingClassifier
            model = GradientBoostingClassifier(n_estimators=100, max_depth=5, random_state=42)

        model.fit(X_train, y_train)

        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]

        self.metrics[f'{name}_accuracy'] = accuracy_score(y_test, y_pred)
        self.metrics[f'{name}_precision'] = precision_score(y_test, y_pred, zero_division=0)
        self.metrics[f'{name}_recall'] = recall_score(y_test, y_pred, zero_division=0)
        self.metrics[f'{name}_f1'] = f1_score(y_test, y_pred, zero_division=0)
        self.metrics[f'{name}_auc'] = roc_auc_score(y_test, y_proba)

        return model

    def _train_regressor(self, X_train, y_train, X_test, y_test, name):
        print(f"  Training {name}...")
        if HAS_LIGHTGBM:
            model = lgb.LGBMRegressor(
                n_estimators=200, max_depth=6, learning_rate=0.05,
                random_state=42, verbose=-1, n_jobs=-1,
            )
        else:
            from sklearn.ensemble import GradientBoostingRegressor
            model = GradientBoostingRegressor(n_estimators=100, max_depth=5, random_state=42)

        model.fit(X_train, y_train)
        y_pred = model.predict(X_test)
        self.metrics[f'{name}_mae'] = mean_absolute_error(y_test, y_pred)

        return model

    def _save_models(self):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        for name, model in [('filter', self.filter_model), ('confidence', self.confidence_model),
                            ('sl', self.sl_model), ('tp', self.tp_model)]:
            if model:
                with open(self.model_dir / f'{name}_model_{timestamp}.pkl', 'wb') as f:
                    pickle.dump(model, f)
                import shutil
                shutil.copy(
                    self.model_dir / f'{name}_model_{timestamp}.pkl',
                    self.model_dir / f'{name}_model_latest.pkl'
                )

        with open(self.model_dir / f'scaler_{timestamp}.pkl', 'wb') as f:
            pickle.dump(self.scaler, f)
        with open(self.model_dir / f'features_{timestamp}.pkl', 'wb') as f:
            pickle.dump(self.feature_names, f)

        import shutil
        shutil.copy(self.model_dir / f'scaler_{timestamp}.pkl', self.model_dir / 'scaler_latest.pkl')
        shutil.copy(self.model_dir / f'features_{timestamp}.pkl', self.model_dir / 'features_latest.pkl')

        print(f"  Models saved to {self.model_dir}")

    def _print_metrics(self):
        print("\nMetrics:")
        print("-" * 40)
        print(f"Filter:     AUC={self.metrics.get('filter_auc', 0):.3f}, F1={self.metrics.get('filter_f1', 0):.3f}")
        print(f"Confidence: AUC={self.metrics.get('confidence_auc', 0):.3f}")
        print(f"SL MAE:     {self.metrics.get('sl_mae', 0):.3f}%")
        print(f"TP MAE:     {self.metrics.get('tp_mae', 0):.3f}%")


if __name__ == '__main__':
    import sys
    data_dir = sys.argv[1] if len(sys.argv) > 1 else 'outputNEWARCH'
    model_dir = sys.argv[2] if len(sys.argv) > 2 else 'models/all_strategies'

    trainer = AllStrategiesTrainer(model_dir=model_dir)
    trainer.train(data_dir)
