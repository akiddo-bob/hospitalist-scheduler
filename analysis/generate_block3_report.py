#!/usr/bin/env python3
"""
Generate an HTML validation report for the Block 3 schedule.

Runs all 12 validation checks from validate_block3.py and renders results
as a self-contained HTML file with collapsible detail sections. Every
violation includes enough context (dates, services, sites, notes) that the
reader never needs to consult another spreadsheet.

Usage:
    python -m analysis.generate_block3_report
"""

import html as html_mod
import json
import math
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

# ── Project root setup ──────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from analysis.validate_block3 import (
    BLOCK_3_START, BLOCK_3_END, MEMORIAL_DAY_WEEK_START, MEMORIAL_DAY_WEEK_END,
    PCT_FIELDS,
    parse_block3, filter_day_only, service_to_site,
    get_week_num, is_weekend_day,
    check_site_eligibility, check_site_demand, check_provider_distribution,
    check_capacity_limits, check_consecutive_stretches, check_availability,
    check_conflict_pairs, check_week_weekend_pairing, check_single_site_per_week,
    check_holiday_rules, check_swing_reservation, extract_swap_notes,
)
from block.engines.shared.loader import (
    load_providers, load_tags, load_sites, load_availability,
    build_name_map, get_eligible_sites, has_tag, get_tag_rules,
    SITE_PCT_MAP,
)

OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")

# ═══════════════════════════════════════════════════════════════════════════
# HTML HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def esc(text):
    if text is None:
        return ""
    return html_mod.escape(str(text))

def fmt_date(d):
    """Format date as Mon 3/2."""
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return f"{days[d.weekday()]} {d.month}/{d.day}"

def week_date_range(wk):
    """Return (start, end) dates for a week number."""
    start = BLOCK_3_START + timedelta(days=(wk - 1) * 7)
    end = start + timedelta(days=6)
    if end > BLOCK_3_END:
        end = BLOCK_3_END
    return start, end

def badge(text, color):
    return f'<span class="badge badge-{color}">{esc(text)}</span>'

def collapsible(summary_html, detail_html, open_default=False):
    op = " open" if open_default else ""
    return f'<details{op}><summary>{summary_html}</summary><div class="detail-body">{detail_html}</div></details>'


# ═══════════════════════════════════════════════════════════════════════════
# DATA ENRICHMENT — build per-provider day-by-day detail
# ═══════════════════════════════════════════════════════════════════════════

def build_provider_day_map(day_assignments):
    """provider -> date -> list of {site, service, note}."""
    m = defaultdict(lambda: defaultdict(list))
    for a in day_assignments:
        m[a["provider"]][a["date"]].append({
            "site": a["site"],
            "service": a["service"],
            "note": a.get("note", ""),
        })
    return m

def build_provider_week_summary(day_assignments):
    """provider -> week_num -> {weekday_sites, weekend_sites, dates}."""
    m = defaultdict(lambda: defaultdict(lambda: {"weekday_sites": set(), "weekend_sites": set(), "dates": []}))
    for a in day_assignments:
        wk = get_week_num(a["date"])
        if is_weekend_day(a["date"]):
            m[a["provider"]][wk]["weekend_sites"].add(a["site"])
        else:
            m[a["provider"]][wk]["weekday_sites"].add(a["site"])
        m[a["provider"]][wk]["dates"].append(a["date"])
    return m

