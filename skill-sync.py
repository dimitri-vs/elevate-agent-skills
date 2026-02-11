#!/usr/bin/env python3
"""
Skill Sync - Manage Claude Code and Codex CLI skills across projects.

A CLI tool to sync skills from a central repository to multiple projects.
Supports both .claude/skills/ and .codex/skills/ target directories.
"""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

# Constants
SCRIPT_DIR = Path(__file__).parent.resolve()
MANIFEST_FILE = SCRIPT_DIR / "manifest.json"
SKILLS_IGNORE = {".git", "__pycache__", ".DS_Store", "manifest.json", "skill-sync.py", "README.md", "local", "CLAUDE.md"}
LOCAL_DIR = SCRIPT_DIR / "local"


def load_manifest() -> dict:
    """Load the manifest file, creating it if it doesn't exist."""
    if not MANIFEST_FILE.exists():
        default_manifest = {
            "source_dir": str(SCRIPT_DIR),
            "installations": {}
        }
        save_manifest(default_manifest)
        return default_manifest

    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Migrate old format (list of paths) to new format (dict with metadata)
    manifest = migrate_manifest(manifest)
    return manifest


def migrate_manifest(manifest: dict) -> dict:
    """Migrate old manifest format to new format with sync state tracking."""
    installations = manifest.get("installations", {})
    migrated = False

    for skill_name, targets in installations.items():
        # Old format: list of path strings
        # New format: dict of {path: {last_synced_hash: ...}}
        if isinstance(targets, list):
            new_targets = {}
            for path in targets:
                new_targets[path] = {"last_synced_hash": None}
            manifest["installations"][skill_name] = new_targets
            migrated = True

    if migrated:
        save_manifest(manifest)

    return manifest


def save_manifest(manifest: dict) -> None:
    """Save the manifest file."""
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def get_available_skills() -> list[dict]:
    """Get list of available skill directories from root and local/.

    Returns list of dicts with 'name', 'path', and 'scope' ("public" or "local").
    """
    skills = []

    # Scan root directory for public skills
    for item in SCRIPT_DIR.iterdir():
        if item.is_dir() and item.name not in SKILLS_IGNORE and not item.name.startswith("."):
            if (item / "SKILL.md").exists():
                skills.append({"name": item.name, "path": item, "scope": "public"})

    # Scan local/ directory for private skills
    if LOCAL_DIR.exists():
        for item in LOCAL_DIR.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                if (item / "SKILL.md").exists():
                    skills.append({"name": item.name, "path": item, "scope": "local"})

    return sorted(skills, key=lambda s: s["name"])


def find_skill_source(name: str) -> Path | None:
    """Find a skill's source directory, checking root first then local/.

    Returns the Path to the skill directory, or None if not found.
    """
    root_path = SCRIPT_DIR / name
    if root_path.is_dir() and (root_path / "SKILL.md").exists():
        return root_path

    local_path = LOCAL_DIR / name
    if local_path.is_dir() and (local_path / "SKILL.md").exists():
        return local_path

    return None


def hash_directory(path: Path) -> str:
    """Compute a hash of all files in a directory for comparison."""
    if not path.exists():
        return ""

    hasher = hashlib.md5()
    for file_path in sorted(path.rglob("*")):
        if file_path.is_file():
            rel_path = file_path.relative_to(path)
            hasher.update(str(rel_path).encode())
            hasher.update(file_path.read_bytes())
    return hasher.hexdigest()


def analyze_sync_state(source: Path, target: Path, last_synced_hash: str | None) -> dict:
    """
    Analyze the sync state between source and target.

    Returns a dict with:
    - source_hash: current hash of source
    - target_hash: current hash of target
    - source_changed: source differs from last sync
    - target_changed: target differs from last sync (local edits)
    - in_sync: source and target match
    - status: one of 'in_sync', 'target_behind', 'target_modified', 'both_changed', 'missing'
    """
    source_hash = hash_directory(source)
    target_hash = hash_directory(target) if target.exists() else ""

    result = {
        "source_hash": source_hash,
        "target_hash": target_hash,
        "source_exists": source.exists(),
        "target_exists": target.exists(),
        "in_sync": source_hash == target_hash and target.exists(),
    }

    if not target.exists():
        result["status"] = "missing"
        result["source_changed"] = True
        result["target_changed"] = False
    elif source_hash == target_hash:
        result["status"] = "in_sync"
        result["source_changed"] = False
        result["target_changed"] = False
    elif last_synced_hash is None:
        # Never synced before - treat as target behind (safe to sync)
        result["status"] = "target_behind"
        result["source_changed"] = True
        result["target_changed"] = False
    else:
        # We have a last_synced_hash to compare against
        source_changed = source_hash != last_synced_hash
        target_changed = target_hash != last_synced_hash

        result["source_changed"] = source_changed
        result["target_changed"] = target_changed

        if source_changed and not target_changed:
            result["status"] = "target_behind"
        elif target_changed and not source_changed:
            result["status"] = "target_modified"
        elif source_changed and target_changed:
            result["status"] = "both_changed"
        else:
            # Neither changed but they differ - shouldn't happen, but treat as target_behind
            result["status"] = "target_behind"

    return result


