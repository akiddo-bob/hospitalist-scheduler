"""
Prior Actuals Verification for V3 Pre-Scheduler (Task 2).

Re-derives prior weeks/weekends worked from B1+B2 Amion HTML schedules
and compares against the Excel Providers sheet values. Flags discrepancies.
"""

import os
import re
import sys
from collections import defaultdict
from datetime import date

_V3_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINES_DIR = os.path.dirname(_V3_DIR)
_BLOCK_DIR = os.path.dirname(_ENGINES_DIR)
_PROJECT_ROOT = os.path.dirname(_BLOCK_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from parse_schedule import parse_schedule, merge_schedules
from name_match import to_canonical, match_provider, clean_html_provider


# ═══════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

BLOCK_1_START = date(2025, 6, 30)
BLOCK_1_END = date(2025, 11, 2)
BLOCK_2_START = date(2025, 11, 3)
BLOCK_2_END = date(2026, 3, 1)

PRIOR_FILES = [
    "2025-06.html", "2025-07.html", "2025-08.html", "2025-09.html",
    "2025-10.html", "2025-11.html", "2025-12.html",
    "2026-01.html", "2026-02.html",
]

DISCREPANCY_THRESHOLD = 0.5  # flag if abs(computed - excel) >= this


# ═══════════════════════════════════════════════════════════════════════════
# SERVICE CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════
# Copied from block/recalculate_prior_actuals.py.
# Must stay in sync with docs/block-scheduling-rules.md Section 1.3.

def classify_service(service_name, hours):
    """Classify a service as 'day', 'night', 'swing', or 'exclude'."""
    sname = service_name.lower()
    hours_lower = hours.lower() if hours else ""

    # ── INCLUDE overrides (match exclusion patterns but are included) ──
    if "ccpa" in sname or "physician advisor" in sname:
        return "day"
    if sname == "hospital medicine consults":
        return "day"
    if "mannington pa" in sname and "app" not in sname:
        return "day"
    if sname == "cape pa":
        return "day"
    if "um referral" in sname or "um rounds" in sname:
        return "day"

    # ── EXCLUDE rules ──
    if " app" in sname or "-app" in sname or "(app)" in sname or sname.startswith("app ") or sname.endswith(" app"):
        return "exclude"
    if "apn" in sname:
        return "exclude"
    if " pa " in sname or sname.endswith(" pa"):
        return "exclude"
    if "night coverage" in sname:
        return "exclude"
    if "resident" in sname:
        return "exclude"
    if "fellow" in sname:
        return "exclude"
    if "behavioral" in sname:
        return "exclude"
    if "site director" in sname:
        return "exclude"
    if "admin" in sname:
        return "exclude"
    if "hospice" in sname:
        return "exclude"
    if "kessler" in sname:
        return "exclude"
    if "holy redeemer" in sname:
        return "exclude"
    if "cape rmd" in sname:
        return "exclude"
    if "long call" in sname:
        return "exclude"
    if sname.startswith("direct care long call"):
        return "exclude"
    if "early call" in sname:
        return "exclude"
    if "virtua" in sname and "coverage" in sname:
        return "exclude"
    if sname.strip() == "um":
        return "day"
    if "consult" in sname:
        return "exclude"
    if "moonlighting" in sname:
        return "exclude"
    if "ltc-sar" in sname and "physician" not in sname:
        return "exclude"
    if "do not use" in sname:
        return "exclude"
    if sname.strip() == "cc":
        return "exclude"
    if "app lead" in sname:
        return "exclude"
    if "teaching admissions" in sname:
        return "exclude"
    if "night app" in sname:
        return "exclude"

    # ── NIGHT shifts ──
    if any(kw in sname for kw in ["night", "nocturnist", "(nah)"]):
        return "night"
    if re.match(r'[57]p-[57]a', hours_lower):
        return "night"
    if re.match(r'5a-7a', hours_lower):
        return "night"
    if re.match(r'11p-7a', hours_lower):
        return "night"

    # ── SWING shifts ──
    if "swing" in sname:
        return "swing"
    if re.match(r'[1-4]p-', hours_lower):
        return "swing"

    # ── DAY shifts — everything remaining ──
    return "day"


def parse_date(date_str):
    """Parse M/D/YYYY date string from parsed HTML schedule."""
    try:
        parts = date_str.split("/")
        if len(parts) == 3:
            return date(int(parts[2]), int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        pass
    return None


def _in_block_range(d):
    """Check if a date falls within Block 1 or Block 2."""
    if d is None:
        return False
    return (BLOCK_1_START <= d <= BLOCK_1_END) or (BLOCK_2_START <= d <= BLOCK_2_END)


# ═══════════════════════════════════════════════════════════════════════════
# COMPUTE PRIOR ACTUALS FROM HTML
# ═══════════════════════════════════════════════════════════════════════════

def compute_prior_actuals(schedules_dir):
    """Parse B1+B2 monthly HTML schedules and compute per-provider actuals.

    Args:
        schedules_dir: path to directory containing monthly HTML files

    Returns:
        tuple: (results_dict, files_parsed_count)
            results_dict: canonical_name -> {
                weekday_shifts, weekend_shifts, night_shifts, swing_shifts,
                prior_weeks, prior_weekends, prior_nights
            }
    """
    all_months = []
    files_parsed = 0
    for fname in PRIOR_FILES:
        fpath = os.path.join(schedules_dir, fname)
        if not os.path.exists(fpath):
            continue
        month_data = parse_schedule(fpath)
        all_months.append(month_data)
        files_parsed += 1

    if not all_months:
        return {}, 0

    merged = merge_schedules(all_months)

    # Pre-classify all services
    service_classes = {}
    for s in merged.get("services", []):
        service_classes[s["name"]] = classify_service(s["name"], s["hours"])

    # Count shifts per provider, deduplicated per day
    # Priority: night(3) > swing(2) > weekday/weekend(1)
    provider_day_shifts = defaultdict(dict)

    for day_entry in merged["schedule"]:
        dow = day_entry["day_of_week"]
        is_weekend = dow in ("Sat", "Sun")
        date_str = day_entry["date"]

        d = parse_date(date_str)
        if not _in_block_range(d):
            continue

        for assignment in day_entry["assignments"]:
            provider = clean_html_provider(assignment["provider"])
            if not provider:
                continue
            if assignment.get("moonlighting", False):
                continue

            svc_class = service_classes.get(assignment["service"], "day")
            if svc_class == "exclude":
                continue

            if svc_class == "night":
                shift_type = "night"
            elif svc_class == "swing":
                shift_type = "swing"
            elif is_weekend:
                shift_type = "weekend"
            else:
                shift_type = "weekday"

            existing = provider_day_shifts[provider].get(date_str)
            priority = {"night": 3, "swing": 2, "weekday": 1, "weekend": 1}
            if existing is None or priority.get(shift_type, 0) > priority.get(existing, 0):
                provider_day_shifts[provider][date_str] = shift_type

    # Aggregate from deduplicated day-level data
    provider_counts = defaultdict(lambda: {
        "weekday_shifts": 0, "weekend_shifts": 0,
        "night_shifts": 0, "swing_shifts": 0,
    })

    for provider, day_map in provider_day_shifts.items():
        for date_str, shift_type in day_map.items():
            if shift_type == "night":
                provider_counts[provider]["night_shifts"] += 1
            elif shift_type == "swing":
                provider_counts[provider]["swing_shifts"] += 1
            elif shift_type == "weekend":
                provider_counts[provider]["weekend_shifts"] += 1
            else:
                provider_counts[provider]["weekday_shifts"] += 1

    # Canonicalize names and sum duplicates
    results = {}
    for provider in sorted(provider_counts.keys()):
        counts = provider_counts[provider]
        canonical = to_canonical(provider)
        if canonical in results:
            results[canonical]["weekday_shifts"] += counts["weekday_shifts"]
            results[canonical]["weekend_shifts"] += counts["weekend_shifts"]
            results[canonical]["night_shifts"] += counts["night_shifts"]
            results[canonical]["swing_shifts"] += counts["swing_shifts"]
            results[canonical]["prior_weeks"] = round(
                results[canonical]["weekday_shifts"] / 5, 1)
            results[canonical]["prior_weekends"] = round(
                results[canonical]["weekend_shifts"] / 2, 1)
            results[canonical]["prior_nights"] = results[canonical]["night_shifts"]
        else:
            results[canonical] = {
                "weekday_shifts": counts["weekday_shifts"],
                "prior_weeks": round(counts["weekday_shifts"] / 5, 1),
                "weekend_shifts": counts["weekend_shifts"],
                "prior_weekends": round(counts["weekend_shifts"] / 2, 1),
                "night_shifts": counts["night_shifts"],
                "prior_nights": counts["night_shifts"],
                "swing_shifts": counts["swing_shifts"],
            }

    return results, files_parsed


# ═══════════════════════════════════════════════════════════════════════════
# EVALUATE: COMPARE COMPUTED VS EXCEL
# ═══════════════════════════════════════════════════════════════════════════

def evaluate_prior_actuals(providers, tags_data, schedules_dir):
    """Compare computed B1+B2 actuals against Excel Providers sheet values.

    Args:
        providers: dict from load_providers_from_excel (name -> provider data)
        tags_data: dict from load_tags_from_excel (for do_not_schedule filtering)
        schedules_dir: path to B1+B2 monthly schedule HTML directory

    Returns:
        dict with keys:
            computed: dict of computed actuals per provider
            comparisons: list of per-provider comparison records
            discrepancies: list of records where has_discrepancy is True
            missing_from_schedule: list of provider names in Excel but not in schedule
            missing_from_excel: list of provider names in schedule but not in Excel
            files_parsed: int
            summary: dict of counts
    """
    computed, files_parsed = compute_prior_actuals(schedules_dir)
    computed_names = list(computed.keys())

    comparisons = []
    discrepancies = []
    missing_from_schedule = []
    missing_from_excel = []

    # Build do_not_schedule set for filtering
    dns_providers = set()
    for pname, tag_list in tags_data.items():
        for t in tag_list:
            if t["tag"] == "do_not_schedule":
                dns_providers.add(pname)

    # Compare each Excel provider against computed actuals
    for pname, pdata in sorted(providers.items()):
        # Skip do_not_schedule providers
        if pname in dns_providers:
            continue

        annual_wk = pdata.get("annual_weeks", 0)
        annual_we = pdata.get("annual_weekends", 0)

        # Skip providers with 0 annual allocation
        if annual_wk == 0 and annual_we == 0:
            continue

        # Match Excel name to computed name
        matched_key = match_provider(pname, computed_names)

        excel_wk = pdata.get("prior_weeks_worked", 0)
        excel_we = pdata.get("prior_weekends_worked", 0)

        if matched_key:
            c = computed[matched_key]
            comp_wk = c["prior_weeks"]
            comp_we = c["prior_weekends"]
            wk_diff = abs(comp_wk - excel_wk)
            we_diff = abs(comp_we - excel_we)
            has_disc = wk_diff >= DISCREPANCY_THRESHOLD or we_diff >= DISCREPANCY_THRESHOLD

            detail_parts = []
            if wk_diff >= DISCREPANCY_THRESHOLD:
                detail_parts.append(
                    f"Weeks: computed {comp_wk} vs Excel {excel_wk} (diff {wk_diff:.1f})")
            if we_diff >= DISCREPANCY_THRESHOLD:
                detail_parts.append(
                    f"Weekends: computed {comp_we} vs Excel {excel_we} (diff {we_diff:.1f})")

            status = "DISCREPANCY" if has_disc else "MATCH"
            rec = {
                "provider": pname,
                "computed_weeks": comp_wk,
                "excel_weeks": excel_wk,
                "weeks_diff": round(wk_diff, 1),
                "computed_weekends": comp_we,
                "excel_weekends": excel_we,
                "weekends_diff": round(we_diff, 1),
                "status": status,
                "detail": "; ".join(detail_parts) if detail_parts else "Values match",
            }
            comparisons.append(rec)
            if has_disc:
                discrepancies.append(rec)
        else:
            rec = {
                "provider": pname,
                "computed_weeks": 0,
                "excel_weeks": excel_wk,
                "weeks_diff": excel_wk,
                "computed_weekends": 0,
                "excel_weekends": excel_we,
                "weekends_diff": excel_we,
                "status": "MISSING_FROM_SCHEDULE",
                "detail": f"'{pname}' not found in parsed B1+B2 schedules",
            }
            comparisons.append(rec)
            missing_from_schedule.append(pname)

    # Check for providers in schedule but not in Excel
    excel_names = list(providers.keys())
    for comp_name in sorted(computed.keys()):
        c = computed[comp_name]
        total = c["weekday_shifts"] + c["weekend_shifts"]
        if total == 0:
            continue
        matched = match_provider(comp_name, excel_names)
        if not matched:
            missing_from_excel.append(comp_name)

    # Summary
    matching = sum(1 for r in comparisons if r["status"] == "MATCH")
    max_wk_diff = max((r["weeks_diff"] for r in comparisons), default=0)
    max_we_diff = max((r["weekends_diff"] for r in comparisons), default=0)

    summary = {
        "providers_compared": len(comparisons),
        "providers_matching": matching,
        "providers_with_discrepancy": len(discrepancies),
        "missing_from_schedule": len(missing_from_schedule),
        "missing_from_excel": len(missing_from_excel),
        "max_weeks_diff": round(max_wk_diff, 1),
        "max_weekends_diff": round(max_we_diff, 1),
        "files_parsed": files_parsed,
        "files_expected": len(PRIOR_FILES),
    }

    return {
        "computed": computed,
        "comparisons": comparisons,
        "discrepancies": discrepancies,
        "missing_from_schedule": missing_from_schedule,
        "missing_from_excel": missing_from_excel,
        "files_parsed": files_parsed,
        "summary": summary,
    }
