#!/usr/bin/env bash
# Shared engine backup orchestration helpers.

engine_enabled_services() {
    local engine_yaml="$1"
    local root="$2"
    "${EASYDEPLOY_BACKUP_PYTHON:-python3}" - "${engine_yaml}" "${root}" <<'PY'
import sys
from pathlib import Path
import yaml

path = Path(sys.argv[1])
root = Path(sys.argv[2]).resolve()
data = yaml.safe_load(path.read_text()) or {}
services = data.get("services") or {}
if not isinstance(services, dict):
    raise SystemExit("services must be a mapping")
items = []
for name, entry in services.items():
    if not isinstance(entry, dict) or not entry.get("enabled"):
        continue
    kit_path = str(entry.get("path") or "").strip()
    if not kit_path:
        raise SystemExit(f"services.{name}.path is required when enabled")
    resolved = Path(kit_path).expanduser()
    if not resolved.is_absolute():
        resolved = root / resolved
    items.append((str(name), str(resolved.resolve())))
items.sort(key=lambda item: (0 if item[0] == "kanidm" else 1, item[0]))
for name, kit_path in items:
    print(f"{name}\t{kit_path}")
PY
}

engine_export_path() {
    local directory="$1"
    local service="$2"
    local timestamp="$3"
    local encrypted="$4"
    local suffix=".tar.gz"
    [[ "$encrypted" == "true" ]] && suffix=".tar.gz.age"
    printf '%s/%s-backup-%s%s\n' "${directory}" "${service}" "${timestamp}" "${suffix}"
}

engine_plan_value() {
    local plan_json="$1"
    local key="$2"
    "${EASYDEPLOY_BACKUP_PYTHON:-python3}" - "${plan_json}" "${key}" <<'PY'
import json
import sys
plan = json.loads(open(sys.argv[1]).read())
value = plan.get(sys.argv[2], "")
print(value if value is not None else "")
PY
}
