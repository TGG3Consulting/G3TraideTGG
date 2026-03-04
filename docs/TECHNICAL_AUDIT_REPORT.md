# ТЕХНИЧЕСКИЙ АУДИТ ТОРГОВОЙ СИГНАЛЬНОЙ СИСТЕМЫ
## BinanceFriend Signal System v1.0

---

## ═══════════════════════════════════════════════════════
## РАЗДЕЛ 1: ПОТОК ДАННЫХ (Data Flow)
## ═══════════════════════════════════════════════════════

### 1.1 Источники данных

| Тип данных | Источник | Файл | Интервал обновления |
|------------|----------|------|---------------------|
| SPOT Trades | WebSocket `@trade` | `realtime_monitor.py:380` | Real-time |
| SPOT Depth | WebSocket `@depth20@100ms` | `realtime_monitor.py:383` | 100ms |
| SPOT Klines | WebSocket `@kline_1m` | `realtime_monitor.py:389` | 1m candles |
| Futures OI | REST `/fapi/v1/openInterest` | `futures_monitor.py:890-978` | `settings.futures.oi_update_interval_sec` |
| Futures Funding | REST `/fapi/v1/premiumIndex` | `futures_monitor.py:980-1040` | `settings.futures.funding_update_interval_sec` |
| Futures L/S Ratio | REST `/futures/data/globalLongShortAccountRatio` | `futures_monitor.py:1056-1096` | `settings.futures.ls_ratio_update_interval_sec` |
| Futures Depth | WebSocket `@depth20@100ms` | `futures_monitor.py:454-503` | 100ms |
| Futures Klines | REST `/fapi/v1/klines` | `futures_monitor.py:653-704` | 60 sec |

### 1.2 Поток: WebSocket → SymbolState

```
WebSocket @trade → _process_trade() → SymbolState
                   ↓
                   • last_price = trade.price
                   • trades_1m.append(trade)
                   • trades_5m.append(trade)
                   • volume_1m/5m/1h += trade_value

WebSocket @depth20 → _process_depth() → SymbolState
                     ↓
                     • best_bid/best_ask
                     • bid_volume_20/ask_volume_20 (legacy top-20)
                     • raw_bids/raw_asks (полный стакан)
                     → _calculate_atr_volumes() → bid_volume_atr/ask_volume_atr

WebSocket @kline_1m → _process_kline() → SymbolState
                      ↓ (только при закрытии свечи)
                      • price_history.append(close)
                      • klines_1h.append((high, low, close))
                      → _calculate_atr_pct() → atr_1h_pct
```

### 1.3 Поток: REST → FuturesState

```
REST /fapi/v1/openInterest → _fetch_oi() → FuturesState
                             ↓
                             • current_oi = OpenInterestData
                             • oi_history.append(oi_data)
                             • oi_change_1m/5m/1h_pct (вычисляется)
                             • price_history.append((now, mark_price))

REST /fapi/v1/premiumIndex → _update_all_funding() → FuturesState
                             ↓
                             • current_funding = FundingRateData
                             • funding_history.append(funding_data)
                             • price_change_5m/1h_pct (вычисляется)

REST /futures/data/globalLongShortAccountRatio → _fetch_ls_ratio() → FuturesState
                                                 ↓
                                                 • current_ls_ratio = LongShortRatioData
```

### 1.4 Поток: States → AccumulationDetector

```
AccumulationDetector.analyze(symbol)
    ↓
    futures_state = self.futures.get_state(symbol)  # FuturesMonitor.get_state()
    spot_state = self.realtime.get_state(symbol)    # RealTimeMonitor.get_state()
    ↓
    _calculate_score(symbol, futures_state, spot_state)
        ↓
        Используемые поля из FuturesState:
        • oi_change_1h_pct, oi_change_5m_pct
        • current_funding.funding_rate_percent
        • funding_history[-3:]
        • current_ls_ratio.short_account_pct
        • price_change_1h_pct
        • futures_bid_volume_atr, futures_ask_volume_atr
        • futures_book_imbalance_atr

        Используемые поля из SymbolState:
        • bid_volume_atr, ask_volume_atr
        • book_imbalance_atr
```

### 1.5 Поток: AccumulationDetector → SignalGenerator

```
SignalGenerator.on_detection(detection)
    ↓
    accumulation_detector.add_detection(symbol, detection)
    ↓
    accumulation = accumulation_detector.analyze(symbol, skip_threshold=True)
    ↓
    _generate_signal(symbol, signal_type, accumulation, trigger_detection)
        ↓
        Используемые поля из AccumulationSignal:
        • score.total, score.orderbook_total
        • direction
        • confidence
        • probability
        • evidence
```

### 1.6 Поток: SignalGenerator → RiskCalculator

```
_generate_signal()
    ↓
    risk_levels = self.risk_calculator.calculate(
        symbol, direction, current_price, spot_state, futures_state
    )
    ↓
    RiskCalculator.calculate() использует:
        • spot_state.atr_1h_pct
        • spot_state.book_imbalance_atr
        • futures_state.futures_atr_1h_pct
        • futures_state.futures_book_imbalance_atr
```

### 1.7 Финальные поля в TradeSignal

```python
TradeSignal:
    signal_id              # str(uuid.uuid4())[:8]
    symbol                 # от detection
    timestamp              # datetime.now()
    direction              # от accumulation или _direction_from_detection
    signal_type            # из _signal_triggers mapping
    confidence             # от accumulation или MEDIUM
    probability            # от accumulation или 65
    entry_zone_low/high    # от RiskCalculator
    entry_limit            # от RiskCalculator
    current_price          # spot_state.last_price или futures_state.current_funding.mark_price
    stop_loss              # от RiskCalculator
    stop_loss_pct          # от RiskCalculator
    take_profits           # от RiskCalculator
    risk_reward_ratio      # от RiskCalculator
    valid_hours            # зависит от signal_type (4-24)
    evidence               # accumulation.evidence + trigger evidence
    details                # собирается в _collect_details()
    scenarios              # _create_scenarios()
    trigger_detections     # [detection_type]
    links                  # hardcoded URLs
```

### 1.8 ПРОБЛЕМЫ ПОТОКА ДАННЫХ

