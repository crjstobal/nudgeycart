#!/usr/bin/env bash
# Local development: the NudgeyCart API on :8797 and a WordPress Playground store on :9411
# (WooCommerce + demo products) with the plugin mounted live from wordpress-plugin/.
# Ctrl+C stops both. Each start is a fresh store; the API keeps its SQLite data.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="${NUDGEY_PYTHON:-/Users/javi/PythonEnv/bin/python}"
API="http://127.0.0.1:8797"
BP="$(mktemp -t nudgey-blueprint).json"

trap 'kill 0' EXIT INT TERM
(cd "$REPO_DIR/backend" && exec "$PY" -m uvicorn nudgeycart.app:app --host 127.0.0.1 --port 8797 --reload) &
for _ in $(seq 1 40); do curl -sf "$API/health" >/dev/null && break; sleep 0.5; done

# dev=1 drops the "install plugin from zip" step: the plugin folder is mounted instead.
curl -sf "$API/playground/blueprint.json?dev=1" -o "$BP"
# The Playground CLI needs Node 24; npx fetches it without touching the system Node.
npx -y -p node@24 -p @wp-playground/cli@3.1.61 wp-playground-cli server --port=9411 \
  --mount-dir "$REPO_DIR/wordpress-plugin/nudgeycart" /wordpress/wp-content/plugins/nudgeycart \
  --blueprint="$BP" 2>&1 | grep -v EBADENGINE | grep -v "npm warn" &

cat <<EOF
  Store:      http://127.0.0.1:9411/shop/          (wp-admin: admin / password)
  Dashboard:  http://127.0.0.1:9411/wp-admin/admin.php?page=nudgeycart
  API:        $API/health
EOF
wait
