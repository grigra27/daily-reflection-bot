# GitHub configuration for CI/CD

The pipeline is defined in `.github/workflows/ci-cd.yml`. To make the deploy
job work you must (1) create a dedicated SSH deploy key, (2) create a
`production` GitHub **Environment** with the variables and secrets below, and
(3) — recommended — publish the GHCR package so the server can pull it
anonymously.

No Telegram token, user IDs, `.env` contents, root password or long-lived GHCR
PAT ever go into the workflow source — they live only in the server-side `.env`
or in protected GitHub Environment values.

---

## 1. Generate a dedicated deployment SSH key

Use a keypair created **just for deployments** — do not reuse your personal SSH
private key.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/daily_reflection_deploy -C "daily-reflection-bot-deploy" -N ""
```

- Private key: `~/.ssh/daily_reflection_deploy` → goes into a GitHub **Secret**.
- Public key:  `~/.ssh/daily_reflection_deploy.pub` → goes into the server's
  `/home/deploy/.ssh/authorized_keys` (see `TIMEWEB_SETUP.md` §4).

Print them:

```bash
cat ~/.ssh/daily_reflection_deploy       # paste into TIMEWEB_SSH_PRIVATE_KEY
cat ~/.ssh/daily_reflection_deploy.pub   # paste into the server's authorized_keys
```

## 2. Collect a `known_hosts` entry (safely)

Do **not** disable host key checking. Obtain the server key and verify its
fingerprint out-of-band (Timeweb control panel / your first interactive login).

```bash
# <server-ip> and port 22 shown as an example
ssh-keyscan -t ed25519,rsa <server-ip> > known_hosts.txt
cat known_hosts.txt          # paste into TIMEWEB_SSH_KNOWN_HOSTS
```

Confirm the printed fingerprints match what the provider/console reports before
trusting them.

## 3. Create the `production` environment

Repository → **Settings → Environments → New environment** → name it exactly:

```text
production
```

### Variables (Settings → Environment → Variables)

| Name | Example / default |
|---|---|
| `TIMEWEB_HOST` | `<server-ip-or-hostname>` |
| `TIMEWEB_SSH_USER` | `deploy` |
| `TIMEWEB_SSH_PORT` | `22` |
| `TIMEWEB_DEPLOY_PATH` | `/opt/daily-reflection-bot` |

(If you leave the last three unset, the workflow defaults to `deploy`, `22`, and
`/opt/daily-reflection-bot`.)

### Secrets (Settings → Environment → Secrets)

| Name | Value |
|---|---|
| `TIMEWEB_SSH_PRIVATE_KEY` | full contents of `~/.ssh/daily_reflection_deploy` |
| `TIMEWEB_SSH_KNOWN_HOSTS` | the verified `known_hosts` line(s) for the server |

Optional: enable **Required reviewers** on the environment so each production
deploy needs manual approval — a good safety gate for the first releases.

## 4. GHCR package visibility (do this after the first publish)

The build job authenticates to GHCR with the built-in `GITHUB_TOKEN`
(`packages: write`), so no PAT is needed to **push**. On the first run, GitHub
creates the package as **private**, which would block the server's anonymous
`docker pull`.

**Recommended — make the package public** (the image contains no secrets and the
repo is already public):

1. Repository → **Packages** → select `daily-reflection-bot`.
2. **Package settings** → **Change visibility** → **Make public** → confirm.

After this the VPS pulls without credentials. Keep the package private only if
you are prepared to `docker login ghcr.io` on the server with a read-only,
long-lived PAT — that is an alternative path, not the default.

## 5. How the pipeline behaves

| Event | test | build+publish | deploy |
|---|---|---|---|
| pull request → main | ✅ | ❌ | ❌ |
| push → main | ✅ | ✅ | ✅ |
| `workflow_dispatch` (manual) on main | ✅ | ✅ | ✅ |

A failed `test` stops everything — no image is published and production is
untouched. Deployment uses the immutable `sha-<full-commit-sha>` tag (never
`latest`), takes a SQLite backup first, verifies the container reached a running
state, and automatically rolls back (restoring the previous image and DB
snapshot) if the new version crashes on startup — while still reporting the run
as **failed**.

## 6. Trigger the first deployment

1. Merge/push to `main` (or run **Actions → CI/CD → Run workflow**).
2. Watch the `deploy` job; on success `deploy.env` on the server points at the
   new SHA tag and the container is running.
3. If it fails, the run shows `deployment failed` / `rollback …` in the log and
   the previous version is restored.
