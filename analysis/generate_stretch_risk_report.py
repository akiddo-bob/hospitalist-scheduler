#!/usr/bin/env python3
"""
Pre-Scheduling Stretch Risk Prediction Report

Analyzes the gap between what providers have worked (Blocks 1+2) and what they
must still work (annual contract) to predict which providers are likely to
create stretch rule violations (Check 5) in Block 3.

No Block 3 schedule is required.  The report runs from prior_actuals.json +
the Google Sheet provider/tag data.

Optional --compare flag: also loads the actual Block 3 schedule and shows
prediction vs reality accuracy.

Usage:
    python -m analysis.generate_stretch_risk_report
    python -m analysis.generate_stretch_risk_report --compare
"""

import argparse
import html as html_mod
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

# ── Project root setup ──────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from block.engines.shared.loader import (
    load_providers, load_tags, has_tag, get_tag_rules, get_eligible_sites,
)

OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")
BLOCK_WEEKS = 17  # Block 3: March 2 - June 28, 2026


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
    return f'<details{op}><summary>{summary_html}</summary><div class="detail-body">{detail_html}</div></details>'


RISK_COLORS = {
    "HIGH": "red",
    "ELEVATED": "yellow",
    "MODERATE": "blue",
    "LOW": "green",
}

RISK_ORDER = {"HIGH": 0, "ELEVATED": 1, "MODERATE": 2, "LOW": 3}


# ═══════════════════════════════════════════════════════════════════════════
# RISK SCORING
# ═══════════════════════════════════════════════════════════════════════════

def classify_risk(density, is_swing=False):
    """Classify stretch risk based on density and tags."""
    if density >= 0.65:
        return "HIGH"
    elif density >= 0.47:
        return "ELEVATED"
    elif density >= 0.35:
        return "ELEVATED" if is_swing else "MODERATE"
    elif density >= 0.30 and is_swing:
        return "MODERATE"
    else:
        return "LOW"


def compute_risk_records(providers, tags_data, prior_actuals):
    """Compute risk records for all eligible providers."""
    records = []

    for pname, pdata in sorted(providers.items()):
        if has_tag(pname, "do_not_schedule", tags_data):
            continue

        annual_wk = pdata.get("annual_weeks", 0)
        annual_we = pdata.get("annual_weekends", 0)

        # Skip providers with 0 annual allocation (nocturnists, etc.)
        if annual_wk == 0 and annual_we == 0:
            continue

        prior = prior_actuals.get(pname, {})
        prior_wk = prior.get("prior_weeks", 0)
        prior_we = prior.get("prior_weekends", 0)

        remaining_wk = max(0, annual_wk - prior_wk)
        remaining_we = max(0, annual_we - prior_we)

        # days_per_week tag — tag name may include the value, e.g. "days_per_week: 4"
        days_per_week = 5
        dpw_rules = get_tag_rules(pname, "days_per_week", tags_data)
        if not dpw_rules:
            ptags = tags_data.get(pname, [])
            for t in ptags:
                if t["tag"].startswith("days_per_week"):
                    dpw_rules = [t["tag"] + " " + t["rule"]]
                    break
        if dpw_rules:
            m = re.search(r'\d+', dpw_rules[0])
            if m:
                days_per_week = int(m.group())

        # swing_shift tag
        is_swing = has_tag(pname, "swing_shift", tags_data)
        swing_detail = ""
        if is_swing:
            swing_rules = get_tag_rules(pname, "swing_shift", tags_data)
            swing_detail = swing_rules[0] if swing_rules else ""

        # pa_rotation tag
        is_pa = has_tag(pname, "pa_rotation", tags_data)
        pa_detail = ""
        if is_pa:
            pa_rules = get_tag_rules(pname, "pa_rotation", tags_data)
            pa_detail = pa_rules[0] if pa_rules else ""

        # Density
        density = remaining_wk / BLOCK_WEEKS if BLOCK_WEEKS > 0 else 0
        we_density = remaining_we / BLOCK_WEEKS if BLOCK_WEEKS > 0 else 0

        risk_level = classify_risk(density, is_swing)

        # Max consecutive stretch based on days_per_week
        max_consecutive = days_per_week + 2 + days_per_week  # wk + WE + wk

        # Build notes
        notes = []
        if density >= 0.65:
            notes.append(f"Density {density:.0%} -- hard violations (>12 days) nearly inevitable")
        elif density >= 0.47:
            notes.append(f"Density {density:.0%} -- extended stretches (8-12 days) likely")

        if is_swing:
            notes.append(f"Swing shift provider ({swing_detail}). Swing gaps force day-shift weeks into denser clusters.")
            if density < 0.47 and density >= 0.30:
                notes.append("Risk bumped due to swing_shift tag")

        if is_pa:
            notes.append(f"PA rotation ({pa_detail}). PA weeks still create assignments.")

        if days_per_week < 5:
            notes.append(f"Works {days_per_week} days/week -- max consecutive stretch reduced to {max_consecutive} days")

        # Eligible sites
        eligible = get_eligible_sites(pname, pdata, tags_data)

        # All tags
        all_tags = tags_data.get(pname, [])

        records.append({
            "provider": pname,
            "fte": pdata.get("fte", 0),
            "annual_weeks": annual_wk,
            "annual_weekends": annual_we,
            "prior_weeks": prior_wk,
            "prior_weekends": prior_we,
            "remaining_weeks": remaining_wk,
            "remaining_weekends": remaining_we,
            "density": density,
            "weekend_density": we_density,
            "risk_level": risk_level,
            "days_per_week": days_per_week,
            "max_consecutive": max_consecutive,
            "is_swing": is_swing,
            "swing_detail": swing_detail,
            "is_pa": is_pa,
            "pa_detail": pa_detail,
            "eligible_sites": eligible,
            "all_tags": all_tags,
            "notes": notes,
        })

    # Sort by density descending
    records.sort(key=lambda r: r["density"], reverse=True)
    return records


