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

    def test_crlf_survives_edits_adds_and_numbering(self):
        bl = backlog.parse(FIXTURE.replace('\n', '\r\n'))
        r = backlog.apply(bl, [
            {'op': 'set', 'id': 'IB-5', 'field': 'title', 'value': 'Five', 'base': base_of(bl, 'IB-5')},
            {'op': 'add', 'tmp': 'NEW-1', 'title': 'A', 'risk': 'low', 'status': 'ready', 'reason': '', 'acceptance': 'X', 'source': 'ui', 'insert': 'top'},
        ])
        self.assertEqual([x['result'] for x in r], ['applied', 'applied'])
        backlog.assign_ids(bl, ['NEW-1'])
        out = backlog.serialise(bl)
        self.assertNotIn('\n', out.replace('\r\n', ''))
        self.assertIn('| IB-5 | Five | low | ready | Text. | #13 |\r\n', out)
        self.assertIn('| IB-6 | A | low | ready | X | ui |\r\n', out)

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

    def test_duplicate_ids_are_reported(self):
        t = FIXTURE + '| IB-5 | Again | low | ready | x | y |\n| IB-5 | Thrice | low | ready | x | y |\n| IB-4 | Twice |\n'
        self.assertEqual(backlog.parse(t).duplicates(), {'IB-5': 3, 'IB-4': 2})
        self.assertEqual(backlog.parse(FIXTURE).duplicates(), {})

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


def ops_fixture():
    return backlog.parse(FIXTURE)


def base_of(bl, id_):
    return bl.lines[bl.find(id_)].raw


