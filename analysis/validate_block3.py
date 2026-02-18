#!/usr/bin/env python3
"""
Validate the actual Block 3 schedule (from Amion HTML) against all scheduling rules.

Rules source of truth: docs/block-scheduling-rules.md

Scope: Block scheduling assigns providers to WEEKS and WEEKENDS at SITES.
  - Night shift scheduling is OUT OF SCOPE (Section 6.3)
  - Swing shift scheduling is OUT OF SCOPE (Section 6.3)
  - Service-level assignment within sites is OUT OF SCOPE (Section 6.3)
  - Only DAY shifts count toward block demand

Checks (mapped to rules doc sections):
  1. Site eligibility — Section 2.2, 2.3 (HARD)
  2. Site demand — Section 1 Input 3, Section 3.6
  3. Provider site distribution — Section 3.4 (SOFT)
  4. Capacity limits — Section 2.4 (HARD)
  5. Consecutive stretches — Section 3.2 (SOFT), 21-day window
  6. Availability — Section 2.1 (HARD)
  7. Conflict pairs — Section 5.2 (HARD)
  8. Week/weekend same-site pairing — Section 2.5 (HARD)
  9. Single site per week — Section 1.2 (structural)
 10. Holiday rules — Section 4 (Memorial Day)
 11. Swing capacity reservation — Section 1 Input 3
 12. Swap notes catalog

Usage:
    python -m analysis.validate_block3
"""

import json
import math
import os
import re
import sys
from collections import defaultdict
from datetime import date, timedelta

# ── Project root setup ──────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from parse_schedule import parse_schedule, merge_schedules
from name_match import to_canonical, match_provider, clean_html_provider
from block.recalculate_prior_actuals import classify_service, parse_date
from block.engines.shared.loader import (
    load_providers, load_tags, load_sites, load_availability,
    build_name_map, build_periods, get_eligible_sites, has_tag,
    get_tag_rules, SITE_PCT_MAP, PCT_TO_SITES,
)


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

BLOCK_3_START = date(2026, 3, 2)   # Monday
BLOCK_3_END   = date(2026, 6, 28)  # Sunday

BLOCK_3_FILES = ["2026-03.html", "2026-04.html", "2026-05.html", "2026-06.html"]
MONTHLY_DIR = os.path.join(_PROJECT_ROOT, "input", "monthlySchedules")

# Memorial Day is the only holiday in Block 3 (Section 4.5)
MEMORIAL_DAY = date(2026, 5, 25)
# Memorial Day week: Mon May 25 - Fri May 29
MEMORIAL_DAY_WEEK_START = date(2026, 5, 25)
MEMORIAL_DAY_WEEK_END = date(2026, 5, 29)


# ═══════════════════════════════════════════════════════════════════════════
# SERVICE → SITE MAPPING
# ═══════════════════════════════════════════════════════════════════════════

def service_to_site(service_name):
    """Map an Amion service name to a hospital site.

    Based on the Full Included Service List in docs/block-scheduling-rules.md.
    Service names encode the site as a prefix. Services without a site prefix
    are at Cooper.
    """
    sname = service_name.strip()
    slow = sname.lower()

    # ── Virtua sites (check before generic patterns) ──
    if "virtua" in slow:
        if "voorhees" in slow:
            return "Virtua Voorhees"
        if "marlton" in slow:
            return "Virtua Marlton"
        if "mount holly" in slow or "mt holly" in slow or "mt. holly" in slow:
            return "Virtua Mt Holly"
        if "willingboro" in slow:
            return "Virtua Willingboro"
        # "Virtua - Additional" could be any Virtua site
        return "Virtua Voorhees"

    # ── MH+E UM services → Mullica Hill (not Cooper!) ──
    # "MH+ E UM Referrals Weekdays", "MH+ E UM Rounds-PA Advisor Weekdays"
    if slow.startswith("mh+") or slow.startswith("mh +"):
        return "Mullica Hill"

    # ── IMC UM Referrals → Cooper (IMC = Inspira Medical Center, Cooper-based) ──
    if slow.startswith("imc "):
        return "Cooper"

    # ── Cape ──
    if slow.startswith("cape ") or slow == "cape":
        return "Cape"

    # ── Mullica Hill ──
    if "mullica hill" in slow:
        return "Mullica Hill"

    # ── Vineland ──
    if slow.startswith("vineland") or "vineland" in slow:
        return "Vineland"

    # ── Elmer ──
    if slow.startswith("elmer") or " elmer" in slow:
        return "Elmer"

    # ── Mannington / Inspira-Mannington ──
    if "mannington" in slow:
        return "Mannington"

    # ── Bridgeton → under Vineland/Inspira ──
    if slow.startswith("bridgeton"):
        return "Vineland"

    # ── Cooper (default for unqualified services) ──
    # H1-H18, HA-HG, SAH, TAH, (MAH), (NAH), Admitter, Night Direct Care,
    # UM, Hospital Medicine Consults, CCPA
    return "Cooper"


# ═══════════════════════════════════════════════════════════════════════════
# PARSE BLOCK 3 SCHEDULE
# ═══════════════════════════════════════════════════════════════════════════

