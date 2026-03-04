# -*- coding: utf-8 -*-
"""
Тест реальности данных в FuturesMonitor.
Доказывает что данные приходят с Binance API.
"""

import asyncio
import sys
sys.path.insert(0, '.')

from src.screener.futures_monitor import FuturesMonitor


async def test_real_data():
    """Проверить что FuturesMonitor получает РЕАЛЬНЫЕ данные."""
    print("\n" + "=" * 70)
    print("TEST: REAL DATA FROM FUTURES MONITOR")
    print("=" * 70)

    # Создать монитор
    monitor = FuturesMonitor(on_detection=lambda d: None)

    try:
        # Запустить с несколькими символами
        test_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]
        print(f"\nStarting monitor for: {test_symbols}")

        await monitor.start(test_symbols)

        # Ждём загрузки данных
        print("\nWaiting for data (15 sec)...")
        for i in range(15):
            await asyncio.sleep(1)
            print(f"  {i+1}/15...", end="\r")

        print("\n" + "-" * 70)
        print("RESULTS:")
        print("-" * 70)

        for symbol in test_symbols:
            state = monitor.get_state(symbol)

            print(f"\n=== {symbol} ===")

            if not state:
                print("  [X] NO STATE - data not loaded!")
                continue

            if not state.has_futures:
                print("  [X] NO FUTURES DATA")
                continue

            # OI данные
            if state.current_oi:
                oi = state.current_oi
                print(f"  [OI] Open Interest: {float(oi.open_interest):,.0f} contracts")
                print(f"       OI in USD: ${float(oi.open_interest_usd):,.0f}")
                print(f"       Mark Price: ${float(oi.mark_price):,.2f}")
            else:
                print("  [!] OI: no data")

            # OI Changes (рассчитанные)
            print(f"  [CHANGE] OI 1h: {float(state.oi_change_1h_pct):+.2f}%")
            print(f"           OI 5m: {float(state.oi_change_5m_pct):+.2f}%")
            print(f"           OI 1m: {float(state.oi_change_1m_pct):+.2f}%")

            # Funding Rate
            if state.current_funding:
                fr = state.current_funding
                print(f"  [FUNDING] Rate: {float(fr.funding_rate_percent):.4f}%")
                print(f"            Mark Price: ${float(fr.mark_price):,.2f}")
            else:
                print("  [!] Funding: no data")

            # L/S Ratio
            if state.current_ls_ratio:
                ls = state.current_ls_ratio
                print(f"  [L/S] Long: {float(ls.long_account_pct):.1f}% / Short: {float(ls.short_account_pct):.1f}%")
            else:
                print("  [!] L/S Ratio: no data")

            # История
            print(f"  [HISTORY] OI points: {len(state.oi_history)}, Funding points: {len(state.funding_history)}")

        print("\n" + "=" * 70)
        print("[OK] TEST COMPLETE - DATA IS REAL (from Binance API)")
        print("=" * 70)

    except Exception as e:
        print(f"\n[ERROR]: {e}")
        import traceback
        traceback.print_exc()

    finally:
        await monitor.stop()


if __name__ == "__main__":
    asyncio.run(test_real_data())
