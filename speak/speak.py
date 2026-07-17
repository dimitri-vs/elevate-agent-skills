# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "openai>=1.60",
#     "python-dotenv>=1.0",
# ]
# ///
"""Read text aloud via OpenAI TTS.

Reads text from stdin (or a positional arg), generates an mp3 with
gpt-4o-mini-tts, saves it to a self-cleaning temp directory, and opens it
in a media player (VLC if installed, else the OS default) so playback can
be paused/scrubbed/replayed. Optionally publishes the mp3 as an ntfy
attachment for playback on a phone (--ntfy).

Only the output file path is printed to stdout; all status goes to stderr.
"""

import argparse
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = os.environ.get("SPEAK_MODEL", "gpt-4o-mini-tts-2025-12-15")
DEFAULT_VOICE = os.environ.get("SPEAK_VOICE", "cedar")
DEFAULT_INSTRUCTIONS = (
    "You are reading a coding assistant's status update aloud to the person "
    "it was written for. Natural, warm, conversational tone, like a colleague "
    "walking someone through their work. Moderate pace with brief pauses "
    "between topics. Read identifiers and file names clearly but don't "
    "over-enunciate punctuation or sound like a formal narrator."
)

# /v1/audio/speech caps input at 4096 chars; leave margin.
CHUNK_LIMIT = 3900

VLC_CANDIDATES = [
    Path(r"C:\Program Files\VideoLAN\VLC\vlc.exe"),
    Path(r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"),
]

TEMP_DIR = Path(os.environ.get("TEMP", "/tmp")) / "speak"
SWEEP_AGE_SECONDS = 24 * 3600


def load_env() -> None:
    """Load .env files without overriding real environment variables.

    Search order: process env (implicit) -> cwd .env -> ~/.env -> script dir .env.
    """
    load_dotenv(Path.cwd() / ".env")
    load_dotenv(Path.home() / ".env")
    load_dotenv(Path(__file__).resolve().parent / ".env")


def chunk_text(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """Split text into <=limit chunks at paragraph, then sentence, boundaries."""
    if len(text) <= limit:
        return [text]

    pieces: list[str] = []
    for para in text.split("\n\n"):
        if len(para) <= limit:
            pieces.append(para)
            continue
        for sent in re.split(r"(?<=[.!?])\s+", para):
            # Hard-split pathological single sentences.
            while len(sent) > limit:
                pieces.append(sent[:limit])
                sent = sent[limit:]
            pieces.append(sent)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > limit:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def synthesize(text: str, model: str, voice: str, instructions: str, speed: float) -> bytes:
    from openai import OpenAI

    client = OpenAI()
    chunks = chunk_text(text)
    if len(chunks) > 1:
        print(f"Input exceeds single-request limit; generating {len(chunks)} segments...", file=sys.stderr)

    audio = b""
    for i, chunk in enumerate(chunks, 1):
        if len(chunks) > 1:
            print(f"  segment {i}/{len(chunks)}", file=sys.stderr)
        resp = client.audio.speech.create(
            model=model,
            voice=voice,
            input=chunk,
            instructions=instructions,
            response_format="mp3",
            speed=speed,
        )
        audio += resp.content
    return audio


def sweep_old_files(directory: Path) -> None:
    now = time.time()
    for f in directory.glob("*.mp3"):
        try:
            if now - f.stat().st_mtime > SWEEP_AGE_SECONDS:
                f.unlink()
        except OSError:
            pass


def open_in_player(path: Path) -> None:
    for vlc in VLC_CANDIDATES:
        if vlc.exists():
            subprocess.Popen(
                [str(vlc), str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
    os.startfile(str(path))  # noqa: S606 - intentional default-app launch


def publish_ntfy(audio: bytes, filename: str, topic: str, title: str) -> None:
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=audio,
        method="PUT",
        headers={
            "Filename": filename,
            "Title": title,
            "Tags": "speaker",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read text aloud via OpenAI TTS.")
    parser.add_argument("text", nargs="?", help="Text to speak (reads stdin if omitted)")
    parser.add_argument("-v", "--voice", default=DEFAULT_VOICE,
                        help=f"TTS voice (default: {DEFAULT_VOICE})")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL,
                        help=f"TTS model (default: {DEFAULT_MODEL})")
    parser.add_argument("-i", "--instructions", default=DEFAULT_INSTRUCTIONS,
                        help="Tone/pacing instructions for the voice")
    parser.add_argument("--speed", type=float, default=1.0, help="Playback speed 0.25-4.0")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output mp3 path (default: self-cleaning temp file)")
    parser.add_argument("--no-open", action="store_true",
                        help="Don't launch a media player")
    parser.add_argument("--ntfy", action="store_true",
                        help="Publish the mp3 as an ntfy attachment (topic from NTFY_TOPIC)")
    parser.add_argument("--ntfy-topic", default=None,
                        help="Override the NTFY_TOPIC env var")
    parser.add_argument("--ntfy-title", default="Claude read-aloud",
                        help="Notification title for --ntfy")
    args = parser.parse_args()

    load_env()

    if not os.environ.get("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY is not set (checked env, ./.env, ~/.env, skill .env).",
              file=sys.stderr)
        return 1

    text = args.text if args.text else sys.stdin.read()
    text = text.strip()
    if not text:
        print("Error: no input text.", file=sys.stderr)
        return 1

    words = len(text.split())
    est_minutes = words / 150
    print(f"Generating speech: {words} words (~{est_minutes:.1f} min, ~{est_minutes * 1.5:.1f} cents)...",
          file=sys.stderr)

    audio = synthesize(text, args.model, args.voice, args.instructions, args.speed)

    if args.out:
        out_path = args.out
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        sweep_old_files(TEMP_DIR)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_path = TEMP_DIR / f"speak_{stamp}_{args.voice}.mp3"

    out_path.write_bytes(audio)

    if args.ntfy:
        topic = args.ntfy_topic or os.environ.get("NTFY_TOPIC")
        if not topic:
            print("Error: --ntfy requires NTFY_TOPIC in env/.env or --ntfy-topic.", file=sys.stderr)
            return 1
        print("Publishing to ntfy...", file=sys.stderr)
        publish_ntfy(audio, out_path.name, topic, args.ntfy_title)

    if not args.no_open:
        open_in_player(out_path)

    print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
