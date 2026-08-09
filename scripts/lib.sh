#!/usr/bin/env bash
# scripts/lib.sh

_lib_sh_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_engine_root="$(cd "${_lib_sh_dir}/.." && pwd)"

# shellcheck source=easydeploy-lib/lib/init.sh
source "${_engine_root}/easydeploy-lib/lib/init.sh"

export PATH="${HOME}/.local/bin:${PATH}"
