# Foreman hooks: design

Status: draft for review, 2026-10-02. Branch `feat/hooks`. Target version 0.4.0.

## Purpose

Foreman's worker contract and merge rules exist only as prose in `skills/foreman/SKILL.md` and in dispatch prompts, and workers break them. These failures happened in real runs:

- **A failed gate pushed anyway (#441).** A `;` chain pushed a commit whose gate had failed.
- **A subagent merged a PR (#345).** A review subagent ran `gh pr merge` despite "read-only" in its prompt.
- **Forbidden trailers.** Every dispatch had to repeat the no-trailer rule; when one forgets it, `Co-Authored-By` lands in a commit.
- **Broken backlog rows.** Unescaped `|` and embedded newlines broke rows. Four rows sat malformed on main until the backlog UI's banner found them.
- **Late report failures.** A worker's final `json` report only fails at W5, when `bk.py` refuses the whole batch.
- **Stalled background workers.** A background worker that hits a permission prompt it can't show is denied and stalls.
- **No live view of a wave.** The orchestrator had to answer "what is that agent doing?" by hand.

Hooks turn the enforceable rules into checks that fire on the tool call itself, and give the orchestrator a live view of the wave.

Success means four things:
- every case above is denied or corrected at the call, with a reason that tells Claude what to do instead;
- nothing fires outside a foreman repo;
- run-only rules stay silent outside a run;
- a bug in a hook never blocks work. Hooks fail open on their own errors.

## Findings this design rests on

The docs were read on 2026-10-02 (`code.claude.com/docs/en/hooks`, `sub-agents`, `plugins/components`, `plugins/mods/*`). A headless probe was also run in a throwaway repo, with Claude Code current on that date.

1. **Skill-frontmatter hooks fire only in the main session.**
   - In the probe, a `PreToolUse` hook declared in a skill's frontmatter fired for the main session's `Bash` call.
   - It did NOT fire for a foreground subagent, a worktree-isolated subagent, or a Workflow agent.
   - So foreman's hooks cannot live in `SKILL.md`.
2. **Settings and plugin hooks fire everywhere.** In the same probe, a hook in `.claude/settings.json` fired in all four places. The docs say plugin hooks apply wherever settings hooks do.
   - Subagent calls carry `agent_id` and `agent_type`: `general-purpose` for an Agent-tool worker, `workflow-subagent` for a Workflow agent.
   - Main-session calls carry neither.
3. **Subagents share the orchestrator's identity.** They carry the orchestrator's `session_id`, and `CLAUDE_PROJECT_DIR` is the main repo even when the subagent's `cwd` is its worktree.
4. **Plugin hooks are active in every session where the plugin is enabled, in every project.** A single plugin hook can't be disabled on its own.
5. **`if: Bash(...)` matching is best-effort.** The docs recommend permission rules for hard denies. These hooks are guardrails against mistakes, not a security boundary.
6. **Mods are plugin JS modules declared as `"modules"` in `hooks/hooks.json`.**
   - They run in-process in a shared worker.
   - They can draw UI: a status line, a band or a pane, refreshed on a timer, and they can run processes.
   - They run unsandboxed with the user's permissions, work only in the terminal and Desktop, and their API "can change between releases" (the generated `.d.ts` is authoritative).
7. **`PermissionRequest` can allow a call.** When no hook decides in a session that can't show a prompt, the call is denied. `WorktreeCreate` would replace git's worktree creation entirely; it is out of scope.

## Scope

There are nine pieces. Pieces 1–8 are `command` hooks served by one script; piece 9 is a mod.

| # | Name (config id) | Event | Active | Applies to |
|---|---|---|---|---|
| 1 | Gate before push (`gate_before_push`) | PreToolUse | during a run | everyone; docs-only diffs exempt |
| 2 | Worker guard (`worker_guard`) | PreToolUse | during a run | subagents |
| 3 | Merge discipline (`merge_discipline`) | PreToolUse | during a run | everyone |
| 4 | Trailer guard (`trailer_guard`) | PreToolUse | in a foreman repo | everyone |
| 5 | Backlog row check (`backlog_check`) | PostToolUse | in a foreman repo | everyone; context only, never blocks |
| 6 | Worker briefing (`worker_briefing`) | SubagentStart | during a run | worker-type subagents with the worker tag |
| 7 | Report check (`report_check`) | SubagentStop | during a run | worker-type subagents with the worker tag; blocks once |
| 8 | Script allow (`script_allow`) | PermissionRequest | during a run | everyone; exact plugin script paths only |
| 9 | Wave status (`status_pane`) | Mod | during a run | the session that started the run |

## Non-goals

- **No run ledger** (recorded PRs or SHAs, or state restored after compaction). The user declined it.
- **No `WorktreeCreate` hook**, no plan-mode warning, no `prompt`- or `agent`-type hooks, no mod-registered tools.
- **No enforcement of anything the user runs.** Hooks see only Claude's tool calls; `! git push` is the user's override.
- **No guarding of Bash-driven file writes** to the backlog or rules (a `python3 -c` that edits the file). Edit/Write tool calls are guarded. `bk.py` validates its own writes.

## Architecture

```
hooks/hooks.json        settings hooks (pieces 1-8) + "modules": ["./status.mjs"] (piece 9)
scripts/hooks.py        one dispatcher: python3 hooks.py <event>; reads the hook JSON on stdin
scripts/state.py        repo_key() / state_dir(): shared by ui.py and hooks.py (moved out of ui.py)
hooks/status.mjs        the wave status mod
scripts/gate-lock.sh    + writes a gate receipt (piece 1)
```

### hooks.json

Every handler is `type: command`, in exec form: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/hooks.py <event>`, with `timeout: 10` (30 for SubagentStop). `if:` filters keep Python from starting on unrelated calls.

- **UserPromptSubmit** and **UserPromptExpansion** → `run-start`. The handler checks whichever event carries the `/foreman` text; the plan probes which.
- **SessionEnd** → `run-end`.
- **PreToolUse** (matcher `Bash`), three handlers with `if: Bash(git *)`, `if: Bash(gh *)` and `if: Bash(gt *)` → `pretool`.
- **PreToolUse** (matcher `Edit|Write|MultiEdit|NotebookEdit`) → `pretool`, for the worker guard's file rule.
- **PostToolUse** (matcher `Edit|Write|MultiEdit`) → `posttool`.
- **SubagentStart** → `subagent-start`.
- **SubagentStop** → `subagent-stop`.
- **PermissionRequest** (matcher `Bash`) → `permission`.

### `hooks.py` common path

These steps run on every event, in order:
1. Parse stdin. On a parse error, exit 0.
2. Find the repo.
   - Take `cwd` from the input. If the Bash command starts with `cd <path> &&` or uses `git -C <path>`, use that path instead.
   - Then `git rev-parse --show-toplevel` gives the checkout. The main checkout is the first `git worktree list --porcelain` entry.
   - Load the config with `cfg.load(main checkout)`. With no foreman config, exit 0. This is the "in a foreman repo" test.
3. Drop any piece listed in the config's `hooks_disabled`.
4. Check whether a run is active: the file `state_dir/runs/<session_id>.json` exists and is younger than 48 hours.
5. Decide whether the caller is a subagent: the input has `agent_id`.
6. Run the event's pieces.
   - A deny prints the PreToolUse JSON `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"foreman: <rule>. <what to do instead>"}}` and exits 0.
   - An unexpected exception anywhere prints `foreman hook error: <type>: <msg>` to stderr and exits 0. That is fail-open and non-blocking.

`state_dir(main)` is `$FOREMAN_HOME/<repo_key>`, where FOREMAN_HOME defaults to `~/.claude/foreman` and `repo_key` = the first 12 hex digits of sha1(`origin url` + newline + `realpath(main)`). This is today's `ui.py` key, moved to `scripts/state.py` and used by both scripts. Directories are 0700 and files 0600, as the backlog UI's final fix set.

## The run marker

- **`run-start`.**
  - Fires when the prompt is `/foreman` or `/foreman:foreman`, optionally followed by arguments whose first word is not `ui` or `init`.
  - It writes `runs/<session_id>.json` = `{"started": <iso>, "args": "<args>"}`.
  - It never blocks the prompt.
- **`run-end` (SessionEnd)** deletes `runs/<session_id>.json`.
- **Markers older than 48 hours are ignored** and pruned on the next `run-start`.
- **Known effect:** after a run finishes, the run-only pieces stay on for the rest of that session. They affect only pushes without receipts, subagent merges and force pushes, and merges without `--match-head-commit`. That's intended.

## The pieces

### 1. Gate before push

- **Triggers:**
  - `git push`, except a delete: `--delete`, `-d`, or a `:<branch>` refspec;
  - `gh pr create`;
  - `gt submit`.
- **Allowed when either:**
  - (a) a receipt exists at `state_dir/gate/<tree>`, where `<tree>` is `git rev-parse HEAD^{tree}` in the command's repo; or
  - (b) every path in `git diff --name-only $(git merge-base origin/<default> HEAD) HEAD` matches a glob in `gate_exempt`. The default is `[".claude/**", "docs/**", "**/*.md"]`, and `<default>` is the default branch's name, from `origin/HEAD` with a fallback to `main`.
- **Otherwise denied:** "foreman: no passing gate for this commit's tree. Run `<gate-lock.sh path> <owner> -- '<gate>'`, then push."
- **The receipt,** written by `gate-lock.sh` after the command exits 0:
  - Only when the joined command equals the config's `gate`, or one of `extra_gates[].run`. A `--name` lock or any other command writes nothing, so `gate-lock.sh x -- true` can't mint a receipt.
  - The key is the tree hash of the working copy at the end of the gate: tracked plus untracked, non-ignored files, via a temporary `GIT_INDEX_FILE`, `git add -A`, then `git write-tree`. Gating before or after the commit therefore both match, as long as the commit includes everything the gate saw.
  - Contents: `{"command", "owner", "time"}`. Receipts older than 14 days are pruned.
- **Limit:** it checks `HEAD`, not an explicit refspec's source. `git push origin other:refs/heads/x` is judged by `HEAD`.

### 2. Worker guard (subagents during a run)

- **Bash commands denied:**
  - `gh pr merge`;
  - `git push` with `--force`, `-f`, `--force-with-lease` or a `+` refspec;
  - `git rebase`;
  - `git reset --hard`.
- **Edit, Write, MultiEdit and NotebookEdit** are denied when the target is the backlog file or matches `rules_glob`. Both are resolved against the target file's checkout. The worker contract already says workers report these as RULE NOTES and NEW ROWS.
- **Each reason names the contract line:** "foreman: workers never merge; report and the orchestrator merges".

### 3. Merge discipline (everyone during a run)

`gh pr merge` is denied when either holds:
- it lacks `--match-head-commit <sha>`: "merge the SHA you verified";
- it has `--delete-branch` or `-d`: "the worker's worktree holds the branch; delete it after removing the worktree".

### 4. Trailer guard (any session in a foreman repo)

- **Commands checked:**
  - `git commit`;
  - `gh pr create`, `gh pr edit`, `gh pr comment`, `gh pr merge` and `gh pr review`;
  - `gt create`, `gt modify` and `gt submit` with `-m` or `--message`.
- **What it reads:**
  - the whole command text;
  - the file named by `-F`, `--file`, `--body-file` or `--message-file`, when that path exists relative to the repo. `-F -` means the text is in the command, for example a heredoc.
- **Rule:** deny when any entry of `forbid_trailers` (a list, default `[]`, matched case-insensitively) appears. The reason names the entry and says to remove it.
- **With an empty list** the piece does nothing.

### 5. Backlog row check (any session in a foreman repo)

- **Fires after** Edit, Write or MultiEdit whose `file_path` is the repo's `backlog_path`.
- **It runs** `backlog.parse(text, prefix)`. It collects `line_problems` for every row-like line (`^| <prefix>-`) and `duplicates()`.
- **Output:** when there are problems, it returns `additionalContext`: "foreman: the backlog now has N problem rows: <up to 10, `id: problem`>. Fix them before committing." It never blocks.
- **Problems that were already there** are reported too. That is deliberate.

### 6. Worker briefing (SubagentStart, during a run)

- **Who gets it:**
  - the subagent's `agent_type` must be in `worker_agent_types`, default `["general-purpose", "workflow-subagent"]`;
  - its prompt must carry the worker tag: SKILL.md W3 makes every worker prompt start with `foreman-worker: <batch>`.
  - If the plan's probe shows SubagentStart receives neither the prompt nor a readable transcript, the briefing goes to every worker-type subagent. It then starts with "If your task does not begin with `foreman-worker:`, ignore this briefing."
- **Content,** as `additionalContext`, capped at 8 KB:
  - the `### Worker contract` section, read at runtime from the installed `skills/foreman/SKILL.md`, so it has one source;
  - the config's `shared_resources`, `gate` and `forbid_trailers`;
  - "your final message's `json` block is checked when you stop".
