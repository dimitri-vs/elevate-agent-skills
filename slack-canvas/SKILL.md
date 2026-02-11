---
name: slack-canvas
description: Read and edit Slack canvases — to-do lists, notes, checklists
---

# Slack Canvas API

Read and edit Slack canvases programmatically using curl. Supports reading full canvas content, marking checklist items as done, adding notes, and more.

## Authentication

Canvas operations use browser-extracted tokens (xoxc/xoxd) which act as your logged-in Slack session. This gives access to any canvas you can see in the Slack UI — including shared team canvases — without needing a Slack app or admin approval.

### Required Environment Variables

```
SLACK_XOXC_TOKEN=xoxc-...
SLACK_XOXD_COOKIE=xoxd-...
SLACK_WORKSPACE_URL=https://yourworkspace.slack.com
```

### How to Get Tokens

1. Open Slack in your **browser** (not the desktop app)
2. Open DevTools (F12) → Network tab
3. Perform any action in Slack (send a message, open a channel)
4. Find any request to `https://yourworkspace.slack.com/api/`
5. From the request headers:
   - **xoxc token**: Look in the request body/form data for a `token` field starting with `xoxc-`
   - **xoxd cookie**: Look in the `Cookie` header for `d=xoxd-...` (URL-decode it)

Alternatively, from the browser console on a Slack tab:
```javascript
// Get xoxc token
JSON.parse(localStorage.localConfig_v2).teams[Object.keys(JSON.parse(localStorage.localConfig_v2).teams)[0]].token

// Get xoxd cookie (from document.cookie)
document.cookie.split('; ').find(c => c.startsWith('d=')).slice(2)
```

**Token lifetime**: These tokens last as long as your browser session. They expire when you log out of Slack. Re-extract when needed.

### Alternative: OAuth Bot/User Token

If you have a Slack app with `canvases:read`, `canvases:write`, and `files:read` scopes, you can use a standard token instead:

```
SLACK_TOKEN=xoxb-... (bot) or xoxp-... (user)
```

Bot tokens can only access canvases in channels the bot is a member of. User tokens have the same access as the user. For shared team canvases you don't own, user tokens (xoxp-) or browser tokens (xoxc-) are required.

## Auth Headers

All curl commands below use these headers. Construct them based on which token type you have:

**Browser tokens (xoxc/xoxd):**
```bash
-H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
-H "Cookie: d=$SLACK_XOXD_COOKIE" \
-H "Content-Type: application/json; charset=utf-8"
```

**Standard tokens (xoxb/xoxp):**
```bash
-H "Authorization: Bearer $SLACK_TOKEN" \
-H "Content-Type: application/json; charset=utf-8"
```

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
  -H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE" \
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
  -H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE" \
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
  -H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "canvas_id": "CANVAS_ID",
    "criteria": { "contains_text": "Buy groceries" }
  }' | python -c "import sys,json; print(json.load(sys.stdin)['sections'][0]['id'])")

# 2. Replace with checked version
curl -s "https://slack.com/api/canvases.edit" \
  -H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE" \
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
  -H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE" \
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
  -H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE" \
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
  -H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE" \
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
