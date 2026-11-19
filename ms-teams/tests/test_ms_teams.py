"""Offline tests for ms_teams.py + pw_backend.py: no live token, no browser, no network.

Run from the skill dir:
    uv run --with pytest --with responses --with click --with requests --with msal \
        --with msal-extensions --with markdown --with markdownify --with beautifulsoup4 \
        pytest tests -q

Graph is mocked with `responses`, which raises on any request that isn't
mocked - so these tests also prove nothing reaches Microsoft. The Playwright
backend is exercised through a fake session fed synthetic chat-service payloads.
"""

import base64
import json
import re
import struct
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import responses
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ms_teams as mt  # noqa: E402
import pw_backend as pb  # noqa: E402

TENANT = "11111111-2222-3333-4444-555555555555"
ME = "me-0000"
ALEX = "alex-1111"
CHAT = "19:abcdef0123456789@thread.v2"
TEAM = "team-aaaa"
CHANNEL = "19:chan0123@thread.tacv2"
GRAPH = "https://graph.microsoft.com/v1.0"
ALL_SCOPES = " ".join(mt.READ_PROFILE + mt.SEND_PROFILE)


def fake_jwt(scp: str) -> str:
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'none'})}.{b64({'scp': scp, 'tid': TENANT, 'exp': 2000000000})}.sig"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_TEAMS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TEAMS_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("MS_TEAMS_ACCESS_TOKEN", fake_jwt(ALL_SCOPES))
    monkeypatch.delenv("MS_TEAMS_BACKEND", raising=False)
    monkeypatch.setattr(mt.time, "sleep", lambda s: None)
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "config.json").write_text(json.dumps({"tenant_id": TENANT}))
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


def G(path_regex: str):
    return re.compile(re.escape(GRAPH) + path_regex + r"(\?.*)?$")


def qs(call) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(call.request.url).query).items()}


def run(*args):
    return CliRunner().invoke(mt.cli, list(args), catch_exceptions=False)


def run_err(*args):
    return CliRunner().invoke(mt.cli, list(args))


def json_lines(text: str) -> list[dict]:
    return [json.loads(x) for x in text.splitlines() if x.startswith("{")]


def mock_me(rsps):
    rsps.get(G(r"/me"), json={"id": ME, "displayName": "Dana Owner", "mail": "dana@contoso.com"})


def mock_chat(rsps):
    rsps.get(G(r"/chats/19%3Aabcdef0123456789%40thread\.v2"), json={
        "id": CHAT, "chatType": "oneOnOne", "topic": None,
        "members": [{"userId": ME, "displayName": "Dana Owner", "email": "dana@contoso.com"},
                    {"userId": ALEX, "displayName": "Alex Client", "email": "alex@contoso.com"}]})


def mock_channel_lookup(rsps):
    rsps.get(G(r"/me/joinedTeams"), json={"value": [{"id": TEAM, "displayName": "Projects"}]})
    rsps.get(G(rf"/teams/{TEAM}/channels"), json={"value": [
        {"id": CHANNEL, "displayName": "General", "membershipType": "standard"}]})


def msg(mid, created, text="hello", user=ALEX, name="Alex Client", **extra):
    m = {"id": mid, "messageType": "message", "createdDateTime": created,
         "lastModifiedDateTime": created, "chatId": CHAT, "replyToId": None,
         "from": {"user": {"id": user, "displayName": name}},
         "body": {"contentType": "html", "content": f"<p>{text}</p>"},
         "attachments": [], "mentions": [], "reactions": [], "webUrl": None}
    m.update(extra)
    return m


def future_iso(seconds=30):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------- parsing

class TestParsing:
    def test_channel_reply_link(self):
        url = ("https://teams.microsoft.com/l/message/19%3Achan0123%40thread.tacv2/1727000000002"
               f"?tenantId={TENANT}&groupId={TEAM}&parentMessageId=1727000000001"
               "&teamName=Projects&channelName=General&createdTime=1727000000002")
        assert mt.parse_teams_url(url) == {"kind": "channel", "team_id": TEAM, "channel_id": CHANNEL,
                                           "message_id": "1727000000002", "parent_id": "1727000000001"}

    def test_channel_root_link_has_no_parent(self):
        url = (f"https://teams.microsoft.com/l/message/{CHANNEL}/1727000000001"
               f"?groupId={TEAM}&parentMessageId=1727000000001")
        assert mt.parse_teams_url(url)["parent_id"] is None

    def test_chat_message_link(self):
        url = ("https://teams.microsoft.com/l/message/19:abcdef0123456789@thread.v2/1727000000009"
               "?context=%7B%22contextType%22%3A%22chat%22%7D")
        assert mt.parse_teams_url(url) == {"kind": "chat", "chat_id": CHAT, "message_id": "1727000000009"}

    def test_one_on_one_chat_link_new_domain(self):
        chat = "19:aaa_bbb@unq.gbl.spaces"
        url = f"https://teams.cloud.microsoft/l/chat/{chat}/0?tenantId={TENANT}"
        assert mt.parse_teams_url(url) == {"kind": "chat", "chat_id": chat}

    def test_channel_and_team_links(self):
        c = mt.parse_teams_url(f"https://teams.microsoft.com/l/channel/{CHANNEL}/General?groupId={TEAM}")
        assert c == {"kind": "channel", "team_id": TEAM, "channel_id": CHANNEL}
        t = mt.parse_teams_url(f"https://teams.microsoft.com/l/team/{CHANNEL}/conversations?groupId={TEAM}")
        assert t["channel_id"] == CHANNEL

    def test_rejects_non_teams(self):
        with pytest.raises(Exception):
            mt.parse_teams_url("https://example.com/l/chat/x/0")

    @pytest.mark.parametrize("value,kind", [
        (CHAT, "chat"), ("19:x_y@unq.gbl.spaces", "chat"), ("19:meeting_abc@thread.v2", "chat"),
        (CHANNEL, "channel"), ("alex@contoso.com", "person"),
        ("Projects/General", "channel-name"), ("Alex", "name"), ("@Alex", "name"),
    ])
    def test_parse_target(self, value, kind):
        assert mt.parse_target(value)["kind"] == kind

    def test_permalinks_round_trip(self):
        link = mt.chat_permalink(CHAT, "1727000000009", TENANT)
        assert mt.parse_teams_url(link) == {"kind": "chat", "chat_id": CHAT, "message_id": "1727000000009"}
        p = mt.parse_teams_url(mt.channel_permalink(TEAM, CHANNEL, "2", "1", TENANT))
        assert (p["team_id"], p["channel_id"], p["message_id"], p["parent_id"]) == (TEAM, CHANNEL, "2", "1")

    def test_message_ref_bare_id(self):
        assert mt.parse_message_ref("1727000000009", chat=CHAT)["chat_id"] == CHAT
        with pytest.raises(Exception):
            mt.parse_message_ref("1727000000009")


