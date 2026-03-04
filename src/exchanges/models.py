# -*- coding: utf-8 -*-
"""
Unified Data Models for Cross-Exchange System.

All exchanges normalize their data to these unified formats.
This ensures consistent handling across different exchange APIs.

Design principles:
1. Immutable dataclasses with slots for memory efficiency
2. Decimal for price/quantity precision (no float rounding errors)
3. UTC timestamps everywhere (datetime with tzinfo)
4. Explicit typing for all fields
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum, auto
from typing import List, Literal, Optional, Tuple


# =============================================================================
# ENUMS
# =============================================================================

class Side(str, Enum):
    """Trade/Order side."""
    BUY = "BUY"
    SELL = "SELL"

    def __str__(self) -> str:
        return self.value


class OrderBookSide(str, Enum):
    """Order book side."""
    BID = "BID"
    ASK = "ASK"


class KlineInterval(str, Enum):
    """Kline/candlestick intervals."""
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H2 = "2h"
    H4 = "4h"
    H6 = "6h"
    H8 = "8h"
    H12 = "12h"
    D1 = "1d"
    D3 = "3d"
    W1 = "1w"
    MO1 = "1M"


class MarketType(str, Enum):
    """Market type."""
    SPOT = "SPOT"
    FUTURES_PERPETUAL = "FUTURES_PERPETUAL"
    FUTURES_DELIVERY = "FUTURES_DELIVERY"
    OPTION = "OPTION"
    MARGIN = "MARGIN"


class LiquidationType(str, Enum):
    """Liquidation type."""
    LONG = "LONG"
    SHORT = "SHORT"


# =============================================================================
# UNIFIED TRADE
# =============================================================================

@dataclass(frozen=True, slots=True)
class UnifiedTrade:
    """
    Unified trade representation across all exchanges.

    Attributes:
        exchange: Exchange identifier (lowercase: "binance", "bybit", etc.)
        symbol: Unified symbol format (e.g., "BTC/USDT")
        timestamp: Trade execution time (UTC)
        price: Trade price
        quantity: Trade quantity (base asset)
        side: BUY or SELL (taker side)
        trade_id: Exchange-specific trade ID
        quote_quantity: Trade value in quote asset (price * quantity)
        is_maker: True if maker order, False if taker
        raw: Original exchange data (for debugging)
    """
    exchange: str
    symbol: str
    timestamp: datetime
    price: Decimal
    quantity: Decimal
    side: Side
    trade_id: str
    quote_quantity: Optional[Decimal] = None
    is_maker: Optional[bool] = None
    raw: Optional[dict] = field(default=None, compare=False, hash=False)

    def __post_init__(self):
        # Validate timestamp has timezone
        if self.timestamp.tzinfo is None:
            object.__setattr__(
                self,
                'timestamp',
                self.timestamp.replace(tzinfo=timezone.utc)
            )

    @property
    def value_usd(self) -> Decimal:
        """Trade value (price * quantity)."""
        if self.quote_quantity is not None:
            return self.quote_quantity
        return self.price * self.quantity

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "price": str(self.price),
            "quantity": str(self.quantity),
            "side": str(self.side),
            "trade_id": self.trade_id,
            "quote_quantity": str(self.quote_quantity) if self.quote_quantity else None,
            "is_maker": self.is_maker,
        }


# =============================================================================
# UNIFIED ORDER BOOK
# =============================================================================

@dataclass(slots=True)
class OrderBookLevel:
    """Single price level in order book."""
    price: Decimal
    quantity: Decimal

    @property
    def value(self) -> Decimal:
        """Level value (price * quantity)."""
        return self.price * self.quantity


@dataclass(slots=True)
class UnifiedOrderBook:
    """
    Unified order book snapshot.

    Attributes:
        exchange: Exchange identifier
        symbol: Unified symbol format
        timestamp: Snapshot time (UTC)
        bids: List of (price, quantity) tuples, sorted high to low
        asks: List of (price, quantity) tuples, sorted low to high
        sequence: Exchange sequence number for ordering
        raw: Original exchange data
    """
    exchange: str
    symbol: str
    timestamp: datetime
    bids: List[Tuple[Decimal, Decimal]]  # [(price, qty), ...]
    asks: List[Tuple[Decimal, Decimal]]  # [(price, qty), ...]
    sequence: Optional[int] = None
    raw: Optional[dict] = field(default=None, compare=False)

    @property
    def best_bid(self) -> Optional[Tuple[Decimal, Decimal]]:
        """Best bid (highest buy price)."""
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> Optional[Tuple[Decimal, Decimal]]:
        """Best ask (lowest sell price)."""
        return self.asks[0] if self.asks else None

    @property
    def mid_price(self) -> Optional[Decimal]:
        """Mid price between best bid and ask."""
        if self.best_bid and self.best_ask:
            return (self.best_bid[0] + self.best_ask[0]) / 2
        return None

    @property
    def spread(self) -> Optional[Decimal]:
        """Absolute spread (ask - bid)."""
        if self.best_bid and self.best_ask:
            return self.best_ask[0] - self.best_bid[0]
        return None

    @property
    def spread_pct(self) -> Optional[Decimal]:
        """Spread as percentage of mid price."""
        if self.mid_price and self.spread:
            return (self.spread / self.mid_price) * 100
        return None

    def depth_at_pct(self, pct: Decimal) -> Tuple[Decimal, Decimal]:
        """
        Calculate cumulative depth within +/- pct% from mid price.

        Returns:
            Tuple of (bid_depth_usd, ask_depth_usd)
        """
        if not self.mid_price:
            return Decimal(0), Decimal(0)

        lower_bound = self.mid_price * (1 - pct / 100)
        upper_bound = self.mid_price * (1 + pct / 100)

        bid_depth = sum(
            p * q for p, q in self.bids
            if p >= lower_bound
        )
        ask_depth = sum(
            p * q for p, q in self.asks
            if p <= upper_bound
        )

        return bid_depth, ask_depth

    @property
    def imbalance(self) -> Optional[Decimal]:
        """
        Order book imbalance ratio.

        Returns:
            Value between -1 (all asks) and +1 (all bids).
            Positive = more bid pressure.
        """
        bid_depth, ask_depth = self.depth_at_pct(Decimal("2"))
        total = bid_depth + ask_depth
        if total == 0:
            return None
        return (bid_depth - ask_depth) / total

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "bids": [[str(p), str(q)] for p, q in self.bids[:20]],
            "asks": [[str(p), str(q)] for p, q in self.asks[:20]],
            "mid_price": str(self.mid_price) if self.mid_price else None,
            "spread_pct": str(self.spread_pct) if self.spread_pct else None,
        }


# =============================================================================
# UNIFIED TICKER
# =============================================================================

@dataclass(frozen=True, slots=True)
class UnifiedTicker:
    """
    Unified 24h ticker data.

    Attributes:
        exchange: Exchange identifier
        symbol: Unified symbol format
        timestamp: Ticker time (UTC)
        last_price: Last traded price
        bid_price: Best bid price
        ask_price: Best ask price
        high_24h: 24h high
        low_24h: 24h low
        volume_24h: 24h volume (base asset)
        quote_volume_24h: 24h volume (quote asset, USD)
        price_change_24h: Absolute price change
        price_change_pct_24h: Percentage price change
        trades_24h: Number of trades in 24h
    """
    exchange: str
    symbol: str
    timestamp: datetime
    last_price: Decimal
    bid_price: Optional[Decimal] = None
    ask_price: Optional[Decimal] = None
    high_24h: Optional[Decimal] = None
    low_24h: Optional[Decimal] = None
    volume_24h: Optional[Decimal] = None
    quote_volume_24h: Optional[Decimal] = None
    price_change_24h: Optional[Decimal] = None
    price_change_pct_24h: Optional[Decimal] = None
    trades_24h: Optional[int] = None
    raw: Optional[dict] = field(default=None, compare=False, hash=False)


# =============================================================================
# UNIFIED FUNDING RATE
# =============================================================================

@dataclass(frozen=True, slots=True)
class UnifiedFunding:
    """
    Unified funding rate data (perpetual futures).

    Attributes:
        exchange: Exchange identifier
        symbol: Unified symbol format
        timestamp: Current time (UTC)
        rate: Current funding rate (e.g., 0.0001 = 0.01%)
        next_funding_time: Next funding settlement time
        predicted_rate: Predicted next funding rate (if available)
        mark_price: Current mark price
        index_price: Current index price
        interval_hours: Funding interval (usually 8)
    """
    exchange: str
    symbol: str
    timestamp: datetime
    rate: Decimal
    next_funding_time: datetime
    predicted_rate: Optional[Decimal] = None
    mark_price: Optional[Decimal] = None
    index_price: Optional[Decimal] = None
    interval_hours: int = 8
    raw: Optional[dict] = field(default=None, compare=False, hash=False)

    @property
    def rate_pct(self) -> Decimal:
        """Funding rate as percentage."""
        return self.rate * 100

    @property
    def annualized_rate(self) -> Decimal:
        """Annualized funding rate (assuming rate stays constant)."""
        periods_per_year = (365 * 24) / self.interval_hours
        return self.rate * Decimal(str(periods_per_year)) * 100

    @property
    def premium(self) -> Optional[Decimal]:
        """Mark-Index premium percentage."""
        if self.mark_price and self.index_price and self.index_price != 0:
            return ((self.mark_price - self.index_price) / self.index_price) * 100
        return None

    def to_dict(self) -> dict:
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "rate": str(self.rate),
            "rate_pct": str(self.rate_pct),
            "next_funding_time": self.next_funding_time.isoformat(),
            "mark_price": str(self.mark_price) if self.mark_price else None,
            "index_price": str(self.index_price) if self.index_price else None,
        }


# =============================================================================
# UNIFIED OPEN INTEREST
# =============================================================================

@dataclass(frozen=True, slots=True)
class UnifiedOpenInterest:
    """
    Unified open interest data (futures).

    Attributes:
        exchange: Exchange identifier
        symbol: Unified symbol format
        timestamp: Snapshot time (UTC)
        open_interest: OI in contracts/base asset
        open_interest_usd: OI in USD value
        market_type: FUTURES_PERPETUAL or FUTURES_DELIVERY
    """
    exchange: str
    symbol: str
    timestamp: datetime
    open_interest: Decimal
    open_interest_usd: Optional[Decimal] = None
    market_type: MarketType = MarketType.FUTURES_PERPETUAL
    raw: Optional[dict] = field(default=None, compare=False, hash=False)

    def to_dict(self) -> dict:
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "open_interest": str(self.open_interest),
            "open_interest_usd": str(self.open_interest_usd) if self.open_interest_usd else None,
            "market_type": str(self.market_type),
        }


# =============================================================================
# UNIFIED KLINE
# =============================================================================

@dataclass(frozen=True, slots=True)
class UnifiedKline:
    """
    Unified candlestick/kline data.

    Attributes:
        exchange: Exchange identifier
        symbol: Unified symbol format
        interval: Kline interval (1m, 5m, 1h, etc.)
        open_time: Candle open time (UTC)
        close_time: Candle close time (UTC)
        open: Open price
        high: High price
        low: Low price
        close: Close price
        volume: Volume (base asset)
        quote_volume: Volume (quote asset)
        trades: Number of trades
        is_closed: True if candle is finalized
    """
    exchange: str
    symbol: str
    interval: KlineInterval
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Optional[Decimal] = None
    trades: Optional[int] = None
    is_closed: bool = False
    raw: Optional[dict] = field(default=None, compare=False, hash=False)

    @property
    def body_pct(self) -> Decimal:
        """Candle body as percentage of range."""
        range_val = self.high - self.low
        if range_val == 0:
            return Decimal(0)
        body = abs(self.close - self.open)
        return (body / range_val) * 100

    @property
    def is_bullish(self) -> bool:
        """True if close > open."""
        return self.close > self.open

    @property
    def change_pct(self) -> Decimal:
        """Price change percentage."""
        if self.open == 0:
            return Decimal(0)
        return ((self.close - self.open) / self.open) * 100


# =============================================================================
# UNIFIED LIQUIDATION
# =============================================================================

@dataclass(frozen=True, slots=True)
class UnifiedLiquidation:
    """
    Unified liquidation event data.

    Attributes:
        exchange: Exchange identifier
        symbol: Unified symbol format
        timestamp: Liquidation time (UTC)
        side: LONG or SHORT (position being liquidated)
        price: Liquidation price
        quantity: Liquidated quantity
        value_usd: Liquidation value in USD
        order_type: Order type used for liquidation
    """
    exchange: str
    symbol: str
    timestamp: datetime
    side: LiquidationType
    price: Decimal
    quantity: Decimal
    value_usd: Optional[Decimal] = None
    order_type: Optional[str] = None
    raw: Optional[dict] = field(default=None, compare=False, hash=False)

    def to_dict(self) -> dict:
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "side": str(self.side),
            "price": str(self.price),
            "quantity": str(self.quantity),
            "value_usd": str(self.value_usd) if self.value_usd else None,
        }


# =============================================================================
# AGGREGATED CROSS-EXCHANGE DATA
# =============================================================================

@dataclass(slots=True)
class CrossExchangePrice:
    """
    Price data aggregated across multiple exchanges.

    Used for cross-exchange arbitrage and divergence detection.
    """
    symbol: str
    timestamp: datetime
    prices: dict[str, Decimal]  # exchange -> price
    volumes: dict[str, Decimal]  # exchange -> 24h volume

    @property
    def vwap(self) -> Optional[Decimal]:
        """Volume-weighted average price across exchanges."""
        total_volume = sum(self.volumes.values())
        if total_volume == 0:
            return None
        weighted_sum = sum(
            self.prices[ex] * self.volumes[ex]
            for ex in self.prices
            if ex in self.volumes
        )
        return weighted_sum / total_volume

    @property
    def max_spread_pct(self) -> Optional[Decimal]:
        """Maximum price spread across exchanges (%)."""
        if len(self.prices) < 2:
            return None
        prices = list(self.prices.values())
        min_price = min(prices)
        max_price = max(prices)
        if min_price == 0:
            return None
        return ((max_price - min_price) / min_price) * 100

    def divergence_from(self, exchange: str) -> dict[str, Decimal]:
        """Price divergence from a reference exchange (%)."""
        if exchange not in self.prices:
            return {}
        ref_price = self.prices[exchange]
        if ref_price == 0:
            return {}
        return {
            ex: ((price - ref_price) / ref_price) * 100
            for ex, price in self.prices.items()
            if ex != exchange
        }


@dataclass(slots=True)
class CrossExchangeFunding:
    """
    Funding rate data aggregated across multiple exchanges.

    Used for funding arbitrage detection.
    """
    symbol: str
    timestamp: datetime
    rates: dict[str, Decimal]  # exchange -> funding rate
    next_times: dict[str, datetime]  # exchange -> next funding time

    @property
    def max_rate(self) -> Tuple[str, Decimal]:
        """Exchange with highest funding rate."""
        if not self.rates:
            return "", Decimal(0)
        return max(self.rates.items(), key=lambda x: x[1])

    @property
    def min_rate(self) -> Tuple[str, Decimal]:
        """Exchange with lowest funding rate."""
        if not self.rates:
            return "", Decimal(0)
        return min(self.rates.items(), key=lambda x: x[1])

    @property
    def spread(self) -> Decimal:
        """Funding rate spread (max - min)."""
        if len(self.rates) < 2:
            return Decimal(0)
        return self.max_rate[1] - self.min_rate[1]

    @property
    def arbitrage_opportunity(self) -> Optional[dict]:
        """
        Detect funding arbitrage opportunity.

        Returns dict with long_exchange, short_exchange, spread_pct
        if spread > 0.05% (profitable after fees).
        """
        if self.spread < Decimal("0.0005"):  # 0.05%
            return None
        return {
            "long_exchange": self.min_rate[0],
            "short_exchange": self.max_rate[0],
            "spread_pct": float(self.spread * 100),
            "annualized_pct": float(self.spread * 100 * 365 * 3),  # 3 settlements/day
        }


@dataclass(slots=True)
class CrossExchangeOI:
    """
    Open Interest data aggregated across multiple exchanges.

    Used for OI divergence detection.
    """
    symbol: str
    timestamp: datetime
    oi_values: dict[str, Decimal]  # exchange -> OI in USD

    @property
    def total_oi(self) -> Decimal:
        """Total OI across all exchanges."""
        return sum(self.oi_values.values())

    @property
    def dominant_exchange(self) -> Tuple[str, Decimal]:
        """Exchange with most OI and its share (%)."""
        if not self.oi_values:
            return "", Decimal(0)
        total = self.total_oi
        if total == 0:
            return "", Decimal(0)
        top = max(self.oi_values.items(), key=lambda x: x[1])
        return top[0], (top[1] / total) * 100

    def exchange_share(self, exchange: str) -> Decimal:
        """Get OI share for specific exchange (%)."""
        total = self.total_oi
        if total == 0 or exchange not in self.oi_values:
            return Decimal(0)
        return (self.oi_values[exchange] / total) * 100
