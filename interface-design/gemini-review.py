# /// script
# requires-python = ">=3.11"
# dependencies = ["google-genai>=1.0.0"]
# ///
"""
Gemini UI Review — send a screenshot + design context to Gemini for aesthetic critique.

Usage:
  uv run gemini-review.py screenshot.png "Brief app description"
  uv run gemini-review.py screenshot.png --design-doc path/to/interface-design.md
  uv run gemini-review.py screenshot.png --design-doc path/to/interface-design.md --model gemini-3-pro

Environment:
  GOOGLE_GENAI_API_KEY — required. Falls back to ~/.env, then .env in CWD.
"""

import argparse
import base64
import os
import sys
from datetime import datetime
from pathlib import Path

def load_env_key():
    """Hunt for GOOGLE_GENAI_API_KEY in env, ~/.env, or .env"""
    key = os.environ.get("GOOGLE_GENAI_API_KEY")
    if key:
        return key

    script_dir = Path(__file__).resolve().parent
    for env_path in [script_dir / ".env", Path.home() / ".env", Path(".env")]:
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("GOOGLE_GENAI_API_KEY=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def build_prompt(context: str, design_doc: str | None) -> str:
    prompt = f"""You are a UI/UX aesthetic critic reviewing a mobile app screenshot.

**App context:** {context}

"""
    if design_doc:
        prompt += f"""**Design system reference (interface-design.md):**
```
{design_doc}
```

Review the screenshot against this design system.

"""

    prompt += """Score this screen on a 0.0–10.0 scale for AESTHETICS ONLY:
- Visual hierarchy — does the eye flow naturally?
- Spacing & proportion — is vertical rhythm balanced?
- Typography — are sizes appropriate for the content hierarchy?
- Overall feel — does this feel premium and intentional, or generic?
- Design system consistency — do colors, fonts, spacing match the documented system?

**Rules:**
- Do NOT suggest copy or text changes. The copy is client-specified and locked.
- Focus on spacing, padding, font sizing, opacity, layout structure, visual weight.
- Be specific — reference exact areas of the screenshot.

**Return format:**
SCORE: X.X/10.0
ISSUES: (3-5 numbered bullet points, aesthetic only)
FIXES: (3-5 numbered bullet points with specific CSS/Tailwind class suggestions where possible)"""

    return prompt


def main():
    parser = argparse.ArgumentParser(description="Gemini UI screenshot review")
    parser.add_argument("screenshot", help="Path to screenshot PNG/JPG")
    parser.add_argument("context", nargs="?", default="A mobile app screen",
                        help="Brief app/screen description")
    parser.add_argument("--design-doc", "-d", help="Path to interface-design.md")
    parser.add_argument("--model", "-m", default=os.environ.get("GEMINI_REVIEW_MODEL", "gemini-3.1-pro-preview"),
                        help="Gemini model (default: gemini-3.1-pro-preview)")
    parser.add_argument("--no-save", action="store_true", help="Don't save to ~/reviews/")
    args = parser.parse_args()

    api_key = load_env_key()
    if not api_key:
        print("Error: GOOGLE_GENAI_API_KEY not found in env, ~/.env, or .env", file=sys.stderr)
        sys.exit(1)

    screenshot_path = Path(args.screenshot)
    if not screenshot_path.exists():
        print(f"Error: Screenshot not found: {screenshot_path}", file=sys.stderr)
        sys.exit(1)

    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp"}
    mime_type = mime_map.get(screenshot_path.suffix.lower(), "image/png")

    design_doc = None
    if args.design_doc:
        doc_path = Path(args.design_doc)
        if doc_path.exists():
            design_doc = doc_path.read_text(encoding="utf-8")
        else:
            print(f"Warning: Design doc not found: {doc_path}", file=sys.stderr)

    image_data = base64.b64encode(screenshot_path.read_bytes()).decode("utf-8")
    prompt = build_prompt(args.context, design_doc)

    print(f"Sending to {args.model}...", file=sys.stderr)

    from google import genai
    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=args.model,
        contents=[{
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": mime_type, "data": image_data}},
                {"text": prompt},
            ],
        }],
        config={"temperature": 0.4, "max_output_tokens": 4000},
    )

    result = response.text.strip()
    print(result)

    if not args.no_save:
        reviews_dir = Path.home() / "reviews"
        reviews_dir.mkdir(exist_ok=True)
        slug = screenshot_path.stem[:40]
        filename = f"{datetime.now():%Y-%m-%d}_gemini-review_{slug}.md"
        save_path = reviews_dir / filename
        frontmatter = f"""---
model: {args.model}
screenshot: {screenshot_path}
date: {datetime.now():%Y-%m-%d %H:%M}
context: {args.context[:100]}
---

"""
        save_path.write_text(frontmatter + result, encoding="utf-8")
        print(f"Saved to: {save_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
