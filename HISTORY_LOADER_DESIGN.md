# ДИЗАЙН: Загрузчик Исторических Данных

## 1. КОНФИГУРАЦИЯ

```yaml
# config/config.yaml
history:
  enabled: true
  download_hours: 2          # Сколько часов истории загружать
  parallel_requests: 10      # Параллельность загрузки
  request_delay_ms: 100      # Задержка между запросами (rate limit)

  # Что загружать при старте
  load_klines: true          # Для avg_volume baseline
  load_oi_history: true      # Для oi_change_1h
  load_funding_history: true # Для funding gradient
  load_trades: false         # Для wash trading (опционально, много данных)
```

## 2. АРХИТЕКТУРА

```
HistoryLoader
├── _load_klines_history()      # Binance SPOT: /api/v3/klines
├── _load_oi_history()          # Binance Futures: /futures/data/openInterestHist
├── _load_funding_history()     # Binance Futures: /fapi/v1/fundingRate
├── _load_trades_history()      # Binance Futures: /fapi/v1/aggTrades (опционально)
└── _load_cross_exchange()      # Для каждой биржи свой loader

CrossExchangeHistoryLoader
├── binance/  → использует BinanceFutures connector
├── bybit/    → /v5/market/kline, /v5/market/funding/history
├── okx/      → /api/v5/market/candles, /api/v5/public/funding-rate-history
└── ...       → каждая биржа отдельно
```

## 3. ИНТЕГРАЦИЯ

### 3.1 В ManipulationScreener.start():

```python
async def start(self):
    # ... existing code ...

    # НОВОЕ: Загрузка исторических данных
    if settings.history.enabled:
        logger.info("Phase 0: Loading historical data...")
        history_loader = HistoryLoader(settings.history)

        # Загружаем параллельно для всех бирж
        await history_loader.load_all(
            symbols=symbols_to_monitor,
            realtime_monitor=self.realtime_monitor,  # Для кэширования klines
            futures_monitor=self.futures_monitor,     # Для OI/funding
            cross_state=self.cross_state              # Для cross-exchange
        )
```

### 3.2 В FuturesMonitor — добавить загрузку funding history:

```python
async def _load_historical_funding(self):
    """
    Загрузить историческую funding rate за последние 24 часа.

    Endpoint: /fapi/v1/fundingRate
    Интервал: 8 часов (3 записи в сутки)
    """
    url = f"{self.FUTURES_URL}/fapi/v1/fundingRate"

    for symbol in self._futures_symbols:
        params = {
            "symbol": symbol,
            "limit": 9,  # 9 * 8h = 72 часа = 3 дня
        }
        # ... fetch and populate state.funding_history
```

## 4. НОВЫЕ ФАЙЛЫ

```
src/screener/history_loader.py    # Главный загрузчик
src/screener/history_config.py    # Конфигурация
config/config.yaml                # Добавить секцию history:
```

## 5. API ENDPOINTS ДЛЯ ИСТОРИИ

### Binance (приоритет — основная биржа)

| Данные | Endpoint | Лимит | Период |
|--------|----------|-------|--------|
| Klines | /api/v3/klines | 1500 | 1m = 25 часов |
| OI History | /futures/data/openInterestHist | 500 | 5m = ~42 часа |
| Funding History | /fapi/v1/fundingRate | 1000 | 8h = ~333 дня |
| Trades | /fapi/v1/aggTrades | 1000 | по времени |

### Формулы расчёта:

```python
# Сколько записей нужно для N часов:
klines_count = download_hours * 60                    # 1min candles
oi_count = download_hours * 12                        # 5min periods
funding_count = max(3, download_hours // 8)           # 8h periods (мин 3 для gradient)
trades_count = download_hours * 60 * avg_trades_min   # зависит от активности
```

## 6. ПЛАН РЕАЛИЗАЦИИ

### Этап 1: Funding History (критично для gradient)
1. Добавить `_load_historical_funding()` в FuturesMonitor
2. Вызвать в `start()` после `_update_all_funding()`
3. Тест: проверить что gradient работает сразу

### Этап 2: Конфигурация
1. Добавить секцию `history:` в config.yaml
2. Создать `HistoryConfig` dataclass
3. Интегрировать в settings

### Этап 3: HistoryLoader класс
1. Создать `src/screener/history_loader.py`
2. Унифицировать загрузку всех типов данных
3. Добавить progress logging

### Этап 4: Cross-Exchange
1. Добавить исторические методы в каждый коннектор
2. Интегрировать в CrossExchangeStateStore
3. Тест: price divergence работает сразу

## 7. ВАЖНЫЕ ОГРАНИЧЕНИЯ

1. **Rate Limits**: Binance = 1200 req/min, нужен delay
2. **Память**: trades история может быть БОЛЬШОЙ
3. **Время старта**: загрузка может занять 1-5 минут
4. **Fallback**: если загрузка не удалась — работаем как раньше

## 8. МЕТРИКИ УСПЕХА

После реализации система должна:
- [ ] Детектировать volume spikes с первой секунды
- [ ] Рассчитывать oi_change_1h сразу (не через час)
- [ ] Показывать funding gradient сразу (не через 24ч)
- [ ] Cross-exchange divergence работает с первой минуты