def render_date_table(prov_day_map, provider, dates):
    """Render a compact table of dates + services for a provider."""
    rows = []
    for d in sorted(dates):
        entries = prov_day_map[provider].get(d, [])
        for e in entries:
            we_cls = ' class="weekend-row"' if is_weekend_day(d) else ""
            note_cell = f' <span class="note-text">({esc(e["note"])})</span>' if e["note"] else ""
            rows.append(
                f"<tr{we_cls}><td>{fmt_date(d)}</td><td>{esc(d.strftime('%Y-%m-%d'))}</td>"
                f"<td>{esc(e['site'])}</td><td>{esc(e['service'])}{note_cell}</td></tr>"
            )
    if not rows:
        return "<p><em>No day-shift assignments found.</em></p>"
    return (
        '<table class="detail-table"><thead><tr><th>Day</th><th>Date</th><th>Site</th><th>Service</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


# ═══════════════════════════════════════════════════════════════════════════
# SECTION RENDERERS
# ═══════════════════════════════════════════════════════════════════════════

def render_overview(all_assignments, day_assignments, providers, tags_data):
    type_counts = defaultdict(int)
    for a in all_assignments:
        type_counts[a["service_type"]] += 1
    providers_seen = set(a["provider"] for a in day_assignments)
    sites_seen = sorted(set(a["site"] for a in day_assignments))

    return f"""
    <div class="overview-grid">
      <div class="ov-card"><div class="ov-num">{len(day_assignments):,}</div><div class="ov-label">Day Shift Assignments</div></div>
      <div class="ov-card"><div class="ov-num">{len(providers_seen)}</div><div class="ov-label">Providers (day shifts)</div></div>
      <div class="ov-card"><div class="ov-num">{len(sites_seen)}</div><div class="ov-label">Hospital Sites</div></div>
      <div class="ov-card"><div class="ov-num">{type_counts.get('night',0):,}</div><div class="ov-label">Night Shifts (excluded)</div></div>
      <div class="ov-card"><div class="ov-num">{type_counts.get('swing',0)}</div><div class="ov-label">Swing Shifts (excluded)</div></div>
      <div class="ov-card"><div class="ov-num">{type_counts.get('exclude',0):,}</div><div class="ov-label">Excluded Services</div></div>
    </div>
    <p class="scope-note">Scope: Day shifts only &mdash; per Section 6.3, night/swing scheduling is out of scope for block validation.
    Block 3 date range: <strong>{BLOCK_3_START.strftime('%b %d, %Y')}</strong> &ndash; <strong>{BLOCK_3_END.strftime('%b %d, %Y')}</strong> (17 weeks).</p>
    <p class="scope-note">Sites: {', '.join(sites_seen)}</p>
    """


def render_summary_card(checks):
    """Render the at-a-glance summary card."""
    hard_rows = []
    soft_rows = []
    for c in checks:
        icon = "✅" if c["count"] == 0 else ("🔴" if c["hard"] else "🟡")
        count_str = str(c["count"]) if isinstance(c["count"], int) else c["count"]
        row = f"<tr><td>{icon}</td><td>{esc(c['name'])}</td><td class='num'>{count_str}</td><td>{esc(c['rule_ref'])}</td></tr>"
        if c["hard"]:
            hard_rows.append(row)
        else:
            soft_rows.append(row)

    return f"""
    <h3>Hard Rule Violations</h3>
    <table class="summary-table"><thead><tr><th></th><th>Check</th><th class="num">Count</th><th>Rule Ref</th></tr></thead>
    <tbody>{''.join(hard_rows)}</tbody></table>
    <h3>Soft Rules &amp; Informational</h3>
    <table class="summary-table"><thead><tr><th></th><th>Check</th><th class="num">Count</th><th>Rule Ref</th></tr></thead>
    <tbody>{''.join(soft_rows)}</tbody></table>
    """


def render_check1(elig_violations, providers, tags_data, prov_day_map):
    if not elig_violations:
        return "<p class='pass'>✅ No site eligibility violations found.</p>"

    by_prov = defaultdict(list)
    for v in elig_violations:
        by_prov[v["provider"]].append(v)

    items = []
    for pname, vlist in sorted(by_prov.items()):
        eligible = sorted(vlist[0]["eligible_sites"])
        dates = sorted(set(v["date"] for v in vlist))
        sites_violated = sorted(set(v["site"] for v in vlist))

        # Full detail table for this provider
        detail_rows = []
        for v in sorted(vlist, key=lambda x: x["date"]):
            note_str = f' <span class="note-text">({esc(v["note"])})</span>' if v["note"] else ""
            we_cls = ' class="weekend-row"' if is_weekend_day(v["date"]) else ""
            detail_rows.append(
                f"<tr{we_cls}><td>{fmt_date(v['date'])}</td><td>{v['date']}</td>"
                f"<td class='violation-site'>{esc(v['site'])}</td><td>{esc(v['service'])}{note_str}</td></tr>"
            )
        violation_table = (
            '<h5 style="margin:8px 0 4px;color:var(--red);">Shifts at ineligible sites:</h5>'
            '<table class="detail-table"><thead><tr><th>Day</th><th>Date</th><th>Site</th><th>Service</th></tr></thead>'
            f"<tbody>{''.join(detail_rows)}</tbody></table>"
        )

        # Full Block 3 schedule for context
        all_dates = sorted(prov_day_map.get(pname, {}).keys())
        schedule_table = render_date_table(prov_day_map, pname, all_dates)
        full_schedule = collapsible(
            f"Full Block 3 schedule ({len(all_dates)} days)",
            schedule_table
        )

        detail = violation_table + full_schedule
        eligible_str = ", ".join(eligible)
        summary = (
            f"<strong>{esc(pname)}</strong> &mdash; {len(vlist)} violations at "
            f"{badge(', '.join(sites_violated), 'red')} &nbsp; Eligible: {esc(eligible_str)}"
        )
        items.append(collapsible(summary, detail))

    return f"<p>{badge(str(len(elig_violations)) + ' violations', 'red')} across {len(by_prov)} providers</p>" + "\n".join(items)


def render_check2(demand_issues, sites_demand, day_assignments):
    """Render site demand as a color-coded heatmap grid.

    Rows = site/day_type combos, Columns = weeks 1-17.
    Cell = actual headcount, colored by deviation from demand:
      green = on target, yellow = ±1, red = ±2+
    """
    total_weeks = (BLOCK_3_END - BLOCK_3_START).days // 7 + 1

    # Build actual staffing counts: (site, week, day_type) -> count of providers
    site_week_staff = defaultdict(set)
    for a in day_assignments:
        wk = get_week_num(a["date"])
        day_type = "weekend" if is_weekend_day(a["date"]) else "weekday"
        site_week_staff[(a["site"], wk, day_type)].add(a["provider"])

    # Build the list of site/dtype rows from demand config (skip swing)
    site_rows = []
    for (site, dtype), needed in sorted(sites_demand.items()):
        if dtype == "swing":
            continue
        site_rows.append((site, dtype, needed))

    # Stats
    total_short = sum(1 for d in demand_issues if d["diff"] < 0)
    total_over = sum(1 for d in demand_issues if d["diff"] > 0)
    total_ok = len(site_rows) * total_weeks - len(demand_issues)

    # Week column headers with date ranges
    week_headers = []
    for wk in range(1, total_weeks + 1):
        ws, we = week_date_range(wk)
        week_headers.append(f'<th class="hm-week" title="{ws.strftime("%m/%d")} – {we.strftime("%m/%d")}">W{wk}</th>')

    # Build heatmap for weekday demand
    def build_heatmap(day_type_filter, label):
        rows_for_type = [(site, dtype, needed) for site, dtype, needed in site_rows if dtype == day_type_filter]
        if not rows_for_type:
            return ""
        body_rows = []
        for site, dtype, needed in rows_for_type:
            cells = [f'<td class="hm-site">{esc(site)} <span class="hm-demand">({needed})</span></td>']
            for wk in range(1, total_weeks + 1):
                actual = len(site_week_staff.get((site, wk, dtype), set()))
                diff = actual - needed
                if diff == 0:
                    cls = "hm-ok"
                elif diff < 0:
                    # Shortfall — understaffed is the real concern
                    cls = "hm-warn" if diff == -1 else "hm-bad"
                else:
                    # Overfill — extra providers, less alarming
                    cls = "hm-over1" if diff == 1 else "hm-over2"
                # Show diff as superscript if non-zero
                diff_str = f'<sup class="hm-diff">{diff:+d}</sup>' if diff != 0 else ""
                providers = sorted(site_week_staff.get((site, wk, dtype), set()))
                tooltip = ", ".join(providers) if providers else "none"
                cells.append(f'<td class="{cls}" title="{esc(tooltip)}">{actual}{diff_str}</td>')
            body_rows.append(f"<tr>{''.join(cells)}</tr>")

        return (
            f'<h4>{label}</h4>'
            f'<div class="hm-scroll"><table class="hm-table">'
            f'<thead><tr><th class="hm-site-hdr">Site (demand)</th>{"".join(week_headers)}</tr></thead>'
            f'<tbody>{"".join(body_rows)}</tbody></table></div>'
        )

    weekday_map = build_heatmap("weekday", "Weekday Staffing")
    weekend_map = build_heatmap("weekend", "Weekend Staffing")

    legend = (
        '<div class="hm-legend">'
        '<span class="hm-legend-item"><span class="hm-swatch hm-bad"></span> Short 2+</span>'
        '<span class="hm-legend-item"><span class="hm-swatch hm-warn"></span> Short 1</span>'
        '<span class="hm-legend-item"><span class="hm-swatch hm-ok"></span> On target</span>'
        '<span class="hm-legend-item"><span class="hm-swatch hm-over1"></span> Over 1</span>'
        '<span class="hm-legend-item"><span class="hm-swatch hm-over2"></span> Over 2+</span>'
        '<span class="hm-legend-item" style="margin-left:16px;">Hover cells for provider names. Superscript = deviation.</span>'
        '</div>'
    )

    return (
        f"<p>{badge(str(total_short) + ' shortfall weeks', 'red')} "
        f"{badge(str(total_over) + ' overfill weeks', 'yellow')} "
        f"{badge(str(total_ok) + ' on-target weeks', 'green')}</p>"
        f"{legend}{weekday_map}{weekend_map}"
    )


def render_check3(dist_issues, prov_day_map, providers, day_assignments, override_info=None):
    if override_info is None:
        override_info = {}

    if not dist_issues and not override_info:
        return "<p class='pass'>✅ All providers within 20% of their target distribution.</p>"

    # Pre-compute actual days by provider x site-group using UNIQUE dates (dedup multi-service days)
    prov_group_dates = defaultdict(lambda: defaultdict(set))   # provider -> pct_field -> set of dates
    prov_group_sites = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))  # provider -> pct_field -> site -> set of dates
    prov_all_dates = defaultdict(set)
    for a in day_assignments:
        pct_field = SITE_PCT_MAP.get(a["site"])
        if pct_field:
            prov_group_dates[a["provider"]][pct_field].add(a["date"])
            prov_group_sites[a["provider"]][pct_field][a["site"]].add(a["date"])
            prov_all_dates[a["provider"]].add(a["date"])

    # All pct fields in display order
    all_pct_fields = [
        ("pct_cooper", "Cooper"),
        ("pct_inspira_veb", "Inspira VEB"),
        ("pct_inspira_mhw", "Mullica Hill"),
        ("pct_mannington", "Mannington"),
        ("pct_virtua", "Virtua"),
        ("pct_cape", "Cape"),
    ]

    by_prov = defaultdict(list)
    for d in dist_issues:
        by_prov[d["provider"]].append(d)

    # Also include providers with overrides but no violations (for display)
    all_prov_names = set(by_prov.keys()) | set(override_info.keys())

    items = []
    override_only_items = []  # providers with overrides but no violations

    for pname in sorted(all_prov_names):
        dlist = by_prov.get(pname, [])
        pdata = providers.get(pname, {})
        total = len(prov_all_dates.get(pname, set()))
        has_override = pname in override_info and override_info[pname].get("overrides")
        ov_info = override_info.get(pname, {})

        # Get effective pcts (overridden or raw)
        if has_override:
            effective_pcts = ov_info["effective"]
        else:
            effective_pcts = {f: pdata.get(f, 0) for f in PCT_FIELDS}

        # Full allocation table: ALL site groups with target vs actual
        alloc_rows = []
        for pct_field, label in all_pct_fields:
            target = effective_pcts.get(pct_field, 0)
            annual_target = pdata.get(pct_field, 0)
            is_overridden = has_override and pct_field in ov_info["overrides"]
            actual_days = len(prov_group_dates.get(pname, {}).get(pct_field, set()))
            actual_pct = actual_days / total if total > 0 else 0
            diff = actual_pct - target

            # Sub-site breakdown
            site_date_map = prov_group_sites.get(pname, {}).get(pct_field, {})
            sub_sites = [(site, len(dates)) for site, dates in site_date_map.items()]
            if len(sub_sites) > 1:
                breakdown = " (" + ", ".join(f"{s} {c}" for s, c in sorted(sub_sites)) + ")"
            else:
                breakdown = ""

            # Target display: show override annotation
            if is_overridden:
                target_display = f"{target:.0%} <span style='color:#999;font-size:0.85em'>(annual: {annual_target:.0%})</span>"
            else:
                target_display = f"{target:.0%}"

            # Color based on deviation
            if target == 0 and actual_days == 0:
                cls = ""  # not applicable
            elif abs(diff) > 0.20:
                cls = ' class="overfill"' if diff > 0 else ' class="shortage"'
            elif abs(diff) > 0.10:
                cls = ' class="weekend-row"'  # mild highlight
            else:
                cls = ""

            if target == 0 and actual_days == 0:
                alloc_rows.append(
                    f"<tr style='color:#aaa'><td>{esc(label)}</td>"
                    f"<td class='num'>—</td><td class='num'>—</td>"
                    f"<td class='num'>—</td><td>0 days</td></tr>"
                )
            else:
                alloc_rows.append(
                    f"<tr{cls}><td>{esc(label)}{esc(breakdown)}</td>"
                    f"<td class='num'>{actual_pct:.0%}</td><td class='num'>{target_display}</td>"
                    f"<td class='num'>{diff:+.0%}</td>"
                    f"<td>{actual_days}/{total} days</td></tr>"
                )

        # Override and warning badges
        override_badges = ""
        if has_override:
            overrides_desc = ", ".join(
                f"{f.replace('pct_', '')}: {v:.0%}" for f, v in ov_info["overrides"].items()
            )
            override_badges += f' {badge("overridden", "blue")}'
            if ov_info.get("warning"):
                override_badges += f' {badge(ov_info["warning"], "yellow")}'
            if ov_info.get("parse_warnings"):
                for w in ov_info["parse_warnings"]:
                    override_badges += f' {badge(w, "yellow")}'

        alloc_table = (
            '<h5 style="margin:8px 0 4px;">Full site allocation (all groups):</h5>'
            '<table class="detail-table"><thead><tr><th>Site Group</th><th>Actual</th><th>Target</th><th>Diff</th><th>Days</th></tr></thead>'
            f"<tbody>{''.join(alloc_rows)}</tbody></table>"
        )

        # Flagged deviations (>20%) — the original violation list
        if dlist:
            flag_rows = []
            for d in sorted(dlist, key=lambda x: abs(x["diff_pct"]), reverse=True):
                cls = "overfill" if d["diff_pct"] > 0 else "shortage"
                flag_rows.append(
                    f"<tr class='{cls}'><td>{esc(d['site'])}</td>"
                    f"<td>{d['actual_pct']:.0%}</td><td>{d['target_pct']:.0%}</td>"
                    f"<td class='num'>{d['diff_pct']:+.0%}</td>"
                    f"<td>{d['actual_days']}/{d['total_days']} days</td></tr>"
                )
            flag_table = (
                '<h5 style="margin:8px 0 4px;color:var(--yellow);">Flagged deviations (&gt;20%):</h5>'
                '<table class="detail-table"><thead><tr><th>Site Group</th><th>Actual</th><th>Target</th><th>Diff</th><th>Days</th></tr></thead>'
                f"<tbody>{''.join(flag_rows)}</tbody></table>"
            )
        else:
            flag_table = '<p style="color:var(--green);margin:8px 0;">No deviations &gt;20% (within tolerance after override)</p>'

        # Full Block 3 schedule for context
        all_dates = sorted(prov_day_map.get(pname, {}).keys())
        schedule_table = render_date_table(prov_day_map, pname, all_dates)
        full_schedule = collapsible(
            f"Full Block 3 schedule ({len(all_dates)} days)",
            schedule_table
        )

        detail = alloc_table + flag_table + full_schedule

        if dlist:
            worst = max(dlist, key=lambda x: abs(x["diff_pct"]))
            summary = f"<strong>{esc(pname)}</strong>{override_badges} &mdash; worst deviation: {worst['diff_pct']:+.0%} at {esc(worst['site'])}"
            items.append(collapsible(summary, detail))
        elif has_override:
            summary = f"<strong>{esc(pname)}</strong>{override_badges} &mdash; within tolerance"
            override_only_items.append(collapsible(summary, detail))

    # Sort violation items by worst deviation
    prov_worst = {}
    for d in dist_issues:
        p = d["provider"]
        if p not in prov_worst or abs(d["diff_pct"]) > abs(prov_worst[p]):
            prov_worst[p] = d["diff_pct"]
    items.sort(key=lambda html: -abs(prov_worst.get(
        html.split("<strong>")[1].split("</strong>")[0] if "<strong>" in html else "", 0)))

    result_parts = []

    if dist_issues:
        result_parts.append(
            f"<p>{badge(str(len(set(d['provider'] for d in dist_issues))) + ' providers', 'yellow')} "
            f"with &gt;20% deviation ({len(dist_issues)} site/provider combos)</p>"
        )
    else:
        result_parts.append("<p class='pass'>✅ All providers within 20% of their target distribution.</p>")

    if override_info:
        override_count = sum(1 for info in override_info.values() if info.get("overrides"))
        if override_count:
            result_parts.append(
                f"<p>{badge(str(override_count) + ' overridden', 'blue')} "
                f"providers using pct_override tags for block-level targets</p>"
            )

    result_parts.extend(items)
    if override_only_items:
        result_parts.append('<h4 style="margin-top:16px;">Overridden providers (within tolerance):</h4>')
        result_parts.extend(override_only_items)

    return "\n".join(result_parts)


