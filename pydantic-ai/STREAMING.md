# Streaming Responses with FastAPI

> **Official Docs (Agents)**: https://ai.pydantic.dev/agents/index.md
> **Output Streaming**: https://ai.pydantic.dev/output/index.md

Pydantic AI supports streaming for real-time token delivery. This is essential for responsive UX in web applications.

## Streaming Methods

### `run_stream()` - Stream Final Output

Best for simple streaming where you just want the text as it arrives:

```python
async with agent.run_stream('Tell me a story') as response:
    async for chunk in response.stream_output():
        print(chunk, end='', flush=True)
```

### `run_stream_events()` - Stream All Events

For visibility into tool calls, thinking, and intermediate steps:

```python
async for event in agent.run_stream_events('What is the weather?'):
    if hasattr(event, 'delta'):
        print(event.delta.content_delta, end='')
    elif hasattr(event, 'part') and event.part.part_kind == 'tool-call':
        print(f"[Calling tool: {event.part.tool_name}]")
```

## FastAPI Integration

### Newline-Delimited JSON (NDJSON)

Simple, easy to parse on the frontend:

```python
import json
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()

@app.post("/chat")
async def chat(prompt: str) -> StreamingResponse:
    async def stream():
        # Echo user message first
        yield json.dumps({"role": "user", "content": prompt}) + "\n"

        # Stream agent response
        async with agent.run_stream(prompt) as response:
            async for chunk in response.stream_output(debounce_by=0.01):
                msg = {"role": "assistant", "content": chunk}
                yield json.dumps(msg) + "\n"

        # Optionally persist conversation
        # await db.save_messages(response.new_messages())

    return StreamingResponse(stream(), media_type="text/plain")
```

### Server-Sent Events (SSE)

Better browser support with automatic reconnection:

```python
@app.post("/chat/sse")
async def chat_sse(prompt: str) -> StreamingResponse:
    async def stream():
        async with agent.run_stream(prompt) as response:
            async for chunk in response.stream_output():
                data = json.dumps({"type": "token", "content": chunk})
                yield f"data: {data}\n\n"

            # Signal completion
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
```

## Streaming with Tool Visibility

Show users when tools are being called:

```python
async def stream_with_tools():
    async for event in agent.run_stream_events(prompt):
        payload = None

        # Text chunk
        if hasattr(event, 'delta') and hasattr(event.delta, 'content_delta'):
            payload = {
                "type": "text_delta",
                "content": event.delta.content_delta
            }

        # Tool call started
        elif hasattr(event, 'part') and event.part.part_kind == 'tool-call':
            payload = {
                "type": "tool_call",
                "tool_name": event.part.tool_name,
                "status": "started"
            }

        # Tool result
        elif hasattr(event, 'part') and event.part.part_kind == 'tool-return':
            payload = {
                "type": "tool_result",
                "status": "completed"
            }

        if payload:
            yield f"data: {json.dumps(payload)}\n\n"
```

## Event Stream Handler Pattern

Capture events during `run_stream()` for side effects (logging, progress updates):

```python
async def event_handler(event):
    """Called for each event during streaming."""
    if hasattr(event, 'part') and event.part.part_kind == 'tool-call':
        print(f"Tool called: {event.part.tool_name}")

async with agent.run_stream(
    prompt,
    event_stream_handler=event_handler
) as response:
    async for chunk in response.stream_output():
        yield chunk
```

## Streaming Structured Output

Stream and validate structured data as it arrives:

```python
from pydantic import BaseModel

class Story(BaseModel):
    title: str
    content: str
    genre: str

agent = Agent('google-gla:gemini-2.5-flash', output_type=Story)

async with agent.run_stream('Write a short story') as response:
    async for partial in response.stream_output():
        # partial is incrementally validated
        print(partial)  # Partial Story object
```

## Conversation History with Streaming

```python
@app.post("/chat")
async def chat(request: ChatRequest):
    # Get history from DB
    history = await db.get_messages(request.session_id)

    async def stream():
        async with agent.run_stream(
            request.prompt,
            message_history=history
        ) as response:
            async for chunk in response.stream_output():
                yield json.dumps({"content": chunk}) + "\n"

            # Save new messages after streaming completes
            await db.add_messages(
                request.session_id,
                response.new_messages_json()
            )

    return StreamingResponse(stream(), media_type="text/plain")
```

## Frontend Consumption

### JavaScript (Fetch API)

```javascript
const response = await fetch('/chat', {
  method: 'POST',
  body: JSON.stringify({ prompt: 'Hello' }),
});

const reader = response.body.getReader();
const decoder = new TextDecoder();

while (true) {
  const { done, value } = await reader.read();
  if (done) break;

  const lines = decoder.decode(value).split('\n');
  for (const line of lines) {
    if (line) {
      const data = JSON.parse(line);
      console.log(data.content);
    }
  }
}
```

### JavaScript (EventSource for SSE)

```javascript
const source = new EventSource('/chat/sse?prompt=Hello');

source.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.type === 'done') {
    source.close();
  } else {
    console.log(data.content);
  }
};
```

## Debouncing

Reduce network overhead by batching small chunks:

```python
async for chunk in response.stream_output(debounce_by=0.01):
    # Chunks are batched by 10ms
    yield chunk
```

## Error Handling

```python
from pydantic_ai.exceptions import UnexpectedModelBehavior

@app.post("/chat")
async def chat(prompt: str):
    async def stream():
        try:
            async with agent.run_stream(prompt) as response:
                async for chunk in response.stream_output():
                    yield json.dumps({"content": chunk}) + "\n"
        except UnexpectedModelBehavior as e:
            yield json.dumps({"error": str(e)}) + "\n"

    return StreamingResponse(stream(), media_type="text/plain")
```

## Important Notes

1. **`run_stream()` stops at first valid output** - If output_type is set, streaming stops when a valid response is detected, even if the model continues generating tool calls.

2. **Use `run_stream_events()` for full control** - When you need to see all events including post-output tool calls.

3. **Debounce for UX** - `debounce_by=0.01` (10ms) is a good default.

4. **Save history after streaming** - Call `response.new_messages()` after the stream completes.

## Common Gotchas

### Missing Initial Tokens (". " prefix bug)

Text arrives in BOTH `PartStartEvent.part.content` AND `PartDeltaEvent.delta.content_delta`. If you only handle `PartDeltaEvent`, you lose initial tokens (e.g., "Done") and see orphan punctuation like `. ` at the start.

**Fix**: Emit text from both event types:
```python
if isinstance(event, PartStartEvent) and event.part.part_kind == 'text':
    emit(event.part.content)  # Initial text - don't skip!
elif isinstance(event, PartDeltaEvent) and event.delta.part_delta_kind == 'text':
    emit(event.delta.content_delta)  # Subsequent chunks
```
See: [GitHub #2659](https://github.com/pydantic/pydantic-ai/issues/2659)
