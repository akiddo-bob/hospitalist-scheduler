#!/usr/bin/env python3
"""
Generate an interactive HTML report from the block schedule engine output.

Views:
  - Monthly calendar: who's at each site each week/weekend
  - Provider detail: click any provider to see their full block schedule
  - Site fill summary, utilization flags, flex usage, stretch analysis
"""

import calendar
import json
import math
import os
import html as html_mod
from datetime import datetime, timedelta
from collections import defaultdict
from block_schedule_engine import (run_engine, SITE_PCT_MAP, OUTPUT_DIR, BLOCK_START, BLOCK_END,
                                   load_availability, build_name_index, match_provider_to_json,
                                   SCHEDULES_DIR)

# Site color palette
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

SITE_SHORT = {
    "Cooper": "CUH",
    "Vineland": "VIN",
    "Elmer": "ELM",
    "Mullica Hill": "MH",
    "Mannington": "MAN",
    "Virtua Voorhees": "V-VH",
    "Virtua Marlton": "V-MAR",
    "Virtua Willingboro": "V-WB",
    "Virtua Mt Holly": "V-MH",
    "Cape": "CAPE",
}


def esc(s):
    return html_mod.escape(str(s))


def prov_id(name):
    """Create a safe HTML id from provider name."""
    return "prov-" + name.replace(" ", "-").replace(",", "").replace("'", "").replace(".", "")


def render_mini_calendar(pname, avail_map, date_assignments, site_colors, site_short):
    """Render a compact 4-month availability calendar for a provider.

    avail_map: {date_str: "available"|"unavailable"|"blank"}
    date_assignments: {date_str: site_name}
    """
    block_months = [(2026, 3), (2026, 4), (2026, 5), (2026, 6)]
    month_names = {3: "March", 4: "April", 5: "May", 6: "June"}
    day_headers = ["S", "M", "T", "W", "T", "F", "S"]

    h = []
    h.append('<div class="avail-legend">')
    h.append('<span class="sw" style="background:#c8e6c9"></span> Available')
    h.append('<span class="sw" style="background:#ffcdd2"></span> Unavailable')
    h.append('<span class="sw" style="background:#fff9c4"></span> Blank/No request')
    h.append('</div>')
    h.append('<div class="avail-months">')

    for year, month in block_months:
        h.append(f'<div class="avail-month">')
        h.append(f'<h4>{month_names[month]} {year}</h4>')
        h.append('<div class="mini-cal">')

        # Day headers
        for dh in day_headers:
            h.append(f'<div class="dh">{dh}</div>')

        # Calendar days
        first_dow = calendar.weekday(year, month, 1)  # 0=Mon
        # Convert to Sunday-start: Sun=0, Mon=1, ...
        first_dow_sun = (first_dow + 1) % 7
        days_in_month = calendar.monthrange(year, month)[1]

        # Empty cells before first day
        for _ in range(first_dow_sun):
            h.append('<div class="dc empty"></div>')

        for day in range(1, days_in_month + 1):
            date_str = f"{year}-{month:02d}-{day:02d}"
            status = avail_map.get(date_str, "blank")
            cls = "dc"
            if status == "available":
                cls += " avail"
            elif status == "unavailable":
                cls += " unavail"
            else:
                cls += " blank"

            # Check if this date falls within the block
            dt = datetime(year, month, day)
            in_block = BLOCK_START <= dt <= BLOCK_END

            h.append(f'<div class="{cls}">')
            h.append(f'<div class="dn">{day}</div>')

            # Overlay assignment
            assigned_site = date_assignments.get(date_str)
            if assigned_site and in_block:
                color = site_colors.get(assigned_site, "#666")
                short = site_short.get(assigned_site, assigned_site[:3])
                h.append(f'<div class="asg" style="background:{color}">{esc(short)}</div>')

            h.append('</div>')

        # Fill remaining cells
        total_cells = first_dow_sun + days_in_month
        remaining = (7 - (total_cells % 7)) % 7
        for _ in range(remaining):
            h.append('<div class="dc empty"></div>')

        h.append('</div></div>')

    h.append('</div>')
    return "\n".join(h)


