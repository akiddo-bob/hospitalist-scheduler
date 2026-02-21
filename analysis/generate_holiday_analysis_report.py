#!/usr/bin/env python3
"""
Pre-Scheduling Holiday Analysis Report

Analyzes Blocks 1+2 holiday history to guide Memorial Day (Block 3's only
holiday) assignment decisions.  For each provider, determines how many holidays
they're contractually required to work, which they already worked, and who
should be assigned Memorial Day.

No Block 3 schedule is required.

Usage:
    python -m analysis.generate_holiday_analysis_report
"""

import html as html_mod
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime

# ── Project root setup ──────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from parse_schedule import parse_schedule, merge_schedules
from name_match import to_canonical, clean_html_provider, match_provider
from block.recalculate_prior_actuals import classify_service, parse_date
from block.engines.shared.loader import (
    load_providers, load_tags, has_tag, load_availability, build_name_map,
)

OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")
MONTHLY_DIR = os.path.join(_PROJECT_ROOT, "input", "monthlySchedules")

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

# Cycle 25-26 holidays (docs/block-scheduling-rules.md Section 4.1)
HOLIDAYS = {
    "4th of July":    date(2025, 7, 4),
    "Labor Day":      date(2025, 9, 1),
    "Thanksgiving":   date(2025, 11, 27),
    "Christmas Day":  date(2025, 12, 25),
    "New Year's Day": date(2026, 1, 1),
    "Memorial Day":   date(2026, 5, 25),
}

BLOCK_1_HOLIDAYS = ["4th of July", "Labor Day"]
BLOCK_2_HOLIDAYS = ["Thanksgiving", "Christmas Day", "New Year's Day"]
BLOCK_3_HOLIDAYS = ["Memorial Day"]
PRIOR_HOLIDAYS = BLOCK_1_HOLIDAYS + BLOCK_2_HOLIDAYS  # B1+B2 only

# Memorial Day week (Mon-Fri)
MEMORIAL_WEEK_DATES = ["2026-05-25", "2026-05-26", "2026-05-27",
                       "2026-05-28", "2026-05-29"]

# Block boundaries (matching recalculate_prior_actuals.py)
BLOCK_1_START = date(2025, 6, 30)
BLOCK_1_END   = date(2025, 11, 2)
BLOCK_2_START = date(2025, 11, 3)
BLOCK_2_END   = date(2026, 3, 1)

# Monthly files covering Blocks 1+2
PRIOR_FILES = [
    "2025-06.html", "2025-07.html", "2025-08.html", "2025-09.html",
    "2025-10.html", "2025-11.html", "2025-12.html",
    "2026-01.html", "2026-02.html",
]

# Site directors get 2 holidays/year regardless of FTE (Section 1.4)
# We'll match these via to_canonical() substring
SITE_DIRECTOR_NAMES = [
    "MANGOLD, MELISSA", "MCMILLAN, TYLER", "HAROLDSON, KATHRYN",
    "GLICKMAN, CYNTHIA", "GROSS, MICHAEL",
    "OBERDORF, W. ERIC", "OLAYEMI, CHARLTON", "GAMBALE, JOSEPH",
]

# Tier ordering for display
TIER_ORDER = {"MUST": 0, "SHOULD": 1, "MET": 2, "UNAVAILABLE": 3, "EXEMPT": 4}
TIER_COLORS = {
    "MUST": "red", "SHOULD": "yellow", "MET": "green",
    "UNAVAILABLE": "blue", "EXEMPT": "blue",
}


# ═══════════════════════════════════════════════════════════════════════════
# HTML HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def esc(text):
    if text is None:
        return ""
    return html_mod.escape(str(text))


def badge(text, color):
    return f'<span class="badge badge-{color}">{esc(text)}</span>'


def collapsible(summary_html, detail_html, open_default=False):
    op = " open" if open_default else ""
    return (f'<details{op}><summary>{summary_html}</summary>'
            f'<div class="detail-body">{detail_html}</div></details>')


# ═══════════════════════════════════════════════════════════════════════════
# DATA LOADING & SCANNING
# ═══════════════════════════════════════════════════════════════════════════

