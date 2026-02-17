# Decision History

Key design decisions made during development of the hospitalist scheduler, with rationale and context for each.

---

## Long Call Engine Decisions

### 1. Two-Weekday Doubles: Absolutely Forbidden

**Decision:** No provider may receive two weekday long calls in the same ISO week, regardless of stretch type.

**Context:** An early version of the engine (Rule 4) allowed two-weekday doubles in "weekday-only stretches" — stretches that contained only Mon–Fri days with no weekend. The theory was that these stretches had no weekend day available for the second LC, so two weekdays were acceptable.

**What the data showed:** Analysis of 3 blocks of manual LC assignments revealed **zero two-weekday doubles** across all blocks. Not one instance. The manual schedulers never do it, even when a stretch is weekday-only.

**Resolution:** Rule 4 was removed entirely. The engine now enforces that all doubles must be 1 weekday + 1 weekend. If a stretch is weekday-only (no adjacent weekend), the provider simply doesn't get a double — they get one LC, and the miss is tracked for potential makeup later.

---

### 2. DC on Teaching Slots: More Common Than Expected

**Decision:** Allow DC→teaching on weekends as a last resort, flag all instances.

**Context:** The initial assumption was that DC providers filling teaching long call slots was rare (1–2 per block). The rule was treated as nearly absolute.

**What the data showed:** 41 violations across 3 blocks (9/14/18 per block), with weekends dominating (25 of 41). Same-day analysis revealed these are almost never reciprocal swaps — on the specific weekend day, there simply aren't enough teaching providers. Crossover providers (who rotate between teaching and DC) are 2x overrepresented, but about half are pure DC providers pressed into service.

**Resolution:** The engine allows DC→teaching on weekends when teaching supply is genuinely short. All instances are flagged in the report for manual review. DC→teaching on weekdays remains forbidden. The validation check was adjusted from FAIL to WARN for weekend instances.

---

### 3. Weekend LC Limit: Target 1, Hard Cap 2

**Decision:** Target max 1 weekend LC per provider; allow 2 as hard ceiling.

**Context:** Initial rule was "max 1 weekend LC per provider, period." But what happens when there aren't enough providers to fill all weekend slots with unique providers?

**What the data showed:** Manual data shows max 1 in the vast majority of cases, with only 3 instances of 2 across all 3 blocks. The manual schedulers try very hard to keep it at 1.

**Resolution:** The engine uses bipartite matching to minimize weekend LCs per provider. Providers who work more weekends are preferred (protecting those with 1–2 weekends). The hard cap is 2, enforced in both Phase 1 (bipartite matching) and Phase 3 (double filling). The validation flags 2 as WARN and 3+ as FAIL.

---

### 4. Rule 12 (Consecutive Misses): Aspirational, Not Absolute

**Decision:** The engine tries hard to prevent 2+ consecutive weeks without an LC, but accepts that some violations are unavoidable.

**Context:** Initial implementation treated this as a hard constraint. But during validation against manual data, it kept failing.

**What the data showed:** 17/18/8 providers per block have 2+ truly consecutive work weeks without an LC in the manual data (after correctly excluding moonlighting weeks and verifying ISO week adjacency). Most are max 2 consecutive. This is a real pattern in manually-crafted schedules.

