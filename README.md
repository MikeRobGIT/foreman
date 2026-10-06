# foreman

A Claude Code plugin that works through an improvement backlog one PR at a time: pick the next `ready`
row, plan, implement test-first, open a PR, get a cross-model review, wait for the PR bots and CI,
address comments, verify in the browser, then stop for your merge (or merge itself with `--auto-merge`).
`parallel N` runs N workers in separate git worktrees while one orchestrator owns merging. Each parallel batch
first writes a design spec that is reviewed until solid (and, in manual mode, approved by you at one checkpoint
per wave) before any code.

## Install

```bash
claude plugin marketplace add MikeRobGIT/foreman      # or a local clone: claude plugin marketplace add ~/src/foreman
claude plugin install foreman@foreman
```

Then, in a project: `/foreman init`, review the two files it writes, commit them, and run `/foreman`.
`.claude/` is a protected path, so Claude Code asks you to approve the two writes (in `-p` mode, use
`--permission-mode auto`; plain `acceptEdits` denies them).

## Usage

| command | what it does |
|---|---|
| `/foreman init` | write `.claude/foreman.config.md` and the backlog from the templates, filled with what it can detect (gate command, merge tool, CI checks, PR bots, reviewer CLIs, env files, install command). Never overwrites. |
| `/foreman seed [<prd-path>] [append]` | draft the first backlog rows from the codebase and an optional PRD or spec: PR-sized, foundation first, each with a checkable acceptance, anything over one PR split. Writes the backlog file only (review the diff, then commit); refuses a backlog that already has real rows unless you say `append`. |
| `/foreman ui` | start the backlog UI (see **Backlog UI**); `ui stop` stops it. |
| `/foreman` | one pass on the topmost `ready` row; stops before merge |
| `/foreman IB-12` | one pass on that row (the only way to run a `risk: high` row) |
| `/foreman --auto-merge` (or `loop`) | keep going: merge each fully green pass, then take the next row |
| `/foreman parallel 4 [--auto-merge]` | up to 4 worktree workers per wave; each batch's spec is reviewed before code (see **Spec phase**) |
| `/foreman tag:e2e` | only rows whose title contains `[e2e]` |
| `/foreman:pr-address-comments [pr]` | triage and fix a PR's review comments (step 8 calls it) |

`foreman` is a skill with `disable-model-invocation: true`: it runs only when you type `/foreman`; Claude
can't start it on its own (the Skill tool refuses it). `pr-address-comments` stays model-invocable because
foreman's step 8 invokes it. Bare `/foreman` works when no other command is named `foreman`; the full name
is `/foreman:foreman`.

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
  minutes of worker time, is parked, and so is one whose spec has a `Blocking` open question or whose worker you
  stopped. Its spec is committed with a `Status: parked` line, and its rows get a note.
- **The tables.** Batch state lives on disk (`scripts/batches.py`), not in the orchestrator's context. The
  checkpoint table and the run report's spec-phase table are printed from it, and with the hooks enabled the
  checkpoint question waits until the table is in the orchestrator's text, and a run report missing from it is
  handed back once at the end of the turn.

With the hooks enabled (0.4.0), a worker can't edit or commit code before its spec is released. The spec stays
frozen after release; any change goes in a `## Deviations after release` section, which a reviewer rules on
before merge.

## Backlog UI

`/foreman ui` starts a local web UI for the backlog: the ready queue in pick order (drag to reorder), search and
filters across every row, a one-row-at-a-time triage mode, progress charts, and a form to file or edit rows.
It reads the backlog from `origin`'s default branch, never your working copy.

- **Edits are pending until you publish.** They live in `~/.claude/foreman/<repo-key>/pending.json`.
  **Publish** replays them onto a fresh branch (`backlog/ui-<date>-<n>`) in a temporary worktree, commits,
  pushes and opens one PR with `gh`. A change whose row changed on main in the meantime is shown as a
  conflict and kept pending, never applied to the new text silently.
