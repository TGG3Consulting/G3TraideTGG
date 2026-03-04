# -*- coding: utf-8 -*-
"""
Rate Limiter for Exchange API Requests.

Implements token bucket algorithm with per-exchange configuration.
Supports both REST and WebSocket rate limiting.

Features:
- Per-endpoint rate limiting
- Sliding window with token bucket
- Async-safe (uses asyncio.Lock)
- Automatic backoff on 429 responses
- Weight-based limiting (some endpoints cost more)

Usage:
    limiter = RateLimiter(RateLimitConfig(
        requests_per_second=10,
        requests_per_minute=1200,
    ))

    async with limiter.acquire():
        response = await client.get(url)

    # With weight
    async with limiter.acquire(weight=5):
        response = await client.get(heavy_endpoint)
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional

import structlog

logger = structlog.get_logger(__name__)


# =============================================================================
# EXCEPTIONS
# =============================================================================

class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded and no retry is possible."""

    def __init__(
        self,
        message: str,
        retry_after: Optional[float] = None,
        exchange: Optional[str] = None
    ):
        super().__init__(message)
        self.retry_after = retry_after
        self.exchange = exchange


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class RateLimitConfig:
    """
    Rate limit configuration for an exchange.

    Different exchanges have different limits:
    - Binance: 1200 req/min, 10 req/sec
    - Bybit: 120 req/min, 50 req/sec
    - OKX: 60 req/2sec
    """
    # Requests per time window
    requests_per_second: int = 10
    requests_per_minute: int = 600

    # Weight-based limits (some endpoints cost more)
    weight_per_minute: int = 1200

    # WebSocket limits
    ws_messages_per_second: int = 5
    ws_connections_max: int = 5
    ws_subscriptions_per_connection: int = 200

    # Behavior
    max_wait_seconds: float = 30.0  # Max time to wait for available slot
    burst_allowance: float = 1.5    # Allow brief bursts (1.5x normal rate)

    # Backoff on 429
    backoff_initial_seconds: float = 1.0
    backoff_max_seconds: float = 60.0
    backoff_factor: float = 2.0


# =============================================================================
# PRESET CONFIGURATIONS
# =============================================================================

