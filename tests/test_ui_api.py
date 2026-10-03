# tests/test_ui_api.py
import http.client
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
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
[ -n "$FAKE_GH_BLOCK" ] && [ "$1 $2" = "pr create" ] && while [ ! -e "$FAKE_GH_BLOCK" ]; do sleep 0.05; done
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

    def request(self, method, path, body=None, token=True, host=None, headers=None):
        extra = headers
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=30)
        headers = {'Host': host or f'127.0.0.1:{self.port}'}
        if token:
            headers['X-Foreman-Token'] = self.server.token
        if body is not None:
            headers['Content-Type'] = 'application/json'
        headers.update(extra or {})
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

    def test_api_needs_the_header_and_nothing_else(self):
        t = self.f.server.token
        self.assertEqual(self.f.request('GET', '/api/backlog')[0], 200)
        self.assertEqual(self.f.request('GET', '/api/backlog', token=False, headers={'X-Foreman-Token': t + 'x'})[0], 403)
        self.assertEqual(self.f.request('GET', '/api/backlog', token=False, headers={'X-Foreman-Token': '\u00e9'.encode().decode('latin-1')})[0], 403)
        self.assertEqual(self.f.request('GET', f'/api/backlog?t={t}', token=False)[0], 403)
        self.assertEqual(self.f.request('GET', '/api/backlog', token=False, headers={'Cookie': f'foreman_t={t}'})[0], 403)

    def test_static_files_need_no_token_and_nothing_sets_a_cookie(self):
        self.assertIn('/#t=', self.f.url)
        for path in ('/', '/ui/js/api.js', '/ui/app.css'):
            status, _, r = self.f.request('GET', path, token=False)
            self.assertEqual(status, 200, path)
            self.assertIsNone(r.getheader('Set-Cookie'), path)
        for method, path in (('GET', '/api/backlog'), ('GET', f'/?t={self.f.server.token}'), ('POST', '/api/ops/undo')):
            self.assertIsNone(self.f.request(method, path, body={} if method == 'POST' else None)[2].getheader('Set-Cookie'), path)

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
        status, data, _ = self.f.request('POST', '/api/ops/undo', {'uid': data['ops'][-1]['uid']})
        self.assertEqual(data['ops'], [])

    def test_ops_get_server_uids_and_the_version_grows(self):
        _, d1, _ = self.f.request('GET', '/api/backlog')
        _, d2, _ = self.f.request('POST', '/api/ops', {'op': 'move', 'id': 'IB-2', 'to': 'top', 'uid': 'mine'})
        _, d3, _ = self.f.request('POST', '/api/ops', {'op': 'move', 'id': 'IB-1', 'to': 'top'})
        self.assertLess(d1['version'], d2['version'])
        self.assertLess(d2['version'], d3['version'])
        uids = [o['uid'] for o in d3['ops']]
        self.assertNotIn('mine', uids)
        self.assertEqual(len(set(uids)), 2)

    def test_drop_and_rebase_with_a_stale_uid_are_409(self):
        self.f.request('POST', '/api/ops', {'op': 'move', 'id': 'IB-2', 'to': 'top'})
        _, data, _ = self.f.request('POST', '/api/ops', {'op': 'move', 'id': 'IB-1', 'to': 'top'})
        first, second = data['ops']
        status, res, _ = self.f.request('DELETE', '/api/ops/0', {'uid': second['uid']})
        self.assertEqual(status, 409)
        self.assertIn('the pending list changed; reloaded', res['error'])
        status, _, _ = self.f.request('POST', '/api/ops/1/rebase', {'uid': first['uid']})
        self.assertEqual(status, 409)
        self.assertEqual(self.f.request('GET', '/api/backlog')[1]['ops'], data['ops'])
        status, res, _ = self.f.request('DELETE', '/api/ops/0', {'uid': first['uid']})
        self.assertEqual(status, 200)
        self.assertEqual(res['ops'], [second])

    def test_undo_after_another_tabs_undo_is_409(self):
        self.f.request('POST', '/api/ops', {'op': 'move', 'id': 'IB-2', 'to': 'top'})
        _, data, _ = self.f.request('POST', '/api/ops', {'op': 'move', 'id': 'IB-1', 'to': 'top'})
        last = data['ops'][-1]['uid']
        self.assertEqual(self.f.request('POST', '/api/ops/undo', {'uid': last})[0], 200)
        status, res, _ = self.f.request('POST', '/api/ops/undo', {'uid': last})
        self.assertEqual(status, 409)
        self.assertEqual(len(self.f.request('GET', '/api/backlog')[1]['ops']), 1)

    def test_invalid_add_is_refused_up_front(self):
        status, data, _ = self.f.request('POST', '/api/ops', {'op': 'add', 'title': 'x', 'risk': 'huge', 'status': 'ready', 'reason': '', 'acceptance': '', 'source': '', 'insert': 'top'})
        self.assertEqual(status, 400)
        self.assertIn('add needs risk', data['error'])

    def test_cross_origin_post_is_refused(self):
        status, data, _ = self.f.request('POST', '/api/ops', {'op': 'move', 'id': 'IB-2', 'to': 'top'},
                                         headers={'Origin': 'http://127.0.0.1:1'})
        self.assertEqual(status, 403)
        self.assertIn('cross-origin', data['error'])
        status, data, _ = self.f.request('GET', '/api/backlog')
        self.assertEqual(data['ops'], [])

    def test_own_origin_post_succeeds(self):
        status, _, _ = self.f.request('POST', '/api/ops', {'op': 'move', 'id': 'IB-2', 'to': 'top'},
                                      headers={'Origin': f'http://localhost:{self.f.port}'})
        self.assertEqual(status, 200)

    def test_post_body_must_be_json(self):
        status, _, _ = self.f.request('POST', '/api/ops', {'op': 'move', 'id': 'IB-2', 'to': 'top'},
                                      headers={'Content-Type': 'text/plain'})
        self.assertEqual(status, 415)

    def test_bad_op_is_400(self):
        status, data, _ = self.f.request('POST', '/api/ops', {'op': 'set', 'id': 'IB-1', 'field': 'id', 'value': 'x'})
        self.assertEqual(status, 400)
        self.assertIn('set needs', data['error'])


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.close()
        os.environ.pop('FAKE_GH_FAIL', None)
        os.environ.pop('FAKE_GH_BLOCK', None)

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
            '+| IB-2 | Two | medium | deferred \u2014 later | B | s |',
            '+| IB-4 | New one | low | ready | Acc | ui |',
        ]))
        msg = git(self.f.work, 'log', '-1', '--format=%B', f'origin/{branch}')
        self.assertNotIn('Co-Authored-By', msg)
        gh = open(self.f.gh_log).read()
        self.assertIn('pr create --base main --head ' + branch, gh)
        status, data, _ = self.f.request('GET', '/api/backlog')
        self.assertEqual(data['ops'], [])
        self.assertEqual(len(git(self.f.work, 'worktree', 'list').splitlines()), 1)

    def test_publish_keeps_conflicts(self):
        self.f.request('POST', '/api/ops', {'op': 'status', 'id': 'IB-1', 'to': 'deferred', 'reason': ''})
        self.f.request('POST', '/api/ops', {'op': 'set', 'id': 'IB-2', 'field': 'title', 'value': 'Two!'})
        other = os.path.join(self.f.tmp, 'other')
        git(self.f.tmp, 'clone', '-q', self.f.origin, other)
        p = os.path.join(other, '.claude/improvement-backlog.md')
        text = open(p).read().replace('| IB-1 | One | low | ready | A | s |', '| IB-1 | One | low | done (#9) | A | s |')
        write(p, text)
        git(other, 'commit', '-qam', 'bookkeeping')
        git(other, 'push', '-q')
        status, res, _ = self.f.request('POST', '/api/publish')
        self.assertEqual((res['applied'], res['conflicts']), (1, 1))
        status, data, _ = self.f.request('GET', '/api/backlog?refresh=1')
        self.assertEqual(len(data['ops']), 1)
        self.assertEqual(data['results'][0]['result'], 'conflict')
        self.assertIn('done (#9)', data['results'][0]['reason'])

    def test_publish_of_noop_changes_clears_them(self):
        self.f.request('POST', '/api/ops', {'op': 'status', 'id': 'IB-1', 'to': 'deferred', 'reason': ''})
        self.f.request('POST', '/api/ops', {'op': 'move', 'id': 'IB-2', 'to': 'top'})
        other = os.path.join(self.f.tmp, 'other')
        git(self.f.tmp, 'clone', '-q', self.f.origin, other)
        p = os.path.join(other, '.claude/improvement-backlog.md')
        text = open(p).read().replace('| IB-1 | One | low | ready | A | s |', '| IB-1 | One | low | done (#9) | A | s |')
        write(p, text)
        git(other, 'commit', '-qam', 'bookkeeping')
        git(other, 'push', '-q')
        status, res, _ = self.f.request('POST', '/api/publish')
        self.assertEqual(status, 200, res)
        self.assertEqual((res['applied'], res['conflicts'], res['pr_url']), (0, 1, None))
        self.assertIn('already match main', res['message'])
        self.assertEqual([b for b in self.remote_branches() if b.startswith('backlog/ui-')], [])
        self.assertFalse(os.path.exists(self.f.gh_log) and 'pr create' in open(self.f.gh_log).read())
        ops = self.f.server.repo.load_ops()
        self.assertEqual([(o['op'], o['id']) for o in ops], [('status', 'IB-1')])
        status, res, _ = self.f.request('POST', '/api/publish')
        self.assertEqual(status, 200, res)

    def test_edits_made_during_publish_survive(self):
        repo = self.f.server.repo
        self.f.request('POST', '/api/ops', {'op': 'set', 'id': 'IB-2', 'field': 'title', 'value': 'Two!'})
        block = os.path.join(self.f.tmp, 'release-gh')
        os.environ['FAKE_GH_BLOCK'] = block
        result = {}
        t = threading.Thread(target=lambda: result.update(res=self.f.request('POST', '/api/publish')))
        t.start()
        try:
            for _ in range(200):
                if os.path.exists(self.f.gh_log) and 'pr create' in open(self.f.gh_log).read():
                    break
                time.sleep(0.05)
            self.assertTrue(repo.publishing.locked())
            repo.add_op({'op': 'status', 'id': 'IB-1', 'to': 'deferred', 'reason': ''})
        finally:
            write(block, '')
            t.join(30)
        status, res, _ = result['res']
        self.assertEqual((status, res['applied']), (200, 1), res)
        self.assertEqual([(o['op'], o['id']) for o in repo.load_ops()], [('status', 'IB-1')])

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
        self.assertEqual(len(git(self.f.work, 'worktree', 'list').splitlines()), 1)
        status, data, _ = self.f.request('GET', '/api/backlog')
        self.assertEqual(len(data['ops']), 1)

    def test_crlf_backlog_keeps_its_line_endings(self):
        other = os.path.join(self.f.tmp, 'other')
        git(self.f.tmp, 'clone', '-q', '-c', 'core.autocrlf=false', self.f.origin, other)
        p = os.path.join(other, '.claude/improvement-backlog.md')
        write(p, BACKLOG.replace('\n', '\r\n'))
        git(other, 'commit', '-qam', 'crlf')
        git(other, 'push', '-q')
        blob = subprocess.run(['git', 'show', 'origin/main:.claude/improvement-backlog.md'], cwd=other, capture_output=True).stdout
        self.assertIn(b'| IB-2 | Two | medium | ready | B | s |\r\n', blob)
        self.f.request('GET', '/api/backlog?refresh=1')
        status, data, _ = self.f.request('POST', '/api/ops', {'op': 'status', 'id': 'IB-2', 'to': 'deferred', 'reason': 'later'})
        self.assertEqual(data['results'], [{'result': 'applied', 'reason': ''}])
        status, res, _ = self.f.request('POST', '/api/publish')
        self.assertEqual((status, res['applied']), (200, 1), res)
        git(self.f.work, 'fetch', '-q', 'origin')
        new = subprocess.run(['git', 'show', f'origin/{res["branch"]}:.claude/improvement-backlog.md'], cwd=self.f.work, capture_output=True).stdout
        self.assertEqual(new, blob.replace(b'| IB-2 | Two | medium | ready | B | s |', '| IB-2 | Two | medium | deferred \u2014 later | B | s |'.encode()))

    def test_a_commit_that_differs_from_the_plan_is_not_pushed(self):
        hook = os.path.join(self.f.work, '.git', 'hooks', 'pre-commit')
        write(hook, '#!/bin/sh\necho "| IB-99 | Sneaked in | low | ready | x | y |" >> .claude/improvement-backlog.md\ngit add .claude/improvement-backlog.md\n')
        os.chmod(hook, 0o755)
        self.f.request('POST', '/api/ops', {'op': 'move', 'id': 'IB-2', 'to': 'top'})
        status, res, _ = self.f.request('POST', '/api/publish')
        self.assertEqual(status, 502, res)
        self.assertIn('not pushed', res['error'])
        self.assertEqual([b for b in self.remote_branches() if b.startswith('backlog/ui-')], [])
        self.assertEqual(len(self.f.server.repo.load_ops()), 1)

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


