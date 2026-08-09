# Easy Deploy Engine

Shared **Caddy on :443** and **`easydeploy-net`** for running multiple Easy Deploy kits on one VPS.

Each product kit can use `proxy.mode: integrate` to emit a Caddy fragment; this engine assembles them and runs a single `easydeploy_caddy` container.

Standalone deployments (one kit, its own Caddy) are unchanged — do not install the engine on those hosts.

## Quick start (integrated VPS)

1. Clone this repo and each kit you use (with submodules where needed).
2. Copy `engine.yaml.example` → `engine.yaml` and set paths + `enabled: true`.
3. In each kit’s `deploy.yaml`, set `proxy.mode: integrate`.
4. Apply kits first, then the engine:

```bash
cd authelia-easy-deploy && bash apply.sh
cd ../easydeploy-engine && bash ensure-dependencies.sh && bash apply.sh
```

Re-run **engine** `apply.sh` whenever a kit fragment changes.

## `engine.yaml`

See [engine.yaml.example](engine.yaml.example). Each enabled service needs:

- `path` — root of the kit checkout
- `fragment` — relative path to the Caddy snippet (written by that kit’s apply)

## MVP limits

- Network name must be `easydeploy-net`.
- OpenCloud / Matrix integrate mode not implemented yet (placeholders in example only).
- No unified wizard; configure `engine.yaml` and each kit manually.
- Warns if known standalone Caddy containers still exist.

## Development

```bash
uv sync --dev
uv run pytest
```

See also [docs/integrated-vps.md](docs/integrated-vps.md) and each kit’s integration docs.
