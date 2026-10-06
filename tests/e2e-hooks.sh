#!/bin/bash
# Headless end-to-end of the foreman hooks: loads this plugin with --plugin-dir in a throwaway repo with a fake gh,
# and drives the main session and haiku workers with `claude -p`. Spends model quota; not part of tests/run.sh.
#   tests/e2e-hooks.sh          (KEEP=1 keeps the temp dir; every hook decision is in $T/hooks.log)
set -uo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
T=$(mktemp -d "${TMPDIR:-/tmp}/foreman-e2e.XXXXXX")
DEF=   # the default-home state dir of $T/repo: Departure 2 mints receipts there too (set once the repo exists)
cleanup() { case $DEF in "$HOME"/.claude/foreman/?*) rm -rf "$DEF" ;; esac; [ -n "${KEEP:-}" ] || rm -rf "$T"; }
trap cleanup EXIT
pass=0; fail=0
ok() { echo "PASS $1"; pass=$((pass + 1)); }
no() { echo "FAIL $1${2:+: $2}"; fail=$((fail + 1)); }
check() { local name=$1; shift; if "$@"; then ok "$name"; else no "$name"; fi; }
git() { /usr/bin/git -c user.name=t -c user.email=t@example.com "$@"; }
logged() { python3 - "$T/hooks.log" "$@" <<'PY'
import json, sys
path, event, needle = sys.argv[1], sys.argv[2], sys.argv[3]
agent = len(sys.argv) > 4
try:
    lines = [json.loads(l) for l in open(path)]
except OSError:
    lines = []
sys.exit(0 if any(d['event'] == event and needle in json.dumps(d['out'], ensure_ascii=False)
                  and (d.get('agent_id') is not None) == agent for d in lines) else 1)
PY
}

export FOREMAN_HOME=$T/home FOREMAN_LOCK_ROOT=$T/locks FOREMAN_HOOK_LOG=$T/hooks.log FAKE_GH_LOG=$T/gh.log
mkdir -p "$T/bin"
cat >"$T/bin/gh" <<'GH'
#!/bin/sh
echo "$@" >>"$FAKE_GH_LOG"
case "$1 $2" in
  "pr view") echo '{"headRefName":"feat","commits":[]}' ;;
  "pr merge") echo "merged (fake gh)" ;;
esac
GH
chmod +x "$T/bin/gh"
export PATH=$T/bin:$PATH

git init -q --bare -b main "$T/origin.git"
git clone -q "$T/origin.git" "$T/repo" 2>/dev/null
cd "$T/repo" || exit 1
git symbolic-ref HEAD refs/heads/main
mkdir -p .claude
printf -- '---\ngate: echo gated\nforbid_trailers: [Co-Authored-By]\n---\n' >.claude/foreman.config.md
printf '# Backlog\n\n| IB-1 | Shipped already | low | done (#1) | x | s |\n' >.claude/improvement-backlog.md
echo 'print(1)' >app.py
git add -A && git commit -qm init && git push -q -u origin main && git remote set-head origin main
SD=$(python3 "$root/scripts/state.py" "$T/repo")
DEF=$(env -u FOREMAN_HOME python3 "$root/scripts/state.py" "$T/repo")

C=(claude -p --model haiku --plugin-dir "$root" --permission-mode bypassPermissions --output-format stream-json --verbose)
run() { "${C[@]}" "$@" >>"$T/out.jsonl" 2>>"$T/err.log"; }

echo "## a /foreman prompt starts a run (P1)"
# --max-turns: stop the skill before it can end the run (hooks.py run-end) on this empty backlog
run --max-turns 2 "/foreman"
SID=$(python3 -c "import json; print(next(json.loads(l)['session_id'] for l in open('$T/out.jsonl') if '\"session_id\"' in l))")
check "this session's run marker exists" test -f "$SD/runs/$SID.json"

