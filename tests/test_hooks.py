# tests/test_hooks.py: feeds hook JSON to scripts/hooks.py in temp repos (temp FOREMAN_HOME, fake gh)
import calendar
import contextlib
import io
import json
import os
import re
import shutil
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


if __name__ == '__main__':
    unittest.main()
