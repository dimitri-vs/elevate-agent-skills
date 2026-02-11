# Tools (Function Calling)

> **Official Docs**: https://ai.pydantic.dev/tools/index.md
> **Advanced Features**: https://ai.pydantic.dev/tools-advanced/index.md
> **Built-in Tools**: https://ai.pydantic.dev/builtin-tools/index.md

Tools let the LLM call Python functions during a conversation. Pydantic AI validates arguments and handles errors automatically.

## Basic Tool Registration

### With Context (Access to Dependencies)

```python
from pydantic_ai import Agent, RunContext

@dataclass
class Deps:
    db: DatabaseConn
    user_id: str

agent = Agent('google-gla:gemini-2.5-flash', deps_type=Deps)

@agent.tool
async def search_notes(ctx: RunContext[Deps], query: str) -> list[dict]:
    """Search notes by query.

    Args:
        query: The search terms to look for.
    """
    return await ctx.deps.db.search_notes(ctx.deps.user_id, query)
```

### Without Context (Plain Tool)

```python
@agent.tool_plain
def get_current_time() -> str:
    """Get the current time in ISO format."""
    return datetime.now().isoformat()
```

## Tool Docstrings

**The docstring is sent to the LLM as the tool description.** Make it clear and helpful.

```python
@agent.tool
async def get_document(ctx: RunContext, doc_id: str, include_metadata: bool = False) -> dict:
    """Retrieve a document by its ID.

    Use this to fetch document content when the user references a specific document.

    Args:
        doc_id: The unique identifier of the document (e.g., 'doc_abc123').
        include_metadata: Whether to include creation date and author info.

    Returns:
        The document content and optionally its metadata.
    """
    ...
```

## Tool Parameters

Parameters are automatically converted to JSON Schema for the LLM:

```python
from typing import Literal
from pydantic import Field

@agent.tool_plain
def search(
    query: str,
    limit: int = Field(default=10, ge=1, le=100, description="Max results"),
    sort_by: Literal['relevance', 'date', 'title'] = 'relevance',
) -> list[dict]:
    """Search documents."""
    ...
```

## Error Handling with ModelRetry

When a tool fails due to bad LLM input, raise `ModelRetry` to give the LLM another chance:

```python
from pydantic_ai import ModelRetry

@agent.tool
async def get_user(ctx: RunContext, username: str) -> dict:
    """Get user by username."""
    user = await ctx.deps.db.find_user(username)
    if user is None:
        raise ModelRetry(
            f"No user named '{username}'. Available users: {await get_usernames()}"
        )
    return user
```

The error message is sent back to the LLM, which can correct its input.

## Tool Return Types

Tools can return any Pydantic-serializable type:

```python
from pydantic import BaseModel
from pydantic_ai import ImageUrl, DocumentUrl

class SearchResult(BaseModel):
    title: str
    snippet: str
    score: float

@agent.tool_plain
def search() -> list[SearchResult]:
    ...

@agent.tool_plain
def get_logo() -> ImageUrl:
    return ImageUrl(url='https://example.com/logo.png')

@agent.tool_plain
def get_report() -> DocumentUrl:
    return DocumentUrl(url='https://example.com/report.pdf')
```

## Dynamic Tools (Prepared Tools)

Register tools dynamically based on context:

```python
from pydantic_ai import Agent, RunContext, WebSearchTool

async def prepared_web_search(ctx: RunContext[dict]) -> WebSearchTool | None:
    """Only enable web search for premium users."""
    if ctx.deps.get('is_premium'):
        return WebSearchTool()
    return None  # Tool not available

agent = Agent(
    'google-gla:gemini-2.5-flash',
    builtin_tools=[prepared_web_search],
)
```

## Tool Lists (Toolsets)

Add multiple tools at once:

```python
from pydantic_ai import Agent
from pydantic_ai.tools import Tool

tools = [
    Tool(search_notes, takes_ctx=True),
    Tool(get_current_time, takes_ctx=False),
]

agent = Agent('google-gla:gemini-2.5-flash', tools=tools)
```

## Built-in Tools

### Web Search

```python
from pydantic_ai import Agent, WebSearchTool

agent = Agent(
    'google-gla:gemini-2.5-flash',
    builtin_tools=[WebSearchTool()],
)
```

### Web Fetch

```python
from pydantic_ai import WebFetchTool

agent = Agent(
    'google-gla:gemini-2.5-flash',
    builtin_tools=[WebFetchTool()],
)
```

### File Search (RAG)

```python
from pydantic_ai import FileSearchTool

agent = Agent(
    model,
    builtin_tools=[FileSearchTool(file_store_ids=['store_abc123'])]
)
```

## Common Tools (Third-Party)

### DuckDuckGo Search

```bash
uv add "pydantic-ai-slim[duckduckgo]"
```

```python
from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool

agent = Agent(
    'google-gla:gemini-2.5-flash',
    tools=[duckduckgo_search_tool()],
)
```

### Tavily Search

```bash
uv add "pydantic-ai-slim[tavily]"
```

```python
from pydantic_ai.common_tools.tavily import tavily_search_tool

agent = Agent(
    'google-gla:gemini-2.5-flash',
    tools=[tavily_search_tool(api_key='...')],
)
```

## LangChain Tool Integration

```python
from langchain_community.tools import DuckDuckGoSearchRun
from pydantic_ai.ext.langchain import tool_from_langchain

search = DuckDuckGoSearchRun()
search_tool = tool_from_langchain(search)

agent = Agent('google-gla:gemini-2.5-flash', tools=[search_tool])
```

## Best Practices

1. **Clear docstrings** - The LLM only sees the docstring, not your code
2. **Descriptive parameter names** - `user_id` not `uid`
3. **Use `Field()` for constraints** - `limit: int = Field(ge=1, le=100)`
4. **Raise `ModelRetry`** - Give the LLM helpful error messages
5. **Type hints everywhere** - They become the JSON Schema
6. **Async when possible** - Don't block the event loop
