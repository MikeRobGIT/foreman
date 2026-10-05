#!/usr/bin/env python3
"""foreman's batch state for the parallel spec phase: one JSON file per batch, written only by the orchestrator.

  batches.py set <batch> key=value...                  create the file on first use; set the keys
  batches.py note <batch> minors|questions|park_note <file>   the field becomes the file's non-empty lines
  batches.py bump <batch> pr_rounds|bounces            add one
  batches.py stamp <batch> sent|comment_sent|spec_ready|stopped
  batches.py task <batch> add <kind> <id> | verdict <id> | done <id>
  batches.py add-round <batch> r=<n> sha=<sha> entry=<n> [fallthrough=<adapter:why,...>] explore=<pass|revise|none>
                       crit=<n> imp=<n> minor=<n>
  batches.py show [<batch>] [--json]
  batches.py table checkpoint <wave>                   print the W3.6 table and stamp it
  batches.py table report                              print the W6 spec-phase table and stamp it
Every command takes `--session <id>`. Exit 0 = done, 1 = refused (one line on stderr), 2 = usage.

Files: <state dir>/batches/<session>/state/<batch>.json in every state.state_dirs(main) entry (hooks.py forget
never reaches them); reads take the newest copy. Session: --session, else $FOREMAN_SESSION, else the most
recently touched live run marker, else `nosession`; an id hooks.py aliased to the run it continues (a session
moved to the background, forked or resumed under a new id) resolves to that run's id. Run scope: with a live run
marker, `show` and the tables see only batches named at or after the marker's `started` minute.
"""
import contextlib
import fcntl
import glob
import hashlib
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cfg  # noqa: E402
import state  # noqa: E402

RUN_TTL = 48 * 3600
NAME = re.compile(r'[A-Za-z0-9_-]+')   # these validators take fullmatch: `^…$` with match lets a final newline in
SHA = re.compile(r'[0-9a-f]{7,40}')
TOKEN = re.compile(r'[A-Za-z0-9_.,/:@+-]+')
PATHS = re.compile(r'[A-Za-z0-9_.,/:@+()\[\]-]+')   # TOKEN plus the brackets and parens of Next.js routes
TASK_ID = re.compile(r'[A-Za-z0-9_.:@-]+')
INT = re.compile(r'0|[1-9][0-9]*')
STATUSES = ('spec', 'review', 'passed', 'released', 'pr', 'parked')
REASONS = {
    'open-question': 'open question', 'no-pass': 'no pass in {n} rounds', 'timed-out': 'spec phase timed out',
    'spec-incomplete': 'spec incomplete', 'bounces': 'spec incomplete after 2 bounces',
    'review-failed': 'review could not run', 'worker-stuck': 'worker stuck',
    'not-released': 'not released by the human', 'scope-conflict': 'scope conflict',
    'release-unrecorded': 'release could not be recorded', 'stopped-by-human': 'stopped by the human',
    'comment-failed': 'comment round failed',
}
FIELDS = {
    'status': lambda v: v in STATUSES,
    'reason': lambda v: v in REASONS,
    'rows': TOKEN.fullmatch, 'owned': PATHS.fullmatch, 'branch': TOKEN.fullmatch, 'spec': PATHS.fullmatch,
    'worktree': lambda v: PATHS.fullmatch(v) and v.startswith('/'),
    'fork_base': SHA.fullmatch, 'sha': SHA.fullmatch, 'reviewed_sha': SHA.fullmatch, 'released_sha': SHA.fullmatch,
    'verified_sha': SHA.fullmatch,
    'pr': lambda v: INT.fullmatch(v) and v != '0',
    'reviewer_model': PATHS.fullmatch,
}
COUNTERS = ('pr_rounds', 'bounces')
TEXTS = ('minors', 'questions', 'park_note')
STAMPS = ('sent', 'comment_sent', 'spec_ready', 'stopped')
KINDS = ('worker', 'timer', 'chain', 'reviewer')
WHYS = ('error', 'quota', 'reads-repo', 'timeout', 'missing')


class Fail(Exception):
    """A refusal: exit 1 with the message on stderr."""


class Usage(Exception):
    """A malformed command: exit 2."""


