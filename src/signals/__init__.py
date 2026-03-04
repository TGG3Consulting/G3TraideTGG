# -*- coding: utf-8 -*-
"""
Trading Signals Module.

Генерирует торговые сигналы на основе детекций манипуляций.
"""

from .models import TradeSignal, SignalDirection, SignalConfidence, AccumulationScore
from .signal_generator import SignalGenerator
from .accumulation_detector import AccumulationDetector

__all__ = [
    "TradeSignal",
    "SignalDirection",
    "SignalConfidence",
    "AccumulationScore",
    "SignalGenerator",
    "AccumulationDetector",
]
