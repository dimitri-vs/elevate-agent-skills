---
name: codex-review
description: Consult OpenAI's Codex CLI (native Windows) as an independent second agent from a different model family. Use it to review code, plans, research or advice (including non-code decisions like parenting, financial or strategy questions), to get a second opinion on an open question, or to hand off a task Codex implements in its own git worktree. Supports follow-up turns in the same Codex session. Runs in the background, 2-40 minutes. Invoke when the user asks for a Codex review, second opinion, sanity check, or to give a task to Codex.
---

# Codex Review

Run OpenAI's Codex CLI as a second agent. Its value is that it is a *different* model: different training, different blind spots. Treat it like a capable colleague you can brief, question and send off on a task, not a linter.

Three ways to use it:

- **Review**: check finished work (code, a plan, research, advice). The most common use.
- **Consult**: ask an open question or get an independent take before you commit to an approach.
- **Delegate**: hand Codex a self-contained task to implement in its own git worktree, then review and bring its commits back.

Any of them can continue over several turns with `--resume`.

## Usage

**Use the `Bash` tool directly with `run_in_background: true`.** Runs take 2-5 minutes at `medium` and 5-40 minutes at the higher tiers. The script's own timeout is 30 minutes for `medium` and 60 for the rest, so never set a fixed Bash `timeout`. A foreground call gets killed first and the output is lost.

**Don't wrap it in a Task subagent.** A subagent's Bash calls are foreground and capped at 10 minutes, while runs can take much longer.

The command:

```bash
cd "<skill-directory>" && uv run review.py -w "C:\path\to\project" "Your full prompt"
```

When the task finishes, read stdout with **TaskOutput**. It holds Codex's reply followed by a footer:
- the session id and the exact `--resume` command;
- any files Codex changed;
- Codex's working directory, for delegated runs;
- `STATUS`/`FATAL` lines if the run failed.

The footer is printed even on failure. A non-zero exit means at least one run failed, even when there is text.

Progress and errors go to stderr.

- **Never pipe through `head`, `tail` or `wc`.** `head` closes the pipe and kills Codex before it runs, leaving only a ~70-byte "Starting Codex..." output. To skim a long reply, read the saved file in `~/reviews/`.
- **Anything longer than a sentence or two: write the prompt to a file and redirect stdin** (`uv run review.py -w "..." < 'C:\...\scratchpad\codex_prompt.txt'`). Redirecting stdin avoids quoting problems and every command-line length limit. `"$(cat file)"` still pushes the whole prompt through the Windows command line.
  - Create the file with the `Write` tool at a real Windows path, such as your scratchpad.
  - A bash heredoc works for short prompts, but Git Bash clips a whole command at 8,186 chars and reports a misleading `unexpected EOF while looking for matching '`.
  - `Write`'s `/tmp` is not Git Bash's `/tmp`.

### Arguments

| Arg | Description |
|-----|-------------|
| `-w`, `--workdir` | Project directory (Windows path). For non-code reviews, use the CWD |
| `-e`, `--effort` | Tier: `medium` (default), `high`, `astra`, `ultra`. Spoken aliases work (see Tiers) |
| `--resume ID` | Send a follow-up turn to an earlier session. `last` picks the newest saved session (for the `-w` directory if given). Model, sandbox, account and working directory are restored from the saved review |
| `--worktree` | Run in a new Codex-managed git worktree (detached HEAD under `~/.codex/worktrees/`). For delegated edits |
| `-s`, `--sandbox` | `read-only` or `workspace-write` instead of the default no-sandbox mode (see Sandbox) |
| `--reasoning` | Override the tier's reasoning effort (`low`..`xhigh`, `max`, `ultra`). Env: `CODEX_REVIEW_REASONING`. Rarely needed. Reasoning `ultra` is not the `ultra` tier: it's multi-agent decomposition and burns quota fast |
| `--timeout` | Seconds before abort. `--timeout 300` fails fast on small jobs |
| `--no-save` | Skip saving to `~/reviews/`. Such sessions can still be resumed by id, but you then have to pass `-e`/`-s` yourself |
| `-a NAME`, `--accounts`, `--account-setup NAME` | Auth profiles (see Account Profiles) |

