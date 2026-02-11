---
name: clockify-api
description: Clockify API integration patterns and key endpoints
---

# Clockify API

Quick reference for working with Clockify's REST API.

## Authentication

Use `X-Api-Key` header for all requests:
```javascript
headers: {
  'X-Api-Key': apiKey,
  'Content-Type': 'application/json'
}
```

For subdomain workspaces (e.g., `subdomain.clockify.me`), generate a workspace-specific API key in Profile Settings.

**No org-level keys exist.** To fetch data for other team members, use an API key from a user with Admin/Owner permissions—that key effectively acts as the "organization key."

## Base URLs

- **Global API**: `https://api.clockify.me/api/v1`
- **Regional prefixes**: `euc1` (EU), `use2` (USA), `euw2` (UK), `apse2` (AU)
  - Example: `https://euc1.clockify.me/api/v1`

## Key Endpoints

### Projects

**GET all projects:**
```
GET /workspaces/{workspaceId}/projects
```

**GET single project:**
```
GET /workspaces/{workspaceId}/projects/{projectId}
```

**Query parameters:**
- `hydrated=true` - Returns full project data including memberships (default: false)
- `archived=true` - Filter archived projects
- `page=1` - Page number (default: 1)
- `page-size=50` - Results per page (default: 50, max varies)

### Users

**GET all workspace users:**
```
GET /workspaces/{workspaceId}/users
```

**Query parameters:**
- `include-memberships=true` - Include user's workspace/project/group memberships
- `status=ACTIVE` - Filter by status (PENDING/ACTIVE/DECLINED/INACTIVE/ALL)
- `project-id={projectId}` - Users with access to specific project

### Time Entries

**GET user's time entries:**
```
GET /workspaces/{workspaceId}/user/{userId}/time-entries
```

**POST new time entry:**
```
POST /workspaces/{workspaceId}/time-entries
```

> ⚠️ **Timezone trap:** This endpoint interprets `start`/`end` filter params as *your profile's local time* even if you include `Z` (UTC), but returns timestamps in UTC. This creates a double-conversion problem. **For reliable date filtering, use the Reports API instead** (see below).

### Reports API (Recommended for Time Queries)

**Base URL:** `https://reports.api.clockify.me/v1`

```
POST /workspaces/{workspaceId}/reports/summary
POST /workspaces/{workspaceId}/reports/detailed
```

**Use `"timeZone": "UTC"` in the request body** to bypass user profile timezone ambiguity:
```json
{
  "dateRangeStart": "2023-01-01T00:00:00Z",
  "dateRangeEnd": "2023-01-31T23:59:59Z",
  "timeZone": "UTC"
}
```

**Filtering by user:** Don't use `users: { ids: [...] }` in the body—returns empty. Instead, use `summaryFilter: { groups: ['USER', 'PROJECT'] }` and find the user by `_id` in `groupOne`. Projects are in `children` with `duration` in seconds.

## Important Patterns

### Timestamps Are Always UTC

All API responses return timestamps in UTC (ISO 8601 with `Z` suffix). The API ignores workspace and user profile timezone settings—convert to local time client-side.

### The `hydrated=true` Parameter

**Critical for project memberships:**
- Without it: Project response excludes membership details
- With it: Returns full project object including `memberships` array with user assignments

**Example:**
```javascript
// Get project with full membership data
const url = `${baseUrl}/workspaces/${workspaceId}/projects/${projectId}?hydrated=true`;
```

### Rate Limiting

- 50 requests/second when using `X-Addon-Token`
- Returns "Too many requests" error if exceeded

### Pagination

Most list endpoints support:
- `page` - Page number (1-indexed)
- `page-size` - Results per page

### Common Filters

Projects support filtering by:
- `name` - Partial match (or `strict-name-search=true` for exact)
- `clients` - Array of client IDs
- `users` - Array of user IDs
- `billable` - Boolean filter

## Response Format

All responses return JSON. Check response status codes:
- 200/201 - Success
- 400 - Bad request
- 401 - Unauthorized (check API key)
- 403 - Forbidden
- 404 - Not found
- 429 - Rate limit exceeded