| # | Место | Проблема |
|---|-------|----------|
| 1 | `signal_generator.py:282-288` | `current_price` fallback: если `spot_state.last_price=0` И `futures_state.current_funding=None`, сигнал не генерируется. Но если `spot_state=None` совсем — будет AttributeError |
| 2 | `signal_generator.py:274` | `spot_state = self.realtime.get_state(symbol)` может вернуть None, далее `spot_state.last_price` вызовет AttributeError |
| 3 | `accumulation_detector.py:371-373` | `float(spot_state.bid_volume_atr)` вызывается БЕЗ проверки что `spot_state is not None` — сработает if на строке 370, но если spot_state=None и условие False, код пойдёт дальше |

---

## ═══════════════════════════════════════════════════════
## РАЗДЕЛ 2: ATR и ORDERBOOK РАСЧЁТЫ
## ═══════════════════════════════════════════════════════

### 2.1 ATR в realtime_monitor.py (SPOT)

**Файл:** `realtime_monitor.py:615-661`

**Когда рассчитывается:**
- При закрытии 1m свечи (`@kline_1m` с `kline["x"]=True`): строка 543-544
- При warmup (`warmup_baselines`): строка 207

**Алгоритм:**
```python
def _calculate_atr_pct(self, klines: list, period: int = 14) -> Decimal:
    # Требуется минимум period+1 = 15 свечей
    if len(klines) < period + 1:
        return Decimal("5")  # Default 5%

    # True Range для каждой свечи (кроме первой)
    for i in range(1, len(klines)):
        high, low, close = klines[i]
        prev_close = klines[i - 1][2]

        tr1 = high - low                # Внутридневной диапазон
        tr2 = abs(high - prev_close)    # Gap вверх
        tr3 = abs(low - prev_close)     # Gap вниз
        tr = max(tr1, tr2, tr3)
        true_ranges.append(tr)

    # EMA расчёт ATR
    multiplier = Decimal(2) / (Decimal(period) + 1)  # 2/15 = 0.133...
    atr = true_ranges[0]
    for tr in true_ranges[1:]:
        atr = (tr - atr) * multiplier + atr

    # Конвертация в проценты
    atr_pct = (atr / current_close) * 100

    # Clamp: [1%, 20%]
    return max(Decimal("1"), min(Decimal("20"), atr_pct))
```

**ПРОБЛЕМЫ:**
1. **До накопления 15 свечей** — возвращает дефолт 5%, что может быть неверно для волатильных/спокойных активов
2. **Clamp [1%, 20%]** — для стейблкоинов (типа USDC/USDT пары) 1% слишком много; для шиткоинов 20% может быть мало

### 2.2 ATR в futures_monitor.py (FUTURES)

**Файл:** `futures_monitor.py:706-752`

**Когда рассчитывается:**
- При загрузке klines (REST каждые 60 сек): `_klines_monitor_loop()`
- При старте: `_load_all_klines()`

**Алгоритм:** ИДЕНТИЧЕН spot версии (копипаста)

**Интервал обновления:** 60 секунд (REST), в отличие от SPOT который обновляется при каждой закрытой свече через WebSocket

**ПРОБЛЕМА:** Гонка данных — depth WebSocket обновляет `futures_raw_bids/asks` каждые 100ms, а klines REST обновляет `futures_atr_1h_pct` раз в 60 сек. Между обновлениями ATR стакан анализируется со "старым" ATR, что может давать неточные объёмы.

### 2.3 bid_volume_atr и ask_volume_atr (SPOT)

**Файл:** `realtime_monitor.py:590-613`

```python
def _calculate_atr_volumes(self, state: "SymbolState"):
    mid = state.mid_price  # (best_bid + best_ask) / 2
    if mid == 0:
        return

    atr_pct = state.atr_1h_pct / 100  # Например 5% → 0.05
    lower_bound = mid * (1 - atr_pct)  # mid - 5%
    upper_bound = mid * (1 + atr_pct)  # mid + 5%

    # Bids: только в диапазоне [lower_bound, mid]
    state.bid_volume_atr = sum(
        p * q for p, q in state.raw_bids
        if lower_bound <= p <= mid  # FIX-1: верхняя граница mid
    )

    # Asks: только в диапазоне [mid, upper_bound]
    state.ask_volume_atr = sum(
        p * q for p, q in state.raw_asks
        if mid <= p <= upper_bound  # FIX-1: нижняя граница mid
    )
```

**Логика границ:**
- `lower_bound = mid * 0.95` (при ATR=5%)
- `upper_bound = mid * 1.05`
- Bids фильтруются: `lower_bound <= price <= mid`
- Asks фильтруются: `mid <= price <= upper_bound`

**Mid как разделитель:** ДА, mid используется как граница между bid и ask зонами

### 2.4 futures_bid_volume_atr и futures_ask_volume_atr

**Файл:** `futures_monitor.py:585-609`

**Логика:** ИДЕНТИЧНА spot версии

```python
def _calculate_futures_atr_volumes(self, state: FuturesState):
    mid = state.futures_mid_price  # best_bid/ask или mark_price fallback
    atr_pct = state.futures_atr_1h_pct / 100
    lower_bound = mid * (1 - atr_pct)
    upper_bound = mid * (1 + atr_pct)

    # FIX-2: аналогично spot
    bid_volume = sum(p * q for p, q in state.futures_raw_bids if lower_bound <= p <= mid)
    ask_volume = sum(p * q for p, q in state.futures_raw_asks if mid <= p <= upper_bound)
```

### 2.5 book_imbalance_atr (SPOT)

**Файл:** `screener/models.py:212-231`

```python
@property
def book_imbalance_atr(self) -> Decimal:
    bid = self.bid_volume_atr
    ask = self.ask_volume_atr
    total = bid + ask

    if total == 0:
        return Decimal("0")  # FIX-5: нет данных = 0

    # Минимальный порог: $100 на каждой стороне
    if bid < 100 or ask < 100:
        return Decimal("0")  # Недостаточно данных

    raw_imbalance = (bid - ask) / total
    return Decimal(str(round(float(raw_imbalance), 4)))
```

**Формула:** `(bid - ask) / (bid + ask)`

**Диапазон:** [-1, +1], где +1 = все bids, -1 = все asks

**Fallback:** Возвращает 0 если:
- `total == 0`
- `bid < $100` ИЛИ `ask < $100`

### 2.6 futures_book_imbalance_atr (FUTURES)

**Файл:** `futures_monitor.py:191-211`

