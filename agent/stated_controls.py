from __future__ import annotations

import re
from pathlib import Path

try:
    import yaml
except Exception:
    yaml = None

ROLE_WORDS = {
    'button': 'button', 'buttons': 'button',
    'link': 'link', 'links': 'link',
    'menuitem': 'menuitem', 'menuitems': 'menuitem',
    'menu item': 'menuitem', 'menu items': 'menuitem',
    'tab': 'tab', 'tabs': 'tab',
    'checkbox': 'checkbox', 'checkboxes': 'checkbox',
    'radio': 'radio', 'radios': 'radio',
    'textbox': 'textbox', 'textboxes': 'textbox',
    'text box': 'textbox', 'text boxes': 'textbox',
    'searchbox': 'searchbox', 'searchboxes': 'searchbox',
    'combobox': 'combobox', 'comboboxes': 'combobox',
    'combo box': 'combobox', 'combo boxes': 'combobox',
    'option': 'option', 'options': 'option',
    'heading': 'heading', 'headings': 'heading',
    'switch': 'switch',
    'dialog': 'dialog', 'dialogs': 'dialog',
    'region': 'region', 'regions': 'region',
    'form': 'form',
    'grid': 'grid',
    'listbox': 'listbox',
    'group': 'group',
}
PLURAL = {k for k, v in ROLE_WORDS.items() if k.replace(' ', '') != v}

CONTROL_ROLES = {'button', 'link', 'menuitem', 'tab', 'checkbox', 'radio',
                 'textbox', 'searchbox', 'combobox', 'option', 'switch'}

FAMILY = {
    'textbox': 'text', 'searchbox': 'text', 'combobox': 'text',
    'menuitem': 'menuitem', 'menuitemcheckbox': 'menuitem', 'menuitemradio': 'menuitem',
    'checkbox': 'check', 'switch': 'check',
}

def fam(r: str) -> str:
    return FAMILY.get(r, r)

_role_alt = '|'.join(sorted((re.escape(k) for k in ROLE_WORDS), key=len, reverse=True))
ROLE_RE = rf'(?:{_role_alt})'
ROLESET_RE = rf'(?P<roles>{ROLE_RE}(?:\s+or\s+{ROLE_RE})*)'
OPEN_Q = '"\u201c`'
CLOSE_Q = '"\u201d`'
NAME_RE = r'[\"\u201c`](?P<name>[^\"\u201c\u201d`]{1,80}?)[\"\u201d`]'
NAME_ANY = r'[\"\u201c`][^\"\u201c\u201d`]{1,80}?[\"\u201d`]'
CONT_RE = re.compile(r'\s*(?:,\s*(?:and\s+|or\s+)?|\s(?:and|or)\s+)' + NAME_RE)

PATTERNS = [

    re.compile(rf'\b{ROLESET_RE}(?:\s+role)?\s+(?:with|using|having|and|whose)\s+'
               rf'(?:the\s+|an?\s+|its\s+)?(?:exact\s+)?(?:accessible\s+)?names?\s+(?:is\s+)?'
               rf'(?:exactly\s+)?' + NAME_RE, re.I),

    re.compile(rf'\b{ROLESET_RE}(?:\s+role)?\s+(?:is\s+|are\s+)?(?:named|labell?ed|titled|called)'
               rf'(?:\s+exactly)?\s+' + NAME_RE, re.I),

    re.compile(rf'\b(?:the|a|an|one|unique|its|their)\s+{ROLESET_RE}\s+' + NAME_RE, re.I),

    re.compile(rf'\b(?:the|a|an|one|unique|its|their)\s+' + NAME_RE + rf'\s+{ROLESET_RE}\b', re.I),
]
NEG_RE = re.compile(r"\b(?:no|not|never|without|nor|neither|absent|disappears?|hidden|hides?|removed?|"
                    r"no longer|instead of|rather than|cannot|can't|must not|does not|doesn't)\b", re.I)

