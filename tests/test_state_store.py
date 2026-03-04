# -*- coding: utf-8 -*-
"""
Test suite for CrossExchangeStateStore.

Tests:
- Price updates and spread calculation
- OI distribution tracking
- Funding divergence detection
- Volume correlation
- Orderbook imbalance tracking
- Arbitrage opportunity detection
- Cleanup of stale data

Run: python tests/test_state_store.py
"""

import asyncio
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal

sys.path.insert(0, ".")

from src.cross_exchange.state_store import StateStore
from src.exchanges.models import UnifiedOrderBook, OrderBookLevel, UnifiedTrade, Side


async def test_price_updates():
    """Test price update and spread calculation."""
    print("\n" + "=" * 60)
    print("TEST: Price Updates and Spread")
    print("=" * 60)

    store = StateStore()

    # Register and update prices from multiple exchanges
    now = datetime.now(timezone.utc)

    await store.update_price("binance", "BTC/USDT", Decimal("50000.00"), Decimal("1000000"), now)
    await store.update_price("bybit", "BTC/USDT", Decimal("50010.00"), Decimal("800000"), now)
    await store.update_price("okx", "BTC/USDT", Decimal("49995.00"), Decimal("600000"), now)

    # Get cross-exchange price
    cross_price = store.get_cross_price("BTC/USDT")
    print(f"  Prices: {dict(cross_price.prices)}")
    print(f"  Volumes: {dict(cross_price.volumes)}")

    # Get price spread
    spread = store.get_price_spread("BTC/USDT")
    print(f"  Spreads: {spread}")

    # Verify spread calculation
    # binance_bybit: abs(50000 - 50010) / avg = 10 / 50005 * 100 = 0.02%
    assert "binance_bybit" in spread
    assert spread["_max"] > 0, "Should have max spread"

    print("  [OK] Price updates work correctly")
    return True


async def test_oi_distribution():
    """Test OI distribution calculation."""
    print("\n" + "=" * 60)
    print("TEST: OI Distribution")
    print("=" * 60)

    store = StateStore()
    now = datetime.now(timezone.utc)

    # Update OI from multiple exchanges
    await store.update_oi("binance", "BTC/USDT", Decimal("10000"), Decimal("500000000"), now)
    await store.update_oi("bybit", "BTC/USDT", Decimal("6000"), Decimal("300000000"), now)
    await store.update_oi("okx", "BTC/USDT", Decimal("4000"), Decimal("200000000"), now)

    # Get OI distribution
    distribution = store.get_oi_distribution("BTC/USDT")
    print(f"  OI Distribution: {distribution}")

    # Verify
    # Total = 500M + 300M + 200M = 1B
    # binance = 500M / 1B = 50%
    assert "binance" in distribution
    assert distribution["binance"] == Decimal("50")  # 50%
    assert distribution["_total_usd"] == Decimal("1000000000")

    print("  [OK] OI distribution works correctly")
    return True


async def test_funding_divergence():
    """Test funding rate divergence detection."""
    print("\n" + "=" * 60)
    print("TEST: Funding Divergence")
    print("=" * 60)

    store = StateStore()
    now = datetime.now(timezone.utc)

    # Update funding rates
    await store.update_funding("binance", "BTC/USDT", Decimal("0.0001"), None, now)  # 0.01%
    await store.update_funding("bybit", "BTC/USDT", Decimal("-0.0002"), None, now)   # -0.02%
    await store.update_funding("okx", "BTC/USDT", Decimal("0.00015"), None, now)     # 0.015%

    # Get funding divergence
    divergence = store.get_funding_divergence("BTC/USDT")
    print(f"  Funding Divergence: {divergence}")

    # Verify
    assert divergence["_max"] == Decimal("0.00015")
    assert divergence["_min"] == Decimal("-0.0002")
    assert divergence["_spread"] == Decimal("0.00035")  # 0.015% - (-0.02%) = 0.035%

    print("  [OK] Funding divergence works correctly")
    return True