def batch_time(batch):
    """Epoch seconds of a `YYYYMMDD-HHMM-...` batch name (local time), or 0 for an undated name (hooks.py uses this copy)."""
    m = re.match(r'^(\d{8}-\d{4})-', batch)
    try:
        return time.mktime(time.strptime(m.group(1), '%Y%m%d-%H%M')) if m else 0
    except ValueError:
        return 0


def wave_of(batch):
    return batch[:-1]


def same_sha(a, b):
    """Two SHAs name the same commit: equal, or one abbreviates the other (both at least 7 hex digits)."""
    return bool(a and b and (a.startswith(b) or b.startswith(a)))


def rm(path):
    try:
        os.remove(path)
    except OSError:
        pass


class Store:
    """One session's batch files across the repo's state dirs."""

    def __init__(self, main, dirs, sid):
        self.main, self.dirs, self.sid = main, dirs, sid

    def path(self, d, name):
        return os.path.join(d, 'batches', self.sid, 'state', name)

    def read(self, name):
        """The newest copy of `name` across the dirs, or None. A copy that isn't JSON is refused, untouched."""
        best = None
        for d in self.dirs:
            p = self.path(d, name)
            if os.path.isfile(p):
                t = os.stat(p).st_mtime_ns
                if best is None or t > best[0]:
                    best = (t, p)
        if best is None:
            return None
        try:
            with open(best[1], encoding='utf-8') as f:
                return json.load(f)
        except ValueError:
            raise Fail(f'corrupt batch state: {best[1]}')

    def write(self, name, data):
        """Every dir's temp file first, then each moved into place. A dir that fails after the first was replaced
        is named on stderr and the call still succeeds, so a retry never applies a change twice."""
        text = json.dumps(data, indent=1, sort_keys=True)
        temps = []
        try:
            for d in self.dirs:
                p = self.path(d, name)
                fd, tmp = tempfile.mkstemp(dir=state.ensure(os.path.dirname(p)), suffix='.tmp')
                temps.append((tmp, p))
                with os.fdopen(fd, 'w') as f:
                    f.write(text)
        except OSError as e:
            for tmp, _ in temps:
                rm(tmp)
            raise Fail(f'could not write batch state: {e}')
        for i, (tmp, p) in enumerate(temps):
            try:
                os.replace(tmp, p)
            except OSError as e:
                for t, _ in temps[i:]:
                    rm(t)
                if i == 0:
                    raise Fail(f'could not write batch state: {e}')
                print(f'batches.py: {name} not written in {os.path.dirname(p)} ({e.strerror}); readers use the '
                      'newest copy', file=sys.stderr)
                return

    @contextlib.contextmanager
    def lock(self):
        d = state.ensure(os.path.join(self.dirs[0], 'batches', self.sid, 'state'))
        with open(os.path.join(d, '.lock'), 'a') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def started(self):
        """The live run marker's `started` for this session, floored to the minute, or None."""
        best = None
        for d in self.dirs:
            m = state.read_json(os.path.join(d, 'runs', self.sid + '.json'))
            if isinstance(m, dict) and time.time() - float(m.get('touched', 0)) < RUN_TTL:
                if best is None or float(m.get('touched', 0)) > float(best.get('touched', 0)):
                    best = m
        if best is None:
            return None
        s = float(best.get('started', 0))
        return s - s % 60

    def batches(self):
        """Every batch of the current run (the whole session with no live run marker), by name."""
        names = set()
        for d in self.dirs:
            for p in glob.glob(os.path.join(glob.escape(self.path(d, '')), '*.json')):
                names.add(os.path.basename(p)[:-len('.json')])
        since = self.started()
        out = []
        for n in sorted(names):
            if NAME.fullmatch(n) and (since is None or batch_time(n) >= since):
                b = self.read(n + '.json')
                if isinstance(b, dict):
                    out.append(b)
        return out


def canonical(dirs, sid):
    """The session whose run `sid` belongs to: the `alias` of hooks.py's `runs/<sid>.json` = {alias, touched} when
    `sid` continues an earlier session, else `sid`. One step: an alias never names another alias. hooks.py uses it."""
    if not NAME.fullmatch(sid or ''):
        return sid
    for d in dirs:
        m = state.read_json(os.path.join(d, 'runs', sid + '.json'))
        if isinstance(m, dict):
            a = m.get('alias')
            return a if isinstance(a, str) and NAME.fullmatch(a) else sid
    return sid


