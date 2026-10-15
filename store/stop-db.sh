#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
if [ -f "$DB_PID" ]; then kill "$(cat "$DB_PID")" && echo "MariaDB stopped."; else echo "MariaDB not running."; fi
