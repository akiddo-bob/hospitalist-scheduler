# Project Overview

## What This Project Does

The Hospitalist Scheduler automates physician scheduling for a multi-site hospital medicine group. The scheduling process has two major components:

1. **Block Scheduling** — Assigning providers to sites and services across a 4-month block (who works where, which weeks)
2. **Long Call Assignment** — Assigning evening/morning long call shifts on top of the published daytime schedule (who covers after-hours)

Both components are implemented and produce interactive HTML reports with assignments, fairness metrics, and flagged issues.

## Who It's For

The primary users are the scheduling coordinators who build the hospitalist schedule every 4 months. Previously this was done entirely by hand in Excel spreadsheets and Amion. The tool automates the assignment logic and produces multiple variations to choose from.

## The Scheduling Workflow

### Annual Structure
- The year is divided into 3 blocks of ~4 months each
- The current year started June 30, 2025
- Block 3 (current): March 2 – June 28, 2026

### The Process
1. **Providers submit availability** — Available/unavailable days for the upcoming block
2. **Block schedule is built** — Providers assigned to sites and services, day by day
3. **Schedule is published to Amion** — The online scheduling system used by the group
4. **Long call is assigned** — Evening/morning shifts layered on top of the daytime schedule

This tool automates steps 2 and 4.

## The Organization

### Sites
This is a multi-site hospital network, not a single hospital:

| Site | Weekday Staff | Notes |
|------|--------------|-------|
| Cooper Camden | 26 doctors | Largest site — filled last (absorbs leftover capacity) |
| Mullica Hill | 11 doctors | Inspira network |
| Vineland | 11 + 1 UM | Inspira network |
| Elmer | 1 doctor | Single-provider site |
| Cape May | 6 + 1 swing + 1 UM | |
| Mannington | 1 doctor | Single-provider site |
| Virtua | 6 doctors | 4 sub-sites (Willingboro, Mt Holly, Marlton, Voorhees) |

### Schedulers
The work is divided among scheduling coordinators, each managing a subset of providers:
- **MM** — Cooper site director
- **PS** — Nights/nocturnists
- **CD** — Vineland/Inspira sites
- **ZF** — Virtua sites
- **AM** — Cape/Atlantic region

### Providers
- ~253 providers in the system
- ~127 with active availability data in any given block
- Mix of full-time, part-time, per diem, and moonlighting
- Providers may work at multiple sites based on percentage splits

## Technology Stack

- **Python 3.8+** with `networkx` for bipartite matching
- **HTML/CSS/JavaScript** for interactive reports
- **Amion** for schedule publishing (external system)
- **Git + git-crypt** for version control with encrypted PII
- **GitHub Pages** for report hosting (password-gated)

## Project Status

| Component | Status | Notes |
|-----------|--------|-------|
| Long call assignment engine | ✅ Complete | Multi-phase algorithm, 14-check validation |
| Long call HTML reports | ✅ Complete | Interactive, sortable, filterable |
| Amion schedule parser | ✅ Complete | Handles moonlighting, telehealth, notes |
| Block schedule engine | ✅ Complete | Multi-site, fair-share two-pass |
| Block schedule reports | ✅ Complete | 5 variations, site utilization, provider detail |
| GitHub Pages deployment | ✅ Complete | Password-gated, iframe-based |
| Manual LC analysis tool | ✅ Complete | 3-block ground truth validation |
| Validation suite | ✅ Complete | 14 automated checks |
