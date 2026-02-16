# Long Call Assignment — Rules & Definitions

## Overview

The engine assigns one long call (LC) per provider per ISO week across a ~17-week block.
Each day has slots: **teaching** + **dc1** + **dc2** on weekdays, **teaching** + **dc2** on weekends/holidays.

Every day, long calls must be assigned from providers working that day.

---

## Terminology

- **Stretch**: Consecutive days a provider works (from schedule data)
- **Mixed stretch**: A stretch containing both weekday and weekend/holiday days
- **Weekday-only stretch**: A stretch with only weekday days (Mon–Fri, non-holiday)
- **Standalone weekend**: A stretch of only weekend/holiday days (1–2 days), not attached to weekdays
- **ISO week**: Mon–Sun calendar week; for LC purposes the key unit is the Mon–Fri portion
- **Assignment need**: One provider needs one LC somewhere in their ISO week dates
- **Window**: A sliding unit of W1 (weekdays) + WE (weekend) + W2 (next week's weekdays)
- **Group A**: Provider whose stretch ends on the weekend (overlaps W1 + WE)
- **Group B**: Provider whose stretch starts on the weekend (overlaps WE + W2)
- **Group C**: Provider working only the standalone weekend
- **Double**: A provider getting 2 LCs in the same stretch (always 1 weekday + 1 weekend)

---

## Strong Rules

| # | Rule | Notes |
|---|------|-------|
| 1 | **DC providers cannot fill teaching slots except as last resort** | Never on weekdays. Extremely rare on weekends (1-2 per block). Only when needed to avoid someone having no LC while another has a double. |
| 2 | **Teaching providers CAN fill DC slots (overflow)** | Normal once all teaching slots are filled that week. |
| 3 | **No two-weekday LCs in the same ISO week** | If a provider works W1+WE+W2 they get 2 LCs total (one per ISO week). A weekend LC replaces one weekday LC — they never have two weekday LCs in the same Mon-Fri block. |
| 5 | **Excluded providers get no LCs** | |
| 6 | **Moonlighting stretches get no LCs** | |
| 7 | **Min 2-day gap between LCs in same stretch** | |
| 8 | **No double if stretch has a holiday LC** | |
| 9 | **Weekends/holidays have no dc1 slot** | |
| 10 | **Max 2 weekend LCs per provider across the block** | Manual data shows max 1 in all 3 blocks. 2 is the hard ceiling. |
| 11 | **All slots must be filled before anyone gets a double** | Can't have someone with no LC and someone with a double in the same week. DC can take teaching slot if needed to make this work. |
| 12 | **No 2+ consecutive weeks without an LC** | Should effectively never happen. |

*Note: Old Rule 4 ("two-weekday doubles allowed in weekday-only stretches") has been **removed** — it is invalid. No two-weekday doubles are allowed in any stretch type.*

---

## Soft Rules (goals, not hard constraints)

| # | Rule | Notes |
|---|------|-------|
| A | **Prefer 3+ day gap** between LCs in same stretch | Hard min is 2 (Strong Rule 7). |
| B | **Max 1 weekend LC per provider** across the block | Goal is 1; strong max is 2. Manual data: 0 providers with 2 weekend LCs in any block. |
| C | **LCs proportional to weeks worked** | |
| D | **Day-of-week variety** — avoid 3+ LCs on same weekday | |
| E | **DC1/DC2 balance** per provider | |
| F | **Providers with a missed week get priority for a double later** | Intentional but not mandatory — depends on whether it can be worked out. |

---

## Manual Data Patterns (ground truth from 3 blocks)

### Doubles
- Block 1: 7, Block 2: 0, Block 3: 4
- **Every double is 1 weekday + 1 weekend** — zero two-weekday doubles
- Doubles compensate for missed weeks when possible

### Missed Weeks
- Block 1: 16, Block 2: 0, Block 3: 9
- Many annotated with compensating doubles elsewhere
- Having a miss makes the provider first priority for a future double

### Weekend LCs
- Max 1 per provider in all 3 blocks
- No provider has 2 weekend LCs in any block in the manual data

---

## Validation Checks

| Check | Level | Description |
|-------|-------|-------------|
| 1 | FAIL | No two-weekday doubles in any ISO week |
| 2 | FAIL | Max 2 weekend LCs per provider |
| 3 | WARN | LC count within 1 of weeks worked |
| 4 | FAIL | No DC provider on teaching slot (except last resort) |
| 5 | FAIL | No 2+ consecutive weeks without LC |
| 6 | FAIL | No moonlighter LC during moonlighting stretch |
| 7 | FAIL | No excluded provider has LCs |
| 8 | WARN | No provider with 6+ weeks has 3+ LCs on same weekday |
| 9 | WARN | DC1/DC2 balanced within 1 per provider |
| 10 | PASS | Standalone weekends handled in stats |
| 11 | PASS | No orphaned weekend fragments |
| 12 | PASS | No holidays have dc1 assignments |
| 13 | FAIL | All slots filled (no one with 0 LC while someone has a double) |
| 14 | PASS | Stats consistency (counts match assignments) |
