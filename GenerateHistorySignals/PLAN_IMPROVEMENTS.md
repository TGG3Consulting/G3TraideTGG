# ПЛАН ДОРАБОТОК БЭКТЕСТЕРА

> **ПРИНЦИПЫ:**
> - ВСЕ данные РЕАЛЬНЫЕ (Binance API, Coinalyze API)
> - НИКАКИХ хардкодов, моков, данных из будущего
> - Entry = next_candle.open (уже исправлено)
> - Каждая доработка = полный анализ цепочки изменений

---

## СТАТУС ДОРАБОТОК

| # | Фича | Статус | Строк кода |
|---|------|--------|------------|
| 1 | Volume/Liquidity Check в Backtest + Slippage | ПЛАН | ~26 |
| 2 | Volume Filter в Signal Generator | ПЛАН | ~45 |
| 3 | XLSX Export с ВСЕМИ данными | ПЛАН | ~231 |
| 4 | Chain/Dedup нумерация сигналов | ПЛАН | ~85 |
| 5 | Улучшенная логика бэктестера | ПЛАН | ~120 |
| 6 | Funding Fee + Trading Fees | ПЛАН | ~62 |
| 7 | Max Drawdown | ПЛАН | ~41 |
| 8 | Avg Hold Time по результату | ПЛАН | ~20 |

---

## ДОРАБОТКА #1: Volume/Liquidity Check + Slippage Model

### Что хотим:
- Проверять что order_size < 0.1% от daily_volume
- Если больше - либо skip, либо добавить slippage penalty
- Параметр order_size_usd (default $100, масштабируется до $10K)

### Анализ кода - что менять:

#### Файл 1: `strategies/base.py`
**Строка 30-36** - DailyCandle dataclass
```python
@dataclass
class DailyCandle:
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    # ДОБАВИТЬ: quote_volume: float
```
**Изменение:** +1 строка

---

#### Файл 2: `strategy_runner.py`
**Строка ~85-95** - агрегация daily candles из 1m klines
```python
# Сейчас агрегируем: open, high, low, close, volume
# ДОБАВИТЬ: суммировать quote_volume
```

**Строка ~110-120** - создание DailyCandle
```python
candles.append(DailyCandle(
    date=d["date"],
    open=d["open"],
    ...
    # ДОБАВИТЬ: quote_volume=d["quote_volume"],
))
```

**Строка ~200-210** - backtest_signals() параметры
```python
def backtest_signals(
    self,
    signals: List[Signal],
    history: Dict,
    max_hold_days: int = 14,
    # ДОБАВИТЬ:
    order_size_usd: float = 100.0,
    max_volume_pct: float = 0.001,  # 0.1%
) -> BacktestResult:
```

**Строка ~220-240** - внутри цикла backtest
```python
# ДОБАВИТЬ перед обработкой сигнала:
entry_candle = candles[entry_idx]
daily_volume = entry_candle.quote_volume

if order_size_usd > daily_volume * max_volume_pct:
    skipped_liquidity += 1
    continue

# ИЛИ slippage model:
slippage = estimate_slippage(order_size_usd, daily_volume, volatility)
actual_entry = signal.entry * (1 + slippage)
```

**Изменение:** ~15 строк

---

#### Файл 3: `strategy_runner.py` - BacktestResult
**Строка ~50-70** - BacktestResult dataclass
```python
@dataclass
class BacktestResult:
    ...
    # ДОБАВИТЬ:
    skipped_liquidity: int = 0
    order_size_usd: float = 100.0
    total_slippage_pct: float = 0.0
```
**Изменение:** +3 строки

---

#### Файл 4: Новая функция (в strategy_runner.py или utils.py)
```python
def estimate_slippage(
    order_size: float,
    daily_volume: float,
    volatility: float = 0.03
) -> float:
    """
    Market Impact = sigma * sqrt(order_size / daily_volume)

    Returns: slippage multiplier (e.g., 0.001 = 0.1%)
    """
    if daily_volume <= 0:
        return 0.01  # 1% penalty если нет данных

    ratio = order_size / daily_volume
    impact = volatility * (ratio ** 0.5)

    return min(impact, 0.02)  # Cap at 2%
```
**Изменение:** +15 строк

---

#### Файл 5: `run_all.py`
**Строка ~107-112** - вызов backtest
```python
result = runner.backtest_signals(
    signals,
    history,
    max_hold_days=max_hold_days,
    # ДОБАВИТЬ:
    order_size_usd=args.order_size,
)
```

**Строка ~215-220** - argparse
```python
# ДОБАВИТЬ:
parser.add_argument("--order-size", type=float, default=100.0,
                    help="Order size in USD (default: 100)")
```
**Изменение:** +5 строк

---

### Источники данных:
- `quote_volume` - УЖЕ ЕСТЬ в klines (data_downloader.py:348)
- Реальные данные Binance API
- НЕТ хардкодов

### Итого по доработке #1:
| Файл | Строк |
|------|-------|
| strategies/base.py | +1 |
| strategy_runner.py | +20 |
| run_all.py | +5 |
| **ВСЕГО** | **~26** |

---

## ДОРАБОТКА #2: Volume Filter в Signal Generator

### Что хотим:
- Signal Generator получает `order_size_usd` на входе
- При генерации сигнала проверяет volume на день входа
- Если volume < order_size / 0.001 (т.е. ордер > 0.1% объёма) → НЕ генерируем сигнал
- Отсекаем монеты ДО бэктеста, а не после

### Цепочка данных (анализ):

```
run_all.py (--order-size 100)
    ↓
StrategyRunner.__init__(order_size_usd=100)
    ↓
StrategyRunner.generate_signals()
    ↓
    для каждого символа:
        ↓
        aggregate_to_daily() → candles с quote_volume
        ↓
        StrategyData(candles=candles)
        ↓
        strategy.generate_signals(data, order_size_usd)
            ↓
            для каждой свечи:
                if next_candle.quote_volume * 0.001 < order_size:
                    skip (не генерируем сигнал)
```

