#!/bin/bash
# Meta Muse Code. Diff in the prompt file; no session log. All options go AFTER `exec`.
# `--approval-mode never` AUTO-ALLOWS every tool call (Muse docs: "nothing stops for approval. The sandbox
# alone contains what runs"; confirmed by a probe: without the --disable-* flags it ran `touch` and wrote
# a file). So the read-only guarantee rests ENTIRELY on --disable-write --disable-shell --disable-web-tools
# (with them the same probe got "tool policy denied shell execution" / "...filesystem write").
# tests/run.sh fails if any of the three is dropped. Never add --yolo, --disable-approval or --disable-sandbox.
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
