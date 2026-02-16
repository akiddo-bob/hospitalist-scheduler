# Issue Analysis: Yagnik 03/21–03/27 No Long Call (Seed 4b16c7ed)

## The Problem

In seed `4b16c7ed`, Yagnik, Hena (DC) works 03/21 Sat – 03/27 Fri but gets **no LC**
during that stretch, despite having only 2 total LCs in the block. Meanwhile, McMackin, Paul
(teaching) gets a **double** in the same stretch: 03/25 Wed teaching + 03/29 Sun teaching.

## The Numbers

### Week 13 window: W1=03/23–27, WE=03/28–29, W2=03/30–04/03

**Teaching side:**
| Provider | Stretch | Group | Available for |
|----------|---------|-------|---------------|
| Peet, Alisa | 03/23–27 | no-WE | weekday teaching |
| Troyanovich, Steve | 03/23–27 | no-WE | weekday teaching |
| Hassinger, Gabrielle | 03/21–27 | (served from prev? no) | weekday teaching |
| Hilditch, Gregory | 03/21–27 | served_from_prev_window (got 03/22 Sun teaching in week 12) | **SKIPPED** |
| McMackin, Paul | 03/23–29 | A (W1+WE) | weekday teaching OR weekend |
| Nash, Rachel | 03/23–29 | A (W1+WE) | weekday teaching OR weekend |
| Nguy, Steven | 03/28–04/03 | B (WE+W2) | weekend only |

Teaching slots: **5 weekday** + **2 weekend** = **7 total**
Teaching providers: **5 for weekday** (Peet, Troyanovich, Hassinger, McMackin, Nash) + **1 for weekend only** (Nguy)

**DC side:**
| Count | What |
|-------|------|
| 13 | DC providers working 03/23–27 (including Yagnik) |
| 10 | Weekday DC slots (5 dc1 + 5 dc2) |
| 2 | Weekend DC slots (03/28 dc2 + 03/29 dc2) |
| **12** | **Total DC capacity** |

## What Happens

### Step A (fill weekend 03/28–29):
1. **03/28 teaching** → Nguy (Group B, only Group B teacher available)
2. **03/29 teaching** → McMackin or Nash (Group A, no more Group B teachers)
   - McMackin takes it. He's now in `served_this_window`.
3. **03/28 dc2** → Deen, Imad ud (Group A DC)
4. **03/29 dc2** → Haydar, Ali (Group A DC)

**Teaching coverage guard fires:** McMackin and Nash are both flagged as W1-needed teachers.
With 5 available and 5 weekday teaching slots, the guard blocks them from the weekend
unless doing so wouldn't leave teaching short. Since remaining_teachers (4) < 5, the
first one is blocked. But on 03/29, one of them MUST take it (Nguy already served).
Result: the guard allows one through.

### Step B (fill weekday teaching 03/23–27):
4 remaining teaching providers: Peet, Troyanovich, Hassinger, Nash
5 weekday teaching slots → **1 slot unfilled**

### Step C (fill weekday DC 03/23–27):
- No teaching overflow (all 4 remaining teachers used in Step B)
- 11 remaining DC providers compete for 10 weekday DC slots
- Yagnik is the one squeezed out (by tiebreaker — most DC providers have similar LC counts at this point in the block)

### Phase 3 (fill empty slots):
The 1 unfilled weekday teaching slot gets double-filled by McMackin (Phase 3 `find_double_filler`).
He now has 03/25 teaching + 03/29 teaching = double in stretch 03/23–29.

## Root Cause

This is a **genuine supply/demand crunch** at the intersection of two constraints:

1. **Teaching supply is tight**: 7 teaching slots (5 weekday + 2 weekend) need filling,
   but only 6 teaching providers are available (5 for weekday + 1 weekend-only).
   One must double, or one slot stays empty until Phase 3 fills it.

2. **DC supply is tight**: 13 DC providers compete for 12 DC slots (10 weekday + 2 weekend).
   One DC provider must miss.

3. **No teaching overflow possible**: Because teaching supply is already short
   (5 providers for 5 weekday slots after losing 1 to weekend), there are zero surplus
   teachers to overflow into DC. If there were 7 teaching providers, the 6th (after 5 fill
   teaching + 1 goes to weekend) could take a DC slot, freeing space for Yagnik.

### Why this is hard to fix in the engine:
- You can't block McMackin from the weekend — there's no Group B teacher for 03/29
- You can't create a surplus teacher — Hilditch is correctly skipped (he already got 03/22 from the previous window)
- You can't give Yagnik a teaching slot — DC can't fill teaching
- Phase 2.5 (minimum guarantee) can't help — Yagnik has 1+ LCs already
- Phase 4 can help partially — it catches that Yagnik has a missed week and tries to swap her in by displacing someone with fewer misses, but this is a late-stage fix

### Why it happens specifically in this week:
- Hilditch got consumed by the **previous** window's weekend (03/22 Sun teaching)
  as a Group B provider. This is correct behavior — Group B providers who get a
  weekend LC are "done" for the next window.
- But it reduces week 13's teaching pool from 6 to 5, exactly matching the 5
  weekday teaching slots with zero surplus.

## Possible Solutions

### Option A: Smarter Group B handling in previous window
In week 12, if the engine recognized that giving Hilditch the 03/22 weekend
teaching would create a tight teaching week in week 13, it could prefer a
different provider for 03/22. But this requires look-ahead across windows.

### Option B: Teaching overflow to DC in Phase 3
When Phase 3 finds an unfilled teaching slot AND there's a missed DC provider in the
same week, instead of just double-filling the teaching slot, it could:
1. Check if any teaching provider already assigned in this week could swap to a DC slot
2. This frees their teaching slot for the double-filler
3. The missed DC provider takes the newly freed DC slot

This is a multi-step swap that Phase 3/3.5 could potentially handle.

### Option C: Accept and let Phase 4 handle it
Phase 4 already catches providers with 2+ missed weeks and swaps them in.
If Yagnik's miss in 03/21–27 is her only miss, Phase 4 won't trigger (needs 2+).
If she has 2+ misses elsewhere, Phase 4 would try to fix it.

### Option D: Allow teaching providers to take DC slots in Step A
If Step A could assign a teaching provider to a weekend DC slot (not just
teaching slots), the teaching provider freed from the weekend teaching slot
could stay for weekday coverage. But this changes the category matching rules.

### What actually happens today:
McMackin doubles (03/25 teaching + 03/29 teaching), Yagnik misses. Phase 4
may or may not fix Yagnik depending on her miss count elsewhere. In the current
run she ends with 2 LCs total (03/11 and 04/18 from other stretches).
