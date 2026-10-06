#!/usr/bin/env python3
"""foreman's settings hooks: one dispatcher, run by hooks/hooks.json as `python3 hooks.py <event>`.

Hook mode, JSON on stdin: run-start, session-start, session-end, pretool, posttool, subagent-start, subagent-stop,
permission, stop.
(SessionEnd runs `session-end`, not `run-end`: the CLI verb never reads stdin, which a Bash call may hold open.)

CLI mode, run from the orchestrator's Bash tool. `pretool` records the effect from its own hook input, so the
record lands where the hooks look and is bound to this session:
  hooks.py run-end                 end this session's run (posttool deletes the marker)
  hooks.py release <batch> <sha>   release a spec-phase batch (piece 10)
  hooks.py stopping <batch>        let the tagged workers of <batch> launched so far stop (piece 7)
  hooks.py forget <batch>          drop the batch's hook state (W5)
  exit 0 = recorded, 1 = the record is missing, 3 = hooks inactive (no fresh run marker); run-end always 0

Every hook fails open on its own errors (stderr note, exit 0), and so does each piece of pretool: one piece's crash
costs only its own decision. Three cases fail closed: an undeterminable push diff, a push or merge command the scan
can't read, and a spec-phase commit whose contents can't be told.
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FAST = re.compile(r'\b(git|gh|gt)\b|hooks\.py|batches\.py')
RUN_TTL = 48 * 3600
BATCH_TTL = 14 * 86400
BATCH = re.compile(r'^[A-Za-z0-9_-]+$')
SHA = re.compile(r'^[0-9a-f]{7,40}$')
CONTROL = ('run-end', 'release', 'stopping', 'forget')
CONTROL_TEXT = re.compile(r'hooks\.py\S*\s+(run-end|release|stopping|forget)\b')
RUN_END = re.compile(r'hooks\.py["\']?\s+run-end')   # posttool's pre-filter: one regex per Bash call
BATCH_VERBS = ('set', 'note', 'bump', 'stamp', 'task', 'add-round', 'show', 'table', '--session')
BATCH_TEXT = re.compile(r'batches\.py\S*\s+(set|note|bump|stamp|task|add-round|show|table|--session)\b')
EDIT_TOOLS = ('Edit', 'Write', 'MultiEdit', 'NotebookEdit')
BACKLOG_DEFAULT = '.claude/improvement-backlog.md'
RULES_DEFAULT = '.claude/rules/*.md'
CONFIG_NAMES = ('.claude/foreman.config.md', '.claude/improve-next.config.md')
RUN_PROMPT = re.compile(r'^\s*(?:/loop(?:\s+(?!/)\S+)?\s+)?/foreman(?::foreman)?(?=\s|$)(.*)', re.S)
TAG = re.compile(r'^foreman-worker: ([A-Za-z0-9_-]+)( spec-phase)?\s*$')

HANDLERS = {}      # event -> fn(h)
BASH_PIECES = {}   # order -> fn(h, sc); piece 2 runs first so its local merge deny saves pieces 3 and 4 a network call
EDIT_PIECES = {}   # order -> fn(h, repo, path)
SUBAGENT_EDIT_PIECES = {0, 10}   # the edit pieces that act only for a subagent: a main-session edit skips the load
POST_EDIT = {}     # order -> fn(h, tool_input)


def _load():
    """Imports the fast paths skip: a Bash call that names no git/gh/gt/hooks.py costs one json.load."""
    global backlog, batches, cfg, state, shellscan, subprocess, shlex, glob, tempfile
    sys.path.insert(0, HERE)
    import backlog  # noqa: F401
    import batches  # noqa: F401
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
        self._sids = {}

    def deny(self, reason):
        self.denies.append(reason)

    def run_sid(self, repo):
        """The id this session's run state is kept under in `repo`: its own, or through an alias the run it continues.
        A session with no runs file of its own tries adoption first (L5), once per process."""
        if repo.top not in self._sids:
            sid = batches.canonical(repo.dirs, self.sid)
            if sid == self.sid and BATCH.match(sid) and not any(
                    os.path.exists(os.path.join(d, 'runs', sid + '.json')) for d in repo.dirs):
                sid = adopt(repo.dirs, sid, self.inp.get('transcript_path')) or sid
            self._sids[repo.top] = sid
        return self._sids[repo.top]

    def run_active(self, repo):
        if repo.top not in self._active:
            self._active[repo.top] = marker(repo, self.run_sid(repo)) is not None
        return self._active[repo.top]

    def active(self, repo, piece, run_only=True):
        """True when `piece` applies in `repo`: a foreman repo, not disabled, and (run-only) a live run."""
        return bool(repo) and not repo.disabled(piece) and (not run_only or self.run_active(repo))


def cmd_repo(c, h):
    return repo_at(c.cwd or h.cwd)


# ---- the run marker -------------------------------------------------------------------------------

def marker(repo, sid):
    """(path, data) of the live run marker of `sid`'s run (followed through an alias), or None."""
    if not sid or os.sep in sid:
        return None
    sid = batches.canonical(repo.dirs, sid)
    for d in repo.dirs:
        p = os.path.join(d, 'runs', sid + '.json')
        m = state.read_json(p)
        if isinstance(m, dict) and 'alias' not in m and time.time() - float(m.get('touched', 0)) < RUN_TTL:
            return p, m
    return None


def touch_marker(repo, sid):
    """Touch `sid`'s live run marker, and the alias that leads to it, which prune and the status band age alike."""
    m = marker(repo, sid)
    if m:
        now = time.time()
        state.write_json(m[0], dict(m[1], touched=now))
        for d in repo.dirs:
            p = os.path.join(d, 'runs', sid + '.json')
            a = state.read_json(p)
            if isinstance(a, dict) and 'alias' in a:
                state.write_json(p, dict(a, touched=now))


def continued_in(path, sid, tail=8192):
    """True when the last `tail` bytes of the transcript `path` hold a `continued-in` record naming `sid`."""
    try:
        with open(path, 'rb') as f:
            f.seek(max(0, f.seek(0, os.SEEK_END) - tail))
            lines = f.read().split(b'\n')
    except OSError:
        return False
    for raw in lines:
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get('type') == 'continued-in' and e.get('continuedInSessionId') == sid:
            return True
    return False


def live_run(d, name):
    """True when `runs/<name>.json` in the state dir d is a live run marker (not an alias)."""
    m = state.read_json(os.path.join(d, 'runs', name + '.json'))
    return isinstance(m, dict) and 'alias' not in m and time.time() - float(m.get('touched', 0)) < RUN_TTL


def adopt(dirs, sid, transcript_path):
    """The run the new session `sid` continues, recorded as the alias `runs/<sid>.json` = {alias, touched} beside
    its marker, or None. A session moved to the background, forked or resumed gets a new id, and the old transcript
    (`<old id>.jsonl` beside the new one) ends with a `continued-in` record naming it; nothing in the hook input names
    the old id. The old session is a live run's own (its marker) or one that continued it (an alias of a live marker),
    and the alias written always names the run itself, so `canonical` stays one hop. A runs file that can't be read
    is skipped."""
    if not transcript_path or not BATCH.match(sid):
        return None
    for d in dirs:
        for p in ls(d, 'runs/*.json'):
            old = os.path.basename(p)[:-len('.json')]
            try:
                m = state.read_json(p)
                run = m.get('alias', old)
                ok = old != sid and BATCH.match(old) and BATCH.match(run) and run != sid and live_run(d, run)
            except (AttributeError, TypeError, ValueError):
                continue
            if ok and continued_in(os.path.join(os.path.dirname(transcript_path), old + '.jsonl'), sid):
                state.write_json(os.path.join(d, 'runs', sid + '.json'), {'alias': run, 'touched': time.time()})
                return run
    return None


def prune(sd):
    """Drop markers untouched for 48 hours, owed reports older than that, and batch files older than 14 days."""
    now = time.time()
    for p in ls(sd, 'runs/*.json'):
        m = state.read_json(p)
        if not isinstance(m, dict) or now - float(m.get('touched', 0)) >= RUN_TTL:
            rm(p)
    for p in ls(sd, 'runs/*.owed'):
        if now - os.path.getmtime(p) >= RUN_TTL:
            rm(p)
    for p in ls(sd, 'batches/*/*') + ls(sd, 'batches/*/agents/*') + ls(sd, 'batches/*/state/*'):
        if os.path.isfile(p) and now - os.path.getmtime(p) >= BATCH_TTL:
            rm(p)


