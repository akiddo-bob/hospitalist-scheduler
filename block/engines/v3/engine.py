#!/usr/bin/env python3
"""
Block Schedule Engine v3 — Constraint propagation with gap reporting.

Key differences from v1/v2:
  - Pre-scheduler provides validated inputs (tags, prior actuals, difficulty, holidays)
  - Phase 1 reserves critical (zero-gap) sites before general assignment
  - Constraint propagation: look-ahead prevents assignments that create impossible future slots
  - Never force-fills — unfilled slots go to actionable gap report
  - Two-stage output: clean draft schedule + gap report with candidate lists
  - Swap evaluation reduces gaps without creating new violations

Design principles:
  - Hard constraints are NEVER relaxed
  - Availability is SACRED — never overridden
  - Every assignment in the draft is solid — no second-guessing needed
  - Gaps are reported with viable candidates so manual scheduler can close them
"""

import json
import math
import os
import random
from collections import defaultdict
from datetime import datetime, date, timedelta

import sys
_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINES_DIR = os.path.dirname(_ENGINE_DIR)
_BLOCK_DIR = os.path.dirname(_ENGINES_DIR)
_PROJECT_ROOT = os.path.dirname(_BLOCK_DIR)

if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from block.engines.shared.loader import (
    load_availability, build_name_map, build_periods,
    get_eligible_sites, has_tag, get_tag_rules, is_available,
    SITE_PCT_MAP, PCT_TO_SITES,
)
from block.engines.v3.excel_io import (
    load_providers_from_excel, load_tags_from_excel, load_sites_from_excel,
)

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

# Site gap tolerance tiers (Section 3.6 of block-scheduling-rules.md)
# Tier 0: zero gaps — must be fully filled every period
# Tier 1: up to 1 gap per day — small shortfalls acceptable
# Tier 2: gaps expected — moonlighters fill remaining
SITE_GAP_TOLERANCE = {
    "Mullica Hill": 1,
    "Vineland": 1,
    "Cooper": 2,
    # Everything else defaults to 0 (zero gaps required)
}

# Sites that MUST have zero gaps — failure here means the gap report
# must surface these prominently
ZERO_GAP_SITES = [
    "Cape", "Mannington", "Elmer",
    "Virtua Voorhees", "Virtua Marlton", "Virtua Willingboro", "Virtua Mt Holly",
]

# Maximum consecutive calendar days a provider can work (hard constraint)
MAX_CONSECUTIVE_DAYS = 12

# Conflict pairs — providers who cannot work the same week
CONFLICT_PAIR_NAMES = [("HAROLDSON", "MCMILLIAN")]

# Memorial Day week for Block 3, cycle 25-26
MEMORIAL_WEEK_NUM = None  # Set during initialization based on dates


def _gap_tolerance(site):
    return SITE_GAP_TOLERANCE.get(site, 0)


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 0: LOAD & VALIDATE
# ═════════════════════════════════════════════════════════════════════════════

