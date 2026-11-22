"""Install and remove the engine's systemd service + timer pairs."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def systemd_unit_dir() -> Path:
    return Path(os.environ.get("EASYDEPLOY_SYSTEMD_UNIT_DIR", "/etc/systemd/system"))


def should_activate(unit_dir: Path) -> bool:
    """Run systemctl only for the real unit directory (tests use a temporary one)."""
    return unit_dir == systemd_unit_dir() and "EASYDEPLOY_SYSTEMD_UNIT_DIR" not in os.environ


def systemd_available() -> bool:
    if not Path("/run/systemd/system").exists():
        return False
    try:
        result = subprocess.run(["systemctl", "--version"], capture_output=True, text=True, check=False)
    except OSError:
        return False
    return result.returncode == 0


def needs_sudo(unit_dir: Path) -> bool:
    if os.geteuid() == 0:
        return False
    if unit_dir.exists():
        return not os.access(unit_dir, os.W_OK)
    return not os.access(unit_dir.parent, os.W_OK)


def write_unit(path: Path, content: str, *, sudo: bool) -> bool:
    """Write a unit file. Returns whether its content changed."""
    try:
        if path.exists() and path.read_text(encoding="utf-8") == content:
            return False
    except PermissionError:
        pass
    if not sudo:
        path.write_text(content, encoding="utf-8")
        return True
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as temporary:
        temporary.write(content)
        source = Path(temporary.name)
    try:
        subprocess.run(["sudo", "install", "-m", "0644", str(source), str(path)], check=True)
    finally:
        source.unlink(missing_ok=True)
    return True


def remove_unit(path: Path, *, sudo: bool) -> None:
    if not path.exists():
        return
    if sudo:
        subprocess.run(["sudo", "rm", "-f", str(path)], check=False)
    else:
        path.unlink()


def systemctl(args: list[str], *, sudo: bool, check: bool) -> None:
    command = ["systemctl", *args]
    if sudo:
        command.insert(0, "sudo")
    subprocess.run(command, check=check)


def timer_enabled(unit_name: str) -> bool:
    try:
        enabled = subprocess.run(
            ["systemctl", "is-enabled", f"{unit_name}.timer"], capture_output=True, text=True, check=False
        )
        active = subprocess.run(
            ["systemctl", "is-active", f"{unit_name}.timer"], capture_output=True, text=True, check=False
        )
    except OSError:
        return False
    return enabled.stdout.strip() == "enabled" and active.stdout.strip() == "active"


def install_timer(
    unit_name: str,
    service: str,
    timer: str,
    *,
    unit_dir: Path,
    activate: bool,
) -> str:
    """Write ``<unit_name>.service`` and ``.timer`` and enable the timer.

    Returns ``written``, ``enabled``, ``no-systemd`` or ``enable-failed``.
    """
    sudo = needs_sudo(unit_dir)
    unit_dir.mkdir(parents=True, exist_ok=True)
    changed = write_unit(unit_dir / f"{unit_name}.service", service, sudo=sudo)
    changed = write_unit(unit_dir / f"{unit_name}.timer", timer, sudo=sudo) or changed
    if not activate:
        return "written"
    if not systemd_available():
        return "no-systemd"
    # Nightly runs as a non-root user must not need sudo when nothing changed.
    if not changed and timer_enabled(unit_name):
        return "enabled"
    try:
        systemctl(["daemon-reload"], sudo=sudo, check=True)
        systemctl(["enable", "--now", f"{unit_name}.timer"], sudo=sudo, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Warning: {unit_name}.timer was not enabled ({exc})", file=sys.stderr)
        return "enable-failed"
    return "enabled"


def remove_timer(unit_name: str, *, unit_dir: Path, activate: bool) -> bool:
    """Disable and delete the pair. Returns False when there was nothing to remove."""
    service = unit_dir / f"{unit_name}.service"
    timer = unit_dir / f"{unit_name}.timer"
    if not service.exists() and not timer.exists():
        return False
    sudo = needs_sudo(unit_dir)
    live = activate and systemd_available()
    if live:
        try:
            systemctl(["disable", "--now", f"{unit_name}.timer"], sudo=sudo, check=False)
        except OSError:
            pass
    remove_unit(service, sudo=sudo)
    remove_unit(timer, sudo=sudo)
    if live:
        try:
            systemctl(["daemon-reload"], sudo=sudo, check=False)
        except OSError:
            pass
    return True
