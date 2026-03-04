# -*- coding: utf-8 -*-
"""
XLSX Loader for ML Training.

Loads backtest results from XLSX file (already computed).
No need to re-run backtest!

Usage:
    loader = XLSXLoader()
    df = loader.load("backtester/output/backtest_20260219_214242.xlsx")
"""

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import structlog


logger = structlog.get_logger(__name__)


class XLSXLoader:
    """
    Loads backtest results from XLSX for ML training.

    XLSX columns:
    - Signal ID, Symbol, Timestamp, Direction, Prob, Conf, R/R
    - Entry Limit, Stop Loss, SL %, TP1, TP1 %, TP2, TP2 %, TP3, TP3 %
    - Filled, Exit Reason, Net PnL, PnL %, Net %
    - TP1 Hit, TP2 Hit, TP3 Hit, SL Hit, Hours
    """

    def load(
        self,
        xlsx_path: str,
        filled_only: bool = True,
    ) -> pd.DataFrame:
        """
        Load backtest results from XLSX.

        Args:
            xlsx_path: Path to XLSX file
            filled_only: Only include filled trades (default: True)

        Returns:
            DataFrame with features and labels
        """
        logger.info("xlsx_load_start", path=xlsx_path)

        df = pd.read_excel(xlsx_path)

        logger.info("xlsx_loaded", rows=len(df))

        # Filter filled only
        if filled_only:
            df = df[df["Filled"] == "YES"].copy()
            logger.info("filtered_filled", rows=len(df))

        # Rename columns to match training format
        df = df.rename(columns={
            "Signal ID": "signal_id",
            "Symbol": "symbol",
            "Timestamp": "timestamp",
            "Direction": "direction",
            "Prob": "probability",
            "Conf": "confidence",
            "R/R": "risk_reward",
            "SL %": "stop_loss_pct",
            "TP1 %": "tp1_pct",
            "TP2 %": "tp2_pct",
            "TP3 %": "tp3_pct",
            "Filled": "entry_filled",
            "Exit Reason": "label_exit_reason",
            "Net PnL": "label_net_pnl",
            "PnL %": "label_pnl_pct",
            "Net %": "label_net_pnl_pct",
            "Hours": "label_hold_hours",
            "TP1 Hit": "label_tp1_hit",
            "TP2 Hit": "label_tp2_hit",
            "TP3 Hit": "label_tp3_hit",
            "SL Hit": "label_sl_hit",
        })

        # Create label_win (target variable)
        df["label_win"] = (df["label_net_pnl_pct"] > 0).astype(int)

        # Convert entry_filled to bool
        df["entry_filled"] = df["entry_filled"] == "YES"

        # Convert direction to numeric
        df["direction_num"] = (df["direction"] == "LONG").astype(int)

        # Fill NaN in hit columns
        for col in ["label_tp1_hit", "label_tp2_hit", "label_tp3_hit", "label_sl_hit"]:
            if col in df.columns:
                df[col] = df[col].fillna(False)
                df[col] = df[col].apply(lambda x: x == "YES" if isinstance(x, str) else bool(x))

        # Log statistics
        wins = (df["label_win"] == 1).sum()
        losses = (df["label_win"] == 0).sum()

        logger.info(
            "xlsx_processed",
            total=len(df),
            wins=wins,
            losses=losses,
            win_rate=wins / len(df) if len(df) > 0 else 0,
        )

        return df

    def get_feature_columns(self, df: pd.DataFrame) -> list:
        """Get feature columns for training."""
        feature_cols = [
            "probability",
            "risk_reward",
            "stop_loss_pct",
            "tp1_pct",
            "tp2_pct",
            "tp3_pct",
            "direction_num",
        ]

        # Only include columns that exist and are numeric
        available = []
        for col in feature_cols:
            if col in df.columns:
                if df[col].dtype in [np.float64, np.float32, np.int64, np.int32]:
                    available.append(col)

        return available