def phase0_load(excel_path, pre_schedule_path, availability_dir,
                block_start, block_end, seed=42):
    """Load all data, filter eligible providers, compute targets.

    Consumes pre-scheduler output for accurate prior actuals and tag config.
    Reads Excel workbook for provider data, site demand, and manual corrections.

    Returns:
        dict (engine state) — all data structures needed by later phases
    """
    random.seed(seed)

    print(f"{'=' * 70}")
    print(f"BLOCK SCHEDULE ENGINE v3 — seed={seed}")
    print(f"Block: {block_start.strftime('%Y-%m-%d')} to {block_end.strftime('%Y-%m-%d')}")
    print(f"{'=' * 70}")

    # ── Load pre-scheduler output ─────────────────────────────────────
    print("\n[Phase 0] Loading data...")

    pre_data = {}
    if pre_schedule_path and os.path.exists(pre_schedule_path):
        with open(pre_schedule_path) as f:
            pre_data = json.load(f)
        print(f"  Pre-scheduler output: loaded")
    else:
        print(f"  Pre-scheduler output: not found, using Excel values only")

    # ── Load Excel workbook ───────────────────────────────────────────
    import openpyxl
    wb = openpyxl.load_workbook(excel_path, data_only=True)

    providers = load_providers_from_excel(wb)
    tags_data = load_tags_from_excel(wb)
    sites_demand = load_sites_from_excel(wb)

    print(f"  Providers:     {len(providers)}")
    print(f"  Tags:          {sum(len(v) for v in tags_data.values())} tags across {len(tags_data)} providers")
    print(f"  Sites demand:  {len(sites_demand)} entries")

    # ── Override prior actuals from pre-scheduler if available ─────────
    computed_actuals = {}
    if "prior_actuals" in pre_data:
        computed_actuals = pre_data["prior_actuals"].get("computed", {})
        if computed_actuals:
            overrides = 0
            for pname, pdata in providers.items():
                if pname in computed_actuals:
                    ca = computed_actuals[pname]
                    comp_wk = ca.get("prior_weeks", ca.get("weeks", 0))
                    comp_we = ca.get("prior_weekends", ca.get("weekends", 0))
                    if comp_wk == 0 and comp_we == 0:
                        continue  # no computed data — keep Excel values
                    new_wk_rem = max(0, pdata["annual_weeks"] - comp_wk)
                    new_we_rem = max(0, pdata["annual_weekends"] - comp_we)
                    if abs(new_wk_rem - pdata["weeks_remaining"]) > 0.3 or \
                       abs(new_we_rem - pdata["weekends_remaining"]) > 0.3:
                        overrides += 1
                    pdata["weeks_remaining"] = new_wk_rem
                    pdata["weekends_remaining"] = new_we_rem
            print(f"  Prior actuals: {len(computed_actuals)} computed, {overrides} overrides applied")

    # ── Load difficulty/holiday info from pre-scheduler ────────────────
    difficulty_records = {}
    if "difficulty" in pre_data:
        for rec in pre_data["difficulty"].get("records", []):
            difficulty_records[rec["provider"]] = rec

    holiday_records = {}
    if "holiday" in pre_data:
        for rec in pre_data["holiday"].get("records", []):
            holiday_records[rec["provider"]] = rec

    # ── Load availability ─────────────────────────────────────────────
    unavailable_dates = load_availability()
    print(f"  Availability:  {len(unavailable_dates)} providers with JSON files")

    # ── Match provider names to availability JSONs ────────────────────
    name_map, unmatched = build_name_map(providers, unavailable_dates)
    matched_count = len(name_map) - len(unmatched)
    print(f"  Name matching: {matched_count}/{len(providers)} matched "
          f"({len(unmatched)} unmatched — treated as fully available)")

    # ── Build period list ─────────────────────────────────────────────
    periods = build_periods(block_start, block_end)
    n_weeks = sum(1 for p in periods if p["type"] == "week")
    n_weekends = sum(1 for p in periods if p["type"] == "weekend")
    print(f"  Periods:       {n_weeks} weeks, {n_weekends} weekends")

    # ── Identify Memorial Day week ────────────────────────────────────
    memorial_day_date = _find_memorial_day(block_start.year, block_end.year)
    memorial_week_num = None
    if memorial_day_date:
        for p in periods:
            if p["type"] == "week":
                for d_str in p["dates"]:
                    if d_str == memorial_day_date.strftime("%Y-%m-%d"):
                        memorial_week_num = p["num"]
                        break
            if memorial_week_num:
                break
    if memorial_week_num:
        print(f"  Memorial Day:  week {memorial_week_num} ({memorial_day_date})")

    # ── Filter to eligible providers ──────────────────────────────────
    eligible = {}
    excluded_reasons = defaultdict(list)

    for pname, pdata in providers.items():
        if has_tag(pname, "do_not_schedule", tags_data):
            excluded_reasons["do_not_schedule"].append(pname)
            continue

        if pdata["weeks_remaining"] <= 0 and pdata["weekends_remaining"] <= 0:
            if pdata["shift_type"] == "Nights":
                excluded_reasons["pure_nocturnist"].append(pname)
            else:
                excluded_reasons["zero_remaining"].append(pname)
            continue

        eligible[pname] = pdata

    print(f"\n  Eligible:      {len(eligible)} providers")
    for reason, names in excluded_reasons.items():
        print(f"  Excluded ({reason}): {len(names)}")

    # ── Compute fair-share targets ────────────────────────────────────
    fair_share_wk = {}
    fair_share_we = {}

    for pname, pdata in eligible.items():
        ann_wk = pdata["annual_weeks"]
        ann_we = pdata["annual_weekends"]
        rem_wk = pdata["weeks_remaining"]
        rem_we = pdata["weekends_remaining"]

        fs_wk = min(math.ceil(ann_wk / 3), math.floor(rem_wk)) if ann_wk > 0 else 0
        fs_we = min(math.ceil(ann_we / 3), math.floor(rem_we)) if ann_we > 0 else 0

        fair_share_wk[pname] = fs_wk
        fair_share_we[pname] = fs_we

    # ── Build per-provider eligible sites ─────────────────────────────
    provider_eligible_sites = {}
    for pname, pdata in eligible.items():
        provider_eligible_sites[pname] = get_eligible_sites(pname, pdata, tags_data)

    # ── Build site demand lists sorted by gap tolerance ───────────────
    site_list_weekday = []
    site_list_weekend = []
    for (site, dtype), needed in sites_demand.items():
        if dtype == "weekday":
            site_list_weekday.append((site, needed))
        elif dtype == "weekend":
            site_list_weekend.append((site, needed))

    def site_sort_key(item):
        return (_gap_tolerance(item[0]), 0 if item[0] != "Cooper" else 1, item[0])

    site_list_weekday.sort(key=site_sort_key)
    site_list_weekend.sort(key=site_sort_key)

    # ── Resolve conflict pairs ────────────────────────────────────────
    conflict_pairs = []
    for name_a, name_b in CONFLICT_PAIR_NAMES:
        found_a = None
        found_b = None
        for pname in eligible:
            norm = pname.upper()
            if name_a in norm:
                found_a = pname
            if name_b in norm:
                found_b = pname
        if found_a and found_b:
            conflict_pairs.append((found_a, found_b))

    # ── Build per-site provider pools (for constraint propagation) ────
    site_provider_pool = defaultdict(list)
    for pname in eligible:
        for site in provider_eligible_sites.get(pname, []):
            site_provider_pool[site].append(pname)

    # Log site pools
    print(f"\n  Site provider pools:")
    for site in sorted(site_provider_pool.keys()):
        pool = site_provider_pool[site]
        print(f"    {site}: {len(pool)} providers")

    # ── Initialize assignment state ───────────────────────────────────
    state = {
        # Raw data
        "providers": providers,
        "tags_data": tags_data,
        "sites_demand": sites_demand,
        "unavailable_dates": unavailable_dates,
        "name_map": name_map,

        # Pre-scheduler data
        "difficulty_records": difficulty_records,
        "holiday_records": holiday_records,

        # Computed
        "periods": periods,
        "eligible": eligible,
        "excluded_reasons": dict(excluded_reasons),
        "fair_share_wk": fair_share_wk,
        "fair_share_we": fair_share_we,
        "provider_eligible_sites": provider_eligible_sites,
        "site_list_weekday": site_list_weekday,
        "site_list_weekend": site_list_weekend,
        "conflict_pairs": conflict_pairs,
        "site_provider_pool": dict(site_provider_pool),
        "memorial_week_num": memorial_week_num,

        # Assignment tracking (mutable)
        "prov_assignments": defaultdict(list),       # pname -> [(period_idx, site), ...]
        "prov_week_count": defaultdict(int),          # pname -> int
        "prov_we_count": defaultdict(int),            # pname -> int
        "prov_site_counts": defaultdict(lambda: defaultdict(int)),  # pname -> {site -> int}
        "prov_week_site": {},                         # (pname, week_num) -> site
        "period_assignments": defaultdict(list),      # period_idx -> [(pname, site), ...]

        # Metadata
        "seed": seed,
        "block_start": block_start,
        "block_end": block_end,
        "n_weeks": n_weeks,
    }

    print(f"\n[Phase 0] Setup complete.")
    return state


