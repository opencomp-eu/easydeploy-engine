#!/usr/bin/env bash
# wizard.sh — interactive setup for easydeploy-engine
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

ENGINE_YAML="${SCRIPT_DIR}/engine.yaml"
KIT_BRANCH="${EASYDEPLOY_KIT_BRANCH:-}"

print_banner() {
	echo
	echo -e "${BOLD}  Easy Deploy Engine — Setup Wizard${RESET}"
	echo -e "  ─────────────────────────────────────────────────────"
	echo
}

usage() {
	echo "Usage: bash wizard.sh [--branch <name>]"
	echo
	echo "Clones Authelia / OpenCloud next to the engine if needed (git clone"
	echo "--recurse-submodules --branch <name>), runs each kit's wizard.sh,"
	echo "switches them to proxy.mode: integrate, and applies Authelia → apps →"
	echo "shared Caddy."
	echo
	echo "  --branch   Git branch to clone (default: feature/engine; use main later)"
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--help|-h)
			usage
			exit 0
			;;
		--branch)
			[[ $# -ge 2 ]] || die "--branch requires a value"
			KIT_BRANCH="$2"
			shift 2
			;;
		--branch=*)
			KIT_BRANCH="${1#*=}"
			shift
			;;
		*)
			die "Unknown option: $1"
			;;
	esac
done

refresh_discover() {
	eval "$(uv run python -m scripts.config_edit --print-discover --engine-root "${SCRIPT_DIR}")"
}

clone_kit() {
	local name="$1"
	local label="$2"
	info "Cloning ${label} (branch ${KIT_BRANCH}, --recurse-submodules)…"
	uv run python -m scripts.config_edit --clone "${name}" --branch "${KIT_BRANCH}" --engine-root "${SCRIPT_DIR}"
}

run_kit_wizard() {
	local kit_dir="$1"
	local label="$2"
	local has_deploy="$3"
	local run="n"

	if [[ ! -f "${kit_dir}/wizard.sh" ]]; then
		die "${label} has no wizard.sh (Easy Deploy kits use wizard.sh as the setup entrypoint)."
	fi

	if [[ "${has_deploy}" != "y" ]]; then
		run="y"
		info "Running ${label} wizard (writes deploy.yaml, engine will apply)…"
	else
		ask_yn run "Re-run ${label} wizard?" "n"
	fi
	[[ "${run}" == "y" ]] || return 0

	echo
	echo -e "${BOLD}  ── ${label} wizard ──${RESET}"
	echo
	bash "${kit_dir}/wizard.sh" --from-engine
	if [[ ! -f "${kit_dir}/deploy.yaml" ]]; then
		die "${label} wizard did not write deploy.yaml (cancelled?)."
	fi
}

