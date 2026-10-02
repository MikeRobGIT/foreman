#!/bin/bash
# prime-agent (a pi fork). No tools, diff in the prompt (piped on stdin), no session, offline startup.
# It silently falls back to its default model on an unknown --model, so the model is checked first.
# foreman: no-stall-watch   (prints only when the answer is complete)
. "$(dirname "$0")/_lib.sh"
with_diff
args=(-p --no-tools --no-session --offline -nc)
if [ -n "$model" ]; then
  prime-agent model list "$model" 2>/dev/null | awk -v m="$model" 'NR > 1 && ($2 == m || $1 "/" $2 == m) {f = 1} END {exit !f}' ||
    fall "prime-agent does not list model $model"
  args+=(--model "$model")
fi
[ -n "$effort" ] && args+=(--thinking "$effort")
run_tool '(No API key|rate.?limit|429|quota|usage limit|not found)' '' "$log.prompt" prime-agent "${args[@]}"
rc=$?; [ $rc = 0 ] || exit $rc
cp "$OUT" "$log"
finish
