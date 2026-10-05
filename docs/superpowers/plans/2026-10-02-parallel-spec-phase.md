# Parallel Spec Phase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In `parallel N` mode, every batch first writes a design spec. The spec is reviewed until a round has no Critical or Important finding, and it is released before any code is written. This is foreman 0.5.0.

**Architecture:** Most of the feature is SKILL.md text:
- the worker's spec phase in the Worker contract;
- new W3.5 (spec review, with its timer-driven clock) and W3.6 (release and parking);
- additions to W1, W2, W3, W4, W5 and W6.

Small script changes back it:
- `review.sh --spec`: real option parsing, a spec-review prompt, a TERM trap and a pid file;
- a `# foreman: reads-repo` adapter marker, with a spec-mode fall-through in `_lib.sh`;
- `codex exec` in spec mode;
- placeholder substitution in `custom.sh`;
- two new templates;
- three config defaults.

The hooks (0.4.0) enforce "no code before release" through piece 10. This plan only calls their CLIs.

**Tech Stack:** Bash 3.2 (macOS `/bin/bash`), Python 3 standard library, perl (already required), Claude Code skill markdown.

**Spec:** `docs/superpowers/specs/2026-10-02-parallel-spec-phase-design.md` (revision 6) is the authority. It relies on the companion hooks design, `docs/superpowers/specs/2026-10-02-foreman-hooks-design.md` on `feat/hooks`, for these interfaces:
- the tag `foreman-worker: <batch> spec-phase`;
- `hooks.py release|stopping|forget|run-end` with exit codes 0/1/3;
- piece 7's `SPEC READY` rule;
- piece 10's spec gate;
- `design_spec_dir`;
- batch names.

**Base branch:** `feat/hooks`, **after the hooks plan (0.4.0) has landed on it**. This branch, `feat/spec-phase` in the worktree `/Users/crisis/_workspace/foreman.wt/spec-phase`, must contain `feat/hooks` before Task 1. Task 0 checks this. Every path below is relative to that worktree.

## Global Constraints

- Python 3 standard library only.
- Bash runs on macOS `/bin/bash` 3.2:
  - no `declare -A`, `mapfile`, `${var,,}` or `timeout`;
  - timeouts use `perl alarm`, as `review.sh` does;
  - under `set -u`, never expand an array that may be empty as `"${a[@]}"`, because bash 3.2 reports it as unbound.
- Tests are `python3 tests/<file>.py`, or `check` lines in `tests/run.sh`. Fake adapters and fake CLIs live inside `tests/run.sh`, like the existing ones.
- Commit messages are plain. No `Co-Authored-By`, no `Claude-Session`, no "Generated with".
- Version `0.5.0` in `.claude-plugin/plugin.json`.
- `review.sh` exit contract, unchanged:
  - 0: a review landed;
  - 1: the chain is exhausted;
  - 2: a hard error. This now also covers usage errors and a missing spec file;
  - new: 143 on TERM or INT.
- Adapter contract, unchanged: exit 0 means the review is in `<log>`; exit 10 means fall through; any other exit is a hard error.
- Config defaults, verbatim from the spec:
  - `design_spec_dir` = `docs/superpowers/specs`, which is distinct from `e2e.spec_dir`;
  - `spec_review_rounds` = `3`;
  - `spec_phase_timeout` = `60`, in minutes of **worker time**.
- Prompt placeholders:
  - `{spec_path}`, never `{spec}`, which `e2e.run` uses;
  - `{base}`, which in spec mode is always the **fork base**.
- Template blanks are `⟦FILL: …⟧`. The pre-check greps only for `⟦FILL`.
- The worker reports `SPEC READY <absolute worktree path> <branch> <spec path> <sha>`. The rule is the first line of the worker's last message that starts with `SPEC READY `.
- Tag line:
  - spec phase: `foreman-worker: <batch> spec-phase`;
  - a W4.4 replacement after release: `foreman-worker: <batch>`.
- Batch names are `<YYYYMMDD-HHMM>-w<n><x>` and match `[A-Za-z0-9_-]+`.
- Hooks CLIs: `python3 $FOREMAN/hooks.py release <batch> <sha>`, `stopping <batch>`, `forget <batch>` and `run-end`. Exit 0 means OK, 1 means not recorded, 3 means the hooks are inactive.
- The orchestrator's `review.sh` call is exactly `(cd <abs worktree> && <abs plugin>/scripts/review.sh --spec <spec path> <fork base> <abs log>)`:
  - every path is literal;
  - no `${`, no `$(`, no quotes, no redirects;
  - hooks piece 8 auto-allows only this shape.
- Serial mode is unchanged.

## Review Focus

1. **A spec path containing a space** (e.g. `docs/my specs/d.md`): `review.sh --spec` keeps it as one path in the prompt and in its file check. Test: Task 1.
2. **A `custom` command that already quotes its placeholders** (`cat '{prompt_file}'`, `"{base}"`): it still runs and lands. Test: Task 3.
3. **`origin/main` moved after the batch branched:** the round's base is the fork base, so the reviewer's `git diff <base>...HEAD` shows the whole spec (round 1 and round 2), and none of main's new commits. Test: Task 1.
4. **`--spec` names a file missing from the worktree** (a typo, an uncommitted file, the wrong cwd): exit 2, naming the path, before any adapter runs or spends quota. Test: Task 1.
5. **A diff-in-prompt adapter in spec mode** (prime-agent, pi, …): it falls through at source time, before it builds a diff or calls its CLI. No quota is spent, and no exit 2 comes from `with_diff`. Test: Task 2.

---

## File map

| file | change | task |
|---|---|---|
| `scripts/review.sh` | option parsing, `--spec`, `FOREMAN_REVIEW_MODE`, `<log>.pid`, TERM/INT trap | 1 |
| `templates/spec-review-prompt.md` | new | 1 |
| `reviewers/_lib.sh` | spec-mode fall-through for adapters without `# foreman: reads-repo` | 2 |
| `reviewers/claude.sh`, `reviewers/opencode.sh` | marker; opencode `--agent plan` in spec mode | 2 |
| `reviewers/custom.sh` | `{prompt_file}`/`{base}` substitution; spec-mode rule | 3 |
| `reviewers/codex.sh` | marker; `exec` in spec mode | 4 |
| `templates/spec.md` | new | 5 |
| `scripts/cfg.py`, `templates/foreman.config.md` | three defaults | 6 |
| `skills/foreman/SKILL.md` | Parallel intro, W1–W6, W3.5, W3.6, Worker contract, Non-negotiables, Step 0.5 | 7–12 |
| `README.md`, `.claude-plugin/plugin.json` | spec phase docs, 0.5.0 | 12 |
| `tests/run.sh` | tests for each task | 1–6, 11, 13 |
| `tests/real-reviewers.sh`, `reviewers/muse.sh` (conditional) | `SPEC=` passthrough; muse spec mode if its probe passes | 14 |

Every `tests/run.sh` block in this plan is inserted **immediately before** the line `echo "## backlog.py + ui.py"`, so each block lands after the previous task's block. The blocks reuse helpers defined earlier in the file: `check`, `git`, `chain`, `$A` (the adapters dir with the real adapters copied in), `$F` (fake CLIs), `$T`, `$MUSE_CALLED`, `tc`. Task 1 defines `$S`, `$fork`, `sv`, and the `reader` and `hang` adapters, which later blocks reuse.

Run the suite with `tests/run.sh 2>&1 | grep -E '^(FAIL|passed)'`. The full run takes about a minute because of the gate-lock and stall tests.

---

### Task 0: Preflight on the hooks base (controller)

**Files:** none modified. Write the findings to `<scratchpad>/task0.md`; later tasks read it.

**Interfaces:**
- Consumes: hooks 0.4.0 on `feat/hooks`, which provides `scripts/hooks.py` (`release`, `stopping`, `forget`, `run-end`), the `design_spec_dir` default, and the `foreman-worker:` tag.
- Produces: `task0.md`, which records:
  - (a) every anchor that is NOT found exactly once, with its new location;
  - (b) which spec-phase items the hooks merge already added to SKILL.md, the README or the template config.

- [ ] **Step 1: Confirm the base**

```bash
cd /Users/crisis/_workspace/foreman.wt/spec-phase
git merge-base --is-ancestor feat/hooks HEAD && echo BASE-OK
grep -n '"version"' .claude-plugin/plugin.json
```

Expected: `BASE-OK`, and `"version": "0.4.0"`.

If `BASE-OK` is missing, STOP and ask the user to rebase `feat/spec-phase` onto `feat/hooks`. Rewriting history is the user's call.

- [ ] **Step 2: Confirm the hooks interfaces this plan calls**

```bash
grep -nE "'(release|stopping|forget|run-end)'" scripts/hooks.py
grep -n 'spec-phase' scripts/hooks.py | head -5
grep -rn 'design_spec_dir' scripts/cfg.py scripts/hooks.py
grep -n 'SPEC READY' tests/test_hooks.py
python3 tests/test_hooks.py >/dev/null && tests/run.sh 2>&1 | tail -1
```

Expected:
- all four CLI verbs are found;
- `hooks.py` reads the ` spec-phase` tag suffix;
- `design_spec_dir` appears in `cfg.py` (or in `hooks.py`);
- `test_hooks.py` has a `SPEC READY` stop case, which belongs to the hooks repo and is not this plan's;
- both suites are green, and the last line reads `passed N, failed 0`.

If an interface is missing, STOP and report which one.

- [ ] **Step 3: Check every anchor later tasks edit (each must occur exactly once)**

```bash
anchors() { f=$1; shift; for a; do n=$(grep -cF -- "$a" "$f"); [ "$n" = 1 ] || echo "ANCHOR ${n}x in $f: $a"; done; }
anchors skills/foreman/SKILL.md \
 'zero-surface skip), and `risk != high`.' \
 '- **Behavior-preserving work changes ZERO behavior**' \
 'Every non-negotiable holds for every PR.' \
 'never touch the worktree of a worker that has not reported), then' \
 '  `git worktree prune`.' \
 '    surface yes/no) before launching.' \
 'Keep a short state table (batch, rows, PR, verified SHA, status) instead of re-reading' \
 '- **W4 Each report, as it arrives** (a report is a claim; re-read everything):' \
 '     only owned files plus what the report lists (tests, `parallel_shared_files`), no unexplained deletion.' \
 '     new worker on the branch), then W4.1 again.' \
 'present the ready PRs with their evidence and head SHAs, and stop;' \
 '  6. **Merge.** Reply to and resolve every thread' \
 '  docs nits (a Critical/Important finding still blocks).' \
 'the next pool, report it, and carry on.' \
 '- Step 2: your batch is the approved scope. Write a short plan in the PR body; skip brainstorming and' \
 '  plan-approval gates. If a row needs a product decision, do the decidable part and report the rest.' \
 '- Own only your files (and your own entries in `parallel_shared_files`); if you need another file, stop and report.' \
 '- Remote work: the `git`+`gh` column of the merge-tool mapping; open the PR ready for review.' \
 '- Never merge. Poll for new bot comments after your last push, and stop pushing once you report.' \
 '- Report: PR URL, head SHA, check states on it, unresolved threads, per-row changes, evidence, files outside' \
 'numbers `<prefix>-NEW-*` from the real max id).' \
 '| `shared_resources` |'
anchors README.md \
 '`parallel N` runs N workers in separate git worktrees while one orchestrator owns merging.' \
 '| `/foreman parallel 4 [--auto-merge]` | up to 4 worktree workers per wave |' \
 '`pre_browser_check`, `worktree_env_files`, `worktree_install`, `stale_check`, `gate_slots`, `parallel_*`,' \
 'diff. No adapter runs with a bypass, yolo or skip-permissions flag.' \
 '| `codex` | `codex -m M -c model_reasoning_effort=E --sandbox read-only review --base B` | sandbox | verified (CLI 0.160.0, gpt-6.1-sol) |' \
 '| `custom` | the legacy `extra_reviewers` command, run with `bash -c` | whatever the command does | — |' \
 'nothing until it finishes.' \
 '| `scripts/review.sh <base> <log>` | the reviewer chain |' \
 '## Backlog UI' \
 'tests/run.sh                       # offline: config parsing, bk.py, worktree setup, gate locks, the chain with fake reviewers'
anchors scripts/review.sh \
 "# Run the project's cross-model reviewer chain against <base>...HEAD." \
 '#   review.sh <base> <logfile>' \
 '# lists every reviewer tried and why it fell through.' \
 'base=${1:?usage: review.sh <base> <logfile>}' \
 'logfile=${2:?usage: review.sh <base> <logfile>}' \
 'mkdir -p "$(dirname "$logfile")"' \
 "B=\$base perl -pe 's/\\{base\\}/\$ENV{B}/g' \"\${FOREMAN_PROMPT_TEMPLATE:-\$plugin/templates/review-prompt.md}\" >\"\$work/prompt.md\"" \
 '  kill -KILL -- "-$pid" 2>/dev/null   # whatever the adapter left behind (codex spawns dozens of children)'
anchors reviewers/_lib.sh 'fall() { echo "fall through: $*" >&2; exit 10; }'
anchors reviewers/opencode.sh '[ -n "$effort" ] && args+=(--variant "$effort")' '# and retries a 429 forever, so JSON `"type":"error"` events (and retry/rate-limit lines) fall through early.'
anchors reviewers/claude.sh '# stream-json so the stall watchdog sees progress; the review is the final `result` event.'
anchors reviewers/codex.sh 'codex "${args[@]}" --sandbox read-only review --base "$base"'
anchors reviewers/custom.sh 'bash -c "$model"'
anchors tests/run.sh 'echo "## backlog.py + ui.py"'
anchors scripts/cfg.py 'def get(repo, key, default=None):'
anchors templates/foreman.config.md 'shared_resources: []            # rules every worker follows'
anchors .claude-plugin/plugin.json '"version": "0.4.0"'
echo anchors-checked
```

