# -*- coding: utf-8 -*-
"""
Cross-Exchange Module for BinanceFriend.

Unified interface for multiple exchanges:
- CEX: Binance, Bybit, OKX, Bitget, Gate, MEXC, KuCoin, BingX, HTX, BitMart
- DEX: Hyperliquid, AsterDEX, Lighter

Usage:
    from src.exchanges import ExchangeRegistry, UnifiedTrade, UnifiedOrderBook

    registry = ExchangeRegistry()
    binance = await registry.get_exchange("binance")
    await binance.connect()
"""

from src.exchanges.base import (
    BaseExchange,
    ExchangeType,
    ConnectionState,
    ExchangeCapability,
)
from src.exchanges.models import (
    UnifiedTrade,
    UnifiedOrderBook,
    UnifiedTicker,
    UnifiedFunding,
    UnifiedOpenInterest,
    UnifiedKline,
    UnifiedLiquidation,
)
from src.exchanges.rate_limiter import (
    RateLimiter,
    RateLimitConfig,
    RateLimitExceeded,
)
from src.exchanges.manager import ExchangeManager

__all__ = [
    # Base
    "BaseExchange",
    "ExchangeType",
    "ConnectionState",
    "ExchangeCapability",
    # Models
    "UnifiedTrade",
    "UnifiedOrderBook",
    "UnifiedTicker",
    "UnifiedFunding",
    "UnifiedOpenInterest",
    "UnifiedKline",
    "UnifiedLiquidation",
    # Rate Limiter
    "RateLimiter",
    "RateLimitConfig",
    "RateLimitExceeded",
    # Manager
    "ExchangeManager",
]
