#!/bin/bash
# pi coding agent (UNVERIFIED: not installed on the machine this was built on). Read-only tool
# allowlist, diff piped in with the prompt, no session.
# foreman: no-stall-watch
. "$(dirname "$0")/_lib.sh"
with_diff
args=(-p --no-session -nc --tools read,grep,find,ls)
[ -n "$model" ] && args+=(--model "$model")
[ -n "$effort" ] && args+=(--thinking "$effort")
run_tool '(No API key|rate.?limit|429|quota|usage limit)' '' "$log.prompt" pi "${args[@]}"
rc=$?; [ $rc = 0 ] || exit $rc
cp "$OUT" "$log"
finish
