# -*- coding: utf-8 -*-
"""
Optimal Parameters Labeler.

Creates training data with OPTIMAL SL/TP/Lifetime labels.
Uses REAL klines AFTER entry to calculate what parameters WOULD have worked.

Usage:
    labeler = OptimalParamsLabeler()
    training_data = await labeler.create_training_data(signals)
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import structlog

from backtester.config import BacktestConfig
from backtester.data_loader import BinanceDataLoader
from backtester.log_parser import LogParser
from backtester.models import ParsedSignal, Kline

from .optimal_params_calculator import OptimalParamsCalculator, OptimalParams


logger = structlog.get_logger(__name__)


class OptimalParamsLabeler:
    """
    Creates training labels with OPTIMAL parameters.

    For each signal:
    1. Load klines AFTER entry
    2. Calculate optimal SL/TP/Lifetime from real price action
    3. Create training row with signal features + optimal params as labels
    """

    def __init__(
        self,
        config: Optional[BacktestConfig] = None,
        lookback_hours: int = 48,
    ):
        """
        Initialize labeler.

        Args:
            config: Backtest config
            lookback_hours: How many hours after entry to analyze
        """
        self._config = config or BacktestConfig()
        self._lookback_hours = lookback_hours
        self._calculator = OptimalParamsCalculator(max_lookback_hours=lookback_hours)
        self._parser = LogParser(self._config)

        logger.info(
            "optimal_params_labeler_init",
            lookback_hours=lookback_hours,
        )

    def create_training_data_sync(
        self,
        signals: List[ParsedSignal],
        klines_cache: Dict[str, List[Kline]],
    ) -> pd.DataFrame:
        """
        Create training data synchronously (when klines already loaded).

        Args:
            signals: List of parsed signals
            klines_cache: Pre-loaded klines {symbol: [klines]}

        Returns:
            DataFrame with features and optimal params labels
        """
        rows = []

        for signal in signals:
            klines = klines_cache.get(signal.symbol, [])

            if not klines:
                continue

            # Filter klines AFTER entry
            klines_after = self._filter_klines_after_entry(klines, signal.timestamp)

            if len(klines_after) < 60:  # Need at least 1 hour of data
                continue

            # Calculate optimal params
            optimal = self._calculator.calculate(
                entry_price=float(signal.entry_zone.low),  # Use entry zone low
                direction=signal.direction.value,
                klines_after_entry=klines_after,
            )

            if not optimal:
                continue

            # Create row with features and labels
            row = self._create_row(signal, optimal)
            rows.append(row)

        df = pd.DataFrame(rows)

        logger.info(
            "training_data_created",
            total_signals=len(signals),
            valid_rows=len(df),
        )

        return df

    async def create_training_data(
        self,
        signals_path: str = "logs/signals.jsonl",
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Create training data by loading klines from Binance.

        Args:
            signals_path: Path to signals.jsonl
            limit: Optional limit on signals to process

        Returns:
            DataFrame with features and optimal params labels
        """
        # Parse signals
        signals = self._parser.parse_all_signals()

        if limit:
            signals = signals[:limit]

        logger.info("signals_parsed", count=len(signals))

        # Get unique symbols and time range
        symbols = {s.symbol for s in signals}
        start_time, end_time = self._parser.get_time_range()

        # Add lookback buffer to end time
        if end_time:
            end_time = end_time + timedelta(hours=self._lookback_hours)

        logger.info(
            "loading_klines",
            symbols=len(symbols),
            start=start_time.isoformat() if start_time else None,
            end=end_time.isoformat() if end_time else None,
        )

        # Load klines
        with BinanceDataLoader(self._config) as loader:
            klines_cache = loader.load_all_symbols(symbols, start_time, end_time)

        logger.info("klines_loaded", symbols_with_data=len(klines_cache))

        # Create training data
        return self.create_training_data_sync(signals, klines_cache)

    def _filter_klines_after_entry(
        self,
        klines: List[Kline],
        entry_time: datetime,
    ) -> List[Kline]:
        """Filter klines to only include those AFTER entry time."""
        # Make entry_time timezone-aware if needed
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)

        return [
            k for k in klines
            if k.open_time >= entry_time
        ]

    def _create_row(
        self,
        signal: ParsedSignal,
        optimal: OptimalParams,
    ) -> Dict:
        """Create a training row with features and labels."""
        row = {
            # Signal identification
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
            "timestamp": signal.timestamp,
            "direction": signal.direction.value,

            # ===== FEATURES (inputs) =====
            # Signal parameters (what was originally set)
            "feat_probability": signal.probability,
            "feat_risk_reward": signal.risk_reward,
            "feat_sl_pct": signal.stop_loss_pct,
            "feat_tp1_pct": signal.tp1.percent,
            "feat_tp2_pct": signal.tp2.percent,
            "feat_tp3_pct": signal.tp3.percent,
            "feat_confidence": signal.confidence,

            # Direction as numeric
            "feat_direction_num": 1 if signal.direction.value == "LONG" else 0,
        }

        # Add details if available
        if signal.details:
            row["feat_oi_change_1h"] = self._parse_pct(signal.details.get("oi_change_1h", "0"))
            row["feat_oi_change_5m"] = self._parse_pct(signal.details.get("oi_change_5m", "0"))
            row["feat_funding"] = self._parse_pct(signal.details.get("funding", "0"))
            row["feat_volume_ratio"] = self._parse_float(signal.details.get("volume_ratio", "1.0"))

        # Add accumulation scores
        if signal.accumulation_score:
            row["feat_score_total"] = signal.accumulation_score.get("total", 0)
            row["feat_score_oi"] = signal.accumulation_score.get("oi_growth", 0)
            row["feat_score_funding"] = signal.accumulation_score.get("funding_cheap", 0)

        # ===== LABELS (outputs - what ML should predict) =====
        row["label_optimal_sl_pct"] = optimal.optimal_sl_pct
        row["label_optimal_tp_pct"] = optimal.optimal_tp_pct
        row["label_optimal_lifetime_hours"] = optimal.optimal_lifetime_hours

        # Additional metrics for analysis
        row["label_max_profit_pct"] = optimal.max_profit_pct
        row["label_max_drawdown_pct"] = optimal.max_drawdown_pct
        row["label_time_to_max_profit_min"] = optimal.time_to_max_profit_minutes
        row["label_would_be_profitable"] = optimal.would_be_profitable
        row["label_risk_reward_optimal"] = optimal.risk_reward_optimal

        return row

    def _parse_pct(self, value: str) -> float:
        """Parse percentage string."""
        if not value:
            return 0.0
        try:
            cleaned = str(value).replace("%", "").replace("+", "").strip()
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0

    def _parse_float(self, value: str) -> float:
        """Parse float string."""
        if not value:
            return 0.0
        try:
            cleaned = str(value).replace("x", "").strip()
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0

    def get_feature_columns(self) -> List[str]:
        """Get list of feature column names."""
        return [
            "feat_probability",
            "feat_risk_reward",
            "feat_sl_pct",
            "feat_tp1_pct",
            "feat_tp2_pct",
            "feat_tp3_pct",
            "feat_direction_num",
            "feat_oi_change_1h",
            "feat_oi_change_5m",
            "feat_funding",
            "feat_volume_ratio",
            "feat_score_total",
        ]

    def get_label_columns(self) -> List[str]:
        """Get list of label column names (what ML predicts)."""
        return [
            "label_optimal_sl_pct",
            "label_optimal_tp_pct",
            "label_optimal_lifetime_hours",
        ]
