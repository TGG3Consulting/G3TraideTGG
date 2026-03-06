# ПЛАН ПЕРЕХОДА НА ПРОД (Telegram Alerts)

**Дата создания:** 2026-03-05
**Статус:** ОЖИДАЕТ РЕАЛИЗАЦИИ

---

## АБСОЛЮТНЫЕ ЗАКОНЫ (НАРУШЕНИЕ ЗАПРЕЩЕНО)

```
!!! КАЖДОЕ СЛОВО ПОЛЬЗОВАТЕЛЯ - СУХОЙ ЗАКОН !!!

ЗАКОН 1: ЧТЕНИЕ ПЕРЕД РАБОТОЙ
   Каждая новая сессия ОБЯЗАНА прочитать ОБА файла перед любой работой:
   - CLAUDE_SESSION.md (состояние сессии)
   - TELEGRAM_PROD_PLAN.md (этот файл)
   Без прочтения этих файлов - РАБОТА ЗАПРЕЩЕНА.

ЗАКОН 2: ТОЛЬКО ДОПИСЫВАТЬ
   Этот файл НЕЛЬЗЯ стирать и писать заново.
   ТОЛЬКО ДОПИСЫВАТЬ новую информацию.
   История должна сохраняться ПОЛНОСТЬЮ.

ЗАКОН 3: ПОСЛЕ КАЖДОЙ РЕАЛИЗАЦИИ - ЗАПИСЬ
   После реализации КАЖДОГО файла/функции записать:
   - Дата и время
   - Что именно реализовано
   - Почему именно так (обоснование решений)
   - В каких файлах изменения (с номерами строк)
   - Что осталось сделать (следующие шаги)

ЗАКОН 4: ФОРМАТ ЗАПИСИ ПОСЛЕ КОДИНГА
   === РЕАЛИЗАЦИЯ: [название] ===
   Дата: YYYY-MM-DD HH:MM
   Файл: [путь к файлу]
   Что сделано: [описание]
   Почему так: [обоснование]
   Строки: [номера строк]
   Осталось: [что дальше]
   ==============================
```

---

## ВОПРОСЫ И ОТВЕТЫ (согласовано)

| # | Вопрос | Ответ |
|---|--------|-------|
| 1 | Режим работы прода | **A) Polling** - ручной запуск или cron |
| 2 | Источник данных | **Кэш + проверка актуальности**, БЕЗ МОКОВ |
| 3 | Формат алерта | **Утверждён** (см. ниже) |
| 4 | Несколько сигналов одновременно | **Каждая стратегия = отдельный алерт** |
| 5 | Фильтры в проде | **Через CLI как сейчас** |
| 6 | Символы для прода | **Как сейчас**: --top N или --symbols |
| 7 | Telegram бот | Данные будут предоставлены в конце |
| 8 | Дата сигнала | **A) Вчерашняя закрытая свеча** (автоматически) |
| 9 | Параметры стратегий | **B) Вынести в config.json** с текущими значениями |
| 10 | Библиотека Telegram | **A) python-telegram-bot** |
| 11 | Время жизни кнопки | **B) Бессрочно** |
| 12 | Режим работы для callback | **B) Отдельный daemon 24/7** |
| 13 | Логирование | **A) Консоль + файл** logs/telegram_YYYYMMDD.log |
| 14 | Защита от дубликатов | **A) Файл-лог** sent_signals.log |
| 15 | DD Tracking | **Ручной** - бот не отслеживает DD |
| 16 | Стратегии | **ВСЕ 5** (ls_fade, momentum, reversal, mean_reversion, momentum_ls) |
| 17 | Язык алертов | **Русский** (кроме терминов) |

---

## КАРТА ПАРАМЕТРОВ СИСТЕМЫ

### Где что обрабатывается (проверено в коде)

| Параметр | Где задаётся | Где обрабатывается | Для прода |
|----------|-------------|-------------------|-----------|
| `--sl` | run_all.py:436 | strategies/base.py:70 | ✓ Работает |
| `--tp` | run_all.py:437 | strategies/base.py:71 | ✓ Работает |
| `--max-hold` | run_all.py:438 | strategy_runner.py:1012 | ⚠️ Только бэктест |
| `--dedup-days` | run_all.py:439 | chain_processor.py | ✓ Генератор |
| `--daily-max-dd` | run_all.py:446 | strategy_runner.py:915 | ❌ Не нужен (ручной DD) |
| `--monthly-max-dd` | run_all.py:449 | strategy_runner.py:897 | ❌ Не нужен (ручной DD) |
| `--coin-regime` | run_all.py:462 | strategy_runner.py:807-840 | ✓ Выносим в signal_filter.py |
| `--vol-filter-low` | run_all.py:464 | strategy_runner.py:843-864 | ✓ Выносим в signal_filter.py |
| `--vol-filter-high` | run_all.py:465 | strategy_runner.py:867-884 | ✓ Выносим в signal_filter.py |
| `--dynamic-size` | run_all.py:455 | strategy_runner.py:991-1004 | ❌ Не для прода |
| `--position-mode` | run_all.py:440 | strategy_runner.py:958-988 | ❌ Только бэктест |

---

## КРИТИЧЕСКИЕ МАТРИЦЫ (копируем в signal_filter.py)

### COIN_REGIME_MATRIX (strategy_runner.py:53-89)