def _find_memorial_day(start_year, end_year):
    """Find Memorial Day (last Monday of May) in the block's year range."""
    for year in range(end_year, start_year - 1, -1):
        # Last Monday of May
        may_31 = date(year, 5, 31)
        offset = (may_31.weekday() - 0) % 7  # 0 = Monday
        memorial = may_31 - timedelta(days=offset)
        return memorial
    return None


# ═════════════════════════════════════════════════════════════════════════════
# HARD CONSTRAINT CHECKS
# ═════════════════════════════════════════════════════════════════════════════

def _get_assigned_dates(state, pname):
    """Get set of all date strings where provider is assigned."""
    dates = set()
    for pa_idx, _ in state["prov_assignments"].get(pname, []):
        for d_str in state["periods"][pa_idx]["dates"]:
            dates.add(d_str)
    return dates


def _max_consecutive_with_dates(existing_dates, new_dates):
    """Compute max consecutive days if new_dates were added to existing_dates.

    Returns the max consecutive streak length.
    """
    all_dates = existing_dates | new_dates
    if not all_dates:
        return 0

    sorted_dates = sorted(all_dates)
    max_streak = 1
    current_streak = 1

    for i in range(1, len(sorted_dates)):
        d1 = datetime.strptime(sorted_dates[i - 1], "%Y-%m-%d").date()
        d2 = datetime.strptime(sorted_dates[i], "%Y-%m-%d").date()
        if (d2 - d1).days == 1:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 1

    return max_streak


def _would_exceed_consecutive(state, pname, period_idx):
    """Check if assigning this period would create a >12 consecutive day run."""
    existing = _get_assigned_dates(state, pname)
    new_dates = set(state["periods"][period_idx]["dates"])
    max_streak = _max_consecutive_with_dates(existing, new_dates)
    return max_streak > MAX_CONSECUTIVE_DAYS


def _current_consecutive_days(state, pname):
    """Get the current max consecutive day run for a provider."""
    dates = _get_assigned_dates(state, pname)
    if not dates:
        return 0
    return _max_consecutive_with_dates(dates, set())


def _check_conflict_pairs(state, pname, period_idx):
    """Returns True if assigning pname here violates a conflict pair."""
    week_num = state["periods"][period_idx]["num"]
    for p1, p2 in state["conflict_pairs"]:
        partner = None
        if pname == p1:
            partner = p2
        elif pname == p2:
            partner = p1
        if partner is None:
            continue
        for pa_idx, _ in state["prov_assignments"].get(partner, []):
            if state["periods"][pa_idx]["num"] == week_num:
                return True
    return False


def _is_provider_available(state, pname, dates):
    """Check if provider is available for all dates."""
    return is_available(pname, state["name_map"].get(pname),
                        dates, state["unavailable_dates"])


def _can_assign(state, pname, period_idx, site, use_cap=True):
    """Full hard constraint check for assigning pname to site in period.

    Returns (True, "") or (False, reason_string).
    """
    period = state["periods"][period_idx]
    ptype = period["type"]
    week_num = period["num"]
    dates = period["dates"]
    pdata = state["eligible"].get(pname)

    if pdata is None:
        return False, "not_eligible"

    # Already assigned this period?
    if any(n == pname for n, _ in state["period_assignments"][period_idx]):
        return False, "already_assigned_period"

    # Capacity check
    if ptype == "week":
        cap = math.floor(pdata["weeks_remaining"])
        if cap <= 0 or state["prov_week_count"][pname] >= cap:
            return False, "capacity_exhausted"
        if use_cap and state["prov_week_count"][pname] >= state["fair_share_wk"][pname]:
            return False, "fair_share_cap"
    else:
        cap = math.floor(pdata["weekends_remaining"])
        if cap <= 0 or state["prov_we_count"][pname] >= cap:
            return False, "capacity_exhausted"
        if use_cap and state["prov_we_count"][pname] >= state["fair_share_we"][pname]:
            return False, "fair_share_cap"

    # Site eligibility
    if site not in state["provider_eligible_sites"].get(pname, []):
        return False, "site_ineligible"

    # Availability (SACRED)
    if not _is_provider_available(state, pname, dates):
        return False, "unavailable"

    # Consecutive day check (>12 days)
    if _would_exceed_consecutive(state, pname, period_idx):
        return False, "consecutive_violation"

    # Conflict pair check
    if _check_conflict_pairs(state, pname, period_idx):
        return False, "conflict_pair"

    return True, ""


# ═════════════════════════════════════════════════════════════════════════════
# ASSIGNMENT OPERATIONS
# ═════════════════════════════════════════════════════════════════════════════

def _place_provider(state, pname, period_idx, site):
    """Place a provider into a period at a site. Updates all tracking state."""
    period = state["periods"][period_idx]
    ptype = period["type"]
    week_num = period["num"]

    state["prov_assignments"][pname].append((period_idx, site))
    state["period_assignments"][period_idx].append((pname, site))
    state["prov_site_counts"][pname][site] += 1

    if ptype == "week":
        state["prov_week_count"][pname] += 1
        state["prov_week_site"][(pname, week_num)] = site
    else:
        state["prov_we_count"][pname] += 1


def _remove_provider(state, pname, period_idx):
    """Remove a provider from a period. Returns the site they were at."""
    period = state["periods"][period_idx]
    ptype = period["type"]
    week_num = period["num"]

    # Remove from prov_assignments
    pa_list = state["prov_assignments"][pname]
    site = None
    for i, (pidx, s) in enumerate(pa_list):
        if pidx == period_idx:
            site = s
            pa_list.pop(i)
            break

    # Remove from period_assignments
    pa_period = state["period_assignments"][period_idx]
    for i, (n, s) in enumerate(pa_period):
        if n == pname:
            pa_period.pop(i)
            break

    if site:
        state["prov_site_counts"][pname][site] -= 1

    if ptype == "week":
        state["prov_week_count"][pname] -= 1
        state["prov_week_site"].pop((pname, week_num), None)
    else:
        state["prov_we_count"][pname] -= 1

    return site


# ═════════════════════════════════════════════════════════════════════════════
# SCORING
# ═════════════════════════════════════════════════════════════════════════════

