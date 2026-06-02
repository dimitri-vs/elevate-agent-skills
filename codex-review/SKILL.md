---
name: codex-review
description: Get an independent review from Codex CLI (OpenAI, via WSL) as a second opinion. Use after implementing features, completing plans, finishing research/analysis, or anytime an unbiased third-party perspective would help - including non-code contexts like decisions, strategies, parenting situations, financial plans, or advice quality-checks. Runs 1-5 minutes in background (longer for complex reviews). Invoke when user asks for a sanity check, second opinion, or Codex review.
---

# Codex Review

Invoke OpenAI Codex CLI (via WSL) as an independent reviewer. Provides a "second set of eyes" from a different model to catch gaps, bugs, missed considerations, or blind spots - in code, research, or any domain where an unbiased third-party perspective adds value.

## When to Use

- After implementing a plan or feature
- After completing data analysis or research
- To validate an approach before implementing
- As a second opinion on advice, strategies, or decisions (parenting, financial, health, business, etc.)
- Anytime a sanity check from an independent party would be valuable

Works for both technical and non-technical contexts. For non-code reviews, the prompt should include all relevant context inline (Codex won't have conversation history).

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

> **Never pipe the command through `head`, `tail`, `| wc`, etc.** `head -N` closes its stdin after N lines and SIGPIPEs the upstream `uv run` process, silently killing Codex before the review runs — you'll see a ~70-byte output with only "Starting Codex review..." and nothing saved to `~/reviews/`. If you want to preview the result without dumping it into context, read the saved file from `~/reviews/` after completion instead.

**Long/multiline prompts:** when the prompt has shell-significant characters (quotes, code blocks, em dashes, etc.) or runs more than ~30 lines, write it to a temp file first and pass via `"$(cat /tmp/myprompt.txt)"`. Use a **bash heredoc** to create the file, not the `Write` tool — `Write`'s `/tmp/` path does NOT resolve to the same filesystem location as bash's `/tmp/` on Windows/Git-Bash, so `cat` will silently fail. Pattern:

```bash
cat > /tmp/codex_prompt.txt << 'PROMPT_EOF'
... your full prompt here ...
PROMPT_EOF
cd "<skill-dir>" && uv run review.py -w "C:\path\to\project" -e high "$(cat /tmp/codex_prompt.txt)"
```

### Arguments

| Arg | Description |
|-----|-------------|
| `-w`, `--workdir` | Project directory (Windows path) — converted to WSL automatically |
| `-s`, `--sandbox` | Override default `--yolo` with `read-only` or `workspace-write` |
| `-e`, `--effort` | Review effort level: `high` (default), `xhigh`. See below |
| `--timeout` | Seconds before abort (default: 1800 = 30 min). Use `--timeout 300` for small files (< 500 lines) to fail faster if something goes wrong |
| `--no-save` | Skip saving to ~/reviews/ |

Runs in `--yolo` mode by default (no approvals, no sandbox) since nobody is present to approve prompts in non-interactive mode.

### Effort levels

Default is `high`, which is good for most reviews. Escalate to `xhigh` only when the task genuinely needs deeper analysis.

| Effort | Model | Reasoning | Typical time | Use when |
|--------|-------|-----------|--------------|----------|
| `high` | gpt-5.4 | high | 2-5 min | Most reviews — code review, plan checks, bug hunts (default) |
| `xhigh` | gpt-5.5 | xhigh | 5-20 min | Deep debugging, reverse engineering, architectural analysis, when Claude is stumped |

### Model selection

The script uses a **tiered model strategy** based on effort level (as of 2026-06-02):

- **`high` (default) → `gpt-5.4`** — the cost-effective daily driver for routine code review, plan validation, and bug hunting. Exactly **2x cheaper per token** than gpt-5.5 (62.50/375 vs 125/750 per 1M input/output credits), translating to roughly 25-33% more messages per 5-hour window on Plus/Team plans. Users hitting rate limits should prefer this tier.
- **`xhigh` → `gpt-5.5`** — the stronger peak model for hard, ambiguous, or long-horizon tasks. Higher benchmarks (Terminal-Bench 82.7% vs 75.1%, Expert-SWE 73.1% vs 68.5%) but burns quota 2x faster. Reserve for tasks that genuinely need it: reverse engineering, deep root-cause analysis, cross-repo architectural reviews, or when the `high` pass missed something.

Override either default via `CODEX_REVIEW_MODEL` env var (applies to all effort levels).

Notes:
- **ChatGPT-account auth** (the typical setup): Codex does NOT support the floating `gpt-5-codex` alias — pass a versioned snapshot. Accepted strings per [Codex docs](https://developers.openai.com/codex/models): `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`. Note: `gpt-5.3-codex` and `gpt-5.2` were removed from ChatGPT-account auth on 2026-06-02.
- **API-key auth**: the floating `gpt-5-codex` alias works and auto-updates within the codex line. Set `CODEX_REVIEW_MODEL=gpt-5-codex` if you're on API-key auth.
- **CLI version matters**: newer models require recent Codex CLI versions. The wrapper surfaces a non-blocking warning encouraging you to upgrade to the latest version when your installed CLI is out of date. Run `wsl npm install -g @openai/codex@latest` to upgrade.

## Writing the Review Prompt

You are responsible for writing the full prompt. Always frame Codex as an independent third-party reviewer. Common patterns:

### Plan implementation review

This is the most common use case — after implementing a plan, check completeness and correctness:

> For background, review the CLAUDE.md in the root project dir (if it exists) and then read this plan: {path to plan file}
>
> If you need to examine the database, feel free to look at the .env file to get the necessary environment variables and credentials.
>
> Please review what was actually implemented, how complete it is, if there are any bugs/issues, and include any other recommendations (in an Other Recommendations section) you may have. As you are exploring, if you happen to come across problems/bugs outside the scope of the plan, feel free to surface those too. Consider this a fairly open-ended task, and you are an independent third party reviewer.

**Note:** Convert any Windows paths in the prompt to WSL paths (e.g., `C:\Users\<username>\.claude\plans\foo.md` becomes `/mnt/c/Users/<username>/.claude/plans/foo.md`). The `--workdir` flag is converted automatically, but paths *inside the prompt text* are not.

### Code review (recent changes)

> For background, review the CLAUDE.md in the root project dir (if it exists).
>
> Review the recent changes in this project. Use git status, git diff, and git log to understand what was changed. Identify any bugs, missed edge cases, security issues, or architectural concerns. Include any other recommendations you may have in an Other Recommendations section. Consider this a fairly open-ended task, and you are an independent third party reviewer.

### Research / analysis review

> For background, review the CLAUDE.md in the root project dir (if it exists).
>
> Review the following research/analysis for accuracy, completeness, and any missed considerations. Verify claims where possible by examining the source data or code. Flag anything that seems incorrect, unsupported, or worth double-checking. {additional context about what was analyzed}

### Advisory / decision review (non-code)

For situations where Claude has been giving advice (parenting, health, financial, strategic, etc.) and the user wants an independent second opinion. Since Codex has no conversation history, the prompt must be self-contained with all relevant context.

Structure the prompt as:
1. **Background** — who's involved, relevant history, constraints
2. **The situation** — what happened, what's being decided
3. **Advice given** — the specific recommendations being reviewed
4. **Review task** — what to assess (what's right, what's missing, what's wrong, additional recommendations)

The `--workdir` flag is still required but less important for non-code reviews. Point it at the CWD (usually the Obsidian Vault) so Codex can optionally read related documents if referenced. Always write these prompts to a temp file first — they're long and have special characters.

Adapt these freely — they're starting points, not rigid templates.

## Interpreting Results & Taking Action

Codex output is verbose. **Do NOT blindly relay all recommendations.** The first ~third of findings tends to be the most actionable (specific bugs, missed edge cases, concrete gaps). The middle is improvement suggestions. The tail end is often generic advice ("add tests", "add documentation") — typically noise.

**After receiving the review:**

1. Read it critically — it came from an independent agent and may contain inaccuracies
2. Explore the codebase to **verify each claim** before acting on it
3. Triage every finding into one of three buckets:

| Bucket | Criteria | Action |
|--------|----------|--------|
| **Act on** | Clear bugs, correctness issues, factual errors, or important missed considerations. You're confident it's a real problem. | For code: fix immediately. For advisory: surface prominently to the user. |
| **Note** | Pre-existing issues, debatable improvements, "yes but" items, things with tradeoffs, alternative perspectives worth considering. | Include in summary but don't act on unless the user asks. |
| **Noise** | Generic advice, low-priority nitpicks, things not worth addressing now. | Mention briefly in summary, grouped together. |

4. **Act on** all actionable items (fix code, or surface advisory findings to the user), then present a **single summary** covering all findings. A compact table works well:

```
| # | Finding | Severity | Action |
|---|---------|----------|--------|
| 1 | autoSaveTimer $state self-dependency | Critical | Fixed — changed to plain let |
| 2 | GET endpoints have no auth | High (Codex) | Pre-existing — not in scope |
| 3 | localStorage shape validation | Low | Noise — not worth addressing now |
```

Expect **1-3 actionable items** per review. If you're surfacing more than 4-5 things, you're probably not filtering aggressively enough.

## When Invoked

1. Determine review context — ask the user if unclear:
   - What project directory? (for non-code reviews, use the CWD)
   - What should Codex review? (plan file, recent changes, advice given in this conversation, custom)
2. Write the full review prompt (see patterns above). For non-code advisory reviews, include all relevant context inline since Codex has no conversation history.
3. Run using the **Bash** tool directly with `run_in_background: true` — **never** a Task/subagent (see Usage above for why)
4. Continue working on other things while waiting
5. When the background task completes, read output with **TaskOutput**
6. Triage findings (see above), act on the clear wins, then present the full summary table

## Saved Reviews

Results auto-save to `~/reviews/` with YAML frontmatter. Check before re-running:

```bash
ls ~/reviews/ | grep "project-name"
```
