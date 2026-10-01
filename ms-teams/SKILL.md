---
name: ms-teams
description: Microsoft Teams access for the agent, as the user - list chats and channels, read history and threads, search, send messages and replies (with @mentions and attachments), edit, watch for replies and reactions, and pull everything changed since a time as JSON for ingestion. Uses Microsoft Graph (delegated) with a local Playwright browser fallback. Use whenever a task involves Teams - a pasted Teams link, a chat with a client, messaging someone, waiting on a reply, or syncing Teams into another system.
---

# Microsoft Teams CLI

Gives the agent the same Teams access the user has: read any chat or channel
they can see, send messages that appear as them, wait for replies, and export
everything changed since a time as JSON lines. Delegated only: it acts as the
signed-in user and can never see more than they can in the Teams client.

**All commands:** `cd ~/.claude/skills/ms-teams && uv run ms_teams.py <command>`

If anything fails, run `doctor` first. It reports which backend is live, which
Graph permissions are granted (and what each unlocks), and whether the browser
session is still signed in.

## Two backends

| | Graph (default) | Playwright (fallback) |
|---|---|---|
| How | Microsoft Graph API, delegated token for our own app registration | A local browser on the Teams web app; reads the JSON the app itself loads |
| Needs | App registration + permissions granted (often by an admin) | One-time interactive sign-in to a dedicated browser profile |
| Reads | Everything below | Chats, chat history, channel threads by link, `messages --all-chats` |
| Sends | Markdown, @mentions, attachments, quote-replies, subjects | Plain typed text only (chat, channel post, channel thread reply) |
| Graph-only | `search`, `edit`, `delete`, `download`, `user` | - |
| Runs on | Anywhere the token is | This machine only |

`--backend auto` (default): Graph when a Graph token works, otherwise
Playwright when its profile is signed in. Force one with
`--backend graph|playwright` (before the command) or `MS_TEAMS_BACKEND`.
Output is the same JSON shape either way.

## Command reference

| Command | What it does |
|---|---|
| `doctor [--quick]` | Diagnose both backends. Start here when something breaks |
| `chats [--since] [--type] [--json]` | Your chats, newest activity first (id, type, topic, members, last activity) |
| `teams` / `channels <team>` | Joined teams; channels in a team |
| `history <target>` | Chat or channel history; **channel replies included** |
| `thread <message link>` | A channel post + all replies (chat link: the message with context) |
| `messages --all-chats --since <t> --json` | **The ingestion read**: every chat changed since `t` (see Output contract) |
| `messages <target> --since <t> --json` | Same records for one conversation |
| `search <query>` | Microsoft Search over your Teams messages (Graph) |
| `send <target>` | Post a message **as the user** |
| `edit <message link>` | Edit one of the user's messages in place (Graph) |
| `delete <message link> --yes` | Soft-delete one of the user's messages (Graph) |
| `watch [target]` | Wait for new messages/reactions; JSON lines |
| `download <message link>` | Save a message's images/files locally (Graph) |
| `user <email or name>` | Look up a person (id, email, mention syntax) (Graph) |
| `configure` / `login` / `logout` | Graph app registration + sign-in |
| `token status` / `token export` | Refresh-token health; hand the refresh token to another tool |
| `pw login` / `pw logout` / `pw capture` | Browser backend sign-in, removal, diagnostics |

### Targets

- **Teams link** - "Copy link" on a message, chat or channel. A link to a
  channel *reply* carries its root (`parentMessageId`), so the whole thread is used.
