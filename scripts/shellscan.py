#!/usr/bin/env python3
"""Fail-closed detection of git, gh and gt commands in one Bash command string: scan, don't parse.

scan(text, cwd) -> Scan
  .ok       False when the text couldn't be lexed (unbalanced quotes, an unterminated heredoc); .cmds then
            holds raw-text regex matches with unknown arguments (args None, cwd None)
  .cmds     every git/gh/gt candidate at any nesting: $(...), backticks, bash -c, eval, a shell's heredoc
  .segs     the top level's segments, empty ones included, with the separator that ended each (piece 8)
  .docs     every heredoc, with the file a `cat > f <<EOF` writes (piece 4)
  .blanked  the top level with quotes, comments, substitutions and heredoc bodies filled with FILL

It errs toward treating text as a command: an unquoted `echo git push` is a candidate. It doesn't see
commands fed to a shell's stdin from a pipe, or strings built at run time; those are accepted limits.
"""
import os
import re
import shlex

FILL = '\x00'
PROGS = ('git', 'gh', 'gt')
SHELLS = ('bash', 'sh', 'zsh')
KEYWORDS = ('then', 'do', 'else', 'elif', 'if', 'while', 'until', '!', 'time', '{', '}')
GT_ALIASES = {'s': 'submit', 'ss': 'submit', 'm': 'modify', 'c': 'create'}
GT_GROUPS = ('stack', 'branch', 'upstack', 'downstack')
GIT_VALUE_OPTS = ('-C', '-c', '--git-dir', '--work-tree', '--namespace', '--config-env', '--super-prefix',
                  '--attr-source')
MAX_DEPTH = 8
HEREDOC = re.compile(r'<<([-~]?)[ \t]*(?:\'([^\'\n]*)\'|"([^"\n]*)"|\\?([^\s;&|<>()\'"]+))')
ASSIGN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
GITENV = re.compile(r'^(?:GIT_DIR|GIT_WORK_TREE)=')
WORD = re.compile(r'[^ \t\n]+')
REDIRECT = re.compile(r'^(?:\d*|&)(?:>>?|<<?|>&|<&|>\|)')
FALLBACK = (
    ('git', 'push', r'\bgit\b[^\n]*\bpush\b'),
    ('git', 'commit', r'\bgit\b[^\n]*\bcommit\b'),
    ('git', 'rebase', r'\bgit\b[^\n]*\brebase\b'),
    ('git', 'pull', r'\bgit\b[^\n]*\bpull\b'),
    ('gh', 'pr merge', r'\bgh\b[^\n]*\bmerge\b'),
    ('gh', 'pr create', r'\bgh\b[^\n]*\bcreate\b'),
    ('gh', 'pr comment', r'\bgh\b[^\n]*\bcomment\b'),
    ('gh', 'pr edit', r'\bgh\b[^\n]*\bedit\b'),
    ('gh', 'pr review', r'\bgh\b[^\n]*\breview\b'),
    ('gh', 'api', r'\bgh\s+api\b'),
    ('gt', 'submit', r'\bgt\s+(?:submit|ss?)\b'),
    ('gt', 'merge', r'\bgt\s+merge\b'),
    ('gt', 'modify', r'\bgt\s+(?:modify|m)\b'),
    ('gt', 'create', r'\bgt\s+(?:create|c)\b'),
)


class ScanError(Exception):
    pass


class Doc:
    """One heredoc. body_start..end spans the body and its terminator line; `direct` is False for a
    heredoc found inside a substitution of the level that recorded it."""

    def __init__(self, op, body_start, end, body, direct):
        self.op, self.body_start, self.end, self.body, self.direct = op, body_start, end, body, direct
        self.target = None


class Seg:
    def __init__(self, start, end, words, sep, depth, cwd):
        self.start, self.end, self.words, self.sep, self.depth, self.cwd = start, end, words, sep, depth, cwd