### Анализ кода - что менять:

---

#### Файл 1: `strategies/base.py`

**Строка 52-68** - StrategyConfig (добавить параметры)
```python
@dataclass
class StrategyConfig:
    sl_pct: float = 5.0
    tp_pct: float = 10.0
    max_hold_days: int = 14
    lookback: int = 7
    # ДОБАВИТЬ:
    order_size_usd: float = 100.0       # Размер ордера
    min_volume_ratio: float = 0.001     # Макс % от объёма (0.1%)

    params: Dict[str, Any] = field(default_factory=dict)
```
**Изменение:** +2 строки

**Строка 71-108** - BaseStrategy (добавить helper метод)
```python
class BaseStrategy(ABC):
    ...

    # ДОБАВИТЬ после _calculate_atr():
    def _has_sufficient_volume(
        self,
        candle: DailyCandle,
        order_size: Optional[float] = None
    ) -> bool:
        """
        Проверяет достаточно ли объёма для ордера.

        Args:
            candle: Свеча дня входа (с quote_volume)
            order_size: Размер ордера в USD (или из config)

        Returns:
            True если объём достаточен, False если надо пропустить
        """
        size = order_size or self.config.order_size_usd
        min_ratio = self.config.min_volume_ratio

        if not hasattr(candle, 'quote_volume') or candle.quote_volume <= 0:
            return True  # Нет данных - пропускаем проверку

        max_order = candle.quote_volume * min_ratio
        return size <= max_order
```
**Изменение:** +20 строк

---

#### Файл 2: `strategies/ls_fade.py`

**Строка ~85-90** - перед созданием сигнала
```python
# СЕЙЧАС:
signal = None
entry_price = next_candle.open

# ИЗМЕНИТЬ НА:
signal = None
entry_price = next_candle.open

# Проверка ликвидности
if not self._has_sufficient_volume(next_candle):
    continue  # Пропускаем - недостаточно объёма
```
**Изменение:** +3 строки

---

#### Файл 3: `strategies/momentum.py`

**Строка ~75** - аналогичная проверка
```python
entry_price = next_candle.open

# ДОБАВИТЬ:
if not self._has_sufficient_volume(next_candle):
    continue
```
**Изменение:** +3 строки

---

#### Файл 4: `strategies/reversal.py`

**Аналогично:** +3 строки

---

#### Файл 5: `strategies/mean_reversion.py`

**Аналогично:** +3 строки

---

#### Файл 6: `strategies/momentum_ls.py`

**Аналогично:** +3 строки

---

#### Файл 7: `strategy_runner.py`

**Строка 63-78** - __init__ (передать order_size в config)
```python
def __init__(
    self,
    strategy_name: str = "ls_fade",
    config: Optional[StrategyConfig] = None,
    output_dir: str = "output",
    order_size_usd: float = 100.0,  # ДОБАВИТЬ
):
    # Если config не передан, создаём с order_size
    if config is None:
        config = StrategyConfig(order_size_usd=order_size_usd)
    else:
        config.order_size_usd = order_size_usd

    self.strategy = get_strategy(strategy_name, config)
```
**Изменение:** +5 строк

---

#### Файл 8: `run_all.py`

**Строка ~107** - создание runner
```python
runner = StrategyRunner(
    strategy_name=strat_name,
    config=config,
    output_dir=output_dir,
    order_size_usd=order_size_usd,  # ДОБАВИТЬ
)
```

**Строка ~42-50** - параметры функции
```python
def run_all_strategies(
    symbols: List[str],
    start: datetime,
    end: datetime,
    sl_pct: float = 4.0,
    tp_pct: float = 10.0,
    max_hold_days: int = 14,
    order_size_usd: float = 100.0,  # ДОБАВИТЬ
    ...
```
**Изменение:** +3 строки

---

### Источники данных:
- `quote_volume` из klines Binance API (РЕАЛЬНЫЕ)
- Проверка на день ВХОДА (next_candle) - нет look-ahead
- Формула: `order_size <= daily_volume * 0.001`

### Итого по доработке #2:
| Файл | Строк |
|------|-------|
| strategies/base.py | +22 |
| strategies/ls_fade.py | +3 |
| strategies/momentum.py | +3 |
| strategies/reversal.py | +3 |
| strategies/mean_reversion.py | +3 |
| strategies/momentum_ls.py | +3 |
| strategy_runner.py | +5 |
| run_all.py | +3 |
| **ВСЕГО** | **~45** |

### Зависимости:
- **ТРЕБУЕТ ДОРАБОТКУ #1** (quote_volume в DailyCandle)

---

## ДОРАБОТКА #3: XLSX Export с ВСЕМИ данными

### Что хотим:
- Бэктестер выводит файл `.xlsx` со ВСЕМИ данными
- Включаем данные которые НЕ используются в сигнале, но ЕСТЬ
- Единая система именования параметров (сквозная)
- Все расчётные поля (entry, SL, TP, PnL и т.д.)

### ВСЕ доступные данные в системе:

#### 1. Klines (Binance API) - агрегируем в daily:
| Поле API | Наше имя (СКВОЗНОЕ) | Описание |
|----------|---------------------|----------|
| k[0] | `candle_timestamp` | Время открытия свечи |
| k[1] | `candle_open` | Цена открытия |
| k[2] | `candle_high` | Максимум |
| k[3] | `candle_low` | Минимум |
| k[4] | `candle_close` | Цена закрытия |
| k[5] | `candle_volume` | Объём в базовой валюте |
| k[7] | `candle_quote_volume` | Объём в USDT |
| k[8] | `candle_trades_count` | Количество сделок |
| k[9] | `candle_taker_buy_volume` | Объём покупок taker |
| k[10] | `candle_taker_buy_quote_volume` | Объём покупок taker в USDT |

