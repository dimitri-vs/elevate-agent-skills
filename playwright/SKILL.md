---
name: playwright
description: Browser automation via Playwright CLI and MCP. Use when interacting with web pages — navigating, clicking, filling forms, taking screenshots, scraping content. Defaults to CLI (`@playwright/cli`) for token efficiency. Falls back to MCP for short exploratory sessions or sandboxed environments.
---

# Playwright Browser Automation

## CLI vs MCP — Decision Framework

Both tools use the same Playwright engine underneath but differ in where page state lives.

**Playwright CLI** (`@playwright/cli`) — shell commands that save snapshots/screenshots to disk. The model gets minimal confirmations (file paths, element IDs). Only sees page state when it explicitly reads a file. Token cost stays flat across 50+ steps.

**Playwright MCP** (`@playwright/mcp`) — persistent MCP server that streams full accessibility trees into context after every action. Rich but expensive — 4-10x more tokens than CLI for the same flow. Model starts losing context around step 12-15.

### Use CLI (default) when:
- The agent has shell/filesystem access (Claude Code always does)
- Session exceeds ~10 browser interactions
- Mixing browser automation with other tasks (code editing, file analysis, research)
- Running in CI/CD (shell commands compose with standard tools)
- Token budget matters (4x cost reduction at scale)

### Use MCP when:
- Short exploratory session (<10 steps) needing deep page understanding
- Sandboxed environment with no shell access (Claude Desktop, web-based assistants)
- Multi-agent collaboration sharing browser state
- Self-healing test maintenance where inline DOM helps the model adapt in real-time

### Hybrid approach (advanced):
Phase 1: MCP for initial exploration/mapping (under 10 steps). Phase 2: CLI for execution (30-50+ steps). Phase 3: If the flow is stable, write a `.spec.ts` file and run `npx playwright test` deterministically — no AI tokens burned.

---

## CLI Core Workflow

The fundamental loop: **snapshot → find ref → interact → verify**.

