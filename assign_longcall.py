#!/usr/bin/env python3
"""
Long Call Assignment Engine
Reads parsed schedule data and assigns long call shifts according to rules
defined in longcall_rules.md.
"""

import hashlib
import json
import os
import random
import networkx as nx
from datetime import datetime, timedelta
from collections import defaultdict

# ============================================================
# CONFIGURATION
# ============================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")

# Load config from config.json (contains PII like excluded provider names)
def _load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {}

_config = _load_config()

BLOCK_START = datetime.strptime(_config.get("block_start", "2026-03-02"), "%Y-%m-%d")
BLOCK_END = datetime.strptime(_config.get("block_end", "2026-06-28"), "%Y-%m-%d")

HOLIDAYS = [
    datetime.strptime(d, "%Y-%m-%d") for d in _config.get("holidays", ["2026-05-25"])
]

EXCLUDED_PROVIDERS = _config.get("excluded_providers", [])

TEACHING_SERVICES = _config.get("teaching_services", [
    "HA", "HB", "HC", "HD", "HE", "HF", "HG", "HM (Family Medicine)",
])

DIRECT_CARE_SERVICES = _config.get("direct_care_services", [
    "H1", "H2", "H3", "H4", "H5", "H6", "H7",
    "H8- Pav 6 & EXAU", "H9", "H10", "H11",
    "H12- Pav 8 & Pav 9", "H13- (Obs overflow)", "H14",
    "H15", "H16", "H17", "H18",
])

ALL_SOURCE_SERVICES = TEACHING_SERVICES + DIRECT_CARE_SERVICES

OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
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


