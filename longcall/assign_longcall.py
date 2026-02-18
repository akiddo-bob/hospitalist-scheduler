#!/usr/bin/env python3
"""
Hospitalist Scheduler — Long Call Assignment Engine
Reads parsed schedule data and assigns long call shifts according to rules
defined in longcall_rules.md.
"""

import copy
import hashlib
import json
import os
import random
import uuid
import networkx as nx
from datetime import datetime, timedelta
from collections import defaultdict

# ============================================================
# CONFIGURATION
# ============================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
CONFIG_FILE = os.path.join(PROJECT_ROOT, "config.json")

# Load config from config.json (contains PII like excluded provider names)
def _load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {}

_config = _load_config()
_lc_config = _config.get("longcall", {})

# Global settings
HOLIDAYS = [
    datetime.strptime(d, "%Y-%m-%d") for d in _config.get("holidays", ["2026-05-25"])
]
EXCLUDED_PROVIDERS = _config.get("excluded_providers", [])

# Long call settings
BLOCK_START = datetime.strptime(_lc_config.get("block_start", "2026-03-02"), "%Y-%m-%d")
BLOCK_END = datetime.strptime(_lc_config.get("block_end", "2026-06-28"), "%Y-%m-%d")

TEACHING_SERVICES = _lc_config.get("teaching_services", [
    "HA", "HB", "HC", "HD", "HE", "HF", "HG", "HM (Family Medicine)",
])

DIRECT_CARE_SERVICES = _lc_config.get("direct_care_services", [
    "H1", "H2", "H3", "H4", "H5", "H6", "H7",
    "H8- Pav 6 & EXAU", "H9", "H10", "H11",
    "H12- Pav 8 & Pav 9", "H13- (Obs overflow)", "H14",
    "H15", "H16", "H17", "H18",
])

ALL_SOURCE_SERVICES = TEACHING_SERVICES + DIRECT_CARE_SERVICES

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
INPUT_FILE = os.path.join(OUTPUT_DIR, "all_months_schedule.json")

# Seed for reproducibility
random.seed(42)


# ============================================================
# DATA LOADING
# ============================================================

def load_schedule():
    """Load the parsed schedule JSON."""
    with open(INPUT_FILE, 'r') as f:
        return json.load(f)


def parse_date(date_str):
    """Parse date string like '3/2/2026' into datetime."""
    parts = date_str.split('/')
    if len(parts) == 3:
        return datetime(int(parts[2]), int(parts[0]), int(parts[1]))
    return None


def is_in_block(dt):
    """Check if a date is within the block period."""
    return BLOCK_START <= dt <= BLOCK_END


def is_weekend(dt):
    """Check if a date is Saturday (5) or Sunday (6)."""
    return dt.weekday() >= 5


def is_holiday(dt):
    """Check if a date is a holiday."""
    return dt in HOLIDAYS


def is_weekend_or_holiday(dt):
    """Check if a date should be treated as weekend (weekend or holiday)."""
    return is_weekend(dt) or is_holiday(dt)


def get_week_number(dt):
    """Get the ISO week number for grouping into weeks.
    We use Monday as start of week."""
    return dt.isocalendar()[1]


def day_name(dt):
    """Get short day name."""
    return dt.strftime('%a')


VARIATION_SEED = uuid.uuid4().hex[:8]  # random seed each run; override before calling assign_long_calls

