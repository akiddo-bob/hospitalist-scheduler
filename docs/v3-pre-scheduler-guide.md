# V3 Pre-Scheduler — User Guide

> **Purpose:** The pre-scheduler evaluates your provider data and flags issues
> **before** the scheduling engine runs. Fix problems here and you avoid
> garbage-in, garbage-out downstream.

**What it does:**
- Validates provider tags against the registry
- Verifies prior weeks/weekends worked against Amion HTML schedules
- Identifies providers that will be difficult to schedule
- Evaluates holiday obligations and Memorial Day readiness

**What it does NOT do:**
- Create or modify a schedule
- Change anything in Amion
- Make decisions for you — it surfaces problems and lets you decide

**Current scope:** Block 3 of cycle 25-26 (March 2 – June 28, 2026).
The tool will refuse to run for other blocks until generalization is implemented.

---

## 1. Prerequisites — What You Need

### Excel Workbook

The pre-scheduler reads from `hospitalist_scheduler.xlsx` with 4 required sheets:

| Sheet | What it contains |
|-------|-----------------|
| **Providers** | One row per provider: name, FTE, shift type, annual obligations, remaining weeks/weekends, site percentages, holiday preferences |
| **Provider Tags** | Per-provider rules and overrides (e.g., `do_not_schedule`, `swing_shift`, `pct_override`) |
| **Tag Definitions** | Registry of known tag names with engine status (ACTIVE, PLANNED, INFO) |
| **Sites** | Demand per site per day type (weekday/weekend/swing headcount) |

Place the workbook at: `block/engines/v3/input/hospitalist_scheduler.xlsx`

### Monthly Schedule Files (Amion HTML exports)

The tool parses raw HTML exports from Amion to count shifts. You need:

| Files | Purpose | Location |
|-------|---------|----------|
| June 2025 – February 2026 (9 files) | Blocks 1 & 2 prior actuals | `input/monthlySchedules/` |
| March – June 2026 (4 files) | Block 3 retrospective (optional) | `input/monthlySchedules/` |

File naming: `2025-06.html`, `2025-07.html`, ... `2026-06.html`

### Individual Availability JSONs

Per-provider per-month availability from Amion. Used to check Memorial Day
availability.

Location: `input/individualSchedules/`
Format: `schedule_FirstName_LastName_MM_YYYY.json`

These are fetched using `fetch_availability.py` (see project tools documentation).

---

## 2. Running the Tool

### Basic usage

```bash
.venv/bin/python3 -m block.engines.v3.pre_schedule
```

This runs all 4 tasks, writes review sheets back to the workbook, and produces
JSON output files.

### Common options

| Option | Default | What it does |
|--------|---------|-------------|
| `--tasks 1,2` | `1,2,3,4` | Run only specific tasks (comma-separated) |
| `--no-write-back` | off | Skip writing Excel sheets (JSON only) |
| `--excel PATH` | `v3/input/hospitalist_scheduler.xlsx` | Use a different workbook |
| `--schedules-dir PATH` | `input/monthlySchedules/` | Directory with Amion HTML files |
| `--availability-dir PATH` | `input/individualSchedules/` | Directory with availability JSONs |

### What happens when you run it

The tool prints a task-by-task summary to the console, then saves:

1. **Excel review sheets** — written back into your workbook (4 new tabs)
2. **`pre_schedule_output.json`** — combined machine-readable output
3. **`tag_config.json`** — tag configuration for the scheduling engine

If Block 3 schedule files exist in the schedules directory, retrospective
columns are automatically added to the Difficulty and Holiday sheets.

---

## 3. Understanding the Output

The tool writes 4 review sheets into your Excel workbook. Each sheet is
color-coded so you can scan for problems quickly.

### Output overview

| Sheet | Task | What it tells you |
|-------|------|-------------------|
| Tag Review | 1 | Are all provider tags valid and correctly spelled? |
| Prior Actuals Review | 2 | Do the weeks/weekends in Excel match what Amion shows? |
| Scheduling Difficulty | 3 | Which providers will be hardest to schedule? |
| Holiday Review | 4 | Who should work Memorial Day? Any holiday issues? |

