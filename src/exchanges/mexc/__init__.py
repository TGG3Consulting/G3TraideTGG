# -*- coding: utf-8 -*-
"""
MEXC exchange connector.

Usage:
    from src.exchanges.mexc import MEXCConnector

    connector = MEXCConnector()
    await connector.connect()
    await connector.subscribe_trades(["BTC/USDT"])
    funding = await connector.get_funding_rate("BTC/USDT")

WARNING: MEXC has strict rate limits (20/s). Use with caution.
"""

from src.exchanges.mexc.connector import MEXCConnector

__all__ = ["MEXCConnector"]
