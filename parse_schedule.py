#!/usr/bin/env python3
"""
Parse Amion HTML schedule(s) into structured data.
Extracts provider assignments by date and service, including moonlighting flags.
Supports multiple input files (one per month) and consolidates output.
"""

import re
import json
import csv
import sys
import os
import glob
from html.parser import HTMLParser
from collections import defaultdict


class AmionScheduleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.services = []
        self.schedule = []

        # Parser state
        self.in_table = False
        self.table_count = 0
        self.in_row = False
        self.in_cell = False
        self.in_font_tag = False  # track font tags for small footnote text
        self.font_is_footnote = False
        self.current_row_cells = []
        self.current_cell_text = ""
        self.current_cell_flags = {
            "moonlighting": False,
            "note": "",
            "telehealth": False,
        }
        self.row_count = 0
        self.header_row_index = None
        self.schedule_table_found = False
        self.year = None  # extracted from title

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        if tag == "table":
            self.table_count += 1
            if attrs_dict.get("border") == "1":
                self.in_table = True
                self.schedule_table_found = True
                self.row_count = 0

        if not self.in_table:
            return

        if tag == "tr":
            self.in_row = True
            self.current_row_cells = []

        elif tag == "td":
            self.in_cell = True
            self.current_cell_text = ""
            self.current_cell_flags = {
                "moonlighting": False,
                "note": "",
                "telehealth": False,
            }

        elif tag == "font" and self.in_cell:
            style = attrs_dict.get("style", "")
            # Detect footnote-style small text (e.g. font-size:8px)
            if "font-size" in style and ("8px" in style or "7px" in style or "6px" in style):
                self.font_is_footnote = True
            else:
                self.font_is_footnote = False
            self.in_font_tag = True

        elif tag == "img" and self.in_cell:
            src = attrs_dict.get("src", "")
            title = attrs_dict.get("title", "")
            if "xpay_dull" in src:
                self.current_cell_flags["moonlighting"] = True
            elif "pnote2" in src or "pnohu4" in src:
                self.current_cell_flags["note"] = title
            elif "telehealth" in src:
                self.current_cell_flags["telehealth"] = True

        elif tag == "br" and self.in_cell:
            self.current_cell_text += "\n"

    def handle_endtag(self, tag):
        if not self.in_table:
            return

        if tag == "font" and self.in_cell:
            self.in_font_tag = False
            self.font_is_footnote = False

        if tag == "td" and self.in_cell:
            self.in_cell = False
            cell_text = self.current_cell_text.strip()
            cell_text = cell_text.replace("\xa0", " ").strip()
            self.current_row_cells.append({
                "text": cell_text,
                "flags": self.current_cell_flags.copy()
            })

        elif tag == "tr" and self.in_row:
            self.in_row = False
            self.row_count += 1
            self._process_row(self.current_row_cells)

        elif tag == "table" and self.in_table:
            self.in_table = False

    def handle_data(self, data):
        if self.in_cell and self.in_table:
            # Skip footnote text (small superscript numbers)
            if self.font_is_footnote:
                return
            self.current_cell_text += data

    def handle_entityref(self, name):
        if self.in_cell and self.in_table:
            if name == "nbsp":
                self.current_cell_text += " "
            else:
                self.current_cell_text += f"&{name};"

    def handle_charref(self, name):
        if self.in_cell and self.in_table:
            try:
                self.current_cell_text += chr(int(name))
            except ValueError:
                pass

    def _process_row(self, cells):
        if not cells:
            return

        first_cell = cells[0]["text"]

        if self.header_row_index is None and len(cells) > 10:
            newline_count = sum(1 for c in cells[1:20] if "\n" in c["text"])
            if newline_count > 5:
                self.header_row_index = self.row_count
                self._parse_header(cells)
                return

        date_match = re.match(r'(Sun|Mon|Tue|Wed|Thu|Fri|Sat)\s+(\d+/\d+)', first_cell)
        if date_match and self.services:
            self._parse_data_row(cells, date_match)

    def _parse_header(self, cells):
        """Parse the header row to extract service names and hours."""
        self.services = []
        for cell in cells[1:]:
            text = cell["text"]
            parts = text.split("\n")
            if len(parts) >= 2:
                name = parts[0].strip()
                # Clean hours: remove stray footnote numbers
                hours = re.sub(r'\s+\d+$', '', parts[1].strip()).strip()
            else:
                name = text.strip()
                hours = ""
            self.services.append({"name": name, "hours": hours})

    def _parse_data_row(self, cells, date_match):
        """Parse a data row with provider assignments."""
        day_of_week = date_match.group(1)
        date_str = date_match.group(2)

        # Add year if we know it
        if self.year:
            full_date = f"{date_str}/{self.year}"
        else:
            full_date = date_str

        assignments = []
        for i, cell in enumerate(cells[1:]):
            if i >= len(self.services):
                break

            service = self.services[i]
            provider_text = cell["text"].strip()

            if provider_text == "-" or provider_text == "":
                provider = ""
            else:
                # Remove trailing footnote numbers
                provider = re.sub(r'\s*\d+\s*$', '', provider_text).strip()
                # Also clean up double spaces
                provider = re.sub(r'\s+', ' ', provider)

            assignment = {
                "service": service["name"],
                "hours": service["hours"],
                "provider": provider,
                "moonlighting": cell["flags"]["moonlighting"],
                "telehealth": cell["flags"]["telehealth"],
            }
            if cell["flags"]["note"]:
                assignment["note"] = cell["flags"]["note"]

            assignments.append(assignment)

        self.schedule.append({
            "date": full_date,
            "day_of_week": day_of_week,
            "assignments": assignments,
        })