def parse_block3():
    """Parse Block 3 HTML files into granular assignments.

    Returns all assignments including excluded/night/swing — callers filter.
    Moonlighting shifts are excluded here (Section 1.3 Classification Notes).
    """
    all_months = []
    for fname in BLOCK_3_FILES:
        fpath = os.path.join(MONTHLY_DIR, fname)
        if not os.path.exists(fpath):
            print(f"  WARNING: Missing {fpath}")
            continue
        month_data = parse_schedule(fpath)
        all_months.append(month_data)
        print(f"  Parsed {fname}: {len(month_data['schedule'])} days, "
              f"{len(month_data['services'])} services")

    merged = merge_schedules(all_months)

    assignments = []

    for day in merged["schedule"]:
        raw_date = day["date"]
        try:
            d = parse_date(raw_date)
        except Exception:
            continue

        # Filter to Block 3 date range
        if d < BLOCK_3_START or d > BLOCK_3_END:
            continue

        for a in day["assignments"]:
            provider_raw = a["provider"]
            if not provider_raw:
                continue

            # Clean junk entries (OPEN SHIFT, RESIDENT, etc.)
            cleaned = clean_html_provider(provider_raw)
            if not cleaned:
                continue

            # Classify service (Section 1.3)
            svc_type = classify_service(a["service"], a["hours"])

            # Skip moonlighting (Section 1.3 Classification Notes)
            if a["moonlighting"]:
                continue

            # Map service to site
            site = service_to_site(a["service"])

            canonical = to_canonical(cleaned)

            assignments.append({
                "provider": canonical,
                "html_provider": provider_raw,
                "date": d,
                "day_of_week": day["day_of_week"],
                "service": a["service"],
                "hours": a["hours"],
                "site": site,
                "service_type": svc_type,
                "moonlighting": False,  # already filtered
                "note": a.get("note", ""),
            })

    return assignments


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def get_week_num(d):
    """Return the week number (1-based) within Block 3."""
    delta = (d - BLOCK_3_START).days
    return delta // 7 + 1

def is_weekend_day(d):
    """Saturday=5, Sunday=6 in weekday()."""
    return d.weekday() >= 5

def filter_day_only(assignments):
    """Return only day-shift assignments (block scheduling scope).

    Per Section 6.3: Night scheduling, swing scheduling are OUT OF SCOPE.
    Per Section 1.3: Excluded services don't count.
    """
    return [a for a in assignments if a["service_type"] == "day"]


# ═══════════════════════════════════════════════════════════════════════════
# VALIDATION CHECKS
# ═══════════════════════════════════════════════════════════════════════════

def check_site_eligibility(day_assignments, providers, tags_data):
    """Check 1 — Section 2.2, 2.3 (HARD RULE)

    Providers can only be assigned to sites where pct > 0.
    Tag restrictions (no_elmer, no_vineland) further remove sites.
    """
    violations = []
    for a in day_assignments:
        pname = a["provider"]
        if pname not in providers:
            continue

        eligible = get_eligible_sites(pname, providers[pname], tags_data)
        if a["site"] not in eligible:
            violations.append({
                "provider": pname,
                "date": a["date"],
                "site": a["site"],
                "service": a["service"],
                "eligible_sites": eligible,
                "note": a["note"],
            })
    return violations


def check_site_demand(day_assignments, sites_demand):
    """Check 2 — Input 3, Section 3.6

    Actual day-shift staffing vs expected demand per site per week.
    Site gap tolerance: Tier 0 = 0 gaps, Tier 1 = 1 gap/day, Cooper = gaps expected.
    """
    site_week_staff = defaultdict(set)

    for a in day_assignments:
        wk = get_week_num(a["date"])
        day_type = "weekend" if is_weekend_day(a["date"]) else "weekday"
        site_week_staff[(a["site"], wk, day_type)].add(a["provider"])

    results = []
    total_weeks = (BLOCK_3_END - BLOCK_3_START).days // 7 + 1

    for (site, dtype), needed in sites_demand.items():
        if dtype == "swing":
            continue  # swing out of scope
        for wk in range(1, total_weeks + 1):
            actual = len(site_week_staff.get((site, wk, dtype), set()))
            if actual != needed:
                results.append({
                    "site": site,
                    "week": wk,
                    "day_type": dtype,
                    "expected": needed,
                    "actual": actual,
                    "diff": actual - needed,
                })
    return results


