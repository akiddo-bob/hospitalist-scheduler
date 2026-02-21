"""
V3 Pre-Scheduler — Provider Tag Evaluation + Prior Actuals + Difficulty + Holiday.

Reads the hospitalist_scheduler.xlsx workbook and runs up to 4 evaluation tasks:
  1. Tag evaluation (tag_eval.py)
  2. Prior actuals verification (prior_actuals_eval.py)
  3. Scheduling difficulty analysis (difficulty_eval.py)
  4. Holiday evaluation (holiday_eval.py)

Outputs:
  - Excel sheets written back to the workbook (one per task)
  - tag_config.json (backward compat)
  - pre_schedule_output.json (combined output for all tasks)

Usage:
    python -m block.engines.v3.pre_schedule
    python -m block.engines.v3.pre_schedule --tasks 1,2
    python -m block.engines.v3.pre_schedule --no-write-back
"""

import argparse
import json
import os
import sys
from datetime import datetime

from openpyxl import load_workbook

# Ensure project root is on sys.path
_V3_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINES_DIR = os.path.dirname(_V3_DIR)
_BLOCK_DIR = os.path.dirname(_ENGINES_DIR)
_PROJECT_ROOT = os.path.dirname(_BLOCK_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from block.engines.v3.excel_io import (
    load_providers_from_excel,
    load_tags_from_excel,
    load_tag_definitions_from_excel,
    load_sites_from_excel,
    write_tag_review_sheet,
    write_prior_actuals_review_sheet,
    write_difficulty_sheet,
    write_holiday_review_sheet,
)
from block.engines.v3.tag_eval import evaluate_tags
from block.engines.v3.prior_actuals_eval import evaluate_prior_actuals
from block.engines.v3.difficulty_eval import evaluate_difficulty
from block.engines.v3.holiday_eval import evaluate_holidays
from block.engines.v3.retrospective_eval import (
    compute_block3_actuals,
    evaluate_difficulty_retrospective,
    evaluate_holiday_retrospective,
)


# ═══════════════════════════════════════════════════════════════════════════
# DEFAULTS
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_EXCEL = os.path.join(_V3_DIR, "input", "hospitalist_scheduler.xlsx")
DEFAULT_OUTPUT_DIR = os.path.join(_V3_DIR, "output")
DEFAULT_SCHEDULES_DIR = os.path.join(_PROJECT_ROOT, "input", "monthlySchedules")
DEFAULT_AVAILABILITY_DIR = os.path.join(_PROJECT_ROOT, "input", "individualSchedules")


# ═══════════════════════════════════════════════════════════════════════════
# SCOPE GUARD
# ═══════════════════════════════════════════════════════════════════════════

def enforce_block3_scope_guard(block, cycle):
    """Raise RuntimeError if not scheduling Block 3 of cycle 25-26.

    Block dates, holidays, and prior actuals logic are hardcoded for
    this specific block. Generalization is a known future task.
    """
    if block != "3" or cycle != "25-26":
        raise RuntimeError(
            f"Scope guard: This pre-scheduler is hardcoded for Block 3 of "
            f"cycle 25-26. Got block={block}, cycle={cycle}. "
            f"Generalization is not yet implemented."
        )


# ═══════════════════════════════════════════════════════════════════════════
# TAG CONFIG JSON (backward compat)
# ═══════════════════════════════════════════════════════════════════════════

def build_tag_config(providers, eval_result, tag_definitions, source_file):
    """Build the tag_config.json structure."""
    results = eval_result["results"]
    issues = eval_result["issues"]
    summary = eval_result["summary"]

    provider_tags = {}
    for pname in providers:
        provider_tags[pname] = []

    for rec in results:
        if rec["name_status"] == "UNRESOLVED":
            continue
        resolved = rec["resolved_name"]
        entry = {
            "tag": rec["base_tag_name"],
            "original_tag": rec["tag"],
            "engine_status": rec["tag_status"],
            "rule_text": rec["rule"],
            "parsed": rec["parsed"],
            "parse_status": rec["parse_status"],
        }
        if rec["parse_warnings"]:
            entry["parse_warnings"] = rec["parse_warnings"]
        if resolved in provider_tags:
            provider_tags[resolved].append(entry)
        else:
            provider_tags[resolved] = [entry]

    return {
        "metadata": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source_file": os.path.basename(source_file),
            "total_tags": summary["total_tags"],
            "tags_recognized": summary["tags_recognized"],
            "tags_unrecognized": summary["tags_unrecognized"],
            "providers_with_tags": summary["providers_with_tags"],
            "data_quality_issues": summary["data_quality_issues"],
        },
        "tag_definitions": tag_definitions,
        "provider_tags": provider_tags,
        "issues": issues,
    }


