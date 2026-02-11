---
name: skill-creator
description: Create new Claude Code skills with proper structure and best practices. Use when building a new skill, understanding skill architecture, or deciding between skills vs prompts vs CLAUDE.md.
---

# Skill Creation Guide

This skill helps you create well-structured Claude Code skills following official best practices.

## When to Create a Skill

| Use Case | Solution |
|----------|----------|
| One-off task instruction | Just tell Claude in the conversation |
| Project-wide context | Add to `CLAUDE.md` |
| Repeatable workflow across projects | **Create a Skill** |
| Domain expertise that loads on-demand | **Create a Skill** |
| Composable capability with scripts/templates | **Create a Skill** |

**Key insight**: Skills load on-demand via progressive disclosure. CLAUDE.md loads every conversation. Use skills for domain expertise you don't always need.

## Required Structure

Every skill needs a directory with at minimum a `SKILL.md` file:

```
skill-name/
└── SKILL.md
```

### SKILL.md Format

```yaml
---
name: skill-name
description: What this skill does and WHEN to use it. Claude uses this to decide when to invoke the skill automatically.
---

# Skill Title

## Instructions
[Step-by-step guidance for Claude to follow]

## Examples
[Concrete examples of using this skill]
```

### Field Requirements

**name** (required):
- Max 64 characters
- Lowercase letters, numbers, hyphens only
- No reserved words: "anthropic", "claude"

**description** (required):
- Max 1024 characters
- Must explain both WHAT and WHEN
- Claude uses this to decide when to invoke

## Optional Frontmatter

```yaml
---
name: skill-name
description: Description here
disable-model-invocation: true  # Only user can invoke via /skill-name
user-invocable: false           # Only Claude can invoke (background knowledge)
allowed-tools: Read, Grep, Glob # Restrict tool access
context: fork                   # Run in isolated subagent
agent: Explore                  # Which agent type when forked
---
```

### Invocation Control Matrix

| Setting | User `/skill` | Claude Auto | Use For |
|---------|---------------|-------------|---------|
| (default) | Yes | Yes | General-purpose skills |
| `disable-model-invocation: true` | Yes | No | Side effects (deploy, email) |
| `user-invocable: false` | No | Yes | Background knowledge |

## Progressive Disclosure (3 Levels)

Skills load content in stages to minimize token usage:

| Level | When Loaded | Token Cost | Content |
|-------|-------------|------------|---------|
| **1. Metadata** | Always (startup) | ~100 tokens | `name` + `description` only |
| **2. Instructions** | When triggered | <5k tokens | SKILL.md body |
| **3. Resources** | As needed | Unlimited | Scripts, templates, reference files |

**Implication**: You can bundle extensive reference materials without token penalty until actually used.

## Adding Resources

For complex skills, add additional files:

```
skill-name/
├── SKILL.md           # Main instructions (required)
├── REFERENCE.md       # Additional docs (loaded when referenced)
├── templates/
│   └── template.yaml  # Templates to apply
└── scripts/
    └── validate.sh    # Scripts Claude can execute
```

Reference these in SKILL.md:
```markdown
For API details, see [REFERENCE.md](REFERENCE.md).
Use the template in [templates/template.yaml](templates/template.yaml).
```

## Dynamic Context Injection

Use backtick commands to inject live data before the skill runs:

```yaml
## Current State
- Branch: !`git branch --show-current`
- Status: !`git status --short`
```

The command output replaces the placeholder before Claude sees the content.

## Arguments Pattern

Skills can receive arguments when invoked:

```markdown
Generate documentation for: $ARGUMENTS
```

When user runs `/skill-name src/utils.ts`, `$ARGUMENTS` becomes `src/utils.ts`.

## Best Practices

1. **Keep it focused** - One workflow per skill. Multiple focused skills compose better than one large skill.

2. **Write clear descriptions** - Claude uses the description to decide when to invoke. Be specific about triggers.

3. **Start simple** - Begin with basic instructions in Markdown before adding scripts.

4. **Use examples** - Include example inputs/outputs so Claude understands success.

5. **Test incrementally** - Test after each change rather than building complex skills all at once.

6. **Tool docstrings matter** - If your skill references scripts, their docstrings help Claude understand usage.

7. **Avoid secrets** - Never hardcode API keys or passwords.

## Example: API Documentation Skill

```
api-doc/
├── SKILL.md
└── openapi-template.yaml
```

**SKILL.md:**
```yaml
---
name: api-doc
description: Generate OpenAPI documentation for an endpoint. Use when documenting API routes or creating API specs.
---

Generate OpenAPI documentation for the endpoint at $ARGUMENTS.

Use the template in [openapi-template.yaml](openapi-template.yaml) as the structure.

1. Read the endpoint code
2. Extract path, method, parameters, request/response schemas
3. Fill in the template with actual values
4. Output the completed YAML
```

## Example: Deploy Skill (User-Only)

```yaml
---
name: deploy-staging
description: Deploy current branch to staging environment
disable-model-invocation: true  # Prevent accidental deploys
---

## Pre-deploy Checks
- Branch: !`git branch --show-current`
- Status: !`git status --short`

## Steps
1. Verify all changes are committed
2. Run `railway up --detach` from the correct directory
3. Monitor logs: `timeout 10 railway logs`
4. Report deployment status
```

## Example: Background Knowledge Skill

```yaml
---
name: project-conventions
description: Code style and patterns for this project. Apply when writing or reviewing code.
user-invocable: false  # Claude-only, automatic
---

## Naming
- Components: PascalCase
- Utilities: camelCase
- Files: kebab-case

## Patterns
- Use `Result<T, E>` for fallible operations
- All API responses: `{ data, error, meta }`

## Forbidden
- No `any` types
- No `console.log` in production
```

## References

- [Official Skills Documentation](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [Skills Cookbook](https://github.com/anthropics/claude-cookbooks/tree/main/skills)
- [Example Skills](https://github.com/anthropics/skills/tree/main/skills)
- [Agent Skills Spec](https://agentskills.io/)
