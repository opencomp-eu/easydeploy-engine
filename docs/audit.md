# Audit log

The audit log answers questions about the workspace on **this VPS** after something has gone wrong: which files were opened, who signed in, who sent mail, who joined a room, and when. It is a record of actions, not a copy of the files, messages, or mail.

It is part of easydeploy-engine, not of the Kanidm admin UI. Creating accounts and reading the audit log are different jobs. A later console for the whole stack can read this same store. It should not be an iframe inside Kanidm.

## What you can ask

```bash
bash audit.sh query --action file.read --since 2026-09-01
bash audit.sh query --actor alice --since 2026-10-10
bash audit.sh query --actor alice --object Contracts/acme.pdf
bash audit.sh query --source kanidm --action auth.failure --since 2026-10-01
bash audit.sh query --source stalwart --action mail.send --actor alice@example.com
bash audit.sh query --source matrix --action chat.join
bash audit.sh export --since 2026-09-01 -o /root/incident.jsonl
bash audit.sh verify
bash audit.sh status
```

Queries and exports are themselves recorded (`audit.query`, `audit.export`), including the SSH user and client address. `file.*` and `auth.*` match a prefix.

`--actor alice` is the Kanidm username. OpenCloud records file access under its own account id (a UUID), not that name. The query reads OpenCloud's user directory and matches the id, the username, and the mail address for that person. The table shows the username. `--json` keeps the stored id in `actor` and adds `actor_name`. Kanidm, Stalwart, and Matrix events already carry the name or address, and those still match.

| Action | Meaning |
| --- | --- |
| `file.read` | OpenCloud served the file (open, preview, or download). It does not say whether the person saved a copy. |
| `file.*` | Other file actions OpenCloud recorded (create, trash, purge, move, restore). The name is OpenCloud's, with underscores written as dots. |
| `share.*`, `public_link.*` | Share and link actions, named the same way. Link tokens are removed. |
| `auth.login`, `auth.failure` | Sign-in succeeded or failed, where the app records it. |
| `mail.send` | An authenticated user submitted a message. Envelope only. |
| `mail.receive`, `mail.delivery_failure` | A message was delivered, or delivery failed. |
| `chat.login`, `chat.join`, `chat.leave`, `chat.kick`, `chat.ban` | Matrix login or room membership. Not message content. |
| `identity.user_create`, `identity.credential_change` | Seen in Kanidm's log when that log line says so. |

Times are UTC. When the application supplies a timestamp it is kept. OpenCloud often does not; those events are stamped when the collector reads them, which is about a minute later.

## Where each fact comes from

| Application | What is collected | What you will not find |
| --- | --- | --- |
| OpenCloud | The built-in audit service: file reads and other file and share events, as JSON. | A split between preview and download. File contents. |
| Kanidm | Security lines from the server log: authentication success and failure, account creation, credential changes. | A full history of every group edit. Kanidm does not yet publish an audit stream. Lines that do not match those events are dropped. |
| Stalwart | Successful authentication, authenticated message submission, delivery, lockouts, and quota failures. | Failed passwords. Stalwart records those at debug, and debug also records every IMAP command. Subjects and bodies. |
| Matrix | Synapse logins and room membership. MAS login lines when they name the account. | Message contents. Encrypted messages are not on the server. A read receipt is not an audit event. |

Caddy access logs are not collected. URLs can carry tokens, and they do not name the person or the file.

## How it is stored

```
.easydeploy-engine/audit/          mode 0700, root operator
  store/events-YYYY-MM-DD.jsonl    the evidence
  index.sqlite                     rebuilt with: bash audit.sh rebuild-index
  spool/<app>/                     apps append here; they cannot write the store
```

Each line includes the hash of the previous line. `bash audit.sh verify` fails if a line was edited or removed outside of retention. The apps can append to their own spool. They cannot rewrite a day file in the store.

Docker json logs for Kanidm, Stalwart, and MAS are read in place and rotated by Docker (20 MB × 5 files). If the collector is stopped longer than that buffer, those streams lose the oldest lines. OpenCloud and Synapse write files the collector drains, so they are not limited by that Docker buffer.

The directory lives inside `.easydeploy-engine`, so the engine Borg backup includes it. Restoring that backup rewinds the audit log to the archive. Before a restore during an incident, export:

```bash
bash audit.sh export --since 2026-01-01 -o /root/audit-before-restore.jsonl
```

## How long it is kept

Default is **365 days**. That is long enough to investigate a breach found months later, and short enough to explain under a storage-limitation duty. Set `audit.retention_days` from 30 to 2555 (seven years) when a contract or a regulator requires a different period.

```yaml
audit:
  enabled: true
  retention_days: 365
  hold: false
```

`hold: true` stops deletion. Collection runs about once a minute (`easydeploy-audit.timer`), or on `bash audit.sh ingest`. The collector reads `engine.yaml` on each pass, so a retention or hold change applies within about a minute. `bash update.sh` and `bash apply.sh` install or remove the timer. Re-apply when you enable or disable audit, so OpenCloud's audit service follows. Pruning removes whole days older than the window and leaves the hash chain verifiable.

Anyone who can run commands as root on the VPS can read and export the log. That is the same trust as SSH to the machine. Do not copy the store to a laptop without a reason; it is personal data (names, file paths, IP addresses).

## Turning it off

```yaml
audit:
  enabled: false
```

Re-apply the engine. The timer is removed. Existing evidence is left on disk until you delete `.easydeploy-engine/audit` yourself. Synapse still writes login and membership lines to its spool file; that file is not the evidence store.

## Trying this on a running VPS

From a checkout that already has `update.sh --branch`:

```bash
bash update.sh --branch feature/auditing
```

That checks the same branch out on the engine and the enabled kits, then applies. Go back with `bash update.sh --branch master`. The first switch off `master`, before this flag exists there, is `git fetch origin && git checkout feature/auditing && bash update.sh --branch feature/auditing`.
