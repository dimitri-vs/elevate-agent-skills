---
name: playwright
description: Browser automation via Playwright CLI and MCP. Use when interacting with web pages — navigating, clicking, filling forms, taking screenshots, scraping content. Defaults to CLI (`@playwright/cli`) for token efficiency. Falls back to MCP for short exploratory sessions or sandboxed environments.
---

# Playwright Browser Automation

## CLI vs MCP

**Always use CLI** (`@playwright/cli`) unless you have a specific reason not to. CLI saves snapshots/screenshots to disk — the agent only sees page state when it explicitly reads a file. Token cost stays flat across 50+ steps.

**MCP** (`@playwright/mcp`) streams full accessibility trees into context after every action. It's reliable and gives the agent richer page understanding, but has two significant downsides:

1. **Context burn**: 4-10x more tokens than CLI for the same flow. The agent starts losing earlier context around step 12-15.
2. **Single session**: MCP runs one persistent browser session. It does NOT support parallel subagents — they'd all share the same browser state. CLI supports parallel sessions via `-s=<name>`.

MCP also handles asset/temp file storage automatically, whereas CLI requires the agent to manage artifacts manually (see [Asset Management](#asset-management--cleanup) below).

**Use MCP only when:**
- Short session (<10 steps) where rich inline page understanding helps
- Sandboxed environment with no shell access (Claude Desktop, web-based assistants)

---

## CLI Core Workflow

**Always use `--headed`** unless explicitly told otherwise. The user should be able to see what the browser is doing.

The fundamental loop: **snapshot → find ref → interact → verify**.

```bash
# 1. Open browser (always use --headed for visibility)
npx @playwright/cli open "https://example.com" --headed

# 2. Take snapshot — saves YAML with element refs (e21, s4, etc.)
npx @playwright/cli snapshot --filename .pw-tmp/page.yaml
# Then read .pw-tmp/page.yaml to find element references

# 3. Interact using refs from snapshot
npx @playwright/cli fill e4 "user@example.com"
npx @playwright/cli fill e7 "password123" --submit
npx @playwright/cli click e12

# 4. Verify — new snapshot or screenshot after navigation
npx @playwright/cli snapshot --filename .pw-tmp/dashboard.yaml
npx @playwright/cli screenshot --filename .pw-tmp/result.png
```

**Key principle:** Only read snapshot files when you need to find element refs or verify content. Don't read them "just in case." Most interactions after the first snapshot just need `click <ref>` or `fill <ref> <text>` — zero context cost.

**Selective reading:** If you know the target element's label, grep the snapshot file instead of reading the whole thing: `grep -i "submit" .pw-tmp/page.yaml` to find the ref, then `click` it directly.

---

## CLI Quick Reference

The agent already knows standard Playwright CLI commands (`click`, `fill`, `goto`, `press`, etc.). This section covers only the less obvious commands and flags.

### Browser Lifecycle
```bash
npx @playwright/cli open [url] --headed                  # Visible browser window (prefer this)
npx @playwright/cli open [url] --headed --browser chrome # Use installed Chrome
npx @playwright/cli open [url] --persistent              # Persistent profile (cookies survive close)
npx @playwright/cli open [url] --profile ./my-profile    # Named persistent profile directory
npx @playwright/cli close                                # Close browser (ephemeral state lost)
npx @playwright/cli resize 1920 1080                     # Resize viewport
```

### Snapshots & Screenshots
```bash
npx @playwright/cli snapshot --filename .pw-tmp/snap.yaml  # Save to file (ALWAYS use --filename)
npx @playwright/cli screenshot --filename .pw-tmp/shot.png # Named screenshot
npx @playwright/cli screenshot --full-page                 # Full scrollable page
npx @playwright/cli screenshot e15 --filename .pw-tmp/el.png # Element screenshot
```

### Sessions (Parallel Browsers)
```bash
npx @playwright/cli list                      # List active sessions
npx @playwright/cli -s=session2 open "url"    # Named session (isolated browser process)
npx @playwright/cli -s=session2 snapshot      # Commands target specific session
npx @playwright/cli close-all                 # Close all sessions
npx @playwright/cli kill-all                  # Force kill zombie processes
```

### Tabs
```bash
npx @playwright/cli tab-list              # List open tabs
npx @playwright/cli tab-new "https://..." # Open new tab
npx @playwright/cli tab-select 1          # Switch to tab by index
npx @playwright/cli tab-close 2           # Close tab by index
```

### Tracing & Recording
```bash
npx @playwright/cli tracing-start                                  # Start recording
npx @playwright/cli tracing-stop --filename=".pw-tmp/trace.zip"   # Save trace
npx @playwright/cli video-start
npx @playwright/cli video-stop
```

### Dialog Handling
```bash
npx @playwright/cli dialog-accept            # Accept alert/confirm
npx @playwright/cli dialog-accept "my input" # Accept prompt with text
npx @playwright/cli dialog-dismiss           # Cancel/dismiss
```

---

## Session Management & Auth Persistence

Understanding the persistence model prevents "why did I lose my login?" issues.

### How CLI sessions work
When you run `open`, CLI launches a browser process that stays running. All subsequent commands (`click`, `fill`, `snapshot`) reuse that same browser context — cookies, localStorage, and login state persist **within the session**. The session lives until you `close` it or the process dies.

**Default (ephemeral):** State exists only in memory. When you `close`, everything is gone. Next `open` starts fresh.

**Persistent mode (`--persistent`):** State writes to disk. Cookies/storage survive across `close` and re-`open`. Default location: `~/.cache/ms-playwright/cli-<browser>-profile`. Override with `--profile <path>`.

### Why sessions sometimes "lose" state
- You accidentally started a **new session** without `-s=<name>` (or used a different name), getting a fresh browser context
- You used `close` on an ephemeral session — all state was discarded
- The browser process crashed or was killed, and without `--persistent`, state was lost
- Session cookies expired naturally (server-side timeout)

### Auth via persistent profiles

Playwright cannot reuse your real Chrome profiles (Chrome locks them to one process, and copying them doesn't preserve auth). Instead, use **dedicated Playwright profiles** that accumulate auth over time — similar to how MCP handles it.

Create two persistent profiles — one for personal, one for work — in a **global location** (`~/.pw-profiles/`). These are shared across all projects, so auth accumulates regardless of which project directory the agent runs from.

```bash
# Personal context — Google, personal email, shopping, banking, etc.
npx @playwright/cli open "https://messages.google.com" --headed --persistent \
  --profile ~/.pw-profiles/personal

# Work context — work email, internal tools, admin panels, etc.
npx @playwright/cli open "https://app.example.com" --headed --persistent \
  --profile ~/.pw-profiles/work
```

**How it works:**
- `--persistent --profile <path>` writes all cookies, localStorage, and session data to disk
- Auth accumulates: log into Gmail once → Google Messages, Drive, Calendar all work in future sessions
- Each profile is independent — personal and work auth never mix
- Profiles live in the user's home directory, not per-project — reusable from any working directory
- If Playwright hits a login wall, **stop and ask the user** to complete the login interactively
- **One session per profile** — a persistent profile is locked to one browser process. If another agent needs the same profile, it must wait or use a different one

### Choosing which profile

**Default to using a profile.** If there's any chance the task involves authenticated content (shopping accounts, email, dashboards, anything behind a login), use the appropriate profile. Only skip the profile for purely public content like reading documentation or searching public websites.

| Task | Profile |
|------|---------|
| "Check my work email" | `~/.pw-profiles/work` |
| "Send a message to Mom" | `~/.pw-profiles/personal` |
| "Find the best price for X" | `~/.pw-profiles/personal` |
| "Check our Railway dashboard" | `~/.pw-profiles/work` |
| "Look up React docs" | No profile (ephemeral) |
| "Scrape this public website" | No profile (ephemeral) |

If unclear whether personal or work, ask the user.

### Other auth utilities
```bash
npx @playwright/cli cookie-list / cookie-get / cookie-set / cookie-delete / cookie-clear
npx @playwright/cli localstorage-list / localstorage-get / localstorage-set
npx @playwright/cli state-save <path>    # Export all cookies + storage to JSON
npx @playwright/cli state-load <path>    # Import saved state into current session
```

---

## Asset Management & Cleanup

Unlike MCP (which manages temp files automatically), CLI requires explicit artifact management. Without it, snapshots and screenshots accumulate and pollute the working directory.

### Artifact directory strategy
Always direct artifacts to `.pw-tmp/` in the project root (add to `.gitignore`).

```bash
# At session start, ensure the directory exists
mkdir -p .pw-tmp

# All snapshots and screenshots go here
npx @playwright/cli snapshot --filename .pw-tmp/page.yaml
npx @playwright/cli screenshot --filename .pw-tmp/home.png
```

### Naming conventions
- **Overwrite-in-place** for current state: always write to `.pw-tmp/current.yaml` — no buildup, always the latest
- **Descriptive names** when you need history: `.pw-tmp/step3-dashboard.yaml`, `.pw-tmp/after-login.png`

### Cleanup
```bash
# End of session: clean up all artifacts
rm -rf .pw-tmp/
```

### .gitignore additions
```
.pw-tmp/
.pw-profiles/
.playwright-cli/
```

---

## Context Efficiency Tips

### Read snapshots selectively
Don't read the full YAML on every page. If you know the target element's label:
```bash
# Instead of reading the whole file, grep for the element
grep -i "submit\|login\|sign in" .pw-tmp/page.yaml
# Find the ref (e.g., e12), then click it directly
npx @playwright/cli click e12
```

### Avoid redundant snapshots
Only snapshot after major state changes (page navigation, dynamic content loads). If nothing changed, reuse existing refs. If a `click` fails with "Node not found", take a fresh snapshot — refs went stale.

### Error recovery pattern
```
1. Attempt action (click, fill, etc.)
2. If "Element not found" or similar error:
   a. Take fresh snapshot
   b. Search for the element by label/text
   c. Retry with new ref
3. If navigation timeout:
   a. Reload page
   b. Re-snapshot
   c. Continue
```

### Minimize screenshot reads
Most verification doesn't need pixels. Use `eval` to extract text data:
```bash
npx @playwright/cli eval "(el) => el.textContent" e31
# Returns: "Revenue: $1,247" — no image token cost
```
Reserve screenshots for visual-only checks (graphs, canvas, layout verification).

---

## Parallel Browsing with Subagents

When the main agent delegates browsing tasks to multiple subagents (via the Agent tool), each subagent runs in its own process but shares the same Playwright CLI state directory. Without coordination, they will **fight over the default session** — one agent's `goto` overwrites another's page, snapshots return wrong content, and actions target stale refs.

### Solution: Named sessions (`-s=<name>`)
Each subagent MUST use a unique session name. The `-s` flag creates an isolated browser process per session.

```bash
# Subagent 1
npx @playwright/cli -s=agent1 open "https://site-a.com" --headed
npx @playwright/cli -s=agent1 screenshot --filename .pw-tmp/agent1-home.png
npx @playwright/cli -s=agent1 close

# Subagent 2
npx @playwright/cli -s=agent2 open "https://site-b.com" --headed
npx @playwright/cli -s=agent2 screenshot --filename .pw-tmp/agent2-home.png
npx @playwright/cli -s=agent2 close
```

### How to instruct subagents
When spawning parallel browsing agents, include the session name in the prompt:

```
"Use Playwright CLI with session name `-s=agent1` for ALL commands.
Example: npx @playwright/cli -s=agent1 open 'https://...' --headed"
```

Also namespace artifact filenames (`.pw-tmp/agent1-*.png`) to avoid file collisions.

Playwright MCP is NOT suitable for parallel subagents — it's a single persistent server that would have the same shared-state problem as the default CLI session.

### Cleanup after parallel sessions
```bash
npx @playwright/cli close-all    # Gracefully close all named sessions
npx @playwright/cli kill-all     # Force-kill zombie browser processes
```

---

## MCP Reference (When MCP is Chosen)

MCP handles asset management and session state automatically, but burns through context. If using it, reduce the token footprint:

```bash
npx @playwright/mcp --image-responses=omit    # Don't stream images in context
npx @playwright/mcp --isolated                # Fresh context each session (no disk persistence)
npx @playwright/mcp --user-data-dir=<path>    # Use specific profile directory
```

**Snapshot modes:** `incremental` (default, sends DOM diffs — use this), `full` (entire page every time — avoid), `none` (manual `browser_snapshot` calls only — most efficient but requires orchestration).

**Session management:** Defaults to persistent profiles at `~/.cache/ms-playwright/mcp-<browser>-profile`. Use `--isolated` to start fresh. For long sessions, consider chunking: finish a sub-flow, disconnect/reconnect MCP to clear accumulated context.

**Schema overhead:** MCP's 26+ tool schemas load ~4,200 tokens at session start before any action. CLI's overhead is ~68 tokens.

---

## CLI Configuration File

Place at `.playwright/cli.config.json` in your project root. The CLI auto-discovers it.

```json
{
  "browserName": "chromium",
  "launchOptions": {
    "headless": false,
    "channel": "chrome"
  },
  "userDataDir": "~/.pw-profiles/personal",
  "contextOptions": {
    "viewport": { "width": 1280, "height": 800 }
  }
}
```

Key fields: `browserName` (`chromium`/`firefox`/`webkit`), `launchOptions.channel` (`chrome`/`msedge` for installed browsers), `launchOptions.headless`, `launchOptions.args`, `userDataDir`, `contextOptions.viewport`, `contextOptions.locale`, `cdpEndpoint` (WebSocket URL for attaching to existing browser).

Override per-invocation: `npx @playwright/cli --config=path/to/config.json open "url"`.

---

## Tips & Gotchas

- **Always use `--filename` with a path** for snapshots and screenshots. Without it, YAML dumps to stdout (floods context) or files land in `.playwright-cli/` with auto-generated names.
- **Element refs (e4, s7) are per-snapshot.** After navigation or significant DOM changes, take a new snapshot — old refs are stale.
- **`--submit` on `fill`** presses Enter after filling. Great for search boxes and login forms.
- **Shadow DOM** is invisible to the accessibility tree. Use `eval` with `document.querySelector` to reach shadow DOM elements.
- **Bot detection:** Neither CLI nor MCP bypasses CAPTCHAs or WAFs. Handle auth via storage state files or persistent profiles rather than interactive login when possible.
- **Context still accumulates** from conversation history even with CLI. For very long sessions (100+ steps), consider summarizing progress and resetting context.
- **Chrome profile locking:** You cannot share a Chrome profile directory between your real Chrome and Playwright simultaneously. Close Chrome or use a copy.
