# -*- coding: utf-8 -*-
"""
BitMart exchange connector.

Usage:
    from src.exchanges.bitmart import BitMartConnector

    connector = BitMartConnector()
    await connector.connect()
    await connector.subscribe_trades(["BTC/USDT"])
    funding = await connector.get_funding_rate("BTC/USDT")

WARNING: BitMart has VERY strict rate limits (150/5s, max 25 WS topics).
"""

from src.exchanges.bitmart.connector import BitMartConnector

__all__ = ["BitMartConnector"]
