# Trading Analysis Results

**Last Updated:** 2026-03-03
**Data Source:** outputNEWARCH/obucheniyeML24_26gg_68_monet_primerno_outputNEWARCH
**Total Trades Analyzed:** ~149,653
**Period:** 2024-2026
**Strategies:** ls_fade, momentum, reversal, mean_reversion, momentum_ls

---

## 1. Feature Importance Analysis

**Method:** Single-feature LightGBM models for each feature x strategy combination
**Metrics:** Filter AUC, Confidence AUC, Direction Accuracy, Lifetime MAE
**Total combinations:** 27 features x 5 strategies = 135

### TOP-10 Features by Filter AUC (averaged across strategies)

| Rank | Feature | Filter AUC | Conf AUC | Dir Acc | Life MAE |
|------|---------|------------|----------|---------|----------|
| 1 | **Month** | **0.601** | 0.601 | 66.8% | 1.29 |
| 2 | **Chain Seq** | **0.546** | 0.550 | 66.6% | 1.30 |
| 3 | DayOfWeek | 0.529 | 0.529 | 66.6% | 1.31 |
| 4 | Open | 0.528 | 0.526 | 66.5% | 1.31 |
| 5 | Prev Low | 0.526 | 0.525 | 66.6% | 1.31 |
| 6 | Prev Close | 0.526 | 0.527 | 66.5% | 1.31 |
| 7 | Direction_num | 0.526 | 0.527 | 66.6% | 1.31 |
| 8 | ADX | 0.525 | 0.524 | 66.5% | 1.31 |
| 9 | Prev High | 0.525 | 0.522 | 66.5% | 1.31 |
| 10 | Prev_Volatility | 0.525 | 0.525 | 66.4% | 1.27 |

### Weak Features (AUC ~ 0.50, no predictive power)

| Feature | Filter AUC | Note |
|---------|------------|------|
| Hour | 0.500 | Data not loaded correctly |
| Prev_CandleDir | 0.500 | Zero signal |
| Prev Volume | 0.501 | Minimal signal |
| Prev Taker Buy Vol | 0.501 | Minimal signal |

### Key Insights
- **Month is the strongest predictor** (0.601 AUC) - significant seasonality
- **Chain Seq confirms loss clustering** - position in signal chain matters
- **L/S features are weak** - Long%, Short%, L/S Ratio all ~0.51

---

## 2. Monthly Performance Analysis

**Breakdown:** All strategies combined, all years (2024-2026)

### Monthly Summary (sorted by WinRate)

| Month | Trades | Wins | WinRate | Total PnL | Avg PnL |
|-------|--------|------|---------|-----------|---------|
| **June** | 12,168 | 5,232 | **43.0%** | **+236.5%** | +0.02% |
| January | 15,951 | 6,315 | 40.0% | +234.9% | +0.01% |
| July | 11,586 | 4,338 | 37.0% | +132.8% | +0.01% |
| April | 12,920 | 4,654 | 36.0% | +124.0% | +0.01% |
| November | 12,239 | 4,196 | 34.0% | +83.8% | +0.01% |
| December | 11,955 | 4,067 | 34.0% | +81.2% | +0.01% |
| March | 13,443 | 4,393 | 33.0% | +70.2% | +0.01% |
| February | 15,164 | 4,469 | 29.0% | +3.5% | +0.00% |
| October | 10,660 | 3,084 | 29.0% | -7.1% | -0.00% |
| August | 11,953 | 3,356 | 28.0% | -19.1% | -0.00% |
| May | 11,630 | 3,113 | 27.0% | -38.1% | -0.00% |
| **September** | 9,984 | 2,512 | **25.0%** | **-56.4%** | -0.01% |

**Spread:** 18 percentage points (June 43% vs September 25%)

---

## 3. Strategy x Month Analysis (WR% / PnL% / MaxDD%)

**Format:** WinRate% / TotalPnL% / MaxDrawdown%

