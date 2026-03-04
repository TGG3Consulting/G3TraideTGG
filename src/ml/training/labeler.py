# -*- coding: utf-8 -*-
"""
Labeler for ML Training.

Creates labels from historical price data based on future price movements.

Labeling strategies:
1. Direction: Did price go up/down by X% within N hours?
2. SL/TP levels: What would optimal SL/TP have been?
3. Trade outcome: Did the trade hit TP or SL first?

Usage:
    labeler = Labeler()
    labels = labeler.create_labels(df, lookahead_hours=24)
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import structlog

from config.settings import settings
from src.ml.data.schemas import Direction


logger = structlog.get_logger(__name__)


class Labeler:
    """
    Creates training labels from historical data.

    Labels are based on future price movements, which is
    why we need a lookahead period.
    """

    def __init__(
        self,
        min_move_pct: float = 0.5,
        lookahead_hours: int = 24,
        sl_search_range: Tuple[float, float] = (0.3, 5.0),
        tp_search_range: Tuple[float, float] = (0.5, 10.0),
    ):
        """
        Initialize labeler.

        Args:
            min_move_pct: Minimum price move to consider directional
            lookahead_hours: Hours to look ahead for labels
            sl_search_range: (min, max) for SL percentage search
            tp_search_range: (min, max) for TP percentage search
        """
        self._min_move_pct = min_move_pct
        self._lookahead_hours = lookahead_hours
        self._sl_range = sl_search_range
        self._tp_range = tp_search_range

        logger.info(
            "labeler_init",
            min_move_pct=min_move_pct,
            lookahead_hours=lookahead_hours,
        )

    def create_labels(
        self,
        df: pd.DataFrame,
        price_col: str = "close",
    ) -> pd.DataFrame:
        """
        Create all labels for training data.

        Args:
            df: DataFrame with OHLCV data and timestamps
            price_col: Column name for price

        Returns:
            DataFrame with label columns added
        """
        if df.empty:
            return df

        df = df.copy()

        logger.info("creating_labels", rows=len(df))

        # Ensure sorted by timestamp
        if "timestamp" in df.columns:
            df = df.sort_values("timestamp").reset_index(drop=True)

        # Get price series
        prices = df[price_col].values

        # Create direction labels
        df = self._add_direction_labels(df, prices)

        # Create optimal SL/TP labels
        df = self._add_level_labels(df, prices)

        # Create outcome labels
        df = self._add_outcome_labels(df, prices)

        # Remove rows where we can't look ahead
        lookahead_rows = self._hours_to_rows(df, self._lookahead_hours)
        df = df.iloc[:-lookahead_rows].copy() if lookahead_rows > 0 else df

        logger.info(
            "labels_created",
            rows=len(df),
            label_columns=[c for c in df.columns if c.startswith("label_")],
        )

        return df

    def _hours_to_rows(self, df: pd.DataFrame, hours: int) -> int:
        """Convert hours to row count based on data frequency."""
        if len(df) < 2:
            return hours

        if "timestamp" in df.columns:
            time_diff = (
                pd.to_datetime(df["timestamp"].iloc[1])
                - pd.to_datetime(df["timestamp"].iloc[0])
            )
            minutes_per_row = max(1, time_diff.total_seconds() / 60)
            return int(hours * 60 / minutes_per_row)

        # Default: assume hourly data
        return hours

    def _add_direction_labels(
        self,
        df: pd.DataFrame,
        prices: np.ndarray,
    ) -> pd.DataFrame:
        """Add direction labels based on future price movement."""
        n = len(prices)
        lookahead_rows = self._hours_to_rows(df, self._lookahead_hours)

        # Initialize labels
        directions = np.zeros(n, dtype=np.int8)
        max_up = np.zeros(n)
        max_down = np.zeros(n)

        for i in range(n):
            end_idx = min(i + lookahead_rows + 1, n)
            if end_idx <= i + 1:
                continue

            future_prices = prices[i + 1:end_idx]
            current_price = prices[i]

            if current_price == 0:
                continue

            # Calculate max up and down moves
            max_future = np.max(future_prices)
            min_future = np.min(future_prices)

            max_up[i] = (max_future - current_price) / current_price * 100
            max_down[i] = (current_price - min_future) / current_price * 100

            # Determine direction
            if max_up[i] >= self._min_move_pct and max_up[i] > max_down[i]:
                directions[i] = 1  # Long
            elif max_down[i] >= self._min_move_pct and max_down[i] > max_up[i]:
                directions[i] = -1  # Short
            # else: 0 (Neutral)

        df["label_direction"] = directions
        df["label_max_up_pct"] = max_up
        df["label_max_down_pct"] = max_down

        # Distribution logging
        logger.debug(
            "direction_labels_distribution",
            long=int((directions == 1).sum()),
            short=int((directions == -1).sum()),
            neutral=int((directions == 0).sum()),
        )

        return df

    def _add_level_labels(
        self,
        df: pd.DataFrame,
        prices: np.ndarray,
    ) -> pd.DataFrame:
        """Add optimal SL/TP level labels."""
        n = len(prices)
        lookahead_rows = self._hours_to_rows(df, self._lookahead_hours)

        # Initialize
        optimal_sl = np.zeros(n)
        optimal_tp = np.zeros(n)

        for i in range(n):
            end_idx = min(i + lookahead_rows + 1, n)
            if end_idx <= i + 1:
                continue

            future_prices = prices[i + 1:end_idx]
            current_price = prices[i]
            direction = df.loc[i, "label_direction"] if "label_direction" in df.columns else 0

            if direction == 0 or current_price == 0:
                # For neutral, use typical values
                optimal_sl[i] = 1.0  # 1% SL
                optimal_tp[i] = 2.0  # 2% TP
                continue

            # Calculate optimal levels based on what actually happened
            if direction == 1:  # Long
                # SL: furthest down before recovery
                # TP: max up achieved
                sl_pct, tp_pct = self._find_optimal_long_levels(
                    current_price, future_prices
                )
            else:  # Short
                sl_pct, tp_pct = self._find_optimal_short_levels(
                    current_price, future_prices
                )

            optimal_sl[i] = np.clip(sl_pct, self._sl_range[0], self._sl_range[1])
            optimal_tp[i] = np.clip(tp_pct, self._tp_range[0], self._tp_range[1])

        df["label_sl_pct"] = optimal_sl
        df["label_tp_pct"] = optimal_tp
        df["label_tp2_pct"] = optimal_tp * 1.5
        df["label_tp3_pct"] = optimal_tp * 2.0

        return df

    def _find_optimal_long_levels(
        self,
        entry_price: float,
        future_prices: np.ndarray,
    ) -> Tuple[float, float]:
        """Find optimal SL/TP for a long position."""
        if len(future_prices) == 0:
            return 1.0, 2.0

        # Maximum profit achieved
        max_price = np.max(future_prices)
        tp_pct = (max_price - entry_price) / entry_price * 100

        # Drawdown before max (for SL)
        max_idx = np.argmax(future_prices)
        if max_idx > 0:
            min_before_max = np.min(future_prices[:max_idx])
            sl_pct = (entry_price - min_before_max) / entry_price * 100
        else:
            sl_pct = 0.5  # Minimum SL

        # Ensure reasonable values
        sl_pct = max(0.3, sl_pct * 1.2)  # Add 20% buffer
        tp_pct = max(0.5, tp_pct * 0.9)  # Take 90% of max

        return sl_pct, tp_pct

    def _find_optimal_short_levels(
        self,
        entry_price: float,
        future_prices: np.ndarray,
    ) -> Tuple[float, float]:
        """Find optimal SL/TP for a short position."""
        if len(future_prices) == 0:
            return 1.0, 2.0

        # Maximum profit achieved (price going down)
        min_price = np.min(future_prices)
        tp_pct = (entry_price - min_price) / entry_price * 100

        # Drawdown before min (price going up = loss for short)
        min_idx = np.argmin(future_prices)
        if min_idx > 0:
            max_before_min = np.max(future_prices[:min_idx])
            sl_pct = (max_before_min - entry_price) / entry_price * 100
        else:
            sl_pct = 0.5  # Minimum SL

        # Ensure reasonable values
        sl_pct = max(0.3, sl_pct * 1.2)  # Add 20% buffer
        tp_pct = max(0.5, tp_pct * 0.9)  # Take 90% of max

        return sl_pct, tp_pct

    def _add_outcome_labels(
        self,
        df: pd.DataFrame,
        prices: np.ndarray,
    ) -> pd.DataFrame:
        """Add trade outcome labels (win/loss)."""
        n = len(prices)
        lookahead_rows = self._hours_to_rows(df, self._lookahead_hours)

        # Initialize
        outcomes = np.zeros(n, dtype=np.int8)  # 0=neutral, 1=win, -1=loss
        pnl_pct = np.zeros(n)

        for i in range(n):
            end_idx = min(i + lookahead_rows + 1, n)
            if end_idx <= i + 1:
                continue

            direction = df.loc[i, "label_direction"] if "label_direction" in df.columns else 0
            sl_pct = df.loc[i, "label_sl_pct"] if "label_sl_pct" in df.columns else 1.0
            tp_pct = df.loc[i, "label_tp_pct"] if "label_tp_pct" in df.columns else 2.0

            if direction == 0:
                outcomes[i] = 0
                pnl_pct[i] = 0
                continue

            current_price = prices[i]
            future_prices = prices[i + 1:end_idx]

            # Simulate trade outcome
            outcome, trade_pnl = self._simulate_trade(
                current_price, future_prices, direction, sl_pct, tp_pct
            )
            outcomes[i] = outcome
            pnl_pct[i] = trade_pnl

        df["label_outcome"] = outcomes
        df["label_pnl_pct"] = pnl_pct

        # Stats
        wins = (outcomes == 1).sum()
        losses = (outcomes == -1).sum()
        total_trades = wins + losses

        if total_trades > 0:
            logger.debug(
                "outcome_labels_stats",
                wins=int(wins),
                losses=int(losses),
                win_rate=wins / total_trades,
                avg_pnl=float(pnl_pct[outcomes != 0].mean()) if (outcomes != 0).any() else 0,
            )

        return df

    def _simulate_trade(
        self,
        entry_price: float,
        future_prices: np.ndarray,
        direction: int,
        sl_pct: float,
        tp_pct: float,
    ) -> Tuple[int, float]:
        """
        Simulate a trade to determine outcome.

        Args:
            entry_price: Entry price
            future_prices: Future price array
            direction: 1 for long, -1 for short
            sl_pct: Stop-loss percentage
            tp_pct: Take-profit percentage

        Returns:
            Tuple of (outcome, pnl_pct)
            outcome: 1=win, -1=loss, 0=timeout
        """
        if len(future_prices) == 0:
            return 0, 0.0

        if direction == 1:  # Long
            sl_price = entry_price * (1 - sl_pct / 100)
            tp_price = entry_price * (1 + tp_pct / 100)

            for price in future_prices:
                if price <= sl_price:
                    return -1, -sl_pct
                if price >= tp_price:
                    return 1, tp_pct

        else:  # Short
            sl_price = entry_price * (1 + sl_pct / 100)
            tp_price = entry_price * (1 - tp_pct / 100)

            for price in future_prices:
                if price >= sl_price:
                    return -1, -sl_pct
                if price <= tp_price:
                    return 1, tp_pct

        # Timeout - calculate final PnL
        final_price = future_prices[-1]
        if direction == 1:
            pnl = (final_price - entry_price) / entry_price * 100
        else:
            pnl = (entry_price - final_price) / entry_price * 100

        return 0, pnl

    def get_label_statistics(self, df: pd.DataFrame) -> Dict:
        """
        Get statistics about the labels.

        Args:
            df: Labeled DataFrame

        Returns:
            Dictionary with label statistics
        """
        stats = {}

        if "label_direction" in df.columns:
            direction = df["label_direction"]
            stats["direction"] = {
                "long": int((direction == 1).sum()),
                "short": int((direction == -1).sum()),
                "neutral": int((direction == 0).sum()),
                "total": len(direction),
            }

        if "label_outcome" in df.columns:
            outcome = df["label_outcome"]
            total_trades = (outcome != 0).sum()
            stats["outcome"] = {
                "wins": int((outcome == 1).sum()),
                "losses": int((outcome == -1).sum()),
                "timeout": int((outcome == 0).sum()),
                "win_rate": (outcome == 1).sum() / total_trades if total_trades > 0 else 0,
            }

        if "label_pnl_pct" in df.columns:
            pnl = df["label_pnl_pct"]
            traded = pnl[df["label_outcome"] != 0] if "label_outcome" in df.columns else pnl
            stats["pnl"] = {
                "mean": float(traded.mean()) if len(traded) > 0 else 0,
                "std": float(traded.std()) if len(traded) > 0 else 0,
                "min": float(traded.min()) if len(traded) > 0 else 0,
                "max": float(traded.max()) if len(traded) > 0 else 0,
            }

        if "label_sl_pct" in df.columns:
            stats["sl"] = {
                "mean": float(df["label_sl_pct"].mean()),
                "std": float(df["label_sl_pct"].std()),
            }

        if "label_tp_pct" in df.columns:
            stats["tp"] = {
                "mean": float(df["label_tp_pct"].mean()),
                "std": float(df["label_tp_pct"].std()),
            }

        return stats
