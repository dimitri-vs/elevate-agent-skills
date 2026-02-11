# Google / Gemini Integration

> **Official Docs**: https://ai.pydantic.dev/models/google/index.md
> **Thinking Mode**: https://ai.pydantic.dev/thinking/index.md

Pydantic AI has native support for Google's Gemini models via two APIs.

## Model Names

```python
# Generative Language API (Google AI Studio)
'google-gla:gemini-2.5-flash'
'google-gla:gemini-2.5-pro'
'google-gla:gemini-3.0-pro'  # Latest

# Vertex AI (Enterprise)
'google-vertex:gemini-2.5-flash'
'google-vertex:gemini-2.5-pro'
```

## Installation

```bash
uv add "pydantic-ai-slim[google]"
```

## Authentication

### Google AI Studio (Generative Language API)

```bash
export GOOGLE_API_KEY=your-api-key-from-aistudio
```

### Vertex AI

```bash
# Option 1: Application Default Credentials
gcloud auth application-default login

# Option 2: Service Account
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

# Required for Vertex AI
export GOOGLE_CLOUD_PROJECT=your-project-id
```

## GoogleModel Class

```python
from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel, GoogleProvider

# Simple usage with model string
agent = Agent('google-gla:gemini-2.5-flash')

# Explicit GoogleModel for more control
model = GoogleModel(
    'gemini-2.5-flash',
    provider=GoogleProvider(
        api_key='...',           # Optional: override env var
        project='my-project',    # Vertex AI project
        location='us-central1',  # Vertex AI region
    )
)
agent = Agent(model)
```

## GoogleModelSettings

Configure model behavior per-run:

```python
from pydantic_ai.models.google import GoogleModelSettings

settings = GoogleModelSettings(
    temperature=0.7,
    max_tokens=4096,
    top_p=0.95,
    top_k=40,
)

result = await agent.run(prompt, model_settings=settings)
```

## Thinking Mode (Extended Reasoning)

Gemini 2.5+ supports thinking mode for complex reasoning tasks.

```python
from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings

model = GoogleModel('gemini-2.5-pro')

settings = GoogleModelSettings(
    google_thinking_config={
        'include_thoughts': True,  # Return thinking in response
        'thinking_budget': 1024,   # Max tokens for thinking
    }
)

agent = Agent(model, model_settings=settings)
```

### Thinking Level vs Budget (Gemini 3.0)

Gemini 3.0 uses `thinking_level` instead of `thinking_budget`:

```python
settings = GoogleModelSettings(
    google_thinking_config={
        'thinking_level': 'high',  # 'minimal', 'low', 'medium', 'high'
    },
    temperature=1.0,  # Required for thinking mode
)
```

**Important**: Gemini 3.0's reasoning engine is optimized for `temperature=1.0`. Lower temperatures can cause looping or degraded reasoning.

## Safety Settings

```python
from pydantic_ai.models.google import GoogleModelSettings

settings = GoogleModelSettings(
    gemini_safety_settings=[
        {
            'category': 'HARM_CATEGORY_HARASSMENT',
            'threshold': 'BLOCK_MEDIUM_AND_ABOVE',
        },
        {
            'category': 'HARM_CATEGORY_HATE_SPEECH',
            'threshold': 'BLOCK_ONLY_HIGH',
        },
    ]
)
```

## Multi-Modal Input

### Images

```python
from pydantic_ai import Agent, ImageUrl

agent = Agent('google-gla:gemini-2.5-flash')

result = await agent.run([
    'Describe this image:',
    ImageUrl(url='https://example.com/image.jpg'),
])
```

### Documents (PDF)

```python
from pydantic_ai import DocumentUrl

result = await agent.run([
    'Summarize this document:',
    DocumentUrl(url='https://example.com/report.pdf'),
])
```

### Files API (For Large Files)

```python
from pydantic_ai.models.google import GoogleModel

model = GoogleModel('gemini-2.5-flash')

# Upload a file
with open('large_document.pdf', 'rb') as f:
    file = await model.client.aio.files.upload(file=f)

# Reference in prompt
result = await agent.run([
    'Analyze this document:',
    {'file_uri': file.uri},
])
```

## File Search (RAG)

```python
from pydantic_ai import Agent, FileSearchTool
from pydantic_ai.models.google import GoogleModel

model = GoogleModel('gemini-2.5-flash')

# Create file search store
store = await model.client.aio.file_search_stores.create(
    config={'display_name': 'my-docs'}
)

# Upload document
with open('document.txt', 'rb') as f:
    await model.client.aio.file_search_stores.upload_to_file_search_store(
        file_search_store_name=store.name,
        file=f,
        config={'mime_type': 'text/plain'}
    )

# Use with agent
agent = Agent(
    model,
    builtin_tools=[FileSearchTool(file_store_ids=[store.name])]
)
```

## Configuration Matrix for Document Editing Agents

| Agent Role | Model | Thinking Level | Temperature | Rationale |
|------------|-------|----------------|-------------|-----------|
| **Primary Editor** | gemini-3.0-pro | high | 1.0 | Complex reasoning for planning |
| **Grammar Checker** | gemini-3.0-flash | low | 0.7 | Rule-based, fast |
| **Style Specialist** | gemini-3.0-flash | medium | 0.9 | Needs nuance |
| **Citation Verifier** | gemini-3.0-flash | minimal | 0.0 | Deterministic lookup |

## Common Issues

### API Key Not Found
```
UserError: API key must be provided or set in GOOGLE_API_KEY
```
**Fix**: Set `GOOGLE_API_KEY` environment variable.

### Vertex AI Authentication
```
DefaultCredentialsError: Could not automatically determine credentials
```
**Fix**: Run `gcloud auth application-default login` or set `GOOGLE_APPLICATION_CREDENTIALS`.

### Rate Limits
Use exponential backoff or set usage limits:
```python
from pydantic_ai import UsageLimits

result = await agent.run(
    prompt,
    usage_limits=UsageLimits(request_limit=10)
)
```
