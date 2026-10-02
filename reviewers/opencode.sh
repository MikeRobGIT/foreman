#!/bin/bash
# opencode CLI. Reads the repo itself; OPENCODE_PERMISSION denies edits, web fetches and every shell
# command except read-only git. `--variant` is the reasoning effort. opencode can exit 0 on a model error
# and retries a 429 forever, so JSON `"type":"error"` events (and retry/rate-limit lines) fall through early.
. "$(dirname "$0")/_lib.sh"
export OPENCODE_PERMISSION='{"edit":"deny","webfetch":"deny","task":"deny","external_directory":"deny","bash":{"*":"deny","git diff*":"allow","git log*":"allow","git show*":"allow","git status*":"allow"}}'
args=(run --pure --format json)
[ -n "$model" ] && args+=(-m "$model")
[ -n "$effort" ] && args+=(--variant "$effort")
run_tool '(rate.?limit|429|quota|insufficient|unauthorized|401)' '"type":"error"|"type":"session\.status".*"retry"' /dev/null \
  opencode "${args[@]}" "$(cat "$prompt")"
rc=$?; [ $rc = 0 ] || exit $rc
python3 - "$OUT" >"$log" <<'PY'
import json, sys
parts = []
for line in open(sys.argv[1]):
    try:
        ev = json.loads(line)
    except ValueError:
        continue
    if ev.get('type') == 'text':
        parts.append((ev.get('part') or {}).get('text') or '')
print(parts[-1] if parts else '')
PY
finish
