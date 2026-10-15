# Lens prompts

Templates for review subagents. Replace `<skill>`, `<SKILL_PATH>` and `<WS>` (the review workspace, `~/.claude/skill-reviews/<skill>/<date>/`). Every lens: read-only on the skill, writes a full report to `<WS>/<lens>.md`, returns a summary of 40 lines or fewer, and states its lane ("other agents cover X, Y").

## Usage & provenance

> You are the PROVENANCE & USAGE lens for `<skill>` (`<SKILL_PATH>`). Your output guards against Chesterton's-fence regressions: establish WHY each rule exists and whether it has earned its place.
> 1. Git: `git log -p --follow -- <skill dir>`. For each rule, find when it was added and whether it came from a generic draft or a field-learned edit. Also check target projects for divergent copies.
> 2. Transcripts: run `scripts/skill_usage.py report <skill> [--cli <binary>]` for the numbers, then read the real-usage sessions it lists. Look for: the task the user was doing, failures and how the agent recovered, `--help` lookups, hits on things the skill warns about, and sessions that did this kind of work without loading the skill.
> 3. Output: usage stats and trigger rate; a **protected-lines** table (`rule | origin commit | incident it prevents (session id + quote) | PROTECT / KEEP / NO-EVIDENCE`; NO-EVIDENCE does not mean delete); recurring stumbles the skill didn't prevent; unused sections; and 3 read-only behavior-test tasks taken from real sessions, each with what correct behavior looks like.

**Cross-model replication (full tier):** give a second model (e.g. Codex) the same raw transcript locations and the questions above. It must produce its own numbers and only then read the first report and list AGREE / DISAGREE / MISSED. Exclude the review session and test runs.

## Currency

> You are the CURRENCY lens for `<skill>`. Check every factual claim against (a) the locally installed tool (`<tool> --version`, and `--help` for every command and flag the skill mentions; read-only commands only) and (b) current official docs. Save raw pages to `<WS>/currency-sources/` (prefer `.md` / `llms.txt` URLs or a markdown scraper) and grep them narrowly. A search or fetch *summary* is never a source. Every verdict must trace to a saved file or real CLI output.
> Also verify these field reports: <anything live sessions or the user reported>.
> Output: `claim | CURRENT / STALE / WRONG / UNVERIFIABLE | evidence | fix`, plus "Missing but needed" (new commands and behaviors an agent would plausibly need).

## Vendor check

> Does `<tool>`'s vendor now ship an official agent skill, plugin or MCP? Read it from source (e.g. `gh api` on its repo). **Never run its install commands.** Produce: a coverage matrix against our protected lines, stumbles and currency findings (`covered / partial / not covered / contradicts`); token cost and progressive disclosure; how broad its trigger description is; side effects (hooks that auto-approve commands, always-on MCP servers, install paths, self-updating copies); bugs in it. Recommend: adopt it with a thin overlay of ours, keep ours and borrow from theirs, or keep ours and point to their references.

## Portability (public skills only)

> The skill is published. Find private names, paths, account details, internal policy, pointers to private skills, and environment assumptions that mislead other users (OS-specific commands, one project's layout). Separate "biased but harmless example" from "misleads". Also check git history for anything sensitive that was ever committed (flag it; don't rewrite history). For every removal: proposed public wording plus a named private destination with the exact text to add. Check that the destination will actually load in the situations where the removed text used to help.

## Craft

> Judge `<skill>` as an artifact for an agent. First ground yourself in current skill-authoring guidance (the official docs, saved raw) and in 2-3 recently refined skills from the same repo for house style.
> Evaluate: signal per token (what the model already knows or gets from `--help`, vs the non-obvious traps that are the skill's real value); duplication; dated snapshots that belong in `references/`; ordering (traps first, reference last); whether the description makes the skill load when it should without firing on unrelated work; AI-slop tells.
> Mark each proposed cut `safe` (pure duplication or generic) or `needs-provenance-check` (anything that looks like a learned lesson).

## Light review (one agent, all-in-one)

> Review `<skill>` for currency, craft and portability in one pass, using the sections above as checklists. Inputs: the skill, `skill_usage.py report <skill>` output, the local tool's `--help` if it wraps a CLI. Treat every rule that looks like a learned lesson as protected unless git history or transcripts show it's obsolete. Write `<WS>/proposal/` per the skill's Synthesis section.
