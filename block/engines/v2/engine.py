#!/usr/bin/env python3
"""
Block Schedule Engine v2 — Gap tolerance enforcement.

Successor to v1. Same 6-phase structure, rebuilt with site gap tolerance
tiers (Section 3.6) baked into the fill logic from the ground up.

Key changes from v1:
  - Sites classified by gap tolerance: zero-gap (Tier 0), gap-tolerant (Tier 1), Cooper (Tier 2)
  - Zero-gap sites filled FIRST within each round-robin pass
  - _find_best_site() gives zero-gap sites a scoring bonus
  - Zero-gap violation count tracked in output stats

Everything else preserved from v1:
  - Two-pass fair-share strategy (Pass 1 capped, Pass 2 uncapped for behind-pace)
  - Round-robin filling with difficulty-scored week ordering
  - Cooper preservation penalty in Phase 2
  - Composite scoring: spacing + stretch + behind + gap + site_pct + jitter
  - Consecutive hard cap at 2 (relaxed to 3 in forced fill only)
  - Availability is SACRED — never overridden
"""

import json
import math
import os
import random
from collections import defaultdict
from datetime import datetime

# ── Import shared loader ─────────────────────────────────────────────────────
import sys
_ENGINE_DIR = os.path.dirname(__file__)
_ENGINES_DIR = os.path.dirname(_ENGINE_DIR)
_BLOCK_DIR = os.path.dirname(_ENGINES_DIR)
_PROJECT_ROOT = os.path.dirname(_BLOCK_DIR)

if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from block.engines.shared.loader import (
    load_providers, load_tags, load_sites, load_availability,
    build_name_map, build_periods, get_eligible_sites, has_tag,
    is_available, SITE_PCT_MAP, OUTPUT_DIR,
)


# ═════════════════════════════════════════════════════════════════════════════
# SITE GAP TOLERANCE (Section 3.6)
# ═════════════════════════════════════════════════════════════════════════════
#
# Tier 0 (zero gaps): must be fully filled every week/weekend.
#   Cape, Mannington, Elmer, Virtua Marlton, Virtua Mt Holly,
#   Virtua Voorhees, Virtua Willingboro
#
# Tier 1 (up to 1 gap per day): small shortfalls acceptable.
#   Mullica Hill, Vineland
#
# Tier 2 (gaps expected): Cooper — moonlighters fill remaining.
#   Cooper

SITE_GAP_TOLERANCE = {
    "Mullica Hill": 1,
    "Vineland": 1,
    "Cooper": 2,
    # Everything else defaults to 0 (zero gaps required)
}


def _gap_tolerance(site):
    """Return gap tolerance tier for a site. 0 = zero gaps, 1 = tolerant, 2 = Cooper."""
    return SITE_GAP_TOLERANCE.get(site, 0)


def _sort_demand_by_tolerance(demand_list):
    """Sort site demand list: zero-gap sites first, then gap-tolerant.

    Within each tier, preserve existing order (alphabetical from setup).
    """
    return sorted(demand_list, key=lambda item: _gap_tolerance(item[0]))


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 1: SETUP
# ═════════════════════════════════════════════════════════════════════════════

