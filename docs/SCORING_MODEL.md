# СКОРИНГОВАЯ МОДЕЛЬ ПРИНЯТИЯ РЕШЕНИЙ ПО СИГНАЛУ

> Аудит кода: `signal_generator.py`, `accumulation_detector.py`, `risk_calculator.py`
> Дата: 2026-02-21

---

## 1. ВХОДНАЯ ТОЧКА: КАКИЕ СОБЫТИЯ ТРИГГЕРЯТ АНАЛИЗ

Детекция из мониторов попадает в `SignalGenerator.on_detection()`. Далее:

1. **Cooldown** — 1 час между сигналами на один символ. Если уже был сигнал — отказ.

2. **Trigger Detection** — если тип детекции есть в списке триггеров (ACTIVE_PUMP, VOLUME_SPIKE_EXTREME, COORDINATED_BUYING и т.д.) — запускается полный анализ с минимальным порогом 50 баллов.

3. **Non-trigger Detection** — для остальных детекций — проверяется накопление со стандартным порогом 65 баллов.

### Список Trigger Detections

| Detection Type | Signal Type |
|----------------|-------------|
| ACTIVE_PUMP | BREAKOUT |
| ACTIVE_DUMP | BREAKOUT |
| VOLUME_SPIKE_EXTREME | BREAKOUT |
| VOLUME_SPIKE_HIGH | ACCUMULATION |
| PRICE_VELOCITY_EXTREME | BREAKOUT |
| PRICE_VELOCITY_HIGH | BREAKOUT |
| COORDINATED_BUYING | ACCUMULATION |
| COORDINATED_SELLING | ACCUMULATION |
| ONE_SIDED_BUYING | ACCUMULATION |
| ONE_SIDED_SELLING | ACCUMULATION |
| ORDERBOOK_IMBALANCE | ACCUMULATION |
| FUTURES_ORDERBOOK_IMBALANCE | ACCUMULATION |
| FUTURES_WHALE_ACCUMULATION_CRITICAL | ACCUMULATION |
| FUTURES_WHALE_ACCUMULATION_STEALTH | ACCUMULATION |
| FUTURES_OI_SPIKE_HIGH | ACCUMULATION |
| FUTURES_OI_SPIKE | ACCUMULATION |
| FUTURES_MASS_EXIT_DETECTED | BREAKOUT |
| FUTURES_OI_DROP | BREAKOUT |
| FUTURES_EXTREME_SHORT_POSITIONING | SQUEEZE_SETUP |
| FUTURES_FUNDING_EXTREME_SHORT | SQUEEZE_SETUP |
| FUTURES_EXTREME_LONG_POSITIONING | SQUEEZE_SETUP |
| FUTURES_FUNDING_EXTREME_LONG | SQUEEZE_SETUP |
| FUTURES_WEAK_PUMP_DIVERGENCE | DIVERGENCE |
| FUTURES_WEAK_DUMP_DIVERGENCE | DIVERGENCE |
| FUTURES_FUNDING_GRADIENT_SPIKE | ACCUMULATION |
| FUTURES_FUNDING_GRADIENT_DROP | ACCUMULATION |
| CX-001_PRICE_DIVERGENCE | CROSS_EXCHANGE |
| CX-003_FUNDING_ARBITRAGE | CROSS_EXCHANGE |
| CX-004_OI_MIGRATION | CROSS_EXCHANGE |

---

## 2. СКОРИНГОВАЯ МОДЕЛЬ ACCUMULATION SCORE

**Итоговый скор = сумма всех факторов.** Минимальный порог для генерации сигнала: **65 баллов**.

### 2.1 OI Факторы

| Фактор | Баллы | Условие |
|--------|-------|---------|
| oi_growth | +20 | OI за 1h >= +15% |
| oi_growth | +5..15 | OI за 1h +3%..+15% (линейно) |
| oi_growth | +5 | OI за 1h >= +3% |
| oi_stability | +5 | OI растёт и за 1h И за 5m |

### 2.2 Funding Факторы

| Фактор | Баллы | Условие |
|--------|-------|---------|
| funding_cheap | +15 | funding <= -0.02% |
| funding_cheap | +10 | funding <= 0% |
| funding_cheap | +5 | funding <= +0.01% |
| extreme_funding_penalty | **-15** | funding >= +0.05% |
| extreme_funding_penalty | **-10** | funding >= +0.03% |
| funding_gradient | +10 | funding упал на 0.02%+ за 3 периода |
| funding_gradient | +5 | funding упал на 0.01%+ за 3 периода |