# ═══════════════════════════════════════════════════════════════════════════
# HTML RENDERING
# ═══════════════════════════════════════════════════════════════════════════

def render_summary(records):
    """Render summary overview cards."""
    by_level = defaultdict(list)
    for r in records:
        by_level[r["risk_level"]].append(r)

    total = len(records)
    densities = [r["density"] for r in records]
    mean_density = sum(densities) / len(densities) if densities else 0

    cards = [
        f'<div class="ov-card"><div class="ov-num">{total}</div><div class="ov-label">Eligible Providers</div></div>',
        f'<div class="ov-card" style="border-color:var(--red)"><div class="ov-num" style="color:var(--red)">{len(by_level["HIGH"])}</div><div class="ov-label">HIGH Risk</div></div>',
        f'<div class="ov-card" style="border-color:var(--yellow)"><div class="ov-num" style="color:#856404">{len(by_level["ELEVATED"])}</div><div class="ov-label">ELEVATED Risk</div></div>',
        f'<div class="ov-card" style="border-color:var(--blue)"><div class="ov-num" style="color:var(--blue)">{len(by_level["MODERATE"])}</div><div class="ov-label">MODERATE Risk</div></div>',
        f'<div class="ov-card" style="border-color:var(--green)"><div class="ov-num" style="color:var(--green)">{len(by_level["LOW"])}</div><div class="ov-label">LOW Risk</div></div>',
        f'<div class="ov-card"><div class="ov-num">{mean_density:.0%}</div><div class="ov-label">Mean Density</div></div>',
    ]
    return f'<div class="overview-grid">{"".join(cards)}</div>'


def _risk_row_style(level):
    """Background tint for risk level rows."""
    return {
        "HIGH": "background: var(--red-bg);",
        "ELEVATED": "background: var(--yellow-bg);",
        "MODERATE": "background: var(--blue-bg);",
        "LOW": "",
    }.get(level, "")


