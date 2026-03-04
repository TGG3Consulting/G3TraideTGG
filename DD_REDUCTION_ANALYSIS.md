# MaxDD Reduction Analysis

**Цель:** Снизить Maximum Drawdown
**Дата начала:** 2026-03-03
**Данные:** G:\BinanceFriend\outputNEWARCH

---

## Эксперименты

### Experiment 1: TOP-20 Major Coins (EXCLUDED coins test)

**Команда:**
```
py -3.12 run_all.py --start 2020-01-01 --end 2026-02-28 --daily-max-dd 100 --monthly-max-dd 30 --dynamic-size --symbols BTCUSDT,ETHUSDT,XRPUSDT,ADAUSDT,BNBUSDT,LINKUSDT,BCHUSDT,ETCUSDT,DOGEUSDT,DOTUSDT,SOLUSDT,AVAXUSDT,VETUSDT,EOSUSDT,THETAUSDT,NEOUSDT,FILUSDT,AAVEUSDT,MKRUSDT,COMPUSDT
```

**Параметры:**
- Период: 2020-01-01 → 2026-02-28 (2250 дней)
- Монеты: 20 (EXCLUDED из validated_symbols.json)
- Dynamic Size: ✅ ON
- Month Filter: ❌ OFF
- Day Filter: ❌ OFF
- Monthly MaxDD: 30%

**Результаты:**
| Strategy | Sigs | Trades | WR% | PnL | MaxDD | Calmar |
|----------|------|--------|-----|-----|-------|--------|
| mean_reversion | 3,576 | 144 | 36.8% | **+199.9%** | 30.3% | 6.60 |
| ls_fade | 28,717 | 85 | 31.8% | +1.0% | 38.0% | 0.03 |
| momentum_ls | 15,642 | 76 | 25.0% | -6.9% | 37.7% | -0.18 |
| momentum | 26,067 | 34 | 23.5% | -8.1% | 39.2% | -0.21 |
| reversal | 5,495 | 8 | 0.0% | -19.7% | 19.7% | -1.00 |

**Skip Stats:**
- Position open: 298
- **Monthly MaxDD limit: 78,852** (!)

**Анализ:**
1. **78,852 сигналов пропущено** из-за monthly MaxDD limit 30% → лимит слишком строгий
2. **reversal полностью провалился** - 0% WR, все 8 сделок в минус
3. **mean_reversion единственная прибыльная** на этих монетах
4. **Это EXCLUDED монеты** (BTC, ETH, SOL...) - подтверждает правильность их исключения
5. **SHORT доминирует** в mean_reversion (+187% SHORT vs +13% LONG)

**Вывод:**
- Excluded монеты плохо работают с нашими стратегиями (кроме mean_reversion)
- Monthly MaxDD 30% слишком строгий - блокирует 78K+ сигналов
- Нужно тестировать на validated_symbols, не на excluded

---

### Experiment 2: Validated Symbols Batch 1 (10 coins)

**Команда:**
```
py -3.12 run_all.py --start 2020-01-01 --end 2026-02-28 --daily-max-dd 100 --monthly-max-dd 30 --dynamic-size --symbols ENJUSDT,IOSTUSDT,COMPUSDT,DASHUSDT,CRVUSDT,SNXUSDT,QTUMUSDT,BATUSDT,XTZUSDT,XMRUSDT
```

**Параметры:**
- Период: 2020-01-01 → 2026-02-28 (2250 дней)
- Монеты: 10 (validated symbols)
- Dynamic Size: ✅ ON
- Monthly MaxDD: 30%

**Результаты:**
| Strategy | Sigs | Trades | WR% | PnL | MaxDD | Calmar |
|----------|------|--------|-----|-----|-------|--------|
| mean_reversion | 1,963 | 74 | 33.8% | **+43.2%** | 20.1% | 2.15 |
| reversal | 2,799 | 85 | 32.9% | **+42.9%** | 32.1% | 1.34 |
| ls_fade | 13,543 | 50 | 28.0% | **+31.0%** | 23.5% | 1.32 |
| momentum | 13,478 | 10 | 10.0% | -6.7% | 15.9% | -0.42 |
| momentum_ls | 8,107 | 11 | 9.1% | -24.7% | 24.7% | -1.00 |

**Skip Stats:**
- Position open: 100
- Monthly MaxDD limit: 39,560

