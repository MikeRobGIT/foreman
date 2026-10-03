# tests/test_hooks.py: feeds hook JSON to scripts/hooks.py in temp repos (temp FOREMAN_HOME, fake gh)
import calendar
import contextlib
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.realpath(os.path.join(HERE, '..', 'scripts'))
HOOKS = os.path.join(SCRIPTS, 'hooks.py')
GATE_LOCK = os.path.join(SCRIPTS, 'gate-lock.sh')
sys.path.insert(0, SCRIPTS)
import hooks  # noqa: E402
import state  # noqa: E402

CONFIG = '''---
gate: echo gated
backlog_path: .claude/improvement-backlog.md
id_prefix: IB
forbid_trailers: [Co-Authored-By, Claude-Session, Generated with]
worktree_env_files: [.env.local]
---
'''
BACKLOG = '# Backlog\n\n| IB-1 | One | low | ready | A | s |\n| IB-2 | Two | low | ready | B | s |\n'
FAKE_GH = '''#!/bin/sh
echo "$@" >> "$FAKE_GH_LOG"
case "$1 $2" in
  "pr view") if [ -f "$FAKE_GH_PR" ]; then cat "$FAKE_GH_PR"; else echo '{"headRefName":"feat","commits":[]}'; fi ;;
esac
'''


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def git(cwd, *args, check=True):
    p = subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@e', '-c', 'init.defaultBranch=main', *args],
                       cwd=cwd, capture_output=True, text=True)
    if check and p.returncode:
        raise AssertionError(f'git {args}: {p.stderr}')
    return p.stdout


class Fx:
    """A clone of a bare origin with a foreman config, plus the env every hook call runs with."""

    def __init__(self, config=CONFIG):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='foreman-hooks-'))
        self.origin = os.path.join(self.tmp, 'origin.git')
        self.work = os.path.join(self.tmp, 'work')
        git(self.tmp, 'init', '-q', '--bare', '-b', 'main', self.origin)
        git(self.tmp, 'clone', '-q', self.origin, self.work)
        git(self.work, 'symbolic-ref', 'HEAD', 'refs/heads/main')
        if config is not None:
            write(os.path.join(self.work, '.claude/foreman.config.md'), config)
            write(os.path.join(self.work, '.claude/improvement-backlog.md'), BACKLOG)
        write(os.path.join(self.work, 'app.py'), 'print(1)\n')
        git(self.work, 'add', '-A')
        git(self.work, 'commit', '-q', '-m', 'init')
        git(self.work, 'push', '-q', '-u', 'origin', 'main')
        git(self.work, 'remote', 'set-head', 'origin', 'main')
        bin_dir = os.path.join(self.tmp, 'bin')
        write(os.path.join(bin_dir, 'gh'), FAKE_GH)
        os.chmod(os.path.join(bin_dir, 'gh'), 0o755)
        self.home = os.path.join(self.tmp, 'home')
        self.env = dict(os.environ, HOME=os.path.join(self.tmp, 'user'), FOREMAN_HOME=self.home,
                        FOREMAN_LOCK_ROOT=os.path.join(self.tmp, 'locks'), FOREMAN_LOCK_POLL='1',
                        PATH=bin_dir + os.pathsep + os.environ['PATH'],
                        FAKE_GH_LOG=os.path.join(self.tmp, 'gh.log'), FAKE_GH_PR=os.path.join(self.tmp, 'pr.json'),
                        GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@e', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@e')
        self.env.pop('FOREMAN_CONFIG', None)
        os.makedirs(self.env['HOME'])

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @property
    def state(self):
        return os.path.join(self.home, state.repo_key(self.work))

    def hook(self, event, payload):
        p = subprocess.run([sys.executable, HOOKS, event], input=json.dumps(payload), capture_output=True,
                           text=True, env=self.env, cwd=payload.get('cwd', self.work))
        assert p.returncode == 0, p.stderr
        return (json.loads(p.stdout) if p.stdout.strip() else None), p.stderr

    def cli(self, *args, cwd=None):
        return subprocess.run([sys.executable, HOOKS, *args], capture_output=True, text=True, env=self.env,
                              cwd=cwd or self.work).returncode

    def payload(self, tool, tool_input, sid='S1', agent=None, cwd=None, event='PreToolUse'):
        d = {'hook_event_name': event, 'session_id': sid, 'cwd': cwd or self.work, 'tool_name': tool,
             'tool_input': tool_input}
        if agent:
            d.update(agent_id=agent, agent_type='general-purpose')
        return d

    def bash(self, command, **kw):
        """The deny reason pretool gives for this Bash command, or None when it allows it."""
        out, _ = self.hook('pretool', self.payload('Bash', {'command': command}, **kw))
        return out['hookSpecificOutput']['permissionDecisionReason'] if out else None

    def edit(self, path, tool='Edit', **kw):
        out, _ = self.hook('pretool', self.payload(tool, {'file_path': path}, **kw))
        return out['hookSpecificOutput']['permissionDecisionReason'] if out else None

    def start_run(self, sid='S1', prompt='/foreman'):
        return self.hook('run-start', {'hook_event_name': 'UserPromptSubmit', 'session_id': sid, 'cwd': self.work,
                                       'prompt': prompt})

    def marker(self, sid='S1'):
        return state.read_json(os.path.join(self.state, 'runs', sid + '.json'))

    def commit(self, rel, text, msg='change', cwd=None):
        write(os.path.join(cwd or self.work, rel), text)
        git(cwd or self.work, 'add', '-A')
        git(cwd or self.work, 'commit', '-q', '-m', msg)

    def gate(self, *cmd, cwd=None, name=None, owner='w1'):
        """Run gate-lock.sh; with no cmd, the configured gate as one argument (the minting form)."""
        argv = [GATE_LOCK] + (['--name', name] if name else []) + [owner, '--'] + (list(cmd) or ['echo gated'])
        return subprocess.run(argv, capture_output=True, text=True, env=self.env, cwd=cwd or self.work)


class Case(unittest.TestCase):
    config = CONFIG

    def setUp(self):
        self.fx = Fx(self.config)

    def tearDown(self):
        self.fx.close()


IMPORTED = re.compile(r'\|\s*(shellscan|state|cfg|backlog)\s*$', re.M)


class CommonPathTest(Case):
    def test_malformed_stdin_exits_zero_silently(self):
        p = subprocess.run([sys.executable, HOOKS, 'pretool'], input='not json', capture_output=True, text=True,
                           env=self.fx.env)
        self.assertEqual((p.returncode, p.stdout), (0, ''))

    def test_own_error_fails_open(self):
        os.remove(os.path.join(self.fx.work, '.claude/foreman.config.md'))
        os.makedirs(os.path.join(self.fx.work, '.claude/foreman.config.md'))
        out, err = self.fx.hook('pretool', self.fx.payload('Bash', {'command': f'python3 {HOOKS} release w1a abc1234'}))
        self.assertIsNone(out)
        self.assertIn('foreman hook error: IsADirectoryError', err)

    def imports(self, event, tool_input):
        """The repo modules a hook call imports, read from `python3 -X importtime`."""
        p = subprocess.run([sys.executable, '-X', 'importtime', HOOKS, event], capture_output=True, text=True,
                           env=self.fx.env, cwd=self.fx.work, input=json.dumps(
                               self.fx.payload('Bash', tool_input, event='PostToolUse' if event == 'posttool' else 'PreToolUse')))
        self.assertEqual((p.returncode, p.stdout), (0, ''))
        return {m.group(1) for m in IMPORTED.finditer(p.stderr)}

    def test_fast_path_imports_nothing_of_ours(self):
        for event, command in (('pretool', 'ls -la && npm test'), ('pretool', 'echo digit'),
                               ('posttool', 'git push'), ('posttool', 'echo hooks.py release')):
            self.assertEqual(self.imports(event, {'command': command}), set(), (event, command))

    def test_fast_path_control_is_not_vacuous(self):
        # the same probe sees the modules when a command does name git or hooks.py, so the empty set above means something
        for event, command in (('pretool', 'git status'), ('pretool', 'echo hooks.py'),
                               ('posttool', 'python3 hooks.py run-end')):
            self.assertEqual(self.imports(event, {'command': command}), {'shellscan', 'state', 'cfg', 'backlog'},
                             (event, command))

    def test_unusual_tool_input_fails_open(self):
        for tool_input in ('git push', ['git'], {'command': ['git', 'push']}, {'command': None}):
            out, _ = self.fx.hook('pretool', self.fx.payload('Bash', tool_input))
            self.assertIsNone(out, tool_input)


class RunMarkerTest(Case):
    def test_prompts_that_start_a_run(self):
        for i, prompt in enumerate(('/foreman', '/foreman:foreman parallel 2', '/loop 2h /foreman',
                                    '/loop /foreman --auto-merge', '  /foreman IB-12')):
            self.fx.start_run(sid=f'S{i}', prompt=prompt)
            self.assertIsNotNone(self.fx.marker(f'S{i}'), prompt)
        self.assertEqual(self.fx.marker('S1')['args'], 'parallel 2')

    def test_prompts_that_do_not(self):
        for i, prompt in enumerate(('please run /foreman later', '/foreman ui', '/foreman init', '/foreman ui stop',
                                    '/foremanx', '/loop 2h /foreman ui')):
            self.fx.start_run(sid=f'N{i}', prompt=prompt)
            self.assertIsNone(self.fx.marker(f'N{i}'), prompt)

    def test_user_prompt_expansion(self):
        # probe P1: the event carries command_name ("foreman:foreman", also when "/foreman" was typed) and command_args
        def expand(sid, name, args):
            self.fx.hook('run-start', {'hook_event_name': 'UserPromptExpansion', 'session_id': sid, 'cwd': self.fx.work,
                                       'prompt': f'/{name} {args}'.strip(), 'command_name': name, 'command_args': args})
        expand('E1', 'foreman:foreman', 'parallel 2')
        expand('E2', 'foreman:foreman', 'ui')
        expand('E3', 'other', '')
        expand('E4', 'foreman', '')
        expand('E5', 'foreman:foreman', 'init')
        self.assertEqual(self.fx.marker('E1')['args'], 'parallel 2')
        self.assertIsNone(self.fx.marker('E2'))
        self.assertIsNone(self.fx.marker('E3'))
        self.assertEqual(self.fx.marker('E4')['args'], '')
        self.assertIsNone(self.fx.marker('E5'))

    def test_submit_and_expansion_of_one_prompt_agree(self):
        self.fx.start_run(prompt='/foreman parallel 2')
        first = self.fx.marker()
        self.fx.hook('run-start', {'hook_event_name': 'UserPromptExpansion', 'session_id': 'S1', 'cwd': self.fx.work,
                                   'command_name': 'foreman:foreman', 'command_args': 'parallel 2'})
        again = self.fx.marker()
        self.assertEqual((again['started'], again['args']), (first['started'], 'parallel 2'))

    def test_repeats_update_args_and_touched(self):
        self.fx.start_run(prompt='/foreman')
        first = self.fx.marker()
        time.sleep(0.01)
        self.fx.start_run(prompt='/foreman parallel 3')
        again = self.fx.marker()
        self.assertEqual(again['started'], first['started'])
        self.assertGreater(again['touched'], first['touched'])
        self.assertEqual(again['args'], 'parallel 3')

    def test_no_config_no_marker_and_foreman_config_is_ignored(self):
        fx = Fx(config=None)
        try:
            write(os.path.join(fx.tmp, 'elsewhere.md'), CONFIG)
            fx.env['FOREMAN_CONFIG'] = os.path.join(fx.tmp, 'elsewhere.md')
            fx.start_run()
            self.assertIsNone(fx.marker())
        finally:
            fx.close()
        self.fx.env['FOREMAN_CONFIG'] = os.path.join(self.fx.tmp, 'missing.md')
        self.fx.start_run()
        self.assertIsNotNone(self.fx.marker())

    def test_session_end_reasons(self):
        for reason, kept in (('clear', False), ('logout', False), ('resume', True), ('other', True),
                             ('prompt_input_exit', True)):
            self.fx.start_run()
            self.fx.hook('session-end', {'hook_event_name': 'SessionEnd', 'session_id': 'S1', 'cwd': self.fx.work,
                                     'reason': reason})
            self.assertEqual(self.fx.marker() is not None, kept, reason)

    def test_run_end_deletes_only_the_calling_sessions_marker(self):
        self.fx.start_run('S1')
        self.fx.start_run('S2')
        call = f'python3 {HOOKS} run-end'
        self.fx.hook('posttool', self.fx.payload('Bash', {'command': call}, sid='S1', agent='a1', event='PostToolUse'))
        self.assertIsNotNone(self.fx.marker('S1'))
        self.fx.hook('posttool', self.fx.payload('Bash', {'command': call + '; true'}, sid='S1', event='PostToolUse'))
        self.assertIsNotNone(self.fx.marker('S1'))
        self.fx.hook('posttool', self.fx.payload('Bash', {'command': call}, sid='S1', event='PostToolUse'))
        self.assertIsNone(self.fx.marker('S1'))
        self.assertIsNotNone(self.fx.marker('S2'))
        self.assertEqual(self.fx.cli('run-end'), 0)

    def test_48_hour_expiry(self):
        self.fx.start_run('S2')
        old = time.time() - 49 * 3600
        state.write_json(os.path.join(self.fx.state, 'runs', 'S2.json'), {'started': old, 'touched': old, 'args': ''})
        self.assertEqual(self.fx.cli('release', 'w1a', 'abc1234'), 3)
        self.fx.start_run('S1')
        self.assertFalse(os.path.exists(os.path.join(self.fx.state, 'runs', 'S2.json')))


