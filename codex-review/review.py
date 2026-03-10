#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Lightweight wrapper around `codex exec` via WSL.

Handles WSL invocation (bash -lic for nvm), Windows-to-WSL path conversion,
clean output capture via -o flag, and result archiving to ~/reviews/.

Usage:
    uv run review.py -w "C:\\path\\to\\project" "Your review prompt here"
    uv run review.py -w "C:\\path\\to\\project" < prompt.txt
"""

import argparse
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

REVIEWS_DIR = Path.home() / "reviews"

EFFORT_PRESETS = {
    "standard": {"model": "gpt-5.3-codex", "reasoning": "medium"},
    "high":     {"model": "gpt-5.3-codex", "reasoning": "high"},
    "xhigh":    {"model": "gpt-5.3-codex", "reasoning": "xhigh"},
}


def win_to_wsl(path: str) -> str:
    """Convert a Windows path to a WSL /mnt/ path."""
    p = PureWindowsPath(path)
    if not p.drive:
        return path
    drive = p.drive[0].lower()
    rest = "/".join(p.parts[1:])
    return f"/mnt/{drive}/{rest}"


def slugify(text: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "review"


def run_codex(prompt: str, workdir: str | None = None,
              sandbox: str | None = None, timeout: int = 1800,
              effort: str = "standard") -> tuple[str, int, float, str]:
    """Run codex exec via WSL and return (output, exit_code, elapsed_secs, stderr).

    Default is --yolo mode (no approvals, no sandbox) since nobody is present
    to approve prompts in non-interactive mode. Pass --sandbox to restrict.
    """
    preset = EFFORT_PRESETS[effort]
    model = preset["model"]
    reasoning = preset["reasoning"]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"/tmp/codex_review_{ts}.md"
    prompt_file = f"/tmp/codex_prompt_{ts}.txt"

    # Write prompt to WSL temp file (avoids shell escaping issues)
    use_file = False
    try:
        subprocess.run(
            ["wsl", "bash", "-lc", f"cat > {shlex.quote(prompt_file)}"],
            input=prompt, capture_output=True, text=True, timeout=15,
        )
        use_file = True
    except Exception:
        pass

    # Build codex exec command
    parts = ["codex", "exec"]
    parts.extend(["-m", model])
    parts.extend(["-c", f"model_reasoning_effort={shlex.quote(reasoning)}"])
    parts.extend(["-c", "reasoning_summary=none"])
    if use_file:
        parts.append("-")  # read prompt from stdin (redirected from file)
    if sandbox:
        parts.extend(["-s", sandbox])
    else:
        parts.append("--yolo")
    parts.extend(["-o", output_file])
    if workdir:
        parts.extend(["-C", win_to_wsl(workdir)])
    else:
        parts.append("--skip-git-repo-check")
    if not use_file:
        parts.append(prompt)

    inner = " ".join(shlex.quote(p) for p in parts)
    if use_file:
        inner += f" < {shlex.quote(prompt_file)}"

    mode = f"sandbox: {sandbox}" if sandbox else "yolo (no approvals/sandbox)"
    print(f"Starting Codex review...", file=sys.stderr)
    print(f"  Directory: {workdir or '(current)'}", file=sys.stderr)
    print(f"  Mode:      {mode}", file=sys.stderr)
    print(f"  Effort:    {effort} (model={model}, reasoning={reasoning})", file=sys.stderr)
    print(f"  Timeout:   {timeout // 60}m", file=sys.stderr)

    start = time.time()
    try:
        result = subprocess.run(
            ["wsl", "bash", "-lic", inner],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.time() - start
        print(f"ERROR: Timed out after {int(elapsed)}s", file=sys.stderr)

        # Recover whatever we can from the timed-out process.
        # exc.stdout/.stderr contain output captured before the kill.
        stderr_out = (exc.stderr or "").strip()
        partial = (exc.stdout or "").strip()

        # Try reading the -o file. Codex writes it only on TaskComplete so
        # it's usually empty after a timeout, but it's free to check.
        if not partial:
            try:
                r = subprocess.run(
                    ["wsl", "bash", "-lc", f"cat {shlex.quote(output_file)}"],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0 and r.stdout.strip():
                    partial = r.stdout.strip()
            except Exception:
                pass

        if partial:
            print(f"  Recovered {len(partial)} chars of partial output", file=sys.stderr)

        return partial, 1, elapsed, stderr_out

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
    try:
        r = subprocess.run(
            ["wsl", "bash", "-lc", f"cat {shlex.quote(output_file)}"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            response = r.stdout.strip()
    except Exception:
        pass

    # Fallback to stdout if -o didn't capture
    if not response and result.stdout:
        response = result.stdout.strip()

    return response, result.returncode, elapsed, result.stderr or ""


def save_review(response: str, workdir: str | None, prompt: str,
                *, status: str = "success", exit_code: int = 0,
                elapsed: float = 0, stderr: str = "",
                effort: str = "standard") -> Path:
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
        f"effort: {effort}\n"
        f"model: {EFFORT_PRESETS[effort]['model']}\n"
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
    parser = argparse.ArgumentParser(description="Codex CLI review wrapper (via WSL)")
    parser.add_argument("prompt", nargs="?", help="Review prompt (or pipe via stdin)")
    parser.add_argument("-w", "--workdir", help="Project directory (Windows path)")
    parser.add_argument("-s", "--sandbox", default=None,
                        choices=["read-only", "workspace-write"],
                        help="Use sandbox instead of default --yolo mode")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="Timeout in seconds (default: 1800)")
    parser.add_argument("-e", "--effort", default="standard",
                        choices=["standard", "high", "xhigh"],
                        help="Review effort level (default: standard). "
                             "standard=gpt-5.3+medium, high=gpt-5.3+high, "
                             "xhigh=gpt-5.3+xhigh")
    parser.add_argument("--no-save", action="store_true",
                        help="Don't save results to ~/reviews/")
    args = parser.parse_args()

    if args.prompt:
        prompt = args.prompt
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    else:
        parser.error("Prompt required (pass as argument or pipe via stdin)")
    if not prompt:
        parser.error("Empty prompt")

    response, exit_code, elapsed, stderr = run_codex(
        prompt, args.workdir, args.sandbox, args.timeout, args.effort,
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
            )
            print(f"Saved to: {saved}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Could not save: {e}", file=sys.stderr)

    if not response:
        sys.exit(1)

    print(response)


if __name__ == "__main__":
    main()
