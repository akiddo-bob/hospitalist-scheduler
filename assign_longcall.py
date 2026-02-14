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
    # PHASE 1.5: Pre-assign ALL weekend slots via weighted bipartite matching
    # --------------------------------------------------------
    # Weekend LC balance is a global constraint that cannot be solved greedily.
    # We use minimum-weight bipartite matching to assign one weekend slot per
    # provider, with weights that prioritize providers whose stretch spans
    # both weekday and weekend days. This prevents two-weekday doubles: a
    # provider with a weekday+weekend stretch who gets a weekend LC from
    # Phase 1.5 will have the proper weekday+weekend split if Phase 3 later
    # gives them a double.

    weekend_dates = sorted([dt for dt in all_dates if is_weekend_or_holiday(dt)])

    # Pre-compute: for each provider, which of their stretches span both
    # weekday and weekend/holiday days?  These providers NEED a weekend slot
    # to avoid a two-weekday double if they get a double assignment later.
    provider_needs_weekend_slot = {}  # provider -> set of weekend dates in mixed stretches
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

    # Pre-compute stretch length (weekday count) for providers who need weekend slots.
    # Longer stretches = more urgent need for a weekend slot (more weekdays that
    # could become two-weekday doubles without it).
    provider_need_urgency = {}  # provider -> max weekday count across mixed stretches with this date
    for provider, needed_dates in provider_needs_weekend_slot.items():
        for pstretch in all_stretches.get(provider, []):
            weekday_count = sum(1 for d in pstretch if not is_weekend_or_holiday(d))
            for d in pstretch:
                if d in needed_dates:
                    old = provider_need_urgency.get((provider, d), 0)
                    provider_need_urgency[(provider, d)] = max(old, weekday_count)

    def build_weighted_weekend_graph(min_weekends):
        """Build weighted bipartite graph for weekend matching.

        Edge weights (lower = higher priority in minimum-weight matching):
          - 1: provider NEEDS this weekend slot (long mixed stretch, 5+ weekdays)
               and the slot's category matches the provider's category
          - 2: provider NEEDS this slot (shorter mixed stretch) + cat match
          - 4: provider NEEDS this slot but category doesn't match (overflow)
          - 50: provider doesn't need this slot (weekend-only stretch)
               and category matches
          - 60: provider doesn't need this slot and category doesn't match

        The large gap (2-4 vs 50-60) ensures providers with mixed stretches
        get strong priority over standalone-weekend providers.
        """
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

                    # Determine weight
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

                    # Add tiny tiebreaker (0 to 0.9) to break symmetry between
                    # equivalent edges, producing different valid matchings per seed.
                    weight += tiebreak_hash(provider, f"wknd_{dt.strftime('%Y%m%d')}_{slot}") * 0.9

                    G.add_edge(pnode, slot_id, weight=weight)
        return G, s_nodes

    # Run weighted matching
    G, slot_nodes = build_weighted_weekend_graph(MIN_WEEKENDS_FOR_WKND_LC)
    try:
        raw_matching = nx.bipartite.minimum_weight_full_matching(G, top_nodes=slot_nodes)
    except (ValueError, nx.NetworkXError):
        # Fall back to unweighted matching if weighted fails
        raw_matching = nx.bipartite.maximum_matching(G, top_nodes=slot_nodes)
    matched_slots = {}
    for k, v in raw_matching.items():
        if k in slot_nodes:
            matched_slots[k] = v

    # Check if all slots are filled
    all_slot_nodes = set()
    for dt in weekend_dates:
        for slot in daily_slots[dt]:
            all_slot_nodes.add(f"slot_{dt.strftime('%Y%m%d')}_{slot}")
    total_weekend_slots = len(all_slot_nodes)

    if len(matched_slots) < total_weekend_slots:
        # Some slots unfilled — fall back to including all providers (min_weekends=1)
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
    # PHASE 1.75: Give multi-week mixed stretches a weekend LC
    # --------------------------------------------------------
    # Phase 1.5 gives each provider ONE weekend slot globally. But providers
    # with multi-week mixed stretches need a weekend LC in THAT stretch to
    # avoid two-weekday doubles. For each such unserved stretch, try to swap
    # the provider into a weekend slot by displacing someone less critical.
    for provider, pstretches in all_stretches.items():
        if provider in EXCLUDED_PROVIDERS:
            continue
        for stretch in pstretches:
            if is_standalone_weekend(stretch):
                continue
            has_weekday = any(not is_weekend_or_holiday(d) for d in stretch)
            has_weekend = any(is_weekend_or_holiday(d) for d in stretch)
            if not (has_weekday and has_weekend):
                continue

            # Does provider already have a weekend LC in this stretch?
            has_wknd_lc_here = False
            for d in stretch:
                if is_weekend_or_holiday(d):
                    for s in ["teaching", "dc1", "dc2"]:
                        if assignments.get(d, {}).get(s) == provider:
                            has_wknd_lc_here = True
            if has_wknd_lc_here:
                continue

            # Try to get provider a weekend slot in this stretch
            we_dates = [d for d in stretch if is_weekend_or_holiday(d)]
            best_swap = None  # (priority, wdt, wslot, displaced)
            for wdt in we_dates:
                prov_cat = get_provider_category(provider, wdt, daily_data)
                if not prov_cat:
                    continue
                for wslot in daily_slots.get(wdt, []):
                    if wslot == "teaching" and prov_cat == "direct_care":
                        continue
                    if assignments[wdt][wslot] is None:
                        # Empty slot! Just take it.
                        best_swap = (-1, 0, wdt, wslot, None)
                        break
                    displaced = assignments[wdt][wslot]
                    if displaced == provider:
                        continue  # already us

                    # Evaluate displacement priority — prefer displacing:
                    # 0: empty slots (handled above)
                    # 1: standalone-weekend providers (don't need weekend LC at all)
                    # 2: single-week mixed-stretch providers (less critical need)
                    # 3+: multi-week mixed-stretch providers — don't displace
                    disp_stretch = find_provider_stretch_for_date(displaced, wdt, all_stretches)
                    disp_priority = 1  # default: standalone weekend
                    if disp_stretch and not is_standalone_weekend(disp_stretch):
                        disp_has_weekday = any(not is_weekend_or_holiday(d) for d in disp_stretch)
                        disp_has_weekend = any(is_weekend_or_holiday(d) for d in disp_stretch)
                        if disp_has_weekday and disp_has_weekend:
                            disp_weeks = split_stretch_into_weeks(disp_stretch)
                            disp_real_weeks = [w for w in disp_weeks
                                               if not is_moonlighting_in_stretch(displaced, w, daily_data)
                                               and not (all(is_weekend_or_holiday(d) for d in w) and len(w) <= 2)]
                            if len(disp_real_weeks) >= 2:
                                continue  # multi-week mixed — they also need it, don't displace
                            else:
                                disp_priority = 2  # single-week mixed — less critical
                        else:
                            disp_priority = 1  # weekday-only or weekend-only stretch

                    # Prefer: (lower disp_priority) displacing standalone > single-week, then cat match
                    cat_match = 0 if (
                        (wslot == "teaching" and prov_cat == "teaching") or
                        (wslot in ("dc1", "dc2") and prov_cat == "direct_care")
                    ) else 1
                    swap_score = (disp_priority, cat_match, wdt)
                    if best_swap is None or swap_score < best_swap[:3]:
                        best_swap = (disp_priority, cat_match, wdt, wslot, displaced)

                if best_swap and best_swap[0] == -1:
                    break  # found empty slot

            if best_swap:
                _, _, wdt, wslot, displaced = best_swap
                if displaced:
                    # Undo displaced provider's stats
                    provider_lc_count[displaced] -= 1
                    provider_weekend_lc[displaced] -= 1
                    if wslot == "dc1":
                        provider_dc1_count[displaced] -= 1
                    elif wslot == "dc2":
                        provider_dc2_count[displaced] -= 1
                    dow = wdt.weekday()
                    if dow in provider_day_of_week.get(displaced, []):
                        provider_day_of_week[displaced].remove(dow)
                    weekend_pre_assigned.discard((wdt, wslot))

                # Assign provider
                assignments[wdt][wslot] = provider
                provider_lc_count[provider] += 1
                provider_weekend_lc[provider] += 1
                provider_day_of_week[provider].append(wdt.weekday())
                if wslot == "dc1":
                    provider_dc1_count[provider] += 1
                elif wslot == "dc2":
                    provider_dc2_count[provider] += 1
                weekend_pre_assigned.add((wdt, wslot))

                pstretch = find_provider_stretch_for_date(provider, wdt, all_stretches)
                if pstretch:
                    provider_wknd_lc_stretches[provider].add(pstretch[0])

    # --------------------------------------------------------
    # PHASE 2: For each assignment need, pick a day and slot
    # --------------------------------------------------------
    # Group needs by week (ISO week number + year)
    week_groups = defaultdict(list)
    for need in assignment_needs:
        # Use the first weekday in the chunk to determine which ISO week
        # this need belongs to. This handles merged Sat-Sun-Mon...Fri chunks
        # correctly — the weekday portion determines the week group, not the
        # leading weekend fragment.
        weekday_dates = [d for d in need["week_dates"] if not is_weekend_or_holiday(d)]
        key_day = weekday_dates[0] if weekday_dates else need["week_dates"][0]
        week_key = (key_day.isocalendar()[0], key_day.isocalendar()[1])
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
    # PHASE 3 + 3.5 with RETRY LOOP
    # --------------------------------------------------------
    # We wrap Phase 3 (fill remaining empty slots) and Phase 3.5 (fix two-weekday
    # doubles) in a retry loop. Each attempt uses a different slot ordering.
    # If one ordering creates unsolvable two-weekday violations, another ordering
    # may produce a better result. We keep the best attempt.

    def _count_two_weekday_violations(asn):
        """Count two-weekday double violations in the given assignments."""
        count = 0
        for provider, pstretches in all_stretches.items():
            for stretch in pstretches:
                if is_standalone_weekend(stretch):
                    continue
                lc_dates = []
                for sdt in stretch:
                    for sslot in ["teaching", "dc1", "dc2"]:
                        if asn.get(sdt, {}).get(sslot) == provider:
                            lc_dates.append(sdt)
                if len(lc_dates) >= 2:
                    weekday_lcs_in = [d for d in lc_dates if not is_weekend_or_holiday(d)]
                    if len(weekday_lcs_in) >= 2:
                        count += 1
        return count

    # Save state before Phase 3 so we can restore and retry
    def _save_defaultdict(dd):
        """Copy a defaultdict preserving its default_factory."""
        result = defaultdict(dd.default_factory)
        result.update(dd)
        return result

    _save_assignments = copy.deepcopy(assignments)
    _save_lc_count = _save_defaultdict(provider_lc_count)
    _save_weekend_lc = _save_defaultdict(provider_weekend_lc)
    _save_dc1_count = _save_defaultdict(provider_dc1_count)
    _save_dc2_count = _save_defaultdict(provider_dc2_count)
    _save_day_of_week = defaultdict(list, {k: list(v) for k, v in provider_day_of_week.items()})
    _save_missed = _save_defaultdict(provider_missed)
    _save_double = _save_defaultdict(provider_double)
    _save_flags = list(flags)
    _save_wknd_lc_stretches = copy.deepcopy(provider_wknd_lc_stretches)

    best_attempt = None  # (violations, assignments, counters, flags)
    MAX_PHASE3_ATTEMPTS = 50

    for _attempt in range(MAX_PHASE3_ATTEMPTS):
        # Restore state to pre-Phase-3
        if _attempt > 0:
            assignments = copy.deepcopy(_save_assignments)
            provider_lc_count = _save_defaultdict(_save_lc_count)
            provider_weekend_lc = _save_defaultdict(_save_weekend_lc)
            provider_dc1_count = _save_defaultdict(_save_dc1_count)
            provider_dc2_count = _save_defaultdict(_save_dc2_count)
            provider_day_of_week = defaultdict(list, {k: list(v) for k, v in _save_day_of_week.items()})
            provider_missed = _save_defaultdict(_save_missed)
            provider_double = _save_defaultdict(_save_double)
            flags = list(_save_flags)
            provider_wknd_lc_stretches = copy.deepcopy(_save_wknd_lc_stretches)

        provider_double_dates = defaultdict(list)

        # Collect all empty slots
        empty_slots = []
        for dt in all_dates:
            for slot in daily_slots.get(dt, []):
                if assignments[dt][slot] is None:
                    empty_slots.append((dt, slot))

        # Sort: weekend slots first, then weekday slots (both in date order)
        # On retries, add a seeded shuffle within each group to try different orderings
        if _attempt == 0:
            empty_slots.sort(key=lambda x: (0 if is_weekend_or_holiday(x[0]) else 1, x[0]))
        else:
            # Use a deterministic but different shuffle for each attempt+seed combo
            attempt_rng = random.Random(f"{VARIATION_SEED}_attempt_{_attempt}")
            weekend_slots = [(dt, s) for dt, s in empty_slots if is_weekend_or_holiday(dt)]
            weekday_slots = [(dt, s) for dt, s in empty_slots if not is_weekend_or_holiday(dt)]
            attempt_rng.shuffle(weekend_slots)
            attempt_rng.shuffle(weekday_slots)
            empty_slots = weekend_slots + weekday_slots

        # ---- PHASE 3: Fill remaining empty slots ----
        for dt, slot in empty_slots:
            if assignments[dt][slot] is not None:
                continue  # may have been filled by a previous iteration's side effect
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

        # ---- PHASE 3.5: Fix two-weekday doubles by reshuffling ----
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

                                prov_wknd_lcs = provider_weekend_lc.get(provider, 0)
                                swap_candidates.append((
                                    prov_wknd_lcs,
                                    prov_gap_penalty,
                                    wdt, wslot, occupant, wkdy_dt, wkdy_slot))

                    swap_candidates.sort()
                    if swap_candidates:
                        _, _, wdt, wslot, occupant, wkdy_dt, wkdy_slot = swap_candidates[0]
                        if provider_weekend_lc.get(provider, 0) < 2:
                            assignments[wkdy_dt][wkdy_slot] = occupant
                            assignments[wdt][wslot] = provider
                            provider_weekend_lc[provider] += 1
                            provider_weekend_lc[occupant] = max(0, provider_weekend_lc.get(occupant, 0) - 1)
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

                                prov_wknd_lcs = provider_weekend_lc.get(provider, 0)
                                if prov_wknd_lcs >= 2:
                                    continue

                                reshuffle_options.append((
                                    prov_wknd_lcs,
                                    reshuffle_gap_penalty,
                                    wdt, wslot, occupant,
                                    wkdy_dt, wkdy_slot))

                    reshuffle_options.sort()
                    for _, _, wdt, wslot, occupant, wkdy_dt, wkdy_slot in reshuffle_options:
                        assignments[wkdy_dt][wkdy_slot] = None
                        provider_lc_count[provider] -= 1
                        provider_double[provider] = max(0, provider_double.get(provider, 0) - 1)

                        old_occupant = assignments[wdt][wslot]
                        assignments[wdt][wslot] = provider
                        provider_weekend_lc[provider] += 1
                        provider_lc_count[provider] += 1

                        if old_occupant:
                            provider_lc_count[old_occupant] = max(0, provider_lc_count.get(old_occupant, 0) - 1)
                            provider_weekend_lc[old_occupant] = max(0, provider_weekend_lc.get(old_occupant, 0) - 1)
                            if wslot == "dc1":
                                provider_dc1_count[old_occupant] = max(0, provider_dc1_count.get(old_occupant, 0) - 1)
                            elif wslot == "dc2":
                                provider_dc2_count[old_occupant] = max(0, provider_dc2_count.get(old_occupant, 0) - 1)

                        new_filler = find_double_filler(
                            wkdy_dt, wkdy_slot, daily_data, assignments, daily_slots,
                            provider_lc_count, provider_weekend_lc,
                            provider_dc1_count, provider_dc2_count,
                            provider_day_of_week, provider_missed,
                            provider_double, weekends_worked,
                            provider_double_dates, all_stretches,
                            provider_wknd_lc_stretches, flags
                        )

                        if new_filler and new_filler != provider:
                            assignments[wkdy_dt][wkdy_slot] = new_filler
                            provider_lc_count[new_filler] += 1
                            provider_double[new_filler] = provider_double.get(new_filler, 0) + 1
                            if wkdy_slot == "dc1":
                                provider_dc1_count[new_filler] = provider_dc1_count.get(new_filler, 0) + 1
                            elif wkdy_slot == "dc2":
                                provider_dc2_count[new_filler] = provider_dc2_count.get(new_filler, 0) + 1

                            fixed = True
                            any_fixed = True
                            break
                        else:
                            assignments[wkdy_dt][wkdy_slot] = provider
                            provider_lc_count[provider] += 1
                            provider_double[provider] = provider_double.get(provider, 0) + 1

                            assignments[wdt][wslot] = old_occupant
                            provider_weekend_lc[provider] -= 1
                            provider_lc_count[provider] -= 1

                            if old_occupant:
                                provider_lc_count[old_occupant] = provider_lc_count.get(old_occupant, 0) + 1
                                provider_weekend_lc[old_occupant] = provider_weekend_lc.get(old_occupant, 0) + 1
                                if wslot == "dc1":
                                    provider_dc1_count[old_occupant] = provider_dc1_count.get(old_occupant, 0) + 1
                                elif wslot == "dc2":
                                    provider_dc2_count[old_occupant] = provider_dc2_count.get(old_occupant, 0) + 1
                            continue

            if not any_fixed:
                break

        # Count violations for this attempt
        violations = _count_two_weekday_violations(assignments)
        if violations == 0:
            # Perfect! Use this attempt
            best_attempt = None  # signal: use current state directly
            break

        # Save this attempt if it's the best so far
        attempt_state = {
            "violations": violations,
            "assignments": copy.deepcopy(assignments),
            "lc_count": _save_defaultdict(provider_lc_count),
            "weekend_lc": _save_defaultdict(provider_weekend_lc),
            "dc1_count": _save_defaultdict(provider_dc1_count),
            "dc2_count": _save_defaultdict(provider_dc2_count),
            "day_of_week": defaultdict(list, {k: list(v) for k, v in provider_day_of_week.items()}),
            "missed": _save_defaultdict(provider_missed),
            "double": _save_defaultdict(provider_double),
            "flags": list(flags),
            "wknd_lc_stretches": copy.deepcopy(provider_wknd_lc_stretches),
        }
        if best_attempt is None or violations < best_attempt["violations"]:
            best_attempt = attempt_state

    # Restore best attempt if we didn't find a perfect solution
    if best_attempt is not None:
        assignments = best_attempt["assignments"]
        provider_lc_count = best_attempt["lc_count"]
        provider_weekend_lc = best_attempt["weekend_lc"]
        provider_dc1_count = best_attempt["dc1_count"]
        provider_dc2_count = best_attempt["dc2_count"]
        provider_day_of_week = best_attempt["day_of_week"]
        provider_missed = best_attempt["missed"]
        provider_double = best_attempt["double"]
        flags = best_attempt["flags"]
        provider_wknd_lc_stretches = best_attempt["wknd_lc_stretches"]

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
                best_swap_score = None

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

                    # Check if swapping here would create a two-weekday double
                    # in the provider's stretch
                    creates_two_weekday = False
                    prov_stretch = find_provider_stretch_for_date(provider, dt, all_stretches)
                    if prov_stretch:
                        # Collect existing LC dates in this stretch
                        existing_lc_dates = [
                            sd for sd in prov_stretch
                            for ss in ["teaching", "dc1", "dc2"]
                            if assignments.get(sd, {}).get(ss) == provider
                        ]

                        # RULE: No double if stretch has a holiday LC
                        if any(is_holiday(d) for d in existing_lc_dates) or is_holiday(dt):
                            continue

                        # RULE: Prefer 3+ calendar day gap between LCs (hard min 2)
                        phase4_gap_penalty = 0
                        if existing_lc_dates:
                            min_gap = min(abs((dt - d).days) for d in existing_lc_dates)
                            if min_gap < 2:
                                continue
                            elif min_gap < 3:
                                phase4_gap_penalty = 1  # prefer 3+ but allow 2

                        if not is_weekend_or_holiday(dt):
                            existing_weekday_lcs = sum(
                                1 for d in existing_lc_dates
                                if not is_weekend_or_holiday(d)
                            )
                            if existing_weekday_lcs >= 1:
                                creates_two_weekday = True

                    # HARD FILTER: never create a two-weekday double
                    if creates_two_weekday:
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

                        # Prefer: (1) 3+ gap, (2) most LCs displaced
                        swap_score = (phase4_gap_penalty, -displaced_lc)
                        if best_swap is None or swap_score < best_swap_score:
                            best_swap = (dt, slot, displaced, displaced_missed_now, displaced_lc)
                            best_swap_score = swap_score

                if best_swap and not best_swap_score[0]:
                    # Only proceed if the swap doesn't create a two-weekday double
                    # (best_swap_score[0] is creates_two_weekday flag)
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

            # Weekend considerations — weekend slots are pre-assigned by Phase 1.5.
            # Skip weekend slots UNLESS they're empty (Phase 1.5 didn't fill them).
            is_wknd = is_weekend_or_holiday(dt)
            if is_wknd:
                continue  # Weekend slots handled by Phase 1.5 matching

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

    Hard rules (provider is excluded if violated):
    1. Must have 3+ calendar days gap between the two LCs in a stretch.
    2. No double allowed if the stretch already contains a holiday LC.
    3. Provider's stretch must span both weekday and weekend days.

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
        already_today = False
        for s in ["teaching", "dc1", "dc2"]:
            if assignments[dt].get(s) == provider:
                already_today = True
                break
        if already_today:
            continue

        # Provider's stretch must contain both weekday and weekend days
        stretch = find_provider_stretch_for_date(provider, dt, all_stretches)
        if stretch is None:
            continue
        if not stretch_has_weekday_and_weekend(stretch):
            continue

        # Check existing LCs in this same stretch
        existing_lc_dates_in_stretch = []
        for s_dt in stretch:
            for s_slot in ["teaching", "dc1", "dc2"]:
                if assignments.get(s_dt, {}).get(s_slot) == provider:
                    existing_lc_dates_in_stretch.append(s_dt)

        # For multi-week stretches, don't give more LCs than weeks
        # (Phase 1.75 + Phase 2 already assigned the right number).
        # Single-week stretches can get doubles (up to 2 LCs).
        stretch_weeks = split_stretch_into_weeks(stretch)
        real_weeks = [w for w in stretch_weeks
                      if not is_moonlighting_in_stretch(provider, w, daily_data)
                      and not (all(is_weekend_or_holiday(d) for d in w) and len(w) <= 2)]
        num_weeks = len(real_weeks)
        if num_weeks >= 2 and len(existing_lc_dates_in_stretch) >= num_weeks:
            continue
        # For any stretch, never exceed 2 LCs total
        if len(existing_lc_dates_in_stretch) >= 2:
            continue

        # RULE 2: No double if stretch already has a holiday LC
        has_holiday_lc = any(is_holiday(d) for d in existing_lc_dates_in_stretch)
        if has_holiday_lc:
            continue
        # Also skip if the new date IS a holiday and would create a double
        if is_holiday(dt) and existing_lc_dates_in_stretch:
            continue

        # RULE 1: Compute gap between LCs (used for scoring below)
        min_gap = 999
        if existing_lc_dates_in_stretch:
            min_gap = min(abs((dt - d).days) for d in existing_lc_dates_in_stretch)
            # Hard filter: never allow gap < 2 (no back-to-back or 1-day-apart)
            if min_gap < 2:
                continue

        existing_has_weekday_lc = any(not is_weekend_or_holiday(d) for d in existing_lc_dates_in_stretch)
        existing_has_weekend_lc = any(is_weekend_or_holiday(d) for d in existing_lc_dates_in_stretch)

        # Determine split quality for this double:
        # 0 = creates proper weekday+weekend split (best)
        # 1 = no existing LC in stretch — first assignment, ok but not a split yet
        # 2 = same-type double (weekday+weekday or weekend+weekend, worst)
        if not existing_lc_dates_in_stretch:
            split_tier = 1
        elif is_wknd and existing_has_weekday_lc and not existing_has_weekend_lc:
            split_tier = 0  # adding weekend to existing weekday = good split
        elif not is_wknd and existing_has_weekend_lc and not existing_has_weekday_lc:
            split_tier = 0  # adding weekday to existing weekend = good split
        elif is_wknd and existing_has_weekend_lc:
            split_tier = 2  # weekend+weekend = bad
            continue  # HARD FILTER: never create two-weekend doubles
        elif not is_wknd and existing_has_weekday_lc:
            split_tier = 2  # weekday+weekday = bad
            # HARD FILTER: never create a two-weekday double in a mixed stretch
            # (this is the Check 1 rule — weekday+weekend split is required)
            continue
        else:
            split_tier = 1  # mixed existing + compatible new

        # Penalize gap < 3 (prefer 3+ day gaps, but allow 2-day gaps as fallback)
        gap_penalty = 0 if min_gap >= 3 else 500

        # RULE 3: Prefer providers who have at least one week with no LC.
        # A provider who already has an LC in every week should not get a double
        # unless no one else is available.
        provider_stretches = all_stretches.get(provider, [])
        all_lc_dates = set()
        for ps in provider_stretches:
            for ps_dt in ps:
                for ps_slot in ["teaching", "dc1", "dc2"]:
                    if assignments.get(ps_dt, {}).get(ps_slot) == provider:
                        all_lc_dates.add(ps_dt)
        found_empty_week = False
        for ps in provider_stretches:
            if is_standalone_weekend(ps):
                continue
            weeks = split_stretch_into_weeks(ps)
            for week in weeks:
                if not any(wd in all_lc_dates for wd in week):
                    found_empty_week = True
                    break
            if found_empty_week:
                break
        has_empty_week = 0 if found_empty_week else 1  # 0=has empty (preferred)

        # Score: prefer those who missed, then fewest doubles, then fewest total
        missed = provider_missed.get(provider, 0)
        doubles = provider_double.get(provider, 0)
        total = provider_lc_count.get(provider, 0)

        # Weekend LC limit — penalize heavily but allow as last resort
        weekend_penalty = 0
        if is_wknd and provider_weekend_lc.get(provider, 0) >= 1:
            weekend_penalty = 1000

        # Penalize providers with few weekends
        low_weekend_penalty = 0
        if weekends_worked.get(provider, 0) <= 1:
            low_weekend_penalty = 200

        score = (-missed * 100) + (doubles * 50) + total + weekend_penalty + low_weekend_penalty + gap_penalty

        # Sort: has_empty_week first (prefer providers with empty weeks),
        # then split_tier (prefer weekday+weekend splits),
        # then score (fairness), then tiebreak.
        candidates.append((has_empty_week, split_tier, score, tiebreak_hash(provider, dt.strftime('%Y%m%d')), provider))

    if not candidates:
        return None

    candidates.sort()
    return candidates[0][4]


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
