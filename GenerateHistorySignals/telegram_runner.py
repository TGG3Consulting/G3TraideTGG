# -*- coding: utf-8 -*-
"""
Telegram Runner - Главный скрипт для отправки сигналов в Telegram.

Использование:
    python telegram_runner.py --top 20 --coin-regime --vol-filter-low
    python telegram_runner.py --symbols BTCUSDT,ETHUSDT --coin-regime
    python telegram_runner.py --top 10 --dry-run

Флаги:
    --config PATH       Путь к config.json (default: config.json)
    --symbols SYM,...   Список символов через запятую
    --top N             Топ N по объёму (если --symbols не указан)
    --coin-regime       Включить фильтр по режиму монеты
    --vol-filter-low    Фильтр низкой волатильности
    --vol-filter-high   Фильтр высокой волатильности
    --sl FLOAT          Stop Loss % (переопределяет config.json)
    --tp FLOAT          Take Profit % (переопределяет config.json)
    --dry-run           Не отправлять, только показать в консоли
"""

import sys
import io
import os
import json
import asyncio
import argparse
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from telegram import Bot

from hybrid_downloader import HybridHistoryDownloader
from strategies import StrategyConfig, get_strategy, DailyCandle, SMAEMAStrategy
from strategy_runner import StrategyRunner
from signal_filter import filter_signal, FilterResult
from telegram_sender import (
    format_group_alert,
    send_alert,
    save_signal_cache,
    load_sent_signals,
    save_sent_signal,
)
from ml.filter import MLSignalFilter, MLPrediction

# HTTP client for TradeBot
import aiohttp


# =============================================================================
# TRADEBOT INTEGRATION
# =============================================================================

async def send_signal_to_tradebot(
    tradebot_url: str,
    signal_data: Dict[str, Any],
    logger: logging.Logger,
) -> bool:
    """
    Отправляет сигнал в TradeBot API.

    НЕ ТРОГАЕТ логику генерации сигналов - только отправка!

    Args:
        tradebot_url: URL TradeBot API (e.g., http://127.0.0.1:8080)
        signal_data: Данные сигнала (из build_signal_data)
        logger: Логгер

    Returns:
        True если успешно, False если ошибка
    """
    endpoint = f"{tradebot_url.rstrip('/')}/signal"

    # Формируем payload для TradeBot API
    payload = {
        "signal_id": signal_data["signal_id"],
        "symbol": signal_data["symbol"],
        "direction": signal_data["direction"],
        "entry_price": signal_data["entry"],
        "stop_loss": signal_data["sl"],
        "take_profit": signal_data["tp"],
        "strategy": signal_data["strategy"],
        "signal_date": signal_data["date"],
        "reason": signal_data.get("reason", ""),
        "action": signal_data.get("regime_action", "FULL"),  # FULL/DYN/OFF
        "sl_pct": signal_data.get("sl_pct", 0.0),
        "tp_pct": signal_data.get("tp_pct", 0.0),
        "coin_regime": signal_data.get("coin_regime", ""),
        "coin_volatility": signal_data.get("coin_volatility", 0.0),
        "metadata": {
            "market_data": signal_data.get("market_data", {}),
            "indicators": signal_data.get("indicators", {}),
            "ml": signal_data.get("ml", {}),
        },
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                endpoint,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    logger.info(f"  TRADEBOT: {signal_data['signal_id']} -> OK (position_id={result.get('position_id', 'N/A')})")
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"  TRADEBOT ERROR: {signal_data['signal_id']} -> HTTP {response.status}: {error_text}")
                    return False
    except aiohttp.ClientConnectorError:
        logger.error(f"  TRADEBOT ERROR: Cannot connect to {tradebot_url}")
        return False
    except asyncio.TimeoutError:
        logger.error(f"  TRADEBOT ERROR: Timeout connecting to {tradebot_url}")
        return False
    except Exception as e:
        logger.error(f"  TRADEBOT ERROR: {signal_data['signal_id']} -> {type(e).__name__}: {e}")
        return False


# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================

