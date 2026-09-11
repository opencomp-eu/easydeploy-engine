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
When --file names a directory, files named <service>-backup-*.tar.gz[.age]
are selected for each service. --yes, --passphrase-file, and --keep-stopped
are passed to every restore script.
EOF
}

die_usage() { usage >&2; die "$1"; }

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

    if [[ "$mode" == file && -d "$value" ]]; then
        local engine_file=""
        for candidate in "$value/engine-backup-"*.tar.gz "$value/engine-backup-"*.tar.gz.age; do
            [[ -f "$candidate" ]] || continue
            engine_file="$candidate"
        done
        [[ -n "$engine_file" ]] || die "No engine portable archive found in $value"
        value="$engine_file"
    fi
    local -a engine_args=("--${mode}")
    [[ "$mode" != latest ]] && engine_args+=("$value")
    bash "${SCRIPT_DIR}/restore.sh" "${engine_args[@]}" "${passthrough[@]}"

    while IFS=$'\t' read -r name kit_root; do
        [[ -n "${name:-}" ]] || continue
        local script="${kit_root}/restore.sh"
        [[ -f "$script" ]] || die "Enabled kit '${name}' has no restore.sh at ${script}"
        local -a kit_args=("--${mode}")
        if [[ "$mode" == file ]]; then
            local kit_file="$value"
            if [[ -d "$value" ]]; then
                kit_file=""
                for candidate in "$value/${name}-backup-"*.tar.gz "$value/${name}-backup-"*.tar.gz.age; do
                    [[ -f "$candidate" ]] || continue
                    kit_file="$candidate"
                done
                [[ -n "$kit_file" ]] || die "No portable archive for enabled kit '${name}' in ${value}"
            fi
            kit_args+=("$kit_file")
        fi
        info "Restoring kit ${name}..."
        bash "$script" "${kit_args[@]}" "${passthrough[@]}"
    done < <(engine_enabled_services "${ENGINE_YAML}" "${SCRIPT_DIR}")
}

main "$@"
