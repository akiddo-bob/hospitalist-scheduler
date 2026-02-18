# Block Scheduling Rules — Source of Truth

> **This is the single authoritative document for all block scheduling rules.**
> All rules, inputs, and process definitions live here. If it's not in this
> document, it's not a rule. When rules change, this document is updated first,
> then the code is updated to match.

**Goal:** The engine assigns providers to **weeks** and **weekends** at **sites**
for a 4-month block. It does NOT assign specific services within a site (teaching
vs non-teaching, PA rotation, etc.) — that is deferred to a later phase.

**Current block:** Cycle 25-26, Block 3 (March 2 – June 28, 2026)

---

## Inputs

The engine reads from two sources: a Google Sheet (provider data, tags, site
demand) and individual schedule JSON files (provider availability). Together
these provide everything needed to produce a block schedule.

### Input 1: Google Sheet — Providers Tab

**Source:** Google Sheet `1dbHUkE-pLtQJK02ig2EbU2eny33N60GQUvk4muiXW5M`,
tab "Providers". Fetched via CSV export (no API key needed).

Each row is one provider. The engine reads every field listed below.

#### Identity & Role
| Field | Type | Example | How the engine uses it |
|-------|------|---------|----------------------|
| `provider_name` | text | "ABRAHAM, ANEY" | Primary key — matches to availability JSONs and tags |
| `shift_type` | text | "Days", "Nights", "Hybrid" | Determines eligibility — pure "Nights" with no remaining weeks/weekends are excluded |
| `fte` | decimal | 0.9, 1.0 | Determines holiday work requirements (see Section 4.2) |
| `scheduler` | text | "MM", "PS/CD/NT", "AF" | Identifies which coordinator manages them; self-schedulers (own initials) should be excluded !!!careful here - we don't want to assume mathing initials is a self schedule. let's rely on the provider tags to tell us who to 'do not schedule' |

#### Annual Obligations & Remaining
| Field | Type | Example | How the engine uses it |
|-------|------|---------|----------------------|
| `annual_weeks` | number | 24, 17 | Total weeks this provider owes per year — used for fair-share calculation (annual ÷ 3) !!!this is a target. |
| `annual_weekends` | number | 23, 16 | Total weekends owed per year !!!this should be fair-share calculated too for a target|
| `annual_nights` | number | 0, 12 | Not used by this engine (night scheduling out of scope) |
| `prior_weeks_worked` | number | 10.4 | Informational — engine uses `weeks_remaining` directly |
| `prior_weekends_worked` | number | 8.0 | Informational — engine uses `weekends_remaining` directly |
| `prior_nights_worked` | number | 0 | Not used by this engine |
| `weeks_remaining` | number | 8.6 | **Primary input** — how many weeks this provider still owes for the year. Hard cap on assignments. |
| `weekends_remaining` | number | 7.0 | **Primary input** — how many weekends still owed. Hard cap on assignments. |
| `nights_remaining` | number | 0 | Not used by this engine |

#### Site Allocation Percentages
| Field | Type | Example | How the engine uses it |
|-------|------|---------|----------------------|
| `pct_cooper` | decimal | 1.0, 0.5 | Fraction of time at Cooper. 0 = never assign to Cooper. |
| `pct_inspira_veb` | decimal | 0.5, 0 | Fraction at Vineland/Elmer. Covers BOTH sites. |
| `pct_inspira_mhw` | decimal | 0.5, 0 | Fraction at Mullica Hill |
| `pct_mannington` | decimal | 0, 1.0 | Fraction at Mannington |
| `pct_virtua` | decimal | 0, 1.0 | Fraction at Virtua (all 4 sub-sites) |
| `pct_cape` | decimal | 0, 1.0 | Fraction at Cape May |

**How percentages map to sites:**

| Percentage Field | Sites it unlocks |
|-----------------|-----------------|
| `pct_cooper` | Cooper |
| `pct_inspira_veb` | Vineland, Elmer |
| `pct_inspira_mhw` | Mullica Hill |
| `pct_mannington` | Mannington |
| `pct_virtua` | Virtua Voorhees, Virtua Marlton, Virtua Willingboro, Virtua Mt Holly |
| `pct_cape` | Cape |

If a percentage is 0, the provider is never assigned to those sites.
If > 0, the provider is eligible and the value guides proportional distribution.

Note: `pct_inspira_veb` covers both Vineland and Elmer as a group. A provider
can be further restricted from one of those sites via the `no_elmer` or
`no_vineland` tags (see Input 2).

#### Holiday Preferences
| Field | Type | Example | How the engine uses it |
|-------|------|---------|----------------------|
| `holiday_1` | text | "New Year's Day", "" | First holiday preference — provider should not be scheduled the week containing this holiday |
| `holiday_2` | text | "Thanksgiving", "" | Second holiday preference — same treatment |

Valid holiday names: "New Year's Day", "Memorial Day", "4th of July",
"Labor Day", "Thanksgiving", "Christmas Day". Empty means no preference.

