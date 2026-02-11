# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "click",
#     "requests",
#     "python-dotenv",
#     "markdownify",
# ]
# ///
"""Slack Canvas CLI — Read and edit Slack canvases."""

import json
import os
import re
import sys
from urllib.parse import urlparse

import click
import requests
from dotenv import load_dotenv
from markdownify import markdownify as md


def _load_env():
    """Load .env from CWD, home dir, and script dir."""
    load_dotenv()  # CWD
    load_dotenv(os.path.expanduser("~/.env"))
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


class SlackClient:
    """Handles Slack API calls with browser (xoxc/xoxd) or standard (xoxb/xoxp) tokens."""

    def __init__(self):
        _load_env()
        self.xoxc_token = os.getenv("SLACK_XOXC_TOKEN")
        self.xoxd_cookie = os.getenv("SLACK_XOXD_COOKIE")
        self.standard_token = os.getenv("SLACK_TOKEN")
        self.workspace_url = (os.getenv("SLACK_WORKSPACE_URL") or "https://app.slack.com").rstrip("/")

        if self.xoxc_token and self.xoxd_cookie:
            self.auth_type = "browser"
        elif self.standard_token:
            self.auth_type = "standard"
        else:
            click.echo(
                "Error: No Slack credentials found.\n"
                "Set SLACK_XOXC_TOKEN + SLACK_XOXD_COOKIE (browser tokens)\n"
                "  or SLACK_TOKEN (bot/user token, xoxb-/xoxp-)\n"
                "See SKILL.md for how to extract tokens.",
                err=True,
            )
            sys.exit(1)

    def api(self, method: str, **params) -> dict:
        """Call a Slack Web API method. Dicts/lists in params are JSON-encoded automatically."""
        if self.auth_type == "browser":
            url = f"{self.workspace_url}/api/{method}"
            headers = {
                "Cookie": f"d={self.xoxd_cookie}",
                "Origin": "https://app.slack.com",
            }
            form_data = {"token": self.xoxc_token}
            for k, v in params.items():
                if isinstance(v, (dict, list)):
                    form_data[k] = json.dumps(v)
                elif v is not None:
                    form_data[k] = str(v)
            resp = requests.post(url, headers=headers, data=form_data, timeout=30)
        else:
            url = f"https://slack.com/api/{method}"
            headers = {
                "Authorization": f"Bearer {self.standard_token}",
                "Content-Type": "application/json; charset=utf-8",
            }
            clean = {k: v for k, v in params.items() if v is not None}
            resp = requests.post(url, headers=headers, json=clean, timeout=30)

        resp.raise_for_status()
        result = resp.json()
        if not result.get("ok"):
            error = result.get("error", "unknown_error")
            raise click.ClickException(f"Slack API error: {error}")
        return result

    def download(self, url: str) -> str:
        """Download content from a Slack private URL."""
        headers = {}
        if self.auth_type == "browser":
            headers["Authorization"] = f"Bearer {self.xoxc_token}"
            headers["Cookie"] = f"d={self.xoxd_cookie}"
        else:
            headers["Authorization"] = f"Bearer {self.standard_token}"
        resp = requests.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        return resp.text


def parse_canvas_id(value: str) -> str:
    """Extract canvas ID from a URL or raw ID string."""
    value = value.strip()
    # Raw canvas ID
    if re.match(r"^F[A-Z0-9]{8,}$", value):
        return value
    # Slack URL: https://workspace.slack.com/docs/TXXXXX/FXXXXX
    try:
        parsed = urlparse(value)
        if parsed.hostname and parsed.hostname.endswith(".slack.com"):
            parts = parsed.path.strip("/").split("/")
            for part in parts:
                if re.match(r"^F[A-Z0-9]{8,}$", part):
                    return part
    except Exception:
        pass
    raise click.ClickException(
        f"Cannot parse canvas ID from: {value}\n"
        "Expected a canvas ID (F...) or Slack canvas URL."
    )


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


@click.group()
def cli():
    """Slack Canvas CLI — read and edit Slack canvases."""
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
def read(canvas, raw_html):
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

    click.echo(f"# {title}")
    click.echo(f"Canvas ID: {canvas_id}")
    click.echo("---")

    if raw_html:
        click.echo(html)
    else:
        markdown = md(html, heading_style="ATX", bullets="-")
        click.echo(markdown.strip())


