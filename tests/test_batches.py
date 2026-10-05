# tests/test_batches.py: scripts/batches.py in a temp repo with a temp FOREMAN_HOME and HOME (never ~/.claude/foreman)
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.realpath(os.path.join(HERE, '..', 'scripts'))
BATCHES = os.path.join(SCRIPTS, 'batches.py')
HOOKS = os.path.join(SCRIPTS, 'hooks.py')
sys.path.insert(0, SCRIPTS)
import batches  # noqa: E402
import state  # noqa: E402

CONFIG = '---\ngate: echo gated\nreviewers:\n  - { use: codex, model: gpt-6.1-sol, effort: medium }\n  - { use: codex, model: gpt-5, effort: low }\n---\n'
SHA1, SHA2 = 'a' * 40, 'b' * 40


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def name_at(t, suffix):
    return time.strftime('%Y%m%d-%H%M', time.localtime(t)) + '-' + suffix


class Base(unittest.TestCase):
    config = CONFIG

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='foreman-batches-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = os.path.join(self.tmp, 'work')
        os.makedirs(self.work)
        subprocess.run(['git', 'init', '-q', '-b', 'main', self.work], check=True)
        write(os.path.join(self.work, '.claude/foreman.config.md'), self.config)
        self.home = os.path.join(self.tmp, 'home')
        self.user = os.path.join(self.tmp, 'user')
        os.makedirs(self.user)
        self.env = dict(os.environ, HOME=self.user, FOREMAN_HOME=self.home)
        for k in ('FOREMAN_SESSION', 'FOREMAN_CONFIG', 'CLAUDE_ENV_FILE'):
            self.env.pop(k, None)
        self.key = state.repo_key(self.work)
        self.dirs = [os.path.join(self.home, self.key), os.path.join(self.user, '.claude', 'foreman', self.key)]
        self.t0 = time.time() - 3600   # the run started an hour ago; batch names are dated after it
        self.b1, self.b2 = name_at(self.t0 + 120, 'w1a'), name_at(self.t0 + 120, 'w1b')
        self.marker('S1', started=self.t0)

    def marker(self, sid, started, touched=None, d=0):
        state.write_json(os.path.join(self.dirs[d], 'runs', sid + '.json'),
                         {'started': started, 'touched': touched or time.time(), 'args': ''})

    def run_cli(self, *args, env=None):
        return subprocess.run([sys.executable, BATCHES, *args], capture_output=True, text=True, cwd=self.work,
                              env=env or self.env)

    def ok(self, *args, **kw):
        p = self.run_cli(*args, '--session', 'S1', **kw)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def refused(self, *args):
        p = self.run_cli(*args, '--session', 'S1')
        self.assertEqual(p.returncode, 1, (args, p.stdout))
        return p.stderr

    def show(self, b):
        return json.loads(self.ok('show', b, '--json'))

    def round_at(self, b, sha, r=1):
        self.ok('add-round', b, f'r={r}', 'sha=' + sha, 'entry=1', 'explore=pass', 'crit=0', 'imp=0', 'minor=0')

    def file(self, b, d=0, sid='S1'):
        return os.path.join(self.dirs[d], 'batches', sid, 'state', b + '.json')

    def store(self, sid='S1'):
        return batches.Store(self.work, self.dirs, sid)

    def put(self, name, sid='S1', **fields):
        """Write a batch file straight into the first state dir (a fixture, not a CLI call)."""
        b = dict(batches.new_batch(name), **fields)
        state.write_json(os.path.join(self.dirs[0], 'batches', sid, 'state', name + '.json'), b)
        return b


