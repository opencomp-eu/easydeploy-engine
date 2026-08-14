# Integrated VPS (one host, many services)

## Architecture

- **easydeploy-engine** — one Caddy, ports 80/443, Docker network `easydeploy-net`
- **Product kits** — own Compose stacks; in `integrate` mode they join `easydeploy-net` and write a Caddy fragment under their state dir

## Apply order

1. Create `engine.yaml` and enable services.
2. Set each kit `proxy.mode: integrate` in `deploy.yaml`.
3. `bash apply.sh` in **Authelia**, then **OpenCloud** (integrate mode), then other kits as they gain integrate support.
4. `bash apply.sh` in **easydeploy-engine**.
5. After changing domains or adding a service: re-apply the kit, then re-apply the engine.

## Authelia on your existing box

If Authelia already runs in **standalone** mode with `authelia_caddy`:

1. Edit `deploy.yaml`: `proxy.mode: integrate`
2. `bash apply.sh` in authelia-easy-deploy (stops `authelia_caddy`, joins `easydeploy-net`)
3. Enable Authelia in `engine.yaml`, then `bash apply.sh` in easydeploy-engine

DNS stays the same (`auth.yourdomain` → this host). Only the container serving TLS changes.

## OIDC

Still configured per kit (`deploy.yaml`). The engine does not merge OIDC settings. Add `oidc.clients` in Authelia when enabling OpenCloud/Matrix.

## Troubleshooting

| Symptom | Check |
|--------|--------|
| Engine removes Authelia containers | Both kits used Compose project name `compose` (directory basename). Fixed: unique `COMPOSE_PROJECT_NAME` per kit. Re-apply Authelia, then engine. |
| `SSL_ERROR_INTERNAL_ERROR_ALERT` on a new domain | Caddy was not reloaded after adding a kit fragment. Run `bash apply.sh` in easydeploy-engine (reloads Caddy), or `docker exec easydeploy_caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile`. Also confirm DNS A/AAAA for that domain. |
| Engine apply: missing fragment | Run kit `apply.sh` with `integrate` mode first |
| :443 already in use | Stop standalone `authelia_caddy` / Matrix `caddy` / OpenCloud Caddy |
| 502 from Caddy | Kit container on `easydeploy-net`? `docker network inspect easydeploy-net` |
