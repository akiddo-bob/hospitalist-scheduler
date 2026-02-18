#!/usr/bin/env python3
"""
Shared data loader for all block scheduling engine versions.

Loads provider data, tags, site demand, and availability from the Google Sheet
and individual schedule JSONs. Every engine version imports from here — no
version-specific data loading code should exist.

Data sources:
  1. Google Sheet (Providers, Provider Tags, Sites tabs)
  2. Individual schedule JSONs (input/individualSchedules/)

All provider name resolution uses the shared name_match module at project root.
"""

import csv
import io
import json
import math
import os
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta

# Project-root shared module for name matching
import sys
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from name_match import normalize_name, to_canonical, match_provider, clean_html_provider

# ─── Configuration ───────────────────────────────────────────────────────────

SHEET_ID = "1dbHUkE-pLtQJK02ig2EbU2eny33N60GQUvk4muiXW5M"
SHEET_BASE = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv"

SCHEDULES_DIR = os.path.join(_PROJECT_ROOT, "input", "individualSchedules")
OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")

# ─── Site/percentage mapping ─────────────────────────────────────────────────
# Maps each site name to the provider-sheet percentage column that governs it.
# Multiple sites can share one percentage column (e.g., Vineland + Elmer both
# use pct_inspira_veb). This is the single source of truth for this mapping.

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

# Reverse: percentage field → list of sites it unlocks
PCT_TO_SITES = {
    "pct_cooper":      ["Cooper"],
    "pct_inspira_veb": ["Vineland", "Elmer"],
    "pct_inspira_mhw": ["Mullica Hill"],
    "pct_mannington":  ["Mannington"],
    "pct_virtua":      ["Virtua Voorhees", "Virtua Marlton", "Virtua Willingboro", "Virtua Mt Holly"],
    "pct_cape":        ["Cape"],
}

# Site display colors (for reports)
SITE_COLORS = {
    "Cooper":             "#1565c0",
    "Vineland":           "#6a1b9a",
    "Elmer":              "#4a148c",
    "Mullica Hill":       "#2e7d32",
    "Mannington":         "#e65100",
    "Virtua Voorhees":    "#00838f",
    "Virtua Marlton":     "#00695c",
    "Virtua Willingboro": "#0277bd",
    "Virtua Mt Holly":    "#00796b",
    "Cape":               "#c62828",
}

# Site short names (for compact display)
SITE_SHORT = {
    "Cooper":             "CUH",
    "Vineland":           "VIN",
    "Elmer":              "ELM",
    "Mullica Hill":       "MH",
    "Mannington":         "MAN",
    "Virtua Voorhees":    "V-VH",
    "Virtua Marlton":     "V-MAR",
    "Virtua Willingboro": "V-WB",
    "Virtua Mt Holly":    "V-MH",
    "Cape":               "CAPE",
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def parse_float(val):
    """Parse a float value, returning 0.0 for empty/invalid."""
    if val is None or val == "":
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def fetch_sheet_csv(tab_name):
    """Fetch a tab from the Google Sheet as CSV text.

    Uses the public gviz CSV export endpoint — no API key required.
    The Sheet must have link sharing enabled (view access).
    """
    url = f"{SHEET_BASE}&sheet={urllib.request.quote(tab_name)}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8")


# ─── Data Loading Functions ──────────────────────────────────────────────────

