# Foreman Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce foreman's worker contract and merge rules at the tool call, with ten hooks served by one Python dispatcher plus a wave-status mod, shipped as foreman 0.4.0.

**Architecture:**
- **`scripts/shellscan.py`:** a fail-closed scan of one Bash command string that finds every `git`/`gh`/`gt` candidate at any nesting (`$(…)`, backticks, `bash -c`, `eval`, a shell's heredoc) and records its subcommand, arguments, effective directory and heredocs.
- **`scripts/hooks.py`:** one dispatcher (`python3 hooks.py <event>`, JSON on stdin) for pieces 1–8 and 10, plus the orchestrator CLIs `run-end`, `release`, `stopping` and `forget`. Run-only pieces read a per-session run marker in the repo's state dir.
- **`scripts/state.py`:** the repo key, the state dir and gate receipts, shared by `ui.py`, `hooks.py`, `gate-lock.sh` and the mod.
- **`hooks/status.mjs`:** a mod that draws one line per worker worktree above the prompt while this session's run marker exists.

**Tech Stack:**
- Python 3.8+ standard library only (`json`, `re`, `shlex`, `subprocess`, `unittest`);
- bash 3.2 (macOS), `perl` for time limits, Apple `/usr/bin/git` 2.50, `gh`;
- Claude Code ≥ 2.1.287 settings hooks (`type: command`, exec form) and mods (ES module, `register(on)`);
- Node 20+ only for `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-02-foreman-hooks-design.md` (revision 6). Read it first; it is the authority. Where this plan and the spec disagree, the spec wins, except for the items under **Spec ambiguities resolved** and **Departures from revision 6**.

## Global Constraints

- **Python 3 standard library only.** No pip, no npm, no build step. Code stays Python 3.8 compatible (the README floor): no `str.removeprefix`, no `match`, no `list[str]` at runtime.
- **bash scripts run on macOS bash 3.2:** no `declare -A`, no `timeout` (use `perl -e 'alarm N; exec @ARGV'`), no `mapfile`. Under `set -u`, never expand an array that can be empty.
- **Tests run as `python3 tests/<file>.py`**, never `python3 -m unittest tests/...` (`tests` resolves to an unrelated module). JS tests run with `node --test`. Every suite is wired into `tests/run.sh` with its `check "<name>" <cmd>` helper.
- **Hooks are `type: command` in `hooks/hooks.json`, exec form:** `"command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/hooks.py", "<event>"]`. `timeout` is 10, or 30 for SubagentStop. The mod is declared as `"modules": ["./status.mjs"]` in the same file.
- **Fail open:** every hook exits 0 on its own error and prints `foreman hook error: <type>: <msg>` to stderr. The one exception is piece 1's undeterminable diff, which denies.
- **Nothing fires outside a foreman repo:** "in a foreman repo" means `cfg.config_path(main, use_env=False)` is not None. Hooks ignore `FOREMAN_CONFIG`; the scripts and tests keep honouring it.
- **State:** `state_dir(main) = $FOREMAN_HOME/<repo_key(main)>`, FOREMAN_HOME defaulting to `~/.claude/foreman`; `repo_key` = the first 12 hex digits of `sha1(origin url + "\n" + realpath(main))`, byte-identical to `ui.py`'s formula. Directories 0700, files 0600. Every hook lookup checks `state_dir(main)` and the default-home dir when they differ.
- **Run marker:** `state_dir/runs/<session_id>.json`, live while `touched` is within 48 hours.
- **Deny output:** one PreToolUse deny per call, reason `"foreman: " + "; ".join(reasons)`.
- **Commit messages are plain:** no `Co-Authored-By`, no `Claude-Session`, no "Generated with".
- **The existing suites stay green with zero edits:** every current check in `tests/run.sh`, `tests/test_backlog.py`, `tests/test_ui_api.py` and `tests/test_ui_js.mjs`.
- **Version:** `0.4.0` in `.claude-plugin/plugin.json` and the README.

## Review Focus

The five inputs the spec implies, that no spec test exercises, and that are most likely to bite a user. Each has a test in the owning task:

1. **A PR or commit body in `"$(cat <<'EOF' … EOF)"` whose text holds a stray `"` and an apostrophe.** Expected: the arguments are still known, and the trailer guard still reads the body. Pinned by `test_heredoc_body_with_stray_quotes_keeps_args_known` (Task 3).
2. **Directories with spaces** (`cd "my wt" && git push`, a worktree under `my repo/`). Expected: the effective directory resolves and piece 1 judges that checkout. Pinned by `test_cd_into_a_dir_with_spaces` (Task 3) and `test_push_from_a_worktree_path_with_spaces` (Task 5).
3. **A Write that creates a file in a directory that doesn't exist yet** (a new `.claude/rules/new.md` in a fresh worktree). Expected: the repo is found through the nearest existing parent and the worker guard still denies it. Pinned by `test_new_rules_file_in_missing_dir_is_denied` (Task 6).
4. **A CRLF backlog.** Expected: a clean CRLF backlog gets no context, and a broken row in it is still named. Pinned by `test_crlf_backlog` (Task 7).
5. **A worker report with two json blocks, or prose after the block.** Expected: the last fenced block is the report. Pinned by `test_last_json_block_wins` (Task 8).

## Spec ambiguities resolved

1. **`release`'s freshness test with an undated batch name** (the spec's own test uses `w1a`): a name without a `YYYYMMDD-HHMM-` prefix counts any live (under 48 h) marker as fresh.
2. **W3.6 and `review.sh --spec`** belong to the spec phase (0.5.0) and don't exist on this branch. 0.4.0's SKILL.md adds neither; piece 8 only allows that argument shape. `stopping` goes before W4.4's and W6's TaskStop; `forget` goes into W5; `release` is documented in the Hooks paragraph.
3. **Dated batch names** are spec-phase's W2 change and are not on the hooks spec's SKILL.md list, so W2 is untouched.
4. **The dispatch prompt keeps the worker contract in 0.4.0** even if P2 passes. The briefing duplicates it until real waves confirm it; dropping the copy is a follow-up.
5. **Piece 8's gate argument:** an exact match to the configured `gate` or `post_merge_check` exempts it from both the leading-`-` rule and the character-set rule. (A gate like `npm run lint && npm test` holds `&`.)
6. **Piece 6's briefing order:** contract, gate, trailers, the "already released" line, then `shared_resources` last, so the 8 KB cut never drops the released line.
7. **`forbid_trailers` regex entries (`/…/`)** are case-sensitive as written; plain entries are case-insensitive.
8. **Piece 7 when the worker's start time is unknown** (no map entry, no transcript timestamp): a `.stopped` batch lets the stop through (fail open).
9. **Piece 6 records the agent map even when `worker_briefing` is in `hooks_disabled`**, so piece 10 keeps working; only the context is skipped.
10. **New config keys' defaults live in `cfg.DEFAULTS`**, used by `cfg.get` and the `cfg.py` CLI when the caller passes no default.

## Departures from revision 6 (the lead may veto any)

These change or add to what the spec says, rather than resolve an ambiguity in it:

1. **SessionEnd runs `hooks.py session-end`**, not `run-end`. `run-end` is also the orchestrator's CLI verb, which must never read stdin (a Bash call may hold stdin open), so the hook mode gets its own name.
2. **Receipts are minted into every state dir** (`state_dirs(main)`), not only `state_dir(main)`. The spec's both-dirs lookup alone misses a receipt minted under a FOREMAN_HOME that only the Bash tool sees (P4).
3. **`FOREMAN_HOOK_LOG`** (an env var naming a file) makes `hooks.py` append every decision it prints. The headless end-to-end needs evidence it can read; when the var is unset, nothing changes.
4. **gate-lock's heartbeat writes to `/dev/null`.** Its `sleep` outlives the script by up to a beat, and a caller capturing gate-lock's output waited for it, 60 s by default.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `docs/superpowers/plans/2026-10-02-foreman-hooks-probes.md` | probe and mod-binding results the later tasks cite | 1, 12, 15 |
| `scripts/cfg.py` | + `config_path(repo, use_env=True)`, `load(repo, use_env=True)`, `DEFAULTS` | 2 |
| `scripts/state.py` | main checkout, repo key, state dirs, JSON I/O; receipts and `mint` (Task 5) | 2, 5 |
| `scripts/ui.py` | state dir via `state.py` | 2 |
| `scripts/shellscan.py` | command detection | 3 |
| `scripts/hooks.py` | dispatcher, run marker, CLIs, pieces 1–8 and 10, the decision trace | 4–10, 15 |
| `scripts/gate-lock.sh` | runs from the top level; mints receipts; heartbeat detached from callers' pipes | 5 |
| `scripts/bk.py` | `--no-fetch` | 8 |
| `scripts/wt-setup.sh`, `scripts/review.sh` | config from the main checkout | 9 |
| `hooks/hooks.json` | settings hooks; `modules` from Task 13 | 11, 13 |
| `hooks/status.mjs` | wave status mod | 13 |
| `skills/foreman/SKILL.md`, `README.md`, `templates/foreman.config.md` | docs | 14 |
| `tests/test_state.py`, `tests/test_shellscan.py`, `tests/test_hooks.py`, `tests/test_hooks_json.py`, `tests/test_status.mjs`, `tests/e2e-hooks.sh` | tests | 2–15 |
| `tests/run.sh` | wires every suite; template and SKILL.md checks | 2–14 |
| `.gitignore` | the mod's generated types | 12 |
| `.claude-plugin/plugin.json` | 0.4.0 | 16 |

## Tasks

1. Probes P1–P8 (controller only)
2. `state.py`, `cfg.py` defaults, `ui.py` onto `state.py`
3. `shellscan.py`
4. `hooks.py` common path, run marker and orchestrator CLIs
5. Gate receipts: `gate-lock.sh` minting and piece 1
6. Pieces 2 and 3: worker guard and merge discipline
7. Pieces 4 and 5: trailer guard and backlog row check
8. Pieces 6 and 7: worker briefing and report check (+ `bk.py --no-fetch`)
9. Piece 8: script allow (+ `wt-setup.sh` and `review.sh` read the main checkout's config)
10. Piece 10: spec gate
11. `hooks/hooks.json`
12. Mod API binding against the generated `.d.ts` (controller only)
13. The wave status mod
14. SKILL.md, README and template
15. Headless end-to-end (controller only)
16. Version 0.4.0 and the release check

---

### Task 1: Probes P1–P8 (controller only)

**Run by the controller, not a subagent:** it spends model quota, and its answers decide fallbacks in Tasks 4, 8 and 10. Use `claude -p --model haiku` in a throwaway repo with a settings-level hook that logs every input it gets. Never touch the user's real shell profile or settings.

**Files:**
- Create: `docs/superpowers/plans/2026-10-02-foreman-hooks-probes.md`

**Interfaces:**
- Produces: the probes doc, with one row per probe (question, observed input fields, verdict, consequence). Tasks 4, 8, 10 and 14 cite it.

- [ ] **Step 1: Build the throwaway repo and the logging hook**

First run `claude plugin list`. If an installed `foreman` is enabled, run `claude plugin disable foreman@foreman` for the probes and `claude plugin enable foreman@foreman` after Step 7. Otherwise it's unclear which copy answers `/foreman`, and P1's `command_name` can't be read. Record which copy answered.

```bash
WT=/Users/crisis/_workspace/foreman.wt/hooks        # this worktree: the plugin under test
P=$(mktemp -d "${TMPDIR:-/tmp}/foreman-probe.XXXXXX"); export PROBE_LOG=$P/log.jsonl
cd "$P" && git init -q -b main && git -c user.name=t -c user.email=t@e commit -q --allow-empty -m init
mkdir -p .claude
cat >.claude/probe.py <<'EOF'
import json, os, sys
d = json.load(sys.stdin)
d['_env_FOREMAN_HOME'] = os.environ.get('FOREMAN_HOME')
with open(os.environ['PROBE_LOG'], 'a') as f:
    f.write(json.dumps(d) + '\n')
EOF
python3 - <<'EOF'
import json
h = lambda: [{"hooks": [{"type": "command", "command": "python3", "args": [".claude/probe.py"]}]}]
m = lambda matcher: [{"matcher": matcher, "hooks": [{"type": "command", "command": "python3", "args": [".claude/probe.py"]}]}]
json.dump({"hooks": {"UserPromptSubmit": h(), "UserPromptExpansion": h(), "SessionEnd": h(), "SubagentStart": h(),
                     "SubagentStop": h(), "PreToolUse": m("Bash"), "PostToolUse": m("Bash"),
                     "PostToolUseFailure": m("Bash")}}, open(".claude/settings.json", "w"), indent=1)
EOF
```

If `args: [".claude/probe.py"]` doesn't resolve because hooks don't run in the project dir, change it to `"${CLAUDE_PROJECT_DIR}/.claude/probe.py"` and re-run. If Claude Code rejects the settings file because it doesn't know `PostToolUseFailure`, drop that entry and re-run.

- [ ] **Step 2: P1 (prompt text), P5 (SessionEnd reason) and the plugin command name**

Load the real plugin with `--plugin-dir`, not a project-level skill. `ui` keeps the skill cheap: with no `origin`, it stops at its first check.

```bash
cd "$P"
claude -p --model haiku --plugin-dir "$WT" "/foreman ui" >/dev/null
claude -p --model haiku --plugin-dir "$WT" "/foreman:foreman ui" >/dev/null
python3 -c "
import json
for l in open('$PROBE_LOG'):
    d = json.loads(l)
    e = d['hook_event_name']
    if e in ('UserPromptSubmit', 'UserPromptExpansion', 'SessionEnd'):
        print(e, d.get('session_id'), repr(d.get('prompt')), d.get('command_name'), repr((d.get('expanded_prompt') or '')[:300]), d.get('reason'))
"
```

Record:
- **P1:** does `UserPromptSubmit.prompt` hold the typed `/foreman ui` text? Does `UserPromptExpansion` fire, with `command_name` (`foreman` or `foreman:foreman`), and does its `expanded_prompt` contain the SKILL.md line ``This run's arguments: `ui` ``? The `/loop 2h /foreman` form is not probed live, because it can schedule a recurring job. Its `UserPromptSubmit` text is the typed prompt, which `RunMarkerTest.test_prompts_that_start_a_run` pins offline (Task 4).
- **P5:** the `reason` a `-p` session ends with. `/clear` and logout can't be driven headlessly; record "not observed" for them and the documented list (`clear`, `resume`, `logout`, `prompt_input_exit`, `other`).

- [ ] **Step 3: P2 (SubagentStart prompt), P6 (agent ids) and the worktree agent's cwd**

```bash
: >"$PROBE_LOG"
claude -p --model haiku --plugin-dir "$WT" --permission-mode bypassPermissions \
  "Use the Agent tool twice, one call after the other. First: subagent_type general-purpose, prompt 'foreman-worker: probe1
Run the Bash command: echo hi. Then reply done.' Second: the same, with isolation \"worktree\" and the tag 'foreman-worker: probe2'. Then reply done." >/dev/null
python3 -c "
import json
for l in open('$PROBE_LOG'):
    d = json.loads(l)
    if d['hook_event_name'] in ('SubagentStart', 'SubagentStop', 'PreToolUse'):
        print(d['hook_event_name'], d.get('agent_id'), d.get('agent_type'), d.get('cwd'), repr((d.get('prompt') or '')[:60]), d.get('agent_transcript_path'), sorted(d))
"
```

Record:
- **P2:** does `SubagentStart` carry `prompt` with the tag as its first line? If not, is a transcript path readable from it?
- **P6:** is each worker's `SubagentStart.agent_id` equal to its `PreToolUse.agent_id`?
- Also the `cwd` that SubagentStart, PreToolUse and SubagentStop report for the `isolation: "worktree"` agent, and whether `agent_transcript_path`'s first `type: "user"` entry starts with the tag.

- [ ] **Step 4: P3 (resume keeps the session id)**

```bash
SID=$(python3 -c "import json; print([json.loads(l) for l in open('$PROBE_LOG')][0]['session_id'])")
: >"$PROBE_LOG"
claude -p --model haiku --resume "$SID" --permission-mode bypassPermissions "Run the Bash command: echo resumed" >/dev/null
python3 -c "import json; print({json.loads(l)['session_id'] for l in open('$PROBE_LOG')}, '$SID')"
```

**P3:** does the resumed session report the same `session_id`?

- [ ] **Step 5: P4 (FOREMAN_HOME from the shell profile)**

A temporary `ZDOTDIR` stands in for the profile; the real one is never edited.

```bash
mkdir -p "$P/zd" && echo "export FOREMAN_HOME=$P/fh" >"$P/zd/.zshrc" && cp "$P/zd/.zshrc" "$P/zd/.zshenv"
: >"$PROBE_LOG"
env -u FOREMAN_HOME SHELL=/bin/zsh ZDOTDIR="$P/zd" claude -p --model haiku --permission-mode bypassPermissions \
  'Run the Bash command: echo "FH=$FOREMAN_HOME" and reply with its exact output.'
python3 -c "import json; print({json.loads(l)['_env_FOREMAN_HOME'] for l in open('$PROBE_LOG')})"
```

**P4:** the Bash tool's `FH=` value versus the hooks' `_env_FOREMAN_HOME`. When they differ, the design's fallback stands: hook lookups check both state dirs, and `pretool` writes `release`/`stopping`/`forget` from its own hook input.

- [ ] **Step 6: P7 (TaskStop fires SubagentStop) and P8 (PostToolUse on a non-zero exit)**

```bash
: >"$PROBE_LOG"
claude -p --model haiku --permission-mode bypassPermissions \
  "Start a background Agent (subagent_type general-purpose, run_in_background true) with the prompt 'foreman-worker: probe7
Run the Bash command: sleep 120'. As soon as it has started, stop it with TaskStop. Then run the Bash command: false. Then reply done." >/dev/null
python3 -c "
import json
for l in open('$PROBE_LOG'):
    d = json.loads(l)
    print(d['hook_event_name'], d.get('agent_id'), (d.get('tool_input') or {}).get('command'), d.get('stop_hook_active'))
"
```

Record:
- **P7:** is there a `SubagentStop` for the stopped worker? If TaskStop can't be driven headlessly, record "not observed". The design's fallback already covers a failure: the orchestrator runs `hooks.py stopping <batch>` before every TaskStop.
- **P8:** for `false`, which event fired: `PostToolUse`, `PostToolUseFailure`, or neither? `hooks.py run-end` always exits 0, so a "no" changes nothing.

- [ ] **Step 7: Write the probes doc**

Create `docs/superpowers/plans/2026-10-02-foreman-hooks-probes.md` with this table, filled from Steps 2–6. Paste the exact field names and one redacted sample input per event below the table.

```markdown
# Foreman hooks: probe results (2026-10-02, Claude Code <version from `claude --version`>)

| # | question | observed | verdict | consequence |
|---|---|---|---|---|
| P1 | `/foreman` text in UserPromptSubmit / UserPromptExpansion | | | Task 4 `run_args` |
| P2 | SubagentStart carries the prompt or a readable transcript | | | Task 8 briefing, map |
| P3 | resumed session keeps its session_id | | | marker survives resume |
| P4 | shell-profile FOREMAN_HOME reaches hooks | | | both state dirs checked |
| P5 | SessionEnd reason values | | | Task 4 `session_end` |
| P6 | SubagentStart agent_id == PreToolUse agent_id | | | Task 8 map, Task 10 gate |
| P7 | TaskStop fires SubagentStop | | | `stopping` before TaskStop |
| P8 | PostToolUse fires on a non-zero Bash exit | | | none (run-end exits 0) |
```

**Fallbacks the later tasks apply** (copy into the doc under the table, marking the ones that apply):
- **P1 fails** (neither event carries `/foreman` or `command_name`): STOP and report to the user. Run-only pieces could never activate, and fixing that is a spec change.
- **P1 partial** (`UserPromptExpansion` fires but has no arguments line): `run_args` treats its arguments as empty, so a `/foreman ui` expansion starts a run marker. `UserPromptSubmit` still filters it when it carries the text.
- **P2 fails:** no briefing and no agent map. Piece 10 fails open (unmapped ids pass), and piece 7 still reads the tag from SubagentStop's `agent_transcript_path`. W3 keeps the contract in the dispatch prompt either way.
- **P3 fails:** a resumed mid-wave session runs unguarded until its next `/foreman`. No code change; the README's limits say so (Task 14).
- **P5:** keep `clear` and `logout`. If neither is ever observed, the explicit `run-end` and the 48 h expiry still end runs.
- **P6 fails:** the map misses every lookup. Piece 10 fails open, and `.stopped` falls back to the transcript's first timestamp. The README's limits say so.
- **P7 fails:** nothing to change.

- [ ] **Step 8: Commit**

```bash
git add docs/superpowers/plans/2026-10-02-foreman-hooks-probes.md
git commit -m "docs: hooks probe results (P1-P8)"
```

---

### Task 2: `state.py`, `cfg.py` defaults, and `ui.py` onto `state.py`

**Files:**
- Create: `scripts/state.py`
- Modify: `scripts/cfg.py` (`config_path`, `load`, `get`; new `DEFAULTS`)
- Modify: `scripts/ui.py:13` (drop `import hashlib`), `:34` (import `state`), `:57-58` (drop `home()`), `:67-68` (key and state dir)
- Create: `tests/test_state.py`
- Modify: `tests/run.sh:17` (py_compile list) and the `## backlog.py + ui.py` section

**Interfaces:**
- Produces (`scripts/state.py`):
  - `DEFAULT_HOME: str` = `~/.claude/foreman` (expanded at import)
  - `git(cwd, *args) -> str | None`: stdout, or None on failure
  - `toplevel(path) -> str | None`
  - `main_checkout(path) -> str | None`: the first `git worktree list --porcelain` entry
  - `repo_key(main) -> str`; `home() -> str`; `state_dir(main) -> str`
  - `state_dirs(main) -> list[str]`: `[state_dir(main)]`, plus `DEFAULT_HOME/<key>` when it differs
  - `ensure(d) -> str` (mkdir 0700); `write_json(path, data)` (atomic, 0600); `read_json(path) -> object | None`
  - CLI: `state.py <path>` prints `state_dir(main_checkout(path))`; exit 1 outside a repo
- Produces (`scripts/cfg.py`): `DEFAULTS: dict`; `config_path(repo, use_env=True)`; `load(repo, use_env=True)`; `get(repo, key, default=None)` falls back to `DEFAULTS[key]` when `default` is None.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_state.py`:

```python
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'scripts')
sys.path.insert(0, SCRIPTS)
import cfg  # noqa: E402
import state  # noqa: E402


def git(cwd, *args):
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@e', *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='foreman-state-'))
        self.repo = os.path.join(self.tmp, 'repo')
        os.makedirs(self.repo)
        git(self.repo, 'init', '-q', '-b', 'main')
        git(self.repo, 'commit', '-q', '--allow-empty', '-m', 'init')
        git(self.repo, 'remote', 'add', 'origin', 'https://example.com/acme/repo.git')
        self.env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def test_repo_key_matches_the_ui_formula(self):
        want = hashlib.sha1(f'https://example.com/acme/repo.git\n{self.repo}'.encode()).hexdigest()[:12]
        self.assertEqual(state.repo_key(self.repo), want)

    def test_main_checkout_from_a_worktree(self):
        wt = os.path.join(self.tmp, 'wt')
        git(self.repo, 'worktree', 'add', '-q', wt, '-b', 'w1')
        self.assertEqual(os.path.realpath(state.main_checkout(wt)), self.repo)
        self.assertEqual(state.repo_key(state.main_checkout(wt)), state.repo_key(self.repo))
        self.assertIsNone(state.main_checkout(self.tmp))

    def test_state_dirs_add_the_default_home_only_when_it_differs(self):
        os.environ['FOREMAN_HOME'] = os.path.join(self.tmp, 'home')
        dirs = state.state_dirs(self.repo)
        self.assertEqual(dirs[0], os.path.join(self.tmp, 'home', state.repo_key(self.repo)))
        self.assertEqual(dirs[1], os.path.join(state.DEFAULT_HOME, state.repo_key(self.repo)))
        os.environ['FOREMAN_HOME'] = state.DEFAULT_HOME
        self.assertEqual(len(state.state_dirs(self.repo)), 1)

    def test_write_json_is_private(self):
        p = os.path.join(self.tmp, 'h', 'k', 'runs', 's.json')
        state.write_json(p, {'a': 1})
        self.assertEqual(state.read_json(p), {'a': 1})
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(p)).st_mode & 0o777, 0o700)
        self.assertIsNone(state.read_json(os.path.join(self.tmp, 'missing.json')))

    def test_cli_prints_the_state_dir(self):
        env = dict(os.environ, FOREMAN_HOME=os.path.join(self.tmp, 'home'))
        out = subprocess.run([sys.executable, os.path.join(SCRIPTS, 'state.py'), self.repo], env=env,
                             capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), os.path.join(self.tmp, 'home', state.repo_key(self.repo)))
        out = subprocess.run([sys.executable, os.path.join(SCRIPTS, 'state.py'), self.tmp], capture_output=True)
        self.assertEqual(out.returncode, 1)


class CfgTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='foreman-cfg-')
        os.makedirs(os.path.join(self.tmp, '.claude'))
        with open(os.path.join(self.tmp, '.claude', 'foreman.config.md'), 'w') as f:
            f.write('---\ngate: make check\n---\n')
        self.other = os.path.join(self.tmp, 'other.md')
        with open(self.other, 'w') as f:
            f.write('---\ngate: from-env\n---\n')
        self.env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def test_use_env_false_ignores_foreman_config(self):
        os.environ['FOREMAN_CONFIG'] = self.other
        self.assertEqual(cfg.config_path(self.tmp), self.other)
        self.assertEqual(cfg.config_path(self.tmp, use_env=False), os.path.join(self.tmp, '.claude', 'foreman.config.md'))
        self.assertEqual(cfg.load(self.tmp, use_env=False)['gate'], 'make check')
        os.environ['FOREMAN_CONFIG'] = os.path.join(self.tmp, 'nope.md')
        self.assertIsNone(cfg.config_path(self.tmp))
        self.assertIsNotNone(cfg.config_path(self.tmp, use_env=False))

    def test_new_keys_default(self):
        self.assertEqual(cfg.get(self.tmp, 'gate_exempt'), ['docs/**', '**/*.md'])
        self.assertEqual(cfg.get(self.tmp, 'design_spec_dir'), 'docs/superpowers/specs')
        self.assertEqual(cfg.get(self.tmp, 'worker_agent_types'), ['general-purpose', 'workflow-subagent'])
        self.assertEqual(cfg.get(self.tmp, 'forbid_trailers'), [])
        self.assertEqual(cfg.get(self.tmp, 'status_pane'), 'true')
        self.assertEqual(cfg.get(self.tmp, 'gate_exempt', ['x']), ['x'])
        self.assertIsNone(cfg.get(self.tmp, 'no_such_key'))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 tests/test_state.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'state'`.

- [ ] **Step 3: Create `scripts/state.py`**

```python
#!/usr/bin/env python3
"""foreman's per-repo state directory, shared by ui.py, hooks.py, gate-lock.sh and the status mod.

  state.py <path>      print the state dir of <path>'s repo (exit 1 outside a git repo)

state_dir(main) = $FOREMAN_HOME (default ~/.claude/foreman)/<repo_key(main)>, where repo_key is the first 12
hex digits of sha1(origin url + "\\n" + realpath(main)) and main is the repo's main checkout (the first
`git worktree list` entry). Directories are 0700 and files 0600.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

DEFAULT_HOME = os.path.expanduser('~/.claude/foreman')


def git(cwd, *args):
    """stdout of a git command, or None when it fails."""
    try:
        p = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def toplevel(path):
    out = git(path, 'rev-parse', '--show-toplevel')
    return out.strip() if out else None


def main_checkout(path):
    """The repo's main checkout (the first `git worktree list` entry), or None outside a repo."""
    for line in (git(path, 'worktree', 'list', '--porcelain') or '').splitlines():
        if line.startswith('worktree '):
            return line[len('worktree '):]
    return None


def repo_key(main):
    origin = (git(main, 'remote', 'get-url', 'origin') or '').strip()
    return hashlib.sha1(f'{origin}\n{os.path.realpath(main)}'.encode()).hexdigest()[:12]


def home():
    return os.environ.get('FOREMAN_HOME', DEFAULT_HOME)


def state_dir(main):
    return os.path.join(home(), repo_key(main))


def state_dirs(main):
    """state_dir(main), then the default-home dir when FOREMAN_HOME points elsewhere. Hooks check both,
    because a FOREMAN_HOME set only for the Bash tool doesn't reach hook processes (probe P4)."""
    key = repo_key(main)
    dirs = [os.path.join(home(), key)]
    default = os.path.join(DEFAULT_HOME, key)
    if os.path.realpath(default) != os.path.realpath(dirs[0]):
        dirs.append(default)
    return dirs