class ExchangeRateLimits:
    """Pre-configured rate limits for known exchanges."""

    BINANCE = RateLimitConfig(
        requests_per_second=10,
        requests_per_minute=1200,
        weight_per_minute=6000,
        ws_messages_per_second=5,
        ws_connections_max=5,
        ws_subscriptions_per_connection=200,
    )

    BINANCE_FUTURES = RateLimitConfig(
        requests_per_second=10,
        requests_per_minute=2400,
        weight_per_minute=2400,
        ws_messages_per_second=10,
        ws_connections_max=10,
        ws_subscriptions_per_connection=200,
    )

    BYBIT = RateLimitConfig(
        requests_per_second=50,
        requests_per_minute=120,  # More restrictive per minute
        weight_per_minute=1000,
        ws_messages_per_second=20,
        ws_connections_max=5,
        ws_subscriptions_per_connection=500,
    )

    OKX = RateLimitConfig(
        requests_per_second=10,  # 20 per 2 seconds
        requests_per_minute=300,
        weight_per_minute=600,
        ws_messages_per_second=10,
        ws_connections_max=3,
        ws_subscriptions_per_connection=240,
    )

    BITGET = RateLimitConfig(
        requests_per_second=10,
        requests_per_minute=600,
        weight_per_minute=1200,
        ws_messages_per_second=10,
        ws_connections_max=30,
        ws_subscriptions_per_connection=240,
    )

    GATE = RateLimitConfig(
        requests_per_second=10,
        requests_per_minute=300,
        weight_per_minute=300,
        ws_messages_per_second=5,
        ws_connections_max=10,
        ws_subscriptions_per_connection=50,
    )

    MEXC = RateLimitConfig(
        requests_per_second=20,
        requests_per_minute=1200,
        weight_per_minute=2400,
        ws_messages_per_second=10,
        ws_connections_max=5,
        ws_subscriptions_per_connection=30,
    )

    KUCOIN = RateLimitConfig(
        requests_per_second=10,
        requests_per_minute=180,  # 3 per second sustained
        weight_per_minute=180,
        ws_messages_per_second=10,
        ws_connections_max=10,
        ws_subscriptions_per_connection=300,
    )

    BINGX = RateLimitConfig(
        requests_per_second=10,
        requests_per_minute=300,
        weight_per_minute=600,
        ws_messages_per_second=5,
        ws_connections_max=5,
        ws_subscriptions_per_connection=200,
    )

    HTX = RateLimitConfig(
        requests_per_second=10,
        requests_per_minute=100,
        weight_per_minute=200,
        ws_messages_per_second=10,
        ws_connections_max=10,
        ws_subscriptions_per_connection=100,
    )

    BITMART = RateLimitConfig(
        requests_per_second=5,
        requests_per_minute=300,
        weight_per_minute=600,
        ws_messages_per_second=5,
        ws_connections_max=5,
        ws_subscriptions_per_connection=100,
    )

    # DEX have different considerations
    HYPERLIQUID = RateLimitConfig(
        requests_per_second=5,
        requests_per_minute=120,
        weight_per_minute=120,
        ws_messages_per_second=10,
        ws_connections_max=1,
        ws_subscriptions_per_connection=1000,
    )

    ASTERDEX = RateLimitConfig(
        requests_per_second=5,
        requests_per_minute=100,
        weight_per_minute=100,
        ws_messages_per_second=5,
        ws_connections_max=1,
        ws_subscriptions_per_connection=100,
    )

    LIGHTER = RateLimitConfig(
        requests_per_second=5,
        requests_per_minute=100,
        weight_per_minute=100,
        ws_messages_per_second=5,
        ws_connections_max=1,
        ws_subscriptions_per_connection=100,
    )

    @classmethod
    def get(cls, exchange: str) -> RateLimitConfig:
        """Get rate limit config for exchange."""
        configs = {
            "binance": cls.BINANCE,
            "binance_futures": cls.BINANCE_FUTURES,
            "bybit": cls.BYBIT,
            "okx": cls.OKX,
            "bitget": cls.BITGET,
            "gate": cls.GATE,
            "mexc": cls.MEXC,
            "kucoin": cls.KUCOIN,
            "bingx": cls.BINGX,
            "htx": cls.HTX,
            "bitmart": cls.BITMART,
            "hyperliquid": cls.HYPERLIQUID,
            "asterdex": cls.ASTERDEX,
            "lighter": cls.LIGHTER,
        }
        return configs.get(exchange.lower(), cls.BINANCE)


# =============================================================================
# TOKEN BUCKET RATE LIMITER
# =============================================================================

class TokenBucket:
    """
    Token bucket implementation for rate limiting.

    Tokens are added at a fixed rate. Each request consumes tokens.
    If no tokens available, request waits.
    """

    def __init__(
        self,
        rate: float,          # Tokens per second
        capacity: float,      # Maximum tokens (burst capacity)
    ):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0, timeout: float = 30.0) -> bool:
        """
        Acquire tokens from bucket.

        Args:
            tokens: Number of tokens to acquire
            timeout: Maximum time to wait

        Returns:
            True if acquired, raises RateLimitExceeded if timeout
        """
        async with self._lock:
            start_time = time.monotonic()

            while True:
                # Refill tokens based on elapsed time
                now = time.monotonic()
                elapsed = now - self.last_update
                self.tokens = min(
                    self.capacity,
                    self.tokens + elapsed * self.rate
                )
                self.last_update = now

                # Check if we have enough tokens
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return True

                # Calculate wait time
                wait_time = (tokens - self.tokens) / self.rate

                # Check timeout
                if now - start_time + wait_time > timeout:
                    raise RateLimitExceeded(
                        f"Rate limit timeout after {timeout}s",
                        retry_after=wait_time
                    )

                # Wait for tokens to refill
                await asyncio.sleep(min(wait_time, 0.1))

    def available(self) -> float:
        """Get current available tokens."""
        now = time.monotonic()
        elapsed = now - self.last_update
        return min(self.capacity, self.tokens + elapsed * self.rate)


# =============================================================================
# SLIDING WINDOW COUNTER
# =============================================================================

