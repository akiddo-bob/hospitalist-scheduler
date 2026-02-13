# Hospitalist Scheduler — User Guide

The Hospitalist Scheduler automates the scheduling tasks that go into building a hospitalist provider schedule for each 4-month block. It reads the existing daytime schedule from Amion and produces reports with assignments, fairness metrics, and flagged issues.

## What This Tool Does

The scheduling process has two major components, and this tool is being built to handle both:

### Long Call Assignment (Current)

The tool currently automates long call shift assignment — taking the published daytime schedule and determining who covers the long call slots each day. If you've done this by hand, you know the pain: balancing teaching vs. direct care slots, making sure nobody gets stuck with weekend long call twice, spreading the days of the week around, handling the weeks where there are too many providers and the weeks where there aren't enough — all while keeping it proportional to how many weeks each person actually works. This tool handles all of that.

### Block Scheduling (Planned)

The next phase of development will add block schedule generation — the upstream problem of assigning providers to services (HA, H1, H5, etc.) across the 4-month block in the first place. This is the part that determines who works which service on which weeks, before long call assignment even enters the picture. Block scheduling has its own set of constraints: service coverage requirements, provider preferences, leave requests, equitable distribution of desirable vs. less-desirable rotations, and continuity rules. This functionality is not yet built, but the app is structured to accommodate it.

The parsing layer (`parse_schedule.py`) and configuration system are shared across both components. Once block scheduling is added, the workflow will extend naturally: generate the block schedule first, then run long call assignment on top of it.

## Prerequisites

- **Python 3.8+** (tested on 3.11/3.12)
- **networkx** library (used for weekend long call assignment optimization)

Install dependencies:

```
pip install networkx
```

## Quick Start (Long Call Assignment)

The long call workflow has three steps:

1. **Export** the schedule from Amion as HTML files (one per month)
2. **Parse** those files into structured data
3. **Generate** the long call assignment report

```bash
# Step 1: Place Amion HTML exports in the input/ directory
mkdir -p input

# Step 2: Parse the schedule
python parse_schedule.py

# Step 3: Generate the report
python generate_report.py
```

The report will be written to `output/reports/{block_start}_{block_end}/`. Open the HTML file in a browser.

## Directory Structure

```
hospitalist-scheduler/
  input/                    # Amion HTML exports go here (not tracked in git)
  output/                   # All generated output (not tracked in git)
    all_months_schedule.json    # Consolidated parsed schedule
    all_months_schedule.csv     # Flat CSV of all assignments
    all_months_by_provider.csv  # Provider-centric CSV
    longcall_assignments.txt    # Text-based assignment table
    longcall_assignments.json   # Machine-readable assignments
    reports/
      {start}_{end}/
        longcall_report_*.html  # HTML reports (one per variation)
        index.html              # Listing page for all variations
  config.json               # Your configuration (not tracked — contains names)
  config.sample.json        # Template for config.json
  parse_schedule.py         # Shared: Parse Amion HTML into structured data
  assign_longcall.py        # Long call: Assignment engine
  generate_report.py        # Long call: HTML report generator
  longcall_rules.md         # Long call: Full rules reference
```

> **Note:** As block scheduling is added, new modules will appear here alongside the existing long call files. The parsing layer and config are shared infrastructure used by both.

## Configuration

Copy `config.sample.json` to `config.json` and fill it in:

```json
{
  "block_start": "2026-03-02",
  "block_end": "2026-06-28",
  "holidays": [
    "2026-01-01",
    "2026-05-25",
    "2026-07-04",
    "2026-09-07",
    "2026-11-26",
    "2026-12-25"
  ],
  "excluded_providers": [
    "Last, First"
  ],
  "teaching_services": [
    "HA", "HB", "HC", "HD", "HE", "HF", "HG", "HM (Family Medicine)"
  ],
  "direct_care_services": [
    "H1", "H2", "H3", "H4", "H5", "H6", "H7",
    "H8- Pav 6 & EXAU", "H9", "H10", "H11",
    "H12- Pav 8 & Pav 9", "H13- (Obs overflow)", "H14",
    "H15", "H16", "H17", "H18"
  ],
  "report_password": ""
}
```

### Config fields

| Field | Description |
|-------|-------------|
| `block_start` | First day of the scheduling block (YYYY-MM-DD). Should be a Monday. |
| `block_end` | Last day of the block (YYYY-MM-DD). Should be a Sunday. |
| `holidays` | All holidays recognized across the year. Include the full list even if some fall outside the block — the engine only acts on holidays within the block dates, but tracks the full set for cross-block fairness. |
| `excluded_providers` | Providers to completely skip for long call assignment. Names must match exactly as they appear in Amion (typically "Last, First" format). |
| `teaching_services` | Service names that count as teaching. Must match Amion column headers exactly. |
| `direct_care_services` | Service names that count as direct care. Must match Amion column headers exactly. |
| `report_password` | Optional. If set, the HTML report will require this password to view. The password is checked client-side via SHA-256 hash — it's a lightweight gate, not real security. Leave empty for no password. |

