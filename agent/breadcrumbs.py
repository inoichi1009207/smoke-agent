from __future__ import annotations

import re
from pathlib import Path

from .stated_controls import mask_comments

SRC_EXT = {'.tsx', '.jsx', '.js', '.ts', '.mjs'}
TEST_RE = re.compile(r'(^|[\\/])(__tests__|tests?|e2e|__mocks__|spec)([\\/])|\.(test|spec)\.[a-z]+$', re.I)
BREADCRUMB_RE = re.compile(r'bread\s*-?\s*crumbs?', re.I)
MAP_RE = re.compile(r'\.map\(\s*(?:\(\s*([A-Za-z_$][\w$]*)\s*(?::[^,()]*)?,\s*([A-Za-z_$][\w$]*)\s*(?::[^,()]*)?\)\s*=>|'
                    r'function\s*\(\s*([A-Za-z_$][\w$]*)\s*,\s*([A-Za-z_$][\w$]*)\s*\))')
TARGET_RE = re.compile(r'(?<![\w-])(?:to|href)\s*=\s*(?:\{\s*`([^`]*)`\s*\}|"([^"]*)"|\'([^\']*)\'|\{([^}]*)\})')
CHOICE_RE = re.compile(r'(?:\?|&&|\|\|)\s*\(?\s*<[A-Za-z]')
FILE_COMPONENT = re.compile(r'(?:File|Blob)(?:View(?:er)?|Detail|Content)?(?:Page|View|Route|Screen)?')
DIR_AWARE = re.compile(r"""\btype\s*[!=]==?\s*['"](?:dir|directory|tree|folder)['"]|['"](?:dir|directory|tree|folder)['"]\s*"""
                       r"""[!=]==?|\bis_?[Dd]ir(?:ectory)?\b|\bisFolder\b|\bArray\.isArray\(""")

def _sources(project_dir: Path):
    root = project_dir / 'frontend' / 'src'
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.rglob('*')):
        if not p.is_file() or p.suffix.lower() not in SRC_EXT:
            continue
        rel = p.relative_to(project_dir).as_posix()
        if 'node_modules' in rel or '/dist/' in rel or TEST_RE.search(rel):
            continue
        try:
            out.append((rel, mask_comments(p.read_text(encoding='utf-8', errors='replace'))))
        except Exception:
            continue
    return out

def routes(files):
    found = []
    for _rel, src in files:
        for m in re.finditer(r'<Route\b', src):
            window = src[m.end():m.end() + 400]
            nxt = window.find('<Route')
            window = window if nxt == -1 else window[:nxt]
            p = re.search(r'(?<![\w-])path\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|\{\s*["\'`]([^"\'`]*)["\'`]\s*\})', window)
            e = re.search(r'(?<![\w-])element\s*=\s*\{\s*<\s*([A-Z][\w.]*)', window)
            if p and e:
                found.append((next(g for g in p.groups() if g is not None), e.group(1)))
        for m in re.finditer(r'(?<![\w$])path\s*:\s*[\'"`]([^\'"`]*)[\'"`]\s*,\s*element\s*:\s*<\s*([A-Z][\w.]*)', src):
            found.append((m.group(1), m.group(2)))
    for path, _c in found:
        if not path.startswith('/') and path != '*':
            return None
    return found

def _segments(path):
    return [s for s in path.split('/') if s]

def _match(route_segs, segs):
    if not route_segs:
        return 0 if not segs else None
    head = route_segs[0]
    if head == '*' or head.endswith('*'):
        return 0
    if not segs:
        return None
    rest = _match(route_segs[1:], segs[1:])
    if rest is None:
        return None
    if head.startswith(':'):
        return rest
    if '${' in segs[0] or segs[0] != head:
        return None
    return rest + 1

def resolve(template, table):
    path = re.split(r'[?#]', template, 1)[0]
    segs = _segments(path)
    if not path.startswith('/') or not segs:
        return None
    scored = []
    for route_path, component in table:
        score = _match(_segments(route_path), segs)
        if score is not None:
            scored.append((score, route_path, component))
    if not scored:
        return None
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1], scored[0][2]

