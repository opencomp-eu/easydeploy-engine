# Renovate (self-hosted)

Renovate keeps the image pins in the kits current. It is not the Mend-hosted
app: [`.github/workflows/renovate.yml`](../.github/workflows/renovate.yml) runs
the open-source Renovate CLI every hour on GitHub Actions. It signs in as a
GitHub App that OpenComp owns, and it can reach only these repositories:

- easydeploy-engine
- kanidm-easy-deploy
- matrix-easy-deploy
- nextcloud-easy-deploy
- opencloud-easy-deploy
- stalwart-easy-deploy

Renovate sends lookups to the registries the images already come from: Docker
Hub, ghcr.io, dock.mau.dev, PyPI, and GitHub.

The rules live in [`renovate/easydeploy.json`](../renovate/easydeploy.json).
Each repository's `renovate.json` extends them.

## One-time setup

### 1. Create the GitHub App

Go to **github.com/organizations/opencomp-eu/settings/apps** and choose
**New GitHub App**.

- **Name:** something like `opencomp-renovate`.
- **Homepage URL:** the engine repository.
- **Webhook:** clear **Active**. The workflow runs on a schedule, so no webhook
  is needed.
- **Repository permissions:**

  | Permission | Access |
  |---|---|
  | Checks | Read |
  | Commit statuses | Read and write |
  | Contents | Read and write |
  | Dependabot alerts | Read (vulnerability pull requests) |
  | Issues | Read and write (Dependency Dashboard issue) |
  | Metadata | Read (mandatory) |
  | Pull requests | Read and write |
  | Workflows | Read and write (lets it bump the actions in `.github/workflows`) |

- **Where can this GitHub App be installed:** *Only on this account*.

After you create it:

1. Note the **Client ID**.
2. Under **Private keys**, choose **Generate a private key**. This downloads a
   `.pem` file.

### 2. Install the app

On the app's page, choose **Install App** and pick `opencomp-eu`. Select
**Only select repositories** and pick the six repositories above.

### 3. Give the workflow the credentials

In `opencomp-eu/easydeploy-engine`, go to **Settings → Secrets and variables →
Actions**:

- **Variables** → `RENOVATE_APP_CLIENT_ID` = the Client ID
- **Secrets** → `RENOVATE_APP_PRIVATE_KEY` = the full contents of the `.pem` file

Then delete the downloaded `.pem` file.

### 4. Run it once

Go to **Actions → Renovate → Run workflow**. Pick log level `debug` if you want
to see what it found.

Each repository gets a **Dependency Dashboard** issue that lists pending and
held-back updates. Renovate opens pull requests from then on.

## How updates land

- **Merged on their own:** patch, minor, and digest updates, once CI passes and
  the release is three days old. Renovate merges them itself on a later hourly
  run. It does not use GitHub's auto-merge, so it never merges before the
  checks finish.
- **Labelled `needs-review` for a person:**
  - major versions
  - Kanidm minor versions
  - minor versions of anything still at 0.x, such as Stalwart
  - `easydeploy-lib` submodule bumps
- **Never proposed:** Postgres major versions. A new major needs a dump and
  restore, not a tag bump.
- **How many at once:** at most 10 open Renovate pull requests per repository.
  There is no hourly cap, because the workflow itself only runs once an hour.
  Ticking a box on the Dependency Dashboard takes effect on the next run, or
  start one from **Actions → Renovate → Run workflow**.
- **Branch protection:** a rule that requires approving reviews on `master`
  blocks automerge. Required status checks are fine.

Whatever reaches `master` is picked up by every server on its next nightly
`update.sh --unattended` run.

## Rotating the key

1. On the app's page, generate a new private key.
2. Replace the `RENOVATE_APP_PRIVATE_KEY` secret with it.
3. Delete the old key from the app.
