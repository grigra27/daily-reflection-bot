#!/usr/bin/env bash
#
# Remote deployment script. Copied to the production server by CI and executed
# over SSH as the deploy user. Performs: backup -> deploy -> verify -> rollback.
#
# Usage: deploy_remote.sh <image-ref>
#
# It only touches deploy.env and the Docker Compose resources. The application
# .env, data/ and backups/ are never deleted or rewritten here.

set -Eeuo pipefail

IMAGE_REF="${1:?usage: deploy_remote.sh <image-ref>}"

cd "$(dirname "${BASH_SOURCE[0]}")"

COMPOSE_FILE="docker-compose.prod.yml"
DEPLOY_ENV="deploy.env"
DB_FILE="data/reflection.db"
BACKUP_DIR="backups"
CONTAINER="daily-reflection-bot"
KEEP_BACKUPS=20

# --- helpers ---------------------------------------------------------------

compose() {
  docker compose --env-file "$DEPLOY_ENV" -f "$COMPOSE_FILE" "$@"
}

read_current_image_ref() {
  if [[ -f "$DEPLOY_ENV" ]]; then
    sed -n 's/^IMAGE_REF=//p' "$DEPLOY_ENV" | tail -n1 || true
  fi
}

# --- capture previous image reference for rollback ------------------------

PREV_IMAGE_REF="$(read_current_image_ref)"
echo "current IMAGE_REF on server: ${PREV_IMAGE_REF:-<none>}"
echo "target IMAGE_REF:            ${IMAGE_REF}"

mkdir -p data "$BACKUP_DIR"

# --- pre-deployment SQLite backup (uses the backup API, not cp) -----------

# Retention: keep the newest KEEP_BACKUPS backups.
# Filenames embed a zero-padded timestamp (reflection-YYYYMMDD-HHMMSS.db), so a
# lexicographic sort is chronological — no 'ls' parsing or mtime races.
prune_backups() {
  local total
  total="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'reflection-*.db' | wc -l | tr -d ' ')"
  if (( total > KEEP_BACKUPS )); then
    # 'sort' is oldest-first (timestamped names); delete the oldest excess.
    find "$BACKUP_DIR" -maxdepth 1 -type f -name 'reflection-*.db' | sort \
      | head -n "$(( total - KEEP_BACKUPS ))" \
      | while IFS= read -r f; do rm -f -- "$f"; done
    echo "retention: kept ${KEEP_BACKUPS} newest backups"
  fi
}

BACKUP_FILE=""
if [[ -f "$DB_FILE" ]]; then
  BACKUP_FILE="${BACKUP_DIR}/reflection-$(date +%Y%m%d-%H%M%S).db"
  python3 - "$DB_FILE" "$BACKUP_FILE" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
    s.backup(d)
print(f"backup written: {dst}")
PY
  prune_backups
fi

# --- verify the running container -----------------------------------------

verify_running() {
  # Poll real Docker state (not a fixed sleep) for up to ~90s. A version that
  # crashes during startup is caught two ways: it never reaches 'running', or it
  # reaches 'running' then flips to a restarting/exited state (RestartCount > 0).
  local required="${STABLE_SECONDS:-8}" poll="${POLL_SECONDS:-2}" ticks="${VERIFY_MAX_TICKS:-45}"
  local state="" restarts="" stable_since=0 now elapsed
  for _ in $(seq 1 "$ticks"); do
    state="$(docker inspect --format '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo missing)"
    restarts="$(docker inspect --format '{{.RestartCount}}' "$CONTAINER" 2>/dev/null || echo 0)"
    now="$(date +%s)"
    if [[ "$state" == "running" && "$restarts" == "0" ]]; then
      (( stable_since == 0 )) && stable_since="$now"
      elapsed=$(( now - stable_since ))
      if (( elapsed >= required )); then
        echo "container running and stable for ${elapsed}s (restarts=0)"
        return 0
      fi
    else
      # dropped out of a healthy state -> reset and keep watching
      (( stable_since != 0 )) && echo "container left healthy state (state=${state}, restarts=${restarts}); waiting"
      stable_since=0
    fi
    sleep "$poll"
  done
  echo "container did not reach a stable running state (last state: ${state:-unknown}, restarts: ${restarts:-?})"
  compose logs --tail=100 || true
  return 1
}

# --- deploy ---------------------------------------------------------------

deploy() {
  echo "IMAGE_REF=${IMAGE_REF}" > "$DEPLOY_ENV"
  compose pull
  compose up -d --remove-orphans
  verify_running
}

# --- rollback -------------------------------------------------------------

rollback() {
  if [[ -z "$PREV_IMAGE_REF" ]]; then
    echo "rollback attempted: no previous image on server (first deployment) — cannot roll back automatically"
    return 1
  fi
  echo "rollback attempted -> ${PREV_IMAGE_REF}"

  compose down || true

  # Restore the pre-deploy DB snapshot if migration may have altered it.
  if [[ -n "$BACKUP_FILE" && -f "$BACKUP_FILE" ]]; then
    rm -f "${DB_FILE}-wal" "${DB_FILE}-shm"
    cp -f "$BACKUP_FILE" "$DB_FILE"
    echo "restored database from ${BACKUP_FILE}"
  fi

  echo "IMAGE_REF=${PREV_IMAGE_REF}" > "$DEPLOY_ENV"
  if compose pull && compose up -d --remove-orphans && verify_running; then
    echo "rollback success"
  else
    echo "rollback failure"
  fi
}

# --- main -----------------------------------------------------------------

if deploy; then
  echo "deployment success: ${IMAGE_REF}"
  exit 0
else
  echo "deployment failed: ${IMAGE_REF}"
  rollback || true
  # Fail the workflow even if rollback restored the previous version.
  exit 1
fi