class Cmd:
    def __init__(self, prog, args, pre, cwd, text, start, end):
        self.prog, self.args, self.pre, self.cwd, self.text = prog, args, pre, cwd, text
        self.start, self.end = start, end
        self.sub = None
        self.ghrepo = None
        self.heredocs = []
        self.denied = False   # set by hooks.py when piece 2 already denied this merge
        self.assigns = [w.split('=', 1)[0] for w in (pre or []) if ASSIGN.match(w)]
        self.env = any(os.path.basename(w) == 'env' for w in (pre or []))

    def __repr__(self):
        return f'Cmd({self.prog} {self.sub} {self.args} cwd={self.cwd})'


class Scan:
    def __init__(self, ok, cmds, segs, docs, blanked):
        self.ok, self.cmds, self.segs, self.docs, self.blanked = ok, cmds, segs, docs, blanked


class _Out:
    def __init__(self):
        self.blank, self.subs, self.docs, self.comments, self.bare, self.conts = [], [], [], [], [], []


# ---- lexing ---------------------------------------------------------------------------------------

def _lex(t, i, closer, out):
    """Walk t from i, recording quote, comment, substitution and heredoc spans in `out`. At the top level
    (closer None) return len(t); inside $( ) return the index of the matching ')'."""
    n, depth, pending = len(t), 0, []
    while i < n:
        c = t[i]
        if c == '\\':
            if t.startswith('\n', i + 1):   # a line continuation: the shell removes the pair
                out.conts.append((i, i + 2))
            out.blank.append((i, min(i + 2, n)))
            i += 2
        elif c == "'":
            j = t.find("'", i + 1)
            if j < 0:
                raise ScanError('unterminated single quote')
            out.blank.append((i, j + 1))
            i = j + 1
        elif t.startswith("$'", i):
            j = i + 2
            while j < n and t[j] != "'":
                j += 2 if t[j] == '\\' else 1
            if j >= n:
                raise ScanError("unterminated $'")
            out.blank.append((i, j + 1))
            i = j + 1
        elif c == '"':
            j = _dquote(t, i + 1, out)
            out.blank.append((i, j + 1))
            i = j + 1
        elif c == '`':
            j = _backtick(t, i + 1)
            out.subs.append((i + 1, j))
            out.blank.append((i, j + 1))
            out.bare.append((i + 1, j))
            i = j + 1
        elif c in '$<>' and t.startswith('(', i + 1):
            j = _subst(t, i + 2, out)
            out.subs.append((i + 2, j))
            out.blank.append((i, j + 1))
            out.bare.append((i + 2, j))
            i = j + 1
        elif c == '#' and (i == 0 or t[i - 1] in ' \t\n;&|()'):
            j = t.find('\n', i)
            j = n if j < 0 else j
            out.blank.append((i, j))
            out.comments.append((i, j))
            i = j
        elif t.startswith('<<<', i):
            i += 3
        elif t.startswith('<<', i):
            m = HEREDOC.match(t, i)
            if not m:
                i += 2
                continue
            pending.append((i, next(g for g in m.group(2, 3, 4) if g is not None), m.group(1)))
            i = m.end()
        elif c == '\n' and pending:
            i = _bodies(t, i + 1, pending, out, closer is None)
            pending = []
        elif closer and c == '(':
            depth += 1
            i += 1
        elif closer and c == ')':
            if depth == 0:
                return i
            depth -= 1
            i += 1
        else:
            i += 1
    if closer:
        raise ScanError('unterminated substitution')
    if pending:
        raise ScanError('unterminated heredoc')
    return n


def _bodies(t, k, pending, out, direct):
    """Consume the bodies of the heredocs opened on the line that just ended; return the index of the
    newline after the last terminator line (or len(t))."""
    n, last = len(t), len(t)
    for op, word, mode in pending:
        start = k
        while True:
            e = t.find('\n', k)
            e = n if e < 0 else e
            line = t[k:e]
            if (line.lstrip('\t') if mode == '-' else line.lstrip() if mode == '~' else line) == word:
                out.docs.append(Doc(op, start, e, t[start:k], direct))
                out.blank.append((start, e))
                last, k = e, e + 1
                break
            if e >= n:
                raise ScanError('unterminated heredoc')
            k = e + 1
    return last


