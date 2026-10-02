# Backlog UI for foreman: design

Status: approved in brainstorming on 2026-10-02 (layout, visual direction and screens). This spec is the input to the implementation plan.

## Purpose

`/foreman` works from a markdown backlog: one table, one line per row, where the row order is the priority order. At vacancy-vibe's size (557 rows, 854 KB, notes up to several KB per row) four jobs are painful in a text editor:

1. **Triage and prioritise:** reorder what the loop picks next; defer, block, cancel or re-risk rows.
2. **Read and search:** find rows by id, text, status, risk, tag or PR; read long notes formatted.
3. **Add and edit rows:** without breaking the table format (one line, escaped pipes, five separators, unique ids).
4. **See progress:** whether the backlog is shrinking, what shipped, what is stuck.

**Success:** a user runs `/foreman ui`, does a triage session in the browser, and publishes the result as one reviewable PR. The diff contains only the rows they changed, and the PR never conflicts with the loop's bookkeeping.

**Not in scope:**
- multi-user or hosted use;
- editing rules or config files;
- starting `/foreman` runs from the UI;
- a mobile layout beyond "usable at 375px";
- any database.

## Decisions made in brainstorming

- **Form:** a local web app shipped in the plugin, started by `/foreman ui`.
- **Saving:** edits collect as pending changes. **Publish** replays them onto a fresh branch off the default branch, in its own worktree, and opens a PR. The UI never writes to the user's checkout.
- **Stack:** Python standard library server plus one static page (vanilla JS/CSS, no build, no dependencies).
- **Look:** the "Ledger" direction, with Auto/Light/Dark theming:
  - serif titles (Source Serif 4), Public Sans body, IBM Plex Mono for ids and code;
  - ink-blue accent; green/amber/red risk chips;
  - Auto follows `prefers-color-scheme`; Light and Dark pin the theme, and the choice is remembered in `localStorage`.
- **Screens:**
  - **Queue:** the main screen. An ordered ready queue with a detail pane, plus a stats strip, a ⌘K bar and a pending bar.
  - **All rows**, **Triage**, **Progress**, **Edit row** and **Publish**.

  The mockups that were approved live in `.superpowers/brainstorm/70201-1790972370/content/` (`ledger-dark-v5.html`, `screens-v8.html`).

## Architecture

```
skills/foreman/SKILL.md     `ui` subcommand: start the server, print the URL
scripts/backlog.py          NEW  parse/serialise/validate the backlog; apply operations
scripts/bk.py               uses backlog.py (behaviour unchanged)
scripts/ui.py               NEW  stdlib HTTP server + JSON API + publish
ui/index.html, app.js, app.css   NEW  the page (no build step)
tests/test_backlog.py, tests/test_ui_api.py   NEW
```

### `scripts/backlog.py` (the one parser)

- `parse(text) -> Backlog`: an ordered list of blocks.
  - **Prose:** any run of non-row lines (headings, the legend, blank lines), kept verbatim.
  - **Row:** the raw line, plus `id`, `title`, `risk`, `status` (the first word), `status_text` (the full cell), `acceptance` and `source`.
  - A row is a line matching `^\| <PREFIX>-\d+ \|`. Cells are split on unescaped ` | `.
- `serialise(Backlog) -> text`: writes every untouched block's raw text unchanged, so `serialise(parse(t)) == t` byte for byte. An edited row is rebuilt from its fields: pipes escaped as `\|`, newlines rejected.
- `validate_row(fields) -> [problems]`: these are bk.py's rules, now in one place:
  - one line;
  - exactly five separators;
  - a known risk (`low|medium|high`);
  - a status whose first word is one of `ready|deferred|blocked|done|cancelled|in-progress`.

  Validation runs only on rows the UI writes. Existing rows with other status words (vacancy-vibe has one `implemented` row) are displayed as they are, filterable as "other", and never rewritten unless edited.
- `apply(backlog, ops) -> (backlog, results)`: replays operations (below) and returns one result per op: `applied`, or `conflict` with a reason.
- `bk.py` is refactored onto this module and must stay behaviour-identical. `tests/run.sh`'s existing bk.py cases, and its byte-identical comparison against the old script, prove it.

The id prefix and backlog path come from `cfg.py` (`id_prefix`, `backlog_path`), as they do today.

### Operations (the pending changes)

Each op records the row's raw line at edit time (`base`), so a later replay can tell whether the row changed underneath it.

| op | fields | effect |
|---|---|---|
| `set` | `id`, `field` (`title`, `risk`, `acceptance`, `source`), `value`, `base` | rebuild the row with one field changed |
| `status` | `id`, `to` (`ready`, `deferred`, `blocked`, `cancelled`), `reason` (required for blocked and cancelled), `base` | status cell becomes `to`, or `to — reason` |
| `move` | `id`, `to`: `top` \| `bottom` \| `{before: id}` \| `{after: id}`, `base` | moves the row's line (see "Queue order") |
| `add` | `tmp` (e.g. `NEW-1`), `title`, `risk`, `status`, `acceptance`, `source`, `insert` (`top` or `bottom`) | inserts a new row; its id is assigned at publish |