def _score_candidate(state, pname, period_idx, site, period_type):
    """Score a candidate for assignment. Higher = better.

    Scoring factors:
      - Stretch pairing (weekend at same site as weekday)
      - Behind-pace at this site (needs more shifts here)
      - Spacing from last assignment
      - Site percentage alignment
      - Anti-compression (avoid consecutive when slack exists)
    """
    pdata = state["eligible"][pname]
    period = state["periods"][period_idx]
    week_num = period["num"]

    score = 0.0

    # ── Stretch pairing bonus (weekends should match weekday site) ────
    if period_type == "weekend":
        prev_site = state["prov_week_site"].get((pname, week_num))
        if prev_site == site:
            score += 100  # Strong bonus: keep at same site for stretch
        elif prev_site is not None and prev_site != site:
            score -= 50   # Penalty: different site breaks stretch

    # ── Site allocation: how far behind at this site ──────────────────
    pct_field = SITE_PCT_MAP.get(site, "")
    site_pct = pdata.get(pct_field, 0) if pct_field else 0
    rem_key = "weeks_remaining" if period_type == "week" else "weekends_remaining"
    target_at_site = math.floor(pdata[rem_key]) * site_pct
    done_at_site = state["prov_site_counts"][pname].get(site, 0)
    behind_at_site = target_at_site - done_at_site
    score += behind_at_site * 5

    # ── Spacing from last assignment ──────────────────────────────────
    assigned_periods = state["prov_assignments"].get(pname, [])
    if assigned_periods:
        last_idx = max(pidx for pidx, _ in assigned_periods)
        gap = period_idx - last_idx
    else:
        gap = period_idx + 10
    score += gap * 3

    # ── Anti-compression: avoid extending consecutive runs when there's slack ──
    existing_dates = _get_assigned_dates(state, pname)
    new_dates = set(state["periods"][period_idx]["dates"])
    would_be_streak = _max_consecutive_with_dates(existing_dates, new_dates)
    current_streak = _max_consecutive_with_dates(existing_dates, set()) if existing_dates else 0

    if would_be_streak > current_streak:
        # This assignment extends a consecutive run
        if period_type == "week":
            total_periods = sum(1 for p in state["periods"] if p["type"] == period_type)
            target = math.floor(pdata["weeks_remaining"])
            slack = total_periods - target
            if slack >= 3 and would_be_streak > 7:
                score -= 150  # Plenty of room, avoid long stretches
            elif slack >= 1 and would_be_streak > 7:
                score -= 50
        elif period_type == "weekend":
            prev_site = state["prov_week_site"].get((pname, week_num))
            if prev_site is None and would_be_streak > 7:
                total_we = sum(1 for p in state["periods"] if p["type"] == "weekend")
                target = math.floor(pdata["weekends_remaining"])
                slack = total_we - target
                if slack >= 3:
                    score -= 120

    # ── Site percentage alignment bonus ───────────────────────────────
    score += site_pct * 2

    # ── Random jitter for variety across seeds ────────────────────────
    score += random.uniform(-2, 2)

    return score


# ═════════════════════════════════════════════════════════════════════════════
# CONSTRAINT PROPAGATION
# ═════════════════════════════════════════════════════════════════════════════

def _count_available_providers(state, site, period_idx, use_cap=True, exclude=None):
    """Count how many eligible providers could fill this slot.

    Used for look-ahead: if assigning someone here reduces a future
    slot's candidate count to zero, we should reconsider.
    """
    exclude = exclude or set()
    count = 0
    period = state["periods"][period_idx]
    dates = period["dates"]

    for pname in state["site_provider_pool"].get(site, []):
        if pname in exclude:
            continue
        ok, _ = _can_assign(state, pname, period_idx, site, use_cap=use_cap)
        if ok:
            count += 1
    return count


def _would_starve_critical_slot(state, pname, period_idx, use_cap=True):
    """Look-ahead: would assigning pname here make a zero-gap slot impossible?

    For each zero-gap site in the same week and adjacent weeks, check if
    removing pname from the candidate pool reduces any slot to zero candidates.

    Returns (True, description) or (False, "").
    """
    period = state["periods"][period_idx]
    week_num = period["num"]

    # Check same week and ±1 week for critical sites
    check_weeks = {week_num, week_num - 1, week_num + 1}

    for check_idx, p in enumerate(state["periods"]):
        if p["num"] not in check_weeks:
            continue
        if check_idx == period_idx:
            continue

        p_dates = p["dates"]
        p_type = "weekday" if p["type"] == "week" else "weekend"

        for site in ZERO_GAP_SITES:
            demand = state["sites_demand"].get((site, p_type), 0)
            if demand <= 0:
                continue

            # How many are already filled?
            filled = sum(1 for _, s in state["period_assignments"][check_idx] if s == site)
            if filled >= demand:
                continue  # Already covered

            # Is pname even a candidate for this slot?
            if site not in state["provider_eligible_sites"].get(pname, []):
                continue

            # Would this slot have zero candidates without pname?
            remaining_need = demand - filled
            available_count = _count_available_providers(
                state, site, check_idx, use_cap=use_cap, exclude={pname}
            )

            if available_count < remaining_need:
                return True, f"{site} week {p['num']} would have {available_count}/{remaining_need} candidates"

    return False, ""


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 1: RESERVE CRITICAL SITES
# ═════════════════════════════════════════════════════════════════════════════

