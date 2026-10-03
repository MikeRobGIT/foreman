#!/usr/bin/env python3
"""foreman's settings hooks: one dispatcher, run by hooks/hooks.json as `python3 hooks.py <event>`.

Hook mode, JSON on stdin: run-start, session-end, pretool, posttool, subagent-start, subagent-stop, permission.
(SessionEnd runs `session-end`, not `run-end`: the CLI verb never reads stdin, which a Bash call may hold open.)

CLI mode, run from the orchestrator's Bash tool. `pretool` records the effect from its own hook input, so the
record lands where the hooks look and is bound to this session:
  hooks.py run-end                 end this session's run (posttool deletes the marker)
  hooks.py release <batch> <sha>   release a spec-phase batch (piece 10)
  hooks.py stopping <batch>        let the tagged workers of <batch> launched so far stop (piece 7)
  hooks.py forget <batch>          drop the batch's hook state (W5)
  exit 0 = recorded, 1 = the record is missing, 3 = hooks inactive (no fresh run marker); run-end always 0

Every hook fails open on its own errors (stderr note, exit 0), except piece 1's undeterminable diff.
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FAST = re.compile(r'\b(git|gh|gt)\b|hooks\.py')
RUN_TTL = 48 * 3600
BATCH_TTL = 14 * 86400
BATCH = re.compile(r'^[A-Za-z0-9_-]+$')
SHA = re.compile(r'^[0-9a-f]{7,40}$')
CONTROL = ('run-end', 'release', 'stopping', 'forget')
CONTROL_TEXT = re.compile(r'hooks\.py\S*\s+(run-end|release|stopping|forget)\b')
EDIT_TOOLS = ('Edit', 'Write', 'MultiEdit', 'NotebookEdit')
BACKLOG_DEFAULT = '.claude/improvement-backlog.md'
RULES_DEFAULT = '.claude/rules/*.md'
CONFIG_NAMES = ('.claude/foreman.config.md', '.claude/improve-next.config.md')
RUN_PROMPT = re.compile(r'^\s*(?:/loop(?:\s+(?!/)\S+)?\s+)?/foreman(?::foreman)?(?=\s|$)(.*)', re.S)
TAG = re.compile(r'^foreman-worker: ([A-Za-z0-9_-]+)( spec-phase)?\s*$')

HANDLERS = {}      # event -> fn(h)
BASH_PIECES = {}   # order -> fn(h, sc); piece 2 runs first so its local merge deny saves pieces 3 and 4 a network call
EDIT_PIECES = {}   # order -> fn(h, repo, path)
POST_EDIT = {}     # order -> fn(h, tool_input)


def _load():
    """Imports the fast paths skip: a Bash call that names no git/gh/gt/hooks.py costs one json.load."""
    global backlog, cfg, state, shellscan, subprocess, shlex, glob, tempfile
    sys.path.insert(0, HERE)
    import backlog  # noqa: F401
    import cfg  # noqa: F401
    import glob  # noqa: F401
    import shellscan  # noqa: F401
    import shlex  # noqa: F401
    import state  # noqa: F401
    import subprocess  # noqa: F401
    import tempfile  # noqa: F401


# ---- repo context ---------------------------------------------------------------------------------

def same_file(a, b):
    try:
        return os.path.realpath(os.path.expanduser(a)) == os.path.realpath(b)
    except ValueError:   # a NUL byte, from a word the scan blanked
        return False


def ls(d, pattern):
    """glob.glob(pattern) inside the directory d, whatever characters d holds."""
    return glob.glob(os.path.join(glob.escape(d), pattern))


def rm(path):
    try:
        os.remove(path)
    except OSError:
        pass


class Repo:
    """One checkout as the hooks see it: its top level, its main checkout, the main checkout's config."""

    def __init__(self, top):
        self.top = os.path.realpath(top)
        self.main = state.main_checkout(top) or top
        self.cfg_path = cfg.config_path(self.main, use_env=False)
        if self.cfg_path:
            with open(self.cfg_path, encoding='utf-8') as f:
                self.cfg = cfg.parse(f.read())
        else:
            self.cfg = {}
        self._dirs = None

    def get(self, key, default=None):
        v = self.cfg.get(key)
        if v is None:
            return default if default is not None else cfg.DEFAULTS.get(key)
        return v

    def list(self, key):
        v = self.get(key)
        return v if isinstance(v, list) else ([] if v in (None, '') else [v])

    @property
    def dirs(self):
        if self._dirs is None:
            self._dirs = state.state_dirs(self.main)
        return self._dirs

    @property
    def backlog(self):
        return self.get('backlog_path', BACKLOG_DEFAULT)

    @property
    def rules(self):
        v = self.get('rules_glob', RULES_DEFAULT)
        return v if isinstance(v, list) else [v]

    def disabled(self, piece):
        return piece in self.list('hooks_disabled')

    def rel(self, path):
        return os.path.relpath(os.path.realpath(path), self.top)


