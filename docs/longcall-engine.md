# Long Call Assignment Engine

The long call engine (`assign_longcall.py`) reads the parsed daytime schedule and assigns providers to evening/morning long call shifts. It processes ~17 weeks of schedule data across a 4-month block, producing fair assignments that satisfy a set of strong and soft rules.

## Core Concepts

### Source Services and Eligibility
Only providers working on **source services** (the teaching and direct care services listed in config.json) are eligible for long call. All other services (UM, SAH, TAH, etc.) are invisible to the engine — a provider on a non-source service is treated as not working that day.

Moonlighters are excluded from long call for their **entire work stretch**, not just the moonlighting days. The engine detects moonlighting via the extra-pay icon (`xpay_dull.gif`) in the Amion export.

### Long Call Shift Types

**Weekdays (Monday–Friday) — 3 slots:**
| Slot | Hours | Eligible Providers |
|------|-------|-------------------|
| Teaching Long Call | 5p–7p | Teaching service (HA–HG, HM) |
| Direct Care Long Call 1 | 7a–8a AM + 5p–7p PM | Direct care service (H1–H18) |
| Direct Care Long Call 2 | 5p–7p | Direct care service (H1–H18) |

DC Long Call 1 is a split shift — the same provider covers both the morning and evening portions.

**Weekends and Holidays — 2 slots:**
| Slot | Hours | Eligible Providers |
|------|-------|-------------------|
| Teaching Long Call | 5p–7p | Teaching service |
| Direct Care Long Call 2 | 5p–7p | Direct care service |

DC Long Call 1 does not exist on weekends/holidays.

### Stretches and Weeks

The fundamental unit is the **stretch** — a run of consecutive days a provider works on source services. Non-source days are invisible and do not connect stretches.

**Stretch types:**
- **Real stretch** — Contains at least one weekday (Mon–Fri). This is the primary assignment target.
- **Standalone weekend** — Only Sat/Sun with no adjacent source-service weekdays. Deprioritized for LC assignment.

Long stretches spanning multiple ISO weeks (Mon–Sun boundaries) are split into week-sized chunks, each getting one LC assignment need. When a stretch starts on a weekend (e.g., Sat–Fri), the leading Sat–Sun stays with the following weekdays as one chunk — it is not split off as a standalone weekend.

**The goal: one long call per week worked.** If a provider works 8 weeks, they should get roughly 8 long calls.

## Algorithm Phases