#### 2. Open Interest (Binance/Coinalyze API):
| Поле API | Наше имя (СКВОЗНОЕ) | Описание |
|----------|---------------------|----------|
| sumOpenInterest | `oi_contracts` | OI в контрактах |
| sumOpenInterestValue | `oi_value_usd` | OI в USD |

#### 3. Long/Short Ratio (Binance/Coinalyze API):
| Поле API | Наше имя (СКВОЗНОЕ) | Описание |
|----------|---------------------|----------|
| longAccount | `ls_long_pct` | % лонгов |
| shortAccount | `ls_short_pct` | % шортов |
| longShortRatio | `ls_ratio` | Соотношение L/S |

#### 4. Funding Rate (Binance API):
| Поле API | Наше имя (СКВОЗНОЕ) | Описание |
|----------|---------------------|----------|
| fundingTime | `funding_timestamp` | Время фандинга |
| fundingRate | `funding_rate` | Ставка фандинга |
| markPrice | `funding_mark_price` | Mark price |

#### 5. Signal (генерируем):
| Поле | Наше имя (СКВОЗНОЕ) | Описание |
|------|---------------------|----------|
| date | `signal_date` | Дата сигнала |
| symbol | `signal_symbol` | Символ |
| direction | `signal_direction` | LONG/SHORT |
| entry | `signal_entry` | Цена входа |
| stop_loss | `signal_sl` | Stop Loss |
| take_profit | `signal_tp` | Take Profit |
| reason | `signal_reason` | Причина сигнала |
| metadata.* | `signal_meta_*` | Метаданные стратегии |

#### 6. Trade Result (бэктест):
| Поле | Наше имя (СКВОЗНОЕ) | Описание |
|------|---------------------|----------|
| exit_date | `trade_exit_date` | Дата выхода |
| exit_price | `trade_exit_price` | Цена выхода |
| pnl_pct | `trade_pnl_pct` | PnL в % |
| result | `trade_result` | WIN/LOSS/TIMEOUT |
| hold_days | `trade_hold_days` | Дней в позиции |

#### 7. Расчётные поля (добавляем):
| Поле | Наше имя (СКВОЗНОЕ) | Формула |
|------|---------------------|---------|
| - | `calc_sl_pct` | (entry - sl) / entry * 100 |
| - | `calc_tp_pct` | (tp - entry) / entry * 100 |
| - | `calc_rr_ratio` | tp_pct / sl_pct |
| - | `calc_pnl_usd` | order_size * pnl_pct / 100 |
| - | `calc_volume_ratio` | order_size / quote_volume |
| - | `calc_slippage_est` | по формуле impact |

---

### Структура XLSX файла:

**Sheet 1: "Trades"** - Все сделки
```
| signal_date | signal_symbol | signal_direction | signal_entry | signal_sl | signal_tp |
| trade_exit_date | trade_exit_price | trade_pnl_pct | trade_result | trade_hold_days |
| calc_pnl_usd | calc_rr_ratio | calc_slippage_est |
| candle_open | candle_high | candle_low | candle_close | candle_quote_volume |
| ls_long_pct | ls_short_pct | oi_value_usd | funding_rate |
| signal_reason | signal_meta_* |
```

**Sheet 2: "Summary"** - Сводка
```
| Метрика | Значение |
| total_trades | ... |
| win_rate | ... |
| total_pnl_pct | ... |
| total_pnl_usd | ... |
| avg_hold_days_win | ... |
| avg_hold_days_loss | ... |
```

**Sheet 3: "Config"** - Параметры запуска
```
| Параметр | Значение |
| strategy_name | ... |
| sl_pct | ... |
| tp_pct | ... |
| order_size_usd | ... |
| start_date | ... |
| end_date | ... |
```

---

### Анализ кода - что менять:

#### Файл 1: НОВЫЙ `xlsx_exporter.py`
```python
"""
XLSX Exporter - Экспорт всех данных бэктеста в Excel.

Принципы:
- ВСЕ имена полей СКВОЗНЫЕ (единая система)
- ВСЕ данные из API включены (даже неиспользуемые)
- Расчётные поля добавлены
"""

import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill
from typing import List, Dict
from datetime import datetime

# СКВОЗНЫЕ ИМЕНА (используются везде!)
FIELD_NAMES = {
    # Candle
    "candle_timestamp": "Candle Time",
    "candle_open": "Open",
    "candle_high": "High",
    "candle_low": "Low",
    "candle_close": "Close",
    "candle_volume": "Volume",
    "candle_quote_volume": "Volume USD",
    "candle_trades_count": "Trades Count",
    "candle_taker_buy_volume": "Taker Buy Vol",
    "candle_taker_buy_quote_volume": "Taker Buy Vol USD",

    # L/S Ratio
    "ls_long_pct": "Long %",
    "ls_short_pct": "Short %",
    "ls_ratio": "L/S Ratio",

    # Open Interest
    "oi_contracts": "OI Contracts",
    "oi_value_usd": "OI USD",

    # Funding
    "funding_rate": "Funding Rate",
    "funding_mark_price": "Mark Price",

    # Signal
    "signal_date": "Signal Date",
    "signal_symbol": "Symbol",
    "signal_direction": "Direction",
    "signal_entry": "Entry Price",
    "signal_sl": "Stop Loss",
    "signal_tp": "Take Profit",
    "signal_reason": "Reason",

    # Trade Result
    "trade_exit_date": "Exit Date",
    "trade_exit_price": "Exit Price",
    "trade_pnl_pct": "PnL %",
    "trade_result": "Result",
    "trade_hold_days": "Hold Days",

    # Calculated
    "calc_sl_pct": "SL %",
    "calc_tp_pct": "TP %",
    "calc_rr_ratio": "R:R Ratio",
    "calc_pnl_usd": "PnL USD",
    "calc_volume_ratio": "Vol Ratio",
    "calc_slippage_est": "Slippage Est",
}


class XLSXExporter:
    def __init__(self, output_path: str):
        self.output_path = output_path
        self.wb = openpyxl.Workbook()

    def export_backtest(
        self,
        trades: List[Trade],
        history: Dict[str, SymbolHistoryData],
        config: StrategyConfig,
        result: BacktestResult,
        order_size_usd: float = 100.0,
    ) -> str:
        """Экспортирует ВСЕ данные в XLSX."""
        self._write_trades_sheet(trades, history, order_size_usd)
        self._write_summary_sheet(result, order_size_usd)
        self._write_config_sheet(config)
        self.wb.save(self.output_path)
        return self.output_path
```
**Изменение:** ~200 строк (новый файл)

