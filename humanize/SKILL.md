---
name: humanize
description: Strip Claude writing tells (em-dashes, "Not X, but Y" contrastives, colon-declarations, delve/realm/seamless/robust lexical cluster, wrap-up closers, synthetic empathy, collective modality) from text via the globally-installed `humanize` CLI. Invoke when the user asks to humanize, de-AI-ify, de-Claude, clean up LLM-sounding text, or make output sound less like a bot. Also use proactively after generating prose that will be shown verbatim to a human (LinkedIn posts, emails, proposals, essays) when the content should read as authored by a real person rather than an assistant.
---

# humanize

The `humanize` CLI strips Claude writing tells via an LLM rewrite pass + light heuristic texture. Source: https://github.com/dimitri-vs/elevate-humanizer. The humanization logic is a standalone CLI so it's usable outside Claude Code (scripts, other agents, manual shell use); this skill is just the integration layer.

## How to invoke

Reads stdin, writes to stdout. `ANTHROPIC_API_KEY` is sourced from (in order):
1. The process environment.
2. A `.env` file auto-loaded via `python-dotenv` — **the canonical location is `~/.env`** (i.e. `C:\Users\Dimitri\.env` on Windows). A per-project `.env` in cwd or any parent also works.

If `humanize` errors with `"ANTHROPIC_API_KEY is not set"`, run `grep ANTHROPIC ~/.env` — if nothing matches, the key is missing from that file and needs to be added there (or exported in the shell). Do not re-add it to a project `.env` as a workaround; put it in `~/.env` once.

> **Bash timeout:** Default model is now Sonnet 4.6 (fast, ~15-30s for most inputs). For long inputs (1000+ words), set `timeout: 300000` or use `run_in_background: true`. Pass `-m claude-opus-4-7` for maximum quality on high-stakes content (slower, 1-3 min).
> - **If Claude Code's Bash display truncates the output** (appears empty after a long run), re-run redirecting to `/tmp/humanize/out.md` and `cat` the file (see [Temp-file pattern](#temp-file-pattern) below).

## Routing: which mode (decide this first)

Two modes, chosen by what the text is and where it goes. Measured against the Pangram 4 detector on 2026-09-10 (details in the elevate-humanizer repo CLAUDE.md):

| Situation | Mode | Why |
|---|---|---|
| Everyday: Slack, email, Basecamp, client updates. Text drafted by Claude. | **Default pass** (`humanize --open -c "..."`). Add `-r business` / `-r personal` when obvious. | Strips the tells human readers notice and fixes the skeleton (paragraph length, anaphora, semicolons, closers) against Dimitri's own baseline. Short messages under ~70 words often pass detectors outright; anything longer that Claude composed will still read as AI to a classifier, and that is accepted for this tier. |
| High stakes: Hacker News, Reddit, anywhere an "AI-edited" flag costs reputation. **Dimitri wrote or dictated the text himself.** | **Tidy pass** (`humanize --tidy -c "..."`). | Typos, punctuation and speech-to-text slips only. No rewrite, no exemplars, no injected typos; reverts to the input if the model changed more than ~20% of the words. The only mode measured to keep a Human verdict: his dictation scored 0.03, a full rewrite of the same words 0.98. |
| High stakes, but the draft was composed by Claude. | **No passing mode exists.** Say so. | Every rewrite strategy tested (any frontier model, any prompt, base-model paraphrasing, note formats, mechanical corruption) scored ~1.00 AI at 150+ words. Offer the workable path instead: Dimitri dictates or types the post in his own words (a rough voice memo transcript is enough), then `--tidy`. Claude can supply a fact list or outline for him to speak from, never the prose. |

Do not "upgrade" a high-stakes Claude draft by running it through the default pass twice, with Opus, or with heavy texture; none of that moves a detector. Do not pass Claude-composed text through `--tidy`; it will not help and the similarity guard may reject it.

## Default delivery: `--open` (do NOT re-type the result)

For the common interactive case — the user asked you to draft a message and humanize it, or to humanize text you just generated — **always pass `-O`/`--open`**, including when you also pass `-s` steering. The CLI writes the humanized result to a self-cleaning temp file and launches it with the OS default `.md` handler (currently Notepad++ on this machine), printing **only the file path** to stdout. The *only* time you omit `--open` is when the output is being piped onward (e.g. `| Set-Clipboard`) or consumed by another tool.

