# Row dependencies and a deterministic picker — design

Foreman picks the topmost `ready` row by eye (SKILL.md step 1, W2). Row order is the only ordering, and nothing says "row B needs row A first", so a wave can start a row whose prerequisite is still open. This adds a dependency marker, a picker that is code, and checks on the write paths. Concept from claude-task-master's dependency-aware next-task; no code or text was copied.

## Goals and non-goals

- A row can say which rows must be `done` first, and the picker never offers it before then.
- Picking is a CLI whose output is tested, not a table the model reads.
- Existing backlogs keep working unchanged: no marker, no behaviour change.
- Non-goals: priorities, a complexity score, a seventh column, tags, subtasks, and any change to merge rules.

## Why not a column

The row is six cells, and `backlog.py` (about ten places), `bk.py`, `hooks.py` (`backlog_problems`), `ui.py`, SKILL.md and the template header all count six cells and five ` | ` separators. A seventh column breaks every existing backlog. The marker lives inside the `acceptance` cell, which `ui/js/md.js` already renders for `**Depends on…**`.

## Grammar

In the `acceptance` cell: `**Depends on:** IB-12, IB-14` using the configured `id_prefix`.

- Regex: `\*\*Depends on:\*\* (P-\d+(?:, P-\d+)*)` where `P` is the escaped prefix. The list is ids joined by `, ` and ends at the first thing that isn't one (a `;`, a `.`, the next `**` or the end of the cell).
- Prose such as "depends on IB-3 being merged" is ignored: only the bold marker counts.
- More than one marker in a row is a problem (`two Depends on markers`).
- A marker whose text holds no valid id (a `NEW-n` or `P-NEW-x` placeholder, a typo) is a problem, `Depends on names no valid ids`, and the row waits. Dropping it silently would let the row run before its prerequisite.

## Eligibility

`eligible(bl, tag, no_high, done)` returns `(ids, waiting)`, both in file order.

A row is a candidate when its status is `ready`, its title carries `[tag]` when a tag was asked for, and it is not `risk: high` when `no_high` is set. A candidate is **eligible** when every dependency is satisfied; otherwise it **waits**, with the reasons.

| the dependency is | outcome |
|---|---|
| `done`, or its id is in `--done` | satisfied |
| `ready`, `in-progress`, `deferred`, `blocked` | waits (`not done`) |
| `cancelled` | waits, problem (`cancelled`) |
| not in the backlog | waits, problem (`missing`) |
| the row itself | waits, problem (`self`) |
| on a cycle | every row on it waits, problem (`cycle`) |
| `risk: high` and still `ready` | waits, named human-only |

`--done` exists because in parallel mode the next wave starts before W5's bookkeeping PR merges, so rows merged in this run still read `ready` on disk.

A row named by id on the command line runs anyway. SKILL.md step 1 echoes its unmet dependencies, and manual mode asks first.

"In an open PR" and "in the same wave" are not computed here. They were `ready` rows the orchestrator already tracks, and it passes them through the existing `ready`-vs-`done` distinction plus `--done`. The picker stays a pure function of the file.

## API (`scripts/backlog.py`)

- `deps(row, prefix) -> list[str]`: ids in the row's marker, in order, `[]` for none.
- `dep_problems(bl) -> list[str]`: `<id>: <problem>` for every row, in file order. Used by every write path.
- `eligible(bl, tag, no_high, done) -> (ids, waiting)`, where `waiting` is `[(id, [reason, ...])]`.
- CLI: `backlog.py ready <repo> [--tag x] [--no-high] [--done id,id] [--limit n] [--json]`. It reads `backlog_path` and `id_prefix` from the config (as `bk.py` does), prints one eligible id per line (or JSON), and prints `waiting:` lines on stderr. Exit 0 with ids, 1 with none (so a script can tell "backlog empty" from "all waiting"), 2 on a usage or file error.

## Plug-in points

- **SKILL.md step 1 and W2** call `backlog.py ready` instead of scanning the table. W2 passes `--done` with the rows merged this run, then keeps its own model-side filters (`parallel_skip`, Procedure rows, rows named by an open PR).
- **Termination report** says "no eligible rows; N waiting: …" when rows wait, never "backlog empty" while any wait.
- **`backlog_check` hook** (PostToolUse on the backlog) warns, never denies, when a row has just turned `done` while a dependency isn't, and reports `dep_problems`.
- **`script_allow`** runs `backlog.py ready` without a permission prompt, in this exact shape only.
- **`bk.py`** refuses a report only for dependency problems it introduces: it computes `dep_problems` before and after its edits and fails on the difference. One hand-edited bad row must not block W5 bookkeeping for code that already merged.
- **`ui.py` publish** runs the same before/after comparison on the replayed ops and shows a new problem as a conflict, never applying it silently.
- **`/foreman seed`** emits `**Depends on:**` for rows it orders foundation-first, and in `append` mode skips a drafted row whose title matches an existing row.

## Failure modes

- **Cycle:** all its rows wait and are reported; nothing loops.
- **Stale origin:** the picker reads the file step 0 synced, so a dependency merged since then reads `ready` and the row waits one more pass. Safe, never early.
- **Pending UI edits:** they count once their PR merges, because the picker reads the file.
- **Malformed row:** an `Unparsed` line has no cells, so it is neither a candidate nor a satisfied dependency (`missing`).
- **No marker anywhere:** `eligible` returns every `ready` row in file order, exactly today's behaviour.

## Tests

`tests/test_backlog.py`: grammar (prose ignored, two markers, unresolved placeholder), each outcome in the table, cycle, self, `--done`, tag and `no_high` filters, no-marker equivalence with `ready_indexes`. `tests/test_hooks.py`: the warning, and `script_allow` accepting only the exact `ready` shape. `tests/run.sh`: `bk.py` refuses a new cycle and writes nothing, and ignores an existing one; CLI exit codes. All synthetic fixtures: no real `tasks.json`.

## Open questions

None. Priority and complexity are out of scope; row order stays the priority.
