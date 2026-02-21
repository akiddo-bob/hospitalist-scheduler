"""
Retrospective Evaluation for V3 Pre-Scheduler.

Parses actual Block 3 schedules (Amion HTML) and compares against
pre-scheduler predictions from Tasks 3 (difficulty) and 4 (holiday).

For Task 3: computes actual B3 weeks worked, actual density, max consecutive
stretch, and whether stretch violations occurred — compares vs predicted risk.

For Task 4: identifies who actually worked Memorial Day week and whether
the tier prediction was accurate.

SCOPE GUARD: Hardcoded for Block 3 of cycle 25-26.
"""

import os
import sys
from collections import defaultdict
from datetime import date, timedelta

_V3_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINES_DIR = os.path.dirname(_V3_DIR)
_BLOCK_DIR = os.path.dirname(_ENGINES_DIR)
_PROJECT_ROOT = os.path.dirname(_BLOCK_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from parse_schedule import parse_schedule, merge_schedules
from name_match import to_canonical, match_provider, clean_html_provider
from block.engines.v3.prior_actuals_eval import classify_service, parse_date


# ═══════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

BLOCK_3_START = date(2026, 3, 2)   # Monday
BLOCK_3_END = date(2026, 6, 28)    # Sunday
BLOCK_3_WEEKS = 17

BLOCK_3_FILES = ["2026-03.html", "2026-04.html", "2026-05.html", "2026-06.html"]

MEMORIAL_DAY_WEEK_START = date(2026, 5, 25)
MEMORIAL_DAY_WEEK_END = date(2026, 5, 29)


def _in_block3(d):
    if d is None:
        return False
    return BLOCK_3_START <= d <= BLOCK_3_END


# ═══════════════════════════════════════════════════════════════════════════
# PARSE BLOCK 3 ACTUALS
# ═══════════════════════════════════════════════════════════════════════════

def compute_block3_actuals(schedules_dir):
    """Parse Block 3 HTML schedules and compute per-provider actuals.

    Returns:
        dict: canonical_name -> {
            weekday_shifts, weekend_shifts, night_shifts, swing_shifts,
            b3_weeks, b3_weekends, dates_worked (set of date objects),
            worked_memorial_week (bool)
        }
    """
    all_months = []
    files_parsed = 0
    for fname in BLOCK_3_FILES:
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
    provider_day_shifts = defaultdict(dict)

    for day_entry in merged["schedule"]:
        dow = day_entry["day_of_week"]
        is_weekend = dow in ("Sat", "Sun")
        date_str = day_entry["date"]

        d = parse_date(date_str)
        if not _in_block3(d):
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
            if existing is None or priority.get(shift_type, 0) > priority.get(existing[0], 0):
                provider_day_shifts[provider][date_str] = (shift_type, d)

    # Aggregate
    provider_counts = defaultdict(lambda: {
        "weekday_shifts": 0, "weekend_shifts": 0,
        "night_shifts": 0, "swing_shifts": 0,
        "dates_worked": set(), "worked_memorial_week": False,
    })

    for provider, day_map in provider_day_shifts.items():
        for date_str, (shift_type, d) in day_map.items():
            canonical = to_canonical(provider)
            counts = provider_counts[canonical]
            counts["dates_worked"].add(d)

            if shift_type == "night":
                counts["night_shifts"] += 1
            elif shift_type == "swing":
                counts["swing_shifts"] += 1
            elif shift_type == "weekend":
                counts["weekend_shifts"] += 1
            else:
                counts["weekday_shifts"] += 1

            if MEMORIAL_DAY_WEEK_START <= d <= MEMORIAL_DAY_WEEK_END:
                counts["worked_memorial_week"] = True

    # Build results with computed fields
    results = {}
    for canonical, counts in provider_counts.items():
        # Merge duplicate canonical entries
        if canonical in results:
            r = results[canonical]
            r["weekday_shifts"] += counts["weekday_shifts"]
            r["weekend_shifts"] += counts["weekend_shifts"]
            r["night_shifts"] += counts["night_shifts"]
            r["swing_shifts"] += counts["swing_shifts"]
            r["dates_worked"] |= counts["dates_worked"]
            r["worked_memorial_week"] = r["worked_memorial_week"] or counts["worked_memorial_week"]
        else:
            results[canonical] = dict(counts)

    # Compute derived fields
    for canonical, r in results.items():
        r["b3_weeks"] = round(r["weekday_shifts"] / 5, 1)
        r["b3_weekends"] = round(r["weekend_shifts"] / 2, 1)
        r["max_consecutive"] = _max_consecutive(r["dates_worked"])
        r["has_hard_violation"] = r["max_consecutive"] > 12
        r["has_extended_stretch"] = r["max_consecutive"] > 7

    return results, files_parsed


def _max_consecutive(dates_worked):
    """Compute maximum consecutive days worked."""
    if not dates_worked:
        return 0
    sorted_dates = sorted(dates_worked)
    max_streak = 1
    current_streak = 1
    for i in range(1, len(sorted_dates)):
        if (sorted_dates[i] - sorted_dates[i - 1]).days == 1:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 1
    return max_streak


# ═══════════════════════════════════════════════════════════════════════════
# DIFFICULTY RETROSPECTIVE
# ═══════════════════════════════════════════════════════════════════════════

def evaluate_difficulty_retrospective(diff_result, b3_actuals):
    """Add retrospective columns to difficulty records.

    For each provider in the difficulty analysis, look up their actual
    Block 3 data and compute:
    - Actual B3 weeks worked
    - Actual max consecutive stretch
    - Whether a stretch violation occurred (>12 days)
    - Prediction accuracy (risk vs actual outcome)

    Args:
        diff_result: dict from evaluate_difficulty()
        b3_actuals: dict from compute_block3_actuals()

    Returns:
        list of augmented records with retrospective fields
    """
    b3_names = list(b3_actuals.keys())
    retro_records = []

    for rec in diff_result["records"]:
        pname = rec["provider"]
        matched = match_provider(pname, b3_names)

        retro = dict(rec)  # copy original

        if matched:
            b3 = b3_actuals[matched]
            actual_wk = b3["b3_weeks"]
            actual_we = b3["b3_weekends"]
            max_consec = b3["max_consecutive"]
            hard_violation = b3["has_hard_violation"]
            extended = b3["has_extended_stretch"]

            retro["actual_b3_weeks"] = actual_wk
            retro["actual_b3_weekends"] = actual_we
            retro["actual_max_consecutive"] = max_consec
            retro["actual_hard_violation"] = hard_violation
            retro["actual_extended_stretch"] = extended

            # Assess prediction accuracy
            predicted_risk = rec["risk_level"]
            if hard_violation:
                actual_severity = "HARD_VIOLATION"
            elif extended:
                actual_severity = "EXTENDED"
            else:
                actual_severity = "CLEAN"

            # Did prediction match reality?
            if predicted_risk == "HIGH" and hard_violation:
                accuracy = "CORRECT"
            elif predicted_risk == "HIGH" and extended:
                accuracy = "CLOSE"  # predicted high, got extended
            elif predicted_risk == "HIGH" and not extended:
                accuracy = "OVER"  # predicted high, was clean
            elif predicted_risk in ("ELEVATED", "MODERATE") and extended:
                accuracy = "CORRECT"
            elif predicted_risk in ("ELEVATED", "MODERATE") and hard_violation:
                accuracy = "UNDER"  # predicted moderate, got hard violation
            elif predicted_risk in ("ELEVATED", "MODERATE") and not extended:
                accuracy = "OVER"
            elif predicted_risk == "LOW" and hard_violation:
                accuracy = "MISS"  # predicted low, got hard violation
            elif predicted_risk == "LOW" and extended:
                accuracy = "UNDER"  # predicted low, got extended
            else:
                accuracy = "CORRECT"  # predicted low, was clean

            retro["prediction_accuracy"] = accuracy
        else:
            retro["actual_b3_weeks"] = 0
            retro["actual_b3_weekends"] = 0
            retro["actual_max_consecutive"] = 0
            retro["actual_hard_violation"] = False
            retro["actual_extended_stretch"] = False
            retro["prediction_accuracy"] = "NO_DATA"

        retro_records.append(retro)

    # Summary stats
    total = len(retro_records)
    with_data = sum(1 for r in retro_records if r["prediction_accuracy"] != "NO_DATA")
    correct = sum(1 for r in retro_records if r["prediction_accuracy"] in ("CORRECT", "CLOSE"))
    over = sum(1 for r in retro_records if r["prediction_accuracy"] == "OVER")
    under = sum(1 for r in retro_records if r["prediction_accuracy"] in ("UNDER", "MISS"))
    hard_violations = sum(1 for r in retro_records if r.get("actual_hard_violation"))
    extended_stretches = sum(1 for r in retro_records if r.get("actual_extended_stretch") and not r.get("actual_hard_violation"))

    summary = {
        "providers_with_data": with_data,
        "correct_predictions": correct,
        "over_predictions": over,
        "under_predictions": under,
        "actual_hard_violations": hard_violations,
        "actual_extended_only": extended_stretches,
        "accuracy_pct": round(correct / with_data * 100, 1) if with_data > 0 else 0,
    }

    return retro_records, summary


# ═══════════════════════════════════════════════════════════════════════════
# HOLIDAY RETROSPECTIVE
# ═══════════════════════════════════════════════════════════════════════════

def evaluate_holiday_retrospective(hol_result, b3_actuals):
    """Add retrospective columns to holiday records.

    For each provider in the holiday analysis, check whether they actually
    worked Memorial Day week in the Block 3 schedule.

    Args:
        hol_result: dict from evaluate_holidays()
        b3_actuals: dict from compute_block3_actuals()

    Returns:
        list of augmented records with retrospective fields
    """
    b3_names = list(b3_actuals.keys())
    retro_records = []

    for rec in hol_result["records"]:
        pname = rec["provider"]
        matched = match_provider(pname, b3_names)

        retro = dict(rec)  # copy original

        if matched:
            b3 = b3_actuals[matched]
            actually_worked = b3["worked_memorial_week"]
            retro["actual_worked_memorial"] = actually_worked

            tier = rec["tier"]
            if tier == "MUST":
                if actually_worked:
                    retro["tier_outcome"] = "CORRECT"  # MUST and did work
                else:
                    retro["tier_outcome"] = "NOT_SCHEDULED"  # MUST but didn't work
            elif tier == "SHOULD":
                if actually_worked:
                    retro["tier_outcome"] = "OVERRIDE"  # preference overridden
                else:
                    retro["tier_outcome"] = "HONORED"  # preference honored
            elif tier == "MET":
                if actually_worked:
                    retro["tier_outcome"] = "EXTRA"  # already met but worked more
                else:
                    retro["tier_outcome"] = "CORRECT"
            elif tier == "UNAVAILABLE":
                if actually_worked:
                    retro["tier_outcome"] = "ERROR"  # scheduled despite unavailable
                else:
                    retro["tier_outcome"] = "CORRECT"
            elif tier == "EXEMPT":
                retro["tier_outcome"] = "N/A"
            else:
                retro["tier_outcome"] = "UNKNOWN"
        else:
            retro["actual_worked_memorial"] = None
            retro["tier_outcome"] = "NO_DATA"

        retro_records.append(retro)

    # Summary
    total = len(retro_records)
    with_data = sum(1 for r in retro_records if r["tier_outcome"] != "NO_DATA")
    actually_worked = sum(1 for r in retro_records if r.get("actual_worked_memorial"))
    must_worked = sum(1 for r in retro_records
                      if r.get("tier") == "MUST" and r.get("actual_worked_memorial"))
    must_total = sum(1 for r in retro_records if r.get("tier") == "MUST")
    unavail_errors = sum(1 for r in retro_records if r.get("tier_outcome") == "ERROR")
    pref_overrides = sum(1 for r in retro_records if r.get("tier_outcome") == "OVERRIDE")

    summary = {
        "providers_with_data": with_data,
        "actually_worked_memorial": actually_worked,
        "must_worked": must_worked,
        "must_total": must_total,
        "must_rate": round(must_worked / must_total * 100, 1) if must_total > 0 else 0,
        "unavailable_errors": unavail_errors,
        "preference_overrides": pref_overrides,
    }

    return retro_records, summary
