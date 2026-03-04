# -*- coding: utf-8 -*-
"""
Bitget exchange connector.

Usage:
    from src.exchanges.bitget import BitgetConnector

    connector = BitgetConnector()
    await connector.connect()
    await connector.subscribe_trades(["BTC/USDT"])
    funding = await connector.get_funding_rate("BTC/USDT")
"""

from src.exchanges.bitget.connector import BitgetConnector

__all__ = ["BitgetConnector"]