class SlidingWindowCounter:
    """
    Sliding window counter for rate limiting.

    More accurate than fixed windows, less bursty than token bucket.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float
    ):
        self.limit = limit
        self.window_seconds = window_seconds
        self.requests: deque = deque()
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        weight: int = 1,
        timeout: float = 30.0
    ) -> bool:
        """
        Acquire slot in window.

        Args:
            weight: Request weight (some endpoints cost more)
            timeout: Maximum time to wait

        Returns:
            True if acquired
        """
        async with self._lock:
            start_time = time.monotonic()

            while True:
                now = time.monotonic()

                # Remove old requests outside window
                cutoff = now - self.window_seconds
                while self.requests and self.requests[0][0] < cutoff:
                    self.requests.popleft()

                # Calculate current weight
                current_weight = sum(w for _, w in self.requests)

                # Check if we can add request
                if current_weight + weight <= self.limit:
                    self.requests.append((now, weight))
                    return True

                # Calculate wait time
                if self.requests:
                    oldest_time = self.requests[0][0]
                    wait_time = oldest_time + self.window_seconds - now
                else:
                    wait_time = 0.1

                # Check timeout
                if now - start_time + wait_time > timeout:
                    raise RateLimitExceeded(
                        f"Rate limit timeout after {timeout}s (window full)",
                        retry_after=wait_time
                    )

                await asyncio.sleep(min(wait_time, 0.1))

    def current_count(self) -> int:
        """Get current request count in window."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        return sum(
            w for t, w in self.requests
            if t >= cutoff
        )


# =============================================================================
# MAIN RATE LIMITER
# =============================================================================

class RateLimiter:
    """
    Combined rate limiter using both token bucket and sliding window.

    Provides both burst handling (token bucket) and sustained rate
    limiting (sliding window).
    """

    def __init__(
        self,
        config: RateLimitConfig,
        name: str = "default"
    ):
        self.config = config
        self.name = name
        self.logger = logger.bind(rate_limiter=name)

        # Token bucket for short-term bursts
        self._second_bucket = TokenBucket(
            rate=config.requests_per_second,
            capacity=config.requests_per_second * config.burst_allowance
        )

        # Sliding window for minute-level limiting
        self._minute_window = SlidingWindowCounter(
            limit=config.requests_per_minute,
            window_seconds=60.0
        )

        # Weight-based limiting
        self._weight_window = SlidingWindowCounter(
            limit=config.weight_per_minute,
            window_seconds=60.0
        )

        # Backoff state
        self._backoff_until: Optional[float] = None
        self._consecutive_429s = 0

        # Stats
        self._total_requests = 0
        self._total_waits = 0
        self._total_rate_limited = 0

    async def acquire(self, weight: int = 1) -> None:
        """
        Acquire permission to make a request.

        Args:
            weight: Request weight (default 1)

        Raises:
            RateLimitExceeded: If rate limit exceeded and timeout
        """
        # Check if in backoff
        if self._backoff_until:
            now = time.monotonic()
            if now < self._backoff_until:
                wait_time = self._backoff_until - now
                self.logger.debug(
                    "backoff_wait",
                    wait_time=wait_time
                )
                await asyncio.sleep(wait_time)

        try:
            # Acquire from all limiters
            await self._second_bucket.acquire(
                tokens=1,
                timeout=self.config.max_wait_seconds
            )
            await self._minute_window.acquire(
                weight=1,
                timeout=self.config.max_wait_seconds
            )

            if weight > 1:
                await self._weight_window.acquire(
                    weight=weight,
                    timeout=self.config.max_wait_seconds
                )

            self._total_requests += 1
            self._consecutive_429s = 0

        except RateLimitExceeded:
            self._total_rate_limited += 1
            raise

    async def __aenter__(self):
        """Context manager entry."""
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        pass

    def on_rate_limit_response(self, retry_after: Optional[float] = None):
        """
        Handle 429 response from server.

        Activates exponential backoff.
        """
        self._consecutive_429s += 1
        self._total_rate_limited += 1

        if retry_after:
            backoff_time = retry_after
        else:
            backoff_time = min(
                self.config.backoff_initial_seconds * (
                    self.config.backoff_factor ** self._consecutive_429s
                ),
                self.config.backoff_max_seconds
            )

        self._backoff_until = time.monotonic() + backoff_time

        self.logger.warning(
            "rate_limit_429",
            consecutive=self._consecutive_429s,
            backoff_seconds=backoff_time
        )

    def reset_backoff(self):
        """Reset backoff state after successful request."""
        self._backoff_until = None
        self._consecutive_429s = 0

    @property
    def stats(self) -> dict:
        """Get rate limiter statistics."""
        return {
            "total_requests": self._total_requests,
            "total_waits": self._total_waits,
            "total_rate_limited": self._total_rate_limited,
            "current_second_tokens": self._second_bucket.available(),
            "current_minute_count": self._minute_window.current_count(),
            "in_backoff": self._backoff_until is not None,
        }