| Month | ls_fade | momentum | reversal | mean_rev | momentum_ls |
|-------|---------|----------|----------|----------|-------------|
| Jan | 43/+105/-27 | 35/+43/-30 | 33/+5/-8 | 49/+13/-2 | 42/+69/-18 |
| Feb | 24/-29/-68 | 34/+28/-35 | 24/-8/-9 | 30/+2/-3 | 31/+10/-25 |
| Mar | 36/+49/-29 | 27/-15/-27 | 42/+19/-4 | 35/+11/-3 | 31/+6/-19 |
| Apr | 42/+71/-30 | 31/+8/-28 | 38/+14/-8 | 40/+8/-5 | 35/+23/-14 |
| May | 31/+13/-26 | 18/-56/-62 | 45/+16/-2 | 55/+24/-1 | 18/-34/-40 |
| Jun | 48/+106/-13 | 41/+63/-16 | 18/-11/-12 | 47/+3/-0 | 45/+75/-13 |
| Jul | 37/+40/-37 | 40/+62/-14 | 29/-1/-11 | 26/-3/-13 | 40/+34/-14 |
| Aug | 32/+13/-33 | 22/-43/-68 | 43/+15/-5 | 55/+17/-1 | 24/-21/-46 |
| Sep | 25/-20/-48 | 25/-25/-31 | 29/+0/-5 | 35/+3/-4 | 24/-15/-21 |
| Oct | 37/+34/-30 | 25/-24/-38 | 19/-15/-19 | 44/+4/-0 | 28/-6/-22 |
| Nov | 27/-10/-45 | 41/+72/-17 | 28/-1/-12 | 27/-3/-15 | 37/+26/-14 |
| Dec | 38/+42/-13 | 32/+16/-17 | 23/-9/-13 | 38/+10/-6 | 35/+22/-13 |

### Strategy Totals

| Strategy | Trades | WR% | Total PnL | MaxDD |
|----------|--------|-----|-----------|-------|
| ls_fade | 47,380 | 35.2% | +414.6% | -93.4% |
| momentum_ls | 33,656 | 33.2% | +188.0% | -56.0% |
| momentum | 50,965 | 31.3% | +128.9% | -85.3% |
| mean_reversion | 6,874 | 37.8% | +88.8% | -20.6% |
| reversal | 10,778 | 31.0% | +26.0% | -23.4% |

---

## 4. Day of Week Analysis

**Breakdown:** All strategies, all years (2024-2026)

### Daily Summary (sorted by WinRate)

| Day | Trades | Wins | WinRate | Total PnL | Avg PnL |
|-----|--------|------|---------|-----------|---------|
| **Thursday** | 20,521 | 7,129 | **35.0%** | +160.6% | +0.010% |
| **Saturday** | 21,213 | 7,356 | **35.0%** | +161.4% | +0.010% |
| Tuesday | 22,109 | 7,443 | 34.0% | +138.6% | +0.010% |
| Friday | 21,012 | 7,115 | 34.0% | +137.0% | +0.010% |
| Sunday | 21,211 | 7,152 | 34.0% | +134.3% | +0.010% |
| Monday | 21,407 | 6,850 | 32.0% | +83.4% | +0.000% |
| **Wednesday** | 22,180 | 6,684 | **30.0%** | +30.9% | +0.000% |

**Spread:** 5 pp (Thursday/Saturday 35% vs Wednesday 30%)

### Strategy x Day of Week (WR% / PnL% / MaxDD%)

| Day | ls_fade | momentum | reversal | mean_rev | momentum_ls | AVG |
|-----|---------|----------|----------|----------|-------------|-----|
| Monday | 36/+63/-15 | 29/-9/-35 | 29/-0/-10 | 40/+16/-3 | 31/+14/-14 | 32.9% |
| Tuesday | 37/+77/-16 | 30/+7/-27 | 31/+4/-12 | **42/+21/-4** | 33/+30/-17 | **34.8%** |
| **Wednesday** | 33/+43/-18 | **27/-27/-47** | 32/+9/-7 | 40/+18/-4 | **28/-12/-34** | 32.1% |
| **Thursday** | 36/+64/-17 | 33/+36/-22 | **35/+13/-8** | 36/+10/-4 | 35/+38/-12 | **35.2%** |
| Friday | 33/+41/-26 | 35/+59/-16 | 33/+8/-16 | **26/-2/-7** | 34/+32/-15 | 32.4% |
| Saturday | 35/+59/-19 | 34/+50/-21 | 28/-2/-15 | 35/+8/-5 | **36/+47/-13** | 33.8% |
| Sunday | 36/+69/-21 | 31/+13/-32 | **27/-4/-7** | **41/+18/-4** | 35/+39/-24 | 34.0% |

### Best & Worst Days per Strategy

| Strategy | BEST Day | WR% | WORST Day | WR% | Spread |
|----------|----------|-----|-----------|-----|--------|
| ls_fade | Tuesday | 37% | Friday | 33% | 4pp |
| momentum | Friday | 35% | **Wednesday** | 27% | 8pp |
| reversal | Thursday | 35% | Sunday | 27% | 8pp |
| mean_reversion | **Tuesday** | 42% | **Friday** | 26% | **16pp** |
| momentum_ls | Saturday | 36% | **Wednesday** | 28% | 9pp |

