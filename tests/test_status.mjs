import test from 'node:test'
import assert from 'node:assert/strict'
import { parseWorktrees, ciCell, toRow, formatRows, summary, collect } from '../hooks/status.mjs'

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
  assert.equal(summary(rows), 'foreman: 2 workers · 1 PR · 1 green')
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