def copy_skill(source: Path, target: Path, state: dict, force: bool = False, dry_run: bool = False) -> dict:
    """Copy a skill from source to target."""
    result = {
        "action": None,
        "source": str(source),
        "target": str(target),
        "success": False,
        "message": "",
        "new_hash": state["source_hash"]
    }

    if not source.exists():
        result["action"] = "skip"
        result["message"] = f"Source does not exist: {source}"
        return result

    status = state["status"]

    if status == "in_sync":
        result["action"] = "skip"
        result["success"] = True
        result["message"] = "Already in sync"
        return result

    if status == "target_modified" and not force:
        result["action"] = "conflict"
        result["message"] = "Target has local edits. Use --force to overwrite."
        return result

    if status == "both_changed" and not force:
        result["action"] = "conflict"
        result["message"] = "Both source and target changed. Use --force to overwrite."
        return result

    # Safe to sync: missing or target_behind
    if dry_run:
        result["action"] = "would_copy"
        result["success"] = True
        if status == "missing":
            result["message"] = "Would copy (new installation)"
        else:
            result["message"] = "Would copy (target behind)"
        return result

    # Ensure parent directory exists
    target.parent.mkdir(parents=True, exist_ok=True)

    # Remove existing target if it exists
    if target.exists():
        shutil.rmtree(target)

    # Copy the skill
    shutil.copytree(source, target)

    result["action"] = "copied"
    result["success"] = True
    result["message"] = "Synced" if status == "target_behind" else "Installed"
    return result


def cmd_sync(args) -> int:
    """Sync skills to their target locations."""
    manifest = load_manifest()
    source_dir = Path(manifest["source_dir"])
    installations = manifest.get("installations", {})

    if not installations:
        print("No skills registered in manifest. Use 'add' to register skills.")
        return 1

    # Filter to specific skill if provided
    skills_to_sync = list(installations.keys())
    if args.skill:
        if args.skill not in installations:
            print(f"Skill '{args.skill}' not found in manifest.")
            print(f"Available: {', '.join(installations.keys())}")
            return 1
        skills_to_sync = [args.skill]

    total = 0
    synced = 0
    conflicts = 0
    skipped = 0
    manifest_changed = False

    for skill_name in skills_to_sync:
        targets = installations[skill_name]
        source = find_skill_source(skill_name)
        if source is None:
            source = source_dir / skill_name  # fallback for missing skills

        for target_base, meta in targets.items():
            target = Path(target_base) / skill_name
            last_synced_hash = meta.get("last_synced_hash")
            total += 1

            state = analyze_sync_state(source, target, last_synced_hash)
            result = copy_skill(source, target, state, force=args.force, dry_run=args.dry_run)

            # Format output
            status_icon = {
                "copied": "[SYNC]",
                "would_copy": "[WOULD SYNC]",
                "skip": "[OK]",
                "conflict": "[CONFLICT]"
            }.get(result["action"], "[?]")

            print(f"{status_icon} {skill_name} -> {target_base}")
            if result["action"] == "conflict":
                print(f"         {result['message']}")
                conflicts += 1
            elif result["action"] in ("copied", "would_copy"):
                synced += 1
                # Update last_synced_hash after successful copy
                if result["action"] == "copied":
                    manifest["installations"][skill_name][target_base]["last_synced_hash"] = result["new_hash"]
                    manifest_changed = True
            else:
                skipped += 1

    # Save manifest if we updated any hashes
    if manifest_changed:
        save_manifest(manifest)

    # Summary
    print()
    print(f"Total: {total} | Synced: {synced} | Skipped: {skipped} | Conflicts: {conflicts}")

    if conflicts > 0:
        print("\nResolve conflicts manually or use --force to overwrite.")
        return 1

    return 0


