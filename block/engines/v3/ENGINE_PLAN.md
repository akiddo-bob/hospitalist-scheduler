# V3 Engine Plan

## Why V3

V1 and V2 both used greedy, phase-based heuristics — fill the hardest sites
first, cap at fair-share, then do a catch-up pass, then force-fill remaining
gaps. Both struggle with the same fundamental problem: **demand exceeds supply**.
When that happens, a greedy algorithm makes locally-optimal choices that can
paint itself into corners, leaving gaps that no later phase can fix.

The manual scheduler succeeds where the engine doesn't because a human thinks
globally — "if I put Dr. X here instead of there, that frees up Dr. Y for this
other site that nobody else can cover." V1/V2 can't do this. They assign
forward and never look back.

V3 takes a different approach.

---

## What's Different This Time

### 1. The pre-scheduler does the data quality work upfront

V1/V2 loaded raw data and hoped for the best. V3 has a 4-task pre-scheduler
that validates tags, verifies prior actuals, flags impossible providers, and
evaluates holiday obligations. By the time the engine runs, the user has
reviewed and corrected the input. This eliminates a class of problems that
V1/V2 silently absorbed.

The user runs the pre-scheduler, reviews the Excel output, fixes data issues,
and explicitly approves the inputs before scheduling begins. The engine can
trust its inputs.

### 2. Accept that perfection is impossible

V1/V2 tried to satisfy every rule. When they couldn't, they force-filled with
relaxed constraints and hoped the result was usable. The output was a complete
schedule that was hard to fix because changing one assignment cascaded.

V3 accepts from the start that the automated system will produce an 85-90%
solution. The remaining 10-15% requires human judgment — the same judgment
that manual schedulers have always applied. The engine's job is to make that
final 10% achievable by ensuring gaps and available providers are aligned.

### 3. Two-stage output: draft schedule + gap report

Instead of one finished schedule, V3 produces:

**Stage 1 — The draft schedule.** Assignments that respect all hard constraints
and most soft constraints. Every assignment in the draft is solid — the user
doesn't need to second-guess any of them.

**Stage 2 — The gap report.** Unfilled slots, paired with the providers who
*could* fill them (eligible, available, have remaining capacity). The manual
scheduler uses this to make the final trade-off decisions: who gets the
extended stretch, which preference gets overridden, which site tolerates a gap
this week.

This is fundamentally different from V1/V2. They tried to fill everything and
produced a schedule where some assignments were forced. V3 only makes
assignments it's confident about and gives the human a clear menu for the rest.

### 4. Constraint propagation, not greedy scoring

V1/V2 scored candidates with a composite formula (spacing×6 + behind×5 + gap×3
+ site_pct×2 + ...) and picked the highest score. The weights were hand-tuned
and fragile.

V3 uses constraint propagation before scoring. Before assigning anyone to a
slot, the engine asks: "if I assign provider X here, does that make any
future slot impossible to fill?" If yes, X is deprioritized or blocked for
this slot even if they score highest.

Concretely:
- If Elmer has only 3 eligible providers for a given week, and one of them is
  the only provider who can cover Cape that same week, don't put them at Elmer.
  Cape has zero gap tolerance; Elmer's provider pool is larger.
- If assigning Dr. Y to week 5 at Mullica Hill means she'd need 3 consecutive
  weeks later to meet her remaining target (creating a guaranteed stretch
  violation), defer her assignment to a better week.

This is the "look-ahead" that greedy scoring can't do.

### 5. Site-first, then provider-fill

V1/V2 iterated by site, then by week, then scored providers. This meant the
algorithm was always asking "who should fill this slot?" — a local question.

V3 flips the perspective for the critical sites. For zero-gap-tolerance sites
(Cape, Mannington, Elmer, Virtua), the engine first ensures every week has at
least one eligible, available provider reserved. These reservations happen
before any general assignment. Think of it as "seat reservation" — small sites
get guaranteed coverage before the general seating begins.

After reservations, the general assignment phase runs for all remaining slots.
This prevents the V1/V2 failure mode where a capable provider gets consumed by
a larger site early, leaving a small site with no candidates later.

