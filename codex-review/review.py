#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Lightweight wrapper around the native Windows `codex exec`.

Handles clean output capture via -o flag, auth profile swapping, and result
archiving to ~/reviews/. (Migrated from the WSL-based invocation 2026-08-18 —
Codex CLI now runs natively on Windows.)

Usage:
    uv run review.py -w "C:\\path\\to\\project" "Your review prompt here"
    uv run review.py -w "C:\\path\\to\\project" < prompt.txt
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

# Codex CLI model selection notes (as of 2026-07-13):
# - GPT-5.6 family went GA 2026-07-09: gpt-5.6-sol (flagship), gpt-5.6-terra
#   (balanced, ~GPT-5.5 performance at ~half the cost), gpt-5.6-luna (fast/
#   cheap volume tier). All three work under ChatGPT-account auth; requires
#   Codex CLI >= 0.144.0. gpt-5.5 / gpt-5.4 remain available as previous-gen.
# - Under ChatGPT-account auth, Codex does NOT support the floating gpt-5-codex
#   alias (API-key auth only). ChatGPT users must pass a versioned snapshot.
# - Tiered model strategy (2026-07-13): gpt-5.6-terra for routine work (high) —
#   GPT-5.5-level review quality at roughly the old gpt-5.4 cost. gpt-5.6-sol
#   for hard/ambiguous tasks (xhigh) — the new peak model, ~2x terra per token.
#   Sol also supports "max" and "ultra" reasoning efforts; ultra spawns
#   subagents and burns quota very fast — not used by default here.
# - Override at runtime via CODEX_REVIEW_MODEL (applies to all effort levels).
EFFORT_PRESETS = {
    "high":  {"model": "gpt-5.6-terra", "reasoning": "high"},
    "xhigh": {"model": "gpt-5.6-sol", "reasoning": "xhigh"},
}

# Last-known-latest Codex CLI version. Used to surface a non-blocking warning
# when the user's local CLI is significantly behind. Bump this periodically.
LAST_KNOWN_CODEX_CLI = (0, 147, 0)
LAST_KNOWN_CODEX_CLI_DATE = "2026-08-18"