### Tiers

Default is `medium`. Escalate when the user asks or the problem is genuinely hard. The names follow how people talk:
- "normal", "regular" or "casual" → `medium`;
- "high", "xhigh" or "extra high" → `high`;
- "max", "ultra", "xxhigh" or "dual" → `ultra`.

| Tier | Runs | Median time | Use when |
|------|------|-------------|----------|
| `medium` | gpt-5.6-terra @ high | 4 min | Most reviews, plan checks and quick consults |
| `high` | gpt-5.6-sol @ xhigh | 12 min (some runs take 30-48) | Deep debugging, cross-file analysis, when `medium` missed something. Best measured bug recall |
| `astra` | gpt-6-astra @ high | ~10 min | A second model family on the same question. Fewer findings, more of them real |
| `ultra` | `high` and `astra` **in parallel** | as long as sol | The hardest or highest-stakes reviews. Two sessions, two saved files, one combined output |

Why `ultra` is two models:
- On an independent 50-PR seeded-bug benchmark (Sep 2026), 5.6 sol confirmed 107 bugs and Astra 91. Astra's precision was higher, 95% vs 85%.
- 32 bugs were found only by sol and 16 only by Astra. Running both catches more than either alone.
- Astra finishes sooner because it spends about a third of the tokens, not because it's faster. It explores less.
- In our own Sep 2026 benchmark, Astra reasoning above medium bought no new findings, so the leg runs at `high`.

When you merge the two outputs, say which findings both models reported (high confidence) and which only one did. Each leg has its own session id. To follow up, resume the leg whose finding you're questioning. `--resume last` picks only one of them.

