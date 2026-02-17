# Architecture Reference

Code structure, data flow, module interfaces, and key data structures.

## Module Map

```
┌─────────────────────────────────────────────────────────────┐
│                    LONG CALL PIPELINE                        │
│                                                             │
│  parse_schedule.py ──► assign_longcall.py ──► generate_report.py
│       (parse)              (engine)              (HTML)      │
│                                │                             │
│                         validate_reports.py                  │
│                            (14 checks)                       │
│                                                             │
│  analyze_manual_lc.py                                       │
│    (ground truth validation against 3 blocks)               │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                  BLOCK SCHEDULE PIPELINE                     │
│                                                             │
│  block_schedule_engine.py ──► generate_block_report.py      │
│       (engine)                     (HTML)                    │
│                                                             │
│  recalculate_prior_actuals.py                               │
│    (compute Block 1+2 worked weeks for Block 3 targets)     │
│                                                             │
│  debug_analyze.py                                           │
│    (debugging & constraint analysis)                        │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                     DEPLOYMENT                               │
│                                                             │
│  deploy_pages.sh                                            │
│    (GitHub Pages deployment with password gate)             │
└─────────────────────────────────────────────────────────────┘
```

## File-by-File Reference

### parse_schedule.py (422 lines)
**Purpose:** Parse Amion HTML schedule exports into structured JSON.

**Key classes:**
- `AmionScheduleParser(HTMLParser)` — Stateful HTML parser

**Key functions:**
- `parse_schedule(filepath)` → schedule dict
- `merge_schedules(schedules)` → consolidated schedule
- `write_json(schedule, path)` / `write_csv(schedule, path)`

**Features:** Extracts dates, services, assignments, moonlighting flags (xpay_dull.gif), telehealth flags, cell notes.

---

### assign_longcall.py (1939 lines)
**Purpose:** Core long call assignment engine.

**Main entry point:**
```python
def assign_long_calls(schedule, config, seed=None):
    """Returns (assignments, flags, provider_stats)"""
```

**Key functions:**
| Function | Purpose |
|----------|---------|
| `load_schedule()` | Load parsed schedule JSON |
| `build_daily_data()` | Consolidate provider data by day |
| `identify_stretches()` | Detect consecutive work stretches |
| `is_standalone_weekend()` | Classify weekend-only stretches |
| `split_stretch_into_weeks()` | Break long stretches into weeks |
| `is_moonlighting_in_stretch()` | Detect moonlighting periods |
| `get_provider_category()` | Teaching vs direct care classification |
| `stretch_has_weekday_and_weekend()` | Check mixed stretch |
| `find_best_assignment()` | Pick best day/slot for a provider |
| `find_double_filler()` | Assign second LC (weekday+weekend pair) |
| `_assign_slot()` / `_unassign_slot()` | Centralized state updates |

**Interface contract:** `generate_report.py` imports 23 items. `validate_reports.py` imports 18 items.

---

### generate_report.py (1445 lines)
**Purpose:** HTML report generator for LC assignments.

**Main entry point:**
```python
def generate_report(assignments, flags, provider_stats, config, seed):
    """Returns HTML string"""
```

**Key sections generated:**
- Full schedule (daily table)
- Monthly schedule tables
- Provider detail (collapsible, color-coded)
- Provider summary (sortable, filterable)
- Weekend LC pivot
- DC1/DC2 balance
- Day-of-week distribution
- Teaching vs DC accuracy
- Flags and violations
- Overall statistics

---

### validate_reports.py (340 lines)
**Purpose:** 14-check automated validation suite.

**Main entry point:**
```python
def run_checks(assignments, flags, provider_stats, config):
    """Returns list of check results"""
```

**Imports from assign_longcall:** 18 items including `stretch_has_weekday_and_weekend`.

---

### block_schedule_engine.py (1525 lines)
**Purpose:** Block schedule generator.

**Main entry point:**
```python
def run_engine(seed=None):
    """Returns scheduling results dict"""
```

**Key functions:**
| Function | Purpose |
|----------|---------|
| `fetch_sheet_csv()` | Fetch Google Sheet data |
| `load_providers()` | Load provider data (FTE, sites) |
| `load_availability()` | Load individual schedule JSONs |
| `build_periods()` | Create scheduling periods |
| `get_eligible_sites()` | Determine site eligibility |
| `can_place()` | Check all constraints for placement |
| `find_best_site_for()` | Score and pick best site |
| `place_provider()` | Execute placement and update state |
| `count_consec_at()` | Count consecutive weeks at a point |
| `get_active_week_nums()` | Get all assigned week numbers |

---

### generate_block_report.py (1893 lines)
**Purpose:** HTML report generator for block schedules.

