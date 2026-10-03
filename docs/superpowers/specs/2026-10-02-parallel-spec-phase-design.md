# Parallel mode: spec before code — design

Status: draft for review, revision 4 (2026-10-02). The findings so far, all addressed below:
- revision 1: 2 Critical, 10 Important, 4 Minor;
- revision 2: 7 Important, 9 Minor;
- revision 3: 4 Important, 11 Minor. Branch `feat/spec-phase`. The companion hooks design (`feat/hooks`, piece 10 "spec gate") enforces this design's "no code before release".

## Purpose

In parallel mode a worker codes straight from its backlog rows. The Worker contract says: "Step 2: your batch is the approved scope. Write a short plan in the PR body; skip brainstorming and plan-approval gates." Every check comes after the code, so design gaps surface late, as review findings on an open PR. In wave 16, two of five PRs needed a follow-up round:
- **#653** missed a second writer of the same status, in `bulk-content-generation.ts`.
- **#655** wrote with a client that row-level security blocks for settings-only roles.

Codex found both after the PR was open.

The user's requirement: parallel mode brainstorms each batch into a spec, and the spec is reviewed until it is solid before any work begins.

Success means:
- no worker edits or commits a non-spec file before its batch's spec is released. Hooks piece 10 enforces this, and W3.5 checks it from the commit list.
- a released spec has passed a round, as defined below, with no Critical or Important finding.
- every batch reaches an end state, released or parked, within a bounded time.
- `--auto-merge` runs never wait on the human for specs.
- manual runs get one checkpoint per wave, announced with a notification.
- serial mode is unchanged.
- **The measure** is follow-up rounds per PR after review, against wave 16's 2 of 5.

## Decisions (from brainstorming, 2026-10-02)

- **Sign-off:** in `--auto-merge`, the review loop's pass is the approval. In manual mode, the human also approves each passed spec, at one checkpoint per wave.
- **Author:** the worker, in its own worktree, before coding.
- **Review:** two reviewers per round, the orchestrator's read-only reviewer on the strongest model and the cross-model reviewer chain. "Solid" means a round with no Critical or Important finding. After `spec_review_rounds` (3) rounds without one, the batch is parked.
- **Home:** the spec is the batch branch's first commit, so it is part of its PR.
- **Plan:** an "Implementation outline" section inside the spec, reviewed in the same loop. No `writing-plans`.
- **Flow:** pipelined. Each batch goes spec → review → build on its own. Manual mode holds passed specs for the one checkpoint.

## Non-goals

- Serial mode keeps step 2 (brainstorming then writing-plans, with the human) as it is.
- W2's batching is unchanged.
- Enforcement of "no code before release" is hooks piece 10. Without the hooks, W3.5's commit-list check is the only guard.

## End states and bounds

A batch in the spec phase ends **released** or **parked**, never anything else.

| bound | default | when exceeded |
|---|---|---|
| `spec_review_rounds` | 3 | rounds without a pass: park |
| `spec_phase_timeout` | 60 min | **worker time** in the spec phase: launch to the first `SPEC READY`, plus each revision from findings to the next `SPEC READY`. Review time doesn't count. Exceeded: park ("spec phase timed out") |
| one review round | chain: its own per-adapter timeouts (`review.sh` exit 2, a hard error, counts as exhausted); read-only reviewer: 20 min | reviewer late: re-dispatch once, then the round doesn't count |
| pre-check bounces | 2 | park ("spec incomplete after 2 bounces") |
| rounds that don't count (a reviewer failed) | 1 | park ("review could not run") |
| W6 silence | `parallel_worker_timeout` | park ("worker stuck"); the silence clock restarts at every findings or release message |
| manual checkpoint | not selected | park ("not released by the human") |
| human-comment round | one per batch, always allowed, exempt from `spec_review_rounds` and `spec_phase_timeout` | failing it parks the batch |