Expected: only `anchors-checked`. On `feat/spec-phase` before the hooks merge, every anchor was confirmed unique on 2026-10-02.

For every `ANCHOR` line printed:
- find where the hooks merge moved that text;
- write the new exact text in `task0.md` next to the task number that uses it (see the File map).

The executing task uses the recorded text in place of the plan's anchor. It never edits a different spot.

- [ ] **Step 4: Record what the hooks merge already added**

```bash
grep -n 'YYYYMMDD' skills/foreman/SKILL.md                     # W2 batch-name rule → Task 7 step 3
grep -n 'foreman-worker: <batch>' skills/foreman/SKILL.md      # W3 tag sentence → Task 7 step 4
grep -n 'hooks.py forget' skills/foreman/SKILL.md              # W5 forget → Task 11 step 3
grep -n 'hooks.py stopping' skills/foreman/SKILL.md            # TaskStop rule → Tasks 9–11
grep -n 'hooks.py release' skills/foreman/SKILL.md             # W3.6 → Task 9
grep -n 'design_spec_dir' skills/foreman/SKILL.md templates/foreman.config.md README.md   # → Tasks 6, 12
grep -n 'trap ' scripts/review.sh                              # existing traps → Task 1 step 4(d) folds them in
```

Copy each hit, with its line, into `task0.md`. The task numbered in each comment uses it:
- a hit means "already present": the task replaces or extends that text and does not add a duplicate;
- no hit means the task inserts the text as written.

No commit.

---

### Task 1: `review.sh --spec`: option parsing, spec prompt, TERM trap

**Files:**
- Modify: `scripts/review.sh` (header comment, argument lines, prompt line, trap and pid file, and a `pid=` reset)
- Create: `templates/spec-review-prompt.md`
- Test: `tests/run.sh` (new block)

**Interfaces:**
- Consumes: the adapter contract (`reviewers/<name>.sh <base> <model> <effort> <prompt_file> <log>`).
- Produces:
  - **Call:** `review.sh [--spec <spec path>] <base> <logfile>`. Options may come before or after the positionals; `--` ends options.
  - **Exit codes:** 0, 1, 2 or 143. Usage errors exit 2, and so does a missing `--spec` file, which is checked before any adapter runs.
  - **Mode variable:** review.sh always exports `FOREMAN_REVIEW_MODE`, set to `review` or `spec`, to every adapter.
  - **Prompt:** in spec mode it is `templates/spec-review-prompt.md` with `{spec_path}` and `{base}` filled. Review mode is unchanged: `FOREMAN_PROMPT_TEMPLATE` or `templates/review-prompt.md`, with `{base}` filled.
  - **Pid file:** `<logfile>.pid` holds review.sh's pid while it runs. It is removed on every exit after it was written.
  - **TERM or INT:** kill the running adapter's process group, start no other adapter, and exit 143.

- [ ] **Step 1: Write the failing tests**

Insert this block in `tests/run.sh` immediately before `echo "## backlog.py + ui.py"`:

```bash
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
check "fork base: the reviewed diff has round 1" grep -q 'ROUND-ONE' "$T/out/spec.log"
check "fork base: ...and round 2" grep -q 'ROUND-TWO' "$T/out/spec.log"
check "fork base: main's later commits are not in it" bash -c "! grep -q MAIN-MOVED '$T/out/spec.log'"
check "pid file removed after a normal exit" test ! -e "$T/out/spec.log.pid"
sv "$fork" "$T/out/plain.log"
check "plain call still works (exit 0)" test $? = 0
check "plain call: FOREMAN_REVIEW_MODE=review" grep -qx 'mode=review' "$T/out/plain.log"
check "plain call: the PR review template" grep -q 'You are reviewing a pull request' "$T/out/plain.log"
sv; check "no arguments: usage, exit 2" test $? = 2
sv --spec; check "--spec without a value: exit 2" test $? = 2
sv --bogus "$fork" "$T/out/x.log"; check "unknown option: exit 2" test $? = 2
sv --spec "docs/my specs/d.md" "$fork" "$T/out/x.log" extra; check "extra argument: exit 2" test $? = 2
sv --spec docs/nope.md "$fork" "$T/out/x.log"; check "missing spec file: exit 2" test $? = 2
check "...naming the path" grep -q 'docs/nope.md' "$T/sv.err"
check "...before any adapter ran" test ! -e "$T/out/x.log.attempts"
sv "$fork" "$T/out/opt.log" --spec "docs/my specs/d.md"; check "an option after the positionals parses" grep -qx 'mode=spec' "$T/out/opt.log"
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
pkill -f '^sleep 299$' 2>/dev/null; unset HANG_PIDFILE
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
```

- [ ] **Step 2: Run them; they must fail**

Run: `tests/run.sh 2>&1 | grep -E '^(FAIL|passed)'`

Expected FAILs, on every positive check in the new block:
- `--spec: lands`, the mode, template and placeholder checks, and both `fork base: …round` checks;
- all three `plain call` checks. The reader reads `FOREMAN_REVIEW_MODE`, which the old script doesn't set, so it crashes under `set -u`;
- every `exit 2` check, and `...naming the path`;
- `pid file holds review.sh's pid`, and `TERM: review.sh exits 143`;
- every `spec-review prompt has` check.

Absence checks (`no placeholder left`, `main's later commits are not in it`, `pid file removed`, `...before any adapter ran`) may already pass. The earlier blocks stay green.

- [ ] **Step 3: Create `templates/spec-review-prompt.md`**

```markdown
You are reviewing a design spec, not code. The spec is `{spec_path}` (a path relative to the repository root), committed on top of `{base}` (`git diff {base}...HEAD` shows it). It describes a change that has not been written yet: judge whether building it as written would work.
You are a reviewer only. Do not edit files, run builds, install anything or make network calls.

## First, build your own list

Before you read the spec's "Callers and writers checked" section, read only its **Rows** and **Change** sections. From them, list every symbol, column, status and file the change touches, and find every caller and every writer of each one (`git grep`, or your Grep tool). For each write, note which client or credentials perform it and which roles reach it. Only then compare your list with the spec's.

## What to find

- A caller or writer the spec missed, including a write whose client or role would be refused (row-level security, permissions).
- A wrong claim about current behaviour: check every `path:line` the spec cites.
- A change that breaks a rule the spec lists, or a rule from the repository's rules files that applies and is not listed.
- A test that can't fail, or that misses the change.
- An Implementation outline step that doesn't achieve its row's acceptance.
- A failure path the design doesn't handle.
- Scope outside the stated files: the outline must stay inside the batch's owned files, and any other file must be listed as a scope request.

Check each claim against the code before you report it. Skip style, wording and speculative "might want to" ideas.

## Report

Report each finding as:

- **[P1|P2|P3] `<spec section>`** what is wrong, the evidence (`path:line`, or the `git grep` that shows it), and the fix to the spec.

P1 = the design is wrong or unsafe as written. P2 = a likely defect or a gap to close before coding. P3 = minor. If you find nothing, say "No issues found."

End with one line: `Verdict: <ready to build | revise | redesign>`.
```

- [ ] **Step 4: Edit `scripts/review.sh`**

The hooks merge changed how this script finds the config (the `repo=`/`cfg()` lines). Leave those lines exactly as they are, and make only these five edits.

(a) Replace the two header lines

```bash
# Run the project's cross-model reviewer chain against <base>...HEAD.
#   review.sh <base> <logfile>
```

with:

```bash
# Run the project's cross-model reviewer chain.
#   review.sh <base> <logfile>                      review <base>...HEAD (serial step 6, parallel W4)
#   review.sh --spec <spec path> <base> <logfile>   review a design spec (parallel W3.5): the prompt is
#     templates/spec-review-prompt.md, <base> is the batch's fork base, and only adapters marked
#     `# foreman: reads-repo` run (the others fall through in _lib.sh). Adapters see FOREMAN_REVIEW_MODE.
```

(b) After the line `# lists every reviewer tried and why it fell through.`, insert:

```bash
# Exit 2: a usage error, a missing --spec file, or not in a git repo.
# Exit 143: TERM or INT; the running adapter's process group is killed and no other adapter starts.
# <logfile>.pid holds this script's pid while it runs (send TERM there to stop the chain).
```

(c) Replace

```bash
base=${1:?usage: review.sh <base> <logfile>}
logfile=${2:?usage: review.sh <base> <logfile>}
```

with:

```bash
usage() { echo "usage: review.sh [--spec <spec path>] <base> <logfile>" >&2; exit 2; }
mode=review spec="" pos=()
while [ $# -gt 0 ]; do
  case $1 in
    --spec) [ $# -ge 2 ] || usage; spec=$2; mode=spec; shift 2 ;;
    --) shift; pos+=("$@"); break ;;
    -*) usage ;;
    *) pos+=("$1"); shift ;;
  esac
done
[ ${#pos[@]} = 2 ] || usage
base=${pos[0]} logfile=${pos[1]}
if [ "$mode" = spec ] && [ ! -f "$spec" ]; then
  echo "review.sh: no spec file at '$spec' (relative to $PWD)" >&2; exit 2
fi
export FOREMAN_REVIEW_MODE=$mode
```

(d) After the line `mkdir -p "$(dirname "$logfile")"`, insert the lines below. If `task0.md` lists an existing `trap` in review.sh (from the hooks merge), fold its commands into these two traps (a later `trap` for the same signal silently replaces the earlier one):

```bash
echo $$ >"$logfile.pid"
trap 'rm -f "$logfile.pid"' EXIT
trap '[ -n "${pid:-}" ] && kill -TERM -- "-$pid" 2>/dev/null; exit 143' TERM INT
```

Then replace the prompt line

```bash
B=$base perl -pe 's/\{base\}/$ENV{B}/g' "${FOREMAN_PROMPT_TEMPLATE:-$plugin/templates/review-prompt.md}" >"$work/prompt.md"
```

with:

```bash
template=${FOREMAN_PROMPT_TEMPLATE:-$plugin/templates/review-prompt.md}
[ "$mode" = spec ] && template=$plugin/templates/spec-review-prompt.md
B=$base S=$spec perl -pe 's/\{base\}/$ENV{B}/g; s/\{spec_path\}/$ENV{S}/g' "$template" >"$work/prompt.md"
```

(e) After the line `  kill -KILL -- "-$pid" 2>/dev/null   # whatever the adapter left behind (codex spawns dozens of children)`, insert:

```bash
  pid=""   # between adapters a TERM has no group to kill (and must not hit a reused pid)
```

- [ ] **Step 5: Run the tests; they must pass**

Run: `bash -n scripts/review.sh && tests/run.sh 2>&1 | grep -E '^(FAIL|passed)'`

Expected: no FAIL lines, and `passed N, failed 0`.

- [ ] **Step 6: Commit**

```bash
git add scripts/review.sh templates/spec-review-prompt.md tests/run.sh
git commit -m "review.sh: --spec mode with the spec-review prompt, option parsing, pid file and TERM trap"
```

---

### Task 2: Spec-capable adapters (`# foreman: reads-repo`) and the spec-mode fall-through

**Files:**
- Modify: `reviewers/_lib.sh` (after the `fall()` line); `reviewers/claude.sh` and `reviewers/opencode.sh` (marker; opencode `--agent plan`)
- Test: `tests/run.sh` (new block)

**Interfaces:**
- Consumes: from Task 1, `FOREMAN_REVIEW_MODE`, `sv`, `$S`, `$fork`, and the `reader` adapter.
- Produces:
  - The marker line `# foreman: reads-repo`, matched with `^# foreman: reads-repo`.
  - In spec mode, an adapter without the marker exits 10 at source time with `fall through: spec mode needs an adapter that reads the repo …`.
  - `opencode` adds `--agent plan` in spec mode.
  - `codex` (Task 4) and `custom` (Task 3) get the marker in their own tasks. `muse` gets it only if its probe passes (Task 14).

- [ ] **Step 1: Write the failing tests**

Insert immediately before `echo "## backlog.py + ui.py"`:

```bash
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
```

- [ ] **Step 2: Run them; they must fail**

Run: `tests/run.sh 2>&1 | grep -E '^(FAIL|passed)'`

Expected FAILs:
- `a non-reads-repo adapter falls through` (good lands instead), and `...noting why`;
- `...each naming spec mode`, and `...without calling either CLI`. The fakes get called. The chain still ends exhausted with two exit-10s, so those two checks already pass;
- `is marked reads-repo` for claude and opencode;
- `...with the plan agent`.

- [ ] **Step 3: Implement**

In `reviewers/_lib.sh`, after the line `fall() { echo "fall through: $*" >&2; exit 10; }`, insert:

