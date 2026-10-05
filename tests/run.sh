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
check "py_compile" python3 -m py_compile "$root/scripts/cfg.py" "$root/scripts/bk.py" "$root/scripts/backlog.py" "$root/scripts/ui.py" "$root/scripts/state.py" "$root/scripts/shellscan.py" "$root/scripts/hooks.py" "$root/scripts/batches.py"

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
check "template: gate_exempt" test "$(tc gate_exempt | tr '\n' ' ')" = "docs/** **/*.md "
check "template: hooks_disabled defaults empty" test -z "$(tc hooks_disabled)"
check "template: forbid_trailers ships the attribution default" test "$(tc forbid_trailers | tr '\n' '|')" = 'Co-Authored-By|Claude-Session|/^\W*Generated with \[?Claude Code/|'
check "template: the hooks_disabled comment names batch_state" grep -qF 'spec_gate, batch_state, status_pane' "$root/templates/foreman.config.md"
check "template: design_spec_dir" test "$(tc design_spec_dir)" = "docs/superpowers/specs"
check "template: all six hook keys present" test "$(grep -c -E '^(forbid_trailers|gate_exempt|worker_agent_types|hooks_disabled|design_spec_dir|status_pane):' "$root/templates/foreman.config.md")" = 6
mkdir -p "$T/tpl/.claude" && cp "$root/templates/improvement-backlog.md" "$T/tpl/.claude/" && printf -- '---\n---\n' >"$T/tpl/.claude/foreman.config.md"
echo '{"pr": 1, "done": [{"id": "IB-1", "note": "x"}], "new_rows": ["| IB-NEW-a | t | low | ready | a | s |"]}' >"$T/tpl.json"
python3 "$root/scripts/bk.py" "$T/tpl" --dry-run "$T/tpl.json" >"$T/tpl.out" 2>&1
check "bk.py parses the template backlog" grep -q "dry run: ok, 1 new rows from IB-2" "$T/tpl.out"

echo "## SKILL.md wires the hooks"
SK=$root/skills/foreman/SKILL.md
check "W3 tags every worker, spec-phase" grep -q 'FIRST line is exactly `foreman-worker: <batch> spec-phase`' "$SK"
check "every termination runs run-end" test "$(grep -c 'hooks.py run-end' "$SK")" -ge 4
check "step 12 pins the verified SHA" grep -q 'gh pr merge <n> --squash --match-head-commit' "$SK"
check "stopping before each TaskStop" test "$(grep -c 'hooks.py stopping' "$SK")" -ge 2
check "W5 forgets each batch" grep -q 'hooks.py forget <batch>' "$SK"
check "/foreman ui passes the main checkout" grep -q 'ui.py" --repo "$(git worktree list' "$SK"

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

