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

# Run a kit script without the engine's Borg environment. Every kit repository
# is keyed with that kit's own passphrase, so engine-run backups, kit timers and
# kit restores all agree.
engine_run_kit() {
    env -u BORG_PASSPHRASE -u BORG_REPO -u BORG_RSH bash "$@"
}

# Inside a subshell: load a kit's backup settings and the passphrase the kit
# scripts themselves resolve. Returns 1 when the kit has no usable repository.
_engine_load_kit_repo_env() {
    local kit_root="$1"
    unset BORG_PASSPHRASE BORG_REPO BORG_RSH
    [[ -f "${kit_root}/deploy.yaml" ]] || return 1
    eval "$(easydeploy_backup_settings_shell "${kit_root}/deploy.yaml")" || return 1
    [[ "${BACKUP_ENABLED}" == true ]] || return 1
    local secrets_file
    secrets_file="$(easydeploy_backup_py "${EASYDEPLOY_LIB}/python/backup_plan.py" \
        --project-root "${kit_root}" --emit-plan-json \
        | "${EASYDEPLOY_BACKUP_PYTHON:-python3}" -c 'import json,sys; print(json.load(sys.stdin)["secrets_file"])')"
    KIT_PASSPHRASE="$(easydeploy_backup_passphrase "${kit_root}/${secrets_file}" || true)"
    [[ -n "${BACKUP_RSH:-}" ]] && export BORG_RSH="${BACKUP_RSH}"
    return 0
}

# Kit repositories created by older engine runs were keyed with the engine's
# passphrase. Re-key them to the kit's own passphrase so kit scripts can open them.
engine_adopt_kit_repo() {
    local name="$1" kit_root="$2"
    local engine_passphrase
    engine_passphrase="$(easydeploy_backup_read_secret "${SCRIPT_DIR}/.easydeploy-engine/secrets.yaml" BORG_PASSPHRASE || true)"
    [[ -n "${engine_passphrase}" ]] || return 0
    (
        _engine_load_kit_repo_env "${kit_root}" || exit 0
        [[ "${BACKUP_REPO_ENCRYPTION:-repokey}" != none ]] || exit 0
        [[ -n "${KIT_PASSPHRASE}" && "${KIT_PASSPHRASE}" != "${engine_passphrase}" ]] || exit 0
        BORG_PASSPHRASE="${KIT_PASSPHRASE}" borg list --short --last 1 "${BACKUP_REPO_URL}" >/dev/null 2>&1 && exit 0
        BORG_PASSPHRASE="${engine_passphrase}" borg list --short --last 1 "${BACKUP_REPO_URL}" >/dev/null 2>&1 || exit 0
        info "Re-keying the ${name} backup repository to the kit's own passphrase..."
        BORG_PASSPHRASE="${engine_passphrase}" BORG_NEW_PASSPHRASE="${KIT_PASSPHRASE}" \
            borg key change-passphrase "${BACKUP_REPO_URL}"
    )
}

# Print "start<TAB>end" for an archive's backup run: from its start until the
# next archive in the same repository (end is empty for the newest archive).
engine_archive_window() {
    local repo_url="$1" archive="$2"
    borg list --json "${repo_url}" | "${EASYDEPLOY_BACKUP_PYTHON:-python3}" -c '
import json, sys
archives = sorted(json.load(sys.stdin)["archives"], key=lambda a: a["start"])
names = [a["name"] for a in archives]
if sys.argv[1] not in names:
    raise SystemExit(f"Archive not found: {sys.argv[1]}")
i = names.index(sys.argv[1])
end = archives[i + 1]["start"] if i + 1 < len(archives) else ""
print(archives[i]["start"] + "\t" + end)
' "${archive}"
}

# Print the kit archive created by the engine backup run that spans [start, end):
# the first one after the engine archive, ignoring later standalone kit backups.
engine_kit_archive_for_run() {
    local kit_root="$1" start="$2" end="$3"
    (
        _engine_load_kit_repo_env "${kit_root}" || exit 1
        [[ -z "${KIT_PASSPHRASE}" ]] || export BORG_PASSPHRASE="${KIT_PASSPHRASE}"
        borg list --json "${BACKUP_REPO_URL}" | "${EASYDEPLOY_BACKUP_PYTHON:-python3}" -c '
import json, sys
start, end = sys.argv[1], sys.argv[2]
matches = [a for a in json.load(sys.stdin)["archives"]
           if a["start"] >= start and (not end or a["start"] < end)]
if matches:
    print(min(matches, key=lambda a: a["start"])["name"])
' "${start}" "${end}"
    )
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