- **The orchestrator's dispatch prompt** shrinks to the batch, its rows, its owned files and the id prefix. SKILL.md W3 changes accordingly.

### 7. Report check (SubagentStop, during a run)

- **Same who-test as piece 6.** The tag is read from the first user message in `agent_transcript_path`.
- **When `stop_hook_active` is true,** it allows the stop. It blocks at most once per stop.
- **Otherwise:**
  - It takes the last fenced `json` block in `last_assistant_message`.
  - If the block is missing, it blocks: "end your report with ONE fenced json block (the worker contract's format), even if empty".
  - If present, it writes the block plus `"pr": 0` to a temp file and runs `bk.py <the worker's checkout> --dry-run <file>`.
    - The worker's checkout is the toplevel of the input's `cwd`. That branch started from `origin/<default>` when the worker launched, so the rows it reports on are there.
    - The main checkout can lag behind origin and would refuse valid reports.
    - A non-zero exit blocks with bk.py's problem lines.
- **The block** is `{"decision":"block","reason":...}`.

### 8. Script allow (PermissionRequest, during a run)

Only Bash commands are considered.
- **Parsing:** `shlex.shlex(command, posix=True, punctuation_chars=True)`.
  - One leading `cd <path> &&` is allowed.
  - Any other operator token (`;`, `&&`, `||`, `|`, `&`, `>`, `<`, `(`, `)`) or a `$(`/backtick in the text means no decision.
