# Foreman hooks: design

Status: draft for review, revision 4 (2026-10-02). The findings so far:
- revision 1: 3 Critical, 13 Important, 5 Minor;
- revision 2: 2 Critical, 3 Important, 6 Minor, plus 8 partials;
- revision 3: 1 Critical, 4 Important, 8 Minor.

Revision 4 makes the scan anchor on any git/gh/gt word, keeps quoted newlines and heredocs from splitting commands, makes the run and release controls orchestrator-only, and makes the spec gate opt-in per batch. Branch `feat/hooks`. Target version 0.4.0. This depends on the parallel spec-phase design (`feat/spec-phase`, see Interactions).

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
- (P6) whether `SubagentStart`'s `agent_id` equals the same worker's `PreToolUse` `agent_id`;
- **(P7) whether TaskStop fires `SubagentStop`.** Fallback: the orchestrator runs `hooks.py stopping <batch>` before every TaskStop, so piece 7 never blocks a stopping worker;
- (P1) whether `UserPromptSubmit` or `UserPromptExpansion` carries the `/foreman` text;
- (P2) whether `SubagentStart` input includes the prompt or a readable transcript;
- (P3) whether a resumed session keeps its `session_id`;
- (P4) whether `FOREMAN_HOME` set in the shell profile reaches hook processes;
- (P5) the exact `SessionEnd` `reason` values.

## Scope

There are ten pieces. Pieces 1–8 and 10 are `command` hooks served by one script; piece 9 is a mod.

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
| 10 | Spec gate (`spec_gate`) | PreToolUse | during a run | tagged workers whose batch is not released |

The user approved the spec gate on 2026-10-02. It enforces the spec-phase rule "no code before the batch's spec is released".

## Non-goals

- No run ledger. The user declined it.
- No `WorktreeCreate`, no plan-mode warning, no `prompt`- or `agent`-type hooks, and no mod-registered tools.
- No enforcement of anything the user runs. Hooks see only Claude's tool calls; `! <command>` is the user's override.
- No guarding of Bash-driven writes to the backlog or rules files (a `python3 -c` that edits them). `bk.py` validates its own writes.
- No defence against deliberate obfuscation (`eval`, base64). These are guardrails.

## Architecture

