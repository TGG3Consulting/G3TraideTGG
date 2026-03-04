# -*- coding: utf-8 -*-
"""Quick regime analysis for current coins."""
import json
from pathlib import Path
from datetime import datetime, timedelta

CACHE_DIR = Path("G:/BinanceFriend/GenerateHistorySignals/cache/binance")

symbols = ['LTCUSDT','UNIUSDT','CRVUSDT','APTUSDT','ARBUSDT','TRXUSDT','HBARUSDT','OPUSDT','XLMUSDT','GALAUSDT','ICPUSDT','DASHUSDT','XMRUSDT','TIAUSDT','SEIUSDT','CHZUSDT','FETUSDT','TONUSDT','ATOMUSDT','INJUSDT','SNXUSDT','ALGOUSDT','SANDUSDT','LDOUSDT','ZENUSDT','ALICEUSDT','EGLDUSDT','ROSEUSDT','PENDLEUSDT','KNCUSDT','CAKEUSDT','DYDXUSDT','TRBUSDT','APEUSDT','SUIUSDT','WLDUSDT','ENAUSDT','KAVAUSDT','ZROUSDT','DENTUSDT','ONDOUSDT','LITUSDT','RENDERUSDT','DUSKUSDT','ORDIUSDT','1000SHIBUSDT','WIFUSDT','JUPUSDT','POLUSDT','EIGENUSDT']

def load_klines(symbol):
    path = CACHE_DIR / symbol / "klines.json"
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    closes = {}
    for k in data:
        if isinstance(k, list):
            ts, close = k[0], float(k[4])
        else:
            ts = k.get("timestamp", k.get("t", 0))
            close = float(k.get("close", k.get("c", 0)))
        if ts > 0:
            dt = datetime.fromtimestamp(ts / 1000)
            closes[dt.strftime("%Y-%m-%d")] = close
    return closes

def calc_regime(closes, target_date, lookback=14):
    try:
        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    except:
        return "UNKNOWN", 0

    current_close = None
    for offset in range(1, 4):
        d = (target_dt - timedelta(days=offset)).strftime("%Y-%m-%d")
        if d in closes:
            current_close = closes[d]
            break

    past_dt = target_dt - timedelta(days=lookback)
    past_close = None
    for offset in range(1, 4):  # Start from 1 to avoid look-ahead bias
        d = (past_dt - timedelta(days=offset)).strftime("%Y-%m-%d")
        if d in closes:
            past_close = closes[d]
            break

    if not current_close or not past_close or past_close == 0:
        return "UNKNOWN", 0

    change = (current_close - past_close) / past_close * 100

    if change > 20: regime = "STRONG_BULL"
    elif change > 5: regime = "BULL"
    elif change > -5: regime = "SIDEWAYS"
    elif change > -20: regime = "BEAR"
    else: regime = "STRONG_BEAR"

    return regime, change

if __name__ == "__main__":
    target = "2026-03-04"
    regime_counts = {}
    coin_regimes = []

    for sym in symbols:
        closes = load_klines(sym)
        if closes:
            regime, change = calc_regime(closes, target, 14)
            coin_regimes.append((sym, regime, change))
            regime_counts[regime] = regime_counts.get(regime, 0) + 1
        else:
            coin_regimes.append((sym, "NO_DATA", 0))

    print("="*70)
    print("COIN REGIME ANALYSIS - March 4, 2026 (14-day lookback)")
    print("="*70)
    print()
    print("REGIME DISTRIBUTION:")
    for r in ["STRONG_BULL", "BULL", "SIDEWAYS", "BEAR", "STRONG_BEAR", "UNKNOWN", "NO_DATA"]:
        if r in regime_counts:
            print(f"  {r}: {regime_counts[r]} coins")
    print()
    coin_regimes.sort(key=lambda x: x[2], reverse=True)
    print("TOP 10 BULLISH:")
    for sym, regime, change in coin_regimes[:10]:
        print(f"  {sym:14} {regime:12} {change:+6.1f}%")
    print()
    print("TOP 10 BEARISH:")
    for sym, regime, change in coin_regimes[-10:]:
        print(f"  {sym:14} {regime:12} {change:+6.1f}%")
    print()
    print("SIDEWAYS COINS (-5% to +5%):")
    sideways = [x for x in coin_regimes if x[1] == "SIDEWAYS"]
    for sym, regime, change in sideways:
        print(f"  {sym:14} {change:+6.1f}%")