- **The executable,** after an optional `python3`, `bash` or `sh`, must be one of these absolute paths: `$CLAUDE_PLUGIN_ROOT/scripts/gate-lock.sh`, `wt-setup.sh`, `review.sh` or `bk.py`.
- **Extra conditions:**
  - `gate-lock.sh`: the command after `--` must equal the config's `gate`, or one of `extra_gates[].run`. Otherwise gate-lock would launder any command.
  - `bk.py` from a subagent: only with `--dry-run`.
- **Then:** `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}`.
- **Anything else gets no decision,** so normal permission handling applies. This piece never denies.

### 9. Wave status (mod `hooks/status.mjs`)

- **When:**
  - The mod registers on `session.start`.
  - It shows only while the session's run marker exists. It finds the repo, the key and the marker through the mods API (`$.process.run('git', …)`, file reads, `crypto.subtle` for sha1). It checks every 60 seconds.
  - `status_pane: false` or `hooks_disabled: [status_pane]` turns it off.
- **Data,** refreshed every 60 seconds while visible:
  - `git worktree list --porcelain` gives the non-main worktrees and their branches;
  - for each branch, `gh pr list --head <branch> --state all --json number,state,headRefOid,mergeStateStatus,statusCheckRollup`;
  - one GraphQL query gives the unresolved review threads for all those PRs;
  - `required_ci` from the config narrows the CI column.
