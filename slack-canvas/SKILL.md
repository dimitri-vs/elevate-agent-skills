---
name: slack-canvas
description: Full Slack access for the agent — read threads, DMs and channel history, send messages as the user (no "Sent using Claude" footer), watch for replies and reactions in real time, view screenshots and files shared in a conversation, and read or edit canvases. Use whenever a task involves Slack: reading a conversation from a URL or channel ID, viewing an image someone posted, messaging someone, waiting on a reply, or working with a canvas.
---

# Slack CLI

Gives the agent the same Slack access the user has: read any conversation they
can see, send messages that appear as them, monitor for replies in real time,
and read/edit canvases.

**All commands:** `cd ~/.claude/skills/slack-canvas && uv run slack_canvas.py <command>`

If anything fails, run `doctor` first — it reports which tokens exist, who they
authenticate as, and which commands they unlock.

## Command reference

| Command | What it does |
|---|---|
| `doctor` | Diagnose credentials and capabilities. Start here when something breaks. |
| `thread <url\|-c CH -t TS>` | Fetch a thread (parent + replies) |
| `history <target>` | Channel/DM history; **thread replies included by default** |
| `send <target>` | Post a message **as the user** |
| `watch [target]` | Real-time monitor for new messages and reactions |
| `download <id|url>` | Save attachments locally (images, PDFs, any file) |
| `read <canvas>` | Canvas content as markdown |
| `search [query]` | Find canvases by title |
| `channel-canvas <ch>` | Canvas attached to a channel's canvas tab |
| `check` / `uncheck` | Toggle a canvas checklist item *(needs OAuth token)* |
| `append` / `insert` / `rename` | Edit canvas content *(needs OAuth token)* |
| `test-auth` | Quick identity check |
| `extract-token` | Pull fresh browser tokens from Slack Desktop |

### Targets — what you can pass

Anywhere a target is accepted (`history`, `send`, `watch`):

- **Channel/DM ID** — `C0AQ5K17UDS`, `D08Q9F1HQGG`
- **Slack URL** — `https://ws.slack.com/archives/D08Q9F1HQGG/p1787211618301419`
- **Channel name** — `#general`
- **A person** — `@sam`, or their email (opens/reuses the DM)

Archive URLs are parsed automatically. A "Copy link" URL taken from a *reply*
carries `?thread_ts=` — that parent timestamp is used, so you get the whole
thread rather than a single orphaned message.

## Agent guidelines

1. **Resolve ambiguity** — if the user says a name like "Jessica", search for
   matching channels/people first. If several match, ask before fetching.
2. **Read into context, don't print** — the user usually wants you to *know* the
   conversation, not to see it dumped. Use `-o <file>` only when the content is
   needed beyond this session or the range is large.
3. **Scope by timeframe, not message count** — ask "last 30 days including
   threads?" rather than "how many messages?". `--since 30d` is a good default.
4. **Threads are on by default.** A channel read without replies is usually
   missing the actual conversation. Reach for `--no-threads` only to skim a
   large volume of parent messages cheaply for context.

## Reading conversations

```bash
# A thread from a pasted URL (the most common case)
uv run slack_canvas.py thread "https://ws.slack.com/archives/C07SZJ355RV/p1771540768173789"

# Explicit channel + timestamp
uv run slack_canvas.py thread -c C07SZJ355RV -t 1771540768.173789

# DM history — threads included automatically
uv run slack_canvas.py history D08Q9F1HQGG --since 30d

# Skim a busy channel for context, parents only
uv run slack_canvas.py history "#engineering" --since 90d --no-threads

# Date range, saved to a file
uv run slack_canvas.py history "#general" --since 2026-02-01 --until 2026-02-15 -o out.md
```

Options: `--since` / `--until` (`YYYY-MM-DD` or relative `30d`, `2w`, `3m`),
`--limit/-n` (default 1000; warns if it truncates), `--threads/--no-threads`,
`--files/--no-files`, `--all-files`, `--files-dir`, `--output/-o`.

Output format — timestamps are **local time** with a UTC offset, and reactions
are shown because a 👍 is often the actual answer:

```
**Username** (2026-02-19 10:32 UTC-05):
The main message text
reactions: :+1: x1

  > **Replier** (2026-02-19 10:35 UTC-05):
  > A threaded reply
```

### Screenshots and attachments

Screenshots usually carry the point of the message — the error, the broken
layout, the row that looks wrong. So **image attachments download automatically**
and the output gives you a local path:

