"""Nightly unattended updates: settings, timer units, lock, and the run itself."""

from __future__ import annotations

import fcntl
import json
import pwd
import subprocess
from pathlib import Path

import pytest

from scripts import auto_update
from scripts import update as update_module
from scripts.auto_update import (
    LockBusy,
    auto_update_settings,
    detached_engine_commit,
    engine_run_lock,
    reconcile_update_schedule,
    render_service,
    render_timer,
    wait_for_backup,
)


def _user(uid: int, name: str, home: str) -> pwd.struct_passwd:
    return pwd.struct_passwd((name, "x", uid, uid, "", home, "/bin/bash"))


def test_settings_default_to_on():
    assert auto_update_settings({}) == {
        "enabled": True,
        "calendar": "*-*-* 04:00:00",
        "randomized_delay": "30min",
    }


def test_settings_read_engine_yaml():
    settings = auto_update_settings(
        {"auto_update": {"enabled": False, "calendar": "Sun *-*-* 02:00", "randomized_delay": 0}}
    )
    assert settings == {"enabled": False, "calendar": "Sun *-*-* 02:00", "randomized_delay": "0"}


@pytest.mark.parametrize(
    "raw",
    [
        {"auto_update": "yes"},
        {"auto_update": {"enabled": "yes"}},
        {"auto_update": {"calendar": "daily\nExecStart=/bin/sh"}},
        {"auto_update": {"randomized_delay": "soon"}},
    ],
)
def test_settings_reject_bad_values(raw):
    with pytest.raises(ValueError):
        auto_update_settings(raw)


def test_service_runs_as_checkout_owner(tmp_path: Path):
    text = render_service(tmp_path, _user(1000, "deploy", "/home/deploy"))
    assert "User=deploy" in text
    assert "Environment=HOME=/home/deploy" in text
    assert "Environment=PATH=/home/deploy/.local/bin:" in text
    assert f"ExecStart=/usr/bin/env bash {tmp_path / 'update.sh'} --unattended" in text
    assert "SuccessExitStatus=75" in text


def test_service_for_root_checkout_has_no_user_line(tmp_path: Path):
    text = render_service(tmp_path, _user(0, "root", "/root"))
    assert "User=" not in text
    assert "Environment=PATH=/root/.local/bin:" in text


def test_timer_uses_calendar_and_delay():
    text = render_timer("*-*-* 05:15:00", "1h")
    assert "OnCalendar=*-*-* 05:15:00" in text
    assert "RandomizedDelaySec=1h" in text
    assert "Persistent=true" in text


def test_reconcile_writes_and_removes_units(tmp_path: Path):
    units = tmp_path / "units"
    owner = _user(1000, "deploy", "/home/deploy")
    on = auto_update_settings({})
    message = reconcile_update_schedule(tmp_path, on, unit_dir=units, activate=False, owner=owner)
    assert message == "Auto-update timer unit files written."
    assert (units / "easydeploy-update.service").is_file()
    assert "OnCalendar=*-*-* 04:00:00" in (units / "easydeploy-update.timer").read_text()

    off = auto_update_settings({"auto_update": {"enabled": False}})
    assert "removed" in reconcile_update_schedule(tmp_path, off, unit_dir=units, activate=False)
    assert not (units / "easydeploy-update.service").exists()
    assert not (units / "easydeploy-update.timer").exists()
    assert reconcile_update_schedule(tmp_path, off, unit_dir=units, activate=False) == (
        "Auto-update timer not configured."
    )


def test_lock_is_exclusive_with_platform_flock(tmp_path: Path):
    lock = tmp_path / ".easydeploy-engine.lock"
    with lock.open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(LockBusy):
            with engine_run_lock(lock, wait_seconds=0):
                pass
    with engine_run_lock(lock, wait_seconds=0):
        pass


def test_wait_for_backup_polls_until_done(monkeypatch):
    monkeypatch.setattr(auto_update, "systemd_available", lambda: True)
    states = iter([True, True, False])
    seen: list[str] = []

    def is_active(unit: str) -> bool:
        seen.append(unit)
        return next(states)

    wait_for_backup("easydeploy-engine-backup", poll_seconds=0, is_active=is_active)
    assert seen == ["easydeploy-engine-backup.service"] * 3


def test_wait_for_backup_gives_up(monkeypatch):
    monkeypatch.setattr(auto_update, "systemd_available", lambda: True)
    with pytest.raises(RuntimeError):
        wait_for_backup("x-backup", wait_seconds=0, poll_seconds=0, is_active=lambda _unit: True)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_detached_engine_commit(tmp_path: Path):
    repo = tmp_path / "engine"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
    assert detached_engine_commit(repo) is None
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "--detach", head)
    assert detached_engine_commit(repo) == head


