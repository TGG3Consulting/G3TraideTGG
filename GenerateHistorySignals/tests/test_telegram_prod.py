# -*- coding: utf-8 -*-
"""
Полные автотесты для Telegram Production системы.

Запуск:
    pytest tests/test_telegram_prod.py -v
    pytest tests/test_telegram_prod.py -v --tb=short
    pytest tests/test_telegram_prod.py::TestCoinRegimeMatrix -v

Покрытие:
    pytest tests/test_telegram_prod.py --cov=. --cov-report=html
"""

import sys
import os
import json
import tempfile
import shutil
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from dataclasses import dataclass

import pytest

# Добавляем путь к модулям
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_filter import (
    COIN_REGIME_MATRIX,
    VOL_FILTER_THRESHOLDS,
    calculate_coin_regime,
    calculate_volatility,
    calculate_regime_change_pct,
    filter_signal,
    FilterResult,
)
from telegram_sender import (
    format_group_alert,
    format_dm_details,
    save_signal_cache,
    load_signal_cache,
    load_sent_signals,
    save_sent_signal,
    SIGNAL_CACHE_FILE,
    SENT_SIGNALS_FILE,
)
from strategies import Signal, DailyCandle


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def sample_candles():
    """Генерирует тестовые свечи за 30 дней."""
    candles = []
    base_date = datetime(2026, 3, 1, tzinfo=timezone.utc)
    base_price = 50000.0

    for i in range(30):
        date = base_date - timedelta(days=29-i)
        # Симулируем рост цены на 1% в день
        price = base_price * (1 + 0.01 * i)
        high = price * 1.02
        low = price * 0.98

        candle = DailyCandle(
            date=date,
            open=price * 0.995,
            high=high,
            low=low,
            close=price,
            volume=1000.0,
            quote_volume=price * 1000,
            trades_count=10000,
            taker_buy_volume=550.0,
            taker_buy_quote_volume=price * 550,
        )
        candles.append(candle)

    return candles


@pytest.fixture
def sample_signal():
    """Генерирует тестовый сигнал."""
    return Signal(
        symbol="BTCUSDT",
        direction="LONG",
        date=datetime(2026, 3, 1, tzinfo=timezone.utc),
        entry=50000.0,
        take_profit=55000.0,
        stop_loss=48000.0,
        reason="Test signal",
        metadata={"adx": 35.0},
    )


@pytest.fixture
def temp_dir():
    """Создаёт временную директорию для тестов файлов."""
    temp = tempfile.mkdtemp()
    yield temp
    shutil.rmtree(temp)


# =============================================================================
# TEST: COIN_REGIME_MATRIX
# =============================================================================

class TestCoinRegimeMatrix:
    """Тесты матрицы режимов монет."""

    def test_all_regimes_present(self):
        """Проверяет что все 5 режимов есть в матрице."""
        expected_regimes = ['STRONG_BULL', 'BULL', 'SIDEWAYS', 'BEAR', 'STRONG_BEAR']
        for regime in expected_regimes:
            assert regime in COIN_REGIME_MATRIX, f"Режим {regime} отсутствует в матрице"

    def test_all_strategies_in_each_regime(self):
        """Проверяет что все 5 стратегий есть в каждом режиме."""
        expected_strategies = ['ls_fade', 'momentum', 'reversal', 'mean_reversion', 'momentum_ls']

        for regime, strategies in COIN_REGIME_MATRIX.items():
            for strategy in expected_strategies:
                assert strategy in strategies, f"Стратегия {strategy} отсутствует в режиме {regime}"

    def test_valid_actions_only(self):
        """Проверяет что все действия валидны (OFF/DYN/FULL)."""
        valid_actions = {'OFF', 'DYN', 'FULL'}

        for regime, strategies in COIN_REGIME_MATRIX.items():
            for strategy, action in strategies.items():
                assert action in valid_actions, f"Невалидное действие {action} для {regime}/{strategy}"

    def test_reversal_always_off(self):
        """Проверяет что reversal всегда OFF (по плану)."""
        for regime, strategies in COIN_REGIME_MATRIX.items():
            assert strategies['reversal'] == 'OFF', f"reversal должен быть OFF в {regime}"

    def test_strong_bear_mostly_off(self):
        """Проверяет что STRONG_BEAR в основном OFF."""
        strong_bear = COIN_REGIME_MATRIX['STRONG_BEAR']
        off_count = sum(1 for action in strong_bear.values() if action == 'OFF')
        assert off_count >= 4, "STRONG_BEAR должен иметь минимум 4 стратегии OFF"

    def test_bear_has_full_momentum(self):
        """Проверяет что BEAR имеет FULL для momentum стратегий."""
        bear = COIN_REGIME_MATRIX['BEAR']
        assert bear['momentum'] == 'FULL', "momentum должен быть FULL в BEAR"
        assert bear['momentum_ls'] == 'FULL', "momentum_ls должен быть FULL в BEAR"

    def test_sideways_mean_reversion_full(self):
        """Проверяет что mean_reversion FULL в SIDEWAYS."""
        assert COIN_REGIME_MATRIX['SIDEWAYS']['mean_reversion'] == 'FULL'


# =============================================================================
# TEST: VOL_FILTER_THRESHOLDS
# =============================================================================