### Matching service names to Amion

Service names in the config must exactly match the column headers in your Amion HTML export. If Amion shows "H8- Pav 6 & EXAU" as the header, that's what goes in the config. The parser extracts these from the first line of each column header cell.

## Step 1: Exporting from Amion

Export each month's schedule from Amion as an HTML file:

1. Navigate to the month view in Amion
2. Use the browser's "Save As" or "Save Page As" function to save the page as HTML
3. Place the saved file in the `input/` directory

The parser accepts `.html` or `.txt` files. Name them however you like — they'll be sorted alphabetically and processed in order. Something like `march.html`, `april.html`, etc. works fine.

### What the parser extracts

- The date range and year from the page title
- All service columns and their hours from the header row
- Each day's provider assignments
- Moonlighting flags (detected by the `xpay_dull.gif` icon in Amion)
- Telehealth flags
- Notes attached to cells

The parsed data is written to both JSON and CSV formats in `output/` for inspection or further use.

## Step 2: Parsing

```bash
python parse_schedule.py
```

This reads all files from `input/`, parses each one, then merges them into a consolidated schedule. Output goes to `output/`:

- **Per-month files** (`march.json`, `march.csv`, etc.) — useful for spot-checking a single month
- **`all_months_schedule.json`** — the consolidated file that the assignment engine reads
- **`all_months_by_provider.csv`** — one row per provider per day, handy for reviewing who's working when

The parser prints a summary showing total assignments, moonlighting shifts, and unique provider count.

## Step 3: Generating Assignments and Reports

```bash
# Generate a single report
python generate_report.py

# Generate multiple variations (each with a different random seed)
python generate_report.py 5
```

Each run produces a timestamped HTML report in `output/reports/{block_start}_{block_end}/`. When generating multiple variations, an `index.html` landing page is also created that links to all reports for that block.

### Why multiple variations?

The assignment engine uses randomized tiebreakers to decide among equally-qualified providers. Running multiple variations lets you compare a few options and pick the one that looks best for your specific block. The random seed is displayed in each report filename and on the index page.

## How the Long Call Assignment Engine Works

This is the core of the tool's current functionality. The engine processes the schedule in several phases, working from global constraints down to local slot-filling.

### Source Services and Eligibility

Only providers working on **source services** (the teaching and direct care services listed in your config) are eligible for long call. All other services (UM, SAH, etc.) are invisible to the engine — a provider on a non-source service on a given day is treated as if they're not working that day.

Moonlighters are excluded from long call for their entire work stretch. The engine detects moonlighting via the extra-pay icon in the Amion export.

### Stretches and Weeks

The fundamental unit of scheduling is the **stretch** — a run of consecutive days a provider works on source services. A stretch might be a simple Mon–Fri week, or it could span a weekend (Mon–Sun, Sat–Fri), or even longer.

Stretches are classified as:

- **Real stretches** — contain at least one weekday (Mon–Fri). These are the primary assignment targets.
- **Standalone weekends** — only Sat/Sun with no adjacent weekdays on a source service. These are deprioritized. A provider working only a standalone weekend won't necessarily get a long call from it.

Long stretches that span multiple ISO weeks (Mon–Sun boundaries) are split into week-sized chunks. Each chunk gets one long call assignment need.

**The goal: one long call per week worked.** If a provider works 8 weeks in the block, they should end up with roughly 8 long calls.

### Long Call Shift Types

**Weekdays (Monday–Friday):**

| Slot | Hours | Who |
|------|-------|-----|
| Teaching Long Call | 5p–7p | Teaching service provider (HA–HG, HM) |
| Direct Care Long Call 1 | 7a–8a AM + 5p–7p PM | Direct care service provider (H1–H18) |
| Direct Care Long Call 2 | 5p–7p | Direct care service provider |

DC Long Call 1 is a split shift — the same provider covers both the morning and evening portions.

**Weekends and Holidays (Saturday, Sunday, observed holidays):**

| Slot | Hours | Who |
|------|-------|-----|
| Teaching Long Call | 5p–7p | Teaching service provider |
| Direct Care Long Call 2 | 5p–7p | Direct care service provider |

DC Long Call 1 does not exist on weekends/holidays — only 2 slots instead of 3.

### Phase 1.5: Weekend Assignment (Bipartite Matching)

Weekend long call is the trickiest constraint to balance by hand. The engine solves it first using graph-based bipartite matching to guarantee:

- **Maximum 1 weekend long call per provider** across the entire block
- Providers who work **more weekends** are preferred for weekend long call (protecting those with only 1–2 weekends from the burden)
- Providers with only 1 weekend in the block are excluded from weekend long call entirely when possible

The engine first tries to fill all weekend slots using only providers who work 2+ weekends. If that's not enough, it falls back to include everyone. After matching, it attempts to swap out low-weekend providers for higher-weekend alternatives where possible.

Holiday long calls count as weekend long calls for the purposes of this limit.

### Phase 2: Weekday Assignment (Priority-Based)

With weekends handled, the engine assigns weekday long calls week by week in chronological order. For each week, it ranks the assignment needs by priority:

1. **Consecutive-week gap** — Providers who went 1+ weeks without a long call get top priority. The engine enforces that no provider should go more than 1 consecutive week without a long call.
2. **Previously missed** — Providers who missed a long call in an earlier week due to surplus are prioritized.
3. **Standalone weekends deprioritized** — Real stretches get preference.
4. **Fewest long calls so far** — Fairness: give it to whoever has the least.
5. **Most total weeks** — Among ties, providers working more weeks can absorb a miss more easily, so providers with fewer weeks are protected.
6. **Randomized tiebreaker** — A hash-based tiebreaker rotates which provider wins ties across different weeks, preventing alphabetical or other systematic bias.

Teaching providers are processed before direct care providers, so teaching providers fill teaching slots first (preventing direct care providers from taking teaching slots).

### Slot Selection

When a provider is assigned a long call, the engine picks the best specific day and slot by:

- **Category match**: Teaching providers prefer teaching slots; direct care providers get DC slots. Teaching providers can overflow into DC slots if there are too many teaching providers that week.
- **Day-of-week variety**: Penalizes assigning the same day of the week repeatedly — spreads long calls across Mon, Tue, Wed, etc.
- **DC1 vs DC2 balance**: Alternates between DC Long Call 1 and DC Long Call 2 so no one is always stuck on the same one.

Direct care providers are never assigned to teaching slots.

### Phase 2.5: Minimum Guarantee

After the main assignment pass, the engine checks for providers who worked at least one weekday week but received zero long calls. These providers are guaranteed at least one long call by swapping them in — displacing a provider who has the most long calls and won't be left with zero.

### Phase 3: Filling Empty Slots (Doubles)

Some slots will be empty after Phase 2 — typically on weeks where there aren't enough providers. The engine fills these with **double long calls**: a second long call for a provider in the same stretch.

Rules for doubles:

