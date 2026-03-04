# ПОВТОРНЫЙ АУДИТ ТОРГОВОЙ СИСТЕМЫ BINANCEFRIEND

**Дата:** 2026-02-20
**Версия:** 2.0 (после 16 + 7 правок)
**Аудитор:** Claude Opus 4.5

═══════════════════════════════════════════════════════
## БЛОК 1: ЛОГИКА ТОРГОВЫХ СИГНАЛОВ
═══════════════════════════════════════════════════════

### 1. Минимальные условия для LONG сигнала

Трассировка кода показывает минимальный набор:

```
on_detection() → AccumulationDetector.analyze() → _determine_direction()
```

**Минимальные условия:**
1. `futures_state.has_futures = True` (есть фьючерсы)
2. `score.total >= 65` ИЛИ `skip_threshold=True` (для trigger-детекций)
3. `probability >= 60` ИЛИ `skip_threshold=True`
4. `risk_reward_ratio >= 2.0`
5. Направление LONG по умолчанию (`accumulation_detector.py:540`)

**Оценка реалистичности:**
Набор из 65 баллов достигается легко:
- `oi_growth=15` (OI +10%) + `oi_stability=5` = 20
- `funding_cheap=10` (funding 0%) = 10
- `crowd_bearish=15` (55% shorts) = 15
- `volume_accumulation=10` = 10
- `cross_oi_migration=10` = 10
- **Итого: 65 без единого orderbook фактора**

**Вердикт:** Слишком легко получить сигнал. LONG генерируется даже при нулевом orderbook scoring.

---

### 2. Entry zone при активном накоплении

Код в `risk_calculator.py:163-191`:
```python
zone_pct = Decimal(str(min(2.0, volatility_pct / 3)))  # При ATR 5% = 1.67%
entry_zone_low = current_price * (1 - zone_pct / 100)
entry_limit = entry_zone_low + (entry_zone_high - entry_zone_low) * Decimal("0.3")
```

При цене $100 и ATR 5%:
- `zone_pct` = 1.67%
- `entry_zone_low` = $98.33
- `entry_limit` = $98.33 + ($100 - $98.33) × 0.3 = **$98.83**

**Проблема:** Если детектировано активное накопление (киты покупают), цена скорее пойдёт вверх. Лимитный ордер на 1.17% ниже текущей цены имеет низкий шанс исполнения.

**Вердикт:** Entry zone рассчитана для спокойного рынка, не для активного накопления. Ордер может не исполниться.

---

### 3. Реалистичность TP уровней

Код в `risk_calculator.py:305-366`:
```python
tp1_distance = risk * Decimal(str(self.config.tp1_ratio))  # 1.5x
tp2_distance = risk * Decimal(str(self.config.tp2_ratio))  # 3.0x
tp3_distance = risk * Decimal(str(self.config.tp3_ratio))  # 5.0x
```

При SL=7%:
- TP1 = +10.5%
- TP2 = +21%
- TP3 = +35%

**Анализ:**
- TP1 (+10.5%) — достижим за 1-3 дня для волатильных альткоинов
- TP2 (+21%) — требует 3-7 дней или сильный памп
- TP3 (+35%) — нереалистично для 24-часового окна сигнала

**Вердикт:** TP3 = 35% при `valid_hours=24` — несоответствие. Это уровни для swing-трейдинга (7-30 дней), не для day-трейдинга.

---

### 4. Cooldown между сигналами

Код в `signal_generator.py:161`:
```python
# FIX-A-0: кулдаун отключён — система собирает все сигналы для бектестинга и ML
```

**Текущее состояние:** Cooldown полностью отключён. Метод `_is_recent_signal()` существует (строка 516-523), но не вызывается.

**Проблема накопления:** Если накопление длится 4-6 часов:
- Без cooldown: система генерирует сигнал каждую минуту при каждом OI update
- С cooldown 1 час: только 4-6 сигналов за весь период

**Вердикт:** Для production нужен cooldown. Для ML-сбора — корректно отключён. Но отсутствует флаг режима работы.

---

### 5. Инвалидация сигналов по изменению условий

Код в `signal_generator.py:361-368`:
```python
_valid_hours_map = {
    SignalType.ACCUMULATION: 24,
    ...
}
```

**Критическая проблема:** `valid_hours` — это просто число в JSON. Нет механизма:
- Отслеживания сигналов после генерации
- Проверки изменения OI/funding
- Автоматической инвалидации