def cmd_status(args) -> int:
    """Show status of all skill installations."""
    manifest = load_manifest()
    source_dir = Path(manifest["source_dir"])
    installations = manifest.get("installations", {})
    available = get_available_skills()

    print(f"Source: {source_dir}")
    print(f"Available skills: {len(available)}")
    print()

    if not installations:
        print("No skills registered. Use 'add' to register skills.")
        print(f"\nAvailable skills to add: {', '.join(available)}")
        return 0

    for skill_name in sorted(installations.keys()):
        targets = installations[skill_name]
        source = find_skill_source(skill_name)
        if source is None:
            source = source_dir / skill_name  # fallback for missing skills
        source_exists = source.exists()

        print(f"{skill_name}:")
        if not source_exists:
            print(f"  [WARNING] Source missing!")

        for target_base, meta in targets.items():
            target = Path(target_base) / skill_name
            last_synced_hash = meta.get("last_synced_hash")
            state = analyze_sync_state(source, target, last_synced_hash)

            status_map = {
                "in_sync": "[OK]",
                "missing": "[MISSING]",
                "target_behind": "[BEHIND]",
                "target_modified": "[LOCAL EDITS]",
                "both_changed": "[CONFLICT]"
            }
            status = status_map.get(state["status"], "[?]")

            print(f"  {status} {target_base}")
        print()

    # Show unregistered skills
    registered = set(installations.keys())
    available_names = {s["name"] for s in available}
    unregistered = available_names - registered
    if unregistered:
        print(f"Unregistered skills: {', '.join(sorted(unregistered))}")

    return 0


def cmd_add(args) -> int:
    """Add a skill to a target location."""
    manifest = load_manifest()
    source_dir = Path(manifest["source_dir"])
    available = get_available_skills()

    skill_name = args.skill
    target_path = args.to

    # Validate skill exists
    available_names = [s["name"] for s in available]
    if skill_name not in available_names:
        print(f"Skill '{skill_name}' not found.")
        print(f"Available: {', '.join(available_names)}")
        return 1

    # Normalize target path
    target = Path(target_path).resolve()

    # Validate target is a skills directory
    if target.name != "skills" and not target.name.endswith((".claude", ".codex")):
        print(f"Warning: Target doesn't look like a skills directory: {target}")
        response = input("Continue anyway? [y/N] ")
        if response.lower() != "y":
            return 1

    # Add to manifest
    if "installations" not in manifest:
        manifest["installations"] = {}

    if skill_name not in manifest["installations"]:
        manifest["installations"][skill_name] = {}

    target_str = str(target)
    if target_str in manifest["installations"][skill_name]:
        print(f"Skill '{skill_name}' already registered for {target}")
        return 0

    # Add with null hash initially
    manifest["installations"][skill_name][target_str] = {"last_synced_hash": None}
    save_manifest(manifest)

    print(f"Added '{skill_name}' -> {target}")

    # Optionally sync immediately
    if not args.no_sync:
        print("Syncing...")
        source = find_skill_source(skill_name)
        target_full = target / skill_name
        state = analyze_sync_state(source, target_full, None)
        result = copy_skill(source, target_full, state, force=False, dry_run=False)
        print(f"  {result['message']}")

        # Update hash after sync
        if result["action"] == "copied":
            manifest["installations"][skill_name][target_str]["last_synced_hash"] = result["new_hash"]
            save_manifest(manifest)

    return 0


def cmd_remove(args) -> int:
    """Remove a skill from a target location."""
    manifest = load_manifest()

    skill_name = args.skill
    target_path = args.from_path

    if skill_name not in manifest.get("installations", {}):
        print(f"Skill '{skill_name}' not in manifest.")
        return 1

    # Normalize target path
    target = Path(target_path).resolve()
    target_str = str(target)

    if target_str not in manifest["installations"][skill_name]:
        print(f"Skill '{skill_name}' not registered for {target}")
        return 1

    del manifest["installations"][skill_name][target_str]

    # Clean up empty dicts
    if not manifest["installations"][skill_name]:
        del manifest["installations"][skill_name]

    save_manifest(manifest)
    print(f"Removed '{skill_name}' from {target}")

    # Optionally delete the files
    if args.delete:
        target_full = target / skill_name
        if target_full.exists():
            shutil.rmtree(target_full)
            print(f"Deleted {target_full}")

    return 0