```bash
humanize --open --context "<what this message is>" <<'EOF'
...draft body...
EOF
```

Then tell the user something brief like "Opened the humanized version — `<path>`." **Do not paste the humanized text back into the terminal.** The whole point is to avoid re-transcribing prose you already drafted: draft it silently, pipe it through `humanize --open`, and report that it's open. Never show the pre-humanized draft either — go straight to the humanized file.

**Away-from-computer exception:** if remote control is active, or Dimitri says he's on mobile / away from his computer, paste the full humanized text into chat *in addition to* reporting the path — he can't see the opened editor window from his phone. The pre-humanize draft still never gets shown.

Old temp files (>24h) are swept automatically on each `--open` run; no cleanup needed.

## The opened file is the working copy (send contract)

For outbound messages (the humanize-first default in the global CLAUDE.md), the opened temp file is the working version — Dimitri may edit and save over it, or not. **Always re-read it before sending:** "send it" = file contents as-is; "make X changes and send" = apply X on top of the file's current contents, then send. Never send from a chat-displayed or in-memory copy. Saving over the same file also feeds the [corpus flywheel](#corpus-flywheel-style-distillation-from-manual-edits) below.

## Always pass `--context`

The humanizer cannot see what kind of message it's rewriting — it only gets the text. So **always pass `-c`/`--context`, and make it a short paragraph, not a label.** Cover four things: what the message is, who reads it, what they already know, and what the message has to accomplish. The prompt uses this to decide what to cut, which is where most of the excess length in a draft lives; a bare "Slack reply" only sets the register and greeting (by default the humanizer drops the greeting, and only keeps one when the context says it's an initial/cold outreach).

This is distinct from `-s`/`--steering` (explicit rules, passed rarely). Context is a situational description, passed every time. Examples:

```bash
humanize --open --context "Slack thread reply to a teammate who asked whether the deploy is blocked. She already knows the staging bug from yesterday's thread; she needs a yes/no and the ETA." <<'EOF' ... EOF
humanize --open --context "Cold outreach email to an ecommerce founder who has never heard of us. Goal is one reply, not a pitch; keep the greeting." <<'EOF' ... EOF
humanize --open --context "Basecamp reply in an ongoing thread with a non-technical client. He has the full history; this only needs to confirm the scope change and the new date." <<'EOF' ... EOF
```

If you genuinely can't tell, default to describing it as a reply in an ongoing thread — that's the common case and the safer assumption (no greeting).

## Tell it what the reader already knows (in `--context`, not `--ref`)

The rewrite's most common failure is length: it keeps sentences that restate what the reader already knows, because it cannot see what they know. Put that knowledge in the context paragraph in your own words ("they have read the post and know the dollar figures", "he has the full thread"). Measured 2026-09-10: a paragraph context alone cut a 320-word HN comment to 79% of its length. Do NOT paste the source document in via `--ref`: the rewrite imports facts from it (see the flags table).

```bash
humanize --open -r reddit --voice --context "Reply comment on HN by the post's author. Readers have read the post and know the cases and the dollar figures; several think the author is gaming companies. The comment must correct the 'title is a flex' read with the numbers and answer the ethics objection. Thread tone is terse." <<'EOF'
...
EOF
```

Recommended invocation for anything public or client-facing: paragraph `--context` + `-r` register + `--voice`. For a quick internal Slack reply, `--context` alone is enough.

## Author pass (do this BEFORE calling the CLI)

The CLI is an editor: it fixes style but cannot know what you know. Detector A/B testing (2026-07, Pangram 3.3.2) showed that lexical tells and typo texture do NOT separate "reads AI" from "reads human" - author-level texture does, and only the drafting agent can supply it, because only it has the conversation context. Before piping a draft through `humanize`, revise the draft:

1. **Count the words, then cut to the budget.** Run `wc -w` on the draft before it goes near the CLI. The CLI removes about 14% and no more (corpus median tool/input 0.86, n=414 runs), so whatever you hand it is roughly what Dimitri gets. These are the lengths he actually sends, measured across 387 real messages (2026-07-22 to 2026-09-11; study in the elevate-humanizer repo notes):

   | Message type | Aim for | Hard cap |
   |---|---|---|
   | Slack / Basecamp / Discord / Upwork reply to a client | 90 | 150 |
   | Internal Slack DM or team channel post | 70 | 120 |
   | Reply in an existing email thread | 100 | 150 |
   | Weekly or status update email | 150 | 200 |
   | First-contact or cold outreach email | 120 | 200 |
   | Civic or volunteer email | 150 | 190 |
   | Consumer claim, vendor dispute, anything formal | 200 | 330 |
   | Public post (LinkedIn, Reddit, HN, Google Business Profile) | 150 | 300 |
   | SMS | 50 | 80 |

   Draft to the **aim**, not the cap. Over the aim, delete a whole point; do not compress every sentence, because uniform density is itself an LLM signature. Cut in this order, which is the order he deletes in (measured over 252 of his edits): the explanation of why you did it that way, reassurance he did not ask for, the second ask riding along with the first, the promise of what you will do next, then any sentence the thread already carries. Numbers and links survive - he cuts prose and keeps data. Leave room for one sentence of actual opinion ("my worry is...", a direct question); that is what he most often adds back. A cap breach is a decision you state in one line when you report the file, not a default. Pass the budget to the CLI too: `-s "Target: N words or fewer."` (named numbers move the model where the standing "shorter" rule does not).

2. **"Make it shorter" means half, not a fifth.** Across the 24 messages he sent back for another pass, what he ended up sending was 0.63x the first draft (IQR 0.47-0.78). Use `humanize --edit <path> -s "about half as long" --open` (see Iterating on an opened result); never hand-edit the opened file.

3. **Ground it in the real situation.** Weave in 1-2 concrete facts from the actual session or thread: the delay being apologized for, what actually happened while testing ("the benchmark nearly crashed my machine"), the specific device or project name ("my Legion 5", not "my machine"). Only facts actually observed in the session, user memory, or the thread - never invented color.

4. **Label epistemic status honestly.** You know which claims were verified this session and which are inference. State verified things flatly; where the draft is genuinely guessing, say so in plain words using Dimitri's actual constructions: "Best guess: ...", "could be wrong on the exact mechanism", "I'm guessing", "I think", a trailing "or something?" or "(crashed/stuck?)". The CLI is forbidden from inventing hedges, so uncertainty must already be in the draft - the 2026-08 corpus distillation showed adding these was one of his most frequent manual edits. Uniform confidence across every claim is the deepest AI tell; honest unevenness is free because it's true.

5. **Commit, don't offer menus.** "I'll attach the benchmark script" beats "Happy to send the script, screenshots, or traces if useful." Pick the one thing that will actually happen.

6. **Name the AI tooling when writing to technical teammates.** When an agent actually did the work, say so the way Dimitri does: "have Claude run an analysis on X", "you can paste this straight into Claude Code", "Codex xhigh double-check". Don't launder agent work into "I analyzed". (Client-facing drafts: only where the relationship already has that vocabulary.)

7. **Don't over-commit on timing.** Crisp promises create obligations: prefer "ASAP" over "same day", "a week or two" over "in a week", "today or tomorrow" over a named weekday - unless a hard date is genuinely required (deadlines, contracts, quotes).

## Structural check (AFTER the CLI returns)

Read the output file once before reporting the path. Two things the LLM pass under-delivers:

- **Block uniformity:** if every paragraph landed at the same 2-3 sentence shape, make one manual edit — merge two paragraphs, or break one after its first sentence. Plain file edit, no API call.
- **Over-repaired looseness:** if the draft had a loose construction the rewrite tidied away (a dangling clause, an abrupt topic graft, a mid-sentence "..."), consider restoring it. Looseness is texture, not error.

For high-stakes public posts (HN, Reddit, LinkedIn), still surface the result for Dimitri's own quick pass. The agent's texture is sampled from Claude's distribution; his is not, and that difference is what detectors measure.

### Iterating on an opened result