```bash

# Spec mode (review.sh --spec): only a reviewer that reads the repo can check a design against the code.
# Every adapter without a `# foreman: reads-repo` line falls through here, at source time, before it
# builds a diff or calls its CLI.
if [ "${FOREMAN_REVIEW_MODE:-}" = spec ] && ! grep -q '^# foreman: reads-repo' "$0"; then
  fall "spec mode needs an adapter that reads the repo (no '# foreman: reads-repo' line in $(basename "$0"))"
fi
```

In `reviewers/claude.sh`, after the line ``# stream-json so the stall watchdog sees progress; the review is the final `result` event.``, insert:

```bash
# foreman: reads-repo
```

In `reviewers/opencode.sh`:
- After the line ``# and retries a 429 forever, so JSON `"type":"error"` events (and retry/rate-limit lines) fall through early.``, insert:

```bash
# Spec mode runs the read-only `plan` agent.
# foreman: reads-repo
```

- After the line `[ -n "$effort" ] && args+=(--variant "$effort")`, insert:

```bash
[ "${FOREMAN_REVIEW_MODE:-}" = spec ] && args+=(--agent plan)
```

- [ ] **Step 4: Run the tests; they must pass**

Run: `for f in reviewers/*.sh; do bash -n "$f"; done && tests/run.sh 2>&1 | grep -E '^(FAIL|passed)'`

Expected: `passed N, failed 0`.

- [ ] **Step 5: Commit**

```bash
git add reviewers/_lib.sh reviewers/claude.sh reviewers/opencode.sh tests/run.sh
git commit -m "reviewers: reads-repo marker; spec mode runs only repo-reading adapters, opencode with its plan agent"
```

---

### Task 3: `custom.sh`: `{prompt_file}` and `{base}` substitution

**Files:**
- Modify: `reviewers/custom.sh` (whole file; 9 lines today)
- Test: `tests/run.sh` (new block)

**Interfaces:**
- Consumes: the Task 2 marker rule, and `FOREMAN_REVIEW_MODE`.
- Produces:
  - `custom.sh` replaces `{prompt_file}` and `{base}` in its command in both modes.
  - Each value is single-quoted for the shell. A pair of quotes directly around a placeholder (`'{x}'` or `"{x}"`) is absorbed.
  - In spec mode, a command missing either placeholder exits 10 and names the missing one.

- [ ] **Step 1: Write the failing tests**

Insert immediately before `echo "## backlog.py + ui.py"`:

```bash
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
cd "$T"
```

- [ ] **Step 2: Run them; they must fail**

Run: `tests/run.sh 2>&1 | grep -E '^(FAIL|passed)'`

Expected FAILs:
- `{prompt_file} is the prompt` and `{base} filled` (the braces reach bash literally);
- the spec-mode checks that need custom to run or to name a placeholder: `with both placeholders lands`, `...reading the spec prompt`, `already-quoted placeholders`, and both `falls through naming it`. Custom has no marker yet, so `_lib.sh` drops it with the generic spec-mode reason;
- `custom.sh is marked reads-repo`.

`...exhausted (exit 1)` and `a legacy extra_reviewers command in spec mode never runs` already pass, for that same reason.

- [ ] **Step 3: Replace `reviewers/custom.sh` with**

```bash
#!/bin/bash
# Legacy `extra_reviewers` entry: <model> carries a whole command line, run with bash -c in the repo.
# Its stdout is the review. Used when the config has `extra_reviewers` and no `reviewers` chain.
# `{prompt_file}` and `{base}` in the command become the prompt file and the base, each single-quoted for the
# shell (a pair of quotes written directly around a placeholder is absorbed). In spec mode (review.sh --spec)
# the command must use both: that is the only way it sees the spec prompt. Otherwise it falls through.
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
  $c =~ s/([\x27"]?)\{prompt_file\}\1/shq($ENV{P})/ge;
  $c =~ s/([\x27"]?)\{base\}\1/shq($ENV{B})/ge;
  print $c' "$model")
run_tool '^ERROR: \{"type":"error".*(not supported|newer version|usage limit|rate.?limit|quota|credits|unauthorized|status":(401|403|429))' '' /dev/null bash -c "$cmd"
rc=$?; [ $rc = 0 ] || exit $rc
cp "$OUT" "$log"
finish
```

- [ ] **Step 4: Run the tests; they must pass**

Run: `bash -n reviewers/custom.sh && tests/run.sh 2>&1 | grep -E '^(FAIL|passed)'`

Expected: `passed N, failed 0`. The earlier `legacy extra_reviewers -> custom` cfg check stays green.

- [ ] **Step 5: Commit**

```bash
git add reviewers/custom.sh tests/run.sh
git commit -m "custom reviewer: substitute {prompt_file} and {base}; spec mode needs both"
```

---

### Task 4: codex runs `exec` in spec mode

**Files:**
- Modify: `reviewers/codex.sh` (whole file; 14 lines today)
- Test: `tests/run.sh` (new block)

**Interfaces:**
- Consumes: from Tasks 1–2, `FOREMAN_REVIEW_MODE` and the marker rule.
- Produces:
  - **Spec mode:** `codex [-m M] [-c model_reasoning_effort="E"] --sandbox read-only exec --ephemeral --color never -o <log> -`, with the prompt file on stdin.
  - **Review mode:** unchanged, `… --sandbox read-only review --base <base>` with the review on stdout.

- [ ] **Step 1: Verify the flags against the installed CLI**

```bash
codex --version
codex --help | grep -E -- '^\s+-s, --sandbox|^\s+-m, --model|^\s+-c, --config'
codex exec --help | grep -E -- '--ephemeral|--color <COLOR>|-o, --output-last-message|^\s+\[PROMPT\]'
codex exec --help | grep -A3 -- '^\s*\[PROMPT\]' | grep -q 'read from stdin' && echo STDIN-OK
```

Expected for `codex-cli 0.160.0`, checked on 2026-10-02:
- the three global flags;
- `--ephemeral`, `--color <COLOR>`, `-o, --output-last-message <FILE>` and `[PROMPT]`;
- `STDIN-OK`.

If any line is missing on the installed version, STOP and report the version and the missing flag. Don't guess a replacement.

- [ ] **Step 2: Write the failing tests**

Insert immediately before `echo "## backlog.py + ui.py"`:

```bash
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
unset CODEX_ARGS
cd "$T"
```

- [ ] **Step 3: Run them; they must fail**

Run: `tests/run.sh 2>&1 | grep -E '^(FAIL|passed)'`

Expected FAILs:
- every positive `codex spec mode` check (`lands`, `-o message`, `runs exec`, `--sandbox precedes exec`, `read-only`, `stdin`, `model and effort`, `passes --ephemeral`, `passes --color`). Without the marker, codex falls through in spec mode before the fake writes its args file;
- `codex.sh is marked reads-repo`.

The `never passes` checks and the review-mode checks already pass.

- [ ] **Step 4: Replace `reviewers/codex.sh` with**

```bash
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
```

- [ ] **Step 5: Run the tests; they must pass**

Run: `bash -n reviewers/codex.sh && tests/run.sh 2>&1 | grep -E '^(FAIL|passed)'`

Expected: `passed N, failed 0`. The two older codex checks (`ERROR lines from the transcript do not fall through`, `its JSON error line falls through`) stay green.

- [ ] **Step 6: Commit**

```bash
git add reviewers/codex.sh tests/run.sh
git commit -m "codex reviewer: exec with the spec prompt in spec mode (flags checked against codex 0.160.0)"
```

---

### Task 5: `templates/spec.md` and the `⟦FILL` pre-check

**Files:**
- Create: `templates/spec.md`
- Test: `tests/run.sh` (new block)

**Interfaces:**
- Produces:
  - **The template's sections**, as `## ` headings in this order: `Rows`, `Current behaviour`, `Change`, `Callers and writers checked`, `Rules that apply`, `Tests`, `Implementation outline`, `Surface`, `Risks`, `Open questions`, `Review responses`.
  - **Blanks:** every blank is a single-line `⟦FILL: …⟧`. `Review responses` reads `None — round 1`.
  - **Scope requests** are lines that start `- **Scope request:**`. The template asks for them inside the outline's `⟦FILL⟧` text, so no example line is left behind.
  - **The two pre-check commands** that Task 8 writes into W3.5 and Task 13 pins. `<spec path>` is the spec, and `${CLAUDE_PLUGIN_ROOT}` is the plugin root:

    ```
    grep -n '⟦FILL' <spec path>
    grep '^## ' ${CLAUDE_PLUGIN_ROOT}/templates/spec.md | while IFS= read -r h; do grep -qxF "$h" <spec path> || echo "missing section: $h"; done
    ```

- [ ] **Step 1: Write the failing tests**

Insert immediately before `echo "## backlog.py + ui.py"`. The `fills` and `missing` bodies are the two W3.5 commands, with `<spec path>` replaced by `$1` and the template path by `$tpl`. Task 13 pins the SKILL.md text to them.

````bash
echo "## templates/spec.md and the W3.5 pre-check"
tpl=$root/templates/spec.md
fills() { grep -n '⟦FILL' "$1"; }
missing() { grep '^## ' "$tpl" | while IFS= read -r h; do grep -qxF "$h" "$1" || echo "missing section: $h"; done; }
check "spec template: the spec's sections, in order" test "$(grep '^## ' "$tpl" 2>/dev/null | tr '\n' '|')" = '## Rows|## Current behaviour|## Change|## Callers and writers checked|## Rules that apply|## Tests|## Implementation outline|## Surface|## Risks|## Open questions|## Review responses|'
check "spec template: Review responses reads 'None — round 1'" grep -qx 'None — round 1' "$tpl"
check "spec template: scope requests have a stated form" grep -qF -- '- **Scope request:**' "$tpl"
check "pre-check flags the unfilled template" test "$(fills "$tpl" | wc -l)" -ge 10
check "pre-check: the template itself has every section" test -z "$(missing "$tpl")"
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
````

- [ ] **Step 2: Run them; they must fail**

Run: `tests/run.sh 2>&1 | grep -E '^(FAIL|passed)'`

Expected: FAIL on every `spec template` check, on `pre-check flags the unfilled template`, and on `pre-check names a missing section`, because there is no template. The other `pre-check` checks already pass, since with no template there are no sections to miss.

- [ ] **Step 3: Create `templates/spec.md`**

```markdown
# ⟦FILL: the batch's row ids joined by " + "⟧ — design

Batch `⟦FILL: batch name⟧` on branch `⟦FILL: branch⟧`. Written in the spec phase: no code for it exists yet. A section with nothing in it reads "None — <reason>"; never delete a section.

## Rows

⟦FILL: for each row, its id, title and acceptance, quoted from the backlog⟧

## Current behaviour

⟦FILL: what happens today, with `path:line` evidence for every claim⟧

## Change

⟦FILL: what will be different, per row⟧

## Callers and writers checked

⟦FILL: for each symbol, column, status or file the change touches: every site `git grep` found and why each one is or isn't affected; for each write, the client or credentials that perform it, the roles that reach it, and why row-level security or permissions allow it⟧

## Rules that apply

⟦FILL: each rules-file entry that applies, quoted with file:line, and how the change honours it⟧

## Tests

⟦FILL: the failing tests to write first, each with the behaviour it pins; mutants the tests must kill, where useful⟧

## Implementation outline

⟦FILL: the files in order and what changes in each, inside the batch's owned files and its own entries in parallel_shared_files; list any other file the change needs on its own line as "- **Scope request:** `<file>` — <why>"⟧

## Surface

⟦FILL: app surface yes or no; if yes, the planned browser check's claims and its control on main⟧

## Risks

⟦FILL: what could go wrong, and how the change or the tests cover it⟧

## Open questions

⟦FILL: product decisions the code can't answer, or "None"⟧

## Review responses

None — round 1
```

- [ ] **Step 4: Run the tests; they must pass**

Run: `tests/run.sh 2>&1 | grep -E '^(FAIL|passed)'`

Expected: `passed N, failed 0`.

- [ ] **Step 5: Commit**

```bash
git add templates/spec.md tests/run.sh
git commit -m "templates: batch spec template with FILL markers; pre-check tests"
```

---

### Task 6: Config keys `design_spec_dir`, `spec_review_rounds`, `spec_phase_timeout`

**Files:**
- Modify: `scripts/cfg.py` (the defaults; see step 3) and `templates/foreman.config.md` (a new section after the `shared_resources:` line)
- Test: `tests/run.sh` (new block)

**Interfaces:**
- Consumes: hooks 0.4.0's `design_spec_dir` default, recorded in `task0.md`.
- Produces: with no config, `cfg.py <repo> design_spec_dir` prints `docs/superpowers/specs`, `spec_review_rounds` prints `3`, and `spec_phase_timeout` prints `60`. A config value wins. SKILL.md substitutes `{{design_spec_dir}}` into paths, and names the other two keys plainly in the bounds table.

- [ ] **Step 1: Write the failing tests**

Insert immediately before `echo "## backlog.py + ui.py"`:

```bash
echo "## spec-phase config defaults"
nc() { FOREMAN_CONFIG=/nonexistent python3 "$root/scripts/cfg.py" "$T" "$@"; }
check "default design_spec_dir" test "$(nc design_spec_dir)" = docs/superpowers/specs
check "default spec_review_rounds" test "$(nc spec_review_rounds)" = 3
check "default spec_phase_timeout" test "$(nc spec_phase_timeout)" = 60
check "e2e's spec_dir is a different key (no default)" test -z "$(nc spec_dir)"
printf -- '---\nspec_review_rounds: 2\n---\n' >"$T/sr.md"
check "a config value wins over the default" test "$(FOREMAN_CONFIG=$T/sr.md python3 "$root/scripts/cfg.py" "$T" spec_review_rounds)" = 2
check "template: design_spec_dir" test "$(tc design_spec_dir)" = docs/superpowers/specs
check "template: spec_review_rounds" test "$(tc spec_review_rounds)" = 3
check "template: spec_phase_timeout" test "$(tc spec_phase_timeout)" = 60
check "template: design_spec_dir defined once" test "$(grep -c '^design_spec_dir:' "$root/templates/foreman.config.md")" = 1
```

- [ ] **Step 2: Run them; they must fail**

Run: `tests/run.sh 2>&1 | grep -E '^(FAIL|passed)'`

Expected FAILs:
- `default spec_review_rounds` and `default spec_phase_timeout`;
- `template: spec_review_rounds` and `template: spec_phase_timeout`;
- `template: design_spec_dir defined once`, unless the hooks merge added it.

If the hooks merge did NOT put a `design_spec_dir` default in `cfg.py`, the other two `design_spec_dir` checks fail too.

- [ ] **Step 3: Add the defaults in `scripts/cfg.py`**

Run `grep -n 'DEFAULTS\|design_spec_dir' scripts/cfg.py`.

- **(a) A defaults dict holding `'design_spec_dir'` exists (hooks 0.4.0 shipped it).** Add these two entries on the lines right after its `'design_spec_dir'` entry:

```python
    'spec_review_rounds': '3',
    'spec_phase_timeout': '60',
```

- **(b) No defaults mechanism exists in `cfg.py`.** Insert this above `def get(repo, key, default=None):`

```python
# Defaults for keys whose callers pass none. An explicit default from the caller still wins.
DEFAULTS = {
    'design_spec_dir': 'docs/superpowers/specs',
    'spec_review_rounds': '3',
    'spec_phase_timeout': '60',
}

```

and replace the body of `get` with:

```python
def get(repo, key, default=None):
    v = load(repo).get(key)
    if v is None:
        v = default if default is not None else DEFAULTS.get(key)
    return v
```

- [ ] **Step 4: Document the keys in `templates/foreman.config.md`**

If `task0.md` shows a `design_spec_dir:` line in the template already, delete that line. The section below holds the only copy.

Then, after the line that starts `shared_resources: []            # rules every worker follows`, insert:

```yaml

# --- spec phase (parallel mode: every batch's spec is reviewed before any code) -----------------------
design_spec_dir: docs/superpowers/specs   # where batch specs are committed; not e2e.spec_dir; no spaces (hooks auto-allow only unquoted arguments) [docs/superpowers/specs]
spec_review_rounds: 3           # review rounds without a pass before a batch is parked [3]
spec_phase_timeout: 60          # minutes of worker time in the spec phase before a batch is parked [60]
```

- [ ] **Step 5: Run the tests; they must pass**

Run: `python3 -m py_compile scripts/cfg.py && tests/run.sh 2>&1 | grep -E '^(FAIL|passed)'`

Expected: `passed N, failed 0`. If `tests/test_hooks.py` exists, `python3 tests/test_hooks.py` stays green too.

- [ ] **Step 6: Commit**

```bash
git add scripts/cfg.py templates/foreman.config.md tests/run.sh
git commit -m "config: spec_review_rounds and spec_phase_timeout defaults; document the spec-phase keys"
```

---

### Task 7: SKILL.md: the Parallel intro, W2, W3 and the Worker contract

**Files:**
- Modify: `skills/foreman/SKILL.md`

**Interfaces:**
- Consumes: Task 0's `task0.md` (the W2 batch-name hit, the W3 tag hit, any moved anchors), and the template path `${CLAUDE_PLUGIN_ROOT}/templates/spec.md` (Task 5).
- Produces, all of which Tasks 8–13 and 14 rely on:
  - **Worker contract step 2** is the spec phase, sub-steps 1–8, with the `SPEC READY` line and `## Deviations after release`.
  - **The W3 sub-bullets** "Spec phase" and "State table".
  - **The W2 sub-bullets** "Batch names" and "Parked rows".

Apply each edit with the Edit tool, using the exact old text shown. Where `task0.md` records a moved anchor, use its recorded text.

- [ ] **Step 1: Parallel mode intro**

Replace `Every non-negotiable holds for every PR.` with:

```
Every non-negotiable holds for every PR. Each batch first writes a design spec, which is reviewed until solid (W3.5) and released (W3.6) before any code.
```

- [ ] **Step 2: W3's state table**

Replace `Keep a short state table (batch, rows, PR, verified SHA, status) instead of re-reading` with:

```
Keep a short state table (columns in the sub-bullet below) instead of re-reading
```

- [ ] **Step 3: W2 sub-bullets.** Insert after the line `    surface yes/no) before launching.`

If `task0.md` has a `YYYYMMDD` hit in W2, omit the first sub-bullet ("Batch names"). Insert the rest:

```
  - **Batch names** are `<YYYYMMDD-HHMM>-w<n><x>`: the invocation's start time, the wave number and a letter
    (e.g. `20261002-1530-w1a`). They are unique across invocations and `/loop` ticks; the hooks key their state
    files on them.
  - **Parked rows** (W3.6). A row stays out of the pool, until the human edits the note away, when its note
    contains `spec parked (open question)` or `spec parked (not released by the human)`, or when it has two
    `spec parked (` notes. Any other `spec parked (` row is eligible again, and its worker starts from the
    committed parked spec (Worker contract step 2.4).
```

- [ ] **Step 4: W3 tag and sub-bullets**

If `task0.md` shows `foreman-worker: <batch>` inside the `- **W3 Launch.**` bullet, change that occurrence, and only that one, to `foreman-worker: <batch> spec-phase`. W4.4's replacement keeps the plain tag.

Then insert these lines immediately before the line `- **W4 Each report, as it arrives** (a report is a claim; re-read everything):`. The line above that one must belong to the W3 bullet. If the hooks merge put another top-level bullet between W3 and W4, insert after W3's last line instead.

```
  - **Spec phase.** Every batch starts with the spec phase (Worker contract step 2). The prompt's first line is
    exactly `foreman-worker: <batch> spec-phase`: the ` spec-phase` suffix opts the batch into the hooks' spec
    gate (no code before release) and their spec-phase report check. The prompt also carries the absolute path
    `${CLAUDE_PLUGIN_ROOT}/templates/spec.md`, the resolved `{{design_spec_dir}}`, and the line "Your batch
    starts with the spec phase: write and commit the spec, report `SPEC READY`, and wait for the release."
    Workers launch together; each batch then moves through spec, review and build on its own.
  - **State table** columns: batch, rows, owned files, worktree, branch, spec path, fork base, round, pinned SHA,
    released SHA, PR, verified SHA, the clock's timestamps and timer task ids (W3.5), and status (`spec`,
    `review <r>`, `passed`, `parked: <reason>`, `released`, `PR #<n>`).
```

- [ ] **Step 5: Worker contract step 2**

Replace these two lines

```
- Step 2: your batch is the approved scope. Write a short plan in the PR body; skip brainstorming and
  plan-approval gates. If a row needs a product decision, do the decidable part and report the rest.
```

with:

```
- **Step 2 is the spec phase** when your prompt's first line ends in ` spec-phase`. No code before the
  orchestrator releases your spec: until then edit and commit nothing outside `{{design_spec_dir}}`, and push
  nothing. (A prompt whose tag has no ` spec-phase` replaces a worker whose spec is already released: go to 8.)
  1. After the setup above, read your rows, the `{{rules_glob}}` files for the area and recent feedback memories.
  2. Explore before designing: read the code the rows name; `git grep` every caller and every writer of each
     function, column, status or file the change touches; for each write, find which client or credentials
     perform it, which roles or permissions reach that path, and why row-level security or permissions allow it;
     note which rules entries apply.
  3. Invoke `superpowers:brainstorming` for its exploration and design discipline, with these overrides, which
     win over the skill's own text: always write the spec file from the template, whatever path the skill would
     classify the batch as (a "bounded" batch still gets a spec); no visual companion, no per-section approval
     and no questions to the user (a question the code can't answer goes under **Open questions**); the
     orchestrator's release is the skill's HARD-GATE approval, so never end a turn with a question: end the spec
     phase with `SPEC READY`; don't invoke `writing-plans`: write the Implementation outline section instead.
  4. Write the spec from the template (its absolute path is in your prompt) to
     `{{design_spec_dir}}/<YYYY-MM-DD>-<row ids joined by ->-design.md`. Replace every `⟦FILL: …⟧` marker; a
     section with nothing in it reads "None — <reason>". A file outside your owned files goes in the outline as a
     `- **Scope request:**` line. If `{{design_spec_dir}}` already holds a parked spec for these rows
     (`Status: parked`), start from it: delete its `Status: parked` line and its old Review responses, then revise
     it. Commit it with `git add -- <spec path> && git commit`, never `git add -A`, as your branch's first commit.
     Don't push. The build phase continues on this same branch.
  5. Report with `SPEC READY <absolute worktree path> <branch> <spec path> <sha>` as the FIRST line of your final
     message, then up to five summary lines, and stop. `SPEC READY` is not your final report.
  6. If you stop without `SPEC READY` (a question, a context limit, an error), the orchestrator resumes you once:
     finish the spec, put questions under Open questions, and end with `SPEC READY` as the first line.
  7. On findings: revise the spec as a new commit (never an amend), answer every finding under **Review
     responses** (fixed, or declined with the reason), and report `SPEC READY` again.
  8. On release ("Spec approved at `<sha>` …"): build it, steps 3-8 below, on this same branch. The spec body is
     frozen at release: a change after it goes only in an appended `## Deviations after release` section, one
     entry per deviation with its reason, and in the PR body. The PR's first commit is the spec. Answer the
     release message's Minor findings in the PR body. If a row needs a product decision, do the decidable part
     and report the rest.
```

- [ ] **Step 6: The other Worker contract lines**

Replace `- Own only your files (and your own entries in `parallel_shared_files`); if you need another file, stop and report.` with:

```
- Own only your files (and your own entries in `parallel_shared_files`, and your batch's spec file); if you need
  another file, stop and report (in the spec phase: a scope request in the outline). A message "owned files now
  also include <file>" adds that file.
```

Replace `- Remote work: the `git`+`gh` column of the merge-tool mapping; open the PR ready for review.` with:

```
- Remote work: the `git`+`gh` column of the merge-tool mapping; open the PR ready for review. Your branch
  already exists (the spec phase made it): skip `git switch -c`, and commit with explicit paths, or `git add -A`
  only once the spec phase is over.
```

Replace `- Never merge. Poll for new bot comments after your last push, and stop pushing once you report.` with:

```
- Never merge. Poll for new bot comments after your last push, and stop pushing once you send your final report
  (`SPEC READY` is not one).
```

Replace `- Report: PR URL, head SHA, check states on it, unresolved threads, per-row changes, evidence, files outside` with:

```
- Report: PR URL, head SHA, the spec path and its deviations after release, check states on it, unresolved
  threads, per-row changes, evidence, files outside
```

Replace ``numbers `<prefix>-NEW-*` from the real max id).`` with:

```
numbers `<prefix>-NEW-*` from the real max id). The block may also carry `"spec": "<spec path>"`, which `bk.py` ignores.
```

- [ ] **Step 7: Verify**

```bash
grep -c 'skip brainstorming' skills/foreman/SKILL.md                     # expect 0
grep -c '^### Worker contract$' skills/foreman/SKILL.md                  # expect 1
grep -c 'SPEC READY <absolute worktree path> <branch> <spec path> <sha>' skills/foreman/SKILL.md   # expect 1
grep -c 'foreman-worker: <batch> spec-phase' skills/foreman/SKILL.md     # expect 1 or 2 (sub-bullet, plus the W3 sentence if hooks added it)
grep -c '## Deviations after release' skills/foreman/SKILL.md            # expect 1
awk '/^### Worker contract/{c=1} /^## Backlog UI/{c=0} c' skills/foreman/SKILL.md | grep -c 'superpowers:brainstorming'   # expect 1
```

Each output must match its expected value.

- [ ] **Step 8: Commit**

```bash
git add skills/foreman/SKILL.md
git commit -m "foreman skill: the worker's spec phase; W2 batch names and parked rows; W3 spec-phase tag and state table"
```

---

### Task 8: SKILL.md: W3.5 spec review and its clock

**Files:**
- Modify: `skills/foreman/SKILL.md` (insert a new bullet before W4)

**Interfaces:**
- Consumes:
  - Task 1's `review.sh --spec` call and `<log>.pid`;
  - Task 5's two pre-check commands, verbatim;
  - Task 6's keys;
  - Task 7's state-table columns.
- Produces: the `**W3.5 Spec review**` bullet, its bounds table and its clock. Tasks 9–11 refer to it as W3.5, the clock and the bounds.

- [ ] **Step 1: Insert W3.5**

Insert these lines immediately before `- **W4 Each report, as it arrives** (a report is a claim; re-read everything):`. They go after Task 7's W3 sub-bullets.

```
- **W3.5 Spec review**, for each batch on its own as its `SPEC READY` arrives (other batches keep moving).
  `SPEC READY` is the first line of the worker's last message that starts with `SPEC READY ` (a preface line
  before it is fine): `SPEC READY <worktree> <branch> <spec path> <sha>`. Read it from the right: the SHA is the
  last word; the spec path starts with `{{design_spec_dir}}/`; the branch is the word before it; the worktree is
  everything before the branch. At the batch's first `SPEC READY`, record its **fork base**:
  `git -C <worktree> merge-base <sha> origin/<default>` (`<default>` is `origin/HEAD`'s branch, else `main`).
  1. **Pre-checks.** They spend no round, but bounces are capped at 2: a third parks the batch ("spec incomplete
     after 2 bounces"). A bounce sends the problems in one `SendMessage` and returns the worker to work.
     - The worktree and the commit exist (`git -C <worktree> cat-file -e <sha>^{commit}`).
     - The spec has every template section and no `⟦FILL` marker. Run both commands in the worktree; any output
       is a bounce:
       `grep -n '⟦FILL' <spec path>`
       `grep '^## ' ${CLAUDE_PLUGIN_ROOT}/templates/spec.md | while IFS= read -r h; do grep -qxF "$h" <spec path> || echo "missing section: $h"; done`
     - `git -C <worktree> diff --name-only <fork base> <sha>` lists only paths under `{{design_spec_dir}}`.
     - Every `Scope request:` line in the Implementation outline is answered. When no other batch owns the file,
       extend the batch's owned files and say so in your next message ("owned files now also include <file>").
       Otherwise re-batch, or park ("scope conflict").
  2. **A round is pinned to one SHA.** Don't resume the worker while its round runs, and discard any result for
     an older SHA. Round r:
     a. **The cross-model chain**, in the background (Bash `run_in_background`). Write `$FOREMAN`, `<scratch>`
        and every other path out literally and absolute, with nothing else in the command (hooks piece 8
        auto-allows exactly this shape; anything more prompts, and a background run stalls on the prompt):
        `(cd <worktree> && $FOREMAN/review.sh --spec <spec path> <fork base> <scratch>/<batch>-spec-r<r>.log)`
        The base is always the fork base, so every round reviews the whole current spec. While the chain runs,
        `<log>.pid` holds its pid.
     b. **When the chain exits** (landed or not), dispatch the **read-only reviewer** in the background: `Agent`
        with `subagent_type: Explore` and `model: opus`. Give it the spec path, the rows, the `{{rules_glob}}`
        files, the worktree path, the chain's log when one landed (exit 0; `<log>.reviewer` names it), and round
        r-1's Critical and Important findings with the worker's responses. Its job, in this order:
        1. Before reading the spec's list, derive its own list of symbols, columns, statuses and writers from
           Rows and Change, `git grep` them, and diff its list against "Callers and writers checked".
        2. Verify the spec's other claims against the code at each cited `path:line`.
        3. Check the outline against the owned files, the tests against the change, and the open questions.
        4. Rule on each chain finding (confirmed, or rejected with evidence) and on each response to a previous
           finding (resolved or not).
        5. Return ONE merged verdict: Critical, Important and Minor findings, each with section, evidence and
           fix, then `Verdict: pass | revise`.
     c. **When the chain is exhausted** (exit 1; exit 2, a hard error; or killed at a session cap), dispatch two
        read-only reviewers in parallel with (b)'s prompt and no chain log: the opus one, and a second `Explore`
        with `model: sonnet`. Combine their verdicts into one findings list, duplicates removed by section and
        evidence; that list goes to the worker and is what round r+1 rules on. Mark the batch "no cross-model
        review" in the run report: the Agent tool offers only Claude models.
  3. **Verdict.**
     - **Pass:** no Critical or Important finding in the merged verdict (case c: in either verdict). Before
       passing, grep-verify the evidence for every chain P1 or P2 the reviewer rejected; a rejection you can't
       confirm turns the pass into a revise. Status `passed`, then W3.6.
     - **Revise:** spot-check the findings (the reviewer already verified them), then send them in ONE
       `SendMessage`, with the Minor ones marked optional. The next `SPEC READY` starts round r+1.
     - **Park** (W3.6): a bound below is exceeded, or an open question the batch depends on needs a human
       decision (reason `open question`). Open questions outside the batch's scope don't block a pass: they
       become row notes in the final report (do the decidable part).
  4. **Reviewer failures.** A read-only reviewer that fails, or isn't back in 20 minutes, is re-dispatched once;
     stop the superseded one (TaskStop) and ignore any late verdict from it. A round that still produces no
     verdict doesn't count, and a second such round parks the batch ("review could not run"). At the subagent
     concurrency limit ("Concurrent subagent limit reached"), queue the dispatch for the next wake: that is not
     a failure.
  5. **A stop without `SPEC READY`** (a question, a context limit, an error): resume the worker once with "finish
     the spec; questions go under Open questions; end with `SPEC READY` as the first line". It counts as a
     bounce. A second such stop parks the batch ("spec incomplete").
  6. **Bounds.** A batch in the spec phase ends `released` or `parked`, never anything else.

     | bound | default | when exceeded |
     |---|---|---|
     | `spec_review_rounds` | 3 | rounds without a pass: park ("no pass in <n> rounds") |
     | `spec_phase_timeout` | 60 minutes of worker time (the clock) | park ("spec phase timed out") |
     | one round | the chain: its adapters' own timeouts (exit 2 counts as exhausted); the read-only reviewer: 20 min | re-dispatch once, then the round doesn't count |
     | pre-check bounces | 2 | park ("spec incomplete after 2 bounces") |
     | rounds that don't count | 1 | park ("review could not run") |
     | W6 silence | `parallel_worker_timeout` | park ("worker stuck"); restarts at every findings or release message |
     | manual checkpoint | not selected | park ("not released by the human") |
     | comment round (W3.6) | one per batch, always allowed, exempt from `spec_review_rounds` and `spec_phase_timeout` | failing it parks the batch |

  7. **The clock.** Only notifications wake you, so the bounds run on timers.
     - **Timestamps**, epoch seconds from `date +%s`, in the state table. Every message that returns the worker
       to work stamps `sent_i`: launch (`sent_0`), findings, a bounce, a resume after a stop without
       `SPEC READY`, a comment round. Every `SPEC READY` stamps `spec_ready_i`; a stop without one stamps
       `stopped_i`.
     - **Worker time** is the sum of every `spec_ready_i − sent_i` (or `stopped_i − sent_i`), plus
       `now − last sent_i` while the worker is working. Time between a stop and your resume never counts, and
       comment-round intervals are left out (their own timer is `parallel_worker_timeout`).
     - **Timers.** For every return to work, start `sleep <remaining worker time in seconds>` with Bash
       `run_in_background`; one `sleep 1200` per running reviewer; one `sleep <parallel_worker_timeout × 60>` per
       comment round. Record each timer's task id. Stop the superseded timer (TaskStop) at `SPEC READY` and on
       every re-arm.
     - **On every wake**, recompute every bound for every batch from the timestamps and act on the exceeded
       ones. Then re-arm a timer for each working batch, running reviewer and comment round whose timer has
       ended. A stray wake is harmless.
     - **Session caps.** Where background commands are capped (`-p`, SDK and cloud sessions: 30 minutes by
       default, 2 hours at most), every timer is `sleep min(remaining, cap − 60)`; re-arming covers the rest.
       Never use `timeout` (macOS lacks it). A chain killed at the cap counts as exhausted.
  8. **Reviewer model.** Before the first dispatch, check whether the project or the user defines its own
     `Explore` agent (`grep -l '^name: Explore' .claude/agents/*.md ~/.claude/agents/*.md`). One that does keeps
     its own `model:`; record that model, not `opus`, as the reviewer in the run report.
```

- [ ] **Step 2: Verify**

```bash
grep -c '^- \*\*W3.5 Spec review\*\*' skills/foreman/SKILL.md                       # expect 1
grep -cF "grep -n '⟦FILL' <spec path>" skills/foreman/SKILL.md                     # expect 1
grep -cF 'while IFS= read -r h; do grep -qxF "$h" <spec path> || echo "missing section: $h"; done' skills/foreman/SKILL.md   # expect 1
grep -cF '$FOREMAN/review.sh --spec <spec path> <fork base>' skills/foreman/SKILL.md   # expect 1
awk '/\*\*W3.5 Spec review\*\*/{a=NR} /^- \*\*W4 Each report/{b=NR} END{exit !(a && b && a<b)}' skills/foreman/SKILL.md && echo ORDER-OK
```

Expected: `1` four times, then `ORDER-OK`.

- [ ] **Step 3: Commit**

```bash
git add skills/foreman/SKILL.md
git commit -m "foreman skill: W3.5 spec review rounds, bounds and timer clock"
```

---

### Task 9: SKILL.md: W3.6 release and parking, and W1

**Files:**
- Modify: `skills/foreman/SKILL.md` (insert W3.6 and Parking before W4; edit W1)

**Interfaces:**
- Consumes:
  - hooks CLIs: `hooks.py release <batch> <sha>` (exit 0/1/3) and `hooks.py stopping <batch>`;
  - Task 1's `<log>.pid`;
  - Task 8's bounds and clock.
- Produces:
  - **The `**W3.6 Release.**` bullet**, with the release message text "Spec approved at `<sha>` (round <r>) …".
  - **The Parking procedure**, with reasons from the bounds table. It copies specs to `$(git rev-parse --git-common-dir)/foreman/parked/<batch>/`, which W5 (Task 11) reads.
  - **W1's parking exception.**

- [ ] **Step 1: Insert W3.6 and Parking**

Insert immediately before `- **W4 Each report, as it arrives** (a report is a claim; re-read everything):`. They go after W3.5.

```
- **W3.6 Release.**
  - **Releasing** a batch takes two steps:
    1. `python3 $FOREMAN/hooks.py release <batch> <sha>`. The hooks record it, bound to this session.
       - Exit 0: recorded; send the message.
       - Exit 3 ("hooks inactive": no run marker in either state dir): send the message; nothing gates the worker.
       - Exit 1 (not recorded): don't send; retry once. If the retry also exits 1, park the batch ("release could
         not be recorded"), unless `spec_gate` is in the config's `hooks_disabled`: then send the message,
         because nothing gates the worker. A released worker that the spec gate still denies would stall.
    2. `SendMessage`: "Spec approved at `<sha>` (round <r>). Minor findings to answer in the PR body: <list, or
       none>. Implement it: Worker contract steps 3-8, on this same branch." Stamp the silence clock; status
       `released`.
  - **`--auto-merge`:** release each batch as soon as it passes.
  - **Manual mode:** hold passed batches until every batch of the wave is `passed` or `parked` (the bounds
    guarantee that it happens). Then send a notification ("foreman: spec checkpoint ready, N passed, M parked"),
    with the session's push-notification tool if it has one, else a plain message. Present ONE table: batch,
    rows, spec path, rounds, each reviewer's final verdict, the remaining Minor findings, open questions. Ask
    which batches to release, as a multi-select question; with more than 4 batches, split it into several
    questions or let the human answer in text. The human may comment on other batches, in the question's notes
    or in a reply.
    - **Selected:** released.
    - **Commented:** one comment round, with the comments sent as findings. It's always allowed, is exempt from
      `spec_review_rounds` and `spec_phase_timeout`, and its timer is `parallel_worker_timeout`.
      Batches that pass it come back together at ONE follow-up checkpoint, which offers only release or park.
      A batch that fails it is parked.
    - **Neither:** parked ("not released by the human").
  - **Parking** (a bound, an open question, the checkpoint, W6's stuck spec-phase worker):
    1. Kill the round's chain and reviewer if they're running. Read the pid in `<log>.pid` and run
       `kill -TERM <pid>`: review.sh then kills its running adapter's process group and exits. TaskStop the
       chain's background task and the reviewer. Run `python3 $FOREMAN/hooks.py stopping <batch>`; exit 3
       means the hooks aren't firing, which is harmless. Then stop the worker (TaskStop) and the batch's
       timers.
    2. Copy every spec in the worktree to `$(git rev-parse --git-common-dir)/foreman/parked/<batch>/`, which
       outlives the session's scratch. Copy committed and uncommitted ones alike, including a park before the
       first `SPEC READY`, when the state table has no spec path yet. The files are the union of:
       - `git -C <worktree> status --porcelain --untracked-files=all -- {{design_spec_dir}}`;
       - `git -C <worktree> diff --name-only <fork base> HEAD -- {{design_spec_dir}}`. With no fork base yet,
         use `$(git -C <worktree> merge-base origin/<default> HEAD)`.

       W5 commits these copies, or writes `no spec` when there are none.
    3. Name the branch and its SHA in the run report.
    4. `git worktree remove --force <worktree>`. This is the one exception to W1's no-`--force` rule: the spec is
       copied and nothing else is pending. The local branch keeps the commit until W5.

    The rows stay `ready`; the status becomes `parked: <reason>`.
```

- [ ] **Step 2: W1**

Replace `never touch the worktree of a worker that has not reported), then` with:

```
never touch the worktree of a worker that has not reported; `SPEC READY` is not a report), then
```

Then replace `  `git worktree prune`.` (the end of W1) with:

```
  `git worktree prune`. A parked batch's worktree is removed at parking (W3.6, the one `--force` exception);
  its branch is deleted after W5 commits its spec.
