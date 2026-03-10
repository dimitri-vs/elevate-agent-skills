---
name: slack-canvas
description: Slack CLI — read canvases, conversations, and threads
---

# Slack CLI

Read Slack canvases, conversation threads, and channel history. Supports reading full canvas content, marking checklist items as done, adding notes, fetching threads, and more.

## Agent Guidelines

When the user asks to read a channel or conversation:

1. **Resolve ambiguity** — If the user says a name like "Jessica", search for matching channels first. If multiple match, ask which one they mean before fetching.
2. **Confirm intent** — The user typically wants you to *read* the conversation for context in the current session, not have it printed out. Save to a file with `-o` when the content is needed beyond this session. Default to reading into context silently unless told otherwise.
3. **Scope the fetch** — Ask about timeframe (e.g. "last 30 days including threads?") rather than message count. Default to a reasonable window like `--since 30d --threads`. Suggest saving to a file for large ranges.

## Authentication

Canvas operations use browser-extracted tokens (xoxc/xoxd) which act as your logged-in Slack session. This gives access to any canvas you can see in the Slack UI — including shared team canvases — without needing a Slack app or admin approval.

### Required Environment Variables

```
SLACK_XOXC_TOKEN=xoxc-...
SLACK_XOXD_COOKIE=xoxd-...
SLACK_WORKSPACE_URL=https://yourworkspace.slack.com
```

### How to Get Tokens

**Automatic extraction (recommended):**

```bash
# Extracts xoxc from LevelDB + xoxd from Cookies DB (Windows: DPAPI decryption)
cd "<skill-directory>" && uv run slack_canvas.py extract-token

# Same, but write directly to .env
cd "<skill-directory>" && uv run slack_canvas.py extract-token --write-env
```

Close Slack Desktop first if the Cookies DB is locked. On macOS/Linux, the xoxd cookie must still be extracted manually from the browser.

**Manual extraction (fallback):**

Open Slack in your browser → DevTools (F12) → Network tab → find any `/api/` request:
- **xoxc token**: in request body/form data, `token` field
- **xoxd cookie**: in Cookie header, `d=xoxd-...` (must be URL-encoded in .env)

**Important**: The xoxd cookie value must be **URL-encoded** (e.g. `%2F` not `/`, `%2B` not `+`). The `extract-token` command handles this automatically.

**Token lifetime**: xoxd cookies last ~1 year (expiry is set by Slack). They are invalidated when you log out. xoxc tokens may rotate when Slack Desktop restarts.

### Alternative: OAuth Bot/User Token

If you have a Slack app with `canvases:read`, `canvases:write`, and `files:read` scopes, you can use a standard token instead:

```
SLACK_TOKEN=xoxb-... (bot) or xoxp-... (user)
```

Bot tokens can only access canvases in channels the bot is a member of. User tokens have the same access as the user. For shared team canvases you don't own, user tokens (xoxp-) or browser tokens (xoxc-) are required.

### Token Capabilities

| Operation | xoxc (browser) | xoxp (user OAuth) | xoxb (bot OAuth) |
|---|---|---|---|
| Read canvas content | Yes | Yes | Yes (channels bot is in) |
| Search/list canvases | Yes | Yes | Yes (limited scope) |
| Edit canvas content | **No** | Yes (`canvases:write`) | Yes (`canvases:write`) |
| Sections lookup | **No** | Yes (`canvases:read`) | Yes (`canvases:read`) |

The `canvases.edit` and `canvases.sections.lookup` APIs explicitly reject xoxc session tokens (`not_allowed_token_type`). For edit operations, you **must** use an OAuth token (xoxb/xoxp) from a Slack app with the appropriate scopes.

## Auth Headers

All curl commands below use these headers. Construct them based on which token type you have:

**Browser tokens (xoxc/xoxd):** — must use workspace-specific URL (`$SLACK_WORKSPACE_URL/api/...`)
```bash
-H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
-H "Cookie: d=$SLACK_XOXD_COOKIE" \
-H "Content-Type: application/json; charset=utf-8"
```

