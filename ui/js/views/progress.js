import { state, set, readyQueue } from '../state.js';
import { esc, inline, ago } from '../md.js';
import { api } from '../api.js';

let loading = false;

export function render(root) {
  if (!state.progress && !loading && !state.error) {
    loading = true;
    api.progress().then((p) => set({ progress: p })).catch((e) => set({ error: e.message })).finally(() => { loading = false; });
  }
  const p = state.progress;
  const err = state.error;
  const ready = readyQueue();
  const byRisk = ['low', 'medium', 'high'].map((k) => [k, ready.filter((r) => r.risk === k).length]);
  const oldest = [...ready].sort((a, b) => (a.touched || 0) - (b.touched || 0)).slice(0, 8);
  root.innerHTML = `
    <div class="chart"><h3>Shipped vs filed, per day</h3>
      <p>Rows marked done (by their PR's merge date) and rows filed from that day's PRs. The backlog shrinks on days the first bar is taller.</p>
      ${err ? `<p class="hint">Couldn't load merge dates: ${esc(err)}</p>` : !p ? '<p class="hint">Loading merge dates…</p>' : !p.available ? `<p class="hint">${esc(p.message)}</p>` : bars(p.days)}
      <div class="legend"><span><i style="background:var(--accent)"></i>Done</span><span><i style="background:var(--medink)"></i>Filed</span></div></div>
    <div class="split" style="grid-template-columns:1fr 1fr">
      <div class="chart"><h3>Ready by risk</h3><p>What the loop can pick (low, medium) versus what waits for you (high).</p>
        ${byRisk.map(([k, n]) => `<div style="display:grid;grid-template-columns:70px 1fr 40px;gap:10px;align-items:center;margin:8px 0;font-size:13px"><span class="risk ${k}">${k}</span><div style="height:10px;background:var(--chip);border-radius:5px;overflow:hidden"><i style="display:block;height:100%;width:${(n / Math.max(ready.length, 1)) * 100}%;background:var(--${k === 'low' ? 'lowink' : k === 'medium' ? 'medink' : 'hiink'})"></i></div><span style="font-variant-numeric:tabular-nums">${n}</span></div>`).join('')}</div>
      <div class="chart"><h3>Longest-untouched ready rows</h3><p>Good triage candidates.</p>
        ${oldest.map((r) => `<div style="font-size:13px;line-height:1.9"><span class="id" style="font-family:var(--mono);color:var(--muted)">${esc(r.id)}</span> ${inline(r.title.slice(0, 70))} <span class="hint">${ago(r.touched)}</span></div>`).join('')}</div>
    </div>`;
}

function bars(days) {
  if (!days.length) return '<p class="hint">No merged PRs referenced by the backlog yet.</p>';
  const W = 1000, H = 190, pad = 28, max = Math.max(1, ...days.flatMap((d) => [d.done, d.filed]));
  const bw = (W - pad * 2) / days.length;
  let g = `<line x1="${pad}" y1="${H - 24}" x2="${W - pad}" y2="${H - 24}" stroke="var(--line)"/>`;
  days.forEach((d, i) => {
    const x = pad + i * bw, h1 = (d.done / max) * (H - 50), h2 = (d.filed / max) * (H - 50);
    g += `<rect x="${x + bw * 0.18}" y="${H - 24 - h1}" width="${bw * 0.3}" height="${h1}" rx="2" fill="var(--accent)"><title>${d.day}: ${d.done} done</title></rect>`;
    g += `<rect x="${x + bw * 0.52}" y="${H - 24 - h2}" width="${bw * 0.3}" height="${h2}" rx="2" fill="var(--medink)"><title>${d.day}: ${d.filed} filed</title></rect>`;
    g += `<text x="${x + bw / 2}" y="${H - 8}" text-anchor="middle" font-size="11" fill="var(--muted)">${d.day.slice(5)}</text>`;
    g += `<text x="${x + bw * 0.33}" y="${H - 28 - h1}" text-anchor="middle" font-size="10.5" fill="var(--ink)">${d.done}</text>`;
    g += `<text x="${x + bw * 0.67}" y="${H - 28 - h2}" text-anchor="middle" font-size="10.5" fill="var(--muted)">${d.filed}</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="height:auto" role="img" aria-label="Rows done and filed per day">${g}</svg>`;
}