def check_provider_distribution(day_assignments, providers, tags_data):
    """Check 3 — Section 3.4 (SOFT RULE)

    Provider site distribution vs pct targets. ±5-10% flexibility expected.
    Flag deviations > 20%.

    IMPORTANT: Multiple sites share a single pct column in the spreadsheet:
      - pct_inspira_veb → Vineland + Elmer (combined)
      - pct_virtua      → Virtua Voorhees + Marlton + Willingboro + Mt Holly (combined)
      - pct_inspira_mhw → Mullica Hill
      - pct_cooper       → Cooper
      - pct_mannington   → Mannington
      - pct_cape         → Cape

    We must aggregate days across all sites that share a pct column before
    comparing against the target, NOT compare each sub-site individually.
    """
    # Count days per provider per SITE
    prov_site_days = defaultdict(lambda: defaultdict(int))
    prov_total_days = defaultdict(int)

    for a in day_assignments:
        prov_site_days[a["provider"]][a["site"]] += 1
        prov_total_days[a["provider"]] += 1

    # Aggregate by pct_field (site group), not individual site
    results = []
    for pname, site_counts in prov_site_days.items():
        if pname not in providers:
            continue
        pdata = providers[pname]
        total = prov_total_days[pname]
        if total == 0:
            continue

        # Sum days by pct_field (site group)
        group_days = defaultdict(int)       # pct_field -> total days
        group_sites = defaultdict(list)     # pct_field -> [(site, count), ...]
        for site, count in site_counts.items():
            pct_field = SITE_PCT_MAP.get(site)
            if not pct_field:
                continue
            group_days[pct_field] += count
            group_sites[pct_field].append((site, count))

        for pct_field, combined_count in group_days.items():
            actual_pct = combined_count / total
            target_pct = pdata.get(pct_field, 0)

            diff = actual_pct - target_pct
            if abs(diff) > 0.20 and combined_count >= 3:
                # Build a readable site group label
                sites_in_group = group_sites[pct_field]
                if len(sites_in_group) == 1:
                    group_label = sites_in_group[0][0]
                else:
                    # e.g. "Virtua (Voorhees 12, Willingboro 7)"
                    parent = _site_group_name(pct_field)
                    breakdown = ", ".join(f"{s} {c}" for s, c in sorted(sites_in_group))
                    group_label = f"{parent} ({breakdown})"

                results.append({
                    "provider": pname,
                    "site": group_label,
                    "actual_days": combined_count,
                    "total_days": total,
                    "actual_pct": round(actual_pct, 2),
                    "target_pct": target_pct,
                    "diff_pct": round(diff, 2),
                })

    results.sort(key=lambda x: abs(x["diff_pct"]), reverse=True)
    return results


def _site_group_name(pct_field):
    """Human-friendly name for a pct column's site group."""
    return {
        "pct_cooper": "Cooper",
        "pct_inspira_veb": "Inspira VEB (Vineland + Elmer)",
        "pct_inspira_mhw": "Mullica Hill",
        "pct_mannington": "Mannington",
        "pct_virtua": "Virtua (all sites)",
        "pct_cape": "Cape",
    }.get(pct_field, pct_field)


def check_capacity_limits(day_assignments, providers, tags_data, prior_actuals):
    """Check 4 — Annual capacity check (HARD RULE)

    Verify that the total weeks/weekends scheduled across ALL blocks (1+2+3)
    do not exceed the provider's annual allocation.

    prior_weeks_worked + block3_weeks <= annual_weeks
    prior_weekends_worked + block3_weekends <= annual_weekends

    We do NOT check against weeks_remaining — that's for the engine to use
    when building the schedule. We assume the manual scheduler checked that.
    What we're validating is that the annual cap was not exceeded.
    """
    # Count weeks and weekends per provider from Block 3 day assignments
    prov_week_nums = defaultdict(set)  # provider -> set of week_nums with weekday work
    prov_we_nums = defaultdict(set)    # provider -> set of week_nums with weekend work

    for a in day_assignments:
        wk = get_week_num(a["date"])
        if is_weekend_day(a["date"]):
            prov_we_nums[a["provider"]].add(wk)
        else:
            prov_week_nums[a["provider"]].add(wk)

    violations = []
    for pname, pdata in providers.items():
        if has_tag(pname, "do_not_schedule", tags_data):
            continue

        annual_wk = pdata["annual_weeks"]
        annual_we = pdata["annual_weekends"]

        # Skip providers with 0 annual allocation (nocturnists, etc.)
        if annual_wk == 0 and annual_we == 0:
            continue

        # Block 3 actuals (from parsed schedule)
        block3_wk = len(prov_week_nums.get(pname, set()))
        block3_we = len(prov_we_nums.get(pname, set()))

        # Prior actuals (Blocks 1+2 from prior_actuals.json)
        prior = prior_actuals.get(pname, {})
        prior_wk = prior.get("prior_weeks", 0)
        prior_we = prior.get("prior_weekends", 0)

        total_wk = prior_wk + block3_wk
        total_we = prior_we + block3_we

        if total_wk > annual_wk and annual_wk > 0:
            violations.append({
                "provider": pname,
                "type": "weeks",
                "annual": annual_wk,
                "prior_b1b2": prior_wk,
                "block3": block3_wk,
                "total": total_wk,
                "over": round(total_wk - annual_wk, 1),
            })
        if total_we > annual_we and annual_we > 0:
            violations.append({
                "provider": pname,
                "type": "weekends",
                "annual": annual_we,
                "prior_b1b2": prior_we,
                "block3": block3_we,
                "total": total_we,
                "over": round(total_we - annual_we, 1),
            })

    return violations