**Формула:** ИДЕНТИЧНА spot

**Fallback порог:** $100 (аналогично spot)

**ПРОБЛЕМА:** Порог $100 одинаков для spot и futures, но MIN_FUTURES_VOLUME_USD в AccumulationDetector = $5000, а MIN_SPOT_VOLUME_USD = $1000. Это несогласованность: imbalance вычисляется при $100+$100=$200, но скоринг требует $5000.

---

## ═══════════════════════════════════════════════════════
## РАЗДЕЛ 3: СКОРИНГ НАКОПЛЕНИЯ (AccumulationDetector)
## ═══════════════════════════════════════════════════════

**Файл:** `accumulation_detector.py:214-351`

### 3.1 Анализ каждого фактора

| # | Фактор | Макс | Условие макс | Вероятность на обычном рынке | Перекос |
|---|--------|-----|--------------|------------------------------|---------|
| 1 | `oi_growth` | 20 | `oi_change_1h >= 15%` | ~5% времени | Нет |
| 2 | `oi_stability` | 5 | `oi_1h > 0 AND oi_5m > 0` | ~30-40% времени | **ДА** — просто положительный OI одновременно |
| 3 | `funding_cheap` | 15 | `funding <= -0.01%` | ~15% времени | Умеренный |
| 4 | `funding_gradient` | 10 | `gradient <= -0.02` (падение за 3 периода) | ~5% времени | Нет |
| 5 | `crowd_bearish` | 20 | `short_pct >= 60%` | ~10% времени | Нет |
| 6 | `coordinated_buying` | 10 | Детекция COORDINATED_BUYING за 30 мин | ~2% времени | Нет |
| 7 | `volume_accumulation` | 10 | VOLUME_SPIKE + цена стабильна (<2%) | ~3% времени | Нет |
| 8 | `cross_oi_migration` | 10 | OI на одной бирже > 60% | ~20% времени | Умеренный |
| 9 | `cross_price_lead` | 5 | Есть price leader | ~50% времени | **ДА** — часто есть лидер |
| 10 | `spot_bid_pressure` | 10 | `bid/ask >= 2.0` | ~5% времени | Нет |
| 11 | `spot_ask_weakness` | 5 | `ask/bid < 0.5` | ~5% времени | Нет |
| 12 | `spot_imbalance_score` | 5 | `imbalance >= 0.4` | ~3% времени | Нет |
| 13 | `futures_bid_pressure` | 10 | `bid/ask >= 2.0` | ~5% времени | Нет |
| 14 | `futures_ask_weakness` | 5 | `ask/bid < 0.5` | ~5% времени | Нет |
| 15 | `futures_imbalance_score` | 5 | `imbalance >= 0.4` | ~3% времени | Нет |
| 16 | `orderbook_divergence` | 5 | Оба imbalance > 0.2 | ~3% времени | Нет |
| 17 | `wash_trading_penalty` | -25 | WASH_TRADING детекция | ~1% времени | Штраф |
| 18 | `extreme_funding_penalty` | -15 | `funding >= 0.05%` | ~5% времени | Штраф |
| 19 | `orderbook_against_penalty` | -10 | `ask/bid > 2` или `imbalance < -0.4` | ~5% времени | Штраф |

### 3.2 Расчёт скора на спокойном рынке

**Типичный спокойный рынок:**
- OI изменение ~0-3% → `oi_growth = 0-5`
- OI обычно растёт медленно → `oi_stability = 3-5`
- Funding нейтральный (~0-0.01%) → `funding_cheap = 0-5`
- L/S ~50/50 → `crowd_bearish = 0`
- Нет детекций → `coordinated_buying = 0`, `volume_accumulation = 0`
- OI распределён → `cross_oi_migration = 0-5`
- Часто есть price leader → `cross_price_lead = 5`
- Стакан сбалансирован → orderbook факторы = 0

**Итого на спокойном рынке:** ~8-25 баллов

### 3.3 Расчёт скора при реальном накоплении

**Реальное накопление:**
- OI +10-15% → `oi_growth = 10-20`
- OI стабильно растёт → `oi_stability = 5`
- Funding отрицательный → `funding_cheap = 10-15`
- Funding падает → `funding_gradient = 5-10`
- Толпа в шортах 55%+ → `crowd_bearish = 15-20`
- Возможно COORDINATED → +10
- Volume spike → +5-10
- Cross-exchange → +5-15
- Orderbook перекос → +10-25

**Итого при накоплении:** ~60-100 баллов

### 3.4 Оценка порога min_accumulation_score=65

**ВЫВОД:** Порог 65 достаточно высок и требует нескольких подтверждающих факторов одновременно. Однако:

- Если есть cross_price_lead (5) + oi_stability (5) + средний funding_cheap (5) + средний crowd_bearish (5) = уже 20 баллов без реального накопления
- Добавить cross_oi_migration (5) + любой orderbook перекос (10) = 35 баллов
- Для 65 нужно ещё 30 баллов от сильных сигналов

**Адекватность:** Порог 65 АДЕКВАТЕН, но может давать ложные срабатывания при совпадении нескольких слабых сигналов.

---

## ═══════════════════════════════════════════════════════
## РАЗДЕЛ 4: ОПРЕДЕЛЕНИЕ ВЕРОЯТНОСТИ
## ═══════════════════════════════════════════════════════

**Файл:** `accumulation_detector.py:544-623`

### 4.1 База вероятности (нелинейная шкала)

```python
_s = score.total
if _s < 50:    base = 45
elif _s < 65:  base = 55
elif _s < 75:  base = 62
elif _s < 85:  base = 70
elif _s < 95:  base = 78
else:          base = 85
```

| Score | Base Probability |
|-------|------------------|
| 0-49 | 45% |
| 50-64 | 55% |
| 65-74 | 62% |
| 75-84 | 70% |
| 85-94 | 78% |
| 95-100 | 85% |

### 4.2 Adjustments

| Условие | Adjustment |
|---------|------------|
| `oi_change_5m > 0` | +5 |
| `funding < 0` | +5 |
| `short_account_pct > 55%` | +5 |
| `spot_imbalance >= 0.4` | +5 |
| `spot_imbalance >= 0.2` | +3 |
| `fut_imbalance >= 0.4` (если ликвидный) | +5 |
| `fut_imbalance >= 0.2` (если ликвидный) | +3 |
| Оба `imbalance > 0.1` | +3 (confirmation) |
| `spot_imb * fut_imb < 0` (divergence) | -5 |
| `orderbook_total >= 20` | +5 |
| `orderbook_total >= 10` | +3 |

