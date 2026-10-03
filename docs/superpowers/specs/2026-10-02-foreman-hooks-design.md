# Foreman hooks: design

Status: draft for review, revision 2 (2026-10-02). Revision 1 had 3 Critical, 13 Important and 5 Minor findings from an independent review; all are addressed below, and the few changes made to a suggested fix are noted where they apply. Branch `feat/hooks`. Target version 0.4.0. This depends on the parallel spec-phase design (`feat/spec-phase`, see Interactions).

## Purpose

Foreman's worker contract and merge rules exist only as prose, in `skills/foreman/SKILL.md` and the dispatch prompts, and workers break them. These failures happened in real runs:

- **A failed gate pushed anyway (#441).** A `;` chain pushed a commit whose gate had failed.
- **A subagent merged a PR (#345).** A review subagent ran `gh pr merge` despite "read-only" in its prompt.
- **Merges of an unverified head.** Merging a SHA other than the one verified is prevented only by the skill's prose (`--match-head-commit`). Deleting a branch that a worker's worktree still holds breaks the worktree cleanup.
- **Forbidden trailers.** Every dispatch had to repeat the no-trailer rule; when one forgets it, `Co-Authored-By` lands in a commit.
- **Broken backlog rows.** Unescaped `|` and embedded newlines broke rows. Four rows sat malformed on main until the backlog UI's banner found them.
- **Late report failures.** A worker's final `json` report only fails at W5, when `bk.py` refuses the whole batch.
- **Stalled background workers.** A background worker that hits a permission prompt it can't show is denied and stalls. This design covers foreman's own scripts; the README gives a `permissions.allow` snippet for the rest of the contract's commands.
- **No live view of a wave.** The orchestrator had to answer "what is that agent doing?" by hand.

Success means:
- every case above is denied or corrected at the call, with a reason that says what to do instead;
- nothing fires outside a foreman repo;
- run-only rules stay silent outside a run;
- hooks fail open on their own errors. There is one stated exception: piece 1 denies a push whose diff it can't determine.

## Findings this design rests on

The docs were read on 2026-10-02 (`code.claude.com/docs/en/hooks`, `sub-agents`, `plugins/components`, `plugins/mods/*`). A headless probe was run in a throwaway repo on the same date.

1. **Skill-frontmatter hooks fire only in the main session.** In the probe, a skill-frontmatter `PreToolUse` hook fired for the main session's `Bash`. It did NOT fire for a foreground subagent, a worktree-isolated subagent, or a Workflow agent. So foreman's hooks can't live in `SKILL.md`.
2. **Settings and plugin hooks fire everywhere.** A hook in `.claude/settings.json` fired in all four places. The docs say plugin hooks apply wherever settings hooks do.
   - Subagent calls carry `agent_id` and `agent_type`: `general-purpose` for an Agent-tool worker, `workflow-subagent` for a Workflow agent.
   - Main-session calls carry neither.
3. **Subagents share the orchestrator's identity.** They carry the orchestrator's `session_id`, and `CLAUDE_PROJECT_DIR` is the main repo even when the subagent's `cwd` is its worktree.
4. **Plugin hooks are active in every session where the plugin is enabled, in every project.** A single plugin hook can't be disabled on its own.
5. **`if: Bash(...)` matching is best-effort,** and `;` chains are not documented as split. This design uses no `if:` on Bash and parses the command itself.
6. **Mods** are plugin JS modules (`"modules"` in `hooks/hooks.json`).
   - They run in-process in a shared worker and can draw UI: a status line, a band or a pane, on a timer. They can run processes.
   - They run unsandboxed, in the terminal and Desktop only. Their API "can change between releases"; the generated `.d.ts` is authoritative.
7. **`PermissionRequest` can allow a call.** A session that can't prompt and gets no decision denies it. `WorktreeCreate` would replace git's worktree creation; it is out of scope.

Still to be probed in the plan; the design states a fallback for each:
- (P1) whether `UserPromptSubmit` or `UserPromptExpansion` carries the `/foreman` text;
- (P2) whether `SubagentStart` input includes the prompt or a readable transcript;
- (P3) whether a resumed session keeps its `session_id`;
- (P4) whether `FOREMAN_HOME` set in the shell profile reaches hook processes;
- (P5) the exact `SessionEnd` `reason` values.

## Scope

There are nine pieces. Pieces 1–8 are `command` hooks served by one script; piece 9 is a mod.

| # | Name (config id) | Event | Active | Applies to |
|---|---|---|---|---|
| 1 | Gate before push (`gate_before_push`) | PreToolUse | during a run | everyone; exempt diffs allowed |
| 2 | Worker guard (`worker_guard`) | PreToolUse | during a run | subagents |
| 3 | Merge discipline (`merge_discipline`) | PreToolUse | during a run | everyone |
| 4 | Trailer guard (`trailer_guard`) | PreToolUse | in a foreman repo | everyone |
| 5 | Backlog row check (`backlog_check`) | PostToolUse | in a foreman repo | everyone; context only |
| 6 | Worker briefing (`worker_briefing`) | SubagentStart | worker tag present | tagged workers |
| 7 | Report check (`report_check`) | SubagentStop | worker tag present | tagged workers; blocks once |
| 8 | Script allow (`script_allow`) | PermissionRequest | during a run | everyone; exact scripts, strict arguments |
| 9 | Wave status (`status_pane`) | Mod | during a run | the session that started the run |

A tenth piece, a **spec gate**, is proposed under Interactions and isn't part of this scope until the user approves it.

## Non-goals

- No run ledger. The user declined it.
- No `WorktreeCreate`, no plan-mode warning, no `prompt`- or `agent`-type hooks, and no mod-registered tools.
- No enforcement of anything the user runs. Hooks see only Claude's tool calls; `! <command>` is the user's override.
- No guarding of Bash-driven writes to the backlog or rules files (a `python3 -c` that edits them). `bk.py` validates its own writes.
- No defence against deliberate obfuscation (`eval`, base64). These are guardrails.

## Architecture

```
hooks/hooks.json        settings hooks (pieces 1-8) + "modules": ["./status.mjs"] (piece 9)
scripts/hooks.py        dispatcher: python3 hooks.py <event>, JSON on stdin; also `hooks.py run-end`
scripts/shellparse.py   split a Bash command into simple commands (below); used by hooks.py
scripts/state.py        main_checkout(path), repo_key(main), state_dir(main); CLI: state.py <path> → prints state_dir
hooks/status.mjs        the wave status mod
scripts/gate-lock.sh    + mints gate receipts (piece 1); state dir via state.py
scripts/wt-setup.sh, scripts/review.sh   read config from the main checkout (piece 8)
```

### hooks.json

Every handler is `type: command`, in exec form: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/hooks.py <event>`. `timeout` is 10, or 30 for SubagentStop.
- **UserPromptSubmit** and **UserPromptExpansion** → `run-start`. Both are registered and handled idempotently (P1).
- **SessionEnd** → `run-end`.
- **PreToolUse** with matcher `Bash` and **no `if:`** → `pretool`. On a Bash command that names no `git`, `gh` or `gt`, the script returns within milliseconds of its own start. It does no git work for those.
- **PreToolUse** with matcher `Edit|Write|MultiEdit|NotebookEdit` → `pretool`.
- **PostToolUse** with matcher `Edit|Write|MultiEdit` → `posttool`.
- **SubagentStart** → `subagent-start`.
- **SubagentStop** → `subagent-stop`.
- **PermissionRequest** with matcher `Bash` → `permission`.

### Command parsing (`shellparse.py`)

- **Splitting:** `shlex.shlex(cmd, posix=True, punctuation_chars=';&|<>()')` with `whitespace=' \t'`. A newline is a separator, so it becomes a token. The command is split on `;`, `&&`, `||`, `|`, `&` and newline into simple commands.
- **Prefixes:** leading `VAR=value` assignments are dropped. `bash -c '<s>'` and `sh -c '<s>'` are recursed into once.
- **Program name:** `git` or `gh` after an optional `command`/`env`/`nice`; `gt`. The subcommand is the first non-option word after the global options (`git -C <path>`, `-c k=v`).
- **What every Bash piece sees:** every simple command, each with its effective directory: a preceding `cd <path>` in the same chain changes it; `git -C <path>` overrides it.
- **On a parse error** (unbalanced quotes), the pieces get the raw string. Each piece says what it does then.

### `hooks.py` common path

1. Parse stdin. On a parse error, exit 0.
2. Find the repo.
   - **Edit-family tools** use `dirname(tool_input.file_path)`. **Bash** uses each simple command's effective directory. **Other events** use `cwd`.
   - `git rev-parse --show-toplevel` gives the checkout. `main_checkout` is the first `git worktree list --porcelain` entry.
   - "In a foreman repo" means `cfg.config_path(main)` is not None, ignoring `FOREMAN_CONFIG`. Otherwise exit 0.
3. Drop any piece listed in the config's `hooks_disabled`.
4. **Run active:** the file `state_dir/runs/<session_id>.json` exists, and its `touched` time is within 48 hours.
5. **Subagent:** the input has `agent_id`.
6. **Run every applicable piece** and collect the denies.
   - If any piece denies, print one PreToolUse deny whose reason joins all of them: `"foreman: <rule> — <what to do>; …"`.
   - A run-only piece that matches touches the marker, which sets `touched` to now.
7. **Unexpected exceptions** print `foreman hook error: <type>: <msg>` to stderr and exit 0, which fails open.

**State.** `state_dir(main)` = `$FOREMAN_HOME/<repo_key(main)>`, with FOREMAN_HOME defaulting to `~/.claude/foreman`. `repo_key` = the first 12 hex digits of sha1(origin url + newline + realpath(main)).
- `ui.py` moves to this function, and SKILL.md's `/foreman ui` passes `--repo <main checkout>`, not `$PWD`.
- `gate-lock.sh` gets the dir from `python3 state.py <toplevel>`.
- `run-start` records the resolved `state_dir` in the marker. If `FOREMAN_HOME` differs between the Bash tool and hooks (P4), hooks.py also checks the dir named in the marker.
- Directories are 0700 and files 0600.

## The run marker

- **`run-start`** fires when the prompt contains a token starting with `/foreman` or `/foreman:foreman` (so `/loop 2h /foreman --auto-merge` counts). The next word must not be `ui` or `init`.
  - It writes `runs/<session_id>.json` = `{"started", "touched", "args", "state_dir"}`. Repeats update `args` and `touched`.
  - It never blocks.
- **`run-end`** has two triggers.
  - `SessionEnd` with reason `clear` or `logout` deletes the marker (P5). Other reasons keep it, so a resumed mid-wave session stays guarded, depending on P3.
  - The skill's termination steps run `python3 $FOREMAN/hooks.py run-end`. It reads the session id from the newest marker of this repo whose `args` match, and deletes it.
- **Expiry:** a marker untouched for 48 hours is ignored and pruned on the next `run-start`.
- **While a marker exists** (between `run-end` steps, or when a run is abandoned):
  - subagents can't merge, force-push or rebase, or edit the backlog, rules or config;
  - everyone needs a gate receipt to push non-exempt diffs, and merges need `--match-head-commit`;
  - piece 8 allows the scripts;
  - the status band shows.

## The pieces

### 1. Gate before push

- **Triggers:**
  - a simple command `git push`, except a delete: `--delete`, `-d`, or a `:<branch>` refspec;
  - `gh pr create`;
  - `gt submit`.
- **The push's tree** is `git rev-parse HEAD^{tree}` in the command's effective directory. The limit: an explicit refspec source other than HEAD is still judged by HEAD.
- **Allowed when any of these holds:**
  - **(a)** a receipt `state_dir/gate/<tree>` exists;
  - **(b)** every path of `git diff --name-only --no-renames $(git merge-base origin/<default> HEAD) HEAD` is exempt;
  - **(c)** a "last receipt" pointer exists for this checkout (`state_dir/gate/last/<sha1(toplevel)>`, naming the last receipted tree), and every path of `git diff --name-only --no-renames <that tree> HEAD^{tree}` is exempt. This covers a docs-only or bookkeeping push on a branch that already has code.
- **Exempt paths:**
  - anything matching `gate_exempt`, using gitignore-style patterns (`**/` matches zero or more directories);
  - always `backlog_path` and anything matching `rules_glob`.
  - The default `gate_exempt` is `["docs/**", "**/*.md"]`. `.claude/**` is not exempt by default, because it can hold code (`pw-lib.mjs`, hooks, settings).
  - `<default>` comes from `origin/HEAD`, falling back to `main`.
- **When none holds:**
  - The push is denied: "foreman: no passing gate for this tree — run `<plugin>/scripts/gate-lock.sh <owner> -- '<gate>'` from the checkout's top level, commit every file the gate changed, then push."
  - When a last receipt exists, the reason also lists up to 10 paths changed since it, so a gate that reformats files outside the batch is visible.
- **When the diff can't be determined** (no `origin/<default>`, or a failing git), the push is denied with the same reason. This is the stated fail-closed exception.
- **Receipts (minted by `gate-lock.sh`):**
  - Before running the command, `gate-lock.sh` changes to `git rev-parse --show-toplevel`.
  - It mints only when ALL of these hold:
    - the command after `--` is exactly one argument;
    - that argument equals the config's `gate`;
    - there was no `--name`;
    - the exit code is 0.
    Extra gates never mint, because their `when:` can't be checked.
  - **The tree** is that of the working copy at the end:
    1. copy the checkout's real index (`git rev-parse --git-path index`) to a temp file;
    2. run `GIT_INDEX_FILE=<temp> git add -A`;
    3. run `git write-tree`.
    Starting from the real index keeps force-added ignored files and the existing blobs, so clean/eol/LFS filters aren't re-run on unchanged files.
  - **Contents:** `{"command", "owner", "time", "toplevel"}`. It also updates the `gate/last/<sha1(toplevel)>` pointer. Receipts older than 14 days are pruned.

### 2. Worker guard (subagents during a run)

- **Bash simple commands denied:**
  - `gh pr merge` and `gt merge`;
  - `git push` with `--force`, `-f`, `--force-with-lease` or a `+` refspec;
  - `git rebase`;
  - `gt modify` once the branch has an upstream ("never amend or force-push once the PR is open").
  - Revision 1's `git reset --hard` is dropped: it has no contract line, and it is a legitimate local recovery.
- **Edit-family calls denied:** when the target is the backlog file, matches `rules_glob`, or is the foreman config file. Each target is resolved against its own checkout.
- **Each reason quotes the contract line.**

### 3. Merge discipline (everyone during a run)

`gh pr merge` and `gt merge` are denied when:
- the command lacks `--match-head-commit <sha>`: "merge the SHA you verified";
- it has `--delete-branch` or `-d` AND the PR's head branch is checked out in a worktree of this repo (`gh pr view <n> --json headRefName`, matched against `git worktree list --porcelain`): "remove the worktree first, then delete the branch".

Serial step 12 (`--delete-branch`, no worktree) passes that second check, but it must add `--match-head-commit`. SKILL.md changes accordingly.

### 4. Trailer guard (any session in a foreman repo)

- **What is checked:**
  - **Message text:** for `git commit`, the `-m` and `--message` values plus the file named by `-F`, `--file` or `--message-file`. For `gh pr create|edit|comment|merge|review`, the `--title`, `--body`, `--subject` and `-b` values plus `--body-file`/`-F`. For `gt create|modify|submit`, `-m`/`--message`.
  - **File paths** resolve against the simple command's effective directory; absolute paths are used as given. `-F -` reads the text from the command's heredoc, which is part of the command string.
- **Rule:** deny when any `forbid_trailers` entry starts a line of the message or body: `(?im)^\s*<entry>`, case-insensitive. Mentions inside a sentence don't count.
- **`gh pr merge --squash` with no body:** the squash body is built from the PR's commit messages. The hook reads `gh pr view <n> --json commits` and denies if any commit message has a forbidden line. The reason names the commit and says to pass `--body` without it. (The review suggested denying every squash without a body; this narrows it.)
- **With an empty list,** the piece does nothing.

### 5. Backlog row check (any session in a foreman repo)

- **Trigger:** after Edit, Write or MultiEdit whose `file_path`, resolved in its own checkout, equals that checkout's `backlog_path`. This includes an isolated bookkeeping worktree.
- **Check:** `backlog.parse(text, prefix)` collects `line_problems` for every row-like line, plus `duplicates()`.
- **Output:** when there are problems, `additionalContext`: "foreman: the backlog now has N problem rows: <up to 10, `id: problem`>. Fix them before committing." Problems that were already there are reported too.
- **It never blocks.**

### 6. Worker briefing (SubagentStart)

- **Who gets it:** `agent_type` must be in `worker_agent_types`, default `["general-purpose", "workflow-subagent"]`. The subagent's prompt must also begin with the worker tag `foreman-worker: <batch>`, read from the input or the transcript (P2).
  - With no readable tag, there is no briefing. Revision 1's "brief every worker-type subagent" fallback is dropped.
  - Workers then still get the contract the way they do today, in the dispatch prompt. SKILL.md keeps it there until P2 is confirmed.
  - The briefing does not need a run marker: the tag is the proof of a foreman worker.
- **Content,** as `additionalContext`:
  - the `### Worker contract` section read from the installed `SKILL.md`, rendered. `$FOREMAN` becomes `${CLAUDE_PLUGIN_ROOT}/scripts`; `{{key}}` values come from the config; `<prefix>` becomes `id_prefix`.
  - then `gate`, `forbid_trailers` and `shared_resources`.
  - Cap: 8 KB. Over the cap, `shared_resources` is truncated first, never the contract.
- **The briefing records `agent_id → batch`** in `state_dir/runs/<session_id>.agents.json`. The proposed spec gate needs it.
- **Dispatch prompts keep:** the batch, rows, owned files, test-data id prefix, the resolved plugin path, and W6's inline rule notes from an unmerged bookkeeping PR.

### 7. Report check (SubagentStop)

- **Same who-test as piece 6.** The tag comes from the first user message in `agent_transcript_path`. With no tag, the stop is allowed.
- **When `stop_hook_active` is true,** the stop is allowed. The check blocks at most once per stop.
- **If the last message starts with `SPEC READY `,** the stop is allowed. That is the spec-phase hand-off, not a final report.
- **Otherwise,** it takes the last fenced `json` block in `last_assistant_message`:
  - **Missing:** block with "end your report with ONE fenced json block in the worker contract's format, even if empty".
  - **Invalid JSON:** block, quoting the parse error.
  - **Valid:** drop `rule_notes`, because they may quote rules text from a bookkeeping PR that isn't merged yet. Add `"pr": 0` and run `bk.py <the worker's checkout> --dry-run <file>`.
    - The worker's checkout is the toplevel of `cwd`; its branch started from `origin/<default>`.
    - It blocks only when stdout contains `PROBLEMS (nothing written)`, listing those problem lines.
    - Any other non-zero exit, such as a crash, is allowed, with a stderr warning.

### 8. Script allow (PermissionRequest, during a run)

Only Bash is considered, and anything not matching every rule below gets no decision; normal handling then applies. This piece never denies.
- **The text** contains no newline or `\r`, no `$(`, no backtick, and no `${`.
- **Parsing** with `shellparse`: exactly one simple command, optionally preceded by one `cd <path> &&`.
- **The executable,** after an optional `python3`, `bash` or `sh`, is one of `${CLAUDE_PLUGIN_ROOT}/scripts/{gate-lock.sh,wt-setup.sh,review.sh,bk.py}` by exact absolute path.
- **Per script:**
  - **`gate-lock.sh`:** the arguments are `<owner> -- <one argument equal to the config's gate>`; `--name` is not allowed.
  - **`wt-setup.sh` and `review.sh`:** the target directory is a worktree of this repo. For `wt-setup.sh` that is the path argument; for `review.sh` it is the effective cwd. Both scripts now read config from that directory's MAIN checkout, never from the directory itself. Otherwise a planted config in a worktree could run any `worktree_install` or `custom` reviewer command.
  - **`bk.py`:** subagents only with `--dry-run`.
- **The config file is write-protected for subagents** by piece 2.
- **On a match:** `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}`.
- **Other commands:** the README gives a `permissions.allow` snippet for the rest of the worker contract: `git push`, `gh pr create|view|checks`, `gh api`, `node --check`. W3 adds a preflight: before launching, the orchestrator checks that those permissions are present and warns if not.

### 9. Wave status (mod `hooks/status.mjs`)

- **When:**
  - it registers on `session.start`;
  - it shows only while this session has a marker. It finds the marker by running `python3 <plugin>/scripts/state.py <cwd>`, which prints `state_dir`, then reading `runs/<session_id>.json` and its `state_dir`;
  - it checks every 60 seconds;
  - `status_pane: false` or `hooks_disabled: [status_pane]` turns it off. Config is read with `python3 cfg.py <main> <key>`, so the mod never parses YAML or derives keys in JS.
- **Rows:** worktrees under `<main>/.claude/worktrees/` only, which are Claude Code's isolation worktrees. Other worktrees of the repo are not shown.
- **Data,** refreshed every 60 seconds while visible:
  - `git worktree list --porcelain`;
  - per branch, `gh pr list --head <branch> --state all --json number,state,headRefOid,mergeStateStatus,statusCheckRollup`;
  - one GraphQL query for unresolved threads;
  - `required_ci` from the config.
- **Display:** a band above the prompt, one line per worker: worktree name, PR, state, head SHA (9 characters), CI, bots, unresolved threads and merge state. Plus a one-line `$.ui.status` summary.
- **Errors:** each refresh is wrapped. A failure shows "foreman status unavailable: <reason>" and keeps the last good table.
- **API binding:** the plan binds the exact calls against the generated `.d.ts`. The formatter is a pure exported function.

## Config keys (new)

| key | default | meaning |
|---|---|---|
| `forbid_trailers` | `[]` | lines that must never start a commit, PR or comment message (piece 4) |
| `gate_exempt` | `["docs/**", "**/*.md"]` | gitignore-style patterns a push may touch without a gate receipt; `backlog_path` and `rules_glob` are always exempt (piece 1) |
| `worker_agent_types` | `["general-purpose", "workflow-subagent"]` | subagent types eligible for pieces 6 and 7 |
| `hooks_disabled` | `[]` | piece ids to turn off in this repo |
| `status_pane` | `true` | piece 9 |

`templates/foreman.config.md` documents them. vacancy-vibe sets `forbid_trailers: [Co-Authored-By, Claude-Session, Generated with]` at switch-over.

## SKILL.md changes

- **W3:**
  - every worker prompt begins with `foreman-worker: <batch>`;
  - a permissions preflight;
  - the contract stays in the dispatch prompt until probe P2 confirms the briefing.
- **Worker contract:**
  - "commit every file the gate changed and list it in your report";
  - a line saying hooks enforce gate receipts, no merge, no force-push, no rebase, no backlog/rules/config edits, trailers and the report check.
- **Gate steps** run `gate-lock.sh <owner> -- '<gate>'` with the exact configured string, from the checkout's top level.
- **Step 12 and W4.6:** `gh pr merge --match-head-commit <verified sha>`. Step 12 deletes the branch after the sync, when no worktree holds it.
- **W5:** a bookkeeping diff with non-exempt paths is gated in its worktree after `{{worktree_setup}}`.
- **Termination** (W6 stop and the autonomous loop's end) runs `python3 $FOREMAN/hooks.py run-end`.
- **`/foreman ui`** passes `--repo <main checkout>`.
- **A "Hooks" paragraph** lists the pieces, `hooks_disabled`, and the `! <command>` override.

## Interactions with the spec-phase design (`feat/spec-phase`)

- **Piece 7** allows the `SPEC READY` stop. That rule is in piece 7 above.
- **Piece 6** briefs the contract as the spec-phase design rewrites it: Step 2 becomes the spec phase.
- **Proposed piece 10, spec gate** (not in scope until approved). During a run, it denies a tagged worker's Edit or Write outside `spec_dir` until its batch's spec is released.
  - The batch comes from piece 6's `agent_id → batch` map.
  - Release is a file the orchestrator writes at W3.6: `state_dir/runs/<session_id>.released/<batch>`.
  - It fails open when the map has no entry. That case is P2: no tag readable at SubagentStart.

## Error handling

- **Fail open:** every hook fails open on its own errors, exiting 0 with a stderr note. The exception is piece 1's undeterminable diff, which denies.
- **Git or gh failures inside piece 3's worktree check** or piece 4's commit read mean that check doesn't apply. The call is allowed, with a stderr note.
- **The mod** catches every refresh.

## Security notes

These are guardrails, not a security boundary. Piece 8 is the only piece that grants anything, and it allows only exact paths, with strict arguments, during a run. Both scripts it can run with repo-controlled commands now read config from the main checkout, which subagents can't edit through the tool calls these hooks see. The mod runs unsandboxed, like every mod, and only reads (git, gh) and draws. The README says so.

## Testing

- **`tests/test_shellparse.py`:**
  - `a; git push`, `a && git push`, `a\ngit push`, `cd wt && git push`;
  - `git -C wt push`, `bash -c 'git push'`, `VAR=1 git push`;
  - quoted operators staying one token, and an unbalanced quote.
- **`tests/test_hooks.py`** feeds recorded hook JSON to `hooks.py` in temp repos, with a temp `FOREMAN_HOME` and a fake `gh`, one row per case:
  - **Scoping:** no config means silent, and `FOREMAN_CONFIG` set means it is ignored; no marker silences the run-only pieces; `agent_id` present versus absent.
  - **Piece 1:**
    - receipt present, missing, an exempt-only diff, and an exempt diff since the last receipt;
    - a rename of code into `docs/` is not exempt;
    - `gate; git push` and `cd wt && git push` are both judged;
    - a delete push is allowed; an undeterminable diff is denied;
    - minting: the gate string versus a multi-argument form, versus `--name`, versus a non-gate command;
    - a receipt tree equals `HEAD^{tree}` with a force-added ignored file, and with a CRLF blob under `text=auto`;
    - a gate run from a subdirectory runs at the top level.
  - **Pieces 2 and 3:** each denial, plus a normal push and merge allowed. `-d` is allowed when no worktree holds the branch.
  - **Piece 4:** a trailer line in `-m`, in a `-F` file in the scratchpad, and in a heredoc; a mid-sentence mention allowed; a squash whose commits carry a trailer.
  - **Piece 5:** a bookkeeping-worktree edit is checked; a clean backlog returns nothing.
  - **Pieces 6 and 7:**
    - briefing with and without the tag; rendered `$FOREMAN`;
    - the report check: missing block, invalid JSON, a refused block, a valid block, `rule_notes` ignored, a bk.py crash allowed, `stop_hook_active`, and `SPEC READY`.
  - **Piece 8:**
    - an exact path allowed;
    - no decision for each of: a newline, `$(`, `; rm`;
    - no decision for gate-lock with a non-gate command or `--name`, for wt-setup on a non-worktree directory, and for bk.py without `--dry-run` from a subagent.
  - **Run marker:** `/loop … /foreman`, `ui`/`init` skipped, `SessionEnd` reasons, `hooks.py run-end`, and the 48-hour expiry.
- **`tests/test_status.mjs`** tests the pure formatter.
- **A headless end-to-end** (manual, documented) loads the plugin with `--plugin-dir` in a throwaway repo with a fake `gh` and haiku workers. Its checks:
  - P1–P5 answered;
  - a worktree subagent's `gh pr merge` denied;
  - a receipt-less push denied, then allowed after the gate;
  - a tagged worker briefed;
  - a worker without a `json` block blocked once.
- **The mod:** `claude plugin validate`, plus one real wave watched in a terminal.

## Version

0.4.0. The README gains a "Hooks" section: what each piece does, `hooks_disabled`, the limits above, the `permissions.allow` snippet, and that the mod runs unsandboxed.
