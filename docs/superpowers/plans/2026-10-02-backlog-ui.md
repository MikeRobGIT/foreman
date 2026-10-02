# Backlog UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/foreman ui` opens a local, Ledger-styled web UI that lets a user triage, search, edit and track the backlog, and publishes their edits as one PR that never touches their checkout.

**Architecture:**
- **`scripts/backlog.py`:** a pure-Python parser/serialiser that leaves untouched lines byte-identical, plus an operations engine (`set`, `status`, `move`, `add`) with base-line conflict detection. `bk.py` reuses its row pattern and line checks.
- **`scripts/ui.py`:** a standard-library `ThreadingHTTPServer` on 127.0.0.1 with a token cookie and a Host check. It reads the backlog from `origin/<default branch>`, keeps pending operations in `~/.claude/foreman/<repo-key>/pending.json`, and publishes by replaying them in a temporary worktree, committing, pushing and running `gh pr create`.
- **`ui/`:** a static page (vanilla ES modules, no build step) in the approved Ledger style with Auto/Light/Dark themes.

**Tech Stack:**
- Python 3.9+ standard library only: `http.server`, `subprocess`, `json`, `unittest`;
- `git`, and `gh` (optional, for publish and progress);
- vanilla JS ES modules and CSS custom properties;
- Node 20+ only for the `node --test` unit tests of the pure JS helpers.

**Spec:** `docs/superpowers/specs/2026-10-02-backlog-ui-design.md` (read it first). Approved mockups: `.superpowers/brainstorm/70201-1790972370/content/ledger-dark-v5.html` (Queue) and `screens-v8.html` (All rows, Triage, Progress, Edit, Publish).

## Global Constraints

- **No third-party dependencies.** No pip installs, no npm packages, no build step. Fonts load from Google Fonts with system fallbacks.
- **Untouched lines stay byte-identical:** `serialise(parse(t)) == t` for any backlog text.
- **Never read or write the user's working tree or current branch.** Data comes from `git show origin/<branch>:<backlog_path>`. Publish uses a temporary worktree.
- **The server binds to `127.0.0.1` only.** Every `/api/*` and `/ui/*` request needs the token (cookie `foreman_t` or header `X-Foreman-Token`). The `Host` header must be `127.0.0.1:<port>` or `localhost:<port>`; anything else gets 403.
- **Commits and PRs carry no trailers:** no `Co-Authored-By`, no `Claude-Session`, no "Generated with".
- **The backlog path and id prefix come from `cfg.get(repo, 'backlog_path', '.claude/improvement-backlog.md')` and `cfg.get(repo, 'id_prefix', 'IB')`.**
- **Theme tokens and fonts:** Ledger, with serif titles (Source Serif 4), Public Sans body and IBM Plex Mono for ids. Auto follows `prefers-color-scheme`; Light and Dark are pinned and stored in `localStorage['foreman-theme']`.
- **Valid statuses written by the UI:** `ready`, `deferred`, `blocked`, `cancelled` (plus the existing `done` and `in-progress` when only other fields change). Risks: `low`, `medium`, `high`. A reason is required for `blocked` and `cancelled`.
- **Plugin version becomes `0.3.0`.**

### Refinement of the spec's pending-store API

