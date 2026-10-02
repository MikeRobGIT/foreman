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


def write(path, text, mode='w'):
    with open(path, mode) as f:
        f.write(text)


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
        write(os.path.join(self.work, '.claude/improvement-backlog.md'), BACKLOG)
        write(os.path.join(self.work, '.claude/foreman.config.md'), CONFIG)
        git(self.work, 'symbolic-ref', 'HEAD', 'refs/heads/main')
        git(self.work, 'add', '-A')
        git(self.work, 'commit', '-q', '-m', 'init')
        git(self.work, 'push', '-q', '-u', 'origin', 'main')
        git(self.work, 'remote', 'set-head', 'origin', 'main')
        bin_dir = os.path.join(self.tmp, 'bin')
        os.makedirs(bin_dir)
        gh = os.path.join(bin_dir, 'gh')
        write(gh, FAKE_GH)
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
        write(os.path.join(self.f.work, '.claude/improvement-backlog.md'), '| IB-9 | Local only | low | ready | x | y |\n', 'a')
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
        self.assertIn('add needs risk', data['error'])

    def test_bad_op_is_400(self):
        status, data, _ = self.f.request('POST', '/api/ops', {'op': 'set', 'id': 'IB-1', 'field': 'id', 'value': 'x'})
        self.assertEqual(status, 400)
        self.assertIn('set needs', data['error'])


if __name__ == '__main__':
    unittest.main()