echo "## config comes from the main checkout, not a worktree's planted copy"
M=$T/main && mkdir -p "$M/.claude" && cd "$M" && git init -q -b main && echo x >f
printf -- '---\nworktree_install: touch from-main.marker\nreviewers:\n  - { use: good, model: m, effort: e }\n---\n' >"$M/.claude/foreman.config.md"
git add -A && git commit -qm init && git worktree add -q "$T/mwt" -b mw 2>/dev/null
printf -- '---\nworktree_install: touch planted.marker\nreviewers:\n  - { use: custom, model: touch %s/PLANTED, effort: e }\n---\n' "$T" >"$T/mwt/.claude/foreman.config.md"
"$root/scripts/wt-setup.sh" "$T/mwt" >/dev/null 2>&1
check "wt-setup runs the main checkout's install" test -e "$T/mwt/from-main.marker"
check "...never the worktree's planted one" test ! -e "$T/mwt/planted.marker"

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
printf '%s\n' "$@" >"$MUSE_CALLED.args"
touch "$MUSE_CALLED"; echo '{"payload":{"kind":"run_terminal","terminal":"completed","text":"review"}}'
EOF
chmod +x "$F"/*
cp "$root"/reviewers/*.sh "$A/"
(cd "$T/mwt" && FOREMAN_REVIEWERS_DIR=$A FOREMAN_WATCH_POLL=1 "$root/scripts/review.sh" main "$T/out/planted.log" 2>/dev/null)
check "review.sh runs the main checkout's chain" grep -q '^good' "$T/out/planted.log.reviewer"
check "...never a worktree's planted custom reviewer" test ! -e "$T/PLANTED"
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
# --approval-mode never auto-allows tool calls, so these flags are muse's only read-only guarantee.
for flag in --disable-write --disable-shell --disable-web-tools; do
  check "muse runs with $flag" grep -qx -- "$flag" "$MUSE_CALLED.args"
done
for flag in --yolo --disable-approval --disable-sandbox; do
  check "muse never runs with $flag" bash -c "! grep -qx -- '$flag' '$MUSE_CALLED.args'"
done

echo "## review.sh --spec"
# A batch branch: two spec commits on top of the fork base, then main moves on.
S=$T/sp && mkdir -p "$S" && cd "$S" && git init -q -b main && echo base >code.txt && git add . && git commit -qm init
git switch -qc batch
mkdir -p "docs/my specs" && printf '# design\nROUND-ONE line\n' >"docs/my specs/d.md" && git add . && git commit -qm "spec r1"
fork=$(git merge-base main HEAD)
printf 'ROUND-TWO line\n' >>"docs/my specs/d.md" && git commit -qam "spec r2"
git switch -q main && echo MAIN-MOVED >>code.txt && git commit -qam "main moves" && git switch -q batch
# reader: a reads-repo fake whose review is the mode, the prompt it got, and the diff it sees against <base>.
cat >"$A/reader.sh" <<'EOF'
#!/bin/bash
# foreman: reads-repo
. "$(dirname "$0")/_lib.sh"
{ echo "mode=$FOREMAN_REVIEW_MODE"; cat "$prompt"; /usr/bin/git diff "$base...HEAD"; } >"$log"; finish
EOF
# hang: a reads-repo fake that never finishes; its child's pid goes to $HANG_PIDFILE.
cat >"$A/hang.sh" <<'EOF'
#!/bin/bash
# foreman: reads-repo
. "$(dirname "$0")/_lib.sh"
sleep 299 & echo $! >"$HANG_PIDFILE"; wait
EOF
chmod +x "$A/reader.sh" "$A/hang.sh"
sv() { FOREMAN_REVIEWERS_DIR=$A FOREMAN_WATCH_POLL=1 FOREMAN_TOOL_POLL=1 FOREMAN_CONFIG=$T/chain.md "$root/scripts/review.sh" "$@" 2>"$T/sv.err"; }
chain 60 30 200 reader >"$T/chain.md"
sv --spec "docs/my specs/d.md" "$fork" "$T/out/spec.log"
check "--spec: lands (exit 0)" test $? = 0
check "--spec: adapters get FOREMAN_REVIEW_MODE=spec" grep -qx 'mode=spec' "$T/out/spec.log"
check "--spec: the spec-review template" grep -q 'has not been written yet' "$T/out/spec.log"
check "--spec: {spec_path} filled, the space kept" grep -qF 'The spec is `docs/my specs/d.md`' "$T/out/spec.log"
check "--spec: {base} is the fork base" grep -qF "committed on top of \`$fork\`" "$T/out/spec.log"
check "--spec: no placeholder left" bash -c "! grep -qE '\{(spec_path|base)\}' '$T/out/spec.log'"
check "the adapter's diff against the given base: the reviewed diff has round 1" grep -q 'ROUND-ONE' "$T/out/spec.log"
check "the adapter's diff against the given base: ...and round 2" grep -q 'ROUND-TWO' "$T/out/spec.log"
check "the adapter's diff against the given base: main's later commits are not in it" bash -c "! grep -q MAIN-MOVED '$T/out/spec.log'"
check "pid file removed after a normal exit" test ! -e "$T/out/spec.log.pid"
sv "$fork" "$T/out/plain.log"
check "plain call still works (exit 0)" test $? = 0
check "plain call: FOREMAN_REVIEW_MODE=review" grep -qx 'mode=review' "$T/out/plain.log"
check "plain call: the PR review template" grep -q 'You are reviewing a pull request' "$T/out/plain.log"
printf 'KEEP {spec_path} END\n' >"$T/custom-tpl.md"
FOREMAN_PROMPT_TEMPLATE=$T/custom-tpl.md sv "$fork" "$T/out/custom.log"
check "review mode: a custom template keeps a literal {spec_path}" grep -qF 'KEEP {spec_path} END' "$T/out/custom.log"
sv; check "no arguments: usage, exit 2" test $? = 2
sv --spec; check "--spec without a value: exit 2" test $? = 2
sv --bogus "$fork" "$T/out/x.log"; check "unknown option: exit 2" test $? = 2
sv --spec "docs/my specs/d.md" "$fork" "$T/out/x.log" extra; check "extra argument: exit 2" test $? = 2
sv --spec docs/nope.md "$fork" "$T/out/x.log"; check "missing spec file: exit 2" test $? = 2
check "...naming the path" grep -q 'docs/nope.md' "$T/sv.err"
check "...before any adapter ran" test ! -e "$T/out/x.log.attempts"
sv "$fork" "$T/out/opt.log" --spec "docs/my specs/d.md"; check "an option after the positionals parses" grep -qx 'mode=spec' "$T/out/opt.log"
sv --spec "docs/my specs/d.md" -- "$fork" "$T/out/dd.log"; check "-- ends options: the positionals after it land (exit 0)" test $? = 0
# TERM mid-adapter: the adapter's group dies, review.sh exits 143, the next adapter never starts.
export HANG_PIDFILE=$T/hang.pid; rm -f "$HANG_PIDFILE"
chain 60 30 200 quota hang reader >"$T/chain.md"
FOREMAN_REVIEWERS_DIR=$A FOREMAN_WATCH_POLL=1 FOREMAN_TOOL_POLL=1 FOREMAN_CONFIG=$T/chain.md \
  "$root/scripts/review.sh" --spec "docs/my specs/d.md" "$fork" "$T/out/term.log" 2>/dev/null &
rvpid=$!
for _ in $(seq 1 40); do [ -s "$HANG_PIDFILE" ] && break; sleep 0.25; done
check "hang adapter started" test -s "$HANG_PIDFILE"
check "pid file holds review.sh's pid" test "$(cat "$T/out/term.log.pid" 2>/dev/null)" = "$rvpid"
kill -TERM "$(cat "$T/out/term.log.pid")"; wait "$rvpid"
check "TERM: review.sh exits 143" test $? = 143
sleep 1
check "TERM: the adapter's child (sleep 299) is gone" bash -c "! kill -0 $(cat "$HANG_PIDFILE") 2>/dev/null"
check "TERM: the next adapter never started" bash -c "grep -q quota '$T/out/term.log.attempts' && ! grep -q reader '$T/out/term.log.attempts'"
check "TERM: no reviewer recorded" test ! -e "$T/out/term.log.reviewer"
check "TERM: pid file removed" test ! -e "$T/out/term.log.pid"
# A group member that ignores TERM must be dead (KILL escalation) once review.sh exits 143, with the pid file gone.
cat >"$A/stubborn.sh" <<'EOF'
#!/bin/bash
# foreman: reads-repo
. "$(dirname "$0")/_lib.sh"
sh -c 'trap "" TERM; while :; do sleep 1; done' stubborn-member & echo $! >"$STUB_PIDFILE"; wait
EOF
chmod +x "$A/stubborn.sh"
export STUB_PIDFILE=$T/stub.pid; rm -f "$STUB_PIDFILE"
chain 60 30 200 stubborn >"$T/chain.md"
FOREMAN_REVIEWERS_DIR=$A FOREMAN_WATCH_POLL=1 FOREMAN_TOOL_POLL=1 FOREMAN_CONFIG=$T/chain.md \
  "$root/scripts/review.sh" --spec "docs/my specs/d.md" "$fork" "$T/out/kill.log" 2>/dev/null &
rvpid=$!
for _ in $(seq 1 40); do [ -s "$STUB_PIDFILE" ] && [ -s "$T/out/kill.log.pid" ] && break; sleep 0.25; done
kill -TERM "$(cat "$T/out/kill.log.pid")"; wait "$rvpid"; rc=$?
sleep 1
check "TERM: a member that ignores TERM is killed, and the pid file is gone" bash -c "[ $rc = 143 ] && [ -s '$STUB_PIDFILE' ] && ! kill -0 \$(cat '$STUB_PIDFILE') 2>/dev/null && [ ! -e '$T/out/kill.log.pid' ]"
kill -9 "$(cat "$STUB_PIDFILE" 2>/dev/null)" 2>/dev/null
# A second TERM while the trap is still escalating must not leave the pid file behind (the nested exit skips the EXIT trap).
rm -f "$STUB_PIDFILE"
FOREMAN_REVIEWERS_DIR=$A FOREMAN_WATCH_POLL=1 FOREMAN_TOOL_POLL=1 FOREMAN_CONFIG=$T/chain.md \
  "$root/scripts/review.sh" --spec "docs/my specs/d.md" "$fork" "$T/out/kill2.log" 2>/dev/null &
rvpid=$!
for _ in $(seq 1 40); do [ -s "$STUB_PIDFILE" ] && [ -s "$T/out/kill2.log.pid" ] && break; sleep 0.25; done
kill -TERM "$rvpid"; sleep 1.5; kill -TERM "$rvpid" 2>/dev/null; wait "$rvpid"; rc=$?
sleep 1
check "TERM twice: exit 143, the member is dead and the pid file is gone" bash -c "[ $rc = 143 ] && [ -s '$STUB_PIDFILE' ] && ! kill -0 \$(cat '$STUB_PIDFILE') 2>/dev/null && [ ! -e '$T/out/kill2.log.pid' ]"
kill -9 "$(cat "$STUB_PIDFILE" 2>/dev/null)" 2>/dev/null; kill -9 "$(cat "$HANG_PIDFILE" 2>/dev/null)" 2>/dev/null; unset HANG_PIDFILE STUB_PIDFILE
echo "## templates/spec-review-prompt.md"
sp=$root/templates/spec-review-prompt.md
while IFS= read -r s; do check "spec-review prompt has: $s" grep -qF -- "$s" "$sp"; done <<'EOF'
The spec is `{spec_path}`
committed on top of `{base}`
has not been written yet
Before you read the spec's "Callers and writers checked" section
A caller or writer the spec missed
A wrong claim about current behaviour
breaks a rule
A test that can't fail
An Implementation outline step that doesn't achieve its row's acceptance
A failure path the design doesn't handle
Scope outside the stated files
P1 = the design is wrong or unsafe as written. P2 = a likely defect or a gap to close before coding. P3 = minor.
Verdict: <ready to build | revise | redesign>
EOF
cd "$T"
echo "## spec mode: only reads-repo adapters"
cd "$S"
chain 60 30 200 good reader >"$T/chain.md"
sv --spec "docs/my specs/d.md" "$fork" "$T/out/ro.log"
check "spec mode: a non-reads-repo adapter falls through, the reader lands" grep -q '^reader' "$T/out/ro.log.reviewer"
check "...noting why" grep -q 'good.*spec mode needs an adapter that reads the repo' "$T/out/ro.log.attempts"
chain 60 30 200 good >"$T/chain.md"; sv "$fork" "$T/out/ro2.log"
check "review mode: the same adapter still lands" grep -q '^good' "$T/out/ro2.log.reviewer"
for cli in prime-agent pi; do printf '#!/bin/bash\ntouch "%s/spec-%s.called"\n' "$T" "$cli" >"$F/$cli"; chmod +x "$F/$cli"; done
chain 60 30 200 prime-agent pi >"$T/chain.md"; PATH=$F:$PATH sv --spec "docs/my specs/d.md" "$fork" "$T/out/ro3.log"
check "spec mode: diff-in-prompt adapters exhaust the chain (exit 1)" test $? = 1
check "...each with exit 10, not a hard error" test "$(grep -c 'exit 10' "$T/out/ro3.log.attempts")" = 2
check "...each naming spec mode" test "$(grep -c 'spec mode needs an adapter' "$T/out/ro3.log.attempts")" = 2
check "...without calling either CLI" bash -c "! ls '$T'/spec-*.called >/dev/null 2>&1"
for a in claude opencode; do check "$a.sh is marked reads-repo" grep -q '^# foreman: reads-repo' "$root/reviewers/$a.sh"; done
for a in agy hermes openclaw pi prime-agent; do check "$a.sh is not marked reads-repo" bash -c "! grep -q '^# foreman: reads-repo' '$root/reviewers/$a.sh'"; done
cat >"$F/opencode" <<'EOF'
#!/bin/bash
printf '%s\n' "$@" >"$OPENCODE_ARGS"
echo '{"type":"text","part":{"text":"spec review by opencode"}}'
EOF
chmod +x "$F/opencode"
export OPENCODE_ARGS=$T/opencode.args
chain 60 30 200 opencode >"$T/chain.md"; PATH=$F:$PATH sv --spec "docs/my specs/d.md" "$fork" "$T/out/oc.log"
check "opencode lands in spec mode" grep -q '^opencode' "$T/out/oc.log.reviewer"
check "...with the plan agent" bash -c "grep -A1 -x -- --agent '$OPENCODE_ARGS' | grep -qx plan"
chain 60 30 200 opencode >"$T/chain.md"; PATH=$F:$PATH sv "$fork" "$T/out/oc2.log"
check "opencode review mode: no --agent" bash -c "! grep -qx -- --agent '$OPENCODE_ARGS'"
unset OPENCODE_ARGS
cd "$T"
echo "## custom.sh placeholders"
cd "$S"
cust() { printf -- '---\nreviewer_timeout_seconds: 60\nreviewer_stall_seconds: 30\nextra_reviewers:\n  - %s\n---\n' "$1" >"$T/chain.md"; }
cust 'cat {prompt_file}; echo BASE={base}'; sv "$fork" "$T/out/cu1.log"
check "custom review mode: {prompt_file} is the prompt" grep -q 'You are reviewing a pull request' "$T/out/cu1.log"
check "custom review mode: {base} filled" grep -qx "BASE=$fork" "$T/out/cu1.log"
cust 'cat {prompt_file}; echo BASE={base}'; sv --spec "docs/my specs/d.md" "$fork" "$T/out/cu2.log"
check "custom spec mode with both placeholders lands" grep -q '^custom' "$T/out/cu2.log.reviewer"
check "...reading the spec prompt" grep -q 'has not been written yet' "$T/out/cu2.log"
cust "cat '{prompt_file}'; echo base \"{base}\""; sv --spec "docs/my specs/d.md" "$fork" "$T/out/cu3.log"
check "custom: already-quoted placeholders still work" grep -qx "base $fork" "$T/out/cu3.log"
cust 'echo {base} no prompt'; sv --spec "docs/my specs/d.md" "$fork" "$T/out/cu4.log"
check "custom spec mode without {prompt_file}: exhausted (exit 1)" test $? = 1
check "...falls through naming it" grep -q "custom.*exit 10.*doesn't use {prompt_file}" "$T/out/cu4.log.attempts"
cust 'cat {prompt_file}'; sv --spec "docs/my specs/d.md" "$fork" "$T/out/cu5.log"
check "custom spec mode without {base}: falls through naming it" grep -q "custom.*exit 10.*doesn't use {base}" "$T/out/cu5.log.attempts"
printf '#!/bin/bash\ntouch "%s/legacy-codex.called"\n' "$T" >"$F/codex"; chmod +x "$F/codex"
cust 'codex -m gpt-5.6-sol --sandbox read-only review --base main'; PATH=$F:$PATH sv --spec "docs/my specs/d.md" "$fork" "$T/out/cu6.log"
check "a legacy extra_reviewers command in spec mode never runs" test ! -e "$T/legacy-codex.called"
check "custom.sh is marked reads-repo" grep -q '^# foreman: reads-repo' "$root/reviewers/custom.sh"
SP="$T/sp ace's"; mkdir -p "$SP" "$T/a{base}b"; hostile="x'; touch $T/pwned; '"
cust "printf '<%s>\\n' {prompt_file} {base}"; TMPDIR=$SP sv --spec "docs/my specs/d.md" "$hostile" "$T/out/x1.log"
check "custom: a hostile base is one literal word" grep -qxF -- "<$hostile>" "$T/out/x1.log"
check "...and nothing ran" test ! -e "$T/pwned"
cust 'cat {prompt_file}; echo {base}'; TMPDIR=$SP sv --spec "docs/my specs/d.md" "$fork" "$T/out/x2.log"
check "custom: bare {prompt_file} survives a spaced path" grep -q 'has not been written yet' "$T/out/x2.log"
cust "cat '{prompt_file}'; echo {base}"; TMPDIR=$SP sv --spec "docs/my specs/d.md" "$fork" "$T/out/x3.log"
check "custom: quoted {prompt_file} survives a spaced path" grep -q 'has not been written yet' "$T/out/x3.log"
cust 'echo legacy ran'; sv "$fork" "$T/out/x4.log"
check "custom review mode: a command with no placeholders still runs" grep -qx 'legacy ran' "$T/out/x4.log"
cust 'cat {prompt_file}; echo {base}'; TMPDIR="$T/a{base}b" sv --spec "docs/my specs/d.md" "$fork" "$T/out/x6.log"
check "custom: a {base} inside the prompt path is not substituted again" grep -q 'has not been written yet' "$T/out/x6.log"
cd "$T"
echo "## codex: exec in spec mode"
cd "$S"
cat >"$F/codex" <<'EOF'
#!/bin/bash
printf '%s\n' "$@" >"$CODEX_ARGS"; cat >"$CODEX_ARGS.stdin"
out=""; prev=""
for a in "$@"; do [ "$prev" = -o ] && out=$a; prev=$a; done
if [ -n "$out" ]; then echo '- [P2] `Change` misses a writer. Verdict: revise' >"$out"; else echo '- [P2] app/x.ts:3 review'; fi
EOF
chmod +x "$F/codex"
export CODEX_ARGS=$T/codex.args
before() { awk -v a="$2" -v b="$3" '$0==a && !x {x=NR} $0==b && !y {y=NR} END {exit !(x && y && x < y)}' "$1"; }
chain 60 30 200 codex >"$T/chain.md"; PATH=$F:$PATH sv --spec "docs/my specs/d.md" "$fork" "$T/out/cx.log"
check "codex spec mode lands" grep -q '^codex' "$T/out/cx.log.reviewer"
check "...the -o message is the review" grep -q 'misses a writer' "$T/out/cx.log"
check "...runs exec" grep -qx exec "$CODEX_ARGS"
check "...--sandbox precedes exec" before "$CODEX_ARGS" --sandbox exec
check "...the sandbox is read-only" bash -c "grep -A1 -x -- --sandbox '$CODEX_ARGS' | grep -qx read-only"
check "...the prompt comes from stdin (last argument -)" bash -c "tail -1 '$CODEX_ARGS' | grep -qx -- -"
check "...and stdin is the spec prompt" grep -q 'has not been written yet' "$CODEX_ARGS.stdin"
check "...model and effort still passed" bash -c "grep -qx -- -m '$CODEX_ARGS' && grep -qx 'model_reasoning_effort=\"e\"' '$CODEX_ARGS'"
for flag in --ephemeral --color; do check "codex spec mode passes $flag" grep -qx -- "$flag" "$CODEX_ARGS"; done
for flag in review --approve-for-me --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust --add-dir --worktree --full-auto; do
  check "codex spec mode never passes $flag" bash -c "! grep -qx -- '$flag' '$CODEX_ARGS'"
done
chain 60 30 200 codex >"$T/chain.md"; PATH=$F:$PATH sv "$fork" "$T/out/cx2.log"
check "codex review mode unchanged: review --base <base>" bash -c "grep -qx review '$CODEX_ARGS' && grep -A1 -x -- --base '$CODEX_ARGS' | grep -qx '$fork'"
check "...no exec there" bash -c "! grep -qx exec '$CODEX_ARGS'"
check "...review text from stdout" grep -q 'app/x.ts:3' "$T/out/cx2.log"
check "codex.sh is marked reads-repo" grep -q '^# foreman: reads-repo' "$root/reviewers/codex.sh"
printf -- '---\nreviewer_timeout_seconds: 60\nreviewer_stall_seconds: 30\nreviewer_max_diff_kb: 200\nreviewers:\n  - { use: codex }\n---\n' >"$T/chain.md"; PATH=$F:$PATH sv --spec "docs/my specs/d.md" "$fork" "$T/out/cx3.log"
check "codex spec mode with no model/effort lands" grep -q '^codex' "$T/out/cx3.log.reviewer"
check "...and args contains no -m (empty model)" bash -c "! grep -qx -- -m '$CODEX_ARGS'"
printf -- '---\nreviewer_timeout_seconds: 60\nreviewer_stall_seconds: 30\nreviewer_max_diff_kb: 200\nreviewers:\n  - { use: codex }\n---\n' >"$T/chain.md"; PATH=$F:$PATH sv "$fork" "$T/out/cx4.log"
check "codex review mode with no model/effort lands" grep -q '^codex' "$T/out/cx4.log.reviewer"
cat >"$F/codex" <<'EOF'
#!/bin/bash
printf '%s\n' "$@" >"$CODEX_ARGS"; cat >"$CODEX_ARGS.stdin"
echo 'ERROR: {"type":"error","status":429,"error":{"message":"usage limit exceeded"}}' >&2
exit 1
EOF
chmod +x "$F/codex"
chain 60 30 200 codex >"$T/chain.md"; PATH=$F:$PATH sv --spec "docs/my specs/d.md" "$fork" "$T/out/cx5.log"
check "codex spec mode: a usage-limit error falls through" grep -q 'codex.*exit 10' "$T/out/cx5.log.attempts"
cat >"$F/codex" <<'EOF'
#!/bin/bash
printf '%s\n' "$@" >"$CODEX_ARGS"; cat >"$CODEX_ARGS.stdin"
out=""; prev=""
for a in "$@"; do [ "$prev" = -o ] && out=$a; prev=$a; done
if [ -n "$out" ]; then : >"$out"; fi
exit 0
EOF
chmod +x "$F/codex"
chain 60 30 200 codex >"$T/chain.md"; PATH=$F:$PATH sv --spec "docs/my specs/d.md" "$fork" "$T/out/cx6.log"
check "codex spec mode: empty -o file falls through" grep -q 'codex.*exit 10' "$T/out/cx6.log.attempts"
cat >"$F/codex" <<'EOF'
#!/bin/bash
printf '%s\n' "$@" >"$CODEX_ARGS"; cat >"$CODEX_ARGS.stdin"
out=""; prev=""
for a in "$@"; do [ "$prev" = -o ] && out=$a; prev=$a; done
if [ -n "$out" ]; then echo '- [P2] `Change` misses a writer. Verdict: revise' >"$out"; else echo '- [P2] app/x.ts:3 review'; fi
exit 0
EOF
chmod +x "$F/codex"
chain 60 30 200 codex >"$T/chain.md"; PATH=$F:$PATH sv --spec "docs/my specs/d.md" "$fork" "$T/out/cx7.log"
check "codex: --color is followed by never" bash -c "grep -A1 -x -- --color '$CODEX_ARGS' | grep -qx never"
rm -f "$MUSE_CALLED" "$MUSE_CALLED.args"
chain 60 30 1 muse >"$T/chain.md"; PATH=$F:$PATH sv --spec "docs/my specs/d.md" "$fork" "$T/out/mu.log"
check "muse spec mode lands without inlining a diff (reviewer_max_diff_kb=1 doesn't skip it)" grep -q '^muse' "$T/out/mu.log.reviewer"
for flag in --disable-write --disable-shell --disable-web-tools; do check "muse spec mode keeps $flag" grep -qx -- "$flag" "$MUSE_CALLED.args"; done
unset CODEX_ARGS
cd "$T"
echo "## templates/spec.md and the W3.5 pre-check"
tpl=$root/templates/spec.md
fills() { grep -n '⟦FILL' "$1"; }
missing() { grep '^## ' "$tpl" | while IFS= read -r h; do grep -qxF "$h" "$1" || echo "missing section: $h"; done; }
check "spec template: the spec's sections, in order" test "$(grep '^## ' "$tpl" 2>/dev/null | tr '\n' '|')" = '## Rows|## Current behaviour|## Change|## Callers and writers checked|## Rules that apply|## Tests|## Implementation outline|## Surface|## Risks|## Open questions|## Review responses|'
check "spec template: Review responses reads 'None — round 1'" grep -qx 'None — round 1' "$tpl"
check "spec template: scope requests have a stated form" grep -qF -- '- **Scope request:**' "$tpl"
check "pre-check flags the unfilled template" test "$(fills "$tpl" | wc -l)" -ge 10
perl -pe 's/⟦FILL:.*?⟧/filled/g' "$tpl" >"$T/spec-filled.md" 2>/dev/null
cat >>"$T/spec-filled.md" <<'EOF'

```ts
function first<T>(xs: T[]): T { return xs[0] } // TODO: empty input
```
A {placeholder} in prose, and <angle brackets>.
EOF
check "pre-check: a filled spec with <T>, TODO and {x} is clean" test -z "$(fills "$T/spec-filled.md")"
check "pre-check: ...and has every section" test -z "$(missing "$T/spec-filled.md")"
grep -vx '## Risks' "$T/spec-filled.md" >"$T/spec-norisks.md"
check "pre-check names a missing section" test "$(missing "$T/spec-norisks.md")" = 'missing section: ## Risks'

echo "## spec-phase config defaults"
nc() { FOREMAN_CONFIG=/nonexistent python3 "$root/scripts/cfg.py" "$T" "$@"; }
check "default design_spec_dir" test "$(nc design_spec_dir)" = docs/superpowers/specs
check "default spec_review_rounds" test "$(nc spec_review_rounds)" = 3
check "default spec_phase_timeout" test "$(nc spec_phase_timeout)" = 60
check "e2e's spec_dir is a different key (no default)" test -z "$(nc spec_dir)"
printf -- '---\nspec_review_rounds: 2\n---\n' >"$T/sr.md"
check "a config value wins over the default" test "$(FOREMAN_CONFIG=$T/sr.md python3 "$root/scripts/cfg.py" "$T" spec_review_rounds)" = 2
check "template: the spec-phase keys, once each, with their defaults" test "$(grep -cE '^(design_spec_dir: docs/superpowers/specs|spec_review_rounds: 3|spec_phase_timeout: 60) ' "$root/templates/foreman.config.md")" = 3

echo "## bk.py: a parked batch's append-only report"
echo '{"pr": 0, "spec": "docs/superpowers/specs/2026-10-02-FM-2-design.md", "append": [{"id": "FM-2", "note": "spec parked (open question): docs/superpowers/specs/2026-10-02-FM-2-design.md — which role owns the setting?"}]}' >"$T/park.json"
python3 "$root/scripts/bk.py" "$T/bk" "$T/park.json" >"$T/park.out" 2>&1
check "bk.py accepts a pr: 0 append-only report (and ignores \"spec\")" test $? = 0
check "...the note lands on FM-2, which stays ready" grep -qF '| FM-2 | second | low | ready | also spec parked (open question): docs/superpowers/specs/2026-10-02-FM-2-design.md — which role owns the setting? | me |' "$T/bk/.claude/backlog.md"
echo '{"pr": 0, "append": [{"id": "FM-2", "note": "spec parked (open question): see FM-NEW-a"}]}' >"$T/park-bad.json"
python3 "$root/scripts/bk.py" "$T/bk" "$T/park-bad.json" >/dev/null 2>&1
check "bk.py refuses a parked note that quotes a placeholder id" test $? = 1

echo "## SKILL.md: spec phase"
sk=$root/skills/foreman/SKILL.md
check "SKILL.md is the foreman skill (guards the negative check below)" grep -qx 'name: foreman' "$sk"
check "'skip brainstorming' is gone" bash -c "! grep -q 'skip brainstorming' '$sk'"
check "...while the Worker contract is still there" grep -qx '### Worker contract' "$sk"
while IFS= read -r s; do check "SKILL.md has: $s" grep -qF -- "$s" "$sk"; done <<'EOF'
- **W3.5 Spec review**
- **W3.6 Release.**
  2b. **Spec reviewer**
### Worker spec phase
SPEC READY <absolute worktree path> <branch> <spec path> <sha>
foreman-worker: <batch> spec-phase
Parallel mode: no code before the batch's spec is released
in parallel mode a W4.2b verdict on the merge SHA
only in an appended `## Deviations after release`
up to its `## Deviations after release` heading
perl -0pe 's/\n## Deviations after release\n.*//s; s/\s+\z/\n/'
superpowers:brainstorming
  1. `python3 $FOREMAN/hooks.py release <batch> <sha>`. The hooks record it