### Detailed by Strategy

#### LS_FADE by Day

| Day | Trades | Wins | WinRate | Total PnL | Avg PnL | MaxDD |
|-----|--------|------|---------|-----------|---------|-------|
| Monday | 6,677 | 2,384 | 35.7% | +62.7% | +0.009% | -15.3% |
| **Tuesday** | 6,822 | 2,525 | **37.0%** | **+77.1%** | +0.011% | -16.3% |
| Wednesday | 6,722 | 2,246 | 33.4% | +42.7% | +0.006% | -18.0% |
| Thursday | 6,743 | 2,405 | 35.7% | +64.1% | +0.009% | -17.0% |
| **Friday** | 6,768 | 2,248 | **33.2%** | +40.7% | +0.006% | -25.6% |
| Saturday | 6,864 | 2,405 | 35.0% | +58.5% | +0.009% | -19.2% |
| Sunday | 6,784 | 2,455 | 36.2% | +68.7% | +0.010% | -21.3% |
| **TOTAL** | 47,380 | 16,668 | 35.2% | +414.6% | +0.009% | -93.4% |

**BEST:** Tuesday (37.0% WR) | **WORST:** Friday (33.2% WR) | **Spread:** 3.8 pp

#### MOMENTUM by Day

| Day | Trades | Wins | WinRate | Total PnL | Avg PnL | MaxDD |
|-----|--------|------|---------|-----------|---------|-------|
| Monday | 7,300 | 2,086 | 28.6% --- | -8.9% | -0.001% | -34.8% !!! |
| Tuesday | 7,545 | 2,270 | 30.1% | +6.9% | +0.001% | -26.7% |
| **Wednesday** | 7,522 | 2,020 | **26.9%** --- | **-26.8%** | -0.004% | **-47.2%** !!! |
| Thursday | 6,869 | 2,282 | 33.2% | +36.3% | +0.005% | -21.7% |
| **Friday** | 7,193 | 2,540 | **35.3%** | **+58.7%** | +0.008% | -15.9% |
| Saturday | 7,255 | 2,499 | 34.4% | +50.2% | +0.007% | -21.5% |
| Sunday | 7,281 | 2,234 | 30.7% | +12.6% | +0.002% | -31.8% !!! |
| **TOTAL** | 50,965 | 15,931 | 31.3% | +128.9% | +0.003% | -85.3% |

**BEST:** Friday (35.3% WR) | **WORST:** Wednesday (26.9% WR) | **Spread:** 8.5 pp

#### REVERSAL by Day

| Day | Trades | Wins | WinRate | Total PnL | Avg PnL | MaxDD |
|-----|--------|------|---------|-----------|---------|-------|
| Monday | 1,635 | 477 | 29.2% --- | -0.4% | -0.000% | -10.0% |
| Tuesday | 1,605 | 496 | 30.9% | +3.7% | +0.002% | -12.1% |
| Wednesday | 1,855 | 602 | 32.5% | +8.5% | +0.005% | -7.3% |
| **Thursday** | 1,434 | 508 | **35.4%** | **+12.6%** | +0.009% | -7.9% |
| Friday | 1,590 | 523 | 32.9% | +7.7% | +0.005% | -16.2% |
| Saturday | 1,383 | 392 | 28.3% --- | -2.1% | -0.002% | -14.9% |
| **Sunday** | 1,276 | 345 | **27.0%** --- | -4.0% | -0.003% | -6.7% |
| **TOTAL** | 10,778 | 3,343 | 31.0% | +26.0% | +0.002% | -23.4% |

**BEST:** Thursday (35.4% WR) | **WORST:** Sunday (27.0% WR) | **Spread:** 8.4 pp

#### MEAN_REVERSION by Day

| Day | Trades | Wins | WinRate | Total PnL | Avg PnL | MaxDD |
|-----|--------|------|---------|-----------|---------|-------|
| Monday | 1,007 | 402 | 39.9% +++ | +15.9% | +0.016% | -2.8% |
| **Tuesday** | 1,095 | 465 | **42.5%** +++ | **+21.1%** | +0.019% | -3.9% |
| Wednesday | 1,104 | 447 | 40.5% +++ | +18.3% | +0.017% | -3.6% |
| Thursday | 895 | 326 | 36.4% | +9.8% | +0.011% | -4.4% |
| **Friday** | 793 | 208 | **26.2%** --- | **-2.2%** | -0.003% | -7.4% |
| Saturday | 939 | 327 | 34.8% | +8.0% | +0.009% | -5.0% |
| Sunday | 1,041 | 426 | 40.9% +++ | +17.9% | +0.017% | -3.6% |
| **TOTAL** | 6,874 | 2,601 | 37.8% | +88.8% | +0.013% | -20.6% |