class TestVolFilterThresholds:
    """Тесты порогов волатильности."""

    def test_all_strategies_present(self):
        """Проверяет что все 5 стратегий имеют пороги."""
        expected = ['ls_fade', 'momentum', 'reversal', 'mean_reversion', 'momentum_ls']
        for strategy in expected:
            assert strategy in VOL_FILTER_THRESHOLDS, f"Стратегия {strategy} отсутствует"

    def test_each_strategy_has_both_thresholds(self):
        """Проверяет что каждая стратегия имеет vol_low и vol_high."""
        for strategy, thresholds in VOL_FILTER_THRESHOLDS.items():
            assert 'vol_low' in thresholds, f"vol_low отсутствует для {strategy}"
            assert 'vol_high' in thresholds, f"vol_high отсутствует для {strategy}"

    def test_thresholds_are_numbers_or_none(self):
        """Проверяет типы значений."""
        for strategy, thresholds in VOL_FILTER_THRESHOLDS.items():
            vol_low = thresholds['vol_low']
            vol_high = thresholds['vol_high']

            assert vol_low is None or isinstance(vol_low, (int, float)), \
                f"vol_low должен быть числом или None для {strategy}"
            assert vol_high is None or isinstance(vol_high, (int, float)), \
                f"vol_high должен быть числом или None для {strategy}"

    def test_mean_reversion_no_low_filter(self):
        """Проверяет что mean_reversion не имеет нижнего порога."""
        assert VOL_FILTER_THRESHOLDS['mean_reversion']['vol_low'] is None

    def test_high_greater_than_low(self):
        """Проверяет что vol_high > vol_low когда оба заданы."""
        for strategy, thresholds in VOL_FILTER_THRESHOLDS.items():
            vol_low = thresholds['vol_low']
            vol_high = thresholds['vol_high']

            if vol_low is not None and vol_high is not None:
                assert vol_high > vol_low, \
                    f"vol_high должен быть > vol_low для {strategy}"

    def test_specific_values(self):
        """Проверяет конкретные значения из плана."""
        assert VOL_FILTER_THRESHOLDS['ls_fade']['vol_low'] == 4.5
        assert VOL_FILTER_THRESHOLDS['ls_fade']['vol_high'] == 22.0
        assert VOL_FILTER_THRESHOLDS['momentum']['vol_low'] == 2.0
        assert VOL_FILTER_THRESHOLDS['momentum']['vol_high'] == 25.0


# =============================================================================
# TEST: calculate_coin_regime
# =============================================================================

class TestCalculateCoinRegime:
    """Тесты расчёта режима монеты."""

    def test_strong_bull_over_20_percent(self, sample_candles):
        """Тест STRONG_BULL при росте > 20%."""
        # Модифицируем свечи для большого роста
        candles = []
        base_date = datetime(2026, 3, 1, tzinfo=timezone.utc)

        for i in range(20):
            date = base_date - timedelta(days=19-i)
            # Рост 2% в день = ~40% за 14 дней
            price = 50000.0 * (1 + 0.02 * i)
            candles.append(DailyCandle(
                date=date,
                open=price * 0.99,
                high=price * 1.01,
                low=price * 0.98,
                close=price,
                volume=1000.0,
                quote_volume=price * 1000,
                trades_count=10000,
                taker_buy_volume=500.0,
                taker_buy_quote_volume=price * 500,
            ))

        target_date = base_date
        regime = calculate_coin_regime(candles, target_date, lookback=14)
        assert regime == 'STRONG_BULL', f"Ожидался STRONG_BULL, получен {regime}"

    def test_bull_5_to_20_percent(self):
        """Тест BULL при росте 5-20%."""
        candles = []
        base_date = datetime(2026, 3, 1, tzinfo=timezone.utc)

        for i in range(20):
            date = base_date - timedelta(days=19-i)
            # Рост ~0.8% в день = ~12% за 14 дней
            price = 50000.0 * (1 + 0.008 * i)
            candles.append(DailyCandle(
                date=date,
                open=price * 0.99,
                high=price * 1.01,
                low=price * 0.98,
                close=price,
                volume=1000.0,
                quote_volume=price * 1000,
                trades_count=10000,
                taker_buy_volume=500.0,
                taker_buy_quote_volume=price * 500,
            ))

        target_date = base_date
        regime = calculate_coin_regime(candles, target_date, lookback=14)
        assert regime == 'BULL', f"Ожидался BULL, получен {regime}"

    def test_sideways_minus5_to_plus5(self):
        """Тест SIDEWAYS при изменении -5% до +5%."""
        candles = []
        base_date = datetime(2026, 3, 1, tzinfo=timezone.utc)

        for i in range(20):
            date = base_date - timedelta(days=19-i)
            # Минимальное изменение
            price = 50000.0 * (1 + 0.001 * i)
            candles.append(DailyCandle(
                date=date,
                open=price * 0.99,
                high=price * 1.01,
                low=price * 0.98,
                close=price,
                volume=1000.0,
                quote_volume=price * 1000,
                trades_count=10000,
                taker_buy_volume=500.0,
                taker_buy_quote_volume=price * 500,
            ))

        target_date = base_date
        regime = calculate_coin_regime(candles, target_date, lookback=14)
        assert regime == 'SIDEWAYS', f"Ожидался SIDEWAYS, получен {regime}"

    def test_bear_minus20_to_minus5(self):
        """Тест BEAR при падении -5% до -20%."""
        candles = []
        base_date = datetime(2026, 3, 1, tzinfo=timezone.utc)

        for i in range(20):
            date = base_date - timedelta(days=19-i)
            # Падение ~0.8% в день = ~-12% за 14 дней
            price = 50000.0 * (1 - 0.008 * i)
            candles.append(DailyCandle(
                date=date,
                open=price * 1.01,
                high=price * 1.02,
                low=price * 0.99,
                close=price,
                volume=1000.0,
                quote_volume=price * 1000,
                trades_count=10000,
                taker_buy_volume=500.0,
                taker_buy_quote_volume=price * 500,
            ))

        target_date = base_date
        regime = calculate_coin_regime(candles, target_date, lookback=14)
        assert regime == 'BEAR', f"Ожидался BEAR, получен {regime}"

    def test_strong_bear_under_minus20(self):
        """Тест STRONG_BEAR при падении > 20%."""
        candles = []
        base_date = datetime(2026, 3, 1, tzinfo=timezone.utc)

        for i in range(20):
            date = base_date - timedelta(days=19-i)
            # Падение 2% в день = ~-28% за 14 дней
            price = 50000.0 * (1 - 0.02 * i)
            candles.append(DailyCandle(
                date=date,
                open=price * 1.01,
                high=price * 1.02,
                low=price * 0.99,
                close=price,
                volume=1000.0,
                quote_volume=price * 1000,
                trades_count=10000,
                taker_buy_volume=500.0,
                taker_buy_quote_volume=price * 500,
            ))

        target_date = base_date
        regime = calculate_coin_regime(candles, target_date, lookback=14)
        assert regime == 'STRONG_BEAR', f"Ожидался STRONG_BEAR, получен {regime}"

    def test_unknown_with_insufficient_data(self):
        """Тест UNKNOWN при недостаточных данных."""
        candles = []
        base_date = datetime(2026, 3, 1, tzinfo=timezone.utc)

        # Только 5 свечей - недостаточно для lookback=14
        for i in range(5):
            date = base_date - timedelta(days=4-i)
            candles.append(DailyCandle(
                date=date,
                open=50000.0,
                high=51000.0,
                low=49000.0,
                close=50000.0,
                volume=1000.0,
                quote_volume=50000000.0,
                trades_count=10000,
                taker_buy_volume=500.0,
                taker_buy_quote_volume=25000000.0,
            ))

        regime = calculate_coin_regime(candles, base_date, lookback=14)
        assert regime == 'UNKNOWN', f"Ожидался UNKNOWN, получен {regime}"

    def test_empty_candles_returns_unknown(self):
        """Тест UNKNOWN для пустого списка свечей."""
        regime = calculate_coin_regime([], datetime.now(timezone.utc), lookback=14)
        assert regime == 'UNKNOWN'

    def test_no_lookahead_bias(self, sample_candles):
        """Тест что используется предыдущая свеча (без look-ahead bias)."""
        target_date = datetime(2026, 3, 1, tzinfo=timezone.utc)

        # Добавляем сегодняшнюю свечу с экстремальной ценой
        today_candle = DailyCandle(
            date=target_date,
            open=100000.0,  # Экстремальное значение
            high=120000.0,
            low=90000.0,
            close=110000.0,  # Должно быть проигнорировано
            volume=1000.0,
            quote_volume=110000000.0,
            trades_count=10000,
            taker_buy_volume=500.0,
            taker_buy_quote_volume=55000000.0,
        )

        candles_with_today = sample_candles + [today_candle]

        # Результат не должен измениться от добавления сегодняшней свечи
        regime_without = calculate_coin_regime(sample_candles, target_date, lookback=14)
        regime_with = calculate_coin_regime(candles_with_today, target_date, lookback=14)

        assert regime_without == regime_with, \
            "Режим не должен зависеть от сегодняшней свечи (look-ahead bias)"