**Conflict rule:** for `set`, `status` and `move`, the op applies only when the row's current raw line equals `base`. Otherwise it is a `conflict` ("changed on main since you edited it"), and the result shows the new status text, e.g. "marked done (#651)". A `move` whose anchor row no longer exists is also a conflict.

Several ops on the same row chain: the second op's `base` is the line after the first op. The UI shows the combined effect.

### Queue order

- The **ready queue** is the `ready` rows in file order, the order `/foreman` picks from. High-risk ready rows are shown, but marked "opt-in: the loop skips it".
- `move top` puts the line directly before the first `ready` row in the file.
- `move bottom` puts it directly after the last `ready` row.
- `before`/`after` place it next to the anchor.

Section headings in the file (e.g. "## Low risk (ready)") are prose and are not moved or renamed. A move that crosses a heading is allowed: the risk column, not the heading, is authoritative.

### `scripts/ui.py` (server)

- **Start:** `python3 ui.py --repo <dir> [--port 0] [--no-open]`. It binds `127.0.0.1` on a free port, writes `~/.claude/foreman/<repo-key>/ui.json` (`{port, token, pid}`) and prints the URL `http://127.0.0.1:<port>/?t=<token>`. `<repo-key>` is a short hash of the origin URL plus the repo path.
- A second start for the same repo reuses the live server and prints its URL. `--stop` stops it.
- **Security:**
  - The token is a `secrets.token_urlsafe(24)`. After the first load it is set as an `HttpOnly; SameSite=Strict` cookie.
  - Every `/api/*` request must carry the cookie or an `X-Foreman-Token` header that matches.
  - The `Host` header must be `127.0.0.1:<port>` or `localhost:<port>`, which stops DNS rebinding.
  - A wrong token or host gets a 403.
  - Static files are served only from the plugin's `ui/` directory.
- **Data source:** the default branch on origin (`git symbolic-ref refs/remotes/origin/HEAD`, falling back to `main`).
  - `GET /api/backlog` runs `git fetch origin <branch>` (at most once a minute unless `?refresh=1`), then `git show origin/<branch>:<backlog_path>`, and returns the parsed rows, the pending ops and their replay results against that text.
  - The user's working tree and current branch are never read or written.
- **Pending store:** `~/.claude/foreman/<repo-key>/pending.json` holds the op list.
  - `PUT /api/pending` replaces it; `POST /api/pending/undo` drops the last op.
  - Writes are atomic: write a temp file, then rename.
- **"Last touched" dates:** `git blame --line-porcelain origin/<branch> -- <backlog_path>` gives each row line's last-modified time. It is cached per commit SHA and backs both the Triage filter ("untouched ≥ N days", default 14) and Progress's "longest-untouched ready rows".
- **Progress data:**
  - `gh pr list --state merged --limit 500 --json number,mergedAt` gives merge dates, cached for 10 minutes.
  - **Done per day:** rows whose status is `done (#N)`, grouped by #N's merge date.
  - **Filed per day:** rows whose source cell names a PR, grouped by that PR's merge date.
  - Without `gh`, the Progress tab shows counts only and a note saying so.
- **Publish** (`POST /api/publish`). One publish runs at a time; a second gets a 409.
  1. `git fetch origin <branch>`. Then `git worktree add -b backlog/ui-<YYYYMMDD>-<n> <tmpdir> origin/<branch>`, where `<n>` is the next free number.
  2. Parse the worktree's backlog and apply the ops. Give each `add` an id: the highest existing number plus 1, in op order.
  3. Validate every changed row. If any op fails validation, abort with nothing written and show the problems.
  4. If no op applied (all were conflicts), stop: remove the worktree and branch, and report "nothing to publish".
  5. Write the file. `git commit -m "docs(backlog): <summary>"` names the counts, e.g. "triage 3 rows, add 1". No trailers.
  6. `git push -u origin HEAD`, then `gh pr create --base <branch> --title <same> --body <list of every op, with row ids>`.
  7. Remove the worktree; delete the local branch (the remote branch stays for the PR).
  8. Remove the applied ops from `pending.json` and keep the conflicts.
  9. Return `{pr_url, applied, conflicts}`.

  Any failure from step 5 onward returns the error text, removes the worktree and deletes the branch locally (and remotely if it was pushed), and leaves `pending.json` unchanged.

### The page (`ui/`)

Vanilla JS modules and one CSS file. Theme tokens live on `:root`, with dark overrides under `@media (prefers-color-scheme: dark) :root:not([data-theme=light])` and `:root[data-theme=dark]`. Fonts come from Google Fonts with system fallbacks, so it works offline in the fallback fonts.

- **Header** (every screen):
  - brand;
  - tabs: Queue, All rows, Triage, Progress;
  - ⌘K bar;
  - the Auto/Light/Dark switch.