def _collect_strings(node, out, given=False):
    if isinstance(node, dict):
        is_given = str(node.get('keyword', '')).upper() == 'GIVEN'
        for k, v in node.items():
            if k in ('description', 'content') and isinstance(v, str):
                out.append((v, is_given))
            else:
                _collect_strings(v, out, given)
    elif isinstance(node, list):
        for x in node:
            _collect_strings(x, out, given)

def requirement_chunks(requirement_text: str):
    chunks = []
    if yaml is not None:
        try:
            data = yaml.safe_load(requirement_text)
            if isinstance(data, (dict, list)):
                _collect_strings(data, chunks)
        except Exception:
            chunks = []
    if not chunks:
        chunks = [(requirement_text, False)]
    return [(re.sub(r'\s+', ' ', t).strip(), g) for t, g in chunks]

def _clause_bounds(text, start, end):
    left = max(text.rfind(c, 0, start) for c in '.;:!?')
    rights = [text.find(c, end) for c in '.;!?']
    rights = [r for r in rights if r != -1]
    right = min(rights) if rights else len(text)
    return left + 1, right

def seed_values(chunks, stated_names=()):
    q = re.compile(r'[\"\u201c`](?P<v>[^\"\u201c\u201d`]{1,80}?)[\"\u201d`]')
    stated = {n.lower() for n in stated_names}
    vals = set()
    for text, given in chunks:
        for m in re.finditer(r'[^.]*\b[Ss]eed(?:ed| data)?\b[^.]*', text):
            for v in q.finditer(m.group(0)):
                v = v.group('v').strip()
                if len(v) >= 3:
                    vals.add(v.lower())
        if given:
            for v in q.finditer(text):
                v = v.group('v').strip()
                if len(v) >= 3 and v.lower() not in stated:
                    vals.add(v.lower())
    return vals

UQ_ROLE_WORDS = {'button': 'button', 'buttons': 'button', 'link': 'link', 'links': 'link',
                 'navigation link': 'link', 'navigation links': 'link', 'tab': 'tab', 'tabs': 'tab',
                 'menu item': 'menuitem', 'menu items': 'menuitem', 'menuitem': 'menuitem', 'menuitems': 'menuitem'}
_UQ_FIRST = r'[A-Z][a-z]+'
_UQ_NAME = _UQ_FIRST + r'(?: [A-Za-z][a-z]*){0,3}'

_UQ_START = r'(?:^|(?<=\n)|(?<=[.;:!?,]) )'
_UQ_SINGULAR = r'(?:navigation link|menu item|menuitem|button|link|tab)'
_UQ_PLURAL = r'(?:navigation links|menu items|menuitems|buttons|links|tabs)'
UQ_PATTERNS = [

    re.compile(_UQ_START + rf'(?P<list>{_UQ_NAME}(?:, {_UQ_NAME})*,? and(?: then)? {_UQ_NAME}) are '
               rf'(?P<role>{_UQ_PLURAL})(?![\w-])'),

    re.compile(_UQ_START + rf'(?P<list>{_UQ_NAME}) is an? (?P<role>{_UQ_SINGULAR})(?![\w-])'),

    re.compile(rf'\b[Tt]he unique (?P<list>{_UQ_NAME}) (?P<role>{_UQ_SINGULAR})(?![\w-])'),
]

UQ_STOP = {'a', 'an', 'the', 'this', 'that', 'these', 'those', 'its', 'their', 'his', 'her', 'our', 'your', 'my',
           'each', 'every', 'all', 'any', 'both', 'either', 'neither', 'some', 'no', 'not', 'only', 'also', 'other',
           'and', 'or', 'nor', 'then', 'but', 'if', 'when', 'while', 'after', 'before', 'once', 'where', 'which',
           'who', 'whose', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'has', 'have', 'had', 'does', 'do',
           'can', 'may', 'must', 'should', 'will', 'would', 'as', 'at', 'by', 'for', 'from', 'with', 'without',
           'into', 'onto', 'to', 'than', 'unique', 'visible', 'disabled', 'enabled', 'primary', 'separate'}