def _dquote(t, i, out):
    n = len(t)
    while i < n:
        c = t[i]
        if c == '\\':
            i += 2
        elif c == '"':
            return i
        elif c == '`':
            j = _backtick(t, i + 1)
            out.subs.append((i + 1, j))
            i = j + 1
        elif c == '$' and t.startswith('(', i + 1):
            j = _subst(t, i + 2, out)
            out.subs.append((i + 2, j))
            i = j + 1
        else:
            i += 1
    raise ScanError('unterminated double quote')


def _backtick(t, i):
    n = len(t)
    while i < n and t[i] != '`':
        i += 2 if t[i] == '\\' else 1
    if i >= n:
        raise ScanError('unterminated backtick')
    return i


def _subst(t, i, out):
    inner = _Out()
    j = _lex(t, i, ')', inner)
    out.docs.extend(inner.docs)
    return j


# ---- segments -------------------------------------------------------------------------------------

def _pieces(b):
    """[(start, end, sep)] of the blanked text: split at ; && || | |& & newline ( ), then at standalone
    { } and the keywords (sep 'kw')."""
    raw, start, i, n = [], 0, 0, len(b)
    while i < n:
        two = b[i:i + 2]
        if two in ('&&', '||', '|&'):
            w = 2
        elif b[i] in ';|\n()':
            w = 1
        elif b[i] == '&' and not (i > 0 and b[i - 1] in '<>') and b[i + 1:i + 2] != '>':
            w = 1
        else:
            i += 1
            continue
        raw.append((start, i, b[i:i + w]))
        start = i = i + w
    raw.append((start, n, None))
    out = []
    for s, e, sep in raw:
        for m in WORD.finditer(b, s, e):
            if m.group() in KEYWORDS:
                out.append((s, m.start(), 'kw'))
                s = m.end()
        out.append((s, e, sep))
    return out


def _unfold(s, start, end, skip):
    """s[start:end] without the characters at the positions in `skip` (the line continuations)."""
    if not skip:
        return s[start:end]
    return ''.join(c for k, c in enumerate(s[start:end], start) if k not in skip)


def _words(s):
    try:
        return shlex.split(s)
    except ValueError:
        return None


def _join(cwd, p):
    if cwd is None or not p or p == '-' or '$' in p or '`' in p:
        return None
    return os.path.normpath(os.path.join(cwd, os.path.expanduser(p)))


def _cd(cwd, words):
    args = words[1:]
    if words[0] == 'popd' or '-' in args:
        return None
    target = next((a for a in args if not a.startswith('-')), '~')
    return _join(cwd, target)


def _target(words):
    for k, w in enumerate(words):
        if w in ('>', '>>', '1>', '1>>'):
            return words[k + 1] if k + 1 < len(words) else None
        m = re.match(r'^1?>>?(.+)$', w)
        if m and not m.group(1).startswith('&'):
            return m.group(1)
    return None


def _feeds_shell(words):
    """True when a shell word comes before the heredoc operator and the shell has no -c script, so the heredoc
    is its script: `bash <<EOF`, `bash << EOF`, `bash 2>&1 <<EOF`, `bash -s arg <<EOF`."""
    h = next((k for k, w in enumerate(words) if w.startswith('<<') and not w.startswith('<<<')), len(words))
    return any(os.path.basename(w) in SHELLS for w in words[:h]) and _shell_c(words) is None


def _shell_c(words):
    """The script string of `bash -c '...'` (any option cluster holding c: -c, -lc, -ec). Each o or O in a
    cluster (-o, +o, -O, -euo) takes the next word as its value; a `--` may follow the c option."""
    for k, w in enumerate(words):
        if os.path.basename(w) not in SHELLS:
            continue
        j = k + 1
        while j < len(words):
            m = re.match(r'^([-+])([a-zA-Z]+)$', words[j])
            if words[j] in ('--rcfile', '--init-file'):
                j += 2
            elif m:
                j += 1 + sum(ch in 'oO' for ch in m.group(2))
                if m.group(1) == '-' and 'c' in m.group(2):
                    if j < len(words) and words[j] == '--':
                        j += 1
                    return words[j] if j < len(words) else None
            elif words[j].startswith('-') and words[j] != '--':
                j += 1
            else:
                break
    return None


