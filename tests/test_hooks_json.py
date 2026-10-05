import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import hooks  # noqa: E402

WANT = {
    ('UserPromptSubmit', None): 'run-start',
    ('UserPromptExpansion', None): 'run-start',
    ('SessionStart', None): 'session-start',
    ('SessionEnd', None): 'session-end',
    ('PreToolUse', 'Bash'): 'pretool',
    ('PreToolUse', 'Edit|Write|MultiEdit|NotebookEdit'): 'pretool',
    ('PreToolUse', 'AskUserQuestion|TaskStop'): 'pretool',
    ('PostToolUse', 'Edit|Write|MultiEdit'): 'posttool',
    ('PostToolUse', 'Bash'): 'posttool',
    ('SubagentStart', None): 'subagent-start',
    ('SubagentStop', None): 'subagent-stop',
    ('PermissionRequest', 'Bash'): 'permission',
}


class HooksJsonTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, 'hooks', 'hooks.json')) as f:
            self.data = json.load(f)

    def entries(self):
        for event, groups in self.data['hooks'].items():
            for g in groups:
                for h in g['hooks']:
                    yield event, g.get('matcher'), h

    def test_every_event_runs_its_subcommand_in_exec_form(self):
        seen = {}
        for event, matcher, h in self.entries():
            self.assertEqual(h['type'], 'command')
            self.assertEqual(h['command'], 'python3')
            self.assertEqual(h['args'][0], '${CLAUDE_PLUGIN_ROOT}/scripts/hooks.py')
            self.assertNotIn('if', h)
            self.assertEqual(h['timeout'], 30 if event == 'SubagentStop' else 10)
            seen[(event, matcher)] = h['args'][1]
        self.assertEqual(seen, WANT)

    def test_every_subcommand_has_a_handler(self):
        for sub in set(WANT.values()):
            self.assertIn(sub, hooks.HANDLERS, sub)

    def test_the_mod_is_declared_and_exists(self):
        self.assertEqual(self.data['modules'], ['./status.mjs'])
        self.assertTrue(os.path.isfile(os.path.join(ROOT, 'hooks', 'status.mjs')))


if __name__ == '__main__':
    unittest.main()
