import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
import backlog  # noqa: E402

FIXTURE = '''# Improvement Backlog

- **status:** `ready` · `done`

## Low risk (ready)

| IB-1 | First row with `code` | low | ready | Accept when: a \\| b works. | #10 review |
| IB-2 | Second | medium | done (#12) | Did it. **DONE (#12):** shipped. | #11 |
| IB-3 | Broken row missing cells | low | ready |
| IB-4 | Fourth | high | blocked — waits on Meta | Needs approval. | IB-1 |
| IB-5 | Fifth | low | ready | Text. | #13 |
'''


class ParseTest(unittest.TestCase):
    def test_round_trip_is_byte_identical(self):
        self.assertEqual(backlog.serialise(backlog.parse(FIXTURE)), FIXTURE)

    def test_round_trip_without_trailing_newline(self):
        t = FIXTURE.rstrip('\n')
        self.assertEqual(backlog.serialise(backlog.parse(t)), t)

    def test_fields_and_status(self):
        bl = backlog.parse(FIXTURE)
        rows = {r.id: r for r in bl.rows()}
        self.assertEqual(sorted(rows), ['IB-1', 'IB-2', 'IB-4', 'IB-5'])
        self.assertEqual(rows['IB-1'].acceptance, 'Accept when: a \\| b works.')
        self.assertEqual(rows['IB-2'].status, 'done')
        self.assertEqual(rows['IB-2'].status_text, 'done (#12)')
        self.assertEqual(rows['IB-4'].status, 'blocked')
        self.assertEqual(rows['IB-1'].source, '#10 review')

    def test_malformed_row_is_kept_unparsed(self):
        bl = backlog.parse(FIXTURE)
        unparsed = [l for l in bl.lines if isinstance(l, backlog.Unparsed)]
        self.assertEqual(len(unparsed), 1)
        self.assertTrue(unparsed[0].startswith('| IB-3 |'))

    def test_find_and_ready_indexes(self):
        bl = backlog.parse(FIXTURE)
        self.assertIsNotNone(bl.find('IB-5'))
        self.assertIsNone(bl.find('IB-99'))
        ready = [bl.lines[i].id for i in bl.ready_indexes()]
        self.assertEqual(ready, ['IB-1', 'IB-5'])

    def test_other_prefix(self):
        t = '| FX-7 | A | low | ready | B | C |\n'
        bl = backlog.parse(t, 'FX')
        self.assertEqual(bl.rows()[0].id, 'FX-7')
        self.assertEqual(backlog.serialise(bl), t)

    @unittest.skipUnless(os.environ.get('FOREMAN_REAL_BACKLOG'), 'set FOREMAN_REAL_BACKLOG to a real backlog file')
    def test_real_backlog_round_trip(self):
        t = open(os.environ['FOREMAN_REAL_BACKLOG'], encoding='utf-8').read()
        self.assertEqual(backlog.serialise(backlog.parse(t)), t)


class BuildTest(unittest.TestCase):
    def test_escape_pipes_and_reject_newlines(self):
        self.assertEqual(backlog.escape('a | b'), 'a \\| b')
        self.assertEqual(backlog.escape('a \\| b'), 'a \\| b')
        row, problems = backlog.make_row(['IB-9', 'T', 'low', 'ready', 'one\ntwo', 's'], 'IB')
        self.assertIsNone(row)
        self.assertIn('acceptance has a newline', problems)

    def test_build_round_trips_through_parse(self):
        cells = ['IB-9', 'Title `x`', 'medium', 'blocked — waits on a \\| b', 'Acc.', '#1']
        line = backlog.build(cells)
        self.assertEqual(line.count(' | '), 5)
        self.assertEqual(backlog.parse(line).rows()[0].cells(), cells)

    def test_validate_rules(self):
        ok = ['IB-9', 'T', 'low', 'ready', 'A', 'S']
        self.assertEqual(backlog.validate_cells(ok, 'IB'), [])
        self.assertIn('risk must be low, medium or high', backlog.validate_cells(['IB-9', 'T', 'huge', 'ready', 'A', 'S'], 'IB'))
        self.assertIn('unknown status "maybe"', backlog.validate_cells(['IB-9', 'T', 'low', 'maybe', 'A', 'S'], 'IB'))
        self.assertIn('title is empty', backlog.validate_cells(['IB-9', ' ', 'low', 'ready', 'A', 'S'], 'IB'))
        self.assertIn('bad id "X-1"', backlog.validate_cells(['X-1', 'T', 'low', 'ready', 'A', 'S'], 'IB'))
        self.assertEqual(backlog.validate_cells(['NEW-1', 'T', 'low', 'ready', 'A', 'S'], 'IB'), [])

    def test_line_problems(self):
        self.assertEqual(backlog.line_problems('| IB-1 | a | b | c | d | e |'), [])
        self.assertEqual(backlog.line_problems('| IB-1 | a | b |'), [('separators', 2)])
        self.assertEqual(backlog.line_problems('| IB-1 | a\nb | c | d | e | f |'), [('newline',)])


if __name__ == '__main__':
    unittest.main()