- **Security.** The server binds to `127.0.0.1` only and refuses any `Host` other than `127.0.0.1`/`localhost`.
  Every API call needs the session token from the printed URL's `#t=` fragment: the page keeps it in the tab's
  `sessionStorage` and sends it as a header, never as a cookie. Its state files are readable by you only.
- **Themes.** Auto follows your system light/dark setting; Light and Dark pin it (remembered per browser).
- `/foreman ui stop` stops the server. Without `gh`, Publish can't open the PR and Progress shows counts only.

## Hooks (since 0.4.0)

foreman ships settings hooks (`hooks/hooks.json`, one Python dispatcher: `scripts/hooks.py`) that enforce the worker
contract and the merge rules at the tool call, plus a wave-status mod. They are guardrails against mistakes, not a
security boundary. They fire in every session where the plugin is enabled, but only in a repo with a foreman config;
they fail open on their own errors. Three cases fail closed instead: a push whose diff can't be determined; a push or
merge command the scan can't read ("couldn't read this command", "couldn't read this merge"); and a spec-phase
worker's commit whose contents can't be told.

Run-only pieces act while the session's **run marker** exists: a prompt starting `/foreman` (or `/loop … /foreman`;
not `ui`, `init` or `seed`) writes it, the skill runs `hooks.py run-end` when the run ends, `/clear` and logout delete it,
and it expires after 48 hours. A session moved to the background, forked or resumed gets a new id; it keeps the run
through an alias file `runs/<new id>.json`, found from the `continued-in` record ending the old transcript. `/clear`
and logout leave a run another live session may still own; a marker no session holds any more expires by its
48-hour TTL.

| id | applies | what it does |
|---|---|---|
| `gate_before_push` | run, everyone | `git push`, `gh pr create` and `gt submit` need a gate receipt for the pushed tree. `gate-lock.sh <owner> -- '<gate>'`, run with the exact configured `gate`, mints one. A branch whose diff touches only `gate_exempt`, the backlog and the rules files needs none. A push after a commit or merge in the same call is denied: push in its own call. |
| `worker_guard` | run, subagents | no merge (`gh pr merge`, `gt merge`, merging `gh api` calls), force-push, push to the default branch, rebase, `gt modify` once pushed, or edits of the backlog, the rules files or the foreman config |
| `merge_discipline` | run, everyone | `gh pr merge` needs `--match-head-commit <sha>`; `--delete-branch` is refused while another worktree holds the branch; no `gt merge` |
| `trailer_guard` | foreman repo | `forbid_trailers` lines are refused in commit, PR and comment messages, and in a `gh pr merge` that would keep them |
| `backlog_check` | foreman repo | after an Edit/Write of the backlog, problem rows (wrong `\|` count, duplicate ids) are reported back |
| `worker_briefing` | tagged workers | a subagent whose prompt starts `foreman-worker: <batch>` gets the Worker contract, rendered |
| `report_check` | tagged workers | a tagged worker's final report must end with a valid `json` block that `bk.py --dry-run` accepts; it is sent back once |
| `script_allow` | run | `gate-lock.sh`, `wt-setup.sh`, `review.sh`, `bk.py`, `hooks.py` and `batches.py` (main session only) run without a permission prompt, in strict shapes only |
| `spec_gate` | run, `spec-phase` workers | edits and commits stay in `design_spec_dir`, and nothing is pushed, until the orchestrator runs `hooks.py release <batch> <sha>` |
| `batch_state` | run, orchestrator | a worker's `batches.py` call is denied; at the spec checkpoint every question is denied until the current `batches.py table checkpoint` is in the orchestrator's text, pasted in a message of its own (a table not yet in the transcript is denied too; the hook never edits the question; the third try against one unchanged table goes through); a `run-end` before `batches.py table report` is in the orchestrator's text leaves the report owed, and the end of the turn (a `Stop` hook) hands it back once, then lets the session stop; TaskStop is refused with no id or with a batch name, and for a worker before `hooks.py stopping` (an id it doesn't know passes); `hooks.py release` needs a passed batch at its reviewed SHA and, in a manual run, the checkpoint table pasted after the pass |
| `status_pane` | run | a band above the prompt: one line per batch (its status with the round or PR, and for a PR its CI, bots, unresolved threads and merge state); without batch files, one line per worker worktree |