The spec lists `PUT /api/pending`. This plan replaces it with `POST /api/ops` (the server fills `base`, so the client never has to compute it), `POST /api/ops/undo`, `DELETE /api/ops/<i>` and `POST /api/ops/<i>/rebase` (the spec's "Re-apply to new text"). The store and its semantics are unchanged.

## Review Focus

1. **A row edited in the UI after a bookkeeping PR changed it on main:**
   - Expected: the op shows as a conflict, it is never silently applied to the new text, and Publish keeps it pending.
   - Pinned by: `test_conflict_on_changed_base` (Task 3) and `test_publish_keeps_conflicts` (Task 6).
2. **Text with `|`, backticks, `<script>` or a newline in a title, acceptance or reason:**
   - Expected: pipes are escaped as `\|` and newlines refused, so the table never shifts columns.
   - Expected: HTML is escaped on screen.
   - Pinned by: `test_escape_pipes_and_reject_newlines` (Task 2) and `notes escape html` (Task 7).
3. **The user's checkout is dirty or on another branch while they publish:**
   - Expected: the working tree and branch stay byte-for-byte unchanged.
   - Pinned by: `test_publish_leaves_checkout_untouched` (Task 6).
4. **A request from another origin or host** (a page in another tab, DNS rebinding):
   - Expected: 403 without the token, and 403 with a foreign Host even when the token is right.
   - Pinned by: `test_rejects_missing_token` and `test_rejects_bad_host` (Task 4).
5. **Publish fails halfway** (push rejected, or `gh` missing or failing):
   - Expected: the worktree and local branch are removed, the remote branch is deleted if it was pushed, and the pending ops are kept.
   - Pinned by: `test_publish_failure_cleans_up` (Task 6).

---

## File Structure

```
scripts/backlog.py          NEW  parse / serialise / validate / apply ops / assign ids
scripts/bk.py               MOD  row pattern + new-row line checks come from backlog.py
scripts/ui.py               NEW  Repo (git data, pending store, blame, progress, publish) + HTTP server
ui/index.html               NEW  page shell, dialogs, theme pre-paint script
ui/app.css                  NEW  Ledger tokens (light/dark) + layout for every screen
ui/js/api.js                NEW  fetch wrapper
ui/js/md.js                 NEW  escape, inline markdown, notes splitting, tags, PR (pure, node-tested)
ui/js/state.js              NEW  store + selectors (pure, node-tested)
ui/js/actions.js            NEW  op helpers that call the API and update the store
ui/js/dialogs.js            NEW  reason prompt, palette, help overlay
ui/js/main.js               NEW  boot, header, stats, pending bar, tabs, keyboard
ui/js/views/queue.js        NEW  Queue screen + shared detail renderer
ui/js/views/rows.js         NEW  All rows screen
ui/js/views/triage.js       NEW  Triage screen
ui/js/views/progress.js     NEW  Progress screen
ui/js/views/edit.js         NEW  Edit / New row form
ui/js/views/publish.js      NEW  Publish screen
tests/test_backlog.py       NEW
tests/test_ui_api.py        NEW
tests/test_ui_js.mjs        NEW
tests/run.sh                MOD  run the three new suites
skills/foreman/SKILL.md     MOD  `ui` / `ui stop` subcommand
README.md                   MOD  "Backlog UI" section
.claude-plugin/plugin.json  MOD  version 0.3.0
```

Work on a branch: `git -C ~/_workspace/foreman switch -c feat/backlog-ui`.

---

### Task 1: Parser and serialiser (`backlog.py` part 1)

**Files:**
- Create: `scripts/backlog.py`
- Test: `tests/test_backlog.py`

**Interfaces:**
- Produces:
  - `backlog.Row(raw: str, cells: list[str])`, with attributes `raw, id, title, risk, status_text, acceptance, source`, the property `status` (the first word, e.g. `done` for `done (#651)`), `cells() -> list[str]` and `as_dict() -> dict`;
  - `backlog.Unparsed(str)`;
  - `backlog.Backlog(lines, prefix)`, with `.lines` (`str | Unparsed | Row`), `.prefix`, `rows() -> list[Row]`, `find(id) -> int | None`, `ready_indexes() -> list[int]`;
  - `row_pattern(prefix) -> re.Pattern`, `split_cells(line) -> list[str] | None`, `parse(text, prefix='IB') -> Backlog`, `serialise(bl) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backlog.py
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
import backlog  # noqa: E402

FIXTURE = '''# Improvement Backlog

- **status:** `ready` · `done`

## Low risk (ready)

| IB-1 | First row with `code` | low | ready | Accept when: a \\| b works. | #10 review |
| IB-2 | Second | medium | done (#12) | Did it. **DONE (#12):** shipped. | #11 |
| IB-3 | Broken row missing cells | low | ready |
| IB-4 | Fourth | high | blocked — waits on Meta | Needs approval. | IB-1 |
| IB-5 | Fifth | low | ready | Text. | #13 |
'''


class ParseTest(unittest.TestCase):
    def test_round_trip_is_byte_identical(self):
        self.assertEqual(backlog.serialise(backlog.parse(FIXTURE)), FIXTURE)

    def test_round_trip_without_trailing_newline(self):
        t = FIXTURE.rstrip('\n')
        self.assertEqual(backlog.serialise(backlog.parse(t)), t)

    def test_fields_and_status(self):
        bl = backlog.parse(FIXTURE)
        rows = {r.id: r for r in bl.rows()}
        self.assertEqual(sorted(rows), ['IB-1', 'IB-2', 'IB-4', 'IB-5'])
        self.assertEqual(rows['IB-1'].acceptance, 'Accept when: a \\| b works.')
        self.assertEqual(rows['IB-2'].status, 'done')
        self.assertEqual(rows['IB-2'].status_text, 'done (#12)')
        self.assertEqual(rows['IB-4'].status, 'blocked')
        self.assertEqual(rows['IB-1'].source, '#10 review')

    def test_malformed_row_is_kept_unparsed(self):
        bl = backlog.parse(FIXTURE)
        unparsed = [l for l in bl.lines if isinstance(l, backlog.Unparsed)]
        self.assertEqual(len(unparsed), 1)
        self.assertTrue(unparsed[0].startswith('| IB-3 |'))

    def test_find_and_ready_indexes(self):
        bl = backlog.parse(FIXTURE)
        self.assertIsNotNone(bl.find('IB-5'))
        self.assertIsNone(bl.find('IB-99'))
        ready = [bl.lines[i].id for i in bl.ready_indexes()]
        self.assertEqual(ready, ['IB-1', 'IB-5'])

    def test_other_prefix(self):
        t = '| FX-7 | A | low | ready | B | C |\n'
        bl = backlog.parse(t, 'FX')
        self.assertEqual(bl.rows()[0].id, 'FX-7')
        self.assertEqual(backlog.serialise(bl), t)

    @unittest.skipUnless(os.environ.get('FOREMAN_REAL_BACKLOG'), 'set FOREMAN_REAL_BACKLOG to a real backlog file')
    def test_real_backlog_round_trip(self):
        t = open(os.environ['FOREMAN_REAL_BACKLOG'], encoding='utf-8').read()
        self.assertEqual(backlog.serialise(backlog.parse(t)), t)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/_workspace/foreman && python3 -m unittest tests/test_backlog.py -v`
Expected: ERROR `ModuleNotFoundError: No module named 'backlog'`

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
"""Parse, edit and write the foreman backlog table without disturbing untouched lines.

A row is a line `| <PREFIX>-<n> | title | risk | status | acceptance | source |`. Cells are
separated by ` | `; a pipe inside a cell is written `\\|`, so splitting on the literal ` | `
never cuts an escaped pipe. Every other line (headings, legend, blank lines, a malformed row)
is kept verbatim, so serialise(parse(text)) == text byte for byte.
"""
import re

SEP = ' | '
RISKS = ('low', 'medium', 'high')
STATUSES = ('ready', 'deferred', 'blocked', 'done', 'cancelled', 'in-progress')
SET_STATUSES = ('ready', 'deferred', 'blocked', 'cancelled')
NEEDS_REASON = ('blocked', 'cancelled')
SETTABLE = ('title', 'risk', 'acceptance', 'source')


def row_pattern(prefix):
    return re.compile(rf'^\| {re.escape(prefix)}-(\d+) \|')


def split_cells(line):
    """'| a | b |' -> ['a', 'b']; None when the line is not framed like a row."""
    s = line.rstrip()
    if not (s.startswith('| ') and s.endswith(' |')):
        return None
    return s[2:-2].split(SEP)


class Unparsed(str):
    """A line that looks like a row but does not have six cells. Shown, never rewritten."""


class Row:
    def __init__(self, raw, cells):
        self.raw = raw
        self.id, self.title, self.risk, self.status_text, self.acceptance, self.source = cells

    @property
    def status(self):
        m = re.match(r'[a-z-]+', self.status_text)
        return m.group(0) if m else ''

    def cells(self):
        return [self.id, self.title, self.risk, self.status_text, self.acceptance, self.source]

    def as_dict(self):
        return {'id': self.id, 'title': self.title, 'risk': self.risk, 'status': self.status,
                'status_text': self.status_text, 'acceptance': self.acceptance, 'source': self.source}


class Backlog:
    def __init__(self, lines, prefix):
        self.lines = lines
        self.prefix = prefix

    def rows(self):
        return [l for l in self.lines if isinstance(l, Row)]

    def find(self, id_):
        hits = [i for i, l in enumerate(self.lines) if isinstance(l, Row) and l.id == id_]
        return hits[0] if len(hits) == 1 else None

    def ready_indexes(self):
        return [i for i, l in enumerate(self.lines) if isinstance(l, Row) and l.status == 'ready']


def parse(text, prefix='IB'):
    pat = row_pattern(prefix)
    tmp = re.compile(r'^\| NEW-\d+ \|')
    lines = []
    for line in text.split('\n'):
        if pat.match(line) or tmp.match(line):
            cells = split_cells(line)
            lines.append(Row(line, cells) if cells and len(cells) == 6 else Unparsed(line))
        else:
            lines.append(line)
    return Backlog(lines, prefix)


def serialise(bl):
    return '\n'.join(l.raw if isinstance(l, Row) else str(l) for l in bl.lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests/test_backlog.py -v`
Expected: 6 tests OK. The real-backlog test is skipped.

Then run the real round trip:
`FOREMAN_REAL_BACKLOG=/Users/crisis/_workspace/vacancy-vibe.wt/orch/.claude/improvement-backlog.md python3 -m unittest tests/test_backlog.py -v`
Expected: 7 tests OK. (Use any real backlog when that path doesn't exist.)

- [ ] **Step 5: Commit**

```bash
git add scripts/backlog.py tests/test_backlog.py
git commit -m "Add backlog.py: byte-identical parse and serialise of the backlog table"
```

---

### Task 2: Row building and validation (`backlog.py` part 2)

**Files:**
- Modify: `scripts/backlog.py` (append)
- Test: `tests/test_backlog.py` (append a class)

**Interfaces:**
- Consumes (Task 1): `Row`, `SEP`, `RISKS`, `STATUSES`.
- Produces:
  - `escape(value: str) -> str` (pipes become `\|`, already-escaped pipes are left alone);
  - `build(cells: list[str]) -> str`;
  - `validate_cells(cells, prefix) -> list[str]` (an empty list means valid);
  - `line_problems(line) -> list[tuple]`: `('newline',)`, or `('separators', n)` when ` | ` doesn't appear exactly 5 times;
  - `make_row(cells, prefix) -> (Row | None, list[str])`.

- [ ] **Step 1: Write the failing tests**

```python
class BuildTest(unittest.TestCase):
    def test_escape_pipes_and_reject_newlines(self):
        self.assertEqual(backlog.escape('a | b'), 'a \\| b')
        self.assertEqual(backlog.escape('a \\| b'), 'a \\| b')
        row, problems = backlog.make_row(['IB-9', 'T', 'low', 'ready', 'one\ntwo', 's'], 'IB')
        self.assertIsNone(row)
        self.assertIn('acceptance has a newline', problems)

    def test_build_round_trips_through_parse(self):
        cells = ['IB-9', 'Title `x`', 'medium', 'blocked — waits on a \\| b', 'Acc.', '#1']
        line = backlog.build(cells)
        self.assertEqual(line.count(' | '), 5)
        self.assertEqual(backlog.parse(line).rows()[0].cells(), cells)

    def test_validate_rules(self):
        ok = ['IB-9', 'T', 'low', 'ready', 'A', 'S']
        self.assertEqual(backlog.validate_cells(ok, 'IB'), [])
        self.assertIn('risk must be low, medium or high', backlog.validate_cells(['IB-9', 'T', 'huge', 'ready', 'A', 'S'], 'IB'))
        self.assertIn('unknown status "maybe"', backlog.validate_cells(['IB-9', 'T', 'low', 'maybe', 'A', 'S'], 'IB'))
        self.assertIn('title is empty', backlog.validate_cells(['IB-9', ' ', 'low', 'ready', 'A', 'S'], 'IB'))
        self.assertIn('bad id "X-1"', backlog.validate_cells(['X-1', 'T', 'low', 'ready', 'A', 'S'], 'IB'))
        self.assertEqual(backlog.validate_cells(['NEW-1', 'T', 'low', 'ready', 'A', 'S'], 'IB'), [])

    def test_line_problems(self):
        self.assertEqual(backlog.line_problems('| IB-1 | a | b | c | d | e |'), [])
        self.assertEqual(backlog.line_problems('| IB-1 | a | b |'), [('separators', 2)])
        self.assertEqual(backlog.line_problems('| IB-1 | a\nb | c | d | e | f |'), [('newline',)])
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests/test_backlog.py -v`
Expected: `AttributeError: module 'backlog' has no attribute 'escape'` (and similar)

- [ ] **Step 3: Implement (append to `scripts/backlog.py`)**

```python
FIELD_NAMES = ('id', 'title', 'risk', 'status', 'acceptance', 'source')


def escape(value):
    return re.sub(r'(?<!\\)\|', r'\\|', value)


def build(cells):
    return '| ' + SEP.join(cells) + ' |'


def line_problems(line):
    """bk.py's two table rules for a whole row line."""
    if '\n' in line:
        return [('newline',)]
    n = line.count(SEP)
    return [] if n == 5 else [('separators', n)]


def validate_cells(cells, prefix):
    problems = []
    for name, value in zip(FIELD_NAMES, cells):
        if '\n' in value or '\r' in value:
            problems.append(f'{name} has a newline')
    id_, title, risk, status_text = cells[0], cells[1], cells[2], cells[3]
    if not re.fullmatch(rf'{re.escape(prefix)}-\d+|NEW-\d+', id_):
        problems.append(f'bad id "{id_}"')
    if not title.strip():
        problems.append('title is empty')
    if risk not in RISKS:
        problems.append('risk must be low, medium or high')
    word = (re.match(r'[a-z-]+', status_text) or [''])[0]
    if word not in STATUSES:
        problems.append(f'unknown status "{word or status_text}"')
    if not problems and build(cells).count(SEP) != 5:
        problems.append('a cell contains an unescaped " | "')
    return problems


def make_row(cells, prefix):
    problems = validate_cells(cells, prefix)
    return (None, problems) if problems else (Row(build(cells), cells), [])
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m unittest tests/test_backlog.py -v`
Expected: all OK

- [ ] **Step 5: Commit**

```bash
git add scripts/backlog.py tests/test_backlog.py
git commit -m "backlog.py: escape, build and validate rows"
```

---

### Task 3: Operations engine (`backlog.py` part 3)

**Files:**
- Modify: `scripts/backlog.py` (append)
- Test: `tests/test_backlog.py` (append a class)

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces:
  - `apply(bl, ops) -> list[dict]`: mutates `bl` and returns one `{'result': 'applied' | 'conflict' | 'invalid', 'reason': str}` per op, in order.
  - `assign_ids(bl, tmp_ids: list[str]) -> dict[str, str]`: replaces `NEW-n` ids with `<prefix>-<max+1…>`, in the order given.
  - `check_op_shape(op) -> None`: raises `ValueError` on a malformed op.
  - `describe(op, mapping=None) -> str`.

  Op shapes (JSON):
  - `{"op":"set","id","field","value","base"}`
  - `{"op":"status","id","to","reason","base"}`
  - `{"op":"move","id","to":"top"|"bottom"|{"before":id}|{"after":id},"base"}`
  - `{"op":"add","tmp":"NEW-n","title","risk","status","reason","acceptance","source","insert":"top"|"bottom"}`

- [ ] **Step 1: Write the failing tests**

```python
def ops_fixture():
    return backlog.parse(FIXTURE)


def base_of(bl, id_):
    return bl.lines[bl.find(id_)].raw


class OpsTest(unittest.TestCase):
    def test_set_changes_only_that_line(self):
        bl = ops_fixture()
        r = backlog.apply(bl, [{'op': 'set', 'id': 'IB-5', 'field': 'title', 'value': 'New | title', 'base': base_of(bl, 'IB-5')}])
        self.assertEqual(r[0]['result'], 'applied')
        before, after = FIXTURE.split('\n'), backlog.serialise(bl).split('\n')
        changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertIn('| New \\| title |', after[changed[0]])

    def test_status_needs_reason_for_blocked(self):
        bl = ops_fixture()
        r = backlog.apply(bl, [{'op': 'status', 'id': 'IB-1', 'to': 'blocked', 'reason': '', 'base': base_of(bl, 'IB-1')}])
        self.assertEqual(r[0]['result'], 'invalid')
        r = backlog.apply(bl, [{'op': 'status', 'id': 'IB-1', 'to': 'blocked', 'reason': 'waits on #20', 'base': base_of(bl, 'IB-1')}])
        self.assertEqual(r[0]['result'], 'applied')
        self.assertEqual(bl.lines[bl.find('IB-1')].status_text, 'blocked — waits on #20')

    def test_move_top_bottom_before_after(self):
        bl = ops_fixture()
        backlog.apply(bl, [{'op': 'move', 'id': 'IB-5', 'to': 'top', 'base': base_of(bl, 'IB-5')}])
        self.assertEqual([bl.lines[i].id for i in bl.ready_indexes()], ['IB-5', 'IB-1'])
        backlog.apply(bl, [{'op': 'move', 'id': 'IB-5', 'to': 'bottom', 'base': base_of(bl, 'IB-5')}])
        self.assertEqual([bl.lines[i].id for i in bl.ready_indexes()], ['IB-1', 'IB-5'])
        backlog.apply(bl, [{'op': 'move', 'id': 'IB-1', 'to': {'after': 'IB-5'}, 'base': base_of(bl, 'IB-1')}])
        self.assertEqual([bl.lines[i].id for i in bl.ready_indexes()], ['IB-5', 'IB-1'])
        backlog.apply(bl, [{'op': 'move', 'id': 'IB-1', 'to': {'before': 'IB-5'}, 'base': base_of(bl, 'IB-1')}])
        self.assertEqual([bl.lines[i].id for i in bl.ready_indexes()], ['IB-1', 'IB-5'])

    def test_conflict_on_changed_base(self):
        bl = ops_fixture()
        r = backlog.apply(bl, [{'op': 'status', 'id': 'IB-5', 'to': 'deferred', 'reason': '', 'base': '| IB-5 | Fifth | low | ready | OLD | #13 |'}])
        self.assertEqual(r[0]['result'], 'conflict')
        self.assertIn('now "ready"', r[0]['reason'])
        self.assertEqual(backlog.serialise(bl), FIXTURE)

    def test_conflict_on_missing_row_and_anchor(self):
        bl = ops_fixture()
        r = backlog.apply(bl, [{'op': 'set', 'id': 'IB-99', 'field': 'title', 'value': 'x', 'base': 'x'},
                               {'op': 'move', 'id': 'IB-5', 'to': {'before': 'IB-77'}, 'base': base_of(bl, 'IB-5')}])
        self.assertEqual([x['result'] for x in r], ['conflict', 'conflict'])
        self.assertEqual(backlog.serialise(bl), FIXTURE)

    def test_chained_ops_follow_each_other(self):
        bl = ops_fixture()
        b1 = base_of(bl, 'IB-5')
        backlog.apply(bl, [{'op': 'set', 'id': 'IB-5', 'field': 'risk', 'value': 'medium', 'base': b1}])
        b2 = base_of(bl, 'IB-5')
        fresh = ops_fixture()
        r = backlog.apply(fresh, [{'op': 'set', 'id': 'IB-5', 'field': 'risk', 'value': 'medium', 'base': b1},
                                  {'op': 'status', 'id': 'IB-5', 'to': 'deferred', 'reason': '', 'base': b2}])
        self.assertEqual([x['result'] for x in r], ['applied', 'applied'])

    def test_add_and_assign_ids(self):
        bl = ops_fixture()
        r = backlog.apply(bl, [
            {'op': 'add', 'tmp': 'NEW-1', 'title': 'A | b', 'risk': 'low', 'status': 'ready', 'reason': '', 'acceptance': 'X', 'source': 'ui', 'insert': 'top'},
            {'op': 'add', 'tmp': 'NEW-2', 'title': 'C', 'risk': 'low', 'status': 'ready', 'reason': '', 'acceptance': 'Y', 'source': 'ui', 'insert': 'bottom'},
        ])
        self.assertEqual([x['result'] for x in r], ['applied', 'applied'])
        self.assertEqual([bl.lines[i].id for i in bl.ready_indexes()], ['NEW-1', 'IB-1', 'IB-5', 'NEW-2'])
        mapping = backlog.assign_ids(bl, ['NEW-1', 'NEW-2'])
        self.assertEqual(mapping, {'NEW-1': 'IB-6', 'NEW-2': 'IB-7'})
        self.assertEqual([bl.lines[i].id for i in bl.ready_indexes()], ['IB-6', 'IB-1', 'IB-5', 'IB-7'])
        self.assertIn('| IB-6 | A \\| b | low | ready | X | ui |', backlog.serialise(bl))

    def test_invalid_value_changes_nothing(self):
        bl = ops_fixture()
        r = backlog.apply(bl, [{'op': 'set', 'id': 'IB-5', 'field': 'risk', 'value': 'huge', 'base': base_of(bl, 'IB-5')}])
        self.assertEqual(r[0]['result'], 'invalid')
        self.assertEqual(backlog.serialise(bl), FIXTURE)

    def test_check_op_shape(self):
        with self.assertRaises(ValueError):
            backlog.check_op_shape({'op': 'set', 'id': 'IB-1', 'field': 'id', 'value': 'x'})
        with self.assertRaises(ValueError):
            backlog.check_op_shape({'op': 'explode'})
        backlog.check_op_shape({'op': 'move', 'id': 'IB-1', 'to': {'after': 'IB-2'}})
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests/test_backlog.py -v`
Expected: `AttributeError: module 'backlog' has no attribute 'apply'`

- [ ] **Step 3: Implement (append to `scripts/backlog.py`)**

```python
def check_op_shape(op):
    kind = op.get('op')
    if kind == 'set':
        if op.get('field') not in SETTABLE or not isinstance(op.get('value'), str) or not op.get('id'):
            raise ValueError('set needs id, a field in title/risk/acceptance/source, and a string value')
    elif kind == 'status':
        if op.get('to') not in SET_STATUSES or not op.get('id'):
            raise ValueError('status needs id and to in ready/deferred/blocked/cancelled')
    elif kind == 'move':
        to = op.get('to')
        ok = to in ('top', 'bottom') or (isinstance(to, dict) and len(to) == 1 and set(to) <= {'before', 'after'})
        if not ok or not op.get('id'):
            raise ValueError('move needs id and to: top, bottom, {before: id} or {after: id}')
    elif kind == 'add':
        if op.get('insert') not in ('top', 'bottom') or not isinstance(op.get('title'), str):
            raise ValueError('add needs title and insert top or bottom')
    else:
        raise ValueError(f'unknown op "{kind}"')


def _status_text(to, reason):
    reason = (reason or '').strip()
    return f'{to} — {escape(reason)}' if reason else to


def _insert_at(bl, where, fallback):
    ready = bl.ready_indexes()
    if where == 'top':
        return ready[0] if ready else fallback
    return ready[-1] + 1 if ready else fallback


def _apply_one(bl, op):
    kind = op['op']
    if kind == 'add':
        if op.get('status') in NEEDS_REASON and not (op.get('reason') or '').strip():
            return {'result': 'invalid', 'reason': f'{op["status"]} needs a reason'}
        cells = [op['tmp'], escape(op['title']), op['risk'], _status_text(op['status'], op.get('reason')),
                 escape(op.get('acceptance', '')), escape(op.get('source', ''))]
        row, problems = make_row(cells, bl.prefix)
        if problems:
            return {'result': 'invalid', 'reason': '; '.join(problems)}
        rows = [i for i, l in enumerate(bl.lines) if isinstance(l, Row)]
        bl.lines.insert(_insert_at(bl, op['insert'], (rows[-1] + 1) if rows else len(bl.lines)), row)
        return {'result': 'applied', 'reason': ''}
    i = bl.find(op['id'])
    if i is None:
        return {'result': 'conflict', 'reason': f'{op["id"]} is no longer in the backlog'}
    row = bl.lines[i]
    if row.raw != op.get('base'):
        return {'result': 'conflict', 'reason': f'changed on main since you edited it; now "{row.status_text}"'}
    if kind == 'move':
        bl.lines.pop(i)
        to = op['to']
        if isinstance(to, dict):
            where, anchor = next(iter(to.items()))
            k = bl.find(anchor)
            if k is None:
                bl.lines.insert(i, row)
                return {'result': 'conflict', 'reason': f'{anchor} is no longer in the backlog'}
            bl.lines.insert(k if where == 'before' else k + 1, row)
        else:
            bl.lines.insert(_insert_at(bl, to, i), row)
        return {'result': 'applied', 'reason': ''}
    cells = row.cells()
    if kind == 'set':
        idx = {'title': 1, 'risk': 2, 'acceptance': 4, 'source': 5}[op['field']]
        cells[idx] = op['value'] if op['field'] == 'risk' else escape(op['value'])
    else:
        if op['to'] in NEEDS_REASON and not (op.get('reason') or '').strip():
            return {'result': 'invalid', 'reason': f'{op["to"]} needs a reason'}
        cells[3] = _status_text(op['to'], op.get('reason'))
    new, problems = make_row(cells, bl.prefix)
    if problems:
        return {'result': 'invalid', 'reason': '; '.join(problems)}
    bl.lines[i] = new
    return {'result': 'applied', 'reason': ''}


def apply(bl, ops):
    return [_apply_one(bl, op) for op in ops]


def assign_ids(bl, tmp_ids):
    pat = row_pattern(bl.prefix)
    raws = [l.raw if isinstance(l, Row) else str(l) for l in bl.lines]  # malformed rows still hold their id
    top = max([int(m.group(1)) for r in raws for m in [pat.match(r)] if m] or [0])
    mapping = {}
    for tmp in tmp_ids:
        top += 1
        mapping[tmp] = f'{bl.prefix}-{top}'
    for i, l in enumerate(bl.lines):
        if isinstance(l, Row) and l.id in mapping:
            cells = l.cells()
            cells[0] = mapping[l.id]
            bl.lines[i] = Row(build(cells), cells)
    return mapping


def describe(op, mapping=None):
    mapping = mapping or {}
    kind = op['op']
    if kind == 'add':
        return f'new row {mapping.get(op["tmp"], op["tmp"])}: {op["title"]}'
    if kind == 'set':
        return f'{op["id"]}: {op["field"]} edited'
    if kind == 'status':
        reason = (op.get('reason') or '').strip()
        return f'{op["id"]}: status → {op["to"]}' + (f' ({reason})' if reason else '')
    to = op['to']
    where = to if isinstance(to, str) else f'{next(iter(to))} {next(iter(to.values()))}'
    return f'{op["id"]}: moved to {where}'
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m unittest tests/test_backlog.py -v`
Expected: all OK

- [ ] **Step 5: Mutation check**

Temporarily change `if row.raw != op.get('base'):` to `if False:`. Run the suite and confirm `test_conflict_on_changed_base` fails. Restore it.

Temporarily change `escape` to `return value`. Confirm `test_escape_pipes_and_reject_newlines` and `test_add_and_assign_ids` fail. Restore it.

- [ ] **Step 6: Commit**

```bash
git add scripts/backlog.py tests/test_backlog.py
git commit -m "backlog.py: operations with base-line conflicts and id assignment"
```

---

### Task 4: bk.py onto backlog.py (behaviour-identical)

**Files:**
- Modify: `scripts/bk.py` (the `ROW` pattern and the new-row checks)
- Test: `tests/run.sh` (existing bk.py cases, unchanged), plus a one-off byte comparison

**Interfaces:**
- Consumes: `backlog.row_pattern(prefix)` and `backlog.line_problems(line)`.

- [ ] **Step 1: Keep the old script for the comparison**

```bash
cp scripts/bk.py /tmp/bk_old.py
```

- [ ] **Step 2: Edit bk.py**

After `import cfg  # noqa: E402`, add:

```python
import backlog  # noqa: E402
```

Replace `ROW = re.compile(rf'^\| {E}-(\d+) \|')` with:

```python
    ROW = backlog.row_pattern(P)
```

Replace the new-row checks:

```python
        for row in rep.get('new_rows', []):
            if '\n' in row:
                problems.append(f'{row[:12]}: new row has a newline')
            elif row.count(' | ') != 5:
                problems.append(f'{row[:12]}: {row.count(" | ")} " | " separators, want 5 (escape a pipe as \\|)')
            else:
```

with:

```python
        for row in rep.get('new_rows', []):
            issue = (backlog.line_problems(row) or [None])[0]
            if issue == ('newline',):
                problems.append(f'{row[:12]}: new row has a newline')
            elif issue:
                problems.append(f'{row[:12]}: {issue[1]} " | " separators, want 5 (escape a pipe as \\|)')
            else:
```

- [ ] **Step 3: Run the existing suite**

Run: `tests/run.sh 2>&1 | tail -3`
Expected: the same pass count as before (76), 0 failed.

- [ ] **Step 4: Byte-compare old and new on a real backlog**

```bash
R=/tmp/bkcmp; rm -rf $R; mkdir -p $R/a/.claude $R/b/.claude
B=/Users/crisis/_workspace/vacancy-vibe.wt/orch/.claude/improvement-backlog.md
cp $B $R/a/.claude/improvement-backlog.md; cp $B $R/b/.claude/improvement-backlog.md
ID=$(grep -m1 -oE '^\| IB-[0-9]+ \| [^|]+ \| low \| ready' $B | grep -oE 'IB-[0-9]+')
printf '{"pr": 999, "done": [{"id": "%s", "note": "x"}], "new_rows": ["| IB-NEW-a | t | low | ready | a | s |", "| IB-NEW-b | bad | low |"]}' $ID > $R/r.json
python3 /tmp/bk_old.py $R/a $R/r.json > $R/a.out 2>&1; echo "old exit $?"
python3 scripts/bk.py $R/b $R/r.json > $R/b.out 2>&1; echo "new exit $?"
diff $R/a.out $R/b.out && diff -r $R/a $R/b && echo IDENTICAL
```

Expected: both exits are 1, because the bad row is refused. Then `IDENTICAL`.

Repeat with only the valid new row (drop `IB-NEW-b`). Expected: both exit 0, then `IDENTICAL`.

- [ ] **Step 5: Commit**

```bash
git add scripts/bk.py
git commit -m "bk.py: take the row pattern and line checks from backlog.py"
```

---

### Task 5: Server core: Repo data, pending store, token and Host checks

**Files:**
- Create: `scripts/ui.py`
- Test: `tests/test_ui_api.py`

**Interfaces:**
- Consumes: `backlog.parse/apply/check_op_shape/row_pattern/Row/Unparsed` and `cfg.get`.
- Produces:
  - `ui.Repo(path)`, with:
    - `.view(force=False) -> dict`
    - `.add_op(op) -> None`
    - `.undo() -> None`
    - `.drop(i) -> None`
    - `.rebase(i) -> None`
    - `.progress() -> dict`
    - `.publish() -> dict`
    - `.publishing: threading.Lock`
  - `ui.make_server(repo_path, port=0) -> (server, url)`. The server has `.token` and `.repo`.
  - `ui.main()`.
  - The JSON API:
    - `GET /api/backlog[?refresh=1]` returns `{sha, branch, fetched_at, prefix, rows, ops, results, notice}`.
    - Each row is `{id, title, risk, status, status_text, acceptance, source, position?, pending, touched}` or `{unparsed}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ui_api.py
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))
import ui  # noqa: E402

BACKLOG = '''# Backlog

| IB-1 | One | low | ready | A | s |
| IB-2 | Two | medium | ready | B | s |
| IB-3 | Three | low | done (#5) | C | #4 |
'''
CONFIG = '---\nbacklog_path: .claude/improvement-backlog.md\nid_prefix: IB\n---\n'
FAKE_GH = '''#!/bin/sh
echo "$@" >> "$FAKE_GH_LOG"
[ -n "$FAKE_GH_FAIL" ] && { echo "gh failed" >&2; exit 1; }
case "$1 $2" in
  "pr create") echo "https://github.com/acme/repo/pull/77" ;;
  "pr list") echo '[{"number":5,"mergedAt":"2026-10-01T10:00:00Z"},{"number":4,"mergedAt":"2026-09-30T10:00:00Z"}]' ;;
esac
'''


def git(cwd, *args):
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@e', *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout


class Fixture:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix='foreman-ui-test-')
        self.origin = os.path.join(self.tmp, 'origin.git')
        self.work = os.path.join(self.tmp, 'work')
        git(self.tmp, 'init', '-q', '--bare', '-b', 'main', self.origin)
        git(self.tmp, 'clone', '-q', self.origin, self.work)
        os.makedirs(os.path.join(self.work, '.claude'))
        open(os.path.join(self.work, '.claude/improvement-backlog.md'), 'w').write(BACKLOG)
        open(os.path.join(self.work, '.claude/foreman.config.md'), 'w').write(CONFIG)
        git(self.work, 'symbolic-ref', 'HEAD', 'refs/heads/main')
        git(self.work, 'add', '-A')
        git(self.work, 'commit', '-q', '-m', 'init')
        git(self.work, 'push', '-q', '-u', 'origin', 'main')
        git(self.work, 'remote', 'set-head', 'origin', 'main')
        bin_dir = os.path.join(self.tmp, 'bin')
        os.makedirs(bin_dir)
        gh = os.path.join(bin_dir, 'gh')
        open(gh, 'w').write(FAKE_GH)
        os.chmod(gh, 0o755)
        self.gh_log = os.path.join(self.tmp, 'gh.log')
        os.environ['FAKE_GH_LOG'] = self.gh_log
        os.environ['PATH'] = bin_dir + os.pathsep + os.environ['PATH']
        os.environ['FOREMAN_HOME'] = os.path.join(self.tmp, 'home')
        os.environ['GIT_AUTHOR_NAME'] = os.environ['GIT_COMMITTER_NAME'] = 't'
        os.environ['GIT_AUTHOR_EMAIL'] = os.environ['GIT_COMMITTER_EMAIL'] = 't@e'
        self.server, self.url = ui.make_server(self.work, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_port

    def request(self, method, path, body=None, token=True, host=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=30)
        headers = {'Host': host or f'127.0.0.1:{self.port}'}
        if token:
            headers['X-Foreman-Token'] = self.server.token
        if body is not None:
            headers['Content-Type'] = 'application/json'
        c.request(method, path, json.dumps(body) if body is not None else None, headers)
        r = c.getresponse()
        data = r.read()
        try:
            return r.status, json.loads(data), r
        except ValueError:
            return r.status, data, r

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.close()
        os.environ.pop('FAKE_GH_FAIL', None)

    def test_rejects_missing_token(self):
        status, _, _ = self.f.request('GET', '/api/backlog', token=False)
        self.assertEqual(status, 403)

    def test_rejects_bad_host(self):
        status, _, _ = self.f.request('GET', '/api/backlog', host=f'evil.example:{self.f.port}')
        self.assertEqual(status, 403)

    def test_index_sets_cookie(self):
        status, _, r = self.f.request('GET', f'/?t={self.f.server.token}', token=False)
        self.assertEqual(status, 200)
        cookie = r.getheader('Set-Cookie')
        self.assertIn('foreman_t=', cookie)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)

    def test_static_path_traversal_refused(self):
        status, _, _ = self.f.request('GET', '/ui/../scripts/ui.py')
        self.assertIn(status, (403, 404))

    def test_backlog_reads_origin_not_working_tree(self):
        open(os.path.join(self.f.work, '.claude/improvement-backlog.md'), 'a').write('| IB-9 | Local only | low | ready | x | y |\n')
        status, data, _ = self.f.request('GET', '/api/backlog?refresh=1')
        self.assertEqual(status, 200)
        ids = [r['id'] for r in data['rows'] if 'id' in r]
        self.assertEqual(ids, ['IB-1', 'IB-2', 'IB-3'])
        self.assertEqual([r.get('position') for r in data['rows'][:2]], [1, 2])

    def test_ops_round_trip_and_undo(self):
        status, data, _ = self.f.request('POST', '/api/ops', {'op': 'move', 'id': 'IB-2', 'to': 'top'})
        self.assertEqual(status, 200)
        self.assertEqual([r['id'] for r in data['rows'] if r.get('status') == 'ready'], ['IB-2', 'IB-1'])
        self.assertTrue(data['ops'][0]['base'].startswith('| IB-2 |'))
        self.assertTrue(next(r for r in data['rows'] if r.get('id') == 'IB-2')['pending'])
        status, data, _ = self.f.request('POST', '/api/ops/undo')
        self.assertEqual(data['ops'], [])

    def test_invalid_add_is_refused_up_front(self):
        status, data, _ = self.f.request('POST', '/api/ops', {'op': 'add', 'title': 'x', 'risk': 'huge', 'status': 'ready', 'reason': '', 'acceptance': '', 'source': '', 'insert': 'top'})
        self.assertEqual(status, 400)
        self.assertIn('risk must be', data['error'])

    def test_bad_op_is_400(self):
        status, data, _ = self.f.request('POST', '/api/ops', {'op': 'set', 'id': 'IB-1', 'field': 'id', 'value': 'x'})
        self.assertEqual(status, 400)
        self.assertIn('set needs', data['error'])
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests/test_ui_api.py -v`
Expected: `ModuleNotFoundError: No module named 'ui'`

- [ ] **Step 3: Implement `scripts/ui.py`** (the publish and progress methods arrive in Tasks 6–7 and raise `NotImplementedError` until then)

```python
#!/usr/bin/env python3
"""Local backlog UI for foreman.

  python3 ui.py --repo DIR [--port N] [--no-open]   start (or print the URL of the running one)
  python3 ui.py --repo DIR --stop                   stop it

Reads the backlog from origin/<default branch> (never the working tree), keeps pending edits in
$FOREMAN_HOME (default ~/.claude/foreman)/<repo-key>/pending.json, and publishes them as a PR from
a temporary worktree. Binds 127.0.0.1 only; every request needs the session token and a local Host.
"""
import argparse
import hashlib
import http.cookies
import http.server
import json
import os
import signal
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backlog  # noqa: E402
import cfg  # noqa: E402

UI_DIR = os.path.realpath(os.path.join(HERE, '..', 'ui'))
MIME = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml'}


class GitError(Exception):
    pass


class Busy(Exception):
    pass


class PublishError(Exception):
    pass


def home():
    return os.environ.get('FOREMAN_HOME', os.path.expanduser('~/.claude/foreman'))


class Repo:
    def __init__(self, path):
        self.path = os.path.realpath(path)
        self.backlog_path = cfg.get(self.path, 'backlog_path', '.claude/improvement-backlog.md')
        self.prefix = cfg.get(self.path, 'id_prefix', 'IB')
        self.origin = self.git('remote', 'get-url', 'origin', check=False).strip()
        self.key = hashlib.sha1(f'{self.origin}\n{self.path}'.encode()).hexdigest()[:12]
        self.state = os.path.join(home(), self.key)
        os.makedirs(self.state, exist_ok=True)
        self.branch = self.default_branch()
        self.lock = threading.Lock()
        self.publishing = threading.Lock()
        self.notice = ''
        self._fetched = None
        self._blame = {}
        self._prs = (0, None)

    def git(self, *args, check=True, cwd=None):
        p = subprocess.run(['git', *args], cwd=cwd or self.path, capture_output=True, text=True, encoding='utf-8')
        if check and p.returncode:
            raise GitError(f'git {" ".join(args)}: {p.stderr.strip() or p.stdout.strip()}')
        return p.stdout

    def default_branch(self):
        out = self.git('symbolic-ref', '--short', 'refs/remotes/origin/HEAD', check=False).strip()
        return out.split('/', 1)[1] if '/' in out else 'main'

    def fetch(self, force=False):
        with self.lock:
            if self._fetched and not force and time.time() - self._fetched[2] < 60:
                return self._fetched
            if not self.origin:
                raise GitError('this repository has no "origin" remote')
            self.git('fetch', '-q', 'origin', self.branch)
            sha = self.git('rev-parse', f'origin/{self.branch}').strip()
            text = self.git('show', f'{sha}:{self.backlog_path}')
            self._fetched = (sha, text, time.time())
            return self._fetched

    # ---- pending store -------------------------------------------------------------------------
    def ops_path(self):
        return os.path.join(self.state, 'pending.json')

    def load_ops(self):
        p = self.ops_path()
        if not os.path.exists(p):
            return []
        try:
            with open(p, encoding='utf-8') as f:
                ops = json.load(f)
            if not isinstance(ops, list):
                raise ValueError('not a list')
            return ops
        except (ValueError, OSError):
            os.replace(p, f'{p}.bad-{int(time.time())}')
            self.notice = 'pending.json could not be read; it was set aside and the list starts empty'
            return []

    def save_ops(self, ops):
        fd, tmp = tempfile.mkstemp(dir=self.state, suffix='.json')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(ops, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.ops_path())

    def replay(self, ops, upto=None):
        _, text, _ = self.fetch()
        bl = backlog.parse(text, self.prefix)
        results = backlog.apply(bl, ops if upto is None else ops[:upto])
        return bl, results

    def add_op(self, op):
        backlog.check_op_shape(op)
        ops = self.load_ops()
        bl, _ = self.replay(ops)
        if op['op'] == 'add':
            n = 1 + max([int(o['tmp'].split('-')[1]) for o in ops if o.get('op') == 'add'] or [0])
            op = {**op, 'tmp': f'NEW-{n}'}
        else:
            i = bl.find(op['id'])
            if i is None:
                raise ValueError(f'{op["id"]} is not in the backlog')
            op = {**op, 'base': bl.lines[i].raw}
        _, results = self.replay(ops + [op])
        if results[-1]['result'] == 'invalid':
            raise ValueError(results[-1]['reason'])
        ops.append(op)
        self.save_ops(ops)

    def undo(self):
        ops = self.load_ops()
        if ops:
            ops.pop()
            self.save_ops(ops)

    def drop(self, i):
        ops = self.load_ops()
        if not 0 <= i < len(ops):
            raise ValueError(f'no pending change #{i}')
        ops.pop(i)
        self.save_ops(ops)

    def rebase(self, i):
        ops = self.load_ops()
        if not 0 <= i < len(ops) or ops[i]['op'] == 'add':
            raise ValueError(f'change #{i} cannot be re-applied')
        bl, _ = self.replay(ops, upto=i)
        k = bl.find(ops[i]['id'])
        if k is None:
            raise ValueError(f'{ops[i]["id"]} is no longer in the backlog; drop this change')
        ops[i] = {**ops[i], 'base': bl.lines[k].raw}
        self.save_ops(ops)

    # ---- the view the page renders ---------------------------------------------------------------
    def blame(self, sha):
        if sha in self._blame:
            return self._blame[sha]
        out = self.git('blame', '--line-porcelain', sha, '--', self.backlog_path, check=False)
        pat, res, t = backlog.row_pattern(self.prefix), {}, None
        for line in out.split('\n'):
            if line.startswith('author-time '):
                t = int(line.split()[1])
            elif line.startswith('\t'):
                content = line[1:]
                if t and pat.match(content):
                    res[content.split(' | ', 1)[0][2:]] = t
        self._blame = {sha: res}
        return res

    def view(self, force=False):
        sha, _, at = self.fetch(force)
        ops = self.load_ops()
        bl, results = self.replay(ops)
        touched = self.blame(sha)
        pending = {o.get('id') or o.get('tmp') for o, r in zip(ops, results) if r['result'] == 'applied'}
        rows, pos = [], 0
        for line in bl.lines:
            if isinstance(line, backlog.Row):
                d = line.as_dict()
                if line.status == 'ready':
                    pos += 1
                    d['position'] = pos
                d['pending'] = line.id in pending
                d['touched'] = touched.get(line.id)
                rows.append(d)
            elif isinstance(line, backlog.Unparsed):
                rows.append({'unparsed': str(line)})
        notice, self.notice = self.notice, ''
        return {'sha': sha, 'branch': self.branch, 'fetched_at': at, 'prefix': self.prefix,
                'rows': rows, 'ops': ops, 'results': results, 'notice': notice}

    def progress(self):
        raise NotImplementedError

    def publish(self):
        raise NotImplementedError


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'foreman-ui'

    def log_message(self, *args):
        pass

    def host_ok(self):
        p = self.server.server_port
        return self.headers.get('Host', '') in (f'127.0.0.1:{p}', f'localhost:{p}')

    def token_ok(self):
        t = self.server.token
        jar = http.cookies.SimpleCookie(self.headers.get('Cookie', ''))
        return (jar.get('foreman_t') is not None and jar['foreman_t'].value == t) or self.headers.get('X-Foreman-Token') == t

    def send(self, code, body, ctype='application/json', extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n) or b'{}') if n else {}

    def static(self, rel):
        path = os.path.realpath(os.path.join(UI_DIR, rel))
        if not path.startswith(UI_DIR + os.sep) or not os.path.isfile(path):
            return self.send(404, {'error': 'not found'})
        with open(path, 'rb') as f:
            self.send(200, f.read(), MIME.get(os.path.splitext(path)[1], 'application/octet-stream'))

    def do_GET(self):
        self.route('GET')

    def do_POST(self):
        self.route('POST')

    def do_DELETE(self):
        self.route('DELETE')

    def route(self, method):
        if not self.host_ok():
            return self.send(403, {'error': 'this server only answers on 127.0.0.1 / localhost'})
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        if method == 'GET' and u.path == '/':
            if q.get('t', [''])[0] == self.server.token:
                cookie = f'foreman_t={self.server.token}; HttpOnly; SameSite=Strict; Path=/'
                return self.static_index(cookie)
            if self.token_ok():
                return self.static_index(None)
            return self.send(403, b'Open the URL that /foreman ui printed.', 'text/plain; charset=utf-8')
        if not self.token_ok():
            return self.send(403, {'error': 'missing or wrong session token'})
        if method == 'GET' and u.path.startswith('/ui/'):
            return self.static(u.path[len('/ui/'):])
        repo = self.server.repo
        try:
            parts = [p for p in u.path.split('/') if p]
            if method == 'GET' and u.path == '/api/backlog':
                return self.send(200, repo.view(force=q.get('refresh', [''])[0] == '1'))
            if method == 'GET' and u.path == '/api/progress':
                return self.send(200, repo.progress())
            if method == 'POST' and u.path == '/api/ops':
                repo.add_op(self.body())
                return self.send(200, repo.view())
            if method == 'POST' and u.path == '/api/ops/undo':
                repo.undo()
                return self.send(200, repo.view())
            if method == 'DELETE' and len(parts) == 3 and parts[:2] == ['api', 'ops']:
                repo.drop(int(parts[2]))
                return self.send(200, repo.view())
            if method == 'POST' and len(parts) == 4 and parts[:2] == ['api', 'ops'] and parts[3] == 'rebase':
                repo.rebase(int(parts[2]))
                return self.send(200, repo.view())
            if method == 'POST' and u.path == '/api/publish':
                return self.send(200, repo.publish())
            return self.send(404, {'error': 'not found'})
        except Busy:
            return self.send(409, {'error': 'a publish is already running'})
        except (ValueError, KeyError) as e:
            return self.send(400, {'error': str(e)})
        except (GitError, PublishError) as e:
            return self.send(502, {'error': str(e)})

    def static_index(self, cookie):
        with open(os.path.join(UI_DIR, 'index.html'), 'rb') as f:
            self.send(200, f.read(), 'text/html; charset=utf-8', {'Set-Cookie': cookie} if cookie else None)


def make_server(repo_path, port=0):
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.token = secrets.token_urlsafe(24)
    server.repo = Repo(repo_path)
    return server, f'http://127.0.0.1:{server.server_port}/?t={server.token}'


def info_path(repo):
    return os.path.join(repo.state, 'ui.json')


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser(description='foreman backlog UI')
    ap.add_argument('--repo', default='.')
    ap.add_argument('--port', type=int, default=0)
    ap.add_argument('--no-open', action='store_true')
    ap.add_argument('--stop', action='store_true')
    a = ap.parse_args()
    repo = Repo(a.repo)
    info = info_path(repo)
    running = json.load(open(info)) if os.path.exists(info) else None
    if a.stop:
        if running and alive(running['pid']):
            os.kill(running['pid'], signal.SIGTERM)
            print('stopped')
        if os.path.exists(info):
            os.remove(info)
        return
    if running and alive(running['pid']):
        print(running['url'])
        return
    server, url = make_server(a.repo, a.port)
    with open(info, 'w') as f:
        json.dump({'pid': os.getpid(), 'port': server.server_port, 'url': url}, f)
    print(url, flush=True)
    if not a.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        if os.path.exists(info):
            os.remove(info)


if __name__ == '__main__':
    main()
```

Also create a minimal `ui/index.html` now, so `test_index_sets_cookie` has a file to serve. Task 7 replaces it:

```html
<!doctype html><html lang="en"><head><meta charset="utf-8"><title>foreman backlog</title></head><body></body></html>
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m unittest tests/test_ui_api.py -v`
Expected: 8 tests OK

- [ ] **Step 5: Commit**

```bash
git add scripts/ui.py ui/index.html tests/test_ui_api.py
git commit -m "Add ui.py: origin-backed backlog view, pending ops, token and Host checks"
```

---

### Task 6: Publish and progress

**Files:**
- Modify: `scripts/ui.py` (replace the two `NotImplementedError` methods; add the helpers)
- Test: `tests/test_ui_api.py` (append a class)

**Interfaces:**
- Consumes: `backlog.apply/assign_ids/describe/serialise` and `Repo.load_ops/save_ops/git/fetch`.
- Produces:
  - `Repo.publish() -> {'pr_url': str | None, 'applied': int, 'conflicts': int, 'branch': str | None, 'message': str}`.
  - `Repo.progress() -> {'available': bool, 'days': [{'day', 'done', 'filed'}], 'message': str}`.

- [ ] **Step 1: Write the failing tests (append)**

```python
class PublishTest(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.close()
        os.environ.pop('FAKE_GH_FAIL', None)

    def remote_branches(self):
        return [l.split('refs/heads/')[1] for l in git(self.f.work, 'ls-remote', '--heads', 'origin').splitlines()]

    def test_publish_creates_branch_with_only_those_lines(self):
        self.f.request('POST', '/api/ops', {'op': 'status', 'id': 'IB-2', 'to': 'deferred', 'reason': 'later'})
        self.f.request('POST', '/api/ops', {'op': 'add', 'title': 'New one', 'risk': 'low', 'status': 'ready', 'reason': '', 'acceptance': 'Acc', 'source': 'ui', 'insert': 'bottom'})
        status, res, _ = self.f.request('POST', '/api/publish')
        self.assertEqual(status, 200, res)
        self.assertEqual((res['applied'], res['conflicts']), (2, 0))
        self.assertEqual(res['pr_url'], 'https://github.com/acme/repo/pull/77')
        branch = res['branch']
        self.assertIn(branch, self.remote_branches())
        git(self.f.work, 'fetch', '-q', 'origin')
        diff = git(self.f.work, 'diff', 'origin/main', f'origin/{branch}', '--', '.claude/improvement-backlog.md')
        changed = [l for l in diff.splitlines() if l.startswith(('+|', '-|'))]
        self.assertEqual(sorted(changed), sorted([
            '-| IB-2 | Two | medium | ready | B | s |',
            '+| IB-2 | Two | medium | deferred — later | B | s |',
            '+| IB-4 | New one | low | ready | Acc | ui |',
        ]))
        msg = git(self.f.work, 'log', '-1', '--format=%B', f'origin/{branch}')
        self.assertNotIn('Co-Authored-By', msg)
        gh = open(self.f.gh_log).read()
        self.assertIn('pr create --base main --head ' + branch, gh)
        status, data, _ = self.f.request('GET', '/api/backlog')
        self.assertEqual(data['ops'], [])
        self.assertNotIn('foreman-ui-', git(self.f.work, 'worktree', 'list'))

    def test_publish_keeps_conflicts(self):
        self.f.request('POST', '/api/ops', {'op': 'status', 'id': 'IB-1', 'to': 'deferred', 'reason': ''})
        self.f.request('POST', '/api/ops', {'op': 'move', 'id': 'IB-2', 'to': 'top'})
        other = os.path.join(self.f.tmp, 'other')
        git(self.f.tmp, 'clone', '-q', self.f.origin, other)
        p = os.path.join(other, '.claude/improvement-backlog.md')
        open(p, 'w').write(open(p).read().replace('| IB-1 | One | low | ready | A | s |', '| IB-1 | One | low | done (#9) | A | s |'))
        git(other, 'commit', '-qam', 'bookkeeping')
        git(other, 'push', '-q')
        status, res, _ = self.f.request('POST', '/api/publish')
        self.assertEqual((res['applied'], res['conflicts']), (1, 1))
        status, data, _ = self.f.request('GET', '/api/backlog?refresh=1')
        self.assertEqual(len(data['ops']), 1)
        self.assertEqual(data['results'][0]['result'], 'conflict')
        self.assertIn('done (#9)', data['results'][0]['reason'])

    def test_publish_leaves_checkout_untouched(self):
        p = os.path.join(self.f.work, '.claude/improvement-backlog.md')
        open(p, 'a').write('local edit\n')
        git(self.f.work, 'checkout', '-q', '-b', 'my-feature')
        before = (open(p).read(), git(self.f.work, 'status', '--porcelain'), git(self.f.work, 'branch', '--show-current'))
        self.f.request('POST', '/api/ops', {'op': 'move', 'id': 'IB-2', 'to': 'top'})
        status, res, _ = self.f.request('POST', '/api/publish')
        self.assertEqual(status, 200)
        after = (open(p).read(), git(self.f.work, 'status', '--porcelain'), git(self.f.work, 'branch', '--show-current'))
        self.assertEqual(before, after)

    def test_publish_failure_cleans_up(self):
        self.f.request('POST', '/api/ops', {'op': 'move', 'id': 'IB-2', 'to': 'top'})
        os.environ['FAKE_GH_FAIL'] = '1'
        status, res, _ = self.f.request('POST', '/api/publish')
        self.assertEqual(status, 502)
        self.assertIn('gh pr create', res['error'])
        self.assertEqual([b for b in self.remote_branches() if b.startswith('backlog/ui-')], [])
        self.assertNotIn('backlog/ui-', git(self.f.work, 'branch', '--list'))
        self.assertNotIn('foreman-ui-', git(self.f.work, 'worktree', 'list'))
        status, data, _ = self.f.request('GET', '/api/backlog')
        self.assertEqual(len(data['ops']), 1)

    def test_concurrent_publish_is_409(self):
        self.f.server.repo.publishing.acquire()
        try:
            status, _, _ = self.f.request('POST', '/api/publish')
            self.assertEqual(status, 409)
        finally:
            self.f.server.repo.publishing.release()

    def test_all_conflicts_publishes_nothing(self):
        self.f.request('POST', '/api/ops', {'op': 'move', 'id': 'IB-2', 'to': 'top'})
        ops = self.f.server.repo.load_ops()
        ops[0]['base'] = 'stale'
        self.f.server.repo.save_ops(ops)
        status, res, _ = self.f.request('POST', '/api/publish')
        self.assertEqual((res['applied'], res['pr_url']), (0, None))
        self.assertIn('nothing to publish', res['message'])

    def test_progress_uses_gh(self):
        status, res, _ = self.f.request('GET', '/api/progress')
        self.assertTrue(res['available'])
        self.assertEqual(res['days'], [{'day': '2026-09-30', 'done': 0, 'filed': 1},
                                       {'day': '2026-10-01', 'done': 1, 'filed': 0}])
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests/test_ui_api.py -v`
Expected: the new tests ERROR with `NotImplementedError` (500, or connection reset)

- [ ] **Step 3: Implement (replace the two stub methods in `Repo`; add the imports)**

Add `import re`, `import shutil` and `from collections import Counter` and `from datetime import datetime` to the imports. Then:

```python
    def merged_prs(self):
        at, val = self._prs
        if val is not None and time.time() - at < 600:
            return val
        if not shutil.which('gh'):
            return None
        p = subprocess.run(['gh', 'pr', 'list', '--state', 'merged', '--limit', '500', '--json', 'number,mergedAt'],
                           cwd=self.path, capture_output=True, text=True)
        if p.returncode:
            return None
        val = {str(x['number']): (x.get('mergedAt') or '')[:10] for x in json.loads(p.stdout or '[]')}
        self._prs = (time.time(), val)
        return val

    def progress(self):
        _, text, _ = self.fetch()
        prs = self.merged_prs()
        if prs is None:
            return {'available': False, 'days': [], 'message': 'gh is not available, so merge dates are unknown'}
        done, filed = Counter(), Counter()
        for r in backlog.parse(text, self.prefix).rows():
            m = re.match(r'done \(#(\d+)\)', r.status_text)
            if m and prs.get(m.group(1)):
                done[prs[m.group(1)]] += 1
            m = re.search(r'#(\d+)', r.source)
            if m and prs.get(m.group(1)):
                filed[prs[m.group(1)]] += 1
        days = sorted(set(done) | set(filed))[-30:]
        return {'available': True, 'days': [{'day': d, 'done': done[d], 'filed': filed[d]} for d in days], 'message': ''}

    def next_branch(self):
        stem = f'backlog/ui-{datetime.now().strftime("%Y%m%d")}-'
        local = self.git('branch', '--list', f'{stem}*').split()
        remote = [l.split('refs/heads/')[1] for l in self.git('ls-remote', '--heads', 'origin', f'refs/heads/{stem}*', check=False).splitlines() if 'refs/heads/' in l]
        taken = set(local) | set(remote)
        n = 1
        while f'{stem}{n}' in taken:
            n += 1
        return f'{stem}{n}'

    def publish(self):
        if not self.publishing.acquire(blocking=False):
            raise Busy()
        wt = branch = None
        pushed = False
        try:
            ops = self.load_ops()
            if not ops:
                return {'pr_url': None, 'applied': 0, 'conflicts': 0, 'branch': None, 'message': 'nothing pending'}
            self.git('fetch', '-q', 'origin', self.branch)
            base_sha = self.git('rev-parse', f'origin/{self.branch}').strip()
            branch = self.next_branch()
            wt = tempfile.mkdtemp(prefix='foreman-ui-')
            os.rmdir(wt)
            self.git('worktree', 'add', '-q', '-b', branch, wt, base_sha)
            path = os.path.join(wt, self.backlog_path)
            with open(path, encoding='utf-8') as f:
                bl = backlog.parse(f.read(), self.prefix)
            results = backlog.apply(bl, ops)
            bad = [f'{backlog.describe(o)}: {r["reason"]}' for o, r in zip(ops, results) if r['result'] == 'invalid']
            if bad:
                raise PublishError('; '.join(bad))
            applied = [o for o, r in zip(ops, results) if r['result'] == 'applied']
            conflicts = [o for o, r in zip(ops, results) if r['result'] == 'conflict']
            if not applied:
                return {'pr_url': None, 'applied': 0, 'conflicts': len(conflicts), 'branch': None,
                        'message': 'nothing to publish: every change conflicts with main'}
            mapping = backlog.assign_ids(bl, [o['tmp'] for o in applied if o['op'] == 'add'])
            with open(path, 'w', encoding='utf-8') as f:
                f.write(backlog.serialise(bl))
            title = self.title_for(applied)
            self.git('add', self.backlog_path, cwd=wt)
            self.git('commit', '-q', '-m', title, cwd=wt)
            self.git('push', '-q', '-u', 'origin', 'HEAD', cwd=wt)
            pushed = True
            body = '\n'.join(['Backlog edits made in the foreman UI, replayed onto '
                              f'`{self.branch}` at `{base_sha[:9]}`.', '']
                             + [f'- {backlog.describe(o, mapping)}' for o in applied]
                             + ([f'\nNot included ({len(conflicts)} changed on main since they were made; still pending in the UI):']
                                + [f'- {backlog.describe(o)}' for o in conflicts] if conflicts else []))
            p = subprocess.run(['gh', 'pr', 'create', '--base', self.branch, '--head', branch, '--title', title, '--body', body],
                               cwd=wt, capture_output=True, text=True)
            if p.returncode:
                raise PublishError(f'gh pr create: {p.stderr.strip() or p.stdout.strip()}')
            url = (p.stdout.strip().splitlines() or [''])[-1]
            self.save_ops(conflicts)
            pushed = False  # success: the remote branch stays for the PR
            return {'pr_url': url, 'applied': len(applied), 'conflicts': len(conflicts), 'branch': branch, 'message': ''}
        except FileNotFoundError as e:
            raise PublishError(str(e))
        finally:
            if pushed:
                self.git('push', '-q', 'origin', '--delete', branch, check=False)
            if wt and os.path.exists(wt):
                self.git('worktree', 'remove', '--force', wt, check=False)
            if branch:
                self.git('branch', '-D', branch, check=False)
            self.publishing.release()

    @staticmethod
    def title_for(applied):
        ids = {o['id'] for o in applied if o['op'] != 'add'}
        adds = sum(1 for o in applied if o['op'] == 'add')
        parts = ([f'triage {len(ids)} row{"s" if len(ids) != 1 else ""}'] if ids else []) + ([f'add {adds}'] if adds else [])
        return 'docs(backlog): ' + ', '.join(parts)
```

The `finally` deletes the remote branch only when the push happened and a later step failed (`pushed` is reset to `False` on success). It always removes the worktree and the local branch.

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m unittest tests/test_ui_api.py -v`
Expected: 15 tests OK

- [ ] **Step 5: Mutation check**

Delete `self.save_ops(conflicts)` and confirm `test_publish_creates_branch_with_only_those_lines` fails. Restore it.

Change the `if pushed:` block to `if False:` and confirm `test_publish_failure_cleans_up` fails. Restore it.

- [ ] **Step 6: Commit**

```bash
git add scripts/ui.py tests/test_ui_api.py
git commit -m "ui.py: publish pending edits as a PR from a temporary worktree; progress from merge dates"
```

---

### Task 7: Page shell, styles, pure helpers, store and API client

**Files:**
- Create: `ui/index.html` (replace the stub), `ui/app.css`, `ui/js/api.js`, `ui/js/md.js`, `ui/js/state.js`, `ui/js/actions.js`, `ui/js/dialogs.js`, `ui/js/main.js`
- Test: `tests/test_ui_js.mjs`

**Interfaces:**
- Produces:
  - `md.js`:
    - `esc(s)`
    - `inline(s)` (unescapes `\|`, then renders `` `code` `` and `**bold**`, HTML-escaped first)
    - `splitNotes(acceptance) -> [{label, html}]`
    - `tags(title) -> string[]`
    - `prOf(statusText) -> string`
    - `ago(epochSeconds, now = Date.now()) -> string`
  - `state.js`:
    - `state`
    - `set(patch)`
    - `subscribe(fn)`
    - `rows()`
    - `readyQueue()`
    - `byId(id)`
    - `filterRows(rows, filters) -> rows`
    - `triageList(rows, days, now = Date.now()) -> rows`
  - `actions.js`:
    - `load(refresh)`
    - `addOp(op)`
    - `undo()`
    - `drop(i)`
    - `rebase(i)`
    - `moveTop(id)`
    - `moveBottom(id)`
    - `moveRel(id, 'before' | 'after', anchorId)`
    - `setStatus(id, to)`, which asks for a reason when needed
    - `publish()`
  - `dialogs.js`:
    - `ask(title, placeholder) -> Promise<string | null>`
    - `openPalette()`
    - `openHelp()`
  - `main.js` registers views from `views/*.js`. Each exports `render(root)` and optionally `onKey(e) -> boolean`.

- [ ] **Step 1: Write the failing JS tests**

```js
// tests/test_ui_js.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { esc, inline, splitNotes, tags, prOf, ago } from '../ui/js/md.js';
import { filterRows, triageList } from '../ui/js/state.js';

test('notes escape html', () => {
  assert.equal(esc('<script>"x"</script>'), '&lt;script&gt;&quot;x&quot;&lt;/script&gt;');
  assert.equal(inline('a \\| b `<b>` **bold**'), 'a | b <code>&lt;b&gt;</code> <b>bold</b>');
});

test('splitNotes labels the original row and each marker block', () => {
  const blocks = splitNotes('Base text. **DONE (#12):** shipped it. **Correction (#13):** fixed. **#14 (partial):** half.');
  assert.deepEqual(blocks.map((b) => b.label), ['Original row', 'DONE (#12)', 'Correction (#13)', '#14 (partial)']);
  assert.equal(blocks[1].html, 'shipped it.');
});

test('tags and PR', () => {
  assert.deepEqual(tags('Fix the thing [serial] [e2e]'), ['serial', 'e2e']);
  assert.equal(prOf('done (#651)'), '651');
  assert.equal(prOf('ready'), '');
});

test('ago', () => {
  const now = Date.UTC(2026, 9, 2);
  assert.equal(ago(now / 1000 - 86400 * 3, now), '3 days');
  assert.equal(ago(null, now), 'unknown');
});

const ROWS = [
  { id: 'IB-1', title: 'Alpha [serial]', risk: 'low', status: 'ready', status_text: 'ready', acceptance: 'mentions foo.ts', source: '#10', touched: 100 },
  { id: 'IB-2', title: 'Beta', risk: 'high', status: 'ready', status_text: 'ready', acceptance: 'x', source: '#11', touched: 2_000_000_000 },
  { id: 'IB-3', title: 'Gamma', risk: 'low', status: 'done', status_text: 'done (#12)', acceptance: 'y', source: '#10', touched: 50 },
];

test('filterRows by text, status, risk, tag and PR', () => {
  const f = (patch) => filterRows(ROWS, { q: '', status: 'all', risk: 'any', tag: '', pr: '', ...patch }).map((r) => r.id);
  assert.deepEqual(f({ q: 'foo.ts' }), ['IB-1']);
  assert.deepEqual(f({ status: 'ready' }), ['IB-1', 'IB-2']);
  assert.deepEqual(f({ risk: 'high' }), ['IB-2']);
  assert.deepEqual(f({ tag: 'serial' }), ['IB-1']);
  assert.deepEqual(f({ pr: '10' }), ['IB-1', 'IB-3']);
});

test('triageList: ready rows untouched for N days, oldest first', () => {
  const now = 2_000_100_000 * 1000;
  assert.deepEqual(triageList(ROWS, 14, now).map((r) => r.id), ['IB-1']);
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `node --test tests/test_ui_js.mjs`
Expected: FAIL, `Cannot find module '.../ui/js/md.js'`

- [ ] **Step 3: Write `ui/js/md.js` and `ui/js/state.js`**

```js
// ui/js/md.js: pure text helpers (no DOM), unit-tested with node --test.
export const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

export function inline(s) {
  return esc(String(s ?? '').replace(/\\\|/g, '|'))
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
}

const MARK = /(?=\*\*(?:DONE \(#\d+\)|Correction|#\d+ \(|Depends on|Raised|PARTIAL|Unblocked|Left|Also)[^*]*\*\*)/;

export function splitNotes(acceptance) {
  return String(acceptance ?? '').split(MARK).map((part, i) => {
    const m = part.match(/^\*\*([^*]+?):?\*\*\s*/);
    if (i === 0 || !m) return { label: i === 0 ? 'Original row' : 'Note', html: inline(part.trim()) };
    return { label: m[1].replace(/:$/, ''), html: inline(part.slice(m[0].length).trim()) };
  }).filter((b) => b.html);
}