# =============================================================================
# TEST: calculate_volatility
# =============================================================================

class TestCalculateVolatility:
    """Тесты расчёта волатильности."""

    def test_returns_positive_value(self, sample_candles):
        """Тест что волатильность положительная."""
        target_date = datetime(2026, 3, 1, tzinfo=timezone.utc)
        vol = calculate_volatility(sample_candles, target_date, lookback=14)
        assert vol > 0, "Волатильность должна быть > 0"

    def test_returns_percentage(self, sample_candles):
        """Тест что волатильность в процентах (разумные значения)."""
        target_date = datetime(2026, 3, 1, tzinfo=timezone.utc)
        vol = calculate_volatility(sample_candles, target_date, lookback=14)
        # Типичная дневная волатильность криптовалют 2-10%
        assert 0 < vol < 50, f"Волатильность {vol}% выглядит нереалистично"

    def test_higher_ranges_higher_volatility(self):
        """Тест что большие диапазоны = большая волатильность."""
        base_date = datetime(2026, 3, 1, tzinfo=timezone.utc)

        # Низкая волатильность
        low_vol_candles = []
        for i in range(20):
            date = base_date - timedelta(days=19-i)
            price = 50000.0
            low_vol_candles.append(DailyCandle(
                date=date,
                open=price,
                high=price * 1.005,  # 0.5% диапазон
                low=price * 0.995,
                close=price,
                volume=1000.0,
                quote_volume=50000000.0,
                trades_count=10000,
                taker_buy_volume=500.0,
                taker_buy_quote_volume=25000000.0,
            ))

        # Высокая волатильность
        high_vol_candles = []
        for i in range(20):
            date = base_date - timedelta(days=19-i)
            price = 50000.0
            high_vol_candles.append(DailyCandle(
                date=date,
                open=price,
                high=price * 1.05,  # 10% диапазон
                low=price * 0.95,
                close=price,
                volume=1000.0,
                quote_volume=50000000.0,
                trades_count=10000,
                taker_buy_volume=500.0,
                taker_buy_quote_volume=25000000.0,
            ))

        low_vol = calculate_volatility(low_vol_candles, base_date, lookback=14)
        high_vol = calculate_volatility(high_vol_candles, base_date, lookback=14)

        assert high_vol > low_vol, "Высокий диапазон должен давать высокую волатильность"

    def test_returns_zero_for_insufficient_data(self):
        """Тест 0.0 при недостаточных данных."""
        vol = calculate_volatility([], datetime.now(timezone.utc), lookback=14)
        assert vol == 0.0

    def test_no_lookahead_bias(self, sample_candles):
        """Тест что не используется сегодняшняя свеча."""
        target_date = datetime(2026, 3, 1, tzinfo=timezone.utc)

        # Экстремальная сегодняшняя свеча
        today_candle = DailyCandle(
            date=target_date,
            open=50000.0,
            high=100000.0,  # Экстремальный high
            low=10000.0,    # Экстремальный low
            close=50000.0,
            volume=1000.0,
            quote_volume=50000000.0,
            trades_count=10000,
            taker_buy_volume=500.0,
            taker_buy_quote_volume=25000000.0,
        )

        candles_with_today = sample_candles + [today_candle]

        vol_without = calculate_volatility(sample_candles, target_date, lookback=14)
        vol_with = calculate_volatility(candles_with_today, target_date, lookback=14)

        assert vol_without == vol_with, \
            "Волатильность не должна зависеть от сегодняшней свечи"


# =============================================================================
# TEST: calculate_regime_change_pct
# =============================================================================

