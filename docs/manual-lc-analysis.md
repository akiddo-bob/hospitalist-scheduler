# Manual Long Call Analysis — Findings

Analysis of manual LC assignments across all 3 blocks (Jun 2025 – Jun 2026) against the revised rules.

---

## Strong Rule Results

### Rule 1: DC on Teaching Slot

| Block | Total | Weekday | Weekend |
|-------|-------|---------|---------|
| Block 1 | 9 | 5 | 4 |
| Block 2 | 14 | 7 | 7 |
| Block 3 | 18 | 4 | 14 |
| **Total** | **41** | **16** | **25** |

This happens more than "1-2 per block" — it's 9-18 per block, with weekends being more common in Block 3. This is a real pattern: when teaching supply is tight (especially weekends), DC providers do take teaching slots. The rule should stay as "last resort" but the engine needs to handle this gracefully rather than hard-blocking it.

**Notable Block 3 weekend DC-on-teaching:** Coyle, Luthra, Hanes, Kagan, Wang, Ajemian, Rupp, Liang, Li Ryan, Vlad, Troyanovich, Patel Parita, Logue — 14 out of 18 are weekends.

#### Rule 1 vs Rule 2 Cross-Check (Reciprocal Overflow)

Two levels of analysis: **weekly** (same ISO week) and **same-day** (exact date match).

**Weekly cross-check:** When a DC provider takes a teaching LC, is a teaching provider also filling a DC LC slot that same ISO week?

| Block | Rule 1 weeks | Reciprocal (Rule 2 same week) | Non-reciprocal | Rule 2 overflow (block-wide) |
|-------|-------------|-------------------------------|----------------|------------------------------|
| Block 1 | 9 | **8** (89%) | 1 | 24 |
| Block 2 | 11 | **10** (91%) | 1 | 24 |
| Block 3 | 13 | **9** (69%) | 4 | 15 |

At the weekly level, most Rule 1 weeks also have Rule 2 overflow (teaching→DC). Rule 2 overflow also occurs in standalone weeks with no Rule 1 (6/5/2 per block) — it's routine.

**Same-day cross-check:** On the *specific date* of a Rule 1 violation, was a teaching provider also assigned to a DC LC?

| Block | Rule 1 total | Same-day reciprocal | Not same-day | Weekend Rule 1 | Weekend same-day reciprocal |
|-------|-------------|--------------------|--------------|--------------|-----------------------------|
| Block 1 | 9 | 1 | 8 | 4 | 1 |
| Block 2 | 14 | 3 | 11 | 6 | 0 |
| Block 3 | 18 | 1 | 17 | 14 | 1 |

**Key finding:** Same-day reciprocity is **rare** — especially on weekends. When a DC provider fills a teaching LC on a weekend, it's almost never because a teaching provider is simultaneously filling a DC slot that same day. The weekly cross-check looked high (69-91%) but that was matching weekend Rule 1 with weekday Rule 2 in the same ISO week — not the same supply crunch.

**This means weekend Rule 1 violations are driven by genuine teaching supply shortages on weekends, not by simultaneous swapping.** The engine needs to anticipate that weekends will frequently lack enough teaching providers for the teaching LC slot, and DC providers will need to fill it.

#### Provider Crossover Patterns

Are Rule 1 violators "crossover" providers who rotate between teaching and DC services, or pure DC providers pressed into teaching duty?

Classification: **primarily teaching** (≥80% teaching days), **primarily DC** (≥80% DC days), **crossover** (mixed).

| Block | Rule 1 Violators | Primarily DC | Crossover | Primarily Teaching |
|-------|-----------------|-------------|-----------|-------------------|
| Block 1 | 9 | 5 | 4 | 0 |
| Block 2 | 13 | 7 | 6 | 0 |
| Block 3 | 16 | 8 | 8 | 0 |

**Crossover rate comparison:**

| Block | Rule 1 violators crossover rate | Non-violators crossover rate |
|-------|-------------------------------|------------------------------|
| Block 1 | 44% | 27% |
| Block 2 | 46% | 22% |
| Block 3 | 50% | 25% |

