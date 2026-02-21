#!/usr/bin/env python3
"""
Runner for Block Schedule Engine v3.

Runs the engine with multiple seeds to produce schedule variations.
Consumes pre-scheduler output for validated inputs.

Usage:
    python -m block.engines.v3.run                    # single default seed
    python -m block.engines.v3.run --seeds 42 7 123   # multiple seeds
    python -m block.engines.v3.run --no-pre-schedule  # skip pre-scheduler data
"""

import argparse
import json
import os
import sys
from datetime import datetime

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from block.engines.v3.engine import run_engine
from block.engines.v3.report import generate_report, generate_multi_seed_report

# ─── Block 3 Configuration ──────────────────────────────────────────────────
BLOCK_START = datetime(2026, 3, 2)   # Monday
BLOCK_END   = datetime(2026, 6, 28)  # Sunday

DEFAULT_SEEDS = [42]

V3_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_EXCEL = os.path.join(V3_DIR, "input", "hospitalist_scheduler.xlsx")
DEFAULT_PRE_SCHEDULE = os.path.join(V3_DIR, "output", "pre_schedule_output.json")
DEFAULT_AVAILABILITY_DIR = os.path.join(_PROJECT_ROOT, "input", "individualSchedules")
DEFAULT_OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output", "v3")


def main():
    parser = argparse.ArgumentParser(description="Block Schedule Engine v3")
    parser.add_argument("--seeds", type=int, nargs="+", default=DEFAULT_SEEDS,
                        help="Random seeds for schedule variations (default: 42)")
    parser.add_argument("--excel", type=str, default=DEFAULT_EXCEL,
                        help="Path to hospitalist_scheduler.xlsx")
    parser.add_argument("--pre-schedule", type=str, default=DEFAULT_PRE_SCHEDULE,
                        help="Path to pre_schedule_output.json")
    parser.add_argument("--no-pre-schedule", action="store_true",
                        help="Skip loading pre-scheduler data")
    parser.add_argument("--availability-dir", type=str, default=DEFAULT_AVAILABILITY_DIR,
                        help="Directory with individual availability JSONs")
    parser.add_argument("--output-dir", type=str, default=DEFAULT_OUTPUT_DIR,
                        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--no-report", action="store_true",
                        help="Skip HTML report generation")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    pre_schedule_path = None if args.no_pre_schedule else args.pre_schedule

    # ── Run engine for each seed ─────────────────────────────────────
    all_results = []
    for seed in args.seeds:
        results = run_engine(
            excel_path=args.excel,
            pre_schedule_path=pre_schedule_path,
            availability_dir=args.availability_dir,
            block_start=BLOCK_START,
            block_end=BLOCK_END,
            seed=seed,
        )
        all_results.append(results)

        # Save per-seed JSON
        json_path = os.path.join(args.output_dir, f"schedule_seed{seed}.json")
        with open(json_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"  Saved: {json_path}")

        # Save gap report separately for easy review
        gap_path = os.path.join(args.output_dir, f"gap_report_seed{seed}.json")
        with open(gap_path, "w") as f:
            json.dump({
                "stats": results["stats"],
                "gap_report": results["gap_report"],
                "site_coverage": results["site_coverage"],
            }, f, indent=2, default=str)
        print(f"  Saved: {gap_path}")

    # ── Generate HTML reports ──────────────────────────────────────────
    if not args.no_report:
        print(f"\n{'=' * 70}")
        print("Generating HTML reports...")
        print(f"{'=' * 70}")
        if len(all_results) > 1:
            generate_multi_seed_report(all_results, args.output_dir)
        else:
            generate_report(all_results[0], args.output_dir)

    # ── Summary across seeds ──────────────────────────────────────────
    if len(all_results) > 1:
        print(f"\n{'=' * 70}")
        print(f"SEED COMPARISON")
        print(f"{'=' * 70}")
        print(f"{'Seed':>6} {'Gaps':>6} {'ZG Viol':>8} {'Wk Cov%':>8} {'WE Cov%':>8} {'W/ Cands':>9}")
        for r in all_results:
            s = r["stats"]
            print(f"{s['seed']:>6} {s['total_gaps']:>6} {s['zero_gap_violations']:>8} "
                  f"{s['weekday_coverage_pct']:>7.1f}% {s['weekend_coverage_pct']:>7.1f}% "
                  f"{s['gaps_with_candidates']:>9}")

    print(f"\nDone! Output in: {args.output_dir}")


if __name__ == "__main__":
    main()