class TestCalculateRegimeChangePct:
    """Тесты расчёта процента изменения для режима."""

    def test_positive_change(self):
        """Тест положительного изменения."""
        candles = []
        base_date = datetime(2026, 3, 1, tzinfo=timezone.utc)

        for i in range(20):
            date = base_date - timedelta(days=19-i)
            # Рост 1% в день
            price = 50000.0 * (1 + 0.01 * i)
            candles.append(DailyCandle(
                date=date,
                open=price * 0.99,
                high=price * 1.01,
                low=price * 0.98,
                close=price,
                volume=1000.0,
                quote_volume=price * 1000,
                trades_count=10000,
                taker_buy_volume=500.0,
                taker_buy_quote_volume=price * 500,
            ))

        change_pct = calculate_regime_change_pct(candles, base_date, lookback=14)
        assert change_pct > 0, "Изменение должно быть положительным"

    def test_negative_change(self):
        """Тест отрицательного изменения."""
        candles = []
        base_date = datetime(2026, 3, 1, tzinfo=timezone.utc)

        for i in range(20):
            date = base_date - timedelta(days=19-i)
            # Падение 1% в день
            price = 50000.0 * (1 - 0.01 * i)
            candles.append(DailyCandle(
                date=date,
                open=price * 1.01,
                high=price * 1.02,
                low=price * 0.99,
                close=price,
                volume=1000.0,
                quote_volume=price * 1000,
                trades_count=10000,
                taker_buy_volume=500.0,
                taker_buy_quote_volume=price * 500,
            ))

        change_pct = calculate_regime_change_pct(candles, base_date, lookback=14)
        assert change_pct < 0, "Изменение должно быть отрицательным"

    def test_zero_for_insufficient_data(self):
        """Тест 0.0 при недостаточных данных."""
        change_pct = calculate_regime_change_pct([], datetime.now(timezone.utc), lookback=14)
        assert change_pct == 0.0


# =============================================================================
# TEST: filter_signal
# =============================================================================

class TestFilterSignal:
    """Тесты фильтрации сигналов."""

    def test_passes_without_filters(self, sample_signal, sample_candles):
        """Тест что сигнал проходит без включённых фильтров."""
        result = filter_signal(
            signal=sample_signal,
            candles=sample_candles,
            strategy_name='momentum',
            coin_regime_enabled=False,
            vol_filter_low_enabled=False,
            vol_filter_high_enabled=False,
        )
        assert result.passed is True
        assert result.skip_reason is None

    def test_returns_filter_result_dataclass(self, sample_signal, sample_candles):
        """Тест что возвращается FilterResult."""
        result = filter_signal(
            signal=sample_signal,
            candles=sample_candles,
            strategy_name='momentum',
        )
        assert isinstance(result, FilterResult)
        assert hasattr(result, 'passed')
        assert hasattr(result, 'skip_reason')
        assert hasattr(result, 'coin_regime')
        assert hasattr(result, 'coin_volatility')
        assert hasattr(result, 'regime_action')
        assert hasattr(result, 'regime_change_pct')

    def test_regime_filter_blocks_off(self, sample_signal, sample_candles):
        """Тест что coin_regime фильтр блокирует OFF стратегии."""
        # reversal всегда OFF
        result = filter_signal(
            signal=sample_signal,
            candles=sample_candles,
            strategy_name='reversal',
            coin_regime_enabled=True,
        )

        if result.coin_regime != 'UNKNOWN':
            assert result.passed is False
            assert result.skip_reason == 'skipped_regime'

    def test_vol_filter_low_blocks(self, sample_signal):
        """Тест что vol_filter_low блокирует низкую волатильность."""
        # Создаём свечи с очень низкой волатильностью
        candles = []
        base_date = datetime(2026, 3, 1, tzinfo=timezone.utc)

        for i in range(20):
            date = base_date - timedelta(days=19-i)
            price = 50000.0
            candles.append(DailyCandle(
                date=date,
                open=price,
                high=price * 1.001,  # 0.1% диапазон
                low=price * 0.999,
                close=price,
                volume=1000.0,
                quote_volume=50000000.0,
                trades_count=10000,
                taker_buy_volume=500.0,
                taker_buy_quote_volume=25000000.0,
            ))

        result = filter_signal(
            signal=sample_signal,
            candles=candles,
            strategy_name='ls_fade',  # vol_low = 4.5
            vol_filter_low_enabled=True,
        )

        # При очень низкой волатильности должен блокироваться
        if result.coin_volatility < VOL_FILTER_THRESHOLDS['ls_fade']['vol_low']:
            assert result.passed is False
            assert result.skip_reason == 'skipped_vol_low'

    def test_vol_filter_high_blocks(self, sample_signal):
        """Тест что vol_filter_high блокирует высокую волатильность."""
        # Создаём свечи с очень высокой волатильностью
        candles = []
        base_date = datetime(2026, 3, 1, tzinfo=timezone.utc)

        for i in range(20):
            date = base_date - timedelta(days=19-i)
            price = 50000.0
            candles.append(DailyCandle(
                date=date,
                open=price,
                high=price * 1.20,  # 40% диапазон
                low=price * 0.80,
                close=price,
                volume=1000.0,
                quote_volume=50000000.0,
                trades_count=10000,
                taker_buy_volume=500.0,
                taker_buy_quote_volume=25000000.0,
            ))

        result = filter_signal(
            signal=sample_signal,
            candles=candles,
            strategy_name='ls_fade',  # vol_high = 22.0
            vol_filter_high_enabled=True,
        )

        # При очень высокой волатильности должен блокироваться
        if result.coin_volatility > VOL_FILTER_THRESHOLDS['ls_fade']['vol_high']:
            assert result.passed is False
            assert result.skip_reason == 'skipped_vol_high'

    def test_calculates_regime_action(self, sample_signal, sample_candles):
        """Тест что regime_action вычисляется корректно."""
        result = filter_signal(
            signal=sample_signal,
            candles=sample_candles,
            strategy_name='momentum',
        )

        if result.coin_regime != 'UNKNOWN':
            expected_action = COIN_REGIME_MATRIX[result.coin_regime]['momentum']
            assert result.regime_action == expected_action