def slugify(text: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "review"


def check_codex_version() -> None:
    """Print Codex CLI version. Warn (non-blocking) if significantly behind.

    Newer models (e.g. the gpt-5.6 family) require recent CLI versions; surfacing a stale
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
        if current < LAST_KNOWN_CODEX_CLI:
            latest = ".".join(str(n) for n in LAST_KNOWN_CODEX_CLI)
            local = version_str.removeprefix("codex-cli ").strip()
            print(
                f"  ⚠️  Your Codex CLI ({local}) is out of date — last known "
                f"latest is {latest} (checked {LAST_KNOWN_CODEX_CLI_DATE}). "
                f"Newer models (e.g. the gpt-5.6 family) may be rejected.\n"
                f"     We recommend upgrading — re-run the installer in pwsh: "
                f'irm https://chatgpt.com/codex/install.ps1 | iex',
                file=sys.stderr,
            )
    except Exception:
        pass  # Version check is best-effort; never block a review


def resolve_model(effort: str) -> str:
    """Resolve model name: CODEX_REVIEW_MODEL env var overrides preset default."""
    return os.getenv("CODEX_REVIEW_MODEL", EFFORT_PRESETS[effort]["model"])


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


def run_codex(prompt: str, workdir: str | None = None,
              sandbox: str | None = None, timeout: int = 1800,
              effort: str = "high",
              account: str | None = None) -> tuple[str, int, float, str]:
    """Run codex exec natively and return (output, exit_code, elapsed_secs, stderr).

    Default is --yolo mode (no approvals, no sandbox) since nobody is present
    to approve prompts in non-interactive mode. Pass --sandbox to restrict.
    """
    preset = EFFORT_PRESETS[effort]
    model = resolve_model(effort)
    reasoning = preset["reasoning"]
    # Include PID so concurrent reviews launched in the same second don't
    # share (and clobber) each other's output files.
    ts = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
    output_file = Path(tempfile.gettempdir()) / f"codex_review_{ts}.md"

    parts = [CODEX_EXE, "exec"]
    parts.extend(["-m", model])
    parts.extend(["-c", f"model_reasoning_effort={reasoning}"])
    parts.extend(["-c", "model_reasoning_summary=none"])
    parts.append("-")  # read prompt from stdin
    if sandbox:
        parts.extend(["-s", sandbox])
    else:
        parts.append("--yolo")
    parts.extend(["-o", str(output_file)])
    if workdir:
        parts.extend(["-C", workdir])
    else:
        parts.append("--skip-git-repo-check")

    mode = f"sandbox: {sandbox}" if sandbox else "yolo (no approvals/sandbox)"
    print(f"Starting Codex review...", file=sys.stderr)
    print(f"  Directory: {workdir or '(current)'}", file=sys.stderr)
    if account:
        print(f"  Account:   {account}", file=sys.stderr)
    print(f"  Mode:      {mode}", file=sys.stderr)
    print(f"  Effort:    {effort} (model={model}, reasoning={reasoning})", file=sys.stderr)
    print(f"  Timeout:   {timeout // 60}m", file=sys.stderr)
    check_codex_version()

    start = time.time()
    try:
        result = subprocess.run(
            parts, input=prompt,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.time() - start
        print(f"ERROR: Timed out after {int(elapsed)}s", file=sys.stderr)

        # Recover whatever we can from the timed-out process.
        # exc.stdout/.stderr contain output captured before the kill.
        stderr_out = exc.stderr or b""
        if isinstance(stderr_out, bytes):
            stderr_out = stderr_out.decode("utf-8", errors="replace")
        partial = exc.stdout or b""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        partial = partial.strip()

        # Try reading the -o file. Codex writes it only on TaskComplete so
        # it's usually empty after a timeout, but it's free to check.
        if not partial and output_file.exists():
            try:
                partial = output_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass

        if partial:
            print(f"  Recovered {len(partial)} chars of partial output", file=sys.stderr)

        return partial, 1, elapsed, stderr_out.strip()

    elapsed = time.time() - start
    mins, secs = int(elapsed // 60), int(elapsed % 60)
    print(f"  Completed in {mins}m {secs}s (exit code {result.returncode})", file=sys.stderr)

    # Surface stderr from Codex process (auth errors, model errors, crashes)
    if result.stderr and result.stderr.strip():
        stderr_lines = result.stderr.strip().splitlines()
        # Show up to 20 lines to avoid flooding
        preview = stderr_lines[:20]
        print(f"  Codex stderr ({len(stderr_lines)} lines):", file=sys.stderr)
        for line in preview:
            print(f"    {line}", file=sys.stderr)
        if len(stderr_lines) > 20:
            print(f"    ... ({len(stderr_lines) - 20} more lines)", file=sys.stderr)

    # Read clean output from -o file
    response = ""
    if output_file.exists():
        try:
            response = output_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    # Fallback to stdout if -o didn't capture
    if not response and result.stdout:
        response = result.stdout.strip()

    return response, result.returncode, elapsed, result.stderr or ""


def save_review(response: str, workdir: str | None, prompt: str,
                *, status: str = "success", exit_code: int = 0,
                elapsed: float = 0, stderr: str = "",
                effort: str = "high",
                account: str | None = None) -> Path:
    """Save review to ~/reviews/ with YAML frontmatter."""
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    project_slug = slugify(PureWindowsPath(workdir).name) if workdir else "general"

    filepath = REVIEWS_DIR / f"{date_str}_{project_slug}.md"
    counter = 2
    while filepath.exists():
        filepath = REVIEWS_DIR / f"{date_str}_{project_slug}_{counter}.md"
        counter += 1

    mins, secs = int(elapsed // 60), int(elapsed % 60)
    frontmatter = (
        f"---\n"
        f"date: {now.isoformat()}\n"
        f"directory: {workdir or 'N/A'}\n"
        f"reviewer: codex\n"
        f"account: {account or 'default'}\n"
        f"effort: {effort}\n"
        f"model: {resolve_model(effort)}\n"
        f"status: {status}\n"
        f"exit_code: {exit_code}\n"
        f"elapsed: {mins}m {secs}s\n"
        f"---\n\n"
    )

    body = f"## Prompt\n\n{prompt}\n\n---\n\n"
    if status == "success":
        body += f"## Review\n\n{response}\n"
    else:
        body += f"## Failure\n\n**Status:** {status}\n**Exit code:** {exit_code}\n\n"
        if response:
            body += f"### Partial Output\n\n{response}\n\n"
        if stderr:
            body += f"### Stderr\n\n```\n{stderr.strip()}\n```\n"
        if not response and not stderr:
            body += "No output or error details captured.\n"

    filepath.write_text(frontmatter + body, encoding="utf-8")
    return filepath


def main():
    parser = argparse.ArgumentParser(description="Codex CLI review wrapper (native Windows)")
    parser.add_argument("prompt", nargs="?", help="Review prompt (or pipe via stdin)")
    parser.add_argument("-w", "--workdir", help="Project directory (Windows path)")
    parser.add_argument("-s", "--sandbox", default=None,
                        choices=["read-only", "workspace-write"],
                        help="Use sandbox instead of default --yolo mode")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="Timeout in seconds (default: 1800)")
    parser.add_argument("-e", "--effort", default="high",
                        choices=["high", "xhigh"],
                        help="Review effort level (default: high). "
                             "Override model via CODEX_REVIEW_MODEL env var.")
    parser.add_argument("--no-save", action="store_true",
                        help="Don't save results to ~/reviews/")
    parser.add_argument("--accounts", action="store_true",
                        help="List available auth profiles")
    parser.add_argument("--account-setup", metavar="NAME",
                        help="Save current Codex login as a named profile")
    parser.add_argument("-a", "--account", metavar="NAME",
                        help="Use a named auth profile for this review")
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

    with use_profile(args.account):
        response, exit_code, elapsed, stderr = run_codex(
            prompt, args.workdir, args.sandbox, args.timeout, args.effort,
            account=args.account,
        )

    if exit_code != 0:
        status = "error"
        print(f"ERROR: Codex exited with code {exit_code}", file=sys.stderr)
    elif response:
        status = "success"
    else:
        status = "no_output"
        print("ERROR: No response from Codex", file=sys.stderr)

    if not args.no_save:
        try:
            saved = save_review(
                response, args.workdir, prompt,
                status=status, exit_code=exit_code,
                elapsed=elapsed, stderr=stderr,
                effort=args.effort,
                account=args.account,
            )
            print(f"Saved to: {saved}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Could not save: {e}", file=sys.stderr)

    if not response:
        sys.exit(1)

    print(response)


if __name__ == "__main__":
    main()