The printed path is a normal file you can edit:

- **Verbatim edit the user dictates**, one sentence or less ("drop the last line", "change X to Y", "attach the template"): `Read` the file, `Edit` it in place (Notepad++ prompts to reload on focus). No API call. The test is whether the new words are the user's; if the agent is composing any prose, or the change spans more than a sentence, it is not this case.
- **Any rewrite the agent authors** ("shorter", "a third shorter", "warmer", "tighter", "less stilted", restructuring): `humanize --edit <path> -s "<the change>" -c "<same context>" --open`. Edit mode applies only that change, returns every other sentence byte-identical, holds the new sentences to the humanizer's rules, and writes a NEW file (the original stays as it was). Report the new path. Never hand-write the shortened version into the opened file: agent-composed sentences carry Claude tells, and the opened file is the corpus sample of Dimitri's edits, so agent prose in it is misrecorded as his (2026-09-11: three hand-shortened drafts in one morning before he called it out).
- **Re-humanize differently** ("do it harder", "treat as a Slack DM"): `cat <path> | humanize -t --open -c "..."`, a fresh full pass. Use this only when the whole register is wrong; for a targeted change, `--edit` leaves the untouched text alone.
- **Lines the user dictated must survive the pass.** When the user asked for a specific phrase ("say 'I promise'", "the learning curve is mine"), the compression pass may strip it as a tell. Fence it: `-s "Keep these lines verbatim: '...', '...'"`. If a pass drops one anyway, restore that line by hand (it is the user's words, so the verbatim-edit rule applies).
- **After sending, stamp the truth**: `humanize --mark-sent <path>` on the file whose content actually went out. It writes `<stem>-sent.md` into the corpus and `sent_at` into the meta. This is what distillation trusts; the inferred tool-vs-final pair is the fallback. Do it in the same Bash call as the send when possible.

**Prefer a heredoc over creating a scratch file.** Anything generated in the current conversation should pipe directly via heredoc, not via a temp file you cat back. Single-quoted delimiters (`<<'EOF'`) prevent shell expansion, so apostrophes, `"quotes"`, URLs, and `$` all pass through verbatim.

```bash
# Short text: positional arg or echo pipe
humanize "your short claude-sounding sentence"
echo "your short text" | humanize

# Multi-paragraph prose generated in this session: heredoc + --open (default delivery)
humanize --open <<'EOF'
First paragraph of the draft.

Second paragraph, with apostrophes, "quotes", URLs (https://example.com) and $dollar signs preserved because the single-quoted delimiter blocks shell expansion.
EOF

# Heredoc + steering (only pass steering when user gives explicit direction) — still --open
humanize --open -s "This is a Twitter post." <<'EOF'
...draft body...
EOF

# Content already on disk
cat draft.md | humanize

# Force heavy heuristic texture (misspellings, dropped apostrophes, etc.)
cat proposal.txt | humanize --heuristic-texture

# Disable heuristic pass (unconditional cleanup like em dashes still runs)
cat polished_draft.md | humanize --no-heuristic

# Maximum quality on high-stakes content (slower)
cat essay.md | humanize -m claude-opus-4-7 > essay.humanized.md

# Raise max-tokens if output looks truncated
humanize --max-tokens 16000 <<'EOF'
...long essay body...
EOF

# Write the result to a new file
cat draft.md | humanize > draft.humanized.md
```

Any warnings go to stderr and do not pollute piped output.

## When to use

- The user asks to humanize, de-AI-ify, de-Claude, or clean up AI-sounding output.
- You just generated prose that will be shown verbatim to a human (LinkedIn post, cold email, client proposal, essay, blog draft) and it should sound human-authored.
- The user pastes obviously-Claude-generated text and wants it rewritten.

## When NOT to use

- Technical documentation, code comments, API reference, JSON, or structured data. The CLI strips structure (bullets, headers, colon-before-list) which is appropriate for prose but wrong for reference material.
- Text that is already conversational and plainly-written. The humanizer does real work only when tells are present.
- Text that is already conversational and short — the humanizer does real work only when tells are present. (There's a built-in degenerate-output guard that only reverts near-empty rewrites, so normal short text passes through fine.)

## Unconditional cleanup (always on)

Em dashes, en dashes, and smart quotes are stripped on every run regardless of heuristic mode. These are mechanical Claude tells, not style preferences. Even `--no-heuristic` will still clean these.

## Heuristic texture (light by default)

The heuristic pass adds surface-level "rushed typing" signals: occasional misspellings, dropped apostrophes in safe contractions (don't → dont), hyphen removal in compound words (real-time → real time), exclamation marks demoted to periods.

**Default behavior is `light`:** conservative probabilities, at most one transform per category. Appropriate for most content (social posts, emails, proposals). The reader shouldn't consciously notice the texture.

This deliberately includes formal and quasi-legal correspondence (escalations, warranty disputes, client emails): small typos and split compounds are expected in real human email and are part of the disguise. Do not reach for `--no-heuristic` just because the content is high-stakes; reserve it for cases where the *user* asks for a verbatim-clean pass.

- `light` (default): conservative probabilities, at most one transform per category.
- `heavy` (casual): aggressive probabilities, multiple transforms. For Slack DMs, SMS, quick replies.
- `none` / off: skip the heuristic pass entirely. Unconditional cleanup still runs.

Override the default with:
- `-t, --heuristic-texture`: force heavy intensity.
- `-l, --heuristic-light`: force light (same as default; useful for explicitness).
- `--no-heuristic`: disable the heuristic pass entirely.

The three flags are mutually exclusive.

## Flags summary

| Flag | Default | Purpose |
|---|---|---|
| `--tidy` | off | Light copyedit only, for text Dimitri wrote or dictated. See [Routing](#routing-which-mode-decide-this-first). |
| `-r, --register {reddit,personal,business}` | none | Which slice of Dimitri's own writing to compare against and draw exemplars from. |
| `--exemplars N` | 3 | Real (PII-scrubbed) samples of his writing injected into the prompt. `0` disables. |
| `--structural-retries N` | 0 | Extra LLM calls when the structural gate still flags the output. Only for pieces where a few more seconds are worth it. |
| `--no-structural` | off | Skip the structural gate entirely. |
| `--no-tell-pass` | off | Skip the lexical tell pass: a span lint on the rewrite (counted-list preambles, rhetorical setups, contrastives, punchy metaphor verbs, injected idioms, engagement closers) plus one extra low-temperature call that edits exactly those spans. On by default; costs a few seconds. |
| `--lint` | off | No LLM call: print the tell spans in the input and exit 1 if any are actionable. Run it on a hand-edited draft before posting (`humanize --lint < draft.md`). |
| `--voice` | off | Extra pass that re-voices the rewrite into Dimitri's own style from the distilled stylometry profile plus six real samples; content locked, reverted if length drifts past 20% or an anchor is lost. Blind-judged 2026-09-10: a small, consistent gain ("a little more my style"), never a loss. Use it on anything public or client-facing; skip it on internal one-liners. Pair with `-r`. |
| `--ref FILE` | none | EXPERIMENTAL, not recommended (Codex review 2026-09-10). A file the reader has already read. In its first run the rewrite imported three true facts from the article that were not in the draft, and no guard catches added facts. Do not use it for anything you will not fact-check line by line against the source. Length gains come from the paragraph `--context`, not from this. |
| `-s, --steering TEXT` | none | Extra steering rules appended to the prompt. Only pass when the user gives explicit direction (e.g. "this is a Twitter post") or specific constraints. Do not add agent-decided steering - the tool runs well without it. |
| `-c, --context TEXT` | none | Short description of what the message is (Slack thread reply, cold outreach, etc.). **Pass this every run** — it drives register and greeting. See [Always pass --context](#always-pass---context). |
| `-t, --heuristic-texture` | — | Force heavy heuristic intensity |
| `-l, --heuristic-light` | — | Force light heuristic (same as default; for explicitness) |
| `--no-heuristic` | — | Disable heuristic pass (unconditional cleanup still runs) |
| `-m, --model ID` | `claude-sonnet-4-6` | Anthropic model ID. Use `-m claude-opus-4-7` for maximum quality on high-stakes content. |
| `--temperature FLOAT` | 0.8 | LLM sampling temperature |
| `--max-tokens INT` | 8192 | Output-length ceiling in tokens. Raise for long-form inputs that hit truncation |
| `--style-file PATH` | `~/.config/humanize/style.md` | Path to a personal style file. Loaded automatically if present. |
| `--no-style` | — | Skip loading the personal style file for this run |
| `--substitutions-file PATH` | `~/.config/humanize/substitutions.py` | Personal regex substitutions (`SUBSTITUTIONS=[(pattern, replacement), ...]`), applied deterministically as the final step. Auto-loaded if present. |
| `--no-substitutions` | — | Skip personal substitutions for this run |
| `-O, --open` | — | Write the result to a self-cleaning temp file and open it with the OS default app; print only the path to stdout. Default for interactive "draft + humanize" delivery (see above). |

## Personal style

The CLI auto-loads `~/.config/humanize/style.md` if it exists. This contains persistent personal writing preferences (formatting conventions, tone rules) injected into the LLM prompt on every run. If the file is absent, nothing changes. Use `--no-style` to skip it for a single run.

### When to keep the personal style vs. dial it back

The personal style is derived from Dimitri's real correspondence and is aggressive about compression: cut to ~half length, strip greetings/closers/sign-offs, answer-and-stop. That's exactly right for the **common case** and should stay the default:

- **Correspondence (default — style ON):** Slack replies, emails, Upwork messages, DMs, status updates, quick replies. Just run `humanize --open`. The aggressive compression is the point here.

But that same compression can **strip substance from load-bearing copy**, where length and specific claims are the deliverable. For these, dial it back:

- **Marketing / sales / proposals / landing-page copy / essays / cover letters:** the value proposition and any CTA are load-bearing — don't let them get compressed away. Either:
  - keep his voice but fence the substance: `humanize --open -s "Preserve the value proposition, specific claims, and any call-to-action. Don't cut length or drop substantive points."`, or
  - skip the personal style entirely: `humanize --open --no-style` (still strips generic Claude tells, just without the aggressive personal compression).
- **Formal / legal / technical docs:** `--no-style` (and usually don't humanize at all — see [When NOT to use](#when-not-to-use)).

Rule of thumb: if making the text *shorter and barer* would lose information the reader needs, dial back the style. If shorter-and-barer is strictly better (most correspondence), let it run.

## Personal substitutions (guaranteed)

Some conventions are mechanical and must always hold, so they're enforced deterministically (not via the prompt) in `~/.config/humanize/substitutions.py` — a `SUBSTITUTIONS = [(regex, replacement), ...]` list applied as the final step every run. Currently: `e.g.`→`eg.`, `i.e.`→`ie.`, and commas pushed outside quotes (`"like this",`). Universal non-typable Unicode (em-dashes, arrows `→`→`>`, ellipsis) is stripped always, regardless of this file. Edit that file to add conventions; no need to touch the prompt or this skill.

## Corpus flywheel (style distillation from manual edits)

Every `--open` run automatically archives three files to `~/.config/humanize/corpus/`, keyed to the temp file's stem (e.g. `humanize-20260722-083219`):

- `<stem>-input.md` — the pre-humanize draft
- `<stem>-tool.md` — what the tool produced
- `<stem>-meta.json` — run parameters (context, model, heuristic level)

Dimitri edits the opened temp file in place (Notepad++) and saves over it. When the 24h sweep later deletes that temp file, it first compares it against the archived `-tool.md`; if they differ, the edited version is saved as `<stem>-final.md`. A tool/final pair is a recorded example of his real editing function. Pass `--no-corpus` to skip archiving a sensitive run.

**The one habit that makes this work: save edits over the same opened file.** Remind Dimitri of this if he mentions tweaking a humanized draft somewhere else.

### Distillation procedure

When Dimitri asks to "distill the humanize corpus" (or during a periodic tune-up, roughly monthly):

1. Generate the pair report: `uv run C:\Users\Dimitri\Code\elevate-humanizer\style-profile\corpus_report.py -o <scratchpad>\corpus-diffs.md` (word-level diff of every `-tool.md` vs `-final.md` pair with its `-meta.json` context).
2. Cluster the recurring edit types across pairs (phrases he consistently cuts, constructions he adds, length deltas, punctuation habits). One-off content edits are noise; only patterns that appear across multiple pairs are signal. Also check `meta.json`'s `prompt_version` — a pattern seen only under an older prompt version may already be fixed.
3. Sort each confirmed pattern into its right home: generic LLM tells go in the repo's base prompt (`prompts/base.py`, bump `PROMPT_VERSION`), personal voice goes in `~/.config/humanize/style.md`, draft-level texture only the agent can supply goes in this skill's author pass, mechanical habits go in `heuristic.py`.
4. Propose the changes, showing the evidence pairs for each. Apply on his approval.
5. After applying, run `corpus_report.py --mark-distilled` to move processed trios into `~/.config/humanize/corpus/distilled/` so the next distillation only sees fresh pairs. (First distillation: 2026-08-20, 53 pairs.)

## Temp-file pattern

When a heredoc won't do — long-form input, text containing a literal line `EOF`, truncated Bash-tool output, or a run where you want the raw input preserved for diffing — stage the input and output under `/tmp/humanize/`. Bash resolves `/tmp/` portably: real `/tmp` on Linux/macOS, and a mapped path under `AppData\Local\Temp\` on Git Bash / MSYS (Windows). Mirrors the pattern used by the `codex-review` skill.

```bash
mkdir -p /tmp/humanize

# Stage the input via heredoc (same 'EOF' quoting rules apply)
cat > /tmp/humanize/in.md <<'EOF'
...draft body, multi-paragraph, quotes, URLs, $dollars all safe...
EOF

# Run and save the humanized output
humanize < /tmp/humanize/in.md > /tmp/humanize/out.md

# Read it back to show the user
cat /tmp/humanize/out.md

# Optional: diff before/after
diff /tmp/humanize/in.md /tmp/humanize/out.md
```

> **Do not use the `Write` tool to create files in `/tmp/humanize/`.** On Windows/Git-Bash, the `Write` tool's `/tmp/` path does NOT resolve to the same filesystem location as bash's `/tmp/`, so `cat` will silently fail to find the file. Always stage input with a bash heredoc as above. (Same caveat as `codex-review`.)

When running several humanize passes in one session (e.g. comparing models, or iterating with different `-s` steering), use descriptive filenames: `/tmp/humanize/proposal-opus.md`, `/tmp/humanize/proposal-sonnet.md`.

## Typical workflow from within a Claude Code session

When you have text in the current conversation that should be humanized:

1. **Default: pipe via heredoc with `--open`.** A single-quoted heredoc (`humanize --open <<'EOF' ... EOF`) handles multi-paragraph prose, apostrophes, quotes, URLs, and `$` signs without scratch files or escaping gymnastics, and the result opens in a file instead of flooding the terminal. Report the path; don't re-type the content. See [Default delivery](#default-delivery---open-do-not-re-type-the-result).
2. **If the content is already on disk**, `cat path.md | humanize --open`.
3. **Drop `--open` only when the caller needs the text on stdout** — e.g. piping onward (`| Set-Clipboard`), or another tool consumes it.
4. **Use the [temp-file pattern](#temp-file-pattern)** when (a) the content is genuinely long-form (roughly >5KB / essay-length), (b) you need to keep the input around for diffing/comparison, (c) the text contains a literal line `EOF` that would terminate the heredoc early, or (d) the Bash-tool display truncated a previous run.

Avoid creating scratch files in the project working directory just to `cat` them through the pipe — either use a heredoc (default) or `/tmp/humanize/` (when heredoc won't do).

## Example

Input (Claude-flavored LinkedIn-style):

> I'm incredibly excited to share that we just wrapped up a pivotal project with a leading fintech client. Our team didn't just build a chatbot — we crafted a comprehensive conversational AI solution that seamlessly integrates with their existing infrastructure.

After `humanize`:

> Just wrapped up a big project with a leading fintech client. We built a conversational AI that plugs into their existing infrastructure.

Em-dash gone, contrastive reframes collapsed, `pivotal`/`comprehensive`/`seamlessly` stripped, corporate preamble ("I'm incredibly excited to share") dropped.
