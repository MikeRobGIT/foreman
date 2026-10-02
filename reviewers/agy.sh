#!/bin/bash
# Antigravity CLI (UNVERIFIED: not signed in on the machine this was built on). Diff in the prompt, plan
# mode, sandbox, JSON output, its own print timeout. The prompt goes in as an argument (agy has no prompt
# file); with_diff caps it at reviewer_max_diff_kb, well under ARG_MAX.
# foreman: no-stall-watch
. "$(dirname "$0")/_lib.sh"
with_diff
args=(-p "$(cat "$log.prompt")" --mode plan --sandbox --output-format json --print-timeout 10m)
[ -n "$model" ] && args+=(--model "$model")
[ -n "$effort" ] && args+=(--effort "$effort")
run_tool '(sign in|rate.?limit|quota|usage limit|429)' '' /dev/null agy "${args[@]}"
rc=$?; [ $rc = 0 ] || exit $rc
python3 - "$OUT" >"$log" <<'PY' || fall "agy returned an error status"
import json, sys
d = json.load(open(sys.argv[1]))
if d.get('error') or d.get('status') not in (None, 'ok', 'success', 'completed'):
    sys.exit(1)
print(d.get('result') or d.get('response') or d.get('text') or '')
PY
finish
