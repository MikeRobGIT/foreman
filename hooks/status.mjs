// foreman wave status: while this session has a /foreman run marker, a band above the prompt shows one line per
// worker worktree (<main>/.claude/worktrees/*): PR, state, head SHA, CI, bots, unresolved threads, merge state.
// Like every mod it runs in-process and unsandboxed; it only reads (git, gh, foreman's state dir) and draws.

const EVERY_MS = 60_000
const RUN_TTL_S = 48 * 3600
let rows = null // the last good table; null draws nothing
let note = '' // "foreman status unavailable: <reason>" after a failed refresh

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
  return `foreman: ${list.length} worker${list.length === 1 ? '' : 's'} · ${prs} PR${prs === 1 ? '' : 's'} · ${green} green`
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
  const workers = []
  for (const w of wts.filter((x) => x.path.startsWith(`${main}/.claude/worktrees/`))) {
    const prs = w.branch
      ? JSON.parse(await sh($, ['gh', 'pr', 'list', '--head', w.branch, '--state', 'all', '--json', 'number,state,headRefOid,mergeStateStatus,statusCheckRollup'], main))
      : []
    workers.push({ name: w.path.slice(main.length + '/.claude/worktrees/'.length), pr: prs[0] || null })
  }
  const numbers = workers.filter((w) => w.pr).map((w) => w.pr.number)
  const extra = numbers.length ? await threads($, main, numbers) : {}
  return workers.map((w) => toRow(w, w.pr ? extra[w.pr.number] : null, required, bots))
}

async function refresh($) {
  try {
    rows = await collect($)
    note = ''
  } catch (err) {
    note = `foreman status unavailable: ${err?.message || err}`
  }
  $.ui.status(note || (rows ? summary(rows) : undefined))
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
    if (!rows || !rows.length) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    const lines = formatRows(rows).concat(note ? [note] : [])
    return Box({
      flexDirection: 'column',
      children: [...lines.map((line) => Text({ children: [line], wrap: 'truncate-end' })), await next(e)],
    })
  })
}
