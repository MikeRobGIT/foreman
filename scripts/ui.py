#!/usr/bin/env python3
"""Local backlog UI for foreman.

  python3 ui.py --repo DIR [--port N] [--no-open]   start (or print the URL of the running one)
  python3 ui.py --repo DIR --stop                   stop it

Reads the backlog from origin/<default branch> (never the working tree), keeps pending edits in
$FOREMAN_HOME (default ~/.claude/foreman)/<repo-key>/pending.json, and publishes them as a PR from
a temporary worktree. Binds 127.0.0.1 only; every request needs a local Host, and every /api request
the session token in an X-Foreman-Token header (the page reads it from the printed URL's #t= fragment).
"""
import argparse
import hashlib
import http.server
import json
import os
import re
import shutil
import signal
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from collections import Counter
from datetime import datetime

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


class Stale(Exception):
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
        os.makedirs(self.state, mode=0o700, exist_ok=True)
        os.chmod(self.state, 0o700)
        for name in ('pending.json', 'ui.json'):
            if os.path.exists(os.path.join(self.state, name)):
                os.chmod(os.path.join(self.state, name), 0o600)
        self.branch = self.default_branch()
        self.lock = threading.Lock()
        self.ops_lock = threading.Lock()
        self.publishing = threading.Lock()
        self.cleanup_lock = threading.Lock()
        self._pub = None
        self.notice = ''
        self.version = 0
        self._fetched = None
        self._blame = {}
        self._prs = (0, None)

    def git(self, *args, check=True, cwd=None):
        # Bytes, decoded by hand: text mode would turn a CRLF backlog into LF and every row would conflict.
        p = subprocess.run(['git', *args], cwd=cwd or self.path, capture_output=True)
        out, err = p.stdout.decode('utf-8'), p.stderr.decode('utf-8', 'replace')
        if check and p.returncode:
            raise GitError(f'git {" ".join(args)}: {err.strip() or out.strip()}')
        return out

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

    def load_store(self):
        """(version, ops) from one read of pending.json."""
        p = self.ops_path()
        if not os.path.exists(p):
            return self.version, []
        try:
            with open(p, encoding='utf-8') as f:
                data = json.load(f)
            if not (isinstance(data, dict) and isinstance(data.get('version'), int) and isinstance(data.get('ops'), list)):
                raise ValueError('not {version, ops}')
        except (ValueError, OSError):
            os.replace(p, f'{p}.bad-{int(time.time())}')
            self.notice = 'pending.json could not be read; it was set aside and the list starts empty'
            return self.version, []
        # The in-memory floor keeps the version rising even when the file was set aside or deleted.
        self.version = max(self.version, data['version'])
        return self.version, data['ops']

    def load_ops(self):
        return self.load_store()[1]

    def save_ops(self, ops):
        self.version = self.load_store()[0] + 1
        # mkstemp creates the file 0600; the rename keeps a crash from leaving half a file.
        fd, tmp = tempfile.mkstemp(dir=self.state, suffix='.json')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'version': self.version, 'ops': ops}, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.ops_path())

    def replay(self, ops, upto=None):
        _, text, _ = self.fetch()
        bl = backlog.parse(text, self.prefix)
        results = backlog.apply(bl, ops if upto is None else ops[:upto])
        return bl, results

    def add_op(self, op):
        with self.ops_lock:
            ops = self.load_ops()
            if op.get('op') == 'add':
                n = 1 + max([int(o['tmp'].split('-')[1]) for o in ops if o.get('op') == 'add'] or [0])
                op = {**op, 'tmp': f'NEW-{n}'}
            backlog.check_op_shape(op)
            op = {**op, 'uid': secrets.token_hex(8)}
            bl, _ = self.replay(ops)
            if op['op'] != 'add':
                i = bl.find(op['id'])
                if i is None:
                    raise ValueError(f'{op["id"]} is not in the backlog')
                if op['op'] == 'status' and bl.lines[i].status == 'done' and op['to'] != 'ready':
                    raise ValueError(f'{op["id"]} is done; it can only go back to ready')
                op = {**op, 'base': bl.lines[i].raw}
            _, results = self.replay(ops + [op])
            if results[-1]['result'] != 'applied':
                raise ValueError(results[-1]['reason'])
            ops.append(op)
            self.save_ops(ops)

    @staticmethod
    def expect(ops, i, uid):
        """The client names the op it saw at position i; anything else means another tab moved the list."""
        if not (0 <= i < len(ops) and ops[i].get('uid') == uid):
            raise Stale()

    def undo(self, uid):
        with self.ops_lock:
            ops = self.load_ops()
            if not ops and uid is None:
                return
            self.expect(ops, len(ops) - 1, uid)
            ops.pop()
            self.save_ops(ops)

    def drop(self, i, uid):
        with self.ops_lock:
            ops = self.load_ops()
            self.expect(ops, i, uid)
            ops.pop(i)
            self.save_ops(ops)

    def rebase(self, i, uid):
        with self.ops_lock:
            ops = self.load_ops()
            self.expect(ops, i, uid)
            if ops[i]['op'] == 'add':
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
        version, ops = self.load_store()
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
        dups = [f'{k} appears {"twice" if n == 2 else f"{n} times"}' for k, n in bl.duplicates().items()]
        notice = '; '.join(n for n in [self.notice, *dups] if n)
        self.notice = ''
        return {'version': version, 'sha': sha, 'branch': self.branch, 'fetched_at': at, 'prefix': self.prefix,
                'rows': rows, 'ops': ops, 'results': results, 'notice': notice}

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
        self._pub = st = {'wt': None, 'branch': None, 'pushed': False}
        try:
            ops = self.load_ops()
            if not ops:
                return {'pr_url': None, 'applied': 0, 'conflicts': 0, 'branch': None, 'message': 'nothing pending'}
            self.git('fetch', '-q', '--prune', 'origin', f'+refs/heads/{self.branch}:refs/remotes/origin/{self.branch}', backlog.UI_REFSPEC)
            base_sha = self.git('rev-parse', f'origin/{self.branch}').strip()
            st['branch'] = branch = self.next_branch()
            st['wt'] = wt = tempfile.mkdtemp(prefix='foreman-ui-')
            os.rmdir(wt)
            self.git('worktree', 'add', '-q', '-b', branch, wt, base_sha)
            path = os.path.join(wt, self.backlog_path)
            with open(path, encoding='utf-8', newline='') as f:
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
            floor = backlog.reserved_max_id(self.path, self.backlog_path, self.prefix, self.branch)
            mapping = backlog.assign_ids(bl, [o['tmp'] for o in applied if o['op'] == 'add'], floor)
            text = backlog.serialise(bl)
            with open(path, 'w', encoding='utf-8', newline='') as f:
                f.write(text)
            title = self.title_for(applied)
            self.git('add', self.backlog_path, cwd=wt)
            if subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=wt).returncode == 0:
                self.settle(applied)
                return {'pr_url': None, 'applied': 0, 'conflicts': len(conflicts), 'branch': None,
                        'message': 'nothing to publish: the pending changes already match main'}
            self.git('commit', '-q', '-m', title, cwd=wt)
            self.check_commit(wt, base_sha, text)
            st['pushed'] = True  # set first: a stop during the push must still delete what it made
            self.git('push', '-q', 'origin', f'HEAD:refs/heads/{branch}', cwd=wt)
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
            self.settle(applied)
            st['pushed'] = False  # success: the remote branch stays for the PR
            return {'pr_url': url, 'applied': len(applied), 'conflicts': len(conflicts), 'branch': branch, 'message': ''}
        except FileNotFoundError as e:
            raise PublishError(str(e))
        finally:
            self.cleanup_publish()
            self.publishing.release()

    def cleanup_publish(self):
        """Delete what an unfinished publish made: its pushed branch, worktree and local branch. Runs once."""
        with self.cleanup_lock:
            st, self._pub = self._pub, None
        if not st:
            return
        if st['pushed']:
            self.git('push', '-q', 'origin', '--delete', st['branch'], check=False)
        if st['wt'] and os.path.exists(st['wt']):
            self.git('worktree', 'remove', '--force', st['wt'], check=False)
        if st['branch']:
            self.git('branch', '-D', st['branch'], check=False)

    def check_commit(self, wt, base_sha, text):
        """The commit must change only the backlog, to exactly the text written (a commit hook could change either)."""
        changed = [n for n in self.git('diff', '--name-only', '-z', f'{base_sha}..HEAD', cwd=wt).split('\0') if n]
        want = subprocess.run(['git', 'hash-object', '--stdin', f'--path={self.backlog_path}'], cwd=wt,
                              input=text.encode('utf-8'), capture_output=True).stdout.decode().strip()
        got = self.git('rev-parse', f'HEAD:{self.backlog_path}', cwd=wt).strip()
        if changed != [self.backlog_path] or want != got:
            raise PublishError(f'the commit is not what the UI wrote (changed: {", ".join(changed) or "nothing"}; '
                               'a commit hook may have edited it); not pushed')

    def settle(self, applied):
        # Edits made while the publish ran are in pending.json now; remove only the ops that were handled.
        with self.ops_lock:
            ops = self.load_ops()
            for o in applied:
                if o in ops:
                    ops.remove(o)
            self.save_ops(ops)

    @staticmethod
    def title_for(applied):
        ids = {o['id'] for o in applied if o['op'] != 'add' and not re.fullmatch(r'NEW-\d+', o['id'])}  # a new row's edits are part of "add"
        adds = sum(1 for o in applied if o['op'] == 'add')
        parts = ([f'triage {len(ids)} row{"s" if len(ids) != 1 else ""}'] if ids else []) + ([f'add {adds}'] if adds else [])
        return 'docs(backlog): ' + ', '.join(parts)


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'foreman-ui'

    def log_message(self, *args):
        pass

    def host_ok(self):
        p = self.server.server_port
        return self.headers.get('Host', '') in (f'127.0.0.1:{p}', f'localhost:{p}')

    def token_ok(self):
        sent = self.headers.get('X-Foreman-Token', '').encode('utf-8', 'surrogateescape')
        return secrets.compare_digest(sent, self.server.token.encode())

    def send(self, code, body, ctype='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
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

    def origin_ok(self):
        p = self.server.server_port
        origin = self.headers.get('Origin')
        return origin is None or origin in (f'http://127.0.0.1:{p}', f'http://localhost:{p}')

    def route(self, method):
        if not self.host_ok():
            return self.send(403, {'error': 'this server only answers on 127.0.0.1 / localhost'})
        if method != 'GET':
            if not self.origin_ok():
                return self.send(403, {'error': 'cross-origin request refused'})
            has_body = int(self.headers.get('Content-Length') or 0) > 0
            if has_body and self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                return self.send(415, {'error': 'request body must be application/json'})
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        if method == 'GET' and u.path == '/':
            return self.static('index.html')
        if method == 'GET' and u.path.startswith('/ui/'):
            return self.static(u.path[len('/ui/'):])
        if not self.token_ok():
            return self.send(403, {'error': 'missing or wrong session token: open the URL printed by /foreman ui'})
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
                repo.undo(self.body().get('uid'))
                return self.send(200, repo.view())
            if method == 'DELETE' and len(parts) == 3 and parts[:2] == ['api', 'ops']:
                repo.drop(int(parts[2]), self.body().get('uid'))
                return self.send(200, repo.view())
            if method == 'POST' and len(parts) == 4 and parts[:2] == ['api', 'ops'] and parts[3] == 'rebase':
                repo.rebase(int(parts[2]), self.body().get('uid'))
                return self.send(200, repo.view())
            if method == 'POST' and u.path == '/api/publish':
                return self.send(200, repo.publish())
            return self.send(404, {'error': 'not found'})
        except Busy:
            return self.send(409, {'error': 'a publish is already running'})
        except Stale:
            return self.send(409, {'error': 'the pending list changed; reloaded'})
        except (ValueError, KeyError) as e:
            return self.send(400, {'error': str(e)})
        except (GitError, PublishError) as e:
            return self.send(502, {'error': str(e)})


def make_server(repo_path, port=0):
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.token = secrets.token_urlsafe(24)
    server.repo = Repo(repo_path)
    return server, f'http://127.0.0.1:{server.server_port}/#t={server.token}'


def shutdown(repo, wait=30):
    """Publish runs on a request thread, which dies with the process: give it `wait` seconds to finish
    (it cleans up after itself), then delete what it made so far."""
    if repo.publishing.acquire(timeout=wait):
        repo.publishing.release()
    else:
        repo.cleanup_publish()


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
    with os.fdopen(os.open(info, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
        json.dump({'pid': os.getpid(), 'port': server.server_port, 'url': url}, f)
    print(url, flush=True)
    if not a.no_open:
        webbrowser.open(url)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # so --stop runs the finally below
    try:
        server.serve_forever()
    finally:
        shutdown(server.repo)
        if os.path.exists(info):
            os.remove(info)


if __name__ == '__main__':
    main()