def render_check4(cap_violations, prov_day_map, day_assignments):
    if not cap_violations:
        return "<p class='pass'>✅ No annual capacity violations found.</p>"

    # Group by provider
    by_prov = defaultdict(list)
    for v in cap_violations:
        by_prov[v["provider"]].append(v)

    # Build per-provider week lists for detail
    prov_week_nums = defaultdict(set)
    prov_we_nums = defaultdict(set)
    for a in day_assignments:
        wk = get_week_num(a["date"])
        if is_weekend_day(a["date"]):
            prov_we_nums[a["provider"]].add(wk)
        else:
            prov_week_nums[a["provider"]].add(wk)

    items = []
    for pname in sorted(by_prov, key=lambda p: max(v["over"] for v in by_prov[p]), reverse=True):
        vlist = by_prov[pname]

        # Summary row for each type
        type_parts = []
        for v in sorted(vlist, key=lambda x: x["over"], reverse=True):
            over_val = v['over']
            over_badge = badge(f'+{over_val:.1f} over', 'red')
            type_parts.append(
                f"{v['type']}: {v['prior_b1b2']:.1f} (B1+B2) + {v['block3']} (B3) = {v['total']:.1f} vs {v['annual']:.1f} annual "
                f"→ {over_badge}"
            )

        # Detail: all Block 3 dates
        all_dates = sorted(set(d for d in prov_day_map.get(pname, {}).keys()))
        date_table = render_date_table(prov_day_map, pname, all_dates) if all_dates else "<p><em>No Block 3 assignments found in parsed data.</em></p>"

        # Week summary
        weekday_weeks = sorted(prov_week_nums.get(pname, set()))
        weekend_weeks = sorted(prov_we_nums.get(pname, set()))
        week_info = f"<p>Weekday weeks ({len(weekday_weeks)}): {', '.join(str(w) for w in weekday_weeks)}</p>"
        week_info += f"<p>Weekend weeks ({len(weekend_weeks)}): {', '.join(str(w) for w in weekend_weeks)}</p>"

        detail = "<br>".join(type_parts) + week_info + date_table
        worst = max(vlist, key=lambda x: x["over"])
        worst_over = worst['over']
        worst_badge = badge(f'+{worst_over:.1f} over', 'red')
        summary = f"<strong>{esc(pname)}</strong> &mdash; {worst_badge} ({worst['type']})"
        items.append(collapsible(summary, detail))

    # Categorize
    big = [v for v in cap_violations if v["over"] >= 2.0]
    small = [v for v in cap_violations if v["over"] < 2.0]
    return (
        f"<p>{badge(str(len(cap_violations)) + ' violations', 'red')} across {len(by_prov)} providers</p>"
        f"<p class='hint'>Note: Many small overages (+0.2 to +1.0) are likely due to fractional prior_weeks_worked values from partial-week assignments. "
        f"Significant overages (&ge;2.0): <strong>{len(big)}</strong>. Minor (&lt;2.0): <strong>{len(small)}</strong>.</p>"
    ) + "\n".join(items)


