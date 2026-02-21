"""
Holiday Evaluation for V3 Pre-Scheduler (Task 4).

Evaluates holiday obligations, B1+B2 history, preferences, and Memorial Day
readiness. Assigns priority tiers and flags issues.

SCOPE GUARD: Hardcoded for Block 3 of cycle 25-26 (Memorial Day only).
"""

import json
import os
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
from block.engines.v3.prior_actuals_eval import classify_service, parse_date


# ═══════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

# Cycle 25-26 holidays (docs/block-scheduling-rules.md Section 4.1)
HOLIDAYS = {
    "4th of July":    date(2025, 7, 4),
    "Labor Day":      date(2025, 9, 1),
    "Thanksgiving":   date(2025, 11, 27),
    "Christmas Day":  date(2025, 12, 25),
    "New Year's Day": date(2026, 1, 1),
    "Memorial Day":   date(2026, 5, 25),
}

BLOCK_1_HOLIDAYS = ["4th of July", "Labor Day"]
BLOCK_2_HOLIDAYS = ["Thanksgiving", "Christmas Day", "New Year's Day"]
BLOCK_3_HOLIDAYS = ["Memorial Day"]
PRIOR_HOLIDAYS = BLOCK_1_HOLIDAYS + BLOCK_2_HOLIDAYS

# Memorial Day week (Mon-Fri)
MEMORIAL_WEEK_DATES = ["2026-05-25", "2026-05-26", "2026-05-27",
                       "2026-05-28", "2026-05-29"]

# Monthly files covering B1+B2
PRIOR_FILES = [
    "2025-06.html", "2025-07.html", "2025-08.html", "2025-09.html",
    "2025-10.html", "2025-11.html", "2025-12.html",
    "2026-01.html", "2026-02.html",
]

# Site directors (Section 1.4) — capped at 2 holidays/year
SITE_DIRECTOR_NAMES = [
    "MANGOLD, MELISSA", "MCMILLAN, TYLER", "HAROLDSON, KATHRYN",
    "GLICKMAN, CYNTHIA", "GROSS, MICHAEL",
    "OBERDORF, W. ERIC", "OLAYEMI, CHARLTON", "GAMBALE, JOSEPH",
]

TIER_ORDER = {"MUST": 0, "SHOULD": 1, "MET": 2, "UNAVAILABLE": 3, "EXEMPT": 4}


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _has_tag(pname, tag_name, tags_data):
    """Check if a provider has a specific tag."""
    ptags = tags_data.get(pname, [])
    return any(t["tag"] == tag_name for t in ptags)


def is_site_director(pname):
    """Check if provider is a site director (Section 1.4)."""
    pname_upper = pname.upper()
    for sd in SITE_DIRECTOR_NAMES:
        if sd.upper() in pname_upper or pname_upper in sd.upper():
            return True
    return False


def get_holiday_requirement(fte, site_dir):
    """Return number of holidays required per year (Section 4.2).

    >= 0.76 -> 3, 0.50-0.75 -> 2, < 0.50 -> 1
    Site directors -> 2 regardless of FTE
    """
    if site_dir:
        return 2
    if fte >= 0.76:
        return 3
    elif fte >= 0.50:
        return 2
    elif fte > 0:
        return 1
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# SCAN HOLIDAY WORKERS FROM B1+B2
# ═══════════════════════════════════════════════════════════════════════════

def scan_holiday_workers(schedules_dir):
    """Parse B1+B2 schedules and find who worked each prior holiday.

    Args:
        schedules_dir: path to monthly HTML schedule directory

    Returns:
        dict: holiday_name -> set of canonical provider names
    """
    all_months = []
    for fname in PRIOR_FILES:
        fpath = os.path.join(schedules_dir, fname)
        if not os.path.exists(fpath):
            continue
        month_data = parse_schedule(fpath)
        all_months.append(month_data)

    if not all_months:
        return {name: set() for name in PRIOR_HOLIDAYS}

    merged = merge_schedules(all_months)

    # Pre-classify all services
    service_classes = {}
    for s in merged.get("services", []):
        service_classes[s["name"]] = classify_service(s["name"], s["hours"])

    holiday_workers = {name: set() for name in PRIOR_HOLIDAYS}

    for day_entry in merged["schedule"]:
        d = parse_date(day_entry["date"])
        if d is None:
            continue

        # Check if this date is a holiday
        matched_holiday = None
        for hol_name in PRIOR_HOLIDAYS:
            if d == HOLIDAYS[hol_name]:
                matched_holiday = hol_name
                break
        if matched_holiday is None:
            continue

        for assignment in day_entry["assignments"]:
            provider = clean_html_provider(assignment["provider"])
            if not provider:
                continue
            if assignment.get("moonlighting", False):
                continue

            svc_class = service_classes.get(
                assignment["service"],
                classify_service(assignment["service"],
                                 assignment.get("hours", ""))
            )
            if svc_class == "exclude":
                continue

            canonical = to_canonical(provider)
            holiday_workers[matched_holiday].add(canonical)

    return holiday_workers