**Standard tokens (xoxb/xoxp):** — can use `https://slack.com/api/...`
```bash
-H "Authorization: Bearer $SLACK_TOKEN" \
-H "Content-Type: application/json; charset=utf-8"
```

## API Base URL

The curl examples below use `https://slack.com/api/`. This works for standard tokens (xoxb/xoxp). **For browser tokens (xoxc), replace with your workspace URL:** `https://yourworkspace.slack.com/api/`. The CLI script handles this automatically via `SLACK_WORKSPACE_URL`.

## Canvas IDs

Canvas IDs start with `F` followed by alphanumeric characters (e.g., `F08ABC1234X`). You can find them in:

- **Canvas URL**: `https://workspace.slack.com/docs/TXXXXX/FXXXXX` — the `F...` part is the canvas ID
- **`files.list` API** with `types=canvas` to discover canvases

```bash
# List canvases visible to you
curl -s "https://slack.com/api/files.list?types=canvas&count=20" \
  -H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE" | python -m json.tool
```

## Reading a Canvas

There is no direct "get canvas content" API. The standard approach is to treat the canvas as a file, get its download URL, and fetch the HTML.

### Step 1: Get canvas metadata

```bash
curl -s "https://slack.com/api/files.info?file=CANVAS_ID_HERE" \
  -H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE" | python -m json.tool
```

The response includes:
- `file.title` — canvas title
- `file.url_private_download` — URL to download the HTML content
- `file.url_private` — alternative URL (view, not download)
- `file.created`, `file.updated` — timestamps

### Step 2: Download the HTML content

```bash
curl -s "URL_PRIVATE_DOWNLOAD_HERE" \
  -H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE"
```

This returns the full canvas as HTML. The HTML contains all text, formatting, checklists, headings, etc. Claude can read and understand this HTML directly — no conversion library needed.

### Quick One-Liner (read canvas as HTML)

```bash
# Get the download URL, then fetch content
DOWNLOAD_URL=$(curl -s "https://slack.com/api/files.info?file=CANVAS_ID" \
  -H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE" | python -c "import sys,json; print(json.load(sys.stdin)['file']['url_private_download'])")

curl -s "$DOWNLOAD_URL" \
  -H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE"
```

## Editing a Canvas

Use `canvases.edit` to modify canvas content. Edits are targeted using section IDs obtained from `canvases.sections.lookup`.

### Finding Sections

`canvases.sections.lookup` finds sections by text content or heading type. It returns section IDs, not content.

```bash
curl -s "https://slack.com/api/canvases.sections.lookup" \
  -H "Authorization: Bearer $SLACK_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "canvas_id": "CANVAS_ID_HERE",
    "criteria": {
      "contains_text": "Buy groceries"
    }
  }'
```

**Response:**
```json
{
  "ok": true,
  "sections": [
    { "id": "temp:C:VXX37d27db7718d44e28803566ae" }
  ]
}
```

**Criteria options:**
- `contains_text` — match sections containing this text
- `section_types` — filter by heading level: `"h1"`, `"h2"`, `"h3"`, or `"any_header"`
- Both can be combined

### Editing Content

`canvases.edit` accepts a `changes` array. Each change specifies an operation, content, and optionally a section ID.

```bash
curl -s "https://slack.com/api/canvases.edit" \
  -H "Authorization: Bearer $SLACK_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "canvas_id": "CANVAS_ID_HERE",
    "changes": [
      {
        "operation": "OPERATION_HERE",
        "section_id": "SECTION_ID_HERE",
        "document_content": {
          "type": "markdown",
          "markdown": "YOUR MARKDOWN HERE\n"
        }
      }
    ]
  }'
```

