#!/usr/bin/env bash
# Query and collect the workspace audit log. See docs/audit.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"
cd "${SCRIPT_DIR}"

clear_parent_python_env
exec uv run python -m scripts.audit "$@"
