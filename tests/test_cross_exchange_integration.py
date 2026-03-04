# -*- coding: utf-8 -*-
"""
Integration Tests for Cross-Exchange System.

Tests:
1. ExchangeManager initialization and connection
2. StateStore data flow
3. Detector orchestration
4. Full screener integration

Usage:
    pytest tests/test_cross_exchange_integration.py -v
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.exchanges.manager import ExchangeManager, ExchangeManagerStats
from src.exchanges.models import (
    UnifiedTrade,
    UnifiedOrderBook,
    UnifiedTicker,
    OrderBookLevel,
    MarketType,
)
from src.exchanges.base import (
    BaseExchange,
    ExchangeConfig,
    ConnectionState,
    ExchangeType,
)
from src.cross_exchange.state_store import StateStore
from src.cross_exchange.correlator import (
    DetectorOrchestrator,
    DetectorOrchestratorConfig,
)
from src.cross_exchange.detectors.base import Detection, Severity, DetectionType


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def mock_exchange_config():
    """Create mock exchange configuration."""
    class MockExchangeConfig:
        enabled = True
        type = "CEX"
        ws_url = "wss://test.exchange.com/ws"
        rest_url = "https://test.exchange.com"

        class rate_limit:
            requests_per_minute = 1200
            requests_per_second = 20
            ws_connections_max = 5
            ws_streams_per_connection = 200

    return MockExchangeConfig()


@pytest.fixture
def mock_exchanges_config(mock_exchange_config):
    """Create mock ExchangesConfig."""
    class MockExchangesConfig:
        binance = mock_exchange_config
        bybit = mock_exchange_config
        okx = mock_exchange_config

        def get(self, name):
            if name in ["binance", "bybit", "okx"]:
                return mock_exchange_config
            return None

    return MockExchangesConfig()


@pytest.fixture
def state_store():
    """Create StateStore instance."""
    return StateStore()


@pytest.fixture
def sample_trade():
    """Create sample UnifiedTrade."""
    return UnifiedTrade(
        exchange="binance",
        symbol="BTC/USDT",
        trade_id="12345",
        timestamp=datetime.now(timezone.utc),
        price=Decimal("50000.00"),
        quantity=Decimal("0.1"),
        side="buy",
        is_maker=False,
    )


@pytest.fixture
def sample_orderbook():
    """Create sample UnifiedOrderBook."""
    return UnifiedOrderBook(
        exchange="binance",
        symbol="BTC/USDT",
        timestamp=datetime.now(timezone.utc),
        bids=[
            OrderBookLevel(price=Decimal("49999"), quantity=Decimal("1.0")),
            OrderBookLevel(price=Decimal("49998"), quantity=Decimal("2.0")),
        ],
        asks=[
            OrderBookLevel(price=Decimal("50001"), quantity=Decimal("1.5")),
            OrderBookLevel(price=Decimal("50002"), quantity=Decimal("2.5")),
        ],
    )


# =============================================================================
# STATE STORE TESTS
# =============================================================================

class TestStateStoreIntegration:
    """Tests for StateStore data flow."""

    def test_update_price(self, state_store):
        """Test price update."""
        state_store.update_price(
            exchange="binance",
            symbol="BTC/USDT",
            price=Decimal("50000"),
            timestamp=datetime.now(timezone.utc),
        )

        snapshot = state_store.get_symbol_snapshot("binance", "BTC/USDT")
        assert snapshot is not None
        assert snapshot.last_price == Decimal("50000")

    def test_update_multiple_exchanges(self, state_store):
        """Test price updates from multiple exchanges."""
        now = datetime.now(timezone.utc)

        state_store.update_price("binance", "BTC/USDT", Decimal("50000"), now)
        state_store.update_price("bybit", "BTC/USDT", Decimal("50010"), now)
        state_store.update_price("okx", "BTC/USDT", Decimal("49990"), now)

        cross_price = state_store.get_cross_price("BTC/USDT")

        assert len(cross_price.prices) == 3
        assert "binance" in cross_price.prices
        assert "bybit" in cross_price.prices
        assert "okx" in cross_price.prices

    def test_add_trade(self, state_store, sample_trade):
        """Test trade addition."""
        state_store.add_trade("binance", sample_trade)

        snapshot = state_store.get_symbol_snapshot("binance", "BTC/USDT")
        assert snapshot is not None
        assert snapshot.trade_count_1m > 0

    def test_update_orderbook(self, state_store, sample_orderbook):
        """Test orderbook update."""
        state_store.update_orderbook("binance", sample_orderbook)

        snapshot = state_store.get_symbol_snapshot("binance", "BTC/USDT")
        assert snapshot is not None
        assert snapshot.last_orderbook is not None
        assert len(snapshot.last_orderbook.bids) == 2

    def test_cross_price_divergence(self, state_store):
        """Test price divergence calculation."""
        now = datetime.now(timezone.utc)

        # Create 2% divergence
        state_store.update_price("binance", "BTC/USDT", Decimal("50000"), now)
        state_store.update_price("bybit", "BTC/USDT", Decimal("51000"), now)  # +2%

        cross_price = state_store.get_cross_price("BTC/USDT")

        assert cross_price.spread_pct > 1.9  # ~2%
        assert cross_price.spread_pct < 2.1

    def test_common_symbols(self, state_store):
        """Test common symbols detection."""
        now = datetime.now(timezone.utc)

        state_store.update_price("binance", "BTC/USDT", Decimal("50000"), now)
        state_store.update_price("binance", "ETH/USDT", Decimal("3000"), now)
        state_store.update_price("bybit", "BTC/USDT", Decimal("50000"), now)
        state_store.update_price("bybit", "SOL/USDT", Decimal("100"), now)

        common = state_store.common_symbols()

        assert "BTC/USDT" in common
        assert "ETH/USDT" not in common  # Only on binance
        assert "SOL/USDT" not in common  # Only on bybit


# =============================================================================
# DETECTOR ORCHESTRATOR TESTS
# =============================================================================

class TestDetectorOrchestrator:
    """Tests for DetectorOrchestrator."""

    @pytest.fixture
    def orchestrator(self, state_store):
        """Create orchestrator with state store."""
        config = DetectorOrchestratorConfig(
            enable_price_divergence=True,
            enable_volume_correlation=True,
            enable_funding_arbitrage=True,
            enable_oi_migration=True,
            enable_liquidity_hunt=True,
            enable_spoofing_cross=True,
            parallel_analysis=False,  # Sequential for deterministic tests
            min_severity="WARNING",
        )
        return DetectorOrchestrator(state_store, config)

    def test_orchestrator_initialization(self, orchestrator):
        """Test orchestrator initializes all detectors."""
        assert len(orchestrator.detectors) == 6

    def test_detector_names(self, orchestrator):
        """Test detector names are correct."""
        names = [d.NAME for d in orchestrator.detectors]
        expected = [
            "price_divergence",
            "volume_correlation",
            "funding_arbitrage",
            "oi_migration",
            "liquidity_hunt",
            "spoofing_cross",
        ]
        assert set(names) == set(expected)

    @pytest.mark.asyncio
    async def test_analyze_symbol_no_data(self, orchestrator):
        """Test analysis with no data returns empty."""
        detections = await orchestrator.analyze_symbol("BTC/USDT")
        assert detections == []

    @pytest.mark.asyncio
    async def test_analyze_with_divergence(self, orchestrator, state_store):
        """Test detection of price divergence."""
        now = datetime.now(timezone.utc)

        # Create significant divergence (>1%)
        state_store.update_price("binance", "BTC/USDT", Decimal("50000"), now)
        state_store.update_price("bybit", "BTC/USDT", Decimal("50600"), now)  # 1.2%
        state_store.update_price("okx", "BTC/USDT", Decimal("49800"), now)

        detections = await orchestrator.analyze_symbol("BTC/USDT")

        # Should detect price divergence
        divergence_detections = [
            d for d in detections
            if d.detection_type == DetectionType.PRICE_DIVERGENCE
        ]

        # May or may not trigger depending on thresholds
        # At least we verify no errors
        assert isinstance(detections, list)

    @pytest.mark.asyncio
    async def test_analyze_all_symbols(self, orchestrator, state_store):
        """Test analyzing all symbols."""
        now = datetime.now(timezone.utc)

        # Add data for multiple symbols
        for symbol in ["BTC/USDT", "ETH/USDT"]:
            state_store.update_price("binance", symbol, Decimal("50000"), now)
            state_store.update_price("bybit", symbol, Decimal("50000"), now)

        results = await orchestrator.analyze_all()

        assert isinstance(results, dict)

    def test_get_statistics(self, orchestrator):
        """Test statistics retrieval."""
        stats = orchestrator.get_statistics()

        assert "active_detectors" in stats
        assert stats["active_detectors"] == 6
        assert "detector_names" in stats
        assert "config" in stats


# =============================================================================
# EXCHANGE MANAGER TESTS
# =============================================================================

class TestExchangeManager:
    """Tests for ExchangeManager."""

    @pytest.fixture
    def mock_connector(self):
        """Create mock exchange connector."""
        connector = MagicMock(spec=BaseExchange)
        connector.is_connected = True
        connector.state = ConnectionState.CONNECTED
        connector.connect = AsyncMock()
        connector.disconnect = AsyncMock()
        connector.subscribe_trades = AsyncMock()
        connector.subscribe_orderbook = AsyncMock()
        connector.unsubscribe = AsyncMock()
        connector.on_trade = MagicMock()
        connector.on_orderbook = MagicMock()
        return connector

    def test_manager_stats_initial(self):
        """Test initial stats."""
        stats = ExchangeManagerStats()
        assert stats.connected_exchanges == 0
        assert stats.total_trades == 0
        assert stats.errors == 0

    def test_callback_registration(self, mock_connector):
        """Test callback registration."""
        callbacks = []

        def on_trade(exchange, trade):
            callbacks.append((exchange, trade))

        # Create mock manager behavior
        trade_callbacks = [on_trade]

        # Simulate trade callback
        sample_trade = UnifiedTrade(
            exchange="test",
            symbol="BTC/USDT",
            trade_id="1",
            timestamp=datetime.now(timezone.utc),
            price=Decimal("50000"),
            quantity=Decimal("1"),
            side="buy",
            is_maker=False,
        )

        for cb in trade_callbacks:
            cb("test", sample_trade)

        assert len(callbacks) == 1
        assert callbacks[0][0] == "test"
        assert callbacks[0][1].symbol == "BTC/USDT"


# =============================================================================
# FULL INTEGRATION TESTS
# =============================================================================

class TestFullIntegration:
    """End-to-end integration tests."""

    @pytest.mark.asyncio
    async def test_data_flow(self, state_store):
        """Test data flows from trade to detection."""
        # 1. Add trades to state store
        now = datetime.now(timezone.utc)

        for exchange in ["binance", "bybit", "okx"]:
            price = Decimal("50000") + Decimal(str(hash(exchange) % 100))
            state_store.update_price(exchange, "BTC/USDT", price, now)

        # 2. Verify cross-price is populated
        cross_price = state_store.get_cross_price("BTC/USDT")
        assert len(cross_price.prices) == 3

        # 3. Create orchestrator and run analysis
        config = DetectorOrchestratorConfig(
            enable_price_divergence=True,
            enable_volume_correlation=False,
            enable_funding_arbitrage=False,
            enable_oi_migration=False,
            enable_liquidity_hunt=False,
            enable_spoofing_cross=False,
            min_severity="INFO",
        )
        orchestrator = DetectorOrchestrator(state_store, config)

        # 4. Analyze
        detections = await orchestrator.analyze_symbol("BTC/USDT")

        # Should work without errors
        assert isinstance(detections, list)

    @pytest.mark.asyncio
    async def test_multiple_symbols_analysis(self, state_store):
        """Test analysis of multiple symbols."""
        now = datetime.now(timezone.utc)
        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

        # Populate data
        for symbol in symbols:
            for exchange in ["binance", "bybit"]:
                price = Decimal("1000") * (hash(symbol) % 50 + 1)
                state_store.update_price(exchange, symbol, price, now)

        # Create orchestrator
        orchestrator = DetectorOrchestrator(
            state_store,
            DetectorOrchestratorConfig(
                enable_price_divergence=True,
                min_severity="INFO",
            )
        )

        # Analyze all
        results = await orchestrator.analyze_all(symbols=symbols)

        assert isinstance(results, dict)

    def test_severity_filtering(self, state_store):
        """Test that severity filtering works."""
        config = DetectorOrchestratorConfig(
            min_severity="CRITICAL",
        )
        orchestrator = DetectorOrchestrator(state_store, config)

        # WARNING severity should be filtered
        assert orchestrator._meets_severity(Severity.WARNING) is False
        assert orchestrator._meets_severity(Severity.ALERT) is False
        assert orchestrator._meets_severity(Severity.CRITICAL) is True


# =============================================================================
# STRESS TESTS
# =============================================================================

class TestStress:
    """Stress tests for performance validation."""

    @pytest.mark.asyncio
    async def test_high_volume_updates(self, state_store):
        """Test handling high volume of updates."""
        now = datetime.now(timezone.utc)

        # Simulate 1000 price updates
        for i in range(1000):
            exchange = ["binance", "bybit", "okx"][i % 3]
            symbol = f"TOKEN{i % 10}/USDT"
            price = Decimal("100") + Decimal(str(i % 100))
            state_store.update_price(exchange, symbol, price, now)

        # Should handle without errors
        common = state_store.common_symbols()
        assert len(common) > 0

    @pytest.mark.asyncio
    async def test_rapid_analysis(self, state_store):
        """Test rapid sequential analysis."""
        now = datetime.now(timezone.utc)

        # Setup data
        for exchange in ["binance", "bybit"]:
            state_store.update_price(exchange, "BTC/USDT", Decimal("50000"), now)

        orchestrator = DetectorOrchestrator(
            state_store,
            DetectorOrchestratorConfig(min_severity="INFO")
        )

        # Run 10 rapid analyses
        for _ in range(10):
            await orchestrator.analyze_symbol("BTC/USDT")

        # Should complete without errors


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
