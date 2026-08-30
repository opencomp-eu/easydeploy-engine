# Integrated VPS (one host, many services)

## Architecture

- **easydeploy-engine** — one Caddy, ports 80/443, Docker network `easydeploy-net`
- **Kanidm** — organisation identity (users, groups, OIDC, LDAP)
- **Product kits** — own Compose stacks; in `integrate` mode they join `easydeploy-net` and write a Caddy fragment under their state dir

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

Kanidm is the source of truth. Do not create independent accounts in the applications.

## Apply order

Preferred: `bash wizard.sh` in **easydeploy-engine**, or non-interactive `engine.yaml` + `kits/*.yaml` + `bash apply.sh`.

Standalone kits are unchanged: clone `opencloud-easy-deploy` (or Kanidm) anywhere and run `bash wizard.sh` there.

Manual equivalent:

1. Clone kits as siblings of the engine (or let the engine wizard clone them).
2. Create `engine.yaml` and enable services.
3. Configure each enabled kit's `deploy.yaml` (or `kits/<name>.yaml`).
4. Run `bash apply.sh` in **easydeploy-engine**. It writes identity sidecars,
   forces integrate mode, applies Kanidm first, applies consumer kits, and
   finally reloads shared Caddy.
5. After changing domains or adding a service, re-run the engine apply.

## Kanidm on your existing box

If Kanidm already runs in **standalone** mode with `kanidm_caddy`, `bash wizard.sh` in the engine will switch it to integrate and start shared Caddy. Manual equivalent:

1. Edit `deploy.yaml`: `proxy.mode: integrate`
2. `bash apply.sh` in kanidm-easy-deploy (stops `kanidm_caddy`, joins `easydeploy-net`)
3. Enable Kanidm in `engine.yaml`, then `bash apply.sh` in easydeploy-engine

DNS stays the same (`idm.yourdomain` → this host). Only the container serving TLS changes.

## Identity wiring

When Kanidm and OpenCloud, Matrix, or Stalwart are enabled, the engine writes **integration sidecars** (it does not rewrite your `deploy.yaml` files):

- Kanidm client: `<kanidm>/.kanidm-easy-deploy/integration/oidc-clients.d/<id>.yaml`
- OpenCloud IdP: `<opencloud>/.opencloud-easy-deploy/integration/oidc-provider.yaml`
- Matrix MAS: `<matrix>/.matrix-easy-deploy/integration/oidc-provider.yaml`
- Stalwart directory: `<stalwart>/.stalwart-easy-deploy/integration/identity-provider.yaml`

Apply order after enabling both (handled by engine `wizard.sh` and `apply.sh`):

1. Engine writes identity sidecars.
2. Engine applies Kanidm, which registers OAuth2 clients.
3. Engine applies OpenCloud / Matrix / Stalwart, which consume provider sidecars.
4. Engine assembles and reloads shared Caddy.

Kanidm OIDC issuers are per client, for example `https://idm.example.com/oauth2/openid/opencloud`. Stalwart selects the Kanidm OIDC directory (`stalwart-webui`) so Bulwark can SSO. IMAP/SMTP password binds are rejected by that directory; use a Stalwart app password for Thunderbird or phones. Set `identity.auth_directory: ldap` on the Stalwart kit to keep Kanidm-password login instead. The portal **Webmail** tile lands on Bulwark.

**Same VPS, no YAML client block:** kit wizards ask “Use Kanidm on this VPS?” when they find `../kanidm-easy-deploy/deploy.yaml`.

**Split VPS:** OpenCloud `deploy.yaml` keeps `auth.oidc` pointing at the remote Kanidm issuer; Matrix keeps `features.sso.provider: kanidm`. On the Kanidm host, either add the client in `deploy.yaml` or:

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

**Opt out of wiring:** `identity.wire: false`, OpenCloud `auth.oidc.managed: false` / `provider: keycloak`, Matrix `features.sso.managed: false` / a non-Kanidm provider list, or Stalwart `identity.managed: false`.

## Troubleshooting

| Symptom | Check |
|--------|--------|
| Engine removes Kanidm containers | Both kits used Compose project name `compose` (directory basename). Fixed: unique `COMPOSE_PROJECT_NAME` per kit. Re-apply Kanidm, then engine. |
| `SSL_ERROR_INTERNAL_ERROR_ALERT` on a new domain | Caddy was not reloaded after adding a kit fragment. Run `bash apply.sh` in easydeploy-engine (reloads Caddy), or `docker exec easydeploy_caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile`. Also confirm DNS A/AAAA for that domain. |
| Engine apply: missing fragment | Run kit `apply.sh` with `integrate` mode first |
| :443 already in use | Stop standalone `kanidm_caddy` / `authelia_caddy` / Matrix `caddy` / OpenCloud Caddy / `stalwart_caddy` |
| 502 from Caddy | Kit container on `easydeploy-net`? `docker network inspect easydeploy-net` |
| App login does not reach Kanidm | Confirm the issuer is the **client-specific** URL (`/oauth2/openid/<client_id>`), then re-apply Kanidm then the app kit |