**Key finding:** Rule 1 violators are roughly **2x more likely** to be crossover providers vs the general population (44-50% vs 22-27%). However, about half are still "primarily DC" — pure DC providers pressed into teaching when supply is tight. No Rule 1 violator is "primarily teaching" — it's always DC or crossover providers filling the teaching gap.

### Rule 3: Two Weekday LCs in Same ISO Week

| Block | Violations |
|-------|-----------|
| Block 1 | 0 |
| Block 2 | 0 |
| Block 3 | 0 |

**Zero violations across all 3 blocks.** This rule is perfectly followed in the manual data.

### Rule 7: Min 2-Day Gap Between LCs

| Block | Violations |
|-------|-----------|
| Block 1 | 0 |
| Block 2 | 0 |
| Block 3 | 0 |

**Zero violations.** Every double has at least 2 days between the two LCs.

### Rule 8: No Double with Holiday LC

| Block | Violations |
|-------|-----------|
| Block 1 | 0 |
| Block 2 | 0 |
| Block 3 | 0 |

**Zero violations.**

### Rule 9: No DC1 on Weekend/Holiday

| Block | Violations |
|-------|-----------|
| Block 1 | 0 |
| Block 2 | 0 |
| Block 3 | 0 |

**Zero violations.**

### Rule 10: Max 2 Weekend LCs Per Provider

| Block | Violations (3+) |
|-------|-----------------|
| Block 1 | 0 |
| Block 2 | 0 |
| Block 3 | 0 |

**Zero violations.** Weekend LC distribution:
- Block 1: 76 providers with 1 weekend LC, 25 with 0
- Block 2: 72 with 1, 21 with 0, **1 with 2**
- Block 3: 66 with 1, 23 with 0, **2 with 2**

So 2 weekend LCs does occur (3 instances across all blocks) but is rare. Max 1 is the strong pattern.

### Rule 11: All Slots Filled Before Doubles

Corrected analysis: checks whether any LC slot went unfilled in a week where a provider received a double. The proper test is `total_assigned < total_available_slots` — not just "another provider didn't get an LC" (most providers don't get one in any given week, that's normal).

| Block | Violations (unfilled slot + double in same week) |
|-------|--------------------------------------------------|
| Block 1 | 0 |
| Block 2 | 0 |
| Block 3 | 0 |

**Zero violations across all 3 blocks.** Whenever the manual scheduler gives a double, all available slots that week are filled first. This rule is perfectly followed.

### Rule 12: No 2+ Consecutive Missed Weeks

Corrected analysis with two fixes: (1) only counts misses as "consecutive" when ISO weeks are truly adjacent (week N then week N+1), and (2) excludes moonlighting shifts — a provider on a moonlighting week is not considered "working" for LC purposes.

| Block | Providers with 2+ consecutive missed weeks | Notable |
|-------|---------------------------------------------|---------|
| Block 1 | 17 | All are max 2 consecutive |
| Block 2 | 18 | Gross 3 consec (wks 1-3), Troyanovich 3 (wks 48-50), Shor 2 runs |
| Block 3 | 8 | Mohiuddin 3 consec (wks 17-19), Li Ka Yi 3 (wks 20-22) |

**Correction history:** Original counts were 58/48/21 (non-adjacent weeks counted as consecutive). After adjacency fix: 26/32/10. After moonlighting exclusion: **17/18/8**.

**Interpretation:** Still a real finding — 17-18 providers per block have 2+ truly consecutive work weeks without an LC. Most are max 2 consecutive (not extreme). The engine should try hard to prevent this but may need to accept some occurrences when supply is tight.

---

## Soft Rule Results

### Rule A: Gap Distribution (doubles within stretches)

| Gap | Block 1 | Block 2 | Block 3 |
|-----|---------|---------|---------|
| 3 days | 1 | 0 | 1 |
| 4 days | 6 | 0 | 5 |
| 5 days | 5 | 1 | 1 |
| 6 days | 3 | 2 | 2 |
| 7 days | 1 | 1 | 0 |
| 11 days | 1 | 0 | 0 |

All gaps are ≥3 days. The hard min of 2 is never triggered. The soft preference for 3+ is achieved in 100% of cases.

### Rule B: Weekend LC Distribution

All blocks show max 1 weekend LC per provider as the overwhelming norm. Block 2 has 1 provider with 2, Block 3 has 2 providers with 2. This is rare.

