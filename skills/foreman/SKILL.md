---
name: foreman
description: One improvement pass from the backlog — plan → PR → wait for bots + CI → address comments → browser-verify → stop for human merge. Manual by default; with `--auto-merge` (or `loop`) it self-drives the backlog, auto-merging each fully-green pass and moving to the next item (no `/loop` needed). With `parallel N` it runs N worktree-isolated workers on separate batches at once (many items per invocation) while merging stays one PR at a time. `init` scaffolds the config and backlog for a new project; `seed` drafts the first backlog rows from the codebase and an optional PRD. Project-agnostic; reads .claude/foreman.config.md.
argument-hint: "[init|seed [<prd-path>] [append]|ui|ui stop] [item-id] [tag:<x>] [loop|--auto-merge] [parallel N]"
disable-model-invocation: true
---

# Foreman

**Plugin scripts** live in `${CLAUDE_PLUGIN_ROOT}/scripts` and the reviewer adapters in
`${CLAUDE_PLUGIN_ROOT}/reviewers` (Claude Code substitutes the absolute path in this text when it loads the
skill; it is NOT set in the Bash tool's environment). Below, `$FOREMAN` means that `scripts` path: write
the resolved absolute path into every command you run and into every worker briefing, because subagents get
no substitution.

Run an end-to-end improvement pass: pick the next backlog item, plan, implement, open a draft PR,
wait for all bot review + CI, address comments, verify in the browser, then (manual mode) STOP for
human merge approval — **one invocation, one item**. With the `--auto-merge` / `loop` token it runs
**autonomously**: auto-merge each *fully-green* pass and **continue to the next backlog item in the
same invocation**, draining the backlog until it's empty or a pass can't reach a green merge (see
**Autonomous loop mode**). `/loop` is **optional** — only for *scheduled* repetition (an interval or
cron cloud agent); bare `/foreman --auto-merge` already loops on its own.

This command is **project-agnostic**. It **orchestrates** existing assets — it does not reimplement
them. It reuses the user-global superpowers process skills (`brainstorming`, `writing-plans`,
`test-driven-development`, `requesting-code-review`; each only if available, and foreman's own text is the fallback), this plugin's
`/foreman:pr-address-comments` command, `graphify`, and the auto-memory system. All project-specific bindings
(backlog path, gate command, merge tool, dev URL, review bots, reviewer chain, E2E) come from a
per-project config — see **Step 0.5**. With no config it falls back to sensible defaults; `/foreman init`
writes one.

## Arguments

This run's arguments: `$ARGUMENTS` (empty means none).

Tokens may be combined in any order (e.g. `IB-03 loop`; ids use the config's `id_prefix`, default `IB`):

- `init` — **set up a project** (see **Init**): write `.claude/foreman.config.md` and the backlog from the
  plugin's templates, filled with what can be detected. Runs alone; other tokens are ignored.
- `seed [<prd-path>] [append]` — **draft the first backlog rows** from the codebase and an optional PRD (see
  **Seed**). Runs alone; other tokens are ignored.
- `ui` — **open the backlog UI** (see **Backlog UI**); `ui stop` stops it.
- (none) — pick the topmost `status: ready` item from the configured backlog.
- `<item-id>` (e.g. `IB-07`) — run that specific item (the only way to start a `risk: high` item).
- `tag:<x>` (e.g. `tag:e2e`) — only consider rows whose title contains `[x]`. Combines with
  `--auto-merge` to drain one kind of work (`/foreman --auto-merge tag:e2e`).
- `loop` | `--auto-merge` — **autonomous mode**: auto-merge each fully-green pass and self-loop
  through the backlog until it's empty or a pass can't go green (see **Autonomous loop mode**).
  Without this token the pass STOPs for a human at merge. `/loop` is optional (only for scheduled
  repetition).
- `parallel <N>` — **parallel wave mode** (see **Parallel mode**): up to N workers at once, one batch
  each. It does not imply self-merge: alone, the orchestrator stops at W4.5 for the human to approve each
  ready PR at its head SHA; add `--auto-merge`/`loop` to let it merge fully-green PRs itself. N defaults to
  4, capped by `parallel_max`; `parallel 1` is a normal pass; with an `<item-id>` the named row runs as a
  normal pass and `parallel` is ignored.

## Non-negotiables (standing rules)

- **Never self-merge in manual mode.** A human approves every merge (step 11). In **autonomous loop
  mode** (`loop`/`--auto-merge` token) self-merge is allowed ONLY on a fully-green pass — `{{gate}}` +
  matched `{{extra_gates}}` green, `{{required_ci}}` + all `{{review_bots}}` green with **no unresolved
  Critical/Important** finding after step-8 triage, step-9 browser-verify passed (or a justified
  zero-surface skip), in parallel mode a W4.2b verdict on the merge SHA with no Critical or Important finding and no deviation ruled unacceptable, and `risk != high`. Anything short of that STOPs and notifies. Never merge on yellow.
- **Wait for ALL configured review bots + CI before addressing** (step 7).
- **Browser-verify EVERY change with an app surface, in the claude-in-chrome browser** (step 9) — the skip is a narrow, explicitly-justified exception (zero application surface only). Playwright and "the code looks right" are never substitutes for the live check.
- **Parallel mode: no code before the batch's spec is released** (W3.5, W3.6). The hooks' spec gate (piece 10) enforces it; without the hooks, W3.5's diff pre-check is the only guard.
- **Behavior-preserving work changes ZERO behavior** — the golden master staying green is the proof.
- **Honor every project-specific guardrail** declared in the config's notes / the project rules (e.g. "no external-platform mutations without go-ahead").
- Gating is executed by you in steps 7–11. Do **not** wire any Stop hook to gate (known false positives).

## Autonomous loop mode (`loop` / `--auto-merge`)

Enabled by the `loop` or `--auto-merge` argument token (e.g. `/foreman --auto-merge`). The
command **self-drives**: it runs a full pass (steps 0–12), and on a fully-green pass it **merges
itself** (step 12), then **loops back to step 1** and starts the next `status: ready` item — all in
the same invocation — until the backlog is empty or a pass can't reach the merge gate. Manual mode
(no token) is unchanged: one item, then stop at step 11.

`/loop` is **not required** — bare `/foreman --auto-merge` already drains the backlog. Reach for
`/loop` only when you want *scheduled* repetition (an interval or cron cloud agent), e.g.
`/loop 2h /foreman --auto-merge`.

What changes in autonomous mode (everything else is identical):
- **Step 1 — skip, don't stop, on `risk: high`.** A human-gated item must never auto-run, but it
  must also not stall the loop: skip every `risk: high` row and pick the next non-high `status: ready`
  item.
- **Step 11 — auto-merge gate replaces the approval ask.** Proceed to step 12 **iff ALL** of the
  auto-merge conditions in the Non-negotiables hold. If any fails, **do NOT merge** — leave the PR
  open, post exactly which condition failed, and **end the loop** (a human takes over from there).
- **Step 12 — merge, then loop.** After the merge + post-merge sync, return to **step 1** for the
  next item, in the same invocation.
- **Termination.** The loop ends when: no eligible `status: ready` item remains (report "backlog
  empty", or "no eligible rows; N waiting: …" when rows wait on a `Depends on` row that isn't done);
  a pass can't reach a green merge (stop, PR left open); or only `risk: high` items remain
  (report them so the human can run each by id). However it ends, run `python3 $FOREMAN/hooks.py run-end`
  last (see **Hooks**).

Autonomy removes only the human approval *click* — never the gates that earn it. The step-4 gate,
step-7 wait-for-all-bots+CI, step-8 grep-verify/diminishing-returns triage, and step-9
browser-verify are all still fully enforced.

## Parallel mode (`parallel N`)

The slow part of a pass is waiting (gate, CI, bots), not coding. Parallel mode overlaps the waits: up to
N **workers** run steps 2-8 on separate batches, each in its own git worktree, and ONE **orchestrator**
(this session) owns every shared resource: picking batches, merging, the browser, the local database, the
backlog, the rules files and generated files. Every non-negotiable holds for every PR. Each batch first writes a
design spec, which is reviewed until solid (W3.5) and released (W3.6) before any code. Steps are labelled
W1-W6 to keep them apart from steps 0-12; W6's stop conditions replace the autonomous loop's Termination list.

### Orchestrator

- **W1 Precondition.** Step 0. Remove worktrees whose PR merged or closed (`git worktree remove`; never
  `--force` over unpushed work, never touch the worktree of a worker that has not reported; `SPEC READY` is not a report), then
  `git worktree prune`. A parked batch's worktree is removed at parking (W3.6, the one `--force` exception);
  its branch is deleted after W5 commits its spec.
- **W2 Pick and batch.**
  - Pool: `python3 "$FOREMAN/backlog.py" ready <repo> [--tag x] --no-high --done <ids merged in this run>` prints
    the eligible rows (`status: ready`, `tag:<x>`, not `risk: high`, every `Depends on` row done; read only the rows
    you pick; backlogs get large). Pass every row merged earlier in this run to `--done`: the file shows it `ready`
    until W5's bookkeeping PR merges. Then drop rows that match `parallel_skip`, name a step-3 `Procedure`, or are
    named by an open PR (match the id as a whole word). Admit rows that seem to need a human
    decision: the spec phase parks them `open question` if they do.
  - Take ~3N. Group them by the files and symbols they touch (`git grep` the symbols and their importers)
    so no two batches share a file (except `parallel_shared_files`) or use a symbol another batch moves,
    renames or re-types. Rows that overlap share one batch (one PR, ~4 rows at most).
  - A batch that moves, renames or re-types an export is a **foundation batch**: it merges first, and
    batches that import it wait for it.
  - At most `parallel_ui_max` batches with an app surface. Print the plan (batch, rows, owned files,
    surface yes/no) before launching.
  - **Batch names** are `<YYYYMMDD-HHMM>-w<n><x>`: the invocation's start in local time (`date +%Y%m%d-%H%M`), the
    wave number and a letter (e.g. `20261002-1530-w1a`). They are unique across invocations and `/loop` ticks; the
    hooks key their state files on them and read the time to judge a fresh run marker.
  - **Parked rows** (W3.6). A row stays out of the pool, until the human edits the note away, when its note
    contains `spec parked (open question)`, `spec parked (not released by the human)`,
    `spec parked (stopped by the human)` or `spec parked (comment round failed)`, or when it has two
    `spec parked (` notes. Any other `spec parked (` row is eligible again, and its worker starts from the
    committed parked spec (Worker spec phase step 4).
- **W3 Launch.** One `Agent` call per batch in one message (general-purpose, `isolation: "worktree"`, background,
  named `fw-<batch>`, never the bare batch name: TaskStop takes that name as the worker's id).
  The prompt's FIRST line is exactly `foreman-worker: <batch> spec-phase` (the hooks brief and
  check tagged workers; see **Hooks**). A batch name must match `[A-Za-z0-9_-]+`: the tag grammar and the
  `hooks.py` CLIs reject anything else, and a name with a space silently disables the briefing and the report
  check. Then the **Worker contract** and **Worker spec phase** from this file copied in (a worker cannot read this command; keep the
  copy even though the hooks also brief it), plus the batch's rows, owned files, a unique test-data id prefix
  and the resolved absolute `$FOREMAN` path. Keep batch state on disk with `batches.py`, not in your context
  (**Batch state** below). Before launching, check that each `reviewers` entry's CLI is on PATH
  (`command -v`) and that the `permissions.allow` entries listed in the Hooks section of
  `${CLAUDE_PLUGIN_ROOT}/README.md` are in `~/.claude/settings.json`, `.claude/settings.json` or
  `.claude/settings.local.json`. A missing CLI is the only reviewer problem to tell workers about, because the
  chain already falls through on quota, credits, auth and unknown models. Missing permission entries are for the
  user, not the workers: warn the user, because a background worker that hits a permission prompt it can't show
  is denied and stalls. Also warn when one of those three settings files has a `PreToolUse` Bash hook that runs
  `rtk hook` directly (`grep -n 'rtk hook' ~/.claude/settings.json .claude/settings.json .claude/settings.local.json`
  prints it): RTK rewrites a worker's `git` to `rtk git`, which the worktree guard refuses, so the worker can't
  push. Then point the user at README's **RTK** recipe: a wrapper that leaves `git` alone in a worktree is fine.
  - **Spec phase.** Every batch starts with the spec phase (Worker contract step 2). The tag's ` spec-phase` suffix
    (W3 above) opts the batch into the hooks' spec gate (no code before release) and their spec-phase report check.
    The prompt also carries the absolute path `${CLAUDE_PLUGIN_ROOT}/templates/spec.md`, the resolved
    `{{design_spec_dir}}`, and the line "Your batch starts with the spec phase: write and commit the spec, report
    `SPEC READY`, and wait for the release." Workers launch together; each batch then moves through spec, review
    and build on its own.
  - **Batch state** is one file per batch, written only with `python3 $FOREMAN/batches.py` (the hooks deny a
    worker's call). The status band, the checkpoint and report tables and the hooks' gates read these files, so
    record each step as it happens. Every argument is one plain word; wrap a `key=value` word holding `[`, `]`,
    `(` or `)` in single quotes (`'owned=app/home/[account]/page.tsx'`): an unquoted `[…]` fails under zsh and
    `(` is shell syntax in any shell. Free text (Minor findings, open questions, a park note) goes in a scratch
    file passed to `note`. A refused call (exit 1) names the problem: fix the
    command and run it again, and never drop a state change. `batches.py show <batch>` prints the record with
    its live task ids and its remaining spec-phase time.
    - Before launching, load the deferred tools with ToolSearch `select:TaskStop,Monitor`.
    - At launch, for each batch: `set <batch> status=spec rows=<id,…> owned=<file,…>`, then
      `task <batch> add worker fw-<batch>` and `stamp <batch> sent`, then start its timer (W3.5's clock) and
      `task <batch> add timer <id>`.
    - `worktree`, `branch`, `spec`, `sha` and `fork_base` are set at the batch's first `SPEC READY`, which
      carries them, never at launch. A park before it finds the worktree with `git worktree list` (the one on
      the branch the worker created), or copies nothing.
    - TaskStop takes only an id `batches.py` recorded: a worker's `fw-<batch>`, an unnamed reviewer's agent id
      from its Agent result, a background Bash task's id; never a batch name. The hooks deny a TaskStop without
      an id, with a batch name, or for a worker before `hooks.py stopping <batch>`.
- **W3.5 Spec review**, for each batch on its own as its `SPEC READY` arrives (other batches keep moving).
  `SPEC READY` is the first line of the worker's last message that starts with `SPEC READY ` (a preface line
  before it is fine): `SPEC READY <worktree> <branch> <spec path> <sha>`. Read it from the right: the SHA is the
  last word; the spec path starts with `{{design_spec_dir}}/`; the branch is the word before it; the worktree is
  everything before the branch. At the batch's first `SPEC READY`, record its **fork base**:
  `git -C <worktree> merge-base <sha> origin/<default>` (`<default>` is `origin/HEAD`'s branch, else `main`).
  At every `SPEC READY`: TaskStop the batch's worker timer by its id and `task <batch> done <id>`,
  `stamp <batch> spec_ready`, and `set <batch> sha=<sha>`; the first one also sets
  `worktree=<worktree> branch=<branch> spec=<spec path> fork_base=<fork base>`.
  1. **Pre-checks.** They spend no round, but bounces are capped at 2: a third parks the batch ("spec incomplete
     after 2 bounces"). A bounce sends the problems in one `SendMessage` and returns the worker to work, with
     `bump <batch> bounces`, `stamp <batch> sent` and a new timer (`task <batch> add timer <id>`).
     - **Open questions are labelled.** Every bullet under `## Open questions` starts `- **Blocking:**` or
       `- **Non-blocking:**`, or the section reads `None — …`; anything else is a bounce. A question that is answered
       (by a review or a comment round) leaves `## Open questions` and is recorded under **Review responses**; if
       none are left, the section reads `None — <reason>`. Run this in the worktree; any output is a bounce:
       `awk '/^## Open questions$/{f=1;next} /^## /{f=0} f && NF && !/^- \*\*(Blocking|Non-blocking):\*\* / && !/^None — / && !/^  /' <spec path>`
     - **A Blocking question parks the batch now** (`reason=open-question`), before any round, at any
       `SPEC READY`.
     - The worktree and the commit exist (`git -C <worktree> cat-file -e <sha>^{commit}`), the worktree's HEAD
       is that commit (`git -C <worktree> rev-parse HEAD`), and `git -C <worktree> status --porcelain --
       {{design_spec_dir}}` prints nothing: the reviewers read the working tree, so it must be the pinned SHA.
     - The spec has every template section and no `⟦FILL` marker. Run both commands in the worktree; any output
       is a bounce:
       `grep -n '⟦FILL' <spec path>`
       `grep '^## ' ${CLAUDE_PLUGIN_ROOT}/templates/spec.md | while IFS= read -r h; do grep -qxF "$h" <spec path> || echo "missing section: $h"; done`
     - `git -C <worktree> diff --name-only <fork base> <sha>` lists only paths under `{{design_spec_dir}}`.
     - Every `Scope request:` line in the Implementation outline is answered. When no other batch owns the file,
       extend the batch's owned files and say so in your next message ("owned files now also include <file>").
       Otherwise re-batch, or park ("scope conflict").
  2. **A round is pinned to one SHA.** Don't resume the worker while its round runs, and discard any result for
     an older SHA. Before each round, `set <batch> status=review`.
     **Every reviewer is recorded and stopped by id.** Record the chain with `task <batch> add chain <task id>`
     (its Bash task id), and each Agent reviewer, the opus and sonnet ones too, with
     `task <batch> add reviewer <agent id>` (the agent id in its Agent result).
     Agent reviewers are dispatched **unnamed** (no `name`): a named agent is a teammate that keeps a terminal.
     When a reviewer's verdict lands: `task <batch> verdict <id>`, TaskStop it by that id, then
     `task <batch> done <id>`; a TaskStop of a task that already finished may error, which is ignored. When the
     chain exits, `task <batch> done <id>`. Round r:
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
  3. **Verdict.** Record every round: `add-round <batch> r=<r> sha=<sha> entry=<n> [fallthrough=<adapter:why,…>]
     explore=<pass|revise|none> crit=<n> imp=<n> minor=<n>`. `entry` is the number in the chain log's `.entry`
     (0 when the chain was exhausted or killed); each `why` is `error`, `quota`, `reads-repo`, `timeout` or
     `missing`, read from `<log>.attempts`; `explore=none` when no read-only verdict came back.
     - **Pass:** no Critical or Important finding in the merged verdict (case c: in either verdict). Before
       passing, grep-verify the evidence for every chain P1 or P2 the reviewer rejected; a rejection you can't
       confirm turns the pass into a revise. Then `set <batch> status=passed reviewed_sha=<sha>`, write the
       Minor findings and the open questions to scratch files (one per line), `note <batch> minors <file>` and
       `note <batch> questions <file>`, then W3.6.
     - **Revise:** spot-check the findings (the reviewer already verified them), then send them in ONE
       `SendMessage`, with the Minor ones marked optional, then `stamp <batch> sent` and a new timer. The next
       `SPEC READY` starts round r+1.
     - **Park** (W3.6): a bound below is exceeded. A verdict that rules an open question blocking parks the
       batch right after that round (`reason=open-question`). Non-blocking questions don't block a pass; they
       become row notes in the final report (do the decidable part). The label decides, not the scope: a
       Blocking question parks the batch even when it lies outside the batch's rows.
     - **Never edit a worker's spec**, not even a typo: the hooks refuse a release at any SHA but the reviewed
       one. Minor findings go in the release message.
  4. **Reviewer failures.** A read-only reviewer that fails, or isn't back in 20 minutes, is re-dispatched once;
     TaskStop the superseded one by its recorded id, `task <batch> done <id>`, record the re-dispatch with
     `task <batch> add reviewer <agent id>`, and ignore any late verdict from it. A round that still produces no
     verdict doesn't count, and a second such round parks the batch ("review could not run"). At the subagent
     concurrency limit ("Concurrent subagent limit reached"), queue the dispatch for the next wake: that is not
     a failure.
  5. **A stop without `SPEC READY`** (a question, a context limit, an error): `stamp <batch> stopped`, then resume
     the worker once with "finish the spec; questions go under Open questions; end with `SPEC READY` as the first
     line". It counts as a bounce: `python3 $FOREMAN/batches.py bump <batch> bounces`, `stamp <batch> sent` and
     a new timer. A second such stop parks the batch ("spec incomplete").
     - **Stopped by the human:** when the stop notification, or a refused resume, says the user stopped the
       agent, don't resume or relaunch it: `stamp <batch> stopped`, then park (`reason=stopped-by-human`).
       Only those two signals count; any other stop takes the rule above.
  6. **Bounds.** A batch in the spec phase ends `released` or `parked`, never anything else.

     | bound | default | when exceeded |
     |---|---|---|
     | `spec_review_rounds` | 3 | rounds without a pass: park ("no pass in <n> rounds") |
     | `spec_phase_timeout` | 60 minutes of worker time (the clock) | park ("spec phase timed out") |
     | one round | the chain: its adapters' own timeouts (exit 2 counts as exhausted); the read-only reviewer: 20 min | re-dispatch once, then the round doesn't count |
     | pre-check bounces | 2 | park ("spec incomplete after 2 bounces") |
     | a `- **Blocking:**` open question | at any `SPEC READY`, or a verdict that rules one blocking | park ("open question") |
     | a stop by the human | the stop notification, or a refused resume, names the user | park ("stopped by the human") |
     | rounds that don't count | 1 | park ("review could not run") |
     | W6 silence | `parallel_worker_timeout` | park ("worker stuck"); restarts at every findings or release message |
     | manual checkpoint | not selected | park ("not released by the human") |
     | comment round (W3.6) | one per batch, always allowed, exempt from `spec_review_rounds` and `spec_phase_timeout` | failing it parks the batch |

  7. **The clock.** Only notifications wake you, so the bounds run on timers.
     - **Stamps**, written by `batches.py stamp`. Every message that returns the worker to work is
       `stamp <batch> sent` (launch, findings, a bounce, a resume after a stop without `SPEC READY`), except a
       comment round, which is `stamp <batch> comment_sent`. Every `SPEC READY` is `stamp <batch> spec_ready`;
       a stop without one is `stamp <batch> stopped`.
     - **Worker time** is the sum of every `spec_ready` (or `stopped`) minus the `sent` before it, plus
       `now − last sent` while the worker is working. Time between a stop and your resume never counts, and
       comment-round intervals are left out (their own timer is `parallel_worker_timeout`).
       `batches.py show <batch>` computes it, and the `remaining` seconds.
     - **Timers.** Every timer is a Bash `run_in_background` `sleep`; a foreground `sleep` is refused.
       For every return to work, `sleep <remaining>`; one `sleep 1200` per running reviewer; one
       `sleep <parallel_worker_timeout × 60>` per comment round. Record each with `task <batch> add timer <id>`.
       Stop a superseded timer at `SPEC READY` and on every re-arm: TaskStop it by its id, then
       `task <batch> done <id>`.
     - **On every wake**, recompute every bound for every batch from `batches.py show` and act on the exceeded
       ones. Then re-arm a timer for each working batch, running reviewer and comment round whose timer has
       ended. A stray wake is harmless.
     - **Session caps.** Where background commands are capped (`-p`, SDK and cloud sessions: 30 minutes by
       default, 2 hours at most), every timer is `sleep min(remaining, cap − 60)`; re-arming covers the rest.
       Never use `timeout` (macOS lacks it). A chain killed at the cap counts as exhausted.
  8. **Reviewer model.** Before the first dispatch, check whether the project or the user defines its own
     `Explore` agent (`grep -l '^name: Explore' .claude/agents/*.md ~/.claude/agents/*.md`). One that does keeps
     its own `model:`; record that model, not `opus`, as the reviewer in the run report, with
     `set <batch> reviewer_model=<model>`.
- **W3.6 Release.**
  - **Releasing** a batch takes two steps, at its `reviewed_sha` only. The hooks deny `hooks.py release` at
    any other SHA, for a batch that isn't `passed`, and in a manual run until a checkpoint table written after
    the batch's latest pass is pasted; a denied release records nothing. Follow the deny text (paste the
    checkpoint table, or run a new W3.5 round at the new SHA, which counts toward `spec_review_rounds`), then
    release again.
    1. `python3 $FOREMAN/hooks.py release <batch> <sha>`. The hooks record it, bound to this session.
       - Exit 0: recorded; send the message.
       - Exit 3 ("hooks inactive": no run marker touched since the batch name's time): send the message; nothing
         gates the worker.
       - Exit 1 (not recorded): don't send; retry once. If the retry also exits 1, park the batch ("release could
         not be recorded"), unless `spec_gate` is in the config's `hooks_disabled`: then send the message,
         because nothing gates the worker. A released worker that the spec gate still denies would stall.
       - Any other exit (2: usage, e.g. an unresolved `$FOREMAN`): fix the command and run it again.
    2. `SendMessage`: "Spec approved at `<sha>` (round <r>). Minor findings to answer in the PR body: <list, or
       none>. Implement it: Worker contract steps 3-8, on this same branch." Stamp the silence clock. Right after
       the message, on every path that sends it (exit 0, exit 3, `spec_gate` disabled):
       `set <batch> status=released released_sha=<sha>`.
  - **`--auto-merge`:** release each batch as soon as it passes.
  - **Manual mode:** hold passed batches until every batch of the wave is `passed` or `parked` (the bounds
    guarantee that it happens). Then send a notification ("foreman: spec checkpoint ready, N passed, M parked"),
    with the session's push-notification tool if it has one, else a plain message.
    Run `python3 $FOREMAN/batches.py table checkpoint <wave>` (`<wave>` is a batch name minus its final letter),
    paste its whole output as text in a message of its own, make one more call (`python3 $FOREMAN/batches.py
    show`), then ask: the hooks read your text from the transcript, which holds a message only once the next
    call starts. The hooks deny every question at the checkpoint until the current table is in your text; a
    tool result doesn't count, because the human doesn't see it, and a table that is not yet in the transcript is
    denied too, never added to the question for you.
    Choose the form per question:
    - A question that covers one batch is single-select: Release, Comment, Park (Release and Park at the
      follow-up checkpoint). A multi-select question with one option is invalid.
    - Two to four batches: one multi-select question.
    - More than four: split them into questions of two to four batches each (5 → 3+2, 9 → 3+3+3), so no
      question is a one-option multi-select. More than sixteen: let the human answer in text.
    - A degraded batch (`⚠ degraded` in the table) says so in its option description:
      "cross-model review fell through to <reviewer>", or "no cross-model review" when none landed.

    The human may comment on other batches, in the question's notes or in a reply.
    - **Selected:** released.
    - **Commented:** one comment round, with the comments sent as findings, and `stamp <batch> comment_sent`
      (the batch leaves `passed` and its `reviewed_sha` is cleared, so it can't be released and the old table
      no longer matches). It's always allowed, is exempt from `spec_review_rounds` and `spec_phase_timeout`, and
      its timer is `parallel_worker_timeout`. Batches that pass it come back together at ONE follow-up
      checkpoint, which offers only release or park, after a fresh `table checkpoint` is pasted. A batch that
      fails it is parked (`reason=comment-failed`).
    - **Neither:** parked ("not released by the human").
  - **Parking** (a bound, an open question, the checkpoint, a human stop, W6's stuck worker whose batch isn't
    released yet), in this order:
    1. Kill the round's chain if it's running: `kill -TERM <pid>` with the pid in `<log>.pid`; review.sh then kills
       its running adapter's process group, removes `<log>.pid` and exits. Wait for the pid file to go with a
       `Monitor` until-loop (`until [ ! -e <log>.pid ]; do sleep 2; done`, 30 s timeout), or, without Monitor, the
       same loop as a `run_in_background` Bash call. Go on only after its exit or timeout notification; on a
       timeout, carry on and say so in the run report. A TaskStop before that would orphan the adapter.
    2. `python3 $FOREMAN/hooks.py stopping <batch>`; exit 3 means the hooks aren't firing, which is harmless.
    3. TaskStop every live task of the batch by its id (`batches.py show <batch>` lists them: the worker, the
       chain, the reviewers, the timers), then `task <batch> done <id>` for each. A TaskStop error for a task that
       already ended is ignored, and the task is still marked done.
    4. Copy the specs to `$(git rev-parse --git-common-dir)/foreman/parked/<batch>/`, which outlives the session's
       scratch: the batch's spec at the last pinned SHA, `git -C <worktree> show <sha>:<spec path>`. When there is
       no `SPEC READY` yet, or that `git show` fails, fall back to the union of committed and uncommitted specs;
       any other spec file under `{{design_spec_dir}}` in the worktree is copied by the same union:
       - `git -C <worktree> status --porcelain --untracked-files=all -- {{design_spec_dir}}`;
       - `git -C <worktree> diff --name-only <fork base> HEAD -- {{design_spec_dir}}`. With no fork base yet,
         use `$(git -C <worktree> merge-base origin/<default> HEAD)`.

       W5 commits these copies, or writes `no spec` when there are none.
    5. Name the branch and its SHA in the run report.
    6. `git worktree remove --force <worktree>`. This is the one exception to W1's no-`--force` rule: the spec is
       copied and nothing else is pending. The local branch keeps the commit until W5.
    7. Write the park note (the open findings or the question) to a scratch file, `note <batch> park_note <file>`,
       then `set <batch> status=parked reason=<reason>`. The reasons: `open-question`, `no-pass`, `timed-out`,
       `spec-incomplete`, `bounces`, `review-failed`, `worker-stuck`, `not-released`, `scope-conflict`,
       `release-unrecorded`, `stopped-by-human`, `comment-failed`. The tables and W5's note spell them out ("no
       pass in <n> rounds", "stopped by the human", …).

    The rows stay `ready`.
- **W4 Each report, as it arrives** (a report is a claim; re-read everything; a `SPEC READY` goes to W3.5, never here):
  1. **Verify on the head SHA.** The latest run of every `required_ci` check on that SHA is green (list
     check runs with pagination); every `review_bots` entry has reviewed that SHA (the config's definition,
     else a review on that SHA that is not a quota, rate-limit or trial notice); read the inline threads,
     review bodies and top-level comments posted since the report; `mergeStateStatus` is clean (re-read
     `UNKNOWN` a few times, then move on and come back). Step 7's ceiling applies. Record it:
     `set <batch> status=pr pr=<n>` at the batch's first report, and `set <batch> verified_sha=<sha>` once the
     SHA is verified.
  2. **Audit the diff** (`git diff --name-status` of the fetched head against its merge base): non-empty,
     only owned files plus what the report lists (tests, `parallel_shared_files`), no unexplained deletion.
     For a spec-phase batch, also check these:
     - The spec file, up to its `## Deviations after release` heading, is byte-identical to the released SHA's
       copy. This prints nothing when it is:
       `cmp <(git show <released sha>:<spec path> | perl -0pe 's/\s+\z/\n/') <(git show <head>:<spec path> | perl -0pe 's/\n## Deviations after release\n.*//s; s/\s+\z/\n/')`
     - Every touched file is in the spec's Implementation outline, or is a test file its Tests section names, or
       is listed in the report as changed by the gate (formatters), or is covered by a deviation. The batch's
       spec file is allowed.
  2b. **Spec reviewer** (spec-phase batches). Dispatch a read-only reviewer in the background (`Agent`,
     `subagent_type: Explore`, `model: opus`; W3.5's reviewer-model note applies; W3.5 step 2's rule:
     unnamed, recorded with `task <batch> add reviewer <agent id>`, stopped by its id when its verdict lands)
     on the SHA W4.1 verified,
     with a 20-minute reviewer timer (W3.5's clock). Give it the spec, the PR diff, the deviations section, the
     report's list of gate-changed files, and every other batch's owned files (`owned` in `batches.py show --json`).
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
     - **An environment failure** (`fork failed`, `Device not configured`) gets one more try first, after
       TaskStopping every live `reviewer` task of the run that has a `verdict` mark (`python3 $FOREMAN/batches.py show --json`). This retry
       is not the one re-dispatch above, and it never stops a `worker` task.
     - **If the head moves,** run it again on the new SHA. W4.6 merges only a SHA whose W4.2b verdict is clean.
     - **A deviation that adds a file outside the outline, or changes a row's acceptance,** gets one spec round
       before merge. It runs only after W4.2b has ruled, and only when W4.2b didn't rule it unacceptable (an
       unacceptable one goes straight to W4.4).
       - The round skips W3.5's pre-checks and runs only W3.5's read-only reviewer (unnamed, recorded and stopped
         by its id), with no chain: the chain's prompt reviews an unbuilt change.
       - The reviewer reviews the spec's deviations section against
         `git diff $(git merge-base origin/<default> <head>) <head>`, minus the spec file. That leaves out main's
         changes merged in at contract step 8.
       - A failure is a W4.4 finding sent to the worker. It never parks a built PR.
  3. **Surface.** Decide it yourself from the diff, using step 9's definition. If there is one, run step 9
     serially on the PR head merged onto current main (`git fetch origin +pull/<n>/head:pr-<n>`, a throwaway
     branch off main with that merge; the running dev server serves whatever is checked out, and a server you
     did not start is not yours to stop), then return to main and delete both branches. Run `pre_browser_check`
     first (a stale install makes every browser finding suspect), and when the worker delivered a check script run
     it on main (the control must differ) and then on the merge. The merge is blocked until the check passes. If a path cannot be driven (a control stays disabled, an action could reach a
     real external platform), leave the PR open, say what was and was not verified, and ask the human.
  4. **Findings** (a real Critical/Important finding, a red or missing check, a failed audit or browser
     check) go back to the worker (`SendMessage`, and `bump <batch> pr_rounds` for each send; if it cannot be
     resumed, run `python3 $FOREMAN/hooks.py stopping <batch>` before any TaskStop of it by its recorded id,
     then `python3 $FOREMAN/batches.py task <batch> done <old id>`, remove its worktree, and launch a new worker on the branch, named `fw-<batch>-2` (then `-3`, …) and recorded
     with `task <batch> add worker <name>`, whose prompt again starts `foreman-worker: <batch>`), then W4.1 again.
     For a spec-phase batch that prompt
     begins `foreman-worker: <batch>`, without ` spec-phase` because the spec is already released, names the
     spec path and the release message's Minor findings, and carries W3's copies of the Worker contract and
     Worker spec phase, and its first step is `git switch <local branch>` (the branch the spec phase made).
  5. **Approve.** Manual mode: present the ready PRs with their evidence, head SHAs and, for spec-phase batches, their deviations with W4.2b's rulings, and stop; the human
     approves a PR at a SHA. This stop is a pause, not the run's end: don't run `run-end` here.
  6. **Merge.** Spec-phase batches merge only at a SHA with a clean W4.2b verdict. Reply to and resolve every thread (step 8's triage), post the merge summary, check the PR's
     commit messages for trailers the user forbids (pass `--subject`/`--body` without them if needed), then
     `gh pr merge --match-head-commit <the SHA verified in W4.1, or approved in W4.5>` with the repo's merge
     method (step 12) and without `--delete-branch` (the worktree holds the branch). If the merge is refused
     because the head moved, go back to W4.1 (manual mode: ask again). Then stop the batch's workers:
     `python3 $FOREMAN/hooks.py stopping <batch>`, TaskStop every live `worker` task of the batch (`fw-<batch>`,
     `fw-<batch>-<k>`) by its id, and `task <batch> done <id>` (a finished worker teammate holds a terminal like
     a reviewer does; a TaskStop error for a task that already ended is ignored, and the task is still marked done). Then remove the worktree and the remote branch.
  7. **After every merge** run `post_merge_check` (under `$FOREMAN/gate-lock.sh orchestrator -- '<cmd>'`, like every gate) on the updated main and confirm it ran tasks (a
     change-scoped command such as `--affected` runs nothing on the default branch). Red: stop merging and
     report; a fix or revert goes through a PR.
- **W5 End of wave.** Pull main; run `{{gate}}` and `post_merge_check`, each under `$FOREMAN/gate-lock.sh orchestrator -- '<cmd>'`; red: stop. Then ONE bookkeeping PR,
  the only PR that edits the backlog, the rules files and generated files, built in an isolated worktree (another
  session may be editing the main checkout; never `git add -A` there). When its diff has paths that aren't exempt
  (anything beyond the backlog, the rules files and `gate_exempt`), run `{{worktree_setup}}` in that worktree and
  then `$FOREMAN/gate-lock.sh orchestrator -- '<gate>'` from its top level before pushing: the hooks deny a push
  without a gate receipt. With `bookkeeping_tool`, save each worker's
  `json` block (plus its `pr`) and run the tool on them: it marks rows, numbers new ones and applies rule notes,
  and refuses (writing nothing) on a mismatch, which you then fix by hand. List every row id in its body;
  mark rows `done (#N)`, or leave them `ready` with a note of what is left; fold each worker's RULE NOTES
  (fixing the exact sentences they quote) and NEW ROWS; turn each RETRO into a `feedback_*` memory (and a
  rules note when the lesson is general); run `graphify update .` if the repo uses it; include any diff the
  gate left behind. It goes through the same checks: fix real contradictions, and after one round defer
  docs nits (a Critical/Important finding still blocks). Then run `python3 $FOREMAN/hooks.py forget <batch>` for each
  of this wave's batches (it drops the hooks' records, never the batch state the report table reads).
  - **Parked batches.** Write one report per parked batch,
    `{"pr": 0, "append": [{"id": <row>, "note": "spec parked (<reason>): <spec path> — <open findings or question>"}]}`,
    and feed it to `bookkeeping_tool` with the others. `<reason>` is the park reason as the tables spell it
    (`open question`, `stopped by the human`, …), and `<spec path>` is `no spec` when nothing was copied. Notes
    are one line, with any ` | ` escaped as
    `\|`, and never quote a placeholder id like `<prefix>-NEW-a`: bk.py refuses one with no new row. The
    bookkeeping PR also commits each copied spec from `foreman/parked/<batch>/` into `{{design_spec_dir}}`, with
    a `Status: parked — <reason>` line under its title. Then delete the batch's local branch and
    `foreman/parked/<batch>/`.
- **W6 Next wave or stop.** The next wave may start once main is green after W5, while the bookkeeping PR is
  still in review, if: its pool excludes every row that PR files or closes, each worker's briefing carries that
  PR's rule notes inline (the rules text is not on main yet), and the next bookkeeping PR is opened only after
  this one has merged (both edit the same files). Otherwise it starts after the merge. Stop when the
  pool is empty, `parallel_max_waves` is reached, the user says stop, main is red, CI is down, or half a
  wave failed. A worker with no report and no PR activity for `parallel_worker_timeout` minutes (not
  counting queued CI) is stuck: run `python3 $FOREMAN/hooks.py stopping <batch>`, then TaskStop its recorded
  `worker` id, leave its
  worktree and PR alone, keep its rows out of the next pool, report it, and carry on. A stuck worker whose batch isn't released yet (status `spec`, `review <r>` or `passed`) is parked instead (W3.6, "worker stuck";
  parking runs the same `stopping` first); a released one gets the rule above. A worker waiting after `SPEC READY`, or awaiting release, isn't silent:
  its clock restarts at every findings or release message. Rows parked during this invocation stay out of later
  waves' pools (W2's parked-row rule covers later invocations). When the run stops, run
  `python3 $FOREMAN/hooks.py run-end` last, and only after every worker has reported or been stopped (`stopping`,
  then TaskStop): ending the run while a background worker still runs turns off the run-only hooks that guard it.
  - **Run report.** At the stop, run `python3 $FOREMAN/batches.py table report` and paste its whole output as text
    in the final report as the spec-phase table: one row per batch with the spec wall-clock, the rounds, the reviewer that
    landed (or the fall-through, or "no cross-model review"), the degraded flag, the reviewer model, the status
    and park reason, and the review rounds after the PR opened. The last column is the measure: follow-up rounds
    per PR after review. Then `run-end`. If the report table never reaches your text, the hooks hand it
    back once at the end of the turn and ask for it to be pasted.

### Worker contract

You run steps 2-8 on your batch in your own worktree, with these overrides:
- First read the project config (guardrails, `shared_resources`) and every `{{rules_glob}}` file for your area.
  Then run `{{worktree_setup}}` in your worktree (default `$FOREMAN/wt-setup.sh <worktree>`: copies
  `worktree_env_files` from the main checkout, runs `worktree_install`, then `stale_check`).
- **Step 2 is the spec phase** when your prompt's first line ends in ` spec-phase`: follow **Worker spec phase**
  (copied into your prompt). Until the orchestrator releases your spec, edit and commit nothing outside
  `{{design_spec_dir}}`, and push nothing. A tag without ` spec-phase` means your batch's spec was released
  before you started: do Worker spec phase step 8.
- Own only your files (and your own entries in `parallel_shared_files`, and your batch's spec file); if you need
  another file, stop and report (in the spec phase: a scope request in the outline). A message "owned files now
  also include <file>" adds that file.
- Remote work: the `git`+`gh` column of the merge-tool mapping; open the PR ready for review. Your branch
  already exists (the spec phase made it): skip `git switch -c`, and commit with explicit paths, or `git add -A`
  only once the spec phase is over.
- Step 6: run the reviewer chain, `$FOREMAN/review.sh origin/main <scratch>/<batch>-review.log` (in the
  background; it can take 15 minutes), AND a read-only reviewer subagent of a tool-restricted type (e.g.
  Explore) when you have one. Name the reviewer that ran (`<log>.reviewer`) in the PR; when the chain is
  exhausted, say in the PR that no cross-model review ran (the orchestrator's read-only review still runs).
- Step 9: no browser and no dev server. Report `NEEDS BROWSER CHECK` with exact steps and predicted
  observations, or `NO APP SURFACE`. When the config sets `browser_lib`, deliver the check as a script
  (`<scratch>/<batch>-check.mjs`) built on it: fixtures with your id prefix and cleanup in a `finally`, one
  `expect(label, got, predicted)` per claim, written so it passes on your fix and DIFFERS on main (the control).
  Syntax-check it (`node --check`), never run it: the orchestrator runs it on main first, then on the PR head.
- Shared state: touch only data you create (with your id prefix), never reset or migrate a shared database,
  never reach a real external platform, delete throwaway files and fixtures before committing, and run every
  gate under a slot, from your checkout's top level, with the configured gate string as ONE argument:
  `$FOREMAN/gate-lock.sh <batch> -- '<gate>'` (never take or clear a slot by hand). That run mints the gate receipt
  a push needs: commit every file the gate changed and list it in your report, then push in its own call, after the
  gate and the commit; don't edit files while a gate runs. A red that appears only under load: re-run that file alone, then the whole
  gate, which must end green in one run. A red your first fix didn't clear: find its root cause (`superpowers:systematic-debugging`
  if available) before a second fix.
- Step 8: push follow-up commits; never amend or force-push once the PR is open. Update your branch (merge
  `origin/main` into it, do not rebase) only when it conflicts or the orchestrator asks, then re-run the gate.
- Step 10: do not edit the backlog, the rules files or generated files. Report RULE NOTES (quote the exact
  sentence your change makes false), NEW ROWS (full row text) and a RETRO (defects you introduced and what
  would have caught them).
- Attribution and temp files: the user's and the repo's rules override harness defaults (e.g. no
  `Co-Authored-By`); temp files go in the scratchpad under your batch name.
- Never merge. Poll for new bot comments after your last push, and stop pushing once you send your final report
  (`SPEC READY` is not one).
- Don't invoke `finishing-a-development-branch`, `subagent-driven-development`, `executing-plans` or
  `using-git-worktrees`: their merge menu, execution handoff and worktree prompt contradict this contract.
- During a run, hooks enforce part of this contract: a push needs a gate receipt for the pushed tree; no merge,
  no force-push, no rebase, no backlog, rules or config edits; no forbidden trailer lines; before a spec-phase
  batch's release, no edit or commit outside `{{design_spec_dir}}`, no push, and a stop without `SPEC READY` is
  sent back once; and a final report
  without one valid fenced `json` block is sent back once.
- Report: PR URL, head SHA, the spec path and its deviations after release, check states on it, unresolved
  threads, per-row changes, evidence, files outside
  your owned list, declined items and why, surface verdict with steps and predictions (and the check script's
  path), RULE NOTES, NEW ROWS, RETRO, risks. End with ONE fenced `json` block the orchestrator feeds to
  `bookkeeping_tool`: `{"done": [{"id", "note"}], "append": [{"id", "note"}], "new_rows": ["| <prefix>-NEW-a | title | risk | ready | acceptance | source |"],
  "rule_notes": [{"file", "old", "new"}]}` (`old` is the exact sentence, one match; a raw ` | ` in a note or a row
  shifts the table's columns: write `\|`; the orchestrator adds `pr` and numbers `<prefix>-NEW-*` from the real max id;
  a new row's risk is exactly `low`, `medium` or `high`, its status a known word (`ready`) and its title not empty:
  one bad row makes `bk.py` refuse the whole report).
  The block may also carry `"spec": "<spec path>"`, which `bk.py` ignores.

### Worker spec phase

You are in it when your prompt's first line ends in ` spec-phase`.
1. After the setup above, read your rows, the `{{rules_glob}}` files for the area and recent feedback memories.
2. Explore before designing: read the code the rows name; `git grep` every caller and every writer of each
   function, column, status or file the change touches; for each write, find which client or credentials
   perform it, which roles or permissions reach that path, and why row-level security or permissions allow it;
   note which rules entries apply.
3. Invoke `superpowers:brainstorming` if available, for its exploration and design discipline (without it, do
   that exploration yourself), with these overrides, which
   win over the skill's own text: always write the spec file from the template, whatever path the skill would
   classify the batch as (a "bounded" batch still gets a spec); no visual companion, no per-section approval
   and no questions to the user (a question the code can't answer goes under **Open questions**); the
   orchestrator's release is the skill's HARD-GATE approval, so never end a turn with a question: end the spec
   phase with `SPEC READY`; don't invoke `writing-plans`: write the Implementation outline section instead.
4. Write the spec from the template (its absolute path is in your prompt) to
   `{{design_spec_dir}}/<YYYY-MM-DD>-<row ids joined by ->-design.md`. Replace every `⟦FILL: …⟧` marker; a
   section with nothing in it reads "None — <reason>". A file outside your owned files goes in the outline as a
   `- **Scope request:**` line. Label every open question `- **Blocking:**` (the batch can't be built as
   specified without the answer) or `- **Non-blocking:**`; on a Blocking one, finish the spec and report
   `SPEC READY` at once (step 5). If `{{design_spec_dir}}` already holds a parked spec for these rows
   (`Status: parked`), start from it: delete its `Status: parked` line and its old Review responses, add any section the current template has that the parked spec lacks (a spec parked under an older template would otherwise fail the pre-check twice and park again), then revise
   it. Commit it with `git add -- <spec path> && git commit -m "<message>"` in a call of its own, never `git add -A`,
   as your branch's first commit. Don't push. The build phase continues on this same branch.
5. Report with `SPEC READY <absolute worktree path> <branch> <spec path> <sha>` as the FIRST line of your final
   message, then up to five summary lines, and stop. `SPEC READY` is not your final report.
6. If you stop without `SPEC READY` (a question, a context limit, an error), the orchestrator resumes you once:
   finish the spec, put questions under Open questions, and end with `SPEC READY` as the first line.
7. On findings: revise the spec as a new commit (never an amend), answer every finding under **Review
   responses** (fixed, or declined with the reason), and report `SPEC READY` again. A question that is answered
   (by a review or a comment round) leaves `## Open questions` and is recorded under **Review responses**; if none
   are left, the section reads `None — <reason>`.
8. On release ("Spec approved at `<sha>` …"): build it, the Worker contract's steps 3-8, on this same branch.
   The spec body is frozen at release: a change after it goes only in an appended `## Deviations after release`
   section, one entry per deviation with its reason, and in the PR body. The PR's first commit is the spec. Answer
   the release message's Minor findings in the PR body. If a row needs a product decision, do the decidable part
   and report the rest.

## Hooks

The plugin's settings hooks (`hooks/hooks.json`, served by `$FOREMAN/hooks.py`) enforce parts of this file at the
tool call. They fire in every session where the plugin is enabled, but only in a repo with a foreman config.
Run-only pieces act only while this session's **run marker** exists: a prompt starting `/foreman` (not `ui`,
`init` or `seed`) writes it, `python3 $FOREMAN/hooks.py run-end` at every termination deletes it, and it expires after 48
hours. A session moved to the background, forked or resumed under a new id keeps its run through an alias.

| id | when | what it does |
|---|---|---|
| `gate_before_push` | run | `git push`, `gh pr create` and `gt submit` need a gate receipt for the pushed tree (minted by `gate-lock.sh` with the exact `gate`), unless the branch changes only exempt paths |
| `worker_guard` | run, subagents | no merge, force-push, push to the default branch, rebase, `gt modify` once pushed, or backlog/rules/config edits |
| `merge_discipline` | run | `gh pr merge` needs `--match-head-commit`; `--delete-branch` is refused while another worktree holds the branch; no `gt merge`; no merging `gh api` call |
| `trailer_guard` | always | `forbid_trailers` lines are refused in commit, PR and comment messages, and in a merge that would keep them |
| `backlog_check` | always | after an edit of the backlog, its problem rows are reported back |
| `worker_briefing`, `report_check` | tagged workers | a subagent whose prompt starts `foreman-worker: <batch>` is briefed with the Worker contract, and its final report's `json` block is checked once |
| `script_allow` | run | foreman's own scripts run without a permission prompt, in strict shapes only |
| `spec_gate` | run, `spec-phase` workers | edits and commits stay in `design_spec_dir` until `hooks.py release <batch> <sha>` |
| `batch_state` | run, orchestrator | a worker's `batches.py` call is denied; at the checkpoint every question is denied until the current `batches.py table checkpoint` is in your text, in a message of its own; a `run-end` before `batches.py table report` is in your text leaves the report owed, and the end of the turn hands it back once; TaskStop needs a recorded id, never a batch name, and a worker only after `stopping`; `hooks.py release` needs a passed batch at its reviewed SHA and, in a manual run, the checkpoint table pasted after the pass |
| `status_pane` | run | the wave status band above the prompt: one line per batch from the batch files |

Orchestrator-only CLIs (a worker's call is denied): `python3 $FOREMAN/hooks.py release <batch> <sha>` (exit 3 means
the hooks are inactive, so nothing gates the batch), `stopping <batch>` before any TaskStop of a tagged worker, and
`forget <batch>` at W5. `python3 $FOREMAN/batches.py` is the orchestrator's too (a worker's call is denied). The
checkpoint gate lets the third question through after two denies against one unchanged table, and a missing run
report is handed back only once, so no gate can trap the session. Turn pieces off per repo with `hooks_disabled: [<id>, …]`. The user's `! <command>` is
never checked: it is the override.

## Backlog UI (`/foreman ui`)

Start the local backlog UI for this repo and print its URL; do nothing else in this invocation.

1. Check that the config's `backlog_path` (default `.claude/improvement-backlog.md`) exists on `origin`'s
   default branch: after `git fetch -q origin`, `git cat-file -e origin/HEAD:<backlog_path>`, or
   `origin/main:<backlog_path>` when `origin/HEAD` isn't set (the same fallback `ui.py` uses). If the file
   isn't there, say so and suggest `/foreman init`; don't start anything.
2. Run it in the background, then print the URL it outputs (one line; the token is its `#t=` fragment):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ui.py" --repo "$(git worktree list --porcelain | awk '/^worktree /{print substr($0, 10); exit}')"
   ```
   `--repo` is the main checkout, so every worktree of the repo shares one pending list.
   A second `/foreman ui` reuses the running server. `/foreman ui stop` runs the same script with `--stop`.
3. Tell the user, in two or three lines:
   - Edits collect as pending changes on this machine.
   - **Publish** opens one PR from a temporary worktree, so their checkout is never touched.
   - The UI's PR is separate from the `/foreman` bookkeeping PR. When both change the same row, the UI shows the conflict before publishing.

## Init (`/foreman init`)

Set up a project for foreman. Write files only; never commit, push or open a PR.

1. **Refuse to overwrite.** If `.claude/foreman.config.md` or the backlog path already exists, stop and say so.
   If only the legacy `.claude/improve-next.config.md` exists, say it is already read as a fallback, and
   offer `git mv .claude/improve-next.config.md .claude/foreman.config.md` instead of writing a new one.
2. **Detect** (each probe may fail; record "not detected" and move on, never guess silently):
   - `gate`: the repo's own check — `package.json` scripts (`check`, else `test` plus `lint`/`typecheck`, run
     with the package manager its lockfile implies), `Makefile` (`check`/`test`), `pyproject.toml`/`tox.ini`
     (`pytest`), `Cargo.toml` (`cargo test`), `go.mod` (`go test ./...`).
   - `merge_tool`: `graphite` when `gt` is on PATH and the repo has Graphite metadata (`.git/.graphite_repo_config`), else `git`.
   - `required_ci`: job names from `.github/workflows/*.yml` that run on `pull_request`; when `gh` works and a
     PR exists, prefer the check names `gh pr checks <last PR>` reports.
   - `review_bots`: authors ending in `[bot]` on the last few PRs (`gh pr list --state all --limit 5`, then
     `gh pr view <n> --comments`). No remote or no `gh` access: leave the list empty and say so.
   - `reviewers`: for each adapter in `${CLAUDE_PLUGIN_ROOT}/reviewers/` whose CLI is on PATH, a commented
     example entry; uncomment none — the user picks the order and the models.
   - `worktree_env_files`: untracked, ignored env files at the repo root and in app dirs (`git status --ignored --porcelain` lines matching `.env*`, minus `*.example`).
   - `worktree_install`: from the lockfile (`pnpm install --frozen-lockfile`, `npm ci`, `yarn install --immutable`, `bun install --frozen-lockfile`, `uv sync`, …).
   - `dev_url`: the dev script's port (`http://localhost:<port>`), else leave the default.
3. **Write** `.claude/foreman.config.md` from `${CLAUDE_PLUGIN_ROOT}/templates/foreman.config.md`, filling the
   detected values and keeping every comment; and the backlog (`backlog_path`, default
   `.claude/improvement-backlog.md`) from `${CLAUDE_PLUGIN_ROOT}/templates/improvement-backlog.md`.
4. **Report** each key: detected value and from where, or "not detected — set it by hand". Then the next
   steps: review the config, add real rows to the backlog (`/foreman seed` drafts them from the code), commit
   both, run `/foreman`.

## Seed (`/foreman seed`)

Draft the backlog's first rows from the codebase and, if one is given, a PRD. Write the backlog file only;
never commit, push or open a PR. Not a run: no run marker, no worker.

1. **Check the backlog.** Read `backlog_path` and `id_prefix` with `python3 "$FOREMAN/cfg.py" <repo> <key> <default>`
   (`<repo>` is `git rev-parse --show-toplevel`). If the backlog file doesn't exist, stop and suggest
   `/foreman init`. If it holds a row other than the template's placeholder (the one whose source is
   `setup example, replace me`), stop and say so unless `append` was given. With `append`, keep every existing
   row and skip any draft whose title matches an existing row; `bk.py` numbers the new ones above every existing
   id and puts them after the highest-numbered row. The placeholder is dropped either way.
2. **Survey.** Read the README and docs, the directory layout, the tests and the `gate`. Grep for
   `TODO|FIXME|HACK`. Look for untested core modules, dead config, stale docs and gate gaps. Build on what exists;
   don't propose what the code already does. Don't run the gate and don't install anything. With a
   `<prd-path>`, read it too: turn every explicit requirement into rows and fill gaps around them. A requirement
   that needs a human decision becomes a `deferred` row that says what is unclear.
3. **Draft rows.** One row = one PR-sized unit:
   - Atomic: one concern each. Order them foundation first (test and tooling gaps before features), because the
     picker offers `ready` rows in file order.
   - When a row can't start before another, put `**Depends on:** <prefix>-NEW-a, <prefix>-NEW-b` in its
     `acceptance` (ids joined by `, `, using the placeholders: `bk.py` numbers them). Only a real prerequisite
     counts; file order already says "earlier first".
   - The most direct path. No row for speculative or cosmetic work.
   - `acceptance` is verifiable: a test that fails before and passes after, a command's output, a green gate.
     Never "improve X".
   - `source`: `file:line`, the PRD section, or `seed`.
   - `risk: high` for schema or migration changes, auth, payments, a public API or a wide blast radius (those
     rows run only when named); otherwise `low` or `medium`. `status: ready`, or `deferred` as above.
4. **Size.** Score each draft 1-10 for effort and blast radius. A row at 7 or more is more than one PR: split it
   into rows of 6 or less, the prerequisite first. A row you can't split below 7 is `deferred`, and its
   `acceptance` says what makes it too big. Write at most 15 rows, best first; say in the report what you left out.
5. **Write.** Use `bk.py` itself, not `bookkeeping_tool`: a custom tool may not add rows to an empty table.
   1. Save `{"pr": 0, "new_rows": ["| <prefix>-NEW-a | title | risk | ready | acceptance | source |", ...]}` to a
      scratch file, one distinct `-NEW-<x>` placeholder per row, one line per row, a literal pipe in a cell
      written `\|`.
   2. Run `python3 "$FOREMAN/bk.py" <repo> --dry-run <file>`. It refuses the whole write on a bad row (a risk
      other than `low`, `medium` or `high`, an unknown status word, an empty title, a raw ` | `): fix the rows it lists under PROBLEMS and run it again. The ids a dry run prints are one too high
      while the placeholder is still in the file.
   3. Only once the dry run is clean: if the placeholder row is present, delete that one line with Edit, then
      run the same command without `--dry-run`. Don't edit the backlog around `bk.py`. A write that succeeded is
      final: running it again adds the rows a second time.
6. **Report** the written rows (id, risk, title), what was left out and why, and the next steps: read the diff,
   edit what you disagree with (or reorder in `/foreman ui` once it's on `origin`), commit, run `/foreman`.
   Ids start above the highest id on origin's default branch or in an open UI branch, and an id is never
   reused: after a pushed `init` the first row is `<prefix>-2`, not `<prefix>-1`.

## Step 0.5 — Load config (do this first, before step 0)

Read `.claude/foreman.config.md`, or the legacy `.claude/improve-next.config.md` when only that exists (YAML frontmatter = bindings; markdown body = project notes/guardrails to honor). No config at all: say so, suggest `/foreman init`, and continue on the defaults below. Resolve these bindings, applying the default / degradation when a key is absent:

| key | meaning | default / degradation if absent |
|---|---|---|
| `backlog_path` | the improvement backlog | `.claude/improvement-backlog.md` |
| `rules_glob` | the mistake-registry rules to read (step 3) + codify into (step 10) | `.claude/rules/*.md` |
| `gate` | local pre-PR gate command | required — if absent, run the repo's obvious check (`pnpm check` / `npm test` / `make check`) and say which you chose |
| `extra_gates` | conditional extra gates (`{when, run}`) | none |
| `merge_tool` | `graphite` \| `git` \| `auto` | `auto`: use `gt` if on PATH, else `git`+`gh` |
| `review_bots` | the PR bots step 7 must wait for | discover from `gh pr view <n> --comments` after CI is green (weaker "all" guarantee — say so) |
| `required_ci` | CI checks that must be green | all required checks reported by `gh pr checks <n>` |
| `id_prefix` | backlog row id prefix (`IB` → `IB-12`) | `IB` |
| `reviewers` | step 6's cross-model reviewer chain, tried in order: `- { use: <adapter>, model: <id>, effort: <level> }`; adapters in `${CLAUDE_PLUGIN_ROOT}/reviewers/` | legacy `extra_reviewers` (each command a one-entry `custom` chain), else none (step 6 has only the Claude reviewer; say so) |
| `reviewer_timeout_seconds` / `reviewer_stall_seconds` / `reviewer_max_diff_kb` | per-reviewer hard timeout / silence that counts as hung / diff size above which diff-in-prompt adapters are skipped | 900 / 150 / 200 |
| `dev_url` | dev tunnel/base URL for browser-verify | fall back to `http://localhost:3000` and flag the missing URL |
| `dev_restart` | skill/command to refresh a stale dev server | none (start the dev server the project's normal way) |
| `e2e` | `{spec_dir, run, gating}` for the durable Playwright codification | none (skip the codify-into-spec sub-step) |
| `post_merge_check` | whole-tree check run on the default branch after every merge and at W5; must not be change-scoped | `{{gate}}` |
| `parallel_max` / `parallel_max_waves` / `parallel_ui_max` / `parallel_worker_timeout` | `parallel N` caps: concurrent workers / waves per invocation / app-surface batches per wave / minutes of worker silence | 5 / 3 / 2 / 90 |
| `parallel_skip` | rows a wave never batches (ids, tags, title patterns) | none (W2 always drops `risk: high`, `Procedure` rows and rows named by an open PR) |
| `parallel_shared_files` | files several batches may edit, each touching only its own entries (e.g. locale JSON) | none |
| `worktree_setup` | command a worker runs first in its worktree (`{worktree}`) | `$FOREMAN/wt-setup.sh {worktree}` |
| `worktree_env_files` / `worktree_install` / `stale_check` | read by `wt-setup.sh`: untracked files copied from the main checkout / install command / command that fails on a stale install at `{dir}` | none / none (skip install) / none |
| `gate_slots` | how many gates may run at once across all worktrees (`gate-lock.sh`) | 2 |
| `browser_lib` | helper library workers build their browser-check scripts on | none (workers describe the check in prose) |
| `pre_browser_check` | command the orchestrator runs before a step-9 check (`{repo}`); non-zero stops the check | `$FOREMAN/wt-setup.sh --check {repo}` when `stale_check` is set, else none |
| `bookkeeping_tool` | command that folds worker `json` reports into the backlog and rules (`{repo} [--dry-run] <reports...>`) | `python3 $FOREMAN/bk.py {repo} [--dry-run] <reports...>` |
| `shared_resources` | notes every worker must follow: shared database and test-data rules, browser and sign-in rules, env files, VCS quirks in worktrees, gate slots | none |
| `forbid_trailers` | lines that must never start a commit, PR or comment message (a `/…/` entry is a regex searched within each line) | none |
| `gate_exempt` | gitignore-style paths a push may change without a gate receipt; `backlog_path` and `rules_glob` always are | `docs/**`, `**/*.md` |
| `worker_agent_types` | subagent types the worker briefing and report check apply to | `general-purpose`, `workflow-subagent` |
| `hooks_disabled` | hook ids to turn off in this repo (see **Hooks**) | none |
| `design_spec_dir` | where parallel-mode batch specs are committed (W3.5), and the only place a spec-phase worker may edit or commit before its spec is released (`spec_gate`); not `e2e.spec_dir` | `docs/superpowers/specs` |
| `spec_review_rounds` | spec review rounds without a pass before a batch is parked (W3.5) | 3 |
| `spec_phase_timeout` | minutes of worker time in the spec phase before a batch is parked (W3.5's clock) | 60 |
| `status_pane` | the wave status band above the prompt during a run | `true` |

### Merge-tool command mapping (referenced by steps 0, 5, 7, 10, 12)

| action | `graphite` | `git`+`gh` |
|---|---|---|
| sync to fresh main | `gt sync` | `git switch main && git pull --rebase` |
| create branch + commit | `gt create --all -m "<msg>"` | `git switch -c <branch> && git add -A && git commit -m "<msg>"` |
| submit as draft PR | `gt submit` (draft) | `git push -u origin HEAD && gh pr create --draft --fill` |
| amend + re-push | `gt modify --all`, re-run the gate (step 4), then `gt submit` in its own call | `git commit --amend -a`, re-run the gate (step 4), then `git push --force-with-lease` in its own call |
| post-merge sync | `gt sync` | `git switch main && git pull --rebase && git branch -d <branch>` |

## Steps

### 0. Precondition
- Sync to fresh main (merge-tool "sync"). If the working tree is dirty (`git status --porcelain`, **including untracked files**), STOP — never start a pass on uncommitted work. Untracked files count on purpose: step 5's create stages them (`git add -A` semantics), so a stray file would be silently swept into the commit.

### 1. Pick the item
- Pick with code, not by eye: `python3 "$FOREMAN/backlog.py" ready <repo> --limit 1` (add `--tag x` for `tag:<x>`, `--no-high` in autonomous mode) prints the first eligible id in the order of `{{backlog_path}}`. A `ready` row is eligible when every row in its `**Depends on:** <id>, <id>` marker (inside its `acceptance`) is `done`; a row with no marker is always eligible. Rows that wait are listed on stderr with the reason. Exit 0 means ids printed, exit 1 means none is eligible, exit 2 is a usage or file error. The `<item-id>` argument runs that row instead.
- A row named by `<item-id>` runs even when its dependencies aren't done: echo its unmet dependencies; in manual mode ask the human first.
- If `risk: high`: **manual mode** → STOP and ask the human to confirm; **autonomous mode** → `--no-high` already skips it (a high-risk item only ever runs from an explicit `<item-id>`).
- If no id is printed: STOP. In autonomous mode, report "no eligible rows; N waiting: <id: reason, …>" when rows wait (a human unblocks them or runs one by id), else "backlog empty" (with `tag:<x>`: "no ready [x] rows"), and end the loop.
- Echo the chosen `id`, `title`, and `acceptance` back to the user.

### 2. Plan (non-trivial only)
- Anything beyond a typo/one-liner → invoke `superpowers:brainstorming` then `superpowers:writing-plans` if available; without them, write a short plan yourself (files to change, tests, risks). Trivial → skip to step 3. These overrides win over the skills' own text:
  - **Autonomous mode** (`loop`/`--auto-merge`): no human gates and no question to the user. Your own check of the plan against the row's `acceptance` is the skill's HARD-GATE approval; a question the code can't answer goes in the PR body. **Manual mode:** ask the human only about a product call the code can't settle.
  - Stop once the plan is written. Don't take `writing-plans`' Execution Handoff, and never invoke `subagent-driven-development`, `executing-plans`, `using-git-worktrees` or `finishing-a-development-branch`: implementation is step 3, in this checkout, and merging is steps 11-12.

### 3. Implement
- **Row procedure:** if the row's `acceptance` names `Procedure <path>`, read that file now and follow it as this pass's implement/verify procedure (it may add preflight, verify and stop rules on top of this command's, including ending the pass with the row `blocked — <reason>`).
- **Pre-flight self-review FIRST:** read every file matched by `{{rules_glob}}` and recent `feedback_*` memory — the mistake registry. Check your intended change against every relevant entry so you don't reintroduce a known defect (this is what makes the loop compound).
- **Behavior-preserving** (decomposition/refactor): the existing golden-master test MUST stay green with ZERO edits — that green run IS the proof. Only ADD tests for newly-extracted units. Decline behavior-CHANGING ideas; log them as new backlog rows.
- **New behavior / bugfix:** use `superpowers:test-driven-development` if available (otherwise write the failing test first yourself): red → green.
- Delegate mechanical/parallelizable work to subagents (Task tool). Follow the rules for the touched area.

### 4. Gate
- Run the gate under a slot, from the checkout's top level, with the configured string as ONE argument:
  `$FOREMAN/gate-lock.sh orchestrator -- '{{gate}}'`. Loop fix → rerun until green; when the first fix doesn't turn it green, find the root cause before a second one (`superpowers:systematic-debugging` if available). During a run the hooks deny a
  push (`git push`, `gh pr create`, `gt submit`) whose tree has no passing gate receipt, so when the branch changes
  any non-exempt path, re-run this after every change you push (steps 7, 8 and 10 included); a branch that changes
  only exempt paths needs no receipt.
- Run any `{{extra_gates}}` whose `when` condition matches the diff.

### 5. Draft PR (sibling branch off main — NOT stacked)
- Merge-tool "create branch + commit" with `"type(scope): <message>"` → "submit as draft". Record the PR number.

### 6. Request review — independent local reviewers
- Invoke `superpowers:requesting-code-review` if available (a Claude `general-purpose` subagent reviewer); without it, dispatch that subagent yourself with the diff, the row's `acceptance` and the Critical/Important/Minor labels the merge gates read.
  - **Give it a Spec axis, reported apart from defects** (one axis can hide the other). The spec is the row's `acceptance` plus your step-2 plan. It reports: (a) what the spec asked for that is missing or only partly done; (b) what the diff does that nobody asked for (scope creep); (c) what looks implemented but looks wrong. Each finding quotes the spec line it rests on.
- **Also run the reviewer chain**: `$FOREMAN/review.sh <base> <log>` (base = the PR's base branch, e.g. `main`) — a cross-model second opinion (a different model family catches different defects). Run it in the background; each reviewer is bounded by `reviewer_timeout_seconds` and the stall watchdog, and the chain moves to the next reviewer on quota, credits, auth, an unknown model, a stall or a timeout.
  - Exit 0 = a real review is in `<log>`, and `<log>.reviewer` names the reviewer; read and triage it. Exit 1 = the chain is exhausted (`<log>.attempts` says why each fell through): say on the PR that no cross-model review ran and rely on the Claude reviewer. Never treat a missing review as a pass, and never `2>/dev/null` the run.
  - The chain sends the diff (or the repo, for reviewers that read it) to every provider it tries.
- **When remote CI/bots are unavailable (billing-block/outage), these local reviews ARE the review gate.**
- Flip the PR draft → ready (`gh pr ready <n>`) to trigger the remote bots (when CI is live).

### 7. WAIT for all bots + CI  ← hard requirement
- Poll `gh pr view <n> --comments` and `gh pr checks <n>` until:
  - **All `{{review_bots}}` have posted** (or, if discovering, the comment set has stabilized).
  - **`{{required_ci}}` is green.**
- Red CI → fix → merge-tool "amend + re-push" → re-wait. Ignore known-environmental failures listed in the project rules.
  - **Classify a red before acting.** A flake or infrastructure failure (lost runner, network, quota) earns ONE re-run; an identical second failure is real, not flake. A failure in code the diff never touched means a stale base: check `git merge-base --is-ancestor origin/<default> HEAD` and merge the default branch in before changing any code.
  - **When your first fix doesn't turn it green,** find the root cause before a second one (`superpowers:systematic-debugging` if available).
- **Optional E2E (`{{e2e}}`):** if the project marks E2E non-gating / opt-in, don't block on it — read it for signal, investigate *persistent* reds via the report artifact.
- **Ceiling:** if a required bot never posts, or CI stays pending, after ~90 minutes counted from when the check starts (a check still queued after 3 hours means CI is down) (or a fixed poll cap), STOP and notify the human instead of polling forever — a missing bot (outage, app removed, misconfig) must not hang the pass.
- **Poll hygiene:** a settle-loop that exits on "0 pending" must FIRST confirm the `gh pr checks` output is non-empty (e.g. it contains a known job name) before counting — `gh pr checks` intermittently returns empty output that a bare pending-counter misreads as "settled" while jobs are still running.
- **Record the head SHA** that the green `{{required_ci}}` checks and every `{{review_bots}}` review ran on; step 12 merges exactly that SHA, and a push after it means step 7 again.

### 8. Address comments — invoke `/foreman:pr-address-comments`
- Carry its guardrails: (a) **grep-verify** every bot-cited "convention" against the actual file before complying — cite the rule file:line when declining (bots fabricate rules); (b) **diminishing-returns gate** (round ≥3 + severity downshift + no open real defect + CI green → recommend merge, stop iterating).
- **Triage the step-6 local reviews with the SAME discipline:** grep-verify each claim against source, apply the latent-vs-live + scope-discipline + diminishing-returns gates, decline non-issues with a cited reason. Because an extra reviewer is a *different model family*, weight a real defect it raises even when the Claude subagent rated that area clean. Re-run an extra reviewer only if a step-8 fix materially changed the **code** — docs/bookkeeping changes don't warrant a re-review.
- On a behavior-preserving PR, decline behavior-changing suggestions → file as new backlog rows.
- **MISTAKE-CAPTURE:** if a bot (or step 9) catches a REAL defect *you introduced*, fixing it is only half — record the mistake → lesson in step 10.

### 9. Browser-verify in claude-in-chrome  ← hard requirement (every change with an app surface)
- **Default = verify, in the `claude-in-chrome` browser** (`mcp__claude-in-chrome__*` — NOT Playwright-as-substitute, NOT "the code looks right": `router.refresh()` and similar can silently fail). Ensure the dev server is live at `{{dev_url}}` (run `{{dev_restart}}` first if stale — multi-minute render + idle CPU = stale). Navigate the changed route(s); confirm **0 console errors**; screenshot desktop + mobile (375px); verify against the REAL rendered layout. For a state-dependent render, **predict from the data then observe**: read the rows/state the code reads, state the expected UI, confirm the browser matches. Summarize the evidence on the PR.
- **The skip is a NARROW, explicitly-justified exception — only for ZERO application surface:** process docs (this file, rules, config), CI/workflow config, or pure test-only edits. A change is NOT skippable just because it's "backend" — a loader, RSC data fn, server action, or status util DRIVES what renders, so navigate the route it feeds and verify the rendered result. Do NOT mis-file render-affecting logic as "no UI surface".
- **Codify the verify into a Playwright spec when the pass adds/changes a user-facing flow AND `{{e2e}}` is configured** — the manual browser-verify is throwaway; the spec is the durable regression guard for exactly what you just checked. Add it under `{{e2e.spec_dir}}`, reuse the project's page-object + API-setup patterns, and **run the NEW spec LOCALLY** to confirm green (`{{e2e.run}}` — substitute `{spec}` with the spec path; the binding is an isolated single-spec run, NOT the full suite). If the binding needs the dev server's port (e.g. a prod-mode harness that refuses a busy port), stop the dev server first and restart it afterwards. Scope: genuinely new/changed flows only — NOT a per-pass requirement, and never for pure backend/SQL/test/doc passes.

### 10. Compound — on the active PR branch, BEFORE approval (incl. mandatory mistake-retro)
Do the bookkeeping now and commit it to **this PR's branch** so it is reviewed and merged **atomically** with the work. Do NOT defer it to a separate follow-up chore PR — that both dirties the tree post-merge AND opens a race: if the next pass's sync lands before the chore PR merges, it pulls a backlog still marking this item `ready` and the runner re-picks it.
- Update `{{backlog_path}}`: set the item to `done (#<this PR>)` (the number is known since step 5) — **unless** a Row procedure ended the pass with the row `blocked — <reason>` or left it `ready` with recorded partial progress; keep that status then, and never mark a blocked row done. Append any discovered or declined items as new `ready` rows.
- **Mistake-retro (required, write "none" if truly clean):** "what went wrong this pass + what would have caught it earlier." For each confirmed defect *you introduced*: write a `feedback_*` memory (lives outside the repo — no commit), and if it's generalizable add it to the relevant file in `{{rules_glob}}` so the next pass's step-3 pre-flight AND the review bots both guard against it. Record other new durable learnings the same way.
- If a graphify knowledge graph exists (`graphify-out/`), run `graphify update .` (AST-only, no API cost); stage any `graphify-out/` changes too.
- Merge-tool "amend + re-push" to fold all the above (tracked files) into the PR. This re-triggers the bots once more — apply the step-8 diminishing-returns gate (a docs/bookkeeping push rarely yields a real finding; don't loop on it).

### 11. Merge decision (mode-dependent)
- Post the concise summary either way: what changed, gate result, bot-round count, browser evidence (or skip-note), declined → new-backlog items, and confirmation the bookkeeping is folded in.
- **Manual mode (default):** HALT and ask the human to approve the merge of the PR at the head SHA recorded in step 7 (name the SHA in the ask). **Never self-merge.**
- **Autonomous mode (`loop`/`--auto-merge`):** evaluate the auto-merge gate (Non-negotiables). All conditions green → note "auto-merging (loop mode)" and proceed to step 12. Any condition unmet → leave the PR open, post exactly which one failed, and STOP for a human (do not merge).

### 12. Merge & sync (on human approval, or auto in loop mode)
- `gh pr merge <n> --squash --match-head-commit <the head SHA you verified> --delete-branch`, run from the main checkout (the merge flag follows repo policy — use `--merge`/`--rebase` if the project isn't squash-only) → merge-tool "post-merge sync". A squash-merge isn't recognized by `git branch -d` as "merged", so if the local branch lingers delete it with `git branch -D <branch>` and `git remote prune origin`. The tree is already clean (bookkeeping merged atomically in step 10), so the next pass's step 0 starts fresh — in autonomous mode the command loops straight back to **step 1** for the next item (no `/loop` tick needed). When the pass ends here (manual mode), run `python3 $FOREMAN/hooks.py run-end`.

## Why this compounds
The `{{rules_glob}}` files are a growing mistake registry. Step 3 reads them (prevent), steps 7–9 catch what slipped through, steps 8 + 10 codify every real defect back into the registry. Because the rules are committed and read by the project's review bot, a captured mistake upgrades both your future implementation and the bots' future review.