class OpsTest(unittest.TestCase):
    def test_set_changes_only_that_line(self):
        bl = ops_fixture()
        r = backlog.apply(bl, [{'op': 'set', 'id': 'IB-5', 'field': 'title', 'value': 'New | title', 'base': base_of(bl, 'IB-5')}])
        self.assertEqual(r[0]['result'], 'applied')
        before, after = FIXTURE.split('\n'), backlog.serialise(bl).split('\n')
        changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertIn('| New \\| title |', after[changed[0]])

    def test_status_needs_reason_for_blocked(self):
        bl = ops_fixture()
        r = backlog.apply(bl, [{'op': 'status', 'id': 'IB-1', 'to': 'blocked', 'reason': '', 'base': base_of(bl, 'IB-1')}])
        self.assertEqual(r[0]['result'], 'invalid')
        r = backlog.apply(bl, [{'op': 'status', 'id': 'IB-1', 'to': 'blocked', 'reason': 'waits on #20', 'base': base_of(bl, 'IB-1')}])
        self.assertEqual(r[0]['result'], 'applied')
        self.assertEqual(bl.lines[bl.find('IB-1')].status_text, 'blocked — waits on #20')

    def test_move_top_bottom_before_after(self):
        bl = ops_fixture()
        backlog.apply(bl, [{'op': 'move', 'id': 'IB-5', 'to': 'top', 'base': base_of(bl, 'IB-5')}])
        self.assertEqual([bl.lines[i].id for i in bl.ready_indexes()], ['IB-5', 'IB-1'])
        backlog.apply(bl, [{'op': 'move', 'id': 'IB-5', 'to': 'bottom', 'base': base_of(bl, 'IB-5')}])
        self.assertEqual([bl.lines[i].id for i in bl.ready_indexes()], ['IB-1', 'IB-5'])
        backlog.apply(bl, [{'op': 'move', 'id': 'IB-1', 'to': {'after': 'IB-5'}, 'base': base_of(bl, 'IB-1')}])
        self.assertEqual([bl.lines[i].id for i in bl.ready_indexes()], ['IB-5', 'IB-1'])
        backlog.apply(bl, [{'op': 'move', 'id': 'IB-1', 'to': {'before': 'IB-5'}, 'base': base_of(bl, 'IB-1')}])
        self.assertEqual([bl.lines[i].id for i in bl.ready_indexes()], ['IB-1', 'IB-5'])

    def test_conflict_on_changed_base(self):
        bl = ops_fixture()
        r = backlog.apply(bl, [{'op': 'status', 'id': 'IB-5', 'to': 'deferred', 'reason': '', 'base': '| IB-5 | Fifth | low | ready | OLD | #13 |'}])
        self.assertEqual(r[0]['result'], 'conflict')
        self.assertIn('now "ready"', r[0]['reason'])
        self.assertEqual(backlog.serialise(bl), FIXTURE)

    def test_conflict_on_missing_row_and_anchor(self):
        bl = ops_fixture()
        r = backlog.apply(bl, [{'op': 'set', 'id': 'IB-99', 'field': 'title', 'value': 'x', 'base': 'x'},
                               {'op': 'move', 'id': 'IB-5', 'to': {'before': 'IB-77'}, 'base': base_of(bl, 'IB-5')}])
        self.assertEqual([x['result'] for x in r], ['conflict', 'conflict'])
        self.assertEqual(backlog.serialise(bl), FIXTURE)

    def test_chained_ops_follow_each_other(self):
        bl = ops_fixture()
        b1 = base_of(bl, 'IB-5')
        backlog.apply(bl, [{'op': 'set', 'id': 'IB-5', 'field': 'risk', 'value': 'medium', 'base': b1}])
        b2 = base_of(bl, 'IB-5')
        fresh = ops_fixture()
        r = backlog.apply(fresh, [{'op': 'set', 'id': 'IB-5', 'field': 'risk', 'value': 'medium', 'base': b1},
                                  {'op': 'status', 'id': 'IB-5', 'to': 'deferred', 'reason': '', 'base': b2}])
        self.assertEqual([x['result'] for x in r], ['applied', 'applied'])

    def test_add_and_assign_ids(self):
        bl = ops_fixture()
        r = backlog.apply(bl, [
            {'op': 'add', 'tmp': 'NEW-1', 'title': 'A | b', 'risk': 'low', 'status': 'ready', 'reason': '', 'acceptance': 'X', 'source': 'ui', 'insert': 'top'},
            {'op': 'add', 'tmp': 'NEW-2', 'title': 'C', 'risk': 'low', 'status': 'ready', 'reason': '', 'acceptance': 'Y', 'source': 'ui', 'insert': 'bottom'},
        ])
        self.assertEqual([x['result'] for x in r], ['applied', 'applied'])
        self.assertEqual([bl.lines[i].id for i in bl.ready_indexes()], ['NEW-1', 'IB-1', 'IB-5', 'NEW-2'])
        mapping = backlog.assign_ids(bl, ['NEW-1', 'NEW-2'])
        self.assertEqual(mapping, {'NEW-1': 'IB-6', 'NEW-2': 'IB-7'})
        self.assertEqual([bl.lines[i].id for i in bl.ready_indexes()], ['IB-6', 'IB-1', 'IB-5', 'IB-7'])
        self.assertIn('| IB-6 | A \\| b | low | ready | X | ui |', backlog.serialise(bl))

    def test_invalid_value_changes_nothing(self):
        bl = ops_fixture()
        r = backlog.apply(bl, [{'op': 'set', 'id': 'IB-5', 'field': 'risk', 'value': 'huge', 'base': base_of(bl, 'IB-5')}])
        self.assertEqual(r[0]['result'], 'invalid')
        self.assertEqual(backlog.serialise(bl), FIXTURE)

    def test_check_op_shape(self):
        with self.assertRaises(ValueError):
            backlog.check_op_shape({'op': 'set', 'id': 'IB-1', 'field': 'id', 'value': 'x'})
        with self.assertRaises(ValueError):
            backlog.check_op_shape({'op': 'explode'})
        backlog.check_op_shape({'op': 'move', 'id': 'IB-1', 'to': {'after': 'IB-2'}})

    def test_check_op_shape_add_validation(self):
        # add missing risk
        with self.assertRaises(ValueError):
            backlog.check_op_shape({'op': 'add', 'tmp': 'NEW-1', 'title': 'T', 'status': 'ready', 'reason': '', 'acceptance': 'A', 'source': 's', 'insert': 'top'})
        # add with bad tmp (not NEW-n)
        with self.assertRaises(ValueError):
            backlog.check_op_shape({'op': 'add', 'tmp': 'IB-1', 'title': 'T', 'risk': 'low', 'status': 'ready', 'reason': '', 'acceptance': 'A', 'source': 's', 'insert': 'top'})
        # add with invalid status 'done'
        with self.assertRaises(ValueError):
            backlog.check_op_shape({'op': 'add', 'tmp': 'NEW-1', 'title': 'T', 'risk': 'low', 'status': 'done', 'reason': '', 'acceptance': 'A', 'source': 's', 'insert': 'top'})
        # add with non-str acceptance
        with self.assertRaises(ValueError):
            backlog.check_op_shape({'op': 'add', 'tmp': 'NEW-1', 'title': 'T', 'risk': 'low', 'status': 'ready', 'reason': '', 'acceptance': 123, 'source': 's', 'insert': 'top'})
        # valid add should not raise
        backlog.check_op_shape({'op': 'add', 'tmp': 'NEW-1', 'title': 'T', 'risk': 'low', 'status': 'ready', 'reason': '', 'acceptance': 'A', 'source': 's', 'insert': 'top'})

    def test_add_with_duplicate_tmp_is_invalid(self):
        bl = ops_fixture()
        r = backlog.apply(bl, [
            {'op': 'add', 'tmp': 'NEW-1', 'title': 'First', 'risk': 'low', 'status': 'ready', 'reason': '', 'acceptance': 'X', 'source': 'ui', 'insert': 'top'},
            {'op': 'add', 'tmp': 'NEW-1', 'title': 'Duplicate', 'risk': 'low', 'status': 'ready', 'reason': '', 'acceptance': 'Y', 'source': 'ui', 'insert': 'bottom'},
        ])
        self.assertEqual(r[0]['result'], 'applied')
        self.assertEqual(r[1]['result'], 'invalid')
        self.assertIn('already in the backlog', r[1]['reason'])
        # verify second add didn't mutate backlog
        ready_ids = [bl.lines[i].id for i in bl.ready_indexes()]
        self.assertEqual(ready_ids, ['NEW-1', 'IB-1', 'IB-5'])