```python
COIN_REGIME_MATRIX = {
    'STRONG_BULL': {
        'ls_fade': 'DYN',        # 29.5% WR, +569% PnL
        'momentum': 'DYN',       # 34.3% WR, +2088% PnL
        'reversal': 'OFF',       # 27.4% WR, -6% PnL
        'mean_reversion': 'DYN', # 31.0% WR, +644% PnL
        'momentum_ls': 'DYN',    # 30.2% WR, +375% PnL
    },
    'BULL': {
        'ls_fade': 'DYN',        # 34.2% WR, +2253% PnL
        'momentum': 'DYN',       # 31.2% WR, +1384% PnL
        'reversal': 'OFF',       # 25.9% WR, -104% PnL
        'mean_reversion': 'FULL',# 43.1% WR, +1171% PnL
        'momentum_ls': 'DYN',    # 32.8% WR, +803% PnL
    },
    'SIDEWAYS': {
        'ls_fade': 'DYN',        # 34.8% WR, +2735% PnL
        'momentum': 'DYN',       # 30.5% WR, +1190% PnL
        'reversal': 'OFF',       # 24.4% WR, -169% PnL
        'mean_reversion': 'FULL',# 52.3% WR, +279% PnL
        'momentum_ls': 'DYN',    # 34.8% WR, +1659% PnL
    },
    'BEAR': {
        'ls_fade': 'DYN',        # 33.2% WR, +2659% PnL
        'momentum': 'FULL',      # 36.3% WR, +4052% PnL
        'reversal': 'OFF',       # 21.5% WR, -749% PnL
        'mean_reversion': 'OFF', # 25.3% WR, -15% PnL
        'momentum_ls': 'FULL',   # 37.8% WR, +4281% PnL
    },
    'STRONG_BEAR': {
        'ls_fade': 'OFF',        # 25.3% WR, -521% PnL
        'momentum': 'OFF',       # 27.8% WR, +103% PnL
        'reversal': 'OFF',       # 24.1% WR, -101% PnL
        'mean_reversion': 'DYN', # 29.8% WR, +26% PnL
        'momentum_ls': 'OFF',    # 27.7% WR, +102% PnL
    },
}
```

### VOL_FILTER_THRESHOLDS (strategy_runner.py:95-101)

```python
VOL_FILTER_THRESHOLDS = {
    'ls_fade':        {'vol_low': 4.5, 'vol_high': 22.0},
    'mean_reversion': {'vol_low': None, 'vol_high': 25.0},
    'momentum':       {'vol_low': 2.0, 'vol_high': 25.0},
    'momentum_ls':    {'vol_low': 4.5, 'vol_high': 25.0},
    'reversal':       {'vol_low': 7.5, 'vol_high': 21.0},
}
```

### MONTH_DATA (strategy_runner.py:33-39)

```python
MONTH_DATA = {
    'ls_fade': {1: (105, -27), 2: (-29, -68), 3: (49, -29), 4: (71, -30), 5: (13, -26), 6: (106, -13), 7: (40, -37), 8: (13, -33), 9: (-20, -48), 10: (34, -30), 11: (-10, -45), 12: (42, -13)},
    'momentum': {1: (43, -30), 2: (28, -35), 3: (-15, -27), 4: (8, -28), 5: (-56, -62), 6: (63, -16), 7: (62, -14), 8: (-43, -68), 9: (-25, -31), 10: (-24, -38), 11: (72, -17), 12: (16, -17)},
    'reversal': {1: (5, -8), 2: (-8, -9), 3: (19, -4), 4: (14, -8), 5: (16, -2), 6: (-11, -12), 7: (-1, -11), 8: (15, -5), 9: (0, -5), 10: (-15, -19), 11: (-1, -12), 12: (-9, -13)},
    'mean_reversion': {1: (13, -2), 2: (2, -3), 3: (11, -3), 4: (8, -5), 5: (24, -1), 6: (3, 0), 7: (-3, -13), 8: (17, -1), 9: (3, -4), 10: (4, 0), 11: (-3, -15), 12: (10, -6)},
    'momentum_ls': {1: (69, -18), 2: (10, -25), 3: (6, -19), 4: (23, -14), 5: (-34, -40), 6: (75, -13), 7: (34, -14), 8: (-21, -46), 9: (-15, -21), 10: (-6, -22), 11: (26, -14), 12: (22, -13)},
}
```

### DAY_DATA (strategy_runner.py:41-47)

```python
DAY_DATA = {  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    'ls_fade': {0: (63, -15), 1: (77, -16), 2: (43, -18), 3: (64, -17), 4: (41, -26), 5: (59, -19), 6: (69, -21)},
    'momentum': {0: (-9, -35), 1: (7, -27), 2: (-27, -47), 3: (36, -22), 4: (59, -16), 5: (50, -21), 6: (13, -32)},
    'reversal': {0: (0, -10), 1: (4, -12), 2: (9, -7), 3: (13, -8), 4: (8, -16), 5: (-2, -15), 6: (-4, -7)},
    'mean_reversion': {0: (16, -3), 1: (21, -4), 2: (18, -4), 3: (10, -4), 4: (-2, -7), 5: (8, -5), 6: (18, -4)},
    'momentum_ls': {0: (14, -14), 1: (30, -17), 2: (-12, -34), 3: (38, -13), 4: (32, -15), 5: (47, -13), 6: (39, -24)},
}
```

---

## АРХИТЕКТУРА