**BEST:** Tuesday (42.5% WR) | **WORST:** Friday (26.2% WR) | **Spread:** 16.2 pp

#### MOMENTUM_LS by Day

| Day | Trades | Wins | WinRate | Total PnL | Avg PnL | MaxDD |
|-----|--------|------|---------|-----------|---------|-------|
| Monday | 4,788 | 1,501 | 31.3% | +14.1% | +0.003% | -14.3% |
| Tuesday | 5,042 | 1,687 | 33.5% | +29.8% | +0.006% | -17.1% |
| **Wednesday** | 4,977 | 1,369 | **27.5%** --- | **-11.8%** | -0.002% | -34.3% !!! |
| Thursday | 4,580 | 1,608 | 35.1% | +37.9% | +0.008% | -12.5% |
| Friday | 4,668 | 1,596 | 34.2% | +32.1% | +0.007% | -14.6% |
| **Saturday** | 4,772 | 1,733 | **36.3%** | **+46.8%** | +0.010% | -13.2% |
| Sunday | 4,829 | 1,692 | 35.0% | +39.1% | +0.008% | -23.7% |
| **TOTAL** | 33,656 | 11,186 | 33.2% | +188.0% | +0.006% | -56.0% |

**BEST:** Saturday (36.3% WR) | **WORST:** Wednesday (27.5% WR) | **Spread:** 8.8 pp

### PIVOT: Total PnL% by Day x Strategy

| Day | ls_fade | momentum | reversal | mean_rev | momentum_ls | TOTAL |
|-----|---------|----------|----------|----------|-------------|-------|
| Monday | +62.7% | -8.9% | -0.4% | +15.9% | +14.1% | +83.4% |
| Tuesday | +77.1% | +6.9% | +3.7% | +21.1% | +29.8% | +138.6% |
| Wednesday | +42.7% | -26.8% | +8.5% | +18.3% | -11.8% | +30.9% |
| Thursday | +64.1% | +36.3% | +12.6% | +9.8% | +37.9% | +160.7% |
| Friday | +40.7% | +58.7% | +7.7% | -2.2% | +32.1% | +137.0% |
| Saturday | +58.5% | +50.2% | -2.1% | +8.0% | +46.8% | +161.4% |
| Sunday | +68.7% | +12.6% | -4.0% | +17.9% | +39.1% | +134.3% |
| **TOTAL** | +414.5% | +129.0% | +26.0% | +88.8% | +188.0% | +846.3% |

### Key Insights - Day of Week

1. **Wednesday is worst for momentum strategies** - momentum (-27% PnL, -47% DD), momentum_ls (-12% PnL, -34% DD)
2. **Thursday/Saturday are best overall** - 35% WR across all strategies
3. **mean_reversion has huge day variance** - 16pp spread (Tuesday 42% vs Friday 26%)
4. **Friday bad for mean_reversion** - only 26% WR (avoid or reduce size)
5. **Sunday bad for reversal** - only 27% WR

### Summary Tables

#### WinRate% PIVOT by Day x Strategy

| Day | ls_fade | momentum | reversal | mean_rev | mom_ls | AVG |
|-----|---------|----------|----------|----------|--------|-----|
| Mon | 35.7% | **28.6%** --- | 29.2% --- | 39.9% +++ | 31.3% | 32.9% |
| **Tue** | **37.0%** | 30.1% | 30.9% | **42.5%** +++ | 33.5% | **34.8%** |
| **Wed** | 33.4% | **26.9%** --- | 32.5% | 40.5% +++ | **27.5%** --- | 32.2% |
| **Thu** | 35.7% | 33.2% | **35.4%** | 36.4% | 35.1% | **35.2%** |
| Fri | 33.2% | **35.3%** | 32.9% | **26.2%** --- | 34.2% | 32.4% |
| **Sat** | 35.0% | 34.4% | 28.3% --- | 34.8% | **36.3%** | 33.8% |
| Sun | 36.2% | 30.7% | **27.0%** --- | 40.9% +++ | 35.0% | 34.0% |

#### Total PnL% PIVOT by Day x Strategy

