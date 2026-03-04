# -*- coding: utf-8 -*-
"""
Per-Strategy Trainer

Trains SEPARATE ML models for EACH strategy.
Each strategy gets its own models tuned to its specific patterns.

Output structure:
    models/
    ├── ls_fade/
    │   ├── filter_model_latest.pkl
    │   ├── confidence_model_latest.pkl
    │   └── ...
    ├── momentum/
    │   └── ...
    └── ...
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


class PerStrategyTrainer:
    """
    Trains separate ML models for each strategy.

    Each strategy gets 6 models:
    1. Filter (binary: should trade or skip)
    2. Confidence (probability of win)
    3. Direction (multiclass: LONG=1, SHORT=-1, SKIP=0)
    4. SL Regressor (optimal stop-loss %)
    5. TP Regressor (optimal take-profit %)
    6. Lifetime Regressor (expected hold days)
    """

    def __init__(self, base_model_dir: str = 'models'):
        self.base_model_dir = Path(base_model_dir)
        self.all_metrics: Dict[str, Dict] = {}

    def train_all(self, data_dir: str, test_size: float = 0.2) -> Dict[str, Dict]:
        """
        Train models for all strategies found in data.

        Args:
            data_dir: Directory with Excel backtest files
            test_size: Fraction for test set

        Returns:
            Dict mapping strategy name to metrics
        """
        print("=" * 70)
        print("PER-STRATEGY TRAINER")
        print("=" * 70)

        adapter = BacktestDataAdapter(data_dir)
        adapter._load_excel_files()

        strategies = adapter.get_strategies()
        print(f"\nFound {len(strategies)} strategies: {strategies}")

        stats = adapter.get_strategy_stats()
        print("\nStrategy Stats:")
        print("-" * 50)
        for strat, s in stats.items():
            print(f"  {strat}: {s['total']} trades, {s['win_rate']:.1f}% WR, {s['symbols']} symbols")

        print("\n" + "=" * 70)

        for strategy in strategies:
            print(f"\n>>> TRAINING: {strategy.upper()}")
            print("-" * 50)

            try:
                metrics = self.train_strategy(adapter, strategy, test_size)
                self.all_metrics[strategy] = metrics
            except Exception as e:
                print(f"  ERROR: {e}")
                self.all_metrics[strategy] = {'error': str(e)}

        print("\n" + "=" * 70)
        print("ALL STRATEGIES COMPLETE")
        print("=" * 70)
        self._print_summary()

        return self.all_metrics

    def train_strategy(
        self,
        adapter: BacktestDataAdapter,
        strategy: str,
        test_size: float = 0.2,
    ) -> Dict[str, float]:
        """Train all 6 models for a single strategy."""

        # Load strategy-specific data
        X_df, y_df = adapter.load_strategy(strategy)

        if len(X_df) < 100:
            raise ValueError(f"Not enough data: {len(X_df)} samples (need 100+)")

        X = X_df.values.astype(np.float32)
        feature_names = X_df.columns.tolist()

        # All 6 targets
        y_filter = y_df['target_filter'].values       # Binary: should trade
        y_win = y_df['target_win'].values             # Binary: will win
        y_direction = y_df['target_direction'].values # Multiclass: 1=LONG, -1=SHORT, 0=SKIP
        y_sl = y_df['target_sl'].values               # Regression: SL %
        y_tp = y_df['target_tp'].values               # Regression: TP %
        y_lifetime = y_df['target_lifetime'].values   # Regression: hold days

        print(f"  Samples: {len(X)}, Features: {len(feature_names)}")

        # Split (same random_state for all to keep alignment)
        X_train, X_test, y_filter_train, y_filter_test = train_test_split(
            X, y_filter, test_size=test_size, random_state=42, stratify=y_filter
        )
        _, _, y_win_train, y_win_test = train_test_split(
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
        _, _, y_life_train, y_life_test = train_test_split(
            X, y_lifetime, test_size=test_size, random_state=42
        )

        # Scale
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)

        metrics = {}

        # Train all 6 models
        print("  Training models...")

        # 1. Filter (binary classifier)
        filter_model = self._train_classifier(
            X_train_scaled, y_filter_train, X_test_scaled, y_filter_test, "filter", metrics
        )

        # 2. Confidence (binary classifier)
        confidence_model = self._train_classifier(
            X_train_scaled, y_win_train, X_test_scaled, y_win_test, "confidence", metrics
        )

        # 3. Direction (multiclass classifier: -1, 0, 1)
        direction_model = self._train_multiclass_classifier(
            X_train_scaled, y_dir_train, X_test_scaled, y_dir_test, "direction", metrics
        )

        # 4. SL Regressor
        sl_model = self._train_regressor(
            X_train_scaled, y_sl_train, X_test_scaled, y_sl_test, "sl", metrics
        )

        # 5. TP Regressor
        tp_model = self._train_regressor(
            X_train_scaled, y_tp_train, X_test_scaled, y_tp_test, "tp", metrics
        )

        # 6. Lifetime Regressor
        lifetime_model = self._train_regressor(
            X_train_scaled, y_life_train, X_test_scaled, y_life_test, "lifetime", metrics
        )

        # Save all 6 models
        model_dir = self.base_model_dir / strategy
        model_dir.mkdir(parents=True, exist_ok=True)

        self._save_models(
            model_dir,
            {
                'filter': filter_model,
                'confidence': confidence_model,
                'direction': direction_model,
                'sl': sl_model,
                'tp': tp_model,
                'lifetime': lifetime_model,
            },
            scaler,
            feature_names,
            metrics
        )

        # Print metrics for this strategy
        self._print_strategy_metrics(strategy, metrics)

        return metrics

    def _train_multiclass_classifier(self, X_train, y_train, X_test, y_test, name, metrics):
        """Train multiclass classifier for direction prediction (-1, 0, 1)."""
        if HAS_LIGHTGBM:
            model = lgb.LGBMClassifier(
                n_estimators=150, max_depth=5, learning_rate=0.05,
                class_weight='balanced', random_state=42, verbose=-1, n_jobs=-1,
                objective='multiclass', num_class=3,
            )
        else:
            from sklearn.ensemble import GradientBoostingClassifier
            model = GradientBoostingClassifier(n_estimators=100, max_depth=4, random_state=42)

        model.fit(X_train, y_train)

        y_pred = model.predict(X_test)

        metrics[f'{name}_accuracy'] = accuracy_score(y_test, y_pred)
        metrics[f'{name}_f1_macro'] = f1_score(y_test, y_pred, average='macro', zero_division=0)

        return model

    def _print_strategy_metrics(self, strategy: str, metrics: Dict):
        """Print metrics for a single strategy."""
        print(f"\n  {'Model':<15} {'Metric':<15} {'Value':>10}")
        print(f"  {'-'*40}")

        # Classifiers
        print(f"  {'Filter':<15} {'AUC':<15} {metrics.get('filter_auc', 0):>10.3f}")
        print(f"  {'Confidence':<15} {'AUC':<15} {metrics.get('confidence_auc', 0):>10.3f}")
        print(f"  {'Direction':<15} {'Accuracy':<15} {metrics.get('direction_accuracy', 0):>10.3f}")
        print(f"  {'Direction':<15} {'F1 (macro)':<15} {metrics.get('direction_f1_macro', 0):>10.3f}")

        # Regressors
        print(f"  {'SL':<15} {'MAE':<15} {metrics.get('sl_mae', 0):>10.3f}")
        print(f"  {'TP':<15} {'MAE':<15} {metrics.get('tp_mae', 0):>10.3f}")
        print(f"  {'Lifetime':<15} {'MAE':<15} {metrics.get('lifetime_mae', 0):>10.3f}")

    def _train_classifier(self, X_train, y_train, X_test, y_test, name, metrics):
        if HAS_LIGHTGBM:
            model = lgb.LGBMClassifier(
                n_estimators=150, max_depth=5, learning_rate=0.05,
                class_weight='balanced', random_state=42, verbose=-1, n_jobs=-1,
            )
        else:
            from sklearn.ensemble import GradientBoostingClassifier
            model = GradientBoostingClassifier(n_estimators=100, max_depth=4, random_state=42)

        model.fit(X_train, y_train)

        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]

        metrics[f'{name}_accuracy'] = accuracy_score(y_test, y_pred)
        metrics[f'{name}_precision'] = precision_score(y_test, y_pred, zero_division=0)
        metrics[f'{name}_recall'] = recall_score(y_test, y_pred, zero_division=0)
        metrics[f'{name}_f1'] = f1_score(y_test, y_pred, zero_division=0)
        metrics[f'{name}_auc'] = roc_auc_score(y_test, y_proba)

        return model

    def _train_regressor(self, X_train, y_train, X_test, y_test, name, metrics):
        if HAS_LIGHTGBM:
            model = lgb.LGBMRegressor(
                n_estimators=150, max_depth=5, learning_rate=0.05,
                random_state=42, verbose=-1, n_jobs=-1,
            )
        else:
            from sklearn.ensemble import GradientBoostingRegressor
            model = GradientBoostingRegressor(n_estimators=100, max_depth=4, random_state=42)

        model.fit(X_train, y_train)
        y_pred = model.predict(X_test)
        metrics[f'{name}_mae'] = mean_absolute_error(y_test, y_pred)

        return model

    def _save_models(self, model_dir, models: Dict, scaler, feature_names, metrics: Dict):
        """Save all 6 models, scaler, features, and metrics."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        import shutil

        # Save all 6 models
        for name, model in models.items():
            path = model_dir / f'{name}_model_{timestamp}.pkl'
            with open(path, 'wb') as f:
                pickle.dump(model, f)
            shutil.copy(path, model_dir / f'{name}_model_latest.pkl')

        # Scaler
        scaler_path = model_dir / f'scaler_{timestamp}.pkl'
        with open(scaler_path, 'wb') as f:
            pickle.dump(scaler, f)
        shutil.copy(scaler_path, model_dir / 'scaler_latest.pkl')

        # Feature names
        features_path = model_dir / f'features_{timestamp}.pkl'
        with open(features_path, 'wb') as f:
            pickle.dump(feature_names, f)
        shutil.copy(features_path, model_dir / 'features_latest.pkl')

        # Metrics
        metrics_path = model_dir / f'metrics_{timestamp}.pkl'
        with open(metrics_path, 'wb') as f:
            pickle.dump(metrics, f)
        shutil.copy(metrics_path, model_dir / 'metrics_latest.pkl')

        print(f"  Saved 6 models to {model_dir}")

    def _print_summary(self):
        print("\n" + "=" * 80)
        print("SUMMARY - ALL STRATEGIES")
        print("=" * 80)

        # Header
        print(f"\n{'Strategy':<16} | {'Filter':>8} {'Conf':>8} {'Dir':>8} | {'SL':>8} {'TP':>8} {'Life':>8}")
        print(f"{'':16} | {'AUC':>8} {'AUC':>8} {'Acc':>8} | {'MAE':>8} {'MAE':>8} {'MAE':>8}")
        print("-" * 80)

        for strategy, metrics in self.all_metrics.items():
            if 'error' in metrics:
                print(f"{strategy:<16} | ERROR: {metrics['error']}")
            else:
                filt = metrics.get('filter_auc', 0)
                conf = metrics.get('confidence_auc', 0)
                dir_acc = metrics.get('direction_accuracy', 0)
                sl = metrics.get('sl_mae', 0)
                tp = metrics.get('tp_mae', 0)
                life = metrics.get('lifetime_mae', 0)
                print(f"{strategy:<16} | {filt:>8.3f} {conf:>8.3f} {dir_acc:>8.3f} | {sl:>8.2f} {tp:>8.2f} {life:>8.2f}")

        print("-" * 80)
        print("\nClassifiers: Higher AUC/Accuracy = Better (max 1.0)")
        print("Regressors:  Lower MAE = Better (in % or days)")


