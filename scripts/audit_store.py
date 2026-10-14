"""Append-only audit evidence plus a rebuildable query index.

Daily JSONL files are the record. SQLite is an index and can be rebuilt.
Each line carries the hash of the previous line so a deleted or edited
event fails verification. Pruning removes a prefix of days and records the
hash it pruned through, so the remaining chain still verifies.
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from scripts.audit_schema import EVENT_KEYS, GENESIS, canonical, chain_hash, dedup_id, parse_ts

RETENTION_MIN_DAYS = 30
RETENTION_MAX_DAYS = 2555
RETENTION_DEFAULT_DAYS = 365


def audit_dir(project_root: Path) -> Path:
    return project_root / ".easydeploy-engine" / "audit"


def _store_dir(root: Path) -> Path:
    return root / "store"


def _db_path(root: Path) -> Path:
    return root / "index.sqlite"


def ensure_layout(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    _store_dir(root).mkdir(parents=True, exist_ok=True)
    (root / "state").mkdir(parents=True, exist_ok=True)
    (root / "spool").mkdir(parents=True, exist_ok=True)
    os.chmod(_store_dir(root), 0o700)
    os.chmod(root / "state", 0o700)


def _connect(root: Path) -> sqlite3.Connection:
    ensure_layout(root)
    conn = sqlite3.connect(_db_path(root), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            dedup TEXT NOT NULL UNIQUE,
            ts TEXT NOT NULL,
            source TEXT NOT NULL,
            action TEXT NOT NULL,
            outcome TEXT NOT NULL,
            actor TEXT NOT NULL,
            object TEXT NOT NULL,
            object_id TEXT NOT NULL,
            remote_ip TEXT NOT NULL,
            message TEXT NOT NULL,
            hash TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_action ON events(action)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_actor ON events(actor)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_source ON events(source)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('head', ?), ('epoch', ?)",
        (GENESIS, GENESIS),
    )
    return conn


def _meta(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else GENESIS


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _day_path(root: Path, when: datetime) -> Path:
    return _store_dir(root) / f"events-{when.astimezone(timezone.utc).date().isoformat()}.jsonl"


def _event_day(event: dict[str, Any]) -> datetime:
    parsed = parse_ts(str(event.get("ts") or ""))
    return parsed or datetime.now(timezone.utc)


def day_files(root: Path) -> list[Path]:
    store = _store_dir(root)
    if not store.is_dir():
        return []
    return sorted(path for path in store.glob("events-*.jsonl") if path.is_file())


def iter_records(root: Path) -> Iterable[dict[str, Any]]:
    for path in day_files(root):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if isinstance(record, dict):
                yield record


class _Lock:
    def __init__(self, root: Path) -> None:
        ensure_layout(root)
        self.path = root / "state" / "write.lock"
        self.handle: Any = None

    def __enter__(self) -> "_Lock":
        self.handle = self.path.open("a+")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *args: object) -> None:
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def reconcile_index(root: Path) -> None:
    """If a crash landed events in JSONL before the index, rebuild the index."""
    records = list(iter_records(root))
    conn = _connect(root)
    try:
        count = int(conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"])
        head = _meta(conn, "head")
        epoch = _meta(conn, "epoch")
    finally:
        conn.close()
    file_head = str(records[-1]["hash"]) if records else epoch
    if count != len(records) or head != file_head:
        _rebuild_index(root)


def append_events(root: Path, events: list[dict[str, str]]) -> int:
    """Append events that are not already in the store. Returns how many were new."""
    if not events:
        return 0
    with _Lock(root):
        reconcile_index(root)
        return _append_events(root, events)


def _append_events(root: Path, events: list[dict[str, str]]) -> int:
    written = 0
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        head = _meta(conn, "head")
        pending: list[tuple[dict[str, str], str, str, Path]] = []
        seen: set[str] = set()
        for event in events:
            body = {key: str(event.get(key) or "") for key in EVENT_KEYS}
            digest = dedup_id(body)
            if digest in seen:
                continue
            seen.add(digest)
            if conn.execute("SELECT 1 FROM events WHERE dedup = ?", (digest,)).fetchone():
                continue
            prev = head
            hashed = chain_hash(prev, body)
            record = {**body, "prev": prev, "hash": hashed}
            when = _event_day(body)
            pending.append((record, digest, hashed, _day_path(root, when)))
            head = hashed
        if not pending:
            conn.execute("COMMIT")
            return 0
        by_file: dict[Path, list[dict[str, str]]] = {}
        for record, _digest, _hashed, path in pending:
            by_file.setdefault(path, []).append(record)
        for path, records in by_file.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
                    handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(path, 0o600)
        for record, digest, hashed, _path in pending:
            conn.execute(
                """
                INSERT INTO events (
                    dedup, ts, source, action, outcome, actor, object, object_id,
                    remote_ip, message, hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest,
                    record["ts"],
                    record["source"],
                    record["action"],
                    record["outcome"],
                    record["actor"],
                    record["object"],
                    record["object_id"],
                    record["remote_ip"],
                    record["message"],
                    hashed,
                ),
            )
            written += 1
        _set_meta(conn, "head", head)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return written


