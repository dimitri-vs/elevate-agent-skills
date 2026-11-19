# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "click",
#     "requests",
#     "msal>=1.30",
#     "msal-extensions>=1.2",
#     "markdown",
#     "markdownify",
#     "beautifulsoup4",
#     "playwright>=1.47",
# ]
# ///
"""Microsoft Teams CLI: read, search, send, edit and watch Teams chats and
channels as the signed-in user.

Backends: Microsoft Graph with delegated permissions (our own public-client
app registration), and a local Playwright fallback (pw_backend.py) that reads
what the Teams web app itself loads. Both emit the same JSON records - see
SKILL.md "Output contract". Everything runs as the user, so it sees exactly
what the user sees in the Teams client and nothing more.
"""

from __future__ import annotations

import base64
import hashlib
import html as _html
import json
import mimetypes
import os
import re
import struct
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import click
import requests

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

GRAPH = "https://graph.microsoft.com/v1.0"

# The client is OUR OWN public-client app registration in the user's tenant
# (see SKILL.md "App registration"). Its ids are per-machine config, not code:
# `configure --client-id ... --tenant ...` writes them to config.json, or set
# MS_TEAMS_CLIENT_ID / MS_TEAMS_TENANT. It must be our own app because the
# refresh token can be exported to a server, and a refresh token only works
# with the client id that obtained it.

# Sign-in asks for `.default`: "whatever this app has been granted for me".
# That never requests a scope the admin hasn't approved (in tenants where user
# consent is off, asking for one more scope = "Need admin approval"), and new
# grants show up in the next token with no code change. MSAL adds openid,
# profile and offline_access (the refresh token) on its own.
GRAPH_DEFAULT = "https://graph.microsoft.com/.default"

CHANNEL_READ = "ChannelMessage.Read.All"   # read channel posts/replies + their images
CHANNEL_WRITE = "ChannelMessage.ReadWrite"  # edit/delete own channel messages
# These two need tenant admin consent even where user consent is allowed
# (verified against the Graph permissions reference, Oct 2026).
ADMIN_SCOPES = [CHANNEL_READ, CHANNEL_WRITE]

# Recommended grants (what to put on the app registration and consent to).
READ_PROFILE = [
    "User.Read",               # whoami
    "User.ReadBasic.All",      # people: email -> id, names on reactions, @mentions
    "Chat.Read",               # list/read chats, chat images, search, watch chats
    CHANNEL_READ,              # read channel posts/replies, watch channels  [admin]
    "Team.ReadBasic.All",      # list joined teams (and resolve a channel's team)
    "Channel.ReadBasic.All",   # list channels
    "Files.Read.All",          # download files people attached (they live in THEIR OneDrive)
]
SEND_PROFILE = [
    "ChatMessage.Send",        # send in chats; the ONLY scope replyWithQuote accepts
    "Chat.ReadWrite",          # edit/delete own chat messages, open a new 1:1 (supersedes Chat.Read)
    "ChannelMessage.Send",     # post to channels, reply in channel threads
    "Files.ReadWrite",         # upload attachments to your OneDrive and share them
]

# Capability -> any one of these scopes unlocks it (from each API's permission table).
CAPS = {
    "chat_read": ["Chat.Read", "Chat.ReadWrite"],
    "chat_send": ["ChatMessage.Send", "Chat.ReadWrite"],
    "chat_quote": ["ChatMessage.Send"],
    "chat_write": ["Chat.ReadWrite"],
    "chat_create": ["Chat.Create", "Chat.ReadWrite"],
    "channel_read": [CHANNEL_READ],
    "channel_send": ["ChannelMessage.Send"],
    "channel_write": [CHANNEL_WRITE],
    "teams_list": ["Team.ReadBasic.All"],
    "channels_list": ["Channel.ReadBasic.All"],
    "users": ["User.ReadBasic.All", "User.Read.All"],
    "files_read": ["Files.Read.All", "Files.ReadWrite.All"],
    "files_write": ["Files.ReadWrite", "Files.ReadWrite.All"],
    "search": ["Chat.Read", "Chat.ReadWrite", CHANNEL_READ],
}

TEAMS_HOSTS = ("teams.microsoft.com", "teams.cloud.microsoft", "teams.live.com")
CHAT_CONTEXT = '{"contextType":"chat"}'

REACTION_EMOJI = {
    "like": "\U0001F44D", "heart": "❤️", "laugh": "\U0001F606",
    "surprised": "\U0001F62E", "sad": "\U0001F622", "angry": "\U0001F621",
}

MAX_ATTACHMENT_BYTES = 500 * 1024 * 1024
MAX_FILES_PER_CALL = 25
FILES_MAX_AGE_HOURS = 24
INLINE_IMAGE_MAX = 4 * 1024 * 1024
SIMPLE_UPLOAD_MAX = 4 * 1024 * 1024
UPLOAD_CHUNK = 320 * 1024 * 16  # 5 MiB; Graph wants multiples of 320 KiB
OURS_RE = re.compile(r"^T-[0-9a-f]{12}")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
BODY_SOFT_LIMIT = 28_000  # Teams rejects message bodies around 28 KB

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# --------------------------------------------------------------------------
# Paths and config (all outside any repo)
# --------------------------------------------------------------------------

def home_dir() -> Path:
    """Token cache + config live here: %LOCALAPPDATA%\\ms-teams-cli on Windows."""
    env = os.getenv("MS_TEAMS_HOME")
    if env:
        return Path(env)
    base = os.getenv("LOCALAPPDATA")
    return Path(base) / "ms-teams-cli" if base else Path.home() / ".ms-teams-cli"


def config_path() -> Path:
    return home_dir() / "config.json"


def load_config() -> dict:
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    home_dir().mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------