ALL_STRATEGIES = ['ls_fade', 'momentum', 'reversal', 'mean_reversion', 'momentum_ls']
ALL_STRATEGIES_WITH_SMAEMA = ALL_STRATEGIES + ['smaema']

# SMAEMA required parameters
SMAEMA_REQUIRED_PARAMS = ['fast_type', 'fast_period', 'slow_type', 'slow_period', 'offset_pct', 'order_lifetime']


def setup_logging() -> logging.Logger:
    """Настройка логирования в консоль и файл."""
    os.makedirs("logs", exist_ok=True)

    log_filename = f"logs/telegram_{datetime.now().strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_filename, encoding='utf-8'),
            logging.StreamHandler(sys.stdout),
        ]
    )

    return logging.getLogger(__name__)


def load_config(config_path: str) -> Dict[str, Any]:
    """Загрузка config.json."""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


# =============================================================================
# ГЕНЕРАЦИЯ СИГНАЛОВ
# =============================================================================
# !!! КРИТИЧЕСКАЯ СЕКЦИЯ - НЕ ИЗМЕНЯТЬ !!!
# =============================================================================
# Эта функция - ГЛАВНАЯ точка входа для LIVE генерации сигналов.
# Все стратегии (ls_fade, momentum, mean_reversion, momentum_ls) были
# полностью протестированы и откалиброваны.
#
# КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО менять:
# - Вызов runner.generate_signals()
# - Логику создания StrategyRunner
# - Параметры по умолчанию
#
# Если нужно добавить SMAEMA или другие новые стратегии - делай отдельную
# ветку кода, НЕ ТРОГАЯ существующую логику для ls_fade/momentum/etc.
#
# Последняя проверка: 2025-03-06 - ВСЁ РАБОТАЕТ КОРРЕКТНО
# =============================================================================

def generate_signals_for_strategy(
    strategy_name: str,
    history: Dict,
    symbols: List[str],
    config: Dict[str, Any],
    dedup_days: int = 3,
    smaema_params: Optional[Dict[str, Any]] = None,
    data_interval: str = "daily",
) -> List:
    """
    Генерирует сигналы для одной стратегии.

    Args:
        strategy_name: Название стратегии
        history: Исторические данные
        symbols: Список символов
        config: Конфигурация
        dedup_days: Порог для группировки цепочек
        smaema_params: SMAEMA-specific params (if strategy is smaema)
        data_interval: Data interval ("daily", "4h", "1h", "15m", "5m", "1m")

    Returns:
        Список сигналов
    """
    # Параметры из конфига
    defaults = config.get("defaults", {})
    strategy_params = config.get("strategy_params", {}).copy()

    # Merge SMAEMA params if provided
    if strategy_name == 'smaema' and smaema_params:
        strategy_params.update(smaema_params)

    # Создаём конфиг стратегии
    strat_config = StrategyConfig(
        sl_pct=defaults.get("sl_pct", 4.0),
        tp_pct=defaults.get("tp_pct", 10.0),
        max_hold_days=defaults.get("max_hold_days", 14),
        lookback=7,
        params=strategy_params,
    )

    # Создаём runner (без ML)
    runner = StrategyRunner(
        strategy_name=strategy_name,
        config=strat_config,
        output_dir="output",
        use_ml=False,
        data_interval=data_interval,
    )

    # Генерируем сигналы (подавляем вывод)
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        signals = runner.generate_signals(history, symbols, dedup_days=dedup_days)
    finally:
        sys.stdout = old_stdout

    return signals


def get_candles_for_symbol(history: Dict, symbol: str, data_interval: str = "daily") -> List[DailyCandle]:
    """Получает свечи для символа с учетом интервала.

    Args:
        history: Исторические данные
        symbol: Символ
        data_interval: Интервал данных ("daily", "4h", "1h", "15m", "5m", "1m")

    Returns:
        Список свечей (агрегированных или passthrough для 1m)
    """
    if symbol not in history:
        return []

    raw = history[symbol]

    # Use static method - NO aggregation for 1m interval
    return StrategyRunner.aggregate_to_interval_static(raw.klines, data_interval)