def ensure(d):
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def write_json(path, data):
    """Write atomically (temp file + rename); mkstemp makes the file 0600."""
    d = ensure(os.path.dirname(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix='.tmp')
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main(argv):
    if len(argv) == 2 and not argv[1].startswith('-'):
        m = main_checkout(argv[1])
        if not m:
            raise SystemExit(1)
        print(state_dir(m))
        return
    raise SystemExit(__doc__)


if __name__ == '__main__':
    main(sys.argv)
```

- [ ] **Step 4: Change `scripts/cfg.py`**

Replace `config_path`, `load` and `get` (lines 116–134) with:

```python
DEFAULTS = {
    'forbid_trailers': [],
    'gate_exempt': ['docs/**', '**/*.md'],
    'worker_agent_types': ['general-purpose', 'workflow-subagent'],
    'hooks_disabled': [],
    'design_spec_dir': 'docs/superpowers/specs',
    'status_pane': 'true',
}


def config_path(repo, use_env=True):
    """use_env=False (the hooks) ignores FOREMAN_CONFIG, so a stray env var can't switch hooks on or off."""
    env = os.environ.get('FOREMAN_CONFIG') if use_env else None
    if env:
        return env if os.path.exists(env) else None
    for name in ('foreman.config.md', 'improve-next.config.md'):
        p = os.path.join(repo, '.claude', name)
        if os.path.exists(p):
            return p
    return None


def load(repo, use_env=True):
    p = config_path(repo, use_env)
    return parse(open(p).read()) if p else {}


def get(repo, key, default=None):
    """The key's value; when the config lacks it, `default`, else the key's DEFAULTS entry."""
    v = load(repo).get(key)
    if v is None:
        return default if default is not None else DEFAULTS.get(key)
    return v
```

- [ ] **Step 5: Move `scripts/ui.py` onto `state.py`**

Delete line 13 (`import hashlib`). After `import cfg  # noqa: E402` add `import state  # noqa: E402`. Delete the `home()` function (lines 57–58 and the blank lines after it). In `Repo.__init__`, replace:

```python
        self.key = hashlib.sha1(f'{self.origin}\n{self.path}'.encode()).hexdigest()[:12]
        self.state = os.path.join(home(), self.key)
```

with:

```python
        self.key = state.repo_key(self.path)
        self.state = state.state_dir(self.path)
```

- [ ] **Step 6: Wire the suite into `tests/run.sh`**

Change line 17 to compile the new module:

```bash
check "py_compile" python3 -m py_compile "$root/scripts/cfg.py" "$root/scripts/bk.py" "$root/scripts/backlog.py" "$root/scripts/ui.py" "$root/scripts/state.py"
```

Under `echo "## backlog.py + ui.py"`, add after the `test_ui_api.py` line:

```bash
check "test_state.py" python3 "$root/tests/test_state.py"
```

- [ ] **Step 7: Run the new tests and the regression suites**

Run: `python3 tests/test_state.py && python3 tests/test_ui_api.py && tests/run.sh`
Expected: `test_state.py` OK (7 tests); `test_ui_api.py` OK with zero edits (the state dir is byte-identical); `run.sh` ends `failed 0`.

- [ ] **Step 8: Commit**

```bash
git add scripts/state.py scripts/cfg.py scripts/ui.py tests/test_state.py tests/run.sh
git commit -m "Add state.py (repo key, state dirs); config defaults for the hooks; ui.py uses state.py"
```

---

### Task 3: `shellscan.py`

**Files:**
- Create: `scripts/shellscan.py`
- Create: `tests/test_shellscan.py`
- Modify: `tests/run.sh` (py_compile list; a new `## shellscan.py + hooks.py` section)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces (`scripts/shellscan.py`):
  - `scan(text, cwd) -> Scan`
  - `Scan`: `ok: bool`, `cmds: list[Cmd]`, `segs: list[Seg]` (top level, empty pieces included), `docs: list[Doc]`, `blanked: str`
  - `Cmd`: `prog` (`'git'|'gh'|'gt'`), `sub` (`'push'`, `'pr merge'`, `'api'`, a resolved gt verb, or None), `args: list[str] | None` (None = unknown), `pre: list[str] | None`, `cwd: str | None` (None = unknown), `text` (the raw segment), `start`, `end`, `heredocs: list[str]`, `assigns: list[str]`, `env: bool`, `ghrepo: str | None`, `denied: bool` (set by hooks.py)
  - `Seg`: `start`, `end`, `words: list[str] | None`, `sep` (`'&&'`, `';'`, `'|'`, `'('`, `')'`, `'\n'`, `'kw'`, … or None at the end), `depth`, `cwd`
  - `Doc`: `op`, `body_start`, `end`, `body`, `direct`, `target: str | None`
  - `push_info(args, default) -> (delete, force, to_default)`; `is_rebase(cmd) -> bool`; `gh_api_merge(args, subagent) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_shellscan.py`. It pins every form the spec lists under Command detection, the revision-3 forms (comment then newline, `true;(git push)`, `(cd wt && git push)`, `{ git push; }`, `if …; then git push; fi`, `|&`, a heredoc body with `don't`, `+main` and `:old`, `/usr/bin/git push`, `env -i git push`, `GIT_DIR=x git push`, `bash -c 'git push'`, `eval "git push"`, and `'a;b'` as one argument), the fail-closed fallback, and Review Focus items 1 and 2.

```python
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
import shellscan  # noqa: E402

PR_FORM = '''gh pr create --title t --body "$(cat <<'EOF'
## Summary
- fixed "two words" here

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"'''
COMMIT_FORM = '''git commit -m "$(cat <<'EOF'
fix: thing

- "two words"
Co-Authored-By: a <a@b>
EOF
)"'''


def cmds(text, cwd='/r'):
    return shellscan.scan(text, cwd).cmds


def pairs(text, cwd='/r'):
    return [(c.prog, c.sub) for c in cmds(text, cwd)]


class DetectTest(unittest.TestCase):
    def test_wrappers_do_not_hide_the_program(self):
        for t in ('nohup git push', 'timeout 60 git push', 'sudo git push', 'xargs -n1 git push',
                  'nice -n 5 git push', 'env -i git push', 'command -p git push', 'stdbuf -oL git push',
                  'caffeinate git push', '/usr/bin/git push', "'git' push", '\\git push'):
            self.assertEqual(pairs(t), [('git', 'push')], t)
        self.assertEqual(pairs('find . -exec git add {} \;'), [('git', 'add')])

    def test_substitutions_shells_eval_and_shell_heredocs(self):
        for t in ('x="$(git push)"', 'x=`git push`', "bash -lc 'git push'", "bash -c 'git push'", 'eval "git push"',
                  "bash <<'EOF'\ngit push\nEOF", "sh -s <<EOF\ngit push\nEOF"):
            self.assertEqual(pairs(t), [('git', 'push')], t)

    def test_unquoted_substitution_is_one_word_and_scanned_once(self):
        self.assertEqual(pairs('x=$(cd sub && git push) ; git status'), [('git', 'push'), ('git', 'status')])
        self.assertEqual(cmds('x=$(cd sub && git push)')[0].cwd, '/r/sub')

    def test_gh_repo_flags_and_api(self):
        c = cmds('gh pr -R o/r merge 1')[0]
        self.assertEqual((c.sub, c.args, c.ghrepo), ('pr merge', ['1'], 'o/r'))
        c = cmds('gh api -X PUT repos/o/r/pulls/1/merge')[0]
        self.assertEqual(c.sub, 'api')
        self.assertTrue(shellscan.gh_api_merge(c.args, False))

    def test_git_dash_C_with_a_quoted_dir(self):
        c = cmds('git -C "my dir" push')[0]
        self.assertEqual((c.sub, c.cwd), ('push', '/r/my dir'))

    def test_cd_into_a_dir_with_spaces(self):
        self.assertEqual(cmds('cd "my wt" && git push')[0].cwd, '/r/my wt')

    def test_pr_form_gives_known_args_and_its_heredoc(self):
        c = cmds(PR_FORM)[0]
        self.assertEqual(c.sub, 'pr create')
        self.assertIsNotNone(c.args)
        self.assertIn('--body', c.args)
        self.assertTrue(any('🤖 Generated with' in h for h in c.heredocs))
        self.assertTrue(any('"two words"' in h for h in c.heredocs))

    def test_commit_form_heredoc_belongs_to_the_commit(self):
        c = cmds(COMMIT_FORM)[0]
        self.assertEqual(c.sub, 'commit')
        self.assertIsNotNone(c.args)
        self.assertTrue(any('Co-Authored-By' in h for h in c.heredocs))

    def test_heredoc_body_with_stray_quotes_keeps_args_known(self):
        t = PR_FORM.replace('- fixed', "- don't keep a stray \" quote\n- fixed")
        sc = shellscan.scan(t, '/r')
        self.assertTrue(sc.ok)
        self.assertIsNotNone(sc.cmds[0].args)
        self.assertTrue(any('stray' in h for h in sc.cmds[0].heredocs))

    def test_quoted_git_push_is_not_a_push(self):
        self.assertEqual(pairs('git commit -m "fix: git push retry"'), [('git', 'commit')])
        self.assertEqual(pairs("echo 'git push'"), [])

    def test_unquoted_echo_git_push_is_an_accepted_false_deny(self):
        self.assertEqual(pairs('echo git push'), [('git', 'push')])

    def test_cd_in_a_group_is_scoped(self):
        a, b = cmds('(cd wt && git push); git push')
        self.assertEqual((a.cwd, b.cwd), ('/r/wt', '/r'))
        self.assertEqual(cmds('(cd wt && git push)')[0].cwd, '/r/wt')
        self.assertEqual(cmds('cd wt && git push')[0].cwd, '/r/wt')
        self.assertIsNone(cmds('cd $X && git push')[0].cwd)
        self.assertIsNone(cmds('cd - && git push')[0].cwd)

    def test_revision_3_forms(self):
        self.assertEqual(pairs('# note\ngit push'), [('git', 'push')])
        self.assertEqual(pairs('true;(git push)'), [('git', 'push')])
        self.assertEqual(pairs('{ git push; }'), [('git', 'push')])
        self.assertEqual(pairs('if true; then git push; fi'), [('git', 'push')])
        self.assertEqual(pairs('git push |& tee log'), [('git', 'push')])
        self.assertTrue(cmds('env -i git push')[0].env)
        self.assertIsNone(cmds('GIT_DIR=x git push')[0].cwd)
        self.assertEqual(cmds('GIT_DIR=x git push')[0].assigns, ['GIT_DIR'])
        self.assertIsNone(cmds('git --git-dir=x push')[0].cwd)
        self.assertEqual(cmds("git commit -m 'a;b'")[0].args, ['-m', 'a;b'])

    def test_heredoc_body_is_blanked(self):
        sc = shellscan.scan("cat > /tmp/m <<'EOF'\ndon't git push +main :old\nEOF\ngit commit -F /tmp/m", '/r')
        self.assertTrue(sc.ok)
        self.assertEqual([(c.prog, c.sub) for c in sc.cmds], [('git', 'commit')])
        self.assertEqual(sc.docs[0].target, '/tmp/m')
        self.assertIn('+main', sc.docs[0].body)

    def test_stdin_heredoc_feeds_the_commit(self):
        c = cmds('git commit -F - <<EOF\nmsg\nCo-Authored-By: x\nEOF')[0]
        self.assertEqual(c.heredocs, ['msg\nCo-Authored-By: x\n'])

    def test_comment_is_blanked_from_the_words(self):
        self.assertEqual(cmds('git push -u origin HEAD  # note')[0].args, ['-u', 'origin', 'HEAD'])

    def test_braces_inside_a_word_do_not_split(self):
        c = cmds('gh api repos/{owner}/{repo}/pulls/1/merge -X PUT')[0]
        self.assertTrue(shellscan.gh_api_merge(c.args, False))

    def test_unbalanced_quote_falls_back_to_unknown_args(self):
        sc = shellscan.scan('git push "oops', '/r')
        self.assertFalse(sc.ok)
        self.assertEqual([(c.prog, c.sub, c.args) for c in sc.cmds], [('git', 'push', None)])
        sc = shellscan.scan('cat <<EOF\nno end\ngit push', '/r')
        self.assertFalse(sc.ok)
        self.assertEqual([c.sub for c in sc.cmds], ['push'])

    def test_redirect_ampersand_does_not_split(self):
        self.assertEqual(pairs('git push 2>&1 | tail -1'), [('git', 'push')])


class ClassifyTest(unittest.TestCase):
    def test_gt_aliases(self):
        self.assertEqual(pairs('gt ss'), [('gt', 'submit')])
        self.assertEqual(pairs('gt s'), [('gt', 'submit')])
        self.assertEqual(pairs('gt m -a'), [('gt', 'modify')])
        self.assertEqual(pairs('gt c -m x'), [('gt', 'create')])

    def test_pull_rebase_is_a_rebase(self):
        self.assertTrue(shellscan.is_rebase(cmds('git pull --rebase')[0]))
        self.assertTrue(shellscan.is_rebase(cmds('git pull -r origin main')[0]))
        self.assertTrue(shellscan.is_rebase(cmds('git rebase main')[0]))
        self.assertFalse(shellscan.is_rebase(cmds('git pull')[0]))
        self.assertFalse(shellscan.is_rebase(cmds('git pull --rebase=false')[0]))

    def test_push_info(self):
        def info(t):
            return shellscan.push_info(cmds(t)[0].args, 'main')
        self.assertEqual(info('git push -u origin HEAD'), (False, False, False))
        self.assertEqual(info('git push origin :old'), (True, False, False))
        self.assertEqual(info('git push --delete origin b'), (True, False, False))
        self.assertEqual(info('git push -d origin b'), (True, False, False))
        self.assertEqual(info('git push --force'), (False, True, False))
        self.assertEqual(info('git push -f'), (False, True, False))
        self.assertEqual(info('git push -uf origin HEAD'), (False, True, False))
        self.assertEqual(info('git push --force-with-lease'), (False, True, False))
        self.assertEqual(info('git push --mirror'), (False, True, False))
        self.assertEqual(info('git push origin +feat'), (False, True, False))
        self.assertEqual(info('git push origin HEAD:main'), (False, False, True))
        self.assertEqual(info('git push origin main'), (False, False, True))
        self.assertEqual(info('git push origin HEAD:refs/heads/main'), (False, False, True))
        self.assertEqual(info('git push origin feat 2>&1'), (False, False, False))

    def test_gh_api_merge_by_method_and_path(self):
        def merge(t, subagent=False):
            return shellscan.gh_api_merge(cmds(t)[0].args, subagent)
        self.assertFalse(merge('gh api repos/o/r/pulls/1/merge'))
        self.assertFalse(merge('gh api -X DELETE repos/o/r/git/refs/heads/x'))
        self.assertTrue(merge('gh api --method PUT repos/o/r/pulls/$N/merge'))
        self.assertTrue(merge('gh api repos/o/r/merges -f base=main -f head=x'))
        self.assertTrue(merge('gh api -X PATCH repos/o/r/git/refs/heads/main -f sha=abc'))
        self.assertTrue(merge("gh api graphql -f query='mutation { mergePullRequest(input:{}) { clientMutationId } }'"))
        self.assertTrue(merge("gh api graphql -f query='mutation { enablePullRequestAutoMerge(input:{}) { clientMutationId } }'"))
        self.assertFalse(merge("gh api graphql -f query='query { viewer { login } }'"))
        self.assertFalse(merge('gh api graphql -F query=@q.graphql'))
        self.assertTrue(merge('gh api graphql -F query=@q.graphql', subagent=True))
        self.assertTrue(merge('gh api graphql --input q.json', subagent=True))
        self.assertTrue(shellscan.gh_api_merge(None, False))


class SegmentTest(unittest.TestCase):
    def test_one_paren_wrap_with_cd(self):
        sc = shellscan.scan('(cd /x && /p/review.sh a b)', '/r')
        words = [(s.words, s.sep, s.cwd) for s in sc.segs if s.words]
        self.assertEqual(words, [(['cd', '/x'], '&&', '/r'), (['/p/review.sh', 'a', 'b'], ')', '/x')])

    def test_quoted_operators_stay_in_one_segment(self):
        sc = shellscan.scan("/p/gate-lock.sh w1 -- 'npm run lint && npm test'", '/r')
        self.assertEqual([s.words for s in sc.segs if s.words], [['/p/gate-lock.sh', 'w1', '--', 'npm run lint && npm test']])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 tests/test_shellscan.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'shellscan'`.

- [ ] **Step 3: Create `scripts/shellscan.py`**

How it works, in the spec's step order:
- `_lex` walks the text once per level. It records spans to blank: quotes (single, double, `$'…'`), backslash escapes, `#` comments that start a word, `$(…)`/`<(…)`/`>(…)`/backtick substitutions, and heredoc bodies with their terminator line. It also records each substitution's inner span, including those inside double quotes, and each heredoc as a `Doc`. A `)` inside a heredoc body never closes a substitution, because `_subst` lexes the body with the same rules.
- `_level` blanks those spans with `FILL` (offsets kept), splits the blanked text into pieces (`_pieces`), and `shlex.split`s each piece's original text with comments, heredoc bodies and unquoted substitutions blanked. That keeps the PR form's arguments known whatever quotes its body holds.
- Every word whose basename is `git`, `gh` or `gt` starts a candidate (`_classify`). A `cd`/`pushd` updates the effective directory for the pieces after it; a `(` pushes it and the matching `)` pops it.
- Substitutions recurse with their own offsets. `-c` scripts, `eval` and a shell's heredoc recurse unaligned, and their candidates take the outer segment's span.
- Every heredoc whose operator or body lies in a candidate's span belongs to that candidate.
- Any `ScanError` (unterminated quote, heredoc or substitution, or nesting deeper than 8) falls back to `FALLBACK`'s raw-text regexes, with unknown arguments.

```python
#!/usr/bin/env python3
"""Fail-closed detection of git, gh and gt commands in one Bash command string: scan, don't parse.

scan(text, cwd) -> Scan
  .ok       False when the text couldn't be lexed (unbalanced quotes, an unterminated heredoc); .cmds then
            holds raw-text regex matches with unknown arguments (args None, cwd None)
  .cmds     every git/gh/gt candidate at any nesting: $(...), backticks, bash -c, eval, a shell's heredoc
  .segs     the top level's segments, empty ones included, with the separator that ended each (piece 8)
  .docs     every heredoc, with the file a `cat > f <<EOF` writes (piece 4)
  .blanked  the top level with quotes, comments, substitutions and heredoc bodies filled with FILL

It errs toward treating text as a command: an unquoted `echo git push` is a candidate. It doesn't see
commands fed to a shell's stdin from a pipe, or strings built at run time; those are accepted limits.
"""
import os
import re
import shlex

FILL = '\x00'
PROGS = ('git', 'gh', 'gt')
SHELLS = ('bash', 'sh', 'zsh')
KEYWORDS = ('then', 'do', 'else', 'elif', 'if', 'while', 'until', '!', 'time', '{', '}')
GT_ALIASES = {'s': 'submit', 'ss': 'submit', 'm': 'modify', 'c': 'create'}
GIT_VALUE_OPTS = ('-C', '-c', '--git-dir', '--work-tree', '--namespace', '--config-env', '--super-prefix',
                  '--attr-source')
MAX_DEPTH = 8
HEREDOC = re.compile(r'<<([-~]?)[ \t]*(?:\'([^\'\n]*)\'|"([^"\n]*)"|\\?([^\s;&|<>()\'"]+))')
ASSIGN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
WORD = re.compile(r'[^ \t\n]+')
REDIRECT = re.compile(r'^(?:\d*|&)(?:>>?|<<?|>&|<&|>\|)')
FALLBACK = (
    ('git', 'push', r'\bgit\b[^\n]*\bpush\b'),
    ('git', 'commit', r'\bgit\b[^\n]*\bcommit\b'),
    ('git', 'rebase', r'\bgit\b[^\n]*\brebase\b'),
    ('gh', 'pr merge', r'\bgh\b[^\n]*\bmerge\b'),
    ('gh', 'pr create', r'\bgh\b[^\n]*\bcreate\b'),
    ('gh', 'api', r'\bgh\s+api\b'),
    ('gt', 'submit', r'\bgt\s+(?:submit|ss?)\b'),
    ('gt', 'merge', r'\bgt\s+merge\b'),
    ('gt', 'modify', r'\bgt\s+(?:modify|m)\b'),
    ('gt', 'create', r'\bgt\s+(?:create|c)\b'),
)


class ScanError(Exception):
    pass


class Doc:
    """One heredoc. body_start..end spans the body and its terminator line; `direct` is False for a
    heredoc found inside a substitution of the level that recorded it."""

    def __init__(self, op, body_start, end, body, direct):
        self.op, self.body_start, self.end, self.body, self.direct = op, body_start, end, body, direct
        self.target = None


class Seg:
    def __init__(self, start, end, words, sep, depth, cwd):
        self.start, self.end, self.words, self.sep, self.depth, self.cwd = start, end, words, sep, depth, cwd


class Cmd:
    def __init__(self, prog, args, pre, cwd, text, start, end):
        self.prog, self.args, self.pre, self.cwd, self.text = prog, args, pre, cwd, text
        self.start, self.end = start, end
        self.sub = None
        self.ghrepo = None
        self.heredocs = []
        self.denied = False   # set by hooks.py when piece 2 already denied this merge
        self.assigns = [w.split('=', 1)[0] for w in (pre or []) if ASSIGN.match(w)]
        self.env = any(os.path.basename(w) == 'env' for w in (pre or []))

    def __repr__(self):
        return f'Cmd({self.prog} {self.sub} {self.args} cwd={self.cwd})'


class Scan:
    def __init__(self, ok, cmds, segs, docs, blanked):
        self.ok, self.cmds, self.segs, self.docs, self.blanked = ok, cmds, segs, docs, blanked


class _Out:
    def __init__(self):
        self.blank, self.subs, self.docs, self.comments, self.bare = [], [], [], [], []


# ---- lexing ---------------------------------------------------------------------------------------

def _lex(t, i, closer, out):
    """Walk t from i, recording quote, comment, substitution and heredoc spans in `out`. At the top level
    (closer None) return len(t); inside $( ) return the index of the matching ')'."""
    n, depth, pending = len(t), 0, []
    while i < n:
        c = t[i]
        if c == '\\':
            out.blank.append((i, min(i + 2, n)))
            i += 2
        elif c == "'":
            j = t.find("'", i + 1)
            if j < 0:
                raise ScanError('unterminated single quote')
            out.blank.append((i, j + 1))
            i = j + 1
        elif t.startswith("$'", i):
            j = i + 2
            while j < n and t[j] != "'":
                j += 2 if t[j] == '\\' else 1
            if j >= n:
                raise ScanError("unterminated $'")
            out.blank.append((i, j + 1))
            i = j + 1
        elif c == '"':
            j = _dquote(t, i + 1, out)
            out.blank.append((i, j + 1))
            i = j + 1
        elif c == '`':
            j = _backtick(t, i + 1)
            out.subs.append((i + 1, j))
            out.blank.append((i, j + 1))
            out.bare.append((i + 1, j))
            i = j + 1
        elif c in '$<>' and t.startswith('(', i + 1):
            j = _subst(t, i + 2, out)
            out.subs.append((i + 2, j))
            out.blank.append((i, j + 1))
            out.bare.append((i + 2, j))
            i = j + 1
        elif c == '#' and (i == 0 or t[i - 1] in ' \t\n;&|()'):
            j = t.find('\n', i)
            j = n if j < 0 else j
            out.blank.append((i, j))
            out.comments.append((i, j))
            i = j
        elif t.startswith('<<<', i):
            i += 3
        elif t.startswith('<<', i):
            m = HEREDOC.match(t, i)
            if not m:
                i += 2
                continue
            pending.append((i, next(g for g in m.group(2, 3, 4) if g is not None), m.group(1)))
            i = m.end()
        elif c == '\n' and pending:
            i = _bodies(t, i + 1, pending, out, closer is None)
            pending = []
        elif closer and c == '(':
            depth += 1
            i += 1
        elif closer and c == ')':
            if depth == 0:
                return i
            depth -= 1
            i += 1
        else:
            i += 1
    if closer:
        raise ScanError('unterminated substitution')
    if pending:
        raise ScanError('unterminated heredoc')
    return n


def _bodies(t, k, pending, out, direct):
    """Consume the bodies of the heredocs opened on the line that just ended; return the index of the
    newline after the last terminator line (or len(t))."""
    n, last = len(t), len(t)
    for op, word, mode in pending:
        start = k
        while True:
            e = t.find('\n', k)
            e = n if e < 0 else e
            line = t[k:e]
            if (line.lstrip('\t') if mode == '-' else line.lstrip() if mode == '~' else line) == word:
                out.docs.append(Doc(op, start, e, t[start:k], direct))
                out.blank.append((start, e))
                last, k = e, e + 1
                break
            if e >= n:
                raise ScanError('unterminated heredoc')
            k = e + 1
    return last


def _dquote(t, i, out):
    n = len(t)
    while i < n:
        c = t[i]
        if c == '\\':
            i += 2
        elif c == '"':
            return i
        elif c == '`':
            j = _backtick(t, i + 1)
            out.subs.append((i + 1, j))
            i = j + 1
        elif c == '$' and t.startswith('(', i + 1):
            j = _subst(t, i + 2, out)
            out.subs.append((i + 2, j))
            i = j + 1
        else:
            i += 1
    raise ScanError('unterminated double quote')


def _backtick(t, i):
    n = len(t)
    while i < n and t[i] != '`':
        i += 2 if t[i] == '\\' else 1
    if i >= n:
        raise ScanError('unterminated backtick')
    return i


def _subst(t, i, out):
    inner = _Out()
    j = _lex(t, i, ')', inner)
    out.docs.extend(inner.docs)
    return j


# ---- segments -------------------------------------------------------------------------------------

def _pieces(b):
    """[(start, end, sep)] of the blanked text: split at ; && || | |& & newline ( ), then at standalone
    { } and the keywords (sep 'kw')."""
    raw, start, i, n = [], 0, 0, len(b)
    while i < n:
        two = b[i:i + 2]
        if two in ('&&', '||', '|&'):
            w = 2
        elif b[i] in ';|\n()':
            w = 1
        elif b[i] == '&' and not (i > 0 and b[i - 1] in '<>') and b[i + 1:i + 2] != '>':
            w = 1
        else:
            i += 1
            continue
        raw.append((start, i, b[i:i + w]))
        start = i = i + w
    raw.append((start, n, None))
    out = []
    for s, e, sep in raw:
        for m in WORD.finditer(b, s, e):
            if m.group() in KEYWORDS:
                out.append((s, m.start(), 'kw'))
                s = m.end()
        out.append((s, e, sep))
    return out


def _words(s):
    try:
        return shlex.split(s)
    except ValueError:
        return None


def _join(cwd, p):
    if cwd is None or not p or p == '-' or '$' in p or '`' in p:
        return None
    return os.path.normpath(os.path.join(cwd, os.path.expanduser(p)))


def _cd(cwd, words):
    args = words[1:]
    if words[0] == 'popd' or '-' in args:
        return None
    target = next((a for a in args if not a.startswith('-')), '~')
    return _join(cwd, target)


def _target(words):
    for k, w in enumerate(words):
        if w in ('>', '>>', '1>', '1>>'):
            return words[k + 1] if k + 1 < len(words) else None
        m = re.match(r'^1?>>?(.+)$', w)
        if m and not m.group(1).startswith('&'):
            return m.group(1)
    return None


def _feeds_shell(words):
    """True when the words run a shell whose script is the heredoc (`bash <<EOF`, `sh -s -e <<'EOF'`)."""
    for k, w in enumerate(words):
        if os.path.basename(w) in SHELLS:
            rest = words[k + 1:]
            return all(r.startswith('-') or r.startswith('<<') for r in rest)
    return False


def _shell_c(words):
    """The script string of `bash -c '...'` (any option cluster holding c: -c, -lc, -ec)."""
    for k, w in enumerate(words):
        if os.path.basename(w) in SHELLS:
            for j in range(k + 1, len(words)):
                if re.match(r'^-[a-zA-Z]*c[a-zA-Z]*$', words[j]):
                    return words[j + 1] if j + 1 < len(words) else None
                if not words[j].startswith('-'):
                    break
    return None


def _level(t, cwd, depth):
    """Scan one level. Returns (cmds, segs, docs, blanked); positions are relative to t."""
    if depth > MAX_DEPTH:
        raise ScanError('nested too deep')
    out = _Out()
    _lex(t, 0, None, out)
    b = list(t)
    for s, e in out.blank:
        b[s:e] = FILL * (e - s)
    blanked = ''.join(b)
    sh = list(t)
    for s, e in out.comments + [(d.body_start, d.end) for d in out.docs]:
        sh[s:e] = ' ' * (e - s)
    for s, e in out.bare:   # an unquoted $(...) stays one word, scanned by its own level
        sh[s:e] = FILL * (e - s)
    shtext = ''.join(sh)

    cmds, segs, docs, stack = [], [], [d for d in out.docs if d.direct], []
    for start, end, sep in _pieces(blanked):
        words = _words(shtext[start:end])
        seg = Seg(start, end, words, sep, len(stack), cwd)
        segs.append(seg)
        own = []
        if words is None:
            for m in WORD.finditer(blanked, start, end):
                if FILL not in m.group() and os.path.basename(m.group()) in PROGS:
                    own.append(_classify(Cmd(os.path.basename(m.group()), None, None, cwd, t[start:end], start, end)))
        elif words:
            for k, w in enumerate(words):
                if os.path.basename(w) in PROGS:
                    own.append(_classify(Cmd(os.path.basename(w), words[k + 1:], words[:k], cwd, t[start:end], start, end)))
            for d in docs:
                if start <= d.op < end:
                    tgt = _target(words)
                    d.target = _join(cwd, tgt) if tgt else None
                    if _feeds_shell(words):
                        own += _nested(d.body, cwd, depth, start, end, docs)
            script = _shell_c(words)
            if script:
                own += _nested(script, cwd, depth, start, end, docs)
            if any(os.path.basename(w) == 'eval' for w in words):
                k = next(k for k, w in enumerate(words) if os.path.basename(w) == 'eval')
                own += _nested(' '.join(words[k + 1:]), cwd, depth, start, end, docs)
            if words[0] in ('cd', 'pushd', 'popd'):
                cwd = _cd(cwd, words)
        for s, e in out.subs:
            if start <= s < end:
                sub_cmds, _, sub_docs, _ = _level(t[s:e], seg.cwd, depth + 1)
                for c in sub_cmds:
                    c.start, c.end = c.start + s, c.end + s
                for d in sub_docs:
                    d.op, d.body_start, d.end = d.op + s, d.body_start + s, d.end + s
                own += sub_cmds
                docs += sub_docs
        for c in own:
            c.heredocs += [d.body for d in out.docs if (c.start <= d.op < c.end or c.start <= d.body_start < c.end)
                           and d.body not in c.heredocs]
        cmds += own
        if sep == '(':
            stack.append(cwd)
        elif sep == ')' and stack:
            cwd = stack.pop()
    return cmds, segs, docs, blanked


def _nested(text, cwd, depth, start, end, docs):
    """Scan a string that isn't aligned with the outer text (a -c script, eval's words, a shell's heredoc);
    its candidates take the outer segment's span."""
    cmds, _, sub_docs, _ = _level(text, cwd, depth + 1)
    for c in cmds:
        c.start, c.end = start, end
    for d in sub_docs:
        d.op = d.body_start = d.end = -1
    docs += sub_docs
    return cmds


def _classify(cmd):
    a = cmd.args
    cwd = None if any(n in ('GIT_DIR', 'GIT_WORK_TREE') for n in cmd.assigns) else cmd.cwd
    if a is None:
        cmd.cwd = None
        return cmd
    if cmd.prog == 'git':
        i = 0
        while i < len(a) and a[i].startswith('-'):
            w = a[i]
            if w in GIT_VALUE_OPTS:
                if w == '-C':
                    cwd = _join(cwd, a[i + 1] if i + 1 < len(a) else None)
                elif w in ('--git-dir', '--work-tree'):
                    cwd = None
                i += 2
                continue
            if w.startswith(('--git-dir=', '--work-tree=')):
                cwd = None
            i += 1
        cmd.sub, cmd.args = (a[i] if i < len(a) else None), a[i + 1:]
    elif cmd.prog == 'gh':
        rest, i = [], 0
        while i < len(a):
            w = a[i]
            if w in ('-R', '--repo'):
                cmd.ghrepo = a[i + 1] if i + 1 < len(a) else None
                i += 2
                continue
            if w.startswith('--repo=') or (w.startswith('-R') and len(w) > 2):
                cmd.ghrepo = w.split('=', 1)[1] if w.startswith('--repo=') else w[2:]
                i += 1
                continue
            rest.append(w)
            i += 1
        if rest[:1] == ['pr'] and len(rest) > 1:
            cmd.sub, cmd.args = 'pr ' + rest[1], rest[2:]
        else:
            cmd.sub, cmd.args = (rest[0] if rest else None), rest[1:]
    else:
        i = 0
        while i < len(a) and a[i].startswith('-'):
            i += 1
        cmd.sub, cmd.args = (GT_ALIASES.get(a[i], a[i]) if i < len(a) else None), a[i + 1:]
    cmd.cwd = cwd
    return cmd


def scan(text, cwd):
    try:
        cmds, segs, docs, blanked = _level(text, cwd, 0)
        return Scan(True, cmds, segs, docs, blanked)
    except ScanError:
        cmds = []
        for prog, sub, pat in FALLBACK:
            if re.search(pat, text):
                c = Cmd(prog, None, None, None, text, 0, len(text))
                c.sub = sub
                cmds.append(c)
        return Scan(False, cmds, [], [], text)


# ---- classification helpers for the pieces --------------------------------------------------------

def push_info(args, default):
    """(delete, force, to_default) for `git push <args>`."""
    delete = force = to_default = False
    positional, i = [], 0
    while i < len(args):
        a = args[i]
        if a in ('--repo', '--receive-pack', '--exec', '-o', '--push-option'):
            i += 2
            continue
        if a in ('--delete', '-d'):
            delete = True
        elif a in ('--force', '--mirror') or a.startswith('--force-with-lease'):
            force = True
        elif re.match(r'^-[a-zA-Z]+$', a):
            force = force or 'f' in a
            delete = delete or 'd' in a
        elif not a.startswith('-') and not REDIRECT.match(a):
            positional.append(a)
        i += 1
    for ref in positional[1:]:
        if ref.startswith('+'):
            force = True
        if ref.startswith(':'):
            delete = True
        dst = ref.lstrip('+').split(':')[-1]
        if dst in (default, f'refs/heads/{default}'):
            to_default = True
    return delete, force, to_default


def is_rebase(cmd):
    if cmd.prog != 'git':
        return False
    if cmd.sub == 'rebase':
        return True
    if cmd.sub == 'pull' and cmd.args is not None:
        return any(a in ('--rebase', '-r') or (a.startswith('--rebase=') and a != '--rebase=false')
                   for a in cmd.args)
    return cmd.sub == 'pull' and cmd.args is None


def gh_api_merge(args, subagent):
    """True when `gh api <args>` merges: a non-GET call to a merge path, or a merging GraphQL mutation."""
    if args is None:
        return True
    method, body, path, query, from_file, i = None, False, None, '', False, 0
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else ''
        if a in ('-X', '--method'):
            method, i = nxt.upper(), i + 2
            continue
        if a.startswith('--method='):
            method = a.split('=', 1)[1].upper()
        elif a.startswith('-X') and len(a) > 2:
            method = a[2:].upper()
        elif a in ('-f', '-F', '--field', '--raw-field') or a.startswith(('--field=', '--raw-field=')):
            body = True
            val = a.split('=', 1)[1] if a.startswith('--') and '=' in a else nxt
            if val.startswith('query='):
                q = val[len('query='):]
                if a in ('-F', '--field') or a.startswith('--field='):
                    from_file = from_file or q.startswith('@')
                query += q
            if '=' not in a:
                i += 1
        elif a == '--input':
            body = from_file = True
            i += 1
        elif a in ('-H', '--header', '-q', '--jq', '-t', '--template', '--cache', '-p', '--preview', '--hostname'):
            i += 1
        elif not a.startswith('-') and path is None:
            path = a
        i += 1
    method = method or ('POST' if body else 'GET')
    if method == 'GET':
        return False
    p = (path or '').lstrip('/')
    if p == 'graphql':
        return (from_file and subagent) or bool(re.search(r'mergePullRequest|enablePullRequestAutoMerge', query))
    if method == 'DELETE' and re.search(r'(^|/)git/refs/', p):
        return False
    return bool(re.search(r'(^|/)pulls/[^/]+/merge(/|$)|/merges(/|$)|(^|/)git/refs/', p))
```

- [ ] **Step 4: Run the tests**

Run: `python3 tests/test_shellscan.py`
Expected: OK (25 tests).

- [ ] **Step 5: Wire the suite into `tests/run.sh`**

Add `"$root/scripts/shellscan.py"` to the py_compile line. After the `## backlog.py + ui.py` section, add:

```bash
echo "## shellscan.py + hooks.py"
check "test_shellscan.py" python3 "$root/tests/test_shellscan.py"
```

Run: `tests/run.sh`
Expected: ends `failed 0`.

- [ ] **Step 6: Commit**

```bash
git add scripts/shellscan.py tests/test_shellscan.py tests/run.sh
git commit -m "Add shellscan.py: fail-closed git/gh/gt detection in Bash commands"
```

---

### Task 4: `hooks.py` common path, run marker and orchestrator CLIs

**Files:**
- Create: `scripts/hooks.py`
- Create: `tests/test_hooks.py`
- Modify: `tests/run.sh` (py_compile list; the `## shellscan.py + hooks.py` section)

**Interfaces:**
- Consumes: `state.toplevel`, `state.main_checkout`, `state.repo_key`, `state.home`, `state.DEFAULT_HOME`, `state.state_dirs`, `state.read_json`, `state.write_json` (Task 2); `cfg.config_path(repo, use_env=False)`, `cfg.parse`, `cfg.DEFAULTS` (Task 2); `shellscan.scan` (Task 3).
- Produces (`scripts/hooks.py`), used by Tasks 5–10:
  - registries: `HANDLERS[event] = fn(h)`; `BASH_PIECES[order] = fn(h, sc)`; `EDIT_PIECES[order] = fn(h, repo, path)`; `POST_EDIT[order] = fn(h, tool_input)`. Orders: piece 2 → 0, piece 1 → 1, piece 3 → 3, piece 4 → 4, piece 10 → 10. Each later task registers right after its functions.
  - constants: `HERE` (the scripts dir), `BATCH`, `SHA`, `CONTROL`, `EDIT_TOOLS`, `BACKLOG_DEFAULT`, `RULES_DEFAULT`, `CONFIG_NAMES`, `RUN_TTL`, `BATCH_TTL`
  - `git_out(cwd, *args, timeout=10) -> str | None`; `same_file(a, b) -> bool`
  - `class Repo`: `top` (realpath), `main`, `cfg_path`, `cfg`, `get(key, default=None)`, `list(key)`, `dirs`, `backlog`, `rules`, `disabled(piece)`, `rel(path)`
  - `repo_at(path) -> Repo | None` (None outside a foreman repo; walks up to the nearest existing dir)
  - `class Hook`: `inp`, `sid`, `agent`, `cwd`, `denies`, `touch: set`, `deny(reason)`, `run_active(repo)`, `active(repo, piece, run_only=True)`
  - `cmd_repo(cmd, h) -> Repo | None`
  - `marker(repo, sid)`, `touch_marker(repo, sid)`, `end_run(sid)`, `run_args(inp)`
  - `control_call(command)`, `control_words(words) -> (verb, args) | None`
  - `batch_file(d, sid, batch, ext)`, `released(repo, sid, batch) -> dict | None`, `agent_entry(repo, sid, agent) -> dict | None`, `forget(repo, sid, batch)`
- Produces (`tests/test_hooks.py`), extended by Tasks 5–10: `CONFIG`, `BACKLOG`, `HOOKS`, `GATE_LOCK`, `write(path, text)`, `git(cwd, *args, check=True)`, `class Fx` (`work`, `origin`, `tmp`, `env`, `state`, `hook(event, payload)`, `cli(*args, cwd=None)`, `payload(tool, tool_input, sid='S1', agent=None, cwd=None, event='PreToolUse')`, `bash(command, **kw) -> reason | None`, `edit(path, tool='Edit', **kw) -> reason | None`, `start_run(sid='S1', prompt='/foreman')`, `marker(sid='S1')`, `commit(rel, text, msg='change', cwd=None)`, `gate(*cmd, cwd=None, name=None, owner='w1')`), and `class Case` (a fresh `Fx` per test; subclasses may set `config`).

**Probe fallbacks** (from the probes doc):
- **P1:** `run_args` reads `UserPromptSubmit.prompt` and `UserPromptExpansion`'s `command_name` plus its ``This run's arguments: `…` `` line. If P1 failed outright, stop and report (see Task 1).
- **P3, P5:** no code change.
- **P4:** handled by `Repo.dirs` and by `pretool` writing records from its own input.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hooks.py`. Later tasks add test classes above its `if __name__ == '__main__':` line.

```python
# tests/test_hooks.py: feeds hook JSON to scripts/hooks.py in temp repos (temp FOREMAN_HOME, fake gh)
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.realpath(os.path.join(HERE, '..', 'scripts'))
HOOKS = os.path.join(SCRIPTS, 'hooks.py')
GATE_LOCK = os.path.join(SCRIPTS, 'gate-lock.sh')
sys.path.insert(0, SCRIPTS)
import state  # noqa: E402

CONFIG = '''---
gate: echo gated
backlog_path: .claude/improvement-backlog.md
id_prefix: IB
forbid_trailers: [Co-Authored-By, Claude-Session, Generated with]
worktree_env_files: [.env.local]
---
'''
BACKLOG = '# Backlog\n\n| IB-1 | One | low | ready | A | s |\n| IB-2 | Two | low | ready | B | s |\n'
FAKE_GH = '''#!/bin/sh
echo "$@" >> "$FAKE_GH_LOG"
case "$1 $2" in
  "pr view") if [ -f "$FAKE_GH_PR" ]; then cat "$FAKE_GH_PR"; else echo '{"headRefName":"feat","commits":[]}'; fi ;;
esac
'''


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def git(cwd, *args, check=True):
    p = subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@e', '-c', 'init.defaultBranch=main', *args],
                       cwd=cwd, capture_output=True, text=True)
    if check and p.returncode:
        raise AssertionError(f'git {args}: {p.stderr}')
    return p.stdout


