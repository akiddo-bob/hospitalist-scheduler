#!/usr/bin/env bash
#
# Deploy HTML reports to GitHub Pages (gh-pages branch).
#
# Because output/** is encrypted by git-crypt, we can't just commit them
# normally. This script copies plaintext files to a temp dir FIRST, then
# switches branches safely.
#
# Each page gets a simple JavaScript password gate so the reports
# aren't openly browsable (the password is "hospitalist").
#
# Usage:
#   ./deploy_pages.sh          # regenerate reports and deploy
#   ./deploy_pages.sh --skip   # deploy existing output/ without regenerating
#

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

# ── 1. Optionally regenerate reports ──────────────────────────────
if [[ "${1:-}" != "--skip" ]]; then
    echo "▸ Regenerating reports..."
    .venv/bin/python3 generate_block_report.py
    echo
fi

# ── 2. Verify output files exist ─────────────────────────────────
HTML_FILES=(
    output/index.html
    output/inputs.html
    output/rules.html
    output/block_schedule_report_v1.html
    output/block_schedule_report_v2.html
    output/block_schedule_report_v3.html
    output/block_schedule_report_v4.html
    output/block_schedule_report_v5.html
)

for f in "${HTML_FILES[@]}"; do
    if [[ ! -f "$f" ]]; then
        echo "ERROR: Missing $f — run generate_block_report.py first"
        exit 1
    fi
    # Verify it's actually HTML, not git-crypt encrypted
    if head -c 10 "$f" | grep -q "GITCRYPT"; then
        echo "ERROR: $f is git-crypt encrypted."
        echo "  Delete the encrypted files and regenerate:"
        echo "    rm output/*.html && .venv/bin/python3 generate_block_report.py"
        exit 1
    fi
done

echo "▸ All ${#HTML_FILES[@]} HTML files verified (plaintext)"

# ── 3. Build gh-pages content in a temp directory ─────────────────
#    IMPORTANT: We do this BEFORE any branch switching so files are
#    safely copied out of the working tree first.
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# Copy report files into a reports/ subdirectory and create a gate index.html
.venv/bin/python3 - "$TMPDIR" "${HTML_FILES[@]}" << 'PYTHON_SCRIPT'
import sys, os, hashlib

tmpdir = sys.argv[1]
files = sys.argv[2:]

# The password — change this to whatever you want
PASSWORD = "hospitalist"
pw_hash = hashlib.sha256(PASSWORD.encode()).hexdigest()

# Create reports subdirectory for the actual HTML files
reports_dir = os.path.join(tmpdir, "reports")
os.makedirs(reports_dir, exist_ok=True)

for filepath in files:
    basename = os.path.basename(filepath)
    # Copy the real report into reports/ subdirectory
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    with open(os.path.join(reports_dir, basename), 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"  Copied: reports/{basename}")

# Create the gate index.html at root — this is the only entry point.
# It shows a password form, then loads the real index in a full-page iframe.
# All navigation happens inside the iframe so the gate never re-appears.
gate = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hospitalist Scheduler</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ height: 100%; overflow: hidden; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #f5f5f5; }}
  #gate {{ display: flex; align-items: center; justify-content: center;
           height: 100%; }}
  .card {{ background: #fff; padding: 2.5rem; border-radius: 12px;
           box-shadow: 0 4px 24px rgba(0,0,0,0.1); text-align: center;
           max-width: 380px; width: 90%; }}
  .card h2 {{ color: #1565c0; margin-bottom: 0.5rem; }}
  .card p {{ color: #666; font-size: 0.9rem; margin-bottom: 1.5rem; }}
  .card input {{ width: 100%; padding: 0.75rem 1rem; border: 2px solid #e0e0e0;
                 border-radius: 8px; font-size: 1rem; outline: none; }}
  .card input:focus {{ border-color: #1565c0; }}
  .card button {{ width: 100%; padding: 0.75rem; margin-top: 1rem;
                  background: #1565c0; color: #fff; border: none;
                  border-radius: 8px; font-size: 1rem; cursor: pointer; }}
  .card button:hover {{ background: #0d47a1; }}
  .err {{ color: #d32f2f; font-size: 0.85rem; margin-top: 0.75rem; display: none; }}
  #app {{ display: none; width: 100%; height: 100%; border: none; }}
</style>
</head>
<body>
<div id="gate">
  <div class="card">
    <h2>Hospitalist Scheduler</h2>
    <p>Enter the password to view reports</p>
    <form onsubmit="return checkPw()">
      <input type="password" id="pw" placeholder="Password" autofocus>
      <button type="submit">View Reports</button>
    </form>
    <div class="err" id="err">Incorrect password</div>
  </div>
</div>
<iframe id="app" src="about:blank"></iframe>
<script>
var H="{pw_hash}";
function sha256(m){{return crypto.subtle.digest("SHA-256",new TextEncoder().encode(m)).then(function(b){{return Array.from(new Uint8Array(b)).map(function(x){{return x.toString(16).padStart(2,"0")}}).join("")}})}}
function showApp(){{
  document.getElementById("gate").style.display="none";
  var f=document.getElementById("app");
  f.style.display="block";
  f.src="reports/index.html";
}}
function checkPw(){{
  var p=document.getElementById("pw").value;
  sha256(p).then(function(h){{
    if(h===H){{sessionStorage.setItem("hs_auth","1");showApp()}}
    else{{document.getElementById("err").style.display="block"}}
  }});
  return false;
}}
if(sessionStorage.getItem("hs_auth")==="1"){{showApp()}}
</script>
</body>
</html>'''

with open(os.path.join(tmpdir, "index.html"), 'w', encoding='utf-8') as f:
    f.write(gate)
print("  Created: index.html (password gate)")
print(f"Password: {PASSWORD}")
PYTHON_SCRIPT

echo "▸ All files password-protected and staged in temp dir"

# ── 4. Switch to gh-pages branch using git worktree ───────────────
#    This avoids touching the main working tree at all.
WORKTREE="$TMPDIR/worktree"

if git show-ref --verify --quiet refs/heads/gh-pages; then
    git worktree add "$WORKTREE" gh-pages 2>/dev/null
    # Remove old files in worktree
    rm -f "$WORKTREE"/*.html
else
    # Create orphan branch via worktree
    git worktree add --detach "$WORKTREE" 2>/dev/null
    git -C "$WORKTREE" checkout --orphan gh-pages
    git -C "$WORKTREE" rm -rf . --quiet 2>/dev/null || true
fi

# Copy gate + reports into worktree
cp "$TMPDIR/index.html" "$WORKTREE/"
mkdir -p "$WORKTREE/reports"
cp "$TMPDIR/reports/"*.html "$WORKTREE/reports/"

# Commit and push from worktree
cd "$WORKTREE"
git add index.html reports/
TIMESTAMP=$(date '+%Y-%m-%d %H:%M')
git commit -m "Deploy reports ($TIMESTAMP)"
git push origin gh-pages --force

echo "▸ Pushed to origin/gh-pages"

# Clean up worktree
cd "$REPO_ROOT"
git worktree remove "$WORKTREE" --force 2>/dev/null || true

echo
echo "========================================"
echo "  Deployed to GitHub Pages!"
echo "  https://akiddo-bob.github.io/hospitalist-scheduler/"
echo ""
echo "  Password: hospitalist"
echo "========================================"
echo
echo "  If this is the first deploy, enable Pages in repo settings:"
echo "  Settings > Pages > Source: 'Deploy from a branch' > Branch: gh-pages > / (root)"
