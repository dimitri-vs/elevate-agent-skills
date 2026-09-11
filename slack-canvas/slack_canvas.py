# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "click",
#     "requests",
#     "python-dotenv",
#     "markdownify",
#     "beautifulsoup4",
#     "cryptography",
#     "websocket-client",
# ]
# ///
"""Slack CLI — read, send, and monitor Slack; read and edit canvases.

Token extraction approach inspired by stablyai/agent-slack
(https://github.com/stablyai/agent-slack).
"""

import html as _html
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse, parse_qs

import click
import requests
from dotenv import load_dotenv
from markdownify import MarkdownConverter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_env():
    """Load .env files. Priority: CWD > home > script dir > system env."""
    # Load in reverse priority order with override=True (last loaded wins)
    load_dotenv(os.path.join(SCRIPT_DIR, ".env"), override=True)
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

        self._user_cache: dict[str, str] | None = None
        self._user_cache_path = os.path.join(SCRIPT_DIR, ".user-cache.json")
        self._users_list: list[dict] | None = None

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
        """URL-encode the xoxd cookie value for use in Cookie headers.

        Detects prior encoding by looking for any %XX escape, so an
        already-encoded cookie is never double-escaped (%2F -> %252F).
        """
        if re.search(r"%[0-9A-Fa-f]{2}", cookie):
            return cookie  # already encoded
        return quote(cookie, safe="")

    def cookie_header(self) -> str:
        """The `d=...` Cookie header value for browser-token requests."""
        return f"d={self._encode_cookie(self.xoxd_cookie)}"

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
                "Cookie": self.cookie_header(),
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

    def _request_with_backoff(self, auth_type: str, method: str, **params) -> requests.Response:
        """Execute a request, honouring HTTP 429 Retry-After (up to 5 attempts)."""
        for attempt in range(5):
            resp = self._do_api_request(auth_type, method, **params)
            if resp.status_code != 429:
                return resp
            wait = float(resp.headers.get("Retry-After", 2 ** attempt))
            wait = min(wait, 60)
            click.echo(f"Rate limited on {method}, waiting {wait:.0f}s...", err=True)
            time.sleep(wait)
        return resp

    def api(self, method: str, **params) -> dict:
        """Call a Slack Web API method. Dicts/lists in params are JSON-encoded automatically."""
        auth_type = self._auth_type_for(method)
        resp = self._request_with_backoff(auth_type, method, **params)
        resp.raise_for_status()
        result = resp.json()
        if not result.get("ok"):
            error = result.get("error", "unknown_error")
            # Fallback: retry with standard token if browser auth failed
            if (auth_type == "browser" and self.has_standard
                    and error in ("invalid_auth", "not_authed", "token_revoked")):
                click.echo(f"Browser token error ({error}), retrying with standard token...", err=True)
                resp = self._request_with_backoff("standard", method, **params)
                resp.raise_for_status()
                result = resp.json()
                if not result.get("ok"):
                    raise click.ClickException(f"Slack API error: {result.get('error', 'unknown_error')}")
                return result
            raise click.ClickException(f"Slack API error: {error}")
        return result

    def _download_response(self, url: str):
        """GET a Slack private URL with whichever auth is available."""
        headers = {}
        if self.has_browser:
            headers["Authorization"] = f"Bearer {self.xoxc_token}"
            headers["Cookie"] = self.cookie_header()
        else:
            headers["Authorization"] = f"Bearer {self.standard_token}"
        resp = requests.get(url, headers=headers, timeout=60)
        # Fallback to standard token if browser auth failed
        if not resp.ok and self.has_browser and self.has_standard:
            click.echo(f"Browser download failed (HTTP {resp.status_code}), retrying with standard token...", err=True)
            resp = requests.get(url, headers={"Authorization": f"Bearer {self.standard_token}"}, timeout=60)
        resp.raise_for_status()
        return resp

    def download(self, url: str) -> str:
        """Download text content (canvas HTML) from a Slack private URL."""
        return self._download_response(url).text

    def download_bytes(self, url: str) -> bytes:
        """Download binary content (images, PDFs) from a Slack private URL."""
        return self._download_response(url).content

    @staticmethod
    def _display_name(user: dict) -> str:
        return (
            user.get("profile", {}).get("display_name")
            or user.get("real_name")
            or user.get("name")
            or user.get("id", "unknown")
        )

    def resolve_users(self, user_ids) -> dict[str, str]:
        """Resolve user IDs to display names, cached on disk and in memory.

        Cache lives at <script dir>/.user-cache.json so repeated history/thread
        fetches cost zero extra API calls. Unknown IDs are looked up once each.
        """
        wanted = {u for u in user_ids if u}
        if not wanted:
            return {}

        if self._user_cache is None:
            self._user_cache = {}
            try:
                with open(self._user_cache_path, encoding="utf-8") as f:
                    self._user_cache = json.load(f)
            except (OSError, ValueError):
                pass

        missing = wanted - self._user_cache.keys()
        if missing:
            for uid in sorted(missing):
                try:
                    result = self.api("users.info", user=uid)
                    self._user_cache[uid] = self._display_name(result.get("user", {}))
                except (click.ClickException, requests.RequestException):
                    self._user_cache[uid] = uid
            try:
                with open(self._user_cache_path, "w", encoding="utf-8") as f:
                    json.dump(self._user_cache, f, indent=0, sort_keys=True)
            except OSError:
                pass  # cache is an optimisation, never fatal

        return {uid: self._user_cache.get(uid, uid) for uid in wanted}

    def lookup_user(self, needle: str) -> str:
        """Resolve a user ID, @handle, or email to a user ID.

        `users.lookupByEmail` rejects xoxc tokens, so email and handle lookups
        fall back to scanning users.list (cached for the process lifetime).
        """
        needle = needle.strip().lstrip("@")
        if re.match(r"^[UW][A-Z0-9]{6,}$", needle):
            return needle

        if self._users_list is None:
            self._users_list = []
            cursor = None
            while True:
                params = {"limit": 200}
                if cursor:
                    params["cursor"] = cursor
                result = self.api("users.list", **params)
                self._users_list.extend(result.get("members", []))
                cursor = result.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

        low = needle.lower()
        for u in self._users_list:
            profile = u.get("profile", {})
            candidates = {
                (u.get("name") or "").lower(),
                (profile.get("display_name") or "").lower(),
                (profile.get("real_name") or "").lower(),
                (profile.get("email") or "").lower(),
            }
            if low in candidates and not u.get("deleted"):
                return u["id"]
        raise click.ClickException(f"No Slack user matches: {needle}")


