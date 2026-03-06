# -*- coding: utf-8 -*-
"""
ML Signal Filter

Applies trained ML models to filter and enhance trading signals.
Supports both all-strategies and per-strategy models.
"""

import pickle
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any

import numpy as np

warnings.filterwarnings('ignore')


@dataclass
class MLPrediction:
    """Result of ML signal enhancement (6 models)."""
    should_trade: bool
    confidence: float            # 0-1 probability of win
    filter_score: float          # 0-1 probability of profitable direction
    predicted_direction: int     # -1=SHORT, 0=SKIP, 1=LONG
    predicted_sl: float          # Optimal SL %
    predicted_tp: float          # Optimal TP %
    predicted_lifetime: float    # Expected hold days
    reason: str                  # Why filtered or enhanced


class MLSignalFilter:
    """
    Applies ML models to filter and optimize signals.

    Supports two modes:
    1. Per-strategy: Load models from models/{strategy}/
    2. All-strategies: Load models from models/all_strategies/

    Usage:
        # Per-strategy mode
        filter = MLSignalFilter(model_dir='models', per_strategy=True)
        filter.load()

        result = filter.predict(features, strategy='ls_fade', symbol='BTCUSDT', direction='SHORT')

        # All-strategies mode
        filter = MLSignalFilter(model_dir='models/all_strategies', per_strategy=False)
        filter.load()
    """

    # HONEST features - must match adapter.py (no look-ahead bias)
    # Uses PREVIOUS DAY's candle data instead of entry day
    NUMERIC_FEATURES = [
        'Long %', 'Short %',
        'Funding Rate', 'OI USD', 'OI Contracts',
        'Open',
        'Prev High', 'Prev Low', 'Prev Close',
        'Prev Volume', 'Prev Volume USD',
        'Prev Trades Count', 'Prev Taker Buy Vol', 'Prev Taker Buy USD',
        'ADX',
        'SL %', 'TP %', 'R:R Ratio',
        'Chain Seq', 'Gap Days',
    ]
    LOG_FEATURES = ['OI USD', 'OI Contracts', 'Prev Volume', 'Prev Volume USD', 'Prev Taker Buy Vol', 'Prev Taker Buy USD']
    BOOL_FEATURES = ['Chain First']
    STRATEGIES = ['ls_fade', 'momentum', 'reversal', 'mean_reversion', 'momentum_ls']

    def __init__(
        self,
        model_dir: str = 'models',
        per_strategy: bool = True,
        min_confidence: float = 0.35,
        min_filter_score: float = 0.45,
    ):
        self.model_dir = Path(model_dir)
        self.per_strategy = per_strategy
        self.min_confidence = min_confidence
        self.min_filter_score = min_filter_score

        # Models storage
        self._models: Dict[str, Dict] = {}  # strategy -> {filter, confidence, sl, tp, scaler, features}
        self._is_loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded

    def load(self) -> 'MLSignalFilter':
        """Load trained models."""
        if self.per_strategy:
            self._load_per_strategy()
        else:
            self._load_all_strategies()

        self._is_loaded = True
        return self

    def _load_per_strategy(self):
        """Load separate models for each strategy."""
        print(f"Loading per-strategy models from {self.model_dir}...")

        for strategy in self.STRATEGIES:
            strategy_dir = self.model_dir / strategy

            if not strategy_dir.exists():
                print(f"  {strategy}: NOT FOUND")
                continue

            try:
                self._models[strategy] = self._load_model_set(strategy_dir)
                print(f"  {strategy}: OK")
            except Exception as e:
                print(f"  {strategy}: ERROR - {e}")

        if not self._models:
            raise FileNotFoundError(f"No models found in {self.model_dir}")

        print(f"Loaded {len(self._models)} strategy models")

    def _load_all_strategies(self):
        """Load single model for all strategies."""
        print(f"Loading all-strategies model from {self.model_dir}...")

        self._models['_all'] = self._load_model_set(self.model_dir)
        print("Loaded all-strategies model")

    def _load_model_set(self, model_dir: Path) -> Dict:
        """Load all 6 models from directory."""
        models = {}

        # 6 models
        for name in ['filter', 'confidence', 'direction', 'sl', 'tp', 'lifetime']:
            path = model_dir / f'{name}_model_latest.pkl'
            if path.exists():
                with open(path, 'rb') as f:
                    models[name] = pickle.load(f)
            else:
                models[name] = None  # Backward compatibility

        # Scaler and features
        with open(model_dir / 'scaler_latest.pkl', 'rb') as f:
            models['scaler'] = pickle.load(f)
        with open(model_dir / 'features_latest.pkl', 'rb') as f:
            models['features'] = pickle.load(f)

        # Extract symbols from features
        models['symbols'] = [
            f.replace('Symbol_', '')
            for f in models['features']
            if f.startswith('Symbol_')
        ]

        return models

    def predict(
        self,
        signal_data: Dict[str, Any],
        strategy: str,
        symbol: str,
        direction: str,
    ) -> MLPrediction:
        """
        Predict using all 6 models.

        Args:
            signal_data: Dict with signal features
            strategy: Strategy name
            symbol: Trading symbol
            direction: 'LONG' or 'SHORT'

        Returns:
            MLPrediction with all 6 model outputs
        """
        if not self._is_loaded:
            raise RuntimeError("Models not loaded. Call load() first.")

        # Get appropriate model set
        if self.per_strategy:
            if strategy not in self._models:
                return MLPrediction(
                    should_trade=True, confidence=0.5, filter_score=0.5,
                    predicted_direction=1 if direction == 'LONG' else -1,
                    predicted_sl=4.0, predicted_tp=10.0, predicted_lifetime=3.0,
                    reason=f"No model for strategy: {strategy}"
                )
            model_set = self._models[strategy]
        else:
            model_set = self._models['_all']

        # Build features
        features = self._build_features(
            signal_data, strategy, symbol, direction, model_set
        )

        # Scale
        features_scaled = model_set['scaler'].transform(features.reshape(1, -1))

        # Predict with all 6 models
        filter_proba = model_set['filter'].predict_proba(features_scaled)[0, 1]
        confidence = model_set['confidence'].predict_proba(features_scaled)[0, 1]

        # Direction model (multiclass: -1, 0, 1)
        if model_set.get('direction') is not None:
            predicted_direction = int(model_set['direction'].predict(features_scaled)[0])
        else:
            predicted_direction = 1 if direction == 'LONG' else -1

        # Regressors
        predicted_sl = float(np.clip(model_set['sl'].predict(features_scaled)[0], 1.0, 15.0))
        predicted_tp = float(np.clip(model_set['tp'].predict(features_scaled)[0], 2.0, 30.0))

        if model_set.get('lifetime') is not None:
            predicted_lifetime = float(np.clip(model_set['lifetime'].predict(features_scaled)[0], 0.5, 30.0))
        else:
            predicted_lifetime = 3.0

        # Decision logic
        should_trade = True
        reason = "ML approved"

        # Expected direction based on signal
        expected_direction = 1 if direction == 'LONG' else -1

        if confidence < self.min_confidence:
            should_trade = False
            reason = f"Low confidence: {confidence:.1%} < {self.min_confidence:.1%}"
        elif filter_proba < self.min_filter_score:
            should_trade = False
            reason = f"Low filter: {filter_proba:.1%} < {self.min_filter_score:.1%}"
        elif predicted_direction == 0:
            should_trade = False
            reason = "Direction model says SKIP"
        elif predicted_direction != expected_direction:
            should_trade = False
            dir_name = "LONG" if predicted_direction == 1 else "SHORT"
            reason = f"Direction mismatch: signal={direction}, ML predicted={dir_name}"

        return MLPrediction(
            should_trade=should_trade,
            confidence=confidence,
            filter_score=filter_proba,
            predicted_direction=predicted_direction,
            predicted_sl=predicted_sl,
            predicted_tp=predicted_tp,
            predicted_lifetime=predicted_lifetime,
            reason=reason,
        )

    def _build_features(
        self,
        signal_data: Dict[str, Any],
        strategy: str,
        symbol: str,
        direction: str,
        model_set: Dict,
    ) -> np.ndarray:
        """Build feature vector matching training features."""
        feature_names = model_set['features']
        features = []

        for fname in feature_names:
            if fname in self.NUMERIC_FEATURES:
                value = signal_data.get(fname, 0)
                # Log transform for large value features
                if fname in self.LOG_FEATURES:
                    value = np.log1p(abs(float(value))) if value else 0
                features.append(float(value) if value else 0)

            elif fname in self.BOOL_FEATURES:
                features.append(1 if signal_data.get(fname, False) else 0)

            elif fname == 'Direction_num':
                features.append(1 if direction == 'LONG' else -1)

            elif fname.startswith('Strategy_'):
                strat_name = fname.replace('Strategy_', '')
                features.append(1 if strategy == strat_name else 0)

            elif fname.startswith('Symbol_'):
                sym_name = fname.replace('Symbol_', '')
                features.append(1 if symbol == sym_name else 0)

            elif fname == 'DayOfWeek':
                features.append(signal_data.get('DayOfWeek', 0))

            elif fname == 'Month':
                features.append(signal_data.get('Month', 1))

            elif fname == 'Hour':
                features.append(signal_data.get('Hour', 0))

            elif fname == 'LS_Extreme':
                long_pct = signal_data.get('Long %', 0.5)
                features.append(abs(float(long_pct) - 0.5))

            elif fname == 'L/S Ratio':
                long_pct = signal_data.get('Long %', 0.5)
                short_pct = signal_data.get('Short %', 0.5) or 0.001
                features.append(float(long_pct) / float(short_pct))

            # Derived features from PREVIOUS DAY (HONEST - no look-ahead bias)
            elif fname == 'Prev_Volatility':
                prev_high = signal_data.get('Prev High', 0)
                prev_low = signal_data.get('Prev Low', 0)
                prev_close = signal_data.get('Prev Close', 1) or 1
                features.append((prev_high - prev_low) / prev_close if prev_close else 0)

            elif fname == 'Prev_BuyPressure':
                prev_taker = signal_data.get('Prev Taker Buy USD', 0)
                prev_vol = signal_data.get('Prev Volume USD', 1) or 1
                features.append(prev_taker / prev_vol if prev_vol else 0.5)

            elif fname == 'Prev_CandleDir':
                prev_close = signal_data.get('Prev Close', 0)
                prev_low = signal_data.get('Prev Low', 0)
                features.append(np.sign(prev_close - prev_low))

            else:
                features.append(0)

        return np.array(features, dtype=np.float32)

    def get_loaded_strategies(self) -> List[str]:
        """Get list of strategies with loaded models."""
        if self.per_strategy:
            return list(self._models.keys())
        return self.STRATEGIES


if __name__ == '__main__':
    # Test
    ml_filter = MLSignalFilter(model_dir='models', per_strategy=True)

    try:
        ml_filter.load()
        print(f"\nLoaded strategies: {ml_filter.get_loaded_strategies()}")

        test_data = {
            'Long %': 0.65, 'Short %': 0.35,
            'Funding Rate': 0.01, 'Volume USD': 100_000_000,
            'OI USD': 500_000_000, 'Vol Ratio': 0.5,
        }

        for strategy in ml_filter.get_loaded_strategies():
            result = ml_filter.predict(test_data, strategy, 'BTCUSDT', 'SHORT')
            print(f"\n{strategy}:")
            print(f"  Trade: {result.should_trade}, Conf: {result.confidence:.1%}")
            print(f"  Direction: {result.predicted_direction}, SL: {result.predicted_sl:.1f}%, TP: {result.predicted_tp:.1f}%")
            print(f"  Lifetime: {result.predicted_lifetime:.1f} days, Reason: {result.reason}")

    except FileNotFoundError as e:
        print(f"Error: {e}")
