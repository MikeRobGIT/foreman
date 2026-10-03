#!/bin/bash
# Run a command while holding a gate slot, so parallel workers don't run more than N gates at once.
#   gate-lock.sh [--name <lock>] <owner> -- <command...>
#   gate-lock.sh w12a -- pnpm -w run check
#   gate-lock.sh w12a -- 'npm run lint && npm test'      (one argument runs through bash -c)
#   gate-lock.sh --name db-test w12a -- npm run db:test  (a single named lock instead of the slot pool)
#
# Lock dir: ${FOREMAN_LOCK_ROOT:-~/.claude/gate-locks}/<repo>, where <repo> is the main checkout's directory
# name (first `git worktree list` entry), so every worktree of one repo shares it. Slots: config `gate_slots`
# (default 2), named slot-1..slot-N. A slot is a directory: mkdir succeeding means you hold it. Its `owner`
# file names the holder and the start time and is touched every minute while the command runs.
# A waiting run clears a stale slot: owner file untouched for 90+ minutes (its holder died), or no owner
# file and the directory older than 10 minutes (its taker died before writing one). Never remove a
# fresh slot by hand. Exits with the command's exit code, or 3 when no slot frees up in time.
#
# The command runs from the checkout's top level. When it passes, is exactly one argument equal to the config's
# `gate`, and no --name was given, a gate receipt is minted for the working tree (state.py mint): the hooks
# then allow a push of that tree. Untracked files the command creates are left out of the receipt.
set -uo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
name=""
if [ "${1:-}" = "--name" ]; then name=${2:?--name needs a value}; shift 2; fi
owner=${1:?usage: gate-lock.sh [--name <lock>] <owner> -- <command...>}; shift
[ "${1:-}" = "--" ] || { echo "usage: gate-lock.sh [--name <lock>] <owner> -- <command...>" >&2; exit 2; }
shift
[ $# -gt 0 ] || { echo "gate-lock.sh: no command" >&2; exit 2; }

main=$(/usr/bin/git worktree list --porcelain 2>/dev/null | awk '/^worktree /{print substr($0, 10); exit}')
[ -n "$main" ] || main=$PWD
root=${FOREMAN_LOCK_ROOT:-$HOME/.claude/gate-locks}/$(basename "$main")
mkdir -p "$root"

if [ -n "$name" ]; then
  slots=("$name")
else
  n=$(python3 "$here/cfg.py" "$main" gate_slots 2)
  slots=(); for i in $(seq 1 "$n"); do slots+=("slot-$i"); done
fi
poll=${FOREMAN_LOCK_POLL:-30}
tries=${FOREMAN_LOCK_TRIES:-120}

clear_stale() {
  local s
  for s in "${slots[@]}"; do
    [ -d "$root/$s" ] || continue
    if [ -n "$(find "$root/$s" -maxdepth 1 -name owner -mmin +90 2>/dev/null)" ] ||
       { [ ! -e "$root/$s/owner" ] && [ -n "$(find "$root" -maxdepth 1 -name "$s" -type d -mmin +10 2>/dev/null)" ]; }; then
      echo "gate-lock: clearing stale $root/$s ($(cat "$root/$s/owner" 2>/dev/null || echo 'no owner'))" >&2
      rm -f "$root/$s/owner"; rmdir "$root/$s" 2>/dev/null
    fi
  done
}

slot=""
for _ in $(seq 1 "$tries"); do
  for s in "${slots[@]}"; do
    if mkdir "$root/$s" 2>/dev/null; then slot=$s; break 2; fi
  done
  clear_stale
  sleep "$poll"
done
[ -n "$slot" ] || { echo "gate-lock: no free slot in $root after $tries tries" >&2; exit 3; }

echo "$owner $(date '+%Y-%m-%d %H:%M:%S')" > "$root/$slot/owner"
# The heartbeat's own output goes to /dev/null: its `sleep` outlives the script by up to a beat, and a caller
# capturing gate-lock's output would otherwise wait for it.
( while sleep "${FOREMAN_LOCK_BEAT:-60}"; do kill -0 $$ 2>/dev/null && touch "$root/$slot/owner" 2>/dev/null || exit; done ) >/dev/null 2>&1 &
beat=$!
release() { kill "$beat" 2>/dev/null; rm -f "$root/$slot/owner"; rmdir "$root/$slot" 2>/dev/null; }
trap release EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "gate-lock: $owner holds $root/$slot" >&2
top=$(/usr/bin/git rev-parse --show-toplevel 2>/dev/null) || top=""
before=""
if [ -n "$top" ]; then
  cd "$top" || exit 2
  before=$(mktemp "${TMPDIR:-/tmp}/foreman-untracked.XXXXXX")
  /usr/bin/git ls-files --others --exclude-standard -z >"$before" 2>/dev/null
fi
if [ $# -eq 1 ]; then bash -c "$1"; else "$@"; fi
rc=$?
if [ -n "$before" ]; then
  if [ "$rc" = 0 ] && [ -z "$name" ] && [ $# -eq 1 ] && [ "$1" = "$(python3 "$here/cfg.py" "$main" gate)" ]; then
    python3 "$here/state.py" mint "$top" "$owner" "$1" "$before" >/dev/null ||
      echo "gate-lock: could not mint a gate receipt" >&2
  fi
  rm -f "$before"
fi
exit "$rc"
