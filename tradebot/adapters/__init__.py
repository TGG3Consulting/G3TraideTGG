# -*- coding: utf-8 -*-
"""
Trade Bot Adapters - Реализации интерфейса биржи.

Каждый адаптер реализует ExchangeInterface из core/interfaces.py.
"""

from .binance import BinanceFuturesAdapter

__all__ = ["BinanceFuturesAdapter"]