import uuid
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

    This alignment ensures that each chunk maps to exactly one ISO week group
    in Phase 2, preventing a single need from appearing in multiple groups
    (which could cause duplicate long call assignments).

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

    # Build stretches from source-service days only
    all_stretches = identify_stretches(daily_data)

    # Split stretches into weeks and build assignment needs.
    # Separate real stretches (has weekdays) from standalone weekends.
    # Standalone weekends are deprioritized for LC assignment.
    assignment_needs = []
    for provider, stretches in all_stretches.items():
        if provider in EXCLUDED_PROVIDERS:
            continue

        for stretch in stretches:
            standalone_wknd = is_standalone_weekend(stretch)

            if standalone_wknd:
                # Standalone weekend — single assignment need, deprioritized
                if is_moonlighting_in_stretch(provider, stretch, daily_data):
                    continue
                assignment_needs.append({
                    "provider": provider,
                    "week_dates": stretch,
                    "standalone_weekend": True,
                })
            else:
                # Real stretch — split into weeks
                weeks = split_stretch_into_weeks(stretch)
                for week in weeks:
                    if is_moonlighting_in_stretch(provider, week, daily_data):
                        continue
                    assignment_needs.append({
                        "provider": provider,
                        "week_dates": week,
                        "standalone_weekend": False,
                    })

    # Pre-compute total non-moonlighting weeks per provider for surplus priority.
    # "weeks" = only stretches/sub-stretches that contain weekdays.
    # Standalone weekends do NOT count as weeks.
    provider_total_weeks = defaultdict(int)
    for provider, stretches in all_stretches.items():
        if provider in EXCLUDED_PROVIDERS:
            continue
        for stretch in stretches:
            if is_standalone_weekend(stretch):
                continue  # standalone weekends don't count as weeks
            weeks = split_stretch_into_weeks(stretch)
            for week in weeks:
                if not is_moonlighting_in_stretch(provider, week, daily_data):
                    provider_total_weeks[provider] += 1

    # Track state
    assignments = {}  # date -> {"teaching": str, "dc1": str, "dc2": str}
    all_dates = sorted(daily_data.keys())
    for dt in all_dates:
        assignments[dt] = {"teaching": None, "dc1": None, "dc2": None}

    # Provider tracking
    provider_lc_count = defaultdict(int)       # total long calls assigned
    provider_weekend_lc = defaultdict(int)     # weekend long calls assigned
    provider_dc1_count = defaultdict(int)      # DC1 assignments
    provider_dc2_count = defaultdict(int)      # DC2 assignments
    provider_day_of_week = defaultdict(list)   # days of week assigned
    provider_missed = defaultdict(int)         # running balance of missed LCs (for priority — goes down when made up)
    provider_no_lc_weeks = defaultdict(int)   # total count of weeks with no LC (never decremented)
    provider_double = defaultdict(int)         # double long calls in a stretch

    flags = []
    weekends_worked = {}

    # Pre-compute weekends worked for each provider
    all_providers = set()
    for dt, providers in daily_data.items():
        for p in providers:
            all_providers.add(p["provider"])
    for provider in all_providers:
        weekends_worked[provider] = count_weekends_in_block(provider, daily_data)

    # --------------------------------------------------------
    # PHASE 1: Determine how many slots each day needs
    # --------------------------------------------------------
    daily_slots = {}
    for dt in all_dates:
        if is_weekend_or_holiday(dt):
            daily_slots[dt] = ["teaching", "dc2"]
        else:
            daily_slots[dt] = ["teaching", "dc1", "dc2"]

    # --------------------------------------------------------
    # PHASE 1.5: Pre-assign ALL weekend slots via bipartite matching
    # --------------------------------------------------------
    # Weekend LC balance is a global constraint that cannot be solved greedily.
    # We solve it first using weighted bipartite matching to guarantee each
    # provider gets at most 1 weekend LC, while preferring providers who work
    # more weekends (protecting those with only 1-2 weekends from getting a
    # weekend LC when possible).

    weekend_dates = sorted([dt for dt in all_dates if is_weekend_or_holiday(dt)])

    # Step 1: Build bipartite graph EXCLUDING providers with <=1 weekend worked.
    # Providers with only 1 weekend in the entire block should not get a weekend
    # LC — it's a penalty (creates doubles in their only stretch with a weekend).
    # We try first without them; if we can't fill all slots, we add them back.

    MIN_WEEKENDS_FOR_WKND_LC = 2  # providers must work at least this many weekends

    def build_weekend_graph(min_weekends):
        """Build bipartite graph for weekend matching, only including providers
        who work at least min_weekends weekends in the block."""
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
                    continue  # skip low-weekend providers
                pnode = f"prov_{provider}"
                if pnode not in p_nodes:
                    G.add_node(pnode, bipartite=1)
                    p_nodes.add(pnode)
                for slot in daily_slots[dt]:
                    # DC providers cannot fill teaching slots
                    if slot == "teaching" and p["category"] == "direct_care":
                        continue
                    slot_id = f"slot_{dt.strftime('%Y%m%d')}_{slot}"
                    G.add_edge(pnode, slot_id)
        return G, s_nodes

    # Try with min_weekends=2 first
    G, slot_nodes = build_weekend_graph(MIN_WEEKENDS_FOR_WKND_LC)
    raw_matching = nx.bipartite.maximum_matching(G, top_nodes=slot_nodes)
    matched_slots = {}
    for k, v in raw_matching.items():
        if k in slot_nodes:
            matched_slots[k] = v

    # Check if all slots are filled
    total_weekend_slots = len(slot_nodes)
    if len(matched_slots) < total_weekend_slots:
        # Some slots unfilled — fall back to including all providers
        G_full, slot_nodes_full = build_weekend_graph(1)
        raw_matching_full = nx.bipartite.maximum_matching(G_full, top_nodes=slot_nodes_full)
        matched_slots = {}
        for k, v in raw_matching_full.items():
            if k in slot_nodes_full:
                matched_slots[k] = v
        slot_nodes = slot_nodes_full

    # Step 2: Post-process to swap out low-weekend providers where possible.
    # Even after fallback, try to replace any low-weekend provider with a
    # higher-weekend alternative that wasn't matched.
    assigned_providers = set(matched_slots.values())
    for slot_id, prov_id in list(matched_slots.items()):
        provider = prov_id.replace("prov_", "")
        wknd_count = weekends_worked.get(provider, 0)
        if wknd_count >= 3:
            continue  # no need to swap

        # Parse date and slot from slot_id
        parts = slot_id.split("_")
        date_str = parts[1]
        slot_type = parts[2]
        dt = datetime.strptime(date_str, "%Y%m%d")

        # Find unassigned providers on this day with more weekends worked
        if dt not in daily_data:
            continue
        best_swap = None
        for p in daily_data[dt]:
            cand = p["provider"]
            if cand in EXCLUDED_PROVIDERS or p["moonlighting"]:
                continue
            # DC providers cannot fill teaching slots
            if slot_type == "teaching" and p["category"] == "direct_care":
                continue
            cand_node = f"prov_{cand}"
            if cand_node in assigned_providers:
                continue  # already assigned to another slot
            cand_wknds = weekends_worked.get(cand, 0)
            if cand_wknds <= wknd_count:
                continue  # not better
            if best_swap is None or cand_wknds > best_swap[1]:
                best_swap = (cand_node, cand_wknds)

        if best_swap:
            new_prov = best_swap[0]
            assigned_providers.discard(prov_id)
            assigned_providers.add(new_prov)
            matched_slots[slot_id] = new_prov

    # Apply the matching to assignments
    weekend_pre_assigned = set()  # track which (date, slot) were pre-assigned
    # Track which providers have a weekend LC in which stretch (for Phase 3 double prevention)
    provider_wknd_lc_stretches = defaultdict(set)  # provider -> set of stretch start dates
    for slot_id, prov_id in matched_slots.items():
        parts = slot_id.split("_")
        date_str = parts[1]
        slot = parts[2]
        provider = prov_id.replace("prov_", "")
        dt = datetime.strptime(date_str, "%Y%m%d")
        if assignments[dt][slot] is not None:
            continue  # safety check

        assignments[dt][slot] = provider
        provider_lc_count[provider] += 1
        provider_weekend_lc[provider] += 1
        provider_day_of_week[provider].append(dt.weekday())
        if slot == "dc1":
            provider_dc1_count[provider] += 1
        elif slot == "dc2":
            provider_dc2_count[provider] += 1
        weekend_pre_assigned.add((dt, slot))

        # Record which stretch this weekend LC falls in
        stretch = find_provider_stretch_for_date(provider, dt, all_stretches)
        if stretch:
            provider_wknd_lc_stretches[provider].add(stretch[0])  # use start date as key

    # Count unfilled weekend slots (should be 0 or very few)
    unfilled_weekend = 0
    for dt in weekend_dates:
        for slot in daily_slots[dt]:
            if assignments[dt][slot] is None:
                unfilled_weekend += 1

    # --------------------------------------------------------
    # PHASE 2: For each assignment need, pick a day and slot
    # --------------------------------------------------------
    # Group needs by week (ISO week number + year)
    week_groups = defaultdict(list)
    for need in assignment_needs:
        # Use the Monday of the week as key
        first_day = need["week_dates"][0]
        week_key = (first_day.isocalendar()[0], first_day.isocalendar()[1])
        week_groups[week_key].append(need)

    # Process weeks in chronological order
    sorted_weeks = sorted(week_groups.keys())

    # Safety net: track which needs have been fulfilled so a provider
    # can't get two LCs from the same stretch-chunk even if it somehow
    # appears in multiple week groups.
    fulfilled_needs = set()  # (provider, first_date_of_need) pairs

    # Track consecutive weeks without LC per provider.
    # Key = provider, Value = number of consecutive weeks (so far) without LC.
    # Reset to 0 when they get a LC. Increment when they're in a week but don't get one.
    consecutive_no_lc = defaultdict(int)

    for week_key in sorted_weeks:
        needs = week_groups[week_key]

        # Collect all dates in this week that have slots
        week_dates = set()
        for need in needs:
            for dt in need["week_dates"]:
                week_dates.add(dt)
        week_dates = sorted(week_dates)

        # Count available slots across the week
        total_slots = 0
        for dt in week_dates:
            for slot in daily_slots.get(dt, []):
                if assignments[dt][slot] is None:
                    total_slots += 1

        num_needs = len(needs)

        # Determine if we have surplus or shortage
        if num_needs > total_slots:
            # More providers than slots - some will miss
            pass  # Handle below
        elif num_needs < total_slots:
            # More slots than providers - some may need doubles
            pass  # Handle below

        # Sort needs by priority:
        # 0. Providers who went 1+ consecutive weeks without LC (highest — enforce the "no more than 1 week gap" rule)
        # 1. Providers who previously missed (next highest priority)
        # 2. Standalone weekends are deprioritized (push to end)
        #    — prefer giving LCs during real stretches with weekdays
        # 3. Then by fewest long calls so far (fairness)
        # 4. Then by fewest total weeks (protect providers who work less —
        #    providers with more weeks can more easily absorb a miss)
        # 5. Hash-based tiebreaker so equal-priority providers rotate fairly
        #    across weeks (no alphabetical bias)
        week_ctx = f"{week_key[0]}-W{week_key[1]}"

        # Separate needs into teaching and direct care, so teaching
        # providers get first pick of teaching slots (preventing DC→teaching crossover).
        teaching_needs = []
        dc_needs = []
        for need in needs:
            provider = need["provider"]
            cat = None
            for dt in need["week_dates"]:
                cat = get_provider_category(provider, dt, daily_data)
                if cat:
                    break
            if cat == "teaching":
                teaching_needs.append(need)
            else:
                dc_needs.append(need)

        # Sort each group independently by the same priority criteria
        sort_key = lambda n: (-min(consecutive_no_lc.get(n["provider"], 0), 1),
                               -provider_missed.get(n["provider"], 0),
                               1 if n.get("standalone_weekend") else 0,
                               provider_lc_count.get(n["provider"], 0),
                               -provider_total_weeks.get(n["provider"], 0),
                               tiebreak_hash(n["provider"], week_ctx))
        teaching_needs.sort(key=sort_key)
        dc_needs.sort(key=sort_key)

        # Process teaching first, then DC — ensures teaching providers
        # fill teaching slots before DC providers compete for remaining slots
        ordered_needs = teaching_needs + dc_needs

        assigned_this_week = set()

        # Check which providers already got a weekend LC in this week from Phase 1.5
        for need in ordered_needs:
            provider = need["provider"]
            for dt in need["week_dates"]:
                if is_weekend_or_holiday(dt) and (dt, "teaching") in weekend_pre_assigned:
                    if assignments[dt]["teaching"] == provider:
                        assigned_this_week.add(provider)
                if is_weekend_or_holiday(dt) and (dt, "dc2") in weekend_pre_assigned:
                    if assignments[dt]["dc2"] == provider:
                        assigned_this_week.add(provider)

        for need in ordered_needs:
            provider = need["provider"]
            eligible_dates = need["week_dates"]

            if provider in EXCLUDED_PROVIDERS:
                continue

            # Safety net: skip if this need was already fulfilled
            need_id = (provider, need["week_dates"][0])
            if need_id in fulfilled_needs:
                continue

            # Skip if already got a weekend LC this week from Phase 1.5
            if provider in assigned_this_week:
                continue

            # Find best day and slot for this provider
            best_assignment = find_best_assignment(
                provider, eligible_dates, daily_data, assignments,
                daily_slots, provider_lc_count, provider_weekend_lc,
                provider_dc1_count, provider_dc2_count,
                provider_day_of_week, weekends_worked, flags
            )

            if best_assignment:
                dt, slot = best_assignment
                assignments[dt][slot] = provider
                provider_lc_count[provider] += 1
                provider_day_of_week[provider].append(dt.weekday())

                if is_weekend_or_holiday(dt):
                    provider_weekend_lc[provider] += 1

                if slot == "dc1":
                    provider_dc1_count[provider] += 1
                elif slot == "dc2":
                    provider_dc2_count[provider] += 1

                # If this provider had missed before, reduce the count
                if provider_missed[provider] > 0:
                    provider_missed[provider] -= 1

                assigned_this_week.add(provider)
                fulfilled_needs.add(need_id)
            else:
                # Provider couldn't be assigned
                is_standalone = need.get("standalone_weekend", False)
                if is_standalone:
                    # Standalone weekends are deprioritized — no flag or missed count
                    pass
                else:
                    provider_missed[provider] += 1
                    provider_no_lc_weeks[provider] += 1
                    flags.append({
                        "date": need["week_dates"][0].strftime("%m/%d/%Y"),
                        "provider": provider,
                        "flag_type": "NO_LONGCALL",
                        "message": f"No long call assigned for week {need['week_dates'][0].strftime('%m/%d')} - {need['week_dates'][-1].strftime('%m/%d')}",
                    })

        # Update consecutive-week-without-LC tracker for all providers in this week
        # Skip standalone weekends — they don't count for consecutive tracking
        providers_in_week = set(n["provider"] for n in needs if not n.get("standalone_weekend"))
        for provider in providers_in_week:
            if provider in assigned_this_week:
                consecutive_no_lc[provider] = 0
            else:
                consecutive_no_lc[provider] = consecutive_no_lc.get(provider, 0) + 1
                # Flag if this is a second consecutive week without LC
                if consecutive_no_lc[provider] >= 2:
                    flags.append({
                        "date": needs[0]["week_dates"][0].strftime("%m/%d/%Y"),
                        "provider": provider,
                        "flag_type": "CONSEC_NO_LC",
                        "message": f"2+ consecutive weeks without long call ({consecutive_no_lc[provider]} weeks)",
                    })

    # --------------------------------------------------------
    # PHASE 2.5: Minimum guarantee — providers with weekday
    # weeks must get at least 1 long call
    # --------------------------------------------------------
    # Find providers who have 0 long calls but work at least one
    # non-moonlighting week that contains weekdays
    for need in assignment_needs:
        provider = need["provider"]
        if provider in EXCLUDED_PROVIDERS:
            continue
        if provider_lc_count[provider] > 0:
            continue  # already has at least one

        # Check if this need's week contains weekdays
        has_weekday = any(not is_weekend_or_holiday(d) for d in need["week_dates"])
        if not has_weekday:
            continue

        # This provider needs a guaranteed assignment. Try to find a slot.
        # Strategy: look for a slot on one of their eligible days where we
        # can swap out the current assignee (pick the one with the most LCs)
        eligible_dates = need["week_dates"]

        # First try: find an open slot (unlikely since Phase 2 just ran)
        best = find_best_assignment(
            provider, eligible_dates, daily_data, assignments,
            daily_slots, provider_lc_count, provider_weekend_lc,
            provider_dc1_count, provider_dc2_count,
            provider_day_of_week, weekends_worked, flags
        )
        if best:
            dt, slot = best
            assignments[dt][slot] = provider
            provider_lc_count[provider] += 1
            provider_day_of_week[provider].append(dt.weekday())
            if is_weekend_or_holiday(dt):
                provider_weekend_lc[provider] += 1
            if slot == "dc1":
                provider_dc1_count[provider] += 1
            elif slot == "dc2":
                provider_dc2_count[provider] += 1
            if provider_missed[provider] > 0:
                provider_missed[provider] -= 1
            continue

        # Second try: swap out the provider with the most long calls
        # on one of this provider's eligible days
        best_swap = None  # (dt, slot, displaced_provider, displaced_lc_count)
        for dt in eligible_dates:
            if dt not in daily_data:
                continue
            # Verify this provider is actually working this day
            working_today = any(p["provider"] == provider for p in daily_data[dt])
            if not working_today:
                continue
            provider_cat = get_provider_category(provider, dt, daily_data)
            for slot in daily_slots.get(dt, []):
                # DC providers cannot fill teaching slots
                if slot == "teaching" and provider_cat == "direct_care":
                    continue
                # Don't displace weekend pre-assignments from Phase 1.5
                if (dt, slot) in weekend_pre_assigned:
                    continue
                current = assignments[dt][slot]
                if current is None:
                    continue
                current_lc = provider_lc_count.get(current, 0)
                if current_lc <= 1:
                    continue  # don't displace someone who also only has 1
                if best_swap is None or current_lc > best_swap[3]:
                    best_swap = (dt, slot, current, current_lc)

        if best_swap:
            dt, slot, displaced, _ = best_swap
            # Undo the displaced provider's stats
            provider_lc_count[displaced] -= 1
            if is_weekend_or_holiday(dt):
                provider_weekend_lc[displaced] -= 1
            if slot == "dc1":
                provider_dc1_count[displaced] -= 1
            elif slot == "dc2":
                provider_dc2_count[displaced] -= 1
            # Remove the day from their DOW list
            dow = dt.weekday()
            if dow in provider_day_of_week[displaced]:
                provider_day_of_week[displaced].remove(dow)
            # Track the displaced provider's miss
            provider_missed[displaced] += 1

            # Assign the guaranteed provider
            assignments[dt][slot] = provider
            provider_lc_count[provider] += 1
            provider_day_of_week[provider].append(dt.weekday())
            if is_weekend_or_holiday(dt):
                provider_weekend_lc[provider] += 1
            if slot == "dc1":
                provider_dc1_count[provider] += 1
            elif slot == "dc2":
                provider_dc2_count[provider] += 1
            if provider_missed[provider] > 0:
                provider_missed[provider] -= 1

            flags.append({
                "date": dt.strftime("%m/%d/%Y"),
                "provider": provider,
                "flag_type": "GUARANTEED_SWAP",
                "message": f"Swapped in (minimum guarantee) — displaced {displaced}",
            })

    # Note: NO_LONGCALL flags are kept even if the provider has LCs in other weeks.
    # Each flag represents a specific week where no LC was assigned.

    # --------------------------------------------------------
    # PHASE 3: Fill remaining empty slots
    # --------------------------------------------------------
    # Track which providers already have a double this stretch
    # so we can enforce weekday+weekend split
    provider_double_dates = defaultdict(list)  # provider -> list of dates they got doubles

    for dt in all_dates:
        for slot in daily_slots.get(dt, []):
            if assignments[dt][slot] is None:
                # Find someone who can take a double
                filler = find_double_filler(
                    dt, slot, daily_data, assignments, daily_slots,
                    provider_lc_count, provider_weekend_lc,
                    provider_dc1_count, provider_dc2_count,
                    provider_day_of_week, provider_missed,
                    provider_double, weekends_worked,
                    provider_double_dates, all_stretches,
                    provider_wknd_lc_stretches, flags
                )
                if filler:
                    assignments[dt][slot] = filler
                    provider_lc_count[filler] += 1
                    provider_double[filler] += 1
                    provider_day_of_week[filler].append(dt.weekday())
                    provider_double_dates[filler].append(dt)

                    if is_weekend_or_holiday(dt):
                        provider_weekend_lc[filler] += 1

                    if slot == "dc1":
                        provider_dc1_count[filler] += 1
                    elif slot == "dc2":
                        provider_dc2_count[filler] += 1

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

    # --------------------------------------------------------
    # PHASE 4: Enforce max 1 missed week per provider
    # --------------------------------------------------------
    # Any provider with 2+ weeks worked should have at most 1 week without LC.
    # If a provider has 2+ missed weeks, swap them into a missed week by
    # displacing a provider who has 0 missed weeks and the most total LCs.

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

    # Iterate multiple times since one swap can create new misses
    for _round in range(10):
        # Find providers with 2+ missed weeks
        worst_providers = []
        for provider in all_providers:
            if provider in EXCLUDED_PROVIDERS:
                continue
            missed_weeks = compute_missed_weeks(provider)
            if len(missed_weeks) >= 2:
                worst_providers.append((provider, missed_weeks))

        if not worst_providers:
            break  # all good

        # Sort by most missed first
        worst_providers.sort(key=lambda x: -len(x[1]))

        swapped_any = False
        for provider, missed_weeks in worst_providers:
            # Try to swap into one of their missed weeks
            for week in missed_weeks:
                best_swap = None  # (dt, slot, displaced, displaced_missed, displaced_lc)

                for dt in week:
                    if dt not in daily_data:
                        continue
                    # Check provider is working this day on a source service
                    working = any(p["provider"] == provider for p in daily_data[dt])
                    if not working:
                        continue

                    # Don't swap into a weekend slot if provider already has 1 weekend LC
                    if is_weekend_or_holiday(dt) and provider_weekend_lc.get(provider, 0) >= 1:
                        continue

                    provider_cat = get_provider_category(provider, dt, daily_data)
                    for slot in daily_slots.get(dt, []):
                        # DC providers cannot fill teaching slots
                        if slot == "teaching" and provider_cat == "direct_care":
                            continue
                        # Don't displace weekend pre-assignments from Phase 1.5
                        if (dt, slot) in weekend_pre_assigned:
                            continue

                        displaced = assignments[dt][slot]
                        if displaced is None or displaced == provider:
                            continue

                        # Don't displace someone who would then have 2+ missed
                        displaced_missed_now = len(compute_missed_weeks(displaced))
                        displaced_lc = provider_lc_count.get(displaced, 0)

                        # Only displace if they have 0 missed (they'd go to 1)
                        if displaced_missed_now > 0:
                            continue

                        # Prefer displacing providers with the most total LCs
                        if best_swap is None or displaced_lc > best_swap[4]:
                            best_swap = (dt, slot, displaced, displaced_missed_now, displaced_lc)

                if best_swap:
                    dt, slot, displaced, _, _ = best_swap

                    # Undo displaced provider's stats
                    provider_lc_count[displaced] -= 1
                    if is_weekend_or_holiday(dt):
                        provider_weekend_lc[displaced] -= 1
                    if slot == "dc1":
                        provider_dc1_count[displaced] -= 1
                    elif slot == "dc2":
                        provider_dc2_count[displaced] -= 1
                    dow = dt.weekday()
                    if dow in provider_day_of_week[displaced]:
                        provider_day_of_week[displaced].remove(dow)

                    # Assign the swapped-in provider
                    assignments[dt][slot] = provider
                    provider_lc_count[provider] += 1
                    provider_day_of_week[provider].append(dt.weekday())
                    if is_weekend_or_holiday(dt):
                        provider_weekend_lc[provider] += 1
                    if slot == "dc1":
                        provider_dc1_count[provider] += 1
                    elif slot == "dc2":
                        provider_dc2_count[provider] += 1

                    flags.append({
                        "date": dt.strftime("%m/%d/%Y"),
                        "provider": provider,
                        "flag_type": "MISSED_SWAP",
                        "message": f"Swapped in (max 1 missed week rule) — displaced {displaced}",
                    })

                    swapped_any = True
                    break  # fixed one missed week for this provider, re-check in next round

        if not swapped_any:
            break  # no more swaps possible

    # (Phase 5 removed — weekend LC balance is now guaranteed by Phase 1.5 matching)

    # Compute weeks worked, standalone weekends, stretches, and missed weeks per provider.
    # These are computed from final assignments so they are always consistent.
    # Build LC lookup for final assignments
    lc_assigned = set()  # (date, provider) pairs with LC
    for dt, a in assignments.items():
        for slot in ["teaching", "dc1", "dc2"]:
            if a[slot]:
                lc_assigned.add((dt, a[slot]))

    # Build source-day lookup
    source_days_set = set()  # (provider, date) pairs on source services
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
                    # Check if this week has a LC in final assignments
                    has_lc = any((dt, provider) in lc_assigned for dt in week)
                    has_source = any((provider, dt) in source_days_set for dt in week)
                    if not has_lc and has_source:
                        total_no_lc += 1
        weeks_worked[provider] = total_weeks
        standalone_weekends_count[provider] = total_standalone
        stretches_count[provider] = total_stretches
        no_lc_weeks_count[provider] = total_no_lc

    # Build provider stats
    provider_stats = {}
    for provider in sorted(all_providers):
        if provider in EXCLUDED_PROVIDERS:
            continue
        provider_stats[provider] = {
            "total_long_calls": provider_lc_count.get(provider, 0),
            "weekend_long_calls": provider_weekend_lc.get(provider, 0),
            "dc1_count": provider_dc1_count.get(provider, 0),
            "dc2_count": provider_dc2_count.get(provider, 0),
            "missed": no_lc_weeks_count.get(provider, 0),
            "doubles": provider_double.get(provider, 0),
            "weekends_worked": weekends_worked.get(provider, 0),
            "weeks_worked": weeks_worked.get(provider, 0),
            "standalone_weekends": standalone_weekends_count.get(provider, 0),
            "stretches": stretches_count.get(provider, 0),
            "days_of_week": provider_day_of_week.get(provider, []),
        }

    return assignments, flags, provider_stats


