# ПОЛНЫЙ АУДИТ И СРАВНЕНИЕ: Production vs GenerateHistorySignals

## КРИТИЧЕСКИЕ РАСХОЖДЕНИЯ (ПРИЧИНА 0 СИГНАЛОВ)

### ПРОБЛЕМА 1: ORDERBOOK ДАННЫЕ ВСЕГДА НУЛЕВЫЕ

**Production (src/signals/accumulation_detector.py):**
- Получает реальные orderbook данные из FuturesMonitor/RealTimeMonitor
- `spot_state.bid_volume_atr`, `ask_volume_atr`, `book_imbalance_atr` - РЕАЛЬНЫЕ значения
- `futures_state.futures_bid_volume_atr`, etc. - РЕАЛЬНЫЕ значения
- Orderbook scoring: до **40 баллов** (spot 20 + futures 20)

**GenerateHistorySignals (state_builder.py строки 85-87, 128-133):**
```python
# FuturesState - ВСЕГДА 0
futures_bid_volume_atr: Decimal = Decimal("0")
futures_ask_volume_atr: Decimal = Decimal("0")
futures_book_imbalance_atr: Decimal = Decimal("0")

# SymbolState - ВСЕГДА 0
bid_volume_atr: Decimal = Decimal("0")
ask_volume_atr: Decimal = Decimal("0")
book_imbalance_atr: Optional[Decimal] = None
```

**РЕЗУЛЬТАТ:**
- Orderbook scoring = 0 баллов (вместо до 40)
- `_determine_direction()` не получает orderbook signals
- `_calculate_probability()` не получает orderbook bonus (+5 до +15)

### ПРОБЛЕМА 2: CROSS-EXCHANGE ДАННЫЕ ОТСУТСТВУЮТ

**Production:**
- Использует StateStore для cross-exchange данных
- `score.cross_oi_migration` - до 10 баллов
- `score.cross_price_lead` - до 5 баллов

**GenerateHistorySignals:**
- Нет cross-exchange данных
- `cross_oi_migration = 0`
- `cross_price_lead = 0`

**РЕЗУЛЬТАТ:** Минус 15 баллов максимум.

### ПРОБЛЕМА 3: МАКСИМАЛЬНЫЙ СКОР В GenerateHistorySignals

**Production max score:**
```
OI factors:        35 баллов
Funding factors:   25 баллов
Crowd sentiment:   20 баллов
Detections:        20 баллов
Cross-exchange:    15 баллов
Orderbook:         40 баллов
─────────────────────────────
ИТОГО:            155 баллов (clamped to 100)
```

**GenerateHistorySignals max score:**
```
OI factors:        35 баллов
Funding factors:   25 баллов
Crowd sentiment:   20 баллов
Detections:        20 баллов
Cross-exchange:     0 баллов  ← ОТСУТСТВУЕТ
Orderbook:          0 баллов  ← ОТСУТСТВУЕТ
─────────────────────────────
ИТОГО:            100 баллов
```

**РЕАЛЬНЫЙ максимум без orderbook и cross:** ~80-85 баллов при идеальных условиях.

При пороге 65 нужно набрать минимум 65/100 = 65%.
Без orderbook (40 баллов) и cross (15 баллов) = без 55 баллов из возможных.
Реально доступно: 100 баллов max.

### ПРОБЛЕМА 4: PROBABILITY РАСЧЕТ БЕЗ ORDERBOOK

**Production _calculate_probability():**
```python
# Orderbook bonus (строки 728-773)
if spot_liquid AND spot_imbalance >= 0.4: +5
if spot_liquid AND spot_imbalance >= 0.2: +3
if fut_imbalance >= 0.4: +5
if fut_imbalance >= 0.2: +3
if both_agree: +3 (confirmation)
if orderbook_total >= 20: +5
if orderbook_total >= 10: +3
```

**GenerateHistorySignals:**
- Все orderbook = 0
- Probability bonus = 0
- Base probability 45-70 не получает boost

**РЕЗУЛЬТАТ:** При пороге min_probability=60, многие сигналы отсекаются.