def _level(t, cwd, depth):
    """Scan one level. Returns (cmds, segs, docs, blanked); positions are relative to t."""
    if depth > MAX_DEPTH:
        raise ScanError('nested too deep')
    out = _Out()
    _lex(t, 0, None, out)
    b = list(t)
    for s, e in out.blank:
        b[s:e] = FILL * (e - s)
    blanked = ''.join(b)
    sh = list(t)
    for s, e in out.comments + [(d.body_start, d.end) for d in out.docs]:
        sh[s:e] = ' ' * (e - s)
    for s, e in out.bare:   # an unquoted $(...) stays one word, scanned by its own level
        sh[s:e] = FILL * (e - s)
    shtext = ''.join(sh)
    skip = {k for s, e in out.conts for k in range(s, e)}

    cmds, segs, docs, stack = [], [], [d for d in out.docs if d.direct], []
    for start, end, sep in _pieces(blanked):
        words = _words(_unfold(shtext, start, end, skip))
        seg = Seg(start, end, words, sep, len(stack), cwd)
        segs.append(seg)
        own = []
        if words is None:   # shlex can't read the segment: fail closed, as on a lexing failure
            own = _fallback_cmds(t[start:end], start, end)
            matched = {c.prog for c in own}
            for m in WORD.finditer(blanked, start, end):
                prog = os.path.basename(m.group())
                if FILL not in m.group() and prog in PROGS and prog not in matched:
                    own.append(_classify(Cmd(prog, None, None, cwd, t[start:end], start, end)))
            first = WORD.search(blanked, start, end)
            if first and first.group() in ('cd', 'pushd', 'popd'):
                cwd = None
        elif words:
            for k, w in enumerate(words):
                if os.path.basename(w) in PROGS:
                    own.append(_classify(Cmd(os.path.basename(w), words[k + 1:], words[:k], cwd, t[start:end], start, end)))
            for d in docs:
                if start <= d.op < end:
                    tgt = _target(words)
                    d.target = _join(cwd, tgt) if tgt else None
                    if _feeds_shell(words):
                        own += _nested(d.body, cwd, depth, start, end, docs)
            script = _shell_c(words)
            if script:
                own += _nested(script, cwd, depth, start, end, docs)
            if any(os.path.basename(w) == 'eval' for w in words):
                k = next(k for k, w in enumerate(words) if os.path.basename(w) == 'eval')
                own += _nested(' '.join(words[k + 1:]), cwd, depth, start, end, docs)
            if words[0] in ('cd', 'pushd', 'popd'):
                cwd = _cd(cwd, words)
            if any(GITENV.match(w) for w in words) and (words[0] == 'export' or all(ASSIGN.match(w) for w in words)):
                cwd = None   # a GIT_DIR/GIT_WORK_TREE that outlives this segment: later commands run elsewhere
        for s, e in out.subs:
            if start <= s < end:
                sub_cmds, _, sub_docs, _ = _level(t[s:e], seg.cwd, depth + 1)
                for c in sub_cmds:
                    c.start, c.end = c.start + s, c.end + s
                for d in sub_docs:
                    d.op, d.body_start, d.end = d.op + s, d.body_start + s, d.end + s
                own += sub_cmds
                docs += sub_docs
        for c in own:
            c.heredocs += [d.body for d in out.docs if (c.start <= d.op < c.end or c.start <= d.body_start < c.end)
                           and d.body not in c.heredocs]
        cmds += own
        if sep == '(':
            stack.append(cwd)
        elif sep == ')' and stack:
            cwd = stack.pop()
    return cmds, segs, docs, blanked


