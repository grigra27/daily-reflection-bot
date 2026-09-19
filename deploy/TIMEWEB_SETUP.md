# Timeweb VPS — one-time server setup

This is the manual, one-time preparation of the production server. After it is
done, every future release is deployed automatically by GitHub Actions — the
server never runs `git clone`, `git pull`, `pip install` or builds from source.
It only pulls a pre-built Docker image and restarts the container.

Target layout (owned by the `deploy` user):

```text
/opt/daily-reflection-bot/
├── docker-compose.prod.yml   # uploaded by CI
├── deploy_remote.sh          # uploaded by CI
├── deploy.env                # written by CI (deployment metadata: IMAGE_REF)
├── .env                      # YOU create this — application secrets
├── data/
│   └── reflection.db         # persistent SQLite (survives redeploys)
└── backups/                  # automatic SQLite snapshots
```

> A convenience script, `deploy/bootstrap_server.sh`, performs most of the
> steps below idempotently. Read it first and run it as root if you prefer.
> The manual steps are documented here as well.

---

## 0. Requirements

- A Linux VPS (Timeweb Cloud) you can SSH into as root once for setup.
- Docker Engine and the Docker Compose plugin installed.
- Enough disk for the image, the database and a rolling set of backups.

Root is used **only** for this one-time bootstrap. Day-to-day deployments run
as the unprivileged `deploy` user over SSH and never need root or a password.

## 1. Connect as root (setup only)

```bash
ssh root@<server-ip>
```

## 2. Install Docker + Compose plugin (if missing)

```bash
docker --version || curl -fsSL https://get.docker.com | sh
docker compose version   # must be present; if not, install docker-compose-plugin
```

## 3. Create the deploy user

```bash
adduser --disabled-password --gecos "" deploy      # Debian/Ubuntu
# or: useradd -m -s /bin/bash deploy && passwd -l deploy

usermod -aG docker deploy    # lets 'deploy' run docker without sudo
```

## 4. Authorize the deployment SSH key

Generate a **dedicated** deploy keypair on your workstation (do not reuse your
personal key — see `GITHUB_SETUP.md`), then install the public key:

```bash
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
echo "<contents of daily_reflection_deploy.pub>" >> /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
```

## 5. Create the deploy directory

```bash
install -d -m 755 -o deploy -g deploy /opt/daily-reflection-bot
install -d -m 750 -o deploy -g deploy /opt/daily-reflection-bot/data
install -d -m 750 -o deploy -g deploy /opt/daily-reflection-bot/backups
```

## 6. Create the production application `.env` (secrets — manual, once)

As the `deploy` user (or root, then chown), create `/opt/daily-reflection-bot/.env`
from the template `deploy/.env.production.example`:

```bash
cat > /opt/daily-reflection-bot/.env <<'ENVC'
TELEGRAM_BOT_TOKEN=<token from BotFather>
ALLOWED_TELEGRAM_IDS=<id1>,<id2>
DEFAULT_TIMEZONE=Europe/Moscow
DEFAULT_CHECKIN_TIME=21:30
DEFAULT_REMINDER_TIME=23:00
LOG_LEVEL=INFO
DATABASE_URL=sqlite:////app/data/reflection.db
ENVC

chown deploy:deploy /opt/daily-reflection-bot/.env
chmod 600 /opt/daily-reflection-bot/.env
```

This `.env` is server-only. GitHub Actions never reads, uploads, downloads or
logs it. Keep it out of git.

> `DATABASE_URL` must be the container path `sqlite:////app/data/reflection.db`
> (four slashes). `/app/data` is bind-mounted to `/opt/daily-reflection-bot/data`,
> so the database persists across redeploys and container recreation.

## 7. Fix data directory ownership for the container

The image runs as an unprivileged user with UID **10001**. The bind-mounted
`data/` directory must be writable by it (needed the first time the DB file is
created):

```bash
chown -R 10001:10001 /opt/daily-reflection-bot/data
```

## 8. Make the GHCR image pullable

The repository is public and the image contains no runtime secrets, so the
recommended path is to mark the GHCR package **public** after the first publish.
Then the server can pull anonymously and needs **no** long-lived PAT:

```bash
docker pull ghcr.io/grigra27/daily-reflection-bot:latest   # smoke-test from the server
```

See `GITHUB_SETUP.md` §GHCR for how to flip the visibility. If you keep the
package private instead, you must `docker login ghcr.io` on the server with a
read-only PAT before pulls — this is **not** the default path.

## 9. First deployment

There is nothing to deploy manually — the first push to `main` (or a manual
`workflow_dispatch`) runs the pipeline and creates `deploy.env`, uploads the
compose file, and starts the container. To confirm the environment afterwards:

```bash
cd /opt/daily-reflection-bot
cat deploy.env                                   # should hold IMAGE_REF=...
docker compose --env-file deploy.env -f docker-compose.prod.yml ps
docker compose --env-file deploy.env -f docker-compose.prod.yml logs --tail=50
```

Once running, message the bot `/start` in Telegram so it may send check-ins.

## 10. Verification checklist

- [ ] `deploy` user exists, in `docker` group, key-based SSH works without password.
- [ ] `/opt/daily-reflection-bot/{data,backups}` exist, owned by `deploy`.
- [ ] `.env` exists, `chmod 600`, owned by `deploy`, never in git.
- [ ] `docker compose version` works for the `deploy` user (group membership).
- [ ] GHCR package is public (or server is logged in) so `pull` succeeds.
- [ ] Firewall exposes only what your normal admin policy requires — the bot
      uses Telegram long polling and opens **no inbound application port**.

## Manual redeploy / rollback (emergency)

If you need to act directly on the server (CI already rolls back automatically
on failed deploys):

```bash
cd /opt/daily-reflection-bot
# restore a known-good database snapshot:
cp backups/reflection-<timestamp>.db data/reflection.db && rm -f data/reflection.db-wal data/reflection.db-shm
# pin a previous image:
printf 'IMAGE_REF=ghcr.io/grigra27/daily-reflection-bot:sha-<old-sha>\n' > deploy.env
docker compose --env-file deploy.env -f docker-compose.prod.yml up -d --remove-orphans
```