echo "## a tagged worktree worker: briefed, its merge denied, its report checked"
run --resume "$SID" "Use the Agent tool exactly once, with subagent_type general-purpose and isolation \"worktree\", and this prompt:
foreman-worker: e2e1
Run the Bash command: echo e2e-start. Then run this exact Bash command once: gh pr merge 1 --squash --match-head-commit abc1234
Then reply with what happened, and quote verbatim the first line of any foreman worker briefing you received (or say: no briefing). Do not include any json block in your reply.
When the agent has returned, reply done."
check "the tagged worker was briefed on its first allowed call" logged pretool 'foreman worker briefing (batch e2e1)' agent
check "the briefing recorded the agent map" bash -c "ls '$SD/batches/$SID/agents/' | grep -q ."
check "the worker's gh pr merge was denied" logged pretool 'workers never merge' agent
check "...and gh never ran the merge" bash -c "! grep -q 'pr merge' '$T/gh.log' 2>/dev/null"
check "the worker's report without a json block was sent back once" logged subagent-stop 'fenced json block' agent

saw_briefing() { python3 - "$SID" <<'PY'
import glob, json, os, sys
sid = sys.argv[1]
for p in glob.glob(os.path.expanduser(f'~/.claude/projects/*/{sid}/subagents/agent-*.jsonl')):
    for l in open(p):
        d = json.loads(l)
        if d.get('type') == 'assistant':
            content = json.dumps(d.get('message', {}).get('content', ''))
            if 'foreman worker briefing (batch e2e1)' in content or 'You run steps 2-8' in content:
                sys.exit(0)
sys.exit(1)
PY
}

check "the worker saw the briefing (quoted its first line back)" saw_briefing

echo "## a push without a receipt is denied, then allowed after the gate (P3: same session after --resume)"
git switch -qc feat && echo 'print(2)' >app.py && git commit -qam change
run --resume "$SID" "Run this exact Bash command once and report its output: git push -u origin HEAD"
check "the receipt-less push was denied" logged pretool 'no passing gate for this tree'
check "...and nothing reached origin" bash -c "! git ls-remote --exit-code origin feat >/dev/null 2>&1"
"$root/scripts/gate-lock.sh" e2e -- 'echo gated' >/dev/null 2>&1
run --resume "$SID" "Run this exact Bash command once and report its output: git push -u origin HEAD"
check "after the gate the push went through" git ls-remote --exit-code origin feat

echo "## an autonomous merge needs a verifier's verdict"
run --max-turns 2 "/foreman --auto-merge"
SID2=$(python3 -c "import json; ids=[json.loads(l)['session_id'] for l in open('$T/out.jsonl') if '\"session_id\"' in l]; print(ids[-1])")
check "the --auto-merge run has its own marker" test -f "$SD/runs/$SID2.json"
SHA=$(git rev-parse HEAD)
run --resume "$SID2" "Run this exact Bash command once and report its output: gh pr merge 1 --squash --match-head-commit $SHA"
check "the merge with no verdict was denied" logged pretool 'no independent verdict for PR 1'
check "...and gh never merged" bash -c "! grep -q 'pr merge' '$T/gh.log' 2>/dev/null"
run --resume "$SID2" "Use the Agent tool exactly once, with subagent_type Explore and a prompt of two lines. Line 1 is exactly: foreman-verifier: 1 $SHA   Line 2 is exactly: Reply with this one line and nothing else: VERDICT PASS $SHA   When the agent returns, reply done."
check "the verifier's PASS wrote a receipt" test -s "$SD/verdict/1-$SHA"
run --resume "$SID2" "Run this exact Bash command once and report its output: gh pr merge 1 --squash --match-head-commit $SHA"
check "after the verdict the merge went through" grep -q 'pr merge' "$T/gh.log"

echo
echo "passed $pass, failed $fail   (temp dir: $T; KEEP=1 keeps it)"
[ "$fail" = 0 ]