- **Display:** a band above the prompt, one line per worker, plus a one-line `$.ui.status` summary ("wave: 3 open · 2 merged · 1 red"). Each line has:
  - the worktree's basename;
  - PR number and state;
  - head SHA, 9 characters;
  - CI as ✓ ✗ or …;
  - review bots as ✓ ✗ or …;
  - unresolved threads;
  - merge state.
- **Errors:** every refresh is wrapped. A failure shows "foreman status unavailable: <reason>" and keeps the last good table. The mod never throws into Claude Code.
- **API binding:** the exact calls are bound in the plan, against the `.d.ts` the installed Claude Code generates. The formatting is a pure exported function with a node test.

## Config keys (new)

| key | default | meaning |
|---|---|---|
| `forbid_trailers` | `[]` | text that must never appear in commits, PRs or PR comments (piece 4) |
| `gate_exempt` | `[".claude/**", "docs/**", "**/*.md"]` | a diff touching only these paths needs no gate receipt (piece 1) |
| `worker_agent_types` | `["general-purpose", "workflow-subagent"]` | subagent types that get the briefing and the report check |
| `hooks_disabled` | `[]` | piece ids to turn off in this repo (the docs offer no per-hook switch) |
| `status_pane` | `true` | piece 9 |

`templates/foreman.config.md` documents them. vacancy-vibe sets `forbid_trailers: [Co-Authored-By, Claude-Session, Generated with]` at switch-over.