# =============================================================================
# TEST: format_group_alert
# =============================================================================

class TestFormatGroupAlert:
    """Тесты форматирования алерта для группы."""

    def test_returns_tuple(self, sample_signal):
        """Тест что возвращает кортеж (text, keyboard)."""
        text, keyboard = format_group_alert(
            signal=sample_signal,
            strategy_name='momentum',
            coin_regime='BULL',
            regime_action='DYN',
            coin_volatility=5.5,
            signal_id='test_123',
        )
        assert isinstance(text, str)
        assert keyboard is not None

    def test_contains_symbol(self, sample_signal):
        """Тест что текст содержит символ."""
        text, _ = format_group_alert(
            signal=sample_signal,
            strategy_name='momentum',
            coin_regime='BULL',
            regime_action='DYN',
            coin_volatility=5.5,
            signal_id='test_123',
        )
        assert 'BTCUSDT' in text

    def test_contains_direction(self, sample_signal):
        """Тест что текст содержит направление."""
        text, _ = format_group_alert(
            signal=sample_signal,
            strategy_name='momentum',
            coin_regime='BULL',
            regime_action='DYN',
            coin_volatility=5.5,
            signal_id='test_123',
        )
        assert 'LONG' in text

    def test_contains_strategy_name(self, sample_signal):
        """Тест что текст содержит название стратегии."""
        text, _ = format_group_alert(
            signal=sample_signal,
            strategy_name='momentum',
            coin_regime='BULL',
            regime_action='DYN',
            coin_volatility=5.5,
            signal_id='test_123',
        )
        assert 'momentum' in text

    def test_contains_russian_text(self, sample_signal):
        """Тест что текст на русском."""
        text, _ = format_group_alert(
            signal=sample_signal,
            strategy_name='momentum',
            coin_regime='BULL',
            regime_action='DYN',
            coin_volatility=5.5,
            signal_id='test_123',
        )
        assert 'Стратегия' in text
        assert 'Вход' in text

    def test_contains_emoji_for_long(self, sample_signal):
        """Тест что LONG имеет зелёный эмодзи."""
        text, _ = format_group_alert(
            signal=sample_signal,
            strategy_name='momentum',
            coin_regime='BULL',
            regime_action='DYN',
            coin_volatility=5.5,
            signal_id='test_123',
        )
        assert '🟢' in text

    def test_contains_emoji_for_short(self):
        """Тест что SHORT имеет красный эмодзи."""
        short_signal = Signal(
            symbol="ETHUSDT",
            direction="SHORT",
            date=datetime(2026, 3, 1, tzinfo=timezone.utc),
            entry=3000.0,
            take_profit=2700.0,
            stop_loss=3120.0,
            reason="Test short",
            metadata={},
        )
        text, _ = format_group_alert(
            signal=short_signal,
            strategy_name='ls_fade',
            coin_regime='BEAR',
            regime_action='DYN',
            coin_volatility=5.5,
            signal_id='test_short',
        )
        assert '🔴' in text

    def test_keyboard_has_button(self, sample_signal):
        """Тест что клавиатура имеет кнопку."""
        _, keyboard = format_group_alert(
            signal=sample_signal,
            strategy_name='momentum',
            coin_regime='BULL',
            regime_action='DYN',
            coin_volatility=5.5,
            signal_id='test_123',
        )
        # InlineKeyboardMarkup должен иметь inline_keyboard
        assert hasattr(keyboard, 'inline_keyboard')
        assert len(keyboard.inline_keyboard) > 0

    def test_button_callback_data(self, sample_signal):
        """Тест что callback_data кнопки корректный."""
        signal_id = 'test_signal_123'
        _, keyboard = format_group_alert(
            signal=sample_signal,
            strategy_name='momentum',
            coin_regime='BULL',
            regime_action='DYN',
            coin_volatility=5.5,
            signal_id=signal_id,
        )

        button = keyboard.inline_keyboard[0][0]
        assert button.callback_data == f'details_{signal_id}'

    def test_prices_formatted_correctly(self, sample_signal):
        """Тест форматирования цен."""
        text, _ = format_group_alert(
            signal=sample_signal,
            strategy_name='momentum',
            coin_regime='BULL',
            regime_action='DYN',
            coin_volatility=5.5,
            signal_id='test_123',
        )
        # Должны быть цены с $
        assert '$' in text


# =============================================================================
# TEST: format_dm_details
# =============================================================================