class ControlTest(Case):
    def release(self, batch='w1a', sha='abc1234', **kw):
        return self.fx.bash(f'python3 {HOOKS} release {batch} {sha}', **kw)

    def batches(self):
        return os.path.join(self.fx.state, 'batches')

    def test_release_is_recorded_by_pretool_from_the_main_session(self):
        self.fx.start_run()
        self.assertEqual(self.fx.cli('release', 'w1a', 'abc1234'), 1)
        self.assertIsNone(self.release())
        rec = state.read_json(os.path.join(self.fx.state, 'batches', 'S1', 'w1a.released'))
        self.assertEqual(rec['sha'], 'abc1234')
        self.assertEqual(self.fx.cli('release', 'w1a', 'abc1234'), 0)

    def test_a_control_call_touches_the_marker(self):
        self.fx.start_run()
        before = self.fx.marker()
        time.sleep(0.01)
        self.release()
        self.assertGreater(self.fx.marker()['touched'], before['touched'])

    def test_release_exits_3_without_a_fresh_marker(self):
        self.assertEqual(self.fx.cli('release', 'w1a', 'abc1234'), 3)
        self.fx.start_run()
        later = time.strftime('%Y%m%d-%H%M', time.localtime(time.time() + 3600))
        self.assertEqual(self.fx.cli('release', f'{later}-w1a', 'abc1234'), 3)
        earlier = time.strftime('%Y%m%d-%H%M', time.localtime(time.time() - 3600))
        self.assertEqual(self.fx.cli('release', f'{earlier}-w1a', 'abc1234'), 1)

    def test_a_subagent_cannot_release_stop_or_end(self):
        self.fx.start_run()
        for verb in ('release w1a abc1234', 'stopping w1a', 'forget w1a', 'run-end'):
            why = self.fx.bash(f'python3 {HOOKS} {verb}', agent='a1')
            self.assertIn('only the orchestrator', why)
        self.assertIn('only the orchestrator', self.fx.bash(f"bash -c 'python3 {HOOKS} release w1a abc1234'", agent='a1'))
        self.assertFalse(os.path.exists(self.batches()))

    def test_a_subagent_is_denied_for_every_spelling_the_acceptor_takes(self):
        # control_call compares realpaths, so a trailing `/` or `/.` and a symlink all name this hooks.py
        link = os.path.join(self.fx.tmp, 'git', 'h')   # 'git' in the text keeps the call past the fast path
        os.makedirs(os.path.dirname(link))
        os.symlink(HOOKS, link)
        hooks._load()
        self.fx.start_run()
        for form in (HOOKS + '/', HOOKS + '/.', link, os.path.join(SCRIPTS, '..', 'scripts', 'hooks.py')):
            for verb in ('release w1a abc1234', 'stopping w1b', 'forget w1c'):
                command = f'python3 {form} {verb}'
                self.assertIsNotNone(hooks.control_call(command), command)   # the main session's call is taken
                self.assertIn('only the orchestrator', self.fx.bash(command, agent='a1') or '', command)
                self.assertIn('only the orchestrator', self.fx.bash(f"bash -c '{command}'", agent='a1') or '', command)
                self.assertIn('only the orchestrator', self.fx.bash(f'cd /tmp && {command}; git status', agent='a1') or '', command)
        self.assertFalse(os.path.exists(self.batches()))

    def test_a_word_the_scan_blanked_does_not_hide_a_later_call(self):
        # an unquoted $(...) reaches the segment as a word holding NUL bytes; realpath raises on those
        self.fx.start_run()
        command = f'git status; python3 $(echo x) release w1a abc1234; python3 {HOOKS} release w1a abc1234'
        out, err = self.fx.hook('pretool', self.fx.payload('Bash', {'command': command}, agent='a1'))
        self.assertIn('only the orchestrator', out['hookSpecificOutput']['permissionDecisionReason'])
        self.assertEqual(err, '')

    def test_a_subagent_is_denied_wherever_the_verb_follows_the_path_as_a_command(self):
        self.fx.start_run()
        for command in (f'cd /tmp && python3 {HOOKS} forget w1a', f'PYTHONPATH=x python3 {HOOKS} release w1a abc1234',
                        f'(python3 {HOOKS} stopping w1a)', f'eval "python3 {HOOKS} stopping w1a"',
                        f'eval python3 {HOOKS} stopping w1a', f"sh -c 'cd /tmp && python3 {HOOKS} run-end'",
                        f"bash -lc 'echo hi; python3 {HOOKS} run-end'", f'/usr/bin/python3 {HOOKS} run-end',
                        f'git status; python3 {HOOKS} \\\nrun-end', f'python3 {HOOKS} release w1a abc1234 "',
                        f'python3 {HOOKS} release w1a abc1234\\'):   # the last two the scan can't read
            self.assertIn('only the orchestrator', self.fx.bash(command, agent='a1') or '', command)
        self.assertFalse(os.path.exists(self.batches()))

    def test_a_mention_of_a_control_verb_is_not_a_control_call(self):
        self.fx.start_run()
        for command in ("git commit -m 'explain hooks.py release'", 'echo "then run hooks.py forget w1a"',
                        f"git commit -m 'python3 {HOOKS} run-end'", f"bash -c \"git commit -m 'hooks.py release w1a'\"",
                        f"cat <<'EOF' > notes.md\npython3 {HOOKS} release w1a abc1234\nEOF",
                        f"git commit -m 'x' # python3 {HOOKS} run-end", f'python3 {HOOKS}', f'echo {HOOKS} releases'):
            self.assertIsNone(self.fx.bash(command, agent='a1'), command)
            self.assertIsNone(self.fx.bash(command), command)
        self.assertFalse(os.path.exists(self.batches()))

    def test_a_subagent_outside_a_foreman_repo_is_not_denied(self):
        fx = Fx(config=None)
        try:
            self.assertIsNone(fx.bash(f'python3 {HOOKS} release w1a abc1234', agent='a1'))
        finally:
            fx.close()

    def test_stopping_and_forget(self):
        self.fx.start_run()
        self.fx.bash(f'python3 {HOOKS} stopping w1a')
        self.release()
        self.assertEqual(self.fx.cli('stopping', 'w1a'), 0)
        agents = os.path.join(self.fx.state, 'batches', 'S1', 'agents')
        state.write_json(os.path.join(agents, 'a1'), {'batch': 'w1a', 'spec': True, 'time': 1})
        state.write_json(os.path.join(agents, 'a2'), {'batch': 'w1b', 'spec': True, 'time': 1})
        self.assertIsNone(self.fx.bash(f'python3 {HOOKS} forget w1a'))
        self.assertEqual(sorted(os.listdir(os.path.join(self.fx.state, 'batches', 'S1'))), ['agents'])
        self.assertEqual(os.listdir(agents), ['a2'])
        self.assertEqual(self.fx.cli('forget', 'w1a'), 0)
        self.assertEqual(self.fx.cli('stopping', 'w1a'), 1)

    def test_only_the_exact_call_is_recorded(self):
        self.fx.start_run()
        self.fx.bash(f'python3 {HOOKS} release w1a abc1234 && echo done')
        self.fx.bash(f'python3 {HOOKS} release w1a not-a-sha')
        self.fx.bash(f'python3 {HOOKS} release "w1a;x" abc1234')
        self.assertFalse(os.path.exists(os.path.join(self.fx.state, 'batches')))
        self.assertEqual(self.fx.cli('release', 'w1a', 'not-a-sha'), 2)

    def test_a_worktree_shares_the_main_checkouts_records(self):
        wt = os.path.join(self.fx.tmp, 'wt')
        git(self.fx.work, 'worktree', 'add', '-q', wt, '-b', 'w1')
        self.fx.start_run()
        self.assertEqual(self.fx.cli('release', 'w1a', 'abc1234', cwd=wt), 1)
        self.assertIsNone(self.fx.bash(f'python3 {HOOKS} release w1a abc1234', cwd=wt))
        self.assertEqual(self.fx.cli('release', 'w1a', 'abc1234', cwd=wt), 0)
        self.assertEqual(self.fx.cli('release', 'w1a', 'abc1234'), 0)

    def test_default_home_marker_counts(self):
        default = os.path.join(self.fx.env['HOME'], '.claude', 'foreman', state.repo_key(self.fx.work))
        state.write_json(os.path.join(default, 'runs', 'S1.json'), {'started': time.time(), 'touched': time.time()})
        self.assertIsNone(self.release())
        self.assertEqual(self.fx.cli('release', 'w1a', 'abc1234'), 0)


class InProcess(Case):
    """Calls scripts/hooks.py's functions directly, with the fixture's FOREMAN_HOME and HOME and a throwaway
    default home, so nothing reaches the user's real ~/.claude/foreman."""

    def setUp(self):
        super().setUp()
        self.default_home = os.path.join(self.fx.env['HOME'], '.claude', 'foreman')
        for p in (mock.patch.dict(os.environ, {'FOREMAN_HOME': self.fx.home, 'HOME': self.fx.env['HOME']}),
                  mock.patch.object(state, 'DEFAULT_HOME', self.default_home)):
            p.start()
            self.addCleanup(p.stop)
        hooks._load()
        self.repo = hooks.repo_at(self.fx.work)

    def hook(self, sid='S1', **inp):
        return hooks.Hook(dict({'session_id': sid, 'cwd': self.fx.work}, **inp))

    def put_marker(self, sid, age=0, home=None):
        path = os.path.join(home or self.fx.state, 'runs', sid + '.json')
        state.write_json(path, {'started': time.time() - age, 'touched': time.time() - age, 'args': ''})
        return path