**Максимум adjustments:** +5+5+5+5+5+3+5 = ~+33 (реально ~+25)

### 4.3 Итоговая формула

```python
probability = min(95, max(0, base + adjustments))
```

### 4.4 Диапазон значений

| Сценарий | Score | Base | Adj | Final Prob |
|----------|-------|------|-----|------------|
| Минимум | 0 | 45 | 0 | 45% |
| Слабый сигнал | 50 | 55 | +5 | 60% |
| Порог (65) | 65 | 62 | +10 | 72% |
| Хороший | 80 | 70 | +15 | 85% |
| Отличный | 95 | 85 | +10 | 95% (cap) |
| Divergence | 65 | 62 | -5 | 57% |

### 4.5 Является ли это реальной вероятностью?

**НЕТ.** Это confidence score в диапазоне 45-95%, не калиброванная вероятность.

**Проблемы:**
1. Минимум 45% — нет способа показать низкую вероятность
2. Шкала сжата в [45, 95] — потеря информативности
3. Не калибровано на исторических данных

### 4.6 Проверка ликвидности futures стакана

**ДА**, проверяется:
```python
# futures_monitor.py:599-603
_fut_liquid = (_fut_bid_v + _fut_ask_v) >= self.MIN_FUTURES_VOLUME_USD  # $5000
fut_imbalance = float(futures_state.futures_book_imbalance_atr) if _fut_liquid else 0.0
```

**ПРОБЛЕМА:** Проверка ликвидности только для fut_imbalance бонуса, но НЕ для spot_imbalance бонуса (строки 592-597).

---

## ═══════════════════════════════════════════════════════
## РАЗДЕЛ 5: ОПРЕДЕЛЕНИЕ НАПРАВЛЕНИЯ
## ═══════════════════════════════════════════════════════

**Файл:** `accumulation_detector.py:449-531`

### 5.1 Условия для short_signals и long_signals

```python
short_signals = 0
long_signals = 0

# === FUTURES SIGNALS ===
# SHORT условие 1: extreme funding + падающий OI
if funding_pct >= 0.05 and oi_change_1h < -5:
    short_signals += 2

# LONG условие 1: negative funding
if funding_pct < -0.01:
    long_signals += 1

# SHORT условие 2: много лонгов + падающий OI
if long_pct >= 70 and oi_change_1h < -5:
    short_signals += 2

# LONG условие 2: много шортов
if short_pct >= 55:
    long_signals += 2

# === ORDERBOOK SIGNALS (SPOT) ===
# Только если ликвидный (>= MIN_SPOT_VOLUME_USD = $1000)
if spot_imbalance >= 0.4:  # IMBALANCE_STRONG
    long_signals += 2
elif spot_imbalance <= -0.4:
    short_signals += 2

# === ORDERBOOK SIGNALS (FUTURES) ===
# Только если ликвидный (>= MIN_FUTURES_VOLUME_USD = $5000)
if fut_imbalance >= 0.4:
    long_signals += 2
elif fut_imbalance <= -0.4:
    short_signals += 2

# === SCORE-BASED ===
if score.orderbook_against_penalty <= -5:
    short_signals += 1
```

### 5.2 Порог для SHORT

```python
if short_signals >= 3 and short_signals > long_signals:
    return SignalDirection.SHORT
```

**Требования для SHORT:**
1. `short_signals >= 3`
2. `short_signals > long_signals` (строгое неравенство)

### 5.3 Default direction

```python
# Default LONG (накопление = pump incoming)
return SignalDirection.LONG
```

### 5.4 Bias анализ

**Сценарии:**

| short_signals | long_signals | Результат |
|---------------|--------------|-----------|
| 0 | 0 | LONG (default) |
| 2 | 2 | LONG (не >= 3) |
| 3 | 3 | LONG (не > long_signals) |
| 3 | 2 | SHORT |
| 4 | 4 | LONG (не > long_signals) |

**Bias к LONG:**
- При ничьей → LONG
- При `short_signals < 3` → LONG (даже если 2 vs 0)
- Оценка: ~70% сигналов будут LONG при неопределённости

### 5.5 Случаи когда orderbook кричит SHORT но система вернёт LONG

**ДА, возможно:**

Пример:
- `spot_imbalance = -0.5` → `short_signals += 2`
- `fut_imbalance = -0.5` → `short_signals += 2`
- `short_signals = 4`, `long_signals = 0`
- **Результат:** SHORT (корректно)

Но:
- `spot_imbalance = -0.5` → `short_signals += 2`
- `fut_imbalance = 0` (нет данных)
- `short_pct = 60%` → `long_signals += 2` (crowd bearish = LONG signal!)
- `short_signals = 2`, `long_signals = 2`
- **Результат:** LONG (orderbook игнорируется!)

**ПРОБЛЕМА:** Логика `short_pct >= 55 → long_signals += 2` конфликтует с orderbook: толпа в шортах обычно означает давление продавцов в стакане, но система считает это LONG сигналом (contrarian).

---

## ═══════════════════════════════════════════════════════
## РАЗДЕЛ 6: RISK CALCULATOR
## ═══════════════════════════════════════════════════════

**Файл:** `risk_calculator.py`

### 6.1 _estimate_volatility

**Приоритет источников (строки 112-161):**

1. **SPOT ATR:** `spot_state.atr_1h_pct > 0`
2. **FUTURES ATR:** `futures_state.futures_atr_1h_pct > 0`
3. **Историческая волатильность:** `(max - min) / avg * 100` из `price_history[-60:]`
4. **Default:** 5%

**Корректировки:**
- Если `price_change_1h > 5%` → `volatility = max(volatility, price_change_1h * 1.5)`
- Если `oi_change > 10%` → `volatility *= 1.2`

**Clamp:** `max(3.0, min(20.0, volatility))`

### 6.2 _calculate_entry_zone

**Формула zone_pct (строка 178):**
```python
zone_pct = min(2.0, volatility_pct / 3)
```
При volatility=5% → zone_pct = 1.67%
При volatility=15% → zone_pct = 2% (cap)

