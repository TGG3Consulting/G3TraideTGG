# -*- coding: utf-8 -*-
"""
Trade App - LIVE Trading Application.

Это ЗАМЕНА run_all.py для LIVE торговли.

run_all.py:
    1. Скачать данные
    2. generate_signals() → сигналы
    3. backtest_signals() → симуляция

TradeApp:
    1. Скачать данные
    2. generate_signals() → сигналы
    3. execute_signal() → РЕАЛЬНЫЕ ордера на бирже

Usage:
    python -m tradebot.trade_app --symbols BTCUSDT,ETHUSDT --interval 300
    python -m tradebot.trade_app --top 10 --testnet
"""

import asyncio
import argparse
import json
import logging
import logging.handlers
import os
import signal
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

# Fix Windows console encoding
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Добавляем путь к GenerateHistorySignals
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'GenerateHistorySignals'))

from hybrid_downloader import HybridHistoryDownloader
from strategies import StrategyConfig
from strategy_runner import StrategyRunner

# Импорт из tradebot
from .engine import TradeEngine, PositionManager, StateManager, MetricsTracker
from .adapters import BinanceFuturesAdapter
from .core.models import Position

# Импорт фильтров из strategy_runner
from strategy_runner import (
    COIN_REGIME_MATRIX,
    VOL_FILTER_THRESHOLDS,
    MONTH_DATA,
    DAY_DATA,
    calculate_coin_regime,
    calculate_volatility,
)

# ML Filter (optional)
try:
    from ml.filter import MLSignalFilter
    ML_AVAILABLE = True
except ImportError:
    ML_AVAILABLE = False
    MLSignalFilter = None

# Logger (настраивается в setup_logging)
logger = logging.getLogger(__name__)

# Config file paths
CONFIG_DIR = Path(__file__).parent.parent / "config"
BINANCE_API_CONFIG = CONFIG_DIR / "binance_api.json"
TRAILING_STOP_CONFIG = CONFIG_DIR / "trailing_stop.json"
TELEGRAM_CONFIG = CONFIG_DIR / "telegram.json"


def load_binance_api_config(testnet: bool = False) -> Tuple[str, str]:
    """
    Загрузить API ключи из config/binance_api.json.

    Args:
        testnet: True для testnet ключей, False для mainnet

    Returns:
        Tuple[api_key, api_secret]
    """
    if not BINANCE_API_CONFIG.exists():
        return "", ""

    try:
        with open(BINANCE_API_CONFIG, "r", encoding="utf-8") as f:
            config = json.load(f)

        if testnet:
            api_key = config.get("testnet_api_key", "")
            api_secret = config.get("testnet_api_secret", "")
        else:
            api_key = config.get("api_key", "")
            api_secret = config.get("api_secret", "")

        # Проверяем что ключи не placeholder
        if api_key.startswith("YOUR_") or api_secret.startswith("YOUR_"):
            return "", ""

        return api_key, api_secret

    except Exception as e:
        print(f"Warning: Failed to load binance_api.json: {e}")
        return "", ""


def load_trailing_stop_config() -> Dict[str, Any]:
    """
    Загрузить настройки trailing stop из config/trailing_stop.json.

    Returns:
        Dict с настройками trailing stop
    """
    defaults = {
        "enabled": False,
        "callback_rate": 1.0,
        "activation_price_pct": None,
        "use_instead_of_tp": True,
    }

    if not TRAILING_STOP_CONFIG.exists():
        return defaults

    try:
        with open(TRAILING_STOP_CONFIG, "r", encoding="utf-8") as f:
            config = json.load(f)

        return {
            "enabled": config.get("enabled", defaults["enabled"]),
            "callback_rate": config.get("callback_rate", defaults["callback_rate"]),
            "activation_price_pct": config.get("activation_price_pct", defaults["activation_price_pct"]),
            "use_instead_of_tp": config.get("use_instead_of_tp", defaults["use_instead_of_tp"]),
        }

    except Exception as e:
        print(f"Warning: Failed to load trailing_stop.json: {e}")
        return defaults


def load_telegram_config() -> Tuple[str, str]:
    """
    Загрузить Telegram credentials из config/telegram.json.

    Returns:
        Tuple[bot_token, chat_id]
    """
    if not TELEGRAM_CONFIG.exists():
        return "", ""

    try:
        with open(TELEGRAM_CONFIG, "r", encoding="utf-8") as f:
            config = json.load(f)

        bot_token = config.get("bot_token", "")
        chat_id = config.get("chat_id", "")

        return bot_token, chat_id

    except Exception as e:
        print(f"Warning: Failed to load telegram.json: {e}")
        return "", ""