def session_id(dirs, given):
    sid = given or os.environ.get('FOREMAN_SESSION')
    if not sid:
        live = []
        for d in dirs:
            for p in glob.glob(os.path.join(glob.escape(d), 'runs', '*.json')):
                m = state.read_json(p)
                if isinstance(m, dict) and 'alias' not in m and time.time() - float(m.get('touched', 0)) < RUN_TTL:
                    live.append((float(m.get('touched', 0)), os.path.basename(p)[:-len('.json')]))
        if not live:
            return 'nosession'
        live.sort(reverse=True)
        sid = live[0][1]
        if len({s for _, s in live}) > 1:
            print(f'batches.py: {len({s for _, s in live})} live runs; using session {sid}, the most recently touched '
                  '(pass --session <id> to pick another)', file=sys.stderr)
    if not NAME.fullmatch(sid):
        raise Usage(f'bad session id: {sid}')
    return canonical(dirs, sid)


def open_store(given):
    main = state.main_checkout(os.getcwd())
    if not main or not cfg.config_path(main, use_env=False):
        raise Fail('no state dir: run batches.py inside a repo with a foreman config')
    dirs = state.state_dirs(main)
    return Store(main, dirs, session_id(dirs, given))


def new_batch(name):
    return {'batch': name, 'pr_rounds': 0, 'bounces': 0, 'stamps': [], 'tasks': [], 'rounds': []}


def pairs(args, allowed):
    out = {}
    for a in args:
        k, eq, v = a.partition('=')
        if not eq or k not in allowed:
            raise Fail(f'unknown key: {k}')
        if not v or not allowed[k](v):
            raise Fail(f'bad value for {k}: {v}')
        out[k] = v
    if not out:
        raise Usage('no key=value given')
    return out


def stamp(b, kind):
    b.setdefault('stamps', []).append([kind, time.time()])


def update(store, name, change):
    """Read-modify-write one batch under the lock; `change(b)` edits the record (new on first use)."""
    if not NAME.fullmatch(name):
        raise Usage(f'bad batch name: {name}')
    with store.lock():
        b = store.read(name + '.json') or new_batch(name)
        change(b)
        store.write(name + '.json', b)


def cmd_set(store, name, args):
    kv = pairs(args, FIELDS)

    def change(b):
        before, reviewed = b.get('status'), b.get('reviewed_sha')
        b.update({k: int(v) if k == 'pr' else v for k, v in kv.items()})
        st = b.get('status')
        if 'status' in kv:
            if st == 'passed' and 'reviewed_sha' not in kv:
                raise Fail('status=passed needs reviewed_sha=<sha> in the same call')
            if st == 'released' and not same_sha(b.get('released_sha'), b.get('reviewed_sha')):
                raise Fail(f"status=released needs released_sha equal to reviewed_sha ({b.get('reviewed_sha') or 'unset'})")
            if st == 'parked' and not b.get('reason'):
                raise Fail('status=parked needs reason=<reason>')
            if st == 'pr' and 'pr' not in kv:
                raise Fail('status=pr needs pr=<n> in the same call')
        if 'reviewed_sha' in kv:
            if kv.get('status') != 'passed':
                raise Fail('reviewed_sha=<sha> goes with status=passed in the same call')
            last = (b.get('rounds') or [{}])[-1].get('sha')
            if not same_sha(last, kv['reviewed_sha']):
                raise Fail("status=passed needs reviewed_sha equal to the last round's sha; the last round is at "
                           f"{last or '(none)'}")
            if st != before or not same_sha(reviewed, kv['reviewed_sha']):
                stamp(b, 'passed')
        if 'status' in kv:
            if st != before and st in ('released', 'parked'):
                stamp(b, 'ended')
        if b.get('reason') and st != 'parked':
            raise Fail('reason=<reason> goes with status=parked only')
    update(store, name, change)


def cmd_note(store, name, field, path):
    if field not in TEXTS:
        raise Usage(f'note takes one of {", ".join(TEXTS)}')
    try:
        with open(path, encoding='utf-8') as f:
            lines = [l.rstrip() for l in f.read().splitlines() if l.strip()]
    except OSError as e:
        raise Fail(f'cannot read {path}: {e.strerror}')
    update(store, name, lambda b: b.__setitem__(field, lines))


