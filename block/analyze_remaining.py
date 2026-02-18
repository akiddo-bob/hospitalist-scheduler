#!/usr/bin/env python3
"""
Analyze providers with high % remaining weeks/weekends.
Fetches live data from Google Sheet Providers tab, flags providers
with >70% of annual weeks or weekends remaining (behind pace).
Cross-references with prior_actuals.json to help explain discrepancies.
"""

import csv
import io
import json
import os
import sys
import urllib.request
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")

SHEET_ID = "1dbHUkE-pLtQJK02ig2EbU2eny33N60GQUvk4muiXW5M"
SHEET_BASE = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv"

# Fiscal year context
YEAR_START = date(2025, 6, 30)
YEAR_END = date(2026, 6, 28)
TODAY = date.today()
TOTAL_YEAR_WEEKS = (YEAR_END - YEAR_START).days / 7
WEEKS_ELAPSED = (TODAY - YEAR_START).days / 7
PCT_YEAR_ELAPSED = WEEKS_ELAPSED / TOTAL_YEAR_WEEKS * 100


def fetch_providers_tab():
    """Fetch Providers tab from Google Sheet as CSV."""
    url = f"{SHEET_BASE}&sheet={urllib.request.quote('Providers')}"
    print(f"Fetching Providers tab from Google Sheet...")
    print(f"  URL: {url}")
    with urllib.request.urlopen(url, timeout=15) as resp:
        text = resp.read().decode("utf-8")
    print(f"  Fetched {len(text)} bytes")
    return text