def phase1_setup(block_start, block_end, seed=42):
    """Load all data, filter eligible providers, compute fair-share targets.

    Returns:
        dict (engine_state) containing all data structures needed by later phases
    """
    random.seed(seed)

    print(f"{'=' * 70}")
    print(f"BLOCK SCHEDULE ENGINE v2 — seed={seed}")
    print(f"Block: {block_start.strftime('%Y-%m-%d')} to {block_end.strftime('%Y-%m-%d')}")
    print(f"{'=' * 70}")

    # ── Load raw data ────────────────────────────────────────────────────
    print("\n[Phase 1] Loading data...")

    providers = load_providers()
    tags_data = load_tags()
    sites_demand = load_sites()
    unavailable_dates = load_availability()

    print(f"  Providers:     {len(providers)}")
    print(f"  Tags:          {sum(len(v) for v in tags_data.values())} tags across {len(tags_data)} providers")
    print(f"  Sites demand:  {len(sites_demand)} entries")
    print(f"  Availability:  {len(unavailable_dates)} providers with JSON files")

    # ── Match provider names to availability JSONs ───────────────────────
    name_map, unmatched = build_name_map(providers, unavailable_dates)
    matched_count = len(name_map) - len(unmatched)
    print(f"  Name matching: {matched_count}/{len(providers)} matched "
          f"({len(unmatched)} unmatched — treated as fully available)")

    # ── Build period list ────────────────────────────────────────────────
    periods = build_periods(block_start, block_end)
    n_weeks = sum(1 for p in periods if p["type"] == "week")
    n_weekends = sum(1 for p in periods if p["type"] == "weekend")
    print(f"  Periods:       {n_weeks} weeks, {n_weekends} weekends")

    # ── Filter to eligible providers ─────────────────────────────────────
    eligible = {}
    excluded_reasons = defaultdict(list)
    split_night = []

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

        if pdata["shift_type"] == "Nights":
            split_night.append(pname)

        eligible[pname] = pdata

    print(f"\n  Eligible:      {len(eligible)} providers")
    for reason, names in excluded_reasons.items():
        print(f"  Excluded ({reason}): {len(names)}")
    if split_night:
        print(f"  Split night/day: {len(split_night)}")
        for sp in split_night:
            pd = eligible[sp]
            print(f"    {sp}: {pd['weeks_remaining']} wks, {pd['weekends_remaining']} wkends remaining")

    # ── Fair-share targets ───────────────────────────────────────────────
    fair_share_wk = {}
    fair_share_we = {}
    behind_pace = []

    for pname, pdata in eligible.items():
        ann_wk = pdata["annual_weeks"]
        ann_we = pdata["annual_weekends"]
        rem_wk = math.floor(pdata["weeks_remaining"])
        rem_we = math.floor(pdata["weekends_remaining"])

        fs_wk = min(math.ceil(ann_wk / 3), rem_wk) if ann_wk > 0 else 0
        fs_we = min(math.ceil(ann_we / 3), rem_we) if ann_we > 0 else 0

        fair_share_wk[pname] = fs_wk
        fair_share_we[pname] = fs_we

        if rem_wk > fs_wk or rem_we > fs_we:
            behind_pace.append(pname)

    if behind_pace:
        total_excess = sum(
            max(0, math.floor(eligible[p]["weeks_remaining"]) - fair_share_wk[p])
            + max(0, math.floor(eligible[p]["weekends_remaining"]) - fair_share_we[p])
            for p in behind_pace
        )
        print(f"\n  Fair-share: {len(behind_pace)} behind-pace providers, "
              f"{total_excess} excess provider-periods deferred to Pass 2")

    # ── Build site demand lists ──────────────────────────────────────────
    # v2: sorted by gap tolerance tier (zero-gap first), then alphabetical
    site_list_weekday = []
    site_list_weekend = []
    for (site, dtype), needed in sites_demand.items():
        if dtype == "weekday":
            site_list_weekday.append((site, needed))
        elif dtype == "weekend":
            site_list_weekend.append((site, needed))

    def site_sort_key(item):
        """Sort by: gap tolerance tier, then non-Cooper before Cooper, then alphabetical."""
        return (_gap_tolerance(item[0]), 0 if item[0] != "Cooper" else 1, item[0])

    site_list_weekday.sort(key=site_sort_key)
    site_list_weekend.sort(key=site_sort_key)

    # Log site tier breakdown
    tier0 = [s for s, _ in site_list_weekday if _gap_tolerance(s) == 0]
    tier1 = [s for s, _ in site_list_weekday if _gap_tolerance(s) == 1]
    tier2 = [s for s, _ in site_list_weekday if _gap_tolerance(s) == 2]
    print(f"\n  Site tiers: zero-gap={tier0}, gap-tolerant={tier1}, gaps-expected={tier2}")

    # ── Named special rules ──────────────────────────────────────────────
    conflict_pairs = []
    haroldson = None
    mcmillian = None
    for pname in eligible:
        norm = pname.upper()
        if "HAROLDSON" in norm:
            haroldson = pname
        if "MCMILLIAN" in norm:
            mcmillian = pname
    if haroldson and mcmillian:
        conflict_pairs.append((haroldson, mcmillian))

    # ── Initialize assignment state ──────────────────────────────────────
    state = {
        # Raw data
        "providers": providers,
        "tags_data": tags_data,
        "sites_demand": sites_demand,
        "unavailable_dates": unavailable_dates,
        "name_map": name_map,
        "unmatched": unmatched,

        # Computed
        "periods": periods,
        "eligible": eligible,
        "excluded_reasons": dict(excluded_reasons),
        "split_night": split_night,
        "fair_share_wk": fair_share_wk,
        "fair_share_we": fair_share_we,
        "behind_pace": behind_pace,
        "site_list_weekday": site_list_weekday,
        "site_list_weekend": site_list_weekend,
        "conflict_pairs": conflict_pairs,

        # Assignment tracking (mutable — modified by phases 2-5)
        "prov_assignments": defaultdict(list),
        "prov_week_count": defaultdict(int),
        "prov_we_count": defaultdict(int),
        "prov_site_counts": defaultdict(lambda: defaultdict(int)),
        "prov_last_period": {},
        "period_assignments": defaultdict(list),
        "prov_week_site": {},
        "prov_last_week_num": {},
        "forced_stretch_overrides": [],

        # Metadata
        "seed": seed,
        "block_start": block_start,
        "block_end": block_end,
    }

    print(f"\n[Phase 1] Setup complete.")
    return state


# ═════════════════════════════════════════════════════════════════════════════
# SCORING & CANDIDATE SELECTION
# ═════════════════════════════════════════════════════════════════════════════

def _get_active_week_nums(state, pname):
    """Get set of week_nums where provider has any assignment."""
    periods = state["periods"]
    wns = set()
    for pa_idx, _ in state["prov_assignments"].get(pname, []):
        if pa_idx < len(periods):
            wns.add(periods[pa_idx]["num"])
    return wns


def _count_consec_back(state, pname, week_num):
    """Count consecutive week_nums active looking backward from week_num."""
    active = _get_active_week_nums(state, pname)
    consec = 0
    check = week_num - 1
    while check >= 1 and check in active:
        consec += 1
        check -= 1
    return consec


def _count_consec_forward(state, pname, week_num):
    """Count consecutive week_nums active looking forward from week_num."""
    active = _get_active_week_nums(state, pname)
    n_weeks = sum(1 for p in state["periods"] if p["type"] == "week")
    consec = 0
    check = week_num + 1
    while check <= n_weeks and check in active:
        consec += 1
        check += 1
    return consec


def _provider_gap(state, pname, period_idx):
    """Gap in periods since last assignment. Bigger = better spacing."""
    last = state["prov_last_period"].get(pname)
    if last is None:
        return period_idx + 10
    return period_idx - last