def load_prior_schedules():
    """Parse B1+B2 monthly HTML files into merged schedule data."""
    all_months = []
    for fname in PRIOR_FILES:
        fpath = os.path.join(MONTHLY_DIR, fname)
        if not os.path.exists(fpath):
            print(f"  WARNING: Missing {fname}")
            continue
        month_data = parse_schedule(fpath)
        all_months.append(month_data)
        print(f"  Parsed {fname}: {len(month_data['schedule'])} days, "
              f"{len(month_data['services'])} services")
    return merge_schedules(all_months)


def scan_holiday_workers(merged):
    """Scan B1+B2 schedules to find who worked each holiday date.

    Returns: {holiday_name: set of canonical provider names}
    """
    # Pre-classify all services
    service_classes = {}
    for s in merged.get("services", []):
        service_classes[s["name"]] = classify_service(s["name"], s["hours"])

    holiday_workers = {name: set() for name in PRIOR_HOLIDAYS}

    for day_entry in merged["schedule"]:
        d = parse_date(day_entry["date"])
        if d is None:
            continue

        # Check if this date is a holiday
        matched_holiday = None
        for hol_name in PRIOR_HOLIDAYS:
            if d == HOLIDAYS[hol_name]:
                matched_holiday = hol_name
                break
        if matched_holiday is None:
            continue

        # Scan all assignments on this holiday
        for assignment in day_entry["assignments"]:
            provider = clean_html_provider(assignment["provider"])
            if not provider:
                continue
            if assignment.get("moonlighting", False):
                continue

            svc_class = service_classes.get(
                assignment["service"],
                classify_service(assignment["service"], assignment.get("hours", ""))
            )
            if svc_class == "exclude":
                continue

            canonical = to_canonical(provider)
            holiday_workers[matched_holiday].add(canonical)

    return holiday_workers


# ═══════════════════════════════════════════════════════════════════════════
# HOLIDAY RECORDS & PRIORITY
# ═══════════════════════════════════════════════════════════════════════════

def is_site_director(pname):
    """Check if provider is a site director (Section 1.4)."""
    pname_upper = pname.upper()
    for sd in SITE_DIRECTOR_NAMES:
        if sd.upper() in pname_upper or pname_upper in sd.upper():
            return True
    return False


def get_holiday_requirement(fte, site_dir):
    """Return number of holidays required per year (Section 4.2)."""
    if site_dir:
        return 2
    if fte >= 0.76:
        return 3
    elif fte >= 0.50:
        return 2
    elif fte > 0:
        return 1
    return 0


def check_memorial_day_availability(pname, availability, name_map):
    """Check if provider is available during Memorial Day week.

    Uses individual schedule JSONs. If no JSON, assume available.
    """
    json_name = name_map.get(pname)
    if json_name is None:
        return True  # no availability data = assume available

    unavail = availability.get(json_name, set())
    return not any(d in unavail for d in MEMORIAL_WEEK_DATES)


def build_holiday_records(providers, tags_data, holiday_workers,
                          availability, name_map):
    """Build per-provider holiday analysis records."""
    records = []

    for pname, pdata in sorted(providers.items()):
        if has_tag(pname, "do_not_schedule", tags_data):
            continue

        fte = pdata.get("fte", 0)
        annual_wk = pdata.get("annual_weeks", 0)
        annual_we = pdata.get("annual_weekends", 0)

        # Skip providers with 0 allocation (nocturnists, etc.)
        if annual_wk == 0 and annual_we == 0:
            continue

        site_dir = is_site_director(pname)
        required = get_holiday_requirement(fte, site_dir)

        # Which holidays did they work in B1+B2?
        # holiday_workers keys are canonical (uppercase), so canonicalize pname
        pname_canon = to_canonical(pname)
        holidays_worked = []
        for hol_name in PRIOR_HOLIDAYS:
            if pname_canon in holiday_workers.get(hol_name, set()):
                holidays_worked.append(hol_name)

        still_owe = max(0, required - len(holidays_worked))

        # Preferences (holidays they want OFF)
        h1 = pdata.get("holiday_1", "").strip()
        h2 = pdata.get("holiday_2", "").strip()
        preferences = [h for h in [h1, h2] if h]
        memorial_is_preference = any(
            "memorial" in p.lower() for p in preferences
        )

        # Christmas / New Year's analysis (Section 4.3)
        worked_christmas = "Christmas Day" in holidays_worked
        worked_new_years = "New Year's Day" in holidays_worked

        # Preference violations in B1+B2
        prefs_violated = []
        for p in preferences:
            p_lower = p.lower()
            for hol_name in PRIOR_HOLIDAYS:
                if hol_name.lower() in p_lower or p_lower in hol_name.lower():
                    if hol_name in holidays_worked:
                        prefs_violated.append(hol_name)
                    break

        # Memorial Day availability
        mem_available = check_memorial_day_availability(
            pname, availability, name_map
        )

        records.append({
            "provider": pname,
            "fte": fte,
            "is_site_director": site_dir,
            "required": required,
            "holidays_worked": holidays_worked,
            "count_worked": len(holidays_worked),
            "still_owe": still_owe,
            "preferences": preferences,
            "memorial_is_preference": memorial_is_preference,
            "worked_christmas": worked_christmas,
            "worked_new_years": worked_new_years,
            "worked_both_xmas_ny": worked_christmas and worked_new_years,
            "worked_neither_xmas_ny": not worked_christmas and not worked_new_years,
            "prefs_violated": prefs_violated,
            "mem_available": mem_available,
            # Filled by compute_memorial_day_priority:
            "tier": None,
            "priority": None,
            "tier_reason": "",
        })

    return records


