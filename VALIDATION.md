# Long Call Report — Validation Checklist

After generating reports, walk through these checks to catch rule violations the engine may have missed or handled incorrectly. Each check tells you where to look in the report and what to look for.

---

## 1. Doubles: Weekday + Weekend, Not Two Weekdays

**Where to look:** Provider Summary table — filter the **Doubles** column for values > 0. Click the provider name to jump to their detail section.

**What to check:** In the provider's detail view, find the stretch where they have two green (LC) rows. One LC should fall on a weekday (Mon–Fri) and the other on a weekend (Sat/Sun) or holiday within the same stretch. If both LCs are on weekdays in the same stretch, that's a violation.

**Why it matters:** Doubles are supposed to split across weekday and weekend to distribute the burden. Two weekday LCs in the same stretch means the provider is covering extra weekday evenings instead of the intended split.

---

## 2. Weekend LC Limit: Max 1 Per Provider

**Where to look:** Weekend Long Call Pivot section — check the "Weekend Long Calls per Provider" table. Any provider with 2+ in the **Weekend LCs** column is flagged in red.

**What to check:** If any provider shows 2+, click their name and find the two weekend/holiday LC rows (green) in their detail. Confirm this was unavoidable — was there truly no one else working that weekend who could have taken it?

**Why it matters:** Weekend LC is a burden. No one should carry it twice in a block unless there's genuinely no alternative.

---

## 3. Total LCs Roughly Equal to Weeks Worked

**Where to look:** Provider Summary table. Compare the **Weeks** column to the **Total LC** column for each provider.

**What to check:** Total LC should be close to Weeks (within 1, ideally equal). Sort by **Missed** descending to find providers who got shorted. Sort by **Doubles** descending to find who got extra. A provider with 8 weeks and 6 LCs is getting shorted. A provider with 4 weeks and 6 LCs is getting overloaded.

**Why it matters:** This is the fundamental fairness rule — long calls proportional to weeks worked.

---

## 4. No DC Provider on Teaching Slot

**Where to look:** Teaching vs Direct Care Assignment Accuracy section.

**What to check:** The **DC provider assigned Teaching** count should be **zero**. If it's not, the table below lists every instance. Click the provider name and verify their service — a provider on H1–H18 should never appear in the Teaching LC column of the daily schedule.

**Why it matters:** Teaching LC should only be covered by teaching service providers. This is a hard rule, not a preference.

---

## 5. Consecutive Weeks Without LC: Max 1

**Where to look:** Flags and Violations section — look for **CONSEC_NO_LC** flags. Also check Provider Summary sorted by **Missed** descending.

**What to check:** Click any flagged provider's name and look at their detail. Find the red-highlighted (NO LC) weeks. Are two or more red weeks in a row? If yes, confirm it was forced — look at those weeks in the Full Schedule to see if every slot was taken by someone else.

**Why it matters:** Going two consecutive weeks without LC while others are getting theirs creates visible inequity. One week gap is acceptable; two is a problem.

---

## 6. Moonlighters Excluded

**Where to look:** Provider Detail — search for providers with grey rows showing "MOON" badges.

**What to check:** Scan a few moonlighting providers. They should have **no green (LC) rows** during any stretch where moonlighting occurs. If a provider has a MOON badge on Monday but an LC badge on Wednesday of the same stretch, that's a violation — moonlighters are excluded from LC for the entire stretch.

**Why it matters:** Moonlighters are already earning extra. They shouldn't also get LC assignments on those stretches.

---

## 7. Excluded Providers Not Appearing

**Where to look:** Provider Summary table — use the filter box to search for any name from the `excluded_providers` list in config.json.

**What to check:** The search should return no results. If an excluded provider appears in the summary or in any daily schedule slot, the exclusion isn't working.

**Why it matters:** Self-schedulers and others on the exclusion list should be completely invisible to the engine.

---

## 8. Day-of-Week Variety

**Where to look:** Day of Week Distribution section.

**What to check:** Look for providers with 2+ assignments on the same day of the week (cells will be highlighted). A provider getting LC every Tuesday for 3 weeks is a pattern that should be spread out. Some concentration is inevitable for providers with few weeks, but providers with 6+ weeks should have reasonable variety.

**Why it matters:** Getting stuck with the same day every time is unfair — some days may conflict with standing personal commitments.

---

