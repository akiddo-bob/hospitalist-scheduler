# Long Call Rules Reference

Complete rule definitions for the long call assignment engine, validated against 3 blocks of manual assignment data (ground truth).

## Terminology

| Term | Definition |
|------|-----------|
| **Stretch** | Consecutive days a provider works on source services (non-source days are invisible) |
| **Mixed stretch** | A stretch containing both weekday and weekend/holiday days |
| **Weekday-only stretch** | A stretch with only Mon–Fri, non-holiday days |
| **Standalone weekend** | A stretch of only Sat/Sun with no adjacent source-service weekdays |
| **ISO week** | Mon–Sun calendar week; the key unit is the Mon–Fri portion |
| **Assignment need** | One provider needs one LC somewhere in their ISO week dates |
| **Window** | A sliding unit: W1 (weekdays) + WE (weekend) + W2 (next weekdays) |
| **Group A** | Provider whose stretch ends on the weekend (overlaps W1 + WE) |
| **Group B** | Provider whose stretch starts on the weekend (overlaps WE + W2) |
| **Group C** | Provider working only the standalone weekend |
| **Double** | A provider getting 2 LCs in the same stretch (always 1 weekday + 1 weekend) |

---

## Strong Rules (Hard Constraints)

These are enforced strictly. Violations are flagged as FAIL in validation.

| # | Rule | Ground Truth |
|---|------|-------------|
| 1 | **DC providers cannot fill teaching slots except as last resort** — Never on weekdays. Rare on weekends (only when teaching supply is genuinely short). | 41 violations across 3 blocks (9/14/18). Weekend-heavy (25 of 41). Driven by genuine teaching shortages, not swapping. |
| 2 | **Teaching providers CAN fill DC slots (overflow)** — Normal once all teaching slots are filled that week. | 15–24 overflow instances per block. Routine. |
| 3 | **No two-weekday LCs in the same ISO week** — If a provider works W1+WE+W2 they get 2 LCs total, but never two on weekdays in the same Mon–Fri block. A weekend LC replaces one weekday LC. | **Zero violations across all 3 blocks.** Absolute. |
| 5 | **Excluded providers get no LCs** | N/A (config-driven exclusion) |
| 6 | **Moonlighting stretches get no LCs** — Entire stretch excluded, not just moonlighting days. | Consistent in manual data. |
| 7 | **Min 2-day gap between LCs in same stretch** | **Zero violations.** All doubles have 3+ day gaps. |
| 8 | **No double if stretch has a holiday LC** | **Zero violations.** |
| 9 | **Weekends/holidays have no DC1 slot** — Only 2 slots (teaching + DC2). | **Zero violations.** |
| 10 | **Max 2 weekend LCs per provider across the block** — Manual data shows max 1 in all 3 blocks with only 3 exceptions at exactly 2. | **Zero violations** (at the >2 level). |
| 11 | **All slots filled before anyone gets a double** — Can't have an unfilled slot and a double in the same week. | **Zero violations.** The manual scheduler always fills all available slots before allowing doubles. |
| 12 | **No 2+ consecutive weeks without an LC** | 17/18/8 providers per block violate this in manual data. Most are max 2 consecutive. **Aspirational, not absolute.** |

**Note:** Old Rule 4 ("two-weekday doubles allowed in weekday-only stretches") was removed — it was invalid. No two-weekday doubles are allowed in any stretch type.

---

## Soft Rules (Goals)

These are optimization targets. Violations are flagged as WARN in validation.

