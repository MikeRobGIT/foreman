#!/bin/bash
# Offline tests for the plugin's scripts: no network, no reviewer accounts, no project of yours touched.
# Usage: tests/run.sh        (exit 0 = all passed)
# Real reviewer runs are separate: tests/real-reviewers.sh.
set -uo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
T=$(mktemp -d "${TMPDIR:-/tmp}/foreman-tests.XXXXXX")
[ -n "${KEEP:-}" ] || trap 'rm -rf "$T"' EXIT
pass=0; fail=0
ok() { echo "PASS $1"; pass=$((pass + 1)); }
no() { echo "FAIL $1${2:+: $2}"; fail=$((fail + 1)); }
check() { local name=$1; shift; if "$@"; then ok "$name"; else no "$name"; fi; }
git() { /usr/bin/git -c user.name=t -c user.email=t@example.com "$@"; }

echo "## syntax"
for f in "$root"/scripts/*.sh "$root"/reviewers/*.sh; do check "bash -n ${f#$root/}" bash -n "$f"; done
check "py_compile" python3 -m py_compile "$root/scripts/cfg.py" "$root/scripts/bk.py"

echo "## cfg.py"
cat >"$T/c.md" <<'EOF'
---
gate: npm test   # comment
review_bots: [claude, "x # not a comment"]
reviewers:
  - { use: codex, model: gpt-6.1-sol, effort: medium }
  - { use: opencode, model: "zai/glm", effort: max }   # fallback
extra_gates:
  - when: diff touches db/**
    run: npm run db:test
e2e:
  run: npx playwright test {spec}
---
body: ignored
EOF
c() { FOREMAN_CONFIG=$T/c.md python3 "$root/scripts/cfg.py" "$T" "$@"; }
check "scalar + comment" test "$(c gate)" = "npm test"
check "flow list keeps quoted #" test "$(c review_bots | tail -1)" = "x # not a comment"
check "chain" test "$(c chain | tr '\t' ' ')" = "codex gpt-6.1-sol medium
opencode zai/glm max"
check "block map in list" test "$(c extra_gates)" = '{"when": "diff touches db/**", "run": "npm run db:test"}'
check "default" test "$(c nope fallback)" = fallback
printf -- '---\nextra_reviewers:\n  - codex review --base main\n---\n' >"$T/legacy.md"
check "legacy extra_reviewers -> custom" test "$(FOREMAN_CONFIG=$T/legacy.md python3 "$root/scripts/cfg.py" "$T" chain | tr '\t' '|')" = "custom|codex review --base main|"

echo "## templates"
tc() { FOREMAN_CONFIG=$root/templates/foreman.config.md python3 "$root/scripts/cfg.py" "$T" "$@"; }
check "template: gate" test "$(tc gate)" = "npm test"
check "template: commented-out reviewers = empty chain" test -z "$(tc chain)"
check "template: timeout" test "$(tc reviewer_timeout_seconds)" = 900
check "template: empty list" test -z "$(tc required_ci)"
mkdir -p "$T/tpl/.claude" && cp "$root/templates/improvement-backlog.md" "$T/tpl/.claude/" && printf -- '---\n---\n' >"$T/tpl/.claude/foreman.config.md"
echo '{"pr": 1, "done": [{"id": "IB-1", "note": "x"}], "new_rows": ["| IB-NEW-a | t | low | ready | a | s |"]}' >"$T/tpl.json"
python3 "$root/scripts/bk.py" "$T/tpl" --dry-run "$T/tpl.json" >"$T/tpl.out" 2>&1
check "bk.py parses the template backlog" grep -q "dry run: ok, 1 new rows from IB-2" "$T/tpl.out"

echo "## bk.py with a non-default prefix"
mkdir -p "$T/bk/.claude/rules"
cat >"$T/bk/.claude/backlog.md" <<'EOF'
| id | title | risk | status | acceptance | source |
|----|-------|------|--------|------------|--------|
| FM-1 | first | low | ready | it works | me |
| FM-2 | second | low | ready | also | me |
EOF
echo "The old sentence stays here." >"$T/bk/.claude/rules/r.md"
printf -- '---\nbacklog_path: .claude/backlog.md\nid_prefix: FM\n---\n' >"$T/bk/.claude/foreman.config.md"
cat >"$T/rep.json" <<'EOF'
{"pr": 7, "done": [{"id": "FM-1", "note": "shipped, see FM-NEW-a"}], "new_rows": ["| FM-NEW-a | follow-up | low | ready | later | #7 |"],
 "rule_notes": [{"file": ".claude/rules/r.md", "old": "The old sentence", "new": "The new sentence"}]}
EOF
python3 "$root/scripts/bk.py" "$T/bk" "$T/rep.json" >"$T/bk.out" 2>&1
check "bk.py exit 0" test $? = 0
check "row marked done" grep -q '^| FM-1 | first | low | done (#7) | it works \*\*DONE (#7):\*\* shipped, see FM-3 | me |$' "$T/bk/.claude/backlog.md"
check "new row numbered FM-3 after FM-2" grep -q '^| FM-3 | follow-up |' "$T/bk/.claude/backlog.md"
check "rule note applied" grep -q 'The new sentence' "$T/bk/.claude/rules/r.md"
echo '{"pr": 8, "done": [{"id": "FM-9", "note": "x"}]}' >"$T/bad.json"
cp "$T/bk/.claude/backlog.md" "$T/before.md"
python3 "$root/scripts/bk.py" "$T/bk" "$T/bad.json" >/dev/null 2>&1
check "bk.py refuses an unknown id (exit 1)" test $? = 1
check "refusal writes nothing" cmp -s "$T/before.md" "$T/bk/.claude/backlog.md"

echo "## wt-setup.sh"
mkdir -p "$T/repo" && cd "$T/repo" && git init -q -b main && echo x >f && git add f && git commit -qm init
printf 'SECRET=1\n' >"$T/repo/.env.local"
cat >"$T/repo/.claude.cfg.md" <<'EOF'
---
worktree_env_files:
  - .env.local
worktree_install: touch installed.marker
stale_check: test -e {dir}/installed.marker
---
EOF
git -C "$T/repo" worktree add -q "$T/wt" -b w1 2>/dev/null
FOREMAN_CONFIG=$T/repo/.claude.cfg.md "$root/scripts/wt-setup.sh" "$T/wt" >"$T/wt.out" 2>&1
check "setup exit 0" test $? = 0
check "env file copied" cmp -s "$T/repo/.env.local" "$T/wt/.env.local"
check "install ran in the worktree" test -e "$T/wt/installed.marker"
FOREMAN_CONFIG=$T/repo/.claude.cfg.md "$root/scripts/wt-setup.sh" --check "$T/repo" >/dev/null 2>&1
check "--check fails on a stale dir (no marker)" test $? = 1
FOREMAN_CONFIG=$T/repo/.claude.cfg.md "$root/scripts/wt-setup.sh" --check "$T/wt" >/dev/null 2>&1
check "--check passes on the installed worktree" test $? = 0
FOREMAN_CONFIG=/nonexistent "$root/scripts/wt-setup.sh" --check "$T/wt" >/dev/null 2>&1
check "--check with no stale_check fails loudly (exit 2)" test $? = 2

echo "## gate-lock.sh"
export FOREMAN_LOCK_ROOT=$T/locks FOREMAN_LOCK_POLL=1
L=$T/locks/repo
printf -- '---\ngate_slots: 1\n---\n' >"$T/one.md"; printf -- '---\ngate_slots: 2\n---\n' >"$T/two.md"
span() { "$root/scripts/gate-lock.sh" "$1" -- bash -c "echo \"$1 start \$(perl -MTime::HiRes=time -e 'printf q(%.2f), time')\" >>$T/spans; sleep 2; echo \"$1 end \$(perl -MTime::HiRes=time -e 'printf q(%.2f), time')\" >>$T/spans" 2>/dev/null; }
overlap() { python3 - "$T/spans" <<'PY'
import sys
t = {}
for line in open(sys.argv[1]):
    who, ev, ts = line.split()
    t.setdefault(who, {})[ev] = float(ts)
a, b = t.values()
sys.exit(0 if a['start'] < b['end'] and b['start'] < a['end'] else 1)
PY
}
: >"$T/spans"; FOREMAN_CONFIG=$T/one.md span a & FOREMAN_CONFIG=$T/one.md span b & wait
overlap; check "1 slot: two runs serialize" test $? = 1
: >"$T/spans"; FOREMAN_CONFIG=$T/two.md span a & FOREMAN_CONFIG=$T/two.md span b & wait
overlap; check "2 slots: two runs overlap" test $? = 0
check "slots released" test -z "$(ls -A "$L" 2>/dev/null)"
FOREMAN_CONFIG=$T/one.md "$root/scripts/gate-lock.sh" x -- 'exit 7' 2>/dev/null
check "exit code propagates (7)" test $? = 7
check "released after a failing command" test ! -e "$L/slot-1"
mkdir -p "$L/slot-1" && echo "dead 2020" >"$L/slot-1/owner" && touch -t 202001010000 "$L/slot-1/owner"
FOREMAN_CONFIG=$T/one.md FOREMAN_LOCK_TRIES=5 "$root/scripts/gate-lock.sh" y -- true 2>/dev/null
check "stale owner (90+ min) reclaimed" test $? = 0
mkdir -p "$L/slot-1" && touch -t 202001010000 "$L/slot-1"
FOREMAN_CONFIG=$T/one.md FOREMAN_LOCK_TRIES=5 "$root/scripts/gate-lock.sh" y -- true 2>/dev/null
check "ownerless slot older than 10 min reclaimed" test $? = 0
mkdir -p "$L/slot-1" && echo "alive" >"$L/slot-1/owner"
FOREMAN_CONFIG=$T/one.md FOREMAN_LOCK_TRIES=3 "$root/scripts/gate-lock.sh" z -- true 2>/dev/null
check "fresh owner NOT reclaimed (control, exit 3)" test $? = 3
check "fresh owner still there" grep -q alive "$L/slot-1/owner"
rm -rf "$L/slot-1"
FOREMAN_CONFIG=$T/one.md "$root/scripts/gate-lock.sh" --name db-test w -- 'test -d "$FOREMAN_LOCK_ROOT/repo/db-test"' 2>/dev/null
check "--name holds a named lock" test $? = 0
FOREMAN_CONFIG=$T/one.md FOREMAN_LOCK_BEAT=1 "$root/scripts/gate-lock.sh" hb -- 'sleep 29' 2>/dev/null &
holder=$!; sleep 2
beat=$(for c in $(pgrep -P "$holder"); do ps -o command= -p "$c" | grep -q gate-lock.sh && echo "$c"; done)
check "heartbeat found while the holder lives" test -n "$beat"
kill -9 "$holder"; sleep 3
check "heartbeat exits after the holder is SIGKILLed (slot can go stale)" bash -c "! kill -0 $beat 2>/dev/null"
pkill -f '^sleep 29$'; kill $beat 2>/dev/null; rm -rf "$L/slot-1"
unset FOREMAN_LOCK_ROOT FOREMAN_LOCK_POLL

echo "## review.sh with fake adapters and fake CLIs"
R=$T/rv && mkdir -p "$R" && cd "$R" && git init -q -b main && echo 'a' >a.txt && git add a.txt && git commit -qm init
git switch -qc feat && head -c 3000 /dev/urandom | base64 >a.txt && git commit -qam change
A=$T/adapters && mkdir -p "$A" && cp "$root/reviewers/_lib.sh" "$A/"
cat >"$A/good.sh" <<'EOF'
#!/bin/bash
. "$(dirname "$0")/_lib.sh"
echo "review by good: Verdict: ship" >"$log"; finish
EOF
cat >"$A/quota.sh" <<'EOF'
#!/bin/bash
. "$(dirname "$0")/_lib.sh"
fall "usage limit reached"
EOF
cat >"$A/sleepy.sh" <<'EOF'
#!/bin/bash
. "$(dirname "$0")/_lib.sh"
sleep 300 & echo $! >"$log.child"; wait
EOF
cat >"$A/empty.sh" <<'EOF'
#!/bin/bash
. "$(dirname "$0")/_lib.sh"
exit 0
EOF
cat >"$A/crash.sh" <<'EOF'
#!/bin/bash
exit 3
EOF
chmod +x "$A"/*.sh
chain() { printf -- '---\nreviewer_timeout_seconds: %s\nreviewer_stall_seconds: %s\nreviewer_max_diff_kb: %s\nreviewers:\n' "$1" "$2" "$3"; shift 3; for u; do echo "  - { use: $u, model: m, effort: e }"; done; echo ---; }
rv() { FOREMAN_REVIEWERS_DIR=${ADIR:-$A} FOREMAN_WATCH_POLL=1 FOREMAN_TOOL_POLL=1 FOREMAN_CONFIG=$T/chain.md "$root/scripts/review.sh" main "$T/out/review.log" 2>"$T/rv.err"; }
mkdir -p "$T/out"
chain 60 30 200 quota crash empty good >"$T/chain.md"; rv
check "chain: quota(10), crash(3), empty(0 no text) fall through; good lands (exit 0)" test $? = 0
check "reviewer file names the one that ran" grep -q '^good m e$' "$T/out/review.log.reviewer"
check "review text copied" grep -q 'review by good' "$T/out/review.log"
check "attempts list every fall-through" test "$(grep -c -E 'quota|crash|empty' "$T/out/review.log.attempts")" = 3
chain 60 3 200 sleepy good >"$T/chain.md"; s=$(date +%s); rv
check "stall watchdog trips a silent adapter, next one lands" test $? = 0
check "stall noted" grep -q 'sleepy.*stalled' "$T/out/review.log.attempts"
check "stall caught in <30s" test $(( $(date +%s) - s )) -lt 30
chain 3 0 200 sleepy >"$T/chain.md"; rv
check "timeout (alarm) on an exhausted chain exits 1" test $? = 1
check "timeout noted" grep -q 'timed out after 3s' "$T/out/review.log.attempts"
check "exhausted chain writes none" grep -qx none "$T/out/review.log.reviewer"
sleep 1; check "no orphaned sleep 300 left behind" bash -c "! pgrep -f 'sleep 300' >/dev/null"
printf -- '---\n---\n' >"$T/chain.md"; rm -f "$T/out/review.log.reviewer"; rv
check "empty chain exits 1" test $? = 1
check "...and writes none" grep -qx none "$T/out/review.log.reviewer"

# Real adapters against fake CLIs on PATH.
F=$T/fakebin && mkdir -p "$F"
cat >"$F/opencode" <<'EOF'
#!/bin/bash
echo '{"type":"error","error":{"name":"ProviderModelNotFoundError","data":{"message":"model not found"}}}'
exit 0
EOF
cat >"$F/muse" <<'EOF'
#!/bin/bash
touch "$MUSE_CALLED"; echo '{"payload":{"kind":"run_terminal","terminal":"completed","text":"review"}}'
EOF
chmod +x "$F"/*
cp "$root"/reviewers/*.sh "$A/"
chain 60 30 200 opencode good >"$T/chain.md"; PATH=$F:$PATH rv
check "opencode exit-0-with-error JSON falls through" grep -q 'opencode.*exit 10' "$T/out/review.log.attempts"
check "...and the next reviewer lands" grep -q '^good' "$T/out/review.log.reviewer"
cat >"$F/codex" <<'EOF'
#!/bin/bash
echo 'ERROR: login failed for user bob (from the test suite codex ran)' >&2
echo 'ERROR: rate limit exceeded in api/limits.test.ts' >&2
echo '- [P2] app/auth.ts:3 the review itself'
EOF
chmod +x "$F/codex"
chain 60 30 200 codex >"$T/chain.md"; PATH=$F:$PATH rv
check "codex: ERROR lines from the transcript do not fall through" grep -q '^codex' "$T/out/review.log.reviewer"
cat >"$F/codex" <<'EOF'
#!/bin/bash
echo 'ERROR: {"type":"error","status":400,"error":{"message":"The model is not supported when using Codex with a ChatGPT account."}}' >&2
exit 1
EOF
chain 60 30 200 codex good >"$T/chain.md"; PATH=$F:$PATH rv
check "codex: its JSON error line falls through" grep -q 'codex.*exit 10' "$T/out/review.log.attempts"
export MUSE_CALLED=$T/muse.called
chain 60 30 1 muse good >"$T/chain.md"; PATH=$F:$PATH rv
check "diff over reviewer_max_diff_kb skips a diff-in-prompt adapter" grep -q 'muse.*exit 10' "$T/out/review.log.attempts"
check "...without calling the CLI" test ! -e "$MUSE_CALLED"
chain 60 30 200 muse >"$T/chain.md"; PATH=$F:$PATH rv
check "same adapter under the cap calls the CLI and lands" test -e "$MUSE_CALLED"

echo
echo "passed $pass, failed $fail"
[ "$fail" = 0 ]