- The provider's stretch must include both weekday and weekend days (so one long call can be weekday, one weekend)
- Providers who previously missed a long call get priority for the double (it's their makeup)
- A provider who already got a weekend long call in this stretch from Phase 1.5 is not eligible for a double in the same stretch
- Doubles are distributed fairly — the same provider shouldn't keep getting doubles
- All doubles are flagged in the report

### Phase 4: Missed Week Rebalancing

After everything is assigned, the engine checks for providers with 2+ weeks that have no long call. It swaps them into one of their missed weeks by displacing a provider who has zero missed weeks and the most total long calls. This ensures no provider is disproportionately shorted.

### Holidays

Holidays are treated like weekends:
- Only 2 long call slots (teaching + DC2) instead of 3
- Holiday long calls count as weekend long calls for the 1-per-block limit
- The engine tracks holidays across the full year list in config even if they fall outside the current block, to support cross-block fairness tracking

## Reading the Report

The HTML report includes these sections:

### Full Schedule
Day-by-day table of all long call assignments. Weekend/holiday rows are highlighted. Provider names link to their detail section.

### Schedule by Month
The same data broken into monthly tables for easier review.

### Provider Detail
The core review tool. Each provider gets a collapsible section showing their entire block schedule, color-coded:

- **Green rows** — long call assigned on this day
- **Grey rows** — moonlighting (excluded from long call) or non-source service
- **Red-tinted rows** — weeks where no long call was assigned
- **Blue rows** — weekend/holiday days
- **Bold week boundaries** — visual separation between ISO weeks

Badges mark long call assignments (LC), moonlighting (MOON), non-source services (NON-SOURCE), and weeks without a long call (NO LC).

### Provider Summary
Sortable/filterable table with each provider's key metrics:

| Column | Meaning |
|--------|---------|
| Weeks | Total non-moonlighting weeks with weekdays worked |
| Wknds | Total weekend days worked in the block |
| Standalone Wknds | Weekend-only appearances with no adjacent weekday work |
| Stretches | Number of distinct work stretches |
| Total LC | Total long calls assigned |
| Wknd LC | Weekend/holiday long calls (should be 0 or 1) |
| DC1 | Direct Care Long Call 1 assignments |
| DC2 | Direct Care Long Call 2 assignments |
| Teaching | Teaching Long Call assignments |
| Missed | Weeks with no long call assigned |
| Doubles | Weeks where they got 2 long calls |

The key fairness check: **Total LC should be roughly equal to Weeks for each provider.**

### Weekend Long Call Pivot
Two tables: one showing every weekend/holiday long call assignment, and one showing counts per provider. Any provider with 2+ weekend long calls is flagged.

### DC1 vs DC2 Balance
Shows whether each provider's DC1 and DC2 assignments are balanced. A difference of 0 or 1 is considered balanced.

### Day of Week Distribution
Grid showing how many times each provider was assigned long call on each day of the week (Mon–Sun). Days with 2+ assignments are flagged — the goal is to spread assignments across different days.

### Teaching vs Direct Care Accuracy
Shows how often teaching providers got teaching slots and DC providers got DC slots. "Teaching overflow into DC" is expected when there are more teaching providers than teaching slots in a week. "DC provider assigned Teaching" should be zero — that's a crossover that the engine prevents.

### Flags and Violations
All flagged issues, grouped by type:

| Flag | Meaning |
|------|---------|
| CONSEC_NO_LC | Provider went 2+ consecutive weeks without a long call |
| GUARANTEED_SWAP | Provider was swapped in under the minimum-guarantee rule |
| MISSED_SWAP | Provider was swapped in to fix a 2+ missed week situation |
| DOUBLE_LONGCALL | Provider has 2 long calls in a single stretch |
| UNFILLED_SLOT | A long call slot could not be filled |
| NO_LONGCALL | A provider's work week had no long call assigned |

Any rule violation that was necessary to complete the schedule is flagged here for manual review.

### Overall Statistics
Block-level summary: total days, slots filled, unfilled slots, doubles, missed weeks, provider counts, and average/min/max long calls per provider.

### Sorting and Filtering

Every table in the report supports:
- **Click a column header** to sort ascending/descending
- **Type in the filter box** under any column header to filter rows (substring match)

### Auto-Refresh

If you serve the report file locally (or just have it open from disk), it polls for file changes every 2 seconds. Re-run the generator and the open browser tab will automatically reload with the new data.

## Generating Multiple Variations

```bash
python generate_report.py 5
```

This generates 5 separate reports, each with a different random seed. The tiebreaker hash ensures that when providers are equally ranked, the "winner" rotates. All reports go into the same block folder, and the `index.html` page lists them all.

Use this to compare variations and pick the schedule that looks fairest for your specific block.

## Roadmap: Block Scheduling

The next major feature is automated block schedule generation. Today, the daytime schedule (who is on which service each week) is built manually and published in Amion, and this tool reads that schedule to assign long call on top of it. Block scheduling will automate the upstream step.

### What block scheduling needs to solve

- **Service coverage**: Every service (HA–HG, HM, H1–H18) needs a provider assigned every day it's staffed. No gaps.
- **Provider workload balancing**: Total weeks worked per block should be equitable across the group, accounting for part-time providers, leave, and other obligations.
- **Leave and requests**: Providers submit vacation, CME, and other time-off requests that must be honored. Some request specific services or specific weeks.
- **Service distribution fairness**: Some services are more desirable than others. Over time, every provider should rotate through a fair mix.
- **Continuity and stretch rules**: Providers shouldn't bounce between unrelated services mid-week. Stretch lengths and transitions need to follow group norms.
- **Weekend equity**: Weekend work should be distributed fairly, especially for services that require weekend coverage.

### How it fits together

Once block scheduling is built, the full workflow will be:

1. **Configure** the block — dates, providers, services, leave requests, constraints
2. **Generate** the block schedule (new) — assign providers to services across the block
3. **Parse** the resulting schedule (existing `parse_schedule.py`, possibly adapted)
4. **Generate** long call assignments (existing `assign_longcall.py` + `generate_report.py`)

The long call engine won't need to change — it already reads whatever schedule the parser produces. Block scheduling will feed into the same data pipeline.

## Troubleshooting

### "No input files found"
Make sure your Amion HTML exports are in the `input/` directory with `.html` or `.txt` extensions.

### Service names not matching
The service names in `config.json` must exactly match the column headers from Amion. Run `parse_schedule.py` first and check the JSON output to see what service names the parser extracted. Compare those against your config.

### A provider is missing from the report
Check whether they're on a source service. Providers only on non-source services (UM, SAH, etc.) won't appear. Also check the `excluded_providers` list in config.

### Too many unfilled slots or doubles
This usually means there aren't enough providers on source services in certain weeks. Check the schedule for light weeks. The engine will fill what it can and flag the rest.

### Provider names look wrong
The parser strips trailing footnote numbers and normalizes whitespace, but some Amion formatting quirks can slip through. Check the parsed CSV/JSON output to see how names were extracted.
