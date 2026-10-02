#!/bin/bash
# OpenClaw (UNVERIFIED: not installed; how --config merges with the user's config is unconfirmed).
# Diff piped in as the message; a config that denies exec/write/edit/process.
# foreman: no-stall-watch
. "$(dirname "$0")/_lib.sh"
with_diff
printf '{ tools: { deny: ["exec", "write", "edit", "process"] } }\n' >"$log.ro.json5"
args=(agent exec --message-file - --json --timeout 600 --config "$log.ro.json5")
[ -n "$model" ] && args+=(--model "$model")
[ -n "$effort" ] && args+=(--thinking "$effort")
run_tool '(rate.?limit|429|quota|usage limit|unauthorized)' '' "$log.prompt" openclaw "${args[@]}"
rc=$?; [ $rc = 0 ] || exit $rc
python3 - "$OUT" >"$log" <<'PY' || fall "openclaw returned ok:false"
import json, sys
d = json.load(open(sys.argv[1]))
if d.get('ok') is False:
    sys.exit(1)
print(d.get('text') or d.get('result') or d.get('output') or '')
PY
finish
