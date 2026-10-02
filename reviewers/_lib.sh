# Sourced by every adapter. Adapter contract:
#   reviewers/<name>.sh <base> <model> <effort> <prompt_file> <log>
#   exit 0  = a real review is in <log>
#   exit 10 = try the next reviewer (quota, credits, auth, unknown model, rate limit, diff too large)
#   other   = hard error (review.sh also falls through, and reports it)
# The tool's raw stdout/stderr stream to <log>.out / <log>.err, which review.sh's stall watchdog watches.
# An adapter whose tool prints nothing until it finishes carries the line `# foreman: no-stall-watch`.
set -uo pipefail

base=${1:?base}; model=${2-}; effort=${3-}; prompt=${4:?prompt_file}; log=${5:?log}
OUT=$log.out
ERR=$log.err
: >"$OUT"; : >"$ERR"

fall() { echo "fall through: $*" >&2; exit 10; }

# run_tool <stderr-regex> <stdout-regex> <stdin-file> <cmd...>
# Runs the tool with stdout -> $OUT and stderr -> $ERR. Returns 10 as soon as either regex matches
# (killing the tool: opencode retries a 429 forever), else the tool's exit code. Match only lines that
# are errors (an `^ERROR:` prefix, a JSON error event): the review text itself can mention "rate limit".
run_tool() {
  local err_re=$1 out_re=$2 input=$3; shift 3
  "$@" <"$input" >"$OUT" 2>"$ERR" &
  local pid=$! hit=""
  while kill -0 "$pid" 2>/dev/null; do
    sleep "${FOREMAN_TOOL_POLL:-3}"
    if { [ -n "$err_re" ] && grep -Eq "$err_re" "$ERR"; } || { [ -n "$out_re" ] && grep -Eq "$out_re" "$OUT"; }; then
      hit=1; kill "$pid" 2>/dev/null; sleep 1; kill -9 "$pid" 2>/dev/null; break
    fi
  done
  wait "$pid"; local rc=$?
  if [ -n "$hit" ] || { [ -n "$err_re" ] && grep -Eq "$err_re" "$ERR"; } || { [ -n "$out_re" ] && grep -Eq "$out_re" "$OUT"; }; then
    echo "matched a fall-through error:" >&2
    { [ -n "$err_re" ] && grep -E "$err_re" "$ERR"; [ -n "$out_re" ] && grep -E "$out_re" "$OUT"; } | head -3 | cut -c1-300 >&2
    return 10
  fi
  return $rc
}

# with_diff: write <prompt> + `git diff <base>...HEAD` to $log.prompt (a file, never an argument: ARG_MAX).
# For adapters that cannot read the repo. Falls through when the diff exceeds reviewer_max_diff_kb.
with_diff() {
  local max=${FOREMAN_MAX_DIFF_KB:-200} diff=$log.diff
  /usr/bin/git diff "$base...HEAD" >"$diff" || { echo "git diff $base...HEAD failed" >&2; exit 2; }
  [ -s "$diff" ] || { echo "empty diff against $base" >&2; exit 2; }
  local kb=$(( ($(wc -c <"$diff") + 1023) / 1024 ))
  [ "$kb" -le "$max" ] || fall "diff is ${kb}KB, over reviewer_max_diff_kb=$max (this adapter inlines the diff)"
  { cat "$prompt"; printf '\n\n## Diff (`git diff %s...HEAD`)\n\n```diff\n' "$base"; cat "$diff"; printf '```\n'; } >"$log.prompt"
}

# finish: the review in <log> must be non-empty, else fall through.
finish() {
  [ -s "$log" ] && grep -q '[^[:space:]]' "$log" || fall "exit 0 but no review text"
  exit 0
}