---

## 4. Task 1 — Tag Review

**What it checks:** Every row in the Provider Tags sheet is validated against
the Tag Definitions registry. Provider names are matched to the Providers sheet
(with fuzzy matching for misspellings).

### Columns

| Column | What it shows |
|--------|-------------|
| Provider (Original) | Name as entered in Provider Tags |
| Provider (Resolved) | Matched name from Providers sheet |
| Name Status | OK or UNRESOLVED |
| Tag | The tag name |
| Tag Status | ACTIVE, PLANNED, INFO, or UNKNOWN |
| Rule | The rule text from Provider Tags |
| Engine Interpretation | How the engine will interpret this tag |
| Issues | Any problems detected |

### Color coding

| Color | Meaning |
|-------|---------|
| Green | ACTIVE tag — engine will enforce it |
| Yellow | PLANNED tag — recognized but not yet enforced |
| Blue | INFO tag — informational only |
| Red | UNKNOWN tag or issue detected |

### Common issues and what to do

| Issue | Meaning | Action |
|-------|---------|--------|
| Unresolved Name | Provider name in Tags doesn't match any provider in Providers sheet | Fix the spelling in the Provider Tags sheet |
| Unknown Tag | Tag name not in the Tag Definitions registry | Add it to Tag Definitions, or fix the typo |
| Duplicate Tag | Same tag appears twice for the same provider | Remove the duplicate row |
| Dead Tag | Tag on a `do_not_schedule` provider | Informational — you can ignore or clean up |

---

## 5. Task 2 — Prior Actuals Review

**What it checks:** Parses the Block 1 and Block 2 monthly HTML schedules from
Amion, counts actual weeks and weekends worked per provider, and compares
against the values in your Excel Providers sheet.

Service classification rules (which services count as day shifts vs excluded)
are documented in [block-scheduling-rules.md](block-scheduling-rules.md),
Section 1.3.

Providers tagged `do_not_schedule` are excluded from comparison since they
won't be scheduled.

### Columns

| Column | What it shows |
|--------|-------------|
| Provider | Provider name |
| Computed Weeks | Weeks worked, calculated from Amion HTML |
| Excel Weeks | `prior_weeks_worked` from your Excel |
| Weeks Diff | Absolute difference |
| Computed WE | Weekends worked, calculated from Amion HTML |
| Excel WE | `prior_weekends_worked` from your Excel |
| WE Diff | Absolute difference |
| Status | MATCH, DISCREPANCY, or MISSING |
| Detail | Explanation of the discrepancy |

### Color coding

| Color | Meaning |
|-------|---------|
| Green | MATCH — difference < 0.5 |
| Red | DISCREPANCY — difference >= 0.5 weeks or weekends |
| Yellow | MISSING — provider found in one source but not the other |

### How to resolve discrepancies

1. **Check service classification** — If a service was recently reclassified
   (e.g., Early Call moved to excluded), the computed value uses the updated
   rules but your Excel may not. The computed value is likely more accurate.

2. **Update Excel values** — If the computed value is correct, update
   `prior_weeks_worked` and/or `prior_weekends_worked` in the Providers sheet,
   then re-run.

3. **"Missing from Excel" providers** — These are people who appear in Amion
   schedules but aren't in your Providers sheet. Usually APPs, residents, or
   other non-physician staff. Typically safe to ignore.

4. **"Missing from schedule" providers** — These are in your Excel but weren't
   found in any Amion HTML file. Could indicate a new provider who started
   mid-cycle.

---

## 6. Task 3 — Scheduling Difficulty

**What it measures:** For each eligible provider, calculates **density** — the
ratio of remaining weeks to block length (17 weeks). Higher density means less
scheduling flexibility and greater risk of consecutive stretch violations.

### Risk levels

