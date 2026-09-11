#!/usr/bin/env bash
set -euo pipefail
command -v docker >/dev/null 2>&1 || exit 0
docker start easydeploy_caddy >/dev/null 2>&1 || true
