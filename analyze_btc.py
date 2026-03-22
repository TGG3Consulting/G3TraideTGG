# -*- coding: utf-8 -*-
import requests
from datetime import datetime, timezone
import statistics

print("=== BTCUSDT REAL-TIME ANALYSIS ===\n")

# 1. Current Price
ticker = requests.get('https://fapi.binance.com/fapi/v1/ticker/price', params={'symbol': 'BTCUSDT'}).json()
price = float(ticker['price'])
print(f"CURRENT PRICE: ${price:,.2f}")
print()

# 2. 24h Stats
stats = requests.get('https://fapi.binance.com/fapi/v1/ticker/24hr', params={'symbol': 'BTCUSDT'}).json()
print("=== 24H STATS ===")
print(f"High:   ${float(stats['highPrice']):,.2f}")
print(f"Low:    ${float(stats['lowPrice']):,.2f}")
print(f"Change: {float(stats['priceChangePercent']):+.2f}%")
print(f"Volume: ${float(stats['quoteVolume'])/1e9:.2f}B")
print()

# 3. L/S Ratio
ls_data = requests.get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
    params={'symbol': 'BTCUSDT', 'period': '1d', 'limit': 7}).json()
print("=== LONG/SHORT RATIO (7 days) ===")
for item in ls_data:
    ts = datetime.fromtimestamp(item['timestamp']/1000, tz=timezone.utc)
    long_pct = float(item['longAccount'])
    short_pct = float(item['shortAccount'])
    print(f"{ts.strftime('%Y-%m-%d')}: LONG={long_pct:.1%} SHORT={short_pct:.1%}")

current_ls = ls_data[-1]
long_pct = float(current_ls['longAccount'])
short_pct = float(current_ls['shortAccount'])
print(f">>> CURRENT: LONG={long_pct:.1%} SHORT={short_pct:.1%}")
print()

# 4. Open Interest
oi_data = requests.get('https://fapi.binance.com/futures/data/openInterestHist',
    params={'symbol': 'BTCUSDT', 'period': '1d', 'limit': 7}).json()
print("=== OPEN INTEREST (7 days) ===")
for item in oi_data:
    ts = datetime.fromtimestamp(item['timestamp']/1000, tz=timezone.utc)
    oi_usd = float(item['sumOpenInterestValue'])
    print(f"{ts.strftime('%Y-%m-%d')}: ${oi_usd/1e9:.2f}B")

if len(oi_data) >= 2:
    oi_change = (float(oi_data[-1]['sumOpenInterestValue']) / float(oi_data[-2]['sumOpenInterestValue']) - 1) * 100
    print(f">>> 1d Change: {oi_change:+.2f}%")
print()

# 5. Funding Rate
funding = requests.get('https://fapi.binance.com/fapi/v1/fundingRate',
    params={'symbol': 'BTCUSDT', 'limit': 8}).json()
print("=== FUNDING RATE (last 24h) ===")
for item in funding[-8:]:
    ts = datetime.fromtimestamp(item['fundingTime']/1000, tz=timezone.utc)
    rate = float(item['fundingRate']) * 100
    print(f"{ts.strftime('%m-%d %H:%M')}: {rate:+.4f}%")

current_funding = float(funding[-1]['fundingRate']) * 100
avg_funding = sum(float(f['fundingRate']) for f in funding[-8:]) / 8 * 100
print(f">>> CURRENT: {current_funding:+.4f}% | AVG(24h): {avg_funding:+.4f}%")
print()

# 6. Price Action
klines = requests.get('https://fapi.binance.com/fapi/v1/klines',
    params={'symbol': 'BTCUSDT', 'interval': '1d', 'limit': 14}).json()
print("=== PRICE ACTION (14 days) ===")
for k in klines:
    ts = datetime.fromtimestamp(k[0]/1000, tz=timezone.utc)
    o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
    change = (c/o - 1) * 100
    print(f"{ts.strftime('%m-%d')}: ${c:,.0f} ({change:+.2f}%)")

daily_ranges = [(float(k[2])-float(k[3]))/float(k[3]) * 100 for k in klines[-14:]]
closes = [float(k[4]) for k in klines]
print(f">>> AVG Daily Range: {statistics.mean(daily_ranges):.2f}%")
print(f">>> 14d High: ${max(float(k[2]) for k in klines):,.0f}")
print(f">>> 14d Low:  ${min(float(k[3]) for k in klines):,.0f}")
print()

# 7. Order Book
depth = requests.get('https://fapi.binance.com/fapi/v1/depth', params={'symbol': 'BTCUSDT', 'limit': 20}).json()
bid_vol = sum(float(b[1]) for b in depth['bids'])
ask_vol = sum(float(a[1]) for a in depth['asks'])
imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol) * 100
print("=== ORDER BOOK (top 20) ===")
print(f"Bid Volume: {bid_vol:.1f} BTC")
print(f"Ask Volume: {ask_vol:.1f} BTC")
print(f"Imbalance:  {imbalance:+.1f}% ({'BUYERS' if imbalance > 0 else 'SELLERS'} dominant)")
print()

# 8. BTC Dominance
btcdom = requests.get('https://fapi.binance.com/fapi/v1/klines',
    params={'symbol': 'BTCDOMUSDT', 'interval': '1d', 'limit': 7}).json()
print("=== BTC DOMINANCE (7 days) ===")
for k in btcdom:
    ts = datetime.fromtimestamp(k[0]/1000, tz=timezone.utc)
    print(f"{ts.strftime('%m-%d')}: {float(k[4]):.2f}%")
dom_change = float(btcdom[-1][4]) - float(btcdom[0][4])
print(f">>> 7d Change: {dom_change:+.2f}%")
print()

# 9. Top Trader L/S (позиции крупных трейдеров)
top_ls = requests.get('https://fapi.binance.com/futures/data/topLongShortAccountRatio',
    params={'symbol': 'BTCUSDT', 'period': '1d', 'limit': 3}).json()
print("=== TOP TRADERS L/S (last 3 days) ===")
for item in top_ls:
    ts = datetime.fromtimestamp(item['timestamp']/1000, tz=timezone.utc)
    print(f"{ts.strftime('%Y-%m-%d')}: LONG={float(item['longAccount']):.1%} SHORT={float(item['shortAccount']):.1%}")
print()

# 10. Taker Buy/Sell Volume
taker = requests.get('https://fapi.binance.com/futures/data/takerlongshortRatio',
    params={'symbol': 'BTCUSDT', 'period': '1d', 'limit': 3}).json()
print("=== TAKER BUY/SELL (last 3 days) ===")
for item in taker:
    ts = datetime.fromtimestamp(item['timestamp']/1000, tz=timezone.utc)
    buy = float(item['buyVol'])
    sell = float(item['sellVol'])
    ratio = buy/sell if sell > 0 else 0
    print(f"{ts.strftime('%Y-%m-%d')}: Buy=${buy/1e9:.2f}B Sell=${sell/1e9:.2f}B Ratio={ratio:.2f}")