**Сценарий:**
1. Сигнал LONG сгенерирован при OI +15%
2. Через 6 часов OI -20% (киты вышли)
3. Сигнал всё ещё "валиден" следующие 18 часов

**Вердикт:** Отсутствует lifecycle management сигналов. Это критический пробел для реальной торговли.

═══════════════════════════════════════════════════════
## БЛОК 2: ГРАНИЧНЫЕ СЛУЧАИ
═══════════════════════════════════════════════════════

### 1. Путь при spot_state=None

Полная трассировка:

```
on_detection()
  → accumulation_detector.analyze()
    → spot_state = self.realtime.get_state(symbol)  # может быть None
    → _calculate_score(symbol, futures_state, spot_state=None)
      → строка 370: if spot_state:  # пропускает весь SPOT scoring
      → score.spot_* = 0 для всех полей
    → _determine_direction(futures_state, spot_state=None)
      → строка 501: if spot_state:  # пропускает SPOT orderbook
    → _calculate_probability(score, futures_state, spot_state=None)
      → строка 601: if spot_state:  # пропускает SPOT bonus
  → _generate_signal()
    → risk_calculator.calculate(spot_state=None)
      → _estimate_volatility() — fallback на futures_atr или 5%
      → _calculate_entry_zone() — combined_imbalance = только FUTURES
```

**Результат:** Сигнал генерируется, но:
- Все SPOT orderbook факторы = 0
- Volatility только из FUTURES (или дефолт 5%)
- Entry zone корректируется только по FUTURES imbalance

**Проблема:** Нет предупреждения что сигнал основан на неполных данных.

---

### 2. Cold start (первые 15 минут)

**Инициализация ATR:**

SPOT (`realtime_monitor.py:206-207`):
```python
if len(state.klines_1h) >= 14:
    state.atr_1h_pct = self._calculate_atr_pct(state.klines_1h)
```

FUTURES (`futures_monitor.py:621`):
```python
await asyncio.sleep(3)  # Ждём инициализации states
await self._load_all_klines()
```

**Timeline:**
- T+0: Start. SPOT ATR = 5% (дефолт), FUTURES ATR = 5% (дефолт)
- T+3s: FUTURES klines загружены → FUTURES ATR = реальный
- T+15min: После 15 закрытых свечей → SPOT ATR = реальный

**Проблема:** В период T+3s до T+15min:
- FUTURES ATR реальный (например 3%)
- SPOT ATR дефолт (5%)
- `avg_atr_pct = (5 + 3) / 2 = 4%` вместо реальных 3%

**Вердикт:** SL будет шире чем нужно, entry zone уже. Сигналы не помечаются как "cold start".

---

### 3. Некорректные данные WebSocket

Код в `realtime_monitor.py:480-481`:
```python
state.best_bid = Decimal(str(bids[0][0])) if bids[0] else Decimal("0")
```

**Сценарии:**
1. `bids[0][0] = "0"` → `best_bid = Decimal("0")` → `mid_price = 0` → деление на 0 в `book_imbalance`
2. `bids[0][0] = "999999999"` → сохраняется как есть → нереальная цена

**Защита отсутствует:**
- Нет проверки `price > 0`
- Нет проверки `price` в разумных пределах относительно последней цены
- Нет валидации `quantity > 0`

**Вердикт:** Malformed WebSocket данные могут вызвать ZeroDivisionError или некорректные расчёты.

---

### 4. funding_history с 1 записью

Код в `accumulation_detector.py:264-274`:
```python
if len(futures_state.funding_history) >= 3:
    recent = futures_state.funding_history[-3:]
    ...
```

**Вердикт:** Безопасно. Guard `>= 3` защищает от IndexError. При < 3 записей `funding_gradient = 0`.

---

### 5. futures_atr_1h_pct = Decimal("0")

Код в `futures_monitor.py:593-596`:
```python
atr_pct = state.futures_atr_1h_pct / 100  # Если 0 → atr_pct = 0
lower_bound = mid * (1 - atr_pct)         # = mid * 1.0 = mid
upper_bound = mid * (1 + atr_pct)         # = mid * 1.0 = mid
```

При `atr_pct = 0`:
- `lower_bound = upper_bound = mid`
- Условие `if lower_bound <= price <= mid` для bids → только price == mid
- Вероятность: 0 (ни один bid не будет точно равен mid)

