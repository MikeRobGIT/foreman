---
description: One improvement pass from the backlog — plan → PR → wait for bots + CI → address comments → browser-verify → stop for human merge. Manual by default; with `--auto-merge` (or `loop`) it self-drives the backlog, auto-merging each fully-green pass and moving to the next item (no `/loop` needed). With `parallel N` it runs N worktree-isolated workers on separate batches at once (many items per invocation) while merging stays one PR at a time. `init` scaffolds the config and backlog for a new project. Project-agnostic; reads .claude/foreman.config.md.
argument-hint: "[init] [item-id] [tag:<x>] [loop|--auto-merge] [parallel N]"
---

# Foreman

**Plugin scripts** live in `${CLAUDE_PLUGIN_ROOT}/scripts` and the reviewer adapters in
`${CLAUDE_PLUGIN_ROOT}/reviewers` (Claude Code substitutes the absolute path in this text when it loads the
command; it is NOT set in the Bash tool's environment). Below, `$FOREMAN` means that `scripts` path: write
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
`test-driven-development`, `requesting-code-review`, `finishing-a-development-branch`), this plugin's
`/foreman:pr-address-comments` command, `graphify`, and the auto-memory system. All project-specific bindings
(backlog path, gate command, merge tool, dev URL, review bots, reviewer chain, E2E) come from a
per-project config — see **Step 0.5**. With no config it falls back to sensible defaults; `/foreman init`
writes one.

## Arguments

Tokens may be combined in any order (e.g. `IB-03 loop`; ids use the config's `id_prefix`, default `IB`):

- `init` — **set up a project** (see **Init**): write `.claude/foreman.config.md` and the backlog from the
  plugin's templates, filled with what can be detected. Runs alone; other tokens are ignored.
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
  zero-surface skip), and `risk != high`. Anything short of that STOPs and notifies. Never merge on yellow.
- **Wait for ALL configured review bots + CI before addressing** (step 7).
- **Browser-verify EVERY change with an app surface, in the claude-in-chrome browser** (step 9) — the skip is a narrow, explicitly-justified exception (zero application surface only). Playwright and "the code looks right" are never substitutes for the live check.
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
  empty"); a pass can't reach a green merge (stop, PR left open); or only `risk: high` items remain
  (report them so the human can run each by id).

Autonomy removes only the human approval *click* — never the gates that earn it. The step-4 gate,
step-7 wait-for-all-bots+CI, step-8 grep-verify/diminishing-returns triage, and step-9
browser-verify are all still fully enforced.

## Parallel mode (`parallel N`)

The slow part of a pass is waiting (gate, CI, bots), not coding. Parallel mode overlaps the waits: up to
N **workers** run steps 2-8 on separate batches, each in its own git worktree, and ONE **orchestrator**
(this session) owns every shared resource: picking batches, merging, the browser, the local database, the
backlog, the rules files and generated files. Every non-negotiable holds for every PR. Steps are labelled
W1-W6 to keep them apart from steps 0-12; W6's stop conditions replace the autonomous loop's Termination list.

### Orchestrator

- **W1 Precondition.** Step 0. Remove worktrees whose PR merged or closed (`git worktree remove`; never
  `--force` over unpushed work, never touch the worktree of a worker that has not reported), then
  `git worktree prune`.
- **W2 Pick and batch.**
  - Pool: `status: ready` rows (list them with grep and read only the rows you pick; backlogs get large),
    filtered by `tag:<x>` if given, minus rows that are `risk: high`, match `parallel_skip`, name a step-3
    `Procedure`, need a human decision, or are named by an open PR (match the id as a whole word).
  - Take ~3N. Group them by the files and symbols they touch (`git grep` the symbols and their importers)
    so no two batches share a file (except `parallel_shared_files`) or use a symbol another batch moves,
    renames or re-types. Rows that overlap share one batch (one PR, ~4 rows at most).
  - A batch that moves, renames or re-types an export is a **foundation batch**: it merges first, and
    batches that import it wait for it.
  - At most `parallel_ui_max` batches with an app surface. Print the plan (batch, rows, owned files,
    surface yes/no) before launching.