$FOREMAN/review.sh --spec <spec path> <fork base>
grep -n '⟦FILL' <spec path>
grep '^## ' ${CLAUDE_PLUGIN_ROOT}/templates/spec.md | while IFS= read -r h; do grep -qxF "$h" <spec path> || echo "missing section: $h"; done
spec parked (<reason>): <spec path>
| `design_spec_dir` |
| `spec_review_rounds` | 3 | rounds without a pass
| `spec_phase_timeout` | 60 minutes of worker time (the clock)
| `spec_review_rounds` | spec review rounds without a pass
| `spec_phase_timeout` | minutes of worker time in the spec phase
EOF
appears_before() { awk -v a="$2" -v b="$3" 'index($0, a) && !x {x=NR} index($0, b) && !y {y=NR} END {exit !(x && y && x < y)}' "$1"; }
check "W3.5 comes before W3.6" appears_before "$sk" '- **W3.5 Spec review**' '- **W3.6 Release.**'
check "W3.6 comes before W4" appears_before "$sk" '- **W3.6 Release.**' '- **W4 Each report'
check "the Worker contract points at the spec phase" appears_before "$sk" '### Worker contract' '**Step 2 is the spec phase**'
check "...before the Worker spec phase section" appears_before "$sk" '**Step 2 is the spec phase**' '### Worker spec phase'
check "...which ends before the Hooks section" appears_before "$sk" '### Worker spec phase' '## Hooks'
check "...and the Hooks section comes before the Backlog UI section" appears_before "$sk" '## Hooks' '## Backlog UI'
check "plugin version 0.5.0" grep -qF '"version": "0.5.0"' "$root/.claude-plugin/plugin.json"

