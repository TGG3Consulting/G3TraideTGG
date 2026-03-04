# ПЛАН ВНЕДРЕНИЯ OI_SPIKE BONUS

**Дата:** 2026-02-27
**Статус:** ✅ РЕАЛИЗОВАНО
**Приоритет:** ВЫСОКИЙ (OI_SPIKE прибылен в 80% файлов, но не даёт баллов)

---

## СИСТЕМНЫЙ ПРОМПТ ДЛЯ ИСПОЛНИТЕЛЯ

```
You are a senior data analyst and trader with 30 years of hands-on experience in financial markets. You have worked across equities, futures, forex, commodities, and crypto. You think like a quant but speak like a veteran floor trader.

When analyzing any trading data, chart, or market question, you MUST follow this exact analytical framework — no shortcuts, no skipping steps:

---

## MANDATORY ANALYSIS PROTOCOL

### 1. MARKET STRUCTURE ANALYSIS
- Identify the current trend (macro + micro timeframes)
- Define key structural levels: HH, HL, LH, LL
- Mark consolidation zones and range boundaries
- Identify market phase: accumulation / markup / distribution / markdown

### 2. VOLUME & LIQUIDITY ANALYSIS
- Analyze volume profile (where is the majority of volume traded?)
- Identify liquidity pools: stops above highs, stops below lows
- Detect volume anomalies: climactic volume, dry-up volume, absorption
- Assess bid/ask pressure and order flow imbalance if applicable

### 3. KEY LEVELS & ZONES
- Support and resistance (historical + current)
- Fair Value Gaps (FVG) / Imbalance zones
- Point of Control (POC) and Value Area (VA)
- Psychological round numbers
- Previous day/week/month highs & lows

### 4. PRICE ACTION ANALYSIS
- Identify dominant candlestick patterns and their context
- Detect manipulation patterns: stop hunts, false breakouts, spring/upthrust
- Assess momentum: is it expanding or contracting?
- Look for divergences between price and momentum

### 5. RISK ASSESSMENT
- Define the highest-probability scenario (primary thesis)
- Define 1-2 alternative scenarios with invalidation levels
- Specify exact entry zone, stop-loss level, and target(s)
- Calculate Risk/Reward ratio
- Assess position sizing recommendation (% risk)

### 6. CONFLUENCE SCORE
Rate the trade setup quality from 1–10 based on how many factors align:
- Trend alignment ✓/✗
- Volume confirmation ✓/✗
- Key level confluence ✓/✗
- Price action signal ✓/✗
- Risk/Reward ≥ 2:1 ✓/✗

### 7. FINAL VERDICT
State clearly:
- **BIAS:** Bullish / Bearish / Neutral
- **ACTION:** Buy / Sell / Wait / Avoid
- **ENTRY:** [price or zone]
- **STOP:** [price]
- **TARGETS:** T1, T2, T3
- **VALIDITY:** How long does this setup remain valid?

---

## YOUR COMMUNICATION STYLE
- Be direct, blunt, and professional — like a trader who has seen every market condition
- No fluff, no vague statements — every claim must be backed by data or price action logic
- If the data is insufficient for confident analysis, say so explicitly and state what additional data you need
- Use trader terminology naturally (liquidity sweep, displacement, OB, FVG, VWAP, etc.)
- Always think in terms of risk first, profit second
```

---

## КОНТЕКСТ ПРОБЛЕМЫ

### Текущее состояние

OI_SPIKE детектируется корректно, но **НЕ ДАЁТ БАЛЛОВ** к Accumulation Score:

| Детекция | Баллы | Прибыльность |
|----------|-------|--------------|
| COORDINATED_BUYING | +10 | **УБЫТОЧЕН в 90%** |
| VOLUME_SPIKE | +5/+10 | Нейтральный |
| OI_SPIKE | **0** | **ПРИБЫЛЕН в 80%** |

### Парадокс

Худший триггер (COORDINATED_BUYING) получает +10 баллов.
Лучший триггер (OI_SPIKE) получает 0 баллов.

### Данные из бэктеста (10 файлов, ~134,000 сделок)

- OI_SPIKE прибылен в 8 из 10 файлов (80%)
- COORDINATED_BUYING убыточен в 9 из 10 файлов (90%)
- OI данные загружаются каждые 5 минут (не daily)
- OI_SPIKE срабатывает когда |oi_change_5m| > 3%

---

## ПЛАН РЕАЛИЗАЦИИ

### ФАЙЛ 1: `GenerateHistorySignals/signals/models.py`

**Изменение 1.1:** Добавить поле в AccumulationScore

```
Найти:
@dataclass
class AccumulationScore:
    ...
    coordinated_buying: int = 0
    volume_accumulation: int = 0

Добавить после volume_accumulation:
    oi_spike_bonus: int = 0
```

**Изменение 1.2:** Добавить в свойство total

```
Найти в @property def total(self):
    positive = (
        ...
        self.coordinated_buying +
        self.volume_accumulation +
        ...

Добавить:
        self.oi_spike_bonus +
```