### 2.3 Crowd Sentiment (L/S Ratio)

| Фактор | Баллы | Условие |
|--------|-------|---------|
| crowd_bearish | +20 | short_pct >= 70% (contrarian LONG) |
| crowd_bearish | +15 | short_pct >= 55% |
| crowd_bearish | +5 | short_pct >= 50% |
| crowd_bullish | +20 | long_pct >= 70% (contrarian SHORT) |
| crowd_bullish | +15 | long_pct >= 60% |
| crowd_bullish | +10 | long_pct >= 55% |

> **Важно:** crowd_bearish и crowd_bullish взаимоисключающие.

### 2.4 Detection Факторы (из кэша за 30 мин)

| Фактор | Баллы | Условие |
|--------|-------|---------|
| coordinated_buying | +10 | COORDINATED_BUYING детекция |
| volume_accumulation | +10 | Volume spike + цена не падает (>= -0.5%) |
| volume_accumulation | +5 | Volume spike (любой) |
| wash_trading_penalty | **-25** | WASH_TRADING детекция |

### 2.5 Cross-Exchange Факторы

| Фактор | Баллы | Условие |
|--------|-------|---------|
| cross_oi_migration | +5 | OI на одной бирже >= 60% |
| cross_oi_migration | +3 | OI на одной бирже >= 50% |
| cross_price_lead | +5 | Есть price leader |

### 2.6 SPOT Orderbook Факторы

**Минимальный объём стакана для учёта: $1,000**

| Фактор | Баллы | Условие |
|--------|-------|---------|
| spot_bid_pressure | +10 | bid/ask ratio >= 2.0 |
| spot_bid_pressure | +5 | bid/ask ratio >= 1.5 |
| spot_bid_pressure | **-8** | bid/ask ratio < 0.5 |
| spot_ask_weakness | +5 | ask/bid ratio < 0.5 |
| spot_ask_weakness | +3 | ask/bid ratio < 0.7 |
| spot_imbalance_score | +5 | imbalance >= +0.4 |
| spot_imbalance_score | +3 | imbalance >= +0.2 |
| spot_imbalance_score | **-8** | imbalance <= -0.4 |

### 2.7 FUTURES Orderbook Факторы

**Минимальный объём стакана для учёта: $5,000**

| Фактор | Баллы | Условие |
|--------|-------|---------|
| futures_bid_pressure | +10 | bid/ask ratio >= 2.0 |
| futures_bid_pressure | +5 | bid/ask ratio >= 1.5 |
| futures_ask_weakness | +5 | ask/bid ratio < 0.5 |
| futures_ask_weakness | +3 | ask/bid ratio < 0.7 |
| futures_imbalance_score | +5 | imbalance >= +0.4 |
| futures_imbalance_score | +3 | imbalance >= +0.2 |
| futures_imbalance_score | **-8** | imbalance <= -0.4 |

### 2.8 Orderbook Divergence

| Фактор | Баллы | Условие |
|--------|-------|---------|
| orderbook_divergence | +5 | оба стакана имеют положительный imbalance > 0.2 |
| orderbook_divergence | 0 | один positive, другой negative (осторожность) |

### 2.9 Orderbook Penalty (применяется один раз per orderbook)

| Фактор | Баллы | Условие |
|--------|-------|---------|
| orderbook_against_penalty | до **-8** | SPOT медвежий (ratio<0.5 ИЛИ imbalance<=-0.4) |
| orderbook_against_penalty | до **-8** | FUTURES медвежий |

> **Важно:** penalty не суммируется внутри одного стакана — берётся max penalty.

---

## 3. ОПРЕДЕЛЕНИЕ НАПРАВЛЕНИЯ (LONG/SHORT)

Система подсчитывает `short_signals` и `long_signals`:

### 3.1 SHORT Signals

| Очки | Условие |
|------|---------|
| +1 | funding >= +0.03% |
| +1 | funding >= +0.03% И OI < -5% (combo) |
| +2 | long_pct >= 70% И OI < -5% (dump setup) |
| +2 | long_pct >= 65% |
| +1 | long_pct >= 60% |
| +2 | SPOT imbalance <= -0.4 |
| +2 | FUTURES imbalance <= -0.4 |
| +1 | orderbook_against_penalty <= -5 |
| +1 | crowd_bullish score >= 15 |

