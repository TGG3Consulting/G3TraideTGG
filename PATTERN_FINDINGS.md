# Pattern Analysis Findings - 241,820 Trades
Date: 2026-03-04

## TO CONSIDER FOR IMPLEMENTATION

### 1. Direction x Regime Rules (HIGH PRIORITY)
- mean_reversion + STRONG_BULL + LONG = 71.9% WR (implement!)
- ls_fade + STRONG_BEAR + LONG = 54.0% WR (contrarian edge)
- NEVER LONG in BEAR for momentum/momentum_ls (24% WR, losing)
- reversal LONG is broken - loses in almost all regimes

### 2. Volatility Sweet Spot
- All strategies: 8-12% vol is optimal
- Current thresholds may need adjustment
- ls_fade 0-3% vol = 47.7% WR but rare (241 trades)

### 3. Day of Week Filter
- Tuesday is BAD for all strategies (28-30% WR)
- Friday BEST for momentum (36.7% WR)
- Monday BEST for mean_reversion (40.2% WR)
- Consider --day-filter Tuesday

### 4. Symbol Blacklist (AVOID)
- LTCUSDT: -10% momentum, -7% momentum_ls
- TRXUSDT: -2.7% momentum, -1.9% momentum_ls
- XMRUSDT: negative everywhere
- XLMUSDT: -2.1% reversal, -1.4% momentum_ls
- LITUSDT: 8-16% WR = disaster

### 5. Symbol Whitelist (FOCUS)
- GALAUSDT: +28% ls_fade, +19% momentum, +17% momentum_ls
- 1000SHIBUSDT: +23% ls_fade, +11% momentum_ls
- CHZUSDT: +16% momentum, +13% momentum_ls
- DENTUSDT: +25% ls_fade, +12% momentum

### 6. Potential New Filters
- --direction-by-regime (auto LONG/SHORT based on coin regime)
- --skip-tuesday
- --symbol-blacklist LTCUSDT,TRXUSDT,XMRUSDT,XLMUSDT
- --symbol-whitelist GALAUSDT,1000SHIBUSDT,CHZUSDT

### 7. Current Best Setup (Confirmed by Tests)
```
--vol-filter-low --coin-regime
```
- Works best in STRONG BULL + HIGH VOL
- Do NOT use --vol-filter-high in bull markets
- Calmar 8.52 achieved with this setup

### 8. ML Filter Status
- Currently too aggressive (filters 72%)
- Kills ls_fade completely
- Needs retraining on 2025-2026 data
- DO NOT USE in production until fixed

---

## RAW DATA REFERENCE

### Direction x Regime Matrix (WinRate)
```
                    LONG    SHORT
ls_fade:
  BEAR              32.5%   36.0% (+231%)
  BULL              37.6%   34.6%
  SIDEWAYS          46.4%   34.8%
  STRONG_BEAR       54.0%   29.7%
  STRONG_BULL       31.5%   35.5%

mean_reversion:
  BEAR              33.3%   46.8%
  BULL               4.0%   37.6%
  SIDEWAYS          29.0%   33.1%
  STRONG_BEAR       30.9%   41.4%
  STRONG_BULL       71.9%   36.2%

momentum:
  BEAR              24.3%   35.4% (+156%)
  BULL              31.3%   35.3%
  SIDEWAYS          30.2%   36.2%
  STRONG_BEAR       26.0%   30.0%
  STRONG_BULL       30.0%   32.7%

momentum_ls:
  BEAR              23.5%   35.5% (+155%)
  BULL              31.7%   35.6%
  SIDEWAYS          28.7%   36.8%
  STRONG_BEAR       38.6%   30.0%
  STRONG_BULL       30.9%   34.3%

reversal:
  BEAR              26.7%   42.5%
  BULL              26.3%   33.4%
  SIDEWAYS          23.1%   38.0%
  STRONG_BEAR       30.3%   29.5%
  STRONG_BULL       26.2%   33.4%
```

### Best Volatility by Strategy
```
ls_fade:        8-12% (+251% PnL)
momentum:       8-12% (+110% PnL)
momentum_ls:    8-12% (+127% PnL)
mean_reversion: 8-12% (+56% PnL)
reversal:       8-12% (+25% PnL)
```

---
STATUS: TO REVIEW / NOT IMPLEMENTED
