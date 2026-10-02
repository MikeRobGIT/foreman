#!/bin/bash
# Claude Code CLI in print mode: plan mode, a read-only tool allowlist, every other prompt auto-denied.
# stream-json so the stall watchdog sees progress; the review is the final `result` event.
. "$(dirname "$0")/_lib.sh"
args=(-p --output-format stream-json --verbose --permission-mode plan --permission-prompts none
  --allowedTools "Read,Grep,Glob,Bash(git diff:*),Bash(git log:*),Bash(git show:*)"
  --disallowedTools "Edit,Write,NotebookEdit" --no-session-persistence)
[ -n "$model" ] && args+=(--model "$model")
[ -n "$effort" ] && args+=(--effort "$effort")
run_tool '(rate.?limit|usage limit|quota|credit balance|not logged in|invalid api key|model.*(not found|not available|does not exist))' \
  '"type":"result".*"is_error":true' "$prompt" claude "${args[@]}"
rc=$?; [ $rc = 0 ] || exit $rc
python3 - "$OUT" >"$log" <<'PY' || fall "no result event"
import json, sys
for line in open(sys.argv[1]):
    try:
        ev = json.loads(line)
    except ValueError:
        continue
    if ev.get('type') == 'result':
        if ev.get('is_error'):
            sys.exit(1)
        print(ev.get('result') or '')
PY
finish