class IdTest(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.close()

    def push_from_other_clone(self, branch, extra_row):
        other = os.path.join(self.f.tmp, 'other-' + branch.replace('/', '-'))
        git(self.f.tmp, 'clone', '-q', self.f.origin, other)
        if branch != 'main':
            git(other, 'checkout', '-q', '-b', branch)
        write(os.path.join(other, '.claude/improvement-backlog.md'), extra_row + '\n', 'a')
        git(other, 'commit', '-qam', 'other')
        git(other, 'push', '-q', 'origin', f'HEAD:refs/heads/{branch}')

    def test_publish_numbers_past_an_open_ui_branch(self):
        self.push_from_other_clone('backlog/ui-1', '| IB-6 | Filed in another UI PR | low | ready | x | ui |')
        self.f.request('POST', '/api/ops', {'op': 'add', 'title': 'New one', 'risk': 'low', 'status': 'ready', 'reason': '', 'acceptance': 'Acc', 'source': 'ui', 'insert': 'bottom'})
        status, res, _ = self.f.request('POST', '/api/publish')
        self.assertEqual(status, 200, res)
        git(self.f.work, 'fetch', '-q', 'origin')
        diff = git(self.f.work, 'diff', 'origin/main', f'origin/{res["branch"]}')
        self.assertIn('+| IB-7 | New one |', diff)

    def test_duplicate_ids_show_in_the_notice(self):
        self.push_from_other_clone('main', '| IB-2 | Two again | low | ready | x | y |')
        status, data, _ = self.f.request('GET', '/api/backlog?refresh=1')
        self.assertIn('IB-2 appears twice', data['notice'])

    def test_bk_numbers_past_an_open_ui_branch(self):
        self.push_from_other_clone('backlog/ui-1', '| IB-6 | Filed in another UI PR | low | ready | x | ui |')
        rep = os.path.join(self.f.tmp, 'rep.json')
        write(rep, json.dumps({'pr': 9, 'new_rows': ['| IB-NEW-a | From the loop | low | ready | a | #9 |']}))
        out = subprocess.run([sys.executable, os.path.join(HERE, '..', 'scripts', 'bk.py'), self.f.work, rep],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn('| IB-7 | From the loop |', open(os.path.join(self.f.work, '.claude/improvement-backlog.md')).read())


class ProcessTest(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.close()

    def start(self):
        script = os.path.join(HERE, '..', 'scripts', 'ui.py')
        p = subprocess.Popen([sys.executable, script, '--repo', self.f.work, '--no-open'], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (subprocess.run([sys.executable, script, '--repo', self.f.work, '--stop'], capture_output=True), p.wait(30)))
        url = p.stdout.readline().strip()
        return url, int(re.search(r':(\d+)/', url).group(1)), re.search(r'[?#]t=(.+)$', url).group(1)

    def test_state_files_are_private(self):
        repo = self.f.server.repo
        repo.save_ops([])
        os.chmod(repo.ops_path(), 0o644)
        os.chmod(repo.state, 0o755)
        url, port, token = self.start()
        mode = lambda path: stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode(repo.state), 0o700)
        self.assertEqual(mode(os.path.join(repo.state, 'ui.json')), 0o600)
        self.assertEqual(mode(repo.ops_path()), 0o600)
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
        c.request('POST', '/api/ops', json.dumps({'op': 'move', 'id': 'IB-2', 'to': 'top'}),
                  {'Host': f'127.0.0.1:{port}', 'X-Foreman-Token': token, 'Content-Type': 'application/json'})
        self.assertEqual(c.getresponse().status, 200)
        self.assertEqual(mode(repo.ops_path()), 0o600)


if __name__ == '__main__':
    unittest.main()