class Fx:
    """A clone of a bare origin with a foreman config, plus the env every hook call runs with."""

    def __init__(self, config=CONFIG):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='foreman-hooks-'))
        self.origin = os.path.join(self.tmp, 'origin.git')
        self.work = os.path.join(self.tmp, 'work')
        git(self.tmp, 'init', '-q', '--bare', '-b', 'main', self.origin)
        git(self.tmp, 'clone', '-q', self.origin, self.work)
        git(self.work, 'symbolic-ref', 'HEAD', 'refs/heads/main')
        if config is not None:
            write(os.path.join(self.work, '.claude/foreman.config.md'), config)
            write(os.path.join(self.work, '.claude/improvement-backlog.md'), BACKLOG)
        write(os.path.join(self.work, 'app.py'), 'print(1)\n')
        git(self.work, 'add', '-A')
        git(self.work, 'commit', '-q', '-m', 'init')
        git(self.work, 'push', '-q', '-u', 'origin', 'main')
        git(self.work, 'remote', 'set-head', 'origin', 'main')
        bin_dir = os.path.join(self.tmp, 'bin')
        write(os.path.join(bin_dir, 'gh'), FAKE_GH)
        os.chmod(os.path.join(bin_dir, 'gh'), 0o755)
        self.home = os.path.join(self.tmp, 'home')
        self.env = dict(os.environ, HOME=os.path.join(self.tmp, 'user'), FOREMAN_HOME=self.home,
                        FOREMAN_LOCK_ROOT=os.path.join(self.tmp, 'locks'), FOREMAN_LOCK_POLL='1',
                        PATH=bin_dir + os.pathsep + os.environ['PATH'],
                        FAKE_GH_LOG=os.path.join(self.tmp, 'gh.log'), FAKE_GH_PR=os.path.join(self.tmp, 'pr.json'),
                        GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@e', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@e')
        self.env.pop('FOREMAN_CONFIG', None)
        os.makedirs(self.env['HOME'])

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @property
    def state(self):
        return os.path.join(self.home, state.repo_key(self.work))

    def hook(self, event, payload):
        p = subprocess.run([sys.executable, HOOKS, event], input=json.dumps(payload), capture_output=True,
                           text=True, env=self.env, cwd=payload.get('cwd', self.work))
        assert p.returncode == 0, p.stderr
        return (json.loads(p.stdout) if p.stdout.strip() else None), p.stderr

    def cli(self, *args, cwd=None):
        return subprocess.run([sys.executable, HOOKS, *args], capture_output=True, text=True, env=self.env,
                              cwd=cwd or self.work).returncode

    def payload(self, tool, tool_input, sid='S1', agent=None, cwd=None, event='PreToolUse'):
        d = {'hook_event_name': event, 'session_id': sid, 'cwd': cwd or self.work, 'tool_name': tool,
             'tool_input': tool_input}
        if agent:
            d.update(agent_id=agent, agent_type='general-purpose')
        return d

    def bash(self, command, **kw):
        """The deny reason pretool gives for this Bash command, or None when it allows it."""
        out, _ = self.hook('pretool', self.payload('Bash', {'command': command}, **kw))
        return out['hookSpecificOutput']['permissionDecisionReason'] if out else None

    def edit(self, path, tool='Edit', **kw):
        out, _ = self.hook('pretool', self.payload(tool, {'file_path': path}, **kw))
        return out['hookSpecificOutput']['permissionDecisionReason'] if out else None

    def start_run(self, sid='S1', prompt='/foreman'):
        return self.hook('run-start', {'hook_event_name': 'UserPromptSubmit', 'session_id': sid, 'cwd': self.work,
                                       'prompt': prompt})

    def marker(self, sid='S1'):
        return state.read_json(os.path.join(self.state, 'runs', sid + '.json'))

    def commit(self, rel, text, msg='change', cwd=None):
        write(os.path.join(cwd or self.work, rel), text)
        git(cwd or self.work, 'add', '-A')
        git(cwd or self.work, 'commit', '-q', '-m', msg)

    def gate(self, *cmd, cwd=None, name=None, owner='w1'):
        """Run gate-lock.sh; with no cmd, the configured gate as one argument (the minting form)."""
        argv = [GATE_LOCK] + (['--name', name] if name else []) + [owner, '--'] + (list(cmd) or ['echo gated'])
        return subprocess.run(argv, capture_output=True, text=True, env=self.env, cwd=cwd or self.work)


class Case(unittest.TestCase):
    config = CONFIG

    def setUp(self):
        self.fx = Fx(self.config)

    def tearDown(self):
        self.fx.close()


class CommonPathTest(Case):
    def test_malformed_stdin_exits_zero_silently(self):
        p = subprocess.run([sys.executable, HOOKS, 'pretool'], input='not json', capture_output=True, text=True,
                           env=self.fx.env)
        self.assertEqual((p.returncode, p.stdout), (0, ''))

    def test_own_error_fails_open(self):
        os.remove(os.path.join(self.fx.work, '.claude/foreman.config.md'))
        os.makedirs(os.path.join(self.fx.work, '.claude/foreman.config.md'))
        out, err = self.fx.hook('pretool', self.fx.payload('Bash', {'command': f'python3 {HOOKS} release w1a abc1234'}))
        self.assertIsNone(out)
        self.assertIn('foreman hook error: IsADirectoryError', err)

    def test_fast_path_skips_commands_without_git(self):
        os.remove(os.path.join(self.fx.work, '.claude/foreman.config.md'))
        os.makedirs(os.path.join(self.fx.work, '.claude/foreman.config.md'))
        out, err = self.fx.hook('pretool', self.fx.payload('Bash', {'command': 'ls -la && npm test'}))
        self.assertEqual((out, err), (None, ''))


class RunMarkerTest(Case):
    def test_prompts_that_start_a_run(self):
        for i, prompt in enumerate(('/foreman', '/foreman:foreman parallel 2', '/loop 2h /foreman',
                                    '/loop /foreman --auto-merge', '  /foreman IB-12')):
            self.fx.start_run(sid=f'S{i}', prompt=prompt)
            self.assertIsNotNone(self.fx.marker(f'S{i}'), prompt)
        self.assertEqual(self.fx.marker('S1')['args'], 'parallel 2')

    def test_prompts_that_do_not(self):
        for i, prompt in enumerate(('please run /foreman later', '/foreman ui', '/foreman init', '/foreman ui stop',
                                    '/foremanx', '/loop 2h /foreman ui')):
            self.fx.start_run(sid=f'N{i}', prompt=prompt)
            self.assertIsNone(self.fx.marker(f'N{i}'), prompt)

    def test_user_prompt_expansion(self):
        def expand(sid, name, args):
            self.fx.hook('run-start', {'hook_event_name': 'UserPromptExpansion', 'session_id': sid, 'cwd': self.fx.work,
                                       'command_name': name,
                                       'expanded_prompt': f"# Foreman\n\nThis run's arguments: `{args}` (empty means none)."})
        expand('E1', 'foreman:foreman', 'parallel 2')
        expand('E2', 'foreman', 'ui')
        expand('E3', 'other', '')
        self.assertEqual(self.fx.marker('E1')['args'], 'parallel 2')
        self.assertIsNone(self.fx.marker('E2'))
        self.assertIsNone(self.fx.marker('E3'))

    def test_repeats_update_args_and_touched(self):
        self.fx.start_run(prompt='/foreman')
        first = self.fx.marker()
        time.sleep(0.01)
        self.fx.start_run(prompt='/foreman parallel 3')
        again = self.fx.marker()
        self.assertEqual(again['started'], first['started'])
        self.assertGreater(again['touched'], first['touched'])
        self.assertEqual(again['args'], 'parallel 3')

    def test_no_config_no_marker_and_foreman_config_is_ignored(self):
        fx = Fx(config=None)
        try:
            write(os.path.join(fx.tmp, 'elsewhere.md'), CONFIG)
            fx.env['FOREMAN_CONFIG'] = os.path.join(fx.tmp, 'elsewhere.md')
            fx.start_run()
            self.assertIsNone(fx.marker())
        finally:
            fx.close()
        self.fx.env['FOREMAN_CONFIG'] = os.path.join(self.fx.tmp, 'missing.md')
        self.fx.start_run()
        self.assertIsNotNone(self.fx.marker())

    def test_session_end_reasons(self):
        for reason, kept in (('clear', False), ('logout', False), ('resume', True), ('other', True),
                             ('prompt_input_exit', True)):
            self.fx.start_run()
            self.fx.hook('session-end', {'hook_event_name': 'SessionEnd', 'session_id': 'S1', 'cwd': self.fx.work,
                                     'reason': reason})
            self.assertEqual(self.fx.marker() is not None, kept, reason)

    def test_run_end_deletes_only_the_calling_sessions_marker(self):
        self.fx.start_run('S1')
        self.fx.start_run('S2')
        call = f'python3 {HOOKS} run-end'
        self.fx.hook('posttool', self.fx.payload('Bash', {'command': call}, sid='S1', agent='a1', event='PostToolUse'))
        self.assertIsNotNone(self.fx.marker('S1'))
        self.fx.hook('posttool', self.fx.payload('Bash', {'command': call + '; true'}, sid='S1', event='PostToolUse'))
        self.assertIsNotNone(self.fx.marker('S1'))
        self.fx.hook('posttool', self.fx.payload('Bash', {'command': call}, sid='S1', event='PostToolUse'))
        self.assertIsNone(self.fx.marker('S1'))
        self.assertIsNotNone(self.fx.marker('S2'))
        self.assertEqual(self.fx.cli('run-end'), 0)

    def test_48_hour_expiry(self):
        self.fx.start_run('S2')
        old = time.time() - 49 * 3600
        state.write_json(os.path.join(self.fx.state, 'runs', 'S2.json'), {'started': old, 'touched': old, 'args': ''})
        self.assertEqual(self.fx.cli('release', 'w1a', 'abc1234'), 3)
        self.fx.start_run('S1')
        self.assertFalse(os.path.exists(os.path.join(self.fx.state, 'runs', 'S2.json')))


class ControlTest(Case):
    def release(self, batch='w1a', sha='abc1234', **kw):
        return self.fx.bash(f'python3 {HOOKS} release {batch} {sha}', **kw)

    def test_release_is_recorded_by_pretool_from_the_main_session(self):
        self.fx.start_run()
        self.assertEqual(self.fx.cli('release', 'w1a', 'abc1234'), 1)
        self.assertIsNone(self.release())
        rec = state.read_json(os.path.join(self.fx.state, 'batches', 'S1', 'w1a.released'))
        self.assertEqual(rec['sha'], 'abc1234')
        self.assertEqual(self.fx.cli('release', 'w1a', 'abc1234'), 0)

    def test_release_exits_3_without_a_fresh_marker(self):
        self.assertEqual(self.fx.cli('release', 'w1a', 'abc1234'), 3)
        self.fx.start_run()
        later = time.strftime('%Y%m%d-%H%M', time.localtime(time.time() + 3600))
        self.assertEqual(self.fx.cli('release', f'{later}-w1a', 'abc1234'), 3)
        earlier = time.strftime('%Y%m%d-%H%M', time.localtime(time.time() - 3600))
        self.assertEqual(self.fx.cli('release', f'{earlier}-w1a', 'abc1234'), 1)

    def test_a_subagent_cannot_release_stop_or_end(self):
        self.fx.start_run()
        for verb in ('release w1a abc1234', 'stopping w1a', 'forget w1a', 'run-end'):
            why = self.fx.bash(f'python3 {HOOKS} {verb}', agent='a1')
            self.assertIn('only the orchestrator', why)
        self.assertIn('only the orchestrator', self.fx.bash(f"bash -c 'python3 {HOOKS} release w1a abc1234'", agent='a1'))
        self.assertFalse(os.path.exists(os.path.join(self.fx.state, 'batches')))

    def test_stopping_and_forget(self):
        self.fx.start_run()
        self.fx.bash(f'python3 {HOOKS} stopping w1a')
        self.release()
        self.assertEqual(self.fx.cli('stopping', 'w1a'), 0)
        agents = os.path.join(self.fx.state, 'batches', 'S1', 'agents')
        state.write_json(os.path.join(agents, 'a1'), {'batch': 'w1a', 'spec': True, 'time': 1})
        state.write_json(os.path.join(agents, 'a2'), {'batch': 'w1b', 'spec': True, 'time': 1})
        self.assertIsNone(self.fx.bash(f'python3 {HOOKS} forget w1a'))
        self.assertEqual(sorted(os.listdir(os.path.join(self.fx.state, 'batches', 'S1'))), ['agents'])
        self.assertEqual(os.listdir(agents), ['a2'])
        self.assertEqual(self.fx.cli('forget', 'w1a'), 0)
        self.assertEqual(self.fx.cli('stopping', 'w1a'), 1)

    def test_only_the_exact_call_is_recorded(self):
        self.fx.start_run()
        self.fx.bash(f'python3 {HOOKS} release w1a abc1234 && echo done')
        self.fx.bash(f'python3 {HOOKS} release w1a not-a-sha')
        self.fx.bash(f'python3 {HOOKS} release "w1a;x" abc1234')
        self.assertFalse(os.path.exists(os.path.join(self.fx.state, 'batches')))
        self.assertEqual(self.fx.cli('release', 'w1a', 'not-a-sha'), 2)

    def test_default_home_marker_counts(self):
        default = os.path.join(self.fx.env['HOME'], '.claude', 'foreman', state.repo_key(self.fx.work))
        state.write_json(os.path.join(default, 'runs', 'S1.json'), {'started': time.time(), 'touched': time.time()})
        self.assertIsNone(self.release())
        self.assertEqual(self.fx.cli('release', 'w1a', 'abc1234'), 0)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 tests/test_hooks.py`
Expected: FAIL; every test errors because `scripts/hooks.py` doesn't exist (`can't open file … hooks.py`).

- [ ] **Step 3: Create `scripts/hooks.py`**

The section comments (`# ---- … ----`) are anchors: later tasks insert their pieces **above `# ---- pretool / posttool ----`**.

```python
#!/usr/bin/env python3
"""foreman's settings hooks: one dispatcher, run by hooks/hooks.json as `python3 hooks.py <event>`.

Hook mode, JSON on stdin: run-start, session-end, pretool, posttool, subagent-start, subagent-stop, permission.
(SessionEnd runs `session-end`, not `run-end`: the CLI verb never reads stdin, which a Bash call may hold open.)

CLI mode, run from the orchestrator's Bash tool. `pretool` records the effect from its own hook input, so the
record lands where the hooks look and is bound to this session:
  hooks.py run-end                 end this session's run (posttool deletes the marker)
  hooks.py release <batch> <sha>   release a spec-phase batch (piece 10)
  hooks.py stopping <batch>        let the tagged workers of <batch> launched so far stop (piece 7)
  hooks.py forget <batch>          drop the batch's hook state (W5)
  exit 0 = recorded, 1 = the record is missing, 3 = hooks inactive (no fresh run marker); run-end always 0

Every hook fails open on its own errors (stderr note, exit 0), except piece 1's undeterminable diff.
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FAST = re.compile(r'\b(git|gh|gt)\b|hooks\.py')
RUN_TTL = 48 * 3600
BATCH_TTL = 14 * 86400
BATCH = re.compile(r'^[A-Za-z0-9_-]+$')
SHA = re.compile(r'^[0-9a-f]{7,40}$')
CONTROL = ('run-end', 'release', 'stopping', 'forget')
CONTROL_TEXT = re.compile(r'hooks\.py\S*\s+(run-end|release|stopping|forget)\b')
EDIT_TOOLS = ('Edit', 'Write', 'MultiEdit', 'NotebookEdit')
BACKLOG_DEFAULT = '.claude/improvement-backlog.md'
RULES_DEFAULT = '.claude/rules/*.md'
CONFIG_NAMES = ('.claude/foreman.config.md', '.claude/improve-next.config.md')
RUN_PROMPT = re.compile(r'^\s*(?:/loop(?:\s+(?!/)\S+)?\s+)?/foreman(?::foreman)?(?=\s|$)(.*)', re.S)

HANDLERS = {}      # event -> fn(h)
BASH_PIECES = {}   # order -> fn(h, sc); piece 2 runs first so its local merge deny saves pieces 3 and 4 a network call
EDIT_PIECES = {}   # order -> fn(h, repo, path)
POST_EDIT = {}     # order -> fn(h, tool_input)


def _load():
    """Imports the fast paths skip: a Bash call that names no git/gh/gt/hooks.py costs one json.load."""
    global backlog, cfg, state, shellscan, subprocess, shlex, glob, tempfile
    sys.path.insert(0, HERE)
    import backlog  # noqa: F401
    import cfg  # noqa: F401
    import glob  # noqa: F401
    import shellscan  # noqa: F401
    import shlex  # noqa: F401
    import state  # noqa: F401
    import subprocess  # noqa: F401
    import tempfile  # noqa: F401


# ---- repo context ---------------------------------------------------------------------------------

def git_out(cwd, *args, timeout=10):
    try:
        p = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def same_file(a, b):
    return os.path.realpath(os.path.expanduser(a)) == os.path.realpath(b)


class Repo:
    """One checkout as the hooks see it: its top level, its main checkout, the main checkout's config."""

    def __init__(self, top):
        self.top = os.path.realpath(top)
        self.main = state.main_checkout(top) or top
        self.cfg_path = cfg.config_path(self.main, use_env=False)
        self.cfg = cfg.parse(open(self.cfg_path, encoding='utf-8').read()) if self.cfg_path else {}
        self._dirs = None

    def get(self, key, default=None):
        v = self.cfg.get(key)
        if v is None:
            return default if default is not None else cfg.DEFAULTS.get(key)
        return v

    def list(self, key):
        v = self.get(key)
        return v if isinstance(v, list) else ([] if v in (None, '') else [v])

    @property
    def dirs(self):
        if self._dirs is None:
            self._dirs = state.state_dirs(self.main)
        return self._dirs

    @property
    def backlog(self):
        return self.get('backlog_path', BACKLOG_DEFAULT)

    @property
    def rules(self):
        v = self.get('rules_glob', RULES_DEFAULT)
        return v if isinstance(v, list) else [v]

    def disabled(self, piece):
        return piece in self.list('hooks_disabled')

    def rel(self, path):
        return os.path.relpath(os.path.realpath(path), self.top)


_repos = {}


def repo_at(path):
    """The Repo holding `path` (a file or a directory, existing or not), or None outside a foreman repo."""
    d = os.path.abspath(path)
    while not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    top = state.toplevel(d)
    if not top:
        return None
    if top not in _repos:
        _repos[top] = Repo(top)
    return _repos[top] if _repos[top].cfg_path else None


class Hook:
    """One hook call: its input, the denies it collects, and the repos whose run marker it touches."""

    def __init__(self, inp):
        self.inp = inp
        self.sid = inp.get('session_id') or ''
        self.agent = inp.get('agent_id')
        self.cwd = inp.get('cwd') or os.getcwd()
        self.denies = []
        self.touch = set()
        self._active = {}

    def deny(self, reason):
        self.denies.append(reason)

    def run_active(self, repo):
        if repo.top not in self._active:
            self._active[repo.top] = marker(repo, self.sid) is not None
        return self._active[repo.top]

    def active(self, repo, piece, run_only=True):
        """True when `piece` applies in `repo`: a foreman repo, not disabled, and (run-only) a live run."""
        return bool(repo) and not repo.disabled(piece) and (not run_only or self.run_active(repo))


def cmd_repo(c, h):
    return repo_at(c.cwd or h.cwd)


# ---- the run marker -------------------------------------------------------------------------------

def marker(repo, sid):
    """(path, data) of this session's live run marker, or None."""
    if not sid or os.sep in sid:
        return None
    for d in repo.dirs:
        p = os.path.join(d, 'runs', sid + '.json')
        m = state.read_json(p)
        if isinstance(m, dict) and time.time() - float(m.get('touched', 0)) < RUN_TTL:
            return p, m
    return None


def touch_marker(repo, sid):
    m = marker(repo, sid)
    if m:
        m[1]['touched'] = time.time()
        state.write_json(m[0], m[1])


def prune(sd):
    """Drop markers untouched for 48 hours and batch files older than 14 days."""
    now = time.time()
    for p in glob.glob(os.path.join(sd, 'runs', '*.json')):
        m = state.read_json(p)
        if not isinstance(m, dict) or now - float(m.get('touched', 0)) >= RUN_TTL:
            os.remove(p)
    for p in glob.glob(os.path.join(sd, 'batches', '*', '*')) + glob.glob(os.path.join(sd, 'batches', '*', 'agents', '*')):
        if os.path.isfile(p) and now - os.path.getmtime(p) >= BATCH_TTL:
            os.remove(p)


def run_args(inp):
    """The /foreman arguments when this prompt starts a run, else None. `ui` and `init` aren't runs."""
    if inp.get('hook_event_name') == 'UserPromptExpansion':
        if inp.get('command_name') not in ('foreman', 'foreman:foreman'):
            return None
        m = re.search(r"This run's arguments: `([^`]*)`", inp.get('expanded_prompt') or '')
        args = m.group(1).strip() if m else ''
    else:
        m = RUN_PROMPT.match(inp.get('prompt') or '')
        if not m:
            return None
        args = m.group(1).strip()
    words = args.split()
    return None if words and words[0] in ('ui', 'init') else args


def run_start(h):
    args = run_args(h.inp)
    repo = repo_at(h.cwd) if args is not None and h.sid and os.sep not in h.sid else None
    if not repo:
        return
    sd = repo.dirs[0]
    prune(sd)
    p = os.path.join(sd, 'runs', h.sid + '.json')
    m = state.read_json(p)
    m = m if isinstance(m, dict) else {'started': time.time()}
    m.update(touched=time.time(), args=args)
    state.write_json(p, m)


def end_run(sid):
    """Delete this session's run markers. A glob, no git: every SessionEnd hook shares 1.5 seconds."""
    if not sid or os.sep in sid:
        return
    for home in {state.home(), state.DEFAULT_HOME}:
        for p in glob.glob(os.path.join(glob.escape(home), '*', 'runs', glob.escape(sid) + '.json')):
            os.remove(p)


def session_end(h):
    if h.inp.get('reason') in ('clear', 'logout'):
        end_run(h.sid)


HANDLERS['run-start'] = run_start
HANDLERS['session-end'] = session_end


# ---- orchestrator CLIs: run-end, release, stopping, forget ----------------------------------------

def control_call(command):
    """(verb, args) when `command` is exactly `python3 <this hooks.py> <verb> <args>`, else None."""
    try:
        w = shlex.split(command)
    except ValueError:
        return None
    return control_words(w[1:]) if len(w) >= 3 and w[0] == 'python3' else None


def control_words(w):
    """(verb, args) for the words `<this hooks.py> <verb> <args...>`, else None."""
    if len(w) < 2 or not same_file(w[0], os.path.join(HERE, 'hooks.py')) or w[1] not in CONTROL:
        return None
    verb, rest = w[1], w[2:]
    ok = ((verb == 'run-end' and not rest)
          or (verb == 'release' and len(rest) == 2 and BATCH.match(rest[0]) and SHA.match(rest[1]))
          or (verb in ('stopping', 'forget') and len(rest) == 1 and BATCH.match(rest[0])))
    return (verb, rest) if ok else None


def batch_file(d, sid, batch, ext):
    return os.path.join(d, 'batches', sid, f'{batch}.{ext}')


def released(repo, sid, batch):
    """The batch's release record ({sha, time}) in this session, or None."""
    for d in repo.dirs:
        r = state.read_json(batch_file(d, sid, batch, 'released'))
        if isinstance(r, dict):
            return r
    return None


def agent_entry(repo, sid, agent):
    """Piece 6's map entry {batch, spec, time} for this agent, or None."""
    if not agent or os.sep in agent:
        return None
    for d in repo.dirs:
        e = state.read_json(os.path.join(d, 'batches', sid, 'agents', agent))
        if isinstance(e, dict):
            return e
    return None


def forget(repo, sid, batch):
    for d in repo.dirs:
        for p in glob.glob(os.path.join(glob.escape(os.path.join(d, 'batches', sid)), glob.escape(batch) + '.*')):
            os.remove(p)
        for p in glob.glob(os.path.join(glob.escape(os.path.join(d, 'batches', sid, 'agents')), '*')):
            e = state.read_json(p)
            if isinstance(e, dict) and e.get('batch') == batch:
                os.remove(p)


def pretool_control(h, repo, verb, rest):
    d = repo.dirs[0]
    if verb == 'release':
        state.write_json(batch_file(d, h.sid, rest[0], 'released'), {'sha': rest[1], 'time': time.time()})
    elif verb == 'stopping':
        state.write_json(batch_file(d, h.sid, rest[0], 'stopped'), {'time': time.time()})
    elif verb == 'forget':
        forget(repo, h.sid, rest[0])
    touch_marker(repo, h.sid)


def batch_time(batch):
    """Epoch seconds of a `YYYYMMDD-HHMM-...` batch name (local time), or 0 for an undated name."""
    m = re.match(r'^(\d{8}-\d{4})-', batch)
    try:
        return time.mktime(time.strptime(m.group(1), '%Y%m%d-%H%M')) if m else 0
    except ValueError:
        return 0


def fresh_marker(dirs, batch):
    """A live run marker in any session, touched after the batch name's time."""
    since = batch_time(batch)
    for d in dirs:
        for p in glob.glob(os.path.join(glob.escape(d), 'runs', '*.json')):
            m = state.read_json(p)
            t = float(m.get('touched', 0)) if isinstance(m, dict) else 0
            if t > since and time.time() - t < RUN_TTL:
                return True
    return False


def cli(verb, rest):
    if verb == 'run-end':
        return 0
    if not control_words([os.path.join(HERE, 'hooks.py'), verb] + rest):
        print(__doc__, file=sys.stderr)
        return 2
    _load()
    main = state.main_checkout(os.getcwd())
    dirs = state.state_dirs(main) if main and cfg.config_path(main, use_env=False) else []
    if not fresh_marker(dirs, rest[0]):
        print('foreman: hooks inactive (no fresh run marker), so nothing gates this batch', file=sys.stderr)
        return 3
    batch = glob.escape(rest[0])
    if verb == 'forget':
        return 1 if any(glob.glob(os.path.join(glob.escape(d), 'batches', '*', batch + '.*')) for d in dirs) else 0
    ext = 'released' if verb == 'release' else 'stopped'
    return 0 if any(glob.glob(os.path.join(glob.escape(d), 'batches', '*', f'{batch}.{ext}')) for d in dirs) else 1


# ---- pretool / posttool ---------------------------------------------------------------------------

def pretool(h):
    tool = h.inp.get('tool_name')
    ti = h.inp.get('tool_input') or {}
    if tool == 'Bash':
        pretool_bash(h, ti.get('command') or '')
    elif tool in EDIT_TOOLS:
        path = ti.get('file_path') or ti.get('notebook_path')
        if path:
            path = os.path.join(h.cwd, path)
            repo = repo_at(path)
            for k in sorted(EDIT_PIECES):
                EDIT_PIECES[k](h, repo, path)
    for r in h.touch:
        touch_marker(r, h.sid)
    if h.denies:
        print(json.dumps({'hookSpecificOutput': {
            'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
            'permissionDecisionReason': 'foreman: ' + '; '.join(dict.fromkeys(h.denies))}}))


def pretool_bash(h, command):
    if h.agent and CONTROL_TEXT.search(command):
        h.deny('only the orchestrator ends a run or releases a spec — a worker never runs hooks.py')
        return
    call = control_call(command)
    if call:
        repo = repo_at(h.cwd)
        if repo:
            pretool_control(h, repo, *call)
        return
    sc = shellscan.scan(command, h.cwd)
    for k in sorted(BASH_PIECES):
        BASH_PIECES[k](h, sc)


def posttool(h):
    ti = h.inp.get('tool_input') or {}
    if h.inp.get('tool_name') == 'Bash':
        if not h.agent and control_call(ti.get('command') or '') == ('run-end', []):
            end_run(h.sid)
        return
    for k in sorted(POST_EDIT):
        POST_EDIT[k](h, ti)


HANDLERS['pretool'] = pretool
HANDLERS['posttool'] = posttool


# ---- dispatcher -----------------------------------------------------------------------------------

def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    event = argv[1]
    if event in CONTROL:
        sys.exit(cli(event, argv[2:]))
    try:
        inp = json.load(sys.stdin)
    except ValueError:
        return
    if not isinstance(inp, dict):
        return
    command = (inp.get('tool_input') or {}).get('command') or '' if inp.get('tool_name') == 'Bash' else None
    if event == 'posttool' and command is not None and (inp.get('agent_id') or 'hooks.py run-end' not in command):
        return
    if event == 'pretool' and command is not None and not FAST.search(command):
        return
    try:
        _load()
        fn = HANDLERS.get(event)
        if fn:
            fn(Hook(inp))
    except Exception as e:  # fail open
        print(f'foreman hook error: {type(e).__name__}: {e}', file=sys.stderr)


if __name__ == '__main__':
    main(sys.argv)
```

- [ ] **Step 4: Run the tests**

Run: `python3 tests/test_hooks.py`
Expected: OK (17 tests).

- [ ] **Step 5: Wire the suite into `tests/run.sh`**

Add `"$root/scripts/hooks.py"` to the py_compile line. In the `## shellscan.py + hooks.py` section, add:

```bash
check "test_hooks.py" python3 "$root/tests/test_hooks.py"
```

Run: `tests/run.sh`
Expected: ends `failed 0`.

- [ ] **Step 6: Commit**

```bash
git add scripts/hooks.py tests/test_hooks.py tests/run.sh
git commit -m "Add hooks.py: dispatcher, run marker, orchestrator CLIs (run-end, release, stopping, forget)"
```

---

### Task 5: Gate receipts: `gate-lock.sh` minting and piece 1

**Files:**
- Modify: `scripts/state.py` (docstring, imports, `RECEIPT_TTL`, a receipts section, `main`)
- Modify: `scripts/gate-lock.sh` (header comment; heartbeat redirect at line 62; the command block at lines 69–70)
- Modify: `scripts/hooks.py` (insert piece 1 above `# ---- pretool / posttool ----`)
- Modify: `tests/test_hooks.py` (new classes above `if __name__ == '__main__':`)

**Interfaces:**
- Consumes: `state.main_checkout`, `state.state_dirs`, `state.ensure`, `state.read_json`, `state.write_json`, `state.git`, `state.toplevel` (Task 2); `Repo`, `Hook`, `cmd_repo`, `git_out`, `BASH_PIECES`, `HERE` (Task 4); `shellscan.push_info` (Task 3); `Fx`, `Case`, `CONFIG`, `BACKLOG`, `GATE_LOCK`, `write`, `git` (Task 4 tests).
- Produces:
  - `state.last_path(sd, top) -> str`; `state.untracked(top) -> set`; `state.write_tree(top, exclude) -> str`; `state.prune_receipts(sd)`; `state.mint(top, owner, command, before_file) -> tree`; CLI `state.py mint <top> <owner> <command> <before-file>`
  - receipt `state_dir/gate/<tree>` = `{command, owner, time, toplevel}`; pointer `state_dir/gate/last/<sha1(realpath(top))>` = `{tree, artifacts, time, toplevel}`; both written to **every** dir of `state_dirs(main)`, so the hooks find them whichever FOREMAN_HOME they see (the P4 gap: a FOREMAN_HOME set only for the Bash tool)
  - `hooks.glob_re(pattern)`, `hooks.glob_match(path, pattern) -> bool`, `hooks.exempt(repo, path, receipt=False) -> bool`, `hooks.default_branch(top) -> str`, `hooks.receipt_check(repo, cwd) -> reason | None`, `hooks.is_push_trigger(cmd) -> bool`, `hooks.UNREADABLE`; registers `BASH_PIECES[1] = gate_before_push`
  - test helper `head_tree(cwd)`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_hooks.py` above `if __name__ == '__main__':`. They cover the spec's piece 1 list, the revision 6 receipt cases, the scoping rows (no config, `FOREMAN_CONFIG` ignored, no marker), `hooks_disabled`, the marker touch, and Review Focus item 2.

```python
def head_tree(cwd):
    return git(cwd, 'rev-parse', 'HEAD^{tree}').strip()


class ReceiptMintTest(Case):
    def receipts(self):
        d = os.path.join(self.fx.state, 'gate')
        return sorted(n for n in os.listdir(d) if n != 'last') if os.path.isdir(d) else []

    def test_only_the_configured_gate_as_one_argument_mints(self):
        self.assertEqual(self.fx.gate('echo', 'gated').returncode, 0)
        self.assertEqual(self.fx.gate(name='db').returncode, 0)
        self.assertEqual(self.fx.gate('echo other').returncode, 0)
        self.assertEqual(self.receipts(), [])
        self.assertEqual(self.fx.gate().returncode, 0)
        self.assertEqual(self.receipts(), [head_tree(self.fx.work)])
        rec = state.read_json(os.path.join(self.fx.state, 'gate', head_tree(self.fx.work)))
        self.assertEqual((rec['command'], rec['owner'], rec['toplevel']), ('echo gated', 'w1', self.fx.work))

    def test_a_failing_gate_mints_nothing_and_keeps_its_exit_code(self):
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'), CONFIG.replace('echo gated', 'exit 7'))
        self.assertEqual(self.fx.gate('exit 7').returncode, 7)
        self.assertEqual(self.receipts(), [])

    def test_receipt_tree_keeps_a_force_added_ignored_file(self):
        write(os.path.join(self.fx.work, '.gitignore'), 'secret.txt\n')
        write(os.path.join(self.fx.work, 'secret.txt'), 'x\n')
        git(self.fx.work, 'add', '.gitignore')
        git(self.fx.work, 'add', '-f', 'secret.txt')
        git(self.fx.work, 'commit', '-q', '-m', 'forced')
        self.fx.gate()
        self.assertEqual(self.receipts(), [head_tree(self.fx.work)])

    def test_receipt_tree_with_a_crlf_blob_under_text_auto(self):
        write(os.path.join(self.fx.work, '.gitattributes'), '* text=auto\n')
        with open(os.path.join(self.fx.work, 'win.txt'), 'wb') as f:
            f.write(b'a\r\nb\r\n')
        git(self.fx.work, 'add', '-A')
        git(self.fx.work, 'commit', '-q', '-m', 'crlf')
        self.fx.gate()
        self.assertEqual(self.receipts(), [head_tree(self.fx.work)])

    def test_a_gate_run_from_a_subdirectory_runs_at_the_top_level(self):
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'), CONFIG.replace('echo gated', 'pwd > "$GATE_OUT"'))
        self.fx.env['GATE_OUT'] = os.path.join(self.fx.tmp, 'where')
        os.makedirs(os.path.join(self.fx.work, 'sub'))
        self.assertEqual(self.fx.gate('pwd > "$GATE_OUT"', cwd=os.path.join(self.fx.work, 'sub')).returncode, 0)
        with open(self.fx.env['GATE_OUT']) as f:
            self.assertEqual(os.path.realpath(f.read().strip()), self.fx.work)
        self.assertEqual(len(self.receipts()), 1)


class GateBeforePushTest(Case):
    def setUp(self):
        super().setUp()
        self.fx.start_run()
        git(self.fx.work, 'switch', '-q', '-c', 'feat')

    def test_receipt_present_and_missing(self):
        self.fx.commit('app.py', 'print(2)\n')
        self.assertIn('no passing gate for this tree', self.fx.bash('git push -u origin HEAD'))
        self.fx.gate()
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))
        self.assertIsNone(self.fx.bash('gh pr create --fill'))
        self.assertIsNone(self.fx.bash('gt submit'))

    def test_exempt_only_diff_needs_no_receipt(self):
        self.fx.commit('docs/guide.md', 'x\n')
        self.fx.commit('.claude/improvement-backlog.md', BACKLOG + '| IB-3 | Three | low | ready | C | s |\n')
        self.fx.commit('.claude/rules/r.md', 'rule\n')
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))

    def test_exempt_diff_since_the_last_receipt(self):
        self.fx.commit('app.py', 'print(2)\n')
        self.fx.gate()
        self.fx.commit('README.md', 'notes\n')
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))
        self.fx.commit('app.py', 'print(3)\n')
        why = self.fx.bash('git push -u origin HEAD')
        self.assertIn('the gate ran on a different tree', why)
        self.assertIn('app.py', why)

    def test_a_rename_of_code_into_docs_is_not_exempt(self):
        os.makedirs(os.path.join(self.fx.work, 'docs'))
        git(self.fx.work, 'mv', 'app.py', 'docs/app.py')
        git(self.fx.work, 'commit', '-q', '-m', 'move')
        self.assertIn('no passing gate', self.fx.bash('git push -u origin HEAD'))

    def test_every_push_form_is_judged(self):
        self.fx.commit('app.py', 'print(2)\n')
        wt = os.path.join(self.fx.tmp, 'wt')
        git(self.fx.work, 'worktree', 'add', '-q', wt, '-b', 'w1')
        for cmd in ('echo gate; git push', f'cd {wt} && git push', '(git push)', '/usr/bin/git push',
                    'git push 2>&1 | tail -1'):
            self.assertIsNotNone(self.fx.bash(cmd), cmd)

    def test_delete_pushes_are_allowed(self):
        self.fx.commit('app.py', 'print(2)\n')
        self.assertIsNone(self.fx.bash('git push origin :old'))
        self.assertIsNone(self.fx.bash('git push --delete origin old'))

    def test_undeterminable_diff_is_denied(self):
        git(self.fx.work, 'remote', 'set-head', 'origin', '--delete')
        git(self.fx.work, 'update-ref', '-d', 'refs/remotes/origin/main')
        self.fx.commit('docs/guide.md', 'x\n')
        self.assertIn('no passing gate', self.fx.bash('git push -u origin HEAD'))

    def test_unknown_arguments_are_denied(self):
        self.assertIn("couldn't read this command", self.fx.bash('git push "oops'))
        self.assertIn("couldn't read this command", self.fx.bash('GIT_DIR=x git push'))

    def test_gate_and_push_in_one_call_is_denied(self):
        self.fx.commit('app.py', 'print(2)\n')
        self.fx.gate()
        self.assertIn("couldn't read this command", self.fx.bash(f"{GATE_LOCK} w1 -- 'echo gated' && git push"))

    def test_a_gate_that_writes_coverage_still_matches_head(self):
        gate = 'mkdir -p coverage && echo x > coverage/out.txt'
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'), CONFIG.replace('echo gated', gate))
        git(self.fx.work, 'add', '-A')
        git(self.fx.work, 'commit', '-q', '-m', 'gate')
        p = self.fx.gate(gate)
        self.assertIn('not part of the receipt: coverage/out.txt', p.stderr)
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))

    def test_push_from_a_worktree_path_with_spaces(self):
        wt = os.path.join(self.fx.tmp, 'my wt')
        git(self.fx.work, 'worktree', 'add', '-q', wt, '-b', 'w2')
        self.fx.commit('app.py', 'print(9)\n', cwd=wt)
        self.assertIn('no passing gate', self.fx.bash(f'cd "{wt}" && git push -u origin HEAD'))
        self.fx.gate(cwd=wt)
        self.assertIsNone(self.fx.bash(f'cd "{wt}" && git push -u origin HEAD'))

    def test_origin_head_unset_falls_back_to_main(self):
        git(self.fx.work, 'remote', 'set-head', 'origin', '--delete')
        self.fx.commit('docs/guide.md', 'x\n')
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))