def check_consecutive_stretches(day_assignments, providers):
    """Check 5 — Section 3.2 (SOFT RULE)

    Rules:
      - Normal: up to 7 consecutive days (Mon-Sun or Mon-Fri)
      - Maximum: 12 consecutive days (Week+WE+Week)
      - NEVER: more than 12 consecutive days
      - 21-day window: max 17 days worked in any 21-day window
    """
    prov_dates = defaultdict(set)
    for a in day_assignments:
        prov_dates[a["provider"]].add(a["date"])

    stretch_violations = []  # > 12 days (HARD violation)
    extended_stretches = []  # 8-12 days (acceptable but notable)
    window_violations = []   # > 17 days in 21-day window

    for pname, dates in prov_dates.items():
        if pname not in providers:
            continue
        sorted_dates = sorted(dates)
        if not sorted_dates:
            continue

        # ── Consecutive stretch check ──
        streak_start = sorted_dates[0]
        streak_end = sorted_dates[0]

        def record_streak(start, end):
            streak_len = (end - start).days + 1
            if streak_len > 12:
                stretch_violations.append({
                    "provider": pname,
                    "start": start,
                    "end": end,
                    "days": streak_len,
                    "severity": "HARD VIOLATION (>12)",
                })
            elif streak_len > 7:
                extended_stretches.append({
                    "provider": pname,
                    "start": start,
                    "end": end,
                    "days": streak_len,
                    "severity": "extended (8-12)",
                })

        for i in range(1, len(sorted_dates)):
            if (sorted_dates[i] - sorted_dates[i-1]).days == 1:
                streak_end = sorted_dates[i]
            else:
                record_streak(streak_start, streak_end)
                streak_start = sorted_dates[i]
                streak_end = sorted_dates[i]
        record_streak(streak_start, streak_end)

        # ── 21-day window check ──
        # Slide a 21-day window across the block and count work days in each
        date_set = set(sorted_dates)
        window_start = BLOCK_3_START
        while window_start + timedelta(days=20) <= BLOCK_3_END:
            days_in_window = sum(
                1 for i in range(21)
                if (window_start + timedelta(days=i)) in date_set
            )
            if days_in_window > 17:
                window_violations.append({
                    "provider": pname,
                    "window_start": window_start,
                    "window_end": window_start + timedelta(days=20),
                    "days_worked": days_in_window,
                })
            window_start += timedelta(days=1)

    return stretch_violations, extended_stretches, window_violations


def check_availability(day_assignments, providers, unavailable_dates, name_map):
    """Check 6 — Section 2.1 (HARD RULE)

    Availability is SACRED. If a provider marks a day as unavailable,
    they are NEVER scheduled that day. No exceptions.
    """
    violations = []
    for a in day_assignments:
        pname = a["provider"]
        if pname not in providers:
            continue

        json_name = name_map.get(pname)
        if json_name is None:
            continue

        unav = unavailable_dates.get(json_name, set())
        date_str = a["date"].strftime("%Y-%m-%d")
        if date_str in unav:
            violations.append({
                "provider": pname,
                "date": a["date"],
                "service": a["service"],
                "site": a["site"],
                "note": a["note"],
            })
    return violations


def check_conflict_pairs(day_assignments, providers):
    """Check 7 — Section 5.2 (HARD RULE)

    Haroldson & McMillian must never be scheduled during the same week
    or weekend at ANY site (not just the same site).
    """
    haroldson = None
    mcmillian = None
    for pname in providers:
        if "HAROLDSON" in pname.upper():
            haroldson = pname
        if "MCMILLIAN" in pname.upper():
            mcmillian = pname

    if not haroldson or not mcmillian:
        return []

    prov_week_sites = defaultdict(lambda: defaultdict(set))
    for a in day_assignments:
        wk = get_week_num(a["date"])
        prov_week_sites[a["provider"]][wk].add(a["site"])

    violations = []
    h_weeks = set(prov_week_sites.get(haroldson, {}).keys())
    m_weeks = set(prov_week_sites.get(mcmillian, {}).keys())
    overlap = h_weeks & m_weeks

    for wk in sorted(overlap):
        violations.append({
            "week": wk,
            "haroldson_sites": sorted(prov_week_sites[haroldson][wk]),
            "mcmillian_sites": sorted(prov_week_sites[mcmillian][wk]),
        })
    return violations


def check_week_weekend_pairing(day_assignments, providers):
    """Check 8 — Section 2.5 (HARD RULE)

    Week + weekend should be at the SAME site. Cross-site only as last resort.
    """
    prov_week_sites = defaultdict(lambda: defaultdict(lambda: {"weekday": set(), "weekend": set()}))

    for a in day_assignments:
        wk = get_week_num(a["date"])
        if is_weekend_day(a["date"]):
            prov_week_sites[a["provider"]][wk]["weekend"].add(a["site"])
        else:
            prov_week_sites[a["provider"]][wk]["weekday"].add(a["site"])

    mismatches = []
    total_pairs = 0
    for pname, weeks in prov_week_sites.items():
        if pname not in providers:
            continue
        for wk, sites in weeks.items():
            if sites["weekday"] and sites["weekend"]:
                total_pairs += 1
                if sites["weekday"] != sites["weekend"]:
                    mismatches.append({
                        "provider": pname,
                        "week": wk,
                        "weekday_sites": sorted(sites["weekday"]),
                        "weekend_sites": sorted(sites["weekend"]),
                    })

    return mismatches, total_pairs


