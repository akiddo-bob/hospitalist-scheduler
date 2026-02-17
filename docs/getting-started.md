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

| Field | Description |
|-------|-------------|
| `block_start` | First day of the scheduling block (YYYY-MM-DD). Should be a Monday. |
| `block_end` | Last day of the block (YYYY-MM-DD). Should be a Sunday. |
| `holidays` | All holidays recognized across the year. The engine only acts on holidays within the block dates, but tracks the full set for cross-block fairness. |
| `excluded_providers` | Providers to skip for long call. Names must match Amion format exactly ("Last, First"). |
| `teaching_services` | Service names that count as teaching. Must match Amion column headers exactly. |
| `direct_care_services` | Service names that count as direct care. Must match Amion column headers exactly. |
| `report_password` | Optional. If set, the HTML report requires this password to view (client-side SHA-256 gate). Leave empty for no password. |

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

# Output: output/block_schedule_report_v1.html through v5.html
#         output/index.html (navigation page)
#         output/inputs.html (configuration snapshot)
#         output/rules.html (rules reference)
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
│   ├── *.html                      # Monthly Amion schedule exports
│   ├── Long call 2025-26.xlsx      # Manual LC data (ground truth)
│   ├── Schedule Book*.xlsx         # Scheduler workspace
│   └── individualSchedules/        # Provider availability JSONs
├── output/                         # Generated output (not tracked in git)
│   ├── all_months_schedule.json    # Consolidated parsed schedule
│   ├── longcall_assignments.*      # LC assignments (JSON + TXT)
│   ├── block_schedule_report_*.html # Block schedule variations
│   ├── index.html                  # Navigation page
│   ├── inputs.html                 # Input parameters snapshot
│   ├── rules.html                  # Rules reference
│   └── reports/                    # LC report variations
├── docs/                           # This documentation
├── config.json                     # Configuration (git-crypt encrypted)
├── config.sample.json              # Config template
├── assign_longcall.py              # LC assignment engine
├── generate_report.py              # LC HTML report generator
├── validate_reports.py             # 14-check validation suite
├── parse_schedule.py               # Amion HTML parser
├── block_schedule_engine.py        # Block schedule generator
├── generate_block_report.py        # Block schedule HTML reports
├── recalculate_prior_actuals.py    # Prior blocks analyzer
├── analyze_manual_lc.py            # Manual LC analysis tool
├── debug_analyze.py                # Debugging & analysis
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