UQ_GENERIC = {'scenario', 'scenarios', 'user', 'users', 'owner', 'owners', 'admin', 'admins', 'administrator',
              'maintain', 'maintainer', 'maintainers', 'member', 'members', 'visitor', 'visitors', 'guest', 'guests',
              'author', 'authors', 'reviewer', 'reviewers', 'read', 'write', 'triage', 'account', 'accounts',
              'system', 'page', 'pages', 'row', 'rows', 'entry', 'entries', 'title', 'titles', 'name', 'names',
              'it', 'they', 'there', 'here', 'label', 'labels', 'action', 'actions', 'option', 'options', 'status',
              'repository', 'repositories', 'issue', 'issues', 'pull', 'request', 'requests', 'branch', 'branches',
              'workbook', 'worksheet', 'sheet', 'cell', 'cells', 'note', 'notes', 'book', 'books', 'shelf',
              'chapter', 'ticket', 'tickets', 'order', 'orders', 'result', 'results', 'list', 'lists', 'header',
              'footer', 'sidebar', 'toolbar', 'dialog', 'form', 'table', 'grid', 'column', 'columns', 'pr', 'prs',
              'button', 'buttons', 'link', 'links', 'tab', 'tabs', 'menu', 'navigation', 'control', 'controls',
              'field', 'fields', 'item', 'items'}
_UQ_SPLIT = re.compile(r',? and then |, and |, | and ')

def _uq_names(listed: str):
    names = [n.strip() for n in _UQ_SPLIT.split(listed)]
    for name in names:
        words = name.split(' ')
        if not name or not 1 <= len(words) <= 4 or not re.fullmatch(_UQ_FIRST, words[0]):
            return None
        if any(w.lower() in UQ_STOP for w in words) or name.lower() in UQ_GENERIC:
            return None
    return names

def uq_normalise(text: str) -> str:
    return re.sub(r'[ \t\r\f\v]*\n\s*', '\n', re.sub(r'[ \t\r\f\v]+', ' ', text)).strip()

def _uq_chunks(requirement_text: str):
    chunks = []
    if yaml is not None:
        try:
            data = yaml.safe_load(requirement_text)
            if isinstance(data, (dict, list)):
                _collect_strings(data, chunks)
        except Exception:
            chunks = []
    if not chunks:
        chunks = [(requirement_text, False)]
    return [uq_normalise(t) for t, _g in chunks]

def unquoted_statements(text: str):
    out = []
    for number, pat in enumerate(UQ_PATTERNS):
        for m in pat.finditer(text):
            names = _uq_names(m.group('list'))
            if not names:
                continue
            cs, ce = _clause_bounds(text, m.start(), m.end())
            if NEG_RE.search(text[cs:m.start()] + ' ' + text[m.end():ce]):
                continue
            role = UQ_ROLE_WORDS[m.group('role')]
            for name in names:
                out.append((role, name, m.start(), m.end(), number))
    return out

def unquoted_unique_controls(text: str):
    return [(role, name) for role, name, _s, _e, number in unquoted_statements(uq_normalise(text)) if number == 2]