class RepoTest(InProcess):
    def test_config_accessors(self):
        r = self.repo
        self.assertEqual((r.get('id_prefix'), r.get('nope'), r.get('nope', 'x')), ('IB', None, 'x'))
        self.assertEqual(r.get('worker_agent_types'), ['general-purpose', 'workflow-subagent'])
        self.assertEqual((r.list('forbid_trailers'), r.list('gate'), r.list('nope')),
                         (['Co-Authored-By', 'Claude-Session', 'Generated with'], ['echo gated'], []))
        self.assertEqual((r.backlog, r.rules), ('.claude/improvement-backlog.md', ['.claude/rules/*.md']))
        self.assertEqual(r.rel(os.path.join(self.fx.work, 'app.py')), 'app.py')

    def test_repo_at_walks_up_to_an_existing_directory(self):
        self.assertEqual(hooks.repo_at(os.path.join(self.fx.work, 'new', 'dir', 'file.py')).top, self.fx.work)
        self.assertIsNone(hooks.repo_at(self.fx.tmp))

    def test_repo_at_a_worktree_reads_the_main_checkouts_config_and_state(self):
        wt = os.path.join(self.fx.tmp, 'wt')
        git(self.fx.work, 'worktree', 'add', '-q', wt, '-b', 'w1')
        os.remove(os.path.join(wt, '.claude/foreman.config.md'))
        r = hooks.repo_at(wt)
        self.assertEqual((r.top, r.main, r.cfg_path), (wt, self.fx.work, os.path.join(self.fx.work, '.claude/foreman.config.md')))
        self.assertEqual(r.dirs, [self.fx.state, os.path.join(self.default_home, state.repo_key(self.fx.work))])

    def test_a_repo_without_a_config_is_not_a_foreman_repo(self):
        fx = Fx(config=None)
        try:
            self.assertIsNone(hooks.repo_at(fx.work))
        finally:
            fx.close()

    def test_a_disabled_piece_is_inactive(self):
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'),
              CONFIG.replace('---\n', '---\nhooks_disabled: [piece_x]\n', 1))
        repo = hooks.Repo(self.fx.work)
        h = self.hook()
        self.assertEqual(repo.list('hooks_disabled'), ['piece_x'])
        self.assertFalse(h.active(repo, 'piece_x', run_only=False))
        self.assertTrue(h.active(repo, 'piece_y', run_only=False))
        self.assertFalse(h.active(None, 'piece_y', run_only=False))


class MarkerTest(InProcess):
    def test_only_a_live_marker_of_this_session_counts(self):
        fresh, stale = self.put_marker('S1'), self.put_marker('S2', age=49 * 3600)
        self.assertEqual(hooks.marker(self.repo, 'S1')[0], fresh)
        for sid in ('S2', 'S3', '', '../S1'):
            self.assertIsNone(hooks.marker(self.repo, sid), sid)
        self.assertTrue(os.path.exists(stale))

    def test_run_only_pieces_need_a_live_marker(self):
        self.put_marker('S1')
        self.put_marker('S2', age=49 * 3600)
        self.assertTrue(self.hook('S1').active(self.repo, 'p'))
        self.assertFalse(self.hook('S2').active(self.repo, 'p'))
        self.assertFalse(self.hook('S3').active(self.repo, 'p'))
        self.assertTrue(self.hook('S3').active(self.repo, 'p', run_only=False))

    def test_a_marker_in_the_default_home_counts_and_is_touched_in_place(self):
        path = self.put_marker('S1', age=60, home=os.path.join(self.default_home, state.repo_key(self.fx.work)))
        self.assertEqual(hooks.marker(self.repo, 'S1')[0], path)
        before = state.read_json(path)['touched']
        hooks.touch_marker(self.repo, 'S1')
        self.assertGreater(state.read_json(path)['touched'], before + 30)
        self.assertFalse(os.path.exists(os.path.join(self.fx.state, 'runs')))

    def test_touching_does_not_revive_an_expired_marker(self):
        path = self.put_marker('S2', age=49 * 3600)
        before = state.read_json(path)
        hooks.touch_marker(self.repo, 'S2')
        self.assertEqual(state.read_json(path), before)

    def test_posttool_run_end_ends_the_run_only_for_the_main_session(self):
        path = self.put_marker('S1')
        call = {'command': f'python3 {HOOKS} run-end'}
        hooks.posttool(self.hook(**self.fx.payload('Bash', call, agent='a1', event='PostToolUse')))
        self.assertTrue(os.path.exists(path))
        hooks.posttool(self.hook(**self.fx.payload('Bash', call, event='PostToolUse')))
        self.assertFalse(os.path.exists(path))


class SubagentWriteTest(InProcess):
    def test_a_subagent_call_is_never_recorded_even_when_the_deny_detector_misses_it(self):
        self.put_marker('S1')
        command = f'python3 {HOOKS} release w1a abc1234'
        with mock.patch.object(hooks, 'runs_control', return_value=False):
            sub = self.hook(**self.fx.payload('Bash', {'command': command}, agent='a1'))
            hooks.pretool_bash(sub, command)
        self.assertEqual(sub.denies, [])
        self.assertFalse(os.path.exists(os.path.join(self.fx.state, 'batches')))
        main = self.hook(**self.fx.payload('Bash', {'command': command}))   # control: the main session's call is recorded
        hooks.pretool_bash(main, command)
        self.assertEqual(main.denies, [])
        self.assertTrue(os.path.exists(os.path.join(self.fx.state, 'batches', 'S1', 'w1a.released')))


class ResolveAgentTest(InProcess):
    """resolve_agent fills the agent map from the worker's own transcript on first sight (probe P2)."""

    WHEN = '2026-10-02T12:30:45.123Z'

    def setUp(self):
        super().setUp()
        self.session = os.path.join(self.fx.tmp, 'session')   # the main transcript is <session>.jsonl

    def transcript(self, first, agent='a1', content=None):
        """Write the worker transcript: a non-user entry, then a user entry whose text is `first`."""
        user = {'type': 'user', 'timestamp': self.WHEN, 'message': {'role': 'user', 'content': content or first}}
        text = '\n'.join(json.dumps(e) for e in ({'type': 'queue-operation', 'timestamp': self.WHEN}, user)) + '\n'
        write(os.path.join(self.session, 'subagents', f'agent-{agent}.jsonl'), text)

    def resolve(self, agent='a1', **inp):
        d = {'transcript_path': self.session + '.jsonl'}
        if agent:
            d['agent_id'] = agent
        return hooks.resolve_agent(self.repo, self.hook(**dict(d, **inp)))

    def stored(self, agent='a1'):
        return state.read_json(os.path.join(self.fx.state, 'batches', 'S1', 'agents', agent))

    def test_a_tagged_worker_gets_an_entry_from_its_transcript(self):
        self.transcript('foreman-worker: w1a\nDo rows IB-1.')
        start = calendar.timegm((2026, 10, 2, 12, 30, 45))
        want = {'batch': 'w1a', 'spec': False, 'briefed': False, 'start': start, 'time': start}
        self.assertEqual(self.resolve(), want)
        self.assertEqual(self.stored(), want)

    def test_the_text_may_be_a_list_of_content_blocks(self):
        self.transcript('', content=[{'type': 'image'}, {'type': 'text', 'text': 'foreman-worker: w1b spec-phase\nSpec.'}])
        entry = self.resolve()
        self.assertEqual((entry['batch'], entry['spec']), ('w1b', True))

    def test_a_near_miss_spec_phase_tag_still_counts(self):
        self.transcript('foreman-worker: w1c (spec-phase)')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            entry = self.resolve()
        self.assertEqual((entry['batch'], entry['spec']), ('w1c', True))
        self.assertIn('odd worker tag read as spec-phase', err.getvalue())

    def test_an_untagged_agent_gets_a_negative_entry_once(self):
        self.transcript('Do rows IB-1.\nforeman-worker: w1a')
        self.assertEqual(self.resolve(), {'batch': None})
        self.assertEqual(self.stored(), {'batch': None})
        os.remove(os.path.join(self.session, 'subagents', 'agent-a1.jsonl'))
        self.assertEqual(self.resolve(), {'batch': None})

    def test_the_map_wins_over_the_transcript(self):
        state.write_json(os.path.join(self.fx.state, 'batches', 'S1', 'agents', 'a1'), {'batch': 'old', 'spec': False})
        self.transcript('foreman-worker: w1a')
        self.assertEqual(self.resolve(), {'batch': 'old', 'spec': False})

    def test_an_unreadable_transcript_writes_nothing(self):
        self.assertIsNone(self.resolve())                                       # not written yet (at SubagentStart)
        self.assertIsNone(self.resolve(transcript_path=''))
        self.assertIsNone(self.resolve(transcript_path=self.session))           # not a .jsonl path
        write(os.path.join(self.session, 'subagents', 'agent-a1.jsonl'), '{"type":"queue-operation"}\nnot json\n')
        self.assertIsNone(self.resolve())                                       # no user entry yet
        self.assertFalse(os.path.exists(os.path.join(self.fx.state, 'batches')))

    def test_the_main_session_and_unsafe_ids_resolve_to_nothing(self):
        self.transcript('foreman-worker: w1a')
        self.assertIsNone(self.resolve(agent=None))
        self.assertIsNone(self.resolve(agent='../a1'))
        self.assertIsNone(self.resolve(sid='../S1'))
        self.assertFalse(os.path.exists(os.path.join(self.fx.state, 'batches')))

    def raw(self, lines, agent='a1'):
        path = os.path.join(self.session, 'subagents', f'agent-{agent}.jsonl')
        write(path, '\n'.join(lines) + '\n')
        return path

    TAGGED = json.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'foreman-worker: w1a'}})

    def test_only_the_first_user_entry_counts(self):
        # a first user entry with no text block must not hand the tag to a later one (a tool result, say)
        path = self.raw([json.dumps({'type': 'user', 'timestamp': self.WHEN, 'message': {'content': [{'type': 'image'}]}}),
                         self.TAGGED])
        self.assertEqual(hooks.transcript_head(path)[0], None)
        self.assertIsNone(self.resolve())
        self.assertFalse(os.path.exists(os.path.join(self.fx.state, 'batches')))

    def test_a_malformed_transcript_resolves_to_nothing_and_never_raises(self):
        user = lambda **kw: json.dumps(dict({'type': 'user'}, **kw))   # noqa: E731
        for lines in (['{"type":"queue-operation"', self.TAGGED],       # a torn line before the first user entry
                      ['5', self.TAGGED], ['[]', self.TAGGED], ['null', self.TAGGED],
                      [user(message='foreman-worker: w1a')],               # message not an object
                      [user(message=None), self.TAGGED], [user(message=[]), self.TAGGED],
                      [user(message={'content': 7}), self.TAGGED], [user(message={'content': [None, 5]}), self.TAGGED],
                      [user(message={'content': [{'type': 'text', 'text': 5}]}), self.TAGGED]):
            path = self.raw(lines)
            self.assertEqual(hooks.transcript_head(path)[0], None, lines)
            self.assertIsNone(self.resolve(), lines)
        self.assertFalse(os.path.exists(os.path.join(self.fx.state, 'batches')))

    def test_blank_lines_are_not_malformed(self):
        self.raw(['', json.dumps({'type': 'queue-operation', 'timestamp': self.WHEN}), '  ', self.TAGGED])
        self.assertEqual(self.resolve()['batch'], 'w1a')


