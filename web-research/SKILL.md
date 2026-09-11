---
name: web-research
description: Perform web research using OpenAI APIs. Fast mode uses gpt-5.5 with web_search and medium reasoning for quick lookups (~1-2 min). Normal/deep modes use gpt-5.6-sol with web_search + code interpreter at high/xhigh reasoning effort for comprehensive multi-step research. Also supports an external mode (`via chatgpt`) that drives the user's own ChatGPT and Gemini Deep Research subscriptions through browser automation instead of the API. Invoke when user needs current web information or thorough research on a topic.
---

# Web Research Skill

Perform web research at three depth levels using OpenAI's APIs.

## Choosing the Right Depth

> **IMPORTANT — Interpreting depth when the user asks for "deep research":**
>
> "Deep research" is the name of the feature — it does **not** automatically mean deep *depth*. When the user says "do deep research", the decision between **normal** and **deep** depth is on a razor's edge — it could genuinely go either way, and it's up to the agent to judge.
>
> The baseline when nothing else is said is **normal**. But the bar to tip into **deep** is very low — any additional language like "thorough", "deep-level", "comprehensive", "exhaustive", "leave no stone unturned", or anything at all hinting the user wants more than a standard research pass, immediately means **deep** depth.
>
> - "Do deep research on X" → **normal**
> - "Do some really thorough deep research on X" → **deep**
> - "I need a comprehensive look at X" → **deep**
> - "Research X in depth, cover every angle" → **deep**
>
> **Fast mode should only be used when** the user asks for a quick lookup, or doesn't specify any depth preference and the query is clearly a simple factual retrieval.

The key question: **Are you retrieving information or exploring a topic?**

### Fast (~1-2 min) — Retrieval

Uses gpt-5.5 via the Responses API with `web_search` tool and medium reasoning effort. Searches the web then synthesizes results with lightweight reasoning — substantially better than a raw search snippet. Use when you'd normally Google something, open a few links, and get your answer.

**Good for:**
- Current facts (prices, dates, events)
- Quick verifications ("Does X support Y?")
- Simple lookups where you know the answer exists
- Low-stakes decisions

**Query detail matters even in fast mode.** Don't write terse Google-style keyword queries — write 1-3 sentences that give the model enough context to search effectively and synthesize a useful answer. Include what you're trying to accomplish, relevant specifics (model numbers, sizes, versions), and what kind of answer you're looking for.

**Examples:**
- "What version of Python does Django 5.0 require? I'm setting up a new project and want to confirm minimum and recommended versions."
- "I'm trying to price a used 6-foot Green Giant Arborvitae for a local sale. What do established arborvitae this size typically sell for secondhand vs. retail nursery pricing? Looking for Craigslist, eBay, and garden forum comps."
- "When is the next Apple event scheduled for 2026? I'm deciding whether to wait for a new MacBook announcement or buy now."

### Normal (3-8 min) — Moderate Research

Use when you need more than a quick lookup but don't need exhaustive coverage. Good for comparisons, how-to questions, and understanding a topic at a moderate depth.

**Good for:**
- Feature comparisons (without needing every detail)
- How-to guides and best practices
- Understanding a topic you're somewhat familiar with
- Questions where you want synthesized information, not just raw facts

**Examples:**
- "What are the best practices for Python async programming in 2026?"
- "Compare Tailwind CSS vs vanilla CSS for a small project"
- "How do I set up GitHub Actions for a Python project?"

### Deep (5-15 min) — Exploratory Research

Use when you're genuinely exploring—you don't have certainty, the topic is niche, or you need the model to follow leads and check multiple sources. Also use when your research might require data analysis (reading PDFs, spreadsheets, doing calculations).

**Good for:**
- Niche or specialized topics
- Multi-faceted questions requiring synthesis
- Research that needs data analysis (trends, comparisons over time)
- Critical decisions where you want thorough source-checking
- Topics where information might be in PDFs or require calculations

**Examples:**
- "Compare US-SK105 Midea Wi-Fi dongle vs ESPHome for Carrier mini-split Home Assistant integration. Include compatibility, setup reliability, and reported issues."
- "Analyze electricity price trends with Peco Electric over the last 10 years"
- "Research the economic impact of semaglutide on global healthcare systems with specific figures and statistics"