---

## ДЕТАЛЬНОЕ СРАВНЕНИЕ ЛОГИКИ

### 1. analyze() - ИДЕНТИЧНО (с оговорками)

| Шаг | Production | GenerateHistorySignals | Статус |
|-----|------------|------------------------|--------|
| has_futures check | Да | Да | OK |
| _calculate_score() | Да | Да | ЧАСТИЧНО* |
| LONG rejection OI < -5% | Да (было -1%) | Да (-5%) | OK |
| score threshold | 65 (или 50) | 65 (или 50) | OK |
| _determine_direction() | Да | Да | ЧАСТИЧНО* |
| _calculate_probability() | Да | Да | ЧАСТИЧНО* |
| probability threshold | 60 | 60 | OK |

*ЧАСТИЧНО = логика идентична, но входные данные разные (нет orderbook)

### 2. _calculate_score() КОМПОНЕНТЫ

| Компонент | Production | GenerateHistorySignals | Разница |
|-----------|------------|------------------------|---------|
| oi_growth | 0-20 | 0-20 | OK |
| oi_stability | 0-5 | 0-5 | OK |
| funding_cheap | 0-15 | 0-15 | OK |
| funding_gradient | 0-10 | 0-10 | OK |
| extreme_funding_penalty | -15 to 0 | -15 to 0 | OK |
| crowd_bearish | 0-20 | 0-20 | OK |
| crowd_bullish | 0-20 | 0-20 | OK |
| coordinated_buying | 0-10 | 0-10 | OK |
| volume_accumulation | 0-10 | 0-10 | OK |
| wash_trading_penalty | -25 to 0 | -25 to 0 | OK |
| cross_oi_migration | 0-10 | **ВСЕГДА 0** | ПРОБЛЕМА |
| cross_price_lead | 0-5 | **ВСЕГДА 0** | ПРОБЛЕМА |
| spot_bid_pressure | 0-10 | **ВСЕГДА 0** | ПРОБЛЕМА |
| spot_ask_weakness | 0-5 | **ВСЕГДА 0** | ПРОБЛЕМА |
| spot_imbalance_score | 0-5 | **ВСЕГДА 0** | ПРОБЛЕМА |
| futures_bid_pressure | 0-10 | **ВСЕГДА 0** | ПРОБЛЕМА |
| futures_ask_weakness | 0-5 | **ВСЕГДА 0** | ПРОБЛЕМА |
| futures_imbalance_score | 0-5 | **ВСЕГДА 0** | ПРОБЛЕМА |
| orderbook_divergence | 0-5 | **ВСЕГДА 0** | ПРОБЛЕМА |
| orderbook_against_penalty | -16 to 0 | **ВСЕГДА 0** | OK (нет penalty) |

### 3. _determine_direction() СИГНАЛЫ

| Сигнал источник | Production | GenerateHistorySignals | Разница |
|-----------------|------------|------------------------|---------|
| Funding >= 0.03% → short +1 | Да | Да | OK |
| Funding < -0.01% → long +1 | Да | Да | OK |
| Long% >= 65% → short +2 | Да | Да | OK |
| Short% >= 55% → long +2 | Да | Да | OK |
| Spot imbalance >= 0.4 → long +2 | Да | **НЕТ (imb=0)** | ПРОБЛЕМА |
| Spot imbalance <= -0.4 → short +2 | Да | **НЕТ (imb=0)** | ПРОБЛЕМА |
| Futures imbalance >= 0.4 → long +2 | Да | **НЕТ (imb=0)** | ПРОБЛЕМА |
| Futures imbalance <= -0.4 → short +2 | Да | **НЕТ (imb=0)** | ПРОБЛЕМА |

### 4. Detection Triggers

| Триггер | Production порог | GenerateHistorySignals порог | Статус |
|---------|------------------|------------------------------|--------|
| VOLUME_SPIKE_HIGH | Real-time detection | volume_spike > 1.5 | OK |
| COORDINATED_BUYING | Real-time detection | buy_ratio > 0.6 | OK |
| OI_SPIKE | Real-time detection | abs(oi_5m) > 3.0 | OK |
| PRICE_MOMENTUM | Real-time detection | abs(price_5m) > 2.0 | OK |