class PretoolTest(InProcess):
    """The dispatcher with a fake edit piece registered, as Tasks 5-10 register theirs."""

    def run_pretool(self):
        def piece(h, repo, path):
            h.touch.add(repo)
            h.deny('piece says no')
        payload = self.fx.payload('Edit', {'file_path': os.path.join(self.fx.work, 'app.py')})
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(hooks.EDIT_PIECES, {99: piece}), mock.patch.object(sys, 'stdin', io.StringIO(json.dumps(payload))), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            hooks.main(['hooks.py', 'pretool'])
        return out.getvalue(), err.getvalue()

    def test_a_deny_touches_the_marker(self):
        path = self.put_marker('S1', age=60)
        out, err = self.run_pretool()
        self.assertEqual(json.loads(out)['hookSpecificOutput']['permissionDecisionReason'], 'foreman: piece says no')
        self.assertEqual(err, '')
        self.assertGreater(state.read_json(path)['touched'], time.time() - 30)

    def test_a_failed_touch_does_not_drop_the_denies(self):
        self.put_marker('S1')
        with mock.patch.object(state, 'write_json', side_effect=OSError('read-only')):
            out, err = self.run_pretool()
        self.assertEqual(json.loads(out)['hookSpecificOutput']['permissionDecisionReason'], 'foreman: piece says no')
        self.assertIn('foreman hook error: OSError: read-only', err)


def head_tree(cwd):
    return git(cwd, 'rev-parse', 'HEAD^{tree}').strip()


class GlobTest(unittest.TestCase):
    def test_gitignore_style_matching(self):
        for path, pattern, want in (
                ('docs/a.md', 'docs/**', True), ('docs/x/y.png', 'docs/**', True), ('docs', 'docs/**', False),
                ('a/docs/x.md', 'docs/**', False),                         # a pattern with a slash is anchored
                ('README.md', '**/*.md', True), ('a/b/c.md', '**/*.md', True),     # `**/` is zero or more directories
                ('a.mdx', '**/*.md', False), ('a/b.md/x', '**/*.md', True),        # a matched directory covers its contents
                ('docs/a.md', 'docs/*.md', True), ('docs/s/a.md', 'docs/*.md', False),   # `*` stays inside one directory
                ('.env.local', '.env.local', True), ('apps/web/.env.local', '.env.local', True),   # no slash: any depth
                ('.env.localx', '.env.local', False), ('a.env.local', '.env.local', False),
                ('.claude/rules/r.md', '.claude/rules/*.md', True), ('.claude/rules/s/r.md', '.claude/rules/*.md', False),
                ('vendor/docs/x', 'docs/', True), ('/docs/a.md', '/docs/a.md', False), ('docs/a.md', '/docs/a.md', True),
                ('a.py', '', False), ('a/b', '', False)):
            self.assertEqual(hooks.glob_match(path, pattern), want, (path, pattern))


class ReceiptMintTest(Case):
    def receipts(self):
        d = os.path.join(self.fx.state, 'gate')
        return sorted(n for n in os.listdir(d) if n != 'last') if os.path.isdir(d) else []

    def test_only_the_configured_gate_as_one_argument_mints(self):
        self.assertEqual(self.fx.gate('echo', 'gated').returncode, 0)
        self.assertEqual(self.fx.gate(name='db').returncode, 0)
        self.assertEqual(self.fx.gate('echo other').returncode, 0)
        self.assertEqual(self.receipts(), [])
        self.assertEqual(self.fx.gate().returncode, 0)
        self.assertEqual(self.receipts(), [head_tree(self.fx.work)])
        rec = state.read_json(os.path.join(self.fx.state, 'gate', head_tree(self.fx.work)))
        self.assertEqual((rec['command'], rec['owner'], rec['toplevel']), ('echo gated', 'w1', self.fx.work))

    def test_a_command_with_more_arguments_never_mints_even_when_its_first_is_the_gate(self):
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'), CONFIG.replace('echo gated', 'true'))
        git(self.fx.work, 'add', '-A')
        git(self.fx.work, 'commit', '-q', '-m', 'gate')
        self.assertEqual(self.fx.gate('true', 'extra').returncode, 0)
        self.assertEqual(self.receipts(), [])
        self.assertEqual(self.fx.gate('true').returncode, 0)
        self.assertEqual(self.receipts(), [head_tree(self.fx.work)])

    def test_a_failing_gate_mints_nothing_and_keeps_its_exit_code(self):
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'), CONFIG.replace('echo gated', 'exit 7'))
        self.assertEqual(self.fx.gate('exit 7').returncode, 7)
        self.assertEqual(self.receipts(), [])

    def test_receipt_tree_keeps_a_force_added_ignored_file(self):
        write(os.path.join(self.fx.work, '.gitignore'), 'secret.txt\n')
        write(os.path.join(self.fx.work, 'secret.txt'), 'x\n')
        git(self.fx.work, 'add', '.gitignore')
        git(self.fx.work, 'add', '-f', 'secret.txt')
        git(self.fx.work, 'commit', '-q', '-m', 'forced')
        self.fx.gate()
        self.assertEqual(self.receipts(), [head_tree(self.fx.work)])

    def test_a_same_size_edit_made_in_the_tick_git_wrote_the_index_is_in_the_receipt(self):
        # git trusts a stat-clean entry unless the index file is no newer than it ("racily clean"), so the index
        # copy must keep its mtime: with a fresh one the edit is never re-hashed and the receipt holds the old blob.
        # Pinned in the past, with ctime ignored, so the stat is identical and the result doesn't hang on the clock.
        then = time.time_ns() - 10 * 10 ** 9
        path = os.path.join(self.fx.work, 'app.py')
        write(path, 'print(2)\n')
        os.utime(path, ns=(then, then))
        git(self.fx.work, 'add', '-A')
        git(self.fx.work, 'commit', '-q', '-m', 'two')
        git(self.fx.work, 'config', 'core.trustctime', 'false')
        write(path, 'print(3)\n')   # same size, rewritten in place
        os.utime(path, ns=(then, then))
        index = os.path.join(self.fx.work, '.git', 'index')
        os.utime(index, ns=(then, then))   # git wrote the index in the tick the entry was recorded
        self.fx.gate()
        tree = state.read_json(state.last_path(self.fx.state, self.fx.work))['tree']
        self.assertEqual(git(self.fx.work, 'show', f'{tree}:app.py'), 'print(3)\n')

    def test_receipt_tree_with_a_crlf_blob_under_text_auto(self):
        # The blob goes in CRLF BEFORE text=auto exists. Only a copy of the real index keeps it: from an empty
        # index, `git add` under text=auto normalises the unchanged file to LF and the tree no longer matches.
        with open(os.path.join(self.fx.work, 'win.txt'), 'wb') as f:
            f.write(b'a\r\nb\r\n')
        git(self.fx.work, '-c', 'core.autocrlf=false', 'add', 'win.txt')
        git(self.fx.work, '-c', 'core.autocrlf=false', 'commit', '-q', '-m', 'crlf')
        write(os.path.join(self.fx.work, '.gitattributes'), '* text=auto\n')
        git(self.fx.work, 'add', '.gitattributes')
        git(self.fx.work, 'commit', '-q', '-m', 'attributes')
        blob = subprocess.run(['git', 'cat-file', 'blob', 'HEAD:win.txt'], cwd=self.fx.work, capture_output=True).stdout
        self.assertEqual(blob, b'a\r\nb\r\n')   # the premise: a CRLF blob under text=auto
        self.fx.gate()
        self.assertEqual(self.receipts(), [head_tree(self.fx.work)])

    def test_an_empty_gate_never_mints(self):
        # no `gate` key: cfg.py prints nothing, and '' equals ''
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'), CONFIG.replace('gate: echo gated\n', ''))
        self.assertEqual(self.fx.gate('').returncode, 0)
        self.assertEqual(self.receipts(), [])

    def default_dir(self):
        return os.path.join(self.fx.env['HOME'], '.claude', 'foreman', state.repo_key(self.fx.work))

    def test_a_receipt_lands_in_both_state_dirs_and_is_found_without_foreman_home(self):
        # probe P4: a hook that doesn't see the Bash tool's FOREMAN_HOME looks only at the default dir
        self.fx.start_run()
        self.fx.commit('app.py', 'print(2)\n')
        self.fx.gate()
        tree = head_tree(self.fx.work)
        for d in (self.fx.state, self.default_dir()):
            self.assertEqual(state.read_json(os.path.join(d, 'gate', tree))['command'], 'echo gated', d)
            self.assertEqual(state.read_json(state.last_path(d, self.fx.work))['tree'], tree, d)
        state.write_json(os.path.join(self.default_dir(), 'runs', 'S1.json'), {'started': time.time(), 'touched': time.time()})
        del self.fx.env['FOREMAN_HOME']
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))
        self.fx.commit('app.py', 'print(3)\n')
        self.assertIn('no passing gate', self.fx.bash('git push -u origin HEAD'))   # control: the hook is judging

    def test_receipts_older_than_14_days_are_pruned_at_the_next_mint(self):
        old, fresh = time.time() - 15 * 86400, time.time() - 13 * 86400
        for sub, name, t in (('gate', 'a' * 40, old), ('gate', 'b' * 40, fresh),
                             ('gate/last', 'c' * 40, old), ('gate/last', 'd' * 40, fresh)):
            path = os.path.join(self.fx.state, sub, name)
            state.write_json(path, {})
            os.utime(path, (t, t))
        self.fx.gate()
        self.assertEqual(self.receipts(), sorted(['b' * 40, head_tree(self.fx.work)]))
        self.assertEqual(sorted(os.listdir(os.path.join(self.fx.state, 'gate', 'last'))),
                         sorted(['d' * 40, os.path.basename(state.last_path(self.fx.state, self.fx.work))]))

    def test_gate_dirs_are_0700_and_receipts_0600(self):
        self.fx.gate()
        for sd in (self.fx.state, self.default_dir()):
            for d in (sd, os.path.join(sd, 'gate'), os.path.join(sd, 'gate', 'last')):
                self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700, d)
                for name in os.listdir(d):
                    if os.path.isfile(os.path.join(d, name)):
                        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, name)).st_mode), 0o600, name)

    def test_a_killed_gate_removes_its_untracked_snapshot(self):
        tmpdir = os.path.join(self.fx.tmp, 'tmpdir')
        os.makedirs(tmpdir)
        for sig in (signal.SIGINT, signal.SIGTERM):
            p = subprocess.Popen([GATE_LOCK, 'w1', '--', 'sleep 30'], env=dict(self.fx.env, TMPDIR=tmpdir), cwd=self.fx.work,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                deadline = time.time() + 15
                while not os.listdir(tmpdir) and time.time() < deadline:
                    time.sleep(0.05)
                self.assertTrue(os.listdir(tmpdir), 'the gate never took its snapshot')
                os.killpg(p.pid, sig)   # the whole group: bash runs its trap only once the foreground child is gone
                p.wait(timeout=15)
            finally:
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGKILL)
            self.assertEqual((os.listdir(tmpdir), p.returncode), ([], 128 + sig), sig)

    def test_a_gate_run_from_a_subdirectory_runs_at_the_top_level(self):
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'), CONFIG.replace('echo gated', 'pwd > "$GATE_OUT"'))
        self.fx.env['GATE_OUT'] = os.path.join(self.fx.tmp, 'where')
        os.makedirs(os.path.join(self.fx.work, 'sub'))
        self.assertEqual(self.fx.gate('pwd > "$GATE_OUT"', cwd=os.path.join(self.fx.work, 'sub')).returncode, 0)
        with open(self.fx.env['GATE_OUT']) as f:
            self.assertEqual(os.path.realpath(f.read().strip()), self.fx.work)
        self.assertEqual(len(self.receipts()), 1)