def run_args(inp):
    """The /foreman arguments when this prompt starts a run, else None. `ui`, `init` and `seed` aren't runs.
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
    return None if words and words[0] in ('ui', 'init', 'seed') else args


def run_start(h):
    args = run_args(h.inp)
    repo = repo_at(h.cwd) if args is not None and h.sid and os.sep not in h.sid else None
    if not repo:
        return
    sd = repo.dirs[0]
    prune(sd)
    p = os.path.join(sd, 'runs', h.run_sid(repo) + '.json')
    m = state.read_json(p)
    m = m if isinstance(m, dict) else {'started': time.time()}
    m.update(touched=time.time(), args=args)
    m.pop('ended', None)   # a new run here: this session owns the marker again
    state.write_json(p, m)


def alias_target(a):
    """The run an alias file's data names, or None when it isn't a (valid) alias."""
    t = a.get('alias') if isinstance(a, dict) else None
    return t if isinstance(t, str) and BATCH.match(t) else None


def aliases(d, run):
    """The paths of every alias file in the state dir d that names `run`, with their data."""
    return [(p, a) for p, a in ((p, state.read_json(p)) for p in ls(d, 'runs/*.json')) if alias_target(a) == run]


def end_run(sid):
    """Delete this session's run: its marker, or for an alias the marker it names, and every alias of that run (a
    sibling left behind would keep a later run's marker on /clear). A glob, no git: every SessionEnd hook shares 1.5
    seconds. An owed report (`.owed`) stays for the Stop hook."""
    if not sid or os.sep in sid:
        return
    for home in {state.home(), state.DEFAULT_HOME}:
        for p in ls(home, f'*/runs/{glob.escape(sid)}.json'):
            d, run = os.path.dirname(os.path.dirname(p)), alias_target(state.read_json(p)) or sid
            for q, _ in aliases(d, run):
                rm(q)
            rm(os.path.join(d, 'runs', run + '.json'))
            rm(p)


def carried(d, sid):
    """True when a live alias in the state dir d names `sid`'s run: another session carries it on."""
    for _, a in aliases(d, sid):
        try:
            if time.time() - float(a.get('touched', 0)) < RUN_TTL:
                return True
        except (TypeError, ValueError):
            continue
    return False


def leave_run(sid):
    """/clear or logout: this session leaves its run, which another live session may still own. A marker another live
    alias still names stays, marked `ended` (its TTL still runs). An alias is removed, and with it the marker it names
    when no other alias names that marker and its own session ended too (`ended`). A marker whose session moved to the
    background without a SessionEnd can't be told from a live fork's, so it is kept and expires by its TTL. `run-end`
    (end_run) removes all."""
    if not sid or os.sep in sid:
        return
    for home in {state.home(), state.DEFAULT_HOME}:
        for p in ls(home, f'*/runs/{glob.escape(sid)}.json'):
            d, a = os.path.dirname(os.path.dirname(p)), state.read_json(p)
            if isinstance(a, dict) and 'alias' not in a and carried(d, sid):
                state.write_json(p, dict(a, ended=True))
                continue
            rm(p)
            run = alias_target(a)
            target = os.path.join(d, 'runs', f'{run}.json')
            m = state.read_json(target) if run else None
            if isinstance(m, dict) and 'alias' not in m and m.get('ended') and not carried(d, run):
                rm(target)


def session_end(h):
    if h.inp.get('reason') in ('clear', 'logout'):
        leave_run(h.sid)


def session_start(h):
    """Export this session's run id to every later Bash call (probe P1): append `export FOREMAN_SESSION=<id>` to
    $CLAUDE_ENV_FILE, which batches.py reads. Nothing is written when the harness sets no env file. A session that
    continues another under a new id (`source` fork or resume) first adopts that session's run (L5). It still exports
    its own id: batches.py resolves it through the alias while the run lasts, and a later run started in this session
    lives under this id once the alias is gone."""
    sid = h.sid
    if h.inp.get('source') in ('fork', 'resume'):
        repo = repo_at(h.cwd)
        if repo:
            h.run_sid(repo)
    path = os.environ.get('CLAUDE_ENV_FILE')
    if path and BATCH.match(sid):
        with open(path, 'a+', encoding='utf-8') as f:
            f.seek(0)
            text = f.read()
            f.write(('\n' if text and not text.endswith('\n') else '') + f'export FOREMAN_SESSION={sid}\n')


HANDLERS['run-start'] = run_start
HANDLERS['session-start'] = session_start
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


def ends_run(sc):
    """True when a top-level segment is `python3 <this hooks.py> run-end`: any python3 (a path whose basename is
    python3), the path quoted or relative to the segment's directory, redirects allowed."""
    for seg in sc.segs:
        w, i = [], 0
        while i < len(seg.words or []):
            m = shellscan.REDIRECT.match(seg.words[i])
            if m:
                i += 2 if m.end() == len(seg.words[i]) else 1   # `>` alone takes the next word as its target
                continue
            w.append(seg.words[i])
            i += 1
        if len(w) != 3 or os.path.basename(w[0]) != 'python3' or w[2] != 'run-end':
            continue
        p = os.path.expanduser(w[1])
        if (os.path.isabs(p) or seg.cwd) and same_file(os.path.join(seg.cwd or '', p), os.path.join(HERE, 'hooks.py')):
            return True
    return False


def runs_control(sc, text, cwd, name='hooks.py', verbs=CONTROL, rx=CONTROL_TEXT):
    """True when the scanned command runs `<path>/<name> <verb>` as a command (by default this hooks.py and its
    control verbs; piece 11 asks about batches.py): a word naming the script (its basename, or the same realpath as
    control_call tests, so `hooks.py/`, `hooks.py/.` and a symlink count) immediately followed by the verb, in a
    top-level segment or in the script a shell or eval is given. A mention in a quoted string, a comment or a
    heredoc isn't a call. Text the scan couldn't read is
    judged by its raw text. Not seen (accepted limit): a call built at run time, in a $(...) or fed to a shell's
    stdin. It tests a path the way control_call does, so a deny sees every call that would be recorded."""
    if not sc.ok:
        return bool(rx.search(text.replace('\\\n', '')))
    for seg in sc.segs:
        w = seg.words
        if w is None:
            if rx.search(text[seg.start:seg.end].replace('\\\n', '')):
                return True
            continue
        if any(b in verbs and (os.path.basename(a) == name or same_file(a, os.path.join(HERE, name)))
               for a, b in zip(w, w[1:])):
            return True
        if any(os.path.basename(a) in shellscan.SHELLS + ('eval',) for a in w):
            for script in (a for a in w if re.search(r'\s', a)):
                if runs_control(shellscan.scan(script, seg.cwd), script, seg.cwd, name, verbs, rx):
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
    d, sid = repo.dirs[0], h.run_sid(repo)
    if verb == 'release':
        state.write_json(batch_file(d, sid, rest[0], 'released'), {'sha': rest[1], 'time': time.time()})
    elif verb == 'stopping':
        state.write_json(batch_file(d, sid, rest[0], 'stopped'), {'time': time.time()})
    elif verb == 'forget':
        forget(repo, sid, rest[0])
    touch_marker(repo, h.sid)


def fresh_marker(dirs, batch):
    """A live run marker in any session, touched after the batch name's time."""
    since = batches.batch_time(batch)
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
    sid = h.run_sid(repo)
    entry = agent_entry(repo, sid, h.agent)
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
    state.write_json(os.path.join(repo.dirs[0], 'batches', sid, 'agents', h.agent), entry)
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
    return (c.prog, c.sub) in (('git', 'push'), ('gh', 'pr create'), ('gh', 'pr new'), ('gt', 'submit'))


# commands that move HEAD; `git checkout -- <path>` is an accepted false deny. shellscan reads only gt's s, ss, m and c
# aliases, so the others (co, u, d, sq, ab, r) and gh's `pr co` are listed as spelled
HEAD_MOVERS = ({('git', s) for s in ('commit', 'merge', 'pull', 'rebase', 'cherry-pick', 'revert', 'am', 'reset',
                                     'checkout', 'switch', 'update-ref', 'commit-tree')}
               | {('gt', s) for s in ('create', 'modify', 'sync', 'squash', 'sq', 'absorb', 'ab', 'fold', 'restack',
                                      'checkout', 'co', 'up', 'u', 'down', 'd', 'r')}
               | {('gh', 'pr checkout'), ('gh', 'pr co')})


def moved_reason(repo):
    return ("a push after a commit or merge in the same call is judged by the HEAD before it — commit (or merge) in "
            f"one call, re-run the gate if it touched non-exempt paths (`{HERE}/gate-lock.sh <owner> -- "
            f"'{repo.get('gate') or '<gate>'}'` from the checkout's top level), then push in its own call")


def gate_before_push(h, sc):
    gate_in_call = any(s.words and any(os.path.basename(w) == 'gate-lock.sh' for w in s.words) for s in sc.segs)
    for k, c in enumerate(sc.cmds):
        if not is_push_trigger(c):
            continue
        repo = cmd_repo(c, h)
        if not h.active(repo, 'gate_before_push'):
            continue
        h.touch.add(repo)
        if c.args is None or c.cwd is None or gate_in_call:
            h.deny(UNREADABLE)
        elif any((m.prog, m.sub) in HEAD_MOVERS for m in sc.cmds[:k]):   # the receipt check reads the HEAD before the call
            h.deny(moved_reason(repo))
        elif c.sub == 'push' and shellscan.push_info(c.args, default_branch(c.cwd))[0]:
            continue
        else:
            why = receipt_check(repo, c.cwd)
            if why:
                h.deny(why)


BASH_PIECES[1] = gate_before_push


# ---- option helpers and the shared `gh pr view` (pieces 3 and 4) ----------------------------------

def opt_values(args, names):
    """Every value of the options in `names`: `--x v`, `--x=v`, `-x v`, `-xv`, and a short option ending a
    flag cluster (`-am msg`)."""
    out, i = [], 0
    shorts = [n[1] for n in names if len(n) == 2 and n[0] == '-' and n[1] != '-']
    while i < len(args):
        a = args[i]
        if a == '--':
            break
        nxt = args[i + 1] if i + 1 < len(args) else None
        if a in names:
            if nxt is not None:
                out.append(nxt)
            i += 2
            continue
        long = next((n for n in names if n.startswith('--') and a.startswith(n + '=')), None)
        if long:
            out.append(a[len(long) + 1:])
        elif re.match(r'^-[A-Za-z]', a) and not a.startswith('--'):
            k = next((j for j, ch in enumerate(a[1:], 1) if ch in shorts), None)
            if k is not None:
                if a[k + 1:]:
                    out.append(a[k + 1:])
                elif nxt is not None:
                    out.append(nxt)
                    i += 1
        i += 1
    return out


