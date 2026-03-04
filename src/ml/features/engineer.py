# -*- coding: utf-8 -*-
"""
Feature Engineer for ML Models.

Combines multiple feature sources:
- Technical indicators (RSI, MACD, BB, ATR, etc.)
- Market features (volume, OI, funding, L/S ratio)
- Cross-exchange features (divergence, concentration)

Usage:
    engineer = FeatureEngineer(futures_monitor, state_store)
    features = engineer.extract_features(df)
"""

import numpy as np
import pandas as pd
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, TYPE_CHECKING
import structlog

from config.settings import settings
from src.ml.data.schemas import FeatureVector, MarketSnapshot
from .technical import TechnicalIndicators

if TYPE_CHECKING:
    from src.screener.futures_monitor import FuturesMonitor
    from src.cross_exchange.state_store import StateStore


logger = structlog.get_logger(__name__)


class FeatureEngineer:
    """
    Generates ML features from market data.

    Integrates with existing BinanceFriend components
    for real-time feature extraction.
    """

    def __init__(
        self,
        futures_monitor: Optional["FuturesMonitor"] = None,
        state_store: Optional["StateStore"] = None,
    ):
        """
        Initialize feature engineer.

        Args:
            futures_monitor: Existing FuturesMonitor for OI/Funding data
            state_store: Existing StateStore for cross-exchange data
        """
        self._config = settings.ml.features
        self._futures_monitor = futures_monitor
        self._state_store = state_store
        self._technical = TechnicalIndicators()

        logger.info(
            "feature_engineer_init",
            has_futures_monitor=futures_monitor is not None,
            has_state_store=state_store is not None,
        )

    def extract_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Extract all features from DataFrame.

        Args:
            df: DataFrame with market data (from preprocessor)

        Returns:
            DataFrame with all features added
        """
        if df.empty:
            return df

        logger.debug("extracting_features", rows=len(df))

        df = df.copy()

        # Add technical indicators
        df = self._add_technical_features(df)

        # Add market features
        df = self._add_market_features(df)

        # Add cross-exchange features
        df = self._add_cross_exchange_features(df)

        # Add time features
        df = self._add_time_features(df)

        # Add lagged features
        df = self._add_lagged_features(df)

        # Add rolling statistics
        df = self._add_rolling_features(df)

        # Clean up any NaN introduced
        df = df.fillna(0)

        logger.info(
            "features_extracted",
            rows=len(df),
            feature_count=len(df.columns),
        )

        return df

    def _add_technical_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add technical indicator features."""
        # Get price column
        price_col = "close" if "close" in df.columns else "price"

        if price_col not in df.columns:
            logger.warning("no_price_column_for_technical")
            return df

        # Use TechnicalIndicators class
        df = self._technical.add_all_indicators(df)

        return df

    def _add_market_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add market-specific features (OI, funding, etc.)."""
        # OI features (if present)
        if "open_interest" in df.columns:
            df["oi_log"] = np.log1p(df["open_interest"])

        if "oi_change_1h_pct" in df.columns:
            # OI momentum
            df["oi_momentum"] = df["oi_change_1h_pct"].rolling(window=4).mean()

            # OI acceleration
            df["oi_acceleration"] = df["oi_change_1h_pct"].diff()

        # Funding features
        if "funding_rate" in df.columns:
            # Funding direction
            df["funding_positive"] = (df["funding_rate"] > 0).astype(int)

            # Funding extremes
            df["funding_extreme_long"] = (df["funding_rate"] > 0.0005).astype(int)
            df["funding_extreme_short"] = (df["funding_rate"] < -0.0005).astype(int)

            # Funding momentum
            df["funding_momentum"] = df["funding_rate"].rolling(window=8).mean()

        # Long/Short ratio features
        if "long_short_ratio" in df.columns:
            df["ls_ratio_normalized"] = df["long_short_ratio"] - 1  # Center at 0

            # Crowd positioning
            df["crowd_long"] = (df["long_short_ratio"] > 1.2).astype(int)
            df["crowd_short"] = (df["long_short_ratio"] < 0.8).astype(int)

        # Volume features
        if "volume_spike_ratio" in df.columns:
            df["volume_spike_log"] = np.log1p(df["volume_spike_ratio"])

        # Book imbalance features
        if "book_imbalance" in df.columns:
            # Imbalance direction
            df["book_bid_heavy"] = (df["book_imbalance"] > 0.6).astype(int)
            df["book_ask_heavy"] = (df["book_imbalance"] < 0.4).astype(int)

            # Imbalance strength
            df["book_imbalance_strength"] = (df["book_imbalance"] - 0.5).abs() * 2

        return df

    def _add_cross_exchange_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add cross-exchange features."""
        # Price divergence
        if "price_divergence_pct" in df.columns:
            df["price_divergence_significant"] = (
                df["price_divergence_pct"].abs() > 0.1
            ).astype(int)

        # Funding divergence
        if "funding_divergence" in df.columns:
            df["funding_arb_opportunity"] = (
                df["funding_divergence"].abs() > 0.01
            ).astype(int)

        # OI concentration
        if "oi_concentration" in df.columns:
            df["oi_concentrated"] = (df["oi_concentration"] > 0.7).astype(int)

        return df

    def _add_time_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add time-based features."""
        if "timestamp" not in df.columns:
            return df

        # Ensure timestamp is datetime
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"])

        # Hour of day (cyclical encoding)
        hour = df["timestamp"].dt.hour
        df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
        df["hour_cos"] = np.cos(2 * np.pi * hour / 24)

        # Day of week (cyclical encoding)
        dow = df["timestamp"].dt.dayofweek
        df["dow_sin"] = np.sin(2 * np.pi * dow / 7)
        df["dow_cos"] = np.cos(2 * np.pi * dow / 7)

        # Session features (approximate)
        df["is_asia_session"] = ((hour >= 0) & (hour < 8)).astype(int)
        df["is_europe_session"] = ((hour >= 7) & (hour < 16)).astype(int)
        df["is_us_session"] = ((hour >= 13) & (hour < 22)).astype(int)

        # Weekend
        df["is_weekend"] = (dow >= 5).astype(int)

        return df

    def _add_lagged_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add lagged versions of key features."""
        # Key features to lag
        lag_features = [
            "price_change_1h_pct",
            "oi_change_1h_pct",
            "funding_rate",
            "volume_spike_ratio",
        ]

        lags = [1, 2, 4]  # 1h, 2h, 4h lags for hourly data

        for feature in lag_features:
            if feature not in df.columns:
                continue

            for lag in lags:
                df[f"{feature}_lag{lag}"] = df[feature].shift(lag)

        return df

    def _add_rolling_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add rolling statistics."""
        windows = self._config.windows_minutes

        # For hourly data, convert minute windows to row counts
        # Assuming 1h intervals, 60 min = 1 row
        hour_windows = [max(1, w // 60) for w in windows]
        hour_windows = list(set(hour_windows))  # Unique

        price_col = "close" if "close" in df.columns else "price"

        if price_col in df.columns:
            for w in hour_windows:
                if w > 1:
                    # Rolling mean of price change
                    if "price_change_1h_pct" in df.columns:
                        df[f"price_change_mean_{w}h"] = (
                            df["price_change_1h_pct"].rolling(window=w).mean()
                        )
                        df[f"price_change_std_{w}h"] = (
                            df["price_change_1h_pct"].rolling(window=w).std()
                        )

                    # Rolling max/min
                    df[f"price_max_{w}h"] = df[price_col].rolling(window=w).max()
                    df[f"price_min_{w}h"] = df[price_col].rolling(window=w).min()

                    # Price range
                    df[f"price_range_{w}h_pct"] = (
                        (df[f"price_max_{w}h"] - df[f"price_min_{w}h"])
                        / df[price_col] * 100
                    )

        return df

    def get_realtime_features(self, symbol: str) -> Optional[FeatureVector]:
        """
        Get real-time features for a symbol using existing monitors.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")

        Returns:
            FeatureVector or None if data unavailable
        """
        if not self._futures_monitor:
            logger.warning("no_futures_monitor_for_realtime_features")
            return None

        # Get current state
        state = self._futures_monitor.get_state(symbol)
        if not state:
            return None

        try:
            features = {}
            feature_names = []

            # Price features
            if state.current_funding:
                price = float(state.current_funding.mark_price)
                features["price"] = price

            # OI features
            if state.current_oi:
                features["oi_change_1h_pct"] = float(state.oi_change_1h_pct)
                features["oi_change_5m_pct"] = float(state.oi_change_5m_pct)

            # Funding features
            if state.current_funding:
                funding = float(state.current_funding.funding_rate)
                features["funding_rate"] = funding
                features["funding_positive"] = 1 if funding > 0 else 0
                features["funding_extreme"] = 1 if abs(funding) > 0.0005 else 0

            # L/S ratio features
            if state.current_ls_ratio:
                ls_ratio = float(state.current_ls_ratio.long_short_ratio)
                features["long_short_ratio"] = ls_ratio
                features["crowd_long"] = 1 if ls_ratio > 1.2 else 0
                features["crowd_short"] = 1 if ls_ratio < 0.8 else 0

            # Cross-exchange features
            if self._state_store:
                try:
                    price_spread = self._state_store.get_price_spread(symbol)
                    if price_spread and "_max_spread_pct" in price_spread:
                        features["price_divergence_pct"] = float(price_spread["_max_spread_pct"])

                    funding_div = self._state_store.get_funding_divergence(symbol)
                    if funding_div and "_max_divergence" in funding_div:
                        features["funding_divergence"] = float(funding_div["_max_divergence"])
                except Exception:
                    pass

            # Build feature vector
            feature_names = list(features.keys())
            feature_values = np.array(list(features.values()), dtype=np.float32)

            return FeatureVector(
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                features=feature_values,
                feature_names=feature_names,
            )

        except Exception as e:
            logger.error("realtime_feature_extraction_error", symbol=symbol, error=str(e))
            return None

    def get_feature_names(self) -> List[str]:
        """Get list of all feature names generated."""
        # This would be populated after running extract_features
        # For now return common feature names
        base_features = [
            "price_change_1m_pct", "price_change_5m_pct", "price_change_15m_pct",
            "price_change_1h_pct", "price_change_4h_pct",
            "volume_spike_ratio", "volume_spike_log",
            "open_interest", "oi_log", "oi_change_1h_pct", "oi_momentum", "oi_acceleration",
            "funding_rate", "funding_positive", "funding_extreme_long", "funding_extreme_short",
            "long_short_ratio", "ls_ratio_normalized", "crowd_long", "crowd_short",
            "book_imbalance", "book_bid_heavy", "book_ask_heavy", "book_imbalance_strength",
            "rsi", "macd", "macd_signal", "macd_hist", "bb_position",
            "atr_pct", "momentum_10", "momentum_20", "volatility_20",
            "hour_sin", "hour_cos", "dow_sin", "dow_cos",
            "is_asia_session", "is_europe_session", "is_us_session", "is_weekend",
        ]
        return base_features