class TestTime:
    def test_relative_and_dates(self):
        now = datetime.now(timezone.utc)
        assert abs((now - mt.parse_when("12h")) - timedelta(hours=12)) < timedelta(seconds=5)
        assert abs((now - mt.parse_when("2w")) - timedelta(weeks=2)) < timedelta(seconds=5)
        assert mt.parse_when("2026-09-01").tzinfo is not None
        assert mt.parse_when("2026-09-01T12:00:00Z") == datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
        with pytest.raises(Exception):
            mt.parse_when("last tuesday")

    def test_graph_dt_seven_digits(self):
        assert mt.parse_graph_dt("2026-09-30T14:05:06.1234567Z") == \
            datetime(2026, 9, 30, 14, 5, 6, 123456, tzinfo=timezone.utc)

    def test_fmt_local_shape(self):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC[+-]\d{2}(:\d{2})?",
                            mt.fmt_local("2026-09-30T14:05:06Z"))

    def test_iso_z(self):
        assert mt.iso_z(datetime(2026, 9, 1, 12, tzinfo=timezone.utc)) == "2026-09-01T12:00:00.000Z"


# ---------------------------------------------------------------- rendering

class TestRendering:
    def test_html_to_text_mentions_emoji_images(self):
        m = msg("1", "2026-09-30T14:00:00Z", mentions=[
            {"id": 0, "mentionText": "Alex", "mentioned": {"user": {"id": ALEX}}},
            {"id": 1, "mentionText": "Client", "mentioned": {"user": {"id": ALEX}}}])
        img = f"{GRAPH}/chats/{CHAT}/messages/1/hostedContents/aaa/$value"
        html = ('<p>Hi <at id="0">Alex</at> <at id="1">Client</at>, see '
                '<emoji alt="\U0001F44D"></emoji> my_var <b>bold</b></p><attachment id="abc"></attachment>'
                f'<p><img src="{img}"></p><codeblock class="python"><code>print(1)</code></codeblock>')
        text = mt.html_to_text(html, "html", m, {img: "C:/tmp/T-x.png"})
        assert "Hi @Alex Client, see \U0001F44D my_var **bold**" in text
        assert "[image -> C:/tmp/T-x.png]" in text
        assert "print(1)" in text and "abc" not in text
        plain = mt.html_to_text(html, "html", m, {}, plain=True)
        assert "my_var bold" in plain and "**" not in plain

    def test_web_mention_span(self):
        html = '<p>ping <span itemtype="http://schema.skype.com/Mention" itemid="0">Alex</span></p>'
        assert mt.html_to_text(html, "html", plain=True) == "ping @Alex"

    def test_quote_reference_and_reactions_render(self, env):
        mock_me(env)
        env.get(G(rf"/users/{ALEX}"), json={"displayName": "Alex Client", "mail": "alex@contoso.com"})
        m = msg("2", "2026-09-30T14:00:00Z", reactions=[
            {"reactionType": "like", "createdDateTime": "2026-09-30T14:01:00Z",
             "user": {"user": {"id": ALEX}}}],
            attachments=[{"id": "1", "contentType": "messageReference", "content": json.dumps(
                {"messageId": "1", "messagePreview": "can you check",
                 "messageSender": {"user": {"displayName": "Alex Client"}}})}])
        g, names = mt.client()
        lines = "\n".join(mt.render_message(g, names, m, {}))
        assert '[quoting Alex Client, id=1] "can you check"' in lines
        assert "reactions: \U0001F44D x1 (Alex Client)" in lines
        assert "id=2:" in lines

    def test_compose_markdown(self):
        src = ("**Bold** and [link](https://x.com) for @[Alex Client](alex@contoso.com)\n"
               "line two\n\n- a\n- b\n\n```python\nprint(1)\n```")
        html, specs = mt.compose_html(src, "markdown", "chat")
        assert "<strong>Bold</strong>" in html and '<a href="https://x.com">link</a>' in html
        assert '<at id="0">Alex Client</at>' in html
        assert specs == [("Alex Client", "alex@contoso.com")]
        assert "<br" in html and "<li>a</li>" in html
        assert '<codeblock class="plaintext"><code>print(1)' in html
        html_ch, _ = mt.compose_html(src, "markdown", "channel")
        assert '<codeblock class="python">' in html_ch

    def test_compose_markdown_paragraph_gap(self):
        gap = "<p>&nbsp;</p>"
        html, _ = mt.compose_html("one\n\ntwo\n\nthree", "markdown", "chat")
        assert html == f"<p>one</p>\n{gap}\n<p>two</p>\n{gap}\n<p>three</p>"
        # only between paragraphs: none next to a list, heading or code block, none trailing
        html, _ = mt.compose_html("# h\n\nintro\n\n- a\n- b\n\nout\n\n```\nx\n\ny\n```\n\nend",
                                  "markdown", "chat")
        assert gap not in html
        assert mt.compose_html("a\nb", "markdown", "chat")[0] == "<p>a<br />\nb</p>"
        assert mt.compose_html("<p>a</p>\n<p>b</p>", "html", "chat")[0] == "<p>a</p>\n<p>b</p>"

    def test_compose_text_escapes(self):
        assert mt.compose_html("a < b\nnext", "text", "chat")[0] == "a &lt; b<br>next"

    def test_image_size(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1600, 900) + b"\x00" * 8
        assert mt.image_size(png) == (1600, 900)
        jpg = b"\xff\xd8\xff\xc0\x00\x11\x08" + struct.pack(">HH", 480, 640) + b"\x00" * 10
        assert mt.image_size(jpg) == (640, 480)

    def test_share_id_matches_graph_algorithm(self):
        url = "https://onedrive.live.com/redir?resid=1231244193912!12&authKey=1201919!12921!1"
        raw = base64.b64encode(url.encode()).decode().rstrip("=").replace("/", "_").replace("+", "-")
        assert mt.share_id(url) == "u!" + raw

    def test_jwt_claims(self):
        assert mt.jwt_claims(fake_jwt("Chat.Read"))["scp"] == "Chat.Read"
        assert mt.jwt_claims("garbage") == {}