**Анализ:**
1. **3/5 стратегий прибыльны** (vs 2/5 на excluded coins)
2. **MaxDD контролируемый**: 20-32% (лучше чем Exp 1)
3. **momentum стратегии провалились** - мало сделок (10-11), низкий WR
4. **SHORT доминирует** во всех прибыльных стратегиях
5. Validated symbols работают лучше excluded

---

### Experiment 3: Day Filter Only

**Команда:**
```
py -3.12 run_all.py --start 2020-01-01 --end 2026-02-28 --daily-max-dd 100 --monthly-max-dd 30 --dynamic-size --day-filter --symbols ENJUSDT,IOSTUSDT,COMPUSDT,DASHUSDT,CRVUSDT,SNXUSDT,QTUMUSDT,BATUSDT,XTZUSDT,XMRUSDT
```

**Параметры:**
- Монеты: 10 (те же что Exp 2)
- Dynamic Size: ✅ ON
- Month Filter: ❌ OFF
- Day Filter: ✅ ON
- Monthly MaxDD: 30%

**Результаты:**
| Strategy | Sigs | Trades | WR% | PnL | MaxDD | Calmar |
|----------|------|--------|-----|-----|-------|--------|
| ls_fade | 13,543 | 50 | 28.0% | +31.0% | 23.5% | 1.32 |
| mean_reversion | 1,963 | 74 | 33.8% | +30.4% | 20.1% | 1.51 |
| reversal | 2,799 | 85 | 32.9% | -0.1% | 23.1% | -0.01 |
| momentum | 13,478 | 10 | 10.0% | -4.8% | 14.1% | -0.34 |
| momentum_ls | 8,107 | 11 | 9.1% | -20.2% | 20.2% | -1.00 |

**Skip Stats:**
- Day filter: 1,949
- Position open: 96
- Monthly MaxDD limit: 37,615

**Анализ:**
1. **mean_reversion УХУДШИЛСЯ**: +43.2% → +30.4% (-12.8%)
2. **reversal ПРОВАЛИЛСЯ**: +42.9% → -0.1% (-43%)
3. **momentum УЛУЧШИЛСЯ**: -6.7% → -4.8% (+1.9%)
4. **momentum_ls УЛУЧШИЛСЯ**: -24.7% → -20.2% (+4.5%)

**Вывод:** Day Filter помогает momentum стратегиям, но ВРЕДИТ mean_reversion и reversal!

---

### Experiment 4: Month + Day Filters

**Команда:**
```
py -3.12 run_all.py --start 2020-01-01 --end 2026-02-28 --daily-max-dd 100 --monthly-max-dd 30 --dynamic-size --month-filter --day-filter --symbols ENJUSDT,IOSTUSDT,COMPUSDT,DASHUSDT,CRVUSDT,SNXUSDT,QTUMUSDT,BATUSDT,XTZUSDT,XMRUSDT
```

**Параметры:**
- Монеты: 10 (те же что Exp 2-3)
- Dynamic Size: ✅ ON
- Month Filter: ✅ ON
- Day Filter: ✅ ON
- Monthly MaxDD: 30%

**Результаты:**
| Strategy | Sigs | Trades | WR% | PnL | MaxDD | Calmar |
|----------|------|--------|-----|-----|-------|--------|
| mean_reversion | 1,963 | 74 | 33.8% | **+47.8%** | **9.9%** | 4.81 |
| ls_fade | 13,543 | 50 | 28.0% | +31.0% | 23.5% | 1.32 |
| reversal | 2,799 | 85 | 32.9% | +1.9% | 20.8% | 0.09 |
| momentum | 13,478 | 10 | 10.0% | -4.8% | 14.1% | -0.34 |
| momentum_ls | 8,107 | 11 | 9.1% | -20.2% | 20.2% | -1.00 |

**Skip Stats:**
- Month filter: 6,825
- Day filter: 1,637
- Position open: 96
- Monthly MaxDD limit: 31,102

**Анализ:**
1. **mean_reversion УЛУЧШИЛСЯ**: MaxDD снизился с 20.1% до **9.9%** (!), PnL вырос до +47.8%
2. **reversal всё ещё плохо**: +42.9% → +1.9%
3. Month filter компенсирует негативный эффект day filter для mean_reversion

---

### Experiment 5: Month Filter Only