def tiebreak_hash(provider, context=""):
    """Deterministic but fair tiebreaker. Returns a float 0-1 based on a hash
    of the provider name + context string. Using a context (like a date or week)
    ensures the same provider doesn't always win or lose ties — the ordering
    rotates across different contexts."""
    h = hashlib.md5(f"{VARIATION_SEED}|{provider}|{context}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


# ============================================================
# STEP 1: BUILD DAILY PROVIDER DATA
# ============================================================

def build_daily_data(schedule_data):
    """
    Build a dict: date -> list of {provider, service, category, moonlighting}
    Only includes providers on source services within the block.
    """
    daily = {}

    for day_entry in schedule_data["schedule"]:
        dt = parse_date(day_entry["date"])
        if dt is None or not is_in_block(dt):
            continue

        providers_today = []
        for assignment in day_entry["assignments"]:
            service = assignment["service"]
            provider = assignment["provider"]

            if not provider or provider == "OPEN SHIFT":
                continue

            if service not in ALL_SOURCE_SERVICES:
                continue

            # Determine category
            if service in TEACHING_SERVICES:
                category = "teaching"
            else:
                category = "direct_care"

            providers_today.append({
                "provider": provider,
                "service": service,
                "category": category,
                "moonlighting": assignment.get("moonlighting", False),
            })

        daily[dt] = providers_today

    return daily


def build_all_daily_data(schedule_data):
    """
    Build a dict: date -> list of {provider, service, moonlighting}
    Includes ALL services (not just source services) within the block.
    Used for determining stretch boundaries — a provider working on UM one week
    and H5 the next is really one continuous stretch, not two separate ones.
    """
    daily = {}

    for day_entry in schedule_data["schedule"]:
        dt = parse_date(day_entry["date"])
        if dt is None or not is_in_block(dt):
            continue

        if dt not in daily:
            daily[dt] = []

        for assignment in day_entry["assignments"]:
            service = assignment["service"]
            provider = assignment["provider"]

            if not provider or provider == "OPEN SHIFT":
                continue

            # Determine if this is a source service
            is_source = service in ALL_SOURCE_SERVICES
            if is_source and service in TEACHING_SERVICES:
                category = "teaching"
            elif is_source:
                category = "direct_care"
            else:
                category = "non_source"

            daily[dt].append({
                "provider": provider,
                "service": service,
                "category": category,
                "is_source": is_source,
                "moonlighting": assignment.get("moonlighting", False),
            })

    return daily


# ============================================================
# STEP 2: IDENTIFY WORK STRETCHES
# ============================================================

def identify_stretches(daily_data):
    """
    For each provider, identify their consecutive work stretches on
    SOURCE services only. Non-source services are invisible.

    Returns: dict of provider -> list of stretches
    Each stretch is a list of dates (sorted).
    """
    # Collect all dates each provider works on source services
    provider_dates = defaultdict(set)
    for dt, providers in daily_data.items():
        for p in providers:
            provider_dates[p["provider"]].add(dt)

    # Now find consecutive stretches
    provider_stretches = {}
    for provider, dates in provider_dates.items():
        sorted_dates = sorted(dates)
        stretches = []
        current_stretch = [sorted_dates[0]]

        for i in range(1, len(sorted_dates)):
            if (sorted_dates[i] - sorted_dates[i-1]).days == 1:
                current_stretch.append(sorted_dates[i])
            else:
                stretches.append(current_stretch)
                current_stretch = [sorted_dates[i]]
        stretches.append(current_stretch)

        provider_stretches[provider] = stretches

    return provider_stretches


def is_standalone_weekend(stretch):
    """
    A standalone weekend is a stretch of consecutive source-service days
    that contains NO weekdays (Mon-Fri). It's only Sat/Sun/holidays.
    Standalone weekends are NOT stretches — they're a separate category.
    """
    return all(is_weekend_or_holiday(dt) for dt in stretch)


def split_stretch_into_weeks(stretch):
    """
    Split a stretch into week-sized chunks aligned to ISO week boundaries.
    Each chunk contains dates from the same ISO week (Mon-Sun).
    If a stretch spans multiple ISO weeks, each ISO week becomes its own chunk.

    IMPORTANT: If the first chunk is a weekend-only fragment (Sat/Sun at the
    end of an ISO week) and the stretch continues into the next ISO week,
    the leading weekend is merged into the following week's chunk. This
    prevents a leading weekend from being orphaned as a fake "standalone
    weekend" when the provider's actual stretch is Sat-Sun-Mon-...-Fri.
    The merged chunk uses the ISO week of the weekday portion so it groups
    correctly in Phase 2.

    Returns list of sub-stretches (each a list of dates).
    """
    if not stretch:
        return []

    weeks = []
    current_week = [stretch[0]]
    current_iso = (stretch[0].isocalendar()[0], stretch[0].isocalendar()[1])

    for i in range(1, len(stretch)):
        dt = stretch[i]
        dt_iso = (dt.isocalendar()[0], dt.isocalendar()[1])
        if dt_iso != current_iso:
            weeks.append(current_week)
            current_week = [dt]
            current_iso = dt_iso
        else:
            current_week.append(dt)

    weeks.append(current_week)

    # Merge a leading weekend-only fragment into the next chunk.
    # A stretch like [Sat, Sun, Mon, Tue, Wed, Thu, Fri] gets split into
    # [Sat, Sun] (ISO week N) and [Mon...Fri] (ISO week N+1). The leading
    # [Sat, Sun] looks like a standalone weekend but it's really the start
    # of a 7-day stretch. Merge it forward so the provider gets one
    # assignment need for the full Sat-Fri stretch instead of a deprioritized
    # standalone weekend + a separate Mon-Fri need.
    if len(weeks) >= 2:
        first_chunk = weeks[0]
        if all(is_weekend_or_holiday(d) for d in first_chunk):
            # Merge first chunk into second chunk
            weeks[1] = first_chunk + weeks[1]
            weeks = weeks[1:]

    # Merge a trailing holiday/weekend-only fragment into the previous chunk.
    # A stretch like [Mon...Sun, Holiday-Mon] gets split at ISO week boundary
    # leaving [Holiday-Mon] orphaned. Merge it back into the prior chunk.
    if len(weeks) >= 2:
        last_chunk = weeks[-1]
        if all(is_weekend_or_holiday(d) for d in last_chunk):
            weeks[-2] = weeks[-2] + last_chunk
            weeks = weeks[:-1]

    return weeks


# ============================================================
# STEP 3: CHECK MOONLIGHTING
# ============================================================

def is_moonlighting_in_stretch(provider, stretch, daily_data):
    """Check if a provider is moonlighting on any day in their stretch."""
    for dt in stretch:
        if dt in daily_data:
            for p in daily_data[dt]:
                if p["provider"] == provider and p["moonlighting"]:
                    return True
    return False


# ============================================================
# STEP 4: COUNT WEEKENDS WORKED
# ============================================================

def count_weekends_in_block(provider, daily_data, exclude_moonlighting=True):
    """Count total weekends (Sat-Sun pairs) a provider works in the block.
    A weekend counts as 1 if the provider works on Saturday, Sunday, or both
    of the same weekend. Excludes weekends where the provider is moonlighting."""
    weekend_saturdays = set()
    for dt, providers in daily_data.items():
        if not is_weekend(dt):
            continue
        for p in providers:
            if p["provider"] == provider:
                if exclude_moonlighting and p["moonlighting"]:
                    continue
                # Normalize to Saturday of that weekend
                if dt.weekday() == 5:  # Saturday
                    weekend_saturdays.add(dt)
                elif dt.weekday() == 6:  # Sunday
                    weekend_saturdays.add(dt - timedelta(days=1))
    return len(weekend_saturdays)


# ============================================================
# STEP 5: GET PROVIDER CATEGORY ON A GIVEN DAY
# ============================================================

def get_provider_category(provider, dt, daily_data):
    """Get whether provider is on teaching or direct_care on a given day."""
    if dt in daily_data:
        for p in daily_data[dt]:
            if p["provider"] == provider:
                return p["category"]
    return None


# ============================================================
# STRETCH UTILITIES
# ============================================================

def find_provider_stretch_for_date(provider, dt, all_stretches):
    """
    Find the stretch (list of dates) that contains dt for a given provider.
    Returns the stretch (list of dates) or None.
    """
    stretches = all_stretches.get(provider, [])
    for stretch in stretches:
        if dt in stretch:
            return stretch
    return None


def stretch_has_weekday_and_weekend(stretch):
    """
    Check if a stretch contains both weekday and weekend/holiday days.
    Returns True only if the stretch has at least one weekday AND at least
    one weekend or holiday day.
    """
    has_weekday = any(not is_weekend_or_holiday(d) for d in stretch)
    has_weekend = any(is_weekend_or_holiday(d) for d in stretch)
    return has_weekday and has_weekend


# ============================================================
# SCORING FUNCTIONS
# ============================================================

def find_best_assignment(provider, eligible_dates, daily_data, assignments,
                         daily_slots, pstate, weekends_worked):
    """
    Find the best (date, slot) for a provider on a weekday.
    Returns (date, slot) or None.
    """
    candidates = []
    category = None

    for dt in eligible_dates:
        cat = get_provider_category(provider, dt, daily_data)
        if cat:
            category = cat

        for slot in daily_slots.get(dt, []):
            if assignments[dt][slot] is not None:
                continue  # slot already filled

            # Check slot compatibility
            if slot == "teaching" and category == "direct_care":
                continue
            elif slot == "teaching" and category == "teaching":
                priority = 0  # best match
            elif slot in ("dc1", "dc2") and category == "teaching":
                priority = 5  # teaching overflow into DC
            elif slot in ("dc1", "dc2") and category == "direct_care":
                priority = 1  # good match
            else:
                priority = 10

            # Weekend slots are pre-assigned by Phase 1.
            if is_weekend_or_holiday(dt):
                continue

            # Day of week variety
            dow = dt.weekday()
            same_dow_count = pstate[provider]["day_of_week"].count(dow)
            priority += same_dow_count * 3

            # DC1 vs DC2 balance
            if slot == "dc1" and pstate[provider]["dc1_count"] > pstate[provider]["dc2_count"]:
                priority += 2
            elif slot == "dc2" and pstate[provider]["dc2_count"] > pstate[provider]["dc1_count"]:
                priority += 2

            candidates.append((priority, dt, slot))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], tiebreak_hash(provider, x[1].strftime('%Y%m%d') + x[2])))
    best = candidates[0]
    return (best[1], best[2])


def find_double_filler(dt, slot, daily_data, assignments, daily_slots,
                       pstate, weekends_worked, all_stretches,
                       provider_wknd_lc_stretches):
    """
    Find a provider to take a double long call to fill an empty slot.

    Hard rules (provider is excluded if violated):
    1. Must have 2+ calendar days gap between the two LCs in a stretch (prefer 3+).
    2. No double allowed if the stretch already contains a holiday LC.
    3. For mixed stretches: never create two-weekday double.
    4. Never exceed 2 LCs per stretch.
    5. For multi-week stretches: don't exceed LCs >= weeks worked.

    Sorting priority:
    1. has_empty_week: prefer providers who have at least one week with no LC
    2. split_tier: prefer weekday+weekend splits (0) over no-split (1) over same-type (2)
    3. score: fairness score (missed priority, double penalty, total LCs)
    """
    if dt not in daily_data:
        return None

    is_wknd = is_weekend_or_holiday(dt)

    candidates = []
    for p in daily_data[dt]:
        provider = p["provider"]
        if provider in EXCLUDED_PROVIDERS:
            continue
        if p["moonlighting"]:
            continue

        # DC providers cannot fill teaching slots
        if slot == "teaching" and p["category"] == "direct_care":
            continue

        # Check if already assigned today
        already_today = any(
            assignments[dt].get(s) == provider
            for s in ["teaching", "dc1", "dc2"]
        )
        if already_today:
            continue

        # Provider must be in a stretch
        stretch = find_provider_stretch_for_date(provider, dt, all_stretches)
        if stretch is None:
            continue

        is_mixed_stretch = stretch_has_weekday_and_weekend(stretch)

        # Check existing LCs in this same stretch
        existing_lc_dates = []
        for s_dt in stretch:
            for s_slot in ["teaching", "dc1", "dc2"]:
                if assignments.get(s_dt, {}).get(s_slot) == provider:
                    existing_lc_dates.append(s_dt)

        # For multi-week stretches, don't give more LCs than weeks
        stretch_weeks = split_stretch_into_weeks(stretch)
        real_weeks = [w for w in stretch_weeks
                      if not is_moonlighting_in_stretch(provider, w, daily_data)
                      and not (all(is_weekend_or_holiday(d) for d in w) and len(w) <= 2)]
        num_weeks = len(real_weeks)
        if num_weeks >= 2 and len(existing_lc_dates) >= num_weeks:
            continue
        # Never exceed 2 LCs total per stretch
        if len(existing_lc_dates) >= 2:
            continue

        # RULE: No double if stretch already has a holiday LC
        if any(is_holiday(d) for d in existing_lc_dates):
            continue
        if is_holiday(dt) and existing_lc_dates:
            continue

        # RULE: Minimum 2-day gap (hard), prefer 3+ (soft)
        min_gap = 999
        if existing_lc_dates:
            min_gap = min(abs((dt - d).days) for d in existing_lc_dates)
            if min_gap < 2:
                continue

        existing_has_weekday_lc = any(not is_weekend_or_holiday(d) for d in existing_lc_dates)
        existing_has_weekend_lc = any(is_weekend_or_holiday(d) for d in existing_lc_dates)

        # Determine split quality tier
        if not existing_lc_dates:
            split_tier = 1  # first assignment in stretch
        elif is_wknd and existing_has_weekday_lc and not existing_has_weekend_lc:
            split_tier = 0  # adding weekend to existing weekday = good split
        elif not is_wknd and existing_has_weekend_lc and not existing_has_weekday_lc:
            split_tier = 0  # adding weekday to existing weekend = good split
        elif is_wknd and existing_has_weekend_lc:
            continue  # HARD FILTER: never create two-weekend doubles
        elif not is_wknd and existing_has_weekday_lc:
            if is_mixed_stretch:
                continue  # HARD FILTER: never two-weekday double in mixed stretch
            else:
                split_tier = 2  # weekday+weekday in weekday-only stretch = fallback
        else:
            split_tier = 1

        gap_penalty = 0 if min_gap >= 3 else 500

        # Get provider state early (needed for offset check and scoring)
        prov_state = pstate[provider]

        # Hard max: 2 weekend LCs per provider
        if is_wknd and prov_state["weekend_lc"] >= 2:
            continue

        # Count eligible stretches (non-standalone-weekend)
        provider_stretches = all_stretches.get(provider, [])
        eligible_stretches = [st for st in provider_stretches
                              if not is_standalone_weekend(st)]

        # RULE: doubles should be offset by a no-LC stretch.
        # After adding this double the provider's total LCs should leave
        # room for at least one eligible stretch with no LC.
        # Strong preference (top sort key) — only relaxed if no one qualifies.
        total_after_double = prov_state["lc_count"] + 1
        can_offset = 0 if total_after_double <= len(eligible_stretches) - 1 else 1

        # Check whether provider currently has an empty-stretch week
        all_lc_dates = set()
        for st in provider_stretches:
            for st_dt in st:
                for st_slot in ["teaching", "dc1", "dc2"]:
                    if assignments.get(st_dt, {}).get(st_slot) == provider:
                        all_lc_dates.add(st_dt)
        found_empty_week = False
        for st in eligible_stretches:
            weeks = split_stretch_into_weeks(st)
            for week in weeks:
                if not any(wd in all_lc_dates for wd in week):
                    found_empty_week = True
                    break
            if found_empty_week:
                break
        has_empty_week = 0 if found_empty_week else 1

        # Score: prefer those who missed, then fewest doubles, then fewest total
        missed = prov_state["missed"]
        doubles = prov_state["doubles"]
        total = prov_state["lc_count"]

        weekend_penalty = 1000 if (is_wknd and prov_state["weekend_lc"] >= 1) else 0
        low_weekend_penalty = 200 if weekends_worked.get(provider, 0) <= 1 else 0

        score = (-missed * 100) + (doubles * 50) + total + weekend_penalty + low_weekend_penalty + gap_penalty

        candidates.append((
            can_offset, has_empty_week, split_tier, score,
            tiebreak_hash(provider, dt.strftime('%Y%m%d')),
            provider
        ))

    if not candidates:
        return None

    candidates.sort()
    return candidates[0][5]


