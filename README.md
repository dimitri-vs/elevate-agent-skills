# Elevate Agent Skills

A curated collection of reusable skills for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and [Codex CLI](https://github.com/openai/codex). Skills are markdown-based knowledge modules that give AI coding agents instant expertise in specific tools, frameworks, and APIs — no prompt engineering required.

## What are skills?

Skills are structured markdown files (typically `SKILL.md`) that live in your project's `.claude/skills/` or `.codex/skills/` directory. When an AI agent starts a session, it reads these files and gains working knowledge of the topic — API patterns, CLI commands, framework conventions, and more.

Think of them as **reusable documentation snippets optimized for AI agents** rather than humans.

## Available Skills

| Skill | Description |
|-------|-------------|
| **clerk** | Clerk authentication — setup, sign-in/sign-up flows, agency workflow for client handover |
| **clockify-api** | Clockify REST API integration patterns and key endpoints |
| **codex-review** | Independent code review via OpenAI Codex CLI (WSL) — second-opinion sanity checks |
| **interface-design** | Interface design guidance for utilitarian apps — dashboards, tools, admin panels, data-heavy UIs |
| **playwright** | Browser automation via Playwright CLI and MCP — navigation, interaction, auth persistence, parallel sessions |
| **pydantic-ai** | Pydantic AI agent framework — agents, tools, streaming, multi-agent delegation, FastAPI integration |
| **railway-cli** | Railway cloud platform — deploy, logs, environments, CLI commands |
| **skeleton** | Skeleton UI framework reference for Svelte components |
| **skill-creator** | Meta-skill for creating new skills with proper structure and best practices |
| **slack-canvas** | Slack CLI — read canvases, conversations, threads, and channel history |
| **tailwindcss-skill** | Tailwind CSS v4 fundamentals — installation, CSS-first config, design systems |
| **web-research** | Web research via OpenAI APIs — fast lookups (gpt-5 + web_search) and deep research (o3-deep-research) |

## Getting Started

### Browse and copy

The simplest approach — just copy a skill folder into your project:

```bash
# Clone the repo
git clone https://github.com/elevatecode/elevate-agent-skills.git

# Copy a skill into your project
cp -r elevate-agent-skills/pydantic-ai /path/to/your-project/.claude/skills/
```

### Use skill-sync.py

For managing skills across multiple projects, `skill-sync.py` tracks installations and keeps them in sync:

```bash
# List available skills
python skill-sync.py list

# Add a skill to a project
python skill-sync.py add clockify-api --to "/path/to/your-project/.claude/skills"

# Check sync status across all projects
python skill-sync.py status

# Sync all skills after editing
python skill-sync.py sync

# Sync a specific skill only
python skill-sync.py sync clockify-api

# Preview changes without writing
python skill-sync.py sync --dry-run

# Overwrite when target has local edits
python skill-sync.py sync --force

# Remove a skill registration
python skill-sync.py remove clockify-api --from "/path/to/your-project/.claude/skills"

# Find .claude/skills directories in your projects
python skill-sync.py discover "/path/to/projects"
```

#### Status indicators

| Indicator | Meaning |
|-----------|---------|
| `[OK]` | Target matches source |
| `[BEHIND]` | Source updated, target needs sync |
| `[LOCAL EDITS]` | Target modified locally (use `--force`) |
| `[CONFLICT]` | Both changed (use `--force`) |
| `[MISSING]` | Target doesn't exist yet |

## Private / Local Skills

The `local/` directory is gitignored and intended for skills that contain personal or proprietary information (API keys, internal URLs, private workflows). `skill-sync.py` scans both root and `local/` — local skills show a `[local]` tag in `list` output and sync just like public ones.

```bash
mkdir local
# Move or create private skills here
mv my-private-skill local/
```

## Creating Your Own Skills

1. Create a directory: `my-skill/`
2. Add a `SKILL.md` with YAML frontmatter:

```markdown
---
name: my-skill
description: What this skill teaches the agent
---

# My Skill

Content here — API patterns, CLI reference, framework conventions, etc.
```

3. Register it: `python skill-sync.py add my-skill --to "/path/to/project/.claude/skills"`

Use the `skill-creator` skill itself for detailed guidance on structure and best practices.

## License

[MIT](LICENSE)
