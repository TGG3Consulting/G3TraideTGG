# КРИТИЧЕСКИЙ БАГ: СИГНАЛЫ В TELEGRAM, НО НЕ В ЛОГАХ

**Дата:** 2026-02-18
**Статус:** БАГ НАЙДЕН

---

## ДОКАЗАТЕЛЬСТВО БАГА

| Параметр | В логах (последний) | В Telegram (последний) |
|----------|---------------------|------------------------|
| signal_id | 624eb921 | 2491d9c1 |
| symbol | XUSDUSDT | FOGOUSDT |
| timestamp | 18:34:46 | 19:01:30 |
| trigger | VOLUME_SPIKE_HIGH | ORDERBOOK_IMBALANCE |

**FOGOUSDT #2491d9c1 отправлен в Telegram, но НЕ записан в signals.jsonl!**

---

## ПРИЧИНА БАГА

### Место 1: signal_logger.py НЕ выбрасывает исключения

```python
# signal_logger.py:100-102
def log_signal(self, signal, ...):
    if not self._started or not self._file:
        logger.warning("signal_logger_not_started", ...)
        return  # ← ТИХО ВОЗВРАЩАЕТ None, БЕЗ EXCEPTION!

    try:
        # ... логирование ...
    except Exception as e:
        logger.error("signal_log_failed", ...)
        # ← НЕТ re-raise! ОШИБКА ПРОГЛАТЫВАЕТСЯ!
```

### Место 2: screener.py отправляет в Telegram НЕЗАВИСИМО от логирования

```python
# screener.py:545-582
if signal:
    try:
        self.signal_logger.log_signal(...)  # ← МОЖЕТ ТИХО НЕ РАБОТАТЬ!
    except Exception as log_err:
        logger.error(...)  # ← НИКОГДА НЕ ВЫЗЫВАЕТСЯ (нет exception)

    # ↓ ВСЕГДА ВЫПОЛНЯЕТСЯ, ДАЖЕ ЕСЛИ ЛОГ НЕ ЗАПИСАЛСЯ!
    if self.telegram_notifier:
        signal_text = self.signal_formatter.format_signal(signal)
        asyncio.create_task(self.telegram_notifier.send_trade_signal(signal_text))
```

---

## ВОЗМОЖНЫЕ ПРИЧИНЫ ПОЧЕМУ log_signal() НЕ РАБОТАЕТ

1. **signal_logger не стартовал** (`self._started = False`)
2. **Файл закрылся** (`self._file = None`)
3. **Ошибка сериализации** в `_build_record()` или `json.dumps()`
4. **I/O ошибка** при записи в файл
5. **Буфер не сбросился** (хотя `flush()` вызывается)

---

## ДИАГРАММА ТЕКУЩЕГО ПОТОКА (НЕПРАВИЛЬНАЯ)

```
Detection
    │
    ▼
signal_generator.on_detection()
    │
    ▼
signal != None
    │
    ├──► log_signal() ──► ТИХО ПАДАЕТ? ──► Ничего
    │                     (без exception)
    │
    └──► send_trade_signal() ──► Telegram ✓
```

---

## ДИАГРАММА ПРАВИЛЬНОГО ПОТОКА

```
Detection
    │
    ▼
signal_generator.on_detection()
    │
    ▼
signal != None
    │
    ▼
log_signal() ──┬──► Успех ──► signals.jsonl ✓
               │                    │
               │                    ▼
               │              send_trade_signal() ──► Telegram ✓
               │
               └──► Ошибка ──► RAISE EXCEPTION!
                                    │
                                    ▼
                              НЕ ОТПРАВЛЯТЬ В TELEGRAM!
                              (или отправлять с предупреждением)
```

---

## ПЛАН ИСПРАВЛЕНИЯ

### Вариант A: Блокировать Telegram если лог не записался (строгий)

```python
# screener.py:541-582 — ЗАМЕНИТЬ НА:
if signal:
    log_success = False
    try:
        futures_state = self.futures_monitor.get_state(detection.symbol)
        spot_state = self.realtime_monitor.get_state(detection.symbol)
        # ...
        self.signal_logger.log_signal(...)
        log_success = True  # ← Только если успешно!
    except Exception as log_err:
        logger.error("signal_logging_failed", error=str(log_err), signal_id=signal.signal_id)

    # Отправляем в Telegram ТОЛЬКО если лог записался!
    if log_success and self.telegram_notifier:
        signal_text = self.signal_formatter.format_signal(signal)
        asyncio.create_task(self.telegram_notifier.send_trade_signal(signal_text))
    elif not log_success:
        logger.error("TELEGRAM_BLOCKED_LOG_FAILED", signal_id=signal.signal_id)
```

### Вариант B: log_signal() должен выбрасывать исключения

```python
# signal_logger.py:100-102 — ЗАМЕНИТЬ НА:
if not self._started or not self._file:
    raise RuntimeError(f"SignalLogger not started! started={self._started}, file={self._file}")

# signal_logger.py:125-126 — ЗАМЕНИТЬ НА:
except Exception as e:
    logger.error("signal_log_failed", error=str(e), signal_id=signal.signal_id)
    raise  # ← RE-RAISE!
```

### Вариант C: log_signal() возвращает bool (менее строгий)

```python
# signal_logger.py
def log_signal(self, ...) -> bool:
    if not self._started or not self._file:
        logger.warning("signal_logger_not_started", ...)
        return False  # ← Вернуть False вместо None

    try:
        # ...
        return True  # ← Успех
    except Exception as e:
        logger.error("signal_log_failed", ...)
        return False  # ← Неудача

# screener.py
log_success = self.signal_logger.log_signal(...)
if log_success and self.telegram_notifier:
    # отправить в TG
```

---

## РЕКОМЕНДАЦИЯ

**Вариант B** — log_signal() должен re-raise исключения.

Это обеспечит:
1. Явную ошибку которую можно поймать
2. Единую точку контроля в screener.py
3. Возможность решить — отправлять в TG или нет

---

## ДОПОЛНИТЕЛЬНО: ПРОВЕРИТЬ

1. Почему console.log устарел (последние записи от 17.02)?
2. Куда пишутся актуальные логи приложения?
3. Есть ли сообщения "signal_log_failed" для FOGOUSDT?

```bash
# Поискать ошибки логирования
grep -i "signal_log_failed\|signal_logger_not_started" <актуальный_лог>
```

---

**ИТОГ:** Сигналы отправляются в Telegram БЕЗ гарантии записи в лог из-за тихого проглатывания ошибок в signal_logger.py
