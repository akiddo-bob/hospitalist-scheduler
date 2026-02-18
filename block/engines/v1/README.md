# Block Schedule Engine v1

**Status:** Frozen — superseded by v2
**Created:** 2026-02-17
**Frozen:** 2026-02-18
**Block:** 3 (March 2 – June 28, 2026)

## What This Version Implements

### Algorithm: 6-Phase Scheduling

| Phase | Name | Description |
|-------|------|-------------|
| 1 | Setup | Load data, filter eligibles, compute fair-share targets |
| 2 | Fill Non-Cooper | Fill smaller sites first (Mullica Hill, Vineland, Virtua, Cape, Mannington, Elmer) with fair-share cap |
| 3 | Fill Cooper | Cooper absorbs remaining capacity, fair-share capped |
| 4 | Behind-Pace Catch-Up | Lift fair-share cap, behind-pace providers fill remaining gaps |
| 5 | Forced Fill | Relax consecutive stretch limits (max 3 weeks), rebalance to meet obligations |
| 6 | Output | Compile results, generate JSON + HTML reports |

### Hard Rules Enforced
- Availability is sacred (never overridden)
- Site eligibility (pct > 0 required)
- Tag exclusions (do_not_schedule, no_elmer, no_vineland)
- Capacity limits: `floor(weeks_remaining)`, `floor(weekends_remaining)`
- Week + weekend pairing (same site preferred)
- Nocturnist exclusion (pure night with no remaining)
- Consecutive cap: max 2 weeks normally, 3 in forced fill
- Haroldson & McMillian never same week/weekend

### Soft Rules (Relaxable in Phase 5)
- Fair-share distribution (ceil(annual/3) per block)
- Even spacing across block
- Site allocation match
- Cooper fills last
- 12-day stretch discouragement

### Scoring Formula
```
score = stretch_bonus + (behind × 5) + (spacing × 6) + (gap × 3) + (site_pct × 2) + jitter
```

Where:
- `spacing`: ideal-spacing score for even distribution (most weight)
- `stretch_bonus`: +100 same-site weekend, -50 cross-site, -120/-150 back-to-back
- `behind`: how far behind target at this site
- `gap`: raw period gap since last assignment
- `site_pct`: provider's allocation percentage at this site
- `jitter`: random ±2 for seed-based variation

## Files

| File | Purpose |
|------|---------|
| `engine.py` | Core 6-phase algorithm |
| `report.py` | HTML report generator (7-tab schedule + index/rules/inputs) |
| `run.py` | Runner script with CLI args |
| `__init__.py` | Package marker |
| `README.md` | This file |

## Shared Dependencies

| File | Location | Purpose |
|------|----------|---------|
| `loader.py` | `block/engines/shared/` | Data loading, site mapping, period construction |
| `name_match.py` | Project root | Provider name normalization and alias resolution |

## Usage

```bash
# Run with default 5 seeds
python -m block.engines.v1.run

# Run with custom seeds
python -m block.engines.v1.run --seeds 42 7

# Skip HTML report
python -m block.engines.v1.run --seeds 42 --no-report

# Custom output directory
python -m block.engines.v1.run --output-dir ./output/v1
```

## Output

Each run produces:
- `block_schedule_v1_seed{N}.json` — per-variation JSON results
- `index.html` — overview with variation cards
- `rules.html` — scheduling rules reference
- `inputs.html` — provider inputs summary
- `block_schedule_report_v1_{N}.html` — per-variation interactive report

### Report Tabs
1. **Calendar** — monthly grid with site columns, provider links, site filter buttons
2. **Providers** — collapsible cards with utilization bars, mini calendars, schedule tables
3. **Site Summary** — per-site fill overview + weekly detail
4. **Utilization** — comprehensive provider utilization table
5. **Flags** — over-assigned, under-utilized, stretch overrides
6. **Shortfalls** — coverage gaps by site, provider utilization gaps
7. **Open Shifts** — unfilled positions with reason breakdown

## Why v1 Was Frozen

v1 achieved good Cooper fill distribution (max 2 short per week, 96-99% fill
rate) through round-robin filling, Cooper preservation penalty, and
difficulty-scored week ordering. However, it has a critical flaw:

**Site gap tolerance is not enforced.** Per Section 3.6 of the scheduling
rules, Cape, Mannington, Elmer, and all Virtua sites must have zero gaps.
Only Mullica Hill and Vineland can tolerate up to 1 gap per day. Cooper gaps
are expected.

v1 treats all non-Cooper sites equally during filling. This results in:

| Site | Weekday Shortfall | Weekend Shortfall | Max Short/Week |
|------|:-:|:-:|:-:|
| **Cape** | **29** | **13** | **4** |
| Mullica Hill | 13 | 17 | 4 |
| Vineland | 17 | 18 | 3 |
| Virtua Mt Holly | 10 | 10 | 1 |
| Virtua Voorhees | 9 | 7 | 1 |
| Mannington | 0 | 0 | 0 |
| Elmer | 0 | 0 | 0 |

Cape has 31 eligible providers with 376 total capacity vs 221 demand — the
shortfalls are purely algorithmic, not a supply problem. The engine doesn't
prioritize zero-gap sites over gap-tolerant ones.

### Additional Known Limitations

1. Holiday scheduling not yet implemented (Memorial Day enforcement)
2. Swing shift capacity reservation not implemented
3. Named rules partially implemented (Haroldson/McMillian yes; Newell/Stone deferred)
4. Site allocation proportionality scoring is basic (percentage-based, not optimized)
5. Gap-leveling between surplus/shortfall weeks not implemented
6. Phases 2-4 place very few providers (~170 each); Phase 5 rebalancing does
   ~1,465 moves — the early phases are too restrictive

## Succeeded By v2

v2 addresses the zero-gap enforcement gap and restructures the fill phases
to be more effective in early passes. See `block/engines/v2/README.md`.