def _ideal_spacing_score(state, pname, period_idx, period_type):
    """Score how well assigning this period maintains even spacing."""
    periods = state["periods"]
    pdata = state["eligible"][pname]

    if period_type == "week":
        target = math.floor(pdata["weeks_remaining"])
        used = state["prov_week_count"][pname]
    else:
        target = math.floor(pdata["weekends_remaining"])
        used = state["prov_we_count"][pname]

    remaining_to_assign = max(1, target - used)

    remaining_periods = sum(1 for i, p in enumerate(periods)
                           if i >= period_idx and p["type"] == period_type)
    if remaining_periods <= 0:
        return 0

    ideal_interval = remaining_periods / remaining_to_assign

    last_wk = state["prov_last_week_num"].get(pname)
    if last_wk is None:
        total_periods = sum(1 for p in periods if p["type"] == period_type)
        ideal_first = total_periods / (remaining_to_assign + 1)
        type_idx = sum(1 for i, p in enumerate(periods)
                       if i < period_idx and p["type"] == period_type)
        return 10 - abs(type_idx - ideal_first) * 3
    else:
        week_num = periods[period_idx]["num"]
        gap = week_num - last_wk
        return (gap - ideal_interval) * 5


def _check_conflict_pairs(state, pname, period_idx):
    """Check if assigning pname would violate a conflict pair.

    Returns True if there's a conflict (should NOT assign).
    """
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


def build_candidates(state, site, period_type, period_idx, week_num,
                     dates, assigned_this_period, use_fair_share_cap=True,
                     preserve_cooper=False):
    """Build scored candidate list for filling one site in one period.

    Returns:
        list of (pname, score, gap, stretch_bonus, behind, site_pct) tuples,
        sorted by score descending
    """
    eligible = state["eligible"]
    tags_data = state["tags_data"]
    name_map = state["name_map"]
    unavailable_dates = state["unavailable_dates"]
    periods = state["periods"]

    pct_field = SITE_PCT_MAP.get(site, "")
    candidates = []

    for pname, pdata in eligible.items():
        # ── Already assigned this period? ────────────────────────────────
        if pname in assigned_this_period:
            continue

        # ── Capacity check ───────────────────────────────────────────────
        if period_type == "week":
            cap = math.floor(pdata["weeks_remaining"])
            if cap <= 0 or state["prov_week_count"][pname] >= cap:
                continue
            if use_fair_share_cap and state["prov_week_count"][pname] >= state["fair_share_wk"][pname]:
                continue
        else:
            cap = math.floor(pdata["weekends_remaining"])
            if cap <= 0 or state["prov_we_count"][pname] >= cap:
                continue
            if use_fair_share_cap and state["prov_we_count"][pname] >= state["fair_share_we"][pname]:
                continue

        # ── Site eligibility ─────────────────────────────────────────────
        eligible_sites = get_eligible_sites(pname, pdata, tags_data)
        if site not in eligible_sites:
            continue

        # ── Availability check (SACRED) ──────────────────────────────────
        if not is_available(pname, name_map.get(pname), dates, unavailable_dates):
            continue

        # ── Consecutive week check ───────────────────────────────────────
        consec_back = _count_consec_back(state, pname, week_num)
        if consec_back >= 2:
            continue

        # ── Conflict pair check ──────────────────────────────────────────
        if _check_conflict_pairs(state, pname, period_idx):
            continue

        # ── Scoring ──────────────────────────────────────────────────────

        # Site allocation: how far behind target at this site
        site_pct = pdata.get(pct_field, 0) if pct_field else 0
        rem_key = "weeks_remaining" if period_type == "week" else "weekends_remaining"
        target_at_site = pdata[rem_key] * site_pct
        done_at_site = state["prov_site_counts"][pname].get(site, 0)
        behind = target_at_site - done_at_site

        gap = _provider_gap(state, pname, period_idx)
        spacing = _ideal_spacing_score(state, pname, period_idx, period_type)

        # ── Stretch logic ────────────────────────────────────────────────
        stretch_bonus = 0
        if period_type == "weekend":
            prev_site = state["prov_week_site"].get((pname, week_num))
            if prev_site == site:
                stretch_bonus = 100
            elif prev_site is not None and prev_site != site:
                stretch_bonus = -50

            if consec_back == 1 and prev_site is None:
                slack = sum(1 for p in periods if p["type"] == "weekend") - math.floor(pdata["weekends_remaining"])
                if slack >= 3:
                    stretch_bonus = -120

        elif period_type == "week":
            if consec_back == 1:
                slack = sum(1 for p in periods if p["type"] == "week") - math.floor(pdata["weeks_remaining"])
                if slack >= 3:
                    stretch_bonus = -150
                elif slack >= 1:
                    stretch_bonus = -50

        # ── Cooper preservation penalty ──────────────────────────────────
        cooper_penalty = 0
        if preserve_cooper and site != "Cooper":
            pct_cooper = pdata.get("pct_cooper", 0)
            if pct_cooper > 0:
                cooper_penalty = -200 * pct_cooper

        # ── Composite score ──────────────────────────────────────────────
        jitter = random.uniform(-2, 2)
        score = (stretch_bonus
                 + (behind * 5)
                 + (spacing * 6)
                 + (gap * 3)
                 + (site_pct * 2)
                 + cooper_penalty
                 + jitter)

        candidates.append((pname, score, gap, stretch_bonus, behind, site_pct))

    candidates.sort(key=lambda x: -x[1])
    return candidates


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
    state["prov_last_period"][pname] = period_idx
    state["prov_site_counts"][pname][site] += 1
    state["prov_last_week_num"][pname] = week_num

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

    pa_list = state["prov_assignments"][pname]
    site = None
    for i, (pidx, s) in enumerate(pa_list):
        if pidx == period_idx:
            site = s
            pa_list.pop(i)
            break

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

    if state["prov_assignments"][pname]:
        last_pa = max(state["prov_assignments"][pname], key=lambda x: x[0])
        state["prov_last_period"][pname] = last_pa[0]
        state["prov_last_week_num"][pname] = state["periods"][last_pa[0]]["num"]
    else:
        state["prov_last_period"].pop(pname, None)
        state["prov_last_week_num"].pop(pname, None)

    return site


