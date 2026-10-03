---
# foreman config: per-project bindings for /foreman. YAML frontmatter = bindings; the markdown body
# below = project notes and guardrails every pass honors. A missing key takes the default in brackets.
# The parser is a small YAML subset: scalars, [a, b] lists, `- item` lists, `- { k: v }` flow maps,
# one level of `k: v` maps. Quote a value that contains ` #`.

# --- backlog -----------------------------------------------------------------------------------------
backlog_path: .claude/improvement-backlog.md   # the improvement backlog [.claude/improvement-backlog.md]
id_prefix: IB                                  # row ids are <prefix>-<n> [IB]
rules_glob: .claude/rules/*.md                 # mistake registry, read in step 3, written in step 10 [.claude/rules/*.md]

# --- gates and merging -------------------------------------------------------------------------------
gate: npm test                  # local pre-PR gate; must pass before the PR [required; else the repo's obvious check]
extra_gates:                    # extra gates that run only when the diff matches `when` [none]
  # - when: diff touches db/**
  #   run: npm run db:test
post_merge_check: npm test      # whole-tree check on main after every merge; must not be change-scoped [= gate]
merge_tool: git                 # graphite | git | auto (gt when on PATH) [auto]
required_ci: []                 # CI check names that must be green on the head SHA [all checks `gh pr checks` reports]
review_bots: []                 # PR bots step 7 waits for, e.g. [claude] [discovered from PR comments]

# --- cross-model review (step 6), run by ${CLAUDE_PLUGIN_ROOT}/scripts/review.sh ---------------------
# Tried in order; the first reviewer that lands a real review wins. A reviewer falls through on quota,
# credits, auth, an unknown model, a rate limit, a stall or a timeout. Repo-reading adapters (codex,
# opencode, claude) belong first; diff-in-prompt adapters (muse, prime-agent, agy, pi, openclaw, hermes)
# only see the diff. Every listed provider may receive your diff.
reviewers:
  # - { use: codex, model: gpt-6.1-sol, effort: medium }
  # - { use: opencode, model: zai-coding-plan/glm-5.3, effort: max }
  # - { use: muse, model: muse-spark-1.3-contributor, effort: max }
reviewer_timeout_seconds: 900   # hard limit per reviewer [900]
reviewer_stall_seconds: 150     # no output for this long while alive = hung, try the next one [150]
reviewer_max_diff_kb: 200       # diff-in-prompt adapters fall through above this [200]
# extra_reviewers:              # LEGACY: whole commands, each run as a one-entry `custom` chain; ignored when `reviewers` is set
#   - codex -m gpt-6.1-sol --sandbox read-only review --base main

# --- browser verification (step 9) -------------------------------------------------------------------
dev_url: http://localhost:3000  # where the dev server serves the app [http://localhost:3000, flagged]
# dev_restart: /dev-restart     # skill or command that restarts a stale dev server [none]
# e2e:                          # durable Playwright codification of a step-9 check [none]
#   spec_dir: e2e/tests
#   run: npx playwright test {spec} --workers=1
#   gating: false
# browser_lib: .claude/scripts/pw-lib.mjs   # helpers workers build browser-check scripts on [none: prose]
# pre_browser_check: ...        # run before a browser check, `{repo}`; non-zero stops it
                                #   [${CLAUDE_PLUGIN_ROOT}/scripts/wt-setup.sh --check {repo} when stale_check is set]

# --- worktrees (parallel mode) -----------------------------------------------------------------------
worktree_env_files: []          # untracked files copied from the main checkout into each worktree, e.g. [.env.local]
# worktree_install: npm ci      # install command run in each new worktree [none]
# stale_check: ...              # exits non-zero when the install at {dir} is stale [none: --check FAILS]
# worktree_setup: ...           # replaces the default `${CLAUDE_PLUGIN_ROOT}/scripts/wt-setup.sh {worktree}`
gate_slots: 2                   # gates allowed at once across all worktrees, enforced by gate-lock.sh [2]
# bookkeeping_tool: ...         # replaces `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/bk.py {repo} [--dry-run] <reports...>`

# --- parallel mode caps ------------------------------------------------------------------------------
parallel_max: 5                 # concurrent workers [5]
parallel_max_waves: 3           # waves per invocation [3]
parallel_ui_max: 2              # batches with an app surface per wave [2]
parallel_worker_timeout: 90     # minutes of worker silence before it counts as stuck [90]
parallel_skip: []               # rows a wave never batches (ids, tags like "[serial]", title patterns) [none]
parallel_shared_files: []       # files several batches may edit, each its own entries (e.g. locale JSON) [none]
shared_resources: []            # rules every worker follows: shared database, test data, browser sign-in, VCS quirks [none]

# --- spec phase (parallel mode: every batch's spec is reviewed before any code) -----------------------
design_spec_dir: docs/superpowers/specs   # batch specs are committed here; before release a spec-phase worker may edit and commit only here (spec_gate); not e2e.spec_dir; no spaces (hooks auto-allow only unquoted arguments) [docs/superpowers/specs]
spec_review_rounds: 3           # review rounds without a pass before a batch is parked [3]
spec_phase_timeout: 60          # minutes of worker time in the spec phase before a batch is parked [60]

# --- hooks (README: Hooks) ------------------------------------------------------------------------------
forbid_trailers: []             # lines that must never start a commit, PR or comment message, e.g. [Co-Authored-By, Claude-Session]; an entry written /.../ is a regex searched within each line [none]
gate_exempt: [docs/**, "**/*.md"]   # gitignore-style paths a push may change without a gate receipt; backlog_path and rules_glob always may [docs/**, **/*.md]
worker_agent_types: [general-purpose, workflow-subagent]   # subagent types the briefing and report check apply to [these two]
hooks_disabled: []              # hook ids off in this repo: gate_before_push, worker_guard, merge_discipline, trailer_guard, backlog_check, worker_briefing, report_check, script_allow, spec_gate, status_pane [none]
status_pane: true               # the wave status band above the prompt during a run [true]
---

# Project notes & guardrails

Write the rules every pass must honor here, for example:

- No mutations on external platforms (payments, ads, email) without an explicit go-ahead.
- Known-environmental CI failures not to chase, and where the bot-triage rules live.
