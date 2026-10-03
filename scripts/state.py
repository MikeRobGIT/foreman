#!/usr/bin/env python3
"""foreman's per-repo state directory, shared by ui.py, hooks.py, gate-lock.sh and the status mod.

  state.py <path>      print the state dir of <path>'s repo (prints nothing, exit 0, outside a git repo)

state_dir(main) = $FOREMAN_HOME (default ~/.claude/foreman)/<repo_key(main)>, where repo_key is the first 12
hex digits of sha1(origin url + "\\n" + realpath(main)) and main is the repo's main checkout (the first
`git worktree list` entry). Directories are 0700 and files 0600.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

DEFAULT_HOME = os.path.expanduser('~/.claude/foreman')


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


def main(argv):
    if len(argv) == 2 and not argv[1].startswith('-'):
        m = main_checkout(argv[1])
        if m:
            print(state_dir(m))
        return
    raise SystemExit(__doc__)


if __name__ == '__main__':
    main(sys.argv)
