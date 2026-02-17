# Case Studies

Detailed analysis of specific scheduling scenarios and edge cases encountered during development.

---

## Case 1: Yagnik — No Long Call Despite Working a Full Week

**Seed:** `4b16c7ed`

### The Problem
Yagnik, Hena (DC provider) works Sat 03/21 through Fri 03/27 but receives no long call during that stretch, despite having only 2 total LCs in the block. Meanwhile, McMackin, Paul (teaching) gets a double in the same week: 03/25 Wed teaching + 03/29 Sun teaching.

### Root Cause
This is a genuine supply/demand crunch at the intersection of two constraints:

1. **Teaching supply is tight:** 7 teaching slots need filling (5 weekday + 2 weekend), but only 6 teaching providers are available (5 for weekday + 1 weekend-only). One must double, or one slot stays empty.

2. **DC supply is tight:** 13 DC providers compete for 12 DC slots (10 weekday + 2 weekend). One DC provider must miss.

3. **No teaching overflow possible:** With 5 providers for 5 weekday teaching slots (after losing 1 to weekend), there are zero surplus teachers to overflow into DC and free space.

### Why It's Hard to Fix
- Can't block McMackin from the weekend — no Group B teacher for 03/29
- Can't create a surplus teacher — Hilditch correctly consumed by previous window's weekend
- Can't give Yagnik a teaching slot — DC can't fill teaching (weekday)
- Phase 2.5 (minimum guarantee) can't help — Yagnik already has 1+ LCs
- Phase 4 can partially help — catches 2+ missed weeks, but this may be Yagnik's only miss

### The Deeper Issue
Hilditch (teaching) got consumed by the **previous** window's weekend (03/22 Sun teaching) as a Group B provider. This is correct behavior, but it reduces week 13's teaching pool from 6 to 5, exactly matching the 5 weekday teaching slots with zero surplus.

**Fix would require look-ahead:** Recognizing that giving Hilditch the 03/22 weekend would create a tight teaching week in week 13, and preferring a different provider for 03/22.

### Takeaway
Some misses are genuinely unavoidable given the constraints. The engine correctly identifies and flags them. Phase 4 provides a late-stage correction when the same provider has 2+ missed weeks.

---

## Case 2: Cooper Gaps — Why Under-Utilized Providers Can't Fill Them

### The Problem
Block schedule reports showed 4–11 unfilled Cooper slots per period, despite 15 Cooper-eligible providers having remaining capacity.

### Investigation
A detailed analysis of each under-utilized Cooper-eligible provider revealed why they couldn't be placed:

**Example — Shor:** Unavailable for weeks 5–17 (personal time-off requests). Only available for the first 4 weeks of the block, and those were already filled.

**Example — Shklar:** Already assigned to 12 of 17 weeks. Remaining 5 weeks blocked by either: (a) consecutive-cap violations (would create 4+ consecutive weeks), or (b) time-off requests on those specific weeks.

### Root Cause
For every under-utilized provider, the gaps aligned with one of:
1. **Unavailability** — Provider marked those days as unavailable
2. **Consecutive cap** — Placing them would exceed the 3-consecutive-week maximum
3. **Already saturated** — Provider is already near their cap for the block

These are genuine scheduling impossibilities, not engine bugs. The gaps cannot be filled without violating hard constraints.

### Takeaway
Some site gaps are inherent to the provider pool's availability. The engine correctly refuses to place providers in violation of hard constraints. Cooper absorbs remaining capacity by design, so Cooper gaps are expected when the overall provider pool can't cover all demand.

---

## Case 3: The deploy_pages.sh Disaster — Recovering From git clean

### The Problem
The first version of `deploy_pages.sh` used `git checkout --orphan gh-pages` followed by `git clean -fd` to create a clean gh-pages branch. This command wiped all untracked files in the working directory — including Python source files, the deploy script itself, and all output HTML.

### What Was Lost
- `block_schedule_engine.py` (1525 lines)
- `generate_block_report.py` (1893 lines)
- `deploy_pages.sh` (the script that caused the problem)
- All output HTML files
- Various other untracked files

### Recovery
Files were recovered from a dangling stash commit (`2353ef82`) that happened to contain the files. This was fortunate — without that stash, the files would have been permanently lost.

### Fix
The deploy script was completely rewritten to use `git worktree add` instead of branch switching:
- `git worktree add "$TMPDIR/worktree" gh-pages` creates a separate working tree
- All operations happen in the temp worktree
- The main working tree is never touched
- The worktree is cleaned up after the push

### Takeaway
Never use `git checkout` + `git clean` in a script that runs in the main working tree. Git worktrees are the safe way to operate on a different branch without affecting the current working directory.

---

## Case 4: Stretch Override Double-Counting

### The Problem
The block schedule report showed "55 Consecutive Stretch Overrides" on the shortfalls tab. But examining the data, many entries appeared to be the same stretch counted multiple times.

### Investigation
A provider's 3-week consecutive run (e.g., weeks 7, 8, 9) was logged as an override event each time a placement extended the run:
- Weekday placement in week 9 → override logged (run is now 3)
- Weekend placement in week 9 → override logged again (same run, counted twice)

Additionally, the tracking was per-placement, not per-run, so a single run could generate 2–3 entries.

### Fix
Replaced raw per-placement tracking with deduplication:
1. After all placements complete, scan actual final assignments
2. For each forced-fill override event, find the actual consecutive run it belongs to
3. Use a `seen_runs` set keyed by `(provider_name, start_week, end_week)` to deduplicate
4. Only count each unique run once
5. Report shows week ranges (e.g., "Weeks 7–9") instead of confusing per-placement data

### Result
55 → actual unique overrides (significantly fewer). The report now accurately represents the scheduling reality.

---

## Case 5: Over-Assigned Providers Display Bug

### The Problem
The "Over-Assigned Providers" table showed providers who appeared to have exactly the right number of assignments (e.g., "6/6 weeks"). Why were they flagged?

### Investigation
Two bugs in the detection logic:

1. **int() vs ceil() mismatch:** The check used `int(wk_rem)` to compute the target (truncates — 5.5 → 5), but the display used `ceil(wk_rem)` (rounds up — 5.5 → 6). A provider with 5.5 remaining, assigned 6: check says `6 > 5` (flagged!), display says `6/6` (looks fine!).

2. **Negative remaining:** Providers who were already over their annual target from prior blocks had negative remaining (e.g., -4.0). The engine assigned them 0 weeks in the current block, but `0 > -4` triggered the over-assigned flag.

### Fix
Changed to `max(0, math.ceil(wk_rem))` for both the check and the display. This ensures:
- Fractional targets round up consistently
- Negative remaining (already over target) is floored at 0
- The check and display always agree

### Result
0 over-assigned providers (was incorrectly showing several).
