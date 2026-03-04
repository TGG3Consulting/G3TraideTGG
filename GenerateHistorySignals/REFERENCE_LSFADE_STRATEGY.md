# REFERENCE: Profitable LS Fade Strategy

## Results (6 months, 18 altcoins, Aug 2024 - Jan 2025)
- Signals: 2456 (0.75 signals/day/coin)
- Win Rate: **34.8%** (855/1557/44 W/L/T)
- Total PnL: **+763.9%**
- Avg per trade: **+0.31%**

## Strategy Logic: LS FADE (Trade Against Crowd Extremes)

Enter trades when crowd positioning becomes extreme:
1. **SHORT**: when crowd is >65% LONG (fade bullish euphoria)
2. **LONG**: when crowd is >65% SHORT (fade bearish panic)

## Parameters
```python
strategy = "ls_fade"
ls_extreme = 0.65      # 65% threshold for extreme positioning
sl_pct = 5.0           # 5% stop loss
tp_pct = 10.0          # 10% take profit
max_hold_days = 14     # Exit on timeout after 14 days
```

## Code (daily_strategy_test.py, generate_signals)
```python
# ========== STRATEGY: LS Extreme Fade ==========
elif strategy == "ls_fade":
    # LONG: Crowd extremely short (fade their bearishness)
    if short_pct >= ls_extreme:
        signal = Signal(
            direction="LONG",
            stop_loss=entry * (1 - sl_pct / 100),
            take_profit=entry * (1 + tp_pct / 100),
        )
    # SHORT: Crowd extremely long (fade their bullishness)
    elif long_pct >= ls_extreme:
        signal = Signal(
            direction="SHORT",
            stop_loss=entry * (1 + sl_pct / 100),
            take_profit=entry * (1 - tp_pct / 100),
        )
```

## Performance Breakdown by Direction
| Direction | PnL |
|-----------|-----|
| **SHORT** | **+758.9%** |
| LONG | +5.0% |

**CRITICAL**: SHORT signals are highly profitable, LONGs are near breakeven.

## Why It Works
- Crowd extremes (>65% one direction) often precede reversals
- When everyone is bullish, there's no one left to buy = price drops
- When everyone is bearish, there's no one left to sell = price rises
- Works especially well on SHORT side (fading retail euphoria)

## Tested Coins (18 altcoins)
ZECUSDT, SUIUSDT, ALICEUSDT, TAOUSDT, ENAUSDT, UNIUSDT, ATOMUSDT,
APTUSDT, ARBUSDT, OPUSDT, INJUSDT, TIAUSDT, SEIUSDT, JUPUSDT,
STRKUSDT, WLDUSDT, MKRUSDT, LDOUSDT

## Alternative Configurations (also profitable)
| Config | Signals | Freq | WR% | TotalPnL | Note |
|--------|---------|------|-----|----------|------|
| LSFade_65_5_10 | 2456 | 0.75 | 34.8% | +763.9% | **BEST** |
| LSFade_60_5_10 | 2806 | 0.85 | 34.4% | +717.3% | More signals, slightly less PnL |
| MeanRev_7_15 | 449 | 0.14 | 38.3% | +685.3% | High WR but low frequency |
| MomLS_5_5_10 | 1391 | 0.42 | 34.9% | +484.7% | Good balance |

## Saved: 2025-02-28
