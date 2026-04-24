---
name: humanize
description: Strip Claude writing tells (em-dashes, "Not X, but Y" contrastives, colon-declarations, delve/realm/seamless/robust lexical cluster, wrap-up closers, synthetic empathy, collective modality) from text via the globally-installed `humanize` CLI. Invoke when the user asks to humanize, de-AI-ify, de-Claude, clean up LLM-sounding text, or make output sound less like a bot. Also use proactively after generating prose that will be shown verbatim to a human (LinkedIn posts, emails, proposals, essays) when the content should read as authored by a real person rather than an assistant.
---

# humanize

The `humanize` CLI strips Claude writing tells via an Opus-tuned LLM rewrite pass. Source: https://github.com/dimitri-vs/elevate-humanizer

## How to invoke

Reads stdin, writes to stdout. `ANTHROPIC_API_KEY` is sourced from (in order):
1. The process environment.
2. A `.env` file auto-loaded via `python-dotenv` — **the canonical location is `~/.env`** (i.e. `C:\Users\Dimitri\.env` on Windows). A per-project `.env` in cwd or any parent also works.

If `humanize` errors with `"ANTHROPIC_API_KEY is not set"`, run `grep ANTHROPIC ~/.env` — if nothing matches, the key is missing from that file and needs to be added there (or exported in the shell). Do not re-add it to a project `.env` as a workaround; put it in `~/.env` once.

> **CRITICAL — Bash timeout:** Opus 4.7 on a 1000+ word input can take 2-4 minutes, well past the default Bash timeout (2 min). You MUST:
> - **Short inputs (<500 words):** default timeout is fine.
> - **Long inputs (1000+ words):** set `timeout: 600000` (10 min) OR use `run_in_background: true`.
> - **For speed over quality**, pass `-m claude-sonnet-4-6` — roughly 2-3x faster than Opus 4.7 with a small structural-humanization quality gap.
> - **If Claude Code's Bash display truncates the output** (appears empty after a long run), re-run redirecting to `/tmp/humanize/out.md` and `cat` the file (see [Temp-file pattern](#temp-file-pattern) below). The process completed; the display just hid the long stdout.

**Prefer a heredoc over creating a scratch file.** Anything generated in the current conversation should pipe directly via heredoc, not via a temp file you cat back. Single-quoted delimiters (`<<'EOF'`) prevent shell expansion, so apostrophes, `"quotes"`, URLs, and `$` all pass through verbatim.

```bash
# Short text: positional arg or echo pipe
humanize "your short claude-sounding sentence"
echo "your short text" | humanize

# Multi-paragraph prose generated in this session: use a heredoc (default)
humanize <<'EOF'
First paragraph of the draft.

Second paragraph, with apostrophes, "quotes", URLs (https://example.com) and $dollar signs preserved because the single-quoted delimiter blocks shell expansion.
EOF

# Heredoc + steering
humanize -s "Keep URLs verbatim. Address recipient as 'you'." <<'EOF'
...draft body...
EOF

# Content already on disk
cat draft.md | humanize

# Force heavy heuristic texture (misspellings, dropped apostrophes, etc.), overriding the LLM's auto recommendation
cat proposal.txt | humanize --heuristic-texture

# Force the heuristic pass off, overriding the LLM's auto recommendation
cat polished_draft.md | humanize --no-heuristic

# Long input, faster model
cat long_reddit_post.md | humanize -m claude-sonnet-4-6 > long_reddit_post.humanized.md

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
- Text under ~100 characters — the default `--min-length-ratio 0.4` safety fallback will often trigger and return the original unchanged. Pass `--min-length-ratio 0.1` for short inputs if you really want a rewrite attempt.

## Heuristic texture (auto by default)

The heuristic pass adds surface-level "rushed typing" signals: occasional misspellings, dropped apostrophes in safe contractions (don't → dont), hyphen removal in compound words (real-time → real time), exclamation marks demoted to periods.

**Default behavior is `auto`:** the LLM classifies the content register while rewriting and emits a hidden control sentinel (`<!-- heuristic: none|light|heavy -->`) that selects the pass intensity. The sentinel is stripped before output.

- `none` (polished — LinkedIn posts, essays, client-facing emails, proposals): no texture pass.
- `light` (semi-casual — Upwork proposals, informal emails, thought-out Slack posts): conservative probabilities, at most one transform per category.
- `heavy` (casual chat — Slack DMs, SMS, quick replies): current default-aggressive probabilities.

Override only when auto gets it wrong:
- `-t, --heuristic-texture`: force heavy regardless of the LLM's recommendation.
- `--no-heuristic`: force off regardless of the LLM's recommendation.

The two override flags are mutually exclusive.

## Flags summary

| Flag | Default | Purpose |
|---|---|---|
| `-s, --steering TEXT` | none | Extra steering rules appended to the prompt |
| `-t, --heuristic-texture` | — | Force the heuristic pass at heavy intensity, overriding auto |
| `--no-heuristic` | — | Disable the heuristic pass entirely, overriding auto |
| `-m, --model ID` | `claude-opus-4-7` | Anthropic model ID (prompt is Opus-tuned, so quality may drop on other models) |
| `--temperature FLOAT` | 0.8 | LLM sampling temperature (silently skipped for models that have deprecated it, e.g. Opus 4.7) |
| `--min-length-ratio FLOAT` | 0.4 | Safety fallback — if output is shorter than this ratio of input, return the original |
| `--max-tokens INT` | 8192 | Output-length ceiling in tokens. Raise for long-form inputs (essays, multi-paragraph posts) that hit truncation |

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

1. **Default: pipe directly via heredoc.** A single-quoted heredoc (`humanize <<'EOF' ... EOF`) handles multi-paragraph prose, apostrophes, quotes, URLs, and `$` signs without scratch files or escaping gymnastics.
2. **If the content is already on disk**, `cat path.md | humanize`.
3. **Use the [temp-file pattern](#temp-file-pattern)** when (a) the content is genuinely long-form (roughly >5KB / essay-length), (b) you need to keep the input around for diffing/comparison, (c) the text contains a literal line `EOF` that would terminate the heredoc early, or (d) the Bash-tool display truncated a previous run.
4. Show the humanized result to the user. Offer to apply it back to the original destination (chat window, file, PR description).

Avoid creating scratch files in the project working directory just to `cat` them through the pipe — either use a heredoc (default) or `/tmp/humanize/` (when heredoc won't do).

## Example

Input (Claude-flavored LinkedIn-style):

> I'm incredibly excited to share that we just wrapped up a pivotal project with a leading fintech client. Our team didn't just build a chatbot — we crafted a comprehensive conversational AI solution that seamlessly integrates with their existing infrastructure.

After `humanize`:

> Just wrapped up a big project with a leading fintech client. We built a conversational AI that plugs into their existing infrastructure.

Em-dash gone, contrastive reframes collapsed, `pivotal`/`comprehensive`/`seamlessly` stripped, corporate preamble ("I'm incredibly excited to share") dropped.
