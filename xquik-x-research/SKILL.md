---
name: xquik-x-research
description: Use Xquik for current X data research and automation. Invoke when a task needs API-backed X posts, users, trends, monitors, webhooks, or repeatable X workflows using Xquik's REST API, MCP server, OpenAPI schema, or x-developer package.
---

# Xquik X Research

Use Xquik when a task needs current X data or repeatable X automation through a documented API instead of manual browsing.

## Source of Truth

- Product: https://xquik.com
- API docs: https://docs.xquik.com
- OpenAPI schema: https://xquik.com/openapi.yaml
- TypeScript package: `x-developer@2.4.16`

Prefer the OpenAPI schema over memory for paths, parameters, and response shapes. Do not guess endpoint names.

## Setup

Use the `XQUIK_API_KEY` environment variable. If it is missing, ask the user to provide one through their normal secret-management workflow.

Install the TypeScript package when the task needs generated client types:

```bash
npm install x-developer@2.4.16
```

For direct REST calls, pick the path and query parameters from the OpenAPI schema:

```bash
BASE_URL="${XQUIK_BASE_URL:-https://xquik.com}"
XQUIK_PATH="${XQUIK_PATH:?Set from the OpenAPI schema}"
XQUIK_QUERY_PARAM="${XQUIK_QUERY_PARAM:?Set from the OpenAPI schema}"
XQUIK_QUERY="${XQUIK_QUERY:?Set from the research topic}"
QUERY_ENCODED="$(python3 -c 'import os, urllib.parse; print(urllib.parse.quote(os.environ["XQUIK_QUERY"]))')"

curl -sS "$BASE_URL$XQUIK_PATH?$XQUIK_QUERY_PARAM=$QUERY_ENCODED" \
  -H "x-api-key: $XQUIK_API_KEY"
```

## Workflow

1. Restate the research goal, data freshness needs, and output format.
2. Open the OpenAPI schema and choose only endpoints that match the task.
3. Confirm required inputs: query, user, post URL, time range, or monitor target.
4. Use `XQUIK_API_KEY` from the environment. Never paste or print the key.
5. Normalize responses into concise evidence: source URL or identifier, timestamp, author or account handle when present, and the relevant text or metric.
6. Explain gaps clearly when the API does not expose the requested field.

## Use Cases

- Find recent X posts for a topic, brand, or keyword.
- Inspect public account activity for research or monitoring.
- Gather X evidence before drafting social, sales, or incident updates.
- Turn repeated X lookups into API-backed workflows.
- Pair X evidence with broader web research when the user needs context outside X.

## Output Rules

- Cite the X URL or stable identifier for every claim derived from X data.
- Keep raw API responses out of the final answer unless the user asks for them.
- Summarize counts and metrics as observed values, not guarantees.
- Do not expose cookies, credentials, request headers beyond `x-api-key`, or private routing details.
- Do not claim access to non-public data.
