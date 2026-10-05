// ui/js/main.js: boot, header, stats, pending bar, tabs, keyboard.
import { state, set, subscribe, rows, readyQueue } from './state.js';
import { esc } from './md.js';
import * as A from './actions.js';
import { openPalette, openHelp } from './dialogs.js';
import * as queue from './views/queue.js';
import * as rowsView from './views/rows.js';
import * as triage from './views/triage.js';
import * as progress from './views/progress.js';
import * as edit from './views/edit.js';
import * as publish from './views/publish.js';

const VIEWS = { queue, rows: rowsView, triage, progress, edit, publish };
const KEY = 'foreman-theme';

window.foremanTheme = (t) => {
  if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
  document.querySelectorAll('.theme button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.t === (t || 'auto'))));
  try { t === 'light' || t === 'dark' ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY); } catch (e) { /* storage blocked */ }
};

function renderStats() {
  const all = rows();
  const count = (s) => all.filter((r) => r.status === s).length;
  const ready = readyQueue();
  const pct = (k) => `${(ready.filter((r) => r.risk === k).length / Math.max(ready.length, 1)) * 100}%`;
  document.getElementById('stats').innerHTML = `
    <div class="stat"><div class="n">${ready.length}</div><div class="l">Ready</div><div class="bar"><i style="width:${pct('low')};background:var(--lowink)"></i><i style="width:${pct('medium')};background:var(--medink)"></i><i style="width:${pct('high')};background:var(--hiink)"></i></div></div>
    <div class="stat"><div class="n">${count('done')}</div><div class="l">Done</div></div>
    <div class="stat"><div class="n">${count('deferred')}</div><div class="l">Deferred</div></div>
    <div class="stat"><div class="n">${count('blocked')}</div><div class="l">Blocked</div></div>
    <div class="stat"><div class="n">${count('cancelled')}</div><div class="l">Cancelled</div></div>`;
}

function renderPending() {
  const el = document.getElementById('pending');
  const ops = state.data ? state.data.ops : [];
  el.hidden = ops.length === 0;
  if (!ops.length) return;
  const conflicts = state.data.results.filter((r) => r.result !== 'applied').length;
  const text = ops.map((o) => (o.op === 'add' ? `new "${o.title}"` : o.op === 'move' ? `${o.id} moved` : o.op === 'status' ? `${o.id} → ${o.to}` : `${o.id} ${o.field}`)).join(' · ');
  el.innerHTML = `<span><b>${ops.length}</b> unpublished${conflicts ? ` · <span class="bad">${conflicts} conflict${conflicts > 1 ? 's' : ''}</span>` : ''}</span>
    <span class="list">${esc(text)}</span>
    <button class="btn" id="undo-last">Undo <kbd>u</kbd></button>
    <button class="btn primary" id="go-publish">Review & publish <kbd>p</kbd></button>`;
  el.querySelector('#undo-last').onclick = () => A.undo();
  el.querySelector('#go-publish').onclick = () => set({ view: 'publish' });
}

function renderBanner() {
  const b = document.getElementById('banner');
  const msg = state.error || (state.data && state.data.notice) || '';
  b.hidden = !msg;
  b.className = state.error ? 'banner' : 'banner info';
  b.textContent = msg;
}

function render() {
  document.querySelectorAll('#tabs button').forEach((b) => b.setAttribute('aria-current', b.dataset.v === state.view ? 'page' : 'false'));
  renderBanner();
  if (!state.data) {
    document.getElementById('view').innerHTML = `<p class="wrap hint">${state.error ? '' : 'Fetching the backlog from origin…'}</p>`;
    return;
  }
  renderStats();
  renderPending();
  VIEWS[state.view].render(document.getElementById('view'));
}

subscribe(render);

document.getElementById('tabs').addEventListener('click', (e) => {
  const v = e.target.closest('button')?.dataset.v;
  if (v) set({ view: v });
});
document.getElementById('cmd').onclick = openPalette;
document.querySelectorAll('.theme button').forEach((b) => { b.onclick = () => window.foremanTheme(b.dataset.t); });
let saved = 'auto';
try { saved = localStorage.getItem(KEY) || 'auto'; } catch (e) { /* storage blocked */ }
window.foremanTheme(saved);

document.addEventListener('keydown', (e) => {
  if (document.querySelector('dialog[open]')) return;
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); return; }
  if (e.target.closest('input, textarea, select') || e.metaKey || e.ctrlKey) return;
  const view = VIEWS[state.view];
  if (view.onKey && view.onKey(e)) { e.preventDefault(); return; }
  if (e.key === '?') { openHelp(); e.preventDefault(); }
  else if (e.key === 'u') A.undo();
  else if (e.key === 'n') set({ view: 'edit', editing: { mode: 'new' } });
  else if (e.key === 'p') set({ view: 'publish' });
});

A.load(true);
