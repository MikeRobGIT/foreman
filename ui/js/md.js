// ui/js/md.js: pure text helpers (no DOM), unit-tested with node --test.
export const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

export function inline(s) {
  return esc(String(s ?? '').replace(/\\\|/g, '|'))
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
}

const MARK = /(?=\*\*(?:DONE \(#\d+\)|Correction|#\d+ \(|Depends on|Raised|PARTIAL|Unblocked|Left|Also)[^*]*\*\*)/;

export function splitNotes(acceptance) {
  return String(acceptance ?? '').split(MARK).map((part, i) => {
    const m = part.match(/^\*\*([^*]+?):?\*\*\s*/);
    if (i === 0 || !m) return { label: i === 0 ? 'Original row' : 'Note', html: inline(part.trim()) };
    return { label: m[1].replace(/:$/, ''), html: inline(part.slice(m[0].length).trim()) };
  }).filter((b) => b.html);
}

export const tags = (title) => [...String(title).matchAll(/\[([a-z0-9-]+)\]/gi)].map((m) => m[1]);
export const prOf = (statusText) => (String(statusText).match(/#(\d+)/) || [])[1] || '';

export function ago(epochSeconds, now = Date.now()) {
  if (!epochSeconds) return 'unknown';
  const days = Math.floor((now / 1000 - epochSeconds) / 86400);
  return days <= 0 ? 'today' : days === 1 ? '1 day' : `${days} days`;
}
