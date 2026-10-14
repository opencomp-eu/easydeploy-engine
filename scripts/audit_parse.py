"""Turn native app lines into normalized audit events.

Parsers are deliberately strict. A line that does not match a known
security event is dropped, so operational noise never becomes evidence.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from scripts.audit_schema import make_event, normalize_action, parse_ts, redact

Parser = Callable[[str, str], list[dict[str, str]]]

_KV = re.compile(r"([A-Za-z][A-Za-z0-9_.-]*)=(\"[^\"]*\"|'[^']*'|\S+)")
_MXID = re.compile(r"@[^\s:@]+:[^\s]+")

# Longest names first so delivery.auth-failed wins over auth.failed.
STALWART_EVENTS: tuple[tuple[str, str, str], ...] = (
    ("delivery.auth-failed", "mail.auth_failure", "failure"),
    ("smtp.auth-mechanism-not-supported", "mail.auth_failure", "failure"),
    ("queue.queue-message-authenticated", "mail.send", "success"),
    ("delivery.message-rejected", "mail.delivery_failure", "failure"),
    ("auth.too-many-attempts", "auth.failure", "failure"),
    ("smtp.auth-not-allowed", "mail.auth_failure", "failure"),
    ("queue.quota-exceeded", "mail.quota", "failure"),
    ("delivery.delivered", "mail.receive", "success"),
    ("delivery.failed", "mail.delivery_failure", "failure"),
    ("auth.success", "auth.login", "success"),
    ("auth.failed", "auth.failure", "failure"),
    ("auth.error", "auth.failure", "failure"),
)

_KANIDM_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"authentication success", re.I), "auth.login", "success"),
    (re.compile(r"authenticated successfully", re.I), "auth.login", "success"),
    (
        re.compile(r"authentication (?:failed|failure|error)|invalid credential", re.I),
        "auth.failure",
        "failure",
    ),
    (re.compile(r"person created|account created", re.I), "identity.user_create", "success"),
    (re.compile(r"credential updated|password changed", re.I), "identity.credential_change", "success"),
)

_EXECUTANT = re.compile(r"^user '([^']+)'")

_OPENCLOUD_KEEP = (
    "Path",
    "OldPath",
    "FileID",
    "Owner",
    "Permissions",
    "ShareWith",
    "ShareType",
    "ItemType",
    "SpaceID",
    "Name",
    "NewName",
    "UserID",
    "GroupID",
)


def _pairs(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, raw in _KV.findall(text):
        found[key] = raw.strip("\"'")
    return found


def _first(pairs: dict[str, str], *keys: str) -> str:
    folded = {key.lower(): value for key, value in pairs.items()}
    for key in keys:
        if folded.get(key.lower()):
            return folded[key.lower()]
    return ""


def _stamp(pairs: dict[str, str], fallback: str) -> tuple[str, str]:
    raw = _first(pairs, "time", "ts", "timestamp", "@timestamp")
    if raw and parse_ts(raw):
        parsed = parse_ts(raw)
        assert parsed is not None
        return parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "event"
    if fallback and parse_ts(fallback):
        parsed = parse_ts(fallback)
        assert parsed is not None
        return parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "event"
    return "", "ingest"


def unwrap_docker(line: str) -> tuple[str, str]:
    """Return the inner log line and the docker timestamp, if this is a json-file record."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return line, ""
    if isinstance(payload, dict) and isinstance(payload.get("log"), str) and "stream" in payload:
        return str(payload["log"]).rstrip("\n"), str(payload.get("time") or "")
    return line, ""


def parse_opencloud(line: str, docker_time: str = "") -> list[dict[str, str]]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    action = str(payload.get("Action") or payload.get("action") or "").strip()
    if not action:
        return []
    secrets = tuple(
        str(payload.get(key) or "")
        for key in ("ShareToken", "Token", "InviteLink")
        if str(payload.get(key) or "")
    )
    when_raw = str(payload.get("Time") or payload.get("time") or "")
    if when_raw and parse_ts(when_raw):
        parsed = parse_ts(when_raw)
        assert parsed is not None
        ts, ts_source = parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "event"
    elif docker_time and parse_ts(docker_time):
        parsed = parse_ts(docker_time)
        assert parsed is not None
        ts, ts_source = parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "event"
    else:
        ts, ts_source = "", "ingest"
    outcome = "failure" if "fail" in action.lower() else "success"
    detail_bits = []
    for key in _OPENCLOUD_KEEP:
        value = payload.get(key)
        if value not in (None, "", []):
            detail_bits.append(f"{key}={value}")
    message = str(payload.get("Message") or payload.get("message") or "")
    executant = ""
    matched = _EXECUTANT.match(message.strip())
    if matched:
        executant = matched.group(1).strip()
    if detail_bits and not message:
        message = " ".join(detail_bits)
    elif detail_bits:
        message = message + " " + " ".join(detail_bits)
    path = str(payload.get("Path") or payload.get("Name") or "")
    user_field = str(payload.get("User") or payload.get("UserID") or "")
    file_owner = str(payload.get("Owner") or "")
    return [
        make_event(
            source="opencloud",
            action=normalize_action(action),
            outcome=outcome,
            ts=ts,
            ts_source=ts_source,
            actor=executant or user_field,
            obj=path,
            object_id=str(payload.get("FileID") or payload.get("ShareID") or payload.get("SpaceID") or ""),
            remote_ip=str(payload.get("RemoteAddr") or ""),
            owner=file_owner or user_field,
            message=message,
            secrets=secrets,
        )
    ]


