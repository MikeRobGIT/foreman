#!/bin/bash
# Worktree setup for a /foreman worker, and the stale-install check.
#   wt-setup.sh <worktree>      copy `worktree_env_files` from the main checkout, run `worktree_install`
#                               in the worktree, then `stale_check` when one is configured
#   wt-setup.sh --check <dir>   `stale_check` only (run it on the checkout a browser check will use)
#
# The main checkout is the first entry of `git worktree list`. Config keys (see templates/foreman.config.md):
#   worktree_env_files  untracked files to copy, paths relative to the repo root
#   worktree_install    install command, run in the worktree (e.g. `npm ci`)
#   stale_check         command that exits non-zero when the install at {dir} is stale
# --check with no stale_check configured FAILS (exit 2): a check that cannot fail must not read as a pass.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cfg() { python3 "$here/cfg.py" "$@"; }

stale_check() {
  local dir=$1 cmd
  cmd=$(cfg "$dir" stale_check)
  if [ -z "$cmd" ]; then
    echo "NO CHECK: no stale_check configured for $dir" >&2
    return 2
  fi
  if bash -c "${cmd//\{dir\}/\"$dir\"}"; then
    echo "install ok: $dir"
  else
    echo "STALE: $dir failed stale_check: $cmd" >&2
    return 1
  fi
}

if [ "${1:-}" = "--check" ]; then
  stale_check "$(cd "${2:?usage: wt-setup.sh --check <dir>}" && pwd)"
  exit
fi

wt=$(cd "${1:?usage: wt-setup.sh <worktree> | --check <dir>}" && pwd)
main=$(/usr/bin/git -C "$wt" worktree list --porcelain | awk '/^worktree /{print substr($0, 10); exit}')
[ -n "$main" ] || { echo "$wt is not a git worktree" >&2; exit 1; }

while IFS= read -r f; do
  [ -n "$f" ] || continue
  [ -e "$main/$f" ] || { echo "missing env file in the main checkout: $main/$f" >&2; exit 1; }
  mkdir -p "$wt/$(dirname "$f")"
  cp "$main/$f" "$wt/$f"
  echo "copied $f"
done < <(cfg "$wt" worktree_env_files)

install=$(cfg "$wt" worktree_install)
if [ -n "$install" ]; then
  (cd "$wt" && bash -c "$install")
else
  echo "no worktree_install configured; skipped install"
fi

if [ -n "$(cfg "$wt" stale_check)" ]; then
  stale_check "$wt"
else
  echo "no stale_check configured; skipped"
fi
