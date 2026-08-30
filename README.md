# Easy Deploy Engine

Shared **Caddy on :443** and **`easydeploy-net`** for running multiple Easy Deploy kits on one VPS.

Each product kit can use `proxy.mode: integrate` to emit a Caddy fragment; this engine assembles them and runs a single `easydeploy_caddy` container.

Kits stay independent: you can still clone [opencloud-easy-deploy](https://github.com/opencomp-eu/opencloud-easy-deploy) (or Kanidm, or Matrix) and run `bash wizard.sh` on its own. The engine is the **one-VPS** path that clones those repos as siblings and runs their wizards for you.

## Identity

[Kanidm](https://kanidm.com/) is the organisation identity source. Users, groups, and authentication live there. Applications do **not** keep their own account databases for normal login.

```
                    EasyDeploy
                        │
                        ▼
                     Kanidm
                  users / groups
                  authentication
                  OIDC / LDAP
                        │
              ┌─────────┼─────────┐
              │         │         │
             OIDC      OIDC      LDAP
              │         │         │
          OpenCloud   Matrix    Stalwart
```

See [docs/integrated-vps.md](docs/integrated-vps.md) and [kanidm-easy-deploy/docs/identity.md](../kanidm-easy-deploy/docs/identity.md).

## Quick start (integrated VPS)

```bash
git clone --recurse-submodules https://github.com/opencomp-eu/easydeploy-engine.git
cd easydeploy-engine
bash wizard.sh
```

The wizard can:

1. Clone Kanidm, OpenCloud, Matrix, and/or Stalwart next to this repo (if they are not already there), or **update existing checkouts** to `engine.kit_branch`, with `git clone --recurse-submodules --branch <branch>`.
2. Run each kit’s `wizard.sh` (domains, admin user, …).
3. Switch those kits to `proxy.mode: integrate`.
4. Apply Kanidm then apps, wire OpenCloud and Matrix OIDC plus Stalwart LDAP, and start shared Caddy.

The clone branch defaults to `feature/engine` (where the engine-aware kit changes live today). After those land on `main`, set `engine.kit_branch: main` in `engine.yaml`, pass `--branch main`, or answer `main` in the wizard.

Re-run a kit `apply.sh`, then engine `apply.sh`, whenever a fragment or domain changes. Re-run `bash wizard.sh` to add a service.

Power users can skip the wizard: write `engine.yaml`, put each service’s `deploy.yaml` in `kits/`, and run `bash apply.sh`.

## Non-interactive (YAML-first)

Same model as a kit: desired state in YAML, `apply.sh` converges.

```bash
git clone --recurse-submodules https://github.com/opencomp-eu/easydeploy-engine.git
cd easydeploy-engine
cp engine.yaml.example engine.yaml
# enable kanidm + opencloud + matrix + stalwart in engine.yaml

# One-time: clone kits so you can copy examples (or let the first apply.sh clone them)
bash apply.sh --ensure-dependencies   # fails until deploy YAML exists — that's ok

cp ../kanidm-easy-deploy/deploy.yaml.example kits/kanidm.yaml
cp ../opencloud-easy-deploy/deploy.yaml.example kits/opencloud.yaml
cp ../matrix-easy-deploy/deploy.yaml.example kits/matrix.yaml
cp ../stalwart-easy-deploy/deploy.yaml.example kits/stalwart.yaml
# edit domains, users, auth in kits/*.yaml  (do not commit secrets)

bash apply.sh
```

`apply.sh` then:

1. Clones missing sibling kits on `engine.kit_branch` (`--sync-kits` also updates existing clones).
2. Copies `kits/<name>.yaml` → `<kit>/deploy.yaml` when that file exists (override with `services.<name>.deploy`).
3. Sets `proxy.mode: integrate` on each kit.
4. Writes identity sidecars, applies Kanidm then apps, starts shared Caddy.

Later changes: edit `kits/opencloud.yaml` (or `engine.yaml`) and run `bash apply.sh` again.

`--skip-kits` only reloads Caddy / identity sidecars without touching kit stacks.

If `kits/<name>.yaml` is absent, the kit’s own `deploy.yaml` is used — so you can still keep config in each repo.

## Kit contract

A kit the engine can clone and run looks like this (Kanidm, OpenCloud, Matrix, and Stalwart already do):

| File | Role |
|------|------|
| `wizard.sh` | Interactive setup; writes `deploy.yaml` |
| `apply.sh` | Converge config and start that kit’s stack |
| `deploy.yaml` | Operator config (created by the wizard) |

`wizard.sh` also accepts `--from-engine` (used by this wizard): set `proxy.mode: integrate` and write `deploy.yaml` without applying — the engine applies in order.

Checkout layout (siblings of this repo), cloned on `engine.kit_branch` (default `feature/engine`):

- `../kanidm-easy-deploy` ← `https://github.com/opencomp-eu/kanidm-easy-deploy.git`
- `../opencloud-easy-deploy` ← `https://github.com/opencomp-eu/opencloud-easy-deploy.git`
- `../matrix-easy-deploy` ← `https://github.com/opencomp-eu/matrix-easy-deploy.git`
- `../stalwart-easy-deploy` ← `https://github.com/opencomp-eu/stalwart-easy-deploy.git`

## `engine.yaml`

See [engine.yaml.example](engine.yaml.example). Each enabled service needs:

- `path` — root of the kit checkout
- `fragment` — relative path to the Caddy snippet (written by that kit’s apply)

### Identity wiring (Kanidm + OpenCloud / Matrix / Stalwart)

When Kanidm and an app kit are enabled, `apply.sh` writes integration sidecars so you do **not** have to paste OIDC/LDAP config by hand:

1. Kanidm OAuth2 client → `.kanidm-easy-deploy/integration/oidc-clients.d/<id>.yaml`
2. OpenCloud IdP settings → `.opencloud-easy-deploy/integration/oidc-provider.yaml`
3. Matrix MAS upstream → `.matrix-easy-deploy/integration/oidc-provider.yaml`
4. Stalwart directory → `.stalwart-easy-deploy/integration/identity-provider.yaml`

Then re-apply Kanidm, then the app kit (the engine wizard does this order for you).

Kanidm OIDC issuers are **per client**: `https://idm.example.com/oauth2/openid/opencloud` and `https://idm.example.com/oauth2/openid/matrix`. Stalwart uses Kanidm LDAP (`ldaps://kanidm:3636`) for IMAP/SMTP identity.

**Same VPS:** kit wizards ask “Use Kanidm on this VPS?” when they find `../kanidm-easy-deploy/deploy.yaml` (the engine wizard auto-accepts).

**Split VPS:** leave the app off this engine; set `auth.oidc` (OpenCloud) or `features.sso` (Matrix) or `identity` (Stalwart) on the other host. On the Kanidm host you can still register a remote client with:

```yaml
identity:
  consumers:
    opencloud:
      domain: cloud.other-vps.example
    matrix:
      domain: matrix.other-vps.example
    stalwart:
      hostname: mail.other-vps.example
      mail_domain: other-vps.example
```

**Opt out:** `identity.wire: false`, or `auth.oidc.managed: false` / `auth.oidc.provider: keycloak` on OpenCloud, or `features.sso.managed: false` / a non-Kanidm `features.sso.providers` list on Matrix, or `identity.managed: false` on Stalwart.

## MVP limits

- Network name must be `easydeploy-net`.
- Warns if known standalone Caddy containers still exist.

## Development

```bash
uv sync --dev
uv run pytest
```

See also [docs/integrated-vps.md](docs/integrated-vps.md) and each kit’s integration docs.
