# /// script
# requires-python = ">=3.10"
# dependencies = ["mistralai>=1.5,!=2.4.6"]
# ///
"""OCR a PDF (or image) to a single consolidated Markdown file via the Mistral OCR API.

Usage:
    uv run ocr_pdf.py input.pdf                       # writes input.md next to the PDF
    uv run ocr_pdf.py input.pdf -o "C:\\out\\doc.md"  # explicit output file
    uv run ocr_pdf.py input.pdf -o "C:\\out\\"        # output dir (keeps input stem)
    uv run ocr_pdf.py input.pdf --pages "0-9"         # zero-based page ranges
    uv run ocr_pdf.py input.pdf --include-images      # save figures to <stem>_assets/
    uv run ocr_pdf.py input.pdf --no-annotate-images  # skip figure descriptions ($4 vs $5 per 1k pages)

By default every detected figure is described inline by a vision model
(bbox annotation) as "> **[Image: <type>]** <description>".

Requires MISTRAL_API_KEY in the environment.

Flow: upload file (purpose="ocr") -> signed URL -> client.ocr.process() ->
concatenate per-page markdown -> delete the uploaded file. Handles 100+ page
PDFs in a single request (max upload size 512 MB). Retries 429/5xx with
exponential backoff.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

try:
    from mistralai.client import Mistral  # SDK v2
except ImportError:
    from mistralai import Mistral  # SDK v1

DEFAULT_MODEL = "mistral-ocr-latest"
BBOX_ANNOTATION_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "image_annotation",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "image_type": {
                    "type": "string",
                    "description": "One of: chart, graph, diagram, photo, screenshot, logo, signature, map, illustration, other",
                },
                "description": {
                    "type": "string",
                    "description": "A few sentences to a short paragraph faithfully describing the image: "
                    "visible text, data points, axis labels, trends, and key content",
                },
            },
            "required": ["image_type", "description"],
            "additionalProperties": False,
        },
    },
}
TRANSIENT_STATUS = {429, 500, 502, 503, 504}
SUPPORTED_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".avif", ".tiff", ".bmp", ".docx", ".pptx"}


def retry_call(fn: Callable[[], Any], label: str, max_retries: int = 5, extra_transient: set[int] | None = None) -> Any:
    transient = TRANSIENT_STATUS | (extra_transient or set())
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            status = getattr(e, "status_code", None)
            if status is None:
                m = re.search(r"Status (\d{3})", str(e))
                status = int(m.group(1)) if m else None
            if status not in transient or attempt == max_retries - 1:
                raise
            delay = min(60.0, 2.0**attempt) + random.uniform(0, 1)
            print(f"[retry] {label}: HTTP {status}, attempt {attempt + 1}/{max_retries}, sleeping {delay:.0f}s", file=sys.stderr)
            time.sleep(delay)


def get_attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def inline_tables(page: Any, markdown: str) -> str:
    """Replace [tbl-N.md](tbl-N.md) placeholders with the extracted table content."""
    for tbl in get_attr(page, "tables", None) or []:
        tbl_id = get_attr(tbl, "id")
        content = get_attr(tbl, "content")
        if tbl_id and content:
            markdown = markdown.replace(f"[{tbl_id}]({tbl_id})", content.strip())
    return markdown


def inline_image_annotations(page: Any, markdown: str, keep_image: bool) -> str:
    """Replace (or augment) image placeholders with the vision-model description of each figure."""
    for img in get_attr(page, "images", None) or []:
        img_id = get_attr(img, "id") or get_attr(img, "image_id")
        ann = get_attr(img, "image_annotation")
        if not img_id or not ann:
            continue
        if isinstance(ann, str):
            try:
                ann = json.loads(ann)
            except ValueError:
                ann = {"description": ann}
        desc = (get_attr(ann, "description") or "").strip()
        itype = get_attr(ann, "image_type") or "figure"
        if not desc:
            continue
        note = f"> **[Image: {itype}]** {desc}"
        pattern = re.compile(r"!\[[^\]]*\]\(" + re.escape(str(img_id)) + r"\)")
        if keep_image:
            markdown = pattern.sub(lambda m: f"{m.group(0)}\n\n{note}", markdown)
        else:
            markdown = pattern.sub(note, markdown)
    return markdown


def save_page_images(page: Any, assets_dir: Path, markdown: str) -> str:
    """Save base64 images from a page and rewrite markdown refs to the assets dir."""
    for img in get_attr(page, "images", None) or []:
        img_id = get_attr(img, "id") or get_attr(img, "image_id")
        data_url = get_attr(img, "image_base64") or get_attr(img, "base64")
        if not img_id or not data_url:
            continue
        m = re.match(r"^data:[^;]+;base64,(.*)$", data_url, flags=re.DOTALL)
        raw = m.group(1) if m else data_url
        assets_dir.mkdir(parents=True, exist_ok=True)
        (assets_dir / str(img_id)).write_bytes(base64.b64decode(raw))
        markdown = markdown.replace(f"]({img_id})", f"]({assets_dir.name}/{img_id})")
    return markdown


def main() -> int:
    p = argparse.ArgumentParser(description="OCR a PDF/image to Markdown via Mistral OCR API.")
    p.add_argument("input", type=Path, help="Path to PDF, image, docx, or pptx")
    p.add_argument("-o", "--output", type=Path, default=None, help="Output .md file or directory (default: next to input)")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"OCR model (default: {DEFAULT_MODEL})")
    p.add_argument("--pages", default=None, help='Zero-based pages/ranges, e.g. "0-9" or "0,2-4" (default: all)')
    p.add_argument("--include-images", action="store_true", help="Extract figures to <stem>_assets/ (default: text only)")
    p.add_argument(
        "--annotate-images",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Describe each figure with a vision model and inline the description in the markdown "
        "(replaces the image ref, or accompanies it with --include-images). Annotated-page pricing (~$5/1k pages). "
        "Use --no-annotate-images for text-only pages at $4/1k",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="HTTP read timeout in seconds for the OCR request (default: 900; large scanned docs with "
        "figure annotation can take several minutes)",
    )
    p.add_argument(
        "--keep-page-furniture",
        action="store_true",
        help="Keep repeated page headers/footers (page numbers, running titles) in the body text (default: stripped)",
    )
    p.add_argument(
        "--table-format",
        choices=["markdown", "html"],
        default=None,
        help="Extract tables via the separate tables pipeline and re-inline them in this format "
        "(default: tables arrive natively inline in the page markdown)",
    )
    args = p.parse_args()

    src: Path = args.input
    if not src.is_file():
        print(f"Error: input not found: {src}", file=sys.stderr)
        return 2
    if src.suffix.lower() not in SUPPORTED_EXTS:
        print(f"Warning: unusual extension {src.suffix}; attempting anyway", file=sys.stderr)

    out: Path = args.output or src.with_suffix(".md")
    if args.output and (args.output.is_dir() or str(args.output).endswith(("\\", "/"))):
        out = args.output / f"{src.stem}.md"
    out.parent.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        print("Error: MISTRAL_API_KEY environment variable not set.", file=sys.stderr)
        return 2

    t0 = time.time()
    with Mistral(api_key=api_key, timeout_ms=args.timeout * 1000) as client:
        print(f"Uploading {src.name} ({src.stat().st_size / 1e6:.1f} MB)...", file=sys.stderr)
        with src.open("rb") as f:
            uploaded = retry_call(
                lambda: client.files.upload(file={"file_name": src.name, "content": f}, purpose="ocr"),
                "upload",
            )
        try:
            # 404 can occur transiently right after upload before the file is indexed
            signed = retry_call(
                lambda: client.files.get_signed_url(file_id=uploaded.id), "signed-url", extra_transient={404}
            )
            kwargs: dict[str, Any] = {
                "model": args.model,
                "document": {"type": "document_url", "document_url": signed.url},
                "include_image_base64": args.include_images,
                "extract_header": not args.keep_page_furniture,
                "extract_footer": not args.keep_page_furniture,
            }
            if args.table_format:
                kwargs["table_format"] = args.table_format
            if args.annotate_images:
                kwargs["bbox_annotation_format"] = BBOX_ANNOTATION_FORMAT
            if args.pages:
                kwargs["pages"] = args.pages
            print("Running OCR...", file=sys.stderr)
            resp = retry_call(lambda: client.ocr.process(**kwargs), "ocr")
        finally:
            try:
                client.files.delete(file_id=uploaded.id)
            except Exception as e:
                print(f"[warn] could not delete uploaded file {uploaded.id}: {e}", file=sys.stderr)

    pages = get_attr(resp, "pages", None) or []
    assets_dir = out.parent / f"{out.stem}_assets"
    parts: list[str] = []
    for page in pages:
        md = (get_attr(page, "markdown", "") or "").strip()
        md = inline_tables(page, md)
        if args.annotate_images:
            md = inline_image_annotations(page, md, keep_image=args.include_images)
        if args.include_images:
            md = save_page_images(page, assets_dir, md)
        parts.append(md)
    out.write_text("\n\n".join(parts).strip() + "\n", encoding="utf-8")

    elapsed = time.time() - t0
    model_used = get_attr(resp, "model", args.model)
    print(f"OK: {len(pages)} pages -> {out} ({model_used}, {elapsed:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