def compute_memorial_day_priority(records):
    """Assign Memorial Day priority tier to each provider."""
    for r in records:
        if r["required"] == 0:
            r["tier"] = "EXEMPT"
            r["priority"] = 97
            r["tier_reason"] = "No holiday requirement"
            continue

        if r["still_owe"] == 0:
            r["tier"] = "MET"
            r["priority"] = 90
            r["tier_reason"] = (
                f"Already worked {r['count_worked']}/{r['required']} holidays"
            )
            continue

        if not r["mem_available"]:
            r["tier"] = "UNAVAILABLE"
            r["priority"] = 95
            r["tier_reason"] = "Unavailable Memorial Day week"
            continue

        if not r["memorial_is_preference"]:
            r["tier"] = "MUST"
            r["priority"] = 10 - r["still_owe"]  # owe more = higher priority
            parts = [f"Owes {r['still_owe']} more holiday(s)"]
            if r["worked_neither_xmas_ny"]:
                parts.append("worked neither Christmas nor New Year's")
            r["tier_reason"] = "; ".join(parts)
        else:
            r["tier"] = "SHOULD"
            r["priority"] = 20 - r["still_owe"]
            r["tier_reason"] = (
                f"Owes {r['still_owe']} more holiday(s), but Memorial Day "
                f"is a preference. May need to override."
            )

    # Sort by tier then priority
    records.sort(key=lambda r: (TIER_ORDER.get(r["tier"], 99), r["priority"]))


# ═══════════════════════════════════════════════════════════════════════════
# ISSUE DETECTION
# ═══════════════════════════════════════════════════════════════════════════

def detect_issues(records):
    """Detect holiday-related issues."""
    issues = {
        "overworked": [],
        "both_xmas_ny": [],
        "neither_xmas_ny": [],
        "pref_violated": [],
        "impossible": [],
    }

    for r in records:
        # Overworked: worked more than required
        if r["count_worked"] > r["required"] and r["required"] > 0:
            issues["overworked"].append(r)

        # Both Christmas AND New Year's (Section 4.3)
        if r["worked_both_xmas_ny"]:
            issues["both_xmas_ny"].append(r)

        # Neither Christmas NOR New Year's (missed the window)
        if (r["worked_neither_xmas_ny"] and r["required"] > 0
                and r["still_owe"] > 0):
            issues["neither_xmas_ny"].append(r)

        # Preference violated in B1+B2
        if r["prefs_violated"]:
            issues["pref_violated"].append(r)

        # Impossible fulfillment: owe 2+ but only Memorial Day remains
        if r["still_owe"] >= 2:
            issues["impossible"].append(r)

    return issues


# ═══════════════════════════════════════════════════════════════════════════
# HTML RENDERING
# ═══════════════════════════════════════════════════════════════════════════