```

- [ ] **Step 3: Verify**

```bash
grep -c '^- \*\*W3.6 Release.\*\*' skills/foreman/SKILL.md                    # expect 1
grep -cF 'python3 $FOREMAN/hooks.py release <batch> <sha>' skills/foreman/SKILL.md   # expect 1
grep -cF 'Spec approved at `<sha>` (round <r>)' skills/foreman/SKILL.md          # expect 1
grep -cF 'foreman/parked/<batch>/' skills/foreman/SKILL.md                       # expect 1
awk '/\*\*W3.5 Spec review\*\*/{a=NR} /\*\*W3.6 Release.\*\*/{b=NR} /^- \*\*W4 Each report/{c=NR} END{exit !(a<b && b<c)}' skills/foreman/SKILL.md && echo ORDER-OK
```

Expected: `1` four times, then `ORDER-OK`.

- [ ] **Step 4: Commit**

```bash
git add skills/foreman/SKILL.md
git commit -m "foreman skill: W3.6 release, manual checkpoint, comment round and parking; W1 parking exception"
```

---

### Task 10: SKILL.md: W4 additions (routing, W4.2, W4.2b, W4.4, W4.5, W4.6)

**Files:**
- Modify: `skills/foreman/SKILL.md` (the W4 bullet)

**Interfaces:**
- Consumes:
  - Task 7's `## Deviations after release` rule;
  - Task 9's release status and released SHA;
  - Task 8's reviewer timer (20 minutes) and Reviewer-model note.
