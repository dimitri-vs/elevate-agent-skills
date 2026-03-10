# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "click",
#     "requests",
#     "python-dotenv",
#     "markdownify",
#     "cryptography",
# ]
# ///
"""Slack CLI — Read Slack canvases, conversations, and threads.

Token extraction approach inspired by stablyai/agent-slack
(https://github.com/stablyai/agent-slack).
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import click
import requests
from dotenv import load_dotenv
from markdownify import MarkdownConverter


def _load_env():
    """Load .env files. Priority: CWD > home > script dir > system env."""
    # Load in reverse priority order with override=True (last loaded wins)
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=True)
    load_dotenv(os.path.expanduser("~/.env"), override=True)
    load_dotenv(override=True)  # CWD


_EDIT_METHODS = {"canvases.edit", "canvases.sections.lookup", "canvases.create",
                  "canvases.access.set", "canvases.access.delete", "canvases.delete",
                  "conversations.canvases.create"}


class SlackClient:
    """Handles Slack API calls with browser (xoxc/xoxd) or standard (xoxb/xoxp) tokens."""

    def __init__(self):
        _load_env()
        self.xoxc_token = os.getenv("SLACK_XOXC_TOKEN")
        self.xoxd_cookie = os.getenv("SLACK_XOXD_COOKIE")
        self.standard_token = os.getenv("SLACK_TOKEN")
        self.workspace_url = (os.getenv("SLACK_WORKSPACE_URL") or "https://app.slack.com").rstrip("/")

        self.has_browser = bool(self.xoxc_token and self.xoxd_cookie)
        self.has_standard = bool(self.standard_token)

        if not self.has_browser and not self.has_standard:
            click.echo(
                "Error: No Slack credentials found.\n"
                "Set SLACK_XOXC_TOKEN + SLACK_XOXD_COOKIE (browser tokens)\n"
                "  or SLACK_TOKEN (bot/user token, xoxb-/xoxp-)\n"
                "See SKILL.md for how to extract tokens.",
                err=True,
            )
            sys.exit(1)

    @staticmethod
    def _encode_cookie(cookie: str) -> str:
        """URL-encode the xoxd cookie value for use in Cookie headers."""
        if "%2F" in cookie or "%2B" in cookie:
            return cookie  # already encoded
        return quote(cookie, safe="")

    def _auth_type_for(self, method: str) -> str:
        """Pick the right token: standard for edit methods, browser for reads."""
        if method in _EDIT_METHODS:
            if not self.has_standard:
                scope = "canvases:read" if method == "canvases.sections.lookup" else "canvases:write"
                raise click.ClickException(
                    f"{method} requires an OAuth token (xoxb-/xoxp-), not a browser token.\n"
                    f"Set SLACK_TOKEN in .env with a token that has {scope} scope."
                )
            return "standard"
        if self.has_browser:
            return "browser"
        return "standard"

    def _do_api_request(self, auth_type: str, method: str, **params) -> requests.Response:
        """Execute a raw Slack API request."""
        if auth_type == "browser":
            url = f"{self.workspace_url}/api/{method}"
            headers = {
                "Cookie": f"d={self._encode_cookie(self.xoxd_cookie)}",
                "Origin": self.workspace_url,
            }
            form_data = {"token": self.xoxc_token}
            for k, v in params.items():
                if isinstance(v, (dict, list)):
                    form_data[k] = json.dumps(v)
                elif v is not None:
                    form_data[k] = str(v)
            return requests.post(url, headers=headers, data=form_data, timeout=30)
        else:
            url = f"https://slack.com/api/{method}"
            headers = {
                "Authorization": f"Bearer {self.standard_token}",
                "Content-Type": "application/json; charset=utf-8",
            }
            clean = {k: v for k, v in params.items() if v is not None}
            return requests.post(url, headers=headers, json=clean, timeout=30)

    def api(self, method: str, **params) -> dict:
        """Call a Slack Web API method. Dicts/lists in params are JSON-encoded automatically."""
        auth_type = self._auth_type_for(method)
        resp = self._do_api_request(auth_type, method, **params)
        resp.raise_for_status()
        result = resp.json()
        if not result.get("ok"):
            error = result.get("error", "unknown_error")
            # Fallback: retry with standard token if browser auth failed
            if (auth_type == "browser" and self.has_standard
                    and error in ("invalid_auth", "not_authed", "token_revoked")):
                click.echo(f"Browser token error ({error}), retrying with standard token...", err=True)
                resp = self._do_api_request("standard", method, **params)
                resp.raise_for_status()
                result = resp.json()
                if not result.get("ok"):
                    raise click.ClickException(f"Slack API error: {result.get('error', 'unknown_error')}")
                return result
            raise click.ClickException(f"Slack API error: {error}")
        return result

    def download(self, url: str) -> str:
        """Download content from a Slack private URL."""
        headers = {}
        if self.has_browser:
            headers["Authorization"] = f"Bearer {self.xoxc_token}"
            headers["Cookie"] = f"d={self._encode_cookie(self.xoxd_cookie)}"
        else:
            headers["Authorization"] = f"Bearer {self.standard_token}"
        resp = requests.get(url, headers=headers, timeout=60)
        # Fallback to standard token if browser auth failed
        if not resp.ok and self.has_browser and self.has_standard:
            click.echo(f"Browser download failed (HTTP {resp.status_code}), retrying with standard token...", err=True)
            resp = requests.get(url, headers={"Authorization": f"Bearer {self.standard_token}"}, timeout=60)
        resp.raise_for_status()
        return resp.text

    def resolve_users(self, user_ids: set[str]) -> dict[str, str]:
        """Batch-resolve user IDs to display names via users.info."""
        user_map = {}
        for uid in user_ids:
            try:
                result = self.api("users.info", user=uid)
                user = result.get("user", {})
                user_map[uid] = (
                    user.get("profile", {}).get("display_name")
                    or user.get("real_name")
                    or user.get("name")
                    or uid
                )
            except click.ClickException:
                user_map[uid] = uid
        return user_map


class SlackCanvasConverter(MarkdownConverter):
    """Markdown converter that preserves Slack checklist checkbox state."""

    def convert_li(self, el, text, convert_as_inline):
        checked = el.get("data-checked")
        if checked is not None:
            result = super().convert_li(el, text, convert_as_inline)
            marker = "[x]" if checked == "true" else "[ ]"
            result = re.sub(r"^(\s*[-*])\s", rf"\1 {marker} ", result, count=1)
            return result
        return super().convert_li(el, text, convert_as_inline)


def _parse_checklist_items(html: str) -> list[dict]:
    """Parse checklist items from canvas HTML, returning text and checked state."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    items = []
    for li in soup.find_all("li"):
        checked_attr = li.get("data-checked")
        if checked_attr is None:
            cb = li.find("input", attrs={"type": "checkbox"})
            if cb:
                checked = cb.has_attr("checked")
            else:
                continue  # Not a checklist item
        else:
            checked = checked_attr == "true"
        text = li.get_text(strip=True)
        if text:
            items.append({"text": text, "checked": checked})
    return items


