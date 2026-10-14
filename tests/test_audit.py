"""Audit store, parsers, and engine wiring."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from scripts.audit_ingest import drain_docker_log, drain_log_file, ingest_sources
from scripts.audit_parse import parse_kanidm, parse_mas, parse_opencloud, parse_stalwart, parse_synapse
from scripts.audit_schedule import reconcile_audit_schedule
from scripts.audit_schema import make_event
from scripts.audit_store import append_events, audit_dir, prune, query_events, verify
from scripts.audit_wire import audit_settings, load_sources, wire_audit


def _file_read(path: str = "/Alice/Contracts/acme.pdf", when: str = "2026-10-01T12:00:00Z") -> dict[str, str]:
    raw = {
        "Action": "file_read",
        "User": "alice",
        "Path": path,
        "FileID": "file-1",
        "Owner": "alice",
        "RemoteAddr": "203.0.113.10",
        "Time": when,
        "Message": "user 'alice' read file 'file-1'",
        "ShareToken": "super-secret-token",
    }
    event = parse_opencloud(json.dumps(raw))[0]
    return event


def test_opencloud_file_read_redacts_share_token():
    event = _file_read()
    assert event["action"] == "file.read"
    assert event["actor"] == "alice"
    assert event["object"] == "/Alice/Contracts/acme.pdf"
    assert event["remote_ip"] == "203.0.113.10"
    assert "super-secret-token" not in event["message"]
    assert event["ts"].startswith("2026-10-01T12:00:00")


def test_parsers_drop_noise_and_keep_security_events():
    assert parse_kanidm("info: something routine happened") == []
    kanidm = parse_kanidm(
        "INFO authentication success spn=alice@idm.example.com client_ip=203.0.113.4"
    )[0]
    assert kanidm["action"] == "auth.login"
    assert kanidm["actor"] == "alice@idm.example.com"
    assert kanidm["remote_ip"] == "203.0.113.4"

    wrapped = json.dumps(
        {
            "log": "INFO authentication failed spn=bob@idm.example.com client_ip=198.51.100.8\n",
            "stream": "stderr",
            "time": "2026-10-02T01:02:03.000000000Z",
        }
    )
    failed = parse_kanidm(wrapped)[0]
    assert failed["action"] == "auth.failure"
    assert failed["outcome"] == "failure"
    assert failed["ts"].startswith("2026-10-02T01:02:03")

    sent = parse_stalwart(
        "INFO queue.queue-message-authenticated account=alice@example.com messageId=<m@example.com>"
    )[0]
    assert sent["action"] == "mail.send"
    assert sent["actor"] == "alice@example.com"
    denied = parse_stalwart("INFO delivery.auth-failed remoteIp=192.0.2.9 account=carol@example.com")[0]
    assert denied["action"] == "mail.auth_failure"
    assert parse_stalwart("INFO imap.fetch mailbox=INBOX") == []

    login = parse_synapse(
        json.dumps(
            {
                "namespace": "synapse.handlers.auth",
                "log": "Logging in user @alice:example.com on device ABCD",
                "time": "2026-10-03T04:05:06Z",
            }
        )
    )[0]
    assert login["action"] == "chat.login"
    assert login["actor"] == "@alice:example.com"
    joined = parse_synapse(
        json.dumps(
            {
                "namespace": "synapse.handlers.room_member",
                "log": "@alice:example.com joined !room:example.com",
            }
        )
    )[0]
    assert joined["action"] == "chat.join"
    assert joined["object"] == "!room:example.com"

    mas = parse_mas("login complete username=alice ip=203.0.113.9")[0]
    assert mas["action"] == "auth.login"
    assert mas["source"] == "matrix"
    assert parse_mas("listening on 0.0.0.0:8080") == []


def test_chain_query_prune_and_tamper(tmp_path: Path):
    root = audit_dir(tmp_path)
    first = _file_read(when="2026-09-01T08:00:00Z")
    second = make_event(
        source="kanidm",
        action="auth.login",
        actor="alice@idm.example.com",
        ts="2026-10-14T09:00:00Z",
        ts_source="event",
    )
    assert append_events(root, [first, first, second]) == 2
    ok, detail = verify(root)
    assert ok, detail

    found = query_events(root, action="file.read", actor="alice", obj="acme.pdf")
    assert len(found) == 1
    assert found[0]["object"].endswith("acme.pdf")

    removed = prune(root, retention_days=30, today=date(2026, 10, 14))
    assert removed == 1
    ok, detail = verify(root)
    assert ok, detail
    assert query_events(root, action="file.read") == []
    assert len(query_events(root, action="auth.login")) == 1

    day_file = next((root / "store").glob("events-*.jsonl"))
    text = day_file.read_text(encoding="utf-8")
    day_file.write_text(text.replace("auth.login", "auth.xxxxx"), encoding="utf-8")
    ok, detail = verify(root)
    assert not ok
    assert "hash" in detail


def test_hold_keeps_old_events(tmp_path: Path):
    root = audit_dir(tmp_path)
    append_events(root, [_file_read(when="2020-01-01T00:00:00Z")])
    assert prune(root, retention_days=30, hold=True, today=date(2026, 10, 14)) == 0
    assert len(query_events(root)) == 1


def test_drain_log_file_keeps_partial_tail(tmp_path: Path):
    path = tmp_path / "opencloud.jsonl"
    path.write_text('{"Action":"file_read","User":"a","Path":"/x","Time":"2026-10-01T00:00:00Z"}\npartial', encoding="utf-8")
    lines = drain_log_file(path)
    assert len(lines) == 1
    assert path.read_text(encoding="utf-8") == "partial"


def test_docker_cursor_does_not_reread(tmp_path: Path):
    path = tmp_path / "container-json.log"
    line = json.dumps(
        {
            "log": "INFO authentication success spn=alice@idm.example.com client_ip=203.0.113.4\n",
            "stream": "stdout",
            "time": "2026-10-02T00:00:00.000000000Z",
        }
    )
    path.write_text(line + "\n", encoding="utf-8")
    records, cursor = drain_docker_log(path, {})
    assert len(records) == 1
    again, cursor = drain_docker_log(path, cursor)
    assert again == []
    path.write_text(path.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")
    more, _cursor = drain_docker_log(path, cursor)
    assert len(more) == 1


def test_ingest_opencloud_spool(tmp_path: Path):
    spool = tmp_path / "spool"
    spool.mkdir()
    log = spool / "opencloud.jsonl"
    log.write_text(json.dumps({
        "Action": "file_read",
        "User": "alice",
        "Path": "/Alice/Contracts/acme.pdf",
        "FileID": "file-1",
        "Time": "2026-10-01T12:00:00Z",
        "Message": "read",
    }) + "\n", encoding="utf-8")
    # ingest_sources looks at project_root/.easydeploy-engine/audit, while the
    # spool file can live anywhere the sources manifest points.
    result = ingest_sources(
        tmp_path,
        {"files": [{"path": str(log), "kind": "opencloud", "source": "opencloud"}], "containers": []},
    )
    assert result["counts"]["opencloud"] == 1
    assert log.read_text(encoding="utf-8") == ""
    rows = query_events(audit_dir(tmp_path), action="file.read")
    assert rows[0]["actor"] == "alice"
    again = ingest_sources(
        tmp_path,
        {"files": [{"path": str(log), "kind": "opencloud", "source": "opencloud"}], "containers": []},
    )
    assert again["counts"] == {}


def test_wire_audit_sidecar_and_opt_out(tmp_path: Path):
    opencloud = tmp_path / "opencloud-easy-deploy"
    matrix = tmp_path / "matrix-easy-deploy"
    opencloud.mkdir()
    matrix.mkdir()
    enabled = [
        {"name": "opencloud", "path": opencloud, "fragment_rel": "x"},
        {"name": "matrix", "path": matrix, "fragment_rel": "x"},
        {"name": "kanidm", "path": tmp_path / "kanidm-easy-deploy", "fragment_rel": "x"},
    ]
    (tmp_path / "kanidm-easy-deploy").mkdir()
    notes = wire_audit({"audit": {"retention_days": 365}}, enabled, tmp_path)
    assert any("OpenCloud" in note for note in notes)
    sidecar = yaml.safe_load((opencloud / ".opencloud-easy-deploy/integration/audit.yaml").read_text())
    assert sidecar["enabled"] is True
    assert Path(sidecar["spool_dir"]).is_dir()
    assert (matrix / "modules/core/audit-spool").is_symlink()
    sources = load_sources(tmp_path)
    assert sources["retention_days"] == 365
    assert any(item["kind"] == "opencloud" for item in sources["files"])
    assert any(item["name"] == "kanidm" for item in sources["containers"])

    wire_audit({"audit": {"enabled": False, "retention_days": 365}}, enabled, tmp_path)
    assert not (opencloud / ".opencloud-easy-deploy/integration/audit.yaml").exists()


def test_retention_bounds():
    assert audit_settings({})["enabled"] is True
    assert audit_settings({})["retention_days"] == 365
    with pytest.raises(ValueError):
        audit_settings({"audit": {"retention_days": 7}})


def test_timer_units_render_without_systemctl(tmp_path: Path):
    unit_dir = tmp_path / "systemd"
    message = reconcile_audit_schedule(tmp_path, enabled=True, unit_dir=unit_dir, activate=False)
    assert "written" in message
    service = (unit_dir / "easydeploy-audit.service").read_text(encoding="utf-8")
    assert "audit.sh ingest" in service
    assert (unit_dir / "easydeploy-audit.timer").is_file()
    removed = reconcile_audit_schedule(tmp_path, enabled=False, unit_dir=unit_dir, activate=False)
    assert "removed" in removed
    assert not (unit_dir / "easydeploy-audit.service").exists()
