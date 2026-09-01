#!/bin/sh
set -eu

: "${THEATRE_PAIR_DIR:?set THEATRE_PAIR_DIR to the activated v3 pair}"
: "${THEATRE_ARM:?set THEATRE_ARM to control or theatre}"
: "${THEATRE_TRAJECTORY:?set THEATRE_TRAJECTORY to the immutable trajectory JSONL}"
: "${THEATRE_SESSION_LOG:?set THEATRE_SESSION_LOG to the immutable session JSONL}"
: "${THEATRE_INTERRUPTED_RUN_ID:?set THEATRE_INTERRUPTED_RUN_ID}"
: "${THEATRE_COMPLETED_RUN_ID:?set THEATRE_COMPLETED_RUN_ID}"

runtime_dir="${THEATRE_RUNTIME_DIR:-.runtime}"
lock_file="$runtime_dir/official-inference.lock"

mkdir -p "$runtime_dir"
exec 9>"$lock_file"
export THEATRE_OFFICIAL_LOCK_FD=9
if ! flock -n 9; then
  printf '%s\n' '{"status":"already_running","detail":"the global official inference lock is held"}'
  exit 0
fi

# No model call occurs here. Sharing the official lock prevents forensic state
# transition from racing a detached provider child or another paired seed.
PYTHONPATH=src python3 -m theatre_business_bench.cli \
  reconcile-openclaw-v3-gateway-restart \
  --pair "$THEATRE_PAIR_DIR" \
  --arm "$THEATRE_ARM" \
  --trajectory "$THEATRE_TRAJECTORY" \
  --session-log "$THEATRE_SESSION_LOG" \
  --interrupted-gateway-run-id "$THEATRE_INTERRUPTED_RUN_ID" \
  --completed-gateway-run-id "$THEATRE_COMPLETED_RUN_ID"