def load_prior_actuals():
    """Load prior_actuals.json for cross-reference."""
    path = os.path.join(OUTPUT_DIR, "prior_actuals.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def parse_float(val, default=0.0):
    """Parse a float from CSV, returning default if empty or invalid."""
    if not val or not val.strip():
        return default
    try:
        return float(val.strip())
    except ValueError:
        return default


def main():
    # Fetch live data
    csv_text = fetch_providers_tab()
    prior_actuals = load_prior_actuals()

    reader = csv.DictReader(io.StringIO(csv_text))

    # Debug: print the column names
    print(f"  Columns: {reader.fieldnames}")

    providers = []
    skipped_dns = []
    skipped_zero = []
    skipped_fmla = []

    for row in reader:
        name = row.get("provider_name", "").strip()
        if not name:
            continue

        shift_type = row.get("shift_type", "").strip()

        # Skip FMLA / do_not_schedule providers
        if shift_type.upper() in ("FMLA", "DNS", "DO NOT SCHEDULE", "INACTIVE"):
            skipped_fmla.append(name)
            continue

        annual_weeks = parse_float(row.get("annual_weeks", ""))
        annual_weekends = parse_float(row.get("annual_weekends", ""))
        annual_nights = parse_float(row.get("annual_nights", ""))

        prior_weeks_worked = parse_float(row.get("prior_weeks_worked", ""))
        prior_weekends_worked = parse_float(row.get("prior_weekends_worked", ""))
        prior_nights_worked = parse_float(row.get("prior_nights_worked", ""))

        weeks_remaining = parse_float(row.get("weeks_remaining", ""))
        weekends_remaining = parse_float(row.get("weekends_remaining", ""))
        nights_remaining = parse_float(row.get("nights_remaining", ""))

        fte = parse_float(row.get("fte", ""), default=0.0)
        scheduler = row.get("scheduler", "").strip()

        # Calculate worked from annual - remaining
        weeks_worked = annual_weeks - weeks_remaining if annual_weeks > 0 else prior_weeks_worked
        weekends_worked = annual_weekends - weekends_remaining if annual_weekends > 0 else prior_weekends_worked

        # Calculate % remaining
        pct_weeks_rem = (weeks_remaining / annual_weeks * 100) if annual_weeks > 0 else None
        pct_weekends_rem = (weekends_remaining / annual_weekends * 100) if annual_weekends > 0 else None

        # Skip pure nights-only providers (annual_weeks=0 and annual_weekends=0)
        if annual_weeks == 0 and annual_weekends == 0:
            if annual_nights > 0:
                skipped_zero.append(f"{name} (Nights-only, {annual_nights} annual nights)")
            else:
                skipped_zero.append(f"{name} (all zeros)")
            continue

        # Look up prior actuals for cross-reference (try multiple name variants)
        name_upper = name.upper()
        pa = prior_actuals.get(name_upper, None)
        if pa is None:
            # Try removing periods (e.g., "W. ERIC" -> "W ERIC")
            name_no_dots = name_upper.replace(".", "")
            pa = prior_actuals.get(name_no_dots, None)
        if pa is None:
            # Try original case
            pa = prior_actuals.get(name, {})
        if pa is None:
            pa = {}
        pa_weeks = pa.get("prior_weeks", None)
        pa_weekends = pa.get("prior_weekends", None)

        providers.append({
            "name": name,
            "shift_type": shift_type,
            "fte": fte,
            "scheduler": scheduler,
            "annual_weeks": annual_weeks,
            "annual_weekends": annual_weekends,
            "annual_nights": annual_nights,
            "weeks_worked": weeks_worked,
            "weekends_worked": weekends_worked,
            "weeks_remaining": weeks_remaining,
            "weekends_remaining": weekends_remaining,
            "nights_remaining": parse_float(row.get("nights_remaining", "")),
            "pct_weeks_rem": pct_weeks_rem,
            "pct_weekends_rem": pct_weekends_rem,
            "pa_weeks": pa_weeks,
            "pa_weekends": pa_weekends,
            "prior_weeks_worked_sheet": prior_weeks_worked,
            "prior_weekends_worked_sheet": prior_weekends_worked,
        })

    print(f"\n  Loaded {len(providers)} active day/hybrid providers")
    print(f"  Skipped {len(skipped_fmla)} FMLA/DNS providers: {', '.join(skipped_fmla)}")
    print(f"  Skipped {len(skipped_zero)} nights-only/zero providers")

    # =========================================================================
    # FILTER: >70% weeks OR >70% weekends remaining
    # =========================================================================
    THRESHOLD = 70.0

    flagged = []
    for p in providers:
        wk_flag = p["pct_weeks_rem"] is not None and p["pct_weeks_rem"] > THRESHOLD
        we_flag = p["pct_weekends_rem"] is not None and p["pct_weekends_rem"] > THRESHOLD
        if wk_flag or we_flag:
            p["flag_weeks"] = wk_flag
            p["flag_weekends"] = we_flag
            # Sort key: max of the two percentages
            p["sort_key"] = max(
                p["pct_weeks_rem"] if p["pct_weeks_rem"] is not None else 0,
                p["pct_weekends_rem"] if p["pct_weekends_rem"] is not None else 0,
            )
            flagged.append(p)

    flagged.sort(key=lambda x: -x["sort_key"])

    # =========================================================================
    # PRINT RESULTS
    # =========================================================================
    print()
    print("=" * 140)
    print(f"PROVIDERS WITH >70% OF ANNUAL WEEKS OR WEEKENDS REMAINING")
    print(f"Year: {YEAR_START} to {YEAR_END}  |  Today: {TODAY}")
    print(f"Year progress: {PCT_YEAR_ELAPSED:.1f}% elapsed ({WEEKS_ELAPSED:.1f} of {TOTAL_YEAR_WEEKS:.0f} weeks)")
    expected_pct_remaining = 100 - PCT_YEAR_ELAPSED
    print(f"Expected ~{expected_pct_remaining:.0f}% remaining if on pace")
    print(f"Threshold: >{THRESHOLD:.0f}% remaining  |  Flagged: {len(flagged)} of {len(providers)} active providers")
    print("=" * 140)

    # Header
    print(f"\n{'Provider':<30s} {'Type':<7s} {'FTE':>4s} {'Sched':<10s} "
          f"{'AnnWk':>6s} {'WkWkd':>6s} {'WkRem':>6s} {'%WkR':>6s} "
          f"{'AnnWE':>6s} {'WEWkd':>6s} {'WERem':>6s} {'%WER':>6s} "
          f"{'Flag':>8s}")
    print("-" * 140)

    for p in flagged:
        wk_pct_str = f"{p['pct_weeks_rem']:.0f}%" if p['pct_weeks_rem'] is not None else "N/A"
        we_pct_str = f"{p['pct_weekends_rem']:.0f}%" if p['pct_weekends_rem'] is not None else "N/A"

        flags = []
        if p["flag_weeks"]:
            flags.append("WK")
        if p["flag_weekends"]:
            flags.append("WE")
        flag_str = "+".join(flags)

        print(f"{p['name']:<30s} {p['shift_type']:<7s} {p['fte']:>4.2f} {p['scheduler']:<10s} "
              f"{p['annual_weeks']:>6.1f} {p['weeks_worked']:>6.1f} {p['weeks_remaining']:>6.1f} {wk_pct_str:>6s} "
              f"{p['annual_weekends']:>6.1f} {p['weekends_worked']:>6.1f} {p['weekends_remaining']:>6.1f} {we_pct_str:>6s} "
              f"{flag_str:>8s}")

    # =========================================================================
    # CROSS-REFERENCE WITH PRIOR ACTUALS
    # =========================================================================
    print()
    print("=" * 140)
    print("CROSS-REFERENCE: Google Sheet vs Prior Actuals (computed from HTML schedules)")
    print("Shows discrepancies that may explain high remaining counts")
    print("=" * 140)

    print(f"\n{'Provider':<30s} {'Sheet PrWk':>10s} {'Calc PrWk':>10s} {'Wk Diff':>8s} "
          f"{'Sheet PrWE':>10s} {'Calc PrWE':>10s} {'WE Diff':>8s} {'Notes':<30s}")
    print("-" * 140)

    for p in flagged:
        sheet_pw = p["prior_weeks_worked_sheet"]
        sheet_pwe = p["prior_weekends_worked_sheet"]
        calc_pw = p["pa_weeks"]
        calc_pwe = p["pa_weekends"]

        pw_diff_str = ""
        pwe_diff_str = ""
        notes = []

        if calc_pw is not None:
            pw_diff = sheet_pw - calc_pw
            pw_diff_str = f"{pw_diff:+.1f}"
            if abs(pw_diff) > 1.0:
                notes.append(f"Wk diff {pw_diff:+.1f}")
        else:
            pw_diff_str = "N/A"
            notes.append("Not in prior calc")

        if calc_pwe is not None:
            pwe_diff = sheet_pwe - calc_pwe
            pwe_diff_str = f"{pwe_diff:+.1f}"
            if abs(pwe_diff) > 1.0:
                notes.append(f"WE diff {pwe_diff:+.1f}")
        else:
            pwe_diff_str = "N/A"

        calc_pw_str = f"{calc_pw:.1f}" if calc_pw is not None else "N/A"
        calc_pwe_str = f"{calc_pwe:.1f}" if calc_pwe is not None else "N/A"

        print(f"{p['name']:<30s} {sheet_pw:>10.1f} {calc_pw_str:>10s} {pw_diff_str:>8s} "
              f"{sheet_pwe:>10.1f} {calc_pwe_str:>10s} {pwe_diff_str:>8s} {' | '.join(notes):<30s}")

    # =========================================================================
    # SUMMARY STATS
    # =========================================================================
    print()
    print("=" * 140)
    print("SUMMARY STATISTICS")
    print("=" * 140)

    all_pct_wk = [p["pct_weeks_rem"] for p in providers if p["pct_weeks_rem"] is not None]
    all_pct_we = [p["pct_weekends_rem"] for p in providers if p["pct_weekends_rem"] is not None]

    if all_pct_wk:
        avg_wk = sum(all_pct_wk) / len(all_pct_wk)
        med_wk = sorted(all_pct_wk)[len(all_pct_wk) // 2]
        print(f"  All providers - Weeks % remaining:    avg={avg_wk:.1f}%, median={med_wk:.1f}%, "
              f"min={min(all_pct_wk):.1f}%, max={max(all_pct_wk):.1f}% (n={len(all_pct_wk)})")

    if all_pct_we:
        avg_we = sum(all_pct_we) / len(all_pct_we)
        med_we = sorted(all_pct_we)[len(all_pct_we) // 2]
        print(f"  All providers - Weekends % remaining: avg={avg_we:.1f}%, median={med_we:.1f}%, "
              f"min={min(all_pct_we):.1f}%, max={max(all_pct_we):.1f}% (n={len(all_pct_we)})")

    print(f"\n  Year progress: {PCT_YEAR_ELAPSED:.1f}% elapsed => expected ~{expected_pct_remaining:.0f}% remaining")
    print(f"  Flagged providers (>{THRESHOLD:.0f}% remaining): {len(flagged)}")

    # Categorize the flagged providers
    both_flag = [p for p in flagged if p["flag_weeks"] and p["flag_weekends"]]
    wk_only = [p for p in flagged if p["flag_weeks"] and not p["flag_weekends"]]
    we_only = [p for p in flagged if not p["flag_weeks"] and p["flag_weekends"]]

    print(f"    Both weeks + weekends flagged: {len(both_flag)}")
    print(f"    Weeks only flagged: {len(wk_only)}")
    print(f"    Weekends only flagged: {len(we_only)}")

    # Providers with negative remaining (over-scheduled)
    over_wk = [p for p in providers if p["weeks_remaining"] < 0]
    over_we = [p for p in providers if p["weekends_remaining"] < 0]
    if over_wk or over_we:
        print(f"\n  Over-scheduled (negative remaining):")
        for p in over_wk:
            print(f"    {p['name']}: {p['weeks_remaining']:.1f} weeks remaining (OVER by {abs(p['weeks_remaining']):.1f})")
        for p in over_we:
            print(f"    {p['name']}: {p['weekends_remaining']:.1f} weekends remaining (OVER by {abs(p['weekends_remaining']):.1f})")

    # Providers with very high remaining (>85%) - most concerning
    very_high = [p for p in flagged if p["sort_key"] > 85]
    if very_high:
        print(f"\n  MOST CONCERNING (>{85}% remaining):")
        for p in very_high:
            max_pct = p["sort_key"]
            print(f"    {p['name']}: {max_pct:.0f}% (annual_wk={p['annual_weeks']}, "
                  f"wk_rem={p['weeks_remaining']}, annual_we={p['annual_weekends']}, "
                  f"we_rem={p['weekends_remaining']}, FTE={p['fte']}, sched={p['scheduler']})")

    print()


if __name__ == "__main__":
    main()