def render_summary(records, issues):
    """Render summary overview cards."""
    by_tier = defaultdict(list)
    for r in records:
        by_tier[r["tier"]].append(r)

    total_issues = sum(len(v) for v in issues.values())

    cards = [
        f'<div class="ov-card"><div class="ov-num">{len(records)}</div>'
        f'<div class="ov-label">Eligible Providers</div></div>',

        f'<div class="ov-card" style="border-color:var(--red)">'
        f'<div class="ov-num" style="color:var(--red)">{len(by_tier["MUST"])}</div>'
        f'<div class="ov-label">MUST (Memorial Day)</div></div>',

        f'<div class="ov-card" style="border-color:var(--yellow)">'
        f'<div class="ov-num" style="color:#856404">{len(by_tier["SHOULD"])}</div>'
        f'<div class="ov-label">SHOULD (pref conflict)</div></div>',

        f'<div class="ov-card" style="border-color:var(--green)">'
        f'<div class="ov-num" style="color:var(--green)">{len(by_tier["MET"])}</div>'
        f'<div class="ov-label">Obligation MET</div></div>',

        f'<div class="ov-card"><div class="ov-num">'
        f'{sum(1 for r in records if r["still_owe"] > 0)}</div>'
        f'<div class="ov-label">Still Owe Holidays</div></div>',

        f'<div class="ov-card" style="border-color:var(--yellow)">'
        f'<div class="ov-num" style="color:#856404">{total_issues}</div>'
        f'<div class="ov-label">Flagged Issues</div></div>',
    ]
    return f'<div class="overview-grid">{"".join(cards)}</div>'


def _tier_row_style(tier):
    return {
        "MUST": "background: var(--red-bg);",
        "SHOULD": "background: var(--yellow-bg);",
        "MET": "",
        "UNAVAILABLE": "background: var(--blue-bg);",
        "EXEMPT": "",
    }.get(tier, "")


def render_recommendation_table(records):
    """Render the Memorial Day recommendation table."""
    header = (
        '<tr>'
        '<th onclick="sortTable(this,0,\'text\')">Provider</th>'
        '<th onclick="sortTable(this,1,\'num\')">FTE</th>'
        '<th onclick="sortTable(this,2,\'text\')">Site Dir</th>'
        '<th onclick="sortTable(this,3,\'num\')">Required</th>'
        '<th onclick="sortTable(this,4,\'num\')">Worked</th>'
        '<th onclick="sortTable(this,5,\'num\')">Owe</th>'
        '<th>Holidays Worked</th>'
        '<th>Preferences (OFF)</th>'
        '<th onclick="sortTable(this,8,\'text\')">Mem Day Pref?</th>'
        '<th onclick="sortTable(this,9,\'text\')">Available?</th>'
        '<th onclick="sortTable(this,10,\'text\')">Tier</th>'
        '</tr>'
    )

    rows = []
    for r in records:
        style = _tier_row_style(r["tier"])
        color = TIER_COLORS.get(r["tier"], "blue")
        worked_str = ", ".join(r["holidays_worked"]) if r["holidays_worked"] else "—"
        pref_str = ", ".join(r["preferences"]) if r["preferences"] else "—"
        rows.append(
            f'<tr style="{style}">'
            f'<td>{esc(r["provider"])}</td>'
            f'<td class="num">{r["fte"]:.2f}</td>'
            f'<td>{"Yes" if r["is_site_director"] else ""}</td>'
            f'<td class="num">{r["required"]}</td>'
            f'<td class="num">{r["count_worked"]}</td>'
            f'<td class="num"><strong>{r["still_owe"]}</strong></td>'
            f'<td>{esc(worked_str)}</td>'
            f'<td>{esc(pref_str)}</td>'
            f'<td>{"Yes" if r["memorial_is_preference"] else ""}</td>'
            f'<td>{"Yes" if r["mem_available"] else badge("No", "red")}</td>'
            f'<td>{badge(r["tier"], color)}</td>'
            f'</tr>'
        )

    return (
        '<div class="hm-scroll">'
        '<table class="detail-table">'
        f'<thead>{header}</thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table></div>'
    )