| Day | ls_fade | momentum | reversal | mean_rev | mom_ls | TOTAL |
|-----|---------|----------|----------|----------|--------|-------|
| Mon | +62.7% | **-8.9%** | -0.4% | +15.9% | +14.1% | +83.4% |
| Tue | **+77.1%** | +6.9% | +3.7% | **+21.1%** | +29.8% | +138.6% |
| **Wed** | +42.7% | **-26.8%** | +8.5% | +18.3% | **-11.8%** | **+30.9%** |
| **Thu** | +64.1% | +36.3% | **+12.6%** | +9.8% | +37.9% | **+160.7%** |
| Fri | +40.7% | **+58.7%** | +7.7% | **-2.2%** | +32.1% | +137.0% |
| **Sat** | +58.5% | +50.2% | -2.1% | +8.0% | **+46.8%** | **+161.4%** |
| Sun | +68.7% | +12.6% | -4.0% | +17.9% | +39.1% | +134.3% |

#### Best & Worst Days per Strategy

| Strategy | BEST Day | WR% | WORST Day | WR% | Spread |
|----------|----------|-----|-----------|-----|--------|
| ls_fade | Tuesday | 37.0% | Friday | 33.2% | 3.8pp |
| momentum | Friday | 35.3% | **Wednesday** | 26.9% | 8.5pp |
| reversal | Thursday | 35.4% | Sunday | 27.0% | 8.4pp |
| **mean_reversion** | **Tuesday** | **42.5%** | **Friday** | **26.2%** | **16.2pp** |
| momentum_ls | Saturday | 36.3% | Wednesday | 27.5% | 8.8pp |

### Critical Day Zones

| Day | Strategy | WR% | PnL% | MaxDD | Action |
|-----|----------|-----|------|-------|--------|
| **Wednesday** | **momentum** | 26.9% | -26.8% | **-47.2%** | DO NOT TRADE |
| Wednesday | momentum_ls | 27.5% | -11.8% | -34.3% | Dynamic Size |
| Monday | momentum | 28.6% | -8.9% | -34.8% | Dynamic Size |
| Sunday | momentum | 30.7% | +12.6% | -31.8% | Dynamic Size |
| **Friday** | **mean_reversion** | 26.2% | -2.2% | -7.4% | DO NOT TRADE |

### Day of Week Trading Calendar

| Day | ls_fade | momentum | reversal | mean_rev | momentum_ls |
|-----|---------|----------|----------|----------|-------------|
| Monday | YES | Dynamic | YES | YES | YES |
| Tuesday | **YES** | YES | YES | **YES** | YES |
| **Wednesday** | YES | **NO** | YES | YES | Dynamic |
| Thursday | YES | YES | **YES** | YES | YES |
| Friday | YES | **YES** | YES | **NO** | YES |
| Saturday | YES | YES | Dynamic | YES | **YES** |
| Sunday | YES | Dynamic | **NO** | YES | YES |

---

## 5. Strategy Inversion Pattern (Month)

**Key Discovery:** Momentum and Reversal strategies have INVERSE seasonality!

```
                  MOMENTUM strategies        REVERSAL strategies
                  (ls_fade, mom, mom_ls)     (reversal, mean_rev)
-----------------------------------------------------------------
May/Aug           18-32% WR (BAD)            43-55% WR (EXCELLENT)
Jun/Jul           37-48% WR (EXCELLENT)      18-29% WR (BAD)
```

**Implication:** When momentum strategies lose, reversal strategies profit (potential hedging).

---

## 6. Critical Zones (Month) (MaxDD > 40%)

| Month | Strategy | WR% | PnL% | MaxDD | Recommendation |
|-------|----------|-----|------|-------|----------------|
| Feb | ls_fade | 24% | -29% | -67.8% | DO NOT TRADE |
| May | momentum | 18% | -56% | -61.7% | DO NOT TRADE |
| Aug | momentum | 22% | -43% | -67.9% | DO NOT TRADE |
| Sep | ls_fade | 25% | -20% | -47.7% | DO NOT TRADE |
| Nov | ls_fade | 27% | -10% | -45.4% | Dynamic Size |
| May | momentum_ls | 18% | -34% | -40.4% | DO NOT TRADE |
| Aug | momentum_ls | 24% | -21% | -45.9% | DO NOT TRADE |

---

## 7. Golden Zones (WR>40%, PnL>+50%, MaxDD<-20%)

| Month | Strategy | WR% | PnL% | MaxDD | Recommendation |
|-------|----------|-----|------|-------|----------------|
| Jun | ls_fade | 48% | +106% | -13% | MAX ALLOCATION |
| Jun | momentum_ls | 45% | +75% | -13% | MAX ALLOCATION |
| Jun | momentum | 41% | +63% | -16% | HIGH ALLOCATION |
| Jan | ls_fade | 43% | +105% | -27% | HIGH ALLOCATION |
| Jul | momentum | 40% | +62% | -14% | HIGH ALLOCATION |
| Nov | momentum | 41% | +72% | -17% | HIGH ALLOCATION |