**Resolution:** The engine prioritizes providers with consecutive gaps (they get top priority in the next week's assignment), but doesn't guarantee prevention. The validation check flags violations for review but the engine doesn't consider them hard failures. The discovery process was iterative — original counts were 58/48/21, then 26/32/10 after adjacency fix, then 17/18/8 after moonlighting exclusion.

---

### 5. Consolidated Provider State (pstate)

**Decision:** Replace 8+ separate defaultdicts with a single consolidated `pstate` dictionary per provider.

**Context:** The original engine tracked provider state across many independent data structures: one dict for total LCs, one for weekend LCs, one for DC1 counts, one for missed weeks, etc. This made it easy to lose track of state and introduced subtle bugs when one dict was updated but another wasn't.

**Resolution:** The February 2026 rebuild introduced `pstate` — a single dictionary per provider containing all tracking fields. Helper functions `_assign_slot()` and `_unassign_slot()` centralize all state updates, ensuring consistency. This was a significant architectural improvement that made the codebase more maintainable and reduced bugs.

---

### 6. Bipartite Matching for Weekends

**Decision:** Use networkx bipartite matching for weekend LC assignment.

**Context:** Weekend assignment is a global optimization problem — you need to consider all weekend slots across the entire block simultaneously. A greedy week-by-week approach would create poor distributions (some providers getting multiple weekend LCs while others who work weekends get none).

**Resolution:** Phase 1 builds a bipartite graph with weekend slots on one side and eligible providers on the other, weighted by fairness criteria (prefer providers with more weekends worked). The matching optimizes globally, then a second pass swaps out low-weekend providers for higher-weekend alternatives. This consistently produces better weekend distributions than greedy approaches.

---

### 7. Sliding Window for Weekday Assignment

**Decision:** Use a sliding window (W1 + WE + W2) rather than processing each week independently.

**Context:** Providers whose stretches span a weekend (e.g., Mon–Sun) create dependencies between adjacent weeks. Processing weeks independently can lead to conflicts where a provider's weekend LC from one week's perspective collides with their weekday assignment from the next week's perspective.

**Resolution:** The sliding window considers three periods together: the current week's weekdays (W1), the weekend (WE), and the next week's weekdays (W2). Providers are classified into groups (A: stretch ends on weekend, B: stretch starts on weekend, C: standalone weekend) to handle these cross-week dependencies. This prevents conflicts and ensures fair assignment across week boundaries.

---

### 8. Multiple Variations via Random Seeds

**Decision:** Generate multiple report variations using different random seeds, rather than a single deterministic output.

**Context:** When multiple providers are equally ranked for a slot, something has to break the tie. A deterministic approach (e.g., alphabetical) would systematically favor certain providers. A purely random approach gives a different answer each time.

**Resolution:** The engine uses a hash-based tiebreaker that takes the provider name and a seed as input. Different seeds produce different tiebreaker orderings, resulting in materially different assignments. Users generate 5 variations and pick the one that looks best. The seed is recorded in the report for reproducibility.

---

## Block Schedule Engine Decisions

### 9. Fair-Share Two-Pass Scheduling

**Decision:** Use a two-pass approach — cap at fair share first, then lift caps for behind-pace providers.

**Context:** A single-pass approach either over-assigns some providers (if caps are loose) or leaves gaps (if caps are tight). Providers who got fewer shifts in Blocks 1–2 need to catch up in Block 3, but you don't want to over-assign providers who are already on pace.

**Resolution:** Pass 1 caps each provider at `ceil(annual_target / 3)`. Pass 2 lifts the cap for providers who are behind their annual pace (didn't get enough in previous blocks). This balances fairness with flexibility.

---

### 10. Consecutive Stretch Cap: Maximum 3 Weeks

**Decision:** Hard cap at 3 consecutive weeks in forced fill. No exceptions.

**Context:** The forced fill phase uses `ignore_consec=True` to relax the normal 2-consecutive-week limit when filling remaining gaps. But the original implementation had **no upper limit at all** — creating runs of 9 consecutive weeks (63+ days of continuous work).

**What happened:** The first report showed 40 "Consecutive Stretch Overrides" with runs as long as 9 weeks. This was clearly unacceptable — no provider should work 9 consecutive weeks.

**Resolution:** Added a hard cap: `if run_len > 3: continue` in the forced fill loop. Results: 40 overrides → 9 overrides, all exactly 3 consecutive weeks. Site gaps increased from 131 → 162 (tradeoff: more unfilled slots but no insane stretches). The tradeoff was accepted — it's better to have a few gaps than to destroy provider wellbeing.

---

### 11. Cooper Fills Last

**Decision:** Fill non-Cooper sites first, then Cooper absorbs remaining capacity.

**Context:** Cooper Camden is the largest site (26 weekday doctors). All other sites have fixed, smaller staffing needs. If Cooper were filled first, it would consume provider capacity that smaller sites need, leaving them unfilled.

**Resolution:** The engine processes sites in priority order: non-Cooper sites first (Mullica Hill, Vineland, Virtua, Cape, Mannington, Elmer), then Cooper last. Cooper absorbs whatever provider capacity remains after all other sites are staffed. This means Cooper may have gaps, but smaller sites are protected.

---

### 12. Over-Assigned Detection: ceil() Not int()

**Decision:** Use `max(0, math.ceil(remaining))` for over-assignment detection, not `int()`.

**Context:** The original check used `int(wk_rem)` to determine a provider's remaining target, but the display used `ceil(wk_rem)`. A provider with 5.5 remaining weeks assigned 6: `int(5.5) = 5`, so `6 > 5` flagged them as over-assigned, but `ceil(5.5) = 6`, so the display showed `6/6` — looking perfectly fine.

Additionally, providers with negative remaining (already over their annual target from prior blocks) were flagged because `0 > -4` was true.

**Resolution:** Changed to `max(0, math.ceil(wk_rem))` for both the check and the display. This ensures consistent behavior: a provider is only flagged when they genuinely exceed their target. The `max(0, ...)` handles negative remaining gracefully.

---

### 13. Stretch Override Deduplication

**Decision:** Deduplicate stretch overrides by unique provider+run, not by per-placement events.

**Context:** The original tracking logged a stretch override event each time a provider was placed in a period that extended a consecutive run. A provider in a 3-week run logged 2 events (one for the weekday placement, one for the weekend placement in the same period). This made the override count confusing — the user saw "55 overrides" but many were the same run counted multiple times.

**Resolution:** Replaced raw per-placement tracking with deduplication. After all placements, the engine scans actual final assignments, finds unique consecutive runs per provider, and uses a `seen_runs` set keyed by `(provider_name, start_week, end_week)`. The report now shows the actual run (e.g., "Weeks 7–9") instead of confusing per-placement data.

---

### 14. Removed "Long Stretches (>7 days)" Section

**Decision:** Remove the "Long Stretches (>7 days)" section from the block schedule report entirely.

**Context:** This section listed every provider with 2+ consecutive weeks of assignments. But 2 consecutive weeks is *normal* scheduling — it's the expected pattern when a provider works a week and the adjacent weekend. The section was redundant with "Consecutive Stretch Overrides" (which properly flags 3+ week runs) and was confusing users.

**Resolution:** Removed the section. The summary stats table was updated to reference "Stretch overrides (3+ consecutive weeks)" instead. A bug was also fixed where the removed `long_stretch_rows` variable was still referenced in the summary stats calculation.

---

## Deployment Decisions

### 15. Git Worktree for Safe Deployment

**Decision:** Use `git worktree` for gh-pages deployment instead of branch switching.

**Context:** The first version of `deploy_pages.sh` used `git checkout --orphan gh-pages` followed by `git clean -fd` to create a clean branch. This was catastrophic — `git clean -fd` deleted all untracked files in the working directory, including `block_schedule_engine.py`, `generate_block_report.py`, `deploy_pages.sh` itself, and all output HTML files. The files were recovered from a dangling stash commit.

**Resolution:** Rewrote the deploy script to use `git worktree add`, which creates a separate working tree for the gh-pages branch in a temp directory. The main working tree is never touched. The temp worktree is cleaned up after the push.

---

### 16. Iframe-Based Password Gate

**Decision:** Use a single gate page with an iframe instead of wrapping each HTML file with a password check.

**Context:** Three approaches were tried:
1. **Per-page wrapping** — Each HTML file was wrapped with base64-encoded content and a `document.write()` unlock. Problem: `document.write()` didn't reliably work across all pages.
2. **Head-script early unlock** — Password check moved to `<head>` to run before body renders. Problem: still flashed the gate briefly on some loads.
3. **Iframe gate** — Single `index.html` at root with password form. On unlock, loads `reports/index.html` in a full-page iframe.

**Resolution:** The iframe approach works because all navigation happens inside the iframe. The user enters the password once, and all subsequent page loads (clicking between reports, viewing different variations) happen within the iframe context. The gate page never re-renders. `sessionStorage` persists the auth state for return visits in the same browser session.

---

## Analysis and Validation Decisions

### 17. Ground Truth Analysis Against Manual Data

**Decision:** Analyze 3 complete blocks of manually-crafted LC assignments before finalizing engine rules.

**Context:** The initial rule set was based on written documentation and verbal descriptions from the scheduling coordinators. Some rules were assumed to be absolute that turned out to be aspirational, and some "rare" events turned out to be common.

**What it revealed:**
- Rule 1 (DC→teaching) happens 9–18 times per block, not 1–2
- Rule 12 (consecutive misses) is violated 8–18 times per block
- Rule 3 (two-weekday doubles) is truly absolute — zero violations
- Doubles always compensate for misses — zero exceptions
- DC1/DC2 balance is not tracked in manual scheduling at all

**Resolution:** Created `analyze_manual_lc.py` to systematically validate every rule against 3 blocks of data. This analysis drove multiple rule adjustments and calibration changes. The findings are documented in `docs/manual-lc-analysis.md`.

---

### 18. Name Matching Strategy

**Decision:** Use fuzzy matching with explicit alias overrides for edge cases.

**Context:** Provider names in the Excel manual LC tracker don't always match Amion format exactly. Common issues: "Kiki Li" vs "Li, Ka Yi" (nickname), "Dhilon" vs "Dhillon" (typo), "Zhang" vs "Zheng" (different romanization).

**Resolution:** The analysis tool uses fuzzy string matching for most cases but maintains a `NAME_ALIASES` dictionary for cases that fuzzy matching can't resolve (e.g., `"Kiki Li": "Li, Ka Yi"`). The block schedule engine uses a similar approach with `build_name_index()` and `match_provider_to_json()`.

---

### 19. Moonlighting Detection via xpay Icon

**Decision:** Detect moonlighting shifts by the presence of the `xpay_dull.gif` icon in Amion HTML exports.

**Context:** Moonlighting shifts need to be identified to exclude providers from LC during those stretches. Amion marks extra-pay shifts with a specific icon.

**Resolution:** The parser (`parse_schedule.py`) detects the `xpay_dull.gif` icon during HTML parsing and flags the corresponding assignments as moonlighting. This flag propagates through the entire pipeline — the LC engine uses it to exclude providers, and the analysis tools use it to correctly count working weeks.

---

### 20. Weekend-Start Stretch Merging

**Decision:** When a stretch starts on Saturday and continues into the following weekdays (e.g., Sat–Fri), the leading weekend is merged with the weekdays as one chunk, not split off as a standalone weekend.

**Context:** An early bug caused Sat–Sun to be classified as a standalone weekend when it was followed by Mon–Fri on the same source service. This meant the provider's stretch was incorrectly split into two parts: a "standalone weekend" (deprioritized for LC) and a separate week — instead of being treated as one 7-day stretch.

**Resolution:** The stretch identification logic was fixed to detect and merge leading weekends into the following week's chunk. The validation suite includes a specific check (Check 11: "Weekend-start stretches not orphaned") to catch regressions.
