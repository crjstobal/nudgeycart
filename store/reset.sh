#!/usr/bin/env bash
# Deletes the local demo store (WordPress files + database) and the backend's SQLite data.
set -euo pipefail
source "$(dirname "$0")/env.sh"
[ -f "$DB_PID" ] && kill "$(cat "$DB_PID")" 2>/dev/null || true
sleep 1
rm -rf "$WP_DIR" "$STORE_DIR/.mariadb" "$REPO_DIR/backend/data"
echo "Store and backend data removed. Run store/setup.sh to rebuild."