def cmd_discover(args) -> int:
    """Discover .claude/skills and .codex/skills directories in a path."""
    search_path = Path(args.path).resolve()

    if not search_path.exists():
        print(f"Path does not exist: {search_path}")
        return 1

    print(f"Searching for skill directories in: {search_path}")
    print()

    found = []
    for pattern in (".claude/skills", ".codex/skills"):
        for skills_dir in search_path.rglob(pattern):
            if skills_dir.is_dir():
                # Skip if inside node_modules, venv, etc.
                parts = skills_dir.parts
                if any(p in ("node_modules", "venv", ".venv", "__pycache__") for p in parts):
                    continue

                found.append(skills_dir)

                # List skills in this directory
                skills = [d.name for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").exists()]

                print(f"{skills_dir}")
                if skills:
                    print(f"  Skills: {', '.join(skills)}")
                else:
                    print(f"  (empty)")
                print()

    print(f"Found {len(found)} skill directories.")
    return 0


def cmd_list(args) -> int:
    """List available skills in the source directory."""
    available = get_available_skills()
    manifest = load_manifest()
    installations = manifest.get("installations", {})

    print("Available skills:")
    print()

    for skill in available:
        name = skill["name"]
        scope_tag = " [local]" if skill["scope"] == "local" else ""
        targets = installations.get(name, {})
        count = len(targets)
        status = f"({count} installation{'s' if count != 1 else ''})" if targets else "(not registered)"
        print(f"  {name}{scope_tag} {status}")

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Skill Sync - Manage Claude Code and Codex CLI skills across projects",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python skill-sync.py sync                    # Sync all skills
  python skill-sync.py sync clockify-api       # Sync specific skill
  python skill-sync.py sync --dry-run          # Preview changes
  python skill-sync.py sync --force            # Overwrite local edits
  python skill-sync.py status                  # Show sync status
  python skill-sync.py add clockify-api --to "C:\\path\\to\\.claude\\skills"
  python skill-sync.py add clockify-api --to "C:\\path\\to\\.codex\\skills"
  python skill-sync.py remove clockify-api --from "C:\\path\\to\\.claude\\skills"
  python skill-sync.py discover "C:\\path\\to\\projects"
  python skill-sync.py list                    # List available skills

Status indicators:
  [OK]          Target matches source
  [BEHIND]      Source updated, target needs sync (safe)
  [LOCAL EDITS] Target was modified locally (needs --force)
  [CONFLICT]    Both source and target changed (needs --force)
  [MISSING]     Target doesn't exist yet
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # sync command
    sync_parser = subparsers.add_parser("sync", help="Sync skills to target locations")
    sync_parser.add_argument("skill", nargs="?", help="Specific skill to sync (optional)")
    sync_parser.add_argument("--dry-run", "-n", action="store_true", help="Show what would be done")
    sync_parser.add_argument("--force", "-f", action="store_true", help="Overwrite even if target has local edits")
    sync_parser.set_defaults(func=cmd_sync)

    # status command
    status_parser = subparsers.add_parser("status", help="Show status of all installations")
    status_parser.set_defaults(func=cmd_status)

    # add command
    add_parser = subparsers.add_parser("add", help="Add a skill to a target location")
    add_parser.add_argument("skill", help="Name of the skill to add")
    add_parser.add_argument("--to", required=True, help="Target skills directory path")
    add_parser.add_argument("--no-sync", action="store_true", help="Don't sync immediately after adding")
    add_parser.set_defaults(func=cmd_add)

    # remove command
    remove_parser = subparsers.add_parser("remove", help="Remove a skill from a target location")
    remove_parser.add_argument("skill", help="Name of the skill to remove")
    remove_parser.add_argument("--from", dest="from_path", required=True, help="Target skills directory path")
    remove_parser.add_argument("--delete", action="store_true", help="Also delete the skill files from target")
    remove_parser.set_defaults(func=cmd_remove)

    # discover command
    discover_parser = subparsers.add_parser("discover", help="Find .claude/skills and .codex/skills directories")
    discover_parser.add_argument("path", nargs="?", default=".", help="Path to search")
    discover_parser.set_defaults(func=cmd_discover)

    # list command
    list_parser = subparsers.add_parser("list", help="List available skills")
    list_parser.set_defaults(func=cmd_list)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
