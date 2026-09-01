#!/bin/sh
set -eu

: "${THEATRE_PAIR_DIR:?set THEATRE_PAIR_DIR to the activated v3 pair}"
: "${THEATRE_ARM:?set THEATRE_ARM to control or theatre}"
: "${THEATRE_TRAJECTORY:?set THEATRE_TRAJECTORY to the immutable trajectory JSONL}"
: "${THEATRE_SESSION_LOG:?set THEATRE_SESSION_LOG to the immutable session JSONL}"

runtime_dir="${THEATRE_RUNTIME_DIR:-.runtime}"
lock_file="$runtime_dir/official-inference.lock"

mkdir -p "$runtime_dir"
exec 9>"$lock_file"
export THEATRE_OFFICIAL_LOCK_FD=9
if ! flock -n 9; then
  printf '%s\n' '{"status":"already_running","detail":"the global official inference lock is held"}'
  exit 0
fi

# This is a zero-inference, zero-retry transition. The shared lock prevents
# immutable evidence from racing an official runner or another paired seed.
PYTHONPATH=src python3 -m theatre_business_bench.cli \
  reconcile-openclaw-v3-undispatched-attempt \
  --pair "$THEATRE_PAIR_DIR" \
  --arm "$THEATRE_ARM" \
  --trajectory "$THEATRE_TRAJECTORY" \
  --session-log "$THEATRE_SESSION_LOG"
