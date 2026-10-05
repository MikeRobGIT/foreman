#!/bin/bash
# Legacy `extra_reviewers` entry: <model> carries a whole command line, run with bash -c in the repo.
# Its stdout is the review. Used when the config has `extra_reviewers` and no `reviewers` chain.
# `{prompt_file}` and `{base}` in the command become the prompt file and the base, each single-quoted for the
# shell (a pair of quotes written directly around a placeholder is absorbed). Placeholders must be a whole shell
# word, or be wrapped directly by one matching pair of quotes. In spec mode (review.sh --spec) the command must
# use both: that is the only way it sees the spec prompt. Otherwise it falls through.
# foreman: reads-repo   (only when its command uses both placeholders; enforced below)
. "$(dirname "$0")/_lib.sh"
[ -n "$model" ] || { echo "custom reviewer: empty command" >&2; exit 2; }
if [ "${FOREMAN_REVIEW_MODE:-}" = spec ]; then
  case $model in *'{prompt_file}'*) ;; *) fall "spec mode: the command doesn't use {prompt_file}" ;; esac
  case $model in *'{base}'*) ;; *) fall "spec mode: the command doesn't use {base}" ;; esac
fi
cmd=$(P=$prompt B=$base perl -e '
  sub shq { my $s = shift; $s =~ s/\x27/\x27\\\x27\x27/g; return "\x27$s\x27" }
  my $c = shift;
  $c =~ s/([\x27"]?)\{(prompt_file|base)\}\1/shq($ENV{$2 eq "base" ? "B" : "P"})/ge;
  print $c' -- "$model")
run_tool '^ERROR: \{"type":"error".*(not supported|newer version|usage limit|rate.?limit|quota|credits|unauthorized|status":(401|403|429))' '' /dev/null bash -c "$cmd"
rc=$?; [ $rc = 0 ] || exit $rc
cp "$OUT" "$log"
finish
