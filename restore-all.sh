#!/usr/bin/env bash
# Restore the engine followed by each enabled kit in Kanidm-first order.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"
source "${SCRIPT_DIR}/scripts/backup_common.sh"
cd "${SCRIPT_DIR}"

ENGINE_YAML="${SCRIPT_DIR}/engine.yaml"

usage() {
    cat <<'EOF'
Usage: bash restore-all.sh (--archive NAME | --latest | --file PATH) [restore options]

The engine is restored first, followed by enabled kits in Kanidm-first order.
With --archive or --latest, each kit restores the archive taken by the same
engine backup run; every archive is resolved before anything is restored.
When --file names a directory, files named <service>-backup-*.tar.gz[.age]
are selected for each service. --yes, --passphrase-file, and --keep-stopped
are passed to every restore script.
EOF
}

die_usage() { usage >&2; die "$1"; }

# Print the engine archive name and its backup-run window as "name<TAB>start<TAB>end".
resolve_engine_run() {
    local mode="$1" value="$2"
    (
        eval "$(easydeploy_backup_settings_shell "${ENGINE_YAML}")" \
            || die "Invalid backup configuration in engine.yaml"
        [[ "${BACKUP_ENABLED}" == true ]] || \
            die "Engine backups are disabled; set backup.enabled=true in engine.yaml before restoring from Borg."
        easydeploy_backup_repo_env "${SCRIPT_DIR}/.easydeploy-engine/secrets.yaml"
        local archive window
        if [[ "$mode" == latest ]]; then
            archive="$(borg list --short --last 1 "${BACKUP_REPO_URL}")"
            [[ -n "$archive" ]] || die "No engine archives found in ${BACKUP_REPO_URL}."
        else
            archive="$(easydeploy_backup_resolve_archive "${BACKUP_REPO_URL}" "$value")"
        fi
        window="$(engine_archive_window "${BACKUP_REPO_URL}" "$archive")"
        printf '%s\t%s\n' "$archive" "$window"
    )
}

main() {
    local mode="" value=""
    local -a passthrough=()
    while (($#)); do
        case "$1" in
            --archive|--file)
                [[ -n "${2:-}" ]] || die_usage "$1 requires a value"
                mode="${1#--}"; value="$2"; shift ;;
            --latest) mode=latest ;;
            --yes|--encrypt|--keep-stopped)
                passthrough+=("$1") ;;
            --passphrase-file)
                [[ -n "${2:-}" ]] || die_usage "--passphrase-file requires a path"
                passthrough+=("$1" "$2"); shift ;;
            -h|--help) usage; return 0 ;;
            *) die_usage "Unknown option: $1" ;;
        esac
        shift
    done
    [[ -n "$mode" ]] || die_usage "Choose --archive NAME, --latest, or --file PATH"
    if [[ "$mode" == file && ! -f "$value" && ! -d "$value" ]]; then
        die "Portable backup not found: $value"
    fi

    local -a kits=()
    while IFS=$'\t' read -r name kit_root; do
        [[ -n "${name:-}" ]] || continue
        [[ -f "${kit_root}/restore.sh" ]] || die "Enabled kit '${name}' has no restore.sh at ${kit_root}/restore.sh"
        kits+=("${name}"$'\t'"${kit_root}")
    done < <(engine_enabled_services "${ENGINE_YAML}" "${SCRIPT_DIR}")

    local -a engine_args=() kit_sources=()
    if [[ "$mode" == file ]]; then
        local engine_file="$value"
        if [[ -d "$value" ]]; then
            engine_file=""
            for candidate in "$value/engine-backup-"*.tar.gz "$value/engine-backup-"*.tar.gz.age; do
                [[ -f "$candidate" ]] || continue
                engine_file="$candidate"
            done
            [[ -n "$engine_file" ]] || die "No engine portable archive found in $value"
        fi
        engine_args=(--file "$engine_file")
        for kit in "${kits[@]}"; do
            IFS=$'\t' read -r name kit_root <<<"$kit"
            local kit_file="$value"
            if [[ -d "$value" ]]; then
                kit_file=""
                for candidate in "$value/${name}-backup-"*.tar.gz "$value/${name}-backup-"*.tar.gz.age; do
                    [[ -f "$candidate" ]] || continue
                    kit_file="$candidate"
                done
                [[ -n "$kit_file" ]] || die "No portable archive for enabled kit '${name}' in ${value}"
            fi
            kit_sources+=("$kit_file")
        done
    else
        require_command borg
        local engine_archive run_start run_end
        IFS=$'\t' read -r engine_archive run_start run_end <<<"$(resolve_engine_run "$mode" "$value")"
        [[ -n "$engine_archive" ]] || die "Could not resolve the engine archive to restore."
        info "Restoring workspace backup '${engine_archive}'..."
        engine_args=(--archive "$engine_archive")
        for kit in "${kits[@]}"; do
            IFS=$'\t' read -r name kit_root <<<"$kit"
            engine_adopt_kit_repo "$name" "$kit_root"
            local kit_archive
            kit_archive="$(engine_kit_archive_for_run "$kit_root" "$run_start" "$run_end")" \
                || die "Could not read the ${name} backup repository."
            [[ -n "$kit_archive" ]] || \
                die "Kit '${name}' has no archive from workspace backup '${engine_archive}'; nothing was restored."
            info "  ${name}: ${kit_archive}"
            kit_sources+=("$kit_archive")
        done
    fi

    bash "${SCRIPT_DIR}/restore.sh" "${engine_args[@]}" "${passthrough[@]}"

    local kit_flag="--archive"
    [[ "$mode" == file ]] && kit_flag="--file"
    local i
    for i in "${!kits[@]}"; do
        IFS=$'\t' read -r name kit_root <<<"${kits[$i]}"
        info "Restoring kit ${name}..."
        engine_run_kit "${kit_root}/restore.sh" "$kit_flag" "${kit_sources[$i]}" "${passthrough[@]}"
    done
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

main "$@"