**The clock.** Only notifications wake the orchestrator, so the bounds are driven by timers.
- **Timestamps:** every message that returns a worker to work stamps `sent_i` in the state table, as epoch seconds from `date +%s`. That covers launch (`sent_0`), findings, a pre-check bounce, a resume after a stop without `SPEC READY`, and a comment round. Every `SPEC READY` stamps `spec_ready_i`, and a stop without one stamps `stopped_i`.
- **Worker time** is the sum of each `spec_ready_i − sent_i`, or `stopped_i − sent_i`, plus `now − last sent_i` while the worker is working. Orchestrator latency between a stop and a resume is therefore never counted.
- **Timers:** for every return to work, the orchestrator starts a background `sleep <remaining worker time>` (Bash `run_in_background`). It starts one per running reviewer too (20 minutes), and one per comment round (`parallel_worker_timeout`).
  - It records each timer's task id. It stops the superseded timer (TaskStop) on disarm, which happens at `SPEC READY`, and on every re-arm. It also stops a superseded reviewer on re-dispatch, and ignores any late verdict from it.
  - On every wake it recomputes every bound for every batch from the timestamps. A stray wake is harmless.
- **Session limits:** in sessions that cap background commands (`-p`, SDK and cloud sessions, which default to 30 min and allow at most 2 h), every timer and chain is started with a `timeout` up to that cap.
  - A timer stopped at the cap counts as a wake, and a chain stopped at the cap counts as exhausted.
  - At the subagent concurrency limit ("Concurrent subagent limit reached"), a reviewer dispatch is queued and retried at the next wake, not counted as a failure.
- **"The bounds guarantee this"** in W3.6 rests on these timers.

## The flow

```
W2 pick + batch (batches named <YYYYMMDD-HHMM>-w<n><x>)
W3 launch workers (prompt: tag, rows, owned files, prefix, plugin path, template path, design_spec_dir)
   worker: SPEC PHASE ─► commit spec ─► "SPEC READY <abs worktree> <branch> <path> <sha>" ─► idle
W3.5 spec review (per batch, as it arrives)
   pre-check (bounded bounces) ─► round r on the pinned SHA (everything in the background):
      1. review.sh --spec (cross-model chain; spec-capable adapters only)
      2. read-only reviewer: own review + rules on the chain's findings + rules on round r-1's findings and responses
         (chain exhausted: two read-only reviewers in parallel, opus + sonnet)
      ─► one merged verdict ─► orchestrator verifies rejected chain P1/P2 before any pass
   pass ─► PASSED │ Critical/Important ─► findings to worker ─► revise ─► SPEC READY ─► round r+1
   bounds exceeded ─► PARKED
W3.6 release
   --auto-merge: PASSED ─► hooks.py release <batch> <sha> ─► "approved at <sha>; Minor: …; implement"
   manual: when every batch is PASSED or PARKED ─► notify ─► checkpoint ─► released / comment round / parked
           (comment-round batches return together in one follow-up checkpoint)
worker: BUILD PHASE ─► final report
W4 … (+ spec checks) … W5 (+ parked bookkeeping) … W6 (+ parked exclusion)
```

## Worker: spec phase

This replaces Worker contract Step 2.

1. Set up the worktree as today (`worktree_setup`). Read the batch's rows, the `rules_glob` files for the area, and recent feedback memories.
2. Explore before designing:
   - read the code the rows name;
   - `git grep` every caller and every writer of each function, column, status or file the change touches;
   - for each write, find which client or credentials perform it, which roles or permissions reach that path, and why row-level security or permissions allow it;
   - note which rules entries apply.
3. Invoke `superpowers:brainstorming` for its exploration and design discipline, with these overrides. They win over the skill's own text:
   - always write the spec file from the template, whatever path the skill would classify the batch as. A "bounded" batch still gets a spec.
   - no visual companion, no per-section approval, and no questions to the user. A question the code can't answer goes under **Open questions**.
   - the orchestrator's release is the skill's HARD-GATE approval. Never end a turn with a question; end the spec phase with `SPEC READY`.
   - don't invoke `writing-plans`; write the Implementation outline section.
4. Write the spec from the template, `<plugin>/templates/spec.md` (absolute path in the dispatch prompt). It goes to `<design_spec_dir>/<YYYY-MM-DD>-<row ids joined by ->-design.md`.
   - Commit it with `git add -- <spec path> && git commit`, never `git add -A`, as the branch's first commit. Don't push.
   - The build phase continues on this same branch; it creates no new one.
   - If a parked spec for these rows already exists in `design_spec_dir`, start from it: revise it rather than writing a new one.