def setup_logging(
    log_file: Optional[str] = None,
    log_level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> None:
    """
    Настройка логирования: консоль + файл (опционально).

    Args:
        log_file: Путь к файлу логов (None = только консоль)
        log_level: Уровень логирования (DEBUG, INFO, WARNING, ERROR)
        max_bytes: Максимальный размер файла (default: 10MB)
        backup_count: Количество backup файлов (default: 5)
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Формат логов
    log_format = '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    formatter = logging.Formatter(log_format, datefmt=date_format)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Очищаем существующие handlers
    root_logger.handlers.clear()

    # Console handler (всегда)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler (опционально)
    if log_file:
        # Создаём директорию если не существует
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # RotatingFileHandler - ротация по размеру
        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8',
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        print(f"Logging to file: {log_file} (max {max_bytes // 1024 // 1024}MB, {backup_count} backups)")


# Стратегии для торговли (можно ограничить список)
DEFAULT_STRATEGIES = ['ls_fade', 'momentum', 'reversal', 'mean_reversion', 'momentum_ls']


class TradeApp:
    """
    LIVE Trading Application.

    Аналог run_all.py, но торгует на реальной бирже.

    Паттерн:
        while True:
            1. Скачать свежие данные
            2. generate_signals() → сигналы
            3. filter (coin_regime, vol_filter, etc.)
            4. execute_signal() → реальные ордера
            5. send_telegram_alert()
            6. sleep(interval)
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = True,
        symbols: Optional[List[str]] = None,
        top_n: int = 10,
        strategies: Optional[List[str]] = None,
        interval_sec: int = 300,
        # !!! НЕ МЕНЯТЬ БЕЗ ЯВНОГО УКАЗАНИЯ ПОЛЬЗОВАТЕЛЯ !!!
        order_size_usd: float = 100.0,
        leverage: int = 10,
        sl_pct: float = 4.0,
        tp_pct: float = 10.0,
        max_hold_days: int = 14,
        telegram_bot_token: str = "",
        telegram_chat_id: str = "",
        # === FILTERS (SAME AS BACKTESTER run_all.py) ===
        coin_regime_enabled: bool = False,
        coin_regime_lookback: int = 14,
        vol_filter_low_enabled: bool = False,
        vol_filter_high_enabled: bool = False,
        dedup_days: int = 3,
        position_mode: str = "single",
        # === DYNAMIC SIZING ===
        dynamic_size_enabled: bool = False,
        protected_size: float = 100.0,  # Size after LOSS (normal size = order_size_usd)
        # === MONTH/DAY FILTERS (STATIC from MONTH_DATA/DAY_DATA) ===
        month_off_dd: Optional[float] = None,
        month_off_pnl: Optional[float] = None,
        day_off_dd: Optional[float] = None,
        day_off_pnl: Optional[float] = None,
        # === ML FILTER ===
        use_ml: bool = False,
        ml_model_dir: str = "models",
        ml_min_confidence: float = 0.35,
        ml_min_filter_score: float = 0.45,
        # === RISK MANAGEMENT ===
        daily_max_dd: float = 5.0,
        monthly_max_dd: float = 20.0,
        # === METRICS ===
        stats_interval: int = 0,  # 0 = disabled, >0 = interval in cycles
        # === TRAILING STOP ===
        trailing_stop_enabled: bool = False,
        trailing_stop_callback_rate: float = 1.0,
        trailing_stop_activation_pct: Optional[float] = None,
        trailing_stop_use_instead_of_tp: bool = True,
        # === LATE SIGNAL PROTECTION ===
        late_signal_skip_after_utc: Optional[int] = 3,  # Skip signals for today if past this hour UTC
    ):
        """
        Инициализация TradeApp.

        Args:
            api_key: Binance API key
            api_secret: Binance API secret
            testnet: True для testnet, False для mainnet
            symbols: Список символов или None для top_n
            top_n: Топ N символов по объёму (если symbols не указан)
            strategies: Список стратегий или None для всех
            interval_sec: Интервал между циклами (секунды)
            order_size_usd: Размер позиции в USD
            leverage: Плечо
            sl_pct: Stop Loss %
            tp_pct: Take Profit %
            max_hold_days: Макс. время удержания позиции
            telegram_bot_token: Telegram bot token
            telegram_chat_id: Telegram chat ID
            coin_regime_enabled: Включить COIN REGIME фильтр (использует COIN_REGIME_MATRIX)
            coin_regime_lookback: Lookback для coin regime (default 14)
            vol_filter_low_enabled: Включить VOL LOW фильтр (skip if vol < threshold)
            vol_filter_high_enabled: Включить VOL HIGH фильтр (skip if vol > threshold)
            dedup_days: Дедупликация сигналов (default 3 дня)
            position_mode: single/direction/multi (default single)
            dynamic_size_enabled: Включить динамический размер позиции
            protected_size: Размер после LOSS (default $100, после WIN = order_size_usd)
            month_off_dd: Skip месяцы где MaxDD > X% (lookup из MONTH_DATA)
            month_off_pnl: Skip месяцы где PnL < X% (lookup из MONTH_DATA)
            day_off_dd: Skip дни где MaxDD > X% (lookup из DAY_DATA)
            day_off_pnl: Skip дни где PnL < X% (lookup из DAY_DATA)
            use_ml: Включить ML фильтр (требует ml/filter.py и models/)
            ml_model_dir: Директория с ML моделями
            ml_min_confidence: Минимальный confidence для ML
            ml_min_filter_score: Минимальный filter score для ML
            daily_max_dd: Stop trading если дневной PnL < -X% (default 5%)
            monthly_max_dd: Stop trading если месячный PnL < -X% (default 20%)
            stats_interval: Интервал вывода статистики в циклах (0 = только при остановке)
            trailing_stop_enabled: Включить trailing stop вместо/в дополнение к TP
            trailing_stop_callback_rate: Процент отката (0.1-5.0, default 1.0 = 1%)
            trailing_stop_activation_pct: Активация при X% профита (None = сразу)
            trailing_stop_use_instead_of_tp: True = заменить TP, False = в дополнение
            late_signal_skip_after_utc: Skip сигналы за текущий день если время > X:00 UTC (default 3, None = выключено)
        """
        self.testnet = testnet
        self.symbols = symbols
        self.top_n = top_n
        self.strategies = strategies or DEFAULT_STRATEGIES
        self.interval_sec = interval_sec
        self.order_size_usd = order_size_usd
        self.leverage = leverage
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct
        self.max_hold_days = max_hold_days
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id

        # === FILTERS (SAME AS BACKTESTER) ===
        self.coin_regime_enabled = coin_regime_enabled
        self.coin_regime_lookback = coin_regime_lookback
        self.vol_filter_low_enabled = vol_filter_low_enabled
        self.vol_filter_high_enabled = vol_filter_high_enabled
        self.dedup_days = dedup_days
        self.position_mode = position_mode

        # === DYNAMIC SIZING ===
        self.dynamic_size_enabled = dynamic_size_enabled
        self.protected_size = protected_size  # После LOSS, после WIN = order_size_usd
        self._last_trade_was_win: bool = True  # Для dynamic sizing (начинаем с normal)

        # === MONTH/DAY FILTERS ===
        self.month_off_dd = month_off_dd
        self.month_off_pnl = month_off_pnl
        self.day_off_dd = day_off_dd
        self.day_off_pnl = day_off_pnl

        # === ML FILTER ===
        self.use_ml = use_ml
        self.ml_model_dir = ml_model_dir
        self.ml_min_confidence = ml_min_confidence
        self.ml_min_filter_score = ml_min_filter_score
        self.ml_filter: Optional[Any] = None  # Инициализируется в start() если use_ml=True

        # === TRAILING STOP ===
        self.trailing_stop_enabled = trailing_stop_enabled
        self.trailing_stop_callback_rate = trailing_stop_callback_rate
        self.trailing_stop_activation_pct = trailing_stop_activation_pct
        self.trailing_stop_use_instead_of_tp = trailing_stop_use_instead_of_tp

        # === LATE SIGNAL PROTECTION ===
        self.late_signal_skip_after_utc = late_signal_skip_after_utc

        # === RISK MANAGEMENT ===
        self.daily_max_dd = daily_max_dd
        self.monthly_max_dd = monthly_max_dd
        self._current_day_pnl: float = 0.0
        self._current_month_pnl: float = 0.0
        self._last_day: Optional[int] = None
        self._last_month: Optional[int] = None
        self._daily_stopped: bool = False
        self._monthly_stopped: bool = False
        self._daily_alert_sent: bool = False
        self._monthly_alert_sent: bool = False
        self._keyboard_listener_task: Optional[asyncio.Task] = None
        self._state_save_task: Optional[asyncio.Task] = None
        self._state_save_interval: int = 300  # 5 минут

        # Компоненты
        self.exchange = BinanceFuturesAdapter(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
        )
        self.trade_engine = TradeEngine(
            exchange=self.exchange,
            default_order_size_usd=order_size_usd,
            default_leverage=leverage,
            max_hold_days=max_hold_days,
            # Trailing Stop
            trailing_stop_enabled=trailing_stop_enabled,
            trailing_stop_callback_rate=trailing_stop_callback_rate,
            trailing_stop_activation_pct=trailing_stop_activation_pct,
            trailing_stop_use_instead_of_tp=trailing_stop_use_instead_of_tp,
        )

        # Position Manager (мониторинг SL/TP через WebSocket)
        self.position_manager = PositionManager(
            exchange=self.exchange,
            trade_engine=self.trade_engine,
        )
        # Связываем с TradeEngine
        self.trade_engine.position_manager = self.position_manager

        # Подключаем callbacks для Error Recovery
        self.trade_engine.on_alert = self._on_alert
        self.exchange.on_critical_error = self._on_critical_error
        self.exchange.on_ip_ban = self._on_ip_ban

        # Data downloader
        self.downloader = HybridHistoryDownloader(
            cache_dir='cache',
            data_interval='daily',
        )

        # Metrics Tracker (PnL, статистика)
        self.metrics = MetricsTracker()
        self.stats_interval = stats_interval

        # State Manager (сохранение/восстановление состояния)
        self.state_manager = StateManager(
            trade_engine=self.trade_engine,
            position_manager=self.position_manager,
            exchange=self.exchange,
            metrics_tracker=self.metrics,
        )

        # Telegram session
        self._telegram_session = None

        # State
        self._running = False
        self._cycle_count = 0
        self._shutdown_event: Optional[asyncio.Event] = None

    async def start(self):
        """Запустить торговое приложение."""
        logger.info("=" * 60)
        logger.info("TRADE APP STARTING")
        logger.info("=" * 60)
        logger.info(f"Mode:        {'TESTNET' if self.testnet else 'MAINNET'}")
        logger.info(f"Strategies:  {len(self.strategies)}: {', '.join(self.strategies)}")
        logger.info(f"Order Size:  ${self.order_size_usd}")
        logger.info(f"Leverage:    {self.leverage}x")
        logger.info(f"SL/TP:       {self.sl_pct}% / {self.tp_pct}%")
        logger.info(f"Max Hold:    {self.max_hold_days} days")
        logger.info(f"Interval:    {self.interval_sec}s")
        logger.info(f"Dedup Days:  {self.dedup_days}")
        logger.info(f"Position:    {self.position_mode}")
        logger.info(f"Coin Regime: {'ENABLED (' + str(self.coin_regime_lookback) + 'd)' if self.coin_regime_enabled else 'Disabled'}")
        vol_filters = []
        if self.vol_filter_low_enabled:
            vol_filters.append("LOW")
        if self.vol_filter_high_enabled:
            vol_filters.append("HIGH")
        logger.info(f"Vol Filter:  {' + '.join(vol_filters) if vol_filters else 'Disabled'}")
        # Month/Day filters
        month_filter_parts = []
        if self.month_off_dd is not None:
            month_filter_parts.append(f"DD>{self.month_off_dd}%")
        if self.month_off_pnl is not None:
            month_filter_parts.append(f"PnL<{self.month_off_pnl}%")
        logger.info(f"Month OFF:   {' '.join(month_filter_parts) if month_filter_parts else 'Disabled'}")
        day_filter_parts = []
        if self.day_off_dd is not None:
            day_filter_parts.append(f"DD>{self.day_off_dd}%")
        if self.day_off_pnl is not None:
            day_filter_parts.append(f"PnL<{self.day_off_pnl}%")
        logger.info(f"Day OFF:     {' '.join(day_filter_parts) if day_filter_parts else 'Disabled'}")
        if self.dynamic_size_enabled:
            logger.info(f"Dynamic Size: ENABLED (normal=${self.order_size_usd}, protected=${self.protected_size})")
        else:
            logger.info(f"Dynamic Size: Disabled")
        # ML Filter
        if self.use_ml:
            if ML_AVAILABLE:
                logger.info(f"ML Filter:   ENABLED (models: {self.ml_model_dir})")
            else:
                logger.warning(f"ML Filter:   REQUESTED but ml/filter.py not found!")
                self.use_ml = False
        else:
            logger.info(f"ML Filter:   Disabled")
        # Risk Management
        logger.info(f"Daily MaxDD: {self.daily_max_dd}%")
        logger.info(f"Month MaxDD: {self.monthly_max_dd}%")
        # Trailing Stop
        if self.trailing_stop_enabled:
            ts_info = f"ENABLED (callback={self.trailing_stop_callback_rate}%"
            if self.trailing_stop_activation_pct:
                ts_info += f", activation={self.trailing_stop_activation_pct}%"
            ts_info += f", {'replaces TP' if self.trailing_stop_use_instead_of_tp else 'with TP'})"
            logger.info(f"TrailStop:   {ts_info}")
        else:
            logger.info(f"TrailStop:   Disabled")
        # Late Signal Protection
        if self.late_signal_skip_after_utc is not None:
            logger.info(f"Late Signal: Skip after {self.late_signal_skip_after_utc}:00 UTC")
        else:
            logger.info(f"Late Signal: Disabled (execute any time)")
        logger.info(f"Telegram:    {'Configured' if self.telegram_bot_token else 'Not configured'}")
        logger.info("=" * 60)

        # Инициализируем ML фильтр если включен
        if self.use_ml and ML_AVAILABLE:
            try:
                self.ml_filter = MLSignalFilter(
                    model_dir=self.ml_model_dir,
                    per_strategy=True,
                    min_confidence=self.ml_min_confidence,
                    min_filter_score=self.ml_min_filter_score,
                )
                self.ml_filter.load()
                logger.info(f"ML Filter loaded from {self.ml_model_dir}")
            except Exception as e:
                logger.error(f"Failed to load ML filter: {e}")
                self.use_ml = False
                self.ml_filter = None

        # Создаём shutdown event
        self._shutdown_event = asyncio.Event()

        # Настраиваем signal handlers
        self._setup_signal_handlers()

        # Подключаемся к бирже
        connected = await self.exchange.connect()
        if not connected:
            logger.error("Failed to connect to exchange!")
            return

        # Получаем баланс
        balance = await self.exchange.get_balance("USDT")
        logger.info(f"Balance:     ${balance:.2f} USDT")

        # Восстанавливаем состояние и синхронизируем с биржей
        sync_stats = await self.state_manager.restore_and_sync()
        if sync_stats["positions_from_exchange"] > 0:
            logger.info(f"Restored {sync_stats['positions_from_exchange']} positions from exchange")

        # КРИТИЧНО: Сразу сохраняем состояние после sync
        # Это гарантирует что файл состояния актуален с первой секунды работы
        # Защита от crash до первого periodic save
        self.state_manager.save_state()
        logger.debug("Initial state saved after restore_and_sync")

        # Определяем символы
        if self.symbols:
            symbols = self.symbols
        else:
            logger.info(f"Fetching top {self.top_n} symbols by volume...")
            symbols = self.downloader.get_active_symbols(top_n=self.top_n)

        logger.info(f"Symbols:     {len(symbols)}: {', '.join(symbols[:5])}{'...' if len(symbols) > 5 else ''}")
        logger.info("=" * 60)

        # Запускаем Position Manager (WebSocket мониторинг SL/TP)
        self.position_manager.on_position_closed = self._on_position_closed
        self.position_manager.on_warning = self._on_position_warning
        pm_started = await self.position_manager.start()
        if pm_started:
            logger.info("Position Manager: STARTED (WebSocket monitoring)")
        else:
            logger.warning("Position Manager: FAILED TO START (will work without monitoring)")

        # Запускаем слушатель клавиатуры для Ctrl+M (resume trading)
        try:
            self._keyboard_listener_task = asyncio.create_task(self._keyboard_listener())
            logger.info("Keyboard listener: STARTED (Ctrl+M to resume after DD limit)")
        except Exception as e:
            logger.warning(f"Keyboard listener: FAILED ({e})")
            self._keyboard_listener_task = None

        # ВАЖНО: Устанавливаем _running = True ДО создания background tasks
        # Иначе они сразу выйдут из while self._running
        self._running = True

        # Запускаем периодическое сохранение состояния (защита от crash)
        self._state_save_task = asyncio.create_task(self._state_save_loop())
        logger.info(f"State save loop: STARTED (every {self._state_save_interval // 60} min)")

        # Отправляем стартовое сообщение в Telegram
        late_signal_info = f"Late Signal Skip: after {self.late_signal_skip_after_utc}:00 UTC" if self.late_signal_skip_after_utc is not None else "Late Signal Skip: OFF"
        await self._send_telegram(
            f"<b>TradeApp Started</b>\n"
            f"Mode: {'TESTNET' if self.testnet else 'MAINNET'}\n"
            f"Balance: ${balance:.2f}\n"
            f"Symbols: {len(symbols)}\n"
            f"Strategies: {len(self.strategies)}\n"
            f"Position Monitor: {'ON' if pm_started else 'OFF'}\n"
            f"Daily MaxDD: {self.daily_max_dd}%\n"
            f"Monthly MaxDD: {self.monthly_max_dd}%\n"
            f"{late_signal_info}"
        )

        # Отправляем уведомление о синхронизированных позициях (только при рестарте)
        if sync_stats["positions_from_exchange"] > 0:
            await self._send_sync_notification(sync_stats, balance)

        # Запускаем основной цикл
        await self._main_loop(symbols)

    async def stop(self):
        """Остановить торговое приложение."""
        logger.info("=" * 60)
        logger.info("GRACEFUL SHUTDOWN INITIATED")
        logger.info("=" * 60)

        self._running = False

        # Сохраняем состояние перед выходом
        logger.info("Saving state...")
        saved = self.state_manager.save_state()
        if saved:
            logger.info("State saved successfully")
        else:
            logger.warning("Failed to save state!")

        # Останавливаем keyboard listener
        if self._keyboard_listener_task:
            self._keyboard_listener_task.cancel()
            try:
                await self._keyboard_listener_task
            except asyncio.CancelledError:
                pass
            logger.info("Keyboard listener stopped")

        # Останавливаем периодическое сохранение состояния
        if self._state_save_task:
            self._state_save_task.cancel()
            try:
                await self._state_save_task
            except asyncio.CancelledError:
                pass
            logger.info("State save loop stopped")

        # Останавливаем Position Manager
        await self.position_manager.stop()

        # Отключаемся от биржи
        await self.exchange.disconnect()

        # Выводим dashboard в лог
        if self.metrics.total_stats.trades > 0:
            logger.info(self.metrics.format_dashboard())

        # Отправляем финальное сообщение с метриками
        stats = self.trade_engine.get_stats()
        pm_stats = self.position_manager.get_stats()
        metrics_summary = self.metrics.get_dashboard()

        await self._send_telegram(
            f"<b>TradeApp Stopped (Graceful Shutdown)</b>\n"
            f"\n"
            f"<b>Cycles:</b> {self._cycle_count}\n"
            f"<b>Signals Received:</b> {stats['signals_received']}\n"
            f"<b>Signals Executed:</b> {stats['signals_executed']}\n"
            f"<b>Open Positions:</b> {stats['open_positions']}\n"
            f"\n"
            f"<b>Closed by SL:</b> {pm_stats['positions_closed_sl']}\n"
            f"<b>Closed by TP:</b> {pm_stats['positions_closed_tp']}\n"
            f"<b>Closed by TIMEOUT:</b> {pm_stats['positions_closed_timeout']}\n"
            f"\n"
            f"📊 <b>METRICS</b>\n"
            f"<b>Total Trades:</b> {metrics_summary['total_trades']}\n"
            f"<b>Total PnL:</b> {metrics_summary['total_pnl']:+.2f} USDT\n"
            f"<b>Win Rate:</b> {metrics_summary['win_rate']:.1f}%\n"
            f"<b>Max Drawdown:</b> {metrics_summary['max_drawdown']:.2f} USDT\n"
            f"\n"
            f"<b>State Saved:</b> {'Yes' if saved else 'No'}"
        )

        if self._telegram_session:
            await self._telegram_session.close()

        logger.info("=" * 60)
        logger.info("TRADEAPP STOPPED")
        logger.info("=" * 60)

    def _on_position_closed(
        self,
        position: Position,
        exit_reason: str,
        realized_pnl: float,
    ) -> None:
        """
        Callback вызывается когда позиция закрылась по SL/TP.

        Args:
            position: Закрытая позиция
            exit_reason: "SL" или "TP"
            realized_pnl: Реализованный PnL в USDT
        """
        # Записываем в MetricsTracker
        self.metrics.record_trade(position, exit_reason, realized_pnl)

        # Обновляем флаг для dynamic sizing
        if self.dynamic_size_enabled:
            self._last_trade_was_win = (realized_pnl >= 0)
            logger.debug(f"Dynamic size: last_trade_was_win={self._last_trade_was_win}")

        # Обновляем daily/monthly PnL для risk management
        now = datetime.now(timezone.utc)
        current_month = now.month

        # Конвертируем PnL в проценты (от order_size_usd)
        pnl_pct = (realized_pnl / self.order_size_usd * 100) if self.order_size_usd > 0 else 0

        # Сбрасываем daily если новый день (auto-reset)
        self._check_daily_reset()

        # Monthly НЕ сбрасывается автоматически! Только через Ctrl+M
        if self._last_month != current_month:
            # Просто обновляем месяц, но НЕ сбрасываем флаг _monthly_stopped
            self._current_month_pnl = 0.0
            self._last_month = current_month

        # Накапливаем PnL
        self._current_day_pnl += pnl_pct
        self._current_month_pnl += pnl_pct

        # Проверяем DAILY лимит
        if not self._daily_stopped and self._current_day_pnl <= -self.daily_max_dd:
            self._daily_stopped = True
            logger.warning(f"DAILY MAX DD HIT: {self._current_day_pnl:.1f}% <= -{self.daily_max_dd}%")
            logger.warning("New orders STOPPED for today. Press Ctrl+M to resume or wait for new day.")
            # Отправляем Telegram алерт
            if not self._daily_alert_sent:
                self._daily_alert_sent = True
                import asyncio
                asyncio.create_task(self._send_risk_alert("DAILY", self._current_day_pnl, self.daily_max_dd))

        # Проверяем MONTHLY лимит
        if not self._monthly_stopped and self._current_month_pnl <= -self.monthly_max_dd:
            self._monthly_stopped = True
            logger.warning(f"MONTHLY MAX DD HIT: {self._current_month_pnl:.1f}% <= -{self.monthly_max_dd}%")
            logger.warning("New orders STOPPED. Press Ctrl+M to resume.")
            # Отправляем Telegram алерт
            if not self._monthly_alert_sent:
                self._monthly_alert_sent = True
                import asyncio
                asyncio.create_task(self._send_risk_alert("MONTHLY", self._current_month_pnl, self.monthly_max_dd))

        # Отправляем уведомление в Telegram (асинхронно)
        import asyncio
        asyncio.create_task(self._send_position_closed_alert(
            position, exit_reason, realized_pnl
        ))

    async def _send_position_closed_alert(
        self,
        position: Position,
        exit_reason: str,
        realized_pnl: float,
    ) -> None:
        """Отправить Telegram уведомление о закрытии позиции."""
        pnl_emoji = "🟢" if realized_pnl >= 0 else "🔴"

        # Emoji по причине закрытия
        if exit_reason == "SL":
            exit_emoji = "🛑"
        elif exit_reason == "TP":
            exit_emoji = "🎯"
        elif exit_reason == "TIMEOUT":
            exit_emoji = "⏰"
        elif exit_reason == "MISSING_TP":
            exit_emoji = "⚠️"
        elif exit_reason == "SYNC_FIX":
            exit_emoji = "🔄"
        else:
            exit_emoji = "📤"

        # Дополнительная инфа
        extra_info = ""
        if exit_reason == "TIMEOUT":
            extra_info = f"<b>Held:</b> {position.get_hold_days():.1f} / {position.max_hold_days} days\n"
        elif exit_reason == "MISSING_TP":
            extra_info = "<b>Reason:</b> TP order could not be placed, closed after 1 hour\n"
        elif exit_reason == "SYNC_FIX":
            extra_info = "<b>Reason:</b> Position closed on exchange, WebSocket event missed\n"

        message = (
            f"{exit_emoji} <b>POSITION CLOSED ({exit_reason})</b>\n"
            f"\n"
            f"<b>Symbol:</b> <code>{position.symbol}</code>\n"
            f"<b>Direction:</b> {position.side.value}\n"
            f"<b>Entry:</b> ${position.entry_price:.6f}\n"
            f"<b>Exit:</b> ${position.exit_price:.6f}\n"
            f"{extra_info}"
            f"\n"
            f"{pnl_emoji} <b>PnL:</b> {realized_pnl:+.2f} USDT\n"
            f"\n"
            f"<b>Strategy:</b> {position.strategy}\n"
            f"<b>Position ID:</b> <code>{position.position_id}</code>"
        )

        await self._send_telegram(message)

    async def _send_risk_alert(self, limit_type: str, current_pnl: float, threshold: float) -> None:
        """
        Отправить Telegram алерт о достижении лимита риска.

        Args:
            limit_type: "DAILY" или "MONTHLY"
            current_pnl: Текущий PnL в %
            threshold: Порог в %
        """
        if limit_type == "DAILY":
            emoji = "⚠️"
            resume_info = "Авто-сброс завтра или Ctrl+M для ручного продолжения"
        else:
            emoji = "🚨"
            resume_info = "Ctrl+M для ручного продолжения (авто-сброс ОТКЛЮЧЕН)"

        message = (
            f"{emoji}{emoji}{emoji} <b>{limit_type} MAX DRAWDOWN HIT</b> {emoji}{emoji}{emoji}\n"
            f"\n"
            f"<b>Current PnL:</b> {current_pnl:.1f}%\n"
            f"<b>Threshold:</b> -{threshold:.1f}%\n"
            f"\n"
            f"<b>Status:</b> NEW ORDERS STOPPED\n"
            f"<b>Positions:</b> Still monitoring (SL/TP active)\n"
            f"\n"
            f"<b>Resume:</b> {resume_info}"
        )

        await self._send_telegram(message)

    def _on_position_warning(
        self,
        level: str,
        message: str,
        position: Position,
        details: Dict[str, Any],
    ) -> None:
        """
        Callback для warnings от PositionManager.

        Args:
            level: WARNING, INFO
            message: Описание проблемы
            position: Позиция с проблемой
            details: Дополнительные данные
        """
        import asyncio

        # Формируем Telegram сообщение
        remaining_min = details.get("remaining_min", 0)
        elapsed_min = details.get("elapsed_min", 0)

        tg_message = (
            f"⚠️ <b>POSITION WARNING</b>\n"
            f"\n"
            f"<b>Symbol:</b> <code>{position.symbol}</code>\n"
            f"<b>Direction:</b> {position.side.value}\n"
            f"<b>Issue:</b> {message}\n"
            f"\n"
            f"<b>Elapsed:</b> {elapsed_min:.0f} min\n"
            f"<b>Will close in:</b> {remaining_min:.0f} min\n"
            f"\n"
            f"<b>Action:</b> Set TP order manually or position will be closed by MARKET\n"
            f"<b>Position ID:</b> <code>{position.position_id}</code>"
        )

        asyncio.create_task(self._send_telegram(tg_message))

    async def _keyboard_listener(self) -> None:
        """
        Слушатель клавиатуры для Ctrl+M (resume trading).

        Работает только когда окно консоли активно.
        """
        try:
            if sys.platform == "win32":
                import msvcrt
                while self._running:
                    if msvcrt.kbhit():
                        ch = msvcrt.getch()
                        if ch == b'\r' or ch == b'\x0d':  # Ctrl+M
                            self._handle_resume_hotkey()
                    await asyncio.sleep(0.1)
            else:
                # Unix
                import termios
                import tty
                import select
                old_settings = termios.tcgetattr(sys.stdin)
                try:
                    tty.setcbreak(sys.stdin.fileno())
                    while self._running:
                        if select.select([sys.stdin], [], [], 0.1)[0]:
                            ch = sys.stdin.read(1)
                            if ch == '\r' or ord(ch) == 13:
                                self._handle_resume_hotkey()
                        await asyncio.sleep(0.1)
                finally:
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        except Exception as e:
            logger.warning(f"Keyboard listener error: {e}")

    async def _state_save_loop(self) -> None:
        """
        Периодическое сохранение состояния (каждые 5 минут).

        Защита от потери данных при crash:
        - Если приложение упадёт без graceful shutdown
        - При рестарте restore_and_sync() найдёт актуальный файл
        - Позиции восстановятся с правильными strategy и signal_id
        """
        logger.info(f"State save loop started (interval: {self._state_save_interval}s)")

        while self._running:
            try:
                await asyncio.sleep(self._state_save_interval)

                if not self._running:
                    break

                # Сохраняем состояние
                saved = self.state_manager.save_state()
                if saved:
                    open_count = len(self.trade_engine.get_open_positions())
                    logger.debug(f"Periodic state save: {open_count} positions saved")
                else:
                    logger.warning("Periodic state save FAILED!")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"State save loop error: {e}")

        logger.info("State save loop stopped")

    def _handle_resume_hotkey(self) -> None:
        """Обработчик Ctrl+M - возобновление торговли."""
        resumed = False

        if self._daily_stopped:
            self._daily_stopped = False
            self._daily_alert_sent = False
            logger.info("DAILY LIMIT RESUMED via Ctrl+M")
            resumed = True

        if self._monthly_stopped:
            self._monthly_stopped = False
            self._monthly_alert_sent = False
            logger.info("MONTHLY LIMIT RESUMED via Ctrl+M")
            resumed = True

        if resumed:
            # Отправляем Telegram уведомление
            import asyncio
            asyncio.create_task(self._send_telegram(
                "✅ <b>TRADING RESUMED</b>\n\n"
                "Risk limits manually overridden via Ctrl+M.\n"
                "New orders will be placed."
            ))
        else:
            logger.debug("Ctrl+M pressed but no limits were active")

    def _check_daily_reset(self) -> None:
        """
        Проверить и сбросить daily лимит при смене дня.

        Вызывается из _on_position_closed и _run_cycle для единообразия.
        Daily лимит автоматически сбрасывается при смене дня.
        """
        now = datetime.now(timezone.utc)
        current_day = now.day

        if self._last_day != current_day:
            if self._daily_stopped:
                logger.info(f"NEW DAY: Daily limit auto-reset (was stopped at {self._current_day_pnl:.1f}%)")
            self._current_day_pnl = 0.0
            self._daily_stopped = False
            self._daily_alert_sent = False
            self._last_day = current_day

    def _on_alert(
        self,
        level: str,
        message: str,
        details: Dict[str, Any],
    ) -> None:
        """
        Callback для алертов от TradeEngine.

        Args:
            level: INFO, WARNING, ERROR, CRITICAL
            message: Сообщение
            details: Детали
        """
        import asyncio

        # Формируем Telegram сообщение
        emoji_map = {
            "INFO": "ℹ️",
            "WARNING": "⚠️",
            "ERROR": "❌",
            "CRITICAL": "🚨",
        }
        emoji = emoji_map.get(level, "📢")

        details_str = "\n".join(f"<b>{k}:</b> {v}" for k, v in details.items())

        tg_message = (
            f"{emoji} <b>ALERT [{level}]</b>\n"
            f"\n"
            f"{message}\n"
            f"\n"
            f"{details_str}"
        )

        asyncio.create_task(self._send_telegram(tg_message))

    def _on_critical_error(self, error) -> None:
        """
        Callback для критических ошибок (Auth, Liquidation).

        Останавливает бота.
        """
        import asyncio

        logger.critical(f"CRITICAL ERROR CALLBACK: [{error.code}] {error.message}")

        # Отправляем CRITICAL alert
        tg_message = (
            f"🚨🚨🚨 <b>CRITICAL ERROR - BOT STOPPING</b> 🚨🚨🚨\n"
            f"\n"
            f"<b>Error Code:</b> {error.code}\n"
            f"<b>Message:</b> {error.message}\n"
            f"<b>Category:</b> {error.category.value}\n"
            f"\n"
            f"<b>ACTION REQUIRED:</b> Check bot status immediately!"
        )

        asyncio.create_task(self._send_telegram(tg_message))

        # Останавливаем бота
        self._running = False

    def _on_ip_ban(self, retry_after: int) -> None:
        """
        Callback для IP бана.

        Args:
            retry_after: Время ожидания в секундах
        """
        import asyncio

        logger.warning(f"IP BAN CALLBACK: retry after {retry_after}s")

        tg_message = (
            f"⛔ <b>IP BANNED</b>\n"
            f"\n"
            f"<b>Retry after:</b> {retry_after // 60} minutes\n"
            f"\n"
            f"Bot will automatically retry after ban expires."
        )

        asyncio.create_task(self._send_telegram(tg_message))

    def _setup_signal_handlers(self) -> None:
        """
        Настроить обработчики сигналов для graceful shutdown.

        Поддерживаемые сигналы:
        - SIGINT (Ctrl+C)
        - SIGTERM (системный terminate)
        """
        loop = asyncio.get_running_loop()

        def _signal_handler(sig_name: str):
            """Обработчик сигнала."""
            logger.info(f"Received signal {sig_name}")
            if self._shutdown_event and not self._shutdown_event.is_set():
                self._shutdown_event.set()

        # На Windows signal.SIGTERM не работает через add_signal_handler
        # Используем try/except для кроссплатформенности
        if sys.platform != "win32":
            # Unix: используем add_signal_handler
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(
                    sig, lambda s=sig.name: _signal_handler(s)
                )
            logger.info("Signal handlers registered: SIGINT, SIGTERM")
        else:
            # Windows: SIGINT обрабатывается через KeyboardInterrupt
            # Для SIGTERM нужен другой подход - оставляем только SIGINT
            logger.info("Windows mode: SIGINT handled via KeyboardInterrupt")

    async def _main_loop(self, symbols: List[str]):
        """Основной торговый цикл."""
        while self._running:
            try:
                # Проверяем shutdown event
                if self._shutdown_event and self._shutdown_event.is_set():
                    logger.info("Shutdown event detected")
                    break

                self._cycle_count += 1
                logger.info(f"")
                logger.info(f"{'=' * 40}")
                logger.info(f"CYCLE {self._cycle_count}")
                logger.info(f"{'=' * 40}")

                await self._run_cycle(symbols)

                # Периодический вывод статистики
                if self.stats_interval > 0 and self._cycle_count % self.stats_interval == 0:
                    if self.metrics.total_stats.trades > 0:
                        logger.info(self.metrics.format_dashboard())

                # Ждём следующий цикл или shutdown event
                logger.info(f"Sleeping {self.interval_sec}s until next cycle...")
                try:
                    if self._shutdown_event:
                        # Ждём либо таймаут, либо shutdown event
                        await asyncio.wait_for(
                            self._shutdown_event.wait(),
                            timeout=self.interval_sec
                        )
                        # Если дождались event - выходим
                        logger.info("Shutdown event received during sleep")
                        break
                    else:
                        await asyncio.sleep(self.interval_sec)
                except asyncio.TimeoutError:
                    # Таймаут истёк, продолжаем цикл
                    pass

            except asyncio.CancelledError:
                logger.info("Main loop cancelled")
                break
            except Exception as e:
                logger.exception(f"Error in main loop: {e}")
                await self._send_telegram(f"<b>ERROR</b>\n{str(e)[:200]}")
                await asyncio.sleep(60)  # Пауза при ошибке

    async def _run_cycle(self, symbols: List[str]):
        """Выполнить один торговый цикл."""
        # 1. Определяем временной диапазон (последние 30 дней для генерации сигналов)
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=30)

        # 2. Скачиваем данные
        logger.info("[1/4] Downloading data...")
        history = self.downloader.download_with_coinalyze_backfill(
            symbols, start, end
        )

        # 3. Генерируем сигналы для каждой стратегии
        logger.info("[2/4] Generating signals...")
        all_signals = []

        config = StrategyConfig(
            sl_pct=self.sl_pct,
            tp_pct=self.tp_pct,
            max_hold_days=self.max_hold_days,
            lookback=7,
        )

        for strat_name in self.strategies:
            try:
                runner = StrategyRunner(
                    strategy_name=strat_name,
                    config=config,
                    output_dir='output',
                )

                # Генерируем сигналы
                signals = runner.generate_signals(history, symbols, dedup_days=self.dedup_days)

                # Фильтруем только сегодняшние сигналы
                today = datetime.now(timezone.utc).date()
                today_signals = [
                    s for s in signals
                    if s.date.date() == today
                ]

                if today_signals:
                    logger.info(f"  {strat_name}: {len(today_signals)} signals today")
                    all_signals.extend(today_signals)

            except Exception as e:
                logger.error(f"Strategy {strat_name} error: {e}")

        logger.info(f"Total signals today: {len(all_signals)}")

        if not all_signals:
            logger.info("No signals to execute")
            return

        # 4. Исполняем сигналы с фильтрацией
        logger.info("[3/4] Executing signals...")

        # Проверяем risk management лимиты
        self._check_daily_reset()

        if self._monthly_stopped:
            logger.warning(f"MONTHLY STOPPED: PnL={self._current_month_pnl:.1f}% <= -{self.monthly_max_dd}%. "
                          f"Press Ctrl+M to resume. Skipping all signals.")
            return

        if self._daily_stopped:
            logger.warning(f"DAILY STOPPED: PnL={self._current_day_pnl:.1f}% <= -{self.daily_max_dd}%. "
                          f"Press Ctrl+M or wait for new day. Skipping signals.")
            return

        # Статистика фильтрации
        skipped_late_signal = 0
        skipped_duplicate = 0  # Дедупликация по signal_id
        skipped_regime = 0
        skipped_vol_low = 0
        skipped_vol_high = 0
        skipped_position = 0
        skipped_month_filter = 0
        skipped_day_filter = 0
        skipped_ml = 0
        regime_dynamic = 0
        executed = 0

        for signal in all_signals:
            try:
                strategy_name = signal.metadata.get('strategy', 'unknown')

                # === LATE SIGNAL CHECK (skip signals for today if past threshold hour) ===
                if self.late_signal_skip_after_utc is not None:
                    now_utc = datetime.utcnow()
                    # Signal date = day the candle closed (00:00 UTC)
                    # If it's past threshold hour and signal is for today → stale
                    if signal.date.date() == now_utc.date() and now_utc.hour >= self.late_signal_skip_after_utc:
                        logger.debug(
                            f"SKIP {signal.symbol}: late signal "
                            f"(signal={signal.date.date()}, now={now_utc.hour}:{now_utc.minute:02d} UTC >= {self.late_signal_skip_after_utc}:00 UTC)"
                        )
                        skipped_late_signal += 1
                        continue

                # === SIGNAL_ID DEDUPLICATION (защита от повторного исполнения) ===
                # КРИТИЧНО: Проверяем ВСЕ позиции (открытые + закрытые)!
                # Если позиция закрылась по SL/TP, тот же сигнал НЕ должен
                # исполняться повторно в тот же день.
                executed_signal_ids = self.trade_engine.get_executed_signal_ids()
                if signal.signal_id in executed_signal_ids:
                    logger.debug(
                        f"SKIP {signal.symbol}: signal_id={signal.signal_id} already executed"
                    )
                    skipped_duplicate += 1
                    continue

                # === MONTH FILTER (lookup from MONTH_DATA) ===
                if self.month_off_dd is not None or self.month_off_pnl is not None:
                    signal_month = signal.date.month  # 1-12
                    if strategy_name in MONTH_DATA and signal_month in MONTH_DATA[strategy_name]:
                        m_pnl, m_dd = MONTH_DATA[strategy_name][signal_month]
                        skip_month = False
                        if self.month_off_dd is not None and m_dd < -self.month_off_dd:
                            skip_month = True
                        if self.month_off_pnl is not None and m_pnl < self.month_off_pnl:
                            skip_month = True
                        if skip_month:
                            logger.debug(f"SKIP {signal.symbol}: month={signal_month}, {strategy_name} stats: pnl={m_pnl}%, dd={m_dd}%")
                            skipped_month_filter += 1
                            continue

                # === DAY FILTER (lookup from DAY_DATA) ===
                if self.day_off_dd is not None or self.day_off_pnl is not None:
                    signal_day = signal.date.weekday()  # 0=Mon..6=Sun
                    if strategy_name in DAY_DATA and signal_day in DAY_DATA[strategy_name]:
                        d_pnl, d_dd = DAY_DATA[strategy_name][signal_day]
                        skip_day = False
                        if self.day_off_dd is not None and d_dd < -self.day_off_dd:
                            skip_day = True
                        if self.day_off_pnl is not None and d_pnl < self.day_off_pnl:
                            skip_day = True
                        if skip_day:
                            logger.debug(f"SKIP {signal.symbol}: day={signal_day}, {strategy_name} stats: pnl={d_pnl}%, dd={d_dd}%")
                            skipped_day_filter += 1
                            continue

                # === POSITION MODE CHECK ===
                # ВАЖНО: Фильтруем по symbol + strategy, как в бэктестере
                # Каждая стратегия может иметь свою позицию на одном символе
                if self.position_mode != "multi":
                    open_positions = self.trade_engine.get_open_positions()
                    # Фильтруем по символу И стратегии (как в backtester - каждая стратегия независима)
                    symbol_positions = [
                        p for p in open_positions
                        if p.symbol == signal.symbol and p.strategy == strategy_name
                    ]

                    if self.position_mode == "single":
                        # single: только 1 позиция на монету
                        if symbol_positions:
                            logger.debug(f"SKIP {signal.symbol}: position_mode=single, already has position")
                            skipped_position += 1
                            continue
                    elif self.position_mode == "direction":
                        # direction: 1 LONG + 1 SHORT на монету
                        direction_positions = [
                            p for p in symbol_positions
                            if (signal.direction == "LONG" and p.side.value == "LONG") or
                               (signal.direction == "SHORT" and p.side.value == "SHORT")
                        ]
                        if direction_positions:
                            logger.debug(f"SKIP {signal.symbol}: position_mode=direction, already has {signal.direction}")
                            skipped_position += 1
                            continue

                # === VOL FILTER ===
                # Конвертируем klines в DailyCandle для calculate_volatility
                symbol_history = history.get(signal.symbol)
                if symbol_history and symbol_history.klines:
                    # Aggregate to daily candles
                    daily_candles = StrategyRunner.aggregate_to_daily(symbol_history.klines)

                    if daily_candles and len(daily_candles) >= 15:
                        coin_vol = calculate_volatility(
                            daily_candles, signal.date, lookback=14
                        )

                        # Получаем пороги для стратегии
                        vol_thresholds = VOL_FILTER_THRESHOLDS.get(strategy_name, {})
                        vol_low = vol_thresholds.get('vol_low')
                        vol_high = vol_thresholds.get('vol_high')

                        # VOL LOW filter
                        if self.vol_filter_low_enabled and vol_low is not None:
                            if coin_vol < vol_low:
                                logger.debug(f"SKIP {signal.symbol}: vol {coin_vol:.1f}% < {vol_low}% (too quiet)")
                                skipped_vol_low += 1
                                continue

                        # VOL HIGH filter
                        if self.vol_filter_high_enabled and vol_high is not None:
                            if coin_vol > vol_high:
                                logger.debug(f"SKIP {signal.symbol}: vol {coin_vol:.1f}% > {vol_high}% (too chaotic)")
                                skipped_vol_high += 1
                                continue

                # === ML FILTER ===
                if self.use_ml and self.ml_filter is not None:
                    try:
                        # Build features for ML - simplified for LIVE
                        # ML filter in strategy_runner uses previous day's candle
                        symbol_history = history.get(signal.symbol)
                        if symbol_history and symbol_history.klines:
                            daily_candles = StrategyRunner.aggregate_to_daily(symbol_history.klines)
                            if daily_candles and len(daily_candles) >= 2:
                                # Get previous day's candle (for HONEST ML - no look-ahead)
                                prev_candle = daily_candles[-2] if len(daily_candles) >= 2 else None
                                candle = daily_candles[-1]

                                # Build minimal features
                                features = {
                                    'Open': candle.open,
                                    'Prev High': prev_candle.high if prev_candle else 0,
                                    'Prev Low': prev_candle.low if prev_candle else 0,
                                    'Prev Close': prev_candle.close if prev_candle else 0,
                                    'Prev Volume': prev_candle.volume if prev_candle else 0,
                                    'SL %': self.sl_pct,
                                    'TP %': self.tp_pct,
                                    'R:R Ratio': self.tp_pct / self.sl_pct if self.sl_pct > 0 else 0,
                                }

                                prediction = self.ml_filter.predict(
                                    features,
                                    strategy=strategy_name,
                                    symbol=signal.symbol,
                                    direction=signal.direction,
                                )

                                if not prediction.should_trade:
                                    logger.debug(f"SKIP {signal.symbol}: ML filtered (conf={prediction.confidence:.2f}, score={prediction.filter_score:.2f})")
                                    skipped_ml += 1
                                    continue
                    except Exception as e:
                        logger.warning(f"ML filter error for {signal.symbol}: {e}")

                # === COIN REGIME FILTER ===
                regime_action = "FULL"

                if self.coin_regime_enabled:
                    symbol_history = history.get(signal.symbol)
                    if symbol_history and symbol_history.klines:
                        daily_candles = StrategyRunner.aggregate_to_daily(symbol_history.klines)

                        if daily_candles and len(daily_candles) >= self.coin_regime_lookback:
                            coin_regime = calculate_coin_regime(
                                daily_candles, signal.date, lookback=self.coin_regime_lookback
                            )

                            # Получаем action из COIN_REGIME_MATRIX
                            regime_actions = COIN_REGIME_MATRIX.get(coin_regime, {})
                            regime_action = regime_actions.get(strategy_name, "FULL")

                            if regime_action == "OFF":
                                logger.debug(f"SKIP {signal.symbol}: regime={coin_regime}, strategy={strategy_name} -> OFF")
                                skipped_regime += 1
                                continue
                            elif regime_action == "DYN":
                                regime_dynamic += 1
                                logger.debug(f"DYN SIZE {signal.symbol}: regime={coin_regime}, strategy={strategy_name}")

                # === DYNAMIC SIZING ===
                if self.dynamic_size_enabled:
                    if self._last_trade_was_win:
                        order_size = self.order_size_usd  # Normal = order_size_usd
                    else:
                        order_size = self.protected_size
                        logger.info(f"Using protected size ${order_size} after loss")
                else:
                    order_size = self.order_size_usd

                # Если regime_action = DYN, используем protected_size
                if regime_action == "DYN":
                    order_size = self.protected_size if self.dynamic_size_enabled else 1.0

                # === EXECUTE SIGNAL ===
                position = await self.trade_engine.execute_signal(
                    signal=signal,
                    order_size_usd=order_size,
                    regime_action=regime_action,
                )

                if position:
                    executed += 1
                    # Отправляем alert в Telegram
                    await self._send_signal_alert(signal, position, order_size)
                    # КРИТИЧНО: Сохраняем состояние сразу после открытия позиции
                    # Защита от crash - signal_id и strategy будут сохранены
                    self.state_manager.save_state()

            except Exception as e:
                logger.error(f"Failed to execute signal {signal.signal_id}: {e}")

        # Логируем статистику фильтрации
        total_skipped = (skipped_late_signal + skipped_duplicate + skipped_regime + skipped_vol_low +
                        skipped_vol_high + skipped_position + skipped_month_filter + skipped_day_filter + skipped_ml)
        if total_skipped > 0:
            logger.info(f"Filter stats: executed={executed}, "
                       f"skipped_late={skipped_late_signal}, skipped_dup={skipped_duplicate}, "
                       f"skipped_month={skipped_month_filter}, skipped_day={skipped_day_filter}, "
                       f"skipped_ml={skipped_ml}, skipped_regime={skipped_regime}, "
                       f"skipped_vol_low={skipped_vol_low}, skipped_vol_high={skipped_vol_high}, "
                       f"skipped_position={skipped_position}, regime_dynamic={regime_dynamic}")

        # 5. Статистика
        logger.info("[4/4] Cycle complete")
        stats = self.trade_engine.get_stats()
        logger.info(f"Stats: {stats}")

    async def _send_signal_alert(self, signal, position: Position, order_size: float):
        """Отправить alert о сигнале в Telegram."""
        direction_emoji = "" if signal.direction == "LONG" else ""

        # Добавляем инфу о режиме если DYN
        size_info = f"${order_size:.0f}"
        if position.regime_action == "DYN":
            size_info += " (DYN)"

        message = (
            f"{direction_emoji} <b>TRADE EXECUTED</b>\n"
            f"\n"
            f"<b>Symbol:</b> <code>{signal.symbol}</code>\n"
            f"<b>Direction:</b> {signal.direction}\n"
            f"<b>Entry:</b> ${position.entry_price:.6f}\n"
            f"<b>SL:</b> ${position.stop_loss:.6f}\n"
            f"<b>TP:</b> ${position.take_profit:.6f}\n"
            f"<b>Size:</b> {size_info}\n"
            f"\n"
            f"<b>Strategy:</b> {signal.metadata.get('strategy', 'unknown')}\n"
            f"<b>Position ID:</b> <code>{position.position_id}</code>\n"
            f"\n"
            f"<a href='https://www.binance.com/ru/futures/{signal.symbol}'>Open Chart</a>"
        )

        await self._send_telegram(message)

    async def _send_sync_notification(self, sync_stats: dict, balance: float):
        """
        Отправить уведомление о синхронизированных позициях при рестарте.

        Вызывается ТОЛЬКО один раз при старте приложения, если были найдены
        позиции на бирже.

        Показывает:
        - OUR SIGNAL: позиция открыта ботом (есть signal_id и strategy)
        - EXTERNAL: позиция найдена на бирже но не распознана как наша
        """
        positions = self.trade_engine.get_open_positions()
        if not positions:
            return

        # Считаем наши vs внешние
        our_count = sum(1 for p in positions if p.strategy and p.strategy != "SYNCED")
        external_count = len(positions) - our_count

        # Формируем список позиций
        pos_lines = []
        for i, pos in enumerate(positions, 1):
            direction_emoji = "🟢" if pos.side.value == "LONG" else "🔴"

            # Определяем тип позиции
            is_our_signal = pos.strategy and pos.strategy != "SYNCED"

            if is_our_signal:
                type_line = "   ✅ <b>OUR SIGNAL</b>"
                strategy_line = f"   Strategy: {pos.strategy}"
                signal_line = f"   Signal ID: <code>{pos.signal_id}</code>" if pos.signal_id else "   Signal ID: -"
                opened_line = f"   Opened: {pos.opened_at.strftime('%Y-%m-%d %H:%M:%S')}" if pos.opened_at else "   Opened: -"
            else:
                type_line = "   ⚠️ <b>EXTERNAL</b>"
                strategy_line = "   Strategy: Unknown"
                signal_line = "   Signal ID: -"
                opened_line = "   Opened: Unknown"

            # Статус ордеров
            sl_status = "✓" if pos.sl_order_id else "✗"
            tp_status = "✓" if pos.tp_order_id else "✗"
            trailing_status = "✓" if pos.trailing_stop_order_id else "✗"

            pos_lines.append(
                f"{i}️⃣ <b>{pos.symbol}</b> {direction_emoji} {pos.side.value}\n"
                f"{type_line}\n"
                f"{strategy_line}\n"
                f"{signal_line}\n"
                f"{opened_line}\n"
                f"   Entry: ${pos.entry_price:.4f}\n"
                f"   Qty: {pos.quantity}\n"
                f"   SL: ${pos.stop_loss:.4f} {sl_status}\n"
                f"   TP: ${pos.take_profit:.4f} {tp_status}\n"
                f"   Trailing: {trailing_status}"
            )

        positions_text = "\n\n".join(pos_lines)

        # Итоговое сообщение
        summary = f"✅ Our: {our_count} | ⚠️ External: {external_count}" if external_count > 0 else f"✅ All {our_count} positions recognized"

        message = (
            f"🔄 <b>BOT RESTARTED</b>\n"
            f"\n"
            f"Synced {len(positions)} position(s) from exchange:\n"
            f"{summary}\n"
            f"\n"
            f"{positions_text}\n"
            f"\n"
            f"<b>Balance:</b> ${balance:.2f} USDT\n"
            f"<b>SL orders:</b> {sync_stats['sl_orders_found']} found, {sync_stats['sl_orders_created']} created\n"
            f"<b>TP orders:</b> {sync_stats['tp_orders_found']} found, {sync_stats['tp_orders_created']} created\n"
            f"<b>Trailing:</b> {sync_stats.get('trailing_orders_found', 0)} found"
        )

        await self._send_telegram(message)
        logger.info(f"Sync notification sent to Telegram: {len(positions)} positions (our: {our_count}, external: {external_count})")

    async def _send_telegram(self, message: str):
        """Отправить сообщение в Telegram."""
        if not self.telegram_bot_token or not self.telegram_chat_id:
            return

        try:
            import aiohttp

            if self._telegram_session is None:
                self._telegram_session = aiohttp.ClientSession()

            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            payload = {
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }

            async with self._telegram_session.post(url, json=payload) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    logger.warning(f"Telegram send failed: {resp.status} {error[:100]}")

        except Exception as e:
            logger.warning(f"Telegram error: {e}")