def parse_kanidm(line: str, docker_time: str = "") -> list[dict[str, str]]:
    text, stamp = unwrap_docker(line)
    if stamp:
        docker_time = stamp
    matched: tuple[str, str] | None = None
    for pattern, action, outcome in _KANIDM_RULES:
        if pattern.search(text):
            matched = (action, outcome)
            break
    if matched is None:
        return []
    pairs = _pairs(text)
    ts, ts_source = _stamp(pairs, docker_time)
    return [
        make_event(
            source="kanidm",
            action=matched[0],
            outcome=matched[1],
            ts=ts,
            ts_source=ts_source,
            actor=_first(pairs, "spn", "name", "user", "account"),
            remote_ip=_first(pairs, "client_ip", "remote_ip", "remoteIp", "ip", "addr"),
            message=redact(text.strip()),
        )
    ]


def parse_stalwart(line: str, docker_time: str = "") -> list[dict[str, str]]:
    text, stamp = unwrap_docker(line)
    if stamp:
        docker_time = stamp
    found: tuple[str, str, str] | None = None
    for name, action, outcome in STALWART_EVENTS:
        if name in text:
            found = (name, action, outcome)
            break
    if found is None:
        return []
    pairs = _pairs(text)
    ts, ts_source = _stamp(pairs, docker_time)
    obj = _first(pairs, "to", "rcpt", "messageId", "queueId")
    return [
        make_event(
            source="stalwart",
            action=found[1],
            outcome=found[2],
            ts=ts,
            ts_source=ts_source,
            actor=_first(pairs, "account", "accountId", "from", "authenticatedAs", "username"),
            obj=obj,
            object_id=_first(pairs, "messageId", "queueId"),
            remote_ip=_first(pairs, "remoteIp", "remote_ip", "ip"),
            message=redact(text.strip()),
        )
    ]


def parse_synapse(line: str, docker_time: str = "") -> list[dict[str, str]]:
    logger = ""
    message = line.strip()
    event_time = docker_time
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and not ("log" in payload and "stream" in payload):
        logger = str(payload.get("namespace") or payload.get("logger") or payload.get("name") or "")
        message = str(payload.get("log") or payload.get("message") or payload.get("msg") or "")
        event_time = str(
            payload.get("time") or payload.get("@timestamp") or payload.get("timestamp") or docker_time
        )
    else:
        text, stamp = unwrap_docker(line)
        message = text.strip()
        if stamp:
            event_time = stamp
    parsed = parse_ts(event_time) if event_time else None
    ts = parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if parsed else ""
    ts_source = "event" if parsed else "ingest"
    mxid = _MXID.search(message)
    actor = mxid.group(0) if mxid else ""
    action = ""
    outcome = "success"
    lowered = message.lower()
    logger_l = logger.lower()
    if "logging in user" in lowered or ("logged in" in lowered and actor):
        action = "chat.login"
    elif "failed" in lowered and ("auth" in lowered or "login" in lowered or "password" in lowered):
        action = "auth.failure"
        outcome = "failure"
    elif "room_member" in logger_l or "membership" in lowered:
        if re.search(r"\b(ban|banned)\b", lowered):
            action = "chat.ban"
        elif re.search(r"\b(kick|kicked)\b", lowered):
            action = "chat.kick"
        elif re.search(r"\b(leave|left)\b", lowered):
            action = "chat.leave"
        elif re.search(r"\b(join|joined)\b", lowered):
            action = "chat.join"
    if not action:
        return []
    room = ""
    room_match = re.search(r"(![^\s:]+:[^\s]+)", message)
    if room_match:
        room = room_match.group(1)
    return [
        make_event(
            source="matrix",
            action=action,
            outcome=outcome,
            ts=ts,
            ts_source=ts_source,
            actor=actor,
            obj=room,
            message=redact(message),
        )
    ]


def parse_mas(line: str, docker_time: str = "") -> list[dict[str, str]]:
    text, stamp = unwrap_docker(line)
    if stamp:
        docker_time = stamp
    lowered = text.lower()
    if "fail" in lowered or "denied" in lowered:
        outcome = "failure"
        action = "auth.failure"
    elif "login" in lowered or "authenticated" in lowered or "session created" in lowered:
        outcome = "success"
        action = "auth.login"
    else:
        return []
    pairs = _pairs(text)
    actor = _first(pairs, "username", "user", "sub", "email")
    if not actor:
        mxid = _MXID.search(text)
        actor = mxid.group(0) if mxid else ""
    if not actor:
        return []
    ts, ts_source = _stamp(pairs, docker_time)
    return [
        make_event(
            source="matrix",
            action=action,
            outcome=outcome,
            ts=ts,
            ts_source=ts_source,
            actor=actor,
            remote_ip=_first(pairs, "ip", "remoteIp", "client_ip"),
            message=redact(text.strip()),
        )
    ]


PARSERS: dict[str, Parser] = {
    "opencloud": parse_opencloud,
    "kanidm": parse_kanidm,
    "stalwart": parse_stalwart,
    "synapse": parse_synapse,
    "mas": parse_mas,
}


def parse_lines(kind: str, lines: list[str], docker_time: str = "") -> list[dict[str, str]]:
    parser = PARSERS[kind]
    events: list[dict[str, str]] = []
    for line in lines:
        if not line.strip():
            continue
        events.extend(parser(line, docker_time))
    return events
