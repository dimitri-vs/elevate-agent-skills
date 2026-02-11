---
name: pydantic-ai
description: Build AI agents with Pydantic AI framework. Use when implementing agents with Gemini/Google, structured outputs, tools, streaming, multi-agent delegation, or FastAPI integration.
---

# Pydantic AI Agent Framework

> **Official Docs**: https://ai.pydantic.dev/
> **Docs Index (for LLMs)**: https://ai.pydantic.dev/llms.txt
>
> This skill provides quick reference patterns. For the most up-to-date API details, fetch the docs index above and then the specific section you need.

Pydantic AI is a Python agent framework with FastAPI-like ergonomics. It provides type-safe outputs, dependency injection, and seamless integration with multiple LLM providers.

## Quick Start

```python
from pydantic_ai import Agent

agent = Agent(
    'google-gla:gemini-2.5-flash',  # Model name
    instructions='Be concise, reply with one sentence.',
)

result = agent.run_sync('What is Pydantic AI?')
print(result.output)
```

## Installation

```bash
uv add pydantic-ai                    # Full install
uv add "pydantic-ai-slim[google]"     # Slim with Google/Gemini only
uv add "pydantic-ai-slim[google,dbos]"  # With durable execution
```

## Core Concepts

### Agent Definition with Structured Output

```python
from dataclasses import dataclass
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

@dataclass
class AgentDeps:
    """Dependencies injected into tools and instructions."""
    user_id: str
    db: DatabaseConn

class AgentOutput(BaseModel):
    """Structured output validated by Pydantic."""
    answer: str = Field(description='The answer to the question')
    confidence: float = Field(ge=0, le=1)

agent = Agent(
    'google-gla:gemini-2.5-flash',
    deps_type=AgentDeps,
    output_type=AgentOutput,
    instructions='You are a helpful assistant.',
)
```

### Tools (Function Calling)

```python
@agent.tool
async def search_database(ctx: RunContext[AgentDeps], query: str) -> list[dict]:
    """Search the database for matching records.

    Args:
        query: The search query to execute.
    """
    return await ctx.deps.db.search(query)

@agent.tool_plain  # No context needed
def get_current_time() -> str:
    """Get the current time."""
    return datetime.now().isoformat()
```

### Dynamic Instructions

```python
@agent.instructions
async def add_context(ctx: RunContext[AgentDeps]) -> str:
    user = await ctx.deps.db.get_user(ctx.deps.user_id)
    return f"The user's name is {user.name}."
```

### Running the Agent

```python
# Synchronous
result = agent.run_sync('Hello', deps=deps)

# Async
result = await agent.run('Hello', deps=deps)

# Streaming (see STREAMING.md)
async with agent.run_stream('Hello', deps=deps) as response:
    async for chunk in response.stream_output():
        print(chunk, end='', flush=True)

# Access results
print(result.output)          # AgentOutput instance
print(result.usage())         # Token usage
print(result.new_messages())  # Conversation history
```

## Key Files Reference

- **[GEMINI.md](GEMINI.md)** - Google/Gemini model configuration and thinking mode
- **[TOOLS.md](TOOLS.md)** - Tool definitions, validation, and error handling
- **[STREAMING.md](STREAMING.md)** - FastAPI streaming patterns (SSE, NDJSON)
- **[MULTI-AGENT.md](MULTI-AGENT.md)** - Agent delegation and orchestration patterns
- **[DURABLE.md](DURABLE.md)** - Durable execution with DBOS/Temporal

## Model Names

```python
# Google AI Studio (API key)
'google-gla:gemini-2.5-flash'
'google-gla:gemini-2.5-pro'
'google-gla:gemini-3.0-pro'  # Latest

# Google Vertex AI (service account)
agent = Agent(GoogleModel('gemini-2.5-flash', provider='google-vertex'))

# Other providers
'openai:gpt-5'
'anthropic:claude-sonnet-4-0'
```

## Environment Variables

```bash
GOOGLE_API_KEY=...           # Google AI Studio
GOOGLE_CLOUD_PROJECT=...     # Vertex AI project
```

## Common Patterns

### Conversation History

```python
# First turn
result1 = await agent.run('Hello')

# Continue conversation
result2 = await agent.run(
    'Follow up question',
    message_history=result1.new_messages()
)
```

### Usage Limits

```python
from pydantic_ai import UsageLimits

result = await agent.run(
    prompt,
    usage_limits=UsageLimits(
        request_limit=10,        # Max API calls
        tool_calls_limit=5,      # Max tool executions
        total_tokens_limit=4000  # Max tokens
    )
)
```

### Error Handling with Retry

```python
from pydantic_ai import ModelRetry

@agent.tool
async def get_user(ctx: RunContext, name: str) -> dict:
    user = await db.find_user(name)
    if user is None:
        raise ModelRetry(f"No user named '{name}'. Try a different name.")
    return user
```

## Best Practices

1. **Always define `output_type`** for structured responses
2. **Use `deps_type`** for dependency injection (DB, HTTP clients, config)
3. **Pass `usage=ctx.usage`** when calling sub-agents for accurate tracking
4. **Tool docstrings are sent to the LLM** - make them descriptive
5. **Use `run_stream`** for real-time UX in web applications
