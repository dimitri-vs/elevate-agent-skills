---
name: railway-cli
description: Deploy and manage apps on Railway's cloud platform. Use when deploying, checking logs, managing environments, or running commands against Railway services.
---

# Railway CLI

Railway CLI for deploying and managing cloud applications.

Full CLI reference: https://docs.railway.com/reference/cli-api

## Important Notes

- **Monorepo**: For commands like `railway up`, ensure you are in the correct project subdirectory (e.g., `frontend/` or `backend/`)
- **Interactive commands**: `railway link`, `railway service`, `railway environment` are interactive by default. Use flags for non-interactive mode (see below).
- **Logs timeout**: Always run `railway logs` with a 5-second timeout as logs stream indefinitely
- **Deployments**: `railway up` may take up to 3-5 minutes to complete
- **JSON output**: All commands support `--json` flag for scripting

## Quick Reference

```bash
# Authentication
railway whoami                    # Check if logged in
railway login                     # Login (opens browser)
railway login --browserless       # Login via CLI for SSH/Codespaces

# Project management
railway init -n "my-project"      # Create new project
railway status                    # Show current project info
railway link                      # Link directory (interactive)
railway link -p <project> -e <env> -s <service>  # Link non-interactively
railway unlink                    # Disassociate project from directory
railway list                      # List all projects in account
railway open                      # Open project dashboard in browser

# Service management
railway service                   # Select/link a service (interactive)
railway service <name>            # Link specific service directly
railway add                       # Add empty service (interactive)
railway add -d postgres           # Add database (postgres, mysql, redis, mongo)
railway add -s api -i nginx:latest              # Add service from Docker image
railway add -s backend --variables "PORT=3000"  # Add with env vars

# Deployments
railway up                        # Deploy from current directory
railway up --detach               # Deploy without blocking terminal
railway up --ci                   # Stream build logs only, exit when done (for CI)
railway down                      # Remove most recent deployment
railway down -y                   # Remove without confirmation
railway redeploy                  # Redeploy latest deployment

# Logs (ALWAYS use a timeout)
railway logs -s <service>         # View logs (use 5s timeout)
railway logs -d                   # Deployment logs
railway logs -b                   # Build logs

# Environment variables
railway variables                 # View variables (table format)
railway variables --kv            # View in KEY=value format
railway variables --set "KEY=value"              # Set a variable
railway run -- <command>          # Run local command with Railway env vars
railway shell                     # Open subshell with Railway env vars loaded

# SSH access
railway ssh -s <service>                    # SSH into service container
railway ssh -s <service> -- <command>       # Run command in container

# Database
railway connect                   # Connect to database shell (psql, mongosh, etc.)
# Note: Requires local client installed (psql, redis-cli, mongosh, mysql)

# Environments
railway environment               # List/select environments (interactive)
railway environment new <name>    # Create new environment
railway environment new <name> -d <source>  # Duplicate existing environment
railway environment delete <name> # Delete environment

# Volumes
railway volume list               # List volumes
railway volume add                # Add a volume
railway volume attach             # Attach volume to service
railway volume detach             # Detach volume from service

# Domains
railway domain                    # Generate Railway domain
railway domain example.com -p 8080  # Add custom domain with port

# Help
railway help                      # List all commands
railway help <command>            # Help for specific subcommand
```

## Deployment Workflow

### Initial Setup (Interactive)
```bash
railway login           # Opens browser
railway link            # Select project/env/service interactively
```

### Initial Setup (Non-Interactive / CI)
```bash
railway login --browserless                     # For SSH/Codespaces
railway link -p myproject -e production -s api  # Specify all options
```

### Deploy
```bash
# From the correct subdirectory (e.g., frontend-v2/ or backend-v2/)
railway up --detach     # Deploy and return immediately
railway up --ci         # Stream build logs, exit when done (for CI pipelines)
```

### Check Status
```bash
railway status
# For logs, use timeout:
timeout 5 railway logs -s <service> || true
```

## Configuration Files