def cmd_bump(store, name, field):
    if field not in COUNTERS:
        raise Usage(f'bump takes one of {", ".join(COUNTERS)}')
    update(store, name, lambda b: b.__setitem__(field, int(b.get(field, 0)) + 1))


def cmd_stamp(store, name, kind):
    if kind not in STAMPS:
        raise Usage(f'stamp takes one of {", ".join(STAMPS)}')

    def change(b):
        if kind == 'comment_sent' and b.get('status') != 'passed':
            raise Fail(f"stamp comment_sent needs status=passed ({b.get('status') or 'spec'})")
        stamp(b, kind)
        if kind == 'comment_sent':
            b['status'] = 'review'
            b.pop('reviewed_sha', None)
    update(store, name, change)


def cmd_task(store, name, args):
    if args[:1] == ['add'] and len(args) == 3 and args[1] in KINDS and TASK_ID.fullmatch(args[2]):
        def change(b):
            tasks = b.setdefault('tasks', [])
            if not any(t['id'] == args[2] and t['kind'] == args[1] and t['live'] for t in tasks):
                tasks.append({'kind': args[1], 'id': args[2], 'added': time.time(), 'verdict': False, 'live': True})
    elif len(args) == 2 and args[0] in ('verdict', 'done'):
        def change(b):
            hits = [t for t in b.get('tasks', []) if t['id'] == args[1]]
            if not hits:
                raise Fail(f'no task {args[1]} in {name}')
            if args[0] == 'verdict':
                hits[-1]['verdict'] = True
            else:
                for t in hits:
                    t['live'] = False
    else:
        raise Usage(f'task takes `add <{"|".join(KINDS)}> <id>`, `verdict <id>` or `done <id>`')
    update(store, name, change)


def fallthrough_ok(v):
    return all(re.fullmatch(r'[A-Za-z0-9_-]+:(' + '|'.join(WHYS) + r')', x) for x in v.split(','))


ROUND = {'r': lambda v: INT.fullmatch(v) and v != '0', 'sha': SHA.fullmatch, 'entry': INT.fullmatch,
         'fallthrough': fallthrough_ok, 'explore': lambda v: v in ('pass', 'revise', 'none'),
         'crit': INT.fullmatch, 'imp': INT.fullmatch, 'minor': INT.fullmatch}


def cmd_add_round(store, name, args):
    kv = pairs(args, ROUND)
    missing = [k for k in ROUND if k != 'fallthrough' and k not in kv]
    if missing:
        raise Fail('add-round needs ' + ', '.join(f'{k}=' for k in missing))
    r = {k: int(v) if k in ('r', 'entry', 'crit', 'imp', 'minor') else v for k, v in kv.items() if k != 'fallthrough'}
    r['fallthrough'] = kv['fallthrough'].split(',') if 'fallthrough' in kv else []
    update(store, name, lambda b: b.setdefault('rounds', []).append(r))


def worker_time(b, now):
    """Seconds the worker spent in the spec phase: each `spec_ready` or `stopped` minus the `sent` before it, plus
    now minus an open `sent`; an interval opened by `comment_sent` doesn't count, and `ended` closes any."""
    total, opened = 0.0, None
    for kind, t in b.get('stamps', []):
        if kind == 'sent':
            opened = t
        elif kind == 'comment_sent':
            opened = None
        elif kind in ('spec_ready', 'stopped', 'ended'):
            if opened is not None:
                total += t - opened
            opened = None
    if opened is not None:
        total += now - opened
    return int(total)


def settings(store):
    """(the reviewer chain, the 1-based position of its first entry eligible in spec mode or None, spec_phase_timeout)."""
    conf = cfg.load(store.main, use_env=False)
    chain = cfg.chain(store.main, use_env=False)
    first = next((i for i, e in enumerate(chain, 1) if spec_eligible(*e)), None)
    timeout = conf.get('spec_phase_timeout') or cfg.DEFAULTS['spec_phase_timeout']
    return chain, first, int(timeout)


