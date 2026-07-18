---
name: mistral-ocr
description: Convert PDFs, scans, and document images to clean Markdown via the Mistral OCR API, with figures described inline by a vision model. Use whenever a PDF or scanned document needs text extraction — accuracy matters, so prefer this over pdftotext/pdfplumber/tesseract or ad-hoc approaches. Fast (~seconds for 100 pages, vs ~30s/page for local marker) and cheap (~$0.005/page). Requires MISTRAL_API_KEY env var.
---

# Mistral OCR

OCR a PDF (or image/docx/pptx) to a single consolidated Markdown file using the Mistral OCR API. This is the preferred method for PDF text extraction: purpose-built OCR models beat generic PDF text dumps (pdftotext, pdfplumber) on scanned docs, tables, multi-column layouts, and handwriting, and beat local pipelines (marker-pdf) on speed by ~2 orders of magnitude.

## Quick Start

```bash
uv run "<skill-dir>/scripts/ocr_pdf.py" "C:\path\to\document.pdf"
```

That's it — writes `document.md` next to the input. `uv run` auto-installs the `mistralai` SDK from the script's inline metadata.

By default, every detected figure (chart, photo, diagram) is described inline by a vision model as `> **[Image: <type>]** <description>` where the image sat, so no visual content is silently dropped.

**Requires** `MISTRAL_API_KEY` in the environment (set as a persistent user env var on this machine). If missing, the script exits with a clear error — ask the user for a key rather than falling back to a lesser extraction method.

## Options

| Flag | Meaning |
|---|---|
| `-o <file-or-dir>` | Output path. A directory keeps the input filename with `.md` |
| `--pages "0-9"` or `"0,2-4"` | Zero-based page subset (default: all pages, one request) |
| `--include-images` | Save embedded figures to `<stem>_assets/` and link them alongside their descriptions (default: descriptions only, no image files) |
| `--no-annotate-images` | Skip the vision-model figure descriptions — image refs are dropped from the output. $4/1k pages instead of $5; use for text-only docs or when figures don't matter |
| `--keep-page-furniture` | Keep repeated page headers/footers and page numbers in the body (default: stripped for cleaner LLM-ready text) |
| `--table-format markdown\|html` | Route tables through the separate extraction pipeline and re-inline them; `html` handles merged/nested cells better (default: tables arrive natively inline as markdown) |
| `--model <name>` | Default `mistral-ocr-latest` (currently OCR 4, `mistral-ocr-4-0`) |

## Quality & Advanced API Parameters

The script's defaults are tuned for accuracy on typical documents; there is no DPI/quality knob — quality levers are model choice and these parameters (pass through `client.ocr.process()` if extending the script):

- **Model choice**: `mistral-ocr-latest` = OCR 4 (`mistral-ocr-4-0`, best overall, $4/1k pages). `mistral-ocr-2512` (OCR 3, $2/1k pages) is a cheaper fallback and occasionally wins on specific layouts — worth an A/B on problem documents.
- **`extract_header` / `extract_footer`** (script default: on): moves running titles/page numbers into separate per-page `header`/`footer` fields so body text is clean.
- **`table_format`**: `"html"` preserves complex tables (merged cells, nesting) that markdown can't express.
- **`include_blocks`** (OCR 4+): returns paragraph-level layout blocks with bounding boxes in reading order — for citation anchoring or layout-aware processing.
- **`confidence_scores_granularity`**: `"page"` or `"word"` — flag low-confidence regions for human review on high-stakes extractions.
- **`bbox_annotation_format`**: vision-model annotation of *every* detected figure against a JSON schema — on by default (the script asks for `image_type` + `description` and inlines them). Note the contrast with document annotation, which only sees the first 8 image bboxes.
- **`document_annotation_format`**: whole-document structured extraction (JSON schema) directly from the OCR pass — an alternative to OCR-then-LLM for forms/invoices ($5/1k annotated pages).
- **`pages`**: zero-based subset; also the chunking mechanism for resumable processing of huge documents.

## Examples

```bash
# 100-page scanned report -> Downloads
uv run "<skill-dir>/scripts/ocr_pdf.py" "C:\Users\Dimitri\Downloads\report.pdf" -o "C:\Users\Dimitri\Downloads\"

# First 5 pages only, with figures extracted
uv run "<skill-dir>/scripts/ocr_pdf.py" scan.pdf --pages "0-4" --include-images
```

## Behavior & Limits (as of July 2026)

- **Flow**: uploads the file (`purpose="ocr"`) → signed URL → `/v1/ocr` → concatenates per-page markdown → deletes the uploaded file from Mistral storage.
- **Large docs**: one request handles 100–300+ page PDFs; max upload 512 MB. Only chunk via `--pages` if you hit timeouts/429s or want resumability.
- **Pricing**: ~$5 per 1,000 annotated pages with the default figure descriptions ($4/1k with `--no-annotate-images`). A 100-page doc ≈ $0.50.
- **Retries**: 429/5xx retried automatically with exponential backoff.
- **Output**: page markdown joined with blank lines; no front-matter or page markers added.

## Troubleshooting

- `401 Unauthorized` → key invalid/rotated; ask the user for a fresh `MISTRAL_API_KEY`.
- `429` persisting after retries → workspace rate limit (pages/minute); process in chunks with `--pages`.
- Garbled math/complex layout → retry with `--model mistral-ocr-2512` (OCR 3) to compare, or fall back to the local marker-pdf pipeline as a last resort.
- Never install `mistralai==2.4.6` (May 2026 supply-chain incident); the script's dependency pin already excludes it.