def render_tier_details(records, tier, color):
    """Render collapsible details for a tier."""
    filtered = [r for r in records if r["tier"] == tier]
    if not filtered:
        return f'<p class="pass">No providers in {tier} tier.</p>'

    items = []
    for r in filtered:
        parts = []

        # Obligation math
        parts.append(
            f'<p><strong>Holiday obligation:</strong> '
            f'Required: {r["required"]} | Worked: {r["count_worked"]} | '
            f'Still owe: <strong>{r["still_owe"]}</strong></p>'
        )

        if r["is_site_director"]:
            parts.append(
                f'<p>{badge("Site Director", "blue")} '
                f'Capped at 2 holidays/year regardless of FTE</p>'
            )

        # Holidays worked detail
        if r["holidays_worked"]:
            hol_list = ", ".join(
                f'{h} ({HOLIDAYS[h].strftime("%b %d")})'
                for h in r["holidays_worked"]
            )
            parts.append(f'<p><strong>Worked:</strong> {esc(hol_list)}</p>')
        else:
            parts.append('<p><strong>Worked:</strong> None yet</p>')

        # Preferences
        if r["preferences"]:
            pref_str = ", ".join(esc(p) for p in r["preferences"])
            parts.append(
                f'<p><strong>Preferences (OFF):</strong> {pref_str}</p>'
            )
        else:
            parts.append(
                '<p><strong>Preferences:</strong> None submitted</p>'
            )

        # Christmas / New Year's
        if r["worked_both_xmas_ny"]:
            parts.append(
                f'<p>{badge("Both Xmas + NY", "yellow")} '
                f'Section 4.3 recommends one or the other, not both</p>'
            )
        elif r["worked_neither_xmas_ny"] and r["required"] > 0:
            parts.append(
                f'<p>{badge("Neither Xmas nor NY", "yellow")} '
                f'Missed both — only Memorial Day remains</p>'
            )

        # Preference violations
        if r["prefs_violated"]:
            violated_str = ", ".join(esc(p) for p in r["prefs_violated"])
            parts.append(
                f'<p>{badge("Pref violated", "red")} '
                f'Worked despite preference: {violated_str}</p>'
            )

        # Impossible fulfillment
        if r["still_owe"] >= 2:
            parts.append(
                f'<div style="background:var(--yellow-bg);border:1px solid '
                f'var(--yellow-border);border-radius:6px;padding:8px 12px;'
                f'margin:8px 0">'
                f'{badge("Cannot fulfill", "yellow")} '
                f'Owes {r["still_owe"]} holidays but only Memorial Day '
                f'remains. Will be {r["still_owe"] - 1} short this cycle.'
                f'</div>'
            )

        # Memorial Day availability
        if not r["mem_available"]:
            parts.append(
                f'<p>{badge("Unavailable", "red")} '
                f'Marked unavailable for Memorial Day week</p>'
            )

        # Tier reason
        parts.append(
            f'<p style="color:var(--text-muted);font-size:0.85rem">'
            f'<em>Tier reason: {esc(r["tier_reason"])}</em></p>'
        )

        detail = "\n".join(parts)

        # Summary line
        owe_badge = badge(f"owe {r['still_owe']}", color) if r["still_owe"] > 0 else ""
        summary = (
            f'<strong>{esc(r["provider"])}</strong> {owe_badge} &mdash; '
            f'{r["count_worked"]}/{r["required"]} worked, '
            f'FTE {r["fte"]:.2f}'
        )
        items.append(collapsible(summary, detail))

    return "\n".join(items)


def render_issues(issues):
    """Render the flagged issues section."""
    parts = []

    # Overworked
    section = issues["overworked"]
    if section:
        rows = []
        for r in section:
            rows.append(
                f'<p>{esc(r["provider"])} — worked {r["count_worked"]} '
                f'(required: {r["required"]})</p>'
            )
        parts.append(collapsible(
            f'{badge(f"{len(section)} overworked", "yellow")} '
            f'Worked more holidays than required',
            "\n".join(rows)
        ))

    # Both Christmas AND New Year's
    section = issues["both_xmas_ny"]
    if section:
        rows = [f'<p>{esc(r["provider"])}</p>' for r in section]
        parts.append(collapsible(
            f'{badge(f"{len(section)} both Xmas+NY", "yellow")} '
            f'Section 4.3: should work one or the other, not both',
            "\n".join(rows)
        ))

    # Neither Christmas NOR New Year's
    section = issues["neither_xmas_ny"]
    if section:
        rows = [f'<p>{esc(r["provider"])} — owes {r["still_owe"]} more</p>'
                for r in section]
        parts.append(collapsible(
            f'{badge(f"{len(section)} neither Xmas nor NY", "yellow")} '
            f'Missed both — only Memorial Day remains this cycle',
            "\n".join(rows)
        ))

    # Preference violations
    section = issues["pref_violated"]
    if section:
        rows = []
        for r in section:
            violated = ", ".join(r["prefs_violated"])
            rows.append(
                f'<p>{esc(r["provider"])} — worked {esc(violated)} '
                f'despite preference</p>'
            )
        parts.append(collapsible(
            f'{badge(f"{len(section)} pref violations", "red")} '
            f'Holiday preference was not honored in B1+B2',
            "\n".join(rows)
        ))

    # Impossible fulfillment
    section = issues["impossible"]
    if section:
        rows = []
        for r in section:
            short = r["still_owe"] - 1
            rows.append(
                f'<p>{esc(r["provider"])} — owes {r["still_owe"]}, '
                f'will be {short} short</p>'
            )
        parts.append(collapsible(
            f'{badge(f"{len(section)} impossible", "red")} '
            f'Owe 2+ holidays but only Memorial Day remains',
            "\n".join(rows)
        ))

    if not parts:
        return '<p class="pass">No flagged issues.</p>'

    return "\n".join(parts)


