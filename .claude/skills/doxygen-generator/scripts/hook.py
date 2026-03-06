#!/usr/bin/env python3
"""Git hook manager for auto-regenerating Doxygen documentation.

Usage:
    python3 hook.py <workspace> install [options]
    python3 hook.py <workspace> remove  [options]
    python3 hook.py <workspace> status  [options]
    python3 hook.py <workspace> run     [options]
"""

import argparse
import os
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MARKER_BEGIN = "# BEGIN doxygen-generator-skill-hook"
MARKER_END = "# END doxygen-generator-skill-hook"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Manage git hooks for doxygen-generator.")
    parser.add_argument("workspace", help="Path to the workspace/project root.")
    parser.add_argument("command", choices=["install", "remove", "status", "run"],
                        help="Hook management command.")
    parser.add_argument("--hook-type", default="pre-push",
                        help="Git hook type (default: pre-push).")
    parser.add_argument("--skill-path", default=None,
                        help="Override path to skill root (auto-detected if omitted).")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def get_hook_path(workspace: Path, hook_type: str) -> Path:
    """Return the path to the git hook file."""
    git_dir = workspace / ".git"
    if not git_dir.is_dir():
        # Support git worktrees: .git may be a file pointing elsewhere
        if git_dir.is_file():
            content = git_dir.read_text().strip()
            if content.startswith("gitdir:"):
                git_dir = Path(content.split(":", 1)[1].strip())
                if not git_dir.is_absolute():
                    git_dir = (workspace / git_dir).resolve()
    return git_dir / "hooks" / hook_type


def generate_hook_block(skill_path: str, workspace: str) -> str:
    """Generate the hook script block to insert."""
    generate_script = Path(skill_path) / "scripts" / "generate.py"
    return f"""{MARKER_BEGIN}
# Auto-regenerate Doxygen documentation on push
python3 "{generate_script}" "{workspace}" --force &
{MARKER_END}"""


def install_hook(hook_path: Path, skill_path: str, workspace: str, verbose: bool) -> None:
    """Install the hook by appending our block to the hook file."""
    hook_path.parent.mkdir(parents=True, exist_ok=True)

    block = generate_hook_block(skill_path, workspace)

    if hook_path.exists():
        content = hook_path.read_text()

        # Check if already installed
        if MARKER_BEGIN in content:
            print(f"Hook already installed in {hook_path}")
            if verbose:
                print("Use 'remove' then 'install' to update.")
            return

        # Append to existing hook
        if not content.endswith("\n"):
            content += "\n"
        content += "\n" + block + "\n"
    else:
        content = f"#!/bin/sh\n\n{block}\n"

    hook_path.write_text(content)

    # Make executable
    current = hook_path.stat().st_mode
    hook_path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f"Hook installed: {hook_path}")


def remove_hook(hook_path: Path, verbose: bool) -> None:
    """Remove our block from the hook file."""
    if not hook_path.exists():
        print(f"Hook file does not exist: {hook_path}")
        return

    content = hook_path.read_text()
    if MARKER_BEGIN not in content:
        print("Hook marker not found. Nothing to remove.")
        return

    lines = content.split("\n")
    new_lines = []
    inside_block = False

    for line in lines:
        if line.strip() == MARKER_BEGIN:
            inside_block = True
            continue
        if line.strip() == MARKER_END:
            inside_block = False
            continue
        if not inside_block:
            new_lines.append(line)

    # Clean up extra blank lines left behind
    cleaned = "\n".join(new_lines).strip()
    if cleaned and cleaned != "#!/bin/sh":
        hook_path.write_text(cleaned + "\n")
    else:
        # Only shebang left — remove the file
        hook_path.unlink()
        if verbose:
            print("Hook file removed (was empty after cleanup).")

    print(f"Hook removed from: {hook_path}")


def check_status(hook_path: Path) -> None:
    """Check if our hook markers are present."""
    if not hook_path.exists():
        print(f"Hook file does not exist: {hook_path}")
        print("Status: NOT installed")
        return

    content = hook_path.read_text()
    if MARKER_BEGIN in content and MARKER_END in content:
        print(f"Hook file: {hook_path}")
        print("Status: INSTALLED")
    else:
        print(f"Hook file: {hook_path}")
        print("Status: NOT installed")


def run_generate(workspace: Path, skill_path: str, verbose: bool) -> None:
    """Manually trigger documentation generation (same as the hook would)."""
    generate_script = Path(skill_path) / "scripts" / "generate.py"
    cmd = [sys.executable, str(generate_script), str(workspace), "--force"]
    if verbose:
        cmd.append("-v")
        print(f"Running: {' '.join(cmd)}")

    result = subprocess.run(cmd)
    sys.exit(result.returncode)


def main(argv=None):
    args = parse_args(argv)
    workspace = Path(args.workspace).resolve()

    if not workspace.is_dir():
        print(f"Error: Workspace not found: {workspace}", file=sys.stderr)
        sys.exit(1)

    import platform as plat_mod
    skill_path = args.skill_path or str(plat_mod.get_skill_root())

    hook_path = get_hook_path(workspace, args.hook_type)

    if args.command == "install":
        install_hook(hook_path, skill_path, str(workspace), args.verbose)
    elif args.command == "remove":
        remove_hook(hook_path, args.verbose)
    elif args.command == "status":
        check_status(hook_path)
    elif args.command == "run":
        run_generate(workspace, skill_path, args.verbose)


if __name__ == "__main__":
    main()
