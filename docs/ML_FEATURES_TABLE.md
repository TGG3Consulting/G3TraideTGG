# ML Features Table

| № | Параметр | Был | Описание | ML (0-20) |
|---|----------|-----|----------|-----------|
| **BASIC (9)** |
| 1 | Prob | ✓ | Вероятность сигнала (%) из скоринга | 12 |
| 2 | R/R | ✓ | Risk/Reward ratio (TP/SL) | 16 |
| 3 | SL % | ✓ | Расстояние до стоп-лосса (%) | 14 |
| 4 | TP1 % | ✓ | Расстояние до TP1 (%) | 10 |
| 5 | TP2 % | ✓ | Расстояние до TP2 (%) | 8 |
| 6 | TP3 % | ✓ | Расстояние до TP3 (%) | 6 |
| 7 | Risk % | | Риск в % от входа | 15 |
| 8 | Reward % | | Потенциальная прибыль (%) | 17 |
| 9 | Valid Hours | | Время жизни сигнала (часы) | 8 |
| **ACCUMULATION SCORE (22)** |
| 10 | acc_oi_growth | | Рост Open Interest | 14 |
| 11 | acc_oi_stability | | Стабильность OI | 10 |
| 12 | acc_funding_cheap | | Дешёвый funding для направления | 16 |
| 13 | acc_funding_gradient | | Динамика изменения funding | 15 |
| 14 | acc_crowd_bearish | | Толпа в шортах (contrarian) | 18 |
| 15 | acc_crowd_bullish | | Толпа в лонгах (contrarian) | 18 |
| 16 | acc_coordinated_buying | | Скоординированные покупки | 13 |
| 17 | acc_volume_accumulation | | Тихое накопление объёма | 14 |
| 18 | acc_cross_oi_migration | | Миграция OI между биржами | 9 |
| 19 | acc_cross_price_lead | | Опережение цены на других биржах | 11 |
| 20 | acc_spot_bid_pressure | | Давление покупателей в SPOT стакане | 13 |
| 21 | acc_spot_ask_weakness | | Слабость продавцов в SPOT | 12 |
| 22 | acc_spot_imbalance_score | | Скор дисбаланса SPOT стакана | 14 |
| 23 | acc_futures_bid_pressure | | Давление покупателей в FUTURES | 13 |
| 24 | acc_futures_ask_weakness | | Слабость продавцов в FUTURES | 12 |
| 25 | acc_futures_imbalance_score | | Скор дисбаланса FUTURES стакана | 14 |
| 26 | acc_orderbook_divergence | | Расхождение SPOT/FUTURES стаканов | 11 |
| 27 | acc_orderbook_total | | Общий скор ордербука | 15 |
| 28 | acc_wash_trading_penalty | | Штраф за wash trading | 10 |
| 29 | acc_extreme_funding_penalty | | Штраф за экстремальный funding | 12 |
| 30 | acc_orderbook_against_penalty | | Штраф за стакан против позиции | 11 |
| 31 | acc_total | | **Общий accumulation score** | 17 |
| **FUTURES (9)** |
| 32 | oi_change_1m_pct | | Изменение OI за 1 минуту (%) | 8 |
| 33 | oi_change_5m_pct | | Изменение OI за 5 минут (%) | 12 |
| 34 | oi_change_1h_pct | | Изменение OI за 1 час (%) | 15 |
| 35 | funding_rate_pct | | Текущий funding rate (%) | 18 |
| 36 | long_account_pct | | % аккаунтов в лонгах | 16 |
| 37 | short_account_pct | | % аккаунтов в шортах | 16 |
| 38 | long_short_ratio | | Ratio лонг/шорт позиций | 17 |
| 39 | futures_price_change_5m_pct | | Изменение цены фьючерса за 5м | 9 |
| 40 | futures_price_change_1h_pct | | Изменение цены фьючерса за 1ч | 11 |
| **SPOT (9)** |
| 41 | spot_spread_pct | | Спред bid/ask (%) | 10 |
| 42 | spot_price_change_1m_pct | | Изменение цены за 1м | 7 |
| 43 | spot_price_change_5m_pct | | Изменение цены за 5м | 9 |
| 44 | spot_price_change_1h_pct | | Изменение цены за 1ч | 11 |
| 45 | volume_spike_ratio | | Всплеск объёма (текущий/средний) | 14 |
| 46 | spot_orderbook_imbalance | | Дисбаланс стакана (-1 до +1) | 15 |
| 47 | buy_ratio_5m | | Доля покупок за 5м (0-1) | 13 |
| 48 | trades_count_1m | | Количество сделок за 1м | 8 |
| 49 | trades_count_5m | | Количество сделок за 5м | 10 |
| **VOLUME (4)** |
| 50 | volume_1m | | Объём за 1 минуту (USD) | 9 |
| 51 | volume_5m | | Объём за 5 минут (USD) | 11 |
| 52 | volume_1h | | Объём за 1 час (USD) | 12 |
| 53 | volume_avg_1h | | Средний часовой объём | 10 |
| **OI (1)** |
| 54 | oi_value_usd | | Open Interest в USD | 13 |
| **ORDERBOOK (8)** |
| 55 | spot_bid_volume_atr | | Объём бидов в ATR глубине | 12 |
| 56 | spot_ask_volume_atr | | Объём асков в ATR глубине | 12 |
| 57 | spot_imbalance_atr | | Дисбаланс в ATR глубине | 14 |
| 58 | futures_bid_volume_atr | | Биды фьючерса в ATR | 12 |
| 59 | futures_ask_volume_atr | | Аски фьючерса в ATR | 12 |
| 60 | futures_imbalance_atr | | Дисбаланс фьючерса в ATR | 14 |
| 61 | spot_bid_volume_20 | | Объём бидов топ-20 уровней | 11 |
| 62 | spot_ask_volume_20 | | Объём асков топ-20 уровней | 11 |
| **EXTRA (2)** |
| 63 | orderbook_score | | Скор ордербука из details | 15 |
| 64 | spot_atr_pct | | ATR спота (%) | 13 |
| **TRIGGER (2)** |
| 65 | trigger_severity | | Уровень серьёзности триггера (1-5) | 12 |
| 66 | trigger_score | | Скор триггера (0-100) | 14 |
| **CATEGORICAL (2)** |
| 67 | direction_num | ✓ | Направление: 1=LONG, 0=SHORT | 10 |
| 68 | signal_type_* | | Тип сигнала (one-hot encoded) | 11 |

