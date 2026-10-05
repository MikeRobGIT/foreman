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
Every command takes `--session <id>`. Exit 0 = done, 1 = refused (one line on stderr), 2 = usage.

Files: <state dir>/batches/<session>/state/<batch>.json in every state.state_dirs(main) entry (hooks.py forget
never reaches them); reads take the newest copy. Session: --session, else $FOREMAN_SESSION, else the most
recently touched live run marker, else `nosession`. Run scope: with a live run marker, `show` and the tables see
only batches named at or after the marker's `started` minute.
"""
import contextlib
import fcntl
import glob
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
NAME = re.compile(r'^[A-Za-z0-9_-]+$')
SHA = re.compile(r'^[0-9a-f]{7,40}$')
TOKEN = re.compile(r'^[A-Za-z0-9_.,/:@+-]+$')
TASK_ID = re.compile(r'^[A-Za-z0-9_.:@-]+$')
INT = re.compile(r'^(0|[1-9][0-9]*)$')
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
    'rows': TOKEN.match, 'owned': TOKEN.match, 'branch': TOKEN.match, 'spec': TOKEN.match,
    'worktree': lambda v: TOKEN.match(v) and v.startswith('/'),
    'fork_base': SHA.match, 'sha': SHA.match, 'reviewed_sha': SHA.match, 'released_sha': SHA.match,
    'verified_sha': SHA.match,
    'pr': lambda v: INT.match(v) and v != '0',
    'reviewer_model': TOKEN.match,
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
    """Epoch seconds of a `YYYYMMDD-HHMM-...` batch name (local time), or 0 for an undated name (hooks.batch_time)."""
    m = re.match(r'^(\d{8}-\d{4})-', batch)
    try:
        return time.mktime(time.strptime(m.group(1), '%Y%m%d-%H%M')) if m else 0
    except ValueError:
        return 0


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
            if NAME.match(n) and (since is None or batch_time(n) >= since):
                b = self.read(n + '.json')
                if isinstance(b, dict):
                    out.append(b)
        return out


def session_id(dirs, given):
    sid = given or os.environ.get('FOREMAN_SESSION')
    if not sid:
        live = []
        for d in dirs:
            for p in glob.glob(os.path.join(glob.escape(d), 'runs', '*.json')):
                m = state.read_json(p)
                if isinstance(m, dict) and time.time() - float(m.get('touched', 0)) < RUN_TTL:
                    live.append((float(m.get('touched', 0)), os.path.basename(p)[:-len('.json')]))
        if not live:
            return 'nosession'
        live.sort(reverse=True)
        sid = live[0][1]
        if len({s for _, s in live}) > 1:
            print(f'batches.py: {len({s for _, s in live})} live runs; using session {sid}, the most recently touched '
                  '(pass --session <id> to pick another)', file=sys.stderr)
    if not NAME.match(sid):
        raise Usage(f'bad session id: {sid}')
    return sid


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
    if not NAME.match(name):
        raise Usage(f'bad batch name: {name}')
    with store.lock():
        b = store.read(name + '.json') or new_batch(name)
        change(b)
        store.write(name + '.json', b)


def cmd_set(store, name, args):
    kv = pairs(args, FIELDS)

    def change(b):
        before = b.get('status')
        b.update({k: int(v) if k == 'pr' else v for k, v in kv.items()})
        st = b.get('status')
        if 'status' in kv:
            if st == 'passed' and 'reviewed_sha' not in kv:
                raise Fail('status=passed needs reviewed_sha=<sha> in the same call')
            if st == 'released' and not same_sha(b.get('released_sha'), b.get('reviewed_sha')):
                raise Fail(f"status=released needs released_sha equal to reviewed_sha ({b.get('reviewed_sha') or 'unset'})")
            if st == 'parked' and not b.get('reason'):
                raise Fail('status=parked needs reason=<reason>')
            if st != before and st == 'passed':
                stamp(b, 'passed')
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
        stamp(b, kind)
        if kind == 'comment_sent':
            b['status'] = 'review'
            b.pop('reviewed_sha', None)
    update(store, name, change)


def cmd_task(store, name, args):
    if args[:1] == ['add'] and len(args) == 3 and args[1] in KINDS and TASK_ID.match(args[2]):
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
    return all(re.match(r'^[A-Za-z0-9_-]+:(' + '|'.join(WHYS) + r')$', x) for x in v.split(','))


ROUND = {'r': lambda v: INT.match(v) and v != '0', 'sha': SHA.match, 'entry': INT.match,
         'fallthrough': fallthrough_ok, 'explore': lambda v: v in ('pass', 'revise', 'none'),
         'crit': INT.match, 'imp': INT.match, 'minor': INT.match}


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
    """spec_phase_timeout, in minutes, from the repo's own config (never FOREMAN_CONFIG, like the hooks)."""
    conf = cfg.load(store.main, use_env=False)
    return int(conf.get('spec_phase_timeout') or cfg.DEFAULTS['spec_phase_timeout'])


def computed(b, timeout, now):
    wt = worker_time(b, now)
    return dict(b, worker_time=wt, remaining=max(0, timeout * 60 - wt))


def cmd_show(store, args):
    as_json = '--json' in args
    names = [a for a in args if a != '--json']
    if len(names) > 1:
        raise Usage('show takes at most one batch')
    if names:
        b = store.read(names[0] + '.json') if NAME.match(names[0]) else None
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
            print(f"{b['batch']}  {b.get('status') or 'spec'}  rounds {len(b.get('rounds') or [])}  "
                  f"remaining {b['remaining'] // 60}m  live tasks: {live or 'none'}")


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
    if cmd not in ('set', 'note', 'bump', 'stamp', 'task', 'add-round', 'show'):
        raise Usage(f'unknown command: {cmd}')
    if cmd != 'show' and not rest:
        raise Usage(f'{cmd} needs a batch')
    store = open_store(given)
    if cmd == 'show':
        return cmd_show(store, rest)
    name, more = rest[0], rest[1:]
    if not NAME.match(name):
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