def phase1_reserve_critical(state):
    """Reserve providers for zero-gap sites before general assignment.

    For each zero-gap site, for each period, ensure at least one eligible
    provider is available. If a site has very few candidates for a period,
    those candidates get priority reservation.

    This prevents the v1/v2 failure mode where a capable provider gets
    consumed by a larger site early, leaving a small site with no candidates.
    """
    periods = state["periods"]
    print(f"\n[Phase 1] Reserving critical sites...")

    reservations = 0
    zero_gap_sites = [s for s in ZERO_GAP_SITES if
                      any(state["sites_demand"].get((s, dt), 0) > 0
                          for dt in ("weekday", "weekend"))]

    # Sort periods by difficulty: fewest available providers first
    period_site_difficulty = []
    for idx, period in enumerate(periods):
        dtype = "weekday" if period["type"] == "week" else "weekend"
        for site in zero_gap_sites:
            demand = state["sites_demand"].get((site, dtype), 0)
            if demand <= 0:
                continue
            available = _count_available_providers(state, site, idx, use_cap=True)
            period_site_difficulty.append((idx, site, demand, available))

    # Fill hardest slots first (fewest available providers)
    period_site_difficulty.sort(key=lambda x: x[3])

    for idx, site, demand, _ in period_site_difficulty:
        period = state["periods"][idx]
        dtype = period["type"]

        filled = sum(1 for _, s in state["period_assignments"][idx] if s == site)
        remaining_need = demand - filled
        if remaining_need <= 0:
            continue

        # Build candidate list for this slot
        candidates = []
        for pname in state["site_provider_pool"].get(site, []):
            ok, _ = _can_assign(state, pname, idx, site, use_cap=True)
            if not ok:
                continue

            score = _score_candidate(state, pname, idx, site, dtype)

            # Bonus: providers who can ONLY work at this site (or very few sites)
            n_sites = len(state["provider_eligible_sites"].get(pname, []))
            if n_sites <= 2:
                score += 30  # Prefer dedicated providers for critical sites

            candidates.append((pname, score))

        candidates.sort(key=lambda x: -x[1])

        # Assign up to remaining_need
        for pname, _ in candidates[:remaining_need]:
            # Double-check with look-ahead
            starves, _ = _would_starve_critical_slot(state, pname, idx, use_cap=True)
            if starves:
                continue

            _place_provider(state, pname, idx, site)
            reservations += 1

    gaps_after = _count_site_gaps(state, zero_gap_only=True)
    total_assigned = sum(state["prov_week_count"].values()) + sum(state["prov_we_count"].values())
    print(f"  Reserved: {reservations} assignments for zero-gap sites")
    print(f"  Zero-gap gaps remaining: {gaps_after}")
    print(f"  Total assigned so far: {total_assigned}")


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 2: GENERAL ASSIGNMENT
# ═════════════════════════════════════════════════════════════════════════════

def phase2_general_assignment(state):
    """Fill remaining slots using constrained scoring with look-ahead.

    Processes all sites (including gap-tolerant and Cooper), filling
    zero-gap sites first within each round. Uses constraint propagation
    to avoid assignments that would make future zero-gap slots impossible.
    """
    periods = state["periods"]
    print(f"\n[Phase 2] General assignment (fair-share capped, with look-ahead)...")

    # Build full demand list sorted by gap tolerance
    all_weekday = sorted(state["site_list_weekday"],
                         key=lambda x: (_gap_tolerance(x[0]), x[0]))
    all_weekend = sorted(state["site_list_weekend"],
                         key=lambda x: (_gap_tolerance(x[0]), x[0]))

    max_demand = 0
    if all_weekday:
        max_demand = max(max_demand, max(n for _, n in all_weekday))
    if all_weekend:
        max_demand = max(max_demand, max(n for _, n in all_weekend))

    # Compute week difficulty for ordering
    difficulty = _compute_week_difficulty(state)

    for fill_round in range(max_demand):
        week_nums = sorted(set(p["num"] for p in periods))
        random.shuffle(week_nums)
        week_nums.sort(key=lambda w: difficulty.get(w, 0), reverse=True)

        for wk_num in week_nums:
            for idx, period in enumerate(periods):
                if period["num"] != wk_num:
                    continue

                if period["type"] == "week":
                    demand_list = all_weekday
                else:
                    demand_list = all_weekend

                for site, needed in demand_list:
                    filled = sum(1 for _, s in state["period_assignments"][idx] if s == site)
                    if filled >= needed:
                        continue

                    _fill_one_slot(state, idx, site, period["type"],
                                   use_cap=True, use_lookahead=True)

    _log_phase_stats(state, "Phase 2")


def phase3_behind_pace(state):
    """Lift fair-share cap for behind-pace providers.

    Same logic as Phase 2 but without the fair-share cap, allowing
    providers who are behind on their annual obligations to catch up.
    """
    periods = state["periods"]
    print(f"\n[Phase 3] Behind-pace fill (fair-share cap lifted)...")

    # All sites sorted by gap tolerance
    all_weekday = sorted(state["site_list_weekday"],
                         key=lambda x: (_gap_tolerance(x[0]), x[0]))
    all_weekend = sorted(state["site_list_weekend"],
                         key=lambda x: (_gap_tolerance(x[0]), x[0]))

    max_demand = 0
    if all_weekday:
        max_demand = max(max_demand, max(n for _, n in all_weekday))
    if all_weekend:
        max_demand = max(max_demand, max(n for _, n in all_weekend))

    # Order by gap-weighted difficulty
    for fill_round in range(max_demand):
        week_nums = sorted(set(p["num"] for p in periods))

        def week_gap_score(wk_num):
            total = 0
            for idx, p in enumerate(periods):
                if p["num"] != wk_num:
                    continue
                dtype = "weekday" if p["type"] == "week" else "weekend"
                for (site, dt), demand in state["sites_demand"].items():
                    if dt != dtype:
                        continue
                    filled = sum(1 for _, s in state["period_assignments"][idx] if s == site)
                    shortfall = max(0, demand - filled)
                    weight = 3 if _gap_tolerance(site) == 0 else 1
                    total += shortfall * weight
            return total

        random.shuffle(week_nums)
        week_nums.sort(key=lambda w: -week_gap_score(w))

        for wk_num in week_nums:
            for idx, period in enumerate(periods):
                if period["num"] != wk_num:
                    continue

                if period["type"] == "week":
                    demand_list = all_weekday
                else:
                    demand_list = all_weekend

                for site, needed in demand_list:
                    filled = sum(1 for _, s in state["period_assignments"][idx] if s == site)
                    if filled >= needed:
                        continue

                    _fill_one_slot(state, idx, site, period["type"],
                                   use_cap=False, use_lookahead=True)

    _log_phase_stats(state, "Phase 3")


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 4: SWAP EVALUATION
# ═════════════════════════════════════════════════════════════════════════════

