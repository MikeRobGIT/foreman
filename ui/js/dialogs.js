// ui/js/dialogs.js
import { esc } from './md.js';
import { state, set, rows, byId } from './state.js';
import * as A from './actions.js';

export function ask(title, placeholder) {
  const d = document.getElementById('ask');
  d.querySelector('h2').textContent = title;
  const input = d.querySelector('input');
  input.value = '';
  input.placeholder = placeholder;
  d.showModal();
  input.focus();
  return new Promise((resolve) => {
    d.onclose = () => resolve(d.returnValue === 'ok' ? input.value : null);
  });
}

export function openHelp() {
  document.getElementById('help').showModal();
}

const COMMANDS = [
  ['IB-123', 'Jump to a row'],
  ['defer IB-1 IB-2 [reason]', 'Defer rows'],
  ['block IB-1 reason', 'Block a row'],
  ['cancel IB-1 reason', 'Cancel a row'],
  ['ready IB-1', 'Make a row ready again'],
  ['move IB-1 top|bottom', 'Reorder'],
  ['new', 'File a new row'],
  ['publish', 'Review and publish'],
  ['refresh', 'Fetch main again'],
  ['show ready|blocked|deferred|done|all', 'Filter All rows'],
  ['theme auto|light|dark', 'Colour theme'],
];

export function openPalette() {
  const d = document.getElementById('palette');
  const input = d.querySelector('input');
  const list = d.querySelector('ul');
  const prefix = state.data ? state.data.prefix : 'IB';
  const draw = () => {
    const q = input.value.trim().toLowerCase();
    const ids = q ? rows().filter((r) => r.id.toLowerCase().startsWith(q)).slice(0, 6).map((r) => [r.id, r.title]) : [];
    const cmds = COMMANDS.filter(([c]) => !q || c.startsWith(q.split(' ')[0]));
    list.innerHTML = [...ids, ...cmds].map(([c, h]) => `<li><code>${esc(c)}</code> <span>${esc(h)}</span></li>`).join('');
  };
  input.value = '';
  input.oninput = draw;
  draw();
  d.showModal();
  input.focus();
  d.onclose = () => { if (d.returnValue === 'ok') runCommand(input.value.trim(), prefix); };
}

export async function runCommand(text, prefix) {
  const [verb, ...rest] = text.split(/\s+/);
  const idRe = new RegExp(`^${prefix}-\\d+$`, 'i');
  const ids = rest.filter((w) => idRe.test(w)).map((w) => w.toUpperCase());
  const reason = rest.filter((w) => !idRe.test(w)).join(' ');
  if (idRe.test(verb)) {
    const r = byId(verb.toUpperCase());
    if (!r) return set({ error: `${verb} not found` });
    return set({ view: r.status === 'ready' ? 'queue' : 'rows', selectedId: r.id });
  }
  if (['defer', 'block', 'cancel', 'ready'].includes(verb)) {
    const to = { defer: 'deferred', block: 'blocked', cancel: 'cancelled', ready: 'ready' }[verb];
    if ((to === 'blocked' || to === 'cancelled') && !reason) return set({ error: `${verb} needs a reason` });
    for (const id of ids) await A.addOp({ op: 'status', id, to, reason });
    return;
  }
  if (verb === 'move' && ids[0]) return rest.includes('bottom') ? A.moveBottom(ids[0]) : A.moveTop(ids[0]);
  if (verb === 'new') return set({ view: 'edit', editing: { mode: 'new' } });
  if (verb === 'publish') return set({ view: 'publish' });
  if (verb === 'refresh') return A.load(true);
  if (verb === 'show') return set({ view: 'rows', filters: { ...state.filters, status: rest[0] || 'all' } });
  if (verb === 'theme') return window.foremanTheme(rest[0] || 'auto');
  return set({ error: `Unknown command "${text}". Press ? for help.` });
}
