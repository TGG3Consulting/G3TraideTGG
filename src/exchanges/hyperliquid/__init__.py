# -*- coding: utf-8 -*-
"""
Hyperliquid DEX connector.

Usage:
    from src.exchanges.hyperliquid import HyperliquidConnector

    connector = HyperliquidConnector()
    await connector.connect()
    await connector.subscribe_trades(["BTC/USDT"])
    funding = await connector.get_funding_rate("BTC/USDT")

Note: Hyperliquid is a DEX with on-chain transparency.
"""

from src.exchanges.hyperliquid.connector import HyperliquidConnector

__all__ = ["HyperliquidConnector"]
