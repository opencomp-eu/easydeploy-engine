#!/usr/bin/env bash
# wizard.sh — interactive setup for easydeploy-engine
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

EASYDEPLOY_INVOKE_ARGS=("$@")
clear_parent_python_env

ENGINE_YAML="${SCRIPT_DIR}/engine.yaml"
KIT_BRANCH="${EASYDEPLOY_KIT_BRANCH:-}"
STACK="${EASYDEPLOY_STACK:-}"

print_banner() {
	echo
	echo -e "${BOLD}  Easy Deploy Engine — Setup Wizard${RESET}"
	echo -e "  ─────────────────────────────────────────────────────"
	echo
}

usage() {
	echo "Usage: bash wizard.sh [--stack opencloud-matrix|nextcloud] [--branch <name>]"
	echo
	echo "Pick a collaboration path, then clone or update its kits next to the engine"
	echo "(git clone --recurse-submodules --branch <name>), run each kit's wizard.sh,"
	echo "switch them to proxy.mode: integrate, and apply Kanidm → apps → shared Caddy."
	echo
	echo "  --stack    opencloud-matrix: Kanidm, Stalwart + Bulwark, OpenCloud, Matrix"
	echo "             nextcloud:        Kanidm, Stalwart, Nextcloud (Collabora, Talk, Mail)"
	echo "  --branch   Git branch to clone (default: master)"
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
		--stack)
			[[ $# -ge 2 ]] || die "--stack requires a value"
			STACK="$2"
			shift 2
			;;
		--stack=*)
			STACK="${1#*=}"
			shift
			;;
		*)
			die "Unknown option: $1"
			;;
	esac
done

# Kits offered on each path, in wizard order (Kanidm first so later wizards can
# detect a local IdP). Fields: name|label|prompt|default if cloned|default if not.
# Keep in sync with scripts/stacks.py.
stack_kits() {
	case "$1" in
		opencloud-matrix)
			printf '%s\n' \
				"kanidm|Kanidm|Enable Kanidm (identity)?|y|y" \
				"opencloud|OpenCloud|Enable OpenCloud (files, office)?|y|y" \
				"matrix|Matrix|Enable Matrix (chat, calls)?|y|n" \
				"stalwart|Stalwart|Enable Stalwart (mail + Bulwark webmail)?|y|n"
			;;
		nextcloud)
			printf '%s\n' \
				"kanidm|Kanidm|Enable Kanidm (identity)?|y|y" \
				"stalwart|Stalwart|Enable Stalwart (mail, read in Nextcloud Mail)?|y|y" \
				"nextcloud|Nextcloud|Enable Nextcloud (files, Collabora, Talk, calendar, contacts, mail)?|y|y"
			;;
		*)
			return 1
			;;
	esac
}

stack_label() {
	case "$1" in
		opencloud-matrix) echo "OpenCloud + Matrix" ;;
		nextcloud) echo "Nextcloud" ;;
	esac
}

# kit_info <name> <FIELD> → value of <NAME>_<FIELD> from --print-discover.
kit_info() {
	local var="${1^^}_$2"
	echo "${!var:-}"
}

refresh_discover() {
	eval "$(uv run python -m scripts.config_edit --print-discover --engine-root "${SCRIPT_DIR}")"
}

clone_kit() {
	local name="$1"
	local label="$2"
	local result
	info "Syncing ${label} on branch ${KIT_BRANCH} (clone or update, --recurse-submodules)…"
	result="$(uv run python -m scripts.config_edit --clone "${name}" --branch "${KIT_BRANCH}" --engine-root "${SCRIPT_DIR}")"
	info "${label}: ${result}"
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
	clear_parent_python_env
	bash "${kit_dir}/wizard.sh" --from-engine
	if [[ ! -f "${kit_dir}/deploy.yaml" ]]; then
		die "${label} wizard did not write deploy.yaml (cancelled?)."
	fi
}