class CommandTest(Base):
    def test_set_note_bump_stamp_task_add_round_and_show(self):
        self.ok('set', self.b1, 'status=spec', 'rows=IB-1,IB-2', 'owned=app.py,lib/x.py')
        notes = os.path.join(self.tmp, 'minors.txt')
        write(notes, 'first finding\n\n  \nsecond finding\n')
        self.ok('note', self.b1, 'minors', notes)
        self.ok('bump', self.b1, 'bounces')
        self.ok('bump', self.b1, 'bounces')
        self.ok('stamp', self.b1, 'sent')
        self.ok('task', self.b1, 'add', 'worker', 'fw-' + self.b1)
        self.ok('task', self.b1, 'add', 'reviewer', 'agent-7f3')
        self.ok('task', self.b1, 'verdict', 'agent-7f3')
        self.ok('task', self.b1, 'done', 'agent-7f3')
        self.ok('add-round', self.b1, 'r=1', 'sha=' + SHA1, 'entry=2', 'fallthrough=codex:quota', 'explore=revise',
                'crit=0', 'imp=2', 'minor=1')
        b = self.show(self.b1)
        self.assertEqual((b['status'], b['rows'], b['owned']), ('spec', 'IB-1,IB-2', 'app.py,lib/x.py'))
        self.assertEqual((b['minors'], b['bounces'], b['pr_rounds']), (['first finding', 'second finding'], 2, 0))
        self.assertEqual([k for k, _ in b['stamps']], ['sent'])
        self.assertEqual([(t['kind'], t['id'], t['verdict'], t['live']) for t in b['tasks']],
                         [('worker', 'fw-' + self.b1, False, True), ('reviewer', 'agent-7f3', True, False)])
        self.assertEqual(b['rounds'], [{'r': 1, 'sha': SHA1, 'entry': 2, 'fallthrough': ['codex:quota'],
                                        'explore': 'revise', 'crit': 0, 'imp': 2, 'minor': 1}])
        self.assertEqual(b['degraded'], 'yes')   # entry 2 isn't the first eligible entry
        self.assertGreaterEqual(b['remaining'], 60 * 60 - 5)
        listing = self.ok('show')
        self.assertIn(self.b1, listing)
        self.assertIn('worker:fw-' + self.b1, listing)

    def test_next_js_paths_round_trip(self):
        owned = 'app/home/[account]/page.tsx,app/(marketing)/layout.tsx,app/[...slug]/@modal/(.)x+y.tsx'
        spec = 'docs/specs/app/(marketing)/[id]-design.md'
        wt = os.path.join(self.tmp, 'wt/[x]/(y)')
        self.ok('set', self.b1, 'owned=' + owned, 'spec=' + spec, 'worktree=' + wt, 'reviewer_model=opus[1m]')
        b = self.show(self.b1)
        self.assertEqual((b['owned'], b['spec'], b['worktree'], b['reviewer_model']), (owned, spec, wt, 'opus[1m]'))
        for kv in ('owned=a b.tsx', 'owned=a;b', 'owned=$X', 'owned=a{b}', "owned=a'b", 'worktree=rel/[x]',
                   'rows=IB-[1]'):
            self.assertIn('bad value for', self.refused('set', self.b1, kv), kv)

    def test_refusals(self):
        self.assertIn('unknown key: colour', self.refused('set', self.b1, 'colour=red'))
        self.assertIn('bad value for status: done', self.refused('set', self.b1, 'status=done'))
        self.assertIn('bad value for sha: xyz', self.refused('set', self.b1, 'sha=xyz'))
        self.assertIn('bad value for reason: tired', self.refused('set', self.b1, 'status=parked', 'reason=tired'))
        self.assertIn('status=parked needs reason', self.refused('set', self.b1, 'status=parked'))
        self.assertIn('status=passed needs reviewed_sha', self.refused('set', self.b1, 'status=passed'))
        self.assertIn('reason=<reason> goes with status=parked', self.refused('set', self.b1, 'reason=no-pass'))
        self.round_at(self.b1, SHA1)
        self.ok('set', self.b1, 'status=passed', 'reviewed_sha=' + SHA1)
        self.assertIn('released_sha equal to reviewed_sha', self.refused('set', self.b1, 'status=released',
                                                                         'released_sha=' + SHA2))
        self.assertIn('bad value for fallthrough', self.refused('add-round', self.b1, 'r=1', 'sha=' + SHA1, 'entry=1',
                                                                'fallthrough=codex:tired', 'explore=pass', 'crit=0',
                                                                'imp=0', 'minor=0'))
        self.assertIn('add-round needs explore=', self.refused('add-round', self.b1, 'r=1', 'sha=' + SHA1, 'entry=1',
                                                               'crit=0', 'imp=0', 'minor=0'))
        self.assertIn('no task nope', self.refused('task', self.b1, 'done', 'nope'))
        self.assertEqual(self.show(self.b1)['status'], 'passed')   # every refusal left the file as it was
        for args in (('bump', self.b1, 'rounds'), ('stamp', self.b1, 'later'), ('task', self.b1, 'add', 'ghost', 'x'),
                     ('nope',), ('set',), ('set', 'bad name'), ('show', '--session')):
            self.assertEqual(self.run_cli(*args).returncode, 2, args)

    def test_a_release_set_needs_the_reviewed_sha(self):
        self.round_at(self.b1, SHA1)
        self.ok('set', self.b1, 'status=passed', 'reviewed_sha=' + SHA1)
        self.ok('set', self.b1, 'status=released', 'released_sha=' + SHA1[:12])   # an abbreviation of the same SHA
        self.assertEqual([k for k, _ in self.show(self.b1)['stamps']], ['passed', 'ended'])

    def test_a_reviewed_sha_needs_a_pass_at_the_last_rounds_sha(self):
        self.assertIn('reviewed_sha=<sha> goes with status=passed', self.refused('set', self.b1, 'reviewed_sha=' + SHA1))
        self.assertIn('the last round is at (none)', self.refused('set', self.b1, 'status=passed', 'reviewed_sha=' + SHA1))
        self.round_at(self.b1, SHA1)
        self.round_at(self.b1, SHA2, r=2)
        self.assertIn(f'the last round is at {SHA2}',
                      self.refused('set', self.b1, 'status=passed', 'reviewed_sha=' + SHA1))   # an earlier round's SHA
        self.ok('set', self.b1, 'status=passed', 'reviewed_sha=' + SHA2[:12])
        self.assertIn('reviewed_sha=<sha> goes with status=passed', self.refused('set', self.b1, 'reviewed_sha=' + SHA1))
        self.assertEqual(self.show(self.b1)['reviewed_sha'], SHA2[:12])

    def test_a_re_pass_at_a_new_sha_is_stamped_again(self):
        self.round_at(self.b1, SHA1)
        self.ok('set', self.b1, 'status=passed', 'reviewed_sha=' + SHA1)
        self.ok('set', self.b1, 'status=passed', 'reviewed_sha=' + SHA1[:12])   # the same SHA: no new stamp
        self.assertEqual([k for k, _ in self.show(self.b1)['stamps']], ['passed'])
        self.round_at(self.b1, SHA2, r=2)
        self.ok('set', self.b1, 'status=passed', 'reviewed_sha=' + SHA2)
        b = self.show(self.b1)
        self.assertEqual(([k for k, _ in b['stamps']], b['reviewed_sha']), (['passed', 'passed'], SHA2))

    def test_comment_sent_needs_a_passed_batch(self):
        self.ok('set', self.b1, 'status=parked', 'reason=no-pass')
        self.assertIn('stamp comment_sent needs status=passed (parked)', self.refused('stamp', self.b1, 'comment_sent'))
        self.assertIn('(spec)', self.refused('stamp', self.b2, 'comment_sent'))   # no file yet
        b = self.show(self.b1)
        self.assertEqual((b['status'], b['reason'], b['stamps'][-1][0]), ('parked', 'no-pass', 'ended'))

    def test_status_pr_needs_a_pr_number(self):
        self.assertIn('status=pr needs pr=<n> in the same call', self.refused('set', self.b1, 'status=pr'))
        self.ok('set', self.b1, 'status=pr', 'pr=12')
        self.assertEqual(batches.status_cell(self.show(self.b1)), 'PR #12')

    def test_a_trailing_newline_is_refused(self):
        self.assertIn('bad value for rows', self.refused('set', self.b1, 'rows=IB-1\n'))
        self.assertIn('bad value for sha', self.refused('set', self.b1, 'sha=' + SHA1 + '\n'))
        self.assertIn('bad value for owned', self.refused('set', self.b1, 'owned=app.py\n'))
        self.assertIn('bad value for r', self.refused('add-round', self.b1, 'r=1\n', 'sha=' + SHA1, 'entry=1',
                                                      'explore=pass', 'crit=0', 'imp=0', 'minor=0'))
        self.assertIn('bad value for fallthrough', self.refused('add-round', self.b1, 'r=1', 'sha=' + SHA1, 'entry=1',
                                                                'fallthrough=codex:quota\n', 'explore=pass',
                                                                'crit=0', 'imp=0', 'minor=0'))
        self.assertEqual(self.run_cli('set', self.b1 + '\n', 'status=spec', '--session', 'S1').returncode, 2)
        self.assertEqual(self.run_cli('show', '--session', 'S1\n').returncode, 2)
        self.assertFalse(os.path.exists(self.file(self.b1)))

    def test_comment_sent_moves_a_passed_batch_back_to_review(self):
        self.round_at(self.b1, SHA1)
        self.ok('set', self.b1, 'status=passed', 'reviewed_sha=' + SHA1)
        self.ok('stamp', self.b1, 'comment_sent')
        b = self.show(self.b1)
        self.assertEqual((b['status'], 'reviewed_sha' in b), ('review', False))
        self.assertIn('released_sha equal to reviewed_sha (unset)',
                      self.refused('set', self.b1, 'status=released', 'released_sha=' + SHA1))

    def test_a_corrupt_file_is_refused_and_left_untouched(self):
        write(self.file(self.b1), '{not json')
        self.assertIn('corrupt batch state: ' + self.file(self.b1), self.refused('set', self.b1, 'status=spec'))
        with open(self.file(self.b1)) as f:
            self.assertEqual(f.read(), '{not json')

    def test_no_temp_file_is_left_behind(self):
        self.ok('set', self.b1, 'status=spec')
        self.refused('set', self.b1, 'status=passed')
        for d in (0, 1):
            left = os.listdir(os.path.dirname(self.file(self.b1, d)))
            self.assertEqual(sorted(left), sorted([self.b1 + '.json'] + (['.lock'] if d == 0 else [])))

    def test_concurrent_calls_all_land(self):
        procs = [subprocess.Popen([sys.executable, BATCHES, 'bump', self.b1, 'pr_rounds', '--session', 'S1'],
                                  cwd=self.work, env=self.env) for _ in range(12)]
        procs += [subprocess.Popen([sys.executable, BATCHES, 'set', self.b1, kv, '--session', 'S1'], cwd=self.work,
                                   env=self.env) for kv in ('rows=IB-1', 'owned=app.py')]
        self.assertEqual([p.wait() for p in procs], [0] * 14)
        b = self.show(self.b1)
        self.assertEqual((b['pr_rounds'], b['rows'], b['owned']), (12, 'IB-1', 'app.py'))

    def test_a_worktree_cwd_writes_the_main_checkouts_state(self):
        subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@e', 'commit', '-q', '--allow-empty', '-m', 'i'],
                       cwd=self.work, check=True)
        wt = os.path.join(self.tmp, 'wt')
        subprocess.run(['git', 'worktree', 'add', '-q', wt, '-b', 'w1'], cwd=self.work, check=True)
        p = subprocess.run([sys.executable, BATCHES, 'set', self.b1, 'status=spec', '--session', 'S1'],
                           capture_output=True, text=True, cwd=wt, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(state.read_json(self.file(self.b1))['status'], 'spec')

    def test_writes_reach_every_state_dir(self):
        self.ok('set', self.b1, 'status=spec')
        for d in (0, 1):
            self.assertEqual(state.read_json(self.file(self.b1, d))['status'], 'spec')

    def test_readers_take_the_newest_copy(self):
        self.ok('set', self.b1, 'status=spec')
        newer = dict(state.read_json(self.file(self.b1, 1)), status='review')
        state.write_json(self.file(self.b1, 1), newer)
        os.utime(self.file(self.b1, 0), (time.time() - 60, time.time() - 60))
        self.assertEqual(self.show(self.b1)['status'], 'review')
        self.ok('bump', self.b1, 'bounces')   # a write starts from the newest copy and brings both dirs level
        self.assertEqual(state.read_json(self.file(self.b1, 0))['status'], 'review')

    def test_a_partial_write_exits_0_with_a_note_and_a_retry_is_not_needed(self):
        self.ok('set', self.b1, 'status=spec')
        os.remove(self.file(self.b1, 1))
        os.makedirs(self.file(self.b1, 1))   # the second dir's target can't be replaced
        p = self.run_cli('stamp', self.b1, 'sent', '--session', 'S1')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(f'{self.b1}.json not written in {os.path.dirname(self.file(self.b1, 1))}', p.stderr)
        self.assertEqual([k for k, _ in self.show(self.b1)['stamps']], ['sent'])
        self.assertEqual([f for f in os.listdir(os.path.dirname(self.file(self.b1, 1))) if f.endswith('.tmp')], [])

    def test_hooks_forget_leaves_the_state_file(self):
        self.ok('set', self.b1, 'status=parked', 'reason=no-pass')
        self.marker('S1', started=self.t0, d=1)   # hooks.py sees the default home only when FOREMAN_HOME is unset
        env = dict(self.env)
        env.pop('FOREMAN_HOME')
        payload = {'hook_event_name': 'PreToolUse', 'session_id': 'S1', 'cwd': self.work, 'tool_name': 'Bash',
                   'tool_input': {'command': f'python3 {HOOKS} forget {self.b1}'}}
        p = subprocess.run([sys.executable, HOOKS, 'pretool'], input=json.dumps(payload), capture_output=True,
                           text=True, env=env, cwd=self.work)
        self.assertEqual((p.returncode, p.stdout), (0, ''), p.stderr)
        self.assertTrue(os.path.exists(self.file(self.b1, 1)))
        self.assertIn(f'| {self.b1} |', self.ok('table', 'report'))


class SessionTest(Base):
    def test_session_flag_env_newest_marker_and_nosession(self):
        self.ok('set', self.b1, 'status=spec')
        self.assertEqual(json.loads(self.run_cli('show', '--json', env=self.env).stdout)[0]['batch'], self.b1)
        env = dict(self.env, FOREMAN_SESSION='S9')
        self.assertEqual(self.run_cli('set', self.b2, 'status=spec', env=env).returncode, 0)
        self.assertTrue(os.path.exists(self.file(self.b2, sid='S9')))
        self.marker('S2', started=self.t0, touched=time.time() + 5)
        p = self.run_cli('set', self.b2, 'status=review')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('2 live runs; using session S2', p.stderr)
        self.assertTrue(os.path.exists(self.file(self.b2, sid='S2')))
        for sid in ('S1', 'S2'):
            os.remove(os.path.join(self.dirs[0], 'runs', sid + '.json'))
        p = self.run_cli('set', self.b2, 'status=spec')
        self.assertEqual((p.returncode, p.stderr), (0, ''))
        self.assertTrue(os.path.exists(self.file(self.b2, sid='nosession')))
        self.assertEqual(self.run_cli('show', '--session', '../S1').returncode, 2)

    def test_an_alias_names_the_run_it_continues(self):
        state.write_json(os.path.join(self.dirs[0], 'runs', 'S2.json'), {'alias': 'S1', 'touched': time.time() + 5})
        p = self.run_cli('set', self.b1, 'status=spec', '--session', 'S2')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.path.exists(self.file(self.b1)))
        self.assertFalse(os.path.exists(self.file(self.b1, sid='S2')))
        for env in (dict(self.env, FOREMAN_SESSION='S2'), self.env):   # the env, and the newest-marker fallback
            p = self.run_cli('show', '--json', env=env)
            self.assertEqual(([b['batch'] for b in json.loads(p.stdout)], p.stderr), ([self.b1], ''))
        state.write_json(os.path.join(self.dirs[0], 'runs', 'S3.json'), {'alias': '../S1', 'touched': time.time()})
        self.assertEqual(json.loads(self.run_cli('show', '--json', '--session', 'S3').stdout), [])

    def test_outside_a_foreman_repo(self):
        os.remove(os.path.join(self.work, '.claude/foreman.config.md'))
        p = self.run_cli('show', '--session', 'S1')
        self.assertEqual(p.returncode, 1)
        self.assertIn('no state dir', p.stderr)


