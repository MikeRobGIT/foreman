// ui/js/actions.js
import { api } from './api.js';
import { set } from './state.js';
import { ask } from './dialogs.js';

async function run(fn) {
  set({ busy: true });
  try {
    const data = await fn();
    if (data && data.rows) set({ data, error: '' });
    return data;
  } catch (e) {
    set({ error: e.message });
    return null;
  } finally {
    set({ busy: false });
  }
}

export const load = (refresh = false) => run(() => api.backlog(refresh));
export const addOp = (op) => run(() => api.addOp(op));
export const undo = () => run(() => api.undo());
export const drop = (i) => run(() => api.drop(i));
export const rebase = (i) => run(() => api.rebase(i));
export const moveTop = (id) => addOp({ op: 'move', id, to: 'top' });
export const moveBottom = (id) => addOp({ op: 'move', id, to: 'bottom' });
export const moveRel = (id, where, anchor) => addOp({ op: 'move', id, to: { [where]: anchor } });

export async function setStatus(id, to) {
  let reason = '';
  if (to === 'blocked' || to === 'cancelled' || to === 'deferred') {
    reason = await ask(`${to === 'deferred' ? 'Defer' : to === 'blocked' ? 'Block' : 'Cancel'} ${id}`,
      to === 'deferred' ? 'Why (optional)' : 'Why (required)');
    if (reason === null) return null;
    if (to !== 'deferred' && !reason.trim()) { set({ error: `${to} needs a reason` }); return null; }
  }
  return addOp({ op: 'status', id, to, reason });
}

export async function publish() {
  set({ busy: true });
  try {
    const res = await api.publish();
    set({ published: res, error: '' });
    await load(true);
    return res;
  } catch (e) {
    set({ error: e.message });
    return null;
  } finally {
    set({ busy: false });
  }
}