- **Chat id** `19:...@thread.v2` / `19:<a>_<b>@unq.gbl.spaces`; **channel id** `19:...@thread.tacv2`
- **Email** - your 1:1 with that person (`send` opens it if it doesn't exist yet)
- **`"Team/Channel"`** - by display names
- **Part of a chat name** - topic or member name; if several match you get a list of ids

The Playwright backend takes links and ids only (names and emails need Graph).

## Agent guidelines

1. **Resolve ambiguity** - if the user says "the chat with Sam", run `chats`
   (or `user sam`) first; if several match, ask before reading or sending.
2. **Read into context, don't print** - the user usually wants you to *know*
   the conversation. Use `-o <file>` for large ranges.
3. **Scope by time** - `--since 30d` is a good default; `history` defaults to 30 days.
4. **Channels are threads.** A channel read without replies misses the
   conversation; replies are on by default.

## Reading

```bash
uv run ms_teams.py history "<chat or channel link>" --since 14d
uv run ms_teams.py history someone@contoso.com --since 2026-09-01 --until 2026-09-15
uv run ms_teams.py thread "<link to any message in a channel thread>"
uv run ms_teams.py chats --since 7d
uv run ms_teams.py search "budget approval"
```

Human output - local time with UTC offset, message id (what `--reply-to`
takes), reactions (a thumbs-up is often the answer):

```
**Alex Client** (2026-10-01 09:12 UTC-04) [edited]  id=1727712345678:
Can you check the Q3 numbers?
[quoting Dana Owner, id=1727712000000] "numbers are in the sheet"
reactions: 👍 x1 (Dana Owner)

  > **Dana Owner** (2026-10-01 09:20 UTC-04)  id=1727712800000:
  > A channel reply
```

### Images and files

Inline images download automatically (Graph: hosted content; Playwright: the
images the web app displays, captured as it loads them) and the output shows
`[image -> C:\...\Temp\teams-files\T-....png]`. **Read that path** to see what
the sender pasted. Attached files (they live in the sender's OneDrive/SharePoint)
are listed; `--all-files` or `download <message link>` fetches them
(needs `Files.Read.All`). The cache is `%TEMP%\teams-files`, swept after 24 h
(override `--files-dir` / `TEAMS_FILES_DIR`).

## Output contract (JSON)

Every `--json` read prints **one JSON object per line**. Each line has a
`record` field: `chat`, `message`, `team`, `channel`, `user` or `search_hit`.
Both backends emit identical keys.

**Identity (upsert key): `(conversation_id, id)`.** `conversation_id` is the
native chat id or channel id (Graph's and the web app's ids are the same), and
`id` is the native message id. Message ids are only unique within a
conversation, so always key on the pair.

`messages --all-chats --since <ISO> --json` - the one-call incremental read:
chats whose last message falls in the window; for each, a `chat` record, then
every message **created, edited, deleted or reacted-to** in the window (Graph:
`lastModifiedDateTime gt since`). Chats are selected by their *last message*
time, so an edit or reaction in a chat that got no new message in the window
isn't seen (run `messages <chat> --changed-since` for a full re-check of one
chat). Use the previous run's start time as the next
`--since`, with a minute or two of overlap. `--channels` adds every joined
channel (Graph; needs `ChannelMessage.Read.All`). `--no-chat-records` drops the
chat lines.

`message` record:

| Field | Notes |
|---|---|
| `record`, `backend` | `"message"`, `"graph"` / `"playwright"` |
| `kind` | `chat` or `channel` |
| `conversation_id`, `chat_id` / `team_id` + `channel_id` | native ids |
| `conversation` | chat/channel display label, or null |
| `id` | native message id |
| `reply_to_id`, `thread_id` | channel replies: the root post id; `thread_id` = root id for roots too. null in chats |
| `quotes` | chat quote-replies: ids of the quoted messages |
| `created`, `modified`, `edited` | ISO UTC (`modified` moves on edits and reactions) |
| `deleted` | bool (`text` is `[deleted]`) |
| `type` | `message` or `systemEventMessage` |
| `from` | `{id, name, email}` - email from chat members or the directory (null if unknown, e.g. external users) |
| `text` | plain text, HTML stripped (`@Name` for mentions, `[image -> path]` for images) |
| `html` | raw HTML body, only with `--raw-html` |
| `subject`, `importance` | channel subject; `normal`/`high`/`urgent` |
| `mentions`, `reactions` | `[{id, text}]`, `[{reaction, type, user_id, user, created}]` |
| `attachments`, `images` | `[{type, name, url, local_path}]`, `[{url, local_path}]` |
| `web_url` | Graph's `webUrl` - **null for chat messages** (Graph only fills it for channel messages) |
| `permalink` | always set: `web_url`, or a built `https://teams.microsoft.com/l/message/<conversation>/<id>?...` deep link |

`chat` record: `record, backend, kind, chat_id, conversation_id, type
(oneOnOne|group|meeting), topic, name, members [{id, name, email}],
last_activity, created, last_message {id, created, from, text}, web_url`.
(Playwright members have ids; names/emails fill in from senders seen and may be null.)

**Exit codes:** `0` ok (an empty result is a real empty result), `1` error,
`2` `watch` timed out, `3` **auth expired**, `4` missing permission. On `3`,
stderr carries one JSON line before the message - never an empty success:

```json
{"error": "auth_expired", "backend": "graph", "detail": "...", "fix": "uv run ms_teams.py login"}
```

On `4`: `{"error": "missing_scope", "scopes": ["ChatMessage.Send"]}`.

## Sending messages

Messages post **as the user**. Draft in Markdown; it's converted to Teams HTML.

```bash
uv run ms_teams.py send someone@contoso.com -f draft.md                 # 1:1
uv run ms_teams.py send "<channel message link>" -f reply.md            # reply in that thread
uv run ms_teams.py send "Team/General" -f post.md --subject "Heads up"  # new channel post
uv run ms_teams.py send "<chat link>" --reply-to "<message link>" -m "on it"   # quote-reply
uv run ms_teams.py send "<chat link>" -m "see screenshot" -a shot.png -a report.pdf
uv run ms_teams.py send "<chat link>" -f draft.md --dry-run             # preview, sends nothing
```

- `-f/--file` avoids shell quoting; it pairs with the `humanize` temp file.
- `--dry-run` resolves the target, renders the HTML, resolves mentions, and
  lists any **missing permission** - without sending or uploading anything.
- `--json` returns `{id, permalink, ...}` for the posted message.

> **Reply in the thread - the #1 send mistake.**
> **Channels:** a top-level post starts a *new conversation*. If you're
> answering a post, reply in its thread: pass the message link as TARGET (a
> channel message link replies in that thread automatically; `--new-post`
> overrides) or `--reply-to <link or id>`. Any message in the thread works -
> the root is found for you.
> **Chats have no threads.** A plain `send` is normal in an active 1:1.
> When answering a specific *earlier* message, or when several topics are
> interleaved, quote-reply it with `--reply-to <message link or id>` so the
> answer shows what it refers to.
> **You can't move a message.** Posted in the wrong place? `delete <link> --yes`,
> then resend correctly.

### Formatting

| Want | Write |
|---|---|
| bold / italic / strike | `**bold**` `*italic*` `~~strike~~` |
| link | `[label](https://url)` |
| lists, headings, tables | normal Markdown |
| code | `` `inline` `` / fenced blocks (language highlighting in channels only) |
| line break | a single newline is kept as a line break |
| **@mention** | `@[Display Name](person@contoso.com)` - a real mention that notifies them |

`--format html` sends raw Teams HTML; `--format text` sends literal text. Bodies
over ~28 KB are rejected by Teams - attach a file instead.

**Attachments:** images go inline (like a pasted screenshot, up to 4 MB);
other files upload to the user's OneDrive `Microsoft Teams Chat Files` folder
(where Teams puts them) and are shared read-only with the chat's members, or
with the organization for channel posts.

**Playwright backend sends plain text only**, typed into the real compose box
(newlines become Shift+Enter). It refuses mentions, attachments, quote-replies,
subjects and importance rather than sending something different from the draft.

**Identify as an agent** when the recipient should know - there is no automatic
footer. If the user says "start with 'Claude here,'", do exactly that.

**Approval:** per the user's global rules, human-facing outbound messages go
through `humanize` first, and approval is only valid *after* the humanized draft
has been shown. Draft -> show -> wait -> send. Never send while testing.

## Editing and deleting (Graph)

```bash
uv run ms_teams.py edit "<message link>" -f fixed.md
uv run ms_teams.py edit "<message link>" --sub "its" "it's" --dry-run
uv run ms_teams.py delete "<message link>" --yes
```

Edits keep the id, reactions and replies - once someone has reacted, edit
instead of delete-and-repost. Only the user's own messages. Chat edits need
`Chat.ReadWrite`; channel edits need `ChannelMessage.ReadWrite` (admin consent).

## Watching for replies and reactions

```bash
uv run ms_teams.py watch "<chat link>" --exit-on-first -T 1800
uv run ms_teams.py watch --thread "<channel message link>" -T 900
uv run ms_teams.py watch                     # all chats, new messages only
```

Prints one JSON object per line, exits `0` on a match, `2` on timeout:

```json
{"event":"message","kind":"chat","chat_id":"19:...","id":"1727...","name":"Alex Client","text":"sounds good","permalink":"..."}
{"event":"reaction_added","kind":"chat","chat_id":"19:...","message_id":"1727...","name":"Alex Client","reaction":"👍"}
```

**Reactions count as answers.** Ask-then-wait = `send`, then
`watch <target> --exit-on-first -T <seconds>`.

`watch` **polls** (Graph change notifications need a public HTTPS webhook):
every `--interval` seconds (default 30, minimum 10) on Graph; on Playwright it
reloads the conversation in the browser. Keep watches bounded and scoped to one
conversation. Microsoft's Teams API terms say polling a resource is allowed
only once per day and more frequent change detection should use change
notifications; a short ask-then-wait is the intended use here, not a standing
monitor. For ingestion, run `messages --all-chats` on a schedule instead.

## Setup

### App registration (Graph)

One **public-client** app registration in the user's own Microsoft 365 tenant
(the tenant that owns the Teams account). Settings:

| Setting | Value |
|---|---|
| Supported account types | Single tenant ("Accounts in this organizational directory only") |
| Platform | **Mobile and desktop applications**, redirect URI `http://localhost` (any port matches) |
| Allow public client flows | **Yes** (Authentication > Advanced settings) - needed for device code and for redeeming the refresh token without a secret |
| Client secret / certificate | none |
| API permissions | Microsoft Graph, **Delegated** only - see below |

Optional hardening: Enterprise applications > the app > Properties >
**Assignment required = Yes**, then assign only the intended user, so nobody
else in the tenant can sign in to it.

The ids are per-machine config, not code:

```bash
uv run ms_teams.py configure --client-id <application id> --tenant <tenant id> --login-hint you@company.com
```

### Permissions

| Scope | Admin consent required? | Unlocks |
|---|---|---|
| `offline_access` | No | refresh token (stay signed in) |
| `User.Read` | No | whoami |
| `User.ReadBasic.All` | No | names/emails of senders and reactors, @mentions, send-by-email |
| `Chat.Read` | No | list + read chats, chat images, search, watch chats |
| `ChannelMessage.Read.All` | **Yes** | read channel posts/replies + their images, watch channels |
| `Team.ReadBasic.All` | No | list teams (and resolve a channel's team) |
| `Channel.ReadBasic.All` | No | list channels |
| `Files.Read.All` | No | download files people attached |
| `ChatMessage.Send` | No | send in chats; the only scope quote-reply accepts |
| `Chat.ReadWrite` | No | edit/delete own chat messages, open a new 1:1 (supersedes `Chat.Read`) |
| `ChannelMessage.Send` | No | post to channels, reply in threads |
| `Files.ReadWrite` | No | upload + share attachments |
| `ChannelMessage.ReadWrite` | **Yes** | edit/delete own channel posts |

"No" means a user may consent themselves **if the tenant allows user consent**.
Many tenants turn user consent off; then an admin must grant every scope.
Reading needs the first eight; sending adds the next four.

`login` asks for `https://graph.microsoft.com/.default`, which means
"everything granted to this app for me". It never requests an unapproved
scope, and newly granted scopes show up on the next token refresh with no code
change or re-login. Keep the registration's permission list equal to what was
granted: an interactive `.default` sign-in prompts for any listed permission
that isn't granted yet, and a tenant with user consent off blocks that prompt.

### Admin consent

Granting consent takes an admin with at least **Cloud Application
Administrator** (Application Administrator, Privileged Role Administrator and
Global Administrator also work). Two ways:

- **Portal (tenant-wide):** Entra admin center > App registrations > the app >
  API permissions > *Grant admin consent*. Covers every listed permission for
  every user who can sign in to the app (pair with Assignment required).
- **One user only (least privilege):** Microsoft's per-user grant
  ([docs](https://learn.microsoft.com/entra/identity/enterprise-apps/grant-consent-single-user)),
  in Graph PowerShell as Cloud Application Administrator:

```powershell
Connect-MgGraph -Scopes "User.ReadBasic.All Application.ReadWrite.All DelegatedPermissionGrant.ReadWrite.All AppRoleAssignment.ReadWrite.All"
$clientSp = Get-MgServicePrincipal -Filter "appId eq '<application id>'"
if (-not $clientSp) { $clientSp = New-MgServicePrincipal -AppId '<application id>' }
$graphSp  = Get-MgServicePrincipal -Filter "appId eq '00000003-0000-0000-c000-000000000000'"
$user     = Get-MgUser -UserId '<user upn>'
New-MgOauth2PermissionGrant -ClientId $clientSp.Id -ResourceId $graphSp.Id -ConsentType Principal `
  -PrincipalId $user.Id -Scope "openid profile offline_access User.Read User.ReadBasic.All Chat.Read ChannelMessage.Read.All Team.ReadBasic.All Channel.ReadBasic.All Files.Read.All"
```

To add scopes later, update that grant's `Scope` (`Update-MgOauth2PermissionGrant`)
rather than creating a second one, and add them to the registration too.

### Sign in (Graph)

```bash
uv run ms_teams.py login          # opens the browser; MFA as usual
uv run ms_teams.py login --device-code    # headless alternative (if the tenant allows device code)
uv run ms_teams.py doctor
```

The token cache is `%LOCALAPPDATA%\ms-teams-cli\token_cache.bin`, encrypted
with Windows DPAPI (msal-extensions), so only this Windows user can read it.
Tokens are never printed. Refresh tokens last 90 days of inactivity and slide
on use; password resets, admin revocation or Conditional Access can end them
sooner - then you get exit `3` and `login` again.

### Exporting the refresh token

For a tool that needs to read Teams as the user without this CLI:

```bash
uv run ms_teams.py token status            # health, no secrets
uv run ms_teams.py token export --out <file>     # or --stdout to pipe
```

The export holds `client_id, tenant_id, token_endpoint, scope_to_request,
scopes, refresh_token`. Redeem it as a public client (no secret):
`POST token_endpoint` with `grant_type=refresh_token&client_id=...&refresh_token=...&scope=<scope_to_request>`.
**Refresh tokens rotate** - store the new `refresh_token` from every
redemption. The file is a password: owner-only permissions, never commit it,
never paste it into chat or a third-party service.

### Playwright backend (local fallback)

For when Graph isn't available (e.g. consent pending). One-time, in a terminal
the user can see:

```bash
uv run ms_teams.py pw login
```

A browser (Edge by default; `MS_TEAMS_PW_CHANNEL=chrome` or empty for bundled
Chromium) opens on `teams.cloud.microsoft` with a dedicated profile at
`%LOCALAPPDATA%\ms-teams-cli\pw-profile`. Sign in, finish MFA, answer **Yes** to
"Stay signed in?", and wait for the chats to appear; the window closes itself.
Every later call runs headless with that profile.

How it reads: it navigates the real web app (deep links to a chat/channel,
scrolling up for older messages) and **passively records the JSON responses
the app itself fetches**, then converts them to the same records Graph gives.
It never reads request headers, never extracts or exports a token, never calls
a Teams/Graph API itself, and drops any response from an auth endpoint.

Limits: slower (seconds per chat), plain-text sends only, no search/edit/
delete/download, channel history = what the channel view loads (open a
`thread` link for full replies), and the web app's internal formats can change
without notice. If reads start coming back empty or odd, run
`pw capture [<chat link>]` - it writes the captured payloads (credential-like
keys scrubbed; message text included, local only) to the CLI home so the
parsers can be checked. A signed-out profile gives exit `3`
(`backend: playwright`); fix with `pw login`. `pw logout --yes` deletes the profile.

Check the tenant's policy before relying on it: where an admin has disabled
user consent for apps, browser automation of the user's own session may be
seen as working around that control.

## Errors

| Error | Meaning / fix |
|---|---|
| exit 3, `auth_expired` | Graph: not signed in / refresh failed -> `login`. Playwright: session signed out -> `pw login` |
| exit 4, `missing_scope` | Token fine, permission not granted -> add it to the registration + extend the admin grant (`doctor` shows what's granted) |
| `Need admin approval` at sign-in | The tenant blocks user consent: an admin must grant (see Admin consent) |
| `AADSTS65001` | Consent missing for a requested scope |
| `AADSTS7000218` | "Allow public client flows" is off on the registration |
| `AADSTS50011` / `AADSTS500113` | Redirect URI missing/wrong: add Mobile and desktop `http://localhost` |
| `AADSTS700016` | Wrong client id or tenant |
| `AADSTS53003` | Blocked by Conditional Access (try interactive instead of device code) |
| `AADSTS700082` / `AADSTS70043` | Refresh token expired -> `login` |
| Graph 403 | Not a member of that chat/team, or the action isn't allowed |
| Graph 404 | Wrong id, or not visible to the user |
| Graph 429 | Throttled; retried automatically (honours `Retry-After`) |

## Notes

- **Token security:** refresh tokens and the browser profile both grant the
  user's Teams access. Never commit, paste, or send them to a third-party fetcher.
- **Rate limits** are retried automatically. Names and emails are cached in
  `%LOCALAPPDATA%\ms-teams-cli\names.json`.
- **Chat messages have no `webUrl`** in Graph (it's null); `permalink` builds
  the same deep link "Copy link" produces.
- **Tests:** `tests/test_ms_teams.py` runs fully offline (mocked Graph, fake
  browser session) - the header shows the `uv run ... pytest` command.
