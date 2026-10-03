#!/bin/bash
# Codex CLI, read-only sandbox. -m, -c and --sandbox are GLOBAL flags and must precede the subcommand.
# Review mode: `review --base`, codex's built-in review prompt; the review lands on stdout.
# Spec mode (review.sh --spec): `exec` with our prompt on stdin (`-`), because `review`'s built-in prompt
# ignores the spec; `-o` writes the final message to <log>; --ephemeral keeps no session files.
# Flags checked against `codex --help` / `codex exec --help`, CLI 0.160.0.
# stderr is the transcript (including output of commands codex runs), so only codex's own JSON error lines
# (`ERROR: {"type":"error",...}`) are matched for fall-through: unknown model, old CLI, usage limit.
# foreman: reads-repo
. "$(dirname "$0")/_lib.sh"
args=()
[ -n "$model" ] && args+=(-m "$model")
[ -n "$effort" ] && args+=(-c "model_reasoning_effort=\"$effort\"")
err='^ERROR: \{"type":"error".*(not supported|newer version|usage limit|rate.?limit|quota|credits|unauthorized|status":(401|403|429))'
if [ "${FOREMAN_REVIEW_MODE:-}" = spec ]; then
  run_tool "$err" '' "$prompt" codex "${args[@]}" --sandbox read-only exec --ephemeral --color never -o "$log" -
  rc=$?; [ $rc = 0 ] || exit $rc
else
  run_tool "$err" '' /dev/null codex "${args[@]}" --sandbox read-only review --base "$base"
  rc=$?; [ $rc = 0 ] || exit $rc
  cp "$OUT" "$log"
fi
finish
