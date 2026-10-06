#!/usr/bin/env python3
"""Fold /foreman worker reports into the backlog and the rules files.

Usage: bk.py <repo-dir> [--dry-run] [--no-fetch] report.json [report.json ...]

The backlog path (`backlog_path`) and the row id prefix (`id_prefix`, default IB) come from the
project's foreman config (see cfg.py). The examples below use the default prefix.

One report per worker PR (the orchestrator saves the worker's final `json` block and
adds `pr`):

  {
    "pr": 591,
    "done":      [{"id": "IB-457", "note": "what shipped + evidence"}],
    "append":    [{"id": "IB-406", "note": "**Correction (#593):** ..."}],
    "new_rows":  ["| IB-NEW-a | title | low | ready | acceptance | source |"],
    "rule_notes":[{"file": ".claude/rules/x.md", "old": "exact sentence", "new": "replacement"}]
  }

- `done` flips a `ready` row to `done (#pr)` and appends `**DONE (#pr):** note` to its
  acceptance column. `append` adds a note to any row without touching its status.
- `IB-NEW-x` placeholders are numbered from the real max id, per report, and rewritten everywhere
  in that report's text (workers' own numbering always collided). The max covers this file,
  origin's default branch and every open backlog UI branch (`backlog/ui-*`, fetched first unless --no-fetch), so a
  new row never takes an id an unmerged UI PR already uses.
- A new row must have exactly 5 ` | ` separators (a stray pipe shifts the table's columns)
  and no newline (rows stay single-line).
- A rule note applies only when `old` matches exactly once; anything else is reported.
- Every problem is collected; if there is any, nothing is written (exit 1). --dry-run
  prints the plan and writes nothing either way.
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cfg  # noqa: E402
import backlog  # noqa: E402


def walk(value, fn):
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [walk(v, fn) for v in value]
    if isinstance(value, dict):
        return {k: walk(v, fn) for k, v in value.items()}
    return value


def main():
    args = [a for a in sys.argv[1:] if a not in ('--dry-run', '--no-fetch')]
    dry = '--dry-run' in sys.argv
    if len(args) < 2:
        raise SystemExit(__doc__)
    repo, paths = args[0], args[1:]
    problems, log = [], []

    BACKLOG = cfg.get(repo, 'backlog_path', '.claude/improvement-backlog.md')
    P = cfg.get(repo, 'id_prefix', 'IB')
    E = re.escape(P)
    ROW = backlog.row_pattern(P)
    NEW = re.compile(rf'{E}-NEW-[A-Za-z0-9]+')

    path = os.path.join(repo, BACKLOG)
    lines = open(path).read().split('\n')
    ids = [int(m.group(1)) for l in lines for m in [ROW.match(l)] if m]
    if '--no-fetch' not in sys.argv:
        try:  # best effort: a prompt or a stalled remote must not hang the loop's bookkeeping
            subprocess.run(['git', 'fetch', '-q', '--prune', 'origin', backlog.UI_REFSPEC], cwd=repo, capture_output=True,
                           stdin=subprocess.DEVNULL, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}, timeout=30)
        except subprocess.TimeoutExpired:
            pass
    next_id = first_id = max(max(ids, default=0), backlog.reserved_max_id(repo, BACKLOG, P)) + 1
    if ids:
        anchor = max(n for n, l in enumerate(lines) if (m := ROW.match(l)) and int(m.group(1)) == max(ids))
    else:  # a table with no rows yet (/foreman seed): new rows go under the |---| line below its `| id |` header
        seps = [n for n in range(1, len(lines)) if re.match(r'^\|[-| :]+\|\s*$', lines[n]) and re.match(r'^\| *id *\|', lines[n - 1], re.I)]
        if not seps:
            raise SystemExit(f'{BACKLOG}: no backlog table to add rows to (want a "| id | ..." header line followed by a |---| line)')
        anchor = seps[0]

    def find(id_):
        hits = [n for n, l in enumerate(lines) if l.startswith(f'| {id_} |')]
        if len(hits) != 1:
            problems.append(f'{id_}: {len(hits)} rows match')
            return None
        return hits[0]

    def split(n):
        r = lines[n].rstrip()
        if not r.endswith(' |'):
            problems.append(f'{lines[n][:12]}: row does not end with " |"')
            return None
        body = r[:-2]
        idx = body.rfind(' | ')
        return body[:idx], body[idx + 3:]

    new_rows, rule_notes = [], []
    for p in paths:
        rep = json.load(open(p))
        pr = rep.get('pr')
        if not isinstance(pr, int):
            problems.append(f'{p}: missing integer "pr"')
            continue
        mapping = {}
        for row in rep.get('new_rows', []):
            m = re.match(rf'^\| ({E}-NEW-[A-Za-z0-9]+) \|', row)
            if not m:
                problems.append(f'{p}: new row must start with "| {P}-NEW-<x> |": {row[:50]}')
                continue
            if m.group(1) in mapping:
                problems.append(f'{p}: {m.group(1)} used twice')
                continue
            mapping[m.group(1)] = f'{P}-{next_id}'
            next_id += 1

        def renumber(s):
            def one(m):
                if m.group(0) not in mapping:
                    problems.append(f'{p}: {m.group(0)} has no new row')
                    return m.group(0)
                return mapping[m.group(0)]
            return NEW.sub(one, s)

        rep = walk(rep, renumber)

        for d in rep.get('done', []):
            n = find(d['id'])
            if n is None:
                continue
            m = re.match(rf'^(\| {E}-\d+ \| .*? \| (?:low|medium|high) \| )ready( \| .*)$', lines[n])
            if not m:
                problems.append(f'{d["id"]}: not a `ready` row')
                continue
            parts = split(n)
            if parts is None:
                continue
            acc, src = parts
            head = m.group(1)
            acc = acc[len(head) + len('ready'):]
            if '\n' in d['note'] or ' | ' in d['note']:
                problems.append(f'{d["id"]}: note has a newline or a raw " | " (escape a pipe as \\|)')
                continue
            lines[n] = f'{head}done (#{pr}){acc} **DONE (#{pr}):** {d["note"]} | {src} |'
            log.append(f'done   {d["id"]} (#{pr})')

        for a in rep.get('append', []):
            n = find(a['id'])
            parts = split(n) if n is not None else None
            if parts is None:
                continue
            if '\n' in a['note'] or ' | ' in a['note']:
                problems.append(f'{a["id"]}: note has a newline or a raw " | " (escape a pipe as \\|)')
                continue
            lines[n] = f'{parts[0]} {a["note"]} | {parts[1]} |'
            log.append(f'append {a["id"]}')

        for row in rep.get('new_rows', []):
            issue = (backlog.line_problems(row) or [None])[0]
            if issue == ('newline',):
                problems.append(f'{row[:12]}: new row has a newline')
            elif issue:
                problems.append(f'{row[:12]}: {issue[1]} " | " separators, want 5 (escape a pipe as \\|)')
            elif (cells := backlog.split_cells(row)) is None or len(cells) != 6:
                problems.append(f'{row[:30]}: new row must be "| id | title | risk | status | acceptance | source |"')
            elif bad := backlog.validate_cells(cells, P):
                problems.append(f'"{cells[1][:30]}": {"; ".join(bad)}')  # by title: its id was renumbered already
            else:
                new_rows.append(row)
                log.append(f'new    {row[:60]}')

        rule_notes.extend((p, rn) for rn in rep.get('rule_notes', []))

    lines[anchor + 1:anchor + 1] = new_rows

    edits = {}
    for p, rn in rule_notes:
        f = os.path.join(repo, rn['file'])
        if not os.path.exists(f):
            problems.append(f'{p}: {rn["file"]} does not exist')
            continue
        t = edits.get(f) or open(f).read()
        c = t.count(rn['old'])
        if c != 1:
            problems.append(f'{rn["file"]}: "{rn["old"][:50]}" matches {c} times, want 1')
            continue
        edits[f] = t.replace(rn['old'], rn['new'])
        log.append(f'rule   {rn["file"]}')

    print('\n'.join(log))
    if problems:
        print('\nPROBLEMS (nothing written):\n  ' + '\n  '.join(problems))
        raise SystemExit(1)
    if dry:
        print(f'\ndry run: ok, {len(new_rows)} new rows from {P}-{first_id}')
        return
    open(path, 'w').write('\n'.join(lines))
    for f, t in edits.items():
        open(f, 'w').write(t)
    print(f'\nwrote {BACKLOG} and {len(edits)} rules file(s)')


if __name__ == '__main__':
    main()
