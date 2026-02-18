#!/usr/bin/env python3
"""
Block Schedule Engine v2.1
Reads provider data from Google Sheet + individual schedule JSONs.
Produces proposed weekly/weekend assignments per site for Block 3 (Mar 2 - Jun 28, 2026).

Constraints:
- Smaller sites filled first, fully
- Cooper absorbs remainder, aiming for 23-26 (moonlighters fill gaps)
- Providers stay at one site for a week+weekend stretch
- Time-off requests respected
- Even spacing of stretches through the block
- No extras — tight lists matching site needs
- Providers only assigned to sites where their allocation % > 0
"""

import csv
import io
import json
import math
import os
import random
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta

# ─── Configuration ───────────────────────────────────────────────────────────

SHEET_ID = "1dbHUkE-pLtQJK02ig2EbU2eny33N60GQUvk4muiXW5M"
SHEET_BASE = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv"

BLOCK_START = datetime(2026, 3, 2)   # Monday
BLOCK_END   = datetime(2026, 6, 28)  # Sunday

SCHEDULES_DIR = os.path.join(os.path.dirname(__file__), "input", "individualSchedules")
OUTPUT_DIR    = os.path.join(os.path.dirname(__file__), "output")


# Site groups for location percentage mapping
SITE_PCT_MAP = {
    "Cooper":             "pct_cooper",
    "Vineland":           "pct_inspira_veb",
    "Elmer":              "pct_inspira_veb",
    "Mullica Hill":       "pct_inspira_mhw",
    "Mannington":         "pct_mannington",
    "Virtua Voorhees":    "pct_virtua",
    "Virtua Marlton":     "pct_virtua",
    "Virtua Willingboro": "pct_virtua",
    "Virtua Mt Holly":    "pct_virtua",
    "Cape":               "pct_cape",
}


# random.seed is set per run_engine() call for variation support

# ─── Data Loading ────────────────────────────────────────────────────────────

def fetch_sheet_csv(tab_name):
    """Fetch a tab from the Google Sheet as CSV."""
    url = f"{SHEET_BASE}&sheet={urllib.request.quote(tab_name)}"
    with urllib.request.urlopen(url) as resp:
        return resp.read().decode("utf-8")


def load_providers():
    """Load provider data from Google Sheet Providers tab."""
    text = fetch_sheet_csv("Providers")
    reader = csv.DictReader(io.StringIO(text))
    providers = {}
    for row in reader:
        name = row["provider_name"].strip()
        if not name:
            continue
        providers[name] = {
            "shift_type":       row.get("shift_type", "").strip(),
            "fte":              float(row.get("fte", 0) or 0),
            "scheduler":        row.get("scheduler", "").strip(),
            "annual_weeks":     float(row.get("annual_weeks", 0) or 0),
            "annual_weekends":  float(row.get("annual_weekends", 0) or 0),
            "annual_nights":    float(row.get("annual_nights", 0) or 0),
            "weeks_remaining":  parse_float(row.get("weeks_remaining", 0)),
            "weekends_remaining": parse_float(row.get("weekends_remaining", 0)),
            "nights_remaining": parse_float(row.get("nights_remaining", 0)),
            "pct_cooper":       parse_float(row.get("pct_cooper", 0)),
            "pct_inspira_veb":  parse_float(row.get("pct_inspira_veb", 0)),
            "pct_inspira_mhw":  parse_float(row.get("pct_inspira_mhw", 0)),
            "pct_mannington":   parse_float(row.get("pct_mannington", 0)),
            "pct_virtua":       parse_float(row.get("pct_virtua", 0)),
            "pct_cape":         parse_float(row.get("pct_cape", 0)),
            "holiday_1":        row.get("holiday_1", "").strip(),
            "holiday_2":        row.get("holiday_2", "").strip(),
        }
    return providers


def load_tags():
    """Load provider tags from Google Sheet."""
    text = fetch_sheet_csv("Provider Tags")
    reader = csv.DictReader(io.StringIO(text))
    tags = defaultdict(list)
    for row in reader:
        name = row["provider_name"].strip()
        tag = row.get("tag", "").strip()
        rule = row.get("rule", "").strip()
        if name and tag:
            tags[name].append({"tag": tag, "rule": rule})
    return dict(tags)


def load_sites():
    """Load site demand from Google Sheet."""
    text = fetch_sheet_csv("Sites")
    reader = csv.DictReader(io.StringIO(text))
    sites = {}
    for row in reader:
        site = row["site"].strip()
        day_type = row["day_type"].strip()
        needed = int(row["providers_needed"])
        sites[(site, day_type)] = needed
    return sites


def parse_float(val):
    """Parse a float value, returning 0 for empty/invalid."""
    if val is None or val == "":
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def load_availability():
    """Load individual schedule JSONs to build per-provider availability."""
    availability = {}  # name -> set of unavailable dates (YYYY-MM-DD)
    if not os.path.isdir(SCHEDULES_DIR):
        print(f"WARNING: Schedules directory not found: {SCHEDULES_DIR}")
        return availability

    for fname in os.listdir(SCHEDULES_DIR):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(SCHEDULES_DIR, fname)
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


# ─── Name Matching ───────────────────────────────────────────────────────────

def normalize_name(name):
    """Normalize name for matching: uppercase, strip suffixes, collapse spaces."""
    n = name.upper().strip()
    for suffix in [" MD", " DO", " PA", " NP", " PA-C", " MBBS"]:
        if n.endswith(suffix):
            n = n[:-len(suffix)].strip()
    n = n.replace(".", "").strip()
    n = re.sub(r"\s+", " ", n)
    return n


def build_name_index(json_names):
    """Build a lookup from normalized JSON names to original JSON names."""
    index = {}
    for name in json_names:
        norm = normalize_name(name)
        index[norm] = name
        parts = norm.split(",", 1)
        if len(parts) == 2:
            last = parts[0].strip()
            first = parts[1].strip().split()[0] if parts[1].strip() else ""
            if first:
                index[f"{last}, {first}"] = name
    return index


def match_provider_to_json(sheet_name, name_index):
    """Match a provider name from the sheet to a JSON schedule name."""
    norm = normalize_name(sheet_name)
    if norm in name_index:
        return name_index[norm]
    parts = norm.split(",", 1)
    if len(parts) == 2:
        last = parts[0].strip()
        first = parts[1].strip().split()[0] if parts[1].strip() else ""
        short = f"{last}, {first}"
        if short in name_index:
            return name_index[short]
    return None


# ─── Period Construction ─────────────────────────────────────────────────────

def build_periods():
    """Build the list of weeks and weekends in the block."""
    periods = []
    current = BLOCK_START
    week_num = 0

    while current <= BLOCK_END:
        dow = current.weekday()
        if dow == 0:
            week_num += 1
            week_dates = []
            for i in range(5):
                d = current + timedelta(days=i)
                if d <= BLOCK_END:
                    week_dates.append(d.strftime("%Y-%m-%d"))
            if week_dates:
                periods.append({
                    "type": "week", "num": week_num, "dates": week_dates,
                    "label": f"Week {week_num}: {week_dates[0]} to {week_dates[-1]}",
                })
            sat = current + timedelta(days=5)
            sun = current + timedelta(days=6)
            we_dates = []
            if sat <= BLOCK_END:
                we_dates.append(sat.strftime("%Y-%m-%d"))
            if sun <= BLOCK_END:
                we_dates.append(sun.strftime("%Y-%m-%d"))
            if we_dates:
                periods.append({
                    "type": "weekend", "num": week_num, "dates": we_dates,
                    "label": f"  WE {week_num}: {we_dates[0]} to {we_dates[-1]}",
                })
            current = current + timedelta(days=7)
        else:
            current = current + timedelta(days=(7 - dow))

    return periods