def extract_stated_controls(requirement_text: str, unquoted: bool = True):
    chunks = requirement_chunks(requirement_text)
    found = {}
    for text, _given in chunks:
        for pat in PATTERNS:
            for m in pat.finditer(text):
                roles_txt = m.group('roles').lower()
                roles = {ROLE_WORDS[r.strip()] for r in re.split(r'\s+or\s+', roles_txt)}
                names = [(m.group('name'), m.start('name'), m.end())]
                plural = roles_txt.split(' or ')[-1] in PLURAL or 'names' in m.group(0).lower()
                if plural:
                    pos = m.end()
                    while True:
                        c = CONT_RE.match(text, pos)
                        if not c:
                            break
                        names.append((c.group('name'), c.start('name'), c.end()))
                        pos = c.end()
                cs, ce = _clause_bounds(text, m.start(), m.end())
                clause = text[cs:ce]

                chk = clause.replace(m.group('roles'), ' ')
                chk = re.sub(NAME_ANY, ' ', chk)
                if NEG_RE.search(chk):
                    continue
                for name, _s, _e in names:
                    name = name.strip()
                    if not name or len(name) > 60:
                        continue
                    key = (frozenset(roles), name)
                    if key not in found:
                        found[key] = {'roles': set(roles), 'name': name, 'context': clause.strip()[:220]}
    if unquoted:
        for text in _uq_chunks(requirement_text):
            for role, name, start, end, _number in unquoted_statements(text):
                key = (frozenset({role}), name)
                if key not in found:
                    cs, ce = _clause_bounds(text, start, end)
                    found[key] = {'roles': {role}, 'name': name,
                                  'context': ' '.join(text[cs:ce].split())[:220], 'unquoted': True}
    stated = list(found.values())
    return stated, seed_values(chunks, [s['name'] for s in stated])

SRC_EXT = {'.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.vue', '.svelte', '.html', '.json', '.hbs', '.ejs'}
JSX_EXT = {'.tsx', '.jsx', '.js', '.mjs', '.vue', '.svelte', '.html', '.hbs', '.ejs'}
TEST_RE = re.compile(r'(^|[\\/])(__tests__|tests?|e2e|__mocks__|spec)([\\/])|\.(test|spec)\.[a-z]+$', re.I)

def iter_sources(project_dir: Path):
    roots = [project_dir / 'frontend' / 'src', project_dir / 'backend' / 'src']
    extra = [project_dir / 'frontend' / 'index.html']
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob('*')):
            if not p.is_file() or p.suffix.lower() not in SRC_EXT:
                continue
            rel = p.relative_to(project_dir).as_posix()
            if 'node_modules' in rel or '/dist/' in rel or TEST_RE.search(rel):
                continue
            yield p, rel, ('frontend' if rel.startswith('frontend') else 'backend')
    for p in extra:
        if p.is_file():
            yield p, p.relative_to(project_dir).as_posix(), 'frontend'

def mask_comments(src: str) -> str:
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
            elif c == '\n' and quote != '`':
                quote = None
            i += 1
            continue
        if c in '"\'`':

            if c == "'":
                j = i - 1
                while j >= 0 and src[j] in ' \t':
                    j -= 1
                prev = src[j] if j >= 0 else '\n'
                if prev.isalnum():
                    i += 1
                    continue
            quote = c
            i += 1
            continue
        if c == '/' and i + 1 < n and src[i + 1] == '/':
            j = i - 1
            if j >= 0 and src[j] == ':':
                i += 2
                continue
            k = src.find('\n', i)
            k = n if k == -1 else k
            for t in range(i, k):
                out[t] = ' '
            i = k
            continue
        if c == '/' and i + 1 < n and src[i + 1] == '*':
            k = src.find('*/', i + 2)
            k = n if k == -1 else k + 2
            for t in range(i, k):
                if out[t] != '\n':
                    out[t] = ' '
            i = k
            continue
        if c == '<' and src.startswith('<!--', i):
            k = src.find('-->', i)
            k = n if k == -1 else k + 3
            for t in range(i, k):
                if out[t] != '\n':
                    out[t] = ' '
            i = k
            continue
        i += 1
    return ''.join(out)

VOID = {'input', 'img', 'br', 'hr', 'meta', 'link', 'area', 'base', 'col', 'embed', 'source', 'track', 'wbr'}
TAG_START = re.compile(r'<(/?)([A-Za-z][\w.:-]*)')

class El:
    __slots__ = ('name', 'start', 'tag_end', 'close', 'attrs', 'parent')

    def __init__(self, name, start, tag_end, attrs):
        self.name, self.start, self.tag_end, self.attrs = name, start, tag_end, attrs
        self.close = None
        self.parent = None