```
[image] image.png (png, 2016x1048, 277.5KB) id=F0BTHBNFH4Z
        -> C:\Users\...\Temp\slack-files\F0BTHBNFH4Z-image.png
```

**Read that path.** It is a normal PNG — one read and you see what the sender
saw. Do not hand-roll `files.info` plus a `files-pri` curl; that is what this
replaces.

Downloading costs no context — the bytes go to disk, not into the conversation
— so the default is on. You still choose whether to read any given image.

- Non-image files (PDFs, zips, video) are **listed but not fetched** — the line
  carries the file ID, so `download <file-id>` is the one-step way to get it.
  `--all-files` fetches them inline instead.
- Files land in a temp dir, **swept after 24 hours**. Treat it as a cache, not
  storage; copy anything you need to keep. Override with `--files-dir` or
  `SLACK_FILES_DIR`.
- Re-reading a thread reuses what is already on disk, so it costs nothing.
- `--no-files` skips downloading, pairing with `--no-threads` for a fast skim.
- Caps: 25 files per call, 500 MB each.

```bash
# Fetch a specific attachment (works for non-images too)
uv run slack_canvas.py download F0BTHBNFH4Z

# Grab every attachment in a thread, as JSON
uv run slack_canvas.py download "https://ws.slack.com/archives/D08.../p178..." --json
```

**Videos** download at full resolution — a screen recording is usually someone
pointing at a specific thing, and Slack's `mp4_low` variant is 640x360, far too
coarse to read UI text. Attachment lines show duration (`mp4, 4:55, 38.1MB`) so
you can pick a range. Once downloaded, hand the path to the `visual-transcript`
skill, which is what actually reads the content:

```bash
uv run slack_canvas.py download F0BSF51V5DJ
# -> C:\Users\...\slack-files\F0BSF51V5DJ-Screenshare....mp4
cd ~/.claude/skills/visual-transcript
uv run transcribe.py "<that path>" --mode high --range 3:00-4:30
```

Clip a range rather than sending the whole video — see that skill for costs.

If a download reports **"got an HTML page, not the file"**, the session token or
cookie is stale — run `doctor`, then `extract-token`.

## Sending messages

Sends use the browser session, so messages appear as the user — no
"Sent using Claude" footer.

```bash
uv run slack_canvas.py send "#general" -m "shipping in 10"
uv run slack_canvas.py send "@sam" -f draft.md              # body from a file
uv run slack_canvas.py send <url> --thread <url> -m "bump"  # reply in-thread
uv run slack_canvas.py send "#design" -m "see attached" -a shot.png
uv run slack_canvas.py send "@sam" -m "..." --dry-run       # preview, sends nothing
```

- `-f/--file` avoids shell-quoting problems entirely — prefer it for anything
  multi-line, and it pairs directly with the `humanize` workflow's temp file.
- `-a/--attach` is repeatable.
- `--dry-run` prints the resolved channel and body without sending.

