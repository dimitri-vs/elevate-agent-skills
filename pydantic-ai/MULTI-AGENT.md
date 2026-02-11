# Multi-Agent Patterns

> **Official Docs**: https://ai.pydantic.dev/multi-agent-applications/index.md
> **Dependencies**: https://ai.pydantic.dev/dependencies/index.md

Pydantic AI supports multi-agent architectures for complex workflows. Agents are stateless and global, so you can call one agent from another's tool.

## Why Multi-Agent?

**Context Isolation**: Separate concerns to avoid "context pollution" where conflicting instructions degrade model performance.

**Specialization**: Use different models for different tasks (e.g., Gemini Pro for planning, Flash for execution).

**Cost Optimization**: Delegate simple tasks to cheaper/faster models.

## Pattern 1: Agent Delegation (Tool-Based)

The parent agent calls a child agent via a tool, then regains control.

```python
from pydantic_ai import Agent, RunContext

# Child agent: specialized in joke generation
joke_generator = Agent(
    'google-gla:gemini-2.5-flash',
    output_type=list[str],
    instructions='Generate funny, family-friendly jokes.',
)

# Parent agent: orchestrates the workflow
joke_selector = Agent(
    'google-gla:gemini-2.5-pro',
    instructions='Use the joke_factory tool to get jokes, then pick the best one.',
)

@joke_selector.tool
async def joke_factory(ctx: RunContext, count: int) -> list[str]:
    """Generate jokes and return them for selection.

    Args:
        count: Number of jokes to generate.
    """
    result = await joke_generator.run(
        f'Please generate {count} jokes.',
        usage=ctx.usage,  # Track tokens across agents
    )
    return result.output

# Run the parent agent
result = await joke_selector.run('Tell me a joke')
print(result.output)
print(result.usage())  # Includes both agents' token usage
```

### Key Points

1. **Pass `usage=ctx.usage`** - Aggregates token usage across agents
2. **Child returns to parent** - Parent continues after child completes
3. **Different models allowed** - But cost calculation becomes complex

## Pattern 2: Delegation with Shared Dependencies

When agents need access to the same resources (DB, HTTP client):

```python
from dataclasses import dataclass
import httpx

@dataclass
class SharedDeps:
    http_client: httpx.AsyncClient
    db: DatabaseConn

# Child agent with same deps_type
research_agent = Agent(
    'google-gla:gemini-2.5-flash',
    deps_type=SharedDeps,
)

@research_agent.tool
async def fetch_url(ctx: RunContext[SharedDeps], url: str) -> str:
    """Fetch content from a URL."""
    response = await ctx.deps.http_client.get(url)
    return response.text

# Parent agent
orchestrator = Agent(
    'google-gla:gemini-2.5-pro',
    deps_type=SharedDeps,
)

@orchestrator.tool
async def research_topic(ctx: RunContext[SharedDeps], topic: str) -> str:
    """Research a topic using the research agent."""
    result = await research_agent.run(
        f'Research: {topic}',
        deps=ctx.deps,      # Pass dependencies
        usage=ctx.usage,    # Track usage
    )
    return result.output
```

### Dependency Rules

- Child agent should have **same or subset** of parent's dependencies
- Pass `deps=ctx.deps` to share the dependency instance
- Avoid creating new connections in tools (reuse from deps)

## Pattern 3: Programmatic Hand-Off

Application code decides which agent runs next (not the LLM):

```python
from pydantic_ai import RunUsage

async def travel_booking_workflow():
    usage = RunUsage()  # Shared usage tracker

    # Step 1: Find flights
    flight_result = await flight_search_agent.run(
        'Find flights NYC to LAX next week',
        usage=usage,
    )

    if flight_result.output.found_flights:
        # Step 2: Select seats (only if flights found)
        seat_result = await seat_selection_agent.run(
            'Select window seats for these flights',
            message_history=flight_result.all_messages(),  # Preserve context
            usage=usage,
        )

        # Step 3: Book
        booking_result = await booking_agent.run(
            'Complete the booking',
            message_history=seat_result.all_messages(),
            usage=usage,
        )

    print(f"Total usage: {usage}")
```

### Key Points

1. **Explicit control flow** - Your code decides the sequence
2. **Message history sharing** - Pass `message_history` to continue context
3. **Single `RunUsage`** - Tracks cumulative usage across all agents
4. **Flexible dependencies** - Agents don't need matching deps_type

## Pattern 4: Hub-and-Spoke (Orchestrator Pattern)

For document editing or complex workflows with multiple specialists:

```python
from pydantic import BaseModel

# Specialist agents
grammar_agent = Agent(
    'google-gla:gemini-2.5-flash',
    output_type=GrammarCorrection,
    instructions='Fix grammar only. Do not change style or tone.',
)

style_agent = Agent(
    'google-gla:gemini-2.5-flash',
    output_type=StyleEdit,
    instructions='Adjust style to match the requested tone.',
)

citation_agent = Agent(
    'google-gla:gemini-2.5-flash',
    output_type=CitationCheck,
    instructions='Verify citations are accurate.',
)

# Orchestrator (the "hub")
editor_agent = Agent(
    'google-gla:gemini-2.5-pro',
    instructions='''
    You are a senior editor. Analyze the document and delegate to specialists:
    - Use check_grammar for syntax/punctuation issues
    - Use adjust_style for tone/voice changes
    - Use verify_citations for fact-checking
    Synthesize their results into a final edit plan.
    ''',
)

@editor_agent.tool
async def check_grammar(ctx: RunContext, text: str) -> GrammarCorrection:
    """Check grammar and punctuation."""
    result = await grammar_agent.run(text, usage=ctx.usage)
    return result.output

@editor_agent.tool
async def adjust_style(ctx: RunContext, text: str, target_tone: str) -> StyleEdit:
    """Adjust the style/tone of the text."""
    result = await style_agent.run(
        f'Rewrite in {target_tone} tone: {text}',
        usage=ctx.usage
    )
    return result.output

@editor_agent.tool
async def verify_citations(ctx: RunContext, text: str) -> CitationCheck:
    """Verify all citations in the text."""
    result = await citation_agent.run(text, usage=ctx.usage)
    return result.output
```

## Usage Limits Across Agents

```python
from pydantic_ai import UsageLimits

result = await orchestrator.run(
    prompt,
    usage_limits=UsageLimits(
        request_limit=20,         # Max API calls (all agents combined)
        total_tokens_limit=10000, # Max tokens (all agents combined)
    )
)
```

## Best Practices

1. **Isolate concerns** - Each agent has one job
2. **Always pass `usage=ctx.usage`** - Track costs accurately
3. **Use cheaper models for simple tasks** - Flash for grammar, Pro for planning
4. **Clear tool docstrings** - The parent agent needs to know what each child does
5. **Match temperature to task** - Lower for deterministic tasks, higher for creative
6. **Avoid deep nesting** - Keep delegation shallow (1-2 levels max)

## Anti-Patterns to Avoid

1. **Creating deps inside tools** - Reuse connections from parent deps
2. **Forgetting `usage=ctx.usage`** - Usage tracking will be inaccurate
3. **Monolithic prompts** - If you're cramming everything into one agent, split it
4. **Too many agents** - Start simple, add agents only when needed