---

## Топ-10 по важности (из исследований)

| Рейтинг | Параметр | Почему важен |
|---------|----------|--------------|
| 18 | acc_crowd_bearish/bullish | Contrarian trading — основа squeeze сетапов |
| 18 | funding_rate_pct | Стоимость позиции, предсказывает развороты |
| 17 | Reward % | R/R определяет математическое ожидание |
| 17 | acc_total | Агрегированный скор накопления |
| 17 | long_short_ratio | Позиционирование толпы |
| 16 | R/R | Классика риск-менеджмента |
| 16 | acc_funding_cheap | Дешёвый вход = преимущество |
| 16 | long/short_account_pct | Retail sentiment |
| 15 | oi_change_1h_pct | Приток денег в рынок |
| 15 | acc_orderbook_total | Подтверждение стакана |

---

## ТОП-30 фичей используемых в ML

```
1.  acc_crowd_bearish        (18) - Толпа в шортах
2.  acc_crowd_bullish        (18) - Толпа в лонгах
3.  funding_rate_pct         (18) - Funding rate
4.  Reward %                 (17) - Потенциальная прибыль
5.  acc_total                (17) - Общий accumulation score
6.  long_short_ratio         (17) - Ratio лонг/шорт
7.  R/R                      (16) - Risk/Reward
8.  acc_funding_cheap        (16) - Дешёвый funding
9.  long_account_pct         (16) - % в лонгах
10. short_account_pct        (16) - % в шортах
11. Risk %                   (15) - Риск в %
12. acc_funding_gradient     (15) - Динамика funding
13. oi_change_1h_pct         (15) - Изменение OI за 1ч
14. acc_orderbook_total      (15) - Скор ордербука
15. spot_orderbook_imbalance (15) - Дисбаланс стакана
16. orderbook_score          (15) - Скор ордербука
17. SL %                     (14) - Стоп-лосс %
18. acc_oi_growth            (14) - Рост OI
19. acc_volume_accumulation  (14) - Накопление объёма
20. acc_spot_imbalance_score (14) - Дисбаланс SPOT
21. acc_futures_imbalance_score (14) - Дисбаланс FUTURES
22. volume_spike_ratio       (14) - Всплеск объёма
23. spot_imbalance_atr       (14) - Дисбаланс в ATR
24. futures_imbalance_atr    (14) - Дисбаланс фьючерса ATR
25. trigger_score            (14) - Скор триггера
26. acc_coordinated_buying   (13) - Скоординированные покупки
27. acc_spot_bid_pressure    (13) - Давление покупателей SPOT
28. acc_futures_bid_pressure (13) - Давление покупателей FUTURES
29. buy_ratio_5m             (13) - Доля покупок за 5м
30. oi_value_usd             (13) - Open Interest в USD
```
