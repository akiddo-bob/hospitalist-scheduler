# Block 3 Validation — How It Works

## Overview

The Block 3 validation system checks the **manually-created** Amion schedule against all scheduling rules documented in [block-scheduling-rules.md](block-scheduling-rules.md). It parses the raw Amion HTML exports, classifies every assignment, and runs 12 checks that cover hard constraints, soft targets, and informational audits.

The output is a self-contained HTML report you can open in any browser. Every violation includes enough drill-down detail (dates, sites, services, swap notes) that you never need to go back to the spreadsheet to understand what happened.

**Important scope note:** Block scheduling assigns providers to **weeks** and **weekends** at **sites**. Night shifts, swing shifts, and service-level assignment within sites are all out of scope (Section 6.3 of the rules doc). The validation filters these out before running checks.

---

## Quick Start

```bash
# Regenerate the validation report
.venv/bin/python3 -m analysis.generate_block3_report

# Output: output/block3_validation_report.html (open in browser)
```

The report is also published to GitHub Pages after each deploy.

If you only want the text-based console output (no HTML):

```bash
.venv/bin/python3 -m analysis.validate_block3
```

---

## Input Data

The validation pulls from five sources. You don't need to configure anything — it reads them automatically.

| Source | Location | What It Contains |
|--------|----------|------------------|
| **Amion HTML exports** | `input/monthlySchedules/2026-03.html` through `2026-06.html` | Raw monthly schedule HTML from Amion. Every provider, every service, every day. |
| **Google Sheet — Providers tab** | Fetched live via Google Sheets API | Annual weeks/weekends targets, site distribution percentages (`pct_cooper`, `pct_virtua`, etc.), holiday preferences, FTE. |
| **Google Sheet — Provider Tags tab** | Fetched live via Google Sheets API | Flexible key-value tags: `do_not_schedule`, `swing_shift`, `no_elmer`, `no_vineland`, etc. |
| **Google Sheet — Sites tab** | Fetched live via Google Sheets API | Demand per site per day type (weekday/weekend/swing headcount). |
| **Provider availability JSONs** | `input/individualSchedules/*.json` | Per-provider per-month availability from Amion. One JSON per provider per month. |
| **Prior actuals** | `output/prior_actuals.json` | Weeks and weekends worked in Blocks 1 and 2, calculated by `recalculate_prior_actuals.py`. Used for the annual capacity check. |

---

## The 12 Checks

Each check maps to a specific section of `docs/block-scheduling-rules.md`. Checks are labeled **HARD** (must never be violated), **SOFT** (flexibility expected), or **INFO** (audit/catalog).

### Check 1 — Site Eligibility (HARD)
**Rules doc:** Section 2.2, 2.3

A provider can only work at sites where their distribution percentage is greater than zero. Tag restrictions (`no_elmer`, `no_vineland`) further limit eligibility.

**What it flags:** Provider assigned to a site they have 0% allocation for, or a tag-blocked site.

**Report detail:** Each violation shows the date, service, assigned site, and the provider's eligible site list. A collapsible "Full Block 3 schedule" shows every day they worked for context.

### Check 2 — Site Demand Heatmap (INFO)
**Rules doc:** Input 3, Section 3.6

Compares actual day-shift headcount at each site each week against the expected demand from the Sites tab.

**What it shows:** A color-coded heatmap grid — weeks across the top (1–17), sites down the left. Cell color indicates how far actual staffing deviates from demand:

| Color | Meaning |
|-------|---------|
| Red | Short by 2+ providers (seriously understaffed) |
| Yellow | Short by 1 provider |
| Green | On target |
| Light blue | Over by 1 provider |
| Medium blue | Over by 2+ providers |

Hover any cell to see which providers are working that site/week. The superscript number shows the exact deviation (e.g., `3⁻¹` means 3 providers, 1 short of demand).

**Note:** Cooper shortfalls are expected — Cooper is the "sink" site that absorbs gaps when smaller sites are prioritized.

### Check 3 — Provider Site Distribution (SOFT)
**Rules doc:** Section 3.4

Each provider has target percentages for how their time should be split across site groups. This check compares actual vs. target and flags deviations greater than 20%.

**Site grouping (critical concept):** Multiple physical sites roll up to a single percentage column:
- `pct_virtua` → Virtua Voorhees + Marlton + Willingboro + Mt Holly (combined)
- `pct_inspira_veb` → Vineland + Elmer (combined)
- `pct_cooper`, `pct_inspira_mhw` (Mullica Hill), `pct_mannington`, `pct_cape` → one site each

A provider with `pct_virtua = 1.0` who works 12 days at Willingboro and 37 at Voorhees is at 100% Virtua — that's on target.