def load_providers():
    """Load provider data from the Google Sheet 'Providers' tab.

    Returns:
        dict: provider_name -> {shift_type, fte, scheduler, annual_weeks,
              annual_weekends, annual_nights, weeks_remaining, weekends_remaining,
              nights_remaining, pct_cooper, pct_inspira_veb, pct_inspira_mhw,
              pct_mannington, pct_virtua, pct_cape, holiday_1, holiday_2}
    """
    text = fetch_sheet_csv("Providers")
    reader = csv.DictReader(io.StringIO(text))
    providers = {}

    for row in reader:
        name = row.get("provider_name", "").strip()
        if not name:
            continue

        providers[name] = {
            "shift_type":         row.get("shift_type", "").strip(),
            "fte":                parse_float(row.get("fte", 0)),
            "scheduler":          row.get("scheduler", "").strip(),
            "annual_weeks":       parse_float(row.get("annual_weeks", 0)),
            "annual_weekends":    parse_float(row.get("annual_weekends", 0)),
            "annual_nights":      parse_float(row.get("annual_nights", 0)),
            "weeks_remaining":    parse_float(row.get("weeks_remaining", 0)),
            "weekends_remaining": parse_float(row.get("weekends_remaining", 0)),
            "nights_remaining":   parse_float(row.get("nights_remaining", 0)),
            "pct_cooper":         parse_float(row.get("pct_cooper", 0)),
            "pct_inspira_veb":    parse_float(row.get("pct_inspira_veb", 0)),
            "pct_inspira_mhw":    parse_float(row.get("pct_inspira_mhw", 0)),
            "pct_mannington":     parse_float(row.get("pct_mannington", 0)),
            "pct_virtua":         parse_float(row.get("pct_virtua", 0)),
            "pct_cape":           parse_float(row.get("pct_cape", 0)),
            "holiday_1":          row.get("holiday_1", "").strip(),
            "holiday_2":          row.get("holiday_2", "").strip(),
        }

    return providers


def load_tags():
    """Load provider tags from the Google Sheet 'Provider Tags' tab.

    Returns:
        dict: provider_name -> [{"tag": str, "rule": str}, ...]
    """
    text = fetch_sheet_csv("Provider Tags")
    reader = csv.DictReader(io.StringIO(text))
    tags = defaultdict(list)

    for row in reader:
        name = row.get("provider_name", "").strip()
        tag = row.get("tag", "").strip()
        rule = row.get("rule", "").strip()
        if name and tag:
            tags[name].append({"tag": tag, "rule": rule})

    return dict(tags)


def load_sites():
    """Load site demand from the Google Sheet 'Sites' tab.

    Returns:
        dict: (site_name, day_type) -> providers_needed
              day_type is "weekday", "weekend", or "swing"
    """
    text = fetch_sheet_csv("Sites")
    reader = csv.DictReader(io.StringIO(text))
    sites = {}

    for row in reader:
        site = row.get("site", "").strip()
        day_type = row.get("day_type", "").strip()
        needed = int(row.get("providers_needed", 0))
        if site and day_type:
            sites[(site, day_type)] = needed

    return sites


def load_availability():
    """Load individual schedule JSONs to build per-provider unavailable dates.

    Reads all JSON files from input/individualSchedules/. Each file contains
    one provider's availability for one month (status: available/unavailable/blank).

    Returns:
        dict: provider_name (as in JSON) -> set of unavailable date strings (YYYY-MM-DD)
    """
    availability = {}  # name -> set of unavailable dates

    if not os.path.isdir(SCHEDULES_DIR):
        print(f"  WARNING: Schedules directory not found: {SCHEDULES_DIR}")
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


def build_name_map(sheet_providers, json_availability):
    """Build a mapping from Google Sheet provider names to JSON availability names.

    Uses the shared name_match module for robust matching across data sources.

    Args:
        sheet_providers: dict of provider_name -> data (from load_providers)
        json_availability: dict of json_name -> unavailable_dates (from load_availability)

    Returns:
        tuple: (name_map, unmatched)
          name_map: dict sheet_name -> json_name (or None if no match)
          unmatched: list of sheet names with no JSON match
    """
    json_names = list(json_availability.keys())
    name_map = {}
    unmatched = []

    for sheet_name in sheet_providers:
        json_name = match_provider(sheet_name, json_names)
        if json_name:
            name_map[sheet_name] = json_name
        else:
            name_map[sheet_name] = None
            unmatched.append(sheet_name)

    return name_map, unmatched


