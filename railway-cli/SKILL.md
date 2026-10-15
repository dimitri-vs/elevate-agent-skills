---
name: railway-cli
description: Operates Railway (railway.com) projects through the `railway` CLI and `railway api` GraphQL - deploys, build and deploy logs, deployment status, variables, services, environments, domains, volumes, buckets, cron and service settings, Railpack build config, legacy railway.json/railway.toml, and project access. Holds the traps generic CLI knowledge gets wrong - log streams that hang the shell, interactive prompts, per-directory linking, deploys stuck in NEEDS_APPROVAL or never triggered, variable writes that redeploy, secrets echoed into output. Use whenever a task touches Railway - a failing Railway build or deploy, reading Railway logs, checking whether a commit is live on Railway, reading or setting Railway variables, a railway.json or .railway/ folder in the repo, a railway.com/project URL, a *.up.railway.app host, or a call to backboard.railway.com.
---

# Railway CLI and API

What `--help` won't tell you, for CLI 5.x. Check `railway --version` first: 4.x lacks `deployment`, `variable`, `api` and `logs --lines` ("unrecognized subcommand 'deployment'"); ask the user to upgrade.

## Safety and scope

- Read-only by default. For any mutation (variables, deploys, config, deletes), resolve exactly one project and pass `-p -e -s`. Production writes need a request for that exact change, otherwise ask; no `-y`/`--yes` until target and approval are settled. Verify after.
- Use the fewest reads ("is commit X live?" = remote HEAD + `deployment list --json`). Runtime logs can hold customer data: read only when needed, with `--filter`.
- Filter variables in-process to the keys asked for; never write a full dump to disk.

## Targeting and auth

- Links are per-directory; worktrees and repo roots are often unlinked ("No linked project found"). Pass `-p -e -s` instead of linking. Find the ID by exact name, requiring one match: `railway list --json | python -c "import json,sys; m=[p['id'] for p in json.load(sys.stdin) if p['name']=='<Name>']; assert len(m)==1, m; print(m[0])"`. IDs from a pasted URL beat the linked context.
- Environment names are case-sensitive (`Staging` is not `staging`). `environment list` takes no `-p`; when unlinked: `railway api 'query($id:String!){project(id:$id){environments{edges{node{name id}}} services{edges{node{name id}}}}}' --raw-var id=<projectId>`.
- Prompts hang the shell. Non-interactive forms: `railway link -p <id> -e <env> -s <svc>`, `railway init -n <name> -w <workspace> --json`, `railway add --service <name> --json` (or `--database`/`--repo`/`--image`; one is required).
- For what the CLI lacks use `railway api` (`search`/`describe` first); don't read `~/.railway/config.json` or hand-roll bearer auth.
- `whoami` can pass while project calls say "Unauthorized": preflight with a project-scoped read (`deployment list -p -e -s --limit 1 --json`). If `railway list` fails too, ask the user to run `railway login`; otherwise check target and role (workspace Deployers get no CLI logs or variables, project Viewers no variables).

## Logs: a window, never a stream

