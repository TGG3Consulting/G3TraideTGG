# -*- coding: utf-8 -*-
"""
SignalRunner - Runs signal generation over historical data.

Iterates through time minute by minute, builds states, and generates signals
using the same logic as the production BinanceFriend system.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple

from data_downloader import SymbolHistoryData
from state_builder import StateBuilder, FuturesState, SymbolState
from output_writer import OutputWriter
from config import AppConfig

from signals import (
    AccumulationDetector,
    AccumulationSignal,
    RiskCalculator,
    SignalConfig,
    SignalDirection,
    SignalType,
    TradeSignal,
    AccumulationScore,
)


class SignalRunner:
    """
    Runs historical signal generation.

    Processes each minute in the time range for each symbol,
    generating signals that would have been triggered by the production system.
    """

    def __init__(
        self,
        downloader_data: Dict[str, SymbolHistoryData],
        state_builder: StateBuilder,
        output_writer: OutputWriter,
        config: AppConfig,
    ):
        """
        Initialize the signal runner.

        Args:
            downloader_data: Historical data from BinanceHistoryDownloader
            state_builder: StateBuilder instance
            output_writer: OutputWriter for saving signals
            config: Application config
        """
        self.data = downloader_data
        self.builder = state_builder
        self.writer = output_writer
        self.config = config

        # Create SignalConfig from AppConfig
        self.signal_config = SignalConfig(
            min_accumulation_score=config.min_accumulation_score,
            min_probability=config.min_probability,
            min_risk_reward=config.min_risk_reward,
        )

        # Кэш порогов как float — не конвертировать Decimal каждую итерацию
        self._volume_threshold = float(config.volume_spike_threshold)
        self._buy_threshold = float(config.buy_ratio_threshold)
        self._oi_threshold = float(config.oi_spike_threshold)
        self._price_threshold = float(config.price_momentum_threshold)

        # Signal ID counter (faster than hashlib)
        self._signal_counter = 0

        # Initialize detectors
        self.detector = AccumulationDetector(config=self.signal_config)
        self.risk_calc = RiskCalculator(config=self.signal_config)

        # Stats
        self.total_signals = 0
        self.signals_by_symbol: Dict[str, int] = {}

        # Cooldown: последний сигнал для каждого символа (7 дней для дневного таймфрейма)
        self._last_signal_time: Dict[str, datetime] = {}
        self._signal_cooldown_hours = 168  # 7 дней

    def run(
        self,
        symbols: List[str],
        start_time: datetime,
        end_time: datetime,
    ) -> int:
        """
        Run signal generation for all symbols over the time period.

        Args:
            symbols: List of symbols to process
            start_time: Start of period (UTC)
            end_time: End of period (UTC)

        Returns:
            Total number of signals generated
        """
        # Ensure UTC timezone
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        print(f"\n{'='*60}", flush=True)
        print(f"SIGNAL GENERATION", flush=True)
        print(f"Period: {start_time.strftime('%Y-%m-%d %H:%M')} -> {end_time.strftime('%Y-%m-%d %H:%M')}", flush=True)
        print(f"Symbols: {len(symbols)}", flush=True)
        print(f"{'='*60}\n", flush=True)

        # Calculate total iterations for progress
        total_minutes = int((end_time - start_time).total_seconds() / 60)
        total_iterations = total_minutes * len(symbols)
        iteration = 0

        # Iterate through time minute by minute
        current_time = start_time

        while current_time <= end_time:
            for symbol in symbols:
                iteration += 1

                # Progress every 5000 iterations
                if iteration % 5000 == 0:
                    progress_pct = (iteration / total_iterations) * 100
                    print(
                        f"[{current_time.strftime('%Y-%m-%d %H:%M')}] "
                        f"{progress_pct:.0f}% | "
                        f"Symbols: {len(symbols)} | "
                        f"Signals: {self.total_signals}",
                        flush=True
                    )

                # Process this symbol at this timestamp
                result = self._process_symbol_at_time(symbol, current_time)

                if result:
                    signal, accumulation, futures_state, spot_state, trigger_detection = result

                    # Build config snapshot
                    config_snapshot = {
                        "min_accumulation_score": self.config.min_accumulation_score,
                        "min_probability": self.config.min_probability,
                        "min_risk_reward": self.config.min_risk_reward,
                        "default_sl_pct": float(self.signal_config.default_sl_pct),
                        "tp1_ratio": float(self.signal_config.tp1_ratio),
                        "tp2_ratio": float(self.signal_config.tp2_ratio),
                        "tp3_ratio": float(self.signal_config.tp3_ratio),
                    }

                    # Write signal with full context
                    self.writer.write_signal(
                        signal=signal,
                        accumulation_score=accumulation.score,
                        futures_state=futures_state,
                        spot_state=spot_state,
                        trigger_detection=trigger_detection,
                        config_snapshot=config_snapshot,
                    )

                    self.total_signals += 1
                    self.signals_by_symbol[symbol] = self.signals_by_symbol.get(symbol, 0) + 1

                    # Обновить cooldown
                    self._last_signal_time[symbol] = current_time

            # Move to next minute
            current_time += timedelta(minutes=self.config.signal_step_minutes)

        # NOTE: Don't close writer here - caller manages writer lifecycle
        # This allows writer reuse across chunks in batch mode

        # Print summary
        print(f"\n{'='*60}", flush=True)
        print(f"GENERATION COMPLETE", flush=True)
        print(f"Total signals: {self.total_signals}", flush=True)
        print(f"Symbols with signals: {len(self.signals_by_symbol)}", flush=True)
        print(f"{'='*60}\n", flush=True)

        # Top 10 symbols by signal count
        if self.signals_by_symbol:
            print("Top 10 symbols by signal count:")
            sorted_symbols = sorted(
                self.signals_by_symbol.items(),
                key=lambda x: x[1],
                reverse=True
            )[:10]
            for sym, count in sorted_symbols:
                print(f"  {sym}: {count}")

        return self.total_signals

    def _process_symbol_at_time(
        self,
        symbol: str,
        timestamp: datetime
    ) -> Optional[Tuple[TradeSignal, AccumulationSignal, FuturesState, SymbolState, Optional[Dict]]]:
        """
        Process a symbol at a specific timestamp.

        1. Build states
        2. Simulate detection trigger
        3. Run accumulation analysis
        4. Generate signal if conditions met

        Returns:
            Tuple of (signal, accumulation, futures_state, spot_state, trigger_detection) or None
        """
        # Blacklist check - токсичные монеты дают 80%+ убытков
        if symbol in self.signal_config.symbol_blacklist:
            return None

        # Blocked hours check - часы 10-12 UTC убыточны в 67% файлов
        if timestamp.hour in self.signal_config.blocked_hours_utc:
            return None

        # Blocked weekdays check (0=Mon, 1=Tue, ..., 6=Sun)
        if timestamp.weekday() in self.signal_config.blocked_weekdays:
            return None

        # Cooldown check - максимум 1 сигнал в 24 часа на символ
        if symbol in self._last_signal_time:
            hours_since_last = (timestamp - self._last_signal_time[symbol]).total_seconds() / 3600
            if hours_since_last < self._signal_cooldown_hours:
                return None

        # Build states (нужны для проверки volume_spike)
        futures_state = self.builder.build_futures_state(symbol, timestamp)
        spot_state = self.builder.build_spot_state(symbol, timestamp)

        if not futures_state or not spot_state:
            return None

        # Max volume spike - перенесено в accumulation_detector как штраф -3 (50% = 0 edge)

        # Check for valid price
        if spot_state.last_price <= 0:
            return None

        # Simulate detection trigger
        detection = self._simulate_detection(symbol, spot_state, futures_state, timestamp)

        if detection:
            self.detector.add_detection(symbol, detection, current_time=timestamp)

        # Run accumulation analysis
        min_score = 50 if detection else None

        accumulation = self.detector.analyze(
            symbol=symbol,
            futures_state=futures_state,
            spot_state=spot_state,
            skip_threshold=False,
            min_score_override=min_score,
        )

        if not accumulation:
            return None

        recent_detections = self.detector.get_recent_detections(
            symbol, minutes=30, current_time=timestamp
        )
        effective_detection = recent_detections[-1] if recent_detections else None

        signal = self._create_signal(
            symbol=symbol,
            accumulation=accumulation,
            spot_state=spot_state,
            futures_state=futures_state,
            timestamp=timestamp,
            detection_type=effective_detection.get("type") if effective_detection else None,
        )

        if not signal:
            return None

        trigger_detection = None
        if effective_detection:
            trigger_detection = {
                "type": effective_detection.get("type"),
                "timestamp": effective_detection.get("timestamp").isoformat() if effective_detection.get("timestamp") else None,
                "severity": effective_detection.get("severity"),
                "score": effective_detection.get("score"),
                "evidence": [],
                "details": effective_detection.get("details", {}),
            }

        return (signal, accumulation, futures_state, spot_state, trigger_detection)

    def _simulate_detection(
        self,
        symbol: str,
        spot_state: SymbolState,
        futures_state: FuturesState,
        timestamp: datetime
    ) -> Optional[Dict[str, Any]]:
        """
        Simulate detection trigger based on market conditions.

        In production, detections come from realtime monitoring.
        For historical analysis, we simulate triggers based on config thresholds.
        """
        # Common details for all detections (matching signal_logger.py format)
        common_details = {
            "trades_count": spot_state.trade_count_5m,
            "buy_ratio": float(spot_state.buy_ratio_5m) if spot_state.buy_ratio_5m else None,
            "sell_ratio": 1.0 - float(spot_state.buy_ratio_5m) if spot_state.buy_ratio_5m else None,
            "volume_5m": float(spot_state.volume_5m),
            "current_price": float(spot_state.last_price),
            "bid_volume": float(spot_state.bid_volume_20),
            "ask_volume": float(spot_state.ask_volume_20),
        }

        # Add L/S ratio if available
        if futures_state.current_ls_ratio:
            common_details["long_account_pct"] = float(futures_state.current_ls_ratio.long_account_pct)
            common_details["short_account_pct"] = float(futures_state.current_ls_ratio.short_account_pct)

        # VOLUME_SPIKE_HIGH detection
        if float(spot_state.volume_spike_ratio) > self._volume_threshold:
            details = {
                **common_details,
                "volume_spike_ratio": float(spot_state.volume_spike_ratio),
                "volume_1h": float(spot_state.volume_1h),
            }
            return {
                "detection_type": "VOLUME_SPIKE_HIGH",
                "timestamp": timestamp,
                "severity": "WARNING",
                "score": 60,
                "details": details,
            }

        # COORDINATED_BUYING detection
        if spot_state.buy_ratio_5m is not None and float(spot_state.buy_ratio_5m) > self._buy_threshold:
            details = {
                **common_details,
                "trade_count": spot_state.trade_count_5m,
            }
            return {
                "detection_type": "COORDINATED_BUYING",
                "timestamp": timestamp,
                "severity": "ALERT",
                "score": 70,
                "details": details,
            }

        # OI_SPIKE detection
        if abs(float(futures_state.oi_change_5m_pct)) > self._oi_threshold:
            details = {
                **common_details,
                "oi_change_5m": float(futures_state.oi_change_5m_pct),
            }
            return {
                "detection_type": "OI_SPIKE",
                "timestamp": timestamp,
                "severity": "WARNING",
                "score": 55,
                "details": details,
            }

        # PRICE_MOMENTUM detection
        if abs(float(spot_state.price_change_5m_pct)) > self._price_threshold:
            details = {
                **common_details,
                "price_change_5m": float(spot_state.price_change_5m_pct),
            }
            return {
                "detection_type": "PRICE_MOMENTUM",
                "timestamp": timestamp,
                "severity": "INFO",
                "score": 50,
                "details": details,
            }

        return None

    def _create_signal(
        self,
        symbol: str,
        accumulation: AccumulationSignal,
        spot_state: SymbolState,
        futures_state: FuturesState,
        timestamp: datetime,
        detection_type: Optional[str] = None,
    ) -> Optional[TradeSignal]:
        """Create a full TradeSignal from accumulation analysis."""
        # Calculate risk levels
        risk_levels = self.risk_calc.calculate(
            symbol=symbol,
            direction=accumulation.direction,
            current_price=spot_state.last_price,
            spot_state=spot_state,
            futures_state=futures_state,
            valid_hours=24,
            accumulation_score=accumulation.score.total,
        )

        # Check minimum R:R
        if risk_levels.risk_reward_ratio < self.signal_config.min_risk_reward:
            return None

        # Generate signal ID
        signal_id = self._generate_signal_id(symbol, timestamp, accumulation.direction)

        # Build details dict (matching signal_logger.py format for ML features)
        details = {
            "accumulation_score": accumulation.score.total,
            "orderbook_score": accumulation.score.orderbook_total,
            "oi_change_1h": float(futures_state.oi_change_1h_pct),
            "oi_change_5m": float(futures_state.oi_change_5m_pct),
            "volume_spike_ratio": float(spot_state.volume_spike_ratio),
            "volume_ratio": float(spot_state.volume_spike_ratio),  # alias
            "price_change_5m": float(spot_state.price_change_5m_pct),
            "atr_pct": float(spot_state.atr_1h_pct),
            # Orderbook data (ATR-based) - neutral for historical data
            "spot_bid_volume_atr": float(spot_state.bid_volume_atr),
            "spot_ask_volume_atr": float(spot_state.ask_volume_atr),
            "spot_imbalance_atr": float(spot_state.book_imbalance_atr) if spot_state.book_imbalance_atr else 0.0,
            "spot_atr_pct": float(spot_state.atr_1h_pct),
            "book_imbalance": float(spot_state.book_imbalance),
        }

        if futures_state.current_funding:
            details["funding_rate"] = float(futures_state.current_funding.funding_rate_percent)

        if futures_state.current_ls_ratio:
            details["long_short_ratio"] = float(futures_state.current_ls_ratio.long_short_ratio)
            details["long_pct"] = float(futures_state.current_ls_ratio.long_account_pct)
            details["short_pct"] = float(futures_state.current_ls_ratio.short_account_pct)

        if spot_state.buy_ratio_5m is not None:
            details["buy_ratio_5m"] = float(spot_state.buy_ratio_5m)

        # Build trigger detections list
        trigger_detections = []
        if detection_type:
            trigger_detections.append(detection_type)

        return TradeSignal(
            signal_id=signal_id,
            symbol=symbol,
            timestamp=timestamp,
            direction=accumulation.direction,
            signal_type=SignalType.ACCUMULATION,
            confidence=accumulation.confidence,
            probability=accumulation.probability,
            entry_zone_low=risk_levels.entry_zone_low,
            entry_zone_high=risk_levels.entry_zone_high,
            entry_limit=risk_levels.entry_limit,
            current_price=spot_state.last_price,
            stop_loss=risk_levels.stop_loss,
            stop_loss_pct=risk_levels.stop_loss_pct,
            take_profits=risk_levels.take_profits,
            risk_reward_ratio=risk_levels.risk_reward_ratio,
            valid_hours=24,
            evidence=accumulation.evidence,
            details=details,
            scenarios={
                "bullish": "Price breaks above entry zone with volume confirmation",
                "bearish": "Price breaks below stop loss, cut position",
            },
            trigger_detections=trigger_detections,
        )

    def _generate_signal_id(self, symbol: str, timestamp: datetime, direction: SignalDirection) -> str:
        """Generate unique signal ID using counter (faster than hashlib)."""
        self._signal_counter += 1
        ts = timestamp.strftime('%Y%m%d%H%M')
        return f"SIG-{symbol[:6]}-{ts}-{self._signal_counter}"


# =============================================================================
# STANDALONE TEST
# =============================================================================

if __name__ == "__main__":
    from data_downloader import BinanceHistoryDownloader

    print("SignalRunner - Test Run")
    print("=" * 60)

    # Download test data
    downloader = BinanceHistoryDownloader(cache_dir="cache")

    symbols = downloader.get_active_symbols(top_n=3)
    print(f"Test symbols: {symbols}")

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=2)

    print(f"Downloading data...")
    history = downloader.download_all(symbols, start_time, end_time)

    # Build state builder
    builder = StateBuilder(history)

    # Create config
    config = AppConfig(
        min_accumulation_score=50,  # Lower threshold for testing
        min_probability=50,
    )

    # Create output writer
    writer = OutputWriter(
        output_dir="output",
        max_signals_per_file=config.max_signals_per_file,
    )

    # Run signal generation
    runner = SignalRunner(
        downloader_data=history,
        state_builder=builder,
        output_writer=writer,
        config=config,
    )

    total = runner.run(
        symbols=symbols,
        start_time=start_time,
        end_time=end_time,
    )

    print(f"\nTest complete. Generated {total} signals.")