def render_check5(hard_stretches, extended_stretches, window_violations, prov_day_map):
    parts = []

    # Hard violations (>12 days)
    if hard_stretches:
        items = []
        for v in sorted(hard_stretches, key=lambda x: x["days"], reverse=True):
            dates = [v["start"] + timedelta(days=i) for i in range(v["days"])]
            date_table = render_date_table(prov_day_map, v["provider"], dates)
            summary = (
                f"<strong>{esc(v['provider'])}</strong> &mdash; "
                f"{badge(str(v['days']) + ' consecutive days', 'red')} "
                f"{fmt_date(v['start'])} &ndash; {fmt_date(v['end'])}"
            )
            items.append(collapsible(summary, date_table))
        parts.append(
            f"<h4>{badge(str(len(hard_stretches)) + ' HARD violations', 'red')} (&gt;12 consecutive days)</h4>"
            + "\n".join(items)
        )
    else:
        parts.append("<h4>✅ No &gt;12 consecutive day violations.</h4>")

    # Extended (8-12 days)
    if extended_stretches:
        items = []
        for v in sorted(extended_stretches, key=lambda x: x["days"], reverse=True):
            dates = [v["start"] + timedelta(days=i) for i in range(v["days"])]
            date_table = render_date_table(prov_day_map, v["provider"], dates)
            summary = (
                f"<strong>{esc(v['provider'])}</strong> &mdash; "
                f"{badge(str(v['days']) + ' days', 'yellow')} "
                f"{fmt_date(v['start'])} &ndash; {fmt_date(v['end'])}"
            )
            items.append(collapsible(summary, date_table))
        parts.append(
            f"<h4>{badge(str(len(extended_stretches)) + ' extended stretches', 'yellow')} (8-12 days, acceptable but notable)</h4>"
            + "\n".join(items)
        )
    else:
        parts.append("<h4>✅ No extended stretches (8-12 days).</h4>")

    # 21-day window
    if window_violations:
        by_prov = defaultdict(list)
        for v in window_violations:
            by_prov[v["provider"]].append(v)

        items = []
        for pname, vlist in sorted(by_prov.items()):
            worst = max(vlist, key=lambda x: x["days_worked"])
            # Show all dates in the worst window
            all_dates_in_window = []
            for dd in range((worst["window_end"] - worst["window_start"]).days + 1):
                check_d = worst["window_start"] + timedelta(days=dd)
                if check_d in set(d for d in prov_day_map.get(pname, {}).keys()):
                    all_dates_in_window.append(check_d)
            date_table = render_date_table(prov_day_map, pname, all_dates_in_window)
            summary = (
                f"<strong>{esc(pname)}</strong> &mdash; "
                f"{badge(str(worst['days_worked']) + '/21 days', 'yellow')} "
                f"in window {fmt_date(worst['window_start'])} &ndash; {fmt_date(worst['window_end'])}"
            )
            items.append(collapsible(summary, date_table))
        parts.append(
            f"<h4>{badge(str(len(by_prov)) + ' providers', 'yellow')} with 21-day window violations (&gt;17 days worked)</h4>"
            + "\n".join(items)
        )
    else:
        parts.append("<h4>✅ No 21-day window violations.</h4>")

    return "\n".join(parts)