**Команда:**
```
py -3.12 run_all.py --start 2020-01-01 --end 2026-02-28 --daily-max-dd 100 --monthly-max-dd 30 --dynamic-size --month-filter --symbols ENJUSDT,IOSTUSDT,COMPUSDT,DASHUSDT,CRVUSDT,SNXUSDT,QTUMUSDT,BATUSDT,XTZUSDT,XMRUSDT
```

**Параметры:**
- Монеты: 10 (те же что Exp 2-4)
- Dynamic Size: ✅ ON
- Month Filter: ✅ ON
- Day Filter: ❌ OFF
- Monthly MaxDD: 30%

**Результаты:**
| Strategy | Sigs | Trades | WR% | PnL | MaxDD | Calmar |
|----------|------|--------|-----|-----|-------|--------|
| mean_reversion | 1,963 | 74 | 33.8% | **+60.5%** | **9.9%** | **6.09** |
| ls_fade | 13,543 | 50 | 28.0% | +31.0% | 23.5% | 1.32 |
| reversal | 2,799 | 85 | 32.9% | +29.4% | 32.4% | 0.91 |
| momentum | 13,478 | 10 | 10.0% | -6.7% | 15.9% | -0.42 |
| momentum_ls | 8,107 | 11 | 9.1% | -24.7% | 24.7% | -1.00 |

**Skip Stats:**
- Month filter: 6,825
- Position open: 100
- Monthly MaxDD limit: 32,735

**Анализ:**
1. **mean_reversion ЛУЧШИЙ РЕЗУЛЬТАТ**: +60.5% PnL, 9.9% MaxDD, Calmar 6.09
2. **reversal восстановился**: +29.4% (лучше чем с day filter)
3. **momentum вернулся к baseline**: -6.7% (day filter ему нужен)

---

## Сводный анализ экспериментов

### Главная таблица: PnL и MaxDD (Monthly Limit 30%)

| Strategy | Exp2 (none) | | Exp3 (day) | | Exp4 (m+d) | | Exp5 (month) | |
|----------|-------------|------|------------|------|------------|------|--------------|------|
| | **PnL** | **DD** | **PnL** | **DD** | **PnL** | **DD** | **PnL** | **DD** |
| mean_reversion | +43.2% | 20.1% | +30.4% | 20.1% | +47.8% | **9.9%** | **+60.5%** | **9.9%** |
| reversal | **+42.9%** | 32.1% | -0.1% | 23.1% | +1.9% | **20.8%** | +29.4% | 32.4% |
| ls_fade | +31.0% | 23.5% | +31.0% | 23.5% | +31.0% | 23.5% | +31.0% | 23.5% |
| momentum | -6.7% | 15.9% | **-4.8%** | **14.1%** | **-4.8%** | **14.1%** | -6.7% | 15.9% |
| momentum_ls | -24.7% | 24.7% | **-20.2%** | **20.2%** | **-20.2%** | **20.2%** | -24.7% | 24.7% |
| **TOTAL PnL** | **+85.7%** | | **+36.3%** | | **+55.7%** | | **+89.5%** | |

---

### Детальная таблица

| Strategy | Exp2 (none) | Exp3 (day) | Exp4 (m+d) | Exp5 (month) | BEST |
|----------|-------------|------------|------------|--------------|------|
| **mean_reversion** |||||
| └ PnL | +43.2% | +30.4% | +47.8% | **+60.5%** | Exp5 |
| └ MaxDD | 20.1% | 20.1% | **9.9%** | **9.9%** | Exp4/5 |
| └ Calmar | 2.15 | 1.51 | 4.81 | **6.09** | Exp5 |
| **reversal** |||||
| └ PnL | **+42.9%** | -0.1% | +1.9% | +29.4% | Exp2 |
| └ MaxDD | 32.1% | 23.1% | **20.8%** | 32.4% | Exp4 |
| └ Calmar | **1.34** | -0.01 | 0.09 | 0.91 | Exp2 |
| **ls_fade** |||||
| └ PnL | +31.0% | +31.0% | +31.0% | +31.0% | = |
| └ MaxDD | 23.5% | 23.5% | 23.5% | 23.5% | = |
| **momentum** |||||
| └ PnL | -6.7% | **-4.8%** | **-4.8%** | -6.7% | Exp3/4 |
| └ MaxDD | 15.9% | **14.1%** | **14.1%** | 15.9% | Exp3/4 |
| **momentum_ls** |||||
| └ PnL | -24.7% | **-20.2%** | **-20.2%** | -24.7% | Exp3/4 |
| └ MaxDD | 24.7% | **20.2%** | **20.2%** | 24.7% | Exp3/4 |

