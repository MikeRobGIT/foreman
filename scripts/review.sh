#!/bin/bash
# Run the project's cross-model reviewer chain against <base>...HEAD.
#   review.sh <base> <logfile>
# Exit 0: a real review is in <logfile>; <logfile>.reviewer names the reviewer (`use model effort`).
# Exit 1: the chain is exhausted (or empty); <logfile>.reviewer says `none`. Either way <logfile>.attempts
# lists every reviewer tried and why it fell through.
#
# Chain: config `reviewers` (tried in order), else the legacy `extra_reviewers` commands (a `custom` entry
# each). Every adapter runs in its own process group under `perl alarm` (reviewer_timeout_seconds, macOS
# has no `timeout`) plus an mtime stall watchdog (reviewer_stall_seconds of silence while alive = hung);
# either kills the whole group, children included, and moves on to the next reviewer.
set -uo pipefail

base=${1:?usage: review.sh <base> <logfile>}
logfile=${2:?usage: review.sh <base> <logfile>}
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
plugin=$(dirname "$here")
adapters=${FOREMAN_REVIEWERS_DIR:-$plugin/reviewers}
repo=$(/usr/bin/git rev-parse --show-toplevel) || { echo "review.sh: not in a git repo" >&2; exit 2; }
# Config comes from the MAIN checkout, never the worktree: a config planted in a worktree must not pick the
# reviewer commands (a `custom` entry runs any command).
main=$(/usr/bin/git worktree list --porcelain | awk '/^worktree /{print substr($0, 10); exit}')
cfg() { python3 "$here/cfg.py" "${main:-$repo}" "$@"; }

timeout=$(cfg reviewer_timeout_seconds 900)
stall=$(cfg reviewer_stall_seconds 150)
export FOREMAN_MAX_DIFF_KB=$(cfg reviewer_max_diff_kb 200)
poll=${FOREMAN_WATCH_POLL:-5}

mkdir -p "$(dirname "$logfile")"
work=$(mktemp -d "${TMPDIR:-/tmp}/foreman-review.XXXXXX")
B=$base perl -pe 's/\{base\}/$ENV{B}/g' "${FOREMAN_PROMPT_TEMPLATE:-$plugin/templates/review-prompt.md}" >"$work/prompt.md"
: >"$logfile.attempts"
note() { echo "$*" | tee -a "$logfile.attempts" >&2; }

newest_mtime() { perl -e 'my $m = 0; for (@ARGV) { my $t = (stat $_)[9]; $m = $t if defined $t && $t > $m } print $m' "$@"; }

i=0
while IFS=$'\t' read -r use model effort; do
  i=$((i + 1))
  [ -n "$use" ] || continue
  adapter=$adapters/$use.sh
  if [ ! -x "$adapter" ]; then note "$i $use: no adapter at $adapter"; continue; fi
  out=$work/$i-$use
  watch=1; grep -q '^# foreman: no-stall-watch' "$adapter" && watch=0
  start=$(date +%s)
  perl -e 'setpgrp(0, 0); alarm shift; exec @ARGV' "$timeout" "$adapter" "$base" "$model" "$effort" "$work/prompt.md" "$out" \
    >"$out.log" 2>&1 </dev/null &
  pid=$!
  why=""
  while kill -0 "$pid" 2>/dev/null; do
    sleep "$poll"
    [ "$watch" = 1 ] && [ "$stall" -gt 0 ] || continue
    last=$(newest_mtime "$out.out" "$out.err" "$out.log")
    [ "$last" -gt "$start" ] || last=$start
    if [ $(( $(date +%s) - last )) -ge "$stall" ]; then
      why="stalled: no output for ${stall}s"
      kill -TERM -- "-$pid" 2>/dev/null; sleep 2; kill -KILL -- "-$pid" 2>/dev/null
      break
    fi
  done
  wait "$pid"; rc=$?
  kill -KILL -- "-$pid" 2>/dev/null   # whatever the adapter left behind (codex spawns dozens of children)
  [ "$rc" = 142 ] && why="timed out after ${timeout}s"
  [ -n "$why" ] && rc=10
  if [ "$rc" = 0 ] && [ -s "$out" ]; then
    cp "$out" "$logfile"
    echo "$use $model $effort" >"$logfile.reviewer"
    note "$i $use ($model $effort): review landed in $(( $(date +%s) - start ))s"
    rm -rf "$work"
    exit 0
  fi
  [ "$rc" = 0 ] && why="exit 0 but no review"
  [ -n "$why" ] || why="exit $rc$([ "$rc" = 10 ] || echo ' (hard error)'): $(tail -3 "$out.log" | tr '\n' ' ' | cut -c1-300)"
  note "$i $use ($model $effort): $why"
done < <(cfg chain)

[ "$i" -gt 0 ] || note "no reviewer chain configured (config \`reviewers\` or \`extra_reviewers\`)"
echo none >"$logfile.reviewer"
note "chain exhausted: no cross-model review ran (raw output kept in $work)"
exit 1
