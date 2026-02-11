#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "openai>=1.0.0",
#     "python-dotenv>=1.0.0",
# ]
# ///
"""
Web Research CLI using OpenAI APIs.

Supports three depth levels:
- fast: gpt-5-search-api via Chat Completions (10-60 sec) - quick lookups, current facts
- normal: o3-deep-research with code interpreter (2-6 min) - thorough analysis
- deep: o3-deep-research with code interpreter (6-14 min) - comprehensive research

Alternative: o4-mini-deep-research is faster/cheaper but lower quality than o3.

Usage:
    uv run research.py "short question here"
    uv run research.py -d normal "question with some context"
    uv run research.py -d deep "detailed query with ~2 paragraphs of context"

Note: Set OPENAI_API_KEY in .env or environment variables.

Tip: For deep research, provide more context (~2 paragraphs) for better results.
     For fast lookups, a simple question is sufficient.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


# Directory for saving research results (and central log)
RESEARCH_DIR = Path.home() / "research"

# Log file for tracking usage over time (centralized alongside saved research)
LOG_FILE = RESEARCH_DIR / "research_log.jsonl"


def log_research(depth: str, query: str, metrics: dict, success: bool = True):
    """Append research metrics to log file (one JSON line per call)."""
    try:
        RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "depth": depth,
            "query_preview": query[:100],
            "success": success,
            **{k: v for k, v in metrics.items() if v is not None},
        }
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # Don't let logging errors affect research


# Depth configurations
# - fast: gpt-5-search-api via Chat Completions (always searches web)
# - normal/deep: o3-deep-research model (true deep research)
#   Alternative: o4-mini-deep-research is faster/cheaper but lower quality
#
# Note: max_tool_calls is the primary way to control cost and latency for deep
# research (not max_output_tokens).
#
# Profiling results (2026-02-02, no limiters):
# ┌─────────────────────────────────────────────────────────────────────────────┐
# │ gpt-5-search-api (fast)                                                     │
# ├────────────┬──────────┬─────────────┬──────────────┬─────────┬─────────────┤
# │ Complexity │ Duration │ Input Tok   │ Output Tok   │ Content │ Citations   │
# ├────────────┼──────────┼─────────────┼──────────────┼─────────┼─────────────┤
# │ Simple     │ 10s      │ 15,680      │ 54           │ 158ch   │ 0           │
# │ Medium     │ 13s      │ 16,039      │ 1,428        │ 6.8Kch  │ 17          │
# │ Complex    │ 19s      │ 30,872      │ 2,518        │ 10.7Kch │ 18          │
# └────────────┴──────────┴─────────────┴──────────────┴─────────┴─────────────┘
# ┌─────────────────────────────────────────────────────────────────────────────┐
# │ o3-deep-research (normal/deep)                                              │
# ├────────────┬──────────┬─────────────┬──────────────┬─────────┬─────────────┤
# │ Complexity │ Duration │ Input Tok   │ Output Tok   │ Searches│ Content     │
# ├────────────┼──────────┼─────────────┼──────────────┼─────────┼─────────────┤
# │ Simple     │ 1:54     │ 8,512       │ 6,546        │ 5       │ 301ch       │
# │ Medium     │ 3:14     │ 23,719      │ 14,926       │ 13      │ 27.6Kch     │
# │ Complex    │ 6:55     │ 41,717      │ 25,362       │ 36      │ 23.2Kch     │
# └────────────┴──────────┴─────────────┴──────────────┴─────────┴─────────────┘
DEPTH_CONFIGS = {
    "fast": {
        "model": "gpt-5-search-api",
        "api": "chat_completions",  # Uses Chat Completions API with web_search_options
        "max_tokens": 50000,
        "timeout": 120,  # 2 minutes
    },
    "normal": {
        "model": "o3-deep-research",
        "api": "responses",
        "tools": [
            {"type": "web_search_preview"},
            {"type": "code_interpreter", "container": {"type": "auto"}},
        ],
        "max_output_tokens": 50000,
        "max_tool_calls": 25,
        "timeout": 420,  # 7 minutes
        "background": True,
    },
    "deep": {
        "model": "o3-deep-research",
        "api": "responses",
        "tools": [
            {"type": "web_search_preview"},
            {"type": "code_interpreter", "container": {"type": "auto"}},
        ],
        "max_output_tokens": 100000,
        # No max_tool_calls limit - let it run fully
        "timeout": 900,  # 15 minutes
        "background": True,
    },
}


def get_api_key() -> str:
    """
    Get OpenAI API key from environment.

    Checks in order:
    1. Local .env file
    2. User home .env file
    3. System environment variables

    Returns:
        API key string

    Raises:
        SystemExit if no key found
    """
    # Load in reverse priority order with override=True so highest-priority
    # file wins (last loaded overrides earlier ones and system env vars).
    # Priority: local .env > home .env > skill dir .env > system env
    skill_env = Path(__file__).parent / ".env"
    if skill_env.exists():
        load_dotenv(skill_env, override=True)

    home_env = Path.home() / ".env"
    if home_env.exists():
        load_dotenv(home_env, override=True)

    env_path = Path.cwd() / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        print("Error: OPENAI_API_KEY not found.", file=sys.stderr)
        print("Please set it in a .env file or as a system environment variable.", file=sys.stderr)
        sys.exit(1)

    return api_key


def extract_content(response) -> str:
    """Extract text content from OpenAI Responses API response."""
    # Try output_text property first (convenience accessor)
    if hasattr(response, 'output_text') and response.output_text:
        return response.output_text

    # Try output property with various structures
    if hasattr(response, 'output') and response.output:
        output = response.output

        # If output is a string directly
        if isinstance(output, str):
            return output

        # If output is a list
        if isinstance(output, list):
            texts = []
            for item in output:
                # Try content attribute
                if hasattr(item, 'content') and item.content:
                    if isinstance(item.content, str):
                        texts.append(item.content)
                    elif isinstance(item.content, list):
                        for block in item.content:
                            if hasattr(block, 'text') and block.text:
                                texts.append(block.text)
                            elif isinstance(block, str):
                                texts.append(block)
                # Try text attribute directly
                elif hasattr(item, 'text') and item.text:
                    texts.append(item.text)
                # Try message attribute (some response formats)
                elif hasattr(item, 'message'):
                    msg = item.message
                    if hasattr(msg, 'content') and msg.content:
                        texts.append(msg.content)

            if texts:
                return "\n\n".join(texts)

    # Last resort: try to get any text-like attribute
    for attr in ['text', 'content', 'message', 'result']:
        if hasattr(response, attr):
            val = getattr(response, attr)
            if isinstance(val, str) and val:
                return val

    return ""


def extract_citations(response) -> list[dict]:
    """Extract citations/annotations from response."""
    citations = []

    # Helper to process annotations
    def process_annotations(annotations):
        if not annotations:
            return
        for ann in annotations:
            url = None
            title = ''
            if hasattr(ann, 'url'):
                url = ann.url
            elif isinstance(ann, dict):
                url = ann.get('url')
                title = ann.get('title', '')
            if url:
                citations.append({
                    "title": getattr(ann, 'title', title) if hasattr(ann, 'title') else title,
                    "url": url,
                })

    if hasattr(response, 'output') and response.output:
        output = response.output
        if isinstance(output, list):
            for item in output:
                # Check content
                if hasattr(item, 'content') and item.content:
                    content = item.content
                    if isinstance(content, list):
                        for block in content:
                            if hasattr(block, 'annotations'):
                                process_annotations(block.annotations)
                # Check annotations directly on item
                if hasattr(item, 'annotations'):
                    process_annotations(item.annotations)

    # Also check top-level annotations
    if hasattr(response, 'annotations'):
        process_annotations(response.annotations)

    return citations


def format_progress_bar(elapsed: float, timeout: int, width: int = 30) -> str:
    """Create a simple progress bar based on elapsed time vs expected timeout."""
    # Estimate progress (use 70% of timeout as expected completion)
    expected = timeout * 0.7
    progress = min(elapsed / expected, 1.0)
    filled = int(width * progress)
    bar = "█" * filled + "░" * (width - filled)

    # Format times
    elapsed_min = int(elapsed // 60)
    elapsed_sec = int(elapsed % 60)
    remaining = max(0, expected - elapsed)
    remain_min = int(remaining // 60)
    remain_sec = int(remaining % 60)

    return f"[{bar}] {elapsed_min}:{elapsed_sec:02d} elapsed | ~{remain_min}:{remain_sec:02d} remaining"


def poll_for_completion(client: OpenAI, response_id: str, timeout: int) -> dict:
    """Poll for background response completion."""
    start_time = time.time()
    poll_interval = 15  # seconds (less frequent polling)

    print(f"Deep research started (ID: {response_id[:20]}...)", file=sys.stderr)
    print(f"Expected time: ~{int(timeout * 0.7 // 60)} minutes", file=sys.stderr)

    while True:
        elapsed = time.time() - start_time
        if elapsed > timeout:
            raise TimeoutError(f"Research timed out after {timeout} seconds")

        # Retrieve the response status
        response = client.responses.retrieve(response_id)

        status = getattr(response, 'status', None)
        if status == 'completed':
            elapsed_min = int(elapsed // 60)
            elapsed_sec = int(elapsed % 60)
            print(f"✓ Completed in {elapsed_min}:{elapsed_sec:02d}", file=sys.stderr)
            return response
        elif status == 'failed':
            error = getattr(response, 'error', 'Unknown error')
            raise RuntimeError(f"Research failed: {error}")
        elif status in ('cancelled', 'expired'):
            raise RuntimeError(f"Research {status}")

        # Show progress bar
        progress_bar = format_progress_bar(elapsed, timeout)
        print(f"  {progress_bar}", file=sys.stderr)
        time.sleep(poll_interval)


def research_chat_completions(client: OpenAI, query: str, config: dict) -> dict:
    """Perform web search using Chat Completions API (gpt-5-search-api)."""
    request_params = {
        "model": config["model"],
        "web_search_options": {},  # Enable web search
        "messages": [{"role": "user", "content": query}],
    }
    if "max_tokens" in config:
        request_params["max_tokens"] = config["max_tokens"]

    response = client.chat.completions.create(**request_params)

    content = response.choices[0].message.content or ""
    citations = []

    # Extract citations from annotations
    annotations = getattr(response.choices[0].message, 'annotations', None) or []
    for ann in annotations:
        if hasattr(ann, 'url_citation'):
            cite = ann.url_citation
            citations.append({
                "title": getattr(cite, 'title', ''),
                "url": getattr(cite, 'url', ''),
            })
        elif hasattr(ann, 'url'):
            citations.append({
                "title": getattr(ann, 'title', ''),
                "url": ann.url,
            })

    # Extract metrics
    metrics = {
        "input_tokens": getattr(response.usage, 'prompt_tokens', None) if response.usage else None,
        "output_tokens": getattr(response.usage, 'completion_tokens', None) if response.usage else None,
        "total_tokens": getattr(response.usage, 'total_tokens', None) if response.usage else None,
    }

    return {"content": content, "citations": citations, "response": response, "metrics": metrics}


def count_tool_calls(response) -> dict:
    """Count tool calls from deep research response."""
    counts = {"web_search": 0, "code_interpreter": 0, "other": 0}
    if hasattr(response, 'output') and response.output:
        for item in response.output:
            item_type = getattr(item, 'type', '')
            if item_type == 'web_search_call':
                counts["web_search"] += 1
            elif item_type == 'code_interpreter_call':
                counts["code_interpreter"] += 1
            elif item_type not in ('message', 'reasoning'):
                counts["other"] += 1
    return counts


def research_responses(client: OpenAI, query: str, config: dict) -> dict:
    """Perform deep research using Responses API (o3-deep-research)."""
    request_params = {
        "model": config["model"],
        "input": query,
        "tools": config["tools"],
    }
    if "max_output_tokens" in config:
        request_params["max_output_tokens"] = config["max_output_tokens"]
    if "max_tool_calls" in config:
        request_params["max_tool_calls"] = config["max_tool_calls"]

    if config.get("background"):
        request_params["background"] = True

    response = client.responses.create(**request_params)

    # If background mode, poll for completion
    if config.get("background"):
        response = poll_for_completion(client, response.id, config["timeout"])

    content = extract_content(response)
    citations = extract_citations(response)

    # Extract metrics
    tool_calls = count_tool_calls(response)
    usage = getattr(response, 'usage', None)
    metrics = {
        "input_tokens": getattr(usage, 'input_tokens', None) if usage else None,
        "output_tokens": getattr(usage, 'output_tokens', None) if usage else None,
        "total_tokens": getattr(usage, 'total_tokens', None) if usage else None,
        "tool_calls": tool_calls,
    }

    return {"content": content, "citations": citations, "response": response, "metrics": metrics}


def research(
    query: str,
    depth: str = "fast",
    api_key: str | None = None,
) -> dict:
    """
    Perform web research using OpenAI APIs.

    Args:
        query: The research question or topic
        depth: Research depth - "fast", "normal", or "deep"
        api_key: OpenAI API key (uses env if not provided)

    Returns:
        Dict with 'content', 'citations', 'metadata', and 'metrics'
    """
    if api_key is None:
        api_key = get_api_key()

    config = DEPTH_CONFIGS.get(depth, DEPTH_CONFIGS["fast"])

    client = OpenAI(
        api_key=api_key,
        timeout=config["timeout"],
    )

    print(f"Starting {depth} research with {config['model']}...", file=sys.stderr)

    # Track timing
    start_time = time.time()

    # Route to appropriate API
    if config.get("api") == "chat_completions":
        result = research_chat_completions(client, query, config)
    else:
        result = research_responses(client, query, config)

    duration = time.time() - start_time

    metrics = {
        **result.get("metrics", {}),
        "duration_seconds": round(duration, 2),
    }

    # Log for tracking over time
    log_research(depth, query, metrics)

    return {
        "content": result["content"],
        "citations": result["citations"],
        "metadata": {
            "model": config["model"],
            "depth": depth,
            "api": config.get("api", "responses"),
            "response_id": getattr(result["response"], 'id', None),
        },
        "metrics": metrics,
    }


def format_output(result: dict) -> str:
    """Format research result as markdown."""
    output = result["content"]

    # Add sources section if citations exist
    if result["citations"]:
        output += "\n\n---\n\n## Sources\n\n"
        seen_urls = set()
        for i, cite in enumerate(result["citations"], 1):
            url = cite.get("url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            title = cite.get("title") or url or "Source"
            if url:
                output += f"{i}. [{title}]({url})\n"
            else:
                output += f"{i}. {title}\n"

    return output


def slugify(text: str, max_length: int = 60) -> str:
    """Convert text to a filename-friendly slug (fallback for title generation)."""
    first_line = text.strip().split("\n")[0]
    slug = first_line.lower()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    slug = slug.strip('-')
    if len(slug) > max_length:
        slug = slug[:max_length].rsplit('-', 1)[0]
    return slug or "research"


def generate_title(query: str, api_key: str) -> str | None:
    """Generate a short descriptive title for the research query using a fast model."""
    try:
        client = OpenAI(api_key=api_key, timeout=15)
        response = client.chat.completions.create(
            model="gpt-5-mini-2025-08-07",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Generate a short, descriptive title (max 70 characters) for this "
                        "research query. The title should capture the core topic concisely. "
                        "Output ONLY the title text. No quotes, no prefix, no punctuation at the end. "
                        "Use only plain ASCII characters — no special dashes, currency symbols, or unicode."
                    ),
                },
                {"role": "user", "content": query[:1000]},
            ],
        )
        title = response.choices[0].message.content.strip().strip('"\'')
        if title:
            return title
    except Exception:
        pass
    return None


def save_result(query: str, result: dict, formatted_output: str, title: str | None = None) -> Path:
    """Save research result as a markdown file to ~/research/."""
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    slug = slugify(title) if title else slugify(query)
    filename = f"{date_str}_{slug}.md"
    filepath = RESEARCH_DIR / filename

    # Handle duplicate filenames
    counter = 2
    while filepath.exists():
        filename = f"{date_str}_{slug}_{counter}.md"
        filepath = RESEARCH_DIR / filename
        counter += 1

    metadata = result.get("metadata", {})
    metrics = result.get("metrics", {})
    duration = metrics.get("duration_seconds", 0)
    tokens = metrics.get("total_tokens", 0)
    tool_calls = metrics.get("tool_calls", {})

    # Build frontmatter — directory first for project traceability
    lines = [
        "---",
        f"directory: {Path.cwd()}",
        f"date: {now.isoformat()}",
    ]
    if title:
        lines.append(f"title: \"{title}\"")
    lines += [
        f"depth: {metadata.get('depth', 'unknown')}",
        f"model: {metadata.get('model', 'unknown')}",
        f"duration: {duration}s",
        f"tokens: {tokens}",
    ]
    if tool_calls:
        searches = tool_calls.get("web_search", 0)
        code = tool_calls.get("code_interpreter", 0)
        if searches:
            lines.append(f"web_searches: {searches}")
        if code:
            lines.append(f"code_interpreter_calls: {code}")
    response_id = metadata.get("response_id", "")
    if response_id:
        lines.append(f"response_id: {response_id}")
    lines.append("---")

    frontmatter = "\n".join(lines)

    content = f"{frontmatter}\n\n## Query\n\n{query}\n\n---\n\n{formatted_output}\n"

    filepath.write_text(content, encoding="utf-8")
    return filepath


def main():
    parser = argparse.ArgumentParser(
        description="Web research using OpenAI APIs (web search + deep research)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Depth levels:
  fast    gpt-5-search-api via Chat Completions (10-60 sec)
          Quick lookups, current facts
          Input: A couple sentences with background context

  normal  o3-deep-research with code interpreter (2-6 min)
          Thorough analysis, multi-step research
          Input: ~1 paragraph of context + question

  deep    o3-deep-research with code interpreter (6-14 min)
          Comprehensive research, maximum output, complex topics
          Input: ~2+ paragraphs of detailed context + question

Context matters: The value over Google search is understanding YOUR situation.
Always include background, constraints, and what you're trying to achieve.

Examples:
  uv run research.py "I'm building a FastAPI app and need to understand
    current best practices for async database connections with SQLAlchemy 2.0"

  uv run research.py -d normal "I have a Carrier 40MHHQ09 mini-split and want
    to integrate it with Home Assistant. What are my options for Wi-Fi dongles
    and which approach is most reliable?"

  uv run research.py -d deep "Compare US-SK105 Midea Wi-Fi dongle vs ESPHome
    dongle for Carrier 40MHHQ09 mini-split Home Assistant integration. Include:
    compatibility for Carrier 40MHH series (rebadged Midea), Midea AC LAN HACS
    setup and reliability, ESPHome alternatives, installation process, and
    whether the dongle reads actual unit state vs just sending commands. [...]"
        """,
    )

    parser.add_argument(
        "query",
        help="The research question or topic",
    )

    parser.add_argument(
        "-d", "--depth",
        choices=["fast", "normal", "deep"],
        default="fast",
        help="Research depth (default: fast)",
    )

    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Don't save results to ~/research/",
    )

    args = parser.parse_args()

    try:
        result = research(
            query=args.query,
            depth=args.depth,
        )

        output = format_output(result)
        print(output)

        # Save result by default
        if not args.no_save:
            try:
                api_key = get_api_key()
                title = generate_title(args.query, api_key)
                saved_path = save_result(args.query, result, output, title=title)
                print(f"\nSaved to: {saved_path}", file=sys.stderr)
            except Exception as e:
                print(f"Warning: Could not save result: {e}", file=sys.stderr)

    except KeyboardInterrupt:
        print("\nResearch cancelled.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
