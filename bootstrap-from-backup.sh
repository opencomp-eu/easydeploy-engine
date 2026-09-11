#!/usr/bin/env bash
# Restore an engine portable archive on a fresh host.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"

usage() {
    cat <<'EOF'
Usage: bash bootstrap-from-backup.sh <portable-archive> [restore options]

Installs dependencies, then restores the engine portable archive. Extra options
are passed to restore.sh (for example --yes, --passphrase-file, or --keep-stopped).
Use restore-all.sh separately for enabled kit archives.
EOF
}

if [[ $# -lt 1 || "$1" == "-h" || "$1" == "--help" ]]; then
    usage >&2
    [[ $# -ge 1 ]] && exit 0 || exit 1
fi

archive="$1"
shift
[[ "$archive" != -* ]] || { usage >&2; die "The first argument must be a portable archive."; }
[[ -f "$archive" ]] || die "Portable backup not found: $archive"

bash "${SCRIPT_DIR}/ensure-dependencies.sh"
exec bash "${SCRIPT_DIR}/restore.sh" --file "$archive" "$@"
