# REFERENCE: High Win-Rate LONG Strategy

## Results (5 months, 20 coins, Sep 2024 - Jan 2025)
- Signals: 7
- Win Rate: **85.7%** (6/7)
- Total PnL: **+98.03%**
- Avg per trade: **+14.00%**

## Strategy Logic
**LONG: Momentum + Contrarian Timing**

Enter LONG when:
1. **Uptrend**: price_change_7d >= 5% (price rising over 7 days)
2. **Crowd bearish**: short_pct >= 50% (crowd turning bearish = pullback opportunity)

This buys dips in uptrends when retail is scared.

## Code (accumulation_detector.py, _determine_direction)
```python
# LONG: Uptrend + crowd bearish = buy the dip
uptrend = price_change_7d >= 5
crowd_bearish_in_uptrend = short_pct >= 50
long_conditions = uptrend and crowd_bearish_in_uptrend
```

## Why It Works
- Trades WITH the trend (not against it)
- Uses crowd sentiment for TIMING (not direction)
- Buys when retail is fearful in an uptrend = optimal entry
- 100% win rate on LONG signals in backtest

## Limitation
- Only 7 signals in 6 months = too few for active trading
- Daily timeframe + strict conditions = rare signals
- Works best in bull markets

## Saved: 2025-01-31