def has_flag(args, names):
    """True when a boolean flag in `names` is present, alone, inside a short-flag cluster (`-sd`) or as
    `--flag=true`; `--flag=false` is not."""
    shorts = {n[1] for n in names if len(n) == 2 and n[0] == '-' and n[1] != '-'}
    for a in args:
        if a == '--':
            break
        name, eq, val = a.partition('=')
        if a in names or (eq and name in names and val.lower() not in ('false', 'f', '0')) \
                or (re.match(r'^-[A-Za-z]+$', a) and shorts & set(a[1:])):
            return True
    return False


GH_MERGE_VALUE_OPTS = ('-t', '--subject', '-b', '--body', '-F', '--body-file', '--match-head-commit', '-A',
                       '--author-email')


# a heredoc, a here-string or `< file`; group 1 is the target glued to the operator ('' when it is the next word)
STDIN_REDIRECT = re.compile(r'^\d*(?:<<<?-?|<(?![<>(]))(.*)$')


def positionals(args, value_opts):
    """The words that are neither options, their values nor a stdin redirect: `git commit -F - <<EOF` feeds its
    message to git, it names no path. An output redirect is not skipped: the shell writes the file first."""
    out, i, opts = [], 0, True
    while i < len(args):
        a = args[i]
        m = STDIN_REDIRECT.match(a)
        if m:
            i += 1 if m.group(1) else 2   # a bare `<<` or `<` takes the next word as its delimiter or file
            continue
        if opts and a == '--':
            opts = False
        elif opts and a in value_opts:
            i += 1
        elif not opts or not a.startswith('-'):
            out.append(a)
        i += 1
    return out


_pr_cache = {}


def pr_view(c):
    """`gh pr view <n> --json headRefName,commits` for a `gh pr merge` candidate, cached per call; None when gh
    fails or answers anything but an object (the checks that need it then don't apply)."""
    n = positionals(c.args, GH_MERGE_VALUE_OPTS)[:1]
    key = (c.cwd, c.ghrepo, tuple(n))
    if key not in _pr_cache:
        argv = ['gh', 'pr', 'view', *n, '--json', 'headRefName,commits'] + (['-R', c.ghrepo] if c.ghrepo else [])
        try:
            p = subprocess.run(argv, cwd=c.cwd, capture_output=True, text=True, timeout=4)
            view = json.loads(p.stdout) if p.returncode == 0 else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            view = None
        _pr_cache[key] = view if isinstance(view, dict) else None
        if _pr_cache[key] is None:
            print('foreman: gh pr view failed; the merge checks that need it are skipped', file=sys.stderr)
    return _pr_cache[key]


def worktrees(cwd):
    """[(path, branch ref or None)] from `git worktree list --porcelain`; [] when git fails."""
    listing = state.git(cwd, 'worktree', 'list', '--porcelain')
    if listing is None:
        print('foreman: git worktree list failed; the worktree check is skipped', file=sys.stderr)
    out = []
    for line in (listing or '').splitlines():
        if line.startswith('worktree '):
            out.append([line[len('worktree '):], None])
        elif line.startswith('branch ') and out:
            out[-1][1] = line[len('branch '):]
    return [tuple(w) for w in out]


# ---- piece 2: worker guard ------------------------------------------------------------------------

NO_MERGE = 'workers never merge — the worker contract: "Never merge."; report the PR and the orchestrator merges it'
NO_FORCE = ('no force-push — the worker contract: "never amend or force-push once the PR is open"; push a '
            'follow-up commit')
NO_REBASE = 'no rebase — the worker contract: "merge `origin/main` into it, do not rebase"'
NO_PUSH_DEFAULT = ('no push to {default} — the worker contract: "Never merge." (a direct push to the default branch '
                   'is an unreviewed merge); push your own branch and open the PR')
NO_AMEND = ('no gt modify once the branch has an upstream — the worker contract: "never amend or force-push once '
            'the PR is open"; commit a follow-up instead')
NO_BOOKKEEPING = ('workers don\'t edit the backlog, the rules files or the foreman config — the worker contract: '
                  '"Step 10: do not edit the backlog, the rules files or generated files"; report RULE NOTES and '
                  'NEW ROWS instead')


def merges(c):
    return ((c.prog, c.sub) in (('gh', 'pr merge'), ('gt', 'merge'))
            or ((c.prog, c.sub) == ('gh', 'api') and shellscan.gh_api_merge(c.args, subagent=True)))


def worker_violation(c):
    if merges(c):
        return NO_MERGE
    if shellscan.is_rebase(c):
        return NO_REBASE
    if (c.prog, c.sub) == ('git', 'push') and c.args is not None:
        default = default_branch(c.cwd) if c.cwd else 'main'
        _, force, to_default = shellscan.push_info(c.args, default)
        if force:
            return NO_FORCE
        if to_default:
            return NO_PUSH_DEFAULT.format(default=default)
    if (c.prog, c.sub) == ('gt', 'modify') and c.cwd and state.git(c.cwd, 'rev-parse', '--abbrev-ref', '@{u}'):
        return NO_AMEND
    return None


def worker_guard(h, sc):
    if not h.agent:
        return
    for c in sc.cmds:
        repo = cmd_repo(c, h)
        if repo is None and merges(c):   # a merge names its repo with -R or the API path, not its directory
            repo = repo_at(h.cwd)
        if not h.active(repo, 'worker_guard'):
            continue
        why = worker_violation(c)
        if why:
            h.touch.add(repo)
            h.deny(why)
            c.denied = c.denied or why == NO_MERGE


def bookkeeping_target(repo, path):
    rel = repo.rel(path)
    return rel == os.path.normpath(repo.backlog) or rel in CONFIG_NAMES or any(glob_match(rel, g) for g in repo.rules)


def worker_guard_edit(h, repo, path):
    if h.agent and h.active(repo, 'worker_guard') and bookkeeping_target(repo, path):
        h.touch.add(repo)
        h.deny(NO_BOOKKEEPING)


BASH_PIECES[0] = worker_guard
EDIT_PIECES[0] = worker_guard_edit


# ---- piece 3: merge discipline --------------------------------------------------------------------

def merge_discipline(h, sc):
    for c in sc.cmds:
        if (c.prog, c.sub) not in (('gh', 'pr merge'), ('gt', 'merge')):
            continue
        repo = cmd_repo(c, h)
        if not h.active(repo, 'merge_discipline'):
            continue
        h.touch.add(repo)
        if c.denied:   # piece 2 already told this worker never to merge; telling it how to would undo that
            continue
        if c.prog == 'gt':
            h.deny('no gt merge during a run — merge with `gh pr merge <n> --match-head-commit <sha>`')
        elif c.args is None or c.cwd is None:
            h.deny("merge the SHA you verified — foreman couldn't read this merge; run `gh pr merge <n> "
                   "--match-head-commit <sha>` in its own call")
        else:
            if not any(opt_values(c.args, ('--match-head-commit',))):
                h.deny('merge the SHA you verified — add `--match-head-commit <sha>`')
            if has_flag(c.args, ('--delete-branch', '-d')):
                holder = other_worktree(c)
                if holder:
                    h.deny(f'remove the worktree at {holder} first, then delete the branch — it still holds the '
                           "PR's head branch")


def other_worktree(c):
    """A worktree other than the command's own that has the PR's head branch checked out, or None."""
    head = (pr_view(c) or {}).get('headRefName')
    if not head:
        return None
    own = os.path.realpath(state.toplevel(c.cwd) or c.cwd)
    for path, branch in worktrees(c.cwd):
        if branch == f'refs/heads/{head}' and os.path.realpath(path) != own:
            return path
    return None


BASH_PIECES[3] = merge_discipline


# ---- piece 4: trailer guard -----------------------------------------------------------------------

MESSAGE_OPTS = {
    'git': {'commit': (('-m', '--message', '--trailer'), ('-F', '--file')),
            'merge': (('-m', '--message'), ('-F', '--file'))},
    'gh': {s: (('-t', '--title', '-b', '--body', '--subject'), ('-F', '--body-file'))
           for s in ('pr create', 'pr new', 'pr edit', 'pr comment', 'pr merge', 'pr review')},
    'gt': {s: (('-m', '--message'), ()) for s in ('create', 'modify', 'submit')},
}


def piped_docs(sc, c):
    """The heredoc bodies of the pipeline stages before the top-level command c. shellscan attaches a heredoc only
    to the command it is written on, so `cat <<EOF | gh pr create --body-file -` doesn't reach c.heredocs."""
    i = next((k for k, s in enumerate(sc.segs) if (s.start, s.end) == (c.start, c.end)), 0)
    out = []
    while i and sc.segs[i - 1].sep in ('|', '|&'):
        i -= 1
        out += [d.body for d in sc.docs if sc.segs[i].start <= d.op < sc.segs[i].end]
    return out


