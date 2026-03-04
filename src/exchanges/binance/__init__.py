# -*- coding: utf-8 -*-
"""
Binance exchange connectors.

Provides connectors for:
- Binance Spot market
- Binance USDT-M Perpetual Futures

Usage:
    from src.exchanges.binance import BinanceSpotConnector, BinanceFuturesConnector

    # Spot
    spot = BinanceSpotConnector()
    await spot.connect()
    await spot.subscribe_trades(["BTC/USDT"])

    # Futures
    futures = BinanceFuturesConnector()
    await futures.connect()
    funding = await futures.get_funding_rate("BTC/USDT")
"""

from src.exchanges.binance.spot import BinanceSpotConnector
from src.exchanges.binance.futures import BinanceFuturesConnector

__all__ = [
    "BinanceSpotConnector",
    "BinanceFuturesConnector",
]