def _assign_period(state, period_idx, site_demand_list, period_type,
                   use_fair_share_cap=True, preserve_cooper=False):
    """Assign providers to all sites for one period.

    site_demand_list is processed in order — zero-gap sites should appear first.
    """
    period = state["periods"][period_idx]
    week_num = period["num"]
    dates = period["dates"]
    assigned_this_period = set(n for n, _ in state["period_assignments"][period_idx])

    for site, needed in site_demand_list:
        already_filled = sum(1 for _, s in state["period_assignments"][period_idx] if s == site)
        remaining_need = needed - already_filled
        if remaining_need <= 0:
            continue

        candidates = build_candidates(
            state, site, period_type, period_idx, week_num,
            dates, assigned_this_period, use_fair_share_cap=use_fair_share_cap,
            preserve_cooper=preserve_cooper,
        )

        to_assign = min(remaining_need, len(candidates))
        for i in range(to_assign):
            pname = candidates[i][0]
            assigned_this_period.add(pname)
            _place_provider(state, pname, period_idx, site)


def _count_site_gaps(state):
    """Count total unfilled slots across all sites and periods."""
    gaps = 0
    all_sites = set(s for s, _ in state["sites_demand"].keys())
    for idx, period in enumerate(state["periods"]):
        dtype = "weekday" if period["type"] == "week" else "weekend"
        for site in all_sites:
            demand = state["sites_demand"].get((site, dtype), 0)
            filled = sum(1 for _, s in state["period_assignments"][idx] if s == site)
            gaps += max(0, demand - filled)
    return gaps


def _count_zero_gap_violations(state):
    """Count unfilled slots at sites that must have zero gaps (Tier 0)."""
    violations = 0
    for idx, period in enumerate(state["periods"]):
        dtype = "weekday" if period["type"] == "week" else "weekend"
        for (site, dt), demand in state["sites_demand"].items():
            if dt != dtype or _gap_tolerance(site) != 0:
                continue
            filled = sum(1 for _, s in state["period_assignments"][idx] if s == site)
            violations += max(0, demand - filled)
    return violations


# ═════════════════════════════════════════════════════════════════════════════
# WEEK DIFFICULTY SCORING
# ═════════════════════════════════════════════════════════════════════════════

def _compute_week_difficulty(state):
    """Pre-compute staffing difficulty score for each week number.

    Weeks with fewer available providers are harder to staff and should be
    filled first in each round-robin pass.

    Returns:
        dict: week_num -> difficulty_score (higher = harder to staff)
    """
    periods = state["periods"]
    eligible = state["eligible"]
    name_map = state["name_map"]
    unavailable = state["unavailable_dates"]

    week_dates = {}
    for period in periods:
        wn = period["num"]
        if wn not in week_dates and period["type"] == "week":
            week_dates[wn] = period["dates"]

    difficulty = {}
    for wk_num, dates in week_dates.items():
        avail_count = 0
        avail_capacity = 0.0

        for pname, pdata in eligible.items():
            json_key = name_map.get(pname)
            if is_available(pname, json_key, dates, unavailable):
                avail_count += 1
                avail_capacity += (pdata["weeks_remaining"] + pdata["weekends_remaining"])

        # Negate: fewer available = higher difficulty
        difficulty[wk_num] = -avail_count * 100 - avail_capacity

    return difficulty


def _order_weeks_by_difficulty(week_nums, difficulty_scores):
    """Order week numbers: hardest to staff first, with random tiebreaking."""
    random.shuffle(week_nums)
    week_nums.sort(key=lambda w: difficulty_scores.get(w, 0), reverse=True)
    return week_nums


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 2: FIRST PASS — FILL NON-COOPER (fair-share capped)
# ═════════════════════════════════════════════════════════════════════════════