_repos = {}


def repo_at(path):
    """The Repo holding `path` (a file or a directory, existing or not), or None outside a foreman repo."""
    d = os.path.abspath(path)
    while not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    top = state.toplevel(d)
    if not top:
        return None
    if top not in _repos:
        _repos[top] = Repo(top)
    return _repos[top] if _repos[top].cfg_path else None


class Hook:
    """One hook call: its input, the denies it collects, and the repos whose run marker it touches."""

    def __init__(self, inp):
        self.inp = inp
        self.sid = inp.get('session_id') or ''
        self.agent = inp.get('agent_id')
        self.cwd = inp.get('cwd') or os.getcwd()
        self.denies = []
        self.touch = set()
        self._active = {}

    def deny(self, reason):
        self.denies.append(reason)

    def run_active(self, repo):
        if repo.top not in self._active:
            self._active[repo.top] = marker(repo, self.sid) is not None
        return self._active[repo.top]

    def active(self, repo, piece, run_only=True):
        """True when `piece` applies in `repo`: a foreman repo, not disabled, and (run-only) a live run."""
        return bool(repo) and not repo.disabled(piece) and (not run_only or self.run_active(repo))


def cmd_repo(c, h):
    return repo_at(c.cwd or h.cwd)


# ---- the run marker -------------------------------------------------------------------------------

def marker(repo, sid):
    """(path, data) of this session's live run marker, or None."""
    if not sid or os.sep in sid:
        return None
    for d in repo.dirs:
        p = os.path.join(d, 'runs', sid + '.json')
        m = state.read_json(p)
        if isinstance(m, dict) and time.time() - float(m.get('touched', 0)) < RUN_TTL:
            return p, m
    return None


def touch_marker(repo, sid):
    m = marker(repo, sid)
    if m:
        m[1]['touched'] = time.time()
        state.write_json(m[0], m[1])


def prune(sd):
    """Drop markers untouched for 48 hours and batch files older than 14 days."""
    now = time.time()
    for p in ls(sd, 'runs/*.json'):
        m = state.read_json(p)
        if not isinstance(m, dict) or now - float(m.get('touched', 0)) >= RUN_TTL:
            rm(p)
    for p in ls(sd, 'batches/*/*') + ls(sd, 'batches/*/agents/*'):
        if os.path.isfile(p) and now - os.path.getmtime(p) >= BATCH_TTL:
            rm(p)


def run_args(inp):
    """The /foreman arguments when this prompt starts a run, else None. `ui` and `init` aren't runs.
    UserPromptExpansion carries the command and its arguments as separate fields (probe P1)."""
    if inp.get('hook_event_name') == 'UserPromptExpansion':
        if inp.get('command_name') not in ('foreman', 'foreman:foreman'):
            return None
        args = (inp.get('command_args') or '').strip()
    else:
        m = RUN_PROMPT.match(inp.get('prompt') or '')
        if not m:
            return None
        args = m.group(1).strip()
    words = args.split()
    return None if words and words[0] in ('ui', 'init') else args


def run_start(h):
    args = run_args(h.inp)
    repo = repo_at(h.cwd) if args is not None and h.sid and os.sep not in h.sid else None
    if not repo:
        return
    sd = repo.dirs[0]
    prune(sd)
    p = os.path.join(sd, 'runs', h.sid + '.json')
    m = state.read_json(p)
    m = m if isinstance(m, dict) else {'started': time.time()}
    m.update(touched=time.time(), args=args)
    state.write_json(p, m)


def end_run(sid):
    """Delete this session's run markers. A glob, no git: every SessionEnd hook shares 1.5 seconds."""
    if not sid or os.sep in sid:
        return
    for home in {state.home(), state.DEFAULT_HOME}:
        for p in ls(home, f'*/runs/{glob.escape(sid)}.json'):
            rm(p)


