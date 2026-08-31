#!/bin/sh
set -eu

: "${THEATRE_PAIR_DIR:?set THEATRE_PAIR_DIR to one activated v2 pair}"
runtime_dir="${THEATRE_RUNTIME_DIR:-.runtime}"
lock_file="$runtime_dir/v2-official.lock"

mkdir -p "$runtime_dir"
exec 9>"$lock_file"
if ! flock -n 9; then
  printf '%s\n' '{"status":"already_running","detail":"the global v2 inference lock is held"}'
  exit 0
fi

# One lock is shared by every official seed. The Python runner serializes the
# two arms inside a pair; this inherited descriptor prevents a second pair from
# opening another provider call if the scheduler shell is interrupted.
PYTHONPATH=src python3 -m theatre_business_bench.cli pair-batch --pair "$THEATRE_PAIR_DIR" "$@"
