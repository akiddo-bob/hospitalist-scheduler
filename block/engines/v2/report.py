#!/usr/bin/env python3
"""
HTML Report Generator for Block Schedule Engine v2.

Generates interactive HTML reports matching the archive report patterns:
  - Index page with variation cards and navigation
  - Rules reference page
  - Inputs summary page
  - Per-variation schedule reports with 7 tabs:
      0. Calendar — monthly grid, site columns, provider links
      1. Providers — collapsible detail cards, mini calendars
      2. Site Summary — per-site fill overview
      3. Utilization — provider utilization table
      4. Flags — over/under/stretch analysis
      5. Shortfalls — coverage gaps, conflicts
      6. Open Shifts — unfilled positions with reasons

Frozen: Do NOT modify this file after moving to v2.
"""

import calendar
import html as html_mod
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from block.engines.shared.loader import (
    SITE_COLORS, SITE_SHORT, SITE_PCT_MAP, SCHEDULES_DIR,
    load_availability,
)
from name_match import match_provider


# ─── Helpers ─────────────────────────────────────────────────────────────────

def esc(s):
    """HTML-escape a string."""
    return html_mod.escape(str(s))


def prov_id(name):
    """Create a safe HTML id from provider name."""
    return "prov-" + name.replace(" ", "-").replace(",", "").replace("'", "").replace(".", "")


# ─── Common CSS ──────────────────────────────────────────────────────────────

def _common_css():
    """Shared CSS for all report pages."""
    return """
:root {
  --bg: #ffffff; --text: #1a1a1a; --heading: #0d47a1;
  --border: #d0d0d0; --stripe: #f5f7fa; --link: #1565c0;
  --weekend-bg: #e3f2fd; --short-bg: #fce4ec; --short-text: #b71c1c;
  --ok-bg: #e8f5e9; --ok-text: #2e7d32; --flex-bg: #fff3e0;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 13px; color: var(--text); background: var(--bg); line-height: 1.4;
}
.container { max-width: 1600px; margin: 0 auto; padding: 16px 24px; }
h1 { font-size: 22px; color: var(--heading); margin-bottom: 4px; }
h2 { font-size: 17px; color: var(--heading); margin: 24px 0 8px; border-bottom: 2px solid var(--heading); padding-bottom: 4px; }
h3 { font-size: 14px; color: #333; margin: 16px 0 6px; }
.subtitle { color: #666; font-size: 13px; margin-bottom: 16px; }
.nav { background: #e8eaf6; padding: 10px 20px; margin-bottom: 20px; border-radius: 6px; }
.nav a { margin-right: 16px; font-weight: 500; font-size: 13px; color: var(--link); text-decoration: none; }
.nav a:hover { text-decoration: underline; }
.nav a.active { font-weight: 700; color: #0d47a1; }

/* Tabs */
.tabs { display: flex; gap: 0; border-bottom: 2px solid var(--heading); margin-bottom: 16px; flex-wrap: wrap; }
.tab { padding: 8px 16px; cursor: pointer; border: 1px solid var(--border); border-bottom: none; border-radius: 6px 6px 0 0; background: #f9f9f9; font-size: 13px; font-weight: 500; color: #555; margin-bottom: -2px; }
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

/* Badges */
.badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 11px; font-weight: 600; }
.badge-ok { background: var(--ok-bg); color: var(--ok-text); }
.badge-short { background: var(--short-bg); color: var(--short-text); }
.badge-warn { background: #fff3e0; color: #e65100; }
.site-badge { display: inline-block; padding: 2px 6px; border-radius: 3px; color: white; font-size: 11px; font-weight: 600; margin: 1px 2px; white-space: nowrap; }
a.prov-link { color: var(--link); text-decoration: none; cursor: pointer; font-size: 12px; display: block; padding: 1px 0; }
a.prov-link:hover { text-decoration: underline; }

/* Stats grid */
.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 16px 0; }
.stat-card { background: #f5f7fa; border: 1px solid var(--border); border-radius: 8px; padding: 12px; text-align: center; }
.stat-value { font-size: 28px; font-weight: 700; color: var(--heading); }
.stat-label { font-size: 11px; color: #666; margin-top: 2px; }

/* Cards */
.card { background: #f5f7fa; border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 12px; }
.var-card { background: #f5f7fa; border: 1px solid var(--border); border-radius: 8px; padding: 16px 20px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; }
.var-card:hover { background: #e3f2fd; }
.var-title { font-size: 16px; font-weight: 600; color: var(--heading); }
.var-stats { font-size: 12px; color: #666; margin-top: 4px; }
.var-link { font-size: 14px; font-weight: 600; color: var(--link); text-decoration: none; }

/* Provider detail cards */
.prov-card { border: 1px solid var(--border); border-radius: 8px; margin-bottom: 8px; overflow: hidden; }
.prov-header { padding: 8px 12px; background: #f5f7fa; cursor: pointer; display: flex; justify-content: space-between; align-items: center; }
.prov-header:hover { background: #e3f2fd; }
.prov-body { display: none; padding: 12px; border-top: 1px solid var(--border); }
.prov-card.open .prov-body { display: block; }
.prov-arrow { transition: transform 0.2s; }
.prov-card.open .prov-arrow { transform: rotate(90deg); }

/* Utilization bars */
.util-bar { width: 80px; height: 14px; background: #eee; border-radius: 3px; display: inline-block; vertical-align: middle; }
.util-fill { height: 100%; border-radius: 3px; }

/* Mini calendar */
.avail-months { display: flex; gap: 12px; flex-wrap: wrap; margin: 8px 0; }
.avail-month h4 { font-size: 11px; margin-bottom: 4px; text-align: center; }
.mini-cal { display: grid; grid-template-columns: repeat(7, 22px); gap: 1px; }
.dh { font-size: 9px; text-align: center; font-weight: 600; color: #999; }
.dc { width: 22px; height: 22px; font-size: 9px; text-align: center; line-height: 22px; border-radius: 2px; position: relative; }
.dc.empty { background: transparent; }
.dc.avail { background: #c8e6c9; }
.dc.unavail { background: #ffcdd2; }
.dc.blank { background: #fff9c4; }
.dn { font-size: 8px; }
.asg { position: absolute; top: 0; left: 0; right: 0; bottom: 0; border-radius: 2px; font-size: 7px; color: white; line-height: 22px; text-align: center; opacity: 0.85; }

/* Search */
.search-box { padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; width: 300px; margin-bottom: 12px; }

/* Rules */
.rule { background: #f5f7fa; border-left: 4px solid var(--heading); padding: 10px 14px; margin: 8px 0; border-radius: 0 6px 6px 0; }
.rule-num { font-weight: 700; color: var(--heading); margin-right: 8px; }

/* Filter buttons */
.filter-btn { padding: 4px 10px; border: 2px solid #ccc; border-radius: 4px; background: white; cursor: pointer; font-size: 11px; font-weight: 600; margin: 2px; }
.filter-btn.active { border-color: var(--heading); background: #e3f2fd; }
"""