# ═══════════════════════════════════════════════════════════════════════════
# CONSOLE OUTPUT — TASK 1
# ═══════════════════════════════════════════════════════════════════════════

def print_tag_summary(eval_result, source_file):
    """Print tag evaluation summary."""
    s = eval_result["summary"]
    issues = eval_result["issues"]

    print()
    print("=" * 60)
    print("  TASK 1 — Tag Evaluation")
    print(f"  Source: {os.path.basename(source_file)}")
    print("=" * 60)
    print()
    print(f"  Tags evaluated:        {s['total_tags']}")
    print(f"  Providers with tags:   {s['providers_with_tags']}")
    print(f"  Tags recognized:       {s['tags_recognized']}")
    print(f"    ACTIVE:              {s['active_count']}")
    print(f"    PLANNED:             {s['planned_count']}")
    print(f"    INFO:                {s['info_count']}")
    print(f"  Tags unrecognized:     {s['tags_unrecognized']}")
    print(f"  Name resolution issues: {s['name_issues']}")
    print(f"  Parse warnings:         {s['parse_warnings']}")
    print(f"  Data quality issues:    {s['data_quality_issues']}")

    if issues:
        print()
        for iss in issues:
            itype = iss["type"].replace("_", " ").title()
            prov = iss.get("provider_name", "")
            tag = iss.get("tag", "")
            detail = iss.get("detail", "")
            print(f"  [{itype}] {prov} / {tag}")
            print(f"    {detail}")
    print()


# ═══════════════════════════════════════════════════════════════════════════
# CONSOLE OUTPUT — TASK 2
# ═══════════════════════════════════════════════════════════════════════════

def print_prior_actuals_summary(pa_result):
    """Print prior actuals verification summary."""
    s = pa_result["summary"]

    print()
    print("=" * 60)
    print("  TASK 2 — Prior Actuals Verification")
    print(f"  Files parsed: {s['files_parsed']}/{s['files_expected']}")
    print("=" * 60)
    print()
    print(f"  Providers compared:      {s['providers_compared']}")
    print(f"  Matching (< 0.5 diff):   {s['providers_matching']}")
    print(f"  Discrepancies:           {s['providers_with_discrepancy']}")
    print(f"  Missing from schedule:   {s['missing_from_schedule']}")
    print(f"  Missing from Excel:      {s['missing_from_excel']}")

    discs = pa_result.get("discrepancies", [])
    if discs:
        print()
        print("  DISCREPANCIES:")
        print("  " + "-" * 56)
        for d in discs:
            print(f"  {d['provider']} — {d['detail']}")

    missing = pa_result.get("missing_from_excel", [])
    if missing:
        print()
        print(f"  IN SCHEDULE BUT NOT IN EXCEL ({len(missing)}):")
        for name in missing[:10]:
            print(f"    {name}")
        if len(missing) > 10:
            print(f"    ... and {len(missing) - 10} more")
    print()


# ═══════════════════════════════════════════════════════════════════════════
# CONSOLE OUTPUT — TASK 3
# ═══════════════════════════════════════════════════════════════════════════

