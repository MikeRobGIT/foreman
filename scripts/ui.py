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
        ops = self.load_ops()
        if op.get('op') == 'add':
            n = 1 + max([int(o['tmp'].split('-')[1]) for o in ops if o.get('op') == 'add'] or [0])
            op = {**op, 'tmp': f'NEW-{n}'}
        backlog.check_op_shape(op)
        bl, _ = self.replay(ops)
        if op['op'] != 'add':
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