def message_texts(c, sc, h):
    """The message texts a commit/PR/comment command would write, or None when it writes none."""
    opts = MESSAGE_OPTS.get(c.prog, {}).get(c.sub)
    if opts is None:
        return None
    if c.args is None:   # unreadable: the raw text, and again with each quote opening a line (`-m "Co-Authored-By: x`)
        return [c.text, re.sub(r'["\']', '\n', c.text)]
    texts = opt_values(c.args, opts[0]) + list(c.heredocs)
    for f in opt_values(c.args, opts[1]):
        if f == '-':
            texts += piped_docs(sc, c)
            continue
        path = os.path.join(c.cwd or h.cwd, os.path.expanduser(f))
        written = [d.body for d in sc.docs if d.target == os.path.normpath(path)]
        texts += written   # a heredoc earlier in the call may rewrite or extend the file: read it and the copy on disk
        if os.path.isfile(path):
            try:
                with open(path, encoding='utf-8', errors='replace') as fh:
                    texts.append(fh.read(1 << 20))
            except OSError as e:   # unreadable: skip this file, not the whole call (main would fail open on every piece)
                print(f'foreman: trailer guard skipped {f} ({e.strerror})', file=sys.stderr)
        elif not written:
            print(f'foreman: trailer guard skipped {f} (it does not exist yet)', file=sys.stderr)
    return texts


def find_trailer(rules, texts):
    """The first forbid_trailers entry that starts a line of the texts (after non-word characters, any case);
    an entry written /.../ is a regex searched within each line."""
    for entry in rules:
        e = str(entry)
        if len(e) > 2 and e.startswith('/') and e.endswith('/'):
            try:
                rx = re.compile(e[1:-1])
            except re.error:
                print(f'foreman: bad forbid_trailers regex {e}', file=sys.stderr)
                continue
            if any(rx.search(line) for t in texts for line in t.splitlines()):
                return e
        elif any(re.search(r'(?im)^[^\w\n]*' + re.escape(e), t) for t in texts):
            return e
    return None


def merge_method(args):
    if has_flag(args, ('--squash', '-s')):
        return 'squash'
    if has_flag(args, ('--rebase', '-r')):
        return 'rebase'
    return 'merge'


def trailer_guard(h, sc):
    for c in sc.cmds:
        if MESSAGE_OPTS.get(c.prog, {}).get(c.sub) is None:   # before cmd_repo: it spawns git
            continue
        repo = cmd_repo(c, h)
        if not h.active(repo, 'trailer_guard', run_only=False):
            continue
        rules = repo.list('forbid_trailers')
        texts = message_texts(c, sc, h) if rules else None
        if texts is None:
            continue
        hit = find_trailer(rules, texts)
        if hit:
            h.deny(f'no forbidden trailer lines — a message line starts with "{hit}" (forbid_trailers); remove it')
        if (c.prog, c.sub) == ('gh', 'pr merge') and c.args is not None and not c.denied:
            bad = [m for m in (pr_view(c) or {}).get('commits') or []
                   if find_trailer(rules, [f"{m.get('messageHeadline', '')}\n{m.get('messageBody', '')}"])]
            method = merge_method(c.args)
            if bad and not (method == 'squash' and opt_values(c.args, ('-b', '--body', '-F', '--body-file'))):
                fix = ('pass --body (and --subject) without it' if method == 'squash'
                       else 'squash with a --body without it; merge and rebase keep every commit message')
                h.deny(f"the PR's commit {str(bad[0].get('oid', ''))[:9]} carries a forbidden trailer — {fix}")


BASH_PIECES[4] = trailer_guard


# ---- piece 5: backlog row check -------------------------------------------------------------------

def backlog_problems(text, prefix):
    """(the number of rows with a problem, ['<id>: <problem>', ...]). A row counts once, however many problems
    name it: a duplicated id puts every row that carries it among the problem rows."""
    bl = backlog.parse(text, prefix)
    pat = backlog.row_pattern(prefix)
    dups = bl.duplicates()
    rows, out = set(), []
    for n, line in enumerate(bl.lines):
        raw = (line.raw if isinstance(line, backlog.Row) else str(line)).rstrip('\r')
        m = pat.match(raw)
        if m and f'{prefix}-{m.group(1)}' in dups:
            rows.add(n)
        if m or raw.startswith('| NEW-'):
            for p in backlog.line_problems(raw):
                rows.add(n)
                out.append(f'{raw.split(" | ")[0][2:]}: {p[1]} " | " separators, want 5')
    out += [f'{id_}: on {count} rows' for id_, count in dups.items()]
    return len(rows), out


def backlog_check(h, ti):
    if h.inp.get('tool_name') not in ('Edit', 'Write', 'MultiEdit'):
        return
    path = ti.get('file_path')
    path = os.path.join(h.cwd, path) if path else None
    repo = repo_at(path) if path else None
    if not h.active(repo, 'backlog_check', run_only=False) or repo.rel(path) != os.path.normpath(repo.backlog):
        return
    with open(path, encoding='utf-8', errors='replace', newline='') as f:
        rows, probs = backlog_problems(f.read(), str(repo.get('id_prefix', 'IB')))
    if probs:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext':
              f'foreman: the backlog now has {rows} problem rows: {"; ".join(probs[:10])}. '
              'Fix them before committing.'}}))


POST_EDIT[5] = backlog_check


# ---- worker tags (pieces 6 and 7) -----------------------------------------------------------------

FENCE = re.compile(r'```json[ \t]*\r?\n(.*?)```', re.S)
SKILL = os.path.join(HERE, '..', 'skills', 'foreman', 'SKILL.md')
BRIEF_CAP = 8192
CUT = ' …(truncated)'


def is_worker(repo, h):
    return h.inp.get('agent_type') in repo.list('worker_agent_types')


# ---- piece 6: worker briefing ---------------------------------------------------------------------

def section(text, heading):
    m = re.search(rf'^{re.escape(heading)}\n(.*?)(?=^#{{1,3}} |\Z)', text, re.S | re.M)
    return m.group(1).strip() if m else ''


def default_table(text):
    """{key: default} from SKILL.md's Step 0.5 table, preferring the default's first backticked span."""
    out = {}
    for line in text.splitlines():
        cells = line.strip().strip('|').split(' | ')
        m = re.fullmatch(r'\s*`(\w+)`\s*', cells[0]) if len(cells) == 3 else None
        if m:
            code = re.search(r'`([^`]+)`', cells[2])
            out[m.group(1)] = code.group(1) if code else cells[2].strip()
    return out


def briefing(repo, batch, spec, sid):
    with open(SKILL, encoding='utf-8') as f:
        skill = f.read()
    defaults = default_table(skill)

    def key(m):
        v = repo.cfg.get(m.group(1))
        v = defaults.get(m.group(1), m.group(0)) if v is None else v
        return ', '.join(map(str, v)) if isinstance(v, list) else str(v)

    text = re.sub(r'\{\{(\w+)\}\}', key, section(skill, '### Worker contract'))
    text = (text.replace('$FOREMAN', HERE).replace('<prefix>', str(repo.get('id_prefix', 'IB')))
            .replace('<batch>', batch))
    text += ('\n\n<scratch> = your scratchpad dir; <worktree> and {worktree} = your current checkout\'s top level; '
             '{repo} = the main checkout')
    head = ['# foreman worker briefing (batch ' + batch + ')', text, '',
            f"Gate (run it under {HERE}/gate-lock.sh {batch} -- '<gate>'): {repo.get('gate') or '(none configured)'}",
            'Forbidden trailer lines: ' + (', '.join(map(str, repo.list('forbid_trailers'))) or 'none')]
    rel = released(repo, sid, batch)
    if not spec and rel:
        head.append(f"Your batch's spec is already released (`{rel.get('sha')}`): skip the spec phase and continue "
                    'the build phase on the existing branch.')
    out = '\n'.join(head) + '\n'
    items = repo.list('shared_resources')
    shared = '\n'.join(['', 'Shared resources:'] + [f'- {s}' for s in items]) if items else ''
    if shared and len((out + shared).encode()) > BRIEF_CAP:
        cut = BRIEF_CAP - len(out.encode()) - len(CUT.encode())
        shared = shared.encode()[:max(cut, 0)].decode('utf-8', 'ignore') + CUT
    return out + shared


