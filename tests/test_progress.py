"""Tests for condensed update/apply progress helpers."""

from __future__ import annotations

from pathlib import Path

from scripts.progress import (
    GitSync,
    diff_image_ids,
    kit_apply_failure_message,
    kit_child_env,
    tail_lines,
)


def test_git_sync_line_updated_and_dirty():
    line = GitSync(
        name="stalwart",
        status="updated",
        old_sha="aaa",
        new_sha="bbb",
        dirty=("assets/branding/override.css",),
    ).line()
    assert "stalwart" in line
    assert "updated" in line
    assert "aaa → bbb" in line
    assert "override.css" in line


def test_git_sync_line_up_to_date():
    line = GitSync(name="engine", status="already up to date").line()
    assert "engine" in line
    assert "already up to date" in line


def test_diff_image_ids_reports_changed_and_new():
    before = {"caddy:2": "old", "kanidm:1": "same"}
    after = {"caddy:2": "new", "kanidm:1": "same", "opencloud:7": "abc"}
    assert diff_image_ids(before, after) == ["caddy:2", "opencloud:7"]


def test_kit_apply_failure_message_quiet_includes_log_and_hint(tmp_path: Path):
    log_path = tmp_path / "update-opencloud.log"
    message = kit_apply_failure_message(
        "opencloud",
        "line1\nline2\neuro-office failed",
        log_path=log_path,
        verbose=False,
    )
    assert "opencloud failed to apply" in message
    assert "euro-office failed" in message
    assert str(log_path) in message
    assert "bash update.sh --verbose" in message


def test_kit_child_env_quiet_sets_compose_progress():
    env = kit_child_env({"PATH": "/usr/bin", "VIRTUAL_ENV": "x"}, verbose=False)
    assert env["EASYDEPLOY_QUIET"] == "1"
    assert env["COMPOSE_PROGRESS"] == "quiet"
    assert "EASYDEPLOY_VERBOSE" not in env


def test_tail_lines_empty():
    assert tail_lines("") == "(no output)"
    assert tail_lines("a\nb\nc", count=2) == "b\nc"
