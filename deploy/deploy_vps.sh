#!/usr/bin/env bash
set -euo pipefail

# Intended to run ON the Ubuntu server.
# Mirrors the pattern used in PolyTalk/scripts/deploy_vps.sh.

APP_DIR="${APP_DIR:-/opt/garmin-tracker/app}"
BRANCH="${BRANCH:-main}"
REMOTE="${REMOTE:-origin}"
SERVICE_NAME="${SERVICE_NAME:-garmin-tracker}"

if [[ "$(uname -s)" == "MINGW"* ]] || [[ "$(uname -s)" == "MSYS"* ]] || [[ "$(uname -s)" == "CYGWIN"* ]]; then
  echo "This deploy script is intended to run on the Linux server, not on Windows." >&2
  exit 2
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "APP_DIR not found: $APP_DIR" >&2
  exit 2
fi

cd "$APP_DIR"

if [[ ! -d .git ]]; then
  echo "No git repository found in $APP_DIR" >&2
  exit 2
fi

echo "== Updating code ($REMOTE/$BRANCH) =="
git fetch --prune "$REMOTE" "$BRANCH"
# Keep server state deterministic; discard local uncommitted changes.
git reset --hard "$REMOTE/$BRANCH"

echo "== Ensuring venv + deps =="
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install -U pip wheel
.venv/bin/python -m pip install -r requirements.txt

echo "== Restarting systemd service: $SERVICE_NAME =="
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager -l

if systemctl list-unit-files | grep -q '^nginx\.service'; then
  echo "== Reloading nginx =="
  sudo systemctl reload nginx || sudo systemctl restart nginx
fi

echo "== Done =="