# ============================================================
# MAIN ASSIGNMENT ENGINE
# ============================================================

def assign_long_calls(daily_data, all_daily_data=None):
    """
    Main assignment engine. Returns:
    - assignments: dict date -> {teaching: provider, dc1: provider, dc2: provider}
    - flags: list of {date, provider, flag_type, message}
    - provider_stats: dict provider -> stats

    all_daily_data: kept for compatibility (used by report for display only).
    Stretches are always based on source-service days only.
    """

    # --------------------------------------------------------
    # PHASE 0: Data Preparation
    # --------------------------------------------------------

    all_stretches = identify_stretches(daily_data)

    # Build assignment needs: one per provider per weekday-work-week
    assignment_needs = []
    for provider, stretches in all_stretches.items():
        if provider in EXCLUDED_PROVIDERS:
            continue
        for stretch in stretches:
            standalone_wknd = is_standalone_weekend(stretch)
            if standalone_wknd:
                if is_moonlighting_in_stretch(provider, stretch, daily_data):
                    continue
                assignment_needs.append({
                    "provider": provider,
                    "week_dates": stretch,
                    "standalone_weekend": True,
                })
            else:
                weeks = split_stretch_into_weeks(stretch)
                for week in weeks:
                    if is_moonlighting_in_stretch(provider, week, daily_data):
                        continue
                    assignment_needs.append({
                        "provider": provider,
                        "week_dates": week,
                        "standalone_weekend": False,
                    })

    # Total non-moonlighting weeks per provider (standalone weekends don't count)
    provider_total_weeks = defaultdict(int)
    for provider, stretches in all_stretches.items():
        if provider in EXCLUDED_PROVIDERS:
            continue
        for stretch in stretches:
            if is_standalone_weekend(stretch):
                continue
            weeks = split_stretch_into_weeks(stretch)
            for week in weeks:
                if not is_moonlighting_in_stretch(provider, week, daily_data):
                    provider_total_weeks[provider] += 1

    # Initialize assignments
    assignments = {}
    all_dates = sorted(daily_data.keys())
    for dt in all_dates:
        assignments[dt] = {"teaching": None, "dc1": None, "dc2": None}

    # Consolidated provider state
    pstate = defaultdict(lambda: {
        "lc_count": 0,
        "weekend_lc": 0,
        "dc1_count": 0,
        "dc2_count": 0,
        "day_of_week": [],
        "missed": 0,
        "no_lc_weeks": 0,
        "doubles": 0,
        "consecutive_no_lc": 0,
        "wknd_lc_stretches": set(),
    })

    flags = []

    # Pre-compute weekends worked
    all_providers = set()
    for dt, providers in daily_data.items():
        for p in providers:
            all_providers.add(p["provider"])
    weekends_worked = {}
    for provider in all_providers:
        weekends_worked[provider] = count_weekends_in_block(provider, daily_data)

    # Daily slot needs
    daily_slots = {}
    for dt in all_dates:
        if is_weekend_or_holiday(dt):
            daily_slots[dt] = ["teaching", "dc2"]
        else:
            daily_slots[dt] = ["teaching", "dc1", "dc2"]

    # Group assignment needs by ISO week
    week_groups = defaultdict(list)
    for need in assignment_needs:
        weekday_dates = [d for d in need["week_dates"] if not is_weekend_or_holiday(d)]
        key_day = weekday_dates[0] if weekday_dates else need["week_dates"][0]
        week_key = (key_day.isocalendar()[0], key_day.isocalendar()[1])
        week_groups[week_key].append(need)

    # --------------------------------------------------------
    # PHASE 1: Advisory Weekend Matching (Bipartite)
    # --------------------------------------------------------

    weekend_dates = sorted([dt for dt in all_dates if is_weekend_or_holiday(dt)])

    # Pre-compute: providers with mixed stretches needing weekend slots
    provider_needs_weekend_slot = {}
    for provider, pstretches in all_stretches.items():
        if provider in EXCLUDED_PROVIDERS:
            continue
        needed_wknd_dates = set()
        for stretch in pstretches:
            if is_standalone_weekend(stretch):
                continue
            if is_moonlighting_in_stretch(provider, stretch, daily_data):
                continue
            has_weekday = any(not is_weekend_or_holiday(d) for d in stretch)
            has_weekend = any(is_weekend_or_holiday(d) for d in stretch)
            if has_weekday and has_weekend:
                for d in stretch:
                    if is_weekend_or_holiday(d):
                        needed_wknd_dates.add(d)
        if needed_wknd_dates:
            provider_needs_weekend_slot[provider] = needed_wknd_dates

    MIN_WEEKENDS_FOR_WKND_LC = 2

    # Pre-compute urgency scores
    provider_need_urgency = {}
    for provider, needed_dates in provider_needs_weekend_slot.items():
        for pstretch in all_stretches.get(provider, []):
            weekday_count = sum(1 for d in pstretch if not is_weekend_or_holiday(d))
            for d in pstretch:
                if d in needed_dates:
                    old = provider_need_urgency.get((provider, d), 0)
                    provider_need_urgency[(provider, d)] = max(old, weekday_count)

    def build_weighted_weekend_graph(min_weekends):
        """Build weighted bipartite graph for weekend matching."""
        G = nx.Graph()
        s_nodes = set()
        for dt in weekend_dates:
            for slot in daily_slots[dt]:
                slot_id = f"slot_{dt.strftime('%Y%m%d')}_{slot}"
                s_nodes.add(slot_id)
                G.add_node(slot_id, bipartite=0)

        p_nodes = set()
        for dt in weekend_dates:
            if dt not in daily_data:
                continue
            for p in daily_data[dt]:
                provider = p["provider"]
                if provider in EXCLUDED_PROVIDERS or p["moonlighting"]:
                    continue
                if weekends_worked.get(provider, 0) < min_weekends:
                    continue
                pnode = f"prov_{provider}"
                if pnode not in p_nodes:
                    G.add_node(pnode, bipartite=1)
                    p_nodes.add(pnode)

                needs_slot = provider in provider_needs_weekend_slot
                needs_this_date = needs_slot and dt in provider_needs_weekend_slot[provider]

                for slot in daily_slots[dt]:
                    if slot == "teaching" and p["category"] == "direct_care":
                        continue
                    slot_id = f"slot_{dt.strftime('%Y%m%d')}_{slot}"

                    cat_match = (
                        (slot == "teaching" and p["category"] == "teaching") or
                        (slot == "dc2" and p["category"] == "direct_care")
                    )
                    if needs_this_date:
                        urgency = provider_need_urgency.get((provider, dt), 0)
                        if urgency >= 5:
                            weight = 1 if cat_match else 4
                        else:
                            weight = 2 if cat_match else 4
                    else:
                        weight = 50 if cat_match else 60

                    weight += tiebreak_hash(provider, f"wknd_{dt.strftime('%Y%m%d')}_{slot}") * 0.9
                    G.add_edge(pnode, slot_id, weight=weight)
        return G, s_nodes

    # Run weighted matching
    G, slot_nodes = build_weighted_weekend_graph(MIN_WEEKENDS_FOR_WKND_LC)
    try:
        raw_matching = nx.bipartite.minimum_weight_full_matching(G, top_nodes=slot_nodes)
    except (ValueError, nx.NetworkXError):
        raw_matching = nx.bipartite.maximum_matching(G, top_nodes=slot_nodes)
    matched_slots = {}
    for k, v in raw_matching.items():
        if k in slot_nodes:
            matched_slots[k] = v

    # Check coverage and fall back if needed
    all_slot_nodes = set()
    for dt in weekend_dates:
        for slot in daily_slots[dt]:
            all_slot_nodes.add(f"slot_{dt.strftime('%Y%m%d')}_{slot}")

    if len(matched_slots) < len(all_slot_nodes):
        G_full, slot_nodes_full = build_weighted_weekend_graph(1)
        try:
            raw_matching_full = nx.bipartite.minimum_weight_full_matching(
                G_full, top_nodes=slot_nodes_full)
        except (ValueError, nx.NetworkXError):
            raw_matching_full = nx.bipartite.maximum_matching(
                G_full, top_nodes=slot_nodes_full)
        for k, v in raw_matching_full.items():
            if k in slot_nodes_full and k not in matched_slots:
                matched_slots[k] = v

    # Convert to advisory suggestions
    weekend_suggestions = {}
    for slot_id, prov_id in matched_slots.items():
        parts = slot_id.split("_")
        date_str = parts[1]
        slot = parts[2]
        provider = prov_id.replace("prov_", "")
        dt = datetime.strptime(date_str, "%Y%m%d")
        weekend_suggestions[(dt, slot)] = provider

    # --------------------------------------------------------
    # PHASE 2: Sliding Window Loop (W1 + WE + W2)
    # --------------------------------------------------------

    def build_week_windows(all_dates, daily_slots):
        """Build sliding windows of (W1_weekdays, WE_dates, W2_weekdays)."""
        iso_week_dates = defaultdict(list)
        for dt in sorted(all_dates):
            wk = (dt.isocalendar()[0], dt.isocalendar()[1])
            iso_week_dates[wk].append(dt)

        iso_weeks_sorted = sorted(iso_week_dates.keys())

        windows = []
        for i, wk in enumerate(iso_weeks_sorted):
            dates_in_week = iso_week_dates[wk]
            w1_weekdays = [d for d in dates_in_week if not is_weekend_or_holiday(d)]
            we_dates = [d for d in dates_in_week if is_weekend_or_holiday(d)]

            w2_week_key = None
            w2_weekdays = []
            if i + 1 < len(iso_weeks_sorted):
                next_wk = iso_weeks_sorted[i + 1]
                w2_week_key = next_wk
                w2_weekdays = [d for d in iso_week_dates[next_wk]
                               if not is_weekend_or_holiday(d)]

            windows.append((w1_weekdays, we_dates, w2_weekdays, wk, w2_week_key))

        return windows

    windows = build_week_windows(all_dates, daily_slots)

    fulfilled_needs = set()
    weekend_pre_assigned = set()
    served_next_window = set()

    # Helper to update state after assigning a slot
    def _assign_slot(provider, dt, slot):
        """Record an assignment in pstate."""
        ps = pstate[provider]
        ps["lc_count"] += 1
        ps["day_of_week"].append(dt.weekday())
        if is_weekend_or_holiday(dt):
            ps["weekend_lc"] += 1
        if slot == "dc1":
            ps["dc1_count"] += 1
        elif slot == "dc2":
            ps["dc2_count"] += 1
        if ps["missed"] > 0:
            ps["missed"] -= 1

    # Helper to undo assignment
    def _unassign_slot(provider, dt, slot):
        """Undo an assignment in pstate."""
        ps = pstate[provider]
        ps["lc_count"] -= 1
        dow = dt.weekday()
        if dow in ps["day_of_week"]:
            ps["day_of_week"].remove(dow)
        if is_weekend_or_holiday(dt):
            ps["weekend_lc"] = max(0, ps["weekend_lc"] - 1)
        if slot == "dc1":
            ps["dc1_count"] = max(0, ps["dc1_count"] - 1)
        elif slot == "dc2":
            ps["dc2_count"] = max(0, ps["dc2_count"] - 1)

    for win_idx, (w1_weekdays, we_dates, w2_weekdays, w1_week_key, w2_week_key) in enumerate(windows):

        served_from_prev_window = served_next_window.copy()
        served_next_window = set()
        served_this_window = set()

        # ==============================================================
        # Step A: Fill weekend slots with stretch-aware grouping
        # ==============================================================

        # Teaching coverage guard
        w1_teaching_slot_count = sum(
            1 for d in w1_weekdays
            for s in daily_slots.get(d, [])
            if s == "teaching" and assignments[d][s] is None
        )
        w1_teaching_available = set()
        if w1_week_key and w1_week_key in week_groups:
            for need in week_groups[w1_week_key]:
                prov = need["provider"]
                if prov in EXCLUDED_PROVIDERS:
                    continue
                if prov in served_from_prev_window:
                    continue
                need_id = (prov, need["week_dates"][0])
                if need_id in fulfilled_needs:
                    continue
                cat = None
                for d in need["week_dates"]:
                    cat = get_provider_category(prov, d, daily_data)
                    if cat:
                        break
                if cat == "teaching":
                    w1_teaching_available.add(prov)

        teaching_consumed_by_weekend = set()

        for we_dt in we_dates:
            if we_dt not in daily_data:
                continue
            for slot in daily_slots.get(we_dt, []):
                if assignments[we_dt][slot] is not None:
                    continue

                candidates = []
                for p in daily_data[we_dt]:
                    provider = p["provider"]
                    if provider in EXCLUDED_PROVIDERS or p["moonlighting"]:
                        continue

                    # Hard max: 2 weekend LCs per provider
                    if pstate[provider]["weekend_lc"] >= 2:
                        continue

                    if slot == "teaching" and p["category"] == "direct_care":
                        continue

                    stretch = find_provider_stretch_for_date(provider, we_dt, all_stretches)
                    stretch_dates = set(stretch) if stretch else set()
                    in_w1 = bool(stretch_dates & set(w1_weekdays)) if w1_weekdays else False
                    in_w2 = bool(stretch_dates & set(w2_weekdays)) if w2_weekdays else False

                    if in_w1 and not in_w2:
                        group_priority = 0  # Group A: ending on weekend
                    elif in_w2 and not in_w1:
                        group_priority = 1  # Group B: starting on weekend
                    elif in_w1 and in_w2:
                        group_priority = 0  # spans both — treat as Group A
                    else:
                        group_priority = 2  # Group C: standalone weekend

                    if provider in served_this_window:
                        continue
                    if provider in served_from_prev_window:
                        continue

                    # Teaching coverage guard
                    if p["category"] == "teaching" and provider in w1_teaching_available:
                        remaining = len(w1_teaching_available - served_this_window - teaching_consumed_by_weekend - {provider})
                        if remaining < w1_teaching_slot_count:
                            continue

                    advisory_bonus = -0.5 if weekend_suggestions.get((we_dt, slot)) == provider else 0

                    sort_key = (
                        group_priority,
                        -min(pstate[provider]["consecutive_no_lc"], 1),
                        -pstate[provider]["missed"],
                        pstate[provider]["lc_count"],
                        -provider_total_weeks.get(provider, 0),
                        advisory_bonus,
                        tiebreak_hash(provider, f"wknd_{we_dt.strftime('%Y%m%d')}_{slot}"),
                    )

                    candidates.append((sort_key, provider, group_priority))

                if not candidates:
                    continue

                candidates.sort(key=lambda x: x[0])
                _, best_provider, best_group = candidates[0]

                if best_provider in w1_teaching_available:
                    teaching_consumed_by_weekend.add(best_provider)

                assignments[we_dt][slot] = best_provider
                _assign_slot(best_provider, we_dt, slot)
                weekend_pre_assigned.add((we_dt, slot))

                # Record stretch with weekend LC
                stretch = find_provider_stretch_for_date(best_provider, we_dt, all_stretches)
                if stretch:
                    pstate[best_provider]["wknd_lc_stretches"].add(stretch[0])

                served_this_window.add(best_provider)

                if best_group == 1:
                    served_next_window.add(best_provider)

                for need in assignment_needs:
                    if need["provider"] == best_provider and we_dt in need["week_dates"]:
                        need_id = (best_provider, need["week_dates"][0])
                        fulfilled_needs.add(need_id)

        # ==============================================================
        # Step B: Fill W1 weekday teaching slots
        # ==============================================================
        if w1_week_key and w1_week_key in week_groups:
            w1_needs = week_groups[w1_week_key]

            w1_teaching_needs = []
            for need in w1_needs:
                provider = need["provider"]
                if provider in EXCLUDED_PROVIDERS:
                    continue
                need_id = (provider, need["week_dates"][0])
                if need_id in fulfilled_needs:
                    continue
                if provider in served_this_window or provider in served_from_prev_window:
                    continue
                cat = None
                for dt in need["week_dates"]:
                    cat = get_provider_category(provider, dt, daily_data)
                    if cat:
                        break
                if cat == "teaching":
                    w1_teaching_needs.append(need)

            week_ctx = f"{w1_week_key[0]}-W{w1_week_key[1]}"
            sort_key_fn = lambda n: (
                -min(pstate[n["provider"]]["consecutive_no_lc"], 1),
                -pstate[n["provider"]]["missed"],
                1 if n.get("standalone_weekend") else 0,
                pstate[n["provider"]]["lc_count"],
                -provider_total_weeks.get(n["provider"], 0),
                tiebreak_hash(n["provider"], week_ctx),
            )
            w1_teaching_needs.sort(key=sort_key_fn)

            for need in w1_teaching_needs:
                provider = need["provider"]
                eligible_dates = need["week_dates"]

                best = find_best_assignment(
                    provider, eligible_dates, daily_data, assignments,
                    daily_slots, pstate, weekends_worked
                )

                if best:
                    dt, slot_assigned = best
                    assignments[dt][slot_assigned] = provider
                    _assign_slot(provider, dt, slot_assigned)
                    served_this_window.add(provider)
                    need_id = (provider, need["week_dates"][0])
                    fulfilled_needs.add(need_id)

            # ==============================================================
            # Step C: Fill W1 weekday DC slots (DC + teaching overflow)
            # ==============================================================
            w1_dc_needs = []
            for need in w1_needs:
                provider = need["provider"]
                if provider in EXCLUDED_PROVIDERS:
                    continue
                need_id = (provider, need["week_dates"][0])
                if need_id in fulfilled_needs:
                    continue
                if provider in served_this_window or provider in served_from_prev_window:
                    continue
                cat = None
                for dt in need["week_dates"]:
                    cat = get_provider_category(provider, dt, daily_data)
                    if cat:
                        break
                if cat != "teaching":
                    w1_dc_needs.append(need)

            # Also add teaching providers who weren't assigned in Step B
            for need in w1_needs:
                provider = need["provider"]
                if provider in EXCLUDED_PROVIDERS:
                    continue
                need_id = (provider, need["week_dates"][0])
                if need_id in fulfilled_needs:
                    continue
                if provider in served_this_window or provider in served_from_prev_window:
                    continue
                cat = None
                for dt in need["week_dates"]:
                    cat = get_provider_category(provider, dt, daily_data)
                    if cat:
                        break
                if cat == "teaching" and need not in w1_dc_needs:
                    w1_dc_needs.append(need)

            w1_dc_needs.sort(key=sort_key_fn)

            for need in w1_dc_needs:
                provider = need["provider"]
                eligible_dates = need["week_dates"]

                if provider in EXCLUDED_PROVIDERS:
                    continue
                need_id = (provider, need["week_dates"][0])
                if need_id in fulfilled_needs:
                    continue
                if provider in served_this_window:
                    continue

                best = find_best_assignment(
                    provider, eligible_dates, daily_data, assignments,
                    daily_slots, pstate, weekends_worked
                )

                if best:
                    dt, slot_assigned = best
                    assignments[dt][slot_assigned] = provider
                    _assign_slot(provider, dt, slot_assigned)
                    served_this_window.add(provider)
                    fulfilled_needs.add(need_id)
                else:
                    is_standalone = need.get("standalone_weekend", False)
                    if not is_standalone:
                        pstate[provider]["missed"] += 1
                        pstate[provider]["no_lc_weeks"] += 1
                        flags.append({
                            "date": need["week_dates"][0].strftime("%m/%d/%Y"),
                            "provider": provider,
                            "flag_type": "NO_LONGCALL",
                            "message": f"No long call assigned for week {need['week_dates'][0].strftime('%m/%d')} - {need['week_dates'][-1].strftime('%m/%d')}",
                        })

            # ==============================================================
            # Step D: Track consecutive misses for W1 providers
            # ==============================================================
            providers_in_w1 = set(
                n["provider"] for n in w1_needs
                if not n.get("standalone_weekend")
            )
            for provider in providers_in_w1:
                if provider in served_this_window or provider in served_from_prev_window:
                    pstate[provider]["consecutive_no_lc"] = 0
                else:
                    pstate[provider]["consecutive_no_lc"] += 1
                    if pstate[provider]["consecutive_no_lc"] >= 2:
                        flag_date = w1_weekdays[0] if w1_weekdays else (we_dates[0] if we_dates else None)
                        if flag_date:
                            flags.append({
                                "date": flag_date.strftime("%m/%d/%Y"),
                                "provider": provider,
                                "flag_type": "CONSEC_NO_LC",
                                "message": f"2+ consecutive weeks without long call ({pstate[provider]['consecutive_no_lc']} weeks)",
                            })

    # --------------------------------------------------------
    # PHASE 2.5: Minimum guarantee
    # --------------------------------------------------------
    for need in assignment_needs:
        provider = need["provider"]
        if provider in EXCLUDED_PROVIDERS:
            continue
        if pstate[provider]["lc_count"] > 0:
            continue
        has_weekday = any(not is_weekend_or_holiday(d) for d in need["week_dates"])
        if not has_weekday:
            continue

        eligible_dates = need["week_dates"]

        # First try: find an open slot
        best = find_best_assignment(
            provider, eligible_dates, daily_data, assignments,
            daily_slots, pstate, weekends_worked
        )
        if best:
            dt, slot = best
            assignments[dt][slot] = provider
            _assign_slot(provider, dt, slot)
            continue

        # Second try: swap out the provider with the most long calls
        best_swap = None
        for dt in eligible_dates:
            if dt not in daily_data:
                continue
            working_today = any(p["provider"] == provider for p in daily_data[dt])
            if not working_today:
                continue
            provider_cat = get_provider_category(provider, dt, daily_data)
            for slot in daily_slots.get(dt, []):
                if slot == "teaching" and provider_cat == "direct_care":
                    continue
                if (dt, slot) in weekend_pre_assigned:
                    continue
                current = assignments[dt][slot]
                if current is None:
                    continue
                current_lc = pstate[current]["lc_count"]
                if current_lc <= 1:
                    continue
                if best_swap is None or current_lc > best_swap[3]:
                    best_swap = (dt, slot, current, current_lc)

        if best_swap:
            dt, slot, displaced, _ = best_swap
            _unassign_slot(displaced, dt, slot)
            pstate[displaced]["missed"] += 1

            assignments[dt][slot] = provider
            _assign_slot(provider, dt, slot)

            flags.append({
                "date": dt.strftime("%m/%d/%Y"),
                "provider": provider,
                "flag_type": "GUARANTEED_SWAP",
                "message": f"Swapped in (minimum guarantee) — displaced {displaced}",
            })

    # --------------------------------------------------------
    # PHASE 3 + 3.5 with RETRY LOOP
    # --------------------------------------------------------

    def _count_two_weekday_violations(asn):
        """Count two-weekday double violations in mixed stretches."""
        count = 0
        for provider, pstretches in all_stretches.items():
            for stretch in pstretches:
                if is_standalone_weekend(stretch):
                    continue
                if not stretch_has_weekday_and_weekend(stretch):
                    continue
                lc_dates = []
                for sdt in stretch:
                    for sslot in ["teaching", "dc1", "dc2"]:
                        if asn.get(sdt, {}).get(sslot) == provider:
                            lc_dates.append(sdt)
                if len(lc_dates) >= 2:
                    weekday_lcs = [d for d in lc_dates if not is_weekend_or_holiday(d)]
                    if len(weekday_lcs) >= 2:
                        count += 1
        return count

    # Save state before Phase 3 for retry loop
    _save_assignments = copy.deepcopy(assignments)
    _save_pstate = copy.deepcopy(dict(pstate))
    _save_flags = list(flags)

    best_attempt = None
    MAX_PHASE3_ATTEMPTS = 50

    for _attempt in range(MAX_PHASE3_ATTEMPTS):
        # Restore state to pre-Phase-3
        if _attempt > 0:
            assignments = copy.deepcopy(_save_assignments)
            pstate = defaultdict(lambda: {
                "lc_count": 0, "weekend_lc": 0, "dc1_count": 0, "dc2_count": 0,
                "day_of_week": [], "missed": 0, "no_lc_weeks": 0, "doubles": 0,
                "consecutive_no_lc": 0, "wknd_lc_stretches": set(),
            })
            for k, v in copy.deepcopy(_save_pstate).items():
                pstate[k] = v
            flags = list(_save_flags)

        provider_double_dates = defaultdict(list)

        # Collect empty slots
        empty_slots = []
        for dt in all_dates:
            for slot in daily_slots.get(dt, []):
                if assignments[dt][slot] is None:
                    empty_slots.append((dt, slot))

        # Sort: weekend-first on attempt 0, shuffle on retries
        if _attempt == 0:
            empty_slots.sort(key=lambda x: (0 if is_weekend_or_holiday(x[0]) else 1, x[0]))
        else:
            attempt_rng = random.Random(f"{VARIATION_SEED}_attempt_{_attempt}")
            weekend_slots = [(dt, s) for dt, s in empty_slots if is_weekend_or_holiday(dt)]
            weekday_slots = [(dt, s) for dt, s in empty_slots if not is_weekend_or_holiday(dt)]
            attempt_rng.shuffle(weekend_slots)
            attempt_rng.shuffle(weekday_slots)
            empty_slots = weekend_slots + weekday_slots

        # ---- PHASE 3: Fill remaining empty slots ----
        for dt, slot in empty_slots:
            if assignments[dt][slot] is not None:
                continue
            filler = find_double_filler(
                dt, slot, daily_data, assignments, daily_slots,
                pstate, weekends_worked, all_stretches,
                pstate  # provider_wknd_lc_stretches lives inside pstate
            )
            if filler:
                assignments[dt][slot] = filler
                pstate[filler]["lc_count"] += 1
                pstate[filler]["doubles"] += 1
                pstate[filler]["day_of_week"].append(dt.weekday())
                provider_double_dates[filler].append(dt)

                if is_weekend_or_holiday(dt):
                    pstate[filler]["weekend_lc"] += 1
                if slot == "dc1":
                    pstate[filler]["dc1_count"] += 1
                elif slot == "dc2":
                    pstate[filler]["dc2_count"] += 1

                flags.append({
                    "date": dt.strftime("%m/%d/%Y"),
                    "provider": filler,
                    "flag_type": "DOUBLE_LONGCALL",
                    "message": f"Double long call in stretch",
                })
            else:
                flags.append({
                    "date": dt.strftime("%m/%d/%Y"),
                    "provider": "UNFILLED",
                    "flag_type": "UNFILLED_SLOT",
                    "message": f"Could not fill {slot} slot",
                })

        # ---- PHASE 3.5: Fix two-weekday doubles ----
        for _phase35_round in range(3):
            any_fixed = False
            for provider, pstretches in all_stretches.items():
                if provider in EXCLUDED_PROVIDERS:
                    continue
                for stretch in pstretches:
                    if is_standalone_weekend(stretch):
                        continue
                    lc_entries = []
                    for sdt in stretch:
                        for sslot in ["teaching", "dc1", "dc2"]:
                            if assignments.get(sdt, {}).get(sslot) == provider:
                                lc_entries.append((sdt, sslot))
                    if len(lc_entries) < 2:
                        continue
                    weekday_lcs = [(d, s) for d, s in lc_entries if not is_weekend_or_holiday(d)]
                    if len(weekday_lcs) < 2:
                        continue

                    weekend_dates_in_stretch = [d for d in stretch if is_weekend_or_holiday(d)]
                    if not weekend_dates_in_stretch:
                        continue

                    # --- Strategy A: Direct swap ---
                    fixed = False
                    swap_candidates = []
                    for wdt in weekend_dates_in_stretch:
                        for wslot in daily_slots.get(wdt, []):
                            occupant = assignments[wdt].get(wslot)
                            if not occupant:
                                continue

                            prov_cat = get_provider_category(provider, wdt, daily_data)
                            if not prov_cat:
                                continue
                            if wslot == "teaching" and prov_cat == "direct_care":
                                continue

                            for wkdy_dt, wkdy_slot in weekday_lcs:
                                occ_cat = get_provider_category(occupant, wkdy_dt, daily_data)
                                if not occ_cat:
                                    continue
                                if wkdy_slot == "teaching" and occ_cat == "direct_care":
                                    continue

                                occ_today = any(
                                    assignments[wkdy_dt].get(s) == occupant
                                    for s in ["teaching", "dc1", "dc2"])
                                if occ_today:
                                    continue

                                remaining = [d for d, _ in weekday_lcs if d != wkdy_dt]
                                prov_gap_ok = True
                                prov_gap_penalty = 0
                                if remaining:
                                    gap = min(abs((wdt - d).days) for d in remaining)
                                    if gap < 2:
                                        prov_gap_ok = False
                                    elif gap < 3:
                                        prov_gap_penalty = 1
                                if not prov_gap_ok:
                                    continue

                                occ_stretch = find_provider_stretch_for_date(
                                    occupant, wkdy_dt, all_stretches)
                                if occ_stretch:
                                    occ_wkdy_lcs = sum(
                                        1 for sd in occ_stretch
                                        if not is_weekend_or_holiday(sd)
                                        for ss in ["teaching", "dc1", "dc2"]
                                        if assignments.get(sd, {}).get(ss) == occupant)
                                    if occ_wkdy_lcs >= 1:
                                        continue

                                    occ_lc_dates = [
                                        sd for sd in occ_stretch
                                        for ss in ["teaching", "dc1", "dc2"]
                                        if assignments.get(sd, {}).get(ss) == occupant
                                    ]
                                    if occ_lc_dates:
                                        occ_gap = min(abs((wkdy_dt - d).days) for d in occ_lc_dates)
                                        if occ_gap < 2:
                                            continue

                                prov_wknd_lcs = pstate[provider]["weekend_lc"]
                                swap_candidates.append((
                                    prov_wknd_lcs, prov_gap_penalty,
                                    wdt, wslot, occupant, wkdy_dt, wkdy_slot))

                    swap_candidates.sort()
                    if swap_candidates:
                        _, _, wdt, wslot, occupant, wkdy_dt, wkdy_slot = swap_candidates[0]
                        if pstate[provider]["weekend_lc"] < 2:
                            assignments[wkdy_dt][wkdy_slot] = occupant
                            assignments[wdt][wslot] = provider
                            pstate[provider]["weekend_lc"] += 1
                            pstate[occupant]["weekend_lc"] = max(0, pstate[occupant]["weekend_lc"] - 1)
                            fixed = True
                            any_fixed = True

                    if fixed:
                        continue

                    # --- Strategy B: Reshuffle ---
                    reshuffle_options = []
                    for wdt in weekend_dates_in_stretch:
                        for wslot in daily_slots.get(wdt, []):
                            occupant = assignments[wdt].get(wslot)
                            if not occupant:
                                continue

                            prov_cat = get_provider_category(provider, wdt, daily_data)
                            if not prov_cat:
                                continue
                            if wslot == "teaching" and prov_cat == "direct_care":
                                continue

                            for wkdy_dt, wkdy_slot in weekday_lcs:
                                remaining = [d for d, _ in weekday_lcs if d != wkdy_dt]
                                reshuffle_gap_ok = True
                                reshuffle_gap_penalty = 0
                                if remaining:
                                    gap = min(abs((wdt - d).days) for d in remaining)
                                    if gap < 2:
                                        reshuffle_gap_ok = False
                                    elif gap < 3:
                                        reshuffle_gap_penalty = 1
                                if not reshuffle_gap_ok:
                                    continue

                                if pstate[provider]["weekend_lc"] >= 2:
                                    continue

                                reshuffle_options.append((
                                    pstate[provider]["weekend_lc"],
                                    reshuffle_gap_penalty,
                                    wdt, wslot, occupant,
                                    wkdy_dt, wkdy_slot))

                    reshuffle_options.sort()
                    for _, _, wdt, wslot, occupant, wkdy_dt, wkdy_slot in reshuffle_options:
                        assignments[wkdy_dt][wkdy_slot] = None
                        pstate[provider]["lc_count"] -= 1
                        pstate[provider]["doubles"] = max(0, pstate[provider]["doubles"] - 1)

                        old_occupant = assignments[wdt][wslot]
                        assignments[wdt][wslot] = provider
                        pstate[provider]["weekend_lc"] += 1
                        pstate[provider]["lc_count"] += 1

                        if old_occupant:
                            pstate[old_occupant]["lc_count"] = max(0, pstate[old_occupant]["lc_count"] - 1)
                            pstate[old_occupant]["weekend_lc"] = max(0, pstate[old_occupant]["weekend_lc"] - 1)
                            if wslot == "dc1":
                                pstate[old_occupant]["dc1_count"] = max(0, pstate[old_occupant]["dc1_count"] - 1)
                            elif wslot == "dc2":
                                pstate[old_occupant]["dc2_count"] = max(0, pstate[old_occupant]["dc2_count"] - 1)

                        new_filler = find_double_filler(
                            wkdy_dt, wkdy_slot, daily_data, assignments, daily_slots,
                            pstate, weekends_worked, all_stretches, pstate
                        )

                        if new_filler and new_filler != provider:
                            assignments[wkdy_dt][wkdy_slot] = new_filler
                            pstate[new_filler]["lc_count"] += 1
                            pstate[new_filler]["doubles"] += 1
                            if wkdy_slot == "dc1":
                                pstate[new_filler]["dc1_count"] += 1
                            elif wkdy_slot == "dc2":
                                pstate[new_filler]["dc2_count"] += 1

                            fixed = True
                            any_fixed = True
                            break
                        else:
                            # Revert
                            assignments[wkdy_dt][wkdy_slot] = provider
                            pstate[provider]["lc_count"] += 1
                            pstate[provider]["doubles"] += 1

                            assignments[wdt][wslot] = old_occupant
                            pstate[provider]["weekend_lc"] -= 1
                            pstate[provider]["lc_count"] -= 1

                            if old_occupant:
                                pstate[old_occupant]["lc_count"] += 1
                                pstate[old_occupant]["weekend_lc"] += 1
                                if wslot == "dc1":
                                    pstate[old_occupant]["dc1_count"] += 1
                                elif wslot == "dc2":
                                    pstate[old_occupant]["dc2_count"] += 1
                            continue

            if not any_fixed:
                break

        # Count violations for this attempt
        violations = _count_two_weekday_violations(assignments)
        if violations == 0:
            best_attempt = None  # signal: use current state directly
            break

        attempt_state = {
            "violations": violations,
            "assignments": copy.deepcopy(assignments),
            "pstate": copy.deepcopy(dict(pstate)),
            "flags": list(flags),
        }
        if best_attempt is None or violations < best_attempt["violations"]:
            best_attempt = attempt_state

    # Restore best attempt if no perfect solution
    if best_attempt is not None:
        assignments = best_attempt["assignments"]
        pstate = defaultdict(lambda: {
            "lc_count": 0, "weekend_lc": 0, "dc1_count": 0, "dc2_count": 0,
            "day_of_week": [], "missed": 0, "no_lc_weeks": 0, "doubles": 0,
            "consecutive_no_lc": 0, "wknd_lc_stretches": set(),
        })
        for k, v in best_attempt["pstate"].items():
            pstate[k] = v
        flags = best_attempt["flags"]

    # --------------------------------------------------------
    # PHASE 4: Enforce max 1 missed week per provider
    # --------------------------------------------------------

    def compute_missed_weeks(provider):
        """Count non-moonlighting real-stretch weeks with no LC for a provider."""
        stretches = all_stretches.get(provider, [])
        missed_week_list = []
        for stretch in stretches:
            if is_standalone_weekend(stretch):
                continue
            weeks = split_stretch_into_weeks(stretch)
            for week in weeks:
                if is_moonlighting_in_stretch(provider, week, daily_data):
                    continue
                has_lc = any(
                    assignments.get(dt, {}).get(slot) == provider
                    for dt in week for slot in ["teaching", "dc1", "dc2"]
                )
                if not has_lc:
                    missed_week_list.append(week)
        return missed_week_list

    for _round in range(10):
        worst_providers = []
        for provider in all_providers:
            if provider in EXCLUDED_PROVIDERS:
                continue
            missed_weeks = compute_missed_weeks(provider)
            if len(missed_weeks) >= 2:
                worst_providers.append((provider, missed_weeks))

        if not worst_providers:
            break

        worst_providers.sort(key=lambda x: -len(x[1]))

        swapped_any = False
        for provider, missed_weeks in worst_providers:
            for week in missed_weeks:
                best_swap = None
                best_swap_score = None

                for dt in week:
                    if dt not in daily_data:
                        continue
                    working = any(p["provider"] == provider for p in daily_data[dt])
                    if not working:
                        continue

                    if is_weekend_or_holiday(dt) and pstate[provider]["weekend_lc"] >= 1:
                        continue

                    creates_two_weekday = False
                    prov_stretch = find_provider_stretch_for_date(provider, dt, all_stretches)
                    if prov_stretch:
                        existing_lc_dates = [
                            sd for sd in prov_stretch
                            for ss in ["teaching", "dc1", "dc2"]
                            if assignments.get(sd, {}).get(ss) == provider
                        ]

                        if any(is_holiday(d) for d in existing_lc_dates) or is_holiday(dt):
                            continue

                        phase4_gap_penalty = 0
                        if existing_lc_dates:
                            min_gap = min(abs((dt - d).days) for d in existing_lc_dates)
                            if min_gap < 2:
                                continue
                            elif min_gap < 3:
                                phase4_gap_penalty = 1

                        if not is_weekend_or_holiday(dt):
                            existing_weekday_lcs = sum(
                                1 for d in existing_lc_dates
                                if not is_weekend_or_holiday(d)
                            )
                            if existing_weekday_lcs >= 1:
                                creates_two_weekday = True

                    if creates_two_weekday and prov_stretch and stretch_has_weekday_and_weekend(prov_stretch):
                        continue

                    provider_cat = get_provider_category(provider, dt, daily_data)
                    for slot in daily_slots.get(dt, []):
                        if slot == "teaching" and provider_cat == "direct_care":
                            continue
                        if (dt, slot) in weekend_pre_assigned:
                            continue

                        displaced = assignments[dt][slot]
                        if displaced is None or displaced == provider:
                            continue

                        displaced_missed_now = len(compute_missed_weeks(displaced))
                        if displaced_missed_now > 0:
                            continue

                        swap_score = (phase4_gap_penalty, -pstate[displaced]["lc_count"])
                        if best_swap is None or swap_score < best_swap_score:
                            best_swap = (dt, slot, displaced)
                            best_swap_score = swap_score

                if best_swap and not best_swap_score[0]:
                    dt, slot, displaced = best_swap

                    _unassign_slot(displaced, dt, slot)

                    assignments[dt][slot] = provider
                    _assign_slot(provider, dt, slot)

                    flags.append({
                        "date": dt.strftime("%m/%d/%Y"),
                        "provider": provider,
                        "flag_type": "MISSED_SWAP",
                        "message": f"Swapped in (max 1 missed week rule) — displaced {displaced}",
                    })

                    swapped_any = True
                    break

        if not swapped_any:
            break

    # --------------------------------------------------------
    # STATS COMPUTATION
    # --------------------------------------------------------

    lc_assigned = set()
    for dt, a in assignments.items():
        for slot in ["teaching", "dc1", "dc2"]:
            if a[slot]:
                lc_assigned.add((dt, a[slot]))

    source_days_set = set()
    for dt, providers in daily_data.items():
        for p in providers:
            source_days_set.add((p["provider"], dt))

    weeks_worked = {}
    standalone_weekends_count = {}
    stretches_count = {}
    no_lc_weeks_count = {}
    for provider in all_providers:
        if provider in EXCLUDED_PROVIDERS:
            continue
        stretches = all_stretches.get(provider, [])
        total_weeks = 0
        total_standalone = 0
        total_stretches = 0
        total_no_lc = 0
        for stretch in stretches:
            if is_standalone_weekend(stretch):
                if not is_moonlighting_in_stretch(provider, stretch, daily_data):
                    total_standalone += 1
            else:
                total_stretches += 1
                weeks = split_stretch_into_weeks(stretch)
                for week in weeks:
                    if is_moonlighting_in_stretch(provider, week, daily_data):
                        continue
                    total_weeks += 1
                    has_lc = any((dt, provider) in lc_assigned for dt in week)
                    has_source = any((provider, dt) in source_days_set for dt in week)
                    if not has_lc and has_source:
                        total_no_lc += 1
        weeks_worked[provider] = total_weeks
        standalone_weekends_count[provider] = total_standalone
        stretches_count[provider] = total_stretches
        no_lc_weeks_count[provider] = total_no_lc

    provider_stats = {}
    for provider in sorted(all_providers):
        if provider in EXCLUDED_PROVIDERS:
            continue
        ps = pstate[provider]
        provider_stats[provider] = {
            "total_long_calls": ps["lc_count"],
            "weekend_long_calls": ps["weekend_lc"],
            "dc1_count": ps["dc1_count"],
            "dc2_count": ps["dc2_count"],
            "missed": no_lc_weeks_count.get(provider, 0),
            "doubles": ps["doubles"],
            "weekends_worked": weekends_worked.get(provider, 0),
            "weeks_worked": weeks_worked.get(provider, 0),
            "standalone_weekends": standalone_weekends_count.get(provider, 0),
            "stretches": stretches_count.get(provider, 0),
            "days_of_week": ps["day_of_week"],
        }

    return assignments, flags, provider_stats