def print_difficulty_summary(diff_result):
    """Print scheduling difficulty summary."""
    s = diff_result["summary"]
    by_risk = diff_result["by_risk"]

    print()
    print("=" * 60)
    print("  TASK 3 — Scheduling Difficulty Analysis")
    print("=" * 60)
    print()
    print(f"  Eligible providers:    {s['total_eligible']}")
    print(f"  HIGH risk:             {s['high_count']}")
    print(f"  ELEVATED risk:         {s['elevated_count']}")
    print(f"  MODERATE risk:         {s['moderate_count']}")
    print(f"  LOW risk:              {s['low_count']}")
    print(f"  Mean density:          {s['mean_density']:.0%}")
    print(f"  Tight capacity:        {s['tight_capacity_count']}")
    print(f"  Single-site providers: {s['single_site_count']}")

    if by_risk["HIGH"]:
        print()
        print("  HIGH risk providers:")
        for rec in diff_result["records"]:
            if rec["risk_level"] == "HIGH":
                print(f"    {rec['provider']:<35s} density={rec['density']:.0%}  "
                      f"remaining={rec['remaining_weeks']:.1f} wks")
    print()


# ═══════════════════════════════════════════════════════════════════════════
# CONSOLE OUTPUT — TASK 4
# ═══════════════════════════════════════════════════════════════════════════

def print_holiday_summary(hol_result):
    """Print holiday evaluation summary."""
    s = hol_result["summary"]
    by_tier = s.get("by_tier", {})
    issue_counts = s.get("issue_counts", {})

    print()
    print("=" * 60)
    print("  TASK 4 — Holiday Evaluation (Memorial Day)")
    print("=" * 60)
    print()
    print(f"  Eligible providers:    {s['total_eligible']}")
    print(f"  MUST (Memorial Day):   {by_tier.get('MUST', 0)}")
    print(f"  SHOULD (pref conflict): {by_tier.get('SHOULD', 0)}")
    print(f"  MET:                   {by_tier.get('MET', 0)}")
    print(f"  UNAVAILABLE:           {by_tier.get('UNAVAILABLE', 0)}")
    print(f"  EXEMPT:                {by_tier.get('EXEMPT', 0)}")
    print()
    print(f"  Flagged issues:        {s['total_issues']}")
    for itype, count in sorted(issue_counts.items()):
        if count > 0:
            print(f"    {itype}: {count}")
    print()


# ═══════════════════════════════════════════════════════════════════════════
# CONSOLE OUTPUT — RETROSPECTIVE
# ═══════════════════════════════════════════════════════════════════════════

def print_difficulty_retro_summary(retro_summary):
    """Print difficulty retrospective summary."""
    print()
    print("  ── Difficulty Retrospective ──")
    print(f"  Providers with B3 data:   {retro_summary['providers_with_data']}")
    print(f"  Correct/Close predictions: {retro_summary['correct_predictions']}")
    print(f"  Over-predictions:          {retro_summary['over_predictions']}")
    print(f"  Under-predictions:         {retro_summary['under_predictions']}")
    print(f"  Actual hard violations:    {retro_summary['actual_hard_violations']}")
    print(f"  Actual extended (8-12):    {retro_summary['actual_extended_only']}")
    print(f"  Prediction accuracy:       {retro_summary['accuracy_pct']}%")


