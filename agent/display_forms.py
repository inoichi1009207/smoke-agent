from __future__ import annotations

import os
import re
from pathlib import Path

QUOTE_RE = re.compile(r'[“"`]([^“”"`]{1,60})[”"`]')
DISPLAY_BEFORE = re.compile(
    r'(display|show|shown|visible|marker|badge|status|state|role|label|text|'
    r'named|heading|reads?|appears?|title|link|button|tab|option)\w*\W*$', re.I)
DISPLAY_AFTER = re.compile(r'^\W{0,3}(?:(?:are|is)\s+(?:an?\s+)?)?(marker|badge|status|'
                           r'role|label|text|link|button|tab|heading|filter|option)', re.I)
STOP = {'The', 'This', 'That', 'Each', 'Any', 'Its', 'Their', 'Every', 'Target', 'New', 'Current', 'Same', 'Selected',
        'His', 'Her', 'Your', 'Our', 'No', 'One', 'An', 'Only', 'Existing', 'Distinct'}
UNQUOTED_RE = re.compile(r'\b([A-Z][a-z]{2,})(?:/([A-Z][a-z]{2,}))?\s+(?:status\s+)?(marker|badge|role)\b')
SHOWN_AS_RE = re.compile(r'\b(?:shown|displayed) as ([A-Z][a-z]{2,})\b')

def _flat(text: str) -> str:
    return re.sub(r'\s+', ' ', text.replace("''", "'"))

def stated_forms(requirement_text: str) -> dict:
    text = _flat(requirement_text)
    plain: dict[str, str] = {}
    slash: dict[str, str] = {}
    quotes = list(QUOTE_RE.finditer(text))
    verdict = []
    for m in quotes:
        q = m.group(1).strip()
        before = text[max(0, m.start() - 60):m.start()]
        after = text[m.end():m.end() + 25]
        verdict.append([False, False])
        if not q or len(q.split()) > 6 or ('<' in q and '>' in q):
            continue
        if '/' in q and re.fullmatch(r'[A-Za-z ]+/[A-Za-z ]+', q):
            sent = re.split(r'[.;:] ', text[max(0, m.start() - 160):m.start()])[-1]
            if re.search(r'heading', sent, re.I):
                slash.setdefault(q, sent + '[' + q + ']')
            continue
        if not q[0].isupper() or len(q.split()) > 4 or not re.search(r'[a-z]', q):
            continue
        verdict[-1] = [True, bool(DISPLAY_BEFORE.search(before) or DISPLAY_AFTER.search(after))]

    for _ in range(3):
        for i in range(len(quotes) - 1):
            gap = text[quotes[i].end():quotes[i + 1].start()]
            if re.fullmatch(r'\s*(,|and|or|/|, and|, or)\s*', gap):
                a, b = verdict[i], verdict[i + 1]
                if a[0] and b[0]:
                    a[1] = b[1] = a[1] or b[1]
    for m, (shape, display) in zip(quotes, verdict):
        if shape and display:
            plain.setdefault(m.group(1).strip(), text[max(0, m.start() - 60):m.end() + 25])
    for m in UNQUOTED_RE.finditer(text):
        for w in (m.group(1), m.group(2)):
            if w and w not in STOP:
                plain.setdefault(w, text[max(0, m.start() - 40):m.end() + 10])
    for m in SHOWN_AS_RE.finditer(text):
        if m.group(1) not in STOP:
            plain.setdefault(m.group(1), text[max(0, m.start() - 40):m.end() + 10])
    return {'plain': plain, 'slash_headings': slash}

TEST_PAT = re.compile(r'(\.test\.|\.spec\.|__tests__|[\\/]tests?[\\/]|e2e|[\\/]node_modules[\\/]|[\\/]dist[\\/]|'
                      r'[\\/]build[\\/])', re.I)
PRUNE = {'node_modules', 'dist', 'build', '.git', 'coverage', 'test', 'tests', '__tests__', 'e2e', 'e2e-support',
         'test-e2e', '_memory_', '.arc', '.arc-test-db'}