# ============================================================
# OUTPUT
# ============================================================

def format_output_table(assignments, daily_slots, flags):
    """Format assignments as a text table with dates down the left."""
    all_dates = sorted(assignments.keys())

    flag_lookup = defaultdict(list)
    for f in flags:
        flag_lookup[(f.get("date", ""), f.get("provider", ""))].append(f)

    lines = []
    lines.append(f"{'Date':<12} {'Day':<5} {'Teaching LC (5p-7p)':<30} {'DC LC 1 AM (7a-8a)':<30} {'DC LC 1 PM (5p-7p)':<30} {'DC LC 2 PM (5p-7p)':<30} {'Flags'}")
    lines.append("-" * 170)

    for dt in all_dates:
        date_str = dt.strftime("%m/%d/%Y")
        day_str = day_name(dt)
        a = assignments[dt]

        teaching = a["teaching"] or "---UNFILLED---"
        dc2 = a["dc2"] or "---UNFILLED---"

        if is_weekend_or_holiday(dt):
            dc1_am = ""
            dc1_pm = ""
        else:
            dc1 = a["dc1"] or "---UNFILLED---"
            dc1_am = dc1
            dc1_pm = dc1

        date_flags = []
        for slot_provider in [a["teaching"], a["dc1"], a["dc2"]]:
            if slot_provider:
                key = (date_str, slot_provider)
                if key in flag_lookup:
                    for f in flag_lookup[key]:
                        date_flags.append(f"[{f['flag_type']}] {f['provider']}: {f['message']}")
        key = (date_str, "UNFILLED")
        if key in flag_lookup:
            for f in flag_lookup[key]:
                date_flags.append(f"[{f['flag_type']}] {f['message']}")

        flag_str = " | ".join(date_flags) if date_flags else ""

        marker = ""
        if is_holiday(dt):
            marker = " *HOLIDAY*"
        elif is_weekend(dt):
            marker = " (wknd)"

        lines.append(f"{date_str:<12} {day_str + marker:<12} {teaching:<30} {dc1_am:<30} {dc1_pm:<30} {dc2:<30} {flag_str}")

    return "\n".join(lines)