def session_end(h):
    if h.inp.get('reason') in ('clear', 'logout'):
        end_run(h.sid)


HANDLERS['run-start'] = run_start
HANDLERS['session-end'] = session_end


# ---- orchestrator CLIs: run-end, release, stopping, forget ----------------------------------------

def control_call(command):
    """(verb, args) when `command` is exactly `python3 <this hooks.py> <verb> <args>`, else None."""
    try:
        w = shlex.split(command)
    except ValueError:
        return None
    return control_words(w[1:]) if len(w) >= 3 and w[0] == 'python3' else None


def control_words(w):
    """(verb, args) for the words `<this hooks.py> <verb> <args...>`, else None."""
    if len(w) < 2 or not same_file(w[0], os.path.join(HERE, 'hooks.py')) or w[1] not in CONTROL:
        return None
    verb, rest = w[1], w[2:]
    ok = ((verb == 'run-end' and not rest)
          or (verb == 'release' and len(rest) == 2 and BATCH.match(rest[0]) and SHA.match(rest[1]))
          or (verb in ('stopping', 'forget') and len(rest) == 1 and BATCH.match(rest[0])))
    return (verb, rest) if ok else None


def runs_control(sc, text, cwd):
    """True when the scanned command runs `<path>/hooks.py <control verb>` as a command: a word naming this
    hooks.py (basename hooks.py, or the same realpath as control_call tests, so `hooks.py/`, `hooks.py/.` and a
    symlink count) immediately followed by the verb, in a top-level segment or in the script a shell or eval is
    given. A mention in a quoted string, a comment or a heredoc isn't a call. Text the scan couldn't read is
    judged by its raw text. Not seen (accepted limit): a call built at run time, in a $(...) or fed to a shell's
    stdin. It tests a path the way control_call does, so a deny sees every call that would be recorded."""
    if not sc.ok:
        return bool(CONTROL_TEXT.search(text.replace('\\\n', '')))
    for seg in sc.segs:
        w = seg.words
        if w is None:
            if CONTROL_TEXT.search(text[seg.start:seg.end].replace('\\\n', '')):
                return True
            continue
        if any(b in CONTROL and (os.path.basename(a) == 'hooks.py' or same_file(a, os.path.join(HERE, 'hooks.py')))
               for a, b in zip(w, w[1:])):
            return True
        if any(os.path.basename(a) in shellscan.SHELLS + ('eval',) for a in w):
            for script in (a for a in w if re.search(r'\s', a)):
                if runs_control(shellscan.scan(script, seg.cwd), script, seg.cwd):
                    return True
    return False


def batch_file(d, sid, batch, ext):
    return os.path.join(d, 'batches', sid, f'{batch}.{ext}')


def released(repo, sid, batch):
    """The batch's release record ({sha, time}) in this session, or None."""
    for d in repo.dirs:
        r = state.read_json(batch_file(d, sid, batch, 'released'))
        if isinstance(r, dict):
            return r
    return None


def agent_entry(repo, sid, agent):
    """The agent map's entry {batch, spec, ...} for this agent in this session, or None."""
    if not agent or os.sep in agent:
        return None
    for d in repo.dirs:
        e = state.read_json(os.path.join(d, 'batches', sid, 'agents', agent))
        if isinstance(e, dict):
            return e
    return None


def forget(repo, sid, batch):
    for d in repo.dirs:
        for p in ls(os.path.join(d, 'batches', sid), glob.escape(batch) + '.*'):
            rm(p)
        for p in ls(os.path.join(d, 'batches', sid, 'agents'), '*'):
            e = state.read_json(p)
            if isinstance(e, dict) and e.get('batch') == batch:
                rm(p)


def pretool_control(h, repo, verb, rest):
    d = repo.dirs[0]
    if verb == 'release':
        state.write_json(batch_file(d, h.sid, rest[0], 'released'), {'sha': rest[1], 'time': time.time()})
    elif verb == 'stopping':
        state.write_json(batch_file(d, h.sid, rest[0], 'stopped'), {'time': time.time()})
    elif verb == 'forget':
        forget(repo, h.sid, rest[0])
    touch_marker(repo, h.sid)


