# -*- coding: utf-8 -*-
"""
Signal Optimizer for ML System.

Applies ML predictions to optimize trading signals:
- Adjusts SL/TP levels based on model predictions
- Filters signals with low ML confidence
- Recalibrates probability/confidence

Usage:
    optimizer = SignalOptimizer(ensemble)
    optimized = optimizer.optimize(signal, features)
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np
import pandas as pd
import structlog

from config.settings import settings
from src.ml.data.schemas import Direction, OptimizedSignal, PredictionResult
from src.ml.models import ModelEnsemble

if TYPE_CHECKING:
    from src.signals.models import TradeSignal


logger = structlog.get_logger(__name__)


class SignalOptimizer:
    """
    Optimizes trading signals using ML predictions.

    Takes existing signals from the system and:
    1. Validates direction with ML prediction
    2. Adjusts SL/TP levels based on regression
    3. Recalibrates confidence with calibrated probabilities
    4. Filters low-quality signals
    """

    def __init__(
        self,
        ensemble: Optional[ModelEnsemble] = None,
        model_dir: Optional[str] = None,
    ):
        """
        Initialize signal optimizer.

        Args:
            ensemble: Pre-loaded model ensemble
            model_dir: Optional path to load models from
        """
        self._config = settings.ml.optimization
        self._ensemble = ensemble

        if ensemble is None and model_dir:
            self._ensemble = ModelEnsemble()
            self._ensemble.load_models(model_dir)

        self._optimization_stats = {
            "total_signals": 0,
            "filtered_signals": 0,
            "direction_conflicts": 0,
            "sl_adjustments": 0,
            "tp_adjustments": 0,
        }

        logger.info(
            "signal_optimizer_init",
            has_ensemble=self._ensemble is not None,
        )

    @property
    def is_ready(self) -> bool:
        """Whether optimizer is ready (models loaded)."""
        return self._ensemble is not None and self._ensemble.is_loaded

    def set_ensemble(self, ensemble: ModelEnsemble) -> None:
        """Set the model ensemble."""
        self._ensemble = ensemble
        logger.info("ensemble_set", is_loaded=ensemble.is_loaded)

    def optimize(
        self,
        signal: "TradeSignal",
        features: np.ndarray,
    ) -> Optional[OptimizedSignal]:
        """
        Optimize a single trading signal.

        Args:
            signal: Original TradeSignal
            features: Feature vector for ML prediction

        Returns:
            OptimizedSignal or None if filtered
        """
        if not self.is_ready:
            logger.warning("optimizer_not_ready")
            return None

        self._optimization_stats["total_signals"] += 1

        # Get ML prediction
        prediction = self._ensemble.predict_single(features, signal.symbol)

        # Check direction agreement
        signal_direction = 1 if signal.direction.value == "LONG" else -1
        ml_direction = prediction.direction.value

        if signal_direction != ml_direction:
            self._optimization_stats["direction_conflicts"] += 1

            # If ML says neutral or opposite with high confidence, filter
            if prediction.confidence > self._config.min_confidence:
                logger.debug(
                    "signal_filtered_direction_conflict",
                    symbol=signal.symbol,
                    signal_dir=signal_direction,
                    ml_dir=ml_direction,
                    ml_confidence=prediction.confidence,
                )
                self._optimization_stats["filtered_signals"] += 1
                return None

        # Check minimum ML confidence
        if prediction.confidence < self._config.min_confidence:
            logger.debug(
                "signal_filtered_low_confidence",
                symbol=signal.symbol,
                confidence=prediction.confidence,
            )
            self._optimization_stats["filtered_signals"] += 1
            return None

        # Optimize levels
        optimized_sl, optimized_tps = self._optimize_levels(signal, prediction)

        # Calculate new R:R
        entry_price = float(signal.entry_limit)
        risk_reward = self._calculate_rr(
            entry_price, optimized_sl, optimized_tps[0], signal_direction
        )

        # Check minimum R:R
        if risk_reward < self._config.min_predicted_rr:
            logger.debug(
                "signal_filtered_low_rr",
                symbol=signal.symbol,
                risk_reward=risk_reward,
            )
            self._optimization_stats["filtered_signals"] += 1
            return None

        # Create optimized signal
        optimized = OptimizedSignal(
            original_signal_id=signal.signal_id,
            symbol=signal.symbol,
            timestamp=datetime.now(timezone.utc),
            direction=Direction.LONG if signal_direction == 1 else Direction.SHORT,
            original_confidence=signal.probability / 100.0,
            ml_confidence=prediction.confidence,
            combined_confidence=self._combine_confidences(
                signal.probability / 100.0, prediction.confidence
            ),
            original_sl_pct=signal.stop_loss_pct,
            optimized_sl_pct=optimized_sl,
            original_tp1_pct=signal.take_profits[0].percent if signal.take_profits else 2.0,
            optimized_tp1_pct=optimized_tps[0],
            optimized_tp2_pct=optimized_tps[1],
            optimized_tp3_pct=optimized_tps[2],
            predicted_win_probability=prediction.long_probability if signal_direction == 1 else prediction.short_probability,
            risk_reward_ratio=risk_reward,
            should_trade=True,
            filter_reason=None,
        )

        logger.debug(
            "signal_optimized",
            symbol=signal.symbol,
            original_sl=signal.stop_loss_pct,
            optimized_sl=optimized_sl,
            ml_confidence=prediction.confidence,
        )

        return optimized

    def _optimize_levels(
        self,
        signal: "TradeSignal",
        prediction: PredictionResult,
    ) -> Tuple[float, List[float]]:
        """
        Optimize SL and TP levels.

        Blends original levels with ML predictions within allowed range.

        Args:
            signal: Original signal
            prediction: ML prediction

        Returns:
            Tuple of (optimized_sl, [tp1, tp2, tp3])
        """
        original_sl = signal.stop_loss_pct
        ml_sl = prediction.predicted_sl_pct

        # Blend SL: weighted average with bounds
        optimized_sl = self._blend_with_bounds(
            original_sl,
            ml_sl,
            ml_weight=0.6,
            max_adjustment_pct=self._config.max_sl_adjustment_pct,
        )

        if abs(optimized_sl - original_sl) > 0.01:
            self._optimization_stats["sl_adjustments"] += 1

        # TPs
        original_tp1 = signal.take_profits[0].percent if signal.take_profits else 2.0
        original_tp2 = signal.take_profits[1].percent if len(signal.take_profits) > 1 else original_tp1 * 1.5
        original_tp3 = signal.take_profits[2].percent if len(signal.take_profits) > 2 else original_tp1 * 2.0

        ml_tp1 = prediction.predicted_tp1_pct
        ml_tp2 = prediction.predicted_tp2_pct
        ml_tp3 = prediction.predicted_tp3_pct

        optimized_tp1 = self._blend_with_bounds(
            original_tp1, ml_tp1, 0.5, self._config.max_tp_adjustment_pct
        )
        optimized_tp2 = self._blend_with_bounds(
            original_tp2, ml_tp2, 0.5, self._config.max_tp_adjustment_pct
        )
        optimized_tp3 = self._blend_with_bounds(
            original_tp3, ml_tp3, 0.5, self._config.max_tp_adjustment_pct
        )

        # Ensure TP ordering
        optimized_tp2 = max(optimized_tp2, optimized_tp1 * 1.2)
        optimized_tp3 = max(optimized_tp3, optimized_tp2 * 1.2)

        if abs(optimized_tp1 - original_tp1) > 0.01:
            self._optimization_stats["tp_adjustments"] += 1

        return optimized_sl, [optimized_tp1, optimized_tp2, optimized_tp3]

    def _blend_with_bounds(
        self,
        original: float,
        ml_value: float,
        ml_weight: float,
        max_adjustment_pct: float,
    ) -> float:
        """Blend values with maximum adjustment bounds."""
        blended = original * (1 - ml_weight) + ml_value * ml_weight

        # Apply bounds
        min_val = original * (1 - max_adjustment_pct / 100)
        max_val = original * (1 + max_adjustment_pct / 100)

        return max(min_val, min(max_val, blended))

    def _combine_confidences(
        self,
        original: float,
        ml: float,
        original_weight: float = 0.4,
    ) -> float:
        """Combine original and ML confidences."""
        combined = original * original_weight + ml * (1 - original_weight)
        return min(0.99, max(0.01, combined))

    def _calculate_rr(
        self,
        entry: float,
        sl_pct: float,
        tp_pct: float,
        direction: int,
    ) -> float:
        """Calculate risk/reward ratio."""
        if sl_pct == 0:
            return 0
        return tp_pct / sl_pct

    def optimize_batch(
        self,
        signals: List["TradeSignal"],
        features: np.ndarray,
    ) -> List[OptimizedSignal]:
        """
        Optimize multiple signals at once.

        Args:
            signals: List of TradeSignal objects
            features: Feature matrix (n_signals, n_features)

        Returns:
            List of OptimizedSignal (filtered signals excluded)
        """
        if not self.is_ready:
            logger.warning("optimizer_not_ready_for_batch")
            return []

        if len(signals) != len(features):
            raise ValueError(
                f"Signal count ({len(signals)}) != feature count ({len(features)})"
            )

        # Get all predictions at once
        symbols = [s.symbol for s in signals]
        predictions = self._ensemble.predict(features, symbols)

        optimized = []
        for signal, prediction in zip(signals, predictions):
            # Reuse single optimization logic
            result = self._optimize_single_with_prediction(signal, prediction)
            if result is not None:
                optimized.append(result)

        logger.info(
            "batch_optimization_complete",
            total=len(signals),
            optimized=len(optimized),
            filtered=len(signals) - len(optimized),
        )

        return optimized

    def _optimize_single_with_prediction(
        self,
        signal: "TradeSignal",
        prediction: PredictionResult,
    ) -> Optional[OptimizedSignal]:
        """Optimize using pre-computed prediction."""
        self._optimization_stats["total_signals"] += 1

        signal_direction = 1 if signal.direction.value == "LONG" else -1
        ml_direction = prediction.direction.value

        # Direction conflict check
        if signal_direction != ml_direction:
            self._optimization_stats["direction_conflicts"] += 1
            if prediction.confidence > self._config.min_confidence:
                self._optimization_stats["filtered_signals"] += 1
                return None

        # Confidence check
        if prediction.confidence < self._config.min_confidence:
            self._optimization_stats["filtered_signals"] += 1
            return None

        # Optimize levels
        optimized_sl, optimized_tps = self._optimize_levels(signal, prediction)

        entry_price = float(signal.entry_limit)
        risk_reward = self._calculate_rr(
            entry_price, optimized_sl, optimized_tps[0], signal_direction
        )

        if risk_reward < self._config.min_predicted_rr:
            self._optimization_stats["filtered_signals"] += 1
            return None

        return OptimizedSignal(
            original_signal_id=signal.signal_id,
            symbol=signal.symbol,
            timestamp=datetime.now(timezone.utc),
            direction=Direction.LONG if signal_direction == 1 else Direction.SHORT,
            original_confidence=signal.probability / 100.0,
            ml_confidence=prediction.confidence,
            combined_confidence=self._combine_confidences(
                signal.probability / 100.0, prediction.confidence
            ),
            original_sl_pct=signal.stop_loss_pct,
            optimized_sl_pct=optimized_sl,
            original_tp1_pct=signal.take_profits[0].percent if signal.take_profits else 2.0,
            optimized_tp1_pct=optimized_tps[0],
            optimized_tp2_pct=optimized_tps[1],
            optimized_tp3_pct=optimized_tps[2],
            predicted_win_probability=prediction.long_probability if signal_direction == 1 else prediction.short_probability,
            risk_reward_ratio=risk_reward,
            should_trade=True,
            filter_reason=None,
        )

    def get_stats(self) -> Dict:
        """Get optimization statistics."""
        return self._optimization_stats.copy()

    def reset_stats(self) -> None:
        """Reset optimization statistics."""
        self._optimization_stats = {
            "total_signals": 0,
            "filtered_signals": 0,
            "direction_conflicts": 0,
            "sl_adjustments": 0,
            "tp_adjustments": 0,
        }