def _scan_tag_end(src, i):
    n = len(src)
    depth = 0
    quote = None
    tpl = []
    limit = min(n, i + 6000)
    while i < limit:
        c = src[i]
        if quote:
            if c == '\\':
                i += 2
                continue
            if quote == '`' and c == '$' and i + 1 < n and src[i + 1] == '{':
                depth += 1
                tpl.append(depth)
                quote = None
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c == '"' or c == '`' or (c == "'" and depth > 0) or (c == "'" and src[i - 1] == '='):
            quote = c
        elif c == '{':
            depth += 1
        elif c == '}':
            if tpl and tpl[-1] == depth:
                tpl.pop()
                depth -= 1
                quote = '`'
                i += 1
                continue
            depth = max(0, depth - 1)
        elif c == '>' and depth == 0:
            if i > 0 and src[i - 1] == '=':
                return None
            return i, src[i - 1] == '/'
        elif c == '<' and depth == 0 and i + 1 < n and (src[i + 1].isalpha() or src[i + 1] == '/'):
            return None
        i += 1
    return None

def parse_elements(src: str, html_mode: bool):
    els = []
    stack = []
    pos = 0
    for m in TAG_START.finditer(src):
        if m.start() < pos:
            continue
        s = m.start()
        prev = src[s - 1] if s > 0 else '\n'
        if not html_mode and not m.group(1) and (prev.isalnum() or prev in '_)]$.'):
            continue
        closing, name = m.group(1), m.group(2)
        if closing:
            k = src.find('>', m.end())
            if k == -1:
                continue
            for idx in range(len(stack) - 1, -1, -1):
                if stack[idx].name == name:
                    stack[idx].close = s
                    del stack[idx:]
                    break
            pos = k + 1
            continue
        r = _scan_tag_end(src, m.end())
        if r is None:
            continue
        end, selfclosing = r
        el = El(name, s, end + 1, src[m.end():end])
        el.parent = stack[-1] if stack else None
        els.append(el)
        if selfclosing or name.lower() in VOID and name.islower():
            el.close = end + 1
        else:
            stack.append(el)
        pos = end + 1
    return els

def attr_static(attrs: str, name: str):
    m = re.search(r'(?<![\w-])' + re.escape(name) + r'\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|\{\s*["\']([^"\']*)["\']\s*\}|(\{))', attrs)
    if not m:
        if re.search(r'(?<![\w-])' + re.escape(name) + r'(?![\w-])(?!\s*=)', attrs):
            return 'true'
        return None
    if m.group(4):
        return ''
    return next(g for g in m.groups()[:3] if g is not None)

def element_role(el: El, router_link: bool):
    r = attr_static(el.attrs, 'role')
    if r is not None:
        if r == '' or r == 'true':
            return None, False
        r = r.strip().split()[0].lower() if r.strip() else ''
        if r in ('presentation', 'none'):
            return 'none', True
        return r, True
    name = el.name
    low = name.lower()
    if name[0].islower():
        if low == 'a':
            if attr_static(el.attrs, 'href') is not None:
                return 'link', True
            return None, False
        if low == 'button':
            return 'button', True
        if low == 'input':
            t = attr_static(el.attrs, 'type')
            if t is None:
                return 'textbox', True
            if t in ('', 'true'):
                return None, False
            t = t.lower()
            return {'text': 'textbox', 'email': 'textbox', 'tel': 'textbox', 'url': 'textbox',
                    'search': 'searchbox', 'checkbox': 'checkbox', 'radio': 'radio',
                    'button': 'button', 'submit': 'button', 'reset': 'button', 'image': 'button',
                    'number': 'spinbutton', 'range': 'slider'}.get(t, None), t in (
                'text', 'email', 'tel', 'url', 'search', 'checkbox', 'radio', 'button', 'submit',
                'reset', 'image', 'number', 'range')
        if low == 'textarea':
            return 'textbox', True
        if low == 'select':
            return 'combobox', True
        if low == 'option':
            return 'option', True
        if re.fullmatch(r'h[1-6]', low):
            return 'heading', True
        if low == 'label':
            return 'label', True
        if low == 'dialog':
            return 'dialog', True
        return None, True

    base = name.split('.')[-1]
    if base in ('Link', 'NavLink') and router_link:
        return 'link', True
    if base.endswith('Button'):
        return 'button?', False
    if base.endswith('MenuItem'):
        return 'menuitem?', False
    if base.endswith('Link'):
        return 'link?', False
    return None, False