def extract_year_from_html(html):
    """Try to extract the year from the schedule title."""
    match = re.search(r'(\d+/\d+)\s+to\s+\d+/\d+,\s+(\d{4})', html)
    if match:
        return match.group(2)
    return None


def parse_schedule(html_file):
    """Parse a single HTML file and return structured schedule data."""
    with open(html_file, 'r', encoding='utf-8', errors='replace') as f:
        html = f.read()

    year = extract_year_from_html(html)

    parser = AmionScheduleParser()
    parser.year = year
    parser.feed(html)

    return {
        "source_file": os.path.basename(html_file),
        "year": year,
        "services": [s for s in parser.services],
        "schedule": parser.schedule,
    }


def merge_schedules(all_data):
    """Merge multiple months of schedule data into one consolidated dataset."""
    merged = {
        "services": [],
        "schedule": [],
        "by_provider": defaultdict(list),
    }

    seen_services = set()
    for month_data in all_data:
        # Merge services (deduplicate by name)
        for s in month_data["services"]:
            if s["name"] not in seen_services:
                seen_services.add(s["name"])
                merged["services"].append(s)

        # Merge schedule days
        merged["schedule"].extend(month_data["schedule"])

    # Build the by-provider index
    for day in merged["schedule"]:
        for a in day["assignments"]:
            if a["provider"] and a["provider"] != "OPEN SHIFT":
                merged["by_provider"][a["provider"]].append({
                    "date": day["date"],
                    "day_of_week": day["day_of_week"],
                    "service": a["service"],
                    "hours": a["hours"],
                    "moonlighting": a["moonlighting"],
                    "telehealth": a["telehealth"],
                    "note": a.get("note", ""),
                })

    # Convert defaultdict to regular dict for JSON serialization
    merged["by_provider"] = dict(merged["by_provider"])

    return merged


def write_json(data, output_file):
    """Write schedule data to JSON."""
    with open(output_file, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"  Wrote JSON: {output_file}")


