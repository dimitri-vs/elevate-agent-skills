---
name: skill-review
description: Review an existing agent skill for currency, accuracy, relevance and bloat without regressing what already works - usage triage first (retire / light / full), evidence-backed changes only, one verification pass, then a usage-based follow-up a few weeks later. Use when asked to review, audit, refresh, modernize, de-bloat or retire a skill (or "which skills are stale/unused"), or when a follow-up task from a previous skill review comes due. Not for creating a new skill from scratch.
---

# Skill Review

Most skills basically work, or someone would have fixed them. The job is to make one cleaner and current without losing the hard-won lines in it (Chesterton's fence), and to retire the ones nobody uses. The order matters: check usage before auditing content, check evidence before cutting anything, run one verification pass, ship, then let real usage decide.

Workspace for every review: `~/.claude/skill-reviews/<skill>/<YYYY-MM-DD>/`, outside the skills repo so drafts and reports never land in a public commit. Run every test from under `~/.claude/skill-reviews/`. The usage miner excludes that tree, so tests don't count as real use.

## Step 0: usage triage (minutes, every skill)

```bash
python -I scripts/skill_usage.py triage            # all skills: loads 30d/120d, last used, scope, bucket
python -I scripts/skill_usage.py report <skill> [--cli <binary>] [--since YYYY-MM-DD]
```

`report --cli` also gives the trigger rate: sessions that ran the tool vs sessions that loaded the skill. It's often the single most important number, because a skill that never loads can't help. Set `SKILLS_REPO` to also scan a skills source repo. Exclusions and bucket lists live in `~/.claude/skill-reviews/` (`excluded-sessions.txt`, `rare-by-nature.txt`, `prod-cli-skills.txt`).

| Bucket | Meaning | Next |
|---|---|---|
| `retire-candidate` | no loads in 120 days | Retirement check below |
| `rare-by-nature` | event-driven (offboarding, onboarding) | Light review only when it next fires |
| `light` | used, low stakes | Light review |
| `full` | heavy use, or guards a production CLI/API | Full review |

Claude Code deletes transcripts after `cleanupPeriodDays` (default 30). Raise it in settings, or "unused for 120 days" can't be measured.

**Retirement check.** A project-scoped skill costs nothing until that project is active, so low use there may just mean a quiet project. A global skill costs description tokens every session and competes for triggering. Prefer archiving (move to `archive/` or tag) over deleting, and confirm with the user before either.

## Light review (one agent)

Feed one agent the `report` output and the skill. It covers currency, craft and portability in a single pass (prompts in [references/lenses.md](references/lenses.md)) and writes `proposal/` (see Synthesis). Then go straight to Apply. No test, unless the change touches safety guidance.

## Full review

1. **Lenses, in parallel** (prompts in [references/lenses.md](references/lenses.md)):
   - **Usage & provenance:** transcripts plus git history. Gives the trigger rate, the real task mix, the stumbles the skill didn't prevent, and a **protected-lines** table (every rule traced to the incident behind it). Have a second model (e.g. Codex) re-mine the transcripts on its own and reconcile. In the pilot it caught the strongest "incident" being misattributed.
   - **Currency:** every command and flag against the locally installed tool's `--help` (authoritative and cheap), everything else against raw saved docs. Never use a search-summary as the source.
   - **Vendor check:** does the tool now ship an official skill, plugin or MCP? Compare coverage, cost, triggering and side effects before polishing your own. It can change the whole strategy.
   - **Portability** (public skills only): private names, paths, policy, pointers to private skills. Every removal needs a named private destination.
   - **Craft:** signal per token, duplication, dated snapshots that belong in `references/`, description triggering, AI-slop.
2. **Ask live sessions.** Any open session that recently used the tool will give better gap reports than a model guessing.
3. **Synthesis** → `proposal/` (below).
4. **One verification pass:**
   - **Behavior test:** 2-3 read-only tasks taken from real sessions, run with the new skill, the old skill, and **no skill**. One judge, scoring blind before reading the skills. A no-skill baseline that ties is normal. The skill's value is in traps, and healthy live infrastructure doesn't trigger them.
   - **Trigger test:** the new description vs the old, on should- and shouldn't-trigger prompts. Details in [references/testing.md](references/testing.md).
   - **Independent diff review:** one pass by a second model, checking protected lines, facts against saved evidence, unsafe guidance, and leaks.
5. **Fix what verification found, then stop.** Only run another loop for a High-severity safety finding. Each extra loop finds smaller problems, and some of them are errors your own rewrite introduced.

Scripted trap scenarios (a fake CLI first on PATH with canned broken states) are worth building only when the skill guards destructive or irreversible actions. See [references/testing.md](references/testing.md).

## Synthesis output (`proposal/`)

- `SKILL.md` + `references/`: the new skill. Bulky, dated material goes in references with a "verified as of" line.
- `CHANGELOG.md`: `old | new or destination | reason | evidence`. **Every deletion says why the line existed and why it's safe to drop, or where it moved.**
- Patches for private destinations (sibling private skill, global instructions), plus the README row.
- `downstream-copies.md`: places that copied the skill's old advice (project CLAUDE.md/AGENTS.md files, memory files, scripts). Grep for the specific stale strings. Fixing the skill doesn't fix those copies.
- `expectations.md`: predictions the follow-up can check against transcripts (trigger rate, commands that should appear or disappear, specific regressions to watch for).

Rules that held up: keep the intent of every protected line even when you rewrite how it's done; cut generic syntax the no-skill run handled fine; agents usually run `--help` before acting, so a names-only command index rarely earns its tokens.

## Apply

Get the user's approval of the proposal first. Then: copy into the repo, sync (never `--force` without confirmation, and back-port any `[LOCAL EDITS]` first), apply the private patches, fix the downstream copies, update the README row, commit. Leave other repos' changes uncommitted unless asked.

## Follow-up (the real verdict)

Create **one** reminder for about 2 weeks after the commit. Its notes must stand on their own: the commit, the `report --since <commit date>` command, the path to `expectations.md`, and "also ask live sessions using the tool". Back-off: if there are fewer than about 3 real sessions, create the *next* reminder instead of judging (+4 weeks, then +8 weeks). If use is still near zero at about 4 months, the follow-up becomes a retirement check. Each follow-up creates only the next reminder, not the whole chain.

## Gotchas

- **Self-reports are unreliable.** Agents say they'd run `--help`, and then sometimes do and sometimes guess. Measure behavior from transcripts or test runs, not from asking.
- **Headless test runners** (`claude -p`) inherit the parent session's identity variables. Unset `CLAUDECODE` and `CLAUDE_CODE_*` messaging/session vars, turn off the Skill tool so the installed old version can't leak in, and give each arm the skill text by path.
- **Windows:** a fixture reset fails if any shell still has its cwd inside the fixture (`cd /` first). PATH shims must use MSYS form (`/c/...`), because `C:/...` splits on the colon and the real CLI wins.
- **Never let an agent run a vendor's "install agent tooling" command** during review. It may write into directories managed by a skill sync.