def render_check6(avail_violations, prov_day_map):
    if not avail_violations:
        return "<p class='pass'>✅ No availability violations found.</p>"

    by_prov = defaultdict(list)
    for v in avail_violations:
        by_prov[v["provider"]].append(v)

    items = []
    for pname, vlist in sorted(by_prov.items()):
        # Violations table (highlighted)
        violation_dates = set(v["date"] for v in vlist)
        detail_rows = []
        for v in sorted(vlist, key=lambda x: x["date"]):
            note_str = f' <span class="note-text">({esc(v["note"])})</span>' if v["note"] else ""
            has_swap = any(kw in (v.get("note","") or "").lower() for kw in ["swap","switch","trade","payback","covering"])
            swap_badge = ' ' + badge("swap note", "blue") if has_swap else ""
            we_cls = ' class="weekend-row"' if is_weekend_day(v["date"]) else ""
            # Show all services if multiple on same day (merged by validate_block3)
            services = v.get("services", [v["service"]])
            svc_text = " + ".join(esc(s) for s in services)
            detail_rows.append(
                f"<tr{we_cls}><td>{fmt_date(v['date'])}</td><td>{v['date']}</td>"
                f"<td>{esc(v['site'])}</td><td>{svc_text}{note_str}{swap_badge}</td></tr>"
            )
        violation_table = (
            '<h5 style="margin:8px 0 4px;color:var(--red);">Unavailable dates where provider was scheduled:</h5>'
            '<table class="detail-table"><thead><tr><th>Day</th><th>Date</th><th>Site</th><th>Service</th></tr></thead>'
            f"<tbody>{''.join(detail_rows)}</tbody></table>"
        )

        # Full Block 3 schedule for context
        all_dates = sorted(prov_day_map.get(pname, {}).keys())
        schedule_table = render_date_table(prov_day_map, pname, all_dates)
        full_schedule = collapsible(
            f"Full Block 3 schedule ({len(all_dates)} days &mdash; violation dates are in the list above)",
            schedule_table
        )

        detail = violation_table + full_schedule
        swap_count = sum(1 for v in vlist if any(kw in (v.get("note","") or "").lower() for kw in ["swap","switch","trade","payback","covering"]))
        swap_info = f" ({swap_count} have swap notes)" if swap_count else ""
        summary = f"<strong>{esc(pname)}</strong> &mdash; {badge(str(len(vlist)) + ' violations', 'red')}{esc(swap_info)}"
        items.append(collapsible(summary, detail))

    with_swaps = sum(1 for v in avail_violations if any(kw in (v.get("note","") or "").lower() for kw in ["swap","switch","trade","payback","covering"]))
    return (
        f"<p>{badge(str(len(avail_violations)) + ' violations', 'red')} across {len(by_prov)} providers"
        f" &mdash; {with_swaps} have swap/modification notes that may explain the conflict</p>"
    ) + "\n".join(items)