For Block 3, only **Memorial Day (May 25, 2026)** falls in range. !!!yes - but you also need to evaluate the block 1 and block 2 schedules to determine if providers have already worked their required number of holidays. if they need to work a holiday still and 5/25 is the only one left they need to be scheduled. this might raise a conflict with their request.

### Input 2: Google Sheet — Provider Tags Tab

**Source:** Same Google Sheet, tab "Provider Tags".

A flexible key-value store for per-provider rules and restrictions. One row per
tag (a provider can have multiple tags).

| Field | Type | Example |
|-------|------|---------|
| `provider_name` | text | "HENRIKSEN, GABRIELLE" |
| `tag` | text | "no_elmer" |
| `rule` | text | "Vineland only. No Elmer" |

#### Tag types observed in the data

| Tag | Meaning | How the engine uses it |
|-----|---------|----------------------|
| `do_not_schedule` | Provider excluded entirely | Hard exclude — skip this provider completely |
| `no_elmer` | Cannot work at Elmer | Remove Elmer from eligible sites |
| `no_vineland` | Cannot work at Vineland | Remove Vineland from eligible sites |
| `location_restriction` | Site/service constraint | Read `rule` field for details; may need manual interpretation |
| `service_restriction` | Service constraint | Out of scope for current engine (service assignment deferred) |
| `swing_shift` | Days & Swing split | Read `rule` field for shift breakdown details |
| `pa_rotation` | Clinical + PA week split | Read `rule` field for specific week counts |
| `scheduling_priority` | Special scheduling note | Read `rule` field for details |
| `night_constraint` | Night shift placement rule | Out of scope for current engine |
| `note` | General note | Informational — review `rule` field |

> **QUESTION FOR SCHEDULER:** Some tags like `location_restriction`,
> `scheduling_priority`, and `swing_shift` have free-text `rule` fields that
> need human interpretation. Should these be broken down into more specific
> tags the engine can act on? Or should the engine ignore them and leave
> those providers for manual adjustment? !!!you should create a list of any tags you can't understand and we will provide the context needed.

### Input 3: Google Sheet — Sites Tab

**Source:** Same Google Sheet, tab "Sites".

Defines how many providers are needed at each site for each day type.

| Field | Type | Example |
|-------|------|---------|
| `site` | text | "Cooper" |
| `day_type` | text | "weekday", "weekend", "swing" |
| `providers_needed` | integer | 26 |

#### Current site demand (from Google Sheet)

| Site | Weekday | Weekend | Swing |
|------|:---:|:---:|:---:|
| Cooper | 26 | 19 | |
| Mullica Hill | 11 | 10 | |
| Vineland | 11 | 11 | |
| Elmer | 1 | 1 | |
| Cape | 7 | 6 | 1 |
| Mannington | 1 | 1 | |
| Virtua Voorhees | 2 | 2 | |
| Virtua Marlton | 1 | 1 | |
| Virtua Willingboro | 1 | 1 | |
| Virtua Mt Holly | 2 | 2 | |
| **TOTAL** | **63** | **54** | **1** |

> **QUESTION FOR SCHEDULER:** The Sites tab includes a "swing" day type for
> Cape (1 provider needed). Should the engine schedule swing shifts, or is
> that handled separately?

### Input 4: Provider Availability (Individual Schedule JSONs)

**Source:** `input/individualSchedules/` directory. ~1,012 JSON files for Block 3.

Each provider has up to 4 files (one per month: March through June 2026).

**File naming:** `schedule_{LastName}_{FirstName}_{MM}_{YYYY}.json`

Example: `schedule_Abraham_Aney_03_2026.json`

**JSON structure:**
```json
{
    "name": "Abraham, Aney",
    "month": 3,
    "year": 2026,
    "days": [
        {"date": "2026-03-01", "status": "blank"},
        {"date": "2026-03-02", "status": "available"},
        {"date": "2026-03-03", "status": "unavailable"},
        ...
    ]
}
```

**Status values and how the engine uses them:**

| Status | Meaning | Engine behavior |
|--------|---------|-----------------|
| `"available"` | Provider indicated they CAN work | Eligible for scheduling |
| `"unavailable"` | Provider indicated they CANNOT work | **Never schedule** — hard constraint |
| `"blank"` | Provider did not submit a request for this day | Treated as **available** |

**Name matching:** Provider names differ across data sources (Google Sheet,
availability JSONs, Amion HTML schedules). The engine uses a system-wide
name alias map to resolve mismatches. See **Name Matching & Aliases** section
below.

**Coverage:** Not all providers have availability files. Providers with no JSON
files are treated as fully available (all days available). However, if a
provider has no file AND is not tagged `do_not_schedule`, this may indicate
a data issue worth flagging.

---

## Name Matching & Aliases

Provider names are not consistent across data sources. The same person may
appear differently in the Google Sheet, the availability JSONs, and the
Amion HTML schedules. The engine must use a **system-wide** name alias map
that applies everywhere names are matched — not just for one input source.

**Normalization rules (apply before matching):**
- Uppercase all names
- Strip credential suffixes: MD, DO, PA, NP, PA-C, MBBS
- Strip periods, collapse whitespace
- Strip trailing `**` markers