**Report detail:** For each flagged provider, shows:
1. **Full allocation table** — all 6 site groups with actual days, actual %, target %, and the difference. This lets you see the whole picture, not just the violation.
2. **Flagged deviations** — the specific groups over the 20% threshold.
3. **Full Block 3 schedule** — every date, site, and service.

### Check 4 — Annual Capacity (HARD)
**Rules doc:** Section 2.4

Verifies that total weeks and weekends across all three blocks do not exceed the provider's annual allocation:

```
prior_weeks (B1 + B2) + block3_weeks <= annual_weeks
prior_weekends (B1 + B2) + block3_weekends <= annual_weekends
```

**What it does NOT check:** It does not validate against `weeks_remaining` — that's for the engine to use when building a schedule. This check validates the final result against the annual cap.

**Report detail:** Shows the full math: annual cap, B1+B2 prior, Block 3, total, and the overage. Lists every Block 3 week number. Includes the full date-by-date schedule.

### Check 5 — Consecutive Stretches (SOFT / HARD)
**Rules doc:** Section 3.2

Providers should not work too many consecutive days:
- **Normal:** up to 7 consecutive days (Mon–Sun or Mon–Fri)
- **Acceptable:** 8–12 consecutive days (week + weekend + week overlap)
- **HARD violation:** more than 12 consecutive days
- **21-day window:** max 17 days worked in any 21-day sliding window

**Report detail:** Each streak shows the exact start and end dates, length, and severity. Organized by provider with the worst violations first.

### Check 6 — Availability (HARD)
**Rules doc:** Section 2.1

Availability is sacred. If a provider marks a day as unavailable in Amion, they must never be scheduled that day.

**What to watch for:** Many apparent violations are explained by **swap notes** in Amion. A provider may show as unavailable on a date, but a swap note says "covering for Smith" — meaning the assignment was intentional. The report shows swap note badges (blue pills) next to each violation so you can see at a glance which ones are real vs. swap-related.

**Report detail:** Each violation shows the date, service, site, and any swap note. A collapsible "Full Block 3 schedule" provides full context.

### Check 7 — Conflict Pairs (HARD)
**Rules doc:** Section 5.2

Specific provider pairs must never be scheduled during the same week or weekend at any site. Currently: Haroldson & McMillian.

**Report detail:** Shows each week where both providers overlap, with their respective sites.

### Check 8 — Week/Weekend Same-Site Pairing (HARD)
**Rules doc:** Section 2.5

When a provider works both the weekday and weekend of the same week, they should be at the same site. Cross-site week/weekend splits should only happen as a last resort.

**Report detail:** Each mismatch shows the provider, week number, weekday sites, and weekend sites.

### Check 9 — Single Site Per Week (STRUCTURAL)
**Rules doc:** Section 1.2

A provider stays at one site for the entire Mon–Fri period. This checks whether any provider was assigned to multiple sites within the same weekday span.

**Report detail:** Lists every provider/week where multiple weekday sites were observed.

### Check 10 — Holiday Rules (HARD / INFO)
**Rules doc:** Section 4

Memorial Day (May 25, 2026) is the only holiday in Block 3.
- Lists all providers working Memorial Day week (May 25–29)
- Flags providers who listed Memorial Day as a holiday preference but are scheduled to work it

**Report detail:** For preference violations, shows the provider's Memorial Day week assignments.

### Check 11 — Swing Capacity Reservation (INFO)
**Rules doc:** Input 3

Swing-tagged providers need weeks reserved for swing shifts. This shows how many day-shift weeks they were assigned vs. their capacity, so you can verify enough room was left.

### Check 12 — Swap Notes Catalog (INFO)

Not a rule violation — just a catalog of all swap/trade/covering notes found in the Amion HTML. Useful for understanding why certain assignments look unusual.

---

## Reading the Report

### Layout

The report opens with:
1. **Overview cards** — total providers, assignments, dates, sites at a glance
2. **Summary table** — all 12 checks in one table with pass/fail/warning counts

Then each check has its own section with a dark header bar that sticks to the top as you scroll, so you always know which check you're in.

### Drill-downs

Most checks use collapsible `<details>` sections. Click a provider's name to expand and see:
- The specific violation detail
- Their full Block 3 schedule (nested collapsible)

### Badges

Color-coded pills appear throughout:
- 🔴 **Red** — hard violation or significantly over
- 🟡 **Yellow** — warning, soft violation, or notable
- 🟢 **Green** — passing or on target
- 🔵 **Blue** — informational (swap notes, etc.)

### Controls

- **Expand All / Collapse All** buttons at the top toggle every collapsible section
- **Hover** heatmap cells (Check 2) to see provider names in tooltips

### Mobile

The report is mobile-responsive. On phones:
- Layout tightens (smaller fonts, compact padding)
- Heatmap cells shrink; a "→ scroll →" hint appears for horizontal scrolling
- No nested scroll traps — everything flows in the page's natural scroll