def main():
    """Главная функция."""
    parser = argparse.ArgumentParser(
        description="Trade App - LIVE Trading",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # API Keys
    parser.add_argument("--api-key", type=str,
                        default=os.environ.get("BINANCE_API_KEY", ""),
                        help="Binance API key (or set BINANCE_API_KEY env)")
    parser.add_argument("--api-secret", type=str,
                        default=os.environ.get("BINANCE_API_SECRET", ""),
                        help="Binance API secret (or set BINANCE_API_SECRET env)")

    # Mode
    parser.add_argument("--testnet", action="store_true", default=True,
                        help="Use Binance testnet (default: True)")
    parser.add_argument("--mainnet", action="store_true",
                        help="Use Binance mainnet (REAL MONEY!)")

    # Symbols
    parser.add_argument("--symbols", type=str, default="",
                        help="Comma-separated symbols (e.g., BTCUSDT,ETHUSDT)")
    parser.add_argument("--top", type=int, default=10,
                        help="Top N symbols by volume (if --symbols not provided)")

    # Strategies
    parser.add_argument("--strategies", type=str, default="",
                        help="Comma-separated strategies (default: all)")

    # Trading params
    # !!! НЕ МЕНЯТЬ БЕЗ ЯВНОГО УКАЗАНИЯ ПОЛЬЗОВАТЕЛЯ !!!
    parser.add_argument("--order-size", type=float, default=100.0,
                        help="Order size in USD (default: 100)")
    parser.add_argument("--leverage", type=int, default=10,
                        help="Leverage (default: 10)")
    parser.add_argument("--sl", type=float, default=4.0,
                        help="Stop Loss %% (default: 4)")
    parser.add_argument("--tp", type=float, default=10.0,
                        help="Take Profit %% (default: 10)")
    parser.add_argument("--max-hold", type=int, default=14,
                        help="Max hold days (default: 14)")

    # Timing
    parser.add_argument("--interval", type=int, default=300,
                        help="Cycle interval in seconds (default: 300)")

    # Telegram
    parser.add_argument("--telegram-token", type=str,
                        default=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                        help="Telegram bot token")
    parser.add_argument("--telegram-chat", type=str,
                        default=os.environ.get("TELEGRAM_CHAT_ID", ""),
                        help="Telegram chat ID")

    # Filters (SAME AS BACKTESTER run_all.py)
    parser.add_argument("--coin-regime", action="store_true",
                        help="Enable COIN REGIME filter (uses COIN_REGIME_MATRIX)")
    parser.add_argument("--coin-regime-lookback", type=int, default=14,
                        help="Lookback days for coin regime (default: 14)")
    parser.add_argument("--vol-filter-low", action="store_true",
                        help="Enable LOW volatility filter (skip if vol < threshold)")
    parser.add_argument("--vol-filter-high", action="store_true",
                        help="Enable HIGH volatility filter (skip if vol > threshold)")
    parser.add_argument("--dedup-days", type=int, default=3,
                        help="Signal deduplication days (default: 3)")
    parser.add_argument("--position-mode", type=str, default="single",
                        choices=["single", "direction", "multi"],
                        help="Position mode: single (1 per coin), direction (1 per direction), multi (default: single)")

    # Dynamic Sizing
    parser.add_argument("--dynamic-size", action="store_true",
                        help="Enable dynamic sizing (protected after loss)")
    parser.add_argument("--protected-size", type=float, default=100.0,
                        help="Order size after LOSS (default: 100, after WIN = --order-size)")

    # Month/Day Filters (uses MONTH_DATA/DAY_DATA from strategy_runner.py)
    parser.add_argument("--month-off-dd", type=float, default=None,
                        help="Skip months where MaxDD > X%% (e.g., 50)")
    parser.add_argument("--month-off-pnl", type=float, default=None,
                        help="Skip months where PnL < X%% (e.g., -20)")
    parser.add_argument("--day-off-dd", type=float, default=None,
                        help="Skip days where MaxDD > X%% (e.g., 40)")
    parser.add_argument("--day-off-pnl", type=float, default=None,
                        help="Skip days where PnL < X%% (e.g., -10)")

    # ML Filter
    parser.add_argument("--ml", action="store_true",
                        help="Enable ML filtering of signals (requires ml/filter.py)")
    parser.add_argument("--ml-model-dir", type=str, default="models",
                        help="Directory with ML models (default: models)")

    # Risk Management
    parser.add_argument("--daily-max-dd", type=float, default=5.0,
                        help="Daily max drawdown %% - stop new trades for day if hit (default: 5)")
    parser.add_argument("--monthly-max-dd", type=float, default=20.0,
                        help="Monthly max drawdown %% - stop all trading if hit (default: 20)")

    # Logging
    parser.add_argument("--log-file", type=str, default=None,
                        help="Path to log file (default: None = console only). Example: logs/tradebot.log")
    parser.add_argument("--log-level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Log level (default: INFO)")
    parser.add_argument("--log-max-mb", type=int, default=10,
                        help="Max log file size in MB before rotation (default: 10)")
    parser.add_argument("--log-backup-count", type=int, default=5,
                        help="Number of backup log files to keep (default: 5)")

    # Metrics
    parser.add_argument("--stats-interval", type=int, default=0,
                        help="Print stats dashboard every N cycles (0 = only on shutdown)")

    # Trailing Stop
    parser.add_argument("--trailing-stop", action="store_true",
                        help="Enable trailing stop (uses config/trailing_stop.json or CLI params)")
    parser.add_argument("--trailing-callback", type=float, default=None,
                        help="Trailing stop callback rate %% (0.1-5.0, default from config or 1.0)")
    parser.add_argument("--trailing-activation", type=float, default=None,
                        help="Trailing stop activation at X%% profit (default: immediate)")
    parser.add_argument("--trailing-with-tp", action="store_true",
                        help="Use trailing stop WITH fixed TP (default: replaces TP)")

    # === LATE SIGNAL PROTECTION ===
    parser.add_argument("--late-signal-skip-after", type=int, default=3,
                        help="Skip signals for today if current hour UTC > X (default: 3, -1 to disable)")

    args = parser.parse_args()

    # Setup logging FIRST (before any logger calls)
    setup_logging(
        log_file=args.log_file,
        log_level=args.log_level,
        max_bytes=args.log_max_mb * 1024 * 1024,
        backup_count=args.log_backup_count,
    )

    # Determine if using mainnet or testnet
    use_testnet = not args.mainnet

    # Load API keys: CLI > ENV > config file
    api_key = args.api_key
    api_secret = args.api_secret

    if not api_key or not api_secret:
        # Try loading from config file
        config_key, config_secret = load_binance_api_config(testnet=use_testnet)
        if config_key and config_secret:
            api_key = config_key
            api_secret = config_secret
            print(f"Loaded API keys from config/binance_api.json ({'testnet' if use_testnet else 'mainnet'})")

    # Validate
    if not api_key or not api_secret:
        print("ERROR: API key and secret required!")
        print("Options:")
        print("  1. Edit config/binance_api.json with your keys")
        print("  2. Set BINANCE_API_KEY and BINANCE_API_SECRET environment variables")
        print("  3. Use --api-key and --api-secret arguments")
        return 1

    # Load trailing stop config: CLI > config file > defaults
    ts_config = load_trailing_stop_config()

    # CLI overrides config file
    trailing_stop_enabled = args.trailing_stop or ts_config["enabled"]
    trailing_stop_callback_rate = args.trailing_callback if args.trailing_callback is not None else ts_config["callback_rate"]
    trailing_stop_activation_pct = args.trailing_activation if args.trailing_activation is not None else ts_config["activation_price_pct"]
    trailing_stop_use_instead_of_tp = not args.trailing_with_tp and ts_config["use_instead_of_tp"]

    if trailing_stop_enabled:
        print(f"Trailing Stop: ENABLED (callback={trailing_stop_callback_rate}%, "
              f"{'with TP' if not trailing_stop_use_instead_of_tp else 'replaces TP'})")

    # Load telegram config: CLI > ENV > config file
    telegram_token = args.telegram_token
    telegram_chat = args.telegram_chat

    if not telegram_token or not telegram_chat:
        config_token, config_chat = load_telegram_config()
        if config_token and config_chat:
            telegram_token = config_token
            telegram_chat = config_chat
            print(f"Loaded Telegram credentials from config/telegram.json")

    # Parse symbols
    symbols = None
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    # Parse strategies
    strategies = None
    if args.strategies:
        strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]

    # Create app
    app = TradeApp(
        api_key=api_key,
        api_secret=api_secret,
        testnet=use_testnet,
        symbols=symbols,
        top_n=args.top,
        strategies=strategies,
        interval_sec=args.interval,
        order_size_usd=args.order_size,
        leverage=args.leverage,
        sl_pct=args.sl,
        tp_pct=args.tp,
        max_hold_days=args.max_hold,
        telegram_bot_token=telegram_token,
        telegram_chat_id=telegram_chat,
        # Filters
        coin_regime_enabled=args.coin_regime,
        coin_regime_lookback=args.coin_regime_lookback,
        vol_filter_low_enabled=args.vol_filter_low,
        vol_filter_high_enabled=args.vol_filter_high,
        dedup_days=args.dedup_days,
        position_mode=args.position_mode,
        # Dynamic Sizing
        dynamic_size_enabled=args.dynamic_size,
        protected_size=args.protected_size,
        # Month/Day Filters
        month_off_dd=args.month_off_dd,
        month_off_pnl=args.month_off_pnl,
        day_off_dd=args.day_off_dd,
        day_off_pnl=args.day_off_pnl,
        # ML Filter
        use_ml=args.ml,
        ml_model_dir=args.ml_model_dir,
        # Risk Management
        daily_max_dd=args.daily_max_dd,
        monthly_max_dd=args.monthly_max_dd,
        # Metrics
        stats_interval=args.stats_interval,
        # Trailing Stop
        trailing_stop_enabled=trailing_stop_enabled,
        trailing_stop_callback_rate=trailing_stop_callback_rate,
        trailing_stop_activation_pct=trailing_stop_activation_pct,
        trailing_stop_use_instead_of_tp=trailing_stop_use_instead_of_tp,
        # Late Signal Protection
        late_signal_skip_after_utc=args.late_signal_skip_after if args.late_signal_skip_after >= 0 else None,
    )

    # Run with proper shutdown handling
    async def run_with_shutdown():
        """Запуск с graceful shutdown."""
        try:
            await app.start()
        except asyncio.CancelledError:
            logger.info("App cancelled")
        finally:
            await app.stop()

    try:
        asyncio.run(run_with_shutdown())
    except KeyboardInterrupt:
        # На Windows KeyboardInterrupt может прервать asyncio.run()
        # В этом случае stop() уже не вызовется из finally
        # Но состояние уже могло быть сохранено в shutdown handler
        print("\nInterrupted by user (KeyboardInterrupt)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