```
┌─────────────────────────────────────────────────────────────┐
│                    telegram_runner.py                        │
│         (запускается вручную или по cron)                   │
├─────────────────────────────────────────────────────────────┤
│  1. Парсинг CLI (--symbols, --top, --coin-regime, etc.)    │
│  2. Проверка кэша (актуальность данных)                    │
│  3. Загрузка данных (hybrid_downloader)                    │
│  4. Генерация сигналов (все 5 стратегий)                   │
│  5. Фильтрация (coin_regime, vol_filter, liquidity)        │
│  6. Проверка дубликатов (sent_signals.log)                 │
│  7. Сохранение данных для callback (signal_cache.json)     │
│  8. Отправка алертов в группу                              │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                telegram_callback_daemon.py                   │
│              (работает 24/7 как сервис)                     │
├─────────────────────────────────────────────────────────────┤
│  1. Polling Telegram API (ждёт callback)                   │
│  2. При нажатии кнопки → читает signal_cache.json          │
│  3. Отправляет полные данные в ЛС пользователю             │
└─────────────────────────────────────────────────────────────┘
```

---

## СТРУКТУРА ФАЙЛОВ

```
GenerateHistorySignals/
├── telegram_runner.py          # НОВЫЙ - отправка сигналов
├── telegram_callback_daemon.py # НОВЫЙ - обработка callback 24/7
├── signal_filter.py            # НОВЫЙ - логика фильтрации
├── telegram_sender.py          # НОВЫЙ - функции Telegram
├── config.json                 # НОВЫЙ - настройки
├── signal_cache.json           # АВТОГЕНЕРИРУЕТСЯ - данные для callback
├── sent_signals.log            # АВТОГЕНЕРИРУЕТСЯ - защита от дубликатов
├── logs/                       # АВТОГЕНЕРИРУЕТСЯ - логи
│   └── telegram_YYYYMMDD.log
│
├── run_all.py                  # БЕЗ ИЗМЕНЕНИЙ
├── strategy_runner.py          # БЕЗ ИЗМЕНЕНИЙ
├── hybrid_downloader.py        # БЕЗ ИЗМЕНЕНИЙ
├── strategies/                 # БЕЗ ИЗМЕНЕНИЙ
└── ...
```

---

## НОВЫЕ ФАЙЛЫ: ДЕТАЛИ

### 1. config.json

```json
{
    "telegram": {
        "bot_token": "ЗАПОЛНИТЬ",
        "chat_id": "ЗАПОЛНИТЬ",
        "admin_ids": []
    },
    "strategy_params": {
        "ls_extreme": 0.65,
        "momentum_threshold": 5.0,
        "oversold_threshold": -10.0,
        "overbought_threshold": 15.0,
        "crowd_bearish": 0.55,
        "crowd_bullish": 0.60,
        "ls_confirm": 0.60
    },
    "defaults": {
        "sl_pct": 4.0,
        "tp_pct": 10.0,
        "max_hold_days": 14,
        "dedup_days": 3,
        "order_size_usd": 100.0,
        "taker_fee_pct": 0.05,
        "coin_regime_lookback": 14
    }
}
```

---

### 2. signal_filter.py

**Что копируем из strategy_runner.py (БЕЗ изменений):**

| Что | Строки в strategy_runner.py |
|-----|----------------------------|
| `COIN_REGIME_MATRIX` | 53-89 |
| `VOL_FILTER_THRESHOLDS` | 95-101 |
| `MONTH_DATA` | 33-39 |
| `DAY_DATA` | 41-47 |
| `calculate_coin_regime()` | 104-169 |
| `calculate_volatility()` | 172-234 |

**Новая функция:**

```python
@dataclass
class FilterResult:
    passed: bool
    skip_reason: Optional[str]  # "skipped_regime", "skipped_vol_low", etc.
    coin_regime: str            # "STRONG_BULL", "BEAR", etc.
    coin_volatility: float      # ATR%
    regime_action: str          # "OFF", "DYN", "FULL"

def filter_signal(
    signal: Signal,
    candles: List[DailyCandle],
    strategy_name: str,
    coin_regime_enabled: bool = False,
    vol_filter_low_enabled: bool = False,
    vol_filter_high_enabled: bool = False,
    coin_regime_lookback: int = 14,
    order_size_usd: float = 100.0,
) -> FilterResult:
    """
    Применяет все фильтры к сигналу.
    Логика идентична strategy_runner.py:807-884
    """
```

---

### 3. telegram_sender.py

**Зависимость:** `python-telegram-bot>=20.0`

```python
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

def format_group_alert(
    signal: Signal,
    strategy_name: str,
    coin_regime: str,
    tp_pct: float,
    sl_pct: float,
) -> Tuple[str, InlineKeyboardMarkup]:
    """Форматирует сообщение для группы (русский язык)."""

def format_dm_details(signal_data: Dict) -> str:
    """Форматирует полные данные для ЛС (русский язык)."""

async def send_alert(
    bot: Bot,
    chat_id: str,
    signal: Signal,
    signal_data: Dict,
) -> bool:
    """Отправляет алерт в группу + сохраняет в signal_cache.json."""

def save_signal_cache(signal_id: str, signal_data: Dict) -> None:
    """Сохраняет данные в signal_cache.json (бессрочно)."""

def load_signal_cache(signal_id: str) -> Optional[Dict]:
    """Загружает данные из signal_cache.json."""
```

---

### 4. telegram_runner.py

**CLI параметры:**

```
python telegram_runner.py [OPTIONS]

Конфиг:
  --config PATH           Путь к config.json (default: config.json)

Символы (один из):
  --symbols SYM1,SYM2     Список символов через запятую
  --top N                 Топ N по объёму

Фильтры:
  --coin-regime           Включить фильтр по режиму монеты
  --vol-filter-low        Фильтр низкой волатильности
  --vol-filter-high       Фильтр высокой волатильности

Параметры (переопределяют config.json):
  --sl FLOAT              Stop Loss %
  --tp FLOAT              Take Profit %

Режимы:
  --dry-run               Не отправлять, только показать в консоли
```

