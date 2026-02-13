# Block Scheduling — Process Definition & Requirements

This document captures everything known about the block scheduling process as of February 2026. It is the working reference for building the automated block scheduler. Rules, provider lists, and staffing numbers are **block-specific configuration** — they change every block based on staffing changes, availability, and site director decisions.

---

## 1. Overview

1.1. The hospitalist schedule is built in 4-month blocks, 3 blocks per year. The current block (Block 3 of 2025–2026) runs **March 2 – June 28, 2026**. The year started June 30, 2025.

1.2. The scheduling process:
  1.2.1. Providers submit availability requests — available/unavailable days for the upcoming block (the individual schedule JSON files)
  1.2.2. Schedulers build the block schedule by hand — assigning providers to sites and services day by day, using the Schedule Book Excel as their workspace
  1.2.3. The finished schedule is published to Amion
  1.2.4. Long call assignment is done on top of the published schedule

1.3. The block scheduler will automate step 1.2.2.

---

## 2. Organizational Structure

### 2.1. Schedulers

2.1.1. The work is divided among schedulers, each managing a subset of providers. Scheduler codes from the Schedule Book:
  - **MM** — Melissa Mangold (Cooper site director)
  - **PS** — (nights/nocturnists)
  - **CD** — (Vineland/Inspira sites)
  - **ZF** — (Virtua sites)
  - **AM** — (Cape/Atlantic region)
  - **IR** — (appears empty in current block)
  - **NT** — (nocturnists, may be embedded in other tabs)

2.1.2. Some providers span multiple schedulers (e.g., "PS/CD/NT" means the provider's time is split across those schedulers' domains).

### 2.2. Locations

2.2.1. This is a multi-site hospital network, not a single hospital. Each site has different staffing needs.

2.2.2. Daytime sites:
  - Cooper Camden (largest — fill last)
  - Vineland (Inspira)
  - Elmer (Inspira)
  - Mullica Hill (Inspira)
  - Cape May
  - Mannington
  - Virtua (Willingboro, Mt Holly, Marlton, Voorhees)

2.2.3. Nighttime coverage is separate from daytime and counted by individual days (no week/weekend stretch requirement).

---

## 3. Staffing Requirements by Site

### 3.1. Cooper Camden

3.1.1. **Weekdays (Mon–Fri): 26 doctors**
  - 8 teaching services: HA, HB, HC, HD, HE, HF, HG, HM (Family Medicine)
  - 13 direct care services: H1–H17 (sometimes extra slots used for moonlighters if census is high)
  - 1 MAH
  - 1 Medicine Consult
  - 1 UM
  - 1 SAH
  - 1 TAH (Teaching Admitting Hospitalist)

3.1.2. **Weekends (Sat–Sun): 19 doctors**
  - Teaching services + direct care services + MAH only
  - NO UM, SAH, or TAH on weekends

3.1.3. **Cooper site directors:** Melissa Mangold, Tyler McMillian, Katie Haroldson, Cynthia Glickman, Michael Gross

### 3.2. Mullica Hill

