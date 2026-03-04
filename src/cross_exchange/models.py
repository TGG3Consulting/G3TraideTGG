# -*- coding: utf-8 -*-
"""
Cross-Exchange Data Models and Configuration.

Contains:
- State dataclasses for tracking exchange data
- Configuration classes for cross-exchange detection
- Alert structures for detected patterns
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional


# =============================================================================
# ENUMS
# =============================================================================

class AlertSeverity(Enum):
    """Severity levels for cross-exchange alerts."""
    INFO = "info"
    WARNING = "warning"
    ALERT = "alert"
    CRITICAL = "critical"


class PatternType(Enum):
    """Types of detected manipulation patterns."""
    PRICE_DIVERGENCE = "price_divergence"
    FUNDING_ARBITRAGE = "funding_arbitrage"
    OI_DIVERGENCE = "oi_divergence"
    LIQUIDATION_HUNTING = "liquidation_hunting"
    VOLUME_ANOMALY = "volume_anomaly"
    ORDERBOOK_IMBALANCE = "orderbook_imbalance"
    WASH_TRADING = "wash_trading"
    FRONT_RUNNING = "front_running"
    SPOOFING = "spoofing"
    LAYERING = "layering"
    COORDINATED_TRADES = "coordinated_trades"


# =============================================================================
# STATE DATA CLASSES
# =============================================================================

@dataclass
class PriceState:
    """
    Current price state for a symbol on an exchange.

    Attributes:
        price: Current/last price
        timestamp: When this price was recorded
        volume_24h: 24-hour trading volume
        change_1h: Price change % over last hour
        change_24h: Price change % over last 24 hours
    """
    price: Decimal
    timestamp: datetime
    volume_24h: Optional[Decimal] = None
    change_1h: Optional[Decimal] = None
    change_24h: Optional[Decimal] = None

    @property
    def age_seconds(self) -> float:
        """Seconds since this price was recorded."""
        return (datetime.now(timezone.utc) - self.timestamp).total_seconds()

    def is_stale(self, max_age_sec: int = 10) -> bool:
        """Check if price data is stale."""
        return self.age_seconds > max_age_sec


@dataclass
class OrderBookState:
    """
    Current orderbook state for a symbol.

    Attributes:
        best_bid: Best bid price
        best_ask: Best ask price
        bid_volume: Total bid volume (top N levels)
        ask_volume: Total ask volume (top N levels)
        imbalance: Bid/Ask volume ratio (0.5 = balanced)
        spread_pct: Bid-ask spread as percentage
        timestamp: When this snapshot was taken
        depth: Number of levels included
    """
    best_bid: Decimal
    best_ask: Decimal
    bid_volume: Decimal
    ask_volume: Decimal
    timestamp: datetime
    depth: int = 10

    @property
    def mid_price(self) -> Decimal:
        """Calculate mid price."""
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread_pct(self) -> Decimal:
        """Calculate spread as percentage."""
        if self.best_bid == 0:
            return Decimal(0)
        return ((self.best_ask - self.best_bid) / self.best_bid) * 100

    @property
    def imbalance(self) -> Decimal:
        """Calculate orderbook imbalance (0 to 1)."""
        total = self.bid_volume + self.ask_volume
        if total == 0:
            return Decimal("0.5")
        return self.bid_volume / total

    def is_stale(self, max_age_sec: int = 5) -> bool:
        """Check if orderbook data is stale."""
        age = (datetime.now(timezone.utc) - self.timestamp).total_seconds()
        return age > max_age_sec


@dataclass
class OIState:
    """
    Open Interest state for a symbol.

    Attributes:
        value: Open interest in base currency
        value_usd: Open interest in USD
        timestamp: When this was recorded
        change_1h: OI change % over last hour
        change_24h: OI change % over last 24 hours
    """
    value: Decimal
    timestamp: datetime
    value_usd: Optional[Decimal] = None
    change_1h: Optional[Decimal] = None
    change_24h: Optional[Decimal] = None

    def is_stale(self, max_age_sec: int = 120) -> bool:
        """Check if OI data is stale."""
        age = (datetime.now(timezone.utc) - self.timestamp).total_seconds()
        return age > max_age_sec


@dataclass
class FundingState:
    """
    Funding rate state for a perpetual contract.

    Attributes:
        rate: Current/predicted funding rate
        timestamp: When this was recorded
        next_funding: Time of next funding settlement
        predicted_rate: Predicted rate for next period
    """
    rate: Decimal
    timestamp: datetime
    next_funding: Optional[datetime] = None
    predicted_rate: Optional[Decimal] = None

    @property
    def rate_annualized(self) -> Decimal:
        """Annualized funding rate (assuming 8h intervals)."""
        return self.rate * 3 * 365  # 3 fundings per day * 365 days

    def is_stale(self, max_age_sec: int = 600) -> bool:
        """Check if funding data is stale (10 min default)."""
        age = (datetime.now(timezone.utc) - self.timestamp).total_seconds()
        return age > max_age_sec


@dataclass
class VolumeState:
    """
    Rolling volume state for aggregation.

    Attributes:
        buy_volume: Buy side volume in quote currency
        sell_volume: Sell side volume in quote currency
        trade_count: Number of trades
        window_start: Start of this aggregation window
        largest_trade: Size of largest trade in window
    """
    buy_volume: Decimal = field(default_factory=lambda: Decimal(0))
    sell_volume: Decimal = field(default_factory=lambda: Decimal(0))
    trade_count: int = 0
    window_start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    largest_trade: Decimal = field(default_factory=lambda: Decimal(0))

    @property
    def total_volume(self) -> Decimal:
        """Total trading volume."""
        return self.buy_volume + self.sell_volume

    @property
    def buy_ratio(self) -> Decimal:
        """Ratio of buy volume to total."""
        if self.total_volume == 0:
            return Decimal("0.5")
        return self.buy_volume / self.total_volume

    @property
    def net_volume(self) -> Decimal:
        """Net volume (buy - sell)."""
        return self.buy_volume - self.sell_volume

    def add_trade(
        self,
        volume: Decimal,
        is_buy: bool,
        trade_size: Optional[Decimal] = None
    ) -> None:
        """Add a trade to this window."""
        if is_buy:
            self.buy_volume += volume
        else:
            self.sell_volume += volume
        self.trade_count += 1

        if trade_size and trade_size > self.largest_trade:
            self.largest_trade = trade_size

    def reset(self, new_window_start: Optional[datetime] = None) -> None:
        """Reset for new window."""
        self.buy_volume = Decimal(0)
        self.sell_volume = Decimal(0)
        self.trade_count = 0
        self.largest_trade = Decimal(0)
        self.window_start = new_window_start or datetime.now(timezone.utc)


@dataclass
class LiquidationLevel:
    """
    Estimated liquidation level.

    Attributes:
        price: Price level where liquidations occur
        volume_usd: Estimated volume of liquidations
        direction: "long" or "short"
        exchange: Exchange where this liquidation would occur
    """
    price: Decimal
    volume_usd: Decimal
    direction: str  # "long" or "short"
    exchange: str


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class CrossExchangeConfig:
    """
    Configuration for cross-exchange state store and detection.

    All time values are in seconds unless otherwise noted.
    """
    # Data staleness thresholds
    price_stale_sec: int = 10       # Price data is stale after 10 seconds
    orderbook_stale_sec: int = 5    # Orderbook is stale after 5 seconds
    oi_stale_sec: int = 120         # OI is stale after 2 minutes
    funding_stale_sec: int = 600    # Funding is stale after 10 minutes

    # Aggregation windows
    volume_window_sec: int = 60     # Volume aggregation window (1 minute)
    price_history_minutes: int = 120  # Keep 2 hours of price history
    oi_history_minutes: int = 60    # Keep 1 hour of OI history

    # Cleanup settings
    cleanup_interval_sec: int = 30  # Run cleanup every 30 seconds
    max_history_points: int = 1000  # Max history points per symbol

    # Detection thresholds
    price_divergence_warning_pct: Decimal = field(
        default_factory=lambda: Decimal("0.05")
    )  # 0.05% divergence warning
    price_divergence_alert_pct: Decimal = field(
        default_factory=lambda: Decimal("0.15")
    )  # 0.15% divergence alert

    funding_divergence_warning_pct: Decimal = field(
        default_factory=lambda: Decimal("0.005")
    )  # 0.5% funding spread warning
    funding_divergence_alert_pct: Decimal = field(
        default_factory=lambda: Decimal("0.02")
    )  # 2% funding spread alert

    oi_divergence_warning_pct: Decimal = field(
        default_factory=lambda: Decimal("5.0")
    )  # 5% OI imbalance warning
    oi_divergence_alert_pct: Decimal = field(
        default_factory=lambda: Decimal("15.0")
    )  # 15% OI imbalance alert

    volume_correlation_threshold: float = 0.95  # Suspiciously high correlation

    orderbook_imbalance_warning: float = 0.65  # 65% on one side
    orderbook_imbalance_alert: float = 0.80    # 80% on one side

    # Arbitrage detection
    arbitrage_min_spread_pct: Decimal = field(
        default_factory=lambda: Decimal("0.1")
    )  # Min 0.1% for arbitrage alert

    # Exchanges to monitor
    enabled_exchanges: List[str] = field(default_factory=lambda: [
        "binance", "bybit", "okx", "bitget", "gate",
        "kucoin", "htx", "mexc", "bingx", "bitmart",
        "hyperliquid"
    ])

    # Symbols to prioritize
    priority_symbols: List[str] = field(default_factory=lambda: [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT",
        "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "MATIC/USDT"
    ])


# =============================================================================
# ALERT STRUCTURES
# =============================================================================

@dataclass
class CrossExchangeAlert:
    """
    Alert generated from cross-exchange pattern detection.

    Attributes:
        pattern: Type of pattern detected
        severity: Alert severity level
        symbol: Affected symbol
        timestamp: When detected
        exchanges: Exchanges involved
        description: Human-readable description
        metrics: Supporting metrics/data
        recommended_action: Suggested response
    """
    pattern: PatternType
    severity: AlertSeverity
    symbol: str
    timestamp: datetime
    exchanges: List[str]
    description: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    recommended_action: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "pattern": self.pattern.value,
            "severity": self.severity.value,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "exchanges": self.exchanges,
            "description": self.description,
            "metrics": self.metrics,
            "recommended_action": self.recommended_action,
        }


@dataclass
class ArbitrageOpportunity:
    """
    Detected arbitrage opportunity.

    Attributes:
        symbol: Trading pair
        buy_exchange: Exchange to buy on
        sell_exchange: Exchange to sell on
        buy_price: Price to buy at
        sell_price: Price to sell at
        spread_pct: Spread percentage
        estimated_profit_pct: Estimated profit after fees
        timestamp: Detection time
        confidence: Confidence score (0-1)
    """
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: Decimal
    sell_price: Decimal
    spread_pct: Decimal
    timestamp: datetime
    estimated_profit_pct: Optional[Decimal] = None
    confidence: float = 0.5

    @property
    def is_actionable(self) -> bool:
        """Check if this opportunity is worth acting on."""
        # Typically need > 0.1% after fees
        return self.spread_pct > Decimal("0.1")


@dataclass
class FundingArbitrageSignal:
    """
    Funding rate arbitrage opportunity.

    Attributes:
        symbol: Trading pair
        long_exchange: Exchange to go long (lower/negative funding)
        short_exchange: Exchange to go short (higher/positive funding)
        long_rate: Funding rate on long exchange
        short_rate: Funding rate on short exchange
        spread: Rate spread
        annualized_yield: Expected annualized yield
        next_funding: Time of next funding
    """
    symbol: str
    long_exchange: str
    short_exchange: str
    long_rate: Decimal
    short_rate: Decimal
    timestamp: datetime
    next_funding: Optional[datetime] = None

    @property
    def spread(self) -> Decimal:
        """Funding rate spread."""
        return self.short_rate - self.long_rate

    @property
    def annualized_yield(self) -> Decimal:
        """Annualized yield (assuming 8h funding intervals)."""
        return self.spread * 3 * 365  # 3 times per day * 365 days


@dataclass
class OIDivergenceSignal:
    """
    Open Interest divergence signal.

    When OI is shifting significantly between exchanges, it may indicate
    position unwinding or building in preparation for a move.
    """
    symbol: str
    timestamp: datetime
    oi_by_exchange: Dict[str, Decimal]  # OI values
    oi_change_by_exchange: Dict[str, Decimal]  # % changes
    total_oi_usd: Decimal
    divergence_score: float  # How divergent (0-1)
    leading_exchange: Optional[str] = None  # Exchange driving the change

    @property
    def is_significant(self) -> bool:
        """Check if divergence is significant enough to note."""
        return self.divergence_score > 0.3


@dataclass
class CrossExchangeSummary:
    """
    Summary of cross-exchange state for a symbol.

    Provides a quick overview of the current state across all exchanges.
    """
    symbol: str
    timestamp: datetime

    # Price summary
    prices: Dict[str, Decimal]
    price_spread_pct: Decimal
    price_leader: Optional[str]

    # Funding summary
    funding_rates: Dict[str, Decimal]
    funding_spread_pct: Decimal
    funding_arbitrage_possible: bool

    # OI summary
    oi_distribution: Dict[str, Decimal]  # Percentages
    total_oi_usd: Decimal
    oi_trend: str  # "increasing", "decreasing", "stable"

    # Orderbook summary
    orderbook_imbalances: Dict[str, float]
    avg_imbalance: float

    # Volume summary
    volume_24h: Dict[str, Decimal]
    dominant_exchange: Optional[str]

    # Alerts
    active_alerts: List[CrossExchangeAlert] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "price": {
                "values": {k: str(v) for k, v in self.prices.items()},
                "spread_pct": str(self.price_spread_pct),
                "leader": self.price_leader,
            },
            "funding": {
                "rates": {k: str(v) for k, v in self.funding_rates.items()},
                "spread_pct": str(self.funding_spread_pct),
                "arbitrage_possible": self.funding_arbitrage_possible,
            },
            "oi": {
                "distribution": {k: str(v) for k, v in self.oi_distribution.items()},
                "total_usd": str(self.total_oi_usd),
                "trend": self.oi_trend,
            },
            "orderbook": {
                "imbalances": self.orderbook_imbalances,
                "avg_imbalance": self.avg_imbalance,
            },
            "volume_24h": {k: str(v) for k, v in self.volume_24h.items()},
            "dominant_exchange": self.dominant_exchange,
            "alert_count": len(self.active_alerts),
        }
