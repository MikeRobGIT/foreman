# foreman

A Claude Code plugin that works through an improvement backlog one PR at a time: pick the next `ready`
row, plan, implement test-first, open a PR, get a cross-model review, wait for the PR bots and CI,
address comments, verify in the browser, then stop for your merge (or merge itself with `--auto-merge`).
`parallel N` runs N workers in separate git worktrees while one orchestrator owns merging.

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
| `/foreman` | one pass on the topmost `ready` row; stops before merge |
| `/foreman IB-12` | one pass on that row (the only way to run a `risk: high` row) |
| `/foreman --auto-merge` (or `loop`) | keep going: merge each fully green pass, then take the next row |
| `/foreman parallel 4 [--auto-merge]` | up to 4 worktree workers per wave |
| `/foreman tag:e2e` | only rows whose title contains `[e2e]` |
| `/foreman:pr-address-comments [pr]` | triage and fix a PR's review comments (step 8 calls it) |

`foreman` is a skill with `disable-model-invocation: true`: it runs only when you type `/foreman`; Claude
can't start it on its own (the Skill tool refuses it). `pr-address-comments` stays model-invocable because
foreman's step 8 invokes it. Bare `/foreman` works when no other command is named `foreman`; the full name
is `/foreman:foreman`.

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
`shared_resources`.

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
| `codex` | `codex -m M -c model_reasoning_effort=E --sandbox read-only review --base B` | sandbox | verified (CLI 0.160.0, gpt-6.1-sol) |
| `opencode` | `opencode run --pure --format json -m M --variant E` | `OPENCODE_PERMISSION` denies edit/web/task, bash only `git diff/log/show/status` | verified (zai-coding-plan/glm-5.3) |
| `claude` | `claude -p --permission-mode plan --permission-prompts none --allowedTools Read,Grep,Glob,Bash(git diff/log/show:*)` | plan mode, allowlist | verified (haiku) |
| `muse` | `muse exec --approval-mode never --disable-write --disable-shell --disable-web-tools --prompt-file …` | only the three `--disable-*` flags: `--approval-mode never` auto-allows every tool call (a probe without them ran a shell command and wrote a file); tests fail if one is dropped | verified (muse-spark-1.3-contributor) |
| `prime-agent` | `prime-agent -p --no-tools --no-session --offline -nc` (prompt + diff on stdin) | no tools | verified (default model) |
| `agy` | `agy -p … --mode plan --sandbox --output-format json` | diff in prompt, plan mode | unverified (not signed in) |
| `pi` | `pi -p --no-session --tools read,grep,find,ls` | tool allowlist | unverified (not installed) |
| `openclaw` | `openclaw agent exec --message-file - --config <deny exec/write/edit/process>` | config deny list | unverified (not installed) |
| `hermes` | `hermes -z -t safe` | `safe` toolset | unverified (not installed) |
| `custom` | the legacy `extra_reviewers` command, run with `bash -c` | whatever the command does | — |

Repo-reading adapters (codex, opencode, claude) should come first: diff-in-prompt adapters see only the
diff. No adapter runs with a bypass, yolo or skip-permissions flag.

Writing an adapter: `reviewers/<name>.sh <base> <model> <effort> <prompt_file> <log>`, source `_lib.sh`,
exit 0 with the review in `<log>`, exit 10 to fall through. Add `# foreman: no-stall-watch` if the CLI prints
nothing until it finishes.

## Scripts

| script | use |
|---|---|
| `scripts/review.sh <base> <log>` | the reviewer chain |
| `scripts/gate-lock.sh [--name L] <owner> -- <cmd>` | run a gate holding one of `gate_slots` slots shared by every worktree of the repo (`~/.claude/gate-locks/<repo>`); stale slots (holder dead 90+ min) are reclaimed; exits with the command's code |
| `scripts/wt-setup.sh <worktree>` / `--check <dir>` | copy env files, install, stale-install check; `--check` with no `stale_check` configured fails |
| `scripts/bk.py <repo> [--dry-run] <reports...>` | fold worker JSON reports into the backlog and rules files; writes nothing on any mismatch |
| `scripts/cfg.py <repo> <key> [default]` | read a config key (no PyYAML needed) |

## Tests

```bash
tests/run.sh                       # offline: config parsing, bk.py, worktree setup, gate locks, the chain with fake reviewers
tests/real-reviewers.sh main /tmp/rr codex=gpt-6.1-sol:medium claude=haiku:low   # real runs, in a throwaway repo; spends quota
```