---

#### Файл 2: `strategy_runner.py`

**Добавить метод экспорта:**
```python
def export_to_xlsx(
    self,
    result: BacktestResult,
    history: Dict[str, SymbolHistoryData],
    order_size_usd: float = 100.0,
    filename: Optional[str] = None,
) -> str:
    """Export backtest results to XLSX."""
    from xlsx_exporter import XLSXExporter

    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"backtest_{self.strategy.name}_{timestamp}.xlsx"

    filepath = os.path.join(self.output_dir, filename)
    exporter = XLSXExporter(filepath)

    return exporter.export_backtest(
        trades=result.trades,
        history=history,
        config=self.strategy.config,
        result=result,
        order_size_usd=order_size_usd,
    )
```
**Изменение:** +20 строк

---

#### Файл 3: `run_all.py`

**Добавить вызов экспорта:**
```python
# После backtest
if args.xlsx:
    xlsx_path = runner.export_to_xlsx(
        result=result,
        history=history,
        order_size_usd=args.order_size,
    )
    print(f"  XLSX: {xlsx_path}")

# Argparse
parser.add_argument("--xlsx", action="store_true",
                    help="Export to XLSX")
```
**Изменение:** +10 строк

---

#### Файл 4: `requirements.txt` (или setup)

```
openpyxl>=3.1.0
```
**Изменение:** +1 строка

---

### Данные НЕ используемые сейчас, но ВКЛЮЧАЕМ:

| Данные | Сейчас | В XLSX |
|--------|--------|--------|
| candle_trades_count | ❌ | ✅ |
| candle_taker_buy_volume | ❌ | ✅ |
| candle_taker_buy_quote_volume | ❌ | ✅ |
| oi_contracts | ❌ | ✅ |
| oi_value_usd | ❌ | ✅ |
| funding_rate | ❌ | ✅ |
| funding_mark_price | ❌ | ✅ |

---

### Источники данных:
- ВСЕ из Binance API / Coinalyze API (РЕАЛЬНЫЕ)
- Расчётные поля на основе реальных данных
- НЕТ хардкодов

### Итого по доработке #3:
| Файл | Строк |
|------|-------|
| xlsx_exporter.py (НОВЫЙ) | ~200 |
| strategy_runner.py | +20 |
| run_all.py | +10 |
| requirements.txt | +1 |
| **ВСЕГО** | **~231** |

### Зависимости:
- Требует `openpyxl` библиотеку
- Использует данные из ДОРАБОТКИ #1 (quote_volume)

---

## ДОРАБОТКА #4: Chain/Dedup нумерация сигналов

### Что хотим:
- Генерируем ВСЕ сигналы (без удаления дублей)
- Нумеруем сигналы в цепочках (seq=1, 2, 3...)
- Новый параметр `dedup_days` - порог для определения цепочки
- Уникальные + читаемые идентификаторы

### Логика:

```python
# Для каждого нового сигнала (symbol + direction):
gap = new_signal.date - prev_signal.date

if gap.days < dedup_days:
    chain_seq += 1      # продолжаем цепочку
else:
    chain_num += 1      # новая цепочка
    chain_seq = 1
```

### Формат идентификаторов:

| Поле | Формат | Пример |
|------|--------|--------|
| `signal_id` | `{date}_{symbol}_{direction}` | `20240115_BTCUSDT_LONG` |
| `chain_id` | `{symbol}_{direction}_C{NNN}` | `BTCUSDT_LONG_C001` |
| `chain_seq` | int | `2` |
| `chain_total` | int (постфактум) | `5` |
| `chain_gap_days` | int | `1` |
| `is_chain_first` | bool | `True` |
| `is_chain_last` | bool | `True` |

### Пример с `dedup_days=3`:

```
День 1:  BTCUSDT LONG → signal_id=20240101_BTCUSDT_LONG, chain_id=BTCUSDT_LONG_C001, seq=1, gap=0
День 2:  BTCUSDT LONG → signal_id=20240102_BTCUSDT_LONG, chain_id=BTCUSDT_LONG_C001, seq=2, gap=1
День 3:  BTCUSDT LONG → signal_id=20240103_BTCUSDT_LONG, chain_id=BTCUSDT_LONG_C001, seq=3, gap=1
День 7:  BTCUSDT LONG → signal_id=20240107_BTCUSDT_LONG, chain_id=BTCUSDT_LONG_C002, seq=1, gap=4 ← НОВАЯ
День 8:  BTCUSDT LONG → signal_id=20240108_BTCUSDT_LONG, chain_id=BTCUSDT_LONG_C002, seq=2, gap=1
```

---

### Анализ кода - что менять:

#### Файл 1: `strategies/base.py`

**Строка 15-25** - Signal dataclass (добавить поля)
```python
@dataclass
class Signal:
    """Trading signal generated by a strategy."""
    date: datetime
    symbol: str
    direction: str
    entry: float
    stop_loss: float
    take_profit: float
    reason: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ДОБАВИТЬ - Chain/Dedup поля:
    signal_id: str = ""           # 20240115_BTCUSDT_LONG
    chain_id: str = ""            # BTCUSDT_LONG_C001
    chain_seq: int = 0            # 2
    chain_total: int = 0          # 5 (заполняется постфактум)
    chain_gap_days: int = 0       # 1
    is_chain_first: bool = False  # True/False
    is_chain_last: bool = False   # True/False
```
**Изменение:** +7 строк