def print_holiday_retro_summary(retro_summary):
    """Print holiday retrospective summary."""
    print()
    print("  ── Holiday Retrospective ──")
    print(f"  Providers with B3 data:    {retro_summary['providers_with_data']}")
    print(f"  Actually worked Mem Day:   {retro_summary['actually_worked_memorial']}")
    print(f"  MUST tier who worked:      {retro_summary['must_worked']}/{retro_summary['must_total']} "
          f"({retro_summary['must_rate']}%)")
    print(f"  Unavailable errors:        {retro_summary['unavailable_errors']}")
    print(f"  Preference overrides:      {retro_summary['preference_overrides']}")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="V3 Pre-Scheduler: Tag Evaluation + Prior Actuals + Difficulty + Holiday"
    )
    parser.add_argument(
        "--excel", default=DEFAULT_EXCEL,
        help="Path to hospitalist_scheduler.xlsx (default: v3/input/)",
    )
    parser.add_argument(
        "--no-write-back", action="store_true",
        help="Skip writing review sheets back to the workbook",
    )
    parser.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR,
        help="Directory for JSON output (default: v3/output/)",
    )
    parser.add_argument(
        "--schedules-dir", default=DEFAULT_SCHEDULES_DIR,
        help="Directory with B1+B2 monthly HTML schedules",
    )
    parser.add_argument(
        "--availability-dir", default=DEFAULT_AVAILABILITY_DIR,
        help="Directory with individual schedule JSONs",
    )
    parser.add_argument(
        "--tasks", default="1,2,3,4",
        help="Comma-separated task numbers to run (default: 1,2,3,4)",
    )
    parser.add_argument(
        "--block", default="3",
        help="Block number (default: 3)",
    )
    parser.add_argument(
        "--cycle", default="25-26",
        help="Cycle identifier (default: 25-26)",
    )
    args = parser.parse_args()

    excel_path = os.path.abspath(args.excel)
    output_dir = os.path.abspath(args.output_dir)
    schedules_dir = os.path.abspath(args.schedules_dir)
    availability_dir = os.path.abspath(args.availability_dir)
    task_nums = [int(t.strip()) for t in args.tasks.split(",")]

    if not os.path.exists(excel_path):
        print(f"ERROR: Excel file not found: {excel_path}", file=sys.stderr)
        sys.exit(1)

    # Scope guard — tasks 2 and 4 have hardcoded Block 3 / cycle 25-26 logic
    if any(t in (2, 4) for t in task_nums):
        enforce_block3_scope_guard(args.block, args.cycle)

    os.makedirs(output_dir, exist_ok=True)

    # ── Load workbook ──
    print(f"Loading workbook: {excel_path}")
    wb = load_workbook(excel_path)

    providers = load_providers_from_excel(wb)
    tags_data = load_tags_from_excel(wb)
    tag_definitions = load_tag_definitions_from_excel(wb)
    sites = load_sites_from_excel(wb)

    print(f"  Providers: {len(providers)}")
    print(f"  Provider Tags: {sum(len(v) for v in tags_data.values())} tags "
          f"across {len(tags_data)} providers")
    print(f"  Tag Definitions: {len(tag_definitions)}")
    print(f"  Sites: {len(sites)} demand rows")

    combined_output = {
        "metadata": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source_file": os.path.basename(excel_path),
            "block": args.block,
            "cycle": args.cycle,
            "block_weeks": 17,
            "tasks_run": task_nums,
        },
    }

    # Track results for cross-task dependencies
    tag_config = None
    pa_result = None
    diff_result = None
    hol_result = None

    # ══════════════════════════════════════════════════════════════════════
    # TASK 1 — Tag Evaluation
    # ══════════════════════════════════════════════════════════════════════
    if 1 in task_nums:
        eval_result = evaluate_tags(providers, tags_data, tag_definitions, sites)
        print_tag_summary(eval_result, excel_path)

        if not args.no_write_back:
            print("  Writing 'Tag Review' sheet...")
            write_tag_review_sheet(wb, eval_result["results"], eval_result["summary"])

        tag_config = build_tag_config(providers, eval_result, tag_definitions, excel_path)
        combined_output["tag_config"] = tag_config

    # ══════════════════════════════════════════════════════════════════════
    # TASK 2 — Prior Actuals Verification
    # ══════════════════════════════════════════════════════════════════════
    if 2 in task_nums:
        pa_result = evaluate_prior_actuals(providers, tags_data, schedules_dir)
        print_prior_actuals_summary(pa_result)

        if not args.no_write_back:
            print("  Writing 'Prior Actuals Review' sheet...")
            write_prior_actuals_review_sheet(wb, pa_result)

        # Store computed actuals (without the full comparison records for JSON size)
        combined_output["prior_actuals"] = {
            "computed": pa_result["computed"],
            "discrepancies": pa_result["discrepancies"],
            "missing_from_schedule": pa_result["missing_from_schedule"],
            "missing_from_excel": pa_result["missing_from_excel"],
            "summary": pa_result["summary"],
        }

    # ══════════════════════════════════════════════════════════════════════
    # TASK 3 — Scheduling Difficulty Analysis
    # ══════════════════════════════════════════════════════════════════════
    if 3 in task_nums:
        diff_result = evaluate_difficulty(
            providers, tags_data, sites,
            prior_actuals=pa_result,
        )
        print_difficulty_summary(diff_result)

        # Slim down for JSON (drop full eligible_sites lists)
        slim_records = []
        for r in diff_result["records"]:
            slim = dict(r)
            slim["eligible_sites"] = len(r.get("eligible_sites", []))
            slim_records.append(slim)
        combined_output["difficulty"] = {
            "records": slim_records,
            "by_risk": diff_result["by_risk"],
            "summary": diff_result["summary"],
        }

    # ══════════════════════════════════════════════════════════════════════
    # TASK 4 — Holiday Evaluation
    # ══════════════════════════════════════════════════════════════════════
    if 4 in task_nums:
        hol_result = evaluate_holidays(
            providers, tags_data, schedules_dir, availability_dir,
        )
        print_holiday_summary(hol_result)

        # Slim down issue records for JSON (avoid nested full records)
        slim_issues = {}
        for itype, recs in hol_result["issues"].items():
            slim_issues[itype] = [r["provider"] for r in recs]
        combined_output["holiday"] = {
            "records": hol_result["records"],
            "holiday_workers": hol_result["holiday_workers"],
            "issues": slim_issues,
            "supply_vs_demand": hol_result["supply_vs_demand"],
            "summary": hol_result["summary"],
        }

    # ══════════════════════════════════════════════════════════════════════
    # RETROSPECTIVE — Compare predictions vs actual Block 3 schedule
    # ══════════════════════════════════════════════════════════════════════
    diff_retro_records = None
    diff_retro_summary = None
    hol_retro_records = None
    hol_retro_summary = None

    if (diff_result or hol_result) and os.path.isdir(schedules_dir):
        # Check if Block 3 schedule files exist
        b3_files_exist = any(
            os.path.exists(os.path.join(schedules_dir, f))
            for f in ["2026-03.html", "2026-04.html", "2026-05.html", "2026-06.html"]
        )
        if b3_files_exist:
            print("\n  Parsing Block 3 actuals for retrospective analysis...")
            b3_actuals, b3_files = compute_block3_actuals(schedules_dir)
            print(f"  Block 3 files parsed: {b3_files}/4, "
                  f"providers found: {len(b3_actuals)}")

            if diff_result and b3_actuals:
                diff_retro_records, diff_retro_summary = (
                    evaluate_difficulty_retrospective(diff_result, b3_actuals)
                )
                print_difficulty_retro_summary(diff_retro_summary)

            if hol_result and b3_actuals:
                hol_retro_records, hol_retro_summary = (
                    evaluate_holiday_retrospective(hol_result, b3_actuals)
                )
                print_holiday_retro_summary(hol_retro_summary)

    # ══════════════════════════════════════════════════════════════════════
    # WRITE EXCEL SHEETS (after retrospective so columns are included)
    # ══════════════════════════════════════════════════════════════════════
    if not args.no_write_back:
        if diff_result:
            print("  Writing 'Scheduling Difficulty' sheet...")
            write_difficulty_sheet(wb, diff_result, diff_retro_records, diff_retro_summary)
        if hol_result:
            print("  Writing 'Holiday Review' sheet...")
            write_holiday_review_sheet(wb, hol_result, hol_retro_records, hol_retro_summary)

    # ══════════════════════════════════════════════════════════════════════
    # SAVE OUTPUTS
    # ══════════════════════════════════════════════════════════════════════
    if not args.no_write_back:
        wb.save(excel_path)
        print(f"\n  Saved workbook: {excel_path}")

    # Write tag_config.json (backward compat)
    if tag_config:
        json_path = os.path.join(output_dir, "tag_config.json")
        with open(json_path, "w") as f:
            json.dump(tag_config, f, indent=2, default=str)
        print(f"  Wrote: {json_path}")

    # Write combined output
    combined_path = os.path.join(output_dir, "pre_schedule_output.json")
    with open(combined_path, "w") as f:
        json.dump(combined_output, f, indent=2, default=str)
    print(f"  Wrote: {combined_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