```
hooks/hooks.json        settings hooks (pieces 1-8, 10) + "modules": ["./status.mjs"] (piece 9)
scripts/hooks.py        dispatcher: python3 hooks.py <event>, JSON on stdin; also the CLIs `hooks.py run-end` and `hooks.py release <batch> <sha>`
scripts/shellscan.py    fail-closed command detection (below); used by hooks.py
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
- **PostToolUse** with matcher `Edit|Write|MultiEdit` → `posttool`. Also matcher `Bash` → `posttool`, which acts only on the main session's `hooks.py run-end` and exits at once otherwise.
- **SubagentStart** → `subagent-start`.
- **SubagentStop** → `subagent-stop`.
- **PermissionRequest** with matcher `Bash` → `permission`.

### Command detection (`shellscan.py`): scan, don't parse

Two review rounds showed that parsing Bash keeps missing forms. Detection is therefore a fail-closed scan, and it errs toward treating text as a matching command.

**Considered and rejected: git-native hooks** (`pre-push`, `commit-msg`).
- Config-based git hooks need git ≥ 2.54, but foreman's scripts call Apple's `/usr/bin/git` 2.50.
- husky owns `core.hooksPath` in vacancy-vibe.
- Every repo would need an install step.

**Scan steps for one Bash command string**, in order:
1. **Heredocs first:**
   - Find each `<<[-~]? *['"]?WORD['"]?` and its body, up to the line equal to `WORD`. `<<<` is a here-string, not a heredoc.
   - A heredoc whose command is a shell (`bash`, `sh` or `zsh`, with any options, including `-s`) is scanned recursively as commands.
   - Every other heredoc body is blanked and attributed to the segment holding its `<<`. Piece 4 reads it from there.
2. **Recursion:** scan recursively, once per level:
   - the argument of a shell's `-[a-z]*c[a-z]*` option (`-c`, `-lc`, `-ec`, …);
   - `eval`'s arguments;
   - every `$(…)` and backtick body, including those inside double quotes.
3. **Blanking:** quoted strings (single and double, with their newlines) and `#` comments that start a word outside quotes are blanked with a non-space filler character (`\x00`). Offsets are kept.
   - Newlines inside quotes and heredocs are therefore never split points.
   - The original text is kept for argument extraction.
4. **Split** the blanked text into segments at:
   - `;`, `&&`, `||`, `|`, `|&` and `&`;
   - newlines;
   - `(`, `)`, `{` and `}`;
   - the words `then`, `do`, `else`, `elif`, `if`, `while`, `until`, `!` and `time`.

   A `cd` inside `( … )` scopes only to the end of that group.
5. **Candidates anchor on the program word, not on a wrapper list.**
   - In each segment, every word whose basename is `git`, `gh` or `gt` starts a candidate. Basename handles `/usr/bin/git`, `'git'` and `\git`.
   - Wrappers before it (`nohup`, `timeout 60`, `sudo`, `xargs -n1`, `nice -n 5`, `env -i`, `command -p`, `stdbuf`, `caffeinate`, `find … -exec`) don't matter.
   - **Accepted false denies:** an unquoted `echo git push` is a candidate.
   - **Assignments and `env`:** a `VAR=` assignment or an `env` anywhere before the candidate is recorded, because piece 8 needs it.
6. **Classify each candidate, using `shlex.split` words** from the original text of the candidate's segment:
   - **`git`:** skip global options (`-C <p>`, `-c <k=v>`, `--git-dir=…`, `--work-tree=…`, `--no-pager`, other `-x`/`--x`); the next word is the subcommand.
   - **`gh`:** skip global flags (`-R <repo>`, `--repo <repo>`) before and after `pr`; then `gh pr <verb>`. Also `gh api` with a path matching `pulls/\d+/merge`, or a body containing `mergePullRequest`, classifies as **`gh pr merge`**.
   - **`gt`:** `gt <verb>`.
   - **Effective directory:** from the last preceding in-scope `cd <p>`, then `git -C <p>`. Unknown with `--git-dir` or `--work-tree`.
   - **Arguments:** from `shlex.split`. If that fails, they are unknown.
7. **On failure** (unbalanced quotes, an unterminated heredoc), pieces 1–4 regex-scan the raw text (`\bgit\b[^\n]*\bpush\b`, `\bgh\b[^\n]*\bmerge\b`, `\bgt\s+(submit|merge|modify)\b`, …). Every match is treated as a command with unknown arguments. Piece 8 gives no decision.

**Tests** pin every form the three reviews found:
- the wrappers above;
- `x="$(git push)"`, `` x=`git push` ``, `bash -lc 'git push'`, `bash <<'EOF'⏎git push⏎EOF`;
- `gh pr -R o/r merge 1`, `gh api -X PUT repos/o/r/pulls/1/merge`;
- `git -C "my dir" push`;
- the Claude Code PR form `gh pr create --title t --body "$(cat <<'EOF'⏎…⏎EOF⏎)"`, which must give known arguments;
- `git commit -m "fix: git push retry"`, which must not be a push;
- `(cd wt && git push); git push`, where the second push runs in the original directory;
- plus every revision-3 form.

### `hooks.py` common path

1. Parse stdin. On a parse error, exit 0.
2. Find the repo.
   - **Edit-family tools** use `dirname(tool_input.file_path)` (`notebook_path` for NotebookEdit). **Bash** uses each simple command's effective directory. **Other events** use `cwd`.
   - `git rev-parse --show-toplevel` gives the checkout. `main_checkout` is the first `git worktree list --porcelain` entry.
   - "In a foreman repo" means `cfg.config_path(main, use_env=False)` is not None. `use_env=False` is a new parameter: hooks ignore `FOREMAN_CONFIG`, while the scripts and tests keep using it. Otherwise exit 0.
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
- Every state lookup in hooks.py checks `state_dir(main)` and also the default `~/.claude/foreman/<key>` when they differ. This covers a `FOREMAN_HOME` set only for the Bash tool (P4).
- Directories are 0700 and files 0600.

## The run marker

- **`run-start`** fires when the prompt begins with `/foreman` or `/foreman:foreman`, or with `/loop [<interval>] /foreman…`. The next word must not be `ui` or `init`. A mention elsewhere in the prompt doesn't start a run.
  - It writes `runs/<session_id>.json` = `{"started", "touched", "args"}`. Repeats update `args` and `touched`.
  - It never blocks.
- **`run-end`** has two triggers. Both act on the calling session's own marker, never another session's:
  - `SessionEnd` with reason `clear` or `logout` (P5). Other reasons keep the marker, so a resumed mid-wave session stays guarded (P3).
  - The skill's termination steps run `python3 <plugin>/scripts/hooks.py run-end`:
    - W6's stop;
    - the autonomous loop's end;
    - serial step 12 after a manual pass.

    `pretool` recognises this exact call (a single segment: `python3` plus that path plus `run-end`). For the main session, `posttool` then deletes `runs/<session_id>.json`, using the hook input's own session id. Piece 8 allows the call.
  - **Only from the main session:** `run-end`, `release` and `stopping` take effect only when the hook input has no `agent_id`. A subagent's call is denied ("only the orchestrator ends a run or releases a spec"). Subagents share the `session_id`, so without this a worker could unguard the run or release itself.
  - **Timing:** `pretool` denies the subagent's call before anything is deleted. For the main session, the marker is deleted in `PostToolUse`, after the call ran, so piece 8's PermissionRequest still sees an active run.
  - **The fast path** treats a command naming `hooks.py` like one naming `git`, `gh` or `gt`.
- **Expiry:** a marker untouched for 48 hours is ignored and pruned on the next `run-start`.
- **While a marker exists:**
  - subagents can't merge, force-push or rebase, or edit the backlog, rules or config;
  - everyone needs a gate receipt to push a non-exempt diff;
  - merges need `--match-head-commit`;
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
- **When the diff can't be determined** (no `origin/<default>`, a failing git, unknown arguments, or an unknown effective directory), the push is denied with the same reason. This is the stated fail-closed exception.
- **Refspecs** are read from the segment's arguments:
  - **a delete** (`--delete`, `-d`, or a refspec starting with `:`) is allowed;
  - **a `+` refspec** is a force push (piece 2).
- **Receipts (minted by `gate-lock.sh`):**
  - Before running the command, `gate-lock.sh` changes to `git rev-parse --show-toplevel`.
  - It runs the command after `--` the same way with or without `--name`, from the top level.
  - **Untracked files:** before the command, it records the untracked, non-ignored paths. After the command, any NEW untracked path (coverage output, reports) is excluded from the receipt tree and listed on stderr ("gate created untracked files; they are not part of the receipt"). A gate artifact therefore never needs to be committed.
  - It mints only when ALL of these hold:
    - the command after `--` is exactly one argument;
    - that argument equals the config's `gate`;
    - there was no `--name`;
    - the exit code is 0.
    Extra gates never mint, because their `when:` can't be checked.
  - **Matching:** a receipt matches a push when `HEAD^{tree}` differs from the receipt tree only by paths ABSENT from HEAD. Those are untracked files the gate saw and nobody committed: scratch files, or leftovers from before the gate. Any other difference means the gate didn't see this code.
  - **The tree** is that of the working copy at the end:
    1. copy the checkout's real index (`git rev-parse --git-path index`) to a temp file;
    2. run `GIT_INDEX_FILE=<temp> git add -A -- . ':!<new untracked paths>'`;
    3. run `git write-tree`.
    Starting from the real index keeps force-added ignored files and the existing blobs, so clean/eol/LFS filters aren't re-run on unchanged files.
  - **Contents:** `{"command", "owner", "time", "toplevel"}`. It also updates the `gate/last/<sha1(toplevel)>` pointer. Receipts older than 14 days are pruned.

### 2. Worker guard (subagents during a run)

- **Bash commands denied:**
  - `gh pr merge` (including the `gh api` merge forms) and `gt merge`;
  - `git push` with `--force`, `-f` or any combined short flag containing `f` (`-uf`), `--force-with-lease`, `--mirror`, or a `+` refspec;
  - `git push` whose destination ref is the default branch (`HEAD:main`, `main`, `refs/heads/<default>`);
  - `git rebase`;
  - `gt modify` once the branch has an upstream ("never amend or force-push once the PR is open").
  - Revision 1's `git reset --hard` is dropped: it has no contract line, and it is a legitimate local recovery.
- **Edit-family calls denied:** when the target is the backlog file, matches `rules_glob`, or is the foreman config file. Each target is resolved against its own checkout.
- **Each reason quotes the contract line.**

### 3. Merge discipline (everyone during a run)

- **`gh pr merge`** is denied when:
  - it lacks `--match-head-commit <sha>`: "merge the SHA you verified";
  - it has `--delete-branch` or `-d`, AND the PR's head branch is checked out in a worktree OTHER than the one the command runs in: "remove that worktree first, then delete the branch". gh switches the current worktree to the default branch itself, so serial step 12's main checkout is not counted.
- **`gt merge`** is denied during a run: "merge with `gh pr merge --match-head-commit <sha>`". It merges through Graphite's queue and can't pin a SHA.
- **The PR's head branch** comes from one `gh pr view <n> --json headRefName,commits` call, with a 4 second subprocess timeout, shared with piece 4. Piece 2's local deny of a subagent's merge is decided first; when it stands, no network call is made.
- **Serial step 12** reads `gh pr merge <n> --squash --match-head-commit <verified sha> --delete-branch`, run from the main checkout. SKILL.md changes accordingly.

### 4. Trailer guard (any session in a foreman repo)

- **The message text checked:**
  - **`git commit`:** the `-m`/`--message` values, plus the file named by `-F`/`--file`.
  - **`gh pr create|edit|comment|merge|review`:** the `-t`/`--title`, `-b`/`--body` and `--subject` values, plus `-F`/`--body-file`.
  - **`gt create|modify|submit`:** `-m`/`--message`.
  - **Heredoc bodies** belong to the command they feed.
  - **With unknown arguments,** the segment's raw text.
- **Files:** a file path resolves against the effective directory; absolute paths are used as given. A file that doesn't exist yet (written earlier in the same command) is read from the heredoc that writes it if there is one, and otherwise skipped with a stderr note.
- **Rule:** deny when a line of the message starts with a `forbid_trailers` entry after any non-word characters: `(?im)^[^\w\n]*<re.escape(entry)>`, case-insensitive. That catches `🤖 Generated with …`. Mentions inside a sentence don't count.
  - An entry written as `/…/` is a full-line regex instead, e.g. `/Generated with \[Claude Code\]/`. That avoids false denies such as `- Generated with the seed script`. vacancy-vibe's switch-over uses the regex form for that entry.
- **`gh pr merge`** with any method (`--squash`, `--merge` or `--rebase`) lands the PR's commit messages unless a body replaces them.
  - The hook reads the commits from piece 3's shared `gh pr view` call. It verifies the field names (`messageHeadline`, `messageBody`) in the plan.
  - It denies when any commit message has a forbidden line and no `--body`/`--body-file` replaces it (squash), or for merge and rebase, which always keep them. The reason names the commit.
- **With an empty list,** the piece does nothing.

### 5. Backlog row check (any session in a foreman repo)

- **Trigger:** after Edit, Write or MultiEdit whose `file_path`, resolved in its own checkout, equals that checkout's `backlog_path`. This includes an isolated bookkeeping worktree.
- **Check:** `backlog.parse(text, prefix)` collects `line_problems` for every row-like line, plus `duplicates()`.
- **Output:** when there are problems, `additionalContext`: "foreman: the backlog now has N problem rows: <up to 10, `id: problem`>. Fix them before committing." Problems that were already there are reported too.
- **It never blocks.**

### 6. Worker briefing (SubagentStart)

- **Who gets it:** `agent_type` must be in `worker_agent_types`, default `["general-purpose", "workflow-subagent"]`. The subagent's prompt must also begin with the worker tag `foreman-worker: <batch>`, optionally followed by ` spec-phase`, read from the input or the transcript (P2).
  - With no readable tag, there is no briefing. Revision 1's "brief every worker-type subagent" fallback is dropped.
  - Workers then still get the contract the way they do today, in the dispatch prompt. SKILL.md keeps it there until P2 is confirmed.
  - The briefing does not need a run marker: the tag is the proof of a foreman worker.
- **Content,** as `additionalContext`:
  - the `### Worker contract` section read from the installed `SKILL.md`, rendered:
    - `$FOREMAN` becomes the absolute path of the plugin's `scripts` directory. It's never left as a variable, because the Bash tool doesn't have it, and piece 8 gives no decision on `${`.
    - `{{key}}` takes the config value, or the SKILL.md default-table value when the config lacks it;
    - `<prefix>` becomes `id_prefix`;
    - `<batch>` comes from the tag.

    `<scratch>` and `<worktree>` stay as written, followed by a one-line legend: "<scratch> = your scratchpad dir; <worktree> = your current checkout's top level".
  - then `gate`, `forbid_trailers` and `shared_resources`.
  - Cap: 8 KB. Over the cap, `shared_resources` is truncated first, never the contract.
- **The briefing records `agent_id → {batch, spec}`** as one file per agent, `state_dir/batches/<session_id>/agents/<agent_id>`, written atomically (temp file plus rename). `spec` is true when the tag ends with ` spec-phase`. Workers launched in one message can't lose each other's entries. Piece 10 needs it. Batch names are dated, `<YYYYMMDD>-w<n><x>` (spec-phase W2), so they're unique across invocations, and the map isn't keyed by session. Entries are pruned with the batch files.
- **Dispatch prompts keep:** the batch, rows, owned files, test-data id prefix, the resolved plugin path, and W6's inline rule notes from an unmerged bookkeeping PR.

### 7. Report check (SubagentStop)

- **Same who-test as piece 6.** The tag comes from the first user message in `agent_transcript_path`. With no tag, the stop is allowed.
- **When `stop_hook_active` is true,** the stop is allowed. The check blocks at most once per stop.
- **Spec-phase batches** (piece 6 recorded `spec: true`) with no valid release:
  - a stop whose last message has a line starting with `SPEC READY ` is allowed;
  - any other stop is blocked once: "you're in the spec phase: finish the spec and end with `SPEC READY <worktree> <branch> <spec path> <sha>` as the first line".
  - After release, `SPEC READY` no longer skips the check.
- **A stopping batch** (the file `batches/<session_id>/<batch>.stopped` exists, written by `hooks.py stopping <batch>` before a TaskStop) is always allowed to stop (P7).
- **Otherwise,** it takes the last fenced `json` block in `last_assistant_message`:
  - **Missing:** block with "end your report with ONE fenced json block in the worker contract's format, even if empty".
  - **Invalid JSON:** block, quoting the parse error.
  - **Wrong shape:** `hooks.py` validates the shape first:
    - `done`/`append` are lists of `{id: str, note: str}`;
    - `new_rows` is a list of strings;
    - `rule_notes` is a list of `{file, old, new}`.

    On a mismatch it blocks, naming the bad field. bk.py never sees a shape it would crash on.
  - **Valid:** drop `rule_notes`, because they may quote rules text from a bookkeeping PR that isn't merged yet. Add `"pr": 0` and run `bk.py <the worker's checkout> --dry-run --no-fetch <file>` with a 20 second timeout. `--no-fetch` is a new flag that skips bk.py's UI-branch fetch.
    - The worker's checkout is the toplevel of `cwd`; its branch started from `origin/<default>`.
    - It blocks only when stdout contains `PROBLEMS (nothing written)`, listing those problem lines.
    - Any other non-zero exit, such as a crash, is allowed, with a stderr warning.

### 8. Script allow (PermissionRequest, during a run)

Only Bash is considered. Anything that doesn't match every rule below gets no decision, and normal handling then applies. This piece never denies.
- **The text** contains no newline or `\r`, no `$(`, no backtick, no `${`, and no `<`/`>` redirection.
- **The scan** (shellscan) yields exactly one command segment, optionally preceded by one `cd <path> &&`. The pair may be wrapped once in `( … )`. No prefix was stripped: no `VAR=` assignment, no `env`, no `command`.
- **The executable,** after an optional `python3`, `bash` or `sh` without options, compared after `realpath(expanduser(…))`, is one of `<plugin>/scripts/gate-lock.sh`, `wt-setup.sh`, `review.sh`, `bk.py` or `hooks.py`.
- **Per script:**
  - **`gate-lock.sh`:** `<owner> -- <one argument>`, where the argument equals the config's `gate` (mints a receipt) or `post_merge_check` (doesn't mint, because only `gate` mints); no `--name`.
  - **`wt-setup.sh [--check] <dir>`:** `<dir>` is a worktree of this repo.
  - **`review.sh [--spec <path>] <base> <logfile>`:** the effective directory is a worktree of this repo. Both scripts read config from that worktree's MAIN checkout, never from the directory itself. Otherwise a planted config could run any `worktree_install` or `custom` reviewer command. The scripts keep honouring `FOREMAN_CONFIG` for tests, which is safe because an env prefix gets no decision here.
  - **`bk.py`:** subagents only with `--dry-run`.
  - **`hooks.py`:** only `run-end`, `release <batch> <sha>` or `stopping <batch>`, and only for the main session (no `agent_id`). Piece 8 never allows `hooks.py` for a subagent.
- **Argument charset** for every allowed script: each argument matches `[A-Za-z0-9._/@~^+:=-]+`, and none starts with `-` except the scripts' own options (`--`, `--check`, `--spec`, `--dry-run`). The one exception is gate-lock's gate argument, which must equal the config value exactly.
- **The config file is write-protected for subagents** by piece 2.
- **On a match:** `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}`.
- **Other commands:** the README gives a `permissions.allow` snippet for the rest of the worker contract:
  - `git add`, `git commit`, `git fetch`, `git merge`, `git push` and `git worktree`;
  - `gh pr create|view|checks|comment|edit`, `gh api`;
  - `node --check`.

  W3's preflight checks that it's present and warns if not.

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

### 10. Spec gate (tagged workers during a run)

- **Who:** a subagent (`agent_id` present) whose map entry from piece 6 has `spec: true`, during a run, whose batch has no release file `state_dir/batches/<session_id>/<batch>.released`.
  - Keying by `session_id` keeps a stale file from another session out.
  - Dated batch names keep a stale file from an earlier invocation in the same session out.
  - **Opt-in per batch:** only batches launched with the ` spec-phase` tag are gated. Hooks 0.4.0 can therefore ship before the spec phase (0.5.0) without blocking any worker.
- **Unmapped ids fail open:** an `agent_id` with no map entry (no tag, or P2 failed) passes, and so does any call outside a run.
- **Denied before release:**
  - Edit-family calls whose target, resolved in its own checkout, is outside that checkout's `design_spec_dir`;
  - `git commit` that would commit a path outside `design_spec_dir`:
    - the staged paths come from `git diff --cached --name-only` in the effective directory;
    - with `-a`/`--all`, the modified tracked files count too;
    - with a pathspec, `-i` or `-o`, the named paths count too;
    - `gt create -a` and `gt modify -a` are judged the same way.

    This catches code written through Bash, which the Edit rule can't see.
  - `git push`, `gh pr create` and `gt submit`. The spec phase doesn't push.
- **Allowed before release:** reading anything, running anything else (tests, `git grep`), and editing and committing inside `design_spec_dir`.
- **Reason:** "foreman: batch <b>'s spec isn't released yet. Finish the spec, put `SPEC READY <worktree> <branch> <spec path> <sha>` as your final message's first line, and wait for the orchestrator's release."
- **Release:** at W3.6, before its `SendMessage`, the orchestrator runs `python3 $FOREMAN/hooks.py release <batch> <sha>`.
  - Like `run-end`, `pretool` recognises the exact call and writes `state_dir/batches/<session_id>/<batch>.released` = `{"sha", "time"}` from its own hook input. This puts the file where the hooks look, whatever `FOREMAN_HOME` the Bash tool has (P4).
  - The script then checks that the file exists in either state dir, and exits 1 if not.
  - `<batch>` must match `[A-Za-z0-9_-]+`, because it becomes a filename.
  - In manual mode it runs only for the batches the human approved at the checkpoint.
- **After release** the gate is silent. Later spec edits are W4's concern: its byte-identical check, or a listed deviation.
- **Cleanup:** W5 deletes `batches/*` for this wave's batches. Entries older than 14 days are pruned on `run-start`.

## Config keys (new)

| key | default | meaning |
|---|---|---|
| `forbid_trailers` | `[]` | lines that must never start a commit, PR or comment message (piece 4) |
| `gate_exempt` | `["docs/**", "**/*.md"]` | gitignore-style patterns a push may touch without a gate receipt; `backlog_path` and `rules_glob` are always exempt (piece 1) |
| `worker_agent_types` | `["general-purpose", "workflow-subagent"]` | subagent types eligible for pieces 6 and 7 |
| `hooks_disabled` | `[]` | piece ids to turn off in this repo |
| `status_pane` | `true` | piece 9 |
| (none new) | | piece 10 reads `design_spec_dir` (spec-phase design) |

`templates/foreman.config.md` documents them. vacancy-vibe sets `forbid_trailers: [Co-Authored-By, Claude-Session, Generated with]` at switch-over.

## SKILL.md changes

- **W3:**
  - every worker prompt begins with `foreman-worker: <batch>`;
  - a permissions preflight;
  - the contract stays in the dispatch prompt until probe P2 confirms the briefing.
- **Worker contract:**
  - "commit every file the gate changed and list it in your report; don't edit files while a gate runs";
  - a line saying hooks enforce gate receipts, no merge, no force-push, no rebase, no backlog/rules/config edits, trailers and the report check.
- **Gate steps** run `gate-lock.sh <owner> -- '<gate>'` with the exact configured string, from the checkout's top level.
- **Step 12:** `gh pr merge <n> --squash --match-head-commit <verified sha> --delete-branch`, from the main checkout. **W4.6:** `--match-head-commit`, no `--delete-branch` (the worktree holds the branch).
- **W5:** a bookkeeping diff with non-exempt paths is gated in its worktree after `{{worktree_setup}}`.
- **Termination** (W6's stop, the autonomous loop's end, and serial step 12) runs `python3 $FOREMAN/hooks.py run-end`.
- **W3.6 (spec-phase release):** runs `python3 $FOREMAN/hooks.py release <batch> <sha>` before the release message. **W5** deletes `batches/*` for this wave.
- **`/foreman ui`** passes `--repo <main checkout>`.
- **A "Hooks" paragraph** lists the pieces, `hooks_disabled`, and the `! <command>` override.

## Interactions with the spec-phase design (`feat/spec-phase`)

- **Piece 7** allows the `SPEC READY` stop. That rule is in piece 7 above.
- **Piece 6** briefs the contract as the spec-phase design rewrites it: Step 2 becomes the spec phase.
- **Piece 10 (spec gate)** enforces the spec phase's "no code before release". The spec-phase design's W3.6 gains the `hooks.py release <batch> <sha>` step, and its "Non-goals: no enforcement by hooks" line is replaced by a pointer to piece 10.

## Error handling

- **Fail open:** every hook fails open on its own errors, exiting 0 with a stderr note. The exception is piece 1's undeterminable diff, which denies.
- **Git or gh failures inside piece 3's worktree check** or piece 4's commit read mean that check doesn't apply. The call is allowed, with a stderr note.
- **The mod** catches every refresh.

## Security notes

These are guardrails, not a security boundary. Piece 8 is the only piece that grants anything, and it allows only exact paths, with strict arguments, during a run. Both scripts it can run with repo-controlled commands now read config from the main checkout, which subagents can't edit through the tool calls these hooks see. The mod runs unsandboxed, like every mod, and only reads (git, gh) and draws. The README says so.

## Testing

- **`tests/test_shellscan.py`:** every form listed under Command detection, plus a fail-closed fallback case for unbalanced quotes.
- **`tests/test_hooks.py`** feeds recorded hook JSON to `hooks.py` in temp repos, with a temp `FOREMAN_HOME` and a fake `gh`, one row per case:
  - **Scoping:** no config means silent, and `FOREMAN_CONFIG` set means it is ignored; no marker silences the run-only pieces; `agent_id` present versus absent.
  - **Piece 1:**
    - receipt present, missing, an exempt-only diff, and an exempt diff since the last receipt;
    - a rename of code into `docs/` is not exempt;
    - `gate; git push`, `cd wt && git push`, `(git push)` and `/usr/bin/git push` are all judged;
    - a gate that writes `coverage/` gets a receipt that matches HEAD without the coverage files;
    - a delete push (`:b`, `--delete`) is allowed; an undeterminable diff or unknown arguments are denied;
    - minting: the gate string versus a multi-argument form, versus `--name`, versus a non-gate command;
    - a receipt tree equals `HEAD^{tree}` with a force-added ignored file, and with a CRLF blob under `text=auto`;
    - a gate run from a subdirectory runs at the top level.
  - **Pieces 2 and 3:** each denial, plus a normal push and merge allowed. `-d` is allowed when only the command's own worktree holds the branch (serial step 12 from the main checkout), and denied when another worktree holds it. `gt merge` is denied during a run.
  - **Piece 4:** a trailer line in `-m`, in a `-F` file in the scratchpad, and in a heredoc; the `🤖 Generated with` footer; a mid-sentence mention allowed; squash, merge and rebase whose commits carry a trailer.
  - **Piece 5:** a bookkeeping-worktree edit is checked; a clean backlog returns nothing.
  - **Pieces 6 and 7:**
    - briefing with and without the tag; rendered `$FOREMAN`;
    - the report check: missing block, invalid JSON, a wrong shape (`"done": ["IB-1"]`), a refused block, a valid block, `rule_notes` ignored, `stop_hook_active`, and `SPEC READY` after a preface line.
  - **Piece 8:**
    - an exact path allowed;
    - no decision for each of: a newline, `$(`, `; rm`, `BASH_ENV=x bash <script>`, `FOREMAN_CONFIG=x <script>`, `env <script>`, and a `>` redirect;
    - allowed: a one-paren wrap `(cd <wt> && <review.sh> --spec …)`, gate-lock with `post_merge_check` (no receipt minted), a `~/` plugin path, `hooks.py run-end`, and `hooks.py release w1a <sha>`;
    - no decision for gate-lock with a non-gate command or `--name`, for wt-setup on a non-worktree directory, and for bk.py without `--dry-run` from a subagent.
  - **Run marker:** `/loop 2h /foreman` starts a run; a mid-prompt mention doesn't; `ui`/`init` are skipped; `SessionEnd` reasons; `hooks.py run-end` deletes only the calling session's marker while another session's stays; the 48-hour expiry.
  - **Piece 10:**
    - a mapped, unreleased worker is denied an Edit outside `design_spec_dir` and allowed one inside it;
    - `git commit` with a staged code file is denied; with only the spec staged it is allowed; `git commit -a` with a modified code file is denied;
    - `git push` and `gh pr create` are denied before release;
    - after `hooks.py release` everything passes;
    - an unmapped `agent_id` passes, and the main session always passes.
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
