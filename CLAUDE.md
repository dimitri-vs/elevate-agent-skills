# Elevate Agent Skills

Central repository for reusable skills for Claude Code and Codex CLI. Skills are synced to projects using `skill-sync.py`. Both `.claude/skills/` and `.codex/skills/` target directories are supported.

## skill-sync.py Quick Reference

```bash
# List available skills
python skill-sync.py list

# Check sync status across all projects
python skill-sync.py status

# Add a skill to a project
python skill-sync.py add <skill-name> --to "C:\path\to\project\.claude\skills"

# Sync all skills to registered projects (after editing)
python skill-sync.py sync

# Sync a specific skill only
python skill-sync.py sync <skill-name>

# Preview sync without making changes
python skill-sync.py sync --dry-run

# Force overwrite (when target has local edits)
python skill-sync.py sync --force

# Remove a skill registration
python skill-sync.py remove <skill-name> --from "C:\path\to\project\.claude\skills"

# Remove and delete files
python skill-sync.py remove <skill-name> --from "C:\path\to\project\.claude\skills" --delete

# Discover skill directories in a path
python skill-sync.py discover "/path/to/projects"
```

## Status Indicators

- `[OK]` - Target matches source
- `[BEHIND]` - Source updated, target needs sync
- `[LOCAL EDITS]` - Target modified locally (needs `--force`)
- `[CONFLICT]` - Both changed (needs `--force`)
- `[MISSING]` - Target doesn't exist yet

## Workflow

1. **Edit skills here** in the skill subdirectories
2. **Sync to projects**: `python skill-sync.py sync`
3. **Commit both repos** if the target project is a separate git repo

## Local / Private Skills

The `local/` directory is gitignored and holds skills with personal or proprietary content. `skill-sync.py` scans both root and `local/` automatically — local skills show a `[local]` tag in `list` output and sync identically to public ones.

## Adding a New Skill

1. Create directory: `<skill-name>/` (or `local/<skill-name>/` for private skills)
2. Add `SKILL.md` with frontmatter (`name`, `description`)
3. Register: `python skill-sync.py add <skill-name> --to "..."`