### 3.2 LONG Signals

| Очки | Условие |
|------|---------|
| +1 | funding < -0.01% |
| +2 | short_pct >= 55% (если SPOT стакан не медвежий) |
| +2 | SPOT imbalance >= +0.4 |
| +2 | FUTURES imbalance >= +0.4 |

### 3.3 Решение

```
if short_signals >= 2 AND short_signals > long_signals:
    direction = SHORT
else:
    direction = LONG  # default — накопление обычно перед пампом
```

---

## 4. CONFIDENCE И PROBABILITY

### 4.1 Confidence (уверенность по score)

| Уровень | Score |
|---------|-------|
| LOW | < 55 |
| MEDIUM | 55-69 |
| HIGH | 70-84 |
| VERY_HIGH | >= 85 |

### 4.2 Probability (вероятность успеха)

**Base probability по score:**

| Score | Base % |
|-------|--------|
| < 50 | 45% |
| 50-64 | 55% |
| 65-74 | 62% |
| 75-84 | 70% |
| 85-94 | 78% |
| 95+ | 85% |

**Adjustments для LONG:**

| Adjustment | Условие |
|------------|---------|
| +5% | OI за 5m > 0 (momentum) |
| +5% | funding < 0 (дешёвые лонги) |
| +5% | short_pct > 55% (contrarian) |

**Adjustments для SHORT:**

| Adjustment | Условие |
|------------|---------|
| +3% | OI за 5m < -2% (выход лонгов) |
| +5% | funding >= +0.05% (экстремальный) |
| +5% | long_pct > 60% (contrarian) |

**Orderbook Adjustments (для обоих направлений):**

| Adjustment | Условие |
|------------|---------|
| +5% | SPOT imbalance >= 0.4 |
| +3% | SPOT imbalance >= 0.2 |
| +5% | FUTURES imbalance >= 0.4 |
| +3% | FUTURES imbalance >= 0.2 |
| +3% | оба стакана согласны (confirmation) |
| **-5%** | стаканы divergence |
| +5% | orderbook_total score >= 20 |
| +3% | orderbook_total score >= 10 |

**Max probability ограничена confidence:**

| Confidence | Max Probability |
|------------|-----------------|
| LOW | 55% |
| MEDIUM | 70% |
| HIGH | 85% |
| VERY_HIGH | 95% |

---

## 5. БЛОКИРОВКИ СИГНАЛА

Сигнал **НЕ генерируется** если:

| Причина | Условие |
|---------|---------|
| Cooldown активен | был сигнал на этот символ < 1 часа назад |
| Score ниже порога | accumulation score < 65 (или < 50 для trigger) |
| Probability ниже порога | probability < 55% |
| LONG при падающем OI | direction = LONG, но OI за 1h < -1% |
| R:R ниже минимума | risk_reward_ratio < 2.0 |
| Нет данных накопления | accumulation = None |

---

## 6. РАСЧЁТ RISK LEVELS

### 6.1 Volatility (определяет SL)

**Приоритет источников:**

1. **Daily ATR** (для сигналов >= 4h) — `atr_daily_pct`
2. **Hourly ATR raw** (без clamp) — `atr_1h_pct_raw`
3. **Hourly ATR clamped** — `atr_1h_pct`
4. **Historical price range** — (high-low)/avg за 60 точек
5. **Default** — 5%

**Корректировки:**

| Корректировка | Условие |
|---------------|---------|
| volatility = max(volatility, price_change × 1.5) | ATR не реальный И price_change_1h > 5% |
| volatility × 1.2 | OI change > 10% |

**Финальный clamp:** `max(0.5%, min(20%, volatility))`

### 6.2 Stop Loss

```
SL = 1.5 × ATR
Clamp: min(8%, max(2%, SL))
```

| Direction | Formula |
|-----------|---------|
| LONG | stop_loss = entry × (1 - SL%) |
| SHORT | stop_loss = entry × (1 + SL%) |

### 6.3 Take Profits

**TP Multiplier зависит от accumulation_score:**

```
score_clamped = clamp(score, 65, 100)
tp_multiplier = 1.0 + (score_clamped - 65) / 35 × 0.5
```

| Score | Multiplier |
|-------|------------|
| 65 | 1.00 |
| 80 | 1.21 |
| 100 | 1.50 |

**Уровни TP:**

