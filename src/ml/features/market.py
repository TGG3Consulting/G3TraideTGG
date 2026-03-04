# -*- coding: utf-8 -*-
"""
Market Features for ML System.

Extracts market-related features from order book and trade data:
- Order book imbalance
- Spread analysis
- Trade flow features
- Liquidity metrics

Usage:
    extractor = MarketFeatureExtractor(futures_monitor)
    features = extractor.extract(symbol)
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, TYPE_CHECKING

import numpy as np
import structlog

from config.settings import settings

if TYPE_CHECKING:
    from src.screener.futures_monitor import FuturesMonitor
    from src.screener.realtime_monitor import RealTimeMonitor


logger = structlog.get_logger(__name__)


@dataclass
class MarketFeatures:
    """Market-related features for ML."""

    # Order book features
    book_imbalance: float = 0.0  # (bid - ask) / (bid + ask)
    bid_depth_usd: float = 0.0
    ask_depth_usd: float = 0.0
    spread_pct: float = 0.0
    spread_to_volatility: float = 0.0  # spread / ATR

    # Trade flow features
    buy_ratio_5m: float = 0.5
    buy_ratio_15m: float = 0.5
    trade_count_5m: int = 0
    avg_trade_size: float = 0.0
    large_trade_ratio: float = 0.0  # % of volume from large trades

    # Volume features
    volume_5m: float = 0.0
    volume_1h: float = 0.0
    volume_spike_ratio: float = 1.0
    vwap_deviation_pct: float = 0.0

    # Liquidity features
    liquidity_score: float = 0.5
    depth_ratio_1pct: float = 1.0  # ask/bid ratio at 1%
    depth_ratio_2pct: float = 1.0

    def to_array(self) -> np.ndarray:
        """Convert to numpy array."""
        return np.array([
            self.book_imbalance,
            self.bid_depth_usd,
            self.ask_depth_usd,
            self.spread_pct,
            self.spread_to_volatility,
            self.buy_ratio_5m,
            self.buy_ratio_15m,
            self.trade_count_5m,
            self.avg_trade_size,
            self.large_trade_ratio,
            self.volume_5m,
            self.volume_1h,
            self.volume_spike_ratio,
            self.vwap_deviation_pct,
            self.liquidity_score,
            self.depth_ratio_1pct,
            self.depth_ratio_2pct,
        ])

    @staticmethod
    def feature_names() -> List[str]:
        """Get feature names."""
        return [
            "book_imbalance",
            "bid_depth_usd",
            "ask_depth_usd",
            "spread_pct",
            "spread_to_volatility",
            "buy_ratio_5m",
            "buy_ratio_15m",
            "trade_count_5m",
            "avg_trade_size",
            "large_trade_ratio",
            "volume_5m",
            "volume_1h",
            "volume_spike_ratio",
            "vwap_deviation_pct",
            "liquidity_score",
            "depth_ratio_1pct",
            "depth_ratio_2pct",
        ]


class MarketFeatureExtractor:
    """
    Extracts market microstructure features.

    Uses RealTimeMonitor for trade/orderbook data.
    """

    def __init__(
        self,
        realtime_monitor: Optional["RealTimeMonitor"] = None,
    ):
        """
        Initialize extractor.

        Args:
            realtime_monitor: RealTimeMonitor instance for market data
        """
        self._monitor = realtime_monitor
        self._config = settings.ml.features

        # Large trade threshold (relative to average)
        self._large_trade_multiplier = 5.0

        logger.info(
            "market_feature_extractor_init",
            has_monitor=realtime_monitor is not None,
        )

    def extract(self, symbol: str) -> MarketFeatures:
        """
        Extract market features for a symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            MarketFeatures dataclass
        """
        if self._monitor is None:
            logger.warning("no_monitor_for_market_features")
            return MarketFeatures()

        state = self._monitor.get_state(symbol)
        if state is None:
            logger.debug("no_state_for_symbol", symbol=symbol)
            return MarketFeatures()

        features = MarketFeatures()

        # Order book features
        if state.bid_volume_20 is not None and state.ask_volume_20 is not None:
            total = state.bid_volume_20 + state.ask_volume_20
            if total > 0:
                features.book_imbalance = float(
                    (state.bid_volume_20 - state.ask_volume_20) / total
                )
            features.bid_depth_usd = float(state.bid_volume_20)
            features.ask_depth_usd = float(state.ask_volume_20)

        # Spread
        if state.spread_pct is not None:
            features.spread_pct = float(state.spread_pct)

        # Trade flow
        if state.buy_ratio_5m is not None:
            features.buy_ratio_5m = float(state.buy_ratio_5m)

        if state.trades_5m is not None:
            features.trade_count_5m = len(state.trades_5m)

            if features.trade_count_5m > 0:
                # Calculate average trade size
                sizes = [float(t.get("qty", 0)) for t in state.trades_5m]
                features.avg_trade_size = np.mean(sizes) if sizes else 0

                # Calculate large trade ratio
                avg_size = features.avg_trade_size
                large_threshold = avg_size * self._large_trade_multiplier
                large_volume = sum(s for s in sizes if s > large_threshold)
                total_volume = sum(sizes)
                if total_volume > 0:
                    features.large_trade_ratio = large_volume / total_volume

        # Volume features
        if state.volume_5m is not None:
            features.volume_5m = float(state.volume_5m)

        if state.volume_1h is not None:
            features.volume_1h = float(state.volume_1h)

        if state.volume_spike_ratio is not None:
            features.volume_spike_ratio = float(state.volume_spike_ratio)

        # Liquidity score (simple heuristic)
        if features.bid_depth_usd > 0 and features.ask_depth_usd > 0:
            # Higher depth and tighter spread = better liquidity
            depth_score = min(1.0, (features.bid_depth_usd + features.ask_depth_usd) / 1000000)
            spread_score = max(0, 1 - features.spread_pct * 100)  # 1% spread = 0 score
            features.liquidity_score = (depth_score + spread_score) / 2

        logger.debug(
            "extracted_market_features",
            symbol=symbol,
            book_imbalance=features.book_imbalance,
            spread_pct=features.spread_pct,
        )

        return features

    def extract_batch(self, symbols: List[str]) -> Dict[str, MarketFeatures]:
        """
        Extract features for multiple symbols.

        Args:
            symbols: List of symbols

        Returns:
            Dict mapping symbol to features
        """
        return {symbol: self.extract(symbol) for symbol in symbols}
