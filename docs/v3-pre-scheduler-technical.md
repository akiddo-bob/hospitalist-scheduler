# V3 Pre-Scheduler — Technical Reference

> Developer documentation for the pre-scheduler modules in
> `block/engines/v3/`. For user-facing guidance, see
> [v3-pre-scheduler-guide.md](v3-pre-scheduler-guide.md).

---

## 1. Architecture

The pre-scheduler runs 4 independent evaluation tasks before the scheduling
engine. Each task has its own module. An optional retrospective step compares
predictions against actual Block 3 data if schedule files exist.

```
pre_schedule.py          ← CLI, orchestration, scope guard, console output
    ├── excel_io.py      ← 4 readers (input sheets) + 4 writers (review sheets)
    ├── tag_eval.py      ← Task 1: tag validation
    ├── prior_actuals_eval.py  ← Task 2: prior weeks/weekends verification
    │       └── classify_service()  ← shared service classifier
    ├── difficulty_eval.py     ← Task 3: scheduling risk analysis
    ├── holiday_eval.py        ← Task 4: holiday obligations & tiers
    │       └── imports classify_service from prior_actuals_eval
    └── retrospective_eval.py  ← optional: B3 actual comparison
```

### Data flow

```
Excel workbook (4 input sheets)
    │
    ├── load_providers_from_excel()  ─┐
    ├── load_tags_from_excel()       ─┤
    ├── load_tag_definitions_from_excel() ─┤
    └── load_sites_from_excel()      ─┤
                                      │
                    evaluate_tags() ←──┘  → Tag Review sheet + tag_config.json
                    evaluate_prior_actuals() → Prior Actuals Review sheet
                         │
                         ├── (computed actuals feed into →)
                         │
                    evaluate_difficulty() → Scheduling Difficulty sheet
                    evaluate_holidays()   → Holiday Review sheet
                         │
                    [if B3 files exist]
                    compute_block3_actuals()
                    evaluate_difficulty_retrospective() → retro columns
                    evaluate_holiday_retrospective()    → retro columns
                         │
                    write_*_sheet() → Excel workbook (review sheets)
                    json.dump()     → pre_schedule_output.json
```

### Dependency chain

- Task 2 feeds Task 3: computed prior actuals → accurate remaining weeks
- Task 2 feeds Task 4: shared `classify_service()` import (no data dependency)
- Tasks 3 and 4 are independent of each other
- Retrospective depends on Tasks 3 and/or 4 completing first

### Reader output shapes

The `load_*_from_excel()` functions return the same data shapes as
`block/engines/shared/loader.py`, so the engine can swap between Google Sheet
and Excel sources without code changes.

---

## 2. Module Reference

### `pre_schedule.py` — Orchestrator

**CLI arguments:** `--excel`, `--no-write-back`, `--output-dir`,
`--schedules-dir`, `--availability-dir`, `--tasks`, `--block`, `--cycle`

**Key functions:**
- `enforce_block3_scope_guard(block, cycle)` — raises `RuntimeError` if not
  Block 3 / cycle 25-26
- `build_tag_config()` — builds backward-compatible `tag_config.json`
- `print_*_summary()` — console output per task
- `main()` — argument parsing, task dispatch, file I/O

### `excel_io.py` — Excel I/O

**Readers:**
- `load_providers_from_excel(wb)` → `dict[name, {field: value}]`
- `load_tags_from_excel(wb)` → `dict[name, [{tag, rule}]]`
- `load_tag_definitions_from_excel(wb)` → `dict[tag_name, {engine_status, ...}]`
- `load_sites_from_excel(wb)` → `dict[(site, day_type), providers_needed]`

**Writers:**
- `write_tag_review_sheet(wb, results, summary)`
- `write_prior_actuals_review_sheet(wb, pa_result)`
- `write_difficulty_sheet(wb, diff_result, retro_records=None, retro_summary=None)`
- `write_holiday_review_sheet(wb, hol_result, retro_records=None, retro_summary=None)`

**Shared helpers:**
- `_col_letter(n)` — 1-based column number to letter(s)
- `_write_header_row(ws, columns)` — styled header with widths
- `_write_summary_block(ws, start_row, lines)` — summary below data

