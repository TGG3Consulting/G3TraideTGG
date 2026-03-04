# -*- coding: utf-8 -*-
"""
Feature Importance Analysis - Single Feature Models

Tests each feature individually to measure its predictive power.
Does NOT modify existing code.

Usage:
    python -m ml.feature_importance --data-dir "outputNEWARCH/Обучение МЛ 24-26гг 68 монет  примерно outputNEWARCH"
"""

import argparse
import warnings
from pathlib import Path
from typing import List, Dict, Tuple

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, accuracy_score, mean_absolute_error
from lightgbm import LGBMClassifier, LGBMRegressor

warnings.filterwarnings('ignore')


# 27 Features to test (excluding static SL%, TP%, R:R Ratio)
FEATURES_TO_TEST = [
    # L/S (2)
    'Long %', 'Short %',
    # Market (3)
    'Funding Rate', 'OI USD', 'OI Contracts',
    # Price (1)
    'Open',
    # Candle Prev (8)
    'Prev High', 'Prev Low', 'Prev Close', 'Prev Volume',
    'Prev Volume USD', 'Prev Trades Count', 'Prev Taker Buy Vol', 'Prev Taker Buy USD',
    # Indicator (1)
    'ADX',
    # Chain (3)
    'Chain Seq', 'Gap Days', 'Chain First',
    # Signal (1)
    'Direction_num',
    # Time (3)
    'DayOfWeek', 'Month', 'Hour',
    # Derived (5)
    'LS_Extreme', 'L/S Ratio', 'Prev_Volatility', 'Prev_BuyPressure', 'Prev_CandleDir',
]

STRATEGIES = ['ls_fade', 'momentum', 'reversal', 'mean_reversion', 'momentum_ls']

LOG_FEATURES = ['OI USD', 'OI Contracts', 'Prev Volume', 'Prev Volume USD',
                'Prev Taker Buy Vol', 'Prev Taker Buy USD']


def load_data(data_dir: str) -> pd.DataFrame:
    """Load all Excel files into single DataFrame."""
    data_path = Path(data_dir)
    xlsx_files = list(data_path.glob('backtest_*.xlsx'))

    if not xlsx_files:
        raise FileNotFoundError(f"No Excel files in {data_dir}")

    print(f"Loading {len(xlsx_files)} files...")

    all_trades = []
    for f in xlsx_files:
        if f.name.startswith('~$'):
            continue
        try:
            df = pd.read_excel(f, sheet_name='Trades')
            # Extract strategy from filename
            if 'ls_fade' in f.stem:
                strategy = 'ls_fade'
            elif 'momentum_ls' in f.stem:
                strategy = 'momentum_ls'
            elif 'mean_reversion' in f.stem:
                strategy = 'mean_reversion'
            elif 'momentum' in f.stem:
                strategy = 'momentum'
            elif 'reversal' in f.stem:
                strategy = 'reversal'
            else:
                continue
            df['Strategy'] = strategy
            all_trades.append(df)
        except Exception:
            continue

    df = pd.concat(all_trades, ignore_index=True)
    df = df[df['Result'].isin(['WIN', 'LOSS'])].copy()

    print(f"Loaded {len(df)} trades")
    return df


def prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    """Prepare all features including derived ones."""
    features = pd.DataFrame(index=df.index)

    # Copy numeric columns
    numeric_cols = [
        'Long %', 'Short %', 'Funding Rate', 'OI USD', 'OI Contracts',
        'Open', 'Prev High', 'Prev Low', 'Prev Close', 'Prev Volume',
        'Prev Volume USD', 'Prev Trades Count', 'Prev Taker Buy Vol',
        'Prev Taker Buy USD', 'ADX', 'Chain Seq', 'Gap Days'
    ]

    for col in numeric_cols:
        if col in df.columns:
            values = pd.to_numeric(df[col], errors='coerce').fillna(0)
            if col in LOG_FEATURES:
                values = np.log1p(np.abs(values))
            features[col] = values

    # Chain First (bool)
    if 'Chain First' in df.columns:
        features['Chain First'] = df['Chain First'].astype(int)
    else:
        features['Chain First'] = 0

    # Direction
    features['Direction_num'] = df['Direction'].map({'LONG': 1, 'SHORT': -1}).fillna(0)

    # Time features
    if 'Signal Date' in df.columns:
        dates = pd.to_datetime(df['Signal Date'], errors='coerce')
        features['DayOfWeek'] = dates.dt.dayofweek.fillna(0)
        features['Month'] = dates.dt.month.fillna(1)
        features['Hour'] = dates.dt.hour.fillna(0)

    # Derived features
    if 'Long %' in df.columns:
        features['LS_Extreme'] = np.abs(df['Long %'].fillna(0.5) - 0.5)
        short_pct = df['Short %'].fillna(0.5).replace(0, 0.001)
        features['L/S Ratio'] = df['Long %'].fillna(0.5) / short_pct

    if 'Prev High' in df.columns and 'Prev Low' in df.columns:
        prev_close = df['Prev Close'].fillna(1).replace(0, 1)
        features['Prev_Volatility'] = ((df['Prev High'] - df['Prev Low']) / prev_close).fillna(0)

    if 'Prev Taker Buy USD' in df.columns and 'Prev Volume USD' in df.columns:
        prev_vol = df['Prev Volume USD'].fillna(1).replace(0, 1)
        features['Prev_BuyPressure'] = (df['Prev Taker Buy USD'].fillna(0) / prev_vol).fillna(0.5)

    if 'Prev Close' in df.columns and 'Prev Low' in df.columns:
        features['Prev_CandleDir'] = np.sign(df['Prev Close'].fillna(0) - df['Prev Low'].fillna(0))

    features = features.fillna(0)
    return features


def prepare_targets(df: pd.DataFrame) -> pd.DataFrame:
    """Prepare target variables."""
    targets = pd.DataFrame(index=df.index)

    # Filter: profitable = 1
    targets['target_filter'] = ((df['Net PnL %'] > 0) | (df['Result'] == 'WIN')).astype(int)

    # Confidence: WIN = 1
    targets['target_confidence'] = (df['Result'] == 'WIN').astype(int)

    # Direction: LONG correct = 1, SHORT correct = -1, Wrong = 0
    is_profitable = (df['Result'] == 'WIN') | (df['Net PnL %'] > 0)
    direction_map = df['Direction'].map({'LONG': 1, 'SHORT': -1}).fillna(0)
    targets['target_direction'] = np.where(is_profitable, direction_map, 0).astype(int)

    # Lifetime: hold days
    targets['target_lifetime'] = df['Hold Days'].fillna(1)

    return targets


def train_single_feature(
    X_train: np.ndarray,
    X_test: np.ndarray,
    y_train: np.ndarray,
    y_test: np.ndarray,
    model_type: str,
) -> float:
    """Train model on single feature and return metric."""

    # Reshape if needed
    if len(X_train.shape) == 1:
        X_train = X_train.reshape(-1, 1)
        X_test = X_test.reshape(-1, 1)

    # Scale
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    try:
        if model_type in ['filter', 'confidence']:
            # Binary classifier - AUC
            model = LGBMClassifier(n_estimators=50, max_depth=3, verbose=-1, n_jobs=1)
            model.fit(X_train_scaled, y_train)
            proba = model.predict_proba(X_test_scaled)[:, 1]
            return roc_auc_score(y_test, proba)

        elif model_type == 'direction':
            # Multiclass classifier - Accuracy
            model = LGBMClassifier(n_estimators=50, max_depth=3, verbose=-1, n_jobs=1)
            model.fit(X_train_scaled, y_train)
            pred = model.predict(X_test_scaled)
            return accuracy_score(y_test, pred)

        elif model_type == 'lifetime':
            # Regressor - MAE
            model = LGBMRegressor(n_estimators=50, max_depth=3, verbose=-1, n_jobs=1)
            model.fit(X_train_scaled, y_train)
            pred = model.predict(X_test_scaled)
            return mean_absolute_error(y_test, pred)

    except Exception:
        return 0.5 if model_type in ['filter', 'confidence'] else (0.33 if model_type == 'direction' else 5.0)