def table(*rows):
    """A backlog with the given (id, risk, status, acceptance) rows, titles T<n>."""
    out = ['# Backlog', '', '| id | title | risk | status | acceptance | source |', '|---|---|---|---|---|---|']
    for id_, risk, status, acc in rows:
        out.append(f'| {id_} | T{id_[3:]} | {risk} | {status} | {acc} | s |')
    return backlog.parse('\n'.join(out) + '\n')


def ready(bl, **kw):
    ids, waiting = backlog.eligible(bl, kw.get('tag'), kw.get('no_high', False), kw.get('done', ()))
    return ids, dict(waiting)


class DepsTest(unittest.TestCase):
    def test_marker_lists_ids_in_order(self):
        bl = table(('IB-1', 'low', 'ready', 'Do it. **Depends on:** IB-3, IB-2; then more.'))
        self.assertEqual(backlog.deps(bl.rows()[0], 'IB'), ['IB-3', 'IB-2'])

    def test_prose_and_other_ids_are_not_dependencies(self):
        bl = table(('IB-1', 'low', 'ready', 'Needs IB-2 first, and depends on IB-3 being merged.'))
        self.assertEqual(backlog.deps(bl.rows()[0], 'IB'), [])

    def test_two_markers_is_a_problem(self):
        bl = table(('IB-1', 'low', 'ready', '**Depends on:** IB-2 and **Depends on:** IB-3'), ('IB-2', 'low', 'done (#1)', 'x'),
                   ('IB-3', 'low', 'done (#2)', 'x'))
        self.assertEqual(backlog.dep_problems(bl), ['IB-1: two Depends on markers'])

    def test_a_marker_with_no_valid_id_is_a_problem_not_a_silent_drop(self):
        bl = table(('IB-1', 'low', 'ready', '**Depends on:** NEW-1'))
        self.assertEqual(backlog.deps(bl.rows()[0], 'IB'), [])
        self.assertEqual(backlog.dep_problems(bl), ['IB-1: Depends on names no valid ids'])
        ids, waiting = ready(bl)
        self.assertEqual((ids, waiting), ([], {'IB-1': ['Depends on names no valid ids']}))

    def test_problems_missing_self_cancelled_cycle(self):
        bl = table(('IB-1', 'low', 'ready', '**Depends on:** IB-9'),
                   ('IB-2', 'low', 'ready', '**Depends on:** IB-2'),
                   ('IB-3', 'low', 'ready', '**Depends on:** IB-4'),
                   ('IB-4', 'low', 'cancelled — nope', 'x'),
                   ('IB-5', 'low', 'ready', '**Depends on:** IB-6'),
                   ('IB-6', 'low', 'ready', '**Depends on:** IB-7'),
                   ('IB-7', 'low', 'ready', '**Depends on:** IB-5'))
        self.assertEqual(backlog.dep_problems(bl), [
            'IB-1: depends on IB-9, which is not in the backlog', 'IB-2: depends on itself',
            'IB-3: depends on IB-4, which is cancelled',
            'IB-5: on a dependency cycle', 'IB-6: on a dependency cycle', 'IB-7: on a dependency cycle'])


