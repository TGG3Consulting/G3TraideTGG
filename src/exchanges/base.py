# -*- coding: utf-8 -*-
"""
Abstract Base Class for Exchange Connectors.

All exchange implementations must inherit from BaseExchange and implement
the required abstract methods. This ensures a consistent interface for
cross-exchange operations.

Design principles:
1. Async-first: All I/O operations are async
2. Stateful connections: WebSocket connections are managed internally
3. Normalized data: All methods return unified data models
4. Error handling: Exchanges handle their own reconnection logic
5. Rate limiting: Built-in rate limiting per exchange
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum, auto
from typing import (
    AsyncIterator,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    TypeVar,
)

import structlog

from src.exchanges.models import (
    UnifiedTrade,
    UnifiedOrderBook,
    UnifiedTicker,
    UnifiedFunding,
    UnifiedOpenInterest,
    UnifiedKline,
    UnifiedLiquidation,
    KlineInterval,
    MarketType,
)

logger = structlog.get_logger(__name__)

T = TypeVar("T")


# =============================================================================
# ENUMS
# =============================================================================

class ExchangeType(str, Enum):
    """Type of exchange."""
    CEX = "CEX"          # Centralized Exchange
    DEX = "DEX"          # Decentralized Exchange
    HYBRID = "HYBRID"    # Hybrid (e.g., dYdX v4)


class ConnectionState(str, Enum):
    """WebSocket connection state."""
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    CLOSING = "CLOSING"
    ERROR = "ERROR"


class ExchangeCapability(Enum):
    """
    Capabilities that an exchange may support.

    Used to check feature availability before calling methods.
    """
    # Market types
    SPOT = auto()              # Spot trading
    SPOT_TRADING = SPOT        # Alias for compatibility
    FUTURES = auto()           # Futures trading (generic)
    FUTURES_PERPETUAL = auto() # Perpetual futures
    FUTURES_DELIVERY = auto()  # Delivery futures
    OPTIONS = auto()
    MARGIN = auto()

    # Connection
    WEBSOCKET = auto()         # WebSocket support

    # Data streams
    TRADES_STREAM = auto()
    TRADES = TRADES_STREAM     # Alias
    ORDERBOOK_STREAM = auto()
    ORDERBOOK = ORDERBOOK_STREAM  # Alias
    KLINE_STREAM = auto()
    TICKER_STREAM = auto()
    LIQUIDATION_STREAM = auto()

    # Futures-specific
    FUNDING_RATE = auto()
    OPEN_INTEREST = auto()
    LONG_SHORT_RATIO = auto()

    # Advanced
    HISTORICAL_TRADES = auto()
    HISTORICAL_KLINES = auto()
    HISTORICAL_FUNDING = auto()
    HISTORICAL_OI = auto()


# =============================================================================
# CALLBACK TYPES
# =============================================================================

TradeCallback = Callable[[UnifiedTrade], None]
OrderBookCallback = Callable[[UnifiedOrderBook], None]
TickerCallback = Callable[[UnifiedTicker], None]
KlineCallback = Callable[[UnifiedKline], None]
FundingCallback = Callable[[UnifiedFunding], None]
LiquidationCallback = Callable[[UnifiedLiquidation], None]


# =============================================================================
# EXCHANGE CONFIG
# =============================================================================

@dataclass
class ExchangeConfig:
    """
    Configuration for a single exchange.

    Loaded from config.yaml exchanges section.
    """
    name: str
    enabled: bool = True
    exchange_type: ExchangeType = ExchangeType.CEX

    # Endpoints
    ws_url: Optional[str] = None
    ws_futures_url: Optional[str] = None
    rest_url: Optional[str] = None
    rest_futures_url: Optional[str] = None

    # Rate limits
    rest_requests_per_minute: int = 1200
    rest_requests_per_second: int = 20
    ws_connections_max: int = 5
    ws_streams_per_connection: int = 200
    ws_messages_per_second: int = 10

    # Timeouts (seconds)
    connect_timeout: float = 30.0
    request_timeout: float = 10.0
    ping_interval: float = 20.0
    ping_timeout: float = 10.0

    # Reconnection
    reconnect_delay: float = 5.0
    max_reconnect_attempts: int = 10
    reconnect_backoff_factor: float = 1.5
    max_reconnect_delay: float = 60.0

    # Symbol mapping (exchange format -> unified format)
    symbol_separator: str = ""  # e.g., "" for BTCUSDT, "/" for BTC/USDT
    futures_suffix: str = ""    # e.g., "USDT" for BTCUSDT perpetual


# =============================================================================
# SYMBOL MAPPING
# =============================================================================

@dataclass
class SymbolInfo:
    """Information about a trading symbol."""
    exchange: str
    symbol_unified: str      # BTC/USDT
    symbol_exchange: str     # BTCUSDT (exchange format)
    base_asset: str          # BTC
    quote_asset: str         # USDT
    market_type: MarketType
    price_precision: int     # Decimal places for price
    quantity_precision: int  # Decimal places for quantity
    min_quantity: Decimal
    min_notional: Decimal    # Min order value
    tick_size: Decimal       # Min price increment
    step_size: Decimal       # Min quantity increment
    is_active: bool = True
    # CONN-3 FIX: Для фьючерсов (размер контракта в базовом активе)
    contract_size: Optional[Decimal] = None


# =============================================================================
# BASE EXCHANGE CLASS
# =============================================================================

class BaseExchange(ABC):
    """
    Abstract base class for all exchange connectors.

    Subclasses must implement:
    - connect() / disconnect()
    - subscribe_trades() / subscribe_orderbook()
    - get_ticker() / get_funding_rate() / get_open_interest()
    - normalize_* methods for data conversion

    Example implementation for a new exchange:

        class BinanceExchange(BaseExchange):
            EXCHANGE_NAME = "binance"
            EXCHANGE_TYPE = ExchangeType.CEX
            CAPABILITIES = {
                ExchangeCapability.SPOT_TRADING,
                ExchangeCapability.FUTURES_PERPETUAL,
                ExchangeCapability.TRADES_STREAM,
                ...
            }

            async def connect(self):
                ...
    """

    # Override in subclass
    EXCHANGE_NAME: str = "base"
    EXCHANGE_TYPE: ExchangeType = ExchangeType.CEX
    CAPABILITIES: Set[ExchangeCapability] = set()

    def __init__(self, config: ExchangeConfig):
        """
        Initialize exchange connector.

        Args:
            config: Exchange configuration from config.yaml
        """
        self.config = config
        self._state = ConnectionState.DISCONNECTED
        self._subscriptions: Dict[str, Set[str]] = {
            "trades": set(),
            "orderbook": set(),
            "kline": set(),
            "ticker": set(),
            "liquidation": set(),
        }

        # Callbacks
        self._trade_callbacks: List[TradeCallback] = []
        self._orderbook_callbacks: List[OrderBookCallback] = []
        self._ticker_callbacks: List[TickerCallback] = []
        self._kline_callbacks: List[KlineCallback] = []
        self._funding_callbacks: List[FundingCallback] = []
        self._liquidation_callbacks: List[LiquidationCallback] = []

        # Symbol cache
        self._symbols: Dict[str, SymbolInfo] = {}
        self._symbol_map: Dict[str, str] = {}  # exchange -> unified
        self._reverse_symbol_map: Dict[str, str] = {}  # unified -> exchange

        # Connection management
        self._ws_connections: List = []
        self._reconnect_task: Optional[asyncio.Task] = None
        self._last_message_time: Optional[datetime] = None

        self.logger = logger.bind(exchange=self.EXCHANGE_NAME)

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def name(self) -> str:
        """Exchange name (lowercase)."""
        return self.EXCHANGE_NAME

    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """True if connected and ready."""
        return self._state == ConnectionState.CONNECTED

    @property
    def capabilities(self) -> Set[ExchangeCapability]:
        """Set of supported capabilities."""
        return self.CAPABILITIES

    def has_capability(self, cap: ExchangeCapability) -> bool:
        """Check if exchange supports a capability."""
        return cap in self.CAPABILITIES

    # -------------------------------------------------------------------------
    # Connection Management (Abstract)
    # -------------------------------------------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        """
        Establish connection(s) to exchange.

        Should:
        - Connect WebSocket(s)
        - Load symbol information
        - Set state to CONNECTED
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """
        Gracefully disconnect from exchange.

        Should:
        - Close all WebSocket connections
        - Cancel pending tasks
        - Set state to DISCONNECTED
        """
        pass

    async def reconnect(self) -> None:
        """
        Reconnect with exponential backoff.

        Called automatically on connection loss.
        """
        if self._state == ConnectionState.RECONNECTING:
            return

        self._state = ConnectionState.RECONNECTING
        delay = self.config.reconnect_delay

        for attempt in range(self.config.max_reconnect_attempts):
            self.logger.info(
                "reconnecting",
                attempt=attempt + 1,
                delay=delay
            )

            await asyncio.sleep(delay)

            try:
                await self.disconnect()
                await self.connect()

                # Resubscribe
                await self._resubscribe_all()

                self.logger.info("reconnected")
                return

            except Exception as e:
                self.logger.error(
                    "reconnect_failed",
                    attempt=attempt + 1,
                    error=str(e)
                )
                delay = min(
                    delay * self.config.reconnect_backoff_factor,
                    self.config.max_reconnect_delay
                )

        self._state = ConnectionState.ERROR
        self.logger.error("reconnect_exhausted")

    async def _resubscribe_all(self) -> None:
        """Resubscribe to all previous subscriptions."""
        if self._subscriptions["trades"]:
            await self.subscribe_trades(list(self._subscriptions["trades"]))
        if self._subscriptions["orderbook"]:
            await self.subscribe_orderbook(list(self._subscriptions["orderbook"]))
        if self._subscriptions["ticker"]:
            await self.subscribe_ticker(list(self._subscriptions["ticker"]))

    # -------------------------------------------------------------------------
    # Symbol Management
    # -------------------------------------------------------------------------

    @abstractmethod
    async def load_symbols(self) -> List[SymbolInfo]:
        """
        Load all available trading symbols from exchange.

        Returns:
            List of SymbolInfo with unified and exchange formats
        """
        pass

    def get_symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        """Get symbol info by unified or exchange symbol."""
        if symbol in self._symbols:
            return self._symbols[symbol]
        # Try reverse lookup
        unified = self._symbol_map.get(symbol)
        if unified:
            return self._symbols.get(unified)
        return None

    def to_unified_symbol(self, exchange_symbol: str) -> str:
        """Convert exchange symbol format to unified format."""
        return self._symbol_map.get(exchange_symbol, exchange_symbol)

    def to_exchange_symbol(self, unified_symbol: str) -> str:
        """Convert unified symbol format to exchange format."""
        return self._reverse_symbol_map.get(unified_symbol, unified_symbol)

    @abstractmethod
    def normalize_symbol(self, raw_symbol: str) -> str:
        """
        Normalize raw exchange symbol to unified format.

        Example: "BTCUSDT" -> "BTC/USDT"
        """
        pass

    # -------------------------------------------------------------------------
    # WebSocket Subscriptions (Abstract)
    # -------------------------------------------------------------------------

    @abstractmethod
    async def subscribe_trades(
        self,
        symbols: List[str],
        callback: Optional[TradeCallback] = None
    ) -> None:
        """
        Subscribe to real-time trade stream.

        Args:
            symbols: List of unified symbols (e.g., ["BTC/USDT", "ETH/USDT"])
            callback: Optional callback for each trade
        """
        pass

    @abstractmethod
    async def subscribe_orderbook(
        self,
        symbols: List[str],
        callback: Optional[OrderBookCallback] = None,
        depth: int = 20
    ) -> None:
        """
        Subscribe to real-time order book updates.

        Args:
            symbols: List of unified symbols
            callback: Optional callback for each update
            depth: Number of price levels (5, 10, 20, etc.)
        """
        pass

    async def subscribe_ticker(
        self,
        symbols: List[str],
        callback: Optional[TickerCallback] = None
    ) -> None:
        """
        Subscribe to real-time ticker updates.

        Default implementation: not supported.
        """
        raise NotImplementedError(
            f"{self.EXCHANGE_NAME} does not support ticker stream"
        )

    async def subscribe_klines(
        self,
        symbols: List[str],
        interval: KlineInterval = KlineInterval.M1,
        callback: Optional[KlineCallback] = None
    ) -> None:
        """
        Subscribe to real-time kline/candlestick updates.

        Default implementation: not supported.
        """
        raise NotImplementedError(
            f"{self.EXCHANGE_NAME} does not support kline stream"
        )

    async def subscribe_liquidations(
        self,
        symbols: List[str],
        callback: Optional[LiquidationCallback] = None
    ) -> None:
        """
        Subscribe to real-time liquidation events (futures).

        Default implementation: not supported.
        """
        raise NotImplementedError(
            f"{self.EXCHANGE_NAME} does not support liquidation stream"
        )

    @abstractmethod
    async def unsubscribe(
        self,
        stream_type: str,
        symbols: List[str]
    ) -> None:
        """
        Unsubscribe from a stream.

        Args:
            stream_type: "trades", "orderbook", "ticker", "kline"
            symbols: List of unified symbols to unsubscribe
        """
        pass

    # -------------------------------------------------------------------------
    # REST API Methods (Abstract)
    # -------------------------------------------------------------------------

    @abstractmethod
    async def get_ticker(self, symbol: str) -> UnifiedTicker:
        """
        Get current ticker data.

        Args:
            symbol: Unified symbol (e.g., "BTC/USDT")

        Returns:
            UnifiedTicker with 24h data
        """
        pass

    @abstractmethod
    async def get_orderbook(
        self,
        symbol: str,
        limit: int = 20
    ) -> UnifiedOrderBook:
        """
        Get current order book snapshot.

        Args:
            symbol: Unified symbol
            limit: Number of price levels

        Returns:
            UnifiedOrderBook snapshot
        """
        pass

    async def get_funding_rate(self, symbol: str) -> UnifiedFunding:
        """
        Get current funding rate (perpetual futures).

        Default implementation: not supported.
        """
        if not self.has_capability(ExchangeCapability.FUNDING_RATE):
            raise NotImplementedError(
                f"{self.EXCHANGE_NAME} does not support funding rate"
            )
        raise NotImplementedError()

    async def get_open_interest(self, symbol: str) -> UnifiedOpenInterest:
        """
        Get current open interest (futures).

        Default implementation: not supported.
        """
        if not self.has_capability(ExchangeCapability.OPEN_INTEREST):
            raise NotImplementedError(
                f"{self.EXCHANGE_NAME} does not support open interest"
            )
        raise NotImplementedError()

    async def get_historical_klines(
        self,
        symbol: str,
        interval: KlineInterval,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 500
    ) -> List[UnifiedKline]:
        """
        Get historical klines/candlesticks.

        Default implementation: not supported.
        """
        if not self.has_capability(ExchangeCapability.HISTORICAL_KLINES):
            raise NotImplementedError(
                f"{self.EXCHANGE_NAME} does not support historical klines"
            )
        raise NotImplementedError()

    async def get_recent_trades(
        self,
        symbol: str,
        limit: int = 500
    ) -> List[UnifiedTrade]:
        """
        Get recent trades.

        Default implementation: not supported.
        """
        if not self.has_capability(ExchangeCapability.HISTORICAL_TRADES):
            raise NotImplementedError(
                f"{self.EXCHANGE_NAME} does not support historical trades"
            )
        raise NotImplementedError()

    # -------------------------------------------------------------------------
    # Batch Methods (For efficiency)
    # -------------------------------------------------------------------------

    async def get_all_tickers(self) -> Dict[str, UnifiedTicker]:
        """
        Get tickers for all symbols.

        Returns:
            Dict mapping unified symbol -> UnifiedTicker
        """
        # Default: fetch one by one (override for batch API)
        result = {}
        for symbol in self._symbols:
            try:
                result[symbol] = await self.get_ticker(symbol)
            except Exception as e:
                self.logger.warning(
                    "ticker_fetch_failed",
                    symbol=symbol,
                    error=str(e)
                )
        return result

    async def get_all_funding_rates(self) -> Dict[str, UnifiedFunding]:
        """
        Get funding rates for all perpetual futures.

        Returns:
            Dict mapping unified symbol -> UnifiedFunding
        """
        if not self.has_capability(ExchangeCapability.FUNDING_RATE):
            return {}

        result = {}
        for symbol, info in self._symbols.items():
            if info.market_type == MarketType.FUTURES_PERPETUAL:
                try:
                    result[symbol] = await self.get_funding_rate(symbol)
                except Exception as e:
                    self.logger.warning(
                        "funding_fetch_failed",
                        symbol=symbol,
                        error=str(e)
                    )
        return result

    async def get_all_open_interest(self) -> Dict[str, UnifiedOpenInterest]:
        """
        Get open interest for all futures.

        Returns:
            Dict mapping unified symbol -> UnifiedOpenInterest
        """
        if not self.has_capability(ExchangeCapability.OPEN_INTEREST):
            return {}

        result = {}
        for symbol, info in self._symbols.items():
            if info.market_type in (
                MarketType.FUTURES_PERPETUAL,
                MarketType.FUTURES_DELIVERY
            ):
                try:
                    result[symbol] = await self.get_open_interest(symbol)
                except Exception as e:
                    self.logger.warning(
                        "oi_fetch_failed",
                        symbol=symbol,
                        error=str(e)
                    )
        return result

    # -------------------------------------------------------------------------
    # Data Normalization (Abstract)
    # -------------------------------------------------------------------------

    @abstractmethod
    def normalize_trade(self, raw: dict) -> UnifiedTrade:
        """
        Convert raw exchange trade data to UnifiedTrade.

        Args:
            raw: Raw trade data from exchange API/WebSocket

        Returns:
            UnifiedTrade instance
        """
        pass

    @abstractmethod
    def normalize_orderbook(self, raw: dict) -> UnifiedOrderBook:
        """
        Convert raw exchange orderbook data to UnifiedOrderBook.

        Args:
            raw: Raw orderbook data from exchange API/WebSocket

        Returns:
            UnifiedOrderBook instance
        """
        pass

    def normalize_ticker(self, raw: dict) -> UnifiedTicker:
        """
        Convert raw ticker data to UnifiedTicker.

        Default implementation must be overridden.
        """
        raise NotImplementedError()

    def normalize_funding(self, raw: dict) -> UnifiedFunding:
        """
        Convert raw funding data to UnifiedFunding.

        Default implementation must be overridden.
        """
        raise NotImplementedError()

    def normalize_kline(self, raw: dict) -> UnifiedKline:
        """
        Convert raw kline data to UnifiedKline.

        Default implementation must be overridden.
        """
        raise NotImplementedError()

    # -------------------------------------------------------------------------
    # Callback Registration
    # -------------------------------------------------------------------------

    def on_trade(self, callback: TradeCallback) -> None:
        """Register a trade callback."""
        self._trade_callbacks.append(callback)

    def on_orderbook(self, callback: OrderBookCallback) -> None:
        """Register an orderbook callback."""
        self._orderbook_callbacks.append(callback)

    def on_ticker(self, callback: TickerCallback) -> None:
        """Register a ticker callback."""
        self._ticker_callbacks.append(callback)

    def on_kline(self, callback: KlineCallback) -> None:
        """Register a kline callback."""
        self._kline_callbacks.append(callback)

    def on_funding(self, callback: FundingCallback) -> None:
        """Register a funding rate callback."""
        self._funding_callbacks.append(callback)

    def on_liquidation(self, callback: LiquidationCallback) -> None:
        """Register a liquidation callback."""
        self._liquidation_callbacks.append(callback)

    def _emit_trade(self, trade: UnifiedTrade) -> None:
        """Emit trade to all registered callbacks."""
        for callback in self._trade_callbacks:
            try:
                callback(trade)
            except Exception as e:
                self.logger.error("trade_callback_error", error=str(e))

    def _emit_orderbook(self, orderbook: UnifiedOrderBook) -> None:
        """Emit orderbook to all registered callbacks."""
        for callback in self._orderbook_callbacks:
            try:
                callback(orderbook)
            except Exception as e:
                self.logger.error("orderbook_callback_error", error=str(e))

    def _emit_ticker(self, ticker: UnifiedTicker) -> None:
        """Emit ticker to all registered callbacks."""
        for callback in self._ticker_callbacks:
            try:
                callback(ticker)
            except Exception as e:
                self.logger.error("ticker_callback_error", error=str(e))

    # -------------------------------------------------------------------------
    # Utilities
    # -------------------------------------------------------------------------

    @staticmethod
    def parse_timestamp(ts: int | float | str) -> datetime:
        """
        Parse timestamp to datetime (UTC).

        Handles milliseconds and seconds.
        """
        if isinstance(ts, str):
            ts = float(ts)

        # Detect milliseconds vs seconds
        if ts > 1e12:  # Likely milliseconds
            ts = ts / 1000

        return datetime.fromtimestamp(ts, tz=timezone.utc)

    @staticmethod
    def to_decimal(value: str | int | float) -> Decimal:
        """Convert value to Decimal safely."""
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} "
            f"name={self.EXCHANGE_NAME} "
            f"state={self._state.value}>"
        )


