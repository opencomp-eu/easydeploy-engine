"""Tests for engine backup plans and service ordering."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_engine_plan_covers_state_and_caddy_volumes():
    helper = ROOT / "easydeploy-lib" / "python" / "backup_plan.py"
    result = subprocess.run(
        ["python3", str(helper), "--project-root", str(ROOT), "--emit-plan-json"],
        check=True,
        capture_output=True,
        text=True,
    )
    plan = json.loads(result.stdout)
    assert plan["service"] == "engine"
    assert {item["path"] for item in plan["persistent_paths"]} == {
        "engine.yaml",
        ".easydeploy-engine",
    }
    assert plan["volumes"] == ["easydeploy_caddy_data", "easydeploy_caddy_config"]
    assert plan["timer_name"] == "easydeploy-engine-backup"


def test_enabled_services_are_kanidm_first(tmp_path: Path):
    config = {
        "services": {
            "stalwart": {"enabled": True, "path": "../stalwart-easy-deploy"},
            "kanidm": {"enabled": True, "path": "../kanidm-easy-deploy"},
            "matrix": {"enabled": True, "path": "../matrix-easy-deploy"},
            "opencloud": {"enabled": False, "path": "../opencloud-easy-deploy"},
        }
    }
    config_path = tmp_path / "engine.yaml"
    config_path.write_text(yaml.safe_dump(config))
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                "source scripts/backup_common.sh; "
                'engine_enabled_services "$1" "$2"'
            ),
            "bash",
            str(config_path),
            str(tmp_path / "engine"),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    names = [line.split("\t", 1)[0] for line in result.stdout.splitlines()]
    assert names == ["kanidm", "matrix", "stalwart"]


def test_enabled_services_empty_is_success(tmp_path: Path):
    config_path = tmp_path / "engine.yaml"
    config_path.write_text(yaml.safe_dump({"services": {}}))
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source scripts/backup_common.sh; engine_enabled_services "$1" "$2"',
            "bash",
            str(config_path),
            str(tmp_path),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == ""
