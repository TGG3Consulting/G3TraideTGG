# -*- coding: utf-8 -*-
"""
Signal Labeler for ML Training.

Creates labels from REAL backtest results, NOT from raw prices!
Uses backtester.models.BacktestResult for actual outcomes.

Usage:
    from src.ml.integration import MLBacktesterIntegration
    from src.ml.training import SignalLabeler

    # Get REAL results
    integration = MLBacktesterIntegration()
    results = integration.run_backtest("logs/signals.jsonl")

    # Create labels from REAL results
    labeler = SignalLabeler()
    training_data = labeler.create_training_data(results)
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import structlog

from backtester.models import BacktestResult, ParsedSignal, ExitReason


logger = structlog.get_logger(__name__)


@dataclass
class SignalLabels:
    """Labels extracted from REAL backtest result."""

    # Was the trade profitable?
    profitable: bool

    # Actual PnL
    pnl_pct: float
    net_pnl_pct: float

    # How did we exit?
    exit_reason: str

    # Which TPs were hit?
    tp1_hit: bool
    tp2_hit: bool
    tp3_hit: bool
    sl_hit: bool

    # Hold time
    hold_hours: float

    # Win probability (for classification)
    # 1 = win, 0 = loss
    win_label: int


class SignalLabeler:
    """
    Creates training labels from REAL backtest results.

    IMPORTANT: This does NOT use lookahead on raw prices!
    It uses actual outcomes from PositionSimulator.
    """

    def __init__(self, min_pnl_for_win: float = 0.0):
        """
        Initialize labeler.

        Args:
            min_pnl_for_win: Minimum net PnL % to consider a win (default: 0)
        """
        self._min_pnl_for_win = min_pnl_for_win

        logger.info(
            "signal_labeler_init",
            min_pnl_for_win=min_pnl_for_win,
        )

    def create_labels(self, result: BacktestResult) -> SignalLabels:
        """
        Create labels from a single REAL backtest result.

        Args:
            result: BacktestResult from PositionSimulator

        Returns:
            SignalLabels with REAL outcomes
        """
        net_pnl = float(result.net_pnl_percent)
        profitable = net_pnl > self._min_pnl_for_win

        return SignalLabels(
            profitable=profitable,
            pnl_pct=float(result.pnl_percent),
            net_pnl_pct=net_pnl,
            exit_reason=result.exit_reason.value,
            tp1_hit=result.tp1_hit,
            tp2_hit=result.tp2_hit,
            tp3_hit=result.tp3_hit,
            sl_hit=result.sl_hit,
            hold_hours=result.hold_time_hours,
            win_label=1 if profitable else 0,
        )

    def create_training_data(
        self,
        results: List[BacktestResult],
        include_not_filled: bool = False,
    ) -> pd.DataFrame:
        """
        Create training DataFrame from backtest results.

        Args:
            results: List of REAL BacktestResult
            include_not_filled: Whether to include signals that didn't fill

        Returns:
            DataFrame with features and labels
        """
        rows = []

        for result in results:
            # Skip not filled unless requested
            if not result.entry_filled and not include_not_filled:
                continue

            signal = result.signal

            # Features from signal
            row = {
                # Signal identification
                "signal_id": signal.signal_id,
                "symbol": signal.symbol,
                "timestamp": signal.timestamp,

                # Signal features
                "direction": signal.direction.value,
                "probability": signal.probability,
                "risk_reward": signal.risk_reward,
                "stop_loss_pct": signal.stop_loss_pct,
                "tp1_pct": signal.tp1.percent,
                "tp2_pct": signal.tp2.percent,
                "tp3_pct": signal.tp3.percent,
                "signal_type": signal.signal_type,
                "confidence": signal.confidence,

                # Entry filled?
                "entry_filled": result.entry_filled,
            }

            # Add signal details if available
            if signal.details:
                row["oi_change_1h"] = self._parse_pct(
                    signal.details.get("oi_change_1h", "0")
                )
                row["oi_change_5m"] = self._parse_pct(
                    signal.details.get("oi_change_5m", "0")
                )
                row["funding_pct"] = self._parse_pct(
                    signal.details.get("funding", "0")
                )
                row["volume_ratio"] = self._parse_float(
                    signal.details.get("volume_ratio", "1.0")
                )

            # Add accumulation scores if available
            if signal.accumulation_score:
                row["score_total"] = signal.accumulation_score.get("total", 0)

            # Labels from REAL result
            if result.entry_filled:
                labels = self.create_labels(result)
                row["label_profitable"] = labels.profitable
                row["label_pnl_pct"] = labels.pnl_pct
                row["label_net_pnl_pct"] = labels.net_pnl_pct
                row["label_exit_reason"] = labels.exit_reason
                row["label_tp1_hit"] = labels.tp1_hit
                row["label_tp2_hit"] = labels.tp2_hit
                row["label_tp3_hit"] = labels.tp3_hit
                row["label_sl_hit"] = labels.sl_hit
                row["label_hold_hours"] = labels.hold_hours
                row["label_win"] = labels.win_label
            else:
                # Not filled - no outcome labels
                row["label_profitable"] = None
                row["label_pnl_pct"] = None
                row["label_net_pnl_pct"] = None
                row["label_exit_reason"] = ExitReason.NOT_FILLED.value
                row["label_tp1_hit"] = False
                row["label_tp2_hit"] = False
                row["label_tp3_hit"] = False
                row["label_sl_hit"] = False
                row["label_hold_hours"] = 0.0
                row["label_win"] = None

            rows.append(row)

        df = pd.DataFrame(rows)

        logger.info(
            "training_data_created",
            total_rows=len(df),
            filled_rows=len(df[df["entry_filled"] == True]) if "entry_filled" in df.columns else 0,
        )

        return df

    def _parse_pct(self, value: str) -> float:
        """Parse percentage string like '+1.5%' or '-0.5%'."""
        if not value:
            return 0.0
        try:
            # Remove % and parse
            cleaned = str(value).replace("%", "").replace("+", "").strip()
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0

    def _parse_float(self, value: str) -> float:
        """Parse float string like '1.5x' or '0.8'."""
        if not value:
            return 0.0
        try:
            cleaned = str(value).replace("x", "").strip()
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0

    def get_label_statistics(self, df: pd.DataFrame) -> Dict:
        """
        Get statistics about labels in training data.

        Args:
            df: Training DataFrame with labels

        Returns:
            Dictionary with statistics
        """
        stats = {}

        if "label_win" in df.columns:
            filled = df[df["entry_filled"] == True]
            wins = filled[filled["label_win"] == 1]
            losses = filled[filled["label_win"] == 0]

            stats["total_signals"] = len(df)
            stats["filled_signals"] = len(filled)
            stats["wins"] = len(wins)
            stats["losses"] = len(losses)
            stats["win_rate"] = len(wins) / len(filled) if len(filled) > 0 else 0.0

        if "label_pnl_pct" in df.columns:
            filled = df[df["entry_filled"] == True]
            pnls = filled["label_pnl_pct"].dropna()

            stats["avg_pnl_pct"] = float(pnls.mean()) if len(pnls) > 0 else 0.0
            stats["median_pnl_pct"] = float(pnls.median()) if len(pnls) > 0 else 0.0
            stats["std_pnl_pct"] = float(pnls.std()) if len(pnls) > 0 else 0.0
            stats["min_pnl_pct"] = float(pnls.min()) if len(pnls) > 0 else 0.0
            stats["max_pnl_pct"] = float(pnls.max()) if len(pnls) > 0 else 0.0

        if "label_exit_reason" in df.columns:
            exit_counts = df["label_exit_reason"].value_counts().to_dict()
            stats["exits_by_reason"] = exit_counts

        logger.info("label_statistics", **stats)

        return stats