def parse_graph_dt(value: str | None) -> datetime | None:
    """Parse Graph's ISO timestamps (Z suffix, 0-7 fractional digits)."""
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    m = re.match(r"^(.*T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(.*)$", v)
    if m:
        frac = m.group(2)
        v = m.group(1) + (("." + frac[:6].ljust(6, "0")) if frac else "") + (m.group(3) or "")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{dt.astimezone(timezone.utc).microsecond // 1000:03d}Z"


def fmt_local(value) -> str:
    """'2026-10-01 09:12 UTC-04' - local time with a compact numeric offset."""
    dt = value if isinstance(value, datetime) else parse_graph_dt(value)
    if not dt:
        return "unknown time"
    dt = dt.astimezone()
    off = dt.strftime("%z")  # e.g. -0400, +0530
    suffix = f"UTC{off[:3]}" + (f":{off[3:5]}" if off[3:5] not in ("", "00") else "") if off else "UTC"
    return f"{dt.strftime('%Y-%m-%d %H:%M')} {suffix}"


def parse_when(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    """YYYY-MM-DD (local), full ISO datetime, or relative: 90min, 12h, 30d, 2w, 3m (months)."""
    if not value:
        return None
    v = value.strip()
    m = re.fullmatch(r"(\d+)\s*(min|h|d|w|m)", v, re.I)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta = {"min": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n),
                 "w": timedelta(weeks=n), "m": timedelta(days=30 * n)}[unit]
        return datetime.now(timezone.utc) - delta
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        d = datetime.strptime(v, "%Y-%m-%d")
        if end_of_day:
            d += timedelta(days=1)
        return d.astimezone(timezone.utc)  # naive -> local midnight
    dt = parse_graph_dt(v)
    if dt:
        return dt
    raise click.BadParameter(f"Unrecognized date {value!r}: use YYYY-MM-DD, ISO, or 30d/12h/2w/3m")


# --------------------------------------------------------------------------
# Target parsing (pure; no network)
# --------------------------------------------------------------------------

def is_channel_thread(thread_id: str) -> bool:
    t = thread_id.lower()
    return t.startswith("19:") and (t.endswith("@thread.tacv2") or t.endswith("@thread.skype"))


def is_chat_id(value: str) -> bool:
    v = value.lower()
    return v.startswith("19:") and (v.endswith("@thread.v2") or v.endswith("@unq.gbl.spaces")
                                    or "meeting_" in v)


def parse_teams_url(url: str) -> dict:
    """Parse a Teams deep link into a target dict.

    Handles the links the Teams client's "Copy link" produces:
      /l/message/<thread>/<msgId>?groupId=..&parentMessageId=..   (channel message)
      /l/message/<chatId>/<msgId>?context={"contextType":"chat"}  (chat message)
      /l/chat/<chatId>/0?tenantId=..                               (chat)
      /l/channel/<channelId>/<name>?groupId=..                     (channel)
      /l/team/<channelId>/conversations?groupId=..                 (team -> General)
    """
    p = urlparse(url.strip())
    host = (p.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in TEAMS_HOSTS):
        raise click.BadParameter(f"Not a Teams link: {url}")
    qs = {k: v[0] for k, v in parse_qs(p.query).items()}
    parts = [unquote(x) for x in p.path.split("/") if x]
    if "l" not in parts:
        raise click.BadParameter(
            f"Unsupported Teams link shape (use the message's 'Copy link'): {url}")
    parts = parts[parts.index("l") + 1:]
    if len(parts) < 2:
        raise click.BadParameter(f"Teams link is missing its conversation id: {url}")
    kind, thread = parts[0].lower(), parts[1]
    ctx = {}
    if qs.get("context"):
        try:
            ctx = json.loads(qs["context"])
        except ValueError:
            ctx = {}
    team = qs.get("groupId")
    if kind == "message":
        if len(parts) < 3:
            raise click.BadParameter(f"Message link without a message id: {url}")
        mid = parts[2]
        if is_channel_thread(thread) and ctx.get("contextType") != "chat":
            parent = qs.get("parentMessageId")
            return {"kind": "channel", "team_id": team, "channel_id": thread,
                    "message_id": mid, "parent_id": parent if parent and parent != mid else None}
        return {"kind": "chat", "chat_id": thread, "message_id": mid}
    if kind == "chat":
        return {"kind": "chat", "chat_id": thread}
    if kind in ("channel", "team"):
        return {"kind": "channel", "team_id": team, "channel_id": thread}
    raise click.BadParameter(f"Unsupported Teams link type '/l/{kind}/': {url}")


def parse_target(value: str) -> dict:
    """Classify a user-supplied target without touching the network."""
    v = value.strip()
    if v.lower().startswith(("http://", "https://")):
        return parse_teams_url(v)
    if is_channel_thread(v):
        return {"kind": "channel", "team_id": None, "channel_id": v}
    if is_chat_id(v):
        return {"kind": "chat", "chat_id": v}
    if re.fullmatch(r"[^@\s/]+@[^@\s/]+\.[A-Za-z]{2,}", v):
        return {"kind": "person", "email": v}
    if v.startswith("@") and len(v) > 1:
        return {"kind": "name", "name": v[1:]}
    if "/" in v:
        team, _, channel = v.partition("/")
        return {"kind": "channel-name", "team": team.strip(), "channel": channel.strip().lstrip("#")}
    return {"kind": "name", "name": v}


def parse_message_ref(value: str, *, chat: str | None = None, team: str | None = None,
                      channel: str | None = None) -> dict:
    """A message: a Teams message link, or a bare id plus --chat / --team+--channel."""
    v = value.strip()
    if v.lower().startswith("http"):
        t = parse_teams_url(v)
        if not t.get("message_id"):
            raise click.BadParameter(f"That link is a conversation, not a message: {v}")
        return t
    if not re.fullmatch(r"\d{10,}", v):
        raise click.BadParameter(f"Expected a Teams message link or a numeric message id, got {v!r}")
    if chat:
        return {"kind": "chat", "chat_id": chat, "message_id": v}
    if channel:
        return {"kind": "channel", "team_id": team, "channel_id": channel, "message_id": v,
                "parent_id": None}
    raise click.BadParameter("A bare message id needs --chat, or --channel (and ideally --team)")


def chat_permalink(chat_id: str, message_id: str | None = None, tenant_id: str | None = None) -> str:
    """Deep link to a chat message. Graph returns webUrl=null for chat messages,
    so this is constructed in the same shape the Teams client's 'Copy link' uses."""
    if not message_id:
        url = f"https://teams.microsoft.com/l/chat/{quote(chat_id, safe='')}/0"
        return url + (f"?tenantId={tenant_id}" if tenant_id else "")
    url = (f"https://teams.microsoft.com/l/message/{quote(chat_id, safe='')}/{message_id}"
           f"?context={quote(CHAT_CONTEXT, safe='')}")
    return url + (f"&tenantId={tenant_id}" if tenant_id else "")


def channel_permalink(team_id: str | None, channel_id: str, message_id: str,
                      parent_id: str | None = None, tenant_id: str | None = None) -> str:
    q = []
    if team_id:
        q.append(f"groupId={team_id}")
    if tenant_id:
        q.append(f"tenantId={tenant_id}")
    q.append(f"parentMessageId={parent_id or message_id}")
    return (f"https://teams.microsoft.com/l/message/{quote(channel_id, safe='')}/{message_id}?"
            + "&".join(q))


def jwt_claims(token: str) -> dict:
    """Decode (NOT verify) an access token's payload - used only to read `scp`."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return {}


# --------------------------------------------------------------------------
# Auth (MSAL public client, delegated, persisted refresh token)
# --------------------------------------------------------------------------

AAD_HINTS = {
    "AADSTS65001": "Consent for these scopes has not been granted. Run `login` again; if the "
                   "sign-in page says 'Need admin approval', a tenant admin must grant them.",
    "AADSTS90094": "The tenant requires an admin to approve these permissions. See SKILL.md "
                   "'Admin consent' for the exact grant to ask for.",
    "AADSTS50076": "MFA is required - run `login` (interactive) again.",
    "AADSTS50079": "MFA registration is required - run `login` (interactive) again.",
    "AADSTS53003": "Blocked by a Conditional Access policy. If device code was used, try the "
                   "default interactive `login`; otherwise the tenant admin must allow it.",
    "AADSTS700082": "The refresh token expired (90 days idle, or revoked). Run `login` again.",
    "AADSTS70043": "The refresh token expired under a sign-in frequency policy. Run `login` again.",
    "AADSTS700016": "The client id isn't registered in (or consented to by) this tenant. Check "
                    "--client-id / --tenant against the app registration (SKILL.md).",
    "AADSTS50020": "This account is not in the tenant. Check --tenant and --login-hint.",
    "AADSTS7000218": "The app registration has 'Allow public client flows' = No. Set it to Yes "
                     "(Authentication > Advanced settings) - required for device code and for "
                     "redeeming the refresh token without a secret.",
    "AADSTS50194": "The app is single-tenant: pass --tenant <domain> instead of organizations.",
    "AADSTS50011": "Redirect URI mismatch: add the 'Mobile and desktop applications' platform "
                   "with redirect URI http://localhost to the app registration.",
    "AADSTS500113": "The app registration has no redirect URI: add 'Mobile and desktop "
                    "applications' with http://localhost.",
}


def aad_hint(text: str) -> str | None:
    for code, hint in AAD_HINTS.items():
        if code in (text or ""):
            return hint
    return None


EXIT_AUTH_EXPIRED = 3
EXIT_MISSING_SCOPE = 4


class AuthError(click.ClickException):
    """Dead/missing session or token. Output contract: exit code 3 plus one JSON
    line on stderr {"error": "auth_expired", ...} - never an empty success."""
    exit_code = EXIT_AUTH_EXPIRED

    def __init__(self, message: str, fix: str | None = None, backend: str = "graph"):
        self.fix, self.backend = fix, backend
        super().__init__(message + (f"\nFix: {fix}" if fix else ""))

    def show(self, file=None):
        click.echo(json.dumps({"error": "auth_expired", "backend": self.backend,
                               "detail": self.message.splitlines()[0], "fix": self.fix}), err=True)
        super().show(file)


class MissingScope(click.ClickException):
    """The token is fine but lacks a permission: exit 4 + {"error": "missing_scope"}."""
    exit_code = EXIT_MISSING_SCOPE

    def __init__(self, cap: str | None, scope: str | None = None):
        self.scopes = CAPS[cap] if cap else [scope]
        super().__init__(
            f"Needs Graph permission {' or '.join(self.scopes)}, which this app hasn't been "
            "granted for you.\nFix: add it to the app registration's API permissions and have a "
            "tenant admin extend the consent (SKILL.md 'Admin consent'); `doctor` lists what's "
            "granted. Or use --backend playwright.")

    def show(self, file=None):
        click.echo(json.dumps({"error": "missing_scope", "scopes": self.scopes}), err=True)
        super().show(file)


class Auth:
    """Wraps MSAL. Tokens are never printed; the cache file is encrypted with
    DPAPI on Windows (msal-extensions) and lives under home_dir()."""

    def __init__(self, client_id: str | None = None, tenant: str | None = None):
        self.cfg = load_config()
        self.client_id = client_id or os.getenv("MS_TEAMS_CLIENT_ID") or self.cfg.get("client_id")
        self.tenant = tenant or os.getenv("MS_TEAMS_TENANT") or self.cfg.get("tenant")
        # Escape hatch for tests and one-off debugging with a token from
        # elsewhere (e.g. Graph Explorer). Never written to disk.
        self.static_token = os.getenv("MS_TEAMS_ACCESS_TOKEN")
        self._app = None
        self.cache_kind = "not opened"
        self._failed = False
        self._granted: set[str] | None = None

    @property
    def cache_file(self) -> Path:
        return home_dir() / "token_cache.bin"

    def _build_cache(self):
        import msal_extensions
        home_dir().mkdir(parents=True, exist_ok=True)
        try:
            persistence = msal_extensions.build_encrypted_persistence(str(self.cache_file))
            self.cache_kind = f"encrypted ({type(persistence).__name__})"
        except Exception as exc:  # e.g. Linux without libsecret
            persistence = msal_extensions.FilePersistence(str(self.cache_file))
            self.cache_kind = f"plaintext file - encryption unavailable ({type(exc).__name__})"
            try:
                os.chmod(self.cache_file, 0o600)
            except OSError:
                pass
        return msal_extensions.PersistedTokenCache(persistence)

    @property
    def app(self):
        if self._app is None:
            if not self.client_id or not self.tenant:
                raise click.ClickException(
                    "No app registration configured. Run once:\n"
                    "  uv run ms_teams.py configure --client-id <app id> --tenant <tenant id>\n"
                    "(SKILL.md 'App registration' says what to register.)")
            import msal
            self._app = msal.PublicClientApplication(
                self.client_id,
                authority=f"https://login.microsoftonline.com/{self.tenant}",
                token_cache=self._build_cache(),
            )
        return self._app

    def account(self):
        if self.static_token:
            return None
        username = self.cfg.get("username")
        accounts = self.app.get_accounts(username=username) if username else []
        accounts = accounts or self.app.get_accounts()
        return accounts[0] if accounts else None

    def token(self) -> str:
        """Access token for Graph `.default` (= every scope granted to this app for you)."""
        if self.static_token:
            return self.static_token
        account = self.account()
        if not account:
            raise AuthError("Not signed in to Graph.", fix="uv run ms_teams.py login")
        if self._failed:
            raise AuthError("Graph token refresh already failed in this run")
        result = self.app.acquire_token_silent([GRAPH_DEFAULT], account=account)
        if result and "access_token" in result:
            return result["access_token"]
        self._failed = True
        detail = (result or {}).get("error_description", "") if result else ""
        first = detail.splitlines()[0] if detail else "no cached refresh token"
        raise AuthError(f"Graph token refresh failed: {first}",
                        fix=aad_hint(detail) or "uv run ms_teams.py login")

    def granted(self) -> set[str]:
        """Scopes in the current access token's `scp` claim."""
        if self._granted is None:
            self._granted = set(jwt_claims(self.token()).get("scp", "").split())
        return self._granted

    def refresh_token_entry(self) -> dict | None:
        """The cached refresh token for the signed-in account (secret included)."""
        import msal
        account = self.account()
        if not account:
            return None
        self.app  # ensure the cache is open
        cache = self.app.token_cache
        query = {"client_id": self.client_id, "home_account_id": account["home_account_id"]}
        rt = msal.TokenCache.CredentialType.REFRESH_TOKEN
        search = getattr(cache, "search", None)  # msal >= 1.29; find() is deprecated
        entries = list(search(rt, query=query)) if search else cache.find(rt, query=query)
        return entries[0] if entries else None

    def has_scope(self, scope: str) -> bool:
        return scope.lower() in {s.lower() for s in self.granted()}

    def can(self, cap: str) -> bool:
        return any(self.has_scope(s) for s in CAPS[cap])

    def login(self, scopes: list[str], *, device_code: bool, login_hint: str | None) -> dict:
        app = self.app
        if device_code:
            flow = app.initiate_device_flow(scopes=scopes)
            if "user_code" not in flow:
                raise AuthError(f"Could not start device code flow: "
                                f"{flow.get('error_description') or flow}")
            click.echo(flow["message"], err=True)
            sys.stderr.flush()
            result = app.acquire_token_by_device_flow(flow)
        else:
            click.echo("Opening a browser for Microsoft sign-in (waits up to 5 minutes)...",
                       err=True)
            result = app.acquire_token_interactive(
                scopes, login_hint=login_hint, prompt="select_account", timeout=300,
                domain_hint=(login_hint.split("@", 1)[1] if login_hint and "@" in login_hint
                             else None))
        if not result or "access_token" not in result:
            desc = (result or {}).get("error_description") or str(result)
            hint = aad_hint(desc)
            raise AuthError(f"Sign-in failed: {desc.splitlines()[0] if desc else 'unknown'}"
                            + (f"\nFix: {hint}" if hint else ""))
        return result


# --------------------------------------------------------------------------
# Graph client
# --------------------------------------------------------------------------

class GraphError(click.ClickException):
    def __init__(self, status: int, code: str, message: str, hint: str | None = None):
        self.status, self.code, self.graph_message = status, code, message
        text = f"Graph {status} {code}: {message}"
        if hint:
            text += f"\nFix: {hint}"
        super().__init__(text)


def graph_hint(status: int, code: str, message: str, url: str, need: str | None) -> str | None:
    low = (message or "").lower()
    if status == 401:
        return "token rejected - run `doctor`, then `login` if needed"
    if status == 403:
        if "missing scope" in low or "scope" in low or "permission" in low:
            return "the token lacks a scope for this call - run `doctor` to see what is granted"
        return "you may not be a member of that chat/team, or the action isn't allowed for you"
    if status == 404:
        return "wrong id, or the signed-in user can't see it (run `chats` / `teams` to list)"
    if status == 429:
        return "throttled by Graph - wait a minute (Teams limits are per user per app)"
    return None


class Graph:
    def __init__(self, auth: Auth, session: requests.Session | None = None):
        self.auth = auth
        self.session = session or requests.Session()
        self._me: dict | None = None

    def require(self, cap: str) -> None:
        """Fail fast, with the fix, when no granted scope unlocks `cap`."""
        if not self.auth.can(cap):
            raise MissingScope(cap)

    def request(self, method: str, url: str, *, params=None, json_body=None, data=None,
                headers=None, need: str | None = None, raw: bool = False, auth: bool = True,
                stream: bool = False):
        full = url if url.startswith("http") else GRAPH + url
        if auth and not full.startswith("https://graph.microsoft.com/"):
            raise click.ClickException(f"Refusing to send the Graph token to {urlparse(full).hostname}")
        if need and auth and not self.auth.has_scope(need):
            raise MissingScope(None, need)
        resp = None
        for attempt in range(5):
            h = dict(headers or {})
            if auth:
                h["Authorization"] = f"Bearer {self.auth.token()}"
            resp = self.session.request(method, full, params=params, json=json_body, data=data,
                                        headers=h, timeout=120, stream=stream)
            if resp.status_code in (429, 503, 504) and attempt < 4:
                wait = resp.headers.get("Retry-After")
                try:
                    delay = min(float(wait), 120.0) if wait is not None else 2.0 ** attempt
                except ValueError:
                    delay = 2.0 ** attempt
                click.echo(f"(Graph {resp.status_code}; retrying in {delay:.0f}s)", err=True)
                time.sleep(delay)
                continue
            break
        if resp.status_code == 401 and auth:
            raise AuthError("Graph rejected the token (401)", fix="uv run ms_teams.py login")
        if resp.status_code >= 400:
            code, message = "Error", resp.text[:300]
            try:
                err = resp.json().get("error") or {}
                code, message = err.get("code") or code, err.get("message") or message
            except ValueError:
                pass
            raise GraphError(resp.status_code, code, message,
                             graph_hint(resp.status_code, code, message, full, need))
        if raw:
            return resp
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    def get(self, url, need=None, **params):
        return self.request("GET", url, params=params or None, need=need)

    def post(self, url, body, need=None):
        return self.request("POST", url, json_body=body, need=need)

    def paged(self, url: str, params: dict | None = None, *, need: str | None = None,
              limit: int | None = None):
        """Yield items across @odata.nextLink pages."""
        count = 0
        page = self.request("GET", url, params=params, need=need)
        while True:
            for item in page.get("value") or []:
                yield item
                count += 1
                if limit and count >= limit:
                    return
            nxt = page.get("@odata.nextLink")
            if not nxt:
                return
            page = self.request("GET", nxt, need=need)

    def lookup_ok(self) -> bool:
        try:
            return self.auth.can("users")
        except click.ClickException:
            return False

    def me(self) -> dict:
        if self._me is None:
            if not self.auth.has_scope("User.Read"):
                raise MissingScope(None, "User.Read")
            self._me = self.get("/me", **{"$select": "id,displayName,mail,userPrincipalName"})
        return self._me

    @property
    def tenant_id(self) -> str | None:
        return self.auth.cfg.get("tenant_id")


# --------------------------------------------------------------------------
# Name cache + resolution
# --------------------------------------------------------------------------

class Names:
    """Persistent id -> display name cache (users) and channel -> team map."""

    def __init__(self):
        self.path = home_dir() / "names.json"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("users", {})
        self.data.setdefault("emails", {})
        self.data.setdefault("channels", {})
        self.dirty = False
        self._looked_up: set[str] = set()

    def save(self):
        if self.dirty:
            home_dir().mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
            self.dirty = False

    def remember(self, uid: str | None, name: str | None, email: str | None = None):
        if uid and name and self.data["users"].get(uid) != name:
            self.data["users"][uid] = name
            self.dirty = True
        if uid and email and self.data["emails"].get(uid) != email:
            self.data["emails"][uid] = email
            self.dirty = True

    def learn_members(self, members: list[dict] | None):
        for m in members or []:
            self.remember(m.get("userId"), m.get("displayName"), m.get("email"))

    def _lookup(self, g, uid: str) -> None:
        """One /users read per id per run, only when User.ReadBasic.All is granted."""
        if uid in self._looked_up or not g.lookup_ok():
            return
        self._looked_up.add(uid)
        try:
            u = g.get(f"/users/{uid}", **{"$select": "displayName,mail,userPrincipalName"})
            self.remember(uid, u.get("displayName"), u.get("mail") or u.get("userPrincipalName"))
        except click.ClickException:
            pass  # external/guest users in other tenants aren't readable

    def user(self, g, uid: str | None, fallback: str | None = None) -> str:
        if not uid:
            return fallback or "unknown"
        if uid in self.data["users"]:
            return self.data["users"][uid]
        if fallback:
            self.remember(uid, fallback)
            return fallback
        self._lookup(g, uid)
        return self.data["users"].get(uid) or uid[:8]

    def email(self, g, uid: str | None) -> str | None:
        if not uid:
            return None
        if uid not in self.data["emails"]:
            self._lookup(g, uid)
        return self.data["emails"].get(uid)


def sender(msg: dict) -> tuple[str | None, str]:
    f = msg.get("from") or {}
    for key in ("user", "application", "device"):
        ident = f.get(key)
        if ident:
            return ident.get("id"), ident.get("displayName") or key
    return None, "system"


def chat_label(chat: dict, me_id: str | None) -> str:
    if chat.get("topic"):
        return chat["topic"]
    names = [m.get("displayName") or "?" for m in chat.get("members") or []
             if m.get("userId") != me_id]
    if names:
        return ", ".join(names)
    return {"oneOnOne": "1:1 chat", "group": "group chat", "meeting": "meeting chat"}.get(
        chat.get("chatType"), chat.get("id", "chat"))


@dataclass
class Conv:
    kind: str                      # "chat" | "channel"
    chat_id: str | None = None
    team_id: str | None = None
    channel_id: str | None = None
    message_id: str | None = None  # from a message link
    parent_id: str | None = None   # channel reply's root post
    label: str = ""
    create_with: dict | None = None  # 1:1 chat that doesn't exist yet: the other user
    extra: dict = field(default_factory=dict)

    @property
    def root_id(self) -> str | None:
        return self.parent_id or self.message_id

    def ctx(self) -> dict:
        d = {"kind": self.kind}
        if self.kind == "chat":
            d["chat_id"] = self.chat_id
        else:
            d.update(team_id=self.team_id, channel_id=self.channel_id)
        d["conversation"] = self.label or None
        return d


def list_chats(g: Graph, *, limit: int = 200, members: bool = True):
    params = {"$top": "50", "$orderby": "lastMessagePreview/createdDateTime desc",
              "$expand": "lastMessagePreview,members" if members else "lastMessagePreview"}
    try:
        yield from g.paged("/me/chats", params, limit=limit)
    except GraphError as exc:
        if exc.status != 400 or not members:
            raise
        # Some tenants reject the double expand; fall back to preview only.
        params["$expand"] = "lastMessagePreview"
        yield from g.paged("/me/chats", params, limit=limit)


def find_user(g: Graph, email_or_id: str) -> dict:
    try:
        return g.get(f"/users/{quote(email_or_id)}",
                     **{"$select": "id,displayName,mail,userPrincipalName"})
    except GraphError as exc:
        if exc.status == 404:
            raise click.ClickException(f"No user {email_or_id!r} in this tenant directory")
        raise


def find_one_on_one(g: Graph, user_id: str) -> dict | None:
    for chat in list_chats(g, limit=500):
        if chat.get("chatType") != "oneOnOne":
            continue
        if any(m.get("userId") == user_id for m in chat.get("members") or []):
            return chat
    return None


def joined_teams(g: Graph) -> list[dict]:
    return list(g.paged("/me/joinedTeams", {"$select": "id,displayName,description"}))


def team_channels(g: Graph, team_id: str) -> list[dict]:
    return list(g.paged(f"/teams/{team_id}/channels",
                        {"$select": "id,displayName,membershipType,webUrl"}))


def channel_info(g: Graph, names: Names, channel_id: str, team_id: str | None) -> dict:
    """Return {team_id, team_name, channel_name}, scanning joined teams if needed."""
    cached = names.data["channels"].get(channel_id)
    if cached and (not team_id or cached.get("team_id") == team_id):
        return cached
    if not (g.auth.can("teams_list") and g.auth.can("channels_list")):
        if team_id:  # a link carries the team id; names are just cosmetic
            return {"team_id": team_id, "team_name": team_id, "channel_name": channel_id}
        raise MissingScope("teams_list")
    teams = joined_teams(g)
    if team_id:
        teams = [t for t in teams if t["id"] == team_id] or [{"id": team_id, "displayName": "?"}]
    for t in teams:
        for c in team_channels(g, t["id"]):
            names.data["channels"][c["id"]] = {"team_id": t["id"], "team_name": t.get("displayName"),
                                               "channel_name": c.get("displayName")}
        names.dirty = True
        if channel_id in names.data["channels"]:
            return names.data["channels"][channel_id]
    raise click.ClickException(f"Channel {channel_id} is not in any team you belong to")


def resolve_conv(g: Graph, names: Names, target: str, *, for_send: bool = False) -> Conv:
    t = parse_target(target)
    me_id = g.me().get("id")
    if t["kind"] == "chat":
        conv = Conv("chat", chat_id=t["chat_id"], message_id=t.get("message_id"))
        try:
            chat = g.get(f"/chats/{quote(conv.chat_id)}", **{"$expand": "members"})
            conv.label = chat_label(chat, me_id)
            names.learn_members(chat.get("members"))
        except GraphError as exc:
            if exc.status in (403, 404):
                raise click.ClickException(f"Chat {conv.chat_id} not found or you're not in it")
            raise
        return conv
    if t["kind"] == "channel":
        info = channel_info(g, names, t["channel_id"], t.get("team_id"))
        return Conv("channel", team_id=info["team_id"], channel_id=t["channel_id"],
                    message_id=t.get("message_id"), parent_id=t.get("parent_id"),
                    label=f"{info.get('team_name')} / {info.get('channel_name')}")
    if t["kind"] == "channel-name":
        teams = [x for x in joined_teams(g) if x.get("displayName", "").lower() == t["team"].lower()]
        if not teams:
            raise click.ClickException(f"No joined team named {t['team']!r} (run `teams`)")
        chans = [c for c in team_channels(g, teams[0]["id"])
                 if c.get("displayName", "").lower() == t["channel"].lower()]
        if not chans:
            raise click.ClickException(f"No channel {t['channel']!r} in {t['team']} "
                                       f"(run `channels \"{t['team']}\"`)")
        return Conv("channel", team_id=teams[0]["id"], channel_id=chans[0]["id"],
                    label=f"{teams[0]['displayName']} / {chans[0]['displayName']}")
    if t["kind"] == "person":
        user = find_user(g, t["email"])
        names.remember(user["id"], user.get("displayName"))
        chat = find_one_on_one(g, user["id"])
        if chat:
            return Conv("chat", chat_id=chat["id"], label=user.get("displayName") or t["email"])
        if not for_send:
            raise click.ClickException(f"No 1:1 chat with {t['email']} yet")
        return Conv("chat", label=user.get("displayName") or t["email"], create_with=user)
    # Free-text name: match chat topics and member names.
    needle = t["name"].lower()
    hits = []
    for chat in list_chats(g, limit=300):
        label = chat_label(chat, me_id)
        if needle in label.lower():
            hits.append((chat, label))
    exact = [h for h in hits if h[1].lower() == needle]
    if exact:
        hits = exact
    if not hits:
        raise click.ClickException(f"No chat matches {t['name']!r}. Try an email address, a chat "
                                   "link, or `chats` to list them.")
    if len(hits) > 1:
        ones = [h for h in hits if h[0].get("chatType") == "oneOnOne"]
        if len(ones) == 1:
            hits = ones
        else:
            listing = "\n".join(f"  {h[0]['id']}  [{h[0].get('chatType')}] {h[1]}" for h in hits[:15])
            raise click.ClickException(f"{len(hits)} chats match {t['name']!r}; pass one id:\n{listing}")
    chat, label = hits[0]
    return Conv("chat", chat_id=chat["id"], label=label)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def is_inline_image(src: str) -> bool:
    """Graph hosted content, or the web app's AMS image (Playwright backend)."""
    return "/hostedContents/" in src or bool(re.search(r"/v1/objects/[^/]+/views/img", src))


def hosted_image_urls(msg: dict) -> list[str]:
    body = (msg.get("body") or {}).get("content") or ""
    urls = [_html.unescape(u) for u in re.findall(r'<img[^>]+src="([^"]+)"', body, re.I)]
    return [u for u in urls if is_inline_image(u)]


def reaction_label(r: dict) -> str:
    rtype = r.get("reactionType") or r.get("displayName") or "?"
    if rtype == "custom":
        return r.get("displayName") or "custom"
    return REACTION_EMOJI.get(rtype, rtype)


def html_to_text(content: str, content_type: str, msg: dict | None = None,
                 image_paths: dict[str, str] | None = None, plain: bool = False) -> str:
    """Message HTML -> readable text. plain=True strips formatting (JSON `text`);
    otherwise light Markdown is kept (human output)."""
    if (content_type or "").lower() != "html":
        return (content or "").strip()
    from bs4 import BeautifulSoup
    from markdownify import markdownify
    soup = BeautifulSoup(content or "", "html.parser")
    mentions = {str(m.get("id")): m for m in (msg or {}).get("mentions") or []}

    def mentioned_id(tag):
        m = mentions.get(tag.get("id", ""))
        ent = (m or {}).get("mentioned") or {}
        who = ent.get("user") or ent.get("application") or {}
        return who.get("id") or (m or {}).get("mentionText")

    # Teams splits "Jane Smith" into two <at> tags for the same person.
    for at in list(soup.find_all("at")):
        if getattr(at, "decomposed", False) or at.parent is None:
            continue
        while True:
            nxt, gap = at.next_sibling, []
            while isinstance(nxt, str) and not nxt.strip():
                gap.append(nxt)
                nxt = nxt.next_sibling
            if (getattr(nxt, "name", None) == "at" and mentioned_id(nxt)
                    and mentioned_id(nxt) == mentioned_id(at)):
                at.string = f"{at.get_text()} {nxt.get_text()}"
                for s in gap:
                    s.extract()
                nxt.decompose()
            else:
                break
    for at in soup.find_all("at"):
        at.replace_with(f"@{at.get_text()}")
    # Web-app (chat service) markup: mentions are spans with a Mention itemtype.
    for span in soup.find_all("span", attrs={"itemtype": re.compile(r"/Mention$")}):
        span.replace_with(f"@{span.get_text()}")
    for tag in soup.find_all("emoji"):
        tag.replace_with(tag.get("alt") or "")
    for tag in soup.find_all("attachment"):
        tag.decompose()
    for img in soup.find_all("img"):
        src = _html.unescape(img.get("src") or "")
        if is_inline_image(src):
            path = (image_paths or {}).get(src) or (image_paths or {}).get(src.split("?", 1)[0])
            img.replace_with(f"[image -> {path}]" if path else "[image]")
        elif img.get("itemtype", "").endswith("/Emoji") or img.get("alt"):
            img.replace_with(img.get("alt") or "")
        else:
            img.replace_with(f"[image {src}]")
    for cb in soup.find_all("codeblock"):
        code = cb.get_text()
        cb.replace_with(f"\n```\n{code}\n```\n")
    strip = ["b", "strong", "i", "em", "s", "del", "u", "a", "code", "span"] if plain else None
    text = markdownify(str(soup), heading_style="ATX", bullets="-", escape_underscores=False,
                       escape_asterisks=False, escape_misc=False, strip=strip)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def describe_attachments(msg: dict, file_paths: dict[str, str]) -> list[str]:
    lines = []
    for a in msg.get("attachments") or []:
        ctype = a.get("contentType") or ""
        name = a.get("name") or ""
        if ctype == "messageReference":
            try:
                ref = json.loads(a.get("content") or "{}")
            except ValueError:
                ref = {}
            who = ((ref.get("messageSender") or {}).get("user") or {}).get("displayName") or "?"
            prev = (ref.get("messagePreview") or "").strip().replace("\n", " ")
            lines.append(f"[quoting {who}, id={ref.get('messageId') or a.get('id')}] "
                         f"\"{prev[:200]}\"")
        elif ctype == "reference":
            url = a.get("contentUrl") or ""
            path = file_paths.get(url)
            label = name or url.rsplit("/", 1)[-1]
            kind = "image" if Path(label).suffix.lower() in IMAGE_EXTS else "file"
            lines.append(f"[{kind}] {label}")
            lines.append(f"        -> {path}" if path else
                         "        -> not downloaded; run: ms_teams.py download <message link>")
        elif ctype.startswith("application/vnd.microsoft.card"):
            title = ""
            try:
                card = json.loads(a.get("content") or "{}")
                title = card.get("title") or card.get("text") or ""
                if not title:
                    texts = [b.get("text") for b in card.get("body") or [] if isinstance(b, dict)
                             and b.get("text")]
                    title = " / ".join(texts[:3])
            except ValueError:
                pass
            lines.append(f"[card] {title[:300]}".rstrip())
        else:
            lines.append(f"[attachment {ctype}] {name}".rstrip())
    return lines


def event_text(msg: dict) -> str:
    detail = msg.get("eventDetail") or {}
    etype = (detail.get("@odata.type") or "").rsplit(".", 1)[-1] or "event"
    bits = [etype]
    for key in ("callDuration", "chatDisplayName", "channelDisplayName"):
        if detail.get(key):
            bits.append(f"{key}={detail[key]}")
    members = detail.get("members") or []
    if members:
        bits.append("members=" + ", ".join(m.get("displayName") or (m.get("id") or "?")[:8]
                                           for m in members[:8]))
    return "[system: " + " ".join(bits) + "]"


def message_text(msg: dict, image_paths: dict[str, str] | None = None, plain: bool = False) -> str:
    if msg.get("deletedDateTime"):
        return "[deleted]"
    if msg.get("messageType") == "systemEventMessage":
        return event_text(msg)
    body = msg.get("body") or {}
    return html_to_text(body.get("content") or "", body.get("contentType") or "text", msg,
                        image_paths, plain=plain) or "[no text content]"


def message_record(g, names: Names, msg: dict, conv: Conv,
                   file_paths: dict[str, str] | None = None, raw_html: bool = False,
                   backend: str = "graph") -> dict:
    """The output contract's message record (SKILL.md 'Output contract').

    Identical keys on both backends. Key messages on (conversation_id, id).
    """
    file_paths = file_paths or {}
    uid, uname = sender(msg)
    names.remember(uid, uname if uname not in ("user", "system") else None)
    rec = {"record": "message", "backend": backend, **conv.ctx()}
    mid = msg.get("id")
    if conv.kind == "chat":
        rec["chat_id"] = msg.get("chatId") or conv.chat_id
        rec["conversation_id"] = rec["chat_id"]
        permalink = chat_permalink(rec["chat_id"], mid, g.tenant_id)
    else:
        rec["conversation_id"] = conv.channel_id
        permalink = msg.get("webUrl") or channel_permalink(
            conv.team_id, conv.channel_id, mid, msg.get("replyToId"), g.tenant_id)
    quotes = []
    for a in msg.get("attachments") or []:
        if a.get("contentType") == "messageReference":
            quotes.append(a.get("id"))
    body = msg.get("body") or {}
    rec.update({
        "id": mid,
        "reply_to_id": msg.get("replyToId"),
        "thread_id": (msg.get("replyToId") or mid) if conv.kind == "channel" else None,
        "created": msg.get("createdDateTime"),
        "modified": msg.get("lastModifiedDateTime"),
        "edited": msg.get("lastEditedDateTime"),
        "deleted": bool(msg.get("deletedDateTime")),
        "type": msg.get("messageType"),
        "importance": msg.get("importance"),
        "subject": msg.get("subject"),
        "from": {"id": uid, "name": names.user(g, uid, uname) if uid else uname,
                 "email": names.email(g, uid)},
        "text": message_text(msg, file_paths, plain=True),
        "mentions": [{"id": ((m.get("mentioned") or {}).get("user") or {}).get("id"),
                      "text": m.get("mentionText")} for m in msg.get("mentions") or []],
        "reactions": [{"reaction": reaction_label(r), "type": r.get("reactionType"),
                       "user_id": ((r.get("user") or {}).get("user") or {}).get("id"),
                       "user": names.user(g, ((r.get("user") or {}).get("user") or {}).get("id")),
                       "created": r.get("createdDateTime")} for r in msg.get("reactions") or []],
        "attachments": [{"type": a.get("contentType"), "name": a.get("name"),
                         "url": a.get("contentUrl"),
                         "local_path": file_paths.get(a.get("contentUrl") or "")}
                        for a in msg.get("attachments") or []
                        if a.get("contentType") != "messageReference"],
        "images": [{"url": u, "local_path": file_paths.get(u)} for u in hosted_image_urls(msg)],
        "quotes": quotes,
        "web_url": msg.get("webUrl"),
        "permalink": permalink,
    })
    if raw_html:
        rec["html"] = body.get("content")
    return rec


def chat_record(g, names: Names, chat: dict, backend: str = "graph") -> dict:
    """The output contract's chat record (chats --json, messages --all-chats)."""
    prev = chat.get("lastMessagePreview") or {}
    pbody = prev.get("body") or {}
    names.learn_members(chat.get("members"))
    me_id = g.me().get("id") if hasattr(g, "me") else None
    activity = max([d for d in (prev.get("createdDateTime"), chat.get("lastUpdatedDateTime")) if d],
                   key=lambda d: parse_graph_dt(d) or datetime.min.replace(tzinfo=timezone.utc),
                   default=None)
    return {
        "record": "chat", "backend": backend, "kind": "chat",
        "chat_id": chat["id"], "conversation_id": chat["id"],
        "type": chat.get("chatType"), "topic": chat.get("topic"),
        "name": chat_label(chat, me_id),
        "members": [{"id": m.get("userId"),
                     "name": m.get("displayName") or names.data["users"].get(m.get("userId")),
                     "email": m.get("email") or names.data["emails"].get(m.get("userId"))}
                    for m in chat.get("members") or []],
        "last_activity": activity,
        "created": chat.get("createdDateTime"),
        "last_message": {"id": prev.get("id"), "created": prev.get("createdDateTime"),
                         "from": ((prev.get("from") or {}).get("user") or {}).get("displayName"),
                         "text": html_to_text(pbody.get("content") or "",
                                              pbody.get("contentType") or "text", plain=True)[:500]}
        if prev else None,
        "web_url": chat.get("webUrl") or chat_permalink(chat["id"], None, g.tenant_id),
    }


def render_message(g: Graph, names: Names, msg: dict, file_paths: dict[str, str],
                   indent: bool = False) -> list[str]:
    uid, uname = sender(msg)
    who = names.user(g, uid, uname) if uid else uname
    flags = []
    if msg.get("lastEditedDateTime"):
        flags.append("edited")
    if msg.get("importance") in ("high", "urgent"):
        flags.append(msg["importance"].upper())
    head = f"**{who}** ({fmt_local(msg.get('createdDateTime'))})"
    if flags:
        head += " [" + ", ".join(flags) + "]"
    head += f"  id={msg.get('id')}:"
    lines = [head]
    if msg.get("subject"):
        lines.append(f"Subject: {msg['subject']}")
    lines.extend(message_text(msg, file_paths).split("\n"))
    if not msg.get("deletedDateTime"):
        lines.extend(describe_attachments(msg, file_paths))
    reactions = msg.get("reactions") or []
    if reactions:
        groups: dict[str, list[str]] = {}
        for r in reactions:
            ruid = ((r.get("user") or {}).get("user") or {}).get("id")
            groups.setdefault(reaction_label(r), []).append(names.user(g, ruid))
        lines.append("reactions: " + ", ".join(f"{k} x{len(v)} ({', '.join(v)})"
                                               for k, v in groups.items()))
    prefix = "  > " if indent else ""
    return [prefix + ln for ln in lines] + [""]


# --------------------------------------------------------------------------
# Files: download cache
# --------------------------------------------------------------------------

def cache_dir(dest: str | None = None) -> Path:
    return Path(dest or os.getenv("TEAMS_FILES_DIR") or Path(tempfile.gettempdir()) / "teams-files")


def sweep_files(dest: Path) -> None:
    """Delete our own cached files older than 24h. Touches only `T-<hash>...` names."""
    if not dest.is_dir():
        return
    cutoff = time.time() - FILES_MAX_AGE_HOURS * 3600
    for f in dest.iterdir():
        try:
            if OURS_RE.match(f.name) and f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def url_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def share_id(url: str) -> str:
    """Graph /shares/{id} encoding of a sharing or SharePoint URL."""
    return "u!" + base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def _ext_for(content_type: str | None) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    return {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
            "image/webp": ".webp", "image/bmp": ".bmp"}.get(ct) or mimetypes.guess_extension(ct) or ".bin"


def download_hosted(g: Graph, url: str, dest: Path, need: str | None) -> str | None:
    key = url_key(url)
    existing = list(dest.glob(f"T-{key}.*"))
    if existing:
        return str(existing[0])
    resp = g.request("GET", url, raw=True, need=need)
    data = resp.content
    if data[:15].lstrip().lower().startswith((b"<!doctype", b"<html")):
        click.echo(f"  ! {url[:80]}...: got an HTML page, not the image", err=True)
        return None
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"T-{key}{_ext_for(resp.headers.get('Content-Type'))}"
    target.write_bytes(data)
    return str(target)


def download_reference(g: Graph, url: str, dest: Path, name_hint: str | None = None) -> str | None:
    key = url_key(url)
    existing = list(dest.glob(f"T-{key}-*"))
    if existing:
        return str(existing[0])
    sid = share_id(url)
    item = g.get(f"/shares/{sid}/driveItem", **{"$select": "id,name,size,file"})
    size = item.get("size") or 0
    if size > MAX_ATTACHMENT_BYTES:
        click.echo(f"  ! {item.get('name')}: {size} bytes is over the 500 MB cap, skipped", err=True)
        return None
    resp = g.request("GET", f"/shares/{sid}/driveItem/content", raw=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", item.get("name") or name_hint or "file")
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"T-{key}-{safe}"
    target.write_bytes(resp.content)
    return str(target)


def fetch_message_files(g: Graph, messages: list[dict], conv: Conv, dest_dir: str | None = None,
                        images_only: bool = True) -> dict[str, str]:
    """Download inline images (and, unless images_only, every attached file).

    Returns {url: local path}. Cached by URL hash, so re-reading costs nothing;
    the cache dir is swept of our files older than 24h.
    """
    dest = cache_dir(dest_dir)
    sweep_files(dest)
    need = CHANNEL_READ if conv.kind == "channel" else None
    jobs: list[tuple[str, str, str | None]] = []
    seen = set()
    for msg in messages:
        if msg.get("deletedDateTime"):
            continue
        for u in hosted_image_urls(msg):
            if u.startswith("https://graph.microsoft.com/") and u not in seen:
                seen.add(u)
                jobs.append(("hosted", u, None))
        for a in msg.get("attachments") or []:
            u = a.get("contentUrl")
            if a.get("contentType") != "reference" or not u or u in seen:
                continue
            name = a.get("name") or u.rsplit("/", 1)[-1]
            if images_only and Path(name).suffix.lower() not in IMAGE_EXTS:
                continue
            seen.add(u)
            jobs.append(("ref", u, name))
    if any(k == "ref" for k, _, _ in jobs) and not g.auth.can("files_read"):
        skipped = [n for k, _, n in jobs if k == "ref"]
        click.echo(f"  ! {len(skipped)} attached file(s) not downloaded: needs Files.Read.All",
                   err=True)
        jobs = [j for j in jobs if j[0] != "ref"]
    paths: dict[str, str] = {}
    for kind, u, name in jobs[:MAX_FILES_PER_CALL]:
        try:
            p = (download_hosted(g, u, dest, need) if kind == "hosted"
                 else download_reference(g, u, dest, name))
        except click.ClickException as exc:
            click.echo(f"  ! could not fetch {name or 'image'}: {exc.message.splitlines()[0]}",
                       err=True)
            continue
        if p:
            paths[u] = p
    if len(jobs) > MAX_FILES_PER_CALL:
        click.echo(f"  ! {len(jobs) - MAX_FILES_PER_CALL} more file(s) not fetched "
                   f"(cap {MAX_FILES_PER_CALL}); use `download <message link>`", err=True)
    return paths


# --------------------------------------------------------------------------
# Composing outbound messages
# --------------------------------------------------------------------------

MENTION_RE = re.compile(r"@\[([^\]\n]+)\]\(([^)\s]+)\)")


def compose_html(text: str, fmt: str, conv_kind: str) -> tuple[str, list[tuple[str, str]]]:
    """Turn the draft into Teams HTML. Returns (html, [(display, email_or_id)]).

    Mentions are written `@[Display Name](person@domain.com)` in any format.
    """
    specs: list[tuple[str, str]] = []

    def stash(m):
        specs.append((m.group(1).strip(), m.group(2).strip()))
        return f"MSTEAMSMENTION{len(specs) - 1}X"

    text = MENTION_RE.sub(stash, text or "")
    if fmt == "html":
        body = text
    elif fmt == "text":
        body = _html.escape(text).replace("\n", "<br>")
    else:
        import markdown
        body = markdown.markdown(text, extensions=["fenced_code", "tables", "sane_lists", "nl2br"])
        # Teams gives <p> no margin, so back-to-back paragraphs read as one wall
        # of text. The Teams client itself writes an empty line as <p>&nbsp;</p>;
        # do the same between consecutive paragraphs (lists, headings, tables
        # and code blocks carry their own spacing, so only <p> next to <p>).
        body = re.sub(r"</p>\s*<p>", "</p>\n<p>&nbsp;</p>\n<p>", body)

        def codeblock(m):
            lang = (m.group(1) or "plaintext") if conv_kind == "channel" else "plaintext"
            return f'<codeblock class="{lang}"><code>{m.group(2)}</code></codeblock>'

        body = re.sub(r'<pre><code(?: class="language-([\w+-]+)")?>(.*?)</code></pre>',
                      codeblock, body, flags=re.S)
    for i, (display, _) in enumerate(specs):
        body = body.replace(f"MSTEAMSMENTION{i}X", f'<at id="{i}">{_html.escape(display)}</at>')
    return body, specs


def build_mentions(g: Graph, specs: list[tuple[str, str]]) -> list[dict]:
    out = []
    for i, (display, who) in enumerate(specs):
        user = find_user(g, who)
        out.append({"id": i, "mentionText": display, "mentioned": {"user": {
            "id": user["id"], "displayName": user.get("displayName") or display,
            "userIdentityType": "aadUser"}}})
    return out


def image_size(data: bytes) -> tuple[int, int] | None:
    """Width/height for PNG, GIF and JPEG without Pillow."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", data[6:10])
        if data[:2] == b"\xff\xd8":
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                seg = struct.unpack(">H", data[i + 2:i + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + seg
    except struct.error:
        return None
    return None


def inline_image(path: Path, temp_id: str) -> tuple[str, dict]:
    data = path.read_bytes()
    ctype = mimetypes.guess_type(path.name)[0] or "image/png"
    dims = image_size(data)
    attrs = ""
    if dims and dims[0] and dims[1]:
        w, h = dims
        if w > 800:
            w, h = 800, round(h * 800 / w)
        attrs = f' width="{w}" height="{h}"'
    tag = f'<img src="../hostedContents/{temp_id}/$value" alt="{_html.escape(path.name)}"{attrs}>'
    return tag, {"@microsoft.graph.temporaryId": temp_id,
                 "contentBytes": base64.b64encode(data).decode("ascii"), "contentType": ctype}


def upload_to_onedrive(g: Graph, path: Path) -> dict:
    """Upload to the user's OneDrive 'Microsoft Teams Chat Files' (where Teams puts them)."""
    remote = quote(f"Microsoft Teams Chat Files/{path.name}")
    size = path.stat().st_size
    if size <= SIMPLE_UPLOAD_MAX:
        item = g.request("PUT", f"/me/drive/root:/{remote}:/content",
                         params={"@microsoft.graph.conflictBehavior": "rename"},
                         data=path.read_bytes(),
                         headers={"Content-Type": "application/octet-stream"})
    else:
        sess = g.post(f"/me/drive/root:/{remote}:/createUploadSession",
                      {"item": {"@microsoft.graph.conflictBehavior": "rename"}})
        upload_url = sess["uploadUrl"]
        item = {}
        with path.open("rb") as fh:
            start = 0
            while start < size:
                chunk = fh.read(UPLOAD_CHUNK)
                end = start + len(chunk) - 1
                # The upload URL is pre-authenticated: no bearer header allowed.
                item = g.request("PUT", upload_url, data=chunk, auth=False, headers={
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end}/{size}"})
                start = end + 1
    return g.get(f"/me/drive/items/{item['id']}", **{"$select": "id,name,eTag,webDavUrl,webUrl"})


def attach_file(g: Graph, conv: Conv, path: Path) -> tuple[str, dict]:
    """Upload, share with the conversation, and return (body tag, attachment)."""
    item = upload_to_onedrive(g, path)
    if conv.kind == "chat":
        members = list(g.paged(f"/chats/{quote(conv.chat_id)}/members"))
        me_id = g.me().get("id")
        emails = [m.get("email") for m in members if m.get("email") and m.get("userId") != me_id]
        if emails:
            g.post(f"/me/drive/items/{item['id']}/invite", {
                "recipients": [{"email": e} for e in emails], "requireSignIn": True,
                "sendInvitation": False, "roles": ["read"]})
        m = re.search(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}",
                      item.get("eTag") or "")
        att_id = (m.group(0) if m else str(uuid.uuid4())).lower()
        att = {"id": att_id, "contentType": "reference",
               "contentUrl": item.get("webDavUrl") or item.get("webUrl"), "name": item.get("name")}
    else:
        link = g.post(f"/me/drive/items/{item['id']}/createLink",
                      {"type": "view", "scope": "organization"})
        att_id = str(uuid.uuid4())
        att = {"id": att_id, "contentType": "reference",
               "contentUrl": (link.get("link") or {}).get("webUrl"), "name": item.get("name")}
    return f'<attachment id="{att_id}"></attachment>', att


def read_body(text: str | None, text_file: str | None) -> str:
    if text_file:
        with open(text_file, encoding="utf-8") as fh:
            file_text = fh.read().strip()
        return f"{text}\n\n{file_text}" if text else file_text
    return text or ""


# --------------------------------------------------------------------------
# Fetchers
# --------------------------------------------------------------------------

def fetch_chat_messages(g: Graph, chat_id: str, *, since=None, until=None, changed_since=None,
                        limit: int = 1000) -> tuple[list[dict], bool]:
    """Messages oldest-first. Returns (messages, truncated)."""
    url = f"/chats/{quote(chat_id)}/messages"
    if changed_since:
        params = {"$top": "50", "$orderby": "lastModifiedDateTime desc",
                  "$filter": f"lastModifiedDateTime gt {iso_z(changed_since)}"}
    else:
        params = {"$top": "50", "$orderby": "createdDateTime desc"}
        if until:
            params["$filter"] = f"createdDateTime lt {iso_z(until)}"
    out, truncated = [], False
    for msg in g.paged(url, params):
        created = parse_graph_dt(msg.get("createdDateTime"))
        if since and not changed_since and created and created < since:
            break
        modified = parse_graph_dt(msg.get("lastModifiedDateTime")) or created
        if changed_since and until and modified and modified >= until:
            continue
        if len(out) >= limit:
            truncated = True
            break
        out.append(msg)
    out.sort(key=lambda m: m.get("createdDateTime") or "")
    return out, truncated


def _in_range(msg: dict, since, until, changed: bool = False) -> bool:
    """created in [since, until) - or, for incremental reads, modified in it."""
    c = parse_graph_dt(msg.get("createdDateTime"))
    if changed:
        c = parse_graph_dt(msg.get("lastModifiedDateTime")) or c
    return bool(c) and (not since or c >= since) and (not until or c < until)


def fetch_replies(g: Graph, team_id: str, channel_id: str, post: dict) -> list[dict]:
    replies = list(post.get("replies") or [])
    nxt = post.get("replies@odata.nextLink")
    if nxt:
        page = g.request("GET", nxt, need=CHANNEL_READ)
        while True:
            replies.extend(page.get("value") or [])
            if not page.get("@odata.nextLink"):
                break
            page = g.request("GET", page["@odata.nextLink"], need=CHANNEL_READ)
    elif "replies" not in post:
        replies = list(g.paged(f"/teams/{team_id}/channels/{quote(channel_id)}/messages/"
                               f"{post['id']}/replies", {"$top": "50"}, need=CHANNEL_READ))
    replies.sort(key=lambda m: m.get("createdDateTime") or "")
    return replies


def fetch_channel_posts(g: Graph, team_id: str, channel_id: str, *, since=None, until=None,
                        limit: int = 300, replies: bool = True,
                        changed: bool = False) -> tuple[list[dict], bool]:
    """Channel posts (oldest-first), each with a complete `replies` list.

    Graph returns posts newest-first with no date filter. Display reads stop
    after two consecutive pages with nothing in range (a long-quiet thread
    that gets a fresh reply can be missed past that point). Incremental reads
    (changed=True: window on lastModifiedDateTime, so edits and reactions
    count) never stop early - they scan up to `limit` posts.
    """
    params = {"$top": "50"}
    if replies:
        params["$expand"] = "replies"
    url = f"/teams/{team_id}/channels/{quote(channel_id)}/messages"
    out, truncated, quiet_pages = [], False, 0
    page = g.request("GET", url, params=params, need=CHANNEL_READ)
    while True:
        hit = False
        for post in page.get("value") or []:
            reps = fetch_replies(g, team_id, channel_id, post) if replies else []
            post["replies"] = reps
            if (not since and not until) or _in_range(post, since, until, changed) or \
                    any(_in_range(r, since, until, changed) for r in reps):
                if len(out) >= limit:
                    truncated = True
                    break
                out.append(post)
                hit = True
        if truncated:
            break
        quiet_pages = 0 if hit else quiet_pages + 1
        nxt = page.get("@odata.nextLink")
        if not nxt or (since and not changed and quiet_pages >= 2):
            break
        page = g.request("GET", nxt, need=CHANNEL_READ)
    out.sort(key=lambda m: m.get("createdDateTime") or "")
    return out, truncated


def flatten_posts(posts: list[dict]) -> list[dict]:
    flat = []
    for p in posts:
        flat.append(p)
        flat.extend(p.get("replies") or [])
    return flat


# --------------------------------------------------------------------------
# Backends: Graph (default) and Playwright (local fallback)
# --------------------------------------------------------------------------

STATE = {"backend": "auto"}
PW_ONLY_TEXT = ("the Playwright backend sends plain text only (no @mentions, attachments, "
                "quote-replies, subject or importance) - use the Graph backend for those")


def pw_profile_dir() -> Path:
    return home_dir() / "pw-profile"


def pw_configured() -> bool:
    return bool(load_config().get("pw_signed_in_at")) and pw_profile_dir().is_dir()


def graph_configured() -> bool:
    auth = Auth()
    return bool(auth.static_token or (auth.client_id and auth.tenant))


def graph_ready() -> bool:
    try:
        Auth().token()
        return True
    except click.ClickException:
        return False


def backend() -> str:
    """Graph when a token works, else Playwright when its profile is set up."""
    choice = STATE["backend"]
    if choice in ("graph", "playwright"):
        return choice
    fallback = pw_configured()
    if graph_configured() and graph_ready():
        if not fallback:
            return "graph"
        try:  # a cached token can still be revoked: prove it before skipping the fallback
            Graph(Auth()).me()
            return "graph"
        except AuthError:
            click.echo("(Graph token rejected; falling back to the Playwright backend)", err=True)
    if fallback:
        return "playwright"
    if graph_configured():
        Auth().token()  # raises the specific Graph auth error
    raise AuthError("No working backend: Graph isn't signed in and the Playwright profile "
                    "isn't set up.", fix="uv run ms_teams.py login   or   uv run ms_teams.py "
                    "pw login", backend="auto")


def graph_only(command: str) -> None:
    if STATE["backend"] == "playwright":
        raise click.ClickException(f"`{command}` needs the Graph backend (not available via "
                                   "--backend playwright)")


def infer_me(chat_ids) -> str | None:
    """The user's own id is the one present in every 1:1 chat id (19:<a>_<b>@unq.gbl.spaces)."""
    common = None
    for cid in chat_ids:
        if not cid.endswith("@unq.gbl.spaces"):
            continue
        pair = set(cid.split(":", 1)[-1].split("@", 1)[0].split("_"))
        common = pair if common is None else common & pair
    return next(iter(common)) if common and len(common) == 1 else None


class WebGraph:
    """Stand-in for Graph on the Playwright backend: ids and config, no API calls."""

    def __init__(self, me_id: str | None = None):
        cfg = load_config()
        self.tenant_id = cfg.get("tenant_id")
        self._me = {"id": me_id or cfg.get("pw_me_id"), "displayName": cfg.get("pw_name"),
                    "mail": cfg.get("username")}

    def me(self) -> dict:
        return self._me

    def lookup_ok(self) -> bool:
        return False

    def get(self, *a, **k):
        raise click.ClickException("not available on the Playwright backend")


def web_session(headless: bool = True, timeout_s: int = 60):
    import pw_backend
    cfg = load_config()
    channel = os.getenv("MS_TEAMS_PW_CHANNEL", "msedge") or None
    return pw_backend.TeamsWeb(pw_profile_dir(), headless=headless, channel=channel,
                               user_agent=cfg.get("pw_user_agent"), timeout_s=timeout_s,
                               log=lambda m: click.echo(m, err=True))


class web_errors:
    """Map the browser backend's failures onto the CLI's exit-code contract."""

    def __enter__(self):
        return self

    def __exit__(self, etype, exc, tb):
        if exc is None:
            return False
        import pw_backend
        if isinstance(exc, pw_backend.SignedOut):
            raise AuthError(str(exc), fix="uv run ms_teams.py pw login", backend="playwright")
        if isinstance(exc, click.ClickException):
            return False
        if isinstance(exc, Exception):
            raise click.ClickException(f"Playwright backend: {exc}")
        return False


def save_web_images(web, dest_dir: str | None) -> dict[str, str]:
    """Write images the web app fetched (passively captured) into the file cache."""
    dest = cache_dir(dest_dir)
    sweep_files(dest)
    paths = {}
    for url, (data, ctype) in list(web.cap.images.items()):
        target = dest / f"T-{url_key(url)}{_ext_for(ctype)}"
        if not target.exists():
            dest.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        paths[url] = str(target)
    return paths


def web_chats(web) -> list[dict]:
    import pw_backend
    chats = [c for c in (pw_backend.to_graph_chat(r) for r in web.cap.conversations.values()) if c]
    chats.sort(key=lambda c: ((c.get("lastMessagePreview") or {}).get("createdDateTime") or ""),
               reverse=True)
    return chats


def web_conv(target: str) -> Conv:
    """Targets the browser backend can open without Graph: ids and links only."""
    t = parse_target(target)
    if t["kind"] == "chat":
        return Conv("chat", chat_id=t["chat_id"], message_id=t.get("message_id"), label=t["chat_id"])
    if t["kind"] == "channel":
        return Conv("channel", team_id=t.get("team_id"), channel_id=t["channel_id"],
                    message_id=t.get("message_id"), parent_id=t.get("parent_id"),
                    label=t["channel_id"])
    raise click.ClickException("The Playwright backend needs a chat/channel link or id "
                               "(run `chats` to list ids); names and emails need Graph.")


def web_conv_messages(web, conv: Conv, *, since=None, until=None, changed_since=None,
                      root: str | None = None) -> list[dict]:
    conv_id = conv.chat_id or conv.channel_id
    web.open_conversation(conv_id, conv.team_id, root)
    key = f"{conv_id};messageid={root}" if root else conv_id
    floor = changed_since or since
    web.load_older(key, iso_z(floor) if floor else None)
    out = []
    for m in web.cap.conv_messages(key):
        created = parse_graph_dt(m.get("createdDateTime"))
        modified = parse_graph_dt(m.get("lastModifiedDateTime")) or created
        if changed_since and (not modified or modified < changed_since):
            continue
        if not changed_since and since and (not created or created < since):
            continue
        edge = modified if changed_since else created
        if until and edge and edge >= until:
            continue
        out.append(m)
    out.sort(key=lambda m: m.get("createdDateTime") or "")
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def emit_json(obj, err: bool = False) -> None:
    click.echo(json.dumps(obj, ensure_ascii=False), err=err)


def output_text(text: str, output_path: str | None) -> None:
    if output_path:
        path = os.path.expanduser(output_path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        click.echo(f"Written to {path}", err=True)
    else:
        click.echo(text)


def client() -> tuple[Graph, Names]:
    return Graph(Auth()), Names()


def files_options(fn):
    fn = click.option("--files-dir", "files_dir",
                      help="Where to save files (default: %TEMP%\\teams-files, swept after 24h)")(fn)
    fn = click.option("--all-files", is_flag=True,
                      help="Also download non-image attachments (PDFs, docs, video)")(fn)
    fn = click.option("--files/--no-files", "want_files", default=True,
                      help="Download images so they can be Read (default on)")(fn)
    return fn


@click.group()
@click.option("--backend", "backend_opt", type=click.Choice(["auto", "graph", "playwright"]),
              default=None, envvar="MS_TEAMS_BACKEND",
              help="Force a backend (default auto: Graph if signed in, else Playwright)")
def cli(backend_opt):
    """Microsoft Teams CLI: read, search, send, edit and watch Teams as yourself.

    Start with `doctor` when anything fails: it shows which backend is live,
    which Graph scopes are granted, and whether the browser session is signed in.
    Exit codes: 0 ok, 1 error, 2 watch timeout, 3 auth_expired, 4 missing_scope.
    """
    STATE["backend"] = backend_opt or "auto"


# ---- auth / config --------------------------------------------------------

@cli.command()
@click.option("--client-id", required=True, help="App registration's Application (client) id")
@click.option("--tenant", required=True, help="Tenant id (GUID) or verified domain")
@click.option("--login-hint", help="Your account, e.g. you@company.com (pre-selects it at sign-in)")
def configure(client_id, tenant, login_hint):
    """Save the app registration this machine signs in with (no secrets; no network)."""
    cfg = load_config()
    if cfg.get("client_id") and cfg["client_id"] != client_id:
        click.echo("! client id changed: the old refresh token won't work with it; run `login`.",
                   err=True)
    cfg.update({"client_id": client_id, "tenant": tenant})
    if re.fullmatch(r"[0-9a-fA-F-]{36}", tenant):
        cfg["tenant_id"] = tenant.lower()
    if login_hint:
        cfg["login_hint"] = login_hint
    save_config(cfg)
    click.echo(f"Saved to {config_path()}\nNext: uv run ms_teams.py login")


@cli.command()
@click.option("--client-id", help="Override/remember the app registration's client id")
@click.option("--tenant", help="Override/remember the tenant id or domain")
@click.option("--login-hint", help="Account to pre-select, e.g. you@company.com")
@click.option("--device-code", is_flag=True,
              help="Device code flow instead of opening a browser (needs public client flows)")
@click.option("--scopes", help="Comma-separated scopes instead of .default (only if the admin "
                               "granted exactly these)")
def login(client_id, tenant, login_hint, device_code, scopes):
    """Sign in to Graph as yourself (delegated). Run it in a terminal you can see.

    Asks for `.default` - everything the admin has granted this app for you -
    so it never requests an unapproved scope. Afterwards it refreshes silently.
    """
    auth = Auth(client_id=client_id, tenant=tenant)
    if auth.static_token:
        raise click.ClickException("MS_TEAMS_ACCESS_TOKEN is set; unset it to use login")
    hint = login_hint or auth.cfg.get("login_hint") or auth.cfg.get("username")
    want = [s.strip() for s in scopes.split(",")] if scopes else [GRAPH_DEFAULT]
    click.echo(f"Requesting: {', '.join(want)} (+ offline_access, openid, profile)", err=True)
    result = auth.login(want, device_code=device_code, login_hint=hint)
    claims = result.get("id_token_claims") or {}
    granted = result.get("scope") or ""
    granted = granted.split() if isinstance(granted, str) else list(granted)
    cfg = load_config()
    cfg.update({"client_id": auth.client_id, "tenant": auth.tenant,
                "tenant_id": claims.get("tid") or cfg.get("tenant_id"),
                "username": claims.get("preferred_username"), "name": claims.get("name")})
    save_config(cfg)
    click.echo(f"Signed in as {claims.get('name')} <{claims.get('preferred_username')}>")
    click.echo(f"Granted: {' '.join(sorted(granted))}")
    click.echo(f"Token cache: {auth.cache_file} ({auth.cache_kind})")


@cli.command()
def logout():
    """Remove the Graph token cache (config and the browser profile are kept)."""
    auth = Auth()
    try:
        for acct in auth.app.get_accounts():
            auth.app.remove_account(acct)
    except click.ClickException:
        pass
    try:
        auth.cache_file.unlink()
    except OSError:
        pass
    click.echo("Signed out of Graph; token cache removed.")


@cli.group()
def token():
    """Inspect or export the cached Graph refresh token."""


@token.command("status")
@click.option("--json", "as_json", is_flag=True)
def token_status(as_json):
    """Is there a usable refresh token? Prints no secrets. Exit 3 if not."""
    auth = Auth()
    entry, ok, err, exp = None, True, None, None
    try:
        entry = auth.refresh_token_entry()
        exp = jwt_claims(auth.token()).get("exp")
    except click.ClickException as exc:
        ok, err = False, exc.message.splitlines()[0]
    info = {"client_id": auth.client_id, "tenant": auth.tenant,
            "tenant_id": auth.cfg.get("tenant_id"), "username": auth.cfg.get("username"),
            "cache_file": str(auth.cache_file), "cache_kind": auth.cache_kind,
            "refresh_token_cached": bool(entry), "silent_refresh_ok": ok,
            "access_token_expires": (datetime.fromtimestamp(exp, timezone.utc).isoformat()
                                     if exp else None), "error": err}
    if as_json:
        emit_json(info)
    else:
        for k, v in info.items():
            click.echo(f"  {k:22}: {v}")
    if not ok:
        emit_json({"error": "auth_expired", "backend": "graph", "detail": err,
                   "fix": "uv run ms_teams.py login"}, err=True)
        sys.exit(EXIT_AUTH_EXPIRED)


@token.command("export")
@click.option("--out", "out_path", type=click.Path(dir_okay=False),
              help="Write the export JSON to this file (owner-only permissions where supported)")
@click.option("--stdout", "to_stdout", is_flag=True,
              help="Print the export JSON to stdout (for piping straight into another tool)")
def token_export(out_path, to_stdout):
    """Export the Graph refresh token for another process to use as you.

    Refreshes first so the exported token is the newest. The JSON carries
    client_id, tenant, token endpoint, scopes and the refresh token. Treat it
    like a password. Refresh tokens rotate: whoever redeems it must keep the
    new refresh_token each redemption returns.
    """
    if bool(out_path) == bool(to_stdout):
        raise click.ClickException("Pick exactly one: --out PATH or --stdout")
    auth = Auth()
    if auth.static_token:
        raise click.ClickException("MS_TEAMS_ACCESS_TOKEN is set; there is no refresh token")
    tok = auth.token()
    entry = auth.refresh_token_entry()
    if not entry or not entry.get("secret"):
        raise AuthError("No refresh token in the cache.", fix="uv run ms_teams.py login")
    tenant_id = auth.cfg.get("tenant_id") or jwt_claims(tok).get("tid")
    payload = {
        "client_id": auth.client_id,
        "tenant": auth.tenant,
        "tenant_id": tenant_id,
        "token_endpoint": f"https://login.microsoftonline.com/{tenant_id or auth.tenant}"
                          "/oauth2/v2.0/token",
        "scope_to_request": f"{GRAPH_DEFAULT} offline_access",
        "username": auth.cfg.get("username"),
        "scopes": sorted(set(jwt_claims(tok).get("scp", "").split()) | {"offline_access"}),
        "refresh_token": entry["secret"],
        "exported_at": iso_z(datetime.now(timezone.utc)),
    }
    text = json.dumps(payload, indent=2)
    if to_stdout:
        click.echo(text)
        return
    path = Path(os.path.expanduser(out_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    click.echo(f"Refresh token exported to {path} (contents not shown)", err=True)


@cli.command()
@click.option("--json", "as_json", is_flag=True)
def whoami(as_json):
    """Who the CLI is signed in as."""
    if backend() == "playwright":
        cfg = load_config()
        info = {"id": cfg.get("pw_me_id"), "name": cfg.get("pw_name") or cfg.get("name"),
                "email": cfg.get("username") or cfg.get("login_hint"), "backend": "playwright"}
    else:
        g, _ = client()
        me = g.me()
        info = {"id": me.get("id"), "name": me.get("displayName"),
                "email": me.get("mail") or me.get("userPrincipalName"),
                "tenant_id": g.tenant_id, "backend": "graph"}
    if as_json:
        emit_json(info)
    else:
        click.echo(f"{info['name']} <{info['email']}> id={info['id']}  [{info['backend']}]")


DOCTOR_CAPS = [
    ("whoami", ["User.Read"]),
    ("list + read chats, chat images, watch chats", CAPS["chat_read"]),
    ("search messages", CAPS["search"]),
    ("read channel posts + threads, watch channels  [admin consent]", CAPS["channel_read"]),
    ("list teams", CAPS["teams_list"]),
    ("list channels", CAPS["channels_list"]),
    ("people: names, emails, @mentions, send to an email", CAPS["users"]),
    ("download files people attached", CAPS["files_read"]),
    ("send in chats", CAPS["chat_send"]),
    ("quote-reply in chats", CAPS["chat_quote"]),
    ("edit / delete own chat messages, open a new 1:1", CAPS["chat_write"]),
    ("post to channels / reply in channel threads", CAPS["channel_send"]),
    ("edit / delete own channel posts  [admin consent]", CAPS["channel_write"]),
    ("send attachments (OneDrive upload + share)", CAPS["files_write"]),
]


def _doctor_graph() -> bool:
    auth = Auth()
    click.echo("[Graph backend]")
    click.echo(f"  home dir    : {home_dir()}")
    click.echo(f"  client id   : {auth.client_id or 'NOT SET'}")
    click.echo(f"  tenant      : {auth.tenant or 'NOT SET'}")
    if not auth.static_token and not (auth.client_id and auth.tenant):
        click.echo("  -> not configured. Fix: uv run ms_teams.py configure --client-id <app id> "
                   "--tenant <tenant id>")
        return False
    if auth.static_token:
        click.echo("  token       : MS_TEAMS_ACCESS_TOKEN override (cache not used)")
    else:
        try:
            _ = auth.app
        except click.ClickException as exc:
            click.echo(f"  FAIL: {exc.message.splitlines()[0]}")
            return False
        click.echo(f"  token cache : {auth.cache_file} "
                   f"({'exists' if auth.cache_file.exists() else 'none yet'}; {auth.cache_kind})")
        acct = auth.account()
        if not acct:
            click.echo("  -> not signed in. Fix: uv run ms_teams.py login")
            return False
        click.echo(f"  account     : {acct.get('username')}")
    try:
        scp = auth.granted()
    except click.ClickException as exc:
        click.echo(f"  token refresh FAILED: {exc.message.splitlines()[0]}")
        return False
    click.echo(f"  granted     : {' '.join(sorted(scp)) or '(none readable)'}")
    missing = [s for s in READ_PROFILE if s not in scp]
    if missing:
        click.echo(f"  missing for full reading: {', '.join(missing)}")
    g = Graph(auth)
    try:
        me = g.me()
        click.echo(f"  OK  /me -> {me.get('displayName')} <{me.get('mail') or me.get('userPrincipalName')}>")
    except click.ClickException as exc:
        click.echo(f"  FAIL /me: {exc.message.splitlines()[0]}")
        return False
    if auth.can("chat_read"):
        try:
            n = len(list(g.paged("/me/chats", {"$top": "5"}, limit=5)))
            click.echo(f"  OK  chats readable ({n} recent)")
        except click.ClickException as exc:
            click.echo(f"  FAIL chats: {exc.message.splitlines()[0]}")
    click.echo("  Capabilities")
    for label, scopes in DOCTOR_CAPS:
        click.echo(f"    [{'x' if any(s in scp for s in scopes) else ' '}] {label}")
    return True


def _doctor_web(check_live: bool) -> bool:
    cfg = load_config()
    click.echo("\n[Playwright backend]  (local fallback; reads what the Teams web app loads)")
    click.echo(f"  profile     : {pw_profile_dir()} "
               f"({'exists' if pw_profile_dir().is_dir() else 'not created'})")
    if not cfg.get("pw_signed_in_at"):
        click.echo("  -> not set up. One-time sign-in: uv run ms_teams.py pw login")
        return False
    click.echo(f"  signed in at: {cfg.get('pw_signed_in_at')}  as {cfg.get('pw_name') or '?'}")
    if not check_live:
        click.echo("  (live session check skipped: --quick)")
        return True
    try:
        with web_errors(), web_session(timeout_s=60) as web:
            web.ensure_app()
            n = len(web_chats(web))
        click.echo(f"  OK  session is signed in; the app loaded {n} chat(s)")
        return True
    except AuthError as exc:
        click.echo(f"  SIGNED OUT: {exc.message.splitlines()[0]}  -> uv run ms_teams.py pw login")
    except click.ClickException as exc:
        click.echo(f"  FAIL: {exc.message.splitlines()[0]}")
    return False


@cli.command()
@click.option("--quick", is_flag=True, help="Skip launching the browser to test its session")
def doctor(quick):
    """Which backend is live, what Graph has granted, whether the browser session is signed in."""
    click.echo("Microsoft Teams CLI check\n")
    graph_ok = _doctor_graph() if STATE["backend"] != "playwright" else False
    web_ok = _doctor_web(not quick) if STATE["backend"] != "graph" else False
    live = ("graph" if graph_ok and STATE["backend"] != "playwright" else
            "playwright" if web_ok else "none")
    click.echo(f"\nLive backend: {live}"
               + ("" if STATE["backend"] == "auto" else f" (forced: {STATE['backend']})"))
    if live == "none":
        emit_json({"error": "auth_expired", "backend": STATE["backend"],
                   "detail": "no working backend",
                   "fix": "uv run ms_teams.py login  or  uv run ms_teams.py pw login"}, err=True)
        sys.exit(EXIT_AUTH_EXPIRED)


# ---- browser backend management -------------------------------------------

@cli.group()
def pw():
    """Playwright (browser) backend: one-time sign-in, status, diagnostics."""


@pw.command("login")
@click.option("--timeout", "-T", default=600, help="Seconds to wait for you to finish (default 600)")
def pw_login(timeout):
    """Open a visible browser on Teams; sign in once (tick 'Stay signed in')."""
    click.echo("A browser window will open on Teams. Sign in with your Teams account, finish "
               "MFA, choose 'Yes' on 'Stay signed in?', and wait until your chats appear. "
               "The window closes by itself once Teams has loaded.", err=True)
    import pw_backend
    with web_errors(), web_session(headless=False, timeout_s=timeout) as web:
        web.page.goto(pw_backend.TEAMS_WEB, wait_until="domcontentloaded")
        if not web.wait_for(lambda: bool(web.cap.conversations), timeout):
            raise click.ClickException("Timed out before Teams loaded your chats; run it again")
        web.page.wait_for_timeout(3000)
        ua = web.page.evaluate("navigator.userAgent")
        me_id = infer_me(web.cap.conversations.keys())
        name = None
        try:
            name = web.page.evaluate(
                "() => (document.querySelector('[data-tid=\"me-control-avatar\"], "
                "button[aria-label*=\"Profile\"]')||{}).ariaLabel || null")
        except Exception:
            pass
        n = len(web_chats(web))
    cfg = load_config()
    cfg.update({"pw_signed_in_at": iso_z(datetime.now(timezone.utc)),
                "pw_user_agent": (ua or "").replace("Headless", "") or None,
                "pw_me_id": me_id or cfg.get("pw_me_id"), "pw_name": name})
    save_config(cfg)
    click.echo(f"Signed in; Teams loaded {n} chat(s). Profile: {pw_profile_dir()}")


@pw.command("logout")
@click.option("--yes", is_flag=True, help="Required: deletes the browser profile")
def pw_logout(yes):
    """Delete the browser profile (signs the fallback backend out)."""
    if not yes:
        raise click.ClickException(f"This deletes {pw_profile_dir()}; re-run with --yes")
    import shutil
    shutil.rmtree(pw_profile_dir(), ignore_errors=True)
    cfg = load_config()
    for k in ("pw_signed_in_at", "pw_user_agent", "pw_me_id", "pw_name"):
        cfg.pop(k, None)
    save_config(cfg)
    click.echo("Browser profile removed.")


@pw.command("capture")
@click.argument("target", required=False)
@click.option("--headed", is_flag=True, help="Show the browser while capturing")
@click.option("--out", "out_path", type=click.Path(dir_okay=False),
              help="Where to write the dump (default: <home>/pw-capture-<time>.json)")
def pw_capture(target, headed, out_path):
    """Diagnostics: record what the web app fetched (credential-named keys scrubbed).

    Use after the first sign-in (or when Teams changes) to check the parsers
    against real payloads. Stays local; contains message text.
    """
    import pw_backend
    with web_errors(), web_session(headless=not headed) as web:
        web.ensure_app()
        if target:
            conv = web_conv(target)
            web.open_conversation(conv.chat_id or conv.channel_id, conv.team_id)
        cap = web.cap
        dump = {"captured_at": iso_z(datetime.now(timezone.utc)),
                "requests": cap.log[-300:],
                "conversations_sample": pw_backend.scrub(list(cap.conversations.values())[:5]),
                "messages_sample": {k: pw_backend.scrub(list(v.values())[:5])
                                    for k, v in list(cap.messages.items())[:5]},
                "teams": cap.teams,
                "normalized_chats": web_chats(web)[:5],
                "image_urls": list(cap.images)[:20]}
    path = Path(out_path) if out_path else home_dir() / f"pw-capture-{int(time.time())}.json"
    path.write_text(json.dumps(dump, indent=1, ensure_ascii=False), encoding="utf-8")
    click.echo(f"Wrote {path} ({len(cap.log)} requests seen, {len(cap.conversations)} "
               f"conversations, {sum(len(v) for v in cap.messages.values())} messages)")


# ---- listing --------------------------------------------------------------

@cli.command()
@click.option("--since", help="Only chats with activity since (30d, 12h, YYYY-MM-DD, ISO)")
@click.option("--limit", "-n", default=50, help="Max chats (default 50)")
@click.option("--type", "chat_type", type=click.Choice(["oneOnOne", "group", "meeting"]))
@click.option("--json", "as_json", is_flag=True, help="One chat record per line")
def chats(since, limit, chat_type, as_json):
    """List your chats, most recent first (id, type, topic, members, last activity)."""
    since_dt = parse_when(since)
    if backend() == "playwright":
        with web_errors(), web_session() as web:
            web.ensure_app()
            rows = web_chats(web)
        g, names, bk = WebGraph(infer_me(c["id"] for c in rows)), Names(), "playwright"
    else:
        g, names = client()
        g.require("chat_read")
        rows, bk = list_chats(g, limit=max(limit * 3, 100)), "graph"
    shown = 0
    for chat in rows:
        last = parse_graph_dt((chat.get("lastMessagePreview") or {}).get("createdDateTime"))
        if since_dt and not last:
            continue
        if since_dt and last < since_dt:
            break  # newest first
        if chat_type and chat.get("chatType") != chat_type:
            continue
        rec = chat_record(g, names, chat, bk)
        if as_json:
            emit_json(rec)
        else:
            lm = rec["last_message"] or {}
            snippet = re.sub(r"\s+", " ", lm.get("text") or "")[:90]
            click.echo(f"{fmt_local(last) if last else '-'}  [{rec['type']}] {rec['name']}\n"
                       f"    {chat['id']}" + (f"\n    {lm.get('from')}: {snippet}" if lm else ""))
        shown += 1
        if shown >= limit:
            break
    names.save()


@cli.command()
@click.option("--json", "as_json", is_flag=True)
def teams(as_json):
    """List the teams you belong to."""
    if backend() == "playwright":
        with web_errors(), web_session() as web:
            web.ensure_app()
            web.wait_for(lambda: bool(web.cap.teams), 15)
            rows = web.cap.teams
        if not rows:
            raise click.ClickException("The web app didn't load a teams list (try `pw capture`)")
    else:
        g, _ = client()
        g.require("teams_list")
        rows = joined_teams(g)
    for t in rows:
        if as_json:
            emit_json({"record": "team", "team_id": t["id"], "name": t.get("displayName"),
                       "description": t.get("description")})
        else:
            click.echo(f"{t.get('displayName')}\n    {t['id']}")


@cli.command()
@click.argument("team")
@click.option("--json", "as_json", is_flag=True)
def channels(team, as_json):
    """List channels in TEAM (name or id)."""
    names = Names()
    if backend() == "playwright":
        with web_errors(), web_session() as web:
            web.ensure_app()
            web.wait_for(lambda: bool(web.cap.teams), 15)
            all_teams = web.cap.teams
        lookup = lambda t: t.get("channels") or []  # noqa: E731
    else:
        g, _ = client()
        g.require("teams_list")
        g.require("channels_list")
        all_teams = joined_teams(g)
        lookup = lambda t: team_channels(g, t["id"])  # noqa: E731
    match = [t for t in all_teams if t["id"] == team or
             (t.get("displayName") or "").lower() == team.lower()]
    if not match:
        raise click.ClickException(f"No joined team {team!r}. Teams: "
                                   + ", ".join(t.get("displayName", "?") for t in all_teams))
    t = match[0]
    for c in lookup(t):
        names.data["channels"][c["id"]] = {"team_id": t["id"], "team_name": t.get("displayName"),
                                           "channel_name": c.get("displayName")}
        names.dirty = True
        if as_json:
            emit_json({"record": "channel", "team_id": t["id"], "channel_id": c["id"],
                       "name": c.get("displayName"), "membership": c.get("membershipType"),
                       "web_url": c.get("webUrl")})
        else:
            click.echo(f"{c.get('displayName')}  [{c.get('membershipType')}]\n    {c['id']}")
    names.save()


@cli.command()
@click.argument("query")
@click.option("--json", "as_json", is_flag=True)
def user(query, as_json):
    """Look up a person (email, or the start of a name) - for @mentions and targets."""
    graph_only("user")
    g, _ = client()
    g.require("users")
    if "@" in query:
        users = [find_user(g, query)]
    else:
        safe = query.replace("'", "''")
        users = list(g.paged("/users", {
            "$filter": f"startswith(displayName,'{safe}') or startswith(givenName,'{safe}') "
                       f"or startswith(surname,'{safe}')",
            "$select": "id,displayName,mail,userPrincipalName,jobTitle", "$top": "15"}, limit=15))
    if not users:
        raise click.ClickException(f"No users match {query!r}")
    for u in users:
        email = u.get("mail") or u.get("userPrincipalName")
        if as_json:
            emit_json({"record": "user", "id": u.get("id"), "name": u.get("displayName"),
                       "email": email, "title": u.get("jobTitle")})
        else:
            click.echo(f"{u.get('displayName')} <{email}>  id={u.get('id')}"
                       + (f"  ({u['jobTitle']})" if u.get("jobTitle") else ""))
            click.echo(f"    mention: @[{u.get('displayName')}]({email})")


# ---- reading ----------------------------------------------------------------

def _print_conversation(g, names, conv, messages, as_json, output_path, file_paths, header=None,
                        threaded=False, raw_html=False, bk="graph"):
    if as_json:
        lines = [json.dumps(message_record(g, names, m, conv, file_paths, raw_html, bk),
                            ensure_ascii=False)
                 for m in (flatten_posts(messages) if threaded else messages)]
        output_text("\n".join(lines), output_path)
        return
    out = [header] if header else []
    for m in messages:
        out.extend(render_message(g, names, m, file_paths))
        if threaded:
            for r in m.get("replies") or []:
                out.extend(render_message(g, names, r, file_paths, indent=True))
    output_text("\n".join(out).rstrip() + "\n", output_path)


def _read_one(target, since_dt, until_dt, changed_dt, limit, replies, want_files, all_files,
              files_dir):
    """Messages of one conversation on whichever backend is live.

    Returns (g, names, conv, messages, threaded, file_paths, backend)."""
    if backend() == "playwright":
        conv = web_conv(target)
        with web_errors(), web_session() as web:
            web.ensure_app()
            msgs = web_conv_messages(web, conv, since=since_dt, until=until_dt,
                                     changed_since=changed_dt)
            paths = save_web_images(web, files_dir) if want_files else {}
            chat = next((c for c in web_chats(web) if c["id"] == conv.chat_id), None)
        names = Names()
        if chat:
            names.learn_members(chat.get("members"))
            conv.label = chat_label(chat, load_config().get("pw_me_id"))
        if len(msgs) > limit:
            click.echo(f"! stopped at --limit {limit}; older messages not shown", err=True)
            msgs = msgs[-limit:]
        return WebGraph(), names, conv, msgs, False, paths, "playwright"
    g, names = client()
    conv = resolve_conv(g, names, target)
    if conv.kind == "chat":
        g.require("chat_read")
        msgs, truncated = fetch_chat_messages(g, conv.chat_id, since=since_dt, until=until_dt,
                                              changed_since=changed_dt, limit=limit)
        threaded = False
    else:
        g.require("channel_read")
        msgs, truncated = fetch_channel_posts(g, conv.team_id, conv.channel_id,
                                              since=changed_dt or since_dt, until=until_dt,
                                              limit=limit, replies=replies,
                                              changed=bool(changed_dt))
        threaded = True
    if truncated:
        click.echo(f"! stopped at --limit {limit}; older messages not shown", err=True)
    paths = (fetch_message_files(g, flatten_posts(msgs) if threaded else msgs, conv, files_dir,
                                 images_only=not all_files) if want_files else {})
    return g, names, conv, msgs, threaded, paths, "graph"


@cli.command()
@click.argument("target")
@click.option("--since", help="From (YYYY-MM-DD, ISO, or 30d/12h/2w); default 30d")
@click.option("--until", "until_", help="Up to (exclusive)")
@click.option("--changed-since", help="Every message created OR edited/reacted-to since "
                                      "(incremental sync; overrides --since)")
@click.option("--limit", "-n", default=1000, help="Max messages / channel posts (default 1000)")
@click.option("--replies/--no-replies", default=True, help="Channels: include thread replies")
@click.option("--raw-html", is_flag=True, help="JSON: include each message's raw HTML body")
@click.option("--json", "as_json", is_flag=True, help="One message record per line")
@click.option("--output", "-o", "output_path", help="Write to a file instead of stdout")
@files_options
def history(target, since, until_, changed_since, limit, replies, raw_html, as_json, output_path,
            want_files, all_files, files_dir):
    """Messages in a chat or channel (replies included for channels).

    TARGET: chat/channel link, chat id (19:...), channel id, an email (your
    1:1 with them), "Team/Channel", or part of a chat name. (The Playwright
    backend takes links and ids only.)
    """
    since_dt = parse_when(since or ("30d" if not changed_since else None))
    g, names, conv, msgs, threaded, paths, bk = _read_one(
        target, since_dt, parse_when(until_, end_of_day=True), parse_when(changed_since), limit,
        replies, want_files, all_files, files_dir)
    header = f"# {conv.label or target} ({len(msgs)} {'posts' if threaded else 'messages'})\n"
    _print_conversation(g, names, conv, msgs, as_json, output_path, paths, header, threaded,
                        raw_html, bk)
    names.save()


@cli.command()
@click.argument("message")
@click.option("--chat", help="Chat id when MESSAGE is a bare id")
@click.option("--team", help="Team id when MESSAGE is a bare channel message id")
@click.option("--channel", help="Channel id when MESSAGE is a bare channel message id")
@click.option("--context", "context_n", default=8,
              help="Chats: messages to show before and after (chats have no threads)")
@click.option("--raw-html", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--output", "-o", "output_path")
@files_options
def thread(message, chat, team, channel, context_n, raw_html, as_json, output_path, want_files,
           all_files, files_dir):
    """A channel post with all its replies (from any message link in the thread).

    For a chat message link, shows the message with surrounding context and
    marks quote-replies that reference it.
    """
    ref = parse_message_ref(message, chat=chat, team=team, channel=channel)
    if backend() == "playwright":
        conv = web_conv(message) if message.startswith("http") else (
            Conv("chat", chat_id=chat) if chat else Conv("channel", team_id=team, channel_id=channel))
        root = (ref.get("parent_id") or ref["message_id"]) if ref["kind"] == "channel" else None
        with web_errors(), web_session() as web:
            web.ensure_app()
            msgs = web_conv_messages(web, conv, root=root)
            paths = save_web_images(web, files_dir) if want_files else {}
        if root:
            post = next((m for m in msgs if m["id"] == root), {"id": root, "replies": []})
            post["replies"] = [m for m in msgs if m["id"] != root]
            msgs, threaded = [post], True
        else:
            i = next((n for n, m in enumerate(msgs) if m["id"] == ref["message_id"]), len(msgs))
            msgs, threaded = msgs[max(0, i - context_n): i + context_n + 1], False
        _print_conversation(WebGraph(), Names(), conv, msgs, as_json, output_path, paths,
                            f"# {conv.label}\n", threaded, raw_html, "playwright")
        return
    g, names = client()
    if ref["kind"] == "channel":
        g.require("channel_read")
        info = channel_info(g, names, ref["channel_id"], ref.get("team_id"))
        conv = Conv("channel", team_id=info["team_id"], channel_id=ref["channel_id"],
                    label=f"{info.get('team_name')} / {info.get('channel_name')}")
        root = ref.get("parent_id") or ref["message_id"]
        post = g.get(f"/teams/{conv.team_id}/channels/{quote(conv.channel_id)}/messages/{root}",
                     need=CHANNEL_READ)
        if post.get("replyToId"):  # link was to a reply without parentMessageId
            post = g.get(f"/teams/{conv.team_id}/channels/{quote(conv.channel_id)}/messages/"
                         f"{post['replyToId']}", need=CHANNEL_READ)
        post["replies"] = fetch_replies(g, conv.team_id, conv.channel_id, post)
        paths = (fetch_message_files(g, flatten_posts([post]), conv, files_dir,
                                     images_only=not all_files) if want_files else {})
        _print_conversation(g, names, conv, [post], as_json, output_path, paths,
                            f"# Thread in {conv.label} ({len(post['replies'])} replies)\n", True,
                            raw_html)
    else:
        g.require("chat_read")
        conv = resolve_conv(g, names, ref["chat_id"])
        target = g.get(f"/chats/{quote(conv.chat_id)}/messages/{ref['message_id']}")
        t_created = parse_graph_dt(target.get("createdDateTime"))
        before, _ = fetch_chat_messages(g, conv.chat_id, until=t_created, limit=context_n)
        after_all, _ = fetch_chat_messages(g, conv.chat_id, changed_since=t_created, limit=500)
        after = [m for m in after_all if (parse_graph_dt(m.get("createdDateTime")) or t_created)
                 > t_created and m.get("id") != target.get("id")][:context_n]
        msgs = before + [target] + after
        paths = (fetch_message_files(g, msgs, conv, files_dir, images_only=not all_files)
                 if want_files else {})
        _print_conversation(g, names, conv, msgs, as_json, output_path, paths,
                            f"# {conv.label}: message {ref['message_id']} with context\n",
                            raw_html=raw_html)
    names.save()


def _all_chats_graph(since_dt, until_dt, with_channels, max_chats, chat_records, raw_html,
                     want_files, all_files, files_dir, emit):
    g, names = client()
    g.require("chat_read")
    me_id = g.me().get("id")
    total = 0
    for chat in list_chats(g, limit=max_chats):
        last = parse_graph_dt((chat.get("lastMessagePreview") or {}).get("createdDateTime"))
        if not last:
            continue
        if last < since_dt:
            break
        names.learn_members(chat.get("members"))
        conv = Conv("chat", chat_id=chat["id"], label=chat_label(chat, me_id))
        msgs, _ = fetch_chat_messages(g, chat["id"], changed_since=since_dt, until=until_dt,
                                      limit=5000)
        if not msgs:
            continue
        total += len(msgs)
        paths = (fetch_message_files(g, msgs, conv, files_dir, images_only=not all_files)
                 if want_files else {})
        emit(g, names, conv, msgs, paths, chat_record(g, names, chat) if chat_records else None,
             f"# {conv.label}  [{chat.get('chatType')}] {chat['id']}\n", False)
    if with_channels:
        if not g.auth.can("channel_read") or not g.auth.can("teams_list"):
            click.echo(f"! skipping channels: needs {CHANNEL_READ} + Team.ReadBasic.All + "
                       "Channel.ReadBasic.All", err=True)
        else:
            for t in joined_teams(g):
                for c in team_channels(g, t["id"]):
                    conv = Conv("channel", team_id=t["id"], channel_id=c["id"],
                                label=f"{t.get('displayName')} / {c.get('displayName')}")
                    posts, _ = fetch_channel_posts(g, t["id"], c["id"], since=since_dt,
                                                   until=until_dt, limit=500, changed=True)
                    if not posts:
                        continue
                    flat = flatten_posts(posts)
                    total += len(flat)
                    paths = (fetch_message_files(g, flat, conv, files_dir,
                                                 images_only=not all_files) if want_files else {})
                    emit(g, names, conv, posts, paths, None, f"# {conv.label}\n", True)
    names.save()
    return total


def _all_chats_web(since_dt, until_dt, max_chats, chat_records, want_files, files_dir, emit):
    total = 0
    with web_errors(), web_session() as web:
        web.ensure_app()
        rows = web_chats(web)
        g, names = WebGraph(infer_me(c["id"] for c in rows)), Names()
        for chat in rows[:max_chats]:
            last = parse_graph_dt((chat.get("lastMessagePreview") or {}).get("createdDateTime"))
            if not last:
                continue
            if last < since_dt:
                break
            names.learn_members(chat.get("members"))
            conv = Conv("chat", chat_id=chat["id"], label=chat_label(chat, g.me().get("id")))
            msgs = web_conv_messages(web, conv, changed_since=since_dt, until=until_dt)
            if not msgs:
                continue
            total += len(msgs)
            paths = save_web_images(web, files_dir) if want_files else {}
            emit(g, names, conv, msgs, paths,
                 chat_record(g, names, chat, "playwright") if chat_records else None,
                 f"# {conv.label}  [{chat.get('chatType')}] {chat['id']}\n", False)
    names.save()
    return total


@cli.command()
@click.argument("target", required=False)
@click.option("--all-chats", is_flag=True, help="Every chat with activity in the window")
@click.option("--channels", "with_channels", is_flag=True,
              help=f"With --all-chats: also every joined channel (Graph; needs {CHANNEL_READ})")
@click.option("--since", help="Window start: ISO, YYYY-MM-DD, or 24h/7d (default 24h with "
                              "--all-chats, 30d with TARGET)")
@click.option("--until", "until_", help="Window end (exclusive)")
@click.option("--changed-since", help="TARGET mode: created OR edited/reacted-to since")
@click.option("--limit", "-n", default=5000, help="TARGET mode: max messages (default 5000)")
@click.option("--max-chats", default=200, help="--all-chats: most chats to scan (default 200)")
@click.option("--chat-records/--no-chat-records", default=True,
              help="--all-chats --json: emit a chat record before each chat's messages")
@click.option("--raw-html", is_flag=True, help="Include each message's raw HTML body")
@click.option("--json", "as_json", is_flag=True, help="JSON lines (the output contract)")
@click.option("--output", "-o", "output_path")
@files_options
def messages(target, all_chats, with_channels, since, until_, changed_since, limit, max_chats,
             chat_records, raw_html, as_json, output_path, want_files, all_files, files_dir):
    """The ingestion read: one conversation, or every chat changed since a time.

    \b
    messages --all-chats --since 2026-10-01T00:00:00Z --json
        Chats whose last message is in the window; within each, every message
        created, edited, deleted or reacted-to in the window. Emits a chat
        record, then that chat's message records. Upsert on
        (conversation_id, id).
    messages <chat link or id> --since 7d --json
        Messages created in the window (--changed-since for edits/reactions too).
    """
    if bool(target) == bool(all_chats):
        raise click.ClickException("Pass a TARGET or --all-chats (not both)")
    until_dt = parse_when(until_, end_of_day=True)
    if target:
        since_dt = parse_when(since or ("30d" if not changed_since else None))
        g, names, conv, msgs, threaded, paths, bk = _read_one(
            target, since_dt, until_dt, parse_when(changed_since), limit, True, want_files,
            all_files, files_dir)
        _print_conversation(g, names, conv, msgs, as_json, output_path, paths,
                            f"# {conv.label or target} ({len(msgs)})\n", threaded, raw_html, bk)
        names.save()
        return
    since_dt = parse_when(since or "24h")
    out: list[str] = []
    bk = backend()

    def emit(g, names, conv, msgs, paths, chat_rec, header, threaded):
        if as_json:
            if chat_rec:
                out.append(json.dumps(chat_rec, ensure_ascii=False))
            out.extend(json.dumps(message_record(g, names, m, conv, paths, raw_html, bk),
                                  ensure_ascii=False)
                       for m in (flatten_posts(msgs) if threaded else msgs))
        else:
            out.append(header)
            for m in msgs:
                out.extend(render_message(g, names, m, paths))
                for r in (m.get("replies") or []) if threaded else []:
                    out.extend(render_message(g, names, r, paths, indent=True))

    if bk == "playwright":
        if with_channels:
            click.echo("! --channels isn't supported on the Playwright backend; chats only", err=True)
        total = _all_chats_web(since_dt, until_dt, max_chats, chat_records, want_files,
                               files_dir, emit)
    else:
        total = _all_chats_graph(since_dt, until_dt, with_channels, max_chats, chat_records,
                                 raw_html, want_files, all_files, files_dir, emit)
    click.echo(f"{total} message(s) changed since {fmt_local(since_dt)} [{bk}]", err=True)
    output_text("\n".join(out), output_path)


@cli.command(hidden=True)
@click.option("--since", default="24h")
@click.option("--channels", "with_channels", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--output", "-o", "output_path")
@click.pass_context
def recent(ctx, since, with_channels, as_json, output_path):
    """Alias: messages --all-chats."""
    ctx.invoke(messages, all_chats=True, since=since, with_channels=with_channels,
               as_json=as_json, output_path=output_path)


@cli.command()
@click.argument("query")
@click.option("--limit", "-n", default=25, help="Max results (default 25)")
@click.option("--json", "as_json", is_flag=True)
def search(query, limit, as_json):
    """Search your Teams messages (chats, and channels you're in) - Microsoft Search (Graph)."""
    graph_only("search")
    g, names = client()
    g.require("search")
    got, start = 0, 0
    while got < limit:
        size = min(25, limit - got)
        res = g.post("/search/query", {"requests": [{
            "entityTypes": ["chatMessage"], "query": {"queryString": query},
            "from": start, "size": size}]})
        containers = ((res.get("value") or [{}])[0].get("hitsContainers") or [{}])
        hits = containers[0].get("hits") or []
        for h in hits:
            r = h.get("resource") or {}
            ch = r.get("channelIdentity") or {}
            sender_ea = (r.get("from") or {}).get("emailAddress") or {}
            sender_name = sender_ea.get("name") or "?"
            summary = re.sub(r"</?c\d+>", "", h.get("summary") or "")
            summary = _html.unescape(re.sub(r"<[^>]+>", "", summary)).strip()
            if ch.get("channelId"):
                link = channel_permalink(ch.get("teamId"), ch["channelId"], r.get("id"),
                                         tenant_id=g.tenant_id)
                where = {"kind": "channel", "team_id": ch.get("teamId"),
                         "channel_id": ch["channelId"], "conversation_id": ch["channelId"]}
            else:
                link = chat_permalink(r.get("chatId"), r.get("id"), g.tenant_id) if r.get("chatId") else None
                where = {"kind": "chat", "chat_id": r.get("chatId"), "conversation_id": r.get("chatId")}
            if as_json:
                emit_json({"record": "search_hit", **where, "id": r.get("id"),
                           "created": r.get("createdDateTime"),
                           "from": {"name": sender_name, "email": sender_ea.get("address")},
                           "summary": summary, "permalink": link})
            else:
                click.echo(f"{fmt_local(r.get('createdDateTime'))}  {sender_name}: {summary[:200]}\n"
                           f"    {link}")
        got += len(hits)
        start += len(hits)
        if not hits or not containers[0].get("moreResultsAvailable"):
            break
    if got == 0:
        click.echo("No matches.", err=True)


@cli.command("download")
@click.argument("targets", nargs=-1, required=True)
@click.option("--files-dir", "files_dir", help="Where to save (default: %TEMP%\\teams-files)")
@click.option("--json", "as_json", is_flag=True, help="Emit {url: local path}")
def download_cmd(targets, files_dir, as_json):
    """Save a message's images and attached files locally (then Read the paths).

    TARGETS: message links, Graph hostedContents URLs, or SharePoint/OneDrive
    file URLs. (Graph backend; on the Playwright backend, images a read
    displays are saved automatically.)
    """
    graph_only("download")
    g, names = client()
    results: dict[str, str] = {}
    for t in targets:
        if t.startswith("https://graph.microsoft.com/") and "/hostedContents/" in t:
            need = CHANNEL_READ if "/channels/" in t else None
            p = download_hosted(g, t, cache_dir(files_dir), need)
            if p:
                results[t] = p
            continue
        host = (urlparse(t).hostname or "").lower()
        if host.endswith("sharepoint.com") or host.endswith("1drv.ms"):
            g.require("files_read")
            p = download_reference(g, t, cache_dir(files_dir))
            if p:
                results[t] = p
            continue
        ref = parse_message_ref(t)
        if ref["kind"] == "chat":
            conv = Conv("chat", chat_id=ref["chat_id"])
            msg = g.get(f"/chats/{quote(ref['chat_id'])}/messages/{ref['message_id']}")
        else:
            info = channel_info(g, names, ref["channel_id"], ref.get("team_id"))
            conv = Conv("channel", team_id=info["team_id"], channel_id=ref["channel_id"])
            base = f"/teams/{conv.team_id}/channels/{quote(conv.channel_id)}/messages"
            path = (f"{base}/{ref['parent_id']}/replies/{ref['message_id']}" if ref.get("parent_id")
                    else f"{base}/{ref['message_id']}")
            msg = g.get(path, need=CHANNEL_READ)
        results.update(fetch_message_files(g, [msg], conv, files_dir, images_only=False))
    if as_json:
        emit_json(results)
    elif not results:
        click.echo("Nothing downloaded (no images or attachments found).", err=True)
    else:
        for p in results.values():
            click.echo(p)
    names.save()


# ---- writing ----------------------------------------------------------------

def _resolve_reply(g: Graph, names: Names, conv: Conv, reply_to: str | None) -> str | None:
    """Return the message id to reply to (channel: the ROOT post; chat: quoted msg)."""
    if not reply_to:
        return None
    if re.fullmatch(r"\d{10,}", reply_to.strip()):
        mid = reply_to.strip()
        if conv.kind == "channel" and g is not None and g.auth.can("channel_read"):
            msg = g.get(f"/teams/{conv.team_id}/channels/{quote(conv.channel_id)}/messages/{mid}",
                        need=CHANNEL_READ)
            return msg.get("replyToId") or mid
        return mid
    ref = parse_message_ref(reply_to)
    if ref["kind"] != conv.kind or (ref.get("chat_id") or ref.get("channel_id")) != \
            (conv.chat_id or conv.channel_id):
        raise click.ClickException("--reply-to points at a different conversation than TARGET")
    return (ref.get("parent_id") or ref["message_id"]) if conv.kind == "channel" else ref["message_id"]


def _send_web(target, body_src, reply_to, new_post, fmt, attach, subject, importance, dry_run,
              as_json):
    if attach or subject or importance or MENTION_RE.search(body_src or "") or fmt == "html":
        raise click.ClickException(PW_ONLY_TEXT)
    conv = web_conv(target)
    root = None
    if conv.kind == "channel":
        if reply_to:
            root = _resolve_reply(None, Names(), conv, reply_to)
        elif conv.message_id and not new_post:
            root = conv.parent_id or conv.message_id
    elif reply_to:
        raise click.ClickException("Quote-replies in chats need the Graph backend")
    plan = {"dry_run": dry_run, "backend": "playwright", **conv.ctx(), "reply_to": root,
            "text": body_src}
    if dry_run:
        if as_json:
            emit_json(plan)
        else:
            where = f"thread {root}" if root else "top level"
            click.echo(f"[dry-run] playwright: type into {conv.chat_id or conv.channel_id} ({where})"
                       f"\n--- text (sent as typed) ---\n{body_src}")
        return
    with web_errors(), web_session() as web:
        web.ensure_app()
        res = web.send_text(conv.chat_id or conv.channel_id, body_src, team_id=conv.team_id,
                            root=root)
    tenant = load_config().get("tenant_id")
    mid = res.get("id")
    link = None
    if mid:
        link = (chat_permalink(conv.chat_id, mid, tenant) if conv.kind == "chat" else
                channel_permalink(conv.team_id, conv.channel_id, mid, root, tenant))
    if as_json:
        emit_json({"ok": True, "backend": "playwright", **conv.ctx(), "id": mid,
                   "reply_to": root, "permalink": link})
    else:
        click.echo(f"Sent via Teams web (id {mid or 'not reported'})" + (f"\n{link}" if link else ""))


@cli.command()
@click.argument("target")
@click.option("--text", "-m", help="Message text (Markdown by default)")
@click.option("--file", "-f", "text_file", type=click.Path(exists=True, dir_okay=False),
              help="Read the message from a file (best for anything multi-line)")
@click.option("--reply-to", "reply_to",
              help="Channel: reply in this post's thread. Chat: quote-reply this message. "
                   "Message link or id.")
@click.option("--new-post", is_flag=True,
              help="TARGET is a channel message link but you want a NEW top-level post")
@click.option("--format", "fmt", type=click.Choice(["markdown", "html", "text"]),
              default="markdown", show_default=True)
@click.option("--attach", "-a", multiple=True, type=click.Path(exists=True, dir_okay=False),
              help="Attach a file (repeatable). Images go inline; others via your OneDrive.")
@click.option("--images-as-files", is_flag=True, help="Send images as file attachments, not inline")
@click.option("--subject", help="Channel posts only: a subject line")
@click.option("--importance", type=click.Choice(["normal", "high", "urgent"]), default=None)
@click.option("--dry-run", is_flag=True, help="Resolve and render everything; send nothing")
@click.option("--json", "as_json", is_flag=True)
def send(target, text, text_file, reply_to, new_post, fmt, attach, images_as_files, subject,
         importance, dry_run, as_json):
    """Send a message AS YOU to a chat, person, or channel.

    \b
    Examples:
        send someone@contoso.com -f draft.md              # 1:1 (opens it if needed)
        send "<channel message link>" -f reply.md         # replies in that thread
        send "Team/General" -m "New post" --subject "Heads up"
        send "<chat link>" --reply-to "<message link>" -m "on it"   # quote-reply
        send "<chat link>" -m "screenshot attached" -a shot.png
    """
    body_src = read_body(text, text_file)
    if not body_src and not attach:
        raise click.ClickException("Nothing to send: pass -m, -f, or -a")
    if backend() == "playwright":
        return _send_web(target, body_src, reply_to, new_post, fmt, attach, subject, importance,
                         dry_run, as_json)
    g, names = client()
    conv = resolve_conv(g, names, target, for_send=True)

    if not reply_to and conv.kind == "channel" and conv.message_id and not new_post:
        reply_to = target  # a channel message link means "answer in that thread"
        click.echo("(TARGET is a channel message link: replying in its thread; "
                   "--new-post to post top-level)", err=True)
    reply_id = _resolve_reply(g, names, conv, reply_to)
    if subject and (conv.kind != "channel" or reply_id):
        raise click.ClickException("--subject only applies to a new top-level channel post")

    html, specs = compose_html(body_src, fmt, conv.kind)
    images = [Path(a) for a in attach if Path(a).suffix.lower() in IMAGE_EXTS
              and not images_as_files and Path(a).stat().st_size <= INLINE_IMAGE_MAX]
    files = [Path(a) for a in attach if Path(a) not in images]
    needed = (["channel_send"] if conv.kind == "channel" else
              ["chat_quote"] if reply_id else ["chat_send"])
    if conv.create_with:
        needed.append("chat_create")
    if specs:
        needed.append("users")
    if files:
        needed.append("files_write")
    missing = [c for c in needed if not g.auth.can(c)]
    mentions = build_mentions(g, specs) if "users" not in missing else []
    if len(html) > BODY_SOFT_LIMIT:
        click.echo(f"! body is {len(html)} chars; Teams rejects ~28 KB - consider an attachment",
                   err=True)
    if conv.kind == "chat":
        endpoint = (f"POST /chats/{{chat}}/messages/replyWithQuote (quoting {reply_id})"
                    if reply_id else "POST /chats/{chat}/messages")
    else:
        endpoint = (f"POST /teams/{{team}}/channels/{{channel}}/messages/{reply_id}/replies"
                    if reply_id else "POST /teams/{team}/channels/{channel}/messages (top-level)")

    if dry_run:
        plan = {"dry_run": True, "backend": "graph", **conv.ctx(), "endpoint": endpoint,
                "reply_to": reply_id,
                "create_chat_with": (conv.create_with or {}).get("mail") if conv.create_with else None,
                "mentions": [{"text": m["mentionText"], "user_id": m["mentioned"]["user"]["id"]}
                             for m in mentions],
                "inline_images": [str(p) for p in images], "files": [str(p) for p in files],
                "missing_permissions": sorted({s for c in missing for s in CAPS[c]}),
                "html": html}
        if as_json:
            emit_json(plan)
        else:
            click.echo(f"[dry-run] to: {conv.label}  ({conv.kind})")
            if conv.create_with:
                click.echo(f"[dry-run] no 1:1 chat yet with {conv.label}: would create it")
            click.echo(f"[dry-run] {endpoint}")
            for m in plan["mentions"]:
                click.echo(f"[dry-run] mention {m['text']} -> {m['user_id']}")
            for p in images:
                click.echo(f"[dry-run] inline image {p}")
            for p in files:
                click.echo(f"[dry-run] upload to OneDrive + share: {p}")
            if missing:
                click.echo(f"[dry-run] WOULD FAIL - missing permission(s): "
                           f"{', '.join(plan['missing_permissions'])}")
            click.echo("--- html ---")
            click.echo(html or "(attachments only)")
        return
    if missing:
        raise MissingScope(missing[0])

    if conv.create_with:
        me_id = g.me()["id"]
        chat = g.post("/chats", {"chatType": "oneOnOne", "members": [
            {"@odata.type": "#microsoft.graph.aadUserConversationMember", "roles": ["owner"],
             "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{uid}')"}
            for uid in (me_id, conv.create_with["id"])]})
        conv.chat_id = chat["id"]

    hosted, attachments = [], []
    for i, p in enumerate(images, start=1):
        tag, hc = inline_image(p, str(i))
        html += tag
        hosted.append(hc)
    for p in files:
        tag, att = attach_file(g, conv, p)
        html += tag
        attachments.append(att)

    payload: dict = {"body": {"contentType": "html", "content": html}}
    if mentions:
        payload["mentions"] = mentions
    if attachments:
        payload["attachments"] = attachments
    if hosted:
        payload["hostedContents"] = hosted
    if importance:
        payload["importance"] = importance
    if subject:
        payload["subject"] = subject

    if conv.kind == "chat":
        base = f"/chats/{quote(conv.chat_id)}/messages"
        if reply_id:
            result = g.post(f"{base}/replyWithQuote", {"messageIds": [reply_id],
                                                       "replyMessage": payload})
        else:
            result = g.post(base, payload)
        link = chat_permalink(conv.chat_id, result.get("id"), g.tenant_id)
    else:
        base = f"/teams/{conv.team_id}/channels/{quote(conv.channel_id)}/messages"
        result = g.post(f"{base}/{reply_id}/replies" if reply_id else base, payload)
        link = result.get("webUrl") or channel_permalink(conv.team_id, conv.channel_id,
                                                         result.get("id"), reply_id, g.tenant_id)
    if as_json:
        emit_json({"ok": True, "backend": "graph", **conv.ctx(), "chat_id": conv.chat_id,
                   "id": result.get("id"), "reply_to": reply_id, "permalink": link})
    else:
        click.echo(f"Sent to {conv.label} (id {result.get('id')})\n{link}")
    names.save()


def _message_path(conv: Conv, mid: str, parent: str | None) -> str:
    if conv.kind == "chat":
        return f"/chats/{quote(conv.chat_id)}/messages/{mid}"
    base = f"/teams/{conv.team_id}/channels/{quote(conv.channel_id)}/messages"
    return f"{base}/{parent}/replies/{mid}" if parent else f"{base}/{mid}"


def _own_message(g: Graph, names: Names, message, chat, team, channel):
    ref = parse_message_ref(message, chat=chat, team=team, channel=channel)
    if ref["kind"] == "chat":
        g.require("chat_write")
        conv, need = Conv("chat", chat_id=ref["chat_id"]), None
    else:
        g.require("channel_write")
        info = channel_info(g, names, ref["channel_id"], ref.get("team_id"))
        conv, need = Conv("channel", team_id=info["team_id"], channel_id=ref["channel_id"]), CHANNEL_WRITE
    path = _message_path(conv, ref["message_id"], ref.get("parent_id"))
    msg = g.get(path, need=need)
    uid, _ = sender(msg)
    if uid != g.me().get("id"):
        raise click.ClickException("That message isn't yours - only your own messages can be "
                                   "edited or deleted")
    return conv, path, msg, need


@cli.command()
@click.argument("message")
@click.option("--text", "-m", help="New full text (Markdown by default)")
@click.option("--file", "-f", "text_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--sub", "subs", nargs=2, multiple=True, metavar="OLD NEW",
              help="Patch the current text: replace OLD with NEW (repeatable)")
@click.option("--format", "fmt", type=click.Choice(["markdown", "html", "text"]), default="markdown")
@click.option("--chat", help="Chat id when MESSAGE is a bare id")
@click.option("--team", help="Team id for a bare channel message id")
@click.option("--channel", help="Channel id for a bare channel message id")
@click.option("--dry-run", is_flag=True, help="Show current and new body; change nothing")
@click.option("--json", "as_json", is_flag=True)
def edit(message, text, text_file, subs, fmt, chat, team, channel, dry_run, as_json):
    """Edit one of YOUR messages in place (keeps reactions and replies). Graph only."""
    graph_only("edit")
    g, names = client()
    conv, path, msg, need = _own_message(g, names, message, chat, team, channel)
    body = msg.get("body") or {}
    current = body.get("content") or ""
    is_html = (body.get("contentType") or "").lower() == "html"
    new_text = read_body(text, text_file)
    if new_text and subs:
        raise click.ClickException("Use either -m/-f (replace) or --sub (patch), not both")
    mentions = None
    if subs:
        new_html = current
        for old, new in subs:
            o, n = (_html.escape(old, quote=False), _html.escape(new, quote=False)) if is_html \
                else (old, new)
            if o not in new_html:
                raise click.ClickException(f"--sub: {old!r} not found in the current text "
                                           "(formatting tags can split words; use -f to replace)")
            new_html = new_html.replace(o, n)
        content_type = body.get("contentType") or "text"
        mentions = msg.get("mentions") or []
    elif new_text:
        new_html, specs = compose_html(new_text, fmt, conv.kind)
        if specs:
            g.require("users")
        mentions = build_mentions(g, specs)
        content_type = "html"
    else:
        raise click.ClickException("Nothing to change: pass -m, -f, or --sub OLD NEW")
    keep = [a for a in msg.get("attachments") or []
            if f'<attachment id="{a.get("id")}"' in new_html]
    dropped = len(msg.get("attachments") or []) - len(keep)
    if dry_run:
        if as_json:
            emit_json({"dry_run": True, "path": path, "current": current, "new": new_html,
                       "attachments_dropped": dropped})
        else:
            click.echo(f"[dry-run] {path}\n--- current ---\n{current}\n--- new ---\n{new_html}")
            if dropped:
                click.echo(f"[dry-run] {dropped} attachment(s) would be dropped (their tags are "
                           "not in the new body)")
        return
    if new_html == current:
        click.echo("No change - message already matches.")
        return
    g.request("PATCH", path, need=need, json_body={
        "body": {"contentType": content_type, "content": new_html},
        "mentions": mentions or [], "attachments": keep})
    if as_json:
        emit_json({"ok": True, "path": path, "id": msg.get("id")})
    else:
        click.echo(f"Edited message {msg.get('id')}")


@cli.command()
@click.argument("message")
@click.option("--chat")
@click.option("--team")
@click.option("--channel")
@click.option("--yes", is_flag=True, help="Required to actually delete")
def delete(message, chat, team, channel, yes):
    """Delete one of YOUR messages (soft delete). Graph only.

    The fix for a reply posted in the wrong place: delete it, then resend
    with --reply-to.
    """
    graph_only("delete")
    g, names = client()
    conv, path, msg, need = _own_message(g, names, message, chat, team, channel)
    preview = message_text(msg)[:200]
    if not yes:
        click.echo(f"Would delete message {msg.get('id')}: {preview!r}\nRe-run with --yes to delete.")
        return
    g.request("POST", f"{path}/softDelete", need=need)
    click.echo(f"Deleted message {msg.get('id')}")


# ---- watch ------------------------------------------------------------------

def _watch_event_message(g, names, conv, msg) -> dict:
    uid, uname = sender(msg)
    rec = conv.ctx()
    rec.pop("conversation", None)
    mid = msg.get("id")
    if conv.kind == "chat":
        rec["chat_id"] = msg.get("chatId") or conv.chat_id
        link = chat_permalink(rec["chat_id"], mid, g.tenant_id)
    else:
        link = msg.get("webUrl") or channel_permalink(conv.team_id, conv.channel_id, mid,
                                                      msg.get("replyToId"), g.tenant_id)
    return {"event": "message", **rec, "conversation": conv.label, "id": mid,
            "reply_to_id": msg.get("replyToId"), "user_id": uid,
            "name": names.user(g, uid, uname) if uid else uname,
            "created": msg.get("createdDateTime"), "text": message_text(msg, plain=True),
            "permalink": link}


class Watcher:
    """Change detector over repeated reads (Graph polling or re-loading the web app).

    New messages = created after start; reactions = reaction createdDateTime
    after start (Graph bumps lastModifiedDateTime on reactions).
    """

    def __init__(self, g, names, start, include_self, reactions):
        self.g, self.names, self.start = g, names, start
        self.include_self, self.reactions = include_self, reactions
        self.me_id = g.me().get("id")
        self.seen_msgs: set[tuple] = set()
        self.seen_reacts: set[tuple] = set()

    def consider(self, conv: Conv, msg: dict, thread_root: str | None = None) -> list[dict]:
        events = []
        if thread_root and msg.get("id") != thread_root and msg.get("replyToId") != thread_root:
            return events
        key = (conv.chat_id or conv.channel_id, msg.get("id"))
        created = parse_graph_dt(msg.get("createdDateTime"))
        uid, _ = sender(msg)
        if (key not in self.seen_msgs and created and created > self.start
                and msg.get("messageType") == "message" and not msg.get("deletedDateTime")):
            self.seen_msgs.add(key)
            if uid != self.me_id or self.include_self:
                events.append(_watch_event_message(self.g, self.names, conv, msg))
        if self.reactions:
            for r in msg.get("reactions") or []:
                ruid = ((r.get("user") or {}).get("user") or {}).get("id")
                rc = parse_graph_dt(r.get("createdDateTime"))
                rkey = key + (ruid, r.get("reactionType"))
                if rkey in self.seen_reacts or not rc or rc <= self.start:
                    continue
                self.seen_reacts.add(rkey)
                if ruid == self.me_id and not self.include_self:
                    continue
                ev = {"event": "reaction_added", **conv.ctx(), "message_id": msg.get("id"),
                      "user_id": ruid, "name": self.names.user(self.g, ruid),
                      "reaction": reaction_label(r), "created": r.get("createdDateTime")}
                if conv.kind == "chat":
                    ev["chat_id"] = conv.chat_id
                events.append(ev)
        return events


def _watch_loop(poll, timeout, interval, exit_on_first) -> int:
    deadline = time.time() + timeout
    matched = 0
    try:
        while True:
            try:
                events = poll()
            except GraphError as exc:
                if exc.status in (403, 404):
                    raise
                click.echo(f"(poll failed: {exc.message.splitlines()[0]}; retrying)", err=True)
                events = []
            for ev in events:
                matched += 1
                emit_json(ev)
                sys.stdout.flush()
                if exit_on_first:
                    break
            if (exit_on_first and matched) or time.time() + interval > deadline:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        click.echo("Stopped.", err=True)
    return matched


@cli.command()
@click.argument("target", required=False)
@click.option("--thread", "thread_ref", help="Channel: only this thread (any message link in it)")
@click.option("--timeout", "-T", default=900, help="Give up after N seconds (default 900)")
@click.option("--interval", "-i", default=30, help="Seconds between polls (default 30, min 10)")
@click.option("--exit-on-first", is_flag=True, help="Return as soon as one event arrives")
@click.option("--reactions/--no-reactions", default=True,
              help="Report reactions (default on) - a thumbs-up is often the answer")
@click.option("--include-self", is_flag=True, help="Also report your own messages/reactions")
def watch(target, thread_ref, timeout, interval, exit_on_first, reactions, include_self):
    """Wait for new messages and reactions; one JSON object per line.

    No TARGET watches all chats (new messages only). Exits 0 when something
    matched, 2 on timeout.
    """
    interval = max(10, interval)
    start = datetime.now(timezone.utc) - timedelta(seconds=2)
    names = Names()

    if backend() == "playwright":
        conv = web_conv(target) if target else None
        root = None
        if thread_ref:
            ref = parse_message_ref(thread_ref)
            if ref["kind"] == "channel":
                conv = conv or Conv("channel", team_id=ref.get("team_id"),
                                    channel_id=ref["channel_id"], label=ref["channel_id"])
                root = ref.get("parent_id") or ref["message_id"]
        click.echo(f"Watching {conv.label if conv else 'all chats'} via Teams web for up to "
                   f"{timeout}s, reloading every {interval}s. Ctrl-C to stop.", err=True)
        import pw_backend
        with web_errors(), web_session() as web:
            web.ensure_app()
            g = WebGraph(infer_me(web.cap.conversations.keys()))
            w = Watcher(g, names, start, include_self, reactions)

            def poll():
                if conv:
                    msgs = web_conv_messages(web, conv, changed_since=start, root=root)
                    return [e for m in msgs for e in w.consider(conv, m)]
                web.page.goto(pw_backend.TEAMS_WEB, wait_until="domcontentloaded")
                web.wait_for(lambda: False, 6)
                if web.on_login_page():
                    raise pw_backend.SignedOut("The Teams web session is signed out")
                events = []
                for chat in web_chats(web):
                    raw = (web.cap.conversations.get(chat["id"]) or {}).get("lastMessage") or {}
                    if raw.get("id"):
                        cconv = Conv("chat", chat_id=chat["id"], label=chat_label(chat, w.me_id))
                        msg = pw_backend.to_graph_message(raw, chat["id"])
                        events += [e for e in w.consider(cconv, msg) if e["event"] == "message"]
                return events

            matched = _watch_loop(poll, timeout, interval, exit_on_first)
    else:
        g, names = client()
        w = Watcher(g, names, start, include_self, reactions)
        conv = resolve_conv(g, names, target) if target else None
        thread_root = None
        if thread_ref:
            ref = parse_message_ref(thread_ref)
            if ref["kind"] != "channel":
                click.echo("(chats have no threads; watching the whole chat)", err=True)
            else:
                if not conv:
                    info = channel_info(g, names, ref["channel_id"], ref.get("team_id"))
                    conv = Conv("channel", team_id=info["team_id"], channel_id=ref["channel_id"],
                                label=f"{info.get('team_name')} / {info.get('channel_name')}")
                thread_root = ref.get("parent_id") or ref["message_id"]
        g.require("channel_read" if conv and conv.kind == "channel" else "chat_read")
        scope = conv.label if conv else "all chats (new messages)"
        click.echo(f"Watching {scope}{' thread ' + thread_root if thread_root else ''} for up to "
                   f"{timeout}s, polling every {interval}s. Ctrl-C to stop.", err=True)
        chat_marks: dict[str, str] = {}

        def poll():
            events = []
            if conv and conv.kind == "chat":
                msgs, _ = fetch_chat_messages(g, conv.chat_id, changed_since=start, limit=200)
                for m in msgs:
                    events += w.consider(conv, m)
            elif conv and thread_root:
                base = f"/teams/{conv.team_id}/channels/{quote(conv.channel_id)}/messages"
                events += w.consider(conv, g.get(f"{base}/{thread_root}", need=CHANNEL_READ))
                for r in g.paged(f"{base}/{thread_root}/replies", {"$top": "50"},
                                 need=CHANNEL_READ, limit=200):
                    events += w.consider(conv, r, thread_root)
            elif conv:
                page = g.get(f"/teams/{conv.team_id}/channels/{quote(conv.channel_id)}/messages",
                             need=CHANNEL_READ, **{"$top": "20", "$expand": "replies"})
                for post in page.get("value") or []:
                    events += w.consider(conv, post)
                    for r in post.get("replies") or []:
                        events += w.consider(conv, r)
            else:
                for chat in list_chats(g, limit=25, members=True):
                    prev = chat.get("lastMessagePreview") or {}
                    last = parse_graph_dt(prev.get("createdDateTime"))
                    if not last:
                        continue
                    if last <= start:
                        break
                    if chat_marks.get(chat["id"]) == prev.get("id"):
                        continue
                    chat_marks[chat["id"]] = prev.get("id")
                    cconv = Conv("chat", chat_id=chat["id"], label=chat_label(chat, w.me_id))
                    msgs, _ = fetch_chat_messages(g, chat["id"], changed_since=start, limit=100)
                    for m in msgs:
                        events += [e for e in w.consider(cconv, m) if e["event"] == "message"]
            return events

        matched = _watch_loop(poll, timeout, interval, exit_on_first)
    names.save()
    if matched == 0:
        click.echo(f"No matching activity within {timeout}s.", err=True)
        sys.exit(2)


if __name__ == "__main__":
    cli()