**Operations:**
| Operation | Section ID | Description |
|---|---|---|
| `insert_at_end` | not needed | Append content to the end of the canvas |
| `insert_at_start` | not needed | Prepend content to the beginning |
| `insert_after` | required | Insert content after a specific section |
| `insert_before` | required | Insert content before a specific section |
| `replace` | optional | Replace a section's content, or the entire canvas if no section_id |
| `delete` | required | Remove a section |

**Renaming a canvas:**
```json
{
  "canvas_id": "CANVAS_ID",
  "changes": [
    {
      "operation": "rename",
      "title_content": {
        "type": "markdown",
        "markdown": "New Canvas Title"
      }
    }
  ]
}
```

## Common Patterns

### Mark a Checklist Item as Done

This is the core use case. Two API calls: find the section, then replace it.

```bash
# 1. Find the section containing the todo item text
SECTION_ID=$(curl -s "https://slack.com/api/canvases.sections.lookup" \
  -H "Authorization: Bearer $SLACK_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "canvas_id": "CANVAS_ID",
    "criteria": { "contains_text": "Buy groceries" }
  }' | python -c "import sys,json; print(json.load(sys.stdin)['sections'][0]['id'])")

# 2. Replace with checked version
curl -s "https://slack.com/api/canvases.edit" \
  -H "Authorization: Bearer $SLACK_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "{
    \"canvas_id\": \"CANVAS_ID\",
    \"changes\": [
      {
        \"operation\": \"replace\",
        \"section_id\": \"$SECTION_ID\",
        \"document_content\": {
          \"type\": \"markdown\",
          \"markdown\": \"- [x] Buy groceries\n\"
        }
      }
    ]
  }"
```

### Add a Note to the End of a Canvas

```bash
curl -s "https://slack.com/api/canvases.edit" \
  -H "Authorization: Bearer $SLACK_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "canvas_id": "CANVAS_ID",
    "changes": [
      {
        "operation": "insert_at_end",
        "document_content": {
          "type": "markdown",
          "markdown": "## Notes\n\nAdded by automation on 2026-02-10.\n"
        }
      }
    ]
  }'
```

### Add a New Checklist Item Under a Heading

```bash
# 1. Find the heading section
HEADING_ID=$(curl -s "https://slack.com/api/canvases.sections.lookup" \
  -H "Authorization: Bearer $SLACK_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "canvas_id": "CANVAS_ID",
    "criteria": {
      "section_types": ["any_header"],
      "contains_text": "To-Do"
    }
  }' | python -c "import sys,json; print(json.load(sys.stdin)['sections'][0]['id'])")

# 2. Insert new item after the heading
curl -s "https://slack.com/api/canvases.edit" \
  -H "Authorization: Bearer $SLACK_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "{
    \"canvas_id\": \"CANVAS_ID\",
    \"changes\": [
      {
        \"operation\": \"insert_after\",
        \"section_id\": \"$HEADING_ID\",
        \"document_content\": {
          \"type\": \"markdown\",
          \"markdown\": \"- [ ] New task item\n\"
        }
      }
    ]
  }"
```

## Canvas Markdown Format

Canvases use markdown with some Slack-specific extensions:

| Element | Syntax |
|---|---|
| Unchecked item | `- [ ] Task text` |
| Checked item | `- [x] Task text` |
| Heading 1 | `# Title` |
| Heading 2 | `## Subtitle` |
| Heading 3 | `### Section` |
| Bold | `**text**` |
| Italic | `_text_` |
| Strikethrough | `~text~` |
| Bulleted list | `- item` |
| Numbered list | `1. item` |
| Code block | `` ```code``` `` |
| Inline code | `` `code` `` |
| Link | `[text](url)` |
| User mention | `![](@U123ABCDEFG)` |
| Channel mention | `![](#C123ABC456)` |
| Divider | `---` |
| Quote | `> text` |

