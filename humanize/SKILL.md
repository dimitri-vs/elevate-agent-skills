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

Two modes, chosen by what the text is and where it goes.

**Framing note (2026-09-11):** this tool is no longer aimed at defeating AI detectors. Dimitri decided to accept detection and optimise for a concise, clear style that is recognisably his. Judge your output by whether it reads like him and says what he meant in fewer words, not by whether a classifier would flag it. The detector findings below are kept only because they still determine which mode to use.

| Situation | Mode | Why |
|---|---|---|
| Everyday: Slack, email, Basecamp, client updates. Text drafted by Claude. | **Default pass** (`humanize --open -c "..."`). Add `-r business` / `-r personal` when obvious. | Strips the tells human readers notice and fixes the skeleton (paragraph length, anaphora, semicolons, closers) against Dimitri's own baseline. Detection is not a goal here; a classifier will flag anything longer that Claude composed, and that is accepted. |
| High stakes: Hacker News, Reddit, anywhere an "AI-edited" flag costs reputation. **Dimitri wrote or dictated the text himself.** | **Tidy pass** (`humanize --tidy -c "..."`). | Typos, punctuation and speech-to-text slips only. No rewrite, no exemplars, no injected typos; reverts to the input if the model changed more than ~20% of the words. The only mode measured to keep a Human verdict: his dictation scored 0.03, a full rewrite of the same words 0.98. |
| High stakes, but the draft was composed by Claude. | **Default pass, and say plainly that it will read as AI-assisted.** | No rewrite makes Claude-composed prose read as human-written at length: every strategy tested through 2026-09-11 (any frontier model, any prompt, base-model paraphrasing at 8B and 14B, a commercial humanizer, mechanical corruption) either failed the detector or mangled the facts. If reading as his own writing genuinely matters for this piece, offer the one thing that works: Dimitri dictates or types it in his own words (a rough voice memo transcript is enough), then `--tidy`. Claude can supply a fact list or outline for him to speak from, never the prose. |

Do not "upgrade" a high-stakes Claude draft by running it through the default pass twice, with Opus, or with heavy texture; none of that improves it. A second pass adds fresh tells rather than removing them. Do not pass Claude-composed text through `--tidy`; it will not help and the similarity guard may reject it.

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