# ---------------------------------------------------------------- Graph backend commands

class TestHistory:
    def test_chat_history_since_json(self, env):
        mock_me(env)
        mock_chat(env)
        page1 = {"value": [
            msg("1727000000003", "2026-09-20T15:00:00Z", "newest", reactions=[
                {"reactionType": "like", "createdDateTime": "2026-09-20T15:05:00Z",
                 "user": {"user": {"id": ME}}}]),
            msg("1727000000002", "2026-09-10T15:00:00Z", "middle", user=ME, name="Dana Owner")],
            "@odata.nextLink": f"{GRAPH}/chats/{CHAT}/messages?$skiptoken=abc"}
        page2 = {"value": [msg("1727000000001", "2026-08-20T15:00:00Z", "too old")]}
        env.get(re.compile(re.escape(GRAPH) + r"/chats/[^/]+/messages\?\$skiptoken=abc"), json=page2)
        env.get(G(r"/chats/19%3Aabcdef0123456789%40thread\.v2/messages"), json=page1)
        r = run("history", mt.chat_permalink(CHAT), "--since", "2026-09-01", "--json", "--no-files")
        assert r.exit_code == 0, r.output
        recs = json_lines(r.output)
        assert [x["id"] for x in recs] == ["1727000000002", "1727000000003"]  # oldest first
        new = recs[1]
        assert new["record"] == "message" and new["backend"] == "graph"
        assert new["text"] == "newest" and new["from"] == {"id": ALEX, "name": "Alex Client",
                                                          "email": "alex@contoso.com"}
        assert new["reactions"][0]["user"] == "Dana Owner"
        assert new["chat_id"] == CHAT == new["conversation_id"] and new["web_url"] is None
        assert mt.parse_teams_url(new["permalink"])["message_id"] == "1727000000003"
        first = [c for c in env.calls if "/messages" in c.request.url][0]
        assert qs(first)["$orderby"] == "createdDateTime desc"

    def test_chat_changed_since_uses_lastmodified_filter(self, env):
        mock_me(env)
        mock_chat(env)
        env.get(G(r"/chats/[^/]+/messages"), json={"value": []})
        r = run("history", CHAT, "--changed-since", "2026-09-30T00:00:00Z", "--json", "--no-files")
        assert r.exit_code == 0, r.output
        q = qs([c for c in env.calls if "/messages" in c.request.url][0])
        assert q["$orderby"] == "lastModifiedDateTime desc"
        assert q["$filter"] == "lastModifiedDateTime gt 2026-09-30T00:00:00.000Z"

    def test_chat_changed_since_until_uses_modified(self, env):
        mock_me(env)
        mock_chat(env)
        env.get(G(r"/chats/[^/]+/messages"), json={"value": [
            msg("3", "2026-09-01T00:00:00Z", "old but edited in window",
                lastModifiedDateTime="2026-09-30T10:00:00Z"),
            msg("4", "2026-09-30T09:00:00Z", "modified after until",
                lastModifiedDateTime="2026-10-01T09:00:00Z")]})
        r = run("history", CHAT, "--changed-since", "2026-09-30T00:00:00Z",
                "--until", "2026-09-30T23:00:00Z", "--json", "--no-files")
        assert [x["id"] for x in json_lines(r.output)] == ["3"]

    def test_channel_changed_since_sees_edits_on_old_posts(self, env):
        mock_me(env)
        mock_channel_lookup(env)
        env.get(G(rf"/users/{ALEX}"), json={"displayName": "Alex Client", "mail": "alex@contoso.com"})
        old_root = msg("50", "2026-08-01T00:00:00Z", "old root", chatId=None,
                       lastModifiedDateTime="2026-09-30T12:00:00Z", replies=[])
        quiet = [msg(str(60 + i), "2026-07-01T00:00:00Z", "quiet", chatId=None, replies=[])
                 for i in range(3)]
        env.get(re.compile(re.escape(GRAPH) + r"/teams/.+/messages\?\$skiptoken=p2"),
                json={"value": quiet[:1], "@odata.nextLink": f"{GRAPH}/teams/{TEAM}/channels/c/messages?$skiptoken=p3"})
        env.get(re.compile(re.escape(GRAPH) + r"/teams/.+/messages\?\$skiptoken=p3"),
                json={"value": [old_root]})
        env.get(G(rf"/teams/{TEAM}/channels/[^/]+/messages"),
                json={"value": quiet[1:], "@odata.nextLink": f"{GRAPH}/teams/{TEAM}/channels/c/messages?$skiptoken=p2"})
        r = run("history", f"https://teams.microsoft.com/l/channel/{CHANNEL}/General?groupId={TEAM}",
                "--changed-since", "2026-09-29T00:00:00Z", "--json", "--no-files")
        assert r.exit_code == 0, r.output
        assert [x["id"] for x in json_lines(r.output)] == ["50"]

    def test_missing_user_read_exit_4(self, env, monkeypatch):
        monkeypatch.setenv("MS_TEAMS_ACCESS_TOKEN", fake_jwt("Chat.Read"))
        r = run_err("whoami")
        assert r.exit_code == mt.EXIT_MISSING_SCOPE and "User.Read" in r.output

    def test_channel_thread(self, env):
        mock_me(env)
        mock_channel_lookup(env)
        env.get(G(rf"/users/{ALEX}"), json={"displayName": "Alex Client", "mail": "alex@contoso.com"})
        root = msg("100", "2026-09-29T10:00:00Z", "root post", chatId=None, subject="Kickoff",
                   webUrl="https://teams.microsoft.com/l/message/x/100")
        reply = msg("101", "2026-09-29T11:00:00Z", "a reply", chatId=None, replyToId="100")
        env.get(G(rf"/teams/{TEAM}/channels/[^/]+/messages/100/replies"), json={"value": [reply]})
        env.get(G(rf"/teams/{TEAM}/channels/[^/]+/messages/100"), json=root)
        link = mt.channel_permalink(TEAM, CHANNEL, "101", "100", TENANT)
        r = run("thread", link, "--no-files")
        assert r.exit_code == 0, r.output
        assert "Thread in Projects / General (1 replies)" in r.output
        assert "Subject: Kickoff" in r.output and "  > a reply" in r.output
        rj = json_lines(run("thread", link, "--no-files", "--json").output)
        assert rj[1]["thread_id"] == "100" and rj[1]["reply_to_id"] == "100"
        assert rj[0]["conversation_id"] == CHANNEL


