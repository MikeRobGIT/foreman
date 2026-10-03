# Parallel mode: spec before code — design

Status: draft for review, revision 3 (2026-10-02). Revision 1 drew 2 Critical, 10 Important and 4 Minor findings; revision 2 drew 7 Important and 9 Minor ones. All are addressed below. Branch `feat/spec-phase`. The companion hooks design (`feat/hooks`, piece 10 "spec gate") enforces this design's "no code before release".

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
| one review round | chain: its own per-adapter timeouts; read-only reviewer: 20 min | reviewer late: re-dispatch once, then the round doesn't count |
| pre-check bounces | 2 | park ("spec incomplete after 2 bounces") |
| rounds that don't count (a reviewer failed) | 1 | park ("review could not run") |
| W6 silence | `parallel_worker_timeout` | park ("worker stuck"); the silence clock restarts at every findings or release message |
| manual checkpoint | not selected | park ("not released by the human") |
| human-comment round | one per batch, always allowed, exempt from `spec_review_rounds` and `spec_phase_timeout` | failing it parks the batch |

## The flow

```
W2 pick + batch (batches named <YYYYMMDD>-w<n><x>)
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
5. Report with `SPEC READY <absolute worktree path> <branch> <spec path> <sha>` as the FIRST line of the final message, followed by up to five summary lines, and stop. Edit and commit nothing outside `design_spec_dir` until released.
6. **On findings:** revise as a new commit, never an amend, and report `SPEC READY` again. Each finding is answered under **Review responses**, either fixed or declined with the reason. The next round rules on every response.

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
- **Review responses:** filled in from round 2 on.

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
   - **a. The cross-model chain** runs in the background (`run_in_background`), so other batches keep moving: `cd <worktree> && $FOREMAN/review.sh --spec <spec path> <fork base> <scratch>/<batch>-spec-r<r>.log`.
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

     Both get the same prompt, without a chain log. The round passes only if neither verdict has a Critical or Important finding. The Agent tool offers only Claude models, so this is a second Claude view, not a second model family. The run report marks the batch "no cross-model review". The fix is a second repo-reading adapter in the chain (vacancy-vibe's planned chain has opencode after codex).
3. **Verdict:**
   - **Pass:** no Critical or Important finding in the merged verdict, or in either reviewer's verdict in case 2c. Before releasing a pass, the orchestrator grep-verifies the evidence for every chain P1 or P2 that the reviewer rejected. A rejection it can't confirm turns the pass into a revise.
   - **Revise:** the orchestrator spot-checks the merged findings (it doesn't re-verify every one; the reviewer already did), then sends them in one `SendMessage`, with the Minor ones listed as optional.
   - **Park:** a bound in "End states and bounds" is exceeded, or an open question the batch depends on needs a human decision.
   - **Open questions outside the batch's scope** don't block a pass. They become row notes in the final report, under the existing "do the decidable part" rule.
4. **Reviewer failures:** a read-only reviewer that fails, or isn't back in 20 minutes, is re-dispatched once. If the round still can't produce a verdict, it doesn't count, and a second such round parks the batch.

### `review.sh --spec` (new mode)

- **Arguments:** proper option parsing, `review.sh [--spec <spec path>] <base> <logfile>`. In spec mode, `FOREMAN_REVIEW_MODE=spec` and the prompt is `templates/spec-review-prompt.md`, with `{spec_path}` and `{base}` filled.
- **The base** is always the **fork base**. Every round's diff is therefore the whole current spec, not the revision delta.
- **Spec-capable adapters:** in spec mode only adapters that read the repo are used, marked by a `# foreman: reads-repo` line.
  - The plan verifies each one: codex (via `exec`), claude, opencode (plan agent), muse (if its read tools work under `--disable-shell`).
  - Others (prime-agent with `--no-tools`, and diff-in-prompt agy, hermes, openclaw and pi) exit 10, meaning fall through, in spec mode.
- **codex** in spec mode runs `codex exec` read-only with the prompt, not `codex review --base`, whose built-in prompt ignores the spec. The plan verifies the exact flags.
- **custom** (legacy `extra_reviewers`): `custom.sh` itself substitutes `{prompt_file}` and `{base}` in its command. In spec mode it exits 10 (falls through) unless the command uses both.
- **vacancy-vibe** moves from `extra_reviewers` to `reviewers: [{use: codex, …}, …]` at switch-over, before its first spec-phase wave.

### `templates/spec-review-prompt.md` (new)