class WorkerTimeTest(unittest.TestCase):
    def test_comment_rounds_and_stops_are_left_out(self):
        b = {'stamps': [['sent', 100], ['spec_ready', 400], ['sent', 1000], ['stopped', 1100], ['sent', 2000],
                        ['spec_ready', 2050], ['passed', 2050], ['comment_sent', 3000], ['spec_ready', 9000],
                        ['sent', 10000]]}
        self.assertEqual(batches.worker_time(b, now=10030), 300 + 100 + 50 + 30)
        b['stamps'].append(['ended', 10060])   # parking ends the open interval
        self.assertEqual(batches.worker_time(b, now=99999), 300 + 100 + 50 + 60)


class ScopeTest(Base):
    def test_run_scope(self):
        old = name_at(self.t0 - 7200, 'w1a')    # a batch of an earlier run in this session (/loop)
        self.put(old, status='released')
        self.put(self.b1, status='spec')
        self.assertEqual([b['batch'] for b in self.store().batches()], [self.b1])
        self.assertNotIn(old, self.ok('table', 'report'))
        self.assertNotIn(old, self.ok('show'))
        self.assertIn(self.b1, self.ok('show'))
        os.remove(os.path.join(self.dirs[0], 'runs', 'S1.json'))   # no live marker: the whole session
        self.assertEqual([b['batch'] for b in self.store().batches()], [old, self.b1])


