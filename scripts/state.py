#!/usr/bin/env python3
"""foreman's per-repo state directory, shared by ui.py, hooks.py, gate-lock.sh and the status mod.

  state.py <path>      print the state dir of <path>'s repo (prints nothing, exit 0, outside a git repo)
  state.py mint <toplevel> <owner> <command> <untracked-before-file>
                       mint a gate receipt for the working tree (gate-lock.sh calls this after a passing gate)

state_dir(main) = $FOREMAN_HOME (default ~/.claude/foreman)/<repo_key(main)>, where repo_key is the first 12
hex digits of sha1(origin url + "\\n" + realpath(main)) and main is the repo's main checkout (the first
`git worktree list` entry). Directories are 0700 and files 0600.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

DEFAULT_HOME = os.path.expanduser('~/.claude/foreman')
RECEIPT_TTL = 14 * 86400


def git(cwd, *args):
    """stdout of a git command, or None when it fails."""
    try:
        p = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def toplevel(path):
    out = git(path, 'rev-parse', '--show-toplevel')
    return out.strip() if out else None


def main_checkout(path):
    """The repo's main checkout (the first `git worktree list` entry), or None outside a repo."""
    for line in (git(path, 'worktree', 'list', '--porcelain') or '').splitlines():
        if line.startswith('worktree '):
            return line[len('worktree '):]
    return None


def repo_key(main):
    origin = (git(main, 'remote', 'get-url', 'origin') or '').strip()
    return hashlib.sha1(f'{origin}\n{os.path.realpath(main)}'.encode()).hexdigest()[:12]


def home():
    return os.environ.get('FOREMAN_HOME', DEFAULT_HOME)


def state_dir(main):
    return os.path.join(home(), repo_key(main))


def state_dirs(main):
    """state_dir(main), then the default-home dir when FOREMAN_HOME points elsewhere. Hooks check both,
    because a FOREMAN_HOME set only for the Bash tool doesn't reach hook processes (probe P4)."""
    key = repo_key(main)
    dirs = [os.path.join(home(), key)]
    default = os.path.join(DEFAULT_HOME, key)
    if os.path.realpath(default) != os.path.realpath(dirs[0]):
        dirs.append(default)
    return dirs


def ensure(d):
    """mkdir -p d with every level it creates 0700 (makedirs' mode covers only the leaf), plus d itself.
    Levels that already existed above d keep their mode: they may be ~/.claude or a temp dir."""
    missing = []
    p = d
    while p and not os.path.isdir(p):
        missing.append(p)
        p = os.path.dirname(p)
    for p in reversed(missing):
        os.makedirs(p, mode=0o700, exist_ok=True)
        os.chmod(p, 0o700)
    os.chmod(d, 0o700)
    return d