def render_check7(conflict_violations):
    if not conflict_violations:
        return "<p class='pass'>✅ Haroldson &amp; McMillian never overlap. No conflict pair violations.</p>"
    rows = []
    for v in conflict_violations:
        ws, we = week_date_range(v["week"])
        rows.append(
            f"<tr><td>Week {v['week']}</td><td>{ws.strftime('%m/%d')} &ndash; {we.strftime('%m/%d')}</td>"
            f"<td>{', '.join(v['haroldson_sites'])}</td><td>{', '.join(v['mcmillian_sites'])}</td></tr>"
        )
    return (
        f"<p>{badge(str(len(conflict_violations)) + ' weeks with both scheduled', 'red')}</p>"
        '<table class="detail-table"><thead><tr><th>Week</th><th>Dates</th><th>Haroldson Sites</th><th>McMillian Sites</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_check8(pairing_mismatches, total_pairs, prov_day_map, day_assignments):
    matched = total_pairs - len(pairing_mismatches)
    match_pct = (matched / total_pairs * 100) if total_pairs > 0 else 100

    summary_html = (
        f"<p>{matched}/{total_pairs} pairs ({match_pct:.0f}%) at the same site. "
        f"{badge(str(len(pairing_mismatches)) + ' cross-site', 'yellow' if pairing_mismatches else 'green')}</p>"
    )

    if not pairing_mismatches:
        return summary_html + "<p class='pass'>✅ All week/weekend pairs at the same site.</p>"

    items = []
    for m in sorted(pairing_mismatches, key=lambda x: (x["provider"], x["week"])):
        ws, we = week_date_range(m["week"])
        # Get the actual dates for this provider in this week
        wk_dates = [a["date"] for a in day_assignments if a["provider"] == m["provider"] and get_week_num(a["date"]) == m["week"]]
        date_table = render_date_table(prov_day_map, m["provider"], wk_dates)
        summary = (
            f"<strong>{esc(m['provider'])}</strong> Week {m['week']} "
            f"({ws.strftime('%m/%d')}&ndash;{we.strftime('%m/%d')}): "
            f"{', '.join(m['weekday_sites'])} → {', '.join(m['weekend_sites'])}"
        )
        items.append(collapsible(summary, date_table))

    return summary_html + "\n".join(items)


def render_check9(multi_site_weeks):
    if not multi_site_weeks:
        return "<p class='pass'>✅ All providers at a single site per week (Mon-Fri). No violations.</p>"
    rows = []
    for v in multi_site_weeks:
        ws, we = week_date_range(v["week"])
        rows.append(
            f"<tr><td>{esc(v['provider'])}</td><td>Week {v['week']}</td>"
            f"<td>{ws.strftime('%m/%d')} &ndash; {we.strftime('%m/%d')}</td>"
            f"<td>{', '.join(v['sites'])}</td></tr>"
        )
    return (
        f"<p>{badge(str(len(multi_site_weeks)) + ' violations', 'red')}</p>"
        '<table class="detail-table"><thead><tr><th>Provider</th><th>Week</th><th>Dates</th><th>Sites</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_check10(memorial_workers, pref_violations, prov_day_map):
    items = []
    if pref_violations:
        for v in pref_violations:
            # Show their Memorial Day week dates
            mem_dates = [d for d in prov_day_map.get(v["provider"], {}).keys()
                         if MEMORIAL_DAY_WEEK_START <= d <= MEMORIAL_DAY_WEEK_END]
            date_table = render_date_table(prov_day_map, v["provider"], mem_dates)
            summary = f"<strong>{esc(v['provider'])}</strong> &mdash; listed Memorial Day as preference but is {badge('WORKING', 'red')}"
            items.append(collapsible(summary, date_table))
        pref_html = "\n".join(items)
    else:
        pref_html = "<p class='pass'>✅ No Memorial Day preference violations.</p>"

    return (
        f"<p>{len(memorial_workers)} providers working Memorial Day week (May 25&ndash;29, 2026).</p>"
        f"<h4>Preference Violations</h4>{pref_html}"
    )


def render_check11(swing_issues):
    if not swing_issues:
        return "<p class='pass'>No swing-tagged providers found.</p>"
    rows = []
    for s in swing_issues:
        over = s["weeks_assigned"] > s["weeks_cap"]
        cls = ' class="overfill"' if over else ""
        over_badge = f' {badge("over cap", "red")}' if over else ""
        rows.append(
            f"<tr{cls}><td>{esc(s['provider'])}</td><td>{esc(s['swing_rule'])}</td>"
            f"<td class='num'>{s['weeks_assigned']}</td><td class='num'>{s['weeks_cap']}</td>"
            f"<td>{over_badge}</td></tr>"
        )
    return (
        f"<p>{len(swing_issues)} swing-tagged providers:</p>"
        '<table class="detail-table"><thead><tr><th>Provider</th><th>Swing Rule</th><th>Weeks Assigned</th><th>Weeks Cap</th><th></th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_check12(swaps, other_notes):
    parts = []

    if swaps:
        rows = []
        for s in sorted(swaps, key=lambda x: x["date"]):
            rows.append(
                f"<tr><td>{fmt_date(s['date'])}</td><td>{s['date']}</td><td>{esc(s['provider'])}</td>"
                f"<td>{esc(s['site'])}</td><td>{esc(s['service'])}</td><td>{esc(s['note'])}</td></tr>"
            )
        table = (
            '<table class="detail-table"><thead><tr><th>Day</th><th>Date</th><th>Provider</th><th>Site</th><th>Service</th><th>Note</th></tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
        parts.append(
            f"<h4>Swaps / Paybacks / Covers / Switches ({len(swaps)})</h4>"
            + collapsible(f"Show all {len(swaps)} swap notes", table)
        )
    else:
        parts.append("<h4>No swap/modification notes found.</h4>")

    if other_notes:
        rows = []
        for n in sorted(other_notes, key=lambda x: x["date"]):
            rows.append(
                f"<tr><td>{fmt_date(n['date'])}</td><td>{n['date']}</td><td>{esc(n['provider'])}</td>"
                f"<td>{esc(n['site'])}</td><td>{esc(n['service'])}</td><td>{esc(n['note'])}</td></tr>"
            )
        table = (
            '<table class="detail-table"><thead><tr><th>Day</th><th>Date</th><th>Provider</th><th>Site</th><th>Service</th><th>Note</th></tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
        parts.append(
            f"<h4>Other Notes ({len(other_notes)})</h4>"
            + collapsible(f"Show all {len(other_notes)} other notes", table)
        )

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# HTML WRAPPER
# ═══════════════════════════════════════════════════════════════════════════

def wrap_html(body):
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Block 3 Validation Report</title>
<style>
  :root {{
    --bg: #f8f9fa; --card-bg: #fff; --border: #dee2e6;
    --text: #212529; --text-muted: #6c757d;
    --red: #dc3545; --red-bg: #f8d7da; --red-border: #f5c2c7;
    --yellow: #ffc107; --yellow-bg: #fff3cd; --yellow-border: #ffecb5;
    --green: #198754; --green-bg: #d1e7dd; --green-border: #badbcc;
    --blue: #0d6efd; --blue-bg: #cfe2ff; --blue-border: #b6d4fe;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.5;
    max-width: 1200px; margin: 0 auto; padding: 20px;
  }}
  h1 {{ font-size: 1.8rem; margin-bottom: 4px; }}
  h2 {{
    font-size: 1.35rem; margin: 30px 0 12px; padding: 10px 14px;
    background: #343a40; color: #fff; border-radius: 6px;
    position: sticky; top: 0; z-index: 10;
  }}
  h2 .check-num {{ opacity: 0.7; font-weight: 400; }}
  h3 {{ font-size: 1.1rem; margin: 16px 0 8px; color: #495057; }}
  h4 {{ font-size: 1rem; margin: 14px 0 6px; }}
  p {{ margin: 6px 0; }}
  .subtitle {{ color: var(--text-muted); font-size: 0.9rem; margin-bottom: 20px; }}
  .scope-note {{ color: var(--text-muted); font-size: 0.85rem; }}
  .hint {{ color: var(--text-muted); font-size: 0.85rem; font-style: italic; }}
  .pass {{ color: var(--green); font-weight: 600; }}

  /* Overview grid */
  .overview-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 12px; margin: 16px 0;
  }}
  .ov-card {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px; text-align: center;
  }}
  .ov-num {{ font-size: 1.8rem; font-weight: 700; color: var(--blue); }}
  .ov-label {{ font-size: 0.8rem; color: var(--text-muted); margin-top: 4px; }}

  /* Badges */
  .badge {{
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 0.78rem; font-weight: 600; white-space: nowrap;
  }}
  .badge-red {{ background: var(--red-bg); color: var(--red); border: 1px solid var(--red-border); }}
  .badge-yellow {{ background: var(--yellow-bg); color: #856404; border: 1px solid var(--yellow-border); }}
  .badge-green {{ background: var(--green-bg); color: var(--green); border: 1px solid var(--green-border); }}
  .badge-blue {{ background: var(--blue-bg); color: var(--blue); border: 1px solid var(--blue-border); }}

  /* Summary table */
  .summary-table {{ width: 100%; border-collapse: collapse; margin: 8px 0; }}
  .summary-table th, .summary-table td {{ padding: 6px 10px; border-bottom: 1px solid var(--border); text-align: left; font-size: 0.9rem; }}
  .summary-table th {{ background: #f1f3f5; font-weight: 600; }}
  .summary-table .num {{ text-align: right; font-variant-numeric: tabular-nums; }}

  /* Detail tables */
  .detail-table {{ width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 0.82rem; }}
  .detail-table th, .detail-table td {{ padding: 4px 8px; border: 1px solid #e9ecef; text-align: left; }}
  .detail-table th {{ background: #f1f3f5; font-weight: 600; position: sticky; top: 40px; }}
  .detail-table .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .detail-table .weekend-row {{ background: #f0f0ff; }}
  .detail-table .shortage {{ background: var(--red-bg); }}
  .detail-table .overfill {{ background: var(--yellow-bg); }}
  .detail-table .violation-site {{ color: var(--red); font-weight: 600; }}
  .note-text {{ color: var(--blue); font-size: 0.78rem; }}

  /* Heatmap table (Check 2) */
  .hm-scroll {{
    overflow-x: auto; margin: 8px 0; -webkit-overflow-scrolling: touch;
    position: relative;
  }}
  .hm-scroll::after {{
    content: "→ scroll →"; display: none; position: absolute;
    top: 4px; right: 8px; font-size: 0.7rem; color: var(--text-muted);
    background: rgba(255,255,255,0.85); padding: 2px 8px; border-radius: 10px;
    pointer-events: none;
  }}
  .hm-table {{ border-collapse: collapse; font-size: 0.8rem; white-space: nowrap; }}
  .hm-table th, .hm-table td {{ padding: 5px 8px; border: 1px solid #dee2e6; text-align: center; }}
  .hm-site-hdr {{ text-align: left !important; min-width: 160px; background: #f1f3f5; font-weight: 600; position: sticky; left: 0; z-index: 2; }}
  .hm-site {{ text-align: left !important; font-weight: 600; background: #fff; position: sticky; left: 0; z-index: 1; min-width: 160px; }}
  .hm-demand {{ font-weight: 400; color: var(--text-muted); font-size: 0.75rem; }}
  .hm-week {{ background: #f1f3f5; font-weight: 600; font-size: 0.75rem; min-width: 48px; }}
  .hm-ok {{ background: #d4edda; color: #155724; font-weight: 600; }}
  .hm-warn {{ background: #fff3cd; color: #856404; font-weight: 600; }}
  .hm-bad {{ background: #f8d7da; color: #721c24; font-weight: 700; }}
  .hm-over1 {{ background: #d6eaf8; color: #1a5276; font-weight: 600; }}
  .hm-over2 {{ background: #aed6f1; color: #154360; font-weight: 700; }}
  .hm-diff {{ font-size: 0.65rem; font-weight: 400; margin-left: 1px; }}
  .hm-legend {{ display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin: 8px 0; font-size: 0.8rem; }}
  .hm-legend-item {{ display: flex; align-items: center; gap: 4px; }}
  .hm-swatch {{ display: inline-block; width: 16px; height: 16px; border-radius: 3px; border: 1px solid #ccc; }}
  .hm-swatch.hm-ok {{ background: #d4edda; }}
  .hm-swatch.hm-warn {{ background: #fff3cd; }}
  .hm-swatch.hm-bad {{ background: #f8d7da; }}
  .hm-swatch.hm-over1 {{ background: #d6eaf8; }}
  .hm-swatch.hm-over2 {{ background: #aed6f1; }}

  /* Collapsible details */
  details {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px;
    margin: 6px 0; overflow: hidden;
  }}
  summary {{
    padding: 10px 14px; cursor: pointer; font-size: 0.9rem;
    background: #f8f9fa; border-bottom: 1px solid transparent;
    list-style: none;
  }}
  summary::-webkit-details-marker {{ display: none; }}
  summary::before {{ content: "▶ "; font-size: 0.7rem; color: var(--text-muted); }}
  details[open] > summary {{ border-bottom: 1px solid var(--border); }}
  details[open] > summary::before {{ content: "▼ "; }}
  .detail-body {{ padding: 10px 14px; }}

  /* Section card */
  .section-card {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px; margin-bottom: 20px;
  }}

  /* Mobile */
  @media (max-width: 768px) {{
    body {{ padding: 10px; font-size: 13px; }}
    h1 {{ font-size: 1.4rem; }}
    h2 {{ font-size: 1.1rem; padding: 8px 10px; position: sticky; top: 0; z-index: 10; }}
    .overview-grid {{ grid-template-columns: repeat(2, 1fr); gap: 8px; }}
    .ov-num {{ font-size: 1.4rem; }}
    .section-card {{ padding: 10px; }}
    summary {{ padding: 8px 10px; font-size: 0.85rem; }}
    .detail-body {{ padding: 8px 10px; }}
    .detail-table th, .detail-table td {{ padding: 3px 5px; font-size: 0.75rem; }}

    /* Heatmap: smaller cells on mobile */
    .hm-table {{ font-size: 0.7rem; }}
    .hm-table th, .hm-table td {{ padding: 3px 4px; }}
    .hm-site-hdr, .hm-site {{ min-width: 120px; font-size: 0.72rem; }}
    .hm-week {{ min-width: 36px; font-size: 0.65rem; }}
    .hm-scroll::after {{ display: block; }}
    .hm-scroll.scrolled::after {{ display: none; }}

    /* Allocation tables in Check 3 */
    .summary-table th, .summary-table td {{ padding: 4px 6px; font-size: 0.8rem; }}

    /* Legend wraps better */
    .hm-legend {{ font-size: 0.72rem; gap: 8px; }}
  }}

  /* Print */
  @media print {{
    details {{ break-inside: avoid; }}
    h2 {{ position: static; }}
  }}

  /* Expand / collapse all */
  .controls {{ margin: 10px 0; }}
  .controls button {{
    padding: 5px 14px; margin-right: 8px; border: 1px solid var(--border);
    border-radius: 4px; background: var(--card-bg); cursor: pointer;
    font-size: 0.82rem;
  }}
  .controls button:hover {{ background: #e9ecef; }}
</style>
</head>
<body>
<h1>Block 3 Schedule Validation Report</h1>
<p class="subtitle">Generated {ts} &mdash; Day shifts only (night/swing out of scope per Section 6.3)</p>
<div class="controls">
  <button onclick="document.querySelectorAll('details').forEach(d=>d.open=true)">Expand All</button>
  <button onclick="document.querySelectorAll('details').forEach(d=>d.open=false)">Collapse All</button>
</div>
{body}
<script>
// Hide scroll hint after first horizontal scroll
document.querySelectorAll('.hm-scroll').forEach(el => {{
  el.addEventListener('scroll', function handler() {{
    el.classList.add('scrolled');
    el.removeEventListener('scroll', handler);
  }});
}});
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print("Loading data...")
    providers = load_providers()
    tags_data = load_tags()
    sites_demand = load_sites()
    unavailable_dates = load_availability()
    name_map, unmatched = build_name_map(providers, unavailable_dates)

    prior_actuals_path = os.path.join(_PROJECT_ROOT, "output", "prior_actuals.json")
    with open(prior_actuals_path) as f:
        prior_actuals = json.load(f)

    print("Parsing Block 3 schedule...")
    all_assignments = parse_block3()
    day = filter_day_only(all_assignments)
    prov_day_map = build_provider_day_map(day)

    print("Running checks...")

    # Run all 12 checks
    elig_violations = check_site_eligibility(day, providers, tags_data)
    demand_issues = check_site_demand(day, sites_demand)
    dist_issues, override_info = check_provider_distribution(day, providers, tags_data)
    cap_violations = check_capacity_limits(day, providers, tags_data, prior_actuals)
    hard_stretches, extended_stretches, window_violations = check_consecutive_stretches(day, providers)
    avail_violations = check_availability(day, providers, unavailable_dates, name_map)
    conflict_violations = check_conflict_pairs(day, providers)
    pairing_mismatches, total_pairs = check_week_weekend_pairing(day, providers)
    multi_site_weeks = check_single_site_per_week(day, providers)
    memorial_workers, pref_violations = check_holiday_rules(day, providers, tags_data)
    swing_issues = check_swing_reservation(day, providers, tags_data)
    swaps, other_notes = extract_swap_notes(all_assignments)

    matched_pairs = total_pairs - len(pairing_mismatches)
    pair_str = f"{len(pairing_mismatches)} / {total_pairs}"

    # Build summary data
    checks = [
        {"name": "Site Eligibility", "count": len(elig_violations), "hard": True, "rule_ref": "Section 2.2, 2.3"},
        {"name": "Annual Capacity (B1+B2+B3 > annual)", "count": len(cap_violations), "hard": True, "rule_ref": "Section 2.4"},
        {"name": "Availability (sacred)", "count": len(avail_violations), "hard": True, "rule_ref": "Section 2.1"},
        {"name": "Conflict Pairs (Haroldson/McMillian)", "count": len(conflict_violations), "hard": True, "rule_ref": "Section 5.2"},
        {"name": "Consecutive >12 days", "count": len(hard_stretches), "hard": True, "rule_ref": "Section 3.2"},
        {"name": "Cross-site week/weekend", "count": pair_str, "hard": True, "rule_ref": "Section 2.5"},
        {"name": "Single site per week", "count": len(multi_site_weeks), "hard": True, "rule_ref": "Section 1.2"},
        {"name": "Site Demand Mismatches", "count": len(demand_issues), "hard": False, "rule_ref": "Input 3, Section 3.6"},
        {"name": "Distribution Deviations (>20%)", "count": len(dist_issues), "hard": False, "rule_ref": "Section 3.4"},
        {"name": "Extended Stretches (8-12 days)", "count": len(extended_stretches), "hard": False, "rule_ref": "Section 3.2"},
        {"name": "21-day Window Violations", "count": len(set(v["provider"] for v in window_violations)), "hard": False, "rule_ref": "Section 3.2"},
        {"name": "Holiday Preference Violations", "count": len(pref_violations), "hard": False, "rule_ref": "Section 4"},
        {"name": "Swing Capacity Providers", "count": len(swing_issues), "hard": False, "rule_ref": "Input 3"},
        {"name": "Swap/Modification Notes", "count": len(swaps), "hard": False, "rule_ref": "Informational"},
    ]

    # Build HTML sections
    print("Rendering HTML...")
    sections = []

    # Overview
    sections.append('<div class="section-card">')
    sections.append(render_overview(all_assignments, day, providers, tags_data))
    sections.append("</div>")

    # Summary
    sections.append('<h2>📋 Validation Summary</h2>')
    sections.append('<div class="section-card">')
    sections.append(render_summary_card(checks))
    sections.append("</div>")

    # Check 1
    sections.append('<h2><span class="check-num">Check 1</span> Site Eligibility <span class="badge badge-red" style="font-size:0.75rem">HARD</span></h2>')
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">Providers can only be assigned to sites where their allocation percentage &gt; 0. Tag restrictions (no_elmer, no_vineland) further remove sites.</p>')
    sections.append(render_check1(elig_violations, providers, tags_data, prov_day_map))
    sections.append("</div>")

    # Check 2
    sections.append('<h2><span class="check-num">Check 2</span> Site Demand</h2>')
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">Actual day-shift staffing vs expected demand per site per week. Cooper gaps are expected (Tier 2). Tier 0 sites should have zero gaps.</p>')
    sections.append(render_check2(demand_issues, sites_demand, day))
    sections.append("</div>")

    # Check 3
    sections.append('<h2><span class="check-num">Check 3</span> Provider Site Distribution <span class="badge badge-yellow" style="font-size:0.75rem">SOFT</span></h2>')
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">Provider site distribution vs percentage targets from the spreadsheet. Flags deviations &gt;20%. Some flexibility is expected (&plusmn;5-10%).</p>')
    sections.append(render_check3(dist_issues, prov_day_map, providers, day, override_info))
    sections.append("</div>")

    # Check 4
    sections.append('<h2><span class="check-num">Check 4</span> Annual Capacity <span class="badge badge-red" style="font-size:0.75rem">HARD</span></h2>')
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">Verifies that prior weeks worked (B1+B2) + Block 3 weeks does not exceed the annual allocation. We assume the manual scheduler checked weeks_remaining; this validates the total was not exceeded.</p>')
    sections.append(render_check4(cap_violations, prov_day_map, day))
    sections.append("</div>")

    # Check 5
    sections.append('<h2><span class="check-num">Check 5</span> Consecutive Stretches</h2>')
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">Normal: up to 7 consecutive days. Maximum: 12 consecutive days (Week+WE+Week). NEVER: more than 12. 21-day window: max 17 days worked in any 21-day window.</p>')
    sections.append(render_check5(hard_stretches, extended_stretches, window_violations, prov_day_map))
    sections.append("</div>")

    # Check 6
    sections.append('<h2><span class="check-num">Check 6</span> Availability <span class="badge badge-red" style="font-size:0.75rem">HARD</span></h2>')
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">Availability is SACRED. If a provider marks a day as unavailable, they are NEVER scheduled that day. Violations with swap notes indicate the schedule was modified after the baseline was set.</p>')
    sections.append(render_check6(avail_violations, prov_day_map))
    sections.append("</div>")

    # Check 7
    sections.append('<h2><span class="check-num">Check 7</span> Conflict Pairs <span class="badge badge-red" style="font-size:0.75rem">HARD</span></h2>')
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">Haroldson &amp; McMillian must never be scheduled during the same week or weekend at ANY site.</p>')
    sections.append(render_check7(conflict_violations))
    sections.append("</div>")

    # Check 8
    sections.append('<h2><span class="check-num">Check 8</span> Week/Weekend Same-Site Pairing <span class="badge badge-red" style="font-size:0.75rem">HARD</span></h2>')
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">When a provider works both weekday and weekend in the same week, both should be at the same site. Cross-site only as last resort.</p>')
    sections.append(render_check8(pairing_mismatches, total_pairs, prov_day_map, day))
    sections.append("</div>")

    # Check 9
    sections.append('<h2><span class="check-num">Check 9</span> Single Site Per Week <span class="badge badge-red" style="font-size:0.75rem">HARD</span></h2>')
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">A provider stays at ONE site for the entire week (Mon-Fri).</p>')
    sections.append(render_check9(multi_site_weeks))
    sections.append("</div>")

    # Check 10
    sections.append('<h2><span class="check-num">Check 10</span> Holiday Rules</h2>')
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">Memorial Day (May 25, 2026) is the only Block 3 holiday. Check if providers who listed Memorial Day as a preference are working.</p>')
    sections.append(render_check10(memorial_workers, pref_violations, prov_day_map))
    sections.append("</div>")

    # Check 11
    sections.append('<h2><span class="check-num">Check 11</span> Swing Capacity Reservation</h2>')
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">For swing-tagged providers, the engine must reserve capacity by leaving weeks unscheduled for swing duties.</p>')
    sections.append(render_check11(swing_issues))
    sections.append("</div>")

    # Check 12
    sections.append('<h2><span class="check-num">Check 12</span> Swap Notes &amp; Modifications</h2>')
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">Catalog of all swap, payback, cover, and switch notes found in the Amion HTML. These explain many deviations from the baseline schedule.</p>')
    sections.append(render_check12(swaps, other_notes))
    sections.append("</div>")

    body = "\n".join(sections)
    html = wrap_html(body)

    # Write output
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "block3_validation_report.html")
    with open(out_path, "w") as f:
        f.write(html)

    print(f"\nReport written to: {out_path}")
    print(f"File size: {os.path.getsize(out_path) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