### Rule C: LCs Proportional to Weeks Worked

After excluding moonlighting weeks from the "weeks worked" count, the proportionality improved (48/42/29 providers with |diff| > 1, down from 64/50/30). Max gap is now -6 (was -9). The remaining gaps are expected — not all providers can get an LC every week. The larger gaps (5+) may still indicate some providers are underserved relative to their availability.

### Rule D: Day-of-Week Variety

~8 providers per block have 3+ LCs on the same weekday. Most are at 3x. One provider (Logue) had 6x Tuesday in Block 2 and 4x Thursday in Block 3.

### Rule E: DC1/DC2 Balance

17-30 providers per block have imbalance >1. The manual process doesn't seem to focus on DC1/DC2 balance — many providers have all DC2 and no DC1.

### Rule F: Missed Weeks vs Doubles

In all 3 blocks, every provider with a double also has a missed week — **zero "double only" cases** exist. This confirms that doubles compensate for misses. However, many providers with misses never get a compensating double.

---

## Swap Analysis

| Block | Swaps on Source Services |
|-------|------------------------|
| Block 1 | 46 |
| Block 2 | 47 |
| Block 3 | 8 |

Blocks 1 and 2 have heavy swap activity (46-47 swaps). Block 3 has very few (8) — likely because it's the most recently scheduled and hasn't accumulated as many real-world swaps yet.

---

## Summary Statistics

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

**Key patterns:**
- Teaching = 1 per day (always), DC2 = 1 per day (always)
- DC1 ≈ 0.7 per day (not every day has dc1 — weekends/holidays skip it)
- ~90-100 providers get LCs per block
- Doubles are rare: 1-8 per block
- Weekend LCs: ~70-76 per block (roughly 1 per weekend day)

---

## Key Takeaways for Engine Design

1. **Rule 1 needs flexibility — driven by weekend teaching shortages**: DC-on-teaching happens 9-18 times per block, with weekends dominating (25 of 41 total). Same-day analysis shows these are almost never reciprocal swaps — on the specific weekend day, there simply aren't enough teaching providers to fill the teaching LC. The engine must anticipate weekend teaching supply shortages and allow DC→teaching fallback, especially on weekends. Rule 1 violators are 2x more likely to be crossover providers, but half are pure DC pressed into service.

2. **Rules 3, 7, 8, 9 are absolute**: Zero violations across all blocks. These are truly hard constraints.

3. **Rule 10 is solid**: Max 2 weekend LCs, with the vast majority at 1. The engine should target 1 and allow 2 as a ceiling.

4. **Rule 11 is absolute**: Zero violations — the manual scheduler never gives a double when slots are unfilled. The engine must enforce this strictly: fill all available slots before allowing any doubles.

5. **Rule 12 is aspirational but frequently violated**: 17/18/8 providers per block have 2+ consecutive work weeks without an LC (after excluding moonlighting weeks). Most are max 2 consecutive. The engine should minimize these but cannot treat this as an absolute constraint.

6. **DC1/DC2 balance is not a priority**: The manual scheduler doesn't appear to track this. May be worth keeping as a soft goal but not a validation failure.

7. **Doubles always compensate misses**: No provider gets a double without having a missed week somewhere. The engine should follow this pattern.

8. **Swaps break stretches**: 46-47 swaps per block in Blocks 1-2 create artificial gaps. When analyzing stretches for rule validation, swap-affected days should be flagged.

9. **Moonlighting weeks are invisible to LC assignment**: Providers on moonlighting shifts should not be counted as "working" for LC purposes. This affects Rule 12 (consecutive misses), Rule C (proportionality), and any supply calculations.

---

## Unmatched Names

Some Excel names couldn't be matched to HTML schedule names (typos in Excel):
- Block 1: Jasjit Dhilon, Jon Edelsetin, Kiki Li, Marjorie Cadstin, Sadia Naswhin
- Block 2: Adi Sapassetty, Cindy Glickman, Kiki Li, Vincent Maioriano
- Block 3: Angela Zhang, Chris Fernanez, Gabriela Contrino, Kiki Li, Lliang Xiaohui, Paul Mcmackini

"Kiki Li" appears in all blocks — this is Ka Yi Li (nickname). The others are spelling variations.