def batch_time(batch):
    """Epoch seconds of a `YYYYMMDD-HHMM-...` batch name (local time), or 0 for an undated name."""
    m = re.match(r'^(\d{8}-\d{4})-', batch)
    try:
        return time.mktime(time.strptime(m.group(1), '%Y%m%d-%H%M')) if m else 0
    except ValueError:
        return 0


def fresh_marker(dirs, batch):
    """A live run marker in any session, touched after the batch name's time."""
    since = batch_time(batch)
    for d in dirs:
        for p in ls(d, 'runs/*.json'):
            m = state.read_json(p)
            t = float(m.get('touched', 0)) if isinstance(m, dict) else 0
            if t > since and time.time() - t < RUN_TTL:
                return True
    return False


def cli(verb, rest):
    if verb == 'run-end':
        return 0
    if not control_words([os.path.join(HERE, 'hooks.py'), verb] + rest):
        print(__doc__, file=sys.stderr)
        return 2
    _load()
    main = state.main_checkout(os.getcwd())
    dirs = state.state_dirs(main) if main and cfg.config_path(main, use_env=False) else []
    if not fresh_marker(dirs, rest[0]):
        print('foreman: hooks inactive (no fresh run marker), so nothing gates this batch', file=sys.stderr)
        return 3
    batch = glob.escape(rest[0])
    if verb == 'forget':
        return 1 if any(ls(d, f'batches/*/{batch}.*') for d in dirs) else 0
    ext = 'released' if verb == 'release' else 'stopped'
    return 0 if any(ls(d, f'batches/*/{batch}.{ext}') for d in dirs) else 1


# ---- the agent map: a subagent's batch and phase, read from its own transcript (probe P2) ---------

def worker_tag(text):
    """(batch, spec) from a prompt whose first line is `foreman-worker: <batch>[ spec-phase]`, else None."""
    first = (text or '').split('\n', 1)[0].rstrip('\r')
    m = TAG.match(first)
    if m:
        return m.group(1), bool(m.group(2))
    m = re.match(r'^foreman-worker:\s*([A-Za-z0-9_-]+)', first)
    if m and 'spec-phase' in first:
        print(f'foreman: odd worker tag read as spec-phase: {first[:80]}', file=sys.stderr)
        return m.group(1), True
    return None


def transcript_head(path):
    """(text of the first user entry, epoch time of the first entry with one) of a JSONL transcript.
    Only the FIRST user entry counts: with no text block, or after a line that isn't a JSON object (we can't
    tell which entry is first), the text is None, never a later entry's."""
    text, born = None, None
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    break
                if not isinstance(e, dict):
                    break
                if born is None and isinstance(e.get('timestamp'), str):
                    born = iso_time(e['timestamp'])
                if e.get('type') == 'user':
                    msg = e.get('message')
                    c = msg.get('content') if isinstance(msg, dict) else None
                    if isinstance(c, list):
                        c = next((b.get('text') for b in c if isinstance(b, dict) and b.get('type') == 'text'), None)
                    text = c if isinstance(c, str) else None
                    break
    except (OSError, TypeError):
        pass
    return text, born


def iso_time(s):
    import calendar
    try:
        return calendar.timegm(time.strptime(s[:19], '%Y-%m-%dT%H:%M:%S'))
    except ValueError:
        return None


def resolve_agent(repo, h):
    """The agent map's entry for this call's subagent; None for the main session.
    SubagentStart has no prompt and the worker's transcript doesn't exist yet (P2), so the entry is filled
    here, on the agent's first sight, from `<transcript_path minus .jsonl>/subagents/agent-<id>.jsonl`:
    {batch, spec, briefed, start} for a tagged worker, {batch: None} for any other agent (read once).
    While that transcript is unreadable nothing is written and the answer is None, so a later call retries.
    `time` repeats `start`: it is the key the report check reads (piece 7)."""
    if not h.agent or not h.sid or os.sep in h.agent + h.sid:
        return None
    entry = agent_entry(repo, h.sid, h.agent)
    if entry:
        return entry
    stem, ext = os.path.splitext(h.inp.get('transcript_path') or '')
    if ext != '.jsonl':
        return None
    text, born = transcript_head(os.path.join(stem, 'subagents', f'agent-{h.agent}.jsonl'))
    if text is None:
        return None
    tag = worker_tag(text)
    if tag:
        start = born or time.time()
        entry = {'batch': tag[0], 'spec': tag[1], 'briefed': False, 'start': start, 'time': start}
    else:
        entry = {'batch': None}
    state.write_json(os.path.join(repo.dirs[0], 'batches', h.sid, 'agents', h.agent), entry)
    return entry