class GateBeforePushTest(Case):
    def setUp(self):
        super().setUp()
        self.fx.start_run()
        git(self.fx.work, 'switch', '-q', '-c', 'feat')

    def test_receipt_present_and_missing(self):
        self.fx.commit('app.py', 'print(2)\n')
        self.assertIn('no passing gate for this tree', self.fx.bash('git push -u origin HEAD'))
        self.fx.gate()
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))
        self.assertIsNone(self.fx.bash('gh pr create --fill'))
        self.assertIsNone(self.fx.bash('gt submit'))

    def test_an_older_receipt_still_matches_its_tree(self):
        self.fx.commit('app.py', 'print(2)\n')
        self.fx.gate()
        self.fx.commit('app.py', 'print(3)\n')
        self.fx.gate()
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))
        git(self.fx.work, 'reset', '-q', '--hard', 'HEAD~1')   # back on the first gated tree; the last pointer names the second
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))

    def test_exempt_only_diff_needs_no_receipt(self):
        self.fx.commit('docs/guide.md', 'x\n')
        self.fx.commit('.claude/improvement-backlog.md', BACKLOG + '| IB-3 | Three | low | ready | C | s |\n')
        self.fx.commit('.claude/rules/r.md', 'rule\n')
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))

    def test_worktree_env_files_are_exempt_only_against_a_receipt(self):
        self.fx.commit('.env.local', 'SECRET=1\n')
        self.assertIn('no passing gate', self.fx.bash('git push -u origin HEAD'))

    def test_a_non_ascii_exempt_path_is_exempt(self):
        # without -z, git prints "docs/caf\303\251.md" quoted, which no pattern matches
        self.fx.commit('docs/café.md', 'x\n')
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))

    def test_exempt_diff_since_the_last_receipt(self):
        self.fx.commit('app.py', 'print(2)\n')
        self.fx.gate()
        self.fx.commit('README.md', 'notes\n')
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))
        self.fx.commit('app.py', 'print(3)\n')
        why = self.fx.bash('git push -u origin HEAD')
        self.assertIn('the gate ran on a different tree', why)
        self.assertIn('app.py', why)

    def test_a_rename_of_code_into_docs_is_not_exempt(self):
        os.makedirs(os.path.join(self.fx.work, 'docs'))
        git(self.fx.work, 'mv', 'app.py', 'docs/app.py')
        git(self.fx.work, 'commit', '-q', '-m', 'move')
        self.assertIn('no passing gate', self.fx.bash('git push -u origin HEAD'))

    def test_every_push_form_is_judged(self):
        self.fx.commit('app.py', 'print(2)\n')
        wt = os.path.join(self.fx.tmp, 'wt')
        git(self.fx.work, 'worktree', 'add', '-q', wt, '-b', 'w1')
        for cmd in ('echo gate; git push', f'cd {wt} && git push', '(git push)', '/usr/bin/git push',
                    'git push 2>&1 | tail -1'):
            self.assertIsNotNone(self.fx.bash(cmd), cmd)

    def test_delete_pushes_are_allowed(self):
        self.fx.commit('app.py', 'print(2)\n')
        self.assertIsNone(self.fx.bash('git push origin :old'))
        self.assertIsNone(self.fx.bash('git push --delete origin old'))

    def test_a_delete_mixed_with_a_real_ref_is_still_judged(self):
        self.fx.commit('app.py', 'print(2)\n')
        for cmd in ('git push origin HEAD :old', 'git push origin :old HEAD', 'git push origin HEAD:refs/heads/x :old'):
            self.assertIn('no passing gate', self.fx.bash(cmd) or '', cmd)
        self.assertIsNone(self.fx.bash('git push origin :old :older'))   # every refspec deletes

    def test_a_bare_colon_and_a_tag_push_are_not_deletes(self):
        # `git push origin :` pushes the matching branches; --tags pushes every tag beside the refspecs
        self.fx.commit('app.py', 'print(2)\n')
        for cmd in ('git push origin :', 'git push origin :old :', 'git push origin --tags :old',
                    'git push --tags origin :old', 'git push --follow-tags origin :old'):
            self.assertIn('no passing gate', self.fx.bash(cmd) or '', cmd)
        self.assertIsNone(self.fx.bash('git push origin :old'))   # control

    def test_a_nested_worktree_committed_as_a_gitlink_is_told_to_untrack_and_ignore_it(self):
        write(os.path.join(self.fx.work, 'app.py'), 'print(2)\n')
        git(self.fx.work, 'worktree', 'add', '-q', os.path.join(self.fx.work, '.claude/worktrees/agent-x'), '-b', 'agent-x')
        self.fx.gate()   # leaves the unignored worktree out of the receipt
        git(self.fx.work, 'add', '-A')   # a worker's commit sweeps it in as a gitlink
        git(self.fx.work, 'commit', '-q', '-m', 'sweeps the worktree in')
        self.assertIn('160000', git(self.fx.work, 'ls-tree', '-r', 'HEAD'))
        why = self.fx.bash('git push -u origin HEAD')
        self.assertIn('git rm --cached .claude/worktrees/agent-x', why)
        self.assertIn('.git/info/exclude', why)
        self.assertNotIn('commit these', why)   # the generic hint doesn't fix this case
        git(self.fx.work, 'rm', '--cached', '-q', '.claude/worktrees/agent-x')
        git(self.fx.work, 'commit', '-q', '-m', 'untrack it')
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))

    def test_a_regular_file_under_the_worktrees_dir_gets_the_generic_hint(self):
        self.fx.commit('app.py', 'print(2)\n')
        self.fx.gate()
        self.fx.commit('.claude/worktrees/notes.txt', 'x\n')   # created after the gate; not a gitlink
        why = self.fx.bash('git push -u origin HEAD')
        self.assertIn('commit these', why)
        self.assertIn('.claude/worktrees/notes.txt', why)
        self.assertNotIn('git rm --cached', why)

    def test_undeterminable_diff_is_denied(self):
        git(self.fx.work, 'remote', 'set-head', 'origin', '--delete')
        git(self.fx.work, 'update-ref', '-d', 'refs/remotes/origin/main')
        self.fx.commit('docs/guide.md', 'x\n')
        self.assertIn('no passing gate', self.fx.bash('git push -u origin HEAD'))

    def test_unknown_arguments_are_denied(self):
        self.assertIn("couldn't read this command", self.fx.bash('git push "oops'))
        self.assertIn("couldn't read this command", self.fx.bash('GIT_DIR=x git push'))

    def test_gate_and_push_in_one_call_is_denied(self):
        self.fx.commit('app.py', 'print(2)\n')
        self.fx.gate()
        self.assertIn("couldn't read this command", self.fx.bash(f"{GATE_LOCK} w1 -- 'echo gated' && git push"))

    def test_a_gate_that_writes_coverage_still_matches_head(self):
        gate = 'mkdir -p coverage && echo x > coverage/out.txt'
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'), CONFIG.replace('echo gated', gate))
        git(self.fx.work, 'add', '-A')
        git(self.fx.work, 'commit', '-q', '-m', 'gate')
        p = self.fx.gate(gate)
        self.assertIn('not part of the receipt: coverage/out.txt', p.stderr)
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))

    def test_an_artifact_whose_name_holds_glob_characters_excludes_only_itself(self):
        # git matches a pathspec literally before it tries it as a glob, so `cov[ab]/x.txt` alone would pass; the
        # glob also matches the real, untracked cova/x.txt, which must stay in the receipt
        gate = "mkdir -p 'cov[ab]' && echo x > 'cov[ab]/x.txt'"
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'), CONFIG.replace('echo gated', gate))
        git(self.fx.work, 'add', '-A')
        git(self.fx.work, 'commit', '-q', '-m', 'gate')
        write(os.path.join(self.fx.work, 'cova/x.txt'), 'real\n')
        self.assertIn('not part of the receipt: cov[ab]/x.txt', self.fx.gate(gate).stderr)
        git(self.fx.work, 'add', 'cova')
        git(self.fx.work, 'commit', '-q', '-m', 'the real file')
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))

    def test_push_from_a_worktree_path_with_spaces(self):
        wt = os.path.join(self.fx.tmp, 'my wt')
        git(self.fx.work, 'worktree', 'add', '-q', wt, '-b', 'w2')
        self.fx.commit('app.py', 'print(9)\n', cwd=wt)
        self.assertIn('no passing gate', self.fx.bash(f'cd "{wt}" && git push -u origin HEAD'))
        self.fx.gate(cwd=wt)
        self.assertIsNone(self.fx.bash(f'cd "{wt}" && git push -u origin HEAD'))

    def test_origin_head_unset_falls_back_to_main(self):
        git(self.fx.work, 'remote', 'set-head', 'origin', '--delete')
        self.fx.commit('docs/guide.md', 'x\n')
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))