**After normalization, apply the alias map.** The canonical name (left column)
is the Google Sheet `provider_name`. All other systems map TO this name.

#### Spelling / Format Aliases
| Canonical (Google Sheet) | Variant (JSON / Amion) | Issue |
|--------------------------|----------------------|-------|
| CERCEO, ELIZABETH | CERCEO, LISA | Different first name |
| DHILLION, JASJIT | DHILLON, JASJIT | Typo (double L) |
| DUNN JR, ERNEST CHARLES | DUNN, E CHARLES | Suffix/format |
| GORDAN, SABRINA | GORDON, SABRINA | Typo (missing O) |
| OBERDORF, W. ERIC | OBERDORF, ERIC | Middle initial stripped |
| ORATE-DIMAPILIS, CHRISTINA | DIMAPILIS, CHRISTINA | Hyphenated last name |
| RACHOIN, JEAN-SEBASTIEN | RACHOIN, SEBASTIEN | Shortened first name |
| TROYANOVICH, ESTEBAN | TROYANOVICH, STEVE | Esteban = Steve |
| TUDOR, VLAD | VLAD, TUDOR | First/last reversed |
| VIJAYKUMAR, ASHWIN | VIJAYAKUMAR, ASHVIN | Spelling variants |

#### Nickname Aliases
| Canonical (Google Sheet) | Variant (JSON / Amion) | Issue |
|--------------------------|----------------------|-------|
| AHMED, SANAINA | AHMED, SUNAINA | Spelling variant |
| AMANAT, AMMAAR | AMANAT, AMMAAR ALI | Missing middle name |
| HAROLDSON, KATHRYN | HAROLDSON, KATIE | Nickname |
| LEE, SUSAN | LEE, SUSAN SE-EUN | Missing middle name |
| LOGUE, RAYMOND | LOGUE, RAY | Nickname |
| MALONE, MICHAEL | MALONE, MIKE | Nickname |
| PEREZ, CHRISTOPHER | PEREZ, CHRIS | Nickname |
| RUGGERO, JAMES | RUGGERO, JAMES GABRIEL | Missing middle name |
| TANIOUS, ASHRAF | TANIOUS, ANTHONY | Different first name |
| THAKUR, NAKITA | THAKUR, NIKITA | Spelling variant |
| TRONGONE, JENNIFER | TRONGONE, JENNA | Nickname |

#### Data Quality Notes
- **SAPASETTY, ADITYA** — JSON has a stray space: `"Sapasetty , Aditya"`.
  Normalizer must collapse whitespace around commas.
- **SHAIKH, SAMANA** — JSON includes "MD" suffix. Stripped by normalizer.
- **VARNER, PHILIP** — JSON includes "DO" suffix. Stripped by normalizer.

#### Providers Previously Missing — Now Resolved
The following 6 providers had no availability JSON files. JSONs were fetched
from Amion on Feb 17, 2026 and added to `input/individualSchedules/`:

| Google Sheet Name | JSON Name | Status |
|-------------------|-----------|--------|
| DHILLION, JASJIT | Dhillon, Jasjit | All months unavailable |
| GUMMADI, VEDAM | Gummadi, Vedam | All months blank (no submissions) |
| LEE, GRACE | Lee, Grace | All months blank (FMLA) |
| PATEL, RITESH | Patel, Ritesh | All months blank (no submissions) |
| RASHEED, SAMMAR | Rasheed, Sammar | Mixed available/unavailable |
| SHAH, HELY | Shah, Hely | Mixed available/unavailable |

**Note:** DHILLON uses the Amion spelling (single L), not the Google Sheet
spelling (DHILLION, double L). This alias is already in the Spelling / Format
table above.

---

## How the Engine Works (Process)

The engine mirrors what a manual scheduler does, in a defined sequence of phases.

### Phase 1: Setup
- Load all inputs (Providers, Tags, Sites, Availability)
- Exclude ineligible providers:
  - Tagged `do_not_schedule`
  - Self-schedulers
  - Pure nocturnists (`shift_type = "Nights"` with no remaining weeks/weekends)
  - Providers with `weeks_remaining <= 0` AND `weekends_remaining <= 0`
- Calculate fair-share targets: `ceil(annual_weeks / 3)` and `ceil(annual_weekends / 3)`
- Identify behind-pace providers (remaining > fair share)
- Build the list of week and weekend periods for the block (count varies by block)

### Phase 2: First Pass — Fill Non-Cooper Sites
- Process non-Cooper sites first (Mullica Hill, Vineland, Virtua, Cape, Mannington, Elmer)
- For each period (week or weekend), for each site:
  - Find eligible providers (available, has capacity, site % > 0, not over consecutive limit)
  - Score candidates (spacing, site allocation match, stretch pairing)
  - Place the best candidate
- Cap each provider at their fair-share target during this pass
- Pair weekends with the same site as the adjacent weekday period