class TableTest(Base):
    def fixture(self):
        self.put(self.b1, status='passed', rows='IB-1,IB-2', spec='docs/superpowers/specs/x-design.md',
                 reviewed_sha=SHA1, minors=['a | b', 'two', 'three', 'four'], questions=['- **Non-blocking:** colour?'],
                 rounds=[{'r': 1, 'sha': SHA1, 'entry': 1, 'fallthrough': [], 'explore': 'revise', 'crit': 0,
                          'imp': 1, 'minor': 0},
                         {'r': 2, 'sha': SHA2, 'entry': 2, 'fallthrough': ['codex:quota'], 'explore': 'pass',
                          'crit': 0, 'imp': 0, 'minor': 4}],
                 stamps=[['sent', 1000], ['spec_ready', 1500], ['passed', 1600]], reviewer_model='opus')
        self.put(self.b2, status='parked', reason='no-pass', rows='IB-3', spec='docs/superpowers/specs/y-design.md',
                 rounds=[{'r': k, 'sha': SHA1, 'entry': 0, 'fallthrough': ['codex:quota', 'codex:error'],
                          'explore': 'revise', 'crit': 1, 'imp': 0, 'minor': 0} for k in (1, 2, 3)],
                 stamps=[['sent', 1000], ['ended', 1000 + 3 * 3600 + 5 * 60]])

    CHECKPOINT = '''| batch | rows | spec | rounds | chain reviewer | read-only verdict | Minor findings | open questions | status |
|---|---|---|---|---|---|---|---|---|
| {b1} | IB-1, IB-2 | docs/superpowers/specs/x-design.md | 2 | codex gpt-5 ⚠ degraded | pass | a \\| b; two; three; +1 more (batches.py show {b1}) | - **Non-blocking:** colour? | passed |
| {b2} | IB-3 | docs/superpowers/specs/y-design.md | 3 | none ⚠ degraded | revise | — | — | parked: no pass in 3 rounds |'''

    REPORT = '''| batch | spec wall-clock | rounds | reviewer | degraded | reviewer model | status | rounds after the PR |
|---|---|---|---|---|---|---|---|
| {b1} | — | 2 | codex gpt-5 (after codex:quota) | yes | opus | passed | — |
| {b2} | 3h05m | 3 | no cross-model review | yes | — | parked: no pass in 3 rounds | — |'''

    def test_both_tables_against_fixed_fixtures(self):
        self.fixture()
        body = self.CHECKPOINT.format(b1=self.b1, b2=self.b2)
        out = self.ok('table', 'checkpoint', batches.wave_of(self.b1))
        self.assertEqual(out, f'foreman checkpoint {batches.wave_of(self.b1)} {batches.table_id(body)}\n{body}\n')
        stamp = state.read_json(os.path.join(self.dirs[0], 'batches', 'S1', 'state', batches.wave_of(self.b1) + '.shown'))
        self.assertEqual((stamp['text'] + '\n', stamp['id']), (out, batches.table_id(body)))
        body = self.REPORT.format(b1=self.b1, b2=self.b2)
        out = self.ok('table', 'report')
        self.assertEqual(out, f'foreman report S1 {batches.table_id(body)}\n{body}\n')
        self.assertTrue(os.path.exists(os.path.join(self.dirs[1], 'batches', 'S1', 'state', 'report.shown')))

    def test_no_rendered_cell_depends_on_the_clock(self):
        self.fixture()
        self.put(self.b2, status='review', stamps=[['sent', 1000]], tasks=[{'kind': 'timer', 'id': 'bg1',
                 'added': 1000, 'verdict': False, 'live': True}])
        store, bs = self.store(), self.store().batches()
        wave = batches.wave_of(self.b1)
        first = (batches.render_checkpoint(store, wave, bs), batches.render_report(store, bs))
        real = time.time
        try:
            time.time = lambda: real() + 7 * 86400
            again = (batches.render_checkpoint(store, wave, bs), batches.render_report(store, bs))
        finally:
            time.time = real
        self.assertEqual(first, again)

    def checkpoint_id(self):
        store = self.store()
        wave = batches.wave_of(self.b1)
        return batches.render_checkpoint(store, wave, [b for b in store.batches() if batches.wave_of(b['batch']) == wave])[1]

    def test_the_checkpoint_id_follows_the_wave_and_nothing_else(self):
        self.fixture()
        other = name_at(self.t0 + 120, 'w2a')
        self.put(other, status='spec')
        ids = [self.checkpoint_id()]
        self.ok('set', other, 'status=review')                    # another wave: same id
        ids.append(self.checkpoint_id())
        self.ok('stamp', self.b1, 'comment_sent')                 # a status change
        ids.append(self.checkpoint_id())
        self.ok('add-round', self.b1, 'r=3', 'sha=' + SHA2, 'entry=1', 'explore=pass', 'crit=0', 'imp=0', 'minor=1')
        ids.append(self.checkpoint_id())                          # a new round
        self.ok('set', self.b1, 'status=passed', 'reviewed_sha=' + SHA2)
        ids.append(self.checkpoint_id())                          # passed again after the comment round (scenario A')
        notes = os.path.join(self.tmp, 'm.txt')
        write(notes, 'a new minor finding\n')
        self.ok('note', self.b1, 'minors', notes)
        ids.append(self.checkpoint_id())                          # new Minor findings
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(len(set(ids[1:])), 5, ids)

    def test_text_cells(self):
        b = {'batch': 'x', 'minors': ['a|b', 'c', 'd', 'e', 'f']}
        self.assertEqual(batches.text_cell(b, 'minors'), 'a\\|b; c; d; +2 more (batches.py show x)')
        self.assertEqual(batches.text_cell(dict(b, minors=['one', 'two']), 'minors'), 'one; two')
        self.assertEqual(batches.text_cell(b, 'questions'), '—')

    def test_an_empty_wave_is_refused(self):
        self.assertIn('no batches in wave', self.refused('table', 'checkpoint', batches.wave_of(self.b1)))