def graph_chats_payload():
    return {"value": [
        {"id": CHAT, "chatType": "oneOnOne", "topic": None,
         "lastUpdatedDateTime": "2026-09-01T00:00:00Z",
         "members": [{"userId": ME, "displayName": "Dana Owner", "email": "dana@contoso.com"},
                     {"userId": ALEX, "displayName": "Alex Client", "email": "alex@contoso.com"}],
         "lastMessagePreview": {"id": "1727000000003", "createdDateTime": "2026-09-30T12:00:00Z",
                                "body": {"contentType": "text", "content": "latest"},
                                "from": {"user": {"displayName": "Alex Client"}}}},
        {"id": "19:old@thread.v2", "chatType": "group", "topic": "Old",
         "lastMessagePreview": {"id": "1", "createdDateTime": "2026-01-01T00:00:00Z"}}]}


class TestChatsAndMessages:
    def test_chats_since_json(self, env):
        mock_me(env)
        env.get(G(r"/me/chats"), json=graph_chats_payload())
        r = run("chats", "--since", "2026-09-15", "--json")
        assert r.exit_code == 0, r.output
        recs = json_lines(r.output)
        assert len(recs) == 1
        c = recs[0]
        assert c["record"] == "chat" and c["name"] == "Alex Client" and c["type"] == "oneOnOne"
        assert c["last_activity"] == "2026-09-30T12:00:00Z"
        assert {"id": ALEX, "name": "Alex Client", "email": "alex@contoso.com"} in c["members"]

    def test_messages_all_chats_contract(self, env):
        mock_me(env)
        env.get(G(r"/me/chats"), json=graph_chats_payload())
        env.get(G(r"/chats/[^/]+/messages"), json={"value": [
            msg("1727000000003", "2026-09-30T12:00:00Z", "latest")]})
        r = run("messages", "--all-chats", "--since", "2026-09-29T00:00:00Z", "--json", "--no-files")
        assert r.exit_code == 0, r.output
        recs = json_lines(r.output)
        assert [x["record"] for x in recs] == ["chat", "message"]
        m = recs[1]
        for key in ("conversation_id", "id", "created", "modified", "deleted", "from", "text",
                    "reply_to_id", "attachments", "permalink"):
            assert key in m
        q = qs([c for c in env.calls if "/messages" in c.request.url][0])
        assert q["$filter"] == "lastModifiedDateTime gt 2026-09-29T00:00:00.000Z"

    def test_messages_requires_target_or_all(self, env):
        r = run_err("messages")
        assert r.exit_code == 1


