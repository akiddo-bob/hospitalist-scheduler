# Deployment

## GitHub Pages

Reports are published to GitHub Pages at:
```
https://akiddo-bob.github.io/hospitalist-scheduler/
```

### How It Works

The deploy script (`deploy_pages.sh`) publishes HTML reports to the `gh-pages` branch with a client-side password gate.

**Why a special process?** The `output/` directory is encrypted by git-crypt (via `.gitattributes`). Files committed normally would be encrypted binary blobs, not viewable HTML. The deploy script copies plaintext files to a temp directory before any git operations.

### Architecture

```
gh-pages branch root:
├── index.html           ← Password gate (entry point)
└── reports/
    ├── index.html       ← Navigation page
    ├── inputs.html      ← Configuration snapshot
    ├── rules.html       ← Rules reference
    └── block_schedule_report_v*.html  ← Report variations
```

The password gate is a single `index.html` at root that:
1. Shows a password form
2. On correct password (checked via SHA-256 hash), stores auth in `sessionStorage`
3. Loads `reports/index.html` in a full-page iframe
4. All navigation happens inside the iframe, so the gate never re-appears
5. On return visits in the same session, `sessionStorage` skips the password

This is a lightweight access gate, not real security. The password is hashed client-side.

### Running the Deploy

```bash
# Regenerate reports and deploy
./deploy_pages.sh

# Deploy existing output without regenerating
./deploy_pages.sh --skip
```

### What the Script Does

1. **Optionally regenerate reports** — Runs `generate_block_report.py` unless `--skip` is passed
2. **Verify HTML files are plaintext** — Checks each file isn't git-crypt encrypted (looks for `GITCRYPT` header)
3. **Copy to temp directory** — Moves plaintext files out of the working tree safely
4. **Create password gate** — Generates `index.html` with SHA-256 password check
5. **Git worktree** — Creates a temporary worktree on the `gh-pages` branch (never touches main working tree)
6. **Commit and force-push** — Pushes to `origin/gh-pages`
7. **Clean up** — Removes the temporary worktree

### First-Time Setup

If this is the first deploy, enable GitHub Pages in the repository settings:
1. Go to Settings → Pages
2. Source: "Deploy from a branch"
3. Branch: `gh-pages` / `/ (root)`

### Changing the Password

The password is set in the deploy script (currently "hospitalist"). To change it, edit the `PASSWORD` variable in the embedded Python script within `deploy_pages.sh`.

### Safety Notes

The deploy script uses **git worktree** to safely manage the `gh-pages` branch without touching the main working tree. This is critical because an earlier version used `git checkout --orphan` + `git clean -fd` which wiped all untracked files including Python source files. The worktree approach was adopted after that incident.

---

## Local Viewing

Reports are also available locally in the `output/` directory after generation. Open any HTML file directly in a browser. If `report_password` is set in `config.json`, local reports will also have a password gate.

### Auto-Refresh

Long call reports poll for file changes every 2 seconds. Re-run the generator and the open browser tab automatically reloads with new data.

---

## Netlify (Alternative)

A `netlify.toml` config exists as a backup deployment option:
- Build command: `echo 'No build needed'` (static files)
- Publish directory: `output/site/`

This is not actively used but available if GitHub Pages has issues.
