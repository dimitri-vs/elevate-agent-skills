---
name: codex-review
description: Get an independent review from Codex CLI (OpenAI, native Windows) as a second opinion. Use after implementing features, completing plans, finishing research/analysis, or anytime an unbiased third-party perspective would help - including non-code contexts like decisions, strategies, parenting situations, financial plans, or advice quality-checks. Runs 1-5 minutes in background (longer for complex reviews). Invoke when user asks for a sanity check, second opinion, or Codex review.
---

# Codex Review

Invoke OpenAI Codex CLI (native Windows install) as an independent reviewer. Provides a "second set of eyes" from a different model to catch gaps, bugs, missed considerations, or blind spots - in code, research, or any domain where an unbiased third-party perspective adds value.

## When to Use

- After implementing a plan or feature
- After completing data analysis or research
- To validate an approach before implementing
- As a second opinion on advice, strategies, or decisions (parenting, financial, health, business, etc.)
- Anytime a sanity check from an independent party would be valuable

Works for both technical and non-technical contexts. For non-code reviews, the prompt should include all relevant context inline (Codex won't have conversation history).

## Usage

**Reviews take 2-5 minutes at the default `medium` tier, 5-40 at `high`/`ultra`. Use the `Bash` tool directly with `run_in_background: true`.**

> **CRITICAL — Bash timeout:** The script's internal safety timeout is 30 minutes for `medium`, 60 minutes for `high` and `ultra`.
> The Bash tool's own timeout must be **at least** as long, or the Bash wrapper
> kills the process first and all output is lost. Always use `run_in_background: true`
> (no timeout cap) instead of setting a fixed `timeout` value.

> **CRITICAL: Do NOT use a Task agent (subagent) to run this command.**
> This is a single shell command, not a multi-step task — a subagent adds
> indirection with no benefit. Worse, Bash calls inside a subagent are
> foreground and capped at 10 minutes, while reviews can take much longer.
> A direct `Bash` call with `run_in_background: true` has no such cap.

The script is a dumb pipe — you write the full prompt, it handles the Codex plumbing (native `codex.exe`; override the binary via `CODEX_EXE` env var).

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
cd "<skill-dir>" && uv run review.py -w "C:\path\to\project" -e medium "$(cat /tmp/codex_prompt.txt)"
```

> **Prompts over ~6 KB: the heredoc stops working — use `Write` to a real Windows path.** The heredoc body is part of the same `bash -c` argument as the command, and Git Bash's MSYS2 runtime silently clips that argument at 8,186 chars (claude-code#85111). You get `unexpected EOF while looking for matching '` pointing at an innocent apostrophe, exit 2, and no review — the failure looks like a quoting bug in the prompt, so the natural next move is to fight the quoting, which never helps.
>
> The `/tmp` caveat above is about `/tmp` specifically, not about `Write`. `Write` to an explicit Windows path is fine and bash reads it back happily. Stage the prompt in your scratchpad directory, then keep the command itself short:
>
> ```bash
> # 1. Write tool -> C:\...\scratchpad\codex_prompt.txt   (no length limit)
> # 2. then, in Bash:
> cd "<skill-dir>" && uv run review.py -w "C:\path\to\project" -e high "$(cat 'C:\...\scratchpad\codex_prompt.txt')"
> ```
>
> Rule of thumb: heredoc for short and medium prompts, `Write` plus a Windows path once the prompt passes roughly 6 KB. A thorough `high` or `ultra` review prompt usually does.

### Arguments

| Arg | Description |
|-----|-------------|
| `-w`, `--workdir` | Project directory (Windows path) |
| `-s`, `--sandbox` | Override default `--yolo` with `read-only` or `workspace-write`. **`read-only` also blocks browser launches**: under it Codex cannot start Playwright/Chromium (or its own browser-use runtime), so a "render the page and look at it" review silently degrades to reading whatever screenshots already exist plus the source (observed 2026-09-19). For any review that must render a page, use `workspace-write` (or the default) and say in the prompt where it may write screenshots |
| `-e`, `--effort` | Review tier: `medium` (default), `high`, `ultra`. Spoken aliases accepted (`normal`/`regular`/`casual` → medium, `xhigh`/`extra-high` → high, `max`/`maximum`/`xxhigh`/`astra` → ultra). See below |
| `--reasoning` | Override the tier's reasoning effort (`low`/`medium`/`high`/`xhigh`/`max`/`ultra`). Env: `CODEX_REVIEW_REASONING`. Rarely needed; the tiers already pick sensible values |
| `--timeout` | Seconds before abort (default: per tier - 1800 for `medium`, 3600 for `high`/`ultra`; empirical data-exploration xhigh reviews were observed blowing past 30 min). Use `--timeout 300` for small files (< 500 lines) to fail faster if something goes wrong |
| `--no-save` | Skip saving to ~/reviews/ |
| `--accounts` | List available auth profiles and show which is active |
| `--account-setup NAME` | Save current Codex login as a named profile |
| `-a`, `--account NAME` | Use a named auth profile for this review |

Runs in `--yolo` mode by default (no approvals, no sandbox) since nobody is present to approve prompts in non-interactive mode.

### Effort tiers

Default is `medium`: the casual "catch anything blatantly wrong" pass, and the right call for most reviews. Escalate only when the user asks for it or the task genuinely needs deeper analysis. Tier names map to spoken language, not to Codex reasoning levels: when the user says "normal", "regular", or "casual" that is `medium`; "high" or "extra high" is `high`; "max", "ultra", "xxhigh", or "Astra" is `ultra`. The script accepts all of those as aliases.

| Tier | Model | Reasoning | Typical time | Use when |
|------|-------|-----------|--------------|----------|
| `medium` | gpt-5.6-terra | high | 2-5 min | Most reviews: code review, plan checks, bug hunts, sanity checks (default) |
| `high` | gpt-5.6-sol | xhigh | 5-40 min | Deep debugging, cross-file architectural analysis, reverse engineering, when the `medium` pass missed something |
| `ultra` | gpt-6-astra | xhigh | 4-20 min | The hardest problems only: when Claude and a `high` pass are both stumped, large cross-repo reviews, subtle data/analysis correctness. Opt-in, never the default |

**Push back on `ultra` for small tasks.** OpenAI's own model guide says Astra "tends to be thorough in testing before considering a task complete" and that on smaller tasks "this can result in broader tests than the task requires". In practice it over-engineers small reviews, runs broader checks than the diff warrants, and can recommend changes that degrade simple code. If the user asks for `ultra` on a small diff, a single-file change, or a routine plan check, say so and suggest `medium` or `high` instead; run `ultra` only if they confirm. When you do run `ultra`, tell the prompt to bound the scope (for example "do not run or write tests beyond what is needed to verify the specific findings").

### Model selection

Three tiers as of 2026-09-11 (GPT-6 Astra GA'd 2026-09-04; the GPT-5.6 family from 2026-07-09 is still live and not deprecated):

- **`medium` (default) → `gpt-5.6-terra` at `high` reasoning**: the balanced tier ($2/$12 per 1M input/output API tokens). Fast, and quality is fine for catching real bugs in ordinary changes. This is the old `high` preset under a new name; nothing about the run changed.
- **`high` → `gpt-5.6-sol` at `xhigh`**: OpenAI now describes sol as the "reliable agentic workhorse for everyday tasks" ($4/$20 promo through 2026-11-21, then $5 input). Unchanged from the old `xhigh` preset.
- **`ultra` → `gpt-6-astra` at `xhigh`**: the new flagship ($10/$50, 2.5x sol per token). Artificial Analysis measured Astra at `low` already scoring above sol at `xhigh` on their Intelligence Index while running 3.6x faster per task, and CodeRabbit's code-review benchmark had Astra finding 20% more bugs than sol on hard cross-file reviews but only 4% more on routine ones. Reports on `max` and `ultra` *reasoning* with Astra are consistently "diminishing returns, slower to first token than sol at max", so the preset stays at `xhigh`. Own benchmark 2026-09-11 (same 5 KB review prompt, same tree, read-only sandbox, blind-judged with repo access): Astra at `medium` 4 min, `xhigh` 10 min, `max` 19 min, all three producing the same eight must-fix findings; sol at `xhigh` took 16 min. Reasoning level above `medium` bought reproduction detail, not new findings. Neither model was more accurate than the other on the tree it reviewed.

Override the model via `CODEX_REVIEW_MODEL` and the reasoning effort via `--reasoning` or `CODEX_REVIEW_REASONING`; both apply to whatever tier is picked. `gpt-5.6-luna` (fast/cheap volume tier, $0.20/$1.20) is available via the env var for a quick low-stakes pass.

Notes:
- **ChatGPT-account auth** (the typical setup): `gpt-6-astra` and all three GPT-5.6 tiers work under ChatGPT sign-in (Astra verified 2026-09-11; Free/Go plans only get terra). Previous-gen `gpt-5.5` remains accepted. The floating `gpt-5-codex` alias is still NOT supported under ChatGPT auth (API-key only). `gpt-5.3-codex` and `gpt-5.2` were removed 2026-06-02.
- **API-key auth**: the floating `gpt-5-codex` alias still resolves but OpenAI marks it deprecated. Prefer an explicit `CODEX_REVIEW_MODEL` on API-key auth.
- **Reasoning levels**: sol and astra both accept `low`..`xhigh`, `max`, and `ultra` (multi-agent subagent decomposition, very quota-heavy, the CLI warns on selection). Astra has no `none`. No preset uses `max`/`ultra` reasoning; pass `--reasoning max` explicitly for a one-off.
- **CLI version matters**: Astra requires Codex CLI >= 0.153.0 (model picker support landed in 0.154.0). The wrapper surfaces a non-blocking warning when your installed CLI is out of date. To upgrade, re-run the installer from pwsh 7: `irm https://chatgpt.com/codex/install.ps1 | iex` (fails under Windows PowerShell 5.1 — the script needs pwsh).

## Writing the Review Prompt

You are responsible for writing the full prompt. Always frame Codex as an independent third-party reviewer. Common patterns:

> **State the tree explicitly.** Say "review the current working tree (HEAD plus uncommitted changes)" or name one exact commit, never both. A prompt that lists commit hashes and also says HEAD is ambiguous: in the 2026-09-11 benchmark all three Astra runs pinned to the last named commit and reviewed code that later commits had already fixed, while sol reviewed HEAD. Re-check any hash list before reusing an old prompt.

### Plan implementation review

This is the most common use case — after implementing a plan, check completeness and correctness:

> For background, review the CLAUDE.md in the root project dir (if it exists) and then read this plan: {path to plan file}
>
> If you need to examine the database, feel free to look at the .env file to get the necessary environment variables and credentials.
>
> Please review what was actually implemented, how complete it is, if there are any bugs/issues, and include any other recommendations (in an Other Recommendations section) you may have. As you are exploring, if you happen to come across problems/bugs outside the scope of the plan, feel free to surface those too. Consider this a fairly open-ended task, and you are an independent third party reviewer.

**Note:** Codex runs natively on Windows — use normal Windows paths (`C:\Users\...`) both in `--workdir` and inside the prompt text. No path conversion needed.

### Code review (recent changes)

> For background, review the CLAUDE.md in the root project dir (if it exists).
>
> Review the recent changes in this project. Use git status, git diff, and git log to understand what was changed. Identify any bugs, missed edge cases, security issues, or architectural concerns. Include any other recommendations you may have in an Other Recommendations section. Consider this a fairly open-ended task, and you are an independent third party reviewer.

### Research / analysis review

> For background, review the CLAUDE.md in the root project dir (if it exists).
>
> Review the following research/analysis for accuracy, completeness, and any missed considerations. Verify claims where possible by examining the source data or code. Flag anything that seems incorrect, unsupported, or worth double-checking. {additional context about what was analyzed}

### Page / UI review (needs a browser)

Codex has no live browser of its own in `exec` mode (the Codex app's `browser`/`chrome` plugins in `~/.codex/config.toml` only work with the desktop app running). It CAN drive headless Chromium through Playwright from the shell, which is what a rendered-page review depends on, but only if the sandbox allows process launches and writes: **never pass `-s read-only` for a page review**. Use `workspace-write` (or the default), point the prompt at a scratch directory for screenshots, and give it a known-good render recipe (`uv run --with playwright python <script>` serving the site with `python -m http.server`; the live URL may be blocked by Cloudflare's bot check for its `curl`). Ask it to state in the report how it viewed the page (live or local, which widths), so a fallback to "read the existing screenshots" is visible instead of silent.

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
2. Pick the tier. Default `medium`. Use `high` when the user asks for high/xhigh or the problem is genuinely hard. Use `ultra` only when asked for max/ultra/Astra, and push back first if the task is small (see Effort tiers).
3. Write the full review prompt (see patterns above). For non-code advisory reviews, include all relevant context inline since Codex has no conversation history.
4. Run using the **Bash** tool directly with `run_in_background: true` — **never** a Task/subagent (see Usage above for why)
5. Continue working on other things while waiting
6. When the background task completes, read output with **TaskOutput**
7. Triage findings (see above), act on the clear wins, then present the full summary table

## Account Profiles

Optional. By default, Codex uses whatever account is logged in (`~/.codex/auth.json`). If you have multiple OpenAI accounts (e.g. an agency Team plan and a personal Plus plan), you can save named profiles and switch between them with `-a`.

Profiles are stored as `~/.codex/<name>.auth.json` — alongside the main auth file, no extra directories. The active auth.json is swapped for the duration of the review and restored afterward (on normal completion and Python exceptions; a hard kill mid-review can leave the swapped profile active).

> **Do not run concurrent reviews with `-a`.** The swap uses a single global `auth.json` with no locking — two overlapping profile-switched reviews can restore the wrong login or cross-contaminate token refreshes. Concurrent reviews on the *default* account (no `-a`) are fine.

### One-time setup

```bash
# 1. Already logged in as agency → save it
cd "<skill-directory>" && uv run review.py --account-setup agency

# 2. Log out, log in with personal account (any shell — codex is on PATH)
codex logout && codex login

# 3. Save personal profile
cd "<skill-directory>" && uv run review.py --account-setup personal
```

### Using profiles

```bash
# List profiles and see which is active
cd "<skill-directory>" && uv run review.py --accounts

# Run a review with a specific account
cd "<skill-directory>" && uv run review.py -a personal -w "C:\path\to\project" "Review prompt"
```

When `-a` is used, the script swaps auth.json before running Codex and restores the original after — including saving any token refreshes back to the profile. If no profiles are set up, `-a` is simply unavailable and everything works as before.

The account name is recorded in saved review frontmatter (`account: personal` or `account: default`).

## Saved Reviews

Results auto-save to `~/reviews/` with YAML frontmatter. Check before re-running:

```bash
ls ~/reviews/ | grep "project-name"
```
