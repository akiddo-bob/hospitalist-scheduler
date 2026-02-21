"""
Tag Evaluation for V3 Pre-Scheduler.

Validates all Provider Tags against the Tag Definitions registry.
Produces structured results for both human review (Excel) and engine config (JSON).
"""

import re
import os
import sys
from collections import defaultdict

_V3_DIR = os.path.dirname(__file__)
_ENGINES_DIR = os.path.dirname(_V3_DIR)
_BLOCK_DIR = os.path.dirname(_ENGINES_DIR)
_PROJECT_ROOT = os.path.dirname(_BLOCK_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from name_match import to_canonical, match_provider


# ═══════════════════════════════════════════════════════════════════════════
# PCT OVERRIDE PARSING (adapted from analysis/validate_block3.py)
# ═══════════════════════════════════════════════════════════════════════════

PCT_FIELDS = ["pct_cooper", "pct_inspira_veb", "pct_inspira_mhw",
              "pct_mannington", "pct_virtua", "pct_cape"]


def _resolve_site_field(text):
    """Fuzzy-match a user-written site name to a pct_* field."""
    s = text.strip().lower()
    if s.startswith("pct_"):
        return s if s in PCT_FIELDS else None
    if "cooper" in s:
        return "pct_cooper"
    if "mullica" in s or s == "mh":
        return "pct_inspira_mhw"
    if "vineland" in s or "elmer" in s or "veb" in s:
        return "pct_inspira_veb"
    if "mannington" in s:
        return "pct_mannington"
    if "virtua" in s:
        return "pct_virtua"
    if "cape" in s:
        return "pct_cape"
    return None


def _parse_pct_value(text):
    """Parse a percentage value. Returns float (0.0-1.0) or None."""
    s = text.strip()
    if not s:
        return None
    try:
        if s.endswith("%"):
            return float(s[:-1]) / 100.0
        val = float(s)
        if val > 1.0:
            return val / 100.0
        return val
    except ValueError:
        return None


def _parse_pct_override_rule(rule):
    """Parse a pct_override rule string into structured data.

    Returns: (parsed_dict, warnings_list)
    """
    overrides = {}
    warnings = []
    pieces = re.split(r"[,;]", rule)
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if ":" in piece:
            key, val = piece.split(":", 1)
        elif "=" in piece:
            key, val = piece.split("=", 1)
        else:
            warnings.append(f"Cannot parse: '{piece}'")
            continue
        field = _resolve_site_field(key)
        if field is None:
            warnings.append(f"Unknown site: '{key.strip()}'")
            continue
        value = _parse_pct_value(val)
        if value is None:
            warnings.append(f"Bad value: '{val.strip()}'")
            continue
        overrides[field] = value
    return overrides, warnings


# ═══════════════════════════════════════════════════════════════════════════
# RULE PARSERS BY TAG TYPE
# ═══════════════════════════════════════════════════════════════════════════

def _parse_presence_only(tag, rule):
    """Tags where only presence matters — rule is free text."""
    return None, "presence_only", []


def _parse_days_per_week(tag, rule):
    """Extract the integer N from 'days_per_week: N' tag name."""
    m = re.search(r'(\d+)', tag)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 7:
            return {"days_per_week": n}, "ok", []
        return {"days_per_week": n}, "ok", [f"Value {n} outside typical 1-7 range"]
    return None, "unparseable", ["No integer found in tag name"]


def _parse_pa_rotation(tag, rule):
    """Try to extract week count from pa_rotation rule."""
    m = re.search(r'(\d+)\s*weeks?\b', rule, re.IGNORECASE)
    if m:
        return {"pa_weeks": int(m.group(1))}, "ok", []
    return None, "partial", ["Could not extract week count from rule"]


def _parse_pct_override(tag, rule):
    """Full parse of pct_override rule."""
    overrides, warnings = _parse_pct_override_rule(rule)
    if overrides:
        status = "ok" if not warnings else "partial"
        return overrides, status, warnings
    return None, "unparseable", warnings or ["Empty or unparseable override"]


def _parse_fmla(tag, rule):
    """Try to extract dates from FMLA structured text."""
    parsed = {}
    for field in ["LeaveBegDate", "LeaveEndDate", "Return to work"]:
        m = re.search(rf'{field}:\s*(\d{{4}}-\d{{2}}-\d{{2}})', rule)
        if m:
            parsed[field.lower().replace(" ", "_")] = m.group(1)
    if parsed:
        return parsed, "ok", []
    return None, "partial", ["Could not extract dates from FMLA text"]


def _parse_split_department(tag, rule):
    """Try to extract department split info."""
    parsed = {}
    m = re.search(r'(\d+)\s*weeks?', rule, re.IGNORECASE)
    if m:
        parsed["weeks"] = int(m.group(1))
    m = re.search(r'(\d+)\s*weekends?', rule, re.IGNORECASE)
    if m:
        parsed["weekends"] = int(m.group(1))
    dept_m = re.search(r'(peds|pediatrics|cardiology)', rule, re.IGNORECASE)
    if dept_m:
        parsed["department"] = dept_m.group(1).title()
    if parsed:
        return parsed, "partial", []
    return None, "partial", ["Could not extract split info"]


def _parse_protected_time(tag, rule):
    """Try to extract reduced allocation from protected_time."""
    parsed = {}
    m = re.search(r'(\d+\.?\d*)\s*wks?', rule, re.IGNORECASE)
    if m:
        parsed["weeks"] = float(m.group(1))
    m = re.search(r'(\d+\.?\d*)\s*wknd', rule, re.IGNORECASE)
    if m:
        parsed["weekends"] = float(m.group(1))
    if parsed:
        return parsed, "partial", []
    return None, "partial", ["Could not extract allocation numbers"]


# Tag name → parser function
_TAG_PARSERS = {
    "do_not_schedule":      _parse_presence_only,
    "no_elmer":             _parse_presence_only,
    "no_vineland":          _parse_presence_only,
    "swing_shift":          _parse_presence_only,
    "no_um":                _parse_presence_only,
    "days_per_week":        _parse_days_per_week,
    "pa_rotation":          _parse_pa_rotation,
    "pct_override":         _parse_pct_override,
    "fmla":                 _parse_fmla,
    "split_department":     _parse_split_department,
    "protected_time":       _parse_protected_time,
    # These record rule text only — no structured parsing
    "note":                 _parse_presence_only,
    "location_restriction": _parse_presence_only,
    "scheduling_priority":  _parse_presence_only,
    "night_constraint":     _parse_presence_only,
    "service_restriction":  _parse_presence_only,
}


# ═══════════════════════════════════════════════════════════════════════════
# MAIN EVALUATION
# ═══════════════════════════════════════════════════════════════════════════

def evaluate_tags(providers, tags_data, tag_definitions, sites):
    """Evaluate all provider tags against the tag definitions registry.

    Args:
        providers: dict from load_providers_from_excel
        tags_data: dict from load_tags_from_excel
        tag_definitions: dict from load_tag_definitions_from_excel
        sites: dict from load_sites_from_excel

    Returns:
        dict with keys:
            results: list of per-tag evaluation records
            issues: list of issue dicts
            summary: dict of counts
    """
    provider_names = list(providers.keys())
    results = []
    issues = []

    # Track for duplicate detection
    seen_tags = defaultdict(list)  # (resolved_name, base_tag) -> [indices]
    dns_providers = set()  # providers with do_not_schedule

    for tag_provider, tag_list in tags_data.items():
        for tag_entry in tag_list:
            tag_name = tag_entry["tag"]
            rule = tag_entry["rule"]

            rec = {
                "provider_name": tag_provider,
                "resolved_name": tag_provider,
                "name_status": "ok",
                "tag": tag_name,
                "base_tag_name": tag_name,
                "tag_status": "UNKNOWN",
                "rule": rule,
                "engine_interpretation": "",
                "parsed": None,
                "parse_status": "n/a",
                "parse_warnings": [],
                "issues": "",
            }

            # ── Check A: Name Resolution ──
            if tag_provider in providers:
                rec["name_status"] = "ok"
                rec["resolved_name"] = tag_provider
            else:
                canonical = to_canonical(tag_provider)
                if canonical in providers:
                    rec["resolved_name"] = canonical
                    rec["name_status"] = "resolved"
                else:
                    matched = match_provider(tag_provider, provider_names)
                    if matched:
                        rec["resolved_name"] = matched
                        rec["name_status"] = "resolved"
                    else:
                        rec["name_status"] = "UNRESOLVED"
                        issues.append({
                            "type": "unresolved_name",
                            "provider_name": tag_provider,
                            "tag": tag_name,
                            "detail": f"'{tag_provider}' does not match any provider in the Providers sheet",
                        })

            # ── Check B: Tag Recognition ──
            base_tag = tag_name.strip()

            # Handle 'days_per_week: N' pattern
            if base_tag.lower().startswith("days_per_week"):
                base_tag = "days_per_week"

            if base_tag in tag_definitions:
                defn = tag_definitions[base_tag]
                rec["tag_status"] = defn["engine_status"]
                rec["base_tag_name"] = base_tag
            else:
                # Try case-insensitive match
                for def_name in tag_definitions:
                    if def_name.lower() == base_tag.lower():
                        defn = tag_definitions[def_name]
                        rec["tag_status"] = defn["engine_status"]
                        rec["base_tag_name"] = def_name
                        break
                else:
                    rec["tag_status"] = "UNKNOWN"
                    issues.append({
                        "type": "unknown_tag",
                        "provider_name": rec["resolved_name"],
                        "tag": tag_name,
                        "detail": f"Tag '{tag_name}' is not defined in Tag Definitions",
                    })

            # ── Check C: Rule Parsing ──
            parser = _TAG_PARSERS.get(rec["base_tag_name"])
            if parser:
                parsed, parse_status, parse_warnings = parser(tag_name, rule)
                rec["parsed"] = parsed
                rec["parse_status"] = parse_status
                rec["parse_warnings"] = parse_warnings

                # Build human-readable interpretation
                rec["engine_interpretation"] = _build_interpretation(
                    rec["base_tag_name"], rec["tag_status"], parsed, parse_status, rule
                )
            else:
                rec["parse_status"] = "no_parser"
                rec["engine_interpretation"] = f"Tag recognized ({rec['tag_status']}) but no rule parser defined"

            # Track for Check D
            resolved = rec["resolved_name"]
            seen_tags[(resolved, rec["base_tag_name"])].append(len(results))
            if rec["base_tag_name"] == "do_not_schedule" and rec["name_status"] != "UNRESOLVED":
                dns_providers.add(resolved)

            results.append(rec)

    # ── Check D: Data Quality ──
    issue_strings = defaultdict(list)  # result index -> list of issue strings

    # D1: Duplicate tags
    for (prov, base_tag), indices in seen_tags.items():
        if len(indices) > 1:
            detail = f"Duplicate: '{base_tag}' appears {len(indices)} times"
            issues.append({
                "type": "duplicate_tag",
                "provider_name": prov,
                "tag": base_tag,
                "detail": detail,
            })
            for idx in indices:
                issue_strings[idx].append(detail)

    # D2: Tags on do_not_schedule providers
    for i, rec in enumerate(results):
        resolved = rec["resolved_name"]
        if resolved in dns_providers and rec["base_tag_name"] != "do_not_schedule":
            detail = "Dead tag: provider is do_not_schedule"
            issues.append({
                "type": "tag_on_excluded",
                "provider_name": resolved,
                "tag": rec["tag"],
                "detail": detail,
            })
            issue_strings[i].append(detail)

    # D3: Name format inconsistencies
    for i, rec in enumerate(results):
        if rec["name_status"] == "resolved":
            detail = f"Name format: entered as '{rec['provider_name']}', resolved to '{rec['resolved_name']}'"
            issue_strings[i].append(detail)

    # D4: Parse warnings
    for i, rec in enumerate(results):
        for w in rec.get("parse_warnings", []):
            issue_strings[i].append(f"Parse: {w}")

    # Consolidate issue strings into results
    for i, issue_list in issue_strings.items():
        results[i]["issues"] = "; ".join(issue_list)

    # ── Summary ──
    total = len(results)
    recognized = sum(1 for r in results if r["tag_status"] != "UNKNOWN")
    unrecognized = total - recognized
    active = sum(1 for r in results if r["tag_status"] == "ACTIVE")
    planned = sum(1 for r in results if r["tag_status"] == "PLANNED")
    info = sum(1 for r in results if r["tag_status"] == "INFO")
    name_issues = sum(1 for r in results if r["name_status"] == "UNRESOLVED")
    pw = sum(len(r.get("parse_warnings", [])) for r in results)
    dq = sum(1 for iss in issues if iss["type"] in ("duplicate_tag", "tag_on_excluded"))

    providers_with_tags = len(set(r["resolved_name"] for r in results if r["name_status"] != "UNRESOLVED"))

    summary = {
        "total_tags": total,
        "providers_with_tags": providers_with_tags,
        "tags_recognized": recognized,
        "tags_unrecognized": unrecognized,
        "active_count": active,
        "planned_count": planned,
        "info_count": info,
        "name_issues": name_issues,
        "parse_warnings": pw,
        "data_quality_issues": dq,
    }

    return {"results": results, "issues": issues, "summary": summary}


def _build_interpretation(base_tag, tag_status, parsed, parse_status, rule):
    """Build a human-readable interpretation of what the engine understood."""

    if tag_status == "UNKNOWN":
        return "UNKNOWN — tag not in registry"

    if base_tag == "do_not_schedule":
        return "EXCLUDE this provider from all scheduling"

    if base_tag == "no_elmer":
        return "Remove Elmer from eligible sites"

    if base_tag == "no_vineland":
        return "Remove Vineland from eligible sites"

    if base_tag == "days_per_week":
        if parsed and "days_per_week" in parsed:
            n = parsed["days_per_week"]
            return f"Week = {n} days (divide weekday count by {n}, not 5)"
        return "days_per_week — could not extract value"

    if base_tag == "swing_shift":
        return "Provider has day/swing split — bump stretch risk"

    if base_tag == "pa_rotation":
        if parsed and "pa_weeks" in parsed:
            return f"PA rotation: {parsed['pa_weeks']} weeks as PA (non-clinical)"
        return "PA rotation — could not extract week count"

    if base_tag == "pct_override":
        if parsed:
            parts = []
            field_labels = {
                "pct_cooper": "Cooper", "pct_inspira_veb": "Vineland/Elmer",
                "pct_inspira_mhw": "Mullica Hill", "pct_mannington": "Mannington",
                "pct_virtua": "Virtua", "pct_cape": "Cape",
            }
            for field, val in sorted(parsed.items()):
                label = field_labels.get(field, field)
                parts.append(f"{label}: {val:.0%}")
            return f"Override site distribution: {', '.join(parts)}"
        return "pct_override — could not parse"

    if base_tag == "fmla":
        if parsed:
            parts = []
            if "leavebegdate" in parsed:
                parts.append(f"start: {parsed['leavebegdate']}")
            if "leaveenddate" in parsed:
                parts.append(f"end: {parsed['leaveenddate']}")
            if "return_to_work" in parsed:
                parts.append(f"return: {parsed['return_to_work']}")
            return f"FMLA leave ({', '.join(parts)})" if parts else "FMLA leave (dates not parsed)"
        return "FMLA leave (no structured dates found)"

    if base_tag == "split_department":
        if parsed:
            parts = []
            if "department" in parsed:
                parts.append(parsed["department"])
            if "weeks" in parsed:
                parts.append(f"{parsed['weeks']} weeks")
            if "weekends" in parsed:
                parts.append(f"{parsed['weekends']} weekends")
            return f"Split dept: {', '.join(parts)}"
        return "Split department — could not extract details"

    if base_tag == "protected_time":
        if parsed:
            parts = []
            if "weeks" in parsed:
                parts.append(f"{parsed['weeks']} weeks")
            if "weekends" in parsed:
                parts.append(f"{parsed['weekends']} weekends")
            return f"Protected time: {', '.join(parts)}"
        return "Protected time — could not extract details"

    # For PLANNED/INFO tags with no special interpretation
    status_label = {"PLANNED": "Not yet enforced", "INFO": "Informational only"}.get(
        tag_status, tag_status
    )
    return f"{status_label}: {rule[:80]}{'...' if len(rule) > 80 else ''}"