## 9. DC1 vs DC2 Balance

**Where to look:** DC1 vs DC2 Balance section.

**What to check:** For each provider, the DC1 and DC2 counts should be within 1 of each other. A provider with 4 DC1 and 0 DC2 (or vice versa) has a problem. The section flags imbalances.

**Why it matters:** DC1 includes a 7a–8a morning shift that DC2 doesn't. Always getting DC1 means always doing the early morning crossover.

---

## 10. Standalone Weekends Handled Correctly

**Where to look:** Provider Summary — look at the **Standalone Wknds** column. Filter for providers with standalone weekends > 0.

**What to check:** Click into the provider detail. Standalone weekends should show as a separate week labeled "SW" in the week number column. They should NOT have a red "NO LC" highlight — standalone weekends are intentionally deprioritized and not missing a LC is not an error. If a standalone weekend DOES have a green LC row, that's fine (it got one), but it shouldn't be at the expense of a real stretch elsewhere.

**Why it matters:** A provider working only Sat-Sun with no adjacent weekdays doesn't have the same LC need as someone working a full week. The engine should not count a missing LC on a standalone weekend as a miss.

---

## 11. Weekend-Start Stretches Not Orphaned

**Where to look:** Provider Detail — look for providers whose stretches start on Saturday.

**What to check:** A Sat-Sun-Mon-Tue-Wed-Thu-Fri stretch should appear as **one week** (week number N) in the detail, not as a standalone weekend (SW) followed by a separate week. The Saturday and Sunday should be in the same week block as the following Mon–Fri. If you see a provider with a "SW" label on Sat-Sun immediately followed by week N starting Monday, the merge isn't working.

**Why it matters:** This was a recent bug fix. The leading weekend is part of the provider's 7-day stretch and should be treated as one unit.

---

## 12. Holidays Treated as Weekends

**Where to look:** Full Schedule — find holiday dates (marked with "HOL"). Also check the Weekend Long Call Pivot for holiday rows.

**What to check:** Holiday rows in the daily schedule should show only 2 slots (Teaching + DC2), not 3. There should be no DC1 column filled on a holiday. In the Weekend Pivot, holidays should appear alongside weekend dates.

**Why it matters:** Holidays have only 2 LC slots, like weekends. If DC1 is showing an assignment on a holiday, the slot structure is wrong.

---

## 13. Unfilled Slots

**Where to look:** Flags and Violations — look for **UNFILLED_SLOT** flags. Also scan the Full Schedule for "---UNFILLED---" entries.

**What to check:** Every unfilled slot should be investigated. Look at the date — who was working that day? Was there genuinely no one available, or did the engine skip someone it shouldn't have? Check if the unfilled day falls in a week where many providers had misses (suggesting a supply problem vs. an engine problem).

**Why it matters:** An unfilled slot means no one is covering long call that shift. This needs to be manually resolved.

---

## 14. Spot-Check a Few Providers End-to-End

**Where to look:** Pick 3–5 providers from different parts of the alphabet and varying week counts (one with 3–4 weeks, one with 7–8, one with 10+).

**What to check for each:**
1. Open their Provider Detail section
2. Count green (LC) rows — does it match their Total LC in the summary?
3. Count red (NO LC) weeks — does it match their Missed count?
4. Is there a green row in every non-red, non-grey, non-standalone week?
5. Is the LC on a day they're actually working (green row should show a service)?
6. If they have a double, is it split weekday/weekend?
7. Do their weekend LCs appear in the Weekend Pivot?

**Why it matters:** The summary stats are computed from the assignments, so they should be consistent with the detail view. Spot-checking catches rendering bugs or logic errors that aggregate stats might hide.

---

## Quick-Pass Workflow

For a fast review of a single report, check these in order (10 minutes):

1. **Provider Summary** — sort by Missed desc, scan for anyone > 1
2. **Provider Summary** — sort by Doubles desc, click into any doubles to verify weekday+weekend split
3. **Weekend Pivot** — check for anyone with 2+ weekend LCs
4. **Teaching vs DC** — confirm DC→Teaching = 0
5. **Flags** — scan UNFILLED_SLOT and CONSEC_NO_LC counts
6. **Spot-check 2 providers** — one high-week, one low-week, verify detail matches summary

For comparing multiple variations, run the quick-pass on each and pick the one with the fewest flags and best balance.