def _nav_bar(active_page, num_variations):
    """Generate navigation bar HTML."""
    pages = [("index.html", "Index"), ("rules.html", "Rules"), ("inputs.html", "Inputs")]
    for i in range(1, num_variations + 1):
        pages.append((f"block_schedule_report_v2_{i}.html", f"Schedule v{i}"))

    links = []
    for href, label in pages:
        cls = ' class="active"' if href == active_page else ""
        links.append(f'<a href="{href}"{cls}>{label}</a>')

    return f'<div class="nav">{" ".join(links)}</div>'


# ═════════════════════════════════════════════════════════════════════════════
# INDEX PAGE
# ═════════════════════════════════════════════════════════════════════════════

def _generate_index(all_results, output_dir):
    """Generate the index page with variation cards."""
    n = len(all_results)
    r0 = all_results[0]
    block_start = r0["block_start"]
    block_end = r0["block_end"]

    # Format dates nicely
    start_dt = datetime.strptime(block_start, "%Y-%m-%d")
    end_dt = datetime.strptime(block_end, "%Y-%m-%d")
    date_range = f"{start_dt.strftime('%B %d')} &ndash; {end_dt.strftime('%B %d, %Y')}"

    h = [f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Block 3 Schedule - Index</title>
<style>{_common_css()}</style></head><body><div class="container">
{_nav_bar("index.html", n)}
<h1>Block 3 Schedule Report (Engine v2)</h1>
<p class="subtitle">{date_range}</p>

<h2>Reference Pages</h2>
<div class="var-card"><div><div class="var-title">Scheduling Rules</div>
<div class="var-stats">All constraints, policies, and logic used by the engine</div>
</div><a class="var-link" href="rules.html">View Rules &rarr;</a></div>

<div class="var-card"><div><div class="var-title">Provider Inputs</div>
<div class="var-stats">Prior block data, remaining obligations, site eligibility</div>
</div><a class="var-link" href="inputs.html">View Inputs &rarr;</a></div>

<h2>Schedule Variations</h2>
<p style="color:#666;margin-bottom:12px">Each variation uses a different random seed, producing a different assignment pattern while following the same rules.</p>
"""]

    for i, results in enumerate(all_results, 1):
        s = results["stats"]
        h.append(f"""<div class="var-card"><div>
<div class="var-title">Variation {i} (seed={s['seed']})</div>
<div class="var-stats">Site gaps: {s['total_site_gaps']} ({s.get('zero_gap_violations', '?')} zero-gap) &nbsp;|&nbsp; Overfills: {s['total_overfills']} &nbsp;|&nbsp; Under-utilized: {s['total_under_utilized']} &nbsp;|&nbsp; Stretch overrides: {s['stretch_overrides']}</div>
</div><a class="var-link" href="block_schedule_report_v2_{i}.html">View Schedule &rarr;</a></div>""")

    h.append("</div></body></html>")

    path = os.path.join(output_dir, "index.html")
    with open(path, "w") as f:
        f.write("\n".join(h))
    print(f"  Saved: {path}")


# ═════════════════════════════════════════════════════════════════════════════
# RULES PAGE
# ═════════════════════════════════════════════════════════════════════════════

def _generate_rules(output_dir, num_variations):
    """Generate the rules reference page."""
    h = [f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Scheduling Rules</title>
<style>{_common_css()}</style></head><body><div class="container">
{_nav_bar("rules.html", num_variations)}
<h1>Block 3 Scheduling Rules</h1>
<p class="subtitle">Reference guide for the scheduling engine constraints and logic.</p>

<h2>Site Assignment</h2>
<div class="rule"><span class="rule-num">1.</span>Providers are only assigned to sites where they have a non-zero allocation percentage.</div>
<div class="rule"><span class="rule-num">2.</span>Smaller sites are filled first (to capacity), then Cooper absorbs the remainder.</div>
<div class="rule"><span class="rule-num">3.</span>Cooper's full demand (26 weekday / 19 weekend) is the target. Shortfalls are expected — moonlighters fill the gap.</div>
<div class="rule"><span class="rule-num">4.</span>Providers stay at one site for a complete week+weekend stretch when possible.</div>

<h2>Scheduling Constraints</h2>
<div class="rule"><span class="rule-num">1.</span>No more than 2 consecutive active weeks (hard cap). A 3rd consecutive week would create a 19+ day stretch.</div>
<div class="rule"><span class="rule-num">2.</span>12-day stretches (2 consecutive weeks) are penalized but allowed when obligations require it.</div>
<div class="rule"><span class="rule-num">3.</span>Weekend assignments prefer pairing with the same site as the weekday assignment for the same week number.</div>
<div class="rule"><span class="rule-num">4.</span>Standalone weekends in back-to-back weeks are discouraged.</div>

<h2>Availability &amp; Time Off</h2>
<div class="rule"><span class="rule-num">1.</span>All provider schedule requests (time off) are honored — never overridden.</div>
<div class="rule"><span class="rule-num">2.</span>If honoring requests means a provider cannot be fully scheduled, this is documented with the reason.</div>
<div class="rule"><span class="rule-num">3.</span>Providers are never assigned to weeks/weekends where they are marked unavailable.</div>

<h2>Capacity &amp; Obligations</h2>
<div class="rule"><span class="rule-num">1.</span>Each provider owes a specific number of weeks and weekends (from their contract, minus prior blocks).</div>
<div class="rule"><span class="rule-num">2.</span>Remaining obligations use floor() — a provider with 8.6 remaining gets 8 weeks max. Never over-schedule.</div>
<div class="rule"><span class="rule-num">3.</span>Night-shift-only providers are excluded. Split providers (nights + days) are scheduled for their day obligations.</div>

<h2>Distribution &amp; Fairness</h2>
<div class="rule"><span class="rule-num">1.</span>Assignments are distributed evenly across the full block — no front-loading.</div>
<div class="rule"><span class="rule-num">2.</span>Week processing order is randomized to prevent early-block bias.</div>
<div class="rule"><span class="rule-num">3.</span>Ideal spacing is calculated per provider to evenly spread their assignments.</div>
<div class="rule"><span class="rule-num">4.</span>Two-pass fair-share: Pass 1 caps at ceil(annual/3), Pass 2 lifts cap for behind-pace providers.</div>
<div class="rule"><span class="rule-num">5.</span>Forced fill relaxes consecutive stretch limits (up to 3 weeks) to meet contractual obligations.</div>

<h2>Named Special Rules</h2>
<div class="rule"><span class="rule-num">1.</span>Haroldson &amp; McMillian — Never same week/weekend at any site.</div>
<div class="rule"><span class="rule-num">2.</span>Glenn Newell — Consults only, Mon-Thu (counts as full weeks for scheduling).</div>
<div class="rule"><span class="rule-num">3.</span>Paul Stone — Non-teaching only, never MAH (service-level constraint, deferred).</div>
</div></body></html>"""]

    path = os.path.join(output_dir, "rules.html")
    with open(path, "w") as f:
        f.write("\n".join(h))
    print(f"  Saved: {path}")


