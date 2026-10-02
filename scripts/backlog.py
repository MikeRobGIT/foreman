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