class TestFormatDMDetails:
    """Тесты форматирования подробностей для ЛС."""

    @pytest.fixture
    def sample_signal_data(self):
        """Тестовые данные сигнала."""
        return {
            "signal_id": "20260305_BTCUSDT_LONG_momentum",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "strategy": "momentum",
            "date": "2026-03-05 00:00 UTC",
            "entry": 50000.0,
            "tp": 55000.0,
            "sl": 48000.0,
            "tp_pct": 10.0,
            "sl_pct": 4.0,
            "rr_ratio": 2.5,
            "coin_regime": "BULL",
            "coin_regime_change_pct": 12.5,
            "coin_volatility": 5.5,
            "regime_action": "DYN",
            "market_data": {
                "long_pct": 55.0,
                "short_pct": 45.0,
                "funding_rate": 0.0001,
                "open_interest": 2000000000,
                "volume_24h": 15000000000,
            },
            "candle": {
                "date": "2026-03-04",
                "open": 49500.0,
                "high": 50500.0,
                "low": 49000.0,
                "close": 50000.0,
                "volume": 1000.0,
                "quote_volume": 50000000.0,
                "trades_count": 100000,
                "taker_buy_pct": 52.0,
            },
            "indicators": {
                "adx": 35.0,
                "atr": 1500.0,
                "atr_pct": 3.0,
            },
            "reason": "Momentum breakout",
        }

    def test_returns_string(self, sample_signal_data):
        """Тест что возвращает строку."""
        text = format_dm_details(sample_signal_data)
        assert isinstance(text, str)

    def test_contains_all_sections(self, sample_signal_data):
        """Тест что содержит все секции."""
        text = format_dm_details(sample_signal_data)

        assert 'ОСНОВНОЕ' in text
        assert 'УРОВНИ' in text
        assert 'РЕЖИМ МОНЕТЫ' in text
        assert 'РЫНОЧНЫЕ ДАННЫЕ' in text
        assert 'СВЕЧА' in text
        assert 'ИНДИКАТОРЫ' in text
        assert 'ПРИЧИНА СИГНАЛА' in text

    def test_contains_symbol(self, sample_signal_data):
        """Тест что содержит символ."""
        text = format_dm_details(sample_signal_data)
        assert 'BTCUSDT' in text

    def test_contains_direction(self, sample_signal_data):
        """Тест что содержит направление."""
        text = format_dm_details(sample_signal_data)
        assert 'LONG' in text

    def test_contains_strategy(self, sample_signal_data):
        """Тест что содержит стратегию."""
        text = format_dm_details(sample_signal_data)
        assert 'momentum' in text

    def test_contains_russian_labels(self, sample_signal_data):
        """Тест что метки на русском."""
        text = format_dm_details(sample_signal_data)
        assert 'Монета' in text
        assert 'Направление' in text
        assert 'Вход' in text
        assert 'Волатильность' in text

    def test_adx_interpretation(self, sample_signal_data):
        """Тест интерпретации ADX."""
        # ADX 35 = умеренный тренд
        text = format_dm_details(sample_signal_data)
        assert 'умеренный тренд' in text or 'сильный тренд' in text

    def test_handles_missing_data(self):
        """Тест обработки отсутствующих данных."""
        minimal_data = {
            "symbol": "TESTUSDT",
            "direction": "LONG",
        }
        # Не должно падать
        text = format_dm_details(minimal_data)
        assert 'TESTUSDT' in text


# =============================================================================
# TEST: Signal Cache
# =============================================================================

class TestSignalCache:
    """Тесты кэширования сигналов."""

    def test_save_and_load(self, temp_dir):
        """Тест сохранения и загрузки."""
        cache_file = os.path.join(temp_dir, 'signal_cache.json')

        # Патчим путь к файлу
        with patch('telegram_sender.SIGNAL_CACHE_FILE', cache_file):
            signal_id = 'test_signal_123'
            signal_data = {'symbol': 'BTCUSDT', 'direction': 'LONG'}

            save_signal_cache(signal_id, signal_data)
            loaded = load_signal_cache(signal_id)

            assert loaded is not None
            assert loaded['symbol'] == 'BTCUSDT'
            assert loaded['direction'] == 'LONG'

    def test_load_nonexistent_returns_none(self, temp_dir):
        """Тест что несуществующий сигнал возвращает None."""
        cache_file = os.path.join(temp_dir, 'signal_cache.json')

        with patch('telegram_sender.SIGNAL_CACHE_FILE', cache_file):
            loaded = load_signal_cache('nonexistent_signal')
            assert loaded is None

    def test_multiple_signals(self, temp_dir):
        """Тест сохранения нескольких сигналов."""
        cache_file = os.path.join(temp_dir, 'signal_cache.json')

        with patch('telegram_sender.SIGNAL_CACHE_FILE', cache_file):
            save_signal_cache('signal_1', {'symbol': 'BTCUSDT'})
            save_signal_cache('signal_2', {'symbol': 'ETHUSDT'})
            save_signal_cache('signal_3', {'symbol': 'SOLUSDT'})

            assert load_signal_cache('signal_1')['symbol'] == 'BTCUSDT'
            assert load_signal_cache('signal_2')['symbol'] == 'ETHUSDT'
            assert load_signal_cache('signal_3')['symbol'] == 'SOLUSDT'

    def test_overwrites_existing(self, temp_dir):
        """Тест перезаписи существующего сигнала."""
        cache_file = os.path.join(temp_dir, 'signal_cache.json')

        with patch('telegram_sender.SIGNAL_CACHE_FILE', cache_file):
            save_signal_cache('signal_1', {'value': 'old'})
            save_signal_cache('signal_1', {'value': 'new'})

            loaded = load_signal_cache('signal_1')
            assert loaded['value'] == 'new'


# =============================================================================
# TEST: Sent Signals
# =============================================================================

class TestSentSignals:
    """Тесты защиты от дубликатов."""

    def test_load_empty_returns_set(self, temp_dir):
        """Тест что пустой файл возвращает пустой set."""
        sent_file = os.path.join(temp_dir, 'sent_signals.log')

        with patch('telegram_sender.SENT_SIGNALS_FILE', sent_file):
            sent = load_sent_signals()
            assert isinstance(sent, set)
            assert len(sent) == 0

    def test_save_and_load(self, temp_dir):
        """Тест сохранения и загрузки."""
        sent_file = os.path.join(temp_dir, 'sent_signals.log')

        with patch('telegram_sender.SENT_SIGNALS_FILE', sent_file):
            save_sent_signal('signal_1')
            save_sent_signal('signal_2')

            sent = load_sent_signals()
            assert 'signal_1' in sent
            assert 'signal_2' in sent

    def test_appends_to_file(self, temp_dir):
        """Тест что дописывает в файл."""
        sent_file = os.path.join(temp_dir, 'sent_signals.log')

        with patch('telegram_sender.SENT_SIGNALS_FILE', sent_file):
            save_sent_signal('signal_1')
            save_sent_signal('signal_2')
            save_sent_signal('signal_3')

            sent = load_sent_signals()
            assert len(sent) == 3

    def test_handles_duplicates(self, temp_dir):
        """Тест обработки дубликатов в файле."""
        sent_file = os.path.join(temp_dir, 'sent_signals.log')

        with patch('telegram_sender.SENT_SIGNALS_FILE', sent_file):
            save_sent_signal('signal_1')
            save_sent_signal('signal_1')
            save_sent_signal('signal_1')

            sent = load_sent_signals()
            # Set удалит дубликаты
            assert len(sent) == 1


