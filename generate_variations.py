#!/usr/bin/env python3
"""
Generate 5 unique long-call schedule variations using different tiebreak seeds.
Outputs each as an HTML report in the same format as longcall_report.html,
plus .txt and .json files.
"""

import json
import os
from datetime import datetime

# Import the assignment engine and report generator
import assign_longcall as engine
from generate_report import generate_report

OUTPUT_DIR = engine.OUTPUT_DIR
VARIATION_SEEDS = ["alpha", "bravo", "charlie", "delta", "echo"]


def compute_stats(assignments, flags, provider_stats):
    """Compute summary stats from a run."""
    total_slots = sum(1 for dt in assignments
                      for slot in ["teaching", "dc1", "dc2"]
                      if assignments[dt].get(slot))
    unfilled = sum(1 for f in flags if f["flag_type"] == "UNFILLED_SLOT")
    doubles = sum(1 for f in flags if f["flag_type"] == "DOUBLE_LONGCALL")
    missed = sum(1 for f in flags if f["flag_type"] == "NO_LONGCALL")

    # Weekend LC distribution
    providers_2plus_wknd = 0
    for p, s in provider_stats.items():
        if s.get("weekend_long_calls", 0) >= 2:
            providers_2plus_wknd += 1

    # LC count range
    lc_counts = [s["total_long_calls"] for s in provider_stats.values() if s["total_long_calls"] > 0]
    min_lc = min(lc_counts) if lc_counts else 0
    max_lc = max(lc_counts) if lc_counts else 0

    return {
        "total_slots": total_slots,
        "unfilled": unfilled,
        "doubles": doubles,
        "missed": missed,
        "total_flags": len(flags),
        "providers_2plus_wknd": providers_2plus_wknd,
        "min_lc": min_lc,
        "max_lc": max_lc,
        "lc_range": f"{min_lc}-{max_lc}",
    }


def main():
    print("Loading schedule data...")
    data = engine.load_schedule()

    print("Building daily provider data...")
    daily_data = engine.build_daily_data(data)
    all_daily_data = engine.build_all_daily_data(data)
    days_in_block = len([d for d in daily_data if engine.is_in_block(d)])
    print(f"  Days in block: {days_in_block}")

    results = []

    for i, seed in enumerate(VARIATION_SEEDS, 1):
        print(f"\n{'='*60}")
        print(f"  Variation {i}/5 — seed: {seed}")
        print(f"{'='*60}")

        # Set the module-level seed before running
        engine.VARIATION_SEED = seed

        assignments, flags, provider_stats = engine.assign_long_calls(daily_data, all_daily_data)
        stats = compute_stats(assignments, flags, provider_stats)

        print(f"  Slots filled: {stats['total_slots']}, Unfilled: {stats['unfilled']}, "
              f"Doubles: {stats['doubles']}, Missed: {stats['missed']}, "
              f"2+ Wknd LCs: {stats['providers_2plus_wknd']}")

        # Generate HTML report (same format as longcall_report.html)
        report_html = generate_report(assignments, flags, provider_stats, daily_data, all_daily_data)
        html_filepath = os.path.join(OUTPUT_DIR, f"variation_{i}.html")
        with open(html_filepath, 'w') as f:
            f.write(report_html)
        print(f"  Wrote: {html_filepath}")

        # Write .txt using the engine's format functions
        table = engine.format_output_table(assignments, None, flags)
        summary = engine.format_provider_summary(provider_stats)
        flag_summary = engine.format_flag_summary(flags)
        full_output = table + "\n" + summary + "\n" + flag_summary
        txt_filepath = os.path.join(OUTPUT_DIR, f"variation_{i}.txt")
        with open(txt_filepath, 'w') as f:
            f.write(full_output)
        print(f"  Wrote: {txt_filepath}")

        # Write .json
        json_out = {}
        for dt, slots in assignments.items():
            date_key = dt.strftime("%Y-%m-%d")
            json_out[date_key] = {
                "teaching": slots.get("teaching"),
                "dc1": slots.get("dc1"),
                "dc2": slots.get("dc2"),
            }
        json_filepath = os.path.join(OUTPUT_DIR, f"variation_{i}.json")
        with open(json_filepath, 'w') as f:
            json.dump({
                "variation": i,
                "seed": seed,
                "assignments": json_out,
                "provider_stats": {
                    p: {k: v for k, v in s.items() if k != "days_of_week"}
                    for p, s in provider_stats.items()
                },
                "flags": flags
            }, f, indent=2, default=str)
        print(f"  Wrote: {json_filepath}")

        results.append((i, seed, stats))

    # Print comparison summary
    print(f"\n{'='*70}")
    print("  VARIATION COMPARISON SUMMARY")
    print(f"{'='*70}")
    print(f"  {'Var':<5} {'Seed':<10} {'Filled':<8} {'Unfilled':<10} {'Doubles':<9} {'Missed':<8} {'2+Wknd':<8} {'LC Range'}")
    print(f"  {'-'*65}")
    for i, seed, stats in results:
        print(f"  {i:<5} {seed:<10} {stats['total_slots']:<8} {stats['unfilled']:<10} "
              f"{stats['doubles']:<9} {stats['missed']:<8} {stats['providers_2plus_wknd']:<8} {stats['lc_range']}")

    print(f"\nAll variation files written to: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