### Phase 3: Fill Cooper
- Cooper is processed after all other sites
- Same scoring and placement logic
- Cooper absorbs remaining provider capacity
- Gaps at Cooper are expected — filled later by moonlighters or per diem

### Phase 4: Second Pass — Behind-Pace Catch-Up
- Lift the fair-share cap
- Re-process all sites, filling remaining gaps
- Behind-pace providers (who worked less in Blocks 1 & 2) now get extra weeks
- This ensures they catch up without having crowded out on-pace providers in earlier passes

### Phase 5: Forced Fill / Rebalancing
- Fill remaining gaps by relaxing soft constraints progressively:
  1. Allow 3 consecutive weeks (normally max 2)
  2. Allow providers to exceed their target site allocation slightly
- Track all constraint relaxations in the output for manual review

### Phase 6: Output & Review
- Generate multiple schedule variations (different random seeds)
- Produce reports showing: assignments, gaps, stretch overrides, utilization balance
- **Highlight how many holes per day per site** — this is critical for the
  scheduler to see where manual intervention is needed
- Manual scheduler reviews and picks the best variation or makes adjustments

---

## Section 1: Facts (Structural truths)

These are facts about the organization that the engine must understand.

### 1.1 Cycles and Blocks

The scheduling year runs from late June to late June (NOT calendar year). Each
year is a **cycle** identified by its two calendar years. Each cycle has 3 blocks.

**Cycle naming:** `YY-YY` — e.g., "25-26" for the cycle starting June 2025.

**Block boundaries within a cycle:**
- **Block 1:** Late June → early November (~18 weeks)
- **Block 2:** Early November → early March (~17 weeks)
- **Block 3:** Early March → late June (~17 weeks)

Exact dates shift slightly each year to land on Monday starts and Sunday ends.

#### Cycle 25-26 (current)
| Block | Start | End | Weeks |
|-------|-------|-----|:-----:|
| Block 1 | June 30, 2025 (Mon) | November 2, 2025 (Sun) | 18 |
| Block 2 | November 3, 2025 (Mon) | March 1, 2026 (Sun) | 17 |
| Block 3 | March 2, 2026 (Mon) | June 28, 2026 (Sun) | 17 |

#### Future cycles (dates TBD)
| Cycle | Approximate Start | Approximate End |
|-------|-------------------|-----------------|
| 26-27 | Late June 2026 | Late June 2027 |
| 27-28 | Late June 2027 | Late June 2028 |

The engine should accept cycle and block as configuration inputs rather than
hardcoding dates. Each run targets one specific block within one cycle.

> **QUESTION FOR SCHEDULER:** How are the exact block boundary dates determined
> each year? Is it always "last Monday in June" for the cycle start? Or is
> there a different convention?

### 1.2 Scheduling Unit
- **Week** = Monday through Friday (5 weekdays)
- **Weekend** = Saturday and Sunday (2 days)
- A provider stays at **one site** for the entire week + adjacent weekend

### 1.3 Service Classification (for prior actuals)

When calculating how many weeks/weekends/nights a provider has already worked
in Blocks 1 and 2, the engine must classify every Amion service as either
**included** (counts toward worked totals) or **excluded** (ignored).

#### Exclusion Rules (in priority order)

Any service matching these patterns is **excluded** from prior work counts:

| Rule | Pattern | Examples |
|------|---------|----------|
| APP | Service name contains `APP`, `APN`, or `PA` (as role) | APP Admitter 1, H14 APP, Mullica Hill APN 1, Cape PA |
| Night Coverage | Name starts with `Night Coverage` or `NIGHT COVERAGE` | NIGHT COVERAGE 1 (MAH H9…), Night Coverage 2 5p-5a H4 |
| Resident | Name contains `Resident` | FM Resident Admitter, Night- Resident Direct Care Admitter |
| Hospitalist Fellow | Name contains `Fellow` | Hospitalist Fellow |
| Behavioral Med | Name contains `Behavioral` | Behavioral Medicine - Monday-Friday, 8a-4p |
| Site Director | Name contains `Site Director` | Cooper Site Director, Mullica Hill Site Director |
| Admin | Name contains `Admin` | Cooper Morning Admin, Mullica Hill Admin Shift 1 |
| Hospice | Name contains `Hospice` | Hospice on call - GIP |
| Kessler Rehab | Name contains `Kessler` | Kessler Rehab (Skobac) |
| Holy Redeemer | Name contains `Holy Redeemer` | (none in current data — retired service) |
| Cape RMD | Name contains `Cape RMD` | Cape RMD On-Call |
| Long Call | Name contains `Long Call` | Long Call H1 7a-8a, Teaching Long Call, Mullica Hill Long Call |
| Direct Care Long Call | Name starts with `Direct Care Long Call` | Direct Care Long Call 1 AM, Direct Care Long Call 2 PM |
| Virtua Coverage | Name contains `Virtua` AND `Coverage` | Virtua Marlton PM Coverage, Virtua Mt Holly AM Coverage |
| UM | Name is exactly `UM` (standalone) | UM |
| Consults | Name contains `Consult` (except Hospital Medicine Consults) | Night Direct Care Admitter 2 (Consult), Woodbury Consult Physician |

