"""Read app spools and Docker json-file logs into the audit store."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from scripts.audit_parse import parse_lines, unwrap_docker
from scripts.audit_store import append_events, audit_dir

CURSOR_NAME = "cursors.json"


def _cursor_path(root: Path) -> Path:
    return root / "state" / CURSOR_NAME


def _load_cursors(root: Path) -> dict[str, Any]:
    path = _cursor_path(root)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    return data if isinstance(data, dict) else {}


def _save_cursors(root: Path, cursors: dict[str, Any]) -> None:
    path = _cursor_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cursors, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def complete_lines(data: bytes) -> tuple[list[str], bytes]:
    """Split off a trailing partial line so a concurrent writer is not cut mid-record."""
    if not data:
        return [], b""
    if data.endswith(b"\n"):
        text = data.decode("utf-8", errors="replace")
        return [line for line in text.splitlines() if line.strip()], b""
    head, sep, tail = data.rpartition(b"\n")
    if not sep:
        return [], data
    text = head.decode("utf-8", errors="replace")
    return [line for line in text.splitlines() if line.strip()], tail


def drain_log_file(path: Path) -> list[str]:
    """Read new complete lines and leave only the unconsumed tail in the file.

    OpenCloud opens its audit file for each event, and Synapse's handler
    reopens when the path changes, so replacing the file with the unread tail
    does not strand a long-lived writer.
    """
    if not path.is_file():
        return []
    original = path.read_bytes()
    lines, leftover = complete_lines(original)
    if not lines:
        return []
    consumed = len(original) - len(leftover)
    current = path.read_bytes()
    if not current.startswith(original[:consumed]):
        # The writer replaced the file. Parse what we saw; dedup covers a replay.
        return lines
    rest = current[consumed:]
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(rest)
    os.replace(temporary, path)
    return lines


def docker_log_path(container: str) -> tuple[str, str]:
    """Return (json-file path, warning). Warning is empty when the path is usable."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.LogPath}}", container],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return "", f"{container}: docker inspect failed ({exc})"
    if result.returncode != 0:
        return "", ""
    path = (result.stdout or "").strip()
    if not path:
        return "", ""
    if not path.endswith("-json.log"):
        return "", (
            f"{container}: log driver is not json-file ({path}); "
            "audit cannot read it. Set logging.driver to json-file and re-apply."
        )
    return path, ""


def _read_new(path: Path, cursor: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    if not path.is_file():
        return b"", cursor
    stat = path.stat()
    offset = 0
    if cursor.get("inode") == stat.st_ino and cursor.get("path") == str(path):
        offset = int(cursor.get("offset") or 0)
        if offset > stat.st_size:
            offset = 0
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()
    new_cursor = {"inode": stat.st_ino, "offset": offset + len(data), "path": str(path)}
    return data, new_cursor


def drain_docker_log(path: Path, cursor: dict[str, Any]) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    """Return (line, docker_time) pairs and the updated cursor.

    After rotation, read the previous file from the saved offset once so the
    tail that moved to ``*.json.log.1`` is not lost.
    """
    events: list[tuple[str, str]] = []
    rotated = Path(str(path) + ".1")
    if rotated.is_file() and cursor.get("inode") == rotated.stat().st_ino:
        data, advanced = _read_new(
            rotated,
            {"inode": cursor.get("inode"), "offset": cursor.get("offset"), "path": str(rotated)},
        )
        events.extend(_docker_records(data, advanced))
        cursor = {"inode": None, "offset": 0, "path": str(path)}
    data, new_cursor = _read_new(path, cursor)
    events.extend(_docker_records(data, new_cursor))
    return events, new_cursor


def _docker_records(data: bytes, cursor: dict[str, Any]) -> list[tuple[str, str]]:
    lines, leftover = complete_lines(data)
    cursor["offset"] = int(cursor.get("offset") or 0) - len(leftover)
    records: list[tuple[str, str]] = []
    for line in lines:
        text, stamp = unwrap_docker(line)
        records.append((text, stamp))
    return records


def ingest_sources(project_root: Path, sources: dict[str, Any]) -> dict[str, Any]:
    """Ingest configured files and containers. Returns counts and warnings."""
    root = audit_dir(project_root)
    counts: dict[str, int] = {}
    warnings: list[str] = []
    cursors = _load_cursors(root)

    for entry in sources.get("files") or []:
        if not isinstance(entry, dict):
            continue
        path = Path(str(entry.get("path") or ""))
        kind = str(entry.get("kind") or "")
        label = str(entry.get("source") or kind or path.name)
        if not path.is_file() or kind not in {"opencloud", "synapse"}:
            continue
        lines = drain_log_file(path)
        written = append_events(root, parse_lines(kind, lines))
        if written:
            counts[label] = counts.get(label, 0) + written

    for entry in sources.get("containers") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "")
        kind = str(entry.get("kind") or "")
        label = str(entry.get("source") or kind or name)
        if not name or kind not in {"kanidm", "stalwart", "mas", "synapse"}:
            continue
        log_path, warning = docker_log_path(name)
        if warning:
            warnings.append(warning)
            continue
        if not log_path:
            continue
        key = f"docker:{name}"
        records, cursors[key] = drain_docker_log(Path(log_path), cursors.get(key) or {})
        events = []
        for text, stamp in records:
            events.extend(parse_lines(kind, [text], stamp))
        written = append_events(root, events)
        if written:
            counts[label] = counts.get(label, 0) + written

    _save_cursors(root, cursors)
    return {"counts": counts, "warnings": warnings}