# ─── Engine ──────────────────────────────────────────────────────────────────

def get_eligible_sites(provider_name, provider_data, tags_data):
    """Return list of sites a provider can work at, based on percentages > 0."""
    sites = []

    pct_fields = {
        "pct_cooper": ["Cooper"],
        "pct_inspira_veb": ["Vineland", "Elmer"],
        "pct_inspira_mhw": ["Mullica Hill"],
        "pct_mannington": ["Mannington"],
        "pct_virtua": ["Virtua Voorhees", "Virtua Marlton", "Virtua Willingboro", "Virtua Mt Holly"],
        "pct_cape": ["Cape"],
    }

    for pct_field, site_list in pct_fields.items():
        if provider_data.get(pct_field, 0) > 0:
            sites.extend(site_list)

    # Apply tag-based restrictions
    ptags = tags_data.get(provider_name, [])
    for t in ptags:
        tag = t["tag"]
        if tag == "no_elmer":
            sites = [s for s in sites if s != "Elmer"]
        if tag == "no_vineland":
            sites = [s for s in sites if s != "Vineland"]

    return sites


def has_do_not_schedule(provider_name, tags_data):
    """Check if provider has do_not_schedule tag."""
    ptags = tags_data.get(provider_name, [])
    return any(t["tag"] == "do_not_schedule" for t in ptags)


def is_available(provider_name, json_name, dates, unavailable_dates):
    """Check if provider is available for all dates in a period."""
    if json_name is None:
        return True
    unavail = unavailable_dates.get(json_name, set())
    return not any(d in unavail for d in dates)


