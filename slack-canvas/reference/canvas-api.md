# Raw Slack Canvas API reference

Read this only when the CLI doesn't cover what you need — an unusual canvas
operation, or debugging a failure from `check` / `append` / `insert` / `rename`.
For everything routine, the CLI commands in SKILL.md are fewer steps and handle
auth, URL parsing and rate limits for you.

## Auth headers

**Browser tokens (xoxc/xoxd)** — must use the workspace-specific host
(`$SLACK_WORKSPACE_URL/api/...`), not `slack.com`:

```bash
-H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
-H "Cookie: d=$SLACK_XOXD_COOKIE" \
-H "Content-Type: application/json; charset=utf-8"
```

**Standard tokens (xoxb/xoxp)** — can use `https://slack.com/api/...`:

```bash
-H "Authorization: Bearer $SLACK_TOKEN" \
-H "Content-Type: application/json; charset=utf-8"
```

Examples below use `https://slack.com/api/`. For browser tokens, swap in your
workspace URL. The CLI does this automatically via `SLACK_WORKSPACE_URL`.

> Note: `*.slack.com` is wildcard DNS and Slack routes by token, so a *wrong*
> workspace subdomain still returns HTTP 200. A bad `SLACK_WORKSPACE_URL` fails
> silently rather than loudly — `slack_canvas.py doctor` checks for this.

## Canvas IDs

Canvas IDs start with `F` (e.g. `F08ABC1234X`). Find them in:

- **Canvas URL**: `https://workspace.slack.com/docs/TXXXXX/FXXXXX` — the `F...` part
- **`files.list`** with `types=canvas`

```bash
curl -s "https://slack.com/api/files.list?types=canvas&count=20" \
  -H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE" | python -m json.tool
```

`files.list` is paginated with `page`/`pages`. A title search must page through
all results — filtering only the first page hides older canvases.

## Reading a canvas

There is no direct "get canvas content" API. Treat the canvas as a file: get its
download URL, then fetch the HTML.

### Step 1 — metadata

```bash
curl -s "https://slack.com/api/files.info?file=CANVAS_ID_HERE" \
  -H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE" | python -m json.tool
```

Response includes `file.title`, `file.url_private_download`, `file.url_private`,
`file.created`, `file.updated`.

### Step 2 — download the HTML

```bash
curl -s "URL_PRIVATE_DOWNLOAD_HERE" \
  -H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE"
```

Returns the canvas as HTML — all text, formatting, checklists, headings. Claude
reads this directly; no conversion library needed. Checklist items are `<li>`
elements carrying a `data-checked` attribute.

### One-liner

```bash
DOWNLOAD_URL=$(curl -s "https://slack.com/api/files.info?file=CANVAS_ID" \
  -H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE" | python -c "import sys,json; print(json.load(sys.stdin)['file']['url_private_download'])")

curl -s "$DOWNLOAD_URL" \
  -H "Authorization: Bearer $SLACK_XOXC_TOKEN" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE"
```

## Editing a canvas

**Requires an OAuth token** (`SLACK_TOKEN`, xoxb/xoxp with `canvases:write`).
`canvases.edit` and `canvases.sections.lookup` reject xoxc session tokens with
`not_allowed_token_type` — verified, not a guess.

### Finding sections

`canvases.sections.lookup` finds sections by text or heading type. It returns
section **IDs only**, never content.

```bash
curl -s "https://slack.com/api/canvases.sections.lookup" \
  -H "Authorization: Bearer $SLACK_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "canvas_id": "CANVAS_ID_HERE",
    "criteria": { "contains_text": "Buy groceries" }
  }'
```

```json
{ "ok": true, "sections": [ { "id": "temp:C:VXX37d27db7718d44e28803566ae" } ] }
```

Criteria: `contains_text`, `section_types` (`"h1"`, `"h2"`, `"h3"`, `"any_header"`),
or both combined.

### Applying changes

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
        "document_content": { "type": "markdown", "markdown": "YOUR MARKDOWN HERE\n" }
      }
    ]
  }'
