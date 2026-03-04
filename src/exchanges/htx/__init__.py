# -*- coding: utf-8 -*-
"""
HTX (ex-Huobi) exchange connector.

Usage:
    from src.exchanges.htx import HTXConnector

    connector = HTXConnector()
    await connector.connect()
    await connector.subscribe_trades(["BTC/USDT"])
    funding = await connector.get_funding_rate("BTC/USDT")
"""

from src.exchanges.htx.connector import HTXConnector

__all__ = ["HTXConnector"]
