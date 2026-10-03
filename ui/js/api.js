// ui/js/api.js
// The token arrives once in the URL fragment (#t=…), lives in this tab's sessionStorage, and goes out as a header.
const KEY = `foreman-token-${location.port}`;
let token = new URLSearchParams(location.hash.slice(1)).get('t') || '';
if (token) {
  try { sessionStorage.setItem(KEY, token); } catch (e) { /* storage blocked: keep it in memory */ }
  history.replaceState(null, '', location.pathname + location.search);
} else {
  try { token = sessionStorage.getItem(KEY) || ''; } catch (e) { /* storage blocked */ }
}

async function call(method, path, body) {
  if (!token) throw new Error('Open the URL printed by /foreman ui');
  const res = await fetch(path, {
    method,
    headers: { 'X-Foreman-Token': token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${method} ${path} failed (${res.status})`);
  return data;
}
export const api = {
  backlog: (refresh) => call('GET', `/api/backlog${refresh ? '?refresh=1' : ''}`),
  addOp: (op) => call('POST', '/api/ops', op),
  undo: () => call('POST', '/api/ops/undo'),
  drop: (i) => call('DELETE', `/api/ops/${i}`),
  rebase: (i) => call('POST', `/api/ops/${i}/rebase`),
  progress: () => call('GET', '/api/progress'),
  publish: () => call('POST', '/api/publish'),
};