| Risk | Density | What it means |
|------|---------|-------------|
| **HIGH** | >= 65% | Stretch violations (>12 consecutive days) are nearly inevitable |
| **ELEVATED** | >= 47% | Extended stretches (8-12 days) are likely |
| **MODERATE** | >= 35% | Manageable with careful scheduling |
| **LOW** | < 35% | Flexible — plenty of room to spread assignments |

Swing-shift providers get bumped up one level at the MODERATE/ELEVATED boundary
because swing gaps force day-shift compression.

### Columns

| Column | What it shows |
|--------|-------------|
| Provider | Provider name |
| FTE | Full-time equivalent |
| Shift Type | Days, Nights, Hybrid |
| Ann Weeks | Annual week target |
| Remaining Wk | Weeks still owed this year |
| Density | Remaining weeks / 17 block weeks |
| Ann WE | Annual weekend target |
| Remaining WE | Weekends still owed |
| WE Density | Remaining weekends / 17 |
| Risk | HIGH, ELEVATED, MODERATE, or LOW |
| Eligible Sites | Number of sites they can work at |
| Capacity | tight, normal, or excess |
| Tags | Relevant tags (swing, PA, dpw, pct_override, etc.) |
| Notes | Detailed explanation of risk factors |

### Capacity status

| Status | Meaning |
|--------|---------|
| **tight** | Remaining weeks exceed fair-share by 20%+ — they'll be squeezed |
| **normal** | On track |
| **excess** | Remaining weeks well below fair-share — room to spare |

Fair share = annual weeks / 3 (one-third per block).

### Color coding

| Color | Meaning |
|-------|---------|
| Red row | HIGH risk |
| Yellow row | ELEVATED risk |
| Blue row | MODERATE risk |
| No fill | LOW risk |

### What to do with findings

- **HIGH risk providers** — Stretch violations are nearly unavoidable. You may
  need to adjust annual targets, approve extended stretches, or accept that
  some violations will occur.
- **Tight capacity** — The provider has more remaining work than expected for
  this point in the year. Verify their prior actuals are correct.
- **Single-site providers** — Less flexibility for balancing demand across
  sites. Be aware they create bottlenecks at their one eligible site.

### Retrospective columns (when Block 3 data exists)

If Block 3 schedule files are present, these columns appear to the right:

| Column | What it shows |
|--------|-------------|
| Actual B3 Wk | Weeks actually worked in Block 3 |
| Actual B3 WE | Weekends actually worked |
| Max Consec | Longest consecutive stretch (days) |
| Violation? | HARD (>12 days) or Extended (8-12) |
| Accuracy | How well the prediction matched reality |

Accuracy labels:
- **CORRECT** — Prediction matched the outcome
- **OVER** — Predicted higher risk than occurred (risk was managed successfully)
- **UNDER** — Predicted lower risk than occurred (a surprise)
- **MISS** — Predicted LOW but a hard violation occurred

Note: "OVER" predictions are expected and not errors — they represent risks
that good scheduling successfully avoided. Violations caused by post-schedule
swaps are beyond the pre-scheduler's control.

---

## 7. Task 4 — Holiday Review

**What it evaluates:** Determines each provider's Memorial Day obligation based
on their holiday work history in Blocks 1 and 2, FTE-based requirements, and
availability.

Holiday rules are documented in [block-scheduling-rules.md](block-scheduling-rules.md),
Section 4.

### Memorial Day priority tiers

| Tier | Meaning | Action |
|------|---------|--------|
| **MUST** | Owes holidays, no preference conflict | Should be scheduled for Memorial Day week |
| **SHOULD** | Owes holidays, but listed Memorial Day as a preference | Needs discussion — preference may need to be overridden |
| **MET** | Already fulfilled holiday requirement in B1+B2 | Don't assign unless needed for staffing |
| **UNAVAILABLE** | Marked unavailable May 25-29 | Cannot be assigned — verify availability is current |
| **EXEMPT** | No holiday requirement (very low FTE) | Skip |

### Columns