Turn pieces off per repo with `hooks_disabled: [<id>, …]` in the config. Your own `! <command>` is never checked:
it's the override. The status band is a mod: like every mod it runs in-process and unsandboxed. It only reads (git,
gh, foreman's state dir) and draws, and only in the terminal and the Desktop app.

**Permissions for background workers.** A background worker that hits a permission prompt it can't show is denied
and stalls. `script_allow` covers foreman's own scripts; add the rest of the worker contract's commands to
`permissions.allow` in `~/.claude/settings.json` or the project's `.claude/settings.json`:

```json
{
  "permissions": {
    "allow": [
      "Bash(sleep:*)", "Bash(date +%s)", "Bash(kill -TERM:*)",
      "Bash(git add:*)", "Bash(git commit:*)", "Bash(git fetch:*)", "Bash(git merge:*)", "Bash(git push:*)", "Bash(git switch:*)", "Bash(git worktree:*)",
      "Bash(gh pr create:*)", "Bash(gh pr view:*)", "Bash(gh pr checks:*)", "Bash(gh pr comment:*)", "Bash(gh pr edit:*)", "Bash(gh api:*)",
      "Bash(node --check:*)",
      "Bash(python3 /path/to/foreman/scripts/batches.py:*)"
    ]
  }
}
```

Replace `/path/to/foreman` with the plugin's install path (the absolute path `/foreman` writes for `$FOREMAN`, minus
`/scripts`). `script_allow` already lets the orchestrator's `batches.py` calls through while a run is live; the entry
covers a call made with the hooks off.

**RTK.** An RTK Bash hook (`rtk hook claude` in a `PreToolUse` entry) rewrites `git …` to `rtk git …`, and Claude
Code's worktree guard refuses that in a worktree-isolated worker, so the worker can't push. Run RTK through a
wrapper that leaves `git` alone inside a worktree, and point the settings hook's `command` at it in place of
`rtk hook claude`; `/foreman parallel` then stops warning about it:

```bash
#!/usr/bin/env bash
# ~/.claude/hooks/rtk-unless-worktree-git: rtk's Bash hook, except git inside a Claude worktree
input=$(cat)
cwd=$(jq -r '.cwd // empty' <<<"$input")
cmd=$(jq -r '.tool_input.command // empty' <<<"$input")
case "$cwd" in
  */.claude/worktrees/*) grep -qE '(^|[;&|( ])git( |$)' <<<"$cmd" && exit 0 ;;
esac
exec rtk hook claude <<<"$input"
```

**Limits.**
- The hooks see only Claude's tool calls, never yours. Writes to the backlog or rules files through Bash (a `python3 -c` that edits them) aren't guarded; `bk.py` validates its own writes.
- Commands fed to a shell's stdin (`echo 'git push' | bash`), `eval` of built strings and encoded commands aren't detected.
- An unquoted `echo git push` is treated as a push (a deliberate false deny).
- Abbreviated long options (e.g. `git push --forc`) aren't recognised: obfuscation is a non-goal.
- A push with an explicit refspec source other than HEAD is judged by HEAD's tree.
- A command the scan can't read (unbalanced quotes, an unterminated heredoc) is judged fail-closed: a push is denied with "couldn't read this command"; push with a plain `git push` in its own call.
- A resumed session whose id changed, or a build whose subagent ids differ between events, runs some checks fail-open. `docs/superpowers/plans/2026-10-02-foreman-hooks-probes.md` records what was observed.
- A tagged worker is briefed at its first Bash or edit call, not its first Read: the PreToolUse hooks match only those tools (the spec gate guards only those tools too).

## Prerequisites (not bundled)

- The **superpowers** plugin (`superpowers@claude-plugins-official`), declared as a dependency. Installing
  foreman installs it when the `claude-plugins-official` marketplace is added
  (`claude plugin marketplace add anthropics/claude-plugins-official`). Without that marketplace the install
  succeeds with a warning and foreman does not load until superpowers is installed; adding the marketplace
  afterwards installs it automatically.
