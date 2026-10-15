#!/usr/bin/env bash
# Runs everything for local testing: MariaDB, the WooCommerce store and the NudgeyCart API.
# Ctrl+C stops the store and the API (MariaDB keeps running; store/stop-db.sh stops it).
set -euo pipefail
source "$(dirname "$0")/../store/env.sh"

[ -f "$WP_DIR/wp-config.php" ] || { echo "Run store/setup.sh first." >&2; exit 1; }
start_db

trap 'kill 0' EXIT INT TERM
# Several PHP workers so the store can serve the page and its Store API calls in parallel.
PHP_CLI_SERVER_WORKERS=4 wpc server --host=127.0.0.1 --port="$WP_PORT" >/tmp/nudgey-wp.log 2>&1 &
(cd "$REPO_DIR/backend" && exec $PY -m uvicorn nudgeycart.app:app --host 0.0.0.0 --port 8797 --reload) &

TOKEN="$(cat "$REPO_DIR/backend/data/admin_token.txt" 2>/dev/null || true)"
cat <<EOF
  Store:      $WP_URL/shop/?nudgey_demo   (adds a "Simulate exit intent" button)
  Dashboard:  $API_URL/dashboard#$TOKEN
  Store log:  /tmp/nudgey-wp.log
EOF
wait