## Structuring Your Query

Unlike ChatGPT's Deep Research (which asks clarifying questions), the API expects **fully-formed prompts**. The model won't ask for clarification—it just starts researching.

**Tips for better results:**
- State your goal explicitly ("I'm trying to decide between X and Y for Z use case")
- Include what you already know or have tried
- Specify constraints (budget, timeline, technical requirements)
- Ask for specific deliverables ("Include a comparison table", "List pros and cons")
- For deep research, mention if you need data analysis or source verification

## Configuration

| Depth | Model | Reasoning | Time | Max Tool Calls |
|-------|-------|-----------|------|----------------|
| **fast** | gpt-5.5 + web_search | medium | ~1-2 min | — |
| **normal** | gpt-5.6-sol + web_search + code interpreter | high | 3-8 min | 25 |
| **deep** | gpt-5.6-sol + web_search + code interpreter | xhigh | 5-15 min | unlimited |

**Note (July 2026 migration):** OpenAI retired the dedicated deep-research models (`o3-deep-research`, `o4-mini-deep-research`) on 2026-07-23. "Deep research" is now a *method* — a reasoning model with the `web_search` tool in background mode — not a model ID. The deprecations page names `gpt-5.6-sol` as the substitute; this skill differentiates normal vs deep via reasoning effort (`high` vs `xhigh`, both verified available) and tool-call caps. A 2026-07-28 side-by-side test on a medium query: high = 4:11 / 12 searches / 11.6k output tokens; xhigh = 5:07 / 12 searches / 16.5k output tokens — comparable searches and wall-clock to old o3, with xhigh producing ~40% more reasoning/output.

**Override defaults via env vars** (frontier moves; bumping a model means changing one line in your shell, not editing code):

| Env var | Default | Applies to |
|---------|---------|------------|
| `WEB_RESEARCH_FAST_MODEL` | `gpt-5.5` | fast tier |
| `WEB_RESEARCH_DEEP_MODEL` | `gpt-5.6-sol` | normal + deep tiers |
| `WEB_RESEARCH_TITLE_MODEL` | `gpt-5.4-nano` | title-generator helper |

Note: versioned slugs are **pinned** — they do not auto-float to newer minors. Update the default (or set the env var) when a new frontier ships.

## Costs

Approximate per-run costs with the default models (fast measured against July 2026 billing; normal/deep computed 2026-07-28 from observed token volumes × confirmed rates — gpt-5.6-sol: $5/M input, $30/M output, $10/1k web searches):

| Depth | Typical cost/run | What drives it |
|-------|-----------------|----------------|
| **fast** | ~$0.50–0.70 | Large input context (web results, ~70–80k tokens/run) + separately-billed web search tool calls (~13 calls/run at $10/1k) |
| **normal** | ~$0.80–1.20 | ~80–115k input + ~12k output tokens + 8–12 searches observed per run on gpt-5.6-sol |
| **deep** | ~$1.20–2.50 | ~106k input + ~17k output on a medium query (~$1.15); complex queries scale searches and tokens up |

