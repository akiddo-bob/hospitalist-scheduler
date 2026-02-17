# Input Data Guide

This document explains how to generate, export, and prepare all input data for both the long call and block scheduling engines.

## Data Sources Overview

| Source | Used By | How to Get It |
|--------|---------|---------------|
| Amion HTML exports | Long call engine, Prior actuals calculator | Export from Amion web interface |
| Individual schedule JSONs | Block schedule engine | Extracted from Amion availability system |
| Schedule Book Excel | Block schedule engine (reference) | Maintained by scheduling coordinators |
| Long call Excel | Analysis tool (ground truth) | Manual LC tracker maintained by schedulers |
| config.json | Both engines | Created manually from config.sample.json |

---

## 1. Amion HTML Exports

### What they are
Monthly schedule exports from the Amion scheduling system. Each file is an HTML page showing a calendar grid with providers assigned to services for every day of the month.

### How to export
1. Navigate to the month view in Amion (https://amion.com)
2. Select the Hospital Medicine schedule
3. Use the browser's "Save As" → "Webpage, HTML Only" to save the page
4. Place the saved file in the `input/` directory

### Naming convention
Files are named by Amion's default format:
```
Hospital Medicine Schedule, M_D to M_D, YYYY.html
```
Example: `Hospital Medicine Schedule, 3_1 to 3_31, 2026.html`

The parser sorts files alphabetically and processes them in order. Any `.html` or `.txt` extension works.

### What the parser extracts
- **Date range and year** from the page title
- **Service columns** and their hours from the header row
- **Provider assignments** — who is on which service each day
- **Moonlighting flags** — detected by the `xpay_dull.gif` icon (at ~$1,500/shift)
- **Telehealth flags** — detected by telehealth indicators
- **Notes** attached to cells

### Coverage needed
- **For long call assignment:** Export the months that cover the current block (e.g., March–June 2026 for Block 3)
- **For prior actuals calculation:** Export all months from the start of the year through the end of the previous block (e.g., July 2025–February 2026 for computing Block 1+2 actuals)
- The parser handles overlapping date ranges gracefully by merging them

### Running the parser
```bash
.venv/bin/python3 parse_schedule.py
```

**Output:**
- `output/all_months_schedule.json` — Consolidated schedule (read by LC engine)
- `output/all_months_schedule.csv` — Flat CSV of all assignments
- `output/all_months_by_provider.csv` — Provider-centric view

The parser prints a summary showing total assignments, moonlighting shifts, and unique provider count.

---

## 2. Individual Schedule JSONs (Provider Availability)

### What they are
Per-provider, per-month JSON files containing each provider's stated availability for the upcoming block. These are the "requests" — which days each provider is available or unavailable to work.

### File format
```json
{
  "name": "Lastname, Firstname",
  "month": 4,
  "year": 2026,
  "days": [
    {"date": "2026-04-01", "status": "available"},
    {"date": "2026-04-02", "status": "unavailable"},
    {"date": "2026-04-03", "status": "blank"},
    ...
  ]
}
```

### Status values
| Status | Meaning |
|--------|---------|
| `available` | Provider indicated they can work this day |
| `unavailable` | Provider indicated they cannot work (vacation, CME, etc.) |
| `blank` | Provider didn't submit a request for this day, or isn't in the active pool |

### File naming
```
schedule_LastName_FirstName_MM_YYYY.json
```
Example: `schedule_Smith_John_03_2026.json`

There are 4 files per provider (one per month in the block), totaling ~1,000+ files for the full provider pool.

### Location
```
input/individualSchedules/
```

### How they're generated
These files are extracted from the Amion availability submission system. The extraction process converts Amion's availability data into the standardized JSON format.

### Used by
The **block schedule engine** (`block_schedule_engine.py`) reads these to determine which days each provider can be scheduled. The long call engine does not use these — it works from the published Amion schedule (the *result* of the block scheduling process).

---

## 3. Schedule Book Excel

### What it is
The master scheduling workbook maintained by the scheduling coordinators. It contains provider master data, scheduler workspace grids, and site-specific schedules.

### File
```
input/Schedule Book - Block 3 2025-2026.xlsx
```

### Key tabs

| Tab | Contents |
|-----|----------|
| **Master File** | Full roster: FTE, shift type (Days/Nights/Hybrid), scheduler codes, employment dates, annualized shift targets, special notes |
| **Lynne- Breakdown** | Monthly weekday shift counts per provider across the full year |
| **Audit** | Annual totals: weekdays, weekends, nights, swing shifts (Jul 2025 – Jun 2026) |
| **Numbers** | Daily staffing counts needed by location (weekday/weekend) — the demand model |
| **MM, PS, CD, ZF, AM, IR** | Scheduler workspace tabs — one per scheduler with day-by-day grids |
| **VEB** | Vineland/Elmer/Bridgeton schedule grid |
| **Cape** | Cape May schedule grid |
| **Mannington** | Date/provider list |
| **Holidays** | Provider holiday preferences (2 choices each) |
| **Per Diem** | Per diem/contract provider assignments |
| **FMLA** | Active FMLA leaves with dates |
| **Call out** | Sick day/call-out log |

### Key data extracted
- **Provider FTE and shift targets** (from Master File) — determines how many weeks/weekends each provider should work
- **Scheduler assignments** — which scheduler manages which provider
- **Site allocation percentages** — what fraction of each provider's time goes to each site
- **Prior block actuals** (from Audit/Breakdown tabs) — used to calculate remaining work for the current block
- **Holiday preferences** — which holidays each provider prefers to work

---

## 4. Long Call Excel (Ground Truth)

### What it is
The manually-maintained long call assignment tracker, covering all 3 blocks of the current year. Used for validation — comparing the automated engine's output against human decisions.

### File
```
input/Long call 2025-26.xlsx
```

### Structure
Three sheets, one per block:
- `Block 1 2025-26` (Jun 30 – Oct 26, 2025)
- `Block 2 2025-26` (Oct 27, 2025 – Mar 1, 2026)
- `Block 3 2025-26` (Mar 2 – Jun 28, 2026)

Each sheet has columns: Date, Teaching LC provider, DC1 provider, DC2 provider.

### Used by
`analyze_manual_lc.py` reads this file and cross-references it against the parsed Amion schedule to validate rules and identify patterns.

---

## 5. config.json

### What it is
The central configuration file for both engines. Contains block dates, service definitions, excluded providers, and report settings.

### How to create it
```bash
cp config.sample.json config.json
# Then edit config.json with your block-specific values
```

### Fields

```json
{
  "block_start": "2026-03-02",
  "block_end": "2026-06-28",
  "holidays": ["2026-01-01", "2026-05-25", ...],
  "excluded_providers": ["Last, First", ...],
  "teaching_services": ["HA", "HB", ...],
  "direct_care_services": ["H1", "H2", ...],
  "report_password": "optional-password"
}
```

### Block-specific configuration
Everything in config.json is **block-specific** — it changes every 4 months based on staffing changes, availability, and site director decisions. The structure stays the same; the values change.

### Service name matching
Service names must exactly match Amion column headers. If Amion shows "H8- Pav 6 & EXAU", that exact string goes in the config. Run the parser and check the JSON output to verify.

---

## 6. Google Sheet (Block Schedule Engine)

### What it is
A Google Sheet containing provider data, tags, and site information used by the block schedule engine. This is a structured, queryable version of the data that also lives in the Schedule Book Excel.

### Sheet ID
```
1dbHUkE-pLtQJK02ig2EbU2eny33N60GQUvk4muiXW5M
```

### Tabs
| Tab | Contents |
|-----|----------|
| **Providers** | Name, FTE, scheduler code, site allocation percentages, shift type |
| **Tags** | Provider tags (e.g., "teaching-only", "no-weekends", "site-director") |
| **Sites** | Site definitions, staffing requirements, service lists |

### How it's accessed
The block schedule engine fetches data directly from the Google Sheet via CSV export URLs (no API key needed — the sheet must be published/shared).

---

## Data Flow Diagram

```
                          ┌─────────────────┐
                          │   Amion System   │
                          └────────┬────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼               ▼
            Monthly HTML    Individual JSON    Published
            Exports         Availability       Schedule
                    │              │
                    ▼              │
            ┌──────────────┐      │
            │parse_schedule│      │
            └──────┬───────┘      │
                   ▼              ▼
            all_months_     ┌──────────────────┐
            schedule.json   │block_schedule_   │
                   │        │engine.py         │
                   │        └────────┬─────────┘
                   │                 │
                   ▼                 ▼
            ┌──────────────┐  ┌──────────────────┐
            │assign_       │  │generate_block_   │
            │longcall.py   │  │report.py         │
            └──────┬───────┘  └────────┬─────────┘
                   │                   │
                   ▼                   ▼
            ┌──────────────┐  Block schedule
            │generate_     │  HTML reports
            │report.py     │  (v1–v5)
            └──────┬───────┘
                   │
                   ▼
            LC report HTML
            (5 variations)
```

## Troubleshooting

### "No input files found"
Amion HTML exports must be in `input/` with `.html` or `.txt` extensions.

### Service names not matching
Run `parse_schedule.py` first and check the JSON output to see what service names the parser extracted. Compare against your config.

### A provider is missing from the report
Check whether they're on a source service. Providers only on non-source services (UM, SAH, etc.) won't appear. Also check `excluded_providers` in config.

### Files appear as binary/encrypted
You need the git-crypt key. Without it, files in `input/` and `output/` will appear as encrypted blobs. Alternatively, regenerate output files from the source data.

### Provider names look wrong
The parser strips trailing footnote numbers and normalizes whitespace, but some Amion formatting quirks can slip through. Check the parsed CSV/JSON output.
