// ui/js/state.js: the store and pure selectors.
const listeners = new Set();

export const state = {
  view: 'queue', data: null, selectedId: null, error: '', busy: false, progress: null, published: null,
  filters: { q: '', status: 'ready', risk: 'any', tag: '', pr: '' },
  triage: { days: 14, index: 0 }, editing: null,
};

export function set(patch) {
  Object.assign(state, patch);
  listeners.forEach((fn) => fn(state));
}
export const subscribe = (fn) => listeners.add(fn);

export const rows = () => (state.data ? state.data.rows.filter((r) => !r.unparsed) : []);
export const readyQueue = () => rows().filter((r) => r.status === 'ready');
export const byId = (id) => rows().find((r) => r.id === id);

export function filterRows(list, f) {
  const q = f.q.trim().toLowerCase();
  const prValid = !f.pr || /^\d+$/.test(f.pr);
  return list.filter((r) =>
    (f.status === 'all' || (f.status === 'other' ? !['ready', 'deferred', 'blocked', 'done', 'cancelled'].includes(r.status) : r.status === f.status)) &&
    (f.risk === 'any' || r.risk === f.risk) &&
    (!f.tag || (r.title.match(/\[([a-z0-9-]+)\]/gi) || []).map((t) => t.slice(1, -1).toLowerCase()).includes(f.tag.toLowerCase())) &&
    (prValid && (!f.pr || new RegExp(`#${f.pr}\\b`).test(`${r.status_text} ${r.source}`))) &&
    (!q || `${r.id} ${r.title} ${r.acceptance} ${r.source}`.toLowerCase().includes(q)));
}

export function triageList(list, days, now = Date.now()) {
  const cutoff = now / 1000 - days * 86400;
  return list.filter((r) => r.status === 'ready' && (!r.touched || r.touched < cutoff))
    .sort((a, b) => (a.touched || 0) - (b.touched || 0));
}
