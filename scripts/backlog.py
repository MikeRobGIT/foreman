#!/usr/bin/env python3
"""Parse, edit and write the foreman backlog table without disturbing untouched lines.

A row is a line `| <PREFIX>-<n> | title | risk | status | acceptance | source |`. Cells are
separated by ` | `; a pipe inside a cell is written `\\|`, so splitting on the literal ` | `
never cuts an escaped pipe. Every other line (headings, legend, blank lines, a malformed row)
is kept verbatim, so serialise(parse(text)) == text byte for byte.
"""
import re
import subprocess
from collections import Counter

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
    def __init__(self, lines, prefix, eol=''):
        self.lines = lines
        self.prefix = prefix
        self.eol = eol  # '\r' in a CRLF file: split on '\n' leaves it on every line, and rebuilt rows keep it

    def row(self, cells):
        return Row(build(cells) + self.eol, cells)

    def rows(self):
        return [l for l in self.lines if isinstance(l, Row)]

    def find(self, id_):
        hits = [i for i, l in enumerate(self.lines) if isinstance(l, Row) and l.id == id_]
        return hits[0] if len(hits) == 1 else None

    def ready_indexes(self):
        return [i for i, l in enumerate(self.lines) if isinstance(l, Row) and l.status == 'ready']

    def duplicates(self):
        """{id: count} for every id on more than one line (malformed rows still hold their id)."""
        pat = row_pattern(self.prefix)
        n = Counter(m.group(1) for l in self.lines for m in [pat.match(l.raw if isinstance(l, Row) else str(l))] if m)
        return {f'{self.prefix}-{k}': c for k, c in n.items() if c > 1}


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
    return Backlog(lines, prefix, '\r' if '\r\n' in text else '')


def serialise(bl):
    return '\n'.join(l.raw if isinstance(l, Row) else str(l) for l in bl.lines)


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


def make_row(cells, prefix, eol=''):
    problems = validate_cells(cells, prefix)
    return (None, problems) if problems else (Row(build(cells) + eol, cells), [])


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
        if not re.match(r'^NEW-\d+$', op.get('tmp', '')):
            raise ValueError('add needs tmp matching NEW-\\d+')
        if op.get('risk') not in RISKS:
            raise ValueError('add needs risk in low/medium/high')
        if op.get('status') not in SET_STATUSES:
            raise ValueError('add needs status in ready/deferred/blocked/cancelled')
        for field in ('acceptance', 'source', 'reason'):
            val = op.get(field, '')
            if not isinstance(val, str):
                raise ValueError(f'add {field} must be a string if present')
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
        if bl.find(op['tmp']) is not None:
            return {'result': 'invalid', 'reason': f'{op["tmp"]} already in the backlog'}
        if op.get('status') in NEEDS_REASON and not (op.get('reason') or '').strip():
            return {'result': 'invalid', 'reason': f'{op["status"]} needs a reason'}
        cells = [op['tmp'], escape(op['title']), op['risk'], _status_text(op['status'], op.get('reason')),
                 escape(op.get('acceptance', '')), escape(op.get('source', ''))]
        row, problems = make_row(cells, bl.prefix, bl.eol)
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
    new, problems = make_row(cells, bl.prefix, bl.eol)
    if problems:
        return {'result': 'invalid', 'reason': '; '.join(problems)}
    bl.lines[i] = new
    return {'result': 'applied', 'reason': ''}


def apply(bl, ops):
    return [_apply_one(bl, op) for op in ops]


def max_id(bl):
    pat = row_pattern(bl.prefix)
    raws = [l.raw if isinstance(l, Row) else str(l) for l in bl.lines]  # malformed rows still hold their id
    return max([int(m.group(1)) for r in raws for m in [pat.match(r)] if m] or [0])


def assign_ids(bl, tmp_ids, floor=0):
    """Number NEW-n rows from max(the file's highest id, floor) + 1."""
    top = max(max_id(bl), floor)
    mapping = {}
    for tmp in tmp_ids:
        top += 1
        mapping[tmp] = f'{bl.prefix}-{top}'
    for i, l in enumerate(bl.lines):
        if isinstance(l, Row) and l.id in mapping:
            cells = l.cells()
            cells[0] = mapping[l.id]
            bl.lines[i] = bl.row(cells)
    return mapping


UI_REFSPEC = '+refs/heads/backlog/ui-*:refs/remotes/origin/backlog/ui-*'


def reserved_max_id(repo, backlog_path, prefix, branch=None):
    """The highest id on origin's default branch or on any open UI branch (refs/remotes/origin/backlog/ui-*),
    as last fetched. A new id must be above it, or it collides with a UI PR that has not merged yet.
    0 when `repo` is not a git checkout."""
    def git(*args):
        p = subprocess.run(['git', *args], cwd=repo, capture_output=True)
        return p.stdout.decode('utf-8') if p.returncode == 0 else None
    if branch is None:
        head = (git('symbolic-ref', '--short', 'refs/remotes/origin/HEAD') or '').strip()
        branch = head.split('/', 1)[1] if '/' in head else 'main'
    refs = (git('for-each-ref', '--format=%(refname)', 'refs/remotes/origin/backlog/') or '').split()
    refs = [f'refs/remotes/origin/{branch}'] + [r for r in refs if r.startswith('refs/remotes/origin/backlog/ui-')]
    texts = [git('show', f'{r}:{backlog_path}') for r in refs]
    return max([max_id(parse(t, prefix)) for t in texts if t is not None] or [0])


def describe(op, mapping=None):
    mapping = mapping or {}
    kind = op['op']
    if kind == 'add':
        return f'new row {mapping.get(op["tmp"], op["tmp"])}: {op["title"]}'
    id_ = f'new row {mapping[op["id"]]}' if op['id'] in mapping else op['id']
    if kind == 'set':
        return f'{id_}: {op["field"]} edited'
    if kind == 'status':
        reason = (op.get('reason') or '').strip()
        return f'{id_}: status → {op["to"]}' + (f' ({reason})' if reason else '')
    to = op['to']
    where = to if isinstance(to, str) else f'{next(iter(to))} {mapping.get(next(iter(to.values())), next(iter(to.values())))}'
    return f'{id_}: moved to {where}'