**Limitations:**
- Block Kit is NOT supported in canvases (markdown only)
- Tables have a 300-cell limit
- Nested formatting in certain combinations can cause `canvas_editing_failed` errors (e.g., a list inside a blockquote)
- Each checklist item is its own section — you replace one item at a time

## Conversation Commands

### Fetch a Thread

Retrieve a full conversation thread (parent message + all replies) from an archive URL or channel + timestamp.

```bash
# Most common: paste an archive URL
cd "<skill-directory>" && uv run slack_canvas.py thread "https://workspace.slack.com/archives/C07SZJ355RV/p1771540768173789"

# Or use explicit channel + timestamp
cd "<skill-directory>" && uv run slack_canvas.py thread -c C07SZJ355RV -t 1771540768.173789

# Save to file
cd "<skill-directory>" && uv run slack_canvas.py thread URL -o /path/to/output.txt
```

The archive URL's `p`-prefixed timestamp (e.g. `p1771540768173789`) is automatically converted to the API format (`1771540768.173789`).

### Fetch Channel History

Retrieve recent messages from a channel, optionally with thread replies inlined.

```bash
# Last 30 days with threads
cd "<skill-directory>" && uv run slack_canvas.py history "#general" --since 30d --threads

# Date range
cd "<skill-directory>" && uv run slack_canvas.py history "#general" --since 2026-02-01 --until 2026-02-15

# Last 2 weeks, save to file
cd "<skill-directory>" && uv run slack_canvas.py history "#engineering" --since 2w --threads -o messages.md
```

Options:
- `--since`: Start date — `YYYY-MM-DD` or relative (`30d`, `2w`, `3m`)
- `--until`: End date — same formats (defaults to now)
- `--limit` / `-n`: Max messages to fetch (default 200)
- `--threads`: Fetch and inline thread replies under each parent message
- `--output` / `-o`: Write to file instead of stdout

### Output Format

Messages are formatted as:

```
**Username** (2026-02-19 10:32):
The main message text here

  > **Replier** (2026-02-19 10:35):
  > A threaded reply
```

User IDs are resolved to display names automatically.

### Output to File (`-o`)

The `thread`, `history`, and `read` commands all support `-o` / `--output` to write to a file instead of stdout:

```bash
cd "<skill-directory>" && uv run slack_canvas.py thread URL -o ~/Downloads/thread.txt
cd "<skill-directory>" && uv run slack_canvas.py history "#general" -o messages.md
cd "<skill-directory>" && uv run slack_canvas.py read F0AE1U25EF7 -o canvas.md
```

## Error Handling

Common error responses from canvas APIs:

| Error | Meaning |
|---|---|
| `canvas_not_found` | Canvas ID is wrong or you don't have access |
| `not_authed` / `invalid_auth` | Token expired or invalid — re-extract from browser |
| `missing_scope` | Token lacks `canvases:read` or `canvases:write` (only for OAuth tokens) |
| `canvas_editing_failed` | Invalid markdown structure or unsupported nesting |
| `free_teams_cannot_edit_standalone_canvases` | Workspace is on free plan |
| `no_permission` | You don't have write access to this canvas |

## Notes

- **Token security**: xoxc/xoxd tokens grant full account access. Never commit them. Use env vars.
- **Rate limiting**: Slack rate-limits API calls. For canvas operations (infrequent by nature), this is rarely an issue. If you hit a 429, wait for the `Retry-After` header value.
- **Enterprise Grid**: On Grid workspaces, programmatic use of browser tokens may trigger hijack detection and kill your sessions. For non-Grid workspaces this is not a concern.
- **sections.lookup returns IDs only**: It does NOT return section content. To read content, use the file download approach. To find a specific section for editing, use `contains_text` in the criteria.
- **Canvas content via HTML**: The downloaded HTML is a rich document. Claude can parse it directly without needing a conversion library. Look for checklist items as `<li>` elements with checkbox attributes.

## References

- Token extraction approach inspired by [stablyai/agent-slack](https://github.com/stablyai/agent-slack)