main() {
	bash "${SCRIPT_DIR}/ensure-dependencies.sh"
	cd "${SCRIPT_DIR}"

	print_banner
	echo -e "  Press Enter to accept a ${CYAN}[default]${RESET}."
	echo
	echo "  Kits stay independent — you can still clone and run wizard.sh in"
	echo "  opencloud-easy-deploy on its own. This wizard is the one-VPS path:"
	echo "  clone siblings, run their wizards, share one Caddy."
	echo

	# AUTHELIA_FOUND, AUTHELIA_PATH, AUTHELIA_REPO, OPENCLOUD_*, MATRIX_*
	refresh_discover

	local enable_authelia="n" enable_opencloud="n" enable_matrix="n"
	local wire_oidc="n" authz_policy="two_factor" proceed
	local clone_authelia="n" clone_opencloud="n"

	echo -e "${BOLD}  Services on this VPS${RESET}"
	if [[ "${AUTHELIA_FOUND}" == "y" ]]; then
		if [[ "${AUTHELIA_HAS_DEPLOY}" != "y" ]]; then
			info "Found ${AUTHELIA_PATH} — will run its wizard.sh (no deploy.yaml yet)."
		fi
		ask_yn enable_authelia "Enable Authelia?" "y"
	else
		info "Authelia is not cloned (will clone ${AUTHELIA_REPO})."
		ask_yn enable_authelia "Enable Authelia?" "y"
		clone_authelia="${enable_authelia}"
	fi
	if [[ "${OPENCLOUD_FOUND}" == "y" ]]; then
		if [[ "${OPENCLOUD_HAS_DEPLOY}" != "y" ]]; then
			info "Found ${OPENCLOUD_PATH} — will run its wizard.sh (no deploy.yaml yet)."
		fi
		ask_yn enable_opencloud "Enable OpenCloud?" "y"
	else
		info "OpenCloud is not cloned (will clone ${OPENCLOUD_REPO})."
		ask_yn enable_opencloud "Enable OpenCloud?" "y"
		clone_opencloud="${enable_opencloud}"
	fi
	if [[ "${MATRIX_FOUND}" == "y" ]]; then
		if [[ "${MATRIX_HAS_DEPLOY}" != "y" ]]; then
			warn "Found ${MATRIX_PATH} but no deploy.yaml — Matrix clone/OIDC is not orchestrated yet."
		else
			warn "Matrix can join the shared Caddy; Authelia OIDC for Matrix is not wired yet."
		fi
		ask_yn enable_matrix "Enable Matrix?" "n"
	fi

	if [[ "${enable_authelia}" != "y" && "${enable_opencloud}" != "y" && "${enable_matrix}" != "y" ]]; then
		die "Enable at least one service."
	fi

	if [[ "${clone_authelia}" == "y" || "${clone_opencloud}" == "y" ]]; then
		echo
		echo -e "${BOLD}  Git clone${RESET}"
		echo "  Clones use: git clone --recurse-submodules --branch <branch>"
		if [[ -z "${KIT_BRANCH}" ]]; then
			ask KIT_BRANCH "Git branch to clone" "${KIT_BRANCH_DEFAULT}"
		else
			info "Kit branch: ${KIT_BRANCH}"
		fi
		if [[ -z "${KIT_BRANCH}" ]]; then
			die "Git branch is required when cloning kits."
		fi
	else
		KIT_BRANCH="${KIT_BRANCH:-${KIT_BRANCH_DEFAULT}}"
	fi

	if [[ "${enable_authelia}" == "y" && "${enable_opencloud}" == "y" ]]; then
		echo
		echo -e "${BOLD}  Identity${RESET}"
		ask_yn wire_oidc "Wire OpenCloud login through Authelia (recommended on one VPS)?" "y"
		if [[ "${wire_oidc}" == "y" ]]; then
			ask authz_policy "Authelia policy for OpenCloud: one_factor or two_factor" "two_factor"
			authz_policy="${authz_policy,,}"
			if [[ "${authz_policy}" != "one_factor" && "${authz_policy}" != "two_factor" ]]; then
				die "authorization policy must be 'one_factor' or 'two_factor'"
			fi
		fi
	fi

	echo
	echo -e "${BOLD}  Summary${RESET}"
	echo "  Authelia:   ${enable_authelia}$([[ "${clone_authelia}" == "y" ]] && echo ' (clone)')"
	echo "  OpenCloud:  ${enable_opencloud}$([[ "${clone_opencloud}" == "y" ]] && echo ' (clone)')"
	echo "  Matrix:     ${enable_matrix}"
	echo "  OIDC wire:  ${wire_oidc}"
	if [[ "${wire_oidc}" == "y" ]]; then
		echo "  Policy:     ${authz_policy}"
	fi
	if [[ "${clone_authelia}" == "y" || "${clone_opencloud}" == "y" ]]; then
		echo "  Clone:      --recurse-submodules --branch ${KIT_BRANCH}"
	fi
	echo
	echo "  Enabled kits will use proxy.mode: integrate behind shared Caddy."
	echo

	ask_yn proceed "Clone (if needed), run kit wizards, write engine.yaml, and apply?" "y"
	[[ "${proceed}" == "y" ]] || {
		info "Cancelled."
		exit 0
	}

	if [[ "${clone_authelia}" == "y" ]]; then
		clone_kit authelia Authelia
	fi
	if [[ "${clone_opencloud}" == "y" ]]; then
		clone_kit opencloud OpenCloud
	fi
	refresh_discover

	# Authelia first so OpenCloud wizard can detect a local IdP.
	if [[ "${enable_authelia}" == "y" ]]; then
		run_kit_wizard "${SCRIPT_DIR}/${AUTHELIA_PATH}" "Authelia" "${AUTHELIA_HAS_DEPLOY}"
		refresh_discover
		if [[ -f "${SCRIPT_DIR}/${AUTHELIA_PATH}/deploy.yaml" ]]; then
			export EASYDEPLOY_AUTHELIA_DEPLOY="$(cd "${SCRIPT_DIR}/${AUTHELIA_PATH}" && pwd)/deploy.yaml"
		fi
	fi
	if [[ "${enable_opencloud}" == "y" ]]; then
		run_kit_wizard "${SCRIPT_DIR}/${OPENCLOUD_PATH}" "OpenCloud" "${OPENCLOUD_HAS_DEPLOY}"
		refresh_discover
	fi

	uv run python - <<PY
from scripts.config_edit import discover_kits, set_proxy_integrate, update_from_wizard
from pathlib import Path

root = Path(${SCRIPT_DIR@Q})
kits = discover_kits(root)
enabled = []
if ${enable_authelia@Q} == "y":
    enabled.append("authelia")
if ${enable_opencloud@Q} == "y":
    enabled.append("opencloud")
if ${enable_matrix@Q} == "y":
    enabled.append("matrix")

update_from_wizard(
    enabled=enabled,
    kits=kits,
    wire=${wire_oidc@Q} == "y",
    authorization_policy=${authz_policy@Q},
    kit_branch=${KIT_BRANCH@Q} or None,
    path=Path(${ENGINE_YAML@Q}),
)
for name in enabled:
    kit = next((item for item in kits if item["name"] == name), None)
    if kit and set_proxy_integrate(kit["path"]):
        print(f"Set proxy.mode: integrate on {kit['path'] / 'deploy.yaml'}")
PY

	success "Wrote ${ENGINE_YAML}"

	info "Applying engine (kit stacks + shared Caddy + identity sidecars)…"
	bash "${SCRIPT_DIR}/apply.sh" --apply-kits

	success "Engine wizard finished."
	echo
	echo "  Later: re-run a kit apply, then bash apply.sh here, after domain or kit changes."
	echo "  Or run this wizard again to add a service."
}

main "$@"
