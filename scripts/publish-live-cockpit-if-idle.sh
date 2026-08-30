#!/bin/sh
set -eu

pair_dir="${THEATRE_PAIR_DIR:-runs/pairs/20260829T021739Z-pair-s1201}"
runtime_dir="${THEATRE_RUNTIME_DIR:-.runtime}"
lock_file="$runtime_dir/pilot-s1201.lock"
cockpit="${THEATRE_COCKPIT_PATH:-live-cockpit.json}"

mkdir -p "$runtime_dir"
if [ "${THEATRE_PILOT_LOCK_HELD:-0}" != "1" ]; then
  exec 9>"$lock_file"
  if ! flock -n 9; then
    printf '%s\n' '{"status":"runner_active","detail":"checkpoint publication deferred"}'
    exit 0
  fi
fi

branch=$(git symbolic-ref --quiet --short HEAD || true)
if [ "$branch" != "main" ]; then
  echo "checkpoint publisher requires the canonical main checkout" >&2
  exit 1
fi

unrelated=$(git status --porcelain --untracked-files=no | grep -v " $cockpit\$" || true)
if [ -n "$unrelated" ]; then
  echo "checkpoint publisher refuses a checkout with unrelated tracked changes" >&2
  printf '%s\n' "$unrelated" >&2
  exit 1
fi

PYTHONPATH=src python3 -m theatre_business_bench.cli verify-pair --pair "$pair_dir" >/dev/null
PYTHONPATH=src python3 -m theatre_business_bench.cli render-cockpit \
  --pair "$pair_dir" \
  --json-out "$cockpit" >/dev/null

git add -- "$cockpit"
if git diff --cached --quiet -- "$cockpit"; then
  printf '%s\n' '{"status":"unchanged","detail":"verified cockpit already current"}'
  exit 0
fi

git config user.email "vilfredo@pareto.io"
git config user.name "Vilfredo"
git commit --only -m "data: refresh live business cockpit" -- "$cockpit"
git push origin HEAD:main
printf '%s\n' '{"status":"published","detail":"verified cockpit pushed to main"}'
