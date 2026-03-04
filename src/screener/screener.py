# -*- coding: utf-8 -*-
"""
Manipulation Screener - главный класс системы.
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional
import structlog

from config.settings import settings
from .models import SymbolState, VulnerableSymbol, Detection, AlertSeverity
from .universe_scanner import UniverseScanner
from .vulnerability_filter import VulnerabilityFilter
from .realtime_monitor import RealTimeMonitor
from .detection_engine import DetectionEngine
from .alert_dispatcher import AlertDispatcher, AlertConfig
from .telegram_notifier import TelegramNotifier, TelegramConfig
from .futures_monitor import FuturesMonitor, FuturesDetection
from .history_loader import HistoryLoader, load_historical_data

# Signal generation imports
from src.signals.signal_generator import SignalGenerator
from src.signals.signal_formatter import SignalFormatter
from src.signals.models import SignalConfig
from src.signals.signal_logger import SignalLogger

# Cross-exchange imports
from src.exchanges.manager import ExchangeManager
from src.exchanges.models import UnifiedTrade, UnifiedOrderBook
from src.cross_exchange.state_store import StateStore as CrossExchangeStateStore
from src.cross_exchange.correlator import DetectorOrchestrator, DetectorOrchestratorConfig

# ML System imports
from src.ml import (
    MLIntegration,
    MLService,
    TailRiskManager,
    ModelMonitor,
    MarketRegimeDetector,
    MarketRegime,
)


logger = structlog.get_logger(__name__)


def _task_exception_handler(task: asyncio.Task) -> None:
    """RACE-4 FIX: Логирование исключений из fire-and-forget tasks."""
    try:
        exc = task.exception()
        if exc:
            logger.error("background_task_error", error=str(exc), task=task.get_name())
    except asyncio.CancelledError:
        pass  # Task was cancelled, not an error


class ManipulationScreener:
    """
    Главный класс скринера манипуляций.

    Объединяет все компоненты:
    1. UniverseScanner - сканирует все пары
    2. VulnerabilityFilter - фильтрует уязвимые
    3. RealTimeMonitor - мониторит в реальном времени
    4. DetectionEngine - детектирует манипуляции
    5. AlertDispatcher - отправляет алерты

    Использование:
        screener = ManipulationScreener()
        await screener.start()
        # ... работает пока не остановим
        await screener.stop()
    """

    # =========================================================================
    # ПАРАМЕТРЫ ЗАГРУЖАЮТСЯ ИЗ config/settings.py (settings.screener.*)
    # Для изменения — редактировать config/config.yaml
    # =========================================================================

    def __init__(
        self,
        alert_config: Optional[AlertConfig] = None,
        telegram_config: Optional[TelegramConfig] = None,
        rescan_interval: Optional[int] = None,
        max_symbols: Optional[int] = None,
    ):
        """
        Args:
            alert_config: Конфигурация для отправки алертов
            telegram_config: Конфигурация Telegram для уведомлений
            rescan_interval: Интервал пересканирования (по умолчанию из config.yaml)
            max_symbols: Максимум пар для мониторинга (по умолчанию из config.yaml)
        """
        self.RESCAN_INTERVAL = rescan_interval or settings.screener.rescan_interval_sec
        self.MAX_MONITORED_SYMBOLS = max_symbols or settings.screener.max_monitored_symbols

        # Компоненты
        self.universe_scanner = UniverseScanner()
        self.vulnerability_filter = VulnerabilityFilter()
        self.realtime_monitor = RealTimeMonitor(
            on_state_update=self._on_state_update
        )
        self.detection_engine = DetectionEngine()

        # Alert dispatcher
        if alert_config is None:
            alert_config = AlertConfig()  # Default: только локальный лог
        self.alert_dispatcher = AlertDispatcher(alert_config)

        # Telegram notifier
        self.telegram_notifier: Optional[TelegramNotifier] = None
        if telegram_config and telegram_config.is_configured:
            self.telegram_notifier = TelegramNotifier(telegram_config)

        # Futures monitor (Open Interest, Funding, Long/Short)
        self.futures_monitor = FuturesMonitor(
            on_detection=self._on_futures_detection
        )

        # =====================================================================
        # CROSS-EXCHANGE COMPONENTS
        # =====================================================================
        self.cross_exchange_enabled = getattr(
            settings.cross_exchange.orchestrator, 'enable_price_divergence', False
        ) or True  # Enable if any detector is enabled

        if self.cross_exchange_enabled:
            # Exchange manager for multi-exchange connections
            self.exchange_manager = ExchangeManager(settings.exchanges)

            # Cross-exchange state store
            self.cross_state = CrossExchangeStateStore()

            # Detector orchestrator
            orchestrator_config = DetectorOrchestratorConfig(
                enable_price_divergence=settings.cross_exchange.orchestrator.enable_price_divergence,
                enable_volume_correlation=settings.cross_exchange.orchestrator.enable_volume_correlation,
                enable_funding_arbitrage=settings.cross_exchange.orchestrator.enable_funding_arbitrage,
                enable_oi_migration=settings.cross_exchange.orchestrator.enable_oi_migration,
                enable_liquidity_hunt=settings.cross_exchange.orchestrator.enable_liquidity_hunt,
                enable_spoofing_cross=settings.cross_exchange.orchestrator.enable_spoofing_cross,
                parallel_analysis=settings.cross_exchange.orchestrator.parallel_analysis,
                max_concurrent_symbols=settings.cross_exchange.orchestrator.max_concurrent_symbols,
                min_severity=settings.cross_exchange.orchestrator.min_severity,
            )
            self.cross_correlator = DetectorOrchestrator(
                self.cross_state,
                orchestrator_config
            )

            # Cross-exchange analysis interval
            self._cross_analysis_interval = settings.cross_exchange.general.check_interval_sec
        else:
            self.exchange_manager = None
            self.cross_state = None
            self.cross_correlator = None
            self._cross_analysis_interval = 5

        self._cross_exchange_task: Optional[asyncio.Task] = None
        # =====================================================================

        # =====================================================================
        # SIGNAL GENERATOR + LOGGER
        # =====================================================================
        self.signal_generator: Optional[SignalGenerator] = None
        self.signal_formatter = SignalFormatter()
        # Абсолютный путь к логам сигналов
        project_root = Path(__file__).parent.parent.parent
        signals_log_path = project_root / "logs" / "signals.jsonl"
        self.signal_logger = SignalLogger(log_path=str(signals_log_path))

        # Initialize after all components are ready
        # (will be done in start() when cross_state is available)
        # =====================================================================

        # =====================================================================
        # ML SYSTEM COMPONENTS
        # =====================================================================
        self.ml_integration: Optional[MLIntegration] = None
        self.ml_service: Optional[MLService] = None
        self.tail_risk_manager = TailRiskManager()
        self.model_monitor = ModelMonitor()
        self.market_regime_detector = MarketRegimeDetector()
        self._current_market_regime: MarketRegime = MarketRegime.SIDEWAYS
        # =====================================================================

        # State
        self._vulnerable_symbols: list[VulnerableSymbol] = []
        self._running = False
        self._scan_task: Optional[asyncio.Task] = None
        # RACE-3 FIX: Track monitor tasks for proper cleanup
        self._realtime_task: Optional[asyncio.Task] = None
        self._futures_task: Optional[asyncio.Task] = None

        # Statistics
        self._stats = {
            "scans": 0,
            "detections": 0,
            "start_time": None,
        }

    async def start(self):
        """Запустить скринер."""
        self._running = True
        self._stats["start_time"] = datetime.now()

        logger.info("=" * 60)
        logger.info("MANIPULATION DETECTION SCREENER")
        logger.info(f"Started: {datetime.now()}")
        logger.info(f"Rescan interval: {self.RESCAN_INTERVAL}s")
        logger.info(f"Max monitored symbols: {self.MAX_MONITORED_SYMBOLS}")
        logger.info("=" * 60)

        # Запустить диспетчер алертов
        await self.alert_dispatcher.start()

        # Запустить Telegram notifier
        if self.telegram_notifier:
            await self.telegram_notifier.start()
            logger.info("Telegram notifications enabled")

        # =====================================================================
        # CROSS-EXCHANGE: Connect all exchanges and start analysis loop
        # =====================================================================
        if self.cross_exchange_enabled and self.exchange_manager:
            logger.info("Phase 0: Connecting cross-exchange connectors...")
            connection_results = await self.exchange_manager.connect_all()
            connected_count = sum(1 for v in connection_results.values() if v)
            logger.info(
                f"  Connected to {connected_count}/{len(connection_results)} exchanges"
            )

            # LEAK-4 FIX: Запустить фоновую очистку cross-exchange state store
            await self.cross_state.start()

            # Register callbacks
            self.exchange_manager.on_trade(self._handle_cross_trade)
            self.exchange_manager.on_orderbook(self._handle_cross_orderbook)

            # Start cross-exchange analysis loop
            self._cross_exchange_task = asyncio.create_task(
                self._cross_exchange_loop()
            )
            logger.info("Cross-exchange analysis loop started")
        # =====================================================================

        # =====================================================================
        # SIGNAL GENERATOR: Initialize after all components are ready
        # =====================================================================
        signal_config = SignalConfig.from_settings()
        self.signal_generator = SignalGenerator(
            futures_monitor=self.futures_monitor,
            state_store=self.cross_state if self.cross_state else CrossExchangeStateStore(),
            realtime_monitor=self.realtime_monitor,
            config=signal_config,
        )
        logger.info(
            "signal_generator_initialized",
            min_accumulation_score=signal_config.min_accumulation_score,
            min_probability=signal_config.min_probability,
            min_risk_reward=signal_config.min_risk_reward,
        )

        # Start signal logger
        self.signal_logger.start()
        # =====================================================================

        # =====================================================================
        # ML SYSTEM: Initialize ML integration
        # =====================================================================
        if settings.ml.enabled:
            logger.info("Phase ML: Initializing ML system...")
            self.ml_integration = MLIntegration(
                futures_monitor=self.futures_monitor,
                state_store=self.cross_state if self.cross_state else CrossExchangeStateStore(),
            )
            ml_initialized = await self.ml_integration.initialize()

            if ml_initialized:
                # Start ML service for periodic model reloading
                self.ml_service = MLService(
                    self.ml_integration,
                    reload_interval_hours=settings.ml.models.reload_interval_hours,
                )
                await self.ml_service.start()

                # Set baseline for model monitoring
                self.model_monitor.set_baseline(
                    accuracy=settings.ml.monitoring.baseline_accuracy,
                    sharpe=settings.ml.monitoring.baseline_sharpe,
                )

                logger.info(
                    "ml_system_initialized",
                    model_dir=settings.ml.models.save_dir,
                )
            else:
                logger.warning("ml_initialization_failed_models_not_found")
        else:
            logger.info("ml_system_disabled_in_config")
        # =====================================================================

        # Основной цикл сканирования
        while self._running:
            try:
                await self._scan_cycle()
                self._stats["scans"] += 1

                # Ждём до следующего сканирования
                logger.info(f"Next scan in {self.RESCAN_INTERVAL} seconds...")
                await asyncio.sleep(self.RESCAN_INTERVAL)

            except asyncio.CancelledError:
                logger.info("Scan cycle cancelled")
                break
            except Exception as e:
                logger.error("scan_cycle_error", error=str(e))
                await asyncio.sleep(60)  # Подождать перед retry

    async def stop(self):
        """Остановить скринер."""
        logger.info("Stopping screener...")
        self._running = False

        # Остановить cross-exchange
        if self._cross_exchange_task:
            self._cross_exchange_task.cancel()
            try:
                await self._cross_exchange_task
            except asyncio.CancelledError:
                pass

        # LEAK-4 FIX: Остановить фоновую очистку state store
        if self.cross_state:
            await self.cross_state.stop()

        if self.exchange_manager:
            await self.exchange_manager.disconnect_all()
            logger.info("Cross-exchange connectors disconnected")

        # Остановить мониторинг (spot)
        await self.realtime_monitor.stop()
        # RACE-3 FIX: Await task completion
        if self._realtime_task:
            try:
                await self._realtime_task
            except asyncio.CancelledError:
                pass

        # Остановить мониторинг (futures)
        await self.futures_monitor.stop()
        # RACE-3 FIX: Await task completion
        if self._futures_task:
            try:
                await self._futures_task
            except asyncio.CancelledError:
                pass

        # Остановить Telegram
        if self.telegram_notifier:
            await self.telegram_notifier.stop()

        # Остановить диспетчер
        await self.alert_dispatcher.stop()

        # Остановить логгер сигналов
        self.signal_logger.stop()

        # Остановить ML service
        if self.ml_service:
            await self.ml_service.stop()
            logger.info("ml_service_stopped")

        # Закрыть сессии
        await self.universe_scanner.close()
        await self.vulnerability_filter.close()

        # Статистика
        runtime = datetime.now() - self._stats["start_time"] if self._stats["start_time"] else None
        logger.info(
            "screener_stopped",
            runtime=str(runtime),
            scans=self._stats["scans"],
            detections=self._stats["detections"],
        )

    async def _scan_cycle(self):
        """Один цикл сканирования."""
        logger.info(f"\n{'='*60}")
        logger.info(f"[{datetime.now()}] Starting scan cycle...")

        # 1. Сканируем все пары
        logger.info("Phase 1: Scanning universe...")
        all_symbols = await self.universe_scanner.scan()
        logger.info(f"  Found {len(all_symbols)} trading pairs")

        # 2. Фильтруем уязвимые
        logger.info("Phase 2: Filtering vulnerable pairs...")
        self._vulnerable_symbols = await self.vulnerability_filter.filter(
            all_symbols,
            max_symbols=self.MAX_MONITORED_SYMBOLS
        )
        logger.info(f"  Found {len(self._vulnerable_symbols)} vulnerable pairs")

        # Топ-10 самых уязвимых
        if self._vulnerable_symbols:
            logger.info("\n  Top 10 most vulnerable:")
            for i, v in enumerate(self._vulnerable_symbols[:10], 1):
                logger.info(
                    f"    {i}. {v.symbol}: "
                    f"score={v.manipulation_ease_score}, "
                    f"depth=${v.order_book_depth_usd:.0f}, "
                    f"vol=${v.stats.volume_24h_usd:.0f}"
                )

        # 3. Обновляем мониторинг
        symbols_to_monitor = [v.symbol for v in self._vulnerable_symbols]

        if not symbols_to_monitor:
            logger.warning("No vulnerable symbols to monitor!")
            return

        # Остановить старый мониторинг
        await self.realtime_monitor.stop()

        # Прогрев baselines (КРИТИЧНО для детекции volume spikes!)
        logger.info(f"\nPhase 3a: Warming up baselines for {len(symbols_to_monitor)} pairs...")
        warmup_count = await self.realtime_monitor.warmup_baselines(symbols_to_monitor)
        logger.info(f"  Baselines initialized: {warmup_count}/{len(symbols_to_monitor)}")

        # Запустить spot мониторинг
        logger.info(f"\nPhase 3b: Starting real-time monitoring for {len(symbols_to_monitor)} pairs...")
        # RACE-3 FIX: Сохраняем task reference для proper cleanup
        self._realtime_task = asyncio.create_task(
            self.realtime_monitor.start(symbols_to_monitor)
        )

        # Запустить futures мониторинг (Open Interest, Funding, L/S Ratio)
        logger.info(f"\nPhase 4: Starting FUTURES monitoring (OI, Funding, L/S)...")
        await self.futures_monitor.stop()  # Остановить старый
        # RACE-3 FIX: Сохраняем task reference для proper cleanup
        self._futures_task = asyncio.create_task(
            self.futures_monitor.start(symbols_to_monitor)
        )

        # =====================================================================
        # Phase 4b: Load additional historical data (funding history for gradient)
        # =====================================================================
        if settings.history.enabled:
            logger.info(f"\nPhase 4b: Loading historical data...")
            try:
                # Wait a bit for futures_monitor to initialize symbols
                await asyncio.sleep(0.5)

                result = await load_historical_data(
                    symbols=symbols_to_monitor,
                    futures_monitor=self.futures_monitor,
                    realtime_monitor=self.realtime_monitor,
                    cross_state=self.cross_state if self.cross_exchange_enabled else None,
                )
                logger.info(
                    f"  Historical data loaded: "
                    f"klines={result.loaded_klines}, "
                    f"funding={result.loaded_funding}, "
                    f"oi={result.loaded_oi}, "
                    f"trades={result.loaded_trades}, "
                    f"cross_ex={result.loaded_cross_exchange} "
                    f"({result.duration_sec:.1f}s)"
                )
            except Exception as e:
                logger.warning("history_loading_failed", error=str(e))
        # =====================================================================

        # Subscribe cross-exchange to new symbols
        if self.exchange_manager and self.cross_exchange_enabled:
            logger.info(f"\nPhase 5: Subscribing cross-exchange to {len(symbols_to_monitor)} pairs...")
            await self.exchange_manager.subscribe_symbols(symbols_to_monitor)

        logger.info("Scan cycle complete. Spot + Futures + Cross-Exchange monitoring active.")

    def _on_state_update(self, state: SymbolState):
        """Callback при обновлении состояния пары."""
        # Запустить детекцию
        detections = self.detection_engine.analyze(state)

        # Добавить данные фьючерсов к детекциям (корреляция)
        futures_signal = self.futures_monitor.get_combined_signal(state.symbol)

        for detection in detections:
            # Добавить OI данные к детекции если есть
            if futures_signal.get("has_data"):
                detection.details["futures_oi_change_1h"] = futures_signal.get("oi_change_1h_pct")
                detection.details["futures_funding"] = futures_signal.get("funding_rate_pct")
                detection.details["futures_pump_risk"] = futures_signal.get("pump_risk_score")

                # Добавить L/S ratio данные для ML анализа
                if "long_pct" in futures_signal:
                    detection.details["long_account_pct"] = futures_signal.get("long_pct")
                    detection.details["short_account_pct"] = futures_signal.get("short_pct")

                # Увеличить score если фьючерсы подтверждают
                if futures_signal.get("pump_risk_score", 0) > 50:
                    detection.score = min(100, detection.score + 10)
                    detection.evidence.append(
                        f"📊 Futures confirm: OI {futures_signal.get('oi_change_1h_pct', 0):+.1f}%, "
                        f"Pump risk: {futures_signal.get('pump_risk_score', 0)}%"
                    )

            # RACE-4 FIX: Handle detection async to support ML optimization
            task = asyncio.create_task(self._handle_detection_async(detection))
            task.add_done_callback(_task_exception_handler)

    def _on_futures_detection(self, detection: FuturesDetection):
        """Callback при детекции на фьючерсах."""
        # NOTE: НЕ увеличиваем self._stats["detections"] здесь!
        # Это делается в _handle_detection() который вызывается ниже.

        # Конвертировать в общий формат Detection
        from .models import Detection as SpotDetection

        # Скопировать details и добавить spot данные
        enriched_details = dict(detection.details)

        # Получить spot state для обогащения данными о трейдах
        spot_state = self.realtime_monitor.get_state(detection.symbol)
        if spot_state:
            # Добавить данные о трейдах из spot мониторинга
            if 'trades_count' not in enriched_details and spot_state.trades_5m is not None:
                enriched_details['trades_count'] = len(spot_state.trades_5m)
            if 'buy_ratio' not in enriched_details and spot_state.buy_ratio_5m is not None:
                enriched_details['buy_ratio'] = round(float(spot_state.buy_ratio_5m), 4)
            if 'sell_ratio' not in enriched_details and spot_state.buy_ratio_5m is not None:
                enriched_details['sell_ratio'] = round(1 - float(spot_state.buy_ratio_5m), 4)
            if 'volume_5m' not in enriched_details and spot_state.volume_5m is not None:
                enriched_details['volume_5m'] = round(float(spot_state.volume_5m), 2)
            if 'current_price' not in enriched_details and spot_state.last_price is not None and spot_state.last_price > 0:
                enriched_details['current_price'] = round(float(spot_state.last_price), 6)
            # Стакан
            if 'bid_volume' not in enriched_details and spot_state.bid_volume_20 is not None and spot_state.bid_volume_20 > 0:
                enriched_details['bid_volume'] = round(float(spot_state.bid_volume_20), 2)
            if 'ask_volume' not in enriched_details and spot_state.ask_volume_20 is not None and spot_state.ask_volume_20 > 0:
                enriched_details['ask_volume'] = round(float(spot_state.ask_volume_20), 2)

        # Округлить все числовые значения для предотвращения 18 знаков после запятой
        for key, value in list(enriched_details.items()):
            if isinstance(value, (float, int)) and not isinstance(value, bool):
                if key in ('bid_volume', 'ask_volume', 'volume_5m', 'volume_ratio', 'current_oi_usd'):
                    enriched_details[key] = round(float(value), 2)
                elif key in ('buy_ratio', 'sell_ratio', 'imbalance', 'spread_pct'):
                    enriched_details[key] = round(float(value), 4)
                elif key in ('current_price', 'mark_price'):
                    enriched_details[key] = round(float(value), 6)
                elif key.endswith('_pct'):
                    enriched_details[key] = round(float(value), 2)

        spot_detection = SpotDetection(
            symbol=detection.symbol,
            timestamp=detection.timestamp,
            severity=detection.severity,
            detection_type=f"FUTURES_{detection.detection_type}",
            score=detection.score,
            details=enriched_details,
            evidence=["[FUTURES DATA] " + e for e in detection.evidence],
        )

        # RACE-4 FIX: Handle detection async to support ML optimization
        task = asyncio.create_task(self._handle_detection_async(spot_detection))
        task.add_done_callback(_task_exception_handler)

    async def _handle_detection_async(self, detection: Detection):
        """Обработать детекцию (async для ML optimization)."""
        self._stats["detections"] += 1

        # Логировать в консоль
        severity_emoji = {
            AlertSeverity.INFO: "ℹ️ ",
            AlertSeverity.WARNING: "⚠️ ",
            AlertSeverity.ALERT: "🚨",
            AlertSeverity.CRITICAL: "🔴",
        }

        emoji = severity_emoji.get(detection.severity, "")
        logger.warning(
            f"\n{emoji} DETECTION: {detection.symbol}",
            type=detection.detection_type,
            score=detection.score,
            severity=detection.severity.name,
        )
        for evidence in detection.evidence:
            logger.warning(f"   • {evidence}")

        # Отправить в Binance (через очередь)
        # RACE-4 FIX: Добавляем exception handler
        task = asyncio.create_task(self.alert_dispatcher.dispatch(detection))
        task.add_done_callback(_task_exception_handler)

        # Отправить в Telegram
        if self.telegram_notifier:
            # RACE-4 FIX: Добавляем exception handler
            task = asyncio.create_task(self.telegram_notifier.send_alert(detection))
            task.add_done_callback(_task_exception_handler)

        # =====================================================================
        # SIGNAL GENERATION: Check for trading signal on WARNING+ detections
        # FIX: Было ALERT - но многие важные детекции (OI_SPIKE, EXTREME_POSITIONING)
        #      имеют severity=WARNING и не попадали на проверку сигналов!
        # =====================================================================
        if self.signal_generator and detection.severity.value >= AlertSeverity.WARNING.value:
            try:
                logger.info(
                    "signal_check_for_detection",
                    symbol=detection.symbol,
                    detection_type=detection.detection_type,
                    severity=detection.severity.value,
                    score=detection.score,
                )
                signal = self.signal_generator.on_detection(detection)
                if signal:
                    # ===============================================================
                    # ML INTEGRATION: Optimize signal before logging/sending
                    # ===============================================================
                    ml_optimized = None
                    ml_filtered = False
                    ml_filter_reason = None

                    if self.ml_integration and self.ml_integration.is_ready:
                        # 1. Check if model should be used (drift detection)
                        if not self.model_monitor.should_use_ml():
                            logger.warning(
                                "ml_fallback_to_original",
                                symbol=signal.symbol,
                                reason="model_drift_detected",
                            )
                        else:
                            # 2. Tail risk check (black swan protection)
                            futures_state_for_tail = self.futures_monitor.get_state(detection.symbol)
                            price_24h_change = detection.details.get("price_change_24h_pct", 0)
                            volume_24h_change = detection.details.get("volume_spike_ratio", 1) * 100 - 100
                            funding_rate_tail = futures_state_for_tail.funding_rate if futures_state_for_tail else 0

                            is_safe, tail_risk_reason = self.tail_risk_manager.check_anomalies(
                                symbol=signal.symbol,
                                price_24h_change_pct=price_24h_change,
                                volume_24h_change_pct=volume_24h_change,
                                funding_rate=funding_rate_tail,
                            )

                            if not is_safe:
                                logger.warning(
                                    "ml_tail_risk_block",
                                    symbol=signal.symbol,
                                    reason=tail_risk_reason,
                                )
                                ml_filtered = True
                                ml_filter_reason = f"tail_risk: {tail_risk_reason}"
                            else:
                                # 3. Risk management check
                                direction = 1 if signal.direction.value == "LONG" else -1
                                can_trade, risk_reason = self.ml_integration.can_trade(
                                    signal.symbol, direction
                                )

                                if not can_trade:
                                    logger.warning(
                                        "ml_risk_block",
                                        symbol=signal.symbol,
                                        reason=risk_reason,
                                    )
                                    ml_filtered = True
                                    ml_filter_reason = f"risk: {risk_reason}"
                                else:
                                    # 4. Optimize signal with ML
                                    ml_optimized = await self.ml_integration.optimize_signal(signal)

                                    if ml_optimized is None:
                                        ml_filtered = True
                                        ml_filter_reason = "ml_low_confidence"
                                        logger.info(
                                            "ml_signal_filtered",
                                            symbol=signal.symbol,
                                            reason="low_ml_confidence",
                                        )
                                    elif not ml_optimized.should_trade:
                                        ml_filtered = True
                                        ml_filter_reason = ml_optimized.filter_reason
                                        logger.info(
                                            "ml_signal_filtered",
                                            symbol=signal.symbol,
                                            reason=ml_optimized.filter_reason,
                                        )
                                    else:
                                        # 5. Get position size recommendation
                                        position_size = self.ml_integration.get_position_size(ml_optimized)
                                        ml_optimized.suggested_position_pct = position_size

                                        logger.info(
                                            "ml_signal_approved",
                                            symbol=signal.symbol,
                                            original_confidence=ml_optimized.original_confidence,
                                            ml_confidence=ml_optimized.ml_confidence,
                                            combined_confidence=ml_optimized.combined_confidence,
                                            optimized_sl=ml_optimized.optimized_sl_pct,
                                            position_size=position_size,
                                        )

                    # ===============================================================
                    # СНАЧАЛА логируем — это критично для бэктестинга!
                    # Telegram отправляется ТОЛЬКО если лог записан успешно!
                    # ===============================================================
                    futures_state = self.futures_monitor.get_state(detection.symbol)
                    spot_state = self.realtime_monitor.get_state(detection.symbol)
                    accumulation = None
                    try:
                        accumulation = self.signal_generator.accumulation_detector.analyze(detection.symbol)
                    except Exception:
                        pass  # Accumulation не критичен для логирования

                    # Add ML data to config snapshot
                    config_snapshot = {
                        "min_accumulation_score": self.signal_generator.config.min_accumulation_score,
                        "min_probability": self.signal_generator.config.min_probability,
                        "min_risk_reward": self.signal_generator.config.min_risk_reward,
                        "default_sl_pct": self.signal_generator.config.default_sl_pct,
                        "tp1_ratio": self.signal_generator.config.tp1_ratio,
                        "tp2_ratio": self.signal_generator.config.tp2_ratio,
                        "tp3_ratio": self.signal_generator.config.tp3_ratio,
                    }

                    # Add ML optimization data if available
                    if ml_optimized and ml_optimized.should_trade:
                        config_snapshot["ml_optimization"] = ml_optimized.to_dict()
                    elif ml_filtered:
                        config_snapshot["ml_filtered"] = True
                        config_snapshot["ml_filter_reason"] = ml_filter_reason

                    log_success = self.signal_logger.log_signal(
                        signal=signal,
                        futures_state=futures_state,
                        spot_state=spot_state,
                        state_store=self.cross_state,
                        trigger_detection=detection,
                        accumulation_score=accumulation.score if accumulation else None,
                        config_snapshot=config_snapshot,
                    )

                    # ===============================================================
                    # Telegram ТОЛЬКО если лог записан!
                    # ML-filtered сигналы логируются, но НЕ отправляются в Telegram
                    # ===============================================================
                    if log_success and not ml_filtered:
                        if self.telegram_notifier:
                            # Format with ML optimization data if available
                            signal_text = self.signal_formatter.format_signal(signal)

                            # Add ML enhancement info
                            if ml_optimized and ml_optimized.should_trade:
                                ml_info = (
                                    f"\n\n🤖 *ML Enhancement:*\n"
                                    f"• Confidence: {ml_optimized.combined_confidence:.0%}\n"
                                    f"• Win Prob: {ml_optimized.predicted_win_probability:.0%}\n"
                                    f"• Position Size: {ml_optimized.suggested_position_pct:.1f}%\n"
                                    f"• Optimized SL: {ml_optimized.optimized_sl_pct:.2f}%"
                                )
                                signal_text += ml_info

                            task = asyncio.create_task(
                                self.telegram_notifier.send_trade_signal(signal_text)
                            )
                            task.add_done_callback(_task_exception_handler)

                        logger.info(
                            "trade_signal_sent",
                            symbol=signal.symbol,
                            direction=signal.direction.value,
                            probability=signal.probability,
                            ml_enhanced=ml_optimized is not None and ml_optimized.should_trade,
                        )
                    elif log_success and ml_filtered:
                        logger.info(
                            "signal_logged_but_filtered_by_ml",
                            symbol=signal.symbol,
                            direction=signal.direction.value,
                            filter_reason=ml_filter_reason,
                        )
                    else:
                        logger.error(
                            "signal_not_sent_log_failed",
                            symbol=signal.symbol,
                            signal_id=signal.signal_id,
                            direction=signal.direction.value,
                        )
            except Exception as e:
                logger.warning("signal_generation_error", error=str(e), symbol=detection.symbol)
        # =====================================================================

    def get_vulnerable_symbols(self) -> list[VulnerableSymbol]:
        """Получить список уязвимых пар."""
        return self._vulnerable_symbols.copy()

    def get_symbol_state(self, symbol: str) -> Optional[SymbolState]:
        """Получить текущее состояние пары."""
        return self.realtime_monitor.get_state(symbol)

    def get_stats(self) -> dict:
        """Получить статистику работы."""
        stats = {
            **self._stats,
            "vulnerable_symbols": len(self._vulnerable_symbols),
            "monitored_symbols": len(self.realtime_monitor.get_all_states()),
            "dispatcher_stats": self.alert_dispatcher.get_stats(),
        }

        # Add cross-exchange stats
        if self.exchange_manager:
            stats["cross_exchange"] = self.exchange_manager.get_stats()

        # Add ML stats
        if self.ml_integration:
            stats["ml"] = self.ml_integration.get_stats()
            stats["ml"]["market_regime"] = self._current_market_regime.value
            stats["ml"]["model_healthy"] = self.model_monitor.should_use_ml()

        return stats

    # =========================================================================
    # CROSS-EXCHANGE METHODS
    # =========================================================================

    def _handle_cross_trade(self, exchange: str, trade: UnifiedTrade) -> None:
        """
        Callback for trades from all exchanges.

        Updates cross-exchange state store with new trade data.
        """
        if not self.cross_state:
            return

        try:
            # Schedule async update (callback is sync, StateStore methods are async)
            asyncio.create_task(
                self.cross_state.update_price(
                    exchange=exchange,
                    symbol=trade.symbol,
                    price=trade.price,
                    timestamp=trade.timestamp,
                    volume_24h=None  # Trade doesn't have 24h volume
                )
            )

            # Update trade for volume tracking
            asyncio.create_task(
                self.cross_state.update_trade(exchange, trade)
            )
        except Exception as e:
            logger.error(
                "cross_trade_handler_error",
                exchange=exchange,
                symbol=trade.symbol,
                error=str(e)
            )

    def _handle_cross_orderbook(
        self,
        exchange: str,
        orderbook: UnifiedOrderBook
    ) -> None:
        """
        Callback for orderbook updates from all exchanges.

        Updates cross-exchange state store with orderbook data.
        """
        if not self.cross_state:
            return

        try:
            # Schedule async update (callback is sync, StateStore methods are async)
            asyncio.create_task(
                self.cross_state.update_orderbook(
                    exchange,
                    orderbook.symbol,
                    orderbook
                )
            )
        except Exception as e:
            logger.error(
                "cross_orderbook_handler_error",
                exchange=exchange,
                symbol=orderbook.symbol,
                error=str(e)
            )

    async def _cross_exchange_loop(self) -> None:
        """
        Periodic cross-exchange analysis loop.

        Runs all cross-exchange detectors on monitored symbols
        and dispatches any detections.
        """
        logger.info("Cross-exchange analysis loop starting...")

        while self._running:
            try:
                # Get symbols to analyze
                symbols = [v.symbol for v in self._vulnerable_symbols]

                if not symbols:
                    await asyncio.sleep(self._cross_analysis_interval)
                    continue

                # Subscribe to symbols on all exchanges (if not already)
                if self.exchange_manager:
                    connected = self.exchange_manager.get_connected_exchanges()
                    if connected:
                        await self.exchange_manager.subscribe_symbols(symbols)

                # Run cross-exchange analysis
                if self.cross_correlator:
                    all_detections = await self.cross_correlator.analyze_all(
                        symbols=symbols
                    )

                    # Process detections
                    for symbol, detections in all_detections.items():
                        for detection in detections:
                            await self._handle_cross_detection(detection)

                await asyncio.sleep(self._cross_analysis_interval)

            except asyncio.CancelledError:
                logger.info("Cross-exchange loop cancelled")
                break
            except Exception as e:
                logger.error("cross_exchange_loop_error", error=str(e))
                await asyncio.sleep(self._cross_analysis_interval)

    async def _handle_cross_detection(self, detection) -> None:
        """
        Handle a detection from cross-exchange analysis.

        Converts to screener Detection format and dispatches.
        """
        from src.cross_exchange.detectors.base import Detection as CrossDetection
        from src.cross_exchange.detectors.base import Severity
        from decimal import Decimal

        self._stats["detections"] += 1

        # Map severity
        severity_map = {
            Severity.INFO: AlertSeverity.INFO,
            Severity.WARNING: AlertSeverity.WARNING,
            Severity.ALERT: AlertSeverity.ALERT,
            Severity.CRITICAL: AlertSeverity.CRITICAL,
        }

        # Build enriched details with spot data
        enriched_details = {
            "exchanges": detection.exchanges,
            "description": detection.description,
            "recommended_action": detection.recommended_action,
        }

        # Add cross-exchange details with rounding
        for key, value in detection.details.items():
            if isinstance(value, Decimal):
                value = float(value)
            if isinstance(value, float):
                if key.endswith('_pct') or key.endswith('_percent'):
                    enriched_details[key] = round(value, 2)
                elif abs(value) > 1000:
                    enriched_details[key] = round(value, 2)
                else:
                    enriched_details[key] = round(value, 4)
            else:
                enriched_details[key] = value

        # Enrich with spot data if available
        spot_state = self.realtime_monitor.get_state(detection.symbol)
        if spot_state:
            if 'trades_count' not in enriched_details and spot_state.trades_5m is not None:
                enriched_details['trades_count'] = len(spot_state.trades_5m)
            if 'buy_ratio' not in enriched_details and spot_state.buy_ratio_5m is not None:
                enriched_details['buy_ratio'] = round(float(spot_state.buy_ratio_5m), 4)
            if 'sell_ratio' not in enriched_details and spot_state.buy_ratio_5m is not None:
                enriched_details['sell_ratio'] = round(1 - float(spot_state.buy_ratio_5m), 4)
            if 'volume_5m' not in enriched_details and spot_state.volume_5m is not None:
                enriched_details['volume_5m'] = round(float(spot_state.volume_5m), 2)

        # Convert to screener Detection
        screener_detection = Detection(
            symbol=detection.symbol,
            timestamp=detection.timestamp,
            severity=severity_map.get(detection.severity, AlertSeverity.WARNING),
            detection_type=f"CROSS_{detection.detection_type.value}",
            score=int(detection.confidence * 100),
            details=enriched_details,
            evidence=[
                f"[CROSS-EXCHANGE] {detection.description}",
                f"Exchanges: {', '.join(detection.exchanges)}",
                f"Confidence: {detection.confidence:.0%}",
            ],
        )

        # Log
        severity_emoji = {
            AlertSeverity.INFO: "ℹ️ ",
            AlertSeverity.WARNING: "⚠️ ",
            AlertSeverity.ALERT: "🚨",
            AlertSeverity.CRITICAL: "🔴",
        }
        emoji = severity_emoji.get(screener_detection.severity, "🔄")

        logger.warning(
            f"\n{emoji} CROSS-EXCHANGE DETECTION: {detection.symbol}",
            type=screener_detection.detection_type,
            score=screener_detection.score,
            severity=screener_detection.severity.name,
            exchanges=detection.exchanges,
        )
        for evidence in screener_detection.evidence:
            logger.warning(f"   • {evidence}")

        # Dispatch
        await self.alert_dispatcher.dispatch(screener_detection)

        # Telegram (детекция)
        if self.telegram_notifier:
            asyncio.create_task(
                self.telegram_notifier.send_alert(screener_detection)
            )

        # =====================================================================
        # SIGNAL GENERATION: Check for trading signal on cross-exchange detections
        # Telegram отправляется ТОЛЬКО если лог записан успешно!
        # =====================================================================
        if self.signal_generator and screener_detection.severity.value >= AlertSeverity.ALERT.value:
            try:
                signal = self.signal_generator.on_detection(screener_detection)
                if signal:
                    # ===============================================================
                    # ML INTEGRATION: Optimize cross-exchange signal
                    # ===============================================================
                    ml_optimized = None
                    ml_filtered = False
                    ml_filter_reason = None

                    if self.ml_integration and self.ml_integration.is_ready:
                        if self.model_monitor.should_use_ml():
                            # Risk management check
                            direction = 1 if signal.direction.value == "LONG" else -1
                            can_trade, risk_reason = self.ml_integration.can_trade(
                                signal.symbol, direction
                            )

                            if not can_trade:
                                ml_filtered = True
                                ml_filter_reason = f"risk: {risk_reason}"
                            else:
                                ml_optimized = await self.ml_integration.optimize_signal(signal)
                                if ml_optimized is None or not ml_optimized.should_trade:
                                    ml_filtered = True
                                    ml_filter_reason = (
                                        ml_optimized.filter_reason if ml_optimized
                                        else "low_ml_confidence"
                                    )
                                else:
                                    position_size = self.ml_integration.get_position_size(ml_optimized)
                                    ml_optimized.suggested_position_pct = position_size

                    # Логируем сигнал
                    futures_state = self.futures_monitor.get_state(screener_detection.symbol)
                    spot_state = self.realtime_monitor.get_state(screener_detection.symbol)
                    accumulation = None
                    try:
                        accumulation = self.signal_generator.accumulation_detector.analyze(screener_detection.symbol)
                    except Exception:
                        pass

                    config_snapshot = {
                        "min_accumulation_score": self.signal_generator.config.min_accumulation_score,
                        "min_probability": self.signal_generator.config.min_probability,
                        "min_risk_reward": self.signal_generator.config.min_risk_reward,
                        "default_sl_pct": self.signal_generator.config.default_sl_pct,
                        "tp1_ratio": self.signal_generator.config.tp1_ratio,
                        "tp2_ratio": self.signal_generator.config.tp2_ratio,
                        "tp3_ratio": self.signal_generator.config.tp3_ratio,
                    }

                    if ml_optimized and ml_optimized.should_trade:
                        config_snapshot["ml_optimization"] = ml_optimized.to_dict()
                    elif ml_filtered:
                        config_snapshot["ml_filtered"] = True
                        config_snapshot["ml_filter_reason"] = ml_filter_reason

                    log_success = self.signal_logger.log_signal(
                        signal=signal,
                        futures_state=futures_state,
                        spot_state=spot_state,
                        state_store=self.cross_state,
                        trigger_detection=screener_detection,
                        accumulation_score=accumulation.score if accumulation else None,
                        config_snapshot=config_snapshot,
                    )

                    # Telegram ТОЛЬКО если лог записан и НЕ отфильтрован ML!
                    if log_success and not ml_filtered:
                        if self.telegram_notifier:
                            signal_text = self.signal_formatter.format_signal(signal)

                            if ml_optimized and ml_optimized.should_trade:
                                ml_info = (
                                    f"\n\n🤖 *ML Enhancement:*\n"
                                    f"• Confidence: {ml_optimized.combined_confidence:.0%}\n"
                                    f"• Win Prob: {ml_optimized.predicted_win_probability:.0%}\n"
                                    f"• Position Size: {ml_optimized.suggested_position_pct:.1f}%"
                                )
                                signal_text += ml_info

                            task = asyncio.create_task(
                                self.telegram_notifier.send_trade_signal(signal_text)
                            )
                            task.add_done_callback(_task_exception_handler)

                        logger.info(
                            "cross_trade_signal_sent",
                            symbol=signal.symbol,
                            direction=signal.direction.value,
                            probability=signal.probability,
                            ml_enhanced=ml_optimized is not None,
                        )
                    elif log_success and ml_filtered:
                        logger.info(
                            "cross_signal_logged_but_filtered_by_ml",
                            symbol=signal.symbol,
                            filter_reason=ml_filter_reason,
                        )
                    else:
                        logger.error(
                            "cross_signal_not_sent_log_failed",
                            symbol=signal.symbol,
                            signal_id=signal.signal_id,
                            direction=signal.direction.value,
                        )
            except Exception as e:
                logger.warning("cross_signal_generation_error", error=str(e), symbol=screener_detection.symbol)