class ReceiptExactnessTest(Case):
    """Revision 6: the receipt must match the pushed tree exactly, apart from exempt paths."""

    def setUp(self):
        super().setUp()
        self.fx.start_run()
        git(self.fx.work, 'switch', '-q', '-c', 'feat')

    def test_git_rm_after_the_gate_is_denied(self):
        write(os.path.join(self.fx.work, 'app.py'), 'print(2)\n')
        self.fx.commit('lib.py', 'x = 1\n')
        self.fx.gate()
        git(self.fx.work, 'rm', '-q', 'lib.py')
        git(self.fx.work, 'commit', '-q', '-m', 'rm')
        why = self.fx.bash('git push -u origin HEAD')
        self.assertIn('lib.py', why)

    def test_commit_am_leaving_out_a_new_file_is_denied(self):
        write(os.path.join(self.fx.work, 'app.py'), 'print(2)\n')
        write(os.path.join(self.fx.work, 'new.py'), 'y = 2\n')
        p = self.fx.gate()
        self.assertIn('part of the gated tree', p.stderr)
        self.assertIn('new.py', p.stderr)
        git(self.fx.work, 'commit', '-q', '-am', 'only tracked')
        why = self.fx.bash('git push -u origin HEAD')
        self.assertIn('the gate ran on a different tree', why)
        self.assertIn('new.py', why)

    def test_a_modified_worktree_env_file_passes(self):
        self.fx.commit('app.py', 'print(2)\n')
        write(os.path.join(self.fx.work, '.env.local'), 'SECRET=2\n')
        self.fx.gate()
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))

    def test_a_second_gate_run_does_not_sweep_the_first_runs_artifact(self):
        gate = 'mkdir -p coverage && touch coverage/a.txt'
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'), CONFIG.replace('echo gated', gate))
        self.fx.commit('app.py', 'print(2)\n')
        self.fx.gate(gate)
        p = self.fx.gate(gate)
        self.assertNotIn('part of the gated tree', p.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.fx.state, 'gate', head_tree(self.fx.work))))


class ScopingTest(Case):
    def test_no_marker_silences_the_run_only_pieces(self):
        self.fx.commit('app.py', 'print(2)\n')
        self.assertIsNone(self.fx.bash('git push'))
        self.fx.start_run()
        self.assertIsNotNone(self.fx.bash('git push'))
        self.assertIsNone(self.fx.bash('git push', sid='S2'))

    def test_no_config_is_silent_even_with_foreman_config_set(self):
        fx = Fx(config=None)
        try:
            write(os.path.join(fx.tmp, 'cfg.md'), CONFIG)
            fx.env['FOREMAN_CONFIG'] = os.path.join(fx.tmp, 'cfg.md')
            state.write_json(os.path.join(fx.state, 'runs', 'S1.json'), {'started': time.time(), 'touched': time.time()})
            fx.commit('app.py', 'print(2)\n')
            self.assertIsNone(fx.bash('git push'))
        finally:
            fx.close()

    def test_hooks_disabled_drops_a_piece(self):
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'),
              CONFIG.replace('---\n', '---\nhooks_disabled: [gate_before_push]\n', 1))
        self.fx.start_run()
        self.fx.commit('app.py', 'print(2)\n')
        self.assertIsNone(self.fx.bash('git push'))

    def test_a_matching_piece_touches_the_marker(self):
        self.fx.start_run()
        before = self.fx.marker()['touched']
        time.sleep(0.01)
        self.fx.bash('git push origin :old')
        self.assertGreater(self.fx.marker()['touched'], before)


```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 tests/test_hooks.py ReceiptMintTest GateBeforePushTest ReceiptExactnessTest ScopingTest`
Expected: FAIL. No receipt is minted (`self.receipts()` is `[]`), and pushes without a receipt are allowed (`bash()` returns None).

- [ ] **Step 3: Add receipts to `scripts/state.py`**

In the docstring, after the `state.py <path>` line, add:

```text
  state.py mint <toplevel> <owner> <command> <untracked-before-file>
                       mint a gate receipt for the working tree (gate-lock.sh calls this after a passing gate)
```

Add `import shutil` and `import time` to the imports, and `RECEIPT_TTL = 14 * 86400` after `DEFAULT_HOME`. Replace `main` with this section and the new `main`:

```python
# ---- gate receipts (piece 1) ----------------------------------------------------------------------

def last_path(sd, top):
    """The pointer to the last receipt minted in checkout `top`: {tree, artifacts, time, toplevel}."""
    return os.path.join(sd, 'gate', 'last', hashlib.sha1(os.path.realpath(top).encode()).hexdigest())


def untracked(top):
    """Untracked, non-ignored paths, relative to the top level."""
    p = subprocess.run(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=top, capture_output=True)
    return {f for f in p.stdout.decode('utf-8', 'surrogateescape').split('\0') if f}


def write_tree(top, exclude):
    """The tree of the working copy, from a copy of the real index (keeps force-added files and existing blobs,
    so clean/eol/LFS filters don't re-run on unchanged files), leaving out `exclude`."""
    real = os.path.join(top, git(top, 'rev-parse', '--git-path', 'index').strip())
    fd, tmp = tempfile.mkstemp(suffix='.index')
    os.close(fd)
    try:
        if os.path.exists(real):
            shutil.copyfile(real, tmp)
        else:
            os.remove(tmp)
        env = dict(os.environ, GIT_INDEX_FILE=tmp)
        subprocess.run(['git', 'add', '-A', '--', '.'], cwd=top, env=env, check=True, capture_output=True)
        if exclude:
            subprocess.run(['git', 'rm', '--cached', '-q', '--ignore-unmatch', '--pathspec-from-file=-',
                            '--pathspec-file-nul'], cwd=top, env=dict(env, GIT_LITERAL_PATHSPECS='1'),
                           input='\0'.join(sorted(exclude)).encode('utf-8', 'surrogateescape'), check=True,
                           capture_output=True)
        return subprocess.run(['git', 'write-tree'], cwd=top, env=env, check=True, capture_output=True,
                              text=True).stdout.strip()
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def prune_receipts(sd):
    cutoff = time.time() - RECEIPT_TTL
    for d in (os.path.join(sd, 'gate'), os.path.join(sd, 'gate', 'last')):
        for name in (os.listdir(d) if os.path.isdir(d) else []):
            p = os.path.join(d, name)
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)


def mint(top, owner, command, before_file):
    """Record that `command` passed on the working tree of checkout `top`. Paths the gate created (untracked
    now, not before) are artifacts: left out of the receipt, and left out again at later mints here. The
    receipt goes to every state dir, so hooks find it whichever FOREMAN_HOME they see (probe P4)."""
    top = os.path.realpath(top)
    dirs = state_dirs(main_checkout(top) or top)
    with open(before_file, 'rb') as f:
        before = {p for p in f.read().decode('utf-8', 'surrogateescape').split('\0') if p}
    now = untracked(top)
    last = next((x for x in (read_json(last_path(d, top)) for d in dirs) if isinstance(x, dict)), {})
    old = set(last.get('artifacts') or [])
    new = now - before
    artifacts = new | (old & now)
    swept = sorted((now & before) - old)
    if new:
        print('gate-lock: the gate created untracked files; they are not part of the receipt: '
              + ', '.join(sorted(new)[:10]), file=sys.stderr)
    if swept:
        print('gate-lock: these untracked files are part of the gated tree; commit them, or delete them and '
              're-run the gate: ' + ', '.join(swept[:10]), file=sys.stderr)
    tree = write_tree(top, artifacts)
    for sd in dirs:
        write_json(os.path.join(sd, 'gate', tree), {'command': command, 'owner': owner, 'time': time.time(),
                                                    'toplevel': top})
        write_json(last_path(sd, top), {'tree': tree, 'artifacts': sorted(artifacts), 'time': time.time(),
                                        'toplevel': top})
        prune_receipts(sd)
    return tree


def main(argv):
    if len(argv) == 2 and not argv[1].startswith('-'):
        m = main_checkout(argv[1])
        if not m:
            raise SystemExit(1)
        print(state_dir(m))
        return
    if len(argv) == 6 and argv[1] == 'mint':
        print(mint(*argv[2:]))
        return
    raise SystemExit(__doc__)


if __name__ == '__main__':
    main(sys.argv)
```

- [ ] **Step 4: Mint from `scripts/gate-lock.sh`**

Append to the header comment (after `# fresh slot by hand. …`):

```bash
#
# The command runs from the checkout's top level. When it passes, is exactly one argument equal to the config's
# `gate`, and no --name was given, a gate receipt is minted for the working tree (state.py mint): the hooks
# then allow a push of that tree. Untracked files the command creates are left out of the receipt.
```

Replace the heartbeat line (line 62) with the version below. Its output goes to `/dev/null`: the heartbeat's `sleep` outlives the script by up to one beat, and a caller capturing gate-lock's output (the tests, and possibly the Bash tool) would otherwise wait for it, 60 s by default.

```bash
# The heartbeat's own output goes to /dev/null: its `sleep` outlives the script by up to a beat, and a caller
# capturing gate-lock's output would otherwise wait for it.
( while sleep "${FOREMAN_LOCK_BEAT:-60}"; do kill -0 $$ 2>/dev/null && touch "$root/$slot/owner" 2>/dev/null || exit; done ) >/dev/null 2>&1 &
```

Replace the last line (`if [ $# -eq 1 ]; then bash -c "$1"; else "$@"; fi`) with:

```bash
top=$(/usr/bin/git rev-parse --show-toplevel 2>/dev/null) || top=""
before=""
if [ -n "$top" ]; then
  cd "$top" || exit 2
  before=$(mktemp "${TMPDIR:-/tmp}/foreman-untracked.XXXXXX")
  /usr/bin/git ls-files --others --exclude-standard -z >"$before" 2>/dev/null
fi
if [ $# -eq 1 ]; then bash -c "$1"; else "$@"; fi
rc=$?
if [ -n "$before" ]; then
  if [ "$rc" = 0 ] && [ -z "$name" ] && [ $# -eq 1 ] && [ "$1" = "$(python3 "$here/cfg.py" "$main" gate)" ]; then
    python3 "$here/state.py" mint "$top" "$owner" "$1" "$before" >/dev/null ||
      echo "gate-lock: could not mint a gate receipt" >&2
  fi
  rm -f "$before"
fi
exit "$rc"
```

These keep the existing `tests/run.sh` gate-lock checks green with zero edits:
- the lock dir is still `basename(main)`;
- outside a git repo there is no `cd` and no mint;
- a mint failure never changes the exit code (`exit 7` still exits 7);
- `$main` is the main checkout computed above, so `cfg.py` reads the gate the hooks read (it still honours `FOREMAN_CONFIG` for tests).

- [ ] **Step 5: Add piece 1 to `scripts/hooks.py`**

Insert above `# ---- pretool / posttool ----`:

```python
# ---- piece 1: gate before push --------------------------------------------------------------------

UNREADABLE = ("foreman couldn't read this command — run the gate, then push with a plain `git push` in its own "
              "call")


def glob_re(pattern):
    """A gitignore-style pattern as a regex: `**/` is zero or more directories, `*` stays inside one, a
    pattern without a slash matches at any depth, and a match on a directory covers what's inside it."""
    p = pattern.strip()
    anchored = '/' in p.rstrip('/')
    p = p.lstrip('/')
    if p.endswith('/'):
        p += '**'
    out, i = '', 0
    while i < len(p):
        if p.startswith('**/', i):
            out, i = out + '(?:.*/)?', i + 3
        elif p.startswith('**', i):
            out, i = out + '.*', i + 2
        elif p[i] == '*':
            out, i = out + '[^/]*', i + 1
        elif p[i] == '?':
            out, i = out + '[^/]', i + 1
        else:
            out, i = out + re.escape(p[i]), i + 1
    return re.compile(('' if anchored else '(?:.*/)?') + out + r'(?:/.*)?\Z')


def glob_match(path, pattern):
    return bool(glob_re(pattern).match(path))


def exempt(repo, path, receipt=False):
    """A path a push may change without a gate: gate_exempt, the backlog, the rules files and, when matching a
    receipt, worktree_env_files (workers copy them in and never commit them)."""
    patterns = repo.list('gate_exempt') + [repo.backlog] + repo.rules
    if receipt:
        patterns += repo.list('worktree_env_files')
    return any(glob_match(path, p) for p in patterns)


def default_branch(top):
    head = git_out(top, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD')
    return head.strip().split('/', 1)[1] if head and '/' in head else 'main'


def receipt_reason(repo, since):
    r = (f"no passing gate for this tree — run `{HERE}/gate-lock.sh <owner> -- '{repo.get('gate') or '<gate>'}'` "
         "from the checkout's top level, commit every file the gate changed, then push")
    if since:
        r += ("; the gate ran on a different tree: commit these, or restore them (`git checkout -- <path>` for a "
              "tracked file, delete for an untracked one) and re-run the gate: " + ', '.join(since[:10]))
    return r


def receipt_check(repo, cwd):
    """None when the tree at `cwd`'s HEAD may be pushed, else the deny reason."""
    top = state.toplevel(cwd)
    tree = git_out(top, 'rev-parse', 'HEAD^{tree}') if top else None
    if not tree:
        return receipt_reason(repo, None)
    tree = tree.strip()
    if any(os.path.exists(os.path.join(d, 'gate', tree)) for d in repo.dirs):
        return None
    since = None
    for d in repo.dirs:
        last = state.read_json(state.last_path(d, top))
        names = git_out(top, 'diff', '--name-only', '--no-renames', last['tree'], tree) \
            if isinstance(last, dict) and last.get('tree') else None
        if names is not None:
            since = [p for p in names.splitlines() if p and not exempt(repo, p, receipt=True)]
            if not since:
                return None
            break
    base = git_out(top, 'merge-base', f'origin/{default_branch(top)}', 'HEAD')
    names = git_out(top, 'diff', '--name-only', '--no-renames', base.strip(), 'HEAD') if base else None
    if names is not None and all(exempt(repo, p) for p in names.splitlines() if p):
        return None
    return receipt_reason(repo, since)


def is_push_trigger(c):
    return (c.prog, c.sub) in (('git', 'push'), ('gh', 'pr create'), ('gt', 'submit'))


def gate_before_push(h, sc):
    gate_in_call = any(s.words and any(os.path.basename(w) == 'gate-lock.sh' for w in s.words) for s in sc.segs)
    for c in sc.cmds:
        if not is_push_trigger(c):
            continue
        repo = cmd_repo(c, h)
        if not h.active(repo, 'gate_before_push'):
            continue
        h.touch.add(repo)
        if c.args is None or c.cwd is None or gate_in_call:
            h.deny(UNREADABLE)
        elif c.sub == 'push' and shellscan.push_info(c.args, default_branch(c.cwd))[0]:
            continue
        else:
            why = receipt_check(repo, c.cwd)
            if why:
                h.deny(why)


BASH_PIECES[1] = gate_before_push


```

- [ ] **Step 6: Run the tests**

Run: `python3 tests/test_hooks.py && tests/run.sh`
Expected: `test_hooks.py` OK (42 tests); `run.sh` ends `failed 0`, its gate-lock section unchanged.

- [ ] **Step 7: Commit**

```bash
git add scripts/state.py scripts/gate-lock.sh scripts/hooks.py tests/test_hooks.py
git commit -m "Gate receipts: gate-lock.sh mints from the top level; piece 1 denies a push without one"
```

---

### Task 6: Pieces 2 and 3: worker guard and merge discipline

**Files:**
- Modify: `scripts/hooks.py` (insert above `# ---- pretool / posttool ----`)
- Modify: `tests/test_hooks.py` (new classes above `if __name__ == '__main__':`)

**Interfaces:**
- Consumes: `Repo.rel`, `Repo.backlog`, `Repo.rules`, `CONFIG_NAMES`, `Hook.active`, `Hook.deny`, `Hook.touch`, `cmd_repo`, `git_out`, `BASH_PIECES`, `EDIT_PIECES` (Task 4); `glob_match`, `default_branch` (Task 5); `shellscan.push_info`, `shellscan.is_rebase`, `shellscan.gh_api_merge`, `Cmd.denied`, `Cmd.ghrepo` (Task 3).
- Produces:
  - `opt_values(args, names) -> list[str]`; `has_flag(args, names) -> bool`; `positionals(args, value_opts) -> list[str]`; `GH_MERGE_VALUE_OPTS`
  - `pr_view(cmd) -> dict | None`: `gh pr view <n> --json headRefName,commits` in the command's directory, 4 s timeout, cached per call; shared with piece 4 (Task 7). The field names `headRefName`, `commits[].oid`, `commits[].messageHeadline` and `commits[].messageBody` were checked against `gh pr view --json commits` on 2026-10-02.
  - `worktrees(cwd) -> list[(path, branch ref | None)]`
  - registers `BASH_PIECES[0] = worker_guard`, `EDIT_PIECES[0] = worker_guard_edit`, `BASH_PIECES[3] = merge_discipline`. Piece 2 runs first and sets `cmd.denied` on a merge it denies, so piece 3 (and piece 4, Task 7) make no network call for that command.

- [ ] **Step 1: Write the failing tests**

Add above `if __name__ == '__main__':`. They cover each denial, a normal push and merge allowed, `-d` judged by which worktree holds the branch, `gt merge`, the `agent_id` scoping row, and Review Focus item 3.

```python
class WorkerGuardTest(Case):
    def setUp(self):
        super().setUp()
        self.fx.start_run()

    def test_subagent_denials(self):
        for cmd, needle in (('gh pr merge 1 --squash --match-head-commit abc1234', 'workers never merge'),
                            ('gt merge', 'workers never merge'),
                            ('gh api -X PUT repos/o/r/pulls/1/merge', 'workers never merge'),
                            ("gh api graphql -f query='mutation { mergePullRequest(input:{}) { clientMutationId } }'",
                             'workers never merge'),
                            ('git push --force', 'no force-push'),
                            ('git push -uf origin HEAD', 'no force-push'),
                            ('git push --force-with-lease', 'no force-push'),
                            ('git push --mirror', 'no force-push'),
                            ('git push origin +feat', 'no force-push'),
                            ('git push origin HEAD:main', 'no push to main'),
                            ('git push origin main', 'no push to main'),
                            ('git rebase origin/main', 'no rebase'),
                            ('git pull --rebase', 'no rebase')):
            self.assertIn(needle, self.fx.bash(cmd, agent='a1') or '', cmd)

    def test_gt_modify_only_once_the_branch_has_an_upstream(self):
        git(self.fx.work, 'switch', '-q', '-c', 'feat')
        self.assertIsNone(self.fx.bash('gt modify -a', agent='a1'))
        git(self.fx.work, 'push', '-q', '-u', 'origin', 'feat')
        self.assertIn('no gt modify', self.fx.bash('gt modify -a', agent='a1'))

    def test_the_main_session_and_normal_calls_are_not_guarded(self):
        self.assertIsNone(self.fx.bash('git rebase origin/main'))
        self.assertIsNone(self.fx.bash('git push --force origin :old'))
        git(self.fx.work, 'switch', '-q', '-c', 'feat')
        self.fx.commit('app.py', 'print(2)\n')
        self.fx.gate()
        self.assertIsNone(self.fx.bash('git push -u origin HEAD', agent='a1'))
        self.assertIsNone(self.fx.bash('gh api repos/o/r/pulls/1/merge', agent='a1'))

    def test_no_marker_no_guard(self):
        self.assertIsNone(self.fx.bash('git rebase origin/main', agent='a1', sid='S2'))

    def test_bookkeeping_edits(self):
        for rel in ('.claude/improvement-backlog.md', '.claude/rules/x.md', '.claude/foreman.config.md'):
            self.assertIn("workers don't edit", self.fx.edit(os.path.join(self.fx.work, rel), agent='a1') or '', rel)
        self.assertIn("workers don't edit", self.fx.edit('.claude/rules/x.md', tool='Write', agent='a1') or '')
        self.assertIsNone(self.fx.edit(os.path.join(self.fx.work, 'app.py'), agent='a1'))
        self.assertIsNone(self.fx.edit(os.path.join(self.fx.work, '.claude/rules/x.md')))

    def test_a_worktrees_own_bookkeeping_files_are_guarded(self):
        wt = os.path.join(self.fx.tmp, 'wt')
        git(self.fx.work, 'worktree', 'add', '-q', wt, '-b', 'w1')
        self.assertIn("workers don't edit", self.fx.edit(os.path.join(wt, '.claude/improvement-backlog.md'),
                                                         agent='a1', cwd=wt))

    def test_new_rules_file_in_missing_dir_is_denied(self):
        wt = os.path.join(self.fx.tmp, 'wt')
        git(self.fx.work, 'worktree', 'add', '-q', wt, '-b', 'w1')
        self.assertFalse(os.path.exists(os.path.join(wt, '.claude/rules')))
        self.assertIn("workers don't edit", self.fx.edit(os.path.join(wt, '.claude/rules/new.md'), tool='Write',
                                                         agent='a1', cwd=wt))


class MergeDisciplineTest(Case):
    def setUp(self):
        super().setUp()
        self.fx.start_run()

    def pr(self, head, commits=()):
        write(self.fx.env['FAKE_GH_PR'], json.dumps({'headRefName': head, 'commits': list(commits)}))

    def test_merge_needs_match_head_commit(self):
        self.assertIn('add `--match-head-commit <sha>`', self.fx.bash('gh pr merge 5 --squash'))
        self.assertIsNone(self.fx.bash('gh pr merge 5 --squash --match-head-commit abc1234'))
        self.assertIsNone(self.fx.bash('gh pr merge 5 --squash --match-head-commit=abc1234'))

    def test_delete_branch_when_only_the_own_worktree_holds_it(self):
        git(self.fx.work, 'switch', '-q', '-c', 'feat')
        self.pr('feat')
        self.assertIsNone(self.fx.bash('gh pr merge 5 --squash --match-head-commit abc1234 --delete-branch'))

    def test_delete_branch_when_another_worktree_holds_it(self):
        wt = os.path.join(self.fx.tmp, 'wt')
        git(self.fx.work, 'worktree', 'add', '-q', wt, '-b', 'feat2')
        self.pr('feat2')
        why = self.fx.bash('gh pr merge 5 --squash --match-head-commit abc1234 -d')
        self.assertIn(f'remove the worktree at {wt} first', why)

    def test_gt_merge_is_denied_during_a_run(self):
        self.assertIn('no gt merge during a run', self.fx.bash('gt merge'))
        self.assertIsNone(self.fx.bash('gt merge', sid='S2'))

    def test_a_subagent_merge_is_decided_locally(self):
        why = self.fx.bash('gh pr merge 5 --squash -d', agent='a1')
        self.assertIn('workers never merge', why)
        self.assertNotIn('match-head-commit', why)
        self.assertFalse(os.path.exists(self.fx.env['FAKE_GH_LOG']))


class OptionHelpersTest(unittest.TestCase):
    def test_opt_values_and_has_flag(self):
        import hooks
        self.assertEqual(hooks.opt_values(['-am', 'msg'], ('-m', '--message')), ['msg'])
        self.assertEqual(hooks.opt_values(['-mfoo', '--message=bar', '-m', 'baz'], ('-m', '--message')),
                         ['foo', 'bar', 'baz'])
        self.assertEqual(hooks.opt_values(['--', '-m', 'x'], ('-m',)), [])
        self.assertTrue(hooks.has_flag(['-sd'], ('--delete-branch', '-d')))
        self.assertFalse(hooks.has_flag(['--squash'], ('--delete-branch', '-d')))
        self.assertEqual(hooks.positionals(['--squash', '-t', 'x', '12'], hooks.GH_MERGE_VALUE_OPTS), ['12'])


```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 tests/test_hooks.py WorkerGuardTest MergeDisciplineTest OptionHelpersTest`
Expected: FAIL. The subagent denials return None, and `OptionHelpersTest` errors with `AttributeError: module 'hooks' has no attribute 'opt_values'`.

- [ ] **Step 3: Add the helpers and pieces 2 and 3 to `scripts/hooks.py`**

Insert above `# ---- pretool / posttool ----`:

```python
# ---- option helpers and the shared `gh pr view` (pieces 3 and 4) ----------------------------------

def opt_values(args, names):
    """Every value of the options in `names`: `--x v`, `--x=v`, `-x v`, `-xv`, and a short option ending a
    flag cluster (`-am msg`)."""
    out, i = [], 0
    shorts = [n[1] for n in names if len(n) == 2 and n[0] == '-' and n[1] != '-']
    while i < len(args):
        a = args[i]
        if a == '--':
            break
        nxt = args[i + 1] if i + 1 < len(args) else None
        if a in names:
            if nxt is not None:
                out.append(nxt)
            i += 2
            continue
        long = next((n for n in names if n.startswith('--') and a.startswith(n + '=')), None)
        if long:
            out.append(a[len(long) + 1:])
        elif re.match(r'^-[A-Za-z]', a) and not a.startswith('--'):
            k = next((j for j, ch in enumerate(a[1:], 1) if ch in shorts), None)
            if k is not None:
                if a[k + 1:]:
                    out.append(a[k + 1:])
                elif nxt is not None:
                    out.append(nxt)
                    i += 1
        i += 1
    return out


def has_flag(args, names):
    """True when a flag in `names` is present, alone or inside a short-flag cluster (`-sd`)."""
    shorts = {n[1] for n in names if len(n) == 2 and n[0] == '-' and n[1] != '-'}
    for a in args:
        if a == '--':
            break
        if a in names or (re.match(r'^-[A-Za-z]+$', a) and shorts & set(a[1:])):
            return True
    return False


GH_MERGE_VALUE_OPTS = ('-t', '--subject', '-b', '--body', '-F', '--body-file', '--match-head-commit', '-A',
                       '--author-email')


def positionals(args, value_opts):
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a == '--':
            return out + args[i + 1:]
        if a in value_opts:
            i += 2
            continue
        if not a.startswith('-'):
            out.append(a)
        i += 1
    return out


_pr_cache = {}


def pr_view(c):
    """`gh pr view <n> --json headRefName,commits` for a `gh pr merge` candidate, cached per call; None when gh
    fails (the checks that need it then don't apply)."""
    n = positionals(c.args, GH_MERGE_VALUE_OPTS)[:1]
    key = (c.cwd, c.ghrepo, tuple(n))
    if key not in _pr_cache:
        argv = ['gh', 'pr', 'view', *n, '--json', 'headRefName,commits'] + (['-R', c.ghrepo] if c.ghrepo else [])
        try:
            p = subprocess.run(argv, cwd=c.cwd, capture_output=True, text=True, timeout=4)
            _pr_cache[key] = json.loads(p.stdout) if p.returncode == 0 else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            _pr_cache[key] = None
        if _pr_cache[key] is None:
            print('foreman: gh pr view failed; the merge checks that need it are skipped', file=sys.stderr)
    return _pr_cache[key]


def worktrees(cwd):
    """[(path, branch ref or None)] from `git worktree list --porcelain`."""
    out, path = [], None
    for line in (git_out(cwd, 'worktree', 'list', '--porcelain') or '').splitlines():
        if line.startswith('worktree '):
            path = line[len('worktree '):]
            out.append([path, None])
        elif line.startswith('branch ') and out:
            out[-1][1] = line[len('branch '):]
    return [tuple(w) for w in out]


# ---- piece 2: worker guard ------------------------------------------------------------------------

NO_MERGE = 'workers never merge — the worker contract: "Never merge."; report the PR and the orchestrator merges it'
NO_FORCE = ('no force-push — the worker contract: "never amend or force-push once the PR is open"; push a '
            'follow-up commit')
NO_REBASE = ('no rebase — the worker contract: "merge `origin/main` into it, do not rebase"')
NO_BOOKKEEPING = ('workers don\'t edit the backlog, the rules files or the foreman config — the worker contract: '
                  '"Step 10: do not edit the backlog, the rules files or generated files"; report RULE NOTES and '
                  'NEW ROWS instead')


def worker_violation(c):
    if (c.prog, c.sub) in (('gh', 'pr merge'), ('gt', 'merge')):
        return NO_MERGE
    if (c.prog, c.sub) == ('gh', 'api') and shellscan.gh_api_merge(c.args, subagent=True):
        return NO_MERGE
    if shellscan.is_rebase(c):
        return NO_REBASE
    if (c.prog, c.sub) == ('git', 'push') and c.args is not None:
        default = default_branch(c.cwd) if c.cwd else 'main'
        _, force, to_default = shellscan.push_info(c.args, default)
        if force:
            return NO_FORCE
        if to_default:
            return f'no push to {default} — push your own branch and open a PR'
    if (c.prog, c.sub) == ('gt', 'modify') and c.cwd and git_out(c.cwd, 'rev-parse', '--abbrev-ref', '@{u}'):
        return ('no gt modify once the branch has an upstream — the worker contract: "never amend or force-push '
                'once the PR is open"; commit a follow-up instead')
    return None


def worker_guard(h, sc):
    if not h.agent:
        return
    for c in sc.cmds:
        repo = cmd_repo(c, h)
        if not h.active(repo, 'worker_guard'):
            continue
        why = worker_violation(c)
        if why:
            h.touch.add(repo)
            h.deny(why)
            c.denied = c.denied or why == NO_MERGE


def bookkeeping_target(repo, path):
    rel = repo.rel(path)
    return rel == os.path.normpath(repo.backlog) or rel in CONFIG_NAMES or any(glob_match(rel, g) for g in repo.rules)


def worker_guard_edit(h, repo, path):
    if h.agent and h.active(repo, 'worker_guard') and bookkeeping_target(repo, path):
        h.touch.add(repo)
        h.deny(NO_BOOKKEEPING)


BASH_PIECES[0] = worker_guard
EDIT_PIECES[0] = worker_guard_edit


# ---- piece 3: merge discipline --------------------------------------------------------------------

def merge_discipline(h, sc):
    for c in sc.cmds:
        if (c.prog, c.sub) not in (('gh', 'pr merge'), ('gt', 'merge')):
            continue
        repo = cmd_repo(c, h)
        if not h.active(repo, 'merge_discipline'):
            continue
        h.touch.add(repo)
        if c.prog == 'gt':
            h.deny('no gt merge during a run — merge with `gh pr merge <n> --match-head-commit <sha>`')
        elif c.denied:
            continue
        elif c.args is None or c.cwd is None:
            h.deny("merge the SHA you verified — foreman couldn't read this merge; run `gh pr merge <n> "
                   "--match-head-commit <sha>` in its own call")
        else:
            if not opt_values(c.args, ('--match-head-commit',)):
                h.deny('merge the SHA you verified — add `--match-head-commit <sha>`')
            if has_flag(c.args, ('--delete-branch', '-d')):
                holder = other_worktree(c)
                if holder:
                    h.deny(f'remove the worktree at {holder} first, then delete the branch — it still holds the '
                           "PR's head branch")


def other_worktree(c):
    """A worktree other than the command's own that has the PR's head branch checked out, or None."""
    pr = pr_view(c)
    own = os.path.realpath(state.toplevel(c.cwd) or c.cwd)
    head = (pr or {}).get('headRefName')
    for path, branch in worktrees(c.cwd) if head else []:
        if branch == f'refs/heads/{head}' and os.path.realpath(path) != own:
            return path
    return None


BASH_PIECES[3] = merge_discipline


```

- [ ] **Step 4: Run the tests**

Run: `python3 tests/test_hooks.py`
Expected: OK (55 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/hooks.py tests/test_hooks.py
git commit -m "Hooks pieces 2 and 3: worker guard, merge discipline"
```

---

### Task 7: Pieces 4 and 5: trailer guard and backlog row check

**Files:**
- Modify: `scripts/hooks.py` (insert above `# ---- pretool / posttool ----`)
- Modify: `tests/test_hooks.py` (new classes above `if __name__ == '__main__':`)

**Interfaces:**
- Consumes: `Hook.active(…, run_only=False)`, `repo_at`, `Repo.rel`, `Repo.backlog`, `Repo.list`, `cmd_repo`, `BASH_PIECES`, `POST_EDIT`, and `backlog` (loaded by `_load`) (Task 4); `opt_values`, `has_flag`, `pr_view`, `Cmd.denied` (Task 6); `Scan.docs`, `Doc.target`, `Cmd.heredocs` (Task 3); `backlog.parse`, `backlog.row_pattern`, `backlog.line_problems`, `Backlog.duplicates` (existing).
- Produces:
  - `MESSAGE_OPTS`; `message_texts(cmd, sc, h) -> list[str] | None`; `find_trailer(rules, texts) -> entry | None`; `merge_method(args) -> 'squash'|'rebase'|'merge'`
  - `backlog_problems(text, prefix) -> list[str]`
  - registers `BASH_PIECES[4] = trailer_guard` and `POST_EDIT[5] = backlog_check`
- Notes:
  - Both pieces apply in any session in a foreman repo (`run_only=False`).
  - A `gh pr merge` with no method flag is judged like `--merge`: gh can't run one non-interactively, so the strict reading is safe.

- [ ] **Step 1: Write the failing tests**

Add above `if __name__ == '__main__':`. They cover the spec's piece 4 list (a trailer in `-m`, in a `-F` file in the scratchpad, in a heredoc; the `🤖 Generated with` footer; a mid-sentence mention allowed; squash, merge and rebase whose commits carry a trailer), its piece 5 list, the regex entry form, and Review Focus item 4.

```python
class TrailerGuardTest(Case):
    def test_trailer_in_a_message_option(self):
        self.assertIn('starts with "Co-Authored-By"', self.fx.bash('git commit -m "fix: x" -m "Co-Authored-By: a <a@b>"'))
        self.assertIn('Claude-Session', self.fx.bash('git commit -am "x\nClaude-Session: abc"'))
        self.assertIn('Co-Authored-By', self.fx.bash('gt create -m "x\n\nco-authored-by: a"'))

    def test_trailer_in_a_message_file_in_the_scratchpad(self):
        msg = os.path.join(self.fx.tmp, 'scratch', 'msg.txt')
        write(msg, 'fix: thing\n\nCo-Authored-By: a <a@b>\n')
        self.assertIn('Co-Authored-By', self.fx.bash(f'git commit -F {msg}'))
        write(msg, 'fix: thing\n')
        self.assertIsNone(self.fx.bash(f'git commit -F {msg}'))

    def test_trailer_in_a_heredoc(self):
        self.assertIn('Co-Authored-By', self.fx.bash("git commit -F - <<'EOF'\nfix: x\n\nCo-Authored-By: a\nEOF"))
        cmd = ("git commit -m \"$(cat <<'EOF'\nfix: x\n\n- \"two words\"\nCo-Authored-By: a <a@b>\nEOF\n)\"")
        self.assertIn('Co-Authored-By', self.fx.bash(cmd))
        later = os.path.join(self.fx.tmp, 'later.txt')
        self.assertIn('Co-Authored-By', self.fx.bash(f"cat > {later} <<'EOF'\nx\nCo-Authored-By: a\nEOF\ngit commit -F {later}"))

    def test_generated_with_footer_in_a_pr_body(self):
        cmd = ("gh pr create --title t --body \"$(cat <<'EOF'\n## Summary\n- \"two words\"\n\n"
               "🤖 Generated with [Claude Code](https://claude.com/claude-code)\nEOF\n)\"")
        self.assertIn('Generated with', self.fx.bash(cmd))
        self.assertIn('Generated with', self.fx.bash('gh pr comment 5 --body "ok\n🤖 Generated with Claude Code"'))

    def test_mid_sentence_mention_is_allowed(self):
        self.assertIsNone(self.fx.bash('git commit -m "fix: drop the Co-Authored-By line from the template"'))

    def test_regex_entry_searches_within_lines(self):
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'),
              CONFIG.replace('Generated with]', '/Generated with \\[Claude Code\\]/]'))
        self.assertIsNone(self.fx.bash('git commit -m "x\n- Generated with the seed script"'))
        self.assertIn('Generated with', self.fx.bash('git commit -m "x\n🤖 Generated with [Claude Code](https://x)"'))

    def test_applies_without_a_run_and_not_with_an_empty_list(self):
        self.assertIsNotNone(self.fx.bash('git commit -m "x\nCo-Authored-By: a"', sid='S9'))
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'),
              CONFIG.replace('[Co-Authored-By, Claude-Session, Generated with]', '[]'))
        self.assertIsNone(self.fx.bash('git commit -m "x\nCo-Authored-By: a"'))

    def test_unknown_arguments_check_the_raw_text(self):
        self.assertIn('Co-Authored-By', self.fx.bash('git commit -m "x\nCo-Authored-By: a'))

    def test_merges_whose_commits_carry_a_trailer(self):
        commits = [{'oid': 'abcdef1234567', 'messageHeadline': 'fix', 'messageBody': 'Co-Authored-By: a <a@b>'}]
        write(self.fx.env['FAKE_GH_PR'], json.dumps({'headRefName': 'feat', 'commits': commits}))
        base = 'gh pr merge 5 --match-head-commit abc1234 '
        self.assertIn('pass --body', self.fx.bash(base + '--squash'))
        self.assertIsNone(self.fx.bash(base + '--squash --subject "fix: x" --body "clean body"'))
        self.assertIn('merge and rebase keep every commit message', self.fx.bash(base + '--merge'))
        self.assertIn('merge and rebase keep every commit message', self.fx.bash(base + '--rebase'))
        self.assertIn('abcdef123', self.fx.bash(base + '--rebase'))


class BacklogCheckTest(Case):
    def post(self, path, tool='Edit', **kw):
        out, _ = self.fx.hook('posttool', self.fx.payload(tool, {'file_path': path}, event='PostToolUse', **kw))
        return out['hookSpecificOutput']['additionalContext'] if out else None

    def test_clean_backlog_returns_nothing(self):
        self.assertIsNone(self.post(os.path.join(self.fx.work, '.claude/improvement-backlog.md')))
        self.assertIsNone(self.post(os.path.join(self.fx.work, 'app.py')))

    def test_a_bookkeeping_worktree_edit_is_checked(self):
        wt = os.path.join(self.fx.tmp, 'bk')
        git(self.fx.work, 'worktree', 'add', '-q', wt, '-b', 'bk')
        bad = BACKLOG + '| IB-3 | Three | a | b | low | ready | C | s |\n| IB-3 | Dup | low | ready | D | s |\n'
        write(os.path.join(wt, '.claude/improvement-backlog.md'), bad)
        ctx = self.post(os.path.join(wt, '.claude/improvement-backlog.md'), tool='Write', cwd=wt)
        self.assertIn('the backlog now has 2 problem rows', ctx)
        self.assertIn('IB-3: 7 " | " separators, want 5', ctx)
        self.assertIn('IB-3: on 2 rows', ctx)

    def test_crlf_backlog(self):
        p = os.path.join(self.fx.work, '.claude/improvement-backlog.md')
        with open(p, 'w', newline='') as f:
            f.write(BACKLOG.replace('\n', '\r\n'))
        self.assertIsNone(self.post(p))
        with open(p, 'w', newline='') as f:
            f.write((BACKLOG + '| IB-3 | Broken | low |\n').replace('\n', '\r\n'))
        self.assertIn('IB-3: 2 " | " separators, want 5', self.post(p))


```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 tests/test_hooks.py TrailerGuardTest BacklogCheckTest`
Expected: FAIL. Every trailer case returns None, and the broken-backlog cases get no context.

- [ ] **Step 3: Add pieces 4 and 5 to `scripts/hooks.py`**

Insert above `# ---- pretool / posttool ----`:

```python
# ---- piece 4: trailer guard -----------------------------------------------------------------------

MESSAGE_OPTS = {
    'git': {'commit': (('-m', '--message'), ('-F', '--file'))},
    'gh': {s: (('-t', '--title', '-b', '--body', '--subject'), ('-F', '--body-file'))
           for s in ('pr create', 'pr edit', 'pr comment', 'pr merge', 'pr review')},
    'gt': {s: (('-m', '--message'), ()) for s in ('create', 'modify', 'submit')},
}


def message_texts(c, sc, h):
    """The message texts a commit/PR/comment command would write, or None when it writes none."""
    opts = MESSAGE_OPTS.get(c.prog, {}).get(c.sub)
    if opts is None:
        return None
    if c.args is None:
        return [c.text]
    texts = opt_values(c.args, opts[0]) + list(c.heredocs)
    for f in opt_values(c.args, opts[1]):
        if f == '-':
            continue
        path = os.path.join(c.cwd or h.cwd, os.path.expanduser(f))
        if os.path.isfile(path):
            with open(path, encoding='utf-8', errors='replace') as fh:
                texts.append(fh.read(1 << 20))
        else:
            doc = next((d for d in sc.docs if d.target == os.path.normpath(path)), None)
            if doc:
                texts.append(doc.body)
            else:
                print(f'foreman: trailer guard skipped {f} (it does not exist yet)', file=sys.stderr)
    return texts


def find_trailer(rules, texts):
    """The first forbid_trailers entry that starts a line of the texts (after non-word characters, any case);
    an entry written /.../ is a regex searched within each line."""
    for entry in rules:
        e = str(entry)
        if len(e) > 2 and e.startswith('/') and e.endswith('/'):
            try:
                rx = re.compile(e[1:-1])
            except re.error:
                print(f'foreman: bad forbid_trailers regex {e}', file=sys.stderr)
                continue
            if any(rx.search(line) for t in texts for line in t.splitlines()):
                return e
        elif any(re.search(r'(?im)^[^\w\n]*' + re.escape(e), t) for t in texts):
            return e
    return None


def merge_method(args):
    if has_flag(args, ('--squash', '-s')):
        return 'squash'
    if has_flag(args, ('--rebase', '-r')):
        return 'rebase'
    return 'merge'


def trailer_guard(h, sc):
    for c in sc.cmds:
        repo = cmd_repo(c, h)
        if not h.active(repo, 'trailer_guard', run_only=False):
            continue
        rules = repo.list('forbid_trailers')
        texts = message_texts(c, sc, h) if rules else None
        if texts is None:
            continue
        hit = find_trailer(rules, texts)
        if hit:
            h.deny(f'no forbidden trailer lines — a message line starts with "{hit}" (forbid_trailers); remove it')
        if (c.prog, c.sub) == ('gh', 'pr merge') and c.args is not None and not c.denied:
            bad = [m for m in (pr_view(c) or {}).get('commits') or []
                   if find_trailer(rules, [f"{m.get('messageHeadline', '')}\n{m.get('messageBody', '')}"])]
            method = merge_method(c.args)
            if bad and not (method == 'squash' and opt_values(c.args, ('-b', '--body', '-F', '--body-file'))):
                fix = ('pass --body (and --subject) without it' if method == 'squash'
                       else 'squash with a --body without it; merge and rebase keep every commit message')
                h.deny(f"the PR's commit {str(bad[0].get('oid', ''))[:9]} carries a forbidden trailer — {fix}")


BASH_PIECES[4] = trailer_guard


# ---- piece 5: backlog row check -------------------------------------------------------------------

def backlog_problems(text, prefix):
    bl = backlog.parse(text, prefix)
    pat = backlog.row_pattern(prefix)
    out = []
    for line in bl.lines:
        raw = (line.raw if isinstance(line, backlog.Row) else str(line)).rstrip('\r')
        if pat.match(raw) or raw.startswith('| NEW-'):
            for p in backlog.line_problems(raw):
                out.append(f'{raw.split(" | ")[0][2:]}: {p[1]} " | " separators, want 5')
    out += [f'{id_}: on {n} rows' for id_, n in bl.duplicates().items()]
    return out


def backlog_check(h, ti):
    path = ti.get('file_path')
    path = os.path.join(h.cwd, path) if path else None
    repo = repo_at(path) if path else None
    if not h.active(repo, 'backlog_check', run_only=False) or repo.rel(path) != os.path.normpath(repo.backlog):
        return
    with open(path, encoding='utf-8', newline='') as f:
        probs = backlog_problems(f.read(), str(repo.get('id_prefix', 'IB')))
    if probs:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext':
              f'foreman: the backlog now has {len(probs)} problem rows: {"; ".join(probs[:10])}. '
              'Fix them before committing.'}}))


POST_EDIT[5] = backlog_check


```

- [ ] **Step 4: Run the tests**

Run: `python3 tests/test_hooks.py`
Expected: OK (67 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/hooks.py tests/test_hooks.py
git commit -m "Hooks pieces 4 and 5: trailer guard, backlog row check"
```

---

### Task 8: Pieces 6 and 7: worker briefing and report check (+ `bk.py --no-fetch`)

**Files:**
- Modify: `scripts/hooks.py` (insert above `# ---- pretool / posttool ----`)
- Modify: `scripts/bk.py` (docstring lines 4 and 22–25; `main` lines 54 and 70)
- Modify: `tests/test_hooks.py` (new classes above `if __name__ == '__main__':`)

**Interfaces:**
- Consumes: `repo_at`, `Repo.cfg`, `Repo.get`, `Repo.list`, `Repo.dirs`, `Repo.disabled`, `Hook.active`, `released`, `agent_entry`, `batch_file`, `HANDLERS`, `HERE`, and `tempfile` (loaded by `_load`) (Task 4); `state.write_json`, `state.read_json`, `state.toplevel` (Task 2).
- Produces:
  - `TAG`, `FENCE`, `SKILL`, `BRIEF_CAP`, `CUT`
  - `worker_tag(text) -> (batch, spec) | None`; `transcript_head(path) -> (first user text | None, epoch | None)`; `iso_time(s)`; `is_worker(repo, h)`
  - `section(text, heading)`, `default_table(text)`, `briefing(repo, batch, spec, sid) -> str`
  - the agent map: `state_dir/batches/<sid>/agents/<agent_id>` = `{batch, spec, time}`, one file per agent, atomic (Task 10 reads it through `agent_entry`)
  - `shape_problem(rep)`, `report_problem(msg, cwd)`, `stopping_allowed(repo, h, batch)`
  - registers `HANDLERS['subagent-start']` and `HANDLERS['subagent-stop']`
  - `bk.py <repo> [--dry-run] [--no-fetch] <reports...>`: `--no-fetch` skips the UI-branch fetch

**Probe fallbacks:**
- **P2:** `subagent_start` reads `prompt`, else the first user message of `agent_transcript_path`. If neither is readable, no briefing and no map: piece 10 fails open, and W3 keeps the contract in the dispatch prompt (Task 14).
- **P6:** if the ids differ, `agent_entry` misses, piece 10 fails open, and `stopping_allowed` falls back to the transcript's first timestamp.
- **P7:** nothing to change.

- [ ] **Step 1: Write the failing tests**

Add above `if __name__ == '__main__':`. They cover the spec's piece 6 and 7 lists (briefing with and without the tag; rendered `$FOREMAN`; the report check's missing block, invalid JSON, wrong shape `"done": ["IB-1"]`, refused block, valid block, `rule_notes` ignored, `stop_hook_active`, and `SPEC READY` after a preface line), the revision 6 `.stopped` case, and Review Focus item 5.

