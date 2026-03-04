# -*- coding: utf-8 -*-
"""
Configuration for Genetic Algorithm Feature Selection.
"""

from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path


@dataclass
class GAConfig:
    """Genetic Algorithm configuration."""

    # Population
    population_size: int = 50
    n_features_to_select: int = 10

    # Evolution
    n_generations: int = 100
    early_stopping_rounds: int = 20

    # Selection
    tournament_size: int = 3
    elite_count: int = 5

    # Crossover
    crossover_rate: float = 0.8
    crossover_type: str = "two_point"  # "single_point", "two_point", "uniform"

    # Mutation
    mutation_rate: float = 0.15
    mutation_type: str = "swap"  # "swap" keeps exactly n_features_to_select

    # Training
    train_test_split: float = 0.8
    model_type: str = "lightgbm"  # fastest with good results

    # Optimization target
    fitness_metric: str = "f1"  # "f1", "precision", "recall", "auc"

    # Reproducibility
    random_seed: Optional[int] = 42

    # Output
    output_dir: Path = field(default_factory=lambda: Path("models/feature_selection"))
    results_file: str = "ga_results.json"
    checkpoint_file: str = "ga_checkpoint.pkl"
    save_every_n_generations: int = 10

    # Logging
    verbose: bool = True
    log_every_n_generations: int = 1


@dataclass
class FeaturePool:
    """
    Pool of available features for selection.

    Features are categorized by source. Orderbook-related features are excluded.
    Names match Excel column names from backtester output.
    """

    # BASIC features (from signal metadata)
    BASIC: List[str] = field(default_factory=lambda: [
        "Prob",
        "Conf",
        "R/R",
        "SL %",
        "TP1 %",
        "TP2 %",
        "TP3 %",
        "Risk %",
        "Reward %",
        "Valid Hours",
    ])

    # ACCUMULATION features (non-orderbook only)
    ACCUMULATION: List[str] = field(default_factory=lambda: [
        "acc_oi_growth",
        "acc_oi_stability",
        "acc_funding_cheap",
        "acc_funding_gradient",
        "acc_crowd_bearish",
        "acc_crowd_bullish",
        "acc_coordinated_buying",
        "acc_volume_accumulation",
        "acc_cross_oi_migration",
        "acc_cross_price_lead",
        "acc_wash_trading_penalty",
        "acc_extreme_funding_penalty",
    ])

    # FUTURES SNAPSHOT - OI
    FUTURES_OI: List[str] = field(default_factory=lambda: [
        "futures_oi_value",
        "futures_oi_value_usd",
        "futures_oi_change_1m_pct",
        "futures_oi_change_5m_pct",
        "futures_oi_change_1h_pct",
    ])

    # FUTURES SNAPSHOT - FUNDING
    FUTURES_FUNDING: List[str] = field(default_factory=lambda: [
        "futures_funding_rate",
        "futures_funding_rate_pct",
        "futures_funding_mark_price",
    ])

    # FUTURES SNAPSHOT - LONG/SHORT RATIO
    FUTURES_LS_RATIO: List[str] = field(default_factory=lambda: [
        "futures_long_account_pct",
        "futures_short_account_pct",
        "futures_long_short_ratio",
    ])

    # FUTURES SNAPSHOT - PRICE CHANGES
    FUTURES_PRICE: List[str] = field(default_factory=lambda: [
        "futures_price_change_5m_pct",
        "futures_price_change_1h_pct",
    ])

    # SPOT SNAPSHOT - PRICE
    SPOT_PRICE: List[str] = field(default_factory=lambda: [
        "spot_price_bid",
        "spot_price_ask",
        "spot_price_last",
        "spot_price_mid",
        "spot_price_spread_pct",
    ])

    # SPOT SNAPSHOT - PRICE CHANGES
    SPOT_PRICE_CHANGES: List[str] = field(default_factory=lambda: [
        "spot_price_change_1m_pct",
        "spot_price_change_5m_pct",
        "spot_price_change_1h_pct",
    ])

    # SPOT SNAPSHOT - VOLUME
    SPOT_VOLUME: List[str] = field(default_factory=lambda: [
        "spot_volume_1m",
        "spot_volume_5m",
        "spot_volume_1h",
        "spot_volume_avg_1h",
        "spot_volume_spike_ratio",
    ])

    # SPOT SNAPSHOT - TRADES
    SPOT_TRADES: List[str] = field(default_factory=lambda: [
        "spot_trades_count_1m",
        "spot_trades_count_5m",
        "spot_trades_buy_ratio_5m",
    ])

    # SIGNAL DETAILS (non-orderbook)
    SIGNAL_DETAILS: List[str] = field(default_factory=lambda: [
        "signal_details_book_imbalance",
        "signal_details_volume_ratio",
        "signal_details_spot_atr_pct",
    ])

    # TRIGGER DETECTION
    TRIGGER: List[str] = field(default_factory=lambda: [
        "trigger_severity",
        "trigger_score",
    ])

    # TRIGGER DETECTION DETAILS
    TRIGGER_DETAILS: List[str] = field(default_factory=lambda: [
        "trigger_details_bid_volume",
        "trigger_details_ask_volume",
        "trigger_details_buy_ratio",
        "trigger_details_sell_ratio",
        "trigger_details_trades_count",
        "trigger_details_volume_5m",
        "trigger_details_current_price",
    ])

    # CONFIG
    CONFIG: List[str] = field(default_factory=lambda: [
        "config_min_accumulation_score",
        "config_min_probability",
        "config_min_risk_reward",
        "config_default_sl_pct",
        "config_tp1_ratio",
        "config_tp2_ratio",
        "config_tp3_ratio",
    ])

    # EXCLUDED: Orderbook-related features (not in pool)
    # - acc_spot_bid_pressure, acc_spot_ask_weakness, acc_spot_imbalance_score
    # - acc_futures_bid_pressure, acc_futures_ask_weakness, acc_futures_imbalance_score
    # - acc_orderbook_divergence, acc_orderbook_total, acc_orderbook_against_penalty
    # - acc_total (includes orderbook components)
    # - spot_orderbook_bid_volume_20, spot_orderbook_ask_volume_20, spot_orderbook_imbalance
    # - signal_details_spot_bid_volume_atr, signal_details_spot_ask_volume_atr
    # - signal_details_spot_imbalance_atr, signal_details_orderbook_score

    def get_all_features(self) -> List[str]:
        """Get all available features as a flat list."""
        return (
            self.BASIC +
            self.ACCUMULATION +
            self.FUTURES_OI +
            self.FUTURES_FUNDING +
            self.FUTURES_LS_RATIO +
            self.FUTURES_PRICE +
            self.SPOT_PRICE +
            self.SPOT_PRICE_CHANGES +
            self.SPOT_VOLUME +
            self.SPOT_TRADES +
            self.SIGNAL_DETAILS +
            self.TRIGGER +
            self.TRIGGER_DETAILS +
            self.CONFIG
        )

    def get_feature_count(self) -> int:
        """Get total number of available features."""
        return len(self.get_all_features())

    def validate_features(self, df_columns: List[str]) -> List[str]:
        """
        Validate which features exist in the DataFrame.

        Returns list of features that exist in df_columns.
        """
        all_features = self.get_all_features()
        available = [f for f in all_features if f in df_columns]
        missing = [f for f in all_features if f not in df_columns]

        if missing:
            print(f"Warning: {len(missing)} features not found in data: {missing[:5]}...")

        return available