def render_risk_table(records):
    """Render the full risk table with sortable columns."""
    # Tag badges helper
    def tag_badges(r):
        parts = []
        if r["is_swing"]:
            parts.append(badge("swing", "yellow"))
        if r["is_pa"]:
            parts.append(badge("PA", "blue"))
        if r["days_per_week"] < 5:
            parts.append(badge(f"{r['days_per_week']}d/wk", "blue"))
        return " ".join(parts)

    header = (
        '<tr>'
        '<th onclick="sortTable(this,0,\'text\')">Provider</th>'
        '<th onclick="sortTable(this,1,\'num\')">FTE</th>'
        '<th onclick="sortTable(this,2,\'num\')">Ann Wks</th>'
        '<th onclick="sortTable(this,3,\'num\')">Prior Wks</th>'
        '<th onclick="sortTable(this,4,\'num\')">Remaining</th>'
        '<th onclick="sortTable(this,5,\'num\')">Density</th>'
        '<th onclick="sortTable(this,6,\'num\')">Ann WE</th>'
        '<th onclick="sortTable(this,7,\'num\')">Prior WE</th>'
        '<th onclick="sortTable(this,8,\'num\')">Rem WE</th>'
        '<th onclick="sortTable(this,9,\'text\')">Risk</th>'
        '<th>Tags</th>'
        '</tr>'
    )

    rows = []
    for r in records:
        style = _risk_row_style(r["risk_level"])
        color = RISK_COLORS[r["risk_level"]]
        rows.append(
            f'<tr style="{style}">'
            f'<td>{esc(r["provider"])}</td>'
            f'<td class="num">{r["fte"]:.2f}</td>'
            f'<td class="num">{r["annual_weeks"]:.1f}</td>'
            f'<td class="num">{r["prior_weeks"]:.1f}</td>'
            f'<td class="num">{r["remaining_weeks"]:.1f}</td>'
            f'<td class="num"><strong>{r["density"]:.0%}</strong></td>'
            f'<td class="num">{r["annual_weekends"]:.1f}</td>'
            f'<td class="num">{r["prior_weekends"]:.1f}</td>'
            f'<td class="num">{r["remaining_weekends"]:.1f}</td>'
            f'<td>{badge(r["risk_level"], color)}</td>'
            f'<td>{tag_badges(r)}</td>'
            f'</tr>'
        )

    return (
        '<div class="hm-scroll">'
        '<table class="detail-table">'
        f'<thead>{header}</thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table></div>'
    )


def render_risk_details(records, level, color):
    """Render collapsible details for providers at a given risk level."""
    filtered = [r for r in records if r["risk_level"] == level]
    if not filtered:
        return f'<p class="pass">No {level} risk providers.</p>'

    items = []
    for r in filtered:
        parts = []

        # Density math
        parts.append(
            f'<p><strong>Density calculation:</strong> '
            f'Annual: {r["annual_weeks"]:.1f} wks &minus; Prior (B1+B2): {r["prior_weeks"]:.1f} wks '
            f'= Remaining: {r["remaining_weeks"]:.1f} wks. '
            f'Density: {r["remaining_weeks"]:.1f} / {BLOCK_WEEKS} = <strong>{r["density"]:.1%}</strong></p>'
        )

        # Weekend info
        parts.append(
            f'<p><strong>Weekends:</strong> '
            f'Annual: {r["annual_weekends"]:.1f} &minus; Prior: {r["prior_weekends"]:.1f} '
            f'= Remaining: {r["remaining_weekends"]:.1f} '
            f'(WE density: {r["weekend_density"]:.0%})</p>'
        )

        # Days per week
        if r["days_per_week"] < 5:
            dpw = r["days_per_week"]
            parts.append(
                f'<p>{badge(f"{dpw} days/week", "blue")} '
                f'Max consecutive stretch reduced to {r["max_consecutive"]} days '
                f'(vs 12 for 5-day providers)</p>'
            )

        # Swing warning
        if r["is_swing"]:
            parts.append(
                f'<div style="background:var(--yellow-bg);border:1px solid var(--yellow-border);border-radius:6px;padding:8px 12px;margin:8px 0">'
                f'{badge("swing_shift", "yellow")} {esc(r["swing_detail"])}<br>'
                f'<em>Swing gaps force remaining day-shift weeks into denser clusters. '
                f'This provider was risk-bumped due to the swing tag.</em></div>'
            )

        # PA info
        if r["is_pa"]:
            parts.append(
                f'<p>{badge("pa_rotation", "blue")} {esc(r["pa_detail"])}. '
                f'PA weeks still create assignments but may be at different sites.</p>'
            )

        # Eligible sites
        if r["eligible_sites"]:
            sites_str = ", ".join(esc(s) for s in sorted(r["eligible_sites"]))
            parts.append(f'<p><strong>Eligible sites:</strong> {sites_str}</p>')

        # All tags
        if r["all_tags"]:
            tag_rows = "".join(
                f'<tr><td>{esc(t["tag"])}</td><td>{esc(t["rule"])}</td></tr>'
                for t in r["all_tags"]
            )
            parts.append(
                '<h5 style="margin:8px 0 4px;">All tags:</h5>'
                '<table class="detail-table"><thead><tr><th>Tag</th><th>Rule</th></tr></thead>'
                f'<tbody>{tag_rows}</tbody></table>'
            )

        # Suggested tag
        if level == "HIGH":
            max_c = min(14, r["remaining_weeks"] + 2)  # practical cap
            suggestion = (
                f'<div style="margin-top:12px;padding:8px 12px;background:var(--red-bg);border:1px solid var(--red-border);border-radius:6px">'
                f'{badge("Suggested Action", "red")} Add tag to Provider Tags:<br>'
                f'<code style="display:block;margin:6px 0;padding:6px;background:#fff;border-radius:4px">'
                f'{esc(r["provider"])} | stretch_override | max_consecutive: {int(max_c)}, '
                f'reason: density {r["density"]:.0%}, remaining {r["remaining_weeks"]:.1f} wks in {BLOCK_WEEKS} wk block'
                f'</code></div>'
            )
            parts.append(suggestion)
        elif level == "ELEVATED" and r["is_swing"]:
            suggestion = (
                f'<div style="margin-top:12px;padding:8px 12px;background:var(--yellow-bg);border:1px solid var(--yellow-border);border-radius:6px">'
                f'{badge("Consider", "yellow")} Swing provider at elevated risk. Consider adding tag:<br>'
                f'<code style="display:block;margin:6px 0;padding:6px;background:#fff;border-radius:4px">'
                f'{esc(r["provider"])} | stretch_override | max_consecutive: 12, '
                f'reason: swing split + density {r["density"]:.0%}'
                f'</code></div>'
            )
            parts.append(suggestion)

        detail = "\n".join(parts)

        # Tag badges in summary
        tag_parts = []
        if r["is_swing"]:
            tag_parts.append(badge("swing", "yellow"))
        if r["is_pa"]:
            tag_parts.append(badge("PA", "blue"))
        tags_str = " ".join(tag_parts)
        if tags_str:
            tags_str = " " + tags_str

        summary = (
            f'<strong>{esc(r["provider"])}</strong>{tags_str} &mdash; '
            f'density {r["density"]:.0%}, remaining {r["remaining_weeks"]:.1f} wks'
        )
        items.append(collapsible(summary, detail))

    return "\n".join(items)