---

## 8. Loss Clustering Analysis

**Finding:** Losses cluster together - after 1 LOSS, 82.5% probability next trade is LOSS.

| Previous Result | P(Next=LOSS) | Sample Size |
|-----------------|--------------|-------------|
| After WIN | 32% | ~50,000 |
| After 1 LOSS | 82.5% | ~100,000 |
| After 2 LOSS | 85%+ | ~80,000 |

### Skip-After-LOSS Rule Impact

| Metric | Without Rule | With Rule | Improvement |
|--------|--------------|-----------|-------------|
| Total PnL | +846% | +2,059% | +143% |
| MaxDD | -127% | -17% | -86% |
| Trades | 149,653 | ~50,000 | -67% |

---

## 9. Final Trading Logic (Verified 2026-03-03)

**Data:** 149,653 trades | 2024-01-09 → 2026-02-27

### BLACKLIST: OFF полностью

**По месяцам (7 комбинаций, MaxDD < -40%):**

| Month | Strategy | WR% | PnL% | MaxDD% |
|-------|----------|-----|------|--------|
| Aug | momentum | 21.7% | -43% | **-67.9%** |
| Feb | ls_fade | 24.3% | -29% | **-67.8%** |
| May | momentum | 18.4% | -56% | **-61.7%** |
| Sep | ls_fade | 24.8% | -20% | **-47.7%** |
| Aug | momentum_ls | 23.8% | -21% | **-45.9%** |
| Nov | ls_fade | 26.9% | -10% | **-45.4%** |
| May | momentum_ls | 18.2% | -34% | **-40.4%** |

**По дням (1 комбинация, MaxDD < -40%):**

| Day | Strategy | WR% | PnL% | MaxDD% |
|-----|----------|-----|------|--------|
| Wed | momentum | 26.9% | -27% | **-47.2%** |

### DYNAMIC SIZE ($1): Пониженный риск

**По месяцам (WR <30%, MaxDD -20% to -40%):**

| Month | Strategy | WR% | MaxDD% |
|-------|----------|-----|--------|
| Oct | momentum | 24.7% | -37.9% |
| Sep | momentum | 24.5% | -31.4% |
| Sep | momentum_ls | 24.1% | -21.3% |
| Oct | momentum_ls | 27.9% | -22.0% |
| Jun | reversal | 18.3% | -12.2% |
| Oct | reversal | 19.1% | -18.7% |
| Feb | reversal | 24.0% | -9.3% |
| Dec | reversal | 22.9% | -12.6% |
| Jul | mean_rev | 26.3% | -12.9% |
| Nov | mean_rev | 27.1% | -15.1% |

**По дням (WR <30%, MaxDD -30% to -40%):**

| Day | Strategy | WR% | MaxDD% |
|-----|----------|-----|--------|
| Mon | momentum | 28.6% | -34.8% |
| Wed | momentum_ls | 27.5% | -34.3% |
| Sun | momentum | 30.7% | -31.8% |
| Fri | mean_rev | 26.2% | -7.4% |
| Sun | reversal | 27.0% | -6.7% |
| Sat | reversal | 28.3% | -14.9% |

### GOLDEN ZONES: Максимальная аллокация

| Month | Strategy | WR% | PnL% | MaxDD% |
|-------|----------|-----|------|--------|
| Jun | ls_fade | **48.0%** | **+106%** | -13% |
| Jun | momentum_ls | 44.7% | +75% | -13% |
| Nov | momentum | 41.1% | +72% | -17% |
| Jan | momentum_ls | 41.7% | +69% | -18% |
| Jun | momentum | 40.7% | +63% | -16% |
| Jul | momentum | 40.1% | +62% | -14% |

### Месячный фильтр (Level 1)

| Month | ls_fade | momentum | reversal | mean_rev | momentum_ls |
|-------|:-------:|:--------:|:--------:|:--------:|:-----------:|
| Jan | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Feb** | ❌ | ✅ | ⚠️ | ✅ | ✅ |
| Mar | ✅ | ✅ | ✅ | ✅ | ✅ |
| Apr | ✅ | ✅ | ✅ | ✅ | ✅ |
| **May** | ✅ | ❌ | ✅ | ✅ | ❌ |
| Jun | ✅ | ✅ | ⚠️ | ✅ | ✅ |
| Jul | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| **Aug** | ✅ | ❌ | ✅ | ✅ | ❌ |
| **Sep** | ❌ | ⚠️ | ✅ | ✅ | ⚠️ |
| Oct | ✅ | ⚠️ | ⚠️ | ✅ | ⚠️ |
| **Nov** | ❌ | ✅ | ✅ | ⚠️ | ✅ |
| Dec | ✅ | ✅ | ⚠️ | ✅ | ✅ |

