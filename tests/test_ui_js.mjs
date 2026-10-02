import test from 'node:test';
import assert from 'node:assert/strict';
import { esc, inline, splitNotes, tags, prOf, ago } from '../ui/js/md.js';
import { filterRows, triageList } from '../ui/js/state.js';

test('notes escape html', () => {
  assert.equal(esc('<script>"x"</script>'), '&lt;script&gt;&quot;x&quot;&lt;/script&gt;');
  assert.equal(inline('a \\| b `<b>` **bold**'), 'a | b <code>&lt;b&gt;</code> <b>bold</b>');
});

test('splitNotes labels the original row and each marker block', () => {
  const blocks = splitNotes('Base text. **DONE (#12):** shipped it. **Correction (#13):** fixed. **#14 (partial):** half.');
  assert.deepEqual(blocks.map((b) => b.label), ['Original row', 'DONE (#12)', 'Correction (#13)', '#14 (partial)']);
  assert.equal(blocks[1].html, 'shipped it.');
});

test('tags and PR', () => {
  assert.deepEqual(tags('Fix the thing [serial] [e2e]'), ['serial', 'e2e']);
  assert.equal(prOf('done (#651)'), '651');
  assert.equal(prOf('ready'), '');
});

test('ago', () => {
  const now = Date.UTC(2026, 9, 2);
  assert.equal(ago(now / 1000 - 86400 * 3, now), '3 days');
  assert.equal(ago(null, now), 'unknown');
});

const ROWS = [
  { id: 'IB-1', title: 'Alpha [serial]', risk: 'low', status: 'ready', status_text: 'ready', acceptance: 'mentions foo.ts', source: '#10', touched: 100 },
  { id: 'IB-2', title: 'Beta', risk: 'high', status: 'ready', status_text: 'ready', acceptance: 'x', source: '#11', touched: 2_000_000_000 },
  { id: 'IB-3', title: 'Gamma', risk: 'low', status: 'done', status_text: 'done (#12)', acceptance: 'y', source: '#10', touched: 50 },
];

test('filterRows by text, status, risk, tag and PR', () => {
  const f = (patch) => filterRows(ROWS, { q: '', status: 'all', risk: 'any', tag: '', pr: '', ...patch }).map((r) => r.id);
  assert.deepEqual(f({ q: 'foo.ts' }), ['IB-1']);
  assert.deepEqual(f({ status: 'ready' }), ['IB-1', 'IB-2']);
  assert.deepEqual(f({ risk: 'high' }), ['IB-2']);
  assert.deepEqual(f({ tag: 'serial' }), ['IB-1']);
  assert.deepEqual(f({ pr: '10' }), ['IB-1', 'IB-3']);
});

test('filterRows PR filter rejects invalid input', () => {
  const f = (patch) => filterRows(ROWS, { q: '', status: 'all', risk: 'any', tag: '', pr: '', ...patch }).map((r) => r.id);
  assert.doesNotThrow(() => f({ pr: '(' }));
  assert.deepEqual(f({ pr: '(' }), []);
  assert.deepEqual(f({ pr: '1.' }), []);
});

test('triageList: ready rows untouched for N days, oldest first', () => {
  const now = 2_000_100_000 * 1000;
  assert.deepEqual(triageList(ROWS, 14, now).map((r) => r.id), ['IB-1']);
});
