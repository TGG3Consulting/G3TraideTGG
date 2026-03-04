# -*- coding: utf-8 -*-
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import requests

r = requests.get('https://fapi.binance.com/fapi/v1/exchangeInfo')
symbols_info = r.json()['symbols']

r2 = requests.get('https://fapi.binance.com/fapi/v1/ticker/24hr')
tickers = {t['symbol']: float(t['quoteVolume']) for t in r2.json()}

usdt_perps = []
for s in symbols_info:
    if (s.get('quoteAsset') == 'USDT' and
        s.get('status') == 'TRADING' and
        s.get('contractType') == 'PERPETUAL'):
        sym = s['symbol']
        vol = tickers.get(sym, 0)
        usdt_perps.append((sym, vol))

usdt_perps.sort(key=lambda x: -x[1])

# === ИСКЛЮЧЕНИЯ ===
exclude = {
    # TOP COINS (слишком ликвидные)
    'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'XRPUSDT', 'SOLUSDT',
    'ADAUSDT', 'DOGEUSDT', 'AVAXUSDT', 'LINKUSDT', 'DOTUSDT',
    # STABLECOINS
    'USDTUSDT', 'USDCUSDT', 'BUSDUSDT', 'TUSDUSDT', 'DAIUSDT',
    'FDUSDUSDT', 'USDPUSDT', 'USDDUSDT',
    # WRAPPED
    'WBTCUSDT', 'WETHUSDT', 'WBNBUSDT',
    # USER EXCLUDED
    '1000PEPEUSDT', 'BCHUSDT', 'ETCUSDT', 'AAVEUSDT', 'FILUSDT',
    'NEARUSDT', 'AXSUSDT',
}

# Мусорные/мем монеты которые не подходят для accumulation trading
meme_trash = {
    'TRUMPUSDT', 'PUMPUSDT', 'USELESSUSDT', 'WLFIUSDT',
    'PIPPINUSDT', 'RAVEUSDT', 'SIRENUSDT', 'BEATUSDT',
    'STABLEUSDT', 'TRUTHUSDT', 'ZAMAUSDT', 'POWERUSDT',
    'VVVUSDT', 'SPACEUSDT', 'FOGOUSDT', 'NOMUSDT',
    'BREVUSDT', 'ACUUSDT', 'NAORISUSDT', 'HUSDT',
    'AWEUSDT', 'XPLUSDT', 'OPNUSDT', 'LYNUSDT', 'MONUSDT',
    'ESPUSDT', 'RIVERUSDT', 'KITEUSDT', 'ASTERUSDT',
    'MYXUSDT', 'ALLOUSDT', 'ENSOUSDT', 'AZTECUSDT',
}

# Ключевые слова для исключения
bad_keywords = ['PAXG', 'GOLD', 'SILVER', 'OIL', 'AAPL', 'TSLA', 'COIN', 'GOOG', 'AMZN']

filtered = []
for sym, vol in usdt_perps:
    if sym in exclude:
        continue
    if sym in meme_trash:
        continue
    skip = False
    for kw in bad_keywords:
        if kw in sym:
            skip = True
            break
    if skip:
        continue
    # Skip non-ASCII symbols (Chinese meme coins etc)
    if not sym.isascii():
        continue
    # Skip very low volume (less than 5M USD daily) - нужна ликвидность
    if vol < 5_000_000:
        continue
    filtered.append((sym, vol))

# Print exactly 100
print("=" * 60)
print("100 USDT PERPETUAL FUTURES (sorted by 24h volume)")
print("Excludes: top coins, stables, wrapped, meme trash")
print("=" * 60)
print()

for i, (sym, vol) in enumerate(filtered[:100], 1):
    vol_m = vol / 1_000_000
    print(f"{i:3}. {sym:<20} ${vol_m:>8.1f}M")

print()
print("=" * 60)
print("COMMA-SEPARATED LIST FOR --symbols:")
print("=" * 60)
symbols_list = ','.join([s[0] for s in filtered[:100]])
print(symbols_list)