# ═══════════════════════════════════════════════════════════════════════════
# AVAILABILITY LOADING
# ═══════════════════════════════════════════════════════════════════════════

def _load_availability(availability_dir):
    """Load individual schedule JSONs for Memorial Day week availability.

    Returns:
        dict: json_name -> set of unavailable date strings
    """
    availability = {}
    if not os.path.isdir(availability_dir):
        return availability

    for fname in os.listdir(availability_dir):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(availability_dir, fname)
        try:
            with open(fpath) as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        name = data.get("name", "").strip()
        if not name:
            continue

        if name not in availability:
            availability[name] = set()

        for day in data.get("days", []):
            if day.get("status") == "unavailable":
                availability[name].add(day["date"])

    return availability


def _check_memorial_availability(pname, availability, provider_names_json):
    """Check if provider is available during Memorial Day week.

    Uses match_provider to resolve Excel name → JSON name.
    """
    matched = match_provider(pname, provider_names_json)
    if matched is None:
        return True  # no availability data = assume available

    unavail = availability.get(matched, set())
    return not any(d in unavail for d in MEMORIAL_WEEK_DATES)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN EVALUATION
# ═══════════════════════════════════════════════════════════════════════════

def evaluate_holidays(providers, tags_data, schedules_dir, availability_dir):
    """Evaluate holiday obligations, history, and Memorial Day readiness.

    Args:
        providers: dict from load_providers_from_excel
        tags_data: dict from load_tags_from_excel
        schedules_dir: path to B1+B2 monthly HTML schedule directory
        availability_dir: path to individual availability JSONs

    Returns:
        dict with keys:
            records: list of per-provider holiday records sorted by tier
            holiday_workers: dict holiday_name -> list of provider names
            issues: dict of issue type -> list of records
            supply_vs_demand: dict with must/should counts
            summary: dict of counts
    """
    # Scan B1+B2 for holiday workers
    holiday_workers = scan_holiday_workers(schedules_dir)

    # Load availability for Memorial Day
    availability = _load_availability(availability_dir)
    json_names = list(availability.keys())

    # Build per-provider records
    records = []

    for pname, pdata in sorted(providers.items()):
        if _has_tag(pname, "do_not_schedule", tags_data):
            continue

        fte = pdata.get("fte", 0)
        annual_wk = pdata.get("annual_weeks", 0)
        annual_we = pdata.get("annual_weekends", 0)

        # Skip providers with 0 allocation (nocturnists, etc.)
        if annual_wk == 0 and annual_we == 0:
            continue

        site_dir = is_site_director(pname)
        required = get_holiday_requirement(fte, site_dir)

        # Which holidays did they work in B1+B2?
        pname_canon = to_canonical(pname)
        holidays_worked = []
        for hol_name in PRIOR_HOLIDAYS:
            if pname_canon in holiday_workers.get(hol_name, set()):
                holidays_worked.append(hol_name)

        still_owe = max(0, required - len(holidays_worked))

        # Preferences
        h1 = pdata.get("holiday_1", "").strip()
        h2 = pdata.get("holiday_2", "").strip()
        preferences = [h for h in [h1, h2] if h]
        memorial_is_preference = any(
            "memorial" in p.lower() for p in preferences
        )

        # Christmas / New Year's analysis
        worked_christmas = "Christmas Day" in holidays_worked
        worked_new_years = "New Year's Day" in holidays_worked

        # Preference violations in B1+B2
        prefs_violated = []
        for p in preferences:
            p_lower = p.lower()
            for hol_name in PRIOR_HOLIDAYS:
                if hol_name.lower() in p_lower or p_lower in hol_name.lower():
                    if hol_name in holidays_worked:
                        prefs_violated.append(hol_name)
                    break

        # Memorial Day availability
        mem_available = _check_memorial_availability(pname, availability, json_names)

        records.append({
            "provider": pname,
            "fte": fte,
            "is_site_director": site_dir,
            "required": required,
            "holidays_worked": holidays_worked,
            "count_worked": len(holidays_worked),
            "still_owe": still_owe,
            "preferences": preferences,
            "memorial_is_preference": memorial_is_preference,
            "worked_christmas": worked_christmas,
            "worked_new_years": worked_new_years,
            "worked_both_xmas_ny": worked_christmas and worked_new_years,
            "worked_neither_xmas_ny": not worked_christmas and not worked_new_years,
            "prefs_violated": prefs_violated,
            "mem_available": mem_available,
            "tier": None,
            "priority": None,
            "tier_reason": "",
        })

    # Assign Memorial Day priority tiers
    _compute_memorial_day_priority(records)

    # Detect issues
    issues = _detect_issues(records)

    # Holiday workers as lists (for JSON serialization)
    hw_lists = {k: sorted(list(v)) for k, v in holiday_workers.items()}

    # Supply vs demand
    must_count = sum(1 for r in records if r["tier"] == "MUST")
    should_count = sum(1 for r in records if r["tier"] == "SHOULD")

    supply_vs_demand = {
        "must_count": must_count,
        "should_count": should_count,
        "total_available_must_should": must_count + should_count,
    }

    # Summary
    by_tier = defaultdict(int)
    for r in records:
        by_tier[r["tier"]] += 1

    total_issues = sum(len(v) for v in issues.values())
    issue_counts = {k: len(v) for k, v in issues.items()}

    summary = {
        "total_eligible": len(records),
        "by_tier": dict(by_tier),
        "total_issues": total_issues,
        "issue_counts": issue_counts,
    }

    return {
        "records": records,
        "holiday_workers": hw_lists,
        "issues": issues,
        "supply_vs_demand": supply_vs_demand,
        "summary": summary,
    }


