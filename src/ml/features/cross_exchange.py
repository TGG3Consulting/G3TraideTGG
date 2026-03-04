# -*- coding: utf-8 -*-
"""
Cross-Exchange Features for ML System.

Extracts features from cross-exchange data:
- Price divergence between exchanges
- Funding rate arbitrage
- OI migration patterns
- Volume correlation

Usage:
    extractor = CrossExchangeFeatureExtractor(state_store)
    features = extractor.extract(symbol)
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Set, TYPE_CHECKING

import numpy as np
import structlog

from config.settings import settings

if TYPE_CHECKING:
    from src.cross_exchange.state_store import StateStore


logger = structlog.get_logger(__name__)


# Known exchanges for cross-exchange analysis
SUPPORTED_EXCHANGES: Set[str] = {
    "binance",
    "bybit",
    "okx",
    "deribit",
    "bitget",
}


@dataclass
class CrossExchangeFeatures:
    """Cross-exchange related features for ML."""

    # Price divergence
    max_price_divergence_pct: float = 0.0
    avg_price_divergence_pct: float = 0.0
    binance_price_lead: float = 0.0  # How often Binance leads

    # Funding divergence
    funding_divergence: float = 0.0
    min_funding_rate: float = 0.0
    max_funding_rate: float = 0.0
    funding_arbitrage_pct: float = 0.0

    # OI distribution
    oi_concentration: float = 0.0  # % of OI on main exchange
    oi_migration_rate: float = 0.0  # Change in concentration

    # Volume correlation
    volume_correlation: float = 0.0
    volume_lead_exchange: str = ""

    # Exchange count
    active_exchanges: int = 0

    def to_array(self) -> np.ndarray:
        """Convert to numpy array."""
        return np.array([
            self.max_price_divergence_pct,
            self.avg_price_divergence_pct,
            self.binance_price_lead,
            self.funding_divergence,
            self.min_funding_rate,
            self.max_funding_rate,
            self.funding_arbitrage_pct,
            self.oi_concentration,
            self.oi_migration_rate,
            self.volume_correlation,
            self.active_exchanges,
        ])

    @staticmethod
    def feature_names() -> List[str]:
        """Get feature names."""
        return [
            "max_price_divergence_pct",
            "avg_price_divergence_pct",
            "binance_price_lead",
            "funding_divergence",
            "min_funding_rate",
            "max_funding_rate",
            "funding_arbitrage_pct",
            "oi_concentration",
            "oi_migration_rate",
            "volume_correlation",
            "active_exchanges",
        ]


class CrossExchangeFeatureExtractor:
    """
    Extracts cross-exchange features.

    Uses StateStore for multi-exchange data.
    """

    def __init__(
        self,
        state_store: Optional["StateStore"] = None,
    ):
        """
        Initialize extractor.

        Args:
            state_store: StateStore instance for cross-exchange data
        """
        self._store = state_store
        self._config = settings.ml.features

        logger.info(
            "cross_exchange_extractor_init",
            has_store=state_store is not None,
        )

    def extract(self, symbol: str) -> CrossExchangeFeatures:
        """
        Extract cross-exchange features for a symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            CrossExchangeFeatures dataclass
        """
        if self._store is None:
            logger.warning("no_store_for_cross_features")
            return CrossExchangeFeatures()

        features = CrossExchangeFeatures()

        try:
            # Get snapshot from state store
            snapshot = self._store.get_symbol_snapshot(symbol)
            if snapshot is None:
                return features

            # Extract prices from each exchange
            prices = {}
            for exchange in SUPPORTED_EXCHANGES:
                price = snapshot.get(f"{exchange}_price")
                if price and price > 0:
                    prices[exchange] = float(price)

            features.active_exchanges = len(prices)

            if len(prices) < 2:
                return features

            # Price divergence
            price_values = list(prices.values())
            avg_price = np.mean(price_values)

            if avg_price > 0:
                divergences = [abs(p - avg_price) / avg_price * 100 for p in price_values]
                features.max_price_divergence_pct = max(divergences)
                features.avg_price_divergence_pct = np.mean(divergences)

            # Binance lead (is Binance price higher/lower than average?)
            binance_price = prices.get("binance")
            if binance_price and avg_price > 0:
                features.binance_price_lead = (binance_price - avg_price) / avg_price * 100

            # Funding rates
            funding_rates = {}
            for exchange in SUPPORTED_EXCHANGES:
                funding = snapshot.get(f"{exchange}_funding")
                if funding is not None:
                    funding_rates[exchange] = float(funding)

            if funding_rates:
                fr_values = list(funding_rates.values())
                features.min_funding_rate = min(fr_values)
                features.max_funding_rate = max(fr_values)
                features.funding_divergence = features.max_funding_rate - features.min_funding_rate
                features.funding_arbitrage_pct = abs(features.funding_divergence) * 100

            # OI concentration
            oi_values = {}
            for exchange in SUPPORTED_EXCHANGES:
                oi = snapshot.get(f"{exchange}_oi")
                if oi and oi > 0:
                    oi_values[exchange] = float(oi)

            if oi_values:
                total_oi = sum(oi_values.values())
                if total_oi > 0:
                    # Concentration on largest exchange
                    max_oi = max(oi_values.values())
                    features.oi_concentration = max_oi / total_oi

            logger.debug(
                "extracted_cross_features",
                symbol=symbol,
                exchanges=features.active_exchanges,
                price_div=features.max_price_divergence_pct,
            )

        except Exception as e:
            logger.warning(
                "cross_feature_extraction_error",
                symbol=symbol,
                error=str(e),
            )

        return features

    def extract_batch(self, symbols: List[str]) -> Dict[str, CrossExchangeFeatures]:
        """
        Extract features for multiple symbols.

        Args:
            symbols: List of symbols

        Returns:
            Dict mapping symbol to features
        """
        return {symbol: self.extract(symbol) for symbol in symbols}

    def calculate_oi_migration(
        self,
        symbol: str,
        lookback_hours: int = 24,
    ) -> float:
        """
        Calculate OI migration rate.

        Positive = OI moving TO main exchange
        Negative = OI moving AWAY from main exchange

        Args:
            symbol: Trading pair
            lookback_hours: Hours to look back

        Returns:
            OI migration rate (-1 to 1)
        """
        if self._store is None:
            return 0.0

        try:
            # Get historical OI data
            # This would require historical data in state store
            # For now, return 0 (neutral)
            return 0.0

        except Exception as e:
            logger.warning("oi_migration_calc_error", error=str(e))
            return 0.0

    def detect_price_leader(
        self,
        symbol: str,
        window_seconds: int = 60,
    ) -> str:
        """
        Detect which exchange is leading price.

        Args:
            symbol: Trading pair
            window_seconds: Time window for analysis

        Returns:
            Exchange name that tends to lead
        """
        # This would require tick-level data and correlation analysis
        # Simplified: return "binance" as default
        return "binance"