def run_engine(seed=42):
    """Main engine: produce proposed block schedule.
    Different seeds produce different valid schedule variations."""
    random.seed(seed)
    print("Loading data from Google Sheet...")
    providers = load_providers()
    tags_data = load_tags()
    sites_demand = load_sites()
    print(f"Loaded {len(providers)} providers, tags for {len(tags_data)} providers")

    print("Loading individual schedules...")
    unavailable_dates = load_availability()
    json_names = list(unavailable_dates.keys())
    name_index = build_name_index(json_names)

    name_map = {}
    unmatched = []
    for pname in providers:
        jname = match_provider_to_json(pname, name_index)
        if jname:
            name_map[pname] = jname
        else:
            unmatched.append(pname)
            name_map[pname] = None

    print(f"Matched {len(name_map) - len(unmatched)}/{len(providers)} to schedule files "
          f"({len(unmatched)} unmatched — treated as fully available)")

    periods = build_periods()
    n_weeks = sum(1 for p in periods if p["type"] == "week")
    n_weekends = sum(1 for p in periods if p["type"] == "weekend")
    print(f"Block 3: {n_weeks} weeks, {n_weekends} weekends\n")

    # Filter to providers with remaining week/weekend capacity > 0
    # Pure nocturnists (Nights with no weeks/weekends remaining) are skipped.
    # Split providers (Nights shift_type but with weeks/weekends remaining)
    # are included so their day-shift obligations get scheduled.
    eligible_providers = {}
    split_night_providers = []
    for pname, pdata in providers.items():
        if has_do_not_schedule(pname, tags_data):
            continue
        # Must have positive remaining in at least one day-shift category
        if pdata["weeks_remaining"] <= 0 and pdata["weekends_remaining"] <= 0:
            continue
        if pdata["shift_type"] == "Nights":
            # Split provider — has night shift type but also owes weeks/weekends
            split_night_providers.append(pname)
        eligible_providers[pname] = pdata

    print(f"{len(eligible_providers)} eligible providers "
          f"({len(split_night_providers)} split night/day)")
    if split_night_providers:
        for sp in split_night_providers:
            pd = eligible_providers[sp]
            print(f"  Split: {sp} — {pd['weeks_remaining']} wks, "
                  f"{pd['weekends_remaining']} wkends remaining")

    # Cooper's demand stays at its true value (26 weekday / 19 weekend).
    # The engine fills Cooper naturally — it won't hit 26/19 every week because
    # there aren't enough provider-weeks, but that's expected. Moonlighters
    # fill the remaining spots. The engine should NOT break other rules
    # (consecutive, availability, site caps) just to push Cooper closer to 26.
    cooper_wk_demand = sites_demand.get(("Cooper", "weekday"), 0)
    cooper_we_demand = sites_demand.get(("Cooper", "weekend"), 0)
    print(f"Cooper demand: wk {cooper_wk_demand}, we {cooper_we_demand} "
          f"(shortfall expected — moonlighters fill gap)")

    # Build site demand lists
    site_list_weekday = []
    site_list_weekend = []
    for (site, dtype), needed in sites_demand.items():
        if dtype == "weekday":
            site_list_weekday.append((site, needed))
        elif dtype == "weekend":
            site_list_weekend.append((site, needed))

    # Sort: non-Cooper first, Cooper last (fill smaller sites first)
    def site_sort_key(item):
        return (0 if item[0] != "Cooper" else 1, item[0])

    site_list_weekday.sort(key=site_sort_key)
    site_list_weekend.sort(key=site_sort_key)

    # ─── Fair-Share Targets ──────────────────────────────────────────────
    # Block 3 is the last block (1 of 3 in the fiscal year = ~17 weeks each).
    # A provider on pace should have ~33% of their annual obligation remaining.
    # Providers behind pace (>33% remaining) shouldn't crowd out on-pace ones.
    #
    # Strategy: two-pass scheduling.
    #   Pass 1: Cap each provider at their fair-share target (annual / 3).
    #           On-pace providers get fully scheduled; behind-pace providers
    #           get their fair share but not more.
    #   Pass 2: Lift the cap — behind-pace providers fill remaining gaps
    #           at sites still under demand.
    fair_share_wk = {}   # pname -> Block 3 weekday target (pass 1 cap)
    fair_share_we = {}   # pname -> Block 3 weekend target (pass 1 cap)
    behind_pace = []     # providers with remaining > fair share

    for pname, pdata in eligible_providers.items():
        ann_wk = pdata["annual_weeks"]
        ann_we = pdata["annual_weekends"]
        rem_wk = math.ceil(pdata["weeks_remaining"])
        rem_we = math.ceil(pdata["weekends_remaining"])

        # Fair share = 1/3 of annual (Block 3 is one of three blocks)
        fs_wk = math.ceil(ann_wk / 3) if ann_wk > 0 else 0
        fs_we = math.ceil(ann_we / 3) if ann_we > 0 else 0

        # Cap at actual remaining (can't schedule more than they owe)
        fs_wk = min(fs_wk, rem_wk)
        fs_we = min(fs_we, rem_we)

        fair_share_wk[pname] = fs_wk
        fair_share_we[pname] = fs_we

        if rem_wk > fs_wk or rem_we > fs_we:
            behind_pace.append(pname)

    if behind_pace:
        total_excess = sum(
            max(0, math.ceil(eligible_providers[p]["weeks_remaining"]) - fair_share_wk[p])
            + max(0, math.ceil(eligible_providers[p]["weekends_remaining"]) - fair_share_we[p])
            for p in behind_pace
        )
        print(f"Fair-share scheduling: {len(behind_pace)} behind-pace providers, "
              f"{total_excess} excess provider-periods deferred to pass 2")

    # Track which pass we're in (Pass 1 = fair-share cap, Pass 2 = full capacity)
    use_fair_share_cap = True

    # ─── Assignment State ─────────────────────────────────────────────────
    prov_assignments = defaultdict(list)   # name -> [(period_idx, site)]
    prov_week_count = defaultdict(int)     # name -> weeks assigned
    prov_we_count = defaultdict(int)       # name -> weekends assigned
    prov_site_counts = defaultdict(lambda: defaultdict(int))  # name -> site -> count
    prov_last_period = {}                  # name -> last period index assigned
    period_assignments = defaultdict(list) # period_idx -> [(name, site)]
    prov_week_site = {}                    # (name, week_num) -> site
    prov_last_week_num = {}               # name -> last week_num where they were assigned
    flex_used = defaultdict(list)          # reserved for future per-provider flex overrides

    # ─── Assignment Logic ─────────────────────────────────────────────────

    def provider_gap(pname, period_idx):
        """Gap in periods since last assignment. Bigger = better spacing."""
        last = prov_last_period.get(pname)
        if last is None:
            return period_idx + 10  # never assigned = big gap
        return period_idx - last

    def ideal_spacing_score(pname, period_idx, period_type):
        """Score how well assigning this period maintains even spacing.

        Computes the ideal interval for a provider based on their remaining
        obligations and available periods, then scores based on how close
        the current assignment would be to the ideal next slot.

        Returns higher values for providers who are 'due' for an assignment
        and lower/negative for those who were recently assigned.
        """
        pdata = eligible_providers[pname]
        if period_type == "week":
            target = math.ceil(pdata["weeks_remaining"])
            used = prov_week_count[pname]
        else:
            target = math.ceil(pdata["weekends_remaining"])
            used = prov_we_count[pname]

        remaining_to_assign = max(1, target - used)

        # Count how many periods of this type remain from current position forward
        remaining_periods = sum(1 for i, p in enumerate(periods)
                               if i >= period_idx and p["type"] == period_type)
        if remaining_periods <= 0:
            return 0

        # Ideal interval: spread remaining assignments across remaining periods
        ideal_interval = remaining_periods / remaining_to_assign

        # How many periods of this type since last assignment?
        last_wk = prov_last_week_num.get(pname)
        if last_wk is None:
            # Never assigned — first assignment. Ideal start depends on
            # when they "should" begin to evenly fill the block.
            # With N to assign across M periods, first should start ~at M/(N+1)
            week_num = periods[period_idx]["num"]
            # Count total periods of this type
            total_periods = sum(1 for p in periods if p["type"] == period_type)
            ideal_first = total_periods / (remaining_to_assign + 1)
            # period_num within type (0-indexed)
            type_idx = sum(1 for i, p in enumerate(periods)
                          if i < period_idx and p["type"] == period_type)
            # How close are we to the ideal first assignment?
            return 10 - abs(type_idx - ideal_first) * 3
        else:
            # Gap since last assigned week_num
            week_num = periods[period_idx]["num"]
            gap = week_num - last_wk
            # Score based on how close to ideal interval
            # Positive = provider is due, negative = too soon
            return (gap - ideal_interval) * 5

    def build_candidates(site, pct_field, period_type, period_idx, week_num,
                         dates, assigned_this_period):
        """Build scored candidate list for a site."""
        candidates = []

        for pname, pdata in eligible_providers.items():
            if pname in assigned_this_period:
                continue

            # Check remaining capacity — must be strictly positive
            # Use math.ceil() to round up fractional remaining values
            # During pass 1, cap at fair-share target so behind-pace providers
            # don't crowd out on-pace ones. Pass 2 lifts the cap.
            if period_type == "week":
                remaining = math.ceil(pdata["weeks_remaining"])
                if remaining <= 0 or prov_week_count[pname] >= remaining:
                    continue
                if use_fair_share_cap and prov_week_count[pname] >= fair_share_wk[pname]:
                    continue
            else:
                remaining = math.ceil(pdata["weekends_remaining"])
                if remaining <= 0 or prov_we_count[pname] >= remaining:
                    continue
                if use_fair_share_cap and prov_we_count[pname] >= fair_share_we[pname]:
                    continue

            # Check site eligibility — only sites where provider has > 0%
            eligible_sites = get_eligible_sites(pname, pdata, tags_data)
            if site not in eligible_sites:
                continue

            # Check availability
            if not is_available(pname, name_map.get(pname), dates, unavailable_dates):
                continue

            # ── Consecutive-week check (hard cap) ────────────────────────────
            # Goal: max 7-day stretch (week+weekend). 12-day (we+wk+we) rare.
            # Anything over 12 days is PROHIBITED.
            # This applies to BOTH weekday and weekend assignments.

            consec_weeks = 0
            pa_list = prov_assignments.get(pname, [])
            if pa_list:
                check_wk = week_num - 1
                assigned_week_nums = set()
                for pa_idx, _ in pa_list:
                    if pa_idx < len(periods):
                        assigned_week_nums.add(periods[pa_idx]["num"])
                while check_wk >= 1 and check_wk in assigned_week_nums:
                    consec_weeks += 1
                    check_wk -= 1

            # Hard cap: 2+ consecutive week_nums already active = SKIP
            # (would make 3+ consecutive week_nums = 19+ days)
            if consec_weeks >= 2:
                continue

            # ── Scoring ──────────────────────────────────────────────────────

            site_pct = pdata.get(pct_field, 0) if pct_field else 0
            rem = pdata["weeks_remaining"] if period_type == "week" else pdata["weekends_remaining"]
            target_at_site = rem * site_pct
            done_at_site = prov_site_counts[pname].get(site, 0)
            behind = target_at_site - done_at_site

            gap = provider_gap(pname, period_idx)
            spacing = ideal_spacing_score(pname, period_idx, period_type)

            # ── Stretch logic ───────────────────────────────────────────────
            stretch_bonus = 0
            if period_type == "weekend":
                # Weekend should match its week's site — this is the core pairing
                prev_site = prov_week_site.get((pname, week_num))
                if prev_site == site:
                    stretch_bonus = 100   # strongly prefer same site as this week
                elif prev_site is not None and prev_site != site:
                    stretch_bonus = -50   # penalize cross-site within same week

                # If this would extend a back-to-back (consec_weeks==1 means
                # provider already active last week), penalize standalone weekends
                # that aren't paired with this week's weekday
                if consec_weeks == 1 and prev_site is None:
                    # Weekend-only in a back-to-back — discourage
                    slack = 17 - math.ceil(pdata["weekends_remaining"])
                    if slack >= 3:
                        stretch_bonus = -120

            elif period_type == "week":
                # 1 consecutive week (would make a 12-day run) — penalize but allow
                if consec_weeks == 1:
                    slack = 17 - math.ceil(pdata["weeks_remaining"])
                    if slack >= 3:
                        stretch_bonus = -150  # strong discouragement
                    elif slack >= 1:
                        stretch_bonus = -50   # mild — tight schedule

            # Composite score:
            # - spacing: ideal-spacing score (even distribution across full block)
            # - stretch_bonus: pair weekends with weeks, penalize back-to-back
            # - behind: how far behind site target this provider is
            # - gap: raw gap since last assignment (tiebreaker)
            # Random tiebreaker ensures different seeds produce different schedules
            jitter = random.uniform(-2, 2)
            score = stretch_bonus + (behind * 5) + (spacing * 6) + (gap * 3) + (site_pct * 2) + jitter

            candidates.append((pname, score, gap, stretch_bonus, behind, site_pct))

        # Sort by composite score (highest first)
        candidates.sort(key=lambda x: -x[1])
        return candidates

    def assign_period(period_idx, period, site_demand_list, period_type):
        """Assign providers to sites for one period (week or weekend)."""
        week_num = period["num"]
        dates = period["dates"]
        assigned_this_period = set(n for n, _ in period_assignments[period_idx])

        for site, needed in site_demand_list:
            # Account for providers already assigned to this site in this period
            # (from a previous pass). Only fill remaining shortfall.
            already_filled = sum(1 for _, s in period_assignments[period_idx] if s == site)
            remaining_need = needed - already_filled
            if remaining_need <= 0:
                continue

            pct_field = SITE_PCT_MAP.get(site, "")

            candidates = build_candidates(
                site, pct_field, period_type, period_idx, week_num,
                dates, assigned_this_period
            )

            to_assign = min(remaining_need, len(candidates))

            for i in range(to_assign):
                pname = candidates[i][0]
                assigned_this_period.add(pname)
                prov_assignments[pname].append((period_idx, site))
                period_assignments[period_idx].append((pname, site))
                prov_last_period[pname] = period_idx
                prov_site_counts[pname][site] += 1

                prov_last_week_num[pname] = week_num

                if period_type == "week":
                    prov_week_count[pname] += 1
                    prov_week_site[(pname, week_num)] = site
                else:
                    prov_we_count[pname] += 1

    # ─── Run Initial Assignment ─────────────────────────────────────────
    # Process weeks in RANDOM order to prevent front-loading bias.
    # Within each week, assign weekday first, then weekend (for stretch pairing).
    week_nums = sorted(set(p["num"] for p in periods))
    random.shuffle(week_nums)

    # ─── Pass 1: Fair-share assignment ──────────────────────────────────
    # Each provider capped at their fair share (annual / 3).
    # On-pace providers fill their full Block 3 allocation.
    # Behind-pace providers get their fair share but no more yet.
    for wk_num in week_nums:
        for idx, period in enumerate(periods):
            if period["num"] != wk_num:
                continue
            if period["type"] == "week":
                assign_period(idx, period, site_list_weekday, "week")
            else:
                assign_period(idx, period, site_list_weekend, "weekend")

    pass1_gaps = 0
    for _site in set(s for s, _ in sites_demand.keys()):
        for _idx, _p in enumerate(periods):
            _dtype = "weekday" if _p["type"] == "week" else "weekend"
            _demand = sites_demand.get((_site, _dtype), 0)
            _filled = sum(1 for _, s in period_assignments[_idx] if s == _site)
            pass1_gaps += max(0, _demand - _filled)

    print(f"Pass 1 (fair-share) complete: {pass1_gaps} site gaps remaining")

    # ─── Pass 2: Excess fill ─────────────────────────────────────────────
    # Lift the fair-share cap. Behind-pace providers now fill remaining gaps
    # at sites still under demand. This gives them extra weeks without
    # having taken slots away from on-pace providers in pass 1.
    use_fair_share_cap = False

    # Re-shuffle week order for pass 2
    random.shuffle(week_nums)
    for wk_num in week_nums:
        for idx, period in enumerate(periods):
            if period["num"] != wk_num:
                continue
            if period["type"] == "week":
                assign_period(idx, period, site_list_weekday, "week")
            else:
                assign_period(idx, period, site_list_weekend, "weekend")

    pass2_gaps = 0
    for _site in set(s for s, _ in sites_demand.keys()):
        for _idx, _p in enumerate(periods):
            _dtype = "weekday" if _p["type"] == "week" else "weekend"
            _demand = sites_demand.get((_site, _dtype), 0)
            _filled = sum(1 for _, s in period_assignments[_idx] if s == _site)
            pass2_gaps += max(0, _demand - _filled)

    print(f"Pass 2 (excess fill) complete: {pass2_gaps} site gaps remaining")

    # ─── Rebalancing Phase ───────────────────────────────────────────────
    # Ensure all providers meet their contractual obligations.
    # Iteratively: find under-utilized providers, place them into periods
    # where sites need coverage, swapping out over-served providers if needed.

    forced_overrides = []   # Track availability overrides: (pname, period_idx, reason)

    def get_active_week_nums(pname):
        """Get set of week_nums where provider has any assignment."""
        wns = set()
        for pa_idx, _ in prov_assignments.get(pname, []):
            if pa_idx < len(periods):
                wns.add(periods[pa_idx]["num"])
        return wns

    def count_consec_at(pname, wk_num):
        """Count consecutive week_nums active leading into wk_num."""
        active = get_active_week_nums(pname)
        consec = 0
        check = wk_num - 1
        while check >= 1 and check in active:
            consec += 1
            check -= 1
        return consec

    def can_place(pname, period_idx, ignore_consec=False):
        """Check if provider can be placed in a period (capacity + availability + consecutive).
        ignore_consec=True allows breaking the consecutive cap for contracts.
        Availability is ALWAYS enforced — schedule requests are never overridden."""
        pdata = eligible_providers[pname]
        period = periods[period_idx]
        ptype = period["type"]
        wk_num = period["num"]

        # Capacity check
        if ptype == "week":
            if prov_week_count[pname] >= math.ceil(pdata["weeks_remaining"]):
                return False
        else:
            if prov_we_count[pname] >= math.ceil(pdata["weekends_remaining"]):
                return False

        # Site eligibility — check if provider can work at ANY site that needs
        # coverage in this period
        eligible_sites = get_eligible_sites(pname, pdata, tags_data)
        if not eligible_sites:
            return False

        # Availability check — always enforced, never overridden
        if not is_available(pname, name_map.get(pname), period["dates"], unavailable_dates):
            return False

        # Consecutive check — hard cap at 2 (can be relaxed for contractual fill)
        if not ignore_consec:
            consec = count_consec_at(pname, wk_num)
            active = get_active_week_nums(pname)
            forward_consec = 0
            check = wk_num + 1
            while check <= 17 and check in active:
                forward_consec += 1
                check += 1
            if consec + forward_consec + 1 > 2:
                return False

        return True

    def place_provider(pname, period_idx, site):
        """Place a provider into a period at a site."""
        period = periods[period_idx]
        ptype = period["type"]
        wk_num = period["num"]

        prov_assignments[pname].append((period_idx, site))
        period_assignments[period_idx].append((pname, site))
        prov_last_period[pname] = period_idx
        prov_site_counts[pname][site] += 1
        prov_last_week_num[pname] = wk_num

        if ptype == "week":
            prov_week_count[pname] += 1
            prov_week_site[(pname, wk_num)] = site
        else:
            prov_we_count[pname] += 1

    def remove_provider(pname, period_idx):
        """Remove a provider from a period."""
        period = periods[period_idx]
        ptype = period["type"]
        wk_num = period["num"]

        # Find and remove from prov_assignments
        pa_list = prov_assignments[pname]
        site = None
        for i, (pidx, s) in enumerate(pa_list):
            if pidx == period_idx:
                site = s
                pa_list.pop(i)
                break

        # Remove from period_assignments
        pa_period = period_assignments[period_idx]
        for i, (n, s) in enumerate(pa_period):
            if n == pname:
                pa_period.pop(i)
                break

        if site:
            prov_site_counts[pname][site] -= 1

        if ptype == "week":
            prov_week_count[pname] -= 1
            prov_week_site.pop((pname, wk_num), None)
        else:
            prov_we_count[pname] -= 1

        # Recalculate last_period and last_week_num
        if prov_assignments[pname]:
            last_pa = max(prov_assignments[pname], key=lambda x: x[0])
            prov_last_period[pname] = last_pa[0]
            prov_last_week_num[pname] = periods[last_pa[0]]["num"]
        else:
            prov_last_period.pop(pname, None)
            prov_last_week_num.pop(pname, None)

        return site

    def provider_utilization_gap(pname):
        """How many total assignments short of contractual obligation."""
        pdata = eligible_providers[pname]
        wk_gap = max(0, math.ceil(pdata["weeks_remaining"]) - prov_week_count[pname])
        we_gap = max(0, math.ceil(pdata["weekends_remaining"]) - prov_we_count[pname])
        return wk_gap + we_gap

    def find_best_site_for(pname, period_idx, allow_overfill=False):
        """Find the best site for a provider in a given period.
        By default only returns sites below demand. With allow_overfill=True,
        allows assignment even when site is at demand (for contractual obligations)."""
        pdata = eligible_providers[pname]
        period = periods[period_idx]
        ptype = period["type"]
        eligible_sites = get_eligible_sites(pname, pdata, tags_data)

        demand_key = "weekday" if ptype == "week" else "weekend"
        best_site = None
        best_score = -999

        for site in eligible_sites:
            demand = sites_demand.get((site, demand_key), 0)
            filled = sum(1 for _, s in period_assignments[period_idx] if s == site)
            shortfall = demand - filled

            # Skip sites at/above demand unless overfill allowed
            if shortfall <= 0 and not allow_overfill:
                continue

            pct_field = SITE_PCT_MAP.get(site, "")
            site_pct = pdata.get(pct_field, 0) if pct_field else 0
            target = math.ceil(pdata["weeks_remaining" if ptype == "week" else "weekends_remaining"]) * site_pct
            done = prov_site_counts[pname].get(site, 0)
            behind = target - done

            # Score: prefer sites with shortfall, then behind-target sites
            # Penalize overfill slots so under-demand sites always win
            score = shortfall * 10 + behind
            if shortfall <= 0:
                score -= 1000  # heavy penalty — only use if no under-demand options
            if score > best_score:
                best_score = score
                best_site = site

        return best_site

    # --- Rebalancing iterations ---
    print("Rebalancing: filling contractual obligations...")
    MAX_REBALANCE_ITERS = 50
    rebalance_moves = 0

    for iteration in range(MAX_REBALANCE_ITERS):
        # Find under-utilized providers sorted by biggest gap
        under = []
        for pname in eligible_providers:
            gap = provider_utilization_gap(pname)
            if gap > 0:
                under.append((pname, gap))
        under.sort(key=lambda x: -x[1])

        if not under:
            break

        moved_this_iter = False

        for pname, gap in under:
            pdata = eligible_providers[pname]

            # Try to place in weekday periods first, then weekends
            for ptype in ["week", "weekend"]:
                if ptype == "week" and prov_week_count[pname] >= math.ceil(pdata["weeks_remaining"]):
                    continue
                if ptype == "weekend" and prov_we_count[pname] >= math.ceil(pdata["weekends_remaining"]):
                    continue

                # Sort periods by shortfall (neediest first) so gaps spread evenly
                def period_shortfall(idx):
                    period = periods[idx]
                    if period["type"] != ptype:
                        return -1  # wrong type, will be skipped
                    demand_key = "weekday" if ptype == "week" else "weekend"
                    total_demand = sum(sites_demand.get((s, demand_key), 0) for s in all_sites_set)
                    total_filled = len(period_assignments[idx])
                    return total_demand - total_filled

                all_sites_set = set(s for s, _ in sites_demand.keys())
                sorted_periods = sorted(range(len(periods)),
                                       key=lambda i: -period_shortfall(i))

                for idx in sorted_periods:
                    period = periods[idx]
                    if period["type"] != ptype:
                        continue

                    # Already assigned this period?
                    if any(n == pname for n, _ in period_assignments[idx]):
                        continue

                    # Can place? (respects availability + consecutive cap)
                    if can_place(pname, idx):
                        site = find_best_site_for(pname, idx)
                        if site:
                            place_provider(pname, idx, site)
                            rebalance_moves += 1
                            moved_this_iter = True
                            break

                    # Provider requested this time off — honor the request.
                    # Do NOT override availability. Under-utilization will be
                    # documented in the report instead.
                else:
                    continue
                break  # placed one — restart gap check for this provider

        if not moved_this_iter:
            break  # no more moves possible

    print(f"Rebalancing complete: {rebalance_moves} moves, {len(forced_overrides)} availability overrides")
    remaining_gaps = sum(provider_utilization_gap(p) for p in eligible_providers)
    if remaining_gaps > 0:
        print(f"  {remaining_gaps} provider-periods remain — running forced fill (relax consecutive cap)...")

    # ─── Forced Fill: relax consecutive cap only ────────────────────────
    # If providers still have gaps, allow longer stretches but NEVER override availability.
    forced_stretch_overrides = []  # (pname, period_idx, consec_run_length)

    if remaining_gaps > 0:
        for iteration in range(MAX_REBALANCE_ITERS):
            under = [(p, provider_utilization_gap(p)) for p in eligible_providers
                     if provider_utilization_gap(p) > 0]
            under.sort(key=lambda x: -x[1])
            if not under:
                break

            moved = False
            for pname, gap in under:
                pdata = eligible_providers[pname]
                for ptype in ["week", "weekend"]:
                    if ptype == "week" and prov_week_count[pname] >= math.ceil(pdata["weeks_remaining"]):
                        continue
                    if ptype == "weekend" and prov_we_count[pname] >= math.ceil(pdata["weekends_remaining"]):
                        continue

                    all_sites_set2 = set(s for s, _ in sites_demand.keys())
                    sorted_p = sorted(range(len(periods)),
                                      key=lambda i: -period_shortfall(i) if periods[i]["type"] == ptype else 999)

                    for idx in sorted_p:
                        period = periods[idx]
                        if period["type"] != ptype:
                            continue
                        if any(n == pname for n, _ in period_assignments[idx]):
                            continue

                        # Try with relaxed consecutive cap (max 3 weeks)
                        if can_place(pname, idx, ignore_consec=True):
                            # Check that placing here won't exceed 3 consecutive weeks
                            wk_num = period["num"]
                            consec_back = count_consec_at(pname, wk_num)
                            active = get_active_week_nums(pname)
                            fwd = 0
                            c = wk_num + 1
                            while c <= 17 and c in active:
                                fwd += 1
                                c += 1
                            run_len = consec_back + fwd + 1
                            if run_len > 3:
                                continue  # would create 4+ consecutive — skip

                            site = find_best_site_for(pname, idx)
                            if site:
                                if run_len > 2:
                                    forced_stretch_overrides.append((pname, idx, run_len))
                                place_provider(pname, idx, site)
                                rebalance_moves += 1
                                moved = True
                                break

                        # Do NOT override availability — honor all schedule requests.
                        # Under-utilization will be documented in the report.
                    else:
                        continue
                    break
            if not moved:
                break

    # ─── Gap-Leveling Pass: smooth shortfalls across periods ─────────────
    # After all placements, move providers from surplus periods to shortfall periods
    # at the same site to even out coverage. This targets the max-short problem.
    level_moves = 0
    MAX_LEVEL_ITERS = 100

    # Build site list for leveling (computed early, before results dict)
    _all_sites_level = sorted(set(s for s, _ in sites_demand.keys()))

    for _lvl_iter in range(MAX_LEVEL_ITERS):
        moved = False

        for site in _all_sites_level:
            for dtype in ["weekday", "weekend"]:
                demand = sites_demand.get((site, dtype), 0)
                if demand == 0:
                    continue
                ptype = "week" if dtype == "weekday" else "weekend"

                # Compute per-period fill for this site
                period_fills = {}  # period_idx -> fill count
                for idx2, p2 in enumerate(periods):
                    if p2["type"] != ptype:
                        continue
                    filled = sum(1 for _, s in period_assignments[idx2] if s == site)
                    period_fills[idx2] = filled

                if not period_fills:
                    continue

                # Find worst shortfall and best surplus
                worst_idx = min(period_fills, key=lambda i: period_fills[i])
                worst_fill = period_fills[worst_idx]
                best_idx = max(period_fills, key=lambda i: period_fills[i])
                best_fill = period_fills[best_idx]

                # Only level if there's a spread of 2+ between best and worst
                # and the worst period is actually short
                if best_fill - worst_fill < 2:
                    continue
                if worst_fill >= demand:
                    continue
                # Don't steal from periods that are at or below demand
                if best_fill <= demand:
                    continue

                # Find a provider in the surplus period we can move
                for pname, psite in list(period_assignments[best_idx]):
                    if psite != site:
                        continue
                    # Check they're not already in the worst period
                    if any(n == pname for n, _ in period_assignments[worst_idx]):
                        continue
                    # Check capacity — moving doesn't change totals, just redistribution
                    # Check consecutive constraint at destination
                    worst_wk = periods[worst_idx]["num"]
                    consec = count_consec_at(pname, worst_wk)
                    if consec >= 2:
                        continue  # would create too-long stretch
                    # Check availability at destination
                    if not is_available(pname, name_map.get(pname),
                                       periods[worst_idx]["dates"], unavailable_dates):
                        continue
                    # Check they don't have a week/weekend pair at the source
                    src_wk = periods[best_idx]["num"]
                    paired_src = False
                    for idx3, p3 in enumerate(periods):
                        if p3["num"] == src_wk and p3["type"] != ptype:
                            if any(n == pname for n, _ in period_assignments[idx3]):
                                paired_src = True
                                break
                    if paired_src:
                        continue  # don't break a stretch

                    # Do the move
                    remove_provider(pname, best_idx)
                    place_provider(pname, worst_idx, site)
                    level_moves += 1
                    moved = True
                    break  # restart inner loop

                if moved:
                    break  # restart site loop
            if moved:
                break  # restart dtype loop

        if not moved:
            break

    # ─── Cross-Site Gap Fill: move unassigned providers into worst gaps ────
    # Find periods with the worst shortfalls and try to place providers from
    # OTHER sites where they're at surplus, or providers not assigned that period.
    cross_fill_moves = 0
    for _cf_iter in range(MAX_LEVEL_ITERS):
        # Find the worst shortfall period+site combination
        worst_shortfall = 0
        worst_info = None
        for idx2, p2 in enumerate(periods):
            ptype = p2["type"]
            dtype = "weekday" if ptype == "week" else "weekend"
            for site in _all_sites_level:
                demand = sites_demand.get((site, dtype), 0)
                filled = sum(1 for _, s in period_assignments[idx2] if s == site)
                short = demand - filled
                if short > worst_shortfall:
                    worst_shortfall = short
                    worst_info = (idx2, site, ptype, demand, filled)

        if worst_shortfall <= 0 or worst_info is None:
            break

        idx2, target_site, ptype, demand, filled = worst_info
        wk_num = periods[idx2]["num"]
        pct_field = SITE_PCT_MAP.get(target_site, "")

        # Look for providers NOT in this period who:
        # 1. Are eligible for the target site
        # 2. Have remaining capacity (or are assigned elsewhere this period at an overfilled site)
        # 3. Can satisfy consecutive constraint
        placed = False

        # Strategy A: find an unassigned-this-period AVAILABLE provider with capacity
        # Schedule requests are always honored — never override availability.
        assigned_names = set(n for n, _ in period_assignments[idx2])
        candidates_cf = []
        for pname, pdata in eligible_providers.items():
            if pname in assigned_names:
                continue
            # Must have capacity
            if ptype == "week":
                if prov_week_count[pname] >= math.ceil(pdata["weeks_remaining"]):
                    continue
            else:
                if prov_we_count[pname] >= math.ceil(pdata["weekends_remaining"]):
                    continue
            # Must be eligible for target site
            esites = get_eligible_sites(pname, pdata, tags_data)
            if target_site not in esites:
                continue
            # Must be available — honor all schedule requests
            if not is_available(pname, name_map.get(pname), periods[idx2]["dates"], unavailable_dates):
                continue
            # Check consecutive cap (relaxed: allow up to 3 for leveling)
            consec = count_consec_at(pname, wk_num)
            active = get_active_week_nums(pname)
            fwd = 0
            c = wk_num + 1
            while c <= 17 and c in active:
                fwd += 1
                c += 1
            total_run = consec + fwd + 1
            if total_run > 3:  # Allow up to 3 consecutive for leveling
                continue

            site_pct = pdata.get(pct_field, 0) if pct_field else 0
            # Prefer providers with utilization gaps (contractual need)
            ugap = provider_utilization_gap(pname)
            score = site_pct * 100 + ugap * 10
            candidates_cf.append((pname, score, total_run))

        candidates_cf.sort(key=lambda x: -x[1])

        if candidates_cf:
            pname = candidates_cf[0][0]
            total_run = candidates_cf[0][2]
            place_provider(pname, idx2, target_site)
            cross_fill_moves += 1
            if total_run > 2:
                forced_stretch_overrides.append((pname, idx2, total_run))
            placed = True

        # Strategy B: move a provider from a site with smaller shortfall to the
        # target site with bigger shortfall — levels coverage across sites
        if not placed:
            e_dtype = "weekday" if ptype == "week" else "weekend"
            target_demand = sites_demand.get((target_site, e_dtype), 0)
            target_filled = sum(1 for _, s in period_assignments[idx2] if s == target_site)
            target_short = target_demand - target_filled

            # Build list of candidates: providers at sites with smaller shortfall
            swap_candidates = []
            for existing_name, existing_site in list(period_assignments[idx2]):
                if existing_site == target_site:
                    continue
                e_demand = sites_demand.get((existing_site, e_dtype), 0)
                e_filled = sum(1 for _, s in period_assignments[idx2] if s == existing_site)
                e_short = e_demand - e_filled  # negative = overfilled, 0 = at demand

                # Only steal if their site's shortfall is LESS than target's shortfall
                # (leveling: move from less-short to more-short)
                if e_short >= target_short:
                    continue  # their site is just as bad or worse

                edata = eligible_providers.get(existing_name)
                if not edata:
                    continue

                # Is this provider eligible for the target site?
                esites = get_eligible_sites(existing_name, edata, tags_data)
                if target_site not in esites:
                    continue

                # Score: prefer stealing from overfilled or at-demand sites
                score = -e_short  # higher = more overfilled source
                swap_candidates.append((existing_name, existing_site, e_short, score))

            swap_candidates.sort(key=lambda x: -x[3])

            if swap_candidates:
                existing_name, existing_site, _, _ = swap_candidates[0]
                remove_provider(existing_name, idx2)
                place_provider(existing_name, idx2, target_site)
                cross_fill_moves += 1
                placed = True

        if not placed:
            break  # can't improve further

    final_gaps = sum(provider_utilization_gap(p) for p in eligible_providers)

    # ── Deduplicate stretch overrides: one entry per unique provider + run ──
    # The raw list logs per-placement, so the same 3-week run may appear
    # multiple times (once for weekday, once for weekend in the same week).
    # Deduplicate by finding the actual run each override belongs to.
    seen_runs = set()  # (pname, run_start_wk, run_end_wk)
    deduped_overrides = []
    for pname, pidx, raw_run_len in forced_stretch_overrides:
        wk_num = periods[pidx]["num"]
        # Find actual run containing this week
        active = get_active_week_nums(pname)
        # Walk backward from wk_num
        start = wk_num
        while start - 1 in active:
            start -= 1
        # Walk forward from wk_num
        end = wk_num
        while end + 1 in active:
            end += 1
        run_key = (pname, start, end)
        if run_key not in seen_runs:
            seen_runs.add(run_key)
            actual_len = end - start + 1
            if actual_len > 2:
                mid_wk = start + actual_len // 2
                mid_pidx = next((pi for pi, p in enumerate(periods)
                                 if p["num"] == mid_wk and p["type"] == "week"), pidx)
                deduped_overrides.append((pname, mid_pidx, actual_len))

    forced_stretch_overrides = sorted(deduped_overrides, key=lambda x: (-x[2], x[0]))

    print(f"Gap-leveling: {level_moves} redistribution + {cross_fill_moves} cross-fill moves")
    print(f"Final: {rebalance_moves} total moves, {len(forced_overrides)} avail overrides, "
          f"{len(forced_stretch_overrides)} stretch overrides, {final_gaps} remaining gaps")

    # ─── Build Results Dict ───────────────────────────────────────────────

    all_sites = sorted(set(s for s, _ in sites_demand.keys()))
    all_sites = [s for s in all_sites if s != "Cooper"] + ["Cooper"]

    # Compute stretch info per provider per week_num
    stretch_map = {}  # (pname, week_num) -> "stretch" | "cross_site" | "week_only" | "we_only"
    for idx, period in enumerate(periods):
        if period["type"] != "week":
            continue
        week_num = period["num"]
        for pname, site in period_assignments[idx]:
            we_site = None
            for idx2, p2 in enumerate(periods):
                if p2["type"] == "weekend" and p2["num"] == week_num:
                    for n2, s2 in period_assignments[idx2]:
                        if n2 == pname:
                            we_site = s2
                            break
                    break
            if we_site == site:
                stretch_map[(pname, week_num)] = "stretch"
            elif we_site is not None:
                stretch_map[(pname, week_num)] = "cross_site"
            else:
                stretch_map[(pname, week_num)] = "week_only"

    # Tag weekend-only assignments
    for idx, period in enumerate(periods):
        if period["type"] != "weekend":
            continue
        week_num = period["num"]
        for pname, site in period_assignments[idx]:
            if (pname, week_num) not in stretch_map:
                stretch_map[(pname, week_num)] = "we_only"

    # Site fill summary
    site_fill = {}
    for site in all_sites:
        wk_demand = sites_demand.get((site, "weekday"), 0)
        we_demand = sites_demand.get((site, "weekend"), 0)
        wk_fills = []
        we_fills = []
        for idx, period in enumerate(periods):
            count = sum(1 for _, s in period_assignments[idx] if s == site)
            if period["type"] == "week":
                wk_fills.append(count)
            else:
                we_fills.append(count)
        site_fill[site] = {
            "wk_demand": wk_demand, "we_demand": we_demand,
            "wk_fills": wk_fills, "we_fills": we_fills,
        }

    # Provider utilization — with reasons for under-utilization
    over_assigned = []
    under_assigned = []
    under_utilization_reasons = {}  # pname -> {reason, detail, ...}
    for pname in sorted(eligible_providers.keys()):
        pdata = eligible_providers[pname]
        wk_rem = pdata["weeks_remaining"]
        we_rem = pdata["weekends_remaining"]
        wk_target = max(0, math.ceil(wk_rem))
        we_target = max(0, math.ceil(we_rem))
        wk_used = prov_week_count[pname]
        we_used = prov_we_count[pname]
        if wk_used > wk_target or we_used > we_target:
            over_assigned.append(pname)
        elif wk_used < wk_target or we_used < we_target:
            under_assigned.append(pname)
            # Classify why they couldn't be fully scheduled
            jname = name_map.get(pname)
            unavail = unavailable_dates.get(jname, set()) if jname else set()
            unavail_week_nums = set()
            for pi, p in enumerate(periods):
                if any(d in unavail for d in p["dates"]):
                    unavail_week_nums.add(p["num"])
            avail_wks = n_weeks - len(unavail_week_nums)
            total_owed = wk_target + we_target
            reasons = []
            if avail_wks < total_owed:
                reasons.append("excessive_time_off")
            # Check if all eligible sites were full when they were available
            esites = get_eligible_sites(pname, pdata, tags_data)
            if not esites:
                reasons.append("no_eligible_sites")
            if not reasons:
                reasons.append("scheduling_constraint")
            under_utilization_reasons[pname] = {
                "reasons": reasons,
                "wk_target": wk_target,
                "we_target": we_target,
                "wk_used": wk_used,
                "we_used": we_used,
                "wk_gap": max(0, wk_target - wk_used),
                "we_gap": max(0, we_target - we_used),
                "avail_weeks": avail_wks,
                "total_weeks": n_weeks,
                "unavail_weeks": len(unavail_week_nums),
                "eligible_sites": list(esites),
            }

    # Stretch totals
    stretch_count = sum(1 for v in stretch_map.values() if v == "stretch")
    non_stretch_weeks = sum(1 for v in stretch_map.values() if v == "week_only")
    cross_site_stretches = sum(1 for v in stretch_map.values() if v == "cross_site")

    results = {
        "periods": periods,
        "period_assignments": dict(period_assignments),
        "eligible_providers": eligible_providers,
        "all_providers": providers,
        "tags_data": tags_data,
        "sites_demand": sites_demand,
        "all_sites": all_sites,
        "site_fill": site_fill,
        "prov_week_count": dict(prov_week_count),
        "prov_we_count": dict(prov_we_count),
        "prov_site_counts": {k: dict(v) for k, v in prov_site_counts.items()},
        "prov_assignments": dict(prov_assignments),
        "prov_week_site": dict(prov_week_site),
        "flex_used": dict(flex_used),
        "stretch_map": stretch_map,
        "over_assigned": over_assigned,
        "under_assigned": under_assigned,
        "under_utilization_reasons": under_utilization_reasons,
        "stretch_count": stretch_count,
        "non_stretch_weeks": non_stretch_weeks,
        "cross_site_stretches": cross_site_stretches,
        "n_weeks": n_weeks,
        "n_weekends": n_weekends,
        "block_start": BLOCK_START,
        "block_end": BLOCK_END,
        "forced_overrides": forced_overrides,
        "forced_stretch_overrides": forced_stretch_overrides,
        "rebalance_moves": rebalance_moves,
        "level_moves": level_moves,
        "unavailable_dates": unavailable_dates,
        "name_map": name_map,
    }

    return results