def write_csv(data, output_file):
    """Write schedule data to a flat CSV (one row per assignment)."""
    with open(output_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            "date", "day_of_week", "service", "hours",
            "provider", "moonlighting", "telehealth", "note"
        ])

        for day in data["schedule"]:
            for a in day["assignments"]:
                if a["provider"]:
                    writer.writerow([
                        day["date"],
                        day["day_of_week"],
                        a["service"],
                        a["hours"],
                        a["provider"],
                        a["moonlighting"],
                        a["telehealth"],
                        a.get("note", ""),
                    ])
    print(f"  Wrote CSV:  {output_file}")


def write_provider_csv(data, output_file):
    """Write provider-centric CSV (one row per provider per day-shift)."""
    with open(output_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            "provider", "date", "day_of_week", "service", "hours",
            "moonlighting", "telehealth", "note"
        ])

        for provider in sorted(data["by_provider"].keys()):
            shifts = data["by_provider"][provider]
            # Sort by date
            for shift in shifts:
                writer.writerow([
                    provider,
                    shift["date"],
                    shift["day_of_week"],
                    shift["service"],
                    shift["hours"],
                    shift["moonlighting"],
                    shift["telehealth"],
                    shift["note"],
                ])
    print(f"  Wrote provider CSV: {output_file}")


def print_summary(data):
    """Print a summary of the parsed data."""
    print(f"\n{'='*60}")
    print(f"SCHEDULE SUMMARY")
    print(f"{'='*60}")
    print(f"Services: {len(data['services'])}")
    print(f"Days: {len(data['schedule'])}")

    if data['schedule']:
        print(f"Date range: {data['schedule'][0]['date']} ({data['schedule'][0]['day_of_week']}) to {data['schedule'][-1]['date']} ({data['schedule'][-1]['day_of_week']})")

    # Count assignments
    total_assignments = sum(
        1 for day in data['schedule']
        for a in day['assignments']
        if a['provider']
    )
    print(f"Total assignments: {total_assignments}")

    # Count moonlighting shifts
    moon_count = 0
    moon_providers = set()
    for day in data['schedule']:
        for a in day['assignments']:
            if a['moonlighting']:
                moon_count += 1
                moon_providers.add(a['provider'])

    print(f"\nMoonlighting shifts: {moon_count}")
    print(f"Providers with moonlighting: {len(moon_providers)}")
    for p in sorted(moon_providers):
        count = sum(1 for day in data['schedule'] for a in day['assignments']
                    if a['moonlighting'] and a['provider'] == p)
        print(f"  {p}: {count} shifts")

    # Unique providers
    print(f"\nTotal unique providers: {len(data.get('by_provider', {}))}")

    # Validation checks (uncomment and customize as needed)
    # print(f"\n--- Validation Checks ---")
    # for day in data['schedule']:
    #     for a in day['assignments']:
    #         if 'SomeProvider' in a.get('provider', ''):
    #             print(f"  {a['provider']} on {day['date']}: {a['service']} ({a['hours']})")


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_dir = os.path.join(script_dir, "input")
    output_dir = os.path.join(script_dir, "output")
    os.makedirs(output_dir, exist_ok=True)

    # Find all HTML/txt input files
    input_files = sorted(
        glob.glob(os.path.join(input_dir, "*.txt")) +
        glob.glob(os.path.join(input_dir, "*.html"))
    )

    if not input_files:
        print("No input files found in", input_dir)
        sys.exit(1)

    all_month_data = []

    for f in input_files:
        print(f"\nParsing: {os.path.basename(f)}")
        month_data = parse_schedule(f)
        all_month_data.append(month_data)

        # Write per-month output
        base = os.path.splitext(os.path.basename(f))[0]
        write_json(month_data, os.path.join(output_dir, f"{base}.json"))
        write_csv(month_data, os.path.join(output_dir, f"{base}.csv"))

    # Merge all months
    merged = merge_schedules(all_month_data)

    # Write consolidated outputs
    write_json(merged, os.path.join(output_dir, "all_months_schedule.json"))
    write_csv(merged, os.path.join(output_dir, "all_months_schedule.csv"))
    write_provider_csv(merged, os.path.join(output_dir, "all_months_by_provider.csv"))

    # Print summary
    print_summary(merged)
