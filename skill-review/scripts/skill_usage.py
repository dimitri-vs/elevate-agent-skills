#!/usr/bin/env python3
"""skill_usage.py - how is skill X actually being used? Mined from local agent transcripts.

Stdlib only. Run with:  C:/Python313/python.exe -I skill_usage.py <command> ...

  report <skill> [--since YYYY-MM-DD] [--cli BIN ...] [--json]
      Skill loads by mechanism, last use. With --cli: sessions that ran BIN at a real
      command position, how many of them loaded the skill (trigger rate), BIN's failing
      calls grouped by first error line, and --help use split proactive vs reactive.
  triage [--since YYYY-MM-DD] [--json]
      Every installed/repo skill: load sessions in the last 30/120 days, last used,
      install scope, and a suggested review bucket (rules in BUCKET RULES below).

Data sources
  Claude Code  ~/.claude/projects/<proj>/<session>.jsonl and <session>/subagents/*.jsonl
               (subagents are grouped under their parent session id)
               loads = Skill tool_use (input.skill) | Read of .../<name>/SKILL.md |
                       user message starting with <command-name>/<name></command-name>
  history      ~/.claude/history.jsonl: prompts typed as "/<name> ..." (user slash
               commands; reaches back further than transcripts, which are pruned ~30d)
  Codex        ~/.codex/sessions/YYYY/MM/DD/*.jsonl (subagent threads grouped by parent)
               loads = a shell command that reads .../skills/<name>/SKILL.md (normally the
                       ~/.agents/skills junction) | "$<name>" in a typed user message

Never counted as usage: the injected available-skills listing (Claude skill_listing
attachments, the Skill tool description, Codex <skills_instructions> developer
message). Only tool calls, typed prompts and slash markers are read.

Default exclusions (printed in every header): sessions whose cwd is under
~/.claude/skill-reviews/, session ids listed in ~/.claude/skill-reviews/
excluded-sessions.txt (prefix match; a parent id also covers its subagents), and the
session running this script ($CLAUDE_CODE_SESSION_ID). SKILL.md reads made from the
skills repo or ~/.claude/skills (maintenance, not use) are dropped from load counts.

Command-position parsing (approximate shell lexer): heredoc / here-string bodies are
stripped, quotes are respected, "# comments" dropped, and commands are split on
; & && | || ( ) { } (pwsh) and newlines; $(...) and `...` are parsed recursively, as are
bash -c / sh -c / pwsh -Command strings. A command matches BIN when its first word
(after VAR=x assignments and wrappers like timeout/env/sudo/xargs/nohup) is BIN, so
URLs, grep patterns, echo text and heredoc bodies never count.

Failure = tool result with non-zero exit, a timeout ("timed out", "did not complete
within", exit 124/143), or is_error. Exit 0 whose first 3 output lines include one
that starts like a CLI error (error:, Unauthorized, No linked project...) is a soft
failure, tagged [exit 0] (typical of `cli ... 2>&1 | head`); skipped when the call pipes
into grep/rg, whose output is filtered log lines. Not failures: user rejections,
harness blocks (<tool_use_error>), Codex "declined", and exit 1 with empty output when
the call pipes into grep/rg (no match). A failing call is attributed to BIN only if its
error line names BIN, or every non-trivial command in it is BIN (cd/echo/grep/jq/head
etc. are trivial), or BIN runs in its last statement (which sets the exit code).
Unattributed failures are reported as "compound calls that failed elsewhere".

--help classification is per transcript (each agent context has its own timeline):
reactive = after any earlier failing BIN call in that transcript; otherwise
before-first-use (that subcommand group not yet run) or after-use (group already run
without failure). proactive = before-first-use + after-use.

Secrets: commands and outputs are never printed. Error lines pass through redact().
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import sys
import time

# ---------------------------------------------------------------- paths & config
HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.join(HOME, ".claude")
CLAUDE_PROJECTS = os.path.join(CLAUDE_DIR, "projects")
CLAUDE_HISTORY = os.path.join(CLAUDE_DIR, "history.jsonl")
CLAUDE_SKILLS = os.path.join(CLAUDE_DIR, "skills")
CODEX_SESSIONS = os.path.join(HOME, ".codex", "sessions")
AGENTS_SKILLS = os.path.join(HOME, ".agents", "skills")
# Optional skills source repo (root + local/ scanned in triage, manifest.json read for scope).
# Set SKILLS_REPO, or leave unset to scan only ~/.claude/skills.
SKILLS_REPO = os.environ.get("SKILLS_REPO", "")
REVIEWS_DIR = os.path.join(CLAUDE_DIR, "skill-reviews")
EXCLUDED_FILE = os.path.join(REVIEWS_DIR, "excluded-sessions.txt")

# ---------------------------------------------------------------- BUCKET RULES
# The only place triage buckets are decided. Evaluated top to bottom, first match wins.
#   0. rare-by-nature   : listed in ~/.claude/skill-reviews/rare-by-nature.txt (event-driven
#                         skills such as offboarding; never auto-flagged for retirement)
#   1. retire-candidate : 0 load sessions in the last 120 days
#   2. full             : guards a production CLI (name ends with "-cli" or listed in
#                         PROD_CLI_SKILLS), or in the top FULL_TOP_FRACTION of skills
#                         by 120-day load sessions with at least FULL_MIN_LOADS
#   3. light            : everything else
def _name_list(fname):
    try:
        with open(os.path.join(REVIEWS_DIR, fname), encoding="utf-8") as f:
            return {l.split("#")[0].strip() for l in f if l.split("#")[0].strip()}
    except OSError:
        return set()


PROD_CLI_SKILLS = _name_list("prod-cli-skills.txt")   # extra "guards a prod CLI" skills
RARE_BY_NATURE = _name_list("rare-by-nature.txt")     # event-driven; low use is expected
FULL_TOP_FRACTION = 0.25
FULL_MIN_LOADS = 5
WINDOWS_DAYS = (30, 120)


def bucket_for(name, loads_120, top_threshold):
    if name in RARE_BY_NATURE:
        return "rare-by-nature"
    if loads_120 == 0:
        return "retire-candidate"
    if name.endswith("-cli") or name in PROD_CLI_SKILLS:
        return "full"
    if loads_120 >= max(FULL_MIN_LOADS, top_threshold):
        return "full"
    return "light"


# ---------------------------------------------------------------- small helpers
def norm_path(p):
    return os.path.normcase(os.path.normpath(p)).rstrip("\\/") if p else ""


REVIEWS_NORM = norm_path(REVIEWS_DIR)
MAINT_DIRS = [norm_path(d) for d in (SKILLS_REPO, CLAUDE_SKILLS) if d]


def under(path, root):
    p = norm_path(path)
    return bool(p) and (p == root or p.startswith(root + os.sep))


def norm_skill(name):
    name = (name or "").strip().lstrip("/").strip()
    return name.rsplit(":", 1)[-1]


def ms_to_iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def short(sid):
    return (sid or "?")[:8]


SECRET_RES = [
    (re.compile(r"\b([A-Z][A-Z0-9_]{2,})=(\S+)"), r"\1=<redacted>"),
    (re.compile(r"(?i)\b(bearer|token|authorization|api[_-]?key|password|secret)(\s*[:=]\s*)\S+"), r"\1\2<redacted>"),
    (re.compile(r"\b(xox[abprs]-[\w-]+|sk-[\w-]{10,}|gh[pousr]_\w{10,}|AKIA[0-9A-Z]{12,})"), "<redacted>"),
    (re.compile(r"(?<![\w/.-])[A-Za-z0-9+/_=-]{32,}(?![\w/.-])"), "<redacted>"),
    (re.compile(r"(://[^:/\s]+:)[^@\s]+@"), r"\1<redacted>@"),
]


def redact(s):
    for rx, rep in SECRET_RES:
        s = rx.sub(rep, s)
    return s


# ---------------------------------------------------------------- shell lexer
KEYWORDS = {"then", "do", "else", "elif", "if", "while", "until", "!", "time", "{", "}",
            "function", "fi", "done", "esac"}
PREFIX_WRAPPERS = {"sudo", "exec", "nohup", "command", "builtin", "nice", "winpty",
                   "caffeinate", "unbuffer", "stdbuf", "noglob"}
SHELLS_C = {"bash": "posix", "sh": "posix", "zsh": "posix", "dash": "posix",
            "pwsh": "pwsh", "powershell": "pwsh", "cmd": "posix"}
RE_HEREDOC = re.compile(r"(?<![<\w])<<(-?)\s*(['\"]?)([A-Za-z_][\w.-]*)\2")
RE_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
RE_HERESTR_OPEN = re.compile(r"@['\"]\s*$")
RE_HERESTR_CLOSE = re.compile(r"^\s*['\"]@")


def strip_heredocs(s, kind):
    if "<<" not in s and "@" not in s:
        return s
    lines = s.split("\n")
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        if kind == "posix" and "<<" in line:
            for m in RE_HEREDOC.finditer(line):
                delim = m.group(3)
                while i < len(lines):
                    body = lines[i]
                    i += 1
                    if body.strip() == delim:
                        break
        elif kind == "pwsh" and RE_HERESTR_OPEN.search(line):
            while i < len(lines):
                body = lines[i]
                i += 1
                if RE_HERESTR_CLOSE.match(body):
                    out.append(body.strip()[2:])
                    break
    return "\n".join(out)


def match_paren(s, i):
    """s[i] == '('; return index of the matching ')' (quote-aware, approximate)."""
    depth = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "'" :
            j = s.find("'", i + 1)
            i = n if j < 0 else j + 1
            continue
        if c == '"':
            j = i + 1
            while j < n and s[j] != '"':
                j += 2 if s[j] == "\\" else 1
            i = j + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n - 1


def split_commands(script, kind="posix", depth=0):
    """Return simple commands found in a shell script as (statement_no, words) tuples.

    Commands joined by a pipe share a statement number; ; && || newline ( ) start a new
    one. Commands found inside $(...) / backticks get statement -1."""
    if depth > 5 or not script:
        return []
    s = strip_heredocs(script, kind)
    esc = "`" if kind == "pwsh" else "\\"
    seps = ";&|()\n" + ("{}" if kind == "pwsh" else "")
    cmds, words, cur = [], [], []
    state = {"has": False, "skip_next": False, "stmt": 0}
    n = len(s)
    i = 0

    def end_word():
        if state["has"]:
            w = "".join(cur)
            if state["skip_next"]:
                state["skip_next"] = False
            else:
                words.append(w)
        cur.clear()
        state["has"] = False

    def end_cmd(pipe=False):
        end_word()
        state["skip_next"] = False
        if words:
            cmds.append((state["stmt"], list(words)))
        words.clear()
        if not pipe:
            state["stmt"] += 1

    def subst(start, end):
        cmds.extend((-1, w) for _st, w in split_commands(s[start:end], kind, depth + 1))
        cur.append("$()")
        state["has"] = True

    while i < n:
        c = s[i]
        if c == esc and i + 1 < n:
            if s[i + 1] != "\n":
                cur.append(s[i + 1])
                state["has"] = True
            i += 2
            continue
        if c == "'":
            j = s.find("'", i + 1)
            j = n if j < 0 else j
            cur.append(s[i + 1:j])
            state["has"] = True
            i = j + 1
            continue
        if c == '"':
            i += 1
            while i < n and s[i] != '"':
                ch = s[i]
                if ch == esc and i + 1 < n:
                    cur.append(s[i + 1])
                    i += 2
                    continue
                if ch == "$" and i + 1 < n and s[i + 1] == "(":
                    j = match_paren(s, i + 1)
                    subst(i + 2, j)
                    i = j + 1
                    continue
                if kind == "posix" and ch == "`":
                    j = s.find("`", i + 1)
                    j = n if j < 0 else j
                    subst(i + 1, j)
                    i = j + 1
                    continue
                cur.append(ch)
                i += 1
            state["has"] = True
            i += 1
            continue
        if c == "$" and i + 1 < n and s[i + 1] == "(":
            j = match_paren(s, i + 1)
            subst(i + 2, j)
            i = j + 1
            continue
        if kind == "posix" and c == "`":
            j = s.find("`", i + 1)
            j = n if j < 0 else j
            subst(i + 1, j)
            i = j + 1
            continue
        if c == "#" and not state["has"]:
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        if c in " \t\r":
            end_word()
            i += 1
            continue
        if c in "<>":
            # redirection: drop an fd prefix (2>), the operator, and its target word
            if state["has"] and "".join(cur).isdigit():
                cur.clear()
                state["has"] = False
            end_word()
            j = i
            while j < n and s[j] in "<>":
                j += 1
            if j < n and s[j] == "&":
                j += 1
                while j < n and (s[j].isdigit() or s[j] == "-"):
                    j += 1
            else:
                state["skip_next"] = True
            i = j
            continue
        if c in seps:
            nxt = s[i + 1] if i + 1 < n else ""
            if c == "|" and nxt not in "|":
                end_cmd(pipe=True)
                i += 2 if nxt == "&" else 1
                continue
            end_cmd()
            i += 2 if (c in "|&" and nxt == c) else 1
            continue
        cur.append(c)
        state["has"] = True
        i += 1
    end_cmd()
    return cmds


def base_cmd(w):
    w = w.strip().strip("'\"")
    w = re.split(r"[\\/]", w)[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1"):
        if w.endswith(ext):
            w = w[: -len(ext)]
    return w


def _skip_opts(words, i, with_value=()):
    while i < len(words) and words[i].startswith("-") and words[i] != "--":
        i += 2 if words[i] in with_value else 1
    if i < len(words) and words[i] == "--":
        i += 1
    return i


def command_head(words, kind):
    """Index of the real command word after assignments, keywords and wrappers."""
    i = 0
    while i < len(words):
        w = words[i]
        b = base_cmd(w)
        if kind == "posix" and RE_ASSIGN.match(w):
            i += 1
        elif b in KEYWORDS:
            i += 1
        elif b in PREFIX_WRAPPERS:
            i = _skip_opts(words, i + 1)
        elif b == "env":
            i = _skip_opts(words, i + 1, ("-u", "-C", "-S"))
            while i < len(words) and RE_ASSIGN.match(words[i]):
                i += 1
        elif b in ("timeout", "gtimeout"):
            i = _skip_opts(words, i + 1, ("-s", "-k", "--signal", "--kill-after")) + 1
        elif b == "xargs":
            i = _skip_opts(words, i + 1, ("-I", "-n", "-P", "-d", "-L", "-s", "-E", "-a"))
        elif b == "watch":
            i = _skip_opts(words, i + 1, ("-n", "--interval", "-d"))
        else:
            break
    return i


# Commands that rarely make a compound call fail on their own (used for attribution).
TRIVIAL_CMDS = {"cd", "echo", "printf", "head", "tail", "grep", "rg", "egrep", "jq", "sleep", "cat",
                "sort", "uniq", "wc", "tee", "true", "false", "date", "sed", "awk", "cut", "tr",
                "set", "export", "test", "[", "pwd", "ls", "write-output", "write-host",
                "select-object", "convertfrom-json", "convertto-json", "out-string", "out-null",
                "select-string", "where-object", "foreach-object", "format-table", "format-list",
                "measure-object", "set-location", "$erroractionpreference='continue'", "less", "column"}


def attributable(script, kind, binary, err):
    """Did a failing call plausibly fail because of `binary`? (see module docstring)"""
    if err and binary in err.lower():
        return True
    heads = []
    for st, words in split_commands(script, kind):
        i = command_head(words, kind)
        if i < len(words):
            heads.append((st, base_cmd(words[i])))
    if not heads:
        return False
    if all(h == binary or h in TRIVIAL_CMDS or h.startswith("$") for _st, h in heads):
        return True
    top = [st for st, _h in heads if st >= 0]
    last = max(top) if top else -1
    return any(h == binary for st, h in heads if st == last)


def invocations(script, binary, kind="posix", depth=0):
    """Return the argument lists of every command-position invocation of `binary`."""
    found = []
    if depth > 4:
        return found
    for _st, words in split_commands(script, kind):
        i = command_head(words, kind)
        if i >= len(words):
            continue
        b = base_cmd(words[i])
        if b == binary:
            found.append(words[i + 1:])
        elif b in SHELLS_C:
            rest = words[i + 1:]
            for k, a in enumerate(rest):
                al = a.lower()
                if (al.startswith("-") and "c" in al and len(al) <= 4) or al in ("-command", "/c"):
                    if k + 1 < len(rest):
                        found.extend(invocations(rest[k + 1], binary, SHELLS_C[b], depth + 1))
                    break
    return found


def sub_group(args):
    """(group, is_help) for one invocation's argument list."""
    pos = [a for a in args if not a.startswith("-")]
    is_help = any(a in ("--help", "-h") for a in args) or (pos[:1] == ["help"])
    if pos[:1] == ["help"]:
        pos = pos[1:]
    return (pos[0] if pos else "(root)"), is_help


