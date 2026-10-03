# Parallel mode: spec before code — design

Status: draft for review, 2026-10-02. Branch `feat/spec-phase`.

## Purpose

In parallel mode a worker codes straight from its backlog rows. The Worker contract says: "Step 2: your batch is the approved scope. Write a short plan in the PR body; skip brainstorming and plan-approval gates." Every check comes after the code. Design gaps therefore surface late, as review findings on an open PR. In wave 16, two of five PRs needed a follow-up round:
- **#653** missed a second route to the same failed mark, in `bulk-content-generation.ts`.
- **#655** wrote with a client that row-level security blocks for settings-only roles.

Codex found both after the PR was open.

The user's requirement: parallel mode brainstorms each batch into a spec, and the spec is reviewed until it is solid before any work begins.

Success means:
- no worker edits a non-spec file before its batch's spec is released;
- a released spec has passed a review round with no Critical or Important finding;
- `--auto-merge` runs never wait on the human for specs;
- manual runs get one checkpoint per wave where the human approves the passed specs;
- serial mode is unchanged.

## Decisions (from brainstorming, 2026-10-02)

- **Sign-off:** in `--auto-merge`, the review loop's pass is the approval. In manual mode, the human also approves each passed spec, at one checkpoint per wave.
- **Author:** the worker, in its own worktree, before coding.
- **Review:** each round runs two reviewers, the orchestrator's read-only reviewer on the strongest model and the cross-model reviewer chain. "Solid" means a round with no Critical or Important finding. After `spec_review_rounds` (3) rounds without one, the batch is parked.
- **Home:** the spec is committed as the first commit of the batch's branch, so it is part of its PR.
- **Plan:** an "Implementation outline" section inside the spec, reviewed in the same loop. Workers don't run `writing-plans`.
- **Flow:** pipelined. Each batch goes spec → review → build on its own. Manual mode holds passed specs for the one checkpoint.

## Non-goals

- Serial mode keeps step 2 (brainstorming then writing-plans, with the human) as it is.
- No enforcement by hooks here. A "no code before release" hook is a candidate for the hooks design (see Interactions).
- W2's batching is unchanged.

## The flow

```
W2 pick + batch (unchanged)
W3 launch workers ──► worker: SPEC PHASE ──► "SPEC READY <path> <sha>", idle
                                                    │
W3.5 spec review (orchestrator, per spec, as it arrives)
   round r: read-only reviewer ∥ review.sh --spec   (r ≤ spec_review_rounds)
     Critical/Important? ── yes ─► SendMessage findings ─► worker revises (new commit) ─► "SPEC READY" ─► next round
                         └─ no  ─► PASSED
     r = cap without a pass ─► PARKED
W3.6 release
   --auto-merge: PASSED ─► SendMessage "spec approved at <sha>: implement" (at once)
   manual: hold PASSED until every batch is PASSED or PARKED ─► one checkpoint with the human
           ─► approved ─► released; comments ─► one revision round ─► re-presented
worker: BUILD PHASE (today's steps 3-8, against the spec) ─► final report
W4 (unchanged, plus the spec checks below) … W5 … W6
```

## Worker: spec phase

This replaces Worker contract Step 2.

1. Set up the worktree as today (`worktree_setup`). Read the batch's rows, the `rules_glob` files for the area, and recent feedback memories.
2. Explore before designing:
   - read the code the rows name;
   - `git grep` every caller and every writer of each function, column, status or file the change touches;
   - note which rules entries apply.
3. Invoke `superpowers:brainstorming` for its exploration and design discipline, with these unattended overrides:
   - no visual companion;
   - no questions to the user: a question the code can't answer is written under **Open questions**;
   - the orchestrator's review loop and release replace the "user reviews written spec" gate;
   - don't invoke `writing-plans`; write the Implementation outline section instead.
4. Write the spec from `templates/spec.md` to `<spec_dir>/<YYYY-MM-DD>-<batch>-design.md` and commit it as the branch's first commit. Don't push and don't open a PR yet.
5. Report `SPEC READY <path> <sha>` (one line, plus up to five lines of summary) and stop. Edit no other file until the orchestrator releases the spec.
6. **On findings:** revise the spec as a new commit, never an amend, and report `SPEC READY` again. A finding the worker declines is answered in the spec under **Review responses**, with the reason. The next round's reviewers judge whether the reason holds.

### `templates/spec.md` (new)