NAME_FROM_CONTENT = CONTROL_ROLES | {'heading', 'menuitemcheckbox', 'menuitemradio', 'label',
                                     'treeitem', 'cell', 'gridcell', 'row', 'columnheader',
                                     'rowheader', 'tooltip', 'none'}
NAMING_ATTRS = {'aria-label', 'title', 'placeholder', 'alt', 'value', 'label'}

def occurrence_role(els, pos, router_link):
    inner = None
    for el in els:
        if el.close is None:
            continue
        if el.start <= pos < max(el.close, el.tag_end):
            if inner is None or el.start >= inner.start:
                inner = el
    if inner is None:
        return None, False, 'outside markup'
    if inner.start <= pos < inner.tag_end:

        seg = inner.attrs[:pos - inner.start - 1 - len(inner.name)]
        am = None
        for am in re.finditer(r'([A-Za-z_][\w:-]*)\s*=(?!>)', seg):
            pass
        attr = am.group(1).lower() if am else ''
        if attr not in NAMING_ATTRS:
            return None, False, f'attr {attr or "?"} of <{inner.name}>'
        if attr == 'label' and inner.name[0].islower() and inner.name.lower() != 'option':
            return None, False, f'label attr of <{inner.name}>'
        role, certain = element_role(inner, router_link)
        return role, certain, f'<{inner.name} {attr}=...>'

    el = inner
    while el is not None:
        role, certain = element_role(el, router_link)
        if not certain:
            return role, False, f'text inside <{el.name}>'
        if role is not None and role != 'none':
            if role in NAME_FROM_CONTENT:

                if attr_static(el.attrs, 'aria-label') is not None or attr_static(el.attrs, 'aria-labelledby') is not None:
                    return None, False, f'text inside <{el.name} aria-label>'
                return role, True, f'text inside <{el.name}>'
            return role, True, f'text inside <{el.name}>'
        el = el.parent
    return 'none', True, 'plain text'

PLACEHOLDER = re.compile(r'<[^<>]{1,40}>')

_CHAR_ALT = {
    '&': r'(?:&|&amp;|&#38;|&#x26;|\{\s*[\'"]&[\'"]\s*\})',
    "'": r"(?:'|’|&apos;|&#39;|\')",
    '’': r"(?:'|’|&apos;|&#39;|\')",
}

def _word_rx(w):
    return ''.join(_CHAR_ALT.get(c, re.escape(c)) for c in w)

_GAP = r'(?:\s|&nbsp;|\{\s*[\'"]\s*[\'"]\s*\})+'

def _name_regex(literal: str):
    words = literal.strip().split()
    body = _GAP.join(_word_rx(w) for w in words)
    return re.compile(r'(?<![A-Za-z0-9_])' + body + r'(?![A-Za-z0-9_])', re.I)

_DYN_AFTER = r'(?:\s|&nbsp;)*(?:\$\{|\{(?![\'"]\s)|[\'"`]\s*\+)'
_DYN_BEFORE = r'(?:\}|\+\s*[\'"`])(?:\s|&nbsp;)*'