def render_holiday_history(holiday_workers):
    """Render per-holiday list of who worked."""
    items = []
    for hol_name in PRIOR_HOLIDAYS:
        workers = sorted(holiday_workers.get(hol_name, set()))
        hol_date = HOLIDAYS[hol_name]
        count = len(workers)

        if workers:
            worker_list = "<br>".join(esc(w) for w in workers)
            detail = f'<p>{worker_list}</p>'
        else:
            detail = '<p style="color:var(--text-muted)">No providers found working this date</p>'

        summary = (
            f'<strong>{esc(hol_name)}</strong> '
            f'({hol_date.strftime("%a %b %d, %Y")}) &mdash; '
            f'{badge(f"{count} providers", "blue")}'
        )
        items.append(collapsible(summary, detail))

    return "\n".join(items)


def render_methodology():
    """Render methodology explanation."""
    return (
        '<p><strong>Working a holiday</strong> means having an included '
        '(non-excluded, non-moonlighting) shift on the actual holiday date. '
        'APP roles, admin shifts, and excluded services do not count.</p>'
        '<p><strong>Holiday requirements</strong> are based on FTE '
        '(Section 4.2): &ge;0.76 FTE &rarr; 3/year, 0.50&ndash;0.75 FTE '
        '&rarr; 2/year, &lt;0.50 FTE &rarr; 1/year. Site directors get 2/year '
        'regardless of FTE.</p>'
        '<p><strong>Christmas / New Year&rsquo;s guideline</strong> '
        '(Section 4.3): On average, a provider should work one or the other '
        '(not both, not neither).</p>'
        '<p><strong>Preferences</strong> indicate holidays the provider '
        'wants OFF. When a provider is assigned a holiday, they are also '
        'scheduled for the full week containing it.</p>'
        '<p><strong>Memorial Day tiers:</strong></p>'
        '<ul>'
        '<li><strong>MUST</strong>: Owes holidays, Memorial Day is NOT in '
        'their preferences. Best candidates for assignment.</li>'
        '<li><strong>SHOULD</strong>: Owes holidays, but Memorial Day IS a '
        'preference. Scheduler may need to override the preference.</li>'
        '<li><strong>MET</strong>: Already fulfilled obligation.</li>'
        '<li><strong>UNAVAILABLE</strong>: Marked unavailable for Memorial '
        'Day week in their individual schedule.</li>'
        '<li><strong>EXEMPT</strong>: No holiday requirement.</li>'
        '</ul>'
        '<p><strong>Data sources:</strong> Amion monthly HTML schedules '
        '(B1+B2), Google Sheet provider data, individual availability JSONs.</p>'
        '<p><strong>Limitations:</strong> Swapped shifts not reflected in Amion '
        'may cause inaccurate holiday detection. The report shows what the '
        'schedule data indicates, not necessarily what happened in practice.</p>'
    )


# ═══════════════════════════════════════════════════════════════════════════
# HTML WRAPPER
# ═══════════════════════════════════════════════════════════════════════════

