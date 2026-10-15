# Access control

Verified as of 2026-10-15 against docs.railway.com (workspaces, project members, variables, enterprise access groups and environment RBAC, committed spend, public API). The Access Groups price is a dashboard observation from 2026-08-20; the docs don't state it.

## Roles

- **Workspace roles: Admin, Member, Deployer.** A workspace Member can see every project in the workspace, including viewing, creating, changing and deleting variables, deploying and creating services. Members can't delete services, volumes or projects. Deployer can view projects and deploy via GitHub commits only: no CLI deploys, no variable access, no logs. On a plain Pro plan there is no way to hide one project's variables from a workspace Member.
- **Project members** (Project Settings > Members, no workspace role needed): Owner, Editor, Viewer. Editor can deploy and change settings but can't do destructive actions. Viewer is read-only and can't see variables or deploy. Project-level invites are the usual way to give contractors or clients access to one project without exposing the rest of the workspace. They only restrict people who are not also workspace Members: the workspace role wins.
- Pro includes unlimited workspace seats.
- Project members don't appear on the workspace People page, which lists workspace roles only. Check Project Settings > Members, or GraphQL `projectMembers(projectId:)` and `projectInvitations(id:)` (the argument names differ).
- GraphQL `ProjectRole` maps `ADMIN` to Owner, `MEMBER` to Editor ("Can Edit") and `VIEWER` to Viewer ("Can View").
- Deploy approval: a deployment can wait in `WAITING` / `NEEDS_APPROVAL` when the commit author isn't recognised (see SKILL.md). Documented: the gate is skipped when the author has the **workspace** role Deployer or higher and a connected GitHub account (Deployer is a workspace role; project roles are Owner/Editor/Viewer). Field-observed, not documented: inviting the author as a project member and having them connect the GitHub account they commit from also cleared it (2026-09-22, 2026-10-15).

## Restricting access beyond roles

- **Access Groups** (workspace People settings, Groups tab): admin-managed groups of people, projects and a role that limit which projects non-admin members can see. Applies to the dashboard, API, CLI, MCP server and Railway Agent tools. Docs say "eligible committed spend plans"; the in-dashboard unlock dialog quoted a $5,000/mo committed-spend minimum as of 2026-08-20, so check current terms.
- **Environment RBAC** (e.g. restrict production to admins): committed-spend $2,000/mo tier. `git push` deploys into a restricted environment still go through.
- **Trusted domains** (People settings): auto-onboard anyone who signs up with a matching email domain at a chosen role. It's onboarding automation, not a security boundary; a default role of Member grants workspace-wide variable access.

## Secrets

- **Sealed variables**: per-variable, write-only. Still injected into builds and deploys, but never shown again in the UI or API and can't be unsealed. Still listed by name in `railway variable list`, with `null` in JSON or `<sealed>` in the table; the value can't be read. Excluded from `railway run`, PR environments, environment duplication and value-bearing exports. Set via the dashboard variable menu or the `variables.<KEY>.isSealed` config path. Good for tokens only the running service needs; note them separately for any handoff, since exports won't contain them.

## Tokens

- **Project tokens** are scoped to one environment of one project; use the `Project-Access-Token` header, or `RAILWAY_TOKEN` for the CLI.
- **Account tokens** ("No workspace" on the tokens page) can do anything you can, across all your workspaces. Don't share them.
- **Workspace tokens** cover every project in one workspace regardless of Access Groups. Treat one as a workspace-admin credential; prefer project tokens for single-project integrations.
- Account and workspace tokens go in `RAILWAY_API_TOKEN` for the CLI, or `Authorization: Bearer` for the API.
- Direct API calls (rare; prefer `railway api`, which handles auth): the CLI session's bearer is `user.accessToken` in `~/.railway/config.json` (`user.token` is null on current OAuth logins), the endpoint is `https://backboard.railway.com/graphql/v2`, and requests need a non-default `User-Agent` such as `railway-cli` (field reports of 403s for Python urllib's default; not confirmed in the 2026-10-15 test). Never print the token.
- "Unauthorized" on one project while `railway list` works is a target or permission problem, not an expired session: a workspace Deployer gets no CLI logs or variables; a project Viewer sees no variables.
- Interactive sessions use `railway login` (browser consent; a human has to do it). `--browserless` is a device-code flow for machines with no browser, not a CI method; CI uses the token variables above.
