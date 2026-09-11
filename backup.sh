#!/usr/bin/env bash
# Create the engine backup and, optionally, backups for every enabled kit.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"
source "${SCRIPT_DIR}/scripts/backup_common.sh"
cd "${SCRIPT_DIR}"

ENGINE_YAML="${SCRIPT_DIR}/engine.yaml"
BACKUP_STATE_DIR="${SCRIPT_DIR}/.easydeploy-engine/backup"
BACKUP_STAGING_CURRENT="${BACKUP_STATE_DIR}/staging/current"
BORG_CONFIG_PATH="${BACKUP_STATE_DIR}/borgmatic.yaml"
PLAN_JSON=""
COLD_STOPPED="false"

if [[ -z "${EASYDEPLOY_BACKUP_PYTHON:-}" ]]; then
    if [[ -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
        EASYDEPLOY_BACKUP_PYTHON="${SCRIPT_DIR}/.venv/bin/python"
    else
        EASYDEPLOY_BACKUP_PYTHON="python3"
    fi
    export EASYDEPLOY_BACKUP_PYTHON
fi

usage() {
    cat <<'EOF'
Usage: bash backup.sh [options]

  (default)              Back up engine state, then enabled kits
  --list                 List archives for the engine and each enabled kit
  --export DIR           Write one portable archive per service into DIR
  --export-only DIR     Export one staged archive per service without Borg
  --export-from-archive NAME --export DIR
                         Export an existing archive for every service
  --encrypt              Encrypt portable exports (suffix .tar.gz.age)
  --cold                 Run configured stop/start hooks around each backup
  --schedule              Reconcile the engine and enabled kit timers
  -h, --help             Show this help
EOF
}

die_usage() {
    usage >&2
    die "$1"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

cleanup() {
    local rc=$?
    [[ -n "${PLAN_JSON}" ]] && rm -f "${PLAN_JSON}"
    rm -rf "${BACKUP_STAGING_CURRENT}" 2>/dev/null || true
    if [[ "${COLD_STOPPED}" == "true" ]]; then
        warn "Backup failed; restarting the engine Caddy container."
        easydeploy_backup_run_hook "${SCRIPT_DIR}" "scripts/backup_start.sh" || true
    fi
    return "$rc"
}

load_plan() {
    PLAN_JSON="$(mktemp)"
    easydeploy_backup_py "${EASYDEPLOY_LIB}/python/backup_plan.py" \
        --project-root "${SCRIPT_DIR}" --emit-plan-json >"${PLAN_JSON}"
}

load_settings() {
    [[ -f "${ENGINE_YAML}" ]] || die "Missing engine.yaml; copy engine.yaml.example first."
    eval "$(easydeploy_backup_settings_shell "${ENGINE_YAML}")" \
        || die "Invalid backup configuration in engine.yaml"
}

plan_hook() {
    engine_plan_value "${PLAN_JSON}" hooks | "${EASYDEPLOY_BACKUP_PYTHON}" -c \
        'import json,sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$1"
}

engine_backup_path() {
    engine_export_path "$1" engine "$2" "$3"
}

stage_payload() {
    # easydeploy-lib cleans its local plan variable with a RETURN trap; nounset
    # would otherwise see that variable after the function scope ends.
    set +u
    easydeploy_backup_stage_payload "$@"
    local rc=$?
    trap - RETURN
    set -u
    return "$rc"
}

backup_engine() {
    local export_path="$1" export_only="$2" encrypt="$3" cold="$4"
    local archive_prefix
    archive_prefix="$(engine_plan_value "${PLAN_JSON}" archive_prefix)"

    if [[ "$cold" == "true" ]]; then
        info "Cold backup: stopping engine services before staging..."
        easydeploy_backup_run_hook "${SCRIPT_DIR}" "scripts/backup_stop.sh"
        COLD_STOPPED="true"
    fi

    stage_payload "${SCRIPT_DIR}" "${BACKUP_STAGING_CURRENT}" \
        "${BACKUP_REPO_URL:-}" "$encrypt"

    if [[ "$export_only" == "true" ]]; then
        easydeploy_backup_export_portable "$export_path" "${BACKUP_STAGING_CURRENT}" "$encrypt"
    else
        mkdir -p "${BACKUP_STATE_DIR}"
        easydeploy_backup_write_borgmatic_config "${BORG_CONFIG_PATH}" \
            "${BACKUP_REPO_URL}" "${BACKUP_STAGING_CURRENT}" "${archive_prefix}"
        easydeploy_backup_repo_create "${BORG_CONFIG_PATH}"
        borgmatic --config "${BORG_CONFIG_PATH}" create --stats
        borgmatic --config "${BORG_CONFIG_PATH}" prune --stats
        borgmatic --config "${BORG_CONFIG_PATH}" check
        [[ -z "$export_path" ]] || easydeploy_backup_export_portable "$export_path" \
            "${BACKUP_STAGING_CURRENT}" "$encrypt"
        success "Engine backup complete (repository: ${BACKUP_REPO_URL})."
    fi

    if [[ "${COLD_STOPPED}" == "true" ]]; then
        easydeploy_backup_run_hook "${SCRIPT_DIR}" "scripts/backup_start.sh"
        COLD_STOPPED="false"
    fi
}

run_kit_backup() {
    local name="$1" kit_root="$2" export_path="$3" export_only="$4" \
        export_from_archive="$5" encrypt="$6" cold="$7"
    local script="${kit_root}/backup.sh"
    [[ -f "$script" ]] || die "Enabled kit '${name}' has no backup.sh at ${script}"
    local -a args=()
    if [[ "$export_only" == "true" ]]; then
        args+=(--export-only "$export_path")
    elif [[ -n "$export_from_archive" ]]; then
        args+=(--export-from-archive "$export_from_archive" --export "$export_path")
    elif [[ -n "$export_path" ]]; then
        args+=(--export "$export_path")
    fi
    [[ "$encrypt" == "true" ]] && args+=(--encrypt)
    [[ "$cold" == "true" ]] && args+=(--cold)
    info "Backing up kit ${name}..."
    bash "$script" "${args[@]}"
}

run_kit_list() {
    local name="$1" kit_root="$2"
    [[ -f "${kit_root}/backup.sh" ]] || die "Enabled kit '${name}' has no backup.sh at ${kit_root}"
    info "${name} archives:"
    bash "${kit_root}/backup.sh" --list
}

run_kit_schedule() {
    local name="$1" kit_root="$2"
    [[ -f "${kit_root}/backup.sh" ]] || die "Enabled kit '${name}' has no backup.sh at ${kit_root}"
    bash "${kit_root}/backup.sh" --schedule
}

main() {
    local list_only=false export_dir="" export_only=false export_from_archive=""
    local encrypt=false cold=false schedule=false
    while (($#)); do
        case "$1" in
            --list) list_only=true ;;
            --export)
                [[ -n "${2:-}" ]] || die_usage "--export requires a directory"
                export_dir="$2"; shift ;;
            --export-only)
                [[ -n "${2:-}" ]] || die_usage "--export-only requires a directory"
                export_dir="$2"; export_only=true; shift ;;
            --export-from-archive)
                [[ -n "${2:-}" ]] || die_usage "--export-from-archive requires an archive name"
                export_from_archive="$2"; shift ;;
            --encrypt) encrypt=true ;;
            --cold) cold=true ;;
            --schedule) schedule=true ;;
            -h|--help) usage; return 0 ;;
            *) die_usage "Unknown option: $1" ;;
        esac
        shift
    done
    [[ -z "$export_from_archive" || -n "$export_dir" ]] || \
        die_usage "--export-from-archive requires --export DIR"
    [[ "$export_only" == false || -z "$export_from_archive" ]] || \
        die_usage "--export-only cannot be combined with --export-from-archive"

    trap cleanup EXIT
    load_plan
    load_settings

    local -a services=()
    while IFS=$'\t' read -r name kit_root; do
        [[ -n "${name:-}" ]] && services+=("${name}"$'\t'"${kit_root}")
    done < <(engine_enabled_services "${ENGINE_YAML}" "${SCRIPT_DIR}")

    if [[ "$schedule" == true ]]; then
        easydeploy_backup_py "${EASYDEPLOY_LIB}/python/backup_schedule.py" \
            --project-root "${SCRIPT_DIR}" --deploy-yaml "${ENGINE_YAML}" \
            --unit-name "$(engine_plan_value "${PLAN_JSON}" timer_name)"
        for service in "${services[@]}"; do
            IFS=$'\t' read -r name kit_root <<<"$service"
            run_kit_schedule "$name" "$kit_root"
        done
        return 0
    fi

    if [[ "$list_only" == true ]]; then
        if [[ "${BACKUP_ENABLED}" == true ]]; then
            require_command borg
            easydeploy_backup_repo_env "${SCRIPT_DIR}/.easydeploy-engine/secrets.yaml"
            info "engine archives:"
            easydeploy_backup_list_archives "${BACKUP_REPO_URL}"
        else
            warn "Engine backups are disabled; skipping engine repository."
        fi
        for service in "${services[@]}"; do
            IFS=$'\t' read -r name kit_root <<<"$service"
            run_kit_list "$name" "$kit_root"
        done
        return 0
    fi

    [[ -n "$export_dir" ]] && mkdir -p "$export_dir"
    local timestamp="$(date -u +%Y-%m-%dT%H:%M:%S)" own_export=""
    [[ -z "$export_dir" ]] || own_export="$(engine_backup_path "$export_dir" "$timestamp" "$encrypt")"

    if [[ "$export_only" == true ]]; then
        backup_engine "$own_export" true "$encrypt" "$cold"
    else
        [[ "${BACKUP_ENABLED}" == true ]] || die "Engine backups are disabled; set backup.enabled=true in engine.yaml."
        require_command borg
        require_command borgmatic
        easydeploy_backup_repo_env "${SCRIPT_DIR}/.easydeploy-engine/secrets.yaml"
        if [[ -n "$export_from_archive" ]]; then
            resolved="$(easydeploy_backup_resolve_archive "${BACKUP_REPO_URL}" "$export_from_archive")"
            easydeploy_backup_export_from_archive "${BACKUP_REPO_URL}" "$resolved" "$own_export" "$encrypt"
        else
            backup_engine "$own_export" false "$encrypt" "$cold"
        fi
    fi

    for service in "${services[@]}"; do
        IFS=$'\t' read -r name kit_root <<<"$service"
        kit_export=""
        [[ -z "$export_dir" ]] || kit_export="$(engine_export_path "$export_dir" "$name" "$timestamp" "$encrypt")"
        run_kit_backup "$name" "$kit_root" "$kit_export" "$export_only" "$export_from_archive" "$encrypt" "$cold"
    done
}

main "$@"