def get_market_data_for_signal(history: Dict, signal, prev_candle: Optional[DailyCandle]) -> Dict[str, Any]:
    """
    Собирает рыночные данные для сигнала.

    Args:
        history: Исторические данные
        signal: Сигнал
        prev_candle: Предыдущая свеча

    Returns:
        Словарь с рыночными данными
    """
    symbol = signal.symbol
    raw = history.get(symbol)

    market_data = {
        "long_pct": 50.0,
        "short_pct": 50.0,
        "funding_rate": 0.0,
        "open_interest": 0,
        "volume_24h": 0,
    }

    if not raw:
        return market_data

    # L/S Ratio
    target_ts = int(signal.date.timestamp() * 1000)
    if raw.ls_ratio_history:
        best_ls = None
        best_diff = float('inf')
        for ls in raw.ls_ratio_history:
            ts = ls.get("timestamp", 0)
            diff = abs(ts - target_ts)
            if diff < best_diff and ts <= target_ts:
                best_diff = diff
                best_ls = ls
        if best_ls:
            market_data["long_pct"] = float(best_ls.get("longAccount", 0.5)) * 100
            market_data["short_pct"] = float(best_ls.get("shortAccount", 0.5)) * 100

    # Funding Rate
    if raw.funding_history:
        best_funding = None
        best_diff = float('inf')
        for f in raw.funding_history:
            ts = f.get("fundingTime", 0)
            diff = abs(ts - target_ts)
            if diff < best_diff and ts <= target_ts:
                best_diff = diff
                best_funding = f
        if best_funding:
            market_data["funding_rate"] = float(best_funding.get("fundingRate", 0))

    # Open Interest
    if raw.oi_history:
        best_oi = None
        best_diff = float('inf')
        for oi in raw.oi_history:
            ts = oi.get("timestamp", 0)
            diff = abs(ts - target_ts)
            if diff < best_diff and ts <= target_ts:
                best_diff = diff
                best_oi = oi
        if best_oi:
            market_data["open_interest"] = float(best_oi.get("sumOpenInterestValue", 0))

    # Volume 24h (из свечи)
    if prev_candle:
        market_data["volume_24h"] = prev_candle.quote_volume

    return market_data


