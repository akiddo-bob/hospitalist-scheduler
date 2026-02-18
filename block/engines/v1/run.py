#!/usr/bin/env python3
"""
Runner for Block Schedule Engine v1.

Runs the engine with multiple seeds to produce schedule variations,
saves JSON output, and generates HTML reports.

Usage:
    python -m block.engines.v1.run                  # 5 default seeds
    python -m block.engines.v1.run --seeds 42 7     # custom seeds
    python -m block.engines.v1.run --seeds 42 --no-report  # skip HTML
"""

import argparse
import json
import os
import sys
from datetime import datetime

# Ensure project root is on path
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from block.engines.v1.engine import run_engine
from block.engines.v1.report import generate_full_report


# ─── Block 3 Configuration ──────────────────────────────────────────────────
BLOCK_START = datetime(2026, 3, 2)   # Monday
BLOCK_END   = datetime(2026, 6, 28)  # Sunday

DEFAULT_SEEDS = [42, 7, 123, 256, 999]

OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")


def main():
    parser = argparse.ArgumentParser(description="Block Schedule Engine v1")
    parser.add_argument("--seeds", type=int, nargs="+", default=DEFAULT_SEEDS,
                        help="Random seeds for schedule variations (default: 42 7 123 256 999)")
    parser.add_argument("--no-report", action="store_true",
                        help="Skip HTML report generation")
    parser.add_argument("--output-dir", type=str, default=OUTPUT_DIR,
                        help=f"Output directory (default: {OUTPUT_DIR})")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # ── Run engine for each seed ─────────────────────────────────────────
    all_results = []
    for seed in args.seeds:
        results = run_engine(BLOCK_START, BLOCK_END, seed=seed)
        all_results.append(results)

        # Save per-variation JSON
        json_path = os.path.join(args.output_dir, f"block_schedule_v1_seed{seed}.json")
        with open(json_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"  Saved: {json_path}")

    # ── Generate HTML reports ────────────────────────────────────────────
    if not args.no_report:
        print(f"\n{'=' * 70}")
        print("Generating HTML reports...")
        print(f"{'=' * 70}")
        generate_full_report(all_results, args.output_dir)

    print(f"\nDone! Output in: {args.output_dir}")


if __name__ == "__main__":
    main()
