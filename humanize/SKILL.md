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

## Default delivery: `--open` (do NOT re-type the result)

For the common interactive case — the user asked you to draft a message and humanize it, or to humanize text you just generated — **always pass `-O`/`--open`**, including when you also pass `-s` steering. The CLI writes the humanized result to a self-cleaning temp file and launches it with the OS default app (Typora on this machine), printing **only the file path** to stdout. The *only* time you omit `--open` is when the output is being piped onward (e.g. `| Set-Clipboard`) or consumed by another tool.

```bash
humanize --open <<'EOF'
...draft body...
EOF
```

Then tell the user something brief like "Opened the humanized version — `<path>`." **Do not paste the humanized text back into the terminal.** The whole point is to avoid re-transcribing prose you already drafted: draft it silently, pipe it through `humanize --open`, and report that it's open. Never show the pre-humanized draft either — go straight to the humanized file.

Old temp files (>24h) are swept automatically on each `--open` run; no cleanup needed.

### Iterating on an opened result

The printed path is a normal file you can edit:

- **Wording tweak** the user requests ("drop the last line", "make it warmer"): `Read` the file, `Edit` it in place. Typora reloads automatically. This is a plain prose edit, not another humanize pass — no API call.
- **Re-humanize differently** ("do it harder", "treat as a Slack DM"): pipe the file back through, producing a fresh file and preserving the original: `cat <path> | humanize -t --open`.

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
| `-s, --steering TEXT` | none | Extra steering rules appended to the prompt. Only pass when the user gives explicit direction (e.g. "this is a Twitter post") or specific constraints. Do not add agent-decided steering - the tool runs well without it. |
| `-t, --heuristic-texture` | — | Force heavy heuristic intensity |
| `-l, --heuristic-light` | — | Force light heuristic (same as default; for explicitness) |
| `--no-heuristic` | — | Disable heuristic pass (unconditional cleanup still runs) |
| `-m, --model ID` | `claude-sonnet-4-6` | Anthropic model ID. Use `-m claude-opus-4-7` for maximum quality on high-stakes content. |
| `--temperature FLOAT` | 0.8 | LLM sampling temperature |
| `--max-tokens INT` | 8192 | Output-length ceiling in tokens. Raise for long-form inputs that hit truncation |
| `--style-file PATH` | `~/.config/humanize/style.md` | Path to a personal style file. Loaded automatically if present. |
| `--no-style` | — | Skip loading the personal style file for this run |
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