### Skip Statistics

| Config | Month Skip | Day Skip | Monthly Limit | Total Filtered |
|--------|------------|----------|---------------|----------------|
| Exp2 (none) | 0 | 0 | 39,560 | 0 |
| Exp3 (day) | 0 | 1,949 | 37,615 | 1,949 |
| Exp4 (m+d) | 6,825 | 1,637 | 31,102 | 8,462 |
| Exp5 (month) | 6,825 | 0 | 32,735 | 6,825 |

### Оптимальные настройки per-strategy

| Strategy | Month Filter | Day Filter | Best PnL | Best MaxDD |
|----------|--------------|------------|----------|------------|
| mean_reversion | ✅ ON | ❌ OFF | +60.5% | 9.9% |
| reversal | ❌ OFF | ❌ OFF | +42.9% | 32.1% |
| ls_fade | - | - | +31.0% | 23.5% |
| momentum | ❌ OFF | ✅ ON | -4.8% | 14.1% |
| momentum_ls | ❌ OFF | ✅ ON | -20.2% | 20.2% |

### Корневые причины

| Проблема | Причина |
|----------|---------|
| mean_reversion: day filter вредит | DAY_DYNAMIC[4] (пятница) - ошибка, пятница прибыльна |
| reversal: оба фильтра вредят | MONTH_DYNAMIC и DAY_DYNAMIC содержат прибыльные периоды |
| ls_fade: нет эффекта | Нет в DYNAMIC словарях |
| momentum: day filter помогает | DAY_OFF[2] (среда) и DAY_DYNAMIC[0,6] верны |

---

## Журнал изменений

| Дата | Действие | Результат |
|------|----------|-----------|
| 2026-03-03 | Exp 1: Excluded coins test | mean_rev +199%, остальные плохо |
| 2026-03-03 | Exp 2: Validated batch 1 | 3/5 profitable, MaxDD 20-32% |
| 2026-03-03 | Exp 3: Day filter only | momentum ✅, mean_rev/reversal ❌ |
| 2026-03-03 | Exp 4: Month+Day filters | mean_rev MaxDD 9.9% (!), reversal ❌ |
| 2026-03-03 | Exp 5: Month filter only | mean_rev +60.5% BEST, reversal +29.4% |
| 2026-03-03 | Исправлен код фильтров | MONTH_DYNAMIC/DAY_DYNAMIC теперь работают |

---

## Итоговые рекомендации

### 1. Немедленные изменения в коде

**Убрать из DAY_DYNAMIC:**
```python
# БЫЛО:
DAY_DYNAMIC = {
    'momentum': [0, 6],
    'momentum_ls': [2],
    'reversal': [5, 6],       # ❌ УБРАТЬ
    'mean_reversion': [4],    # ❌ УБРАТЬ
}

# ДОЛЖНО БЫТЬ:
DAY_DYNAMIC = {
    'momentum': [0, 6],
    'momentum_ls': [2],
}
```

**Убрать из MONTH_DYNAMIC:**
```python
# БЫЛО:
MONTH_DYNAMIC = {
    'momentum': [9, 10],
    'momentum_ls': [9, 10],
    'reversal': [2, 6, 10, 12],  # ❌ УБРАТЬ
    'mean_reversion': [7, 11],   # ✅ ОСТАВИТЬ
}

# ДОЛЖНО БЫТЬ:
MONTH_DYNAMIC = {
    'momentum': [9, 10],
    'momentum_ls': [9, 10],
    'mean_reversion': [7, 11],
}
```

### 2. Ожидаемый результат после исправления

| Strategy | Current Best | After Fix |
|----------|--------------|-----------|
| mean_reversion | +60.5% (month only) | +60.5% |
| reversal | +42.9% (no filters) | +42.9% |
| ls_fade | +31.0% | +31.0% |
| momentum | -4.8% (day filter) | -4.8% |
| momentum_ls | -20.2% (day filter) | -20.2% |
| **PORTFOLIO** | - | **+109.4%** |

### 3. Долгосрочные улучшения

1. **Per-strategy filter flags** - раздельные настройки для каждой стратегии
2. **Re-analyze MONTH_DYNAMIC** - перепроверить какие месяцы реально плохие
3. **Test on more coins** - валидировать на других наборах монет