echo "## review.sh .entry and cfg.chain(use_env=False)"
cd "$R"
chain 60 30 200 quota good >"$T/chain.md"; rv
check ".entry: a fall-through to the second entry writes 2" test "$(cat "$T/out/review.log.entry" 2>/dev/null)" = 2
chain 60 30 200 quota >"$T/chain.md"; rv
check ".entry: an exhausted chain writes 0" test "$(cat "$T/out/review.log.entry" 2>/dev/null)" = 0
check ".entry: a TERM-killed run writes none" test ! -e "$T/out/term.log.entry"
mkdir -p "$T/ue/.claude" && printf -- '---\nreviewers:\n  - { use: codex, model: m }\n---\n' >"$T/ue/.claude/foreman.config.md"
check "cfg.chain(use_env=False) ignores FOREMAN_CONFIG" test "$(cd "$root/scripts" && FOREMAN_CONFIG=$T/chain.md python3 -c "import cfg; print(cfg.chain('$T/ue', use_env=False)[0][0], cfg.chain('$T/ue')[0][0])")" = "codex quota"
cd "$T"

echo "## templates: open-question labels"
check "spec template: Open questions names the Blocking label" grep -qF -- '`- **Blocking:**`' "$root/templates/spec.md"
check "spec template: ...the Non-blocking label" grep -qF -- '`- **Non-blocking:**`' "$root/templates/spec.md"
check "spec template: ...and None — <reason>" grep -qF -- '"None — <reason>"' "$root/templates/spec.md"