def phase4_swap_evaluation(state):
    """Evaluate swaps to reduce gaps without creating new violations.

    For each unfilled slot at a zero-gap or tier-1 site, check if swapping
    two assigned providers would fill the gap. A swap is only executed if
    it reduces total gaps without creating hard constraint violations.
    """
    periods = state["periods"]
    print(f"\n[Phase 4] Swap evaluation...")

    swaps_executed = 0
    swaps_evaluated = 0
    MAX_SWAP_ROUNDS = 3

    for swap_round in range(MAX_SWAP_ROUNDS):
        round_swaps = 0

        for idx, period in enumerate(periods):
            dtype = "weekday" if period["type"] == "week" else "weekend"

            for (site, dt), demand in state["sites_demand"].items():
                if dt != dtype:
                    continue
                if _gap_tolerance(site) >= 2:
                    continue  # Don't swap-optimize Cooper

                filled = sum(1 for _, s in state["period_assignments"][idx] if s == site)
                shortfall = demand - filled
                if shortfall <= 0:
                    continue

                # Find providers assigned to OTHER sites this period who could work here
                for assigned_name, assigned_site in list(state["period_assignments"][idx]):
                    if assigned_site == site:
                        continue
                    if site not in state["provider_eligible_sites"].get(assigned_name, []):
                        continue

                    # Can we find someone else to cover their current slot?
                    other_demand = state["sites_demand"].get((assigned_site, dtype), 0)
                    other_filled = sum(1 for _, s in state["period_assignments"][idx]
                                       if s == assigned_site)

                    # Only swap if the donor site won't be short
                    if other_filled <= other_demand and _gap_tolerance(assigned_site) < 2:
                        continue  # Would create a new gap at a non-Cooper site

                    swaps_evaluated += 1

                    # Try the swap: move assigned_name from assigned_site to site
                    # First check if we can find a replacement for assigned_site
                    replacement = _find_replacement(state, idx, assigned_site,
                                                     period["type"], exclude={assigned_name})

                    if replacement or other_filled > other_demand or _gap_tolerance(assigned_site) >= 2:
                        # Execute swap
                        _remove_provider(state, assigned_name, idx)
                        _place_provider(state, assigned_name, idx, site)

                        if replacement:
                            _place_provider(state, replacement, idx, assigned_site)

                        swaps_executed += 1
                        round_swaps += 1
                        break  # Move to next gap

        if round_swaps == 0:
            break

    print(f"  Swaps: {swaps_evaluated} evaluated, {swaps_executed} executed")
    _log_phase_stats(state, "Phase 4")


def _find_replacement(state, period_idx, site, period_type, exclude=None):
    """Find an unassigned provider who could fill this slot."""
    exclude = exclude or set()
    period = state["periods"][period_idx]
    dates = period["dates"]

    candidates = []
    for pname in state["site_provider_pool"].get(site, []):
        if pname in exclude:
            continue
        if any(n == pname for n, _ in state["period_assignments"][period_idx]):
            continue

        ok, _ = _can_assign(state, pname, period_idx, site, use_cap=False)
        if not ok:
            continue

        score = _score_candidate(state, pname, period_idx, site, period_type)
        candidates.append((pname, score))

    if not candidates:
        return None

    candidates.sort(key=lambda x: -x[1])
    return candidates[0][0]


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 5: OUTPUT
# ═════════════════════════════════════════════════════════════════════════════