**Entry limit по умолчанию (LONG, строка 185):**
```python
entry_limit = entry_zone_low + (entry_zone_high - entry_zone_low) * 0.3
```
То есть 30% от низа зоны — ближе к нижней границе.

**Orderbook корректировка:**
```python
if combined_imbalance > 0.4:  # Очень сильная поддержка
    entry_limit = current_price  # Входим по рынку
elif combined_imbalance > 0.2:
    entry_limit = ... * 0.5  # Середина зоны
```

**Максимальный сдвиг:** От 30% зоны до 100% (current_price) = сдвиг до 70% зоны.

### 6.3 _calculate_stop_loss

**Base SL (строка 248):**
```python
sl_pct = max(self.config.default_sl_pct, volatility_pct * 1.2)
```
При default_sl_pct=7% и volatility=5%: `max(7, 6) = 7%`
При volatility=10%: `max(7, 12) = 12%`

**Orderbook влияние:**
```python
# Сильный bid wall защищает
if combined_imbalance > 0.4:
    sl_pct *= 0.8  # Уменьшаем на 20%
elif combined_imbalance > 0.2:
    sl_pct *= 0.9  # Уменьшаем на 10%
elif combined_imbalance < -0.2:
    sl_pct *= 1.15  # Увеличиваем на 15%
```

**Проверка ATR (строка 291):**
```python
sl_pct = max(sl_pct, avg_atr_pct * 0.8)
```
SL не должен быть меньше 80% от ATR.

**Финальный clamp (строка 294):**
```python
sl_pct = min(15.0, max(3.0, sl_pct))
```

**ПРОБЛЕМА для низковолатильных активов:**
- Если ATR = 1%, то `sl_pct >= 0.8%`, но clamp → 3%
- Для стейблкоина (ATR ~0.1%) SL = 3% — это огромный стоп, убыток почти гарантирован

### 6.4 _calculate_take_profits

**Ratios (из config):**
```python
tp1_ratio = 1.5  # TP1 = 1.5x риска
tp2_ratio = 3.0  # TP2 = 3x риска
tp3_ratio = 5.0  # TP3 = 5x риска
```

**Portions:**
```python
tp1_portion = 30%
tp2_portion = 40%
tp3_portion = 30%
```

**Учитываются ли уровни из стакана?**
**НЕТ.** TP рассчитываются только как кратные от риска, без анализа resistance levels в orderbook.

### 6.5 _round_price

**Ветки округления (строки 391-404):**
```python
if price >= 10000:   # BTC > $10k
    → до $1
elif price >= 1000:  # $1k-$10k
    → до $0.1
elif price >= 100:   # $100-$1k
    → до $0.01
elif price >= 1:     # $1-$100
    → до $0.0001
else:                # < $1
    → до $0.000001
```

**ПРОБЛЕМА:** Нет дублирующихся веток, но нет проверки на price <= 0.

### 6.6 _calculate_risk_reward

**Формула (строки 368-389):**
```python
risk = abs(entry_price - stop_loss)
if risk == 0:
    return 0.0

weighted_reward = sum(
    abs(tp.price - entry_price) * tp.portion / 100
    for tp in take_profits
)

return round(weighted_reward / risk, 2)
```

**Корректность:** Формула взвешивания КОРРЕКТНА:
- Суммируется `reward * portion%` для каждого TP
- Делится на risk

Пример: SL=5%, TP1=7.5% (30%), TP2=15% (40%), TP3=25% (30%)
```
weighted_reward = 7.5*0.3 + 15*0.4 + 25*0.3 = 2.25 + 6 + 7.5 = 15.75
RR = 15.75 / 5 = 3.15
```

---

## ═══════════════════════════════════════════════════════
## РАЗДЕЛ 7: SIGNAL GENERATOR — ПОТОК И ЛОГИКА
## ═══════════════════════════════════════════════════════

**Файл:** `signal_generator.py`

### 7.1 on_detection()

**Условия фильтрации (строки 136-223):**

1. **add_detection:** Всегда добавляет в кэш (строка 159)
2. **Cooldown check** (строка 162): `_is_recent_signal(symbol)` → 1 час между сигналами
3. **Trigger check** (строка 167): Если не в `_signal_triggers` → проверяем накопление
4. **Score check** (строка 177): `detection.score < 60` → пропуск

**Кулдаун ВКЛЮЧЁН:** Да, `_is_recent_signal` вызывается на строке 162.

**Если detection_type не в _signal_triggers:**
```python
# Строка 170
return self._check_accumulation_signal(symbol)
```
Происходит проверка накопления без trigger_detection.

### 7.2 _generate_signal()

**current_price приоритет (строки 281-289):**
```python
current_price = Decimal("0")
if spot_state and spot_state.last_price > 0:
    current_price = spot_state.last_price
elif futures_state.current_funding:
    current_price = futures_state.current_funding.mark_price
```
1. SPOT last_price
2. FUTURES mark_price
3. Возврат None если оба = 0

**Direction приоритет (строки 295-302):**
```python
direction = SignalDirection.LONG  # default

if accumulation:
    direction = accumulation.direction  # Приоритет 1
elif trigger_detection:
    direction = self._direction_from_detection(trigger_detection)  # Приоритет 2
```

**valid_hours (строки 364-371):**
```python
_valid_hours_map = {
    SignalType.BREAKOUT: 4,
    SignalType.SQUEEZE_SETUP: 8,
    SignalType.ACCUMULATION: 24,
    SignalType.DIVERGENCE: 12,
    SignalType.CROSS_EXCHANGE: 6,
}
_valid_hours = _valid_hours_map.get(signal_type, self.config.default_valid_hours)
```
Зависит от signal_type.

**signal_id (строка 374):**
```python
signal_id=str(uuid.uuid4())[:8]
```
Первые 8 символов UUID4. Риск коллизий: ~1 на 4 миллиарда при 8 hex символах.

### 7.3 _collect_details()

**Bare except (строка 495):**
```python
except Exception:  # FIX-11: было bare except
    pass
```
Исправлено на `except Exception`, но всё ещё проглатывает без логирования.

**Собираемые поля:**
- `oi_change_1h`, `oi_change_5m`
- `funding`, `long_pct`, `short_pct`
- `futures_bid_volume_atr`, `futures_ask_volume_atr`, `futures_imbalance_atr`, `futures_atr_pct`
- `volume_ratio`, `spread`
- `spot_bid_volume_atr`, `spot_ask_volume_atr`, `spot_imbalance_atr`, `spot_atr_pct`
- `book_imbalance` (legacy)
- `cross_spread`, `funding_spread`