echo "## SKILL.md 0.5.1: W2, W3, Worker spec phase"
sk=$root/skills/foreman/SKILL.md
while IFS= read -r s; do check "SKILL.md has: $s" grep -qF -- "$s" "$sk"; done <<'EOF'
Admit rows that seem to need a human
decision: the spec phase parks them `open question` if they do.
`spec parked (open question)`
`spec parked (not released by the human)`
`spec parked (stopped by the human)`
`spec parked (comment round failed)`
named `fw-<batch>`, never the bare batch name
`set <batch> status=spec rows=<id,…> owned=<file,…>`
`task <batch> add worker fw-<batch>`
ToolSearch `select:TaskStop,Monitor`
TaskStop takes only an id `batches.py` recorded
Label every open question `- **Blocking:**` (the batch can't be built as
EOF
check "W2 no longer filters rows that need a human decision" bash -c "! grep -qF 'need a human decision, or are named' '$sk'"
check "W3 keeps no state table in context" bash -c "! grep -qF 'Keep a short state table' '$sk'"
check "P2 (wrapper): W3 points at README's RTK recipe" grep -qF "point the user at README's **RTK** recipe" "$sk"
echo "## SKILL.md 0.5.1: W3.5"
while IFS= read -r s; do check "SKILL.md has: $s" grep -qF -- "$s" "$sk"; done <<'EOF'
**Open questions are labelled.**
**A Blocking question parks the batch now** (`reason=open-question`), before any round, at any
A verdict that rules an open question blocking parks the
`bump <batch> bounces`, `stamp <batch> sent` and a new timer
**Every reviewer is recorded and stopped by id.**
Agent reviewers are dispatched **unnamed** (no `name`)
`set <batch> status=passed reviewed_sha=<sha>`
**Never edit a worker's spec**
**Stopped by the human:**
then park (`reason=stopped-by-human`)
| a stop by the human |
Every timer is a Bash `run_in_background` `sleep`; a foreground `sleep` is refused.
which is `stamp <batch> comment_sent`
`set <batch> reviewer_model=<model>`
TaskStop the superseded one by its recorded id, `task <batch> done <id>`, record the re-dispatch with
EOF
oq() { awk '/^## Open questions$/{f=1;next} /^## /{f=0} f && NF && !/^- \*\*(Blocking|Non-blocking):\*\* / && !/^None — / && !/^  /' "$1"; }
check "SKILL.md's label pre-check is oq()'s awk" grep -qF "awk '/^## Open questions\$/{f=1;next} /^## /{f=0} f && NF && !/^- \*\*(Blocking|Non-blocking):\*\* / && !/^None — / && !/^  /' <spec path>" "$sk"
printf '## Open questions\n\n- **Blocking:** who owns the setting?\n  (it decides the column)\n- **Non-blocking:** the colour\n\n## Review responses\n\n- free text\n' >"$T/oq1.md"
check "label pre-check: labelled questions pass" test -z "$(oq "$T/oq1.md")"
printf '## Open questions\n\nNone — the rows are fully specified\n' >"$T/oq2.md"
check "label pre-check: None — passes" test -z "$(oq "$T/oq2.md")"
printf '## Open questions\n\n- who owns the setting?\n- **Blocking:** x\n' >"$T/oq3.md"
check "label pre-check: an unlabelled question is a bounce" test "$(oq "$T/oq3.md")" = '- who owns the setting?'
check "the old timestamps text is gone" bash -c "! grep -qF 'in the state table. Every message that returns' '$sk'"

echo "## SKILL.md 0.5.1: W3.6 to W6, Hooks"
while IFS= read -r s; do check "SKILL.md has: $s" grep -qF -- "$s" "$sk"; done <<'EOF'
Releasing** a batch takes two steps, at its `reviewed_sha` only.
`set <batch> status=released released_sha=<sha>`
Run `python3 $FOREMAN/batches.py table checkpoint <wave>`
A question that covers one batch is single-select: Release, Comment, Park
Two to four batches: one multi-select question.
More than four: split them into questions of two to four batches each (5 → 3+2, 9 → 3+3+3)
More than sixteen: let the human answer in text.
"cross-model review fell through to <reviewer>"
which offers only release or park, after a fresh `table checkpoint` is pasted
parked (`reason=comment-failed`)
`Monitor` until-loop (`until [ ! -e <log>.pid ]; do sleep 2; done`, 30 s timeout)
same loop as a `run_in_background` Bash call
`set <batch> status=parked reason=<reason>`
`set <batch> status=pr pr=<n>` at the batch's first report
`bump <batch> pr_rounds` for each send
named `fw-<batch>-2` (then `-3`, …)
unnamed, recorded with `task <batch> add reviewer <agent id>`
**An environment failure** (`fork failed`, `Device not configured`) gets one more try first
is not the one re-dispatch above, and it never stops a `worker` task.
read-only reviewer (unnamed, recorded and stopped
Then stop the batch's workers:
run `python3 $FOREMAN/batches.py table report` and paste its whole output
| `batch_state` | run, orchestrator |
EOF
check "parking runs in seven steps, in order" bash -c "awk -v a='1. Kill the round' -v b='2. \`python3 \$FOREMAN/hooks.py stopping' -v c='3. TaskStop every live task' -v d='4. Copy the specs to' -v e='5. Name the branch' -v f='6. \`git worktree remove --force' -v g='7. Write the park note' 'index(\$0,a)&&!A{A=NR} index(\$0,b)&&!B{B=NR} index(\$0,c)&&!C{C=NR} index(\$0,d)&&!D{D=NR} index(\$0,e)&&!E{E=NR} index(\$0,f)&&!F{F=NR} index(\$0,g)&&!G{G=NR} END{exit !(A&&A<B&&B<C&&C<D&&D<E&&E<F&&F<G)}' '$sk'"
check "W6 pastes the report before run-end" appears_before "$sk" 'run `python3 $FOREMAN/batches.py table report`' 'the hooks refuse it until the current report table'
check "W3.6 pastes the checkpoint before asking" appears_before "$sk" 'Run `python3 $FOREMAN/batches.py table checkpoint <wave>`' 'A question that covers one batch is single-select'
check "SKILL.md no longer reads a state table" bash -c "! grep -qF 'state table' '$sk'"
check "P3 (next-message): the checkpoint paste is a message of its own" grep -qF 'in a message of its own, make one more call' "$sk"
check "P3 (next-message): one more call before run-end" grep -qF 'then one more call (`python3 $FOREMAN/batches.py show`), then `run-end`' "$sk"

echo "## backlog.py + ui.py"
check "test_backlog.py" python3 "$root/tests/test_backlog.py"
check "test_ui_api.py" python3 "$root/tests/test_ui_api.py"
check "test_state.py" python3 "$root/tests/test_state.py"
check "test_batches.py" python3 "$root/tests/test_batches.py"
echo "## shellscan.py + hooks.py"
check "test_shellscan.py" python3 "$root/tests/test_shellscan.py"
check "test_hooks.py" python3 "$root/tests/test_hooks.py"
check "test_hooks_json.py" python3 "$root/tests/test_hooks_json.py"
echo "## ui js"
if command -v node >/dev/null; then check "test_ui_js.mjs" node --test "$root/tests/test_ui_js.mjs"; check "test_status.mjs" node --test "$root/tests/test_status.mjs"; else echo "SKIP node not installed"; fi

echo
echo "passed $pass, failed $fail"
[ "$fail" = 0 ]