| Уровень | Множитель | Доля позиции |
|---------|-----------|--------------|
| TP1 | 1.2 × risk × multiplier | 35% |
| TP2 | 2.5 × risk × multiplier | 40% |
| TP3 | 4.0 × risk × multiplier | 25% |

### 6.4 Risk:Reward Ratio

```
risk = |entry - stop_loss|
weighted_reward = sum(|TP_price - entry| × portion%) для всех TP
R:R = weighted_reward / risk
```

**Минимум для генерации сигнала: R:R >= 2.0**

---

## 7. VALID HOURS (время жизни сигнала)

| Signal Type | Valid Hours |
|-------------|-------------|
| BREAKOUT | 4h |
| SQUEEZE_SETUP | 8h |
| ACCUMULATION | 24h |
| DIVERGENCE | 12h |
| CROSS_EXCHANGE | 6h |

> Valid hours влияет на выбор ATR (daily vs hourly) и передаётся в расчёт SL.

---

## 8. ИТОГОВАЯ ЛОГИКА ПРИНЯТИЯ РЕШЕНИЯ

```
┌─────────────────────────────────────────────────────────────┐
│                    Detection приходит                        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Cooldown check: был сигнал на символ < 1h назад?           │
│  ДА → EXIT                                                   │
└─────────────────────────────────────────────────────────────┘
                              │ НЕТ
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Добавить detection в кэш (для pattern analysis)            │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Detection в списке triggers?                                │
│  ДА → min_score = 50                                         │
│  НЕТ → min_score = 65                                        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Рассчитать accumulation score (все факторы)                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  score >= min_score?                                         │
│  НЕТ → EXIT                                                  │
└─────────────────────────────────────────────────────────────┘
                              │ ДА
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Определить direction (LONG/SHORT)                          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Direction = LONG И OI за 1h < -1%?                         │
│  ДА → EXIT (LONG невозможен при падающем OI)                │
└─────────────────────────────────────────────────────────────┘
                              │ НЕТ
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Рассчитать probability                                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  probability >= 55%?                                         │
│  НЕТ → EXIT                                                  │
└─────────────────────────────────────────────────────────────┘
                              │ ДА
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Рассчитать risk levels (volatility → SL → TP)              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  R:R >= 2.0?                                                 │
│  НЕТ → EXIT                                                  │
└─────────────────────────────────────────────────────────────┘
                              │ ДА
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  ✓ ГЕНЕРАЦИЯ СИГНАЛА                                        │
│  - Записать в cooldown cache                                 │
│  - Вернуть TradeSignal                                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 9. ПРИМЕРЫ РАСЧЁТА

### Пример 1: Сильный LONG сигнал

**Входные данные:**
- OI за 1h: +12%
- Funding: -0.015%
- Short ratio: 62%
- SPOT bid/ask: $45,000 / $18,000 (ratio 2.5)
- SPOT imbalance: +0.43
- FUTURES imbalance: +0.35

**Расчёт score:**
- oi_growth: +12 (OI +12%)
- oi_stability: +5 (растёт)
- funding_cheap: +15 (funding <= -0.02%)
- crowd_bearish: +15 (short >= 55%)
- spot_bid_pressure: +10 (ratio >= 2.0)
- spot_imbalance_score: +5 (>= 0.4)
- futures_imbalance_score: +3 (>= 0.2)
- orderbook_divergence: +5 (оба positive > 0.2)

**Итого: 70 баллов** → Confidence: HIGH

**Direction:**
- long_signals: +1 (funding) + 2 (crowd) + 2 (SPOT imb) + 2 (FUT imb) = 7
- short_signals: 0
- **Direction: LONG**

**Probability:**
- Base (score 70): 62%
- +5% (OI momentum)
- +5% (funding cheap)
- +5% (crowd short)
- +5% (SPOT imbalance)
- +3% (FUTURES imbalance)
- +5% (orderbook score >= 20)
- **Total: 90%** → capped by HIGH: **85%**

### Пример 2: Отклонённый сигнал

**Входные данные:**
- OI за 1h: -3%
- Funding: +0.02%
- Long ratio: 58%
- SPOT imbalance: -0.15
- FUTURES imbalance: +0.10

**Расчёт score:**
- oi_growth: 0 (OI падает)
- funding_cheap: 0 (funding > 0.01%)
- crowd_bullish: +10 (long >= 55%)

**Итого: 10 баллов** → **ОТКЛОНЁН** (< 65)

---

*Документ сгенерирован на основе аудита кода. Без фантазий — только логика из исходников.*