**Результат:**
```python
state.futures_bid_volume_atr = 0
state.futures_ask_volume_atr = 0
state.futures_book_imbalance_atr = 0  # (0-0)/(0+0) guard → returns 0
```

**Вердикт:** Тихий отказ без логирования. Дефолт `Decimal("5")` защищает, но если значение обнулится — проблема скрыта.

═══════════════════════════════════════════════════════
## БЛОК 3: ПРОИЗВОДИТЕЛЬНОСТЬ
═══════════════════════════════════════════════════════

### 1. _cleanup_old_trades

Код в `realtime_monitor.py:551-556`:
```python
def _cleanup_old_trades(self, state: SymbolState, now_ms: int):
    state.trades_1m = [t for t in state.trades_1m if now_ms - t.time < 60_000]
    state.trades_5m = [t for t in state.trades_5m if now_ms - t.time < 300_000]
```

**Расчёт нагрузки:**
- Вызов: каждый трейд (строка 429)
- Частота: 500 символов × 10 trades/sec = 5000 вызовов/сек
- Операции: 2 list comprehension × 5000 = 10000/сек
- Размер списка trades_5m: до 3000 элементов (5min × 10 trades/sec)

**Оценка:** O(n) × 10000/сек где n до 3000 = 30 млн операций/сек в worst case.

**Вердикт:** Неэффективно. Должен использоваться `collections.deque` с `maxlen` или периодическая очистка (раз в секунду), не на каждом трейде.

---

### 2. _calculate_atr_volumes

Код в `realtime_monitor.py:590-613`:
```python
state.bid_volume_atr = sum(
    p * q for p, q in state.raw_bids
    if lower_bound <= p <= mid
)
```

**Расчёт:**
- Вызов: каждый depth update (100ms)
- Частота: 500 символов × 10/сек = 5000 вызовов/сек
- Итерации: 20 bids + 20 asks = 40 × 5000 = 200,000/сек

**Оценка:** Lightweight операции (сравнение + умножение Decimal). ~200K ops/sec приемлемо.

**Вердикт:** Не критично, но можно оптимизировать фильтрацией на стороне хранения.

---

### 3. _calculate_atr_pct с Decimal

Код в `realtime_monitor.py:615-661`:

**Расчёт:**
- Вызов: при закрытии свечи (1 раз/мин/символ)
- Частота: 500 символов / 60 сек ≈ 8 вызовов/сек
- Операции Decimal: ~60 per call (true range calculation)

**Оценка:** 8 × 60 = 480 Decimal операций/сек. Незначительно.

**Вердикт:** Не проблема.

---

### 4. Decimal конвертация в depth

Код в `realtime_monitor.py:488`:
```python
state.raw_bids = [(Decimal(str(p)), Decimal(str(q))) for p, q in bids]
```

**Расчёт:**
- Вызов: каждый depth update
- Частота: 5000/сек
- Конвертации: 20 × 2 × 2 (bid/ask, price/qty) = 80 per call
- Всего: 5000 × 80 = **400,000 Decimal(str(x)) в секунду**

`Decimal(str(x))` — медленная операция (string parsing + decimal construction).

**Benchmark estimate:** ~10μs per conversion = 400,000 × 10μs = 4 секунды CPU/сек

**Вердикт:** **Это узкое место.** 400% CPU utilization только на Decimal конвертацию. Следует использовать `float` для промежуточных расчётов или кэшировать конвертацию.

═══════════════════════════════════════════════════════
## БЛОК 4: СОГЛАСОВАННОСТЬ ДАННЫХ
═══════════════════════════════════════════════════════

### 1. SPOT ATR vs FUTURES ATR timing

**Источники:**
- SPOT ATR: WebSocket kline закрытие (`realtime_monitor.py:543-544`)
- FUTURES ATR: REST каждые 60 сек (`futures_monitor.py:627`)

**Gap на старте:**
```
T+0:    SPOT ATR = 5% (default)     FUTURES ATR = 5% (default)
T+3s:   SPOT ATR = 5% (default)     FUTURES ATR = 2.5% (real)
T+15min: SPOT ATR = 2.8% (real)     FUTURES ATR = 2.5% (real)
```

**Влияние в risk_calculator.py:261:**
```python
avg_atr_pct = (spot_atr_pct + futures_atr_pct) / 2
# T+3s: (5 + 2.5) / 2 = 3.75% вместо реальных ~2.5%
```

**Вердикт:** SL на 50% шире реального первые 15 минут. Сигналы не помечены.

---

### 2. Устаревшие OI данные