# ═════════════════════════════════════════════════════════════════════════════
# INPUTS PAGE
# ═════════════════════════════════════════════════════════════════════════════

def _generate_inputs(results, output_dir, num_variations):
    """Generate the inputs summary page showing provider data."""
    provider_summary = results["provider_summary"]
    stats = results["stats"]

    # Sort providers alphabetically
    sorted_provs = sorted(provider_summary.keys())

    total_wk_owed = sum(ps["weeks_target"] for ps in provider_summary.values())
    total_we_owed = sum(ps["weekends_target"] for ps in provider_summary.values())

    h = [f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Provider Inputs</title>
<style>{_common_css()}</style></head><body><div class="container">
{_nav_bar("inputs.html", num_variations)}
<h1>Provider Inputs — Block 3</h1>
<p class="subtitle">Provider obligations, remaining capacity, and site eligibility.</p>

<div class="stats-grid">
<div class="stat-card"><div class="stat-value">{stats['total_eligible']}</div><div class="stat-label">Eligible Providers</div></div>
<div class="stat-card"><div class="stat-value">{total_wk_owed}</div><div class="stat-label">Total Weeks Owed</div></div>
<div class="stat-card"><div class="stat-value">{total_we_owed}</div><div class="stat-label">Total Weekends Owed</div></div>
<div class="stat-card"><div class="stat-value">{stats['behind_pace_count']}</div><div class="stat-label">Behind-Pace</div></div>
</div>

<input type="text" class="search-box" id="provSearch" placeholder="Search providers..." onkeyup="filterProviders()">

<table id="provTable">
<thead><tr>
<th>Provider</th><th>Shift Type</th><th>FTE</th>
<th>Ann WK</th><th>Ann WE</th>
<th>Remaining WK</th><th>Remaining WE</th>
<th>Owed WK</th><th>Owed WE</th>
<th>Eligible Sites</th>
</tr></thead><tbody>"""]

    for pname in sorted_provs:
        ps = provider_summary[pname]
        # Flag providers with > 33% remaining (behind pace)
        ann_wk = ps["annual_weeks"]
        rem_ratio = ps["weeks_remaining"] / ann_wk if ann_wk > 0 else 0
        row_cls = ' style="background:#fff3e0"' if rem_ratio > 0.40 else ""

        site_badges = ""
        for site in sorted(ps["eligible_sites"]):
            color = SITE_COLORS.get(site, "#666")
            short = SITE_SHORT.get(site, site[:3])
            site_badges += f'<span class="site-badge" style="background:{color}">{esc(short)}</span>'

        h.append(f"""<tr{row_cls}>
<td>{esc(pname)}</td><td>{esc(ps['shift_type'])}</td><td>{ps['fte']:.2f}</td>
<td>{ps['annual_weeks']:.0f}</td><td>{ps['annual_weekends']:.0f}</td>
<td>{ps['weeks_remaining']:.1f}</td><td>{ps['weekends_remaining']:.1f}</td>
<td>{ps['weeks_target']}</td><td>{ps['weekends_target']}</td>
<td>{site_badges}</td>
</tr>""")

    h.append("""</tbody></table>
<script>
function filterProviders() {
  var q = document.getElementById('provSearch').value.toUpperCase();
  var rows = document.querySelectorAll('#provTable tbody tr');
  rows.forEach(function(r) {
    r.style.display = r.textContent.toUpperCase().includes(q) ? '' : 'none';
  });
}
</script>
</div></body></html>""")

    path = os.path.join(output_dir, "inputs.html")
    with open(path, "w") as f:
        f.write("\n".join(h))
    print(f"  Saved: {path}")


# ═════════════════════════════════════════════════════════════════════════════
# SCHEDULE REPORT (per variation)
# ═════════════════════════════════════════════════════════════════════════════

def _load_full_availability():
    """Load all availability statuses (not just unavailable) for mini calendars."""
    avail_all = {}
    if not os.path.isdir(SCHEDULES_DIR):
        return avail_all
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
        if jname not in avail_all:
            avail_all[jname] = {}
        for day in data.get("days", []):
            avail_all[jname][day["date"]] = day.get("status", "blank")
    return avail_all


def _render_mini_calendar(avail_map, date_assignments, block_start, block_end):
    """Render a compact 4-month availability calendar."""
    block_months = [(2026, 3), (2026, 4), (2026, 5), (2026, 6)]
    month_names = {3: "March", 4: "April", 5: "May", 6: "June"}
    day_headers = ["S", "M", "T", "W", "T", "F", "S"]

    h = ['<div class="avail-months">']

    for year, month in block_months:
        h.append(f'<div class="avail-month"><h4>{month_names[month]} {year}</h4>')
        h.append('<div class="mini-cal">')

        for dh in day_headers:
            h.append(f'<div class="dh">{dh}</div>')

        first_dow = calendar.weekday(year, month, 1)
        first_dow_sun = (first_dow + 1) % 7
        days_in_month = calendar.monthrange(year, month)[1]

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

            dt = datetime(year, month, day)
            in_block = block_start <= dt <= block_end

            h.append(f'<div class="{cls}"><div class="dn">{day}</div>')

            assigned_site = date_assignments.get(date_str)
            if assigned_site and in_block:
                color = SITE_COLORS.get(assigned_site, "#666")
                short = SITE_SHORT.get(assigned_site, assigned_site[:3])
                h.append(f'<div class="asg" style="background:{color}">{esc(short)}</div>')

            h.append('</div>')

        total_cells = first_dow_sun + days_in_month
        remaining = (7 - (total_cells % 7)) % 7
        for _ in range(remaining):
            h.append('<div class="dc empty"></div>')

        h.append('</div></div>')

    h.append('</div>')
    return "\n".join(h)


def _generate_schedule_report(results, variation_num, output_dir, num_variations):
    """Generate a single schedule variation report with 7 tabs."""
    periods = results["periods"]
    period_assignments = results["period_assignments"]
    provider_summary = results["provider_summary"]
    site_fill = results["site_fill"]
    stats = results["stats"]
    forced_stretch = results["forced_stretch_overrides"]
    block_start_str = results["block_start"]
    block_end_str = results["block_end"]
    block_start = datetime.strptime(block_start_str, "%Y-%m-%d")
    block_end = datetime.strptime(block_end_str, "%Y-%m-%d")

    all_sites = sorted(site_fill.keys())
    filename = f"block_schedule_report_v2_{variation_num}.html"

    # Load full availability for mini calendars
    avail_all = _load_full_availability()
    json_names = list(avail_all.keys())

    # Build date->site assignments per provider
    prov_date_asgn = defaultdict(dict)
    for idx_str, assigns in period_assignments.items():
        idx = int(idx_str)
        if idx < len(periods):
            for pname, site in assigns:
                for d in periods[idx]["dates"]:
                    prov_date_asgn[pname][d] = site

    # ── Start HTML ───────────────────────────────────────────────────────
    h = [f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Schedule Variation {variation_num} (seed={stats['seed']})</title>
<style>{_common_css()}</style></head><body><div class="container">
{_nav_bar(filename, num_variations)}
<h1>Schedule Variation {variation_num} (seed={stats['seed']})</h1>
<p class="subtitle">Engine v2 &mdash; {block_start.strftime('%B %d')} to {block_end.strftime('%B %d, %Y')}</p>

<div class="stats-grid">
<div class="stat-card"><div class="stat-value">{stats['total_eligible']}</div><div class="stat-label">Eligible Providers</div></div>
<div class="stat-card"><div class="stat-value">{stats['total_weeks_assigned']}</div><div class="stat-label">Weeks Assigned</div></div>
<div class="stat-card"><div class="stat-value">{stats['total_weekends_assigned']}</div><div class="stat-label">Weekends Assigned</div></div>
<div class="stat-card"><div class="stat-value">{stats['total_site_gaps']}</div><div class="stat-label">Site Gaps</div></div>
<div class="stat-card"><div class="stat-value">{stats.get('zero_gap_violations', '?')}</div><div class="stat-label">Zero-Gap Violations</div></div>
<div class="stat-card"><div class="stat-value">{stats['total_under_utilized']}</div><div class="stat-label">Under-Utilized</div></div>
<div class="stat-card"><div class="stat-value">{stats['stretch_overrides']}</div><div class="stat-label">Stretch Overrides</div></div>
</div>

<div class="tabs">
<div class="tab active" onclick="switchTab(0)">Calendar</div>
<div class="tab" onclick="switchTab(1)">Providers</div>
<div class="tab" onclick="switchTab(2)">Site Summary</div>
<div class="tab" onclick="switchTab(3)">Utilization</div>
<div class="tab" onclick="switchTab(4)">Flags</div>
<div class="tab" onclick="switchTab(5)">Shortfalls</div>
<div class="tab" onclick="switchTab(6)">Open Shifts</div>
</div>
"""]

    # ═══ TAB 0: CALENDAR ═════════════════════════════════════════════════
    h.append('<div class="tab-content active" id="tab0">')
    h.append('<h2>Schedule Calendar</h2>')

    # Site filter buttons
    h.append('<div style="margin-bottom:12px">')
    h.append('<button class="filter-btn active" onclick="filterSite(\'all\',this)">All</button>')
    for site in all_sites:
        color = SITE_COLORS.get(site, "#666")
        short = SITE_SHORT.get(site, site[:3])
        h.append(f'<button class="filter-btn" onclick="filterSite(\'{esc(site)}\',this)" '
                 f'style="border-color:{color}">{short}</button>')
    h.append('</div>')

    # Group periods by month
    months = {}
    for idx, p in enumerate(periods):
        d = datetime.strptime(p["dates"][0], "%Y-%m-%d")
        mk = d.strftime("%B %Y")
        months.setdefault(mk, []).append(idx)

    for month_key, period_indices in months.items():
        h.append(f'<h3>{month_key}</h3>')
        h.append('<table><thead><tr><th>Period</th><th>Fill</th>')
        for site in all_sites:
            short = SITE_SHORT.get(site, site[:3])
            h.append(f'<th class="site-col" data-site="{esc(site)}">{short}</th>')
        h.append('</tr></thead><tbody>')

        for idx in period_indices:
            period = periods[idx]
            is_we = period["type"] == "weekend"
            row_cls = ' class="weekend-row"' if is_we else ""
            dtype = "weekend" if is_we else "weekday"

            label = period["label"]
            date_range = f"{period['dates'][0]} to {period['dates'][-1]}" if period['dates'] else ""

            # Total fill
            total_demand = sum(site_fill.get(s, {}).get(dtype, [{}])[0].get("demand", 0)
                               if site_fill.get(s, {}).get(dtype) else 0
                               for s in all_sites)
            # Find the right entry for this period
            total_filled = len(period_assignments.get(str(idx), []))
            total_needed = 0
            for site in all_sites:
                total_needed += results.get("site_fill", {}).get(site, {}).get(dtype, [{}])[0].get("demand", 0) if False else 0

            # Calculate actual total from period_assignments
            assigns = period_assignments.get(str(idx), [])
            fill_count = len(assigns)

            h.append(f'<tr{row_cls}><td style="white-space:nowrap">{esc(label)}</td>')
            h.append(f'<td style="text-align:center">{fill_count}</td>')

            for site in all_sites:
                demand = 0
                for sf_entry in site_fill.get(site, {}).get(dtype, []):
                    if sf_entry.get("period_idx") == idx:
                        demand = sf_entry["demand"]
                        break

                site_provs = [n for n, s in assigns if s == site]
                filled = len(site_provs)

                # Badge color
                if demand == 0:
                    badge = f'<span class="badge" style="color:#999">—</span>'
                elif filled >= demand:
                    badge = f'<span class="badge badge-ok">{filled}/{demand}</span>'
                else:
                    badge = f'<span class="badge badge-short">{filled}/{demand}</span>'

                # Provider links
                prov_links = ""
                for pn in site_provs:
                    pid = prov_id(pn)
                    short_name = pn.split(",")[0] if "," in pn else pn
                    prov_links += f'<a class="prov-link" onclick="showProvider(\'{pid}\')">{esc(short_name)}</a>'

                h.append(f'<td class="site-col" data-site="{esc(site)}">{badge}{prov_links}</td>')

            h.append('</tr>')

        h.append('</tbody></table>')

    h.append('</div>')  # end tab0

    # ═══ TAB 1: PROVIDERS ════════════════════════════════════════════════
    h.append('<div class="tab-content" id="tab1">')
    h.append('<h2>Provider Details</h2>')
    h.append('<input type="text" class="search-box" id="provSearchDetail" placeholder="Search providers..." onkeyup="filterProvDetail()">')

    sorted_provs = sorted(provider_summary.keys())
    for pname in sorted_provs:
        ps = provider_summary[pname]
        pid = prov_id(pname)

        wk_pct = (ps["weeks_assigned"] / ps["weeks_target"] * 100) if ps["weeks_target"] > 0 else 0
        we_pct = (ps["weekends_assigned"] / ps["weekends_target"] * 100) if ps["weekends_target"] > 0 else 0

        # Status
        status_badges = ""
        if ps["weeks_gap"] > 0 or ps["weekends_gap"] > 0:
            status_badges += '<span class="badge badge-short">UNDER</span> '
        elif wk_pct > 100 or we_pct > 100:
            status_badges += '<span class="badge badge-warn">OVER</span> '
        else:
            status_badges += '<span class="badge badge-ok">OK</span> '

        # Site badges
        site_badges = ""
        for site, count in sorted(ps["site_distribution"].items()):
            color = SITE_COLORS.get(site, "#666")
            short = SITE_SHORT.get(site, site[:3])
            site_badges += f'<span class="site-badge" style="background:{color}">{short}({count})</span>'

        h.append(f'<div class="prov-card" id="{pid}">')
        h.append(f'<div class="prov-header" onclick="toggleCard(this)">')
        h.append(f'<div><strong>{esc(pname)}</strong> &mdash; {esc(ps["shift_type"])} &mdash; FTE {ps["fte"]:.2f} {status_badges}</div>')
        h.append(f'<div>WK: {ps["weeks_assigned"]}/{ps["weeks_target"]} &nbsp; WE: {ps["weekends_assigned"]}/{ps["weekends_target"]} &nbsp; <span class="prov-arrow">&#9654;</span></div>')
        h.append('</div>')
        h.append('<div class="prov-body">')

        # Utilization bars
        wk_color = "#4caf50" if wk_pct >= 80 else ("#ff9800" if wk_pct >= 50 else "#f44336")
        we_color = "#4caf50" if we_pct >= 80 else ("#ff9800" if we_pct >= 50 else "#f44336")
        h.append(f'<p>Weeks: <span class="util-bar"><span class="util-fill" style="width:{min(wk_pct,100):.0f}%;background:{wk_color}"></span></span> {wk_pct:.0f}%</p>')
        h.append(f'<p>Weekends: <span class="util-bar"><span class="util-fill" style="width:{min(we_pct,100):.0f}%;background:{we_color}"></span></span> {we_pct:.0f}%</p>')

        # Site distribution
        if site_badges:
            h.append(f'<p style="margin-top:8px">Sites: {site_badges}</p>')

        # Holidays
        if ps.get("holiday_1") or ps.get("holiday_2"):
            h.append(f'<p style="margin-top:4px;color:#666">Holiday prefs: {esc(ps.get("holiday_1",""))} / {esc(ps.get("holiday_2",""))}</p>')

        # Under-utilization reason
        if ps["under_utilization_reason"]:
            reason = ps["under_utilization_reason"].replace("_", " ").title()
            h.append(f'<p style="margin-top:4px;color:var(--short-text)">Gap reason: {reason}</p>')

        # Mini calendar
        json_name = match_provider(pname, json_names)
        avail_map = avail_all.get(json_name, {}) if json_name else {}
        date_asgn = prov_date_asgn.get(pname, {})
        h.append(_render_mini_calendar(avail_map, date_asgn, block_start, block_end))

        # Schedule table
        assignments = ps.get("assignments", [])
        if assignments:
            h.append('<table style="margin-top:8px"><thead><tr><th>Wk</th><th>Type</th><th>Dates</th><th>Site</th></tr></thead><tbody>')
            for pidx, site in assignments:
                if pidx < len(periods):
                    p = periods[pidx]
                    color = SITE_COLORS.get(site, "#666")
                    short = SITE_SHORT.get(site, site[:3])
                    h.append(f'<tr><td>{p["num"]}</td><td>{p["type"]}</td>')
                    h.append(f'<td>{p["dates"][0]} to {p["dates"][-1]}</td>')
                    h.append(f'<td><span class="site-badge" style="background:{color}">{esc(short)}</span></td></tr>')
            h.append('</tbody></table>')

        h.append('</div></div>')  # end prov-body, prov-card

    h.append('</div>')  # end tab1

    # ═══ TAB 2: SITE SUMMARY ════════════════════════════════════════════
    h.append('<div class="tab-content" id="tab2">')
    h.append('<h2>Site Fill Summary</h2>')

    h.append('<table><thead><tr><th>Site</th><th>WK Demand</th><th>WK Min-Max</th><th>WK Short</th><th>WE Demand</th><th>WE Min-Max</th><th>WE Short</th></tr></thead><tbody>')
    for site in all_sites:
        sf = site_fill.get(site, {})
        for dtype, label in [("weekday", "WK"), ("weekend", "WE")]:
            entries = sf.get(dtype, [])
            if not entries:
                continue

        wk_entries = sf.get("weekday", [])
        we_entries = sf.get("weekend", [])
        wk_demand = wk_entries[0]["demand"] if wk_entries else 0
        we_demand = we_entries[0]["demand"] if we_entries else 0
        wk_fills = [e["filled"] for e in wk_entries]
        we_fills = [e["filled"] for e in we_entries]
        wk_short = sum(e["shortfall"] for e in wk_entries)
        we_short = sum(e["shortfall"] for e in we_entries)

        wk_range = f"{min(wk_fills)}-{max(wk_fills)}" if wk_fills else "—"
        we_range = f"{min(we_fills)}-{max(we_fills)}" if we_fills else "—"
        wk_badge = f'<span class="badge badge-ok">0</span>' if wk_short == 0 else f'<span class="badge badge-short">{wk_short}</span>'
        we_badge = f'<span class="badge badge-ok">0</span>' if we_short == 0 else f'<span class="badge badge-short">{we_short}</span>'

        color = SITE_COLORS.get(site, "#666")
        h.append(f'<tr><td><span class="site-badge" style="background:{color}">{esc(site)}</span></td>')
        h.append(f'<td>{wk_demand}</td><td>{wk_range}</td><td>{wk_badge}</td>')
        h.append(f'<td>{we_demand}</td><td>{we_range}</td><td>{we_badge}</td></tr>')

    h.append('</tbody></table>')

    # Per-site weekly detail
    for site in all_sites:
        sf = site_fill.get(site, {})
        wk_entries = sf.get("weekday", [])
        we_entries = sf.get("weekend", [])
        if not wk_entries and not we_entries:
            continue

        color = SITE_COLORS.get(site, "#666")
        h.append(f'<h3><span class="site-badge" style="background:{color}">{esc(site)}</span> Weekly Detail</h3>')
        h.append('<table><thead><tr><th>Week</th><th>Weekday</th><th>Weekend</th></tr></thead><tbody>')

        wk_by_num = {e["week_num"]: e for e in wk_entries}
        we_by_num = {e["week_num"]: e for e in we_entries}
        all_nums = sorted(set(list(wk_by_num.keys()) + list(we_by_num.keys())))

        for wn in all_nums:
            wk = wk_by_num.get(wn, {})
            we = we_by_num.get(wn, {})

            wk_filled = wk.get("filled", 0)
            wk_demand = wk.get("demand", 0)
            we_filled = we.get("filled", 0)
            we_demand = we.get("demand", 0)

            wk_cls = "badge-ok" if wk_filled >= wk_demand else "badge-short"
            we_cls = "badge-ok" if we_filled >= we_demand else "badge-short"

            row_cls = ""
            if wk_filled < wk_demand or we_filled < we_demand:
                row_cls = ' class="short-row"'

            h.append(f'<tr{row_cls}><td>Week {wn}</td>')
            h.append(f'<td><span class="badge {wk_cls}">{wk_filled}/{wk_demand}</span></td>')
            h.append(f'<td><span class="badge {we_cls}">{we_filled}/{we_demand}</span></td></tr>')

        h.append('</tbody></table>')

    h.append('</div>')  # end tab2

    # ═══ TAB 3: UTILIZATION ══════════════════════════════════════════════
    h.append('<div class="tab-content" id="tab3">')
    h.append('<h2>Provider Utilization</h2>')
    h.append('<table><thead><tr><th>Provider</th><th>Type</th><th>FTE</th>')
    h.append('<th>WK Used/Target</th><th>WK %</th>')
    h.append('<th>WE Used/Target</th><th>WE %</th>')
    h.append('<th>Sites</th><th>Status</th></tr></thead><tbody>')

    for pname in sorted_provs:
        ps = provider_summary[pname]
        wk_pct = (ps["weeks_assigned"] / ps["weeks_target"] * 100) if ps["weeks_target"] > 0 else 0
        we_pct = (ps["weekends_assigned"] / ps["weekends_target"] * 100) if ps["weekends_target"] > 0 else 0

        status = ""
        if ps["weeks_gap"] > 0 or ps["weekends_gap"] > 0:
            status = '<span class="badge badge-short">UNDER</span>'
        elif wk_pct > 100 or we_pct > 100:
            status = '<span class="badge badge-warn">OVER</span>'
        else:
            status = '<span class="badge badge-ok">OK</span>'

        site_list = ", ".join(SITE_SHORT.get(s, s[:3]) for s in sorted(ps["site_distribution"].keys()))

        h.append(f'<tr><td>{esc(pname)}</td><td>{esc(ps["shift_type"])}</td><td>{ps["fte"]:.2f}</td>')
        h.append(f'<td>{ps["weeks_assigned"]}/{ps["weeks_target"]}</td><td>{wk_pct:.0f}%</td>')
        h.append(f'<td>{ps["weekends_assigned"]}/{ps["weekends_target"]}</td><td>{we_pct:.0f}%</td>')
        h.append(f'<td>{site_list}</td><td>{status}</td></tr>')

    h.append('</tbody></table></div>')  # end tab3

    # ═══ TAB 4: FLAGS ════════════════════════════════════════════════════
    h.append('<div class="tab-content" id="tab4">')
    h.append('<h2>Flags &amp; Warnings</h2>')

    # Over-assigned
    over = [(p, ps) for p, ps in provider_summary.items()
            if ps["weeks_assigned"] > ps["weeks_target"] or ps["weekends_assigned"] > ps["weekends_target"]]
    h.append(f'<h3>Over-Assigned ({len(over)})</h3>')
    if over:
        h.append('<table><thead><tr><th>Provider</th><th>WK Used/Target</th><th>WE Used/Target</th></tr></thead><tbody>')
        for p, ps in sorted(over):
            h.append(f'<tr><td>{esc(p)}</td><td>{ps["weeks_assigned"]}/{ps["weeks_target"]}</td>')
            h.append(f'<td>{ps["weekends_assigned"]}/{ps["weekends_target"]}</td></tr>')
        h.append('</tbody></table>')
    else:
        h.append('<p style="color:#666">None</p>')

    # Under-utilized
    under = [(p, ps) for p, ps in provider_summary.items()
             if ps["weeks_gap"] > 0 or ps["weekends_gap"] > 0]
    h.append(f'<h3>Under-Utilized ({len(under)})</h3>')
    if under:
        h.append('<table><thead><tr><th>Provider</th><th>WK Gap</th><th>WE Gap</th><th>Reason</th></tr></thead><tbody>')
        for p, ps in sorted(under, key=lambda x: -(x[1]["weeks_gap"] + x[1]["weekends_gap"])):
            reason = ps["under_utilization_reason"].replace("_", " ").title() if ps["under_utilization_reason"] else "—"
            h.append(f'<tr><td>{esc(p)}</td><td>{ps["weeks_gap"]}</td><td>{ps["weekends_gap"]}</td><td>{reason}</td></tr>')
        h.append('</tbody></table>')
    else:
        h.append('<p style="color:#666">None — all providers fully scheduled</p>')

    # Stretch overrides
    h.append(f'<h3>Stretch Overrides ({len(forced_stretch)})</h3>')
    if forced_stretch:
        h.append('<table><thead><tr><th>Provider</th><th>Period</th><th>Run Length (weeks)</th></tr></thead><tbody>')
        for pname, pidx, run_len in forced_stretch:
            p = periods[pidx] if pidx < len(periods) else {}
            h.append(f'<tr><td>{esc(pname)}</td><td>Week {p.get("num","?")}</td><td>{run_len}</td></tr>')
        h.append('</tbody></table>')
    else:
        h.append('<p style="color:#666">None</p>')

    h.append('</div>')  # end tab4

    # ═══ TAB 5: SHORTFALLS ═══════════════════════════════════════════════
    h.append('<div class="tab-content" id="tab5">')
    h.append('<h2>Site Coverage Gaps</h2>')

    gap_rows = []
    for site in all_sites:
        for dtype in ["weekday", "weekend"]:
            for entry in site_fill.get(site, {}).get(dtype, []):
                if entry["shortfall"] > 0:
                    gap_rows.append((site, dtype, entry["week_num"], entry["demand"], entry["filled"], entry["shortfall"]))

    h.append(f'<p style="margin-bottom:8px">{len(gap_rows)} site-period gaps found.</p>')
    if gap_rows:
        h.append('<table><thead><tr><th>Site</th><th>Type</th><th>Week</th><th>Need</th><th>Filled</th><th>Short</th></tr></thead><tbody>')
        for site, dtype, wn, demand, filled, short in sorted(gap_rows, key=lambda x: (-x[5], x[0], x[2])):
            color = SITE_COLORS.get(site, "#666")
            h.append(f'<tr><td><span class="site-badge" style="background:{color}">{esc(site)}</span></td>')
            h.append(f'<td>{dtype}</td><td>{wn}</td><td>{demand}</td><td>{filled}</td>')
            h.append(f'<td><span class="badge badge-short">{short}</span></td></tr>')
        h.append('</tbody></table>')

    # Provider utilization gaps
    h.append('<h2>Provider Utilization Gaps</h2>')
    util_gaps = [(p, ps) for p, ps in provider_summary.items()
                 if ps["weeks_gap"] + ps["weekends_gap"] >= 2]
    if util_gaps:
        h.append('<table><thead><tr><th>Provider</th><th>WK Assigned/Owed</th><th>WK Gap</th><th>WE Assigned/Owed</th><th>WE Gap</th><th>Total Gap</th></tr></thead><tbody>')
        for p, ps in sorted(util_gaps, key=lambda x: -(x[1]["weeks_gap"] + x[1]["weekends_gap"])):
            total = ps["weeks_gap"] + ps["weekends_gap"]
            row_cls = ' style="background:#fce4ec"' if total >= 3 else ""
            h.append(f'<tr{row_cls}><td>{esc(p)}</td>')
            h.append(f'<td>{ps["weeks_assigned"]}/{ps["weeks_target"]}</td><td>{ps["weeks_gap"]}</td>')
            h.append(f'<td>{ps["weekends_assigned"]}/{ps["weekends_target"]}</td><td>{ps["weekends_gap"]}</td>')
            h.append(f'<td><span class="badge badge-short">{total}</span></td></tr>')
        h.append('</tbody></table>')
    else:
        h.append('<p style="color:#666">No providers with utilization gap >= 2</p>')

    h.append('</div>')  # end tab5

    # ═══ TAB 6: OPEN SHIFTS ══════════════════════════════════════════════
    h.append('<div class="tab-content" id="tab6">')
    h.append('<h2>Open Shifts by Week</h2>')

    total_open_wk = sum(e["shortfall"] for s in all_sites for e in site_fill.get(s, {}).get("weekday", []))
    total_open_we = sum(e["shortfall"] for s in all_sites for e in site_fill.get(s, {}).get("weekend", []))

    h.append(f"""<div class="stats-grid">
<div class="stat-card"><div class="stat-value">{total_open_wk}</div><div class="stat-label">Open Weekday Shifts</div></div>
<div class="stat-card"><div class="stat-value">{total_open_we}</div><div class="stat-label">Open Weekend Shifts</div></div>
</div>""")

    # Open shifts detail table
    open_rows = []
    for site in all_sites:
        for dtype in ["weekday", "weekend"]:
            for entry in site_fill.get(site, {}).get(dtype, []):
                if entry["shortfall"] > 0:
                    pidx = entry["period_idx"]
                    p = periods[pidx] if pidx < len(periods) else {}
                    open_rows.append({
                        "site": site, "dtype": dtype, "week_num": entry["week_num"],
                        "dates": f"{p['dates'][0]} to {p['dates'][-1]}" if p.get("dates") else "—",
                        "demand": entry["demand"], "filled": entry["filled"], "open": entry["shortfall"],
                    })

    if open_rows:
        h.append('<table><thead><tr><th>Site</th><th>Type</th><th>Week</th><th>Dates</th><th>Demand</th><th>Filled</th><th>Open</th></tr></thead><tbody>')
        for row in sorted(open_rows, key=lambda x: (-x["open"], x["site"], x["week_num"])):
            color = SITE_COLORS.get(row["site"], "#666")
            h.append(f'<tr><td><span class="site-badge" style="background:{color}">{esc(row["site"])}</span></td>')
            h.append(f'<td>{row["dtype"]}</td><td>{row["week_num"]}</td><td>{row["dates"]}</td>')
            h.append(f'<td>{row["demand"]}</td><td>{row["filled"]}</td>')
            h.append(f'<td><span class="badge badge-short">{row["open"]}</span></td></tr>')
        h.append('</tbody></table>')
    else:
        h.append('<p style="color:#2e7d32;font-weight:600">All sites fully staffed!</p>')

    # Under-utilized with reasons
    h.append('<h2>Under-Utilized Providers</h2>')
    under = [(p, ps) for p, ps in provider_summary.items()
             if ps["weeks_gap"] > 0 or ps["weekends_gap"] > 0]

    if under:
        # Reason breakdown
        reasons = defaultdict(int)
        for p, ps in under:
            r = ps["under_utilization_reason"] or "unknown"
            reasons[r] += 1

        h.append('<div class="stats-grid">')
        for r, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
            label = r.replace("_", " ").title()
            h.append(f'<div class="stat-card"><div class="stat-value">{cnt}</div><div class="stat-label">{label}</div></div>')
        h.append('</div>')

        h.append('<table><thead><tr><th>Provider</th><th>WK Gap</th><th>WE Gap</th><th>Eligible Sites</th><th>Reason</th></tr></thead><tbody>')
        for p, ps in sorted(under, key=lambda x: -(x[1]["weeks_gap"] + x[1]["weekends_gap"])):
            sites_str = ", ".join(SITE_SHORT.get(s, s[:3]) for s in sorted(ps["eligible_sites"]))
            reason = ps["under_utilization_reason"].replace("_", " ").title() if ps["under_utilization_reason"] else "—"
            h.append(f'<tr><td>{esc(p)}</td><td>{ps["weeks_gap"]}</td><td>{ps["weekends_gap"]}</td>')
            h.append(f'<td>{sites_str}</td><td>{reason}</td></tr>')
        h.append('</tbody></table>')

    h.append('</div>')  # end tab6

    # ═══ JAVASCRIPT ══════════════════════════════════════════════════════
    h.append("""
<script>
function switchTab(idx) {
  document.querySelectorAll('.tab').forEach(function(t, i) {
    t.classList.toggle('active', i === idx);
  });
  document.querySelectorAll('.tab-content').forEach(function(tc, i) {
    tc.classList.toggle('active', i === idx);
  });
}

function toggleCard(header) {
  var card = header.parentElement;
  card.classList.toggle('open');
}

function showProvider(pid) {
  switchTab(1);
  var card = document.getElementById(pid);
  if (card) {
    card.classList.add('open');
    setTimeout(function() { card.scrollIntoView({behavior: 'smooth', block: 'center'}); }, 100);
  }
}

function filterProvDetail() {
  var q = document.getElementById('provSearchDetail').value.toUpperCase();
  document.querySelectorAll('.prov-card').forEach(function(card) {
    card.style.display = card.textContent.toUpperCase().includes(q) ? '' : 'none';
  });
}

function filterSite(site, btn) {
  document.querySelectorAll('.filter-btn').forEach(function(b) { b.classList.remove('active'); });
  btn.classList.add('active');
  document.querySelectorAll('.site-col').forEach(function(col) {
    if (site === 'all') {
      col.style.display = '';
    } else {
      col.style.display = col.getAttribute('data-site') === site ? '' : 'none';
    }
  });
}
</script>
""")

    h.append("</div></body></html>")

    path = os.path.join(output_dir, filename)
    with open(path, "w") as f:
        f.write("\n".join(h))
    print(f"  Saved: {path}")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def generate_full_report(all_results, output_dir):
    """Generate all HTML report pages.

    Args:
        all_results: list of result dicts (one per seed variation)
        output_dir: directory to write HTML files to
    """
    n = len(all_results)
    os.makedirs(output_dir, exist_ok=True)

    _generate_index(all_results, output_dir)
    _generate_rules(output_dir, n)
    _generate_inputs(all_results[0], output_dir, n)

    for i, results in enumerate(all_results, 1):
        _generate_schedule_report(results, i, output_dir, n)

    print(f"\n  All reports generated: index + rules + inputs + {n} schedule variations")