def parse_slack_url(value: str) -> dict:
    """Parse a Slack URL or raw ID into a typed result.

    Returns one of:
        {"type": "thread", "channel": "C...", "ts": "1771540768.173789"}
        {"type": "channel", "channel": "C..."}
        {"type": "canvas", "canvas_id": "F..."}
    """
    value = value.strip()

    # Raw IDs by prefix
    if re.match(r"^F[A-Z0-9]{8,}$", value):
        return {"type": "canvas", "canvas_id": value}
    if re.match(r"^[CDG][A-Z0-9]{8,}$", value):
        return {"type": "channel", "channel": value}

    # URL parsing
    try:
        parsed = urlparse(value)
        if parsed.hostname and parsed.hostname.endswith(".slack.com"):
            parts = parsed.path.strip("/").split("/")

            # Archive URLs: /archives/CXXXXXX/p1771540768173789
            if len(parts) >= 2 and parts[0] == "archives":
                channel = parts[1]
                if len(parts) >= 3 and parts[2].startswith("p"):
                    raw_ts = parts[2][1:]  # strip the 'p'
                    ts = raw_ts[:10] + "." + raw_ts[10:]
                    return {"type": "thread", "channel": channel, "ts": ts}
                return {"type": "channel", "channel": channel}

            # Canvas URLs: /docs/TXXXXX/FXXXXX
            for part in parts:
                if re.match(r"^F[A-Z0-9]{8,}$", part):
                    return {"type": "canvas", "canvas_id": part}
    except Exception:
        pass

    raise click.ClickException(
        f"Cannot parse Slack URL or ID from: {value}\n"
        "Expected a canvas ID (F...), channel ID (C.../D.../G...), "
        "or Slack URL (/archives/... or /docs/...)."
    )


