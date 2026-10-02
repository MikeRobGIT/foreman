#!/bin/bash
# Legacy `extra_reviewers` entry: <model> carries a whole command line, run with bash -c in the repo.
# Its stdout is the review. Used when the config has `extra_reviewers` and no `reviewers` chain.
. "$(dirname "$0")/_lib.sh"
[ -n "$model" ] || { echo "custom reviewer: empty command" >&2; exit 2; }
run_tool '^ERROR: \{"type":"error".*(not supported|newer version|usage limit|rate.?limit|quota|credits|unauthorized|status":(401|403|429))' '' /dev/null bash -c "$model"
rc=$?; [ $rc = 0 ] || exit $rc
cp "$OUT" "$log"
finish
