# -*- coding: utf-8 -*-
"""
Debug script to test accumulation analysis.
"""
import asyncio
import sys
sys.path.insert(0, '.')

from config.settings import settings
from src.signals.models import SignalConfig
from src.signals.accumulation_detector import AccumulationDetector
from src.screener.futures_monitor import FuturesMonitor
from src.cross_exchange.state_store import CrossExchangeStateStore


async def main():
    print("=" * 60)
    print("ACCUMULATION DEBUG")
    print("=" * 60)

    # Test symbols from alerts.jsonl
    test_symbols = ["TRXUSDT", "HBARUSDT", "TRUMPUSDT", "VIRTUALUSDT"]

    # Initialize components
    futures_monitor = FuturesMonitor()
    state_store = CrossExchangeStateStore()
    config = SignalConfig.from_settings()

    print(f"\nConfig values:")
    print(f"  min_accumulation_score: {config.min_accumulation_score}")
    print(f"  min_probability: {config.min_probability}")

    # Start futures monitor for test symbols
    print(f"\nStarting FuturesMonitor for {len(test_symbols)} symbols...")
    await futures_monitor.start(test_symbols)

    # Wait for data to load
    print("Waiting 5 seconds for data...")
    await asyncio.sleep(5)

    # Check each symbol
    print("\n" + "-" * 60)
    for symbol in test_symbols:
        print(f"\n--- {symbol} ---")

        # Check FuturesState
        state = futures_monitor.get_state(symbol)
        if not state:
            print(f"  FuturesState: None (not loaded)")
            continue

        print(f"  FuturesState: loaded")
        print(f"    has_futures: {state.has_futures}")
        print(f"    current_oi: {state.current_oi is not None}")
        print(f"    current_funding: {state.current_funding is not None}")
        print(f"    current_ls_ratio: {state.current_ls_ratio is not None}")
        print(f"    oi_change_1h_pct: {state.oi_change_1h_pct}")
        print(f"    oi_change_5m_pct: {state.oi_change_5m_pct}")

        if state.current_funding:
            print(f"    funding_rate_percent: {state.current_funding.funding_rate_percent}")
        if state.current_ls_ratio:
            print(f"    short_account_pct: {state.current_ls_ratio.short_account_pct}")

    # Test accumulation detector
    print("\n" + "-" * 60)
    print("\nAccumulation Detector Analysis:")

    detector = AccumulationDetector(
        futures_monitor=futures_monitor,
        state_store=state_store,
        config=config,
    )

    for symbol in test_symbols:
        print(f"\n--- {symbol} ---")
        result = detector.analyze(symbol)
        if result:
            print(f"  ACCUMULATION DETECTED!")
            print(f"    score: {result.score.total}")
            print(f"    direction: {result.direction.value}")
            print(f"    probability: {result.probability}")
            print(f"    confidence: {result.confidence.value}")
        else:
            print(f"  No accumulation (result=None)")
            # Try to understand why
            state = futures_monitor.get_state(symbol)
            if not state:
                print(f"    Reason: no FuturesState")
            elif not state.has_futures:
                print(f"    Reason: has_futures=False (current_oi is None)")
            else:
                # Calculate score manually
                score = detector._calculate_score(symbol, state)
                print(f"    Score breakdown:")
                print(f"      oi_growth: {score.oi_growth}")
                print(f"      oi_stability: {score.oi_stability}")
                print(f"      funding_cheap: {score.funding_cheap}")
                print(f"      funding_gradient: {score.funding_gradient}")
                print(f"      crowd_bearish: {score.crowd_bearish}")
                print(f"      volume_accumulation: {score.volume_accumulation}")
                print(f"      TOTAL: {score.total}")
                print(f"      threshold: {config.min_accumulation_score}")
                if score.total < config.min_accumulation_score:
                    print(f"    Reason: score {score.total} < threshold {config.min_accumulation_score}")

    # Cleanup
    await futures_monitor.stop()
    print("\n" + "=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
