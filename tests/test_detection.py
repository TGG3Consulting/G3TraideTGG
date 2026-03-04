# -*- coding: utf-8 -*-
"""Tests for detection engine."""

import pytest
from decimal import Decimal
from datetime import datetime

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from screener.models import SymbolState, Trade, AlertSeverity
from screener.detection_engine import DetectionEngine


class TestDetectionEngine:
    @pytest.fixture
    def engine(self):
        return DetectionEngine()

    @pytest.fixture
    def base_state(self):
        state = SymbolState(symbol="TESTUSDT")
        state.last_price = Decimal("1.0")
        state.avg_volume_1h = Decimal("60000")  # 5000 per 5min
        return state

    def test_no_detection_on_normal_state(self, engine, base_state):
        """Нормальное состояние не должно вызывать детекции."""
        base_state.volume_5m = Decimal("5000")  # Normal
        base_state.price_1m_ago = Decimal("1.0")
        base_state.price_5m_ago = Decimal("0.99")  # 1% change

        detections = engine.analyze(base_state)
        assert len(detections) == 0

    def test_volume_spike_detection(self, engine, base_state):
        """Детекция volume spike."""
        base_state.volume_5m = Decimal("150000")  # 30x normal (above ALERT threshold)

        detections = engine.analyze(base_state)

        volume_detections = [d for d in detections if "VOLUME" in d.detection_type]
        assert len(volume_detections) > 0
        assert volume_detections[0].severity in [AlertSeverity.WARNING, AlertSeverity.ALERT, AlertSeverity.CRITICAL]

    def test_extreme_volume_spike(self, engine, base_state):
        """Критический volume spike."""
        base_state.volume_5m = Decimal("300000")  # 60x normal

        detections = engine.analyze(base_state)

        critical = [d for d in detections if d.severity == AlertSeverity.CRITICAL]
        assert len(critical) > 0

    def test_price_velocity_detection(self, engine, base_state):
        """Детекция быстрого движения цены."""
        base_state.price_5m_ago = Decimal("0.7")  # 30% down from 1.0
        base_state.last_price = Decimal("1.0")

        detections = engine.analyze(base_state)

        price_detections = [d for d in detections if "PRICE" in d.detection_type]
        assert len(price_detections) > 0

    def test_orderbook_imbalance_detection(self, engine, base_state):
        """Детекция перекоса стакана."""
        base_state.bid_volume_20 = Decimal("100000")
        base_state.ask_volume_20 = Decimal("10000")  # 90% bids

        detections = engine.analyze(base_state)

        imbalance_detections = [d for d in detections if "IMBALANCE" in d.detection_type]
        assert len(imbalance_detections) > 0

    def test_wash_trading_detection(self, engine, base_state):
        """Детекция wash trading."""
        # Много трейдов одинакового размера
        for _ in range(30):
            base_state.trades_5m.append(Trade(
                price=Decimal("1.0"),
                qty=Decimal("100.0"),  # Same qty
                time=1000000,
                is_buyer_maker=False,
            ))

        detections = engine.analyze(base_state)

        wash_detections = [d for d in detections if "WASH" in d.detection_type]
        assert len(wash_detections) > 0

    def test_coordinated_buying_detection(self, engine, base_state):
        """Детекция координированной покупки."""
        # 95% buys
        for i in range(100):
            base_state.trades_5m.append(Trade(
                price=Decimal("1.0"),
                qty=Decimal(str(i + 1)),  # Different sizes
                time=1000000 + i,
                is_buyer_maker=i >= 95,  # Only 5 sells
            ))

        detections = engine.analyze(base_state)

        coord_detections = [d for d in detections if "COORDINATED" in d.detection_type or "ONE_SIDED" in d.detection_type]
        assert len(coord_detections) > 0

    def test_active_pump_detection(self, engine, base_state):
        """Детекция активного pump."""
        # Все факторы вместе
        base_state.volume_5m = Decimal("60000")  # 12x
        base_state.price_5m_ago = Decimal("0.8")  # +25%
        base_state.bid_volume_20 = Decimal("80000")
        base_state.ask_volume_20 = Decimal("20000")  # 60% imbalance

        detections = engine.analyze(base_state)

        pump_detections = [d for d in detections if "PUMP" in d.detection_type]
        assert len(pump_detections) > 0
        assert pump_detections[0].severity == AlertSeverity.CRITICAL

    def test_deduplication(self, engine, base_state):
        """Проверка дедупликации алертов."""
        base_state.volume_5m = Decimal("100000")  # Spike

        # Первый анализ
        detections1 = engine.analyze(base_state)
        assert len(detections1) > 0

        # Второй анализ сразу же
        detections2 = engine.analyze(base_state)

        # Должны быть отфильтрованы как дубликаты
        assert len(detections2) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
