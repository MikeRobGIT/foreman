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
