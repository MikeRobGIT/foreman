#!/bin/bash
# Real reviewer runs: each adapter as a one-entry chain through review.sh, plus one chain of bogus models
# that must fall through to the last entry. Run inside a throwaway repo whose branch differs from <base>.
# Spends real quota. Usage: tests/real-reviewers.sh <base> <outdir> [adapter=model:effort ...]
#   tests/real-reviewers.sh main /tmp/rr codex=gpt-6.1-sol:medium opencode=zai-coding-plan/glm-5.3:max \
#     muse=muse-spark-1.3-contributor:max prime-agent=: claude=haiku:low
set -uo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
base=${1:?base}; out=${2:?outdir}; shift 2
mkdir -p "$out"
for spec in "$@"; do
  use=${spec%%=*}; rest=${spec#*=}; model=${rest%%:*}; effort=${rest#*:}
  printf -- '---\nreviewers:\n  - { use: %s, model: %s, effort: %s }\n---\n' "$use" "$model" "$effort" >"$out/$use.config.md"
  ( FOREMAN_CONFIG=$out/$use.config.md "$root/scripts/review.sh" "$base" "$out/$use.review" >"$out/$use.stderr" 2>&1
    echo "$use: exit $? ($(cat "$out/$use.review.reviewer"))" ) &
done
printf -- '---\nreviewers:\n  - { use: codex, model: bogus-model-xyz, effort: medium }\n  - { use: opencode, model: zai-coding-plan/bogus-xyz, effort: max }\n  - { use: muse, model: bogus-xyz, effort: low }\n  - { use: prime-agent, model: bogus-xyz, effort: low }\n  - { use: claude, model: haiku, effort: low }\n---\n' >"$out/bogus.config.md"
( FOREMAN_CONFIG=$out/bogus.config.md "$root/scripts/review.sh" "$base" "$out/bogus.review" >"$out/bogus.stderr" 2>&1
  echo "bogus chain: exit $? ($(cat "$out/bogus.review.reviewer"))"; cat "$out/bogus.review.attempts" ) &
wait