5. Report with `SPEC READY <absolute worktree path> <branch> <spec path> <sha>` as the FIRST line of the final message, followed by up to five summary lines, and stop. The orchestrator and the hooks use one rule: the first line of the last message that starts with `SPEC READY `. A preface line before it is tolerated, not penalised. Edit and commit nothing outside `design_spec_dir` until released.
6. **A stop without `SPEC READY`** (a question, a context limit, an error) is resumed once by the orchestrator: "finish the spec; questions go under Open questions; end with `SPEC READY` as the first line". It counts as a pre-check bounce. A second such stop parks the batch ("spec incomplete"). The hooks' report check (piece 7) asks a not-yet-released spec-phase worker for `SPEC READY`, not for a json report.
7. **On findings:** revise as a new commit, never an amend, and report `SPEC READY` again. Each finding is answered under **Review responses**, either fixed or declined with the reason. The next round rules on every response.

### `templates/spec.md` (new)

Sections, in order:
- **Rows:** id, title and acceptance, quoted.
- **Current behaviour:** what happens today, with `path:line` evidence.
- **Change:** what will be different, per row.
- **Callers and writers checked:** for each symbol, column, status or file touched:
  - every site found, and why each one is or isn't affected;
  - for each write, the client or credentials, the roles that reach it, and why RLS or permissions allow it.
- **Rules that apply:** rules-file entries quoted with file:line, and how the change honours each.
- **Tests:** the failing tests to write first, each with the behaviour it pins. Mutants the tests must kill, where useful.
- **Implementation outline:** the files in order, and what changes in each.
  - It must stay inside the batch's owned files plus its own entries in `parallel_shared_files`.
  - A file outside that set is listed as a **scope request**.
- **Surface:** app surface yes or no. If yes, the planned browser check's claims and its control on main.
- **Risks:** what could go wrong, and how the change or the tests cover it.
- **Open questions:** product decisions the code can't answer. Write "None" when there are none.
- **Review responses:** the template's text is "None — round 1"; filled in from round 2 on.

A worker reusing a parked spec (step 4) deletes its `Status: parked` line and its old Review responses first.

A section may read "None — <reason>". The template marks every blank with `⟦FILL: …⟧`, and the pre-check greps only for `⟦FILL`. Code samples with `<T>` or `TODO` never trigger it.

## Orchestrator: spec review (new W3.5)

Each `SPEC READY` is handled as it arrives, independently of other batches. The state table gains these columns: spec path, worktree, branch, **fork base** (`git merge-base <sha> origin/<default>`, recorded at the first `SPEC READY`), round, pinned SHA, released SHA, and status (`spec`, `review r`, `passed`, `parked: <reason>`, `released`, `PR #n`).

1. **Pre-checks.** They don't spend a round, but bounces are capped at 2.
   - The worktree and commit exist.
   - The spec file is at the stated path, has every template section, and contains no `⟦FILL` marker.
   - `git diff --name-only <fork base> <sha>` touches only `design_spec_dir`.
   - Any scope request is answered:
     - extend the owned files when no other batch owns the file;
     - otherwise re-batch, or park ("scope conflict").