#### Full Excluded Service List

```
APP Admitter 1 (ED and PACU ICU downgrades)
APP Admitter 2
APP Admitter 3
APP Admitting
APP Admitting SWING
Behavioral Medicine - Monday-Friday, 8a-4p (108-0597)
Bridgeton APP
Cape APP Cross Coverage
Cape APP- Night Cross Coverage
Cape Admin Staff
Cape Day APP 1
Cape LTC-SAR  On Call APP overnight
Cape LTC-SAR Day APP
Cape LTC-SAR On call APP
Cape PA
Cape RMD On-Call
Cape Site Director On-Call
Cape Swing APP
Cooper APP Lead
Cooper Morning Admin (Weekend-Holiday)
Cooper Morning Admin- Weekday
Cooper Site Director
Direct Care Admissions APP
Direct Care Long Call 1 AM (Morning Cross Over)
Direct Care Long Call 1 PM
Direct Care Long Call 2 PM
Elmer APP
FM Resident Admitter
H10 APP- CDU
H11 APP - CDU
H12 APP- Pav 8 & Pav 9
H13 APN
H14 APP
H15 APP
H2 APP
H2 APP 2
H4 APP
H6 APP
H7 APP
H8 APP- Pav 6 & EXAU
H9 APP- CDU
Hospice on call - GIP
Hospital Medicine Consults APP
Hospitalist Fellow
Inpsira- Mannington APN
Inspira- Mannington APN Day Shift
Kessler Rehab (Skobac)
Long Call H1 7a-8a
Long Call H10 7a-8a
Long Call H11 7a-8a
Long Call H12 7a-8a
Long Call H13 7a-8a
Long Call H14 7a-8a
Long Call H15 7a-8a
Long Call H16 7a-8a
Long Call H17 7a-8a
Long Call H18 7a-8a
Long Call H2 7a-8a
Long Call H3 7a-8a
Long Call H4 7a-8a
Long Call H5 7a-8a
Long Call H6 7a-8a
Long Call H7 7a-8a
Long Call H8 7a-8a
Long Call H9 7a-8a
Moonlighting Day Resident Admitter 1
Moonlighting Day Resident Admitter 2
Mullica Hill APN 1 (Team Y)
Mullica Hill APN 2 (Extra)
Mullica Hill Admin Shift 1
Mullica Hill Long Call
Mullica Hill Night Shift APP
Mullica Hill Night Shift Orienting APP
Mullica Hill Site Director
NIGHT COVERAGE 1  (MAH H9, H10, H11, H16)
NIGHT COVERAGE 2  (H2, H4, H5,  H7)
NIGHT COVERAGE 2  (H2, H4, H5, H7)
NIGHT COVERAGE 3  (H1, H6, H8, H15)
NIGHT COVERAGE 4 (CADV, H3, H12, H13, H14, H17, on-going consults)
Night APP ADMIT 1
Night Coverage 1 5A-7A H11
Night Coverage 1 5A-7A H16
Night Coverage 1 5a-7a H10
Night Coverage 1 5a-7a H9
Night Coverage 1 5a-7a MAH
Night Coverage 1 5p-5a H10
Night Coverage 1 5p-5a H11
Night Coverage 1 5p-5a H16
Night Coverage 1 5p-5a H9
Night Coverage 1 5p-5a MAH
Night Coverage 2 5a-7a H2
Night Coverage 2 5a-7a H4
Night Coverage 2 5a-7a H5
Night Coverage 2 5a-7a H7
Night Coverage 2 5p-5a H2
Night Coverage 2 5p-5a H4
Night Coverage 2 5p-5a H5
Night Coverage 2 5p-5a H7
Night Coverage 3 5p-7p H1
Night Coverage 3 5p-7p H15
Night Coverage 3 5p-7p H6
Night Coverage 3 5p-7p H8
Night Coverage 3 7P-7A H1
Night Coverage 3 7P-7A H15
Night Coverage 3 7P-7A H6
Night Coverage 3 7P-7A H8
Night Coverage 4 5p-7p H12
Night Coverage 4 5p-7p H13
Night Coverage 4 5p-7p H14
Night Coverage 4 5p-7p H3
Night Coverage 4 7p-7a H12
Night Coverage 4 7p-7a H13
Night Coverage 4 7p-7a H14
Night Coverage 4 7p-7a H3
Night Direct Care Admitter 2 (Consult)
Night- Resident Direct Care Admitter
Teaching Long Call
UM
Vineland Admin- Shift 1
Vineland CDU-APP
Vineland Day-APP (extra)
Vineland Day-Resident Moonlighter
Vineland E (Bridgeton APP pulled to Vineland)
Vineland Long Call
Vineland Long Call Weekend Admitting Hospitalist
Vineland Long Call Weekend Early Call
Vineland Long Call Weekend Late Call
Vineland Night APP
Vineland Night APP 2
Vineland Night Orienting APP
Vineland Site Director
Vineland Swing Shift Resident Moonlighter
Vineland Team F (APP)
Vineland Y (Elmer APP pulled to Vineland)
Virtua Marlton PM Coverage
Virtua Marlton-Voorhees APN
Virtua Mt Holly AM Coverage
Virtua Mt Holly PM Coverage
Virtua Mt. Holly-Willingboro APN
Virtua Site Director
Virtua Voorhees AM Coverage
Virtua Voorhees PM Coverage
Virtua Willingboro PM Coverage
Woodbury Consult APP
Woodbury Consult Physician
```

