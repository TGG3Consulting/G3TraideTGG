import json
import sys
import io
from datetime import datetime, timezone, timedelta

# Fix encoding
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from hybrid_downloader import HybridHistoryDownloader
from state_builder import StateBuilder
from signals import AccumulationDetector, SignalConfig
from config import AppConfig

config = AppConfig()
downloader = HybridHistoryDownloader(cache_dir='cache', coinalyze_api_key=config.coinalyze_api_key)

# Download full January 2025
start = datetime(2025, 1, 1, tzinfo=timezone.utc)
end = datetime(2025, 1, 31, 23, 59, tzinfo=timezone.utc)

print(f"Downloading 1INCHUSDT data for January 2025...")
data = downloader.download_with_coinalyze_backfill(['1INCHUSDT'], start, end)

builder = StateBuilder(data)
signal_config = SignalConfig(min_accumulation_score=45, min_probability=45)
detector = AccumulationDetector(config=signal_config)

# Scan all of January 2025 for best scores
print("\nScanning January 2025 for high-score opportunities...")
best_days = []

for day in range(1, 32):
    check_date = datetime(2025, 1, day, 12, 0, tzinfo=timezone.utc)
    futures = builder.build_futures_state('1INCHUSDT', check_date)
    spot = builder.build_spot_state('1INCHUSDT', check_date)

    if futures and spot and len(futures.oi_history) >= 3:
        score = detector._calculate_score('1INCHUSDT', futures, spot)

        current_oi = float(futures.oi_history[-1].open_interest)
        oi_3d_ago = float(futures.oi_history[-3].open_interest)
        change_3d = ((current_oi - oi_3d_ago) / oi_3d_ago) * 100 if oi_3d_ago > 0 else 0

        best_days.append({
            'day': day,
            'score': score.total,
            'oi_change': change_3d,
            'oi_growth': score.oi_growth,
            'oi_stability': score.oi_stability,
            'crowd_bullish': score.crowd_bullish,
            'crowd_bearish': score.crowd_bearish,
            'funding': score.funding_cheap + score.funding_gradient,
            'vol_accum': score.volume_accumulation
        })

# Sort by score
best_days.sort(key=lambda x: x['score'], reverse=True)

print("\nTop 10 days by score:")
print(f"{'Day':>5} {'Score':>6} {'OI%':>7} {'OIg':>4} {'OIs':>4} {'Crd':>4} {'Fund':>5} {'Vol':>4}")
print("-" * 50)
for d in best_days[:10]:
    crowd = d['crowd_bullish'] or d['crowd_bearish']
    print(f"Jan {d['day']:2} {d['score']:>6} {d['oi_change']:>+6.1f}% {d['oi_growth']:>4} {d['oi_stability']:>4} {crowd:>4} {d['funding']:>5} {d['vol_accum']:>4}")

# Deep dive into Jan 24
print("\n" + "="*50)
print("DEEP DIVE: Jan 24, 2025")
print("="*50)

check_date = datetime(2025, 1, 24, 14, 0, tzinfo=timezone.utc)  # 14:00 not blocked
futures = builder.build_futures_state('1INCHUSDT', check_date)
spot = builder.build_spot_state('1INCHUSDT', check_date)

if futures and spot:
    # Run full analyze
    result = detector.analyze('1INCHUSDT', futures, spot, skip_threshold=False)

    # Always calculate score and direction for R:R check
    score = detector._calculate_score('1INCHUSDT', futures, spot)
    direction = detector._determine_direction(futures, spot, score)

    if result:
        print(f"SIGNAL GENERATED!")
        print(f"  Direction: {result.direction}")
        print(f"  Probability: {result.probability}")
        print(f"  Score: {result.score.total}")
        direction = result.direction  # Use actual result direction
        score = result.score
    else:
        print("NO SIGNAL - checking why...")
        print(f"  Score: {score.total} (threshold: 45)")
        print(f"  Direction: {direction}")

        # Check OI rejection
        oi_1h = float(futures.oi_change_1h_pct)
        print(f"  OI change 1h: {oi_1h}%")

        if len(futures.oi_history) >= 3:
            current_oi = float(futures.oi_history[-1].open_interest)
            oi_3d_ago = float(futures.oi_history[-3].open_interest)
            change_3d = ((current_oi - oi_3d_ago) / oi_3d_ago) * 100 if oi_3d_ago > 0 else 0
            print(f"  OI change 3d: {change_3d:+.1f}%")

        probability = detector._calculate_probability(score, futures, spot, direction)
        print(f"  Probability: {probability} (threshold: 45)")

        if score.total < 45:
            print(f"  BLOCKED BY: Score too low")
        elif probability < 45:
            print(f"  BLOCKED BY: Probability too low")
        else:
            print(f"  BLOCKED BY: Unknown reason")

    # Also check R:R
    from signals import RiskCalculator
    risk_calc = RiskCalculator(config=signal_config)
    risk_levels = risk_calc.calculate(
        symbol='1INCHUSDT',
        direction=direction,
        current_price=spot.last_price,
        spot_state=spot,
        futures_state=futures,
        valid_hours=24,
        accumulation_score=score.total,
    )
    print(f"\nRisk calculation:")
    print(f"  R:R ratio: {risk_levels.risk_reward_ratio} (min: 2.0)")
    print(f"  Entry: {risk_levels.entry_limit}")
    print(f"  SL: {risk_levels.stop_loss} ({risk_levels.stop_loss_pct}%)")
    if risk_levels.take_profits:
        for tp in risk_levels.take_profits:
            print(f"  {tp.label}: {tp.price} ({tp.percent}%)")