def format_provider_summary(provider_stats):
    """Format provider statistics summary."""
    lines = []
    lines.append(f"\n{'='*120}")
    lines.append("PROVIDER SUMMARY")
    lines.append(f"{'='*120}")
    lines.append(f"{'Provider':<30} {'Total LC':>8} {'Wknd LC':>8} {'DC1':>5} {'DC2':>5} {'Missed':>7} {'Doubles':>8} {'Wknds Worked':>13} {'Days of Week'}")
    lines.append("-" * 120)

    dow_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

    for provider in sorted(provider_stats.keys()):
        s = provider_stats[provider]
        if s["total_long_calls"] == 0 and s["missed"] == 0:
            continue

        days = [dow_names[d] for d in s["days_of_week"]]
        days_str = ", ".join(days) if days else ""

        lines.append(f"{provider:<30} {s['total_long_calls']:>8} {s['weekend_long_calls']:>8} "
                     f"{s['dc1_count']:>5} {s['dc2_count']:>5} {s['missed']:>7} "
                     f"{s['doubles']:>8} {s['weekends_worked']:>13} {days_str}")

    return "\n".join(lines)


def format_flag_summary(flags):
    """Format flag summary."""
    lines = []
    lines.append(f"\n{'='*80}")
    lines.append("FLAGS AND VIOLATIONS")
    lines.append(f"{'='*80}")

    by_type = defaultdict(list)
    for f in flags:
        by_type[f["flag_type"]].append(f)

    for ftype, flist in sorted(by_type.items()):
        lines.append(f"\n{ftype} ({len(flist)} occurrences):")
        for f in flist:
            lines.append(f"  {f['date']} - {f['provider']}: {f['message']}")

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("Loading schedule data...")
    data = load_schedule()

    print("Building daily provider data...")
    daily_data = build_daily_data(data)
    all_daily_data = build_all_daily_data(data)
    print(f"  Days in block: {len([d for d in daily_data if is_in_block(d)])}")

    print("Running assignment engine...")
    assignments, flags, provider_stats = assign_long_calls(daily_data, all_daily_data)

    # Output
    table = format_output_table(assignments, None, flags)
    summary = format_provider_summary(provider_stats)
    flag_summary = format_flag_summary(flags)

    full_output = table + "\n" + summary + "\n" + flag_summary

    # Write to file
    output_file = os.path.join(OUTPUT_DIR, "longcall_assignments.txt")
    with open(output_file, 'w') as f:
        f.write(full_output)
    print(f"\nWrote assignments to: {output_file}")

    # Write JSON for comparison tooling
    json_out = {}
    for dt, slots in assignments.items():
        date_key = dt.strftime("%Y-%m-%d")
        json_out[date_key] = {
            "teaching": slots.get("teaching"),
            "dc1": slots.get("dc1"),
            "dc2": slots.get("dc2"),
        }
    json_file = os.path.join(OUTPUT_DIR, "longcall_assignments.json")
    with open(json_file, 'w') as f:
        json.dump({"assignments": json_out, "provider_stats": {p: {k: v for k, v in s.items() if k != "days_of_week"} for p, s in provider_stats.items()}, "flags": flags}, f, indent=2, default=str)
    print(f"Wrote JSON to: {json_file}")

    # Print summary stats
    total_slots = sum(1 for dt in assignments
                      for slot in ["teaching", "dc1", "dc2"]
                      if assignments[dt].get(slot))
    unfilled = sum(1 for f in flags if f["flag_type"] == "UNFILLED_SLOT")
    doubles = sum(1 for f in flags if f["flag_type"] == "DOUBLE_LONGCALL")
    missed = sum(1 for f in flags if f["flag_type"] == "NO_LONGCALL")

    print(f"\n--- Quick Stats ---")
    print(f"Total slots filled: {total_slots}")
    print(f"Unfilled slots: {unfilled}")
    print(f"Double long calls: {doubles}")
    print(f"Missed long calls: {missed}")
    print(f"Total flags: {len(flags)}")