def check_single_site_per_week(day_assignments, providers):
    """Check 9 — Section 1.2 (Structural)

    A provider stays at ONE site for the entire week (Mon-Fri).
    Check if any provider works at multiple sites within the same Mon-Fri period.
    """
    # provider -> week_num -> set of weekday sites
    prov_weekday_sites = defaultdict(lambda: defaultdict(set))

    for a in day_assignments:
        if not is_weekend_day(a["date"]):
            wk = get_week_num(a["date"])
            prov_weekday_sites[a["provider"]][wk].add(a["site"])

    violations = []
    for pname, weeks in prov_weekday_sites.items():
        if pname not in providers:
            continue
        for wk, sites in weeks.items():
            if len(sites) > 1:
                violations.append({
                    "provider": pname,
                    "week": wk,
                    "sites": sorted(sites),
                })

    return violations


def check_holiday_rules(day_assignments, providers, tags_data):
    """Check 10 — Section 4

    Memorial Day (May 25, 2026) is the only Block 3 holiday.
    Memorial Day week: May 25–29.

    Check:
    - Who is working Memorial Day week?
    - Holiday preference violations (providers with Memorial Day as holiday_1/2)
    """
    memorial_workers = set()
    for a in day_assignments:
        if MEMORIAL_DAY_WEEK_START <= a["date"] <= MEMORIAL_DAY_WEEK_END:
            memorial_workers.add(a["provider"])

    # Check providers who listed Memorial Day as a preference
    preference_violations = []
    for pname, pdata in providers.items():
        h1 = pdata.get("holiday_1", "").strip().lower()
        h2 = pdata.get("holiday_2", "").strip().lower()
        if "memorial" in h1 or "memorial" in h2:
            if pname in memorial_workers:
                preference_violations.append({
                    "provider": pname,
                    "preference": "Memorial Day",
                    "status": "WORKING (preference violated)",
                })

    return memorial_workers, preference_violations


def check_swing_reservation(day_assignments, providers, tags_data):
    """Check 11 — Input 3 note on swing shifts

    For swing-tagged providers, the engine must reserve capacity by leaving
    weeks unscheduled. Check if swing providers are over-scheduled.
    """
    # Count weeks assigned per provider
    prov_weeks = defaultdict(set)
    for a in day_assignments:
        if not is_weekend_day(a["date"]):
            wk = get_week_num(a["date"])
            prov_weeks[a["provider"]].add(wk)

    issues = []
    for pname in providers:
        if not has_tag(pname, "swing_shift", tags_data):
            continue
        rules = get_tag_rules(pname, "swing_shift", tags_data)
        weeks_used = len(prov_weeks.get(pname, set()))
        wk_cap = math.floor(providers[pname]["weeks_remaining"])
        issues.append({
            "provider": pname,
            "swing_rule": "; ".join(rules),
            "weeks_assigned": weeks_used,
            "weeks_cap": wk_cap,
        })

    return issues