---

## The Three Analysis Tools

### `validate_block3.py` — Validation Engine

The core check functions. Can be run standalone for console output or imported by the report generator.

```bash
.venv/bin/python3 -m analysis.validate_block3
```

**What it does:**
1. Loads provider data, tags, sites demand, and availability from the Google Sheet and local JSONs
2. Parses Block 3 HTML files (March–June 2026)
3. Classifies each assignment (day/night/swing/exclude) and maps services to sites
4. Filters to day shifts only
5. Runs all 12 checks
6. Prints results to console

### `generate_block3_report.py` — HTML Report Generator

Imports every check function from `validate_block3.py` and renders results as a self-contained HTML file.

```bash
.venv/bin/python3 -m analysis.generate_block3_report
# Output: output/block3_validation_report.html
```

**What it adds beyond the console output:**
- Collapsible drill-downs with full provider schedules
- Color-coded heatmap for site demand (Check 2)
- Full allocation tables for distribution (Check 3)
- Badge system for quick visual scanning
- Mobile-responsive layout
- Expand All / Collapse All controls

### `compare_block3_actuals.py` — Actuals Comparison Spreadsheet

A separate tool that compares Block 3 actuals against annual targets and prior block data.

```bash
.venv/bin/python3 -m analysis.compare_block3_actuals
# Output: output/block3_actuals.xlsx
```

Produces an Excel file with columns for annual targets, Block 1+2 actuals, and Block 3 actuals — designed to be pasted into the Google Sheet for side-by-side comparison.

---

## Key Concepts

### Service Classification

Every Amion service is classified into one of four types (using `classify_service()` from `recalculate_prior_actuals.py`):

| Type | Description | Counted in validation? |
|------|-------------|----------------------|
| `day` | Standard day shifts (H1–H18, HA–HG, site services) | Yes |
| `night` | Night shifts (NAH, Night Direct Care, etc.) | No — out of scope |
| `swing` | Swing shifts (SAH, etc.) | No — out of scope |
| `exclude` | Non-clinical (UM, consults, admin, moonlighting) | No |

### Service-to-Site Mapping

Amion services encode the hospital site in their name prefix. The `service_to_site()` function maps every service to one of these sites:

- **Cooper** — default for services without a site prefix (H1–H18, HA–HG, etc.)
- **Virtua Voorhees / Marlton / Willingboro / Mt Holly** — "Virtua" prefix
- **Vineland / Elmer** — "Vineland" or "Elmer" prefix
- **Mullica Hill** — "MH+" prefix or "Mullica Hill" in name
- **Mannington** — "Mannington" in name
- **Cape** — "Cape" prefix

### Week Numbering

Block 3 runs from March 2 to June 28, 2026 (17 weeks). Week 1 starts on March 2. Each week runs Monday–Sunday. Weekend days are Saturday and Sunday.

### Name Matching

Provider names appear in different formats across Amion HTML, the Google Sheet, and availability JSONs. The `name_match.py` module handles canonicalization and fuzzy matching to link them all together.

---

## Common Scenarios When Reviewing

### "Why does this provider show an availability violation?"

Check for a **swap note badge** (blue pill) next to the violation. Most availability violations are explained by swaps recorded in Amion — the provider agreed to cover someone else's shift on a day they had originally marked unavailable.

### "This provider looks over on capacity, but I think they're fine"

Check which type is flagged — **weeks** or **weekends**. A provider can be over on weekends but fine on weeks (or vice versa). Also check the prior actuals (B1+B2 column) — if those numbers seem wrong, re-run `recalculate_prior_actuals.py` with updated Amion data.

### "The site distribution looks wrong for this provider"

Expand their drill-down in Check 3 and look at the **full allocation table**. It shows all 6 site groups with actual vs. target. Remember that Virtua sub-sites are combined — a provider at Willingboro and Voorhees both count toward `pct_virtua`.

### "Cooper shows red in the heatmap every week"

That's expected. Cooper is the largest site and acts as the "sink" — when smaller sites are fully staffed, there may not be enough providers left to fully cover Cooper's demand. Focus on the smaller sites (Virtua, Inspira, Cape, Mannington) where shortfalls are more actionable.

### "A provider is flagged for working at two sites in the same week"

Check 9 catches this. It could be a real scheduling issue, or it could be a swap that moved them mid-week. Look at the specific dates and services to understand whether it's intentional.

---

## Deploying Updated Reports

After regenerating the HTML report, deploy to GitHub Pages:

```bash
# Regenerate
.venv/bin/python3 -m analysis.generate_block3_report

# Deploy (if deploy_pages.sh handles the validation report)
./deploy_pages.sh

# Or manually: copy to gh-pages branch and push
```

The report is published at:
`https://akiddo-bob.github.io/hospitalist-scheduler/block3_validation_report.html`
