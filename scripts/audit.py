#!/usr/bin/env python3
"""Query and maintain the VPS audit log.

Examples:
  bash audit.sh query --action file.read --since 2026-09-01
  bash audit.sh query --actor alice --object Contracts/acme.pdf
  bash audit.sh export --since 2026-09-01 -o incident.jsonl
  bash audit.sh verify
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

import yaml

from scripts.audit_ingest import ingest_sources
from scripts.audit_schema import make_event
from scripts.audit_store import (
    append_events,
    audit_dir,
    prune,
    query_events,
    rebuild_index,
    status,
    verify,
)
from scripts.audit_wire import audit_settings, load_sources

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENGINE_PATH = PROJECT_ROOT / "engine.yaml"

COLUMNS = ("ts", "source", "action", "outcome", "actor", "object", "remote_ip")


def _engine() -> dict:
    if not ENGINE_PATH.is_file():
        raise FileNotFoundError(f"Missing {ENGINE_PATH.name}")
    data = yaml.safe_load(ENGINE_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("engine.yaml root must be a mapping")
    return data


def _operator() -> str:
    return os.environ.get("SUDO_USER") or getpass.getuser()


def _client_ip() -> str:
    raw = os.environ.get("SSH_CLIENT") or os.environ.get("SSH_CONNECTION") or ""
    return raw.split()[0] if raw else ""


def _record(action: str, message: str) -> None:
    root = audit_dir(PROJECT_ROOT)
    append_events(
        root,
        [
            make_event(
                source="audit",
                action=action,
                actor=_operator(),
                remote_ip=_client_ip(),
                message=message,
                ts_source="event",
            )
        ],
    )


def _print_table(rows: list[dict[str, str]]) -> None:
    if not rows:
        print("No audit events matched.")
        return
    widths = {key: len(key) for key in COLUMNS}
    for row in rows:
        for key in COLUMNS:
            widths[key] = max(widths[key], len(row.get(key) or ""))
    header = "  ".join(key.ljust(widths[key]) for key in COLUMNS)
    print(header)
    print("  ".join("-" * widths[key] for key in COLUMNS))
    for row in rows:
        print("  ".join((row.get(key) or "").ljust(widths[key]) for key in COLUMNS))
    print(f"\n{len(rows)} event(s).")


def _filters(args: argparse.Namespace) -> dict[str, str]:
    return {
        "since": args.since or "",
        "until": args.until or "",
        "source": args.source or "",
        "action": args.action or "",
        "actor": args.actor or "",
        "obj": args.object or "",
        "remote_ip": args.ip or "",
    }


def cmd_query(args: argparse.Namespace) -> int:
    rows = query_events(audit_dir(PROJECT_ROOT), limit=args.limit, **_filters(args))
    if args.json:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
    else:
        _print_table(rows)
    summary = " ".join(f"{key}={value}" for key, value in _filters(args).items() if value)
    _record("audit.query", summary or "unfiltered")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    rows = query_events(audit_dir(PROJECT_ROOT), limit=args.limit, **_filters(args))
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    destination = Path(args.output)
    destination.write_text(text, encoding="utf-8")
    os.chmod(destination, 0o600)
    print(f"Wrote {len(rows)} event(s) to {destination}")
    _record("audit.export", f"{destination} {len(rows)} events")
    return 0


def cmd_verify(_args: argparse.Namespace) -> int:
    ok, detail = verify(audit_dir(PROJECT_ROOT))
    print(detail)
    return 0 if ok else 1


def cmd_rebuild(_args: argparse.Namespace) -> int:
    count = rebuild_index(audit_dir(PROJECT_ROOT))
    print(f"Rebuilt the audit index ({count} events).")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    settings = audit_settings(_engine())
    info = status(audit_dir(PROJECT_ROOT))
    hold = "yes" if settings["hold"] else "no"
    enabled = "yes" if settings["enabled"] else "no"
    print(f"Enabled:    {enabled}")
    print(f"Retention:  {settings['retention_days']} days")
    print(f"Hold:       {hold}")
    print(f"Events:     {info['count']}")
    print(f"Oldest:     {info['oldest'] or '-'}")
    print(f"Newest:     {info['newest'] or '-'}")
    print(f"Store:      {audit_dir(PROJECT_ROOT)}")
    return 0


def cmd_ingest(_args: argparse.Namespace) -> int:
    config = _engine()
    settings = audit_settings(config)
    if not settings["enabled"]:
        print("Audit is disabled (audit.enabled: false).")
        return 0
    sources = load_sources(PROJECT_ROOT)
    result = ingest_sources(PROJECT_ROOT, sources)
    for warning in result["warnings"]:
        print(f"Warning: {warning}", file=sys.stderr)
    counts = result["counts"]
    if counts:
        summary = ", ".join(f"{name} {count}" for name, count in sorted(counts.items()))
        print(f"Ingested: {summary}")
    else:
        print("Ingested: nothing new")
    removed = prune(
        audit_dir(PROJECT_ROOT),
        settings["retention_days"],
        hold=settings["hold"],
    )
    if removed:
        _record("audit.prune", f"removed {removed} events older than {settings['retention_days']} days")
        print(f"Pruned {removed} event(s).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit.sh",
        description=(
            "Audit log for the workspace on this VPS: file access, sign-ins, "
            "mail submission and delivery, and chat login or membership. "
            "Message, mail, and file contents are not stored."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_filters(target: argparse.ArgumentParser, *, default_limit: int) -> None:
        target.add_argument("--since", help="UTC date or timestamp, inclusive (YYYY-MM-DD)")
        target.add_argument("--until", help="UTC date or timestamp, inclusive day")
        target.add_argument("--source", choices=["opencloud", "kanidm", "stalwart", "matrix", "audit"])
        target.add_argument(
            "--action",
            help="Exact action (file.read, auth.login) or a prefix (file.*, auth.*)",
        )
        target.add_argument("--actor", help="User id, Kanidm spn, or Matrix id. Substring.")
        target.add_argument("--object", help="Path, file id, room, or message id. Substring.")
        target.add_argument("--ip", dest="ip", help="Client IP, exact")
        target.add_argument("--limit", type=int, default=default_limit, help="0 means no limit")

    query = sub.add_parser("query", help="Print matching events")
    add_filters(query, default_limit=200)
    query.add_argument("--json", action="store_true", help="Print JSON lines")
    query.set_defaults(func=cmd_query)

    export = sub.add_parser("export", help="Write matching events as JSON lines")
    add_filters(export, default_limit=0)
    export.add_argument("-o", "--output", required=True, help="Destination file, mode 0600")
    export.set_defaults(func=cmd_export)

    verify_cmd = sub.add_parser("verify", help="Check the hash chain")
    verify_cmd.set_defaults(func=cmd_verify)

    rebuild = sub.add_parser("rebuild-index", help="Rebuild the query index from the JSONL evidence")
    rebuild.set_defaults(func=cmd_rebuild)

    status_cmd = sub.add_parser("status", help="Show retention, hold, and how much is stored")
    status_cmd.set_defaults(func=cmd_status)

    ingest = sub.add_parser("ingest", help="Read new app events, then apply retention")
    ingest.set_defaults(func=cmd_ingest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