### railway.json
**Important**: The config file path does NOT follow Root Directory. In dashboard settings, use absolute path like `/backend/railway.json`.

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "builder": "RAILPACK",
    "buildCommand": "npm install && npm run build"
  },
  "deploy": {
    "startCommand": "npx serve dist -l $PORT",
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

### Railpack Environment Variables
Railpack is the new default builder (Nixpacks is legacy). Configure with env vars:

| Variable | Description |
|----------|-------------|
| `RAILPACK_PACKAGES` | Mise packages to install |
| `RAILPACK_BUILD_APT_PACKAGES` | Apt packages for build |
| `RAILPACK_DEPLOY_APT_PACKAGES` | Apt packages in final image |
| `RAILPACK_INSTALL_COMMAND` | Override install command |

See [Railpack docs](https://railpack.com/config/environment-variables) for full options.

### Monorepo Setup
1. Go to Railway Dashboard > Service > Settings
2. Set **Root Directory** to `/frontend` or `/backend`
3. Set **Railway Config File** to absolute path: `/frontend/railway.json`
4. Set **Watch Paths** using absolute paths: `/frontend/**`

## Running One-off Commands

### Using `railway run` (local command with Railway env vars)
```bash
# Injects Railway environment variables into local command
railway run --service=backend -- python script.py
```

### Using `railway shell` (subshell with env vars)
```bash
# Opens a new shell with all Railway env vars loaded
railway shell -s <service>
# Now all commands in this shell have access to env vars
```

### Using `railway ssh` (run inside container)
```bash
# List files in container
railway ssh -s <service> -- ls -la /

# Run script inside container
railway ssh -s <service> -- python /app/scripts/some_script.py
```

## Troubleshooting

- **"No start command found"**: Check Root Directory setting in dashboard
- **Port issues**: Vite/React apps need `$PORT` environment variable
- **Environment variables**: Prefix with `VITE_` for build-time availability in Vite apps
- **Variables syntax**: Use `railway variables --set "KEY=value"` (not `railway variables set`)

## Dashboard Settings Reference (as of 2026-01-22)

The Service > Settings page in the Railway dashboard has these sections:

### Source
- **Source Repo**: Connected GitHub repo with disconnect option
- **Root Directory**: Subdirectory for build/deploy (important for monorepos)
- **Branch**: Which branch triggers deployments for this environment
- **Wait for CI**: Option to wait for GitHub Actions before deploying

### Networking
- **Public Networking**:
  - Railway-generated domain (`*.up.railway.app`) with port configuration
  - Custom domains with proxy detection
  - Target port setting for each domain
- **Private Networking**:
  - Internal hostname (`<service>.railway.internal`) for service-to-service communication
  - Can also use short name (`<service>`) within the project
- **Static Outbound IPs**: Optional permanent IP for outbound traffic

### Build
- **Builder**: Railpack (new default) or Nixpacks (legacy). Configure via env vars or `railpack.json`
- **Metal Build Environment**: Faster builds (becoming default)
- **Providers**: Auto-detected language (Python, Node, etc.)
- **Custom Build Command**: Override default build command (follows Root Directory)
- **Watch Paths**: Gitignore-style patterns to trigger deploys on specific path changes
  - **Note**: Patterns always operate from repo root `/`, even with a Root Directory set. For root directory `/app`, use `/app/**/*.js` to match files.

### Deploy
- **Custom Start Command**: Command to run the service
- **Pre-deploy Command**: Runs before the main service starts (e.g., migrations)
- **Regions**: Select deployment regions and replica count
- **Teardown**: Configure old deployment termination behavior
- **Resource Limits**: CPU (up to 32 vCPU) and Memory (up to 32 GB) per replica
- **Cron Schedule**: Run service on a cron schedule
- **Healthcheck Path**: Endpoint to verify deployment is live before completing
- **Serverless**: Scale to zero when idle, wake on traffic
- **Restart Policy**: What to do on process exit (Never, Always, On Failure with retry count)

### Config-as-code
- **Railway Config File**: Path to `railway.json` or `railway.toml` for version-controlled settings
  - **Note**: Does NOT follow Root Directory. Use absolute path like `/backend/railway.toml`

### Root Directory Behavior
When a Root Directory is set, these settings follow it:
- Build command, start command, pre-deploy command

These do **NOT** follow Root Directory (always use absolute paths from repo root):
- **Watch Paths**: Use `/app/**/*.js` not `**/*.js` for root dir `/app`
- **Railway Config File**: Use `/backend/railway.toml` not `railway.toml`

### Danger Zone
- **Delete Service**: Permanently removes service and all deployments from the environment
