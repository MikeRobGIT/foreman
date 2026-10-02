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