def take_briefing(h, repo):
    """The briefing for a tagged worker, once: None when the agent has no tag, was briefed already or briefing is
    off. SubagentStart and the worker's first PreToolUse both call it, because the tag can't be read at
    SubagentStart (probe P2); whichever sees the worker first delivers. A `.briefed` file made with O_EXCL is the
    claim: two parallel first calls both read `briefed: false`, and only one creates it."""
    entry = resolve_agent(repo, h)
    if not entry or not entry.get('batch') or entry.get('briefed') or repo.disabled('worker_briefing'):
        return None
    text = briefing(repo, entry['batch'], bool(entry.get('spec')), h.run_sid(repo))
    path = os.path.join(repo.dirs[0], 'batches', h.run_sid(repo), 'agents', h.agent)
    state.ensure(os.path.dirname(path))
    try:
        fd = os.open(path + '.briefed', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return None
    with os.fdopen(fd, 'w') as f:
        json.dump({'batch': entry['batch']}, f)   # a batch name, so `forget` drops the claim with the entry
    state.write_json(path, dict(entry, briefed=True))
    return text


def worker_briefing(h):
    repo = repo_at(h.cwd) if h.agent else None
    return take_briefing(h, repo) if repo and is_worker(repo, h) else None


def subagent_start(h):
    ctx = worker_briefing(h)
    if ctx:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SubagentStart', 'additionalContext': ctx}}))


HANDLERS['subagent-start'] = subagent_start


# ---- piece 7: report check ------------------------------------------------------------------------

def shape_problem(rep):
    """The first field of a worker's json report that bk.py would crash on, or None."""
    if not isinstance(rep, dict):
        return 'the block'
    for k in ('done', 'append'):
        v = rep.get(k, [])
        if not (isinstance(v, list) and all(isinstance(x, dict) and isinstance(x.get('id'), str)
                                            and isinstance(x.get('note'), str) for x in v)):
            return k
    v = rep.get('new_rows', [])
    if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
        return 'new_rows'
    v = rep.get('rule_notes', [])
    if not (isinstance(v, list) and all(isinstance(x, dict) and all(isinstance(x.get(f), str)
                                                                    for f in ('file', 'old', 'new')) for x in v)):
        return 'rule_notes'
    return None


def report_problem(msg, cwd):
    blocks = FENCE.findall(msg)
    if not blocks:
        return "end your report with ONE fenced json block in the worker contract's format, even if empty"
    try:
        rep = json.loads(blocks[-1])
    except ValueError as e:
        return f'the json block at the end of your report does not parse ({e}); fix it'
    bad = shape_problem(rep)
    if bad:
        return (f'the json block has a bad "{bad}" field: done/append are lists of {{"id", "note"}} strings, '
                'new_rows a list of row strings, rule_notes a list of {"file", "old", "new"}')
    rep.pop('rule_notes', None)
    rep['pr'] = 0
    top = state.toplevel(cwd) or cwd
    fd, tmp = tempfile.mkstemp(suffix='.json')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(rep, f)
        env = {k: v for k, v in os.environ.items() if k != 'FOREMAN_CONFIG'}
        p = subprocess.run([sys.executable, os.path.join(HERE, 'bk.py'), top, '--dry-run', '--no-fetch', tmp],
                           capture_output=True, text=True, timeout=20, env=env)
    except subprocess.TimeoutExpired:
        print('foreman: report check skipped (bk.py timed out)', file=sys.stderr)
        return None
    finally:
        os.remove(tmp)
    if 'PROBLEMS (nothing written)' in p.stdout:
        lines = [l.strip() for l in p.stdout.split('PROBLEMS (nothing written):', 1)[1].splitlines() if l.strip()]
        return 'bookkeeping would refuse your json block: ' + '; '.join(lines)
    if p.returncode:
        print(f'foreman: report check skipped (bk.py exit {p.returncode}): {p.stderr.strip()[-300:]}', file=sys.stderr)
    return None


def stopping_allowed(repo, h, batch):
    """True when `hooks.py stopping <batch>` ran after this worker started (piece 7, probe P7)."""
    stop = stopped_record(repo, h.run_sid(repo), batch)
    if stop is None:
        return False
    entry = agent_entry(repo, h.run_sid(repo), h.agent)
    born = (entry or {}).get('time') or transcript_head(h.inp.get('agent_transcript_path') or '')[1]
    return born is None or float(born) < float(stop.get('time', 0))


def subagent_stop(h):
    if h.inp.get('stop_hook_active'):
        return
    repo = repo_at(h.cwd)
    if not h.active(repo, 'report_check', run_only=False) or not is_worker(repo, h):
        return
    tag = worker_tag(transcript_head(h.inp.get('agent_transcript_path') or '')[0])
    if not tag:
        return
    batch, spec = tag
    if stopping_allowed(repo, h, batch):
        return
    msg = h.inp.get('last_assistant_message') or ''
    if spec and not released(repo, h.run_sid(repo), batch):
        if any(line.startswith('SPEC READY ') for line in msg.splitlines()):
            return
        why = ("you're in the spec phase: finish the spec and end with `SPEC READY <worktree> <branch> <spec path> "
               '<sha>` as the first line')
    else:
        why = report_problem(msg, h.cwd)
    if why:
        print(json.dumps({'decision': 'block', 'reason': 'foreman: ' + why}))


HANDLERS['subagent-stop'] = subagent_stop


# ---- piece 8: script allow ------------------------------------------------------------------------

SCRIPTS = ('gate-lock.sh', 'wt-setup.sh', 'review.sh', 'bk.py', 'hooks.py', 'batches.py')
OWN_OPTS = ('--', '--check', '--spec', '--dry-run')
BAD_TEXT = re.compile(r'[\n\r`<>]|\$\(|\$\{')
BAD_ARG = re.compile(r'[\'"$`\\;|&<>\x00-\x1f\x7f]')
INTERPRETERS = {'python3': '.py', 'bash': '.sh', 'sh': '.sh'}   # a script runs bare or under its own interpreter
EXPANDS = re.compile(r'[$*?\[{]')   # the shell expands these in a bare word, so the word isn't the path it names


def bare_word(command, seg, word):
    """True when `word` is written without quotes as the segment's first word, or right after an interpreter:
    the only place the shell expands a leading `~`."""
    raw = command[seg.start:seg.end]
    return re.match(r'\s*(?:(?:python3|bash|sh)\s+)?' + re.escape(word), raw) is not None


def plain(arg):
    return not BAD_ARG.search(arg) and (not arg.startswith('-') or arg in OWN_OPTS)


def is_worktree(repo, path):
    return os.path.realpath(path) in {os.path.realpath(p) for p, _ in worktrees(repo.main)}


def gate_args(repo):
    """The commands gate-lock.sh may be given as its one argument: `gate`, or `post_merge_check` (mints nothing)."""
    both = (repo.get('gate'), repo.get('post_merge_check') or repo.get('gate'))
    return {g for g in both if isinstance(g, str) and g}


def script_args_ok(name, args, repo, h, cwd):
    if name == 'gate-lock.sh':
        return len(args) == 3 and plain(args[0]) and args[1] == '--' and args[2] in gate_args(repo)
    if name == 'batches.py':   # the orchestrator's state writes: a known command, every argument one plain word
        return (not h.agent and bool(args) and args[0] in BATCH_VERBS
                and all(plain(a) or a in ('--json', '--session') for a in args))
    if not all(plain(a) for a in args):
        return False
    if name == 'wt-setup.sh':
        rest = args[1:] if args[:1] == ['--check'] else args
        return len(rest) == 1 and rest[0] not in OWN_OPTS and is_worktree(repo, os.path.join(cwd, rest[0]))
    if name == 'review.sh':
        rest = args[2:] if args[:1] == ['--spec'] and len(args) > 1 else args
        return len(rest) == 2 and not set(rest) & set(OWN_OPTS) and is_worktree(repo, cwd)
    if name == 'bk.py':
        return (not h.agent or '--dry-run' in args) and len([a for a in args if a not in OWN_OPTS]) >= 2
    return not h.agent and control_words([os.path.join(HERE, 'hooks.py')] + args) is not None


def script_allowed(h, command):
    """True when `command` runs one of foreman's own scripts in a strict shape (piece 8 never denies)."""
    sc = shellscan.scan(command, h.cwd)
    if not sc.ok or any(s.words is None for s in sc.segs):
        return False
    b = sc.blanked.strip()
    if (b.count('(') or b.count(')')) and not (b.count('(') == b.count(')') == 1 and b[0] == '(' and b[-1] == ')'):
        return False
    segs = [s for s in sc.segs if s.words]
    if len(segs) == 2 and segs[0].words[0] == 'cd' and len(segs[0].words) == 2 and segs[0].sep == '&&':
        cmd = segs[1]
    elif len(segs) == 1:
        cmd = segs[0]
    else:
        return False
    cd = segs[0] if cmd is not segs[0] else None
    if any(s.sep not in (None, '(', ')') for s in sc.segs if s is not cd):
        return False
    interp = cmd.words[0] if cmd.words[0] in INTERPRETERS else None
    words = cmd.words[1:] if interp else cmd.words
    name = os.path.basename(words[0]) if words else ''
    if name not in SCRIPTS or cmd.cwd is None or EXPANDS.search(words[0]):
        return False
    if interp and not name.endswith(INTERPRETERS[interp]):
        return False
    exe = words[0]
    if exe.startswith('~') and bare_word(command, cmd, exe):   # a quoted `~` stays literal, as in the shell
        exe = os.path.expanduser(exe)
    exe = os.path.join(cmd.cwd, exe)   # a relative path is the shell's, not the hook's
    if not same_file(exe, os.path.join(HERE, name)):
        return False
    repo = repo_at(cmd.cwd)
    if not h.active(repo, 'script_allow'):
        return False
    bare = command
    if name == 'gate-lock.sh':   # a configured gate may hold `<`, `>` or `2>&1`: its quoted argument is exempt
        for g in gate_args(repo):
            bare = bare.replace(f"'{g}'", '')
    return not BAD_TEXT.search(bare) and script_args_ok(name, words[1:], repo, h, cmd.cwd)


def permission(h):
    ti = h.inp.get('tool_input')
    command = ti.get('command') if isinstance(ti, dict) else None
    if h.inp.get('tool_name') == 'Bash' and isinstance(command, str) and script_allowed(h, command):
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PermissionRequest',
                                                 'decision': {'behavior': 'allow'}}}))


HANDLERS['permission'] = permission


# ---- piece 10: spec gate --------------------------------------------------------------------------

