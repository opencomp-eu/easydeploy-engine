from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import app_password


def _write_engine(tmp_path: Path, *, kanidm: Path, stalwart: Path) -> Path:
    engine = {
        "services": {
            "kanidm": {"enabled": True, "path": str(kanidm)},
            "stalwart": {"enabled": True, "path": str(stalwart)},
        }
    }
    path = tmp_path / "engine.yaml"
    path.write_text(yaml.safe_dump(engine))
    return path


def _stalwart_kit(tmp_path: Path) -> Path:
    root = tmp_path / "stalwart-easy-deploy"
    root.mkdir()
    (root / "deploy.yaml").write_text(
        yaml.safe_dump({"stalwart": {"hostname": "mail.acme.test", "domain": "acme.test"}})
    )
    return root


def test_choose_mailbox_email_prefers_the_stalwart_domain():
    assert app_password.choose_mailbox_email(
        ["alice@other.test", "alice@acme.test"], "acme.test"
    ) == "alice@acme.test"


def test_choose_mailbox_email_rejects_the_wrong_domain():
    with pytest.raises(RuntimeError, match="acme.test"):
        app_password.choose_mailbox_email(["alice@other.test"], "acme.test")


def test_create_for_username_looks_up_kanidm_then_calls_stalwart(tmp_path: Path):
    kanidm = tmp_path / "kanidm-easy-deploy"
    kanidm.mkdir()
    (kanidm / "deploy.yaml").write_text("kanidm: {domain: idm.acme.test}\n")
    stalwart = _stalwart_kit(tmp_path)
    engine_path = _write_engine(tmp_path, kanidm=kanidm, stalwart=stalwart)
    calls: list[tuple[Path, str, list[str]]] = []

    def runner(command, cwd, capture_output, text, check):
        calls.append((Path(cwd), " ".join(command), command))
        if "scripts.user" in command:
            return subprocess.CompletedProcess(
                command, 0, json.dumps({"username": "alice", "emails": ["alice@acme.test"]}), ""
            )
        if "scripts.app_password" in command:
            assert "alice@acme.test" in command
            assert "--description" in command
            return subprocess.CompletedProcess(
                command, 0, json.dumps({"email": "alice@acme.test", "secret": "app_secret"}), ""
            )
        return subprocess.CompletedProcess(command, 1, "", "unexpected")

    created = app_password.create_for_username(
        "alice",
        description="Phone",
        engine=yaml.safe_load(engine_path.read_text()),
        project_root=tmp_path,
        runner=runner,
    )
    assert created == {"email": "alice@acme.test", "secret": "app_secret"}
    assert any("scripts.user" in " ".join(command) for _cwd, _joined, command in calls)
    assert any("scripts.app_password" in " ".join(command) for _cwd, _joined, command in calls)


def test_create_for_username_honours_email_override(tmp_path: Path):
    kanidm = tmp_path / "kanidm-easy-deploy"
    kanidm.mkdir()
    (kanidm / "deploy.yaml").write_text("kanidm: {}\n")
    stalwart = _stalwart_kit(tmp_path)
    engine = {
        "services": {
            "kanidm": {"enabled": True, "path": str(kanidm)},
            "stalwart": {"enabled": True, "path": str(stalwart)},
        }
    }

    def runner(command, cwd, capture_output, text, check):
        assert "scripts.user" not in command
        assert "shared@acme.test" in command
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"email": "shared@acme.test", "secret": "app_x"}), ""
        )

    created = app_password.create_for_username(
        "alice",
        email="shared@acme.test",
        engine=engine,
        project_root=tmp_path,
        runner=runner,
    )
    assert created["secret"] == "app_x"