def phase2_fill_non_cooper(state):
    """Fill non-Cooper sites with fair-share capped assignments.

    v2 key change: within each round-robin pass, zero-gap sites (Cape,
    Mannington, Elmer, Virtua) are filled BEFORE gap-tolerant sites
    (Mullica Hill, Vineland). This ensures scarce provider capacity flows
    to sites that must be fully staffed.

    Uses round-robin filling with difficulty-scored week ordering.
    """
    periods = state["periods"]
    print(f"\n[Phase 2] First Pass — Fill non-Cooper sites (fair-share capped)...")

    difficulty = _compute_week_difficulty(state)

    # Log difficulty ranking
    ranked = sorted(difficulty.items(), key=lambda x: x[1], reverse=True)
    hardest_3 = [(wn, -int(d // 100)) for wn, d in ranked[:3]]
    easiest_3 = [(wn, -int(d // 100)) for wn, d in ranked[-3:]]
    print(f"  Week difficulty: hardest={hardest_3} easiest={easiest_3}")

    # Non-Cooper demand lists, sorted by gap tolerance (zero-gap first)
    non_cooper_weekday = _sort_demand_by_tolerance(
        [(s, n) for s, n in state["site_list_weekday"] if s != "Cooper"]
    )
    non_cooper_weekend = _sort_demand_by_tolerance(
        [(s, n) for s, n in state["site_list_weekend"] if s != "Cooper"]
    )

    max_demand = max(n for _, n in non_cooper_weekday) if non_cooper_weekday else 0
    max_demand = max(max_demand, max(n for _, n in non_cooper_weekend) if non_cooper_weekend else 0)

    for fill_round in range(max_demand):
        week_nums = sorted(set(p["num"] for p in periods))
        _order_weeks_by_difficulty(week_nums, difficulty)

        for wk_num in week_nums:
            for idx, period in enumerate(periods):
                if period["num"] != wk_num:
                    continue

                if period["type"] == "week":
                    # Build demand-of-1 list, zero-gap sites first
                    round_demand = []
                    for site, needed in non_cooper_weekday:
                        filled = sum(1 for _, s in state["period_assignments"][idx] if s == site)
                        if filled < needed:
                            round_demand.append((site, 1))
                    if round_demand:
                        _assign_period(state, idx, round_demand, "week",
                                       use_fair_share_cap=True, preserve_cooper=True)
                else:
                    round_demand = []
                    for site, needed in non_cooper_weekend:
                        filled = sum(1 for _, s in state["period_assignments"][idx] if s == site)
                        if filled < needed:
                            round_demand.append((site, 1))
                    if round_demand:
                        _assign_period(state, idx, round_demand, "weekend",
                                       use_fair_share_cap=True, preserve_cooper=True)

    gaps = _count_site_gaps(state)
    zgv = _count_zero_gap_violations(state)
    assigned_wk = sum(state["prov_week_count"].values())
    assigned_we = sum(state["prov_we_count"].values())
    print(f"  Non-Cooper fill: {assigned_wk} weeks, {assigned_we} weekends assigned")
    print(f"  Site gaps: {gaps} total, {zgv} zero-gap violations")


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 3: FILL COOPER (fair-share capped)
# ═════════════════════════════════════════════════════════════════════════════

def phase3_fill_cooper(state):
    """Fill Cooper with remaining provider capacity (fair-share capped).

    Cooper absorbs whatever capacity is left after non-Cooper sites.
    Gaps at Cooper are expected — moonlighters fill them.
    Round-robin with difficulty-scored week ordering.
    """
    periods = state["periods"]
    print(f"\n[Phase 3] Fill Cooper (fair-share capped)...")

    difficulty = _compute_week_difficulty(state)

    cooper_weekday = [(s, n) for s, n in state["site_list_weekday"] if s == "Cooper"]
    cooper_weekend = [(s, n) for s, n in state["site_list_weekend"] if s == "Cooper"]

    cooper_wk_demand = cooper_weekday[0][1] if cooper_weekday else 0
    cooper_we_demand = cooper_weekend[0][1] if cooper_weekend else 0
    max_demand = max(cooper_wk_demand, cooper_we_demand)

    for fill_round in range(max_demand):
        week_nums = sorted(set(p["num"] for p in periods))
        _order_weeks_by_difficulty(week_nums, difficulty)

        for wk_num in week_nums:
            for idx, period in enumerate(periods):
                if period["num"] != wk_num:
                    continue

                if period["type"] == "week":
                    filled = sum(1 for _, s in state["period_assignments"][idx] if s == "Cooper")
                    if filled < cooper_wk_demand:
                        _assign_period(state, idx, [("Cooper", 1)], "week",
                                       use_fair_share_cap=True)
                else:
                    filled = sum(1 for _, s in state["period_assignments"][idx] if s == "Cooper")
                    if filled < cooper_we_demand:
                        _assign_period(state, idx, [("Cooper", 1)], "weekend",
                                       use_fair_share_cap=True)

    gaps = _count_site_gaps(state)
    zgv = _count_zero_gap_violations(state)
    assigned_wk = sum(state["prov_week_count"].values())
    assigned_we = sum(state["prov_we_count"].values())
    print(f"  After Cooper fill: {assigned_wk} weeks, {assigned_we} weekends total")
    print(f"  Site gaps: {gaps} total, {zgv} zero-gap violations")


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 4: SECOND PASS — BEHIND-PACE CATCH-UP
# ═════════════════════════════════════════════════════════════════════════════

def phase4_behind_pace(state):
    """Lift fair-share cap — behind-pace providers fill remaining gaps.

    v2 key change: within each round, zero-gap sites are filled before
    gap-tolerant sites and Cooper. Combined with gap-priority week ordering.
    """
    periods = state["periods"]
    sites_demand = state["sites_demand"]
    all_sites = set(s for s, _ in sites_demand.keys())
    print(f"\n[Phase 4] Second Pass — lift fair-share cap (behind-pace catch-up)...")

    # All sites, sorted by gap tolerance tier
    all_weekday = _sort_demand_by_tolerance(state["site_list_weekday"])
    all_weekend = _sort_demand_by_tolerance(state["site_list_weekend"])

    max_demand = max(n for _, n in all_weekday + all_weekend) if (all_weekday + all_weekend) else 0

    for fill_round in range(max_demand):
        week_nums = sorted(set(p["num"] for p in periods))

        def week_gap_score(wk_num):
            """Total shortfall across all sites, weighted by gap tolerance.

            Zero-gap site shortfalls count 3x to prioritize those weeks.
            """
            total_gap = 0
            for idx, p in enumerate(periods):
                if p["num"] != wk_num:
                    continue
                dtype = "weekday" if p["type"] == "week" else "weekend"
                for site in all_sites:
                    demand = sites_demand.get((site, dtype), 0)
                    filled = sum(1 for _, s in state["period_assignments"][idx] if s == site)
                    shortfall = max(0, demand - filled)
                    # Weight zero-gap shortfalls higher
                    weight = 3 if _gap_tolerance(site) == 0 else 1
                    total_gap += shortfall * weight
            return total_gap

        random.shuffle(week_nums)
        week_nums.sort(key=lambda w: -week_gap_score(w))

        for wk_num in week_nums:
            for idx, period in enumerate(periods):
                if period["num"] != wk_num:
                    continue

                if period["type"] == "week":
                    round_demand = []
                    for site, needed in all_weekday:
                        filled = sum(1 for _, s in state["period_assignments"][idx] if s == site)
                        if filled < needed:
                            round_demand.append((site, 1))
                    if round_demand:
                        _assign_period(state, idx, round_demand, "week",
                                       use_fair_share_cap=False)
                else:
                    round_demand = []
                    for site, needed in all_weekend:
                        filled = sum(1 for _, s in state["period_assignments"][idx] if s == site)
                        if filled < needed:
                            round_demand.append((site, 1))
                    if round_demand:
                        _assign_period(state, idx, round_demand, "weekend",
                                       use_fair_share_cap=False)

    gaps = _count_site_gaps(state)
    zgv = _count_zero_gap_violations(state)
    assigned_wk = sum(state["prov_week_count"].values())
    assigned_we = sum(state["prov_we_count"].values())
    print(f"  After behind-pace fill: {assigned_wk} weeks, {assigned_we} weekends total")
    print(f"  Site gaps: {gaps} total, {zgv} zero-gap violations")


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 5: FORCED FILL / REBALANCING
# ═════════════════════════════════════════════════════════════════════════════

def _provider_utilization_gap(state, pname):
    """How many total assignments short of contractual obligation."""
    pdata = state["eligible"][pname]
    wk_gap = max(0, math.floor(pdata["weeks_remaining"]) - state["prov_week_count"][pname])
    we_gap = max(0, math.floor(pdata["weekends_remaining"]) - state["prov_we_count"][pname])
    return wk_gap + we_gap


def _can_place(state, pname, period_idx, ignore_consec=False):
    """Check if provider can be placed in a period.

    Availability is NEVER overridden.
    """
    pdata = state["eligible"][pname]
    period = state["periods"][period_idx]
    ptype = period["type"]
    week_num = period["num"]

    if ptype == "week":
        if state["prov_week_count"][pname] >= math.floor(pdata["weeks_remaining"]):
            return False
    else:
        if state["prov_we_count"][pname] >= math.floor(pdata["weekends_remaining"]):
            return False

    eligible_sites = get_eligible_sites(pname, pdata, state["tags_data"])
    if not eligible_sites:
        return False

    if not is_available(pname, state["name_map"].get(pname),
                        period["dates"], state["unavailable_dates"]):
        return False

    if _check_conflict_pairs(state, pname, period_idx):
        return False

    if not ignore_consec:
        consec_back = _count_consec_back(state, pname, week_num)
        consec_fwd = _count_consec_forward(state, pname, week_num)
        if consec_back + consec_fwd + 1 > 2:
            return False

    return True


def _find_best_site(state, pname, period_idx, allow_overfill=False):
    """Find the best site for a provider in a given period.

    v2 key change: zero-gap sites with shortfalls get a +50 scoring bonus.
    This steers rebalancing moves toward filling Cape/Elmer/Mannington/Virtua
    before Mullica Hill/Vineland.
    """
    pdata = state["eligible"][pname]
    period = state["periods"][period_idx]
    ptype = period["type"]
    eligible_sites = get_eligible_sites(pname, pdata, state["tags_data"])

    demand_key = "weekday" if ptype == "week" else "weekend"
    best_site = None
    best_score = -999

    for site in eligible_sites:
        demand = state["sites_demand"].get((site, demand_key), 0)
        filled = sum(1 for _, s in state["period_assignments"][period_idx] if s == site)
        shortfall = demand - filled

        if shortfall <= 0 and not allow_overfill:
            continue

        pct_field = SITE_PCT_MAP.get(site, "")
        site_pct = pdata.get(pct_field, 0) if pct_field else 0
        rem_key = "weeks_remaining" if ptype == "week" else "weekends_remaining"
        target = math.floor(pdata[rem_key]) * site_pct
        done = state["prov_site_counts"][pname].get(site, 0)
        behind = target - done

        score = shortfall * 10 + behind

        # v2: zero-gap bonus — prioritize sites that MUST be fully filled
        if shortfall > 0 and _gap_tolerance(site) == 0:
            score += 50

        if shortfall <= 0:
            score -= 1000

        if score > best_score:
            best_score = score
            best_site = site

    return best_site


def phase5_forced_fill(state):
    """Rebalance and forced fill — relax soft constraints progressively.

    5a: Standard rebalancing (respects consecutive cap)
    5b: Forced fill (relaxes consecutive cap to max 3)

    Availability is NEVER relaxed.
    """
    periods = state["periods"]
    print(f"\n[Phase 5] Forced Fill / Rebalancing...")

    MAX_ITERS = 50
    all_sites = set(s for s, _ in state["sites_demand"].keys())

    # ── 5a: Standard rebalancing ─────────────────────────────────────────
    rebalance_moves = 0

    for iteration in range(MAX_ITERS):
        under = [(p, _provider_utilization_gap(state, p))
                 for p in state["eligible"] if _provider_utilization_gap(state, p) > 0]
        under.sort(key=lambda x: -x[1])

        if not under:
            break

        moved = False
        for pname, gap in under:
            pdata = state["eligible"][pname]

            for ptype in ["week", "weekend"]:
                if ptype == "week" and state["prov_week_count"][pname] >= math.floor(pdata["weeks_remaining"]):
                    continue
                if ptype == "weekend" and state["prov_we_count"][pname] >= math.floor(pdata["weekends_remaining"]):
                    continue

                def period_shortfall(idx, _ptype=ptype):
                    p = periods[idx]
                    if p["type"] != _ptype:
                        return -999
                    dk = "weekday" if _ptype == "week" else "weekend"
                    # v2: weight zero-gap shortfalls 3x in period scoring
                    total = 0
                    for s in all_sites:
                        demand = state["sites_demand"].get((s, dk), 0)
                        filled = len([x for x in state["period_assignments"][idx] if x[1] == s])
                        shortfall = max(0, demand - filled)
                        weight = 3 if _gap_tolerance(s) == 0 else 1
                        total += shortfall * weight
                    return total

                sorted_periods = sorted(range(len(periods)), key=lambda i: -period_shortfall(i))

                for idx in sorted_periods:
                    if periods[idx]["type"] != ptype:
                        continue
                    if any(n == pname for n, _ in state["period_assignments"][idx]):
                        continue
                    if _can_place(state, pname, idx):
                        site = _find_best_site(state, pname, idx)
                        if not site:
                            site = _find_best_site(state, pname, idx, allow_overfill=True)
                        if site:
                            _place_provider(state, pname, idx, site)
                            rebalance_moves += 1
                            moved = True
                            break
                else:
                    continue
                break

        if not moved:
            break

    print(f"  Rebalancing: {rebalance_moves} moves")

    # ── 5b: Forced fill (relax consecutive cap) ─────────────────────────
    remaining_gaps = sum(_provider_utilization_gap(state, p) for p in state["eligible"])
    if remaining_gaps == 0:
        print(f"  All providers fully scheduled — no forced fill needed")
        zgv = _count_zero_gap_violations(state)
        final_gaps = _count_site_gaps(state)
        print(f"  Final: {final_gaps} site gaps ({zgv} zero-gap violations), 0 provider-periods unfilled")
        return

    print(f"  {remaining_gaps} provider-periods still short — running forced fill...")
    forced_moves = 0

    for iteration in range(MAX_ITERS):
        under = [(p, _provider_utilization_gap(state, p))
                 for p in state["eligible"] if _provider_utilization_gap(state, p) > 0]
        under.sort(key=lambda x: -x[1])

        if not under:
            break

        moved = False
        for pname, gap in under:
            pdata = state["eligible"][pname]

            for ptype in ["week", "weekend"]:
                if ptype == "week" and state["prov_week_count"][pname] >= math.floor(pdata["weeks_remaining"]):
                    continue
                if ptype == "weekend" and state["prov_we_count"][pname] >= math.floor(pdata["weekends_remaining"]):
                    continue

                def period_shortfall_f(idx, _ptype=ptype):
                    p = periods[idx]
                    if p["type"] != _ptype:
                        return -999
                    dk = "weekday" if _ptype == "week" else "weekend"
                    total = 0
                    for s in all_sites:
                        demand = state["sites_demand"].get((s, dk), 0)
                        filled = len([x for x in state["period_assignments"][idx] if x[1] == s])
                        shortfall = max(0, demand - filled)
                        weight = 3 if _gap_tolerance(s) == 0 else 1
                        total += shortfall * weight
                    return total

                sorted_p = sorted(range(len(periods)), key=lambda i: -period_shortfall_f(i))

                for idx in sorted_p:
                    if periods[idx]["type"] != ptype:
                        continue
                    if any(n == pname for n, _ in state["period_assignments"][idx]):
                        continue

                    if _can_place(state, pname, idx, ignore_consec=True):
                        wk_num = periods[idx]["num"]
                        consec_back = _count_consec_back(state, pname, wk_num)
                        consec_fwd = _count_consec_forward(state, pname, wk_num)
                        run_len = consec_back + consec_fwd + 1
                        if run_len > 3:
                            continue

                        site = _find_best_site(state, pname, idx, allow_overfill=True)
                        if site:
                            _place_provider(state, pname, idx, site)
                            forced_moves += 1
                            moved = True

                            if run_len > 2:
                                state["forced_stretch_overrides"].append(
                                    (pname, idx, run_len)
                                )
                            break
                else:
                    continue
                break

        if not moved:
            break

    print(f"  Forced fill: {forced_moves} moves, {len(state['forced_stretch_overrides'])} stretch overrides")

    final_gaps = _count_site_gaps(state)
    zgv = _count_zero_gap_violations(state)
    final_util = sum(_provider_utilization_gap(state, p) for p in state["eligible"])
    print(f"  Final: {final_gaps} site gaps ({zgv} zero-gap violations), {final_util} provider-periods unfilled")


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 6: OUTPUT — COMPILE RESULTS
# ═════════════════════════════════════════════════════════════════════════════

def phase6_output(state):
    """Compile all results into a structured output dict for reporting."""
    periods = state["periods"]
    eligible = state["eligible"]
    sites_demand = state["sites_demand"]
    all_sites = sorted(set(s for s, _ in sites_demand.keys()))

    print(f"\n[Phase 6] Compiling results...")

    # ── Per-provider summary ─────────────────────────────────────────────
    provider_summary = {}
    for pname, pdata in eligible.items():
        wk_target = math.floor(pdata["weeks_remaining"])
        we_target = math.floor(pdata["weekends_remaining"])
        wk_used = state["prov_week_count"][pname]
        we_used = state["prov_we_count"][pname]

        site_dist = dict(state["prov_site_counts"][pname])
        esites = get_eligible_sites(pname, pdata, state["tags_data"])

        wk_gap = max(0, wk_target - wk_used)
        we_gap = max(0, we_target - we_used)
        reason = ""
        if wk_gap > 0 or we_gap > 0:
            avail_weeks = 0
            avail_weekends = 0
            for idx, period in enumerate(periods):
                if any(n == pname for n, _ in state["period_assignments"][idx]):
                    continue
                if is_available(pname, state["name_map"].get(pname),
                                period["dates"], state["unavailable_dates"]):
                    if period["type"] == "week":
                        avail_weeks += 1
                    else:
                        avail_weekends += 1

            if avail_weeks == 0 and wk_gap > 0:
                reason = "excessive_time_off"
            elif not esites or len(esites) <= 1:
                reason = "limited_sites"
            else:
                reason = "scheduling_constraint"

        provider_summary[pname] = {
            "shift_type": pdata["shift_type"],
            "fte": pdata["fte"],
            "scheduler": pdata["scheduler"],
            "annual_weeks": pdata["annual_weeks"],
            "annual_weekends": pdata["annual_weekends"],
            "weeks_remaining": pdata["weeks_remaining"],
            "weekends_remaining": pdata["weekends_remaining"],
            "weeks_target": wk_target,
            "weekends_target": we_target,
            "weeks_assigned": wk_used,
            "weekends_assigned": we_used,
            "weeks_gap": wk_gap,
            "weekends_gap": we_gap,
            "site_distribution": site_dist,
            "eligible_sites": esites,
            "under_utilization_reason": reason,
            "assignments": state["prov_assignments"].get(pname, []),
            "holiday_1": pdata.get("holiday_1", ""),
            "holiday_2": pdata.get("holiday_2", ""),
        }

    # ── Site fill analysis ───────────────────────────────────────────────
    site_fill = {}
    for site in all_sites:
        site_fill[site] = {"weekday": [], "weekend": []}

    for idx, period in enumerate(periods):
        dtype = "weekday" if period["type"] == "week" else "weekend"
        for site in all_sites:
            demand = sites_demand.get((site, dtype), 0)
            filled = sum(1 for _, s in state["period_assignments"][idx] if s == site)
            shortfall = max(0, demand - filled)
            overfill = max(0, filled - demand)

            site_fill[site][dtype].append({
                "period_idx": idx,
                "week_num": period["num"],
                "demand": demand,
                "filled": filled,
                "shortfall": shortfall,
                "overfill": overfill,
                "providers": [n for n, s in state["period_assignments"][idx] if s == site],
            })

    # ── Summary stats ────────────────────────────────────────────────────
    total_gaps = _count_site_gaps(state)
    zgv = _count_zero_gap_violations(state)
    total_overfills = sum(
        sf["overfill"]
        for site_data in site_fill.values()
        for dtype_data in site_data.values()
        for sf in dtype_data
    )
    total_under_utilized = sum(
        1 for ps in provider_summary.values()
        if ps["weeks_gap"] > 0 or ps["weekends_gap"] > 0
    )

    stats = {
        "seed": state["seed"],
        "total_eligible": len(eligible),
        "total_weeks_assigned": sum(state["prov_week_count"].values()),
        "total_weekends_assigned": sum(state["prov_we_count"].values()),
        "total_site_gaps": total_gaps,
        "zero_gap_violations": zgv,
        "total_overfills": total_overfills,
        "total_under_utilized": total_under_utilized,
        "stretch_overrides": len(state["forced_stretch_overrides"]),
        "behind_pace_count": len(state["behind_pace"]),
    }

    print(f"  Eligible: {stats['total_eligible']}")
    print(f"  Assigned: {stats['total_weeks_assigned']} weeks, {stats['total_weekends_assigned']} weekends")
    print(f"  Site gaps: {stats['total_site_gaps']} ({stats['zero_gap_violations']} zero-gap violations)")
    print(f"  Overfills: {stats['total_overfills']}")
    print(f"  Under-utilized: {stats['total_under_utilized']}")
    print(f"  Stretch overrides: {stats['stretch_overrides']}")

    results = {
        "stats": stats,
        "periods": periods,
        "period_assignments": {str(k): v for k, v in state["period_assignments"].items()},
        "provider_summary": provider_summary,
        "site_fill": site_fill,
        "forced_stretch_overrides": state["forced_stretch_overrides"],
        "excluded_reasons": state["excluded_reasons"],
        "conflict_pairs": state["conflict_pairs"],
        "block_start": state["block_start"].strftime("%Y-%m-%d"),
        "block_end": state["block_end"].strftime("%Y-%m-%d"),
    }

    return results


# ═════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def run_engine(block_start, block_end, seed=42):
    """Run the full 6-phase scheduling engine.

    Returns:
        dict — comprehensive results for report generation
    """
    state = phase1_setup(block_start, block_end, seed=seed)
    phase2_fill_non_cooper(state)
    phase3_fill_cooper(state)
    phase4_behind_pace(state)
    phase5_forced_fill(state)
    results = phase6_output(state)

    print(f"\n{'=' * 70}")
    print(f"Engine complete (seed={seed})")
    print(f"{'=' * 70}\n")

    return results