def _composable(name, src):
    words = name.split()
    if len(words) > 1:

        lw = [re.sub(r'\W', '', w).lower() for w in words]
        keys = {'_'.join(lw), '-'.join(lw), lw[0] + ''.join(w.capitalize() for w in lw[1:])}
        for k in keys:
            if re.search(r'[\'"`]' + re.escape(k) + r'[\'"`]', src, re.I):
                return True
    for k in range(1, len(words)):
        pre = _GAP.join(_word_rx(w) for w in words[:k])
        if re.search(r'(?<![A-Za-z0-9_])' + pre + _DYN_AFTER, src, re.I):
            return True
        suf = _GAP.join(_word_rx(w) for w in words[k:])
        if re.search(_DYN_BEFORE + suf + r'(?![A-Za-z0-9_])', src, re.I):
            return True
    return False

def _is_key_literal(src, s, e, name):
    if name == name.lower() or ' ' in name.strip():
        return False
    frag = src[s:e]
    if frag != frag.lower():
        return False
    if not (s > 0 and e < len(src) and src[s - 1] in '\'"`' and src[e] == src[s - 1]):
        return False

    before = src[max(0, s - 60):s - 1]
    return bool(re.search(r'(?:(?:add|remove)EventListener\(\s*|\.(?:type|key|code)\s*[!=]==?\s*|case\s+)$', before))

def _dyn_after(src, e):
    tail = src[e:e + 6]
    return bool(re.match(r'\s*(\$\{|\{(?![\'"]\s)|[\'"`]\s*\+|:?\s*\$\{)', tail))

_LEFT_DELIMS = set('>{}"`' + "'")
_RIGHT_DELIMS = set('<{}"`' + "'")
_WS = set(' \t\r\n')

def _is_exact(src, s, e, is_template):
    j = s - 1
    while j >= 0 and src[j] in _WS:
        j -= 1
    if j < 0 or src[j] not in _LEFT_DELIMS:
        return False
    if is_template:
        return True
    k = e
    while k < len(src) and src[k] in _WS:
        k += 1
    return k < len(src) and src[k] in _RIGHT_DELIMS

def _is_text_node(src, s, e):
    j = s - 1
    while j >= 0 and src[j] in _WS:
        j -= 1
    k = e
    while k < len(src) and src[k] in _WS:
        k += 1
    return j >= 0 and src[j] == '>' and k < len(src) and src[k] == '<'

def _split_by_markup(name, src):
    words = name.split()
    for k in range(1, len(words)):
        pre = _GAP.join(_word_rx(w) for w in words[:k])
        suf = re.compile(r'(?:\s|&nbsp;)*' + _GAP.join(_word_rx(w) for w in words[k:]) + r'(?![A-Za-z0-9_])', re.I)
        for m in re.finditer(r'(?<![A-Za-z0-9_])' + pre + r'(?:' + _GAP + r')?(?=<)', src, re.I):

            rest = re.sub(r'<[^<>]*>|\{\s*[\'"]\s*[\'"]\s*\}', ' ', src[m.end():m.end() + 300])
            if suf.match(rest):
                return True
    return False

NOT_CONTROL_RENDERED = {'heading': 'heading', 'none': 'text'}

