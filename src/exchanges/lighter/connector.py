# -*- coding: utf-8 -*-
"""
Lighter DEX Connector.

Lighter is a decentralized perpetual futures exchange with verifiable
order matching and liquidations using ZK proofs on Ethereum L2.

API Documentation: https://apidocs.lighter.xyz
Main Site: https://lighter.xyz

Endpoints:
- REST: https://mainnet.zklighter.elliot.ai
- WebSocket: wss://mainnet.zklighter.elliot.ai/stream

Features:
- Zero-fee trading for standard accounts
- On-chain orderbook with ZK verification
- Price-time priority matching
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Set

import aiohttp
import structlog

from src.exchanges.base import (
    BaseExchange,
    ConnectionState,
    ExchangeCapability,
    ExchangeConfig,
    ExchangeType,
    OrderBookCallback,
    SymbolInfo,
    TradeCallback,
)
from src.exchanges.models import (
    KlineInterval,
    MarketType,
    OrderBookLevel,
    UnifiedFunding,
    UnifiedOpenInterest,
    UnifiedOrderBook,
    UnifiedTicker,
    UnifiedTrade,
)

logger = structlog.get_logger(__name__)


class LighterConnector(BaseExchange):
    """
    Lighter perpetual futures DEX connector.

    Connects to Lighter's REST and WebSocket APIs for market data.
    Supports orderbook streaming, trades, funding rates, and open interest.
    """

    EXCHANGE_NAME = "lighter"
    EXCHANGE_TYPE = ExchangeType.DEX
    CAPABILITIES = {
        ExchangeCapability.FUTURES_PERPETUAL,
        ExchangeCapability.TRADES_STREAM,
        ExchangeCapability.ORDERBOOK_STREAM,
        ExchangeCapability.FUNDING_RATE,
        ExchangeCapability.OPEN_INTEREST,
        ExchangeCapability.HISTORICAL_TRADES,
    }

    # API URLs
    REST_URL = "https://mainnet.zklighter.elliot.ai"
    WS_URL = "wss://mainnet.zklighter.elliot.ai/stream"

    def __init__(self, config: Optional[ExchangeConfig] = None):
        """Initialize Lighter connector."""
        if config is None:
            config = ExchangeConfig(
                name="lighter",
                ws_url=self.WS_URL,
                rest_url=self.REST_URL,
            )
        super().__init__(config)
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._subscribed_channels: Set[str] = set()

        # Market index mapping (Lighter uses numeric market indices)
        self._market_indices: Dict[str, int] = {}
        self._index_to_symbol: Dict[int, str] = {}

        # Asset decimals for price/quantity conversion
        self._asset_decimals: Dict[str, int] = {}

    # =========================================================================
    # CONNECTION MANAGEMENT
    # =========================================================================

    async def connect(self) -> None:
        """Connect to Lighter REST and WebSocket APIs."""
        self._state = ConnectionState.CONNECTING
        self.logger.info("connecting_to_lighter")

        try:
            # Create HTTP session
            timeout = aiohttp.ClientTimeout(total=self.config.request_timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)

            # Test REST API connectivity
            await self._test_connection()

            # Load symbols/markets
            await self.load_symbols()

            # Connect WebSocket
            await self._connect_websocket()

            self._state = ConnectionState.CONNECTED
            self.logger.info(
                "lighter_connected",
                symbols=len(self._symbols),
                markets=len(self._market_indices)
            )

        except Exception as e:
            self._state = ConnectionState.ERROR
            self.logger.error("lighter_connect_failed", error=str(e))
            raise

    async def disconnect(self) -> None:
        """Disconnect from Lighter."""
        self._state = ConnectionState.CLOSING
        self.logger.info("disconnecting_from_lighter")

        # Cancel tasks
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass

        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        # Close WebSocket
        if self._ws and not self._ws.closed:
            await self._ws.close()

        # Close HTTP session
        if self._session and not self._session.closed:
            await self._session.close()

        self._state = ConnectionState.DISCONNECTED
        self.logger.info("lighter_disconnected")

    async def _test_connection(self) -> None:
        """Test REST API connectivity."""
        url = f"{self.REST_URL}/"
        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise ConnectionError(f"Lighter status check failed: {resp.status}")
            data = await resp.json()
            self.logger.debug("lighter_status", data=data)

    async def _connect_websocket(self) -> None:
        """Connect to WebSocket stream."""
        try:
            self._ws = await self._session.ws_connect(
                self.WS_URL,
                heartbeat=self.config.ping_interval,
            )
            self._ws_task = asyncio.create_task(self._ws_handler())
            self._ping_task = asyncio.create_task(self._ws_ping_loop())
            self.logger.info("lighter_ws_connected")
        except Exception as e:
            self.logger.error("lighter_ws_connect_failed", error=str(e))
            raise

    async def _ws_handler(self) -> None:
        """Handle incoming WebSocket messages."""
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_ws_message(json.loads(msg.data))
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    self.logger.error("ws_error", error=str(self._ws.exception()))
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    self.logger.warning("ws_closed")
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error("ws_handler_error", error=str(e))
            if self._state == ConnectionState.CONNECTED:
                asyncio.create_task(self.reconnect())

    async def _ws_ping_loop(self) -> None:
        """Send periodic pings to keep connection alive."""
        try:
            while self._state == ConnectionState.CONNECTED:
                await asyncio.sleep(self.config.ping_interval)
                if self._ws and not self._ws.closed:
                    await self._ws.ping()
        except asyncio.CancelledError:
            pass

    async def _handle_ws_message(self, data: Dict[str, Any]) -> None:
        """Process WebSocket message."""
        channel = data.get("channel", "")

        # CONN-5 FIX: Wrap index parsing in try-except
        try:
            # Order book channel: order_book:{market_index}
            if channel.startswith("order_book:"):
                market_index = int(channel.split(":")[1])
                symbol = self._index_to_symbol.get(market_index, "")
                if symbol:
                    orderbook = self._normalize_ws_orderbook(data, symbol)
                    self._emit_orderbook(orderbook)

            # Market stats channel
            elif channel.startswith("market_stats:"):
                market_index = int(channel.split(":")[1])
                symbol = self._index_to_symbol.get(market_index, "")
                if symbol:
                    ticker = self._normalize_market_stats(data, symbol)
                    self._emit_ticker(ticker)

            # Trade channel
            elif channel.startswith("trades:"):
                market_index = int(channel.split(":")[1])
                symbol = self._index_to_symbol.get(market_index, "")
                if symbol:
                    for trade_data in data.get("trades", []):
                        trade = self._normalize_ws_trade(trade_data, symbol)
                        self._emit_trade(trade)
        except (ValueError, IndexError):
            # CONN-5 FIX: Malformed channel string, skip
            return

    # =========================================================================
    # SYMBOL MANAGEMENT
    # =========================================================================

    async def load_symbols(self) -> List[SymbolInfo]:
        """Load all available trading symbols/markets."""
        # Get order book details for all markets
        url = f"{self.REST_URL}/api/v1/orderBookDetails"

        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to load markets: {resp.status}")
            data = await resp.json()

        symbols = []
        for market in data.get("order_book_details", []):
            market_index = market.get("order_book_id", 0)
            base = market.get("base_asset_symbol", "")
            quote = market.get("quote_asset_symbol", "USDC")

            symbol_unified = f"{base}/{quote}"
            symbol_exchange = f"{base}-{quote}"

            # Store market index mapping
            self._market_indices[symbol_unified] = market_index
            self._index_to_symbol[market_index] = symbol_unified

            # Get decimals for price conversion
            price_decimals = market.get("price_decimals", 8)
            size_decimals = market.get("size_decimals", 8)

            info = SymbolInfo(
                exchange=self.EXCHANGE_NAME,
                symbol_unified=symbol_unified,
                symbol_exchange=symbol_exchange,
                base_asset=base,
                quote_asset=quote,
                market_type=MarketType.FUTURES_PERPETUAL,
                price_precision=price_decimals,
                quantity_precision=size_decimals,
                min_quantity=Decimal(str(10 ** -size_decimals)),
                min_notional=Decimal("1"),
                tick_size=Decimal(str(10 ** -price_decimals)),
                step_size=Decimal(str(10 ** -size_decimals)),
            )

            symbols.append(info)
            self._symbols[symbol_unified] = info
            self._symbol_map[symbol_exchange] = symbol_unified
            self._reverse_symbol_map[symbol_unified] = symbol_exchange

        self.logger.info("symbols_loaded", count=len(symbols))
        return symbols

    def normalize_symbol(self, raw_symbol: str) -> str:
        """Convert BTC-USDC to BTC/USDC format."""
        return raw_symbol.replace("-", "/")

    # =========================================================================
    # SUBSCRIPTIONS
    # =========================================================================

    async def subscribe_trades(
        self,
        symbols: List[str],
        callback: Optional[TradeCallback] = None
    ) -> None:
        """Subscribe to trade streams."""
        if callback:
            self.on_trade(callback)

        for symbol in symbols:
            market_index = self._market_indices.get(symbol)
            if market_index is not None:
                channel = f"trades:{market_index}"
                await self._subscribe_channel(channel)
                self._subscriptions["trades"].add(symbol)

    async def subscribe_orderbook(
        self,
        symbols: List[str],
        callback: Optional[OrderBookCallback] = None,
        depth: int = 20
    ) -> None:
        """Subscribe to orderbook streams."""
        if callback:
            self.on_orderbook(callback)

        for symbol in symbols:
            market_index = self._market_indices.get(symbol)
            if market_index is not None:
                channel = f"order_book:{market_index}"
                await self._subscribe_channel(channel)
                self._subscriptions["orderbook"].add(symbol)

    async def subscribe_ticker(
        self,
        symbols: List[str],
        callback: Optional[Callable] = None
    ) -> None:
        """Subscribe to market stats (ticker) streams."""
        if callback:
            self.on_ticker(callback)

        for symbol in symbols:
            market_index = self._market_indices.get(symbol)
            if market_index is not None:
                channel = f"market_stats:{market_index}"
                await self._subscribe_channel(channel)
                self._subscriptions["ticker"].add(symbol)

    async def _subscribe_channel(self, channel: str) -> None:
        """Subscribe to a WebSocket channel."""
        if not self._ws or self._ws.closed:
            self.logger.warning("ws_not_connected_for_subscribe")
            return

        msg = {
            "op": "subscribe",
            "channel": channel,
        }

        await self._ws.send_json(msg)
        self._subscribed_channels.add(channel)
        self.logger.debug("subscribed_channel", channel=channel)

    async def unsubscribe(self, stream_type: str, symbols: List[str]) -> None:
        """Unsubscribe from streams."""
        if not self._ws or self._ws.closed:
            return

        for symbol in symbols:
            market_index = self._market_indices.get(symbol)
            if market_index is None:
                continue

            if stream_type == "trades":
                channel = f"trades:{market_index}"
            elif stream_type == "orderbook":
                channel = f"order_book:{market_index}"
            elif stream_type == "ticker":
                channel = f"market_stats:{market_index}"
            else:
                continue

            msg = {
                "op": "unsubscribe",
                "channel": channel,
            }
            await self._ws.send_json(msg)
            self._subscribed_channels.discard(channel)

    # =========================================================================
    # REST API METHODS
    # =========================================================================

    async def get_ticker(self, symbol: str) -> UnifiedTicker:
        """Get exchange stats (ticker) for a symbol."""
        url = f"{self.REST_URL}/api/v1/exchangeStats"

        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Exchange stats request failed: {resp.status}")
            data = await resp.json()

        # Find the market in the response
        market_index = self._market_indices.get(symbol)
        for market_stats in data.get("exchange_stats", []):
            if market_stats.get("order_book_id") == market_index:
                return self._normalize_exchange_stats(market_stats, symbol)

        # Return empty ticker if not found
        return UnifiedTicker(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            last_price=Decimal("0"),
            bid_price=Decimal("0"),
            ask_price=Decimal("0"),
        )

    async def get_orderbook(self, symbol: str, limit: int = 20) -> UnifiedOrderBook:
        """Get orderbook snapshot."""
        market_index = self._market_indices.get(symbol)
        if market_index is None:
            raise ValueError(f"Unknown symbol: {symbol}")

        url = f"{self.REST_URL}/api/v1/orderBookOrders"
        params = {"order_book_id": market_index, "limit": limit}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Orderbook request failed: {resp.status}")
            data = await resp.json()

        return self._normalize_rest_orderbook(data, symbol)

    async def get_funding_rate(self, symbol: str) -> UnifiedFunding:
        """Get current funding rate."""
        url = f"{self.REST_URL}/api/v1/funding-rates"

        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Funding rate request failed: {resp.status}")
            data = await resp.json()

        # Find funding for this symbol
        market_index = self._market_indices.get(symbol)
        for funding in data.get("funding_rates", []):
            if funding.get("order_book_id") == market_index:
                return self._normalize_funding(funding, symbol)

        # Return zero funding if not found
        return UnifiedFunding(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            funding_rate=Decimal("0"),
        )

    async def get_open_interest(self, symbol: str) -> UnifiedOpenInterest:
        """Get open interest from orderbook details."""
        market_index = self._market_indices.get(symbol)
        if market_index is None:
            raise ValueError(f"Unknown symbol: {symbol}")

        url = f"{self.REST_URL}/api/v1/orderBookDetails"
        params = {"order_book_id": market_index}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Order book details request failed: {resp.status}")
            data = await resp.json()

        details = data.get("order_book_details", [{}])[0]
        oi = Decimal(str(details.get("open_interest", "0")))

        # Get price for USD conversion
        ticker = await self.get_ticker(symbol)
        oi_usd = oi * ticker.last_price if ticker.last_price else Decimal("0")

        return UnifiedOpenInterest(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            open_interest=oi,
            open_interest_usd=oi_usd,
        )

    async def get_recent_trades(self, symbol: str, limit: int = 100) -> List[UnifiedTrade]:
        """Get recent trades."""
        market_index = self._market_indices.get(symbol)
        if market_index is None:
            raise ValueError(f"Unknown symbol: {symbol}")

        url = f"{self.REST_URL}/api/v1/recentTrades"
        params = {"market_id": market_index, "limit": limit}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Recent trades request failed: {resp.status}")
            data = await resp.json()

        return [
            self._normalize_rest_trade(t, symbol)
            for t in data.get("trades", [])
        ]

    # =========================================================================
    # DATA NORMALIZATION
    # =========================================================================

    def normalize_trade(self, raw: Dict[str, Any]) -> UnifiedTrade:
        """Normalize trade data (generic)."""
        symbol = raw.get("symbol", "UNKNOWN")
        return self._normalize_rest_trade(raw, symbol)

    def _normalize_rest_trade(self, raw: Dict[str, Any], symbol: str) -> UnifiedTrade:
        """Normalize REST API trade response."""
        # Parse timestamp
        ts = raw.get("timestamp", raw.get("time", 0))
        if isinstance(ts, str):
            timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            timestamp = self.parse_timestamp(ts)

        return UnifiedTrade(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            trade_id=str(raw.get("id", raw.get("trade_id", ""))),
            timestamp=timestamp,
            price=Decimal(str(raw.get("price", "0"))),
            quantity=Decimal(str(raw.get("size", raw.get("quantity", "0")))),
            side="buy" if raw.get("is_bid", raw.get("side") == "buy") else "sell",
            is_maker=raw.get("is_maker", False),
        )

    def _normalize_ws_trade(self, raw: Dict[str, Any], symbol: str) -> UnifiedTrade:
        """Normalize WebSocket trade message."""
        return self._normalize_rest_trade(raw, symbol)

    def normalize_orderbook(self, raw: Dict[str, Any], symbol: str = "") -> UnifiedOrderBook:
        """Normalize orderbook data."""
        return self._normalize_rest_orderbook(raw, symbol)

    def _normalize_rest_orderbook(self, raw: Dict[str, Any], symbol: str) -> UnifiedOrderBook:
        """Normalize REST orderbook response."""
        bids = []
        asks = []

        for order in raw.get("bids", []):
            bids.append(OrderBookLevel(
                price=Decimal(str(order.get("price", "0"))),
                quantity=Decimal(str(order.get("size", "0"))),
            ))

        for order in raw.get("asks", []):
            asks.append(OrderBookLevel(
                price=Decimal(str(order.get("price", "0"))),
                quantity=Decimal(str(order.get("size", "0"))),
            ))

        # Sort: bids descending, asks ascending
        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)

        return UnifiedOrderBook(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            bids=bids,
            asks=asks,
        )

    def _normalize_ws_orderbook(self, raw: Dict[str, Any], symbol: str) -> UnifiedOrderBook:
        """Normalize WebSocket orderbook update."""
        bids = []
        asks = []

        for bid in raw.get("bids", []):
            if isinstance(bid, dict):
                bids.append(OrderBookLevel(
                    price=Decimal(str(bid.get("price", "0"))),
                    quantity=Decimal(str(bid.get("size", "0"))),
                ))
            elif isinstance(bid, list) and len(bid) >= 2:
                bids.append(OrderBookLevel(
                    price=Decimal(str(bid[0])),
                    quantity=Decimal(str(bid[1])),
                ))

        for ask in raw.get("asks", []):
            if isinstance(ask, dict):
                asks.append(OrderBookLevel(
                    price=Decimal(str(ask.get("price", "0"))),
                    quantity=Decimal(str(ask.get("size", "0"))),
                ))
            elif isinstance(ask, list) and len(ask) >= 2:
                asks.append(OrderBookLevel(
                    price=Decimal(str(ask[0])),
                    quantity=Decimal(str(ask[1])),
                ))

        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)

        return UnifiedOrderBook(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            bids=bids,
            asks=asks,
            last_update_id=raw.get("nonce", 0),
        )

    def normalize_ticker(self, raw: Dict[str, Any]) -> UnifiedTicker:
        """Normalize ticker/market stats data."""
        symbol = raw.get("symbol", "UNKNOWN")
        return self._normalize_exchange_stats(raw, symbol)

    def _normalize_exchange_stats(self, raw: Dict[str, Any], symbol: str) -> UnifiedTicker:
        """Normalize exchange stats response."""
        return UnifiedTicker(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            last_price=Decimal(str(raw.get("last_price", raw.get("price", "0")))),
            bid_price=Decimal(str(raw.get("best_bid", "0"))),
            ask_price=Decimal(str(raw.get("best_ask", "0"))),
            high_24h=Decimal(str(raw.get("high_24h", "0"))),
            low_24h=Decimal(str(raw.get("low_24h", "0"))),
            volume_24h=Decimal(str(raw.get("volume_24h", "0"))),
            volume_24h_quote=Decimal(str(raw.get("quote_volume_24h", "0"))),
            price_change_24h=Decimal(str(raw.get("price_change_24h", "0"))),
            price_change_pct_24h=Decimal(str(raw.get("price_change_pct_24h", "0"))),
        )

    def _normalize_market_stats(self, raw: Dict[str, Any], symbol: str) -> UnifiedTicker:
        """Normalize WebSocket market stats message."""
        return self._normalize_exchange_stats(raw, symbol)

    def normalize_funding(self, raw: Dict[str, Any]) -> UnifiedFunding:
        """Normalize funding rate data."""
        symbol = raw.get("symbol", "UNKNOWN")
        return self._normalize_funding(raw, symbol)

    def _normalize_funding(self, raw: Dict[str, Any], symbol: str) -> UnifiedFunding:
        """Normalize funding rate response."""
        # Parse timestamp
        ts = raw.get("timestamp", raw.get("time", 0))
        if isinstance(ts, str):
            timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            timestamp = self.parse_timestamp(ts) if ts else datetime.now(timezone.utc)

        # Parse next funding time
        next_funding = None
        next_ts = raw.get("next_funding_time")
        if next_ts:
            if isinstance(next_ts, str):
                next_funding = datetime.fromisoformat(next_ts.replace("Z", "+00:00"))
            else:
                next_funding = self.parse_timestamp(next_ts)

        return UnifiedFunding(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=timestamp,
            funding_rate=Decimal(str(raw.get("funding_rate", raw.get("rate", "0")))),
            next_funding_time=next_funding,
            mark_price=Decimal(str(raw.get("mark_price", "0"))),
            index_price=Decimal(str(raw.get("index_price", "0"))),
        )
