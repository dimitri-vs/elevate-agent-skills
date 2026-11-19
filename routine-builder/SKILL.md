---
name: routine-builder
description: Design, create, and maintain Claude Code Desktop routines (local scheduled tasks). Use when building a new routine, migrating a scheduled task from another platform, debugging a broken routine, or doing a monthly tune-up on existing routines. Covers the two-layer architecture (deterministic Python + editorial Claude), SKILL.md authoring, schedule configuration, and common pitfalls.
---

# Routine Builder

Guide for creating and maintaining Claude Code Desktop routines - local scheduled tasks that run on the user's machine at cron intervals.

Full reference docs (read only when this skill doesn't answer the question - e.g. cloud vs local mode tradeoffs, storage/backup internals, MSIX path quirks):
- `C:\Users\Dimitri\elevate-vault\playbook\Scheduling - CLI, Desktop, Cloud.md` - Cloud Routine vs Desktop local vs `/loop` decision guide, catch-up behavior details
- `C:\Users\Dimitri\elevate-vault\playbook\Where Claude Stores Things - Chat, Cowork, Code.md` - where prompts/schedules/skills live on disk across Chat/Cowork/Code, backup advice

## When to Use

- Building a new routine from scratch
- Migrating a scheduled task from ChatGPT, Cowork, or another automation platform
- Debugging a routine that's failing or producing poor results
- Monthly tune-up / retrospective on an existing routine
- Deciding whether a workflow should be a routine, a skill, or a CLAUDE.md entry

## Core Architecture: Two-Layer Model

Every non-trivial routine should split into two layers:

**Deterministic layer (Python script):** Handles API calls, data collection, deduplication, sending. Runs via `uv run` with PEP 723 inline script metadata for dependencies. This layer is fast, testable, and predictable.

**Editorial layer (Claude via SKILL.md):** Handles triage, classification, summarization, composition, and any judgment call. This is the routine prompt that Claude follows at each scheduled run.

The boundary between layers follows one rule: if a step requires judgment (is this mention about us or a false positive?), it belongs in the editorial layer. If it's mechanical (fetch an API, deduplicate URLs, send an email), it belongs in the deterministic layer. Resist the temptation to have the Python layer make editorial decisions via regex or substring matching - even for seemingly simple classification. Low-volume editorial judgment (under ~100 items per run) should always go to the agent. Reserve deterministic filtering only for high-volume data reduction where agent cost would be prohibitive.

### File Structure

```
Code/<routine-name>/
  .env                    # API keys (gitignored)
  .gitignore
  config.py               # All configuration (sources, queries, thresholds)
  <main-script>.py        # Deterministic layer (PEP 723, uv run)
  template.html           # Email template (if the routine sends digests)
  <ROUTINE>_ROUTINE.md    # Documented mirror of the SKILL.md prompt
  seen.db / <name>.db     # SQLite for dedup/state (gitignored, created at runtime)

~/.claude/scheduled-tasks/<routine-name>/
  SKILL.md                # Live routine prompt (Desktop reads this at fire time)
```

Keep the documented mirror (`*_ROUTINE.md`) in the project repo and the live copy (`SKILL.md`) in `~/.claude/scheduled-tasks/`. They should stay in sync - the monthly tune-up step handles this.

### Python Script Conventions

Use PEP 723 inline script metadata so `uv run` handles dependencies without a pyproject.toml:

```python
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "httpx",
#   "python-dotenv",
# ]
# ///
```

Use `load_dotenv(override=True)` - shell environment variables can shadow `.env` values otherwise.

Use argparse subcommands so the SKILL.md can invoke specific phases:
```
uv run script.py collect    # Gather data from sources
uv run script.py send       # Send composed output
uv run script.py all        # Run all phases in sequence
```

For Google API access (Gmail, Calendar, Tasks), use:
```python
sys.path.insert(0, r"C:\Users\Dimitri\scripts\google-auth")
from google_auth import get_credentials
```

### SKILL.md Authoring

The SKILL.md is a prompt that Claude follows step-by-step at each scheduled run. Structure it as:

1. **Purpose** - one paragraph explaining what the routine does
2. **Steps** - numbered phases, each with a bash command (deterministic) or editorial instructions
3. **Filter/triage rules** - explicit criteria for what to keep, cut, and flag
4. **Composition rules** - constraints on the output (link grounding, length limits)
5. **Send step** - the bash command to deliver the output
6. **Quiet edition rules** - what to do when there's nothing to report
7. **Notes** - runtime considerations (cost, timing, known limitations)

Key principles for SKILL.md writing:
- Be explicit about what constitutes a false positive. Give concrete examples.
- Define the "quiet edition" behavior - a routine that sends nothing when there's nothing to report is better than one that pads.
- Include an audit trail step (write a triage.md or similar) so you can review decisions after the fact.
- Every link in the output must trace back to collected data. No hallucinated URLs.

### Frontmatter

```yaml
---
name: routine-name
description: What this routine monitors/does, how often, and what it delivers.
---
```

## Schedule Configuration

Routines are scheduled via the Desktop app's Scheduled page (Code side; older docs say "Routines page").

**Creating a LOCAL routine in the UI (Desktop UI as of 2026-11-19, the trap that cost a session 3 tries):** the new
"Scheduled tasks" page defaults to a CLOUD routine (runs on Anthropic's servers, "Cloud environment: Default", no local
files, nothing written to disk). A local routine needs all of:
1. The **Code** side of Desktop (the `</>` toggle top-left), not the chat side. Chat/Cowork "scheduled tasks" are a third kind
   (sandboxed VM, stored under `%APPDATA%\Claude\local-agent-mode-sessions\...`), also wrong.
2. **Scheduled > New task > "Set up manually"** (not "Create with Claude").
3. **Advanced settings > General > "Only on this computer" ON.** This is the switch that makes it local; only then does the
   folder field appear.
4. **A local folder** (the routine's cwd), plus name and prompt (a placeholder prompt is fine, see the deployment order below).
The UI only offers Daily/Weekly with a time, and its time picker pre-fills the current time: the saved cron comes out like
`16 13 * * *`. Fix the cron, and add `model` and `permissionMode`, by hand in `scheduled-tasks.json` with the app QUIT (below).
**Check it landed before going on:** `~/.claude/scheduled-tasks/<id>/` exists and the entry is in
`%APPDATA%\Claude\claude-code-sessions\<account>\<org>\scheduled-tasks.json`. If neither exists, it was made as a cloud or
Cowork task: delete it and redo with the steps above. "Quit" means the tray too: after a UI edit Desktop is often still
running (`Get-Process claude | ? Path -like '*WindowsApps*'`), and its exit rewrites the JSON over hand edits.

**Deployment order matters (verified 2026-07-25):** creating a routine in the Routines UI **overwrites** any pre-staged `~/.claude/scheduled-tasks/<id>/SKILL.md` with whatever placeholder prompt was typed in the dialog. Correct order: (1) user creates the routine in the UI (placeholder prompt is fine), (2) THEN copy the real SKILL.md from the repo mirror over the placeholder. The routine `id` in scheduled-tasks.json = the folder name.

**Verify after creation:** the schedule the user picks in the UI often doesn't match intent (observed: "Fri 8:30am" intent saved as `0 9 * * 1-5`). Reading `scheduled-tasks.json` while the app runs is safe (only writes are clobbered) — confirm the entry:

```json
{"scheduledTasks": [{"id": "<name>", "cronExpression": "30 8 * * 5", "enabled": true,
  "filePath": "...\\scheduled-tasks\\<name>\\SKILL.md", "cwd": "...", "useWorktree": false}]}
```

`permissionMode` ("bypassPermissions" = Act without asking), `model` (e.g. `claude-opus-5[1m]`; `[1m]` = the 1M-context variant, same price under 200K tokens) and `displayName` (a human label; the `id` stays the folder name) are all optional keys on the same entry (schema verified in the Desktop bundle 2026-09-06). Entries created from the UI often omit `model`, and a routine then inherits whatever its previous run used, so set it explicitly. Bulk edits: quit Desktop, edit the JSON, relaunch.

**Setting the schedule:** Use the "Custom" option in the schedule dropdown to enter a raw cron expression. Standard 5-field cron in local time: `minute hour day-of-month month day-of-week`.

Common patterns:
- `30 7 * * 1` - every Monday at 7:30 AM
- `30 7 * * 1,4` - Monday + Thursday at 7:30 AM
- `30 7 * * 0,2,4,6` - Sun/Tue/Thu/Sat at 7:30 AM

**Permission mode:** Set to "Act without asking" for unattended runs.

**Model:** set `claude-opus-5[1m]` on every routine (decision 2026-09-06: all routines at least Opus 5). The `[1m]` suffix selects the 1M-context variant; it costs the same until a run exceeds 200K tokens, so there is no reason to omit it.

**Working directory:** Set to the project directory (e.g., `C:\Users\Dimitri\Code\<routine-name>`).

**Catch-up behavior:** If the machine is asleep/off at fire time, Desktop fires ONE catch-up run for the most recently missed time (within 7 days). Older misses are discarded.

## Deduplication Pattern

Use SQLite for tracking seen items across runs:

```python
def db():
    conn = sqlite3.connect("seen.db")
    conn.execute("""CREATE TABLE IF NOT EXISTS seen_items (
        url_hash TEXT PRIMARY KEY, url TEXT, title TEXT,
        source TEXT, first_seen TEXT)""")
    return conn

def url_hash(url):
    normalized = re.sub(r"[?&](utm_\w+|fbclid|gclid)=[^&]*", "", url or "")
    return hashlib.sha1(normalized.rstrip("?&/").lower().encode()).hexdigest()[:16]
```

Each URL surfaces exactly once across all runs. To re-surface an old item, delete it from the table manually.

## Delivery Patterns

### Email digest via Gmail API

```python
sys.path.insert(0, r"C:\Users\Dimitri\scripts\google-auth")
from google_auth import get_credentials
from googleapiclient.discovery import build

creds = get_credentials(profile="business")  # or "personal"
service = build("gmail", "v1", credentials=creds)
msg = MIMEText(html_content, "html")
msg["To"] = recipient
msg["From"] = sender
msg["Subject"] = subject
raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
service.users().messages().send(userId="me", body={"raw": raw}).execute()
```

Always send HTML emails (`MIMEText(body, 'html')`) with `<p>` tags. Plain text emails render newlines literally on mobile.

### ntfy notification

```python
urllib.request.urlopen(urllib.request.Request(
    f"https://ntfy.sh/{topic}",
    data=message.encode("utf-8"),
    headers={"Title": title, "Priority": "3", "Tags": "mag"}), timeout=10)
```

Send ntfy on every run (success count or "all clear") so silence = something broke.

### Delivery design: write for a 3-second skim (added 2026-10-26)

Dimitri does not read routine emails. He skims the subject and the first block for
about three seconds, answers one question ("do I have to do anything?"), and moves
on. Design every delivery for that reader. The agent that follows up reads the
audit-trail file, never the email. Evidence: an `ACTION` item re-emailed four runs
over 11 days (IKOO card pause, Oct 2026) was never acted on; it took 30 seconds once
it showed up in an interactive session's checklist.

1. **Email is for exceptions only.** A clean run sends one ntfy line and no email
   (silence still means the routine broke, so the ntfy is mandatory). An email means
   "you must act" or "the routine failed". A weekly "all fine" email trains him to
   ignore the one that matters.
2. **The subject is the email.** Verdict + count + the ask, nothing about the run:
   `ACTION (2): pause card 1052 by 11/09, approve 1 DM` /
   `FAILED: xero-weekly-close, Upwork token refresh`. Never a gist of what happened.
3. **First block = the asks**, a numbered list of imperatives: verb + object +
   deadline + effort + where. `1. Pause Privacy card 1052 (Ikechi) before 11/09 -
   1 min, Privacy > Cards > pause`. No background inside the item.
4. **Background goes below a visible "Details (skip)" divider.** Diagnosis prose
   belongs in the audit file; in the email it is optional reading.
5. **Repeat asks escalate, they never re-explain.** `(3rd ask, 11 days)` in the
   subject and on the item. Identical-looking emails get banner blindness.
6. **Use the channel he already acts in.** If an interactive session exists in the
   routine's cycle (the Monday weekly-close chat), write the asks into that
   session's checklist file and drop them from the email. Email then becomes a
   pulse, or nothing.
7. **Mobile first.** Gmail shows ~90 characters of body under the subject. Body
   line 1 is the first ask, not a heading, not "Good news first".
8. **One banner, one list.** If everything is bold, nothing is.

Examples:

```
OK run      ntfy only:  "xero-weekly-close OK · 1 payout applied · Monday: 0 decisions, 2 holds"
ACTION      subject:    "ACTION (1, 2nd ask): pause Privacy 1052 (Ikechi) before 11/09"
            body:       1. Pause Privacy card 1052 (Ikechi) before 11/09 - 1 min, Privacy > Cards.
                           Then: claude -r <uuid> "I paused IKOO, mark paused + update roster"
                        ---- Details (skip) ----
                        No reply since 10/10 ... (diagnosis, numbers, audit path)
FAILED      subject:    "FAILED: xero-weekly-close - fetch_upwork_ledger 401 after 3 retries"
            body:       Upwork rail not assessed; Stripe + Wise ran clean. Re-run: <command>. Log: <path>
```

The re-entry footer below is the "how to act" half of the same design; keep it on
every ACTION email.

### Re-entry footer (shared component, added 2026-09-05)

Any routine that stages a decision for Dimitri (approvals, a draft to polish) must make it trivial to drop back into *that run's* session. Every Desktop routine run is a normal Claude Code session: its transcript is `~/.claude/projects/<cwd-slug>/<uuid>.jsonl`, the Desktop index (`%APPDATA%\Claude\claude-code-sessions\<acct>\<org>\local_<id>.json`) maps `scheduledTaskId` -> `cliSessionId`, and the routine reads its own id from `$CLAUDE_CODE_SESSION_ID` in any Bash step. `claude --resume <uuid>` works from any directory (verified 2026-09-05).

Use the shared helper instead of hand-rolling the email send:

```python
sys.path.insert(0, r"C:\Users\Dimitri\scripts\routine-common")
from reentry import send_email, ntfy, session_id
send_email(profile="business", to=..., subject=..., html_body=...,
           reentry={"sid": session_id(), "cwd": str(ROOT), "routine": "<id>",
                    "prompt": "review the proposals with me and apply what I approve"})
```

The footer renders the exact `claude -r <uuid> "<prompt>"` command in monospace, the sentence "or open this run under Scheduled in the Desktop sidebar", and the documented `claude-cli://open?cwd=...&q=%2Fresume%20<uuid>` deep link as text (Gmail strips custom URL schemes from links). Documented-but-not-built: a tiny https hop page that fires the scheme so the email gets a real button; and `claude://resume?uuid=<cliSessionId>` (undocumented, in the Desktop bundle, untested). Phone path v2: extend `scripts\claude-rc-listener.ps1` to accept `resume:<uuid>`. Details in the module docstring of `reentry.py`.

Pair the footer with a **staging convention** so the resumed session can act: number the proposals in the email and in the routine's durable record (e.g. a `### PROPOSED (rung 3) - awaiting Dimitri` section in a REVIEW_LOG entry), so "approve 1 and 3" is unambiguous.

## Maintenance: Monthly Tune-Up

Every routine should get a periodic review. Check:

1. **Source health** - are any APIs failing silently? Check the run report for feeds showing 0 results.
2. **False positive rate** - review the last 4 triage.md files. If >80% of candidates are being cut, tighten the collection queries.
3. **False negative risk** - are there mentions you know about that the routine missed? Add sources or adjust queries.
4. **SKILL.md drift** - compare the live `~/.claude/scheduled-tasks/<name>/SKILL.md` with the documented mirror in the repo. Sync if they've diverged.
5. **Cost check** - review API usage (Firecrawl dashboard, OpenAI usage page). Adjust query count if overspending.

## Common Pitfalls

- **Shell env shadowing .env:** Always use `load_dotenv(override=True)`. Without `override`, existing shell variables win and you get stale keys.
- **Substring matching for editorial judgment:** Don't use regex or `in` checks for classification that requires context. "elevate code quality" matches "elevate code" but isn't about the company. Let the agent decide.
- **Unscoped Reddit searches:** Reddit's `/search.json` is site-wide. Use `/r/{subreddit}/search.json` with `restrict_sr=true` to avoid noise from unrelated subreddits.
- **Firecrawl search time windows:** `POST /v2/search` takes Google-style `tbs` (`qdr:d`, `qdr:w`, `qdr:m`, `qdr:y`) for recency filtering; there is no `time_period` param. Size the window to the routine cadence (see brand-patrol's `--days` mapping).
- **Overly broad search queries for promo/opportunity scanning:** Broad queries like "Windows speech to text" match anything with those common words. Scope to relevant subreddits AND require intent signals (help, recommend, alternative, looking for).
- **Not saving full LLM responses:** When probing LLMs for brand mentions, save the complete response text - truncating to excerpts makes false positives unauditable.
- **Desktop rewrites scheduled-tasks.json on exit:** If you need to hand-edit this file, quit the Desktop app first (verified working 2026-07-27: quit → edit `cronExpression` → relaunch; the app reads the file at startup). Reading while the app runs is safe. Prefer the Desktop UI's "Custom" cron option when the user is driving.
- **Routines UI creation clobbers pre-staged SKILL.md:** Write the live prompt AFTER the user creates the routine in the UI, not before (see Schedule Configuration).
- **`claude.exe` is ambiguous in process checks:** CLI sessions are also `claude.exe` (`~\.local\bin\claude.exe`). "Is the Desktop app running?" must filter by executable path, not image name — a `tasklist`-by-name watcher will wait forever while CLI sessions are open.

## Migration Checklist (from ChatGPT / other platform)

When migrating a scheduled task from another platform:

1. **Extract the prompt** - copy the exact prompt being used
2. **Identify the data sources** - what APIs, searches, or feeds does it query?
3. **Assess volume** - how many results per run? This determines whether you need a deterministic collection layer or can do everything in the SKILL.md
4. **Design the two layers** - split mechanical collection from editorial judgment
5. **Set up the project** - create `Code/<name>/`, write config.py, main script, template
6. **Write the SKILL.md** - follow the structure above
7. **Deploy** - user creates the routine in the Desktop Scheduled page first as a LOCAL task (Set up manually + "Only on this computer" + folder; see Schedule Configuration), placeholder prompt, THEN copy the real SKILL.md over `~/.claude/scheduled-tasks/<name>/SKILL.md`, then verify cron/cwd in scheduled-tasks.json (read-only is safe)
8. **Test** - trigger a manual run, review the output, fix issues
9. **Iterate** - tune queries, tighten triage rules, adjust schedule based on first few runs
