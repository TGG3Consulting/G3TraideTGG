# -*- coding: utf-8 -*-
"""
Bybit exchange connector.

Usage:
    from src.exchanges.bybit import BybitConnector

    connector = BybitConnector()
    await connector.connect()
    await connector.subscribe_trades(["BTC/USDT"])
    funding = await connector.get_funding_rate("BTC/USDT")
"""

from src.exchanges.bybit.connector import BybitConnector

__all__ = ["BybitConnector"]