**Основной flow:**

```python
async def main():
    # 1. Парсинг CLI + загрузка config.json
    args = parse_args()
    config = load_config(args.config)
    setup_logging()  # logs/telegram_YYYYMMDD.log

    # 2. Определить дату сигнала (вчерашняя закрытая свеча UTC)
    signal_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    log.info(f"Анализируем свечу: {signal_date}")

    # 3. Определить символы
    if args.symbols:
        symbols = args.symbols.split(",")
    else:
        symbols = get_top_symbols(args.top)

    # 4. Загрузить и проверить кэш
    history = load_and_verify_cache(symbols, signal_date)

    # 5. Загрузить отправленные сигналы (защита от дубликатов)
    sent_ids = load_sent_signals()  # из sent_signals.log

    # 6. Инициализировать бота
    bot = Bot(token=config["telegram"]["bot_token"])

    # 7. Для КАЖДОЙ из 5 стратегий
    for strategy_name in ["ls_fade", "momentum", "reversal", "mean_reversion", "momentum_ls"]:

        # 7.1 Генерация сигналов
        signals = generate_signals(strategy_name, history, symbols, config)

        # 7.2 Фильтруем только сигналы на signal_date
        today_signals = [s for s in signals if s.date.date() == signal_date]

        for signal in today_signals:
            # 7.3 Проверка дубликатов
            signal_id = f"{signal_date}_{signal.symbol}_{signal.direction}_{strategy_name}"
            if signal_id in sent_ids:
                log.info(f"SKIP (дубликат): {signal_id}")
                continue

            # 7.4 Применить фильтры
            candles = get_candles_for_symbol(history, signal.symbol)
            result = filter_signal(
                signal=signal,
                candles=candles,
                strategy_name=strategy_name,
                coin_regime_enabled=args.coin_regime,
                vol_filter_low_enabled=args.vol_filter_low,
                vol_filter_high_enabled=args.vol_filter_high,
                coin_regime_lookback=config["defaults"]["coin_regime_lookback"],
                order_size_usd=config["defaults"]["order_size_usd"],
            )

            if not result.passed:
                log.info(f"SKIP ({result.skip_reason}): {signal_id}")
                continue

            # 7.5 Собрать полные данные для callback
            signal_data = build_signal_data(signal, result, history, config, strategy_name)

            # 7.6 Отправить или dry-run
            if args.dry_run:
                log.info(f"DRY-RUN: {signal_id}")
                text, keyboard = format_group_alert(signal, strategy_name, result.coin_regime, ...)
                print(text)
            else:
                success = await send_alert(bot, config["telegram"]["chat_id"], signal, signal_data)
                if success:
                    save_sent_signal(signal_id)  # в sent_signals.log
                    log.info(f"SENT: {signal_id}")
                else:
                    log.error(f"FAILED: {signal_id}")

    log.info("Завершено")
```

---

### 5. telegram_callback_daemon.py

```python
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes
import json
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

SIGNAL_CACHE_FILE = "signal_cache.json"

def load_signal_cache(signal_id: str) -> Optional[Dict]:
    """Загружает данные сигнала из кэша."""
    try:
        with open(SIGNAL_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        return cache.get(signal_id)
    except:
        return None

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатия кнопки 'Подробности'."""
    query = update.callback_query
    await query.answer()

    # Извлекаем signal_id из callback_data
    signal_id = query.data.replace("details_", "")

    # Загружаем данные из кэша
    signal_data = load_signal_cache(signal_id)

    if not signal_data:
        await query.answer("Данные не найдены", show_alert=True)
        return

    # Форматируем полные данные
    text = format_dm_details(signal_data)

    # Отправляем в ЛС пользователю
    user_id = query.from_user.id
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="HTML"
        )
        log.info(f"Отправлено в ЛС: user={user_id}, signal={signal_id}")
    except Exception as e:
        log.error(f"Ошибка отправки в ЛС: {e}")
        await query.answer("Ошибка. Напишите боту /start", show_alert=True)

def main():
    config = load_config("config.json")

    app = Application.builder().token(config["telegram"]["bot_token"]).build()
    app.add_handler(CallbackQueryHandler(handle_callback, pattern="^details_"))

    log.info("Callback daemon запущен. Ожидание нажатий кнопок...")
    app.run_polling(allowed_updates=["callback_query"])

if __name__ == "__main__":
    main()
```

---

## ФОРМАТ АЛЕРТА В ГРУППЕ (русский)

```
🟢 LONG BTCUSDT
━━━━━━━━━━━━━━━━━━━━━━━━━━
Стратегия: ls_fade
Режим монеты: STRONG_BULL
━━━━━━━━━━━━━━━━━━━━━━━━━━
Вход:  $67,420.50
TP:    $74,162.55 (+10.0%)
SL:    $64,723.68 (-4.0%)

              [📊 Подробности]
```

---

## ФОРМАТ В ЛС (при нажатии кнопки)