def render_tag_suggestions(records):
    """Render the tag suggestions summary table."""
    suggestions = []

    for r in records:
        if r["risk_level"] == "HIGH":
            max_c = min(14, r["remaining_weeks"] + 2)
            suggestions.append({
                "provider": r["provider"],
                "tag": "stretch_override",
                "rule": f'max_consecutive: {int(max_c)}, reason: density {r["density"]:.0%}',
                "rationale": f'Density {r["density"]:.0%} -- hard violations nearly inevitable',
                "priority": "red",
            })
        elif r["risk_level"] == "ELEVATED" and r["is_swing"]:
            suggestions.append({
                "provider": r["provider"],
                "tag": "stretch_override",
                "rule": f'max_consecutive: 12, reason: swing split + density {r["density"]:.0%}',
                "rationale": f'Swing provider at {r["density"]:.0%} density -- surprise violations likely',
                "priority": "yellow",
            })

    if not suggestions:
        return '<p class="pass">No tag suggestions at this time.</p>'

    rows = []
    for s in suggestions:
        rows.append(
            f'<tr><td>{esc(s["provider"])}</td>'
            f'<td><code>{esc(s["tag"])}</code></td>'
            f'<td><code>{esc(s["rule"])}</code></td>'
            f'<td>{badge(s["rationale"], s["priority"])}</td></tr>'
        )

    guidance = (
        '<p style="margin-bottom:12px">The <code>stretch_override</code> tag tells the scheduling engine '
        'to allow longer consecutive stretches for specific providers. The <code>max_consecutive</code> '
        'value sets the ceiling (default limit is 12 days). Add these to the Provider Tags tab in the '
        'Google Sheet <strong>before</strong> running the engine.</p>'
    )

    return guidance + (
        '<table class="detail-table">'
        '<thead><tr><th>Provider</th><th>Tag</th><th>Rule</th><th>Rationale</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def render_methodology():
    """Render the methodology explanation."""
    return (
        '<p><strong>Density</strong> = remaining weeks / block weeks (17). '
        'A density of 50% means the provider must work 8.5 out of 17 weeks. '
        'When a provider needs to work most available weeks, the scheduler has no room '
        'to insert gaps between stretches, forcing consecutive assignments.</p>'
        '<p><strong>Thresholds</strong> were calibrated against a prototype analysis '
        'of actual Block 3 stretch violations:</p>'
        '<ul>'
        '<li><strong>65%+</strong> (HIGH): All providers at this density who were scheduled had hard violations (>12 days)</li>'
        '<li><strong>47-65%</strong> (ELEVATED): Extended stretches (8-12 days) were common</li>'
        '<li><strong>35-47%</strong> (MODERATE): Some violations occurred, dependent on scheduling strategy</li>'
        '<li><strong>&lt;35%</strong> (LOW): Violations were rare (5 surprises out of 38 providers)</li>'
        '</ul>'
        '<p><strong>Swing shift adjustment:</strong> Providers with <code>swing_shift</code> tags '
        'split their time between day and swing shifts. The swing weeks create scheduling gaps that '
        'force remaining day-shift weeks into denser clusters. In the prototype, 2 of 5 "surprise" '
        'violators (predicted LOW but actually violated) were swing providers. Risk is bumped one level '
        'when density &ge; 30%.</p>'
        '<p><strong>Limitations:</strong> Density is necessary but not sufficient for violation prediction. '
        'Availability constraints, site distribution requirements, and scheduling strategy also affect '
        'whether a provider actually violates stretch rules. The false positive rate is ~57% at the '
        'ELEVATED threshold -- many providers who "should" have needed dense stretches were scheduled '
        'with adequate gaps by a skilled scheduler.</p>'
    )


# ═══════════════════════════════════════════════════════════════════════════
# COMPARISON MODE
# ═══════════════════════════════════════════════════════════════════════════

def run_comparison(records):
    """Run actual Check 5 against Block 3 schedule and compare to predictions."""
    from analysis.validate_block3 import (
        parse_block3, filter_day_only, check_consecutive_stretches,
    )

    print("  Loading Block 3 schedule for comparison...")
    all_a = parse_block3()
    day = filter_day_only(all_a)

    providers_data = load_providers()
    hard, extended, window = check_consecutive_stretches(day, providers_data)

    # Build actual violator sets
    actual_hard = set(h["provider"] for h in hard)
    actual_extended = set(e["provider"] for e in extended)
    actual_window = set(w["provider"] for w in window)
    actual_any = actual_hard | actual_extended | actual_window

    # Annotate records
    for r in records:
        p = r["provider"]
        r["actual_hard"] = p in actual_hard
        r["actual_extended"] = p in actual_extended
        r["actual_window"] = p in actual_window
        r["actual_any"] = p in actual_any

    return records, hard, extended, window


def render_comparison(records, hard, extended, window):
    """Render comparison section."""
    actual_any = set()
    for r in records:
        if r.get("actual_any"):
            actual_any.add(r["provider"])

    predicted_positive = set(r["provider"] for r in records if r["risk_level"] in ("HIGH", "ELEVATED"))
    predicted_possible = set(r["provider"] for r in records if r["risk_level"] == "MODERATE")
    predicted_negative = set(r["provider"] for r in records if r["risk_level"] == "LOW")
    all_providers = set(r["provider"] for r in records)

    # Confusion matrix
    tp = predicted_positive & actual_any
    fp = predicted_positive - actual_any
    fn_low = predicted_negative & actual_any
    fn_mod = predicted_possible & actual_any
    tn = (predicted_negative | predicted_possible) - actual_any

    caught_all = (predicted_positive | predicted_possible) & actual_any

    sensitivity = len(caught_all) / len(actual_any) if actual_any else 0
    specificity = len(tn) / (len(tn) + len(fp)) if (len(tn) + len(fp)) > 0 else 0
    ppv = len(tp) / len(predicted_positive) if predicted_positive else 0

    parts = []

    # Accuracy cards
    cards = [
        f'<div class="ov-card"><div class="ov-num">{len(actual_any)}</div><div class="ov-label">Actual Violators</div></div>',
        f'<div class="ov-card" style="border-color:var(--green)"><div class="ov-num" style="color:var(--green)">{sensitivity:.0%}</div><div class="ov-label">Sensitivity (caught)</div></div>',
        f'<div class="ov-card"><div class="ov-num">{len(tp)}</div><div class="ov-label">True Positives</div></div>',
        f'<div class="ov-card"><div class="ov-num">{len(fp)}</div><div class="ov-label">False Positives</div></div>',
        f'<div class="ov-card" style="border-color:var(--red)"><div class="ov-num" style="color:var(--red)">{len(fn_low)}</div><div class="ov-label">Surprises (LOW)</div></div>',
        f'<div class="ov-card"><div class="ov-num">{len(fn_mod)}</div><div class="ov-label">Caught at MODERATE</div></div>',
    ]
    parts.append(f'<div class="overview-grid">{"".join(cards)}</div>')

    # Full comparison table
    header = (
        '<tr><th>Provider</th><th>Density</th><th>Predicted</th>'
        '<th>Hard (&gt;12d)</th><th>Extended (8-12d)</th><th>21-day Window</th><th>Match</th></tr>'
    )
    rows = []
    for r in sorted(records, key=lambda x: x["density"], reverse=True):
        if not r.get("actual_any") and r["risk_level"] == "LOW":
            continue  # skip LOW providers with no violations for brevity

        actual_parts = []
        if r.get("actual_hard"):
            actual_parts.append(badge("HARD", "red"))
        if r.get("actual_extended"):
            actual_parts.append(badge("EXT", "yellow"))
        if r.get("actual_window"):
            actual_parts.append(badge("21D", "yellow"))

        is_match = (
            (r["risk_level"] in ("HIGH", "ELEVATED") and r.get("actual_any")) or
            (r["risk_level"] in ("MODERATE", "LOW") and not r.get("actual_any"))
        )
        match_str = badge("match", "green") if is_match else badge("mismatch", "red")

        style = _risk_row_style(r["risk_level"])
        rows.append(
            f'<tr style="{style}">'
            f'<td>{esc(r["provider"])}</td>'
            f'<td class="num">{r["density"]:.0%}</td>'
            f'<td>{badge(r["risk_level"], RISK_COLORS[r["risk_level"]])}</td>'
            f'<td>{"".join([badge("YES","red")] if r.get("actual_hard") else [])}</td>'
            f'<td>{"".join([badge("YES","yellow")] if r.get("actual_extended") else [])}</td>'
            f'<td>{"".join([badge("YES","yellow")] if r.get("actual_window") else [])}</td>'
            f'<td>{match_str}</td>'
            f'</tr>'
        )

    parts.append(
        '<h4>Prediction vs Actual (non-trivial providers)</h4>'
        '<table class="detail-table">'
        f'<thead>{header}</thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )

    # Surprises detail
    surprises = [r for r in records if r["risk_level"] == "LOW" and r.get("actual_any")]
    if surprises:
        surprise_items = []
        for r in surprises:
            actual_types = []
            if r.get("actual_hard"):
                actual_types.append("HARD")
            if r.get("actual_extended"):
                actual_types.append("EXT")
            if r.get("actual_window"):
                actual_types.append("21D")
            tag_note = ""
            if r["is_swing"]:
                tag_note = f" (swing: {esc(r['swing_detail'])})"
            elif r["is_pa"]:
                tag_note = f" (PA: {esc(r['pa_detail'])})"
            surprise_items.append(
                f'<p>{esc(r["provider"])} &mdash; density {r["density"]:.0%}, '
                f'actual: {"+".join(actual_types)}{tag_note}</p>'
            )
        parts.append(collapsible(
            f'{badge(f"{len(surprises)} surprises", "red")} Predicted LOW but actually violated',
            "\n".join(surprise_items)
        ))

    # Caught at MODERATE
    moderate_caught = [r for r in records if r["risk_level"] == "MODERATE" and r.get("actual_any")]
    if moderate_caught:
        mod_items = []
        for r in moderate_caught:
            actual_types = []
            if r.get("actual_hard"):
                actual_types.append("HARD")
            if r.get("actual_extended"):
                actual_types.append("EXT")
            if r.get("actual_window"):
                actual_types.append("21D")
            mod_items.append(
                f'<p>{esc(r["provider"])} &mdash; density {r["density"]:.0%}, '
                f'actual: {"+".join(actual_types)}</p>'
            )
        parts.append(collapsible(
            f'{badge(f"{len(moderate_caught)} caught at MODERATE", "blue")} '
            f'Would have been missed by HIGH/ELEVATED threshold alone',
            "\n".join(mod_items)
        ))

    return "\n".join(parts)


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
<title>Stretch Risk Prediction Report</title>
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
  code {{ background: #e9ecef; padding: 1px 5px; border-radius: 3px; font-size: 0.85rem; }}
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
<h1>Block 3 Stretch Risk Prediction Report</h1>
<p class="subtitle">Generated {ts} &mdash; Pre-scheduling analysis based on B1+B2 actuals vs annual contract</p>
<p class="hint">Block 3: March 2 &mdash; June 28, 2026 ({BLOCK_WEEKS} weeks). Density = remaining weeks / {BLOCK_WEEKS}.</p>
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
  // Clear other headers
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
    parser = argparse.ArgumentParser(description="Pre-scheduling stretch risk prediction report")
    parser.add_argument("--compare", action="store_true",
                        help="Also load actual Block 3 schedule and compare predictions vs reality")
    args = parser.parse_args()

    print("Loading data...")
    providers = load_providers()
    tags_data = load_tags()

    prior_path = os.path.join(OUTPUT_DIR, "prior_actuals.json")
    if not os.path.exists(prior_path):
        print(f"ERROR: {prior_path} not found. Run recalculate_prior_actuals.py first.")
        sys.exit(1)
    with open(prior_path) as f:
        prior_actuals = json.load(f)

    print("Computing risk scores...")
    records = compute_risk_records(providers, tags_data, prior_actuals)

    # Console summary
    by_level = defaultdict(list)
    for r in records:
        by_level[r["risk_level"]].append(r)

    print(f"\n  Eligible providers: {len(records)}")
    for level in ("HIGH", "ELEVATED", "MODERATE", "LOW"):
        count = len(by_level[level])
        print(f"  {level:>10s}: {count}")

    if by_level["HIGH"]:
        print(f"\n  HIGH risk providers:")
        for r in by_level["HIGH"]:
            swing_note = " [swing]" if r["is_swing"] else ""
            print(f"    {r['provider']:<30s} density={r['density']:.0%} "
                  f"remaining={r['remaining_weeks']:.1f} wks{swing_note}")

    # Comparison mode
    comparison_html = ""
    if args.compare:
        print("\nRunning comparison with actual Block 3 schedule...")
        records, hard, extended, window = run_comparison(records)
        comparison_html = render_comparison(records, hard, extended, window)

    # Build HTML
    print("\nRendering HTML...")
    sections = []

    # Section 1: Summary
    sections.append('<h2>Summary</h2>')
    sections.append('<div class="section-card">')
    sections.append(render_summary(records))
    sections.append('</div>')

    # Section 2: Risk Table
    sections.append('<h2>All Providers by Density</h2>')
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">Click column headers to sort. Density = remaining weeks / 17 block weeks.</p>')
    sections.append(render_risk_table(records))
    sections.append('</div>')

    # Section 3: HIGH Risk Details
    high_count = len(by_level["HIGH"])
    sections.append(f'<h2>{badge(f"{high_count} providers", "red")} HIGH Risk Details</h2>')
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">Hard violations (&gt;12 consecutive days) are nearly inevitable for these providers. Consider pre-authorizing stretch exceptions.</p>')
    sections.append(render_risk_details(records, "HIGH", "red"))
    sections.append('</div>')

    # Section 4: ELEVATED Risk Details
    elev_count = len(by_level["ELEVATED"])
    sections.append(f'<h2>{badge(f"{elev_count} providers", "yellow")} ELEVATED Risk Details</h2>')
    sections.append('<div class="section-card">')
    sections.append('<p class="hint">Extended stretches (8-12 days) are likely. Review scheduling strategy for these providers.</p>')
    sections.append(render_risk_details(records, "ELEVATED", "yellow"))
    sections.append('</div>')

    # Section 5: Tag Suggestions
    sections.append('<h2>Tag Suggestions for Scheduler</h2>')
    sections.append('<div class="section-card">')
    sections.append(render_tag_suggestions(records))
    sections.append('</div>')

    # Section 6: Comparison (conditional)
    if comparison_html:
        sections.append('<h2>Comparison: Predictions vs Actual Block 3</h2>')
        sections.append('<div class="section-card">')
        sections.append(comparison_html)
        sections.append('</div>')

    # Section 7: Methodology
    sections.append('<h2>Methodology</h2>')
    sections.append('<div class="section-card">')
    sections.append(render_methodology())
    sections.append('</div>')

    body = "\n".join(sections)
    html = wrap_html(body)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "stretch_risk_report.html")
    with open(out_path, "w") as f:
        f.write(html)

    size_kb = os.path.getsize(out_path) / 1024
    print(f"\nReport written to: {out_path}")
    print(f"File size: {size_kb:.0f} KB")


if __name__ == "__main__":
    main()
