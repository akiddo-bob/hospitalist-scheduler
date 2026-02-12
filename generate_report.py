#!/usr/bin/env python3
"""
Generate a comprehensive HTML report from long call assignments.
Auto-refreshes in the browser so you always see the latest data.
"""

import hashlib
import json
import os
from datetime import datetime, timedelta
from collections import defaultdict

# Import the assignment engine
from assign_longcall import (
    load_schedule, build_daily_data, build_all_daily_data, assign_long_calls,
    is_weekend_or_holiday, is_weekend, is_holiday, day_name,
    BLOCK_START, BLOCK_END, HOLIDAYS, EXCLUDED_PROVIDERS,
    TEACHING_SERVICES, DIRECT_CARE_SERVICES, ALL_SOURCE_SERVICES,
    get_provider_category, identify_stretches, is_standalone_weekend,
    split_stretch_into_weeks,
    is_moonlighting_in_stretch, count_weekends_in_block,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")


def esc(text):
    """Escape HTML special characters."""
    if text is None:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def provider_anchor(name):
    """Generate a stable HTML anchor ID from a provider name."""
    return "prov-" + name.lower().replace(" ", "-").replace(",", "").replace(".", "")


def provider_link(name):
    """Return an HTML link to a provider's detail section."""
    escaped = esc(name)
    return f'<a href="#{provider_anchor(name)}" title="Jump to detail">{escaped}</a>'


def _load_report_password():
    """Load the report password from config.json, if set."""
    config_file = os.path.join(SCRIPT_DIR, "config.json")
    if os.path.exists(config_file):
        with open(config_file, 'r') as f:
            cfg = json.load(f)
        return cfg.get("report_password", "")
    return ""


def generate_report(assignments, flags, provider_stats, daily_data, all_daily_data=None, password=None):
    """Generate the full HTML report. If password is provided (or set in config.json),
    the report will be password-protected with a client-side gate."""
    sections = []

    sections.append(generate_schedule_table(assignments))
    sections.append(generate_monthly_schedule_tables(assignments))
    sections.append(generate_provider_detail_table(assignments, daily_data, provider_stats, all_daily_data))
    sections.append(generate_summary_by_provider(provider_stats))
    sections.append(generate_weekend_pivot(assignments, provider_stats))
    sections.append(generate_dc1_dc2_balance(provider_stats))
    sections.append(generate_day_of_week_distribution(provider_stats))
    sections.append(generate_teaching_vs_dc_report(assignments, daily_data))
    sections.append(generate_flags_report(flags))
    sections.append(generate_overall_stats(assignments, flags, provider_stats))

    body = "\n".join(sections)

    # Determine password: explicit arg > config.json > none
    pw = password if password is not None else _load_report_password()

    return wrap_html(body, pw)


def wrap_html(body, password=""):
    """Wrap body content in a full HTML page with styles, auto-refresh, sort & filter.
    If password is non-empty, wraps the report in a client-side password gate."""
    generated = datetime.now().strftime("%B %d, %Y at %I:%M %p")

    # Compute SHA-256 hash of the password for client-side verification
    if password:
        pw_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
    else:
        pw_hash = ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Long Call Assignment Report — March–June 2026</title>
<style>
  :root {{
    --bg: #ffffff;
    --text: #1a1a1a;
    --heading: #0d47a1;
    --border: #d0d0d0;
    --stripe: #f5f7fa;
    --highlight: #fff3cd;
    --warn-bg: #fce4ec;
    --warn-text: #b71c1c;
    --weekend-bg: #e3f2fd;
    --link: #1565c0;
    --toc-bg: #f0f4f8;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 13px;
    line-height: 1.5;
    color: var(--text);
    background: var(--bg);
    padding: 24px 32px;
    max-width: 1400px;
    margin: 0 auto;
  }}
  h1 {{ font-size: 24px; color: var(--heading); margin-bottom: 4px; }}
  h2 {{
    font-size: 18px;
    color: var(--heading);
    margin-top: 32px;
    margin-bottom: 12px;
    padding-bottom: 6px;
    border-bottom: 2px solid var(--heading);
  }}
  h3 {{
    font-size: 15px;
    color: #333;
    margin-top: 20px;
    margin-bottom: 8px;
  }}
  .subtitle {{ color: #555; font-size: 14px; margin-bottom: 2px; }}
  .generated {{ color: #888; font-size: 12px; margin-bottom: 20px; }}
  hr {{ border: none; border-top: 1px solid var(--border); margin: 16px 0; }}

  /* Table of Contents */
  .toc {{
    background: var(--toc-bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 16px 24px;
    margin-bottom: 24px;
    display: inline-block;
  }}
  .toc h2 {{ border-bottom: none; margin-top: 0; margin-bottom: 8px; font-size: 16px; }}
  .toc ol {{ padding-left: 20px; }}
  .toc li {{ margin: 3px 0; }}
  .toc a {{ color: var(--link); text-decoration: none; }}
  .toc a:hover {{ text-decoration: underline; }}
  .toc ul {{ list-style: disc; padding-left: 20px; margin: 2px 0; }}

  /* Tables */
  table {{
    border-collapse: collapse;
    width: 100%;
    margin-bottom: 16px;
    font-size: 12px;
  }}
  th, td {{
    border: 1px solid var(--border);
    padding: 4px 8px;
    text-align: left;
    white-space: nowrap;
  }}
  th {{
    background: #e8edf2;
    font-weight: 600;
    position: sticky;
    top: 0;
    z-index: 2;
    cursor: pointer;
    user-select: none;
    vertical-align: top;
  }}
  th .sort-arrow {{ font-size: 10px; color: #888; margin-left: 3px; }}
  th .sort-arrow.active {{ color: var(--heading); font-weight: 700; }}
  th .col-filter {{
    display: block;
    width: 100%;
    margin-top: 3px;
    padding: 1px 4px;
    font-size: 11px;
    font-weight: 400;
    border: 1px solid var(--border);
    border-radius: 3px;
    background: #fff;
    outline: none;
  }}
  th .col-filter:focus {{ border-color: var(--link); box-shadow: 0 0 0 1px var(--link); }}
  th .col-filter::placeholder {{ color: #bbb; }}
  tr:nth-child(even) {{ background: var(--stripe); }}
  tr.weekend {{ background: var(--weekend-bg); font-weight: 600; }}
  tr.holiday {{ background: #fff9c4; font-weight: 600; }}
  tr.filtered-out {{ display: none; }}

  /* Provider links */
  a.prov-link, td a {{ color: var(--link); text-decoration: none; }}
  td a:hover {{ text-decoration: underline; }}

  .flag {{ color: var(--warn-text); font-weight: 700; }}
  .unfilled {{ color: var(--warn-text); font-weight: 700; background: var(--warn-bg); }}
  .warn {{ background: var(--highlight); }}
  .bold {{ font-weight: 700; }}
  .muted {{ color: #999; }}
  .num {{ text-align: right; }}

  /* Provider detail toggles */
  details {{ margin-bottom: 6px; }}
  details summary {{
    cursor: pointer;
    font-weight: 600;
    padding: 4px 0;
  }}
  details summary:hover {{ color: var(--link); }}
  details table {{ margin-top: 6px; }}

  /* Provider detail color coding */
  tr.lc-day {{ background: #c8e6c9; }}           /* green — long call assigned */
  tr.lc-day td {{ font-weight: 600; }}
  tr.moon-day {{ background: #e0e0e0; color: #777; }}  /* grey — moonlighting */
  tr.no-lc-week {{ background: #ffcdd2; }}        /* red-ish — week with no LC */
  tr.week-sep td {{ border-top: 3px solid var(--heading); }}  /* week boundary */
  .lc-badge {{
    display: inline-block;
    background: #2e7d32;
    color: #fff;
    font-size: 10px;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 3px;
    margin-left: 4px;
  }}
  .moon-badge {{
    display: inline-block;
    background: #9e9e9e;
    color: #fff;
    font-size: 10px;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 3px;
    margin-left: 4px;
  }}
  .no-lc-badge {{
    display: inline-block;
    background: var(--warn-text);
    color: #fff;
    font-size: 10px;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 3px;
    margin-left: 4px;
  }}

  .stat-grid {{
    display: grid;
    grid-template-columns: 200px 1fr;
    gap: 2px 12px;
    margin-bottom: 16px;
  }}
  .stat-label {{ font-weight: 600; }}
  .stat-value {{ }}

  .warning-box {{
    background: var(--warn-bg);
    color: var(--warn-text);
    border: 1px solid #ef9a9a;
    border-radius: 4px;
    padding: 8px 12px;
    margin: 8px 0;
    font-weight: 600;
  }}

  /* Floating back-to-top button */
  .back-to-top {{
    position: fixed;
    top: 16px;
    right: 16px;
    z-index: 999;
    background: var(--heading);
    color: #fff;
    border: none;
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    box-shadow: 0 2px 6px rgba(0,0,0,0.25);
    text-decoration: none;
    line-height: 1;
  }}
  .back-to-top:hover {{
    background: #1565c0;
  }}

  /* Password gate */
  #pw-overlay {{
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: #f5f7fa;
    z-index: 10000;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  #pw-overlay.hidden {{ display: none; }}
  #pw-box {{
    background: #fff;
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 40px;
    max-width: 380px;
    width: 90%;
    text-align: center;
    box-shadow: 0 4px 24px rgba(0,0,0,0.1);
  }}
  #pw-box h2 {{ margin-bottom: 8px; font-size: 20px; color: var(--heading); border: none; }}
  #pw-box p {{ color: #666; font-size: 13px; margin-bottom: 20px; }}
  #pw-input {{
    width: 100%;
    padding: 10px 14px;
    font-size: 15px;
    border: 2px solid var(--border);
    border-radius: 6px;
    outline: none;
    margin-bottom: 12px;
  }}
  #pw-input:focus {{ border-color: var(--link); }}
  #pw-btn {{
    width: 100%;
    padding: 10px;
    font-size: 15px;
    font-weight: 600;
    background: var(--heading);
    color: #fff;
    border: none;
    border-radius: 6px;
    cursor: pointer;
  }}
  #pw-btn:hover {{ background: #1565c0; }}
  #pw-error {{ color: var(--warn-text); font-size: 13px; margin-top: 10px; display: none; }}
  #report-content {{ display: none; }}
  #report-content.unlocked {{ display: block; }}
</style>
</head>
<body>

{"" if not pw_hash else '<div id="pw-overlay"><div id="pw-box"><h2>🔒 Password Required</h2><p>This report contains protected information.</p><input type="password" id="pw-input" placeholder="Enter password" autocomplete="off"><button id="pw-btn">Unlock</button><div id="pw-error">Incorrect password</div></div></div>'}

<div id="report-content" class="{"unlocked" if not pw_hash else ""}">

<a href="#" class="back-to-top" title="Back to top">&uarr; Top</a>

<h1>Long Call Assignment Report</h1>
<div class="subtitle">Block: March 2 – June 28, 2026</div>
<div class="generated">Generated: {generated} &nbsp;|&nbsp; <span id="refresh-status">Auto-refresh: watching for file changes</span></div>
<hr>

<nav class="toc">
<h2>Table of Contents</h2>
<ol>
  <li><a href="#full-schedule">Full Schedule</a></li>
  <li><a href="#schedule-by-month">Schedule by Month</a>
    <ul>
      <li><a href="#march-2026">March 2026</a></li>
      <li><a href="#april-2026">April 2026</a></li>
      <li><a href="#may-2026">May 2026</a></li>
      <li><a href="#june-2026">June 2026</a></li>
    </ul>
  </li>
  <li><a href="#provider-detail">Provider Detail — All Long Call Assignments</a></li>
  <li><a href="#provider-summary">Provider Summary</a></li>
  <li><a href="#weekend-pivot">Weekend Long Call Pivot</a></li>
  <li><a href="#dc1-dc2-balance">DC Long Call 1 vs 2 Balance</a></li>
  <li><a href="#dow-distribution">Day of Week Distribution</a></li>
  <li><a href="#teaching-vs-dc">Teaching vs Direct Care Assignment Accuracy</a></li>
  <li><a href="#flags">Flags and Violations</a></li>
  <li><a href="#overall-stats">Overall Statistics</a></li>
</ol>
</nav>

{body}

</div><!-- end report-content -->

<script>
(function() {{
  // ---- Auto-refresh on file change ----
  // Polls the file's Last-Modified header and only reloads when it changes.
  var statusEl = document.getElementById('refresh-status');
  var lastModified = null;
  var pollInterval = 2000; // check every 2 seconds

  function checkForChanges() {{
    fetch(location.href, {{ method: 'HEAD', cache: 'no-store' }})
      .then(function(resp) {{
        var lm = resp.headers.get('Last-Modified');
        if (lastModified === null) {{
          lastModified = lm; // first check — just record it
        }} else if (lm && lm !== lastModified) {{
          if (statusEl) statusEl.textContent = 'File changed — reloading...';
          location.reload();
          return;
        }}
        setTimeout(checkForChanges, pollInterval);
      }})
      .catch(function() {{
        // Network error — just retry later
        setTimeout(checkForChanges, pollInterval);
      }});
  }}

  checkForChanges();

  // ---- Sorting & Filtering for all tables ----
  // Adds filter inputs to each <th> and click-to-sort behavior.

  function parseVal(text) {{
    // Strip non-numeric wrappers to get a sortable value
    var s = text.replace(/[^\\d.\\-/]/g, '').trim();
    // Try date like MM/DD or MM/DD/YYYY
    var dm = s.match(/^(\\d{{1,2}})\\/(\\d{{1,2}})(?:\\/(\\d{{2,4}}))?$/);
    if (dm) {{
      var y = dm[3] ? (dm[3].length === 2 ? 2000 + parseInt(dm[3]) : parseInt(dm[3])) : 2026;
      return new Date(y, parseInt(dm[1]) - 1, parseInt(dm[2])).getTime();
    }}
    var n = parseFloat(s);
    if (!isNaN(n) && s.length > 0) return n;
    return null;
  }}

  function getCellText(cell) {{
    return (cell.textContent || cell.innerText || '').trim();
  }}

  document.querySelectorAll('table').forEach(function(table) {{
    var thead = table.querySelector('thead');
    var tbody = table.querySelector('tbody');
    if (!thead || !tbody) return;

    var ths = thead.querySelectorAll('th');
    if (ths.length < 2) return; // skip tiny key-value tables

    var sortState = {{}};  // colIndex -> 'asc' | 'desc'

    ths.forEach(function(th, colIdx) {{
      // Add sort arrow
      var arrow = document.createElement('span');
      arrow.className = 'sort-arrow';
      arrow.textContent = ' \u25B4\u25BE';
      th.appendChild(arrow);

      // Add filter input
      var input = document.createElement('input');
      input.className = 'col-filter';
      input.type = 'text';
      input.placeholder = 'filter...';
      input.addEventListener('click', function(e) {{ e.stopPropagation(); }});
      input.addEventListener('input', function() {{ applyFilters(); }});
      th.appendChild(input);

      // Sort on header click (but not on input)
      th.addEventListener('click', function(e) {{
        if (e.target.tagName === 'INPUT') return;
        doSort(colIdx);
      }});
    }});

    function applyFilters() {{
      var inputs = thead.querySelectorAll('.col-filter');
      var filters = [];
      inputs.forEach(function(inp, idx) {{
        var v = inp.value.trim().toLowerCase();
        filters.push(v);
      }});

      var rows = tbody.querySelectorAll('tr');
      rows.forEach(function(row) {{
        var cells = row.querySelectorAll('td');
        var show = true;
        filters.forEach(function(f, idx) {{
          if (!f) return;
          if (idx >= cells.length) return;
          var cellText = getCellText(cells[idx]).toLowerCase();
          if (cellText.indexOf(f) === -1) show = false;
        }});
        row.classList.toggle('filtered-out', !show);
      }});
    }}

    function doSort(colIdx) {{
      var dir = sortState[colIdx] === 'asc' ? 'desc' : 'asc';
      sortState = {{}};
      sortState[colIdx] = dir;

      // Update arrows
      ths.forEach(function(th, i) {{
        var arr = th.querySelector('.sort-arrow');
        if (i === colIdx) {{
          arr.className = 'sort-arrow active';
          arr.textContent = dir === 'asc' ? ' \u25B4' : ' \u25BE';
        }} else {{
          arr.className = 'sort-arrow';
          arr.textContent = ' \u25B4\u25BE';
        }}
      }});

      var rows = Array.from(tbody.querySelectorAll('tr'));
      rows.sort(function(a, b) {{
        var ca = a.querySelectorAll('td')[colIdx];
        var cb = b.querySelectorAll('td')[colIdx];
        if (!ca || !cb) return 0;
        var ta = getCellText(ca);
        var tb = getCellText(cb);
        var na = parseVal(ta);
        var nb = parseVal(tb);
        var result;
        if (na !== null && nb !== null) {{
          result = na - nb;
        }} else {{
          result = ta.localeCompare(tb, undefined, {{numeric: true, sensitivity: 'base'}});
        }}
        return dir === 'asc' ? result : -result;
      }});

      rows.forEach(function(row) {{ tbody.appendChild(row); }});
    }}
  }});
  // ---- Auto-open <details> when navigating to a provider anchor ----
  function openTargetDetails() {{
    var hash = location.hash;
    if (!hash) return;
    var el = document.querySelector(hash);
    if (el && el.tagName === 'DETAILS') {{
      el.open = true;
      el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
    }}
  }}
  window.addEventListener('hashchange', openTargetDetails);
  openTargetDetails(); // handle initial load with hash
}})();
</script>

{"" if not pw_hash else '''<script>
(function() {
  var HASH = "''' + pw_hash + '''";
  var overlay = document.getElementById("pw-overlay");
  var content = document.getElementById("report-content");
  var input = document.getElementById("pw-input");
  var btn = document.getElementById("pw-btn");
  var errEl = document.getElementById("pw-error");
  if (!overlay) return; // no password gate

  async function sha256(str) {
    var buf = new TextEncoder().encode(str);
    var hash = await crypto.subtle.digest("SHA-256", buf);
    return Array.from(new Uint8Array(hash)).map(function(b) {
      return b.toString(16).padStart(2, "0");
    }).join("");
  }

  async function tryUnlock() {
    var pw = input.value;
    var h = await sha256(pw);
    if (h === HASH) {
      overlay.classList.add("hidden");
      content.classList.add("unlocked");
    } else {
      errEl.style.display = "block";
      input.value = "";
      input.focus();
    }
  }

  btn.addEventListener("click", tryUnlock);
  input.addEventListener("keydown", function(e) {
    if (e.key === "Enter") tryUnlock();
  });
  input.focus();
})();
</script>
'''}
</body>
</html>"""


def generate_schedule_table(assignments):
    """Main schedule table — all dates."""
    rows = []
    for dt in sorted(assignments.keys()):
        a = assignments[dt]
        date_str = dt.strftime("%m/%d")
        day_str = day_name(dt)

        is_wknd = is_weekend(dt)
        is_hol = is_holiday(dt)
        is_wknd_or_hol = is_weekend_or_holiday(dt)

        if is_hol:
            row_class = "holiday"
            marker = " HOL"
        elif is_wknd:
            row_class = "weekend"
            marker = ""
        else:
            row_class = ""
            marker = ""

        teaching = provider_link(a["teaching"]) if a["teaching"] else '<span class="unfilled">UNFILLED</span>'
        dc2 = provider_link(a["dc2"]) if a["dc2"] else '<span class="unfilled">UNFILLED</span>'

        if is_wknd_or_hol:
            dc1_am = '<span class="muted">—</span>'
            dc1_pm = '<span class="muted">—</span>'
        else:
            dc1_val = provider_link(a["dc1"]) if a["dc1"] else '<span class="unfilled">UNFILLED</span>'
            dc1_am = dc1_val
            dc1_pm = dc1_val

        rows.append(f'<tr class="{row_class}">'
                     f'<td>{date_str}</td><td>{day_str}{marker}</td>'
                     f'<td>{teaching}</td><td>{dc1_am}</td><td>{dc1_pm}</td><td>{dc2}</td></tr>')

    return f"""
<h2 id="full-schedule">Full Schedule</h2>
<table>
<thead><tr>
  <th>Date</th><th>Day</th>
  <th>Teaching LC (5p-7p)</th>
  <th>DC LC 1 AM (7a-8a)</th><th>DC LC 1 PM (5p-7p)</th>
  <th>DC LC 2 PM (5p-7p)</th>
</tr></thead>
<tbody>
{"".join(rows)}
</tbody>
</table>"""


def generate_monthly_schedule_tables(assignments):
    """Separate tables for each month."""
    months = defaultdict(list)
    for dt in sorted(assignments.keys()):
        months[dt.strftime("%B %Y")].append(dt)

    parts = ['<h2 id="schedule-by-month">Schedule by Month</h2>']

    for month_name, dates in months.items():
        anchor = month_name.lower().replace(" ", "-")
        rows = []
        for dt in dates:
            a = assignments[dt]
            date_str = dt.strftime("%m/%d")
            day_str = day_name(dt)
            is_wknd_or_hol = is_weekend_or_holiday(dt)
            is_hol = is_holiday(dt)

            if is_hol:
                row_class = "holiday"
                marker = " HOL"
            elif is_weekend(dt):
                row_class = "weekend"
                marker = ""
            else:
                row_class = ""
                marker = ""

            teaching = provider_link(a["teaching"]) if a["teaching"] else '<span class="unfilled">UNFILLED</span>'
            dc2 = provider_link(a["dc2"]) if a["dc2"] else '<span class="unfilled">UNFILLED</span>'

            if is_wknd_or_hol:
                dc1 = '<span class="muted">—</span>'
            else:
                dc1 = provider_link(a["dc1"]) if a["dc1"] else '<span class="unfilled">UNFILLED</span>'

            rows.append(f'<tr class="{row_class}"><td>{date_str}</td><td>{day_str}{marker}</td>'
                        f'<td>{teaching}</td><td>{dc1}</td><td>{dc2}</td></tr>')

        parts.append(f"""
<h3 id="{anchor}">{month_name}</h3>
<table>
<thead><tr><th>Date</th><th>Day</th><th>Teaching LC</th><th>DC LC 1</th><th>DC LC 2</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>""")

    return "\n".join(parts)


def generate_provider_detail_table(assignments, daily_data, provider_stats, all_daily_data=None):
    """Collapsible full-schedule detail for each provider, color-coded."""
    # Build LC lookup: (date, provider) -> slot label
    lc_lookup = {}
    for dt in sorted(assignments.keys()):
        a = assignments[dt]
        for slot, label in [("teaching", "Teaching LC"), ("dc1", "DC LC 1"), ("dc2", "DC LC 2")]:
            if a[slot]:
                lc_lookup[(dt, a[slot])] = label

    # Build stretches from source-service days only
    all_stretches = identify_stretches(daily_data)

    # Build set of source-service days per provider
    source_days = set()
    for dt, providers in daily_data.items():
        for p in providers:
            source_days.add((p["provider"], dt))

    # Build set of all-service days per provider (for showing non-source context)
    all_service_days = {}  # provider -> set of dates
    if all_daily_data:
        for dt, providers in all_daily_data.items():
            for p in providers:
                prov = p["provider"]
                if prov not in all_service_days:
                    all_service_days[prov] = set()
                all_service_days[prov].add(dt)

    # Collect all providers that appear in stats (includes those with LCs)
    all_providers = set(provider_stats.keys())

    parts = ['<h2 id="provider-detail">Provider Detail — Full Schedule</h2>',
             '<p style="margin-bottom:12px">'
             '<span class="lc-badge">LC</span> = Long Call assigned &nbsp; '
             '<span class="moon-badge">MOON</span> = Moonlighting &nbsp; '
             '<span class="moon-badge">NON-SOURCE</span> = Non-source service (not eligible for LC) &nbsp; '
             '<span class="no-lc-badge">NO LC</span> = Week without long call'
             '</p>']

    for provider in sorted(all_providers):
        if provider in EXCLUDED_PROVIDERS:
            continue
        s = provider_stats.get(provider, {})
        total_lc = s.get("total_long_calls", 0)
        weeks = s.get("weeks_worked", "?")
        wknds = s.get("weekends_worked", "?")
        standalone_wknds = s.get("standalone_weekends", 0)
        stretch_count = s.get("stretches", "?")

        stretches = all_stretches.get(provider, [])
        if not stretches:
            continue

        # Build display weeks: separate standalone weekends from real stretches.
        # For real stretches, also insert non-source days from all_daily_data
        # that fall within the stretch date range for context display.
        all_weeks = []  # list of (week_dates, is_moonlighting, is_standalone)
        for stretch in stretches:
            is_moon = is_moonlighting_in_stretch(provider, stretch, daily_data)
            standalone = is_standalone_weekend(stretch)

            if standalone:
                all_weeks.append((stretch, is_moon, True))
            else:
                # For real stretches, expand to include non-source days
                # that bridge gaps within the stretch for display purposes
                expanded_stretch = list(stretch)
                if all_daily_data:
                    start_dt = stretch[0]
                    end_dt = stretch[-1]
                    prov_all_days = all_service_days.get(provider, set())
                    for d in prov_all_days:
                        if start_dt <= d <= end_dt and d not in expanded_stretch:
                            expanded_stretch.append(d)
                    expanded_stretch.sort()

                sub_weeks = split_stretch_into_weeks(expanded_stretch)
                for week in sub_weeks:
                    all_weeks.append((week, is_moon, False))

        # Build rows
        rows = []
        week_num = 0
        for week_dates, is_moon, is_standalone in all_weeks:
            week_num += 1
            # Check if this week has a long call
            week_has_lc = any((dt, provider) in lc_lookup for dt in week_dates)
            has_source = any((provider, dt) in source_days for dt in week_dates)
            is_no_lc_week = not week_has_lc and not is_moon and has_source and not is_standalone

            # Count source-service days in this week for display
            source_count = sum(1 for dt in week_dates if (provider, dt) in source_days)

            # Week label for standalone weekends
            week_label = f"SW{week_num}" if is_standalone else str(week_num)

            for day_idx, dt in enumerate(week_dates):
                date_str = dt.strftime("%m/%d")
                day_str = day_name(dt)
                is_source_day = (provider, dt) in source_days

                # Get service for this day — try source data first, then all_daily_data
                service = ""
                category = ""
                is_moon_day = False
                if is_source_day and dt in daily_data:
                    for p in daily_data[dt]:
                        if p["provider"] == provider:
                            service = p["service"]
                            category = p["category"]
                            is_moon_day = p["moonlighting"]
                            break
                elif all_daily_data and dt in all_daily_data:
                    for p in all_daily_data[dt]:
                        if p["provider"] == provider:
                            service = p["service"]
                            category = "non_source"
                            is_moon_day = p["moonlighting"]
                            break

                # Check for LC on this day
                lc_slot = lc_lookup.get((dt, provider), "")

                # Determine row class
                row_classes = []
                if day_idx == 0 and week_num > 1:
                    row_classes.append("week-sep")
                if lc_slot:
                    row_classes.append("lc-day")
                elif is_moon_day:
                    row_classes.append("moon-day")
                elif not is_source_day:
                    row_classes.append("moon-day")  # grey for non-source
                elif is_no_lc_week:
                    row_classes.append("no-lc-week")
                elif is_weekend_or_holiday(dt):
                    row_classes.append("weekend")

                row_cls = f' class="{" ".join(row_classes)}"' if row_classes else ""

                # Week number column (only on first day of week)
                wk_cell = f'<td class="num" rowspan="{len(week_dates)}">{week_label}</td>' if day_idx == 0 else ""

                # Holiday marker
                hol_marker = " HOL" if is_holiday(dt) else ""

                # Badges
                badges = ""
                if lc_slot:
                    badges = f' <span class="lc-badge">{esc(lc_slot)}</span>'
                elif is_moon_day:
                    badges = ' <span class="moon-badge">MOON</span>'
                elif not is_source_day:
                    badges = ' <span class="moon-badge">NON-SOURCE</span>'

                # No-LC badge on first day of a no-LC week (only for weeks with source days)
                no_lc_marker = ""
                if is_no_lc_week and day_idx == 0 and source_count > 0:
                    no_lc_marker = ' <span class="no-lc-badge">NO LC</span>'

                rows.append(f'<tr{row_cls}>{wk_cell}'
                            f'<td>{date_str}</td><td>{day_str}{hol_marker}</td>'
                            f'<td>{esc(service)}</td>'
                            f'<td>{esc(category)}{badges}{no_lc_marker}</td></tr>')

        parts.append(f"""
<details id="{provider_anchor(provider)}">
<summary>{esc(provider)} — {total_lc} long calls | Weeks: {weeks} | Weekends: {wknds} ({standalone_wknds} standalone) | Stretches: {stretch_count}</summary>
<table>
<thead><tr><th>Wk</th><th>Date</th><th>Day</th><th>Service</th><th>Details</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>
</details>""")

    return "\n".join(parts)


def generate_summary_by_provider(provider_stats):
    """Summary table of all providers."""
    rows = []
    for provider in sorted(provider_stats.keys()):
        s = provider_stats[provider]
        if s["total_long_calls"] == 0 and s["missed"] == 0:
            continue

        teaching_count = s["total_long_calls"] - s["dc1_count"] - s["dc2_count"]

        missed_cls = ' class="flag"' if s['missed'] > 0 else ""
        doubles_cls = ' class="flag"' if s['doubles'] > 0 else ""

        standalone_wknds = s.get("standalone_weekends", 0)
        stretch_count = s.get("stretches", 0)

        rows.append(f'<tr><td>{provider_link(provider)}</td>'
                     f'<td class="num">{s["weeks_worked"]}</td>'
                     f'<td class="num">{s["weekends_worked"]}</td>'
                     f'<td class="num">{standalone_wknds}</td>'
                     f'<td class="num">{stretch_count}</td>'
                     f'<td class="num">{s["total_long_calls"]}</td>'
                     f'<td class="num">{s["weekend_long_calls"]}</td>'
                     f'<td class="num">{s["dc1_count"]}</td>'
                     f'<td class="num">{s["dc2_count"]}</td>'
                     f'<td class="num">{teaching_count}</td>'
                     f'<td class="num"{missed_cls}>{s["missed"]}</td>'
                     f'<td class="num"{doubles_cls}>{s["doubles"]}</td></tr>')

    return f"""
<h2 id="provider-summary">Provider Summary</h2>
<table>
<thead><tr>
  <th>Provider</th><th>Weeks</th><th>Wknds</th><th>Standalone Wknds</th><th>Stretches</th>
  <th>Total LC</th><th>Wknd LC</th><th>DC1</th><th>DC2</th>
  <th>Teaching</th><th>Missed</th><th>Doubles</th>
</tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>"""


def generate_weekend_pivot(assignments, provider_stats):
    """Pivot showing weekend long call assignments."""
    weekend_assigns = []
    for dt in sorted(assignments.keys()):
        if not is_weekend_or_holiday(dt):
            continue
        a = assignments[dt]
        for slot, label in [("teaching", "Teaching"), ("dc2", "DC 2")]:
            if a[slot]:
                weekend_assigns.append({
                    "date": dt,
                    "provider": a[slot],
                    "slot": label,
                    "holiday": is_holiday(dt),
                })

    # Assignment rows
    assign_rows = []
    for wa in weekend_assigns:
        date_str = wa["date"].strftime("%m/%d")
        day_str = day_name(wa["date"])
        hol = "YES" if wa["holiday"] else ""
        hol_cls = ' class="holiday"' if wa["holiday"] else ""
        assign_rows.append(f'<tr{hol_cls}><td>{date_str}</td><td>{day_str}</td>'
                           f'<td>{esc(wa["slot"])}</td><td>{provider_link(wa["provider"])}</td>'
                           f'<td>{hol}</td></tr>')

    # Per-provider counts
    wknd_counts = defaultdict(int)
    for wa in weekend_assigns:
        wknd_counts[wa["provider"]] += 1

    provider_rows = []
    for provider in sorted(wknd_counts.keys()):
        wknds_worked = provider_stats.get(provider, {}).get("weekends_worked", 0)
        weeks_worked = provider_stats.get(provider, {}).get("weeks_worked", 0)
        count = wknd_counts[provider]
        flag_cls = ' class="flag"' if count > 1 else ""
        flag_text = " [2+ WKND LCs]" if count > 1 else ""
        provider_rows.append(f'<tr><td{flag_cls}>{provider_link(provider)}{flag_text}</td>'
                             f'<td class="num">{count}</td>'
                             f'<td class="num">{weeks_worked}</td>'
                             f'<td class="num">{wknds_worked}</td></tr>')

    multi = [p for p, c in wknd_counts.items() if c > 1]
    warning = ""
    if multi:
        warning = f'<div class="warning-box">{len(multi)} provider(s) with 2+ weekend long calls: {", ".join(esc(p) for p in multi)}</div>'

    return f"""
<h2 id="weekend-pivot">Weekend Long Call Pivot</h2>
<h3>Weekend/Holiday Assignments</h3>
<table>
<thead><tr><th>Date</th><th>Day</th><th>Slot</th><th>Provider</th><th>Holiday?</th></tr></thead>
<tbody>{"".join(assign_rows)}</tbody>
</table>

<h3>Weekend Long Calls per Provider</h3>
<table>
<thead><tr><th>Provider</th><th>Weekend LCs</th><th>Weeks Worked</th><th>Weekends Worked</th></tr></thead>
<tbody>{"".join(provider_rows)}</tbody>
</table>
{warning}"""


def generate_dc1_dc2_balance(provider_stats):
    """Show DC1 vs DC2 balance per provider."""
    rows = []
    for provider in sorted(provider_stats.keys()):
        s = provider_stats[provider]
        dc1 = s["dc1_count"]
        dc2 = s["dc2_count"]
        if dc1 == 0 and dc2 == 0:
            continue

        diff = abs(dc1 - dc2)
        if diff <= 1:
            balanced = "Yes"
            bal_cls = ""
        else:
            balanced = f"No ({diff})"
            bal_cls = ' class="flag"'

        rows.append(f'<tr><td>{provider_link(provider)}</td>'
                     f'<td class="num">{dc1}</td><td class="num">{dc2}</td>'
                     f'<td class="num">{diff}</td><td{bal_cls}>{balanced}</td></tr>')

    return f"""
<h2 id="dc1-dc2-balance">DC Long Call 1 vs 2 Balance</h2>
<table>
<thead><tr><th>Provider</th><th>DC1</th><th>DC2</th><th>Difference</th><th>Balanced?</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>"""


def generate_day_of_week_distribution(provider_stats):
    """Show day-of-week distribution for each provider."""
    rows = []
    for provider in sorted(provider_stats.keys()):
        s = provider_stats[provider]
        if s["total_long_calls"] == 0:
            continue

        dow_counts = [0] * 7
        for d in s["days_of_week"]:
            dow_counts[d] += 1

        unique = sum(1 for c in dow_counts if c > 0)
        total = s["total_long_calls"]

        cells = []
        for c in dow_counts:
            if c >= 2:
                cells.append(f'<td class="num flag">{c}</td>')
            elif c == 0:
                cells.append('<td class="num muted">·</td>')
            else:
                cells.append(f'<td class="num">{c}</td>')

        rows.append(f'<tr><td>{provider_link(provider)}</td>{"".join(cells)}'
                     f'<td class="num">{unique}/{total}</td></tr>')

    return f"""
<h2 id="dow-distribution">Day of Week Distribution</h2>
<table>
<thead><tr><th>Provider</th><th>Mon</th><th>Tue</th><th>Wed</th><th>Thu</th><th>Fri</th><th>Sat</th><th>Sun</th><th>Unique Days</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>"""


def generate_teaching_vs_dc_report(assignments, daily_data):
    """Show if teaching providers got teaching LC and DC providers got DC LC."""
    correct = 0
    overflow = 0
    cross_assign = 0
    total = 0
    mismatches = []

    for dt in sorted(assignments.keys()):
        a = assignments[dt]
        for slot, slot_type in [("teaching", "teaching"), ("dc1", "direct_care"), ("dc2", "direct_care")]:
            provider = a[slot]
            if not provider:
                continue
            total += 1

            cat = get_provider_category(provider, dt, daily_data)
            if cat is None:
                continue

            if slot_type == "teaching" and cat == "teaching":
                correct += 1
            elif slot_type == "direct_care" and cat == "direct_care":
                correct += 1
            elif slot_type == "direct_care" and cat == "teaching":
                overflow += 1
                mismatches.append({
                    "date": dt.strftime("%m/%d"),
                    "provider": provider,
                    "service_cat": cat,
                    "assigned_slot": slot,
                    "type": "Teaching→DC (overflow)",
                })
            elif slot_type == "teaching" and cat == "direct_care":
                cross_assign += 1
                mismatches.append({
                    "date": dt.strftime("%m/%d"),
                    "provider": provider,
                    "service_cat": cat,
                    "assigned_slot": slot,
                    "type": "DC→Teaching",
                })

    pct_correct = (correct / total * 100) if total else 0

    mismatch_rows = []
    for m in mismatches[:50]:
        mismatch_rows.append(f'<tr><td>{m["date"]}</td><td>{provider_link(m["provider"])}</td>'
                             f'<td>{m["service_cat"]}</td><td>{m["assigned_slot"]}</td>'
                             f'<td>{m["type"]}</td></tr>')

    overflow_text = ""
    if mismatches:
        more = f"<p><em>...and {len(mismatches) - 50} more</em></p>" if len(mismatches) > 50 else ""
        overflow_text = f"""
<h3>Cross-Category Assignments</h3>
<table>
<thead><tr><th>Date</th><th>Provider</th><th>Service Category</th><th>Assigned Slot</th><th>Type</th></tr></thead>
<tbody>{"".join(mismatch_rows)}</tbody>
</table>
{more}"""

    return f"""
<h2 id="teaching-vs-dc">Teaching vs Direct Care Assignment Accuracy</h2>
<div class="stat-grid">
  <span class="stat-label">Total assignments:</span><span class="stat-value">{total}</span>
  <span class="stat-label">Correct match:</span><span class="stat-value">{correct} ({pct_correct:.1f}%)</span>
  <span class="stat-label">Teaching overflow into DC:</span><span class="stat-value">{overflow}</span>
  <span class="stat-label">DC provider assigned Teaching:</span><span class="stat-value">{cross_assign}</span>
</div>
{overflow_text}"""


def generate_flags_report(flags):
    """Detailed flags and violations report."""
    by_type = defaultdict(list)
    for f in flags:
        by_type[f["flag_type"]].append(f)

    parts = ['<h2 id="flags">Flags and Violations</h2>']

    for ftype, title in [("CONSEC_NO_LC", "Consecutive Weeks Without Long Call"),
                          ("GUARANTEED_SWAP", "Guaranteed Swap (Minimum LC)"),
                          ("MISSED_SWAP", "Missed Week Swap (Max 1 Missed Rule)"),
                          ("WKND_LC_SWAP", "Weekend LC Balance Swap"),
                          ("DOUBLE_LONGCALL", "Double Long Call"),
                          ("UNFILLED_SLOT", "Unfilled Slot"),
                          ("NO_LONGCALL", "No Long Call")]:
        flist = by_type.get(ftype, [])
        if not flist:
            continue

        rows = []
        for f in flist:
            prov = provider_link(f["provider"]) if f["provider"] != "UNFILLED" else esc(f["provider"])
            rows.append(f'<tr><td>{f["date"]}</td><td>{prov}</td>'
                        f'<td>{esc(f["message"])}</td></tr>')

        parts.append(f"""
<h3 class="flag">{title} ({len(flist)} occurrences)</h3>
<table>
<thead><tr><th>Date</th><th>Provider</th><th>Details</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>""")

    return "\n".join(parts)


def generate_overall_stats(assignments, flags, provider_stats):
    """Overall statistics."""
    total_days = len(assignments)
    weekdays = sum(1 for dt in assignments if not is_weekend_or_holiday(dt))
    weekends = sum(1 for dt in assignments if is_weekend(dt))
    holidays = sum(1 for dt in assignments if is_holiday(dt))

    total_slots_filled = sum(1 for dt in assignments
                             for slot in ["teaching", "dc1", "dc2"]
                             if assignments[dt].get(slot))
    unfilled = sum(1 for f in flags if f["flag_type"] == "UNFILLED_SLOT")
    doubles = sum(1 for f in flags if f["flag_type"] == "DOUBLE_LONGCALL")
    missed = sum(1 for f in flags if f["flag_type"] == "NO_LONGCALL")

    active_providers = sum(1 for p, s in provider_stats.items() if s["total_long_calls"] > 0)
    total_providers = len(provider_stats)

    lc_counts = [s["total_long_calls"] for s in provider_stats.values() if s["total_long_calls"] > 0]
    avg_lc = f"{sum(lc_counts) / len(lc_counts):.1f}" if lc_counts else "—"
    min_lc = min(lc_counts) if lc_counts else "—"
    max_lc = max(lc_counts) if lc_counts else "—"

    return f"""
<h2 id="overall-stats">Overall Statistics</h2>
<table>
<thead><tr><th>Metric</th><th>Value</th></tr></thead>
<tbody>
<tr><td>Block period</td><td>Mar 2 – Jun 28, 2026</td></tr>
<tr><td>Total days</td><td class="num">{total_days}</td></tr>
<tr><td>Weekdays</td><td class="num">{weekdays}</td></tr>
<tr><td>Weekends</td><td class="num">{weekends}</td></tr>
<tr><td>Holidays</td><td class="num">{holidays}</td></tr>
<tr><td>Total slots filled</td><td class="num">{total_slots_filled}</td></tr>
<tr><td>Unfilled slots</td><td class="num">{unfilled}</td></tr>
<tr><td>Double long calls</td><td class="num">{doubles}</td></tr>
<tr><td>Missed long calls (surplus)</td><td class="num">{missed}</td></tr>
<tr><td>Active providers (with LC)</td><td class="num">{active_providers}</td></tr>
<tr><td>Total providers in pool</td><td class="num">{total_providers}</td></tr>
<tr><td>Total flags</td><td class="num">{len(flags)}</td></tr>
<tr><td>Avg long calls per provider</td><td class="num">{avg_lc}</td></tr>
<tr><td>Min long calls</td><td class="num">{min_lc}</td></tr>
<tr><td>Max long calls</td><td class="num">{max_lc}</td></tr>
</tbody>
</table>"""


def generate_index_html(reports_dir, block_label):
    """Generate or update the index.html for a block's report folder.
    Scans the folder for longcall_report_*.html files and builds the listing."""
    import glob

    index_path = os.path.join(reports_dir, "index.html")

    # Find all report HTML files
    report_files = sorted(glob.glob(os.path.join(reports_dir, "longcall_report_*.html")))
    filenames = [os.path.basename(f) for f in report_files]

    # Build the JS array
    js_entries = ",\n".join(f'  "{fn}"' for fn in filenames)

    # Password hash
    pw = _load_report_password()
    pw_hash = hashlib.sha256(pw.encode('utf-8')).hexdigest() if pw else ""

    # Password gate HTML (only if password is set)
    if pw_hash:
        pw_overlay = '''<div id="pw-overlay">
  <div id="pw-box">
    <h2>Password Required</h2>
    <p>This site contains protected schedule information.</p>
    <input type="password" id="pw-input" placeholder="Enter password" autocomplete="off">
    <button id="pw-btn">Unlock</button>
    <div id="pw-error">Incorrect password</div>
  </div>
</div>'''
        content_class = ""
    else:
        pw_overlay = ""
        content_class = "unlocked"

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Long Call Schedule Variations — {esc(block_label)}</title>
<style>
  :root {{
    --bg: #ffffff;
    --text: #1a1a1a;
    --heading: #0d47a1;
    --border: #d0d0d0;
    --link: #1565c0;
    --card-bg: #f5f7fa;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 15px;
    line-height: 1.6;
    color: var(--text);
    background: var(--bg);
    padding: 24px;
    max-width: 700px;
    margin: 0 auto;
  }}
  h1 {{ font-size: 22px; color: var(--heading); margin-bottom: 4px; }}
  .subtitle {{ color: #555; font-size: 14px; margin-bottom: 20px; }}
  .report-list {{ list-style: none; padding: 0; }}
  .report-list li {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    margin-bottom: 10px;
    transition: box-shadow 0.15s;
  }}
  .report-list li:hover {{ box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
  .report-list a {{
    display: block;
    padding: 16px 20px;
    color: var(--link);
    text-decoration: none;
    font-weight: 600;
    font-size: 16px;
  }}
  .report-list a:hover {{ text-decoration: underline; }}
  .report-list .meta {{ font-size: 12px; color: #888; font-weight: 400; }}

  #pw-overlay {{
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: #f5f7fa; z-index: 10000;
    display: flex; align-items: center; justify-content: center;
  }}
  #pw-overlay.hidden {{ display: none; }}
  #pw-box {{
    background: #fff; border: 1px solid var(--border); border-radius: 12px;
    padding: 40px; max-width: 380px; width: 90%; text-align: center;
    box-shadow: 0 4px 24px rgba(0,0,0,0.1);
  }}
  #pw-box h2 {{ margin-bottom: 8px; font-size: 20px; color: var(--heading); }}
  #pw-box p {{ color: #666; font-size: 13px; margin-bottom: 20px; }}
  #pw-input {{
    width: 100%; padding: 10px 14px; font-size: 15px;
    border: 2px solid var(--border); border-radius: 6px; outline: none; margin-bottom: 12px;
  }}
  #pw-input:focus {{ border-color: var(--link); }}
  #pw-btn {{
    width: 100%; padding: 10px; font-size: 15px; font-weight: 600;
    background: var(--heading); color: #fff; border: none; border-radius: 6px; cursor: pointer;
  }}
  #pw-btn:hover {{ background: #1565c0; }}
  #pw-error {{ color: #b71c1c; font-size: 13px; margin-top: 10px; display: none; }}
  #report-content {{ display: none; }}
  #report-content.unlocked {{ display: block; }}
</style>
</head>
<body>

{pw_overlay}

<div id="report-content" class="{content_class}">
  <h1>Long Call Schedule Variations</h1>
  <div class="subtitle">Block: {esc(block_label)}</div>

  <ul class="report-list" id="report-list">
    <li><em style="padding:16px 20px; display:block; color:#888;">Loading report list&hellip;</em></li>
  </ul>
</div>

<script>
var REPORT_FILES = [
{js_entries}
];

(function() {{
  var HASH = "{pw_hash}";
  var overlay = document.getElementById("pw-overlay");
  var content = document.getElementById("report-content");
  var input = document.getElementById("pw-input");
  var btn = document.getElementById("pw-btn");
  var errEl = document.getElementById("pw-error");

  if (overlay && HASH) {{
    async function sha256(str) {{
      var buf = new TextEncoder().encode(str);
      var hash = await crypto.subtle.digest("SHA-256", buf);
      return Array.from(new Uint8Array(hash)).map(function(b) {{
        return b.toString(16).padStart(2, "0");
      }}).join("");
    }}

    async function tryUnlock() {{
      var pw = input.value;
      var h = await sha256(pw);
      if (h === HASH) {{
        overlay.classList.add("hidden");
        content.classList.add("unlocked");
      }} else {{
        errEl.style.display = "block";
        input.value = "";
        input.focus();
      }}
    }}

    btn.addEventListener("click", tryUnlock);
    input.addEventListener("keydown", function(e) {{
      if (e.key === "Enter") tryUnlock();
    }});
    input.focus();
  }}

  var listEl = document.getElementById("report-list");
  var items = REPORT_FILES.map(function(f, i) {{
    var seed = f.match(/_([a-f0-9]+)\\.html$/);
    var seedStr = seed ? seed[1] : "";
    var ts = f.match(/_(\\d{{8}}_\\d{{6}})_/);
    var dateStr = "";
    if (ts) {{
      var d = ts[1];
      dateStr = d.substring(0,4) + "-" + d.substring(4,6) + "-" + d.substring(6,8) +
                " " + d.substring(9,11) + ":" + d.substring(11,13) + ":" + d.substring(13,15);
    }}
    return '<li><a href="' + f + '">Variation ' + (i + 1) +
           '<br><span class="meta">Seed: ' + seedStr + ' &nbsp;|&nbsp; Generated: ' + dateStr + '</span></a></li>';
  }});
  listEl.innerHTML = items.join("");
}})();
</script>

</body>
</html>'''

    with open(index_path, 'w') as f:
        f.write(html)

    print(f"Wrote index.html with {len(filenames)} report(s)")


if __name__ == "__main__":
    import sys
    import assign_longcall as engine

    # Determine how many reports to generate (default 1)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1

    # Reports go into a block-specific subfolder: output/reports/{start}_{end}/
    block_start_str = BLOCK_START.strftime("%Y-%m-%d")
    block_end_str = BLOCK_END.strftime("%Y-%m-%d")
    block_folder = f"{block_start_str}_{block_end_str}"
    block_label = f"{BLOCK_START.strftime('%B %d, %Y')} – {BLOCK_END.strftime('%B %d, %Y')}"

    REPORTS_DIR = os.path.join(OUTPUT_DIR, "reports", block_folder)
    os.makedirs(REPORTS_DIR, exist_ok=True)

    print(f"Block: {block_label}")
    print(f"Reports folder: {REPORTS_DIR}")

    print("Loading schedule data...")
    data = load_schedule()

    print("Building daily provider data...")
    daily_data = build_daily_data(data)
    all_daily_data = build_all_daily_data(data)

    for i in range(count):
        # Each run gets a fresh random seed (uuid already set at import,
        # but re-randomise for runs 2+)
        if i > 0:
            import uuid
            engine.VARIATION_SEED = uuid.uuid4().hex[:8]

        seed = engine.VARIATION_SEED
        print(f"\n--- Report {i+1}/{count}  (seed: {seed}) ---")

        print("Running assignment engine...")
        assignments, flags, provider_stats = assign_long_calls(daily_data, all_daily_data)

        print("Generating HTML report...")
        report = generate_report(assignments, flags, provider_stats, daily_data, all_daily_data)

        # Timestamped filename for uniqueness
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"longcall_report_{timestamp}_{seed}.html"
        output_file = os.path.join(REPORTS_DIR, filename)
        with open(output_file, 'w') as f:
            f.write(report)

        print(f"Wrote report to: {output_file}")
        print(f"Report size: {len(report):,} characters")

        # Brief pause to ensure next timestamp is different
        if i < count - 1:
            import time
            time.sleep(1)

    # Generate/update index.html for this block
    generate_index_html(REPORTS_DIR, block_label)
