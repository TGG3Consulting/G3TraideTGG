# -*- coding: utf-8 -*-
"""
Run Genetic Algorithm Feature Selection.

Usage:
    python -m src.ml.feature_selection.run_ga backtester/output/backtest_YYYYMMDD_HHMMSS.xlsx

Options:
    --population    Population size (default: 50)
    --generations   Max generations (default: 100)
    --features      Number of features to select (default: 10)
    --early-stop    Early stopping rounds (default: 20)
    --metric        Fitness metric: f1, precision, recall, auc (default: f1)
    --model         Model type: lightgbm, xgboost, catboost, rf (default: lightgbm)
    --seed          Random seed (default: 42)
    --resume        Resume from checkpoint
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

from .config import GAConfig, FeaturePool
from .genetic_selector import GeneticFeatureSelector


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Genetic Algorithm Feature Selection for ML Trading Models"
    )

    parser.add_argument(
        "excel_path",
        type=str,
        help="Path to backtest results Excel file"
    )

    parser.add_argument(
        "--population",
        type=int,
        default=50,
        help="Population size (default: 50)"
    )

    parser.add_argument(
        "--generations",
        type=int,
        default=100,
        help="Maximum generations (default: 100)"
    )

    parser.add_argument(
        "--features",
        type=int,
        default=10,
        help="Number of features to select (default: 10)"
    )

    parser.add_argument(
        "--early-stop",
        type=int,
        default=20,
        help="Early stopping rounds (default: 20)"
    )

    parser.add_argument(
        "--metric",
        type=str,
        default="f1",
        choices=["f1", "precision", "recall", "auc"],
        help="Fitness metric (default: f1)"
    )

    parser.add_argument(
        "--model",
        type=str,
        default="lightgbm",
        choices=["lightgbm", "xgboost", "catboost", "rf"],
        help="Model type (default: lightgbm)"
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed (default: 42)"
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from checkpoint"
    )

    parser.add_argument(
        "--output-dir",
        type=str,
        default="models/feature_selection",
        help="Output directory (default: models/feature_selection)"
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Validate input file
    excel_path = Path(args.excel_path)
    if not excel_path.exists():
        print(f"Error: File not found: {excel_path}")
        sys.exit(1)

    # Load data
    print(f"Loading {excel_path}...")
    df = pd.read_excel(excel_path)
    print(f"Total rows: {len(df)}")

    # Filter filled only
    if "Filled" in df.columns:
        df = df[df["Filled"] == "YES"].copy()
        print(f"Filled trades: {len(df)}")

    # Create target
    if "Net %" in df.columns:
        df["label_win"] = (df["Net %"] > 0).astype(int)
        win_rate = df["label_win"].mean()
        print(f"Win rate: {win_rate:.1%}")
    else:
        print("Error: 'Net %' column not found")
        sys.exit(1)

    # Create config
    config = GAConfig(
        population_size=args.population,
        n_generations=args.generations,
        n_features_to_select=args.features,
        early_stopping_rounds=args.early_stop,
        fitness_metric=args.metric,
        model_type=args.model,
        random_seed=args.seed,
        output_dir=Path(args.output_dir),
    )

    # Feature pool
    feature_pool = FeaturePool()

    # Create selector
    selector = GeneticFeatureSelector(config, feature_pool)

    # Resume from checkpoint if requested
    if args.resume:
        checkpoint_path = config.output_dir / config.checkpoint_file
        if selector.load_checkpoint(checkpoint_path):
            print(f"Resumed from generation {selector.generation}")
        else:
            print("No checkpoint found, starting fresh")

    # Run GA
    result = selector.run(df, target_column="label_win")

    # Print summary for copy-paste into training script
    print("\n" + "=" * 70)
    print("COPY THIS TO train_full_features.py:")
    print("=" * 70)
    print("TOP_FEATURES = [")
    for feature in result.best_features:
        print(f'    "{feature}",')
    print("]")
    print("=" * 70)


if __name__ == "__main__":
    main()