- **Stats strip:** counts per status, plus a ready-by-risk bar.
- **Pending bar** (when there are ops): "N unpublished", a one-line summary, then **Review & publish**, which opens the Publish screen.
- **Queue:**
  - The left pane is the ready queue: position number, id, title (inline `code` rendered), source, risk chip, and an "edited" marker for rows with pending ops.
  - Rows can be reordered by drag, or with `⌥↑`/`⌥↓`.
  - The right pane is the selected row: id and position, a serif title, chips, and actions (Edit `e`, Move to top `t`, Defer `d`, Block… `b`, Cancel… `x`).
  - Notes are split into blocks at `**DONE (#N):**`, `**Correction…**`, `**#N (…)**`, `**Depends on…**`, `**Raised…**` and `**PARTIAL…**` markers. The first block is labelled "Original row".
  - Keyboard: `j`/`k` move the selection.
- **All rows:**
  - search across id, title, acceptance and source (client-side over the whole backlog);
  - segmented filters: status (with counts), risk, tag (`[x]` in the title), filed-from PR;
  - a table of id, title, risk, status chip and PR link;
  - clicking a row opens it in the Queue detail pane, or in a drawer for non-ready rows.
- **Triage:**
  - one row at a time from a filter (default: ready, untouched ≥ 14 days, oldest first);
  - a progress meter;
  - keys: `k` keep, `t` top, `↓` bottom, `e` edit, `d` defer, `b` block (asks for a reason), `x` cancel (asks for a reason), `u` undo.
- **Progress:**
  - done vs filed per day (inline SVG bars from real data, with the values labelled);
  - ready by risk;
  - longest-untouched ready rows.
- **Edit row / New row:** a form with title, risk, status (with a reason when it needs one), insert position (new rows only), acceptance (a monospace textarea), and source.
  - Live checks: one line, pipes escaped, five separators, tags detected.
  - A hint about the `[serial]` tag.
  - Saving adds an op; nothing is written yet.
- **Publish:**
  - every op with its effect and diff (a status or text change shown struck through and new), with Undo or Edit;
  - conflicts in red, with the reason and **Drop** or **Re-apply to new text**, which rebases the op onto the current line;
  - the target commit and when it was fetched;
  - **Publish N as PR**, then the PR link.
- **⌘K:**
  - jump to a row id;
  - filter presets;
  - "defer IB-53 IB-54 reason…" (bulk status);
  - "move IB-12 top";
  - new row;
  - publish;
  - theme.
- **Accessibility:**
  - every action is a real button with a visible focus ring;
  - keyboard shortcuts are listed in a `?` overlay;
  - colour is never the only signal (chips carry text);
  - `prefers-reduced-motion` is respected.

### The `/foreman ui` subcommand

`skills/foreman/SKILL.md` gains `ui` (and `ui stop`):
- It runs `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ui.py" --repo "$PWD"` in the background and prints the URL.
- It tells the user that edits are published as a PR, and that the loop's rule ("only the bookkeeping PR edits the backlog") is unaffected: the UI's PR is a separate, reviewable PR.
- It doesn't start a server when the config has no backlog file.

## Error handling

- **No origin, no `gh`, or a fetch failure:** the page shows a banner with the exact command that failed, still shows the last fetched text if there is one, and disables Publish when origin is missing.
- **A malformed row in the file** (wrong separator count): the parser keeps it as a raw "unparsed row" block, shown in All rows with a warning, and never rewritten.
- **pending.json** is unreadable: it is renamed to `pending.json.bad-<ts>`, the user is told, and the app starts empty.
- **Server crash or restart:** pending ops survive (they are on disk), and the page reconnects on reload.

## Testing

- **`tests/test_backlog.py`** (unittest):
  - **Round trip:** byte-identical `serialise(parse(t))` on vacancy-vibe's real backlog, on a fixture with escaped pipes, and on a malformed row.
  - **Every op type:** applies to a fixture and gives the expected text.
  - **Conflicts:** a `base` mismatch and a missing anchor each give a conflict. A chained op's `base` follows the earlier op.
  - **Ids:** `add` takes the max + 1, with two adds in order.
  - **Validation:** each rule gets a failing case.
  - **Mutants:** dropping the `base` check, and escaping pipes wrongly, each fail a test.
- **`tests/test_ui_api.py`:** starts the server against a temp git repo with a bare "origin" and a fake `gh` on `PATH` that records its arguments.
  - 403s for a missing token and a wrong Host.
  - A pending-op round trip.
  - Publish creates the branch on the bare origin with exactly the expected diff, calls `gh pr create` with the expected title and body, removes the worktree, and keeps the conflicts.
  - A second concurrent publish gets a 409.
- **`tests/run.sh`** runs both, next to the existing suite.
- **Browser check** (manual, before release):
  - open the UI on vacancy-vibe, at 1440px and 375px, in light and dark;
  - reorder one row and defer one;
  - publish against a throwaway fork or branch, and confirm the PR diff is exactly those two rows.

## Version

Plugin version 0.3.0. The README gains a "Backlog UI" section covering start and stop, how publishing works, the pending store location, and the security model.