# ---------------------------------------------------------------- outcome classification
RE_EXIT = re.compile(r"^Exit code (-?\d+)")
RE_TIMEOUT = re.compile(r"(?i)command timed out|did not complete within|timed out after \d+ ?m")
RE_ERRLINE = re.compile(
    r"(?i)\b(error|unauthori[sz]ed|forbidden|not found|failed|failure|denied|invalid|"
    r"unrecognized|unexpected argument|timed out|terminated|no such|cannot|could not|"
    r"couldn't|not authorized|must provide|required|traceback|exception|did not complete|"
    r"no linked project|not linked)\b")
RE_SOFTERR = re.compile(
    r"^\s*(error\b|Error\b|ERROR\b|Unauthorized|Not Authorized|No linked project|"
    r"Failed to|failed to|Project not found|Service not found|Environment .* not found|"
    r"Invalid |error:)")
RE_GREP_PIPE = re.compile(r"\|\s*(grep|rg|egrep|findstr|select-string)\b", re.I)
RE_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\[[0-9;]{1,12}m")
RE_BOILER = re.compile(r"^(Exit code:? -?\d+|Script (failed|completed|error:?)|Wall time|Output:|"
                       r"Total output lines|Warning: truncated|Command running in background|Chunk ID|"
                       r"Process exited with code|Original token count|.*profile\.ps1\b)", re.I)


def output_lines(text):
    lines = (RE_ANSI.sub("", l).strip() for l in text.split("\n"))
    return [l for l in lines if l and not RE_EXIT.match(l) and not RE_BOILER.match(l)]


def first_error_line(text):
    lines = output_lines(text)
    for l in lines:
        if RE_ERRLINE.search(l):
            return l
    return lines[0] if lines else "(no output)"


SOFT_TAG = " [exit 0]"


def group_key(line):
    tag = SOFT_TAG if line.endswith(SOFT_TAG) else ""
    line = redact(line[: len(line) - len(tag)] if tag else line)
    line = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<id>", line, flags=re.I)
    line = re.sub(r"\b[0-9a-f]{7,}\b", "<hex>", line, flags=re.I)
    line = re.sub(r"\d+", "#", line)
    return line[:100] + tag


def classify(text, exit_code, is_error, script, declined=False):
    """-> (status, error_line). status: ok | fail | timeout | blocked | nomatch."""
    text = text or ""
    head = text[:400]
    if declined or head.startswith("The user doesn't want to proceed") or head.startswith("<tool_use_error>"):
        return "blocked", None
    if exit_code is None:
        m = RE_EXIT.match(head)
        if m:
            exit_code = int(m.group(1))
    if RE_TIMEOUT.search(head) or exit_code in (124, 143):
        return "timeout", first_error_line(text)
    if exit_code not in (None, 0):
        body = RE_EXIT.sub("", text, count=1).strip()
        if exit_code == 1 and not body and RE_GREP_PIPE.search(script or ""):
            return "nomatch", None
        return "fail", first_error_line(text)
    if is_error:
        return "fail", first_error_line(text)
    if not RE_GREP_PIPE.search(script or ""):
        for l in output_lines(text)[:3]:
            if RE_SOFTERR.match(l):
                return "fail", l + SOFT_TAG
    return "ok", None


def finish_call(call, text, exit_code, is_error, declined=False):
    """Classify a CLI call's result and decide, per binary, whether a failure is its own."""
    call["status"], call["err"] = classify(text, exit_code, is_error, call["script"], declined)
    call["attr"] = {}
    if call["status"] in ("fail", "timeout"):
        for b in call["inv"]:
            call["attr"][b] = attributable(call["script"], call["kind"], b, call["err"])


# ---------------------------------------------------------------- data model
class Session:
    __slots__ = ("key", "agent", "cwd", "files", "loads", "calls", "first_ts", "sub_files")

    def __init__(self, key, agent):
        self.key, self.agent = key, agent
        self.cwd = None
        self.files = 0
        self.sub_files = 0
        self.loads = []   # (ts, skill, mechanism)
        self.calls = []   # dicts: ts, file, order, inv {bin: [args...]}, status, err
        self.first_ts = None


RE_SKILLMD = re.compile(r"[\\/]+([A-Za-z0-9_.:-]+)[\\/]+SKILL\.md\b")
RE_CMDNAME = re.compile(r"<command-name>/?([^<\s]+)</command-name>")
RE_DOLLAR = re.compile(r"(?<![\w$])\$([a-z][a-z0-9_:-]*[a-z0-9])")
RE_CLAUDE_SUB = re.compile(r"[\\/]([0-9a-f-]{36})[\\/]subagents[\\/]", re.I)


def _block_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") in ("text", "input_text"))
    return ""


# ---------------------------------------------------------------- Claude scan
def iter_claude_files():
    for proj in os.listdir(CLAUDE_PROJECTS):
        pdir = os.path.join(CLAUDE_PROJECTS, proj)
        if not os.path.isdir(pdir):
            continue
        for name in os.listdir(pdir):
            full = os.path.join(pdir, name)
            if name.endswith(".jsonl"):
                yield full, name[:-6], False
            elif os.path.isdir(full):
                sub = os.path.join(full, "subagents")
                if os.path.isdir(sub):
                    for sn in os.listdir(sub):
                        if sn.endswith(".jsonl"):
                            yield os.path.join(sub, sn), name, True


def scan_claude_file(path, clis):
    """One streaming pass. Returns (cwd, first_ts, loads, calls)."""
    clis_b = [c.encode() for c in clis]
    cwd = None
    first_ts = None
    loads, calls = [], []
    pending = {}
    fname = os.path.basename(path)
    with open(path, "rb") as fh:
        for order, line in enumerate(fh):
            if cwd is None and b'"cwd":"' in line:
                m = re.search(rb'"cwd":"((?:[^"\\]|\\.)*)"', line)
                if m:
                    try:
                        cwd = json.loads(b'"' + m.group(1) + b'"')
                    except ValueError:
                        pass
            if first_ts is None:
                k = line.find(b'"timestamp":"')
                if k >= 0:
                    first_ts = line[k + 13:k + 37].decode("ascii", "replace")
            if b'"tool_use"' in line:
                want = (b'"name":"Skill"' in line
                        or (b'SKILL.md' in line and b'"name":"Read"' in line)
                        or (clis_b and b'"command"' in line and any(c in line for c in clis_b)))
                if not want:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                # only real turns; api-request(-blob) records replay old messages
                if d.get("type") != "assistant":
                    continue
                ts = d.get("timestamp") or ""
                content = (d.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for b in content:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    name, inp = b.get("name"), b.get("input") or {}
                    if not isinstance(inp, dict):
                        continue
                    if name == "Skill" and inp.get("skill"):
                        loads.append((ts, norm_skill(inp["skill"]), "skill_tool"))
                    elif name == "Read":
                        m = RE_SKILLMD.search(str(inp.get("file_path", "")))
                        if m and str(inp.get("file_path", "")).endswith("SKILL.md"):
                            loads.append((ts, norm_skill(m.group(1)), "read"))
                    elif clis and isinstance(inp.get("command"), str) and not name.startswith("mcp__"):
                        script = inp["command"]
                        kind = "pwsh" if name == "PowerShell" else "posix"
                        inv = {}
                        for c in clis:
                            if c in script:
                                found = invocations(script, c, kind)
                                if found:
                                    inv[c] = found
                        if inv:
                            call = {"ts": ts, "file": fname, "order": order, "inv": inv, "kind": kind,
                                    "status": "unknown", "err": None, "script": script, "attr": {}}
                            calls.append(call)
                            pending[b.get("id", "")] = call
            elif b"<command-name>" in line and b'"type":"user"' in line:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") != "user" or d.get("isMeta"):
                    continue
                text = _block_text((d.get("message") or {}).get("content")).lstrip()
                if text.startswith("<command-message>") or text.startswith("<command-name>"):
                    m = RE_CMDNAME.search(text[:500])
                    if m:
                        loads.append((d.get("timestamp") or "", norm_skill(m.group(1)), "slash"))
            elif pending and b'"tool_result"' in line:
                hit = [pid for pid in pending if pid and pid.encode() in line]
                if not hit:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") != "user":
                    continue
                content = (d.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                        call = pending.pop(b["tool_use_id"])
                        finish_call(call, _block_text(b.get("content")), None, bool(b.get("is_error")))
    for call in calls:
        call.pop("script", None)
    return cwd, first_ts, loads, calls


# ---------------------------------------------------------------- Codex scan
CODEX_SHELL_FN = re.compile(r"tools\.(shell_command|exec_command|shell)\(\s*")
RE_JS_CMD = re.compile(r"[\"']?(?:command|cmd)[\"']?\s*:\s*(\"(?:[^\"\\]|\\.)*\")")
RE_CODEX_EXIT = re.compile(r"(?:Exit code:|\"exit_code\":)\s*(-?\d+)")


def command_from_list(cmd):
    """Codex command arrays -> (script, kind)."""
    if isinstance(cmd, str):
        return cmd, "pwsh"
    if not isinstance(cmd, list) or not cmd:
        return "", "posix"
    b = base_cmd(str(cmd[0]))
    if b in SHELLS_C and len(cmd) >= 2:
        return str(cmd[-1]), SHELLS_C[b]
    return " ".join(str(x) for x in cmd), "posix"


def js_shell_commands(js):
    out = []
    for m in CODEX_SHELL_FN.finditer(js):
        start = m.end()
        cmd = None
        if start < len(js) and js[start] == "{":
            try:
                obj, _ = json.JSONDecoder().raw_decode(js, start)
                cmd = obj.get("command") or obj.get("cmd")
            except ValueError:
                pass
        if cmd is None:
            mm = RE_JS_CMD.search(js, start, start + 20000)
            if mm:
                try:
                    cmd = json.loads(mm.group(1))
                except ValueError:
                    cmd = None
        if cmd:
            out.append(command_from_list(cmd))
    return out


def scan_codex_file(path, clis):
    meta = {"id": None, "parent": None, "cwd": None, "ts": None}
    loads_ce, loads_call, mentions = [], [], {}
    calls_ce, calls_call = [], []
    pending = {}
    fname = os.path.basename(path)

    def handle_script(ts, order, script, kind, sink_calls, sink_loads):
        for m in RE_SKILLMD.finditer(script):
            sink_loads.append((ts, norm_skill(m.group(1)), "codex_read"))
        inv = {}
        for c in clis:
            if c in script:
                found = invocations(script, c, kind)
                if found:
                    inv[c] = found
        if inv:
            call = {"ts": ts, "file": fname, "order": order, "inv": inv, "kind": kind,
                    "status": "unknown", "err": None, "script": script, "attr": {}}
            sink_calls.append(call)
            return call
        return None

    with open(path, "rb") as fh:
        for order, line in enumerate(fh):
            if order < 3 and meta["id"] is None and (b'"session_meta"' in line or order == 0):
                try:
                    d = json.loads(line)
                    p = d.get("payload", d)
                    if isinstance(p, dict) and p.get("id"):
                        src = p.get("source")
                        parent = p.get("parent_thread_id")
                        if isinstance(src, dict):
                            parent = parent or (((src.get("subagent") or {}) if isinstance(src.get("subagent"), dict) else {}).get("thread_spawn") or {}).get("parent_thread_id")
                        meta.update(id=p.get("id"), parent=parent, cwd=p.get("cwd"), ts=p.get("timestamp") or d.get("timestamp"))
                except ValueError:
                    pass
                continue
            if meta["cwd"] is None and b'"turn_context"' in line:
                try:
                    p = json.loads(line).get("payload") or {}
                    meta["cwd"] = p.get("cwd")
                except ValueError:
                    pass
                continue
            interesting = (b"CommandExecution" in line or b'"function_call' in line
                           or b'"custom_tool_call' in line or b"user_message" in line
                           or b"UserMessage" in line)
            if not interesting:
                continue
            if not (b"SKILL.md" in line or b"$" in line or b"_output" in line or b"_call" in line
                    or (clis and any(c.encode() in line for c in clis))):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            p = d.get("payload", d)
            if not isinstance(p, dict):
                continue
            ts = d.get("timestamp") or meta["ts"] or ""
            t = p.get("type")
            if t == "item_completed" and isinstance(p.get("item"), dict):
                it = p["item"]
                if it.get("type") == "CommandExecution":
                    script, kind = command_from_list(it.get("command"))
                    call = handle_script(ts, order, script, kind, calls_ce, loads_ce)
                    if call:
                        text = it.get("aggregated_output") or ((it.get("stdout") or "") + "\n" + (it.get("stderr") or ""))
                        st = it.get("status")
                        finish_call(call, text, it.get("exit_code"), st == "failed", declined=(st == "declined"))
                elif it.get("type") == "UserMessage" and b"$" in line:
                    text = "\n".join(c.get("text", "") for c in it.get("content") or [] if isinstance(c, dict))
                    for m in RE_DOLLAR.finditer(text):
                        mentions.setdefault((m.group(1), text[:200]), (ts, norm_skill(m.group(1)), "codex_mention"))
            elif t == "user_message" and d.get("type") == "event_msg":
                text = p.get("message") or ""
                if isinstance(text, str):
                    for m in RE_DOLLAR.finditer(text):
                        mentions.setdefault((m.group(1), text[:200]), (ts, norm_skill(m.group(1)), "codex_mention"))
            elif t == "function_call" and p.get("name") == "shell":
                try:
                    args = json.loads(p.get("arguments") or "{}")
                except ValueError:
                    continue
                script, kind = command_from_list(args.get("command"))
                call = handle_script(ts, order, script, kind, calls_call, loads_call)
                if call:
                    pending[p.get("call_id")] = call
            elif t == "custom_tool_call" and p.get("name") == "exec":
                js = p.get("input") or ""
                cmds = js_shell_commands(js)
                merged = None
                for script, kind in cmds:
                    call = handle_script(ts, order, script, kind, calls_call, loads_call)
                    if call and merged is None:
                        merged = call
                    elif call and merged is not None:
                        for k, v in call["inv"].items():
                            merged["inv"].setdefault(k, []).extend(v)
                        merged["script"] += "\n" + call["script"]
                        if merged["kind"] != call["kind"]:
                            merged["kind"] = "posix"
                        calls_call.remove(call)
                if merged:
                    pending[p.get("call_id")] = merged
            elif t in ("function_call_output", "custom_tool_call_output") and p.get("call_id") in pending:
                call = pending.pop(p["call_id"])
                out = p.get("output")
                code = None
                if isinstance(out, str):
                    try:
                        o = json.loads(out)
                        if isinstance(o, dict):
                            code = (o.get("metadata") or {}).get("exit_code")
                            out = o.get("output", out)
                    except ValueError:
                        pass
                    text = out if isinstance(out, str) else str(out)
                else:
                    text = _block_text(out)
                if code is None:
                    codes = [int(x) for x in RE_CODEX_EXIT.findall(text)]
                    code = max(codes, key=abs) if codes else (1 if text.startswith("Script failed") else 0)
                finish_call(call, text, code, False)
    use_ce = bool(calls_ce or loads_ce)
    calls = calls_ce if use_ce else calls_call
    loads = (loads_ce if use_ce else loads_call) + list(mentions.values())
    for c in calls:
        c.pop("script", None)
    return meta, loads, calls


# ---------------------------------------------------------------- collection
def load_excluded():
    """Session-id prefixes, plus cwd prefixes from lines written as `cwd:<path prefix>`."""
    ids, cwds = set(), []
    if os.path.exists(EXCLUDED_FILE):
        with open(EXCLUDED_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if line.lower().startswith("cwd:"):
                    cwds.append(os.path.normcase(os.path.normpath(line[4:].strip())))
                elif line:
                    ids.add(line.lower())
    return ids, cwds


def is_excluded_id(sid, excluded):
    s = (sid or "").lower()
    return any(s.startswith(e) for e in excluded)


def is_excluded_cwd(cwd, prefixes):
    c = norm_path(cwd)
    return bool(c) and (under(cwd, REVIEWS_NORM) or any(c.startswith(p) for p in prefixes))


def _scan_claude_safe(args):
    path, clis = args
    try:
        return scan_claude_file(path, clis)
    except OSError:
        return None, None, [], []


def _scan_codex_safe(args):
    path, clis = args
    try:
        return scan_codex_file(path, clis)
    except OSError:
        return {"id": None, "parent": None, "cwd": None, "ts": None}, [], []


def _scan_all(claude_files, codex_files, clis, jobs):
    """Scan every transcript, in parallel worker processes when jobs > 1."""
    ca = [(p, clis) for p, _sid, _sub in claude_files]
    xa = [(p, clis) for p in codex_files]
    if jobs <= 1:
        return [_scan_claude_safe(a) for a in ca], [_scan_codex_safe(a) for a in xa]
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        cr = ex.map(_scan_claude_safe, ca, chunksize=16)
        xr = ex.map(_scan_codex_safe, xa, chunksize=16)
        return list(cr), list(xr)


def collect(clis, jobs=1):
    t0 = time.time()
    excluded, excl_cwds = load_excluded()
    current = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip().lower()
    stats = collections.Counter()
    coverage = {}
    sessions = {}
    excl_review, excl_listed = set(), set()

    def cov(src, ts):
        if not ts:
            return
        lo, hi = coverage.get(src, (ts, ts))
        coverage[src] = (min(lo, ts), max(hi, ts))

    claude_files = list(iter_claude_files())
    codex_files = [os.path.join(root, fn) for root, _d, files in os.walk(CODEX_SESSIONS)
                   for fn in files if fn.endswith(".jsonl")]
    claude_res, codex_res = _scan_all(claude_files, codex_files, clis, jobs)

    # Claude
    for (path, sid, is_sub), (cwd, first_ts, loads, calls) in zip(claude_files, claude_res):
        stats["claude_files"] += 1
        key = ("claude", sid)
        s = sessions.get(key)
        if s is None:
            s = sessions[key] = Session(sid, "claude")
        s.files += 1
        s.sub_files += is_sub
        if cwd and (s.cwd is None or not is_sub):
            s.cwd = cwd
        if first_ts and (s.first_ts is None or first_ts < s.first_ts):
            s.first_ts = first_ts
        cov("claude", first_ts)
        s.loads.extend(loads)
        s.calls.extend(calls)

    # Codex
    for path, (meta, loads, calls) in zip(codex_files, codex_res):
        stats["codex_files"] += 1
        sid = meta["parent"] or meta["id"] or os.path.basename(path)
        key = ("codex", sid)
        s = sessions.get(key)
        if s is None:
            s = sessions[key] = Session(sid, "codex")
        s.files += 1
        s.sub_files += bool(meta["parent"])
        if meta["cwd"] and (s.cwd is None or not meta["parent"]):
            s.cwd = meta["cwd"]
        if meta["ts"] and (s.first_ts is None or meta["ts"] < s.first_ts):
            s.first_ts = meta["ts"]
        cov("codex", meta["ts"])
        s.loads.extend(loads)
        s.calls.extend(calls)

    # history.jsonl slash commands
    hist = []
    if os.path.exists(CLAUDE_HISTORY):
        with open(CLAUDE_HISTORY, "rb") as fh:
            for line in fh:
                if b'"display":"/' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                m = re.match(r"/([A-Za-z0-9_:.-]+)(?=\s|$)", d.get("display", ""))
                if not m or not d.get("timestamp"):
                    continue
                ts = ms_to_iso(d["timestamp"])
                cov("history", ts)
                hist.append((ts, norm_skill(m.group(1)), d.get("sessionId"), d.get("project")))

    # exclusions
    for key in list(sessions):
        s = sessions[key]
        if is_excluded_cwd(s.cwd, excl_cwds):
            excl_review.add(key)
            del sessions[key]
        elif is_excluded_id(s.key, excluded) or (current and s.key.lower() == current):
            excl_listed.add(key)
            del sessions[key]

    # attach history slash entries (skip ones already seen as a transcript marker)
    for ts, name, hsid, proj in hist:
        if is_excluded_cwd(proj, excl_cwds) or (hsid and (is_excluded_id(hsid, excluded) or hsid.lower() == current)):
            stats["history_excluded"] += 1
            continue
        key = ("claude", hsid or "history:" + ts)
        s = sessions.get(key)
        if s and any(m == "slash" and n == name and abs(_secs(t) - _secs(ts)) < 300 for t, n, m in s.loads):
            continue
        if s is None:
            s = sessions[key] = Session(hsid or "history:" + ts[:19], "claude")
            s.cwd = proj
            s.first_ts = ts
        s.loads.append((ts, name, "slash_history"))

    # drop maintenance reads (editing the skill is not using it)
    for s in sessions.values():
        if any(under(s.cwd, m) for m in MAINT_DIRS):
            before = len(s.loads)
            s.loads = [l for l in s.loads if l[2] not in ("read", "codex_read")]
            stats["maintenance_reads_dropped"] += before - len(s.loads)

    stats["excluded_review_cwd"] = len(excl_review)
    stats["excluded_listed"] = len(excl_listed)
    stats["seconds"] = round(time.time() - t0, 1)
    return {"sessions": sessions, "stats": stats, "coverage": coverage,
            "excluded_ids": sorted(excluded), "excluded_cwds": excl_cwds, "current": current}


def _secs(ts):
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def header_lines(data, since):
    st, cov = data["stats"], data["coverage"]

    def c(src):
        lo, hi = cov.get(src, ("?", "?"))
        return f"{lo[:10]}..{hi[:10]}"
    out = [
        f"Sources: Claude {st['claude_files']} transcripts ({c('claude')}), Codex {st['codex_files']} "
        f"({c('codex')}), history.jsonl slash cmds ({c('history')}). Scan {st['seconds']}s.",
        f"Window: since {since or 'all data'}. Claude transcripts are pruned (~30d), so older Claude "
        f"usage only shows via history.jsonl slash commands.",
        f"Exclusions: cwd under {REVIEWS_DIR} or a cwd: prefix in the file below "
        f"({st['excluded_review_cwd']} sessions); "
        f"{EXCLUDED_FILE} ({len(data['excluded_ids'])} ids + {len(data['excluded_cwds'])} cwd prefixes, {st['excluded_listed']} sessions incl. current "
        f"{short(data['current']) if data['current'] else 'n/a'}); injected skill listings never counted; "
        f"{st['maintenance_reads_dropped']} SKILL.md reads from the skills repo / ~/.claude/skills dropped.",
    ]
    return out


# ---------------------------------------------------------------- report
MECH_ORDER = ["skill_tool", "read", "slash", "slash_history", "codex_read", "codex_mention"]


def build_report(data, skill, since, clis):
    sk = norm_skill(skill)
    sessions = data["sessions"]
    since = since or ""
    mech = collections.defaultdict(lambda: {"events": 0, "sessions": set()})
    last = None
    loaded_sessions = {}
    for key, s in sessions.items():
        for ts, name, m in s.loads:
            if name != sk:
                continue
            if ts >= since:
                mech[m]["events"] += 1
                mech[m]["sessions"].add(key)
            loaded_sessions.setdefault(key, []).append(ts)
            if last is None or ts > last[0]:
                last = (ts, m, s.key)
    rep = {
        "skill": sk, "since": since or None,
        "loads": {
            "events": sum(v["events"] for v in mech.values()),
            "sessions": len(set().union(*[v["sessions"] for v in mech.values()])) if mech else 0,
            "by_mechanism": {m: {"events": mech[m]["events"], "sessions": len(mech[m]["sessions"])}
                             for m in MECH_ORDER if m in mech},
        },
        "last_used": {"ts": last[0], "mechanism": last[1], "session": last[2]} if last else None,
        "cli": {},
    }
    known = set(discover_skills())
    for c in clis:
        rep["cli"][c] = cli_report(sessions, sk, c, since, loaded_sessions, known)
    return rep


def cli_report(sessions, sk, binary, since, loaded_sessions, known):
    per_agent = {}
    fail_groups = {}
    help_counts = collections.Counter()
    help_sessions = set()
    other_skills = collections.Counter()
    blocked = nomatch = elsewhere = 0
    for agent in ("claude", "codex"):
        used, loaded, before = [], [], []
        sub_used = 0
        for key, s in sessions.items():
            if s.agent != agent:
                continue
            calls = [c for c in s.calls if binary in c["inv"]]
            in_win = [c for c in calls if c["ts"] >= since]
            if not in_win:
                continue
            used.append(key)
            sub_used += any(c["file"] != s.key + ".jsonl" for c in in_win) if agent == "claude" else 0
            first_call = min(c["ts"] for c in calls)
            lts = loaded_sessions.get(key)
            if lts:
                loaded.append(s.key)
                if min(lts) <= first_call:
                    before.append(s.key)
            for name in {n for _t, n, _m in s.loads if n != sk and n in known}:
                other_skills[name] += 1
            # failures in window
            for c in in_win:
                if c["status"] in ("fail", "timeout") and not c["attr"].get(binary):
                    elsewhere += 1
                elif c["status"] in ("fail", "timeout"):
                    g = group_key(("timeout: " if c["status"] == "timeout" else "") + (c["err"] or "?"))
                    e = fail_groups.setdefault(g, {"calls": 0, "sessions": []})
                    e["calls"] += 1
                    if s.key not in e["sessions"]:
                        e["sessions"].append(s.key)
                elif c["status"] == "blocked":
                    blocked += 1
                elif c["status"] == "nomatch":
                    nomatch += 1
            # --help timeline, per transcript file
            by_file = collections.defaultdict(list)
            for c in calls:
                by_file[c["file"]].append(c)
            for fcalls in by_file.values():
                fcalls.sort(key=lambda c: c["order"])
                failed_seen = False
                used_groups = set()
                for c in fcalls:
                    for args in c["inv"][binary]:
                        grp, is_help = sub_group(args)
                        if is_help:
                            if c["ts"] >= since:
                                cat = ("reactive" if failed_seen else
                                       "after_use" if grp in used_groups else "before_first_use")
                                help_counts[(agent, cat)] += 1
                                help_sessions.add(key)
                        else:
                            used_groups.add(grp)
                    if c["status"] in ("fail", "timeout") and c["attr"].get(binary):
                        failed_seen = True
        per_agent[agent] = {
            "sessions_using_cli": len(used),
            "subagent_sessions_grouped": sub_used,
            "sessions_loading_skill": len(loaded),
            "loaded_before_first_cli_call": len(before),
            "trigger_rate": round(len(loaded) / len(used), 3) if used else None,
            "loading_session_ids": sorted(loaded),
        }
    groups = sorted(fail_groups.items(), key=lambda kv: (-kv[1]["calls"], kv[0]))
    hc = {a: {k: help_counts[(a, k)] for k in ("before_first_use", "after_use", "reactive")}
          for a in ("claude", "codex")}
    tot = {k: hc["claude"][k] + hc["codex"][k] for k in ("before_first_use", "after_use", "reactive")}
    return {
        "per_agent": per_agent,
        "failures": {
            "failing_calls": sum(g["calls"] for _k, g in groups),
            "groups": [{"first_error_line": k, "calls": g["calls"], "sessions": len(g["sessions"]),
                        "example_sessions": g["sessions"][:3]} for k, g in groups],
            "not_counted": {"blocked_or_rejected": blocked, "grep_no_match": nomatch,
                            "compound_call_failed_elsewhere": elsewhere},
        },
        "help": {
            "by_agent": hc, "total": sum(tot.values()), "sessions": len(help_sessions),
            "proactive": tot["before_first_use"] + tot["after_use"], "reactive": tot["reactive"],
            "before_first_use": tot["before_first_use"], "after_use": tot["after_use"],
        },
        "other_skills_in_cli_sessions": other_skills.most_common(8),
    }


def print_report(data, rep):
    print(f"# skill_usage report: {rep['skill']}")
    for l in header_lines(data, rep["since"]):
        print("  " + l)
    L = rep["loads"]
    lu = rep["last_used"]
    print(f"\nLoads: {L['events']} events in {L['sessions']} sessions. Last used: "
          + (f"{lu['ts'][:10]} via {lu['mechanism']} ({short(lu['session'])})" if lu else "never"))
    for m, v in L["by_mechanism"].items():
        print(f"  {m:<14} {v['events']:>4} events  {v['sessions']:>4} sessions")
    for binary, c in rep["cli"].items():
        print(f"\nCLI `{binary}` (real command positions only):")
        for agent, a in c["per_agent"].items():
            rate = f"{a['trigger_rate']:.0%}" if a["trigger_rate"] is not None else "n/a"
            ids = ", ".join(short(x) for x in a["loading_session_ids"][:6])
            extra = f", {a['subagent_sessions_grouped']} via subagents (grouped)" if a["subagent_sessions_grouped"] else ""
            print(f"  {agent:<6} {a['sessions_using_cli']:>3} sessions ran it{extra}; "
                  f"{a['sessions_loading_skill']} loaded the skill ({rate}), "
                  f"{a['loaded_before_first_cli_call']} before the first call" + (f" [{ids}]" if ids else ""))
        if c["other_skills_in_cli_sessions"]:
            print("  other skills loaded in those sessions: "
                  + ", ".join(f"{n} {k}" for n, k in c["other_skills_in_cli_sessions"]))
        f = c["failures"]
        nc = f["not_counted"]
        print(f"  Failing calls: {f['failing_calls']} (not counted: {nc['compound_call_failed_elsewhere']} "
              f"compound calls that failed elsewhere, {nc['blocked_or_rejected']} blocked/rejected, "
              f"{nc['grep_no_match']} grep no-match)")
        for g in f["groups"][:15]:
            print(f"    {g['calls']:>3} calls {g['sessions']:>2} sess  {g['first_error_line']}"
                  f"  [{', '.join(short(x) for x in g['example_sessions'])}]")
        if len(f["groups"]) > 15:
            print(f"    ... {len(f['groups']) - 15} more groups (--json for all)")
        h = c["help"]
        print(f"  --help: {h['total']} in {h['sessions']} sessions; proactive {h['proactive']} "
              f"(before first use {h['before_first_use']}, after use {h['after_use']}), "
              f"reactive {h['reactive']}  | claude {h['by_agent']['claude']} codex {h['by_agent']['codex']}")


# ---------------------------------------------------------------- triage
def discover_skills():
    found = {}

    def add(root, scope_tag):
        if not os.path.isdir(root):
            return
        for name in os.listdir(root):
            if os.path.isfile(os.path.join(root, name, "SKILL.md")):
                found.setdefault(name, set()).add(scope_tag)
    add(CLAUDE_SKILLS, "live")
    if SKILLS_REPO:
        add(SKILLS_REPO, "repo")
        add(os.path.join(SKILLS_REPO, "local"), "repo-local")
    manifest = {}
    mpath = os.path.join(SKILLS_REPO, "manifest.json") if SKILLS_REPO else ""
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as fh:
            manifest = (json.load(fh) or {}).get("installations", {})
    glob_norm = norm_path(CLAUDE_SKILLS)
    out = {}
    for name, tags in sorted(found.items()):
        targets = [norm_path(t) for t in (manifest.get(name) or {})]
        projects = [t for t in targets if t != glob_norm]
        if "live" in tags or glob_norm in targets:
            scope = "global"
        elif projects:
            scope = f"project-only({len(projects)})"
        else:
            scope = "repo-unsynced"
        if "live" in tags and not ({"repo", "repo-local"} & tags):
            scope += ",not-in-repo"
        if os.path.isdir(os.path.join(AGENTS_SKILLS, name)):
            scope += ",codex"
        out[name] = {"scope": scope, "local": "repo-local" in tags}
    return out


def build_triage(data, since):
    skills = discover_skills()
    now = dt.datetime.now(dt.timezone.utc)
    cut = {d: (now - dt.timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%S") for d in WINDOWS_DAYS}
    since = since or ""
    rows = {}
    for name, info in skills.items():
        rows[name] = {"skill": name, "scope": info["scope"], "last_used": None,
                      **{f"loads_{d}d": set() for d in WINDOWS_DAYS}, "mechanisms": collections.Counter()}
    for key, s in data["sessions"].items():
        for ts, name, m in s.loads:
            r = rows.get(name)
            if r is None or ts < since:
                continue
            r["mechanisms"][m] += 1
            for d in WINDOWS_DAYS:
                if ts >= cut[d]:
                    r[f"loads_{d}d"].add(key)
            if r["last_used"] is None or ts > r["last_used"]:
                r["last_used"] = ts
    for r in rows.values():
        for d in WINDOWS_DAYS:
            r[f"loads_{d}d"] = len(r[f"loads_{d}d"])
    vals = sorted((r["loads_120d"] for r in rows.values()), reverse=True)
    k = max(1, int(round(len(vals) * FULL_TOP_FRACTION)))
    top_threshold = vals[k - 1] if vals else 0
    for name, r in rows.items():
        r["bucket"] = bucket_for(name, r["loads_120d"], top_threshold)
        r["last_used"] = r["last_used"][:10] if r["last_used"] else None
        r["mechanisms"] = dict(r["mechanisms"])
    ordered = sorted(rows.values(), key=lambda r: (-r["loads_120d"], -r["loads_30d"], r["skill"]))
    return {"since": since or None, "top_threshold_120d": max(FULL_MIN_LOADS, top_threshold),
            "rules": {"retire-candidate": "0 load sessions in 120d",
                      "full": f"name ends -cli or in {sorted(PROD_CLI_SKILLS)}, or >= {max(FULL_MIN_LOADS, top_threshold)} "
                              f"load sessions in 120d (top {int(FULL_TOP_FRACTION * 100)}%, min {FULL_MIN_LOADS})",
                      "light": "everything else"},
            "skills": ordered}


def print_triage(data, tri):
    print("# skill_usage triage")
    for l in header_lines(data, tri["since"]):
        print("  " + l)
    print("  Loads = distinct sessions (Claude parent+subagents, Codex thread+subagents, or a history.jsonl slash entry).")
    print("  Buckets: " + "; ".join(f"{k}: {v}" for k, v in tri["rules"].items()))
    print(f"\n{'skill':<22} {'30d':>4} {'120d':>5} {'last used':<11} {'bucket':<17} scope")
    for r in tri["skills"]:
        print(f"{r['skill']:<22} {r['loads_30d']:>4} {r['loads_120d']:>5} {r['last_used'] or '-':<11} "
              f"{r['bucket']:<17} {r['scope']}")
    c = collections.Counter(r["bucket"] for r in tri["skills"])
    print(f"\n{len(tri['skills'])} skills: " + ", ".join(f"{v} {k}" for k, v in sorted(c.items())))


# ---------------------------------------------------------------- main
def _date(s):
    dt.date.fromisoformat(s)
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report", help="usage report for one skill")
    r.add_argument("skill")
    r.add_argument("--since", type=_date)
    r.add_argument("--cli", nargs="+", default=[], help="CLI binaries the skill guards, e.g. railway")
    r.add_argument("--json", action="store_true")
    t = sub.add_parser("triage", help="all skills, usage + suggested review bucket")
    t.add_argument("--since", type=_date)
    t.add_argument("--json", action="store_true")
    for p in (r, t):
        p.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1),
                       help="worker processes for scanning (1 = no multiprocessing)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    clis = [c.lower() for c in getattr(a, "cli", [])]
    data = collect(clis, a.jobs)
    if a.cmd == "report":
        rep = build_report(data, a.skill, a.since, clis)
        if a.json:
            rep["header"] = header_lines(data, a.since)
            print(json.dumps(rep, indent=2, default=list))
        else:
            print_report(data, rep)
    else:
        tri = build_triage(data, a.since)
        if a.json:
            tri["header"] = header_lines(data, a.since)
            print(json.dumps(tri, indent=2, default=list))
        else:
            print_triage(data, tri)


if __name__ == "__main__":
    main()