### `tag_eval.py` — Task 1

**Entry point:** `evaluate_tags(providers, tags_data, tag_definitions, sites)`

**Returns:** `{results, issues, summary}`

**Name resolution pipeline:**
1. Exact match against Providers sheet keys
2. Canonical form via `to_canonical()` (LAST, FIRST → normalized)
3. Fuzzy match via `match_provider()` (Levenshtein-based)
4. If all fail → `name_status = "UNRESOLVED"`

**Tag parsing by type:**
- Presence-only tags: `do_not_schedule`, `no_elmer`, `no_vineland`,
  `swing_shift`, `no_um`, `note`, `location_restriction`,
  `scheduling_priority`, `night_constraint`, `service_restriction`
- Structured parse:
  - `pct_override` — parses `"cooper: 50%, mh: 50%"` →
    `{"pct_cooper": 0.5, "pct_inspira_mhw": 0.5}`
  - `days_per_week` — extracts integer from tag name/rule
  - `pa_rotation` — extracts week count
  - `fmla` — extracts date ranges
  - `split_department`, `protected_time` — presence + rule text

**Issue categories:**
- `unresolved_name` — tag provider not found
- `unknown_tag` — tag name not in registry
- `duplicate_tag` — same tag appears multiple times
- `tag_on_excluded` — tag on a `do_not_schedule` provider
- `parse_warning` — rule text couldn't be fully parsed

### `prior_actuals_eval.py` — Task 2

**Entry point:** `evaluate_prior_actuals(providers, tags_data, schedules_dir)`

**Returns:** `{computed, comparisons, discrepancies, missing_from_schedule,
missing_from_excel, summary}`

**Key functions:**
- `classify_service(service_name, hours)` → `"day"` / `"night"` / `"swing"` /
  `"exclude"`. Authoritative rules in
  [block-scheduling-rules.md](block-scheduling-rules.md) Section 1.3.
- `compute_prior_actuals(schedules_dir)` → per-provider shift counts
- `parse_date(date_str)` → `date` object from `M/D/YYYY`

**Shift deduplication:** When a provider has multiple services on the same day,
the highest-priority type wins: night (3) > swing (2) > weekday/weekend (1).

**Conversion:** weekday_shifts ÷ 5 = weeks, weekend_shifts ÷ 2 = weekends.

**Constants:**
- `BLOCK_1_START/END` = `2025-06-30` / `2025-11-02`
- `BLOCK_2_START/END` = `2025-11-03` / `2026-03-01`
- `DISCREPANCY_THRESHOLD` = 0.5
- `PRIOR_FILES` = 9 HTML files (June 2025 – February 2026)

### `difficulty_eval.py` — Task 3

**Entry point:** `evaluate_difficulty(providers, tags_data, sites, prior_actuals=None)`

**Returns:** `{records, by_risk, summary}`

**Risk classification (`classify_risk`):**

| Density | Swing? | Risk Level |
|---------|--------|-----------|
| >= 0.65 | any | HIGH |
| >= 0.47 | any | ELEVATED |
| 0.35 – 0.46 | no | MODERATE |
| 0.35 – 0.46 | yes | ELEVATED |
| 0.30 – 0.34 | yes | MODERATE |
| < 0.30 | any | LOW |

**Density:** `remaining_weeks / BLOCK_WEEKS` where `BLOCK_WEEKS = 17`.

When `prior_actuals` is provided, remaining weeks are recomputed as
`annual_weeks - computed_prior_weeks` instead of using the Excel
`weeks_remaining` value.

**Fair share:** `ceil(annual_weeks / 3)` — expected work per block.

**Capacity status:**
- `tight` — remaining > fair_share × 1.2
- `excess` — remaining < fair_share × 0.5
- `normal` — in between

**`get_eligible_sites(pname, pdata, tags_data)`:** Returns sites where
`pct_* > 0`, minus `no_elmer` / `no_vineland` tag restrictions. Uses
`PCT_TO_SITES` mapping from `shared/loader.py`.

**Compounding factors detected:**
- Swing shift + density >= 0.30
- PA rotation (with week count)
- Days-per-week < 5
- <= 2 eligible sites
- Tight capacity