# ---- piece 1: gate before push --------------------------------------------------------------------

# pretool prefixes every deny with "foreman: ", so this one doesn't repeat the name
UNREADABLE = "couldn't read this command. Run the gate, then push with a plain `git push` in its own call"


def glob_re(pattern):
    """A gitignore-style pattern as a regex: `**/` is zero or more directories, `*` stays inside one, a
    pattern without a slash matches at any depth, and a match on a directory covers what's inside it."""
    p = pattern.strip()
    anchored = '/' in p.rstrip('/')
    p = p.lstrip('/')
    if p.endswith('/'):
        p += '**'
    out, i = '', 0
    while i < len(p):
        if p.startswith('**/', i):
            out, i = out + '(?:.*/)?', i + 3
        elif p.startswith('**', i):
            out, i = out + '.*', i + 2
        elif p[i] == '*':
            out, i = out + '[^/]*', i + 1
        elif p[i] == '?':
            out, i = out + '[^/]', i + 1
        else:
            out, i = out + re.escape(p[i]), i + 1
    return re.compile(('' if anchored else '(?:.*/)?') + out + r'(?:/.*)?\Z')


def glob_match(path, pattern):
    return bool(glob_re(pattern).match(path))


def exempt(repo, path, receipt=False):
    """A path a push may change without a gate: gate_exempt, the backlog, the rules files and, when matching a
    receipt, worktree_env_files (workers copy them in and never commit them)."""
    patterns = repo.list('gate_exempt') + [repo.backlog] + repo.rules
    if receipt:
        patterns += repo.list('worktree_env_files')
    return any(glob_match(path, p) for p in patterns)