# ─── Eligibility & Site Logic ────────────────────────────────────────────────

def get_eligible_sites(provider_name, provider_data, tags_data):
    """Return list of sites a provider can work at.

    Based on percentage allocations (> 0) minus tag-based restrictions
    (no_elmer, no_vineland).

    Args:
        provider_name: str
        provider_data: dict with pct_* fields
        tags_data: dict provider_name -> [{"tag": ..., "rule": ...}, ...]

    Returns:
        list of site name strings
    """
    sites = []

    for pct_field, site_list in PCT_TO_SITES.items():
        if provider_data.get(pct_field, 0) > 0:
            sites.extend(site_list)

    # Apply tag-based restrictions
    ptags = tags_data.get(provider_name, [])
    for t in ptags:
        tag = t["tag"]
        if tag == "no_elmer":
            sites = [s for s in sites if s != "Elmer"]
        elif tag == "no_vineland":
            sites = [s for s in sites if s != "Vineland"]

    return sites


def has_tag(provider_name, tag_name, tags_data):
    """Check if a provider has a specific tag."""
    ptags = tags_data.get(provider_name, [])
    return any(t["tag"] == tag_name for t in ptags)


def get_tag_rules(provider_name, tag_name, tags_data):
    """Get all rule texts for a specific tag on a provider."""
    ptags = tags_data.get(provider_name, [])
    return [t["rule"] for t in ptags if t["tag"] == tag_name]


# ─── Period Construction ─────────────────────────────────────────────────────

def build_periods(block_start, block_end):
    """Build the list of week and weekend periods for a block.

    Each period is either a weekday block (Mon-Fri) or weekend (Sat-Sun).
    Periods are paired by week_num for stretch pairing.

    Args:
        block_start: datetime — first Monday of the block
        block_end: datetime — last Sunday of the block

    Returns:
        list of dicts, each with:
          type: "week" or "weekend"
          num: week number (1-based)
          dates: list of date strings (YYYY-MM-DD)
          label: human-readable label
    """
    periods = []
    current = block_start
    week_num = 0

    while current <= block_end:
        dow = current.weekday()  # 0=Monday
        if dow == 0:
            week_num += 1

            # Weekday period: Mon-Fri
            week_dates = []
            for i in range(5):
                d = current + timedelta(days=i)
                if d <= block_end:
                    week_dates.append(d.strftime("%Y-%m-%d"))
            if week_dates:
                periods.append({
                    "type": "week",
                    "num": week_num,
                    "dates": week_dates,
                    "label": f"Week {week_num}: {week_dates[0]} to {week_dates[-1]}",
                })

            # Weekend period: Sat-Sun
            sat = current + timedelta(days=5)
            sun = current + timedelta(days=6)
            we_dates = []
            if sat <= block_end:
                we_dates.append(sat.strftime("%Y-%m-%d"))
            if sun <= block_end:
                we_dates.append(sun.strftime("%Y-%m-%d"))
            if we_dates:
                periods.append({
                    "type": "weekend",
                    "num": week_num,
                    "dates": we_dates,
                    "label": f"  WE {week_num}: {we_dates[0]} to {we_dates[-1]}",
                })

            current += timedelta(days=7)
        else:
            # Advance to next Monday
            current += timedelta(days=(7 - dow))

    return periods


# ─── Availability Check ─────────────────────────────────────────────────────

def is_available(provider_name, json_name, dates, unavailable_dates):
    """Check if a provider is available for ALL dates in a period.

    If the provider has no JSON file (json_name is None), they are treated
    as fully available per the rules document.

    Args:
        provider_name: Google Sheet name (for logging)
        json_name: matched JSON name (or None)
        dates: list of date strings to check
        unavailable_dates: dict json_name -> set of unavailable date strings

    Returns:
        True if provider is available for all dates
    """
    if json_name is None:
        return True  # no JSON = fully available
    unavail = unavailable_dates.get(json_name, set())
    return not any(d in unavail for d in dates)
