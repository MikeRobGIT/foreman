import { state, set, rows, byId, filterRows } from '../state.js';
import { esc, inline, prOf, tags } from '../md.js';
import { detailHtml, bindDetail } from './queue.js';

const STATUSES = ['all', 'ready', 'deferred', 'blocked', 'done', 'cancelled', 'other'];

export function render(root) {
  const f = state.filters;
  const all = rows();
  const count = (s) => (s === 'all' ? all.length : filterRows(all, { q: '', status: s, risk: 'any', tag: '', pr: '' }).length);
  const list = filterRows(all, f);
  const sel = byId(state.selectedId);
  const allTags = [...new Set(all.flatMap((r) => tags(r.title)))].sort();
  const unparsed = state.data.rows.filter((r) => r.unparsed);
  root.innerHTML = `<div class="toolbar">
      <input type="search" id="q" placeholder="Search ids, titles, notes, file paths…" value="${esc(f.q)}" aria-label="Search">
      <div class="seg" role="group" aria-label="Status">${STATUSES.map((s) => `<button data-status="${s}" aria-pressed="${f.status === s}">${s[0].toUpperCase() + s.slice(1)} ${count(s)}</button>`).join('')}</div>
      <div class="seg" role="group" aria-label="Risk">${['any', 'low', 'medium', 'high'].map((k) => `<button data-risk="${k}" aria-pressed="${f.risk === k}">${k === 'any' ? 'Any risk' : k}</button>`).join('')}</div>
      <select id="tag" aria-label="Tag"><option value="">Any tag</option>${allTags.map((t) => `<option ${f.tag === t ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>
      <input id="pr" placeholder="PR #" value="${esc(f.pr)}" size="6" aria-label="Filed from or done in PR">
    </div>
    ${unparsed.length ? `<p class="banner">${unparsed.length} line(s) look like rows but have the wrong number of cells; they are shown as-is and never rewritten: ${unparsed.map((u) => `<code>${esc(u.unparsed.slice(0, 40))}</code>`).join(' ')}</p>` : ''}
    <div class="split"><div style="overflow:auto;max-height:calc(100vh - 230px)"><table class="rows"><thead><tr><th>Id</th><th>Title</th><th>Risk</th><th>Status</th><th>PR</th></tr></thead><tbody>
    ${list.slice(0, 400).map((r) => `<tr data-id="${esc(r.id)}" class="${sel && sel.id === r.id ? 'sel' : ''}"><td class="id">${esc(r.id)}</td><td>${inline(r.title)}${r.pending ? '<span class="edited">edited</span>' : ''}</td><td><span class="risk ${esc(r.risk)}">${esc(r.risk)}</span></td><td><span class="st ${esc(r.status)}">${esc(r.status)}</span></td><td class="pr">${prOf(r.status_text) ? '#' + prOf(r.status_text) : ''}</td></tr>`).join('')}
    </tbody></table>${list.length > 400 ? `<p class="hint wrap">Showing 400 of ${list.length}; narrow the search.</p>` : ''}</div>
    <aside class="drawer" id="drawer">${sel ? detailHtml(sel, sel.position ? `queue position ${sel.position}` : '') : '<p class="hint">Select a row to read it.</p>'}</aside></div>`;
  if (sel) bindDetail(root.querySelector('#drawer'), sel);
  const q = root.querySelector('#q');
  q.oninput = () => { state.filters.q = q.value; renderKeepFocus(root, 'q'); };
  root.querySelector('#pr').oninput = (e) => { state.filters.pr = e.target.value.replace(/\D/g, ''); renderKeepFocus(root, 'pr'); };
  root.querySelector('#tag').onchange = (e) => set({ filters: { ...f, tag: e.target.value } });
  root.querySelectorAll('[data-status]').forEach((b) => { b.onclick = () => set({ filters: { ...f, status: b.dataset.status } }); });
  root.querySelectorAll('[data-risk]').forEach((b) => { b.onclick = () => set({ filters: { ...f, risk: b.dataset.risk } }); });
  root.querySelectorAll('tbody tr').forEach((tr) => { tr.onclick = () => set({ selectedId: tr.dataset.id }); });
}

function renderKeepFocus(root, id) {
  const el = document.getElementById(id);
  const pos = el.selectionStart;
  render(root);
  const again = document.getElementById(id);
  again.focus();
  again.setSelectionRange(pos, pos);
}

export function onKey(e) {
  const list = filterRows(rows(), state.filters);
  const i = list.findIndex((r) => r.id === state.selectedId);
  if (e.key === 'j' && i < list.length - 1) { set({ selectedId: list[i + 1].id }); return true; }
  if (e.key === 'k' && i > 0) { set({ selectedId: list[i - 1].id }); return true; }
  if (e.key === '/') { document.getElementById('q').focus(); return true; }
  return false;
}