The sections, in order:
- **Rows:** id, title and acceptance, quoted.
- **Current behaviour:** what happens today, with `path:line` evidence.
- **Change:** what will be different, per row.
- **Callers and writers checked:** each symbol or column touched, every site found, and why each one is or isn't affected.
- **Rules that apply:** rules-file entries quoted with file:line, and how the change honours each.
- **Tests:** the failing tests to write first, each with the behaviour it pins. Mutants the tests must kill, where useful.
- **Implementation outline:** the files in order, and what changes in each.
  - It must stay inside the batch's owned files and its own entries in `parallel_shared_files`.
  - A file outside that set is listed as a **scope request** and blocks the pass until the orchestrator rules.
- **Surface:** app surface yes or no. If yes, the planned browser check's claims and its control on main.
- **Risks:** what could go wrong, and how the change or the tests cover it.
- **Open questions:** product decisions the code can't answer. Write "None" when there are none.
- **Review responses:** filled in from round 2 on.

## Orchestrator: spec review (new W3.5)

Each `SPEC READY` is handled as it arrives, independently of other batches.

1. **Pre-checks**, which fail fast without spending a review round:
   - the commit exists in the worker's worktree;
   - the file is at the stated path, every template section is present, and no placeholder text is left;
   - any scope request is answered:
     - extend the batch's owned files when no other batch owns the file;
     - otherwise re-batch, or park.
   A pre-check failure goes straight back to the worker and does not count as a round.
2. **One round** runs these two in parallel:
   - **A read-only reviewer** on the strongest model (`subagent_type: Explore`, `model: opus`; a tool-restricted type, per the existing rule). It gets the spec path, the rows, the `rules_glob` files and the worktree path. It checks:
     - the claims against the code (it reads the cited `path:line`);
     - the callers-and-writers list for completeness (it `git grep`s the same symbols);
     - the outline against the owned files;
     - the tests against the change;
     - the open questions.
     It reports findings as Critical, Important or Minor, each with the spec section, the evidence and a fix.
   - **The cross-model chain:** `(cd <worktree> && $FOREMAN/review.sh --spec <spec path> <spec commit>^ <scratch>/<batch>-spec-r<r>.log)`, in the background. It uses the new `templates/spec-review-prompt.md` (below). The same P1/P2/P3 grades map to Critical/Important/Minor.
3. **Verdict:**
   - **Passed:** neither review reports a Critical or Important finding.
   - **Revise:** the orchestrator triages the findings as it does PR findings: it grep-verifies each claim, drops fabricated rules, and doesn't treat Minor items as blocking. It sends the remaining Critical/Important ones to the worker in one `SendMessage`, with the Minor ones listed as optional.
   - **Parked:** after `spec_review_rounds` rounds with no pass, or at once when an open question the batch depends on needs a human decision.
   - **Open questions outside the batch's scope** don't block a pass. The spec does the decidable part, under the existing "do the decidable part and report the rest" rule, and the question becomes a row note in the worker's final report.
4. **Chain exhausted:** if the chain runs out of reviewers (every adapter fell through), the round counts with the read-only reviewer alone, and the release message says no cross-model review ran.

### `review.sh --spec` (new mode)

- `review.sh --spec <spec path> <base> <logfile>`:
  - sets `FOREMAN_REVIEW_MODE=spec`;
  - fills `{spec}` and `{base}` in `templates/spec-review-prompt.md`;
  - walks the same chain, with the same fall-through rules.
- Adapters that take a prompt use the spec-review prompt as they use the PR prompt today.
- The `codex` adapter, in spec mode, runs `codex exec` read-only with the prompt instead of `codex review --base`, because `review` uses its built-in code-review prompt. The plan verifies the exact `exec` flags against the installed CLI.

### `templates/spec-review-prompt.md` (new)

- **The task:** review the design spec at `{spec}`, committed on top of `{base}`, for a change that has not been written yet.
- **The reviewer reads the repo** to check the spec's claims: the current behaviour and its `path:line` evidence, and the callers and writers it lists. It looks for any it missed.
- **What to find:**
  - missed callers or writers;
  - wrong claims about current behaviour;
  - a change that breaks a listed rule;
  - tests that can't fail, or that miss the change;
  - an outline step that doesn't achieve its row's acceptance;
  - unhandled failure paths;
  - scope outside the stated files.
- **Grades:** P1 means the design is wrong or unsafe as written; P2 means a likely defect or a gap that must be closed before coding; P3 is minor.
- **Last line:** `Verdict: <ready to build | revise | redesign>`.

## Orchestrator: release (new W3.6)