**Once you have reported the path, the tab is his.** He often starts typing in the opened file within seconds. Do every check you intend to do (facts against the session, the author pass, the structural check below) before you report the path, in that same turn. After that, do not start a change he did not ask for: no `--edit`, no re-humanize, no in-place edit, even when you have just spotted a real error. A second version opened while he is mid-edit forks the message: it is built from the file on disk without the words he is typing, and he ends up with two tabs to reconcile (reported 2026-11-05). Put the correction in chat instead, quoting the line and the fix ("para 2 says Thursday, the thread says Friday"), and let him fix it in the tab or tell you to. Changes he asks for go through the routes in [Iterating on an opened result](#iterating-on-an-opened-result) as before.

Notepad++ auto-saves humanize drafts (a PythonScript startup hook at `%APPDATA%\Notepad++\plugins\config\PythonScript\scripts\startup.py`, added 2026-11-05: about 2 seconds after a keystroke and on focus loss, only for `%TEMP%\humanize\humanize-*.md`), so the file on disk normally matches what he sees and `--check-edited` picks up his edits without a manual save. A "left open, it has unsaved changes" line on stderr from `--edit` or `--mark-sent` now means he was typing at that moment, or the hook did not load: tell him, because the version you just read may be missing his last words.

**Any difference between the opened file and what the tool wrote is Dimitri's edit, by definition.** The tool's output is archived at `~/.config/humanize/corpus/<stem>-tool.md` the moment `--open` runs, so the test is mechanical — run **`humanize --check-edited <opened path>`** before any `--edit`, re-humanize, or send: exit 0 means untouched, exit 1 means edited and prints the tool→current diff to stdout, exit 2 means it can't tell (no corpus copy, so fall back to reading the file). This replaces hand-rolling the corpus path yourself; you no longer need to know the `<stem>` or the `~/.config/humanize/corpus/` layout. Treat every changed line the diff shows as dictated text (fence it verbatim, see below; never "restore" it). Do not reason from the content: a sentence that looks off-topic, wrong, or tool-invented is still his if the diff says the file changed after the tool wrote it. On 2026-09-17 an agent read his added Cloudflare domain link as an `--edit` hallucination, put the tool's paragraph back, and sent; the send went out without his edit and needed a follow-up email.

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

## Counsel first (liability-sensitive messages)

Some drafts need a second agent before the author pass, not after: the `counsel` skill (`~/.claude/skills/counsel/`). It reviews the draft in isolation from this session for over-disclosure, over-precision, over-commitment, confessed fault and foreclosed options, and returns findings you apply before humanizing. Run it, unprompted, when the draft is any of:

- a client message in a security incident or breach notice, or any answer to "was our data accessed"; anything asserting a cause, a fix, or that something is secure in an incident or dispute context (a routine "fixed, deployed" is not that);
- a billing, rate, contract, scope or warranty dispute; a change to the client's live data; a contract mismatch we are disclosing;
- the substantive insurance, warranty or consumer claim messages (reply to a denial, counter, close-out, executive outreach, anything asserting entitlement or declining an offer; not "here is the receipt"), and any support claim asking for money, a replacement or an exception;
- anything the recipient could forward to a lawyer, insurer, regulator or platform dispute desk.

Not for routine support replies ("fixed, deployed"), status updates, scheduling or internal messages; the author pass below handles those. When counsel ran, say so in `--context` ("draft has had a counsel pass; keep its cuts, don't re-add specifics, and preserve its qualifiers word for word"). The author pass otherwise puts the numbers back and compresses qualifiers like "in the logs we have for that window" into "we saw no access", which undoes the review. Counsel's apply policy (how hard to lean on its findings, which ones go to Dimitri as a yes/no) is in its own SKILL.md.

## Author pass (do this BEFORE calling the CLI)

The CLI is an editor: it fixes style but cannot know what you know. What Dimitri's blind judging (2026-09-10) and his edit corpus reward is author-level substance (the right length, one real opinion, honest uncertainty, a concrete fact from the thread), and only the drafting agent can supply that, because only it has the conversation context. Before piping a draft through `humanize`, revise the draft:

1. **Answer the original ask, from the recipient's seat.** By the time the work is done the session has pivots, decisions, fixes and detours in it, and the draft tends to answer the session instead of the message that started it. Before drafting, write two things down for yourself: the original ask in one line (the sentence they actually sent, or the question that opened the thread), and what the recipient can see (their own message, earlier replies in the thread, the deliverable you are handing over). Everything else in the session is invisible to them. Then write only the answer to that one line, in their words for the problem, not yours.

   Cut on sight, because these are what Dimitri deletes by hand (2026-10-07 scorecard, 289 sent messages: roughly half of them had at least one such cut, and the cut was already in the agent's draft, not something the CLI added): commit hashes and branch names; "also fixed X while I was in there", "also shipped #827"; "I had Claude run..." or "I'm still going through everything" as narration of how the work went; implementation detail of a change they did not ask about; the explanation of why you did it that way; a pivot or alternative you considered. The one exception is a fact that changes what the recipient should do next; say that fact, not the story behind it.

   Mark anything you are guessing as a guess while you are here, in Dimitri's own constructions: "Best guess: ...", "I *believe*", "probably", "Kendall(?)", "like I believe you said", "could be wrong on the exact mechanism", a trailing "or something?". He adds these back to 1 in 6 sent messages; the CLI is forbidden from inventing hedges, so if the draft is flat the sent message will be too. Uniform confidence across every claim is the deepest AI tell; honest unevenness is free because it's true.

2. **Count the words, then cut to the budget.** Run `wc -w` on the draft before it goes near the CLI and print the count with the aim in your report line (e.g. "112w, aim 90"); a report without it is incomplete. Length is the strongest predictor of whether he sends the tool output untouched: 50% under 50 words, 26% at 51-100, 11% at 151-250, 0 of 13 over 250 (2026-10-07 scorecard). The CLI removes about 14% and no more (corpus median tool/input 0.86, n=414 runs), so whatever you hand it is roughly what Dimitri gets. These are the lengths he actually sends, measured across 387 real messages (2026-07-22 to 2026-09-11; study at `elevate-humanizer/style-profile/2026-09-11_word-budget-study.md`, gitignored because it quotes real messages):

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

3. **"Make it shorter" means half, not a fifth.** Across the 24 messages he sent back for another pass, what he ended up sending was 0.63x the first draft (IQR 0.47-0.78). Use `humanize --edit <path> -s "about half as long" --open` (see Iterating on an opened result); never hand-edit the opened file.

4. **Ground it in the real situation.** Weave in 1-2 concrete facts the recipient asked about or needs: the delay being apologized for, the specific device or project name ("my Legion 5", not "my machine"), what the test showed. Only facts actually observed in the session, user memory, or the thread - never invented color, and never facts about how the work went (see step 1).

5. **Commit, don't offer menus.** "I'll attach the benchmark script" beats "Happy to send the script, screenshots, or traces if useful." Pick the one thing that will actually happen. "Say the word and I'll X" is the menu in one sentence; he deleted it from half the messages it reached (6 of 12) and never writes it himself. Ask the direct question ("Want it stricter?") or just do X.

6. **Name the AI tooling when it is part of what the recipient needs.** When a technical teammate will use or run the thing, say so the way Dimitri does: "have Claude run an analysis on X", "you can paste this straight into Claude Code", "Codex xhigh double-check". Don't launder agent work into "I analyzed". But "I had Claude run it today" as narration of how the work got done is session bleed and gets cut (step 1). Client-facing drafts: only where the relationship already has that vocabulary.

7. **Don't over-commit on timing.** Crisp promises create obligations: prefer "ASAP" over "same day", "a week or two" over "in a week", "today or tomorrow" over a named weekday - unless a hard date is genuinely required (deadlines, contracts, quotes).

8. **Three habits the CLI inherits from the draft instead of fixing** (diff-mined over 1,155 runs, 2026-10-26). Get them right in the draft:

   - **Greeting fused into the first sentence.** When a greeting is kept, write it as one line that keeps going: "Hi James, mockups are attached as one HTML file." (continuation lowercase). Never "Hi James," on its own line, a blank line, then a new capitalised sentence. That is how he writes (318 fused vs 49 own-line in his own mail) and the CLI enforces it: it fused a split greeting in 80 of 80 runs and never split a fused one. So a fused greeting in the output is correct. Do not "restore" the split version by hand, and do not treat the greeting as a dictated line that must survive verbatim; its placement belongs to the CLI. Four times between Oct 16 and 21 an agent drafted the split form, steered "Keep the greeting 'Hi X,'", then un-fused the CLI's output as a verbatim fix, and the corpus now records those as his edits. If you fence it at all, say `-s "Keep the greeting 'Hi James,' fused into the first sentence."` The greeting word is his choice, not the tool's: pass "Hi" or "Hey" as the thread uses it.
   - **No "land".** Never "land / lands / landed / landing" for arrive, ship, merge, go live, happen or get delivered ("should land before Oct 27", "first removals land mid-December", "once the invite lands", "where things landed"). It is the word he dislikes most, it is 0.02 per 1k words in his own writing, and 54 of the 60 uses in CLI output came straight from the agent's draft. Write the literal verb: arrives, is delivered, ships, is merged, goes live, happens, shows up.
   - **Colons: write them the way you naturally would, no more.** Drafts carry 13 mid-sentence colons per 1k words against 5 in his pre-AI mail, but his edits do not fight them: he kept 76% of the CLI's colons, wrote out a flat ~20% of the prose ones no matter how many the text had or what shape the head took, kept 98% of list and data colons, and added as many colons himself as he removed (751 chains, `benchmarks/alignment/colon_evidence.py`). So: colons before a number, a link, a list or a label are never a problem; do not reach for the "Label: payoff" shape ("Fix: open the grant", "Found the issue: ...") as a punch device, since that is the kind he rewrites as a sentence; and never add a colon to the CLI's output by hand to tighten it.

## Structural check (AFTER the CLI returns)

Read the output file once before reporting the path. Two things the LLM pass under-delivers:

- **Block uniformity:** if every paragraph came out at the same 2-3 sentence shape, make one manual edit — merge two paragraphs, or break one after its first sentence. Plain file edit, no API call.
- **Over-repaired looseness:** if the draft had a loose construction the rewrite tidied away (a dangling clause, an abrupt topic graft, a mid-sentence "..."), consider restoring it. Looseness is texture, not error.

One thing that looks like damage and is not: a greeting fused onto the first line ("Hi James, mockups are attached...") is the intended shape (author pass step 8). Leave it.

For high-stakes public posts (HN, Reddit, LinkedIn), still surface the result for Dimitri's own quick pass. The agent's texture is sampled from Claude's distribution and his is not, and no tooling closes that gap - his hand edit is the last real lever, and the corpus records it.

### Iterating on an opened result

The printed path is a normal file you can edit:

- **Verbatim edit the user dictates**, one sentence or less ("drop the last line", "change X to Y", "attach the template"): `Read` the file, `Edit` it in place (Notepad++ prompts to reload on focus). No API call. The test is whether the new words are the user's; if the agent is composing any prose, or the change spans more than a sentence, it is not this case.
- **Any rewrite the agent authors** ("shorter", "a third shorter", "warmer", "tighter", "less stilted", restructuring): `humanize --edit <path> -s "<the change>" -c "<same context>" --open`. Edit mode applies only that change, returns every other sentence byte-identical, holds the new sentences to the humanizer's rules, and writes a NEW file (the original stays as it was). It also closes the parent's Notepad++ tab so only the newest version is open (left open if it has unsaved changes, and stderr says so; `--keep-parent-tab` opts out). If it exits 1 with "changed on disk while the edit was running", he typed in the file during the call: nothing was written and his tab is untouched, so wait for him to finish and re-run on the file as it is now (do not retry in a loop). Never script your own tab hunt by activating tabs: every tab whose file the sweep deleted pops a modal dialog (2026-10-05). Before running it, run `humanize --check-edited <path>` (see the send contract above); any lines Dimitri added go into `-s` as "Keep these lines verbatim: ..." and are checked in the output afterwards. Its "N/M paragraph(s) changed" report counts against the file you passed in, so a paragraph count that does not match your own draft means he already edited the file. Report the new path. Never hand-write the shortened version into the opened file: agent-composed sentences carry Claude tells, and the opened file is the corpus sample of Dimitri's edits, so agent prose in it is misrecorded as his (2026-09-11: three hand-shortened drafts in one morning before he called it out).
- **Re-humanize differently** ("do it harder", "treat as a Slack DM"): `cat <path> | humanize -t --open -c "..."`, a fresh full pass. Use this only when the whole register is wrong; for a targeted change, `--edit` leaves the untouched text alone.
- **Lines the user dictated must survive the pass.** When the user asked for a specific phrase ("say 'I promise'", "the learning curve is mine"), the compression pass may strip it as a tell. Fence it: `-s "Keep these lines verbatim: '...', '...'"`. If a pass drops one anyway, restore that line by hand (it is the user's words, so the verbatim-edit rule applies). A greeting or sign-off is not a dictated line: fencing it means the words stay, not that it keeps its own line. The CLI fusing "Hi James," into the first sentence is the rule working (author pass step 8), so never un-fuse it by hand.
- **After sending, stamp the truth**: `humanize --mark-sent <path>` on the file whose content actually went out. It also closes that file's Notepad++ tab (sent = done; unsaved tabs are left alone; `--keep-tab` opts out). It writes `<stem>-sent.md` into the corpus and `sent_at` into the meta. This is what distillation trusts; the inferred tool-vs-final pair is the fallback. **`gmail_reply.py --text-file <humanize draft>` now does this itself, only after Gmail confirms the send** (`--no-mark-sent` opts out), so don't add your own mark-sent after it. For any other sender, gate the stamp on the sender's own success marker (eg. a `SENT:` line), never chain it after a pipe: `send ... | tail -2 && humanize --mark-sent` runs on tail's exit code, so a failed send still gets stamped (happened 2026-10-28, two "sent" drafts that never went out). One send per Bash call, literal paths, no loop variables inside quoted Windows paths.

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

This deliberately includes formal and quasi-legal correspondence (escalations, warranty disputes, client emails). It is Dimitri's standing preference: his real sent mail carries the occasional typo and split compound ("experiance", "wether" are in the baseline), so a spotless draft reads less like him, not more. Do not reach for `--no-heuristic` just because the content is high-stakes; reserve it for cases where the *user* asks for a verbatim-clean pass.

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