def write_json(path, data):
    """Write atomically (temp file + rename); mkstemp makes the file 0600."""
    d = ensure(os.path.dirname(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix='.tmp')
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ---- gate receipts (piece 1) ----------------------------------------------------------------------

def last_path(sd, top):
    """The pointer to the last receipt minted in checkout `top`: {tree, artifacts, time, toplevel}."""
    return os.path.join(sd, 'gate', 'last', hashlib.sha1(os.path.realpath(top).encode()).hexdigest())


def untracked(top):
    """Untracked, non-ignored paths, relative to the top level. Git lists an embedded repo as `dir/`."""
    p = subprocess.run(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=top, capture_output=True)
    return {f for f in p.stdout.decode('utf-8', 'surrogateescape').split('\0') if f}


def nested_paths(top, listed):
    """Paths under `top` that no receipt holds: embedded repos (`dir/` in the untracked listing) and registered
    worktrees. `git add -A` would stage them as 160000 gitlinks, or, for a worktree git no longer sees as a repo,
    as plain files, and HEAD^{tree} holds neither. A submodule HEAD tracks is not untracked, so it stays."""
    found = {f.rstrip('/') for f in listed if f.endswith('/')}
    for line in (git(top, 'worktree', 'list', '--porcelain') or '').splitlines():
        if line.startswith('worktree '):
            p = os.path.realpath(line[len('worktree '):])
            if p.startswith(top + os.sep):
                found.add(os.path.relpath(p, top))
    return found


def outside(paths, nested):
    return {p for p in paths if not any(p.rstrip('/') == n or p.startswith(n + '/') for n in nested)}


def not_ignored(top, paths, env):
    """`paths` minus those git ignores (judged against the index in `env`, which keeps a tracked path in). `git add`
    refuses a pathspec that names an ignored path, an exclude too ("The following paths are ignored"), which
    would fail every mint in a checkout that ignores .claude/worktrees/. Git never stages an ignored path anyway."""
    if not paths:
        return []
    p = subprocess.run(['git', 'check-ignore', '-z', '--stdin'], cwd=top, env=env, capture_output=True,
                       input='\0'.join(paths).encode('utf-8', 'surrogateescape'))
    if p.returncode > 1:   # 0 = some ignored, 1 = none
        raise subprocess.CalledProcessError(p.returncode, p.args, p.stdout, p.stderr)
    ignored = set(p.stdout.decode('utf-8', 'surrogateescape').split('\0'))
    return [x for x in paths if x not in ignored]


def write_tree(top, exclude):
    """The tree of the working copy, from a copy of the real index (keeps force-added files and existing blobs,
    so clean/eol/LFS filters don't re-run on unchanged files). `exclude` goes to the add as `:(exclude)` pathspecs,
    so git never reads those paths: no filter runs on an artifact, and an embedded repo with no commit can't make
    the add fail. A path the real index already tracks stays, which is what keeps the receipt equal to HEAD."""
    real = os.path.join(top, git(top, 'rev-parse', '--git-path', 'index').strip())
    fd, tmp = tempfile.mkstemp(suffix='.index')
    os.close(fd)
    try:
        if os.path.exists(real):
            shutil.copy2(real, tmp)
        else:
            os.remove(tmp)
        env = dict(os.environ, GIT_INDEX_FILE=tmp)
        specs = ['.'] + [f':(exclude,literal){p}' for p in not_ignored(top, sorted(exclude), env)]
        subprocess.run(['git', 'add', '-A', '--pathspec-from-file=-', '--pathspec-file-nul'], cwd=top, env=env,
                       input='\0'.join(specs).encode('utf-8', 'surrogateescape'), check=True, capture_output=True)
        return subprocess.run(['git', 'write-tree'], cwd=top, env=env, check=True, capture_output=True,
                              text=True).stdout.strip()
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def prune_receipts(sd):
    cutoff = time.time() - RECEIPT_TTL
    for d in (os.path.join(sd, 'gate'), os.path.join(sd, 'gate', 'last')):
        for name in (os.listdir(d) if os.path.isdir(d) else []):
            p = os.path.join(d, name)
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)


def needs_commit(top, paths):
    """`paths` minus what a push may leave uncommitted, by the hooks' own rule (hooks.exempt with receipt=True:
    gate_exempt, the backlog, the rules, worktree_env_files), so the note below never tells a worker to commit a
    worktree's .env.local. Imported here because hooks.py imports this module."""
    try:
        import hooks
        hooks._load()
        repo = hooks.repo_at(top)
        return [p for p in paths if not (repo and hooks.exempt(repo, p, receipt=True))]
    except Exception:   # a note must never cost the receipt
        return list(paths)


def mint(top, owner, command, before_file):
    """Record that `command` passed on the working tree of checkout `top`. Paths the gate created (untracked
    now, not before) are artifacts: left out of the receipt, and left out again at later mints here. The
    receipt goes to every state dir, so hooks find it whichever FOREMAN_HOME they see (probe P4)."""
    top = os.path.realpath(top)
    dirs = state_dirs(main_checkout(top) or top)
    with open(before_file, 'rb') as f:
        before = {p for p in f.read().decode('utf-8', 'surrogateescape').split('\0') if p}
    listed = untracked(top)
    nested = nested_paths(top, listed | before)
    now, before = outside(listed, nested), outside(before, nested)
    last = next((x for x in (read_json(last_path(d, top)) for d in dirs) if isinstance(x, dict)), {})
    old = set(last.get('artifacts') or [])
    new = now - before
    artifacts = new | (old & now)
    swept = needs_commit(top, sorted((now & before) - old))
    if new:
        print('gate-lock: the gate created untracked files; they are not part of the receipt: '
              + ', '.join(sorted(new)[:10]), file=sys.stderr)
    if swept:
        print('gate-lock: these untracked files are part of the gated tree; commit them, or delete them and '
              're-run the gate: ' + ', '.join(swept[:10]), file=sys.stderr)
    tree = write_tree(top, artifacts | nested)
    for sd in dirs:
        write_json(os.path.join(sd, 'gate', tree), {'command': command, 'owner': owner, 'time': time.time(),
                                                    'toplevel': top})
        write_json(last_path(sd, top), {'tree': tree, 'artifacts': sorted(artifacts), 'time': time.time(),
                                        'toplevel': top})
        prune_receipts(sd)
    return tree


def main(argv):
    if len(argv) == 2 and not argv[1].startswith('-'):
        m = main_checkout(argv[1])
        if m:
            print(state_dir(m))
        return
    if len(argv) == 6 and argv[1] == 'mint':
        print(mint(*argv[2:]))
        return
    raise SystemExit(__doc__)


if __name__ == '__main__':
    main(sys.argv)