def verify(root: Path) -> tuple[bool, str]:
    with _Lock(root):
        return _verify(root)


def _verify(root: Path) -> tuple[bool, str]:
    conn = _connect(root)
    try:
        epoch = _meta(conn, "epoch")
        head = _meta(conn, "head")
    finally:
        conn.close()
    prev = epoch
    count = 0
    last = epoch
    for record in iter_records(root):
        count += 1
        if str(record.get("prev") or "") != prev:
            return False, f"chain break at event {count}: previous hash does not match"
        body = {key: str(record.get(key) or "") for key in EVENT_KEYS}
        expected = chain_hash(prev, body)
        if str(record.get("hash") or "") != expected:
            return False, f"chain break at event {count}: hash does not match the event"
        if canonical(body) != canonical(record):
            return False, f"chain break at event {count}: event body changed"
        prev = expected
        last = expected
    if last != head:
        return False, "chain head does not match the last event"
    return True, f"Audit chain ok ({count} events)."


def rebuild_index(root: Path) -> int:
    """Rebuild the SQLite index from the JSONL evidence without rewriting it."""
    with _Lock(root):
        return _rebuild_index(root)


def _rebuild_index(root: Path) -> int:
    records = list(iter_records(root))
    ok, detail = _verify(root)
    if not ok:
        raise RuntimeError(detail)
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        epoch = _meta(conn, "epoch")
        conn.execute("DELETE FROM events")
        for record in records:
            body = {key: str(record.get(key) or "") for key in EVENT_KEYS}
            conn.execute(
                """
                INSERT INTO events (
                    dedup, ts, source, action, outcome, actor, object, object_id,
                    remote_ip, message, hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dedup_id(body),
                    body["ts"],
                    body["source"],
                    body["action"],
                    body["outcome"],
                    body["actor"],
                    body["object"],
                    body["object_id"],
                    body["remote_ip"],
                    body["message"],
                    str(record.get("hash") or ""),
                ),
            )
        head = str(records[-1]["hash"]) if records else epoch
        _set_meta(conn, "head", head)
        _set_meta(conn, "epoch", epoch)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return len(records)


def _row_dict(row: sqlite3.Row) -> dict[str, str]:
    return {key: str(row[key]) for key in ("ts", "source", "action", "outcome", "actor", "object", "object_id", "remote_ip", "message")}


def query_events(
    root: Path,
    *,
    since: str = "",
    until: str = "",
    source: str = "",
    action: str = "",
    actor: str = "",
    actor_ids: tuple[str, ...] = (),
    obj: str = "",
    remote_ip: str = "",
    limit: int = 200,
) -> list[dict[str, str]]:
    clauses = ["1=1"]
    args: list[Any] = []
    if since:
        clauses.append("ts >= ?")
        args.append(_bound(since, end=False))
    if until:
        clauses.append("ts < ?")
        args.append(_bound(until, end=True))
    if source:
        clauses.append("source = ?")
        args.append(source)
    if action:
        if action.endswith("*"):
            clauses.append("action LIKE ?")
            args.append(action[:-1] + "%")
        else:
            clauses.append("action = ?")
            args.append(action)
    if actor or actor_ids:
        parts: list[str] = []
        if actor:
            parts.append("actor LIKE ? ESCAPE '\\'")
            args.append(_like(actor))
        distinct_ids = tuple(dict.fromkeys(item for item in actor_ids if item))
        if distinct_ids:
            marks = ", ".join("?" for _ in distinct_ids)
            parts.append(f"actor IN ({marks})")
            args.extend(distinct_ids)
            for ident in distinct_ids:
                parts.append("message LIKE ? ESCAPE '\\'")
                args.append(_like(ident))
        clauses.append("(" + " OR ".join(parts) + ")")
    if obj:
        clauses.append(
            "(object LIKE ? ESCAPE '\\' OR object_id LIKE ? ESCAPE '\\' OR message LIKE ? ESCAPE '\\')"
        )
        like = _like(obj)
        args.extend([like, like, like])
    if remote_ip:
        clauses.append("remote_ip = ?")
        args.append(remote_ip)
    sql = (
        "SELECT ts, source, action, outcome, actor, object, object_id, remote_ip, message "
        f"FROM events WHERE {' AND '.join(clauses)} ORDER BY ts DESC, id DESC"
    )
    if limit > 0:
        sql += " LIMIT ?"
        args.append(limit)
    conn = _connect(root)
    try:
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    found = [_row_dict(row) for row in rows]
    found.reverse()
    return found


def _bound(value: str, *, end: bool) -> str:
    text = value.strip()
    if len(text) == 10:
        parsed = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        if end:
            parsed = parsed + timedelta(days=1)
        return parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    parsed_dt = parse_ts(text)
    if parsed_dt is None:
        raise ValueError(f"Could not parse time {value!r}")
    if end and len(text) == 10:
        parsed_dt = parsed_dt + timedelta(days=1)
    return parsed_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def status(root: Path) -> dict[str, Any]:
    conn = _connect(root)
    try:
        count = int(conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"])
        oldest = conn.execute("SELECT ts FROM events ORDER BY ts ASC, id ASC LIMIT 1").fetchone()
        newest = conn.execute("SELECT ts FROM events ORDER BY ts DESC, id DESC LIMIT 1").fetchone()
        epoch = _meta(conn, "epoch")
        head = _meta(conn, "head")
    finally:
        conn.close()
    return {
        "count": count,
        "oldest": str(oldest["ts"]) if oldest else "",
        "newest": str(newest["ts"]) if newest else "",
        "epoch": epoch,
        "head": head,
    }


def prune(
    root: Path,
    retention_days: int,
    *,
    hold: bool = False,
    today: date | None = None,
) -> int:
    """Delete whole days older than the retention window. Returns rows removed."""
    if hold:
        return 0
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    with _Lock(root):
        return _prune(root, retention_days, today=today)


def _prune(
    root: Path,
    retention_days: int,
    *,
    today: date | None = None,
) -> int:
    today = today or datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=retention_days)
    cutoff_ts = datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    removed_hashes: list[str] = []
    for path in day_files(root):
        day_text = path.stem.removeprefix("events-")
        try:
            day = date.fromisoformat(day_text)
        except ValueError:
            continue
        if day >= cutoff:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if isinstance(record, dict) and record.get("hash"):
                removed_hashes.append(str(record["hash"]))
        path.unlink()
    if not removed_hashes:
        return 0
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        deleted = conn.execute("DELETE FROM events WHERE ts < ?", (cutoff_ts,)).rowcount
        _set_meta(conn, "epoch", removed_hashes[-1])
        remaining = conn.execute("SELECT hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
        _set_meta(conn, "head", str(remaining["hash"]) if remaining else removed_hashes[-1])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return int(deleted or 0)