def _files(root: Path, exts) -> list[Path]:
    if not root.is_dir():
        return []
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix in exts and not TEST_PAT.search(str(p.relative_to(root.parent))):
                out.append(p)
    return sorted(out)

def _line(src: str, pos: int) -> int:
    return src.count('\n', 0, pos) + 1

def _strip_comments(src: str) -> str:
    out = list(src)
    i, n = 0, len(src)
    quote = None
    while i < n:
        c = src[i]
        if quote:
            if c == '\\':
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in '\'"`':
            prev = src[i - 1] if i else ' '
            if prev in '=(,:[{?!&|+ \t\n' and c != '`' or c == '`':
                quote = c
            i += 1
            continue
        for opener, closer in (('{/*', '*/}'), ('/*', '*/')):
            if src.startswith(opener, i):
                j = src.find(closer, i)
                j = n if j < 0 else j + len(closer)
                for k in range(i, j):
                    if out[k] != '\n':
                        out[k] = ' '
                i = j
                break
        else:
            if src.startswith('//', i) and (i == 0 or src[i - 1] != ':'):
                j = src.find('\n', i)
                j = n if j < 0 else j
                for k in range(i, j):
                    out[k] = ' '
                i = j
                continue
            i += 1
            continue
    return ''.join(out)

def _prev_nonws(src: str, pos: int):
    j = pos - 1
    while j >= 0 and src[j] in ' \t\r\n':
        j -= 1
    return (src[j] if j >= 0 else '', j)

def jsx_child_exprs(src: str):
    for m in re.finditer(r'\{', src):
        i = m.start()
        if i and src[i - 1] == '$':
            continue
        prev, j = _prev_nonws(src, i)
        if prev not in '>}':
            continue
        if prev == '>' and j and src[j - 1] in '=-':
            continue
        if prev == '}':
            line_start = src.rfind('\n', 0, i) + 1
            if '<' not in src[line_start:i] and not src[line_start:i].strip().startswith('}'):
                continue
            if src[line_start:i].strip().startswith('}'):
                continue
        depth, k = 0, i
        while k < len(src):
            if src[k] == '{':
                depth += 1
            elif src[k] == '}':
                depth -= 1
                if depth == 0:
                    break
            k += 1
        expr = src[i + 1:k]
        if '\n' in expr and len(expr) > 400:
            continue
        yield i, expr.strip()

GENERIC_FIELDS = {'value', 'name', 'id', 'key', 'type', 'className', 'href', 'to', 'htmlFor', 'placeholder',
                  'defaultValue', 'method', 'mode', 'tab', 'activeTab', 'filter', 'variant', 'sort', 'order', 'kind',
                  'path'}

def field_evidence(sources, value: str, frontend: bool = False) -> dict:
    v = re.escape(value)
    if frontend:
        pats = [re.compile(r'\.(\w+)\s*(?:===|!==|==|!=)\s*[\'"]' + v + r'[\'"]'),
                re.compile(r'[\'"]' + v + r'[\'"]\s*(?:===|!==|==|!=)\s*[\w?]+(?:\??\.\w+)*\??\.(\w+)\b')]
    else:
        pats = [re.compile(r'\b(\w+)\s*(?:===|!==|==|!=|=)\s*[\'"]' + v + r'[\'"]'),
                re.compile(r'[\'"]' + v + r'[\'"]\s*(?:===|!==|==|!=)\s*[\w.?]*?\b(\w+)\b'),
                re.compile(r'\b(\w+)\s*:\s*[\'"]' + v + r'[\'"]'),
                re.compile(r'\b(\w+)\s+[A-Za-z]+(?:\([\d, ]*\))?(?:\s+[A-Za-z]+)*?\s+(?i:DEFAULT)\s+[\'"]' + v + r'[\'"]'),
                re.compile(r'\b(\w+)\s+(?i:IN)\s*\([^)]*[\'"]' + v + r'[\'"]')]
    ev: dict = {}
    for _path, rel, src in sources:
        for rx in pats:
            for m in rx.finditer(src):
                f = m.group(1)
                if f in GENERIC_FIELDS or f.isdigit() or len(f) < 3:
                    continue
                ev.setdefault(f, (rel, _line(src, m.start()), src[m.start():m.end()][:80]))
    return ev

