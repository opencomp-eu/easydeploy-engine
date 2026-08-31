#!/usr/bin/env python3
"""Pull engine + kit repos, sync pinned image tags, then apply."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from scripts.apply import apply_engine, load_engine, validate_engine
from scripts.config_edit import ensure_enabled_kits

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "easydeploy-lib" / "python"))

from scripts.version_sync import sync_image_tags  # noqa: E402


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def pull_engine_repo(project_root: Path = PROJECT_ROOT) -> bool:
    """Fast-forward the engine checkout and refresh submodules."""
    if not (project_root / ".git").is_dir():
        print("Engine is not a git checkout; skipping git pull.")
        return False
    print("Pulling easydeploy-engine…")
    subprocess.run(
        ["git", "-C", str(project_root), "pull", "--ff-only"],
        check=True,
        env=_git_env(),
    )
    subprocess.run(
        ["git", "-C", str(project_root), "submodule", "update", "--init", "--recursive"],
        check=True,
        env=_git_env(),
    )
    return True


def update_stack(
    *,
    skip_git: bool = False,
    skip_tags: bool = False,
    skip_pull: bool = False,
    skip_runtime: bool = False,
) -> None:
    if not skip_git:
        pull_engine_repo()

    config = load_engine()
    enabled = validate_engine(config)

    if not skip_git:
        ensure_enabled_kits(enabled, project_root=PROJECT_ROOT, sync=True)

    if not skip_tags:
        tag_notes = sync_image_tags(enabled, PROJECT_ROOT)
        if tag_notes:
            print("Synced image tags from kit deploy.yaml.example:")
            for line in tag_notes:
                print(f"  {line}")
        else:
            print("Image tags already match kit deploy.yaml.example (or no tags to sync).")

    apply_engine(
        skip_runtime=skip_runtime,
        skip_pull=skip_pull,
        sync_kits=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update easydeploy-engine: git pull, sync kit tags, pull images, apply",
    )
    parser.add_argument(
        "--skip-git",
        action="store_true",
        help="Do not git pull the engine or sync kit checkouts",
    )
    parser.add_argument(
        "--skip-tags",
        action="store_true",
        help="Do not merge tag/tools_tag from kit deploy.yaml.example into operator YAML",
    )
    parser.add_argument("--skip-pull", action="store_true", help="Skip docker compose pull")
    parser.add_argument(
        "--skip-runtime",
        action="store_true",
        help="Render config only (no docker compose up)",
    )
    args = parser.parse_args()
    try:
        update_stack(
            skip_git=args.skip_git,
            skip_tags=args.skip_tags,
            skip_pull=args.skip_pull,
            skip_runtime=args.skip_runtime,
        )
    except (FileNotFoundError, ValueError, RuntimeError, PermissionError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