class ReceiptExactnessTest(Case):
    """Revision 6: the receipt must match the pushed tree exactly, apart from exempt paths."""

    def setUp(self):
        super().setUp()
        self.fx.start_run()
        git(self.fx.work, 'switch', '-q', '-c', 'feat')

    def test_git_rm_after_the_gate_is_denied(self):
        write(os.path.join(self.fx.work, 'app.py'), 'print(2)\n')
        self.fx.commit('lib.py', 'x = 1\n')
        self.fx.gate()
        git(self.fx.work, 'rm', '-q', 'lib.py')
        git(self.fx.work, 'commit', '-q', '-m', 'rm')
        why = self.fx.bash('git push -u origin HEAD')
        self.assertIn('lib.py', why)

    def test_commit_am_leaving_out_a_new_file_is_denied(self):
        write(os.path.join(self.fx.work, 'app.py'), 'print(2)\n')
        write(os.path.join(self.fx.work, 'new.py'), 'y = 2\n')
        p = self.fx.gate()
        self.assertIn('part of the gated tree', p.stderr)
        self.assertIn('new.py', p.stderr)
        git(self.fx.work, 'commit', '-q', '-am', 'only tracked')
        why = self.fx.bash('git push -u origin HEAD')
        self.assertIn('the gate ran on a different tree', why)
        self.assertIn('new.py', why)

    def test_a_modified_worktree_env_file_passes(self):
        self.fx.commit('app.py', 'print(2)\n')
        write(os.path.join(self.fx.work, '.env.local'), 'SECRET=2\n')
        self.fx.gate()
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))

    def test_a_second_gate_run_does_not_sweep_the_first_runs_artifact(self):
        gate = 'mkdir -p coverage && touch coverage/a.txt'
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'), CONFIG.replace('echo gated', gate))
        self.fx.commit('app.py', 'print(2)\n')
        self.fx.gate(gate)
        p = self.fx.gate(gate)
        self.assertNotIn('part of the gated tree', p.stderr)
        # the first run's receipt has this key too, so read the pointer the second run wrote
        last = state.read_json(state.last_path(self.fx.state, self.fx.work))
        self.assertEqual((last['tree'], last['artifacts']), (head_tree(self.fx.work), ['coverage/a.txt']))

    def gated_push_passes(self, *cmd):
        """Gate the checkout, then expect the receipt to match HEAD (no noise about the nested paths) and the push through."""
        p = self.fx.gate(*cmd)
        self.assertEqual((p.returncode, 'part of the gated tree' in p.stderr, 'could not mint' in p.stderr), (0, False, False), p.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.fx.state, 'gate', head_tree(self.fx.work))))
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))

    def test_nested_worktrees_and_embedded_repos_are_left_out_of_the_receipt(self):
        # `git add -A` stages both as 160000 gitlinks, which HEAD^{tree} never holds
        self.fx.commit('app.py', 'print(2)\n')
        git(self.fx.work, 'worktree', 'add', '-q', os.path.join(self.fx.work, '.claude/worktrees/agent-x'), '-b', 'agent-x')
        emb = os.path.join(self.fx.work, 'vendor', 'emb')
        os.makedirs(emb)
        git(emb, 'init', '-q')
        self.fx.commit('e.txt', 'e\n', cwd=emb)
        self.gated_push_passes()

    def test_a_broken_worktree_is_left_out_too(self):
        # git no longer sees a repo there: it lists the files one by one and `git add -A` stages them as plain files
        self.fx.commit('app.py', 'print(2)\n')
        wt = os.path.join(self.fx.work, '.claude/worktrees/agent-x')
        git(self.fx.work, 'worktree', 'add', '-q', wt, '-b', 'agent-x')
        os.remove(os.path.join(self.fx.work, '.git', 'worktrees', 'agent-x', 'HEAD'))
        write(os.path.join(wt, 'inside.txt'), 'x\n')
        self.gated_push_passes()

    def test_an_embedded_repo_with_no_commit_does_not_break_minting(self):
        # `git add -A` fails outright on one ("does not have a commit checked out")
        self.fx.commit('app.py', 'print(2)\n')
        for name, has_file in (('tmp-repo', True), ('empty-repo', False)):
            repo = os.path.join(self.fx.work, name)
            os.makedirs(repo)
            git(repo, 'init', '-q')
            if has_file:
                write(os.path.join(repo, 'f.txt'), 'x\n')
        self.gated_push_passes()

    def test_clean_filters_do_not_run_on_excluded_artifacts(self):
        log = os.path.join(self.fx.tmp, 'spy.log')
        gate = 'mkdir -p cov && echo x > cov/out.cov'
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'), CONFIG.replace('echo gated', gate))
        write(os.path.join(self.fx.work, '.gitattributes'), '*.cov filter=spy\n')
        git(self.fx.work, 'add', '-A')
        git(self.fx.work, 'commit', '-q', '-m', 'gate and attributes')
        git(self.fx.work, 'config', 'filter.spy.clean', f'echo %f >> "{log}"; cat')
        p = self.fx.gate(gate)
        self.assertIn('not part of the receipt: cov/out.cov', p.stderr)
        self.assertNotIn('out.cov', open(log).read() if os.path.exists(log) else '')
        self.assertIsNone(self.fx.bash('git push -u origin HEAD'))

    def test_the_untracked_note_leaves_out_paths_a_push_may_not_commit(self):
        self.fx.commit('app.py', 'print(2)\n')
        for rel in ('.env.local', 'docs/n.md', 'new.py'):   # a worktree env file, an exempt doc, real code
            write(os.path.join(self.fx.work, rel), 'x\n')
        p = self.fx.gate()
        self.assertIn('new.py', p.stderr)
        self.assertNotIn('.env.local', p.stderr)
        self.assertNotIn('n.md', p.stderr)

    def ignored_worktree_gates(self, ignore_file):
        # vacancy-vibe's orchestrator checkout ignores .claude/worktrees/ this way; `git add` refuses a pathspec
        # that names an ignored path, even an exclude
        self.fx.commit('app.py', 'print(2)\n')
        if ignore_file == '.gitignore':
            self.fx.commit('.gitignore', '.claude/worktrees/\n')
        else:
            with open(os.path.join(self.fx.work, '.git', 'info', 'exclude'), 'a') as f:
                f.write('.claude/worktrees/\n')
        git(self.fx.work, 'worktree', 'add', '-q', os.path.join(self.fx.work, '.claude/worktrees/agent-x'), '-b', 'agent-x')
        self.gated_push_passes()

    def test_a_worktree_ignored_by_gitignore_does_not_stop_the_mint(self):
        self.ignored_worktree_gates('.gitignore')

    def test_a_worktree_ignored_by_info_exclude_does_not_stop_the_mint(self):
        self.ignored_worktree_gates('info/exclude')

    def test_a_path_the_gate_itself_ignores_does_not_stop_the_mint(self):
        # an embedded repo listed before the gate ran, ignored by the time of the mint
        gate = 'echo vendor/emb/ >> .git/info/exclude'
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'), CONFIG.replace('echo gated', gate))
        self.fx.commit('app.py', 'print(2)\n')
        emb = os.path.join(self.fx.work, 'vendor', 'emb')
        os.makedirs(emb)
        git(emb, 'init', '-q')
        self.fx.commit('e.txt', 'e\n', cwd=emb)
        self.gated_push_passes(gate)

    def test_a_tracked_gitlink_stays_in_the_receipt(self):
        # a real submodule is in HEAD^{tree}; leaving it out would deny every push in such a repo
        sub = os.path.join(self.fx.tmp, 'sub')
        os.makedirs(sub)
        git(sub, 'init', '-q')
        write(os.path.join(sub, 's.txt'), 's\n')
        git(sub, 'add', '-A')
        git(sub, 'commit', '-q', '-m', 's')
        git(self.fx.work, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', sub, 'mods/sub')
        git(self.fx.work, 'commit', '-q', '-m', 'submodule')
        self.gated_push_passes()
        self.assertIn('160000', git(self.fx.work, 'ls-tree', '-r', head_tree(self.fx.work)))


class ScopingTest(Case):
    def test_no_marker_silences_the_run_only_pieces(self):
        self.fx.commit('app.py', 'print(2)\n')
        self.assertIsNone(self.fx.bash('git push'))
        self.fx.start_run()
        self.assertIsNotNone(self.fx.bash('git push'))
        self.assertIsNone(self.fx.bash('git push', sid='S2'))

    def test_no_config_is_silent_even_with_foreman_config_set(self):
        fx = Fx(config=None)
        try:
            write(os.path.join(fx.tmp, 'cfg.md'), CONFIG)
            fx.env['FOREMAN_CONFIG'] = os.path.join(fx.tmp, 'cfg.md')
            state.write_json(os.path.join(fx.state, 'runs', 'S1.json'), {'started': time.time(), 'touched': time.time()})
            fx.commit('app.py', 'print(2)\n')
            self.assertIsNone(fx.bash('git push'))
        finally:
            fx.close()

    def test_hooks_disabled_drops_a_piece(self):
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'),
              CONFIG.replace('---\n', '---\nhooks_disabled: [gate_before_push]\n', 1))
        self.fx.start_run()
        self.fx.commit('app.py', 'print(2)\n')
        self.assertIsNone(self.fx.bash('git push'))

    def test_a_matching_piece_touches_the_marker(self):
        self.fx.start_run()
        before = self.fx.marker()['touched']
        time.sleep(0.01)
        self.fx.bash('git push origin :old')
        self.assertGreater(self.fx.marker()['touched'], before)


SKILL = os.path.join(HERE, '..', 'skills', 'foreman', 'SKILL.md')


class WorkerGuardTest(Case):
    def setUp(self):
        super().setUp()
        self.fx.start_run()

    def test_subagent_denials(self):
        for cmd, needle in (('gh pr merge 1 --squash --match-head-commit abc1234', 'workers never merge'),
                            ('gt merge', 'workers never merge'),
                            ('gh api -X PUT repos/o/r/pulls/1/merge', 'workers never merge'),
                            ("gh api graphql -f query='mutation { mergePullRequest(input:{}) { clientMutationId } }'",
                             'workers never merge'),
                            ('git push --force', 'no force-push'),
                            ('git push -uf origin HEAD', 'no force-push'),
                            ('git push --force-with-lease', 'no force-push'),
                            ('git push --mirror', 'no force-push'),
                            ('git push origin +feat', 'no force-push'),
                            ('git push origin HEAD:main', 'no push to main'),
                            ('git push origin main', 'no push to main'),
                            ('git rebase origin/main', 'no rebase'),
                            ('git pull --rebase', 'no rebase')):
            self.assertIn(needle, self.fx.bash(cmd, agent='a1') or '', cmd)

    def test_every_reason_quotes_a_line_of_the_worker_contract(self):
        # a reason that quotes words the contract doesn't hold sends the worker to a rule that isn't there
        with open(SKILL, encoding='utf-8') as f:
            section = re.search(r'^### Worker contract\n(.*?)^### ', f.read(), re.S | re.M)
        self.assertTrue(section, 'SKILL.md has no "### Worker contract" section')
        contract = ' '.join(section.group(1).split())
        for name in ('NO_MERGE', 'NO_FORCE', 'NO_REBASE', 'NO_BOOKKEEPING', 'NO_PUSH_DEFAULT', 'NO_AMEND'):
            quoted = re.findall(r'"([^"]+)"', getattr(hooks, name))
            self.assertTrue(quoted, f'{name} quotes no contract line')
            for q in quoted:
                self.assertIn(' '.join(q.split()), contract, name)

    def test_gt_modify_only_once_the_branch_has_an_upstream(self):
        git(self.fx.work, 'switch', '-q', '-c', 'feat')
        self.assertIsNone(self.fx.bash('gt modify -a', agent='a1'))
        git(self.fx.work, 'push', '-q', '-u', 'origin', 'feat')
        self.assertIn('no gt modify', self.fx.bash('gt modify -a', agent='a1'))

    def test_the_main_session_and_normal_calls_are_not_guarded(self):
        self.assertIsNone(self.fx.bash('git rebase origin/main'))
        self.assertIsNone(self.fx.bash('git push --force origin :old'))
        git(self.fx.work, 'switch', '-q', '-c', 'feat')
        self.fx.commit('app.py', 'print(2)\n')
        self.fx.gate()
        self.assertIsNone(self.fx.bash('git push -u origin HEAD', agent='a1'))
        self.assertIsNone(self.fx.bash('gh api repos/o/r/pulls/1/merge', agent='a1'))

    def test_no_marker_no_guard(self):
        self.assertIsNone(self.fx.bash('git rebase origin/main', agent='a1', sid='S2'))

    def test_hooks_disabled_drops_the_piece(self):
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'),
              CONFIG.replace('---\n', '---\nhooks_disabled: [worker_guard]\n', 1))
        self.assertIsNone(self.fx.bash('git rebase origin/main', agent='a1'))
        self.assertIsNone(self.fx.edit(os.path.join(self.fx.work, '.claude/improvement-backlog.md'), agent='a1'))
        # piece 3 still judges the same worker's merge
        self.assertIn('--match-head-commit', self.fx.bash('gh pr merge 5 --squash', agent='a1'))

    def test_bookkeeping_edits(self):
        for rel in ('.claude/improvement-backlog.md', '.claude/rules/x.md', '.claude/foreman.config.md'):
            self.assertIn("workers don't edit", self.fx.edit(os.path.join(self.fx.work, rel), agent='a1') or '', rel)
        self.assertIn("workers don't edit", self.fx.edit('.claude/rules/x.md', tool='Write', agent='a1') or '')
        self.assertIsNone(self.fx.edit(os.path.join(self.fx.work, 'app.py'), agent='a1'))
        self.assertIsNone(self.fx.edit(os.path.join(self.fx.work, '.claude/rules/x.md')))

    def test_a_worktrees_own_bookkeeping_files_are_guarded(self):
        wt = os.path.join(self.fx.tmp, 'wt')
        git(self.fx.work, 'worktree', 'add', '-q', wt, '-b', 'w1')
        self.assertIn("workers don't edit", self.fx.edit(os.path.join(wt, '.claude/improvement-backlog.md'),
                                                         agent='a1', cwd=wt))

    def test_a_worker_in_its_nested_worktree(self):
        # probe P6: an isolation: worktree worker's cwd is <repo>/.claude/worktrees/agent-<agent_id>, inside the main checkout
        wt = os.path.join(self.fx.work, '.claude', 'worktrees', 'agent-a1')
        git(self.fx.work, 'worktree', 'add', '-q', wt, '-b', 'w1')
        denied = lambda why: self.assertIn("workers don't edit", why or '')   # noqa: E731
        denied(self.fx.edit(os.path.join(wt, '.claude/improvement-backlog.md'), agent='a1', cwd=wt))
        denied(self.fx.edit('.claude/rules/new.md', tool='Write', agent='a1', cwd=wt))
        denied(self.fx.edit(os.path.join(self.fx.work, '.claude/improvement-backlog.md'), agent='a1', cwd=wt))
        self.assertIsNone(self.fx.edit(os.path.join(wt, 'app.py'), agent='a1', cwd=wt))
        self.assertIn('no rebase', self.fx.bash('git rebase origin/main', agent='a1', cwd=wt))
        self.assertIn('no push to main', self.fx.bash('git push origin HEAD:main', agent='a1', cwd=wt))

    def test_new_rules_file_in_missing_dir_is_denied(self):
        wt = os.path.join(self.fx.tmp, 'wt')
        git(self.fx.work, 'worktree', 'add', '-q', wt, '-b', 'w1')
        self.assertFalse(os.path.exists(os.path.join(wt, '.claude/rules')))
        self.assertIn("workers don't edit", self.fx.edit(os.path.join(wt, '.claude/rules/new.md'), tool='Write',
                                                         agent='a1', cwd=wt))


