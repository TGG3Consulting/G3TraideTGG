# -*- coding: utf-8 -*-
"""
Unified State Store for Cross-Exchange Data.

Maintains real-time snapshots of price, funding, OI, and orderbook
data across all connected exchanges.

Features:
- Thread-safe updates (asyncio.Lock)
- Time-windowed history for change detection
- Automatic cleanup of stale data
- Efficient memory usage with deque

Design:
    StateStore
    └── exchange (binance, bybit, ...)
        └── symbol (BTC/USDT, ETH/USDT, ...)
            ├── price_history: deque[(timestamp, price)]
            ├── funding_history: deque[(timestamp, rate)]
            ├── oi_history: deque[(timestamp, oi)]
            └── last_orderbook: UnifiedOrderBook
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import (
    Callable,
    Deque,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
)

import structlog

from src.exchanges.models import (
    UnifiedTrade,
    UnifiedOrderBook,
    UnifiedFunding,
    UnifiedOpenInterest,
    CrossExchangePrice,
    CrossExchangeFunding,
    CrossExchangeOI,
)

logger = structlog.get_logger(__name__)


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class PricePoint:
    """Single price data point."""
    timestamp: datetime
    price: Decimal
    volume_24h: Optional[Decimal] = None


@dataclass
class FundingPoint:
    """Single funding rate data point."""
    timestamp: datetime
    rate: Decimal
    next_time: Optional[datetime] = None


@dataclass
class OIPoint:
    """Single open interest data point."""
    timestamp: datetime
    oi: Decimal
    oi_usd: Optional[Decimal] = None


@dataclass
class SymbolSnapshot:
    """
    Real-time state for a symbol on a single exchange.

    Stores recent history for trend detection.
    """
    exchange: str
    symbol: str

    # Price data
    last_price: Optional[Decimal] = None
    last_price_time: Optional[datetime] = None
    volume_24h: Optional[Decimal] = None
    price_history: Deque[PricePoint] = field(
        default_factory=lambda: deque(maxlen=8640)  # 24ч * 60мин * 6 (раз в 10 сек)
    )

    # Funding data
    last_funding: Optional[Decimal] = None
    last_funding_time: Optional[datetime] = None
    next_funding_time: Optional[datetime] = None
    funding_history: Deque[FundingPoint] = field(
        default_factory=lambda: deque(maxlen=27)  # 72ч / 8ч = 9 периодов * 3 дня
    )

    # Open Interest
    last_oi: Optional[Decimal] = None
    last_oi_usd: Optional[Decimal] = None
    last_oi_time: Optional[datetime] = None
    oi_history: Deque[OIPoint] = field(
        default_factory=lambda: deque(maxlen=1440)  # 24ч * 60мин = 1440
    )

    # Order book
    last_orderbook: Optional[UnifiedOrderBook] = None
    last_orderbook_time: Optional[datetime] = None

    # Trade stats (rolling window)
    trades_1m: int = 0
    buy_volume_1m: Decimal = field(default_factory=lambda: Decimal(0))
    sell_volume_1m: Decimal = field(default_factory=lambda: Decimal(0))

    def update_price(
        self,
        price: Decimal,
        volume_24h: Optional[Decimal] = None,
        timestamp: Optional[datetime] = None
    ) -> None:
        """Update price data."""
        ts = timestamp or datetime.now(timezone.utc)
        self.last_price = price
        self.last_price_time = ts
        if volume_24h is not None:
            self.volume_24h = volume_24h
        self.price_history.append(PricePoint(ts, price, volume_24h))

    def update_funding(
        self,
        rate: Decimal,
        next_time: Optional[datetime] = None,
        timestamp: Optional[datetime] = None
    ) -> None:
        """Update funding rate data."""
        ts = timestamp or datetime.now(timezone.utc)
        self.last_funding = rate
        self.last_funding_time = ts
        self.next_funding_time = next_time
        self.funding_history.append(FundingPoint(ts, rate, next_time))

    def update_oi(
        self,
        oi: Decimal,
        oi_usd: Optional[Decimal] = None,
        timestamp: Optional[datetime] = None
    ) -> None:
        """Update open interest data."""
        ts = timestamp or datetime.now(timezone.utc)
        self.last_oi = oi
        self.last_oi_usd = oi_usd
        self.last_oi_time = ts
        self.oi_history.append(OIPoint(ts, oi, oi_usd))

    def price_change_pct(self, minutes: int = 5) -> Optional[Decimal]:
        """Calculate price change over N minutes."""
        if not self.price_history or len(self.price_history) < 2:
            return None

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        old_price = None

        for point in self.price_history:
            if point.timestamp <= cutoff:
                old_price = point.price
                break

        if old_price is None or old_price == 0:
            return None

        return ((self.last_price - old_price) / old_price) * 100

    def oi_change_pct(self, minutes: int = 60) -> Optional[Decimal]:
        """Calculate OI change over N minutes."""
        if not self.oi_history or len(self.oi_history) < 2:
            return None

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        old_oi = None

        for point in self.oi_history:
            if point.timestamp <= cutoff:
                old_oi = point.oi
                break

        if old_oi is None or old_oi == 0:
            return None

        return ((self.last_oi - old_oi) / old_oi) * 100

    def funding_change(self, periods: int = 3) -> Optional[Decimal]:
        """Calculate funding rate change over N periods."""
        if len(self.funding_history) < periods + 1:
            return None

        recent = list(self.funding_history)[-periods:]
        oldest = recent[0].rate

        return self.last_funding - oldest

    def is_stale(self, max_age_seconds: int = 60) -> bool:
        """Check if data is stale."""
        if not self.last_price_time:
            return True

        age = datetime.now(timezone.utc) - self.last_price_time
        return age.total_seconds() > max_age_seconds


@dataclass
class ExchangeSnapshot:
    """
    Real-time state for an entire exchange.

    Contains snapshots for all monitored symbols.
    """
    exchange: str
    symbols: Dict[str, SymbolSnapshot] = field(default_factory=dict)
    connected: bool = False
    last_update: Optional[datetime] = None

    def get_symbol(self, symbol: str) -> SymbolSnapshot:
        """Get or create symbol snapshot."""
        if symbol not in self.symbols:
            self.symbols[symbol] = SymbolSnapshot(
                exchange=self.exchange,
                symbol=symbol
            )
        return self.symbols[symbol]

    def active_symbols(self) -> List[str]:
        """Get symbols with recent data."""
        return [
            s for s, snap in self.symbols.items()
            if not snap.is_stale()
        ]


# =============================================================================
# STATE STORE
# =============================================================================

class StateStore:
    """
    Central state store for cross-exchange data.

    Thread-safe, supports callbacks for state changes.

    Usage:
        store = StateStore()

        # Update from exchange data
        store.update_price("binance", "BTC/USDT", Decimal("50000"))
        store.update_funding("bybit", "BTC/USDT", Decimal("0.0001"))

        # Get aggregated data
        cross_price = store.get_cross_price("BTC/USDT")
        cross_funding = store.get_cross_funding("BTC/USDT")
    """

    def __init__(self, max_history_minutes: int = 120):
        """
        Initialize state store.

        Args:
            max_history_minutes: Maximum history to retain
        """
        self._exchanges: Dict[str, ExchangeSnapshot] = {}
        self._lock = asyncio.Lock()
        self._max_history = timedelta(minutes=max_history_minutes)

        # Callbacks
        self._price_callbacks: List[Callable] = []
        self._funding_callbacks: List[Callable] = []
        self._oi_callbacks: List[Callable] = []

        # Symbol index (for fast lookup)
        self._symbol_exchanges: Dict[str, Set[str]] = defaultdict(set)

        self.logger = logger.bind(component="state_store")

        # LEAK-4 FIX: Фоновая очистка
        self._running = False
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self):
        """
        Запустить фоновую очистку стейта.
        LEAK-4 FIX: cleanup_stale() теперь вызывается периодически.
        """
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self.logger.info("state_store_started")

    async def stop(self):
        """Остановить фоновую очистку."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        self.logger.info("state_store_stopped")

    async def _cleanup_loop(self):
        """Периодическая очистка устаревших данных."""
        while self._running:
            try:
                await asyncio.sleep(300)  # Каждые 5 минут
                # ПРОФЕССИОНАЛЬНЫЕ TTL внутри cleanup_stale():
                # - price_history: 24 часа
                # - oi_history: 24 часа
                # - funding_history: 72 часа
                # - Удаление символа: 6 часов неактивности
                await self.cleanup_stale()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error("cleanup_loop_error", error=str(e))

    # -------------------------------------------------------------------------
    # Exchange Management
    # -------------------------------------------------------------------------

    def register_exchange(self, exchange: str) -> None:
        """Register a new exchange."""
        if exchange not in self._exchanges:
            self._exchanges[exchange] = ExchangeSnapshot(exchange=exchange)
            self.logger.info("exchange_registered", exchange=exchange)

    def set_exchange_connected(self, exchange: str, connected: bool) -> None:
        """Update exchange connection status."""
        if exchange in self._exchanges:
            self._exchanges[exchange].connected = connected

    def get_exchange(self, exchange: str) -> Optional[ExchangeSnapshot]:
        """Get exchange snapshot."""
        return self._exchanges.get(exchange)

    def connected_exchanges(self) -> List[str]:
        """Get list of connected exchanges."""
        return [
            name for name, snap in self._exchanges.items()
            if snap.connected
        ]

    def exchanges_for_symbol(self, symbol: str) -> List[str]:
        """Get exchanges that have data for a symbol."""
        return list(self._symbol_exchanges.get(symbol, set()))

    def get_exchanges_for_symbol(self, symbol: str) -> List[str]:
        """Alias for exchanges_for_symbol."""
        return self.exchanges_for_symbol(symbol)

    # -------------------------------------------------------------------------
    # Data Updates
    # -------------------------------------------------------------------------

    async def update_price(
        self,
        exchange: str,
        symbol: str,
        price: Decimal,
        volume_24h: Optional[Decimal] = None,
        timestamp: Optional[datetime] = None
    ) -> None:
        """
        Update price for a symbol on an exchange.

        Args:
            exchange: Exchange name
            symbol: Unified symbol (e.g., "BTC/USDT")
            price: Current price
            volume_24h: 24h volume (optional)
            timestamp: Data timestamp (default: now)
        """
        async with self._lock:
            self.register_exchange(exchange)
            snap = self._exchanges[exchange].get_symbol(symbol)
            snap.update_price(price, volume_24h, timestamp)
            self._exchanges[exchange].last_update = datetime.now(timezone.utc)
            self._symbol_exchanges[symbol].add(exchange)

        # Notify callbacks
        for callback in self._price_callbacks:
            try:
                callback(exchange, symbol, price, volume_24h)
            except Exception as e:
                self.logger.error("price_callback_error", error=str(e))

    async def update_funding(
        self,
        exchange: str,
        symbol: str,
        rate: Decimal,
        next_time: Optional[datetime] = None,
        timestamp: Optional[datetime] = None
    ) -> None:
        """
        Update funding rate for a symbol.

        Args:
            exchange: Exchange name
            symbol: Unified symbol
            rate: Funding rate (e.g., 0.0001 = 0.01%)
            next_time: Next funding settlement time
            timestamp: Data timestamp
        """
        async with self._lock:
            self.register_exchange(exchange)
            snap = self._exchanges[exchange].get_symbol(symbol)
            snap.update_funding(rate, next_time, timestamp)

        for callback in self._funding_callbacks:
            try:
                callback(exchange, symbol, rate)
            except Exception as e:
                self.logger.error("funding_callback_error", error=str(e))

    async def update_oi(
        self,
        exchange: str,
        symbol: str,
        oi: Decimal,
        oi_usd: Optional[Decimal] = None,
        timestamp: Optional[datetime] = None
    ) -> None:
        """
        Update open interest for a symbol.

        Args:
            exchange: Exchange name
            symbol: Unified symbol
            oi: Open interest (contracts)
            oi_usd: Open interest in USD
            timestamp: Data timestamp
        """
        async with self._lock:
            self.register_exchange(exchange)
            snap = self._exchanges[exchange].get_symbol(symbol)
            snap.update_oi(oi, oi_usd, timestamp)

        for callback in self._oi_callbacks:
            try:
                callback(exchange, symbol, oi, oi_usd)
            except Exception as e:
                self.logger.error("oi_callback_error", error=str(e))

    async def update_orderbook(
        self,
        exchange: str,
        symbol: str,
        orderbook: UnifiedOrderBook
    ) -> None:
        """Update orderbook snapshot."""
        async with self._lock:
            self.register_exchange(exchange)
            snap = self._exchanges[exchange].get_symbol(symbol)
            snap.last_orderbook = orderbook
            snap.last_orderbook_time = datetime.now(timezone.utc)

    async def update_trade(
        self,
        exchange: str,
        trade: UnifiedTrade
    ) -> None:
        """Update trade statistics."""
        async with self._lock:
            self.register_exchange(exchange)
            snap = self._exchanges[exchange].get_symbol(trade.symbol)

            # Update price from trade
            snap.update_price(trade.price, timestamp=trade.timestamp)

            # Update trade stats
            snap.trades_1m += 1
            # Handle both enum (Side.BUY) and string ("BUY") formats
            side_str = trade.side.value if hasattr(trade.side, 'value') else str(trade.side)
            if side_str.upper() == "BUY":
                snap.buy_volume_1m += trade.value_usd
            else:
                snap.sell_volume_1m += trade.value_usd

    # -------------------------------------------------------------------------
    # Cross-Exchange Queries
    # -------------------------------------------------------------------------

    def get_cross_price(self, symbol: str) -> CrossExchangePrice:
        """
        Get aggregated price data across all exchanges.

        Returns:
            CrossExchangePrice with prices and volumes per exchange
        """
        prices: Dict[str, Decimal] = {}
        volumes: Dict[str, Decimal] = {}

        for exchange in self._symbol_exchanges.get(symbol, set()):
            snap = self._exchanges.get(exchange)
            if snap:
                symbol_snap = snap.symbols.get(symbol)
                if symbol_snap and symbol_snap.last_price:
                    prices[exchange] = symbol_snap.last_price
                    if symbol_snap.volume_24h:
                        volumes[exchange] = symbol_snap.volume_24h

        return CrossExchangePrice(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            prices=prices,
            volumes=volumes
        )

    def get_cross_funding(self, symbol: str) -> CrossExchangeFunding:
        """
        Get aggregated funding rate data across all exchanges.

        Returns:
            CrossExchangeFunding with rates per exchange
        """
        rates: Dict[str, Decimal] = {}
        next_times: Dict[str, datetime] = {}

        for exchange in self._symbol_exchanges.get(symbol, set()):
            snap = self._exchanges.get(exchange)
            if snap:
                symbol_snap = snap.symbols.get(symbol)
                if symbol_snap and symbol_snap.last_funding is not None:
                    rates[exchange] = symbol_snap.last_funding
                    if symbol_snap.next_funding_time:
                        next_times[exchange] = symbol_snap.next_funding_time

        return CrossExchangeFunding(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            rates=rates,
            next_times=next_times
        )

    def get_cross_oi(self, symbol: str) -> CrossExchangeOI:
        """
        Get aggregated open interest across all exchanges.

        Returns:
            CrossExchangeOI with OI per exchange
        """
        oi_values: Dict[str, Decimal] = {}

        for exchange in self._symbol_exchanges.get(symbol, set()):
            snap = self._exchanges.get(exchange)
            if snap:
                symbol_snap = snap.symbols.get(symbol)
                if symbol_snap and symbol_snap.last_oi_usd:
                    oi_values[exchange] = symbol_snap.last_oi_usd

        return CrossExchangeOI(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            oi_values=oi_values
        )

    def get_symbol_snapshot(
        self,
        exchange: str,
        symbol: str
    ) -> Optional[SymbolSnapshot]:
        """Get snapshot for a specific exchange/symbol."""
        ex_snap = self._exchanges.get(exchange)
        if ex_snap:
            return ex_snap.symbols.get(symbol)
        return None

    def all_symbols(self) -> Set[str]:
        """Get all symbols with data on any exchange."""
        return set(self._symbol_exchanges.keys())

    def common_symbols(self, exchanges: Optional[List[str]] = None) -> Set[str]:
        """
        Get symbols available on all specified exchanges.

        If exchanges is None, uses all connected exchanges.
        """
        if exchanges is None:
            exchanges = self.connected_exchanges()

        if not exchanges:
            return set()

        # Start with symbols from first exchange
        common = set(self._exchanges[exchanges[0]].symbols.keys()) if exchanges[0] in self._exchanges else set()

        # Intersect with other exchanges
        for ex in exchanges[1:]:
            if ex in self._exchanges:
                common &= set(self._exchanges[ex].symbols.keys())

        return common

    # -------------------------------------------------------------------------
    # Callbacks
    # -------------------------------------------------------------------------

    def on_price_update(self, callback: Callable) -> None:
        """Register price update callback."""
        self._price_callbacks.append(callback)

    def on_funding_update(self, callback: Callable) -> None:
        """Register funding update callback."""
        self._funding_callbacks.append(callback)

    def on_oi_update(self, callback: Callable) -> None:
        """Register OI update callback."""
        self._oi_callbacks.append(callback)

    # -------------------------------------------------------------------------
    # Maintenance
    # -------------------------------------------------------------------------

    async def cleanup_stale(self) -> int:
        """
        Очистка устаревших данных с ПРОФЕССИОНАЛЬНЫМИ TTL для детекции манипуляций.

        TTL для разных типов данных:
        - price_history: 24 часа (86400 сек) - тренды, pump/dump
        - oi_history: 24 часа (86400 сек) - OI migration, accumulation
        - funding_history: 72 часа (259200 сек) - funding arbitrage
        - Удаление символа: 6 часов неактивности + все истории пустые

        Returns:
            Number of symbols cleaned up
        """
        # TTL константы (в секундах)
        TTL_PRICE = 86400      # 24 часа
        TTL_OI = 86400         # 24 часа
        TTL_FUNDING = 259200   # 72 часа (3 дня)
        TTL_SYMBOL_INACTIVE = 21600  # 6 часов - для удаления символа

        now = datetime.now(timezone.utc)
        cleaned_symbols = 0
        cleaned_records = 0

        async with self._lock:
            for exchange, ex_snap in self._exchanges.items():
                symbols_to_remove = []

                for symbol, snap in ex_snap.symbols.items():
                    # === ОЧИСТКА PRICE HISTORY ===
                    if snap.price_history:
                        old_len = len(snap.price_history)
                        # Фильтруем старые записи (deque не поддерживает remove, создаём новый)
                        fresh_prices = deque(
                            (p for p in snap.price_history
                             if (now - p.timestamp).total_seconds() < TTL_PRICE),
                            maxlen=8640
                        )
                        snap.price_history = fresh_prices
                        cleaned_records += old_len - len(fresh_prices)

                    # === ОЧИСТКА OI HISTORY ===
                    if snap.oi_history:
                        old_len = len(snap.oi_history)
                        fresh_oi = deque(
                            (oi for oi in snap.oi_history
                             if (now - oi.timestamp).total_seconds() < TTL_OI),
                            maxlen=1440
                        )
                        snap.oi_history = fresh_oi
                        cleaned_records += old_len - len(fresh_oi)

                    # === ОЧИСТКА FUNDING HISTORY ===
                    if snap.funding_history:
                        old_len = len(snap.funding_history)
                        fresh_funding = deque(
                            (f for f in snap.funding_history
                             if (now - f.timestamp).total_seconds() < TTL_FUNDING),
                            maxlen=27
                        )
                        snap.funding_history = fresh_funding
                        cleaned_records += old_len - len(fresh_funding)

                    # === УДАЛЕНИЕ СИМВОЛА ===
                    # Только если ВСЕ истории пустые И неактивен > 6 часов
                    all_empty = (
                        not snap.price_history and
                        not snap.oi_history and
                        not snap.funding_history
                    )

                    last_activity = snap.last_price_time
                    inactive_long = (
                        last_activity is None or
                        (now - last_activity).total_seconds() > TTL_SYMBOL_INACTIVE
                    )

                    if all_empty and inactive_long:
                        symbols_to_remove.append(symbol)

                # Удаляем символы
                for symbol in symbols_to_remove:
                    del ex_snap.symbols[symbol]
                    self._symbol_exchanges[symbol].discard(exchange)
                    cleaned_symbols += 1
                    self.logger.debug("symbol_removed_inactive",
                                     exchange=exchange,
                                     symbol=symbol)

            # Clean up empty symbol sets
            empty_symbols = [
                s for s, exchanges in self._symbol_exchanges.items()
                if not exchanges
            ]
            for symbol in empty_symbols:
                del self._symbol_exchanges[symbol]

        if cleaned_symbols > 0 or cleaned_records > 0:
            self.logger.info("stale_cleanup",
                           symbols_removed=cleaned_symbols,
                           records_cleaned=cleaned_records)

        return cleaned_symbols

    def stats(self) -> dict:
        """Get store statistics."""
        return {
            "exchanges": len(self._exchanges),
            "connected": len(self.connected_exchanges()),
            "total_symbols": len(self._symbol_exchanges),
            "symbols_per_exchange": {
                ex: len(snap.symbols)
                for ex, snap in self._exchanges.items()
            },
        }

    # -------------------------------------------------------------------------
    # Cross-Exchange Analysis Methods
    # -------------------------------------------------------------------------

    def get_price_spread(self, symbol: str) -> Dict[str, Decimal]:
        """
        Get price spread between all exchange pairs.

        Returns:
            Dict with keys like "binance_bybit" and spread % values.
            Example: {"binance_bybit": Decimal("0.02"), "binance_okx": Decimal("0.01")}

        The spread is calculated as: abs(price_a - price_b) / avg(price_a, price_b) * 100
        """
        spreads: Dict[str, Decimal] = {}
        prices: Dict[str, Decimal] = {}

        # Collect prices from all exchanges
        for exchange in self._symbol_exchanges.get(symbol, set()):
            snap = self._exchanges.get(exchange)
            if snap:
                symbol_snap = snap.symbols.get(symbol)
                if symbol_snap and symbol_snap.last_price and not symbol_snap.is_stale(30):
                    prices[exchange] = symbol_snap.last_price

        # Calculate pairwise spreads
        exchanges = list(prices.keys())
        for i, ex_a in enumerate(exchanges):
            for ex_b in exchanges[i + 1:]:
                price_a = prices[ex_a]
                price_b = prices[ex_b]
                avg_price = (price_a + price_b) / 2

                if avg_price > 0:
                    spread_pct = abs(price_a - price_b) / avg_price * 100
                    key = f"{ex_a}_{ex_b}"
                    spreads[key] = spread_pct

        # Add max/min/avg stats
        if spreads:
            values = list(spreads.values())
            spreads["_max"] = max(values)
            spreads["_min"] = min(values)
            spreads["_avg"] = sum(values) / len(values)

        return spreads

    def get_oi_distribution(self, symbol: str) -> Dict[str, Decimal]:
        """
        Get OI distribution as percentage across exchanges.

        Returns:
            Dict with exchange names and their % of total OI.
            Example: {"binance": Decimal("45.5"), "bybit": Decimal("30.2"), "okx": Decimal("24.3")}
        """
        distribution: Dict[str, Decimal] = {}
        oi_values: Dict[str, Decimal] = {}
        total_oi = Decimal(0)

        # Collect OI from all exchanges
        for exchange in self._symbol_exchanges.get(symbol, set()):
            snap = self._exchanges.get(exchange)
            if snap:
                symbol_snap = snap.symbols.get(symbol)
                if symbol_snap and symbol_snap.last_oi_usd:
                    oi_values[exchange] = symbol_snap.last_oi_usd
                    total_oi += symbol_snap.last_oi_usd

        # Calculate percentages
        if total_oi > 0:
            for exchange, oi in oi_values.items():
                distribution[exchange] = (oi / total_oi) * 100

        # Add total
        distribution["_total_usd"] = total_oi

        return distribution

    def get_funding_divergence(self, symbol: str) -> Dict[str, Decimal]:
        """
        Get funding rate divergence across exchanges.

        Returns:
            Dict with exchange rates and spread metrics.
            Example: {
                "binance": Decimal("0.0001"),
                "bybit": Decimal("-0.0002"),
                "_spread": Decimal("0.0003"),
                "_max": Decimal("0.0001"),
                "_min": Decimal("-0.0002")
            }
        """
        divergence: Dict[str, Decimal] = {}
        rates: List[Decimal] = []

        # Collect funding rates
        for exchange in self._symbol_exchanges.get(symbol, set()):
            snap = self._exchanges.get(exchange)
            if snap:
                symbol_snap = snap.symbols.get(symbol)
                if symbol_snap and symbol_snap.last_funding is not None:
                    divergence[exchange] = symbol_snap.last_funding
                    rates.append(symbol_snap.last_funding)

        # Calculate spread
        if rates:
            max_rate = max(rates)
            min_rate = min(rates)
            divergence["_max"] = max_rate
            divergence["_min"] = min_rate
            divergence["_spread"] = max_rate - min_rate
            divergence["_avg"] = sum(rates) / len(rates)

        return divergence

    def get_volume_correlation(
        self,
        symbol: str,
        window_minutes: int = 5
    ) -> Dict[str, float]:
        """
        Get volume correlation between exchanges.

        This measures how synchronized trading activity is.
        High correlation may indicate wash trading or coordinated activity.

        Returns:
            Dict with correlation metrics.
            Example: {
                "buy_sell_ratio": {"binance": 0.6, "bybit": 0.4},
                "volume_share": {"binance": 0.55, "bybit": 0.45},
            }
        """
        result: Dict[str, Any] = {
            "buy_sell_ratio": {},
            "volume_share": {},
            "trade_count": {},
        }

        total_buy = Decimal(0)
        total_sell = Decimal(0)
        total_volume = Decimal(0)
        total_trades = 0

        # Collect trade stats
        for exchange in self._symbol_exchanges.get(symbol, set()):
            snap = self._exchanges.get(exchange)
            if snap:
                symbol_snap = snap.symbols.get(symbol)
                if symbol_snap:
                    buy_vol = symbol_snap.buy_volume_1m
                    sell_vol = symbol_snap.sell_volume_1m
                    trades = symbol_snap.trades_1m

                    total = buy_vol + sell_vol
                    if total > 0:
                        result["buy_sell_ratio"][exchange] = float(buy_vol / total)

                    total_buy += buy_vol
                    total_sell += sell_vol
                    total_volume += total
                    total_trades += trades
                    result["trade_count"][exchange] = trades

        # Calculate volume shares
        if total_volume > 0:
            for exchange in self._symbol_exchanges.get(symbol, set()):
                snap = self._exchanges.get(exchange)
                if snap:
                    symbol_snap = snap.symbols.get(symbol)
                    if symbol_snap:
                        ex_volume = symbol_snap.buy_volume_1m + symbol_snap.sell_volume_1m
                        result["volume_share"][exchange] = float(ex_volume / total_volume)

        # Global stats
        if total_volume > 0:
            result["_global_buy_ratio"] = float(total_buy / total_volume)
        result["_total_volume_usd"] = float(total_volume)
        result["_total_trades"] = total_trades

        return result

    def get_orderbook_imbalance_cross(self, symbol: str, depth: int = 10) -> Dict[str, float]:
        """
        Get orderbook imbalance on each exchange.

        Imbalance = bid_volume / (bid_volume + ask_volume)
        - 0.5 = balanced
        - > 0.5 = more bids (bullish pressure)
        - < 0.5 = more asks (bearish pressure)

        Returns:
            Dict with exchange imbalances.
            Example: {"binance": 0.65, "bybit": 0.45, "_avg": 0.55}
        """
        imbalances: Dict[str, float] = {}

        for exchange in self._symbol_exchanges.get(symbol, set()):
            snap = self._exchanges.get(exchange)
            if snap:
                symbol_snap = snap.symbols.get(symbol)
                if symbol_snap and symbol_snap.last_orderbook:
                    book = symbol_snap.last_orderbook
                    # Sum volumes for top N levels
                    bid_vol = sum(
                        level.quantity for level in book.bids[:depth]
                    )
                    ask_vol = sum(
                        level.quantity for level in book.asks[:depth]
                    )

                    total_vol = bid_vol + ask_vol
                    if total_vol > 0:
                        imbalances[exchange] = float(bid_vol / total_vol)

        # Calculate average
        if imbalances:
            values = [v for k, v in imbalances.items() if not k.startswith("_")]
            imbalances["_avg"] = sum(values) / len(values)
            imbalances["_std"] = (
                sum((v - imbalances["_avg"]) ** 2 for v in values) / len(values)
            ) ** 0.5

        return imbalances

    def get_price_leader(self, symbol: str, lookback_minutes: int = 5) -> Optional[str]:
        """
        Determine which exchange leads price movements.

        Analyzes price history to find which exchange moves first.
        Returns exchange name that tends to lead.
        """
        # Collect price histories
        histories: Dict[str, List[Tuple[datetime, Decimal]]] = {}

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)

        for exchange in self._symbol_exchanges.get(symbol, set()):
            snap = self._exchanges.get(exchange)
            if snap:
                symbol_snap = snap.symbols.get(symbol)
                if symbol_snap and symbol_snap.price_history:
                    history = [
                        (p.timestamp, p.price)
                        for p in symbol_snap.price_history
                        if p.timestamp >= cutoff
                    ]
                    if history:
                        histories[exchange] = history

        if len(histories) < 2:
            return None

        # Simple heuristic: exchange with earliest significant price move leads
        # More sophisticated analysis would use cross-correlation

        # Find first significant move (> 0.1% change)
        first_movers: Dict[str, datetime] = {}

        for exchange, history in histories.items():
            if len(history) < 2:
                continue

            base_price = history[0][1]
            for ts, price in history[1:]:
                if base_price > 0:
                    change = abs(price - base_price) / base_price
                    if change > Decimal("0.001"):  # 0.1%
                        first_movers[exchange] = ts
                        break

        if first_movers:
            # Return exchange that moved first
            return min(first_movers.items(), key=lambda x: x[1])[0]

        return None

    def get_arbitrage_opportunities(
        self,
        symbol: str,
        min_spread_pct: Decimal = Decimal("0.05")
    ) -> List[Dict[str, Any]]:
        """
        Find arbitrage opportunities between exchanges.

        Args:
            symbol: Symbol to check
            min_spread_pct: Minimum spread % to consider (default 0.05%)

        Returns:
            List of opportunities with buy/sell exchanges and expected profit
        """
        opportunities = []
        prices: Dict[str, Decimal] = {}

        # Collect prices
        for exchange in self._symbol_exchanges.get(symbol, set()):
            snap = self._exchanges.get(exchange)
            if snap:
                symbol_snap = snap.symbols.get(symbol)
                if symbol_snap and symbol_snap.last_price and not symbol_snap.is_stale(10):
                    prices[exchange] = symbol_snap.last_price

        # Find arbitrage
        exchanges = list(prices.keys())
        for i, ex_low in enumerate(exchanges):
            for ex_high in exchanges[i + 1:]:
                price_low = prices[ex_low]
                price_high = prices[ex_high]

                # Ensure price_low <= price_high
                if price_low > price_high:
                    price_low, price_high = price_high, price_low
                    ex_low, ex_high = ex_high, ex_low

                if price_low > 0:
                    spread_pct = (price_high - price_low) / price_low * 100

                    if spread_pct >= min_spread_pct:
                        opportunities.append({
                            "buy_exchange": ex_low,
                            "sell_exchange": ex_high,
                            "buy_price": price_low,
                            "sell_price": price_high,
                            "spread_pct": spread_pct,
                            "symbol": symbol,
                        })

        # Sort by spread (highest first)
        opportunities.sort(key=lambda x: x["spread_pct"], reverse=True)

        return opportunities

    def reset_trade_stats(self) -> None:
        """Reset 1-minute trade statistics (call every minute)."""
        for ex_snap in self._exchanges.values():
            for symbol_snap in ex_snap.symbols.values():
                symbol_snap.trades_1m = 0
                symbol_snap.buy_volume_1m = Decimal(0)
                symbol_snap.sell_volume_1m = Decimal(0)

    # -------------------------------------------------------------------------
    # History Loader Integration
    # -------------------------------------------------------------------------

    async def cache_funding_history(
        self,
        exchange: str,
        symbol: str,
        records: list,
    ) -> None:
        """
        Cache historical funding rate from HistoryLoader.

        Args:
            exchange: Exchange name (bybit, okx, etc.)
            symbol: Unified symbol (BTCUSDT, ETHUSDT)
            records: List of funding rate records from API

        Records format varies by exchange:
        - Bybit: {"fundingRate": "0.0001", "fundingRateTimestamp": "1234567890000", ...}
        - OKX: {"fundingRate": "0.0001", "fundingTime": "1234567890000", ...}
        """
        async with self._lock:
            self.register_exchange(exchange)
            snap = self._exchanges[exchange].get_symbol(symbol)

            for record in records:
                try:
                    # Parse rate
                    rate_str = record.get("fundingRate", record.get("rate", "0"))
                    rate = Decimal(str(rate_str))

                    # Parse timestamp
                    ts_key = None
                    for key in ["fundingRateTimestamp", "fundingTime", "timestamp", "ts"]:
                        if key in record:
                            ts_key = key
                            break

                    if ts_key:
                        ts_ms = int(record[ts_key])
                        timestamp = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                    else:
                        timestamp = datetime.now(timezone.utc)

                    point = FundingPoint(
                        timestamp=timestamp,
                        rate=rate,
                    )
                    snap.funding_history.append(point)

                except (KeyError, ValueError, TypeError) as e:
                    self.logger.debug(
                        "funding_history_parse_error",
                        exchange=exchange,
                        symbol=symbol,
                        error=str(e)
                    )

            # Sort by timestamp (oldest first)
            snap.funding_history = deque(
                sorted(snap.funding_history, key=lambda x: x.timestamp),
                maxlen=27
            )

        self.logger.debug(
            "funding_history_cached",
            exchange=exchange,
            symbol=symbol,
            records=len(snap.funding_history),
        )