- `git`, `gh` (signed in), `python3` 3.8+, `perl`.
- At least one reviewer CLI from the table below, signed in, for the cross-model review.
- For browser verification: the Claude in Chrome extension.

## Config

`.claude/foreman.config.md` in the project: YAML frontmatter with the bindings, markdown body with the
project's guardrails. `templates/foreman.config.md` documents every key with its default. The legacy name
`.claude/improve-next.config.md` is read when `foreman.config.md` doesn't exist.

Main keys: `backlog_path`, `id_prefix` (row ids, default `IB`), `rules_glob`, `gate`, `extra_gates`,
`post_merge_check`, `merge_tool`, `required_ci`, `review_bots`, `reviewers` (+ `reviewer_timeout_seconds`,
`reviewer_stall_seconds`, `reviewer_max_diff_kb`), `dev_url`, `dev_restart`, `e2e`, `browser_lib`,
`pre_browser_check`, `worktree_env_files`, `worktree_install`, `stale_check`, `gate_slots`, `parallel_*`,
`spec_review_rounds`, `spec_phase_timeout`,
`shared_resources`. Hooks: `forbid_trailers`, `gate_exempt`, `worker_agent_types`, `hooks_disabled`,
`design_spec_dir`, `status_pane`.

### Per-project hooks

These stay in the project, not the plugin, because they know your app: a browser-check helper library
(`browser_lib`, e.g. logins for seeded test users), a dev-server restart (`dev_restart`), deploy or prod
probes. Point the config at them with project-relative paths.

## Reviewer chain

Step 6 runs `scripts/review.sh <base> <log>`, which tries each `reviewers` entry in order until one lands a
real review:

```yaml
reviewers:
  - { use: codex,    model: gpt-6.1-sol,                effort: medium }
  - { use: opencode, model: zai-coding-plan/glm-5.3,    effort: max }
  - { use: muse,     model: muse-spark-1.3-contributor, effort: max }
```

A reviewer is skipped (exit 10, next one) on quota, credits, auth, an unknown model, a rate limit, a diff
too big for its prompt, a timeout (`reviewer_timeout_seconds`, via `perl alarm`) or a stall (no output for
`reviewer_stall_seconds`). Each runs in its own process group, which is killed afterwards, children
included. `<log>.reviewer` names the reviewer that ran (or `none`) and `<log>.attempts` says why each one
fell through. **Every provider in the chain may receive your diff.**

| adapter | how it runs | read-only by | status |
|---|---|---|---|
| `codex` | review: `codex -m M -c model_reasoning_effort=E --sandbox read-only review --base B`; spec: `… --sandbox read-only exec --ephemeral --color never -o <log> -` (prompt on stdin) | sandbox | verified (CLI 0.160.0, gpt-6.1-sol); spec mode verified 2026-10-04 |
| `opencode` | `opencode run --pure --format json -m M --variant E` | `OPENCODE_PERMISSION` denies edit/web/task, bash only `git diff/log/show/status` | verified (zai-coding-plan/glm-5.3); spec mode verified 2026-10-04 |
| `claude` | `claude -p --permission-mode plan --permission-prompts none --allowedTools Read,Grep,Glob,Bash(git diff/log/show:*)` | plan mode, allowlist | verified (haiku); spec mode verified 2026-10-04 |
| `muse` | `muse exec --approval-mode never --disable-write --disable-shell --disable-web-tools --prompt-file …` | only the three `--disable-*` flags: `--approval-mode never` auto-allows every tool call (a probe without them ran a shell command and wrote a file); tests fail if one is dropped | verified (muse-spark-1.3-contributor); spec mode verified 2026-10-04 (reads the repo under --disable-shell) |
| `prime-agent` | `prime-agent -p --no-tools --no-session --offline -nc` (prompt + diff on stdin) | no tools | verified (default model) |
| `agy` | `agy -p … --mode plan --sandbox --output-format json` | diff in prompt, plan mode | unverified (not signed in) |
| `pi` | `pi -p --no-session --tools read,grep,find,ls` | tool allowlist | unverified (not installed) |
| `openclaw` | `openclaw agent exec --message-file - --config <deny exec/write/edit/process>` | config deny list | unverified (not installed) |
| `hermes` | `hermes -z -t safe` | `safe` toolset | unverified (not installed) |
| `custom` | the legacy `extra_reviewers` command, run with `bash -c`; `{prompt_file}` and `{base}` are substituted (shell-quoted) | whatever the command does | — |