def train_per_strategy(
    data_dir: str,
    model_dir: str = 'models',
    strategy: Optional[str] = None,
) -> Dict[str, Dict]:
    """
    Convenience function to train per-strategy models.

    Args:
        data_dir: Directory with Excel backtest files
        model_dir: Base directory for models
        strategy: Specific strategy to train (None = all)

    Returns:
        Dict mapping strategy name to metrics
    """
    trainer = PerStrategyTrainer(base_model_dir=model_dir)

    if strategy:
        # Train single strategy
        adapter = BacktestDataAdapter(data_dir)
        adapter._load_excel_files()
        metrics = trainer.train_strategy(adapter, strategy)
        return {strategy: metrics}
    else:
        # Train all strategies
        return trainer.train_all(data_dir)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Train ML models per strategy')
    parser.add_argument('--data-dir', type=str, default='outputNEWARCH',
                        help='Directory with Excel backtest files')
    parser.add_argument('--model-dir', type=str, default='models',
                        help='Directory to save models')
    parser.add_argument('--strategy', type=str, default=None,
                        help='Train specific strategy only (ls_fade, momentum, reversal, mean_reversion, momentum_ls)')

    args = parser.parse_args()

    print("=" * 70)
    if args.strategy:
        print(f"TRAINING: {args.strategy.upper()}")
    else:
        print("TRAINING: ALL STRATEGIES")
    print("=" * 70)

    train_per_strategy(args.data_dir, args.model_dir, args.strategy)
