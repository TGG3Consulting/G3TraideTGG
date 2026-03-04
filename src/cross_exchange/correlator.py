# -*- coding: utf-8 -*-
"""
Cross-Exchange Correlator.

Analyzes data across exchanges to detect correlation patterns
and anomalies that indicate manipulation.

Correlation types:
1. Price divergence - when prices differ significantly between exchanges
2. Volume correlation - when volume patterns are suspiciously similar
3. Funding divergence - when funding rates create arbitrage opportunity
4. OI divergence - when OI changes don't match across exchanges
5. Lead-lag relationships - when one exchange leads price movements

Usage:
    store = StateStore()
    correlator = Correlator(store)

    # Check for price divergence
    result = correlator.check_price_divergence("BTC/USDT")
    if result.is_significant:
        print(f"Price divergence detected: {result.spread_pct}%")
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Tuple

import structlog

from src.cross_exchange.state_store import StateStore, SymbolSnapshot
from src.exchanges.models import CrossExchangePrice, CrossExchangeFunding
from src.cross_exchange.detectors.base import (
    BaseCrossDetector,
    Detection,
    DetectionType,
    Severity,
)
from src.cross_exchange.detectors.price_divergence import (
    PriceDivergenceDetector,
    PriceDivergenceConfig,
)
from src.cross_exchange.detectors.volume_correlation import (
    VolumeCorrelationDetector,
    VolumeCorrelationConfig,
)
from src.cross_exchange.detectors.funding_arbitrage import (
    FundingArbitrageDetector,
    FundingArbitrageConfig,
)
from src.cross_exchange.detectors.oi_migration import (
    OIMigrationDetector,
    OIMigrationConfig,
)
from src.cross_exchange.detectors.liquidity_hunt import (
    LiquidityHuntDetector,
    LiquidityHuntConfig,
)
from src.cross_exchange.detectors.spoofing_cross import (
    SpoofingCrossDetector,
    SpoofingCrossConfig,
)

logger = structlog.get_logger(__name__)


# =============================================================================
# ENUMS
# =============================================================================

class CorrelationType(str, Enum):
    """Types of cross-exchange correlation/divergence."""
    PRICE_DIVERGENCE = "PRICE_DIVERGENCE"
    VOLUME_CORRELATION = "VOLUME_CORRELATION"
    FUNDING_ARBITRAGE = "FUNDING_ARBITRAGE"
    OI_DIVERGENCE = "OI_DIVERGENCE"
    LEAD_LAG = "LEAD_LAG"
    LIQUIDATION_CASCADE = "LIQUIDATION_CASCADE"


class SignificanceLevel(str, Enum):
    """Significance level of correlation."""
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# =============================================================================
# RESULT DATACLASSES
# =============================================================================

@dataclass
class CorrelationResult:
    """
    Result of a cross-exchange correlation check.

    Contains details about the detected pattern.
    """
    correlation_type: CorrelationType
    symbol: str
    timestamp: datetime
    significance: SignificanceLevel

    # Exchanges involved
    exchanges: List[str]
    primary_exchange: Optional[str] = None  # Leader or source
    secondary_exchange: Optional[str] = None  # Follower or target

    # Metrics
    spread_pct: Optional[Decimal] = None
    correlation_coef: Optional[float] = None
    lag_seconds: Optional[float] = None
    value_usd: Optional[Decimal] = None

    # Details
    details: Dict = field(default_factory=dict)
    raw_data: Dict = field(default_factory=dict)

    @property
    def is_significant(self) -> bool:
        """True if significance is MEDIUM or higher."""
        return self.significance in (
            SignificanceLevel.MEDIUM,
            SignificanceLevel.HIGH,
            SignificanceLevel.CRITICAL
        )

    def to_alert_message(self) -> str:
        """Format as alert message."""
        lines = [
            f"🔗 {self.correlation_type.value}",
            f"Symbol: {self.symbol}",
            f"Significance: {self.significance.value}",
            f"Exchanges: {', '.join(self.exchanges)}",
        ]

        if self.spread_pct:
            lines.append(f"Spread: {self.spread_pct:.3f}%")
        if self.lag_seconds:
            lines.append(f"Lag: {self.lag_seconds:.1f}s")
        if self.value_usd:
            lines.append(f"Value: ${self.value_usd:,.0f}")

        return "\n".join(lines)


@dataclass
class PriceDivergenceResult(CorrelationResult):
    """Specific result for price divergence."""
    highest_price: Optional[Decimal] = None
    lowest_price: Optional[Decimal] = None
    highest_exchange: Optional[str] = None
    lowest_exchange: Optional[str] = None
    vwap: Optional[Decimal] = None


@dataclass
class FundingArbitrageResult(CorrelationResult):
    """Specific result for funding arbitrage."""
    long_exchange: Optional[str] = None
    short_exchange: Optional[str] = None
    long_rate: Optional[Decimal] = None
    short_rate: Optional[Decimal] = None
    annualized_return: Optional[Decimal] = None


# =============================================================================
# CORRELATOR
# =============================================================================

class Correlator:
    """
    Cross-exchange correlation analyzer.

    Analyzes data from StateStore to detect patterns across exchanges.
    """

    def __init__(
        self,
        state_store: StateStore,
        config: Optional[Dict] = None
    ):
        """
        Initialize correlator.

        Args:
            state_store: StateStore instance with exchange data
            config: Optional configuration overrides
        """
        self.store = state_store
        self.logger = logger.bind(component="correlator")

        # Default thresholds
        self.config = {
            # Price divergence thresholds
            "price_divergence_low": Decimal("0.1"),      # 0.1%
            "price_divergence_medium": Decimal("0.3"),   # 0.3%
            "price_divergence_high": Decimal("0.5"),     # 0.5%
            "price_divergence_critical": Decimal("1.0"), # 1.0%

            # Funding arbitrage thresholds
            "funding_spread_low": Decimal("0.01"),       # 0.01%
            "funding_spread_medium": Decimal("0.03"),    # 0.03%
            "funding_spread_high": Decimal("0.05"),      # 0.05%
            "funding_spread_critical": Decimal("0.1"),   # 0.1%

            # OI divergence thresholds
            "oi_divergence_low": Decimal("5"),           # 5%
            "oi_divergence_medium": Decimal("10"),       # 10%
            "oi_divergence_high": Decimal("20"),         # 20%
            "oi_divergence_critical": Decimal("30"),     # 30%

            # Volume correlation threshold
            "volume_correlation_suspicious": 0.95,       # r > 0.95

            # Lead-lag detection
            "lead_lag_min_seconds": 0.5,
            "lead_lag_max_seconds": 30.0,

            # Minimum exchanges for analysis
            "min_exchanges": 2,
        }

        if config:
            self.config.update(config)

    # -------------------------------------------------------------------------
    # Price Divergence
    # -------------------------------------------------------------------------

    def check_price_divergence(
        self,
        symbol: str,
        exchanges: Optional[List[str]] = None
    ) -> PriceDivergenceResult:
        """
        Check for price divergence across exchanges.

        Price divergence indicates potential arbitrage opportunity
        or manipulation when prices differ significantly.

        Args:
            symbol: Unified symbol to check
            exchanges: Optional list of exchanges (default: all)

        Returns:
            PriceDivergenceResult with divergence details
        """
        cross_price = self.store.get_cross_price(symbol)

        if len(cross_price.prices) < self.config["min_exchanges"]:
            return PriceDivergenceResult(
                correlation_type=CorrelationType.PRICE_DIVERGENCE,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                significance=SignificanceLevel.NONE,
                exchanges=list(cross_price.prices.keys()),
                details={"reason": "insufficient_exchanges"},
            )

        # Filter exchanges if specified
        prices = cross_price.prices
        if exchanges:
            prices = {k: v for k, v in prices.items() if k in exchanges}

        if len(prices) < 2:
            return PriceDivergenceResult(
                correlation_type=CorrelationType.PRICE_DIVERGENCE,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                significance=SignificanceLevel.NONE,
                exchanges=list(prices.keys()),
            )

        # Calculate divergence
        sorted_prices = sorted(prices.items(), key=lambda x: x[1])
        lowest_exchange, lowest_price = sorted_prices[0]
        highest_exchange, highest_price = sorted_prices[-1]

        if lowest_price == 0:
            spread_pct = Decimal(0)
        else:
            spread_pct = ((highest_price - lowest_price) / lowest_price) * 100

        # Determine significance
        if spread_pct >= self.config["price_divergence_critical"]:
            significance = SignificanceLevel.CRITICAL
        elif spread_pct >= self.config["price_divergence_high"]:
            significance = SignificanceLevel.HIGH
        elif spread_pct >= self.config["price_divergence_medium"]:
            significance = SignificanceLevel.MEDIUM
        elif spread_pct >= self.config["price_divergence_low"]:
            significance = SignificanceLevel.LOW
        else:
            significance = SignificanceLevel.NONE

        return PriceDivergenceResult(
            correlation_type=CorrelationType.PRICE_DIVERGENCE,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            significance=significance,
            exchanges=list(prices.keys()),
            primary_exchange=highest_exchange,
            secondary_exchange=lowest_exchange,
            spread_pct=spread_pct,
            highest_price=highest_price,
            lowest_price=lowest_price,
            highest_exchange=highest_exchange,
            lowest_exchange=lowest_exchange,
            vwap=cross_price.vwap,
            details={
                "all_prices": {k: str(v) for k, v in prices.items()},
            },
        )

    # -------------------------------------------------------------------------
    # Funding Arbitrage
    # -------------------------------------------------------------------------

    def check_funding_arbitrage(
        self,
        symbol: str,
        exchanges: Optional[List[str]] = None
    ) -> FundingArbitrageResult:
        """
        Check for funding rate arbitrage opportunity.

        When funding rates differ significantly between exchanges,
        traders can profit by going long where funding is low
        and short where funding is high.

        Args:
            symbol: Unified symbol to check
            exchanges: Optional list of exchanges

        Returns:
            FundingArbitrageResult with arbitrage details
        """
        cross_funding = self.store.get_cross_funding(symbol)

        if len(cross_funding.rates) < self.config["min_exchanges"]:
            return FundingArbitrageResult(
                correlation_type=CorrelationType.FUNDING_ARBITRAGE,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                significance=SignificanceLevel.NONE,
                exchanges=list(cross_funding.rates.keys()),
                details={"reason": "insufficient_exchanges"},
            )

        rates = cross_funding.rates
        if exchanges:
            rates = {k: v for k, v in rates.items() if k in exchanges}

        if len(rates) < 2:
            return FundingArbitrageResult(
                correlation_type=CorrelationType.FUNDING_ARBITRAGE,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                significance=SignificanceLevel.NONE,
                exchanges=list(rates.keys()),
            )

        # Find best arbitrage pair
        sorted_rates = sorted(rates.items(), key=lambda x: x[1])
        long_exchange, long_rate = sorted_rates[0]   # Lowest = go long
        short_exchange, short_rate = sorted_rates[-1]  # Highest = go short

        spread = short_rate - long_rate
        spread_pct = spread * 100  # Convert to percentage

        # Annualized return (assuming 8h funding, 3x per day)
        annualized = spread_pct * 365 * 3

        # Determine significance
        if spread_pct >= self.config["funding_spread_critical"]:
            significance = SignificanceLevel.CRITICAL
        elif spread_pct >= self.config["funding_spread_high"]:
            significance = SignificanceLevel.HIGH
        elif spread_pct >= self.config["funding_spread_medium"]:
            significance = SignificanceLevel.MEDIUM
        elif spread_pct >= self.config["funding_spread_low"]:
            significance = SignificanceLevel.LOW
        else:
            significance = SignificanceLevel.NONE

        return FundingArbitrageResult(
            correlation_type=CorrelationType.FUNDING_ARBITRAGE,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            significance=significance,
            exchanges=list(rates.keys()),
            primary_exchange=short_exchange,
            secondary_exchange=long_exchange,
            spread_pct=spread_pct,
            long_exchange=long_exchange,
            short_exchange=short_exchange,
            long_rate=long_rate,
            short_rate=short_rate,
            annualized_return=annualized,
            details={
                "all_rates": {k: str(v * 100) + "%" for k, v in rates.items()},
            },
        )

    # -------------------------------------------------------------------------
    # OI Divergence
    # -------------------------------------------------------------------------

    def check_oi_divergence(
        self,
        symbol: str,
        time_window_minutes: int = 60
    ) -> CorrelationResult:
        """
        Check for Open Interest divergence across exchanges.

        OI divergence occurs when OI changes significantly on one
        exchange but not others - may indicate whale accumulation
        or manipulation on specific exchange.

        Args:
            symbol: Unified symbol to check
            time_window_minutes: Time window for change calculation

        Returns:
            CorrelationResult with OI divergence details
        """
        cross_oi = self.store.get_cross_oi(symbol)

        if len(cross_oi.oi_values) < self.config["min_exchanges"]:
            return CorrelationResult(
                correlation_type=CorrelationType.OI_DIVERGENCE,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                significance=SignificanceLevel.NONE,
                exchanges=list(cross_oi.oi_values.keys()),
                details={"reason": "insufficient_exchanges"},
            )

        # Calculate OI change for each exchange
        oi_changes: Dict[str, Optional[Decimal]] = {}

        for exchange in cross_oi.oi_values:
            snap = self.store.get_symbol_snapshot(exchange, symbol)
            if snap:
                change = snap.oi_change_pct(time_window_minutes)
                oi_changes[exchange] = change

        # Filter out None values
        valid_changes = {k: v for k, v in oi_changes.items() if v is not None}

        if len(valid_changes) < 2:
            return CorrelationResult(
                correlation_type=CorrelationType.OI_DIVERGENCE,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                significance=SignificanceLevel.NONE,
                exchanges=list(cross_oi.oi_values.keys()),
                details={"reason": "insufficient_oi_history"},
            )

        # Calculate divergence (max difference in OI change)
        changes = list(valid_changes.values())
        max_change = max(changes)
        min_change = min(changes)
        divergence = abs(max_change - min_change)

        # Find exchanges with extreme changes
        max_exchange = max(valid_changes.items(), key=lambda x: x[1])[0]
        min_exchange = min(valid_changes.items(), key=lambda x: x[1])[0]

        # Determine significance
        if divergence >= self.config["oi_divergence_critical"]:
            significance = SignificanceLevel.CRITICAL
        elif divergence >= self.config["oi_divergence_high"]:
            significance = SignificanceLevel.HIGH
        elif divergence >= self.config["oi_divergence_medium"]:
            significance = SignificanceLevel.MEDIUM
        elif divergence >= self.config["oi_divergence_low"]:
            significance = SignificanceLevel.LOW
        else:
            significance = SignificanceLevel.NONE

        return CorrelationResult(
            correlation_type=CorrelationType.OI_DIVERGENCE,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            significance=significance,
            exchanges=list(valid_changes.keys()),
            primary_exchange=max_exchange,
            secondary_exchange=min_exchange,
            spread_pct=divergence,
            details={
                "oi_changes": {k: str(v) + "%" for k, v in valid_changes.items()},
                "total_oi_usd": str(cross_oi.total_oi),
                "dominant_exchange": cross_oi.dominant_exchange[0],
            },
        )

    # -------------------------------------------------------------------------
    # Lead-Lag Detection
    # -------------------------------------------------------------------------

    def check_lead_lag(
        self,
        symbol: str,
        leader: str,
        follower: str
    ) -> CorrelationResult:
        """
        Check for lead-lag relationship between two exchanges.

        A lead-lag pattern indicates one exchange consistently
        moves before another - useful for front-running detection.

        Args:
            symbol: Unified symbol to check
            leader: Suspected leading exchange
            follower: Suspected following exchange

        Returns:
            CorrelationResult with lead-lag analysis
        """
        leader_snap = self.store.get_symbol_snapshot(leader, symbol)
        follower_snap = self.store.get_symbol_snapshot(follower, symbol)

        if not leader_snap or not follower_snap:
            return CorrelationResult(
                correlation_type=CorrelationType.LEAD_LAG,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                significance=SignificanceLevel.NONE,
                exchanges=[leader, follower],
                details={"reason": "missing_data"},
            )

        # Get price histories
        leader_prices = list(leader_snap.price_history)
        follower_prices = list(follower_snap.price_history)

        if len(leader_prices) < 10 or len(follower_prices) < 10:
            return CorrelationResult(
                correlation_type=CorrelationType.LEAD_LAG,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                significance=SignificanceLevel.NONE,
                exchanges=[leader, follower],
                details={"reason": "insufficient_history"},
            )

        # Simple lag detection: compare timestamps of similar price moves
        # (Full implementation would use cross-correlation)

        # Calculate average lag
        lag_samples: List[float] = []

        for i, leader_point in enumerate(leader_prices[:-1]):
            leader_change = leader_prices[i + 1].price - leader_point.price

            if abs(leader_change) < Decimal("0.01"):
                continue

            # Find similar move in follower
            for j, follower_point in enumerate(follower_prices[:-1]):
                if follower_point.timestamp < leader_point.timestamp:
                    continue

                follower_change = follower_prices[j + 1].price - follower_point.price

                # Check if moves are similar
                if leader_change != 0:
                    similarity = follower_change / leader_change
                    if Decimal("0.8") <= similarity <= Decimal("1.2"):
                        lag = (follower_point.timestamp - leader_point.timestamp).total_seconds()
                        if self.config["lead_lag_min_seconds"] <= lag <= self.config["lead_lag_max_seconds"]:
                            lag_samples.append(lag)
                        break

        if len(lag_samples) < 5:
            return CorrelationResult(
                correlation_type=CorrelationType.LEAD_LAG,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                significance=SignificanceLevel.NONE,
                exchanges=[leader, follower],
                details={"reason": "insufficient_lag_samples", "samples": len(lag_samples)},
            )

        avg_lag = sum(lag_samples) / len(lag_samples)

        # Determine significance based on consistency
        if len(lag_samples) > 20 and avg_lag < 2:
            significance = SignificanceLevel.HIGH
        elif len(lag_samples) > 10:
            significance = SignificanceLevel.MEDIUM
        else:
            significance = SignificanceLevel.LOW

        return CorrelationResult(
            correlation_type=CorrelationType.LEAD_LAG,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            significance=significance,
            exchanges=[leader, follower],
            primary_exchange=leader,
            secondary_exchange=follower,
            lag_seconds=avg_lag,
            details={
                "samples": len(lag_samples),
                "min_lag": min(lag_samples),
                "max_lag": max(lag_samples),
            },
        )

    # -------------------------------------------------------------------------
    # Batch Analysis
    # -------------------------------------------------------------------------

    def check_all(
        self,
        symbol: str,
        exchanges: Optional[List[str]] = None
    ) -> List[CorrelationResult]:
        """
        Run all correlation checks for a symbol.

        Args:
            symbol: Unified symbol to check
            exchanges: Optional list of exchanges

        Returns:
            List of significant CorrelationResults
        """
        results: List[CorrelationResult] = []

        # Price divergence
        price_result = self.check_price_divergence(symbol, exchanges)
        if price_result.is_significant:
            results.append(price_result)

        # Funding arbitrage
        funding_result = self.check_funding_arbitrage(symbol, exchanges)
        if funding_result.is_significant:
            results.append(funding_result)

        # OI divergence
        oi_result = self.check_oi_divergence(symbol)
        if oi_result.is_significant:
            results.append(oi_result)

        return results

    async def check_all_symbols(
        self,
        exchanges: Optional[List[str]] = None
    ) -> Dict[str, List[CorrelationResult]]:
        """
        Run all checks for all common symbols.

        Args:
            exchanges: Optional list of exchanges

        Returns:
            Dict mapping symbol -> list of significant results
        """
        symbols = self.store.common_symbols(exchanges)
        results: Dict[str, List[CorrelationResult]] = {}

        for symbol in symbols:
            symbol_results = self.check_all(symbol, exchanges)
            if symbol_results:
                results[symbol] = symbol_results

        return results


# =============================================================================
# DETECTOR ORCHESTRATOR
# =============================================================================

@dataclass
class DetectorOrchestratorConfig:
    """Configuration for detector orchestrator."""
    # Enable/disable individual detectors
    enable_price_divergence: bool = True
    enable_volume_correlation: bool = True
    enable_funding_arbitrage: bool = True
    enable_oi_migration: bool = True
    enable_liquidity_hunt: bool = True
    enable_spoofing_cross: bool = True

    # Analysis settings
    parallel_analysis: bool = True
    max_concurrent_symbols: int = 50

    # Result filtering
    min_severity: str = "WARNING"  # INFO, WARNING, ALERT, CRITICAL


class DetectorOrchestrator:
    """
    Orchestrates all cross-exchange manipulation detectors.

    Runs CX-001 through CX-006 detectors on provided symbols
    and aggregates results.

    Usage:
        store = StateStore()
        orchestrator = DetectorOrchestrator(store)

        # Run all detectors on a symbol
        detections = await orchestrator.analyze_symbol("BTC/USDT")

        # Run on all symbols
        all_detections = await orchestrator.analyze_all()
    """

    def __init__(
        self,
        state_store: StateStore,
        config: Optional[DetectorOrchestratorConfig] = None
    ):
        """
        Initialize orchestrator with detectors.

        Args:
            state_store: StateStore instance with exchange data
            config: Optional configuration
        """
        self.store = state_store
        self.config = config or DetectorOrchestratorConfig()
        self.logger = logger.bind(component="detector_orchestrator")

        # Initialize detectors
        self._detectors: List[BaseCrossDetector] = []
        self._init_detectors()

    def _init_detectors(self) -> None:
        """Initialize all enabled detectors."""
        if self.config.enable_price_divergence:
            self._detectors.append(PriceDivergenceDetector())

        if self.config.enable_volume_correlation:
            self._detectors.append(VolumeCorrelationDetector())

        if self.config.enable_funding_arbitrage:
            self._detectors.append(FundingArbitrageDetector())

        if self.config.enable_oi_migration:
            self._detectors.append(OIMigrationDetector())

        if self.config.enable_liquidity_hunt:
            self._detectors.append(LiquidityHuntDetector())

        if self.config.enable_spoofing_cross:
            self._detectors.append(SpoofingCrossDetector())

        self.logger.info(
            "detectors_initialized",
            count=len(self._detectors),
            detectors=[d.NAME for d in self._detectors]
        )

    def get_detector(self, name: str) -> Optional[BaseCrossDetector]:
        """Get a specific detector by name."""
        for detector in self._detectors:
            if detector.NAME == name:
                return detector
        return None

    @property
    def detectors(self) -> List[BaseCrossDetector]:
        """Get list of all active detectors."""
        return self._detectors.copy()

    async def analyze_symbol(
        self,
        symbol: str
    ) -> List[Detection]:
        """
        Run all detectors on a single symbol.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")

        Returns:
            List of detections from all detectors
        """
        detections: List[Detection] = []

        for detector in self._detectors:
            try:
                detection = await detector.analyze(symbol, self.store)
                if detection is not None:
                    # Filter by minimum severity
                    if self._meets_severity(detection.severity):
                        detections.append(detection)
            except Exception as e:
                self.logger.error(
                    "detector_error",
                    detector=detector.NAME,
                    symbol=symbol,
                    error=str(e)
                )

        return detections

    async def analyze_all(
        self,
        symbols: Optional[List[str]] = None,
        exchanges: Optional[List[str]] = None
    ) -> Dict[str, List[Detection]]:
        """
        Run all detectors on all symbols.

        Args:
            symbols: Optional list of symbols (default: all common)
            exchanges: Optional list of exchanges to consider

        Returns:
            Dict mapping symbol -> list of detections
        """
        if symbols is None:
            symbols = self.store.common_symbols(exchanges)

        results: Dict[str, List[Detection]] = {}

        if self.config.parallel_analysis:
            # Run in parallel with semaphore
            semaphore = asyncio.Semaphore(self.config.max_concurrent_symbols)

            async def analyze_with_semaphore(sym: str) -> Tuple[str, List[Detection]]:
                async with semaphore:
                    dets = await self.analyze_symbol(sym)
                    return sym, dets

            tasks = [analyze_with_semaphore(s) for s in symbols]
            completed = await asyncio.gather(*tasks, return_exceptions=True)

            for result in completed:
                if isinstance(result, Exception):
                    self.logger.error("parallel_analysis_error", error=str(result))
                else:
                    symbol, detections = result
                    if detections:
                        results[symbol] = detections
        else:
            # Sequential analysis
            for symbol in symbols:
                detections = await self.analyze_symbol(symbol)
                if detections:
                    results[symbol] = detections

        self.logger.info(
            "analysis_complete",
            symbols_analyzed=len(symbols),
            symbols_with_detections=len(results),
            total_detections=sum(len(d) for d in results.values())
        )

        return results

    def _meets_severity(self, severity: Severity) -> bool:
        """Check if severity meets minimum threshold."""
        severity_order = {
            Severity.INFO: 0,
            Severity.WARNING: 1,
            Severity.ALERT: 2,
            Severity.CRITICAL: 3,
        }

        min_level = severity_order.get(
            Severity(self.config.min_severity.lower()),
            1  # Default to WARNING
        )
        detection_level = severity_order.get(severity, 0)

        return detection_level >= min_level

    def get_statistics(self) -> Dict[str, Any]:
        """Get detector statistics."""
        return {
            "active_detectors": len(self._detectors),
            "detector_names": [d.NAME for d in self._detectors],
            "config": {
                "parallel_analysis": self.config.parallel_analysis,
                "max_concurrent_symbols": self.config.max_concurrent_symbols,
                "min_severity": self.config.min_severity,
            }
        }

    def reset_all(self) -> None:
        """Reset all detector states (deduplication, history)."""
        for detector in self._detectors:
            detector._last_alerts.clear()
        self.logger.info("detectors_reset")
