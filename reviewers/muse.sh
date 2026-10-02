#!/bin/bash
# Meta Muse Code. Diff in the prompt file; writes, shell and web tools disabled; no session log.
# All options go AFTER `exec`. `--approval-mode never` = never prompt (headless); with writes and shell
# disabled nothing it could approve can change the repo. Not --yolo / --disable-approval / --disable-sandbox.
. "$(dirname "$0")/_lib.sh"
with_diff
args=(exec --json --approval-mode never --approval-judge off --user-input-auto-resolve --disable-write
  --disable-shell --disable-web-tools --no-session-log --max-model-steps 40 --workspace . --prompt-file "$log.prompt")
[ -n "$model" ] && args+=(--model "$model")
[ -n "$effort" ] && args+=(--reasoning-effort "$effort")
run_tool '(usage limit|rate.?limited|not logged in|does not exist or you lack access|model .* not found|quota)' '' /dev/null \
  muse "${args[@]}"
rc=$?; [ $rc = 0 ] || exit $rc
python3 - "$OUT" >"$log" <<'PY'
import json, sys
text = ''
for line in open(sys.argv[1]):
    try:
        ev = json.loads(line)
    except ValueError:
        continue
    p = ev.get('payload') or {}
    if p.get('kind') == 'run_terminal' and p.get('terminal') == 'completed':
        text = p.get('text') or text
print(text)
PY
finish
