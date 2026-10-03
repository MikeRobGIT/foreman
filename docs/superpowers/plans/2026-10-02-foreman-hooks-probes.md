# Foreman hooks: probe results (2026-10-02, Claude Code 2.1.288)

Probes ran in a throwaway repo, with a settings-level hook logging every input it received, using `claude -p --model haiku`. The plugin was loaded with `--plugin-dir` from this worktree; foreman is not installed, so no other copy could answer `/foreman`.

| # | question | observed | verdict | consequence |
|---|---|---|---|---|
| P1 | `/foreman` text in UserPromptSubmit / UserPromptExpansion | `UserPromptSubmit.prompt` = the typed text (`/foreman ui`, or `/foreman:foreman ui`). `UserPromptExpansion` fires with `command_name: "foreman:foreman"` (plugin-namespaced, also when `/foreman ui` was typed), `command_args: "ui"`, `prompt: "/foreman:foreman ui"`, `expansion_type`, `command_source`. | **pass.** Both events carry it, and the expansion has the arguments as a separate field. | Task 4 `run_args`: read `command_name` and `command_args` from UserPromptExpansion; read UserPromptSubmit's prompt text as before. |
| P2 | SubagentStart carries the prompt or a readable transcript | `SubagentStart` keys: `agent_id, agent_type, cwd, hook_event_name, prompt_id, session_id, transcript_path` (the MAIN session's). No `prompt`. The worker's transcript path is derivable as `<transcript_path minus .jsonl>/subagents/agent-<agent_id>.jsonl`, and its first `type: "user"` entry starts with the tag line. **At SubagentStart that file does not exist yet** (re-probed: `exists=False`); by the worker's first PreToolUse it does. | **partial.** No tag at start; the tag is readable from the first PreToolUse on. | See Ruling P2 below: the agent map and the briefing move to the worker's first PreToolUse. |
| P3 | resumed session keeps its session_id | `--resume <sid>`: every hook reports the original `session_id`. | **pass** | The marker survives a resume. |
| P4 | shell-profile FOREMAN_HOME reaches hooks | The Bash tool printed `FH=<P>/fh` (from the profile). Hooks saw `FOREMAN_HOME = None`. | **differs** | Fallback stands: hooks check both state dirs, and `pretool` writes `release`/`stopping`/`forget` from its own hook input. Receipts are minted into every state dir (Departure 2). |
| P5 | SessionEnd reason values | `-p` sessions end with `reason: "other"`. `clear` and `logout` can't be driven headlessly: not observed. | **as documented** | Task 4 `session_end`: delete the marker only for `clear`/`logout`; `other`/`resume`/`prompt_input_exit` keep it. |
| P6 | SubagentStart agent_id == PreToolUse agent_id | Same id in SubagentStart, PreToolUse, PostToolUse and SubagentStop for each worker. An `isolation: worktree` worker's `cwd` is `<repo>/.claude/worktrees/agent-<agent_id>` in all of them. | **pass** | The map keyed by `agent_id` works. |
| P7 | TaskStop fires SubagentStop | A background worker started, ran `sleep 120`, and was stopped with TaskStop. No SubagentStop was logged for it. | **no (in `-p`)** | Nothing to change: piece 7 never sees a stopped worker's stop. `stopping` before TaskStop stays as harmless insurance. |
| P8 | PostToolUse fires on a non-zero Bash exit | `false` produced `PostToolUseFailure`, not `PostToolUse`. | **no** | None: `hooks.py run-end` always exits 0, so its PostToolUse fires. |

## Ruling P2 (controller)

The spec reads the tag "from the input or the transcript (P2)". The plan's "P2 fails" fallback would leave piece 10, the spec gate the user approved explicitly, failing open for every worker. The probe shows a narrower result: the tag can't be read at SubagentStart, but it can be read at the worker's first tool call. So:

- **Agent map (Tasks 8 and 10):** written lazily. On any PreToolUse with an `agent_id` that has no map entry, `pretool` reads the derived worker transcript, takes its first `type: "user"` entry's first line, matches the tag grammar, and writes the map entry atomically. It writes a negative entry (`{"batch": null}`) when there is no tag, so the transcript is read once per agent. `SubagentStart` still tries first (a future Claude Code may write the transcript earlier).
- **Briefing (Task 8):** delivered as `additionalContext` on the tagged worker's FIRST PreToolUse (once per agent, recorded in the map), not at SubagentStart. The tool call itself proceeds unless another piece denies it, and the deny reason and the briefing are then both returned. W3 keeps the contract in the dispatch prompt, as already resolved.
- **Piece 10** consults the map after this lazy fill, so a tagged spec-phase worker is gated from its first edit.
- **Piece 7** reads the tag from `agent_transcript_path` at SubagentStop, unchanged.

## Exact input field names

- **UserPromptSubmit:** `session_id, prompt_id, cwd, permission_mode, hook_event_name, prompt, transcript_path`
- **UserPromptExpansion:** `session_id, prompt_id, cwd, permission_mode, hook_event_name, prompt, command_name, command_args, command_source, expansion_type, transcript_path`
- **SubagentStart:** `session_id, prompt_id, cwd, hook_event_name, agent_id, agent_type, transcript_path`
- **PreToolUse (subagent):** `session_id, prompt_id, cwd, permission_mode, hook_event_name, agent_id, agent_type, tool_name, tool_input, tool_use_id, transcript_path`
- **PostToolUse:** the PreToolUse fields plus `tool_response, duration_ms`
- **SubagentStop:** `session_id, prompt_id, cwd, hook_event_name, agent_id, agent_type, agent_transcript_path, stop_hook_active, last_assistant_message, transcript_path`
- **SessionEnd:** `session_id, prompt_id, cwd, hook_event_name, reason, transcript_path`

### Redacted sample (UserPromptExpansion)

```json
{"session_id": "71499e09-…", "cwd": "<probe repo>", "hook_event_name": "UserPromptExpansion",
 "prompt": "/foreman:foreman ui", "command_name": "foreman:foreman", "command_args": "ui",
 "command_source": "…", "expansion_type": "…", "permission_mode": "…", "prompt_id": "…"}
```

## Mod API binding (Claude Code 2.1.288: `// Written by Claude Code 2.1.288.`)

A throwaway mod making every call Task 13 needs passed `claude plugin validate`, and a `claude -p --plugin-dir` session generated `.claude-plugin/types/claude-code/index.d.ts` (562 KiB). Each signature below was matched in that file.

| call in hooks/status.mjs | signature in the installed .d.ts |
|---|---|
| `$.plugin.root` | `root: string` |
| `await $.session.cwd()`, `await $.session.id()` | `() => Promise<string>` |
| `await $.process.run(argv, { cwd, timeoutMs })` → `{ exitCode, stdout, stderr }` | `(argv: readonly string[], init?: ProcessRunInit) => Promise<ProcessRunResult>`; `timeoutMs?: number`; result `exitCode: number`, `stdout: string`, `stderr: string` (each capped at 4 MiB) |
| `$.clock.every(ms, fn)` | `every: TimerCall`, `TimerCall = (ms: number, fn: () => void) => Timer` |
| `await $.fs.exists(path)`, `await $.fs.read(path)` | `(path: string) => Promise<boolean>`, `(path: string): Promise<string>` |
| `$.ui.status(text \| undefined)` | `(text: string \| undefined) => void` |
| `$.ui.invalidate('ui.render')` | `(event: InvalidatableEventName) => void`; `RenderEventName = 'ui.render'` is a member |
| `$.ui.resolve(e)` → `{ Box, Text }` | `<E extends ResolveInput>(e: E) => Elements[E['surface']]`; every surface has `Box: ElementConstructor<BoxProps>` and `Text: ElementConstructor<TextProps>` |
| `Box({ flexDirection: 'column', children })`, `Text({ children, wrap: 'truncate-end' })` | `ElementConstructor<P> = (props: P & ElementChildren) => RenderElement`; `children?: RenderChildren`; `BoxProps.flexDirection?: 'row' \| 'column' \| …`; `TextProps.wrap?: 'wrap' \| … \| 'truncate-end'` |
| `on('session.start', …)`, `on('ui.render', { component: 'AbovePrompt' }, …)` | event and render component present |

Static analysis: extra named export accepted; `$` passed from an inner arrow to a top-level function accepted (validate lists those calls as `(via tick)`).

## Headless end-to-end (tests/e2e-hooks.sh, Claude Code 2.1.288)

Run 2, `passed 10, failed 0`:

```text
PASS this session's run marker exists
PASS the tagged worker was briefed on its first allowed call
PASS the briefing recorded the agent map
PASS the worker's gh pr merge was denied
PASS ...and gh never ran the merge
PASS the worker's report without a json block was sent back once
PASS the worker saw the briefing (quoted its first line back)
PASS the receipt-less push was denied
PASS ...and nothing reached origin
PASS after the gate the push went through
```

- **Run 1 passed 9 of 10.** The one failure was the check's needle, not a hook. The worker quoted the contract's first content line ("You run steps 2-8 on your batch in your own worktree, with these overrides:"), not the `# foreman worker briefing` heading. Both strings exist only in the injected briefing, and its transcript has a `hook_additional_context` attachment. The check now accepts either.
- **The marker survives `/foreman`.** The script stops the skill with `--max-turns 2`. Otherwise, on an empty backlog, SKILL.md ends the run with `hooks.py run-end` before the later steps.
- **The briefing arrives on the first ALLOWED call.** A denied call carries none (Ruling P2). The worker therefore runs `echo e2e-start` before the denied `gh pr merge`.
- **The report check, live:** the json-less final report was blocked once, and the worker then appended its block.
- **PermissionRequest from a subagent (controller probe, `--permission-mode default`):**
  - It carries `agent_id`; both `permission` lines in the trace had the worker's id.
  - `script_allow` allowed the worker's `gate-lock.sh` and `wt-setup.sh` calls.
  - A plain `git status` was denied, as `-p` default mode does for an unlisted command.
  - So piece 8's "hooks.py only for the main session" rule can rely on `agent_id` here as well as on piece 2's PreToolUse deny.