def _call_end(src, open_index):
    depth = 0
    i, n = open_index, min(len(src), open_index + 8000)
    stack = []
    in_template = False
    brace = 0
    while i < n:
        c = src[i]
        if in_template:
            if c == '\\':
                i += 2
                continue
            if c == '`':
                in_template = False
            elif c == '$' and i + 1 < n and src[i + 1] == '{':
                in_template = False
                brace += 1
                stack.append(brace)
                i += 2
                continue
            i += 1
            continue
        if c in '"\'':
            if c == "'" and i > 0 and src[i - 1].isalnum():
                i += 1
                continue
            k = i + 1
            while k < n and src[k] != c and src[k] != '\n':
                k += 2 if src[k] == '\\' else 1
            i = k + 1
            continue
        if c == '`':
            in_template = True
        elif c == '{':
            brace += 1
        elif c == '}':
            if stack and stack[-1] == brace:
                stack.pop()
                in_template = True
            brace -= 1
        elif c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None

def _defines(src, component):
    base = re.escape(component.split('.')[-1])
    return bool(re.search(rf'(?:function|class)\s+{base}\b|(?:const|let|var)\s+{base}\s*[:=]', src))

def breadcrumb_findings(requirement_text: str, project_dir) -> list[dict[str, str]]:
    try:
        return _breadcrumb_findings(requirement_text, project_dir)
    except Exception:
        return []

def _breadcrumb_findings(requirement_text: str, project_dir) -> list[dict[str, str]]:
    if not BREADCRUMB_RE.search(requirement_text or ''):
        return []
    project_dir = Path(project_dir)
    files = _sources(project_dir)
    table = routes(files)
    if not table:
        return []
    findings = []
    for rel, src in files:
        for m in MAP_RE.finditer(src):
            index = m.group(2) or m.group(4)
            open_paren = src.index('(', m.start())
            end = _call_end(src, open_paren)
            if end is None:
                continue
            body = src[open_paren:end]
            ancestor = re.search(r'\.slice\(\s*0\s*,\s*' + re.escape(index) + r'\s*\+\s*1\s*\)', body)
            if not ancestor:
                continue
            if re.search(r'\bonClick\b|\bnavigate\s*\(|\bhistory\.push\s*\(', body):
                continue
            prefix_vars = re.findall(r'(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*[^;\n]*?\.slice\(\s*0\s*,\s*'
                                     + re.escape(index) + r'\s*\+\s*1\s*\)', body)
            targets, unresolved = [], False
            for t in TARGET_RE.finditer(body):
                template = t.group(1)
                if template is None:
                    unresolved = True
                    break
                uses_prefix = bool(re.search(r'\.slice\(\s*0\s*,\s*' + re.escape(index) + r'\s*\+\s*1\s*\)', template)) or any(
                    re.search(r'\$\{\s*(?:encodeURI(?:Component)?\(\s*)?' + re.escape(v) + r'\b', template) for v in prefix_vars)
                if not uses_prefix:
                    unresolved = True
                    break
                targets.append(template)
            if unresolved or not targets:
                continue
            if CHOICE_RE.search(body) and len(targets) < 2:
                continue
            resolved = {resolve(t, table) for t in targets}
            if len(resolved) != 1 or None in resolved:
                continue
            route_path, component = resolved.pop()
            if not _defines(src, component):
                continue
            if not FILE_COMPONENT.fullmatch(component.split('.')[-1]):
                continue
            if not ({'blob', 'file'} & {s.lower() for s in _segments(route_path)}):
                continue
            if DIR_AWARE.search(src):
                continue
            line = src.count('\n', 0, m.start()) + 1
            shown = ' '.join(targets[0].split())
            findings.append({
                'where': f'{rel}:{line}',
                'requirement': '(the requirement: the breadcrumbs return to the parent directory) '
                               + _breadcrumb_sentence(requirement_text),
                'problem': (f'The breadcrumb of {component} links every ancestor crumb -- a DIRECTORY -- to `{shown}`, '
                            f'which is the route {route_path} of {component} itself, the file page. Opening a directory '
                            f'crumb loads that directory as a file, so the breadcrumbs cannot return to the parent '
                            f'directory.'),
                'fix': ('Link each ancestor crumb (every segment but the last) to the directory view -- the same target '
                        'the file list uses to open a directory -- with that ancestor path; keep the last crumb (the '
                        'file itself) as it is.'),
            })
            break
    return findings

def _breadcrumb_sentence(text):
    flat = ' '.join((text or '').split())
    m = BREADCRUMB_RE.search(flat)
    if not m:
        return ''
    left = max(flat.rfind('. ', 0, m.start()) + 2, 0)
    right = flat.find('. ', m.end())
    return flat[left:right + 1 if right != -1 else len(flat)][:220]