class DegradedTest(Base):
    def first(self, config):
        write(os.path.join(self.work, '.claude/foreman.config.md'), config)
        return batches.settings(self.store())[1]

    def test_the_first_entry_eligible_in_spec_mode(self):
        rounds = lambda e: {'rounds': [{'entry': e}]}   # noqa: E731
        first = self.first(CONFIG)
        self.assertEqual((first, batches.degraded(rounds(1), first), batches.degraded(rounds(2), first),
                          batches.degraded(rounds(0), first), batches.degraded({}, first)), (1, 'no', 'yes', 'yes', ''))
        # the first entry can't run in spec mode (no reads-repo marker), so the second is the one to land
        first = self.first('---\nreviewers:\n  - { use: prime-agent, model: m }\n  - { use: codex, model: m }\n---\n')
        self.assertEqual((first, batches.degraded(rounds(2), first)), (2, 'no'))
        # a legacy extra_reviewers command without both placeholders is never eligible
        first = self.first('---\nextra_reviewers:\n  - codex review --base main\n---\n')
        self.assertEqual((first, batches.degraded(rounds(1), first)), (None, 'yes'))
        first = self.first('---\nextra_reviewers:\n  - cat {prompt_file}; echo {base}\n---\n')
        self.assertEqual(first, 1)
        first = self.first('---\ngate: x\n---\n')   # an empty chain is always degraded
        self.assertEqual((first, batches.degraded({}, first)), (None, 'yes'))


if __name__ == '__main__':
    unittest.main()
