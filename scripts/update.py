#!/usr/bin/env python3
"""Pull engine + kit repos, sync pinned image tags, then apply."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "easydeploy-lib" / "python"))

from scripts.apply import ApplyResult, apply_engine, ensure_enabled_kits, load_engine, validate_engine  # noqa: E402
from scripts.config_edit import (  # noqa: E402
    catalog_by_name,
    checkout_origin_branch,
    normalize_branch,
    normalize_commit,
    prepare_origin_branch,
    set_engine_kit_branch,
)
from scripts.oidc_wire import resolve_kit_path  # noqa: E402
from scripts.progress import (  # noqa: E402
    NAME_WIDTH,
    GitSync,
    UpdateFailed,
    diff_image_ids,
    git_dirty_paths,
    git_failure_message,
    git_short_head,
    snapshot_image_ids,
)
from scripts.version_sync import sync_image_tags  # noqa: E402
from update_lock import NOTHING_TO_UPDATE  # noqa: E402


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_git(project_root: Path, *args: str, echo: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(project_root), *args],
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    if echo:
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git command failed").strip()
        raise UpdateFailed(git_failure_message("easydeploy-engine", detail))
    return result


_OPERATOR_BASENAMES = frozenset({"engine.yaml", "deploy.yaml", "secrets.yaml"})


def _operator_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in _OPERATOR_BASENAMES or path == "kits" or path.startswith("kits/")


def changes_blocking_switch(repo: Path) -> tuple[str, ...]:
    """Tracked local edits that a branch switch would risk overwriting.

    Operator config (engine.yaml, kit deploy.yaml, kits/*.yaml) stays put.
    Untracked files are ignored.
    """
    if not (repo / ".git").exists():
        return ()
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git status failed").strip()
        raise UpdateFailed(git_failure_message(repo.name, detail))
    blocked: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        target = line[3:].strip().strip('"').split(" -> ")[-1].strip().strip('"')
        if target and not _operator_path(target):
            blocked.append(target)
    return tuple(blocked)


def _remote_has_branch(repo_url: str, branch: str) -> bool:
    result = subprocess.run(
        ["git", "ls-remote", "--heads", repo_url, branch],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git ls-remote failed").strip()
        raise UpdateFailed(f"Could not list branches of {repo_url}:\n{detail}")
    return bool(result.stdout.strip())


def switch_deployment_branch(project_root: Path, branch: str) -> None:
    """Check the branch out on the engine and remember it as engine.kit_branch.

    Enabled kit checkouts are validated here (the branch exists, and switching
    would not discard local commits or tracked edits). The following update
    syncs those kits onto the branch.
    """
    branch = normalize_branch(branch)
    if not (project_root / ".git").exists():
        raise UpdateFailed("easydeploy-engine is not a git checkout, so it cannot switch branches.")

    config = load_engine(project_root / "engine.yaml")
    enabled = validate_engine(config)

    blocked = changes_blocking_switch(project_root)
    if blocked:
        shown = ", ".join(blocked[:8])
        extra = f", +{len(blocked) - 8} more" if len(blocked) > 8 else ""
        raise UpdateFailed(
            f"easydeploy-engine has local changes ({shown}{extra}). "
            "Commit or stash them before switching branches."
        )

    kit_checkouts: list[Path] = []
    try:
        prepare_origin_branch(project_root, branch)
    except RuntimeError as exc:
        raise UpdateFailed(str(exc)) from exc
    for service in enabled:
        dest = resolve_kit_path(service, project_root)
        if dest.exists() and (dest / ".git").exists():
            kit_blocked = changes_blocking_switch(dest)
            if kit_blocked:
                shown = ", ".join(kit_blocked[:8])
                extra = f", +{len(kit_blocked) - 8} more" if len(kit_blocked) > 8 else ""
                raise UpdateFailed(
                    f"{service['name']} has local changes ({shown}{extra}). "
                    "Commit or stash them before switching branches."
                )
            try:
                prepare_origin_branch(dest, branch)
            except RuntimeError as exc:
                raise UpdateFailed(str(exc)) from exc
            kit_checkouts.append(dest)
            continue
        repo_url = str(catalog_by_name(service["name"])["repo"])
        if not _remote_has_branch(repo_url, branch):
            raise UpdateFailed(
                f"{service['name']} has no branch {branch} on origin. "
                "Push it on that kit before checking it out here."
            )

    print(f"Switching easydeploy-engine and enabled kits to {branch}", flush=True)
    checkout_origin_branch(project_root, branch)
    for dest in kit_checkouts:
        checkout_origin_branch(dest, branch)
    set_engine_kit_branch(project_root / "engine.yaml", branch)
    _run_git(
        project_root,
        "submodule",
        "update",
        "--init",
        "--recursive",
        "--quiet",
    )


def _reexec_update_argv(args: argparse.Namespace) -> list[str]:
    forwarded = [sys.executable, "-m", "scripts.update"]
    if args.verbose:
        forwarded.append("--verbose")
    if args.skip_tags:
        forwarded.append("--skip-tags")
    if args.skip_pull:
        forwarded.append("--skip-pull")
    if args.skip_runtime:
        forwarded.append("--skip-runtime")
    if args.force:
        forwarded.append("--force")
    return forwarded


def pull_engine_repo(project_root: Path = PROJECT_ROOT, *, verbose: bool = False) -> GitSync:
    """Fast-forward the engine checkout and refresh submodules."""
    if not (project_root / ".git").is_dir():
        if verbose:
            print("Engine is not a git checkout; skipping git pull.")
        return GitSync(name="engine", status="skipped")
    old_sha = git_short_head(project_root)
    pinned = os.environ.get("EASYDEPLOY_ENGINE_COMMIT", "").strip()
    if pinned:
        commit = normalize_commit(pinned)
        if verbose:
            print(f"Checking out pinned easydeploy-engine {commit}…")
        has_commit = subprocess.run(
            ["git", "-C", str(project_root), "cat-file", "-e", f"{commit}^{{commit}}"],
            capture_output=True,
            env=_git_env(),
        )
        if has_commit.returncode != 0:
            _run_git(project_root, "fetch", "-q", "origin", commit, echo=verbose)
        _run_git(project_root, "checkout", "-q", "--detach", commit, echo=verbose)
    else:
        if verbose:
            print("Pulling easydeploy-engine…")
        _run_git(project_root, "pull", "--ff-only", echo=verbose)
    _run_git(
        project_root,
        "submodule",
        "update",
        "--init",
        "--recursive",
        *(["--quiet"] if not verbose else []),
        echo=verbose,
    )
    new_sha = git_short_head(project_root)
    dirty = git_dirty_paths(project_root)
    status = "updated" if old_sha and new_sha and old_sha != new_sha else "already up to date"
    return GitSync(name="engine", status=status, old_sha=old_sha, new_sha=new_sha, dirty=dirty)


def sync_kit_repos(enabled: list[dict], *, verbose: bool = False) -> list[GitSync]:
    before: dict[str, str] = {}
    for service in enabled:
        dest = resolve_kit_path(service, PROJECT_ROOT)
        if dest.exists():
            before[service["name"]] = git_short_head(dest)
    kit_results = ensure_enabled_kits(
        enabled, project_root=PROJECT_ROOT, sync=True, verbose=verbose
    )
    results: list[GitSync] = []
    for name, status in kit_results:
        service = next(item for item in enabled if item["name"] == name)
        dest = resolve_kit_path(service, PROJECT_ROOT)
        results.append(
            GitSync(
                name=name,
                status=status,
                old_sha=before.get(name, ""),
                new_sha=git_short_head(dest) if dest.exists() else "",
                dirty=git_dirty_paths(dest) if dest.exists() else (),
            )
        )
    return results


def _print_git_section(entries: list[GitSync]) -> None:
    print("Git")
    if not entries:
        print("  skipped")
        return
    for entry in entries:
        print(entry.line())


def _print_docker_section(changed: list[str], *, skipped: bool) -> None:
    print("Docker")
    if skipped:
        print("  skipped (--skip-pull)")
        return
    if changed:
        print(f"  {len(changed)} image(s) updated:")
        for ref in changed:
            print(f"    {ref}")
        return
    print("  all images already up to date")


def _print_apply_section(result: ApplyResult) -> None:
    print("Applying")
    if not result.kits:
        print("  skipped")
    for kit in result.kits:
        print(f"  {kit.name:<{NAME_WIDTH}} {kit.status}")
    print(f"  {'caddy':<{NAME_WIDTH}} {result.caddy}")


def _kit_update_args(*, verbose: bool, force: bool, skip_pull: bool) -> list[str]:
    args = ["--skip-git"]
    if verbose:
        args.append("--verbose")
    if force:
        args.append("--force")
    if skip_pull:
        args.append("--skip-pull")
    return args


def update_stack(
    *,
    skip_git: bool = False,
    skip_tags: bool = False,
    skip_pull: bool = False,
    skip_runtime: bool = False,
    verbose: bool = False,
    force: bool = False,
) -> None:
    if verbose:
        os.environ["EASYDEPLOY_VERBOSE"] = "1"
        os.environ.pop("EASYDEPLOY_QUIET", None)
    else:
        os.environ["EASYDEPLOY_QUIET"] = "1"
        os.environ["COMPOSE_PROGRESS"] = "quiet"
        os.environ["DOCKER_CLI_HINTS"] = "false"
        os.environ.pop("EASYDEPLOY_VERBOSE", None)

    git_entries: list[GitSync] = []
    if not skip_git:
        git_entries.append(pull_engine_repo(verbose=verbose))

    config = load_engine()
    enabled = validate_engine(config)

    if not skip_git:
        git_entries.extend(sync_kit_repos(enabled, verbose=verbose))

    tag_notes: list[str] = []
    if not skip_tags:
        tag_notes = sync_image_tags(enabled, PROJECT_ROOT)

    if verbose and not skip_tags:
        if tag_notes:
            print("Synced image tags from kit deploy.yaml.example:")
            for line in tag_notes:
                print(f"  {line}")
        else:
            print("Image tags already match kit deploy.yaml.example (or no tags to sync).")

    before_images = snapshot_image_ids() if not verbose else {}
    result = apply_engine(
        skip_runtime=skip_runtime,
        skip_pull=skip_pull,
        sync_kits=False,
        verbose=verbose,
        kit_script="update.sh",
        kit_args=_kit_update_args(verbose=verbose, force=force, skip_pull=skip_pull),
        force=force,
    )
    if verbose:
        return
    if result.all_noop:
        print(NOTHING_TO_UPDATE)
        return

    print("Updating Easy Deploy", flush=True)
    print()
    _print_git_section(git_entries)
    print()
    if not skip_tags:
        print("Image pins")
        if tag_notes:
            for line in tag_notes:
                print(f"  {line}")
        else:
            print("  unchanged")
        print()
    _print_apply_section(result)
    after_images = snapshot_image_ids()
    changed = diff_image_ids(before_images, after_images)
    print()
    _print_docker_section(changed, skipped=skip_pull)
    print()
    print("Update complete.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update easydeploy-engine: git pull, sync kit tags, pull images, apply",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show full git, Docker Compose, and kit apply output",
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore update locks and always apply kits and Caddy",
    )
    parser.add_argument(
        "--branch",
        metavar="NAME",
        help=(
            "Check out this branch on the engine and every enabled kit, "
            "store it as engine.kit_branch, then update"
        ),
    )
    args = parser.parse_args()
    if args.branch and args.skip_git:
        print("Error: --branch cannot be combined with --skip-git", file=sys.stderr)
        raise SystemExit(2)
    try:
        if args.branch:
            switch_deployment_branch(PROJECT_ROOT, args.branch)
            os.execv(sys.executable, _reexec_update_argv(args))
        update_stack(
            skip_git=args.skip_git,
            skip_tags=args.skip_tags,
            skip_pull=args.skip_pull,
            skip_runtime=args.skip_runtime,
            verbose=args.verbose,
            force=args.force,
        )
    except UpdateFailed as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    except (FileNotFoundError, ValueError, RuntimeError, PermissionError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