**Key functions:**
- `generate_html(results)` → HTML string
- `render_mini_calendar(provider)` → SVG element
- `generate_rules_page()` → Rules reference HTML
- `generate_inputs_page()` → Config snapshot HTML
- `generate_index_page(variations)` → Comparison page HTML

---

### analyze_manual_lc.py (1176 lines)
**Purpose:** Analyze manual LC data against parsed schedules for ground truth validation.

**Key functions:**
- `parse_excel_lc(path)` → 3 blocks of manual LC assignments
- `parse_html_schedules(paths)` → Parsed schedule data
- `analyze_block(block_lc, schedule)` → Rule violation report
- `match_excel_name(name, index)` → Fuzzy name matching

**Name aliases:** `NAME_ALIASES = {"Kiki Li": "Li, Ka Yi"}` for nicknames that can't fuzzy match.

---

### recalculate_prior_actuals.py (472 lines)
**Purpose:** Compute worked weeks/weekends/nights for Blocks 1 & 2 from HTML schedules.

**Logic:** Weekday shifts ÷ 5 = weeks worked. Weekend shifts ÷ 2 = weekends. Night shifts ÷ 5 = nights.

**Exclusions:** APP roles, residents, behavioral medicine, admin roles, hospice.

---

### deploy_pages.sh (205 lines)
**Purpose:** Deploy HTML reports to GitHub Pages.

**Process:** Verify plaintext → copy to temp dir → create password gate → git worktree → commit + push → cleanup.

---

## Key Data Structures

### pstate (Long Call Engine)
Consolidated per-provider state dictionary:
```python
pstate["Provider, Name"] = {
    "stretches": [                  # List of work stretches
        {"start": date, "end": date, "days": [...], "category": "teaching"|"dc"}
    ],
    "weeks": [10, 11, 12, ...],     # ISO weeks worked
    "total_lc": 5,                  # Total LCs assigned
    "weekend_lc": 1,                # Weekend/holiday LCs
    "dc1_count": 2,                 # DC1 assignments
    "dc2_count": 2,                 # DC2 assignments
    "teaching_count": 1,            # Teaching assignments
    "days_of_week": [1, 3, 5, ...], # Mon=0, Sun=6
    "missed_weeks": [14],           # ISO weeks with no LC
    "doubles": [11],                # ISO weeks with 2 LCs
    "moonlighting_stretches": [...],# Moonlighting periods
    "is_excluded": False,           # Config-driven exclusion
}
```

### Assignments (LC Engine Output)
```python
assignments = {
    "2026-03-02": {
        "teaching": "Gordon, Sabrina",
        "dc1": "Logue, Ray",
        "dc2": "Vlad, Tudor"
    },
    "2026-03-03": {
        "teaching": "Hassinger, Gabrielle",
        "dc1": "Wang, Christopher",
        "dc2": "Li, Ryan"
    },
    # ...
}
```

### Flags (LC Engine Output)
```python
flags = [
    {"type": "DOUBLE_LONGCALL", "provider": "McMackin, Paul", "week": 13,
     "details": "2 LCs in stretch 03/23-03/29"},
    {"type": "CONSEC_NO_LC", "provider": "Yagnik, Hena", "weeks": [12, 13],
     "details": "2 consecutive weeks without LC"},
    {"type": "UNFILLED_SLOT", "date": "2026-04-11", "slot": "dc1",
     "details": "No eligible provider available"},
    # ...
]
```

### Periods (Block Schedule Engine)
```python
periods = [
    {"num": 1, "type": "week",    "start": "2026-03-02", "end": "2026-03-06",
     "demand": {"cooper": 26, "mullica": 11, ...}},
    {"num": 1, "type": "weekend", "start": "2026-03-07", "end": "2026-03-08",
     "demand": {"cooper": 19, "mullica": 10, ...}},
    {"num": 2, "type": "week",    "start": "2026-03-09", "end": "2026-03-13",
     "demand": {"cooper": 26, "mullica": 11, ...}},
    # ...
]
```

## External Dependencies

| Package | Used By | Purpose |
|---------|---------|---------|
| `networkx` | assign_longcall.py | Bipartite matching for weekend LC |
| `openpyxl` | analyze_manual_lc.py, recalculate_prior_actuals.py | Excel file reading/writing |
| `urllib` | block_schedule_engine.py | Google Sheet CSV fetching |
| Standard lib | All files | datetime, json, csv, os, math, hashlib, html.parser, collections |

## Configuration

All block-specific configuration lives in `config.json`:
```json
{
    "block_start": "2026-03-02",
    "block_end": "2026-06-28",
    "holidays": ["2026-01-01", "2026-05-25", ...],
    "excluded_providers": ["Last, First", ...],
    "teaching_services": ["HA", "HB", ...],
    "direct_care_services": ["H1", "H2", ...],
    "report_password": ""
}
```

The block schedule engine also reads from a Google Sheet for provider data, tags, and site definitions.