export const tags = (title) => [...String(title).matchAll(/\[([a-z0-9-]+)\]/gi)].map((m) => m[1]);
export const prOf = (statusText) => (String(statusText).match(/#(\d+)/) || [])[1] || '';

export function ago(epochSeconds, now = Date.now()) {
  if (!epochSeconds) return 'unknown';
  const days = Math.floor((now / 1000 - epochSeconds) / 86400);
  return days <= 0 ? 'today' : days === 1 ? '1 day' : `${days} days`;
}
```

```js
// ui/js/state.js: the store and pure selectors.
const listeners = new Set();

export const state = {
  view: 'queue', data: null, selectedId: null, error: '', busy: false, progress: null, published: null,
  filters: { q: '', status: 'ready', risk: 'any', tag: '', pr: '' },
  triage: { days: 14, index: 0 }, editing: null,
};

export function set(patch) {
  Object.assign(state, patch);
  listeners.forEach((fn) => fn(state));
}
export const subscribe = (fn) => listeners.add(fn);

export const rows = () => (state.data ? state.data.rows.filter((r) => !r.unparsed) : []);
export const readyQueue = () => rows().filter((r) => r.status === 'ready');
export const byId = (id) => rows().find((r) => r.id === id);

export function filterRows(list, f) {
  const q = f.q.trim().toLowerCase();
  return list.filter((r) =>
    (f.status === 'all' || (f.status === 'other' ? !['ready', 'deferred', 'blocked', 'done', 'cancelled'].includes(r.status) : r.status === f.status)) &&
    (f.risk === 'any' || r.risk === f.risk) &&
    (!f.tag || (r.title.match(/\[([a-z0-9-]+)\]/gi) || []).map((t) => t.slice(1, -1).toLowerCase()).includes(f.tag.toLowerCase())) &&
    (!f.pr || new RegExp(`#${f.pr}\\b`).test(`${r.status_text} ${r.source}`)) &&
    (!q || `${r.id} ${r.title} ${r.acceptance} ${r.source}`.toLowerCase().includes(q)));
}

export function triageList(list, days, now = Date.now()) {
  const cutoff = now / 1000 - days * 86400;
  return list.filter((r) => r.status === 'ready' && (!r.touched || r.touched < cutoff))
    .sort((a, b) => (a.touched || 0) - (b.touched || 0));
}
```

- [ ] **Step 4: Run the JS tests**

Run: `node --test tests/test_ui_js.mjs`
Expected: 6 tests pass

- [ ] **Step 5: Write `ui/js/api.js`, `ui/js/actions.js`, `ui/js/dialogs.js`**

```js
// ui/js/api.js
async function call(method, path, body) {
  const res = await fetch(path, {
    method, credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${method} ${path} failed (${res.status})`);
  return data;
}
export const api = {
  backlog: (refresh) => call('GET', `/api/backlog${refresh ? '?refresh=1' : ''}`),
  addOp: (op) => call('POST', '/api/ops', op),
  undo: () => call('POST', '/api/ops/undo'),
  drop: (i) => call('DELETE', `/api/ops/${i}`),
  rebase: (i) => call('POST', `/api/ops/${i}/rebase`),
  progress: () => call('GET', '/api/progress'),
  publish: () => call('POST', '/api/publish'),
};
```

```js
// ui/js/actions.js
import { api } from './api.js';
import { set } from './state.js';
import { ask } from './dialogs.js';

async function run(fn) {
  set({ busy: true });
  try {
    const data = await fn();
    if (data && data.rows) set({ data, error: '' });
    return data;
  } catch (e) {
    set({ error: e.message });
    return null;
  } finally {
    set({ busy: false });
  }
}

export const load = (refresh = false) => run(() => api.backlog(refresh));
export const addOp = (op) => run(() => api.addOp(op));
export const undo = () => run(() => api.undo());
export const drop = (i) => run(() => api.drop(i));
export const rebase = (i) => run(() => api.rebase(i));
export const moveTop = (id) => addOp({ op: 'move', id, to: 'top' });
export const moveBottom = (id) => addOp({ op: 'move', id, to: 'bottom' });
export const moveRel = (id, where, anchor) => addOp({ op: 'move', id, to: { [where]: anchor } });

export async function setStatus(id, to) {
  let reason = '';
  if (to === 'blocked' || to === 'cancelled' || to === 'deferred') {
    reason = await ask(`${to === 'deferred' ? 'Defer' : to === 'blocked' ? 'Block' : 'Cancel'} ${id}`,
      to === 'deferred' ? 'Why (optional)' : 'Why (required)');
    if (reason === null) return null;
    if (to !== 'deferred' && !reason.trim()) { set({ error: `${to} needs a reason` }); return null; }
  }
  return addOp({ op: 'status', id, to, reason });
}

export async function publish() {
  set({ busy: true });
  try {
    const res = await api.publish();
    set({ published: res, error: '' });
    await load(true);
    return res;
  } catch (e) {
    set({ error: e.message });
    return null;
  } finally {
    set({ busy: false });
  }
}
```

```js
// ui/js/dialogs.js
import { esc } from './md.js';
import { state, set, rows, byId } from './state.js';
import * as A from './actions.js';

export function ask(title, placeholder) {
  const d = document.getElementById('ask');
  d.querySelector('h2').textContent = title;
  const input = d.querySelector('input');
  input.value = '';
  input.placeholder = placeholder;
  d.showModal();
  input.focus();
  return new Promise((resolve) => {
    d.onclose = () => resolve(d.returnValue === 'ok' ? input.value : null);
  });
}

export function openHelp() {
  document.getElementById('help').showModal();
}

const COMMANDS = [
  ['IB-123', 'Jump to a row'],
  ['defer IB-1 IB-2 [reason]', 'Defer rows'],
  ['block IB-1 reason', 'Block a row'],
  ['cancel IB-1 reason', 'Cancel a row'],
  ['ready IB-1', 'Make a row ready again'],
  ['move IB-1 top|bottom', 'Reorder'],
  ['new', 'File a new row'],
  ['publish', 'Review and publish'],
  ['refresh', 'Fetch main again'],
  ['show ready|blocked|deferred|done|all', 'Filter All rows'],
  ['theme auto|light|dark', 'Colour theme'],
];

export function openPalette() {
  const d = document.getElementById('palette');
  const input = d.querySelector('input');
  const list = d.querySelector('ul');
  const prefix = state.data ? state.data.prefix : 'IB';
  const draw = () => {
    const q = input.value.trim().toLowerCase();
    const ids = q ? rows().filter((r) => r.id.toLowerCase().startsWith(q)).slice(0, 6).map((r) => [r.id, r.title]) : [];
    const cmds = COMMANDS.filter(([c]) => !q || c.startsWith(q.split(' ')[0]));
    list.innerHTML = [...ids, ...cmds].map(([c, h]) => `<li><code>${esc(c)}</code> <span>${esc(h)}</span></li>`).join('');
  };
  input.value = '';
  input.oninput = draw;
  draw();
  d.showModal();
  input.focus();
  d.onclose = () => { if (d.returnValue === 'ok') runCommand(input.value.trim(), prefix); };
}

export async function runCommand(text, prefix) {
  const [verb, ...rest] = text.split(/\s+/);
  const idRe = new RegExp(`^${prefix}-\\d+$`, 'i');
  const ids = rest.filter((w) => idRe.test(w)).map((w) => w.toUpperCase());
  const reason = rest.filter((w) => !idRe.test(w)).join(' ');
  if (idRe.test(verb)) {
    const r = byId(verb.toUpperCase());
    if (!r) return set({ error: `${verb} not found` });
    return set({ view: r.status === 'ready' ? 'queue' : 'rows', selectedId: r.id });
  }
  if (['defer', 'block', 'cancel', 'ready'].includes(verb)) {
    const to = { defer: 'deferred', block: 'blocked', cancel: 'cancelled', ready: 'ready' }[verb];
    if ((to === 'blocked' || to === 'cancelled') && !reason) return set({ error: `${verb} needs a reason` });
    for (const id of ids) await A.addOp({ op: 'status', id, to, reason });
    return;
  }
  if (verb === 'move' && ids[0]) return rest.includes('bottom') ? A.moveBottom(ids[0]) : A.moveTop(ids[0]);
  if (verb === 'new') return set({ view: 'edit', editing: { mode: 'new' } });
  if (verb === 'publish') return set({ view: 'publish' });
  if (verb === 'refresh') return A.load(true);
  if (verb === 'show') return set({ view: 'rows', filters: { ...state.filters, status: rest[0] || 'all' } });
  if (verb === 'theme') return window.foremanTheme(rest[0] || 'auto');
  return set({ error: `Unknown command "${text}". Press ? for help.` });
}
```

- [ ] **Step 6: Write `ui/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>foreman backlog</title>
<script>try{const t=localStorage.getItem('foreman-theme');if(t==='light'||t==='dark')document.documentElement.dataset.theme=t}catch(e){}</script>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Source+Serif+4:opsz,wght@8..60,500;8..60,600&family=Public+Sans:wght@400;500;600&display=swap">
<link rel="stylesheet" href="/ui/app.css">
</head>
<body>
<div class="app">
  <header class="top">
    <span class="brand">foreman</span>
    <nav class="tabs" id="tabs" aria-label="Screens">
      <button data-v="queue">Queue</button><button data-v="rows">All rows</button><button data-v="triage">Triage</button><button data-v="progress">Progress</button>
    </nav>
    <button class="cmd" id="cmd" aria-label="Open the command palette">⌘K&nbsp; Jump to a row, filter, or run a command…<kbd>⌘K</kbd></button>
    <div class="theme" role="group" aria-label="Color theme">
      <button data-t="auto" title="Follow the system setting">◐ Auto</button><button data-t="light">☀ Light</button><button data-t="dark">☾ Dark</button>
    </div>
  </header>
  <div class="banner" id="banner" role="alert" hidden></div>
  <section class="stats" id="stats"></section>
  <main id="view"></main>
  <footer class="pending" id="pending" hidden></footer>
</div>
<dialog id="ask"><form method="dialog"><h2></h2><input name="reason" autocomplete="off"><div class="dlg-actions"><button value="cancel">Cancel</button><button value="ok" class="primary">OK</button></div></form></dialog>
<dialog id="palette" class="palette"><form method="dialog"><input aria-label="Command" autocomplete="off" placeholder="IB-123, defer IB-1 reason, new, publish…"><ul></ul><button value="ok" hidden>Run</button></form></dialog>
<dialog id="help"><form method="dialog"><h2>Keyboard</h2>
<dl class="keys-help"><dt>⌘K / Ctrl+K</dt><dd>Command palette</dd><dt>j / k</dt><dd>Next / previous row</dd><dt>⌥↑ / ⌥↓</dt><dd>Move the row up / down</dd><dt>t</dt><dd>Move to top</dd><dt>e</dt><dd>Edit</dd><dt>d / b / x</dt><dd>Defer / block / cancel</dd><dt>n</dt><dd>New row</dd><dt>u</dt><dd>Undo the last change</dd><dt>p</dt><dd>Review & publish</dd><dt>?</dt><dd>This help</dd></dl>
<div class="dlg-actions"><button value="ok" class="primary">Close</button></div></form></dialog>
<script type="module" src="/ui/js/main.js"></script>
</body>
</html>
```

- [ ] **Step 7: Write `ui/app.css`**

Start from the approved mockups. Tokens and the dark variants are copied verbatim from `ledger-dark-v5.html`; the screen styles come from `screens-v8.html`.

```css
:root{--bg:#f6f7f5;--ink:#1d2430;--muted:#5d6675;--faint:#b7bdc6;--line:#dfe3e6;--rowline:#e9ecee;--chip:#eceff1;--field:#fff;--btn:#fff;--card:#fff;--selbg:#fff;
 --accent:#2b4fd6;--onaccent:#fff;--accentink:#2b4fd6;--pendbg:#e6ecff;--pendink:#2b4fd6;--barbg:#fff;
 --lowbg:#e5f2ea;--lowink:#1f6b44;--medbg:#fbf0d9;--medink:#8a5a00;--hibg:#fbe4e2;--hiink:#a12a22;color-scheme:light;
 --display:"Source Serif 4",Georgia,serif;--body:"Public Sans",system-ui,sans-serif;--mono:"IBM Plex Mono",ui-monospace,monospace;--r:8px}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#14171c;--ink:#e7e9ed;--muted:#9aa3b1;--faint:#4d5563;--line:#2a3039;--rowline:#21262e;--chip:#222831;--field:#1a1e24;--btn:#1c2128;--card:#191d23;--selbg:#1c2129;
 --accent:#8aa4ff;--onaccent:#0e1426;--accentink:#9db3ff;--pendbg:#1e2846;--pendink:#a9bcff;--barbg:#171b21;--lowbg:#16271e;--lowink:#86d3a8;--medbg:#2c2414;--medink:#f0c873;--hibg:#301a1a;--hiink:#f39b92;color-scheme:dark}}
:root[data-theme=dark]{--bg:#14171c;--ink:#e7e9ed;--muted:#9aa3b1;--faint:#4d5563;--line:#2a3039;--rowline:#21262e;--chip:#222831;--field:#1a1e24;--btn:#1c2128;--card:#191d23;--selbg:#1c2129;
 --accent:#8aa4ff;--onaccent:#0e1426;--accentink:#9db3ff;--pendbg:#1e2846;--pendink:#a9bcff;--barbg:#171b21;--lowbg:#16271e;--lowink:#86d3a8;--medbg:#2c2414;--medink:#f0c873;--hibg:#301a1a;--hiink:#f39b92;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 var(--body);background:var(--bg);color:var(--ink)}
button{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
code{font-family:var(--mono);font-size:.88em;padding:1px 4px;border-radius:4px;background:var(--chip)}
kbd{font-family:var(--mono);font-size:11px;padding:1px 5px;border:1px solid var(--line);border-bottom-width:2px;border-radius:4px;color:var(--muted);background:var(--field)}
.app{min-height:100vh;display:grid;grid-template-rows:auto auto auto 1fr auto}
.top{display:flex;align-items:center;gap:14px;padding:12px 20px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.brand{font-family:var(--display);font-weight:600;font-size:16px}
.tabs{display:flex;gap:4px}
.tabs button{border:0;background:none;padding:6px 10px;border-radius:var(--r);color:var(--muted);cursor:pointer}
.tabs button[aria-current=page]{background:var(--chip);color:var(--ink)}
.cmd{margin-left:auto;display:flex;align-items:center;gap:8px;min-width:280px;padding:7px 10px;border:1px solid var(--line);border-radius:var(--r);background:var(--field);color:var(--muted);font:12.5px var(--mono);cursor:pointer}
.cmd kbd{margin-left:auto}
.theme{display:flex;border:1px solid var(--line);border-radius:999px;padding:2px;background:var(--field)}
.theme button{border:0;background:none;color:var(--muted);padding:5px 10px;border-radius:999px;cursor:pointer;font-size:12px}
.theme button[aria-pressed=true]{background:var(--chip);color:var(--ink)}
.banner{padding:10px 20px;background:var(--hibg);color:var(--hiink);border-bottom:1px solid var(--line)}
.banner.info{background:var(--pendbg);color:var(--pendink)}
.stats{display:flex;gap:28px;padding:12px 20px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums;flex-wrap:wrap}
.stat .n{font-family:var(--display);font-size:22px;font-weight:600}
.stat .l{font-size:11.5px;color:var(--muted)}
.stat .bar{display:flex;height:4px;width:120px;border-radius:2px;overflow:hidden;margin-top:6px;background:var(--chip)}
.main{display:grid;grid-template-columns:minmax(380px,1.05fr) 1fr;min-height:0}
.queue{border-right:1px solid var(--line);overflow:auto;max-height:calc(100vh - 170px)}
.qhead{display:flex;align-items:center;gap:10px;padding:12px 20px 8px;color:var(--muted);font-size:12px;flex-wrap:wrap}
.qhead h2{margin:0;font-family:var(--display);font-size:18px;color:var(--ink)}
.row{display:grid;grid-template-columns:22px 34px 1fr auto;gap:10px;align-items:start;padding:10px 20px;border-top:1px solid var(--rowline);cursor:pointer;background:none;border-left:0;border-right:0;border-bottom:0;text-align:left;width:100%}
.row .grip{color:var(--faint);cursor:grab}
.row .pos{font-family:var(--display);font-size:16px;color:var(--muted);font-variant-numeric:tabular-nums}
.row .id{font-family:var(--mono);font-size:12px;color:var(--muted);margin-right:6px}
.row .meta{font-size:12px;color:var(--muted);margin-top:2px}
.row.sel{background:var(--selbg);box-shadow:inset 3px 0 0 var(--accent)}
.row.drop-before{box-shadow:inset 0 2px 0 var(--accent)}
.row.drop-after{box-shadow:inset 0 -2px 0 var(--accent)}
.edited{margin-left:8px;font-size:11px;padding:1px 6px;border-radius:10px;background:var(--pendbg);color:var(--pendink)}
.optin{font-size:11px;color:var(--hiink);margin-left:6px}
.risk{font-family:var(--mono);font-size:11px;padding:2px 7px;border-radius:10px;white-space:nowrap}
.risk.low{background:var(--lowbg);color:var(--lowink)}.risk.medium{background:var(--medbg);color:var(--medink)}.risk.high{background:var(--hibg);color:var(--hiink)}
.st{font-size:12px;padding:2px 8px;border-radius:10px;background:var(--chip);color:var(--muted);white-space:nowrap}
.st.ready{background:var(--pendbg);color:var(--pendink)}.st.done{background:var(--lowbg);color:var(--lowink)}.st.blocked,.st.cancelled{background:var(--hibg);color:var(--hiink)}.st.deferred{background:var(--medbg);color:var(--medink)}
.detail{overflow:auto;padding:20px 28px;max-height:calc(100vh - 170px)}
.crumb{font-family:var(--mono);font-size:12px;color:var(--muted)}
.detail h1,.triage h1{font-family:var(--display);font-size:26px;line-height:1.25;margin:6px 0 10px;font-weight:600;text-wrap:balance;max-width:60ch}
.chips{display:flex;gap:8px;align-items:center;margin-bottom:14px;color:var(--muted);font-size:12px;flex-wrap:wrap}
.actions{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 18px}
.actions button,.btn{display:inline-flex;gap:8px;align-items:center;padding:6px 11px;border-radius:var(--r);border:1px solid var(--line);background:var(--btn);cursor:pointer;font-weight:500;font-size:13px}
.actions button.primary,.btn.primary,.primary{background:var(--accent);border-color:var(--accent);color:var(--onaccent)}
.notes{max-width:68ch;line-height:1.6}
.notes .block{padding:12px 14px;border:1px solid var(--line);border-radius:var(--r);margin-bottom:10px;background:var(--card)}
.notes .lbl{font-size:11.5px;font-family:var(--mono);color:var(--accentink);margin-bottom:4px}
.pending{display:flex;align-items:center;gap:14px;padding:10px 20px;border-top:1px solid var(--line);background:var(--barbg);position:sticky;bottom:0}
.pending .list{color:var(--muted);font:12.5px var(--mono);overflow:hidden;white-space:nowrap;text-overflow:ellipsis;flex:1}
.toolbar{display:flex;gap:8px;align-items:center;padding:12px 20px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.toolbar input,.toolbar select{font:13px var(--body);padding:6px 10px;border:1px solid var(--line);border-radius:var(--r);background:var(--field);color:var(--ink)}
.toolbar input[type=search]{flex:1;min-width:220px}
.seg{display:flex;border:1px solid var(--line);border-radius:var(--r);overflow:hidden}
.seg button{border:0;border-right:1px solid var(--line);background:none;padding:6px 10px;font-size:12.5px;color:var(--muted);cursor:pointer}
.seg button:last-child{border-right:0}.seg button[aria-pressed=true]{background:var(--chip);color:var(--ink)}
.split{display:grid;grid-template-columns:1fr 420px}
table.rows{width:100%;border-collapse:collapse;font-size:13.5px}
table.rows th{text-align:left;font-weight:500;color:var(--muted);font-size:12px;padding:8px 12px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg)}
table.rows td{padding:9px 12px;border-bottom:1px solid var(--rowline);vertical-align:top;cursor:pointer}
table.rows tr.sel td{background:var(--selbg)}
table.rows td.id,.pr{font-family:var(--mono);font-size:12px;color:var(--muted);white-space:nowrap}
.pr{color:var(--accentink)}
.drawer{border-left:1px solid var(--line);padding:20px;overflow:auto;max-height:calc(100vh - 230px)}
.wrap{max-width:860px;margin:0 auto;padding:28px 20px}
.card{border:1px solid var(--line);border-radius:12px;background:var(--card);padding:24px 28px}
.prog{display:flex;justify-content:space-between;color:var(--muted);font-size:12.5px;margin-bottom:10px}
.meter{height:4px;background:var(--chip);border-radius:2px;margin-bottom:18px;overflow:hidden}.meter i{display:block;height:100%;background:var(--accent)}
.keygrid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:20px}
.keygrid button{border:1px solid var(--line);border-radius:var(--r);padding:10px;background:var(--btn);cursor:pointer;font-size:13px}
.keygrid kbd{display:block;margin:0 auto 6px;width:max-content;font-size:13px}
.keygrid .warn{color:var(--hiink)}
.chart{border:1px solid var(--line);border-radius:12px;background:var(--card);padding:18px 20px;margin:20px}
.chart h3{margin:0 0 4px;font-family:var(--display);font-size:18px}
.chart p{margin:0 0 14px;color:var(--muted);font-size:12.5px}
.legend{display:flex;gap:16px;font-size:12px;color:var(--muted);margin-top:8px}.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}
.form{display:grid;gap:16px}
.form label{display:grid;gap:6px;font-size:12.5px;color:var(--muted)}
.form input,.form textarea,.form select{font:14px var(--body);color:var(--ink);background:var(--field);border:1px solid var(--line);border-radius:var(--r);padding:9px 11px}
.form textarea{min-height:150px;font:12.5px/1.55 var(--mono)}
.form .three{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
.hint{font-size:12px;color:var(--muted)}.ok{color:var(--lowink)}.bad{color:var(--hiink)}
.op{display:grid;grid-template-columns:auto 1fr auto;gap:12px;align-items:start;padding:14px 16px;border:1px solid var(--line);border-radius:var(--r);background:var(--card);margin-bottom:10px}
.op .k{font:11.5px var(--mono);padding:2px 8px;border-radius:10px;background:var(--chip);color:var(--muted)}
.op.conflict{border-color:var(--hiink)}.op.conflict .k{color:var(--hiink)}
dialog{border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink);padding:20px;min-width:min(520px,92vw)}
dialog::backdrop{background:rgb(0 0 0 / .35)}
dialog h2{margin:0 0 12px;font-family:var(--display);font-size:18px}
dialog input{width:100%;font:14px var(--body);padding:9px 11px;border:1px solid var(--line);border-radius:var(--r);background:var(--field);color:var(--ink)}
.palette ul{list-style:none;margin:10px 0 0;padding:0;max-height:300px;overflow:auto}.palette li{padding:6px 4px;color:var(--muted)}.palette li code{color:var(--ink)}
.dlg-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}
.dlg-actions button{padding:6px 12px;border-radius:var(--r);border:1px solid var(--line);background:var(--btn);cursor:pointer}
.keys-help{display:grid;grid-template-columns:auto 1fr;gap:6px 16px;font-size:13px}.keys-help dt{font-family:var(--mono)}
@media (max-width:900px){.main,.split{grid-template-columns:1fr}.queue{border-right:0;max-height:none}.detail,.drawer{max-height:none}.cmd{min-width:0;flex:1}.keygrid,.form .three{grid-template-columns:repeat(2,1fr)}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
```

- [ ] **Step 8: Write `ui/js/main.js`**

```js
// ui/js/main.js: boot, header, stats, pending bar, tabs, keyboard.
import { state, set, subscribe, rows, readyQueue } from './state.js';
import { esc } from './md.js';
import * as A from './actions.js';
import { openPalette, openHelp } from './dialogs.js';
import * as queue from './views/queue.js';
import * as rowsView from './views/rows.js';
import * as triage from './views/triage.js';
import * as progress from './views/progress.js';
import * as edit from './views/edit.js';
import * as publish from './views/publish.js';

const VIEWS = { queue, rows: rowsView, triage, progress, edit, publish };
const KEY = 'foreman-theme';

window.foremanTheme = (t) => {
  if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
  document.querySelectorAll('.theme button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.t === (t || 'auto'))));
  try { t === 'light' || t === 'dark' ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY); } catch (e) { /* storage blocked */ }
};

function renderStats() {
  const all = rows();
  const count = (s) => all.filter((r) => r.status === s).length;
  const ready = readyQueue();
  const pct = (k) => `${(ready.filter((r) => r.risk === k).length / Math.max(ready.length, 1)) * 100}%`;
  document.getElementById('stats').innerHTML = `
    <div class="stat"><div class="n">${ready.length}</div><div class="l">Ready</div><div class="bar"><i style="width:${pct('low')};background:var(--lowink)"></i><i style="width:${pct('medium')};background:var(--medink)"></i><i style="width:${pct('high')};background:var(--hiink)"></i></div></div>
    <div class="stat"><div class="n">${count('done')}</div><div class="l">Done</div></div>
    <div class="stat"><div class="n">${count('deferred')}</div><div class="l">Deferred</div></div>
    <div class="stat"><div class="n">${count('blocked')}</div><div class="l">Blocked</div></div>
    <div class="stat"><div class="n">${count('cancelled')}</div><div class="l">Cancelled</div></div>`;
}

function renderPending() {
  const el = document.getElementById('pending');
  const ops = state.data ? state.data.ops : [];
  el.hidden = ops.length === 0;
  if (!ops.length) return;
  const conflicts = state.data.results.filter((r) => r.result !== 'applied').length;
  const text = ops.map((o) => (o.op === 'add' ? `new "${o.title}"` : o.op === 'move' ? `${o.id} moved` : o.op === 'status' ? `${o.id} → ${o.to}` : `${o.id} ${o.field}`)).join(' · ');
  el.innerHTML = `<span><b>${ops.length}</b> unpublished${conflicts ? ` · <span class="bad">${conflicts} conflict${conflicts > 1 ? 's' : ''}</span>` : ''}</span>
    <span class="list">${esc(text)}</span>
    <button class="btn" id="undo-last">Undo <kbd>u</kbd></button>
    <button class="btn primary" id="go-publish">Review & publish <kbd>p</kbd></button>`;
  el.querySelector('#undo-last').onclick = () => A.undo();
  el.querySelector('#go-publish').onclick = () => set({ view: 'publish' });
}

function renderBanner() {
  const b = document.getElementById('banner');
  const msg = state.error || (state.data && state.data.notice) || '';
  b.hidden = !msg;
  b.className = state.error ? 'banner' : 'banner info';
  b.textContent = msg;
}

function render() {
  document.querySelectorAll('#tabs button').forEach((b) => b.setAttribute('aria-current', b.dataset.v === state.view ? 'page' : 'false'));
  renderBanner();
  if (!state.data) {
    document.getElementById('view').innerHTML = `<p class="wrap hint">${state.error ? '' : 'Fetching the backlog from origin…'}</p>`;
    return;
  }
  renderStats();
  renderPending();
  VIEWS[state.view].render(document.getElementById('view'));
}

subscribe(render);

document.getElementById('tabs').addEventListener('click', (e) => {
  const v = e.target.closest('button')?.dataset.v;
  if (v) set({ view: v });
});
document.getElementById('cmd').onclick = openPalette;
document.querySelectorAll('.theme button').forEach((b) => { b.onclick = () => window.foremanTheme(b.dataset.t); });
let saved = 'auto';
try { saved = localStorage.getItem(KEY) || 'auto'; } catch (e) { /* storage blocked */ }
window.foremanTheme(saved);

document.addEventListener('keydown', (e) => {
  if (document.querySelector('dialog[open]')) return;
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); return; }
  if (e.target.closest('input, textarea, select') || e.metaKey || e.ctrlKey) return;
  const view = VIEWS[state.view];
  if (view.onKey && view.onKey(e)) { e.preventDefault(); return; }
  if (e.key === '?') { openHelp(); e.preventDefault(); }
  else if (e.key === 'u') A.undo();
  else if (e.key === 'n') set({ view: 'edit', editing: { mode: 'new' } });
  else if (e.key === 'p') set({ view: 'publish' });
});

A.load(true);
```

- [ ] **Step 9: Run the JS tests again and commit**

Run: `node --test tests/test_ui_js.mjs`
Expected: 6 pass. (The view modules don't exist yet; `main.js` is only loaded by the browser.)

```bash
git add ui tests/test_ui_js.mjs
git commit -m "ui: page shell, Ledger styles with Auto/Light/Dark, store, API client, dialogs"
```

---

### Task 8: Queue screen (with the shared detail renderer)

**Files:**
- Create: `ui/js/views/queue.js`

**Interfaces:**
- Consumes: `state/set/readyQueue/byId` (state.js), `inline/splitNotes/esc/ago` (md.js), and the action helpers (actions.js).
- Produces:
  - `render(root)`;
  - `onKey(e) -> boolean`;
  - `detailHtml(row, positionText) -> string`;
  - `bindDetail(root, row)`, which wires the action buttons in a rendered detail.

- [ ] **Step 1: Write `ui/js/views/queue.js`**

```js
import { state, set, readyQueue, byId } from '../state.js';
import { esc, inline, splitNotes, ago } from '../md.js';
import * as A from '../actions.js';

export function detailHtml(r, positionText) {
  const blocks = splitNotes(r.acceptance).map((b) => `<div class="block"><div class="lbl">${esc(b.label)}</div>${b.html}</div>`).join('');
  const opt = r.status === 'ready' && r.risk === 'high' ? '<span class="optin">opt-in: the loop skips it</span>' : '';
  return `<div class="crumb">${esc(r.id)}${positionText ? ` · ${positionText}` : ''} · last touched ${ago(r.touched)} ago</div>
    <h1>${inline(r.title)}</h1>
    <div class="chips"><span class="risk ${esc(r.risk)}">${esc(r.risk)}</span><span class="st ${esc(r.status)}">${esc(r.status_text)}</span>${opt}<span>· from ${inline(r.source)}</span></div>
    <div class="actions">
      <button class="primary" data-act="edit">Edit <kbd>e</kbd></button>
      ${r.status === 'ready' ? '<button data-act="top">Move to top <kbd>t</kbd></button>' : '<button data-act="ready">Make ready</button>'}
      <button data-act="deferred">Defer <kbd>d</kbd></button>
      <button data-act="blocked">Block… <kbd>b</kbd></button>
      <button data-act="cancelled">Cancel… <kbd>x</kbd></button>
    </div>
    <div class="notes">${blocks}</div>`;
}

export function bindDetail(root, r) {
  root.querySelectorAll('[data-act]').forEach((b) => {
    b.onclick = () => act(b.dataset.act, r);
  });
}

function act(what, r) {
  if (what === 'edit') return set({ view: 'edit', editing: { mode: 'edit', id: r.id } });
  if (what === 'top') return A.moveTop(r.id);
  return A.setStatus(r.id, what);
}

function selected() {
  const q = readyQueue();
  return byId(state.selectedId) && byId(state.selectedId).status === 'ready' ? byId(state.selectedId) : q[0];
}

export function render(root) {
  const q = readyQueue();
  const sel = selected();
  root.innerHTML = `<div class="main">
    <div class="queue" id="queue" role="listbox" aria-label="Ready queue">
      <div class="qhead"><h2>Ready queue</h2><span>${q.length} rows · the loop takes the top first · drag or <kbd>⌥↑</kbd><kbd>⌥↓</kbd></span></div>
      ${q.map((r) => `<div class="row${sel && r.id === sel.id ? ' sel' : ''}" role="option" aria-selected="${sel && r.id === sel.id}" draggable="true" data-id="${esc(r.id)}" tabindex="-1">
        <span class="grip" aria-hidden="true">⠿</span><span class="pos">${r.position}</span>
        <div><div class="t"><span class="id">${esc(r.id)}</span>${inline(r.title)}${r.pending ? '<span class="edited">edited</span>' : ''}${r.risk === 'high' ? '<span class="optin">opt-in</span>' : ''}</div><div class="meta">${inline(r.source)}</div></div>
        <span class="risk ${esc(r.risk)}">${esc(r.risk)}</span></div>`).join('') || '<p class="wrap hint">No ready rows.</p>'}
    </div>
    <article class="detail" id="detail">${sel ? detailHtml(sel, `position ${sel.position} of ${q.length}`) : ''}</article></div>`;
  if (sel) bindDetail(root.querySelector('#detail'), sel);
  const list = root.querySelector('#queue');
  list.querySelectorAll('.row').forEach((el) => {
    el.onclick = () => set({ selectedId: el.dataset.id });
    el.ondragstart = (e) => { e.dataTransfer.setData('text/plain', el.dataset.id); };
    el.ondragover = (e) => {
      e.preventDefault();
      const after = e.offsetY > el.offsetHeight / 2;
      el.classList.toggle('drop-after', after);
      el.classList.toggle('drop-before', !after);
    };
    el.ondragleave = () => el.classList.remove('drop-after', 'drop-before');
    el.ondrop = (e) => {
      e.preventDefault();
      const id = e.dataTransfer.getData('text/plain');
      const after = el.classList.contains('drop-after');
      el.classList.remove('drop-after', 'drop-before');
      if (id && id !== el.dataset.id) A.moveRel(id, after ? 'after' : 'before', el.dataset.id);
    };
  });
  list.querySelector('.row.sel')?.scrollIntoView({ block: 'nearest' });
}

export function onKey(e) {
  const q = readyQueue();
  const sel = selected();
  if (!sel) return false;
  const i = q.findIndex((r) => r.id === sel.id);
  if (e.altKey && e.key === 'ArrowUp' && i > 0) { A.moveRel(sel.id, 'before', q[i - 1].id); return true; }
  if (e.altKey && e.key === 'ArrowDown' && i < q.length - 1) { A.moveRel(sel.id, 'after', q[i + 1].id); return true; }
  if (e.key === 'j' && i < q.length - 1) { set({ selectedId: q[i + 1].id }); return true; }
  if (e.key === 'k' && i > 0) { set({ selectedId: q[i - 1].id }); return true; }
  const map = { e: 'edit', t: 'top', d: 'deferred', b: 'blocked', x: 'cancelled' };
  if (map[e.key]) { act(map[e.key], sel); return true; }
  return false;
}
```

- [ ] **Step 2: Create minimal stub views so `main.js` loads.** Tasks 9–10 replace them. For each of `rows.js`, `triage.js`, `progress.js`, `edit.js` and `publish.js`:

```js
export function render(root) { root.innerHTML = '<p class="wrap hint">Coming in the next task.</p>'; }
```

- [ ] **Step 3: Check it in the browser**

Run: `python3 scripts/ui.py --repo /Users/crisis/_workspace/vacancy-vibe` (opens a tab)
Expected:
- The queue lists the ready rows in file order; numbers start at 1; high-risk rows read "opt-in".
- Clicking a row shows its notes as blocks ("Original row", "DONE (#…)").
- `j`/`k` move the selection.
- `t` adds a pending change: "1 unpublished" appears, and the row jumps to position 1 with an "edited" chip.
- `u` undoes it.
- Dragging a row onto another moves it.
- The theme switch works in Auto/Light/Dark.
- The console shows 0 errors.

Stop the server with Ctrl+C. Then `rm ~/.claude/foreman/*/pending.json`, so test edits don't linger.

- [ ] **Step 4: Commit**

```bash
git add ui/js/views
git commit -m "ui: Queue screen with notes blocks, reorder by drag and keys, status actions"
```

---

### Task 9: All rows, Triage and Progress screens

**Files:**
- Create: `ui/js/views/rows.js`, `ui/js/views/triage.js`, `ui/js/views/progress.js` (replacing the stubs)

**Interfaces:**
- Consumes:
  - `filterRows`, `triageList`, `rows`, `readyQueue`, `byId` (state.js);
  - `detailHtml`, `bindDetail` (queue.js);
  - `api.progress` (api.js);
  - `A.moveTop/moveBottom/setStatus/undo` (actions.js).

- [ ] **Step 1: Write `ui/js/views/rows.js`**

```js
import { state, set, rows, byId, filterRows } from '../state.js';
import { esc, inline, prOf, tags } from '../md.js';
import { detailHtml, bindDetail } from './queue.js';

const STATUSES = ['all', 'ready', 'deferred', 'blocked', 'done', 'cancelled', 'other'];

export function render(root) {
  const f = state.filters;
  const all = rows();
  const count = (s) => (s === 'all' ? all.length : filterRows(all, { q: '', status: s, risk: 'any', tag: '', pr: '' }).length);
  const list = filterRows(all, f);
  const sel = byId(state.selectedId);
  const allTags = [...new Set(all.flatMap((r) => tags(r.title)))].sort();
  const unparsed = state.data.rows.filter((r) => r.unparsed);
  root.innerHTML = `<div class="toolbar">
      <input type="search" id="q" placeholder="Search ids, titles, notes, file paths…" value="${esc(f.q)}" aria-label="Search">
      <div class="seg" role="group" aria-label="Status">${STATUSES.map((s) => `<button data-status="${s}" aria-pressed="${f.status === s}">${s[0].toUpperCase() + s.slice(1)} ${count(s)}</button>`).join('')}</div>
      <div class="seg" role="group" aria-label="Risk">${['any', 'low', 'medium', 'high'].map((k) => `<button data-risk="${k}" aria-pressed="${f.risk === k}">${k === 'any' ? 'Any risk' : k}</button>`).join('')}</div>
      <select id="tag" aria-label="Tag"><option value="">Any tag</option>${allTags.map((t) => `<option ${f.tag === t ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>
      <input id="pr" placeholder="PR #" value="${esc(f.pr)}" size="6" aria-label="Filed from or done in PR">
    </div>
    ${unparsed.length ? `<p class="banner">${unparsed.length} line(s) look like rows but have the wrong number of cells; they are shown as-is and never rewritten: ${unparsed.map((u) => `<code>${esc(u.unparsed.slice(0, 40))}</code>`).join(' ')}</p>` : ''}
    <div class="split"><div style="overflow:auto;max-height:calc(100vh - 230px)"><table class="rows"><thead><tr><th>Id</th><th>Title</th><th>Risk</th><th>Status</th><th>PR</th></tr></thead><tbody>
    ${list.slice(0, 400).map((r) => `<tr data-id="${esc(r.id)}" class="${sel && sel.id === r.id ? 'sel' : ''}"><td class="id">${esc(r.id)}</td><td>${inline(r.title)}${r.pending ? '<span class="edited">edited</span>' : ''}</td><td><span class="risk ${esc(r.risk)}">${esc(r.risk)}</span></td><td><span class="st ${esc(r.status)}">${esc(r.status)}</span></td><td class="pr">${prOf(r.status_text) ? '#' + prOf(r.status_text) : ''}</td></tr>`).join('')}
    </tbody></table>${list.length > 400 ? `<p class="hint wrap">Showing 400 of ${list.length}; narrow the search.</p>` : ''}</div>
    <aside class="drawer" id="drawer">${sel ? detailHtml(sel, sel.position ? `queue position ${sel.position}` : '') : '<p class="hint">Select a row to read it.</p>'}</aside></div>`;
  if (sel) bindDetail(root.querySelector('#drawer'), sel);
  const q = root.querySelector('#q');
  q.oninput = () => { state.filters.q = q.value; renderKeepFocus(root, 'q'); };
  root.querySelector('#pr').oninput = (e) => { state.filters.pr = e.target.value.replace(/\D/g, ''); renderKeepFocus(root, 'pr'); };
  root.querySelector('#tag').onchange = (e) => set({ filters: { ...f, tag: e.target.value } });
  root.querySelectorAll('[data-status]').forEach((b) => { b.onclick = () => set({ filters: { ...f, status: b.dataset.status } }); });
  root.querySelectorAll('[data-risk]').forEach((b) => { b.onclick = () => set({ filters: { ...f, risk: b.dataset.risk } }); });
  root.querySelectorAll('tbody tr').forEach((tr) => { tr.onclick = () => set({ selectedId: tr.dataset.id }); });
}

function renderKeepFocus(root, id) {
  const el = document.getElementById(id);
  const pos = el.selectionStart;
  render(root);
  const again = document.getElementById(id);
  again.focus();
  again.setSelectionRange(pos, pos);
}

export function onKey(e) {
  const list = filterRows(rows(), state.filters);
  const i = list.findIndex((r) => r.id === state.selectedId);
  if (e.key === 'j' && i < list.length - 1) { set({ selectedId: list[i + 1].id }); return true; }
  if (e.key === 'k' && i > 0) { set({ selectedId: list[i - 1].id }); return true; }
  if (e.key === '/') { document.getElementById('q').focus(); return true; }
  return false;
}
```

- [ ] **Step 2: Write `ui/js/views/triage.js`**

```js
import { state, set, rows, triageList } from '../state.js';
import { esc, inline, splitNotes, ago } from '../md.js';
import * as A from '../actions.js';

const list = () => triageList(rows(), state.triage.days);

export function render(root) {
  const l = list();
  const i = Math.min(state.triage.index, Math.max(l.length - 1, 0));
  const r = l[i];
  root.innerHTML = `<div class="wrap">
    <div class="prog"><span>Triage · ready rows untouched for
      <select id="days" aria-label="Days untouched">${[7, 14, 30, 60, 90].map((d) => `<option ${d === state.triage.days ? 'selected' : ''}>${d}</option>`).join('')}</select> days or more, oldest first</span>
      <span><b>${l.length ? i + 1 : 0}</b> of ${l.length}</span></div>
    <div class="meter"><i style="width:${l.length ? ((i + 1) / l.length) * 100 : 0}%"></i></div>
    ${r ? `<div class="card">
      <div class="crumb">${esc(r.id)} · queue position ${r.position} · last touched ${ago(r.touched)} ago</div>
      <h1>${inline(r.title)}</h1>
      <div class="chips"><span class="risk ${esc(r.risk)}">${esc(r.risk)}</span><span class="st ready">ready</span><span>· from ${inline(r.source)}</span></div>
      <div class="notes">${splitNotes(r.acceptance).map((b) => `<div class="block"><div class="lbl">${esc(b.label)}</div>${b.html}</div>`).join('')}</div>
      <div class="keygrid">
        <button data-k="k"><kbd>k</kbd>Keep</button><button data-k="t"><kbd>t</kbd>Move to top</button><button data-k="ArrowDown"><kbd>↓</kbd>Move to bottom</button><button data-k="e"><kbd>e</kbd>Edit…</button>
        <button data-k="d"><kbd>d</kbd>Defer</button><button data-k="b"><kbd>b</kbd>Block…</button><button data-k="x" class="warn"><kbd>x</kbd>Cancel…</button><button data-k="u"><kbd>u</kbd>Undo last</button>
      </div></div>` : '<p class="hint">Nothing to triage with this filter.</p>'}
  </div>`;
  root.querySelector('#days').onchange = (e) => set({ triage: { days: Number(e.target.value), index: 0 } });
  root.querySelectorAll('[data-k]').forEach((b) => { b.onclick = () => decide(b.dataset.k); });
}

async function decide(key) {
  const l = list();
  const r = l[Math.min(state.triage.index, l.length - 1)];
  if (!r) return;
  const next = () => set({ triage: { ...state.triage, index: state.triage.index + 1 } });
  if (key === 'k') return next();
  if (key === 'u') return A.undo();
  if (key === 'e') return set({ view: 'edit', editing: { mode: 'edit', id: r.id } });
  if (key === 't') { await A.moveTop(r.id); return next(); }
  if (key === 'ArrowDown') { await A.moveBottom(r.id); return next(); }
  const to = { d: 'deferred', b: 'blocked', x: 'cancelled' }[key];
  if (to && (await A.setStatus(r.id, to))) set({ triage: { ...state.triage } });
}

export function onKey(e) {
  if (['k', 't', 'ArrowDown', 'e', 'd', 'b', 'x', 'u'].includes(e.key)) { decide(e.key); return true; }
  return false;
}
```

A decision that changes the status (defer, block, cancel) removes the row from the ready list, so the same index already shows the next row; only "keep", "top" and "bottom" advance the index.

- [ ] **Step 3: Write `ui/js/views/progress.js`**

```js
import { state, set, readyQueue } from '../state.js';
import { esc, inline, ago } from '../md.js';
import { api } from '../api.js';

let loading = false;

export function render(root) {
  if (!state.progress && !loading) {
    loading = true;
    api.progress().then((p) => set({ progress: p })).catch((e) => set({ error: e.message })).finally(() => { loading = false; });
  }
  const p = state.progress;
  const ready = readyQueue();
  const byRisk = ['low', 'medium', 'high'].map((k) => [k, ready.filter((r) => r.risk === k).length]);
  const oldest = [...ready].sort((a, b) => (a.touched || 0) - (b.touched || 0)).slice(0, 8);
  root.innerHTML = `
    <div class="chart"><h3>Shipped vs filed, per day</h3>
      <p>Rows marked done (by their PR's merge date) and rows filed from that day's PRs. The backlog shrinks on days the first bar is taller.</p>
      ${!p ? '<p class="hint">Loading merge dates…</p>' : !p.available ? `<p class="hint">${esc(p.message)}</p>` : bars(p.days)}
      <div class="legend"><span><i style="background:var(--accent)"></i>Done</span><span><i style="background:var(--medink)"></i>Filed</span></div></div>
    <div class="split" style="grid-template-columns:1fr 1fr">
      <div class="chart"><h3>Ready by risk</h3><p>What the loop can pick (low, medium) versus what waits for you (high).</p>
        ${byRisk.map(([k, n]) => `<div style="display:grid;grid-template-columns:70px 1fr 40px;gap:10px;align-items:center;margin:8px 0;font-size:13px"><span class="risk ${k}">${k}</span><div style="height:10px;background:var(--chip);border-radius:5px;overflow:hidden"><i style="display:block;height:100%;width:${(n / Math.max(ready.length, 1)) * 100}%;background:var(--${k === 'low' ? 'lowink' : k === 'medium' ? 'medink' : 'hiink'})"></i></div><span style="font-variant-numeric:tabular-nums">${n}</span></div>`).join('')}</div>
      <div class="chart"><h3>Longest-untouched ready rows</h3><p>Good triage candidates.</p>
        ${oldest.map((r) => `<div style="font-size:13px;line-height:1.9"><span class="id" style="font-family:var(--mono);color:var(--muted)">${esc(r.id)}</span> ${inline(r.title.slice(0, 70))} <span class="hint">${ago(r.touched)}</span></div>`).join('')}</div>
    </div>`;
}

function bars(days) {
  if (!days.length) return '<p class="hint">No merged PRs referenced by the backlog yet.</p>';
  const W = 1000, H = 190, pad = 28, max = Math.max(1, ...days.flatMap((d) => [d.done, d.filed]));
  const bw = (W - pad * 2) / days.length;
  let g = `<line x1="${pad}" y1="${H - 24}" x2="${W - pad}" y2="${H - 24}" stroke="var(--line)"/>`;
  days.forEach((d, i) => {
    const x = pad + i * bw, h1 = (d.done / max) * (H - 50), h2 = (d.filed / max) * (H - 50);
    g += `<rect x="${x + bw * 0.18}" y="${H - 24 - h1}" width="${bw * 0.3}" height="${h1}" rx="2" fill="var(--accent)"><title>${d.day}: ${d.done} done</title></rect>`;
    g += `<rect x="${x + bw * 0.52}" y="${H - 24 - h2}" width="${bw * 0.3}" height="${h2}" rx="2" fill="var(--medink)"><title>${d.day}: ${d.filed} filed</title></rect>`;
    g += `<text x="${x + bw / 2}" y="${H - 8}" text-anchor="middle" font-size="11" fill="var(--muted)">${d.day.slice(5)}</text>`;
    g += `<text x="${x + bw * 0.33}" y="${H - 28 - h1}" text-anchor="middle" font-size="10.5" fill="var(--ink)">${d.done}</text>`;
    g += `<text x="${x + bw * 0.67}" y="${H - 28 - h2}" text-anchor="middle" font-size="10.5" fill="var(--muted)">${d.filed}</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="height:auto" role="img" aria-label="Rows done and filed per day">${g}</svg>`;
}
```

- [ ] **Step 4: Check it in the browser** (same server command as in Task 8)

Expected:
- **All rows:** status counts add up to the stats strip. Searching a file name finds its rows. The PR filter `651` lists the rows done in or filed from #651. Clicking a done row shows it in the drawer.
- **Triage:**
  - `k` advances.
  - `d` asks for an optional reason, adds a pending change and shows the next row.
  - `u` undoes the change.
- **Progress:** the bars match `gh pr list` merge dates, and both themes read correctly.
- 0 console errors.

Clear `pending.json` afterwards.

- [ ] **Step 5: Commit**

```bash
git add ui/js/views
git commit -m "ui: All rows, Triage and Progress screens"
```

---

### Task 10: Edit / New row and Publish screens

**Files:**
- Create: `ui/js/views/edit.js`, `ui/js/views/publish.js` (replacing the stubs)

**Interfaces:**
- Consumes:
  - `state.editing = {mode: 'new'} | {mode: 'edit', id}`;
  - `A.addOp`, `A.drop`, `A.rebase`, `A.publish`;
  - `state.data.ops/results/sha/fetched_at/branch`;
  - `state.published`.

- [ ] **Step 1: Write `ui/js/views/edit.js`**

```js
import { state, set, byId } from '../state.js';
import { esc, tags } from '../md.js';
import * as A from '../actions.js';

const RISKS = ['low', 'medium', 'high'];
const STATUSES = ['ready', 'deferred', 'blocked', 'cancelled'];

export function render(root) {
  const ed = state.editing || { mode: 'new' };
  const r = ed.mode === 'edit' ? byId(ed.id) : null;
  if (ed.mode === 'edit' && !r) { set({ view: 'rows', editing: null }); return; }
  const v = r ? { title: r.title.replace(/\\\|/g, '|'), risk: r.risk, status: STATUSES.includes(r.status) ? r.status : '', acceptance: r.acceptance.replace(/\\\|/g, '|'), source: r.source.replace(/\\\|/g, '|') }
    : { title: '', risk: 'low', status: 'ready', acceptance: '', source: '' };
  root.innerHTML = `<div class="wrap">
    <div class="crumb">${r ? `${esc(r.id)} · ${esc(r.status_text)}` : `New row · id assigned at publish (next free after origin/${esc(state.data.branch)}'s highest)`}</div>
    <h1 style="font-family:var(--display);font-size:26px;margin:8px 0 20px">${r ? 'Edit row' : 'File a row'}</h1>
    <form class="form" id="f">
      <label>Title<input name="title" required value="${esc(v.title)}"></label>
      <div class="three">
        <label>Risk<select name="risk">${RISKS.map((k) => `<option ${k === v.risk ? 'selected' : ''}>${k}</option>`).join('')}</select></label>
        <label>Status<select name="status">${r && !STATUSES.includes(r.status) ? `<option value="" selected>${esc(r.status)} (unchanged)</option>` : ''}${STATUSES.map((k) => `<option ${k === v.status ? 'selected' : ''}>${k}</option>`).join('')}</select></label>
        ${r ? '<label>Reason (for blocked or cancelled)<input name="reason"></label>' : '<label>Insert at<select name="insert"><option value="bottom">Bottom of the ready queue</option><option value="top">Top of the ready queue</option></select></label>'}
      </div>
      ${r ? '' : '<label>Reason (for blocked or cancelled)<input name="reason"></label>'}
      <label>Acceptance (Markdown; kept on one line in the file)<textarea name="acceptance">${esc(v.acceptance)}</textarea></label>
      <label>Source<input name="source" value="${esc(v.source)}"></label>
      <div class="hint" id="checks"></div>
      <div style="display:flex;gap:10px"><button class="btn primary" type="submit">${r ? 'Add changes to pending' : 'Add to pending changes'}</button><button class="btn" type="button" id="cancel">Cancel</button></div>
    </form></div>`;
  const form = root.querySelector('#f');
  const checks = () => {
    const d = Object.fromEntries(new FormData(form));
    const nl = /[\r\n]/.test(`${d.title}${d.acceptance}${d.source}${d.reason || ''}`);
    const pipes = /\|/.test(`${d.title}${d.acceptance}${d.source}${d.reason || ''}`);
    const t = tags(d.title);
    root.querySelector('#checks').innerHTML = `${nl ? '<span class="bad">✗ line breaks are not allowed; they will be refused</span>' : '<span class="ok">✓ one line</span>'} · ${pipes ? '<span class="ok">✓ pipes will be escaped as \\|</span>' : '<span class="ok">✓ no pipes</span>'} · tags: ${t.length ? t.map(esc).join(', ') : 'none (add [serial] to keep it out of parallel waves)'}`;
  };
  form.oninput = checks;
  checks();
  root.querySelector('#cancel').onclick = () => set({ view: r && r.status === 'ready' ? 'queue' : 'rows', editing: null });
  form.onsubmit = async (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(form));
    if (!r) {
      const ok = await A.addOp({ op: 'add', title: d.title, risk: d.risk, status: d.status, reason: d.reason || '', acceptance: d.acceptance, source: d.source, insert: d.insert });
      if (ok) set({ view: 'queue', editing: null });
      return;
    }
    for (const field of ['title', 'risk', 'acceptance', 'source']) {
      if (d[field] !== v[field] && !(await A.addOp({ op: 'set', id: r.id, field, value: d[field] }))) return;
    }
    if (d.status && d.status !== r.status && !(await A.addOp({ op: 'status', id: r.id, to: d.status, reason: d.reason || '' }))) return;
    set({ view: r.status === 'ready' ? 'queue' : 'rows', editing: null, selectedId: r.id });
  };
}
```

- [ ] **Step 2: Write `ui/js/views/publish.js`**

```js
import { state, set } from '../state.js';
import { esc, inline } from '../md.js';
import * as A from '../actions.js';

function describe(o) {
  if (o.op === 'add') return `<b>New row</b> “${inline(o.title)}” <span class="hint">→ numbered at publish · ${esc(o.insert)} of the queue</span>`;
  if (o.op === 'move') return `<b>${esc(o.id)}</b> moved to ${typeof o.to === 'string' ? esc(o.to) : `${esc(Object.keys(o.to)[0])} ${esc(Object.values(o.to)[0])}`}`;
  if (o.op === 'status') return `<b>${esc(o.id)}</b> status → ${esc(o.to)}${o.reason ? ` — ${esc(o.reason)}` : ''}`;
  return `<b>${esc(o.id)}</b> ${esc(o.field)} → <span class="hint">${inline(o.value.slice(0, 160))}</span>`;
}

export function render(root) {
  const { ops, results, sha, fetched_at: at, branch } = state.data;
  const applied = results.filter((r) => r.result === 'applied').length;
  const pub = state.published;
  root.innerHTML = `<div class="wrap">
    <h1 style="font-family:var(--display);font-size:26px;margin:0 0 6px">Publish ${ops.length} change${ops.length === 1 ? '' : 's'}</h1>
    <p class="hint" style="margin:0 0 18px">Replayed onto origin/${esc(branch)} <code>${esc(sha.slice(0, 9))}</code> (fetched ${new Date(at * 1000).toLocaleTimeString()}) in a fresh worktree, then committed and opened as one PR. Your checkout is not touched. <button class="btn" id="refresh">Fetch again</button></p>
    ${pub && pub.pr_url ? `<p class="banner info">Opened <a href="${esc(pub.pr_url)}" target="_blank" rel="noopener">${esc(pub.pr_url)}</a> with ${pub.applied} change${pub.applied === 1 ? '' : 's'}${pub.conflicts ? `; ${pub.conflicts} conflicting change${pub.conflicts === 1 ? ' stays' : 's stay'} below` : ''}.</p>` : ''}
    ${pub && !pub.pr_url && pub.message ? `<p class="banner">${esc(pub.message)}</p>` : ''}
    ${ops.map((o, i) => {
      const r = results[i];
      const bad = r.result !== 'applied';
      return `<div class="op${bad ? ' conflict' : ''}"><span class="k">${bad ? esc(r.result) : esc(o.op)}</span>
        <div>${describe(o)}${bad ? `<div class="bad" style="margin-top:6px">${esc(r.reason)}${r.result === 'conflict' ? ' This change will be skipped.' : ''}</div>` : ''}</div>
        <div style="display:flex;gap:6px">${bad && o.op !== 'add' && r.result === 'conflict' ? `<button class="btn" data-rebase="${i}">Re-apply to new text</button>` : ''}<button class="btn" data-drop="${i}">${bad ? 'Drop' : 'Undo'}</button></div></div>`;
    }).join('') || '<p class="hint">No pending changes.</p>'}
    <div style="display:flex;align-items:center;gap:14px;margin-top:18px">
      <button class="btn primary" id="publish" ${applied && !state.busy ? '' : 'disabled'}>${state.busy ? 'Publishing…' : `Publish ${applied} as PR`}</button>
      <span class="hint">Branch <code>backlog/ui-&lt;date&gt;-&lt;n&gt;</code> · commit without trailers</span>
    </div></div>`;
  root.querySelector('#refresh').onclick = () => A.load(true);
  root.querySelectorAll('[data-drop]').forEach((b) => { b.onclick = () => A.drop(Number(b.dataset.drop)); });
  root.querySelectorAll('[data-rebase]').forEach((b) => { b.onclick = () => A.rebase(Number(b.dataset.rebase)); });
  root.querySelector('#publish').onclick = () => { set({ published: null }); A.publish(); };
}
```

- [ ] **Step 3: Check it in the browser against a throwaway repo**

Make a throwaway copy with its own bare origin, so Publish can't reach GitHub. `gh` still has to run, so put the test suite's fake `gh` first on PATH:

```bash
T=$(mktemp -d); git init -q --bare -b main $T/o.git; git clone -q $T/o.git $T/w
mkdir -p $T/w/.claude && cp /Users/crisis/_workspace/vacancy-vibe.wt/orch/.claude/improvement-backlog.md $T/w/.claude/
printf -- '---\nid_prefix: IB\n---\n' > $T/w/.claude/foreman.config.md
(cd $T/w && git checkout -q -b main && git add -A && git commit -qm init && git push -q -u origin main && git remote set-head origin main)
mkdir $T/bin && printf '#!/bin/sh\necho "$@" >> %s/gh.log\ncase "$1 $2" in "pr create") echo https://example.invalid/pull/1;; "pr list") echo "[]";; esac\n' $T > $T/bin/gh && chmod +x $T/bin/gh
PATH=$T/bin:$PATH python3 scripts/ui.py --repo $T/w
```

Steps in the UI:
1. File a new row with a `|` in its title.
2. Defer one row, with a reason.
3. Move one row to the top.
4. Open Publish and click "Publish 3 as PR".

Expected:
- The banner shows `https://example.invalid/pull/1`.
- `git -C $T/w diff origin/main origin/backlog/ui-*` shows exactly the three changed or added lines, with the pipe escaped.
- `$T/gh.log` has the `pr create` call.
- Pending is now empty.
- 0 console errors.

Then a conflict:
1. Make a change in the UI.
2. Push a different change to the same row from another clone.
3. Click "Fetch again". Publish shows the change in red with "Re-apply to new text". Re-apply it, then publish.

Expected: it applies to the new text.

- [ ] **Step 4: Commit**

```bash
git add ui/js/views
git commit -m "ui: Edit/New row form with live checks, Publish screen with conflicts"
```

---

### Task 11: `/foreman ui`, test wiring, README, version, release check

**Files:**
- Modify: `skills/foreman/SKILL.md`, `tests/run.sh`, `README.md`, `.claude-plugin/plugin.json`

- [ ] **Step 1: Add the subcommand to `skills/foreman/SKILL.md`**

In the frontmatter, change `argument-hint` to `"[init|ui|ui stop] [item-id] [tag:<x>] [loop|--auto-merge] [parallel N]"`.

In `## Arguments`, add:

```markdown
- `ui` — **open the backlog UI** (see **Backlog UI**); `ui stop` stops it.
```

Before `## Init (`/foreman init`)`, add:

````markdown
## Backlog UI (`/foreman ui`)

Start the local backlog UI for this repo and print its URL; do nothing else in this invocation.

1. Check that the config's `backlog_path` (default `.claude/improvement-backlog.md`) exists on `origin`'s
   default branch: `git cat-file -e origin/HEAD:<backlog_path>` after `git fetch -q origin`. If it doesn't,
   say so and suggest `/foreman init`; don't start anything.
2. Run it in the background, then print the URL it outputs (one line; the token is part of it):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ui.py" --repo "$PWD"
   ```
   A second `/foreman ui` reuses the running server. `/foreman ui stop` runs the same script with `--stop`.
3. Tell the user, in two or three lines:
   - Edits collect as pending changes on this machine.
   - **Publish** opens one PR from a temporary worktree, so their checkout is never touched.
   - The UI's PR is separate from the `/foreman` bookkeeping PR. When both change the same row, the UI shows the conflict before publishing.
````

- [ ] **Step 2: Wire the new suites into `tests/run.sh`**

In the `## syntax` block, extend the `py_compile` line so it also compiles `$root/scripts/backlog.py` and `$root/scripts/ui.py`. Before the final summary line, add:

```bash
echo "## backlog.py + ui.py"
check "test_backlog.py" python3 -m unittest -q "$root/tests/test_backlog.py"
check "test_ui_api.py" python3 -m unittest -q "$root/tests/test_ui_api.py"
echo "## ui js"
if command -v node >/dev/null; then check "test_ui_js.mjs" node --test "$root/tests/test_ui_js.mjs"; else echo "SKIP node not installed"; fi
```

Run: `tests/run.sh 2>&1 | tail -3`
Expected: `76 + 3` passes (79), 0 failed.

- [ ] **Step 3: README and version**

Add a `## Backlog UI` section to `README.md`:

```markdown
## Backlog UI

`/foreman ui` starts a local web UI for the backlog: the ready queue in pick order (drag to reorder), search and
filters across every row, a one-row-at-a-time triage mode, progress charts, and a form to file or edit rows.
It reads the backlog from `origin`'s default branch, never your working copy.

- **Edits are pending until you publish.** They live in `~/.claude/foreman/<repo-key>/pending.json`.
  **Publish** replays them onto a fresh branch (`backlog/ui-<date>-<n>`) in a temporary worktree, commits,
  pushes and opens one PR with `gh`. A change whose row changed on main in the meantime is shown as a
  conflict and kept pending, never applied to the new text silently.
- **Security.** The server binds to `127.0.0.1` only, needs the session token from the printed URL (stored
  as an `HttpOnly; SameSite=Strict` cookie), and refuses any `Host` other than `127.0.0.1`/`localhost`.
- **Themes.** Auto follows your system light/dark setting; Light and Dark pin it (remembered per browser).
- `/foreman ui stop` stops the server. Without `gh`, Publish can't open the PR and Progress shows counts only.
```

In `.claude-plugin/plugin.json`, set `"version": "0.3.0"`.

- [ ] **Step 4: Release check in the browser** (claude-in-chrome, on vacancy-vibe; read-only except one throwaway publish)

Start it: `python3 scripts/ui.py --repo /Users/crisis/_workspace/vacancy-vibe --no-open`. Then open the printed URL in the browser at 1440px and at 375px, in light and in dark.

Walk every screen and confirm:
- The queue order matches the file.
- The notes blocks render.
- Search finds `content-detail-view.tsx` rows.
- Triage and Progress render.
- 0 console errors.

Do NOT publish against vacancy-vibe. The Task 10 throwaway repo covered Publish end to end.

Afterwards, clear `pending.json`.

- [ ] **Step 5: Commit and push the branch**

```bash
git add skills/foreman/SKILL.md tests/run.sh README.md .claude-plugin/plugin.json
git commit -m "Add /foreman ui; run the UI suites in tests/run.sh; README; version 0.3.0"
git push -u origin feat/backlog-ui
```

Open the PR (`gh pr create --fill`, with no trailers in the body). The human merges it.