class TestSend:
    def test_dry_run_sends_nothing(self, env):
        mock_me(env)
        mock_chat(env)
        env.get(G(r"/users/alex%40contoso\.com"), json={"id": ALEX, "displayName": "Alex Client"})
        r = run("send", CHAT, "-m", "**Done** @[Alex](alex@contoso.com)", "--dry-run", "--json")
        assert r.exit_code == 0, r.output
        plan = json_lines(r.output)[-1]
        assert plan["dry_run"] and plan["endpoint"] == "POST /chats/{chat}/messages"
        assert '<at id="0">Alex</at>' in plan["html"] and "<strong>Done</strong>" in plan["html"]
        assert plan["mentions"] == [{"text": "Alex", "user_id": ALEX}]
        assert plan["missing_permissions"] == []
        assert all(c.request.method == "GET" for c in env.calls)

    def test_channel_message_link_replies_in_thread(self, env):
        mock_me(env)
        mock_channel_lookup(env)
        r = run("send", mt.channel_permalink(TEAM, CHANNEL, "101", "100", TENANT), "-m", "on it",
                "--dry-run", "--json")
        assert r.exit_code == 0, r.output
        plan = json_lines(r.output)[-1]
        assert plan["reply_to"] == "100" and plan["endpoint"].endswith("/messages/100/replies")
        assert all(c.request.method == "GET" for c in env.calls)

    def test_email_without_chat_would_create(self, env):
        mock_me(env)
        env.get(G(r"/users/sam%40contoso\.com"), json={"id": "sam-1", "displayName": "Sam Lee",
                                                       "mail": "sam@contoso.com"})
        env.get(G(r"/me/chats"), json={"value": []})
        r = run("send", "sam@contoso.com", "-m", "hi", "--dry-run", "--json")
        assert r.exit_code == 0, r.output
        assert json_lines(r.output)[-1]["create_chat_with"] == "sam@contoso.com"

    def test_quote_reply_posts_reply_with_quote(self, env):
        mock_me(env)
        mock_chat(env)
        env.post(G(r"/chats/[^/]+/messages/replyWithQuote"), json={"id": "1727000000099"}, status=201)
        r = run("send", CHAT, "--reply-to", mt.chat_permalink(CHAT, "1727000000005"), "-m", "on it",
                "--json")
        assert r.exit_code == 0, r.output
        body = json.loads([c for c in env.calls if c.request.method == "POST"][0].request.body)
        assert body["messageIds"] == ["1727000000005"]
        assert body["replyMessage"]["body"]["contentType"] == "html"
        out = json_lines(r.output)[-1]
        assert out["id"] == "1727000000099" and "1727000000099" in out["permalink"]

    def test_subject_only_for_new_channel_posts(self, env):
        mock_me(env)
        mock_chat(env)
        r = run_err("send", CHAT, "-m", "x", "--subject", "s", "--dry-run")
        assert r.exit_code != 0 and "--subject" in r.output

    def test_missing_send_scope_exit_4(self, env, monkeypatch):
        monkeypatch.setenv("MS_TEAMS_ACCESS_TOKEN", fake_jwt("User.Read Chat.Read ChannelMessage.Read.All"))
        mock_me(env)
        mock_chat(env)
        r = run_err("send", CHAT, "-m", "hi")
        assert r.exit_code == mt.EXIT_MISSING_SCOPE
        assert '"error": "missing_scope"' in r.output and "ChatMessage.Send" in r.output
        assert all(c.request.method == "GET" for c in env.calls)
        dry = run("send", CHAT, "-m", "hi", "--dry-run", "--json")
        assert "ChatMessage.Send" in json_lines(dry.output)[-1]["missing_permissions"]


class TestEdit:
    def test_sub_dry_run(self, env):
        mock_me(env)
        env.get(G(r"/chats/[^/]+/messages/1727000000002"),
                json=msg("1727000000002", "2026-09-30T10:00:00Z", "its done", user=ME))
        r = run("edit", mt.chat_permalink(CHAT, "1727000000002"), "--sub", "its", "it's", "--dry-run")
        assert r.exit_code == 0, r.output
        assert "<p>it's done</p>" in r.output
        assert all(c.request.method == "GET" for c in env.calls)

    def test_refuses_others_messages(self, env):
        mock_me(env)
        env.get(G(r"/chats/[^/]+/messages/1727000000002"),
                json=msg("1727000000002", "2026-09-30T10:00:00Z", "theirs"))
        r = run_err("edit", mt.chat_permalink(CHAT, "1727000000002"), "-m", "x", "--dry-run")
        assert r.exit_code != 0 and "isn't yours" in r.output


class TestWatch:
    def test_exit_on_first_message(self, env):
        mock_me(env)
        mock_chat(env)
        env.get(G(r"/chats/[^/]+/messages"), json={"value": [msg("1727000000010", future_iso(), "yes")]})
        r = run("watch", CHAT, "--exit-on-first", "-T", "60")
        assert r.exit_code == 0, r.output
        ev = json_lines(r.output)[0]
        assert ev["event"] == "message" and ev["text"] == "yes" and ev["chat_id"] == CHAT

    def test_reaction_counts_as_event(self, env):
        mock_me(env)
        mock_chat(env)
        old = msg("1727000000004", "2026-09-01T00:00:00Z", "mine", user=ME, reactions=[
            {"reactionType": "like", "createdDateTime": future_iso(), "user": {"user": {"id": ALEX}}}])
        env.get(G(r"/chats/[^/]+/messages"), json={"value": [old]})
        ev = json_lines(run("watch", CHAT, "--exit-on-first", "-T", "60").output)[0]
        assert ev["event"] == "reaction_added" and ev["name"] == "Alex Client"

    def test_timeout_exit_2(self, env):
        mock_me(env)
        mock_chat(env)
        env.get(G(r"/chats/[^/]+/messages"), json={"value": []})
        assert run("watch", CHAT, "-T", "5").exit_code == 2


