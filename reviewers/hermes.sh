#!/bin/bash
# Hermes agent (UNVERIFIED: not installed; only a shim exists on the machine this was built on).
# `-t safe` toolset, diff piped in with the prompt. Exit 2 = failed or out of budget.
# foreman: no-stall-watch
. "$(dirname "$0")/_lib.sh"
with_diff
args=(-z -t safe)
[ -n "$model" ] && args+=(-m "$model")
[ -n "$effort" ] && args+=(--reasoning "$effort")
run_tool '(rate.?limit|429|quota|usage limit|unauthorized)' '' "$log.prompt" hermes "${args[@]}"
rc=$?; [ $rc = 2 ] && fall "hermes exit 2 (failed or out of budget)"; [ $rc = 0 ] || exit $rc
cp "$OUT" "$log"
finish