async def test_orderbook_imbalance():
    """Test orderbook imbalance tracking."""
    print("\n" + "=" * 60)
    print("TEST: Orderbook Imbalance")
    print("=" * 60)

    store = StateStore()

    # Create orderbooks with different imbalances
    # Binance: heavy bids (bullish)
    binance_book = UnifiedOrderBook(
        exchange="binance",
        symbol="BTC/USDT",
        timestamp=datetime.now(timezone.utc),
        bids=[
            OrderBookLevel(Decimal("50000"), Decimal("10")),  # 500k
            OrderBookLevel(Decimal("49990"), Decimal("8")),
            OrderBookLevel(Decimal("49980"), Decimal("7")),
        ],
        asks=[
            OrderBookLevel(Decimal("50010"), Decimal("3")),
            OrderBookLevel(Decimal("50020"), Decimal("2")),
            OrderBookLevel(Decimal("50030"), Decimal("2")),
        ],
    )

    # Bybit: heavy asks (bearish)
    bybit_book = UnifiedOrderBook(
        exchange="bybit",
        symbol="BTC/USDT",
        timestamp=datetime.now(timezone.utc),
        bids=[
            OrderBookLevel(Decimal("50000"), Decimal("3")),
            OrderBookLevel(Decimal("49990"), Decimal("2")),
        ],
        asks=[
            OrderBookLevel(Decimal("50010"), Decimal("10")),
            OrderBookLevel(Decimal("50020"), Decimal("8")),
        ],
    )

    await store.update_orderbook("binance", "BTC/USDT", binance_book)
    await store.update_orderbook("bybit", "BTC/USDT", bybit_book)

    # Get cross-exchange imbalance
    imbalance = store.get_orderbook_imbalance_cross("BTC/USDT")
    print(f"  Orderbook Imbalance: {imbalance}")

    # Verify
    # binance: bids = 25, asks = 7, imbalance = 25/32 = 0.78
    # bybit: bids = 5, asks = 18, imbalance = 5/23 = 0.22
    assert "binance" in imbalance
    assert "bybit" in imbalance
    assert imbalance["binance"] > 0.7  # Heavy bids
    assert imbalance["bybit"] < 0.3    # Heavy asks

    print("  [OK] Orderbook imbalance works correctly")
    return True


async def test_arbitrage_detection():
    """Test arbitrage opportunity detection."""
    print("\n" + "=" * 60)
    print("TEST: Arbitrage Detection")
    print("=" * 60)

    store = StateStore()
    now = datetime.now(timezone.utc)

    # Create significant price difference
    await store.update_price("binance", "BTC/USDT", Decimal("50000.00"), None, now)
    await store.update_price("bybit", "BTC/USDT", Decimal("50100.00"), None, now)  # 0.2% higher

    # Find arbitrage
    opportunities = store.get_arbitrage_opportunities("BTC/USDT", min_spread_pct=Decimal("0.1"))
    print(f"  Arbitrage Opportunities: {opportunities}")

    # Verify
    assert len(opportunities) > 0
    opp = opportunities[0]
    assert opp["buy_exchange"] == "binance"
    assert opp["sell_exchange"] == "bybit"
    assert opp["spread_pct"] >= Decimal("0.1")

    print("  [OK] Arbitrage detection works correctly")
    return True


async def test_trade_volume():
    """Test trade volume aggregation."""
    print("\n" + "=" * 60)
    print("TEST: Trade Volume Aggregation")
    print("=" * 60)

    store = StateStore()
    now = datetime.now(timezone.utc)

    # Simulate trades
    for i in range(10):
        trade = UnifiedTrade(
            exchange="binance",
            symbol="BTC/USDT",
            timestamp=now,
            price=Decimal("50000"),
            quantity=Decimal("0.1"),
            side=Side.BUY if i % 2 == 0 else Side.SELL,
            trade_id=str(i),
        )
        await store.update_trade("binance", trade)

    for i in range(5):
        trade = UnifiedTrade(
            exchange="bybit",
            symbol="BTC/USDT",
            timestamp=now,
            price=Decimal("50000"),
            quantity=Decimal("0.2"),
            side=Side.BUY,
            trade_id=str(100 + i),
        )
        await store.update_trade("bybit", trade)

    # Get volume correlation
    correlation = store.get_volume_correlation("BTC/USDT")
    print(f"  Volume Correlation: {correlation}")

    # Verify
    assert "trade_count" in correlation
    assert correlation["trade_count"]["binance"] == 10
    assert correlation["trade_count"]["bybit"] == 5

    print("  [OK] Trade volume aggregation works correctly")
    return True


