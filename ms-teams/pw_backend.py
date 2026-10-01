"""Playwright fallback backend for ms_teams.py (local machine only).

Reads Teams by PASSIVELY capturing the JSON the Teams web app itself fetches
while its UI navigates (page.on("response")), and sends only by typing into
the real compose box. It never reads request headers, never extracts or
exports a token, and never calls a Teams/Graph API itself. Responses from
auth/token endpoints are ignored outright.

Captured chat-service payloads are converted to Graph-shaped objects
(`to_graph_message`, `to_graph_chat`), so the CLI's renderer and JSON output
are identical for both backends - and the ids are the native ones (chat
service conversation id == Graph chat/channel id; message id == Graph
chatMessage id).

The payload shapes below are the Teams chat-service ("msnp24") format the web
client uses. They were written without a live session; `ms_teams.py pw
capture` records real (scrubbed) payloads so the parsers can be verified.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

TEAMS_WEB = "https://teams.cloud.microsoft/"

# Chat service: list of conversations, and messages of one conversation.
# Host varies (teams.cloud.microsoft/api/chatsvc/<region>/..., <region>.ng.msg.teams.microsoft.com).
CONV_LIST_RE = re.compile(r"/v1/users/ME/conversations/?(?:\?|$)")
CONV_MSGS_RE = re.compile(r"/v1/users/ME/conversations/([^/?]+)/messages/?(?:\?|$)")
CONV_ONE_RE = re.compile(r"/v1/users/ME/conversations/([^/?]+)/?(?:\?|$)")
TEAMS_LIST_RE = re.compile(r"/api/csa/[^?]*/teams/users/me", re.I)
AMS_IMAGE_RE = re.compile(r"/v1/objects/[^/]+/views/img", re.I)
# Never capture anything that smells of authentication.
BLOCKED_RE = re.compile(r"authsvc|/authz|oauth2|/token|login\.microsoftonline|login\.live|"
                        r"skypetoken|/auth/", re.I)
# Only these hosts' responses are ever read (a third-party frame could otherwise
# serve a look-alike path).
ALLOWED_HOSTS = ("teams.microsoft.com", "teams.cloud.microsoft", "teams.live.com",
                 "asm.skype.com")
SECRET_KEY_RE =re.compile(r"token|secret|password|authorization|cookie|skype", re.I)

SYSTEM_PREFIXES = ("48:",)  # 48:notifications, 48:calllogs, 48:mentions, 48:drafts ...
TEXT_TYPES = {"text", "richtext", "richtext/html", "richtext/media_card", "richtext/uriobject",
              "richtext/media_genericfile", "richtext/media_video", "richtext/media_callrecording"}


class SignedOut(Exception):
    pass


# --------------------------------------------------------------------------
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------

def allowed_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return urlparse(url).scheme == "https" and any(
        host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def mri_oid(value: str | None) -> str | None:
    """'https://.../contacts/8:orgid:<oid>' or '8:orgid:<oid>' -> '<oid>'."""
    if not value:
        return None
    tail = value.rstrip("/").rsplit("/", 1)[-1]
    m = re.match(r"^\d+:(?:orgid:)?(.+)$", tail)
    return m.group(1) if m else tail


def ms_iso(ms) -> str | None:
    try:
        v = int(str(ms))
    except (TypeError, ValueError):
        return None
    dt = datetime.fromtimestamp(v / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def maybe_json(v):
    if isinstance(v, str) and v[:1] in ("[", "{"):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def split_conv(conv_id: str) -> tuple[str, str | None]:
    """Channel replies live in '<channelId>;messageid=<rootId>'."""
    base, _, rest = conv_id.partition(";messageid=")
    return base, (rest or None)


def is_channel_conv(conv_id: str) -> bool:
    base = split_conv(conv_id)[0].lower()
    return base.endswith("@thread.tacv2") or base.endswith("@thread.skype")


def chat_type_of(conv_id: str, props: dict | None = None) -> str:
    ptt = ((props or {}).get("productThreadType") or "").lower()
    if "meeting" in ptt or "meeting_" in conv_id:
        return "meeting"
    if conv_id.endswith("@unq.gbl.spaces") or "onetoone" in ptt:
        return "oneOnOne"
    return "group"


def _latest(*isos):
    vals = [v for v in isos if v]
    return max(vals) if vals else None


def to_graph_message(raw: dict, conv_id: str | None = None) -> dict:
    """Chat-service message -> Graph chatMessage shape (fields ms_teams.py uses)."""
    conv = raw.get("conversationid") or raw.get("conversationLink", "").rsplit("/", 1)[-1] or conv_id or ""
    base, root = split_conv(conv)
    if not root and conv_id:
        root = split_conv(conv_id)[1]
    props = raw.get("properties") or {}
    mid = str(raw.get("id") or raw.get("clientmessageid") or "")
    created = raw.get("originalarrivaltime") or raw.get("composetime")
    edited = ms_iso(props.get("edittime")) if props.get("edittime") else None
    deleted = ms_iso(props.get("deletetime")) if props.get("deletetime") else None
    reactions = []
    for emo in maybe_json(props.get("emotions")) or []:
        for u in emo.get("users") or []:
            reactions.append({"reactionType": emo.get("key"), "createdDateTime": ms_iso(u.get("time")),
                              "user": {"user": {"id": mri_oid(u.get("mri"))}}})
    attachments = []
    for f in maybe_json(props.get("files")) or []:
        url = f.get("objectUrl") or (f.get("fileInfo") or {}).get("fileUrl") or f.get("baseUrl")
        attachments.append({"id": f.get("itemid") or f.get("id"), "contentType": "reference",
                            "name": f.get("fileName") or f.get("title"), "contentUrl": url})
    mtype = (raw.get("messagetype") or "").lower()
    content = raw.get("content") or ""
    is_msg = mtype in TEXT_TYPES or mtype.startswith("richtext")
    msg = {
        "id": mid,
        "messageType": "message" if is_msg else "systemEventMessage",
        "createdDateTime": created,
        "lastModifiedDateTime": _latest(created, edited, deleted,
                                        *[r["createdDateTime"] for r in reactions]),
        "lastEditedDateTime": edited,
        "deletedDateTime": deleted,
        "subject": props.get("subject") or None,
        "importance": (props.get("importance") or "normal").lower(),
        "from": {"user": {"id": mri_oid(raw.get("from")), "displayName": raw.get("imdisplayname")}},
        "body": {"contentType": "html" if ("html" in mtype or content.lstrip().startswith("<"))
                 else "text", "content": content},
        "attachments": attachments,
        "mentions": [],
        "reactions": reactions,
        "replyToId": root if root and root != mid else None,
        "webUrl": None,
        "eventDetail": {"@odata.type": f"#microsoft.graph.{raw.get('messagetype') or 'event'}"}
        if not is_msg else None,
    }
    if is_channel_conv(base):
        msg["channelIdentity"] = {"channelId": base}
    else:
        msg["chatId"] = base
    return msg


def to_graph_chat(raw: dict) -> dict | None:
    """Chat-service conversation -> Graph chat shape. None for system feeds."""
    cid = raw.get("id") or ""
    if not cid or cid.startswith(SYSTEM_PREFIXES) or is_channel_conv(cid):
        return None
    tp = raw.get("threadProperties") or {}
    last = raw.get("lastMessage") or {}
    members = []
    for m in maybe_json(tp.get("members")) or raw.get("members") or []:
        if isinstance(m, dict):
            members.append({"userId": mri_oid(m.get("id") or m.get("mri")),
                            "displayName": m.get("friendlyName") or m.get("displayName"),
                            "email": m.get("email") or m.get("userPrincipalName")})
    if not members and cid.endswith("@unq.gbl.spaces"):
        pair = cid.split(":", 1)[-1].split("@", 1)[0].split("_")
        members = [{"userId": oid, "displayName": None, "email": None} for oid in pair]
    preview = None
    if last.get("id"):
        preview = {"id": str(last.get("id")),
                   "createdDateTime": last.get("originalarrivaltime") or last.get("composetime"),
                   "body": {"contentType": "html", "content": last.get("content") or ""},
                   "from": {"user": {"id": mri_oid(last.get("from")),
                                     "displayName": last.get("imdisplayname")}}}
    props = raw.get("properties") or {}
    return {"id": cid, "chatType": chat_type_of(cid, tp), "topic": tp.get("topic") or None,
            "members": members, "lastMessagePreview": preview,
            "lastUpdatedDateTime": ms_iso(props.get("lastimreceivedtime"))
            if str(props.get("lastimreceivedtime", "")).isdigit() else props.get("lastimreceivedtime"),
            "createdDateTime": ms_iso(tp.get("createdat")) if str(tp.get("createdat", "")).isdigit()
            else None, "webUrl": None}


def teams_from_csa(data) -> list[dict]:
    """Walk a captured teams payload for {team with channels}. Defensive by design."""
    found = []

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("channels"), list) and (o.get("displayName") or o.get("name")):
                site = o.get("teamSiteInformation") or {}
                found.append({
                    "id": site.get("groupId") or o.get("groupId") or o.get("id"),
                    "displayName": o.get("displayName") or o.get("name"),
                    "channels": [{"id": c.get("id"), "displayName": c.get("displayName") or c.get("name"),
                                  "membershipType": c.get("membershipType") or
                                  ("private" if c.get("isPrivate") else "standard")}
                                 for c in o["channels"] if isinstance(c, dict)]})
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return found


def scrub(o):
    """Remove anything key-named like a credential (for `pw capture` dumps)."""
    if isinstance(o, dict):
        return {k: ("[scrubbed]" if SECRET_KEY_RE.search(k) else scrub(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(v) for v in o]
    return o


class Capture:
    """Everything the web app fetched, routed by URL. Pure and unit-testable."""

    def __init__(self):
        self.conversations: dict[str, dict] = {}
        self.messages: dict[str, dict[str, dict]] = {}
        self.teams: list[dict] = []
        self.images: dict[str, tuple[bytes, str]] = {}
        self.sent: list[dict] = []
        self.log: list[dict] = []  # (url path, method, status) for diagnostics
        self.events = 0

    def ingest(self, url: str, method: str, status: int, data) -> None:
        if BLOCKED_RE.search(url) or not allowed_host(url):
            return
        path = urlparse(url).path
        self.log.append({"path": path, "method": method, "status": status})
        m = CONV_MSGS_RE.search(path)
        if m:
            conv = _unq(m.group(1))
            if method == "POST":
                self.sent.append({"conversation": conv, "status": status,
                                  "data": data if isinstance(data, dict) else {}})
                self.events += 1
                return
            if isinstance(data, dict):
                bucket = self.messages.setdefault(conv, {})
                for raw in data.get("messages") or []:
                    if raw.get("id"):
                        bucket[str(raw["id"])] = raw
                self.events += 1
            return
        if CONV_LIST_RE.search(path) and isinstance(data, dict):
            for raw in data.get("conversations") or []:
                if raw.get("id"):
                    self.conversations[raw["id"]] = raw
            self.events += 1
            return
        if TEAMS_LIST_RE.search(path):
            teams = teams_from_csa(data)
            if teams:
                self.teams = teams
                self.events += 1
            return
        m = CONV_ONE_RE.search(path)
        if m and isinstance(data, dict) and data.get("id"):
            self.conversations[data["id"]] = {**self.conversations.get(data["id"], {}), **data}

    def conv_messages(self, conv_id: str) -> list[dict]:
        out = {}
        for key, bucket in self.messages.items():
            if key == conv_id or (not split_conv(conv_id)[1] and split_conv(key)[0] == conv_id):
                out.update(bucket)
        return [to_graph_message(r, conv_id) for r in out.values()]


def _unq(s: str) -> str:
    from urllib.parse import unquote
    return unquote(s)


# --------------------------------------------------------------------------
# Browser driver
# --------------------------------------------------------------------------

def deep_link(conv_id: str, team_id: str | None = None, root: str | None = None) -> str:
    base, _ = split_conv(conv_id)
    enc = quote(base, safe="")
    if root:
        q = f"?parentMessageId={root}" + (f"&groupId={team_id}" if team_id else "")
        return f"{TEAMS_WEB}l/message/{enc}/{root}{q}"
    if is_channel_conv(base):
        return f"{TEAMS_WEB}l/channel/{enc}/channel" + (f"?groupId={team_id}" if team_id else "")
    return f"{TEAMS_WEB}l/chat/{enc}/0"


class TeamsWeb:
    """A persistent-profile Edge/Chromium session on the Teams web app."""

    def __init__(self, profile: Path, *, headless: bool = True, channel: str | None = "msedge",
                 user_agent: str | None = None, timeout_s: int = 60, log=print):
        self.profile, self.headless, self.channel = Path(profile), headless, channel
        self.user_agent, self.timeout_s, self.log = user_agent, timeout_s, log
        self.cap = Capture()
        self._pw = self.ctx = self.page = None

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("playwright is not installed: run via `uv run ms_teams.py`") from exc
        self._pw = sync_playwright().start()
        self.profile.mkdir(parents=True, exist_ok=True)
        kwargs = dict(headless=self.headless, viewport={"width": 1400, "height": 900},
                      args=["--disable-blink-features=AutomationControlled"])
        if self.user_agent and self.headless:
            kwargs["user_agent"] = self.user_agent
        try:
            self.ctx = self._pw.chromium.launch_persistent_context(
                str(self.profile), channel=self.channel, **kwargs)
        except Exception as exc:
            if not self.channel:
                raise
            self.log(f"(could not launch {self.channel}: {type(exc).__name__}; trying bundled "
                     "Chromium - if that fails: uv run --with playwright playwright install chromium)")
            self.ctx = self._pw.chromium.launch_persistent_context(str(self.profile), **kwargs)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.page.on("response", self._on_response)
        return self

    def __exit__(self, *exc):
        try:
            if self.ctx:
                self.ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    # -- passive capture ---------------------------------------------------
    def _on_response(self, resp) -> None:
        url = resp.url
        if BLOCKED_RE.search(url) or not allowed_host(url):
            return
        try:
            if AMS_IMAGE_RE.search(url) and resp.ok:
                self.cap.images[url.split("?", 1)[0]] = (
                    resp.body(), resp.headers.get("content-type", "image/png"))
                return
            if "/v1/users/ME/conversations" not in url and not TEAMS_LIST_RE.search(url):
                return
            if "json" not in (resp.headers.get("content-type") or ""):
                return
            self.cap.ingest(url, resp.request.method, resp.status, resp.json())
        except Exception:
            pass  # a body can vanish on navigation; capture is best-effort

    def wait_for(self, predicate, timeout_s: float | None = None, step: float = 0.25) -> bool:
        deadline = time.time() + (timeout_s or self.timeout_s)
        while time.time() < deadline:
            if predicate():
                return True
            self.page.wait_for_timeout(int(step * 1000))
        return predicate()

    def on_login_page(self) -> bool:
        host = (urlparse(self.page.url).hostname or "").lower()
        return host.startswith("login.") or "microsoftonline" in host or host.endswith("live.com")

    def _dismiss_interstitials(self) -> None:
        for label in ("Use the web app instead", "Continue on this browser", "Use web app instead",
                      "Dismiss", "Got it"):
            try:
                loc = self.page.get_by_text(label, exact=False)
                if loc.count() and loc.first.is_visible():
                    loc.first.click(timeout=1500)
            except Exception:
                pass

    def ensure_app(self, timeout_s: float | None = None) -> None:
        """Load the app and wait until it has fetched the conversation list."""
        if not self.cap.conversations:
            self.page.goto(TEAMS_WEB, wait_until="domcontentloaded")
        ok = self.wait_for(lambda: bool(self.cap.conversations) or self.on_login_page(),
                           timeout_s or max(self.timeout_s, 90))
        if self.on_login_page():
            raise SignedOut("The Teams web session is signed out")
        if not ok:
            self._dismiss_interstitials()
            if not self.wait_for(lambda: bool(self.cap.conversations), 30):
                raise SignedOut("Teams web app never loaded the chat list (signed out, or the "
                                "page changed; try `pw login`)")

    def open_conversation(self, conv_id: str, team_id: str | None = None,
                          root: str | None = None) -> None:
        target = f"{split_conv(conv_id)[0]};messageid={root}" if root else conv_id
        before = self.cap.events
        self.page.goto(deep_link(conv_id, team_id, root), wait_until="domcontentloaded")
        if self.on_login_page():
            raise SignedOut("The Teams web session is signed out")
        got = lambda: self.cap.events > before and any(  # noqa: E731
            k == target or split_conv(k)[0] == split_conv(target)[0] for k in self.cap.messages)
        if not self.wait_for(got, self.timeout_s):
            self._dismiss_interstitials()
            if not self.wait_for(got, 20):
                if self.on_login_page():
                    raise SignedOut("The Teams web session is signed out")
                raise RuntimeError(f"Teams web didn't load messages for {target}")

    def load_older(self, conv_id: str, since_iso: str | None, max_rounds: int = 40) -> None:
        """Scroll the message pane up until messages older than since_iso are loaded."""
        stale = 0
        for _ in range(max_rounds):
            msgs = self.cap.conv_messages(conv_id)
            oldest = min((m["createdDateTime"] for m in msgs if m.get("createdDateTime")), default=None)
            if since_iso and oldest and oldest <= since_iso:
                return
            n = len(msgs)
            self.page.evaluate(SCROLL_UP_JS)
            self.page.wait_for_timeout(1200)
            stale = stale + 1 if len(self.cap.conv_messages(conv_id)) == n else 0
            if stale >= 3:
                return

    def send_text(self, conv_id: str, text: str, *, team_id: str | None = None,
                  root: str | None = None) -> dict:
        """Type into the real compose box and press send. Returns {"id": ...} if seen."""
        self.open_conversation(conv_id, team_id, root)
        box = self.page.locator('[contenteditable="true"][role="textbox"]')
        if not box.count():
            raise RuntimeError("Couldn't find the Teams compose box")
        box = box.last
        box.click()
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line:
                self.page.keyboard.insert_text(line)
            if i < len(lines) - 1:
                self.page.keyboard.press("Shift+Enter")
        before = len(self.cap.sent)
        sent_btn = self.page.locator('button[aria-label^="Send"], [data-tid="newMessageCommands-send"]')
        if sent_btn.count() and sent_btn.last.is_enabled():
            sent_btn.last.click()
        else:
            self.page.keyboard.press("Enter")
        if not self.wait_for(lambda: len(self.cap.sent) > before, 30):
            raise RuntimeError("Typed the message but never saw Teams post it - check the chat "
                               "before retrying (it may be sitting unsent in the compose box)")
        res = self.cap.sent[-1]
        if res["status"] >= 400:
            raise RuntimeError(f"Teams refused the message (HTTP {res['status']})")
        arrival = (res["data"] or {}).get("OriginalArrivalTime")
        return {"id": str(arrival) if arrival else None, "conversation": res["conversation"]}


# Find the message list's scroll container (the element with the most scrollable
# height) and jump it to the top, which makes the app fetch the previous page.
SCROLL_UP_JS = """() => {
  const known = document.querySelector('[data-tid="message-pane-list-viewport"], [data-tid="chat-pane-list"]');
  if (known) { known.scrollTop = 0; return 'known'; }
  const minX = window.innerWidth * 0.3;  /* skip the left chat list */
  const els = [...document.querySelectorAll('*')].filter(el => {
    const s = getComputedStyle(el);
    return (s.overflowY === 'auto' || s.overflowY === 'scroll')
      && el.scrollHeight > el.clientHeight + 50 && el.getBoundingClientRect().left >= minX;
  });
  els.sort((a, b) => b.scrollHeight - a.scrollHeight);
  if (els[0]) els[0].scrollTop = 0;
  return els.length;
}"""
