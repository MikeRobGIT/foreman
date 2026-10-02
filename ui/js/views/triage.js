import { state, set, rows, triageList } from '../state.js';
import { esc, inline, splitNotes, ago } from '../md.js';
import * as A from '../actions.js';

const list = () => triageList(rows(), state.triage.days);

export function render(root) {
  const l = list();
  const i = Math.min(state.triage.index, Math.max(l.length - 1, 0));
  const r = l[i];
  root.innerHTML = `<div class="wrap">
    <div class="prog"><span>Triage · ready rows untouched for
      <select id="days" aria-label="Days untouched">${[7, 14, 30, 60, 90].map((d) => `<option ${d === state.triage.days ? 'selected' : ''}>${d}</option>`).join('')}</select> days or more, oldest first</span>
      <span><b>${l.length ? i + 1 : 0}</b> of ${l.length}</span></div>
    <div class="meter"><i style="width:${l.length ? ((i + 1) / l.length) * 100 : 0}%"></i></div>
    ${r ? `<div class="card">
      <div class="crumb">${esc(r.id)} · queue position ${r.position} · last touched ${['today', 'unknown'].includes(ago(r.touched)) ? ago(r.touched) : ago(r.touched) + ' ago'}</div>
      <h1>${inline(r.title)}</h1>
      <div class="chips"><span class="risk ${esc(r.risk)}">${esc(r.risk)}</span><span class="st ready">ready</span><span>· from ${inline(r.source)}</span></div>
      <div class="notes">${splitNotes(r.acceptance).map((b) => `<div class="block"><div class="lbl">${esc(b.label)}</div>${b.html}</div>`).join('')}</div>
      <div class="keygrid">
        <button data-k="k"><kbd>k</kbd>Keep</button><button data-k="t"><kbd>t</kbd>Move to top</button><button data-k="ArrowDown"><kbd>↓</kbd>Move to bottom</button><button data-k="e"><kbd>e</kbd>Edit…</button>
        <button data-k="d"><kbd>d</kbd>Defer</button><button data-k="b"><kbd>b</kbd>Block…</button><button data-k="x" class="warn"><kbd>x</kbd>Cancel…</button><button data-k="u"><kbd>u</kbd>Undo last</button>
      </div></div>` : '<p class="hint">Nothing to triage with this filter.</p>'}
  </div>`;
  root.querySelector('#days').onchange = (e) => set({ triage: { days: Number(e.target.value), index: 0 } });
  root.querySelectorAll('[data-k]').forEach((b) => { b.onclick = () => decide(b.dataset.k); });
}

async function decide(key) {
  if (state.busy) return;
  const l = list();
  const r = l[Math.min(state.triage.index, l.length - 1)];
  if (!r) return;
  const next = () => set({ triage: { ...state.triage, index: state.triage.index + 1 } });
  if (key === 'k') return next();
  if (key === 'u') return A.undo();
  if (key === 'e') return set({ view: 'edit', editing: { mode: 'edit', id: r.id } });
  if (key === 't') { if (await A.moveTop(r.id)) return next(); return; }
  if (key === 'ArrowDown') { if (await A.moveBottom(r.id)) return next(); return; }
  const to = { d: 'deferred', b: 'blocked', x: 'cancelled' }[key];
  if (to && (await A.setStatus(r.id, to))) set({ triage: { ...state.triage } });
}

export function onKey(e) {
  if (['k', 't', 'ArrowDown', 'e', 'd', 'b', 'x', 'u'].includes(e.key)) { decide(e.key); return true; }
  return false;
}
