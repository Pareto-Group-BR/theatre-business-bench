#!/bin/sh
set -eu

: "${THEATRE_PAIR_DIR:?set THEATRE_PAIR_DIR to one activated v3 pair}"
runtime_dir="${THEATRE_RUNTIME_DIR:-.runtime}"
lock_file="$runtime_dir/official-inference.lock"

mkdir -p "$runtime_dir"
exec 9>"$lock_file"
export THEATRE_OFFICIAL_LOCK_FD=9
if ! flock -n 9; then
  printf '%s\n' '{"status":"already_running","detail":"the global official inference lock is held"}'
  exit 0
fi

# The same lock serializes every official protocol and remains inherited by
# the OpenClaw child, so a detached provider call never appears idle.
PYTHONPATH=src python3 -m theatre_business_bench.cli pair-batch --pair "$THEATRE_PAIR_DIR" "$@"