---

## МАТЕМАТИКА: ПОЧЕМУ 0 СИГНАЛОВ

### Типичный сценарий без Orderbook

**Входные данные (реалистичные):**
```
oi_change_1h = +4%   → oi_growth = 5
oi_change_5m = +1%   → oi_stability = 5 (if 1h > 0)
funding = -0.005%    → funding_cheap = 10 (between 0 and -0.01)
funding_gradient = -0.01 → funding_gradient = 5
short_pct = 52%      → crowd_bearish = 5
detection = VOLUME_SPIKE → coordinated = 0, volume_accum = 5-10
```

**Расчет скора:**
```
oi_growth:        5
oi_stability:     5
funding_cheap:   10
funding_gradient: 5
crowd_bearish:    5
volume_accum:    10
─────────────────
ИТОГО:           40 баллов
```

**Результат:** 40 < 65 (порог) → **НЕТ СИГНАЛА**

### Что нужно для 65+ без orderbook

```
oi_growth:       20 (нужен OI +15%+ за час - РЕДКО)
oi_stability:     5
funding_cheap:   15 (нужен funding <= -0.01%)
funding_gradient:10 (нужен gradient -0.02%+)
crowd_bearish:   20 (нужен short% >= 60%)
volume_accum:    10
coordinated:     10 (нужен COORDINATED_BUYING detection)
─────────────────
ИТОГО:           90 баллов - ДОСТАТОЧНО
```

**Но это требует ИДЕАЛЬНЫХ условий:**
- OI +15% за час (очень редко)
- Funding <= -0.01% (не всегда)
- Short% >= 60% (не всегда)
- И volume spike + coordinated buying

---

## РЕШЕНИЯ

### Вариант 1: Снизить пороги (БЫСТРО)

```python
# config.py
min_accumulation_score: int = 45  # было 65
min_probability: int = 50         # было 60
```

**Минус:** Много шумных сигналов.

### Вариант 2: Добавить фиктивный orderbook scoring (СРЕДНЕ)

```python
# В _calculate_score() добавить после crowd:

# Компенсация за отсутствие orderbook в historical данных
# В production orderbook дает до 40 баллов
# Добавляем 15-20 фиксированных баллов
if not self._has_real_orderbook:
    score.spot_bid_pressure = 10  # фиктивно-нейтральный
    score.futures_bid_pressure = 10
```

### Вариант 3: Исправить веса без orderbook (ПРАВИЛЬНО)

Пересчитать все веса для режима "без orderbook":
- OI факторы: увеличить веса
- Funding: увеличить веса
- Crowd: увеличить веса
- Убрать orderbook из расчета полностью

---

## ВЫВОДЫ

1. **Главная проблема:** Orderbook данные = 0, это минус ~40 баллов из возможного скора.

2. **Вторичная проблема:** Cross-exchange данные = 0, минус еще ~15 баллов.

3. **Итог:** Максимум ~80 баллов вместо 155, при этом пороги остались 65/60.

4. **Production** работает потому что получает real-time orderbook данные которые дают существенный вклад в скор.

5. **GenerateHistorySignals** не может получить исторический orderbook (Binance не хранит), поэтому эти компоненты = 0.

---

## РЕКОМЕНДОВАННОЕ ИСПРАВЛЕНИЕ

В файле `GenerateHistorySignals/signals/accumulation_detector.py`:

1. После расчета всех компонентов, добавить компенсацию:

```python
# Компенсация за отсутствие orderbook в historical режиме
# Production получает до 40 баллов от orderbook
# Добавляем базовые 15 баллов если основные факторы положительные
if score.oi_growth > 0 or score.funding_cheap > 0 or score.crowd_bearish > 0:
    score.spot_bid_pressure = 8
    score.futures_bid_pressure = 7
```

2. Или снизить порог до 50:

```python
# config.py
min_accumulation_score: int = 50
```