### `holiday_eval.py` — Task 4

**Entry point:** `evaluate_holidays(providers, tags_data, schedules_dir, availability_dir)`

**Returns:** `{records, holiday_workers, issues, supply_vs_demand, summary}`

**Key functions:**
- `scan_holiday_workers(schedules_dir)` → `{holiday_name: set(canonical_names)}`
- `is_site_director(pname)` — checks against `SITE_DIRECTOR_NAMES` list
- `get_holiday_requirement(fte, site_dir)` — FTE-based requirement per
  [block-scheduling-rules.md](block-scheduling-rules.md) Section 4.2
- `_compute_memorial_day_priority(records)` — tier assignment
- `_detect_issues(records)` — issue detection

**Tier assignment order:**
1. EXEMPT (priority 97) — requirement = 0
2. UNAVAILABLE (priority 95) — unavailable during Memorial Day week
3. MET (priority 90) — already fulfilled requirement
4. MUST (priority 10 - owe) — owes holidays, Memorial Day NOT a preference
5. SHOULD (priority 20 - owe) — owes holidays, Memorial Day IS a preference

Lower priority number = scheduled first.

**Issue types:**
- `overworked` — worked more holidays than required
- `both_xmas_ny` — worked both Christmas and New Year's
- `neither_xmas_ny` — worked neither (and still owes)
- `pref_violated` — scheduled on a preference holiday in B1/B2
- `impossible` — owes >= 2 holidays with only Memorial Day remaining

**Constants:**
- `HOLIDAYS` — 6 dates for cycle 25-26
- `MEMORIAL_WEEK_DATES` — May 25-29, 2026
- `SITE_DIRECTOR_NAMES` — 8 names (canonical form)
- `PRIOR_FILES` — 9 HTML files for B1+B2 parsing

### `retrospective_eval.py` — Retrospective

**Entry points:**
- `compute_block3_actuals(schedules_dir)` → `(dict, files_parsed)`
- `evaluate_difficulty_retrospective(diff_result, b3_actuals)` → `(records, summary)`
- `evaluate_holiday_retrospective(hol_result, b3_actuals)` → `(records, summary)`

**Triggered:** Automatically when Block 3 HTML files (2026-03 through 2026-06)
exist in `schedules_dir`.

**Difficulty accuracy labels:**

| Label | Meaning |
|-------|---------|
| CORRECT | Predicted risk matched actual outcome |
| CLOSE | Predicted HIGH, got extended (not hard) violation |
| OVER | Predicted higher risk than occurred |
| UNDER | Predicted lower risk, got extended stretch |
| MISS | Predicted LOW, got hard violation |
| NO_DATA | Provider not found in B3 schedule |

"OVER" predictions are expected — they represent risks that good scheduling
successfully avoided. Swap-caused violations are outside pre-scheduler control.

**Holiday outcome labels:**

| Label | Meaning |
|-------|---------|
| CORRECT | Tier prediction aligned with outcome |
| NOT_SCHEDULED | MUST tier, didn't work Memorial Day |
| OVERRIDE | SHOULD tier, preference was overridden |
| HONORED | SHOULD tier, preference was respected |
| EXTRA | MET tier, worked Memorial Day anyway |
| ERROR | UNAVAILABLE tier, but was scheduled (stale availability) |

---

## 3. Scope Guard & Generalization

`enforce_block3_scope_guard()` raises `RuntimeError` if `block != "3"` or
`cycle != "25-26"`. This is enforced when Tasks 2 or 4 are requested.

### What's hardcoded for Block 3 / cycle 25-26

| Item | Location | Value |
|------|----------|-------|
| Block date ranges | `prior_actuals_eval.py` | B1: 6/30/25 – 11/2/25, B2: 11/3/25 – 3/1/26 |
| Block 3 dates | `retrospective_eval.py` | 3/2/26 – 6/28/26 |
| Block weeks | `difficulty_eval.py` | 17 |
| Holiday dates | `holiday_eval.py` | 6 specific dates |
| Memorial Day week | `holiday_eval.py` | May 25-29, 2026 |
| Prior HTML files | `prior_actuals_eval.py`, `holiday_eval.py` | 9 filenames |
| B3 HTML files | `retrospective_eval.py` | 4 filenames |
| Site directors | `holiday_eval.py` | 8 names |