The cost *shape* changed with the 2026-07-23 migration from o3-deep-research to gpt-5.6-sol: o3 was output-dominated (~24k input/run), while gpt-5.6-sol with the current `web_search` tool is **input-dominated like the fast tier** (~80–115k input tokens/run, because full search results are fed into context at the model's input rate). Reasoning tokens still bill as output (`usage.output_tokens` includes them). **Web search tool calls bill separately** from tokens on all tiers (they appear as their own line item on the invoice). Notably, the migration made the deep tiers *cheaper* than o3-era measurements (~$1.50 normal / ~$2.50–3.00 deep) despite the input bloat, because sol's output rate is far below o3-deep-research's. Reconcile against real billing after a few weeks of use (see Recalculating below).

### Recalculating

Costs drift as models and pricing change. To re-derive them:

1. **Rough pass (no API needed):** every saved report in `~/research/` has `tokens`, `depth`, and `web_searches` in its frontmatter. Grep these out and average per depth to get token volume per run.
2. **Accurate pass (requires an org admin key):** query `GET /v1/organization/usage/completions` with `group_by: ["api_key_id", "model"]` to get the real input/cached/output token split for the key this skill uses, then multiply by current rates from the [pricing page](https://platform.openai.com/docs/pricing). Reasoning tokens are included in `output_tokens`.
3. **Reconcile against billing:** query `GET /v1/organization/costs` with `group_by: ["line_item"]` — this returns actual billed dollars per model and surfaces separately-billed items like `web search tool calls` that token math alone misses. The two passes should agree within a few percent.

Using a **dedicated API key** for this skill makes step 2 trivial — per-key attribution via the usage endpoint requires no guesswork about which requests were research runs. (The costs endpoint only groups down to project level, so a dedicated project additionally gives clean invoice-level reads.)

## Usage

**Important (Windows):** Always quote paths containing backslashes or spaces.

> **CRITICAL — Bash timeout:** The default Bash timeout (2 min) is too short for research calls. You MUST:
> - **Fast:** Set `timeout: 300000` (5 min) or use `run_in_background: true`
> - **Normal / Deep:** Use `run_in_background: true`
>
> **CRITICAL — Long prompts or prompts with attached documents:** Bash heredocs break on single quotes, backticks, and other shell-significant characters common in real documents. When the prompt exceeds ~30 lines or includes pasted document content, write it to a temp file via Python first, then pass via `$(cat ...)`:
> ```bash
> python3 -c "
> import pathlib
> doc = pathlib.Path(r'C:\path\to\document.md').read_text(encoding='utf-8')
> query = 'Your research question here...\n\n---\n\n' + doc
> pathlib.Path(r'C:\Users\<you>\AppData\Local\Temp\prompt.txt').write_text(query, encoding='utf-8')
> "
> cd "<skill-directory>" && uv run research.py -d deep "$(cat 'C:\Users\<you>\AppData\Local\Temp\prompt.txt')"
> ```
> This avoids all quoting issues and lets you attach full documents to the research query.
>
> **CRITICAL — Don't invoke from a fork/sub-agent.** Forks reap their child processes on exit, so a `run_in_background: true` bash task launched from inside a fork gets killed when the fork terminates — even if the fork's summary message claims the research is "running in background." Always invoke this skill directly from the main conversation thread; `run_in_background: true` from there already provides out-of-band completion via task notification, with no benefit to wrapping the skill in a fork.

```bash
# Fast lookup (default) — 1-3 sentences with context
cd "<skill-directory>" && uv run research.py "I'm trying to price a used 6-foot Green Giant Arborvitae for a local sale. What do established arborvitae this size typically sell for secondhand vs. retail nursery pricing? Looking for Craigslist, eBay, and garden forum comps."

# Normal research — roughly a paragraph of context
# ⚠️ Use timeout: 600000 or run_in_background: true
cd "<skill-directory>" && uv run research.py -d normal "I'm building a FastAPI app and trying to decide on an async database approach. What are the current best practices for async database connections with SQLAlchemy 2.0? I'm particularly interested in connection pooling, session management, and whether to use encode/databases or native SQLAlchemy async."

# Deep research — two paragraphs of detailed context
# ⚠️ Use timeout: 600000 or run_in_background: true
cd "<skill-directory>" && uv run research.py -d deep "I have a Carrier 40MHHQ09 mini-split (which is a rebadged Midea unit) and want to integrate it with Home Assistant. I've seen mentions of the US-SK105 Midea Wi-Fi dongle and ESPHome-based solutions but I'm not sure which approach is more reliable or if they even work with Carrier-branded units. [...]

Compare these options and include: (1) compatibility confirmation for Carrier 40MHH series, (2) Midea AC LAN HACS integration setup and reliability, (3) ESPHome alternatives, (4) USB port location, and (5) whether the solution reads actual unit state vs just sending commands. [...]"
```

## Arguments

- `--depth` / `-d`: Research depth - `fast`, `normal`, or `deep` (default: `fast`)
- `--no-save`: Don't save results to `~/research/`
- `--retrieve` / `-r` `<response_id>`: Recover a previously-started background (deep/normal) response by its id and save it like a normal run. The positional `query` becomes optional here (pass it for a better filename/title).
- `query` (required unless `--retrieve` is given): The research question or topic

## Recovering a Dropped Deep Run

Deep/normal research runs in OpenAI background mode, so the response keeps generating server-side even if the local poller dies (dropped connection, killed process, machine sleep). The script prints the **full** `response_id` to stderr the moment the run starts (look for `Deep research started (response_id: resp_...)` in the Bash/task output). To recover the result later:

```bash
# Minimal — recovers and saves to ~/research/
cd "<skill-directory>" && uv run research.py --retrieve resp_xxxxxxxxxxxx

# Better filename/title — pass the original query as the positional arg
cd "<skill-directory>" && uv run research.py --retrieve resp_xxxxxxxxxxxx "the original research question"
```

If the response is still running, `--retrieve` waits for it to finish. Recovered files are tagged `depth: recovered` in the frontmatter.

## Output

Returns markdown-formatted results with:
- Main content answering the query
- Inline citations where applicable
- Source URLs listed at the end

## Saved Research

All research results are automatically saved to `~/research/` as markdown files. Each file contains YAML frontmatter (working directory, date, depth, model, metrics) followed by the original query and the full response with sources.

**Before running new research**, check if relevant past research already exists — especially for normal/deep depths which are expensive. Search by keyword or by project directory:

```bash
# Find past research containing a keyword
grep -rl "keyword" ~/research/

# Find past research done from the current project directory
grep -rl "directory: <current-working-directory>" ~/research/

# Read a past result
cat ~/research/2026-02-04_some-query.md
```

If a relevant past result exists, read it and present it to the user instead of re-running the research.

## External Mode (`via chatgpt`)

When invoked as `/web-research [depth] via chatgpt [topic]`, the skill routes the research to the user's consumer Deep Research subscriptions instead of calling the API. This saves ~$1-2/query while producing equivalent or better results, since the subscription is already paid for.

### Drive it yourself, and run both providers

**If browser automation is available (Claude in Chrome or equivalent), the agent runs the research itself. Do not hand the user a prompt block to paste, and do not ask whether they would rather run it themselves — that question wastes a turn and the answer is almost always "you do it."** Deep Research takes 10-30 minutes of unattended wall-clock; the entire point of driving it is that the user walks away.

**If the user has both ChatGPT and Gemini subscriptions, run the same prompt through both, in parallel.** The marginal cost is zero, the two run concurrently, and two independently-produced reports are worth far more than one: where they converge you can act with confidence, and where they disagree you know exactly where to dig. Ask which subscriptions exist only if it is genuinely unknown and not discoverable.

Fall back to generating a prompt for the user to paste only when browser automation is unavailable, the provider's UI is blocking automation, or the user explicitly asks to run it by hand.

### Workflow

1. **Gather context** identically to a normal invocation — search the user's knowledge base and notes, check existing research in `~/research/`, pull in whatever makes the prompt better.
2. **Write one fully-formed prompt** using the same structuring principles (goal, constraints, deliverables, existing knowledge). **Include an explicit instruction not to ask clarifying questions**, e.g. *"Do not ask me clarifying questions; proceed with the assumptions stated here and flag any that matter."* Consumer Deep Research otherwise stops and waits on a clarifying round, which strands an unattended run. Save the prompt to a file so both providers get byte-identical input and the query is reproducible.
3. **Launch both runs**, then stop babysitting. Set a single long background timer (10+ minutes) rather than polling every few seconds; a tight poll loop burns turns and tokens for nothing.
4. **Export both reports**, file them to `~/research/`, and only then read them.
5. **Synthesize.** Read both together, reconcile them against each other, and write a short synthesis naming where they agreed, where they disagreed, and which way you called it. The synthesis is the deliverable; the raw reports are the evidence.

The depth argument still shapes the prompt — a `deep via chatgpt` prompt should be more detailed and multi-faceted than a `normal via chatgpt` one.

### Browser mechanics that will bite you

These are provider UI details, verified against real runs. They change; re-verify with a screenshot if something does not match.

**Enable the Deep Research mode before pasting.** On both providers it lives behind the `+` / plus button in the composer. Select it first, then confirm the mode chip actually rendered in the composer (a `find` for "Deep research" is enough) before typing anything. Pasting first and switching mode after can drop the text.

**Never use the `type` action for a multi-line prompt.** Enter submits, so a multi-paragraph prompt fires off after the first line. Inject the text with JavaScript instead:

- **ChatGPT** — target `#prompt-textarea` and dispatch a synthetic `paste` `ClipboardEvent` carrying a `DataTransfer` with the text. This works reliably.
- **Gemini** — the composer is a Quill editor at `.ql-editor`, and it **ignores synthetic paste events entirely**. Focus it and use `document.execCommand('insertText', false, text)` instead. Verify by reading back `el.innerText.length` and comparing to the prompt length; a returned length of 1 means the injection silently failed.

**Gemini generates a research plan first** and waits on an explicit "Start research" click before doing any work. It is easy to walk away from a run that never actually started, so confirm the run is in progress before setting your timer.

**Act on one tab at a time.** Batching actions across two different domains in a single browser batch fails partway with a permission error on the second domain. Take the screenshot or page-text read for one provider, then the other, in separate calls.

**Use the browser profile that holds the subscription.** Paid tiers are per-account, and the wrong profile silently gets a free-tier or degraded run. An "Upgrade" button visible in the UI is the tell that you are on the wrong profile. If the user keeps separate work and personal profiles, confirm which one carries which subscription rather than guessing.

**Gemini sometimes hard-errors** with a generic "I encountered an error doing what you asked" and produces nothing. Retry once in a fresh chat before concluding anything is wrong with the prompt.

### Exporting the reports

**ChatGPT** renders a finished Deep Research report as a document card with a download icon in its top-right corner. That menu offers **Export to Markdown**, which is what you want. Caveat: if the browser is configured to ask where to save each download, a native Save As dialog appears that browser automation **cannot** dismiss — the user has to click Save. Tell them that is coming, then watch the downloads directory for the new `.md` rather than assuming it landed.

**Gemini** exports through **Share & Export → Export to Docs**, which creates a Google Doc. Do not then click through the Docs UI to download it. Pull it programmatically with the Drive API instead:

```python
drive.files().export(fileId=doc_id, mimeType="text/markdown").execute()
```

Find the newly-created doc by listing Docs ordered by `createdTime desc`. This is dramatically more reliable than driving the Docs export menu, and it sidesteps the Save As dialog entirely.

### Pair with a grounded pass for volatile facts

Deep Research reports are synthesis, and they carry stale or confidently-wrong specifics for anything fast-moving — prices, stock status, current versions. When the research drives a purchase or a decision with live numbers in it, run a grounded scrape-and-quote pass **in parallel with** the Deep Research runs and reconcile the three. In practice the grounded pass is what catches "this product is discontinued everywhere" and "that price is six months old."

### Filing and frontmatter

Save each report to `~/research/` as `YYYY-MM-DD_kebab-slug-<provider>.md`, plus the prompt and the synthesis alongside it:

```
2026-01-15_topic-slug-chatgpt.md
2026-01-15_topic-slug-gemini.md
2026-01-15_topic-slug-PROMPT.md
2026-01-15_topic-slug-SYNTHESIS.md
```

Prepend lightweight YAML frontmatter for provenance and searchability:

```yaml
---
date: YYYY-MM-DD
source: chatgpt-deep-research   # or gemini-deep-research
query: "original prompt or topic summary"
directory: working directory at time of request
---
```

Skip fields that don't apply to external research (token counts, model version, processing time, max tool calls). The `source` field enables `grep -rl "source: chatgpt" ~/research/` to separate external from API-generated reports.

### Manual fallback

When you cannot drive the browser: generate the prompt, present it in chat for the user to copy, and create no files at that stage. When the user returns with results (usually a file path from a downloads or temp location), read the content, generate the filename from the report's topic, prepend frontmatter, move it to `~/research/`, and confirm.

### Recognition Patterns

The user may signal results are ready with any of:
- A file path (e.g. `C:\Users\<you>\Downloads\report.md`)
- "file it", "research result", "here's the report"
- Dropping a file path with minimal context after a `via chatgpt` prompt was generated earlier in the session

## Credentials

The script looks for `OPENAI_API_KEY` in:
1. Local `.env` file in current directory
2. User home `~/.env` file
3. Skill directory `.env` file
4. System environment variables