**Изменение 1.3:** Добавить в to_dict()

```
Найти в def to_dict(self):
    return {
        ...
        "volume_accumulation": self.volume_accumulation,

Добавить после:
        "oi_spike_bonus": self.oi_spike_bonus,
```

**Изменение 1.4:** Добавить параметр в SignalConfig

```
Найти:
@dataclass
class SignalConfig:
    ...
    crowd_extreme_short: float = 60.0

Добавить после:
    oi_spike_bonus_points: int = 10
```

---

### ФАЙЛ 2: `GenerateHistorySignals/signals/accumulation_detector.py`

**Изменение 2.1:** Добавить проверку OI_SPIKE в _calculate_score()

```
Найти в методе _calculate_score():
    has_volume_spike = any(
        "VOLUME_SPIKE" in d["type"]
        for d in recent
    )
    ...
    if has_volume_spike and price_stable:
        ...
        score.volume_accumulation = 10
    elif has_volume_spike:
        score.volume_accumulation = 5

Добавить ПОСЛЕ этого блока:
    # OI_SPIKE BONUS
    has_oi_spike = any("OI_SPIKE" in d["type"] for d in recent)
    if has_oi_spike:
        score.oi_spike_bonus = self.config.oi_spike_bonus_points
```

---

### ФАЙЛ 3: Синхронизация с `src/signals/models.py` (если используется)

Проверить есть ли `G:\BinanceFriend\src\signals\models.py` и нужно ли синхронизировать изменения.

---

## ВИЗУАЛЬНАЯ СХЕМА

```
БЫЛО:
┌─────────────────────────────────────────────────────────────────┐
│ OI_SPIKE detected → stored in cache → NOT USED IN SCORE        │
│                                                                 │
│ _calculate_score():                                             │
│   check COORDINATED_BUYING → +10 points ✓                      │
│   check VOLUME_SPIKE → +5/+10 points ✓                         │
│   check OI_SPIKE → ??? (НЕТ КОДА!)                             │
└─────────────────────────────────────────────────────────────────┘

СТАНЕТ:
┌─────────────────────────────────────────────────────────────────┐
│ OI_SPIKE detected → stored in cache → ADDS BONUS TO SCORE      │
│                                                                 │
│ _calculate_score():                                             │
│   check COORDINATED_BUYING → +10 points ✓                      │
│   check VOLUME_SPIKE → +5/+10 points ✓                         │
│   check OI_SPIKE → +10 points ✓ (ДОБАВЛЕНО)                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## КРИТЕРИИ ПРИЁМКИ

1. **Поле существует:** `AccumulationScore.oi_spike_bonus` добавлено
2. **Учитывается в total:** Бонус суммируется в `total` property
3. **Записывается в output:** Поле есть в `to_dict()` и пишется в JSONL
4. **Настраивается:** Значение берётся из `SignalConfig.oi_spike_bonus_points`
5. **Работает:** При OI_SPIKE детекции score увеличивается на заданное значение

---

## ТЕСТИРОВАНИЕ

После внедрения:

1. Запустить генерацию сигналов на небольшом периоде
2. Проверить что в output JSONL появилось поле `oi_spike_bonus`
3. Проверить что значения > 0 когда есть OI_SPIKE триггер
4. Проверить что `total` score увеличился соответственно

---

## СВЯЗАННЫЕ ФАЙЛЫ

| Файл | Путь | Что менять |
|------|------|------------|
| models.py | `GenerateHistorySignals/signals/models.py` | AccumulationScore, SignalConfig |
| accumulation_detector.py | `GenerateHistorySignals/signals/accumulation_detector.py` | _calculate_score() |
| models.py (prod) | `src/signals/models.py` | Синхронизировать если нужно |
| accumulation_detector.py (prod) | `src/signals/accumulation_detector.py` | Синхронизировать если нужно |

---

## ПРИНЦИПЫ РЕАЛИЗАЦИИ

1. **БЕЗ ХАРДКОДА** — значение баллов в конфиге
2. **БЕЗ КОСТЫЛЕЙ** — следуем паттернам COORDINATED_BUYING
3. **БЕЗ ДУБЛИРОВАНИЯ** — используем существующую инфраструктуру
4. **ПРОЗРАЧНО** — бонус записывается в output для анализа
5. **ТЕСТИРУЕМО** — можно проверить в бэктесте

---

## КОМАНДА ДЛЯ СЛЕДУЮЩЕГО СЕАНСА

```
Реализуй план из файла PLAN_OI_SPIKE_IMPLEMENTATION.md:
1. Добавь поле oi_spike_bonus в AccumulationScore
2. Добавь его в total и to_dict()
3. Добавь параметр oi_spike_bonus_points в SignalConfig
4. Добавь проверку OI_SPIKE в _calculate_score()
5. Синхронизируй с prod версией если нужно
```

---

**Конец плана**
