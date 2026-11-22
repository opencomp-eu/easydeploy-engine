"""Nightly unattended updates: settings, the systemd timer, and run helpers.

The timer runs ``update.sh --unattended``. That run
  * takes the same flock as OpenComp platform jobs (``~/.easydeploy-engine.lock``),
  * waits for the engine backup if it is still running,
  * keeps a pinned (detached) engine commit where it is,
  * writes ``.easydeploy-engine/auto-update.json`` with the outcome.
"""

from __future__ import annotations

import fcntl
import json
import os
import pwd
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from scripts.systemd_units import install_timer, remove_timer, should_activate, systemd_available, systemd_unit_dir

UNIT_NAME = "easydeploy-update"
DEFAULT_CALENDAR = "*-*-* 04:00:00"
DEFAULT_RANDOMIZED_DELAY = "30min"
LOCK_WAIT_SECONDS = 60 * 60
BACKUP_WAIT_SECONDS = 2 * 60 * 60
STATUS_FILENAME = "auto-update.json"
# A run that cannot get the lock is not a failure; the next night tries again.
EXIT_SKIPPED = 75

_DELAY = re.compile(r"^\d+\s*(ms|s|sec|second|seconds|m|min|minute|minutes|h|hr|hour|hours)?$")


class LockBusy(RuntimeError):
    pass


def auto_update_settings(config: dict) -> dict[str, Any]:
    """``auto_update`` from engine.yaml. Absent means on, at the defaults."""
    raw = config.get("auto_update")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("auto_update must be a mapping")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("auto_update.enabled must be true or false")
    calendar = str(raw.get("calendar") or DEFAULT_CALENDAR).strip()
    if not calendar or "\n" in calendar:
        raise ValueError("auto_update.calendar must be one systemd OnCalendar expression")
    delay = str(raw.get("randomized_delay", DEFAULT_RANDOMIZED_DELAY)).strip()
    if not _DELAY.match(delay):
        raise ValueError(
            "auto_update.randomized_delay must be a duration such as 30min, 2h or 0"
        )
    return {"enabled": enabled, "calendar": calendar, "randomized_delay": delay}


def _owner(project_root: Path) -> pwd.struct_passwd:
    return pwd.getpwuid(project_root.stat().st_uid)


def render_service(project_root: Path, owner: pwd.struct_passwd | None = None) -> str:
    """The update runs as the user who owns the checkout, like platform jobs do.

    Running as root would leave root-owned files in a non-root checkout.
    """
    owner = owner or _owner(project_root)
    path = f"{owner.pw_dir}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    lines = [
        "[Unit]",
        "Description=Update Easy Deploy (engine, kits, and container images)",
        "Wants=network-online.target docker.service",
        "After=network-online.target docker.service",
        "",
        "[Service]",
        "Type=oneshot",
    ]
    if owner.pw_uid != 0:
        lines.append(f"User={owner.pw_name}")
    lines += [
        f"WorkingDirectory={project_root}",
        f"Environment=HOME={owner.pw_dir}",
        f"Environment=PATH={path}",
        f"ExecStart=/usr/bin/env bash {project_root / 'update.sh'} --unattended",
        # Lock wait + backup wait + the update itself.
        "TimeoutStartSec=5h",
        "SuccessExitStatus=75",
        "Nice=5",
        "",
    ]
    return "\n".join(lines)


def render_timer(calendar: str, randomized_delay: str) -> str:
    return "\n".join(
        [
            "[Unit]",
            "Description=Update Easy Deploy every night",
            "",
            "[Timer]",
            f"OnCalendar={calendar}",
            f"RandomizedDelaySec={randomized_delay}",
            "Persistent=true",
            f"Unit={UNIT_NAME}.service",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )


def reconcile_update_schedule(
    project_root: Path,
    settings: dict[str, Any],
    *,
    unit_dir: Path | None = None,
    activate: bool | None = None,
    owner: pwd.struct_passwd | None = None,
) -> str:
    unit_dir = unit_dir or systemd_unit_dir()
    if activate is None:
        activate = should_activate(unit_dir)

    if settings["enabled"]:
        outcome = install_timer(
            UNIT_NAME,
            render_service(project_root, owner),
            render_timer(settings["calendar"], settings["randomized_delay"]),
            unit_dir=unit_dir,
            activate=activate,
        )
        return {
            "written": "Auto-update timer unit files written.",
            "no-systemd": "Auto-update timer not started: systemd is not available. "
            "Run bash update.sh --unattended from cron.",
            "enable-failed": "Auto-update timer unit files written; enable failed.",
        }.get(outcome, f"Auto-update timer enabled ({settings['calendar']}).")

    if not remove_timer(UNIT_NAME, unit_dir=unit_dir, activate=activate):
        return "Auto-update timer not configured."
    return "Auto-update timer removed (auto_update.enabled: false)."


def engine_lock_path() -> Path:
    """The lock OpenComp platform jobs hold while they run an engine script."""
    override = os.environ.get("EASYDEPLOY_ENGINE_LOCK", "").strip()
    return Path(override) if override else Path.home() / ".easydeploy-engine.lock"


@contextmanager
def engine_run_lock(
    path: Path | None = None,
    *,
    wait_seconds: int = LOCK_WAIT_SECONDS,
    poll_seconds: float = 10.0,
) -> Iterator[None]:
    path = path or engine_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        deadline = time.monotonic() + wait_seconds
        announced = False
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockBusy(
                        f"Another engine run still holds {path}; skipping this update."
                    ) from None
                if not announced:
                    print("Waiting for an earlier engine run on this server to finish.", flush=True)
                    announced = True
                time.sleep(poll_seconds)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _unit_active(unit: str) -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", unit], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() in {"active", "activating", "reloading"}


def wait_for_backup(
    backup_unit: str | None,
    *,
    wait_seconds: int = BACKUP_WAIT_SECONDS,
    poll_seconds: float = 30.0,
    is_active=_unit_active,
) -> None:
    """Do not restart containers while the nightly backup reads their data."""
    if not backup_unit or not systemd_available():
        return
    unit = f"{backup_unit}.service"
    deadline = time.monotonic() + wait_seconds
    announced = False
    while is_active(unit):
        if time.monotonic() >= deadline:
            raise RuntimeError(f"{unit} is still running after {wait_seconds // 60} minutes.")
        if not announced:
            print(f"Waiting for {unit} to finish before updating.", flush=True)
            announced = True
        time.sleep(poll_seconds)


def detached_engine_commit(project_root: Path) -> str | None:
    """HEAD when the engine is checked out at a commit rather than a branch.

    OpenComp can pin every server to one engine commit. An unattended update
    must not move such a checkout to the tip of master.
    """
    if not (project_root / ".git").exists():
        return None
    branch = subprocess.run(
        ["git", "-C", str(project_root), "symbolic-ref", "-q", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if branch.returncode == 0:
        return None
    head = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    commit = head.stdout.strip()
    return commit if head.returncode == 0 and commit else None


def prune_dangling_images() -> None:
    """Drop the untagged images each pull leaves behind, so the disk does not fill."""
    try:
        result = subprocess.run(
            ["docker", "image", "prune", "-f"], capture_output=True, text=True, check=False
        )
    except OSError:
        return
    if result.returncode != 0:
        print(f"Warning: docker image prune failed: {result.stderr.strip()}", file=sys.stderr)
    elif result.stdout.strip():
        print(result.stdout.strip())


def write_status(state_dir: Path, *, started: datetime, outcome: str, detail: str = "") -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / STATUS_FILENAME
    payload = {
        "outcome": outcome,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "detail": detail[-4000:],
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)
    return path
