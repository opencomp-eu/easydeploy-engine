"""Condensed progress reporting for engine update/apply."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess

LOG_TAIL_LINES = 40
NAME_WIDTH = 10


class UpdateFailed(RuntimeError):
    """User-facing update failure. ``str(exc)`` is the full message."""


@dataclass(frozen=True)
class GitSync:
    name: str
    status: str
    old_sha: str = ""
    new_sha: str = ""
    dirty: tuple[str, ...] = field(default_factory=tuple)

    def line(self) -> str:
        if self.status == "skipped":
            return f"  {self.name:<{NAME_WIDTH}} skipped (not a git checkout)"
        extra = ""
        if self.status == "updated" and self.old_sha and self.new_sha and self.old_sha != self.new_sha:
            extra = f" ({self.old_sha} → {self.new_sha})"
        elif self.status == "cloned" and self.new_sha:
            extra = f" ({self.new_sha})"
        if self.dirty:
            shown = ", ".join(self.dirty[:3])
            if len(self.dirty) > 3:
                shown += f", +{len(self.dirty) - 3} more"
            extra += f"  local changes: {shown}"
        return f"  {self.name:<{NAME_WIDTH}} {self.status}{extra}"


def git_short_head(path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def git_dirty_paths(path: Path) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ()
    files: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        path_part = line[3:] if len(line) > 3 else line.strip()
        files.append(path_part.strip())
    return tuple(files)


def snapshot_image_ids() -> dict[str, str]:
    """Return repo:tag → image ID for images currently used by containers."""
    listed = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Image}}\t{{.ImageID}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if listed.returncode != 0:
        return {}
    mapping: dict[str, str] = {}
    for line in listed.stdout.splitlines():
        if "\t" not in line:
            continue
        ref, image_id = line.split("\t", 1)
        ref, image_id = ref.strip(), image_id.strip()
        if not ref or not image_id or ref.startswith("<none>") or ref.endswith(":<none>"):
            continue
        mapping[ref] = image_id
    return mapping


def diff_image_ids(before: dict[str, str], after: dict[str, str]) -> list[str]:
    changed: list[str] = []
    for ref, image_id in after.items():
        previous = before.get(ref)
        if previous != image_id:
            changed.append(ref)
    return sorted(changed)


def kit_child_env(base: dict[str, str], *, verbose: bool) -> dict[str, str]:
    env = dict(base)
    if verbose:
        env["EASYDEPLOY_VERBOSE"] = "1"
        env.pop("EASYDEPLOY_QUIET", None)
        env.pop("COMPOSE_PROGRESS", None)
    else:
        env["EASYDEPLOY_QUIET"] = "1"
        env["COMPOSE_PROGRESS"] = "quiet"
        env["DOCKER_CLI_HINTS"] = "false"
        env.pop("EASYDEPLOY_VERBOSE", None)
    return env


def combined_output(result: subprocess.CompletedProcess[str]) -> str:
    parts = []
    if result.stdout:
        parts.append(result.stdout)
    if result.stderr:
        parts.append(result.stderr)
    return "".join(parts)


def tail_lines(text: str, count: int = LOG_TAIL_LINES) -> str:
    lines = text.strip().splitlines()
    if not lines:
        return "(no output)"
    return "\n".join(lines[-count:])


def kit_apply_failure_message(
    name: str,
    output: str,
    *,
    log_path: Path | None = None,
    verbose: bool = False,
) -> str:
    if verbose:
        return (
            f"{name} failed to apply. See the output above.\n"
            "Re-run with: bash update.sh --verbose"
        )
    body = [
        f"{name} failed to apply.",
        "",
        "Last output:",
        tail_lines(output),
        "",
    ]
    if log_path is not None:
        body.append(f"Full log: {log_path}")
    body.append("Re-run with more detail: bash update.sh --verbose")
    return "\n".join(body)


def git_failure_message(name: str, detail: str) -> str:
    return (
        f"Could not update {name} from git.\n"
        f"{detail.strip()}\n\n"
        "If you have local commits or edits, inspect with git status, then re-run:\n"
        "  bash update.sh --verbose"
    )