```
📊 ПОЛНЫЕ ДАННЫЕ СИГНАЛА
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ОСНОВНОЕ:
• Монета: BTCUSDT
• Направление: LONG
• Дата сигнала: 2026-03-05 00:00 UTC
• Стратегия: ls_fade

УРОВНИ:
• Вход: $67,420.50
• Take Profit: $74,162.55 (+10.0%)
• Stop Loss: $64,723.68 (-4.0%)
• R:R Ratio: 2.5

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЕЖИМ МОНЕТЫ:
• Coin Regime: STRONG_BULL
• Изменение за 14д: +25.3%
• Волатильность (ATR%): 8.5%
• Действие матрицы: DYN ($1)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЫНОЧНЫЕ ДАННЫЕ:
• Long %: 62.4%
• Short %: 37.6%
• L/S Ratio: 1.66
• Funding Rate: +0.0150%
• Open Interest: $2,145,000,000
• Объём 24ч: $15,320,000,000

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
СВЕЧА (предыдущий день):
• Open: $66,800.00
• High: $67,950.00
• Low: $65,200.00
• Close: $67,420.50
• Volume: 45,230 BTC
• Quote Volume: $3,051,000,000
• Trades: 1,245,678
• Taker Buy %: 54.2%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ИНДИКАТОРЫ:
• ADX: 42.3 (сильный тренд)
• ATR: $2,831.26
• ATR %: 4.2%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ПРИЧИНА СИГНАЛА:
L/S ratio extreme: 62.4% лонгов (порог 60%)
```

---

## СТРУКТУРА signal_cache.json

```json
{
    "20260305_BTCUSDT_LONG_ls_fade": {
        "signal_id": "20260305_BTCUSDT_LONG_ls_fade",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "strategy": "ls_fade",
        "date": "2026-03-05T00:00:00Z",
        "entry": 67420.50,
        "tp": 74162.55,
        "sl": 64723.68,
        "tp_pct": 10.0,
        "sl_pct": 4.0,
        "rr_ratio": 2.5,
        "coin_regime": "STRONG_BULL",
        "coin_regime_change_pct": 25.3,
        "coin_volatility": 8.5,
        "regime_action": "DYN",
        "market_data": {
            "long_pct": 62.4,
            "short_pct": 37.6,
            "ls_ratio": 1.66,
            "funding_rate": 0.015,
            "open_interest": 2145000000,
            "volume_24h": 15320000000
        },
        "candle": {
            "date": "2026-03-04",
            "open": 66800.0,
            "high": 67950.0,
            "low": 65200.0,
            "close": 67420.5,
            "volume": 45230.0,
            "quote_volume": 3051000000,
            "trades_count": 1245678,
            "taker_buy_pct": 54.2
        },
        "indicators": {
            "adx": 42.3,
            "atr": 2831.26,
            "atr_pct": 4.2
        },
        "reason": "L/S ratio extreme: 62.4% лонгов (порог 60%)"
    }
}
```

---

## ПОРЯДОК РЕАЛИЗАЦИИ

| # | Файл | Описание | Зависит от |
|---|------|----------|-----------|
| 1 | `config.json` | Шаблон конфига | - |
| 2 | `signal_filter.py` | Логика фильтрации | - |
| 3 | `telegram_sender.py` | Форматирование + отправка | #2 |
| 4 | `telegram_runner.py` | Главный скрипт | #2, #3 |
| 5 | `telegram_callback_daemon.py` | Daemon для кнопок | #3 |
| 6 | Тестирование `--dry-run` | Проверка без отправки | #4 |
| 7 | Боевой запуск | После получения токена | #1-6 |

---

## ЗАПУСК В ПРОДЕ

```bash
# 1. Установить зависимости
pip install python-telegram-bot>=20.0

# 2. Заполнить config.json (токен, chat_id)

# 3. Запустить daemon (один раз, работает 24/7)
python telegram_callback_daemon.py &
# или через systemd/screen/tmux

# 4. Запускать по cron каждый день в 00:05 UTC
# crontab -e:
# 5 0 * * * cd /path/to/GenerateHistorySignals && python telegram_runner.py --top 20 --coin-regime --vol-filter-low

# Или вручную
python telegram_runner.py --top 20 --coin-regime --vol-filter-low
python telegram_runner.py --symbols BTCUSDT,ETHUSDT,SOLUSDT --coin-regime
```

---

## КРИТИЧЕСКИЕ ТОЧКИ (контроль качества)

| # | Риск | Решение |
|---|------|---------|
| 1 | Фильтры не применятся | Unit тесты для filter_signal() |
| 2 | Callback потеряет данные | signal_cache.json бессрочно |
| 3 | Кэш устаревший | Проверка даты последней свечи перед генерацией |
| 4 | Дубли при повторном запуске | sent_signals.log + проверка |
| 5 | Матрицы рассинхронятся | Одна копия в signal_filter.py |
| 6 | TP/SL не совпадают | Берём из signal.take_profit/stop_loss |
| 7 | Данные в ЛС неполные | Сохраняем ВСЕ в signal_cache.json |
| 8 | Бот не может писать в ЛС | Пользователь должен написать /start боту |

---

## ЗАВИСИМОСТИ (requirements.txt)

```
python-telegram-bot>=20.0
# Остальные уже установлены для run_all.py
```

---

## СТАТУС

- [ ] config.json - ожидает данные от пользователя
- [ ] signal_filter.py - не реализован
- [ ] telegram_sender.py - не реализован
- [ ] telegram_runner.py - не реализован
- [ ] telegram_callback_daemon.py - не реализован
- [ ] Тестирование - не проведено
- [ ] Боевой запуск - не выполнен

---

**Жду команду "можно кодить" или "реализуй"**

---

## ЛОГ РЕАЛИЗАЦИИ (ТОЛЬКО ДОПИСЫВАТЬ)

### Формат записи:
```
=== РЕАЛИЗАЦИЯ: [название] ===
Дата: YYYY-MM-DD HH:MM
Файл: [путь к файлу]
Что сделано: [описание]
Почему так: [обоснование]
Строки: [номера строк или "новый файл"]
Тесты: [как проверено]
Осталось: [следующие шаги]
==============================
```