- Produces:
  - **The `2b. **Spec reviewer**` item**, whose verdict gates the merge. Task 12's Non-negotiables clause refers to "a W4.2b verdict".
  - **The W4.4 replacement rule**: the plain tag `foreman-worker: <batch>`, `git switch <local branch>`, and `hooks.py stopping` before TaskStop.

- [ ] **Step 1: Routing**

Replace `- **W4 Each report, as it arrives** (a report is a claim; re-read everything):` with:

```
- **W4 Each report, as it arrives** (a report is a claim; re-read everything; a `SPEC READY` goes to W3.5, never here):
```

- [ ] **Step 2: W4.2 spec checks and the new W4.2b**

Replace the line `     only owned files plus what the report lists (tests, `parallel_shared_files`), no unexplained deletion.` with:

```
     only owned files plus what the report lists (tests, `parallel_shared_files`), no unexplained deletion.
     For a spec-phase batch, also check these:
     - The spec file, up to its `## Deviations after release` heading, is byte-identical to the released SHA's
       copy. This prints nothing when it is:
       `cmp <(git show <released sha>:<spec path> | perl -0pe 's/\s+\z/\n/') <(git show <head>:<spec path> | perl -0pe 's/\n## Deviations after release\n.*//s; s/\s+\z/\n/')`
     - Every touched file is in the spec's Implementation outline, or is a test file its Tests section names, or
       is listed in the report as changed by the gate (formatters), or is covered by a deviation. The batch's
       spec file is allowed.
  2b. **Spec reviewer** (spec-phase batches). Dispatch a read-only reviewer in the background (`Agent`,
     `subagent_type: Explore`, `model: opus`; W3.5's reviewer-model note applies) on the SHA W4.1 verified,
     with a 20-minute reviewer timer (W3.5's clock). Give it the spec, the PR diff and the deviations section.
     It does four things:
     - checks the code against the spec;
     - reports deviations the spec doesn't list;
     - rules on each listed deviation: acceptable, or a finding;
     - confirms that each file the report lists as gate-changed has formatting-only hunks and is owned by no
       other batch. Anything else is an unlisted deviation.

     It returns Critical, Important and Minor findings, plus a ruling per deviation.
     - **Part of the auto-merge gate.** A Critical or Important finding, or a deviation ruled unacceptable,
       blocks the merge like any review finding, and goes to the worker as a W4.4 finding. In `--auto-merge`
       its rulings stand. In manual mode the human sees them at W4.5.
     - **If it fails or times out,** re-dispatch it once. If it fails again, don't auto-merge that PR: leave it
       open with a note, carry on with the wave, and list it in the run report for the human.
     - **If the head moves,** run it again on the new SHA. W4.6 merges only a SHA whose W4.2b verdict is clean.
     - **A deviation that adds a file outside the outline, or changes a row's acceptance,** gets one spec round
       before merge. It runs only after W4.2b has ruled, and only when W4.2b didn't rule it unacceptable (an
       unacceptable one goes straight to W4.4).
       - The round skips W3.5's pre-checks and runs only W3.5's read-only reviewer, with no chain: the chain's
         prompt reviews an unbuilt change.
       - The reviewer reviews the spec's deviations section against
         `git diff $(git merge-base origin/<default> <head>) <head>`, minus the spec file. That leaves out main's
         changes merged in at contract step 8.
       - A failure is a W4.4 finding sent to the worker. It never parks a built PR.
```

- [ ] **Step 3: W4.4 replacement worker**

Replace `     new worker on the branch), then W4.1 again.` with:

```
     new worker on the branch), then W4.1 again. A replacement for a spec-phase batch: run
     `python3 $FOREMAN/hooks.py stopping <batch>` before any TaskStop of the old worker. The new worker's prompt
     begins `foreman-worker: <batch>`, without ` spec-phase` because the spec is already released, and names the
     spec path. Its first step is `git switch <local branch>`: a batch that never pushed has its branch only
     locally.