@pytest.fixture
def unattended(tmp_path: Path, monkeypatch):
    """run_unattended with the engine config, update, and side effects stubbed."""
    state = tmp_path / "state"
    monkeypatch.setenv("EASYDEPLOY_ENGINE_LOCK", str(tmp_path / "engine.lock"))
    monkeypatch.delenv("EASYDEPLOY_ENGINE_COMMIT", raising=False)
    monkeypatch.setattr(update_module, "STATE_DIR", state)
    monkeypatch.setattr(update_module, "_backup_unit", lambda _root: None)
    monkeypatch.setattr(update_module, "prune_dangling_images", lambda: None)
    monkeypatch.setattr(update_module, "reconcile_timers", lambda _root=None: None)
    monkeypatch.setattr(update_module, "detached_engine_commit", lambda _root: None)
    config: dict = {}
    monkeypatch.setattr(update_module, "load_engine", lambda: config)
    calls: list[dict] = []
    monkeypatch.setattr(update_module, "update_stack", lambda **kwargs: calls.append(kwargs))

    def status() -> dict:
        return json.loads((state / "auto-update.json").read_text())

    return config, calls, status


def test_unattended_off_does_nothing(unattended, tmp_path: Path):
    config, calls, _status = unattended
    config["auto_update"] = {"enabled": False}
    assert update_module.run_unattended(tmp_path) == 0
    assert calls == []


def test_unattended_success_records_ok(unattended, tmp_path: Path):
    _config, calls, status = unattended
    assert update_module.run_unattended(tmp_path) == 0
    assert calls == [{"verbose": True}]
    assert status()["outcome"] == "ok"


def test_unattended_keeps_pinned_engine_commit(unattended, tmp_path: Path, monkeypatch):
    _config, _calls, _status = unattended
    seen: list[str] = []
    monkeypatch.setattr(update_module, "detached_engine_commit", lambda _root: "a" * 40)
    monkeypatch.setattr(
        update_module,
        "update_stack",
        lambda **_kwargs: seen.append(update_module.os.environ["EASYDEPLOY_ENGINE_COMMIT"]),
    )
    assert update_module.run_unattended(tmp_path) == 0
    assert seen == ["a" * 40]


def test_unattended_failure_is_recorded(unattended, tmp_path: Path, monkeypatch):
    _config, _calls, status = unattended

    def boom(**_kwargs):
        raise RuntimeError("docker pull failed")

    monkeypatch.setattr(update_module, "update_stack", boom)
    assert update_module.run_unattended(tmp_path) == 1
    assert status() | {"started_at": "", "finished_at": ""} == {
        "outcome": "failed",
        "detail": "docker pull failed",
        "started_at": "",
        "finished_at": "",
    }


def test_unattended_kit_exit_is_recorded(unattended, tmp_path: Path, monkeypatch):
    _config, _calls, status = unattended

    def kit_failed(**_kwargs):
        raise SystemExit(3)

    monkeypatch.setattr(update_module, "update_stack", kit_failed)
    assert update_module.run_unattended(tmp_path) == 3
    assert status()["outcome"] == "failed"


def test_unattended_skips_while_platform_job_runs(unattended, tmp_path: Path, monkeypatch):
    _config, calls, status = unattended
    monkeypatch.setattr(auto_update, "LOCK_WAIT_SECONDS", 0)
    monkeypatch.setattr(
        update_module,
        "engine_run_lock",
        lambda: engine_run_lock(wait_seconds=0),
    )
    with (tmp_path / "engine.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert update_module.run_unattended(tmp_path) == auto_update.EXIT_SKIPPED
    assert calls == []
    assert status()["outcome"] == "skipped"


def test_unchanged_enabled_timer_needs_no_systemctl(tmp_path: Path, monkeypatch):
    """A nightly run as a non-root user must not call sudo systemctl when nothing changed."""
    from scripts import systemd_units

    calls: list[list[str]] = []
    monkeypatch.setattr(systemd_units, "systemd_available", lambda: True)
    monkeypatch.setattr(systemd_units, "systemctl", lambda args, **_kw: calls.append(args))
    monkeypatch.setattr(systemd_units, "timer_enabled", lambda _name: True)
    units = tmp_path / "units"
    owner = _user(1000, "deploy", "/home/deploy")
    settings = auto_update_settings({})
    reconcile_update_schedule(tmp_path, settings, unit_dir=units, activate=True, owner=owner)
    assert calls == [["daemon-reload"], ["enable", "--now", "easydeploy-update.timer"]]
    calls.clear()
    reconcile_update_schedule(tmp_path, settings, unit_dir=units, activate=True, owner=owner)
    assert calls == []