Repo-reading adapters (codex, opencode, claude) should come first: diff-in-prompt adapters see only the
diff. No adapter runs with a bypass, yolo or skip-permissions flag.

The spec phase runs `scripts/review.sh --spec <spec path> <fork base> <log>`. It uses
`templates/spec-review-prompt.md`, and only adapters that read the repo themselves (marked
`# foreman: reads-repo`): codex (`codex exec`), claude, opencode (its `plan` agent), muse (its read tools under `--disable-shell`), and `custom` when its
command uses both `{prompt_file}` and `{base}`. Every other adapter falls through. If no adapter is left, the
orchestrator runs two Claude reviewers instead. On TERM, review.sh kills the running adapter's process group and
exits 143. `<log>.pid` holds its pid while it runs.

Writing an adapter: `reviewers/<name>.sh <base> <model> <effort> <prompt_file> <log>`, source `_lib.sh`,
exit 0 with the review in `<log>`, exit 10 to fall through. Add `# foreman: no-stall-watch` if the CLI prints
nothing until it finishes, and `# foreman: reads-repo` if the reviewer reads the repo itself: only those run in
spec mode (`FOREMAN_REVIEW_MODE=spec`; `_lib.sh` makes the others fall through).

## Scripts

| script | use |
|---|---|
| `scripts/review.sh [--spec <spec path>] <base> <log>` | the reviewer chain; `--spec` reviews a batch spec (exit 0 landed, 1 exhausted, 2 hard error, 143 terminated) |
| `scripts/gate-lock.sh [--name L] <owner> -- <cmd>` | run a gate holding one of `gate_slots` slots shared by every worktree of the repo (`~/.claude/gate-locks/<repo>`); stale slots (holder dead 90+ min) are reclaimed; exits with the command's code |
| `scripts/wt-setup.sh <worktree>` / `--check <dir>` | copy env files, install, stale-install check; `--check` with no `stale_check` configured fails |
| `scripts/bk.py <repo> [--dry-run] <reports...>` | fold worker JSON reports into the backlog and rules files (also how `/foreman seed` adds rows to an empty table); a `new_rows` entry must be a 6-cell row with a valid risk and status (since 0.6.0 this applies to worker reports too); writes nothing on any mismatch |
| `scripts/cfg.py <repo> <key> [default]` | read a config key (no PyYAML needed) |
| `scripts/hooks.py <event>` | the hooks' dispatcher; orchestrator CLIs `run-end` (always exits 0), `release <batch> <sha>`, `stopping <batch>`, `forget <batch>` (these three exit 3 when the hooks are inactive) |
| `scripts/state.py <path>` | print the repo's state dir (`~/.claude/foreman/<repo-key>`, or under `$FOREMAN_HOME`) |
| `scripts/batches.py <command> …` | the parallel spec phase's batch state (`set`, `note`, `bump`, `stamp`, `task`, `add-round`, `show`) and its two tables (`table checkpoint <wave>`, `table report`); orchestrator only |

## Tests

```bash
tests/run.sh                       # offline: config parsing, bk.py, worktree setup, gate locks, the chain with fake reviewers (incl. spec mode), the spec template, the hooks, the mod's formatter, SKILL.md spec-phase text
SPEC=docs/superpowers/specs/x-design.md tests/real-reviewers.sh <fork base> /tmp/rrs codex=gpt-6.1-sol:medium   # a real spec-mode run
tests/e2e-hooks.sh                 # headless end-to-end of the hooks with haiku workers, in a throwaway repo; spends quota
tests/real-reviewers.sh main /tmp/rr codex=gpt-6.1-sol:medium claude=haiku:low   # real runs, in a throwaway repo; spends quota
```
