#!/usr/bin/env bash
# Reconcile engine-generated runtime files after restoring engine state.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINE_YAML="${SCRIPT_DIR}/engine.yaml"
[[ -f "${ENGINE_YAML}" ]] || exit 0

if ! "${EASYDEPLOY_BACKUP_PYTHON:-python3}" - "${ENGINE_YAML}" <<'PY'
import sys
import yaml

data = yaml.safe_load(open(sys.argv[1])) or {}
services = data.get("services") or {}
raise SystemExit(0 if any(isinstance(v, dict) and v.get("enabled") for v in services.values()) else 1)
PY
then
    echo "No engine services enabled; skipping runtime reconciliation."
    exit 0
fi

bash "${SCRIPT_DIR}/apply.sh" --skip-runtime --skip-kits
