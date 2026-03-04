# -*- coding: utf-8 -*-
"""
Gate.io exchange connector.

Usage:
    from src.exchanges.gate import GateConnector

    connector = GateConnector()
    await connector.connect()
    await connector.subscribe_trades(["BTC/USDT"])
    funding = await connector.get_funding_rate("BTC/USDT")
"""

from src.exchanges.gate.connector import GateConnector

__all__ = ["GateConnector"]
