"""Install or remove the audit ingest timer."""

from __future__ import annotations

from pathlib import Path

from scripts.systemd_units import install_timer, remove_timer, should_activate, systemd_unit_dir


UNIT_NAME = "easydeploy-audit"


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
        activate = should_activate(unit_dir)

    if enabled:
        outcome = install_timer(
            UNIT_NAME,
            render_service(project_root),
            render_timer(),
            unit_dir=unit_dir,
            activate=activate,
        )
        return {
            "written": "Audit timer unit files written.",
            "no-systemd": "Audit timer not started: systemd is not available. Run bash audit.sh ingest from cron.",
            "enable-failed": "Audit timer unit files written; enable failed.",
        }.get(outcome, "Audit timer enabled (ingest about once a minute).")

    if not remove_timer(UNIT_NAME, unit_dir=unit_dir, activate=activate):
        return "Audit timer not configured."
    return "Audit timer removed."