class EligibleTest(unittest.TestCase):
    def test_no_markers_is_todays_ready_order(self):
        bl = table(('IB-1', 'low', 'done (#1)', 'x'), ('IB-2', 'low', 'ready', 'x'), ('IB-3', 'high', 'ready', 'x'))
        self.assertEqual(ready(bl)[0], ['IB-2', 'IB-3'])
        self.assertEqual([bl.lines[i].id for i in bl.ready_indexes()], ['IB-2', 'IB-3'])

    def test_a_row_waits_for_an_open_dependency(self):
        bl = table(('IB-1', 'low', 'ready', '**Depends on:** IB-2'), ('IB-2', 'low', 'ready', 'x'))
        ids, waiting = ready(bl)
        self.assertEqual(ids, ['IB-2'])
        self.assertEqual(waiting, {'IB-1': ['IB-2 is not done']})

    def test_done_or_passed_done_satisfies(self):
        bl = table(('IB-1', 'low', 'ready', '**Depends on:** IB-2, IB-3'), ('IB-2', 'low', 'done (#4)', 'x'), ('IB-3', 'low', 'ready', 'x'))
        self.assertEqual(ready(bl)[0], ['IB-3'])
        self.assertEqual(ready(bl, done=('IB-3',))[0], ['IB-1', 'IB-3'])

    def test_every_non_done_status_waits(self):
        for status in ('ready', 'in-progress', 'deferred', 'blocked — why'):
            bl = table(('IB-1', 'low', 'ready', '**Depends on:** IB-2'), ('IB-2', 'low', status, 'x'))
            self.assertNotIn('IB-1', ready(bl)[0], status)

    def test_a_high_risk_dependency_names_the_human(self):
        bl = table(('IB-1', 'low', 'ready', '**Depends on:** IB-2'), ('IB-2', 'high', 'ready', 'x'))
        ids, waiting = ready(bl, no_high=True)
        self.assertEqual(ids, [])
        self.assertEqual(waiting['IB-1'], ['IB-2 is risk: high and waits for a human'])

    def test_problem_rows_wait_with_the_reason(self):
        bl = table(('IB-1', 'low', 'ready', '**Depends on:** IB-9'), ('IB-2', 'low', 'ready', '**Depends on:** IB-2'),
                   ('IB-3', 'low', 'ready', '**Depends on:** IB-4'), ('IB-4', 'low', 'ready', '**Depends on:** IB-3'))
        ids, waiting = ready(bl)
        self.assertEqual(ids, [])
        self.assertEqual(sorted(waiting), ['IB-1', 'IB-2', 'IB-3', 'IB-4'])
        self.assertIn('IB-9 is not in the backlog', waiting['IB-1'])
        self.assertIn('depends on itself', waiting['IB-2'])
        self.assertIn('on a dependency cycle', waiting['IB-3'])

    def test_tag_and_no_high_filter_candidates(self):
        bl = backlog.parse('| IB-1 | [e2e] A | low | ready | x | s |\n| IB-2 | B | low | ready | x | s |\n| IB-3 | [e2e] C | high | ready | x | s |\n')
        self.assertEqual(ready(bl, tag='e2e')[0], ['IB-1', 'IB-3'])
        self.assertEqual(ready(bl, tag='e2e', no_high=True)[0], ['IB-1'])

    def test_an_unparsed_line_is_not_a_dependency(self):
        bl = backlog.parse('| IB-1 | A | low | ready | **Depends on:** IB-2 | s |\n| IB-2 | broken | low | ready |\n')
        ids, waiting = ready(bl)
        self.assertEqual(ids, [])
        self.assertIn('IB-2 is not in the backlog', waiting['IB-1'])


if __name__ == '__main__':
    unittest.main()
