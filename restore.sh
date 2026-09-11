#!/usr/bin/env bash
# Restore only the engine state. Use restore-all.sh to restore enabled kits too.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"
source "${SCRIPT_DIR}/scripts/backup_common.sh"
cd "${SCRIPT_DIR}"

ENGINE_YAML="${SCRIPT_DIR}/engine.yaml"
BACKUP_STATE_DIR="${SCRIPT_DIR}/.easydeploy-engine/backup"
RESTORE_ROOT="${BACKUP_STATE_DIR}/restore"
PLAN_JSON=""
RESTORE_STAGE=""
STACK_STOPPED="false"
KEEP_STOPPED="false"

if [[ -z "${EASYDEPLOY_BACKUP_PYTHON:-}" ]]; then
    EASYDEPLOY_BACKUP_PYTHON="python3"
    export EASYDEPLOY_BACKUP_PYTHON
fi

usage() {
    cat <<'EOF'
Usage: bash restore.sh (--archive NAME | --latest | --file PATH | --list) [options]

  --archive NAME       Restore a named engine Borg archive
  --latest             Restore the newest engine Borg archive
  --file PATH          Restore an engine portable archive
  --list               List engine archives
  --encrypt            Accepted for option parity; encrypted files are detected
  --passphrase-file F  Passphrase file for openssl-encrypted portable archives
  --yes                Skip destructive confirmation prompts
  --keep-stopped       Leave the engine Caddy container stopped
  -h, --help           Show this help
EOF
}

die_usage() { usage >&2; die "$1"; }

load_plan() {
    PLAN_JSON="$(mktemp)"
    easydeploy_backup_py "${EASYDEPLOY_LIB}/python/backup_plan.py" \
        --project-root "${SCRIPT_DIR}" --emit-plan-json >"${PLAN_JSON}"
}

load_settings() {
    [[ -f "${ENGINE_YAML}" ]] || die "Missing engine.yaml; a Borg restore requires the engine configuration."
    eval "$(easydeploy_backup_settings_shell "${ENGINE_YAML}")" \
        || die "Invalid backup configuration in engine.yaml"
    [[ "${BACKUP_ENABLED}" == true ]] || \
        die "Engine backups are disabled; set backup.enabled=true in engine.yaml before restoring from Borg."
    easydeploy_backup_repo_env "${SCRIPT_DIR}/.easydeploy-engine/secrets.yaml"
}

cleanup() {
    local rc=$?
    [[ -n "${PLAN_JSON}" ]] && rm -f "${PLAN_JSON}"
    [[ -z "${RESTORE_STAGE}" ]] || rm -rf "${RESTORE_STAGE}"
    if [[ "${STACK_STOPPED}" == true && "${KEEP_STOPPED}" != true ]]; then
        info "Starting engine Caddy after restore..."
        easydeploy_backup_run_hook "${SCRIPT_DIR}" "scripts/backup_start.sh" || rc=1
    fi
    return "$rc"
}

confirm_restore() {
    [[ "${ASSUME_YES:-false}" == true ]] && return 0
    [[ -t 0 ]] || die "Refusing to restore non-interactively without --yes."
    local answer
    ask_yn answer "Restore engine state and overwrite current configuration?" n
    [[ "$answer" == y ]] || die "Aborted."
    ask_yn answer "Last chance — continue with the engine restore?" n
    [[ "$answer" == y ]] || die "Aborted."
}

list_archives() {
    easydeploy_backup_list_archives "${BACKUP_REPO_URL}"
}

extract_source() {
    local mode="$1" value="$2"
    mkdir -p "${RESTORE_ROOT}"
    RESTORE_STAGE="$(mktemp -d "${RESTORE_ROOT}/restore.XXXXXX")"
    if [[ "$mode" == file ]]; then
        easydeploy_backup_extract_portable "$value" "${RESTORE_STAGE}"
    else
        local archive="$value"
        if [[ "$mode" == latest ]]; then
            archive="$(borg list --short --last 1 "${BACKUP_REPO_URL}")"
            [[ -n "$archive" ]] || die "No engine archives found in ${BACKUP_REPO_URL}."
        else
            archive="$(easydeploy_backup_resolve_archive "${BACKUP_REPO_URL}" "$archive")"
        fi
        info "Extracting engine archive '${archive}'..."
        (cd "${RESTORE_STAGE}" && borg extract "${BACKUP_REPO_URL}::${archive}" payload)
    fi
    [[ -d "${RESTORE_STAGE}/payload" ]] || die "Backup does not contain a payload directory."
}

main() {
    local mode="" value="" passphrase_file=""
    local ASSUME_YES=false
    while (($#)); do
        case "$1" in
            --archive) [[ -n "${2:-}" ]] || die_usage "--archive requires a name"; mode=archive; value="$2"; shift ;;
            --latest) mode=latest ;;
            --file) [[ -n "${2:-}" ]] || die_usage "--file requires a path"; mode=file; value="$2"; shift ;;
            --list) mode=list ;;
            --encrypt) : ;;
            --passphrase-file) [[ -n "${2:-}" ]] || die_usage "--passphrase-file requires a path"; passphrase_file="$2"; shift ;;
            --yes) ASSUME_YES=true ;;
            --keep-stopped) KEEP_STOPPED=true ;;
            -h|--help) usage; return 0 ;;
            *) die_usage "Unknown option: $1" ;;
        esac
        shift
    done
    [[ -n "$mode" ]] || die_usage "Choose --archive NAME, --latest, --file PATH, or --list"
    [[ -z "$passphrase_file" || -f "$passphrase_file" ]] || die "Passphrase file not found: $passphrase_file"
    [[ -z "$passphrase_file" ]] || export PASSPHRASE_FILE="$passphrase_file"

    trap cleanup EXIT
    load_plan
    if [[ "$mode" == list ]]; then
        require_command borg
        load_settings
        list_archives
        return 0
    fi
    if [[ "$mode" != file ]]; then
        require_command borg
        load_settings
    else
        [[ -f "$value" ]] || die "Portable backup not found: $value"
    fi

    confirm_restore
    if [[ "$mode" == archive || "$mode" == latest ]]; then
        info "Stopping engine Caddy before Borg restore..."
        easydeploy_backup_run_hook "${SCRIPT_DIR}" "scripts/backup_stop.sh"
        STACK_STOPPED=true
    elif easydeploy_backup_container_running easydeploy_caddy; then
        info "Stopping engine Caddy before portable restore..."
        easydeploy_backup_run_hook "${SCRIPT_DIR}" "scripts/backup_stop.sh"
        STACK_STOPPED=true
    fi

    extract_source "$mode" "$value"
    easydeploy_backup_restore_payload "${SCRIPT_DIR}" "${RESTORE_STAGE}/payload" "${PLAN_JSON}"
    success "Engine restore complete."
}

main "$@"