### 7.4 Дедупликация сигналов

**Структура (строка 76):**
```python
self._recent_signals: dict[str, datetime] = {}  # symbol → last_signal_time
```

**Проверка (строки 519-526):**
```python
def _is_recent_signal(self, symbol: str) -> bool:
    last_signal = self._recent_signals.get(symbol)
    if not last_signal:
        return False
    return (datetime.now() - last_signal) < timedelta(hours=1)
```

**Очистка (строки 528-537):**
```python
def _record_signal(self, symbol: str) -> None:
    self._recent_signals[symbol] = datetime.now()

    # Очистка старых (>24 часа)
    cutoff = datetime.now() - timedelta(hours=24)
    self._recent_signals = {
        s: t for s, t in self._recent_signals.items()
        if t > cutoff
    }
```
Очищается только при записи нового сигнала.

---

## ═══════════════════════════════════════════════════════
## РАЗДЕЛ 8: SIGNAL FORMATTER
## ═══════════════════════════════════════════════════════

**Файл:** `signal_formatter.py`

### 8.1 format_signal()

**Доступ к пустым спискам (строки 51-54):**
```python
for tp in signal.take_profits:  # Безопасно, пустой список = 0 итераций
    lines.append(...)
```
**Безопасно** — итерация по пустому списку не вызывает ошибку.

**Доступ к evidence (строка 114):**
```python
for evidence in signal.evidence[:6]:  # Безопасно, слайс пустого = []
```
**Безопасно.**

### 8.2 format_signal_compact()

**take_profits[0] (строка 164):**
```python
f"TP1: <code>${signal.take_profits[0].price if signal.take_profits else 'N/A'}</code>"
```
**Защита есть:** Тернарный оператор проверяет `if signal.take_profits`.

### 8.3 HTML escaping

**Используется (строки 116, 127):**
```python
safe_evidence = html.escape(str(evidence))
safe_scenario = html.escape(str(scenario))
```

**НЕ экранируются:**
- `signal.symbol` (строка 31) — но это контролируемые данные (от API Binance)
- `signal.links` URLs (строки 141-145) — контролируемые данные

### 8.4 Длина сообщения

**Потенциальная проблема:**
- Header: ~300 символов
- Entry: ~150 символов
- Take Profits (3): ~200 символов
- Orderbook: ~400 символов
- Evidence (до 6): ~600 символов
- Scenarios (3): ~450 символов
- Links: ~200 символов
- **Итого:** ~2300 символов

**Лимит Telegram:** 4096 символов
**Вывод:** Вероятно уложится, но при длинных evidence может превысить.

---

## ═══════════════════════════════════════════════════════
## РАЗДЕЛ 9: SIGNAL LOGGER
## ═══════════════════════════════════════════════════════

**Файл:** `signal_logger.py`

### 9.1 Ротация файла

**Отсутствует.** Файл открывается в режиме append (`"a"`), ротация не реализована.

### 9.2 Thread safety / async safety

**Частичная безопасность:**
- Один файл handle (`self._file`)
- В asyncio нет реальной параллельности (single thread)
- НО: если `log_signal` вызывается из разных tasks и один делает flush, а другой пишет — проблем не будет из-за GIL

**Потенциальная проблема:** Если `log_signal` вызывается когда файл ещё не открыт (строка 103-104):
```python
if not self._started or not self._file:
    logger.error(...)
    return False
```

### 9.3 Обработка ошибок файла

**При ошибке открытия (строки 66-68):**
```python
except Exception as e:
    logger.error("signal_logger_start_failed", error=str(e), path=str(self._log_path))
    self._started = False
```
Файл не открывается, но система продолжает работать.

**При ошибке записи (строки 135-142):**
```python
except Exception as e:
    logger.error("signal_log_failed", error=str(e), ...)
    return False
```
Логируется ошибка, возвращается False.

**При диске полном / нет прав:** Будет поймано `Exception` и залогировано.

### 9.4 _safe_dict типы данных

**Обрабатываемые (строки 377-396):**
- `Decimal` → `float`
- `datetime` → `isoformat()`
- `dict` → рекурсивный вызов
- `list, tuple` → обработка элементов (но только Decimal→float)
- Остальное → как есть

**Не обрабатываются:**
- `Enum` — будет сохранён как объект, что может сломать JSON
- Вложенные datetime в списках

---

## ═══════════════════════════════════════════════════════
## РАЗДЕЛ 10: КЭШИ И ПАМЯТЬ
## ═══════════════════════════════════════════════════════

### 10.1 _recent_detections (AccumulationDetector)

**Файл:** `accumulation_detector.py:93`

```python
self._recent_detections: dict[str, List] = {}
```

**Ограничение по символам:** НЕТ

**Очистка:** Только при добавлении нового (строки 112-117):
```python
cutoff = datetime.now() - timedelta(minutes=30)
self._recent_detections[symbol] = [
    d for d in self._recent_detections[symbol]
    if d["timestamp"] > cutoff
]
```

**Проблема:** Если символ перестал приходить, его детекции НЕ очищаются до следующего вызова `add_detection` для ЭТОГО символа.

**Memory leak при 500+ символах:**
- Каждый символ хранит ~10-50 детекций за 30 минут
- Каждая детекция ~500 байт
- 500 символов × 30 детекций × 500 байт = ~7.5 MB
- **Умеренный риск**, но не критично.

### 10.2 _recent_signals (SignalGenerator)

**Файл:** `signal_generator.py:76`

```python
self._recent_signals: dict[str, datetime] = {}
```

**Ограничение:** НЕТ

**Очистка:** При записи нового сигнала, удаляются записи старше 24 часов (строки 533-537).

**Memory:** 500 символов × ~100 байт = ~50 KB — **не критично**.

### 10.3 FuturesState.oi_history

**Файл:** `futures_monitor.py:130`

**Ограничение (строки 919-923):**
```python
cutoff = datetime.now() - timedelta(hours=1, minutes=15)
state.oi_history = [
    oi for oi in state.oi_history
    if oi.timestamp > cutoff
]
```
Хранится 75 минут данных.

**При обновлении каждые 60 сек:** ~75 записей × ~200 байт = ~15 KB на символ.