### 6. Iterative refinement with swap evaluation

After the initial assignment, V3 runs a swap evaluator: for every unfilled
slot, check if swapping two assigned providers would fill the gap without
creating a new one. This is the automated version of what manual schedulers do
naturally — "if I move her from Cooper to Cape this week, I can fill Cooper
with someone else who can't do Cape."

Swaps are evaluated with a net-benefit score. A swap is only executed if it
reduces total gaps without creating hard constraint violations.

---

## What 85-90% Looks Like

Based on the Block 3 validation of the manual schedule, here's what good
looks like in practice:

### Must achieve (100%)

These are the non-negotiable hard constraints. The manual schedule achieves
all of these, and the engine must too.

| Constraint | Target | Manual Schedule |
|-----------|--------|----------------|
| Site eligibility | 0 violations | 0 violations |
| Availability respect | 0 violations | 0 violations |
| Conflict pairs (Haroldson/McMillian) | 0 violations | 0 violations |
| No >12 consecutive days | 0 violations | 0 violations |
| Week/weekend same-site pairing | >95% | ~98% |
| Single site per week | >95% | ~97% |

### Should achieve (85-90%)

These are the soft constraints where some deviation is expected and acceptable.

| Constraint | Target | Manual Schedule |
|-----------|--------|----------------|
| Weekday demand coverage | 85-90% | ~90% |
| Weekend demand coverage | 75-85% | ~80% |
| Zero-gap site coverage | >90% | ~85% |
| Provider capacity respect | >85% | ~68% (47 providers over) |
| Site distribution targets | Within ±25% | 21 providers deviated >20% |
| Holiday obligations | MUST tier assigned | 68% of MUST worked Memorial Day |

### Acceptable trade-offs

The engine explicitly acknowledges these will happen:

- **Extended stretches (8-12 days)**: Some providers will work 8-12 consecutive
  days. This is acceptable and expected. The engine tracks them but doesn't
  prevent them.
- **Distribution deviations up to ±30%**: A provider with 50% Cooper / 50%
  Vineland might end up 70/30 in a given block. That's fine.
- **Cooper as pressure valve**: Cooper will absorb the most gaps because it has
  the most eligible providers and is the most gap-tolerant site.
- **Some weekends understaffed**: Weekend provider pools are smaller. The
  engine will do its best but some weekends will be short.

### What the engine will NOT do

- Exceed annual capacity without flagging it. If a provider's remaining weeks
  can't cover their fair share, the engine assigns what it can and reports the
  gap. It does not silently over-schedule.
- Fill a slot that creates a guaranteed >12-day stretch. The draft stays clean.
- Assign to unavailable dates. Ever.
- Hide problems. Every unfilled slot, every trade-off, every near-miss is
  reported.

---

## What the Manual Scheduler Does With the Output

The engine produces a draft schedule and a gap report. The manual scheduler's
job is to close the remaining 10-15% of gaps. Here's what that work looks like:

### 1. Review the gap report

The gap report shows each unfilled slot with:
- Site, week number, day type (weekday/weekend)
- List of providers who *could* fill it (eligible, available, have capacity)
- What constraint each candidate would need to bend (extended stretch, over
  fair-share, distribution deviation, preference override)

### 2. Make trade-off decisions

For each gap, the scheduler picks a resolution:
- **Assign a provider with an extended stretch** — acceptable if the stretch is
  8-10 days and the provider is willing
- **Override a holiday preference** — move a SHOULD provider to MUST for
  Memorial Day
- **Accept a site gap** — Mullica Hill or Vineland can tolerate 1 gap per week.
  If no good candidate exists, leave it empty and plan for moonlighter coverage
- **Over-assign capacity** — assign a provider beyond their remaining weeks.
  This is a business decision, not a scheduling error. The engine flags it; the
  human approves it.

### 3. Validate the final schedule

After manual adjustments, the scheduler runs the Block 3 validation suite
(`analysis/validate_block3.py`) to confirm no hard constraints were violated.
The validation report shows the final state across all 12 checks.