GIT_COMMIT_VALUE_OPTS = ('-m', '--message', '-F', '--file', '-C', '--reuse-message', '-c', '--reedit-message', '-t',
                         '--template', '--author', '--date', '--cleanup', '--fixup', '--squash', '--trailer',
                         '--pathspec-from-file')
GT_VALUE_OPTS = ('-m', '--message', '-o', '--onto')   # the short options `gt create` and `gt modify` give a value to
COMMITS = (('git', 'commit'), ('gt', 'create'), ('gt', 'modify'))
OWN_CALL = 'commit in its own call (after `git add <spec path>`), so foreman can see what it commits'


def gated_batch(h, repo):
    """The batch when this subagent is a spec-phase worker whose batch isn't released, else None. resolve_agent
    fills a missing map entry from the worker's transcript (P2), so the gate holds from the worker's first call,
    whichever other piece denied it or briefed it."""
    if not h.agent or not h.active(repo, 'spec_gate'):
        return None
    e = resolve_agent(repo, h)
    if not e or not e.get('spec') or released(repo, h.run_sid(repo), e.get('batch', '')):
        return None
    return e['batch']


def spec_reason(batch):
    return (f"batch {batch}'s spec isn't released yet. Finish the spec, put `SPEC READY <worktree> <branch> "
            "<spec path> <sha>` as your final message's first line, and wait for the orchestrator's release")


def in_spec_dir(repo, rel):
    d = os.path.normpath(str(repo.get('design_spec_dir'))).strip('/')
    return rel == d or rel.startswith(d + '/')


class Unreadable(Exception):
    """git couldn't say which paths a commit would include."""


def git_names(top, *args):
    out = state.git(top, *args)
    if out is None:
        raise Unreadable(' '.join(args))
    return out.split('\0')


def diff_names(top, *args):
    """Top-level-relative paths of `git diff --name-only`. NUL-separated: without -z git quotes a path with
    non-ASCII characters, and the quoted form is never inside a directory. --no-renames: a move into the spec
    directory would otherwise name only its new path, hiding the file it deleted."""
    return [p for p in git_names(top, 'diff', '--name-only', '-z', '--no-renames', *args) if p]


def changed_paths(top, everything):
    """Top-level-relative paths: modified tracked files, plus untracked ones when `everything`."""
    if not everything:
        return diff_names(top)
    return [t[3:] for t in git_names(top, 'status', '--porcelain', '-uall', '-z', '--no-renames') if len(t) > 3]


def under(top, cwd, path):
    """`path` as written in `cwd`, relative to the checkout's top level. A path the shell expands at run time (`$(...)`,
    backticks, `$VAR`, `{a,../b}`, a leading `~`; the scan blanks a substitution with NUL bytes) or git resolves against
    the top level (`:/x`, `:(top)x`, `:^x`) can be anything: Unreadable."""
    if re.search('[${`' + shellscan.FILL + ']', path) or path.startswith(('~', ':')):
        raise Unreadable(path)
    return os.path.relpath(os.path.realpath(os.path.join(cwd, path)), os.path.realpath(top))


def commit_words(args, value_opts=GIT_COMMIT_VALUE_OPTS):
    """A commit command's words with each short-flag cluster split as it reads them, left to right: the first letter
    that takes a value takes the rest of the cluster as that value (`-mtest` is `-m test`), or the next word when it
    ends the cluster (`-am msg` is `-a -m msg`)."""
    out = []
    for a in args:
        k = None
        if re.match(r'^-[A-Za-z]+$', a):
            k = next((i for i, ch in enumerate(a) if i and '-' + ch in value_opts), None)
        if k is None:
            out.append(a)
        else:
            out += (['-' + a[1:k]] if k > 1 else []) + ['-' + a[k]] + ([a[k + 1:]] if a[k + 1:] else [])
    return out


def own_command(c, seg):
    """True when c is the program of the segment's own words, behind nothing but `VAR=value` assignments and `env`.
    A command found inside `bash -c`, `eval` or a shell's heredoc takes its wrapper's segment, and the wrapper's script
    runs (and may write) before it. Any other prefix wraps it too: `xargs git commit` appends the pathspecs it reads
    from stdin or a file, and a redirect (`> app.py git commit -am x`) writes before the commit reads the tree. An
    assignment runs a substitution in its value first (`X=$(echo x > app.py) git commit`, also `<(...)`, backticks, and
    those inside quotes, which the scan leaves as text): only a plain value counts."""
    pre = c.pre or []
    return (all((shellscan.ASSIGN.match(w) and not any(x in w for x in (shellscan.FILL, '`', '$('))) or w == 'env'
                for w in pre) and len(seg.words or []) > len(pre)
            and seg.words[:len(pre)] == pre and os.path.basename(seg.words[len(pre)]) == c.prog)


def alone(c, sc):
    """True when nothing but `cd`, `pushd` and `git add` runs before the top-level command c in its call. Anything
    else (a redirect, a script, `git rm`, `git mv`, `git apply --cached`, ...) may write files or stage paths after
    this hook has read the tree, so a commit that follows it can't be judged."""
    k = next((i for i, s in enumerate(sc.segs) if (s.start, s.end) == (c.start, c.end)), None)
    if k is None or not own_command(c, sc.segs[k]):
        return False
    by_span = {(s.start, s.end): s for s in sc.segs}
    adds = {(a.start, a.end) for a in sc.cmds if (a.prog, a.sub) == ('git', 'add') and (a.start, a.end) in by_span
            and own_command(a, by_span[(a.start, a.end)])}   # a `git add` inside a wrapper has its wrapper's span
    return all(s.words == [] or (bool(s.words) and (
        (s.words[0] in ('cd', 'pushd') and not any(re.search('[<>]', w) for w in s.words[1:]))   # `cd .>app.py` is one word
        or (s.start, s.end) in adds)) for s in sc.segs[:k])


def would_commit(c, sc, top):
    paths = set(diff_names(top, '--cached'))
    for a in sc.cmds:   # every `git add` in the call stages its paths, one inside a substitution too
        if (a.prog, a.sub) == ('git', 'add') and a.args is not None and a.cwd:
            named = [p for p in positionals(a.args, ('--pathspec-from-file', '--chmod')) if p]
            if has_flag(a.args, ('-A', '--all')) or '.' in named or not named:
                paths |= set(changed_paths(top, everything=True))
            paths |= {under(top, a.cwd, p) for p in named if p != '.'}
    if c.prog == 'gt':
        gt_args = commit_words(c.args, GT_VALUE_OPTS)
        if has_flag(gt_args, ('--all', '-a')):
            paths |= set(changed_paths(top, everything=True))
        elif has_flag(gt_args, ('--update', '-u')):
            paths |= set(changed_paths(top, everything=False))
        return paths
    args = commit_words(c.args)
    if any(a == '--pathspec-from-file' or a.startswith('--pathspec-from-file=') for a in args):
        return None   # the paths are in a file
    if has_flag(args, ('--all', '-a')):
        paths |= set(changed_paths(top, everything=False))
    return paths | {under(top, c.cwd, p) for p in positionals(args, GIT_COMMIT_VALUE_OPTS)}


def commit_paths(c, sc, top):
    """The paths a `git commit` / `gt create|modify` would commit, or None when they can't be told: a path file, or
    a git that failed (nothing changed would be a guess)."""
    try:
        return would_commit(c, sc, top)
    except Unreadable:
        return None


def spec_gate(h, sc):
    if not h.agent:
        return
    for c in sc.cmds:
        push = is_push_trigger(c)
        if not push and (c.prog, c.sub) not in COMMITS:   # before cmd_repo: it spawns git
            continue
        repo = cmd_repo(c, h)
        batch = gated_batch(h, repo)
        if not batch:
            continue
        h.touch.add(repo)
        if push:
            h.deny(spec_reason(batch) + " (the spec phase doesn't push)")
            continue
        top = state.toplevel(c.cwd) if c.cwd and c.args is not None else None
        if top and not alone(c, sc):
            h.deny(spec_reason(batch) + '; ' + OWN_CALL)
            continue
        paths = commit_paths(c, sc, top) if top else None
        if paths is None:
            h.deny(spec_reason(batch) + "; foreman couldn't tell what this commit would include; " + OWN_CALL)
            continue
        outside = sorted(p for p in paths if not in_spec_dir(repo, p))
        if outside:
            h.deny(spec_reason(batch) + '; this commit would include ' + ', '.join(outside[:10]))


def spec_gate_edit(h, repo, path):
    batch = gated_batch(h, repo)
    if batch and not in_spec_dir(repo, repo.rel(path)):
        h.touch.add(repo)
        h.deny(spec_reason(batch))


BASH_PIECES[10] = spec_gate
EDIT_PIECES[10] = spec_gate_edit


# ---- piece 11: batch state (worker deny, checkpoint gate, report gate, TaskStop guard, release check) ------------

WAY_OUT = ' (to turn this check off: `hooks_disabled: [batch_state]` in the foreman config)'


def batch_store(repo, h):
    return batches.Store(repo.main, repo.dirs, h.run_sid(repo))


def squash(line):
    return re.sub(r'\s+', '', line)


def reverse_lines(f, block=1 << 16):
    """The lines of a binary file, last first, read in blocks from its end."""
    f.seek(0, os.SEEK_END)
    pos, rest = f.tell(), b''
    while pos > 0:
        n = min(block, pos)
        pos -= n
        f.seek(pos)
        parts = (f.read(n) + rest).split(b'\n')
        rest = parts[0]
        yield from reversed(parts[1:])
    yield rest


