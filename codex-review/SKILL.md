---
name: codex-review
description: Get an independent review from Codex CLI (OpenAI, via WSL) as a second opinion. Use after implementing features, completing plans, or finishing research/analysis to sanity-check work. Runs 1-5 minutes in background (longer for complex reviews). Invoke when user asks for a sanity check, second opinion, or Codex review.
---

# Codex Review

Invoke OpenAI Codex CLI (via WSL) as an independent reviewer. Provides a "second set of eyes" from a different model to catch gaps, bugs, and missed considerations.

## When to Use

- After implementing a plan or feature (most common)
- After completing data analysis or research
- To validate an approach before implementing
- Anytime a sanity check from an independent party would be valuable

## Usage

**Reviews take 1-5 minutes at default effort. Use the `Bash` tool directly with `run_in_background: true`.**

> **CRITICAL — Bash timeout:** The script's internal safety timeout is 30 minutes.
> The Bash tool's own timeout must be **at least** as long, or the Bash wrapper
> kills the process first and all output is lost. Always use `run_in_background: true`
> (no timeout cap) instead of setting a fixed `timeout` value.

> **CRITICAL: Do NOT use a Task agent (subagent) to run this command.**
> This is a single shell command, not a multi-step task — a subagent adds
> indirection with no benefit. Worse, Bash calls inside a subagent are
> foreground and capped at 10 minutes, while reviews can take much longer.
> A direct `Bash` call with `run_in_background: true` has no such cap.

The script is a dumb pipe — you write the full prompt, it handles the WSL/Codex plumbing.

### Correct invocation

Use the **Bash** tool with these parameters:
- `command`: `cd "<skill-directory>" && uv run review.py -w "C:\path\to\project" "Your full review prompt here"`
- `run_in_background`: `true`

When the background task completes, read results with **TaskOutput**. The review text is on stdout; status/progress messages are on stderr.

### Arguments

| Arg | Description |
|-----|-------------|
| `-w`, `--workdir` | Project directory (Windows path) — converted to WSL automatically |
| `-s`, `--sandbox` | Override default `--yolo` with `read-only` or `workspace-write` |
| `-e`, `--effort` | Review effort level: `standard` (default), `high`, `xhigh`. See below |
| `--timeout` | Seconds before abort (default: 1800 = 30 min). Use `--timeout 300` for small files (< 500 lines) to fail faster if something goes wrong |
| `--no-save` | Skip saving to ~/reviews/ |

Runs in `--yolo` mode by default (no approvals, no sandbox) since nobody is present to approve prompts in non-interactive mode.

### Effort levels

Each level controls reasoning effort. Default is `standard`, which is good for most reviews. Only escalate when the task genuinely needs it.

| Effort | Reasoning | Typical time | Use when |
|--------|-----------|--------------|----------|
| `standard` | medium | ~1 min | Most reviews (default) |
| `high` | high | 2-5 min | Complex multi-file reviews, subtle bugs |
| `xhigh` | xhigh | 5-20 min | Deep architectural analysis, exhaustive review |

## Writing the Review Prompt

You are responsible for writing the full prompt. Always frame Codex as an independent third-party reviewer. Common patterns:

### Plan implementation review

This is the most common use case — after implementing a plan, check completeness and correctness:

> For background, review the CLAUDE.md in the root project dir (if it exists) and then read this plan: {path to plan file}
>
> If you need to examine the database, feel free to look at the .env file to get the necessary environment variables and credentials.
>
> Please review what was actually implemented, how complete it is, if there are any bugs/issues, and include any other recommendations (in an Other Recommendations section) you may have. As you are exploring, if you happen to come across problems/bugs outside the scope of the plan, feel free to surface those too. Consider this a fairly open-ended task, and you are an independent third party reviewer.

**Note:** Convert any Windows paths in the prompt to WSL paths (e.g., `C:\Users\Dimitri\.claude\plans\foo.md` becomes `/mnt/c/Users/Dimitri/.claude/plans/foo.md`). The `--workdir` flag is converted automatically, but paths *inside the prompt text* are not.

### Code review (recent changes)

> For background, review the CLAUDE.md in the root project dir (if it exists).
>
> Review the recent changes in this project. Use git status, git diff, and git log to understand what was changed. Identify any bugs, missed edge cases, security issues, or architectural concerns. Include any other recommendations you may have in an Other Recommendations section. Consider this a fairly open-ended task, and you are an independent third party reviewer.

### Research / analysis review

> For background, review the CLAUDE.md in the root project dir (if it exists).
>
> Review the following research/analysis for accuracy, completeness, and any missed considerations. Verify claims where possible by examining the source data or code. Flag anything that seems incorrect, unsupported, or worth double-checking. {additional context about what was analyzed}

Adapt these freely — they're starting points, not rigid templates.

## Interpreting Results & Taking Action

Codex output is verbose. **Do NOT blindly relay all recommendations.** The first ~third of findings tends to be the most actionable (specific bugs, missed edge cases, concrete gaps). The middle is improvement suggestions. The tail end is often generic advice ("add tests", "add documentation") — typically noise.

**After receiving the review:**

1. Read it critically — it came from an independent agent and may contain inaccuracies
2. Explore the codebase to **verify each claim** before acting on it
3. Triage every finding into one of three buckets:

| Bucket | Criteria | Action |
|--------|----------|--------|
| **Fix now** | Clear bugs, correctness issues, unambiguously wrong. You're confident it's a real problem. | Go ahead and fix immediately — no need to ask first. |
| **Note** | Pre-existing issues not introduced by this change, debatable improvements, "yes but" items, things with tradeoffs. | Include in summary but don't fix unless the user asks. |
| **Noise** | Generic advice, low-priority nitpicks, things not worth addressing now. | Mention briefly in summary, grouped together. |

4. **Fix** all "Fix now" items, then present a **single summary** covering all findings — what was fixed, what was noted, and what was triaged as noise. A compact table works well:

```
| # | Finding | Severity | Action |
|---|---------|----------|--------|
| 1 | autoSaveTimer $state self-dependency | Critical | Fixed — changed to plain let |
| 2 | GET endpoints have no auth | High (Codex) | Pre-existing — not in scope |
| 3 | localStorage shape validation | Low | Noise — not worth addressing now |
```

Expect **1-3 actionable fixes** per review. If you're fixing more than 4-5 things, you're probably not filtering aggressively enough.

## When Invoked

1. Determine review context — ask the user if unclear:
   - What project directory?
   - What should Codex review? (plan file, recent changes, custom)
2. Write the full review prompt (see patterns above)
3. Run using the **Bash** tool directly with `run_in_background: true` — **never** a Task/subagent (see Usage above for why)
4. Continue working on other things while waiting
5. When the background task completes, read output with **TaskOutput**
6. Triage findings (see above), fix the clear wins, then present the full summary table

## Saved Reviews

Results auto-save to `~/reviews/` with YAML frontmatter. Check before re-running:

```bash
ls ~/reviews/ | grep "project-name"
```
