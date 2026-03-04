# -*- coding: utf-8 -*-
"""
KuCoin exchange connector.

Usage:
    from src.exchanges.kucoin import KuCoinConnector

    connector = KuCoinConnector()
    await connector.connect()
    await connector.subscribe_trades(["BTC/USDT"])
    funding = await connector.get_funding_rate("BTC/USDT")
"""

from src.exchanges.kucoin.connector import KuCoinConnector

__all__ = ["KuCoinConnector"]