def assistant_lines(path, since):
    """The whitespace-free lines of the assistant text blocks a transcript holds from `since` (epoch seconds, floored
    to the second like the transcript's ISO timestamps) on. Read from the end, it stops at the first entry older than
    `since`, so a long run's transcript costs only its tail. Raises OSError when the transcript can't be read."""
    if not path:
        raise OSError('no transcript_path')
    since, out = int(since), set()
    with open(path, 'rb') as f:
        for raw in reverse_lines(f):
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            t = iso_time(e['timestamp']) if isinstance(e.get('timestamp'), str) else None
            if t is not None and t < since:
                break
            msg = e.get('message')
            if e.get('type') != 'assistant' or not isinstance(msg, dict) or not isinstance(msg.get('content'), list):
                continue
            for b in msg['content']:
                if isinstance(b, dict) and b.get('type') == 'text' and isinstance(b.get('text'), str):
                    out.update(squash(line) for line in b['text'].splitlines())
    return out


def holds(have, text):
    """True when every non-blank line of `text`, whitespace removed, is in the set `have`."""
    return all(squash(line) in have for line in text.splitlines() if squash(line))


def pasted(text, path, at):
    """True when every line of `text` is a line of the assistant's own text written since `at`, whitespace ignored.
    A tool result doesn't count: the human sees only the assistant's text."""
    return holds(assistant_lines(path, at), text)


def table_problem(h, store, name, text, cmd, where, again=None):
    """None when the stamp `name` holds `text` and its lines were pasted after it, else the deny reason. An unreadable
    transcript fails open. `again` names what to do after the one more call when the table was shown but isn't in the
    text yet (the hooks see the text only once the next call starts)."""
    shown = store.read(name)
    if not isinstance(shown, dict):
        return f'run `{cmd}` and paste its output as text {where}'
    if shown.get('text') != text:
        return f'the table changed since it was shown; run `{cmd}` again and paste its output as text {where}'
    try:
        if pasted(text, h.inp.get('transcript_path'), float(shown.get('at', 0))):
            return None
    except OSError as e:
        print(f'foreman: batch_state could not read the transcript ({e}), so the table check is skipped', file=sys.stderr)
        return None
    if again:
        return (f'paste the output of `{cmd}` as text in a message of its own, make one more call, then '
                f'{again}; the hooks see your text only once the next call starts')
    return (f"paste the output of `{cmd}` as text {where}: a tool result doesn't count, because the human sees only "
            'your text')


def escaped(repo, store, name, tid):
    """The way out of a deny loop: after two denies against one unchanged table (id `tid`), the third call passes."""
    seen = store.read(name)
    count = int(seen.get('count', 0)) if isinstance(seen, dict) and seen.get('id') == tid else 0
    if count >= 2:
        print(f'foreman: batch_state let this call through after {count} denies against the same table',
              file=sys.stderr)
        return True
    state.write_json(store.path(repo.dirs[0], name), {'id': tid, 'count': count + 1})
    return False


def effective(repo, h, b):
    """A batch's status at the checkpoint: a `passed` batch whose release hooks.py recorded counts as released."""
    st = b.get('status') or 'spec'
    return 'released' if st == 'passed' and released(repo, h.run_sid(repo), b['batch']) else st


def checkpoint_gate(h):
    """Duty 2: at the checkpoint moment (a wave of the run has a passed batch and none in spec or review), every
    AskUserQuestion needs that wave's current checkpoint table pasted in the assistant's text."""
    repo = repo_at(h.cwd) if not h.agent else None
    if not h.active(repo, 'batch_state'):
        return
    store = batch_store(repo, h)
    waves = {}
    for b in store.batches():
        waves.setdefault(batches.wave_of(b['batch']), []).append(b)
    for wave, bs in sorted(waves.items()):
        st = {effective(repo, h, b) for b in bs}
        if 'passed' not in st or st & {'spec', 'review'}:
            continue
        h.touch.add(repo)
        text, tid = batches.render_checkpoint(store, wave, bs)
        why = table_problem(h, store, f'{wave}.shown', text, f'python3 {HERE}/batches.py table checkpoint {wave}',
                            'before asking', again='ask')
        if why and not escaped(repo, store, f'{wave}.denies', tid):
            h.deny(why + WAY_OUT)


def batch_call(h, sc, command):
    """Duty 1: a worker's `batches.py` call is denied; the main session's touches the run marker."""
    if runs_control(sc, command, h.cwd, 'batches.py', BATCH_VERBS, BATCH_TEXT):
        repo = repo_at(h.cwd)
        if h.active(repo, 'batch_state'):
            if h.agent:
                h.deny('only the orchestrator writes batch state — a worker never runs batches.py')
                return
            h.touch.add(repo)   # the newest live marker is batches.py's session fallback


def owed_path(d, sid):
    return os.path.join(d, 'runs', sid + '.owed')


def report_gate(h, sc):
    """Duty 3: a `run-end` whose run has batches, before the current report is in the assistant's text, records the
    report as owed and goes on. The paste usually rides in the same message as `run-end`, which PreToolUse can't see
    (P3), so the Stop hook checks the turn's last text instead. Keyed on this session's own id: `run-end` deletes the
    marker, and the Stop hook has only its own id. `since` (the current stamp's time, else a minute before now) bounds
    the transcript the Stop hook searches; `repo` is the repo whose `hooks_disabled` it reads."""
    if not ends_run(sc):
        return
    repo = repo_at(h.cwd)
    if not h.active(repo, 'batch_state'):
        return
    store = batch_store(repo, h)
    bs = store.batches()
    if not bs:
        return
    h.touch.add(repo)
    text, tid = batches.render_report(store, bs)
    if table_problem(h, store, 'report.shown', text, 'table report', 'in the run report'):
        shown = store.read('report.shown')
        since = float(shown['at']) if isinstance(shown, dict) and shown.get('text') == text else time.time() - 60
        state.write_json(owed_path(repo.dirs[0], h.sid),
                         {'id': tid, 'text': text, 'blocked': 0, 'since': since, 'repo': repo.top})


def manual_run(repo, h):
    m = marker(repo, h.sid)
    words = str((m[1] if m else {}).get('args') or '').split()
    return not {'--auto-merge', 'loop'} & set(words)


def checkpoint_shown(h, store, b):
    """True when a checkpoint stamp written after the batch's latest pass lists it as passed, and was pasted."""
    shown = store.read(batches.wave_of(b['batch']) + '.shown')
    passes = [t for k, t in b.get('stamps') or [] if k == 'passed']
    if not isinstance(shown, dict) or not passes or float(shown.get('at', 0)) <= passes[-1]:
        return False
    row = re.compile(r'\|\s*' + re.escape(b['batch']) + r'\s*\|.*\|\s*passed\s*\|')
    if not any(row.fullmatch(line.strip()) for line in str(shown.get('text', '')).splitlines()):
        return False
    try:
        return pasted(shown['text'], h.inp.get('transcript_path'), float(shown['at']))
    except OSError as e:
        print(f'foreman: batch_state could not read the transcript ({e}), so the table check is skipped', file=sys.stderr)
        return True


def spec_worker(repo, sid, batch):
    """True when the agent map holds a spec-phase worker of `batch` in this session."""
    return any(isinstance(e, dict) and e.get('batch') == batch and e.get('spec')
               for d in repo.dirs for e in map(state.read_json, ls(os.path.join(d, 'batches', sid, 'agents'), '*')))


def release_check(h, repo, batch, sha):
    """Duty 5: `hooks.py release` records nothing unless the batch passed at this SHA and, in a manual run, its
    checkpoint table was pasted after the pass."""
    if not h.active(repo, 'batch_state'):
        return
    h.touch.add(repo)
    store = batch_store(repo, h)
    b = store.read(batch + '.json')
    if b is None:
        if spec_worker(repo, h.run_sid(repo), batch):
            h.deny(f'record the batch with `python3 {HERE}/batches.py set {batch} …` first' + WAY_OUT)
        return
    if b.get('status') != 'passed' or not batches.same_sha(b.get('reviewed_sha'), sha):
        h.deny(f"release only a passed batch at its reviewed SHA {b.get('reviewed_sha') or '(none)'}; a spec that "
               'changed after review needs a new round' + WAY_OUT)
    elif manual_run(repo, h) and not checkpoint_shown(h, store, b):
        h.deny('show the checkpoint table first: run '
               f'`python3 {HERE}/batches.py table checkpoint {batches.wave_of(batch)}` and paste its output as text'
               + WAY_OUT)


def stopped_record(repo, sid, batch):
    """hooks.py's `<batch>.stopped` record ({time}) in this session, or None (never the batches.py `stopped` stamp)."""
    for d in repo.dirs:
        r = state.read_json(batch_file(d, sid, batch, 'stopped'))
        if isinstance(r, dict):
            return r
    return None


def worker_of(repo, h, tid, names):
    """The run's batch whose worker `tid` names without a task record: `fw-<batch>`, `fw-<batch>-<k>`, or an agent id
    the agent map tags as a worker of that batch; else None."""
    for n in names:
        if tid == f'fw-{n}' or re.fullmatch(re.escape(f'fw-{n}-') + r'\d+', tid):
            return n
    e = agent_entry(repo, h.run_sid(repo), tid)
    return e['batch'] if isinstance(e, dict) and e.get('batch') in names else None