#### Full Included Service List

These services **count** toward prior weeks/weekends/nights worked:

```
(MAH) Admitter 1 - Day Admitting Hospitalist
(NAH) Night Admitting Hospitalist
Admitter 2
Bridgeton On-Call
Cape ATT 1
Cape ATT 2
Cape ATT 3
Cape ATT 4
Cape ATT 5
Cape ATT 6
Cape ATT 7
Cape ATT 8
Cape Early Call
Cape Extra
Cape LTC-SAR on call Physician
Cape MAH
Cape Nocturnist 1
Cape Swing Shift 1
Clinical Care Physician Advisor (CCPA)
Elmer 1
Elmer 2
Elmer Nocturnist 1
H1
H2
H3
H4
H5
H6
H7
H8- Pav 6 & EXAU
H9
H10
H11
H12- Pav 8 & Pav 9
H13- (Obs overflow)
H14
H15
H16
H17
HA
HB
HC
HD
HE
HF
HG
HM (Family Medicine)
Hospital Medicine Consults
IMC UM Referrals Weekends and Holidays
Inspira- Mannington Days
Inspira- Mannington Nights
Inspira- Mannington PA
MH+ E UM Referrals Weekdays
MH+ E UM Rounds-PA Advisor Weekdays
Mullica Hill - Med 3
Mullica Hill -Med 1
Mullica Hill -Med 2
Mullica Hill A
Mullica Hill B
Mullica Hill C
Mullica Hill D
Mullica Hill E
Mullica Hill FM
Mullica Hill MAH
Mullica Hill Med 4
Mullica Hill Med 5
Mullica Hill Nocturnist 1
Mullica Hill Nocturnist 2
Mullica Hill Nocturnist 3
Mullica Hill Swing Shift
Mullica Hill V
Mullica Hill W (OBS Unit)
Mullica Hill X (extra)
Mullica Hill Z (extra)
Night Direct Care Admitter 1
SAH
Teaching Admitting Hospitalist (TAH)
Vineland A
Vineland B
Vineland C
Vineland CDU
Vineland D
Vineland MAH
Vineland Med 1
Vineland Med 2
Vineland Med 3
Vineland Med 4
Vineland Nocturnist 1
Vineland Nocturnist 2
Vineland Nocturnist- Additonal
Vineland Swing Shift Physician
Vineland UM Referrals Weekdays
Vineland UM Rounds (PA) Weekdays
Vineland UM Rounds (PA) Weekends and Holidays
Vineland Z (extra)
Vineland- Team X (extra)
Virtua - Additional
Virtua - Marlton
Virtua - Mount Holly 1
Virtua - Mount Holly 2
Virtua - Voorhees 1
Virtua - Voorhees 2
Virtua - Voorhees 3
Virtua - Willingboro
Virtua Marlton Nights
Virtua Mount Holly Nights
Virtua Voorhees Nights
Virtua-Willingboro Nights
```

#### Classification Notes
- **Moonlighting** shifts are always excluded regardless of service name
  (the Amion HTML marks them with `xpay_dull` icon)
- **Virtua "Coverage"** shifts (AM/PM Coverage) are effectively long call
  and do not count toward weeks/weekends. Regular Virtua site shifts and
  Virtua night shifts DO count.
- **Night Coverage 1-4** are APP roles, not physician nocturnist shifts.
  Physician nocturnist services (e.g., Mullica Hill Nocturnist 1, Cape
  Nocturnist 1) ARE included.
- If a new service appears that is not in either list, the engine should
  flag it for manual review rather than silently including or excluding it.

### 1.4 Site Directors
| Site | Directors |
|------|----------|
| Cooper | Melissa Mangold, Tyler McMillian, Katie Haroldson, Cynthia Glickman, Michael Gross |
| Mullica Hill | Oberdorf, Olayemi, Gambale |

Site directors get reduced holiday requirements (2/year regardless of FTE,
see Section 4.2).

### 1.5 Scheduling Coordinators
| Code | Coordinator | Manages |
|------|------------|---------|
| MM | — | Cooper site |
| PS | — | Nights/nocturnists |
| CD | — | Vineland/Inspira sites |
| ZF | — | Virtua sites |
| AM | — | Cape/Atlantic region |

Some providers have compound scheduler codes (e.g., "PS/CD/NT", "MM/NT")
indicating split responsibilities across coordinators.

---

## Section 2: Hard Rules (Must never be violated)

These constraints are absolute. The engine must never break them.