- **The task:** review the design spec at `{spec_path}`, committed on top of `{base}`, for a change that has not been written yet.
- **First,** from its Rows and Change sections, list the symbols, columns, statuses and files the change touches, and `git grep` every caller and writer. Only then compare with the spec's list.
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
  1. Run `python3 $FOREMAN/hooks.py release <batch> <sha>`. The hooks' `pretool` records it, bound to this session. If `scripts/hooks.py` doesn't exist (the hooks not installed), skip this step. If it exits non-zero, don't send the message: report the failure and retry once. A released worker that the spec gate still denies would otherwise stall.
  2. `SendMessage`: "Spec approved at `<sha>` (round r). Minor findings to answer in the PR body: <list or none>. Implement it: Worker contract steps 3-8, on this same branch."
- **`--auto-merge`:** a passed batch is released at once.
- **Manual mode:**
  - Passed batches are held until every batch is passed or parked. The bounds guarantee this happens.
  - The orchestrator sends a notification ("foreman: spec checkpoint ready, N passed, M parked") and presents one table: batch, rows, spec path, rounds, each reviewer's final verdict, the remaining Minor findings, and open questions. The human picks batches to release (multi-select) and may comment on others.
  - **Selected:** released.
  - **Commented:** one comment round with the comments as findings. It's always allowed, and exempt from the round cap and the timeout. Batches that pass their comment round come back together at ONE follow-up checkpoint. A failure parks the batch.
  - **Neither:** parked ("not released by the human").
- **Parking:**
  1. Kill the round's chain and reviewer if they're running. Stop the worker (TaskStop).
  2. Copy the spec, including uncommitted edits, to `$(git rev-parse --git-common-dir)/foreman/parked/`, which outlives the session's scratch.
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
- **Deviations are judged, not just listed:**
  - **The W4.2b reviewer** rules on each deviation: acceptable, or a finding.
  - **`--auto-merge`:** its ruling stands.
  - **Manual mode:** the human sees the deviations at W4.5.
  - **A deviation that adds a file outside the outline, or changes a row's acceptance,** gets one spec round before merge.
    - The round skips W3.5's pre-checks. It reviews the spec's deviations section against the diff from the release SHA to the head.
    - A failure becomes a W4.4 finding sent back to the worker. It never parks a built PR.

## W5 and W6 additions

- **W5 bookkeeping for parked batches.** The orchestrator writes one report per parked batch: `{"pr": 0, "append": [{"id": <row>, "note": "spec parked (<reason>): <spec path> — <open findings or question>"}]}`. `<reason>` is `open question` for that case.
  - Notes are one line, with any ` | ` escaped as `\|`, and never quote a placeholder id like `IB-NEW-a`. bk.py refuses one with no matching new row.
  - The bookkeeping PR also commits the parked spec to `design_spec_dir` with a `Status: parked — <reason>` line. Then the local branch is deleted.
- **W6:**
  - rows parked during this invocation are excluded from later waves' pools.
  - **W2 rule for later invocations:**
    - a row whose note contains `spec parked (open question)` stays excluded until the human edits that note away;
    - any other `spec parked (` row is eligible, and its worker starts from the committed parked spec (step 4).
  - a worker waiting after `SPEC READY`, or awaiting release, isn't silent. Its clock restarts at each findings or release message.

## SKILL.md changes

- **Non-negotiables:** "Parallel mode: no code before the batch's spec is released."
- **W1:** a worktree whose batch was parked is removed at parking; its branch is deleted after W5 commits the spec. `SPEC READY` is not a final report.
- **W2:** batches are named `<YYYYMMDD>-w<n><x>` (e.g. `20261002-w1a`), which is unique across invocations. Add the parked-row rule.
- **W3:** the prompt gains the `foreman-worker: <batch>` tag (hooks), the absolute `templates/spec.md` path, `design_spec_dir`, and "the batch starts with the spec phase". Add the state-table columns.
- **Step 0.5 table:** add `design_spec_dir`, `spec_review_rounds` and `spec_phase_timeout` with their defaults.
- **W3.5 and W3.6:** new, as above.
- **W4:** the `SPEC READY` routing.
- **W4.2:** the spec checks; add **W4.2b** (the spec reviewer).
- **W4.5 (manual):** the human sees the deviations, with W4.2b's rulings, beside the PR.
- **W4.4:** the "new worker on the branch" fallback runs `git switch <local branch>` (the spec phase hasn't pushed) and passes the spec path.
- **W5 and W6:** as above. W6's stuck-worker text says a stuck spec-phase worker is parked.
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
| `spec_phase_timeout` | `120` | minutes from launch to a pass before a batch parks |

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