def stated_control_problems(requirement_text: str, project_dir: Path) -> list:
    project_dir = Path(project_dir)
    stated, seeds = extract_stated_controls(requirement_text)

    seeds = seeds - {s['name'].strip().lower() for s in stated}
    stated_headings = {s['name'].strip().lower() for s in stated if 'heading' in s['roles']}
    files = []
    for p, rel, side in iter_sources(project_dir):
        try:
            raw = p.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue
        src = mask_comments(raw) if p.suffix.lower() != '.json' else raw
        files.append({'rel': rel, 'side': side, 'src': src, 'ext': p.suffix.lower(), 'els': None,
                      'router': bool(re.search(r"from\s+['\"](react-router(-dom)?|next/link|@tanstack/react-router)['\"]", raw))})
    if not files:
        return []
    problems = []
    for sc in stated:
        name, roles = sc['name'], sc['roles']
        lname = name.lower()

        if any(v == lname or re.search(r'(?<!\w)' + re.escape(v) + r'(?!\w)', lname) or
               re.search(r'(?<!\w)' + re.escape(lname) + r'(?!\w)', v) for v in seeds):
            continue
        is_template = bool(PLACEHOLDER.search(name))
        literals = [x.strip(' :') for x in PLACEHOLDER.split(name)]
        literals = [x for x in literals if len(x) >= 3]
        if is_template and not literals:
            continue
        if not is_template and re.search(r'\d', name):
            continue
        if not is_template and len(name) < 2:
            continue

        occ = {lit: [] for lit in (literals if is_template else [name])}
        for f in files:
            for lit in occ:
                rx = _name_regex(lit)
                for m in rx.finditer(f['src']):
                    if _is_key_literal(f['src'], m.start(), m.end(), lit):
                        continue
                    occ[lit].append((f, m.start(), m.end()))
        missing = [lit for lit, lst in occ.items() if not lst]
        role_txt = '/'.join(sorted(roles))
        if missing and not is_template and any(_composable(name, f['src']) for f in files):
            continue

        context = ('(stated without quotes) ' if sc.get('unquoted') else '') + sc['context']
        if missing:
            problems.append({'kind': 'stated control missing', 'role': role_txt, 'name': name,
                             'evidence': 'no occurrence of ' + ', '.join(repr(x) for x in missing) +
                                         ' in frontend/src or backend/src (tests excluded); requirement: ' + context,
                             'unquoted': bool(sc.get('unquoted'))})
            continue

        if not roles <= CONTROL_ROLES:
            continue
        lit = max(occ, key=len)
        lst = occ[lit]
        judged = []
        silent = False
        backend_seen = False
        for f, s, e in lst:
            if is_template and not _dyn_after(f['src'], e):
                continue
            if f['ext'] == '.json' or f['ext'] not in JSX_EXT or f['ext'] == '.ts':
                if f['side'] == 'backend':
                    backend_seen = True
                    continue
                silent = True
                break
            if f['els'] is None:
                f['els'] = parse_elements(f['src'], f['ext'] in ('.html', '.hbs', '.ejs', '.vue', '.svelte'))
            role, certain, desc = occurrence_role(f['els'], s, f['router'])
            if f['side'] == 'backend':
                backend_seen = True
                if role is None:
                    continue
            if not _is_exact(f['src'], s, e, is_template):

                if not certain or role is None or role.endswith('?') or fam(role) in {fam(x) for x in roles}:
                    silent = True
                    break
                continue
            if not certain or role is None or role.endswith('?') or role == 'label':
                silent = True
                break
            if role in NOT_CONTROL_RENDERED:

                if (role == 'none' and desc != 'plain text') or not _is_text_node(f['src'], s, e):
                    silent = True
                    break
            line = f['src'].count('\n', 0, s) + 1
            judged.append((role, f"{f['rel']}:{line} {desc}"))
        if silent or not judged:
            continue
        if any(fam(r) in {fam(x) for x in roles} for r, _ in judged):
            continue
        rendered = ''
        if all(r in NOT_CONTROL_RENDERED for r, _ in judged):

            if (backend_seen or any(_composable(name, f['src']) or _split_by_markup(name, f['src']) for f in files)
                    or lname in stated_headings or is_template):
                continue
            rendered = NOT_CONTROL_RENDERED[judged[0][0]] if len({r for r, _ in judged}) == 1 else 'heading or text'
        elif not all(r in CONTROL_ROLES or r in ('menuitemcheckbox', 'menuitemradio') for r, _ in judged):
            continue

        problem = {'kind': 'stated control not a control' if rendered else 'stated role differs', 'role': role_txt, 'name': name,
                   'evidence': '; '.join(f"{d} -> {'plain text, no role' if r == 'none' else 'role ' + r}"
                                         for r, d in judged[:4]) + ' | requirement: ' + context,
                   'unquoted': bool(sc.get('unquoted'))}
        if rendered:
            problem['rendered'] = rendered
        problems.append(problem)
    return problems