# =============================================================================
# EXCHANGE REGISTRY
# =============================================================================

class ExchangeRegistry:
    """
    Registry for managing multiple exchange instances.

    Usage:
        registry = ExchangeRegistry()
        registry.register("binance", BinanceExchange, config)

        binance = await registry.get("binance")
        await binance.connect()

        # Get all connected exchanges
        for exchange in registry.connected():
            tickers = await exchange.get_all_tickers()
    """

    def __init__(self):
        self._exchanges: Dict[str, BaseExchange] = {}
        self._configs: Dict[str, ExchangeConfig] = {}
        self.logger = structlog.get_logger("exchange_registry")

    def register(
        self,
        name: str,
        exchange_class: type,
        config: ExchangeConfig
    ) -> None:
        """
        Register an exchange.

        Args:
            name: Exchange name (lowercase)
            exchange_class: Exchange class (subclass of BaseExchange)
            config: Exchange configuration
        """
        self._configs[name] = config
        self._exchanges[name] = exchange_class(config)
        self.logger.info("exchange_registered", name=name)

    def get(self, name: str) -> Optional[BaseExchange]:
        """Get exchange by name."""
        return self._exchanges.get(name)

    def all(self) -> List[BaseExchange]:
        """Get all registered exchanges."""
        return list(self._exchanges.values())

    def connected(self) -> List[BaseExchange]:
        """Get all connected exchanges."""
        return [
            ex for ex in self._exchanges.values()
            if ex.is_connected
        ]

    def enabled(self) -> List[BaseExchange]:
        """Get all enabled exchanges."""
        return [
            ex for ex in self._exchanges.values()
            if self._configs[ex.name].enabled
        ]

    async def connect_all(self, parallel: bool = True) -> None:
        """
        Connect all enabled exchanges.

        Args:
            parallel: If True, connect in parallel
        """
        enabled = self.enabled()

        if parallel:
            await asyncio.gather(
                *[ex.connect() for ex in enabled],
                return_exceptions=True
            )
        else:
            for ex in enabled:
                try:
                    await ex.connect()
                except Exception as e:
                    self.logger.error(
                        "connect_failed",
                        exchange=ex.name,
                        error=str(e)
                    )

    async def disconnect_all(self) -> None:
        """Disconnect all exchanges."""
        await asyncio.gather(
            *[ex.disconnect() for ex in self._exchanges.values()],
            return_exceptions=True
        )