### Дневной фильтр (Level 2)

| Day | ls_fade | momentum | reversal | mean_rev | momentum_ls |
|-----|:-------:|:--------:|:--------:|:--------:|:-----------:|
| Mon | ✅ | ⚠️ | ✅ | ✅ | ✅ |
| Tue | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Wed** | ✅ | ❌ | ✅ | ✅ | ⚠️ |
| Thu | ✅ | ✅ | ✅ | ✅ | ✅ |
| Fri | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| Sat | ✅ | ✅ | ⚠️ | ✅ | ✅ |
| Sun | ✅ | ⚠️ | ⚠️ | ✅ | ✅ |

**Легенда:** ✅ = $100 | ⚠️ = $1 (Dynamic) | ❌ = OFF

### Сводка BLACKLIST для кода

```
MONTH_OFF = {
    'ls_fade':     [2, 9, 11],      # Feb, Sep, Nov
    'momentum':    [5, 8],          # May, Aug
    'momentum_ls': [5, 8],          # May, Aug
}

DAY_OFF = {
    'momentum':    [2],             # Wednesday (0=Mon)
}

MONTH_DYNAMIC = {
    'momentum':    [9, 10],         # Sep, Oct
    'momentum_ls': [9, 10],         # Sep, Oct
    'reversal':    [2, 6, 10, 12],  # Feb, Jun, Oct, Dec
    'mean_rev':    [7, 11],         # Jul, Nov
}

DAY_DYNAMIC = {
    'momentum':    [0, 6],          # Mon, Sun
    'momentum_ls': [2],             # Wed
    'reversal':    [5, 6],          # Sat, Sun
    'mean_rev':    [4],             # Fri
}
```

### Risk Management Rules

1. **Month Filter:** Check MONTH_OFF first → skip trade entirely
2. **Day Filter:** Check DAY_OFF second → skip trade entirely
3. **Dynamic Size:** If in MONTH_DYNAMIC or DAY_DYNAMIC → trade with $1 instead of $100
4. **Skip-After-LOSS:** After LOSS, skip until next WIN (reduces MaxDD by 86%)
5. **Strategy Rotation:** When momentum OFF (May/Aug), reversal/mean_rev are STRONG

---

## 10. COIN REGIME Analysis (2026-03-04)

**Data:** 53,420 trades with 14d lookback regime detection

### Coin Regime Definition

Режим монеты определяется по изменению цены за последние 14 дней:

| Change % | Regime |
|----------|--------|
| > +20% | STRONG_BULL |
| +5% to +20% | BULL |
| -5% to +5% | SIDEWAYS |
| -20% to -5% | BEAR |
| < -20% | STRONG_BEAR |

### BTC REGIME x STRATEGY Results

| BTC Regime | ls_fade | momentum | reversal | mean_rev | mom_ls |
|------------|:-------:|:--------:|:--------:|:--------:|:------:|
| STRONG_BULL | 19% -1095% | **37% +1665%** | 10% | 29% | 24% |
| BULL | **33% +5328%** | 32% +4318% | 22% | 30% | **33% +2410%** |
| SIDEWAYS | 32% +8212% | 30% | 26% | **39% +1950%** | **33% +6119%** |
| BEAR | 19% -1340% | 32% +1941% | 30% | 26% | **34% +2240%** |
| STRONG_BEAR | 26% | 29% | 5% | **40% +151%** | 30% |

### COIN REGIME x STRATEGY Results (MORE IMPORTANT!)

| Coin Regime | ls_fade | momentum | reversal | mean_rev | mom_ls |
|-------------|:-------:|:--------:|:--------:|:--------:|:------:|
| STRONG_BULL | 20% -1870% | **41% +4625%** | 20% | 23% | 31% |
| BULL | 30% | 26% | 29% | **53% +1922%** | 23% |
| SIDEWAYS | **33% +2056%** | 24% | 32% | **69% +686%** | 29% |
| BEAR | **37% +4269%** | **34% +2819%** | 23% | **46% +300%** | **37% +3833%** |
| STRONG_BEAR | **40% +2322%** | **37% +2714%** | 11% | 25% | **39% +2853%** |