**500 символов:** ~7.5 MB — **приемлемо**.

### 10.4 FuturesState.price_history

**Ограничение (строки 963-967):**
```python
price_cutoff = datetime.now() - timedelta(hours=1, minutes=5)
state.price_history = [
    (ts, price) for ts, price in state.price_history
    if ts > price_cutoff
]
```
Хранится 65 минут.

**Memory:** Аналогично oi_history — **приемлемо**.

### 10.5 SymbolState.trades_1m и trades_5m

**Очистка по времени (realtime_monitor.py:551-556):**
```python
def _cleanup_old_trades(self, state: SymbolState, now_ms: int):
    state.trades_1m = [t for t in state.trades_1m if now_ms - t.time < 60_000]
    state.trades_5m = [t for t in state.trades_5m if now_ms - t.time < 300_000]
```

**Корректность:** ДА, очистка корректна — удаляются трейды старше 1/5 минут.

**Memory:** При 100 трейдов/мин × 5 мин × ~100 байт = ~50 KB на символ — **приемлемо**.

---

## ═══════════════════════════════════════════════════════
## РАЗДЕЛ 11: RACE CONDITIONS И ASYNC SAFETY
## ═══════════════════════════════════════════════════════

### 11.1 realtime_monitor._process_depth()

**Код (строки 476-507):**
```python
if bids:
    state.best_bid = Decimal(str(bids[0][0]))
    state.raw_bids = [(Decimal(str(p)), Decimal(str(q))) for p, q in bids]
# ...
self._calculate_atr_volumes(state)  # Читает raw_bids/asks
```

**Риск:** В asyncio — **НЕТ РИСКА** (cooperative multitasking). Между `state.raw_bids = ...` и `_calculate_atr_volumes()` нет `await`, поэтому другой coroutine не может вмешаться.

### 11.2 futures_monitor depth WebSocket vs klines REST

**Проблема:**
- WebSocket пишет `futures_raw_bids/asks` каждые 100ms
- REST пишет `futures_klines_1h` и `futures_atr_1h_pct` раз в 60 сек

**Риск:** НЕТ прямого data race, но логическая несогласованность:
- `_calculate_futures_atr_volumes()` использует `futures_atr_1h_pct` который мог обновиться между чтениями
- В asyncio это безопасно, но данные могут быть "несвежими"

### 11.3 on_detection callback

**Код (futures_monitor.py:1684-1690):**
```python
if self._on_detection:
    result = self._on_detection(detection)
    if asyncio.iscoroutine(result):
        asyncio.create_task(result)
```

**Блокировка:** Если callback синхронный — **ДА**, блокирует. Если async — создаётся task.

**Проблема:** В signal_generator callback может быть долгим (вызывает analyze, calculate, etc.). Если синхронный — блокирует WebSocket обработку.

### 11.4 _recent_detections dict

**Модификация:**
- `add_detection()` — добавляет и фильтрует
- `get_recent_detections()` — читает
- `_calculate_score()` → `self._recent_detections.get(symbol, [])` — читает

**Риск:** В asyncio — **НИЗКИЙ**. Dict operations атомарны в CPython благодаря GIL.

---

## ═══════════════════════════════════════════════════════
## РАЗДЕЛ 12: КОНФИГУРАЦИЯ (SignalConfig)
## ═══════════════════════════════════════════════════════

**Файл:** `signals/models.py:244-317`

### 12.1 Все пороги по умолчанию

```python
@dataclass
class SignalConfig:
    min_accumulation_score: int = 65
    min_probability: int = 60

    confidence_low: int = 50
    confidence_medium: int = 65
    confidence_high: int = 80
    confidence_very_high: int = 90

    default_sl_pct: float = 7.0
    min_risk_reward: float = 2.0

    tp1_ratio: float = 1.5
    tp2_ratio: float = 3.0
    tp3_ratio: float = 5.0

    tp1_portion: int = 30
    tp2_portion: int = 40
    tp3_portion: int = 30

    default_valid_hours: int = 24

    oi_growth_min: float = 5.0
    oi_growth_strong: float = 15.0

    funding_cheap_threshold: float = -0.01
    funding_extreme_threshold: float = 0.05

    crowd_short_threshold: float = 55.0
    crowd_extreme_short: float = 60.0
```

### 12.2 min_accumulation_score=65

**Когда набирается легко:**
- Сильный OI рост (20) + negative funding (15) + crowd bearish (15) = 50
- Добавить oi_stability (5) + cross_price_lead (5) + любой orderbook (10) = 70

**Вывод:** При совпадении 3-4 сильных факторов.

### 12.3 min_probability=60

**Связь с реальной вероятностью:** НЕТ. При score=65, base=62, adjustments=0 → probability=62. Это просто трансформация score.

### 12.4 default_sl_pct=7.0

**Адекватность:**
- Для BTC/ETH (волатильность ~3-5%): 7% SL может быть слишком далеко, не выбьет на нормальном движении, но и profit будет долго ждать
- Для альткоинов (волатильность ~10-15%): 7% SL может быть слишком близко
- Для шиткоинов (волатильность ~20%+): 7% — выбьет сразу

**Вывод:** Дефолт 7% неадекватен как универсальное значение.

### 12.5 MIN_SPOT_VOLUME_USD и MIN_FUTURES_VOLUME_USD

**Расположение:**
- `accumulation_detector.py:70-71`:
  ```python
  MIN_SPOT_VOLUME_USD = 1000
  MIN_FUTURES_VOLUME_USD = 5000
  ```
- `risk_calculator.py`: НЕТ (не использует эти пороги)
- `screener/models.py:227`: `bid < 100 or ask < 100` — порог $100

**Несогласованность:** Три разных порога ($100, $1000, $5000) в разных местах.

---

## ═══════════════════════════════════════════════════════
## РАЗДЕЛ 13: ОБРАБОТКА ОШИБОК
## ═══════════════════════════════════════════════════════

### 13.1 Bare except (except: без типа)

**НЕ НАЙДЕНО.** Все except используют типы (Exception, конкретные исключения).

### 13.2 except Exception: pass (проглатывание)

| Файл | Строка | Код |
|------|--------|-----|
| `signal_generator.py` | 495-496 | `except Exception: pass` — cross-exchange данные |

### 13.3 Нет проверки на None перед обращением