def spec_eligible(use, model, _effort):
    """True when review.sh --spec would run this entry: its adapter carries `# foreman: reads-repo` and, for
    `custom`, its command uses both {prompt_file} and {base} (custom.sh falls through otherwise)."""
    path = os.path.join(HERE, '..', 'reviewers', f'{use}.sh')
    if not NAME.fullmatch(use or '') or not os.path.isfile(path):
        return False
    with open(path, encoding='utf-8') as f:
        if not re.search(r'^# foreman: reads-repo', f.read(), re.M):
            return False
    return use != 'custom' or ('{prompt_file}' in model and '{base}' in model)


def degraded(b, first):
    """'yes' when the last round's review wasn't the chain's first spec-eligible entry (or none landed, or no
    entry is eligible), 'no' when it was, '' before any round."""
    if first is None:
        return 'yes'
    rounds = b.get('rounds') or []
    if not rounds:
        return ''
    return 'no' if rounds[-1].get('entry') == first else 'yes'


def computed(b, conf, now):
    _, first, timeout = conf
    wt = worker_time(b, now)
    return dict(b, degraded=degraded(b, first), worker_time=wt, remaining=max(0, timeout * 60 - wt))


def cmd_show(store, args):
    as_json = '--json' in args
    names = [a for a in args if a != '--json']
    if len(names) > 1:
        raise Usage('show takes at most one batch')
    if names:
        b = store.read(names[0] + '.json') if NAME.fullmatch(names[0]) else None
        if b is None:
            raise Fail(f'no batch {names[0]} in session {store.sid}')
        out = computed(b, settings(store), time.time())
        if as_json:
            print(json.dumps(out, indent=1, sort_keys=True))
        else:
            for k in sorted(out):
                print(f'{k}: {json.dumps(out[k]) if isinstance(out[k], (list, dict)) else out[k]}')
        return
    conf, now = settings(store), time.time()
    out = [computed(b, conf, now) for b in store.batches()]
    if as_json:
        print(json.dumps(out, indent=1, sort_keys=True))
    else:
        for b in out:
            live = ' '.join(f"{t['kind']}:{t['id']}" for t in b.get('tasks', []) if t.get('live'))
            print(f"{b['batch']}  {status_cell(b)}  rounds {len(b.get('rounds') or [])}  "
                  f"remaining {b['remaining'] // 60}m  live tasks: {live or 'none'}")


# ---- tables ------------------------------------------------------------------------------------------------------

def status_cell(b):
    st = b.get('status') or 'spec'
    if st == 'parked':
        return 'parked: ' + REASONS.get(b.get('reason'), b.get('reason') or '?').format(n=len(b.get('rounds') or []))
    if st == 'pr':
        return f"PR #{b.get('pr')}"
    return st


def text_cell(b, field):
    items = [l.replace('|', '\\|') for l in b.get(field) or []]
    if not items:
        return '—'
    out = '; '.join(items[:3])
    return out + (f"; +{len(items) - 3} more (batches.py show {b['batch']})" if len(items) > 3 else '')


def reviewer_label(chain, entry):
    if not entry:
        return 'none'
    if entry > len(chain):
        return f'#{entry}'
    use, model, _ = chain[entry - 1]
    return f'{use} {model}'.strip() if use != 'custom' else 'custom'


