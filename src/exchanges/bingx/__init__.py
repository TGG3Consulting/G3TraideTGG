# -*- coding: utf-8 -*-
"""
BingX exchange connector.

Usage:
    from src.exchanges.bingx import BingXConnector

    connector = BingXConnector()
    await connector.connect()
    await connector.subscribe_trades(["BTC/USDT"])
    funding = await connector.get_funding_rate("BTC/USDT")
"""

from src.exchanges.bingx.connector import BingXConnector

__all__ = ["BingXConnector"]