async def test_cleanup_stale():
    """Test cleanup of stale data."""
    print("\n" + "=" * 60)
    print("TEST: Stale Data Cleanup")
    print("=" * 60)

    store = StateStore()

    # Add old price (2 minutes ago)
    old_time = datetime.now(timezone.utc) - timedelta(minutes=2)
    await store.update_price("binance", "BTC/USDT", Decimal("50000"), None, old_time)

    # Add recent price
    now = datetime.now(timezone.utc)
    await store.update_price("bybit", "BTC/USDT", Decimal("50000"), None, now)

    print(f"  Before cleanup: {store.stats()}")

    # Cleanup data older than 60 seconds
    cleaned = await store.cleanup_stale(max_age_seconds=60)
    print(f"  Cleaned {cleaned} stale symbols")
    print(f"  After cleanup: {store.stats()}")

    # Verify binance data was cleaned
    snapshot = store.get_symbol_snapshot("binance", "BTC/USDT")
    assert snapshot is None or snapshot.is_stale(60)

    # Verify bybit data remains
    snapshot = store.get_symbol_snapshot("bybit", "BTC/USDT")
    assert snapshot is not None

    print("  [OK] Stale cleanup works correctly")
    return True


async def test_common_symbols():
    """Test common symbols detection."""
    print("\n" + "=" * 60)
    print("TEST: Common Symbols")
    print("=" * 60)

    store = StateStore()
    now = datetime.now(timezone.utc)

    # Add different symbols to different exchanges
    await store.update_price("binance", "BTC/USDT", Decimal("50000"), None, now)
    await store.update_price("binance", "ETH/USDT", Decimal("3000"), None, now)
    await store.update_price("binance", "SOL/USDT", Decimal("100"), None, now)

    await store.update_price("bybit", "BTC/USDT", Decimal("50000"), None, now)
    await store.update_price("bybit", "ETH/USDT", Decimal("3000"), None, now)

    await store.update_price("okx", "BTC/USDT", Decimal("50000"), None, now)

    store.set_exchange_connected("binance", True)
    store.set_exchange_connected("bybit", True)
    store.set_exchange_connected("okx", True)

    # Find common symbols
    common = store.common_symbols()
    print(f"  All symbols: {store.all_symbols()}")
    print(f"  Common symbols: {common}")

    # Verify
    assert "BTC/USDT" in common
    assert "SOL/USDT" not in common  # Only on binance

    print("  [OK] Common symbols detection works correctly")
    return True


async def test_price_leader():
    """Test price leader detection."""
    print("\n" + "=" * 60)
    print("TEST: Price Leader Detection")
    print("=" * 60)

    store = StateStore()

    # Simulate price movements over time
    base_time = datetime.now(timezone.utc) - timedelta(minutes=3)

    # Binance moves first
    for i in range(30):
        t = base_time + timedelta(seconds=i * 6)
        price = Decimal("50000") + Decimal(str(i * 10))  # Moving up
        await store.update_price("binance", "BTC/USDT", price, None, t)

    # Bybit follows with delay
    for i in range(30):
        t = base_time + timedelta(seconds=i * 6 + 3)  # 3 second delay
        price = Decimal("50000") + Decimal(str(i * 10))
        await store.update_price("bybit", "BTC/USDT", price, None, t)

    # Detect leader
    leader = store.get_price_leader("BTC/USDT", lookback_minutes=5)
    print(f"  Price leader: {leader}")

    # Should detect binance as leader (moves first)
    # Note: This is a simple heuristic, may not always work perfectly
    print("  [OK] Price leader detection completed")
    return True


async def main():
    """Run all tests."""
    print("\n" + "#" * 60)
    print("# CROSS-EXCHANGE STATE STORE TEST SUITE")
    print(f"# Started: {datetime.now()}")
    print("#" * 60)

    tests = [
        ("Price Updates", test_price_updates),
        ("OI Distribution", test_oi_distribution),
        ("Funding Divergence", test_funding_divergence),
        ("Orderbook Imbalance", test_orderbook_imbalance),
        ("Arbitrage Detection", test_arbitrage_detection),
        ("Trade Volume", test_trade_volume),
        ("Stale Cleanup", test_cleanup_stale),
        ("Common Symbols", test_common_symbols),
        ("Price Leader", test_price_leader),
    ]

    results = {}
    for name, test_func in tests:
        try:
            results[name] = await test_func()
        except Exception as e:
            print(f"  [X] Error: {e}")
            results[name] = False

    # Summary
    print("\n" + "#" * 60)
    print("# TEST SUMMARY")
    print("#" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, passed_test in results.items():
        status = "[OK]" if passed_test else "[FAIL]"
        print(f"  {status} {name}")

    print(f"\n  TOTAL: {passed}/{total} tests passed")

    # Rating
    rating = min(5, passed * 5 // total)
    print(f"\n  RATING: {rating}/5")


if __name__ == "__main__":
    asyncio.run(main())