---

### 2026-03-05: Подготовка

=== ПОДГОТОВКА: Создание планов ===
Дата: 2026-03-05
Файлы: TELEGRAM_PROD_PLAN.md, CLAUDE_SESSION.md
Что сделано:
- Создан полный план реализации
- Создан файл синхронизации сессий
- Согласованы все архитектурные решения (17 вопросов)
- Задокументированы матрицы и параметры
Почему так: Детальное планирование перед кодингом предотвращает ошибки
Осталось: Получить токен/chat_id, команда "можно кодить"
==============================

---

=== РЕАЛИЗАЦИЯ: config.json ===
Дата: 2026-03-05
Файл: G:\BinanceFriend\GenerateHistorySignals\config.json
Что сделано: Создан конфиг с токеном и chat_id
Почему так: Вынесены настройки из кода для гибкости
Строки: новый файл, 27 строк
Тесты: JSON валидный
Осталось: signal_filter.py, telegram_sender.py, telegram_runner.py, telegram_callback_daemon.py
==============================

=== РЕАЛИЗАЦИЯ: signal_filter.py ===
Дата: 2026-03-05
Файл: G:\BinanceFriend\GenerateHistorySignals\signal_filter.py
Что сделано:
- Скопированы COIN_REGIME_MATRIX, VOL_FILTER_THRESHOLDS из strategy_runner.py
- Скопированы calculate_coin_regime(), calculate_volatility()
- Добавлена calculate_regime_change_pct() для отображения % изменения
- Создан FilterResult dataclass
- Создана filter_signal() - применяет все фильтры
Почему так: Выносим логику фильтрации чтобы не трогать strategy_runner.py
Строки: новый файл, 270 строк
Тесты: Импорт проверен
Осталось: telegram_sender.py, telegram_runner.py, telegram_callback_daemon.py
==============================

=== РЕАЛИЗАЦИЯ: telegram_sender.py ===
Дата: 2026-03-05
Файл: G:\BinanceFriend\GenerateHistorySignals\telegram_sender.py
Что сделано:
- format_group_alert() - форматирование алерта для группы (русский)
- format_dm_details() - форматирование полных данных для ЛС
- save_signal_cache() / load_signal_cache() - кэш для callback
- load_sent_signals() / save_sent_signal() - защита от дубликатов
- send_alert() - отправка с кнопкой
Почему так: Отдельный модуль для Telegram логики
Строки: новый файл, 260 строк
Тесты: Импорт проверен
Осталось: telegram_runner.py, telegram_callback_daemon.py
==============================

=== РЕАЛИЗАЦИЯ: telegram_runner.py ===
Дата: 2026-03-05
Файл: G:\BinanceFriend\GenerateHistorySignals\telegram_runner.py
Что сделано:
- CLI парсинг (--symbols, --top, --coin-regime, --vol-filter-low/high, --dry-run)
- setup_logging() - логи в консоль + файл
- generate_signals_for_strategy() - генерация сигналов
- build_signal_data() - сбор данных для callback
- main() - основной flow: загрузка → генерация → фильтрация → отправка
Почему так: Главный скрипт, аналог run_all.py но для Telegram
Строки: новый файл, 340 строк
Тесты: --dry-run режим для проверки без отправки
Осталось: telegram_callback_daemon.py
==============================

=== РЕАЛИЗАЦИЯ: telegram_callback_daemon.py ===
Дата: 2026-03-05
Файл: G:\BinanceFriend\GenerateHistorySignals\telegram_callback_daemon.py
Что сделано:
- handle_callback() - обработка нажатия кнопки "Подробности"
- Загрузка данных из signal_cache.json
- Отправка в ЛС пользователю
- Обработка ошибок (бот заблокирован, чат не найден)
Почему так: Отдельный daemon для 24/7 работы
Строки: новый файл, 120 строк
Тесты: Запуск проверен
Осталось: Тестирование с --dry-run, боевой запуск
==============================

### СТАТУС: ВСЕ ФАЙЛЫ СОЗДАНЫ

| Файл | Статус |
|------|--------|
| config.json | ✅ ГОТОВО |
| signal_filter.py | ✅ ГОТОВО |
| telegram_sender.py | ✅ ГОТОВО |
| telegram_runner.py | ✅ ГОТОВО |
| telegram_callback_daemon.py | ✅ ГОТОВО |

### СЛЕДУЮЩИЙ ШАГ: Тестирование с --dry-run

---

=== РЕАЛИЗАЦИЯ: test_telegram_prod.py ===
Дата: 2026-03-05
Файл: G:\BinanceFriend\GenerateHistorySignals\tests\test_telegram_prod.py
Что сделано: Полное покрытие автотестами (~1100 строк, 58KB)
Тестовые классы:
- TestCoinRegimeMatrix (7 тестов) - проверка матрицы режимов
- TestVolFilterThresholds (6 тестов) - проверка порогов волатильности
- TestCalculateCoinRegime (8 тестов) - тесты расчёта режима
- TestCalculateVolatility (5 тестов) - тесты расчёта волатильности
- TestCalculateRegimeChangePct (3 теста) - тесты изменения %
- TestFilterSignal (6 тестов) - тесты фильтрации
- TestFormatGroupAlert (10 тестов) - тесты форматирования алерта
- TestFormatDMDetails (8 тестов) - тесты подробностей ЛС
- TestSignalCache (4 теста) - тесты кэширования
- TestSentSignals (4 теста) - тесты защиты от дубликатов
- TestCLIArguments (2 теста) - тесты CLI
- TestConfigLoading (2 теста) - тесты конфига
- TestSignalIdFormat (4 теста) - тесты формата ID
- TestPriceFormatting (5 тестов) - тесты форматирования цен
- TestTPSLCalculation (3 теста) - тесты TP/SL расчётов
- TestAllStrategies (2 теста) - тесты константы ALL_STRATEGIES
- TestDateHandling (3 теста) - тесты обработки дат
- TestIntegration (3 тестов) - интеграционные тесты