choose_stack() {
	if [[ -n "${STACK}" ]]; then
		stack_kits "${STACK}" >/dev/null || die "Unknown --stack ${STACK} (use opencloud-matrix or nextcloud)."
		info "Collaboration path: $(stack_label "${STACK}")"
		return
	fi
	echo -e "${BOLD}  Collaboration path${RESET}"
	echo "  Both paths share Kanidm (sign-in) and Stalwart (mail). Pick the suite on top:"
	echo "    opencloud-matrix  OpenCloud files + office, Matrix chat, Bulwark webmail"
	echo "    nextcloud         Nextcloud files, Collabora office, Talk, calendar, contacts, Nextcloud Mail"
	while true; do
		ask STACK "Path" "${CURRENT_STACK}"
		STACK="${STACK,,}"
		stack_kits "${STACK}" >/dev/null && break
		warn "Type opencloud-matrix or nextcloud."
	done
	if [[ "${STACK}" != "${CURRENT_STACK}" && -f "${ENGINE_YAML}" ]]; then
		warn "engine.yaml currently runs $(stack_label "${CURRENT_STACK}"). Switching disables its kits here;"
		warn "stop them first (bash stop.sh) and move data yourself. Nothing is migrated."
	fi
	echo
}

main() {
	bash "${SCRIPT_DIR}/ensure-dependencies.sh"
	ensure_docker_group_session "${EASYDEPLOY_INVOKE_ARGS[@]}"
	cd "${SCRIPT_DIR}"

	print_banner
	echo -e "  Press Enter to accept a ${CYAN}[default]${RESET}."
	echo
	print_data_dir_hint
	echo
	echo "  Kits stay independent — you can still clone and run wizard.sh in"
	echo "  any kit on its own. This wizard is the one-VPS path:"
	echo "  clone siblings, run their wizards, share one Caddy."
	echo

	# CURRENT_STACK, KIT_BRANCH_DEFAULT, and <KIT>_FOUND / _PATH / _HAS_DEPLOY / _REPO per catalog kit.
	refresh_discover
	choose_stack

	local -a rows=() enabled=()
	mapfile -t rows < <(stack_kits "${STACK}")

	local row name label prompt default_found default_missing answer
	echo -e "${BOLD}  Services on this VPS${RESET}"
	for row in "${rows[@]}"; do
		IFS='|' read -r name label prompt default_found default_missing <<<"${row}"
		if [[ "$(kit_info "${name}" FOUND)" == "y" ]]; then
			if [[ "$(kit_info "${name}" HAS_DEPLOY)" != "y" ]]; then
				info "Found $(kit_info "${name}" PATH) — will run its wizard.sh (no deploy.yaml yet)."
			fi
			ask_yn answer "${prompt}" "${default_found}"
		else
			info "${label} is not cloned (will clone $(kit_info "${name}" REPO))."
			ask_yn answer "${prompt}" "${default_missing}"
		fi
		if [[ "${answer}" == "y" ]]; then
			enabled+=("${name}")
		fi
	done
	[[ ${#enabled[@]} -gt 0 ]] || die "Enable at least one service."

	echo
	echo -e "${BOLD}  Git clone${RESET}"
	echo "  Enabled kits are cloned or updated: git fetch + checkout --recurse-submodules."
	if [[ -z "${KIT_BRANCH}" ]]; then
		ask KIT_BRANCH "Git branch to clone/update" "${KIT_BRANCH_DEFAULT}"
	else
		info "Kit branch: ${KIT_BRANCH}"
	fi
	[[ -n "${KIT_BRANCH}" ]] || die "Git branch is required when cloning or updating kits."

	local wire_oidc="n" has_kanidm="n" consumers=()
	for name in "${enabled[@]}"; do
		if [[ "${name}" == "kanidm" ]]; then
			has_kanidm="y"
		else
			consumers+=("$(stack_kits "${STACK}" | awk -F'|' -v n="${name}" '$1 == n {print $2}')")
		fi
	done
	if [[ "${has_kanidm}" == "y" && ${#consumers[@]} -gt 0 ]]; then
		echo
		echo -e "${BOLD}  Identity${RESET}"
		local joined
		joined="$(printf '%s, ' "${consumers[@]}")"
		ask_yn wire_oidc "Sign in to ${joined%, } through Kanidm (recommended on one VPS)?" "y"
	fi

	echo
	echo -e "${BOLD}  Summary${RESET}"
	echo "  Path:       $(stack_label "${STACK}")"
	for row in "${rows[@]}"; do
		IFS='|' read -r name label _ <<<"${row}"
		local state="n" git=""
		if [[ " ${enabled[*]} " == *" ${name} "* ]]; then
			state="y"
			if [[ "$(kit_info "${name}" FOUND)" == "y" ]]; then git=" (update)"; else git=" (clone)"; fi
		fi
		printf '  %-11s %s%s\n' "${label}:" "${state}" "${git}"
	done
	echo "  Identity:   ${wire_oidc}"
	echo "  Git:        --recurse-submodules --branch ${KIT_BRANCH}"
	echo
	echo "  Enabled kits will use proxy.mode: integrate behind shared Caddy."
	echo

	local proceed
	ask_yn proceed "Sync kits, run kit wizards, write engine.yaml, and apply?" "y"
	[[ "${proceed}" == "y" ]] || {
		info "Cancelled."
		exit 0
	}

	for row in "${rows[@]}"; do
		IFS='|' read -r name label _ <<<"${row}"
		if [[ " ${enabled[*]} " == *" ${name} "* ]]; then
			clone_kit "${name}" "${label}"
		fi
	done
	refresh_discover

	# On the Nextcloud path, Nextcloud Mail is the webmail; Stalwart skips Bulwark.
	if [[ "${STACK}" == "nextcloud" ]]; then
		export EASYDEPLOY_WEBMAIL_PROVIDER="Nextcloud Mail"
	else
		unset EASYDEPLOY_WEBMAIL_PROVIDER
	fi

	for row in "${rows[@]}"; do
		IFS='|' read -r name label _ <<<"${row}"
		[[ " ${enabled[*]} " == *" ${name} "* ]] || continue
		run_kit_wizard "${SCRIPT_DIR}/$(kit_info "${name}" PATH)" "${label}" "$(kit_info "${name}" HAS_DEPLOY)"
		refresh_discover
		if [[ "${name}" == "kanidm" && -f "${SCRIPT_DIR}/${KANIDM_PATH}/deploy.yaml" ]]; then
			export EASYDEPLOY_KANIDM_DEPLOY="$(cd "${SCRIPT_DIR}/${KANIDM_PATH}" && pwd)/deploy.yaml"
		fi
		if [[ "${name}" == "stalwart" && -f "${SCRIPT_DIR}/${STALWART_PATH}/deploy.yaml" ]]; then
			export EASYDEPLOY_STALWART_DEPLOY="$(cd "${SCRIPT_DIR}/${STALWART_PATH}" && pwd)/deploy.yaml"
		fi
	done

	local enabled_csv
	enabled_csv="$(IFS=,; echo "${enabled[*]}")"
	uv run python - <<PY
from scripts.config_edit import discover_kits, set_proxy_integrate, update_from_wizard
from pathlib import Path

root = Path(${SCRIPT_DIR@Q})
kits = discover_kits(root)
enabled = [name for name in ${enabled_csv@Q}.split(",") if name]

update_from_wizard(
    enabled=enabled,
    kits=kits,
    wire=${wire_oidc@Q} == "y",
    kit_branch=${KIT_BRANCH@Q} or None,
    stack=${STACK@Q},
    path=Path(${ENGINE_YAML@Q}),
)
for name in enabled:
    kit = next((item for item in kits if item["name"] == name), None)
    if kit and set_proxy_integrate(kit["path"]):
        print(f"Set proxy.mode: integrate on {kit['path'] / 'deploy.yaml'}")
PY

	success "Wrote ${ENGINE_YAML}"

	info "Applying engine (kit stacks + shared Caddy + identity sidecars)…"
	bash "${SCRIPT_DIR}/apply.sh"

	success "Engine wizard finished."
	echo
	echo "  Later: re-run a kit apply, then bash apply.sh here, after domain or kit changes."
	echo "  Or run this wizard again to add a service."
}

main "$@"
