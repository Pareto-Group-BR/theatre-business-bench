#!/bin/sh
set -eu

pair_dir="${THEATRE_PAIR_DIR:-runs/pairs/20260829T021739Z-pair-s1201}"
runtime_dir="${THEATRE_RUNTIME_DIR:-.runtime}"
lock_file="$runtime_dir/pilot-s1201.lock"

mkdir -p "$runtime_dir"
exec 9>"$lock_file"
if ! flock -n 9; then
  printf '%s\n' '{"status":"already_running","detail":"canonical pilot lock is held"}'
  exit 0
fi

# FD 9 is intentionally inherited by the Python child. If the scheduler shell
# disappears, a still-running batch continues to hold the serialization lock.
PYTHONPATH=src python3 -m theatre_business_bench.cli pair-batch --pair "$pair_dir" "$@"

# Normal completion publishes immediately. A separate idle publisher recovers
# this step when the scheduler disappears before reaching this line.
THEATRE_PILOT_LOCK_HELD=1 ./scripts/publish-live-cockpit-if-idle.sh