### To generalize

- Parameterize block start/end dates by cycle and block number
- Load holiday calendar dynamically (or from config)
- Derive prior file lists from date ranges
- Move site director list to Excel input or config

---

## 4. Extending the System

### Adding a new tag

1. Add the tag to the **Tag Definitions** sheet in the Excel workbook
2. Add a parse case in `tag_eval.py` → `_parse_rule()` function
3. If the tag affects scheduling logic, consume it in the relevant evaluator

### Adding a new evaluation task

1. Create `new_eval.py` with an `evaluate_*()` function
2. Add a `write_*_sheet()` function in `excel_io.py`
3. Wire into `pre_schedule.py`:
   - Import the evaluator and writer
   - Add task number to the dispatch logic in `main()`
   - Add a `print_*_summary()` function
   - Update `--tasks` default and scope guard if needed

### Changing risk thresholds

Update constants in `difficulty_eval.py` → `classify_risk()`. The thresholds
were calibrated against actual Block 3 outcomes:

```python
def classify_risk(density, is_swing=False):
    if density >= 0.65:     return "HIGH"
    elif density >= 0.47:   return "ELEVATED"
    elif density >= 0.35:   return "ELEVATED" if is_swing else "MODERATE"
    elif density >= 0.30 and is_swing: return "MODERATE"
    else:                   return "LOW"
```

### Changing holiday requirements

1. Update [block-scheduling-rules.md](block-scheduling-rules.md) Section 4.2
   first (source of truth for rules)
2. Then update `holiday_eval.py` → `get_holiday_requirement()` to match

---

## 5. Key Constants Reference

| Constant | Value | File | Line |
|----------|-------|------|------|
| `BLOCK_WEEKS` | 17 | `difficulty_eval.py` | 27 |
| `BLOCK_1_START` | 2025-06-30 | `prior_actuals_eval.py` | 29 |
| `BLOCK_1_END` | 2025-11-02 | `prior_actuals_eval.py` | 30 |
| `BLOCK_2_START` | 2025-11-03 | `prior_actuals_eval.py` | 31 |
| `BLOCK_2_END` | 2026-03-01 | `prior_actuals_eval.py` | 32 |
| `BLOCK_3_START` | 2026-03-02 | `retrospective_eval.py` | 36 |
| `BLOCK_3_END` | 2026-06-28 | `retrospective_eval.py` | 37 |
| `DISCREPANCY_THRESHOLD` | 0.5 | `prior_actuals_eval.py` | 40 |
| `MEMORIAL_WEEK_DATES` | May 25-29 | `holiday_eval.py` | 48 |
| `SITE_DIRECTOR_NAMES` | 8 names | `holiday_eval.py` | 59 |
| `PCT_TO_SITES` | 6 mappings | `difficulty_eval.py` | 31 |
| `PCT_FIELDS` | 6 fields | `tag_eval.py` | 27 |

---

## 6. Output Schemas

### `pre_schedule_output.json`

```json
{
  "metadata": {
    "generated_at": "2026-02-21T10:30:00",
    "source_file": "hospitalist_scheduler.xlsx",
    "block": "3",
    "cycle": "25-26",
    "block_weeks": 17,
    "tasks_run": [1, 2, 3, 4]
  },
  "tag_config": { "metadata": {}, "tag_definitions": {}, "provider_tags": {}, "issues": [] },
  "prior_actuals": { "computed": {}, "discrepancies": [], "missing_from_*": [], "summary": {} },
  "difficulty": { "records": [], "by_risk": {}, "summary": {} },
  "holiday": { "records": [], "holiday_workers": {}, "issues": {}, "supply_vs_demand": {}, "summary": {} }
}
```

### `tag_config.json` (backward compat)

```json
{
  "metadata": { "generated_at": "", "total_tags": 0, "tags_recognized": 0 },
  "tag_definitions": { "tag_name": { "engine_status": "ACTIVE" } },
  "provider_tags": { "PROVIDER, NAME": [{ "tag": "", "engine_status": "", "parsed": {} }] },
  "issues": []
}
```