def wrap_html(body):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Holiday Analysis Report</title>
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
  h3 {{ font-size: 1.1rem; margin: 16px 0 8px; color: #495057; }}
  h4 {{ font-size: 1rem; margin: 14px 0 6px; }}
  p {{ margin: 6px 0; }}
  ul {{ margin: 6px 0 6px 24px; }}
  li {{ margin: 3px 0; }}
  .subtitle {{ color: var(--text-muted); font-size: 0.9rem; margin-bottom: 20px; }}
  .hint {{ color: var(--text-muted); font-size: 0.85rem; font-style: italic; }}
  .pass {{ color: var(--green); font-weight: 600; }}

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

  .badge {{
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 0.78rem; font-weight: 600; white-space: nowrap;
  }}
  .badge-red {{ background: var(--red-bg); color: var(--red); border: 1px solid var(--red-border); }}
  .badge-yellow {{ background: var(--yellow-bg); color: #856404; border: 1px solid var(--yellow-border); }}
  .badge-green {{ background: var(--green-bg); color: var(--green); border: 1px solid var(--green-border); }}
  .badge-blue {{ background: var(--blue-bg); color: var(--blue); border: 1px solid var(--blue-border); }}

  .detail-table {{ width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 0.82rem; }}
  .detail-table th, .detail-table td {{ padding: 4px 8px; border: 1px solid #e9ecef; text-align: left; }}
  .detail-table th {{
    background: #f1f3f5; font-weight: 600; position: sticky; top: 40px;
    cursor: pointer; user-select: none;
  }}
  .detail-table th:hover {{ background: #dee2e6; }}
  .detail-table .num {{ text-align: right; font-variant-numeric: tabular-nums; }}

  .section-card {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px; margin-bottom: 20px;
  }}

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
  summary::before {{ content: "\\25B6 "; font-size: 0.7rem; color: var(--text-muted); }}
  details[open] > summary {{ border-bottom: 1px solid var(--border); }}
  details[open] > summary::before {{ content: "\\25BC "; }}
  .detail-body {{ padding: 10px 14px; }}

  .hm-scroll {{
    overflow-x: auto; margin: 8px 0; -webkit-overflow-scrolling: touch;
  }}

  .controls {{ margin: 10px 0; }}
  .controls button {{
    padding: 5px 14px; margin-right: 8px; border: 1px solid var(--border);
    border-radius: 4px; background: var(--card-bg); cursor: pointer;
    font-size: 0.82rem;
  }}
  .controls button:hover {{ background: #e9ecef; }}

  @media (max-width: 768px) {{
    body {{ padding: 10px; font-size: 13px; }}
    h1 {{ font-size: 1.4rem; }}
    h2 {{ font-size: 1.1rem; padding: 8px 10px; }}
    .overview-grid {{ grid-template-columns: repeat(2, 1fr); gap: 8px; }}
    .ov-num {{ font-size: 1.4rem; }}
    .section-card {{ padding: 10px; }}
    summary {{ padding: 8px 10px; font-size: 0.85rem; }}
    .detail-body {{ padding: 8px 10px; }}
    .detail-table th, .detail-table td {{ padding: 3px 5px; font-size: 0.75rem; }}
  }}

  @media print {{
    details {{ break-inside: avoid; }}
    h2 {{ position: static; }}
  }}
</style>
</head>
<body>
<h1>Block 3 Holiday Analysis Report</h1>
<p class="subtitle">Generated {ts} &mdash; Pre-scheduling analysis of holiday obligations and Memorial Day recommendations</p>
<p class="hint">Cycle 25-26: 6 holidays total. B1+B2 had 5 holidays. Block 3 has Memorial Day only (May 25, 2026).</p>
<div class="controls">
  <button onclick="document.querySelectorAll('details').forEach(d=>d.open=true)">Expand All</button>
  <button onclick="document.querySelectorAll('details').forEach(d=>d.open=false)">Collapse All</button>
</div>
{body}
<script>
function sortTable(th, colIdx, type) {{
  const table = th.closest('table');
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  const asc = th.dataset.sortDir !== 'asc';
  th.dataset.sortDir = asc ? 'asc' : 'desc';
  table.querySelectorAll('th').forEach(h => {{ if (h !== th) delete h.dataset.sortDir; }});
  rows.sort((a, b) => {{
    let va = a.cells[colIdx].textContent.trim();
    let vb = b.cells[colIdx].textContent.trim();
    if (type === 'num') {{
      va = parseFloat(va.replace('%','')) || 0;
      vb = parseFloat(vb.replace('%','')) || 0;
    }}
    if (va < vb) return asc ? -1 : 1;
    if (va > vb) return asc ? 1 : -1;
    return 0;
  }});
  rows.forEach(r => tbody.appendChild(r));
}}
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
    availability = load_availability()
    name_map, _unmatched = build_name_map(providers, availability)

    print("\nParsing Block 1+2 schedules...")
    merged = load_prior_schedules()

    print("\nScanning holiday dates...")
    holiday_workers = scan_holiday_workers(merged)
    for hol_name in PRIOR_HOLIDAYS:
        count = len(holiday_workers.get(hol_name, set()))
        hol_date = HOLIDAYS[hol_name]
        print(f"  {hol_name} ({hol_date.strftime('%Y-%m-%d')}): "
              f"{count} providers worked")

    print("\nBuilding holiday records...")
    records = build_holiday_records(
        providers, tags_data, holiday_workers, availability, name_map
    )
    compute_memorial_day_priority(records)

    # Console summary
    by_tier = defaultdict(list)
    for r in records:
        by_tier[r["tier"]].append(r)

    print(f"\n  Eligible providers: {len(records)}")
    for tier in ("MUST", "SHOULD", "MET", "UNAVAILABLE", "EXEMPT"):
        print(f"  {tier:>12s}: {len(by_tier[tier])}")

    if by_tier["MUST"]:
        print(f"\n  MUST work Memorial Day ({len(by_tier['MUST'])} providers):")
        for r in by_tier["MUST"]:
            print(f"    {r['provider']:<30s} owe={r['still_owe']} "
                  f"worked={r['count_worked']}/{r['required']} "
                  f"FTE={r['fte']:.2f}")

    print("\nDetecting issues...")
    issues = detect_issues(records)
    for issue_type, issue_list in issues.items():
        if issue_list:
            print(f"  {issue_type}: {len(issue_list)}")

    # Build HTML
    print("\nRendering HTML...")
    sections = []

    # Section 1: Summary
    sections.append('<h2>Summary</h2>')
    sections.append('<div class="section-card">')
    sections.append(render_summary(records, issues))
    sections.append('</div>')

    # Section 2: Recommendation Table
    sections.append('<h2>Memorial Day Recommendations</h2>')
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">Click column headers to sort. '
                    'MUST = best candidates for Memorial Day. '
                    'SHOULD = owe holidays but Memorial Day is a preference.</p>')
    sections.append(render_recommendation_table(records))
    sections.append('</div>')

    # Section 3: MUST Tier Details
    must_count = len(by_tier["MUST"])
    sections.append(
        f'<h2>{badge(f"{must_count} providers", "red")} '
        f'MUST Work Memorial Day</h2>'
    )
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">These providers owe holidays and '
                    'Memorial Day is NOT in their preferences. '
                    'Best candidates for assignment.</p>')
    sections.append(render_tier_details(records, "MUST", "red"))
    sections.append('</div>')

    # Section 4: SHOULD Tier Details
    should_count = len(by_tier["SHOULD"])
    sections.append(
        f'<h2>{badge(f"{should_count} providers", "yellow")} '
        f'SHOULD Work Memorial Day</h2>'
    )
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">These providers owe holidays but '
                    'Memorial Day IS a preference. Scheduler may need to '
                    'override the preference.</p>')
    sections.append(render_tier_details(records, "SHOULD", "yellow"))
    sections.append('</div>')

    # Section 5: Flagged Issues
    sections.append('<h2>Flagged Issues</h2>')
    sections.append('<div class="section-card">')
    sections.append(render_issues(issues))
    sections.append('</div>')

    # Section 6: Holiday History
    sections.append('<h2>Holiday History (B1+B2)</h2>')
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">Who worked each holiday in '
                    'Blocks 1 and 2.</p>')
    sections.append(render_holiday_history(holiday_workers))
    sections.append('</div>')

    # Section 7: Methodology
    sections.append('<h2>Methodology</h2>')
    sections.append('<div class="section-card">')
    sections.append(render_methodology())
    sections.append('</div>')

    body = "\n".join(sections)
    html = wrap_html(body)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "holiday_analysis_report.html")
    with open(out_path, "w") as f:
        f.write(html)

    size_kb = os.path.getsize(out_path) / 1024
    print(f"\nReport written to: {out_path}")
    print(f"File size: {size_kb:.0f} KB")


if __name__ == "__main__":
    main()