def phase5_output(state):
    """Compile draft schedule and gap report.

    Every assignment in the draft is solid. Every unfilled slot goes to the
    gap report with a list of viable candidates and what constraints they'd bend.
    """
    periods = state["periods"]
    eligible = state["eligible"]
    sites_demand = state["sites_demand"]
    all_sites = sorted(set(s for s, _ in sites_demand.keys()))

    print(f"\n[Phase 5] Compiling output...")

    # ── Draft schedule (per-period assignments) ───────────────────────
    draft_schedule = []
    for idx, period in enumerate(periods):
        assignments = []
        for pname, site in state["period_assignments"][idx]:
            assignments.append({"provider": pname, "site": site})

        draft_schedule.append({
            "period_idx": idx,
            "type": period["type"],
            "week_num": period["num"],
            "dates": period["dates"],
            "label": period["label"],
            "assignments": assignments,
        })

    # ── Gap report ────────────────────────────────────────────────────
    gap_report = []
    for idx, period in enumerate(periods):
        dtype = "weekday" if period["type"] == "week" else "weekend"

        for site in all_sites:
            demand = sites_demand.get((site, dtype), 0)
            if demand <= 0:
                continue

            filled = sum(1 for _, s in state["period_assignments"][idx] if s == site)
            shortfall = demand - filled
            if shortfall <= 0:
                continue

            # Find viable candidates for this gap
            candidates = _build_gap_candidates(state, idx, site, period["type"])

            gap_report.append({
                "period_idx": idx,
                "week_num": period["num"],
                "type": period["type"],
                "dates": period["dates"],
                "site": site,
                "gap_tolerance": _gap_tolerance(site),
                "demand": demand,
                "filled": filled,
                "shortfall": shortfall,
                "candidates": candidates,
            })

    # ── Provider summary ──────────────────────────────────────────────
    provider_summary = {}
    for pname, pdata in eligible.items():
        wk_used = state["prov_week_count"][pname]
        we_used = state["prov_we_count"][pname]

        # Target = what's left in their contract (remaining after B1+B2)
        wk_target = math.floor(pdata["weeks_remaining"])
        we_target = math.floor(pdata["weekends_remaining"])

        # Prior blocks worked (B1 + B2) = annual - remaining
        wk_prior = round(pdata["annual_weeks"] - pdata["weeks_remaining"], 1)
        we_prior = round(pdata["annual_weekends"] - pdata["weekends_remaining"], 1)

        # Fair share (for reference / comparison)
        fs_wk = state["fair_share_wk"][pname]
        fs_we = state["fair_share_we"][pname]

        site_dist = dict(state["prov_site_counts"][pname])
        esites = state["provider_eligible_sites"].get(pname, [])

        # Compute max consecutive stretch
        max_consec = _compute_max_consecutive(state, pname)

        provider_summary[pname] = {
            "shift_type": pdata["shift_type"],
            "fte": pdata["fte"],
            "annual_weeks": pdata["annual_weeks"],
            "annual_weekends": pdata["annual_weekends"],
            "weeks_remaining": pdata["weeks_remaining"],
            "weekends_remaining": pdata["weekends_remaining"],
            "weeks_prior": wk_prior,
            "weekends_prior": we_prior,
            "weeks_target": wk_target,
            "weekends_target": we_target,
            "fair_share_wk": fs_wk,
            "fair_share_we": fs_we,
            "weeks_assigned": wk_used,
            "weekends_assigned": we_used,
            "weeks_gap": max(0, wk_target - wk_used),
            "weekends_gap": max(0, we_target - we_used),
            "site_distribution": site_dist,
            "eligible_sites": esites,
            "max_consecutive_days": max_consec,
            "assignments": [(pidx, s) for pidx, s in state["prov_assignments"].get(pname, [])],
        }

    # ── Site coverage summary ─────────────────────────────────────────
    site_coverage = {}
    for site in all_sites:
        weekday_gaps = 0
        weekend_gaps = 0
        weekday_total = 0
        weekend_total = 0

        for idx, period in enumerate(periods):
            dtype = "weekday" if period["type"] == "week" else "weekend"
            demand = sites_demand.get((site, dtype), 0)
            filled = sum(1 for _, s in state["period_assignments"][idx] if s == site)
            shortfall = max(0, demand - filled)

            if dtype == "weekday":
                weekday_total += demand
                weekday_gaps += shortfall
            else:
                weekend_total += demand
                weekend_gaps += shortfall

        site_coverage[site] = {
            "gap_tolerance": _gap_tolerance(site),
            "weekday_demand": weekday_total,
            "weekday_filled": weekday_total - weekday_gaps,
            "weekday_coverage_pct": round((weekday_total - weekday_gaps) / weekday_total * 100, 1) if weekday_total > 0 else 100,
            "weekend_demand": weekend_total,
            "weekend_filled": weekend_total - weekend_gaps,
            "weekend_coverage_pct": round((weekend_total - weekend_gaps) / weekend_total * 100, 1) if weekend_total > 0 else 100,
        }

    # ── Summary stats ─────────────────────────────────────────────────
    total_gaps = sum(g["shortfall"] for g in gap_report)
    zgv = sum(g["shortfall"] for g in gap_report if g["gap_tolerance"] == 0)
    gaps_with_candidates = sum(1 for g in gap_report if g["candidates"])
    gaps_without = sum(1 for g in gap_report if not g["candidates"])

    stats = {
        "seed": state["seed"],
        "total_eligible": len(eligible),
        "total_weeks_assigned": sum(state["prov_week_count"].values()),
        "total_weekends_assigned": sum(state["prov_we_count"].values()),
        "total_gaps": total_gaps,
        "zero_gap_violations": zgv,
        "gaps_with_candidates": gaps_with_candidates,
        "gaps_without_candidates": gaps_without,
        "providers_at_capacity": sum(1 for ps in provider_summary.values()
                                     if ps["weeks_gap"] == 0 and ps["weekends_gap"] == 0),
        "providers_under_utilized": sum(1 for ps in provider_summary.values()
                                        if ps["weeks_gap"] > 0 or ps["weekends_gap"] > 0),
    }

    # Coverage percentages
    total_weekday_demand = sum(
        sites_demand.get((s, "weekday"), 0) * state["n_weeks"]
        for s in all_sites
    )
    total_weekend_demand = sum(
        sites_demand.get((s, "weekend"), 0) * sum(1 for p in periods if p["type"] == "weekend")
        for s in all_sites
    )
    total_weekday_filled = stats["total_weeks_assigned"]
    total_weekend_filled = stats["total_weekends_assigned"]

    stats["weekday_coverage_pct"] = round(total_weekday_filled / total_weekday_demand * 100, 1) if total_weekday_demand > 0 else 100
    stats["weekend_coverage_pct"] = round(total_weekend_filled / total_weekend_demand * 100, 1) if total_weekend_demand > 0 else 100

    print(f"\n  {'=' * 60}")
    print(f"  RESULTS SUMMARY")
    print(f"  {'=' * 60}")
    print(f"  Eligible providers:     {stats['total_eligible']}")
    print(f"  Weeks assigned:         {stats['total_weeks_assigned']}")
    print(f"  Weekends assigned:      {stats['total_weekends_assigned']}")
    print(f"  Weekday coverage:       {stats['weekday_coverage_pct']}%")
    print(f"  Weekend coverage:       {stats['weekend_coverage_pct']}%")
    print(f"  Total gaps:             {stats['total_gaps']}")
    print(f"  Zero-gap violations:    {stats['zero_gap_violations']}")
    print(f"  Gaps with candidates:   {stats['gaps_with_candidates']}")
    print(f"  Gaps without candidates:{stats['gaps_without_candidates']}")
    print(f"  Providers at capacity:  {stats['providers_at_capacity']}")
    print(f"  Providers under-used:   {stats['providers_under_utilized']}")

    results = {
        "stats": stats,
        "draft_schedule": draft_schedule,
        "gap_report": gap_report,
        "provider_summary": provider_summary,
        "site_coverage": site_coverage,
        "periods": periods,
        "block_start": state["block_start"].strftime("%Y-%m-%d"),
        "block_end": state["block_end"].strftime("%Y-%m-%d"),
        "excluded_reasons": state["excluded_reasons"],
        "conflict_pairs": state["conflict_pairs"],
    }

    return results


