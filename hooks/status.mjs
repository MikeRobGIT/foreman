// foreman wave status: while this session has a /foreman run marker, a band above the prompt shows one line per
// batch from scripts/batches.py (status, and for a batch with a PR its CI, bots, unresolved threads and merge state),
// or, with no batch files, one line per worker worktree (<main>/.claude/worktrees/*). The harness prefixes the
// plugin's status line, so neither the summary nor the error note carries a `foreman:` prefix.
// Like every mod it runs in-process and unsandboxed; it only reads (git, gh, foreman's state dir) and draws.

const EVERY_MS = 60_000
const RUN_TTL_S = 48 * 3600
let view = null // the last good {kind, rows}; null draws nothing
let note = '' // "status unavailable: <reason>" after a failed refresh

const PASS = new Set(['SUCCESS', 'NEUTRAL', 'SKIPPED'])
const FAIL = new Set(['FAILURE', 'CANCELLED', 'TIMED_OUT', 'ACTION_REQUIRED', 'STARTUP_FAILURE', 'ERROR'])

// ---- pure helpers (tests/test_status.mjs) -------------------------------------------------------------

export function parseWorktrees(porcelain) {
  const out = []
  for (const line of porcelain.split('\n')) {
    if (line.startsWith('worktree ')) out.push({ path: line.slice(9), branch: null })
    else if (line.startsWith('branch ') && out.length) out[out.length - 1].branch = line.slice(7).replace(/^refs\/heads\//, '')
  }
  return out
}

export function ciCell(checks, required) {
  const state = (c) => {
    const v = (c.conclusion || c.state || '').toUpperCase()
    return PASS.has(v) ? 'pass' : FAIL.has(v) ? 'fail' : 'pending'
  }
  const named = (c) => c.name || c.context || ''
  const relevant = required.length ? required.map((n) => checks.find((c) => named(c) === n) || {}) : checks
  if (!relevant.length) return '—'
  const s = relevant.map(state)
  const fail = s.filter((x) => x === 'fail').length
  const pending = s.filter((x) => x === 'pending').length
  return fail ? `✗${fail}` : pending ? `…${pending}` : '✓'
}

export function toRow(worker, extra, required, bots) {
  const pr = worker.pr
  const seen = bots.filter((b) => (extra?.authors || []).some((a) => a === b || a === `${b}[bot]`)).length
  return {
    name: worker.name,
    pr: pr ? `#${pr.number}` : '—',
    state: pr ? String(pr.state).toLowerCase() : 'no PR',
    sha: pr ? String(pr.headRefOid || '').slice(0, 9) : '',
    ci: pr ? ciCell(pr.statusCheckRollup || [], required) : '—',
    bots: bots.length ? `${seen}/${bots.length}` : '—',
    threads: extra ? extra.threads : 0,
    merge: pr ? String(pr.mergeStateStatus || '').toLowerCase() : '',
  }
}

export function formatRows(list) {
  const cells = list.map((r) => [r.name, r.pr, r.state, r.sha, `ci ${r.ci}`, `bots ${r.bots}`, `threads ${r.threads}`, r.merge])
  const widths = cells[0] ? cells[0].map((_, i) => Math.max(...cells.map((c) => String(c[i]).length))) : []
  return cells.map((c) => c.map((v, i) => String(v).padEnd(widths[i])).join('  ').trimEnd())
}

export function summary(list) {
  const prs = list.filter((r) => r.pr !== '—').length
  const green = list.filter((r) => r.ci === '✓' && r.merge === 'clean').length
  return `${list.length} worker${list.length === 1 ? '' : 's'} · ${prs} PR${prs === 1 ? '' : 's'} · ${green} green`
}

export function unavailable(err) {
  return `status unavailable: ${err?.message || err}`
}

export function batchStatus(b) {
  const done = (b.rounds || []).length
  if (b.status === 'review') return `review r${done + 1}`
  if (b.status === 'passed') return `passed r${done}`
  if (b.status === 'pr') return `PR #${b.pr}`
  return b.status || 'spec'
}

export function batchRow(b, pr, extra, required, bots) {
  return { ...toRow({ name: b.batch, pr }, extra, required, bots), status: batchStatus(b), kind: b.status || 'spec' }
}

export function formatBatchRows(list) {
  const wName = Math.max(0, ...list.map((r) => r.name.length))
  const wStatus = Math.max(0, ...list.map((r) => r.status.length))
  return list.map((r) => {
    const cells = [r.name.padEnd(wName), r.status.padEnd(wStatus)]
    if (r.kind === 'pr') cells.push(r.sha, `ci ${r.ci}`, `bots ${r.bots}`, `threads ${r.threads}`, r.merge)
    return cells.join('  ').trimEnd()
  })
}

export function batchSummary(list) {
  const n = (kind) => list.filter((r) => r.kind === kind).length
  const green = list.filter((r) => r.kind === 'pr' && r.ci === '✓' && r.merge === 'clean').length
  const parts = [`${list.length} batch${list.length === 1 ? '' : 'es'}`]
  for (const [count, label] of [[n('spec'), 'spec'], [n('review'), 'in review'], [n('passed'), 'passed'],
    [n('released'), 'released'], [n('pr'), n('pr') === 1 ? 'PR' : 'PRs'], [n('parked'), 'parked'], [green, 'green']]) {
    if (count) parts.push(`${count} ${label}`)
  }
  return parts.join(' · ')
}

// ---- data (mods API) ------------------------------------------------------------------------------------

async function sh($, argv, cwd) {
  const r = await $.process.run(argv, { cwd, timeoutMs: 20_000 })
  if (r.exitCode !== 0) throw new Error(`${argv.slice(0, 3).join(' ')} exited ${r.exitCode}: ${r.stderr.trim().slice(0, 120)}`)
  return r.stdout
}

async function cfgList($, main, key) {
  const out = await sh($, ['python3', `${$.plugin.root}/scripts/cfg.py`, main, key], main)
  return out.split('\n').map((s) => s.trim()).filter(Boolean)
}

async function threads($, main, numbers) {
  const repo = JSON.parse(await sh($, ['gh', 'repo', 'view', '--json', 'owner,name'], main))
  const fields = 'reviewThreads(first:100){nodes{isResolved}} reviews(last:50){nodes{author{login}}} comments(last:50){nodes{author{login}}}'
  const q = `query($owner:String!,$name:String!){repository(owner:$owner,name:$name){${numbers.map((n) => `p${n}:pullRequest(number:${n}){${fields}}`).join(' ')}}}`
  const data = JSON.parse(await sh($, ['gh', 'api', 'graphql', '-f', `query=${q}`, '-F', `owner=${repo.owner.login}`, '-F', `name=${repo.name}`], main))
  const out = {}
  for (const n of numbers) {
    const p = data.data.repository[`p${n}`] || {}
    const logins = [...(p.reviews?.nodes || []), ...(p.comments?.nodes || [])].map((x) => x.author?.login).filter(Boolean)
    out[n] = { threads: (p.reviewThreads?.nodes || []).filter((t) => !t.isResolved).length, authors: logins }
  }
  return out
}

export async function collect($) {
  const cwd = await $.session.cwd()
  const sid = await $.session.id()
  const sd = (await sh($, ['python3', `${$.plugin.root}/scripts/state.py`, cwd], cwd)).trim()
  if (!sd) return null
  const marker = `${sd}/runs/${sid}.json`
  if (!(await $.fs.exists(marker))) return null
  const m = JSON.parse(await $.fs.read(marker))
  if (Date.now() / 1000 - Number(m.touched || 0) > RUN_TTL_S) return null
  const wts = parseWorktrees(await sh($, ['git', 'worktree', 'list', '--porcelain'], cwd))
  const main = wts[0]?.path
  if (!main) return null
  if ((await cfgList($, main, 'status_pane'))[0] === 'false') return null
  if ((await cfgList($, main, 'hooks_disabled')).includes('status_pane')) return null
  const required = await cfgList($, main, 'required_ci')
  const bots = await cfgList($, main, 'review_bots')
  let list = null
  try {
    list = JSON.parse(await sh($, ['python3', `${$.plugin.root}/scripts/batches.py`, 'show', '--json', '--session', sid], main))
  } catch {
    list = null // no batch state (or batches.py failed): the worktree scan below
  }
  if (Array.isArray(list) && list.length) {
    const prs = {}
    for (const b of list.filter((x) => x.pr)) {
      prs[b.pr] = JSON.parse(await sh($, ['gh', 'pr', 'view', String(b.pr), '--json', 'number,state,headRefOid,mergeStateStatus,statusCheckRollup'], main))
    }
    const numbers = Object.keys(prs).map(Number)
    const extra = numbers.length ? await threads($, main, numbers) : {}
    return { kind: 'batches', rows: list.map((b) => batchRow(b, b.pr ? prs[b.pr] : null, b.pr ? extra[b.pr] : null, required, bots)) }
  }
  const workers = []
  for (const w of wts.filter((x) => x.path.startsWith(`${main}/.claude/worktrees/`))) {
    const prs = w.branch
      ? JSON.parse(await sh($, ['gh', 'pr', 'list', '--head', w.branch, '--state', 'all', '--json', 'number,state,headRefOid,mergeStateStatus,statusCheckRollup'], main))
      : []
    workers.push({ name: w.path.slice(main.length + '/.claude/worktrees/'.length), pr: prs[0] || null })
  }
  const numbers = workers.filter((w) => w.pr).map((w) => w.pr.number)
  const extra = numbers.length ? await threads($, main, numbers) : {}
  return { kind: 'workers', rows: workers.map((w) => toRow(w, w.pr ? extra[w.pr.number] : null, required, bots)) }
}

export function lines(v) {
  return v.kind === 'batches' ? formatBatchRows(v.rows) : formatRows(v.rows)
}

export function statusText(v) {
  return v.kind === 'batches' ? batchSummary(v.rows) : summary(v.rows)
}

async function refresh($) {
  try {
    view = await collect($)
    note = ''
  } catch (err) {
    note = unavailable(err)
  }
  $.ui.status(note || (view ? statusText(view) : undefined))
  $.ui.invalidate('ui.render')
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    refresh($)
    $.clock.every(EVERY_MS, () => {
      refresh($)
    })
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (!view || !view.rows.length) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    const shown = lines(view).concat(note ? [note] : [])
    return Box({
      flexDirection: 'column',
      children: [...shown.map((line) => Text({ children: [line], wrap: 'truncate-end' })), await next(e)],
    })
  })
}