### 2.1 Availability Is Sacred
- If a provider marks a day as **unavailable**, they are NEVER scheduled that day
- No exceptions, no overrides, no forced fill can violate this
- "Blank" days (no submission) are treated as available

### 2.2 Site Eligibility
- Providers can only be assigned to sites where their allocation percentage > 0
- A provider with `pct_cooper = 0` is never placed at Cooper
- Tag-based restrictions (`no_elmer`, `no_vineland`) further remove specific sites

### 2.3 Tag-Based Exclusions
- `do_not_schedule` → provider is completely excluded from scheduling
- `no_elmer` → provider cannot be assigned to Elmer
- `no_vineland` → provider cannot be assigned to Vineland

### 2.4 Capacity Limits
- A provider cannot be scheduled for more weeks than `ceil(weeks_remaining)`
- A provider cannot be scheduled for more weekends than `ceil(weekends_remaining)`

### 2.5 Week + Weekend Pairing
- When a provider works a weekday period, their weekend should be at the **same site**
- This creates a 7-day stretch (Mon–Sun) at one location

> **QUESTION FOR SCHEDULER:** Is this an absolute rule? Can a provider ever
> work weekdays at one site and the adjacent weekend at a different site?

### 2.6 Nocturnists Excluded
- Pure night providers (`shift_type = "Nights"` with no remaining weeks/weekends) are excluded
- "Split" providers (Night/Hybrid shift type but still owe day shifts) ARE included

### 2.7 Excluded Providers
- Providers tagged `do_not_schedule` are excluded from automated scheduling
- The tag is the sole mechanism for exclusion (engine does not infer from
  other fields)

As of the process definition document, the following are excluded:
Rachel Nash, Alka Farmer, Samer Badr, Deepa Velayadikot,
Sebastien Rachoin, Lisa Cerceo, Snehal Ghandi, Alisa Peet

Plus leave-based exclusions:
Gummadi (leave), Patel Kajal (FMLA), Zheng Angela (maternity leave)

> **QUESTION FOR SCHEDULER:** Is this list current? The user added 6 new
> do_not_schedule tags on Feb 17, 2026 — are they reflected here?

---

## Section 3: Soft Rules (Strong preferences, can be relaxed)

### 3.1 Fair-Share Distribution
- Each provider should work roughly 1/3 of their annual obligation per block
- When annual total doesn't divide evenly by 3, distribute the remainder
  (e.g., 26 weeks/year → 9 + 9 + 8 across three blocks)
- Vary which block gets the short count across the provider group — don't
  give every provider their short block in the same block
- Pass 1 caps at `ceil(annual / 3)` — fair share
- Pass 2 lifts the cap for behind-pace providers

### 3.2 Consecutive Stretch Limits
- **Preferred:** 1 week (7 days — Mon through Sun at same site)
- **Acceptable:** 2 consecutive weeks (14 days)
- **Maximum in forced fill:** 3 consecutive weeks (21 days)
- **Never allowed:** 4+ consecutive weeks

> **QUESTION FOR SCHEDULER:** Is 2 consecutive weeks the right normal limit?

### 3.3 Even Spacing
- Provider assignments should be spread evenly across the block
- Avoid front-loading or back-loading a provider's schedule

### 3.4 Site Allocation Match
- Providers should be assigned to sites proportional to their site percentages
- Some flexibility (±5-10%) is expected

### 3.5 Cooper Fills Last
- Non-Cooper sites filled first (smaller, fixed staffing needs)
- Cooper absorbs remaining capacity
- Cooper gaps are expected — moonlighters/per diem fill them

> **QUESTION FOR SCHEDULER:** How are Cooper gaps filled in practice?

### 3.6 Site Gap Tolerance
- Mullica Hill and Vineland: can leave up to 1 unfilled slot per day
- All other non-Cooper sites: must be fully filled (0 gaps)
- Cooper: gaps expected and acceptable

### 3.7 Minimum Gap Between Stretches
- There should be days off between consecutive work stretches

> **QUESTION FOR SCHEDULER:** What is the minimum acceptable gap?

### 3.8 Stretch Flexibility
- A stretch can start on a weekend and continue into the following week
  (e.g., Sat–Fri), or start Monday through the next weekend (Mon–Sun)
- Splitting weekdays at one site and the adjacent weekend at another
  should be rare

---

## Section 4: Holiday Rules

### 4.1 Holidays in the System
6 holidays per year: New Year's Day, Memorial Day, 4th of July,
Labor Day, Thanksgiving, Christmas Day.

### 4.2 Holiday Work Requirements by FTE
Each provider is required to work a certain number of holidays per year
based on their FTE:

| FTE Range | Holidays Required Per Year |
|-----------|:---:|
| >= 0.76 | 3 |
| 0.50 – 0.75 | 2 |
| < 0.50 | 1 |
| Site directors (any FTE) | 2 |

### 4.3 New Year's / Christmas Guideline
On average, a provider should work either New Year's Day OR Christmas Day
as one of their required holidays (not both, not neither — one or the other).

### 4.4 Holiday Preferences
- Providers submit 2 preferred holidays via `holiday_1` / `holiday_2`
- These are the holidays they prefer to have OFF
- If a provider lists a holiday, they should not be scheduled during the week
  containing that holiday

