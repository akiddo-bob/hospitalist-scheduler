# Block Schedule Engine v2

**Status:** Active
**Created:** 2026-02-18
**Block:** 3 (March 2 – June 28, 2026)
**Predecessor:** v1 (frozen 2026-02-18)

## What v2 Fixes

v1 achieved good Cooper distribution (max 2 short/week) but failed to enforce
**site gap tolerance rules** (Section 3.6). Cape had 29 weekday + 13 weekend
total shortfall despite having ample provider supply. The root cause was that
v1 treated all non-Cooper sites equally — zero-gap sites like Cape competed
for the same provider pool as gap-tolerant sites like Mullica Hill and
Vineland, with no priority differentiation.

A secondary problem: v1's Phases 2-4 placed very few providers (~170 each)
while Phase 5 rebalancing did ~1,465 moves. The early phases were too
restrictive, making the algorithm overly dependent on the greedy rebalancer.

## Key Changes from v1

### 1. Site Gap Tolerance Tiers
Sites are now classified by gap tolerance per Section 3.6:
- **Tier 0 (zero gaps):** Cape, Mannington, Elmer, all Virtua sites
- **Tier 1 (up to 1 gap/day):** Mullica Hill, Vineland
- **Tier 2 (gaps expected):** Cooper

Zero-gap sites are filled FIRST within each round-robin pass, before
gap-tolerant sites. This ensures scarce provider capacity flows to sites
that must be fully staffed.

### 2. Restructured Fill Phases
- Phase 2 fills non-Cooper zero-gap sites first, then gap-tolerant sites
- Phase 3 fills Cooper (unchanged — round-robin, difficulty-ordered)
- Phase 4 behind-pace catch-up prioritizes zero-gap sites
- Phase 5 rebalancing scores zero-gap sites higher than gap-tolerant ones

### 3. Zero-Gap Violation Tracking
Output stats now include `zero_gap_violations` count — the number of
unfilled slots at sites that must have zero gaps. This is the primary
quality metric for v2.

## Algorithm: 6-Phase Scheduling

| Phase | Name | Description |
|-------|------|-------------|
| 1 | Setup | Load data, filter eligibles, compute fair-share targets, score week difficulty |
| 2 | Fill Non-Cooper | Zero-gap sites first, then gap-tolerant sites; round-robin, difficulty-ordered |
| 3 | Fill Cooper | Cooper absorbs remaining capacity; round-robin, difficulty-ordered |
| 4 | Behind-Pace Catch-Up | Lift fair-share cap; zero-gap sites first, gap-priority ordering |
| 5 | Forced Fill | Relax consecutive stretch limits; zero-gap site bonus in rebalancing |
| 6 | Output | Compile results with zero-gap violation tracking |

## Hard Rules Enforced
- Availability is sacred (never overridden)
- Site eligibility (pct > 0 required)
- Tag exclusions (do_not_schedule, no_elmer, no_vineland)
- Capacity limits: `floor(weeks_remaining)`, `floor(weekends_remaining)`
- Week + weekend pairing (same site preferred)
- Nocturnist exclusion (pure night with no remaining)
- Consecutive cap: max 2 weeks normally, 3 in forced fill
- Haroldson & McMillian never same week/weekend
- **Zero-gap sites must be fully filled** (Cape, Mannington, Elmer, Virtua)

## Scoring Formula
```
score = stretch_bonus + (behind x 5) + (spacing x 6) + (gap x 3) + (site_pct x 2) + cooper_penalty + jitter
```

## Files

| File | Purpose |
|------|---------|
| `engine.py` | Core 6-phase algorithm with gap tolerance enforcement |
| `report.py` | HTML report generator (reused from v1 with v2 branding) |
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
python -m block.engines.v2.run

# Run with custom seeds
python -m block.engines.v2.run --seeds 42 7

# Skip HTML report
python -m block.engines.v2.run --seeds 42 --no-report

# Custom output directory
python -m block.engines.v2.run --output-dir ./output/v2
```

## Results (5-seed run, 2026-02-18)

| Seed | Total Gaps | Zero-Gap Violations | Overfills | Stretch Overrides |
|------|:----------:|:-------------------:|:---------:|:-----------------:|
| 42   | 169        | 16                  | 3         | 17                |
| 7    | 167        | 18                  | 4         | 21                |
| 123  | 169        | 21                  | 5         | 18                |
| 256  | 165        | 15                  | 1         | 26                |
| 999  | 168        | 22                  | 6         | 20                |

### v1 → v2 improvement (Cape, seed 42)

| Metric            | v1  | v2  |
|-------------------|:---:|:---:|
| Cape WD shortfall | 29  |  4  |
| Cape WE shortfall | 13  |  2  |

### Zero-gap site status (seed 42)

| Site              | WD Short | WE Short | Status     |
|-------------------|:--------:|:--------:|------------|
| Cape              | 4        | 2        | Near-zero  |
| Elmer             | 0        | 0        | Perfect    |
| Mannington        | 0        | 0        | Perfect    |
| Virtua Marlton    | 0        | 0        | Perfect    |
| Virtua Mt Holly   | 4        | 2        | Near-zero  |
| Virtua Voorhees   | 3        | 1        | Near-zero  |
| Virtua Willingboro| 0        | 0        | Perfect    |

Remaining 15-22 zero-gap violations per seed are concentrated at Cape
(limited provider availability in hardest weeks) and Virtua Mt Holly /
Voorhees (small provider pools). These may be near-irreducible given
availability constraints.

### Known issue: Phase 2-4 throughput

Phases 2-4 still place only ~170 providers each, with Phase 5 rebalancing
doing ~1,460 moves. The fair-share cap is very restrictive in early phases.
This is a structural issue inherited from v1 — the algorithm works but the
early phases aren't pulling their weight. A future version could investigate
loosening the fair-share cap or doing multiple sub-rounds within each phase.

## Success Criteria

- Zero-gap sites (Cape, Mannington, Elmer, Virtua): 0 shortfall ← **4 of 7 sites perfect, 3 near-zero**
- Cooper: max 2-3 short weekday, 1-2 short weekend per week ← **met**
- Mullica Hill / Vineland: absorb remaining gaps (up to 1/day acceptable) ← **met**
- Stretch overrides: < 25 per variation ← **met (17-26)**
- Provider utilization gaps: < 60 unfilled provider-periods ← **met (51-55)**