def build_signal_data(
    signal,
    filter_result: FilterResult,
    history: Dict,
    config: Dict[str, Any],
    strategy_name: str,
    candles: List[DailyCandle],
) -> Dict[str, Any]:
    """
    Собирает полные данные сигнала для callback.

    Args:
        signal: Сигнал
        filter_result: Результат фильтрации
        history: Исторические данные
        config: Конфигурация
        strategy_name: Название стратегии
        candles: Свечи

    Returns:
        Полные данные для signal_cache.json
    """
    # Находим предыдущую свечу
    prev_candle = None
    candle_by_date = {c.date.strftime("%Y-%m-%d"): c for c in candles}
    prev_date = (signal.date - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_candle = candle_by_date.get(prev_date)

    # Рыночные данные
    market_data = get_market_data_for_signal(history, signal, prev_candle)

    # TP/SL %
    if signal.direction == "LONG":
        tp_pct = (signal.take_profit - signal.entry) / signal.entry * 100
        sl_pct = (signal.entry - signal.stop_loss) / signal.entry * 100
    else:
        tp_pct = (signal.entry - signal.take_profit) / signal.entry * 100
        sl_pct = (signal.stop_loss - signal.entry) / signal.entry * 100

    rr_ratio = tp_pct / sl_pct if sl_pct > 0 else 0

    # Signal ID
    signal_id = f"{signal.date.strftime('%Y%m%d')}_{signal.symbol}_{signal.direction}_{strategy_name}"

    # Свеча
    candle_data = {}
    if prev_candle:
        taker_buy_pct = 50.0
        if prev_candle.volume > 0:
            taker_buy_pct = (prev_candle.taker_buy_volume / prev_candle.volume) * 100

        candle_data = {
            "date": prev_candle.date.strftime("%Y-%m-%d"),
            "open": prev_candle.open,
            "high": prev_candle.high,
            "low": prev_candle.low,
            "close": prev_candle.close,
            "volume": prev_candle.volume,
            "quote_volume": prev_candle.quote_volume,
            "trades_count": prev_candle.trades_count,
            "taker_buy_pct": taker_buy_pct,
        }

    # Индикаторы
    indicators = {
        "adx": signal.metadata.get("adx", 0),
        "atr": 0,
        "atr_pct": filter_result.coin_volatility,
    }

    # ATR в абсолютных значениях
    if prev_candle and prev_candle.close > 0:
        indicators["atr"] = prev_candle.close * filter_result.coin_volatility / 100

    return {
        "signal_id": signal_id,
        "symbol": signal.symbol,
        "direction": signal.direction,
        "strategy": strategy_name,
        "date": signal.date.strftime("%Y-%m-%d %H:%M UTC"),
        "entry": signal.entry,
        "tp": signal.take_profit,
        "sl": signal.stop_loss,
        "tp_pct": tp_pct,
        "sl_pct": sl_pct,
        "rr_ratio": rr_ratio,
        "coin_regime": filter_result.coin_regime,
        "coin_regime_change_pct": filter_result.regime_change_pct,
        "coin_volatility": filter_result.coin_volatility,
        "regime_action": filter_result.regime_action,
        "market_data": market_data,
        "candle": candle_data,
        "indicators": indicators,
        "reason": signal.reason,
    }


# =============================================================================
# ML FEATURES
# =============================================================================

def build_ml_features(
    signal,
    candle: Optional[DailyCandle],
    prev_candle: Optional[DailyCandle],
    market_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Собирает фичи для ML предсказания.
    HONEST версия - использует данные ПРЕДЫДУЩЕГО дня.
    """
    # SL%, TP%, R:R
    if signal.direction == "LONG":
        sl_pct = (signal.entry - signal.stop_loss) / signal.entry * 100
        tp_pct = (signal.take_profit - signal.entry) / signal.entry * 100
    else:
        sl_pct = (signal.stop_loss - signal.entry) / signal.entry * 100
        tp_pct = (signal.entry - signal.take_profit) / signal.entry * 100

    rr_ratio = tp_pct / sl_pct if sl_pct > 0 else 0

    features = {
        # Market Data
        'Long %': market_data.get('long_pct', 50.0) / 100,
        'Short %': market_data.get('short_pct', 50.0) / 100,
        'Funding Rate': market_data.get('funding_rate', 0) * 100,
        'OI USD': market_data.get('open_interest', 0),
        'OI Contracts': 0,

        # Open price
        'Open': candle.open if candle else signal.entry,

        # Previous day candle (HONEST)
        'Prev High': prev_candle.high if prev_candle else 0.0,
        'Prev Low': prev_candle.low if prev_candle else 0.0,
        'Prev Close': prev_candle.close if prev_candle else 0.0,
        'Prev Volume': prev_candle.volume if prev_candle else 0.0,
        'Prev Volume USD': prev_candle.quote_volume if prev_candle else 0.0,
        'Prev Trades Count': prev_candle.trades_count if prev_candle else 0,
        'Prev Taker Buy Vol': prev_candle.taker_buy_volume if prev_candle else 0.0,
        'Prev Taker Buy USD': prev_candle.taker_buy_quote_volume if prev_candle else 0.0,

        # Indicators
        'ADX': signal.metadata.get('adx', 0.0),

        # Trade params
        'SL %': sl_pct,
        'TP %': tp_pct,
        'R:R Ratio': rr_ratio,

        # Chain
        'Chain Seq': signal.chain_seq,
        'Gap Days': signal.chain_gap_days,
        'Chain First': signal.is_chain_first,

        # Time
        'DayOfWeek': signal.date.weekday(),
        'Month': signal.date.month,
        'Hour': signal.date.hour,
    }

    return features


# =============================================================================
# MAIN
# =============================================================================

async def main():
    parser = argparse.ArgumentParser(description="Telegram Signal Runner")
    parser.add_argument("--config", type=str, default="config.json", help="Path to config.json")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbols")
    parser.add_argument("--top", type=int, default=20, help="Top N symbols by volume")
    parser.add_argument("--coin-regime", action="store_true", help="Enable coin regime filter")
    parser.add_argument("--vol-filter-low", action="store_true", help="Enable low volatility filter")
    parser.add_argument("--vol-filter-high", action="store_true", help="Enable high volatility filter")
    parser.add_argument("--sl", type=float, default=None, help="Stop Loss %")
    parser.add_argument("--tp", type=float, default=None, help="Take Profit %")
    parser.add_argument("--dry-run", action="store_true", help="Don't send, just print")
    parser.add_argument("--strategies", type=str, default="all", help="Strategies: all or comma-separated (ls_fade,momentum,reversal,mean_reversion,momentum_ls,smaema)")
    parser.add_argument("--strategy", type=str, default=None, help="Single strategy to run (e.g., smaema)")
    parser.add_argument("--ml", action="store_true", help="Enable ML filtering of signals")
    parser.add_argument("--ml-model-dir", type=str, default="models", help="Directory with ML models")

    # SMAEMA parameters (all required for SMAEMA strategy)
    parser.add_argument("--bar", type=str, default="daily", help="Timeframe for SMAEMA: 1, 5, 15, 60, 240, daily")
    parser.add_argument("--fast-type", type=str, default=None, help="SMAEMA fast MA type: SMA or EMA")
    parser.add_argument("--fast-period", type=int, default=None, help="SMAEMA fast MA period")
    parser.add_argument("--slow-type", type=str, default=None, help="SMAEMA slow MA type: SMA or EMA")
    parser.add_argument("--slow-period", type=int, default=None, help="SMAEMA slow MA period")
    parser.add_argument("--offset-pct", type=float, default=None, help="SMAEMA entry offset %% (+ above, - below)")
    parser.add_argument("--order-lifetime", type=int, default=None, help="SMAEMA candles to wait for entry")

    # ==========================================================================
    # TRADEBOT INTEGRATION - Параметры для интеграции с TradeBot
    # ==========================================================================
    parser.add_argument("--tradebot-url", type=str, default=None,
                        help="TradeBot API URL (e.g., http://127.0.0.1:8080). If set, signals are sent to TradeBot")
    parser.add_argument("--continuous", action="store_true",
                        help="Run continuously (don't exit after one cycle)")
    parser.add_argument("--interval", type=int, default=86400,
                        help="Interval between runs in seconds (default: 86400 = 24h). Only with --continuous")

    args = parser.parse_args()

    # Convert --bar from numeric to interval format
    BAR_CONVERSION = {
        "1": "1m", "5": "5m", "15": "15m", "60": "1h", "240": "4h",
        "1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h",
        "daily": "daily"
    }
    if args.bar not in BAR_CONVERSION:
        print(f"ERROR: Invalid --bar value: {args.bar}")
        print(f"Valid values: 1, 5, 15, 60, 240, daily (or 1m, 5m, 15m, 1h, 4h)")
        sys.exit(1)
    args.bar = BAR_CONVERSION[args.bar]

    # Настройка логирования
    log = setup_logging()
    log.info("=" * 60)
    log.info("TELEGRAM SIGNAL RUNNER")
    log.info("=" * 60)

    # Загрузка конфига
    config = load_config(args.config)
    log.info(f"Config: {args.config}")

    # Переопределение SL/TP если указано
    if args.sl is not None:
        config["defaults"]["sl_pct"] = args.sl
    if args.tp is not None:
        config["defaults"]["tp_pct"] = args.tp

    # Дата сигнала (вчерашняя закрытая свеча UTC)
    now_utc = datetime.now(timezone.utc)
    signal_date = (now_utc - timedelta(days=1)).date()
    log.info(f"Signal date: {signal_date} (yesterday's closed candle)")

    # ПРОВЕРКА АКТУАЛЬНОСТИ: свеча должна быть закрыта (UTC 00:00+)
    if now_utc.hour == 0 and now_utc.minute < 5:
        log.warning("WARNING: Running before UTC 00:05 - candle may not be fully closed!")
        log.warning(f"Current UTC time: {now_utc.strftime('%H:%M:%S')}")
        log.warning("Recommended to run after 00:05 UTC for reliable data.")

    # Определяем символы
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        log.info(f"Symbols: {len(symbols)} (from --symbols)")
    else:
        log.info(f"Fetching top {args.top} symbols...")
        downloader = HybridHistoryDownloader(cache_dir='cache')
        symbols = downloader.get_active_symbols(top_n=args.top)
        log.info(f"Symbols: {len(symbols)} (top by volume)")

    # Фильтры
    log.info(f"Filters: coin_regime={args.coin_regime}, vol_low={args.vol_filter_low}, vol_high={args.vol_filter_high}, ml={args.ml}")
    log.info(f"Mode: {'DRY-RUN' if args.dry_run else 'LIVE'}")

    # ML Filter
    ml_filter = None
    if args.ml:
        log.info(f"Loading ML models from {args.ml_model_dir}...")
        try:
            ml_filter = MLSignalFilter(model_dir=args.ml_model_dir, per_strategy=True)
            ml_filter.load()
            log.info(f"ML models loaded: {ml_filter.get_loaded_strategies()}")
        except Exception as e:
            log.error(f"Failed to load ML models: {e}")
            log.warning("Continuing without ML filter")
            ml_filter = None

    # Загружаем данные
    log.info("Loading historical data...")
    start = datetime(signal_date.year, signal_date.month, signal_date.day, tzinfo=timezone.utc) - timedelta(days=30)
    end = datetime(signal_date.year, signal_date.month, signal_date.day, 23, 59, 59, tzinfo=timezone.utc)

    downloader = HybridHistoryDownloader(
        cache_dir='cache',
        coinalyze_api_key=config.get("coinalyze_api_key", ""),
        data_interval=args.bar
    )
    history = downloader.download_with_coinalyze_backfill(symbols, start, end)
    log.info(f"Loaded data for {len(history)} symbols")

    # Загружаем отправленные сигналы
    sent_ids = load_sent_signals()
    log.info(f"Already sent: {len(sent_ids)} signals")

    # Инициализируем бота
    bot = None
    if not args.dry_run:
        bot = Bot(token=config["telegram"]["bot_token"])
        log.info("Telegram bot initialized")

    # Статистика
    total_signals = 0
    total_sent = 0
    total_skipped_filter = 0
    total_skipped_dup = 0

    # Check SMAEMA parameters
    smaema_params = {
        'fast_type': args.fast_type,
        'fast_period': args.fast_period,
        'slow_type': args.slow_type,
        'slow_period': args.slow_period,
        'offset_pct': args.offset_pct,
        'order_lifetime': args.order_lifetime,
    }
    smaema_params_available = all(v is not None for v in smaema_params.values())
    smaema_missing_params = [k for k, v in smaema_params.items() if v is None]

    # Определяем стратегии
    # --strategy (singular) takes precedence over --strategies
    if args.strategy:
        # Single strategy mode
        requested = [args.strategy.lower()]
    elif args.strategies == "all":
        strategies_to_run = ALL_STRATEGIES.copy()
        # Add SMAEMA only if all params provided
        if smaema_params_available:
            strategies_to_run.append('smaema')
            log.info(f"SMAEMA included: params OK")
        else:
            log.warning(f"WARNING: SMAEMA strategy skipped - missing required parameters.")
            log.warning(f"         Missing: {', '.join('--' + p.replace('_', '-') for p in smaema_missing_params)}")
            log.warning(f"         To run SMAEMA, specify: --fast-type, --fast-period, --slow-type,")
            log.warning(f"         --slow-period, --offset-pct, --order-lifetime, --tp, --sl")
        requested = None  # Flag to skip the loop
    else:
        requested = [s.strip().lower() for s in args.strategies.split(",") if s.strip()]

    # Process requested list (for --strategy or comma-separated --strategies)
    if requested is not None:
        strategies_to_run = []

        for s in requested:
            if s == 'smaema':
                # SMAEMA requires all params
                if not smaema_params_available:
                    log.error(f"ERROR: Missing required SMAEMA parameters: {', '.join('--' + p.replace('_', '-') for p in smaema_missing_params)}")
                    log.error(f"SMAEMA strategy requires all parameters to be specified.")
                    log.error(f"")
                    log.error(f"Required parameters:")
                    log.error(f"  --fast-type      (SMA or EMA)")
                    log.error(f"  --fast-period    (integer)")
                    log.error(f"  --slow-type      (SMA or EMA)")
                    log.error(f"  --slow-period    (integer)")
                    log.error(f"  --offset-pct     (float, + above price, - below price)")
                    log.error(f"  --order-lifetime (integer, in candles)")
                    log.error(f"  --tp             (float, %%)")
                    log.error(f"  --sl             (float, %%)")
                    return
                strategies_to_run.append('smaema')
            elif s in ALL_STRATEGIES:
                strategies_to_run.append(s)
            else:
                log.warning(f"Unknown strategy: {s}")

        if not strategies_to_run:
            log.error(f"No valid strategies found. Available: {ALL_STRATEGIES_WITH_SMAEMA}")
            return

    log.info(f"Strategies: {strategies_to_run}")

    # Для каждой стратегии
    for strategy_name in strategies_to_run:
        log.info(f"\n--- Strategy: {strategy_name} ---")

        # Prepare SMAEMA params if needed
        smaema_strategy_params = None
        if strategy_name == 'smaema' and smaema_params_available:
            smaema_strategy_params = {
                'fast_type': args.fast_type,
                'fast_period': args.fast_period,
                'slow_type': args.slow_type,
                'slow_period': args.slow_period,
                'offset_pct': args.offset_pct,
                'order_lifetime': args.order_lifetime,
            }

        # Генерируем сигналы
        signals = generate_signals_for_strategy(
            strategy_name=strategy_name,
            history=history,
            symbols=symbols,
            config=config,
            dedup_days=config["defaults"].get("dedup_days", 3),
            smaema_params=smaema_strategy_params,
            data_interval=args.bar,
        )
        log.info(f"Generated: {len(signals)} signals")

        # Фильтруем по дате
        today_signals = [s for s in signals if s.date.date() == signal_date]
        log.info(f"Today's signals: {len(today_signals)}")
        total_signals += len(today_signals)

        for signal in today_signals:
            signal_id = f"{signal.date.strftime('%Y%m%d')}_{signal.symbol}_{signal.direction}_{strategy_name}"

            # Проверка дубликатов
            if signal_id in sent_ids:
                log.info(f"  SKIP (duplicate): {signal_id}")
                total_skipped_dup += 1
                continue

            # Получаем свечи (с учетом интервала - NO aggregation for 1m)
            candles = get_candles_for_symbol(history, signal.symbol, data_interval=args.bar)

            # Применяем фильтры
            filter_result = filter_signal(
                signal=signal,
                candles=candles,
                strategy_name=strategy_name,
                coin_regime_enabled=args.coin_regime,
                vol_filter_low_enabled=args.vol_filter_low,
                vol_filter_high_enabled=args.vol_filter_high,
                coin_regime_lookback=config["defaults"].get("coin_regime_lookback", 14),
            )

            if not filter_result.passed:
                log.info(f"  SKIP ({filter_result.skip_reason}): {signal_id}")
                total_skipped_filter += 1
                continue

            # ML Prediction (рекомендация, не фильтр)
            ml_prediction = None
            if ml_filter is not None:
                # Находим свечи для ML
                candle_by_date = {c.date.strftime("%Y-%m-%d"): c for c in candles}
                signal_date_str = signal.date.strftime("%Y-%m-%d")
                prev_date_str = (signal.date - timedelta(days=1)).strftime("%Y-%m-%d")
                candle = candle_by_date.get(signal_date_str)
                prev_candle = candle_by_date.get(prev_date_str)

                # Рыночные данные для ML
                market_data = get_market_data_for_signal(history, signal, prev_candle)

                # Собираем фичи
                ml_features = build_ml_features(signal, candle, prev_candle, market_data)

                # Предсказание (НЕ фильтруем, только рекомендация)
                ml_prediction = ml_filter.predict(
                    signal_data=ml_features,
                    strategy=strategy_name,
                    symbol=signal.symbol,
                    direction=signal.direction,
                )

                # Логируем рекомендацию (не skip!)
                ml_status = "рекомендует" if ml_prediction.should_trade else "НЕ рекомендует"
                log.info(f"  ML {ml_status}: {signal_id}")

            # Собираем данные
            signal_data = build_signal_data(
                signal=signal,
                filter_result=filter_result,
                history=history,
                config=config,
                strategy_name=strategy_name,
                candles=candles,
            )

            # Добавляем ML данные
            if ml_prediction is not None:
                signal_data["ml"] = {
                    "enabled": True,
                    "recommends": ml_prediction.should_trade,
                    "confidence": round(ml_prediction.confidence, 3),
                    "filter_score": round(ml_prediction.filter_score, 3),
                    "reason": ml_prediction.reason,
                    "predicted_direction": ml_prediction.predicted_direction,
                    "predicted_sl": round(ml_prediction.predicted_sl, 2),
                    "predicted_tp": round(ml_prediction.predicted_tp, 2),
                    "predicted_lifetime": round(ml_prediction.predicted_lifetime, 1),
                }
            else:
                signal_data["ml"] = {"enabled": False}

            # =================================================================
            # ОТПРАВКА В TRADEBOT (если указан --tradebot-url)
            # =================================================================
            if args.tradebot_url:
                await send_signal_to_tradebot(
                    tradebot_url=args.tradebot_url,
                    signal_data=signal_data,
                    logger=log,
                )

            # Отправляем или dry-run
            if args.dry_run:
                text, keyboard = format_group_alert(
                    signal=signal,
                    strategy_name=strategy_name,
                    coin_regime=filter_result.coin_regime,
                    regime_action=filter_result.regime_action,
                    coin_volatility=filter_result.coin_volatility,
                    signal_id=signal_id,
                    ml_data=signal_data.get("ml"),
                )
                log.info(f"  DRY-RUN: {signal_id}")
                print("\n" + text + "\n")
                # Сохраняем в кэш даже для dry-run (для тестирования callback)
                save_signal_cache(signal_id, signal_data)
                total_sent += 1
            else:
                success = await send_alert(
                    bot=bot,
                    chat_id=config["telegram"]["chat_id"],
                    signal=signal,
                    signal_data=signal_data,
                    strategy_name=strategy_name,
                )
                if success:
                    save_sent_signal(signal_id)
                    sent_ids.add(signal_id)
                    log.info(f"  SENT: {signal_id}")
                    total_sent += 1
                else:
                    log.error(f"  FAILED: {signal_id}")

                # Rate limit: 3 сек между сообщениями (Telegram лимит ~20 msg/min в группу)
                await asyncio.sleep(3)

    # Итоги
    log.info("\n" + "=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    log.info(f"Total signals today: {total_signals}")
    log.info(f"Sent: {total_sent}")
    log.info(f"Skipped (filter): {total_skipped_filter}")
    log.info(f"Skipped (duplicate): {total_skipped_dup}")
    log.info("=" * 60)

    return total_sent  # Возвращаем количество отправленных для внешнего использования


async def main_continuous():
    """
    Wrapper для непрерывного режима работы.

    Парсит аргументы и запускает main() в цикле с заданным интервалом.
    """
    parser = argparse.ArgumentParser(description="Telegram Signal Runner")
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--interval", type=int, default=86400)
    args, _ = parser.parse_known_args()

    if not args.continuous:
        # Одиночный запуск
        await main()
        return

    # Непрерывный режим
    print("=" * 60)
    print("CONTINUOUS MODE ENABLED")
    print(f"Interval: {args.interval} seconds ({args.interval / 3600:.1f} hours)")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    cycle = 0
    while True:
        cycle += 1
        print(f"\n{'='*60}")
        print(f"CYCLE {cycle} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}\n")

        try:
            await main()
        except Exception as e:
            print(f"ERROR in cycle {cycle}: {e}")
            import traceback
            traceback.print_exc()

        # Ждём следующий цикл
        next_run = datetime.now() + timedelta(seconds=args.interval)
        print(f"\nNext run at: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Sleeping for {args.interval} seconds...")

        try:
            await asyncio.sleep(args.interval)
        except asyncio.CancelledError:
            print("\nShutdown requested. Exiting...")
            break


if __name__ == "__main__":
    try:
        asyncio.run(main_continuous())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Goodbye!")
