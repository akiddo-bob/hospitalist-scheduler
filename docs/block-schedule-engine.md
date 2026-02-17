# Block Schedule Engine

The block schedule engine (`block_schedule_engine.py`) assigns providers to sites and services across a 4-month block. It reads provider data, availability, and staffing requirements to produce a fair schedule that fills all sites while respecting constraints.

## Core Concepts

### Sites and Services
Each site has its own set of services. Service names are site-specific:

| Site | Teaching Services | Non-Teaching Services | Special |
|------|------------------|----------------------|---------|
| Cooper Camden | HA–HG, HM | H1–H18 | UM, SAH, TAH, MAH, Med Consult |
| Mullica Hill | Med 1–4, FM | A–D, W | MAH, PA |
| Vineland | Med 1–4 | A–D, CDU, Z | MAH, UM |
| Elmer | (single provider) | | |
| Cape May | (6 clinical) | | UM, Swing |
| Mannington | (single provider) | | |
| Virtua | (6 across 4 sub-sites) | | |

### Scheduling Periods
The block is divided into scheduling periods:
- **Weeks** — Monday through Friday (the basic scheduling unit)
- **Weekends** — Saturday and Sunday
- A provider stays at **one location for the entire week+weekend**

### Fill Order
**Non-Cooper sites are filled first**, then Cooper absorbs the remaining capacity. Cooper is the largest site and can accommodate variable staffing.

## Algorithm Overview

### Fair-Share Two-Pass Approach

**Pass 1 — Cap at Fair Share:**
Each provider has an annual target for weeks and weekends (from Master File FTE). The engine divides this across 3 blocks, using prior actuals from Blocks 1 and 2 to compute remaining work for Block 3.

In Pass 1, providers are capped at `ceil(annual_target / 3)` to prevent over-assignment.

**Pass 2 — Lift Caps for Behind-Pace:**
Providers who are behind their annual pace (didn't get enough in Blocks 1–2) have their caps lifted to catch up.

### Consecutive Stretch Management
- **Preferred:** 7 consecutive days (1 week + adjacent weekend)
- **Maximum:** 3 consecutive weeks (enforced in forced fill — was uncapped before, creating 9-week runs)
- **Minimum gap:** 2–3 days off between stretches

The engine tracks consecutive weeks per provider and refuses to place a provider if it would create a run of 4+ consecutive weeks. Any override to the normal 2-week maximum is logged as a "stretch override."

### Availability and Constraints
- Providers mark days as available/unavailable in their individual schedule JSONs
- Unavailable days are hard constraints — the engine never schedules on them
- "Blank" days (no submission) are treated as available by default

## Detailed Process

### Step 1: Load Data
- Fetch provider data from Google Sheet (FTE, scheduler, site allocations)
- Load individual schedule JSONs for availability
- Build name matching index (fuzzy matching for variants)
- Compute prior block actuals from Schedule Book or HTML schedules

### Step 2: Build Periods
- Create scheduling periods (weeks and weekends) for the block
- Each period has a number, type (week/weekend), date range, and per-site demand

### Step 3: Site Assignment
For each period, in site priority order (non-Cooper first):

1. **Identify eligible providers** — Available, not over cap, not creating excessive consecutive stretch
2. **Score candidates** — Based on utilization (assigned/target ratio), site allocation match, stretch spacing
3. **Place best candidate** — Update all tracking (weeks used, weekends used, consecutive count)

### Step 4: Forced Fill (Gap Leveling)
After the main pass, some sites may still have unfilled slots. The engine relaxes constraints progressively:

1. **Relax consecutive cap** — Allow 3 consecutive weeks (normally max 2)
2. **Relax site percentages** — Allow providers to exceed their target site allocation
3. **Moonlighter fill** — Use moonlighting providers as a last resort

Each relaxation is tracked and reported.

### Step 5: Output
- Per-provider assignment list (which site, which periods)
- Per-site fill status (filled vs. required, with gap counts)
- Utilization metrics (how close each provider is to their target)
- Stretch analysis (consecutive runs, spacing)
- 5 variations with different random seeds

## Key Constraints

### Provider-Level
- **FTE-based caps** — Providers can't exceed their annual week/weekend targets (divided across blocks)
- **Availability** — Hard constraint from individual schedule JSONs
- **Site allocation** — Percentage targets for each site (flex ±5–10%)
- **Consecutive stretch limit** — Maximum 3 consecutive weeks (hard cap in forced fill)
- **Day/night transition** — Hybrid providers need 2–3 days between switching

### Site-Level
- **Minimum staffing** — Each site has weekday and weekend minimums
- **Service coverage** — Every service needs a provider assigned
- **Cooper fills last** — Absorbs remaining capacity after all other sites filled

### Special Rules
- **Mullica Hill teaching restrictions** — Specific providers never on Med 1–4
- **PA rotation** — Limited pool with per-provider annual limits
- **Family Medicine** — Dedicated provider pool at Mullica Hill
- **Site directors** — "Site Director" alone doesn't count as a working week; must have an assigned service
- **Katie Haroldson & Tyler McMillian** — Never scheduled on the same weeks/weekends
- **Glenn Newell** — Consults only, Mon–Thu only

## Output: HTML Reports

The block schedule report (`generate_block_report.py`) produces 5 variations plus navigation pages:

- `block_schedule_report_v1.html` through `v5.html` — Full reports
- `index.html` — Navigation page comparing variations
- `inputs.html` — Configuration snapshot
- `rules.html` — Rules reference

### Report Sections
- **Monthly calendars** — Color-coded provider-by-month grids
- **Provider detail** — Per-provider SVG mini-calendars, site assignments, stretch analysis
- **Site utilization** — Fill rates per site per period
- **Shortfalls** — Unfilled slots, stretch overrides, over-assigned providers
- **Moonlighter analysis** — Which moonlighters were used to fill gaps

## Running

```bash
# Generate block schedule reports (5 variations)
.venv/bin/python3 generate_block_report.py

# Output files in output/
```

## Key Metrics to Check

1. **Site gaps** — Total unfilled slots across all sites. Lower is better.
2. **Stretch overrides** — Number of 3-consecutive-week runs. Fewer is better.
3. **Over-assigned providers** — Anyone exceeding their fair-share target. Should be zero.
4. **Utilization balance** — How evenly are providers used relative to their targets?
