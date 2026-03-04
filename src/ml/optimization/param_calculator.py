# -*- coding: utf-8 -*-
"""
Optimal Parameter Calculator for ML System.

Calculates optimal trading parameters based on ML predictions:
- Entry price optimization
- SL/TP level optimization
- Position sizing
- Valid hours estimation

Usage:
    calculator = OptimalParamCalculator(ensemble)
    params = calculator.calculate(signal, features, market_data)
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np
import structlog

from config.settings import settings
from src.ml.data.schemas import Direction, PredictionResult

if TYPE_CHECKING:
    from src.ml.models import ModelEnsemble
    from src.signals.models import TradeSignal


logger = structlog.get_logger(__name__)


@dataclass
class OptimalParams:
    """Calculated optimal parameters for a signal."""

    # Entry
    entry_price: Decimal
    entry_zone_low: Decimal
    entry_zone_high: Decimal

    # Stop Loss
    sl_price: Decimal
    sl_pct: float

    # Take Profits
    tp1_price: Decimal
    tp1_pct: float
    tp2_price: Decimal
    tp2_pct: float
    tp3_price: Decimal
    tp3_pct: float

    # Timing
    valid_hours: int
    predicted_lifetime_hours: float

    # Sizing
    position_size_pct: float
    risk_per_trade_pct: float

    # Quality
    confidence: float
    risk_reward_ratio: float


class OptimalParamCalculator:
    """
    Calculates optimal trading parameters using ML predictions.

    Combines:
    - ML predictions (direction, levels, lifetime)
    - Market data (volatility, liquidity)
    - Risk constraints
    """

    def __init__(self, ensemble: Optional["ModelEnsemble"] = None):
        """
        Initialize calculator.

        Args:
            ensemble: ModelEnsemble for predictions
        """
        self._config = settings.ml.optimization
        self._risk_config = settings.ml.risk
        self._ensemble = ensemble

        logger.info("optimal_param_calculator_init")

    def set_ensemble(self, ensemble: "ModelEnsemble") -> None:
        """Set the model ensemble."""
        self._ensemble = ensemble

    def calculate(
        self,
        signal: "TradeSignal",
        prediction: PredictionResult,
        volatility: Optional[float] = None,
        atr_pct: Optional[float] = None,
    ) -> OptimalParams:
        """
        Calculate optimal parameters for a signal.

        Args:
            signal: Original trade signal
            prediction: ML prediction result
            volatility: Optional current volatility
            atr_pct: Optional ATR as percentage

        Returns:
            OptimalParams with all calculated values
        """
        direction = 1 if signal.direction.value == "LONG" else -1
        entry_price = float(signal.entry_limit)

        # Calculate optimal levels
        sl_pct, tp1_pct, tp2_pct, tp3_pct = self._optimize_levels(
            signal, prediction, volatility, atr_pct
        )

        # Calculate prices
        if direction == 1:  # Long
            sl_price = entry_price * (1 - sl_pct / 100)
            tp1_price = entry_price * (1 + tp1_pct / 100)
            tp2_price = entry_price * (1 + tp2_pct / 100)
            tp3_price = entry_price * (1 + tp3_pct / 100)
        else:  # Short
            sl_price = entry_price * (1 + sl_pct / 100)
            tp1_price = entry_price * (1 - tp1_pct / 100)
            tp2_price = entry_price * (1 - tp2_pct / 100)
            tp3_price = entry_price * (1 - tp3_pct / 100)

        # Calculate entry zone
        zone_width = atr_pct or 0.5  # Default 0.5%
        entry_zone_low = entry_price * (1 - zone_width / 100 / 2)
        entry_zone_high = entry_price * (1 + zone_width / 100 / 2)

        # Calculate valid hours
        valid_hours = self._calculate_valid_hours(signal, prediction)

        # Calculate position size
        position_size_pct = self._calculate_position_size(
            prediction.confidence,
            tp1_pct / sl_pct,  # R:R
            sl_pct,
        )

        risk_per_trade = position_size_pct * sl_pct / 100

        return OptimalParams(
            entry_price=Decimal(str(round(entry_price, 8))),
            entry_zone_low=Decimal(str(round(entry_zone_low, 8))),
            entry_zone_high=Decimal(str(round(entry_zone_high, 8))),
            sl_price=Decimal(str(round(sl_price, 8))),
            sl_pct=sl_pct,
            tp1_price=Decimal(str(round(tp1_price, 8))),
            tp1_pct=tp1_pct,
            tp2_price=Decimal(str(round(tp2_price, 8))),
            tp2_pct=tp2_pct,
            tp3_price=Decimal(str(round(tp3_price, 8))),
            tp3_pct=tp3_pct,
            valid_hours=valid_hours,
            predicted_lifetime_hours=getattr(prediction, "predicted_lifetime", valid_hours),
            position_size_pct=position_size_pct,
            risk_per_trade_pct=risk_per_trade,
            confidence=prediction.confidence,
            risk_reward_ratio=tp1_pct / sl_pct if sl_pct > 0 else 0,
        )

    def _optimize_levels(
        self,
        signal: "TradeSignal",
        prediction: PredictionResult,
        volatility: Optional[float],
        atr_pct: Optional[float],
    ) -> Tuple[float, float, float, float]:
        """Optimize SL and TP levels."""
        # Original levels
        orig_sl = signal.stop_loss_pct
        orig_tp1 = signal.take_profits[0].percent if signal.take_profits else 2.0
        orig_tp2 = signal.take_profits[1].percent if len(signal.take_profits) > 1 else orig_tp1 * 1.5
        orig_tp3 = signal.take_profits[2].percent if len(signal.take_profits) > 2 else orig_tp1 * 2.0

        # ML predictions
        ml_sl = prediction.predicted_sl_pct
        ml_tp1 = prediction.predicted_tp1_pct
        ml_tp2 = prediction.predicted_tp2_pct
        ml_tp3 = prediction.predicted_tp3_pct

        # Blend with volatility adjustment
        vol_factor = 1.0
        if volatility:
            # Higher volatility = wider stops
            vol_factor = min(2.0, max(0.5, volatility / 50))  # Normalize around 50% annualized

        if atr_pct:
            # Ensure SL is at least 1.5x ATR
            min_sl = atr_pct * 1.5
            ml_sl = max(ml_sl, min_sl)

        # Blend original and ML (60% ML, 40% original)
        ml_weight = 0.6

        sl_pct = self._blend_with_bounds(
            orig_sl, ml_sl * vol_factor, ml_weight,
            self._config.max_sl_adjustment_pct
        )

        tp1_pct = self._blend_with_bounds(
            orig_tp1, ml_tp1 * vol_factor, ml_weight,
            self._config.max_tp_adjustment_pct
        )

        tp2_pct = self._blend_with_bounds(
            orig_tp2, ml_tp2 * vol_factor, ml_weight,
            self._config.max_tp_adjustment_pct
        )

        tp3_pct = self._blend_with_bounds(
            orig_tp3, ml_tp3 * vol_factor, ml_weight,
            self._config.max_tp_adjustment_pct
        )

        # Ensure TP ordering
        tp2_pct = max(tp2_pct, tp1_pct * 1.2)
        tp3_pct = max(tp3_pct, tp2_pct * 1.2)

        return sl_pct, tp1_pct, tp2_pct, tp3_pct

    def _blend_with_bounds(
        self,
        original: float,
        ml_value: float,
        ml_weight: float,
        max_adjustment_pct: float,
    ) -> float:
        """Blend values with bounds."""
        blended = original * (1 - ml_weight) + ml_value * ml_weight

        min_val = original * (1 - max_adjustment_pct / 100)
        max_val = original * (1 + max_adjustment_pct / 100)

        return max(min_val, min(max_val, blended))

    def _calculate_valid_hours(
        self,
        signal: "TradeSignal",
        prediction: PredictionResult,
    ) -> int:
        """Calculate optimal valid hours for signal."""
        # Use predicted lifetime if available
        if hasattr(prediction, "predicted_lifetime"):
            return max(4, min(168, int(prediction.predicted_lifetime)))

        # Otherwise use original with confidence adjustment
        base_hours = signal.valid_hours

        # Higher confidence = longer validity
        if prediction.confidence > 0.8:
            return min(168, int(base_hours * 1.5))
        elif prediction.confidence > 0.6:
            return base_hours
        else:
            return max(4, int(base_hours * 0.7))

    def _calculate_position_size(
        self,
        confidence: float,
        risk_reward: float,
        sl_pct: float,
    ) -> float:
        """Calculate position size using Kelly-inspired approach."""
        # Estimate win probability from confidence
        win_prob = 0.4 + confidence * 0.3  # Map 0.6-1.0 confidence to 0.58-0.7 win prob

        # Kelly fraction
        if risk_reward <= 0:
            return self._risk_config.min_position_pct

        kelly = (win_prob * risk_reward - (1 - win_prob)) / risk_reward

        if kelly <= 0:
            return self._risk_config.min_position_pct

        # Apply Kelly fraction (conservative)
        kelly_fraction = self._risk_config.kelly_fraction
        position_pct = kelly * kelly_fraction * 100

        # Apply limits
        return max(
            self._risk_config.min_position_pct,
            min(self._risk_config.max_position_pct, position_pct),
        )

    def calculate_batch(
        self,
        signals: List["TradeSignal"],
        predictions: List[PredictionResult],
    ) -> List[OptimalParams]:
        """Calculate optimal params for multiple signals."""
        results = []

        for signal, prediction in zip(signals, predictions):
            params = self.calculate(signal, prediction)
            results.append(params)

        return results