## SKILL.md changes

- **W3 / Worker contract:**
  - every worker prompt starts with `foreman-worker: <batch>`;
  - the contract notes that hooks enforce: gate receipts, no merge or force push, no backlog/rules edits, trailers, and the report check.
- **Gate steps** use `gate-lock.sh` with the exact configured `gate` string, so a receipt is written.
- **A "Hooks" paragraph** lists the nine pieces, `hooks_disabled`, and the user's override (`! <command>`).

## Error handling

- **Fail open:** every hook fails open on its own errors. It writes to stderr and exits 0.
- **Git errors** (no `origin/<default>`, a detached HEAD) count as "can't decide", which allows the call. The exception is piece 1: there a missing receipt plus an undeterminable diff denies, because an unknown push is what the rule exists to stop. The reason says to run the gate.
- **The mod** catches everything per refresh.

## Security notes

- **Guardrails, not security.** `if:` matching and the command parsing are best-effort; a determined bypass (`sh -c "$(echo …)"`) is out of scope. Piece 8 is the one piece that grants something. It allows only four exact paths with operator-free arguments, and only during a run.
- **The mod runs unsandboxed,** like every mod. It only reads (git, gh) and draws. The README says so.

## Testing

- **`tests/test_hooks.py`** feeds recorded hook JSON to `hooks.py` in temp repos, one row per case, with `FOREMAN_HOME` in a temp dir. Cases:
  - no config, so nothing fires;
  - no marker, so the run-only pieces are silent;
  - `agent_id` present versus absent;
  - receipt present, missing, and exempt-only diff;
  - `gate-lock.sh` with the gate versus with another command (receipt written or not);
  - each worker-guard denial, and an allowed normal push;
  - merge with and without `--match-head-commit`, and with `-d`;
  - a trailer in the command, in a `-F` file, and in a heredoc;
  - backlog problems reported, and a clean backlog returning nothing;
  - briefing with and without the tag;
  - the report check: missing block, refused block, valid block, and `stop_hook_active`;
  - script allow: exact path; path plus `; rm`; gate-lock with a non-gate command; bk.py without `--dry-run` from a subagent.
- **`tests/test_status.mjs`** tests the mod's pure formatter.
- **A headless end-to-end** loads the plugin with `--plugin-dir` in a throwaway repo, with a fake `gh` on PATH and haiku workers. It checks:
  - a worktree subagent's `gh pr merge` is denied;
  - a push without a receipt is denied, and pushes after the gate;
  - a tagged worker receives the briefing;
  - a worker that stops without a `json` block is blocked once.
- **`tests/run.sh`** runs the first two suites. The end-to-end costs model calls, so it is manual and documented.
- **The mod:** `claude plugin validate` lists its hooks and calls, plus a manual look in a terminal during a real wave.

## Version

0.4.0. The README gains a "Hooks" section covering what each piece does, `hooks_disabled`, the limits above, and that a mod runs unsandboxed.
