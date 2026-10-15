#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Lightweight wrapper around the native Windows `codex exec`.

Handles clean output capture, session capture for follow-up turns (--resume),
optional managed git worktrees (--worktree), the dual-model "ultra" tier, auth
profile swapping, and result archiving to ~/reviews/. (Migrated from the
WSL-based invocation 2026-08-18 — Codex CLI now runs natively on Windows.)

Usage:
    uv run review.py -w "C:\\path\\to\\project" "Your prompt here"
    uv run review.py -w "C:\\path\\to\\project" < prompt.txt
    uv run review.py --resume <session-id> "Follow-up question"
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

REVIEWS_DIR = Path.home() / "reviews"
CODEX_HOME = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex")))

# Native Windows install location (PS installer). PATH lookup first so a
# newer install elsewhere wins; env var override for exotic setups.
CODEX_EXE = (
    os.getenv("CODEX_EXE")
    or shutil.which("codex")
    or str(Path.home() / "AppData/Local/Programs/OpenAI/Codex/bin/codex.exe")
)

# Codex CLI model selection notes (as of 2026-10-15):
# - GPT-6 Astra (gpt-6-astra, GA 2026-09-04): flagship, $10/$50 per 1M API
#   tokens. Uses ~1/3 the tokens of sol in the Codex harness, so it finishes
#   sooner but explores less. Higher precision, lower recall than 5.6 sol on
#   an independent 50-PR review benchmark; the two find largely different bugs.
# - GPT-5.6 sol stays the thorough single-model tier (best measured review
#   recall, incl. low-severity and security finds). Terra is the fast default.
# - GPT-6.1 Sol (gpt-6.1-sol, 2026-09-29) is Codex's own default since CLI
#   0.161 but has no review benchmark yet; not in any preset. Try it via
#   CODEX_REVIEW_MODEL.
# - "ultra" runs sol@xhigh and astra@high in parallel (two sessions, two saved
#   files). Astra above `high` bought no new findings in our 2026-09-11
#   benchmark, and OpenAI's guide puts "careful review" at high.
# - Under ChatGPT-account auth, Codex does NOT support the floating gpt-5-codex
#   alias (API-key auth only). ChatGPT users must pass a versioned snapshot.
# Per-tier default timeout: xhigh reviews that explore data empirically
# routinely blow past 30 min (observed 2026-08-31), so sol/astra get an hour.
EFFORT_PRESETS = {
    "medium": {"model": "gpt-5.6-terra", "reasoning": "high", "timeout": 1800},
    "high":   {"model": "gpt-5.6-sol", "reasoning": "xhigh", "timeout": 3600},
    "astra":  {"model": "gpt-6-astra", "reasoning": "high", "timeout": 3600},
}
# "ultra" is not a preset of its own: it runs these tiers in parallel.
ULTRA_LEGS = ("high", "astra")
TIERS = (*EFFORT_PRESETS, "ultra")
# Minimum Codex CLI that accepts each model family (server rejects older clients).
MODEL_MIN_CLI = {
    "gpt-6-": (0, 153, 0),
    "gpt-5.6-": (0, 144, 0),
}
# Spoken-language aliases so "use normal", "xhigh", "max it out" all resolve.
EFFORT_ALIASES = {
    "normal": "medium", "regular": "medium", "casual": "medium",
    "default": "medium", "med": "medium",
    "xhigh": "high", "extra-high": "high", "extrahigh": "high", "sol": "high",
    "max": "ultra", "maximum": "ultra", "xxhigh": "ultra", "dual": "ultra",
}
VALID_REASONING = ("low", "medium", "high", "xhigh", "max", "ultra")
SANDBOXES = ("read-only", "workspace-write")


def resolve_effort(name: str) -> str:
    """Map a tier name or alias to its canonical key."""
    key = name.strip().lower()
    key = EFFORT_ALIASES.get(key, key)
    if key not in TIERS:
        raise argparse.ArgumentTypeError(
            f"unknown effort {name!r}; use one of "
            f"{', '.join(TIERS)} (aliases: {', '.join(EFFORT_ALIASES)})"
        )
    return key

# Last-known-latest Codex CLI version. Used to surface a non-blocking warning
# when the user's local CLI is two or more minor versions behind. Bump this
# periodically. (A model-specific minimum, MODEL_MIN_CLI, warns separately.)
LAST_KNOWN_CODEX_CLI = (0, 163, 0)
LAST_KNOWN_CODEX_CLI_DATE = "2026-10-15"


