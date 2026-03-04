# -*- coding: utf-8 -*-
"""Tests for data models."""

import pytest
from decimal import Decimal
from datetime import datetime

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from screener.models import (
    SymbolStats,
    VulnerableSymbol,
    VulnerabilityLevel,
    SymbolState,
    Trade,
    Detection,
    AlertSeverity,
)


class TestSymbolStats:
    def test_creation(self):
        stats = SymbolStats(
            symbol="TESTUSDT",
            price=Decimal("1.5"),
            volume_24h_usd=Decimal("100000"),
            price_change_24h=Decimal("5.5"),
            trade_count_24h=5000,
            quote_asset="USDT",
        )
        assert stats.symbol == "TESTUSDT"
        assert stats.base_asset == "TEST"
        assert stats.is_usdt_pair is True
        assert stats.is_tradeable is True

    def test_non_usdt_pair(self):
        stats = SymbolStats(
            symbol="ETHBTC",
            price=Decimal("0.05"),
            volume_24h_usd=Decimal("50000"),
            price_change_24h=Decimal("-2.0"),
            trade_count_24h=1000,
            quote_asset="BTC",
        )
        assert stats.is_usdt_pair is False


class TestVulnerableSymbol:
    def test_manipulation_ease_score(self):
        stats = SymbolStats(
            symbol="LOWCAPUSDT",
            price=Decimal("0.001"),
            volume_24h_usd=Decimal("30000"),  # Very low
            price_change_24h=Decimal("0"),
            trade_count_24h=500,
            quote_asset="USDT",
        )

        vulnerable = VulnerableSymbol(
            symbol="LOWCAPUSDT",
            stats=stats,
            vulnerability_level=VulnerabilityLevel.CRITICAL,
            vulnerability_reasons=["ULTRA_THIN_BOOK"],
            order_book_depth_usd=Decimal("2000"),  # Very thin
            spread_percent=Decimal("1.5"),  # Wide
        )

        score = vulnerable.manipulation_ease_score
        assert score > 70  # Should be high


class TestSymbolState:
    def test_creation(self):
        state = SymbolState(symbol="TESTUSDT")
        assert state.symbol == "TESTUSDT"
        assert state.last_price == Decimal("0")

    def test_price_change_calculation(self):
        state = SymbolState(symbol="TESTUSDT")
        state.price_1m_ago = Decimal("100")
        state.last_price = Decimal("110")

        assert state.price_change_1m_pct == Decimal("10")

    def test_book_imbalance(self):
        state = SymbolState(symbol="TESTUSDT")
        state.bid_volume_20 = Decimal("100000")
        state.ask_volume_20 = Decimal("20000")

        # (100000 - 20000) / 120000 = 0.666...
        imbalance = state.book_imbalance
        assert imbalance > Decimal("0.6")

    def test_volume_spike_ratio(self):
        state = SymbolState(symbol="TESTUSDT")
        state.volume_5m = Decimal("50000")
        state.avg_volume_1h = Decimal("60000")  # 5000 per 5min on average

        ratio = state.volume_spike_ratio
        assert ratio == Decimal("10")  # 50000 / 5000 = 10x


class TestTrade:
    def test_trade_side(self):
        buy_trade = Trade(
            price=Decimal("100"),
            qty=Decimal("1"),
            time=1000000,
            is_buyer_maker=False,  # Taker buy
        )
        assert buy_trade.side == "BUY"

        sell_trade = Trade(
            price=Decimal("100"),
            qty=Decimal("1"),
            time=1000000,
            is_buyer_maker=True,  # Taker sell
        )
        assert sell_trade.side == "SELL"


class TestDetection:
    def test_to_alert_payload(self):
        detection = Detection(
            symbol="TESTUSDT",
            timestamp=datetime(2025, 2, 16, 12, 0, 0),
            severity=AlertSeverity.CRITICAL,
            detection_type="ACTIVE_PUMP",
            score=95,
            details={"volume_spike": Decimal("50.5")},
            evidence=["Volume 50x normal"],
        )

        payload = detection.to_alert_payload()
        assert payload["symbol"] == "TESTUSDT"
        assert payload["severity"] == "CRITICAL"
        assert payload["type"] == "ACTIVE_PUMP"
        assert payload["score"] == 95
        assert payload["details"]["volume_spike"] == "50.5"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
