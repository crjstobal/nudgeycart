# Shared paths for the local demo store. Sourced by the other scripts.
STORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$STORE_DIR")"
WP_DIR="$STORE_DIR/wp"
DB_DATA="$STORE_DIR/.mariadb/data"
# Unix sockets have a ~104 char path limit on macOS, so keep it short.
DB_SOCK="${TMPDIR:-/tmp}/nudgeycart-mysqld.sock"
DB_PID="$STORE_DIR/.mariadb/mysqld.pid"
DB_LOG="$STORE_DIR/.mariadb/mysqld.log"
DB_PORT="${NUDGEY_DB_PORT:-3307}"
WP_PORT="${NUDGEY_WP_PORT:-8090}"
WP_URL="http://localhost:$WP_PORT"
API_URL="${NUDGEY_PUBLIC_URL:-http://localhost:8797}"
PY="${NUDGEY_PYTHON:-/Users/javi/PythonEnv/bin/python}"
# Function, not a string: the repo path contains spaces. PHP 8.5 deprecation notices from
# wp-cli itself are silenced so real errors stay visible.
WP_BIN="$(command -v wp)"
wpc() { php -d memory_limit=1024M -d error_reporting=22527 "$WP_BIN" --path="$WP_DIR" "$@"; }

db_running() { [ -S "$DB_SOCK" ] && mariadb-admin --socket="$DB_SOCK" -u"$(whoami)" --connect-timeout=2 ping >/dev/null 2>&1; }

start_db() {
  if db_running; then return 0; fi
  mkdir -p "$(dirname "$DB_DATA")"
  if [ ! -d "$DB_DATA/mysql" ]; then
    mariadb-install-db --datadir="$DB_DATA" --auth-root-authentication-method=socket >/dev/null
  fi
  mariadbd --datadir="$DB_DATA" --socket="$DB_SOCK" --pid-file="$DB_PID" --port="$DB_PORT" \
    --bind-address=127.0.0.1 --log-error="$DB_LOG" >/dev/null 2>&1 &
  for _ in $(seq 1 30); do db_running && return 0; sleep 0.5; done
  echo "MariaDB did not start, see $DB_LOG" >&2; return 1
}
