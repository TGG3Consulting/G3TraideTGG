# -*- coding: utf-8 -*-
"""
BitMart exchange connector.

WebSocket: wss://ws-manager-compress.bitmart.com/api?protocol=1.1
REST: https://api-cloud.bitmart.com
Rate limits: 150/5s REST (VERY STRICT!), 25 topics WS

WARNING: BitMart has VERY strict rate limits. Use with extreme caution.
"""

import asyncio
import gzip
import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Set

import aiohttp

from src.exchanges.base import (
    BaseExchange,
    ConnectionState,
    ExchangeCapability,
    ExchangeConfig,
    ExchangeType,
    SymbolInfo,
)
from src.exchanges.models import (
    MarketType,
    UnifiedFunding,
    UnifiedOpenInterest,
    UnifiedOrderBook,
    UnifiedTicker,
    UnifiedTrade,
    OrderBookLevel,
    Side,
)
from src.exchanges.rate_limiter import RateLimiter, RateLimitConfig

logger = logging.getLogger(__name__)


class BitMartConnector(BaseExchange):
    """BitMart Futures connector with VERY strict rate limiting."""

    EXCHANGE_NAME = "bitmart"
    EXCHANGE_TYPE = ExchangeType.CEX
    CAPABILITIES = {
        ExchangeCapability.SPOT,
        ExchangeCapability.FUTURES,
        ExchangeCapability.WEBSOCKET,
        ExchangeCapability.TRADES,
        ExchangeCapability.ORDERBOOK,
        ExchangeCapability.FUNDING_RATE,
        ExchangeCapability.OPEN_INTEREST,
    }

    DEFAULT_WS_URL = "wss://openapi-ws-v2.bitmart.com/api?protocol=1.1"
    DEFAULT_REST_URL = "https://api-cloud-v2.bitmart.com"

    def __init__(self, config: Optional[ExchangeConfig] = None):
        """Initialize BitMart connector."""
        if config is None:
            config = ExchangeConfig(
                name="bitmart",
                ws_url=self.DEFAULT_WS_URL,
                rest_url=self.DEFAULT_REST_URL,
            )
        super().__init__(config)

        # BitMart URLs (Futures)
        self._ws_url = config.ws_url or self.DEFAULT_WS_URL
        self._rest_url = config.rest_url or self.DEFAULT_REST_URL

        # VERY STRICT rate limiter: 150/5s = 30/s, be VERY conservative
        self._rate_limiter = RateLimiter(
            RateLimitConfig(
                requests_per_second=5,  # Very conservative
                requests_per_minute=150,
            )
        )

        # HTTP session
        self._session: Optional[aiohttp.ClientSession] = None

        # WebSocket state
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._subscriptions: Dict[str, Set[str]] = {
            "trades": set(),
            "orderbook": set(),
        }
        self._subscription_count = 0  # Track to stay under 25 topics

        # Callbacks
        self._trade_callbacks: List[Callable] = []
        self._orderbook_callbacks: List[Callable] = []

    # ============ Symbol Conversion ============

    def to_exchange_symbol(self, unified_symbol: str) -> str:
        """Convert unified symbol to BitMart format: BTC/USDT -> BTCUSDT"""
        return unified_symbol.replace("/", "")

    def to_unified_symbol(self, exchange_symbol: str) -> str:
        """Convert BitMart symbol to unified format: BTCUSDT -> BTC/USDT"""
        # Try common quote currencies
        for quote in ["USDT", "USDC", "USD", "BTC", "ETH"]:
            if exchange_symbol.endswith(quote):
                base = exchange_symbol[:-len(quote)]
                return f"{base}/{quote}"
        return exchange_symbol

    def normalize_symbol(self, raw_symbol: str) -> str:
        """Normalize raw symbol to unified format."""
        return self.to_unified_symbol(raw_symbol)

    async def load_symbols(self) -> List[SymbolInfo]:
        """Load all trading symbols."""
        await self._load_symbols()
        return list(self._symbols.values())

    # ============ Connection Management ============

    async def connect(self) -> None:
        """Connect to BitMart."""
        if self._state == ConnectionState.CONNECTED:
            return

        self._state = ConnectionState.CONNECTING
        logger.info(f"[{self.EXCHANGE_NAME}] Connecting...")

        try:
            # Create session
            if not self._session:
                self._session = aiohttp.ClientSession()

            # Load symbols
            await self._load_symbols()

            # Connect WebSocket
            self._ws = await self._session.ws_connect(
                self._ws_url,
                heartbeat=None,  # BitMart uses custom ping/pong
            )

            # Start message handler
            self._ws_task = asyncio.create_task(self._ws_handler())
            self._ping_task = asyncio.create_task(self._ping_loop())

            self._state = ConnectionState.CONNECTED
            self._reconnect_count = 0
            logger.info(f"[{self.EXCHANGE_NAME}] Connected successfully")

        except Exception as e:
            self._state = ConnectionState.DISCONNECTED
            logger.error(f"[{self.EXCHANGE_NAME}] Connection failed: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from BitMart."""
        logger.info(f"[{self.EXCHANGE_NAME}] Disconnecting...")

        self._state = ConnectionState.DISCONNECTED

        # Cancel tasks
        for task in [self._ws_task, self._ping_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close WebSocket
        if self._ws and not self._ws.closed:
            await self._ws.close()

        # Close session
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

        logger.info(f"[{self.EXCHANGE_NAME}] Disconnected")

    async def _load_symbols(self) -> None:
        """Load available symbols from BitMart."""
        url = f"{self._rest_url}/contract/public/details"

        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to load symbols: {resp.status}")

            result = await resp.json()
            contracts = result.get("data", {}).get("symbols", [])

            for contract in contracts:
                # symbol: BTCUSDT
                exchange_symbol = contract.get("symbol", "")
                unified = self.to_unified_symbol(exchange_symbol)

                base = contract.get("base_currency", "")
                quote = contract.get("quote_currency", "USDT")
                # price_precision in V2 is tick size like "0.1"
                tick_size_str = str(contract.get("price_precision", "0.01"))
                tick_size = Decimal(tick_size_str)
                price_prec = max(0, -tick_size.as_tuple().exponent) if tick_size > 0 else 2
                vol_prec_str = str(contract.get("vol_precision", "1"))
                step_size = Decimal(vol_prec_str)
                qty_prec = max(0, -step_size.as_tuple().exponent) if step_size > 0 else 0
                self._symbols[unified] = SymbolInfo(
                    exchange=self.EXCHANGE_NAME,
                    symbol_unified=unified,
                    symbol_exchange=exchange_symbol,
                    base_asset=base,
                    quote_asset=quote,
                    market_type=MarketType.FUTURES_PERPETUAL,
                    price_precision=price_prec,
                    quantity_precision=qty_prec,
                    min_quantity=Decimal(str(contract.get("min_volume", "1"))),
                    min_notional=Decimal("5"),
                    tick_size=tick_size,
                    step_size=step_size,
                )
                self._symbol_map[exchange_symbol] = unified
                self._reverse_symbol_map[unified] = exchange_symbol

            logger.info(f"[{self.EXCHANGE_NAME}] Loaded {len(self._symbols)} symbols")

    # ============ WebSocket Handler ============

    async def _ws_handler(self) -> None:
        """Handle WebSocket messages."""
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    # BitMart sends gzip-compressed messages
                    try:
                        decompressed = gzip.decompress(msg.data)
                        data = json.loads(decompressed)
                        await self._process_message(data)
                    except Exception as e:
                        logger.error(f"[{self.EXCHANGE_NAME}] Decompress error: {e}")
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    await self._process_message(json.loads(msg.data))
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"[{self.EXCHANGE_NAME}] WS error: {msg.data}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    break

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[{self.EXCHANGE_NAME}] WS handler error: {e}")

        # Reconnect if not intentionally disconnected
        if self._state != ConnectionState.DISCONNECTED:
            asyncio.create_task(self._reconnect())

    async def _ping_loop(self) -> None:
        """Send periodic pings."""
        try:
            while self._state == ConnectionState.CONNECTED:
                await asyncio.sleep(15)
                if self._ws and not self._ws.closed:
                    await self._ws.send_str("ping")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[{self.EXCHANGE_NAME}] Ping error: {e}")

    async def _process_message(self, data: Dict[str, Any]) -> None:
        """Process WebSocket message."""
        # Handle pong
        if data == "pong" or data.get("event") == "pong":
            return

        # Handle subscription response
        event = data.get("event")
        if event == "subscribe":
            logger.debug(f"[{self.EXCHANGE_NAME}] Subscribed: {data.get('channel')}")
            return

        # Handle data
        group = data.get("group", "")
        payload = data.get("data", [])

        if not group or not payload:
            return

        # Trade updates: futures/trade:BTCUSDT
        if "trade" in group:
            symbol = group.split(":")[-1] if ":" in group else ""
            for trade_data in payload:
                trade_data["symbol"] = symbol
                trade = self.normalize_trade(trade_data)
                for callback in self._trade_callbacks:
                    try:
                        callback(trade)
                    except Exception as e:
                        logger.error(f"Trade callback error: {e}")

        # Orderbook updates: futures/depth20:BTCUSDT
        elif "depth" in group:
            symbol = group.split(":")[-1] if ":" in group else ""
            for ob_data in payload:
                ob_data["symbol"] = symbol
                orderbook = self.normalize_orderbook(ob_data)
                for callback in self._orderbook_callbacks:
                    try:
                        callback(orderbook)
                    except Exception as e:
                        logger.error(f"Orderbook callback error: {e}")

    async def _reconnect(self) -> None:
        """Reconnect to WebSocket."""
        if self._state == ConnectionState.DISCONNECTED:
            return

        self._state = ConnectionState.RECONNECTING
        self._reconnect_count += 1

        wait_time = min(30, 2 ** self._reconnect_count)
        logger.info(f"[{self.EXCHANGE_NAME}] Reconnecting in {wait_time}s...")
        await asyncio.sleep(wait_time)

        try:
            # Close old connection
            if self._ws and not self._ws.closed:
                await self._ws.close()

            # Reconnect
            self._ws = await self._session.ws_connect(
                self._ws_url,
                heartbeat=None,
            )

            # Restart handlers
            self._ws_task = asyncio.create_task(self._ws_handler())
            self._ping_task = asyncio.create_task(self._ping_loop())

            # Resubscribe
            self._subscription_count = 0
            await self._resubscribe()

            self._state = ConnectionState.CONNECTED
            logger.info(f"[{self.EXCHANGE_NAME}] Reconnected successfully")

        except Exception as e:
            logger.error(f"[{self.EXCHANGE_NAME}] Reconnection failed: {e}")
            asyncio.create_task(self._reconnect())

    async def _resubscribe(self) -> None:
        """Resubscribe to all channels."""
        if self._subscriptions["trades"]:
            symbols = list(self._subscriptions["trades"])
            self._subscriptions["trades"].clear()
            await self.subscribe_trades(symbols)

        if self._subscriptions["orderbook"]:
            symbols = list(self._subscriptions["orderbook"])
            self._subscriptions["orderbook"].clear()
            await self.subscribe_orderbook(symbols)

    # ============ Subscriptions ============

    async def subscribe_trades(
        self,
        symbols: List[str],
        callback: Optional[Callable[[UnifiedTrade], None]] = None,
    ) -> None:
        """Subscribe to trade updates (max 25 topics total!)."""
        if callback and callback not in self._trade_callbacks:
            self._trade_callbacks.append(callback)

        subscribed = 0
        skipped = 0

        for symbol in symbols:
            if symbol in self._subscriptions["trades"]:
                continue

            # Check subscription limit (25 topics max for BitMart)
            if self._subscription_count >= 25:
                skipped += 1
                continue

            exchange_symbol = self.to_exchange_symbol(symbol)
            msg = {
                "action": "subscribe",
                "args": [f"futures/trade:{exchange_symbol}"],
            }

            await self._ws.send_json(msg)
            self._subscriptions["trades"].add(symbol)
            self._subscription_count += 1
            subscribed += 1
            # Longer delay for strict rate limits
            await asyncio.sleep(0.2)

        # Log summary once
        if subscribed > 0 or skipped > 0:
            logger.info(f"[{self.EXCHANGE_NAME}] Trades: subscribed {subscribed}, skipped {skipped} (limit 25)")

    async def subscribe_orderbook(
        self,
        symbols: List[str],
        callback: Optional[Callable[[UnifiedOrderBook], None]] = None,
        depth: int = 20,
    ) -> None:
        """Subscribe to orderbook updates (max 25 topics total!)."""
        if callback and callback not in self._orderbook_callbacks:
            self._orderbook_callbacks.append(callback)

        subscribed = 0
        skipped = 0

        for symbol in symbols:
            if symbol in self._subscriptions["orderbook"]:
                continue

            # Check subscription limit (25 topics max for BitMart)
            if self._subscription_count >= 25:
                skipped += 1
                continue

            exchange_symbol = self.to_exchange_symbol(symbol)
            # BitMart supports depth5, depth20, depth50
            depth_level = 5 if depth <= 5 else (20 if depth <= 20 else 50)

            msg = {
                "action": "subscribe",
                "args": [f"futures/depth{depth_level}:{exchange_symbol}"],
            }

            await self._ws.send_json(msg)
            self._subscriptions["orderbook"].add(symbol)
            self._subscription_count += 1
            subscribed += 1
            await asyncio.sleep(0.2)

        # Log summary once
        if subscribed > 0 or skipped > 0:
            logger.info(f"[{self.EXCHANGE_NAME}] Orderbook: subscribed {subscribed}, skipped {skipped} (limit 25)")

    async def unsubscribe(self, stream_type: str, symbols: List[str]) -> None:
        """Unsubscribe from streams."""
        if not self._ws or self._ws.closed:
            return

        for symbol in symbols:
            self._subscriptions.get(stream_type, set()).discard(symbol)
            exchange_symbol = self.to_exchange_symbol(symbol)

            if stream_type == "trades":
                arg = f"futures/trade:{exchange_symbol}"
            elif stream_type == "orderbook":
                arg = f"futures/depth20:{exchange_symbol}"
            else:
                continue

            msg = {
                "action": "unsubscribe",
                "args": [arg],
            }
            await self._ws.send_json(msg)
            self._subscription_count = max(0, self._subscription_count - 1)
            logger.info(f"[{self.EXCHANGE_NAME}] Unsubscribed from {stream_type}: {symbol}")

    # ============ REST API ============

    async def get_ticker(self, symbol: str) -> UnifiedTicker:
        """Get ticker for symbol."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/contract/public/details"
        params = {"symbol": exchange_symbol}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get ticker: {resp.status}")

            result = await resp.json()
            data = result.get("data", {}).get("symbols", [{}])[0]

            return UnifiedTicker(
                exchange=self.EXCHANGE_NAME,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                last_price=Decimal(str(data.get("last_price", 0))),
                bid_price=Decimal(str(data.get("best_bid", 0) or 0)),
                ask_price=Decimal(str(data.get("best_ask", 0) or 0)),
                volume_24h=Decimal(str(data.get("volume_24h", 0) or 0)),
                high_24h=Decimal(str(data.get("high_24h", 0) or 0)),
                low_24h=Decimal(str(data.get("low_24h", 0) or 0)),
            )

    async def get_orderbook(self, symbol: str, limit: int = 20) -> UnifiedOrderBook:
        """Get orderbook snapshot."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/contract/public/depth"
        params = {"symbol": exchange_symbol}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get orderbook: {resp.status}")

            result = await resp.json()
            data = result.get("data", {})
            data["symbol"] = exchange_symbol
            return self.normalize_orderbook(data, symbol)

    async def get_funding_rate(self, symbol: str) -> UnifiedFunding:
        """Get current funding rate."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/contract/public/funding-rate"
        params = {"symbol": exchange_symbol}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get funding rate: {resp.status}")

            result = await resp.json()
            data = result.get("data", {})

            funding_rate = Decimal(str(data.get("rate", 0)))
            rate_value = Decimal(str(data.get("rate_value", 0)))

            # Next funding time
            next_funding_ts = data.get("next_funding_time")
            next_funding_time = None
            if next_funding_ts:
                next_funding_time = datetime.fromtimestamp(
                    next_funding_ts / 1000, tz=timezone.utc
                )

            # Get mark price from details
            mark_price = Decimal(0)
            try:
                details_url = f"{self._rest_url}/contract/public/details"
                async with self._session.get(details_url, params={"symbol": exchange_symbol}) as det_resp:
                    if det_resp.status == 200:
                        det_result = await det_resp.json()
                        symbols = det_result.get("data", {}).get("symbols", [{}])
                        if symbols:
                            mark_price = Decimal(str(symbols[0].get("index_price", 0)))
            except Exception:
                pass

            return UnifiedFunding(
                exchange=self.EXCHANGE_NAME,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                rate=funding_rate,
                mark_price=mark_price,
                next_funding_time=next_funding_time,
            )

    async def get_open_interest(self, symbol: str) -> UnifiedOpenInterest:
        """Get open interest."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/contract/public/open-interest"
        params = {"symbol": exchange_symbol}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get open interest: {resp.status}")

            result = await resp.json()
            data = result.get("data", {})

            oi = Decimal(str(data.get("open_interest", 0)))
            oi_value = Decimal(str(data.get("open_interest_value", 0) or 0))

            return UnifiedOpenInterest(
                exchange=self.EXCHANGE_NAME,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                open_interest=oi,
                open_interest_usd=oi_value if oi_value else None,
            )

    # ============ Normalization ============

    def normalize_trade(self, raw: Dict[str, Any]) -> UnifiedTrade:
        """Normalize trade data."""
        exchange_symbol = raw.get("symbol", "")
        symbol = self.to_unified_symbol(exchange_symbol)

        # BitMart: way 1=buy, 2=sell
        way = raw.get("way", 1)
        side = Side.BUY if way == 1 else Side.SELL

        price = Decimal(str(raw.get("deal_price", raw.get("price", 0))))
        quantity = Decimal(str(raw.get("deal_vol", raw.get("vol", 0))))

        ts = raw.get("timestamp", raw.get("created_at", 0))
        try:
            ts = float(ts) if ts else 0
            if ts > 1e12:
                ts = ts / 1000
            dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)
        except (ValueError, TypeError):
            dt = datetime.now(timezone.utc)

        return UnifiedTrade(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=dt,
            price=price,
            quantity=quantity,
            side=side,
            trade_id=str(raw.get("trade_id", "")),
            quote_quantity=price * quantity,
            raw=raw,
        )

    def normalize_orderbook(
        self, raw: Dict[str, Any], symbol: Optional[str] = None
    ) -> UnifiedOrderBook:
        """Normalize orderbook data."""
        if not symbol:
            exchange_symbol = raw.get("symbol", "")
            symbol = self.to_unified_symbol(exchange_symbol)

        bids = []
        asks = []

        for bid in raw.get("bids", []):
            if isinstance(bid, list) and len(bid) >= 2:
                price = Decimal(str(bid[0]))
                size = Decimal(str(bid[1]))
                bids.append(OrderBookLevel(price=price, quantity=size))

        for ask in raw.get("asks", []):
            if isinstance(ask, list) and len(ask) >= 2:
                price = Decimal(str(ask[0]))
                size = Decimal(str(ask[1]))
                asks.append(OrderBookLevel(price=price, quantity=size))

        # Sort
        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)

        ts = raw.get("timestamp", 0)
        try:
            ts = float(ts) if ts else 0
            if ts > 1e12:
                ts = ts / 1000
            dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)
        except (ValueError, TypeError):
            dt = datetime.now(timezone.utc)

        return UnifiedOrderBook(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=dt,
            bids=bids,
            asks=asks,
        )