2. **One round is pinned to one SHA.** The worker is not resumed while a round runs, and results for an older SHA are discarded.
   - **a. The cross-model chain** runs in the background (`run_in_background`), so other batches keep moving: `( cd <worktree> && $FOREMAN/review.sh --spec <spec path> <fork base> <scratch>/<batch>-spec-r<r>.log )`. The subshell keeps the orchestrator's own working directory unchanged. Hooks piece 8 allows one paren wrap.
   - **b. When the chain exits** (landed or exhausted), dispatch the **read-only reviewer** in the background: `subagent_type: Explore`, `model: opus`, a tool-restricted type. It gets:
     - the spec, the rows, the `rules_glob` files and the worktree path;
     - the chain's log, if one landed;
     - round r-1's Critical and Important findings and the worker's responses.

     Its job:
     1. **Before reading the spec's list,** derive its own list of symbols, columns, statuses and writers from Rows and Change. Then `git grep` them, and diff its list against the spec's "Callers and writers checked". That is what would have caught #653.
     2. Verify the spec's other claims against the code, at the cited `path:line`.
     3. Check the outline against the owned files, the tests against the change, and the open questions.
     4. Rule on each chain finding (confirmed, or rejected with evidence) and on each previous finding's response (resolved or not).
     5. Return ONE merged verdict: Critical, Important and Minor findings with section, evidence and fix, plus `Verdict: pass | revise`.
   - **c. When the chain is exhausted** (exit 1), two read-only reviewers run in parallel:
     - the opus one from (b);
     - a second, `subagent_type: Explore` with `model: sonnet`.

     Both get the same prompt, without a chain log. The round passes only if neither verdict has a Critical or Important finding. The two verdicts are combined into one findings list, with duplicates removed by section and evidence. That list is what goes to the worker and what round r+1 rules on. The Agent tool offers only Claude models, so this is a second Claude view, not a second model family. The run report marks the batch "no cross-model review". The fix is a second repo-reading adapter in the chain (vacancy-vibe's planned chain has opencode after codex).
3. **Verdict:**
   - **Pass:** no Critical or Important finding in the merged verdict, or in either reviewer's verdict in case 2c. Before releasing a pass, the orchestrator grep-verifies the evidence for every chain P1 or P2 that the reviewer rejected. A rejection it can't confirm turns the pass into a revise.
   - **Revise:** the orchestrator spot-checks the merged findings (it doesn't re-verify every one; the reviewer already did), then sends them in one `SendMessage`, with the Minor ones listed as optional.
   - **Park:** a bound in "End states and bounds" is exceeded, or an open question the batch depends on needs a human decision.
   - **Open questions outside the batch's scope** don't block a pass. They become row notes in the final report, under the existing "do the decidable part" rule.
4. **Reviewer failures:** a read-only reviewer that fails, or isn't back in 20 minutes, is re-dispatched once. If the round still can't produce a verdict, it doesn't count, and a second such round parks the batch.

### `review.sh --spec` (new mode)

- **Arguments:** proper option parsing, `review.sh [--spec <spec path>] <base> <logfile>`. In spec mode, `FOREMAN_REVIEW_MODE=spec` and the prompt is `templates/spec-review-prompt.md`, with `{spec_path}` and `{base}` filled.
- **The base** is always the **fork base**. Every round's diff is therefore the whole current spec, not the revision delta.
- **Killing the chain:** `review.sh` gains `trap '[ -n "${pid:-}" ] && kill -TERM -- "-$pid" 2>/dev/null; exit 143' TERM INT`. Killing the chain at parking then stops the running adapter's process group, and the chain exits instead of moving on to the next adapter.
- **Spec-capable adapters:** in spec mode only adapters that read the repo are used, marked by a `# foreman: reads-repo` line.
  - The plan verifies each one: codex (via `exec`), claude, opencode (plan agent), muse (if its read tools work under `--disable-shell`).
  - Others (prime-agent with `--no-tools`, and diff-in-prompt agy, hermes, openclaw and pi) exit 10, meaning fall through, in spec mode.
- **codex** in spec mode runs `codex exec` read-only with the prompt, not `codex review --base`, whose built-in prompt ignores the spec. The plan verifies the exact flags.
- **custom** (legacy `extra_reviewers`): `custom.sh` itself substitutes `{prompt_file}` and `{base}` in its command. In spec mode it exits 10 (falls through) unless the command uses both.
- **vacancy-vibe** moves from `extra_reviewers` to `reviewers: [{use: codex, …}, …]` at switch-over, before its first spec-phase wave.

### `templates/spec-review-prompt.md` (new)

- **The task:** review the design spec at `{spec_path}`, committed on top of `{base}`, for a change that has not been written yet.
- **First,** from its Rows and Change sections, list the symbols, columns, statuses and files the change touches, and find every caller and writer (`git grep`, or your Grep tool). Only then compare with the spec's list.
- **What to find:**
  - missed callers or writers, including writes whose client or role would be refused;
  - wrong claims about current behaviour;
  - a change that breaks a listed rule;
  - tests that can't fail, or that miss the change;
  - an outline step that doesn't achieve its row's acceptance;
  - unhandled failure paths;
  - scope outside the stated files.
- **Grades:** P1 means the design is wrong or unsafe as written; P2 means a likely defect or a gap to close before coding; P3 is minor.
- **Last line:** `Verdict: <ready to build | revise | redesign>`.

## Orchestrator: release (new W3.6)

- **Release** has two steps:
  1. Run `python3 $FOREMAN/hooks.py release <batch> <sha>`. The hooks' `pretool` records it, bound to this session. The script exits 1 unless the release file now exists (it checks both state dirs). It exits 3 ("hooks inactive") when no run marker exists in either state dir, which happens when hooks don't fire (`disableAllHooks`, a managed-hooks policy, a load error); then the message is sent, because nothing will gate the worker. On exit 1, don't send the message: retry once. If the retry also fails, park the batch ("release could not be recorded"), unless `spec_gate` is in `hooks_disabled`. A released worker that the spec gate still denies would otherwise stall.
  2. `SendMessage`: "Spec approved at `<sha>` (round r). Minor findings to answer in the PR body: <list or none>. Implement it: Worker contract steps 3-8, on this same branch."
- **`--auto-merge`:** a passed batch is released at once.
- **Manual mode:**
  - Passed batches are held until every batch is passed or parked. The bounds guarantee this happens.
  - The orchestrator sends a notification ("foreman: spec checkpoint ready, N passed, M parked") and presents one table: batch, rows, spec path, rounds, each reviewer's final verdict, the remaining Minor findings, and open questions. The human picks batches to release (multi-select) and may comment on others.
  - **Selected:** released.
  - **Commented:** one comment round with the comments as findings. It's always allowed, and exempt from the round cap and the timeout. Batches that pass their comment round come back together at ONE follow-up checkpoint. A failure parks the batch.
  - **Neither:** parked ("not released by the human").
- **Parking:**
  1. Kill the round's chain and reviewer if they're running. Run `python3 $FOREMAN/hooks.py stopping <batch>`, if the hooks are installed, so the hooks' report check lets the stop through. Then stop the worker (TaskStop).
  2. Copy every spec path to `$(git rev-parse --git-common-dir)/foreman/parked/<batch>/`, which outlives the session's scratch. Copy committed and uncommitted ones alike, including a park before the first `SPEC READY`, when the state table has no spec path yet:
     - `git -C <wt> status --porcelain --untracked-files=all -- <design_spec_dir>`;
     - `git -C <wt> diff --name-only <fork base, or $(git -C <wt> merge-base origin/<default> HEAD)> HEAD -- <design_spec_dir>`.

     W5 uses these copies, or writes "no spec" in the row note when there are none.
  3. Name the branch and SHA in the run report.
  4. `git worktree remove --force`. This is a stated exception to W1's no-`--force` rule: the spec is already copied, and nothing else is pending. The local branch keeps the commit until W5.

  A spec-phase worker that W6 finds stuck is parked this way, not left alone.
  The rows stay `ready`.

## Worker: build phase

This is the Worker contract's current steps 3–8, plus:
- **The spec body is frozen at release.** Any change after release goes only in an appended section, `## Deviations after release`, one entry per deviation with its reason. The PR body lists them too.
- **First commit:** the PR's first commit is the spec.
- **Minor findings** from the release message are answered in the PR body.

## Orchestrator: W4 additions

- **W4 routing:** a `SPEC READY` goes to W3.5, never to W4.
- **W4.2, the diff audit:**
  - the spec file, up to its `## Deviations after release` heading, is byte-identical to the released SHA's copy;
  - touched files are inside the outline, or are test files the spec's Tests section names, or are files the report lists as changed by the gate (formatters), or are covered by a deviation;
  - the batch's spec file is allowed in the diff.
- **New W4.2b, spec reviewer:** the orchestrator dispatches a read-only reviewer (`Explore`, opus). SKILL.md has none in W4 today. It gets the spec, the PR diff and the deviations. It checks the code against the spec, reports unlisted deviations, and rules on each listed one.
  - **Gate-changed files:** it confirms that each file the report lists as gate-changed has formatting-only hunks and is owned by no other batch. Anything else is an unlisted deviation.
  - **Its findings and rulings are part of the auto-merge gate:** a Critical or Important finding, or a deviation ruled unacceptable, blocks auto-merge like any other review finding.
  - **If it fails or times out (20 min),** it's re-dispatched once. If it fails again, that PR isn't auto-merged: it stays open with a note. The wave continues with its other PRs, and the run report lists it for the human.
  - **It runs in the background** with a reviewer timer (the clock). It reviews the SHA W4.1 verified. If the head moves, it runs again on the new SHA, and W4.6 merges only a SHA whose W4.2b verdict is clean.
  - **Order:** W4.2b's ruling comes first. The deviation spec round (below) runs only for a deviation that adds a file or changes acceptance, and only after W4.2b hasn't ruled it unacceptable. An unacceptable ruling goes straight to the worker as a W4.4 finding.
- **Deviations are judged, not just listed:**
  - **The W4.2b reviewer** rules on each deviation: acceptable, or a finding.
  - **`--auto-merge`:** its ruling stands.
  - **Manual mode:** the human sees the deviations at W4.5.
  - **A deviation that adds a file outside the outline, or changes a row's acceptance,** gets one spec round before merge.
    - The round skips W3.5's pre-checks. It reviews the spec's deviations section against `git diff $(git merge-base origin/<default> <head>) <head>`, minus the spec file. That excludes main's changes merged in at contract step 8.
    - A failure becomes a W4.4 finding sent back to the worker. It never parks a built PR.

## W5 and W6 additions

- **W5 bookkeeping for parked batches.** The orchestrator writes one report per parked batch: `{"pr": 0, "append": [{"id": <row>, "note": "spec parked (<reason>): <spec path> — <open findings or question>"}]}`. `<reason>` is `open question` for that case.
  - Notes are one line, with any ` | ` escaped as `\|`, and never quote a placeholder id like `IB-NEW-a`. bk.py refuses one with no matching new row.
  - The bookkeeping PR also commits the parked spec to `design_spec_dir` with a `Status: parked — <reason>` line. Then the local branch is deleted, along with `foreman/parked/<batch>/` and the hooks' `batches/<session_id>/` entries for this wave (`hooks.py` state).
- **W6:**
  - rows parked during this invocation are excluded from later waves' pools.
  - **W2 rule for later invocations:**
    - a row whose note contains `spec parked (open question)` or `spec parked (not released by the human)`, or two `spec parked (` notes, stays excluded until the human edits the note away;
    - any other `spec parked (` row is eligible once more, and its worker starts from the committed parked spec (step 4).
  - a worker waiting after `SPEC READY`, or awaiting release, isn't silent. Its clock restarts at each findings or release message.

## SKILL.md changes

- **Non-negotiables:** "Parallel mode: no code before the batch's spec is released."
- **W1:** a worktree whose batch was parked is removed at parking; its branch is deleted after W5 commits the spec. `SPEC READY` is not a final report.
- **W2:** batches are named `<YYYYMMDD-HHMM>-w<n><x>`, where the time is the invocation's start (e.g. `20261002-1530-w1a`). That is unique across invocations, including repeated `/loop` ticks in one session. Add the parked-row rule.
- **W3:** the prompt begins with `foreman-worker: <batch> spec-phase`. The ` spec-phase` suffix opts the batch into the hooks' spec gate (piece 10) and the spec-phase report check (piece 7). The prompt also gains the absolute `templates/spec.md` path, `design_spec_dir`, and "the batch starts with the spec phase". Add the state-table columns.
- **Step 0.5 table:** add `design_spec_dir`, `spec_review_rounds` and `spec_phase_timeout` with their defaults.
- **W3.5 and W3.6:** new, as above.
- **W4:** the `SPEC READY` routing.
- **W4.2:** the spec checks; add **W4.2b** (the spec reviewer).
- **W4.5 (manual):** the human sees the deviations, with W4.2b's rulings, beside the PR.
- **Non-negotiables, auto-merge conditions:** add "a W4.2b verdict on the merge SHA with no Critical or Important finding and no deviation ruled unacceptable".
- **W4.4:** the "new worker on the branch" fallback runs `git switch <local branch>` (the spec phase hasn't pushed) and passes the spec path. Its tag is `foreman-worker: <batch>`, without ` spec-phase`, because the spec is already released.
- **Reviewer model:** W3.5 and W4.2b pass `model: opus` to `Explore`. A project that defines its own `Explore` agent keeps that agent's model, so the plan verifies which model actually ran and records it in the run report.
- **TaskStop of a tagged worker** (parking, W6's stuck worker, W4.4's replacement) is preceded by `hooks.py stopping <batch>`.
- **W5 and W6:** as above. W6's stuck-worker text says a stuck spec-phase worker is parked.
- **W1:** state the parking exception to "never `--force`" (W3.6 step 4).
- **Worker contract step 5** (`git switch -c` then `git add -A`): in parallel mode, the build phase stays on the spec branch, which already exists. It commits with explicit paths, or `git add -A` only after the spec phase.
- **W3.5 scope grants:** the orchestrator tells the worker in the next message: "owned files now also include <file>".
- **Notification:** use the session's push-notification tool if one exists, otherwise a plain message. The checkpoint question uses multi-select. With more than 4 batches it's split into several questions, or the human answers in text.
- **Comments at the checkpoint** come as free text, on the question's notes or in a reply. The follow-up checkpoint offers only release or park, with no second comment round.
- **Worker contract:**
  - **Step 2** becomes the spec phase, as in "Worker: spec phase".
  - **"Own only your files"** also allows "and your batch's spec file".
  - **"Stop pushing once you report"** applies to the final report; `SPEC READY` is not one.
  - **The report** names the spec path and its deviations in prose. The json block may carry `"spec": "<path>"`, which `bk.py` ignores.
  - **Remove** "skip brainstorming and plan-approval gates".

## Config (new keys)

| key | default | meaning |
|---|---|---|
| `design_spec_dir` | `docs/superpowers/specs` | where batch specs are committed. Distinct from `e2e.spec_dir` |
| `spec_review_rounds` | `3` | rounds without a pass before a batch parks |
| `spec_phase_timeout` | `60` | minutes of **worker time** in the spec phase before a batch parks (see End states and bounds) |

`templates/foreman.config.md` documents them. The prompt placeholder is `{spec_path}`, which avoids `e2e.run`'s `{spec}`.

## Cost and visibility

A five-batch wave adds about 1.5–2 rounds per batch before code. Each round is one chain run plus one or two read-only reviewers.
- **Context:** the orchestrator keeps only the merged verdicts, so its context grows by about one short report per round.
- **Run report:** the run report gains a spec-phase table. Per batch it records the spec wall-clock, rounds, which reviewer landed or the fall-through, parks and reasons, and the post-PR review rounds. That is the success measure, kept visible.

## Testing

- **`tests/run.sh`:**
  - `review.sh --spec` argument parsing;
  - a fake codex receives `exec` with the spec prompt (like the existing muse argument test);
  - `custom` falls through in spec mode without the placeholders, and runs with them;
  - a non-reads-repo adapter falls through;
  - the round-2 base is the fork base, so the prompt names the whole spec (checked through a fake reads-repo adapter's received prompt);
  - `custom.sh` substitutes `{prompt_file}` and `{base}`;
  - a TERM to `review.sh` mid-adapter kills the adapter's process group and exits 143, without starting the next adapter;
  - the template's `⟦FILL` markers and a code sample with `<T>` (only the markers are flagged);
  - `bk.py` accepts a `pr: 0` append-only report;
  - `templates/spec.md` and `spec-review-prompt.md` contain every named section;
  - a SKILL.md text check: "skip brainstorming" is gone, and `SPEC READY`, W3.5 and W3.6 are present.
- **Dry runs** (manual, documented) in a throwaway repo with a small backlog:
  - manual mode reaching the checkpoint;
  - `--auto-merge` releasing without a checkpoint;
  - a deliberately ambiguous row parking at the round cap;
  - a worker killed mid-spec parking at the timeout;
  - a comment round;
  - a release whose spec commit is the PR's first commit.
- **The hooks repo** adds a `SPEC READY` stop case to `test_hooks.py`.
- **The first real use** is one vacancy-vibe wave with `parallel 2`, after the reviewer migration. Its run report gives the measure.

## Version

0.5.0. It ships after hooks 0.4.0, or the numbers swap at merge.