class MergeDisciplineTest(Case):
    def setUp(self):
        super().setUp()
        self.fx.start_run()

    def pr(self, head, commits=()):
        write(self.fx.env['FAKE_GH_PR'], json.dumps({'headRefName': head, 'commits': list(commits)}))

    def worktree(self, branch, wt=None):
        wt = wt or os.path.join(self.fx.tmp, 'wt')
        git(self.fx.work, 'worktree', 'add', '-q', wt, '-b', branch)
        self.pr(branch)
        return wt

    def test_merge_needs_match_head_commit(self):
        self.assertIn('add `--match-head-commit <sha>`', self.fx.bash('gh pr merge 5 --squash'))
        self.assertIsNone(self.fx.bash('gh pr merge 5 --squash --match-head-commit abc1234'))
        self.assertIsNone(self.fx.bash('gh pr merge 5 --squash --match-head-commit=abc1234'))

    def test_an_empty_match_head_commit_pins_nothing(self):
        for cmd in ('gh pr merge 5 --match-head-commit=', "gh pr merge 5 --match-head-commit ''"):
            self.assertIn('add `--match-head-commit <sha>`', self.fx.bash(cmd), cmd)

    def test_delete_branch_when_only_the_own_worktree_holds_it(self):
        git(self.fx.work, 'switch', '-q', '-c', 'feat')
        self.pr('feat')
        self.assertIsNone(self.fx.bash('gh pr merge 5 --squash --match-head-commit abc1234 --delete-branch'))

    def test_delete_branch_from_the_worktree_that_holds_it(self):
        wt = self.worktree('feat2')   # the main checkout holds main; the command runs in the worktree
        self.assertIsNone(self.fx.bash('gh pr merge 5 --squash --match-head-commit abc1234 -d', cwd=wt))

    def test_delete_branch_when_another_worktree_holds_it(self):
        wt = self.worktree('feat2')
        why = self.fx.bash('gh pr merge 5 --squash --match-head-commit abc1234 -d')
        self.assertIn(f'remove the worktree at {wt} first', why)
        for flags in ('-sd', '--delete-branch=true', '-d=true'):
            self.assertIn('remove the worktree', self.fx.bash(f'gh pr merge 5 --match-head-commit abc1234 {flags}'), flags)
        self.assertIsNone(self.fx.bash('gh pr merge 5 --match-head-commit abc1234 --delete-branch=false'))

    def test_delete_branch_with_the_worktree_nested_in_the_main_checkout(self):
        wt = self.worktree('feat2', os.path.join(self.fx.work, '.claude', 'worktrees', 'agent-a1'))   # probe P6's layout
        merge = 'gh pr merge 5 --squash --match-head-commit abc1234 --delete-branch'
        self.assertIn(f'remove the worktree at {wt} first', self.fx.bash(merge))   # serial step 12, from the main checkout
        self.assertIsNone(self.fx.bash(merge, cwd=wt))

    def test_delete_branch_is_not_judged_without_a_pr_head(self):
        self.worktree('feat2')
        os.remove(self.fx.env['FAKE_GH_PR'])   # the fake answers headRefName "feat", which no worktree holds
        self.assertIsNone(self.fx.bash('gh pr merge 5 --match-head-commit abc1234 --delete-branch'))
        self.assertIsNone(self.fx.bash('gh pr merge 5 --match-head-commit abc1234'))   # no -d: no lookup at all

    def test_a_failing_gh_view_allows_the_call_with_a_note(self):
        self.worktree('feat2')
        broken = os.path.join(self.fx.tmp, 'broken')
        write(os.path.join(broken, 'gh'), '#!/bin/sh\nexit 1\n')
        os.chmod(os.path.join(broken, 'gh'), 0o755)
        self.fx.env['PATH'] = broken + os.pathsep + self.fx.env['PATH']
        out, err = self.fx.hook('pretool', self.fx.payload(
            'Bash', {'command': 'gh pr merge 5 --match-head-commit abc1234 -d'}))
        self.assertIsNone(out)
        self.assertIn('gh pr view failed', err)
        for body in ('[1]', 'null', '"x"'):   # gh answering something that isn't an object counts as failing
            write(os.path.join(broken, 'gh'), f"#!/bin/sh\necho '{body}'\n")
            out, err = self.fx.hook('pretool', self.fx.payload(
                'Bash', {'command': 'gh pr merge 5 --match-head-commit abc1234 -d'}))
            self.assertEqual((out, 'foreman hook error' in err), (None, False), body)

    def test_gt_merge_is_denied_during_a_run(self):
        self.assertIn('no gt merge during a run', self.fx.bash('gt merge'))
        self.assertIsNone(self.fx.bash('gt merge', sid='S2'))

    def test_a_subagent_merge_is_decided_locally(self):
        why = self.fx.bash('gh pr merge 5 --squash -d', agent='a1')
        self.assertIn('workers never merge', why)
        self.assertNotIn('match-head-commit', why)
        self.assertFalse(os.path.exists(self.fx.env['FAKE_GH_LOG']))

    def test_a_subagent_gt_merge_is_not_told_how_to_merge(self):
        why = self.fx.bash('gt merge', agent='a1')
        self.assertIn('workers never merge', why)
        self.assertNotIn('match-head-commit', why)   # a worker told to `gh pr merge` would read it as leave to merge

    def test_hooks_disabled_drops_the_piece(self):
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'),
              CONFIG.replace('---\n', '---\nhooks_disabled: [merge_discipline]\n', 1))
        self.assertIsNone(self.fx.bash('gh pr merge 5 --squash'))
        self.assertIsNone(self.fx.bash('gt merge'))


