# Easy Deploy Engine

Shared **Caddy on :443** and **`easydeploy-net`** for running multiple Easy Deploy kits on one VPS.

Each product kit can use `proxy.mode: integrate` to emit a Caddy fragment; this engine assembles them and runs a single `easydeploy_caddy` container.

Kits stay independent: you can still clone [opencloud-easy-deploy](https://github.com/opencomp-eu/opencloud-easy-deploy) (or Authelia) and run `bash wizard.sh` on its own. The engine is the **one-VPS** path that clones those repos as siblings and runs their wizards for you.

## Quick start (integrated VPS)

```bash
git clone --recurse-submodules https://github.com/opencomp-eu/easydeploy-engine.git
cd easydeploy-engine
bash wizard.sh
```

The wizard can:

1. Clone Authelia and OpenCloud next to this repo (if they are not already there) with `git clone --recurse-submodules --branch <branch>`.
2. Run each kit’s `wizard.sh` (domains, admin user, …).
3. Switch those kits to `proxy.mode: integrate`.
4. Apply Authelia then apps, wire OpenCloud OIDC sidecars, and start shared Caddy.

The clone branch defaults to `feature/engine` (where the engine-aware kit changes live today). After those land on `main`, set `engine.kit_branch: main` in `engine.yaml`, pass `--branch main`, or answer `main` in the wizard.

Re-run a kit `apply.sh`, then engine `apply.sh`, whenever a fragment or domain changes. Re-run `bash wizard.sh` to add a service.

Power users can copy `engine.yaml.example` → `engine.yaml` and apply kits + engine by hand instead.

## Kit contract

A kit the engine can clone and run looks like this (Authelia and OpenCloud already do):

| File | Role |
|------|------|
| `wizard.sh` | Interactive setup; writes `deploy.yaml` |
| `apply.sh` | Converge config and start that kit’s stack |
| `deploy.yaml` | Operator config (created by the wizard) |

`wizard.sh` also accepts `--from-engine` (used by this wizard): set `proxy.mode: integrate` and write `deploy.yaml` without applying — the engine applies in order.

Checkout layout (siblings of this repo), cloned on `engine.kit_branch` (default `feature/engine`):

- `../authelia-easy-deploy` ← `https://github.com/opencomp-eu/authelia-easy-deploy.git`
- `../opencloud-easy-deploy` ← `https://github.com/opencomp-eu/opencloud-easy-deploy.git`

Matrix is discovered if already cloned (`../matrix-easy-deploy`); the engine does not clone it yet. That kit’s standard entrypoint is also `wizard.sh` (wraps `matrix-wizard.sh`).

## `engine.yaml`

See [engine.yaml.example](engine.yaml.example). Each enabled service needs:

- `path` — root of the kit checkout
- `fragment` — relative path to the Caddy snippet (written by that kit’s apply)

### OIDC wiring (Authelia + OpenCloud)

When Authelia and OpenCloud are both enabled, `apply.sh` writes integration sidecars so you do **not** have to paste OIDC client YAML by hand:

1. Authelia client → `.authelia-easy-deploy/integration/oidc-clients.d/opencloud.yaml`
2. OpenCloud IdP settings → `.opencloud-easy-deploy/integration/oidc-provider.yaml`

Then re-apply Authelia, then OpenCloud (the engine wizard does this order for you).

**Split VPS:** leave OpenCloud off this engine; set `auth.oidc` on the OpenCloud host as today. On the Authelia host you can still register a remote client with:

```yaml
identity:
  consumers:
    opencloud:
      domain: cloud.other-vps.example
```

**Opt out:** `identity.wire: false`, or `auth.oidc.managed: false` / `auth.oidc.provider: keycloak` on OpenCloud.

## MVP limits

- Network name must be `easydeploy-net`.
- Matrix OIDC wiring is not implemented yet (OpenCloud is).
- Warns if known standalone Caddy containers still exist.

## Development

```bash
uv sync --dev
uv run pytest
```

See also [docs/integrated-vps.md](docs/integrated-vps.md) and each kit’s integration docs.