```python
def transcript(fx, name, prompt, born=None):
    """A subagent transcript whose first user message is `prompt` and whose first entry has time `born`."""
    path = os.path.join(fx.tmp, 'transcripts', name + '.jsonl')
    stamp = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(born or time.time()))
    write(path, json.dumps({'type': 'system', 'timestamp': stamp}) + '\n' +
          json.dumps({'type': 'user', 'timestamp': stamp, 'message': {'role': 'user', 'content': prompt}}) + '\n')
    return path


class BriefingTest(Case):
    def start(self, prompt, agent='a1', agent_type='general-purpose', use_prompt=True):
        d = {'hook_event_name': 'SubagentStart', 'session_id': 'S1', 'cwd': self.fx.work, 'agent_id': agent,
             'agent_type': agent_type}
        if use_prompt:
            d['prompt'] = prompt
        else:
            d['agent_transcript_path'] = transcript(self.fx, agent, prompt)
        out, _ = self.fx.hook('subagent-start', d)
        return out['hookSpecificOutput']['additionalContext'] if out else None

    def entry(self, agent='a1'):
        return state.read_json(os.path.join(self.fx.state, 'batches', 'S1', 'agents', agent))

    def test_a_tagged_worker_is_briefed_with_the_rendered_contract(self):
        ctx = self.start('foreman-worker: 20261002-1530-w1a\nDo rows IB-1.')
        self.assertIn('You run steps 2-8 on your batch', ctx)
        self.assertIn(os.path.join(SCRIPTS, 'wt-setup.sh'), ctx)
        self.assertIn(f'{SCRIPTS}/gate-lock.sh 20261002-1530-w1a', ctx)
        self.assertNotIn('$FOREMAN', ctx)
        self.assertNotIn('{{', ctx)
        self.assertIn('.claude/rules/*.md', ctx)
        self.assertIn('<scratch> = your scratchpad dir', ctx)
        self.assertIn('Gate (run it under', ctx)
        self.assertIn('Forbidden trailer lines: Co-Authored-By, Claude-Session, Generated with', ctx)
        self.assertEqual(self.entry()['batch'], '20261002-1530-w1a')
        self.assertFalse(self.entry()['spec'])

    def test_no_tag_no_briefing(self):
        self.assertIsNone(self.start('Do rows IB-1.\nforeman-worker: w1a'))
        self.assertIsNone(self.entry())
        self.assertIsNone(self.start('foreman-worker: w1a', agent_type='Explore'))

    def test_tag_from_the_transcript_and_spec_phase(self):
        self.assertIsNotNone(self.start('foreman-worker: w1b spec-phase\nWrite the spec.', use_prompt=False))
        self.assertTrue(self.entry()['spec'])
        self.start('foreman-worker: w1c (spec-phase)', agent='a2')
        self.assertTrue(self.entry('a2')['spec'])

    def test_a_replacement_of_a_released_batch_is_told_so(self):
        self.fx.start_run()
        self.fx.bash(f'python3 {HOOKS} release w1a abc1234')
        self.assertIn("Your batch's spec is already released (`abc1234`)", self.start('foreman-worker: w1a'))
        self.assertNotIn('already released', self.start('foreman-worker: w1a spec-phase', agent='a2'))

    def test_shared_resources_are_cut_first_at_8_kb(self):
        long = '\n'.join(['shared_resources:'] + [f'  - rule {i} ' + 'x' * 200 for i in range(60)])
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'), CONFIG.replace('---\n', '---\n' + long + '\n', 1))
        ctx = self.start('foreman-worker: w1a')
        self.assertLessEqual(len(ctx.encode()), 8192)
        self.assertIn('You run steps 2-8', ctx)
        self.assertTrue(ctx.endswith('…(truncated)'))

    def test_briefing_disabled_still_records_the_map(self):
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'),
              CONFIG.replace('---\n', '---\nhooks_disabled: [worker_briefing]\n', 1))
        self.assertIsNone(self.start('foreman-worker: w1a spec-phase'))
        self.assertTrue(self.entry()['spec'])


GOOD = 'Report.\n\n```json\n{"done": [{"id": "IB-1", "note": "shipped"}], "append": [], "new_rows": [], "rule_notes": []}\n```\n'


class ReportCheckTest(Case):
    def stop(self, msg, prompt='foreman-worker: w1a\nrows', agent='a1', active=False, born=None):
        d = {'hook_event_name': 'SubagentStop', 'session_id': 'S1', 'cwd': self.fx.work, 'agent_id': agent,
             'agent_type': 'general-purpose', 'agent_transcript_path': transcript(self.fx, agent, prompt, born),
             'last_assistant_message': msg, 'stop_hook_active': active}
        out, _ = self.fx.hook('subagent-stop', d)
        return out['reason'] if out else None

    def test_missing_block(self):
        self.assertIn('end your report with ONE fenced json block', self.stop('All done, PR #5.'))

    def test_invalid_json(self):
        self.assertIn('does not parse', self.stop('x\n```json\n{"done": [}\n```\n'))

    def test_wrong_shape(self):
        self.assertIn('bad "done" field', self.stop('x\n```json\n{"done": ["IB-1"]}\n```\n'))

    def test_a_block_bookkeeping_would_refuse(self):
        why = self.stop('x\n```json\n{"done": [{"id": "IB-99", "note": "x"}]}\n```\n')
        self.assertIn('bookkeeping would refuse your json block: IB-99: 0 rows match', why)

    def test_a_valid_block_and_ignored_rule_notes(self):
        self.assertIsNone(self.stop(GOOD))
        notes = GOOD.replace('"rule_notes": []', '"rule_notes": [{"file": ".claude/rules/none.md", "old": "a", "new": "b"}]')
        self.assertIsNone(self.stop(notes))

    def test_last_json_block_wins(self):
        self.assertIsNone(self.stop('Draft:\n```json\n{"done": [}\n```\n\n' + GOOD + '\nThanks.'))
        self.assertIn('does not parse', self.stop(GOOD + '\nCorrected:\n```json\n{oops}\n```'))

    def test_stop_hook_active_and_untagged_workers_pass(self):
        self.assertIsNone(self.stop('no block', active=True))
        self.assertIsNone(self.stop('no block', prompt='just a helper'))

    def test_spec_phase_stops(self):
        prompt = 'foreman-worker: w1b spec-phase\nspec'
        self.assertIsNone(self.stop('Preface line.\nSPEC READY /wt b docs/s.md abc1234\nsummary', prompt=prompt))
        self.assertIn("you're in the spec phase", self.stop('A question for you?', prompt=prompt))
        self.fx.start_run()
        self.fx.bash(f'python3 {HOOKS} release w1b abc1234')
        self.assertIn('end your report with ONE fenced json block', self.stop('SPEC READY /wt b s.md abc', prompt=prompt))

    def test_stopping_lets_the_stopped_worker_through_but_not_a_later_replacement(self):
        self.fx.start_run()
        state.write_json(os.path.join(self.fx.state, 'batches', 'S1', 'agents', 'a1'),
                         {'batch': 'w1a', 'spec': False, 'time': time.time() - 60})
        self.fx.bash(f'python3 {HOOKS} stopping w1a')
        self.assertIsNone(self.stop('stopped mid-work', agent='a1'))
        time.sleep(0.01)
        state.write_json(os.path.join(self.fx.state, 'batches', 'S1', 'agents', 'a2'),
                         {'batch': 'w1a', 'spec': False, 'time': time.time()})
        self.assertIn('fenced json block', self.stop('stopped mid-work', agent='a2'))
        self.assertIsNone(self.stop('unmapped, older transcript', agent='a3', born=time.time() - 120))
        self.assertIn('fenced json block', self.stop('unmapped, newer transcript', agent='a4', born=time.time() + 120))


class BkNoFetchTest(unittest.TestCase):
    def test_no_fetch_skips_the_ui_branch_fetch(self):
        fx = Fx()
        try:
            rep = os.path.join(fx.tmp, 'r.json')
            write(rep, json.dumps({'pr': 0, 'done': [{'id': 'IB-1', 'note': 'x'}]}))
            git(fx.work, 'remote', 'set-url', 'origin', os.path.join(fx.tmp, 'gone.git'))
            p = subprocess.run([sys.executable, os.path.join(SCRIPTS, 'bk.py'), fx.work, '--dry-run', '--no-fetch', rep],
                               capture_output=True, text=True, env=dict(fx.env, GIT_TRACE='1'))
            self.assertIn('dry run: ok', p.stdout)
            self.assertNotIn('fetch', p.stderr)
        finally:
            fx.close()


```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 tests/test_hooks.py BriefingTest ReportCheckTest BkNoFetchTest`
Expected: FAIL. `subagent-start` and `subagent-stop` print nothing, and `bk.py` treats `--no-fetch` as a report path (`FileNotFoundError`).

- [ ] **Step 3: Add `--no-fetch` to `scripts/bk.py`**

Change the usage line to `Usage: bk.py <repo-dir> [--dry-run] [--no-fetch] report.json [report.json ...]`. In the `IB-NEW-x` bullet, change `(\`backlog/ui-*\`, fetched first)` to `(\`backlog/ui-*\`, fetched first unless --no-fetch)`. In `main`, replace the argument filter:

```python
    args = [a for a in sys.argv[1:] if a not in ('--dry-run', '--no-fetch')]
```

and guard the fetch:

```python
    if '--no-fetch' not in sys.argv:
        subprocess.run(['git', 'fetch', '-q', '--prune', 'origin', backlog.UI_REFSPEC], cwd=repo, capture_output=True)
```

- [ ] **Step 4: Add pieces 6 and 7 to `scripts/hooks.py`**

Insert above `# ---- pretool / posttool ----`:

```python
# ---- worker tags (pieces 6 and 7) -----------------------------------------------------------------

TAG = re.compile(r'^foreman-worker: ([A-Za-z0-9_-]+)( spec-phase)?\s*$')
FENCE = re.compile(r'```json[ \t]*\r?\n(.*?)```', re.S)
SKILL = os.path.join(HERE, '..', 'skills', 'foreman', 'SKILL.md')
BRIEF_CAP = 8192
CUT = ' …(truncated)'


def worker_tag(text):
    """(batch, spec) from a prompt whose first line is `foreman-worker: <batch>[ spec-phase]`, else None."""
    first = (text or '').split('\n', 1)[0].rstrip('\r')
    m = TAG.match(first)
    if m:
        return m.group(1), bool(m.group(2))
    m = re.match(r'^foreman-worker:\s*([A-Za-z0-9_-]+)', first)
    if m and 'spec-phase' in first:
        print(f'foreman: odd worker tag read as spec-phase: {first[:80]}', file=sys.stderr)
        return m.group(1), True
    return None


def transcript_head(path):
    """(first user message text, first entry's epoch time) of a JSONL transcript; Nones when unreadable."""
    text, born = None, None
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if born is None and isinstance(e.get('timestamp'), str):
                    born = iso_time(e['timestamp'])
                if text is None and e.get('type') == 'user':
                    c = (e.get('message') or {}).get('content')
                    if isinstance(c, list):
                        c = next((b.get('text') for b in c if isinstance(b, dict) and b.get('type') == 'text'), None)
                    text = c if isinstance(c, str) else None
                if text is not None and born is not None:
                    break
    except (OSError, TypeError):
        pass
    return text, born


def iso_time(s):
    import calendar
    try:
        return calendar.timegm(time.strptime(s[:19], '%Y-%m-%dT%H:%M:%S'))
    except ValueError:
        return None


def is_worker(repo, h):
    return h.inp.get('agent_type') in repo.list('worker_agent_types')


# ---- piece 6: worker briefing ---------------------------------------------------------------------

def section(text, heading):
    m = re.search(rf'^{re.escape(heading)}\n(.*?)(?=^#{{1,3}} |\Z)', text, re.S | re.M)
    return m.group(1).strip() if m else ''


def default_table(text):
    """{key: default} from SKILL.md's Step 0.5 table, preferring the default's first backticked span."""
    out = {}
    for line in text.splitlines():
        cells = line.strip().strip('|').split(' | ')
        m = re.fullmatch(r'\s*`(\w+)`\s*', cells[0]) if len(cells) == 3 else None
        if m:
            code = re.search(r'`([^`]+)`', cells[2])
            out[m.group(1)] = code.group(1) if code else cells[2].strip()
    return out


def briefing(repo, batch, spec, sid):
    with open(SKILL, encoding='utf-8') as f:
        skill = f.read()
    defaults = default_table(skill)

    def key(m):
        v = repo.cfg.get(m.group(1))
        v = defaults.get(m.group(1), m.group(0)) if v is None else v
        return ', '.join(map(str, v)) if isinstance(v, list) else str(v)

    text = re.sub(r'\{\{(\w+)\}\}', key, section(skill, '### Worker contract'))
    text = (text.replace('$FOREMAN', HERE).replace('<prefix>', str(repo.get('id_prefix', 'IB')))
            .replace('<batch>', batch))
    text += ('\n\n<scratch> = your scratchpad dir; <worktree> and {worktree} = your current checkout\'s top level; '
             '{repo} = the main checkout')
    head = ['# foreman worker briefing (batch ' + batch + ')', text, '',
            f"Gate (run it under {HERE}/gate-lock.sh {batch} -- '<gate>'): {repo.get('gate') or '(none configured)'}",
            'Forbidden trailer lines: ' + (', '.join(map(str, repo.list('forbid_trailers'))) or 'none')]
    rel = released(repo, sid, batch)
    if not spec and rel:
        head.append(f"Your batch's spec is already released (`{rel.get('sha')}`): skip the spec phase and continue "
                    'the build phase on the existing branch.')
    out = '\n'.join(head) + '\n'
    shared = '\n'.join(['', 'Shared resources:'] + [f'- {s}' for s in repo.list('shared_resources')])
    if len((out + shared).encode()) > BRIEF_CAP:
        cut = BRIEF_CAP - len(out.encode()) - len(CUT.encode())
        shared = shared.encode()[:max(cut, 0)].decode('utf-8', 'ignore') + CUT
    return out + shared


def subagent_start(h):
    repo = repo_at(h.cwd)
    if not repo or not is_worker(repo, h):
        return
    text = h.inp.get('prompt')
    if not isinstance(text, str):
        text, _ = transcript_head(h.inp.get('agent_transcript_path') or '')
    tag = worker_tag(text)
    if not tag:
        return
    batch, spec = tag
    if h.agent and h.sid and os.sep not in h.agent + h.sid:
        state.write_json(os.path.join(repo.dirs[0], 'batches', h.sid, 'agents', h.agent),
                         {'batch': batch, 'spec': spec, 'time': time.time()})
    if not repo.disabled('worker_briefing'):
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SubagentStart',
                                                 'additionalContext': briefing(repo, batch, spec, h.sid)}}))


HANDLERS['subagent-start'] = subagent_start


# ---- piece 7: report check ------------------------------------------------------------------------

def shape_problem(rep):
    """The first field of a worker's json report that bk.py would crash on, or None."""
    if not isinstance(rep, dict):
        return 'the block'
    for k in ('done', 'append'):
        v = rep.get(k, [])
        if not (isinstance(v, list) and all(isinstance(x, dict) and isinstance(x.get('id'), str)
                                            and isinstance(x.get('note'), str) for x in v)):
            return k
    v = rep.get('new_rows', [])
    if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
        return 'new_rows'
    v = rep.get('rule_notes', [])
    if not (isinstance(v, list) and all(isinstance(x, dict) and all(isinstance(x.get(f), str)
                                                                    for f in ('file', 'old', 'new')) for x in v)):
        return 'rule_notes'
    return None


def report_problem(msg, cwd):
    blocks = FENCE.findall(msg)
    if not blocks:
        return "end your report with ONE fenced json block in the worker contract's format, even if empty"
    try:
        rep = json.loads(blocks[-1])
    except ValueError as e:
        return f'the json block at the end of your report does not parse ({e}); fix it'
    bad = shape_problem(rep)
    if bad:
        return (f'the json block has a bad "{bad}" field: done/append are lists of {{"id", "note"}} strings, '
                'new_rows a list of row strings, rule_notes a list of {"file", "old", "new"}')
    rep.pop('rule_notes', None)
    rep['pr'] = 0
    top = state.toplevel(cwd) or cwd
    fd, tmp = tempfile.mkstemp(suffix='.json')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(rep, f)
        env = {k: v for k, v in os.environ.items() if k != 'FOREMAN_CONFIG'}
        p = subprocess.run([sys.executable, os.path.join(HERE, 'bk.py'), top, '--dry-run', '--no-fetch', tmp],
                           capture_output=True, text=True, timeout=20, env=env)
    except subprocess.TimeoutExpired:
        print('foreman: report check skipped (bk.py timed out)', file=sys.stderr)
        return None
    finally:
        os.remove(tmp)
    if 'PROBLEMS (nothing written)' in p.stdout:
        lines = [l.strip() for l in p.stdout.split('PROBLEMS (nothing written):', 1)[1].splitlines() if l.strip()]
        return 'bookkeeping would refuse your json block: ' + '; '.join(lines)
    if p.returncode:
        print(f'foreman: report check skipped (bk.py exit {p.returncode}): {p.stderr.strip()[-300:]}', file=sys.stderr)
    return None


def stopping_allowed(repo, h, batch):
    """True when `hooks.py stopping <batch>` ran after this worker started (piece 7, probe P7)."""
    stop = next((s for s in (state.read_json(batch_file(d, h.sid, batch, 'stopped')) for d in repo.dirs)
                 if isinstance(s, dict)), None)
    if stop is None:
        return False
    entry = agent_entry(repo, h.sid, h.agent)
    born = entry.get('time') if entry else transcript_head(h.inp.get('agent_transcript_path') or '')[1]
    return born is None or float(born) < float(stop.get('time', 0))


def subagent_stop(h):
    if h.inp.get('stop_hook_active'):
        return
    repo = repo_at(h.cwd)
    if not h.active(repo, 'report_check', run_only=False) or not is_worker(repo, h):
        return
    tag = worker_tag(transcript_head(h.inp.get('agent_transcript_path') or '')[0])
    if not tag:
        return
    batch, spec = tag
    if stopping_allowed(repo, h, batch):
        return
    msg = h.inp.get('last_assistant_message') or ''
    if spec and not released(repo, h.sid, batch):
        if any(line.startswith('SPEC READY ') for line in msg.splitlines()):
            return
        why = ("you're in the spec phase: finish the spec and end with `SPEC READY <worktree> <branch> <spec path> "
               '<sha>` as the first line')
    else:
        why = report_problem(msg, h.cwd)
    if why:
        print(json.dumps({'decision': 'block', 'reason': 'foreman: ' + why}))


HANDLERS['subagent-stop'] = subagent_stop


```

- [ ] **Step 5: Run the tests**

Run: `python3 tests/test_hooks.py && tests/run.sh`
Expected: `test_hooks.py` OK (83 tests); `run.sh` ends `failed 0` (its bk.py checks don't pass `--no-fetch` and are unchanged).

- [ ] **Step 6: Commit**

```bash
git add scripts/hooks.py scripts/bk.py tests/test_hooks.py
git commit -m "Hooks pieces 6 and 7: worker briefing and agent map, report check; bk.py --no-fetch"
```

---

### Task 9: Piece 8: script allow (+ `wt-setup.sh` and `review.sh` read the main checkout's config)

**Files:**
- Modify: `scripts/hooks.py` (insert above `# ---- pretool / posttool ----`)
- Modify: `scripts/wt-setup.sh` (header comment; `main_of`; every `cfg` call)
- Modify: `scripts/review.sh` (line 20: `cfg` reads the main checkout)
- Modify: `tests/test_hooks.py` (new class above `if __name__ == '__main__':`)
- Modify: `tests/run.sh` (a planted-config section; one review.sh check)

**Interfaces:**
- Consumes: `repo_at`, `Hook.active`, `same_file`, `control_words`, `HANDLERS`, `HERE` (Task 4); `worktrees` (Task 6); `shellscan.scan`, `Scan.segs`, `Scan.blanked`, `Seg.words`, `Seg.sep`, `Seg.cwd` (Task 3); `Fx.gate` (Task 4 tests).
- Produces:
  - `SCRIPTS`, `OWN_OPTS`, `BAD_TEXT`, `BAD_ARG`; `plain(arg)`, `is_worktree(repo, path)`, `script_args_ok(name, args, repo, h, cwd)`, `script_allowed(h, command) -> bool`
  - registers `HANDLERS['permission']`
  - `wt-setup.sh` and `review.sh` take every config key from the main checkout (the first `git worktree list` entry). They still honour `FOREMAN_CONFIG`, which is safe because piece 8 gives no decision to an env prefix.
- Note: the gate argument of `gate-lock.sh` must equal `gate` or `post_merge_check` exactly; that exact match exempts it from the character-set and leading-`-` rules (resolution 5).

- [ ] **Step 1: Write the failing tests**

Add above `if __name__ == '__main__':`. They cover the spec's piece 8 list: an exact path; no decision for a newline, `$(`, `; rm`, `BASH_ENV=x bash <script>`, `FOREMAN_CONFIG=x <script>`, `env <script>` and a `>` redirect; the one-paren wrap with `--spec`, `post_merge_check` (no receipt minted), a `~/` path, `hooks.py run-end`, `release`, `stopping` and `forget`; no decision for a non-gate command, `--name`, a non-worktree dir, and bk.py without `--dry-run` from a subagent.

```python
class ScriptAllowTest(Case):
    config = CONFIG.replace('gate: echo gated\n', 'gate: echo gated\npost_merge_check: echo whole-tree\n')

    def setUp(self):
        super().setUp()
        self.fx.start_run()
        self.wt = os.path.join(self.fx.tmp, 'wt')
        git(self.fx.work, 'worktree', 'add', '-q', self.wt, '-b', 'w1')

    def allowed(self, command, agent=None, cwd=None):
        out, _ = self.fx.hook('permission', self.fx.payload('Bash', {'command': command}, agent=agent, cwd=cwd,
                                                           event='PermissionRequest'))
        if out:
            self.assertEqual(out, {'hookSpecificOutput': {'hookEventName': 'PermissionRequest',
                                                          'decision': {'behavior': 'allow'}}})
        return out is not None

    def test_exact_paths_are_allowed(self):
        self.assertTrue(self.allowed(f"{GATE_LOCK} w1 -- 'echo gated'"))
        self.assertTrue(self.allowed(f'{SCRIPTS}/wt-setup.sh {self.wt}'))
        self.assertTrue(self.allowed(f'bash {SCRIPTS}/wt-setup.sh --check {self.wt}'))
        self.assertTrue(self.allowed(f'python3 {SCRIPTS}/bk.py {self.fx.work} --dry-run /tmp/r.json'))
        self.assertTrue(self.allowed(f'python3 {SCRIPTS}/bk.py {self.fx.work} /tmp/r.json'))
        self.assertTrue(self.allowed(f'python3 {SCRIPTS}/bk.py {self.fx.work} --dry-run /tmp/r.json', agent='a1'))

    def test_one_paren_wrap_with_cd_and_review_spec(self):
        self.assertTrue(self.allowed(f'(cd {self.wt} && {SCRIPTS}/review.sh --spec docs/s.md origin/main /tmp/r.log)'))
        self.assertTrue(self.allowed(f'cd {self.wt} && {SCRIPTS}/review.sh origin/main /tmp/r.log'))
        self.assertTrue(self.allowed(f'{SCRIPTS}/review.sh origin/main /tmp/r.log', cwd=self.wt))

    def test_post_merge_check_is_allowed_and_mints_nothing(self):
        self.assertTrue(self.allowed(f"{GATE_LOCK} orchestrator -- 'echo whole-tree'"))
        self.fx.gate('echo whole-tree', owner='orchestrator')
        self.assertFalse(os.path.isdir(os.path.join(self.fx.state, 'gate')))

    def test_a_home_relative_plugin_path(self):
        home = self.fx.env['HOME']
        os.symlink(os.path.dirname(SCRIPTS), os.path.join(home, 'foreman'))
        self.assertTrue(self.allowed(f"~/foreman/scripts/gate-lock.sh w1 -- 'echo gated'"))

    def test_hooks_py_for_the_main_session_only(self):
        self.assertTrue(self.allowed(f'python3 {HOOKS} run-end'))
        self.assertTrue(self.allowed(f'python3 {HOOKS} release w1a abc1234'))
        self.assertTrue(self.allowed(f'python3 {HOOKS} stopping w1a'))
        self.assertTrue(self.allowed(f'python3 {HOOKS} forget w1a'))
        self.assertFalse(self.allowed(f'python3 {HOOKS} run-end', agent='a1'))
        self.assertFalse(self.allowed(f'python3 {HOOKS} release w1a not-a-sha'))

    def test_no_decision(self):
        script = f'{SCRIPTS}/wt-setup.sh {self.wt}'
        for cmd in (script + '\necho hi', f'{SCRIPTS}/wt-setup.sh $(pwd)', script + '; rm -rf /tmp/x',
                    f'BASH_ENV=x bash {script}', f'FOREMAN_CONFIG=x {script}', f'env {script}', script + ' > /tmp/out',
                    f'(({script}))', f'{{ {script}; }}', f'command {script}'):
            self.assertFalse(self.allowed(cmd), cmd)

    def test_no_decision_for_wrong_arguments(self):
        self.assertFalse(self.allowed(f"{GATE_LOCK} w1 -- 'echo other'"))
        self.assertFalse(self.allowed(f"{GATE_LOCK} --name db w1 -- 'echo gated'"))
        self.assertFalse(self.allowed(f'{GATE_LOCK} w1 -- echo gated'))
        self.assertFalse(self.allowed(f'{SCRIPTS}/wt-setup.sh {self.fx.tmp}'))
        self.assertFalse(self.allowed(f'python3 {SCRIPTS}/bk.py {self.fx.work} /tmp/r.json', agent='a1'))
        self.assertFalse(self.allowed(f'{SCRIPTS}/review.sh origin/main /tmp/r.log', cwd=self.fx.tmp))
        self.assertFalse(self.allowed(f"{SCRIPTS}/review.sh 'origin/main;x' /tmp/r.log", cwd=self.wt))
        self.assertFalse(self.allowed(f'{SCRIPTS}/review.sh --verbose origin/main', cwd=self.wt))
        self.assertFalse(self.allowed(f'{self.fx.tmp}/gate-lock.sh w1 -- x'))

    def test_only_during_a_run(self):
        out, _ = self.fx.hook('permission', self.fx.payload('Bash', {'command': f"{GATE_LOCK} w1 -- 'echo gated'"},
                                                           sid='S2', event='PermissionRequest'))
        self.assertIsNone(out)


```

Add to `tests/run.sh`, after the gate-lock section (after `unset FOREMAN_LOCK_ROOT FOREMAN_LOCK_POLL`) and before `echo "## review.sh with fake adapters and fake CLIs"`. It must come after the gate-lock section, whose checks assume the current directory is `$T/repo`:

```bash
echo "## config comes from the main checkout, not a worktree's planted copy"
M=$T/main && mkdir -p "$M/.claude" && cd "$M" && git init -q -b main && echo x >f
printf -- '---\nworktree_install: touch from-main.marker\nreviewers:\n  - { use: good, model: m, effort: e }\n---\n' >"$M/.claude/foreman.config.md"
git add -A && git commit -qm init && git worktree add -q "$T/mwt" -b mw 2>/dev/null
printf -- '---\nworktree_install: touch planted.marker\nreviewers:\n  - { use: custom, model: touch %s/PLANTED, effort: e }\n---\n' "$T" >"$T/mwt/.claude/foreman.config.md"
"$root/scripts/wt-setup.sh" "$T/mwt" >/dev/null 2>&1
check "wt-setup runs the main checkout's install" test -e "$T/mwt/from-main.marker"
check "...never the worktree's planted one" test ! -e "$T/mwt/planted.marker"
```

And in the review.sh section, right before `chain 60 30 200 opencode good >"$T/chain.md"; PATH=$F:$PATH rv` (after the real adapters, `custom.sh` included, are copied into `$A`):

```bash
(cd "$T/mwt" && FOREMAN_REVIEWERS_DIR=$A FOREMAN_WATCH_POLL=1 "$root/scripts/review.sh" main "$T/out/planted.log" 2>/dev/null)
check "review.sh runs the main checkout's chain" grep -q '^good' "$T/out/planted.log.reviewer"
check "...never a worktree's planted custom reviewer" test ! -e "$T/PLANTED"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 tests/test_hooks.py ScriptAllowTest; tests/run.sh | grep -E '^FAIL|passed'`
Expected:
- `ScriptAllowTest` FAILs: `permission` prints nothing, so every "allowed" case is False.
- `run.sh` FAILs "...never the worktree's planted one" and "...never a worktree's planted custom reviewer".

- [ ] **Step 3: Read config from the main checkout in `scripts/wt-setup.sh`**

Replace the header line `# The main checkout is the first entry of \`git worktree list\`. Config keys (see templates/foreman.config.md):` with:

```bash
# The main checkout is the first entry of `git worktree list`. Config is read from the MAIN checkout, never from
# the worktree itself: a config planted in a worktree must not choose the install command. Keys (see
# templates/foreman.config.md):
```

After `cfg() { … }` add:

```bash
main_of() { /usr/bin/git -C "$1" worktree list --porcelain 2>/dev/null | awk '/^worktree /{print substr($0, 10); exit}' || true; }
```

In `stale_check`, replace `local dir=$1 cmd` and `cmd=$(cfg "$dir" stale_check)` with:

```bash
  local dir=$1 cmd main
  main=$(main_of "$dir")
  cmd=$(cfg "${main:-$dir}" stale_check)
```

Replace `main=$(/usr/bin/git -C "$wt" worktree list --porcelain | awk '/^worktree /{print substr($0, 10); exit}')` with `main=$(main_of "$wt")`. Change the three remaining `cfg "$wt" …` calls (`worktree_env_files`, `worktree_install`, `stale_check`) to `cfg "$main" …`. The `|| true` keeps `set -e` from killing the script before its own "is not a git worktree" message.

- [ ] **Step 4: Read config from the main checkout in `scripts/review.sh`**

Replace line 20 (`cfg() { python3 "$here/cfg.py" "$repo" "$@"; }`) with:

```bash
# Config comes from the MAIN checkout, never the worktree: a config planted in a worktree must not pick the
# reviewer commands (a `custom` entry runs any command).
main=$(/usr/bin/git worktree list --porcelain | awk '/^worktree /{print substr($0, 10); exit}')
cfg() { python3 "$here/cfg.py" "${main:-$repo}" "$@"; }
```

- [ ] **Step 5: Add piece 8 to `scripts/hooks.py`**

Insert above `# ---- pretool / posttool ----`:

```python
# ---- piece 8: script allow ------------------------------------------------------------------------

SCRIPTS = ('gate-lock.sh', 'wt-setup.sh', 'review.sh', 'bk.py', 'hooks.py')
OWN_OPTS = ('--', '--check', '--spec', '--dry-run')
BAD_TEXT = re.compile(r'[\n\r`<>]|\$\(|\$\{')
BAD_ARG = re.compile(r'[\'"$`\;|&<>\x00-\x1f\x7f]')


def plain(arg):
    return not BAD_ARG.search(arg) and (not arg.startswith('-') or arg in OWN_OPTS)


def is_worktree(repo, path):
    return os.path.realpath(path) in {os.path.realpath(p) for p, _ in worktrees(repo.main)}


def script_args_ok(name, args, repo, h, cwd):
    if name == 'gate-lock.sh':
        gates = {repo.get('gate'), repo.get('post_merge_check') or repo.get('gate')} - {None, ''}
        return len(args) == 3 and plain(args[0]) and args[1] == '--' and args[2] in gates
    if not all(plain(a) for a in args):
        return False
    if name == 'wt-setup.sh':
        rest = args[1:] if args[:1] == ['--check'] else args
        return len(rest) == 1 and rest[0] not in OWN_OPTS and is_worktree(repo, os.path.join(cwd, rest[0]))
    if name == 'review.sh':
        rest = args[2:] if args[:1] == ['--spec'] and len(args) > 1 else args
        return len(rest) == 2 and not set(rest) & set(OWN_OPTS) and is_worktree(repo, cwd)
    if name == 'bk.py':
        return (not h.agent or '--dry-run' in args) and len([a for a in args if a not in OWN_OPTS]) >= 2
    return not h.agent and control_words([os.path.join(HERE, 'hooks.py')] + args) is not None


def script_allowed(h, command):
    """True when `command` runs one of foreman's own scripts in a strict shape (piece 8 never denies)."""
    if BAD_TEXT.search(command):
        return False
    sc = shellscan.scan(command, h.cwd)
    if not sc.ok or any(s.words is None for s in sc.segs):
        return False
    b = sc.blanked.strip()
    if (b.count('(') or b.count(')')) and not (b.count('(') == b.count(')') == 1 and b[0] == '(' and b[-1] == ')'):
        return False
    segs = [s for s in sc.segs if s.words]
    if len(segs) == 2 and segs[0].words[0] == 'cd' and len(segs[0].words) == 2 and segs[0].sep == '&&':
        cmd = segs[1]
    elif len(segs) == 1:
        cmd = segs[0]
    else:
        return False
    cd = segs[0] if cmd is not segs[0] else None
    if any(s.sep not in (None, '(', ')') for s in sc.segs if s is not cd):
        return False
    words = cmd.words[1:] if cmd.words[0] in ('python3', 'bash', 'sh') else cmd.words
    name = os.path.basename(words[0]) if words else ''
    if name not in SCRIPTS or not same_file(words[0], os.path.join(HERE, name)) or cmd.cwd is None:
        return False
    repo = repo_at(cmd.cwd)
    return h.active(repo, 'script_allow') and script_args_ok(name, words[1:], repo, h, cmd.cwd)


def permission(h):
    if h.inp.get('tool_name') == 'Bash' and script_allowed(h, (h.inp.get('tool_input') or {}).get('command') or ''):
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PermissionRequest',
                                                 'decision': {'behavior': 'allow'}}}))


HANDLERS['permission'] = permission


```

- [ ] **Step 6: Run the tests**

Run: `python3 tests/test_hooks.py && tests/run.sh`
Expected: `test_hooks.py` OK (91 tests); `run.sh` ends `failed 0`.

- [ ] **Step 7: Commit**

```bash
git add scripts/hooks.py scripts/wt-setup.sh scripts/review.sh tests/test_hooks.py tests/run.sh
git commit -m "Hooks piece 8: allow foreman's own scripts; wt-setup and review read the main checkout's config"
```

---

### Task 10: Piece 10: spec gate

**Files:**
- Modify: `scripts/hooks.py` (insert above `# ---- pretool / posttool ----`)
- Modify: `tests/test_hooks.py` (new class above `if __name__ == '__main__':`)

**Interfaces:**
- Consumes: `agent_entry`, `released`, `Hook.active`, `Repo.rel`, `Repo.get('design_spec_dir')` (default from `cfg.DEFAULTS`), `cmd_repo`, `git_out`, `BASH_PIECES`, `EDIT_PIECES` (Task 4); `is_push_trigger` (Task 5); `has_flag`, `positionals` (Task 6); the agent map written by piece 6 (Task 8).
- Produces: `GIT_COMMIT_VALUE_OPTS`; `gated_batch(h, repo)`, `spec_reason(batch)`, `in_spec_dir(repo, rel)`, `changed_paths(top, everything)`, `commit_paths(cmd, sc, top) -> set | None`; registers `BASH_PIECES[10] = spec_gate` and `EDIT_PIECES[10] = spec_gate_edit`.

**Probe fallback:** if P6 failed (SubagentStart and PreToolUse ids differ), every lookup misses and the gate fails open. That is the designed degradation; the README's limits say so (Task 14).

- [ ] **Step 1: Write the failing tests**

Add above `if __name__ == '__main__':`. They cover the spec's piece 10 list.

```python
class SpecGateTest(Case):
    def setUp(self):
        super().setUp()
        self.fx.start_run()
        self.wt = os.path.join(self.fx.tmp, 'wt')
        git(self.fx.work, 'worktree', 'add', '-q', self.wt, '-b', 'w1b')
        self.map('a1', spec=True)

    def map(self, agent, spec):
        state.write_json(os.path.join(self.fx.state, 'batches', 'S1', 'agents', agent),
                         {'batch': 'w1b', 'spec': spec, 'time': time.time()})

    def spec(self, name='plan.md'):
        return os.path.join(self.wt, 'docs/superpowers/specs', name)

    def test_edits_outside_the_spec_dir_are_denied(self):
        self.assertIn("batch w1b's spec isn't released yet", self.fx.edit(os.path.join(self.wt, 'app.py'),
                                                                          agent='a1', cwd=self.wt))
        self.assertIsNone(self.fx.edit(self.spec(), tool='Write', agent='a1', cwd=self.wt))

    def test_commits_are_judged_by_what_they_would_include(self):
        write(os.path.join(self.wt, 'app.py'), 'print(2)\n')
        git(self.wt, 'add', 'app.py')
        self.assertIn('this commit would include app.py', self.fx.bash('git commit -m wip', agent='a1', cwd=self.wt))
        git(self.wt, 'reset', '-q')
        write(self.spec(), '# spec\n')
        git(self.wt, 'add', self.spec())
        self.assertIsNone(self.fx.bash('git commit -m spec', agent='a1', cwd=self.wt))
        git(self.wt, 'commit', '-q', '-m', 'spec')
        self.assertIn('app.py', self.fx.bash('git commit -a -m wip', agent='a1', cwd=self.wt))
        self.assertIn('app.py', self.fx.bash('git commit -m wip app.py', agent='a1', cwd=self.wt))
        self.assertIn('app.py', self.fx.bash('git add -A && git commit -m wip', agent='a1', cwd=self.wt))
        self.assertIn('app.py', self.fx.bash('gt create -a -m wip', agent='a1', cwd=self.wt))
        self.assertIsNone(self.fx.bash(f'git add {self.spec()} && git commit -m spec', agent='a1', cwd=self.wt))

    def test_pushes_are_denied_before_release(self):
        self.assertIn("isn't released yet", self.fx.bash('git push -u origin HEAD', agent='a1', cwd=self.wt))
        self.assertIn("isn't released yet", self.fx.bash('gh pr create --fill', agent='a1', cwd=self.wt))

    def test_after_release_everything_passes(self):
        self.fx.bash(f'python3 {HOOKS} release w1b abc1234')
        self.assertIsNone(self.fx.edit(os.path.join(self.wt, 'app.py'), agent='a1', cwd=self.wt))
        write(os.path.join(self.wt, 'app.py'), 'print(2)\n')
        self.assertIsNone(self.fx.bash('git commit -a -m wip', agent='a1', cwd=self.wt))

    def test_unmapped_non_spec_and_main_session_pass(self):
        self.assertIsNone(self.fx.edit(os.path.join(self.wt, 'app.py'), agent='a9', cwd=self.wt))
        self.map('a2', spec=False)
        self.assertIsNone(self.fx.edit(os.path.join(self.wt, 'app.py'), agent='a2', cwd=self.wt))
        self.assertIsNone(self.fx.edit(os.path.join(self.wt, 'app.py'), cwd=self.wt))
        self.assertIsNone(self.fx.edit(os.path.join(self.wt, 'app.py'), agent='a1', sid='S2', cwd=self.wt))


```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 tests/test_hooks.py SpecGateTest`
Expected: FAIL. The out-of-spec Edit, the code commits and the pushes all return None.

- [ ] **Step 3: Add piece 10 to `scripts/hooks.py`**

Insert above `# ---- pretool / posttool ----`:

```python
# ---- piece 10: spec gate --------------------------------------------------------------------------

GIT_COMMIT_VALUE_OPTS = ('-m', '--message', '-F', '--file', '-C', '--reuse-message', '-c', '--reedit-message', '-t',
                         '--template', '--author', '--date', '--cleanup', '--fixup', '--squash', '--trailer',
                         '--pathspec-from-file')


def gated_batch(h, repo):
    """The batch when this subagent is a mapped spec-phase worker whose batch isn't released, else None."""
    if not h.agent or not h.active(repo, 'spec_gate'):
        return None
    e = agent_entry(repo, h.sid, h.agent)
    if not e or not e.get('spec') or released(repo, h.sid, e.get('batch', '')):
        return None
    return e['batch']


def spec_reason(batch):
    return (f"batch {batch}'s spec isn't released yet. Finish the spec, put `SPEC READY <worktree> <branch> "
            "<spec path> <sha>` as your final message's first line, and wait for the orchestrator's release")


def in_spec_dir(repo, rel):
    d = os.path.normpath(str(repo.get('design_spec_dir'))).strip('/')
    return rel == d or rel.startswith(d + '/')


def changed_paths(top, everything):
    """Top-level-relative paths: modified tracked files, plus untracked ones when `everything`."""
    if not everything:
        return (git_out(top, 'diff', '--name-only') or '').splitlines()
    toks = (git_out(top, 'status', '--porcelain', '-uall', '-z') or '').split('\0')
    out, i = [], 0
    while i < len(toks):
        if len(toks[i]) > 3:
            out.append(toks[i][3:])
            if toks[i][0] in 'RC':
                i += 1
        i += 1
    return out


def commit_paths(c, sc, top):
    """The paths a `git commit` / `gt create|modify -a` would commit, or None when they can't be told."""
    if c.args is None or c.cwd is None:
        return None
    rel = lambda p: os.path.relpath(os.path.normpath(os.path.join(c.cwd, p)), top)  # noqa: E731
    paths = set((git_out(top, 'diff', '--cached', '--name-only') or '').splitlines())
    if c.prog == 'gt':
        if has_flag(c.args, ('--all', '-a')):
            paths |= set(changed_paths(top, everything=True))
        return paths
    if has_flag(c.args, ('--all', '-a')):
        paths |= set(changed_paths(top, everything=False))
    paths |= {rel(p) for p in positionals(c.args, GIT_COMMIT_VALUE_OPTS)}
    for a in sc.cmds:
        if a is c:
            break
        if (a.prog, a.sub) == ('git', 'add') and a.args is not None and a.cwd:
            named = [p for p in positionals(a.args, ('--pathspec-from-file', '--chmod')) if p]
            if has_flag(a.args, ('-A', '--all')) or '.' in named or not named:
                paths |= set(changed_paths(top, everything=True))
            paths |= {os.path.relpath(os.path.normpath(os.path.join(a.cwd, p)), top) for p in named if p != '.'}
    return paths


def spec_gate(h, sc):
    for c in sc.cmds:
        repo = cmd_repo(c, h)
        batch = gated_batch(h, repo) if repo else None
        if not batch:
            continue
        if is_push_trigger(c):
            h.touch.add(repo)
            h.deny(spec_reason(batch) + ' (the spec phase doesn\'t push)')
        elif (c.prog, c.sub) in (('git', 'commit'), ('gt', 'create'), ('gt', 'modify')):
            h.touch.add(repo)
            top = state.toplevel(c.cwd) if c.cwd else None
            paths = commit_paths(c, sc, top) if top else None
            outside = sorted(p for p in paths or () if not in_spec_dir(repo, p))
            if paths is None:
                h.deny(spec_reason(batch) + "; foreman couldn't tell what this commit would include")
            elif outside:
                h.deny(spec_reason(batch) + '; this commit would include ' + ', '.join(outside[:10]))


def spec_gate_edit(h, repo, path):
    batch = gated_batch(h, repo) if repo else None
    if batch and not in_spec_dir(repo, repo.rel(path)):
        h.touch.add(repo)
        h.deny(spec_reason(batch))


BASH_PIECES[10] = spec_gate
EDIT_PIECES[10] = spec_gate_edit


```

- [ ] **Step 4: Run the tests**

Run: `python3 tests/test_hooks.py`
Expected: OK (96 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/hooks.py tests/test_hooks.py
git commit -m "Hooks piece 10: spec gate for unreleased spec-phase batches"
```

---

### Task 11: `hooks/hooks.json`

**Files:**
- Create: `hooks/hooks.json`
- Create: `tests/test_hooks_json.py`
- Modify: `tests/run.sh` (the `## shellscan.py + hooks.py` section)

**Interfaces:**
- Consumes: `hooks.HANDLERS`, with keys `run-start`, `session-end`, `pretool`, `posttool` (Task 4), `subagent-start`, `subagent-stop` (Task 8) and `permission` (Task 9).
- Produces: the plugin's settings hooks. Task 13 adds `"modules": ["./status.mjs"]` once that file exists. It can't be added here, because Claude Code fails to load a plugin whose module is missing.

- [ ] **Step 1: Write the failing test**

Create `tests/test_hooks_json.py`:

```python
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import hooks  # noqa: E402

WANT = {
    ('UserPromptSubmit', None): 'run-start',
    ('UserPromptExpansion', None): 'run-start',
    ('SessionEnd', None): 'session-end',
    ('PreToolUse', 'Bash'): 'pretool',
    ('PreToolUse', 'Edit|Write|MultiEdit|NotebookEdit'): 'pretool',
    ('PostToolUse', 'Edit|Write|MultiEdit'): 'posttool',
    ('PostToolUse', 'Bash'): 'posttool',
    ('SubagentStart', None): 'subagent-start',
    ('SubagentStop', None): 'subagent-stop',
    ('PermissionRequest', 'Bash'): 'permission',
}


class HooksJsonTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, 'hooks', 'hooks.json')) as f:
            self.data = json.load(f)

    def entries(self):
        for event, groups in self.data['hooks'].items():
            for g in groups:
                for h in g['hooks']:
                    yield event, g.get('matcher'), h

    def test_every_event_runs_its_subcommand_in_exec_form(self):
        seen = {}
        for event, matcher, h in self.entries():
            self.assertEqual(h['type'], 'command')
            self.assertEqual(h['command'], 'python3')
            self.assertEqual(h['args'][0], '${CLAUDE_PLUGIN_ROOT}/scripts/hooks.py')
            self.assertNotIn('if', h)
            self.assertEqual(h['timeout'], 30 if event == 'SubagentStop' else 10)
            seen[(event, matcher)] = h['args'][1]
        self.assertEqual(seen, WANT)

    def test_every_subcommand_has_a_handler(self):
        for sub in set(WANT.values()):
            self.assertIn(sub, hooks.HANDLERS, sub)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 tests/test_hooks_json.py`
Expected: FAIL with `FileNotFoundError: … hooks/hooks.json`.

- [ ] **Step 3: Create `hooks/hooks.json`**

Exec form throughout, and no `if:` on Bash: `pretool` scans the command itself and returns at once when it names no `git`, `gh`, `gt` or `hooks.py`.

```json
{
  "description": "foreman hooks: gate receipts, worker guard, merge discipline, trailer guard, backlog rows, worker briefing and report check, script allow, spec gate",
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command", "command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/hooks.py", "run-start"], "timeout": 10 }] }
    ],
    "UserPromptExpansion": [
      { "hooks": [{ "type": "command", "command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/hooks.py", "run-start"], "timeout": 10 }] }
    ],
    "SessionEnd": [
      { "hooks": [{ "type": "command", "command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/hooks.py", "session-end"], "timeout": 10 }] }
    ],
    "PreToolUse": [
      { "matcher": "Bash", "hooks": [{ "type": "command", "command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/hooks.py", "pretool"], "timeout": 10 }] },
      { "matcher": "Edit|Write|MultiEdit|NotebookEdit", "hooks": [{ "type": "command", "command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/hooks.py", "pretool"], "timeout": 10 }] }
    ],
    "PostToolUse": [
      { "matcher": "Edit|Write|MultiEdit", "hooks": [{ "type": "command", "command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/hooks.py", "posttool"], "timeout": 10 }] },
      { "matcher": "Bash", "hooks": [{ "type": "command", "command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/hooks.py", "posttool"], "timeout": 10 }] }
    ],
    "SubagentStart": [
      { "hooks": [{ "type": "command", "command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/hooks.py", "subagent-start"], "timeout": 10 }] }
    ],
    "SubagentStop": [
      { "hooks": [{ "type": "command", "command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/hooks.py", "subagent-stop"], "timeout": 30 }] }
    ],
    "PermissionRequest": [
      { "matcher": "Bash", "hooks": [{ "type": "command", "command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/hooks.py", "permission"], "timeout": 10 }] }
    ]
  }
}
```

- [ ] **Step 4: Run the test, the suite and the validator**

Run:

```bash
python3 tests/test_hooks_json.py
d=$(mktemp -d) && rsync -a --exclude .git --exclude .claude-plugin/marketplace.json ./ "$d/" && claude plugin validate "$d"; rm -rf "$d"
```

Expected: OK (2 tests), then `✔ Validation passed`. The copy without `marketplace.json` matters: with that file present, `claude plugin validate .` checks only the marketplace manifest.

Add to the `## shellscan.py + hooks.py` section of `tests/run.sh`:

```bash
check "test_hooks_json.py" python3 "$root/tests/test_hooks_json.py"
```

Run: `tests/run.sh`
Expected: ends `failed 0`.

- [ ] **Step 5: Commit**

```bash
git add hooks/hooks.json tests/test_hooks_json.py tests/run.sh
git commit -m "Register the foreman hooks (hooks/hooks.json)"
```

---

### Task 12: Mod API binding against the generated `.d.ts` (controller only)

**Run by the controller:** it loads a throwaway mod in a real Claude Code session to get the type declarations for the installed build. The API "can change between releases", and the generated `.d.ts` is authoritative.

**Files:**
- Modify: `docs/superpowers/plans/2026-10-02-foreman-hooks-probes.md` (a `## Mod API binding` section)
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: the binding table Task 13 codes against. This plan's mod code was checked against the declarations Claude Code 2.1.277 published (`mods/types/claude-code.d.ts`) and passes `claude plugin validate` on 2.1.288. Where the installed build's `.d.ts` differs, Task 13 uses the `.d.ts` name.

- [ ] **Step 1: Build a throwaway mod that uses every call Task 13 needs**

It also confirms two static-analysis points: an extra named export (the pure formatter) is accepted, and so is passing `$` from an arrow inside a hook to a top-level function.

```bash
M=$(mktemp -d "${TMPDIR:-/tmp}/foreman-modprobe.XXXXXX")
mkdir -p "$M/.claude-plugin" "$M/hooks"
echo '{ "name": "foreman-modprobe", "version": "0.0.1", "description": "binding probe", "author": { "name": "t" } }' >"$M/.claude-plugin/plugin.json"
echo '{ "modules": ["./probe.mjs"] }' >"$M/hooks/hooks.json"
cat >"$M/hooks/probe.mjs" <<'EOF'
let rows = null

export function formatRows(list) {
  return list.map((r) => String(r))
}

async function tick($) {
  const cwd = await $.session.cwd()
  const sid = await $.session.id()
  const r = await $.process.run(['python3', `${$.plugin.root}/hooks/x.py`, cwd], { cwd, timeoutMs: 10_000 })
  if (await $.fs.exists(`${cwd}/${sid}.json`)) rows = [await $.fs.read(`${cwd}/${sid}.json`)]
  $.ui.status(r.exitCode === 0 ? 'ok' : undefined)
  $.ui.invalidate('ui.render')
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    tick($)
    $.clock.every(60_000, () => { tick($) })
    return next(e)
  })
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (!rows) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    return Box({ flexDirection: 'column', children: [...formatRows(rows).map((l) => Text({ children: [l], wrap: 'truncate-end' })), await next(e)] })
  })
}
EOF
claude plugin validate "$M"
```

Expected (verified on 2.1.288):

```text
  ❯ ./probe.mjs hooks: session.start, ui.render{component=AbovePrompt}
  ❯ ./probe.mjs calls: $.clock.every, $.fs.exists (via tick), $.fs.read (via tick), $.process.run (via tick), $.session.cwd (via tick), $.session.id (via tick), $.ui.invalidate (via tick), $.ui.resolve, $.ui.status (via tick)

✔ Validation passed
```

If validation rejects the extra export, Task 13 moves the pure helpers to `hooks/status-format.mjs` and imports them by relative path. If it rejects the inner-arrow `tick($)`, Task 13 inlines `refresh`'s calls in the timer callback.

- [ ] **Step 2: Generate the declarations for the installed build**

```bash
(cd "$M" && claude -p --model haiku --plugin-dir "$M" "reply ok" >/dev/null)
T=$M/.claude-plugin/types/claude-code/index.d.ts
test -f "$T" || echo "no types written by -p: run 'claude --plugin-dir $M' once interactively, then /exit"
head -1 "$T"
```

- [ ] **Step 3: Check every binding Task 13 uses**

```bash
for k in 'root: string;' 'cwd: () => Promise<string>;' 'id: () => Promise<string>;' \
         'run: (argv: readonly string[], init?: ProcessRunInit) => Promise<ProcessRunResult>;' \
         'exitCode: number;' 'timeoutMs?: number;' 'every: TimerCall;' 'TimerCall = (ms: number, fn: () => void) => Timer;' \
         'exists: (path: string) => Promise<boolean>;' '(path: string): Promise<string>;' \
         'status: (text: string | undefined) => void;' "'AbovePrompt'" "'session.start'"; do
  printf '%-90s %s\n' "$k" "$(grep -c -F -- "$k" "$T")"
done
```

Expected: every count ≥ 1. For a 0, find the member in `$T` (`grep -n -A3 '<name>' "$T"`) and record the installed build's signature.

- [ ] **Step 4: Record the binding and ignore the generated files**

Append to the probes doc:

```markdown
## Mod API binding (Claude Code <first line of the .d.ts>)

| call in hooks/status.mjs | signature in the installed .d.ts |
|---|---|
| `$.plugin.root` | `root: string` |
| `await $.session.cwd()`, `await $.session.id()` | `() => Promise<string>` |
| `await $.process.run(argv, { cwd, timeoutMs })` → `{ exitCode, stdout, stderr }` | `(argv: readonly string[], init?: ProcessRunInit) => Promise<ProcessRunResult>` |
| `$.clock.every(ms, fn)` | `TimerCall = (ms: number, fn: () => void) => Timer` |
| `await $.fs.exists(path)`, `await $.fs.read(path)` | `Promise<boolean>`, `Promise<string>` |
| `$.ui.status(text \| undefined)`, `$.ui.invalidate('ui.render')`, `$.ui.resolve(e)` | as in the .d.ts |
| `on('session.start', …)`, `on('ui.render', { component: 'AbovePrompt' }, …)` | event and render component present |

Static analysis: extra named export <accepted|rejected>; `$` passed from an inner arrow to a top-level function <accepted|rejected>.
```

Loading the plugin itself with `--plugin-dir` (Tasks 13 and 15) makes Claude Code write the same files into this repo. Add them to `.gitignore`:

```gitignore
.claude-plugin/types/
/tsconfig.json
```

- [ ] **Step 5: Commit**

```bash
git add .gitignore docs/superpowers/plans/2026-10-02-foreman-hooks-probes.md
git commit -m "docs: mod API binding against the installed build's types; ignore generated mod types"
```

---

### Task 13: The wave status mod

**Files:**
- Create: `hooks/status.mjs`
- Create: `tests/test_status.mjs`
- Modify: `hooks/hooks.json` (`description`; `"modules": ["./status.mjs"]`)
- Modify: `tests/test_hooks_json.py` (one test)
- Modify: `tests/run.sh` (the `## ui js` section)

**Interfaces:**
- Consumes: `state.py <cwd>` prints the state dir (Task 2); `cfg.py <main> <key>` prints one list item per line, with `DEFAULTS` for `status_pane` (Task 2); the run marker `runs/<session_id>.json` with `touched` (Task 4); the mods API as bound in Task 12.
- Produces: `hooks/status.mjs` exporting `register(on)`, plus the pure helpers `parseWorktrees(porcelain)`, `ciCell(checks, required)`, `toRow(worker, extra, required, bots)`, `formatRows(rows) -> string[]` and `summary(rows) -> string`.
- Behaviour:
  - Shows only while this session's marker is live.
  - Refreshes on `session.start`, then every 60 s.
  - Turned off by `status_pane: false` or `hooks_disabled: [status_pane]`.
  - Rows are worktrees under `<main>/.claude/worktrees/` only.
  - A failed refresh keeps the last good table and adds "foreman status unavailable: <reason>".
  - The mod reads only the first state dir. It runs `state.py` in Claude Code's own environment, which is the environment hook processes inherit, so it sees the same FOREMAN_HOME as `run-start`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_status.mjs`:

```js
import test from 'node:test'
import assert from 'node:assert/strict'
import { parseWorktrees, ciCell, toRow, formatRows, summary } from '../hooks/status.mjs'

const PR = {
  number: 12,
  state: 'OPEN',
  headRefOid: '0123456789abcdef',
  mergeStateStatus: 'CLEAN',
  statusCheckRollup: [
    { __typename: 'CheckRun', name: 'test', status: 'COMPLETED', conclusion: 'SUCCESS' },
    { __typename: 'StatusContext', context: 'lint', state: 'PENDING' },
  ],
}

test('parseWorktrees reads paths and branches', () => {
  const p = 'worktree /r\nHEAD abc\nbranch refs/heads/main\n\nworktree /r/.claude/worktrees/w1\nHEAD def\nbranch refs/heads/feat\n\nworktree /x\nHEAD 1\ndetached\n'
  assert.deepEqual(parseWorktrees(p), [
    { path: '/r', branch: 'main' },
    { path: '/r/.claude/worktrees/w1', branch: 'feat' },
    { path: '/x', branch: null },
  ])
})

test('ciCell: required checks only, failures first, a missing check is pending', () => {
  assert.equal(ciCell(PR.statusCheckRollup, []), '…1')
  assert.equal(ciCell(PR.statusCheckRollup, ['test']), '✓')
  assert.equal(ciCell(PR.statusCheckRollup, ['test', 'e2e']), '…1')
  assert.equal(ciCell([{ name: 'test', conclusion: 'FAILURE' }, { name: 'x', state: 'PENDING' }], []), '✗1')
  assert.equal(ciCell([], []), '—')
})

test('toRow: 9-character SHA, bots seen out of configured, unresolved threads', () => {
  const r = toRow({ name: 'w1', pr: PR }, { threads: 2, authors: ['claude[bot]', 'me'] }, ['test'], ['claude', 'codex'])
  assert.deepEqual(r, { name: 'w1', pr: '#12', state: 'open', sha: '012345678', ci: '✓', bots: '1/2', threads: 2, merge: 'clean' })
  assert.deepEqual(toRow({ name: 'w2', pr: null }, null, [], []), { name: 'w2', pr: '—', state: 'no PR', sha: '', ci: '—', bots: '—', threads: 0, merge: '' })
})

test('formatRows aligns the columns; summary counts PRs and green ones', () => {
  const rows = [
    toRow({ name: 'w1', pr: PR }, { threads: 0, authors: [] }, ['test'], []),
    toRow({ name: 'worker-two', pr: null }, null, [], []),
  ]
  const lines = formatRows(rows)
  assert.equal(lines.length, 2)
  assert.ok(lines[0].startsWith('w1          #12  open'))
  assert.ok(lines[1].startsWith('worker-two  —    no PR'))
  assert.equal(lines[0].indexOf('ci '), lines[1].indexOf('ci '))
  assert.equal(summary(rows), 'foreman: 2 workers · 1 PR · 1 green')
  assert.deepEqual(formatRows([]), [])
})
```

Add to `HooksJsonTest` in `tests/test_hooks_json.py`:

```python
    def test_the_mod_is_declared_and_exists(self):
        self.assertEqual(self.data['modules'], ['./status.mjs'])
        self.assertTrue(os.path.isfile(os.path.join(ROOT, 'hooks', 'status.mjs')))
```

- [ ] **Step 2: Run them and watch them fail**

Run: `node --test tests/test_status.mjs; python3 tests/test_hooks_json.py`
Expected: FAIL with `Cannot find module '…/hooks/status.mjs'` and `KeyError: 'modules'`.

- [ ] **Step 3: Create `hooks/status.mjs`**

```js
// foreman wave status: while this session has a /foreman run marker, a band above the prompt shows one line per
// worker worktree (<main>/.claude/worktrees/*): PR, state, head SHA, CI, bots, unresolved threads, merge state.
// Like every mod it runs in-process and unsandboxed; it only reads (git, gh, foreman's state dir) and draws.

const EVERY_MS = 60_000
const RUN_TTL_S = 48 * 3600
let rows = null // the last good table; null draws nothing
let note = '' // "foreman status unavailable: <reason>" after a failed refresh

const PASS = new Set(['SUCCESS', 'NEUTRAL', 'SKIPPED'])
const FAIL = new Set(['FAILURE', 'CANCELLED', 'TIMED_OUT', 'ACTION_REQUIRED', 'STARTUP_FAILURE', 'ERROR'])

// ---- pure helpers (tests/test_status.mjs) -------------------------------------------------------------

export function parseWorktrees(porcelain) {
  const out = []
  for (const line of porcelain.split('\n')) {
    if (line.startsWith('worktree ')) out.push({ path: line.slice(9), branch: null })
    else if (line.startsWith('branch ') && out.length) out[out.length - 1].branch = line.slice(7).replace(/^refs\/heads\//, '')
  }
  return out
}

export function ciCell(checks, required) {
  const state = (c) => {
    const v = (c.conclusion || c.state || '').toUpperCase()
    return PASS.has(v) ? 'pass' : FAIL.has(v) ? 'fail' : 'pending'
  }
  const named = (c) => c.name || c.context || ''
  const relevant = required.length ? required.map((n) => checks.find((c) => named(c) === n) || {}) : checks
  if (!relevant.length) return '—'
  const s = relevant.map(state)
  const fail = s.filter((x) => x === 'fail').length
  const pending = s.filter((x) => x === 'pending').length
  return fail ? `✗${fail}` : pending ? `…${pending}` : '✓'
}

export function toRow(worker, extra, required, bots) {
  const pr = worker.pr
  const seen = bots.filter((b) => (extra?.authors || []).some((a) => a === b || a === `${b}[bot]`)).length
  return {
    name: worker.name,
    pr: pr ? `#${pr.number}` : '—',
    state: pr ? String(pr.state).toLowerCase() : 'no PR',
    sha: pr ? String(pr.headRefOid || '').slice(0, 9) : '',
    ci: pr ? ciCell(pr.statusCheckRollup || [], required) : '—',
    bots: bots.length ? `${seen}/${bots.length}` : '—',
    threads: extra ? extra.threads : 0,
    merge: pr ? String(pr.mergeStateStatus || '').toLowerCase() : '',
  }
}

export function formatRows(list) {
  const cells = list.map((r) => [r.name, r.pr, r.state, r.sha, `ci ${r.ci}`, `bots ${r.bots}`, `threads ${r.threads}`, r.merge])
  const widths = cells[0] ? cells[0].map((_, i) => Math.max(...cells.map((c) => String(c[i]).length))) : []
  return cells.map((c) => c.map((v, i) => String(v).padEnd(widths[i])).join('  ').trimEnd())
}

export function summary(list) {
  const prs = list.filter((r) => r.pr !== '—').length
  const green = list.filter((r) => r.ci === '✓' && r.merge === 'clean').length
  return `foreman: ${list.length} worker${list.length === 1 ? '' : 's'} · ${prs} PR${prs === 1 ? '' : 's'} · ${green} green`
}

// ---- data (mods API) ------------------------------------------------------------------------------------

async function sh($, argv, cwd) {
  const r = await $.process.run(argv, { cwd, timeoutMs: 20_000 })
  if (r.exitCode !== 0) throw new Error(`${argv.slice(0, 3).join(' ')} exited ${r.exitCode}: ${r.stderr.trim().slice(0, 120)}`)
  return r.stdout
}

async function cfgList($, main, key) {
  const out = await sh($, ['python3', `${$.plugin.root}/scripts/cfg.py`, main, key], main)
  return out.split('\n').map((s) => s.trim()).filter(Boolean)
}

async function threads($, main, numbers) {
  const repo = JSON.parse(await sh($, ['gh', 'repo', 'view', '--json', 'owner,name'], main))
  const fields = 'reviewThreads(first:100){nodes{isResolved}} reviews(last:50){nodes{author{login}}} comments(last:50){nodes{author{login}}}'
  const q = `query($owner:String!,$name:String!){repository(owner:$owner,name:$name){${numbers.map((n) => `p${n}:pullRequest(number:${n}){${fields}}`).join(' ')}}}`
  const data = JSON.parse(await sh($, ['gh', 'api', 'graphql', '-f', `query=${q}`, '-F', `owner=${repo.owner.login}`, '-F', `name=${repo.name}`], main))
  const out = {}
  for (const n of numbers) {
    const p = data.data.repository[`p${n}`] || {}
    const logins = [...(p.reviews?.nodes || []), ...(p.comments?.nodes || [])].map((x) => x.author?.login).filter(Boolean)
    out[n] = { threads: (p.reviewThreads?.nodes || []).filter((t) => !t.isResolved).length, authors: logins }
  }
  return out
}

async function collect($) {
  const cwd = await $.session.cwd()
  const sid = await $.session.id()
  const sd = (await sh($, ['python3', `${$.plugin.root}/scripts/state.py`, cwd], cwd)).trim()
  const marker = `${sd}/runs/${sid}.json`
  if (!(await $.fs.exists(marker))) return null
  const m = JSON.parse(await $.fs.read(marker))
  if (Date.now() / 1000 - Number(m.touched || 0) > RUN_TTL_S) return null
  const wts = parseWorktrees(await sh($, ['git', 'worktree', 'list', '--porcelain'], cwd))
  const main = wts[0]?.path
  if (!main) return null
  if ((await cfgList($, main, 'status_pane'))[0] === 'false') return null
  if ((await cfgList($, main, 'hooks_disabled')).includes('status_pane')) return null
  const required = await cfgList($, main, 'required_ci')
  const bots = await cfgList($, main, 'review_bots')
  const workers = []
  for (const w of wts.filter((x) => x.path.startsWith(`${main}/.claude/worktrees/`))) {
    const prs = w.branch
      ? JSON.parse(await sh($, ['gh', 'pr', 'list', '--head', w.branch, '--state', 'all', '--json', 'number,state,headRefOid,mergeStateStatus,statusCheckRollup'], main))
      : []
    workers.push({ name: w.path.slice(main.length + '/.claude/worktrees/'.length), pr: prs[0] || null })
  }
  const numbers = workers.filter((w) => w.pr).map((w) => w.pr.number)
  const extra = numbers.length ? await threads($, main, numbers) : {}
  return workers.map((w) => toRow(w, w.pr ? extra[w.pr.number] : null, required, bots))
}

async function refresh($) {
  try {
    rows = await collect($)
    note = ''
  } catch (err) {
    note = `foreman status unavailable: ${err?.message || err}`
  }
  $.ui.status(note || (rows ? summary(rows) : undefined))
  $.ui.invalidate('ui.render')
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    refresh($)
    $.clock.every(EVERY_MS, () => {
      refresh($)
    })
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (!rows || !rows.length) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    const lines = formatRows(rows).concat(note ? [note] : [])
    return Box({
      flexDirection: 'column',
      children: [...lines.map((line) => Text({ children: [line], wrap: 'truncate-end' })), await next(e)],
    })
  })
}
```

- [ ] **Step 4: Declare the mod in `hooks/hooks.json`**

Replace the `description` line with these two lines:

```json
  "description": "foreman hooks: gate receipts, worker guard, merge discipline, trailer guard, backlog rows, worker briefing and report check, script allow, spec gate; plus the wave status mod",
  "modules": ["./status.mjs"],
```

- [ ] **Step 5: Run the tests and the validator**

```bash
node --test tests/test_status.mjs && python3 tests/test_hooks_json.py
d=$(mktemp -d) && rsync -a --exclude .git --exclude .claude-plugin/marketplace.json ./ "$d/" && claude plugin validate "$d"; rm -rf "$d"
```

Expected: 4 node tests pass; 3 Python tests pass; the validator prints:

```text
  ❯ ./status.mjs hooks: session.start, ui.render{component=AbovePrompt}
  ❯ ./status.mjs calls: $.clock.every, $.fs.exists (via collect), $.fs.read (via collect), $.process.run (via sh), $.session.cwd (via collect), $.session.id (via collect), $.ui.invalidate (via refresh), $.ui.resolve, $.ui.status (via refresh)

✔ Validation passed
```

In `tests/run.sh`, change the `## ui js` line to run both JS suites:

```bash
if command -v node >/dev/null; then check "test_ui_js.mjs" node --test "$root/tests/test_ui_js.mjs"; check "test_status.mjs" node --test "$root/tests/test_status.mjs"; else echo "SKIP node not installed"; fi
```

Run: `tests/run.sh`
Expected: ends `failed 0`.

- [ ] **Step 6: Commit**

```bash
git add hooks/status.mjs hooks/hooks.json tests/test_status.mjs tests/test_hooks_json.py tests/run.sh
git commit -m "Add the wave status mod (band above the prompt during a /foreman run)"
```

---

### Task 14: SKILL.md, README and template

**Files:**
- Modify: `skills/foreman/SKILL.md`
- Modify: `README.md`
- Modify: `templates/foreman.config.md`
- Modify: `tests/run.sh` (template and SKILL.md checks)

**Interfaces:**
- Consumes: the piece ids (Tasks 5–10, 13); the CLIs `run-end`, `release`, `stopping` and `forget` (Task 4); the `foreman-worker: <batch>[ spec-phase]` tag (Task 8); `cfg.DEFAULTS` (Task 2); the probes doc (Tasks 1, 12).
- Produces:
  - **SKILL.md:** a `### Worker contract` section that is still the text between that heading and the next `#`–`###` heading (piece 6 reads it); the Step 0.5 table rows piece 6 takes defaults from.
  - **README.md:** the README's Hooks section, which W3's permissions preflight points at.
- Constraint: the briefing test (`BriefingTest`, Task 8) reads the real SKILL.md and must stay green.

- [ ] **Step 1: Write the failing checks**

In `tests/run.sh`, after `check "template: empty list" test -z "$(tc required_ci)"`, add:

```bash
check "template: gate_exempt" test "$(tc gate_exempt | tr '\n' ' ')" = "docs/** **/*.md "
check "template: hook lists default empty" test -z "$(tc forbid_trailers)$(tc hooks_disabled)"
check "template: design_spec_dir" test "$(tc design_spec_dir)" = "docs/superpowers/specs"

echo "## SKILL.md wires the hooks"
SK=$root/skills/foreman/SKILL.md
check "W3 tags every worker" grep -q 'FIRST line is exactly `foreman-worker: <batch>`' "$SK"
check "every termination runs run-end" test "$(grep -c 'hooks.py run-end' "$SK")" -ge 4
check "step 12 pins the verified SHA" grep -q 'gh pr merge <n> --squash --match-head-commit' "$SK"
check "stopping before each TaskStop" test "$(grep -c 'hooks.py stopping' "$SK")" -ge 2
check "W5 forgets each batch" grep -q 'hooks.py forget <batch>' "$SK"
check "/foreman ui passes the main checkout" grep -q 'ui.py" --repo "$(git worktree list' "$SK"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `tests/run.sh | grep -E '^FAIL|passed'`
Expected: the six SKILL.md checks FAIL. The template checks pass already, because `cfg.DEFAULTS` supplies the keys; they turn into real checks of the template's own values once Step 5 writes them.

- [ ] **Step 3: Edit `skills/foreman/SKILL.md`**

Each `old` text below occurs exactly once in `skills/foreman/SKILL.md` (checked on this branch). Apply them in order. They make these changes:
- W3 tags every worker prompt and adds the permissions preflight.
- W4.4 and W6 run `stopping` before any TaskStop of a tagged worker.
- W5 gates a non-exempt bookkeeping diff in its worktree, and runs `forget` for each batch.
- The Worker contract states the gate-receipt rule and what the hooks enforce.
- Step 4 runs the gate under `gate-lock.sh` with the exact configured string.
- Step 12 pins `--match-head-commit` and runs from the main checkout.
- Every termination (the autonomous loop's end, W6's stop, a manual pass's step 12) runs `run-end`.
- `/foreman ui` passes the main checkout.
- The Step 0.5 table gains the six new keys.
- A new **Hooks** section goes before **Backlog UI**.

W4.6 already merges with `--match-head-commit` and without `--delete-branch`, so it is unchanged. W3.6 belongs to the spec phase (resolution 2).

SKILL edit 1. Replace:

````markdown
- **Termination.** The loop ends when: no eligible `status: ready` item remains (report "backlog
  empty"); a pass can't reach a green merge (stop, PR left open); or only `risk: high` items remain
  (report them so the human can run each by id).
````

with:

````markdown
- **Termination.** The loop ends when: no eligible `status: ready` item remains (report "backlog
  empty"); a pass can't reach a green merge (stop, PR left open); or only `risk: high` items remain
  (report them so the human can run each by id). However it ends, run `python3 $FOREMAN/hooks.py run-end`
  last (see **Hooks**).
````

SKILL edit 2. Replace:

````markdown
- **W3 Launch.** One `Agent` call per batch in one message (general-purpose, `isolation: "worktree"`,
  named, background). The prompt: the **Worker contract** from this file copied in (a worker cannot read
  this command), plus the batch's rows, owned files, a unique test-data id prefix and the resolved absolute
  `$FOREMAN` path.
````

with:

````markdown
- **W3 Launch.** One `Agent` call per batch in one message (general-purpose, `isolation: "worktree"`,
  named, background). The prompt's FIRST line is exactly `foreman-worker: <batch>` (the hooks brief and
  check tagged workers; see **Hooks**). Then the **Worker contract** from this file copied in (a worker
  cannot read this command; keep the copy even though the hooks also brief it), plus the batch's rows, owned
  files, a unique test-data id prefix and the resolved absolute `$FOREMAN` path. Before launching, check that
  the README's `permissions.allow` entries are in `~/.claude/settings.json`, `.claude/settings.json` or
  `.claude/settings.local.json`, and warn the user when they're missing: a background worker that hits a
  permission prompt it can't show is denied and stalls.
````

SKILL edit 3. Replace:

````markdown
     check) go back to the worker (`SendMessage`; if it cannot be resumed, remove its worktree and launch a
     new worker on the branch), then W4.1 again.
````

with:

````markdown
     check) go back to the worker (`SendMessage`; if it cannot be resumed, run `python3 $FOREMAN/hooks.py stopping
     <batch>` before any TaskStop of it, remove its worktree, and launch a new worker on the branch whose prompt again
     starts `foreman-worker: <batch>`), then W4.1 again.
````

SKILL edit 4. Replace:

````markdown
  the only PR that edits the backlog, the rules files and generated files, built in an isolated worktree (another
  session may be editing the main checkout; never `git add -A` there).
````

with:

````markdown
  the only PR that edits the backlog, the rules files and generated files, built in an isolated worktree (another
  session may be editing the main checkout; never `git add -A` there). When its diff has paths that aren't exempt
  (anything beyond the backlog, the rules files and `gate_exempt`), run `{{worktree_setup}}` in that worktree and
  then `$FOREMAN/gate-lock.sh orchestrator -- '<gate>'` from its top level before pushing: the hooks deny a push
  without a gate receipt.
````

SKILL edit 5. Replace:

````markdown
  docs nits (a Critical/Important finding still blocks).
````

with:

````markdown
  docs nits (a Critical/Important finding still blocks). Then run `python3 $FOREMAN/hooks.py forget <batch>` for each
  of this wave's batches.
````

SKILL edit 6. Replace:

````markdown
  counting queued CI) is stuck: stop it (TaskStop), leave its worktree and PR alone, keep its rows out of
  the next pool, report it, and carry on.
````

with:

````markdown
  counting queued CI) is stuck: run `python3 $FOREMAN/hooks.py stopping <batch>`, then stop it (TaskStop), leave its
  worktree and PR alone, keep its rows out of the next pool, report it, and carry on. When the run stops, run
  `python3 $FOREMAN/hooks.py run-end` last.
````

SKILL edit 7. Replace:

````markdown
  never reach a real external platform, delete throwaway files and fixtures before committing, and run every
  gate under a slot: `$FOREMAN/gate-lock.sh <batch> -- '<gate>'` (never take or clear a slot by hand).
````

with:

````markdown
  never reach a real external platform, delete throwaway files and fixtures before committing, and run every
  gate under a slot, from your checkout's top level, with the configured gate string as ONE argument:
  `$FOREMAN/gate-lock.sh <batch> -- '<gate>'` (never take or clear a slot by hand). That run mints the gate receipt
  a push needs: commit every file the gate changed and list it in your report; don't edit files while a gate runs.
````

SKILL edit 8. Replace:

````markdown
- Never merge. Poll for new bot comments after your last push, and stop pushing once you report.
````

with:

````markdown
- Never merge. Poll for new bot comments after your last push, and stop pushing once you report.
- During a run, hooks enforce part of this contract: a push needs a gate receipt for the pushed tree; no merge,
  no force-push, no rebase, no backlog, rules or config edits; no forbidden trailer lines; and a final report
  without one valid fenced `json` block is sent back once.
````

SKILL edit 9. Replace:

````markdown
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ui.py" --repo "$PWD"
   ```
````

with:

