import { state, set, readyQueue, byId } from '../state.js';
import { esc, inline, splitNotes, ago } from '../md.js';
import * as A from '../actions.js';

export function detailHtml(r, positionText) {
  const blocks = splitNotes(r.acceptance).map((b) => `<div class="block"><div class="lbl">${esc(b.label)}</div>${b.html}</div>`).join('');
  const opt = r.status === 'ready' && r.risk === 'high' ? '<span class="optin">opt-in: the loop skips it</span>' : '';
  return `<div class="crumb">${esc(r.id)}${positionText ? ` · ${positionText}` : ''} · last touched ${ago(r.touched)} ago</div>
    <h1>${inline(r.title)}</h1>
    <div class="chips"><span class="risk ${esc(r.risk)}">${esc(r.risk)}</span><span class="st ${esc(r.status)}">${esc(r.status_text)}</span>${opt}<span>· from ${inline(r.source)}</span></div>
    <div class="actions">
      <button class="primary" data-act="edit">Edit <kbd>e</kbd></button>
      ${r.status === 'ready' ? '<button data-act="top">Move to top <kbd>t</kbd></button>' : '<button data-act="ready">Make ready</button>'}
      <button data-act="deferred">Defer <kbd>d</kbd></button>
      <button data-act="blocked">Block… <kbd>b</kbd></button>
      <button data-act="cancelled">Cancel… <kbd>x</kbd></button>
    </div>
    <div class="notes">${blocks}</div>`;
}

export function bindDetail(root, r) {
  root.querySelectorAll('[data-act]').forEach((b) => {
    b.onclick = () => act(b.dataset.act, r);
  });
}

function act(what, r) {
  if (what === 'edit') return set({ view: 'edit', editing: { mode: 'edit', id: r.id } });
  if (what === 'top') return A.moveTop(r.id);
  return A.setStatus(r.id, what);
}

function selected() {
  const q = readyQueue();
  return byId(state.selectedId) && byId(state.selectedId).status === 'ready' ? byId(state.selectedId) : q[0];
}

export function render(root) {
  const q = readyQueue();
  const sel = selected();
  root.innerHTML = `<div class="main">
    <div class="queue" id="queue" role="listbox" aria-label="Ready queue">
      <div class="qhead"><h2>Ready queue</h2><span>${q.length} rows · the loop takes the top first · drag or <kbd>⌥↑</kbd><kbd>⌥↓</kbd></span></div>
      ${q.map((r) => `<div class="row${sel && r.id === sel.id ? ' sel' : ''}" role="option" aria-selected="${sel && r.id === sel.id}" draggable="true" data-id="${esc(r.id)}" tabindex="-1">
        <span class="grip" aria-hidden="true">⠿</span><span class="pos">${r.position}</span>
        <div><div class="t"><span class="id">${esc(r.id)}</span>${inline(r.title)}${r.pending ? '<span class="edited">edited</span>' : ''}${r.risk === 'high' ? '<span class="optin">opt-in</span>' : ''}</div><div class="meta">${inline(r.source)}</div></div>
        <span class="risk ${esc(r.risk)}">${esc(r.risk)}</span></div>`).join('') || '<p class="wrap hint">No ready rows.</p>'}
    </div>
    <article class="detail" id="detail">${sel ? detailHtml(sel, `position ${sel.position} of ${q.length}`) : ''}</article></div>`;
  if (sel) bindDetail(root.querySelector('#detail'), sel);
  const list = root.querySelector('#queue');
  list.querySelectorAll('.row').forEach((el) => {
    el.onclick = () => set({ selectedId: el.dataset.id });
    el.ondragstart = (e) => { e.dataTransfer.setData('text/plain', el.dataset.id); };
    el.ondragover = (e) => {
      e.preventDefault();
      const after = e.offsetY > el.offsetHeight / 2;
      el.classList.toggle('drop-after', after);
      el.classList.toggle('drop-before', !after);
    };
    el.ondragleave = () => el.classList.remove('drop-after', 'drop-before');
    el.ondrop = (e) => {
      e.preventDefault();
      const id = e.dataTransfer.getData('text/plain');
      const after = el.classList.contains('drop-after');
      el.classList.remove('drop-after', 'drop-before');
      if (id && id !== el.dataset.id) A.moveRel(id, after ? 'after' : 'before', el.dataset.id);
    };
  });
  list.querySelector('.row.sel')?.scrollIntoView({ block: 'nearest' });
}

export function onKey(e) {
  const q = readyQueue();
  const sel = selected();
  if (!sel) return false;
  const i = q.findIndex((r) => r.id === sel.id);
  if (e.altKey && e.key === 'ArrowUp' && i > 0) { A.moveRel(sel.id, 'before', q[i - 1].id); return true; }
  if (e.altKey && e.key === 'ArrowDown' && i < q.length - 1) { A.moveRel(sel.id, 'after', q[i + 1].id); return true; }
  if (e.key === 'j' && i < q.length - 1) { set({ selectedId: q[i + 1].id }); return true; }
  if (e.key === 'k' && i > 0) { set({ selectedId: q[i - 1].id }); return true; }
  const map = { e: 'edit', t: 'top', d: 'deferred', b: 'blocked', x: 'cancelled' };
  if (map[e.key]) { act(map[e.key], sel); return true; }
  return false;
}