---

#### Файл 2: НОВЫЙ `chain_processor.py`

```python
"""
Chain Processor - Нумерация и группировка сигналов в цепочки.

Логика:
- Сигналы группируются по symbol + direction
- Если gap между сигналами < dedup_days → та же цепочка
- Если gap >= dedup_days → новая цепочка
"""

from typing import List, Dict
from datetime import datetime
from collections import defaultdict

from strategies.base import Signal


def process_chains(
    signals: List[Signal],
    dedup_days: int = 14
) -> List[Signal]:
    """
    Обрабатывает сигналы и присваивает chain ID/seq.

    Args:
        signals: Список сигналов (отсортирован по дате)
        dedup_days: Порог дней для определения новой цепочки

    Returns:
        Тот же список с заполненными chain полями
    """
    # Группируем по symbol + direction
    groups: Dict[str, List[Signal]] = defaultdict(list)
    for s in signals:
        key = f"{s.symbol}_{s.direction}"
        groups[key].append(s)

    # Обрабатываем каждую группу
    for key, group_signals in groups.items():
        # Сортируем по дате
        group_signals.sort(key=lambda x: x.date)

        chain_num = 0
        prev_date = None

        for i, signal in enumerate(group_signals):
            # Определяем gap
            if prev_date is None:
                gap_days = 0
                chain_num = 1
                chain_seq = 1
            else:
                gap_days = (signal.date - prev_date).days
                if gap_days >= dedup_days:
                    chain_num += 1
                    chain_seq = 1
                else:
                    chain_seq += 1

            # Заполняем поля
            signal.signal_id = f"{signal.date.strftime('%Y%m%d')}_{signal.symbol}_{signal.direction}"
            signal.chain_id = f"{signal.symbol}_{signal.direction}_C{chain_num:03d}"
            signal.chain_seq = chain_seq
            signal.chain_gap_days = gap_days
            signal.is_chain_first = (chain_seq == 1)

            prev_date = signal.date

        # Второй проход - заполняем chain_total и is_chain_last
        chain_counts: Dict[str, int] = defaultdict(int)
        for signal in group_signals:
            chain_counts[signal.chain_id] += 1

        for signal in group_signals:
            signal.chain_total = chain_counts[signal.chain_id]

        # Определяем is_chain_last
        chain_last_seq: Dict[str, int] = {}
        for signal in group_signals:
            chain_last_seq[signal.chain_id] = max(
                chain_last_seq.get(signal.chain_id, 0),
                signal.chain_seq
            )

        for signal in group_signals:
            signal.is_chain_last = (signal.chain_seq == chain_last_seq[signal.chain_id])

    return signals
```
**Изменение:** ~70 строк (новый файл)

---

#### Файл 3: `strategy_runner.py`

**Строка ~165-178** - после generate_signals, вызвать process_chains
```python
def generate_signals(
    self,
    history: Dict[str, SymbolHistoryData],
    symbols: List[str],
    dedup_days: int = 14,  # ДОБАВИТЬ параметр
) -> List[Signal]:
    ...
    # После сбора всех сигналов:
    all_signals = []
    for symbol in symbols:
        signals = self.strategy.generate_signals(data)
        all_signals.extend(signals)

    # ДОБАВИТЬ: обработка цепочек
    from chain_processor import process_chains
    all_signals = process_chains(all_signals, dedup_days=dedup_days)

    return all_signals
```
**Изменение:** +5 строк

---

#### Файл 4: `run_all.py`

**Строка ~215-220** - argparse
```python
parser.add_argument("--dedup-days", type=int, default=14,
                    help="Days threshold for chain detection (default: 14)")
```

**Строка ~107** - передать в generate_signals
```python
signals = runner.generate_signals(history, symbols, dedup_days=args.dedup_days)
```
**Изменение:** +3 строки

---

### Источники данных:
- Даты сигналов (уже есть)
- Расчёт gap в днях (чистая математика)
- НЕТ внешних данных, НЕТ хардкодов

### Итого по доработке #4:
| Файл | Строк |
|------|-------|
| strategies/base.py | +7 |
| chain_processor.py (НОВЫЙ) | ~70 |
| strategy_runner.py | +5 |
| run_all.py | +3 |
| **ВСЕГО** | **~85** |

### Зависимости:
- Нет зависимостей от других доработок
- Интегрируется с ДОРАБОТКОЙ #3 (XLSX export)

### Возможности анализа после внедрения:
- Фильтр `is_chain_first=True` → только первые сигналы
- Фильтр `chain_total >= 3` → только длинные цепочки
- Группировка по `chain_seq` → какой seq самый прибыльный
- Тест разных `--dedup-days` → найти оптимальное значение

---

## ДОРАБОТКА #5: Улучшенная логика бэктестера

### Что хотим:
- Тестируем ВСЕ сигналы (для анализа паттернов)
- Маркируем каждый сигнал (chain_seq, trade_status)
- Для общей статистики - параметр какие сигналы считать
- Контроль позиций: 1 позиция на монету (опционально)

### Новые параметры:

#### Параметр 1: `--position-mode`

| Значение | Логика |
|----------|--------|
| `single` | **DEFAULT.** 1 позиция на монету (LONG или SHORT, не оба) |
| `direction` | 1 позиция на монету НА НАПРАВЛЕНИЕ (LONG и SHORT одновременно OK) |
| `multi` | Несколько позиций разрешено |

#### Параметр 2: `--count-seq`

| Значение | Что считаем в общей статистике |
|----------|-------------------------------|
| `1` | Только первые сигналы в цепочках |
| `2` | Только вторые сигналы |
| `all` | Все сигналы (текущее поведение) |
| `first-win` | Первый WIN в каждой цепочке |

