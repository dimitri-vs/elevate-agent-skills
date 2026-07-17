---
name: speak
description: Read the assistant's last message (or any text) aloud via OpenAI TTS, opening the audio in a media player with pause/scrub controls. Invoke when the user asks to speak, read aloud, say it, narrate, or "read that to me" — typically after a dense or information-heavy response. Rewrites the message into a listening-friendly script before synthesis. Supports phone delivery via ntfy for remote/cloud sessions.
---

# speak

Reads text aloud using OpenAI TTS (`gpt-4o-mini-tts`). The common case: the user just received a dense assistant message and wants it read out loud so it's easier to follow. Cost is trivial (~1.5¢ per minute of audio; a typical message is 2-3¢).

The script lives in this skill directory as `speak.py` and runs via `uv run` (dependencies are declared inline). It saves an mp3 to a self-cleaning temp folder (`%TEMP%\speak\`, files older than 24h swept on each run), opens it in VLC (falling back to the OS default player), and prints only the file path to stdout.

## Workflow: ALWAYS rewrite for listening first

**Do not pipe your message verbatim.** Markdown read aloud is painful: headers, bullets, code blocks, tables, and long file paths don't work as speech. Before calling the CLI, rewrite the message as a spoken script:

- Convert structure to flowing prose. Bullets become sentences; headers become spoken transitions ("First, about the pricing...").
- Summarize code blocks in a phrase ("a short PowerShell command that restarts the service") — never read code line by line.
- Shorten file paths to the meaningful part ("speak.py in the skills repo", not the full `C:\Users\...` path).
- Narrate tables: state what the table shows and read only the rows that matter.
- Round numbers where precision doesn't matter aloud; say URLs as domain names.
- Keep the information content — this is a re-rendering for the ear, not a summary. Target roughly the same ground the message covered. (~150 words ≈ 1 minute of audio.)
- Write it in first person, since it's your own message being read back.

Then pipe the script via a single-quoted heredoc:

```bash
cd "<skill-directory>" && uv run speak.py <<'EOF'
...spoken script...
EOF
```

Tell the user something brief like "Playing it now — `<path>`." Don't paste the spoken script into the terminal; the audio is the deliverable.

## Delivery modes

**Local (default):** opens the mp3 in VLC so the user can pause, scrub, and re-listen. Nothing extra needed.

**Phone / remote session (`--ntfy`):** publishes the mp3 as an ntfy attachment; the user's phone gets a notification and the attachment plays in the mobile player. Use this when the session is remote-controlled or running in the cloud (no local speakers), or when the user asks to "send it to my phone".

```bash
# Phone only (remote/cloud session — no local player)
uv run speak.py --ntfy --no-open <<'EOF' ... EOF

# Both: play locally AND send to phone
uv run speak.py --ntfy <<'EOF' ... EOF
```

The topic comes from `NTFY_TOPIC` in the environment or `~/.env`. If unset, pass `--ntfy-topic <topic>`.

## Flags

| Flag | Default | Purpose |
|---|---|---|
| `-v, --voice` | `cedar` | TTS voice. `cedar` and `marin` are the highest-quality voices; others: alloy, ash, ballad, coral, echo, fable, nova, onyx, sage, shimmer, verse. |
| `-i, --instructions` | calm colleague tone | Tone/pacing steering. Only override for a specific reason (e.g. "read slowly", "sound upbeat"). |
| `--speed` | 1.0 | 0.25-4.0. Prefer instructions for pacing; use this only when the user explicitly wants faster/slower playback. |
| `-m, --model` | `gpt-4o-mini-tts-2025-12-15` | Pinned TTS model. Env override: `SPEAK_MODEL`. |
| `--out PATH` | temp file | Save to a specific path instead of the self-cleaning temp dir. |
| `--no-open` | — | Skip launching the player (use with `--ntfy` in remote sessions). |
| `--ntfy` | — | Also publish the mp3 to ntfy for phone playback. |
| `--ntfy-topic` | `NTFY_TOPIC` env | Override the ntfy topic. |
| `--ntfy-title` | "Claude read-aloud" | Notification title. |

Env overrides: `SPEAK_MODEL`, `SPEAK_VOICE` (set in shell or `~/.env` to change defaults without editing the skill).

## Credentials

`OPENAI_API_KEY` is looked up in: process env → `./.env` → `~/.env` → this skill's `.env`. The canonical location is `~/.env`.

## Notes

- Input over ~3,900 characters is automatically split at paragraph/sentence boundaries into multiple TTS requests and concatenated — no action needed, but expect a few seconds per extra segment.
- Generation time is roughly a few seconds per paragraph; for very long scripts use `run_in_background: true` or a raised Bash timeout.
- The user can ask to "re-read" or "read it slower" — re-run with the same script and adjusted `-i`/`--speed`; the previous mp3 stays in the temp dir until swept.
- Don't use this for text that is mostly code, tables, or reference material — offer a spoken summary of it instead (that's still a rewrite-for-listening, just more aggressive).