def extract_swap_notes(assignments):
    """Check 12: Catalog all swap/modification notes from Block 3."""
    swaps = []
    other_notes = []

    for a in assignments:
        note = a.get("note", "").strip()
        if not note:
            continue

        entry = {
            "provider": a["provider"],
            "date": a["date"],
            "service": a["service"],
            "site": a["site"],
            "note": note,
        }

        note_lower = note.lower()
        if any(kw in note_lower for kw in ["swap", "trade", "switch", "payback", "make up", "covering"]):
            swaps.append(entry)
        else:
            other_notes.append(entry)

    return swaps, other_notes


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 100)
    print("BLOCK 3 SCHEDULE VALIDATION (DAY SHIFTS ONLY — per rules doc scope)")
    print(f"Date range: {BLOCK_3_START} to {BLOCK_3_END}")
    print("Night/swing/excluded shifts are filtered out per Section 6.3")
    print("=" * 100)

    # ── Load data ──
    print("\n[1] Loading data...")
    providers = load_providers()
    tags_data = load_tags()
    sites_demand = load_sites()
    unavailable_dates = load_availability()
    name_map, unmatched = build_name_map(providers, unavailable_dates)

    print(f"  Providers: {len(providers)}")
    print(f"  Tags: {sum(len(v) for v in tags_data.values())} tags across {len(tags_data)} providers")
    print(f"  Sites: {len(sites_demand)} demand entries")
    print(f"  Availability: {len(unavailable_dates)} provider JSONs")
    print(f"  Name map: {sum(1 for v in name_map.values() if v)} matched, {len(unmatched)} unmatched")

    # Load prior actuals (Blocks 1+2) for annual capacity check
    prior_actuals_path = os.path.join(_PROJECT_ROOT, "output", "prior_actuals.json")
    with open(prior_actuals_path) as f:
        prior_actuals = json.load(f)
    print(f"  Prior actuals: {len(prior_actuals)} providers (Blocks 1+2)")

    # ── Parse Block 3 ──
    print("\n[2] Parsing Block 3 schedule...")
    all_assignments = parse_block3()

    # Service type breakdown (before filtering)
    type_counts = defaultdict(int)
    for a in all_assignments:
        type_counts[a["service_type"]] += 1
    print(f"  Total assignments: {len(all_assignments)}")
    print(f"  Service types: {dict(type_counts)}")

    # Filter to DAY ONLY for block scheduling validation
    day = filter_day_only(all_assignments)
    providers_seen = set(a["provider"] for a in day)
    sites_seen = set(a["site"] for a in day)

    print(f"\n  DAY SHIFTS (in scope): {len(day)}")
    print(f"  Night shifts (out of scope): {type_counts.get('night', 0)}")
    print(f"  Swing shifts (out of scope): {type_counts.get('swing', 0)}")
    print(f"  Excluded services: {type_counts.get('exclude', 0)}")
    print(f"  Day-shift providers: {len(providers_seen)}")
    print(f"  Sites observed: {sorted(sites_seen)}")

    # ═══════════════════════════════════════════════════════════════════════
    # RUN CHECKS
    # ═══════════════════════════════════════════════════════════════════════

    # ── Check 1: Site eligibility (HARD) ──
    print(f"\n{'=' * 100}")
    print("[CHECK 1] SITE ELIGIBILITY — Section 2.2, 2.3 (HARD RULE)")
    print(f"{'=' * 100}")
    elig_violations = check_site_eligibility(day, providers, tags_data)
    if elig_violations:
        print(f"  VIOLATIONS: {len(elig_violations)}")
        by_prov = defaultdict(list)
        for v in elig_violations:
            by_prov[v["provider"]].append(v)
        for pname, vlist in sorted(by_prov.items()):
            print(f"\n  {pname} ({len(vlist)} violations)")
            print(f"    Eligible sites: {vlist[0]['eligible_sites']}")
            for v in vlist[:5]:
                note_str = f"  NOTE: {v['note']}" if v["note"] else ""
                print(f"    {v['date']} @ {v['site']} — {v['service']}{note_str}")
            if len(vlist) > 5:
                print(f"    ... and {len(vlist) - 5} more")
    else:
        print("  No violations found.")

    # ── Check 2: Site demand ──
    print(f"\n{'=' * 100}")
    print("[CHECK 2] SITE DEMAND — Input 3, Section 3.6 (day shifts only)")
    print(f"{'=' * 100}")
    demand_issues = check_site_demand(day, sites_demand)
    if demand_issues:
        by_site = defaultdict(list)
        for d in demand_issues:
            by_site[(d["site"], d["day_type"])].append(d)

        total_shortfalls = sum(1 for d in demand_issues if d["diff"] < 0)
        total_overfills = sum(1 for d in demand_issues if d["diff"] > 0)
        print(f"  Total weeks with mismatch: {len(demand_issues)}")
        print(f"  Shortfalls: {total_shortfalls}, Overfills: {total_overfills}")

        for (site, dtype), issues in sorted(by_site.items()):
            shortfalls = [d for d in issues if d["diff"] < 0]
            overfills = [d for d in issues if d["diff"] > 0]
            expected = issues[0]["expected"]
            print(f"\n  {site} ({dtype}) — expected {expected}/week")
            print(f"    Shortfalls: {len(shortfalls)} weeks, Overfills: {len(overfills)} weeks")
            for s in shortfalls[:3]:
                print(f"      Week {s['week']}: {s['actual']}/{s['expected']} ({s['diff']:+d})")
            if len(shortfalls) > 3:
                print(f"      ... and {len(shortfalls) - 3} more shortfall weeks")
            for o in overfills[:3]:
                print(f"      Week {o['week']}: {o['actual']}/{o['expected']} ({o['diff']:+d})")
            if len(overfills) > 3:
                print(f"      ... and {len(overfills) - 3} more overfill weeks")
    else:
        print("  All sites perfectly staffed every week.")

    # ── Check 3: Provider site distribution (SOFT) ──
    print(f"\n{'=' * 100}")
    print("[CHECK 3] PROVIDER SITE DISTRIBUTION — Section 3.4 (SOFT, ±5-10% expected)")
    print(f"{'=' * 100}")
    dist_issues = check_provider_distribution(day, providers, tags_data)
    if dist_issues:
        print(f"  Providers with >20% deviation: {len(set(d['provider'] for d in dist_issues))}")
        print(f"\n  {'Provider':<25s} {'Site':<20s} {'Actual':>8s} {'Target':>8s} {'Diff':>8s}  Days")
        print(f"  {'-' * 90}")
        for d in dist_issues[:30]:
            print(f"  {d['provider']:<25s} {d['site']:<20s} {d['actual_pct']:>7.0%} {d['target_pct']:>7.0%} "
                  f"{d['diff_pct']:>+7.0%}  {d['actual_days']}/{d['total_days']}")
        if len(dist_issues) > 30:
            print(f"  ... and {len(dist_issues) - 30} more")
    else:
        print("  All providers within 20% of their target distribution.")

    # ── Check 4: Annual capacity (HARD) ──
    print(f"\n{'=' * 100}")
    print("[CHECK 4] ANNUAL CAPACITY — prior (B1+B2) + block3 <= annual")
    print(f"{'=' * 100}")
    cap_violations = check_capacity_limits(day, providers, tags_data, prior_actuals)
    if cap_violations:
        print(f"  VIOLATIONS: {len(cap_violations)} (total across all blocks exceeds annual)")
        print(f"\n  {'Provider':<25s} {'Type':<10s} {'Annual':>7s} {'B1+B2':>7s} {'B3':>5s} {'Total':>7s} {'Over':>6s}")
        print(f"  {'-' * 75}")
        for v in sorted(cap_violations, key=lambda x: x["over"], reverse=True):
            print(f"  {v['provider']:<25s} {v['type']:<10s} {v['annual']:>7.1f} {v['prior_b1b2']:>7.1f} "
                  f"{v['block3']:>5d} {v['total']:>7.1f} {v['over']:>+6.1f}")
    else:
        print("  No annual capacity violations found.")

    # ── Check 5: Consecutive stretches (SOFT / HARD at 12) ──
    print(f"\n{'=' * 100}")
    print("[CHECK 5] CONSECUTIVE STRETCHES — Section 3.2")
    print(f"{'=' * 100}")
    hard_stretches, extended_stretches, window_violations = check_consecutive_stretches(day, providers)

    if hard_stretches:
        print(f"  HARD VIOLATIONS (>12 consecutive days): {len(hard_stretches)}")
        for v in hard_stretches:
            print(f"    {v['provider']}: {v['start']} to {v['end']} ({v['days']} days)")
    else:
        print("  No >12 consecutive day violations.")

    if extended_stretches:
        print(f"  Extended stretches (8-12 days, acceptable): {len(extended_stretches)}")
        for v in extended_stretches[:10]:
            print(f"    {v['provider']}: {v['start']} to {v['end']} ({v['days']} days)")
        if len(extended_stretches) > 10:
            print(f"    ... and {len(extended_stretches) - 10} more")
    else:
        print("  No extended stretches (8-12 days).")

    if window_violations:
        # Dedupe by provider (report worst window only)
        by_prov = defaultdict(list)
        for v in window_violations:
            by_prov[v["provider"]].append(v)
        print(f"  21-day window violations (>17 days): {len(by_prov)} providers")
        for pname, vlist in sorted(by_prov.items()):
            worst = max(vlist, key=lambda x: x["days_worked"])
            print(f"    {pname}: {worst['days_worked']} days in window "
                  f"{worst['window_start']} to {worst['window_end']}")
    else:
        print("  No 21-day window violations.")

    # ── Check 6: Availability (HARD) ──
    print(f"\n{'=' * 100}")
    print("[CHECK 6] AVAILABILITY — Section 2.1 (HARD RULE)")
    print(f"{'=' * 100}")
    avail_violations = check_availability(day, providers, unavailable_dates, name_map)
    if avail_violations:
        print(f"  VIOLATIONS: {len(avail_violations)}")
        by_prov = defaultdict(list)
        for v in avail_violations:
            by_prov[v["provider"]].append(v)
        for pname, vlist in sorted(by_prov.items()):
            print(f"\n  {pname} ({len(vlist)} violations)")
            for v in vlist[:5]:
                note_str = f"  NOTE: {v['note']}" if v["note"] else ""
                print(f"    {v['date']} @ {v['site']} — {v['service']}{note_str}")
            if len(vlist) > 5:
                print(f"    ... and {len(vlist) - 5} more")
    else:
        print("  No availability violations found.")

    # ── Check 7: Conflict pairs (HARD) ──
    print(f"\n{'=' * 100}")
    print("[CHECK 7] CONFLICT PAIRS — Section 5.2 (HARD RULE)")
    print(f"{'=' * 100}")
    conflict_violations = check_conflict_pairs(day, providers)
    if conflict_violations:
        print(f"  VIOLATIONS: {len(conflict_violations)} weeks with both scheduled")
        for v in conflict_violations:
            print(f"    Week {v['week']}: Haroldson @ {v['haroldson_sites']}, "
                  f"McMillian @ {v['mcmillian_sites']}")
    else:
        print("  No conflict pair violations.")

    # ── Check 8: Week/weekend pairing (HARD) ──
    print(f"\n{'=' * 100}")
    print("[CHECK 8] WEEK/WEEKEND SAME-SITE PAIRING — Section 2.5 (HARD RULE)")
    print(f"{'=' * 100}")
    pairing_mismatches, total_pairs = check_week_weekend_pairing(day, providers)
    matched = total_pairs - len(pairing_mismatches)
    match_pct = (matched / total_pairs * 100) if total_pairs > 0 else 0
    print(f"  Total week+weekend pairs: {total_pairs}")
    print(f"  Same-site pairs: {matched} ({match_pct:.0f}%)")
    print(f"  Cross-site pairs: {len(pairing_mismatches)} ({100 - match_pct:.0f}%)")
    if pairing_mismatches:
        print(f"\n  {'Provider':<25s} {'Week':>4s}  Weekday Sites -> Weekend Sites")
        print(f"  {'-' * 70}")
        for m in pairing_mismatches[:20]:
            print(f"  {m['provider']:<25s} {m['week']:>4d}  "
                  f"{', '.join(m['weekday_sites'])} -> {', '.join(m['weekend_sites'])}")
        if len(pairing_mismatches) > 20:
            print(f"  ... and {len(pairing_mismatches) - 20} more")

    # ── Check 9: Single site per week ──
    print(f"\n{'=' * 100}")
    print("[CHECK 9] SINGLE SITE PER WEEK — Section 1.2")
    print(f"{'=' * 100}")
    multi_site_weeks = check_single_site_per_week(day, providers)
    if multi_site_weeks:
        print(f"  Providers with multiple sites in same week: {len(multi_site_weeks)}")
        for v in multi_site_weeks[:20]:
            print(f"    {v['provider']:<25s} Week {v['week']:>2d}: {', '.join(v['sites'])}")
        if len(multi_site_weeks) > 20:
            print(f"    ... and {len(multi_site_weeks) - 20} more")
    else:
        print("  All providers at a single site per week.")

    # ── Check 10: Holiday rules ──
    print(f"\n{'=' * 100}")
    print("[CHECK 10] HOLIDAY RULES — Section 4 (Memorial Day May 25-29)")
    print(f"{'=' * 100}")
    memorial_workers, pref_violations = check_holiday_rules(day, providers, tags_data)
    print(f"  Providers working Memorial Day week: {len(memorial_workers)}")
    if pref_violations:
        print(f"  Preference violations (listed Memorial Day as preference): {len(pref_violations)}")
        for v in pref_violations:
            print(f"    {v['provider']}: preference={v['preference']}, {v['status']}")
    else:
        print("  No Memorial Day preference violations.")

    # ── Check 11: Swing capacity reservation ──
    print(f"\n{'=' * 100}")
    print("[CHECK 11] SWING SHIFT CAPACITY RESERVATION — Input 3 note")
    print(f"{'=' * 100}")
    swing_issues = check_swing_reservation(day, providers, tags_data)
    if swing_issues:
        print(f"  Swing-tagged providers: {len(swing_issues)}")
        print(f"\n  {'Provider':<25s} {'Rule':<40s} {'Wks Assigned':>12s} {'Wks Cap':>8s}")
        print(f"  {'-' * 90}")
        for s in swing_issues:
            print(f"  {s['provider']:<25s} {s['swing_rule']:<40s} {s['weeks_assigned']:>12d} {s['weeks_cap']:>8d}")
    else:
        print("  No swing-tagged providers found.")

    # ── Check 12: Swap notes ──
    print(f"\n{'=' * 100}")
    print("[CHECK 12] SWAP NOTES AND MODIFICATIONS")
    print(f"{'=' * 100}")
    swaps, other_notes = extract_swap_notes(all_assignments)
    print(f"  Swaps/paybacks/covers/switches: {len(swaps)}")
    print(f"  Other notes: {len(other_notes)}")
    if swaps:
        print(f"\n  SWAP / MODIFICATION NOTES:")
        for s in swaps:
            print(f"    {s['date']} {s['provider']:<25s} @ {s['site']:<20s} — {s['note']}")
    if other_notes:
        print(f"\n  OTHER NOTES:")
        for n in other_notes[:30]:
            print(f"    {n['date']} {n['provider']:<25s} @ {n['site']:<20s} — {n['note']}")
        if len(other_notes) > 30:
            print(f"  ... and {len(other_notes) - 30} more")

    # ═══════════════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 100}")
    print("VALIDATION SUMMARY (day shifts only)")
    print(f"{'=' * 100}")
    print(f"  HARD RULE VIOLATIONS:")
    print(f"    Site eligibility (2.2/2.3):          {len(elig_violations)}")
    print(f"    Annual capacity exceeded:             {len(cap_violations)}")
    print(f"    Availability (2.1):                  {len(avail_violations)}")
    print(f"    Conflict pairs (5.2):                {len(conflict_violations)}")
    print(f"    Consecutive >12 days (3.2):          {len(hard_stretches)}")
    print(f"    Cross-site week/weekend (2.5):       {len(pairing_mismatches)} / {total_pairs}")
    print(f"  SOFT RULE / INFORMATIONAL:")
    print(f"    Site demand mismatches:              {len(demand_issues)}")
    print(f"    Distribution deviations (>20%):      {len(dist_issues)}")
    print(f"    Extended stretches (8-12 days):       {len(extended_stretches)}")
    print(f"    21-day window violations:             {len(set(v['provider'] for v in window_violations))}")
    print(f"    Multi-site weeks:                    {len(multi_site_weeks)}")
    print(f"    Holiday preference violations:       {len(pref_violations)}")
    print(f"    Swing-tagged providers:              {len(swing_issues)}")
    print(f"    Swap/modification notes:             {len(swaps)}")


if __name__ == "__main__":
    main()