```

- [ ] **Step 4: W4.5 and W4.6**

Replace `present the ready PRs with their evidence and head SHAs, and stop;` with:

```
present the ready PRs with their evidence, head SHAs and, for spec-phase batches, their deviations with W4.2b's rulings, and stop;
```

Replace `  6. **Merge.** Reply to and resolve every thread` with:

```
  6. **Merge.** Spec-phase batches merge only at a SHA with a clean W4.2b verdict. Reply to and resolve every thread
```

- [ ] **Step 5: Verify**

```bash
grep -cF 'a `SPEC READY` goes to W3.5, never here' skills/foreman/SKILL.md     # expect 1
grep -c '^  2b\. \*\*Spec reviewer\*\*' skills/foreman/SKILL.md                 # expect 1
grep -cF 'begins `foreman-worker: <batch>`, without ` spec-phase`' skills/foreman/SKILL.md   # expect 1
grep -cF 'with a clean W4.2b verdict' skills/foreman/SKILL.md                    # expect 1
```

Each must print `1`.

- [ ] **Step 6: Commit**

```bash
git add skills/foreman/SKILL.md
git commit -m "foreman skill: W4 spec checks, W4.2b spec reviewer, deviation rounds, replacement worker"
```

---

### Task 11: SKILL.md: W5 and W6 (parked bookkeeping, forget, stuck spec workers, run report)

**Files:**
- Modify: `skills/foreman/SKILL.md` (append to W5 and W6)
- Test: `tests/run.sh` (a `bk.py` block)

**Interfaces:**
- Consumes:
  - Task 9's `foreman/parked/<batch>/` copies and park reasons;
  - `bk.py`'s report format;
  - hooks `forget <batch>` and `stopping <batch>`.
- Produces:
  - **Parked-row notes** in the form `spec parked (<reason>): <spec path> — <open findings or question>`, which Task 7's W2 rule reads.
  - **The run report's spec-phase table.**

- [ ] **Step 1: Write the bk.py test (it pins W5's report format against bk.py)**

Insert immediately before `echo "## backlog.py + ui.py"`:

```bash
echo "## bk.py: a parked batch's append-only report"
echo '{"pr": 0, "spec": "docs/superpowers/specs/2026-10-02-FM-2-design.md", "append": [{"id": "FM-2", "note": "spec parked (open question): docs/superpowers/specs/2026-10-02-FM-2-design.md — which role owns the setting?"}]}' >"$T/park.json"
python3 "$root/scripts/bk.py" "$T/bk" "$T/park.json" >"$T/park.out" 2>&1
check "bk.py accepts a pr: 0 append-only report (and ignores \"spec\")" test $? = 0
check "...the note lands on FM-2, which stays ready" grep -qF '| FM-2 | second | low | ready | also spec parked (open question): docs/superpowers/specs/2026-10-02-FM-2-design.md — which role owns the setting? | me |' "$T/bk/.claude/backlog.md"
echo '{"pr": 0, "append": [{"id": "FM-2", "note": "spec parked (open question): see FM-NEW-a"}]}' >"$T/park-bad.json"
python3 "$root/scripts/bk.py" "$T/bk" "$T/park-bad.json" >/dev/null 2>&1
check "bk.py refuses a parked note that quotes a placeholder id" test $? = 1
```

- [ ] **Step 2: Run it**

Run: `tests/run.sh 2>&1 | grep -E '^(FAIL|passed)'`

Expected: `passed N, failed 0`. `bk.py` already accepts `pr: 0`, so this test pins existing behaviour that W5 now relies on. If it fails, STOP: W5's format would need changing, not `bk.py`.

- [ ] **Step 3: W5**

Insert after the line `  docs nits (a Critical/Important finding still blocks).`, which ends W5. If `task0.md` shows a `hooks.py forget` hit in W5 already, omit the second sub-bullet ("Hooks state").

```
  - **Parked batches.** Write one report per parked batch,
    `{"pr": 0, "append": [{"id": <row>, "note": "spec parked (<reason>): <spec path> — <open findings or question>"}]}`,
    and feed it to `bookkeeping_tool` with the others. `<reason>` is the park reason (`open question` for that
    case), and `<spec path>` is `no spec` when nothing was copied. Notes are one line, with any ` | ` escaped as
    `\|`, and never quote a placeholder id like `<prefix>-NEW-a`: bk.py refuses one with no new row. The
    bookkeeping PR also commits each copied spec from `foreman/parked/<batch>/` into `{{design_spec_dir}}`, with
    a `Status: parked — <reason>` line under its title. Then delete the batch's local branch and
    `foreman/parked/<batch>/`.
  - **Hooks state.** Run `python3 $FOREMAN/hooks.py forget <batch>` for every batch of the wave (exit 3 means the
    hooks are inactive, which is harmless).
```

- [ ] **Step 4: W6**

Replace `the next pool, report it, and carry on.` with:

```
the next pool, report it, and carry on. Before any TaskStop of a tagged worker, run
  `python3 $FOREMAN/hooks.py stopping <batch>`. A stuck spec-phase worker is parked instead (W3.6, "worker
  stuck"). A worker waiting after `SPEC READY`, or awaiting release, isn't silent: its clock restarts at every
  findings or release message. Rows parked during this invocation stay out of later waves' pools (W2's
  parked-row rule covers later invocations).
  - **Run report.** At the stop, the report includes a spec-phase table, one row per batch: spec wall-clock,
    rounds, the reviewer that landed (or the fall-through, or "no cross-model review"), the reviewer model, park
    and reason, and the review rounds after the PR opened. The last column is the measure: follow-up rounds per
    PR after review.
```

- [ ] **Step 5: Verify**

```bash
grep -cF 'spec parked (<reason>): <spec path>' skills/foreman/SKILL.md          # expect 1
grep -cF 'python3 $FOREMAN/hooks.py forget <batch>' skills/foreman/SKILL.md      # expect 1
grep -cF 'A stuck spec-phase worker is parked instead' skills/foreman/SKILL.md   # expect 1
grep -cF 'Status: parked — <reason>' skills/foreman/SKILL.md                     # expect 1
```

Each must print `1`.

- [ ] **Step 6: Commit**

```bash
git add skills/foreman/SKILL.md tests/run.sh
git commit -m "foreman skill: W5 parked-batch bookkeeping and forget; W6 parks stuck spec workers, run-report spec table"
```

---

### Task 12: SKILL.md Non-negotiables and Step 0.5; README; version 0.5.0

**Files:**
- Modify: `skills/foreman/SKILL.md`, `README.md`, `.claude-plugin/plugin.json`

**Interfaces:**
- Consumes:
  - Task 6's keys;
  - Task 10's W4.2b;
  - Tasks 1–4's script and adapter behaviour, for the README.
- Produces: the Non-negotiables rule text `no code before the batch's spec is released`, which Task 13 pins, and version `0.5.0`.

- [ ] **Step 1: Non-negotiables**

Replace ``zero-surface skip), and `risk != high`.`` with:

```
zero-surface skip), in parallel mode a W4.2b verdict on the merge SHA with no Critical or Important finding and no deviation ruled unacceptable, and `risk != high`.
```

Then insert this new bullet immediately before the line `- **Behavior-preserving work changes ZERO behavior**`:

```
- **Parallel mode: no code before the batch's spec is released** (W3.5, W3.6). The hooks' spec gate (piece 10) enforces it; without the hooks, W3.5's diff pre-check is the only guard.
```

- [ ] **Step 2: Step 0.5 table**

If `task0.md` shows a `design_spec_dir` row in the table already, replace that row with the first row below.

Otherwise insert these three rows directly after the row that starts ``| `shared_resources` |``:

```
| `design_spec_dir` | where parallel-mode batch specs are committed (W3.5); not `e2e.spec_dir` | `docs/superpowers/specs` |
| `spec_review_rounds` | spec review rounds without a pass before a batch is parked (W3.5) | 3 |
| `spec_phase_timeout` | minutes of worker time in the spec phase before a batch is parked (W3.5's clock) | 60 |
```

- [ ] **Step 3: README**

Replace ``\`parallel N\` runs N workers in separate git worktrees while one orchestrator owns merging.`` with:

```
`parallel N` runs N workers in separate git worktrees while one orchestrator owns merging. Each parallel batch
first writes a design spec that is reviewed until solid (and, in manual mode, approved by you at one checkpoint
per wave) before any code.
```

Replace ``| `/foreman parallel 4 [--auto-merge]` | up to 4 worktree workers per wave |`` with:

```
| `/foreman parallel 4 [--auto-merge]` | up to 4 worktree workers per wave; each batch's spec is reviewed before code (see **Spec phase**) |
```

Insert immediately before `## Backlog UI`:

```markdown
## Spec phase (parallel mode)

In `parallel N`, every batch starts with a spec, not code:
- **The spec.** The worker explores the code, writes `<design_spec_dir>/<date>-<rows>-design.md` from
  `templates/spec.md`, and commits it as the branch's first commit.
- **The review.** The orchestrator reviews it in rounds: the reviewer chain in spec mode
  (`review.sh --spec`), plus a read-only Claude reviewer that derives its own list of callers and writers
  before it reads the spec's list. A round with no Critical or Important finding passes.
- **The release.** `--auto-merge` releases a passed spec at once. A manual run holds passed specs for one
  checkpoint per wave and notifies you when it is ready.
- **Parking.** A batch that can't pass within `spec_review_rounds` rounds, or within `spec_phase_timeout`
  minutes of worker time, is parked. Its spec is committed with a `Status: parked` line, and its rows get a
  note.

With the hooks enabled (0.4.0), a worker can't edit or commit code before its spec is released. The spec stays
frozen after release; any change goes in a `## Deviations after release` section, which a reviewer rules on
before merge.

```

Replace ``\`pre_browser_check\`, \`worktree_env_files\`, \`worktree_install\`, \`stale_check\`, \`gate_slots\`, \`parallel_*\`,`` with:

```
`pre_browser_check`, `worktree_env_files`, `worktree_install`, `stale_check`, `gate_slots`, `parallel_*`,
`design_spec_dir`, `spec_review_rounds`, `spec_phase_timeout`,
```

Replace `diff. No adapter runs with a bypass, yolo or skip-permissions flag.` (the paragraph after the adapter table) with:

```
diff. No adapter runs with a bypass, yolo or skip-permissions flag.

The spec phase runs `scripts/review.sh --spec <spec path> <fork base> <log>`. It uses
`templates/spec-review-prompt.md`, and only adapters that read the repo themselves (marked
`# foreman: reads-repo`): codex (`codex exec`), claude, opencode (its `plan` agent), and `custom` when its
command uses both `{prompt_file}` and `{base}`. Every other adapter falls through. If no adapter is left, the
orchestrator runs two Claude reviewers instead. On TERM, review.sh kills the running adapter's process group and
exits 143. `<log>.pid` holds its pid while it runs.
```

Replace the codex row with:

```
| `codex` | review: `codex -m M -c model_reasoning_effort=E --sandbox read-only review --base B`; spec: `… --sandbox read-only exec --ephemeral --color never -o <log> -` (prompt on stdin) | sandbox | verified (CLI 0.160.0, gpt-6.1-sol) |
```

Replace the custom row with:

```
| `custom` | the legacy `extra_reviewers` command, run with `bash -c`; `{prompt_file}` and `{base}` are substituted (shell-quoted) | whatever the command does | — |
```

Replace `nothing until it finishes.` (the end of "Writing an adapter") with:

```
nothing until it finishes, and `# foreman: reads-repo` if the reviewer reads the repo itself: only those run in
spec mode (`FOREMAN_REVIEW_MODE=spec`; `_lib.sh` makes the others fall through).
```

Replace ``| `scripts/review.sh <base> <log>` | the reviewer chain |`` with:

```
| `scripts/review.sh [--spec <spec path>] <base> <log>` | the reviewer chain; `--spec` reviews a batch spec (exit 0 landed, 1 exhausted, 2 hard error, 143 terminated) |
```

Replace the line `tests/run.sh                       # offline: config parsing, bk.py, worktree setup, gate locks, the chain with fake reviewers` with:

```
tests/run.sh                       # offline: config parsing, bk.py, worktree setup, gate locks, the chain with fake reviewers (incl. spec mode), the spec template, SKILL.md spec-phase text
SPEC=docs/superpowers/specs/x-design.md tests/real-reviewers.sh <fork base> /tmp/rrs codex=gpt-6.1-sol:medium   # a real spec-mode run
```

- [ ] **Step 4: Version**

In `.claude-plugin/plugin.json`, replace `"version": "0.4.0"` with `"version": "0.5.0"`.

- [ ] **Step 5: Verify**

```bash
grep -cF "Parallel mode: no code before the batch's spec is released" skills/foreman/SKILL.md   # expect 1
grep -cF 'in parallel mode a W4.2b verdict on the merge SHA' skills/foreman/SKILL.md              # expect 1
grep -c '^| `spec_review_rounds` |' skills/foreman/SKILL.md                                        # expect 1
grep -c '^| `design_spec_dir` |' skills/foreman/SKILL.md                                           # expect 1
grep -c '^## Spec phase (parallel mode)$' README.md                                                # expect 1
python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['version'])"          # expect 0.5.0
tests/run.sh 2>&1 | tail -1                                                                         # expect passed N, failed 0
```

- [ ] **Step 6: Commit**

```bash
git add skills/foreman/SKILL.md README.md .claude-plugin/plugin.json
git commit -m "foreman 0.5.0: spec-phase non-negotiable and auto-merge condition, config table rows, README spec phase"
```

---

### Task 13: SKILL.md text-check test

**Files:**
- Test: `tests/run.sh` (new block)

**Interfaces:**
- Consumes: the exact strings that Tasks 5, 7, 8, 9, 10, 11 and 12 wrote.
- Produces: an offline guard against regressions in the spec-phase text. Its two pre-check lines are the commands Task 5's `fills` and `missing` run.

- [ ] **Step 1: Write the test**

Insert immediately before `echo "## backlog.py + ui.py"`:

````bash
echo "## SKILL.md: spec phase"
sk=$root/skills/foreman/SKILL.md
check "SKILL.md is the foreman skill (guards the negative check below)" grep -qx 'name: foreman' "$sk"
check "'skip brainstorming' is gone" bash -c "! grep -q 'skip brainstorming' '$sk'"
check "...while the Worker contract is still there" grep -qx '### Worker contract' "$sk"
while IFS= read -r s; do check "SKILL.md has: $s" grep -qF -- "$s" "$sk"; done <<'EOF'
- **W3.5 Spec review**
- **W3.6 Release.**
  2b. **Spec reviewer**
SPEC READY <absolute worktree path> <branch> <spec path> <sha>
foreman-worker: <batch> spec-phase
Parallel mode: no code before the batch's spec is released
in parallel mode a W4.2b verdict on the merge SHA
## Deviations after release
superpowers:brainstorming
python3 $FOREMAN/hooks.py release <batch> <sha>
python3 $FOREMAN/hooks.py stopping <batch>
python3 $FOREMAN/hooks.py forget <batch>
$FOREMAN/review.sh --spec <spec path> <fork base>
grep -n '⟦FILL' <spec path>
grep '^## ' ${CLAUDE_PLUGIN_ROOT}/templates/spec.md | while IFS= read -r h; do grep -qxF "$h" <spec path> || echo "missing section: $h"; done
spec parked (<reason>): <spec path>
| `design_spec_dir` |
| `spec_review_rounds` |
| `spec_phase_timeout` |
EOF
appears_before() { awk -v a="$2" -v b="$3" 'index($0, a) && !x {x=NR} index($0, b) && !y {y=NR} END {exit !(x && y && x < y)}' "$1"; }
check "W3.5 comes before W3.6" appears_before "$sk" '- **W3.5 Spec review**' '- **W3.6 Release.**'
check "W3.6 comes before W4" appears_before "$sk" '- **W3.6 Release.**' '- **W4 Each report'
check "the spec phase is inside the Worker contract" appears_before "$sk" '### Worker contract' '**Step 2 is the spec phase**'
check "...and ends before the Backlog UI section" appears_before "$sk" '**Step 2 is the spec phase**' '## Backlog UI'
check "plugin version 0.5.0" grep -qF '"version": "0.5.0"' "$root/.claude-plugin/plugin.json"
````

- [ ] **Step 2: Run it; it must pass, and must fail on a mutant**

Run: `tests/run.sh 2>&1 | grep -E '^(FAIL|passed)'`

Expected: `passed N, failed 0`.

Then prove the checks can fail:

```bash
cp skills/foreman/SKILL.md /tmp/SKILL.bak
sed -i '' 's/- \*\*W3.5 Spec review\*\*/- **W3.5 Review**/' skills/foreman/SKILL.md
tests/run.sh 2>&1 | grep -c '^FAIL'      # expect 2 or more (the 'has' check and the order check)
cp /tmp/SKILL.bak skills/foreman/SKILL.md
printf '\nskip brainstorming\n' >>skills/foreman/SKILL.md
tests/run.sh 2>&1 | grep -c "^FAIL 'skip brainstorming' is gone"   # expect 1
cp /tmp/SKILL.bak skills/foreman/SKILL.md && git diff --quiet skills/foreman/SKILL.md && echo RESTORED
```

Expected: `2` or more, then `1`, then `RESTORED`.

- [ ] **Step 3: Commit**

```bash
git add tests/run.sh
git commit -m "tests: SKILL.md spec-phase text check"
```

---

### Task 14: Real adapters, the muse probe, and the documented dry runs (controller)

**Files:**
- Modify: `tests/real-reviewers.sh` (the `SPEC=` passthrough)
- Modify, depending on outcomes: `reviewers/muse.sh`, `reviewers/opencode.sh`, `tests/run.sh`, `README.md`

The orchestrating session runs this task, not a subagent. It spends real reviewer quota, creates a private GitHub repo, and needs the human to watch two of the scenarios. Ask the user before step 1 (quota) and before step 4 (repo creation).

**Interfaces:**
- Consumes: everything from Tasks 1–13; hooks 0.4.0 enabled through the plugin.
- Produces:
  - recorded evidence for each spec Testing scenario;
  - README status cells for spec mode;
  - a switch-over checklist for vacancy-vibe.

- [ ] **Step 1: `SPEC=` passthrough in `tests/real-reviewers.sh`**

Replace the usage comment line `# Spends real quota. Usage: tests/real-reviewers.sh <base> <outdir> [adapter=model:effort ...]` with:

```bash
# Spends real quota. Usage: [SPEC=<spec path>] tests/real-reviewers.sh <base> <outdir> [adapter=model:effort ...]
# With SPEC set, every run is a spec-mode run (review.sh --spec "$SPEC"); <base> is then the fork base.
```

In both review.sh calls, replace `"$root/scripts/review.sh" "$base"` with `"$root/scripts/review.sh" ${SPEC:+--spec "$SPEC"} "$base"`. On macOS bash 3.2 that expands to two words, with a space in `$SPEC` kept. This was checked on 2026-10-02.

Run `bash -n tests/real-reviewers.sh`, then commit:

```bash
git add tests/real-reviewers.sh && git commit -m "real-reviewers.sh: SPEC= runs the chain in spec mode"
```

- [ ] **Step 2: Real spec-mode runs (quota): codex exec, claude, opencode's plan agent**

```bash
P=$(mktemp -d /tmp/fm-spec-probe.XXXXXX) && cd "$P" && /usr/bin/git init -q -b main
mkdir -p app docs/superpowers/specs
printf 'STATUSES = ("draft", "live", "closed")\n\ndef close(item):\n    item["status"] = "closed"\n    return item\n' >app/status.py
printf 'def close_all(items):\n    for item in items:\n        item["status"] = "closed"\n    return items\n' >app/bulk.py
/usr/bin/git add -A && /usr/bin/git -c user.name=t -c user.email=t@x commit -qm init && /usr/bin/git switch -qc batch
cat >docs/superpowers/specs/probe-design.md <<'EOF'
# DR-1 — design

## Rows
DR-1 "Add an archived status": `archived` is a valid status; closing an archived item is refused.
## Current behaviour
`app/status.py:4` sets `closed` unconditionally.
## Change
Add `archived` to STATUSES; `close()` refuses an archived item.
## Callers and writers checked
`item["status"]` is written only in `app/status.py:4`.
## Rules that apply
None — no rules files.
## Tests
A test that closing an archived item raises.
## Implementation outline
`app/status.py` only.
## Surface
No app surface.
## Risks
None — small change.
## Open questions
None
## Review responses
None — round 1
EOF
/usr/bin/git add -A && /usr/bin/git -c user.name=t -c user.email=t@x commit -qm spec
SPEC=docs/superpowers/specs/probe-design.md /Users/crisis/_workspace/foreman.wt/spec-phase/tests/real-reviewers.sh \
  "$(/usr/bin/git merge-base main HEAD)" "$P/out" codex=gpt-6.1-sol:medium claude=haiku:low opencode=zai-coding-plan/glm-5.3:max
grep -l 'bulk.py' "$P"/out/*.review
```

