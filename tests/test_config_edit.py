"""Tests for engine wizard config helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.config_edit import (
    DEFAULT_KIT_BRANCH,
    clone_kit,
    clone_named_kit,
    discover_kits,
    emit_wizard_discover,
    load_kit_branch,
    normalize_branch,
    set_proxy_integrate,
    update_from_wizard,
)


def _write_kit(parent: Path, dirname: str, *, deploy: bool = True) -> Path:
    kit = parent / dirname
    kit.mkdir()
    (kit / "apply.sh").write_text("#!/bin/bash\n")
    (kit / "wizard.sh").write_text("#!/bin/bash\n")
    if deploy:
        (kit / "deploy.yaml").write_text(
            yaml.safe_dump({"proxy": {"type": "caddy", "mode": "standalone"}})
        )
    return kit


def test_discover_kits_siblings(tmp_path: Path):
    engine = tmp_path / "easydeploy-engine"
    engine.mkdir()
    _write_kit(tmp_path, "authelia-easy-deploy")
    _write_kit(tmp_path, "opencloud-easy-deploy", deploy=False)

    kits = {item["name"]: item for item in discover_kits(engine)}
    assert "authelia" in kits
    assert kits["authelia"]["rel_path"] == "../authelia-easy-deploy"
    assert kits["authelia"]["has_deploy"] is True
    assert kits["opencloud"]["has_deploy"] is False
    assert "matrix" not in kits


def test_emit_wizard_discover(tmp_path: Path):
    engine = tmp_path / "easydeploy-engine"
    engine.mkdir()
    _write_kit(tmp_path, "authelia-easy-deploy")

    text = emit_wizard_discover(engine)
    assert "KIT_BRANCH_DEFAULT=feature/engine" in text
    assert "AUTHELIA_FOUND=y" in text
    assert "AUTHELIA_HAS_DEPLOY=y" in text
    assert "AUTHELIA_HAS_WIZARD=y" in text
    assert "OPENCLOUD_FOUND=n" in text
    assert "OPENCLOUD_PATH=../opencloud-easy-deploy" in text
    assert "OPENCLOUD_REPO=https://github.com/opencomp-eu/opencloud-easy-deploy.git" in text
    assert "OPENCLOUD_ORCHESTRATE=y" in text
    assert "MATRIX_FOUND=n" in text
    assert "MATRIX_ORCHESTRATE=n" in text


def test_set_proxy_integrate_writes_mode(tmp_path: Path):
    kit = _write_kit(tmp_path, "authelia-easy-deploy")
    assert set_proxy_integrate(kit) is True
    data = yaml.safe_load((kit / "deploy.yaml").read_text())
    assert data["proxy"]["mode"] == "integrate"
    assert data["proxy"]["integrate"]["network"] == "easydeploy-net"
    assert set_proxy_integrate(kit) is False


def test_set_proxy_integrate_missing_deploy(tmp_path: Path):
    kit = tmp_path / "empty-kit"
    kit.mkdir()
    assert set_proxy_integrate(kit) is False


def test_update_from_wizard_enables_and_drops_apply_kits(tmp_path: Path):
    engine_yaml = tmp_path / "engine.yaml"
    example = tmp_path / "engine.yaml.example"
    example.write_text(
        yaml.safe_dump(
            {
                "engine": {"network": "easydeploy-net"},
                "identity": {"wire": "auto", "apply_kits": True},
                "services": {"authelia": {"enabled": False, "path": "../old"}},
            }
        )
    )
    kits = [
        {
            "name": "authelia",
            "rel_path": "../authelia-easy-deploy",
            "fragment": ".authelia-easy-deploy/integration/caddy.caddy",
        }
    ]
    update_from_wizard(
        enabled=["authelia", "opencloud"],
        kits=kits,
        wire=True,
        authorization_policy="two_factor",
        kit_branch="feature/engine",
        path=engine_yaml,
        example=example,
    )
    data = yaml.safe_load(engine_yaml.read_text())
    assert data["identity"]["wire"] == "auto"
    assert "apply_kits" not in data["identity"]
    assert data["engine"]["kit_branch"] == "feature/engine"
    assert data["services"]["authelia"]["enabled"] is True
    assert data["services"]["authelia"]["path"] == "../authelia-easy-deploy"
    assert data["services"]["opencloud"]["enabled"] is True
    assert data["services"]["matrix"]["enabled"] is False


def _git_commit(repo: Path) -> None:
    import os
    import subprocess

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.test",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.test",
        }
    )
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True,
        capture_output=True,
        env=env,
    )


def test_clone_kit_from_local_repo(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "wizard.sh").write_text("#!/bin/bash\n")
    (src / "apply.sh").write_text("#!/bin/bash\n")
    _git_commit(src)

    dest = tmp_path / "opencloud-easy-deploy"
    assert clone_kit(str(src), dest) == "cloned"
    assert (dest / "wizard.sh").is_file()
    assert clone_kit(str(src), dest) == "exists"


def test_clone_kit_updates_existing_checkout(tmp_path: Path):
    import os
    import subprocess

    src = tmp_path / "src"
    src.mkdir()
    (src / "wizard.sh").write_text("v1\n")
    (src / "apply.sh").write_text("x\n")
    _git_commit(src)
    subprocess.run(
        ["git", "-C", str(src), "checkout", "-b", "feature/engine"],
        check=True,
        capture_output=True,
    )

    dest = tmp_path / "kit"
    assert clone_kit(str(src), dest, branch="feature/engine") == "cloned"
    assert (dest / "wizard.sh").read_text() == "v1\n"

    (src / "wizard.sh").write_text("v2-authelia-detect\n")
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.test",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.test",
        }
    )
    subprocess.run(["git", "-C", str(src), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(src), "commit", "-m", "detect authelia"],
        check=True,
        capture_output=True,
        env=env,
    )

    assert clone_kit(str(src), dest, branch="feature/engine") == "updated"
    assert (dest / "wizard.sh").read_text() == "v2-authelia-detect\n"


def test_clone_kit_rejects_non_kit_dir(tmp_path: Path):
    dest = tmp_path / "authelia-easy-deploy"
    dest.mkdir()
    (dest / "README.md").write_text("nope\n")
    with pytest.raises(FileExistsError, match="not an Easy Deploy kit"):
        clone_kit("https://example.invalid/repo.git", dest)


def test_clone_named_kit_unknown():
    with pytest.raises(KeyError, match="unknown kit"):
        clone_named_kit("not-a-kit")


def test_normalize_branch_rejects_junk():
    with pytest.raises(ValueError, match="invalid git branch"):
        normalize_branch("feature engine")
    with pytest.raises(ValueError, match="invalid git branch"):
        normalize_branch("-main")
    assert normalize_branch("feature/engine") == "feature/engine"


def test_load_kit_branch_from_engine_yaml(tmp_path: Path):
    engine = tmp_path / "easydeploy-engine"
    engine.mkdir()
    (engine / "engine.yaml").write_text(yaml.safe_dump({"engine": {"kit_branch": "main"}}))
    assert load_kit_branch(engine) == "main"
    assert DEFAULT_KIT_BRANCH == "feature/engine"


def test_clone_kit_checks_out_branch(tmp_path: Path):
    import os
    import subprocess

    src = tmp_path / "src"
    src.mkdir()
    (src / "wizard.sh").write_text("main\n")
    (src / "apply.sh").write_text("x\n")
    _git_commit(src)
    subprocess.run(
        ["git", "-C", str(src), "checkout", "-b", "feature/engine"],
        check=True,
        capture_output=True,
    )
    (src / "wizard.sh").write_text("feature\n")
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.test",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.test",
        }
    )
    subprocess.run(["git", "-C", str(src), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(src), "commit", "-m", "on feature"],
        check=True,
        capture_output=True,
        env=env,
    )

    dest = tmp_path / "feature-clone"
    assert clone_kit(str(src), dest, branch="feature/engine") == "cloned"
    assert (dest / "wizard.sh").read_text() == "feature\n"

    dest_main = tmp_path / "main-clone"
    assert clone_kit(str(src), dest_main, branch="main") == "cloned"
    assert (dest_main / "wizard.sh").read_text() == "main\n"
