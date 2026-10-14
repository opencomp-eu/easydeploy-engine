"""Install or remove the audit ingest timer."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


UNIT_NAME = "easydeploy-audit"


def systemd_unit_dir() -> Path:
    return Path(os.environ.get("EASYDEPLOY_SYSTEMD_UNIT_DIR", "/etc/systemd/system"))


def render_service(project_root: Path) -> str:
    script = project_root / "audit.sh"
    return "\n".join(
        [
            "[Unit]",
            "Description=Collect Easy Deploy audit events",
            "After=docker.service",
            "",
            "[Service]",
            "Type=oneshot",
            f"WorkingDirectory={project_root}",
            f"ExecStart=/usr/bin/env bash {script} ingest",
            "",
        ]
    )


def render_timer() -> str:
    return "\n".join(
        [
            "[Unit]",
            "Description=Collect Easy Deploy audit events every minute",
            "",
            "[Timer]",
            "OnBootSec=1min",
            "OnUnitActiveSec=60",
            "AccuracySec=15",
            "Persistent=true",
            f"Unit={UNIT_NAME}.service",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )


def _systemd_available() -> bool:
    if not Path("/run/systemd/system").exists():
        return False
    result = subprocess.run(["systemctl", "--version"], capture_output=True, text=True, check=False)
    return result.returncode == 0


def _needs_sudo(unit_dir: Path) -> bool:
    if os.geteuid() == 0:
        return False
    if unit_dir.exists():
        return not os.access(unit_dir, os.W_OK)
    return not os.access(unit_dir.parent, os.W_OK)


def _write(path: Path, content: str, *, sudo: bool) -> None:
    if not sudo:
        if path.exists() and path.read_text(encoding="utf-8") == content:
            return
        path.write_text(content, encoding="utf-8")
        return
    import tempfile

    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as temporary:
        temporary.write(content)
        source = Path(temporary.name)
    try:
        subprocess.run(["sudo", "install", "-m", "0644", str(source), str(path)], check=True)
    finally:
        source.unlink(missing_ok=True)


def _systemctl(args: list[str], *, sudo: bool, check: bool) -> None:
    command = ["systemctl", *args]
    if sudo:
        command.insert(0, "sudo")
    subprocess.run(command, check=check)


def reconcile_audit_schedule(
    project_root: Path,
    *,
    enabled: bool,
    unit_dir: Path | None = None,
    activate: bool | None = None,
) -> str:
    """Write the timer, or remove it when audit is disabled.

    ``activate`` defaults to true only for the real systemd unit directory.
    Tests pass a temporary directory and skip systemctl.
    """
    unit_dir = unit_dir or systemd_unit_dir()
    if activate is None:
        activate = unit_dir == systemd_unit_dir() and "EASYDEPLOY_SYSTEMD_UNIT_DIR" not in os.environ
    service = unit_dir / f"{UNIT_NAME}.service"
    timer = unit_dir / f"{UNIT_NAME}.timer"
    sudo = _needs_sudo(unit_dir)

    if enabled:
        unit_dir.mkdir(parents=True, exist_ok=True)
        _write(service, render_service(project_root), sudo=sudo)
        _write(timer, render_timer(), sudo=sudo)
        if not activate:
            return "Audit timer unit files written."
        if not _systemd_available():
            return "Audit timer not started: systemd is not available. Run bash audit.sh ingest from cron."
        try:
            _systemctl(["daemon-reload"], sudo=sudo, check=True)
            _systemctl(["enable", "--now", f"{UNIT_NAME}.timer"], sudo=sudo, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"Warning: audit timer was not enabled ({exc})", file=sys.stderr)
            return "Audit timer unit files written; enable failed."
        return "Audit timer enabled (ingest about once a minute)."

    if not service.exists() and not timer.exists():
        return "Audit timer not configured."
    if activate and _systemd_available():
        try:
            _systemctl(["disable", "--now", f"{UNIT_NAME}.timer"], sudo=sudo, check=False)
        except OSError:
            pass
    if service.exists():
        if sudo:
            subprocess.run(["sudo", "rm", "-f", str(service)], check=False)
        else:
            service.unlink()
    if timer.exists():
        if sudo:
            subprocess.run(["sudo", "rm", "-f", str(timer)], check=False)
        else:
            timer.unlink()
    if activate and _systemd_available():
        try:
            _systemctl(["daemon-reload"], sudo=sudo, check=False)
        except OSError:
            pass
    return "Audit timer removed."
