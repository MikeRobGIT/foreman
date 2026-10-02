#!/bin/bash
# Codex CLI. Reads the repo itself (`review --base`), read-only sandbox, uses its built-in review prompt.
# -m, -c and --sandbox are GLOBAL flags and must precede `review`. The review lands on stdout; stderr is the
# transcript (including output of commands codex runs), so only codex's own JSON error lines
# (`ERROR: {"type":"error",...}`) are matched for fall-through: unknown model, old CLI, usage limit.
. "$(dirname "$0")/_lib.sh"
args=()
[ -n "$model" ] && args+=(-m "$model")
[ -n "$effort" ] && args+=(-c "model_reasoning_effort=\"$effort\"")
run_tool '^ERROR: \{"type":"error".*(not supported|newer version|usage limit|rate.?limit|quota|credits|unauthorized|status":(401|403|429))' '' /dev/null \
  codex "${args[@]}" --sandbox read-only review --base "$base"
rc=$?; [ $rc = 0 ] || exit $rc
cp "$OUT" "$log"
finish