def write_text_report(results):
    """Write the text-format report (backward compat)."""
    periods = results["periods"]
    period_assignments = results["period_assignments"]
    eligible_providers = results["eligible_providers"]
    sites_demand = results["sites_demand"]
    all_sites = results["all_sites"]
    site_fill = results["site_fill"]
    prov_week_count = results["prov_week_count"]
    prov_we_count = results["prov_we_count"]
    prov_week_site = results["prov_week_site"]
    flex_used = results["flex_used"]
    stretch_map = results["stretch_map"]

    lines = []
    lines.append("=" * 90)
    lines.append("PROPOSED BLOCK 3 SCHEDULE (v2.1)")
    lines.append(f"Block: {BLOCK_START.strftime('%b %d')} – {BLOCK_END.strftime('%b %d, %Y')}")
    lines.append(f"Periods: {results['n_weeks']} weeks, {results['n_weekends']} weekends")
    lines.append(f"Eligible providers: {len(eligible_providers)}")
    lines.append("=" * 90)

    for site in all_sites:
        sf = site_fill[site]
        lines.append(f"\n{'='*90}\n  {site.upper()}\n{'='*90}")
        lines.append(f"  Demand: {sf['wk_demand']} weekday / {sf['we_demand']} weekend\n")

        for idx, period in enumerate(periods):
            assignments = [(n, s) for n, s in period_assignments.get(idx, []) if s == site]
            demand = sf["wk_demand"] if period["type"] == "week" else sf["we_demand"]
            filled = len(assignments)
            status = "OK" if filled >= demand else f"SHORT {demand - filled}"
            dates_str = f"{period['dates'][0]} to {period['dates'][-1]}"
            prefix = "  Week" if period["type"] == "week" else "    WE"
            lines.append(f"{prefix} {period['num']:2d}: {dates_str}  — {filled}/{demand} [{status}]")
            for pname, _ in sorted(assignments):
                pdata = eligible_providers[pname]
                pct_field = SITE_PCT_MAP.get(site, "")
                pct = pdata.get(pct_field, 0) if pct_field else 0
                wk_u = prov_week_count.get(pname, 0)
                we_u = prov_we_count.get(pname, 0)
                s_flag = " [stretch]" if stretch_map.get((pname, period["num"])) == "stretch" else ""
                f_flag = " [FLEX]" if site in flex_used.get(pname, []) else ""
                lines.append(f"    {pname:<40s} wk {wk_u}/{math.ceil(pdata['weeks_remaining'])} "
                             f"we {we_u}/{math.ceil(pdata['weekends_remaining'])}  "
                             f"{int(pct*100)}%{s_flag}{f_flag}")
            lines.append("")

    lines.append(f"\n{'='*90}\nSITE FILL SUMMARY\n{'='*90}")
    for site in all_sites:
        sf = site_fill[site]
        wk_short = sum(max(0, sf["wk_demand"] - f) for f in sf["wk_fills"])
        we_short = sum(max(0, sf["we_demand"] - f) for f in sf["we_fills"])
        wk_over = sum(max(0, f - sf["wk_demand"]) for f in sf["wk_fills"])
        we_over = sum(max(0, f - sf["we_demand"]) for f in sf["we_fills"])
        lines.append(f"\n  {site}:")
        lines.append(f"    Weekdays: need {sf['wk_demand']}, filled "
                     f"{min(sf['wk_fills'])}–{max(sf['wk_fills'])}, "
                     f"total short: {wk_short}, total over: {wk_over}")
        lines.append(f"    Weekends: need {sf['we_demand']}, filled "
                     f"{min(sf['we_fills'])}–{max(sf['we_fills'])}, "
                     f"total short: {we_short}, total over: {we_over}")

    lines.append(f"\n{'='*90}\nPROVIDER UTILIZATION\n{'='*90}")
    if results["over_assigned"]:
        lines.append("\n  OVER-ASSIGNED:")
        for pname in results["over_assigned"]:
            pd = eligible_providers[pname]
            lines.append(f"    {pname:<40s} wk {prov_week_count.get(pname,0)}/{math.ceil(pd['weeks_remaining'])}  "
                         f"we {prov_we_count.get(pname,0)}/{math.ceil(pd['weekends_remaining'])}")
    else:
        lines.append("\n  No over-assigned providers.")
    under_reasons = results.get("under_utilization_reasons", {})
    if results["under_assigned"]:
        lines.append(f"\n  UNDER-UTILIZED: {len(results['under_assigned'])} providers")
        for pname in results["under_assigned"]:
            pd = eligible_providers[pname]
            ur = under_reasons.get(pname, {})
            reason_strs = []
            for r in ur.get("reasons", []):
                if r == "excessive_time_off":
                    reason_strs.append(f"time-off requests ({ur.get('unavail_weeks', '?')}/{ur.get('total_weeks', '?')} wks unavail)")
                elif r == "no_eligible_sites":
                    reason_strs.append("no eligible sites")
                else:
                    reason_strs.append("scheduling constraint")
            reason_label = "; ".join(reason_strs) if reason_strs else "unknown"
            lines.append(f"    {pname:<40s} wk {prov_week_count.get(pname,0)}/{math.ceil(pd['weeks_remaining'])}  "
                         f"we {prov_we_count.get(pname,0)}/{math.ceil(pd['weekends_remaining'])}  "
                         f"— {reason_label}")

    if flex_used:
        lines.append(f"\n{'='*90}\nLOCATION FLEX USAGE (±10%)\n{'='*90}")
        for pname in sorted(flex_used.keys()):
            lines.append(f"  {pname}: flexed to {', '.join(flex_used[pname])}")

    lines.append(f"\n{'='*90}\nSTRETCH ANALYSIS\n{'='*90}")
    lines.append(f"  Week+weekend same-site stretches: {results['stretch_count']}")
    lines.append(f"  Week without weekend (unmatched): {results['non_stretch_weeks']}")
    lines.append(f"  Cross-site week/weekend:          {results['cross_site_stretches']}")

    output = "\n".join(lines)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    outpath = os.path.join(OUTPUT_DIR, "proposed_schedule_v2.txt")
    with open(outpath, "w") as f:
        f.write(output)
    print(f"Text report saved to: {outpath}")


if __name__ == "__main__":
    results = run_engine()
    write_text_report(results)
