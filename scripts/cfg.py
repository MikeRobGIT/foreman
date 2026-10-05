#!/usr/bin/env python3
"""Read a key from the foreman config's YAML frontmatter (no PyYAML needed).

Usage: cfg.py <repo> <key> [default]
       cfg.py <repo> --path          print the config file in use (exit 1 if none)
       cfg.py <repo> chain           print the reviewer chain, one `use<TAB>model<TAB>effort` line per entry

Config file: $FOREMAN_CONFIG, else <repo>/.claude/foreman.config.md, else the legacy
<repo>/.claude/improve-next.config.md. A list prints one item per line (a map item as JSON).
A missing key prints the default (or nothing) and exits 0.

Supported subset: `key: scalar`, `key: [a, b]`, `key:` followed by `- item` lines (an item may be
a `{ k: v, ... }` flow map) or by indented `k: v` lines (one level of map). `#` starts a comment
when it follows whitespace outside quotes.
"""
import json
import os
import re
import sys


def strip_comment(s):
    q = None
    for i, c in enumerate(s):
        if q:
            if c == q:
                q = None
        elif c in '"\'':
            q = c
        elif c == '#' and (i == 0 or s[i - 1] in ' \t'):
            return s[:i].rstrip()
    return s.rstrip()


def scalar(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in '"\'':
        return s[1:-1]
    return s


def split_flow(s):
    parts, depth, q, cur = [], 0, None, ''
    for c in s:
        if q:
            q = None if c == q else q
        elif c in '"\'':
            q = c
        elif c in '[{':
            depth += 1
        elif c in ']}':
            depth -= 1
        elif c == ',' and depth == 0:
            parts.append(cur)
            cur = ''
            continue
        cur += c
    if cur.strip():
        parts.append(cur)
    return [p.strip() for p in parts]


def value(s):
    s = s.strip()
    if s.startswith('{') and s.endswith('}'):
        out = {}
        for p in split_flow(s[1:-1]):
            k, _, v = p.partition(':')
            out[k.strip()] = scalar(v)
        return out
    if s.startswith('[') and s.endswith(']'):
        return [value(p) for p in split_flow(s[1:-1])]
    return scalar(s)


def parse(text):
    lines = text.split('\n')
    if not lines or lines[0].strip() != '---':
        return {}
    data, key = {}, None
    for raw in lines[1:]:
        if raw.strip() == '---':
            break
        line = strip_comment(raw)
        if not line.strip():
            continue
        indented = raw[:1] in ' \t' or raw.lstrip().startswith('- ')
        if not indented:
            k, _, v = line.partition(':')
            key = k.strip()
            data[key] = value(v) if v.strip() else None
            continue
        if key is None:
            continue
        item = line.strip()
        if item.startswith('- '):
            if not isinstance(data[key], list):
                data[key] = []
            k, sep, v = item[2:].partition(': ')
            if sep and re.fullmatch(r'[A-Za-z_][\w-]*', k):
                data[key].append({k: value(v)})
            else:
                data[key].append(value(item[2:]))
        elif ':' in item and isinstance(data[key], list):
            if data[key] and isinstance(data[key][-1], dict):
                k, _, v = item.partition(':')
                data[key][-1][k.strip()] = value(v)
        elif ':' in item:
            if not isinstance(data[key], dict):
                data[key] = {}
            k, _, v = item.partition(':')
            data[key][k.strip()] = value(v)
    return data


DEFAULTS = {
    'forbid_trailers': [],
    'gate_exempt': ['docs/**', '**/*.md'],
    'worker_agent_types': ['general-purpose', 'workflow-subagent'],
    'hooks_disabled': [],
    'design_spec_dir': 'docs/superpowers/specs',
    'spec_review_rounds': '3',
    'spec_phase_timeout': '60',
    'status_pane': 'true',
}


def config_path(repo, use_env=True):
    """use_env=False (the hooks) ignores FOREMAN_CONFIG, so a stray env var can't switch hooks on or off."""
    env = os.environ.get('FOREMAN_CONFIG') if use_env else None
    if env:
        return env if os.path.exists(env) else None
    for name in ('foreman.config.md', 'improve-next.config.md'):
        p = os.path.join(repo, '.claude', name)
        if os.path.exists(p):
            return p
    return None


def load(repo, use_env=True):
    p = config_path(repo, use_env)
    return parse(open(p).read()) if p else {}


def get(repo, key, default=None):
    """The key's value; when the config lacks it, `default`, else the key's DEFAULTS entry."""
    v = load(repo).get(key)
    if v is None:
        return default if default is not None else DEFAULTS.get(key)
    return v


def chain(repo, use_env=True):
    """Reviewer chain as (use, model, effort). Legacy `extra_reviewers` commands become `custom` entries."""
    cfg = load(repo, use_env)
    if cfg.get('reviewers'):
        return [(r.get('use', ''), r.get('model', ''), r.get('effort', '')) for r in cfg['reviewers'] if isinstance(r, dict)]
    return [('custom', cmd, '') for cmd in cfg.get('extra_reviewers') or [] if isinstance(cmd, str)]


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    repo, key = sys.argv[1], sys.argv[2]
    if key == '--path':
        p = config_path(repo)
        if not p:
            raise SystemExit(1)
        print(p)
        return
    if key == 'chain':
        for entry in chain(repo):
            print('\t'.join(entry))
        return
    v = get(repo, key, sys.argv[3] if len(sys.argv) > 3 else None)
    if v is None:
        return
    if isinstance(v, list):
        for item in v:
            print(json.dumps(item) if isinstance(item, dict) else item)
    elif isinstance(v, dict):
        print(json.dumps(v))
    else:
        print(v)


if __name__ == '__main__':
    main()