class WorktreesTest(InProcess):
    def test_lists_every_worktree_with_its_branch_ref(self):
        wt = os.path.join(self.fx.tmp, 'wt')
        git(self.fx.work, 'worktree', 'add', '-q', '--detach', wt)
        self.assertEqual(hooks.worktrees(self.fx.work), [(self.fx.work, 'refs/heads/main'), (wt, None)])

    def test_a_failing_git_gives_nothing_with_a_note(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(hooks.worktrees(os.path.join(self.fx.tmp, 'missing')), [])
        self.assertIn('git worktree list failed', err.getvalue())


class OptionHelpersTest(unittest.TestCase):
    def test_opt_values_and_has_flag(self):
        self.assertEqual(hooks.opt_values(['-am', 'msg'], ('-m', '--message')), ['msg'])
        self.assertEqual(hooks.opt_values(['-mfoo', '--message=bar', '-m', 'baz'], ('-m', '--message')),
                         ['foo', 'bar', 'baz'])
        self.assertEqual(hooks.opt_values(['--', '-m', 'x'], ('-m',)), [])
        self.assertTrue(hooks.has_flag(['-sd'], ('--delete-branch', '-d')))
        self.assertFalse(hooks.has_flag(['--squash'], ('--delete-branch', '-d')))
        self.assertTrue(hooks.has_flag(['--delete-branch=true'], ('--delete-branch', '-d')))
        self.assertFalse(hooks.has_flag(['--delete-branch=false', '--body=-d', '-bfoo=-d'], ('--delete-branch', '-d')))
        self.assertEqual(hooks.positionals(['--squash', '-t', 'x', '12'], hooks.GH_MERGE_VALUE_OPTS), ['12'])


class TrailerGuardTest(Case):
    def test_trailer_in_a_message_option(self):
        self.assertIn('starts with "Co-Authored-By"', self.fx.bash('git commit -m "fix: x" -m "Co-Authored-By: a <a@b>"'))
        self.assertIn('Claude-Session', self.fx.bash('git commit -am "x\nClaude-Session: abc"'))
        self.assertIn('Co-Authored-By', self.fx.bash('gt create -m "x\n\nco-authored-by: a"'))

    def test_trailer_in_a_message_file_in_the_scratchpad(self):
        msg = os.path.join(self.fx.tmp, 'scratch', 'msg.txt')
        write(msg, 'fix: thing\n\nCo-Authored-By: a <a@b>\n')
        self.assertIn('Co-Authored-By', self.fx.bash(f'git commit -F {msg}'))
        write(msg, 'fix: thing\n')
        self.assertIsNone(self.fx.bash(f'git commit -F {msg}'))

    def test_trailer_in_a_heredoc(self):
        self.assertIn('Co-Authored-By', self.fx.bash("git commit -F - <<'EOF'\nfix: x\n\nCo-Authored-By: a\nEOF"))
        cmd = ("git commit -m \"$(cat <<'EOF'\nfix: x\n\n- \"two words\"\nCo-Authored-By: a <a@b>\nEOF\n)\"")
        self.assertIn('Co-Authored-By', self.fx.bash(cmd))
        later = os.path.join(self.fx.tmp, 'later.txt')
        self.assertIn('Co-Authored-By', self.fx.bash(f"cat > {later} <<'EOF'\nx\nCo-Authored-By: a\nEOF\ngit commit -F {later}"))

    def test_trailer_in_a_heredoc_piped_into_a_stdin_message(self):
        # shellscan attaches a heredoc only to the command it is written on, so `cat <<EOF | gh ... -F -` reads sc.docs
        pipe = "cat <<'EOF' | gh pr create --title t --body-file -\n## Summary\n\nCo-Authored-By: a\nEOF"
        self.assertIn('Co-Authored-By', self.fx.bash(pipe))
        self.assertIn('Co-Authored-By', self.fx.bash(pipe.replace('| gh pr', '| tee /dev/null | gh pr')))   # two stages back
        self.assertIsNone(self.fx.bash(pipe.replace('Co-Authored-By: a', 'all good')))
        # a heredoc that feeds something else isn't what the candidate reads
        other = "cat <<'EOF' > notes.txt\nCo-Authored-By: a\nEOF\necho ok | gh pr create --title t --body-file -"
        self.assertIsNone(self.fx.bash(other))

    def test_generated_with_footer_in_a_pr_body(self):
        cmd = ("gh pr create --title t --body \"$(cat <<'EOF'\n## Summary\n- \"two words\"\n\n"
               "🤖 Generated with [Claude Code](https://claude.com/claude-code)\nEOF\n)\"")
        self.assertIn('Generated with', self.fx.bash(cmd))
        self.assertIn('Generated with', self.fx.bash('gh pr comment 5 --body "ok\n🤖 Generated with Claude Code"'))

    def test_mid_sentence_mention_is_allowed(self):
        self.assertIsNone(self.fx.bash('git commit -m "fix: drop the Co-Authored-By line from the template"'))

    def test_regex_entry_searches_within_lines(self):
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'),
              CONFIG.replace('Generated with]', '/Generated with \\[Claude Code\\]/]'))
        self.assertIsNone(self.fx.bash('git commit -m "x\n- Generated with the seed script"'))
        self.assertIn('Generated with', self.fx.bash('git commit -m "x\n🤖 Generated with [Claude Code](https://x)"'))
        self.assertIsNone(self.fx.bash('git commit -m "x\n🤖 generated with [claude code](https://x)"'))   # a regex is case-sensitive

    def test_a_bad_regex_entry_is_skipped_with_a_note(self):
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'), CONFIG.replace('[Co-Authored-By, ', '[/(/, '))   # the bad entry first: a later hit would end the search
        out, err = self.fx.hook('pretool', self.fx.payload('Bash', {'command': 'git commit -m "x\nClaude-Session: a"'}))
        self.assertIn('Claude-Session', out['hookSpecificOutput']['permissionDecisionReason'])   # the other entries still apply
        self.assertIn('bad forbid_trailers regex /(/', err)
        self.assertNotIn('foreman hook error', err)

    def test_a_message_file_resolves_against_the_effective_directory(self):
        write(os.path.join(self.fx.work, 'sub', 'msg.txt'), 'fix: x\n\nCo-Authored-By: a\n')
        self.assertIn('Co-Authored-By', self.fx.bash(f'cd {self.fx.work}/sub && git commit -F msg.txt'))
        self.assertIn('Co-Authored-By', self.fx.bash('git commit -F msg.txt', cwd=os.path.join(self.fx.work, 'sub')))
        self.assertIsNone(self.fx.bash('git commit -F msg.txt'))   # no msg.txt at the top: skipped with a note

    def test_applies_without_a_run_and_not_with_an_empty_list(self):
        self.assertIsNotNone(self.fx.bash('git commit -m "x\nCo-Authored-By: a"', sid='S9'))
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'),
              CONFIG.replace('[Co-Authored-By, Claude-Session, Generated with]', '[]'))
        self.assertIsNone(self.fx.bash('git commit -m "x\nCo-Authored-By: a"'))

    def test_unknown_arguments_check_the_raw_text(self):
        self.assertIn('Co-Authored-By', self.fx.bash('git commit -m "x\nCo-Authored-By: a'))

    def test_hooks_disabled_drops_the_piece(self):
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'),
              CONFIG.replace('---\n', '---\nhooks_disabled: [trailer_guard]\n', 1))
        self.assertIsNone(self.fx.bash('git commit -m "x\nCo-Authored-By: a"'))

    def test_merges_whose_commits_carry_a_trailer(self):
        commits = [{'oid': 'abcdef1234567', 'messageHeadline': 'fix', 'messageBody': 'Co-Authored-By: a <a@b>'}]
        write(self.fx.env['FAKE_GH_PR'], json.dumps({'headRefName': 'feat', 'commits': commits}))
        base = 'gh pr merge 5 --match-head-commit abc1234 '
        self.assertIn('pass --body', self.fx.bash(base + '--squash'))
        self.assertIsNone(self.fx.bash(base + '--squash --subject "fix: x" --body "clean body"'))
        clean = os.path.join(self.fx.tmp, 'clean.txt')
        write(clean, 'clean body\n')
        self.assertIsNone(self.fx.bash(base + f'--squash --body-file {clean}'))
        self.assertIn('merge and rebase keep every commit message', self.fx.bash(base + '--merge'))
        self.assertIn('merge and rebase keep every commit message', self.fx.bash(base + '--rebase'))
        self.assertIn('abcdef123', self.fx.bash(base + '--rebase'))

    def test_a_merge_with_no_method_is_judged_like_merge(self):
        commits = [{'oid': 'abcdef1234567', 'messageHeadline': 'fix', 'messageBody': 'Co-Authored-By: a <a@b>'}]
        write(self.fx.env['FAKE_GH_PR'], json.dumps({'headRefName': 'feat', 'commits': commits}))
        self.assertIn('merge and rebase keep every commit message', self.fx.bash('gh pr merge 5 --match-head-commit abc1234'))

    def test_a_merge_of_clean_commits_is_allowed(self):
        commits = [{'oid': 'abcdef1234567', 'messageHeadline': 'fix', 'messageBody': 'about the Co-Authored-By line'}]
        write(self.fx.env['FAKE_GH_PR'], json.dumps({'headRefName': 'feat', 'commits': commits}))
        for method in ('--squash', '--merge', '--rebase'):
            self.assertIsNone(self.fx.bash(f'gh pr merge 5 --match-head-commit abc1234 {method}'), method)

    def test_the_merge_check_reuses_piece_3s_gh_call(self):
        self.fx.start_run()
        commits = [{'oid': 'abcdef1234567', 'messageHeadline': 'fix', 'messageBody': 'Co-Authored-By: a <a@b>'}]
        write(self.fx.env['FAKE_GH_PR'], json.dumps({'headRefName': 'feat', 'commits': commits}))
        # --delete-branch makes piece 3 read the PR's head, piece 4 reads the commits: one gh call serves both
        why = self.fx.bash('gh pr merge 5 --squash --match-head-commit abc1234 --delete-branch')
        self.assertIn('pass --body', why)
        with open(self.fx.env['FAKE_GH_LOG']) as f:
            self.assertEqual(len([l for l in f.read().splitlines() if l.startswith('pr view')]), 1)

    def test_a_denied_worker_merge_makes_no_gh_call(self):
        self.fx.start_run()
        commits = [{'oid': 'abcdef1234567', 'messageHeadline': 'fix', 'messageBody': 'Co-Authored-By: a <a@b>'}]
        write(self.fx.env['FAKE_GH_PR'], json.dumps({'headRefName': 'feat', 'commits': commits}))
        why = self.fx.bash('gh pr merge 5 --squash --match-head-commit abc1234', agent='a1')
        self.assertIn('workers never merge', why)
        self.assertNotIn('forbidden trailer', why)
        self.assertFalse(os.path.exists(self.fx.env['FAKE_GH_LOG']))


class BacklogCheckTest(Case):
    def post(self, path, tool='Edit', **kw):
        out, _ = self.fx.hook('posttool', self.fx.payload(tool, {'file_path': path}, event='PostToolUse', **kw))
        return out['hookSpecificOutput']['additionalContext'] if out else None

    def backlog_path(self):
        return os.path.join(self.fx.work, '.claude/improvement-backlog.md')

    def write_raw(self, text):
        with open(self.backlog_path(), 'w', newline='') as f:   # newline='': keep the CRLFs the caller wrote
            f.write(text)

    def test_clean_backlog_returns_nothing(self):
        self.assertIsNone(self.post(os.path.join(self.fx.work, '.claude/improvement-backlog.md')))
        self.assertIsNone(self.post(os.path.join(self.fx.work, 'app.py')))

    def test_a_bookkeeping_worktree_edit_is_checked(self):
        wt = os.path.join(self.fx.tmp, 'bk')
        git(self.fx.work, 'worktree', 'add', '-q', wt, '-b', 'bk')
        bad = BACKLOG + '| IB-3 | Three | a | b | low | ready | C | s |\n| IB-3 | Dup | low | ready | D | s |\n'
        write(os.path.join(wt, '.claude/improvement-backlog.md'), bad)
        ctx = self.post(os.path.join(wt, '.claude/improvement-backlog.md'), tool='Write', cwd=wt)
        self.assertIn('the backlog now has 2 problem rows', ctx)
        self.assertIn('IB-3: 7 " | " separators, want 5', ctx)
        self.assertIn('IB-3: on 2 rows', ctx)

    def test_crlf_backlog(self):
        # a CRLF file is read as is (newline=''), so every row ends in \r and the check must not carry it along
        self.write_raw(BACKLOG.replace('\n', '\r\n'))
        self.assertIsNone(self.post(self.backlog_path()))
        self.write_raw((BACKLOG + '| IB-3 | Broken | low |\n').replace('\n', '\r\n'))
        self.assertIn('IB-3: 2 " | " separators, want 5', self.post(self.backlog_path()))
        # a row cut back to its id has no " | " to name it by: the \r that ends the line must not leak into the name
        self.write_raw((BACKLOG + '| IB-4 |\n').replace('\n', '\r\n'))
        ctx = self.post(self.backlog_path())
        self.assertIn('0 " | " separators, want 5', ctx)
        self.assertIn('IB-4', ctx)
        self.assertNotIn('\r', ctx)

    def test_a_row_counts_once_however_many_problems_it_has(self):
        # two broken rows sharing an id: three problems, two rows
        self.write_raw(BACKLOG + '| IB-3 | Broken | low |\n| IB-3 | Also | low |\n')
        ctx = self.post(self.backlog_path())
        self.assertIn('the backlog now has 2 problem rows:', ctx)
        self.assertEqual(ctx.count('IB-3: 2 " | " separators, want 5'), 2)
        self.assertIn('IB-3: on 2 rows', ctx)

    def test_only_the_first_ten_problems_are_listed(self):
        self.write_raw(BACKLOG + ''.join(f'| IB-{n} | Broken | low |\n' for n in range(10, 22)))
        ctx = self.post(self.backlog_path())
        self.assertIn('the backlog now has 12 problem rows:', ctx)
        self.assertEqual(ctx.count('separators, want 5'), 10)
        self.assertIn('IB-19:', ctx)
        self.assertNotIn('IB-20:', ctx)

    def test_hooks_disabled_drops_the_piece(self):
        write(os.path.join(self.fx.work, '.claude/foreman.config.md'),
              CONFIG.replace('---\n', '---\nhooks_disabled: [backlog_check]\n', 1))
        self.write_raw(BACKLOG + '| IB-3 | Broken | low |\n')
        self.assertIsNone(self.post(self.backlog_path()))


if __name__ == '__main__':
    unittest.main()