def generate_html(results):
    periods = results["periods"]
    period_assignments = results["period_assignments"]
    eligible = results["eligible_providers"]
    all_providers = results["all_providers"]
    tags_data = results["tags_data"]
    sites_demand = results["sites_demand"]
    all_sites = results["all_sites"]
    site_fill = results["site_fill"]
    prov_week_count = results["prov_week_count"]
    prov_we_count = results["prov_we_count"]
    prov_site_counts = results["prov_site_counts"]
    prov_assignments = results["prov_assignments"]
    prov_week_site = results["prov_week_site"]
    flex_used = results["flex_used"]
    stretch_map = results["stretch_map"]
    unavailable_dates = results.get("unavailable_dates", {})
    name_map = results.get("name_map", {})

    # Load full availability data (all statuses) from JSON files — single pass
    json_avail_all = {}  # json_name -> {date_str: status}
    if os.path.isdir(SCHEDULES_DIR):
        for fname in os.listdir(SCHEDULES_DIR):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(SCHEDULES_DIR, fname)
            try:
                with open(fpath) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError):
                continue
            jname = data.get("name", "").strip()
            if not jname:
                continue
            if jname not in json_avail_all:
                json_avail_all[jname] = {}
            for day in data.get("days", []):
                json_avail_all[jname][day["date"]] = day.get("status", "blank")

    # Map sheet names to JSON availability
    json_names = list(json_avail_all.keys())
    name_index = build_name_index(json_names)
    prov_avail_data = {}  # sheet_name -> {date_str: status}
    for pname in eligible:
        jname = match_provider_to_json(pname, name_index)
        if jname and jname in json_avail_all:
            prov_avail_data[pname] = json_avail_all[jname]

    # Build date -> site assignment map per provider (for overlaying on calendar)
    prov_date_assignments = defaultdict(dict)  # pname -> {date: site}
    for idx, period in enumerate(periods):
        for pname, site in period_assignments.get(idx, []):
            for d in period["dates"]:
                prov_date_assignments[pname][d] = site

    # Group periods by month
    months = {}  # month_key -> [period indices]
    for idx, p in enumerate(periods):
        d = datetime.strptime(p["dates"][0], "%Y-%m-%d")
        mk = d.strftime("%B %Y")
        months.setdefault(mk, []).append(idx)

    # Build provider -> list of (week_num, type, site, is_flex) for detail view
    prov_schedule = defaultdict(list)
    for idx, period in enumerate(periods):
        for pname, site in period_assignments.get(idx, []):
            is_flex = site in flex_used.get(pname, [])
            prov_schedule[pname].append({
                "period_idx": idx,
                "week_num": period["num"],
                "type": period["type"],
                "dates": period["dates"],
                "site": site,
                "is_flex": is_flex,
                "stretch": stretch_map.get((pname, period["num"]), ""),
            })

    h = []
    h.append("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Block 3 Schedule Report</title>
<style>
:root {
  --bg: #ffffff;
  --text: #1a1a1a;
  --heading: #0d47a1;
  --border: #d0d0d0;
  --stripe: #f5f7fa;
  --weekend-bg: #e3f2fd;
  --short-bg: #fce4ec;
  --short-text: #b71c1c;
  --ok-bg: #e8f5e9;
  --ok-text: #2e7d32;
  --flex-bg: #fff3e0;
  --link: #1565c0;
  --sidebar-bg: #f5f5f5;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 13px;
  color: var(--text);
  background: var(--bg);
  line-height: 1.4;
}
.container { max-width: 1600px; margin: 0 auto; padding: 16px 24px; }
h1 { font-size: 22px; color: var(--heading); margin-bottom: 4px; }
h2 { font-size: 17px; color: var(--heading); margin: 24px 0 8px; border-bottom: 2px solid var(--heading); padding-bottom: 4px; }
h3 { font-size: 14px; color: #333; margin: 16px 0 6px; }
.subtitle { color: #666; font-size: 13px; margin-bottom: 16px; }

/* Navigation tabs */
.tabs {
  display: flex; gap: 0; border-bottom: 2px solid var(--heading);
  margin-bottom: 16px; flex-wrap: wrap;
}
.tab {
  padding: 8px 16px; cursor: pointer; border: 1px solid var(--border);
  border-bottom: none; border-radius: 6px 6px 0 0; background: #f9f9f9;
  font-size: 13px; font-weight: 500; color: #555;
  margin-bottom: -2px;
}
.tab:hover { background: #e3f2fd; }
.tab.active { background: var(--heading); color: white; border-color: var(--heading); }
.tab-content { display: none; }
.tab-content.active { display: block; }

/* Tables */
table { border-collapse: collapse; width: 100%; margin-bottom: 16px; }
th, td { border: 1px solid var(--border); padding: 4px 8px; text-align: left; vertical-align: top; }
th { background: #e8eaf6; font-weight: 600; font-size: 12px; position: sticky; top: 0; z-index: 2; }
tr:nth-child(even) { background: var(--stripe); }
tr.weekend-row { background: var(--weekend-bg); }
tr.short-row { background: var(--short-bg); }

/* Site badges */
.site-badge {
  display: inline-block; padding: 2px 6px; border-radius: 3px;
  color: white; font-size: 11px; font-weight: 600; margin: 1px 2px;
  white-space: nowrap;
}

/* Provider links */
a.prov-link {
  color: var(--link); text-decoration: none; cursor: pointer;
  font-size: 12px; display: block; padding: 1px 0;
}
a.prov-link:hover { text-decoration: underline; }

/* Status badges */
.badge {
  display: inline-block; padding: 1px 6px; border-radius: 3px;
  font-size: 11px; font-weight: 600;
}
.badge-ok { background: var(--ok-bg); color: var(--ok-text); }
.badge-short { background: var(--short-bg); color: var(--short-text); }
.badge-flex { background: var(--flex-bg); color: #e65100; }
.badge-stretch { background: #e8f5e9; color: #1b5e20; }
.badge-cross { background: #fce4ec; color: #880e4f; }
.badge-warn { background: #fff8e1; color: #f57f17; }

/* Provider detail cards */
.prov-card {
  border: 1px solid var(--border); border-radius: 6px; margin-bottom: 12px;
  overflow: hidden;
}
.prov-card-header {
  background: #e8eaf6; padding: 8px 12px; cursor: pointer;
  display: flex; justify-content: space-between; align-items: center;
  font-weight: 600;
}
.prov-card-header:hover { background: #c5cae9; }
.prov-card-body { padding: 8px 12px; display: none; }
.prov-card.open .prov-card-body { display: block; }
.prov-card-header .arrow { transition: transform 0.2s; }
.prov-card.open .prov-card-header .arrow { transform: rotate(90deg); }

/* Calendar grid for provider detail */
.prov-cal { width: 100%; }
.prov-cal td { padding: 3px 6px; font-size: 12px; }
.prov-cal .off { color: #bbb; }
.prov-cal .assigned { font-weight: 600; }

/* Mini availability calendar */
.avail-months { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.avail-month { flex: 1; min-width: 200px; }
.avail-month h4 { font-size: 12px; color: #555; margin-bottom: 4px; text-align: center; }
.mini-cal { display: grid; grid-template-columns: repeat(7, 1fr); gap: 1px; background: #ddd; border: 1px solid #ddd; border-radius: 4px; overflow: hidden; }
.mini-cal .dh { background: #546e7a; color: white; text-align: center; padding: 2px; font-size: 10px; font-weight: 600; }
.mini-cal .dc { background: #fff; text-align: center; padding: 3px 1px; font-size: 10px; min-height: 24px; position: relative; }
.mini-cal .dc.empty { background: #f5f5f5; }
.mini-cal .dc.avail { background: #c8e6c9; }
.mini-cal .dc.unavail { background: #ffcdd2; }
.mini-cal .dc.blank { background: #fff9c4; }
.mini-cal .dc .dn { font-weight: 600; }
.mini-cal .dc .asg { position: absolute; bottom: 0; left: 0; right: 0; font-size: 7px; font-weight: 700; color: white; padding: 1px 0; border-radius: 0 0 2px 2px; line-height: 1; }
.avail-legend { display: flex; gap: 10px; margin-bottom: 8px; font-size: 11px; align-items: center; }
.avail-legend .sw { width: 12px; height: 12px; border: 1px solid #999; border-radius: 2px; display: inline-block; }

/* Stats grid */
.stats-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px; margin-bottom: 16px;
}
.stat-card {
  border: 1px solid var(--border); border-radius: 6px; padding: 12px;
  text-align: center;
}
.stat-card .stat-value { font-size: 28px; font-weight: 700; color: var(--heading); }
.stat-card .stat-label { font-size: 12px; color: #666; margin-top: 2px; }

/* Provider filter */
.filter-bar {
  margin-bottom: 12px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
}
.filter-bar input {
  padding: 6px 10px; border: 1px solid var(--border); border-radius: 4px;
  font-size: 13px; width: 250px;
}
.filter-bar select {
  padding: 6px 10px; border: 1px solid var(--border); border-radius: 4px;
  font-size: 13px;
}

/* Site filter buttons */
.site-filter-btn {
  padding: 4px 10px; border-radius: 4px; border: 2px solid;
  font-size: 12px; font-weight: 600; cursor: pointer; background: white;
}
.site-filter-btn.active { color: white; }

/* Utilization bar */
.util-bar {
  height: 14px; background: #e0e0e0; border-radius: 3px; overflow: hidden;
  display: inline-block; width: 80px; vertical-align: middle;
}
.util-fill { height: 100%; border-radius: 3px; }

/* Back to top */
.back-top {
  position: fixed; bottom: 20px; right: 20px; background: var(--heading);
  color: white; border: none; border-radius: 50%; width: 40px; height: 40px;
  font-size: 18px; cursor: pointer; display: none; z-index: 100;
  box-shadow: 0 2px 8px rgba(0,0,0,0.3);
}
.back-top:hover { background: #0d3a7a; }

@media print {
  .tabs, .filter-bar, .back-top { display: none; }
  .tab-content { display: block !important; page-break-before: always; }
  .prov-card-body { display: block !important; }
}
</style>
</head>
<body>
<div class="container">
""")

    # Navigation bar
    h.append(_nav_bar())

    # Header
    h.append(f'<h1>Block 3 Proposed Schedule</h1>')
    h.append(f'<div class="subtitle">{BLOCK_START.strftime("%b %d")} &ndash; '
             f'{BLOCK_END.strftime("%b %d, %Y")} &middot; '
             f'{results["n_weeks"]} weeks, {results["n_weekends"]} weekends &middot; '
             f'{len(eligible)} eligible providers</div>')

    # ── Stats cards ──
    total_wk_short = sum(
        sum(max(0, sf["wk_demand"] - f) for f in sf["wk_fills"])
        for sf in site_fill.values()
    )
    total_we_short = sum(
        sum(max(0, sf["we_demand"] - f) for f in sf["we_fills"])
        for sf in site_fill.values()
    )
    h.append('<div class="stats-grid">')
    h.append(f'<div class="stat-card"><div class="stat-value">{len(eligible)}</div>'
             f'<div class="stat-label">Eligible Providers</div></div>')
    h.append(f'<div class="stat-card"><div class="stat-value">{results["stretch_count"]}</div>'
             f'<div class="stat-label">Week+WE Stretches</div></div>')
    h.append(f'<div class="stat-card"><div class="stat-value">{results["cross_site_stretches"]}</div>'
             f'<div class="stat-label">Cross-Site Splits</div></div>')
    h.append(f'<div class="stat-card"><div class="stat-value">{total_wk_short + total_we_short}</div>'
             f'<div class="stat-label">Total Shortfall (wk+we)</div></div>')
    h.append(f'<div class="stat-card"><div class="stat-value">{len(flex_used)}</div>'
             f'<div class="stat-label">Flex Assignments</div></div>')
    h.append(f'<div class="stat-card"><div class="stat-value">{len(results["over_assigned"])}</div>'
             f'<div class="stat-label">Over-Assigned</div></div>')
    h.append('</div>')

    # ── Tabs ──
    tab_names = ["Calendar", "Providers", "Site Summary", "Utilization", "Flags", "Shortfalls", "Open Shifts"]
    h.append('<div class="tabs">')
    for i, tn in enumerate(tab_names):
        cls = " active" if i == 0 else ""
        h.append(f'<div class="tab{cls}" onclick="switchTab({i})">{tn}</div>')
    h.append('</div>')

    # ════════════════════════════════════════════════════════════════════════
    # TAB 0: Calendar view — by month, by site
    # ════════════════════════════════════════════════════════════════════════
    h.append('<div class="tab-content active" id="tab-0">')
    h.append('<h2>Monthly Calendar</h2>')

    # Site filter buttons
    h.append('<div class="filter-bar">')
    h.append('<span style="font-weight:600;margin-right:4px;">Site:</span>')
    h.append('<button class="site-filter-btn active" style="border-color:#333;background:#333;color:white" '
             'onclick="filterSite(\'all\',this)">All</button>')
    for site in all_sites:
        color = SITE_COLORS.get(site, "#666")
        h.append(f'<button class="site-filter-btn" style="border-color:{color};color:{color}" '
                 f'onclick="filterSite(\'{esc(site)}\',this)" '
                 f'data-color="{color}">{esc(SITE_SHORT.get(site, site))}</button>')
    h.append('</div>')

    for month_key, period_indices in months.items():
        h.append(f'<h3>{esc(month_key)}</h3>')
        h.append('<table class="cal-table"><thead><tr>')
        h.append('<th style="width:140px">Period</th><th style="width:70px">Fill</th>')
        # One column per site
        for site in all_sites:
            color = SITE_COLORS.get(site, "#666")
            short = SITE_SHORT.get(site, site)
            h.append(f'<th class="site-col" data-site="{esc(site)}" '
                     f'style="background:{color};color:white;font-size:11px;min-width:100px">{esc(short)}</th>')
        h.append('</tr></thead><tbody>')

        for pidx in period_indices:
            period = periods[pidx]
            is_we = period["type"] == "weekend"
            week_num = period["num"]
            d0 = period["dates"][0]
            d1 = period["dates"][-1]

            # Compute total fill across all sites
            total_assigned = len(period_assignments.get(pidx, []))
            total_demand = 0
            for site in all_sites:
                dtype = "weekend" if is_we else "weekday"
                total_demand += sites_demand.get((site, dtype), 0)

            row_class = "weekend-row" if is_we else ""
            # Check if any site is short
            any_short = False
            for site in all_sites:
                dtype = "weekend" if is_we else "weekday"
                demand = sites_demand.get((site, dtype), 0)
                filled = sum(1 for _, s in period_assignments.get(pidx, []) if s == site)
                if filled < demand:
                    any_short = True
            if any_short and not is_we:
                row_class = "short-row"

            label = f"WE {week_num}" if is_we else f"Week {week_num}"
            d0_fmt = datetime.strptime(d0, "%Y-%m-%d").strftime("%m/%d")
            d1_fmt = datetime.strptime(d1, "%Y-%m-%d").strftime("%m/%d")

            h.append(f'<tr class="{row_class}">')
            h.append(f'<td><strong>{label}</strong><br><span style="color:#888;font-size:11px">'
                     f'{d0_fmt}&ndash;{d1_fmt}</span></td>')

            # Fill badge
            fill_pct = int(100 * total_assigned / total_demand) if total_demand > 0 else 0
            badge_cls = "badge-ok" if total_assigned >= total_demand else "badge-short"
            h.append(f'<td style="text-align:center"><span class="badge {badge_cls}">'
                     f'{total_assigned}/{total_demand}</span></td>')

            # Per-site cells
            for site in all_sites:
                dtype = "weekend" if is_we else "weekday"
                demand = sites_demand.get((site, dtype), 0)
                assigned = [(n, s) for n, s in period_assignments.get(pidx, []) if s == site]
                filled = len(assigned)

                cell_style = ""
                if filled < demand:
                    cell_style = "background:#fce4ec;"
                elif filled == demand:
                    cell_style = ""

                h.append(f'<td class="site-col" data-site="{esc(site)}" style="{cell_style}">')
                # Count label
                count_badge = "badge-ok" if filled >= demand else "badge-short"
                h.append(f'<span class="badge {count_badge}" style="margin-bottom:3px">'
                         f'{filled}/{demand}</span><br>')
                # Provider names
                for pname, _ in sorted(assigned):
                    pid = prov_id(pname)
                    short_name = pname.split(",")[0].title() if "," in pname else pname
                    # add first initial
                    parts = pname.split(",")
                    if len(parts) == 2:
                        first_init = parts[1].strip()[0] if parts[1].strip() else ""
                        short_name = f"{parts[0].strip().title()}, {first_init}."
                    sm = stretch_map.get((pname, week_num), "")
                    flex_mark = "*" if site in flex_used.get(pname, []) else ""
                    title_text = f"{pname}"
                    if sm == "stretch":
                        title_text += " [stretch]"
                    if flex_mark:
                        title_text += " [FLEX]"
                    h.append(f'<a class="prov-link" href="#{pid}" title="{esc(title_text)}" '
                             f'onclick="showProvider(\'{pid}\')">'
                             f'{esc(short_name)}{flex_mark}</a>')
                h.append('</td>')
            h.append('</tr>')

        h.append('</tbody></table>')

    h.append('</div>')

    # ════════════════════════════════════════════════════════════════════════
    # TAB 1: Provider detail cards
    # ════════════════════════════════════════════════════════════════════════
    h.append('<div class="tab-content" id="tab-1">')
    h.append('<h2>Provider Schedules</h2>')

    h.append('<div class="filter-bar">')
    h.append('<input type="text" id="prov-search" placeholder="Search providers..." '
             'oninput="filterProviders()">')
    h.append('<select id="prov-site-filter" onchange="filterProviders()">')
    h.append('<option value="all">All Sites</option>')
    for site in all_sites:
        h.append(f'<option value="{esc(site)}">{esc(site)}</option>')
    h.append('</select>')
    h.append('<select id="prov-status-filter" onchange="filterProviders()">')
    h.append('<option value="all">All Status</option>')
    h.append('<option value="over">Over-assigned</option>')
    h.append('<option value="under">Under-utilized</option>')
    h.append('<option value="flex">Flex used</option>')
    h.append('</select>')
    h.append('</div>')

    for pname in sorted(eligible.keys()):
        pdata = eligible[pname]
        pid = prov_id(pname)
        wk_used = prov_week_count.get(pname, 0)
        we_used = prov_we_count.get(pname, 0)
        wk_rem = math.ceil(pdata["weeks_remaining"])
        we_rem = math.ceil(pdata["weekends_remaining"])

        # Provider sites
        psites = sorted(prov_site_counts.get(pname, {}).keys())
        site_badges = ""
        for s in psites:
            cnt = prov_site_counts[pname][s]
            color = SITE_COLORS.get(s, "#666")
            short = SITE_SHORT.get(s, s)
            site_badges += f'<span class="site-badge" style="background:{color}">{short} ({cnt})</span> '

        # Status flags
        flags = []
        if pname in results["over_assigned"]:
            flags.append('<span class="badge badge-short">OVER</span>')
        if pname in results["under_assigned"]:
            flags.append('<span class="badge badge-warn">UNDER</span>')
        if pname in flex_used:
            flags.append('<span class="badge badge-flex">FLEX</span>')

        # Tags
        ptags = tags_data.get(pname, [])

        # Data attributes for filtering
        site_data = " ".join(psites)
        status_data = ""
        if pname in results["over_assigned"]:
            status_data += "over "
        if pname in results["under_assigned"]:
            status_data += "under "
        if pname in flex_used:
            status_data += "flex "

        h.append(f'<div class="prov-card" id="{pid}" data-name="{esc(pname.lower())}" '
                 f'data-sites="{esc(site_data)}" data-status="{esc(status_data.strip())}">')
        h.append(f'<div class="prov-card-header" onclick="toggleCard(this)">')
        h.append(f'<div><span class="arrow">&#9654;</span> '
                 f'<strong>{esc(pname)}</strong> &nbsp; '
                 f'<span style="color:#666;font-size:12px">'
                 f'{esc(pdata["shift_type"])} &middot; FTE {pdata["fte"]} &middot; '
                 f'{esc(pdata["scheduler"])}</span>'
                 f'&nbsp; {" ".join(flags)}</div>')
        h.append(f'<div style="font-size:12px">wk {wk_used}/{wk_rem} &nbsp; '
                 f'we {we_used}/{we_rem} &nbsp; {site_badges}</div>')
        h.append('</div>')

        h.append('<div class="prov-card-body">')

        # Utilization bars
        h.append('<div style="display:flex;gap:24px;margin-bottom:8px">')
        wk_pct = int(100 * wk_used / wk_rem) if wk_rem > 0 else 0
        we_pct = int(100 * we_used / we_rem) if we_rem > 0 else 0
        wk_color = "#2e7d32" if wk_pct >= 80 else ("#f57f17" if wk_pct >= 50 else "#c62828")
        we_color = "#2e7d32" if we_pct >= 80 else ("#f57f17" if we_pct >= 50 else "#c62828")
        h.append(f'<div>Weeks: {wk_used}/{wk_rem} '
                 f'<div class="util-bar"><div class="util-fill" style="width:{min(100,wk_pct)}%;'
                 f'background:{wk_color}"></div></div></div>')
        h.append(f'<div>Weekends: {we_used}/{we_rem} '
                 f'<div class="util-bar"><div class="util-fill" style="width:{min(100,we_pct)}%;'
                 f'background:{we_color}"></div></div></div>')
        h.append('</div>')

        # Location split
        h.append('<div style="margin-bottom:8px"><strong>Location allocation:</strong> ')
        pct_parts = []
        for pf_name, pf_key in [("Cooper","pct_cooper"),("VEB","pct_inspira_veb"),
                                  ("MH","pct_inspira_mhw"),("Mannington","pct_mannington"),
                                  ("Virtua","pct_virtua"),("Cape","pct_cape")]:
            pct = pdata.get(pf_key, 0)
            if pct > 0:
                pct_parts.append(f"{pf_name} {int(pct*100)}%")
        h.append(", ".join(pct_parts) if pct_parts else "N/A")

        # Actual distribution
        actual_parts = []
        for s in psites:
            cnt = prov_site_counts[pname][s]
            actual_parts.append(f"{SITE_SHORT.get(s,s)}: {cnt}")
        if actual_parts:
            h.append(f' &rarr; Actual: {", ".join(actual_parts)}')
        h.append('</div>')

        # Tags
        if ptags:
            h.append('<div style="margin-bottom:8px"><strong>Tags:</strong> ')
            for t in ptags:
                h.append(f'<span class="badge" style="background:#e8eaf6;color:#333;margin:1px">'
                         f'{esc(t["tag"])}</span> ')
                if t["rule"]:
                    h.append(f'<span style="color:#666;font-size:11px">({esc(t["rule"])})</span> ')
            h.append('</div>')

        # Holiday preferences
        if pdata.get("holiday_1") or pdata.get("holiday_2"):
            h.append(f'<div style="margin-bottom:8px"><strong>Holiday prefs:</strong> '
                     f'{esc(pdata.get("holiday_1",""))} / {esc(pdata.get("holiday_2",""))}</div>')

        # Availability request calendar
        avail_map = prov_avail_data.get(pname, {})
        date_asgn = prov_date_assignments.get(pname, {})
        if avail_map or date_asgn:
            h.append('<div style="margin-bottom:10px"><strong>Availability &amp; Assignments:</strong></div>')
            h.append(render_mini_calendar(pname, avail_map, date_asgn, SITE_COLORS, SITE_SHORT))

        # Schedule table
        sched = prov_schedule.get(pname, [])
        if sched:
            h.append('<table class="prov-cal"><thead><tr>'
                     '<th>Week</th><th>Type</th><th>Dates</th><th>Site</th><th>Notes</th>'
                     '</tr></thead><tbody>')
            for entry in sorted(sched, key=lambda x: (x["week_num"], 0 if x["type"] == "week" else 1)):
                d0 = datetime.strptime(entry["dates"][0], "%Y-%m-%d").strftime("%m/%d")
                d1 = datetime.strptime(entry["dates"][-1], "%Y-%m-%d").strftime("%m/%d")
                site = entry["site"]
                color = SITE_COLORS.get(site, "#666")
                short = SITE_SHORT.get(site, site)
                row_cls = "weekend-row" if entry["type"] == "weekend" else ""

                notes = []
                if entry["stretch"] == "stretch":
                    notes.append('<span class="badge badge-stretch">stretch</span>')
                elif entry["stretch"] == "cross_site":
                    notes.append('<span class="badge badge-cross">cross-site</span>')
                if entry["is_flex"]:
                    notes.append('<span class="badge badge-flex">FLEX</span>')

                typ_label = "WE" if entry["type"] == "weekend" else "Wk"
                h.append(f'<tr class="{row_cls}">'
                         f'<td>{entry["week_num"]}</td>'
                         f'<td>{typ_label}</td>'
                         f'<td>{d0}&ndash;{d1}</td>'
                         f'<td><span class="site-badge" style="background:{color}">{esc(short)}</span></td>'
                         f'<td>{" ".join(notes)}</td></tr>')
            h.append('</tbody></table>')
        else:
            h.append('<div style="color:#999;padding:8px">No assignments this block.</div>')

        h.append('</div></div>')

    h.append('</div>')

    # ════════════════════════════════════════════════════════════════════════
    # TAB 2: Site Summary
    # ════════════════════════════════════════════════════════════════════════
    h.append('<div class="tab-content" id="tab-2">')
    h.append('<h2>Site Fill Summary</h2>')

    # Overview table
    h.append('<table><thead><tr>'
             '<th>Site</th><th>Wkday Demand</th><th>Wkday Range</th><th>Wkday Short</th>'
             '<th>Wkend Demand</th><th>Wkend Range</th><th>Wkend Short</th>'
             '</tr></thead><tbody>')
    for site in all_sites:
        sf = site_fill[site]
        wk_short = sum(max(0, sf["wk_demand"] - f) for f in sf["wk_fills"])
        we_short = sum(max(0, sf["we_demand"] - f) for f in sf["we_fills"])
        wk_min, wk_max = min(sf["wk_fills"]), max(sf["wk_fills"])
        we_min, we_max = min(sf["we_fills"]), max(sf["we_fills"])
        color = SITE_COLORS.get(site, "#666")

        wk_badge = f'<span class="badge badge-ok">0</span>' if wk_short == 0 else \
                   f'<span class="badge badge-short">{wk_short}</span>'
        we_badge = f'<span class="badge badge-ok">0</span>' if we_short == 0 else \
                   f'<span class="badge badge-short">{we_short}</span>'

        h.append(f'<tr>'
                 f'<td><span class="site-badge" style="background:{color}">{esc(site)}</span></td>'
                 f'<td style="text-align:center">{sf["wk_demand"]}</td>'
                 f'<td style="text-align:center">{wk_min}&ndash;{wk_max}</td>'
                 f'<td style="text-align:center">{wk_badge}</td>'
                 f'<td style="text-align:center">{sf["we_demand"]}</td>'
                 f'<td style="text-align:center">{we_min}&ndash;{we_max}</td>'
                 f'<td style="text-align:center">{we_badge}</td>'
                 f'</tr>')
    h.append('</tbody></table>')

    # Per-site weekly detail
    for site in all_sites:
        sf = site_fill[site]
        color = SITE_COLORS.get(site, "#666")
        h.append(f'<h3><span class="site-badge" style="background:{color}">{esc(site)}</span> '
                 f'Weekly Detail</h3>')
        h.append('<table><thead><tr><th>Week</th><th>Weekday Fill</th><th>Weekend Fill</th></tr></thead><tbody>')
        for wn in range(1, results["n_weeks"] + 1):
            wk_f = sf["wk_fills"][wn-1]
            we_f = sf["we_fills"][wn-1]
            wk_cls = "badge-ok" if wk_f >= sf["wk_demand"] else "badge-short"
            we_cls = "badge-ok" if we_f >= sf["we_demand"] else "badge-short"
            row_cls = "short-row" if wk_f < sf["wk_demand"] or we_f < sf["we_demand"] else ""
            h.append(f'<tr class="{row_cls}">'
                     f'<td>Week {wn}</td>'
                     f'<td><span class="badge {wk_cls}">{wk_f}/{sf["wk_demand"]}</span></td>'
                     f'<td><span class="badge {we_cls}">{we_f}/{sf["we_demand"]}</span></td></tr>')
        h.append('</tbody></table>')

    h.append('</div>')

    # ════════════════════════════════════════════════════════════════════════
    # TAB 3: Utilization
    # ════════════════════════════════════════════════════════════════════════
    h.append('<div class="tab-content" id="tab-3">')
    h.append('<h2>Provider Utilization</h2>')

    h.append('<table id="util-table"><thead><tr>'
             '<th>Provider</th><th>Type</th><th>FTE</th>'
             '<th>Wk Used</th><th>Wk Target</th><th>Wk %</th>'
             '<th>WE Used</th><th>WE Target</th><th>WE %</th>'
             '<th>Sites</th><th>Status</th>'
             '</tr></thead><tbody>')

    for pname in sorted(eligible.keys()):
        pdata = eligible[pname]
        wk_used = prov_week_count.get(pname, 0)
        we_used = prov_we_count.get(pname, 0)
        wk_rem = math.ceil(pdata["weeks_remaining"])
        we_rem = math.ceil(pdata["weekends_remaining"])
        wk_pct = int(100 * wk_used / wk_rem) if wk_rem > 0 else 0
        we_pct = int(100 * we_used / we_rem) if we_rem > 0 else 0

        psites = prov_site_counts.get(pname, {})
        site_str = ", ".join(f"{SITE_SHORT.get(s,s)}({c})" for s, c in sorted(psites.items()))

        status_badges = ""
        if pname in results["over_assigned"]:
            status_badges += '<span class="badge badge-short">OVER</span> '
        if pname in results["under_assigned"]:
            status_badges += '<span class="badge badge-warn">UNDER</span> '
        if pname in flex_used:
            status_badges += '<span class="badge badge-flex">FLEX</span> '
        if not status_badges:
            status_badges = '<span class="badge badge-ok">OK</span>'

        wk_color = "#2e7d32" if wk_pct >= 80 else ("#f57f17" if wk_pct >= 50 else "#c62828")
        we_color = "#2e7d32" if we_pct >= 80 else ("#f57f17" if we_pct >= 50 else "#c62828")

        pid = prov_id(pname)
        h.append(f'<tr>'
                 f'<td><a class="prov-link" href="#{pid}" onclick="showProvider(\'{pid}\')">'
                 f'{esc(pname)}</a></td>'
                 f'<td>{esc(pdata["shift_type"])}</td>'
                 f'<td>{pdata["fte"]}</td>'
                 f'<td style="text-align:center">{wk_used}</td>'
                 f'<td style="text-align:center">{wk_rem}</td>'
                 f'<td style="text-align:center;color:{wk_color};font-weight:600">{wk_pct}%</td>'
                 f'<td style="text-align:center">{we_used}</td>'
                 f'<td style="text-align:center">{we_rem}</td>'
                 f'<td style="text-align:center;color:{we_color};font-weight:600">{we_pct}%</td>'
                 f'<td style="font-size:11px">{esc(site_str)}</td>'
                 f'<td>{status_badges}</td></tr>')

    h.append('</tbody></table>')
    h.append('</div>')

    # ════════════════════════════════════════════════════════════════════════
    # TAB 4: Flags (issues, flex, stretch)
    # ════════════════════════════════════════════════════════════════════════
    h.append('<div class="tab-content" id="tab-4">')
    h.append('<h2>Flags &amp; Notes</h2>')

    # Over-assigned
    h.append('<h3>Over-Assigned Providers</h3>')
    if results["over_assigned"]:
        h.append('<table><thead><tr><th>Provider</th><th>Wk Used/Target</th>'
                 '<th>WE Used/Target</th></tr></thead><tbody>')
        for pname in results["over_assigned"]:
            pd = eligible[pname]
            h.append(f'<tr><td>{esc(pname)}</td>'
                     f'<td>{prov_week_count.get(pname,0)}/{math.ceil(pd["weeks_remaining"])}</td>'
                     f'<td>{prov_we_count.get(pname,0)}/{math.ceil(pd["weekends_remaining"])}</td></tr>')
        h.append('</tbody></table>')
    else:
        h.append('<p style="color:#2e7d32">None — all providers within targets.</p>')

    # Under-utilized
    h.append(f'<h3>Under-Utilized Providers ({len(results["under_assigned"])})</h3>')
    if results["under_assigned"]:
        h.append('<table><thead><tr><th>Provider</th><th>Wk Used/Target</th>'
                 '<th>WE Used/Target</th><th>Gap</th></tr></thead><tbody>')
        for pname in results["under_assigned"]:
            pd = eligible[pname]
            wku = prov_week_count.get(pname, 0)
            weu = prov_we_count.get(pname, 0)
            wkr = math.ceil(pd["weeks_remaining"])
            wer = math.ceil(pd["weekends_remaining"])
            gap = (wkr - wku) + (wer - weu)
            h.append(f'<tr><td><a class="prov-link" href="#{prov_id(pname)}" '
                     f'onclick="showProvider(\'{prov_id(pname)}\')">{esc(pname)}</a></td>'
                     f'<td>{wku}/{wkr}</td><td>{weu}/{wer}</td>'
                     f'<td><span class="badge badge-warn">{gap} short</span></td></tr>')
        h.append('</tbody></table>')

    # Flex usage
    h.append(f'<h3>Location Flex Usage ({len(flex_used)} providers)</h3>')
    if flex_used:
        h.append('<table><thead><tr><th>Provider</th><th>Flexed To</th>'
                 '<th>Normal Sites</th></tr></thead><tbody>')
        for pname in sorted(flex_used.keys()):
            pd = eligible.get(pname, {})
            normal_sites = get_eligible_sites_for_report(pname, pd, tags_data)
            flex_sites_list = flex_used[pname]
            h.append(f'<tr><td>{esc(pname)}</td>'
                     f'<td>{", ".join(esc(s) for s in flex_sites_list)}</td>'
                     f'<td>{", ".join(esc(s) for s in normal_sites)}</td></tr>')
        h.append('</tbody></table>')
    else:
        h.append('<p>No flex assignments used.</p>')

    # Stretch analysis
    h.append('<h3>Stretch Analysis</h3>')
    h.append(f'<table><tbody>'
             f'<tr><td>Week+weekend same-site stretches</td>'
             f'<td><span class="badge badge-stretch">{results["stretch_count"]}</span></td></tr>'
             f'<tr><td>Week without weekend (unmatched)</td>'
             f'<td><span class="badge badge-warn">{results["non_stretch_weeks"]}</span></td></tr>'
             f'<tr><td>Cross-site week/weekend splits</td>'
             f'<td><span class="badge badge-cross">{results["cross_site_stretches"]}</span></td></tr>'
             f'</tbody></table>')

    # Cross-site detail
    if results["cross_site_stretches"] > 0:
        h.append('<h3>Cross-Site Stretch Detail</h3>')
        h.append('<table><thead><tr><th>Provider</th><th>Week</th>'
                 '<th>Weekday Site</th><th>Weekend Site</th></tr></thead><tbody>')
        for (pname, wn), stype in sorted(stretch_map.items(), key=lambda x: (x[0][1], x[0][0])):
            if stype != "cross_site":
                continue
            wk_site = prov_week_site.get((pname, wn), "?")
            # Find weekend site
            we_site = "?"
            for idx, p in enumerate(periods):
                if p["type"] == "weekend" and p["num"] == wn:
                    for n, s in period_assignments.get(idx, []):
                        if n == pname:
                            we_site = s
                            break
                    break
            h.append(f'<tr><td>{esc(pname)}</td><td>{wn}</td>'
                     f'<td>{esc(wk_site)}</td><td>{esc(we_site)}</td></tr>')
        h.append('</tbody></table>')

    # Providers with negative remaining
    neg_providers = [(n, d) for n, d in all_providers.items()
                     if d.get("weeks_remaining", 0) < 0 or d.get("weekends_remaining", 0) < 0]
    if neg_providers:
        h.append('<h3>Negative Remaining (overworked in prior blocks)</h3>')
        h.append('<table><thead><tr><th>Provider</th><th>Wk Remaining</th>'
                 '<th>WE Remaining</th></tr></thead><tbody>')
        for pname, pd in sorted(neg_providers):
            h.append(f'<tr><td>{esc(pname)}</td>'
                     f'<td style="color:red">{pd["weeks_remaining"]}</td>'
                     f'<td style="color:red">{pd["weekends_remaining"]}</td></tr>')
        h.append('</tbody></table>')

    h.append('</div>')

    # ════════════════════════════════════════════════════════════════════════
    # TAB 5: Shortfalls & Violations
    # ════════════════════════════════════════════════════════════════════════
    h.append('<div class="tab-content" id="tab-5">')
    h.append('<h2>Shortfalls &amp; Violations</h2>')
    h.append('<p style="color:#666">All issues that need attention — site coverage gaps, '
             'provider utilization gaps, availability overrides, and long stretches.</p>')

    # --- 1. Site Coverage Gaps ---
    h.append('<h3>Site Coverage Gaps</h3>')
    h.append('<table><thead><tr><th>Site</th><th>Period</th><th>Week</th>'
             '<th>Need</th><th>Filled</th><th>Short</th></tr></thead><tbody>')
    total_site_gaps = 0
    for site in results["all_sites"]:
        sf = results["site_fill"][site]
        for i, fill in enumerate(sf["wk_fills"]):
            if fill < sf["wk_demand"]:
                short = sf["wk_demand"] - fill
                total_site_gaps += short
                h.append(f'<tr><td>{esc(site)}</td><td>Weekday</td><td>{i+1}</td>'
                         f'<td>{sf["wk_demand"]}</td><td>{fill}</td>'
                         f'<td><span class="badge badge-warn">{short}</span></td></tr>')
        for i, fill in enumerate(sf["we_fills"]):
            if fill < sf["we_demand"]:
                short = sf["we_demand"] - fill
                total_site_gaps += short
                h.append(f'<tr><td>{esc(site)}</td><td>Weekend</td><td>{i+1}</td>'
                         f'<td>{sf["we_demand"]}</td><td>{fill}</td>'
                         f'<td><span class="badge badge-warn">{short}</span></td></tr>')
    h.append('</tbody></table>')
    if total_site_gaps == 0:
        h.append('<p style="color:#2e7d32">All sites fully covered every week.</p>')
    else:
        h.append(f'<p><strong>Total site coverage gaps: {total_site_gaps} provider-periods</strong></p>')

    # --- 1b. Site Overfills ---
    h.append('<h3>Site Overfills</h3>')
    h.append('<table><thead><tr><th>Site</th><th>Period</th><th>Week</th>'
             '<th>Demand</th><th>Filled</th><th>Over</th></tr></thead><tbody>')
    total_overfills = 0
    for site in results["all_sites"]:
        sf = results["site_fill"][site]
        for i, fill in enumerate(sf["wk_fills"]):
            if fill > sf["wk_demand"]:
                over = fill - sf["wk_demand"]
                total_overfills += over
                h.append(f'<tr><td>{esc(site)}</td><td>Weekday</td><td>{i+1}</td>'
                         f'<td>{sf["wk_demand"]}</td><td>{fill}</td>'
                         f'<td><span class="badge badge-short">{over}</span></td></tr>')
        for i, fill in enumerate(sf["we_fills"]):
            if fill > sf["we_demand"]:
                over = fill - sf["we_demand"]
                total_overfills += over
                h.append(f'<tr><td>{esc(site)}</td><td>Weekend</td><td>{i+1}</td>'
                         f'<td>{sf["we_demand"]}</td><td>{fill}</td>'
                         f'<td><span class="badge badge-short">{over}</span></td></tr>')
    h.append('</tbody></table>')
    if total_overfills == 0:
        h.append('<p style="color:#2e7d32">No sites overscheduled.</p>')
    else:
        h.append(f'<p style="color:#c62828"><strong>Total overfills: {total_overfills} '
                 f'provider-periods over demand</strong></p>')

    # --- 2. Provider Utilization Gaps ---
    h.append('<h3>Provider Utilization Gaps (Contract Obligations)</h3>')
    h.append('<p>Providers who are not fully utilizing their contractual weeks/weekends.</p>')
    util_gap_rows = []
    for pname in sorted(eligible.keys()):
        pd = eligible[pname]
        wkr = math.ceil(pd["weeks_remaining"])
        wer = math.ceil(pd["weekends_remaining"])
        wku = prov_week_count.get(pname, 0)
        weu = prov_we_count.get(pname, 0)
        wk_gap = max(0, wkr - wku)
        we_gap = max(0, wer - weu)
        if wk_gap > 0 or we_gap > 0:
            util_gap_rows.append((pname, wku, wkr, wk_gap, weu, wer, we_gap))

    if util_gap_rows:
        h.append(f'<table><thead><tr><th>Provider</th><th>Wk Assigned</th><th>Wk Owed</th>'
                 f'<th>Wk Gap</th><th>WE Assigned</th><th>WE Owed</th><th>WE Gap</th>'
                 f'<th>Total Gap</th></tr></thead><tbody>')
        for pname, wku, wkr, wkg, weu, wer, weg in sorted(util_gap_rows, key=lambda x: -(x[3]+x[6])):
            tg = wkg + weg
            cls = ' style="background:#ffebee"' if tg >= 3 else ''
            h.append(f'<tr{cls}><td><a class="prov-link" href="#{prov_id(pname)}" '
                     f'onclick="showProvider(\'{prov_id(pname)}\')">{esc(pname)}</a></td>'
                     f'<td>{wku}</td><td>{wkr}</td>'
                     f'<td>{"<span class=\"badge badge-warn\">" + str(wkg) + "</span>" if wkg else "0"}</td>'
                     f'<td>{weu}</td><td>{wer}</td>'
                     f'<td>{"<span class=\"badge badge-warn\">" + str(weg) + "</span>" if weg else "0"}</td>'
                     f'<td><strong>{tg}</strong></td></tr>')
        h.append('</tbody></table>')
        total_util = sum(r[3]+r[6] for r in util_gap_rows)
        h.append(f'<p><strong>{len(util_gap_rows)} providers with gaps, '
                 f'{total_util} total unassigned periods</strong></p>')
    else:
        h.append('<p style="color:#2e7d32">All providers fully utilized.</p>')

    # --- 3. Availability Overrides ---
    forced = results.get("forced_overrides", [])
    n_excessive = sum(1 for _, _, r in forced if r == "excessive_time_off")
    n_demand = sum(1 for _, _, r in forced if r == "schedule_demand")
    h.append(f'<h3>Availability Request Overrides ({len(forced)})</h3>')
    h.append('<p>Providers assigned to periods where they requested time off. '
             'These overrides were needed to meet contractual obligations.</p>')
    if forced:
        h.append(f'<div style="margin-bottom:8px">'
                 f'<span class="badge badge-warn" style="margin-right:8px">'
                 f'{n_excessive} excessive time-off</span>'
                 f'<span class="badge badge-short" style="margin-right:8px">'
                 f'{n_demand} schedule demand</span></div>')
        h.append('<table><thead><tr><th>Provider</th><th>Period</th><th>Week</th>'
                 '<th>Dates</th><th>Reason</th></tr></thead><tbody>')
        for pname, pidx, reason in sorted(forced, key=lambda x: (x[0], x[1])):
            period = periods[pidx]
            reason_label = "Excessive Time-Off Request" if reason == "excessive_time_off" else "Schedule Demand"
            reason_cls = "badge-warn" if reason == "excessive_time_off" else "badge-short"
            h.append(f'<tr><td><a class="prov-link" href="#{prov_id(pname)}" '
                     f'onclick="showProvider(\'{prov_id(pname)}\')">{esc(pname)}</a></td>'
                     f'<td>{period["type"].title()}</td><td>{period["num"]}</td>'
                     f'<td>{period["dates"][0]} – {period["dates"][-1]}</td>'
                     f'<td><span class="badge {reason_cls}">{esc(reason_label)}</span></td></tr>')
        h.append('</tbody></table>')
    else:
        h.append('<p style="color:#2e7d32">No availability overrides needed.</p>')

    # --- 3b. Stretch Overrides ---
    stretch_overrides = results.get("forced_stretch_overrides", [])
    h.append(f'<h3>Consecutive Stretch Overrides ({len(stretch_overrides)})</h3>')
    h.append('<p>Providers assigned to periods that create &gt;7-day stretches (2+ consecutive '
             'active weeks). These overrides were needed to fulfill contractual obligations.</p>')
    if stretch_overrides:
        h.append('<table><thead><tr><th>Provider</th><th>Weeks</th>'
                 '<th>Run Length</th><th>~Max Days</th><th>Severity</th></tr></thead><tbody>')
        for pname, pidx, run_len in sorted(stretch_overrides, key=lambda x: (-x[2], x[0])):
            period = periods[pidx]
            mid_wk = period["num"]
            start_wk = mid_wk - run_len // 2
            end_wk = start_wk + run_len - 1
            max_days = 5 + 2 + (run_len - 1) * 7
            if max_days <= 21:
                sev = '<span class="badge badge-warn">~{} days</span>'.format(max_days)
            else:
                sev = '<span class="badge" style="background:#f44336;color:white">~{} days</span>'.format(max_days)
            h.append(f'<tr><td><a class="prov-link" href="#{prov_id(pname)}" '
                     f'onclick="showProvider(\'{prov_id(pname)}\')">{esc(pname)}</a></td>'
                     f'<td>{start_wk}&ndash;{end_wk}</td>'
                     f'<td>{run_len} consecutive</td><td>~{max_days}</td>'
                     f'<td>{sev}</td></tr>')
        h.append('</tbody></table>')
    else:
        h.append('<p style="color:#2e7d32">No consecutive stretch overrides needed.</p>')

    # --- 4. Availability vs Obligation Conflicts ---
    h.append('<h3>Availability vs Obligation Conflicts</h3>')
    h.append('<p>Providers who have requested too many unavailable days relative to their '
             'remaining obligations. They may need to reduce their time-off requests.</p>')

    conflict_rows = []
    for pname in sorted(eligible.keys()):
        pd = eligible[pname]
        wkr = math.ceil(pd["weeks_remaining"])
        wer = math.ceil(pd["weekends_remaining"])
        total_periods_owed = wkr + wer
        if total_periods_owed <= 0:
            continue

        # Count unavailable weeks
        jname = name_map.get(pname)
        unavail = unavailable_dates.get(jname, set()) if jname else set()
        # Count how many week_nums have ANY unavailable day
        unavail_week_nums = set()
        for idx2, period in enumerate(periods):
            for d in period["dates"]:
                if d in unavail:
                    unavail_week_nums.add(period["num"])
                    break

        available_weeks = 17 - len(unavail_week_nums)
        # If available weeks < owed periods, there's a conflict
        if available_weeks < wkr:
            conflict_rows.append((pname, wkr, wer, available_weeks, len(unavail_week_nums)))

    if conflict_rows:
        h.append('<table><thead><tr><th>Provider</th><th>Weeks Owed</th>'
                 '<th>Weekends Owed</th><th>Available Weeks</th>'
                 '<th>Unavail Weeks</th><th>Issue</th></tr></thead><tbody>')
        for pn, wkr, wer, avail_wks, unavail_wks in sorted(conflict_rows, key=lambda x: -(x[1]-x[3])):
            deficit = wkr - avail_wks
            h.append(f'<tr style="background:#fff3e0"><td><a class="prov-link" '
                     f'href="#{prov_id(pn)}" onclick="showProvider(\'{prov_id(pn)}\')">'
                     f'{esc(pn)}</a></td>'
                     f'<td>{wkr}</td><td>{wer}</td><td>{avail_wks}</td>'
                     f'<td>{unavail_wks}</td>'
                     f'<td><span class="badge badge-warn">Needs {deficit} more weeks</span></td></tr>')
        h.append('</tbody></table>')
    else:
        h.append('<p style="color:#2e7d32">No availability/obligation conflicts.</p>')

    # --- 6. Summary ---
    h.append('<h3>Summary</h3>')
    h.append('<table class="summary-table"><tbody>')
    h.append(f'<tr><td>Total site coverage gaps</td><td><strong>{total_site_gaps}</strong></td></tr>')
    total_util_gaps = sum(r[3]+r[6] for r in util_gap_rows) if util_gap_rows else 0
    h.append(f'<tr><td>Total provider utilization gaps</td><td><strong>{total_util_gaps}</strong></td></tr>')
    h.append(f'<tr><td>Availability overrides</td><td><strong>{len(forced)}</strong>'
             f' ({n_excessive} excessive time-off, {n_demand} schedule demand)</td></tr>')
    h.append(f'<tr><td>Stretch overrides (3+ consecutive weeks)</td>'
             f'<td><strong>{len(stretch_overrides)}</strong></td></tr>')
    h.append(f'<tr><td>Availability/obligation conflicts</td>'
             f'<td><strong>{len(conflict_rows)}</strong></td></tr>')
    rebal = results.get("rebalance_moves", 0)
    level = results.get("level_moves", 0)
    h.append(f'<tr><td>Rebalancing moves made</td><td><strong>{rebal}</strong></td></tr>')
    h.append(f'<tr><td>Gap-leveling redistribution moves</td><td><strong>{level}</strong></td></tr>')
    h.append('</tbody></table>')

    h.append('</div>')

    # ════════════════════════════════════════════════════════════════════════
    # TAB 6: Open Shifts & Under Utilization
    # ════════════════════════════════════════════════════════════════════════
    h.append('<div class="tab-content" id="tab-6">')
    h.append('<h2>Open Shifts &amp; Under Utilization</h2>')
    h.append('<p style="color:#666">Dedicated view of unfilled shifts and providers who could not be '
             'fully scheduled. Use this to identify manual intervention opportunities.</p>')

    # --- Open Shifts by Week ---
    h.append('<h3>Open Shifts by Week</h3>')
    h.append('<p>Periods where sites have fewer providers assigned than demanded. '
             'These are shifts that need to be filled manually (locums, moonlighters, etc).</p>')

    # Build per-week open shifts data
    total_open_wk = 0
    total_open_we = 0
    max_open_wk = 0
    max_open_we = 0
    open_shifts_data = []
    for idx2, period in enumerate(periods):
        ptype = period["type"]
        wk_num = period["num"]
        dtype = "weekday" if ptype == "week" else "weekend"
        period_opens = []
        for site in all_sites:
            demand = sites_demand.get((site, dtype), 0)
            filled = sum(1 for _, s in period_assignments.get(idx2, []) if s == site)
            if filled < demand:
                period_opens.append((site, demand, filled, demand - filled))
        if period_opens:
            open_shifts_data.append((idx2, period, period_opens))

    if open_shifts_data:
        # Summary stats
        total_open_wk = 0
        total_open_we = 0
        max_open_wk = 0
        max_open_we = 0
        for idx2, period, opens in open_shifts_data:
            period_total = sum(o[3] for o in opens)
            if period["type"] == "week":
                total_open_wk += period_total
                max_open_wk = max(max_open_wk, period_total)
            else:
                total_open_we += period_total
                max_open_we = max(max_open_we, period_total)

        h.append('<div class="stats-grid">')
        h.append(f'<div class="stat-card"><div class="stat-value">{total_open_wk}</div>'
                 f'<div class="stat-label">Total Open Weekday Shifts</div></div>')
        h.append(f'<div class="stat-card"><div class="stat-value">{total_open_we}</div>'
                 f'<div class="stat-label">Total Open Weekend Shifts</div></div>')
        h.append(f'<div class="stat-card"><div class="stat-value">{max_open_wk}</div>'
                 f'<div class="stat-label">Max Weekday Short (1 week)</div></div>')
        h.append(f'<div class="stat-card"><div class="stat-value">{max_open_we}</div>'
                 f'<div class="stat-label">Max Weekend Short (1 week)</div></div>')
        h.append('</div>')

        h.append('<table><thead><tr><th>Period</th><th>Week</th><th>Dates</th>'
                 '<th>Site</th><th>Demand</th><th>Filled</th><th>Open</th>'
                 '</tr></thead><tbody>')
        for idx2, period, opens in open_shifts_data:
            d0 = datetime.strptime(period["dates"][0], "%Y-%m-%d").strftime("%m/%d")
            d1 = datetime.strptime(period["dates"][-1], "%Y-%m-%d").strftime("%m/%d")
            ptype_label = "WE" if period["type"] == "weekend" else "Wk"
            row_cls = "weekend-row" if period["type"] == "weekend" else ""
            for i, (site, demand, filled, short) in enumerate(opens):
                color = SITE_COLORS.get(site, "#666")
                sn = SITE_SHORT.get(site, site)
                # Only show period info on first row for this period
                if i == 0:
                    h.append(f'<tr class="{row_cls}">'
                             f'<td rowspan="{len(opens)}">{ptype_label}</td>'
                             f'<td rowspan="{len(opens)}">{period["num"]}</td>'
                             f'<td rowspan="{len(opens)}">{d0}&ndash;{d1}</td>'
                             f'<td><span class="site-badge" style="background:{color}">{esc(sn)}</span></td>'
                             f'<td style="text-align:center">{demand}</td>'
                             f'<td style="text-align:center">{filled}</td>'
                             f'<td style="text-align:center"><span class="badge badge-short">{short}</span></td></tr>')
                else:
                    h.append(f'<tr class="{row_cls}">'
                             f'<td><span class="site-badge" style="background:{color}">{esc(sn)}</span></td>'
                             f'<td style="text-align:center">{demand}</td>'
                             f'<td style="text-align:center">{filled}</td>'
                             f'<td style="text-align:center"><span class="badge badge-short">{short}</span></td></tr>')
        h.append('</tbody></table>')
    else:
        h.append('<p style="color:#2e7d32">No open shifts — all sites fully covered.</p>')

    # --- Under-Utilized Providers ---
    h.append('<h3>Under-Utilized Providers</h3>')
    h.append('<p>Providers with remaining contractual obligations that the engine could not '
             'schedule. These providers need manual assignment or schedule negotiation.</p>')

    under_util_detail = []
    for pname in sorted(eligible.keys()):
        pd = eligible[pname]
        wkr = math.ceil(pd["weeks_remaining"])
        wer = math.ceil(pd["weekends_remaining"])
        wku = prov_week_count.get(pname, 0)
        weu = prov_we_count.get(pname, 0)
        wk_gap = max(0, wkr - wku)
        we_gap = max(0, wer - weu)
        if wk_gap > 0 or we_gap > 0:
            # Determine WHY they couldn't be scheduled
            esites = get_eligible_sites_for_report(pname, pd, tags_data)
            jname = name_map.get(pname)
            unavail = unavailable_dates.get(jname, set()) if jname else set()
            unavail_week_nums = set()
            for idx2, period in enumerate(periods):
                for d in period["dates"]:
                    if d in unavail:
                        unavail_week_nums.add(period["num"])
                        break
            avail_wks = results["n_weeks"] - len(unavail_week_nums)

            # Classify reason
            reasons = []
            if avail_wks < wkr:
                reasons.append("excessive_time_off")
            # Check if all their sites are consistently full
            sites_full_count = 0
            for site in esites:
                sf_data = site_fill.get(site, {})
                wk_fills = sf_data.get("wk_fills", [])
                wk_demand = sf_data.get("wk_demand", 0)
                if wk_fills and all(f >= wk_demand for f in wk_fills):
                    sites_full_count += 1
            if sites_full_count == len(esites) and esites:
                reasons.append("all_sites_full")
            if not reasons:
                reasons.append("scheduling_constraint")

            under_util_detail.append({
                "name": pname,
                "wk_used": wku, "wk_owed": wkr, "wk_gap": wk_gap,
                "we_used": weu, "we_owed": wer, "we_gap": we_gap,
                "sites": esites,
                "avail_wks": avail_wks,
                "unavail_wks": len(unavail_week_nums),
                "reasons": reasons,
            })

    if under_util_detail:
        # Sort by total gap descending
        under_util_detail.sort(key=lambda x: -(x["wk_gap"] + x["we_gap"]))

        h.append(f'<div style="margin-bottom:12px"><strong>{len(under_util_detail)} providers</strong> '
                 f'with a combined <strong>{sum(u["wk_gap"] + u["we_gap"] for u in under_util_detail)} '
                 f'unassigned periods</strong></div>')

        h.append('<table><thead><tr><th>Provider</th><th>Wk Assigned</th><th>Wk Owed</th>'
                 '<th>Wk Gap</th><th>WE Assigned</th><th>WE Owed</th><th>WE Gap</th>'
                 '<th>Eligible Sites</th><th>Avail Wks</th><th>Likely Reason</th>'
                 '</tr></thead><tbody>')
        for u in under_util_detail:
            pname = u["name"]
            pid = prov_id(pname)
            total_gap = u["wk_gap"] + u["we_gap"]
            cls = ' style="background:#ffebee"' if total_gap >= 3 else ''

            # Format reasons
            reason_badges = []
            for r in u["reasons"]:
                if r == "excessive_time_off":
                    reason_badges.append('<span class="badge badge-warn">Excessive Time-Off</span>')
                elif r == "all_sites_full":
                    reason_badges.append('<span class="badge badge-short">Sites Full</span>')
                else:
                    reason_badges.append('<span class="badge" style="background:#e8eaf6;color:#333">'
                                        'Scheduling Constraint</span>')

            site_badges = " ".join(
                f'<span class="site-badge" style="background:{SITE_COLORS.get(s, "#666")}">'
                f'{esc(SITE_SHORT.get(s, s))}</span>'
                for s in u["sites"]
            )

            h.append(f'<tr{cls}><td><a class="prov-link" href="#{pid}" '
                     f'onclick="showProvider(\'{pid}\')">{esc(pname)}</a></td>'
                     f'<td style="text-align:center">{u["wk_used"]}</td>'
                     f'<td style="text-align:center">{u["wk_owed"]}</td>'
                     f'<td style="text-align:center">'
                     f'{"<span class=\"badge badge-warn\">" + str(u["wk_gap"]) + "</span>" if u["wk_gap"] else "0"}'
                     f'</td>'
                     f'<td style="text-align:center">{u["we_used"]}</td>'
                     f'<td style="text-align:center">{u["we_owed"]}</td>'
                     f'<td style="text-align:center">'
                     f'{"<span class=\"badge badge-warn\">" + str(u["we_gap"]) + "</span>" if u["we_gap"] else "0"}'
                     f'</td>'
                     f'<td>{site_badges}</td>'
                     f'<td style="text-align:center">{u["avail_wks"]}/{results["n_weeks"]}</td>'
                     f'<td>{" ".join(reason_badges)}</td></tr>')
        h.append('</tbody></table>')

        # Reason breakdown
        n_eto = sum(1 for u in under_util_detail if "excessive_time_off" in u["reasons"])
        n_full = sum(1 for u in under_util_detail if "all_sites_full" in u["reasons"])
        n_constraint = sum(1 for u in under_util_detail if "scheduling_constraint" in u["reasons"])
        h.append('<h3>Reason Breakdown</h3>')
        h.append('<table><tbody>')
        h.append(f'<tr><td><span class="badge badge-warn">Excessive Time-Off</span></td>'
                 f'<td>{n_eto} providers requested more time off than their remaining obligations allow</td></tr>')
        h.append(f'<tr><td><span class="badge badge-short">Sites Full</span></td>'
                 f'<td>{n_full} providers\' eligible sites are at capacity every week</td></tr>')
        h.append(f'<tr><td><span class="badge" style="background:#e8eaf6;color:#333">'
                 f'Scheduling Constraint</span></td>'
                 f'<td>{n_constraint} providers blocked by consecutive-week limits or other constraints</td></tr>')
        h.append('</tbody></table>')

        # Recommendations
        h.append('<h3>Recommended Actions</h3>')
        h.append('<ul style="margin-left:20px;line-height:1.8">')
        if n_eto > 0:
            h.append('<li><strong>Excessive Time-Off:</strong> Review these providers\' time-off requests. '
                     'They have more unavailable weeks than their contract allows. Consider discussing '
                     'schedule adjustments.</li>')
        if n_full > 0:
            h.append('<li><strong>Sites Full:</strong> These providers only work at sites that are fully '
                     'staffed. Consider cross-training, temporary site reassignment, or reducing demand.</li>')
        if total_open_wk + total_open_we > 0:
            h.append(f'<li><strong>Open Shifts:</strong> {total_open_wk + total_open_we} shifts need coverage. '
                     f'Locums, moonlighters, or schedule swaps can fill these gaps.</li>')
        h.append('</ul>')
    else:
        h.append('<p style="color:#2e7d32">All providers fully utilized — no scheduling gaps.</p>')

    h.append('</div>')

    # ════════════════════════════════════════════════════════════════════════
    # JavaScript
    # ════════════════════════════════════════════════════════════════════════
    h.append("""
<button class="back-top" onclick="window.scrollTo({top:0,behavior:'smooth'})" id="backTop">&#8679;</button>

<script>
// Tab switching
function switchTab(idx) {
  document.querySelectorAll('.tab').forEach((t,i) => t.classList.toggle('active', i===idx));
  document.querySelectorAll('.tab-content').forEach((c,i) => c.classList.toggle('active', i===idx));
  window.scrollTo({top: 0});
}

// Provider card toggle
function toggleCard(header) {
  header.parentElement.classList.toggle('open');
}

// Show provider (switch to providers tab, open card, scroll)
function showProvider(pid) {
  switchTab(1);
  setTimeout(() => {
    const card = document.getElementById(pid);
    if (card) {
      card.classList.add('open');
      card.scrollIntoView({behavior: 'smooth', block: 'start'});
    }
  }, 100);
}

// Filter providers
function filterProviders() {
  const search = document.getElementById('prov-search').value.toLowerCase();
  const siteFilter = document.getElementById('prov-site-filter').value;
  const statusFilter = document.getElementById('prov-status-filter').value;

  document.querySelectorAll('.prov-card').forEach(card => {
    const name = card.dataset.name || '';
    const sites = card.dataset.sites || '';
    const status = card.dataset.status || '';

    let show = true;
    if (search && !name.includes(search)) show = false;
    if (siteFilter !== 'all' && !sites.includes(siteFilter)) show = false;
    if (statusFilter !== 'all' && !status.includes(statusFilter)) show = false;

    card.style.display = show ? '' : 'none';
  });
}

// Site column filter in calendar
function filterSite(site, btn) {
  // Toggle button states
  document.querySelectorAll('.site-filter-btn').forEach(b => {
    b.classList.remove('active');
    b.style.background = 'white';
    b.style.color = b.dataset.color || '#333';
  });
  btn.classList.add('active');
  btn.style.background = btn.dataset.color || '#333';
  btn.style.color = 'white';

  document.querySelectorAll('.site-col').forEach(col => {
    if (site === 'all') {
      col.style.display = '';
    } else {
      col.style.display = col.dataset.site === site ? '' : 'none';
    }
  });
}

// Back to top button
window.addEventListener('scroll', () => {
  document.getElementById('backTop').style.display = window.scrollY > 300 ? 'block' : 'none';
});
</script>
""")

    h.append('</div></body></html>')

    return "\n".join(h)


def get_eligible_sites_for_report(provider_name, provider_data, tags_data):
    """Simple site eligibility for display (no flex)."""
    sites = []
    pct_fields = {
        "pct_cooper": ["Cooper"], "pct_inspira_veb": ["Vineland", "Elmer"],
        "pct_inspira_mhw": ["Mullica Hill"], "pct_mannington": ["Mannington"],
        "pct_virtua": ["Virtua Voorhees", "Virtua Marlton", "Virtua Willingboro", "Virtua Mt Holly"],
        "pct_cape": ["Cape"],
    }
    for pf, sl in pct_fields.items():
        if provider_data.get(pf, 0) > 0:
            sites.extend(sl)
    ptags = tags_data.get(provider_name, [])
    for t in ptags:
        if t["tag"] == "no_elmer":
            sites = [s for s in sites if s != "Elmer"]
        if t["tag"] == "no_vineland":
            sites = [s for s in sites if s != "Vineland"]
    return sites


def _common_css():
    """Shared CSS for all report pages."""
    return """
:root {
  --bg: #ffffff; --text: #1a1a1a; --heading: #0d47a1;
  --border: #d0d0d0; --stripe: #f5f7fa; --link: #1565c0;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 14px; color: var(--text); background: var(--bg); line-height: 1.5;
}
.container { max-width: 1200px; margin: 0 auto; padding: 24px 32px; }
h1 { font-size: 24px; color: var(--heading); margin-bottom: 4px; }
h2 { font-size: 18px; color: var(--heading); margin: 28px 0 10px; border-bottom: 2px solid var(--heading); padding-bottom: 4px; }
h3 { font-size: 15px; color: #333; margin: 18px 0 8px; }
.subtitle { color: #666; font-size: 13px; margin-bottom: 20px; }
table { border-collapse: collapse; width: 100%; margin-bottom: 16px; }
th, td { border: 1px solid var(--border); padding: 6px 10px; text-align: left; }
th { background: #e8eaf6; font-weight: 600; font-size: 13px; }
tr:nth-child(even) { background: var(--stripe); }
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }
.nav { background: #e8eaf6; padding: 10px 20px; margin-bottom: 20px; border-radius: 6px; }
.nav a { margin-right: 16px; font-weight: 500; font-size: 13px; }
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 3px;
  font-size: 11px; font-weight: 600; margin: 1px 2px;
}
.badge-ok { background: #e8f5e9; color: #2e7d32; }
.badge-warn { background: #fff3e0; color: #e65100; }
.badge-short { background: #fce4ec; color: #b71c1c; }
.card { background: #f5f7fa; border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 12px; }
.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 16px 0; }
.stat-card { background: #f5f7fa; border: 1px solid var(--border); border-radius: 8px; padding: 12px; text-align: center; }
.stat-value { font-size: 28px; font-weight: 700; color: var(--heading); }
.stat-label { font-size: 11px; color: #666; margin-top: 2px; }
"""


def _nav_bar(active=""):
    """Shared navigation bar across all pages."""
    links = [
        ("index.html", "Index"),
        ("rules.html", "Rules"),
        ("inputs.html", "Inputs"),
    ]
    for i in range(1, 6):
        links.append((f"block_schedule_report_v{i}.html", f"Schedule v{i}"))

    parts = ['<div class="nav">']
    for href, label in links:
        if label == active:
            parts.append(f'<a href="{href}" style="font-weight:700;color:#0d47a1">{label}</a>')
        else:
            parts.append(f'<a href="{href}">{label}</a>')
    parts.append('</div>')
    return "\n".join(parts)


def generate_rules_page():
    """Generate the scheduling rules reference page."""
    h = []
    h.append(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Scheduling Rules</title>
<style>{_common_css()}
.rule {{ background: #f5f7fa; border-left: 4px solid var(--heading); padding: 10px 14px; margin: 8px 0; border-radius: 0 6px 6px 0; }}
.rule-num {{ font-weight: 700; color: var(--heading); margin-right: 8px; }}
</style></head><body><div class="container">
{_nav_bar("Rules")}
<h1>Block 3 Scheduling Rules</h1>
<p class="subtitle">Reference guide for the scheduling engine constraints and logic.</p>
""")

    rules = [
        ("Site Assignment", [
            ("Providers are only assigned to sites where they have a non-zero allocation percentage.",),
            ("Smaller sites are filled first (to capacity), then Cooper absorbs the remainder.",),
            ("Cooper's full demand (26 weekday / 19 weekend) is the target. Shortfalls are expected — moonlighters fill the gap.",),
            ("Providers stay at one site for a complete week+weekend stretch when possible.",),
        ]),
        ("Scheduling Constraints", [
            ("No more than 2 consecutive active weeks (hard cap). A 3rd consecutive week would create a 19+ day stretch.",),
            ("12-day stretches (2 consecutive weeks) are penalized but allowed when obligations require it.",),
            ("Weekend assignments prefer pairing with the same site as the weekday assignment for the same week number.",),
            ("Standalone weekends in back-to-back weeks are discouraged.",),
        ]),
        ("Availability & Time Off", [
            ("All provider schedule requests (time off) are honored — never overridden.",),
            ("If honoring requests means a provider cannot be fully scheduled, this is documented with the reason.",),
            ("Providers are never assigned to weeks/weekends where they are marked unavailable.",),
        ]),
        ("Capacity & Obligations", [
            ("Each provider owes a specific number of weeks and weekends (from their contract, minus prior blocks).",),
            ("Remaining obligations are rounded UP to the next whole number (e.g., 14.6 weeks = 15 weeks owed).",),
            ("Night-shift-only providers are excluded. Split providers (nights + days) are scheduled for their day obligations.",),
        ]),
        ("Distribution & Fairness", [
            ("Assignments are distributed evenly across the full block — no front-loading.",),
            ("Week processing order is randomized to prevent early-block bias.",),
            ("Ideal spacing is calculated per provider to evenly spread their assignments.",),
            ("Rebalancing passes attempt to fill contractual gaps after the initial assignment.",),
            ("Gap-leveling smooths out per-site fill counts across weeks (move from surplus to shortfall weeks).",),
            ("Cross-site gap fill places remaining unassigned providers into the neediest site+period combinations.",),
        ]),
    ]

    for section, items in rules:
        h.append(f'<h2>{esc(section)}</h2>')
        for i, (rule,) in enumerate(items, 1):
            h.append(f'<div class="rule"><span class="rule-num">{i}.</span>{esc(rule)}</div>')

    h.append('</div></body></html>')
    return "\n".join(h)


def generate_inputs_page(results):
    """Generate the provider inputs page showing prior blocks and remaining obligations."""
    eligible = results["eligible_providers"]
    all_providers = results["all_providers"]
    tags_data = results["tags_data"]
    n_weeks = results["n_weeks"]
    n_weekends = results["n_weekends"]

    h = []
    h.append(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Provider Inputs</title>
<style>{_common_css()}
input[type=text] {{ padding: 6px 10px; border: 1px solid var(--border); border-radius: 4px; width: 250px; font-size: 13px; }}
.flag-high {{ background: #fce4ec !important; color: #b71c1c; font-weight: 600; }}
.row-flagged {{ background: #fff8f8 !important; }}
tr.row-flagged:nth-child(even) {{ background: #fff0f0 !important; }}
</style></head><body><div class="container">
{_nav_bar("Inputs")}
<h1>Provider Inputs &amp; Prior Block Data</h1>
<p class="subtitle">Block 3: {BLOCK_START.strftime('%b %d')} &ndash; {BLOCK_END.strftime('%b %d, %Y')} &mdash;
{n_weeks} weeks, {n_weekends} weekends</p>
""")

    # Summary stats
    total_wk_owed = sum(math.ceil(p["weeks_remaining"]) for p in eligible.values())
    total_we_owed = sum(math.ceil(p["weekends_remaining"]) for p in eligible.values())
    nights_only = sum(1 for p in all_providers.values() if p["shift_type"] == "Nights"
                      and p["weeks_remaining"] <= 0 and p["weekends_remaining"] <= 0)
    split_prov = sum(1 for p in all_providers.values() if p["shift_type"] == "Nights"
                     and (p["weeks_remaining"] > 0 or p["weekends_remaining"] > 0))

    h.append('<div class="stats-grid">')
    h.append(f'<div class="stat-card"><div class="stat-value">{len(eligible)}</div>'
             f'<div class="stat-label">Eligible Providers</div></div>')
    h.append(f'<div class="stat-card"><div class="stat-value">{total_wk_owed}</div>'
             f'<div class="stat-label">Total Weeks Owed</div></div>')
    h.append(f'<div class="stat-card"><div class="stat-value">{total_we_owed}</div>'
             f'<div class="stat-label">Total Weekends Owed</div></div>')
    h.append(f'<div class="stat-card"><div class="stat-value">{nights_only}</div>'
             f'<div class="stat-label">Night-Only (excluded)</div></div>')
    h.append(f'<div class="stat-card"><div class="stat-value">{split_prov}</div>'
             f'<div class="stat-label">Split Night/Day</div></div>')
    h.append('</div>')

    # Count flagged providers (>33% remaining)
    n_flagged_wk = sum(1 for p in eligible.values()
                       if p["annual_weeks"] > 0 and p["weeks_remaining"] / p["annual_weeks"] > 0.33)
    n_flagged_we = sum(1 for p in eligible.values()
                       if p["annual_weekends"] > 0 and p["weekends_remaining"] / p["annual_weekends"] > 0.33)
    h.append(f'<div class="stat-card" style="display:inline-block;margin-right:12px">'
             f'<div class="stat-value" style="color:#b71c1c">{n_flagged_wk}</div>'
             f'<div class="stat-label">&gt;33% Weeks Remaining</div></div>')
    h.append(f'<div class="stat-card" style="display:inline-block">'
             f'<div class="stat-value" style="color:#b71c1c">{n_flagged_we}</div>'
             f'<div class="stat-label">&gt;33% Weekends Remaining</div></div>')

    # Provider table
    h.append('<h2>Provider Obligations</h2>')
    h.append('<p style="margin-bottom:8px">Search: <input type="text" id="provSearch" onkeyup="filterTable()" '
             'placeholder="Filter by provider name..."></p>')
    h.append('<p style="color:#666;font-size:12px;margin-bottom:8px">'
             '<span class="flag-high" style="padding:2px 6px;border-radius:3px">Highlighted cells</span> '
             'indicate &gt;33% of annual obligation still remaining — these providers are behind pace.</p>')
    h.append('<table id="provTable"><thead><tr>'
             '<th>Provider</th><th>Shift Type</th><th>FTE</th>'
             '<th>Annual Wks</th><th>Annual WEs</th>'
             '<th>Wks Remaining</th><th>WEs Remaining</th>'
             '<th>Wks Owed (ceil)</th><th>WEs Owed (ceil)</th>'
             '<th>Eligible Sites</th>'
             '</tr></thead><tbody>')

    for pname in sorted(eligible.keys()):
        pdata = eligible[pname]
        wk_rem = pdata["weeks_remaining"]
        we_rem = pdata["weekends_remaining"]
        wk_ceil = math.ceil(wk_rem)
        we_ceil = math.ceil(we_rem)
        esites = get_eligible_sites_for_report(pname, pdata, tags_data)
        site_str = ", ".join(esites) if esites else "<em>none</em>"

        # Flag providers with >33% of their annual obligation remaining
        ann_wk = pdata["annual_weeks"]
        ann_we = pdata["annual_weekends"]
        wk_pct_rem = (wk_rem / ann_wk * 100) if ann_wk > 0 else 0
        we_pct_rem = (we_rem / ann_we * 100) if ann_we > 0 else 0
        wk_high = wk_pct_rem > 33 and ann_wk > 0
        we_high = we_pct_rem > 33 and ann_we > 0

        wk_rem_cls = ' class="flag-high"' if wk_high else ''
        we_rem_cls = ' class="flag-high"' if we_high else ''
        wk_ceil_cls = ' class="flag-high"' if wk_high else ''
        we_ceil_cls = ' class="flag-high"' if we_high else ''
        row_cls = ' class="row-flagged"' if (wk_high or we_high) else ''

        h.append(f'<tr{row_cls}>'
                 f'<td>{esc(pname)}</td>'
                 f'<td>{esc(pdata["shift_type"])}</td>'
                 f'<td>{pdata["fte"]:.2f}</td>'
                 f'<td style="text-align:center">{ann_wk:.1f}</td>'
                 f'<td style="text-align:center">{ann_we:.1f}</td>'
                 f'<td style="text-align:center"{wk_rem_cls}>{wk_rem:.2f}'
                 f'{"  (" + str(int(wk_pct_rem)) + "%)" if wk_high else ""}</td>'
                 f'<td style="text-align:center"{we_rem_cls}>{we_rem:.2f}'
                 f'{"  (" + str(int(we_pct_rem)) + "%)" if we_high else ""}</td>'
                 f'<td style="text-align:center"{wk_ceil_cls}>{wk_ceil}</td>'
                 f'<td style="text-align:center"{we_ceil_cls}>{we_ceil}</td>'
                 f'<td>{site_str}</td>'
                 f'</tr>')

    h.append('</tbody></table>')

    # Excluded providers (nights-only)
    h.append('<h2>Excluded Providers (Night-Only)</h2>')
    h.append('<p>These providers have shift_type "Nights" with no remaining weeks/weekends. '
             'They are not scheduled in the day-shift engine.</p>')
    night_provs = [(n, p) for n, p in all_providers.items()
                   if p["shift_type"] == "Nights" and p["weeks_remaining"] <= 0
                   and p["weekends_remaining"] <= 0]
    if night_provs:
        h.append('<table><thead><tr><th>Provider</th><th>FTE</th><th>Nights Remaining</th>'
                 '</tr></thead><tbody>')
        for pname, pdata in sorted(night_provs):
            h.append(f'<tr><td>{esc(pname)}</td><td>{pdata["fte"]:.2f}</td>'
                     f'<td>{pdata.get("nights_remaining", 0):.1f}</td></tr>')
        h.append('</tbody></table>')
    else:
        h.append('<p>No night-only providers found.</p>')

    h.append("""
<script>
function filterTable() {
  var input = document.getElementById('provSearch').value.toUpperCase();
  var rows = document.getElementById('provTable').getElementsByTagName('tr');
  for (var i = 1; i < rows.length; i++) {
    var name = rows[i].cells[0].textContent.toUpperCase();
    rows[i].style.display = name.indexOf(input) > -1 ? '' : 'none';
  }
}
</script>
""")
    h.append('</div></body></html>')
    return "\n".join(h)


def generate_index_page(variation_stats):
    """Generate the index page linking all reports together."""
    h = []
    h.append(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Block 3 Schedule - Index</title>
<style>{_common_css()}
.var-card {{
  background: #f5f7fa; border: 1px solid var(--border); border-radius: 8px;
  padding: 16px 20px; margin-bottom: 12px; display: flex; justify-content: space-between;
  align-items: center;
}}
.var-card:hover {{ background: #e3f2fd; }}
.var-title {{ font-size: 16px; font-weight: 600; color: var(--heading); }}
.var-stats {{ font-size: 12px; color: #666; margin-top: 4px; }}
.var-link {{ font-size: 14px; font-weight: 600; }}
</style></head><body><div class="container">
{_nav_bar("Index")}
<h1>Block 3 Schedule Report</h1>
<p class="subtitle">{BLOCK_START.strftime('%B %d')} &ndash; {BLOCK_END.strftime('%B %d, %Y')}</p>
""")

    h.append('<h2>Reference Pages</h2>')
    h.append('<div class="var-card">'
             '<div><div class="var-title">Scheduling Rules</div>'
             '<div class="var-stats">All constraints, policies, and logic used by the engine</div></div>'
             '<a class="var-link" href="rules.html">View Rules &rarr;</a></div>')
    h.append('<div class="var-card">'
             '<div><div class="var-title">Provider Inputs</div>'
             '<div class="var-stats">Prior block data, remaining obligations, site eligibility</div></div>'
             '<a class="var-link" href="inputs.html">View Inputs &rarr;</a></div>')

    h.append('<h2>Schedule Variations</h2>')
    h.append('<p style="color:#666;margin-bottom:12px">Each variation uses a different random seed, '
             'producing a different assignment pattern while following the same rules.</p>')

    for i, stats in enumerate(variation_stats, 1):
        fname = f"block_schedule_report_v{i}.html"
        site_gaps = stats["site_gaps"]
        site_overfills = stats.get("site_overfills", 0)
        under = stats["under_util"]
        stretches = stats["stretch_overrides"]
        overfill_label = (f' &nbsp;|&nbsp; <span style="color:#c62828">Overfills: {site_overfills}</span>'
                          if site_overfills > 0 else ' &nbsp;|&nbsp; Overfills: 0')
        h.append(f'<div class="var-card">'
                 f'<div><div class="var-title">Variation {i} (seed={stats["seed"]})</div>'
                 f'<div class="var-stats">'
                 f'Site gaps: {site_gaps}{overfill_label} &nbsp;|&nbsp; '
                 f'Under-utilized: {under} &nbsp;|&nbsp; '
                 f'Stretch overrides: {stretches}'
                 f'</div></div>'
                 f'<a class="var-link" href="{fname}">View Schedule &rarr;</a></div>')

    h.append('</div></body></html>')
    return "\n".join(h)


if __name__ == "__main__":
    import sys

    n_variations = 5
    if len(sys.argv) > 1:
        try:
            n_variations = int(sys.argv[1])
        except ValueError:
            pass

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    seeds = [42, 7, 123, 256, 999]
    variation_stats = []
    first_results = None

    for v in range(n_variations):
        seed = seeds[v % len(seeds)]
        label = f"v{v+1}"
        print(f"\n{'='*60}")
        print(f"Running block schedule engine (seed={seed}) {label}...")
        print(f"{'='*60}")
        results = run_engine(seed=seed)
        if first_results is None:
            first_results = results

        print("Generating HTML report...")
        html_content = generate_html(results)

        fname = f"block_schedule_report_{label}.html"
        outpath = os.path.join(OUTPUT_DIR, fname)
        with open(outpath, "w") as f:
            f.write(html_content)

        # Summary stats for comparison
        sf = results["site_fill"]
        total_short = 0
        total_over = 0
        for site, data in sf.items():
            total_short += sum(max(0, data["wk_demand"] - f) for f in data["wk_fills"])
            total_short += sum(max(0, data["we_demand"] - f) for f in data["we_fills"])
            total_over += sum(max(0, f - data["wk_demand"]) for f in data["wk_fills"])
            total_over += sum(max(0, f - data["we_demand"]) for f in data["we_fills"])
        n_under = len(results["under_assigned"])
        n_stretches = len(results.get("forced_stretch_overrides", []))

        variation_stats.append({
            "seed": seed,
            "site_gaps": total_short,
            "site_overfills": total_over,
            "under_util": n_under,
            "stretch_overrides": n_stretches,
        })

        print(f"\n{label}: site gaps={total_short}, overfills={total_over}, "
              f"under-util={n_under}, stretches={n_stretches}")
        print(f"Saved to: {outpath}")

    # Generate supporting pages
    print("\nGenerating index, rules, and inputs pages...")

    # Rules page
    rules_html = generate_rules_page()
    rules_path = os.path.join(OUTPUT_DIR, "rules.html")
    with open(rules_path, "w") as f:
        f.write(rules_html)
    print(f"  Rules: {rules_path}")

    # Inputs page (uses first results for provider data)
    inputs_html = generate_inputs_page(first_results)
    inputs_path = os.path.join(OUTPUT_DIR, "inputs.html")
    with open(inputs_path, "w") as f:
        f.write(inputs_html)
    print(f"  Inputs: {inputs_path}")

    # Index page
    index_html = generate_index_page(variation_stats)
    index_path = os.path.join(OUTPUT_DIR, "index.html")
    with open(index_path, "w") as f:
        f.write(index_html)
    print(f"  Index: {index_path}")

    print(f"\nOpen: file://{os.path.abspath(index_path)}")