---

## Engine Architecture (High Level)

```
pre_schedule.py (Tasks 1-4)
    ↓
User reviews Excel, fixes data issues
    ↓
engine.py
    │
    ├── Phase 0: Load & Validate
    │     Read pre-scheduler output (tag_config, prior_actuals, difficulty, holiday)
    │     Build provider pool, site demand, availability, periods
    │     Flag impossible providers (density > 90%)
    │
    ├── Phase 1: Reserve Critical Sites
    │     For each zero-gap site + week: ensure ≥1 eligible provider is reserved
    │     Propagate constraints (reservation reduces available pool for other slots)
    │
    ├── Phase 2: General Assignment
    │     Fill remaining slots using constrained scoring
    │     Hard constraints enforced at assignment time
    │     Look-ahead: skip assignments that would make future slots impossible
    │     Fair-share capped (assignable ≤ remaining weeks)
    │
    ├── Phase 3: Behind-Pace Fill
    │     Lift fair-share cap for behind-pace providers
    │     Fill remaining gaps at all sites
    │     Zero-gap sites prioritized
    │
    ├── Phase 4: Swap Evaluation
    │     For each unfilled slot, evaluate swap candidates
    │     Execute net-positive swaps only
    │     Track all attempted swaps for reporting
    │
    └── Phase 5: Output
          Draft schedule (all confident assignments)
          Gap report (unfilled slots + candidate lists)
          Provider summary (assigned vs target, distribution, stretch analysis)
          Site summary (coverage %, gaps by week)
          Holiday assignments (Memorial Day placements)
```

### Key Design Decisions

**Hard constraints are never relaxed.** Unlike V1/V2's "forced fill" phase that
relaxed consecutive week caps, V3 never bends hard constraints. If a slot can't
be filled without a hard violation, it stays empty and goes to the gap report.

**Availability is sacred.** Same as V1/V2 — never assign an unavailable date.

**Capacity is respected by default, overridable by the user.** The engine won't
over-schedule a provider. If demand requires it, the gap report surfaces this
and the human decides.

**Holiday assignment is integrated.** Memorial Day week gets special handling.
MUST-tier providers from the pre-scheduler are slotted first for that week.
SHOULD-tier providers are listed as candidates with a "preference override"
flag.

**Seed-based variation preserved.** Like V1/V2, multiple seeds produce different
schedules. The user can compare and pick the best starting point.

---

## Pre-Scheduler → Engine Interface

The engine reads from the pre-scheduler's combined output:

| Pre-Scheduler Output | Engine Consumes |
|----------------------|-----------------|
| `tag_config.provider_tags` | Tag-based constraints (do_not_schedule, no_elmer, pct_override, swing_shift, etc.) |
| `prior_actuals.computed` | Accurate remaining weeks/weekends (not Excel, which may be stale) |
| `difficulty.records` | Risk level, density, capacity status per provider |
| `difficulty.by_risk.HIGH` | Providers to flag as difficult / potentially impossible |
| `holiday.records` | Memorial Day tier, availability, preferences per provider |
| `holiday.supply_vs_demand` | How many MUST/SHOULD providers available |

The engine also reads the Excel workbook directly for provider data, site
demand, and any manual corrections the user made after reviewing the
pre-scheduler output.

---

## Success Criteria

The V3 engine is successful if:

1. **Zero hard constraint violations** in the draft schedule
2. **85%+ weekday demand coverage** across all sites
3. **75%+ weekend demand coverage** across all sites
4. **Zero-gap sites filled 90%+** of weeks
5. **Gap report is actionable** — every gap has at least one viable candidate
   listed, or is explicitly marked as "no candidates available"
6. **Manual scheduler can close remaining gaps** in 2-4 hours (vs. building
   from scratch in 20+ hours)
7. **Draft schedule is stable** — the assignments in it don't need to be
   rearranged to close gaps. The manual scheduler only adds, never removes.

The goal is not to replace the manual scheduler. The goal is to give them an
85-90% head start that respects all the hard rules, so they can focus their
expertise on the 10-15% that requires human judgment.