| # | Rule | Ground Truth |
|---|------|-------------|
| A | **Prefer 3+ day gap** between LCs in same stretch | 100% achieved in manual data. Hard min is 2 (Strong Rule 7). |
| B | **Max 1 weekend LC per provider** across the block | Goal is 1; hard max is 2. Manual data: 3 instances of 2 weekend LCs across all 3 blocks. |
| C | **LCs proportional to weeks worked** — Each provider's total LC count should be within 1 of their weeks worked. | 29–48 providers per block exceed ±1 difference. Expected — not all providers can get an LC every week. |
| D | **Day-of-week variety** — Avoid 3+ LCs on same weekday for providers with 6+ weeks. | ~8 providers per block have 3+ on same day. Most at 3x. |
| E | **DC1/DC2 balance** per provider — Within 1 of each other. | 17–30 providers per block imbalanced >1. The manual process doesn't focus on this. |
| F | **Providers with a missed week get priority for a double later** | 100% in manual data — every provider with a double also has a missed week. Zero "double only" cases. |

---

## Validation Checks (14-point suite)

Run with: `.venv/bin/python3 validate_reports.py`

| Check | Level | Description |
|-------|-------|-------------|
| 1 | FAIL | No two-weekday doubles in any ISO week |
| 2 | FAIL (>2), WARN (=2) | Max 2 weekend LCs per provider |
| 3 | WARN | LC count within 1 of weeks worked |
| 4 | FAIL | No DC provider on teaching slot (except last resort) |
| 5 | FAIL | No 2+ consecutive weeks without LC |
| 6 | FAIL | No moonlighter LC during moonlighting stretch |
| 7 | FAIL | No excluded provider has LCs |
| 8 | WARN | No provider with 6+ weeks has 3+ LCs on same weekday |
| 9 | WARN | DC1/DC2 balanced within 1 per provider |
| 10 | PASS | Standalone weekends handled in stats |
| 11 | PASS | No orphaned weekend fragments |
| 12 | PASS | No holidays have DC1 assignments |
| 13 | FAIL | All slots filled (no unfilled slot + double in same week) |
| 14 | PASS | Stats consistency (counts match assignments) |

---

## Rule 1 Deep Dive: DC on Teaching Slots

This is the most nuanced rule. Analysis of 3 blocks of manual data reveals:

**It happens more than expected:** 9–18 times per block, with weekends dominating (25 of 41 total).

**Weekend teaching shortages drive it:** Same-day analysis shows these are almost never reciprocal swaps. On the specific weekend day, there simply aren't enough teaching providers to fill the teaching LC slot.

**Crossover providers are overrepresented:** Rule 1 violators are 2x more likely to be providers who rotate between teaching and DC services (44–50% vs 22–27% for the general population). However, about half are pure DC providers pressed into service.

**The engine must handle this gracefully:** DC→teaching fallback is allowed on weekends as last resort, with all instances flagged for review.

---

## Key Patterns from Ground Truth

### Doubles
- Block 1: 7 doubles, Block 2: 0 doubles, Block 3: 4 doubles
- **Every double is 1 weekday + 1 weekend** — zero two-weekday doubles across all blocks
- Doubles always compensate for a missed week somewhere — no provider gets a double without having missed a week

### Missed Weeks
- Block 1: 16 missed, Block 2: 0 missed, Block 3: 9 missed
- Many annotated with compensating doubles elsewhere in the block

### Weekend LCs
- Overwhelmingly max 1 per provider
- 3 instances of 2 weekend LCs across all 3 blocks — extremely rare

### Swaps
- 46–47 swaps per block in Blocks 1–2 (accumulated real-world changes)
- Block 3: only 8 swaps (most recently scheduled)
- Swaps can break stretches and create artificial gaps in the data

### Summary Statistics
| Metric | Block 1 | Block 2 | Block 3 |
|--------|---------|---------|---------|
| Days | 126 | 119 | 119 |
| Total LCs | 340 | 320 | 322 |
| Teaching LCs | 126 | 119 | 119 |
| DC1 LCs | 88 | 82 | 84 |
| DC2 LCs | 126 | 119 | 119 |
| Providers with LCs | 101 | 94 | 91 |
| Doubles | 8 | 1 | 4 |
| Weekend LCs | 76 | 74 | 70 |

Teaching = 1 per day (always). DC2 = 1 per day (always). DC1 ≈ 0.7 per day (weekdays only).