Код в `futures_monitor.py:386-391`:
```python
async def _oi_monitor_loop(self):
    while self._running:
        await self._update_all_oi()
        await asyncio.sleep(settings.futures.oi_update_interval_sec)  # 60s default
```

**При сбое REST:**
- `_fetch_oi()` ловит исключение (строка 973-978)
- Логирует warning
- `state.oi_change_*` остаётся от предыдущего успешного запроса

**Нет:**
- Timeout на свежесть данных
- Флага `stale_data`
- Exponential backoff

**Вердикт:** Система может работать с OI данными часовой давности без предупреждения.

---

### 3. MIN_SPOT_VOLUME_USD согласованность

**AccumulationDetector (accumulation_detector.py:70):**
```python
MIN_SPOT_VOLUME_USD = 1000
```

**FuturesState.futures_book_imbalance_atr (futures_monitor.py:207-208):**
```python
if bid < 100 or ask < 100:
    return Decimal("0")
```

**SymbolState (нет эквивалентной проверки в property):**
`book_imbalance_atr` не имеет встроенной проверки минимального объёма.

**Проверка в AccumulationDetector (строка 377):**
```python
if spot_total >= self.MIN_SPOT_VOLUME_USD:
```

**Пример:** bid=$800, ask=$800, total=$1600
- `book_imbalance_atr` рассчитывается (нет встроенного guard)
- AccumulationDetector: $1600 >= $1000 → используется

**Вердикт:** Согласованность есть, но разная логика: FUTURES имеет guard в property, SPOT — внешняя проверка. Inconsistent design.

---

### 4. Мониторинг свежести WebSocket

**SPOT WebSocket (realtime_monitor.py):**
```python
state.last_update = datetime.now()  # строка 434, 504, 546
```

**FUTURES WebSocket (futures_monitor.py):**
```python
state.last_depth_time = data.get("E") or data.get("lastUpdateId", 0)  # строка 562
```

**Отсутствует:**
- Периодическая проверка `last_update > N секунд назад`
- Health check для WebSocket соединений
- Автоматический реконнект при stale data

**Сценарий:**
1. WebSocket умирает без ConnectionClosed (сеть таймаут)
2. `_running = True`, но сообщения не приходят
3. `state.last_update` остаётся старым
4. Система продолжает генерировать сигналы на stale данных

**Вердикт:** Нет механизма детекции stale data. Критический пробел для production.

═══════════════════════════════════════════════════════
## БЛОК 5: БЕЗОПАСНОСТЬ И НАДЁЖНОСТЬ
═══════════════════════════════════════════════════════

### 1. signal_id формат и коллизии

Код в `signal_generator.py:371`:
```python
signal_id=str(uuid.uuid4())[:8]
```

**Анализ UUID4:**
```python
>>> str(uuid.uuid4())
'a1b2c3d4-e5f6-4a7b-8c9d-e0f1a2b3c4d5'
>>> str(uuid.uuid4())[:8]
'a1b2c3d4'  # 8 hex символов, дефис на позиции 8
```

**Пространство:** 16^8 = 4,294,967,296 (4.3 млрд)

**Birthday paradox:** 50% вероятность коллизии при ~82,000 сигналах.
При 1000 сигналов/день = ~82 дней до вероятной коллизии.

**Вердикт:** Недостаточно для долгосрочной работы. Нужно 12+ символов или UUID целиком.

---

### 2. Symbol в URLs

Код в `signal_generator.py:350-354`:
```python
links = {
    "binance_futures": f"https://www.binance.com/ru/futures/{symbol}",
    "tradingview": f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}.P",
    "coinglass": f"https://www.coinglass.com/tv/{base_symbol}_USDT",
}
```

**Источник symbol:** Binance API → `exchangeInfo` → только валидные символы (BTCUSDT формат).

**Теоретический риск:** Если symbol содержит `../` или `?param=` — URL manipulation.

**Вердикт:** Низкий риск (Binance валидирует), но нет явной проверки `symbol.isalnum()`.

---

### 3. HTML injection в signal_formatter

Код в `signal_formatter.py`:

**Экранируется (строки 116, 127):**
```python
safe_evidence = html.escape(str(evidence))
safe_scenario = html.escape(str(scenario))
```

**НЕ экранируется (строка 31):**
```python
f"🎯 <b>ТОРГОВЫЙ СИГНАЛ: {signal.symbol}</b>"
```

