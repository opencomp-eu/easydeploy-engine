# Integrated VPS (one host, many services)

## Architecture

- **easydeploy-engine** — one Caddy, ports 80/443, Docker network `easydeploy-net`
- **Product kits** — own Compose stacks; in `integrate` mode they join `easydeploy-net` and write a Caddy fragment under their state dir

## Apply order

Preferred: `bash wizard.sh` in **easydeploy-engine**, or non-interactive `engine.yaml` + `kits/*.yaml` + `bash apply.sh`.

Standalone kits are unchanged: clone `opencloud-easy-deploy` (or Authelia) anywhere and run `bash wizard.sh` there.

Manual equivalent:

1. Clone kits as siblings of the engine (or let the engine wizard clone them).
2. Create `engine.yaml` and enable services.
3. Set each kit `proxy.mode: integrate` in `deploy.yaml`.
4. `bash apply.sh` in **Authelia**, then **OpenCloud** / **Matrix** (integrate mode).
5. `bash apply.sh` in **easydeploy-engine**.
6. After changing domains or adding a service: re-apply the kit, then re-apply the engine.

## Authelia on your existing box

If Authelia already runs in **standalone** mode with `authelia_caddy`, `bash wizard.sh` in the engine will switch it to integrate and start shared Caddy. Manual equivalent:

1. Edit `deploy.yaml`: `proxy.mode: integrate`
2. `bash apply.sh` in authelia-easy-deploy (stops `authelia_caddy`, joins `easydeploy-net`)
3. Enable Authelia in `engine.yaml`, then `bash apply.sh` in easydeploy-engine

DNS stays the same (`auth.yourdomain` → this host). Only the container serving TLS changes.

## OIDC

When Authelia and OpenCloud or Matrix are enabled, the engine writes **integration sidecars** (it does not rewrite your `deploy.yaml` files):

- Authelia client: `<authelia>/.authelia-easy-deploy/integration/oidc-clients.d/<id>.yaml`
- OpenCloud IdP: `<opencloud>/.opencloud-easy-deploy/integration/oidc-provider.yaml`
- Matrix MAS: `<matrix>/.matrix-easy-deploy/integration/oidc-provider.yaml`

Apply order after enabling both (handled by `bash wizard.sh`):

1. Kit apply (fragments + stacks)
2. Engine apply (Caddy + OIDC sidecars)
3. Authelia apply, then OpenCloud / Matrix apply (consume sidecars)

**Same VPS, no YAML client block:** kit wizards ask “Use Authelia on this VPS?” when they find `../authelia-easy-deploy/deploy.yaml`.

**Split VPS:** OpenCloud `deploy.yaml` keeps `auth.oidc` pointing at the remote Authelia issuer; Matrix keeps `features.sso.provider: authelia`. On the Authelia host, either add the client in `deploy.yaml` or:

```yaml
identity:
  consumers:
    opencloud:
      domain: cloud.other-vps.example
    matrix:
      domain: matrix.other-vps.example
```

**Opt out of wiring:** `identity.wire: false`, OpenCloud `auth.oidc.managed: false` / `provider: keycloak`, or Matrix `features.sso.managed: false` / a non-Authelia provider list.

## Troubleshooting

| Symptom | Check |
|--------|--------|
| Engine removes Authelia containers | Both kits used Compose project name `compose` (directory basename). Fixed: unique `COMPOSE_PROJECT_NAME` per kit. Re-apply Authelia, then engine. |
| `SSL_ERROR_INTERNAL_ERROR_ALERT` on a new domain | Caddy was not reloaded after adding a kit fragment. Run `bash apply.sh` in easydeploy-engine (reloads Caddy), or `docker exec easydeploy_caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile`. Also confirm DNS A/AAAA for that domain. |
| Engine apply: missing fragment | Run kit `apply.sh` with `integrate` mode first |
| :443 already in use | Stop standalone `authelia_caddy` / Matrix `caddy` / OpenCloud Caddy |
| 502 from Caddy | Kit container on `easydeploy-net`? `docker network inspect easydeploy-net` |
