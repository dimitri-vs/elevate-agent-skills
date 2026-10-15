# Testing a revised skill

Tests exist to catch **regressions**, not to prove the new version is better. Real usage after shipping is the verdict. Keep it to one pass.

## Behavior test (full tier)

- **Tasks:** 2-3 read-only prompts taken from real sessions (the provenance lens proposes them), worded like real user asks, plus "what correct looks like" for each.
- **Arms:** new skill, old skill, no skill. Blind them as X/Y/Z and keep the mapping file outside the folder the judge reads.
- **Runners:** subagents or headless `claude -p` sessions. Give the skill text by path ("read it before starting, the way you'd use a loaded skill"). Forbid the Skill tool and reading other copies, so the installed old version can't leak in. Ask for a "Commands run" list in the final answer.
- **Judge:** one, scoring correctness, safety, efficiency and how many lookups the runner needed. It ranks blind first, then reads the arms' skill text to attribute behavior to specific lines. Flag any case where the new arm is worse than the old or no-skill arm as a REGRESSION. Add a second judge only if the verdict is close.
- **Expect parity on healthy infrastructure.** The traps a skill guards against usually aren't firing when you test. Parity with no-skill on basic tasks means the skill's value is in the traps; it isn't a reason to delete them.

## Trigger test (whenever the description changes)

Two conditions, the same ~10 prompts (6 that should trigger, phrased like real asks including ones that never name the tool; 4 near-misses for neighbouring tools):

1. **Baseline:** a temp project with only the installed old skill.
2. **Head-to-head:** the same, plus the new skill at project level under a different name (`<skill>-v2`), so both descriptions compete.

Run `claude -p "<prompt>" --max-turns 3 --disallowedTools "Bash,PowerShell,Write,Edit,WebFetch,WebSearch,Agent" --output-format stream-json --verbose`, then record which `Skill` tool_use fires. Put the temp projects under `~/.claude/skill-reviews/` so the usage miner excludes them. Clean prompts overstate real trigger rates, because real sessions often go straight to the CLI. The follow-up measures the real number.

## Headless runner hygiene

- Unset the parent session's identity variables (`CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_MESSAGING_*`, `CLAUDE_CODE_CHILD_SESSION`, `CLAUDE_PID`, `CLAUDE_CODE_ENTRYPOINT`) so each run is an independent session.
- Run each arm's runs for the same scenario sequentially when they share state. `cd` out of fixtures before resetting them (Windows locks a directory that's any shell's cwd).
- Save each transcript and final answer next to its grade. Grade from the transcript, not from the runner's own summary.

## Scripted trap scenarios (only for skills guarding destructive actions)

When the live test can't trigger the traps that matter (an approval-gated deploy, a log stream that never ends, a secret-heavy unlinked directory), build a fake CLI:

- A script with the tool's name, first on the runner's PATH (MSYS form `/c/...` on Windows; set PATH on the runner *process*, because exports don't persist between Claude Code Bash calls).
- It returns canned output in the real CLI's format. Use saved real `--help` texts so flag errors look genuine. Interactive commands block; mutations log `MUTATION_CALLED` before failing; bare streams run forever.
- Plant unique canary strings in secret values. The grader scans the transcript for canaries, checks the violations log and the key facts in the answer, and flags any access to real credentials.
- Self-test the harness: a good run must PASS, and planted violations or leaks must FAIL.

It's expensive to build (an agent-hour or so). The usual result is that every arm stays safe and the revised skill just gets there in fewer calls.
