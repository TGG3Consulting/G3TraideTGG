# -*- coding: utf-8 -*-
"""
Exchange Manager - Unified control of all exchange connectors.

Manages connections, subscriptions, and callbacks for all enabled exchanges.
Central point for cross-exchange data collection.

Usage:
    from config.settings import settings
    from src.exchanges.manager import ExchangeManager

    manager = ExchangeManager(settings.exchanges)
    await manager.connect_all()

    # Register callbacks
    manager.on_trade(handle_trade)
    manager.on_orderbook(handle_orderbook)

    # Subscribe to symbols
    await manager.subscribe_symbols(["BTC/USDT", "ETH/USDT"])

    # Cleanup
    await manager.disconnect_all()
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    TYPE_CHECKING,
)

import structlog

from src.exchanges.base import (
    BaseExchange,
    ConnectionState,
    ExchangeConfig,
    ExchangeType,
)
from src.exchanges.models import (
    UnifiedTrade,
    UnifiedOrderBook,
    UnifiedTicker,
    UnifiedFunding,
    UnifiedOpenInterest,
)

if TYPE_CHECKING:
    from config.settings import ExchangesConfig

logger = structlog.get_logger(__name__)


# =============================================================================
# CALLBACK TYPES
# =============================================================================

# Callback with exchange name
TradeCallbackWithExchange = Callable[[str, UnifiedTrade], None]
OrderBookCallbackWithExchange = Callable[[str, UnifiedOrderBook], None]
TickerCallbackWithExchange = Callable[[str, UnifiedTicker], None]
FundingCallbackWithExchange = Callable[[str, UnifiedFunding], None]


# =============================================================================
# EXCHANGE MANAGER
# =============================================================================

@dataclass
class ExchangeManagerStats:
    """Statistics for ExchangeManager."""
    connected_exchanges: int = 0
    total_trades: int = 0
    total_orderbook_updates: int = 0
    subscribed_symbols: int = 0
    last_trade_time: Optional[datetime] = None
    errors: int = 0


class ExchangeManager:
    """
    Manages all exchange connectors for cross-exchange analysis.

    Features:
    - Automatic connector initialization based on config
    - Unified callback system with exchange identification
    - Parallel connection management
    - Symbol subscription across all exchanges
    - Error isolation (one exchange failure doesn't affect others)
    """

    def __init__(self, config: "ExchangesConfig"):
        """
        Initialize exchange manager.

        Args:
            config: ExchangesConfig from settings
        """
        self.config = config
        self.logger = logger.bind(component="exchange_manager")

        # Connectors: exchange_name -> BaseExchange instance
        self._connectors: Dict[str, BaseExchange] = {}

        # Callbacks
        self._trade_callbacks: List[TradeCallbackWithExchange] = []
        self._orderbook_callbacks: List[OrderBookCallbackWithExchange] = []
        self._ticker_callbacks: List[TickerCallbackWithExchange] = []
        self._funding_callbacks: List[FundingCallbackWithExchange] = []

        # State
        self._subscribed_symbols: Set[str] = set()
        self._running = False
        self._stats = ExchangeManagerStats()

        # Initialize connectors
        self._init_connectors()

    def _init_connectors(self) -> None:
        """Initialize connectors for all enabled exchanges."""
        # Import connectors here to avoid circular imports
        from src.exchanges.binance.futures import BinanceFuturesConnector
        from src.exchanges.bybit.connector import BybitConnector
        from src.exchanges.okx.connector import OKXConnector
        from src.exchanges.bitget.connector import BitgetConnector
        from src.exchanges.gate.connector import GateConnector
        from src.exchanges.mexc.connector import MEXCConnector
        from src.exchanges.kucoin.connector import KuCoinConnector
        from src.exchanges.bingx.connector import BingXConnector
        from src.exchanges.htx.connector import HTXConnector
        from src.exchanges.bitmart.connector import BitMartConnector
        from src.exchanges.hyperliquid.connector import HyperliquidConnector
        from src.exchanges.asterdex.connector import AsterDEXConnector
        from src.exchanges.lighter.connector import LighterConnector

        # Mapping of exchange names to connector classes
        connector_classes = {
            "binance": BinanceFuturesConnector,
            "bybit": BybitConnector,
            "okx": OKXConnector,
            "bitget": BitgetConnector,
            "gate": GateConnector,
            "mexc": MEXCConnector,
            "kucoin": KuCoinConnector,
            "bingx": BingXConnector,
            "htx": HTXConnector,
            "bitmart": BitMartConnector,
            "hyperliquid": HyperliquidConnector,
            "asterdex": AsterDEXConnector,
            "lighter": LighterConnector,
        }

        for name, connector_class in connector_classes.items():
            exchange_config = self.config.get(name)
            if exchange_config and exchange_config.enabled:
                try:
                    # Create ExchangeConfig from settings
                    config = self._create_exchange_config(name, exchange_config)
                    connector = connector_class(config)

                    # Register internal callbacks
                    connector.on_trade(
                        lambda t, ex=name: self._on_trade(ex, t)
                    )
                    connector.on_orderbook(
                        lambda ob, ex=name: self._on_orderbook(ex, ob)
                    )

                    self._connectors[name] = connector
                    self.logger.info(
                        "connector_initialized",
                        exchange=name,
                        type=exchange_config.type
                    )
                except Exception as e:
                    self.logger.error(
                        "connector_init_failed",
                        exchange=name,
                        error=str(e)
                    )

        self.logger.info(
            "connectors_ready",
            count=len(self._connectors),
            exchanges=list(self._connectors.keys())
        )

    def _create_exchange_config(
        self,
        name: str,
        settings_config
    ) -> ExchangeConfig:
        """Create ExchangeConfig from settings config."""
        return ExchangeConfig(
            name=name,
            enabled=settings_config.enabled,
            exchange_type=ExchangeType(settings_config.type),
            ws_url=settings_config.ws_url,
            ws_futures_url=getattr(settings_config, "ws_futures_url", None),
            rest_url=settings_config.rest_url,
            rest_futures_url=getattr(settings_config, "rest_futures_url", None),
            rest_requests_per_minute=settings_config.rate_limit.requests_per_minute,
            rest_requests_per_second=settings_config.rate_limit.requests_per_second,
            ws_connections_max=settings_config.rate_limit.ws_connections_max,
            ws_streams_per_connection=settings_config.rate_limit.ws_streams_per_connection,
        )

    # -------------------------------------------------------------------------
    # Connection Management
    # -------------------------------------------------------------------------

    async def connect_all(self, parallel: bool = True) -> Dict[str, bool]:
        """
        Connect to all enabled exchanges.

        Args:
            parallel: If True, connect in parallel (faster but more load)

        Returns:
            Dict mapping exchange name -> connection success
        """
        self._running = True
        results: Dict[str, bool] = {}

        if parallel:
            # Connect all in parallel
            tasks = []
            for name, connector in self._connectors.items():
                tasks.append(self._connect_exchange(name, connector))

            completed = await asyncio.gather(*tasks, return_exceptions=True)

            for (name, _), result in zip(self._connectors.items(), completed):
                if isinstance(result, Exception):
                    results[name] = False
                    self.logger.error(
                        "connect_failed",
                        exchange=name,
                        error=str(result)
                    )
                else:
                    results[name] = result
        else:
            # Connect sequentially
            for name, connector in self._connectors.items():
                results[name] = await self._connect_exchange(name, connector)

        # Update stats
        self._stats.connected_exchanges = sum(1 for v in results.values() if v)

        self.logger.info(
            "connect_all_complete",
            connected=self._stats.connected_exchanges,
            total=len(self._connectors)
        )

        return results

    async def _connect_exchange(
        self,
        name: str,
        connector: BaseExchange
    ) -> bool:
        """Connect a single exchange."""
        try:
            await connector.connect()
            self.logger.info("exchange_connected", exchange=name)
            return True
        except Exception as e:
            self._stats.errors += 1
            self.logger.error(
                "exchange_connect_error",
                exchange=name,
                error=str(e)
            )
            return False

    async def disconnect_all(self) -> None:
        """Disconnect from all exchanges."""
        self._running = False

        tasks = []
        for name, connector in self._connectors.items():
            tasks.append(self._disconnect_exchange(name, connector))

        await asyncio.gather(*tasks, return_exceptions=True)

        self.logger.info("all_exchanges_disconnected")

    async def _disconnect_exchange(
        self,
        name: str,
        connector: BaseExchange
    ) -> None:
        """Disconnect a single exchange."""
        try:
            await connector.disconnect()
            self.logger.info("exchange_disconnected", exchange=name)
        except Exception as e:
            self.logger.error(
                "disconnect_error",
                exchange=name,
                error=str(e)
            )

    # -------------------------------------------------------------------------
    # Subscriptions
    # -------------------------------------------------------------------------

    async def subscribe_symbols(
        self,
        symbols: List[str],
        subscribe_trades: bool = True,
        subscribe_orderbook: bool = True
    ) -> Dict[str, bool]:
        """
        Subscribe to symbols across all connected exchanges.

        Args:
            symbols: List of unified symbols (e.g., ["BTC/USDT"])
            subscribe_trades: Whether to subscribe to trades
            subscribe_orderbook: Whether to subscribe to orderbook

        Returns:
            Dict mapping exchange name -> subscription success
        """
        self._subscribed_symbols.update(symbols)
        results: Dict[str, bool] = {}

        for name, connector in self._connectors.items():
            if not connector.is_connected:
                results[name] = False
                continue

            try:
                if subscribe_trades:
                    await connector.subscribe_trades(symbols)
                if subscribe_orderbook:
                    await connector.subscribe_orderbook(symbols)
                results[name] = True
                self.logger.debug(
                    "subscribed",
                    exchange=name,
                    symbols=len(symbols)
                )
            except Exception as e:
                results[name] = False
                self._stats.errors += 1
                self.logger.error(
                    "subscribe_error",
                    exchange=name,
                    error=str(e)
                )

        self._stats.subscribed_symbols = len(self._subscribed_symbols)
        return results

    async def unsubscribe_symbols(self, symbols: List[str]) -> None:
        """Unsubscribe from symbols on all exchanges."""
        for symbol in symbols:
            self._subscribed_symbols.discard(symbol)

        for name, connector in self._connectors.items():
            if not connector.is_connected:
                continue
            try:
                await connector.unsubscribe("trades", symbols)
                await connector.unsubscribe("orderbook", symbols)
            except Exception as e:
                self.logger.warning(
                    "unsubscribe_error",
                    exchange=name,
                    error=str(e)
                )

    # -------------------------------------------------------------------------
    # Callbacks
    # -------------------------------------------------------------------------

    def on_trade(self, callback: TradeCallbackWithExchange) -> None:
        """
        Register callback for trades from all exchanges.

        Callback signature: (exchange_name: str, trade: UnifiedTrade) -> None
        """
        self._trade_callbacks.append(callback)

    def on_orderbook(self, callback: OrderBookCallbackWithExchange) -> None:
        """
        Register callback for orderbook updates from all exchanges.

        Callback signature: (exchange_name: str, orderbook: UnifiedOrderBook) -> None
        """
        self._orderbook_callbacks.append(callback)

    def on_ticker(self, callback: TickerCallbackWithExchange) -> None:
        """Register callback for ticker updates."""
        self._ticker_callbacks.append(callback)

    def on_funding(self, callback: FundingCallbackWithExchange) -> None:
        """Register callback for funding rate updates."""
        self._funding_callbacks.append(callback)

    def _on_trade(self, exchange: str, trade: UnifiedTrade) -> None:
        """Internal handler for trade events."""
        self._stats.total_trades += 1
        self._stats.last_trade_time = datetime.now(timezone.utc)

        for callback in self._trade_callbacks:
            try:
                callback(exchange, trade)
            except Exception as e:
                self._stats.errors += 1
                self.logger.error(
                    "trade_callback_error",
                    exchange=exchange,
                    error=str(e)
                )

    def _on_orderbook(self, exchange: str, orderbook: UnifiedOrderBook) -> None:
        """Internal handler for orderbook events."""
        self._stats.total_orderbook_updates += 1

        for callback in self._orderbook_callbacks:
            try:
                callback(exchange, orderbook)
            except Exception as e:
                self._stats.errors += 1
                self.logger.error(
                    "orderbook_callback_error",
                    exchange=exchange,
                    error=str(e)
                )

    # -------------------------------------------------------------------------
    # Data Access
    # -------------------------------------------------------------------------

    def get_connector(self, name: str) -> Optional[BaseExchange]:
        """Get a specific exchange connector."""
        return self._connectors.get(name)

    def get_connected_exchanges(self) -> List[str]:
        """Get list of connected exchange names."""
        return [
            name for name, conn in self._connectors.items()
            if conn.is_connected
        ]

    def get_all_exchanges(self) -> List[str]:
        """Get list of all configured exchange names."""
        return list(self._connectors.keys())

    async def get_ticker(
        self,
        symbol: str,
        exchange: Optional[str] = None
    ) -> Dict[str, UnifiedTicker]:
        """
        Get ticker data for a symbol.

        Args:
            symbol: Unified symbol
            exchange: Optional specific exchange, or None for all

        Returns:
            Dict mapping exchange -> ticker
        """
        result: Dict[str, UnifiedTicker] = {}

        if exchange:
            connector = self._connectors.get(exchange)
            if connector and connector.is_connected:
                try:
                    result[exchange] = await connector.get_ticker(symbol)
                except Exception as e:
                    self.logger.warning(
                        "ticker_fetch_failed",
                        exchange=exchange,
                        error=str(e)
                    )
        else:
            tasks = []
            exchanges = []
            for name, connector in self._connectors.items():
                if connector.is_connected:
                    tasks.append(connector.get_ticker(symbol))
                    exchanges.append(name)

            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for name, res in zip(exchanges, results):
                    if not isinstance(res, Exception):
                        result[name] = res

        return result

    async def get_funding_rates(
        self,
        symbol: str
    ) -> Dict[str, UnifiedFunding]:
        """
        Get funding rates for a symbol from all exchanges.

        Args:
            symbol: Unified symbol

        Returns:
            Dict mapping exchange -> funding
        """
        result: Dict[str, UnifiedFunding] = {}
        tasks = []
        exchanges = []

        for name, connector in self._connectors.items():
            if connector.is_connected:
                try:
                    tasks.append(connector.get_funding_rate(symbol))
                    exchanges.append(name)
                except NotImplementedError:
                    pass

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for name, res in zip(exchanges, results):
                if not isinstance(res, Exception):
                    result[name] = res

        return result

    async def get_open_interest(
        self,
        symbol: str
    ) -> Dict[str, UnifiedOpenInterest]:
        """
        Get open interest for a symbol from all exchanges.

        Args:
            symbol: Unified symbol

        Returns:
            Dict mapping exchange -> open interest
        """
        result: Dict[str, UnifiedOpenInterest] = {}
        tasks = []
        exchanges = []

        for name, connector in self._connectors.items():
            if connector.is_connected:
                try:
                    tasks.append(connector.get_open_interest(symbol))
                    exchanges.append(name)
                except NotImplementedError:
                    pass

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for name, res in zip(exchanges, results):
                if not isinstance(res, Exception):
                    result[name] = res

        return result

    # -------------------------------------------------------------------------
    # Stats & Utilities
    # -------------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get manager statistics."""
        return {
            "connected_exchanges": self._stats.connected_exchanges,
            "total_exchanges": len(self._connectors),
            "total_trades": self._stats.total_trades,
            "total_orderbook_updates": self._stats.total_orderbook_updates,
            "subscribed_symbols": self._stats.subscribed_symbols,
            "last_trade_time": (
                self._stats.last_trade_time.isoformat()
                if self._stats.last_trade_time else None
            ),
            "errors": self._stats.errors,
            "exchanges": {
                name: {
                    "connected": conn.is_connected,
                    "state": conn.state.value,
                }
                for name, conn in self._connectors.items()
            }
        }

    def __repr__(self) -> str:
        return (
            f"<ExchangeManager "
            f"connected={self._stats.connected_exchanges}/{len(self._connectors)} "
            f"symbols={self._stats.subscribed_symbols}>"
        )