Почему так: Полное покрытие для уверенности в работе системы
Строки: новый файл, ~1100 строк
Запуск: pytest tests/test_telegram_prod.py -v
==============================

---

## 🔍 АУДИТ: ПЛАН vs РЕАЛИЗАЦИЯ (2026-03-05)

### ❌ КРИТИЧЕСКИЕ УПУЩЕНИЯ

| # | Что в плане | Что в реализации | Критичность |
|---|-------------|------------------|-------------|
| 1 | **MONTH_DATA, DAY_DATA** (план стр. 266-267) | **НЕ СКОПИРОВАНЫ** в signal_filter.py | ⚠️ Средняя - не используются для фильтрации, но план требовал |
| 2 | **Проверка актуальности кэша** (план: "2. Проверка кэша") | **НЕТ** - telegram_runner просто грузит данные без проверки | 🔴 ВЫСОКАЯ |
| 3 | **Действие матрицы: DYN ($1)** (план стр. 554) | Выводит просто "DYN" без пояснения суммы | ⚠️ Средняя |
| 4 | **Volume: 45,230 BTC** (план стр. 571) | Выводит число без единицы измерения | ⚠️ Низкая |
| 5 | **coinalyze_api_key** | **ЗАХАРДКОЖЕН** в telegram_runner.py:379 | 🔴 ВЫСОКАЯ - должен быть в config.json |
| 6 | **order_size_usd** параметр | Передаётся в filter_signal, но **НЕ ИСПОЛЬЗУЕТСЯ** | ⚠️ Средняя |

---

### ✅ РЕАЛИЗОВАНО КОРРЕКТНО

| # | Компонент | Статус |
|---|-----------|--------|
| 1 | COIN_REGIME_MATRIX | ✅ Идентична strategy_runner.py:53-89 |
| 2 | VOL_FILTER_THRESHOLDS | ✅ Идентична strategy_runner.py:95-101 |
| 3 | calculate_coin_regime() | ✅ Идентична (без look-ahead bias) |
| 4 | calculate_volatility() | ✅ Идентична (без look-ahead bias) |
| 5 | Все 5 стратегий | ✅ ALL_STRATEGIES содержит все 5 |
| 6 | --coin-regime, --vol-filter-low/high | ✅ CLI реализован |
| 7 | --dry-run | ✅ Работает |
| 8 | Кнопка "📊 Подробности" | ✅ callback_data=details_{signal_id} |
| 9 | sent_signals.log | ✅ Защита от дубликатов |
| 10 | signal_cache.json | ✅ Бессрочное хранение |
| 11 | Формат алерта (русский) | ✅ Соответствует плану |
| 12 | logs/telegram_YYYYMMDD.log | ✅ Логирование работает |

---

### 🔴 ДЕТАЛИ КРИТИЧЕСКИХ ПРОБЛЕМ

**1. Захардкоженный API ключ (telegram_runner.py:377-381)**
```python
downloader = HybridHistoryDownloader(
    cache_dir='cache',
    coinalyze_api_key='adb282f9-7e9e-4b6c-a669-b01c0304d506',  # ← ПЛОХО!
    data_interval='daily'
)
```
**Должно быть:** `config["coinalyze_api_key"]`

**2. Нет проверки актуальности кэша**
- План: "Проверка кэша (актуальность данных)"
- Факт: Скрипт загружает данные без проверки что вчерашняя свеча уже закрыта
- Риск: Если запустить в 23:55 UTC, получим незакрытую свечу

**3. order_size_usd - мёртвый параметр**
```python
# signal_filter.py:273
order_size_usd: float = 100.0,  # принимается...
# ...но НИГДЕ не используется в теле функции!
```

---

### 📋 ФОРМАТ ЛС - МЕЛКИЕ РАСХОЖДЕНИЯ

| Что в плане | Что в реализации |
|-------------|------------------|
| `Действие матрицы: DYN ($1)` | `Действие матрицы: DYN` (без суммы) |
| `Volume: 45,230 BTC` | `Volume: 45,230` (без единицы) |
| `ls_ratio` в market_data | Вычисляется на лету в format_dm_details, не хранится |

---

### ⚠️ ТЕСТЫ - СЛЕПЫЕ ЗОНЫ

| Что НЕ покрыто тестами |
|------------------------|
| Интеграция с реальным Telegram API |
| Проверка что HybridHistoryDownloader отдаёт свежие данные |
| Проверка формата выходных данных против реального Telegram |
| Тест на большом кол-ве символов (--top 100) |
| Тест timeout/retry при ошибках сети |

---

### 📊 ИТОГО АУДИТА

| Категория | Количество |
|-----------|------------|
| ✅ Выполнено корректно | 12 пунктов |
| ⚠️ Средние недочёты | 4 пункта |
| 🔴 Критические проблемы | 2 пункта |

---

### 🛠️ TODO: ИСПРАВИТЬ ПЕРЕД ПРОДОМ

