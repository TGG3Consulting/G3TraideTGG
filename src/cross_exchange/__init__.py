# -*- coding: utf-8 -*-
"""
Cross-Exchange Detection Module.

Aggregates data from multiple exchanges and detects cross-exchange
manipulation patterns:
- Price divergence / arbitrage
- Funding rate arbitrage
- OI divergence
- Liquidation cascade hunting
- Volume correlation (wash trading)
- Front-running patterns

Usage:
    from src.cross_exchange import StateStore, Correlator, CrossExchangeDetector

    store = StateStore()
    correlator = Correlator(store)
    detector = CrossExchangeDetector(correlator)

    # Feed data from exchanges
    store.update_price("binance", "BTC/USDT", price, volume)
    store.update_funding("bybit", "BTC/USDT", funding_rate)

    # Detect patterns
    alerts = detector.check_all("BTC/USDT")
"""

from src.cross_exchange.state_store import (
    StateStore,
    ExchangeSnapshot,
    SymbolSnapshot,
)
from src.cross_exchange.correlator import (
    Correlator,
    CorrelationResult,
)
from src.cross_exchange.models import (
    CrossExchangeConfig,
    CrossExchangeAlert,
    CrossExchangeSummary,
    ArbitrageOpportunity,
    FundingArbitrageSignal,
    OIDivergenceSignal,
    PriceState,
    OrderBookState,
    OIState,
    FundingState,
    VolumeState,
    AlertSeverity,
    PatternType,
)

__all__ = [
    # State Store
    "StateStore",
    "ExchangeSnapshot",
    "SymbolSnapshot",
    # Correlator
    "Correlator",
    "CorrelationResult",
    # Configuration
    "CrossExchangeConfig",
    # Alerts and Signals
    "CrossExchangeAlert",
    "CrossExchangeSummary",
    "ArbitrageOpportunity",
    "FundingArbitrageSignal",
    "OIDivergenceSignal",
    # State Classes
    "PriceState",
    "OrderBookState",
    "OIState",
    "FundingState",
    "VolumeState",
    # Enums
    "AlertSeverity",
    "PatternType",
]
