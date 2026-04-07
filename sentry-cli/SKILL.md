---
name: sentry-cli
description: Query Sentry issues and events via sentry-cli. Use when investigating errors, triaging issues, or resolving them programmatically.
---

# Sentry CLI

Org: `elevate-code`. Auth token stored in `~/.sentryclirc`.

**IMPORTANT**: Always use `sentry-cli` first. Only fall back to curl/API if sentry-cli genuinely lacks the subcommand. Run `sentry-cli <command> --help` to discover flags before assuming something isn't supported.

## Common Commands

```bash
# List projects
sentry-cli projects list --org elevate-code

# List unresolved issues for a project (use -s flag, not --query)
sentry-cli issues list -o elevate-code -p <project-slug> -s unresolved

# Resolve/mute/unresolve — use -i for ID, -p is required
sentry-cli issues resolve -o elevate-code -p <project-slug> -i <issue-id>
sentry-cli issues mute -o elevate-code -p <project-slug> -i <issue-id>
sentry-cli issues unresolve -o elevate-code -p <project-slug> -i <issue-id>
```

## API Fallback (last resort)

Only if `sentry-cli` lacks the subcommand (e.g. fetching full event payloads/stack traces):

```bash
curl -s -H "Authorization: Bearer $(grep token ~/.sentryclirc | cut -d= -f2)" \
  "https://sentry.io/api/0/issues/<issue-id>/events/latest/" | python -m json.tool
```