def find_best_assignment(provider, eligible_dates, daily_data, assignments,
                         daily_slots, provider_lc_count, provider_weekend_lc,
                         provider_dc1_count, provider_dc2_count,
                         provider_day_of_week, weekends_worked, flags):
    """
    Find the best (date, slot) for a provider.
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
                # DC providers NEVER fill teaching slots.
                # Teaching slots are reserved for teaching service providers.
                continue
            elif slot == "teaching" and category == "teaching":
                priority = 0  # best match
            elif slot in ("dc1", "dc2") and category == "teaching":
                # Teaching overflow into DC
                priority = 5
            elif slot in ("dc1", "dc2") and category == "direct_care":
                priority = 1  # good match
            else:
                priority = 10

            # Weekend considerations — weekend slots are pre-assigned by Phase 1.5,
            # so this is just a safety net. Skip any remaining weekend slots.
            is_wknd = is_weekend_or_holiday(dt)
            if is_wknd:
                continue  # All weekend slots handled by Phase 1.5 matching

            # Day of week variety
            dow = dt.weekday()
            dow_counts = provider_day_of_week.get(provider, [])
            same_dow_count = dow_counts.count(dow)
            priority += same_dow_count * 3  # penalize same day of week

            # DC1 vs DC2 balance
            if slot == "dc1":
                dc1c = provider_dc1_count.get(provider, 0)
                dc2c = provider_dc2_count.get(provider, 0)
                if dc1c > dc2c:
                    priority += 2  # already has more dc1, penalize
            elif slot == "dc2":
                dc1c = provider_dc1_count.get(provider, 0)
                dc2c = provider_dc2_count.get(provider, 0)
                if dc2c > dc1c:
                    priority += 2  # already has more dc2, penalize

            candidates.append((priority, dt, slot))

    if not candidates:
        return None

    # Sort by priority (lower is better), then hash tiebreaker (not date or slot name)
    candidates.sort(key=lambda x: (x[0], tiebreak_hash(provider, x[1].strftime('%Y%m%d') + x[2])))
    best = candidates[0]
    return (best[1], best[2])


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


def find_double_filler(dt, slot, daily_data, assignments, daily_slots,
                       provider_lc_count, provider_weekend_lc,
                       provider_dc1_count, provider_dc2_count,
                       provider_day_of_week, provider_missed,
                       provider_double, weekends_worked,
                       provider_double_dates, all_stretches,
                       provider_wknd_lc_stretches, flags):
    """
    Find a provider to take a double long call to fill an empty slot.
    Prefer providers who previously missed a long call.

    HARD RULES:
    1. A provider is only eligible for a double if their stretch
       contains BOTH weekday AND weekend/holiday days.
    2. A provider who already has a weekend LC from Phase 1.5 in this
       same stretch is NOT eligible — the weekend LC already covers
       this stretch, so a double would unfairly burden them.
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
        already_today = False
        for s in ["teaching", "dc1", "dc2"]:
            if assignments[dt].get(s) == provider:
                already_today = True
                break
        if already_today:
            continue

        # HARD EXCLUSION: Provider's stretch must contain both weekday
        # and weekend days to be eligible for a double
        stretch = find_provider_stretch_for_date(provider, dt, all_stretches)
        if stretch is None:
            continue
        if not stretch_has_weekday_and_weekend(stretch):
            continue

        # HARD EXCLUSION: If this provider already got a weekend LC from
        # Phase 1.5 in this same stretch, skip them. The weekend LC already
        # covers this stretch — giving them a double is a penalty we should
        # give to someone else.
        if provider in provider_wknd_lc_stretches:
            if stretch[0] in provider_wknd_lc_stretches[provider]:
                continue

        # Score: prefer those who missed, then fewest doubles, then fewest total
        missed = provider_missed.get(provider, 0)
        doubles = provider_double.get(provider, 0)
        total = provider_lc_count.get(provider, 0)

        # Weekend LC limit — penalize heavily but allow as last resort
        # (Phase 3 is filling leftover slots; if everyone already has 1 wknd LC we must allow 2nd)
        weekend_penalty = 0
        if is_wknd and provider_weekend_lc.get(provider, 0) >= 1:
            weekend_penalty = 1000  # very heavy penalty, but not a hard skip

        # Weekday+weekend split enforcement for doubles
        # If this provider already has a double, check if the existing double
        # is same type (weekday/weekend) as this slot
        split_penalty = 0
        existing_doubles = provider_double_dates.get(provider, [])
        if existing_doubles:
            existing_has_weekday = any(not is_weekend_or_holiday(d) for d in existing_doubles)
            existing_has_weekend = any(is_weekend_or_holiday(d) for d in existing_doubles)

            if is_wknd and existing_has_weekend:
                split_penalty = 500
            elif not is_wknd and existing_has_weekday:
                split_penalty = 500

        # Penalize providers with few weekends — they already have limited
        # weekend time and shouldn't bear the double burden
        low_weekend_penalty = 0
        if weekends_worked.get(provider, 0) <= 1:
            low_weekend_penalty = 200

        score = (-missed * 100) + (doubles * 50) + total + weekend_penalty + split_penalty + low_weekend_penalty

        candidates.append((score, tiebreak_hash(provider, dt.strftime('%Y%m%d')), provider))

    if not candidates:
        return None

    candidates.sort()
    return candidates[0][2]


# ============================================================
# OUTPUT
# ============================================================

def format_output_table(assignments, daily_slots, flags):
    """Format assignments as a text table with dates down the left."""
    all_dates = sorted(assignments.keys())

    # Build flag lookup
    flag_lookup = defaultdict(list)
    for f in flags:
        flag_lookup[(f.get("date", ""), f.get("provider", ""))].append(f)

    # Header
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

        # Collect flags for this date
        date_flags = []
        for slot_provider in [a["teaching"], a["dc1"], a["dc2"]]:
            if slot_provider:
                key = (date_str, slot_provider)
                if key in flag_lookup:
                    for f in flag_lookup[key]:
                        date_flags.append(f"[{f['flag_type']}] {f['provider']}: {f['message']}")
        # Check for unfilled
        key = (date_str, "UNFILLED")
        if key in flag_lookup:
            for f in flag_lookup[key]:
                date_flags.append(f"[{f['flag_type']}] {f['message']}")

        flag_str = " | ".join(date_flags) if date_flags else ""

        # Mark weekends/holidays
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
            continue  # skip providers with no involvement

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

    # Also print summary stats
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