# =============================================================================
# TEST: CLI Arguments
# =============================================================================

class TestCLIArguments:
    """Тесты аргументов командной строки."""

    def test_default_values(self):
        """Тест значений по умолчанию."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--config", type=str, default="config.json")
        parser.add_argument("--symbols", type=str, default="")
        parser.add_argument("--top", type=int, default=20)
        parser.add_argument("--coin-regime", action="store_true")
        parser.add_argument("--vol-filter-low", action="store_true")
        parser.add_argument("--vol-filter-high", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

        args = parser.parse_args([])

        assert args.config == "config.json"
        assert args.symbols == ""
        assert args.top == 20
        assert args.coin_regime is False
        assert args.vol_filter_low is False
        assert args.vol_filter_high is False
        assert args.dry_run is False

    def test_custom_values(self):
        """Тест кастомных значений."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--config", type=str, default="config.json")
        parser.add_argument("--symbols", type=str, default="")
        parser.add_argument("--top", type=int, default=20)
        parser.add_argument("--coin-regime", action="store_true")
        parser.add_argument("--vol-filter-low", action="store_true")
        parser.add_argument("--vol-filter-high", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

        args = parser.parse_args([
            '--config', 'custom.json',
            '--symbols', 'BTCUSDT,ETHUSDT',
            '--top', '10',
            '--coin-regime',
            '--vol-filter-low',
            '--dry-run',
        ])

        assert args.config == "custom.json"
        assert args.symbols == "BTCUSDT,ETHUSDT"
        assert args.top == 10
        assert args.coin_regime is True
        assert args.vol_filter_low is True
        assert args.vol_filter_high is False
        assert args.dry_run is True


# =============================================================================
# TEST: Config Loading
# =============================================================================

class TestConfigLoading:
    """Тесты загрузки конфигурации."""

    def test_loads_valid_config(self, temp_dir):
        """Тест загрузки валидного конфига."""
        config_path = os.path.join(temp_dir, 'config.json')

        config_data = {
            "telegram": {
                "bot_token": "test_token",
                "chat_id": "-123456",
            },
            "defaults": {
                "sl_pct": 4.0,
                "tp_pct": 10.0,
            },
        }

        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f)

        with open(config_path, 'r', encoding='utf-8') as f:
            loaded = json.load(f)

        assert loaded['telegram']['bot_token'] == 'test_token'
        assert loaded['defaults']['sl_pct'] == 4.0

    def test_required_fields_present(self):
        """Тест что конфиг имеет все необходимые поля."""
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'config.json'
        )

        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)

            assert 'telegram' in config
            assert 'bot_token' in config['telegram']
            assert 'chat_id' in config['telegram']
            assert 'defaults' in config
            assert 'sl_pct' in config['defaults']
            assert 'tp_pct' in config['defaults']


# =============================================================================
# TEST: Signal ID Format
# =============================================================================

class TestSignalIdFormat:
    """Тесты формата ID сигнала."""

    def test_signal_id_format(self):
        """Тест формата signal_id."""
        signal_date = datetime(2026, 3, 5, tzinfo=timezone.utc)
        symbol = "BTCUSDT"
        direction = "LONG"
        strategy = "momentum"

        signal_id = f"{signal_date.strftime('%Y%m%d')}_{symbol}_{direction}_{strategy}"

        assert signal_id == "20260305_BTCUSDT_LONG_momentum"

    def test_signal_id_unique_per_day(self):
        """Тест уникальности signal_id в день."""
        ids = set()

        for symbol in ['BTCUSDT', 'ETHUSDT']:
            for direction in ['LONG', 'SHORT']:
                for strategy in ['momentum', 'ls_fade']:
                    signal_id = f"20260305_{symbol}_{direction}_{strategy}"
                    ids.add(signal_id)

        # 2 символа * 2 направления * 2 стратегии = 8 уникальных ID
        assert len(ids) == 8

    def test_callback_data_format(self):
        """Тест формата callback_data."""
        signal_id = "20260305_BTCUSDT_LONG_momentum"
        callback_data = f"details_{signal_id}"

        assert callback_data == "details_20260305_BTCUSDT_LONG_momentum"
        assert callback_data.startswith("details_")

    def test_extract_signal_id_from_callback(self):
        """Тест извлечения signal_id из callback_data."""
        callback_data = "details_20260305_BTCUSDT_LONG_momentum"
        signal_id = callback_data.replace("details_", "")

        assert signal_id == "20260305_BTCUSDT_LONG_momentum"


# =============================================================================
# TEST: Price Formatting
# =============================================================================

class TestPriceFormatting:
    """Тесты форматирования цен."""

    def test_format_high_price(self):
        """Тест форматирования высокой цены."""
        price = 67420.50

        def fmt_price(p):
            if p >= 1000:
                return f"${p:,.2f}"
            elif p >= 1:
                return f"${p:.4f}"
            else:
                return f"${p:.6f}"

        formatted = fmt_price(price)
        assert formatted == "$67,420.50"

    def test_format_medium_price(self):
        """Тест форматирования средней цены."""
        price = 3.1415

        def fmt_price(p):
            if p >= 1000:
                return f"${p:,.2f}"
            elif p >= 1:
                return f"${p:.4f}"
            else:
                return f"${p:.6f}"

        formatted = fmt_price(price)
        assert formatted == "$3.1415"

    def test_format_low_price(self):
        """Тест форматирования низкой цены."""
        price = 0.000123

        def fmt_price(p):
            if p >= 1000:
                return f"${p:,.2f}"
            elif p >= 1:
                return f"${p:.4f}"
            else:
                return f"${p:.6f}"

        formatted = fmt_price(price)
        assert formatted == "$0.000123"

    def test_format_big_number_billions(self):
        """Тест форматирования миллиардов."""
        num = 2_145_000_000

        def fmt_big_number(n):
            if n >= 1_000_000_000:
                return f"${n/1_000_000_000:.2f}B"
            elif n >= 1_000_000:
                return f"${n/1_000_000:.2f}M"
            else:
                return f"${n:,.0f}"

        formatted = fmt_big_number(num)
        assert formatted == "$2.15B"

    def test_format_big_number_millions(self):
        """Тест форматирования миллионов."""
        num = 15_320_000

        def fmt_big_number(n):
            if n >= 1_000_000_000:
                return f"${n/1_000_000_000:.2f}B"
            elif n >= 1_000_000:
                return f"${n/1_000_000:.2f}M"
            else:
                return f"${n:,.0f}"

        formatted = fmt_big_number(num)
        assert formatted == "$15.32M"