- **`--auto-merge`:** a passed spec is released at once: `SendMessage` "Spec approved at `<sha>` (round r). Implement it: Worker contract steps 3-8."
- **Manual mode:**
  - Passed specs are held. When every batch of the wave has passed or parked, the orchestrator presents one checkpoint. It shows a table (batch, rows, spec path, rounds, each reviewer's final verdict, any Minor findings left, open questions) and asks which batches to release (multi-select).
  - Released batches get the same message.
  - A batch the human comments on gets one revision round with those comments as findings. After it passes, that batch alone is presented again.
- **Parked batches** in either mode:
  - the worker is stopped (TaskStop);
  - the spec file is copied to `<scratch>/parked/`;
  - the batch's rows stay `ready`.
  At W5 the bookkeeping PR commits each parked spec to `<spec_dir>/` with a `Status: parked — <reason>` line, and appends to each row: "spec parked (`<spec path>`): <the open findings, or the open question>". A later run starts from that spec.
- **Timeout:** a worker waiting after `SPEC READY` is not counted against `parallel_worker_timeout`. It has reported.

## Worker: build phase

This is the Worker contract's current steps 3–8, plus:
- **Spec as contract:** build what the released spec says. A necessary departure goes under **Spec deviations** in the PR body, with the reason. The spec file is not edited after release.
- **First commit:** the PR's first commit is the spec. A commit that changes the spec after release shows up in W4's audit.

## Orchestrator: W4 additions

- **W4.2, the diff audit,** also checks:
  - the spec file at the PR head is byte-identical to the released SHA's copy, unless the PR body lists a deviation that explains the change;
  - the touched files are inside the outline's files, or are listed as deviations.
- **The read-only reviewer** in W4 gets the spec. It checks the code against the spec and reports unlisted deviations.

## SKILL.md changes

- **W3:** the worker prompt says the batch starts with the spec phase. Remove "skip brainstorming and plan-approval gates".
- **Add W3.5 and W3.6** as above.
- **W4.2:** add the spec checks. **W4.5** in manual mode is unchanged: the human still approves merges. The spec checkpoint is an extra, earlier gate.
- **W5:** the bookkeeping PR commits parked specs and adds their row notes.
- **W6:** a waiting worker is not stuck.
- **Worker contract:**
  - Step 2 becomes the spec phase;
  - a "Spec as contract" line;
  - the report lists the spec path and any deviations.
- **Non-negotiables:** "Parallel mode: no code before the batch's spec is released."

## Config (new keys)

| key | default | meaning |
|---|---|---|
| `spec_dir` | `docs/superpowers/specs` | where batch specs are committed |
| `spec_review_rounds` | `3` | rounds without a pass before a batch is parked |

`templates/foreman.config.md` documents both.

## Interactions with the hooks design (`feat/hooks`)

The hooks spec is revised after this one:
- **Report check (piece 7)** must allow a stop whose last message is `SPEC READY <path> <sha>`. Today it would block that stop for having no `json` block.
- **Worker briefing (piece 6)** carries the new contract, including the spec phase.
- **Candidate new piece, "spec gate":** during a run, deny a tagged worker's Edit or Write outside `spec_dir` until its batch's spec is released. The orchestrator would write a release file the hook can read. It's only feasible if a PreToolUse call can be mapped to its batch, so the hooks revision probes that.

## Error handling

- **No `SPEC READY`:** a worker that reports without one, or that edits non-spec files during the spec phase (seen in its commit list), is sent back once. A second time parks the batch.
- **Reviewer failures:** the read-only reviewer is re-dispatched once; then the round counts the chain alone. If both fail, the round doesn't count, and the orchestrator says so in the run report.
- **Context:** the orchestrator keeps per-batch state in its existing state table: batch, rows, spec path, released SHA, round, status (`spec`, `review r`, `passed`, `parked`, `released`, `PR #n`).

## Testing

- **`tests/run.sh`:**
  - `review.sh --spec` with a fake adapter: the prompt file has `{spec}` filled, the spec template is used, and fall-through works;
  - a check that `templates/spec.md` and `templates/spec-review-prompt.md` exist and carry every section the skill names.
- **A dry run** in a throwaway repo with a two-row backlog: `/foreman parallel 2` in manual mode. It reaches the checkpoint with two passed specs, or one passed and one parked when a row is made deliberately ambiguous. After release it opens two PRs whose first commit is the spec. This costs model calls, so it is manual and documented.
- **The first real use** is one vacancy-vibe wave with `parallel 2`. Its run report compares the spec rounds and findings with how many PR review rounds were needed afterwards.

## Version

0.5.0. Hooks would be 0.4.0, so the spec phase ships on top of the hooks if they land first. Otherwise the numbers swap at merge.
