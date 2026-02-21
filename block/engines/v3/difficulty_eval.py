"""
Scheduling Difficulty Analysis for V3 Pre-Scheduler (Task 3).

Identifies providers that will be difficult to schedule based on density
(remaining weeks / block weeks), site constraints, and compounding factors.
"""

import math
import re
import os
import sys

_V3_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINES_DIR = os.path.dirname(_V3_DIR)
_BLOCK_DIR = os.path.dirname(_ENGINES_DIR)
_PROJECT_ROOT = os.path.dirname(_BLOCK_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from name_match import to_canonical, match_provider


# ═══════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

BLOCK_WEEKS = 17  # Block 3: March 2 - June 28, 2026

# Reverse map: pct field → site names it unlocks
# Copied from block/engines/shared/loader.py
PCT_TO_SITES = {
    "pct_cooper":      ["Cooper"],
    "pct_inspira_veb": ["Vineland", "Elmer"],
    "pct_inspira_mhw": ["Mullica Hill"],
    "pct_mannington":  ["Mannington"],
    "pct_virtua":      ["Virtua Voorhees", "Virtua Marlton",
                        "Virtua Willingboro", "Virtua Mt Holly"],
    "pct_cape":        ["Cape"],
}


# ═══════════════════════════════════════════════════════════════════════════
# TAG HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _has_tag(pname, tag_name, tags_data):
    """Check if a provider has a specific tag."""
    ptags = tags_data.get(pname, [])
    return any(t["tag"] == tag_name for t in ptags)


def _get_tag_rules(pname, tag_name, tags_data):
    """Get all rule texts for a specific tag on a provider."""
    ptags = tags_data.get(pname, [])
    return [t["rule"] for t in ptags if t["tag"] == tag_name]


# ═══════════════════════════════════════════════════════════════════════════
# RISK CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════
# Thresholds from analysis/generate_stretch_risk_report.py, calibrated
# against actual Block 3 stretch violations.

def classify_risk(density, is_swing=False):
    """Classify stretch risk based on density.

    Returns: "HIGH", "ELEVATED", "MODERATE", or "LOW"
    """
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


def get_eligible_sites(pname, pdata, tags_data):
    """Return list of sites a provider can work at.

    Based on pct_* fields > 0, minus tag-based restrictions.
    Adapted from block/engines/shared/loader.py.
    """
    sites = []
    for pct_field, site_list in PCT_TO_SITES.items():
        if pdata.get(pct_field, 0) > 0:
            sites.extend(site_list)

    ptags = tags_data.get(pname, [])
    for t in ptags:
        tag = t["tag"]
        if tag == "no_elmer":
            sites = [s for s in sites if s != "Elmer"]
        elif tag == "no_vineland":
            sites = [s for s in sites if s != "Vineland"]

    return sites


# ═══════════════════════════════════════════════════════════════════════════
# MAIN EVALUATION
# ═══════════════════════════════════════════════════════════════════════════

def evaluate_difficulty(providers, tags_data, sites, prior_actuals=None):
    """Assess scheduling difficulty for all eligible providers.

    Args:
        providers: dict from load_providers_from_excel
        tags_data: dict from load_tags_from_excel
        sites: dict from load_sites_from_excel
        prior_actuals: optional dict from evaluate_prior_actuals().
            If provided, uses computed remaining values instead of Excel values.

    Returns:
        dict with keys:
            records: list of per-provider difficulty records, sorted by density desc
            by_risk: dict risk_level -> list of provider names
            summary: dict of counts
    """
    # If prior_actuals provided, build a lookup of computed values
    computed_lookup = {}
    if prior_actuals and "computed" in prior_actuals:
        computed = prior_actuals["computed"]
        computed_names = list(computed.keys())
        for pname in providers:
            matched = match_provider(pname, computed_names)
            if matched:
                computed_lookup[pname] = computed[matched]

    records = []

    for pname, pdata in sorted(providers.items()):
        if _has_tag(pname, "do_not_schedule", tags_data):
            continue

        annual_wk = pdata.get("annual_weeks", 0)
        annual_we = pdata.get("annual_weekends", 0)

        # Skip providers with 0 annual allocation (nocturnists, etc.)
        if annual_wk == 0 and annual_we == 0:
            continue

        # Determine remaining weeks/weekends
        if pname in computed_lookup:
            c = computed_lookup[pname]
            remaining_wk = max(0, annual_wk - c.get("prior_weeks", 0))
            remaining_we = max(0, annual_we - c.get("prior_weekends", 0))
            actuals_source = "computed"
        else:
            remaining_wk = pdata.get("weeks_remaining", 0)
            remaining_we = pdata.get("weekends_remaining", 0)
            actuals_source = "excel"

        # days_per_week tag
        days_per_week = 5
        ptags = tags_data.get(pname, [])
        for t in ptags:
            if t["tag"].startswith("days_per_week"):
                m = re.search(r'\d+', t["tag"] + " " + t["rule"])
                if m:
                    days_per_week = int(m.group())
                break

        # swing_shift tag
        is_swing = _has_tag(pname, "swing_shift", tags_data)
        swing_detail = ""
        if is_swing:
            rules = _get_tag_rules(pname, "swing_shift", tags_data)
            swing_detail = rules[0] if rules else ""

        # pa_rotation tag
        is_pa = _has_tag(pname, "pa_rotation", tags_data)
        pa_detail = ""
        if is_pa:
            rules = _get_tag_rules(pname, "pa_rotation", tags_data)
            pa_detail = rules[0] if rules else ""

        # Density
        density = remaining_wk / BLOCK_WEEKS if BLOCK_WEEKS > 0 else 0
        we_density = remaining_we / BLOCK_WEEKS if BLOCK_WEEKS > 0 else 0

        risk_level = classify_risk(density, is_swing)

        # Max consecutive stretch
        max_consecutive = days_per_week + 2 + days_per_week

        # Eligible sites
        eligible = get_eligible_sites(pname, pdata, tags_data)

        # Fair share
        fair_share_wk = math.ceil(annual_wk / 3) if annual_wk > 0 else 0
        fair_share_we = math.ceil(annual_we / 3) if annual_we > 0 else 0

        if remaining_wk > fair_share_wk * 1.2:
            capacity_status = "tight"
        elif remaining_wk < fair_share_wk * 0.5:
            capacity_status = "excess"
        else:
            capacity_status = "normal"

        # Compounding factors
        compounding = []
        if is_swing and density >= 0.30:
            compounding.append("swing + high density")
        if is_pa:
            compounding.append(f"PA rotation ({pa_detail})")
        if days_per_week < 5:
            compounding.append(f"{days_per_week}-day work week")
        if len(eligible) <= 2:
            compounding.append(f"limited to {len(eligible)} site(s)")
        if capacity_status == "tight":
            compounding.append(f"tight capacity (remaining {remaining_wk:.1f} > fair-share {fair_share_wk})")

        # Notes
        notes = []
        if density >= 0.65:
            notes.append(f"Density {density:.0%} -- hard violations (>12 days) nearly inevitable")
        elif density >= 0.47:
            notes.append(f"Density {density:.0%} -- extended stretches (8-12 days) likely")
        if is_swing:
            notes.append(f"Swing shift ({swing_detail}). Gaps force day-shift compression.")
        if is_pa:
            notes.append(f"PA rotation ({pa_detail}). PA weeks create non-clinical blocks.")
        if days_per_week < 5:
            notes.append(f"Works {days_per_week} days/week -- max consecutive {max_consecutive} days")
        if capacity_status == "tight":
            notes.append(f"Tight: remaining {remaining_wk:.1f} wk exceeds fair-share {fair_share_wk}")
        elif capacity_status == "excess":
            notes.append(f"Excess: remaining {remaining_wk:.1f} wk well below fair-share {fair_share_wk}")

        # Build tag summary
        tag_badges = []
        if is_swing:
            tag_badges.append("swing")
        if is_pa:
            tag_badges.append("PA")
        if days_per_week < 5:
            tag_badges.append(f"dpw:{days_per_week}")
        if _has_tag(pname, "no_elmer", tags_data):
            tag_badges.append("no_elmer")
        if _has_tag(pname, "no_vineland", tags_data):
            tag_badges.append("no_vineland")
        if _has_tag(pname, "pct_override", tags_data):
            tag_badges.append("pct_override")

        records.append({
            "provider": pname,
            "fte": pdata.get("fte", 0),
            "shift_type": pdata.get("shift_type", ""),
            "annual_weeks": annual_wk,
            "annual_weekends": annual_we,
            "remaining_weeks": round(remaining_wk, 1),
            "remaining_weekends": round(remaining_we, 1),
            "density": round(density, 3),
            "weekend_density": round(we_density, 3),
            "risk_level": risk_level,
            "days_per_week": days_per_week,
            "max_consecutive": max_consecutive,
            "is_swing": is_swing,
            "is_pa": is_pa,
            "eligible_sites": eligible,
            "num_eligible_sites": len(eligible),
            "compounding_factors": compounding,
            "fair_share_weeks": fair_share_wk,
            "fair_share_weekends": fair_share_we,
            "capacity_status": capacity_status,
            "tag_badges": tag_badges,
            "actuals_source": actuals_source,
            "notes": notes,
        })

    # Sort by density descending
    records.sort(key=lambda r: r["density"], reverse=True)

    # Group by risk
    by_risk = {"HIGH": [], "ELEVATED": [], "MODERATE": [], "LOW": []}
    for r in records:
        by_risk[r["risk_level"]].append(r["provider"])

    # Summary
    total = len(records)
    densities = [r["density"] for r in records]
    mean_density = sum(densities) / total if total > 0 else 0

    summary = {
        "total_eligible": total,
        "high_count": len(by_risk["HIGH"]),
        "elevated_count": len(by_risk["ELEVATED"]),
        "moderate_count": len(by_risk["MODERATE"]),
        "low_count": len(by_risk["LOW"]),
        "mean_density": round(mean_density, 3),
        "tight_capacity_count": sum(1 for r in records if r["capacity_status"] == "tight"),
        "excess_capacity_count": sum(1 for r in records if r["capacity_status"] == "excess"),
        "single_site_count": sum(1 for r in records if r["num_eligible_sites"] <= 1),
    }

    return {"records": records, "by_risk": by_risk, "summary": summary}
