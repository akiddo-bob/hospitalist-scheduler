# Getting Started

## Prerequisites

- **Python 3.8+** (tested on 3.11/3.12)
- **networkx** library (bipartite matching for weekend LC assignment)
- **openpyxl** (for Excel file processing)
- **git-crypt** (for decrypting PII-protected files)

### Install dependencies

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install packages
pip install networkx openpyxl
```

## Configuration

Copy `config.sample.json` to `config.json` and fill it in:

```bash
cp config.sample.json config.json
```

### Config fields

**Global settings:**

| Field | Description |
|-------|-------------|
| `holidays` | All holidays recognized across the year. The engine only acts on holidays within the block dates, but tracks the full set for cross-block fairness. |
| `excluded_providers` | Providers to skip for long call. Names must match Amion format exactly ("Last, First"). |
| `report_password` | Optional. If set, the HTML report requires this password to view (client-side SHA-256 gate). Leave empty for no password. |

**`amion` — Amion connection settings (used by `fetch_availability.py`):**

| Field | Description |
|-------|-------------|
| `file_id` | Amion file identifier from the schedule URL. |
| `ps` | Amion password/session parameter. |
| `ui_prefix` | Amion UI prefix parameter. |
| `base_url` | Amion OCS base URL. |

**`longcall` — Long call engine settings:**

| Field | Description |
|-------|-------------|
| `block_start` | First day of the scheduling block (YYYY-MM-DD). Should be a Monday. |
| `block_end` | Last day of the block (YYYY-MM-DD). Should be a Sunday. |
| `teaching_services` | Service names that count as teaching. Must match Amion column headers exactly. |
| `direct_care_services` | Service names that count as direct care. Must match Amion column headers exactly. |

### Matching service names to Amion

Service names in the config must exactly match the column headers in your Amion HTML export. If Amion shows "H8- Pav 6 & EXAU" as the header, that's what goes in the config. Run `parse_schedule.py` and check the JSON output to see what the parser extracted.

## Quick Start: Long Call Assignment

```bash
# Step 1: Place Amion HTML exports in input/
# Step 2: Parse the schedule
.venv/bin/python3 parse_schedule.py

# Step 3: Generate the report (5 variations)
.venv/bin/python3 generate_report.py 5

# Step 4: Validate
.venv/bin/python3 validate_reports.py
```

Reports are written to `output/reports/{block_start}_{block_end}/`.

## Quick Start: Block Scheduling

```bash
# Generate 5 block schedule variations
.venv/bin/python3 generate_block_report.py

# Output: output/v1/report_1.html through report_5.html (engine v1)
#         output/v2/report_1.html through report_5.html (engine v2)
#         output/index.html (navigation page)
#         output/inputs.html (configuration snapshot)
#         output/rules.html (rules reference)
```

## Quick Start: Block 3 Validation

Validate the manually-created Amion Block 3 schedule against all scheduling rules.
See [Block 3 Validation docs](block3-validation.md) for full details on what each check does.

```bash
# Generate the HTML validation report (self-contained, open in any browser)
.venv/bin/python3 -m analysis.generate_block3_report
# Output: output/block3_validation_report.html

# Console-only validation (no HTML)
.venv/bin/python3 -m analysis.validate_block3

# Compare Block 3 actuals against annual targets (Excel output)
.venv/bin/python3 -m analysis.compare_block3_actuals
# Output: output/block3_actuals.xlsx
```

## Quick Start: Deploy to GitHub Pages

```bash
# Generate reports and deploy
./deploy_pages.sh

# Deploy existing output without regenerating
./deploy_pages.sh --skip
```

Reports are published to `https://akiddo-bob.github.io/hospitalist-scheduler/` with a password gate.

## Directory Structure

```
hospitalist-scheduler/
├── input/                          # Input data (not tracked in git)
│   ├── monthlySchedules/           # Monthly Amion HTML exports (Mar-Jun 2026)
│   ├── Long call 2025-26.xlsx      # Manual LC data (ground truth)
│   ├── Schedule Book*.xlsx         # Scheduler workspace
│   └── individualSchedules/        # Provider availability JSONs
├── output/                         # Generated output (not tracked in git)
│   ├── block3_validation_report.html  # Block 3 validation report
│   ├── block3_actuals.xlsx         # Block 3 actuals comparison
│   ├── prior_actuals.json          # Blocks 1+2 actuals (for capacity check)
│   ├── providers_sheet.csv         # Provider data snapshot
│   └── ...                         # Other reports and data
├── analysis/                       # Block 3 validation tools
│   ├── validate_block3.py          # 12-check validation engine
│   ├── generate_block3_report.py   # HTML report generator
│   └── compare_block3_actuals.py   # Actuals vs targets comparison
├── block/                          # Block scheduling engines
│   ├── engines/shared/loader.py    # Shared data loading (Google Sheets)
│   └── recalculate_prior_actuals.py  # Prior block actuals calculator
├── longcall/                       # Long call engine
│   ├── assign_longcall.py          # LC assignment engine
│   ├── generate_report.py          # LC HTML report generator
│   └── validate_reports.py         # 14-check LC validation suite
├── docs/                           # Documentation
│   ├── block-scheduling-rules.md   # Source of truth for block rules
│   ├── block3-validation.md        # How the validation system works
│   └── ...                         # Other docs
├── parse_schedule.py               # Shared Amion HTML parser
├── name_match.py                   # Provider name matching
├── fetch_availability.py           # Amion availability fetcher
├── config.json                     # Configuration (git-crypt encrypted)
├── deploy_pages.sh                 # GitHub Pages deployment
└── .venv/                          # Python virtual environment
```

## Git and Encryption

The repository uses **git-crypt** to encrypt files containing PII:
- `config.json` — Provider names, passwords
- `output/**` — Generated reports with provider data
- `input/**` — Schedule data with provider information

To work with encrypted files, you need the git-crypt key. Without it, these files appear as binary blobs.

**Important:** When deploying to GitHub Pages, the deploy script copies plaintext files to a temp directory before any git operations. This avoids issues with git-crypt encrypting freshly generated HTML files.