- [x] API ключ coinalyze вынести в config.json ✅ ИСПРАВЛЕНО 2026-03-05
- [x] Добавить проверку актуальности данных (что свеча закрыта, UTC 00:00+) ✅ ИСПРАВЛЕНО 2026-03-05
- [x] Добавить пояснение к действию матрицы: DYN ($1) / FULL ($100) ✅ ИСПРАВЛЕНО 2026-03-05
- [x] Убрать мёртвый параметр order_size_usd ✅ ИСПРАВЛЕНО 2026-03-05
- [x] MONTH_DATA, DAY_DATA - добавлены в signal_filter.py ✅ ИСПРАВЛЕНО 2026-03-05

---

=== ИСПРАВЛЕНИЕ: ВСЕ КРИТИЧЕСКИЕ УПУЩЕНИЯ ===
Дата: 2026-03-05
Файлы изменены:
- signal_filter.py: +MONTH_DATA, +DAY_DATA, -order_size_usd параметр
- config.json: +coinalyze_api_key, -order_size_usd
- telegram_runner.py: использует api_key из конфига, +проверка UTC 00:05
- telegram_sender.py: +_format_regime_action() для DYN($1)/FULL($100)/OFF(пропуск)

Тесты: 85 passed in 0.34s
==============================

---

=== РЕАЛИЗАЦИЯ: --strategies параметр ===
Дата: 2026-03-05
Файл: G:\BinanceFriend\GenerateHistorySignals\telegram_runner.py
Что сделано:
- Добавлен CLI параметр --strategies (default="all")
- Формат: --strategies ls_fade,momentum или --strategies all
- Валидация против ALL_STRATEGIES
- Если невалидные стратегии - ошибка и выход
Почему так: Пользователь попросил возможность выбора стратегий
Строки:
- Добавлен parser.add_argument("--strategies", ...)
- Добавлен парсинг и валидация strategies_to_run
Тесты: Не требуется - простое добавление CLI аргумента
==============================

---

=== РЕАЛИЗАЦИЯ: --ml параметр ===
Дата: 2026-03-05
Файл: G:\BinanceFriend\GenerateHistorySignals\telegram_runner.py
Что сделано:
- Добавлен импорт MLSignalFilter, MLPrediction из ml.filter
- Добавлены CLI параметры --ml, --ml-model-dir
- Загрузка ML моделей при --ml флаге
- Функция build_ml_features() для сборки фичей
- ML фильтрация после coin_regime/vol фильтров
- ML метаданные добавляются в signal_data["ml"]
- Статистика total_skipped_ml в summary

Почему так: Идентичная логика как в strategy_runner.py (бэктестер)
Строки: ~60 строк добавлено
Использование:
```bash
py -3.12 telegram_runner.py --symbols BTCUSDT --coin-regime --ml --dry-run
```
==============================

---

=== РЕАЛИЗАЦИЯ: ML как рекомендация (не фильтр) ===
Дата: 2026-03-05
Файлы: telegram_runner.py, telegram_sender.py

Что сделано:
1. telegram_runner.py:
   - Убран `continue` при ML=False — сигналы больше не скипаются
   - ML теперь только рекомендация
   - signal_data["ml"] всегда содержит enabled + данные
   - Убрана статистика total_skipped_ml

2. telegram_sender.py format_group_alert():
   - Добавлен параметр ml_data
   - В конце сообщения жирная строка:
     - `✅ ML рекомендует` или `❌ ML не рекомендует`
     - Если --ml не включен: `🤖 ML: режим не включен при запуске`
   - Добавлен parse_mode=HTML

3. telegram_sender.py format_dm_details():
   - Блок "🤖 ML АНАЛИЗ": confidence, filter_score, reason
   - Блок "🔬 ML ПОДРОБНО": predicted SL/TP/lifetime/direction

Почему так: По требованию пользователя — ML не должен убивать сигналы,
только давать рекомендацию.

4. telegram_callback_daemon.py:
   - Добавлен parse_mode="HTML" в send_message (строка 101)
   - Без этого <b> теги не работали бы в ЛС
==============================

=== РЕАЛИЗАЦИЯ: ADX Display Fix (Вариант Б) ===
Дата: 2026-03-05
Файл: telegram_sender.py

Что сделано:
1. ADX делится на 14 перед отображением (строки 158-165)
2. Добавлен комментарий с TODO для полного исправления
3. Напоминание: "Погрумить перед кодингом!"

Код:
```python
# WORKAROUND: ADX в коде завышен в ~14x из-за бага в wilder_smooth
adx_raw = indicators.get("adx", 0)
adx = adx_raw / 14 if adx_raw > 0 else 0  # Нормализуем для отображения
```

Почему так:
- Баг в strategies/base.py:236 — wilder_smooth использует SUM вместо AVG
- ML модели обучены на "кривых" данных → StandardScaler компенсирует
- Полный фикс требует re-backtest + re-train (2-4 часа)
- Вариант Б — минимальное изменение, ML не трогаем

Строки: 158-165

Осталось (ПЛАН НА БУДУЩЕЕ):
==============================

⚠️ ПЛАН ПОЛНОГО ИСПРАВЛЕНИЯ ADX (TODO)
!!! ПЕРЕД КОДИНГОМ ОБЯЗАТЕЛЬНО ПОГРУМИТЬ ВМЕСТЕ !!!

| # | Шаг | Файл | Статус |
|---|-----|------|--------|
| 1 | Fix wilder_smooth | strategies/base.py:236 | ⏳ TODO |
| 2 | Re-backtest | run_all.py | ⏳ TODO |
| 3 | Re-train ML | ml/trainer_per_strategy.py | ⏳ TODO |
| 4 | Remove workaround | telegram_sender.py | ⏳ TODO |
| 5 | Test | - | ⏳ TODO |

==============================