def _build_gap_candidates(state, period_idx, site, period_type):
    """Build candidate list for an unfilled gap slot.

    Each candidate includes what constraint they'd need to bend:
      - extended_stretch: would create 8-12 day stretch
      - over_fair_share: already at fair-share cap
      - distribution_deviation: would push site distribution off target
      - None: clean assignment (only blocked by being assigned elsewhere this period)
    """
    candidates = []
    period = state["periods"][period_idx]
    dates = period["dates"]
    week_num = period["num"]

    for pname in state["site_provider_pool"].get(site, []):
        pdata = state["eligible"].get(pname)
        if pdata is None:
            continue

        # Already assigned this period
        if any(n == pname for n, _ in state["period_assignments"][period_idx]):
            continue

        # Check individual constraints
        constraints_to_bend = []

        # Availability (absolute — skip if unavailable)
        if not _is_provider_available(state, pname, dates):
            continue

        # Site eligibility (absolute)
        if site not in state["provider_eligible_sites"].get(pname, []):
            continue

        # Conflict pairs (absolute)
        if _check_conflict_pairs(state, pname, period_idx):
            continue

        # Hard capacity
        if period_type == "week":
            cap = math.floor(pdata["weeks_remaining"])
            used = state["prov_week_count"][pname]
            if cap <= 0 or used >= cap:
                continue
            if used >= state["fair_share_wk"][pname]:
                constraints_to_bend.append("over_fair_share")
        else:
            cap = math.floor(pdata["weekends_remaining"])
            used = state["prov_we_count"][pname]
            if cap <= 0 or used >= cap:
                continue
            if used >= state["fair_share_we"][pname]:
                constraints_to_bend.append("over_fair_share")

        # Consecutive check
        if _would_exceed_consecutive(state, pname, period_idx):
            # Would create >12 days — absolute constraint
            continue

        # Extended stretch check (>7 days)
        existing = _get_assigned_dates(state, pname)
        new_dates = set(period["dates"])
        would_be_streak = _max_consecutive_with_dates(existing, new_dates)
        if would_be_streak > 7:
            constraints_to_bend.append(f"extended_stretch_{would_be_streak}_days")

        candidates.append({
            "provider": pname,
            "constraints_to_bend": constraints_to_bend if constraints_to_bend else None,
            "remaining_weeks": pdata["weeks_remaining"],
            "remaining_weekends": pdata["weekends_remaining"],
            "weeks_assigned": state["prov_week_count"][pname],
            "weekends_assigned": state["prov_we_count"][pname],
        })

    # Sort: clean candidates first, then by fewest constraints to bend
    candidates.sort(key=lambda c: (
        len(c["constraints_to_bend"]) if c["constraints_to_bend"] else 0,
    ))

    return candidates


def _compute_max_consecutive(state, pname):
    """Compute the maximum consecutive days this provider is scheduled."""
    assignments = state["prov_assignments"].get(pname, [])
    if not assignments:
        return 0

    all_dates = set()
    for period_idx, _ in assignments:
        for d_str in state["periods"][period_idx]["dates"]:
            all_dates.add(d_str)

    if not all_dates:
        return 0

    sorted_dates = sorted(all_dates)
    max_streak = 1
    current_streak = 1

    for i in range(1, len(sorted_dates)):
        d1 = datetime.strptime(sorted_dates[i - 1], "%Y-%m-%d").date()
        d2 = datetime.strptime(sorted_dates[i], "%Y-%m-%d").date()
        if (d2 - d1).days == 1:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 1

    return max_streak


# ═════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═════════════════════════════════════════════════════════════════════════════

def _fill_one_slot(state, period_idx, site, period_type, use_cap=True,
                   use_lookahead=True):
    """Try to fill one slot at a site in a period."""
    candidates = []

    for pname in state["site_provider_pool"].get(site, []):
        ok, reason = _can_assign(state, pname, period_idx, site, use_cap=use_cap)
        if not ok:
            continue

        # Look-ahead: skip if this would starve a critical slot
        if use_lookahead and _gap_tolerance(site) > 0:
            starves, _ = _would_starve_critical_slot(state, pname, period_idx,
                                                      use_cap=use_cap)
            if starves:
                continue

        score = _score_candidate(state, pname, period_idx, site, period_type)
        candidates.append((pname, score))

    if not candidates:
        return False

    candidates.sort(key=lambda x: -x[1])
    pname = candidates[0][0]
    _place_provider(state, pname, period_idx, site)
    return True


def _count_site_gaps(state, zero_gap_only=False):
    """Count total unfilled slots."""
    gaps = 0
    for idx, period in enumerate(state["periods"]):
        dtype = "weekday" if period["type"] == "week" else "weekend"
        for (site, dt), demand in state["sites_demand"].items():
            if dt != dtype:
                continue
            if zero_gap_only and _gap_tolerance(site) != 0:
                continue
            filled = sum(1 for _, s in state["period_assignments"][idx] if s == site)
            gaps += max(0, demand - filled)
    return gaps


def _compute_week_difficulty(state):
    """Pre-compute staffing difficulty score for each week number."""
    periods = state["periods"]
    eligible = state["eligible"]

    week_dates = {}
    for period in periods:
        wn = period["num"]
        if wn not in week_dates and period["type"] == "week":
            week_dates[wn] = period["dates"]

    difficulty = {}
    for wk_num, dates in week_dates.items():
        avail_count = 0
        for pname in eligible:
            if _is_provider_available(state, pname, dates):
                avail_count += 1
        difficulty[wk_num] = -avail_count
    return difficulty


def _log_phase_stats(state, phase_name):
    """Log stats after a phase."""
    total_gaps = _count_site_gaps(state)
    zgv = _count_site_gaps(state, zero_gap_only=True)
    wk = sum(state["prov_week_count"].values())
    we = sum(state["prov_we_count"].values())
    print(f"  After {phase_name}: {wk} weeks, {we} weekends assigned")
    print(f"  Gaps: {total_gaps} total, {zgv} zero-gap violations")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def run_engine(excel_path, pre_schedule_path, availability_dir,
               block_start, block_end, seed=42):
    """Run the full V3 scheduling engine.

    Returns:
        dict — draft schedule + gap report + summaries
    """
    state = phase0_load(excel_path, pre_schedule_path, availability_dir,
                        block_start, block_end, seed=seed)
    phase1_reserve_critical(state)
    phase2_general_assignment(state)
    phase3_behind_pace(state)
    phase4_swap_evaluation(state)
    results = phase5_output(state)

    print(f"\n{'=' * 70}")
    print(f"Engine v3 complete (seed={seed})")
    print(f"{'=' * 70}\n")

    return results
