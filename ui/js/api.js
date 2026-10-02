// ui/js/api.js
async function call(method, path, body) {
  const res = await fetch(path, {
    method, credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : {},
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
