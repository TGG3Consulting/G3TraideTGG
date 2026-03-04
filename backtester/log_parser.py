# -*- coding: utf-8 -*-
"""
ManipBackTester - Парсер логов сигналов.

Извлекает сигналы из logs/signals.jsonl.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Set, Tuple, Optional

from .models import ParsedSignal, TakeProfit, Direction, MLFeatures
from .config import BacktestConfig, SIGNALS_FILE


class LogParser:
    """
    Парсер логов сигналов BinanceFriend.

    Читает signals.jsonl и извлекает все торговые сигналы.
    """

    def __init__(self, config: BacktestConfig = None):
        self.config = config or BacktestConfig()
        self._signals: List[ParsedSignal] = []

    def parse_all_signals(self) -> List[ParsedSignal]:
        """
        Извлечь все сигналы из логов.

        Returns:
            Список сигналов, отсортированный по времени
        """
        signals_file = self.config.signals_file

        if not signals_file.exists():
            print(f"WARNING: Signals file not found: {signals_file}")
            return []

        signals = []
        errors = 0

        with open(signals_file, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    signal = self._parse_signal_record(data)
                    if signal:
                        signals.append(signal)
                except json.JSONDecodeError as e:
                    errors += 1
                    if self.config.verbose:
                        print(f"JSON error on line {line_num}: {e}")
                except Exception as e:
                    errors += 1
                    if self.config.verbose:
                        print(f"Parse error on line {line_num}: {e}")

        # Сортировать по времени
        signals.sort(key=lambda s: s.timestamp)

        self._signals = signals

        if self.config.verbose:
            print(f"Parsed {len(signals)} signals ({errors} errors)")

        return signals

    def _parse_signal_record(self, data: dict) -> Optional[ParsedSignal]:
        """
        Распарсить одну запись из JSONL.

        Формат записи (из signal_logger.py):
        {
            "log_version": "1.0",
            "logged_at": "...",
            "signal": {
                "signal_id": "...",
                "symbol": "BTCUSDT",
                "timestamp": "...",
                "direction": "LONG",
                "probability": 75,
                "entry_zone": {"low": "...", "high": "...", "limit": "..."},
                "current_price": "...",
                "stop_loss": "...",
                "stop_loss_pct": 5.2,
                "take_profits": [
                    {"label": "TP1", "price": "...", "percent": 4.3, "portion": 30},
                    ...
                ],
                "risk_reward": 2.5,
                "valid_hours": 24,
                ...
            },
            "accumulation_score": {...},
            ...
        }
        """
        # Извлечь signal object
        signal_data = data.get("signal")
        if not signal_data:
            return None

        # Обязательные поля
        symbol = signal_data.get("symbol")
        if not symbol:
            return None

        # Direction
        direction_str = signal_data.get("direction", "LONG")
        direction = Direction.LONG if direction_str == "LONG" else Direction.SHORT

        # Timestamp - normalize to UTC
        timestamp_str = signal_data.get("timestamp") or data.get("logged_at", "")
        timestamp = self._parse_timestamp(timestamp_str)

        # Entry zone
        entry_zone = signal_data.get("entry_zone", {})
        entry_limit = Decimal(str(entry_zone.get("limit", "0")))
        entry_zone_low = Decimal(str(entry_zone.get("low", "0")))
        entry_zone_high = Decimal(str(entry_zone.get("high", "0")))
        current_price = Decimal(str(signal_data.get("current_price", "0")))

        # Stop loss
        stop_loss = Decimal(str(signal_data.get("stop_loss", "0")))
        stop_loss_pct = float(signal_data.get("stop_loss_pct", 0))

        # Take profits
        take_profits_data = signal_data.get("take_profits", [])
        take_profits = []
        for tp_data in take_profits_data:
            tp = TakeProfit(
                label=tp_data.get("label", f"TP{len(take_profits)+1}"),
                price=Decimal(str(tp_data.get("price", "0"))),
                percent=float(tp_data.get("percent", 0)),
                portion=int(tp_data.get("portion", 0))
            )
            take_profits.append(tp)

        # Убедиться что есть 3 TP
        while len(take_profits) < 3:
            take_profits.append(TakeProfit(
                label=f"TP{len(take_profits)+1}",
                price=Decimal("0"),
                percent=0,
                portion=0
            ))

        # Accumulation score
        accumulation_score = data.get("accumulation_score", {})

        # ML Features - извлекаем ВСЕ данные
        ml_features = self._extract_ml_features(data, signal_data)

        return ParsedSignal(
            signal_id=signal_data.get("signal_id", ""),
            symbol=symbol,
            timestamp=timestamp,
            direction=direction,
            entry_limit=entry_limit,
            entry_zone_low=entry_zone_low,
            entry_zone_high=entry_zone_high,
            current_price=current_price,
            stop_loss=stop_loss,
            stop_loss_pct=stop_loss_pct,
            tp1=take_profits[0],
            tp2=take_profits[1],
            tp3=take_profits[2],
            risk_reward=float(signal_data.get("risk_reward", 0)),
            probability=int(signal_data.get("probability", 0)),
            confidence=signal_data.get("confidence", ""),
            signal_type=signal_data.get("signal_type", ""),
            max_hold_hours=int(signal_data.get("valid_hours", 24)),
            evidence=signal_data.get("evidence", []),
            details=signal_data.get("details", {}),
            accumulation_score=accumulation_score,
            ml_features=ml_features,
        )

    def _parse_timestamp(self, timestamp_str: str) -> datetime:
        """Parse timestamp string and normalize to UTC."""
        if not timestamp_str:
            return datetime.now(timezone.utc)

        # Handle Z suffix
        timestamp_str = timestamp_str.replace("Z", "+00:00")

        dt = datetime.fromisoformat(timestamp_str)

        # If naive (no timezone), assume UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt

    def _extract_ml_features(self, data: dict, signal_data: dict) -> MLFeatures:
        """
        Извлечь ВСЕ фичи для ML из записи.

        Источники данных:
        - data["accumulation_score"] - 22 компонента скоринга
        - data["futures_snapshot"] - OI, funding, LS ratio, price changes
        - data["spot_snapshot"] - spread, volume, orderbook, trades
        - data["trigger_detection"] - тип триггера, severity, score, details
        - data["config"] - конфигурация генерации сигналов
        - signal_data["details"] - агрегированные данные
        """
        features = MLFeatures()

        # Helper для безопасного извлечения float
        def safe_float(val, default=0.0):
            if val is None:
                return default
            try:
                return float(val)
            except (ValueError, TypeError):
                return default

        # Helper для безопасного извлечения int
        def safe_int(val, default=0):
            if val is None:
                return default
            try:
                return int(val)
            except (ValueError, TypeError):
                return default

        # Helper для парсинга строк вида "210.9x" или "+0.20"
        def parse_ratio_string(val, default=0.0):
            if val is None:
                return default
            if isinstance(val, (int, float)):
                return float(val)
            try:
                # Remove 'x' suffix and parse
                cleaned = str(val).replace('x', '').replace('+', '').replace('%', '').strip()
                return float(cleaned)
            except (ValueError, TypeError):
                return default

        # Helper для парсинга severity (строка "WARNING" -> число 2)
        def parse_severity(val, default=0):
            if val is None:
                return default
            if isinstance(val, int):
                return val
            severity_map = {
                "INFO": 1,
                "WARNING": 2,
                "ALERT": 3,
                "CRITICAL": 4,
            }
            return severity_map.get(str(val).upper(), default)

        # === 1. ACCUMULATION SCORE (22 components) ===
        acc = data.get("accumulation_score") or {}
        features.acc_oi_growth = safe_int(acc.get("oi_growth"))
        features.acc_oi_stability = safe_int(acc.get("oi_stability"))
        features.acc_funding_cheap = safe_int(acc.get("funding_cheap"))
        features.acc_funding_gradient = safe_int(acc.get("funding_gradient"))
        features.acc_crowd_bearish = safe_int(acc.get("crowd_bearish"))
        features.acc_crowd_bullish = safe_int(acc.get("crowd_bullish"))
        features.acc_coordinated_buying = safe_int(acc.get("coordinated_buying"))
        features.acc_volume_accumulation = safe_int(acc.get("volume_accumulation"))
        features.acc_cross_oi_migration = safe_int(acc.get("cross_oi_migration"))
        features.acc_cross_price_lead = safe_int(acc.get("cross_price_lead"))
        features.acc_spot_bid_pressure = safe_int(acc.get("spot_bid_pressure"))
        features.acc_spot_ask_weakness = safe_int(acc.get("spot_ask_weakness"))
        features.acc_spot_imbalance_score = safe_int(acc.get("spot_imbalance_score"))
        features.acc_futures_bid_pressure = safe_int(acc.get("futures_bid_pressure"))
        features.acc_futures_ask_weakness = safe_int(acc.get("futures_ask_weakness"))
        features.acc_futures_imbalance_score = safe_int(acc.get("futures_imbalance_score"))
        features.acc_orderbook_divergence = safe_int(acc.get("orderbook_divergence"))
        features.acc_orderbook_total = safe_int(acc.get("orderbook_total"))
        features.acc_wash_trading_penalty = safe_int(acc.get("wash_trading_penalty"))
        features.acc_extreme_funding_penalty = safe_int(acc.get("extreme_funding_penalty"))
        features.acc_orderbook_against_penalty = safe_int(acc.get("orderbook_against_penalty"))
        features.acc_total = safe_int(acc.get("total"))

        # === 2. FUTURES SNAPSHOT ===
        futures = data.get("futures_snapshot") or {}

        # OI (handle None)
        oi = futures.get("oi") or {}
        features.futures_oi_value = safe_float(oi.get("value"))
        features.futures_oi_value_usd = safe_float(oi.get("value_usd"))

        # OI changes (handle None)
        oi_changes = futures.get("oi_changes") or {}
        features.futures_oi_change_1m_pct = safe_float(oi_changes.get("1m_pct"))
        features.futures_oi_change_5m_pct = safe_float(oi_changes.get("5m_pct"))
        features.futures_oi_change_1h_pct = safe_float(oi_changes.get("1h_pct"))

        # Funding (handle None)
        funding = futures.get("funding") or {}
        features.futures_funding_rate = safe_float(funding.get("rate"))
        features.futures_funding_rate_pct = safe_float(funding.get("rate_pct"))
        features.futures_funding_mark_price = safe_float(funding.get("mark_price"))

        # Long/Short ratio (handle None)
        ls_ratio = futures.get("ls_ratio") or {}
        features.futures_long_account_pct = safe_float(ls_ratio.get("long_account_pct"))
        features.futures_short_account_pct = safe_float(ls_ratio.get("short_account_pct"))
        features.futures_long_short_ratio = safe_float(ls_ratio.get("long_short_ratio"))

        # Price changes (handle None)
        price_changes = futures.get("price_changes") or {}
        features.futures_price_change_5m_pct = safe_float(price_changes.get("5m_pct"))
        features.futures_price_change_1h_pct = safe_float(price_changes.get("1h_pct"))

        # === 3. SPOT SNAPSHOT ===
        spot = data.get("spot_snapshot") or {}

        # Price - ALL price data (handle None)
        price = spot.get("price") or {}
        features.spot_price_bid = safe_float(price.get("bid"))
        features.spot_price_ask = safe_float(price.get("ask"))
        features.spot_price_last = safe_float(price.get("last"))
        features.spot_price_mid = safe_float(price.get("mid"))
        features.spot_price_spread_pct = safe_float(price.get("spread_pct"))

        # Price changes (handle None)
        spot_price_changes = spot.get("price_changes") or {}
        features.spot_price_change_1m_pct = safe_float(spot_price_changes.get("1m_pct"))
        features.spot_price_change_5m_pct = safe_float(spot_price_changes.get("5m_pct"))
        features.spot_price_change_1h_pct = safe_float(spot_price_changes.get("1h_pct"))

        # Volume - ALL volume data (handle None)
        volume = spot.get("volume") or {}
        features.spot_volume_1m = safe_float(volume.get("1m"))
        features.spot_volume_5m = safe_float(volume.get("5m"))
        features.spot_volume_1h = safe_float(volume.get("1h"))
        features.spot_volume_avg_1h = safe_float(volume.get("avg_1h"))
        features.spot_volume_spike_ratio = safe_float(volume.get("spike_ratio"))

        # Orderbook - ALL orderbook data (handle None)
        orderbook = spot.get("orderbook") or {}
        features.spot_orderbook_bid_volume_20 = safe_float(orderbook.get("bid_volume_20"))
        features.spot_orderbook_ask_volume_20 = safe_float(orderbook.get("ask_volume_20"))
        features.spot_orderbook_imbalance = safe_float(orderbook.get("imbalance"))

        # Trades - ALL trades data (handle None)
        trades = spot.get("trades") or {}
        features.spot_trades_count_1m = safe_int(trades.get("count_1m"))
        features.spot_trades_count_5m = safe_int(trades.get("count_5m"))
        features.spot_trades_buy_ratio_5m = safe_float(trades.get("buy_ratio_5m"))

        # === 4. SIGNAL DETAILS ===
        details = signal_data.get("details") or {}
        features.signal_details_book_imbalance = parse_ratio_string(details.get("book_imbalance"))
        features.signal_details_volume_ratio = parse_ratio_string(details.get("volume_ratio"))
        features.signal_details_orderbook_score = safe_int(details.get("orderbook_score"))
        features.signal_details_spot_bid_volume_atr = safe_float(details.get("spot_bid_volume_atr"))
        features.signal_details_spot_ask_volume_atr = safe_float(details.get("spot_ask_volume_atr"))
        features.signal_details_spot_imbalance_atr = safe_float(details.get("spot_imbalance_atr"))
        features.signal_details_spot_atr_pct = safe_float(details.get("spot_atr_pct"))

        # === 5. TRIGGER DETECTION ===
        trigger = data.get("trigger_detection") or {}
        features.trigger_type = str(trigger.get("type", "") or "")
        features.trigger_severity = parse_severity(trigger.get("severity"))
        features.trigger_score = safe_int(trigger.get("score"))

        # Trigger details - ALL trigger details (handle None)
        trigger_details = trigger.get("details") or {}
        features.trigger_details_bid_volume = safe_float(trigger_details.get("bid_volume"))
        features.trigger_details_ask_volume = safe_float(trigger_details.get("ask_volume"))
        features.trigger_details_buy_ratio = safe_float(trigger_details.get("buy_ratio"))
        features.trigger_details_sell_ratio = safe_float(trigger_details.get("sell_ratio"))
        features.trigger_details_trades_count = safe_int(trigger_details.get("trades_count"))
        features.trigger_details_volume_5m = safe_float(trigger_details.get("volume_5m"))
        features.trigger_details_current_price = safe_float(trigger_details.get("current_price"))

        # === 6. CONFIG ===
        config = data.get("config") or {}
        features.config_min_accumulation_score = safe_int(config.get("min_accumulation_score"))
        features.config_min_probability = safe_int(config.get("min_probability"))
        features.config_min_risk_reward = safe_float(config.get("min_risk_reward"))
        features.config_default_sl_pct = safe_float(config.get("default_sl_pct"))
        features.config_tp1_ratio = safe_float(config.get("tp1_ratio"))
        features.config_tp2_ratio = safe_float(config.get("tp2_ratio"))
        features.config_tp3_ratio = safe_float(config.get("tp3_ratio"))

        # === 7. TRIGGER DETAILS (missing 2) ===
        features.trigger_details_long_account_pct = safe_float(trigger_details.get("long_account_pct"))
        features.trigger_details_short_account_pct = safe_float(trigger_details.get("short_account_pct"))

        # === 8. TIMESTAMPS ===
        logged_at = data.get("logged_at", "")
        if logged_at:
            try:
                dt = datetime.fromisoformat(logged_at.replace("Z", "+00:00"))
                features.signal_hour = dt.hour
                features.signal_minute = dt.minute
                features.signal_day_of_week = dt.weekday()
            except:
                pass

        # === 9. OI HISTORY (derived features) ===
        oi_history = futures.get("oi_history", [])
        if oi_history:
            oi_values = [safe_float(item.get("value")) for item in oi_history if isinstance(item, dict)]
            if oi_values:
                features.oi_history_count = len(oi_values)
                features.oi_history_first = oi_values[0]
                features.oi_history_last = oi_values[-1]
                features.oi_history_min = min(oi_values)
                features.oi_history_max = max(oi_values)
                features.oi_history_avg = sum(oi_values) / len(oi_values)
                # Standard deviation
                if len(oi_values) > 1:
                    mean = features.oi_history_avg
                    variance = sum((x - mean) ** 2 for x in oi_values) / len(oi_values)
                    features.oi_history_std = variance ** 0.5
                # Trend: (last - first) / first * 100
                if features.oi_history_first > 0:
                    features.oi_history_trend = (features.oi_history_last - features.oi_history_first) / features.oi_history_first * 100
                # Range percent: (max - min) / avg * 100
                if features.oi_history_avg > 0:
                    features.oi_history_range_pct = (features.oi_history_max - features.oi_history_min) / features.oi_history_avg * 100

        # === 10. FUNDING HISTORY (derived features) ===
        funding_history = futures.get("funding_history", [])
        if funding_history:
            funding_values = [safe_float(item.get("rate_pct")) for item in funding_history if isinstance(item, dict)]
            if funding_values:
                features.funding_history_count = len(funding_values)
                features.funding_history_first = funding_values[0]
                features.funding_history_last = funding_values[-1]
                features.funding_history_min = min(funding_values)
                features.funding_history_max = max(funding_values)
                features.funding_history_avg = sum(funding_values) / len(funding_values)
                # Standard deviation
                if len(funding_values) > 1:
                    mean = features.funding_history_avg
                    variance = sum((x - mean) ** 2 for x in funding_values) / len(funding_values)
                    features.funding_history_std = variance ** 0.5
                # Trend: last - first
                features.funding_history_trend = features.funding_history_last - features.funding_history_first

        # === 11. PRICE HISTORY ===
        price_history = spot.get("price_history", [])
        if price_history:
            features.price_history_count = len(price_history)
            if isinstance(price_history[0], (int, float)):
                features.price_history_first = safe_float(price_history[0])
                features.price_history_last = safe_float(price_history[-1])

        # === 12. TRIGGER DETECTIONS COUNT ===
        trigger_detections = signal_data.get("trigger_detections", [])
        features.trigger_detections_count = len(trigger_detections) if trigger_detections else 0

        # === 13. ADDITIONAL FIELDS (previously missing) ===
        # Entry zone boundaries
        entry_zone = signal_data.get("entry_zone") or {}
        features.entry_zone_low = safe_float(entry_zone.get("low"))
        features.entry_zone_high = safe_float(entry_zone.get("high"))

        # Scenarios
        scenarios = signal_data.get("scenarios") or {}
        features.scenario_bullish = str(scenarios.get("bullish", "") or "")
        features.scenario_bearish = str(scenarios.get("bearish", "") or "")

        # Evidence
        evidence = signal_data.get("evidence", [])
        if evidence:
            features.evidence_text = " | ".join(str(e) for e in evidence)
            features.evidence_count = len(evidence)

        # Meta timestamps
        features.logged_at = str(data.get("logged_at", "") or "")
        features.futures_last_update = str(futures.get("last_update", "") or "")
        features.spot_last_update = str(spot.get("last_update", "") or "")

        oi_data = futures.get("oi") or {}
        features.oi_timestamp = str(oi_data.get("timestamp", "") or "")

        funding_data = futures.get("funding") or {}
        features.funding_time = str(funding_data.get("funding_time", "") or "")

        ls_data = futures.get("ls_ratio") or {}
        features.ls_ratio_timestamp = str(ls_data.get("timestamp", "") or "")

        return features

    def get_unique_symbols(self) -> Set[str]:
        """Получить уникальные символы из сигналов."""
        return {s.symbol for s in self._signals}

    def get_time_range(self) -> Tuple[datetime, datetime]:
        """
        Получить временной диапазон сигналов.

        Returns:
            (первый_сигнал, последний_сигнал)
        """
        if not self._signals:
            raise ValueError("No signals parsed")
        return self._signals[0].timestamp, self._signals[-1].timestamp

    def get_signals_by_symbol(self, symbol: str) -> List[ParsedSignal]:
        """Получить сигналы для конкретного символа."""
        return [s for s in self._signals if s.symbol == symbol]

    def get_signals_by_direction(self, direction: Direction) -> List[ParsedSignal]:
        """Получить сигналы по направлению."""
        return [s for s in self._signals if s.direction == direction]

    def print_summary(self) -> None:
        """Вывести сводку по сигналам."""
        if not self._signals:
            print("No signals found")
            return

        symbols = self.get_unique_symbols()
        start, end = self.get_time_range()

        longs = len([s for s in self._signals if s.direction == Direction.LONG])
        shorts = len([s for s in self._signals if s.direction == Direction.SHORT])

        print("\n" + "=" * 50)
        print("SIGNAL LOG SUMMARY")
        print("=" * 50)
        print(f"Total signals:    {len(self._signals)}")
        print(f"Unique symbols:   {len(symbols)}")
        print(f"LONG signals:     {longs}")
        print(f"SHORT signals:    {shorts}")
        print(f"Time range:       {start.strftime('%Y-%m-%d %H:%M')} to {end.strftime('%Y-%m-%d %H:%M')}")
        print(f"Duration:         {(end - start).days} days")
        print("=" * 50)