def _nested(text, cwd, depth, start, end, docs):
    """Scan a string that isn't aligned with the outer text (a -c script, eval's words, a shell's heredoc);
    its candidates take the outer segment's span."""
    cmds, _, sub_docs, _ = _level(text, cwd, depth + 1)
    for c in cmds:
        c.start, c.end = start, end
    for d in sub_docs:
        d.op = d.body_start = d.end = -1
    docs += sub_docs
    return cmds


def _verb(w):
    """A subcommand word up to its first redirect character: `push>/dev/null` is `push`."""
    return re.match(r'[^<>&]*', w).group()


def _redirect_skip(a, i):
    """How many words the redirect at a[i] takes: 0 if it isn't one, 2 for a bare operator (`>`, `2>`, `&>`)
    whose target is the next word."""
    m = REDIRECT.match(a[i])
    return 0 if not m else 2 if m.end() == len(a[i]) else 1


def _classify(cmd):
    a = cmd.args
    cwd = None if any(n in ('GIT_DIR', 'GIT_WORK_TREE') for n in cmd.assigns) else cmd.cwd
    if a is None:
        cmd.cwd = None
        return cmd
    if cmd.prog == 'git':
        i = 0
        while i < len(a):
            w = a[i]
            skip = _redirect_skip(a, i)
            if skip:
                i += skip
                continue
            if not w.startswith('-'):
                break
            if w in GIT_VALUE_OPTS:
                if w == '-C':
                    cwd = _join(cwd, a[i + 1] if i + 1 < len(a) else None)
                elif w in ('--git-dir', '--work-tree'):
                    cwd = None
                i += 2
                continue
            if w.startswith(('--git-dir=', '--work-tree=')):
                cwd = None
            i += 1
        cmd.sub, cmd.args = (_verb(a[i]) if i < len(a) else None), a[i + 1:]
    elif cmd.prog == 'gh':
        rest, i = [], 0
        while i < len(a):
            w = a[i]
            skip = _redirect_skip(a, i) if len(rest) < 2 else 0
            if skip:
                i += skip
                continue
            if w in ('-R', '--repo'):
                cmd.ghrepo = a[i + 1] if i + 1 < len(a) else None
                i += 2
                continue
            if w.startswith('--repo=') or (w.startswith('-R') and len(w) > 2):
                cmd.ghrepo = w.split('=', 1)[1] if w.startswith('--repo=') else w[2:]
                i += 1
                continue
            rest.append(w)
            i += 1
        verb = _verb(rest[0]) if rest else None
        if verb == 'pr' and len(rest) > 1:
            cmd.sub, cmd.args = 'pr ' + _verb(rest[1]), rest[2:]
        else:
            cmd.sub, cmd.args = verb, rest[1:]
    else:
        i = 0
        while i < len(a):
            w = a[i]
            skip = _redirect_skip(a, i)
            if skip:
                i += skip
                continue
            if not w.startswith('-'):
                break
            if w == '--cwd':
                cwd = _join(cwd, a[i + 1] if i + 1 < len(a) else None)
                i += 2
                continue
            if w.startswith('--cwd='):
                cwd = _join(cwd, w.split('=', 1)[1])
            i += 1
        verb = _verb(a[i]) if i < len(a) else None
        if verb in GT_GROUPS and i + 1 < len(a):
            nxt = _verb(a[i + 1])
            if GT_ALIASES.get(nxt, nxt) in ('submit', 'merge'):
                i, verb = i + 1, nxt
        cmd.sub, cmd.args = GT_ALIASES.get(verb, verb), a[i + 1:]
    cmd.cwd = cwd
    return cmd


def _fallback_cmds(text, start, end):
    """One candidate per FALLBACK regex that matches the raw `text`, with unknown arguments and directory."""
    cmds, flat = [], text.replace('\\\n', '')
    for prog, sub, pat in FALLBACK:
        if re.search(pat, flat):
            c = Cmd(prog, None, None, None, text, start, end)
            c.sub = sub
            cmds.append(c)
    return cmds