@cli.command()
@click.argument("canvas")
@click.argument("item_text")
def check(canvas, item_text):
    """Mark a checklist item as done (checked)."""
    client = SlackClient()
    canvas_id = parse_canvas_id(canvas)

    # Find the section
    result = client.api(
        "canvases.sections.lookup",
        canvas_id=canvas_id,
        criteria={"contains_text": item_text},
    )
    sections = result.get("sections", [])
    if not sections:
        raise click.ClickException(f"No section found containing: {item_text}")
    if len(sections) > 1:
        click.echo(f"Warning: {len(sections)} sections match. Using first.", err=True)

    section_id = sections[0]["id"]

    # Replace with checked version
    client.api(
        "canvases.edit",
        canvas_id=canvas_id,
        changes=[{
            "operation": "replace",
            "section_id": section_id,
            "document_content": {
                "type": "markdown",
                "markdown": f"- [x] {item_text}\n",
            },
        }],
    )
    click.echo(f"Checked: {item_text}")


@cli.command()
@click.argument("canvas")
@click.argument("item_text")
def uncheck(canvas, item_text):
    """Mark a checklist item as not done (unchecked)."""
    client = SlackClient()
    canvas_id = parse_canvas_id(canvas)

    result = client.api(
        "canvases.sections.lookup",
        canvas_id=canvas_id,
        criteria={"contains_text": item_text},
    )
    sections = result.get("sections", [])
    if not sections:
        raise click.ClickException(f"No section found containing: {item_text}")
    if len(sections) > 1:
        click.echo(f"Warning: {len(sections)} sections match. Using first.", err=True)

    section_id = sections[0]["id"]

    client.api(
        "canvases.edit",
        canvas_id=canvas_id,
        changes=[{
            "operation": "replace",
            "section_id": section_id,
            "document_content": {
                "type": "markdown",
                "markdown": f"- [ ] {item_text}\n",
            },
        }],
    )
    click.echo(f"Unchecked: {item_text}")


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
def extract_token():
    """Extract xoxc token from Slack Desktop's local storage (Windows).

    The xoxd cookie must be extracted separately from your browser
    (Slack > DevTools > Application > Cookies > d).
    """
    import glob
    import platform

    if platform.system() != "Windows":
        raise click.ClickException("Token extraction is only supported on Windows currently.")

    appdata = os.environ.get("APPDATA", "")
    leveldb_dir = os.path.join(appdata, "Slack", "Local Storage", "leveldb")

    if not os.path.isdir(leveldb_dir):
        raise click.ClickException(f"Slack LevelDB not found at: {leveldb_dir}")

    click.echo(f"Scanning: {leveldb_dir}")

    tokens = {}
    for f in sorted(glob.glob(os.path.join(leveldb_dir, "*.ldb"))) + \
             sorted(glob.glob(os.path.join(leveldb_dir, "*.log"))):
        try:
            with open(f, "rb") as fh:
                data = fh.read().decode("utf-8", errors="ignore")

            # Find xoxc tokens
            for m in re.finditer(r"xoxc-[a-zA-Z0-9_-]+", data):
                token = m.group(0)
                tokens[token] = os.path.basename(f)

            # Find team info near the token
            for m in re.finditer(r'"name":"([^"]+)"', data):
                name = m.group(1)
                # Check if there's a token nearby
                start = max(0, m.start() - 200)
                end = min(len(data), m.end() + 500)
                chunk = data[start:end]
                token_match = re.search(r"xoxc-[a-zA-Z0-9_-]+", chunk)
                if token_match:
                    click.echo(f"\nWorkspace: {name}")
                    click.echo(f"Token: {token_match.group(0)}")
                    break
        except Exception:
            continue

    if not tokens:
        click.echo("No xoxc tokens found in Slack Desktop storage.")
        click.echo("Make sure Slack Desktop is installed and you're logged in.")
        return

    click.echo(f"\nFound {len(set(tokens.keys()))} unique token(s)")
    click.echo("\nTo complete setup, you also need the xoxd cookie:")
    click.echo("  1. Open Slack in your browser (not desktop app)")
    click.echo("  2. DevTools (F12) > Application > Cookies > d")
    click.echo("  3. Copy the value (starts with xoxd-)")
    click.echo("\nSet these environment variables:")
    click.echo("  SLACK_XOXC_TOKEN=<token above>")
    click.echo("  SLACK_XOXD_COOKIE=<xoxd cookie>")
    click.echo("  SLACK_WORKSPACE_URL=https://yourworkspace.slack.com")


if __name__ == "__main__":
    cli()