def minutes(seconds):
    m = int(seconds // 60)
    return f'{m // 60}h{m % 60:02d}m' if m >= 60 else f'{m}m'


def wall_clock(b):
    sent = [t for k, t in b.get('stamps', []) if k == 'sent']
    ended = [t for k, t in b.get('stamps', []) if k == 'ended']
    return minutes(ended[-1] - sent[0]) if sent and ended else '—'


def table(head, rows):
    lines = ['| ' + ' | '.join(head) + ' |', '|' + '---|' * len(head)]
    lines += ['| ' + ' | '.join(r) + ' |' for r in rows]
    return '\n'.join(lines)


def table_id(text):
    return hashlib.sha1(text.encode()).hexdigest()[:8]


def render_checkpoint(store, wave, batches):
    """(text, id) of the W3.6 table for the wave's batches. No cell depends on the clock."""
    chain, first, _ = settings(store)
    rows = []
    for b in batches:
        rounds = b.get('rounds') or []
        last = rounds[-1] if rounds else None
        rev = '—'
        if last:
            rev = reviewer_label(chain, last.get('entry')) + (' ⚠ degraded' if degraded(b, first) == 'yes' else '')
        rows.append([b['batch'], (b.get('rows') or '—').replace(',', ', '), b.get('spec') or '—', str(len(rounds)),
                     rev, last.get('explore') if last else '—', text_cell(b, 'minors'), text_cell(b, 'questions'),
                     status_cell(b)])
    body = table(['batch', 'rows', 'spec', 'rounds', 'chain reviewer', 'read-only verdict', 'Minor findings',
                  'open questions', 'status'], rows)
    tid = table_id(body)
    return f'foreman checkpoint {wave} {tid}\n{body}', tid


def render_report(store, batches):
    """(text, id) of the W6 spec-phase table for the given batches."""
    chain, first, _ = settings(store)
    rows = []
    for b in batches:
        rounds = b.get('rounds') or []
        landed = '—'
        if rounds:
            last = rounds[-1]
            if not last.get('entry'):
                landed = 'no cross-model review'
            else:
                landed = reviewer_label(chain, last['entry'])
                if last.get('fallthrough'):
                    landed += ' (after ' + ', '.join(last['fallthrough']) + ')'
        rows.append([b['batch'], wall_clock(b), str(len(rounds)), landed, degraded(b, first) or '—',
                     b.get('reviewer_model') or '—', status_cell(b),
                     str(b.get('pr_rounds', 0)) if b.get('pr') else '—'])
    body = table(['batch', 'spec wall-clock', 'rounds', 'reviewer', 'degraded', 'reviewer model', 'status',
                  'rounds after the PR'], rows)
    tid = table_id(body)
    return f'foreman report {store.sid} {tid}\n{body}', tid


def cmd_table(store, args):
    if args[:1] == ['checkpoint'] and len(args) == 2 and NAME.fullmatch(args[1]):
        wave = args[1]
        with store.lock():
            bs = [b for b in store.batches() if wave_of(b['batch']) == wave]
            if not bs:
                raise Fail(f'no batches in wave {wave} (session {store.sid})')
            text, tid = render_checkpoint(store, wave, bs)
            store.write(f'{wave}.shown', {'at': time.time(), 'id': tid, 'text': text})
    elif args == ['report']:
        with store.lock():
            text, tid = render_report(store, store.batches())
            store.write('report.shown', {'at': time.time(), 'id': tid, 'text': text})
    else:
        raise Usage('table takes `checkpoint <wave>` or `report`')
    print(text)


# ---- CLI ---------------------------------------------------------------------------------------------------------

def run(argv):
    args = list(argv)
    given = None
    if '--session' in args:
        i = args.index('--session')
        if i + 1 >= len(args):
            raise Usage('--session needs an id')
        given = args[i + 1]
        del args[i:i + 2]
    if not args:
        raise Usage('no command')
    cmd, rest = args[0], args[1:]
    if cmd not in ('set', 'note', 'bump', 'stamp', 'task', 'add-round', 'show', 'table'):
        raise Usage(f'unknown command: {cmd}')
    if cmd not in ('show', 'table') and not rest:
        raise Usage(f'{cmd} needs a batch')
    store = open_store(given)
    if cmd == 'show':
        return cmd_show(store, rest)
    if cmd == 'table':
        return cmd_table(store, rest)
    name, more = rest[0], rest[1:]
    if not NAME.fullmatch(name):
        raise Usage(f'bad batch name: {name}')
    if cmd == 'set':
        return cmd_set(store, name, more)
    if cmd == 'add-round':
        return cmd_add_round(store, name, more)
    if cmd == 'task':
        return cmd_task(store, name, more)
    if len(more) != (2 if cmd == 'note' else 1):
        raise Usage(f'{cmd} takes {"a field and a file" if cmd == "note" else "one argument"}')
    return {'note': cmd_note, 'bump': cmd_bump, 'stamp': cmd_stamp}[cmd](store, name, *more)


def main(argv):
    try:
        run(argv[1:])
    except Usage as e:
        print(f'batches.py: {e}\n\n{__doc__}', file=sys.stderr)
        return 2
    except Fail as e:
        print(f'batches.py: {e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
