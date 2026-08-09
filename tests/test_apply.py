"""Tests for easydeploy-engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.apply import collect_fragments, render_caddyfile, validate_engine


def test_validate_engine_requires_enabled_service(tmp_path: Path):
    config = {"engine": {"network": "easydeploy-net"}, "services": {}}
    with pytest.raises(ValueError, match="Enable at least one"):
        validate_engine(config)


def test_collect_and_render_fragments(tmp_path: Path):
    kit = tmp_path / "authelia"
    frag = kit / ".authelia-easy-deploy" / "integration"
    frag.mkdir(parents=True)
    (frag / "caddy.caddy").write_text("auth.test.example {\n    reverse_proxy authelia:9091\n}\n")

    enabled = [
        {
            "name": "authelia",
            "path": kit,
            "fragment_rel": ".authelia-easy-deploy/integration/caddy.caddy",
        }
    ]
    fragments = collect_fragments(enabled)
    assert len(fragments) == 1
    caddy = render_caddyfile(fragments)
    assert "auth.test.example" in caddy
    assert "authelia-easy-deploy" in caddy