```bash
# 1. Open browser
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

## CLI Command Reference

### Browser Lifecycle
```bash
npx @playwright/cli open [url]                           # Open browser (headless by default)
npx @playwright/cli open [url] --headed                  # Visible browser window
npx @playwright/cli open [url] --headed --browser chrome # Use installed Chrome
npx @playwright/cli open [url] --persistent              # Persistent profile (cookies survive close)
npx @playwright/cli open [url] --profile ./my-profile    # Named persistent profile directory
npx @playwright/cli close                                # Close browser (ephemeral state lost)
npx @playwright/cli resize 1920 1080                     # Resize viewport
```

### Page Interaction
```bash
npx @playwright/cli snapshot                         # Print snapshot to stdout (avoid in long sessions)
npx @playwright/cli snapshot --filename snap.yaml    # Save to file (preferred)
npx @playwright/cli click <ref>                      # Left click
npx @playwright/cli click <ref> right                # Right click
npx @playwright/cli click <ref> --modifiers Shift    # Modified click
npx @playwright/cli dblclick <ref>                   # Double click
npx @playwright/cli fill <ref> "text"                # Clear + type into input
npx @playwright/cli fill <ref> "query" --submit      # Fill and press Enter
npx @playwright/cli type "text"                      # Type into focused element (no ref needed)
npx @playwright/cli select <ref> "option-value"      # Select dropdown option
npx @playwright/cli check <ref>                      # Check checkbox/radio
npx @playwright/cli uncheck <ref>                    # Uncheck checkbox
npx @playwright/cli hover <ref>                      # Hover (tooltips, menus)
npx @playwright/cli drag <startRef> <endRef>         # Drag and drop
npx @playwright/cli upload "path/to/file.pdf"        # File upload
```

### Navigation
```bash
npx @playwright/cli goto "https://example.com/page"  # Navigate to URL
npx @playwright/cli go-back                           # Browser back
npx @playwright/cli go-forward                        # Browser forward
npx @playwright/cli reload                            # Reload page
```

### Keyboard
```bash
npx @playwright/cli press Enter
npx @playwright/cli press Control+a
npx @playwright/cli press Escape
npx @playwright/cli keydown Shift
npx @playwright/cli keyup Shift
```

### Save / Capture
```bash
npx @playwright/cli screenshot                               # Auto-named PNG to .playwright-cli/
npx @playwright/cli screenshot --filename .pw-tmp/shot.png   # Named file
npx @playwright/cli screenshot --full-page                   # Full scrollable page
npx @playwright/cli screenshot e15                           # Element screenshot
npx @playwright/cli screenshot e15 --filename .pw-tmp/el.png # Element to named file
npx @playwright/cli pdf                                      # Save page as PDF
```

### Tabs
```bash
npx @playwright/cli tab-list              # List open tabs
npx @playwright/cli tab-new "https://..." # Open new tab
npx @playwright/cli tab-select 1          # Switch to tab by index
npx @playwright/cli tab-close 2           # Close tab by index
```

### Network Mocking
```bash
npx @playwright/cli route "**/api/users" --status 200 --body '{"users": []}' --content-type application/json
npx @playwright/cli route "**/api/error" --status 500 --body "Internal Server Error"
npx @playwright/cli route-list            # List active mocks
npx @playwright/cli unroute "**/api/users"  # Remove specific mock
npx @playwright/cli unroute               # Remove all mocks
```

### DevTools / Debugging
```bash
npx @playwright/cli console              # All console messages (info+)
npx @playwright/cli console error        # Errors only
npx @playwright/cli console --clear      # Clear and return
npx @playwright/cli network              # List all network requests
npx @playwright/cli eval "() => document.title"                  # Page-level JS
npx @playwright/cli eval "(el) => el.textContent" e21            # Element-level JS
npx @playwright/cli run-code "await page.waitForTimeout(2000)"   # Run Playwright code
npx @playwright/cli show                 # Open browser DevTools
```

### Tracing & Recording
```bash
npx @playwright/cli tracing-start
npx @playwright/cli tracing-stop              # Saves trace.zip
npx @playwright/cli video-start
npx @playwright/cli video-stop
```

### Sessions (Parallel Browsers)
```bash
npx @playwright/cli list                      # List active sessions
npx @playwright/cli -s=session2 open "url"    # Named session
npx @playwright/cli -s=session2 snapshot      # Commands target specific session
npx @playwright/cli close-all                 # Close all sessions
npx @playwright/cli kill-all                  # Force kill zombie processes
```

### Install / Setup
```bash
npx @playwright/cli install               # Initialize workspace
npx @playwright/cli install-browser        # Install browsers
```

---

## Session Management & Auth Persistence

This is a common source of confusion. Understanding the persistence model prevents "why did I lose my login?" issues.

### How CLI sessions work
When you run `open`, CLI launches a browser process that stays running. All subsequent commands (`click`, `fill`, `snapshot`) reuse that same browser context — cookies, localStorage, and login state persist **within the session**. The session lives until you `close` it or the process dies.

**Default (ephemeral):** State exists only in memory. When you `close`, everything is gone. Next `open` starts fresh.

**Persistent mode (`--persistent`):** State writes to disk. Cookies/storage survive across `close` and re-`open`. Default location: `~/.cache/ms-playwright/cli-<browser>-profile`. Override with `--profile <path>`.

### Why sessions sometimes "lose" state
- You accidentally started a **new session** without `-s=<name>` (or used a different name), getting a fresh browser context
- You used `close` on an ephemeral session — all state was discarded
- The browser process crashed or was killed, and without `--persistent`, state was lost
- Session cookies expired naturally (server-side timeout)

### Auth persistence patterns

**Pattern 1: state-save/state-load (recommended for most workflows)**
```bash
# First run: log in manually, then save
npx @playwright/cli open "https://app.example.com/login" --headed
# ... fill credentials, click submit ...
npx @playwright/cli state-save .pw-tmp/auth-work.json