### Phase 0: Data Preparation
- Load the consolidated schedule JSON
- Build daily slot inventory (which slots need filling, who's working)
- Identify each provider's stretches and classify them
- Detect moonlighting via extra-pay flags
- Build the `pstate` dictionary (consolidated provider state)

### Phase 1: Weekend Assignment (Bipartite Matching)
Weekend long call is the trickiest constraint. The engine uses graph-based bipartite matching (networkx) to solve it optimally.

**Guarantees:**
- Maximum 1 weekend LC per provider (soft target; hard ceiling is 2)
- Providers who work more weekends are preferred (protecting those with 1–2 weekends)
- Providers with only 1 weekend in the block are excluded when possible

**Process:**
1. Build a bipartite graph: weekend slots on one side, eligible providers on the other
2. First pass: only include providers with 2+ weekends worked
3. If not all slots can be filled, second pass includes everyone
4. After matching, attempt to swap out low-weekend providers for higher-weekend alternatives

Holiday long calls count as weekend long calls for this limit.

### Phase 2: Weekday Assignment (Sliding Window)
With weekends handled, weekday LCs are assigned week by week in chronological order using a sliding window (W1 weekdays + WE weekend + W2 next weekdays).

**Provider classification within each window:**
- **Group A** — Stretch ends on the weekend (overlaps W1 + WE)
- **Group B** — Stretch starts on the weekend (overlaps WE + W2)
- **Group C** — Working only the standalone weekend

**Priority ranking for each week:**
1. **Consecutive gap** — Provider went 1+ weeks without LC → top priority
2. **Previously missed** — Missed LC in an earlier week due to surplus
3. **Standalone weekends deprioritized** — Real stretches get preference
4. **Fewest LCs so far** — Fairness: give it to whoever has the least
5. **Most total weeks** — Among ties, more-weeks providers can absorb a miss
6. **Randomized tiebreaker** — Hash-based rotation prevents systematic bias

Teaching providers are processed before direct care to fill teaching slots first.

### Phase 2.5: Minimum Guarantee
After the main pass, check for providers who worked at least one weekday week but got zero long calls. These providers are guaranteed at least one LC by swapping them in — displacing a provider who has the most LCs and won't be left with zero.

### Phase 3: Filling Empty Slots (Doubles)
Some slots remain empty after Phase 2 — typically on weeks with insufficient provider supply. These are filled with **double long calls**: a second LC for a provider in the same stretch.

**Double rules:**
- The stretch must include both weekday and weekend days (one LC weekday, one weekend)
- Two weekday LCs in the same stretch are absolutely forbidden
- Providers who previously missed an LC get priority for the double (makeup)
- A provider who already got a weekend LC in this stretch from Phase 1 is not eligible
- Maximum 2 weekend LCs per provider (hard cap enforced here)
- Doubles are distributed fairly — same provider shouldn't keep getting extras
- All doubles are flagged in the report

**Phase 3.5:** 50 retry attempts with different random seeds to optimize the double assignment.

### Phase 4: Rebalancing
After everything is assigned, check for providers with 2+ weeks that have no LC. Swap them in by displacing someone with zero missed weeks and the most total LCs. This ensures no provider is disproportionately shorted.

## Slot Selection Logic

When a provider is assigned an LC, the engine picks the best specific day and slot:

1. **Category match** — Teaching providers prefer teaching slots; DC providers get DC slots. Teaching overflow into DC is normal when there are more teaching providers than teaching slots.
2. **Day-of-week variety** — Penalizes repeating the same weekday (e.g., always getting Tuesday LC)
3. **DC1 vs DC2 balance** — Alternates between DC1 and DC2 so no one is always stuck on the same type
4. **Gap from previous LC** — Prefers 3+ day gaps between LCs in the same stretch (minimum 2)

**Critical rule:** Direct care providers are never assigned to teaching slots on weekdays. On weekends, DC→teaching is allowed as a last resort when teaching supply is genuinely short.

## Key Data Structure: pstate

The `pstate` dictionary consolidates all per-provider state into a single structure, replacing the 8+ separate defaultdicts used in earlier versions:

```python
pstate[provider_name] = {
    "stretches": [...],           # List of work stretches
    "weeks": [...],               # ISO weeks worked
    "total_lc": 0,                # Total LCs assigned
    "weekend_lc": 0,              # Weekend/holiday LCs
    "dc1_count": 0,               # DC1 assignments
    "dc2_count": 0,               # DC2 assignments
    "teaching_count": 0,          # Teaching assignments
    "days_of_week": [],           # Day-of-week distribution
    "missed_weeks": [],           # Weeks with no LC
    "doubles": [],                # Stretches with 2 LCs
    # ... additional tracking fields
}
```

## Output

The engine returns a tuple: `(assignments, flags, provider_stats)`

- **assignments** — Dict mapping dates to slot assignments (teaching, dc1, dc2)
- **flags** — List of flagged issues (CONSEC_NO_LC, DOUBLE_LONGCALL, UNFILLED_SLOT, etc.)
- **provider_stats** — Per-provider summary statistics

Output is written to:
- `output/longcall_assignments.json` — Machine-readable
- `output/longcall_assignments.txt` — Human-readable table

## Running

```bash
# Standalone (produces JSON + TXT output)
.venv/bin/python3 assign_longcall.py

# Via report generator (produces HTML reports)
.venv/bin/python3 generate_report.py       # Single variation
.venv/bin/python3 generate_report.py 5     # 5 variations with different seeds
```

### Multiple Variations
The engine uses randomized tiebreakers. Running multiple variations (different random seeds) lets you compare options and pick the fairest schedule for your specific block. The seed is shown in each report filename.