class SlackCanvasConverter(MarkdownConverter):
    """Markdown converter that preserves Slack checklist checkbox state."""

    def convert_li(self, el, text, *args, **kwargs):
        # markdownify <0.14 passes convert_as_inline positionally; >=0.14 passes
        # parent_tags as a keyword. Forward whatever we were given.
        checked = el.get("data-checked")
        result = super().convert_li(el, text, *args, **kwargs)
        if checked is not None:
            marker = "[x]" if checked == "true" else "[ ]"
            result = re.sub(r"^(\s*[-*])\s", rf"\1 {marker} ", result, count=1)
        return result


def _li_to_markdown(li) -> str:
    """Convert a checklist <li>'s inner HTML to markdown, preserving inline formatting.

    Falls back to spaced plain text if conversion yields nothing useful.
    """
    inner = "".join(str(c) for c in li.contents)
    md = SlackCanvasConverter(heading_style="ATX", bullets="-").convert(inner)
    md = md.replace("​", "").strip()
    md = re.sub(r"\s*\n\s*", " ", md)
    return md or li.get_text(" ", strip=True)


def _parse_checklist_items(html: str) -> list[dict]:
    """Parse checklist items from canvas HTML.

    Each item carries both `text` (plain, space-joined — used for matching and
    for the sections.lookup query) and `markdown` (inline formatting preserved —
    used when writing the item back, so links/bold/mentions survive a check).
    """
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
        # NOTE: get_text(strip=True) with no separator concatenates nested inline
        # tags ("Review <b>the</b> doc" -> "Reviewthedoc"), which both breaks
        # matching and corrupts the item on write-back. Always pass a separator.
        text = li.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            items.append({"text": text, "checked": checked, "markdown": _li_to_markdown(li)})
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

            # Archive URLs: /archives/CXXXXXX/p1771540768173789[?thread_ts=...]
            if len(parts) >= 2 and parts[0] == "archives":
                channel = parts[1]
                query = parse_qs(parsed.query or "")
                if len(parts) >= 3 and re.match(r"^p\d{10,}$", parts[2]):
                    raw_ts = parts[2][1:]  # strip the 'p'
                    msg_ts = raw_ts[:10] + "." + raw_ts[10:]
                    # "Copy link" on a REPLY yields the reply's ts in the path and
                    # the parent's in ?thread_ts=. conversations.replies needs the
                    # parent, so prefer thread_ts when present or we silently fetch
                    # a one-message "thread".
                    parent_ts = (query.get("thread_ts") or [None])[0]
                    result = {"type": "thread", "channel": channel, "ts": parent_ts or msg_ts}
                    if parent_ts and parent_ts != msg_ts:
                        result["highlight_ts"] = msg_ts
                    return result
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
    """Resolve a channel name, ID, Slack URL, @user, or user ID to a channel ID.

    Accepts:
      C.../D.../G... ID   -> returned as-is
      a Slack archive URL -> the channel from the URL
      #name or name       -> matched against public + private channels
      @handle / email / U -> opens (or reuses) the DM with that person
    """
    channel = channel.strip()

    if channel.startswith("http"):
        parsed = parse_slack_url(channel)
        if parsed.get("channel"):
            return parsed["channel"]
        raise click.ClickException(f"URL does not point at a conversation: {channel}")

    if re.match(r"^[CDG][A-Z0-9]{6,}$", channel):
        return channel

    # A user reference means "the DM with this person".
    if channel.startswith("@") or re.match(r"^[UW][A-Z0-9]{6,}$", channel) or "@" in channel:
        user_id = client.lookup_user(channel)
        opened = client.api("conversations.open", users=user_id)
        return opened["channel"]["id"]

    name = channel.lstrip("#")
    cursor = None
    while True:
        params = {
            "types": "public_channel,private_channel,mpim,im",
            "limit": 200,
            "exclude_archived": True,
        }
        if cursor:
            params["cursor"] = cursor
        result = client.api("conversations.list", **params)
        for ch in result.get("channels", []):
            if name in (ch.get("name"), ch.get("name_normalized")):
                return ch["id"]
        cursor = result.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    raise click.ClickException(
        f"Channel not found: #{name}\n"
        "Pass a channel ID (C.../D...), a Slack URL, or @person for a DM."
    )


def _ts_to_datetime(ts: str) -> str:
    """Convert a Slack timestamp to a human-readable LOCAL date string.

    Rendering in UTC (the previous behaviour) silently shifted every timestamp
    by the local offset with nothing marking it, which made transcripts read
    hours off. Local time with a %Z suffix is what a human actually wants.
    """
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone()
    except (TypeError, ValueError):
        return "unknown time"
    # %Z spells out "Eastern Daylight Time" on Windows; a numeric offset is
    # compact and unambiguous everywhere.
    offset = dt.strftime("%z")
    suffix = f"UTC{offset[:3].lstrip('0') or '+0'}{':' + offset[3:] if offset[3:] != '00' else ''}"
    return f"{dt.strftime('%Y-%m-%d %H:%M')} {suffix}"


# Per-file ceiling. Deliberately generous: screen recordings routinely run
# 20-50 MB and must arrive at FULL resolution — downstream tools like the
# visual-transcript skill read on-screen UI text and cursor targets, which a
# downscaled transcode destroys. Slack's own mp4_low is ~8x smaller and unusable
# for that, so it is never substituted.
MAX_ATTACHMENT_BYTES = 500 * 1024 * 1024
MAX_ATTACHMENTS = 25
FILES_MAX_AGE_HOURS = 24
# Names this tool creates: "<file-id>-<safe name>". The sweep touches nothing else.
OURS_RE = re.compile(r"^F[A-Z0-9]{6,}-")