### COIN_REGIME_MATRIX (For Live Trading)

```python
COIN_REGIME_MATRIX = {
    'STRONG_BULL': {
        'ls_fade': 'OFF',        # 20% WR, -1870% PnL
        'momentum': 'FULL',      # 41% WR, +4625% PnL ✓
        'reversal': 'OFF',       # 20% WR
        'mean_reversion': 'OFF', # 23% WR
        'momentum_ls': 'DYN',    # 31% WR
    },
    'BULL': {
        'ls_fade': 'DYN',        # 30% WR
        'momentum': 'DYN',       # 26% WR
        'reversal': 'DYN',       # 29% WR
        'mean_reversion': 'FULL',# 53% WR, +1922% PnL ✓✓
        'momentum_ls': 'OFF',    # 23% WR
    },
    'SIDEWAYS': {
        'ls_fade': 'FULL',       # 33% WR, +2056% PnL
        'momentum': 'OFF',       # 24% WR
        'reversal': 'DYN',       # 32% WR
        'mean_reversion': 'FULL',# 69% WR, +686% PnL ✓✓✓
        'momentum_ls': 'DYN',    # 29% WR
    },
    'BEAR': {
        'ls_fade': 'FULL',       # 37% WR, +4269% PnL ✓
        'momentum': 'FULL',      # 34% WR, +2819% PnL ✓
        'reversal': 'OFF',       # 23% WR
        'mean_reversion': 'FULL',# 46% WR, +300% PnL ✓
        'momentum_ls': 'FULL',   # 37% WR, +3833% PnL ✓
    },
    'STRONG_BEAR': {
        'ls_fade': 'FULL',       # 40% WR, +2322% PnL ✓✓
        'momentum': 'FULL',      # 37% WR, +2714% PnL ✓
        'reversal': 'OFF',       # 11% WR
        'mean_reversion': 'DYN', # 25% WR
        'momentum_ls': 'FULL',   # 39% WR, +2853% PnL ✓✓
    },
}
```

**Legend:** FULL = $100 | DYN = $1 (dynamic) | OFF = skip

---

## 11. WIN-after-WIN Analysis in DYN Zone (2026-03-04)

**Question:** Does the "WIN leads to WIN" pattern work inside DYN zone?

**Data:** 11,069 transitions in DYN zone

### Results

| After | WIN | LOSS | WIN Rate |
|-------|-----|------|----------|
| **After WIN** | 1,950 | 1,297 | **60.1%** |
| After LOSS | 1,436 | 6,386 | 18.4% |

### By Strategy in DYN Zone

| Strategy | After WIN → WIN% | Sample |
|----------|------------------|--------|
| **ls_fade** | **67.7%** | 940 |
| **momentum** | **60.3%** | 960 |
| momentum_ls | 56.6% | 829 |
| reversal | 51.4% | 502 |
| mean_reversion | 50.0% | 16 |

### Conclusion

**YES! Pattern works in DYN zone.**

After WIN in DYN zone, next trade is WIN with 60.1% probability (vs 18.4% after LOSS).

### DYN Zone Dynamic Sizing Logic

```
DYN zone entry:
  - First trade or after LOSS → $1
  - After WIN → $100 (60% chance of another WIN!)
  - After WIN then WIN → continue $100
  - After LOSS → back to $1
```

**Especially effective for:**
- ls_fade: 67.7% WIN after WIN
- momentum: 60.3% WIN after WIN

---

## 12. Complete Live Trading Logic (2026-03-04)

### Filter Order (Sequential)

1. **Month Filter** → skip if month historically bad
2. **Day Filter** → skip if day of week historically bad
3. **Coin Regime Filter** → apply COIN_REGIME_MATRIX
4. **Monthly DD Limit** → skip if monthly limit hit
5. **Daily DD Limit** → skip if daily limit hit
6. **Liquidity Check** → skip if volume too low
7. **Position Check** → skip if position already open

### Coin Regime Actions

| Action | Behavior |
|--------|----------|
| OFF | Skip trade completely |
| FULL | Trade with $100 |
| DYN | Start with $1, after WIN → $100, after LOSS → $1 |

### CLI Usage

```bash
# Enable coin regime filter
python run_all.py --start 2024-01-01 --end 2025-01-31 --coin-regime

# With custom lookback (default 14 days)
python run_all.py --start 2024-01-01 --end 2025-01-31 --coin-regime --coin-regime-lookback 7
```

---

*Last verified: 2026-03-04 | Data: 167,386 trades | Period: 2024-01-09 to 2026-02-27*