def _compute_memorial_day_priority(records):
    """Assign Memorial Day priority tier to each provider."""
    for r in records:
        if r["required"] == 0:
            r["tier"] = "EXEMPT"
            r["priority"] = 97
            r["tier_reason"] = "No holiday requirement"
            continue

        if r["still_owe"] == 0:
            r["tier"] = "MET"
            r["priority"] = 90
            r["tier_reason"] = (
                f"Already worked {r['count_worked']}/{r['required']} holidays"
            )
            continue

        if not r["mem_available"]:
            r["tier"] = "UNAVAILABLE"
            r["priority"] = 95
            r["tier_reason"] = "Unavailable Memorial Day week"
            continue

        if not r["memorial_is_preference"]:
            r["tier"] = "MUST"
            r["priority"] = 10 - r["still_owe"]
            parts = [f"Owes {r['still_owe']} more holiday(s)"]
            if r["worked_neither_xmas_ny"]:
                parts.append("worked neither Christmas nor New Year's")
            r["tier_reason"] = "; ".join(parts)
        else:
            r["tier"] = "SHOULD"
            r["priority"] = 20 - r["still_owe"]
            r["tier_reason"] = (
                f"Owes {r['still_owe']} more holiday(s), but Memorial Day "
                f"is a preference. May need to override."
            )

    # Sort by tier then priority
    records.sort(key=lambda r: (TIER_ORDER.get(r["tier"], 99), r["priority"]))


def _detect_issues(records):
    """Detect holiday-related issues."""
    issues = {
        "overworked": [],
        "both_xmas_ny": [],
        "neither_xmas_ny": [],
        "pref_violated": [],
        "impossible": [],
    }

    for r in records:
        if r["count_worked"] > r["required"] and r["required"] > 0:
            issues["overworked"].append(r)

        if r["worked_both_xmas_ny"]:
            issues["both_xmas_ny"].append(r)

        if (r["worked_neither_xmas_ny"] and r["required"] > 0
                and r["still_owe"] > 0):
            issues["neither_xmas_ny"].append(r)

        if r["prefs_violated"]:
            issues["pref_violated"].append(r)

        if r["still_owe"] >= 2:
            issues["impossible"].append(r)

    return issues