def _human_size(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024


def _is_image(f: dict) -> bool:
    return (f.get("mimetype") or "").startswith("image/")


def _files_dir(dest_dir: str | None = None) -> Path:
    """Where downloaded attachments land.

    A plain temp dir, deliberately: no dependency on any one agent harness's
    scratchpad, so Claude Code and Codex behave identically. Override with
    --files-dir or SLACK_FILES_DIR.
    """
    return Path(dest_dir or os.getenv("SLACK_FILES_DIR")
                or Path(tempfile.gettempdir()) / "slack-files")


def _sweep_files_dir(dest: Path) -> None:
    """Delete downloaded attachments older than FILES_MAX_AGE_HOURS.

    Same self-cleaning contract as the humanize CLI's temp files: this cache is
    a convenience, never a store. Windows does not reliably clear %TEMP% on its
    own (Storage Sense only fires on low disk), so nothing else will do it.

    Only ever deletes files matching our own `<file-id>-<name>` naming, so
    pointing --files-dir at a real directory cannot destroy anything else in it.
    """
    if not dest.is_dir():
        return
    cutoff = time.time() - FILES_MAX_AGE_HOURS * 3600
    for old in dest.iterdir():
        try:
            if not OURS_RE.match(old.name) or not old.is_file():
                continue
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


def _describe_file(f: dict, local_path: str | None = None) -> list[str]:
    """Describe one attachment as output lines, written for an agent to act on.

    Always shows the file ID (what `download` takes) and, once fetched, the
    local path — the agent can then read that path directly and see the same
    screenshot a human sees inline in Slack.
    """
    name = f.get("name") or f.get("title") or "file"
    fid = f.get("id") or "?"
    meta = [f.get("filetype") or f.get("mimetype") or "?"]
    if f.get("original_w") and f.get("original_h"):
        meta.append(f"{f['original_w']}x{f['original_h']}")
    if f.get("duration_ms"):
        secs = round(f["duration_ms"] / 1000)
        meta.append(f"{secs // 60}:{secs % 60:02d}")
    if f.get("size"):
        meta.append(_human_size(f["size"]))
    head = f"[{'image' if _is_image(f) else 'file'}] {name} ({', '.join(meta)}) id={fid}"
    if local_path:
        return [head, f"        -> {local_path}"]
    return [head, f"        -> not downloaded; run: slack_canvas.py download {fid}"]


def _fetch_message_files(client, messages: list[dict], dest_dir: str | None = None,
                         images_only: bool = True) -> dict[str, str]:
    """Download attachments; return {file_id: absolute local path}.

    Cached by file ID, so re-reading the same thread costs nothing.
    """
    dest = _files_dir(dest_dir)
    _sweep_files_dir(dest)

    seen: dict[str, dict] = {}
    for msg in messages:
        for f in msg.get("files") or []:
            if f.get("id") and f["id"] not in seen:
                seen[f["id"]] = f

    wanted = [f for f in seen.values() if not images_only or _is_image(f)]
    paths: dict[str, str] = {}
    for f in wanted[:MAX_ATTACHMENTS]:
        fid, size = f["id"], f.get("size") or 0
        if size > MAX_ATTACHMENT_BYTES:
            click.echo(f"  ! {fid} ({f.get('name', '')}): {_human_size(size)} is over "
                       f"{_human_size(MAX_ATTACHMENT_BYTES)}, skipped", err=True)
            continue
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", f.get("name") or "file")
        target = dest / f"{fid}-{safe}"
        if target.exists() and (not size or target.stat().st_size == size):
            paths[fid] = str(target)
            continue
        url = f.get("url_private_download") or f.get("url_private")
        if not url:
            try:
                url = (client.api("files.info", file=fid).get("file") or {}).get("url_private_download")
            except Exception:
                url = None
        if not url:
            click.echo(f"  ! {fid} ({safe}): no download URL", err=True)
            continue
        try:
            data = client.download_bytes(url)
        except Exception as exc:
            click.echo(f"  ! {fid} ({safe}): {exc}", err=True)
            continue
        # Slack answers 200 with an HTML login page when auth is wrong. Writing
        # that out as a .png would be a silent corruption.
        if data[:15].lstrip().lower().startswith((b"<!doctype", b"<html")):
            click.echo(f"  ! {fid} ({safe}): got an HTML page, not the file — token/cookie "
                       f"likely stale (run: slack_canvas.py doctor)", err=True)
            continue
        dest.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        paths[fid] = str(target)

    if len(wanted) > MAX_ATTACHMENTS:
        click.echo(f"  ! {len(wanted) - MAX_ATTACHMENTS} more attachment(s) not fetched "
                   f"(cap {MAX_ATTACHMENTS}); use `download` for specific IDs", err=True)
    return paths


def _message_body(msg: dict, file_paths: dict[str, str] | None = None) -> str:
    """Best-effort readable body for a message, including non-plain-text content.

    Plain `text` is empty for many real messages (attachment-only posts, some
    bot/app messages, file shares), which previously rendered as blank lines.
    """
    parts = []
    text = (msg.get("text") or "").strip()
    if text:
        parts.append(text)

    for att in msg.get("attachments") or []:
        chunk = " ".join(
            s for s in [att.get("title"), att.get("text") or att.get("fallback")] if s
        ).strip()
        if chunk:
            parts.append(f"[attachment] {chunk}")

    for f in msg.get("files") or []:
        parts.extend(_describe_file(f, (file_paths or {}).get(f.get("id"))))

    if not parts and msg.get("subtype"):
        parts.append(f"[{msg['subtype']}]")

    return "\n".join(parts) or "[no text content]"


def _format_reactions(msg: dict) -> str:
    """Render reactions as a compact trailing line, or '' if there are none."""
    reactions = msg.get("reactions") or []
    if not reactions:
        return ""
    return "   reactions: " + ", ".join(
        f":{r.get('name')}: x{r.get('count', 0)}" for r in reactions
    )


def _format_messages(messages: list[dict], user_map: dict[str, str], indent: bool = False,
                     file_paths: dict[str, str] | None = None) -> str:
    """Format Slack messages into readable text.

    Args:
        messages: List of Slack message dicts.
        user_map: Mapping of user IDs to display names.
        indent: If True, prefix every line with "  > " (for thread replies).
        file_paths: {file_id: local path} for attachments already downloaded.
    """
    lines = []
    for msg in messages:
        author = msg.get("user") or msg.get("bot_id") or ""
        user = user_map.get(author) or msg.get("username") or author or "unknown"
        ts = _ts_to_datetime(msg.get("ts", "0"))
        prefix = "  > " if indent else ""
        lines.append(f"{prefix}**{user}** ({ts}):")
        for line in _message_body(msg, file_paths).split("\n"):
            lines.append(f"{prefix}{line}")
        reactions = _format_reactions(msg)
        if reactions:
            lines.append(f"{prefix}{reactions.strip()}")
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
    """Slack CLI — read, send, watch; plus canvas read/edit.

    Run `doctor` first if anything is failing: it reports which tokens are
    present, who they authenticate as, and which commands they unlock.
    """
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
def doctor():
    """Diagnose credentials: which tokens exist, who they are, what they unlock."""
    _load_env()
    xoxc = os.getenv("SLACK_XOXC_TOKEN")
    xoxd = os.getenv("SLACK_XOXD_COOKIE")
    std = os.getenv("SLACK_TOKEN")
    workspace = os.getenv("SLACK_WORKSPACE_URL")

    click.echo("Slack credential check")
    click.echo(f"  .env searched: {os.path.join(SCRIPT_DIR, '.env')}, ~/.env, CWD")
    click.echo(f"  SLACK_WORKSPACE_URL : {workspace or 'NOT SET  <- required for browser tokens'}")
    if workspace and "YOURWORKSPACE" in workspace.upper():
        # *.slack.com is wildcard DNS and Slack routes by token, so the placeholder
        # left behind by extract-token still returns 200 — broken but invisible.
        click.echo("      ^ this is the placeholder value. It appears to work (wildcard DNS)"
                   " but should be your real workspace domain.")

    ok_browser = ok_standard = False

    click.echo("\n[browser session tokens]  -> read everything you can see; send AS YOU; watch")
    if not (xoxc and xoxd):
        click.echo("  MISSING (need both SLACK_XOXC_TOKEN and SLACK_XOXD_COOKIE)")
        click.echo("  Fix: uv run slack_canvas.py extract-token --write-env")
    else:
        try:
            r = requests.post(
                f"{(workspace or 'https://slack.com').rstrip('/')}/api/auth.test",
                headers={"Cookie": f"d={SlackClient._encode_cookie(xoxd)}"},
                data={"token": xoxc}, timeout=20,
            ).json()
            if r.get("ok"):
                ok_browser = True
                click.echo(f"  OK  {r.get('user')} @ {r.get('team')}  ({r.get('user_id')})")
            else:
                click.echo(f"  FAILED: {r.get('error')}")
                if r.get("error") in ("invalid_auth", "not_authed"):
                    click.echo("  Fix: uv run slack_canvas.py extract-token --write-env")
        except requests.RequestException as exc:
            click.echo(f"  ERROR contacting Slack: {exc}")

    click.echo("\n[OAuth token SLACK_TOKEN]  -> canvas EDITS (check/uncheck/append/insert/rename)")
    if not std:
        click.echo("  MISSING — canvas edit commands will refuse to run.")
        click.echo("  These APIs reject browser tokens (not_allowed_token_type), so an")
        click.echo("  xoxb/xoxp with canvases:write is the only way to edit a canvas.")
    else:
        try:
            resp = requests.post("https://slack.com/api/auth.test",
                                 headers={"Authorization": f"Bearer {std}"}, timeout=20)
            r = resp.json()
            scopes = [s.strip() for s in resp.headers.get("x-oauth-scopes", "").split(",") if s.strip()]
            if r.get("ok"):
                ok_standard = True
                kind = "bot" if std.startswith("xoxb-") else "user"
                click.echo(f"  OK  {r.get('user')} @ {r.get('team')}  ({kind} token)")
                canvas = [s for s in scopes if s.startswith("canvases:")]
                click.echo(f"  canvas scopes: {', '.join(canvas) if canvas else 'NONE — edits will fail'}")
                if kind == "bot":
                    click.echo("  note: a bot token only reaches canvases in channels the bot is in,")
                    click.echo("        and any message it posts appears as the bot, not as you.")
            else:
                click.echo(f"  FAILED: {r.get('error')}")
        except requests.RequestException as exc:
            click.echo(f"  ERROR contacting Slack: {exc}")

    click.echo("\nCapabilities")
    for label, enabled in [
        ("read canvases / threads / history", ok_browser or ok_standard),
        ("send as you (send, watch)", ok_browser),
        ("edit canvases (check/append/rename)", ok_standard),
    ]:
        click.echo(f"  [{'x' if enabled else ' '}] {label}")

    if not (ok_browser or ok_standard):
        sys.exit(1)


def files_options(fn):
    """--files / --all-files / --files-dir, shared by `thread` and `history`."""
    fn = click.option("--files-dir", "files_dir",
                      help="Where to save attachments (default: temp dir, swept after 24h)")(fn)
    fn = click.option("--all-files", "all_files", is_flag=True,
                      help="Also download non-image attachments (PDFs, zips, video)")(fn)
    fn = click.option("--files/--no-files", "want_files", default=True,
                      help="Download image attachments so they can be read (default on)")(fn)
    return fn


@cli.command()
@click.argument("url_or_channel", required=False)
@click.option("--channel", "-c", "channel_opt", help="Channel ID (alternative to URL)")
@click.option("--ts", "-t", "ts_opt", help="Thread timestamp (alternative to URL)")
@click.option("--output", "-o", "output_path", help="Write output to file instead of stdout")
@files_options
def thread(url_or_channel, channel_opt, ts_opt, output_path, want_files, all_files, files_dir):
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

    file_paths = (_fetch_message_files(client, messages, files_dir, images_only=not all_files)
                  if want_files else {})

    # Format: first message is the parent, rest are replies
    parts = []
    parts.append(_format_messages(messages[:1], user_map, file_paths=file_paths))
    if len(messages) > 1:
        parts.append(_format_messages(messages[1:], user_map, indent=True, file_paths=file_paths))

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
@click.option("--limit", "-n", default=1000, help="Max messages to fetch (default 1000)")
@click.option("--since", "since", help="Start date (YYYY-MM-DD or relative: 30d, 2w)")
@click.option("--until", "until_", help="End date (YYYY-MM-DD or relative: 7d)")
@click.option("--threads/--no-threads", "threads", default=True,
              help="Inline thread replies (default ON). --no-threads is a cheap "
                   "skim of many parent messages for context-gathering.")
@click.option("--output", "-o", "output_path", help="Write output to file instead of stdout")
@files_options
def history(channel, limit, since, until_, threads, output_path, want_files, all_files, files_dir):
    """Fetch recent messages from a channel, DM, or @person.

    Thread replies are included by default — a channel read without them is
    usually missing the actual conversation. Use --no-threads to skim.

    \b
    Examples:
        uv run slack_canvas.py history "#general" --since 30d
        uv run slack_canvas.py history "#general" --since 2026-02-01 --until 2026-02-15
        uv run slack_canvas.py history D08Q9F1HQGG --since 2w
        uv run slack_canvas.py history "@sam" --since 7d
        uv run slack_canvas.py history "#engineering" --since 90d --no-threads -o skim.md
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
    more_available = False
    while len(messages) < limit:
        api_params["limit"] = min(200, limit - len(messages))
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
        more_available = True
    if len(messages) > limit:
        messages = messages[:limit]

    if not messages:
        click.echo("No messages found.")
        return

    # Truncation used to be silent, which quietly turned "last 30 days" into
    # "the newest 200 messages" with no way to notice.
    if len(messages) >= limit and more_available:
        click.echo(
            f"Warning: hit the --limit of {limit} messages; older messages in this "
            f"range were NOT fetched. Re-run with a larger -n or a narrower --since.",
            err=True,
        )

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

    file_paths = {}
    if want_files:
        every = list(messages) + [m for r in thread_replies.values() for m in r]
        file_paths = _fetch_message_files(client, every, files_dir, images_only=not all_files)

    # Format output
    parts = []
    for msg in messages:
        parts.append(_format_messages([msg], user_map, file_paths=file_paths).rstrip())
        if msg["ts"] in thread_replies and thread_replies[msg["ts"]]:
            parts.append(_format_messages(thread_replies[msg["ts"]], user_map, indent=True,
                                          file_paths=file_paths).rstrip())
        parts.append("")

    _output_text("\n".join(parts).strip(), output_path)


@cli.command()
@click.argument("query", required=False)
@click.option("--channel", "-c", help="Channel name or ID to filter by")
@click.option("--limit", "-n", default=20, help="Max results (default 20)")
def search(query, channel, limit):
    """Search for canvases by title. Lists all canvases if no query given."""
    client = SlackClient()
    base_params = {"types": "canvas", "count": 100}
    if channel:
        base_params["channel"] = resolve_channel_id(client, channel)

    # With a query we must page the whole file list — filtering happens client
    # side, so stopping at the first page would hide any canvas that is not
    # among the most recently updated ones.
    max_pages = 20 if query else 1
    files, page = [], 1
    while page <= max_pages:
        result = client.api("files.list", page=page, **base_params)
        batch = result.get("files", [])
        files.extend(batch)
        paging = result.get("paging", {})
        if not batch or page >= paging.get("pages", 1):
            break
        page += 1

    if query:
        q = query.lower()
        files = [f for f in files if q in (f.get("title") or f.get("name") or "").lower()]

    if not files:
        click.echo("No canvases found.")
        return

    for f in files[:limit]:
        title = _html.unescape(f.get("title") or f.get("name") or "(untitled)")
        fid = f.get("id", "?")
        channels = ", ".join(f.get("channels", []))
        ch_info = f" [#{channels}]" if channels else ""
        click.echo(f"  {fid}  {title}{ch_info}")

    if len(files) > limit:
        click.echo(f"  ... {len(files) - limit} more (raise -n to see them)", err=True)


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
                "markdown": f"- [x] {match.get('markdown') or match['text']}\n",
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
                "markdown": f"- [ ] {match.get('markdown') or match['text']}\n",
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


@cli.command()
@click.argument("target", required=False)
@click.option("--thread", "thread_ref", help="Only events in this thread (parent ts or URL)")
@click.option("--timeout", "-T", default=900, help="Give up after N seconds (default 900)")
@click.option("--exit-on-first", is_flag=True,
              help="Return as soon as one matching event arrives (poll-loop replacement)")
@click.option("--reactions/--no-reactions", default=True,
              help="Report reaction_added events (default on) — a thumbsup often IS the reply")
@click.option("--include-self", is_flag=True, help="Also report your own messages")
def watch(target, thread_ref, timeout, exit_on_first, reactions, include_self):
    """Watch for new messages and reactions in real time (no polling).

    Opens Slack's RTM websocket using your browser session, so it sees
    everything you can see — including DMs — and pushes events as they happen.
    Prints one JSON object per line, then exits on timeout or --exit-on-first.

    \b
    Examples:
        uv run slack_canvas.py watch D08Q9F1HQGG --exit-on-first
        uv run slack_canvas.py watch --thread URL -T 1800
        uv run slack_canvas.py watch "#general" --no-reactions
    """
    try:
        from websocket import WebSocketTimeoutException, create_connection
    except ImportError:
        raise click.ClickException("websocket-client is required: run via `uv run slack_canvas.py`")

    client = SlackClient()
    if not client.has_browser:
        raise click.ClickException(
            "watch needs browser tokens (SLACK_XOXC_TOKEN + SLACK_XOXD_COOKIE).\n"
            "Bot tokens cannot see your DMs and are not supported here."
        )

    channel_id = resolve_channel_id(client, target) if target else None
    thread_ts = _resolve_thread_ts(thread_ref)
    me = client.api("auth.test").get("user_id")

    conn = client.api("rtm.connect")
    # The gateway rejects the token on the querystring; the session cookie must
    # be presented on the websocket handshake itself.
    ws = create_connection(
        conn["url"], timeout=30,
        header=[f"Cookie: {client.cookie_header()}", f"Origin: {client.workspace_url}"],
    )
    hello = json.loads(ws.recv())
    if hello.get("type") != "hello":
        ws.close()
        raise click.ClickException(f"RTM handshake failed: {json.dumps(hello)[:200]}")

    scope = channel_id or "all conversations"
    click.echo(f"Watching {scope}"
               f"{f' thread {thread_ts}' if thread_ts else ''} "
               f"for up to {timeout}s. Ctrl-C to stop.", err=True)

    deadline = time.time() + timeout
    matched = 0
    try:
        while time.time() < deadline:
            try:
                ws.settimeout(max(1.0, min(30.0, deadline - time.time())))
                event = json.loads(ws.recv())
            except json.JSONDecodeError:
                continue
            except WebSocketTimeoutException:
                # Just "no events in the last 30s" — the socket is still fine.
                # Reconnecting here re-handshakes rtm.connect twice a minute on
                # any quiet channel, which trips Slack's rate limit; the 60s
                # backoffs that follow leave the watch deaf, and it silently
                # misses the very message it was waiting for.
                continue
            except Exception as exc:
                if time.time() >= deadline:
                    break
                click.echo(f"(socket closed: {type(exc).__name__}; reconnecting)", err=True)
                conn = client.api("rtm.connect")
                ws = create_connection(
                    conn["url"], timeout=30,
                    header=[f"Cookie: {client.cookie_header()}",
                            f"Origin: {client.workspace_url}"],
                )
                ws.recv()
                continue

            etype = event.get("type")
            if etype == "message":
                ev_channel = event.get("channel")
                ev_user = event.get("user")
                # `message_replied` is an update to the PARENT ("this message
                # now has replies"), not the reply: no top-level user, no text,
                # and `ts` is the parent's. Treating it as a message made
                # --exit-on-first return a bare "[message_replied]" and stop
                # watching before the actual reply arrived.
                if event.get("subtype") in (
                    "message_deleted", "message_changed", "message_replied",
                ):
                    continue
                if channel_id and ev_channel != channel_id:
                    continue
                if thread_ts and event.get("thread_ts") != thread_ts:
                    continue
                if ev_user == me and not include_self:
                    continue
                body = _message_body(event)
                # Guard against future subtypes that behave like message_replied:
                # an event with no author whose body is only a "[subtype]"
                # placeholder carries nothing worth waking anyone for.
                if ev_user is None and re.fullmatch(r"\[[a-z_]+\]", body):
                    continue
                name = client.resolve_users([ev_user]).get(ev_user, ev_user)
                payload = {
                    "event": "message", "channel": ev_channel, "user": ev_user,
                    "name": name, "ts": event.get("ts"),
                    "thread_ts": event.get("thread_ts"), "text": body,
                }
            elif etype in ("reaction_added", "reaction_removed") and reactions:
                item = event.get("item") or {}
                ev_channel = item.get("channel")
                if channel_id and ev_channel != channel_id:
                    continue
                # A reaction on the thread parent counts as activity on the thread.
                if thread_ts and item.get("ts") != thread_ts:
                    continue
                if event.get("user") == me and not include_self:
                    continue
                name = client.resolve_users([event.get("user")]).get(event.get("user"))
                payload = {
                    "event": etype, "channel": ev_channel, "user": event.get("user"),
                    "name": name, "reaction": event.get("reaction"),
                    "item_ts": item.get("ts"),
                }
            else:
                continue

            matched += 1
            click.echo(json.dumps(payload, ensure_ascii=False))
            sys.stdout.flush()
            if exit_on_first:
                break
    except KeyboardInterrupt:
        click.echo("Stopped.", err=True)
    finally:
        try:
            ws.close()
        except Exception:
            pass

    if matched == 0:
        click.echo(f"No matching activity within {timeout}s.", err=True)
        sys.exit(2)


def _resolve_thread_ts(value: str | None) -> str | None:
    """Accept a raw ts or a Slack message URL for --thread."""
    if not value:
        return None
    value = value.strip()
    if re.match(r"^\d{10}\.\d{1,8}$", value):
        return value
    parsed = parse_slack_url(value)
    if parsed["type"] != "thread":
        raise click.ClickException(f"--thread needs a message URL or ts, got: {value}")
    return parsed["ts"]


def _upload_files(client: SlackClient, channel_id: str, paths: tuple[str, ...],
                  comment: str | None, thread_ts: str | None) -> dict:
    """Upload files via the current external-upload flow and share them.

    files.upload was retired in 2025; getUploadURLExternal + completeUploadExternal
    is the supported path.
    """
    uploaded = []
    for raw_path in paths:
        path = os.path.expanduser(raw_path)
        if not os.path.isfile(path):
            raise click.ClickException(f"Attachment not found: {path}")
        size = os.path.getsize(path)
        name = os.path.basename(path)

        ticket = client.api("files.getUploadURLExternal", filename=name, length=size)
        upload_url, file_id = ticket["upload_url"], ticket["file_id"]

        with open(path, "rb") as fh:
            put = requests.post(upload_url, files={"file": (name, fh)}, timeout=180)
        put.raise_for_status()
        uploaded.append({"id": file_id, "title": name})
        click.echo(f"  uploaded {name} ({size:,} bytes)", err=True)

    params = {"files": uploaded, "channel_id": channel_id}
    if comment:
        params["initial_comment"] = comment
    if thread_ts:
        params["thread_ts"] = thread_ts
    return client.api("files.completeUploadExternal", **params)


_MENTION_RE = re.compile(r"(?:<|&lt;)@([UW][A-Z0-9]+)\|[^>&]*(?:>|&gt;)")


def _normalize_mentions(text: str) -> str:
    """Rewrite `<@UID|label>` mentions to the bare `<@UID>` form Slack renders.

    Slack's labeled form is deprecated and silently fails when the label has a
    space (`<@U123|clayton bell>` posts as literal text, HTML-escaped to
    `&lt;@U123|clayton bell&gt;`), while `<@U123|oz>` happens to work. Only the
    bare form is reliable, so both shapes are normalized before send/edit.
    (Bit on 2026-09-09: two #wins-public posts landed with raw mention markup.)
    """
    return _MENTION_RE.sub(r"<@>", text) if text else text


@cli.command()
@click.argument("target")
@click.option("--text", "-m", help="Message text (markdown-ish Slack mrkdwn)")
@click.option("--file", "-f", "text_file", type=click.Path(exists=True, dir_okay=False),
              help="Read message text from a file (avoids shell-quoting problems)")
@click.option("--thread", "thread_ref", help="Reply in a thread: parent ts or message URL")
@click.option("--attach", "-a", multiple=True, help="Attach a file (repeatable)")
@click.option("--unfurl/--no-unfurl", default=True, help="Expand link previews (default on)")
@click.option("--dry-run", is_flag=True, help="Print what would be sent, send nothing")
@click.option("--json", "as_json", is_flag=True,
              help="Emit the result as JSON (for scripts: channel, ts, permalink)")
def send(target, text, text_file, thread_ref, attach, unfurl, dry_run, as_json):
    """Send a message as YOU (no 'Sent using Claude' footer).

    TARGET may be a channel (#name or C...), a DM (D...), an @person or email,
    or any Slack URL. With browser tokens the message posts under your own
    account, exactly as if you had typed it.

    \b
    Examples:
        uv run slack_canvas.py send "#general" -m "shipping in 10"
        uv run slack_canvas.py send "@sam" -f draft.md
        uv run slack_canvas.py send URL --thread URL -m "bumping this"
        uv run slack_canvas.py send "#design" -m "see attached" -a shot.png
    """
    client = SlackClient()

    if text_file:
        with open(text_file, encoding="utf-8") as f:
            file_text = f.read().strip()
        text = f"{text}\n\n{file_text}" if text else file_text
    if not text and not attach:
        raise click.ClickException("Nothing to send: pass --text/-m, --file/-f, or --attach/-a")
    text = _normalize_mentions(text)

    channel_id = resolve_channel_id(client, target)
    thread_ts = _resolve_thread_ts(thread_ref)

    if dry_run:
        if as_json:
            click.echo(json.dumps({
                "dry_run": True, "channel": channel_id, "thread_ts": thread_ts,
                "attachments": list(attach), "text": text,
            }))
            return
        click.echo(f"[dry-run] channel={channel_id}"
                   f"{' thread_ts=' + thread_ts if thread_ts else ''}"
                   f"{' attachments=' + str(list(attach)) if attach else ''}")
        click.echo("--- message ---")
        click.echo(text or "(no text, attachments only)")
        return

    if attach:
        result = _upload_files(client, channel_id, attach, text, thread_ts)
        if as_json:
            click.echo(json.dumps({
                "ok": True, "channel": channel_id, "attachments": len(attach),
                "files": [f.get("id") for f in (result.get("files") or [])],
            }))
        else:
            click.echo(f"Sent with {len(attach)} attachment(s) to {channel_id}")
        return

    params = {"channel": channel_id, "text": text, "unfurl_links": unfurl, "unfurl_media": unfurl}
    if thread_ts:
        params["thread_ts"] = thread_ts
    result = client.api("chat.postMessage", **params)
    ts = result.get("ts")

    if as_json:
        payload = {"ok": True, "channel": channel_id, "ts": ts, "thread_ts": thread_ts}
        try:
            link = client.api("chat.getPermalink", channel=channel_id, message_ts=ts)
            payload["permalink"] = link.get("permalink")
        except click.ClickException:
            pass
        click.echo(json.dumps(payload))
    else:
        click.echo(f"Sent to {channel_id} (ts {ts})")


@cli.command()
@click.argument("message")
@click.option("--text", "-m", help="New full message text (Slack mrkdwn)")
@click.option("--file", "-f", "text_file", type=click.Path(exists=True, dir_okay=False),
              help="New full message text from a file")
@click.option("--sub", "subs", nargs=2, multiple=True, metavar="OLD NEW",
              help="Replace OLD with NEW inside the current text (repeatable); "
                   "combines with the current message, not with -m/-f")
@click.option("--channel", "-c", "channel_opt",
              help="Channel ID when MESSAGE is a bare ts instead of a URL")
@click.option("--dry-run", is_flag=True, help="Show current and new text, change nothing")
@click.option("--json", "as_json", is_flag=True, help="Emit the result as JSON")
def edit(message, text, text_file, subs, channel_opt, dry_run, as_json):
    """Edit one of YOUR existing messages in place (chat.update).

    MESSAGE is a message URL (…/archives/C…/p1788963393531279) or a bare ts
    with --channel. Reactions, thread replies and the original timestamp are
    kept - use this instead of delete-and-repost once anyone has reacted.
    Only messages posted by the token's own user can be edited.

    
    Examples:
        uv run slack_canvas.py edit <url> -f fixed.md
        uv run slack_canvas.py edit <url> --sub "its" "it's" --sub "Fantastic." "Fantastic!"
        uv run slack_canvas.py edit 1788963393.531279 -c C08RGJP59CM -m "new text" --dry-run
    """
    client = SlackClient()

    message = message.strip()
    if message.startswith("http"):
        parsed = parse_slack_url(message)
        if parsed.get("type") != "thread" or not parsed.get("channel"):
            raise click.ClickException(f"Not a message URL: {message}")
        channel_id, ts = parsed["channel"], parsed["ts"]
    elif re.match(r"^\d{10}\.\d{1,8}$", message):
        if not channel_opt:
            raise click.ClickException("A bare ts needs --channel/-c")
        channel_id, ts = resolve_channel_id(client, channel_opt), message
    else:
        raise click.ClickException(f"MESSAGE must be a message URL or a ts, got: {message}")

    hist = client.api("conversations.history", channel=channel_id, latest=ts, oldest=ts,
                      inclusive=True, limit=1)
    msgs = hist.get("messages") or []
    if not msgs or msgs[0].get("ts") != ts:
        raise click.ClickException(f"Message {ts} not found in {channel_id}")
    current = msgs[0].get("text", "")

    if text_file:
        with open(text_file, encoding="utf-8") as f:
            text = f.read().strip()
    if text and subs:
        raise click.ClickException("Use either -m/-f (replace) or --sub (patch), not both")
    if subs:
        new_text = current
        for old, new in subs:
            if old not in new_text:
                raise click.ClickException(f"--sub: {old!r} not found in the current text")
            new_text = new_text.replace(old, new)
    elif text:
        new_text = text
    else:
        raise click.ClickException("Nothing to change: pass -m, -f, or --sub OLD NEW")
    new_text = _normalize_mentions(new_text)

    if dry_run:
        if as_json:
            click.echo(json.dumps({"dry_run": True, "channel": channel_id, "ts": ts,
                                   "current": current, "new": new_text}))
            return
        click.echo(f"[dry-run] channel={channel_id} ts={ts}")
        click.echo("--- current ---")
        click.echo(current)
        click.echo("--- new ---")
        click.echo(new_text)
        return
    if new_text == current:
        click.echo("No change - message already matches.")
        return

    result = client.api("chat.update", channel=channel_id, ts=ts, text=new_text)
    if as_json:
        click.echo(json.dumps({"ok": True, "channel": channel_id, "ts": result.get("ts", ts)}))
    else:
        click.echo(f"Edited {channel_id} ts {ts}")


@cli.command("download")
@click.argument("targets", nargs=-1, required=True)
@click.option("--files-dir", "files_dir",
              help="Where to save (default: temp dir, swept after 24h)")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON mapping file IDs to paths")
def download_files(targets, files_dir, as_json):
    """Download Slack attachments so they can be read locally.

    Accepts bare file IDs, file permalinks, or a message/thread archive URL
    (which downloads every attachment in that thread). Unlike `thread`, this
    fetches non-image files too.

    \b
    Examples:
        uv run slack_canvas.py download F0BTHBNFH4Z
        uv run slack_canvas.py download "https://work.slack.com/archives/D08.../p178..."
    """
    client = SlackClient()
    messages: list[dict] = []

    for target in targets:
        t = target.strip()
        m = re.search(r"/files/[^/]+/(F[A-Z0-9]+)", t) or re.fullmatch(r"(F[A-Z0-9]{6,})", t)
        if m:
            info = client.api("files.info", file=m.group(1)).get("file") or {}
            info.setdefault("id", m.group(1))
            messages.append({"files": [info]})
            continue
        parsed = parse_slack_url(t)
        if parsed["type"] != "thread":
            raise click.ClickException(
                f"Cannot interpret target: {target}  (expected a file ID, file permalink, "
                f"or a message/thread archive URL)")
        resp = client.api("conversations.replies", channel=parsed["channel"],
                          ts=parsed["ts"], limit=1000)
        messages.extend(resp.get("messages", []))

    paths = _fetch_message_files(client, messages, files_dir, images_only=False)

    if as_json:
        click.echo(json.dumps(paths, indent=2))
    elif paths:
        for fid, path in paths.items():
            click.echo(f"{fid}  {path}")
    else:
        click.echo("No attachments found.", err=True)


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
        env_path = os.path.join(SCRIPT_DIR, ".env")
        _merge_env(env_path, {
            "SLACK_XOXC_TOKEN": xoxc_token,
            "SLACK_XOXD_COOKIE": xoxd_cookie,
        })
        click.echo(f"\nUpdated {env_path} (other keys preserved)")
        if not os.getenv("SLACK_WORKSPACE_URL"):
            click.echo("  Note: SLACK_WORKSPACE_URL is not set — add your workspace domain.")


def _merge_env(env_path: str, updates: dict[str, str]):
    """Rewrite only the given keys in a .env, preserving every other line.

    The previous implementation opened the file in "w" and wrote three lines,
    which wiped SLACK_WORKSPACE_URL and SLACK_TOKEN every time a rotated token
    was re-extracted — i.e. exactly when you could least afford it.
    """
    lines = []
    if os.path.isfile(env_path):
        with open(env_path, encoding="utf-8") as f:
            lines = f.read().splitlines()

    remaining = dict(updates)
    out = []
    for line in lines:
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        key = m.group(1) if m else None
        if key in remaining:
            out.append(f'{key}="{remaining.pop(key)}"')
        else:
            out.append(line)
    for key, value in remaining.items():
        out.append(f'{key}="{value}"')

    with open(env_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip("\n") + "\n")


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

    # Try the main Cookies DB.
    # NOTE: a locked DB raises sqlite3.OperationalError, which is NOT an OSError
    # subclass — catching (PermissionError, OSError) here meant the StaleCookies
    # fallback below never ran in the one situation it exists for.
    tmp = os.path.join(tempfile.gettempdir(), "slack_cookies_tmp")
    try:
        shutil.copy2(cookies_db, tmp)
        conn = sqlite3.connect(tmp)
        try:
            row = conn.execute(
                "SELECT encrypted_value FROM cookies WHERE name='d' AND host_key LIKE '%slack.com' LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if row:
            enc_value = row[0]
    except Exception as exc:
        click.echo(f"  Cookies DB unavailable ({type(exc).__name__}), trying StaleCookies...", err=True)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

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
