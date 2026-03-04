# -*- coding: utf-8 -*-
"""
OKX exchange connector.

Usage:
    from src.exchanges.okx import OKXConnector

    connector = OKXConnector()
    await connector.connect()
    await connector.subscribe_trades(["BTC/USDT"])
    funding = await connector.get_funding_rate("BTC/USDT")
"""

from src.exchanges.okx.connector import OKXConnector

__all__ = ["OKXConnector"]