**Identifying as an agent:** because these post as the user, say so when the
recipient should know. The user will often ask for this explicitly ("start with
'Claude here,'"). Follow that instruction literally — there is no automatic
footer to fall back on.

**Approval:** per the user's global rules, human-facing outbound messages go
through `humanize` first, and approval is only valid *after* the humanized draft
has been shown. Draft → show → wait → send.

## Watching for replies and reactions

`watch` opens Slack's real-time websocket, so it pushes events as they happen —
no polling loop, no backoff to hand-roll, and it sees everything the user sees
including DMs.

```bash
# Block until someone replies in this DM, then return
uv run slack_canvas.py watch D08Q9F1HQGG --exit-on-first

# Watch one thread for 30 minutes
uv run slack_canvas.py watch --thread <url> -T 1800

# Everything, messages only
uv run slack_canvas.py watch --no-reactions
```

Options: `--thread`, `--timeout/-T` (default 900s), `--exit-on-first`,
`--reactions/--no-reactions` (default on), `--include-self`.

Prints one JSON object per line:

```json
{"event":"message","channel":"D08Q...","user":"U08P...","name":"Oz","ts":"...","text":"sounds good"}
{"event":"reaction_added","channel":"D08Q...","name":"Oz","reaction":"+1","item_ts":"..."}
```

Exits `0` when an event matched, `2` on timeout with nothing seen.

**Reactions count as answers.** A 👍 on a question usually means "yes"/"ok" —
treat `reaction_added` as a reply for the purpose of an ask-and-wait loop.

**Pattern for ask-then-wait:** send the message, then `watch <target>
--exit-on-first -T <seconds>`. One blocking call replaces a polling loop. For
waits longer than the timeout, re-invoke `watch`; it is cheap to restart and
reconnects automatically if the socket drops mid-run.

## Authentication

Two tokens, doing different jobs. `doctor` reports the state of both.

| | xoxc/xoxd (browser session) | SLACK_TOKEN (OAuth xoxb/xoxp) |
|---|---|---|
| Read canvases, threads, history | Yes | Yes |
| Send **as the user** | Yes | No (posts as the bot) |
| `watch` real-time events | Yes | No |
| **Edit** canvases | **No** | Yes (`canvases:write`) |
| `canvases.sections.lookup` | **No** | Yes (`canvases:read`) |

```
SLACK_XOXC_TOKEN=xoxc-...
SLACK_XOXD_COOKIE=xoxd-...          # must be URL-encoded
SLACK_WORKSPACE_URL=https://yourworkspace.slack.com
SLACK_TOKEN=xoxb-...                # optional; only for canvas edits
```

`canvases.edit` and `canvases.sections.lookup` reject session tokens with
`not_allowed_token_type`. That is a hard Slack limitation, not a config error —
canvas *edits* require an OAuth app; canvas *reads* do not.

A **bot** token (xoxb) only reaches canvases in channels the bot belongs to.

### Refreshing browser tokens

```bash
uv run slack_canvas.py extract-token              # print
uv run slack_canvas.py extract-token --write-env  # merge into .env
```

Extracts xoxc from LevelDB and, on Windows, decrypts xoxd from the Cookies DB
via DPAPI. Close Slack Desktop first if the DB is locked (there is a
StaleCookies fallback if you don't). On macOS/Linux, grab xoxd manually:
DevTools → Application → Cookies → `d`.

`--write-env` **merges** — other keys in `.env` are preserved.

**Token lifetime:** xoxd cookies last ~1 year and die on logout; xoxc may rotate
when Slack Desktop restarts. On `not_authed`/`invalid_auth`, re-extract.

## Canvas content

`read` returns markdown with checklist state preserved. Editing uses the
markdown below.

| Element | Syntax |
|---|---|
| Unchecked / checked item | `- [ ] Task` / `- [x] Task` |
| Headings | `# H1`, `## H2`, `### H3` |
| Bold / italic / strike | `**b**`, `_i_`, `~s~` |
| Lists | `- item`, `1. item` |
| Code | `` `inline` ``, ```` ```block``` ```` |
| Link | `[text](url)` |
| User / channel mention | `![](@U123ABCDEFG)` / `![](#C123ABC456)` |
| Divider / quote | `---` / `> text` |

**Limitations:** no Block Kit (markdown only); tables cap at 300 cells; some
nesting (e.g. a list inside a blockquote) throws `canvas_editing_failed`; each
checklist item is its own section, so items are replaced one at a time.

## Errors

| Error | Meaning |
|---|---|
| `canvas_not_found` | Wrong ID, or no access |
| `not_authed` / `invalid_auth` | Token expired — re-extract |
| `not_allowed_token_type` | Session token used on an edit API — needs `SLACK_TOKEN` |
| `missing_scope` | OAuth token lacks `canvases:read`/`canvases:write` |
| `canvas_editing_failed` | Invalid markdown structure or unsupported nesting |
| `free_teams_cannot_edit_standalone_canvases` | Workspace on the free plan |
| `no_permission` | No write access to this canvas |
| `channel_not_found` | Bad ID, or the account isn't in that conversation |

## Notes

- **Token security**: xoxc/xoxd grant full account access. Never commit them,
  never send them to a third-party fetcher.
- **Rate limits**: handled automatically — HTTP 429 is retried honouring
  `Retry-After`. User names are cached in `.user-cache.json` to keep bulk reads
  cheap.
- **Enterprise Grid**: on Grid workspaces, programmatic browser-token use can
  trip hijack detection and kill sessions. Not a concern on standard workspaces.
- **`sections.lookup` returns IDs only**, never content. To read content, use
  `read`; to target an edit, match on `contains_text`.
- **Raw API details** — undocumented endpoints, curl equivalents, the RTM
  handshake, canvas edit operations: `reference/canvas-api.md`. Only needed when
  the CLI doesn't cover the case.

## References

- Token extraction approach inspired by [stablyai/agent-slack](https://github.com/stablyai/agent-slack)
