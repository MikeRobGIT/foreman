import { state, set } from '../state.js';
import { esc, inline } from '../md.js';
import * as A from '../actions.js';

function describe(o) {
  if (o.op === 'add') return `<b>New row</b> "${inline(o.title)}" <span class="hint">→ numbered at publish · ${esc(o.insert)} of the queue</span>`;
  if (o.op === 'move') return `<b>${esc(o.id)}</b> moved to ${typeof o.to === 'string' ? esc(o.to) : `${esc(Object.keys(o.to)[0])} ${esc(Object.values(o.to)[0])}`}`;
  if (o.op === 'status') return `<b>${esc(o.id)}</b> status → ${esc(o.to)}${o.reason ? ` — ${esc(o.reason)}` : ''}`;
  return `<b>${esc(o.id)}</b> ${esc(o.field)} → <span class="hint">${inline(o.value.slice(0, 160))}</span>`;
}

export function render(root) {
  const { ops, results, sha, fetched_at: at, branch } = state.data;
  const applied = results.filter((r) => r.result === 'applied').length;
  const hasInvalid = results.some((r) => r.result === 'invalid');
  const pub = state.published;
  root.innerHTML = `<div class="wrap">
    <h1 style="font-family:var(--display);font-size:26px;margin:0 0 6px">Publish ${ops.length} change${ops.length === 1 ? '' : 's'}</h1>
    <p class="hint" style="margin:0 0 18px">Replayed onto origin/${esc(branch)} <code>${esc(sha.slice(0, 9))}</code> (fetched ${new Date(at * 1000).toLocaleTimeString()}) in a fresh worktree, then committed and opened as one PR. Your checkout is not touched. <button class="btn" id="refresh">Fetch again</button></p>
    ${pub && pub.pr_url ? `<p class="banner info">Opened <a href="${esc(pub.pr_url)}" target="_blank" rel="noopener">${esc(pub.pr_url)}</a> with ${pub.applied} change${pub.applied === 1 ? '' : 's'}${pub.conflicts ? `; ${pub.conflicts} conflicting change${pub.conflicts === 1 ? ' stays' : 's stay'} below` : ''}.</p>` : ''}
    ${pub && !pub.pr_url && pub.message ? `<p class="banner">${esc(pub.message)}</p>` : ''}
    ${ops.map((o, i) => {
      const r = results[i];
      const bad = r.result !== 'applied';
      return `<div class="op${bad ? ' conflict' : ''}"><span class="k">${bad ? esc(r.result) : esc(o.op)}</span>
        <div>${describe(o)}${bad ? `<div class="bad" style="margin-top:6px">${esc(r.reason)}${r.result === 'conflict' ? ' This change will be skipped.' : ''}</div>` : ''}</div>
        <div style="display:flex;gap:6px">${bad && o.op !== 'add' && r.result === 'conflict' ? `<button class="btn" data-rebase="${i}" data-uid="${esc(o.uid)}" ${state.busy ? 'disabled' : ''}>Re-apply to new text</button>` : ''}<button class="btn" data-drop="${i}" data-uid="${esc(o.uid)}" ${state.busy ? 'disabled' : ''}>${bad ? 'Drop' : 'Undo'}</button></div></div>`;
    }).join('') || '<p class="hint">No pending changes.</p>'}
    <div style="display:flex;align-items:center;gap:14px;margin-top:18px">
      <button class="btn primary" id="publish" ${applied && !state.busy && !hasInvalid ? '' : 'disabled'}>${state.busy ? 'Publishing…' : `Publish ${applied} as PR`}</button>
      <span class="hint">Branch <code>backlog/ui-&lt;date&gt;-&lt;n&gt;</code> · commit without trailers</span>
    </div></div>`;
  root.querySelector('#refresh').onclick = () => A.load(true);
  root.querySelectorAll('[data-drop]').forEach((b) => { b.onclick = () => { if (state.busy) return; A.drop(Number(b.dataset.drop), b.dataset.uid); }; });
  root.querySelectorAll('[data-rebase]').forEach((b) => { b.onclick = () => { if (state.busy) return; A.rebase(Number(b.dataset.rebase), b.dataset.uid); }; });
  root.querySelector('#publish').onclick = () => { set({ published: null }); A.publish(); };
}