The spec deliberately misses the second writer, `app/bulk.py`.

Expected:
- `codex: exit 0 (codex gpt-6.1-sol medium)`, `claude: exit 0 (…)` and `opencode: exit 0 (…)`;
- `grep -l` lists each adapter's `.review`;
- the bogus chain ends `exit 0 (claude haiku low)`, and its attempts show the muse and prime-agent entries falling through on spec mode.

Outcomes:
- **An adapter exits 0 and its review names `bulk.py`:** in README, append `; spec mode verified <YYYY-MM-DD>` to its status cell.
- **opencode fails on `--agent plan`** (an unknown agent, or `plan` being a subagent under `--pure`; check `$P/out/opencode.review.attempts`): delete the line `[ "${FOREMAN_REVIEW_MODE:-}" = spec ] && args+=(--agent plan)` and the comment `# Spec mode runs the read-only \`plan\` agent.` from `reviewers/opencode.sh`. Change the two opencode `--agent` checks in `tests/run.sh` to:

```bash
check "opencode lands in spec mode" grep -q '^opencode' "$T/out/oc.log.reviewer"
check "opencode spec mode: no --agent (default agent; OPENCODE_PERMISSION keeps it read-only)" bash -c "! grep -qx -- --agent '$OPENCODE_ARGS'"
```

  Delete the review-mode `no --agent` check, which is now redundant. In README, change `opencode (its \`plan\` agent)` to `opencode`. Re-run step 2 for opencode.
- **An adapter fails for an account reason** (quota, auth): record it in the run notes, and leave its README cell unchanged.

- [ ] **Step 3: The muse probe (quota), then the conditional change**

```bash
cd "$P"
printf 'Do not run shell commands. Using only your file-reading and search tools: (1) quote line 1 of app/status.py exactly; (2) list every file in this repository that assigns item["status"]. Answer in two lines.\n' >"$P/muse-probe.md"
muse exec --json --approval-mode never --approval-judge off --user-input-auto-resolve --disable-write --disable-shell \
  --disable-web-tools --no-session-log --max-model-steps 20 --workspace . --prompt-file "$P/muse-probe.md" \
  --model muse-spark-1.3-contributor >"$P/muse.jsonl"
python3 - "$P/muse.jsonl" <<'PY'
import json, sys
for line in open(sys.argv[1]):
    try:
        p = (json.loads(line).get('payload') or {})
    except ValueError:
        continue
    if p.get('kind') == 'run_terminal':
        print(p.get('text'))
PY
```

The probe PASSES only when the answer quotes `STATUSES = ("draft", "live", "closed")` and names both `app/status.py` and `app/bulk.py`.

**On PASS:**
- In `reviewers/muse.sh`, after the line `# tests/run.sh fails if any of the three is dropped. Never add --yolo, --disable-approval or --disable-sandbox.`, insert:

```bash
# Spec mode: the prompt file goes in as is (no inlined diff): its read and search tools work under
# --disable-shell (probe <YYYY-MM-DD>).
# foreman: reads-repo
```

- Replace `with_diff` with `if [ "${FOREMAN_REVIEW_MODE:-}" = spec ]; then pf=$prompt; else with_diff; pf=$log.prompt; fi`.
- Replace `--prompt-file "$log.prompt")` with `--prompt-file "$pf")`.
- Add to `tests/run.sh` at the end of the Task 4 block (before its `unset CODEX_ARGS`):

```bash
rm -f "$MUSE_CALLED" "$MUSE_CALLED.args"
chain 60 30 1 muse >"$T/chain.md"; PATH=$F:$PATH sv --spec "docs/my specs/d.md" "$fork" "$T/out/mu.log"
check "muse spec mode lands without inlining a diff (reviewer_max_diff_kb=1 doesn't skip it)" grep -q '^muse' "$T/out/mu.log.reviewer"
for flag in --disable-write --disable-shell --disable-web-tools; do check "muse spec mode keeps $flag" grep -qx -- "$flag" "$MUSE_CALLED.args"; done
```

- Append `; spec mode verified <YYYY-MM-DD> (reads the repo under --disable-shell)` to the README muse status cell.

**On FAIL:**
- Change nothing in `muse.sh`. muse falls through in spec mode through `_lib.sh`.
- Append `; spec mode: falls through (its read tools did not work under --disable-shell, probe <YYYY-MM-DD>)` to the README muse status cell.

Then, either way, run:

```bash
cd /Users/crisis/_workspace/foreman.wt/spec-phase
tests/run.sh 2>&1 | tail -1          # expect passed N, failed 0
git add -A reviewers tests README.md && git commit -m "reviewers: spec-mode verification results"
rm -rf "$P"
```

- [ ] **Step 4: Dry-run repo (ask the user first: this creates a private GitHub repo)**

```bash
D=$(mktemp -d /tmp/foreman-dry.XXXXXX) && cd "$D" && /usr/bin/git init -q -b main
mkdir -p app tests .claude/rules
printf 'STATUSES = ("draft", "live", "closed")\n\ndef close(item):\n    item["status"] = "closed"\n    return item\n' >app/status.py
printf 'def close_all(items):\n    for item in items:\n        item["status"] = "closed"\n    return items\n' >app/bulk.py
printf 'def summary(items):\n    return {s: sum(1 for i in items if i["status"] == s) for s in ("draft", "live", "closed")}\n' >app/report.py
touch app/__init__.py
cat >tests/test_app.py <<'EOF'
import unittest
from app.status import close
from app.report import summary

class T(unittest.TestCase):
    def test_close(self):
        self.assertEqual(close({"status": "live"})["status"], "closed")

    def test_summary(self):
        self.assertEqual(summary([{"status": "live"}])["live"], 1)

if __name__ == "__main__":
    unittest.main()
EOF
echo "- Every status write goes through app/status.py." >.claude/rules/rules.md
cat >.claude/foreman.config.md <<'EOF'
---
backlog_path: .claude/improvement-backlog.md
id_prefix: DR
gate: python3 -m unittest discover -s tests
post_merge_check: python3 -m unittest discover -s tests
merge_tool: git
required_ci: []
review_bots: []
reviewers:
  - { use: codex, model: gpt-6.1-sol, effort: medium }
  - { use: claude, model: haiku, effort: low }
spec_review_rounds: 2
spec_phase_timeout: 60
parallel_worker_timeout: 30
---
EOF
cat >.claude/improvement-backlog.md <<'EOF'
| id | title | risk | status | acceptance | source |
|----|-------|------|--------|------------|--------|
| DR-1 | Add an `archived` status | low | ready | `archived` is a valid status; closing an archived item is refused | dry run |
| DR-2 | Count archived items in the summary | low | ready | `summary()` returns an `archived` count | dry run |
| DR-3 | Make the export better | low | ready | the export is better | dry run (deliberately ambiguous: there is no export) |
| DR-4 | Rename `close_all` to `close_many` | low | ready | every caller uses `close_many` | dry run |
EOF
/usr/bin/git add -A && /usr/bin/git -c user.name=t -c user.email=t@x commit -qm init
gh repo create foreman-dry --private --source . --push
```

Start each scenario in a fresh session in `$D`:

```bash
claude --plugin-dir /Users/crisis/_workspace/foreman.wt/spec-phase
```

Reset between scenarios:
- close and delete their PRs;
- `/usr/bin/git reset --hard origin/main`;
- re-add any rows that were marked done.

`parallel 1` is a normal serial pass, with no spec phase. A scenario that needs one batch therefore runs `parallel 2` with every other row listed in `parallel_skip`. Commit each config change before the run, because W1 needs a clean tree.

- [ ] **Step 5: Scenario A: manual mode reaching the checkpoint, plus a comment round**

Set `parallel_skip: [DR-4]` in the dry config, commit it, and run `/foreman parallel 3`. DR-1, DR-2 and DR-3 become three batches.

Expected:
- each worker reports `SPEC READY …` first, and its branch's first commit touches only `docs/superpowers/specs/`;
- W3.5 rounds run in the background;
- DR-3 parks (`open question` or `no pass in 2 rounds`);
- when every batch is passed or parked, a notification arrives ("foreman: spec checkpoint ready, N passed, M parked") with one table and a multi-select question.

At the checkpoint, select DR-1 and comment on DR-2 ("also count `draft`").

Expected:
- DR-1 is released: `~/.claude/foreman/<key>/batches/<session>/<batch>.released` exists, and the worker builds;
- DR-2 runs one comment round, then comes back at ONE follow-up checkpoint that offers only release or park.

Record the state table and the run report's spec-phase table.

- [ ] **Step 6: Scenario B: `--auto-merge` releases without a checkpoint**

Set `parallel_skip: [DR-1, DR-3]`, commit it, and run `/foreman parallel 2 --auto-merge`. DR-2 (`report.py`) and DR-4 (`bulk.py`) touch disjoint files, so they become two batches.

Expected:
- no checkpoint question;
- each batch is released as soon as it passes;
- PRs merge only at a SHA with a clean W4.2b verdict.

For each merged PR:

```bash
gh pr view <n> --json commits --jq '.commits[0].oid' | xargs -I{} /usr/bin/git show --name-only --format= {}
```

Expected: the first commit lists only the batch's `docs/superpowers/specs/…-design.md`. That is the spec's "release whose spec commit is the PR's first commit" check.

Also check that the spec file in the merged PR is byte-identical, above any `## Deviations after release`, to the released SHA's copy, using W4.2's `cmp` command.

- [ ] **Step 7: Scenario C: an ambiguous row parks at the round cap**

Set `parallel_skip: [DR-1, DR-2, DR-4]`, commit it, and run `/foreman parallel 2`. Only DR-3 is eligible.

Expected:
- after at most 2 rounds, or at once with reason `open question`, the batch is `parked: <reason>`;
- the spec was copied to `$(git rev-parse --git-common-dir)/foreman/parked/<batch>/` and the worktree removed;
- the W5 bookkeeping PR commits the spec with `Status: parked — <reason>` and adds the note `spec parked (<reason>): …` to DR-3;
- `hooks.py forget <batch>` ran.

Then run `/foreman parallel 2` again and confirm W2's rule:
- DR-3 is excluded when the reason is `open question` or `not released by the human`;
- otherwise DR-3 is picked again, and its worker starts from the parked spec.

- [ ] **Step 8: Scenario D: a worker killed mid-spec parks at the timeout**

Set `spec_phase_timeout: 3` and `parallel_skip: [DR-1, DR-3, DR-4]` in the dry config, commit it, and run `/foreman parallel 2`. Only DR-2 is eligible. While the worker is still exploring (before `SPEC READY`), stop it from the session's background-task list (`/tasks`, select the worker, stop).

Expected:
- the orchestrator either resumes it once (a bounce) or, if it can't be resumed, keeps its timer;
- within 3 minutes of worker time plus one wake, the batch is `parked: spec phase timed out`, or `parked: spec incomplete` after a second stop;
- the run report names the branch and SHA;
- W5 notes DR-2 with `no spec` or with the copied spec.

Restore `spec_phase_timeout: 60` afterwards.

- [ ] **Step 9: Reviewer-model check**

In any scenario's session, find a W3.5 reviewer's transcript (the Agent tool's completion names the agent id):

```bash
grep -rl '<agent id>' ~/.claude/projects/ 2>/dev/null | head -1 | xargs grep -o '"model":"[^"]*"' | sort | uniq -c
```

Expected: `claude-opus-…`, unless a custom `Explore` agent exists, in which case it shows that agent's model. Confirm that the run report records the same model.

- [ ] **Step 10: Hand-off notes (no code in this repo)**

Write the results of steps 2–9 into the PR description, one line per scenario with pass or fail and the evidence.

Include this checklist for vacancy-vibe's switch-over, to be done there before its first spec-phase wave:
- In `.claude/improve-next.config.md`, replace the `extra_reviewers:` block with:

```yaml
reviewers:
  - { use: codex, model: gpt-5.6-sol, effort: medium }
  - { use: opencode, model: zai-coding-plan/glm-5.3, effort: max }
```

  With today's legacy codex command in `extra_reviewers` (no placeholders), every spec round would be "no cross-model review".
- The first real use is one wave with `parallel 2`. Its run report's spec-phase table gives the measure: follow-up rounds per PR after review, against wave 16's 2 of 5.

Delete the dry-run repo once the user agrees: `gh repo delete foreman-dry --yes`.

---

## Notes for the executor

- **Pre-existing, out of scope:** `reviewers/*.sh` expand `"${args[@]}"` under `set -u`. On bash 3.2 that errors when both `model` and `effort` are empty. Every real config sets a model, and Task 4 keeps the existing pattern. Mention it in the final report; don't fix it here.
- **Not this plan's work:** the `SPEC READY` stop case in `tests/test_hooks.py` belongs to the hooks plan (Task 0 checks it exists). Piece 10's enforcement is also the hooks plan's.
- **The hooks merge's text.** If it worded something in SKILL.md differently from what this plan expects (Task 0's `task0.md`), keep the hooks' sentence and adapt only the spec-phase part. Never delete a hooks rule to make an anchor fit.
