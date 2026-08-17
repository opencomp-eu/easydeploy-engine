#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"
# shellcheck source=scripts/deps_config.sh
source "${SCRIPT_DIR}/scripts/deps_config.sh"

ensure_git() {
	command -v git &>/dev/null || die "git is required"
}

ensure_submodules() {
	if [[ -d "${SCRIPT_DIR}/.git" ]]; then
		git -C "${SCRIPT_DIR}" submodule update --init --recursive
	fi
}

ensure_uv() {
	export PATH="${HOME}/.local/bin:${PATH}"
	command -v uv &>/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
	export PATH="${HOME}/.local/bin:${PATH}"
}

main() {
	echo -e "${BOLD}Easy Deploy Engine — ensure dependencies${RESET}"
	ensure_git
	ensure_submodules
	ensure_dependencies_installed
	ensure_uv
	uv sync --dev --directory "${SCRIPT_DIR}"
	success "Ready. Next: bash wizard.sh  or  bash apply.sh"
}

main "$@"