def parse_canvas_id(value: str) -> str:
    """Extract canvas ID from a URL or raw ID string."""
    result = parse_slack_url(value)
    if result["type"] != "canvas":
        raise click.ClickException(
            f"Expected a canvas ID or canvas URL, got {result['type']}: {value}"
        )
    return result["canvas_id"]


def resolve_channel_id(client: SlackClient, channel: str) -> str:
    """Resolve a channel name or ID to a channel ID."""
    channel = channel.strip().lstrip("#")
    if re.match(r"^[CDG][A-Z0-9]{8,}$", channel):
        return channel
    # Search by name
    cursor = None
    while True:
        params = {"types": "public_channel,private_channel", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        result = client.api("conversations.list", **params)
        for ch in result.get("channels", []):
            if ch.get("name") == channel or ch.get("name_normalized") == channel:
                return ch["id"]
        cursor = result.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    raise click.ClickException(f"Channel not found: #{channel}")


def _ts_to_datetime(ts: str) -> str:
    """Convert a Slack timestamp to a human-readable date string."""
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _format_messages(messages: list[dict], user_map: dict[str, str], indent: bool = False) -> str:
    """Format Slack messages into readable text.

    Args:
        messages: List of Slack message dicts.
        user_map: Mapping of user IDs to display names.
        indent: If True, prefix every line with "  > " (for thread replies).
    """
    lines = []
    for msg in messages:
        user = user_map.get(msg.get("user", ""), msg.get("user", "unknown"))
        ts = _ts_to_datetime(msg.get("ts", "0"))
        text = msg.get("text", "")
        prefix = "  > " if indent else ""
        lines.append(f"{prefix}**{user}** ({ts}):")
        for line in text.split("\n"):
            lines.append(f"{prefix}{line}")
        lines.append("")
    return "\n".join(lines)


def _output_text(text: str, output_path: str | None):
    """Write text to a file or stdout."""
    if output_path:
        path = os.path.expanduser(output_path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        click.echo(f"Written to {path}", err=True)
    else:
        click.echo(text)


@click.group()
def cli():
    """Slack CLI — read canvases, conversations, and threads."""
    pass


@cli.command()
def test_auth():
    """Verify Slack credentials are working."""
    client = SlackClient()
    result = client.api("auth.test")
    click.echo(json.dumps({
        "ok": True,
        "user": result.get("user"),
        "user_id": result.get("user_id"),
        "team": result.get("team"),
        "team_id": result.get("team_id"),
        "url": result.get("url"),
    }, indent=2))


@cli.command()
@click.argument("url_or_channel", required=False)
@click.option("--channel", "-c", "channel_opt", help="Channel ID (alternative to URL)")
@click.option("--ts", "-t", "ts_opt", help="Thread timestamp (alternative to URL)")
@click.option("--output", "-o", "output_path", help="Write output to file instead of stdout")
def thread(url_or_channel, channel_opt, ts_opt, output_path):
    """Fetch a conversation thread.

    Most common usage — pass an archive URL:

        uv run slack_canvas.py thread "https://workspace.slack.com/archives/C.../p..."

    Or use explicit channel + timestamp:

        uv run slack_canvas.py thread -c C07SZJ355RV -t 1771540768.173789
    """
    client = SlackClient()

    if url_or_channel:
        result = parse_slack_url(url_or_channel)
        if result["type"] == "thread":
            channel_id = result["channel"]
            ts = result["ts"]
        elif result["type"] == "channel":
            if not ts_opt:
                raise click.ClickException("Channel URL given but no timestamp. Use -t to specify the thread timestamp.")
            channel_id = result["channel"]
            ts = ts_opt
        else:
            raise click.ClickException(f"Expected a thread or channel URL, got {result['type']}")
    elif channel_opt and ts_opt:
        channel_id = resolve_channel_id(client, channel_opt)
        ts = ts_opt
    else:
        raise click.ClickException("Provide a Slack archive URL, or use -c CHANNEL -t TIMESTAMP")

    resp = client.api("conversations.replies", channel=channel_id, ts=ts, limit=1000)
    messages = resp.get("messages", [])
    if not messages:
        click.echo("No messages found.")
        return

    # Resolve user names
    user_ids = {m.get("user") for m in messages if m.get("user")}
    user_map = client.resolve_users(user_ids)

    # Format: first message is the parent, rest are replies
    parts = []
    parts.append(_format_messages(messages[:1], user_map))
    if len(messages) > 1:
        parts.append(_format_messages(messages[1:], user_map, indent=True))

    _output_text("\n".join(parts).strip(), output_path)


def _parse_date(value: str) -> float:
    """Parse a date string into a Unix timestamp. Accepts YYYY-MM-DD or relative like '30d', '2w'."""
    value = value.strip()
    # Relative: 30d, 2w, 7d, etc.
    m = re.match(r"^(\d+)([dwm])$", value)
    if m:
        from datetime import timedelta
        amount = int(m.group(1))
        unit = m.group(2)
        delta = {"d": timedelta(days=amount), "w": timedelta(weeks=amount),
                 "m": timedelta(days=amount * 30)}[unit]
        return (datetime.now(tz=timezone.utc) - delta).timestamp()
    # Absolute: YYYY-MM-DD
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        raise click.ClickException(f"Cannot parse date: {value}  (expected YYYY-MM-DD or relative like 30d, 2w)")


@cli.command()
@click.argument("channel")
@click.option("--limit", "-n", default=200, help="Max messages to fetch (default 200)")
@click.option("--since", "since", help="Start date (YYYY-MM-DD or relative: 30d, 2w)")
@click.option("--until", "until_", help="End date (YYYY-MM-DD or relative: 7d)")
@click.option("--threads", is_flag=True, help="Inline thread replies under each message")
@click.option("--output", "-o", "output_path", help="Write output to file instead of stdout")
def history(channel, limit, since, until_, threads, output_path):
    """Fetch recent messages from a channel.

    \b
    Examples:
        uv run slack_canvas.py history "#general" --since 30d
        uv run slack_canvas.py history "#general" --since 2026-02-01 --until 2026-02-15
        uv run slack_canvas.py history C07SZJ355RV --since 2w --threads
        uv run slack_canvas.py history "#engineering" --since 7d -o messages.md
    """
    client = SlackClient()
    channel_id = resolve_channel_id(client, channel)

    api_params: dict = {"channel": channel_id, "limit": min(limit, 200)}
    if since:
        api_params["oldest"] = str(_parse_date(since))
    if until_:
        api_params["latest"] = str(_parse_date(until_))

    # Paginate to collect up to `limit` messages
    messages = []
    cursor = None
    while len(messages) < limit:
        if cursor:
            api_params["cursor"] = cursor
        resp = client.api("conversations.history", **api_params)
        batch = resp.get("messages", [])
        if not batch:
            break
        messages.extend(batch)
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    messages = messages[:limit]

    if not messages:
        click.echo("No messages found.")
        return

    # Reverse to chronological order (API returns newest first)
    messages.reverse()

    # Collect all user IDs (including from thread replies)
    all_user_ids = {m.get("user") for m in messages if m.get("user")}

    # Optionally fetch thread replies
    thread_replies: dict[str, list[dict]] = {}
    if threads:
        for msg in messages:
            if msg.get("reply_count", 0) > 0:
                r = client.api("conversations.replies", channel=channel_id, ts=msg["ts"], limit=1000)
                replies = r.get("messages", [])[1:]  # skip parent
                thread_replies[msg["ts"]] = replies
                all_user_ids.update(m.get("user") for m in replies if m.get("user"))

    user_map = client.resolve_users(all_user_ids)

    # Format output
    parts = []
    for msg in messages:
        parts.append(_format_messages([msg], user_map).rstrip())
        if msg["ts"] in thread_replies and thread_replies[msg["ts"]]:
            parts.append(_format_messages(thread_replies[msg["ts"]], user_map, indent=True).rstrip())
        parts.append("")

    _output_text("\n".join(parts).strip(), output_path)


@cli.command()
@click.argument("query", required=False)
@click.option("--channel", "-c", help="Channel name or ID to filter by")
@click.option("--limit", "-n", default=20, help="Max results (default 20)")
def search(query, channel, limit):
    """Search for canvases by title. Lists all canvases if no query given."""
    client = SlackClient()
    params = {"types": "canvas", "count": min(limit, 100)}
    if channel:
        params["channel"] = resolve_channel_id(client, channel)

    result = client.api("files.list", **params)
    files = result.get("files", [])

    if query:
        q = query.lower()
        files = [f for f in files if q in (f.get("title") or f.get("name") or "").lower()]

    if not files:
        click.echo("No canvases found.")
        return

    for f in files[:limit]:
        title = f.get("title") or f.get("name") or "(untitled)"
        fid = f.get("id", "?")
        channels = ", ".join(f.get("channels", []))
        ch_info = f" [#{channels}]" if channels else ""
        click.echo(f"  {fid}  {title}{ch_info}")


@cli.command("channel-canvas")
@click.argument("channel")
def channel_canvas(channel):
    """Get the canvas attached to a channel's canvas tab."""
    client = SlackClient()
    channel_id = resolve_channel_id(client, channel)
    result = client.api("conversations.info", channel=channel_id)
    ch = result.get("channel", {})
    canvas = ch.get("properties", {}).get("canvas", {})
    canvas_id = canvas.get("file_id") or canvas.get("id")
    if not canvas_id:
        raise click.ClickException(f"No canvas tab found for channel {channel}")
    click.echo(f"Canvas ID: {canvas_id}")
    click.echo(f"Channel: #{ch.get('name', channel_id)}")


@cli.command()
@click.argument("canvas")
@click.option("--raw-html", is_flag=True, help="Output raw HTML instead of markdown")
@click.option("--output", "-o", "output_path", help="Write output to file instead of stdout")
def read(canvas, raw_html, output_path):
    """Read a canvas's full content. Accepts canvas ID (F...) or URL."""
    client = SlackClient()
    canvas_id = parse_canvas_id(canvas)

    # Get file info
    result = client.api("files.info", file=canvas_id)
    file_info = result.get("file", {})
    title = file_info.get("title") or file_info.get("name") or "(untitled)"
    download_url = file_info.get("url_private_download") or file_info.get("url_private")

    if not download_url:
        raise click.ClickException("Canvas has no download URL")

    # Download HTML
    html = client.download(download_url)

    parts = [f"# {title}", f"Canvas ID: {canvas_id}", "---"]

    if raw_html:
        parts.append(html)
    else:
        markdown = SlackCanvasConverter(heading_style="ATX", bullets="-").convert(html)
        markdown = markdown.replace("\u200b", "")
        markdown = re.sub(r"\n{3,}", "\n\n", markdown)
        parts.append(markdown.strip())

    _output_text("\n".join(parts), output_path)


def _resolve_checklist_item(client: SlackClient, canvas_id: str, item_text: str) -> dict:
    """Read a canvas and find the checklist item matching item_text.

    Returns dict with keys: text (str), checked (bool).
    Raises ClickException if no match or canvas has no download URL.
    """
    result = client.api("files.info", file=canvas_id)
    file_info = result.get("file", {})
    download_url = file_info.get("url_private_download") or file_info.get("url_private")
    if not download_url:
        raise click.ClickException("Canvas has no download URL")

    html = client.download(download_url)
    items = _parse_checklist_items(html)

    q = item_text.lower()
    matches = [i for i in items if q in i["text"].lower()]
    if not matches:
        raise click.ClickException(f"No checklist item found containing: {item_text}")

    # Prefer exact match
    exact = [m for m in matches if m["text"].lower() == q]
    if len(exact) == 1:
        return exact[0]
    if len(matches) == 1:
        return matches[0]

    click.echo(f"Warning: {len(matches)} items match '{item_text}'. Using first:", err=True)
    for m in matches:
        state = "[x]" if m["checked"] else "[ ]"
        click.echo(f"  - {state} {m['text']}", err=True)
    return matches[0]


@cli.command()
@click.argument("canvas")
@click.argument("item_text")
def check(canvas, item_text):
    """Mark a checklist item as done (checked)."""
    client = SlackClient()
    canvas_id = parse_canvas_id(canvas)

    match = _resolve_checklist_item(client, canvas_id, item_text)

    if match["checked"]:
        click.echo(f"Already checked: {match['text']}")
        return

    # Find the section by its actual text
    result = client.api(
        "canvases.sections.lookup",
        canvas_id=canvas_id,
        criteria={"contains_text": match["text"]},
    )
    sections = result.get("sections", [])
    if not sections:
        raise click.ClickException(f"Section not found for: {match['text']}")

    client.api(
        "canvases.edit",
        canvas_id=canvas_id,
        changes=[{
            "operation": "replace",
            "section_id": sections[0]["id"],
            "document_content": {
                "type": "markdown",
                "markdown": f"- [x] {match['text']}\n",
            },
        }],
    )
    click.echo(f"Checked: {match['text']}")


@cli.command()
@click.argument("canvas")
@click.argument("item_text")
def uncheck(canvas, item_text):
    """Mark a checklist item as not done (unchecked)."""
    client = SlackClient()
    canvas_id = parse_canvas_id(canvas)

    match = _resolve_checklist_item(client, canvas_id, item_text)

    if not match["checked"]:
        click.echo(f"Already unchecked: {match['text']}")
        return

    result = client.api(
        "canvases.sections.lookup",
        canvas_id=canvas_id,
        criteria={"contains_text": match["text"]},
    )
    sections = result.get("sections", [])
    if not sections:
        raise click.ClickException(f"Section not found for: {match['text']}")

    client.api(
        "canvases.edit",
        canvas_id=canvas_id,
        changes=[{
            "operation": "replace",
            "section_id": sections[0]["id"],
            "document_content": {
                "type": "markdown",
                "markdown": f"- [ ] {match['text']}\n",
            },
        }],
    )
    click.echo(f"Unchecked: {match['text']}")


@cli.command()
@click.argument("canvas")
@click.argument("content")
def append(canvas, content):
    """Append content to the end of a canvas. Content is markdown."""
    client = SlackClient()
    canvas_id = parse_canvas_id(canvas)

    # Unescape literal \n in CLI args to real newlines
    content = content.replace("\\n", "\n")
    if not content.endswith("\n"):
        content += "\n"

    client.api(
        "canvases.edit",
        canvas_id=canvas_id,
        changes=[{
            "operation": "insert_at_end",
            "document_content": {
                "type": "markdown",
                "markdown": content,
            },
        }],
    )
    click.echo("Content appended.")


@cli.command()
@click.argument("canvas")
@click.argument("content")
@click.option("--after", "after_text", required=True, help="Insert after section containing this text")
def insert(canvas, content, after_text):
    """Insert content after a specific section (found by text match)."""
    client = SlackClient()
    canvas_id = parse_canvas_id(canvas)

    # Find the target section
    result = client.api(
        "canvases.sections.lookup",
        canvas_id=canvas_id,
        criteria={"contains_text": after_text},
    )
    sections = result.get("sections", [])
    if not sections:
        raise click.ClickException(f"No section found containing: {after_text}")
    if len(sections) > 1:
        click.echo(f"Warning: {len(sections)} sections match. Using first.", err=True)

    section_id = sections[0]["id"]

    content = content.replace("\\n", "\n")
    if not content.endswith("\n"):
        content += "\n"

    client.api(
        "canvases.edit",
        canvas_id=canvas_id,
        changes=[{
            "operation": "insert_after",
            "section_id": section_id,
            "document_content": {
                "type": "markdown",
                "markdown": content,
            },
        }],
    )
    click.echo(f"Content inserted after section containing: {after_text}")


@cli.command()
@click.argument("canvas")
@click.argument("new_title")
def rename(canvas, new_title):
    """Rename a canvas."""
    client = SlackClient()
    canvas_id = parse_canvas_id(canvas)

    client.api(
        "canvases.edit",
        canvas_id=canvas_id,
        changes=[{
            "operation": "rename",
            "title_content": {
                "type": "markdown",
                "markdown": new_title,
            },
        }],
    )
    click.echo(f"Renamed to: {new_title}")


@cli.command("extract-token")
@click.option("--write-env", is_flag=True, help="Write credentials to .env in the script directory")
def extract_token(write_env):
    """Extract xoxc token and xoxd cookie from Slack Desktop.

    On Windows, decrypts the xoxd cookie from Slack's Cookies DB via DPAPI.
    On macOS/Linux, extracts xoxc from LevelDB (xoxd must be grabbed manually).

    Close Slack Desktop first if the Cookies DB is locked.
    """
    import glob
    import platform
    import subprocess

    def _is_wsl() -> bool:
        try:
            with open("/proc/version", "r") as f:
                return "microsoft" in f.read().lower()
        except (OSError, IOError):
            return False

    system = platform.system()
    wsl = system == "Linux" and _is_wsl()

    if system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        slack_dir = os.path.join(appdata, "Slack")
    elif wsl:
        try:
            win_appdata = subprocess.check_output(
                ["cmd.exe", "/c", "echo", "%APPDATA%"],
                text=True, stderr=subprocess.DEVNULL,
            ).strip().rstrip("\r")
            wsl_appdata = subprocess.check_output(
                ["wslpath", "-u", win_appdata],
                text=True, stderr=subprocess.DEVNULL,
            ).strip()
            slack_dir = os.path.join(wsl_appdata, "Slack")
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise click.ClickException(
                "Cannot locate Windows APPDATA from WSL.\n"
                "Run from Windows directly, or set the path manually."
            )
        click.echo("Detected WSL — using Windows Slack data directory.")
    elif system == "Darwin":
        slack_dir = os.path.join(
            os.path.expanduser("~"), "Library", "Application Support", "Slack",
        )
    elif system == "Linux":
        slack_dir = os.path.join(os.path.expanduser("~"), ".config", "Slack")
    else:
        raise click.ClickException(f"Unsupported platform: {system}")

    leveldb_dir = os.path.join(slack_dir, "Local Storage", "leveldb")
    if not os.path.isdir(leveldb_dir):
        raise click.ClickException(f"Slack LevelDB not found at: {leveldb_dir}")

    # --- Extract xoxc token from LevelDB ---
    click.echo(f"Scanning LevelDB: {leveldb_dir}")
    xoxc_token = None
    workspace_name = None
    for f in sorted(glob.glob(os.path.join(leveldb_dir, "*.ldb")), reverse=True) + \
             sorted(glob.glob(os.path.join(leveldb_dir, "*.log")), reverse=True):
        try:
            with open(f, "rb") as fh:
                data = fh.read().decode("utf-8", errors="ignore")
            for m in re.finditer(r"xoxc-[a-zA-Z0-9_-]{50,}", data):
                xoxc_token = m.group(0)
            if not workspace_name:
                for m in re.finditer(r'"name":"([^"]+)"', data):
                    start = max(0, m.start() - 200)
                    chunk = data[start:m.end() + 500]
                    if re.search(r"xoxc-", chunk):
                        workspace_name = m.group(1)
        except Exception:
            continue

    if not xoxc_token:
        raise click.ClickException("No xoxc token found. Is Slack Desktop installed and logged in?")

    click.echo(f"  xoxc token: {xoxc_token[:40]}...")
    if workspace_name:
        click.echo(f"  Workspace: {workspace_name}")

    # --- Extract xoxd cookie (Windows only: DPAPI + AES-GCM) ---
    xoxd_cookie = None
    if system == "Windows":
        xoxd_cookie = _extract_xoxd_windows(slack_dir)

    if xoxd_cookie:
        click.echo(f"  xoxd cookie: {xoxd_cookie[:40]}...")
    else:
        if system != "Windows":
            click.echo("\nxoxd cookie must be extracted manually on this platform:")
            click.echo("  1. Open Slack in your browser")
            click.echo("  2. DevTools (F12) > Application > Cookies > d")
            click.echo("  3. Copy the value (starts with xoxd-)")

    # --- Output ---
    click.echo("\n--- Credentials ---")
    click.echo(f"SLACK_XOXC_TOKEN={xoxc_token}")
    if xoxd_cookie:
        click.echo(f"SLACK_XOXD_COOKIE={xoxd_cookie}")
    click.echo("SLACK_WORKSPACE_URL=https://YOURWORKSPACE.slack.com")

    if write_env and xoxd_cookie:
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        with open(env_path, "w") as f:
            f.write(f'SLACK_XOXC_TOKEN="{xoxc_token}"\n')
            f.write(f'SLACK_XOXD_COOKIE="{xoxd_cookie}"\n')
            f.write('SLACK_WORKSPACE_URL="https://YOURWORKSPACE.slack.com"\n')
        click.echo(f"\nWritten to {env_path}")
        click.echo("  Edit SLACK_WORKSPACE_URL to your actual workspace domain.")


def _extract_xoxd_windows(slack_dir: str) -> str | None:
    """Decrypt the xoxd cookie from Slack's Cookies DB on Windows (DPAPI + AES-GCM)."""
    import ctypes
    import ctypes.wintypes
    import shutil
    import sqlite3
    import tempfile

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    # 1. Decrypt AES key from Local State via DPAPI
    local_state_path = os.path.join(slack_dir, "Local State")
    if not os.path.isfile(local_state_path):
        click.echo("  Warning: Local State not found, cannot decrypt cookies", err=True)
        return None

    with open(local_state_path, "r") as f:
        encrypted_key_b64 = json.load(f)["os_crypt"]["encrypted_key"]
    dpapi_blob = __import__("base64").b64decode(encrypted_key_b64)[5:]  # strip DPAPI prefix

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    blob_in = DATA_BLOB(len(dpapi_blob), ctypes.create_string_buffer(dpapi_blob, len(dpapi_blob)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        click.echo("  Warning: DPAPI decryption failed", err=True)
        return None
    aes_key = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)

    # 2. Read cookie from Cookies DB (try copy first, fall back to StaleCookies)
    cookies_db = os.path.join(slack_dir, "Network", "Cookies")
    stale_dir = os.path.join(slack_dir, "Network")
    enc_value = None

    # Try the main Cookies DB
    try:
        tmp = os.path.join(tempfile.gettempdir(), "slack_cookies_tmp")
        shutil.copy2(cookies_db, tmp)
        conn = sqlite3.connect(tmp)
        row = conn.execute(
            "SELECT encrypted_value FROM cookies WHERE name='d' AND host_key LIKE '%slack.com' LIMIT 1"
        ).fetchone()
        conn.close()
        os.unlink(tmp)
        if row:
            enc_value = row[0]
    except (PermissionError, OSError):
        click.echo("  Cookies DB locked, trying StaleCookies...", err=True)

    # Fallback: StaleCookies files (available even when Slack is running)
    if enc_value is None:
        for sf in sorted(os.listdir(stale_dir), reverse=True):
            if not sf.startswith("StaleCookies"):
                continue
            try:
                conn = sqlite3.connect(os.path.join(stale_dir, sf))
                row = conn.execute(
                    "SELECT encrypted_value FROM cookies WHERE name='d' AND host_key LIKE '%slack.com' LIMIT 1"
                ).fetchone()
                conn.close()
                if row:
                    enc_value = row[0]
                    click.echo(f"  Using cookie from {sf} (close Slack for freshest cookie)", err=True)
                    break
            except Exception:
                continue

    if enc_value is None:
        click.echo("  Warning: No d cookie found in Cookies DB", err=True)
        return None

    # 3. Decrypt: v10 prefix (3 bytes) + nonce (12 bytes) + ciphertext
    plaintext = AESGCM(aes_key).decrypt(enc_value[3:15], enc_value[15:], None)
    plaintext_str = plaintext.decode("utf-8", errors="replace")
    m = re.search(r"xoxd-[A-Za-z0-9%+/=]+", plaintext_str)
    if not m:
        click.echo("  Warning: Decrypted cookie does not contain xoxd-", err=True)
        return None

    from urllib.parse import unquote
    return quote(unquote(m.group(0)), safe="")


if __name__ == "__main__":
    cli()