# Subsequent runs: load saved state
npx @playwright/cli open "https://app.example.com" --headed
npx @playwright/cli state-load .pw-tmp/auth-work.json
npx @playwright/cli goto "https://app.example.com/dashboard"
# Now logged in without re-entering credentials
```

**Pattern 2: persistent profile (for long-lived sessions)**
```bash
# Always use the same profile directory
npx @playwright/cli open "https://app.example.com" --headed --persistent --profile .pw-profiles/work
# Cookies persist across close/reopen as long as you use the same --profile path
```

**Pattern 3: cookie injection (for API tokens)**
```bash
npx @playwright/cli cookie-set "session_token" "abc123"
npx @playwright/cli localstorage-set "auth_token" "Bearer xyz"
```

### Individual storage operations
```bash
npx @playwright/cli cookie-list
npx @playwright/cli cookie-get "session_id"
npx @playwright/cli cookie-set "key" "value"
npx @playwright/cli cookie-delete "key"
npx @playwright/cli cookie-clear
npx @playwright/cli localstorage-list
npx @playwright/cli localstorage-get "token"
npx @playwright/cli localstorage-set "key" "value"
npx @playwright/cli sessionstorage-list
npx @playwright/cli sessionstorage-get "key"
npx @playwright/cli sessionstorage-set "key" "value"
```

---

## Chrome Profile Selection

Playwright launches its own browser by default — it doesn't connect to your running Chrome. But you can point it at an existing Chrome profile directory.

### Option 1: Use a Chrome profile directory (simple)
```bash
# Point to an existing Chrome profile (Chrome must NOT be running with this profile)
npx @playwright/cli open "https://example.com" --headed --browser chrome \
  --persistent --profile "C:\Users\<you>\AppData\Local\Google\Chrome\User Data\Profile 1"
```

**Critical constraint:** Chrome locks a profile directory to one process. If your real Chrome is running with that profile, Playwright will fail or hang. Either close Chrome first or make a copy of the profile directory for automation use.

### Option 2: Attach to running Chrome via CDP (advanced)
Start Chrome with remote debugging enabled, then connect Playwright to it:
```bash
# Start Chrome manually with remote debugging
"C:\Program Files\Google\Chrome\Application\chrome.exe" --profile-directory="Profile 1" --remote-debugging-port=9222

# In .playwright/cli.config.json, set:
# { "cdpEndpoint": "ws://localhost:9222/devtools/browser/<id>" }
# Then playwright-cli commands will control that existing Chrome instance
```

This gives full access to the real profile (extensions, cookies, saved passwords) but be aware the agent is operating in your actual browser session.

### Option 3: Dedicated automation profiles (recommended)
Create separate profile directories for automation, seeded with auth state:
```bash
# Create work profile
npx @playwright/cli open "https://work-app.example.com" --headed --browser chrome \
  --persistent --profile .pw-profiles/work
# Log in manually, then state is saved to the profile directory

# Create personal profile
npx @playwright/cli open "https://personal-app.example.com" --headed --browser chrome \
  --persistent --profile .pw-profiles/personal
# Log in manually
```

This avoids the "can't share profile with real Chrome" problem and keeps automation state separate from your daily browsing.

---

## Asset Management & Cleanup

CLI generates snapshot files (YAML) and screenshots (PNG) that accumulate quickly. Without management, they'll pollute your working directory.

### Artifact directory strategy
Always direct artifacts to a dedicated directory. Use `.pw-tmp/` in the project root (add to `.gitignore`).

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
- **Traces and videos** also go here: `npx @playwright/cli tracing-stop --filename=".pw-tmp/trace.zip"`

### Cleanup
```bash
# End of session: clean up all artifacts
rm -rf .pw-tmp/