### 4.5 Block 3 Holidays
| Holiday | Date | Week (Mon–Fri) |
|---------|------|----------------|
| Memorial Day | May 25, 2026 (Mon) | May 25–29 |

All other holidays fall outside Block 3:
- New Year's Day → Jan 1, 2026 (Block 2)
- 4th of July → Jul 4, 2026 (after Block 3)
- Labor Day, Thanksgiving, Christmas → next cycle

> **QUESTION FOR SCHEDULER:** Does a holiday preference block the whole week
> or just the holiday day itself?

> **QUESTION FOR SCHEDULER:** Since there are 6 holidays spread across 3
> blocks (~2 per block), how do you track which holidays a provider has already
> worked in Blocks 1 & 2 to know what they still owe in Block 3? Is this
> tracked in the Google Sheet?

---

## Section 5: Named Special Rules (Provider-Specific)

### 5.1 Glenn Newell — Consults Only, Mon–Thu
- Available Monday through Thursday only (Friday blank)

> **QUESTION FOR SCHEDULER:** Should we assign him full weeks and note Friday
> is blank, or only assign 4-day weeks?

### 5.2 Haroldson & McMillian — Never Same Week/Weekend
- Katie Haroldson and Tyler McMillian must never be scheduled at the same
  site during the same week or weekend

> **QUESTION FOR SCHEDULER:** Same site same week, or same week at any site?

### 5.3 Paul Stone — Non-Teaching Only, Never MAH
- Service-level constraint — may not apply until service assignment phase

> **QUESTION FOR SCHEDULER:** Any site-level restrictions, or only service-level?

---

## Section 6: Scope Boundaries

### 6.1 In Scope
- Assigning providers to weeks and weekends at sites
- Respecting availability, capacity, site eligibility, holidays
- Fair distribution across providers and across the block
- Multiple output variations for scheduler to choose from

### 6.2 Design Principle: Block-Specific Configuration
All provider-to-service eligibility rules, teaching restrictions, location
percentages, PA rotations, and special constraints are **block-specific
configuration that changes every block**. The engine must accept these as
configurable input (via the Google Sheet), not hardcoded logic. The structure
of the rules stays the same; the specific values change based on staffing,
availability, and site director decisions.

### 6.3 Out of Scope (Deferred)
- Service-level assignment within sites (teaching vs non-teaching, PA, etc.)
- Night shift scheduling
- Day/night transition rules for hybrid providers (process doc says 2-3 days
  off between switching day↔night — will need this when hybrid scheduling
  comes in scope)
- Swing shift scheduling
- PA rotation pool assignment (limited pool at MH — Butt, Oberdorf,
  Siddiqui, Bibbin, Nicole)
- Teaching restrictions per site (detailed lists exist for MH;
  Cooper/Vineland/Cape need similar capture)
- UM-eligible provider restrictions ("only certain people" at Vineland, Cape)

---

## Section 7: Known Data Issues

### 7.1 Prior Actuals May Be Inflated
- `weeks_remaining` / `weekends_remaining` carry forward ALL deficit from
  Blocks 1 & 2, potentially overloading Block 3
- 55 providers have >40% of annual remaining (fair share = 33%)
- Some show >70% remaining — likely leave or counting errors

### 7.2 Missing Dates in Prior Actuals
- June 30, 2025 (Block 1 start) missing from calculation
- March 1, 2026 (Block 2 end) missing from calculation

### 7.3 Service Classification Under Review
- 175 excluded, 107 included services — correctness pending review

---

## Section 8: Open Questions

| # | Question | Section |
|---|----------|---------|
| Q1 | Should engine act on free-text tags (location_restriction, swing_shift, etc.) or ignore them for manual adjustment? | Input 2 |
| Q2 | Should the engine schedule Cape swing shifts? | Input 3 |
| Q3 | Is week + weekend pairing at the same site absolute? | 2.5 |
| Q4 | How should self-schedulers be identified? | 2.7 |
| Q5 | Is 2 consecutive weeks the right normal limit? | 3.2 |
| Q6 | How are Cooper gaps filled in practice? | 3.5 |
| Q7 | What is the minimum gap between stretches? | 3.6 |
| Q8 | Does a holiday preference block the whole week or just the day? | 4.1 |
| Q9 | Glenn Newell — full week or 4-day week? | 5.1 |
| Q10 | Haroldson & McMillian — same site or same week restriction? | 5.2 |
| Q11 | Paul Stone — any site-level restrictions? | 5.3 |
| Q12 | Are the site staffing numbers in the Google Sheet current? | Input 3 |
| Q13 | Should providers with >70% remaining be investigated before scheduling? | 7.1 |
| Q14 | How are exact block boundary dates determined each year? | 1.1 |
| Q15 | How are holidays worked in Blocks 1 & 2 tracked so we know what's owed in Block 3? | 4.2 |
| Q16 | Is the do_not_schedule list current after the Feb 17 additions? | 2.7 |