def field_evidence_all(be, fe, value: str) -> dict:
    ev = field_evidence(fe, value, frontend=True)
    ev.update(field_evidence(be, value))
    return ev

CASE_CLASS = re.compile(r'\b(capitalize|uppercase|text-transform)\b')

def _transform_near(src: str, pos: int) -> bool:
    open_tag = src.rfind('<', 0, pos)
    return bool(open_tag >= 0 and CASE_CLASS.search(src[open_tag:pos]))

def inconsistent_display_forms(requirement_text: str, project_dir) -> list[dict]:
    proj = Path(project_dir)
    forms = stated_forms(requirement_text)
    fe = []
    for p in _files(proj / 'frontend' / 'src', {'.tsx', '.jsx', '.ts', '.js'}):
        fe.append((p, p.relative_to(proj).as_posix(), _strip_comments(p.read_text(encoding='utf-8', errors='replace'))))
    be = []
    be_root = proj / 'backend'
    for p in _files(be_root, {'.js', '.ts', '.mjs', '.cjs', '.py', '.sql'}):
        if p.parent == be_root and p.name.endswith('config.js'):
            continue
        be.append((p, p.relative_to(proj).as_posix(), _strip_comments(p.read_text(encoding='utf-8', errors='replace'))))
    jsx = [f for f in fe if f[0].suffix in ('.tsx', '.jsx')]
    out: list[dict] = []
    seen: set = set()

    def add(kind, stated, app_form, rel, line, evidence, note=''):
        k = (kind, rel, line)
        if k in seen:
            for d in out:
                if (d['kind'], d['file'], d['line']) == k and stated not in d['stated']:
                    d['stated'] += ' | ' + stated
            return
        seen.add(k)
        out.append({'kind': kind, 'stated': stated, 'app_form': app_form, 'file': rel, 'line': line,
                    'evidence': evidence, 'note': note})

    by_field: dict = {}
    capital: dict = {}
    for w in forms['plain']:
        if not re.fullmatch(r'[A-Z][a-z]{2,}', w):
            continue
        for f, e in field_evidence_all(be, fe, w.lower()).items():
            by_field.setdefault(f, {})[w] = e
        capital.update(field_evidence_all(be, fe, w))
    enum_fields = {f: ws for f, ws in by_field.items() if len(ws) >= 2 and f not in capital}
    if enum_fields:
        for _p, rel, src in jsx:
            for pos, expr in jsx_child_exprs(src):
                m = re.fullmatch(r'[A-Za-z_]\w*(?:\??\.\w+)*\??\.(\w+)', expr)
                if not m or m.group(1) not in enum_fields:
                    continue
                ws = enum_fields[m.group(1)]
                erel, eline, esnip = ws[sorted(ws)[0]]
                add('raw-stored-value', '/'.join(sorted(ws)), '{' + expr + '}', rel, _line(src, pos),
                    f"field '{m.group(1)}' stores {sorted(x.lower() for x in ws)} (e.g. {erel}:{eline}: {esnip})",
                    'css case transform on element' if _transform_near(src, pos) else '')

    for tmpl in forms['slash_headings']:
        m = re.fullmatch(r'([a-z]+) name', tmpl.split('/')[-1].strip().lower())
        if not m:
            continue
        pre = m.group(1)[:4]
        for _p, rel, src in jsx:
            for hm in re.finditer(r'<h1\b[^>]*>(.*?)</h1>', src, re.S):
                em = re.fullmatch(r'\{\s*([\w?.]+)\s*\}', hm.group(1).strip())
                if not em:
                    continue
                parts = em.group(1).replace('?', '').split('.')
                if (len(parts) >= 2 and parts[-1] == 'name' and any(pre in p.lower() for p in parts[:-1])) or \
                        (len(parts) == 1 and pre in parts[0].lower() and parts[0].lower().endswith('name')):
                    add('template-part-only', tmpl, '<h1>' + hm.group(1).strip() + '</h1>', rel,
                        _line(src, hm.start()), 'h1 renders only the last template part')
    out.sort(key=lambda d: (d['file'], d['line'], d['kind']))
    return out