3.2.1. **Weekdays: 11 daytime doctors**
  - 5 teaching: Med 1, Med 2, Med 3, Med 4, FM (Family Medicine)
  - 5 non-teaching: A, B, C, D, W (W is an extra/overflow service for high census — not always staffed, but non-moonlighting shifts on it count toward a provider's weeks)
  - 1 MAH

3.2.2. **Weekends: 10 daytime doctors** (ideally 11 with an FFS doc)
  - 5 teaching
  - 4 non-teaching
  - 1 MAH

3.2.3. **PA (Physician Assistant service):** 1 per day Mon–Fri only (weekend cross-covered by Vineland)

3.2.4. **Nights:** 3 per night (Noc 1, Noc 2, Noc 3), 7 days/week

3.2.5. **MH site directors:** Oberdorf, Olayemi, Gambale

#### 3.2.6. PA Rotation (special limited pool)
  - Kambiz Butt — 19 weeks PA/year. Never MAH. Almost always non-teaching for other weeks. Unusual contract.
  - Eric Oberdorf — 12 weeks PA/year
  - Shiraz Siddiqui — 6 weeks PA/year (alternates with Vineland PA)
  - Bibbin — 4 weeks PA/year (informatics/EPIC time)
  - Nicole — remaining PA weeks (~11–12/year)

#### 3.2.7. Teaching Restrictions
  3.2.7.1. **Never on teaching (Med 1–4) unless true staffing emergency:** Miguel Deleon, Laurie Charles, Mike Malone, Chris Fernandez, Paul Stone, Kambiz Butt, Sadia Nawshin, Sara Ausaf, Anamta Contractor, Sunaina Ahmed

  3.2.7.2. **Paul Stone:** non-teaching only, never MAH

#### 3.2.8. Family Medicine — Dedicated Pool
  - Kristen Marotta, Sean Matchett, Kath Heaton (plus site directors if conflicts)

#### 3.2.9. Teaching (Med 1–4) — Preferred Pool
  3.2.9.1. **Always teaching:** Gabby Hassinger, Ian Gleaner, Andre Gabriel, Matt Springer, Angela Zheng

  3.2.9.2. **Teaching or non-teaching:** Chris Bazergui, Andrew Ajemian, Wei Chen, Bruce Smith, Maher Al-Safadi, Brad Bender

#### 3.2.10. Mixture (teaching or non-teaching)
  - Parita Patel, Emma White, site directors (Oberdorf, Olayemi, Gambale), Victor Pomary, Emily Cunnings, Joe Capalbo, Ryan Li, Jasper Mok, Vincent Mairano, Tudor Vlad, all new providers (Alam, Amanat, Haider, Rameeza, Shor, Yanhong Zhang, Daroshefski, etc.)

#### 3.2.11. MAH Special Case
  - Ken So (per diem) — always gets 6–8 MAH shifts/month, but need to ask his availability first.

### 3.3. Vineland

3.3.1. **Weekdays: 11 doctors clinical + 1 UM**
  - Teaching: Med 1, Med 2, Med 3, Med 4
  - Non-teaching: A, B, C, D, CDU, Z
  - 1 MAH
  - 1 UM (only certain people can do this)

3.3.2. **Weekend staffing: ~11 doctors** (needs final confirmation)

### 3.4. Elmer

3.4.1. 1 doctor per day

### 3.5. Cape May

3.5.1. **Weekdays: 6 doctors clinical + 1 swing shift + 1 UM**
  - UM: only certain people can do this

3.5.2. **Weekend staffing:** TBD

### 3.6. Mannington

3.6.1. 1 doctor per day

### 3.7. Virtua

3.7.1. **6 doctors per day total:**
  - 1 Virtua Willingboro
  - 2 Virtua Mt Holly
  - 1 Virtua Marlton
  - 2 Virtua Voorhees

### 3.8. Night Shifts (all sites)

3.8.1. Nightly staffing needs:

| Location | Nightly Need |
|----------|-------------|
| Cooper | 2 |
| Virtua Voorhees | 1 |
| Virtua Willingboro | 1 |
| Mannington | 1 |
| Mullica Hill | 3 |
| Vineland | 2 |
| Elmer | 1 |
| Cape | 1 |

3.8.2. Night shifts are counted by **individual days** — no requirement for full week/weekend stretches.

---

## 4. Scheduling Rules

### 4.1. Block Structure

4.1.1. 3 blocks per year, each ~4 months

4.1.2. A **week** = Monday–Friday (always 5 days)

4.1.3. A **weekend** = Saturday–Sunday (always 2 days)

4.1.4. Current block (Block 3): March 2 – June 28, 2026

4.1.5. Year started June 30, 2025

### 4.2. Annual Balance Across Blocks

4.2.1. Each provider has annual targets for weeks and weekends (from Master File: EffectiveWeeksCurrent, EffectiveWECurrent)

4.2.2. Divide as equally as possible across 3 blocks. Example: 26 weeks/year → 9 + 9 + 8 across three blocks.

4.2.3. Vary which block gets the short count across the provider group (don't short the same block for everyone)

4.2.4. Need Block 1 & 2 actuals to determine Block 3 targets

4.2.5. When counting previous blocks: weekday shifts ÷ 5 = weeks, weekend shifts ÷ 2 = weekends

4.2.6. Moonlighting does NOT count toward totals

### 4.3. Stretch Rules

4.3.1. Try to keep weeks and weekends together in one 7-day stretch

4.3.2. Max 7 consecutive days preferred; longer stretches allowed if forced by time-off requests

4.3.3. Space stretches evenly throughout the block — don't bunch them up

4.3.4. A provider may start on a weekend and continue into the week, or start Monday through the next weekend

4.3.5. Splitting a week at one location and weekend at another should be rare

### 4.4. Location Assignment

4.4.1. Most providers work at multiple sites based on a percentage split per provider

4.4.2. Percentage can flex ±5–10%

4.4.3. Daytime providers stay at **one location for the entire week+weekend**

4.4.4. **Fill non-Cooper sites first**, then plug remaining providers into Cooper Camden

4.4.5. Cooper absorbs the leftover capacity

4.4.6. Highlight how many holes per day per site in the output

4.4.7. Can leave 1 hole at Mullica Hill and Vineland; all other sites must be fully filled

### 4.5. Hybrid (Day/Night) Providers

4.5.1. Need **2–3 days off** between switching from days→nights or nights→days

### 4.6. Holidays

4.6.1. 6 holidays per year: New Year's Day, Memorial Day, July 4th, Labor Day, Thanksgiving, Christmas

4.6.2. Holiday requirements by FTE:
  - ≥0.76 FTE → 3 holidays
  - 0.5–0.75 FTE → 2 holidays
  - <0.5 FTE → 1 holiday
  - Exception: site directors get only 2 holidays regardless of FTE

4.6.3. Average person should work either New Year's OR Christmas as one of their holidays.

4.6.4. Providers submit 2 preferred holidays (from the Holidays tab in the Schedule Book).

### 4.7. UM/SAH

4.7.1. Non-clinical services but still count as a working week (unless moonlighting)

4.7.2. Not staffed on weekends at Cooper

### 4.8. Site Director Rules

4.8.1. "Site Director" alone on the schedule does NOT count as a working week or weekend

4.8.2. "Site Director + assigned service" DOES count

### 4.9. Special Provider Rules

4.9.1. **Glenn Newell:** consults only, Mon–Thu only, Fridays blank

4.9.2. **Katie Haroldson & Tyler McMillian:** never scheduled on the same weeks/weekends

4.9.3. **Do not schedule** (self-schedulers, added later): Rachel Nash, Alka Farmer, Samer Badr, Deepa Velayadikot, Sebastien Rachoin, Lisa Cerceo, Snehal Ghandi, Alisa Peet

---

## 5. Input Data Sources

### 5.1. Provider Availability Requests (JSON files)

5.1.1. 253 providers in the system, ~127 with active availability data

5.1.2. Format: `{"name": "Last, First", "month": 3, "year": 2026, "days": [{"date": "YYYY-MM-DD", "status": "available|unavailable|blank"}]}`

5.1.3. 4 files per provider (one per month in the block)

5.1.4. "blank" = provider didn't submit requests or isn't in the active scheduling pool

5.1.5. "available"/"unavailable" = their stated availability for each day

### 5.2. Schedule Book Excel (18 tabs)

#### 5.2.1. Provider master data
  - **Master File** — Full roster with FTE, shift type (Days/Nights/Hybrid), scheduler codes, employment dates, annualized shift targets, special notes
  - **Lynne- Breakdown** — Monthly weekday shift counts per provider across the full year (for tracking/balancing)
  - **Audit** — Annual totals: weekdays, weekends, nights, swing shifts (Jul 2025 – Jun 2026)
  - **Admin Audit** — Half-year audit split (mostly empty, filled as year progresses)

#### 5.2.2. Scheduler workspace tabs (one per scheduler)
  - **MM, PS, CD, ZF, AM, IR** — Each has the scheduler's provider subset with a day-by-day grid for the block. This is where assignments are built.

#### 5.2.3. Location schedules (completed output)
  - **VEB** — Vineland/Elmer/Bridgeton schedule grid
  - **Cape** — Cape May schedule grid
  - **Mannington** — Simple date/provider list

#### 5.2.4. Reference/tracking
  - **Numbers** — Daily staffing counts needed by location (weekday/weekend). This is the demand model.
  - **Call out** — Sick day/call-out log
  - **FMLA** — Active FMLA leaves with dates
  - **Holidays** — Provider holiday preferences (2 choices each)
  - **Per Diem** — Per diem/contract provider assignments

---

## 6. Key Modeling Insights

6.1. All provider-to-service eligibility rules, teaching restrictions, location percentages, PA rotations, and special constraints are **block-specific configuration that changes every block**. The engine must accept these as configurable input, not hardcoded logic. The structure of the rules stays the same; the specific values change based on staffing, availability, and site director decisions.

6.2. **Service names are site-specific.** Cooper uses HA–HG/HM for teaching and H1–H17 for direct care. Mullica Hill uses Med 1–4/FM for teaching and A–D/W for non-teaching. Vineland uses Med 1–4 for teaching and A–D/CDU/Z for non-teaching. Each site defines its own service taxonomy; the engine should treat service names as opaque labels within each site's configuration.

---

## 7. Open Questions

7.1. **Weekend staffing numbers** for Elmer, Cape, Mannington, Virtua — Vineland is ~11 (needs final confirmation), but still need weekend counts for the other non-Cooper sites

7.2. **Provider-to-location mapping** — the Master File has some notes but no clean structured source for which providers can work which locations

7.3. **Block 1 & 2 actuals** — needed to compute Block 3 targets. Source: Lynne Breakdown tab? Audit tab? Or do we need historical Amion data?

7.4. **Provider-to-service rules for all sites** — have detailed rules for Mullica Hill (see 3.2.7–3.2.11). All other sites (Cooper, Vineland, Cape, Virtua) have similar provider-to-service rules that need to be captured

7.5. **Scheduling order within non-Cooper sites** — fill non-Cooper first (see 4.4.4), but is there a priority among them?

7.6. **Night scheduling process** — handled same way or separately? The scheduler tabs have night columns. PS scheduler handles nights.

7.7. **Swing shift details** — Cape has a swing shift slot (see 3.5.1). Other sites? How do swing shifts interact with day/night scheduling?

7.8. **How do scheduler boundaries work?** — if a provider spans MM and CD, who "owns" their schedule? Or does the engine just see all providers as one pool?