````markdown
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ui.py" --repo "$(git worktree list --porcelain | awk '/^worktree /{print substr($0, 10); exit}')"
   ```
   `--repo` is the main checkout, so every worktree of the repo shares one pending list.
````

SKILL edit 10. Replace:

````markdown
| `shared_resources` | notes every worker must follow: shared database and test-data rules, browser and sign-in rules, env files, VCS quirks in worktrees, gate slots | none |
````

with:

````markdown
| `shared_resources` | notes every worker must follow: shared database and test-data rules, browser and sign-in rules, env files, VCS quirks in worktrees, gate slots | none |
| `forbid_trailers` | lines that must never start a commit, PR or comment message (a `/…/` entry is a regex searched within each line) | none |
| `gate_exempt` | gitignore-style paths a push may change without a gate receipt; `backlog_path` and `rules_glob` always are | `docs/**`, `**/*.md` |
| `worker_agent_types` | subagent types the worker briefing and report check apply to | `general-purpose`, `workflow-subagent` |
| `hooks_disabled` | hook ids to turn off in this repo (see **Hooks**) | none |
| `design_spec_dir` | where a spec-phase worker may edit and commit before its spec is released | `docs/superpowers/specs` |
| `status_pane` | the wave status band above the prompt during a run | `true` |
````

SKILL edit 11. Replace:

````markdown
### 4. Gate
- Run `{{gate}}`. Loop fix → rerun until green.
````

with:

````markdown
### 4. Gate
- Run the gate under a slot, from the checkout's top level, with the configured string as ONE argument:
  `$FOREMAN/gate-lock.sh orchestrator -- '{{gate}}'`. Loop fix → rerun until green. During a run the hooks deny a
  push (`git push`, `gh pr create`, `gt submit`) whose tree has no passing gate receipt, so re-run this after every
  change you push (steps 7, 8 and 10 included).
````

SKILL edit 12. Replace:

````markdown
- `gh pr merge <n> --squash --delete-branch` (the merge flag follows repo policy — use `--merge`/`--rebase` if the project isn't squash-only) → merge-tool "post-merge sync".
````

with:

````markdown
- `gh pr merge <n> --squash --match-head-commit <the head SHA you verified> --delete-branch`, run from the main checkout (the merge flag follows repo policy — use `--merge`/`--rebase` if the project isn't squash-only) → merge-tool "post-merge sync".
````

SKILL edit 13. Replace:

````markdown
in autonomous mode the command loops straight back to **step 1** for the next item (no `/loop` tick needed).
````

with:

````markdown
in autonomous mode the command loops straight back to **step 1** for the next item (no `/loop` tick needed). When the pass ends here (manual mode), run `python3 $FOREMAN/hooks.py run-end`.
````

SKILL edit 14. Replace:

````markdown
## Backlog UI (`/foreman ui`)
````

with:

````markdown
## Hooks

The plugin's settings hooks (`hooks/hooks.json`, served by `$FOREMAN/hooks.py`) enforce parts of this file at the
tool call. They fire in every session where the plugin is enabled, but only in a repo with a foreman config.
Run-only pieces act only while this session's **run marker** exists: a prompt starting `/foreman` (not `ui` or
`init`) writes it, `python3 $FOREMAN/hooks.py run-end` at every termination deletes it, and it expires after 48
hours.

| id | when | what it does |
|---|---|---|
| `gate_before_push` | run | `git push`, `gh pr create` and `gt submit` need a gate receipt for the pushed tree (minted by `gate-lock.sh` with the exact `gate`), unless the branch changes only exempt paths |
| `worker_guard` | run, subagents | no merge, force-push, push to the default branch, rebase, `gt modify` once pushed, or backlog/rules/config edits |
| `merge_discipline` | run | `gh pr merge` needs `--match-head-commit`; `--delete-branch` is refused while another worktree holds the branch; no `gt merge` |
| `trailer_guard` | always | `forbid_trailers` lines are refused in commit, PR and comment messages, and in a merge that would keep them |
| `backlog_check` | always | after an edit of the backlog, its problem rows are reported back |
| `worker_briefing`, `report_check` | tagged workers | a subagent whose prompt starts `foreman-worker: <batch>` is briefed with the Worker contract, and its final report's `json` block is checked once |
| `script_allow` | run | foreman's own scripts run without a permission prompt, in strict shapes only |
| `spec_gate` | run, `spec-phase` workers | edits and commits stay in `design_spec_dir` until `hooks.py release <batch> <sha>` |
| `status_pane` | run | the wave status band above the prompt |

Orchestrator-only CLIs (a worker's call is denied): `python3 $FOREMAN/hooks.py release <batch> <sha>` (exit 3 means
the hooks are inactive, so nothing gates the batch), `stopping <batch>` before any TaskStop of a tagged worker, and
`forget <batch>` at W5. Turn pieces off per repo with `hooks_disabled: [<id>, …]`. The user's `! <command>` is
never checked: it is the override.

## Backlog UI (`/foreman ui`)
````


- [ ] **Step 4: Edit `README.md`**

The README's Hooks section is new; it carries the `permissions.allow` snippet, the limits and the mod's unsandboxed note. The version line ("since 0.4.0") is its heading.

README edit 1. Replace:

````markdown
## Prerequisites (not bundled)
````

with:

````markdown
## Hooks (since 0.4.0)

foreman ships settings hooks (`hooks/hooks.json`, one Python dispatcher: `scripts/hooks.py`) that enforce the worker
contract and the merge rules at the tool call, plus a wave-status mod. They are guardrails against mistakes, not a
security boundary. They fire in every session where the plugin is enabled, but only in a repo with a foreman config;
they fail open on their own errors (the one exception: a push whose diff can't be determined is denied).

Run-only pieces act while the session's **run marker** exists: a prompt starting `/foreman` (or `/loop … /foreman`;
not `ui` or `init`) writes it, the skill runs `hooks.py run-end` when the run ends, `/clear` and logout delete it,
and it expires after 48 hours.

| id | applies | what it does |
|---|---|---|
| `gate_before_push` | run, everyone | `git push`, `gh pr create` and `gt submit` need a gate receipt for the pushed tree. `gate-lock.sh <owner> -- '<gate>'`, run with the exact configured `gate`, mints one. A branch whose diff touches only `gate_exempt`, the backlog and the rules files needs none. |
| `worker_guard` | run, subagents | no merge (`gh pr merge`, `gt merge`, merging `gh api` calls), force-push, push to the default branch, rebase, `gt modify` once pushed, or edits of the backlog, the rules files or the foreman config |
| `merge_discipline` | run, everyone | `gh pr merge` needs `--match-head-commit <sha>`; `--delete-branch` is refused while another worktree holds the branch; no `gt merge` |
| `trailer_guard` | foreman repo | `forbid_trailers` lines are refused in commit, PR and comment messages, and in a `gh pr merge` that would keep them |
| `backlog_check` | foreman repo | after an Edit/Write of the backlog, problem rows (wrong `\|` count, duplicate ids) are reported back |
| `worker_briefing` | tagged workers | a subagent whose prompt starts `foreman-worker: <batch>` gets the Worker contract, rendered |
| `report_check` | tagged workers | a tagged worker's final report must end with a valid `json` block that `bk.py --dry-run` accepts; it is sent back once |
| `script_allow` | run | `gate-lock.sh`, `wt-setup.sh`, `review.sh`, `bk.py` and `hooks.py` run without a permission prompt, in strict shapes only |
| `spec_gate` | run, `spec-phase` workers | edits and commits stay in `design_spec_dir`, and nothing is pushed, until the orchestrator runs `hooks.py release <batch> <sha>` |
| `status_pane` | run | a band above the prompt: one line per worker worktree with its PR, state, head SHA, CI, bots, unresolved threads and merge state |

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
      "Bash(sleep:*)", "Bash(date +%s)",
      "Bash(git add:*)", "Bash(git commit:*)", "Bash(git fetch:*)", "Bash(git merge:*)", "Bash(git push:*)", "Bash(git worktree:*)",
      "Bash(gh pr create:*)", "Bash(gh pr view:*)", "Bash(gh pr checks:*)", "Bash(gh pr comment:*)", "Bash(gh pr edit:*)", "Bash(gh api:*)",
      "Bash(node --check:*)"
    ]
  }
}
```

**Limits.**
- The hooks see only Claude's tool calls, never yours. Writes to the backlog or rules files through Bash (a `python3 -c` that edits them) aren't guarded; `bk.py` validates its own writes.
- Commands fed to a shell's stdin (`echo 'git push' | bash`), `eval` of built strings and encoded commands aren't detected.
- An unquoted `echo git push` is treated as a push (a deliberate false deny).
- A push with an explicit refspec source other than HEAD is judged by HEAD's tree.
- A command the scan can't read (unbalanced quotes, an unterminated heredoc) is judged fail-closed: a push is denied with "couldn't read this command"; push with a plain `git push` in its own call.
- A resumed session whose id changed, or a build whose subagent ids differ between events, runs some checks fail-open. `docs/superpowers/plans/2026-10-02-foreman-hooks-probes.md` records what was observed.

## Prerequisites (not bundled)
````

README edit 2. Replace:

````markdown
Main keys: `backlog_path`, `id_prefix` (row ids, default `IB`), `rules_glob`, `gate`, `extra_gates`,
`post_merge_check`, `merge_tool`, `required_ci`, `review_bots`, `reviewers` (+ `reviewer_timeout_seconds`,
`reviewer_stall_seconds`, `reviewer_max_diff_kb`), `dev_url`, `dev_restart`, `e2e`, `browser_lib`,
`pre_browser_check`, `worktree_env_files`, `worktree_install`, `stale_check`, `gate_slots`, `parallel_*`,
`shared_resources`.
````

with:

````markdown
Main keys: `backlog_path`, `id_prefix` (row ids, default `IB`), `rules_glob`, `gate`, `extra_gates`,
`post_merge_check`, `merge_tool`, `required_ci`, `review_bots`, `reviewers` (+ `reviewer_timeout_seconds`,
`reviewer_stall_seconds`, `reviewer_max_diff_kb`), `dev_url`, `dev_restart`, `e2e`, `browser_lib`,
`pre_browser_check`, `worktree_env_files`, `worktree_install`, `stale_check`, `gate_slots`, `parallel_*`,
`shared_resources`. Hooks: `forbid_trailers`, `gate_exempt`, `worker_agent_types`, `hooks_disabled`,
`design_spec_dir`, `status_pane`.
````

README edit 3. Replace:

````markdown
| `scripts/cfg.py <repo> <key> [default]` | read a config key (no PyYAML needed) |
````

with:

````markdown
| `scripts/cfg.py <repo> <key> [default]` | read a config key (no PyYAML needed) |
| `scripts/hooks.py <event>` | the hooks' dispatcher; orchestrator CLIs `run-end`, `release <batch> <sha>`, `stopping <batch>`, `forget <batch>` (exit 3 = hooks inactive) |
| `scripts/state.py <path>` | print the repo's state dir (`~/.claude/foreman/<repo-key>`, or under `$FOREMAN_HOME`) |
````

README edit 4. Replace:

````markdown
tests/run.sh                       # offline: config parsing, bk.py, worktree setup, gate locks, the chain with fake reviewers
````

with:

````markdown
tests/run.sh                       # offline: config parsing, bk.py, worktree setup, gate locks, the chain with fake reviewers, the hooks, the mod's formatter
tests/e2e-hooks.sh                 # headless end-to-end of the hooks with haiku workers, in a throwaway repo; spends quota
````


- [ ] **Step 5: Document the new keys in `templates/foreman.config.md`**

Template edit 1. Replace:

````markdown
shared_resources: []            # rules every worker follows: shared database, test data, browser sign-in, VCS quirks [none]
---
````

with:

````markdown
shared_resources: []            # rules every worker follows: shared database, test data, browser sign-in, VCS quirks [none]

# --- hooks (README: Hooks) ------------------------------------------------------------------------------
forbid_trailers: []             # lines that must never start a commit, PR or comment message, e.g. [Co-Authored-By, Claude-Session]; an entry written /.../ is a regex searched within each line [none]
gate_exempt: [docs/**, "**/*.md"]   # gitignore-style paths a push may change without a gate receipt; backlog_path and rules_glob always may [docs/**, **/*.md]
worker_agent_types: [general-purpose, workflow-subagent]   # subagent types the briefing and report check apply to [these two]
hooks_disabled: []              # hook ids off in this repo: gate_before_push, worker_guard, merge_discipline, trailer_guard, backlog_check, worker_briefing, report_check, script_allow, spec_gate, status_pane [none]
design_spec_dir: docs/superpowers/specs   # where a spec-phase worker may edit before its spec is released [docs/superpowers/specs]
status_pane: true               # the wave status band above the prompt during a run [true]
---
````


- [ ] **Step 6: Run everything**

Run: `tests/run.sh`
Expected: ends `failed 0`, with the template and SKILL.md checks passing and `test_hooks.py` (`BriefingTest` included) green.

- [ ] **Step 7: Commit**

```bash
git add skills/foreman/SKILL.md README.md templates/foreman.config.md tests/run.sh
git commit -m "Document the hooks: SKILL.md wiring (tags, receipts, run-end, stopping, forget), README Hooks section, template keys"
```

---

### Task 15: Headless end-to-end (controller only)

**Run by the controller:** it drives real `claude -p` sessions (haiku) against this worktree loaded with `--plugin-dir`. The code part, the `FOREMAN_HOOK_LOG` trace, is small and can be implemented first by anyone.

**Files:**
- Modify: `scripts/hooks.py` (imports; `main`; a new `trace`)
- Create: `tests/e2e-hooks.sh`
- Modify: `tests/test_hooks.py` (one class above `if __name__ == '__main__':`)
- Modify: `docs/superpowers/plans/2026-10-02-foreman-hooks-probes.md` (a `## Headless end-to-end` section)

**Interfaces:**
- Consumes: everything from Tasks 4–13; `HANDLERS`, `Hook` and `main` (Task 4).
- Produces: `trace(event, inp, out)`. With `FOREMAN_HOOK_LOG=<file>` set, every decision a hook prints is appended as `{"event", "session_id", "agent_id", "out"}`. That is the end-to-end's evidence, and a debugging aid; unset, nothing changes.

The spec's end-to-end checks, and where each is answered:
- P1–P5 are answered by Task 1. This run re-confirms P1 (a marker after `/foreman`) and P3 (the same marker still counts after `--resume`).
- A worktree subagent's `gh pr merge` is denied.
- A receipt-less push is denied, then allowed after the gate.
- A tagged worker is briefed.
- A worker without a `json` block is blocked once.

- [ ] **Step 1: Write the failing test for the trace**

Add above `if __name__ == '__main__':` in `tests/test_hooks.py`:

```python
class HookLogTest(Case):
    def test_hook_log_records_decisions(self):
        log = os.path.join(self.fx.tmp, 'hooks.log')
        self.fx.env['FOREMAN_HOOK_LOG'] = log
        self.fx.start_run()
        self.fx.bash('git rebase origin/main', agent='a1')
        self.fx.bash('ls')
        with open(log) as f:
            lines = [json.loads(l) for l in f]
        self.assertEqual(len(lines), 1)
        self.assertEqual((lines[0]['event'], lines[0]['agent_id']), ('pretool', 'a1'))
        self.assertIn('no rebase', lines[0]['out']['hookSpecificOutput']['permissionDecisionReason'])


```

Run: `python3 tests/test_hooks.py HookLogTest`
Expected: FAIL with `FileNotFoundError` (no log is written).

- [ ] **Step 2: Add the trace to `scripts/hooks.py`**

Add `import contextlib` and `import io` to the top-level imports. In `main`, replace the `try:` block:

```python
    try:
        _load()
        fn = HANDLERS.get(event)
        if fn:
            fn(Hook(inp))
    except Exception as e:  # fail open
        print(f'foreman hook error: {type(e).__name__}: {e}', file=sys.stderr)
```

with:

```python
    try:
        _load()
        fn = HANDLERS.get(event)
        if fn:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                fn(Hook(inp))
            sys.stdout.write(buf.getvalue())
            trace(event, inp, buf.getvalue())
    except Exception as e:  # fail open
        print(f'foreman hook error: {type(e).__name__}: {e}', file=sys.stderr)


def trace(event, inp, out):
    """With FOREMAN_HOOK_LOG=<file>, append every decision a hook printed (the headless end-to-end reads it)."""
    path = os.environ.get('FOREMAN_HOOK_LOG')
    if path and out.strip():
        with open(path, 'a') as f:
            f.write(json.dumps({'event': event, 'session_id': inp.get('session_id'), 'agent_id': inp.get('agent_id'),
                                'out': json.loads(out)}) + '\n')
```

`trace` is defined after `main` and only looked up when `main` runs, which is after the whole module has loaded.

Run: `python3 tests/test_hooks.py`
Expected: OK (97 tests).

- [ ] **Step 3: Create `tests/e2e-hooks.sh`**

```bash
#!/bin/bash
# Headless end-to-end of the foreman hooks: loads this plugin with --plugin-dir in a throwaway repo with a fake gh,
# and drives the main session and haiku workers with `claude -p`. Spends model quota; not part of tests/run.sh.
#   tests/e2e-hooks.sh          (KEEP=1 keeps the temp dir; every hook decision is in $T/hooks.log)
set -uo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
T=$(mktemp -d "${TMPDIR:-/tmp}/foreman-e2e.XXXXXX")
[ -n "${KEEP:-}" ] || trap 'rm -rf "$T"' EXIT
pass=0; fail=0
ok() { echo "PASS $1"; pass=$((pass + 1)); }
no() { echo "FAIL $1${2:+: $2}"; fail=$((fail + 1)); }
check() { local name=$1; shift; if "$@"; then ok "$name"; else no "$name"; fi; }
git() { /usr/bin/git -c user.name=t -c user.email=t@example.com "$@"; }
logged() { python3 - "$T/hooks.log" "$@" <<'PY'
import json, sys
path, event, needle = sys.argv[1], sys.argv[2], sys.argv[3]
agent = len(sys.argv) > 4
try:
    lines = [json.loads(l) for l in open(path)]
except OSError:
    lines = []
sys.exit(0 if any(d['event'] == event and needle in json.dumps(d['out'], ensure_ascii=False)
                  and (d.get('agent_id') is not None) == agent for d in lines) else 1)
PY
}

export FOREMAN_HOME=$T/home FOREMAN_LOCK_ROOT=$T/locks FOREMAN_HOOK_LOG=$T/hooks.log FAKE_GH_LOG=$T/gh.log
mkdir -p "$T/bin"
cat >"$T/bin/gh" <<'GH'
#!/bin/sh
echo "$@" >>"$FAKE_GH_LOG"
case "$1 $2" in
  "pr view") echo '{"headRefName":"feat","commits":[]}' ;;
  "pr merge") echo "merged (fake gh)" ;;
esac
GH
chmod +x "$T/bin/gh"
export PATH=$T/bin:$PATH

git init -q --bare -b main "$T/origin.git"
git clone -q "$T/origin.git" "$T/repo" 2>/dev/null
cd "$T/repo" && git symbolic-ref HEAD refs/heads/main
mkdir -p .claude
printf -- '---\ngate: echo gated\nforbid_trailers: [Co-Authored-By]\n---\n' >.claude/foreman.config.md
printf '# Backlog\n\n| IB-1 | Shipped already | low | done (#1) | x | s |\n' >.claude/improvement-backlog.md
echo 'print(1)' >app.py
git add -A && git commit -qm init && git push -q -u origin main && git remote set-head origin main
SD=$(python3 "$root/scripts/state.py" "$T/repo")

C=(claude -p --model haiku --plugin-dir "$root" --permission-mode bypassPermissions --output-format stream-json --verbose)
run() { "${C[@]}" "$@" >>"$T/out.jsonl" 2>>"$T/err.log"; }

echo "## a /foreman prompt starts a run (P1)"
run "/foreman"
SID=$(python3 -c "import json; print(next(json.loads(l)['session_id'] for l in open('$T/out.jsonl') if '\"session_id\"' in l))")
check "this session's run marker exists" test -f "$SD/runs/$SID.json"

echo "## a tagged worktree worker: briefed, its merge denied, its report checked"
run --resume "$SID" "Use the Agent tool exactly once, with subagent_type general-purpose and isolation \"worktree\", and this prompt:
foreman-worker: e2e1
Run this exact Bash command once: gh pr merge 1 --squash --match-head-commit abc1234
Then reply with one line saying what happened. Do not include any json block in your reply.
When the agent has returned, reply done."
check "the tagged worker was briefed" logged subagent-start 'You run steps 2-8' agent
check "the briefing recorded the agent map" bash -c "ls '$SD/batches/$SID/agents/' | grep -q ."
check "the worker's gh pr merge was denied" logged pretool 'workers never merge' agent
check "...and gh never ran the merge" bash -c "! grep -q 'pr merge' '$T/gh.log' 2>/dev/null"
check "the worker's report without a json block was sent back once" logged subagent-stop 'fenced json block' agent

echo "## a push without a receipt is denied, then allowed after the gate (P3: same session after --resume)"
git switch -qc feat && echo 'print(2)' >app.py && git commit -qam change
run --resume "$SID" "Run this exact Bash command once and report its output: git push -u origin HEAD"
check "the receipt-less push was denied" logged pretool 'no passing gate for this tree'
check "...and nothing reached origin" bash -c "! git ls-remote --exit-code origin feat >/dev/null 2>&1"
"$root/scripts/gate-lock.sh" e2e -- 'echo gated' >/dev/null 2>&1
run --resume "$SID" "Run this exact Bash command once and report its output: git push -u origin HEAD"
check "after the gate the push went through" git ls-remote --exit-code origin feat

echo
echo "passed $pass, failed $fail   (temp dir: $T; KEEP=1 keeps it)"
[ "$fail" = 0 ]
```

Run: `chmod +x tests/e2e-hooks.sh && bash -n tests/e2e-hooks.sh`

- [ ] **Step 4: Run it**

Run: `claude plugin list` (disable an installed `foreman@foreman` for the run, as in Task 1 Step 1), then `KEEP=1 tests/e2e-hooks.sh`.
Expected: `passed 9, failed 0`.

On a failure, read `$T/hooks.log`, `$T/out.jsonl` and `$T/err.log` before changing anything. Known ways it can fail:
- **The orchestrator ran `hooks.py run-end` after `/foreman`,** so the marker was gone for the later steps. Re-run, or start the run with `/foreman --auto-merge` and an empty backlog.
- **haiku didn't follow a prompt** (no Agent call, or the worker appended a json block anyway). Re-run once. Twice in a row is a real finding: record it.
- **No `subagent-start` line:** P2 failed (Task 1's fallback).
- **No `pretool` line for the worker:** the settings hooks aren't firing for worktree agents. That contradicts the spec's Findings 2; stop and report.

- [ ] **Step 5: Record and commit**

Append to the probes doc:

```markdown
## Headless end-to-end (tests/e2e-hooks.sh, Claude Code <version>)

<the script's PASS/FAIL lines, and for each FAIL the hooks.log line or the missing one>
```

```bash
git add scripts/hooks.py tests/test_hooks.py tests/e2e-hooks.sh docs/superpowers/plans/2026-10-02-foreman-hooks-probes.md
git commit -m "Headless end-to-end for the hooks (tests/e2e-hooks.sh); FOREMAN_HOOK_LOG decision trace"
```

---

### Task 16: Version 0.4.0 and the release check

**Files:**
- Modify: `.claude-plugin/plugin.json` (`"version"`)

**Interfaces:**
- Consumes: everything. The README's "Hooks (since 0.4.0)" heading is from Task 14.
- Produces: the 0.4.0 plugin.

- [ ] **Step 1: Bump the version**

In `.claude-plugin/plugin.json`, change `"version": "0.3.0"` to `"version": "0.4.0"`.

- [ ] **Step 2: Run the whole release check**

```bash
tests/run.sh
claude plugin validate .claude-plugin/plugin.json
d=$(mktemp -d) && rsync -a --exclude .git --exclude .claude-plugin/marketplace.json ./ "$d/" && claude plugin validate "$d"; rm -rf "$d"
grep -n '"version"' .claude-plugin/plugin.json && grep -n 'since 0.4.0' README.md
git status --short
```

Expected:
- `run.sh` ends `failed 0`;
- both validations print `✔ Validation passed`, the second listing `./status.mjs hooks: session.start, ui.render{component=AbovePrompt}`;
- `"version": "0.4.0"` and the README heading are found;
- `git status` shows only `.claude-plugin/plugin.json` (no generated `.claude-plugin/types/` or `tsconfig.json`; Task 12 ignored them).

- [ ] **Step 3: Commit**

```bash
git add .claude-plugin/plugin.json
git commit -m "foreman 0.4.0: hooks"
```

- [ ] **Step 4: Hand the user the one check that needs a real wave**

The spec's mod test ends with "one real wave watched in a terminal". It needs a real project and real workers, so the user decides when to run it. Tell the user, with the exact command: `claude --plugin-dir /Users/crisis/_workspace/foreman.wt/hooks` in the project, then `/foreman parallel 2`. They should watch for the band above the prompt (one line per worker, refreshed every minute), and for the "foreman status unavailable" line if `gh` fails.

---

## Spec test coverage

Every case in the spec's Testing section, and the test that pins it. `T3` is `tests/test_shellscan.py`, `TH` is `tests/test_hooks.py` (class.method), `RS` is `tests/run.sh`.

| spec case | task | test |
|---|---|---|
| shellscan: wrappers (`nohup`, `timeout 60`, `sudo`, `xargs -n1`, `nice -n 5`, `env -i`, `command -p`, `stdbuf`, `caffeinate`, `find … -exec`) | 3 | T3 `DetectTest.test_wrappers_do_not_hide_the_program` |
| `x="$(git push)"`, `` x=`git push` ``, `bash -lc 'git push'`, `bash <<'EOF'⏎git push⏎EOF` | 3 | T3 `test_substitutions_shells_eval_and_shell_heredocs` |
| `gh pr -R o/r merge 1`, `gh api -X PUT repos/o/r/pulls/1/merge` | 3 | T3 `test_gh_repo_flags_and_api` |
| `git -C "my dir" push` | 3 | T3 `test_git_dash_C_with_a_quoted_dir` |
| the Claude Code PR form gives known arguments; each heredoc form with an inner `"two words"` and a footer | 3 | T3 `test_pr_form_gives_known_args_and_its_heredoc`, `test_commit_form_heredoc_belongs_to_the_commit` |
| `git commit -m "fix: git push retry"` is not a push | 3 | T3 `test_quoted_git_push_is_not_a_push` |
| `(cd wt && git push); git push` | 3 | T3 `test_cd_in_a_group_is_scoped` |
| revision-3 forms (comment then newline, `true;(git push)`, `{ git push; }`, `if …; then`, `\|&`, heredoc body with `don't` `+main` `:old`, `/usr/bin/git`, `env -i`, `GIT_DIR=x`, `bash -c`, `eval`, `'a;b'`) | 3 | T3 `test_revision_3_forms`, `test_heredoc_body_is_blanked`, `test_wrappers_do_not_hide_the_program`, `test_substitutions_shells_eval_and_shell_heredocs` |
| `gh api` by method and path; GraphQL merge; subagent file query | 3 | T3 `ClassifyTest.test_gh_api_merge_by_method_and_path` |
| fail-closed fallback for unbalanced quotes | 3 | T3 `test_unbalanced_quote_falls_back_to_unknown_args` |
| scoping: no config silent; `FOREMAN_CONFIG` ignored | 4, 5 | TH `RunMarkerTest.test_no_config_no_marker_and_foreman_config_is_ignored`, `ScopingTest.test_no_config_is_silent_even_with_foreman_config_set` |
| scoping: no marker silences run-only pieces | 5, 6 | TH `ScopingTest.test_no_marker_silences_the_run_only_pieces`, `WorkerGuardTest.test_no_marker_no_guard` |
| scoping: `agent_id` present vs absent | 6 | TH `WorkerGuardTest.test_the_main_session_and_normal_calls_are_not_guarded` |
| piece 1: receipt present, missing | 5 | TH `GateBeforePushTest.test_receipt_present_and_missing` |
| piece 1: exempt-only diff; exempt diff since the last receipt | 5 | TH `test_exempt_only_diff_needs_no_receipt`, `test_exempt_diff_since_the_last_receipt` |
| piece 1: a rename of code into `docs/` | 5 | TH `test_a_rename_of_code_into_docs_is_not_exempt` |
| piece 1: `gate; git push`, `cd wt && git push`, `(git push)`, `/usr/bin/git push` | 5 | TH `test_every_push_form_is_judged` |
| piece 1: a gate that writes `coverage/` | 5 | TH `test_a_gate_that_writes_coverage_still_matches_head` |
| piece 1: delete push allowed; undeterminable diff and unknown args denied | 5 | TH `test_delete_pushes_are_allowed`, `test_undeterminable_diff_is_denied`, `test_unknown_arguments_are_denied` |
| piece 1: minting (gate string vs multi-arg vs `--name` vs non-gate) | 5 | TH `ReceiptMintTest.test_only_the_configured_gate_as_one_argument_mints` |
| piece 1: receipt tree with a force-added ignored file; with a CRLF blob under `text=auto` | 5 | TH `test_receipt_tree_keeps_a_force_added_ignored_file`, `test_receipt_tree_with_a_crlf_blob_under_text_auto` |
| piece 1: a gate run from a subdirectory runs at the top level | 5 | TH `test_a_gate_run_from_a_subdirectory_runs_at_the_top_level` |
| pieces 2 and 3: each denial; a normal push and merge allowed | 6 | TH `WorkerGuardTest.test_subagent_denials`, `test_gt_modify_only_once_the_branch_has_an_upstream`, `test_bookkeeping_edits`, `test_the_main_session_and_normal_calls_are_not_guarded`, `MergeDisciplineTest.test_merge_needs_match_head_commit` |
| pieces 2 and 3: `-d` with only the own worktree vs another worktree | 6 | TH `test_delete_branch_when_only_the_own_worktree_holds_it`, `test_delete_branch_when_another_worktree_holds_it` |
| piece 3: `gt merge` denied during a run | 6 | TH `test_gt_merge_is_denied_during_a_run` |
| piece 4: trailer in `-m`, in a `-F` scratchpad file, in a heredoc; the `🤖 Generated with` footer; a mid-sentence mention | 7 | TH `TrailerGuardTest.test_trailer_in_a_message_option`, `…_file_in_the_scratchpad`, `test_trailer_in_a_heredoc`, `test_generated_with_footer_in_a_pr_body`, `test_mid_sentence_mention_is_allowed` |
| piece 4: squash, merge and rebase whose commits carry a trailer | 7 | TH `test_merges_whose_commits_carry_a_trailer` |
| piece 5: bookkeeping-worktree edit checked; clean backlog nothing | 7 | TH `BacklogCheckTest.test_a_bookkeeping_worktree_edit_is_checked`, `test_clean_backlog_returns_nothing` |
| pieces 6 and 7: briefing with and without the tag; rendered `$FOREMAN` | 8 | TH `BriefingTest.test_a_tagged_worker_is_briefed_with_the_rendered_contract`, `test_no_tag_no_briefing` |
| piece 7: missing block, invalid JSON, wrong shape, refused block, valid block, `rule_notes` ignored, `stop_hook_active`, `SPEC READY` after a preface | 8 | TH `ReportCheckTest.test_missing_block`, `test_invalid_json`, `test_wrong_shape`, `test_a_block_bookkeeping_would_refuse`, `test_a_valid_block_and_ignored_rule_notes`, `test_stop_hook_active_and_untagged_workers_pass`, `test_spec_phase_stops` |
| piece 8: exact path allowed | 9 | TH `ScriptAllowTest.test_exact_paths_are_allowed` |
| piece 8: no decision for newline, `$(`, `; rm`, `BASH_ENV=x bash`, `FOREMAN_CONFIG=x`, `env`, `>` | 9 | TH `test_no_decision` |
| piece 8: one-paren wrap with `--spec`; `post_merge_check` (no receipt); `~/` path; `hooks.py run-end`; `release w1a <sha>` | 9 | TH `test_one_paren_wrap_with_cd_and_review_spec`, `test_post_merge_check_is_allowed_and_mints_nothing`, `test_a_home_relative_plugin_path`, `test_hooks_py_for_the_main_session_only` |
| piece 8: no decision for a non-gate command, `--name`, non-worktree dir, bk.py without `--dry-run` from a subagent | 9 | TH `test_no_decision_for_wrong_arguments` |
| run marker: `/loop 2h /foreman`; mid-prompt mention; `ui`/`init` | 4 | TH `RunMarkerTest.test_prompts_that_start_a_run`, `test_prompts_that_do_not`, `test_user_prompt_expansion` |
| run marker: SessionEnd reasons; run-end deletes only the caller's marker; 48-hour expiry | 4 | TH `test_session_end_reasons`, `test_run_end_deletes_only_the_calling_sessions_marker`, `test_48_hour_expiry` |
| revision 6: `git rm` after the gate; `commit -am` leaving out a new file; a modified `worktree_env_files` path; a second gate run | 5 | TH `ReceiptExactnessTest.test_git_rm_after_the_gate_is_denied`, `test_commit_am_leaving_out_a_new_file_is_denied`, `test_a_modified_worktree_env_file_passes`, `test_a_second_gate_run_does_not_sweep_the_first_runs_artifact` |
| revision 6: `.stopped` lets the stopped worker through, not a later replacement | 8 | TH `ReportCheckTest.test_stopping_lets_the_stopped_worker_through_but_not_a_later_replacement` |
| revision 6: `forget` removes the batch's files; `release` exits 3 with no fresh marker | 4 | TH `ControlTest.test_stopping_and_forget`, `test_release_exits_3_without_a_fresh_marker` |
| revision 6: piece 8 allows `stopping` and `forget` for the main session | 9 | TH `ScriptAllowTest.test_hooks_py_for_the_main_session_only` |
| piece 10: Edit outside vs inside `design_spec_dir` | 10 | TH `SpecGateTest.test_edits_outside_the_spec_dir_are_denied` |
| piece 10: commit with a staged code file; only the spec staged; `commit -a` | 10 | TH `test_commits_are_judged_by_what_they_would_include` |
| piece 10: `git push` and `gh pr create` before release; after release everything passes | 10 | TH `test_pushes_are_denied_before_release`, `test_after_release_everything_passes` |
| piece 10: an unmapped `agent_id` passes; the main session always passes | 10 | TH `test_unmapped_non_spec_and_main_session_pass` |
| the mod's pure formatter | 13 | `tests/test_status.mjs` |
| headless end-to-end (P1–P5 answered; worktree subagent's merge denied; receipt-less push denied then allowed; tagged worker briefed; json-less worker blocked once) | 1, 15 | probes doc; `tests/e2e-hooks.sh` |
| the mod: `claude plugin validate`, plus one real wave watched in a terminal | 13, 16 | Task 13 Step 5; Task 16 Step 4 (the user) |
| config from the main checkout (wt-setup, review.sh), not a planted copy | 9 | RS "wt-setup runs the main checkout's install", "review.sh runs the main checkout's chain" |