def taskstop_guard(h, ti):
    """Duty 4: TaskStop is refused with no id or a batch name, and for a worker before `stopping`; an id it doesn't
    know passes."""
    repo = repo_at(h.cwd) if not h.agent else None
    if not h.active(repo, 'batch_state'):
        return
    raw = ti.get('task_id') or ti.get('shell_id')
    tid = raw.split('@', 1)[0].strip() if isinstance(raw, str) else ''
    run = {b['batch']: b for b in batch_store(repo, h).batches()}
    h.touch.add(repo)
    if not tid:
        h.deny(f'TaskStop needs a task id; the ids are in `python3 {HERE}/batches.py show <batch>`' + WAY_OUT)
        return
    recorded = [(n, t) for n, b in run.items() for t in b.get('tasks') or [] if t.get('id') == tid]
    if tid in run and not recorded:
        h.deny('TaskStop takes a task id, not a batch name; the ids are in '
               f'`python3 {HERE}/batches.py show {tid}`' + WAY_OUT)
        return
    live = [(n, t) for n, t in recorded if t.get('kind') == 'worker' and t.get('live')]
    if live:
        n, t = max(live, key=lambda x: float(x[1].get('added', 0)))
        stop = stopped_record(repo, h.run_sid(repo), n)
        ok = stop is not None and float(stop.get('time', 0)) >= float(t.get('added', 0))
    else:
        n = worker_of(repo, h, tid, run)
        ok = n is None or stopped_record(repo, h.run_sid(repo), n) is not None
    if not ok:
        h.deny(f'run `python3 {HERE}/hooks.py stopping {n}` first, then TaskStop {tid}' + WAY_OUT)


# ---- pretool / posttool ---------------------------------------------------------------------------

def run_piece(piece, *args):
    """One piece's call, isolated: a crash costs only that piece's decision (a stderr note), not the denies the other
    pieces recorded or are yet to record, which main's fail-open handler would drop with it."""
    try:
        piece(*args)
    except Exception as e:
        print(f'foreman hook error: {piece.__name__}: {type(e).__name__}: {e}', file=sys.stderr)


def pretool(h):
    tool = h.inp.get('tool_name')
    ti = h.inp.get('tool_input') or {}
    if tool == 'Bash':
        command = ti.get('command')
        if isinstance(command, str) and FAST.search(command):   # a Bash call without git/gh/gt gets here only from a subagent, to be briefed
            pretool_bash(h, command)
    elif tool in EDIT_TOOLS:
        path = ti.get('file_path') or ti.get('notebook_path')
        if path:
            path = os.path.join(h.cwd, path)
            repo = repo_at(path)
            for k in sorted(EDIT_PIECES):
                run_piece(EDIT_PIECES[k], h, repo, path)
    elif tool == 'AskUserQuestion':
        run_piece(checkpoint_gate, h)
    elif tool == 'TaskStop':
        run_piece(taskstop_guard, h, ti)
    briefing_text = None
    if not h.denies:   # a denied call carries no briefing: it may not be shown, and `briefed` would be spent
        try:
            briefing_text = worker_briefing(h)   # piece 6: a tagged worker's first allowed call (probe P2)
        except Exception as e:   # a failed briefing must not skip the marker touches below
            print(f'foreman hook error: {type(e).__name__}: {e}', file=sys.stderr)
    if h.denies:   # printed before the touches: a failed touch (stderr, via main) can't drop a deny
        print(json.dumps({'hookSpecificOutput': {
            'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
            'permissionDecisionReason': 'foreman: ' + '; '.join(dict.fromkeys(h.denies))}}), flush=True)
    elif briefing_text:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'additionalContext': briefing_text}}),
              flush=True)
    for r in h.touch:
        touch_marker(r, h.sid)


def pretool_bash(h, command):
    sc = shellscan.scan(command, h.cwd)
    if h.agent and runs_control(sc, command, h.cwd) and repo_at(h.cwd):
        h.deny('only the orchestrator ends a run or releases a spec — a worker never runs hooks.py')
        return
    run_piece(batch_call, h, sc, command)
    if h.denies:
        return
    if not h.agent:
        run_piece(report_gate, h, sc)
    call = control_call(command)
    if call and not h.agent:   # a subagent's call is never recorded, whatever the deny above saw
        repo = repo_at(h.cwd)
        if repo:
            if call[0] == 'release':
                run_piece(release_check, h, repo, *call[1])
                if h.denies:   # nothing is recorded
                    return
            pretool_control(h, repo, *call)
        return
    for k in sorted(BASH_PIECES):
        run_piece(BASH_PIECES[k], h, sc)


def posttool(h):
    ti = h.inp.get('tool_input') or {}
    if h.inp.get('tool_name') == 'Bash':
        if not h.agent and ends_run(shellscan.scan(ti.get('command') or '', h.cwd)):
            end_run(h.sid)
        return
    for k in sorted(POST_EDIT):
        POST_EDIT[k](h, ti)


HANDLERS['pretool'] = pretool
HANDLERS['posttool'] = posttool


# ---- the end of a turn: the owed run report (piece 11, duty 3) ------------------------------------

STOP_ASK = 'foreman: end the run report with the spec-phase table, pasted verbatim as text as the last thing in your reply:'


def paid(h, owed):
    """True when the owed report is in the turn's last text, or in the assistant text the transcript holds since
    `since` (every message but the last is written by the time Stop fires; the last may not be yet)."""
    if holds({squash(line) for line in (h.inp.get('last_assistant_message') or '').splitlines()}, owed['text']):
        return True
    try:
        return pasted(owed['text'], h.inp.get('transcript_path'), float(owed.get('since') or 0))
    except OSError:
        return False


def turn_end(h):
    """The Stop hook (main session only). A report `run-end` left owed must be in the turn's text: handed back once
    with one block (every owed report together), and dropped at the next turn end either way, so a session can never
    be held. A turn already continued by a Stop hook (`stop_hook_active`) is never blocked. A debt is dropped silently
    when its repo has `batch_state` off, or when the run is still live (the `run-end` never ran)."""
    if not h.sid or os.sep in h.sid:
        return
    ask = []
    for home in {state.home(), state.DEFAULT_HOME}:
        for p in ls(home, f'*/runs/{glob.escape(h.sid)}.owed'):
            owed = state.read_json(p)
            if not isinstance(owed, dict) or not isinstance(owed.get('text'), str):
                rm(p)
                print(f'foreman: dropped an unreadable owed run report ({p})', file=sys.stderr)
                continue
            top = owed.get('repo')
            repo = repo_at(top if isinstance(top, str) else h.cwd)
            if repo and (repo.disabled('batch_state') or marker(repo, h.sid)):
                rm(p)
            elif paid(h, owed):
                rm(p)
            elif owed.get('blocked') or h.inp.get('stop_hook_active'):
                rm(p)
                print('foreman: the run report was not pasted; ending anyway', file=sys.stderr)
            else:
                ask.append((p, owed))
    for p, owed in ask:
        state.write_json(p, dict(owed, blocked=1))
    if ask:
        print(json.dumps({'decision': 'block',
                          'reason': STOP_ASK + '\n\n' + '\n\n'.join(owed['text'] for _, owed in ask)}))


HANDLERS['stop'] = turn_end


# ---- dispatcher -----------------------------------------------------------------------------------

class _CaptureStdout:
    """Captures writes to stdout while forwarding them to the original stdout."""
    def __init__(self, original):
        self.original = original
        self.chunks = []

    def write(self, s):
        self.chunks.append(s)
        return self.original.write(s)

    def flush(self):
        return self.original.flush()

    def getvalue(self):
        return ''.join(self.chunks)

    def __getattr__(self, name):
        return getattr(self.original, name)


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
        if event == 'posttool' and command is not None and (inp.get('agent_id') or not RUN_END.search(command)):
            return
        if event == 'pretool' and command is not None and not inp.get('agent_id') and not FAST.search(command):
            return   # a subagent's call goes on: its first one carries the briefing (probe P2)
        if (event == 'pretool' and inp.get('tool_name') in EDIT_TOOLS and not inp.get('agent_id')
                and set(EDIT_PIECES) <= SUBAGENT_EDIT_PIECES):
            return
        path = ti.get('file_path') if isinstance(ti, dict) else None
        if event == 'posttool' and command is None and not (isinstance(path, str) and path.endswith('.md')):
            return   # the backlog check, the only post-edit piece, reads a .md file
        _load()
        fn = HANDLERS.get(event)
        if fn:
            capture = _CaptureStdout(sys.stdout)
            old_stdout = sys.stdout
            sys.stdout = capture
            try:
                fn(Hook(inp))
            finally:
                sys.stdout = old_stdout
                try:
                    trace(event, inp, capture.getvalue())
                except Exception:
                    pass
    except Exception as e:  # fail open
        print(f'foreman hook error: {type(e).__name__}: {e}', file=sys.stderr)


def trace(event, inp, out):
    """With FOREMAN_HOOK_LOG=<file>, append every decision a hook printed (the headless end-to-end reads it)."""
    path = os.environ.get('FOREMAN_HOOK_LOG')
    if path and out.strip():
        with open(path, 'a') as f:
            f.write(json.dumps({'event': event, 'session_id': inp.get('session_id'), 'agent_id': inp.get('agent_id'),
                                'out': json.loads(out)}) + '\n')


if __name__ == '__main__':
    main(sys.argv)