def default_branch(top):
    head = state.git(top, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD')
    return head.strip().split('/', 1)[1] if head and '/' in head else 'main'


def changed(top, a, b):
    """The paths that differ between two trees or commits, or None when git can't say. NUL-separated: without -z
    git quotes a path with non-ASCII characters, and no pattern would match the quoted form."""
    out = state.git(top, 'diff', '--name-only', '-z', '--no-renames', a, b)
    return None if out is None else [p for p in out.split('\0') if p]


def worktree_links(top, tree, paths):
    """The paths among `paths` that `tree` holds as gitlinks under .claude/worktrees/: a worktree some commit swept in."""
    under = [p for p in paths if p.startswith('.claude/worktrees/')]
    out = state.git(top, '--literal-pathspecs', 'ls-tree', '-z', tree, '--', *under) if under else None
    return {e.split('\t', 1)[1] for e in (out or '').split('\0') if e.startswith('160000 ')}


def receipt_reason(repo, since, links=()):
    r = (f"no passing gate for this tree — run `{HERE}/gate-lock.sh <owner> -- '{repo.get('gate') or '<gate>'}'` "
         "from the checkout's top level, commit every file the gate changed, then push")
    plain = [p for p in since or () if p not in links]
    if plain:
        r += ("; the gate ran on a different tree: commit these, or restore them (`git checkout -- <path>` for a "
              "tracked file, delete for an untracked one) and re-run the gate: " + ', '.join(plain[:10]))
    if links:
        r += ("; a worktree under .claude/worktrees/ was committed as a gitlink, which the gate never sees: "
              "`git rm --cached " + ' '.join(shlex.quote(p) for p in sorted(links)[:10]) + "`, commit, and ignore "
              ".claude/worktrees/ (e.g. in .git/info/exclude)")
    return r


def receipt_check(repo, cwd):
    """None when the tree at `cwd`'s HEAD may be pushed, else the deny reason."""
    top = state.toplevel(cwd)
    tree = state.git(top, 'rev-parse', 'HEAD^{tree}') if top else None
    if not tree:
        return receipt_reason(repo, None)
    tree = tree.strip()
    if any(os.path.exists(os.path.join(d, 'gate', tree)) for d in repo.dirs):
        return None
    since = None
    for d in repo.dirs:
        last = state.read_json(state.last_path(d, top))
        names = changed(top, last['tree'], tree) if isinstance(last, dict) and last.get('tree') else None
        if names is not None:
            since = [p for p in names if not exempt(repo, p, receipt=True)]
            if not since:
                return None
            break
    base = state.git(top, 'merge-base', f'origin/{default_branch(top)}', 'HEAD')
    names = changed(top, base.strip(), 'HEAD') if base else None
    if names is not None and all(exempt(repo, p) for p in names):
        return None
    return receipt_reason(repo, since, worktree_links(top, tree, since or []))


def is_push_trigger(c):
    return (c.prog, c.sub) in (('git', 'push'), ('gh', 'pr create'), ('gt', 'submit'))


def gate_before_push(h, sc):
    gate_in_call = any(s.words and any(os.path.basename(w) == 'gate-lock.sh' for w in s.words) for s in sc.segs)
    for c in sc.cmds:
        if not is_push_trigger(c):
            continue
        repo = cmd_repo(c, h)
        if not h.active(repo, 'gate_before_push'):
            continue
        h.touch.add(repo)
        if c.args is None or c.cwd is None or gate_in_call:
            h.deny(UNREADABLE)
        elif c.sub == 'push' and shellscan.push_info(c.args, default_branch(c.cwd))[0]:
            continue
        else:
            why = receipt_check(repo, c.cwd)
            if why:
                h.deny(why)


BASH_PIECES[1] = gate_before_push


# ---- pretool / posttool ---------------------------------------------------------------------------

def pretool(h):
    tool = h.inp.get('tool_name')
    ti = h.inp.get('tool_input') or {}
    if tool == 'Bash':
        pretool_bash(h, ti.get('command') or '')
    elif tool in EDIT_TOOLS:
        path = ti.get('file_path') or ti.get('notebook_path')
        if path:
            path = os.path.join(h.cwd, path)
            repo = repo_at(path)
            for k in sorted(EDIT_PIECES):
                EDIT_PIECES[k](h, repo, path)
    if h.denies:   # printed before the touches: a failed touch (stderr, via main) can't drop a deny
        print(json.dumps({'hookSpecificOutput': {
            'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
            'permissionDecisionReason': 'foreman: ' + '; '.join(dict.fromkeys(h.denies))}}), flush=True)
    for r in h.touch:
        touch_marker(r, h.sid)


def pretool_bash(h, command):
    sc = shellscan.scan(command, h.cwd)
    if h.agent and runs_control(sc, command, h.cwd) and repo_at(h.cwd):
        h.deny('only the orchestrator ends a run or releases a spec — a worker never runs hooks.py')
        return
    call = control_call(command)
    if call and not h.agent:   # a subagent's call is never recorded, whatever the deny above saw
        repo = repo_at(h.cwd)
        if repo:
            pretool_control(h, repo, *call)
        return
    for k in sorted(BASH_PIECES):
        BASH_PIECES[k](h, sc)


def posttool(h):
    ti = h.inp.get('tool_input') or {}
    if h.inp.get('tool_name') == 'Bash':
        if not h.agent and control_call(ti.get('command') or '') == ('run-end', []):
            end_run(h.sid)
        return
    for k in sorted(POST_EDIT):
        POST_EDIT[k](h, ti)


HANDLERS['pretool'] = pretool
HANDLERS['posttool'] = posttool


# ---- dispatcher -----------------------------------------------------------------------------------

def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    event = argv[1]
    if event in CONTROL:
        sys.exit(cli(event, argv[2:]))
    try:
        inp = json.load(sys.stdin)
    except ValueError:
        return
    if not isinstance(inp, dict):
        return
    try:
        ti = inp.get('tool_input')
        command = None
        if inp.get('tool_name') == 'Bash':
            command = ti.get('command') if isinstance(ti, dict) else None
            command = command if isinstance(command, str) else ''
        if event == 'posttool' and command is not None and (inp.get('agent_id') or 'hooks.py run-end' not in command):
            return
        if event == 'pretool' and command is not None and not FAST.search(command):
            return
        _load()
        fn = HANDLERS.get(event)
        if fn:
            fn(Hook(inp))
    except Exception as e:  # fail open
        print(f'foreman hook error: {type(e).__name__}: {e}', file=sys.stderr)


if __name__ == '__main__':
    main(sys.argv)