def slugify(text: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "review"


def check_codex_version(model: str = "") -> None:
    """Warn (non-blocking) if the Codex CLI is too old for `model` or well behind latest.

    Newer models (e.g. gpt-6-astra) require recent CLI versions; surfacing a stale
    install proactively saves a confusing "requires a newer version of Codex"
    error round-trip.
    """
    try:
        r = subprocess.run(
            [CODEX_EXE, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        version_str = (r.stdout or "").strip()
        # Anchor on the codex-cli prefix so we don't misparse if future output
        # includes other semver-shaped strings (e.g. update notices).
        m = re.search(r"codex-cli\s+v?(\d+)\.(\d+)\.(\d+)", version_str, re.IGNORECASE)
        if not m:
            return
        current = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        local = version_str.removeprefix("codex-cli ").strip()
        upgrade_hint = (
            "     We recommend upgrading — re-run the installer in pwsh: "
            "irm https://chatgpt.com/codex/install.ps1 | iex"
        )
        needed = next((v for prefix, v in MODEL_MIN_CLI.items() if model.startswith(prefix)), None)
        if needed and current < needed:
            print(
                f"  ⚠️  Your Codex CLI ({local}) is older than {'.'.join(map(str, needed))}, "
                f"the minimum for {model}. The server will probably reject this run.\n"
                + upgrade_hint,
                file=sys.stderr,
            )
        elif current[:2] < (LAST_KNOWN_CODEX_CLI[0], LAST_KNOWN_CODEX_CLI[1] - 1):
            latest = ".".join(str(n) for n in LAST_KNOWN_CODEX_CLI)
            print(
                f"  ⚠️  Your Codex CLI ({local}) is well behind the last known "
                f"latest {latest} (checked {LAST_KNOWN_CODEX_CLI_DATE}).\n"
                + upgrade_hint,
                file=sys.stderr,
            )
    except Exception:
        pass  # Version check is best-effort; never block a review


def resolve_model(effort: str) -> str:
    """Resolve model name: CODEX_REVIEW_MODEL env var overrides preset default."""
    return os.getenv("CODEX_REVIEW_MODEL", EFFORT_PRESETS[effort]["model"])


def resolve_reasoning(effort: str, override: str | None = None) -> str:
    """Resolve reasoning effort: --reasoning flag > CODEX_REVIEW_REASONING > preset."""
    value = override or os.getenv("CODEX_REVIEW_REASONING") or EFFORT_PRESETS[effort]["reasoning"]
    value = value.strip().lower()
    if value not in VALID_REASONING:
        raise SystemExit(f"invalid reasoning effort {value!r}; use one of {', '.join(VALID_REASONING)}")
    return value


# ---------------------------------------------------------------------------
# Auth profile management
# ---------------------------------------------------------------------------

def _jwt_claims(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _read_auth(path: Path | None = None) -> dict | None:
    path = path or (CODEX_HOME / "auth.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _validate_profile_name(name: str) -> None:
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        print("Profile name must contain only letters, digits, hyphens, underscores.",
              file=sys.stderr)
        sys.exit(1)


def list_profiles() -> None:
    current = _read_auth()
    current_id = (current or {}).get("tokens", {}).get("account_id")

    profiles = sorted(CODEX_HOME.glob("*.auth.json"))
    profiles = [p for p in profiles if not p.name.startswith("auth.json")]
    if not profiles:
        print("No profiles configured. Use --account-setup <name> to save the current login.")
        return

    for fpath in profiles:
        name = fpath.name.removesuffix(".auth.json")
        profile = _read_auth(fpath)
        active = False
        details = []
        if profile:
            tok = profile.get("tokens", {})
            active = tok.get("account_id") == current_id and current_id is not None
            claims = _jwt_claims(tok.get("id_token", ""))
            auth_info = claims.get("https://api.openai.com/auth", {})
            plan = auth_info.get("chatgpt_plan_type", "")
            if plan:
                details.append(plan)
            email = claims.get("email", "")
            if email:
                details.append(email)
        desc = f" — {', '.join(details)}" if details else ""
        marker = " (active)" if active else ""
        print(f"  {name}{desc}{marker}")


def setup_profile(name: str) -> None:
    auth_file = CODEX_HOME / "auth.json"
    if not auth_file.exists():
        print("No auth.json found. Run 'codex login' first.", file=sys.stderr)
        sys.exit(1)
    dest = CODEX_HOME / f"{name}.auth.json"
    try:
        shutil.copyfile(auth_file, dest)
    except OSError as e:
        print(f"Failed to save profile: {e}", file=sys.stderr)
        sys.exit(1)
    auth = _read_auth()
    claims = _jwt_claims((auth or {}).get("tokens", {}).get("id_token", ""))
    plan = claims.get("https://api.openai.com/auth", {}).get("chatgpt_plan_type", "")
    label = f" ({plan})" if plan else ""
    print(f"Saved current auth as '{name}'{label}")


@contextmanager
def use_profile(name: str | None):
    """Swap auth.json to a named profile for the duration, then restore."""
    if name is None:
        yield
        return

    src = CODEX_HOME / f"{name}.auth.json"
    if not src.exists():
        print(f"Profile '{name}' not found. Use --accounts to list.", file=sys.stderr)
        sys.exit(1)

    auth_file = CODEX_HOME / "auth.json"
    backup = CODEX_HOME / "auth.json.bak"
    shutil.copyfile(auth_file, backup)
    shutil.copyfile(src, auth_file)
    try:
        yield
    finally:
        # Save any token refreshes back to the profile, then restore original.
        shutil.copyfile(auth_file, src)
        shutil.copyfile(backup, auth_file)
        backup.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Sessions (follow-up turns)
# ---------------------------------------------------------------------------

def parse_events(stdout: str) -> dict:
    """Pull thread id, last agent message, file edits and warnings from --json output."""
    info = {"thread_id": None, "message": "", "files": [], "warnings": []}
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        etype = ev.get("type")
        if etype == "thread.started":
            info["thread_id"] = ev.get("thread_id")
        elif etype in ("error", "turn.failed"):
            # Fatal: e.g. a model refusal or the cyber-safety filter stopping the turn.
            err = ev.get("error") if isinstance(ev.get("error"), dict) else {}
            msg = (err.get("message") or ev.get("message") or etype).strip()
            if f"FATAL: {msg}" not in info["warnings"]:
                info["warnings"].append(f"FATAL: {msg}")
        elif etype == "item.completed":
            item = ev.get("item", {})
            kind = item.get("type")
            if kind == "agent_message":
                info["message"] = item.get("text", "")
            elif kind == "file_change" and item.get("status") != "failed":
                for ch in item.get("changes", []):
                    entry = f"{ch.get('kind', '?')} {ch.get('path', '?')}"
                    if entry not in info["files"]:
                        info["files"].append(entry)
            elif kind == "error" and item.get("message"):
                if item["message"].strip() not in info["warnings"]:
                    info["warnings"].append(item["message"].strip())
    return info


def session_cwd(thread_id: str) -> str | None:
    """Working dir Codex actually used, from the session's rollout file.

    Differs from --workdir under --worktree, and `codex exec resume` has no -C:
    it runs wherever it is launched, so resumes must start from this directory.
    """
    sessions = CODEX_HOME / "sessions"
    for f in sorted(sessions.rglob(f"rollout-*{thread_id}.jsonl"), reverse=True):
        try:
            with f.open(encoding="utf-8") as fh:
                meta = json.loads(fh.readline())
            return (meta.get("payload") or meta).get("cwd")
        except Exception:
            continue
    return None


def _frontmatter(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            head = fh.read(2000)
    except OSError:
        return {}
    if not head.startswith("---"):
        return {}
    return dict(re.findall(r"^(\w+): (.*)$", head.split("---", 2)[1], re.M))


def find_session_review(session: str, workdir: str | None) -> tuple[Path | None, dict]:
    """Find the saved review for a session id, or the newest one for `last`."""
    files = sorted(REVIEWS_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        fm = _frontmatter(f)
        sid = fm.get("session_id")
        if not sid or sid == "none":
            continue
        if session == "last":
            if workdir and fm.get("directory", "").lower() != workdir.lower():
                continue
            return f, fm
        if sid == session:
            return f, fm
    return None, {}


# ---------------------------------------------------------------------------
# Running Codex
# ---------------------------------------------------------------------------

def run_codex(prompt: str, *, model: str, reasoning: str,
              workdir: str | None = None, sandbox: str | None = None,
              timeout: int = 1800, worktree: bool = False,
              resume_id: str | None = None, label: str = "") -> dict:
    """Run one `codex exec` (or `exec resume`) turn and return a result dict.

    Default is no sandbox and no approvals, since nobody is present to approve
    prompts in non-interactive mode. Pass `sandbox` to restrict.
    """
    # Include PID + label so concurrent runs (and the two ultra legs) don't
    # share (and clobber) each other's output files.
    ts = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}{label}"
    output_file = Path(tempfile.gettempdir()) / f"codex_review_{ts}.md"

    parts = [CODEX_EXE, "exec"]
    if resume_id:
        parts.extend(["resume", resume_id])
    parts.extend(["-m", model])
    parts.extend(["-c", f"model_reasoning_effort={reasoning}"])
    parts.extend(["-c", "model_reasoning_summary=none"])
    if sandbox and resume_id:
        # `exec resume` has no -s and does NOT inherit the first turn's sandbox
        # (openai/codex#40149): a resumed read-only session can write. Re-pass it.
        parts.extend(["-c", f'sandbox_mode="{sandbox}"'])
    elif sandbox:
        parts.extend(["-s", sandbox])
    else:
        parts.append("--dangerously-bypass-approvals-and-sandbox")
    if workdir and not resume_id:
        parts.extend(["-C", workdir])
    if worktree:
        parts.append("--worktree")
    parts.extend(["--json", "-o", str(output_file), "--skip-git-repo-check", "-"])

    start = time.time()
    timed_out = False
    proc = subprocess.Popen(
        parts, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        # resume runs wherever it is launched (no -C); see session_cwd().
        cwd=workdir if resume_id and workdir else None,
    )
    try:
        stdout, stderr = proc.communicate(prompt, timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        # Kill the whole tree: Codex may have started test runners or dev
        # servers that would otherwise keep running (and writing) after we exit.
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            proc.kill()
        try:
            stdout, stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
        code = 1
    stdout, stderr = stdout or "", stderr or ""
    elapsed = time.time() - start

    info = parse_events(stdout)
    # -o holds the final message; it's only written on TaskComplete, so after a
    # timeout the last agent_message from the event stream is the best partial.
    response = ""
    if output_file.exists():
        try:
            response = output_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    if not response:
        response = info["message"].strip()

    mins, secs = int(elapsed // 60), int(elapsed % 60)
    tag = f" [{label.strip('_')}]" if label else ""
    if timed_out:
        print(f"ERROR{tag}: Timed out after {int(elapsed)}s", file=sys.stderr)
        if response:
            print(f"  Recovered {len(response)} chars of partial output", file=sys.stderr)
    else:
        print(f"  Completed{tag} in {mins}m {secs}s (exit code {code})", file=sys.stderr)

    # Surface stderr from Codex process (auth errors, model errors, crashes)
    noise = re.compile(r"oauth::refresh_transaction|failed to refresh OAuth tokens")
    stderr_lines = [l for l in stderr.strip().splitlines() if l.strip() and not noise.search(l)]
    if stderr_lines:
        print(f"  Codex stderr{tag} ({len(stderr_lines)} lines):", file=sys.stderr)
        for line in stderr_lines[:20]:
            print(f"    {line}", file=sys.stderr)
        if len(stderr_lines) > 20:
            print(f"    ... ({len(stderr_lines) - 20} more lines)", file=sys.stderr)

    thread_id = info["thread_id"] or resume_id
    cwd = session_cwd(thread_id) if thread_id else None
    if cwd is None and not worktree:
        # Under --worktree the main checkout is never the right answer.
        cwd = workdir
    return {
        "response": response, "exit_code": code, "elapsed": elapsed,
        "stderr": stderr, "timed_out": timed_out, "thread_id": thread_id,
        "cwd": cwd, "files": info["files"], "warnings": info["warnings"],
        "model": model, "reasoning": reasoning,
    }


def status_of(res: dict) -> str:
    if res["timed_out"]:
        return "timeout"
    if res["exit_code"] != 0:
        return "error"
    return "success" if res["response"] else "no_output"


def save_review(res: dict, workdir: str | None, prompt: str, *,
                effort: str, sandbox: str | None, account: str | None,
                worktree: bool, leg: str = "") -> Path:
    """Save a new review to ~/reviews/ with YAML frontmatter."""
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    project_slug = slugify(PureWindowsPath(workdir).name) if workdir else "general"
    if leg:
        project_slug += f"_{leg}"

    status = status_of(res)
    mins, secs = int(res["elapsed"] // 60), int(res["elapsed"] % 60)
    frontmatter = (
        f"---\n"
        f"date: {now.isoformat()}\n"
        f"directory: {workdir or 'N/A'}\n"
        f"reviewer: codex\n"
        f"account: {account or 'default'}\n"
        f"effort: {effort}\n"
        f"model: {res['model']}\n"
        f"reasoning: {res['reasoning']}\n"
        f"sandbox: {sandbox or 'none'}\n"
        f"session_id: {res['thread_id'] or 'none'}\n"
        f"codex_cwd: {res['cwd'] or 'N/A'}\n"
        f"worktree: {'yes' if worktree else 'no'}\n"
        f"turns: 1\n"
        f"status: {status}\n"
        f"exit_code: {res['exit_code']}\n"
        f"elapsed: {mins}m {secs}s\n"
        f"---\n\n"
    )

    body = f"## Prompt\n\n{prompt}\n\n---\n\n" + _result_body(res, status, "##")
    # Exclusive create, so concurrent runs on the same project can't both
    # claim the same free filename and overwrite each other.
    counter = 1
    while True:
        suffix = f"_{counter}" if counter > 1 else ""
        filepath = REVIEWS_DIR / f"{date_str}_{project_slug}{suffix}.md"
        try:
            with filepath.open("x", encoding="utf-8") as fh:
                fh.write(frontmatter + body)
            return filepath
        except FileExistsError:
            counter += 1


def _result_body(res: dict, status: str, h: str) -> str:
    if status == "success":
        body = f"{h} Review\n\n{res['response']}\n"
    else:
        body = f"{h} Failure\n\n**Status:** {status}\n**Exit code:** {res['exit_code']}\n\n"
        if res["response"]:
            body += f"{h}# Partial Output\n\n{res['response']}\n\n"
        if res["stderr"].strip():
            body += f"{h}# Stderr\n\n```\n{res['stderr'].strip()}\n```\n"
    if res["files"]:
        body += f"\n{h}# Files changed by Codex\n\n" + "".join(f"- {f}\n" for f in res["files"])
    return body


def append_followup(path: Path, prompt: str, res: dict) -> None:
    """Append a resumed turn to the session's saved review and bump `turns`."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^turns: (\d+)$", text, re.M)
    turn = int(m.group(1)) + 1 if m else 2
    if m:
        text = text[:m.start()] + f"turns: {turn}" + text[m.end():]
    status = status_of(res)
    mins, secs = int(res["elapsed"] // 60), int(res["elapsed"] % 60)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    text += (
        f"\n---\n\n## Turn {turn} ({stamp}, {mins}m {secs}s, {status})\n\n"
        f"### Prompt\n\n{prompt}\n\n" + _result_body(res, status, "###")
    )
    path.write_text(text, encoding="utf-8")


def footer(res: dict, worktree: bool) -> str:
    """Session handle printed after the reply (or failure) so the caller can follow up."""
    lines = []
    status = status_of(res)
    if status != "success":
        lines.append(f"STATUS: {status} (exit code {res['exit_code']})")
    lines.extend(w.splitlines()[0] for w in res["warnings"] if w.startswith("FATAL"))
    if res["files"]:
        lines.append("Files changed by Codex (patch edits; shell writes are not listed): "
                     + "; ".join(res["files"]))
    if worktree:
        lines.append(f"Codex working dir (inside its worktree, detached HEAD): "
                     f"{res['cwd'] or 'unknown - check git worktree list'}")
    if res["thread_id"]:
        lines.append(f"Codex session: {res['thread_id']}  "
                     f"(follow up: uv run review.py --resume {res['thread_id']} \"...\")")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Codex CLI review/consult wrapper (native Windows)")
    parser.add_argument("prompt", nargs="?", help="Prompt (or pipe via stdin)")
    parser.add_argument("-w", "--workdir", help="Project directory (Windows path)")
    parser.add_argument("-s", "--sandbox", default=None, choices=SANDBOXES,
                        help="Use a sandbox instead of the default no-sandbox mode")
    parser.add_argument("--timeout", type=int, default=None,
                        help="Timeout in seconds (default: per tier - "
                             "1800 for medium, 3600 for the others)")
    parser.add_argument("-e", "--effort", default="medium", type=resolve_effort,
                        help="Tier: medium (default) = gpt-5.6-terra@high, "
                             "high (alias xhigh) = gpt-5.6-sol@xhigh, astra = gpt-6-astra@high, "
                             "ultra (aliases max/xxhigh) = high + astra in parallel. "
                             "Override model via CODEX_REVIEW_MODEL env var.")
    parser.add_argument("--reasoning", default=None,
                        help="Override the tier's reasoning effort "
                             f"({'/'.join(VALID_REASONING)}). Env: CODEX_REVIEW_REASONING.")
    parser.add_argument("--worktree", action="store_true",
                        help="Run in a new Codex-managed git worktree (detached HEAD) so "
                             "Codex's edits can't collide with yours")
    parser.add_argument("--resume", metavar="SESSION_ID",
                        help="Send a follow-up turn to an earlier session (id from the "
                             "previous output, or 'last' for the newest saved one, "
                             "filtered by -w if given)")
    parser.add_argument("--no-save", action="store_true",
                        help="Don't save results to ~/reviews/")
    parser.add_argument("--accounts", action="store_true",
                        help="List available auth profiles")
    parser.add_argument("--account-setup", metavar="NAME",
                        help="Save current Codex login as a named profile")
    parser.add_argument("-a", "--account", metavar="NAME",
                        help="Use a named auth profile for this run")
    args = parser.parse_args()

    if args.accounts:
        list_profiles()
        return

    if args.account_setup:
        _validate_profile_name(args.account_setup)
        setup_profile(args.account_setup)
        return

    if args.account:
        _validate_profile_name(args.account)

    if args.prompt:
        prompt = args.prompt
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    else:
        parser.error("Prompt required (pass as argument or pipe via stdin)")
    if not prompt:
        parser.error("Empty prompt")

    if args.resume:
        return resume_main(args, prompt)

    if args.effort == "ultra" and args.worktree:
        parser.error("--worktree is for delegated edits; use a single tier, not ultra")
    if args.worktree:
        # Managed worktrees start from committed HEAD: staged, modified and
        # untracked work is all absent from Codex's copy.
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=args.workdir,
                               capture_output=True, text=True).stdout.strip()
        if dirty:
            n = len(dirty.splitlines())
            print(f"  ⚠️  {n} uncommitted change(s) in {args.workdir or 'the current dir'} will "
                  f"NOT be in Codex's worktree (it starts from committed HEAD)", file=sys.stderr)
    if args.effort == "ultra" and os.getenv("CODEX_REVIEW_MODEL"):
        print("  ⚠️  CODEX_REVIEW_MODEL is ignored for ultra (it would make both legs "
              "the same model)", file=sys.stderr)

    legs = ULTRA_LEGS if args.effort == "ultra" else (args.effort,)
    plans = []
    for leg in legs:
        model = (EFFORT_PRESETS[leg]["model"] if args.effort == "ultra"
                 else resolve_model(leg))
        reasoning = resolve_reasoning(leg, args.reasoning)
        timeout = args.timeout or EFFORT_PRESETS[leg]["timeout"]
        plans.append((leg, model, reasoning, timeout))

    mode = f"sandbox: {args.sandbox}" if args.sandbox else "no sandbox, no approvals"
    print("Starting Codex...", file=sys.stderr)
    print(f"  Directory: {args.workdir or '(current)'}"
          + ("  (in a new managed worktree)" if args.worktree else ""), file=sys.stderr)
    if args.account:
        print(f"  Account:   {args.account}", file=sys.stderr)
    print(f"  Mode:      {mode}", file=sys.stderr)
    for leg, model, reasoning, timeout in plans:
        print(f"  Effort:    {leg} (model={model}, reasoning={reasoning}, "
              f"timeout {timeout // 60}m)", file=sys.stderr)
        check_codex_version(model)

    def run(plan):
        leg, model, reasoning, timeout = plan
        return run_codex(prompt, model=model, reasoning=reasoning, workdir=args.workdir,
                         sandbox=args.sandbox, timeout=timeout, worktree=args.worktree,
                         label=f"_{leg}" if len(plans) > 1 else "")

    with use_profile(args.account):
        if len(plans) > 1:
            with ThreadPoolExecutor(max_workers=len(plans)) as pool:
                results = list(pool.map(run, plans))
        else:
            results = [run(plans[0])]

    outputs = []
    for (leg, model, reasoning, _), res in zip(plans, results):
        status = status_of(res)
        if status != "success":
            print(f"ERROR [{leg}]: {status} (exit code {res['exit_code']})", file=sys.stderr)
        for w in res["warnings"]:
            print(f"  Codex warning [{leg}]: {w.splitlines()[0]}", file=sys.stderr)
        if not args.no_save:
            try:
                # Ultra legs record their own tier so --resume restores the right preset.
                saved = save_review(res, args.workdir, prompt, effort=leg,
                                    sandbox=args.sandbox, account=args.account,
                                    worktree=args.worktree,
                                    leg=leg if len(plans) > 1 else "")
                print(f"Saved to: {saved}", file=sys.stderr)
            except Exception as e:
                print(f"Warning: Could not save: {e}", file=sys.stderr)
        text = res["response"] or "(no reply from Codex)"
        if len(plans) > 1:
            text = f"# {leg}: {model} @ {reasoning}\n\n{text}"
        outputs.append(text + "\n\n---\n" + footer(res, args.worktree))

    # Always print, so session ids and worktree paths survive failures; exit
    # non-zero if any leg failed, even when another leg (or a partial) has text.
    print("\n\n".join(outputs))
    if any(status_of(r) != "success" for r in results):
        sys.exit(1)


def resume_main(args, prompt: str) -> None:
    path, fm = find_session_review(args.resume, args.workdir)
    if path is None and args.resume == "last":
        sys.exit("No saved review with a session id found"
                 + (f" for {args.workdir}" if args.workdir else ""))
    session = fm.get("session_id") or args.resume
    if path is None:
        print(f"  ⚠️  No saved review for session {session}; using -e/-s from this "
              f"command line (default medium, no sandbox)", file=sys.stderr)
    sandbox = fm.get("sandbox") if path else args.sandbox
    sandbox = args.sandbox or (sandbox if sandbox in SANDBOXES else None)
    fm_effort = fm.get("effort")
    leg = fm_effort if fm_effort in EFFORT_PRESETS else args.effort
    if leg == "ultra":
        leg = "high"
    model = fm.get("model") or resolve_model(leg)
    reasoning = resolve_reasoning(leg, args.reasoning or fm.get("reasoning"))
    cwd = session_cwd(session) or (fm.get("codex_cwd") if fm.get("codex_cwd") != "N/A" else None) \
        or fm.get("directory") or args.workdir
    if cwd == "N/A":
        cwd = None
    if cwd and not Path(cwd).is_dir():
        sys.exit(f"Session {session} ran in {cwd}, which no longer exists (worktree removed?). "
                 f"Start a fresh run instead.")
    timeout = args.timeout or EFFORT_PRESETS[leg]["timeout"]
    worktree = fm.get("worktree") == "yes"

    print(f"Resuming Codex session {session}...", file=sys.stderr)
    print(f"  Directory: {cwd or '(current)'}", file=sys.stderr)
    print(f"  Mode:      {'sandbox: ' + sandbox if sandbox else 'no sandbox, no approvals'}",
          file=sys.stderr)
    print(f"  Model:     {model} (reasoning={reasoning}, timeout {timeout // 60}m)", file=sys.stderr)

    account = args.account or (fm.get("account") if fm.get("account") not in (None, "default") else None)
    with use_profile(account):
        res = run_codex(prompt, model=model, reasoning=reasoning, workdir=cwd,
                        sandbox=sandbox, timeout=timeout, resume_id=session)

    status = status_of(res)
    if status != "success":
        print(f"ERROR: {status} (exit code {res['exit_code']})", file=sys.stderr)
    if path and not args.no_save:
        try:
            append_followup(path, prompt, res)
            print(f"Appended to: {path}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Could not save: {e}", file=sys.stderr)
    print((res["response"] or "(no reply from Codex)") + "\n\n---\n" + footer(res, worktree))
    if status != "success":
        sys.exit(1)


if __name__ == "__main__":
    main()