# Or selective cleanup: keep screenshots, delete snapshots
rm .pw-tmp/*.yaml
```

### .gitignore additions
```
.pw-tmp/
.playwright-cli/
.pw-profiles/
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

### Dialog handling
```bash
npx @playwright/cli dialog-accept            # Accept alert/confirm
npx @playwright/cli dialog-accept "my input" # Accept prompt with text
npx @playwright/cli dialog-dismiss           # Cancel/dismiss
```

---

## MCP Optimization (When MCP is Chosen)

If MCP is the right tool for the job, reduce its token footprint:

### Server launch options
```bash
# Reduce token waste with these flags
npx @playwright/mcp --image-responses=omit    # Don't stream images in context
npx @playwright/mcp --isolated                # Fresh context each session (no disk persistence)
npx @playwright/mcp --user-data-dir=<path>    # Use specific profile directory
```

### Snapshot modes
MCP supports three snapshot strategies:
- **incremental** (default since 2025): Only sends DOM diffs since last snapshot. Use this.
- **full**: Entire page structure every time. Avoid.
- **none**: No auto-snapshots. You manually call `browser_snapshot` when needed. Most token-efficient but requires explicit orchestration.

### Configuration
MCP config supports `allowedHosts`/`blockedHosts` to restrict domains, `capabilities` to disable unused tools (reducing schema overhead), and `contextOptions` for viewport/locale defaults. Disabling unused capabilities (PDF, file upload, etc.) trims tool schemas from context.

### Session management
MCP defaults to persistent profiles at `~/.cache/ms-playwright/mcp-<browser>-profile`. To start fresh, use `--isolated`. To reset mid-session without restarting, use `browser_close` → `browser_navigate` cycle. For long sessions, consider chunking: finish a sub-flow, disconnect/reconnect MCP to clear accumulated context.

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
  "userDataDir": ".pw-profiles/default",
  "contextOptions": {
    "viewport": { "width": 1280, "height": 800 }
  }
}
```

### Key config fields
- **`browserName`**: `"chromium"` (default), `"firefox"`, `"webkit"`
- **`launchOptions.channel`**: `"chrome"`, `"msedge"` — use installed browser instead of bundled
- **`launchOptions.headless`**: `false` to always run headed
- **`launchOptions.args`**: Custom browser flags (e.g., `["--start-maximized"]`)
- **`userDataDir`**: Default persistent profile path
- **`contextOptions.viewport`**: Default viewport dimensions
- **`contextOptions.locale`**: Default locale (e.g., `"en-US"`)
- **`cdpEndpoint`**: WebSocket URL to attach to an existing browser instance

Override config per-invocation: `npx @playwright/cli --config=path/to/config.json open "url"`.

---

## Parallel Browsing with Subagents

When the main agent delegates browsing tasks to multiple subagents (via the Agent tool), each subagent runs in its own process but shares the same Playwright CLI state directory. Without coordination, they will **fight over the default session** — one agent's `goto` overwrites another's page, snapshots return wrong content, and actions target stale refs.

### The problem (observed 2026-03-06)
Three subagents were launched in parallel to browse different websites. All used the default session (`npx @playwright/cli open "url"`). Result: they stomped on each other's browser state, causing navigation conflicts and garbled results.

### Solution: Named sessions (`-s=<name>`) — CONFIRMED WORKING
Each subagent MUST use a unique session name. The `-s` flag creates an isolated browser process per session. **Verified 2026-03-06:** opening 5 simultaneous named sessions (`-s=test1` through `-s=test5`) produced 5 separate browser PIDs, each independently controllable. Screenshots taken in parallel returned correct (non-cross-contaminated) content.

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

### Alternative: mixed tooling for parallel browsing
If named sessions prove unreliable, use different browser tools per subagent:
- **Subagent A:** Playwright CLI (`-s=agentA`)
- **Subagent B:** Playwright CLI (`-s=agentB`)
- **Subagent C:** Puppeteer MCP (entirely separate browser process)

Playwright MCP is NOT suitable for parallel subagents — it's a single persistent server that would have the same shared-state problem as the default CLI session.

### Cleanup after parallel sessions
```bash
npx @playwright/cli close-all    # Gracefully close all named sessions
npx @playwright/cli kill-all     # Force-kill zombie browser processes
```

---

## Tips & Gotchas

- **Always use `--filename` with a path** for snapshots and screenshots. Without it, YAML dumps to stdout (enters context, defeats token efficiency) or files land in `.playwright-cli/` with auto-generated names.
- **Element refs (e4, s7) are per-snapshot.** After navigation or significant DOM changes, take a new snapshot — old refs are stale.
- **`--submit` on `fill`** presses Enter after filling. Great for search boxes and login forms.
- **Shadow DOM** is invisible to the accessibility tree. Both CLI and MCP share this limitation. Use `eval` with `document.querySelector` to reach shadow DOM elements.
- **Sessions are isolated.** Use `-s=<name>` to run parallel browser sessions (e.g., testing two user roles simultaneously). Each session is a full browser process.
- **Bot detection:** Neither CLI nor MCP bypasses CAPTCHAs or WAFs. Handle auth via storage state files or persistent profiles rather than interactive login when possible.
- **Context still accumulates** from conversation history even with CLI. For very long sessions (100+ steps), consider summarizing progress and resetting context.
- **Chrome profile locking:** You cannot share a Chrome profile directory between your real Chrome and Playwright simultaneously. Close Chrome or use a copy.
- **MCP's 26+ tool schemas** load ~4,200 tokens at session start before any action. CLI's overhead is ~68 tokens (one `--help` read).