```

| Operation | Section ID | Description |
|---|---|---|
| `insert_at_end` | not needed | Append to the end of the canvas |
| `insert_at_start` | not needed | Prepend to the beginning |
| `insert_after` | required | Insert after a specific section |
| `insert_before` | required | Insert before a specific section |
| `replace` | optional | Replace a section, or the whole canvas if omitted |
| `delete` | required | Remove a section |

**Rename:**

```json
{
  "canvas_id": "CANVAS_ID",
  "changes": [
    { "operation": "rename",
      "title_content": { "type": "markdown", "markdown": "New Canvas Title" } }
  ]
}
```

## Common patterns

### Mark a checklist item done

Two calls: find the section, replace it.

```bash
SECTION_ID=$(curl -s "https://slack.com/api/canvases.sections.lookup" \
  -H "Authorization: Bearer $SLACK_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{ "canvas_id": "CANVAS_ID", "criteria": { "contains_text": "Buy groceries" } }' \
  | python -c "import sys,json; print(json.load(sys.stdin)['sections'][0]['id'])")

curl -s "https://slack.com/api/canvases.edit" \
  -H "Authorization: Bearer $SLACK_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "{
    \"canvas_id\": \"CANVAS_ID\",
    \"changes\": [
      { \"operation\": \"replace\", \"section_id\": \"$SECTION_ID\",
        \"document_content\": { \"type\": \"markdown\", \"markdown\": \"- [x] Buy groceries\n\" } }
    ]
  }"
```

**Gotcha:** replacing with plain text destroys any inline formatting the item
had (bold, links, mentions). Rebuild the item's markdown rather than writing its
plain-text rendering — `slack_canvas.py check` does this for you.

### Append a note

```bash
curl -s "https://slack.com/api/canvases.edit" \
  -H "Authorization: Bearer $SLACK_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "canvas_id": "CANVAS_ID",
    "changes": [
      { "operation": "insert_at_end",
        "document_content": { "type": "markdown", "markdown": "## Notes\n\nAdded by automation.\n" } }
    ]
  }'
```

### Add a checklist item under a heading

```bash
HEADING_ID=$(curl -s "https://slack.com/api/canvases.sections.lookup" \
  -H "Authorization: Bearer $SLACK_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{ "canvas_id": "CANVAS_ID",
        "criteria": { "section_types": ["any_header"], "contains_text": "To-Do" } }' \
  | python -c "import sys,json; print(json.load(sys.stdin)['sections'][0]['id'])")

curl -s "https://slack.com/api/canvases.edit" \
  -H "Authorization: Bearer $SLACK_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "{
    \"canvas_id\": \"CANVAS_ID\",
    \"changes\": [
      { \"operation\": \"insert_after\", \"section_id\": \"$HEADING_ID\",
        \"document_content\": { \"type\": \"markdown\", \"markdown\": \"- [ ] New task item\n\" } }
    ]
  }"
```

## Sending messages and files (raw)

The CLI's `send` covers this; the raw calls are here for reference.

```bash
# Post as the session user (xoxc)
curl -s -X POST "$SLACK_WORKSPACE_URL/api/chat.postMessage" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE" \
  --data-urlencode "token=$SLACK_XOXC_TOKEN" \
  --data-urlencode "channel=C123" \
  --data-urlencode "text=hello"

# Open a DM first if you only have a user ID
curl -s -X POST "$SLACK_WORKSPACE_URL/api/conversations.open" \
  -H "Cookie: d=$SLACK_XOXD_COOKIE" \
  --data-urlencode "token=$SLACK_XOXC_TOKEN" \
  --data-urlencode "users=U123"
```

`users.lookupByEmail` rejects xoxc — resolve people via `users.list` or from IDs
seen in channel history.

File uploads use `files.getUploadURLExternal` → POST the bytes to the returned
`upload_url` → `files.completeUploadExternal`. The legacy `files.upload` was
retired in 2025.

## Real-time events (RTM)

`rtm.connect` returns a WSS URL for xoxc tokens, but the gateway rejects the
token on the querystring. The session cookie must be sent on the **websocket
handshake**:

```python
ws = create_connection(url, header=[f"Cookie: d={xoxd}", f"Origin: {workspace_url}"])
```

Delivers `message`, `reaction_added`, and `reaction_removed` for everything the
user can see, DMs included. Note that `reaction_*` events carry the channel at
`event["item"]["channel"]`, not `event["channel"]`. `rtm.start` is rejected
outright. This is what `slack_canvas.py watch` wraps.