- **W3 Launch.** One `Agent` call per batch in one message (general-purpose, `isolation: "worktree"`,
  named, background). The prompt: the **Worker contract** from this file copied in (a worker cannot read
  this command), plus the batch's rows, owned files, a unique test-data id prefix and the resolved absolute
  `$FOREMAN` path. Keep a short state table (batch, rows, PR, verified SHA, status) instead of re-reading
  reports. Before launching, check that each `reviewers` entry's CLI is on PATH (`command -v`); the chain
  already falls through on quota, credits, auth and unknown models, so a missing CLI is the only thing to tell
  workers about.
- **W4 Each report, as it arrives** (a report is a claim; re-read everything):
  1. **Verify on the head SHA.** The latest run of every `required_ci` check on that SHA is green (list
     check runs with pagination); every `review_bots` entry has reviewed that SHA (the config's definition,
     else a review on that SHA that is not a quota, rate-limit or trial notice); read the inline threads,
     review bodies and top-level comments posted since the report; `mergeStateStatus` is clean (re-read
     `UNKNOWN` a few times, then move on and come back). Step 7's ceiling applies.
  2. **Audit the diff** (`git diff --name-status` of the fetched head against its merge base): non-empty,
     only owned files plus what the report lists (tests, `parallel_shared_files`), no unexplained deletion.
  3. **Surface.** Decide it yourself from the diff, using step 9's definition. If there is one, run step 9
     serially on the PR head merged onto current main (`git fetch origin +pull/<n>/head:pr-<n>`, a throwaway
     branch off main with that merge; the running dev server serves whatever is checked out, and a server you
     did not start is not yours to stop), then return to main and delete both branches. Run `pre_browser_check`
     first (a stale install makes every browser finding suspect), and when the worker delivered a check script run
     it on main (the control must differ) and then on the merge. The merge is blocked until the check passes. If a path cannot be driven (a control stays disabled, an action could reach a
     real external platform), leave the PR open, say what was and was not verified, and ask the human.
  4. **Findings** (a real Critical/Important finding, a red or missing check, a failed audit or browser
     check) go back to the worker (`SendMessage`; if it cannot be resumed, remove its worktree and launch a
     new worker on the branch), then W4.1 again.
  5. **Approve.** Manual mode: present the ready PRs with their evidence and head SHAs, and stop; the human
     approves a PR at a SHA.
  6. **Merge.** Reply to and resolve every thread (step 8's triage), post the merge summary, check the PR's
     commit messages for trailers the user forbids (pass `--subject`/`--body` without them if needed), then
     `gh pr merge --match-head-commit <the SHA verified in W4.1, or approved in W4.5>` with the repo's merge
     method (step 12) and without `--delete-branch` (the worktree holds the branch). If the merge is refused
     because the head moved, go back to W4.1 (manual mode: ask again). Then remove the worktree and the
     remote branch.
  7. **After every merge** run `post_merge_check` (under `$FOREMAN/gate-lock.sh orchestrator -- '<cmd>'`, like every gate) on the updated main and confirm it ran tasks (a
     change-scoped command such as `--affected` runs nothing on the default branch). Red: stop merging and
     report; a fix or revert goes through a PR.
- **W5 End of wave.** Pull main; run `{{gate}}` and `post_merge_check`, each under `$FOREMAN/gate-lock.sh orchestrator -- '<cmd>'`; red: stop. Then ONE bookkeeping PR,
  the only PR that edits the backlog, the rules files and generated files, built in an isolated worktree (another
  session may be editing the main checkout; never `git add -A` there). With `bookkeeping_tool`, save each worker's
  `json` block (plus its `pr`) and run the tool on them: it marks rows, numbers new ones and applies rule notes,
  and refuses (writing nothing) on a mismatch, which you then fix by hand. List every row id in its body;
  mark rows `done (#N)`, or leave them `ready` with a note of what is left; fold each worker's RULE NOTES
  (fixing the exact sentences they quote) and NEW ROWS; turn each RETRO into a `feedback_*` memory (and a
  rules note when the lesson is general); run `graphify update .` if the repo uses it; include any diff the
  gate left behind. It goes through the same checks: fix real contradictions, and after one round defer
  docs nits (a Critical/Important finding still blocks).
- **W6 Next wave or stop.** The next wave may start once main is green after W5, while the bookkeeping PR is
  still in review, if: its pool excludes every row that PR files or closes, each worker's briefing carries that
  PR's rule notes inline (the rules text is not on main yet), and the next bookkeeping PR is opened only after
  this one has merged (both edit the same files). Otherwise it starts after the merge. Stop when the
  pool is empty, `parallel_max_waves` is reached, the user says stop, main is red, CI is down, or half a
  wave failed. A worker with no report and no PR activity for `parallel_worker_timeout` minutes (not
  counting queued CI) is stuck: stop it (TaskStop), leave its worktree and PR alone, keep its rows out of
  the next pool, report it, and carry on.

### Worker contract

You run steps 2-8 on your batch in your own worktree, with these overrides:
- First read the project config (guardrails, `shared_resources`) and every `{{rules_glob}}` file for your area.
  Then run `{{worktree_setup}}` in your worktree (default `$FOREMAN/wt-setup.sh <worktree>`: copies
  `worktree_env_files` from the main checkout, runs `worktree_install`, then `stale_check`).
- Step 2: your batch is the approved scope. Write a short plan in the PR body; skip brainstorming and
  plan-approval gates. If a row needs a product decision, do the decidable part and report the rest.
- Own only your files (and your own entries in `parallel_shared_files`); if you need another file, stop and report.
- Remote work: the `git`+`gh` column of the merge-tool mapping; open the PR ready for review.
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
  gate under a slot: `$FOREMAN/gate-lock.sh <batch> -- '<gate>'` (never take or clear a slot by hand). A red that appears only under load: re-run that file alone, then the whole
  gate, which must end green in one run.
- Step 8: push follow-up commits; never amend or force-push once the PR is open. Update your branch (merge
  `origin/main` into it, do not rebase) only when it conflicts or the orchestrator asks, then re-run the gate.
- Step 10: do not edit the backlog, the rules files or generated files. Report RULE NOTES (quote the exact
  sentence your change makes false), NEW ROWS (full row text) and a RETRO (defects you introduced and what
  would have caught them).
- Attribution and temp files: the user's and the repo's rules override harness defaults (e.g. no
  `Co-Authored-By`); temp files go in the scratchpad under your batch name.
- Never merge. Poll for new bot comments after your last push, and stop pushing once you report.
- Report: PR URL, head SHA, check states on it, unresolved threads, per-row changes, evidence, files outside
  your owned list, declined items and why, surface verdict with steps and predictions (and the check script's
  path), RULE NOTES, NEW ROWS, RETRO, risks. End with ONE fenced `json` block the orchestrator feeds to
  `bookkeeping_tool`: `{"done": [{"id", "note"}], "append": [{"id", "note"}], "new_rows": ["| <prefix>-NEW-a | title | risk | ready | acceptance | source |"],
  "rule_notes": [{"file", "old", "new"}]}` (`old` is the exact sentence, one match; a raw ` | ` in a note or a row
  shifts the table's columns: write `\|`; the orchestrator adds `pr` and numbers `<prefix>-NEW-*` from the real max id).

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
   steps: review the config, add real rows to the backlog, commit both, run `/foreman`.

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

### Merge-tool command mapping (referenced by steps 0, 5, 7, 10, 12)

| action | `graphite` | `git`+`gh` |
|---|---|---|
| sync to fresh main | `gt sync` | `git switch main && git pull --rebase` |
| create branch + commit | `gt create --all -m "<msg>"` | `git switch -c <branch> && git add -A && git commit -m "<msg>"` |
| submit as draft PR | `gt submit` (draft) | `git push -u origin HEAD && gh pr create --draft --fill` |
| amend + re-push | `gt modify --all && gt submit` | `git commit --amend -a && git push --force-with-lease` |
| post-merge sync | `gt sync` | `git switch main && git pull --rebase && git branch -d <branch>` |

## Steps

### 0. Precondition
- Sync to fresh main (merge-tool "sync"). If the working tree is dirty (`git status --porcelain`, **including untracked files**), STOP — never start a pass on uncommitted work. Untracked files count on purpose: step 5's create stages them (`git add -A` semantics), so a stray file would be silently swept into the commit.

### 1. Pick the item
- Read `{{backlog_path}}`. Select the topmost `status: ready` row (or the `<item-id>` argument). With `tag:<x>`, only rows whose title contains `[x]` are eligible.
- If `risk: high`: **manual mode** → STOP and ask the human to confirm; **autonomous mode** → skip it and pick the next non-high `status: ready` row (a high-risk item only ever runs from an explicit `<item-id>`).
- If no eligible `status: ready` item remains: STOP. In autonomous mode, report "backlog empty" (with `tag:<x>`: "no ready [x] rows") and end the loop.
- Echo the chosen `id`, `title`, and `acceptance` back to the user.

### 2. Plan (non-trivial only)
- Anything beyond a typo/one-liner → invoke `brainstorming` then `writing-plans`. Trivial → skip to step 3.

### 3. Implement
- **Row procedure:** if the row's `acceptance` names `Procedure <path>`, read that file now and follow it as this pass's implement/verify procedure (it may add preflight, verify and stop rules on top of this command's, including ending the pass with the row `blocked — <reason>`).
- **Pre-flight self-review FIRST:** read every file matched by `{{rules_glob}}` and recent `feedback_*` memory — the mistake registry. Check your intended change against every relevant entry so you don't reintroduce a known defect (this is what makes the loop compound).
- **Behavior-preserving** (decomposition/refactor): the existing golden-master test MUST stay green with ZERO edits — that green run IS the proof. Only ADD tests for newly-extracted units. Decline behavior-CHANGING ideas; log them as new backlog rows.
- **New behavior / bugfix:** use `test-driven-development` (red → green).
- Delegate mechanical/parallelizable work to subagents (Task tool). Follow the rules for the touched area.

### 4. Gate
- Run `{{gate}}`. Loop fix → rerun until green.
- Run any `{{extra_gates}}` whose `when` condition matches the diff.

### 5. Draft PR (sibling branch off main — NOT stacked)
- Merge-tool "create branch + commit" with `"type(scope): <message>"` → "submit as draft". Record the PR number.

### 6. Request review — independent local reviewers
- Invoke `requesting-code-review` (a Claude `general-purpose` subagent reviewer).
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
- **Optional E2E (`{{e2e}}`):** if the project marks E2E non-gating / opt-in, don't block on it — read it for signal, investigate *persistent* reds via the report artifact.
- **Ceiling:** if a required bot never posts, or CI stays pending, after ~90 minutes counted from when the check starts (a check still queued after 3 hours means CI is down) (or a fixed poll cap), STOP and notify the human instead of polling forever — a missing bot (outage, app removed, misconfig) must not hang the pass.
- **Poll hygiene:** a settle-loop that exits on "0 pending" must FIRST confirm the `gh pr checks` output is non-empty (e.g. it contains a known job name) before counting — `gh pr checks` intermittently returns empty output that a bare pending-counter misreads as "settled" while jobs are still running.

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
- **Manual mode (default):** HALT and ask the human to approve the merge. **Never self-merge.**
- **Autonomous mode (`loop`/`--auto-merge`):** evaluate the auto-merge gate (Non-negotiables). All conditions green → note "auto-merging (loop mode)" and proceed to step 12. Any condition unmet → leave the PR open, post exactly which one failed, and STOP for a human (do not merge).

### 12. Merge & sync (on human approval, or auto in loop mode)
- `gh pr merge <n> --squash --delete-branch` (the merge flag follows repo policy — use `--merge`/`--rebase` if the project isn't squash-only) → merge-tool "post-merge sync". A squash-merge isn't recognized by `git branch -d` as "merged", so if the local branch lingers delete it with `git branch -D <branch>` and `git remote prune origin`. The tree is already clean (bookkeeping merged atomically in step 10), so the next pass's step 0 starts fresh — in autonomous mode the command loops straight back to **step 1** for the next item (no `/loop` tick needed).

## Why this compounds
The `{{rules_glob}}` files are a growing mistake registry. Step 3 reads them (prevent), steps 7–9 catch what slipped through, steps 8 + 10 codify every real defect back into the registry. Because the rules are committed and read by the project's review bot, a captured mistake upgrades both your future implementation and the bots' future review.