class TestSearchAndDoctor:
    def test_search(self, env):
        env.post(G(r"/search/query"), json={"value": [{"hitsContainers": [{
            "moreResultsAvailable": False, "hits": [{"summary": "the <c0>budget</c0> doc",
                "resource": {"id": "1727000000020", "createdDateTime": "2026-09-30T10:00:00Z",
                             "chatId": CHAT, "channelIdentity": {},
                             "from": {"emailAddress": {"name": "Alex Client",
                                                       "address": "alex@contoso.com"}}}}]}]}]})
        r = run("search", "budget", "--json")
        assert r.exit_code == 0, r.output
        hit = json_lines(r.output)[0]
        assert hit["summary"] == "the budget doc" and hit["conversation_id"] == CHAT
        assert hit["from"]["email"] == "alex@contoso.com"
        assert json.loads(env.calls[0].request.body)["requests"][0]["entityTypes"] == ["chatMessage"]

    def test_doctor_reports_capabilities(self, env, monkeypatch):
        monkeypatch.setenv("MS_TEAMS_ACCESS_TOKEN", fake_jwt("User.Read Chat.Read ChannelMessage.Read.All"))
        mock_me(env)
        env.get(G(r"/me/chats"), json={"value": [{"id": CHAT}]})
        r = run("doctor", "--quick")
        assert r.exit_code == 0, r.output
        assert "[x] list + read chats" in r.output
        assert "[x] read channel posts" in r.output
        assert "[ ] send in chats" in r.output
        assert "missing for full reading: User.ReadBasic.All, Team.ReadBasic.All" in r.output
        assert "[Playwright backend]" in r.output and "not set up" in r.output
        assert "Live backend: graph" in r.output