def scan(text, cwd):
    try:
        cmds, segs, docs, blanked = _level(text, cwd, 0)
        return Scan(True, cmds, segs, docs, blanked)
    except ScanError:
        return Scan(False, _fallback_cmds(text, 0, len(text)), [], [], text)


# ---- classification helpers for the pieces --------------------------------------------------------

def push_info(args, default):
    """(delete, force, to_default) for `git push <args>`. A delete is `--delete`/`-d`, or every refspec after the
    remote being `:<ref>`: `git push origin HEAD :old` also pushes HEAD, a bare `:` pushes the matching branches,
    and `--tags`, `--all`, `--follow-tags` and `--prune` push refs beside the refspecs."""
    delete = force = to_default = pushes_refs = False
    positional, i = [], 0
    while i < len(args):
        a = args[i]
        if a in ('--repo', '--receive-pack', '--exec', '-o', '--push-option'):
            i += 2
            continue
        if a in ('--delete', '-d'):
            delete = True
        elif a in ('--force', '--mirror') or a.startswith('--force-with-lease'):
            force = True
        elif a in ('--tags', '--all', '--prune') or a.startswith('--follow-tags'):
            pushes_refs = True
        elif re.match(r'^-[a-zA-Z]+$', a):
            force = force or 'f' in a
            delete = delete or 'd' in a
        elif not a.startswith('-') and not REDIRECT.match(a):
            positional.append(a)
        i += 1
    for ref in positional[1:]:
        if ref.startswith('+'):
            force = True
        dst = ref.lstrip('+').split(':')[-1]
        if dst in (default, f'refs/heads/{default}'):
            to_default = True
    if pushes_refs:
        delete = False
    elif positional[1:] and all(len(ref) > 1 and ref.startswith(':') for ref in positional[1:]):
        delete = True
    return delete, force, to_default


def is_rebase(cmd):
    if cmd.prog != 'git':
        return False
    if cmd.sub == 'rebase':
        return True
    if cmd.sub == 'pull' and cmd.args is not None:
        return any(a in ('--rebase', '-r') or (a.startswith('--rebase=') and a != '--rebase=false')
                   for a in cmd.args)
    return cmd.sub == 'pull' and cmd.args is None


def gh_api_merge(args, subagent):
    """True when `gh api <args>` merges: a non-GET call to a merge path, or a merging GraphQL mutation."""
    if args is None:
        return True
    method, body, path, query, from_file, i = None, False, None, '', False, 0
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else ''
        if a in ('-X', '--method'):
            method, i = nxt.upper(), i + 2
            continue
        if a.startswith('--method='):
            method = a.split('=', 1)[1].upper()
        elif a.startswith('-X') and len(a) > 2:
            method = a[2:].upper()
        elif a in ('-f', '-F', '--field', '--raw-field') or a.startswith(('--field=', '--raw-field=')) \
                or re.match(r'^-[fF].', a):
            body = True
            glued = a not in ('-f', '-F', '--field', '--raw-field')
            val = (a.split('=', 1)[1] if a.startswith('--') else a[2:]) if glued else nxt
            if val.startswith('query='):
                q = val[len('query='):]
                if a.startswith(('-F', '--field')):
                    from_file = from_file or q.startswith('@')
                query += q
            if not glued:
                i += 1
        elif a == '--input' or a.startswith('--input='):
            body = from_file = True
            if a == '--input':
                i += 1
        elif a in ('-H', '--header', '-q', '--jq', '-t', '--template', '--cache', '-p', '--preview', '--hostname'):
            i += 1
        elif not a.startswith('-') and path is None:
            path = a
        i += 1
    method = method or ('POST' if body else 'GET')
    if method == 'GET':
        return False
    p = (path or '').lstrip('/')
    if p == 'graphql':
        return (from_file and subagent) or bool(re.search(r'mergePullRequest|enablePullRequestAutoMerge', query))
    if method == 'DELETE' and re.search(r'(^|/)git/refs/', p):
        return False
    return bool(re.search(r'(^|/)pulls/[^/]+/merge(/|$)|/merges(/|$)|(^|/)git/refs/', p))