def run_analysis(data_dir: str, output_csv: str = None):
    """Run full feature importance analysis."""

    # Load data
    df = load_data(data_dir)

    # Prepare features and targets
    all_features = prepare_features(df)
    all_targets = prepare_targets(df)
    df['_strategy'] = df['Strategy']

    results = []
    total = len(FEATURES_TO_TEST) * len(STRATEGIES)
    current = 0

    print(f"\nAnalyzing {len(FEATURES_TO_TEST)} features × {len(STRATEGIES)} strategies = {total} combinations\n")
    print("-" * 90)
    print(f"{'Feature':<20} {'Strategy':<15} {'Filter':<10} {'Conf':<10} {'Dir':<10} {'Life':<10}")
    print("-" * 90)

    for feature in FEATURES_TO_TEST:
        if feature not in all_features.columns:
            print(f"[SKIP] {feature} not found")
            continue

        for strategy in STRATEGIES:
            current += 1

            # Filter by strategy
            mask = df['_strategy'] == strategy
            if mask.sum() < 100:
                continue

            X = all_features.loc[mask, feature].values

            # Skip if no variance
            if np.std(X) < 1e-6:
                results.append({
                    'feature': feature,
                    'strategy': strategy,
                    'filter_auc': 0.50,
                    'conf_auc': 0.50,
                    'dir_acc': 0.33,
                    'life_mae': 5.0,
                })
                continue

            y_filter = all_targets.loc[mask, 'target_filter'].values
            y_conf = all_targets.loc[mask, 'target_confidence'].values
            y_dir = all_targets.loc[mask, 'target_direction'].values
            y_life = all_targets.loc[mask, 'target_lifetime'].values

            # Train/test split
            X_tr, X_te, yf_tr, yf_te, yc_tr, yc_te, yd_tr, yd_te, yl_tr, yl_te = train_test_split(
                X, y_filter, y_conf, y_dir, y_life,
                test_size=0.2, random_state=42
            )

            # Train 4 models
            filter_auc = train_single_feature(X_tr, X_te, yf_tr, yf_te, 'filter')
            conf_auc = train_single_feature(X_tr, X_te, yc_tr, yc_te, 'confidence')
            dir_acc = train_single_feature(X_tr, X_te, yd_tr, yd_te, 'direction')
            life_mae = train_single_feature(X_tr, X_te, yl_tr, yl_te, 'lifetime')

            results.append({
                'feature': feature,
                'strategy': strategy,
                'filter_auc': filter_auc,
                'conf_auc': conf_auc,
                'dir_acc': dir_acc,
                'life_mae': life_mae,
            })

            print(f"{feature:<20} {strategy:<15} {filter_auc:<10.3f} {conf_auc:<10.3f} {dir_acc:<10.1%} {life_mae:<10.2f}")

    print("-" * 90)

    # Create results DataFrame
    results_df = pd.DataFrame(results)

    # Summary: average across strategies
    print("\n" + "=" * 70)
    print("SUMMARY: Average across all strategies (sorted by Filter AUC)")
    print("=" * 70)

    summary = results_df.groupby('feature').agg({
        'filter_auc': 'mean',
        'conf_auc': 'mean',
        'dir_acc': 'mean',
        'life_mae': 'mean',
    }).round(3)

    summary = summary.sort_values('filter_auc', ascending=False)

    print(f"\n{'Rank':<5} {'Feature':<20} {'Filter':<10} {'Conf':<10} {'Dir':<10} {'Life':<10}")
    print("-" * 65)
    for rank, (feature, row) in enumerate(summary.iterrows(), 1):
        print(f"{rank:<5} {feature:<20} {row['filter_auc']:<10.3f} {row['conf_auc']:<10.3f} {row['dir_acc']:<10.1%} {row['life_mae']:<10.2f}")

    # Save to CSV
    if output_csv:
        results_df.to_csv(output_csv, index=False)
        print(f"\nResults saved to: {output_csv}")

    return results_df


def main():
    parser = argparse.ArgumentParser(description='Feature Importance Analysis')
    parser.add_argument('--data-dir', type=str, required=True, help='Directory with Excel files')
    parser.add_argument('--output', type=str, default='feature_importance.csv', help='Output CSV file')
    args = parser.parse_args()

    run_analysis(args.data_dir, args.output)


if __name__ == '__main__':
    main()