class TestAuthExpired:
    def _no_account(self, monkeypatch):
        monkeypatch.delenv("MS_TEAMS_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("MS_TEAMS_CLIENT_ID", "app-id")
        monkeypatch.setenv("MS_TEAMS_TENANT", TENANT)

        class NoAccounts:
            def get_accounts(self, username=None):
                return []
        monkeypatch.setattr(mt.Auth, "app", property(lambda self: NoAccounts()))

    def test_dead_graph_session_exits_3_with_marker(self, env, monkeypatch):
        self._no_account(monkeypatch)
        r = run_err("messages", "--all-chats", "--json")
        assert r.exit_code == mt.EXIT_AUTH_EXPIRED
        marker = json_lines(r.output)[0]
        assert marker["error"] == "auth_expired" and marker["fix"]

    def test_doctor_exits_3_when_nothing_works(self, env, monkeypatch):
        self._no_account(monkeypatch)
        r = run_err("doctor", "--quick")
        assert r.exit_code == mt.EXIT_AUTH_EXPIRED and "auth_expired" in r.output


class TestTokenExport:
    def _stub_auth(self, monkeypatch, client_id="app-id"):
        import msal
        monkeypatch.delenv("MS_TEAMS_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("MS_TEAMS_CLIENT_ID", client_id)
        monkeypatch.setenv("MS_TEAMS_TENANT", TENANT)
        cache = msal.TokenCache()
        id_claims = {"oid": "oid1", "tid": TENANT, "preferred_username": "dana@contoso.com",
                     "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "sub": "s",
                     "aud": client_id}

        def b64(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
        cache.add({"client_id": client_id, "scope": ["Chat.Read"],
                   "token_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
                   "response": {"access_token": fake_jwt(ALL_SCOPES), "refresh_token": "RT-SECRET",
                                "expires_in": 3600, "token_type": "Bearer",
                                "id_token": f"{b64({'alg': 'none'})}.{b64(id_claims)}.",
                                "client_info": b64({"uid": "oid1", "utid": TENANT})}})

        class StubApp:
            token_cache = cache

            def get_accounts(self, username=None):
                return list(cache.search(msal.TokenCache.CredentialType.ACCOUNT))

            def acquire_token_silent(self, scopes, account=None):
                assert scopes == [mt.GRAPH_DEFAULT]
                return {"access_token": fake_jwt("User.Read Chat.Read")}

        monkeypatch.setattr(mt.Auth, "app", property(lambda self: StubApp()))

    def test_export_writes_file_and_never_prints_secret(self, env, monkeypatch, tmp_path):
        self._stub_auth(monkeypatch)
        out = tmp_path / "rt.json"
        r = run("token", "export", "--out", str(out))
        assert r.exit_code == 0, r.output
        assert "RT-SECRET" not in r.output
        data = json.loads(out.read_text())
        assert data["refresh_token"] == "RT-SECRET" and data["client_id"] == "app-id"
        assert data["token_endpoint"].endswith(f"{TENANT}/oauth2/v2.0/token")
        assert data["scope_to_request"] == f"{mt.GRAPH_DEFAULT} offline_access"
        assert set(data["scopes"]) == {"User.Read", "Chat.Read", "offline_access"}

    def test_export_requires_a_destination(self, env, monkeypatch):
        self._stub_auth(monkeypatch)
        r = run_err("token", "export")
        assert r.exit_code != 0 and "--out" in r.output

    def test_status_prints_no_secret(self, env, monkeypatch):
        self._stub_auth(monkeypatch)
        r = run("token", "status", "--json")
        assert r.exit_code == 0, r.output
        assert "RT-SECRET" not in r.output
        assert json_lines(r.output)[0]["refresh_token_cached"] is True


# ---------------------------------------------------------------- Playwright backend

def web_message(mid, created_ms, text, oid=ALEX, name="Alex Client", conv=CHAT, **props):
    return {"id": str(mid), "conversationid": conv, "type": "Message",
            "messagetype": "RichText/Html", "content": f"<p>{text}</p>",
            "from": f"https://amer.ng.msg.teams.microsoft.com/v1/users/ME/contacts/8:orgid:{oid}",
            "imdisplayname": name, "composetime": pb.ms_iso(created_ms),
            "originalarrivaltime": pb.ms_iso(created_ms), "properties": props}


ONE_ON_ONE = f"19:{ALEX}_{ME}@unq.gbl.spaces"


def web_conversations():
    return {"conversations": [
        {"id": ONE_ON_ONE, "threadProperties": {"productThreadType": "OneToOneChat"},
         "lastMessage": {"id": "1790000000000", "originalarrivaltime": pb.ms_iso(1790000000000),
                         "content": "<p>latest</p>", "imdisplayname": "Alex Client",
                         "from": f"8:orgid:{ALEX}"},
         "properties": {"lastimreceivedtime": "1790000000000"}},
        {"id": f"19:{ME}_other-2@unq.gbl.spaces", "threadProperties": {},
         "lastMessage": {"id": "1", "originalarrivaltime": "2020-01-01T00:00:00.000Z"}},
        {"id": "48:notifications", "threadProperties": {}},
        {"id": CHANNEL, "threadProperties": {"topic": "General"}},
    ]}


class TestWebNormalizers:
    def test_mri_and_times(self):
        assert pb.mri_oid("https://x/v1/users/ME/contacts/8:orgid:abc-123") == "abc-123"
        assert pb.mri_oid("8:orgid:abc") == "abc"
        assert pb.ms_iso("1727000000000") == "2024-09-22T10:13:20.000Z"

    def test_message_to_graph_shape(self):
        raw = web_message(1727000000123, 1727000000123, "hi <b>there</b>",
                          edittime="1727000005000",
                          emotions=json.dumps([{"key": "like", "users": [
                              {"mri": f"8:orgid:{ME}", "time": 1727000009000}]}]),
                          files=json.dumps([{"fileName": "Budget.xlsx",
                                             "objectUrl": "https://contoso.sharepoint.com/b.xlsx"}]))
        m = pb.to_graph_message(raw)
        assert m["id"] == "1727000000123" and m["chatId"] == CHAT
        assert m["from"]["user"] == {"id": ALEX, "displayName": "Alex Client"}
        assert m["lastEditedDateTime"] == pb.ms_iso(1727000005000)
        assert m["lastModifiedDateTime"] == pb.ms_iso(1727000009000)
        assert m["reactions"][0]["reactionType"] == "like"
        assert m["reactions"][0]["user"]["user"]["id"] == ME
        assert m["attachments"][0]["name"] == "Budget.xlsx"
        assert m["messageType"] == "message" and m["replyToId"] is None

    def test_channel_reply_and_system_events(self):
        raw = web_message(200, 1727000000200, "reply", conv=f"{CHANNEL};messageid=100")
        m = pb.to_graph_message(raw)
        assert m["replyToId"] == "100" and m["channelIdentity"]["channelId"] == CHANNEL
        ev = web_message(300, 1727000000300, "x")
        ev["messagetype"] = "ThreadActivity/AddMember"
        assert pb.to_graph_message(ev)["messageType"] == "systemEventMessage"

    def test_conversation_to_chat(self):
        chats = [pb.to_graph_chat(c) for c in web_conversations()["conversations"]]
        assert chats[2] is None and chats[3] is None  # system feed, channel
        c = chats[0]
        assert c["chatType"] == "oneOnOne" and c["lastMessagePreview"]["id"] == "1790000000000"
        assert {m["userId"] for m in c["members"]} == {ALEX, ME}

    def test_capture_routes_and_blocks(self):
        cap = pb.Capture()
        base = "https://teams.cloud.microsoft/api/chatsvc/amer/v1/users/ME/conversations"
        cap.ingest(base + "?view=msnp24Equivalent", "GET", 200, web_conversations())
        cap.ingest(f"{base}/{CHAT.replace(':', '%3A').replace('@', '%40')}/messages?pageSize=50",
                   "GET", 200, {"messages": [web_message(1, 1727000000001, "a")]})
        cap.ingest("https://teams.cloud.microsoft/api/authsvc/v1.0/authz", "POST", 200,
                   {"tokens": {"skypeToken": "SECRET"}})
        assert len(cap.conversations) == 4 and list(cap.messages) == [CHAT]
        assert all("authsvc" not in e["path"] for e in cap.log)
        assert cap.conv_messages(CHAT)[0]["id"] == "1"

    def test_capture_ignores_foreign_hosts(self):
        cap = pb.Capture()
        cap.ingest("https://evil.example.com/v1/users/ME/conversations", "GET", 200, web_conversations())
        cap.ingest("http://teams.microsoft.com/v1/users/ME/conversations", "GET", 200, web_conversations())
        assert cap.conversations == {} and cap.log == []
        assert pb.allowed_host("https://amer.ng.msg.teams.microsoft.com/v1/users/ME/conversations")
        assert pb.allowed_host("https://us-api.asm.skype.com/v1/objects/0-x/views/imgo")

    def test_scrub_and_infer_me(self):
        assert pb.scrub({"a": 1, "skypeToken": "x", "nested": [{"authorization": "y"}]}) == \
            {"a": 1, "skypeToken": "[scrubbed]", "nested": [{"authorization": "[scrubbed]"}]}
        assert mt.infer_me([ONE_ON_ONE, f"19:{ME}_other-2@unq.gbl.spaces", CHAT]) == ME

    def test_deep_links(self):
        assert pb.deep_link(CHAT) == "https://teams.cloud.microsoft/l/chat/19%3Aabcdef0123456789%40thread.v2/0"
        assert "parentMessageId=100" in pb.deep_link(CHANNEL, TEAM, "100")


class FakeWeb:
    """Stands in for pw_backend.TeamsWeb: no browser; replays captured payloads."""
    sent: list = []
    signed_out = False

    def __init__(self):
        self.cap = pb.Capture()
        self.page = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ensure_app(self):
        if FakeWeb.signed_out:
            raise pb.SignedOut("The Teams web session is signed out")
        base = "https://teams.cloud.microsoft/api/chatsvc/amer/v1/users/ME/conversations"
        self.cap.ingest(base, "GET", 200, web_conversations())

    def open_conversation(self, conv_id, team_id=None, root=None):
        base = "https://teams.cloud.microsoft/api/chatsvc/amer/v1/users/ME/conversations"
        self.cap.ingest(f"{base}/{conv_id}/messages", "GET", 200, {"messages": [
            web_message(1790000000000, 1790000000000, "latest", conv=conv_id),
            web_message(1600000000000, 1600000000000, "ancient", conv=conv_id)]})

    def load_older(self, conv_id, since_iso, max_rounds=40):
        pass

    def send_text(self, conv_id, text, team_id=None, root=None):
        FakeWeb.sent.append((conv_id, text, root))
        return {"id": "1790000000500", "conversation": conv_id}


@pytest.fixture
def web(env, monkeypatch, tmp_path):
    monkeypatch.delenv("MS_TEAMS_ACCESS_TOKEN", raising=False)
    cfg = json.loads((tmp_path / "home" / "config.json").read_text())
    cfg.update({"pw_signed_in_at": "2026-10-01T00:00:00.000Z", "pw_me_id": ME})
    (tmp_path / "home" / "config.json").write_text(json.dumps(cfg))
    (tmp_path / "home" / "pw-profile").mkdir()
    FakeWeb.sent, FakeWeb.signed_out = [], False
    monkeypatch.setattr(mt, "web_session", lambda **k: FakeWeb())
    return env


class TestWebBackend:
    def test_auto_falls_back_to_playwright(self, web):
        assert mt.backend() == "playwright"

    def test_auto_falls_back_when_graph_token_rejected(self, web, monkeypatch):
        monkeypatch.setenv("MS_TEAMS_ACCESS_TOKEN", fake_jwt(ALL_SCOPES))
        web.get(G(r"/me"), status=401, json={"error": {"code": "InvalidAuthenticationToken"}})
        assert mt.backend() == "playwright"

    def test_auto_prefers_valid_graph(self, web, monkeypatch):
        monkeypatch.setenv("MS_TEAMS_ACCESS_TOKEN", fake_jwt(ALL_SCOPES))
        mock_me(web)
        assert mt.backend() == "graph"

    def test_chats_json_shape(self, web):
        r = run("chats", "--json")
        assert r.exit_code == 0, r.output
        recs = json_lines(r.output)
        assert len(recs) == 2 and recs[0]["chat_id"] == ONE_ON_ONE
        assert recs[0]["backend"] == "playwright" and recs[0]["type"] == "oneOnOne"
        assert recs[0]["last_activity"] == pb.ms_iso(1790000000000)

    def test_messages_all_chats_same_shape_as_graph(self, web):
        r = run("messages", "--all-chats", "--since", "2026-09-01T00:00:00Z", "--json", "--no-files")
        assert r.exit_code == 0, r.output
        recs = json_lines(r.output)
        assert [x["record"] for x in recs] == ["chat", "message"]
        m = recs[1]
        assert m["text"] == "latest" and m["id"] == "1790000000000"
        assert m["conversation_id"] == ONE_ON_ONE
        assert mt.parse_teams_url(m["permalink"])["message_id"] == "1790000000000"
        graph_like = mt.message_record(mt.WebGraph(), mt.Names(),
                                       msg("1", "2026-09-30T00:00:00Z"), mt.Conv("chat", chat_id=CHAT))
        assert set(m) == set(graph_like)

    def test_signed_out_exits_3(self, web):
        FakeWeb.signed_out = True
        r = run_err("messages", "--all-chats", "--json")
        assert r.exit_code == mt.EXIT_AUTH_EXPIRED
        marker = json_lines(r.output)[0]
        assert marker == {"error": "auth_expired", "backend": "playwright",
                          "detail": "The Teams web session is signed out",
                          "fix": "uv run ms_teams.py pw login"}

    def test_send_types_plain_text_only(self, web):
        r = run("send", ONE_ON_ONE, "-m", "on it", "--json")
        assert r.exit_code == 0, r.output
        assert FakeWeb.sent == [(ONE_ON_ONE, "on it", None)]
        assert json_lines(r.output)[-1]["id"] == "1790000000500"
        bad = run_err("send", ONE_ON_ONE, "-m", "hi @[Alex](alex@contoso.com)")
        assert bad.exit_code != 0 and "plain text only" in bad.output
        assert len(FakeWeb.sent) == 1

    def test_send_dry_run_and_channel_thread(self, web):
        link = mt.channel_permalink(TEAM, CHANNEL, "101", "100", TENANT)
        r = run("send", link, "-m", "reply", "--dry-run", "--json")
        plan = json_lines(r.output)[-1]
        assert plan["reply_to"] == "100" and plan["backend"] == "playwright"
        assert FakeWeb.sent == []

    def test_graph_only_commands_refuse_forced_playwright(self, web):
        r = run_err("--backend", "playwright", "search", "x")
        assert r.exit_code == 1 and "Graph backend" in r.output