**Push back on `ultra` for small tasks.** It doubles the quota and the noise. Suggest `medium` or `high`, and run `ultra` only if the user confirms. Also, Astra tends to over-test small tasks ("broader tests than the task requires", per OpenAI's model guide). When you do run it, bound the scope in the prompt, for example "don't run or write tests beyond what's needed to verify a finding".

**Model notes (2026-10-15):**
- GPT-6.1 Sol (`gpt-6.1-sol`) has been Codex's own default since CLI 0.161, but there's no review benchmark for it yet. Try it with `CODEX_REVIEW_MODEL=gpt-6.1-sol` for a one-off. The env var applies to single tiers only; `ultra` ignores it.
- `gpt-5.6-luna` is the cheap smoke-test model.
- Under ChatGPT-account auth the floating `gpt-5-codex` alias doesn't work. Use versioned names.
- Some Astra code reviews get cut off by OpenAI's cyber-safety filter. Open issue openai/codex#47310; 5.6 sol isn't affected. If an `astra` leg stops with a safety message, rely on the `high` leg.
- Tested on Codex CLI 0.162.1. Resume, `--json` and `--worktree` behaviour depends on recent releases, so upgrade rather than debug an old CLI. From Git Bash:
  ```bash
  pwsh -NoProfile -Command '$env:PATH = "C:\Windows\System32;" + $env:PATH; irm https://chatgpt.com/codex/install.ps1 | iex'
  ```
  Putting System32 first matters: otherwise the installer picks up Git's `tar` and fails with "did not contain the expected package layout".

### Sandbox

The default is no sandbox and no approvals, because nobody is there to approve prompts. That's deliberate:
- Reviews regularly need the network (DB checks via `.env` credentials, Playwright renders, package lookups).
- `read-only` and `workspace-write` both block network access.
- `read-only` also blocks browser launches.

The footer lists every file Codex edited with its patch tool, so an unwanted edit is visible. Writes from shell commands are not listed. For review and consult, still tell Codex in the prompt not to modify files, but don't rely on it. In the review that checked this skill (2026-10-15), Codex was told "do not modify any files" and still tried to delete a `__pycache__` folder twice. Codex's built-in command policy blocked it.

For a pure review that needs no network (no database, no renders), prefer `-s read-only`. The wrapper re-applies the sandbox on every `--resume`, because Codex itself doesn't carry it over (openai/codex#40149).

## Writing the Prompt

You write the whole prompt; the script is a dumb pipe. Codex has no access to this conversation, so the prompt must stand on its own.

**Principles that measurably matter:**

- **Give facts, not your verdict.** Don't tell Codex "tests pass", "I think this is solid" or "the bug is fixed". Reported effect: self-assessment in the prompt halves how often real bugs get flagged as critical. Give it the plan, diff, data or situation and let it judge.
- **State the tree.** Say "review the current working tree (HEAD plus uncommitted changes)" or name one exact commit, never both. Astra pins to a named commit even when the prompt also says HEAD.
- **Set the stance.** For reviews: "you are an independent reviewer; your job is to find what's wrong, not to validate it." Ask it to put real problems first and to say how confident it is in each.
- **Allow any answer shape.** Don't force a template. Ask for whatever serves the question: findings ranked by severity, a recommendation with reasoning, or "nothing material found" when that's true.
- **Grant access explicitly.** For example: "read the CLAUDE.md first", "you may use the .env credentials to query the database", "you may run the test suite". For review and consult, add: "don't modify files".
- **Use plain Windows paths** (`C:\Users\...`) in the prompt. Codex runs natively, so no `/c/...` conversion.

### Starting points (adapt freely)

**Plan or implementation review** (the most common):

> For background, read the CLAUDE.md in the project root (if any), then this plan: {path}. You may read the .env to query the database. Review what was actually implemented against the plan: how complete it is, bugs, missed edge cases, and anything else you'd recommend. If you spot problems outside the plan's scope, surface those too. You're an independent reviewer: look for what's wrong, put the important things first, say how confident you are in each, and don't modify files.

**Code review of recent changes:** the same, but "use git status, git diff and git log to see what changed" in place of the plan.

**Research or analysis review:** "verify claims against the source data or code where you can, and flag anything incorrect, unsupported or worth double-checking."

**Consult:** state the question, the constraints, and what you've already ruled out and why. Ask for a recommendation plus what would change it.

**Advisory or decision review (non-code):** cover four things in order:
1. Background: who's involved, history, constraints.
2. The situation or decision.
3. The specific advice given so far.
4. What to assess: what's right, what's missing, what's wrong.

Point `-w` at the CWD (usually the Obsidian Vault) so Codex can read referenced notes. Always write these to a file first; they're long.

**Page or UI review:** Codex can drive headless Chromium through Playwright, but only without `-s read-only`. Tell it:
- where to write screenshots;
- a working render recipe (`uv run --with playwright python <script>`, serving the site with `python -m http.server`; Cloudflare may block the live URL);
- to state how it viewed the page.

That way a fallback to stale screenshots is visible.

## Follow-ups (`--resume`)

The session keeps everything Codex already read, so a follow-up is cheaper and better informed than a fresh run that re-explains the context.

**One round is the default.** Fixing a finding does not send it back to Codex: verify the fix yourself (a test that fails without it, a rerun of the query) and move on. Don't resume to get fixes signed off or to invite pushback on findings you chose not to act on.

**Resume only when:**
- You checked a finding and think it's wrong, and it matters. Quote it, give your evidence (file:line, output), and ask Codex to defend or withdraw it.
- Codex asked a question or flagged missing context, and you can supply it.
- A delegated task needs another iteration.
- The user asks for a recheck.

**Keep it bounded.**
- Stop after one or two rounds on the same disagreement. Models tend to cave rather than converge, so a long back-and-forth produces agreement, not truth.
- If you still disagree, put both positions in front of the user, with the evidence for each.

Give new facts in each turn, not just "are you sure?".

```bash
cd "<skill-directory>" && uv run review.py --resume <session-id> "Your follow-up"
```

Use `run_in_background: true` here too. The reply is appended to the same file in `~/reviews/` as a new turn.

## Delegating a task (`--worktree`)

For self-contained work you'd otherwise give a subagent, when a different model's approach is the point or when you want to keep working in parallel.

```bash
cd "<skill-directory>" && uv run review.py -w "C:\path\to\repo" --worktree -e high < 'C:\...\task.txt'
```

1. **Brief it like a colleague:** the goal, the constraints, the files involved, how to verify (which tests to run), and "commit your work in the worktree with a descriptive message when done". The worktree is a detached HEAD under `~/.codex/worktrees/`, created from **committed HEAD**:
   - **Uncommitted work is missing**, whether staged, modified or untracked. Commit what Codex needs to build on first. The wrapper warns when the tree is dirty.
   - Ignored files such as `.env` are missing too (verified). Tell Codex to read secrets from the main repo path if it needs them.
2. When it finishes, review its commits from the main repo, which shares the same git objects:
   - `git -C "<worktree>" log --oneline "$(git rev-parse HEAD)"..HEAD` (run from the main repo), then `git show <sha>`;
   - `git -C "<worktree>" status` for anything it didn't commit.
3. Verify it yourself: run the tests and read the diff. Its work gets the same skepticism as its reviews.
4. Bring it over with `git cherry-pick <sha>` in the main repo, or `--resume` to ask for changes first.
5. Clean up with `git worktree remove "$(git -C "<codex working dir>" rev-parse --show-toplevel)"`. The footer's path is the subdirectory if `-w` pointed at one. A removed worktree can't be resumed.

Without `--worktree`, Codex edits the main tree directly. That's fine only when you're not editing at the same time.

## Interpreting Results

Codex is an independent agent and is sometimes wrong. One controlled study found that unverified Codex review of Claude's code *lowered* accuracy. Verify each claim (read the code, run the query) before acting on it or relaying it.

- **Act:** confirmed bugs, factual errors, important gaps. Fix them and verify the fix yourself (no Codex recheck), or for advisory reviews, put them in front of the user.
- **Dispute:** claims you checked and believe are wrong. `--resume` with your evidence when it matters; otherwise note the disagreement.
- **Note:** debatable or out-of-scope items. Mention them, don't act on them.
- **Drop:** generic advice ("add more tests", "add docs").

Filter hard. A typical review leaves 1-3 things worth acting on. If you're relaying more than about 5, make sure each one survived your own check.

Report back in whatever shape fits. A short table helps when there are several findings: finding, verdict (fixed / disputed / noted), one-line reason. Lead with what changed and what the user needs to decide. A clean review is a fine result; say so plainly.

## Account Profiles

Optional. By default Codex uses the logged-in account (`~/.codex/auth.json`). With several OpenAI accounts (e.g. an agency Team plan and a personal Plus plan), save named profiles and pick one with `-a`. Profiles live at `~/.codex/<name>.auth.json`.

- The active `auth.json` is swapped for the duration of the run, then restored.
- Token refreshes are saved back to the profile.
- A hard kill mid-run can leave the swapped profile active.
- `--resume` reuses a named profile recorded in the saved review. A `default` session resumes on whatever account is active at the time, and so does any `--no-save` run, so pass `-a` yourself.

> **Do not run concurrent `-a` runs.** The swap uses one global `auth.json` with no locking. Concurrent runs on the default account are fine, and so are `ultra`'s two legs, which share one swap.

```bash
cd "<skill-directory>" && uv run review.py --account-setup agency   # save current login
codex logout && codex login                                         # log in as the other account
cd "<skill-directory>" && uv run review.py --account-setup personal
cd "<skill-directory>" && uv run review.py --accounts               # list, show active
cd "<skill-directory>" && uv run review.py -a personal -w "C:\path" "Prompt"
```

## Saved Reviews

Results save to `~/reviews/<date>_<project>.md`; `ultra` adds `_high` and `_astra` suffixes. Each file's YAML frontmatter records:
- the model and sandbox;
- `session_id`, `codex_cwd` and `turns`.

Follow-up turns are appended to the same file. Check for an earlier review before re-running: `ls ~/reviews/ | grep "<project>"`. To pick that session up again, use `--resume last -w "<dir>"`.
