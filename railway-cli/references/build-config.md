# Build configuration

Verified as of 2026-10-15 against docs.railway.com, railpack.com and Railway CLI 5.62.1. The Config as Code section expires on 2026-12-01.

## Builders

- Documented builders are `RAILPACK` (default for new services) and `DOCKERFILE`. Nixpacks has been dropped from the docs; older services may still run on it, and the config schema still accepts `NIXPACKS`.
- A Dockerfile in the build root wins if present.
- `nixpacks.toml` is read only when the builder is Nixpacks. Under Railpack it's silently ignored, so editing it to fix a Railpack build changes nothing. Check the service's builder first (`build.builder` in `railway environment config -e <env> --json`, or Settings > Build), then use `railpack.json` or the `RAILPACK_*` variables below.
- Field notes: adding a `railway.json` to a working service has changed its GitHub-triggered builds enough to break runtime imports, and Railpack's prepare step has ignored a `railway.json` in a subdirectory. When a config file seems to have no effect, set the value on the service instead (`railway environment edit --service-config`, see Railway's `configure.md`).

## Railpack variables

Set as service variables. Lists are space-separated.

| Variable | Effect |
|---|---|
| `RAILPACK_INSTALL_CMD` | Replace the install step |
| `RAILPACK_BUILD_CMD` | Replace the build step |
| `RAILPACK_START_CMD` | Replace the start command |
| `RAILPACK_PACKAGES` | Extra Mise packages, `pkg[@version]` |
| `RAILPACK_BUILD_APT_PACKAGES` | Apt packages for the build |
| `RAILPACK_DEPLOY_APT_PACKAGES` | Apt packages in the final image (e.g. `ffmpeg`) |

Full list: https://railpack.com/config/environment-variables

## Node and pnpm under Railpack

- Package manager detection order: `packageManager` in `package.json`, then Mise idiomatic version files, then lockfiles, then `engines`. Pin with `"packageManager": "pnpm@10.x.y"`; Mise installs that pnpm version (Corepack is only used for npm and Yarn).
- With a `pnpm-lock.yaml`, Railpack runs `pnpm install --frozen-lockfile --prefer-offline`. A lockfile that drifted from `package.json` fails with `ERR_PNPM_OUTDATED_LOCKFILE`: run `pnpm install` locally and commit the lockfile, or override with `RAILPACK_INSTALL_CMD`.
- Mixed lockfiles: track only the lockfile for the package manager you use and gitignore the others. On legacy Nixpacks setups that ran `npm ci`, a stale `package-lock.json` next to `pnpm-lock.yaml` broke every deploy.

## Frontend build-time variables

Framework public variables (`VITE_*`, `NEXT_PUBLIC_*`) are baked in during the build. Changing one needs a rebuild (a new deployment), not a restart. The server must listen on `$PORT` (Railway injects it); a static Vite build needs a server such as `serve dist -l $PORT`.

## Root Directory

| Follows Root Directory | Does not (absolute from repo root) |
|---|---|
| Build command, start command, pre-deploy command | Watch paths (`/backend/**`, not `**`) |
| | Railway Config File path (`/backend/railway.toml`) |

"No start command could be found" usually means the Root Directory is wrong.

## Monorepos

- CLI: `railway environment edit --service-config <svc> source.rootDirectory "/frontend"`, and watch paths as a JSON array, `build.watchPatterns '["/frontend/**"]'`. Railway's `deploy.md` covers isolated vs shared monorepos and the pnpm `--filter` pitfalls.
- Dashboard: Service > Settings > Source > Root Directory, then Build > Watch Paths (absolute paths).

## Legacy Config as Code (until 2026-12-01)

- `railway.json` / `railway.toml` keep working only for services that already use them, until the 2026-12-01 cutoff. New services can't opt in. Don't create these files.
- The replacement is Infrastructure as Code in `.railway/railway.ts` (`railway config init/pull/plan/apply`).
- Migrate with `railway config migrate` (dry run, prints proposed code), then `--apply`. Use CLI 5.49.1 or newer, since older TypeScript output could drop `preDeployCommand`. Compare commands, healthchecks and replicas against the original before `railway config plan`. Read Railway's `iac.md` first.
- While a legacy file still exists, its values take precedence over dashboard settings at deploy time, and the Railway Config File path in settings must be absolute (it doesn't follow Root Directory).
- Schema URL for editing legacy files: `https://railway.com/railway.schema.json`.

## Deploy triggers and networking (from the 2026-01-22 dashboard inventory, docs-checked 2026-10-15)

- Service > Settings > Source has **Branch** (which branch deploys each environment) and **Wait for CI** (hold deploys until GitHub checks pass). Both explain a merge that didn't deploy when no `SKIPPED` / approval record exists.
- Private networking: `<service>.railway.internal` over plain `http://` on the service's port, resolvable only from inside Railway.
