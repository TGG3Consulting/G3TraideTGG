# -*- coding: utf-8 -*-
"""
Backtest signals using our Binance integration.
"""

import json
import sys
import time
import requests
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import List, Dict, Any, Optional


def fetch_klines_1h(symbol: str, start_time: datetime, end_time: datetime) -> List[Dict]:
    """Fetch 1h klines directly from Binance API."""
    url = "https://fapi.binance.com/fapi/v1/klines"
    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)

    all_klines = []
    current_start = start_ms

    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "interval": "1h",
            "startTime": current_start,
            "endTime": end_ms,
            "limit": 1500,
        }

        resp = requests.get(url, params=params)
        if resp.status_code != 200:
            print(f"Error: {resp.status_code} - {resp.text}")
            break

        data = resp.json()
        if not data:
            break

        for k in data:
            all_klines.append({
                "open_time": k[0],
                "open": k[1],
                "high": k[2],
                "low": k[3],
                "close": k[4],
                "volume": k[5],
                "close_time": k[6],
            })

        # Move to next batch
        current_start = data[-1][0] + 1

        if len(data) < 1500:
            break

        time.sleep(0.1)

    return all_klines


def backtest_signal(
    symbol: str,
    entry_time: datetime,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profits: List[Dict],
    hold_days: int = 7
) -> Dict[str, Any]:
    """
    Backtest a single signal using real kline data.

    Returns:
        Dict with result, exit_price, exit_time, pnl_pct
    """
    # Download klines for the holding period
    end_time = entry_time + timedelta(days=hold_days)

    # Get 1h klines for faster analysis
    klines = fetch_klines_1h(symbol, entry_time, end_time)

    if not klines:
        return {"result": "NO_DATA", "reason": "No klines available"}

    # Sort TPs by price
    tps_sorted = sorted(take_profits, key=lambda x: float(x["price"]), reverse=(direction == "SHORT"))

    result = {
        "symbol": symbol,
        "direction": direction,
        "entry_time": entry_time.isoformat(),
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profits": take_profits,
        "result": "TIMEOUT",  # Default if nothing hit
        "exit_price": None,
        "exit_time": None,
        "pnl_pct": 0.0,
        "tps_hit": [],
        "max_favorable": 0.0,
        "max_adverse": 0.0,
    }

    # Track max favorable/adverse excursion
    for kline in klines:
        ts = datetime.fromtimestamp(kline["open_time"] / 1000, tz=timezone.utc)
        high = float(kline["high"])
        low = float(kline["low"])
        close = float(kline["close"])

        if direction == "SHORT":
            # For SHORT: low is favorable, high is adverse
            favorable_pct = (entry_price - low) / entry_price * 100
            adverse_pct = (high - entry_price) / entry_price * 100

            result["max_favorable"] = max(result["max_favorable"], favorable_pct)
            result["max_adverse"] = max(result["max_adverse"], adverse_pct)

            # Check SL hit (high >= SL)
            if high >= stop_loss:
                result["result"] = "LOSS"
                result["exit_price"] = stop_loss
                result["exit_time"] = ts.isoformat()
                result["pnl_pct"] = -((stop_loss - entry_price) / entry_price * 100)
                break

            # Check TPs hit (low <= TP)
            for tp in tps_sorted:
                tp_price = float(tp["price"])
                if low <= tp_price and tp["label"] not in result["tps_hit"]:
                    result["tps_hit"].append(tp["label"])
                    result["result"] = "WIN"
                    result["exit_price"] = tp_price
                    result["exit_time"] = ts.isoformat()
                    result["pnl_pct"] = (entry_price - tp_price) / entry_price * 100

        else:  # LONG
            # For LONG: high is favorable, low is adverse
            favorable_pct = (high - entry_price) / entry_price * 100
            adverse_pct = (entry_price - low) / entry_price * 100

            result["max_favorable"] = max(result["max_favorable"], favorable_pct)
            result["max_adverse"] = max(result["max_adverse"], adverse_pct)

            # Check SL hit (low <= SL)
            if low <= stop_loss:
                result["result"] = "LOSS"
                result["exit_price"] = stop_loss
                result["exit_time"] = ts.isoformat()
                result["pnl_pct"] = -((entry_price - stop_loss) / entry_price * 100)
                break

            # Check TPs hit (high >= TP)
            for tp in tps_sorted:
                tp_price = float(tp["price"])
                if high >= tp_price and tp["label"] not in result["tps_hit"]:
                    result["tps_hit"].append(tp["label"])
                    result["result"] = "WIN"
                    result["exit_price"] = tp_price
                    result["exit_time"] = ts.isoformat()
                    result["pnl_pct"] = (tp_price - entry_price) / entry_price * 100

    # If timeout, calculate PnL based on last close
    if result["result"] == "TIMEOUT" and klines:
        last_close = float(klines[-1]["close"])
        if direction == "SHORT":
            result["pnl_pct"] = (entry_price - last_close) / entry_price * 100
        else:
            result["pnl_pct"] = (last_close - entry_price) / entry_price * 100
        result["exit_price"] = last_close
        result["exit_time"] = datetime.fromtimestamp(klines[-1]["close_time"] / 1000, tz=timezone.utc).isoformat()

    return result