| Column | What it shows |
|--------|-------------|
| Provider | Provider name |
| FTE | Full-time equivalent |
| Site Dir | "Yes" if site director (affects holiday requirement) |
| Required | Holidays required per year (based on FTE) |
| Worked | Number of holidays worked so far this cycle |
| Owe | Holidays still owed |
| Holidays Worked | Which specific holidays they worked |
| Preferences | Their holiday preferences (holidays they want OFF) |
| Mem Day Pref? | "Yes" if Memorial Day is one of their preferences |
| Mem Day Avail? | "Yes" if available May 25-29, "No" if unavailable |
| Tier | MUST, SHOULD, MET, UNAVAILABLE, or EXEMPT |
| Issues | Any flagged problems |

### Issue flags

| Issue | Meaning |
|-------|---------|
| **Impossible** | Provider owes 2+ holidays but only 1 (Memorial Day) remains in Block 3 |
| **Both Xmas+NY** | Worked both Christmas and New Year's (guideline says one or the other) |
| **Neither Xmas nor NY** | Worked neither — needs attention if they still owe holidays |
| **Pref Violated** | Was scheduled on a holiday they listed as a preference in B1/B2 |
| **Overworked** | Worked more holidays than required |

### Color coding

| Color | Meaning |
|-------|---------|
| Red row | MUST tier |
| Yellow row | SHOULD tier |
| Blue row | UNAVAILABLE tier |
| No fill | MET or EXEMPT |
| Red font on "Owe" | Owes 2+ (impossible to fulfill in Block 3) |
| Red font on availability | Unavailable for Memorial Day week |

### What to do

1. **Review the MUST list** — These providers should work Memorial Day. Check
   that the list looks reasonable.
2. **Discuss SHOULD overrides** — These providers owe holidays but listed
   Memorial Day as a preference. Decide whether to override the preference.
3. **Verify UNAVAILABLE** — Availability data may be stale. Confirm with
   providers marked unavailable that their status is current.
4. **Address "Impossible"** — 35 providers owe 2+ holidays with only Memorial
   Day remaining. This is a structural constraint — they cannot fully satisfy
   their obligation in Block 3. Awareness is the action.

### Retrospective columns (when Block 3 data exists)

| Column | What it shows |
|--------|-------------|
| Actual Mem Day? | "Yes" if they actually worked Memorial Day week |
| Tier Outcome | How the tier prediction played out |

Outcome labels:
- **CORRECT** — MUST and worked, or UNAVAILABLE and didn't
- **NOT_SCHEDULED** — MUST tier but wasn't scheduled for Memorial Day
- **OVERRIDE** — SHOULD tier but was scheduled anyway (preference overridden)
- **HONORED** — SHOULD tier and preference was respected
- **EXTRA** — MET tier but worked Memorial Day anyway
- **ERROR** — UNAVAILABLE but was scheduled (availability data was stale)

---

## 8. Workflow

The pre-scheduler is designed to be run iteratively:

```
1. Prepare your Excel workbook
   └── Ensure Providers, Provider Tags, Tag Definitions, Sites sheets are current

2. Run the pre-scheduler
   └── .venv/bin/python3 -m block.engines.v3.pre_schedule

3. Review the 4 output sheets in Excel
   └── Look for red rows, discrepancies, issues

4. Fix data problems
   └── Correct misspelled names, update prior actuals, verify availability

5. Re-run the pre-scheduler
   └── Repeat until output is clean

6. Proceed to the scheduling engine
   └── Use the clean data and tag_config.json as engine input
```

### Running specific tasks

If you only need to recheck one area:

```bash
# Just tags
.venv/bin/python3 -m block.engines.v3.pre_schedule --tasks 1

# Tags + prior actuals
.venv/bin/python3 -m block.engines.v3.pre_schedule --tasks 1,2

# Everything except holiday (e.g., availability not fetched yet)
.venv/bin/python3 -m block.engines.v3.pre_schedule --tasks 1,2,3
```

### JSON-only mode

If you don't want the tool to modify your workbook:

```bash
.venv/bin/python3 -m block.engines.v3.pre_schedule --no-write-back
```

Output goes to `block/engines/v3/output/pre_schedule_output.json`.
