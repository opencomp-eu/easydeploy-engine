from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.apply import seed_kit_deploy, validate_engine
from scripts.config_edit import KIT_CATALOG, apply_kit_policy, update_from_wizard
from scripts.integrations import IDENTITY_CONSUMERS, MAIL_CONSUMERS
from scripts.stacks import CORE_KITS, NEXTCLOUD, OPENCLOUD_MATRIX, STACKS, apply_order, infer_stack, resolve_stack


def _services(*names: str) -> dict:
    return {
        "services": {
            name: {"enabled": True, "path": f"../{name}-easy-deploy", "fragment": "x"} for name in names
        }
    }


def test_every_stack_kit_is_in_the_catalog_and_starts_with_kanidm():
    catalog = {kit["name"] for kit in KIT_CATALOG}
    for stack in STACKS.values():
        assert stack.apply_order[0] == "kanidm"
        assert set(stack.apply_order) <= catalog
        assert set(CORE_KITS) <= stack.kits
        assert set(stack.suite) <= stack.kits


def test_suites_do_not_overlap():
    assert not set(OPENCLOUD_MATRIX.suite) & set(NEXTCLOUD.suite)


def test_every_adapter_belongs_to_a_stack():
    kits = set().union(*(stack.kits for stack in STACKS.values()))
    assert {consumer.kit for consumer in (*IDENTITY_CONSUMERS, *MAIL_CONSUMERS)} <= kits


def test_infer_stack():
    assert infer_stack(["kanidm", "stalwart"]) is OPENCLOUD_MATRIX
    assert infer_stack(["kanidm", "opencloud"]) is OPENCLOUD_MATRIX
    assert infer_stack(["kanidm", "stalwart", "nextcloud"]) is NEXTCLOUD


def test_mixed_paths_are_rejected():
    with pytest.raises(ValueError, match="one collaboration path"):
        resolve_stack({}, ["kanidm", "opencloud", "nextcloud"])


def test_explicit_stack_rejects_foreign_kits():
    with pytest.raises(ValueError, match="belong to another path"):
        resolve_stack({"engine": {"stack": "nextcloud"}}, ["kanidm", "matrix"])


def test_unknown_stack_name():
    with pytest.raises(ValueError, match="engine.stack must be one of"):
        resolve_stack({"engine": {"stack": "sharepoint"}}, ["kanidm"])


def test_core_only_respects_explicit_stack():
    assert resolve_stack({"engine": {"stack": "nextcloud"}}, ["kanidm", "stalwart"]) is NEXTCLOUD


def test_apply_order_per_path():
    assert apply_order(["stalwart", "opencloud", "kanidm", "matrix"]) == ["kanidm", "matrix", "opencloud", "stalwart"]
    assert apply_order(["nextcloud", "stalwart", "kanidm"]) == ["kanidm", "stalwart", "nextcloud"]


def test_validate_engine_orders_by_stack():
    enabled = validate_engine(_services("nextcloud", "kanidm", "stalwart"))
    assert [item["name"] for item in enabled] == ["kanidm", "stalwart", "nextcloud"]


def test_validate_engine_rejects_mixed_paths():
    with pytest.raises(ValueError, match="one collaboration path"):
        validate_engine(_services("kanidm", "matrix", "nextcloud"))


def test_apply_kit_policy_turns_bulwark_off(tmp_path: Path):
    (tmp_path / "deploy.yaml").write_text(
        yaml.safe_dump({"stalwart": {"hostname": "mail.x"}, "bulwark": {"enabled": True, "domain": "webmail.x"}})
    )
    assert apply_kit_policy(tmp_path, NEXTCLOUD.policy_for("stalwart")) is True
    data = yaml.safe_load((tmp_path / "deploy.yaml").read_text())
    assert data["bulwark"] == {"enabled": False, "domain": "webmail.x"}
    assert data["stalwart"] == {"hostname": "mail.x"}
    assert apply_kit_policy(tmp_path, NEXTCLOUD.policy_for("stalwart")) is False


def test_seed_kit_deploy_applies_stack_policy(tmp_path: Path):
    kit = tmp_path / "stalwart-easy-deploy"
    kit.mkdir()
    (kit / "deploy.yaml").write_text(yaml.safe_dump({"proxy": {"mode": "integrate"}}))
    service = {"name": "stalwart", "path": kit, "fragment_rel": "x", "deploy": False}
    seed_kit_deploy(service, tmp_path, verbose=False, stack=NEXTCLOUD)
    assert yaml.safe_load((kit / "deploy.yaml").read_text())["bulwark"]["enabled"] is False


def test_seed_kit_deploy_leaves_bulwark_alone_on_opencloud_matrix(tmp_path: Path):
    kit = tmp_path / "stalwart-easy-deploy"
    kit.mkdir()
    (kit / "deploy.yaml").write_text(yaml.safe_dump({"proxy": {"mode": "integrate"}}))
    service = {"name": "stalwart", "path": kit, "fragment_rel": "x", "deploy": False}
    seed_kit_deploy(service, tmp_path, verbose=False, stack=OPENCLOUD_MATRIX)
    assert "bulwark" not in yaml.safe_load((kit / "deploy.yaml").read_text())


def test_update_from_wizard_records_stack(tmp_path: Path):
    path = tmp_path / "engine.yaml"
    update_from_wizard(
        enabled=["kanidm", "stalwart", "nextcloud"],
        kits=[],
        wire=True,
        path=path,
        stack="nextcloud",
    )
    data = yaml.safe_load(path.read_text())
    assert data["engine"]["stack"] == "nextcloud"
    assert data["services"]["nextcloud"]["enabled"] is True
    assert data["services"]["opencloud"]["enabled"] is False


def test_update_from_wizard_rejects_foreign_kits(tmp_path: Path):
    with pytest.raises(ValueError):
        update_from_wizard(
            enabled=["kanidm", "opencloud"],
            kits=[],
            wire=True,
            path=tmp_path / "engine.yaml",
            stack="nextcloud",
        )
