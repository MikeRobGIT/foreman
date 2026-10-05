import test from 'node:test'
import assert from 'node:assert/strict'
import { parseWorktrees, ciCell, toRow, formatRows, summary, collect, unavailable, batchStatus, batchRow, formatBatchRows, batchSummary, lines, statusText } from '../hooks/status.mjs'

const PR = {
  number: 12,
  state: 'OPEN',
  headRefOid: '0123456789abcdef',
  mergeStateStatus: 'CLEAN',
  statusCheckRollup: [
    { __typename: 'CheckRun', name: 'test', status: 'COMPLETED', conclusion: 'SUCCESS' },
    { __typename: 'StatusContext', context: 'lint', state: 'PENDING' },
  ],
}

test('parseWorktrees reads paths and branches', () => {
  const p = 'worktree /r\nHEAD abc\nbranch refs/heads/main\n\nworktree /r/.claude/worktrees/w1\nHEAD def\nbranch refs/heads/feat\n\nworktree /x\nHEAD 1\ndetached\n'
  assert.deepEqual(parseWorktrees(p), [
    { path: '/r', branch: 'main' },
    { path: '/r/.claude/worktrees/w1', branch: 'feat' },
    { path: '/x', branch: null },
  ])
})

test('ciCell: required checks only, failures first, a missing check is pending', () => {
  assert.equal(ciCell(PR.statusCheckRollup, []), '…1')
  assert.equal(ciCell(PR.statusCheckRollup, ['test']), '✓')
  assert.equal(ciCell(PR.statusCheckRollup, ['test', 'e2e']), '…1')
  assert.equal(ciCell([{ name: 'test', conclusion: 'FAILURE' }, { name: 'x', state: 'PENDING' }], []), '✗1')
  assert.equal(ciCell([], []), '—')
})

test('toRow: 9-character SHA, bots seen out of configured, unresolved threads', () => {
  const r = toRow({ name: 'w1', pr: PR }, { threads: 2, authors: ['claude[bot]', 'me'] }, ['test'], ['claude', 'codex'])
  assert.deepEqual(r, { name: 'w1', pr: '#12', state: 'open', sha: '012345678', ci: '✓', bots: '1/2', threads: 2, merge: 'clean' })
  assert.deepEqual(toRow({ name: 'w2', pr: null }, null, [], []), { name: 'w2', pr: '—', state: 'no PR', sha: '', ci: '—', bots: '—', threads: 0, merge: '' })
})

test('formatRows aligns the columns; summary counts PRs and green ones', () => {
  const rows = [
    toRow({ name: 'w1', pr: PR }, { threads: 0, authors: [] }, ['test'], []),
    toRow({ name: 'worker-two', pr: null }, null, [], []),
  ]
  const lines = formatRows(rows)
  assert.equal(lines.length, 2)
  assert.ok(lines[0].startsWith('w1          #12  open'))
  assert.ok(lines[1].startsWith('worker-two  —    no PR'))
  assert.equal(lines[0].indexOf('ci '), lines[1].indexOf('ci '))
  assert.equal(summary(rows), '2 workers · 1 PR · 1 green')
  assert.deepEqual(formatRows([]), [])
})

function fake(stateOut, exitCode = 0) {
  const calls = []
  const $ = {
    plugin: { root: '/plugin' },
    session: { cwd: async () => '/not-a-repo', id: async () => 'sid' },
    process: { run: async (argv) => { calls.push(argv); return { exitCode, stdout: stateOut, stderr: exitCode ? 'boom' : '' } } },
    fs: { exists: async () => { calls.push('exists'); return false }, read: async () => '' },
  }
  return { $, calls }
}

test('collect: no repo (state.py prints nothing) shows nothing and stops there', async () => {
  const { $, calls } = fake('')
  assert.equal(await collect($), null)
  assert.equal(calls.length, 1)
  assert.match(calls[0][1], /state\.py$/)
})

test('collect: a failing state.py rejects, so refresh reports unavailable', async () => {
  const { $ } = fake('', 2)
  await assert.rejects(collect($), /exited 2: boom/)
})