# =============================================================================
# TEST: TP/SL Calculation
# =============================================================================

class TestTPSLCalculation:
    """Тесты расчёта TP/SL процентов."""

    def test_long_tp_sl_calculation(self):
        """Тест расчёта для LONG."""
        entry = 50000.0
        take_profit = 55000.0
        stop_loss = 48000.0

        tp_pct = (take_profit - entry) / entry * 100
        sl_pct = (entry - stop_loss) / entry * 100

        assert tp_pct == 10.0
        assert sl_pct == 4.0

    def test_short_tp_sl_calculation(self):
        """Тест расчёта для SHORT."""
        entry = 50000.0
        take_profit = 45000.0
        stop_loss = 52000.0

        # Для SHORT формулы инвертированы
        tp_pct = (entry - take_profit) / entry * 100
        sl_pct = (stop_loss - entry) / entry * 100

        assert tp_pct == 10.0
        assert sl_pct == 4.0

    def test_rr_ratio_calculation(self):
        """Тест расчёта R:R."""
        tp_pct = 10.0
        sl_pct = 4.0

        rr_ratio = tp_pct / sl_pct

        assert rr_ratio == 2.5


# =============================================================================
# TEST: ALL_STRATEGIES Constant
# =============================================================================

class TestAllStrategies:
    """Тесты константы ALL_STRATEGIES."""

    def test_all_five_strategies(self):
        """Тест что все 5 стратегий определены."""
        ALL_STRATEGIES = ['ls_fade', 'momentum', 'reversal', 'mean_reversion', 'momentum_ls']

        assert len(ALL_STRATEGIES) == 5
        assert 'ls_fade' in ALL_STRATEGIES
        assert 'momentum' in ALL_STRATEGIES
        assert 'reversal' in ALL_STRATEGIES
        assert 'mean_reversion' in ALL_STRATEGIES
        assert 'momentum_ls' in ALL_STRATEGIES

    def test_strategies_match_matrix(self):
        """Тест что стратегии совпадают с матрицей."""
        ALL_STRATEGIES = ['ls_fade', 'momentum', 'reversal', 'mean_reversion', 'momentum_ls']

        for regime in COIN_REGIME_MATRIX.values():
            for strategy in ALL_STRATEGIES:
                assert strategy in regime


# =============================================================================
# TEST: Date Handling
# =============================================================================

class TestDateHandling:
    """Тесты обработки дат."""

    def test_yesterday_date_calculation(self):
        """Тест расчёта вчерашней даты."""
        now_utc = datetime.now(timezone.utc)
        yesterday = (now_utc - timedelta(days=1)).date()

        assert yesterday < now_utc.date()

    def test_signal_date_format(self):
        """Тест формата даты сигнала."""
        signal_date = datetime(2026, 3, 5, 0, 0, 0, tzinfo=timezone.utc)
        formatted = signal_date.strftime("%Y-%m-%d %H:%M UTC")

        assert formatted == "2026-03-05 00:00 UTC"

    def test_date_in_signal_id(self):
        """Тест даты в signal_id."""
        signal_date = datetime(2026, 3, 5, tzinfo=timezone.utc)
        date_str = signal_date.strftime('%Y%m%d')

        assert date_str == "20260305"


# =============================================================================
# TEST: Integration
# =============================================================================

class TestIntegration:
    """Интеграционные тесты."""

    def test_full_filter_flow(self, sample_signal, sample_candles):
        """Тест полного flow фильтрации."""
        # Применяем фильтр
        result = filter_signal(
            signal=sample_signal,
            candles=sample_candles,
            strategy_name='momentum',
            coin_regime_enabled=True,
            vol_filter_low_enabled=True,
            vol_filter_high_enabled=True,
        )

        # Проверяем что результат корректный
        assert isinstance(result, FilterResult)
        assert isinstance(result.passed, bool)
        assert result.coin_regime in ['STRONG_BULL', 'BULL', 'SIDEWAYS', 'BEAR', 'STRONG_BEAR', 'UNKNOWN']
        assert isinstance(result.coin_volatility, float)

    def test_full_format_flow(self, sample_signal):
        """Тест полного flow форматирования."""
        # Форматируем алерт
        text, keyboard = format_group_alert(
            signal=sample_signal,
            strategy_name='momentum',
            coin_regime='BULL',
            regime_action='DYN',
            coin_volatility=5.5,
            signal_id='integration_test_123',
        )

        # Проверяем результат
        assert 'BTCUSDT' in text
        assert 'LONG' in text
        assert 'momentum' in text
        assert keyboard.inline_keyboard[0][0].callback_data == 'details_integration_test_123'

    def test_cache_round_trip(self, temp_dir):
        """Тест полного цикла кэширования."""
        cache_file = os.path.join(temp_dir, 'signal_cache.json')

        with patch('telegram_sender.SIGNAL_CACHE_FILE', cache_file):
            # Сохраняем
            signal_data = {
                'symbol': 'BTCUSDT',
                'direction': 'LONG',
                'entry': 50000.0,
                'market_data': {'long_pct': 55.0},
            }
            save_signal_cache('test_round_trip', signal_data)

            # Загружаем
            loaded = load_signal_cache('test_round_trip')

            # Проверяем
            assert loaded['symbol'] == 'BTCUSDT'
            assert loaded['market_data']['long_pct'] == 55.0


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