---

### Логика бэктеста:

```
Для каждого сигнала:

1. Проверка position_mode:
   - Если уже есть открытая позиция по этой монете:
     - single: пропускаем (любое направление)
     - direction: пропускаем только то же направление
     - multi: торгуем

2. Если пропускаем:
   - trade_status = "skipped_position_open"
   - Записываем, НО не считаем в общую стату

3. Если торгуем:
   - Симулируем трейд (entry → SL/TP/TIMEOUT)
   - trade_status = "traded"
   - Записываем результат

4. Для общей статистики:
   - Фильтруем по count_seq параметру
```

---

### Маркировка сигналов:

| Поле | Тип | Описание |
|------|-----|----------|
| `trade_status` | str | `traded`, `skipped_position_open`, `skipped_no_volume` |
| `trade_result` | str | `WIN`, `LOSS`, `TIMEOUT`, `null` (если не торговался) |
| `position_id` | str | ID позиции (для группировки) |

---

### Пример работы:

```
Монета: BTCUSDT
dedup_days: 3
position_mode: single

Сигналы:
  Day 1: LONG, chain=C001, seq=1 → TRADED, позиция открыта
  Day 2: LONG, chain=C001, seq=2 → skipped_position_open
  Day 3: SHORT, chain=C001, seq=1 → skipped_position_open (single mode!)
  Day 5: позиция закрылась (WIN)
  Day 6: LONG, chain=C002, seq=1 → TRADED
```

---

### Статистика для анализа паттернов:

После бэктеста можем увидеть:

```
Статистика по chain_seq:
  seq=1: 150 трейдов, WR 42%, avg PnL +0.5%
  seq=2: 89 трейдов, WR 51%, avg PnL +1.2%  ← лучше!
  seq=3: 45 трейдов, WR 55%, avg PnL +1.8%  ← ещё лучше!
  seq=4: 20 трейдов, WR 48%, avg PnL +0.9%

Вывод: Оптимально брать 2-3 сигнал в цепочке
```

---

### Анализ кода - что менять:

#### Файл 1: `strategy_runner.py`

**backtest_signals()** - основные изменения:

```
Добавить параметры:
- position_mode: str = "single"
- count_seq: str = "all"

Добавить логику:
- Трекинг открытых позиций по символам
- Проверка перед каждым трейдом
- Маркировка пропущенных сигналов
- Фильтрация для общей статистики
```
**Изменение:** ~60 строк

#### Файл 2: `strategies/base.py`