**Также не экранируется:**
- Строка 41-42: `${signal.entry_zone_low}`, `${signal.entry_zone_high}`
- Строка 46: `${signal.stop_loss}`
- Строка 53: `${tp.price}`

**Анализ:** Все эти значения — Decimal числа или символы от Binance. XSS нереалистичен.

**Вердикт:** Теоретическая уязвимость, практически неэксплуатируемая. Но inconsistent — часть данных экранируется, часть нет.

---

### 4. Свежесть cross-exchange данных

Код в `signal_generator.py:483-491`:
```python
price_spread = self.state.get_price_spread(symbol)
funding_div = self.state.get_funding_divergence(symbol)
```

**StateStore API не проверяет:**
- Возраст данных
- Время последнего обновления
- Флаг stale

**Вердикт:** Cross-exchange данные могут быть произвольно старыми. Нет timestamp validation.

═══════════════════════════════════════════════════════
## БЛОК 6: ИТОГОВАЯ ТАБЛИЦА НОВЫХ ПРОБЛЕМ
═══════════════════════════════════════════════════════

| # | Файл | Метод | Строка | Критичность | Описание |
|---|------|-------|--------|-------------|----------|
| 1 | signal_generator.py | _generate_signal | - | **5** | Отсутствует lifecycle management сигналов — нет инвалидации при изменении условий |
| 2 | realtime_monitor.py | _process_depth | 480-481 | **5** | Нет валидации WebSocket данных — `bid[0][0]="0"` вызовет ZeroDivisionError |
| 3 | realtime_monitor.py | _process_depth | 488 | **4** | 400K Decimal(str()) конвертаций/сек — CPU bottleneck |
| 4 | realtime_monitor.py | _cleanup_old_trades | 554-556 | **4** | List comprehension на каждом трейде — O(n) × 5000/сек |
| 5 | futures_monitor.py | _calculate_futures_atr_volumes | 593-596 | **4** | При ATR=0 тихо возвращает 0 без логирования |
| 6 | signal_generator.py | - | - | **4** | Нет детекции stale data — сигналы на устаревших данных |
| 7 | risk_calculator.py | _calculate_entry_zone | 178-191 | **3** | Entry limit 1%+ ниже цены при активном накоплении — низкий fill rate |
| 8 | signal_generator.py | _generate_signal | 361-368 | **3** | TP3=35% при valid_hours=24 — несоответствие timeframe |
| 9 | signal_generator.py | _generate_signal | 371 | **3** | signal_id 8 символов — коллизия за ~82 дня при 1000 сигналов/день |
| 10 | accumulation_detector.py | _calculate_score | - | **3** | 65 баллов достигается без orderbook — слишком низкий порог |
| 11 | realtime_monitor.py, futures_monitor.py | - | - | **3** | Нет health check для WebSocket — stale data без детекции |
| 12 | risk_calculator.py | _estimate_volatility | 126-161 | **3** | На старте SPOT ATR=5%, FUTURES ATR=real — avg_atr искажён 15 минут |
| 13 | signal_formatter.py | format_signal | 31 | **2** | signal.symbol не экранируется (теоретический XSS) |
| 14 | futures_monitor.py | futures_book_imbalance_atr | 207-208 | **2** | FUTURES имеет guard $100, SPOT нет — inconsistent design |
| 15 | state_store (cross) | get_* | - | **2** | Нет проверки свежести cross-exchange данных |
| 16 | accumulation_detector.py | analyze | 143-145 | **2** | При spot_state=None сигнал генерируется без предупреждения о неполных данных |

**Критичность:**
- **5** = краш или заведомо неверный результат
- **4** = логическая ошибка влияющая на торговые решения
- **3** = деградация качества или непредсказуемое поведение
- **2** = технический долг / code smell

═══════════════════════════════════════════════════════
## РЕКОМЕНДАЦИИ ПО ПРИОРИТЕТУ
═══════════════════════════════════════════════════════

### Критические (блокеры для production):
1. **#1** — Signal lifecycle management
2. **#2** — WebSocket data validation
3. **#6** — Stale data detection

### Высокий приоритет (влияют на качество сигналов):
4. **#3** — Decimal performance
5. **#4** — Trade cleanup optimization
6. **#5** — ATR=0 silent failure

### Средний приоритет (улучшение качества):
7. **#7-12** — Entry zone, TP timing, signal_id, thresholds, health checks

### Низкий приоритет (технический долг):
8. **#13-16** — Consistency improvements
