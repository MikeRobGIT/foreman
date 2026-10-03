import { state, set, byId } from '../state.js';
import { esc, tags } from '../md.js';
import * as A from '../actions.js';

const RISKS = ['low', 'medium', 'high'];
const STATUSES = ['ready', 'deferred', 'blocked', 'cancelled'];

export function render(root) {
  const ed = state.editing || { mode: 'new' };
  const r = ed.mode === 'edit' ? byId(ed.id) : null;
  if (ed.mode === 'edit' && !r) { set({ view: 'rows', editing: null }); return; }
  const draft = ed.draft || {};
  const v = draft.title !== undefined ? draft : (r ? { title: r.title.replace(/\\\|/g, '|'), risk: r.risk, status: STATUSES.includes(r.status) ? r.status : '', acceptance: r.acceptance.replace(/\\\|/g, '|'), source: r.source.replace(/\\\|/g, '|') }
    : { title: '', risk: 'low', status: 'ready', acceptance: '', source: '' });
  root.innerHTML = `<div class="wrap">
    <div class="crumb">${r ? `${esc(r.id)} · ${esc(r.status_text)}` : `New row · id assigned at publish (next free after origin/${esc(state.data.branch)}'s highest)`}</div>
    <h1 style="font-family:var(--display);font-size:26px;margin:8px 0 20px">${r ? 'Edit row' : 'File a row'}</h1>
    <form class="form" id="f">
      <label>Title<input name="title" required value="${esc(v.title)}"></label>
      <div class="three">
        <label>Risk<select name="risk">${r && !RISKS.includes(r.risk) ? `<option value="" selected>${esc(r.risk)} (unchanged)</option>` : ''}${RISKS.map((k) => `<option ${k === v.risk ? 'selected' : ''}>${k}</option>`).join('')}</select></label>
        <label>Status<select name="status">${r && !STATUSES.includes(r.status) ? `<option value="" selected>${esc(r.status)} (unchanged)</option>` : ''}${(r && r.status === 'done' ? [] : STATUSES).map((k) => `<option ${k === v.status ? 'selected' : ''}>${k}</option>`).join('')}</select></label>
        ${r ? '<label>Reason (for blocked or cancelled)<input name="reason"></label>' : '<label>Insert at<select name="insert"><option value="bottom">Bottom of the ready queue</option><option value="top">Top of the ready queue</option></select></label>'}
      </div>
      ${r ? '' : '<label>Reason (for blocked or cancelled)<input name="reason"></label>'}
      <label>Acceptance (Markdown; kept on one line in the file)<textarea name="acceptance">${esc(v.acceptance)}</textarea></label>
      <label>Source<input name="source" value="${esc(v.source)}"></label>
      <div class="hint" id="checks"></div>
      <div style="display:flex;gap:10px"><button class="btn primary" type="submit">${r ? 'Add changes to pending' : 'Add to pending changes'}</button><button class="btn" type="button" id="cancel">Cancel</button></div>
    </form></div>`;
  const form = root.querySelector('#f');
  const checks = () => {
    const d = Object.fromEntries(new FormData(form));
    Object.assign(ed, { draft: d });
    const nl = /[\r\n]/.test(`${d.title}${d.acceptance}${d.source}${d.reason || ''}`);
    const pipes = /\|/.test(`${d.title}${d.acceptance}${d.source}${d.reason || ''}`);
    const t = tags(d.title);
    const statusChanging = r && d.status && d.status !== r.status && (d.status === 'blocked' || d.status === 'cancelled');
    const newRowBlocked = !r && (d.status === 'blocked' || d.status === 'cancelled');
    const missingReason = (statusChanging || newRowBlocked) && !d.reason;
    const msg = missingReason ? `<span class="bad">✗ ${d.status} needs a reason</span>` : `${nl ? '<span class="bad">✗ line breaks are not allowed; they will be refused</span>' : '<span class="ok">✓ one line</span>'} · ${pipes ? '<span class="ok">✓ pipes will be escaped as \\|</span>' : '<span class="ok">✓ no pipes</span>'} · tags: ${t.length ? t.map(esc).join(', ') : 'none (add [serial] to keep it out of parallel waves)'}`;
    root.querySelector('#checks').innerHTML = msg;
  };
  form.oninput = checks;
  checks();
  root.querySelector('#cancel').onclick = () => set({ view: r && r.status === 'ready' ? 'queue' : 'rows', editing: null });
  form.onsubmit = async (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(form));
    const statusChanging = r && d.status && d.status !== r.status && (d.status === 'blocked' || d.status === 'cancelled');
    const newRowBlocked = !r && (d.status === 'blocked' || d.status === 'cancelled');
    if ((statusChanging || newRowBlocked) && !d.reason) {
      root.querySelector('#checks').innerHTML = `<span class="bad">✗ ${d.status} needs a reason</span>`;
      return;
    }
    if (!r) {
      const ok = await A.addOp({ op: 'add', title: d.title, risk: d.risk, status: d.status, reason: d.reason || '', acceptance: d.acceptance, source: d.source, insert: d.insert });
      if (ok) set({ view: 'queue', editing: null });
      return;
    }
    for (const field of ['title', 'acceptance', 'source']) {
      if (d[field] !== v[field] && !(await A.addOp({ op: 'set', id: r.id, field, value: d[field] }))) return;
    }
    if (d.risk && d.risk !== r.risk && !(await A.addOp({ op: 'set', id: r.id, field: 'risk', value: d.risk }))) return;
    if (d.status && d.status !== r.status && !(await A.addOp({ op: 'status', id: r.id, to: d.status, reason: d.reason || '' }))) return;
    set({ view: r.status === 'ready' ? 'queue' : 'rows', editing: null, selectedId: r.id });
  };
}