| Файл | Строка | Проблема |
|------|--------|----------|
| `signal_generator.py` | 282-283 | `spot_state.last_price` когда spot_state может быть None (проверка только `if spot_state and ...`) |
| `accumulation_detector.py` | 467 | `futures_state.current_funding` — проверяется, но внутри блока `funding_pct = float(...)` может упасть если `current_funding` станет None между проверкой и использованием (race condition в теории) |

### 13.4 Нет проверки на пустой список перед индексом

| Файл | Строка | Код | Защита |
|------|--------|-----|--------|
| `futures_monitor.py` | 1070 | `data[0]` | Проверка `if not data: return` выше |
| `signal_formatter.py` | 164 | `take_profits[0]` | Тернарный `if signal.take_profits else` |
| `accumulation_detector.py` | 266 | `recent[-1]` | Проверка `if len(funding_history) >= 3` выше |

**Защиты присутствуют**, но могут быть пропущены при рефакторинге.

### 13.5 Нет проверки на деление на ноль

| Файл | Строка | Код | Защита |
|------|--------|-----|--------|
| `screener/models.py` | 264-266 | `self.volume_5m / avg_5m` | `if avg_5m == 0: return Decimal("0")` |
| `risk_calculator.py` | 379-381 | `weighted_reward / risk` | `if risk == 0: return 0.0` |
| `accumulation_detector.py` | 379 | `spot_bid / spot_ask` | `if spot_ask > 0:` |

**Защиты присутствуют** в большинстве мест.

**НАЙДЕНА ПРОБЛЕМА:**
| Файл | Строка | Код |
|------|--------|-----|
| `accumulation_detector.py` | 661 | `ratio = spot_bid / spot_ask if spot_ask > 0 else 0` — но это в evidence, не критично |

---

## ═══════════════════════════════════════════════════════
## РАЗДЕЛ 14: ИТОГОВАЯ ТАБЛИЦА ПРОБЛЕМ
## ═══════════════════════════════════════════════════════

| # | Файл | Метод/строка | Тип проблемы | Крит. | Описание |
|---|------|--------------|--------------|-------|----------|
| 1 | `signal_generator.py` | `_generate_signal:274` | None access | 4 | `spot_state` может быть None, далее используется без проверки в некоторых ветках |
| 2 | `accumulation_detector.py` | `_calculate_probability:592-597` | Логика | 3 | Нет проверки ликвидности для SPOT imbalance бонуса (есть только для FUTURES) |
| 3 | `accumulation_detector.py` | `_determine_direction:487` | Логика | 4 | `short_pct >= 55 → long_signals += 2` конфликтует с orderbook SHORT сигналами |
| 4 | `risk_calculator.py` | `_calculate_stop_loss:294` | Логика | 3 | Clamp SL минимум 3% неадекватен для низковолатильных активов |
| 5 | `risk_calculator.py` | `_calculate_take_profits` | Отсутствует | 2 | TP не учитывают resistance levels из orderbook |
| 6 | `futures_monitor.py` | `_klines_monitor_loop` | Гонка данных | 2 | ATR обновляется раз в 60 сек, depth каждые 100ms — несинхронность |
| 7 | `signal_generator.py` | `_collect_details:495-496` | Проглатывание | 2 | `except Exception: pass` — ошибки cross-exchange не логируются |
| 8 | `signal_logger.py` | класс | Отсутствует | 2 | Нет ротации логов — файл растёт бесконечно |
| 9 | `signal_logger.py` | `_safe_dict:377-396` | Неполная обработка | 2 | Enum и datetime в списках не конвертируются в JSON-совместимый формат |
| 10 | `accumulation_detector.py` | `_recent_detections` | Memory | 3 | Детекции для неактивных символов не очищаются |
| 11 | `screener/models.py` | `book_imbalance_atr:227` | Несогласованность | 2 | Порог $100 не совпадает с MIN_SPOT_VOLUME_USD=$1000 |
| 12 | `signal_formatter.py` | `format_signal` | Лимит | 2 | Длина сообщения может превысить 4096 при длинных evidence |
| 13 | `accumulation_detector.py` | `_calculate_score:230-241` | Перекос | 3 | `oi_stability` даёт 5 баллов за обычное состояние (OI > 0 одновременно) |
| 14 | `signal_generator.py` | `_recent_signals` | Memory | 2 | Очистка только при записи нового сигнала |
| 15 | `accumulation_detector.py` | `_determine_direction:531` | Bias | 3 | Default LONG при ничьей — 70%+ сигналов будут LONG при неопределённости |
| 16 | `risk_calculator.py` | `_estimate_volatility:161` | Clamp | 2 | Минимум 3% волатильности неадекватен для стейблкоинов |
| 17 | `signals/models.py` | `SignalConfig:258` | Конфигурация | 3 | `default_sl_pct=7%` неадекватен как универсальный дефолт |
| 18 | `futures_monitor.py` | `_depth_ws_connection:454-503` | Callback блокировка | 3 | Синхронный callback блокирует WebSocket обработку |
| 19 | `accumulation_detector.py` | `_calculate_orderbook_score:387` | Логика | 2 | `orderbook_against_penalty -= 5` кумулятивно вычитается (может стать < -10) |
| 20 | `realtime_monitor.py` | `_calculate_atr_pct:626` | Холодный старт | 2 | До 15 свечей возвращает дефолт 5%, что может быть неверно |

---

## РЕЗЮМЕ

### Критичные проблемы (уровень 4):
1. **None access в signal_generator** — может вызвать crash при отсутствии spot_state
2. **Конфликт логики direction** — crowd bearish добавляет LONG signals, что противоречит orderbook SHORT

### Логические проблемы (уровень 3):
1. **Отсутствие проверки ликвидности SPOT** при расчёте probability
2. **Минимальный SL 3%** неадекватен для разных классов активов
3. **LONG bias 70%+** при неопределённости
4. **oi_stability** легко набирается без реального накопления
5. **Детекции для неактивных символов** не очищаются
6. **Callback блокировка** в WebSocket handler

### Технический долг (уровень 2):
1. Нет ротации логов
2. Несогласованные пороги ликвидности ($100/$1000/$5000)
3. TP не учитывают уровни из стакана
4. Проглатывание ошибок cross-exchange
5. Неполная конвертация типов в JSON

---

*Отчёт сгенерирован: 2026-02-20*
*Аудитор: Claude Opus 4.5*