# =============================================================================
# WEBSOCKET RATE LIMITER
# =============================================================================

class WebSocketRateLimiter:
    """
    Rate limiter for WebSocket operations.

    Limits:
    - Messages per second (subscriptions, pings)
    - Total subscriptions per connection
    - Total connections
    """

    def __init__(
        self,
        config: RateLimitConfig,
        name: str = "ws"
    ):
        self.config = config
        self.name = name

        # Message rate limiting
        self._message_bucket = TokenBucket(
            rate=config.ws_messages_per_second,
            capacity=config.ws_messages_per_second * 2  # Allow short burst
        )

        # Subscription tracking
        self._subscriptions: Dict[int, int] = {}  # connection_id -> count
        self._total_subscriptions = 0

        self._lock = asyncio.Lock()

    async def acquire_message(self) -> None:
        """Acquire permission to send a WebSocket message."""
        await self._message_bucket.acquire()

    async def acquire_subscription(
        self,
        connection_id: int,
        count: int = 1
    ) -> bool:
        """
        Acquire permission to add subscriptions.

        Args:
            connection_id: WebSocket connection identifier
            count: Number of subscriptions to add

        Returns:
            True if allowed, False if would exceed limit
        """
        async with self._lock:
            current = self._subscriptions.get(connection_id, 0)

            # Check per-connection limit
            if current + count > self.config.ws_subscriptions_per_connection:
                return False

            # Add subscriptions
            self._subscriptions[connection_id] = current + count
            self._total_subscriptions += count
            return True

    async def release_subscription(
        self,
        connection_id: int,
        count: int = 1
    ) -> None:
        """Release subscriptions when unsubscribing."""
        async with self._lock:
            current = self._subscriptions.get(connection_id, 0)
            new_count = max(0, current - count)

            if new_count == 0:
                self._subscriptions.pop(connection_id, None)
            else:
                self._subscriptions[connection_id] = new_count

            self._total_subscriptions = max(0, self._total_subscriptions - count)

    def connection_count(self) -> int:
        """Get current number of connections."""
        return len(self._subscriptions)

    def can_add_connection(self) -> bool:
        """Check if a new connection can be added."""
        return self.connection_count() < self.config.ws_connections_max

    def subscriptions_for_connection(self, connection_id: int) -> int:
        """Get subscription count for a connection."""
        return self._subscriptions.get(connection_id, 0)


# =============================================================================
# RATE LIMITER FACTORY
# =============================================================================

class RateLimiterFactory:
    """
    Factory for creating rate limiters per exchange.

    Caches rate limiters to ensure one per exchange.
    """

    _instances: Dict[str, RateLimiter] = {}
    _ws_instances: Dict[str, WebSocketRateLimiter] = {}
    _lock = asyncio.Lock()

    @classmethod
    async def get_rest_limiter(cls, exchange: str) -> RateLimiter:
        """Get or create REST rate limiter for exchange."""
        async with cls._lock:
            if exchange not in cls._instances:
                config = ExchangeRateLimits.get(exchange)
                cls._instances[exchange] = RateLimiter(config, name=exchange)
            return cls._instances[exchange]

    @classmethod
    async def get_ws_limiter(cls, exchange: str) -> WebSocketRateLimiter:
        """Get or create WebSocket rate limiter for exchange."""
        async with cls._lock:
            if exchange not in cls._ws_instances:
                config = ExchangeRateLimits.get(exchange)
                cls._ws_instances[exchange] = WebSocketRateLimiter(
                    config,
                    name=f"{exchange}_ws"
                )
            return cls._ws_instances[exchange]

    @classmethod
    def get_all_stats(cls) -> Dict[str, dict]:
        """Get stats for all rate limiters."""
        return {
            name: limiter.stats
            for name, limiter in cls._instances.items()
        }
