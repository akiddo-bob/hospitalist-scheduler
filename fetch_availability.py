#!/usr/bin/env python3
"""
Fetch provider availability from Amion and save as JSON files.

Connects to Amion's individual schedule pages, parses the calendar grid
for availability indicators (checkmark = available, X = unavailable,
blank = no submission), and writes one JSON file per provider per month.

Usage:
  # Fetch specific providers for specific months
  python fetch_availability.py --providers "Shah, Hely" "Rasheed, Sammar" \\
                               --months 3 4 5 6 --year 2026

  # Fetch a single provider
  python fetch_availability.py --providers "Bender, Bradley" --months 3 --year 2026

  # Fetch from a file (one "Last, First" per line)
  python fetch_availability.py --provider-file missing_providers.txt \\
                               --months 3 4 5 6 --year 2026

  # Dry run — show what would be fetched without writing files
  python fetch_availability.py --providers "Shah, Hely" --months 3 --year 2026 --dry-run

  # Overwrite existing files
  python fetch_availability.py --providers "Shah, Hely" --months 3 --year 2026 --force

Output format (matches existing individualSchedules JSONs):
  {"name": "Last, First", "month": 3, "year": 2026,
   "days": [{"date": "2026-03-01", "status": "available"}, ...]}

Amion URL pattern:
  https://www.amion.com/cgi-bin/ocs?Fi=<FILE_ID>&Ps=<PS_ID>&Ui=<UI_PREFIX>*<PROVIDER>&Mo=<M>-<YY>

Configuration is read from config.json (amion_file_id, amion_ps, amion_ui_prefix).
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
from datetime import date
from html.parser import HTMLParser


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "input", "individualSchedules")

# Amion defaults — can be overridden in config.json under "amion" key
DEFAULT_AMION = {
    "file_id": "!18fd1c46hcuhatt_1",
    "ps": "914",
    "ui_prefix": "24*1600",
    "base_url": "https://www.amion.com/cgi-bin/ocs",
}

# Rate limit: seconds between requests to be polite to Amion
REQUEST_DELAY = 0.5


def load_amion_config():
    """Load Amion connection settings from config.json, with defaults."""
    cfg = dict(DEFAULT_AMION)
    if os.path.isfile(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        amion = data.get("amion", {})
        for key in cfg:
            if key in amion:
                cfg[key] = amion[key]
    return cfg


# ---------------------------------------------------------------------------
# Amion HTML Parser
# ---------------------------------------------------------------------------

class AmionAvailabilityParser(HTMLParser):
    """
    Parse an Amion individual schedule page to extract per-day availability.

    The calendar is a <table> with border=1. Rows alternate between:
      - Header rows (BGCOLOR=#f6deac): contain day numbers + weekday labels
      - Data rows: contain <td> cells with availability images

    Each data <td> contains one of:
      - <img src="../oci/wp_av.gif" ...>    → available
      - <img src="../oci/wp_unav.gif" ...>  → unavailable
      - No availability image               → blank

    Next-month overflow cells have BGCOLOR=#dcdcdc or #f0f0f0 and are skipped.
    """

    def __init__(self):
        super().__init__()
        self.provider_name = ""
        self.month = 0
        self.year = 0

        # Parser state
        self.in_calendar = False
        self.table_depth = 0           # nesting depth inside the calendar
        self.in_header_row = False
        self.in_data_row = False
        self.in_cell = False
        self.cell_is_overflow = False  # next-month gray cells
        self.cell_status = None        # "available", "unavailable", or None

        self.day_numbers = []          # from header rows
        self.day_statuses = []         # from data rows
        self.current_header_days = []  # day numbers in current header row

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        if tag == "table":
            if not self.in_calendar:
                # Detect the calendar table (border=1)
                if attrs_dict.get("border") == "1":
                    self.in_calendar = True
                    self.table_depth = 1
                return
            else:
                # Nested table inside a calendar cell (shift details)
                self.table_depth += 1
                return

        if not self.in_calendar:
            return

        # Only process TR/TD at the top level of the calendar table
        if tag == "tr" and self.table_depth == 1:
            bgcolor = attrs_dict.get("bgcolor", "").lower()
            if bgcolor == "#f6deac":
                self.in_header_row = True
                self.current_header_days = []
            else:
                self.in_data_row = True
                self.cell_status = None

        elif tag == "td" and self.table_depth == 1:
            self.in_cell = True
            self.cell_status = None
            # Check for overflow cells (next-month, grayed out)
            bgcolor = attrs_dict.get("bgcolor", "").lower()
            self.cell_is_overflow = bgcolor in ("#dcdcdc", "#f0f0f0")

        elif tag == "img" and self.in_cell:
            src = attrs_dict.get("src", "")
            if "wp_av.gif" in src:
                self.cell_status = "available"
            elif "wp_unav.gif" in src:
                self.cell_status = "unavailable"

    def handle_endtag(self, tag):
        if not self.in_calendar:
            return

        if tag == "table":
            self.table_depth -= 1
            if self.table_depth == 0:
                self.in_calendar = False
            return

        # Only process TR/TD at the top level of the calendar table
        if tag == "td" and self.in_cell and self.table_depth == 1:
            self.in_cell = False
            if self.in_data_row and not self.cell_is_overflow:
                status = self.cell_status if self.cell_status else "blank"
                self.day_statuses.append(status)

        elif tag == "tr" and self.table_depth == 1:
            if self.in_header_row:
                self.in_header_row = False
                self.day_numbers.extend(self.current_header_days)
            self.in_data_row = False

    def handle_data(self, data):
        if not self.in_calendar:
            return

        if self.in_header_row and self.in_cell and self.table_depth == 1:
            # Extract day number from header cell text like "1 March" or "15 Su"
            text = data.strip()
            match = re.match(r'^(\d+)', text)
            if match:
                day_num = int(match.group(1))
                # Skip if this looks like a next-month day
                if not self.cell_is_overflow:
                    self.current_header_days.append(day_num)


def extract_title_info(html):
    """Extract provider name, month, and year from the page title.

    Title format: 'Schedule for Last, First, Month YYYY'
    """
    match = re.search(
        r'Schedule for\s+(.+?),\s+(\w+)\s+(\d{4})',
        html
    )
    if match:
        name = match.group(1).strip()
        month_str = match.group(2)
        year = int(match.group(3))
        # Parse month name
        months = {
            "January": 1, "February": 2, "March": 3, "April": 4,
            "May": 5, "June": 6, "July": 7, "August": 8,
            "September": 9, "October": 10, "November": 11, "December": 12,
        }
        month = months.get(month_str, 0)
        return name, month, year
    return None, 0, 0


def parse_availability(html):
    """Parse Amion HTML and return (name, month, year, [{date, status}]).

    Returns:
        (name, month, year, days_list) or raises ValueError on parse failure.
    """
    # Extract title info (more reliable for name than parsing HTML)
    title_match = re.search(
        r'<(?:TITLE|title)>\s*Schedule for\s+(.+?),\s+(\w+)\s+(\d{4})',
        html
    )
    if not title_match:
        raise ValueError("Could not find schedule title in HTML")

    # Name is "Last, First" — the title has "Last, First, Month Year"
    # We need to carefully extract just the name part
    full_title = re.search(
        r'Schedule for\s+(.+),\s+(January|February|March|April|May|June|'
        r'July|August|September|October|November|December)\s+(\d{4})',
        html
    )
    if not full_title:
        raise ValueError("Could not parse title format")

    name = full_title.group(1).strip()
    month_name = full_title.group(2)
    year = int(full_title.group(3))

    month_map = {
        "January": 1, "February": 2, "March": 3, "April": 4,
        "May": 5, "June": 6, "July": 7, "August": 8,
        "September": 9, "October": 10, "November": 11, "December": 12,
    }
    month = month_map[month_name]

    # Parse the calendar table
    parser = AmionAvailabilityParser()
    parser.feed(html)

    # How many days should this month have?
    if month == 12:
        expected_days = (date(year + 1, 1, 1) - date(year, 12, 1)).days
    else:
        expected_days = (date(year, month + 1, 1) - date(year, month, 1)).days

    statuses = parser.day_statuses

    if len(statuses) != expected_days:
        raise ValueError(
            f"Expected {expected_days} days for {month_name} {year}, "
            f"but parsed {len(statuses)} status entries"
        )

    # Build the days list
    days = []
    for d in range(1, expected_days + 1):
        dt = date(year, month, d)
        days.append({
            "date": dt.isoformat(),
            "status": statuses[d - 1],
        })

    return name, month, year, days


# ---------------------------------------------------------------------------
# Fetch from Amion
# ---------------------------------------------------------------------------

def build_url(amion_cfg, provider_name, month, year):
    """Build the Amion URL for a provider's individual schedule page.

    Args:
        amion_cfg: dict with file_id, ps, ui_prefix, base_url
        provider_name: "Last, First" format
        month: integer 1-12
        year: integer (e.g. 2026)
    """
    yy = year % 100
    ui_value = f"{amion_cfg['ui_prefix']}*{provider_name}"
    params = {
        "Fi": amion_cfg["file_id"],
        "Ps": amion_cfg["ps"],
        "Ui": ui_value,
        "Mo": f"{month}-{yy}",
    }
    return f"{amion_cfg['base_url']}?{urllib.parse.urlencode(params)}"


def fetch_page(url):
    """Fetch a URL and return the HTML content as a string."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "HospitalistScheduler/1.0 (availability fetch)"
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def make_filename(provider_name, month, year):
    """Generate the output filename for a provider/month.

    Converts "Last, First" → "schedule_Last_First_MM_YYYY.json"
    Handles multi-part names: "Al-Safadi, Maher" → "schedule_Al-Safadi_Maher_03_2026.json"
    """
    parts = provider_name.split(",")
    last = parts[0].strip().replace(" ", "_")
    first = parts[1].strip().replace(" ", "_") if len(parts) > 1 else ""
    return f"schedule_{last}_{first}_{month:02d}_{year}.json"


def write_availability_json(name, month, year, days, output_dir):
    """Write a single availability JSON file. Returns the filepath."""
    obj = {
        "name": name,
        "month": month,
        "year": year,
        "days": days,
    }
    filename = make_filename(name, month, year)
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w") as f:
        json.dump(obj, f)
    return filepath


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fetch provider availability from Amion and save as JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --providers "Shah, Hely" "Rasheed, Sammar" --months 3 4 5 6 --year 2026
  %(prog)s --provider-file providers.txt --months 3 4 5 6 --year 2026
  %(prog)s --providers "Bender, Bradley" --months 3 --year 2026 --dry-run
        """,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--providers", nargs="+", metavar='"Last, First"',
        help='Provider names in "Last, First" format (Amion spelling)',
    )
    group.add_argument(
        "--provider-file", metavar="FILE",
        help="File with one provider name per line",
    )
    parser.add_argument(
        "--months", nargs="+", type=int, required=True,
        help="Month numbers to fetch (e.g. 3 4 5 6)",
    )
    parser.add_argument(
        "--year", type=int, required=True,
        help="Year (e.g. 2026)",
    )
    parser.add_argument(
        "--output-dir", default=OUTPUT_DIR,
        help=f"Output directory (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be fetched without writing files",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite existing files (default: skip)",
    )
    parser.add_argument(
        "--delay", type=float, default=REQUEST_DELAY,
        help=f"Seconds between requests (default: {REQUEST_DELAY})",
    )

    args = parser.parse_args()

    # Load providers
    if args.provider_file:
        with open(args.provider_file) as f:
            providers = [line.strip() for line in f if line.strip()
                         and not line.strip().startswith("#")]
    else:
        providers = args.providers

    # Validate months
    for m in args.months:
        if m < 1 or m > 12:
            print(f"ERROR: Invalid month: {m}", file=sys.stderr)
            sys.exit(1)

    # Load Amion config
    amion_cfg = load_amion_config()

    # Ensure output directory exists
    os.makedirs(args.output_dir, exist_ok=True)

    # Summary
    total = len(providers) * len(args.months)
    print(f"Providers:  {len(providers)}")
    print(f"Months:     {sorted(args.months)}")
    print(f"Year:       {args.year}")
    print(f"Output:     {args.output_dir}")
    print(f"Total jobs: {total}")
    if args.dry_run:
        print("MODE:       DRY RUN (no files will be written)\n")
    else:
        print()

    # Process each provider × month
    success = 0
    skipped = 0
    errors = []

    for provider in providers:
        print(f"  {provider}:")
        for month in sorted(args.months):
            filename = make_filename(provider, month, args.year)
            filepath = os.path.join(args.output_dir, filename)
            label = f"    {month:02d}/{args.year}"

            # Skip if file exists and not forcing
            if os.path.isfile(filepath) and not args.force:
                print(f"{label}: SKIP (exists) — {filename}")
                skipped += 1
                continue

            # Build URL
            url = build_url(amion_cfg, provider, month, args.year)

            if args.dry_run:
                print(f"{label}: WOULD FETCH — {url}")
                continue

            # Fetch and parse
            try:
                html = fetch_page(url)
                name, parsed_month, parsed_year, days = parse_availability(html)

                # Sanity check
                if parsed_month != month or parsed_year != args.year:
                    raise ValueError(
                        f"Page returned month={parsed_month}, year={parsed_year} "
                        f"but expected month={month}, year={args.year}"
                    )

                # Write JSON
                written = write_availability_json(
                    name, month, args.year, days, args.output_dir
                )
                # Count statuses
                avail = sum(1 for d in days if d["status"] == "available")
                unavail = sum(1 for d in days if d["status"] == "unavailable")
                blank = sum(1 for d in days if d["status"] == "blank")
                print(f"{label}: OK ({avail}a/{unavail}u/{blank}b) — {filename}")
                success += 1

            except Exception as e:
                print(f"{label}: ERROR — {e}")
                errors.append((provider, month, str(e)))

            # Rate limit
            time.sleep(args.delay)

        print()

    # Summary
    print(f"{'='*50}")
    print(f"Done. Success: {success}, Skipped: {skipped}, Errors: {len(errors)}")
    if errors:
        print(f"\nErrors:")
        for provider, month, msg in errors:
            print(f"  {provider} ({month:02d}): {msg}")

    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
