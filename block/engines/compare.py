#!/usr/bin/env python3
"""
Compare outputs across engine versions or seed variations.

Reads JSON result files from different engine runs and produces a
side-by-side comparison of key metrics.

Usage:
    python -m block.engines.compare output/block_schedule_v1_seed42.json output/block_schedule_v1_seed7.json
    python -m block.engines.compare output/v1/seed42.json output/v2/seed42.json
"""

import json
import os
import sys


def load_results(path):
    """Load a JSON results file."""
    with open(path) as f:
        return json.load(f)


def compare_stats(results_list, labels):
    """Compare high-level stats across results."""
    print(f"\n{'=' * 80}")
    print("SCHEDULE COMPARISON")
    print(f"{'=' * 80}")

    # Header
    col_width = max(20, max(len(l) for l in labels) + 2)
    header = f"{'Metric':<30}"
    for label in labels:
        header += f"{label:>{col_width}}"
    print(header)
    print("-" * (30 + col_width * len(labels)))

    # Stats rows
    metrics = [
        ("seed", "Seed"),
        ("total_eligible", "Eligible Providers"),
        ("total_weeks_assigned", "Weeks Assigned"),
        ("total_weekends_assigned", "Weekends Assigned"),
        ("total_site_gaps", "Site Gaps"),
        ("total_overfills", "Overfills"),
        ("total_under_utilized", "Under-Utilized"),
        ("stretch_overrides", "Stretch Overrides"),
        ("behind_pace_count", "Behind-Pace"),
    ]

    for key, label in metrics:
        row = f"{label:<30}"
        for r in results_list:
            val = r.get("stats", {}).get(key, "—")
            row += f"{str(val):>{col_width}}"
        print(row)


def compare_site_gaps(results_list, labels):
    """Compare per-site gaps across results."""
    print(f"\n{'─' * 80}")
    print("SITE GAP COMPARISON")
    print(f"{'─' * 80}")

    col_width = max(20, max(len(l) for l in labels) + 2)

    # Collect all sites
    all_sites = set()
    for r in results_list:
        all_sites.update(r.get("site_fill", {}).keys())
    all_sites = sorted(all_sites)

    header = f"{'Site':<25}"
    for label in labels:
        header += f"{label:>{col_width}}"
    print(header)
    print("-" * (25 + col_width * len(labels)))

    for site in all_sites:
        row = f"{site:<25}"
        for r in results_list:
            sf = r.get("site_fill", {}).get(site, {})
            total_short = 0
            for dtype in ["weekday", "weekend"]:
                for entry in sf.get(dtype, []):
                    total_short += entry.get("shortfall", 0)
            row += f"{total_short:>{col_width}}"
        print(row)


def compare_provider_utilization(results_list, labels, threshold=2):
    """Compare providers with utilization gaps >= threshold."""
    print(f"\n{'─' * 80}")
    print(f"PROVIDER UTILIZATION GAPS (>= {threshold})")
    print(f"{'─' * 80}")

    col_width = max(20, max(len(l) for l in labels) + 2)

    # Collect all providers with gaps in any result
    gap_providers = set()
    for r in results_list:
        for pname, ps in r.get("provider_summary", {}).items():
            if ps.get("weeks_gap", 0) + ps.get("weekends_gap", 0) >= threshold:
                gap_providers.add(pname)
    gap_providers = sorted(gap_providers)

    if not gap_providers:
        print("No providers with significant gaps.")
        return

    header = f"{'Provider':<30}"
    for label in labels:
        header += f"{label:>{col_width}}"
    print(header)
    print("-" * (30 + col_width * len(labels)))

    for pname in gap_providers:
        row = f"{pname:<30}"
        for r in results_list:
            ps = r.get("provider_summary", {}).get(pname, {})
            wk_gap = ps.get("weeks_gap", 0)
            we_gap = ps.get("weekends_gap", 0)
            total = wk_gap + we_gap
            row += f"{f'{wk_gap}wk+{we_gap}we={total}':>{col_width}}"
        print(row)


def compare_all(file_paths):
    """Run all comparisons."""
    results_list = []
    labels = []

    for path in file_paths:
        r = load_results(path)
        results_list.append(r)
        # Create a short label from filename
        basename = os.path.basename(path).replace(".json", "")
        labels.append(basename)

    compare_stats(results_list, labels)
    compare_site_gaps(results_list, labels)
    compare_provider_utilization(results_list, labels)


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m block.engines.compare <file1.json> <file2.json> [file3.json ...]")
        print("\nCompares block schedule outputs side-by-side.")
        sys.exit(1)

    compare_all(sys.argv[1:])


if __name__ == "__main__":
    main()
