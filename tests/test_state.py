import hashlib
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'scripts')
sys.path.insert(0, SCRIPTS)
import cfg  # noqa: E402
import state  # noqa: E402


def git(cwd, *args):
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@e', *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='foreman-state-'))
        self.repo = os.path.join(self.tmp, 'repo')
        os.makedirs(self.repo)
        git(self.repo, 'init', '-q', '-b', 'main')
        git(self.repo, 'commit', '-q', '--allow-empty', '-m', 'init')
        git(self.repo, 'remote', 'add', 'origin', 'https://example.com/acme/repo.git')
        self.env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def test_repo_key_matches_the_ui_formula(self):
        want = hashlib.sha1(f'https://example.com/acme/repo.git\n{self.repo}'.encode()).hexdigest()[:12]
        self.assertEqual(state.repo_key(self.repo), want)

    def test_main_checkout_from_a_worktree(self):
        wt = os.path.join(self.tmp, 'wt')
        git(self.repo, 'worktree', 'add', '-q', wt, '-b', 'w1')
        self.assertEqual(os.path.realpath(state.main_checkout(wt)), self.repo)
        self.assertEqual(state.repo_key(state.main_checkout(wt)), state.repo_key(self.repo))
        self.assertIsNone(state.main_checkout(self.tmp))

    def test_state_dirs_add_the_default_home_only_when_it_differs(self):
        os.environ['FOREMAN_HOME'] = os.path.join(self.tmp, 'home')
        dirs = state.state_dirs(self.repo)
        self.assertEqual(dirs[0], os.path.join(self.tmp, 'home', state.repo_key(self.repo)))
        self.assertEqual(dirs[1], os.path.join(state.DEFAULT_HOME, state.repo_key(self.repo)))
        os.environ['FOREMAN_HOME'] = state.DEFAULT_HOME
        self.assertEqual(len(state.state_dirs(self.repo)), 1)

    def test_write_json_is_private(self):
        p = os.path.join(self.tmp, 'h', 'k', 'runs', 's.json')
        state.write_json(p, {'a': 1})
        self.assertEqual(state.read_json(p), {'a': 1})
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(p)).st_mode & 0o777, 0o700)
        self.assertIsNone(state.read_json(os.path.join(self.tmp, 'missing.json')))

    def test_every_level_ensure_creates_is_private(self):
        # FOREMAN_HOME, <key>, batches, <sid>, agents: makedirs' own mode covers only the leaf.
        # The umask strips the owner's bits, so a level is only usable once ensure has chmodded it.
        leaf = os.path.join(self.tmp, 'fh', 'key', 'batches', 'sid', 'agents')
        old = os.umask(0o277)
        try:
            self.assertEqual(state.ensure(leaf), leaf)
        finally:
            os.umask(old)
        p = leaf
        while p != self.tmp:
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o700, p)
            p = os.path.dirname(p)
        self.assertEqual(os.path.relpath(leaf, self.tmp).count(os.sep), 4)

    def test_ensure_leaves_levels_that_already_existed_alone(self):
        pre = os.path.join(self.tmp, 'pre')
        os.mkdir(pre)
        os.chmod(pre, 0o755)
        state.ensure(os.path.join(pre, 'a', 'b'))
        self.assertEqual(os.stat(pre).st_mode & 0o777, 0o755)
        for sub in ('a', os.path.join('a', 'b')):
            self.assertEqual(os.stat(os.path.join(pre, sub)).st_mode & 0o777, 0o700)

    def test_cli_prints_the_state_dir(self):
        env = dict(os.environ, FOREMAN_HOME=os.path.join(self.tmp, 'home'))
        out = subprocess.run([sys.executable, os.path.join(SCRIPTS, 'state.py'), self.repo], env=env,
                             capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), os.path.join(self.tmp, 'home', state.repo_key(self.repo)))
        # outside a repo: nothing on stdout, exit 0 (callers read empty output as "no repo")
        out = subprocess.run([sys.executable, os.path.join(SCRIPTS, 'state.py'), self.tmp], env=env, capture_output=True)
        self.assertEqual((out.returncode, out.stdout), (0, b''))


class CfgTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='foreman-cfg-')
        os.makedirs(os.path.join(self.tmp, '.claude'))
        with open(os.path.join(self.tmp, '.claude', 'foreman.config.md'), 'w') as f:
            f.write('---\ngate: make check\n---\n')
        self.other = os.path.join(self.tmp, 'other.md')
        with open(self.other, 'w') as f:
            f.write('---\ngate: from-env\n---\n')
        self.env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def test_use_env_false_ignores_foreman_config(self):
        os.environ['FOREMAN_CONFIG'] = self.other
        self.assertEqual(cfg.config_path(self.tmp), self.other)
        self.assertEqual(cfg.config_path(self.tmp, use_env=False), os.path.join(self.tmp, '.claude', 'foreman.config.md'))
        self.assertEqual(cfg.load(self.tmp, use_env=False)['gate'], 'make check')
        os.environ['FOREMAN_CONFIG'] = os.path.join(self.tmp, 'nope.md')
        self.assertIsNone(cfg.config_path(self.tmp))
        self.assertIsNotNone(cfg.config_path(self.tmp, use_env=False))

    def test_new_keys_default(self):
        self.assertEqual(cfg.get(self.tmp, 'gate_exempt'), ['docs/**', '**/*.md'])
        self.assertEqual(cfg.get(self.tmp, 'design_spec_dir'), 'docs/superpowers/specs')
        self.assertEqual(cfg.get(self.tmp, 'worker_agent_types'), ['general-purpose', 'workflow-subagent'])
        self.assertEqual(cfg.get(self.tmp, 'forbid_trailers'), [])
        self.assertEqual(cfg.get(self.tmp, 'status_pane'), 'true')
        self.assertEqual(cfg.get(self.tmp, 'gate_exempt', ['x']), ['x'])
        self.assertIsNone(cfg.get(self.tmp, 'no_such_key'))


if __name__ == '__main__':
    unittest.main()
