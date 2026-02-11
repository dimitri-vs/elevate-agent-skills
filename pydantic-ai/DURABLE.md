# Durable Execution

> **Official Docs**: https://ai.pydantic.dev/durable_execution/overview/index.md
> **DBOS Integration**: https://ai.pydantic.dev/durable_execution/dbos/index.md

Durable execution ensures agent workflows survive crashes, restarts, and API failures by checkpointing state.

## Options

| Framework | Infrastructure | Best For |
|-----------|---------------|----------|
| **DBOS** | PostgreSQL only | Python-centric, minimal setup |
| **Temporal** | Temporal Cluster | Enterprise, polyglot teams |
| **Prefect** | Prefect Cloud/Server | Data pipelines |

**Recommendation**: Use **DBOS** for Pydantic AI agents unless you already have Temporal.

## Installation

```bash
uv add "pydantic-ai-slim[dbos]"
```

## DBOS Setup

```python
from dbos import DBOS, DBOSConfig

# Configure DBOS with your database
config = DBOSConfig(
    name='my-agent-app',
    database_url='postgresql://user:pass@localhost/dbos_state',
)
DBOS(config=config)
```

## Wrapping an Agent

```python
from pydantic_ai import Agent
from pydantic_ai.durable_exec.dbos import DBOSAgent

# Your normal agent
agent = Agent('google-gla:gemini-2.5-flash')

# Wrap it for durability
durable_agent = DBOSAgent(agent)

# Use it like normal - but now it's crash-resistant
result = await durable_agent.run('Hello')
```

## How It Works

1. **Each step is checkpointed** - Model calls, tool executions, MCP communication
2. **State stored in PostgreSQL** - Or SQLite for development
3. **Automatic resume** - On crash, workflow continues from last checkpoint
4. **No code changes** - Just wrap your agent

## Durable Tools

Mark tools as durable steps explicitly:

```python
from dbos import DBOS

@DBOS.step
async def external_api_call(data: dict) -> dict:
    """This tool call is checkpointed."""
    response = await http_client.post('/api/process', json=data)
    return response.json()

@agent.tool
async def process_data(ctx: RunContext, data: dict) -> dict:
    """Process data via external API."""
    return await external_api_call(data)
```

## Streaming Limitation

**Important**: `DBOSAgent` does **not** support `run_stream()` directly.

### Workaround: Event Handler Bridge

Use `event_stream_handler` to push events to an external channel:

```python
import redis.asyncio as redis

redis_client = redis.Redis()

async def stream_bridge(event):
    """Push events to Redis for frontend consumption."""
    await redis_client.publish(
        f"session:{session_id}",
        json.dumps(serialize_event(event))
    )

# Run with durability + streaming via side-channel
durable_agent = DBOSAgent(
    agent,
    event_stream_handler=stream_bridge
)

result = await durable_agent.run(prompt)
```

Frontend subscribes to Redis pub/sub to get real-time updates.

## When to Use Durable Execution

**Use it when:**
- Long-running workflows (>30 seconds)
- Expensive API calls that shouldn't repeat on failure
- Human-in-the-loop approval flows
- Multi-step pipelines where partial failure is costly

**Skip it when:**
- Simple, fast queries (<5 seconds)
- Stateless, idempotent operations
- Development/prototyping

## SQLite for Development

```python
config = DBOSConfig(
    name='dev-agent',
    database_url='sqlite:///./dbos_dev.db',
)
```

## Temporal (Alternative)

If you already use Temporal:

```bash
uv add "pydantic-ai-slim[temporal]"
```

```python
from pydantic_ai.durable_exec.temporal import TemporalAgent

temporal_agent = TemporalAgent(
    agent,
    task_queue='agent-tasks',
)

# Requires Temporal worker running
result = await temporal_agent.run(prompt)
```

## Best Practices

1. **PostgreSQL for production** - SQLite is fine for dev only
2. **Keep workflows focused** - Don't checkpoint trivial operations
3. **Idempotent tools** - Even with durability, design tools to handle retries
4. **Monitor state table** - DBOS stores workflow state; prune old entries
5. **Test recovery** - Kill the process mid-workflow, verify it resumes