test('no foreman: prefix in the summary or the error note (the harness prefixes the status line)', () => {
  assert.equal(unavailable(new Error('gh exited 1')), 'status unavailable: gh exited 1')
  assert.ok(!summary([]).includes('foreman'))
})

const BATCHES = [
  { batch: '20261004-0930-w1a', status: 'pr', pr: 12, rounds: [{}, {}] },
  { batch: '20261004-0930-w1b', status: 'review', rounds: [{}] },
  { batch: '20261004-0930-w1c', status: 'parked', reason: 'no-pass', rounds: [{}, {}, {}] },
  { batch: '20261004-0930-w1d', status: 'passed', rounds: [{}] },
]

test('batch rows: status with round or PR; the PR cells only for a batch with a PR', () => {
  assert.deepEqual(BATCHES.map(batchStatus), ['PR #12', 'review r2', 'parked', 'passed r1'])
  const rows = BATCHES.map((b) => batchRow(b, b.pr ? PR : null, b.pr ? { threads: 1, authors: ['claude[bot]'] } : null, ['test'], ['claude']))
  assert.deepEqual(formatBatchRows(rows), [
    '20261004-0930-w1a  PR #12     012345678  ci ✓  bots 1/1  threads 1  clean',
    '20261004-0930-w1b  review r2',
    '20261004-0930-w1c  parked',
    '20261004-0930-w1d  passed r1',
  ])
  assert.equal(batchSummary(rows), '4 batches · 1 in review · 1 passed · 1 PR · 1 parked · 1 green')
  assert.equal(batchSummary([batchRow({ batch: 'x', status: 'spec' }, null, null, [], [])]), '1 batch · 1 spec')
})

function routed(routes) {
  const calls = []
  const $ = {
    plugin: { root: '/plugin' },
    session: { cwd: async () => '/r', id: async () => 'S1' },
    process: {
      run: async (argv) => {
        calls.push(argv.join(' '))
        for (const [re, out] of routes) if (re.test(argv.join(' '))) return typeof out === 'string' ? { exitCode: 0, stdout: out, stderr: '' } : out
        return { exitCode: 0, stdout: '', stderr: '' }
      },
    },
    fs: { exists: async () => true, read: async () => JSON.stringify({ touched: Date.now() / 1000 }) },
  }
  return { $, calls }
}

const WORLD = [
  [/state\.py/, '/sd\n'],
  [/^git worktree list/, 'worktree /r\nbranch refs/heads/main\n\nworktree /r/.claude/worktrees/w1\nbranch refs/heads/feat\n'],
  [/cfg\.py \/r required_ci/, 'test\n'],
  [/^gh pr view 12/, JSON.stringify(PR)],
  [/^gh pr list/, '[]'],
  [/^gh repo view/, '{"owner":{"login":"o"},"name":"n"}'],
  [/^gh api graphql/, JSON.stringify({ data: { repository: { p12: { reviewThreads: { nodes: [{ isResolved: false }] }, reviews: { nodes: [] }, comments: { nodes: [] } } } } })],
]

test('collect: with batch files, one row per batch and the batch summary', async () => {
  const { $, calls } = routed([[/batches\.py show --json --session S1/, JSON.stringify(BATCHES)], ...WORLD])
  const v = await collect($)
  assert.equal(v.kind, 'batches')
  assert.deepEqual(v.rows.map((r) => r.name), BATCHES.map((b) => b.batch))
  assert.equal(lines(v)[0], '20261004-0930-w1a  PR #12     012345678  ci ✓  bots —  threads 1  clean')
  assert.equal(statusText(v), '4 batches · 1 in review · 1 passed · 1 PR · 1 parked · 1 green')
  assert.ok(!calls.some((c) => c.startsWith('gh pr list')))
})

test('collect: no batch files, or a failing batches.py, falls back to the worktree scan', async () => {
  for (const out of ['[]', { exitCode: 1, stdout: '', stderr: 'corrupt batch state' }]) {
    const { $ } = routed([[/batches\.py show/, out], ...WORLD])
    const v = await collect($)
    assert.equal(v.kind, 'workers')
    assert.deepEqual(v.rows.map((r) => r.name), ['w1'])
    assert.equal(statusText(v), '1 worker · 0 PRs · 0 green')
  }
})
