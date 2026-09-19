#!/usr/bin/env bash
#
# One-time, manual server bootstrap for the Daily Reflection Bot.
# Run as root (or with sudo) ONCE on a fresh Timeweb VPS:
#
#   sudo DEPLOY_PUBKEY="$(cat ~/.ssh/daily_reflection_deploy.pub)" \
#     bash bootstrap_server.sh
#
# This script is idempotent, never deletes application data, and never creates
# real secrets. It is NOT run by GitHub Actions.
#
# What it does:
#   - creates the 'deploy' user (no password, SSH-key login only)
#   - installs the provided deploy public key in authorized_keys
#   - verifies Docker + the Compose plugin are available
#   - adds 'deploy' to the docker group
#   - creates /opt/daily-reflection-bot with data/ and backups/
#   - grants shared ./data access via POSIX ACLs (host 'deploy' + container UID)
#   - prints the remaining MANUAL steps (create .env, GHCR, GitHub secrets)

set -Eeuo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"
DEPLOY_PATH="${DEPLOY_PATH:-/opt/daily-reflection-bot}"
DEPLOY_PUBKEY="${DEPLOY_PUBKEY:-}"
APP_UID="${APP_UID:-10001}"   # unprivileged 'appuser' inside the container

log() { printf '\n==> %s\n' "$*"; }

if [[ "$(id -u)" != "0" ]]; then
  echo "This script must be run as root (use sudo)." >&2
  exit 1
fi

if [[ -z "$DEPLOY_PUBKEY" ]]; then
  cat >&2 <<'EOF'
DEPLOY_PUBKEY is required. Pass the deploy user's SSH *public* key, e.g.:

  sudo DEPLOY_PUBKEY="$(cat ~/.ssh/daily_reflection_deploy.pub)" bash bootstrap_server.sh

Generate a dedicated deploy key first if you have not:
  ssh-keygen -t ed25519 -f ~/.ssh/daily_reflection_deploy -C "daily-reflection-deploy"
EOF
  exit 1
fi

# --- deploy user ----------------------------------------------------------

log "Ensuring deploy user '${DEPLOY_USER}' exists"
if id "$DEPLOY_USER" >/dev/null 2>&1; then
  echo "user already exists"
else
  adduser --disabled-password --gecos "" "$DEPLOY_USER" || \
    useradd -m -s /bin/bash "$DEPLOY_USER"
  passwd -l "$DEPLOY_USER" || true
fi

log "Installing deploy public key"
HOME_DIR="$(getent passwd "$DEPLOY_USER" | cut -d: -f6)"
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "${HOME_DIR}/.ssh"
touch "${HOME_DIR}/.ssh/authorized_keys"
if ! grep -qxF "$DEPLOY_PUBKEY" "${HOME_DIR}/.ssh/authorized_keys"; then
  printf '%s\n' "$DEPLOY_PUBKEY" >> "${HOME_DIR}/.ssh/authorized_keys"
  echo "public key added"
else
  echo "public key already present"
fi
chmod 600 "${HOME_DIR}/.ssh/authorized_keys"
chown "$DEPLOY_USER:$DEPLOY_USER" "${HOME_DIR}/.ssh/authorized_keys"

# --- Docker ---------------------------------------------------------------

log "Checking Docker + Compose plugin"
if ! command -v docker >/dev/null 2>&1; then
  cat >&2 <<'EOF'
Docker is not installed. Install it first (see deploy/TIMEWEB_SETUP.md), for example:
  curl -fsSL https://get.docker.com | sh
Then re-run this script.
EOF
  exit 1
fi
docker --version
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is missing. Install the 'docker-compose-plugin' package." >&2
  exit 1
fi
docker compose version

log "Adding ${DEPLOY_USER} to the docker group"
usermod -aG docker "$DEPLOY_USER"

# --- deploy directory -----------------------------------------------------

log "Creating ${DEPLOY_PATH} layout (never deletes existing data)"
install -d -m 755 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$DEPLOY_PATH"
install -d -m 750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "${DEPLOY_PATH}/data"
install -d -m 750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "${DEPLOY_PATH}/backups"

# --- shared ./data access via POSIX ACLs ----------------------------------
# The bind-mounted ./data is written by the container (UID ${APP_UID}) but must
# also be readable/restorable by the host 'deploy' user that runs the CI deploy
# script. Two principals need access; chmod 777 is NOT acceptable, so use ACLs.
# 'data' stays owned by 'deploy'; the container gets write access purely via ACL.
log "Checking setfacl (POSIX ACL) availability"
if ! command -v setfacl >/dev/null 2>&1; then
  cat >&2 <<'EOF'
setfacl is missing but required for safe shared access to ./data.
Install the 'acl' package and re-run, e.g.:
  apt-get install -y acl      # Debian/Ubuntu
  dnf install -y acl          # RHEL/CentOS/Rocky
EOF
  exit 1
fi

log "Granting host '${DEPLOY_USER}' and container UID '${APP_UID}' access to ${DEPLOY_PATH}/data"
# Existing directory: traverseable+writable by both, nobody else.
setfacl -m "u:${DEPLOY_USER}:rwx,u:${APP_UID}:rwx" "${DEPLOY_PATH}/data"
# Default ACL: files the container creates (reflection.db, -wal, -shm) inherit
# rw for both principals and no world access.
setfacl -m "d:u:${DEPLOY_USER}:rw-,d:u:${APP_UID}:rw-,d:o::---" "${DEPLOY_PATH}/data"
# Apply to any pre-existing content (idempotent; empty on a fresh install).
find "${DEPLOY_PATH}/data" -mindepth 1 -type d -exec setfacl -m "u:${DEPLOY_USER}:rwx,u:${APP_UID}:rwx" {} + 2>/dev/null || true
find "${DEPLOY_PATH}/data" -mindepth 1 -type f -exec setfacl -m "u:${DEPLOY_USER}:rw-,u:${APP_UID}:rw-" {} + 2>/dev/null || true
getfacl -p "${DEPLOY_PATH}/data" 2>/dev/null || true

cat <<EOF

${DEPLOY_PATH}/
├── (docker-compose.prod.yml, deploy_remote.sh — uploaded by CI)
├── deploy.env   (written by CI)
├── .env         (you create this — manual)
├── data/
└── backups/
EOF

log "Done. Remaining MANUAL steps:"
cat <<EOF
1. Create the production application secrets file (do NOT store it in git):
     cat > ${DEPLOY_PATH}/.env <<'ENVC'
TELEGRAM_BOT_TOKEN=<token from BotFather>
ALLOWED_TELEGRAM_IDS=<id1>,<id2>
DEFAULT_TIMEZONE=Europe/Moscow
DEFAULT_CHECKIN_TIME=21:30
DEFAULT_REMINDER_TIME=23:00
LOG_LEVEL=INFO
DATABASE_URL=sqlite:////app/data/reflection.db
ENVC
     chmod 600 ${DEPLOY_PATH}/.env
     chown ${DEPLOY_USER}:${DEPLOY_USER} ${DEPLOY_PATH}/.env
     (see deploy/.env.production.example in the repository for reference)

2. After the first image is published, make the GHCR package public
   (recommended) so the server can pull it anonymously. See deploy/GITHUB_SETUP.md.

3. Configure the GitHub 'production' environment (secrets + variables)
   and add the deploy public key — see deploy/GITHUB_SETUP.md.

4. Trigger a workflow_dispatch run on the 'main' branch to deploy.
EOF