def main():
    """Backtest signals from the output file."""

    # Read signals from output - find latest file
    import glob
    signal_files = sorted(glob.glob("output/signals_*.jsonl"))
    if not signal_files:
        print("No signal files found in output/")
        return []
    signals_file = signal_files[-1]  # Latest file

    print("=" * 60)
    print("BACKTEST SIGNALS (using Binance 1h klines)")
    print("=" * 60)
    print()

    signals = []
    with open(signals_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                signals.append(json.loads(line))

    print(f"Loaded {len(signals)} signals from {signals_file}")
    print()

    results = []
    wins = 0
    losses = 0
    timeouts = 0
    total_pnl = 0.0

    for i, sig_data in enumerate(signals):
        sig = sig_data["signal"]
        symbol = sig["symbol"]
        direction = sig["direction"]
        entry_time = datetime.fromisoformat(sig["timestamp"])
        entry_price = float(sig["current_price"])
        stop_loss = float(sig["stop_loss"])
        take_profits = sig["take_profits"]

        print(f"[{i+1}/{len(signals)}] {symbol} {direction} @ {entry_price:.4f}")
        print(f"    Entry: {entry_time.strftime('%Y-%m-%d %H:%M')}")
        print(f"    SL: {stop_loss:.4f} ({sig['stop_loss_pct']}%)")
        tps_str = ', '.join([tp['label'] + "=" + str(tp['price']) for tp in take_profits])
        print(f"    TPs: {tps_str}")

        result = backtest_signal(
            symbol=symbol,
            entry_time=entry_time,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profits=take_profits,
            hold_days=7
        )

        results.append(result)

        if result["result"] == "WIN":
            wins += 1
            print(f"    >>> WIN: {', '.join(result['tps_hit'])} hit, PnL: +{result['pnl_pct']:.2f}%")
        elif result["result"] == "LOSS":
            losses += 1
            print(f"    >>> LOSS: SL hit, PnL: {result['pnl_pct']:.2f}%")
        else:
            timeouts += 1
            print(f"    >>> TIMEOUT: PnL: {result['pnl_pct']:.2f}%")

        print(f"    Max favorable: +{result['max_favorable']:.2f}%, Max adverse: -{result['max_adverse']:.2f}%")
        print()

        total_pnl += result["pnl_pct"]

    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total signals: {len(signals)}")
    print(f"Wins: {wins} ({wins/len(signals)*100:.1f}%)")
    print(f"Losses: {losses} ({losses/len(signals)*100:.1f}%)")
    print(f"Timeouts: {timeouts} ({timeouts/len(signals)*100:.1f}%)")
    print(f"Total PnL: {total_pnl:.2f}%")
    print(f"Avg PnL per trade: {total_pnl/len(signals):.2f}%")
    print("=" * 60)

    return results


if __name__ == "__main__":
    main()