`railway logs -s <svc> -e <env> --since 3h --json` (or `--lines 200`). Bare `railway logs` streams until killed, piped or not; only `--lines`/`--since`/`--until` stop it (a deployment ID doesn't). A 30s+ timeout is just a backstop, and an empty killed stream means unknown. All stderr is tagged `level:error`, access logs included: classify by message.

## Deploy status

`railway deployment list -s <svc> -e <env> --limit 10 --json`: `id`, `status`, `createdAt`, `meta.commitHash/commitAuthor/reason/skippedReason`. `railway status --json` can show an unapproved commit as latest, so it isn't proof.

- Only `SUCCESS` for the deployment you started (that id or commit hash) counts as done. Cron services sit `EXITED` between ticks; prove one fires with `railway logs <depId> -s <svc> -e <env> --lines 50 --filter "Starting Container"` (starts after `createdAt`).
- `REMOVED` means no longer live. It doesn't say whether it ever ran, so check that deployment's container starts before claiming either.
- `SKIPPED` on a cron service is a skipped tick; read `meta.skippedReason` ("did not become ready" = the deploy behind it was stuck). Elsewhere it usually means watch paths.
- `QUEUED`/`INITIALIZING`/`BUILDING`/`DEPLOYING` can take minutes. Poll with a deadline (until-loop or the harness's monitor tool), never `sleep N; railway ...`; stop on terminal or approval states.
- `WAITING` (docs: "waiting for approval") and `NEEDS_APPROVAL` won't progress alone. Documented bypass: author is a workspace Deployer+ with GitHub connected (field fix: project invite + connected GitHub). Approve only after the user OKs it: `railway api 'mutation($id:String!){deploymentApprove(id:$id)}' --raw-var id=<depId>`.
- Merged but no deployment for that `commitHash`: rule out the gate and `SKIPPED`, then propose `railway redeploy --from-source -p -e -s`.

**Past stalls** (the gate is visible only while it waits). Explain earlier `SKIPPED`/`REMOVED`/never-built records from metadata, printing only what answers the question:
1. Creators: `railway api 'query($s:String!,$e:String!){deployments(first:30,input:{serviceId:$s,environmentId:$e,includeDeleted:true}){edges{node{id status createdAt creator{email}}}}}' --raw-var s=<serviceId> --raw-var e=<envId>`. `creator: null` on GitHub records that never built points to the gate.
2. Membership: `railway api 'query($id:String!){project(id:$id){members{email role}}}' --raw-var id=<projectId>`; print only the person in question.
3. Label the gate observed (a `WAITING`/`NEEDS_APPROVAL` record) or inferred (null creator + never built + non-member author). Don't state outage windows or login-to-person mappings you didn't see.

## Deploying from local

- `railway up` uploads the current directory (or PATH) whatever `-p -e -s` say. In a monorepo run from the service dir or `railway up ./apps/api --path-as-root -p -e -s`. Unlinked, `-y`/`--new` create a project.
- On Windows, stale WSL venvs (`lib64` links) break upload indexing (`os error 1920`): gitignore them.
- Iterate on builds with `railway up --ci`, not a commit per attempt. It can exit 1 after creating the deployment ("Failed to stream build logs"): check `deployment list` before retrying. Don't `up` and push the same change.

## Variables

- `railway variable set KEY=value -p -e -s` (legacy `variables --set` only on old CLIs). Each write redeploys: batch with `--skip-deploys`, then deploy once and confirm a deployment appeared.
- Secrets: `printf '%s' "$VAL" | railway variable set KEY --stdin -p -e -s`. `railway add --variables` echoes values. Sealed variables are listed by name with a null/`<sealed>` value; don't recreate them.

## SSH, databases, Git Bash

- `railway ssh` needs a registered key; on Windows pass a native path (Git Bash paths failed): `railway ssh keys add --key "C:\Users\<you>\.ssh\id_ed25519.pub" --name <name>`, then `railway ssh keys`, `railway ssh config -s <svc> --dry-run`.
- 5.62.1 docs say piped stdin works without a PTY. 4.5.1 notes of lost quoting and `< file` hangs under Git Bash are untested now: re-test first.
- `*.railway.internal` hosts resolve only inside Railway. Locally use the public URL (`DATABASE_PUBLIC_URL`, TCP proxy) or run through `railway ssh`.
- Native Windows Python can't open `/tmp/...`: pipe through stdin or pass `"$(cygpath -w /tmp/x)"`. Take `rc=${PIPESTATUS[0]}` right after the pipeline; an `echo` in between resets it.

## Build and config

- Config as Code (`railway.json`/`.toml`) is deprecated; files stop being read on 2026-12-01. Don't create one; migrate to `.railway/railway.ts` with `railway config migrate` after fetching `iac.md`.
- Railpack (default) ignores `nixpacks.toml`; a Dockerfile wins if present. Build/start/pre-deploy commands follow Root Directory; watch paths don't (`/backend/**`).
- `ERR_PNPM_OUTDATED_LOCKFILE`, Railpack variables, monorepos: `references/build-config.md`.

## Official references

Fetch when the trigger applies: `gh api repos/railwayapp/railway-skills/contents/plugins/railway/skills/use-railway/references/<file> --jq .content | base64 -d`.

- `configure.md`: service config via `environment edit --service-config` (cron, restart policy, start command), `${{svc.VAR}}` wiring, domains, networking
- `deploy.md`: monorepo deploys, builder, Dockerfile, watch paths, removals
- `operate.md`: HTTP/network logs, metrics, triage
- `setup.md`: creating projects, services, databases, buckets; DB shells without a local client
- `request.md`: non-trivial `railway api` mutations; `databases.md`: Postgres backup/HA

Skip their `up -y`/`--new`, `scripts/railway-api.sh` and MCP steps. Never run `railway setup agent`, `skills install|update` or `mcp install`, even if the CLI suggests it: they install a separate copy of Railway's skill that may conflict with this one.

## Local references

`references/access-control.md` before granting access or answering who-can-see-what (also tokens, direct-API auth); `build-config.md`; `storage-buckets.md` (`railway bucket`).