**Signal dataclass** - добавить поля:
```
trade_status: str = ""
trade_result: str = ""
position_id: str = ""
```
**Изменение:** +3 строки (в дополнение к ДОРАБОТКЕ #4)

#### Файл 3: `run_all.py`

**argparse** - добавить параметры:
```
--position-mode single|direction|multi
--count-seq 1|2|3|all|first-win
```
**Изменение:** +10 строк

#### Файл 4: `BacktestResult` dataclass

**Добавить поля для детальной статистики:**
```
stats_by_seq: Dict[int, SeqStats]  # статистика по номеру в цепочке
skipped_count: int
skipped_reasons: Dict[str, int]
```
**Изменение:** +15 строк

---

### Итого по доработке #5:

| Файл | Строк |
|------|-------|
| strategy_runner.py | ~60 |
| strategies/base.py | +3 |
| run_all.py | +10 |
| BacktestResult | +15 |
| Логика подсчёта stats_by_seq | ~30 |
| **ВСЕГО** | **~118** |

---

### Зависимости:
- Требует ДОРАБОТКУ #4 (chain_seq должен быть заполнен)
- Интегрируется с ДОРАБОТКОЙ #3 (XLSX export покажет все поля)

---

### Возможности после внедрения:

1. **Анализ "зрелости" сигнала:**
   - Какой seq в цепочке самый прибыльный?

2. **Оптимизация стратегии:**
   - Если seq=2 лучше → в live торговать только вторые сигналы

3. **Риск-менеджмент:**
   - single mode предотвращает перегруз одной монеты

4. **Гибкость тестирования:**
   - Можно протестировать разные режимы и сравнить

---

## ДОРАБОТКА #6: Funding Fee + Trading Fees

### Что хотим:
- Учитывать реальные торговые комиссии (maker/taker)
- Учитывать Funding Fee за удержание позиции
- Точный расчёт PnL как в реальной торговле

---

### Часть 1: Trading Fees (комиссии)

#### Формула:
```
entry_fee = order_size × taker_fee
exit_fee = order_size × taker_fee
total_fee = entry_fee + exit_fee
```

#### Параметры:

| Параметр | Default | Описание |
|----------|---------|----------|
| `--taker-fee` | 0.0005 | 0.05% (консервативно) |
| `--maker-fee` | 0.0002 | 0.02% (если лимитный ордер) |

#### Пример:
```
Позиция: $1,000
Taker fee: 0.05%

Entry: $1,000 × 0.0005 = $0.50
Exit:  $1,000 × 0.0005 = $0.50
Total: $1.00 (0.10% от позиции)
```

---

### Часть 2: Funding Fee

#### Логика:
```
1. Определяем период удержания: entry_date → exit_date
2. Считаем сколько 8-часовых периодов попало (× 3 в день)
3. Берём реальные funding rates из funding_history
4. Суммируем с учётом направления
```

#### Формула:
```
funding_fee = сумма(position_size × funding_rate × direction)

direction:
  LONG + positive funding = -1 (платим)
  LONG + negative funding = +1 (получаем)
  SHORT + positive funding = +1 (получаем)
  SHORT + negative funding = -1 (платим)
```

#### Пример:
```
Позиция: $1,000 LONG
Hold: 5 дней = 15 funding периодов
Avg funding: +0.01%

Funding Fee = $1,000 × 0.0001 × 15 = $1.50 (платим)
```

---

### Итоговый PnL:

```
gross_pnl = (exit_price - entry_price) / entry_price × position_size
net_pnl = gross_pnl - trading_fees - funding_fee

Пример:
  Gross PnL: +$20 (+2%)
  Trading fees: -$0.80
  Funding fee: -$1.50
  Net PnL: +$17.70 (+1.77%)
```

---

### Новые поля в Trade:

| Поле | Тип | Описание |
|------|-----|----------|
| `pnl_gross_pct` | float | PnL до комиссий (%) |
| `pnl_gross_usd` | float | PnL до комиссий ($) |
| `trading_fee_usd` | float | Комиссии ($) |
| `funding_fee_usd` | float | Funding Fee ($) |
| `funding_periods` | int | Кол-во funding периодов |
| `pnl_net_pct` | float | PnL после всех комиссий (%) |
| `pnl_net_usd` | float | PnL после всех комиссий ($) |

---

### Анализ кода - что менять:

#### Файл 1: `strategy_runner.py`

**backtest_signals()** - добавить расчёт fees:
```
- Параметры: taker_fee, maker_fee, order_size_usd
- Для каждого трейда:
  - Посчитать trading_fee
  - Найти funding rates за период
  - Посчитать funding_fee
  - Вычислить net_pnl
```
**Изменение:** ~35 строк

#### Файл 2: `strategy_runner.py` - Trade dataclass

**Добавить поля:**
```
pnl_gross_pct: float
pnl_gross_usd: float
trading_fee_usd: float
funding_fee_usd: float
funding_periods: int
pnl_net_pct: float
pnl_net_usd: float
```
**Изменение:** +7 строк

#### Файл 3: `run_all.py`

**argparse:**
```
--taker-fee 0.0005
--maker-fee 0.0002
--order-size 100
```
**Изменение:** +5 строк

#### Файл 4: Новая функция `calculate_funding_fee()`

```
def calculate_funding_fee(
    symbol: str,
    direction: str,
    entry_date: datetime,
    exit_date: datetime,
    position_size: float,
    funding_history: List[Dict]
) -> Tuple[float, int]:
    """
    Считает funding fee за период.
    Returns: (total_fee, num_periods)
    """
```
**Изменение:** ~15 строк

---

### Источники данных:
- `funding_history` - УЖЕ ЕСТЬ (Binance API)
- Комиссии - параметры (реальные значения Binance)

---

### Итого по доработке #6:

| Файл | Строк |
|------|-------|
| strategy_runner.py (логика) | ~35 |
| strategy_runner.py (Trade поля) | +7 |
| run_all.py | +5 |
| calculate_funding_fee() | ~15 |
| **ВСЕГО** | **~62** |

---

### Зависимости:
- Нет зависимостей от других доработок
- Интегрируется с ДОРАБОТКОЙ #3 (XLSX покажет все fee поля)

---

### Влияние на результаты:

```
До учёта комиссий:
  100 трейдов, avg PnL: +1.5%, total: +150%

После учёта:
  Trading fees: 100 × 0.10% = 10%
  Funding fees: ~5% (зависит от hold time)
  Net total: +150% - 10% - 5% = +135%
```

**Реалистичнее на ~10-15%**

---

## ДОРАБОТКА #7: Max Drawdown

### Что хотим:
- Рассчитывать максимальную просадку (Max Drawdown)
- Режим: фиксированный размер ордера (Вариант A)
- Показывать в статистике и XLSX

---

### Логика расчёта:

```
1. Сортируем трейды по дате закрытия
2. Считаем cumulative PnL (накопительный)
3. Отслеживаем пик (максимум)
4. Считаем просадку от пика
5. Запоминаем максимальную просадку
```

---

### Пример:

```
Трейд 1: +$10  → Баланс: +$10  (пик: $10)
Трейд 2: -$5   → Баланс: +$5   (DD: $5)
Трейд 3: -$8   → Баланс: -$3   (DD: $13) ← MAX DD
Трейд 4: +$15  → Баланс: +$12  (пик: $12)
Трейд 5: +$12  → Баланс: +$24  (пик: $24)
Трейд 6: -$10  → Баланс: +$14  (DD: $10)

Max Drawdown = $13
Max Drawdown % = $13 / $10 (пик до DD) = нужен пик перед DD
```

---

### Формула Max Drawdown %:

```
Для каждой точки:
  drawdown = (peak - current) / peak × 100%

Max DD % = максимум из всех drawdown
```

---

### Новые поля в BacktestResult:

| Поле | Тип | Описание |
|------|-----|----------|
| `max_drawdown_usd` | float | Макс просадка в $ |
| `max_drawdown_pct` | float | Макс просадка в % от пика |
| `max_drawdown_date` | datetime | Когда была макс просадка |
| `peak_balance_usd` | float | Максимальный баланс |
| `calmar_ratio` | float | PnL / Max DD (качество стратегии) |
| `equity_curve` | List[float] | Кривая баланса (для графика) |

---

### Анализ кода - что менять:

#### Файл 1: `strategy_runner.py`

**backtest_signals()** - после сбора трейдов:
```
def calculate_drawdown(trades: List[Trade], order_size: float) -> DrawdownStats:
    # Сортируем по дате выхода
    sorted_trades = sorted(trades, key=lambda t: t.exit_date)

    balance = 0
    peak = 0
    max_dd = 0
    max_dd_date = None
    equity_curve = []

    for trade in sorted_trades:
        balance += trade.pnl_net_usd
        equity_curve.append(balance)

        if balance > peak:
            peak = balance

        dd = peak - balance
        if dd > max_dd:
            max_dd = dd
            max_dd_date = trade.exit_date

    max_dd_pct = (max_dd / peak * 100) if peak > 0 else 0
    calmar = (balance / max_dd) if max_dd > 0 else 0

    return DrawdownStats(...)
```
**Изменение:** ~30 строк

#### Файл 2: `strategy_runner.py` - BacktestResult

**Добавить поля:**
```
max_drawdown_usd: float = 0
max_drawdown_pct: float = 0
max_drawdown_date: Optional[datetime] = None
peak_balance_usd: float = 0
calmar_ratio: float = 0
equity_curve: List[float] = field(default_factory=list)
```
**Изменение:** +6 строк

#### Файл 3: `xlsx_exporter.py`

**Sheet "Summary"** - добавить:
```
Max Drawdown ($): ...
Max Drawdown (%): ...
Max DD Date: ...
Peak Balance: ...
Calmar Ratio: ...
```
**Изменение:** +5 строк (в рамках ДОРАБОТКИ #3)

---

### Интерпретация Calmar Ratio:

| Calmar | Оценка |
|--------|--------|
| < 1 | Плохо (DD больше прибыли) |
| 1-2 | Нормально |
| 2-3 | Хорошо |
| > 3 | Отлично |

---

### Итого по доработке #7:

| Файл | Строк |
|------|-------|
| strategy_runner.py (логика) | ~30 |
| strategy_runner.py (поля) | +6 |
| xlsx_exporter.py | +5 |
| **ВСЕГО** | **~41** |

---

### Зависимости:
- Требует ДОРАБОТКУ #6 (pnl_net_usd для точного расчёта)
- Интегрируется с ДОРАБОТКОЙ #3 (XLSX export)

---

## ДОРАБОТКА #8: Avg Hold Time по результату

### Что хотим:
- Среднее время удержания для WIN / LOSS / TIMEOUT
- Min / Max hold time
- Готовые метрики в BacktestResult и Summary sheet

---

### Новые поля в BacktestResult:

| Поле | Тип | Описание |
|------|-----|----------|
| `avg_hold_days_win` | float | Среднее дней для WIN |
| `avg_hold_days_loss` | float | Среднее дней для LOSS |
| `avg_hold_days_timeout` | float | Среднее дней для TIMEOUT |
| `min_hold_days` | int | Минимум дней |
| `max_hold_days_actual` | int | Максимум дней (факт) |
| `median_hold_days` | float | Медиана |

---

### Логика расчёта:

```
wins = [t.hold_days for t in trades if t.result == "WIN"]
losses = [t.hold_days for t in trades if t.result == "LOSS"]
timeouts = [t.hold_days for t in trades if t.result == "TIMEOUT"]

avg_hold_days_win = sum(wins) / len(wins) if wins else 0
avg_hold_days_loss = sum(losses) / len(losses) if losses else 0
avg_hold_days_timeout = sum(timeouts) / len(timeouts) if timeouts else 0

all_holds = [t.hold_days for t in trades]
min_hold_days = min(all_holds)
max_hold_days_actual = max(all_holds)
median_hold_days = median(all_holds)
```

---

### В XLSX Summary sheet:

```
| Метрика              | Значение |
|----------------------|----------|
| Avg Hold Days (WIN)  | 4.2      |
| Avg Hold Days (LOSS) | 2.1      |
| Avg Hold Days (TOUT) | 14.0     |
| Min Hold Days        | 1        |
| Max Hold Days        | 14       |
| Median Hold Days     | 5        |
```

---

### Анализ кода - что менять:

#### Файл 1: `strategy_runner.py` - BacktestResult

**Добавить поля:**
```
avg_hold_days_win: float = 0
avg_hold_days_loss: float = 0
avg_hold_days_timeout: float = 0
min_hold_days: int = 0
max_hold_days_actual: int = 0
median_hold_days: float = 0
```
**Изменение:** +6 строк

#### Файл 2: `strategy_runner.py` - backtest_signals()

**После сбора трейдов:**
```
# Считаем hold stats
wins = [t.hold_days for t in trades if t.result == "WIN"]
# ... и т.д.
```
**Изменение:** +12 строк

#### Файл 3: `xlsx_exporter.py`

**Summary sheet** - добавить строки
**Изменение:** +6 строк (в рамках ДОРАБОТКИ #3)

---

### Итого по доработке #8:

| Файл | Строк |
|------|-------|
| strategy_runner.py (поля) | +6 |
| strategy_runner.py (логика) | +12 |
| xlsx_exporter.py | +6 |
| **ВСЕГО** | **~24** |

---

### Зависимости:
- Нет зависимостей
- Интегрируется с ДОРАБОТКОЙ #3 (XLSX export)

---

### Полезность:

```
Avg Hold WIN: 3.5 дней
Avg Hold LOSS: 8.2 дней

Вывод: Лосы держим слишком долго, надо уменьшить max_hold_days
```

---

## ДОРАБОТКА #9: [ЖДЁМ ТВОЮ ИДЕЮ]

### Что хотим:
...

### Анализ кода:
...

---

## РАНЕЕ ОБСУЖДЁННЫЕ ИДЕИ (не в плане пока):

1. **Breakeven** - SL двигается в безубыток после +X%
2. **Trailing Stop** - SL следует за ценой
3. **Multi-TP** - TP1/TP2/TP3 с частичным закрытием
4. **Duplicate blocking** - 1 позиция на символ
5. **Trading fees** - maker/taker комиссии
6. **Funding rate** - стоимость удержания позиции
7. **Excel export** - как в старом бэктестере
8. **Avg hold time** - по WIN/LOSS/TIMEOUT
9. **Max drawdown** - расчёт просадки
10. **Scoring system** - многофакторный скоринг вместо бинарных условий

---

## ПРИНЦИПЫ КОДИНГА:

1. **Данные:** Только Binance API + Coinalyze API
2. **Entry:** Всегда next_candle.open (no look-ahead)
3. **Exit:** SL/TP проверяем с j=1 (следующий день после входа)
4. **Параметры:** Всё через argparse, никаких магических чисел в коде
5. **Тесты:** После каждой доработки - проверка на реальных данных

---

*Последнее обновление: 2026-03-01*
