from __future__ import annotations

import re
from pathlib import Path

try:
    import yaml
except Exception:
    yaml = None

Q = r'"([^"]{1,60})"'

def _norm(s: str) -> str:
    s = s.replace("“", '"').replace("”", '"').replace("`", '"').replace("→", "->")
    return re.sub(r"\s+", " ", s).strip()

def _texts(requirement_text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    data = None
    if yaml is not None:
        try:
            data = yaml.safe_load(requirement_text)
        except Exception:
            data = None
    if not isinstance(data, (dict, list)):
        return [("?", requirement_text)]

    def walk(n, rid):
        if isinstance(n, dict):
            rid = n.get("id", rid) or rid
            for k, v in n.items():
                if isinstance(v, str) and k in ("description", "content"):
                    out.append((str(rid), v))
                elif not isinstance(v, str):
                    walk(v, rid)
        elif isinstance(n, list):
            for x in n:
                walk(x, rid)

    walk(data, "ROOT")
    return out

def _sentences(text: str) -> list[str]:
    text = _norm(re.sub(r"\s*Seed values:.*?(?=$|\n)", "", text))
    return [s.strip() for s in re.split(r"(?<=[.;])\s+(?=[A-Z\"])", text) if s.strip()]

def _e(source, name, role, chain=None, data=False):
    return {"source": (source or "").strip() or None, "name": name, "role": role, "chain": chain, "data": data}

PATTERNS: list[tuple[str, re.Pattern, object]] = [
    ("from-X-link-open-Y-link",
     re.compile(r"\b(?:[Ff]rom|[Oo]pen) (?:the )?(?P<src>[\w' -]*?)\s*" + Q.replace("(", "(?P<a>", 1)
                + r" link,? (?:and )?(?:then )?(?:open |click |activate )?(?:then )?(?:its |the )"
                + Q.replace("(", "(?P<b>", 1) + r" link"),
     lambda m, p: [_e(m["src"], m["a"], "link"), _e(None, m["b"], "link", chain=-1)]),
    ("Y-is-a-link-on-the-X-page",
     re.compile(Q.replace("(", "(?P<a>", 1) + r" is a link on the (?P<src>[\w -]+? page)"),
     lambda m, p: [_e(m["src"], m["a"], "link")]),
    ("through-Y-in-the-X-menu",
     re.compile(r"through " + Q.replace("(", "(?P<a>", 1) + r" in the (?P<src>[\w -]+? menu)"),
     lambda m, p: [_e(m["src"], m["a"], None)]),
    ("X-link-Y-opens-page-with-link-Z",
     re.compile(r"The (?P<src>[\w-]+?)[- ]link " + Q.replace("(", "(?P<a>", 1)
                + r" opens a page with (?:the |a )?link " + Q.replace("(", "(?P<b>", 1)),
     lambda m, p: [_e(m["src"], m["a"], "link"), _e(None, m["b"], "link", chain=-1)]),
    ("opened-by-link-Y-from-X-page",
     re.compile(r"opened by the (?:unique )?link (?:named )?" + Q.replace("(", "(?P<a>", 1)
                + r" (?:from|on) the (?P<src>[\w -]+? page)"),
     lambda m, p: [_e(m["src"], m["a"], "link")]),
    ("X-page-Y-link",
     re.compile(r"the (?P<src>home)-page " + Q.replace("(", "(?P<a>", 1) + r" link"),
     lambda m, p: [_e(m["src"] + " page", m["a"], "link")]),
    ("X-has-Y-link",
     re.compile(r"^The (?P<src>[\w -]+?) (?:has|provides) (?:a |an )?(?:navigation )?"
                + Q.replace("(", "(?P<a>", 1) + r" link"),
     lambda m, p: [_e(m["src"], m["a"], "link")]),
    ("X-provides-navigation-link-Y",
     re.compile(r"^The (?P<src>[\w -]+?) (?:[^.;]*?,? and )?provides a navigation link " + Q.replace("(", "(?P<a>", 1)),
     lambda m, p: [_e(m["src"], m["a"], "link")]),
    ("X-presents-Y-as-navigation-link",
     re.compile(r"^The (?P<src>[\w -]+?) presents " + Q.replace("(", "(?P<a>", 1) + r" as a navigation link"),
     lambda m, p: [_e(m["src"], m["a"], "link")]),
    ("X-opens-Y-through-a-link",
     re.compile(r"^The (?P<src>[\w -]+?) opens " + Q.replace("(", "(?P<a>", 1) + r" through a link"),
     lambda m, p: [_e(m["src"], m["a"], "link")]),
    ("that-page-has-Y-link",
     re.compile(r"(?:^|; )[Tt]hat page has an? " + Q.replace("(", "(?P<a>", 1) + r" link"),
     lambda m, p: [_e(None, m["a"], "link", chain="prev")]),
    ("X-pages-expose-link-named-Y",
     re.compile(r"The (?P<src>[\w ]+? pages?) (?:each )?exposes? one (?:[\w-]+ )?link named " + Q.replace("(", "(?P<a>", 1)),
     lambda m, p: [_e(m["src"], m["a"], "link")]),
    ("control-named-Y-in-the-X-navigation",
     re.compile(r"(?P<role>link or button|button|link) (?:with accessible name|named) (?:exactly )?"
                + Q.replace("(", "(?P<a>", 1)
                + r" in the (?P<src>(?:global|top|homepage|main|primary)[\w -]*?navigation(?: bar)?)"),
     lambda m, p: [_e(m["src"], m["a"], None if "or" in m["role"] else m["role"])]),
    ("from-the-X-using-control-Y",
     re.compile(r"from the (?P<src>sidebar|[\w -]+? bar) using the (?:unique )?(?P<role>button|link) named "
                + Q.replace("(", "(?P<a>", 1)),
     lambda m, p: [_e(m["src"], m["a"], m["role"])]),
    ("Ys-navigation-entries-have-link-roles",
     re.compile(r"(?P<list>(?:\"[^\"]{1,60}\",? (?:and )?)+)navigation entries have link roles"),
     lambda m, p: [_e(None, n, "link") for n in re.findall(Q, m["list"])]),
    ("menu-contains-link-named-Y",
     re.compile(r"(?:[Ii]ts|[Tt]he) (?P<src>[\w -]*?menu) contains (?:exactly )?one link named " + Q.replace("(", "(?P<a>", 1)),
     lambda m, p: [_e(_menu_from_prev(m["src"], p + " " + m.string[:m.start()]), m["a"], "link")]),
    ("record-provides-link-named-by-data",
     re.compile(r"provides a link whose accessible name is the (?P<data>[\w ]+? name)"),
     lambda m, p: [_e("home page" if re.search(r"\bhome ?page\b", p, re.I) else None, m["data"], "link", data=True)]),
    ("Y-is-a-link",
     re.compile(Q.replace("(", "(?P<a>", 1) + r" is a link\.?$"),
     lambda m, p: [_e(None, m["a"], "link")]),
]

def _menu_from_prev(src: str, prev: str) -> str:
    src = (src or "").strip()
    if src.lower() in ("", "menu", "its menu") and prev:
        mm = re.findall(r'"([^"]*menu)"', prev, re.I)
        if mm:
            return mm[-1]
    return src

def extract_edges(requirement_text: str) -> list[dict]:
    edges: list[dict] = []
    seen: dict[tuple, int] = {}
    for rid, text in _texts(requirement_text):
        sents = _sentences(text)
        last_in_prev: int | None = None
        for i, s in enumerate(sents):
            prev = sents[i - 1] if i else ""
            got_here: list[int] = []
            for pname, rx, build in PATTERNS:
                for m in rx.finditer(s):
                    parts = build(m, prev)
                    idxs: list[int] = []
                    for part in parts:
                        chain = part.pop("chain")
                        parent = None
                        if chain == -1 and idxs:
                            parent = idxs[-1]
                        elif chain == "prev":
                            parent = got_here[-1] if got_here else last_in_prev
                        if chain is not None and parent is None:
                            continue
                        key = (part["source"], part["name"], part["role"], parent)
                        if key in seen:
                            idxs.append(seen[key])
                            continue
                        e = dict(part, parent=parent, req=rid, pattern=pname, quote=s)
                        edges.append(e)
                        seen[key] = len(edges) - 1
                        idxs.append(len(edges) - 1)
                    got_here.extend(idxs)

            last_in_prev = got_here[-1] if got_here else None

    keep = []
    for i, e in enumerate(edges):
        if e["source"] is None and e["parent"] is None:
            if any(j != i and o["name"] == e["name"] and o["role"] == e["role"] and (o["source"] or o["parent"] is not None)
                   for j, o in enumerate(edges)):
                continue
        keep.append(i)
    remap = {old: new for new, old in enumerate(keep)}
    out = []
    for old in keep:
        e = dict(edges[old])
        e["parent"] = remap.get(e["parent"]) if e["parent"] is not None else None
        e["id"] = len(out)
        out.append(e)
    return out

SRC_EXT = (".tsx", ".ts", ".jsx", ".js")
INTERACTIVE_ROLES = {"link", "button", "tab", "menuitem", "option", "checkbox", "radio", "switch", "combobox",
                     "textbox", "menuitemradio", "menuitemcheckbox", "treeitem"}
TEXT_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "span", "div", "label", "legend", "th", "td", "li", "dt", "dd",
             "strong", "em", "b", "i", "small", "caption", "figcaption", "summary", "title", "section", "header",
             "footer", "main", "nav", "aside", "ul", "ol", "form", "article", "table", "tr", "thead", "tbody", "code",
             "pre", "fieldset", "dialog", "time", "mark", "abbr"}

_CODE_STOP = re.compile(r"['\"`]|/[*/]")

def _strip_comments(t: str) -> str:
    out = list(t)
    i, n = 0, len(t)
    while True:
        m = _CODE_STOP.search(t, i)
        if not m:
            break
        i, tok = m.start(), m.group(0)
        if tok in ("'", '"', "`"):
            k = i + 1
            while k < n and t[k] != tok and (tok == "`" or t[k] != "\n"):
                k += 2 if t[k] == "\\" else 1
            i = k + 1
        elif tok == "/*":
            j = t.find("*/", i + 2)
            if j < 0:
                i += 2
                continue
            out[i:j + 2] = " " * (j + 2 - i)
            i = j + 2
        elif i == 0 or out[i - 1].isspace() or out[i - 1] in ";{}(),":
            j = t.find("\n", i)
            j = n if j < 0 else j
            out[i:j] = " " * (j - i)
            i = j
        else:
            i += 2
    return "".join(out)

class Tag:
    __slots__ = ("start", "end", "name", "closing", "selfclose", "attrs")

    def __init__(self, start, end, name, closing, selfclose, attrs):
        self.start, self.end, self.name, self.closing, self.selfclose, self.attrs = start, end, name, closing, selfclose, attrs

def _scan_tags(t: str) -> list[Tag]:
    tags: list[Tag] = []
    i, n = 0, len(t)
    while True:
        i = t.find("<", i)
        if i < 0 or i + 1 >= n:
            break
        j = i - 1
        while j >= 0 and t[j] in " \t":
            j -= 1
        prevc = t[j] if j >= 0 else "\n"
        nxt = t[i + 1]
        if nxt != "/" and ((i > 0 and (t[i - 1].isalnum() or t[i - 1] in "_.])")) or nxt in " =<0123456789"):
            i += 1
            continue
        m = re.match(r"<(/?)([A-Za-z][\w.:-]*)?", t[i:])
        closing = m.group(1) == "/"
        name = m.group(2) or ""
        if not name and not (t[i + len(m.group(0))] == ">"):
            i += 1
            continue
        k = i + len(m.group(0))
        depth, quote = 0, None
        while k < n:
            c = t[k]
            if quote:
                if c == quote:
                    quote = None
            elif depth == 0 and c in "\"'":
                quote = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth = max(0, depth - 1)
            elif c == ">" and depth == 0:
                break
            elif c == "<" and depth == 0 and not closing and t[k + 1:k + 2].isalpha():
                k = -1
                break
            k += 1
        if k < 0 or k >= n:
            i += 1
            continue
        attrs = t[i + len(m.group(0)):k]
        selfclose = attrs.rstrip().endswith("/")
        tags.append(Tag(i, k + 1, name or "#frag", closing, selfclose, attrs))
        i = k + 1
    return tags

def _attr(attrs: str, key: str) -> str | None:
    m = re.search(r"(?<![\w-])" + re.escape(key) + r"\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|\{\s*(['\"`])(.*?)\3\s*\}|\{([^{}]*)\})", attrs)
    if not m:
        return None
    for g in (1, 2, 4):
        if m.group(g) is not None:
            return m.group(g)
    return "{" + (m.group(5) or "") + "}"

class SrcFile:
    def __init__(self, path: Path, rel: str, text: str):
        self.path, self.rel = path, rel
        self.raw = text
        self.text = _strip_comments(text)
        self.tags = _scan_tags(self.text)
        self._stacks: list[tuple[int, tuple]] | None = None

    def line(self, pos: int) -> int:
        return self.text.count("\n", 0, pos) + 1

    def stack_at(self, pos: int) -> list[Tag]:
        st: list[Tag] = []
        for tg in self.tags:
            if tg.start >= pos:
                break
            if tg.end > pos:
                break
            if tg.closing:
                for k in range(len(st) - 1, -1, -1):
                    if st[k].name == tg.name:
                        del st[k:]
                        break
            elif not tg.selfclose:
                st.append(tg)
        return st

    def tag_containing(self, pos: int) -> Tag | None:
        for tg in self.tags:
            if tg.start < pos < tg.end:
                return tg
            if tg.start > pos:
                break
        return None

class App:
    def __init__(self, project_dir: Path):
        self.root = Path(project_dir)
        self.src = self.root / "frontend" / "src"
        self.files: dict[str, SrcFile] = {}
        if self.src.is_dir():
            for p in sorted(self.src.rglob("*")):
                if p.suffix in SRC_EXT and p.is_file() and ".test." not in p.name and ".spec." not in p.name \
                        and "__tests__" not in p.parts:
                    rel = p.relative_to(self.src).as_posix()
                    self.files[rel] = SrcFile(p, rel, p.read_text(encoding="utf-8", errors="replace"))
        self.imports = {rel: self._imports(f) for rel, f in self.files.items()}
        self.comp_def = self._component_defs()
        self._role_cache: dict[str, str] = {}
        self.routes = self._routes()
        self.global_comps = self._global_comps()

    def _resolve(self, frm: str, spec: str) -> str | None:
        if not spec.startswith("."):
            return None
        base = (Path(frm).parent / spec).as_posix()
        parts: list[str] = []
        for seg in base.split("/"):
            if seg == "..":
                if parts:
                    parts.pop()
            elif seg and seg != ".":
                parts.append(seg)
        base = "/".join(parts)
        for cand in [base] + [base + e for e in SRC_EXT] + [base + "/index" + e for e in SRC_EXT]:
            if cand in self.files:
                return cand
        return None

    def _imports(self, f: SrcFile) -> dict[str, str]:
        out: dict[str, str] = {}
        for m in re.finditer(r"import\s+(?:type\s+)?([^;]*?)\s+from\s+['\"]([^'\"]+)['\"]", f.text):
            target = self._resolve(f.rel, m.group(2))
            if not target:
                continue
            clause = m.group(1)
            dm = re.match(r"\s*([A-Za-z_$][\w$]*)", clause)
            if dm and not clause.strip().startswith("{"):
                out[dm.group(1)] = target
            for nm in re.findall(r"\{([^}]*)\}", clause):
                for part in nm.split(","):
                    part = part.strip()
                    if not part:
                        continue
                    local = part.split(" as ")[-1].strip()
                    out[local] = target
            out.setdefault("__file__" + target, target)
        for m in re.finditer(r"import\(\s*['\"]([^'\"]+)['\"]\s*\)", f.text):
            target = self._resolve(f.rel, m.group(1))
            if target:
                out.setdefault("__file__" + target, target)
                lm = re.search(r"(?:const|let)\s+([A-Z]\w*)\s*=\s*(?:React\.)?lazy\(\s*\(\)\s*=>\s*import\(\s*['\"]"
                               + re.escape(m.group(1)), f.text)
                if lm:
                    out[lm.group(1)] = target
        return out

    def _component_defs(self) -> dict[tuple[str, str], tuple[int, int]]:
        defs: dict[tuple[str, str], tuple[int, int]] = {}
        for rel, f in self.files.items():
            starts = [(m.start(), m.group(1)) for m in re.finditer(
                r"^(?:export\s+)?(?:default\s+)?(?:function\s+([A-Z]\w*)|const\s+([A-Z]\w*)\s*[:=])", f.text, re.M)]
            starts = [(s, re.match(r"^(?:export\s+)?(?:default\s+)?(?:function\s+([A-Z]\w*)|const\s+([A-Z]\w*))",
                                   f.text[s:]).group(1) or re.match(r"^(?:export\s+)?(?:default\s+)?(?:function\s+([A-Z]\w*)|const\s+([A-Z]\w*))", f.text[s:]).group(2))
                      for s, _ in starts]
            for k, (s, name) in enumerate(starts):
                e = starts[k + 1][0] if k + 1 < len(starts) else len(f.text)
                defs[(rel, name)] = (s, e)
        return defs

    def _find_component(self, rel: str, name: str) -> tuple[str, str] | None:
        base = name.split(".")[0]
        if (rel, base) in self.comp_def:
            return (rel, base)
        target = self.imports.get(rel, {}).get(base)
        if target:
            if (target, base) in self.comp_def:
                return (target, base)
            m = re.search(r"export\s+default\s+(?:function\s+)?([A-Z]\w*)", self.files[target].text)
            if m and (target, m.group(1)) in self.comp_def:
                return (target, m.group(1))
        return None

    def component_role(self, rel: str, name: str, depth: int = 0) -> str:
        if name in ("Link", "NavLink"):
            return "link"
        if name in ("#frag", "Fragment", "React.Fragment", "Suspense", "React.Suspense"):
            return "wrapper"
        key = rel + "::" + name
        if key in self._role_cache:
            return self._role_cache[key]
        self._role_cache[key] = "unknown"
        loc = self._find_component(rel, name)
        if not loc or depth > 4:
            return "unknown"
        frel, cname = loc
        s, e = self.comp_def[(frel, cname)]
        f = self.files[frel]
        roles = set()
        for tg in f.tags:
            if tg.start < s or tg.start >= e or tg.closing:
                continue
            r = self.tag_role(frel, tg, depth + 1)
            if r in INTERACTIVE_ROLES or r == "unknown":
                roles.add(r)
        if not roles:
            res = "wrapper"
        elif len(roles) == 1:
            res = roles.pop()
        else:
            res = "unknown"
        self._role_cache[key] = res
        return res

    def ancestor_role(self, rel: str, tg: Tag, depth: int = 0) -> str:
        if tg.name[:1].isupper() and tg.name not in ("Link", "NavLink") and not _attr(tg.attrs, "role"):
            return self.children_role(rel, tg.name, depth)
        return self.tag_role(rel, tg, depth)

    def children_role(self, rel: str, name: str, depth: int = 0) -> str:
        if name in ("#frag", "Fragment", "React.Fragment", "Suspense", "React.Suspense", "Routes", "Route"):
            return "wrapper"
        key = "children::" + rel + "::" + name
        if key in self._role_cache:
            return self._role_cache[key]
        self._role_cache[key] = "unknown"
        loc = self._find_component(rel, name)
        if not loc or depth > 4:
            return "unknown"
        frel, cname = loc
        s, e = self.comp_def[(frel, cname)]
        f = self.files[frel]
        roles = set()
        for m in re.finditer(r"\{\s*(?:props\.)?children\s*\}", f.text[s:e]):
            pos = s + m.start()
            r = "wrapper"
            for anc in reversed(f.stack_at(pos)):
                if anc.start < s:
                    break
                ar = self.ancestor_role(frel, anc, depth + 1)
                if ar in ("text", "wrapper"):
                    continue
                r = ar
                break
            roles.add(r)
        if not roles:
            res = "unknown"
        elif roles == {"wrapper"}:
            res = "wrapper"
        elif len(roles - {"wrapper"}) == 1 and "wrapper" not in roles:
            res = roles.pop()
        else:
            res = "unknown"
        self._role_cache[key] = res
        return res

    def tag_role(self, rel: str, tg: Tag, depth: int = 0) -> str:
        r = _attr(tg.attrs, "role")
        if r and not r.startswith("{"):
            return r.strip()
        if r and r.startswith("{"):
            return "unknown"
        nm = tg.name
        if nm in ("Link", "NavLink"):
            return "link"
        if nm == "a":
            return "link" if re.search(r"\bhref\s*=", tg.attrs) else "text"
        if nm == "button":
            return "button"
        if nm == "input":
            ty = (_attr(tg.attrs, "type") or "text").lower()
            return "button" if ty in ("submit", "button", "reset") else ("checkbox" if ty == "checkbox" else "textbox")
        if nm == "select":
            return "combobox"
        if nm == "option":
            return "option"
        if nm[:1].isupper():
            return self.component_role(rel, nm, depth)
        if nm in TEXT_TAGS or nm[:1].islower():
            return "text"
        return "unknown"

    def _routes(self) -> list[dict]:
        out: list[dict] = []
        for rel, f in self.files.items():
            if "<Route" not in f.text:
                continue
            stack: list[tuple[str, tuple | None]] = []
            for tg in f.tags:
                if tg.name != "Route":
                    continue
                if tg.closing:
                    if stack:
                        stack.pop()
                    continue
                path = _attr(tg.attrs, "path")
                index = re.search(r"(?<![\w-])index\b", tg.attrs) is not None
                em = re.search(r"element\s*=\s*\{\s*<\s*([A-Z][\w.]*)", tg.attrs)
                comp = self._find_component(rel, em.group(1)) if em else None
                if comp is None and em:
                    comp = ("?", em.group(1))
                prefix = stack[-1][0] if stack else ""
                if path is None or path.startswith("{"):
                    full = prefix if index or path is None else None
                elif path.startswith("/"):
                    full = path
                else:
                    full = prefix.rstrip("/") + "/" + path
                layouts = [c for _, c in stack if c]
                if full is not None and (path is not None or index):
                    out.append({"path": full or "/", "comp": comp, "layouts": layouts, "file": rel})
                if not tg.selfclose:
                    stack.append((full if full is not None else prefix, comp))

        for r in out:
            f = self.files.get(r["file"])
            if f:
                glob = []
                for tg in f.tags:
                    if tg.closing or tg.name[:1].islower() or tg.name in ("Route", "Routes", "BrowserRouter", "Router",
                                                                           "#frag", "React.StrictMode", "StrictMode"):
                        continue
                    if re.search(r"element\s*=", tg.attrs):
                        continue
                    inside_route = any(t2.name == "Route" and t2.start < tg.start < t2.end for t2 in f.tags)
                    if not inside_route:
                        c = self._find_component(r["file"], tg.name)
                        if c:
                            glob.append(c)
                r["layouts"] = list(dict.fromkeys(r["layouts"] + glob))
        return out

    def _global_comps(self) -> list[tuple]:
        out = []
        for rel, f in self.files.items():
            if not re.search(r"<App\b", f.text):
                continue
            for tg in f.tags:
                if tg.closing or not tg.name[:1].isupper() or tg.name in ("App", "BrowserRouter", "Router", "StrictMode",
                                                                        "React.StrictMode", "HashRouter", "MemoryRouter"):
                    continue
                c = self._find_component(rel, tg.name)
                if c:
                    out.append(c)
        return out

    def match_route(self, to: str) -> dict | None:
        rs = self.match_routes(to)
        return rs[0] if len(rs) == 1 else (rs[0] if rs and not to.startswith("${") else None)

    def match_routes(self, to: str) -> list[dict]:
        to = to.split("?")[0].split("#")[0]
        m = re.match(r"^\$\{[^}]*\}(/.*)$", to)
        if m:
            tail = [x for x in re.sub(r"\$\{[^}]*\}", ":p", m.group(1)).split("/") if x]
            out = []
            for r in self.routes:
                ps = [x for x in r["path"].split("/") if x]
                if len(ps) > len(tail) and all(a.startswith(":") or b.startswith(":") or a == b
                                               for a, b in zip(ps[-len(tail):], tail)):
                    score = sum(1 for a, b in zip(ps[-len(tail):], tail) if a == b and not a.startswith(":"))
                    out.append((score, r))
            best = max((sc for sc, _ in out), default=0)
            return [r for sc, r in out if sc == best]
        one = self._match_route(to)
        return [one] if one else []

    def _match_route(self, to: str) -> dict | None:
        if not to.startswith("/"):
            return None
        segs = [s for s in re.sub(r"\$\{[^}]*\}", ":p", to).split("/") if s]
        best = None
        for r in self.routes:
            ps = [s for s in r["path"].split("/") if s]
            if ps and ps[-1] == "*":
                if len(segs) < len(ps) - 1:
                    continue
                ps2, sg2 = ps[:-1], segs[:len(ps) - 1]
            else:
                if len(ps) != len(segs):
                    continue
                ps2, sg2 = ps, segs
            ok, score = True, 0
            for a, b in zip(ps2, sg2):
                if a.startswith(":"):
                    continue
                if b.startswith(":") or b == a:
                    score += (1 if b == a else 0)
                    if b != a and not b.startswith(":"):
                        ok = False
                    continue
                ok = False
                break
            if ok and (best is None or score > best[0]):
                best = (score, r)
        return best[1] if best else None

    def closure(self, comps: list[tuple]) -> set[str]:
        files: set[str] = set()
        todo = [c[0] for c in comps if c and c[0] in self.files]
        while todo:
            x = todo.pop()
            if x in files:
                continue
            files.add(x)
            todo.extend(v for v in self.imports.get(x, {}).values() if v not in files)
        return files

    def route_closure(self, r: dict) -> set[str] | None:
        if not r["comp"] or r["comp"][0] not in self.files:
            return None
        return self.closure([r["comp"]] + list(r["layouts"]) + self.global_comps)

    def occurrences(self, name: str) -> list[dict]:
        occ: list[dict] = []
        rx = re.compile(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])")
        rxi = re.compile(re.escape(name), re.I)
        for rel, f in self.files.items():
            t = f.text
            for m in rxi.finditer(t):
                pos = m.start()
                exact_case = t[pos:m.end()] == name and bool(rx.match(t, pos))
                o = {"file": rel, "line": f.line(pos), "kind": None, "role": None, "to": None, "exact": exact_case}
                tg = f.tag_containing(pos)
                if tg is not None:
                    am = None
                    for am in re.finditer(r"([\w-]+)\s*=\s*(?:\"[^\"]*\"|'[^']*'|\{)", tg.attrs):
                        if tg.start + 1 + len(tg.name) + am.start() > pos:
                            break
                    attr_name = None
                    for am2 in re.finditer(r"([\w-]+)\s*=", tg.attrs):
                        if tg.start + 1 + len(tg.name) + am2.start() < pos:
                            attr_name = am2.group(1)
                    val = _attr(tg.attrs, attr_name) if attr_name else None
                    whole = val is not None and val.strip() == t[pos:m.end()]
                    if attr_name in ("aria-label", "aria-labelledby") and whole:
                        o["kind"] = "aria-label"
                        o["role"] = self.tag_role(rel, tg)
                        if o["role"] in ("text", "wrapper"):
                            o["role"] = self._ancestor_role(rel, f, tg.start) or "text"
                        o["to"] = _attr(tg.attrs, "to") or _attr(tg.attrs, "href")
                    elif tg.name[:1].isupper() and whole and attr_name not in ("className", "id", "to", "href",
                                                                               "placeholder", "title", "key"):
                        o["kind"] = "prop"
                        cr = self.tag_role(rel, tg)
                        o["role"] = cr if cr in INTERACTIVE_ROLES else "unknown"
                        o["to"] = _attr(tg.attrs, "to") or _attr(tg.attrs, "href")
                    else:
                        o["kind"] = "attr-other"
                        o["role"] = "none"
                    occ.append(o)
                    continue

                prev_end = max((x.end for x in f.tags if x.end <= pos), default=None)
                next_start = min((x.start for x in f.tags if x.start >= m.end()), default=None)
                between = t[prev_end:next_start] if prev_end is not None and next_start is not None else None
                if between is not None and not re.search(r"[;]|=>|\breturn\b|\bconst\b|\bfunction\b", between):
                    b2 = re.sub(r"\{\s*(['\"`])(.*?)\1\s*\}", r"\2", between)
                    b2 = re.sub(r"\{[^{}]*\}", " ", b2)
                    b2 = re.sub(r"\s+", " ", b2).strip()
                    o["kind"] = "jsx-text" if b2.lower() == name.lower() else "jsx-text-partial"
                    o["exact"] = o["exact"] and b2 == name
                    st = f.stack_at(pos)
                    role, to = "text", None
                    for anc in reversed(st):
                        r = self.ancestor_role(rel, anc)
                        if r in ("text", "wrapper"):
                            continue
                        role = r
                        to = _attr(anc.attrs, "to") or _attr(anc.attrs, "href")
                        break
                    o["role"], o["to"] = role, to
                    occ.append(o)
                    continue

                ql = t[pos - 1:pos] if pos else ""
                qr = t[m.end():m.end() + 1]
                if ql in "\"'`" and ql == qr and ql:
                    o["kind"] = "literal"
                    around = t[max(0, pos - 250):m.end() + 250]
                    link_ctx = re.search(r"\b(to|href|path)\s*:", around) or re.search(r"<(Link|NavLink|a)\b", t)
                    o["role"] = "unknown" if link_ctx else "literal-nonlink"
                    o["exact"] = exact_case

                    lb = t.rfind("{", 0, pos)
                    rb, depth = -1, 0
                    for k in range(lb, min(len(t), lb + 400)) if lb >= 0 else ():
                        if t[k] == "{":
                            depth += 1
                        elif t[k] == "}":
                            depth -= 1
                            if depth == 0:
                                rb = k
                                break
                    obj = t[lb:rb + 1] if lb >= 0 and rb >= 0 and "{" not in t[lb + 1:pos] else ""
                    km = re.search(r"(\w+)\s*:\s*$", t[lb:pos - 1]) if obj and "\n\n" not in obj else None
                    if km:

                        key = km.group(1)
                        roles = set()
                        for em in re.finditer(r"\{\s*\w+\??\." + key + r"\s*\}", t):
                            tg2 = f.tag_containing(em.start())
                            if tg2 is not None:
                                if re.search(r"aria-label\s*=\s*\{\s*\w+\??\." + key + r"\s*\}", tg2.attrs):
                                    r2 = self.tag_role(rel, tg2)
                                    roles.add(r2 if r2 not in ("text", "wrapper") else
                                              (self._ancestor_role(rel, f, tg2.start) or "text"))
                                continue
                            roles.add(self._ancestor_role(rel, f, em.start()) or "text")
                        if len(roles) == 1 and next(iter(roles)) in INTERACTIVE_ROLES:
                            r2 = roles.pop()
                            om = re.search(r"\b(to|href|path)\s*:\s*(`[^`]*`|'[^']*'|\"[^\"]*\")", obj)
                            o["role"], o["kind"] = r2, "literal-navitem"
                            o["to"] = om.group(2) if om and r2 == "link" else None
                    occ.append(o)
                    continue
                o["kind"] = "other"
                o["role"] = "none"
                occ.append(o)
        return occ

    def _ancestor_role(self, rel, f, pos):
        for anc in reversed(f.stack_at(pos)):
            r = self.ancestor_role(rel, anc)
            if r not in ("text", "wrapper"):
                return r
        return None

def _phrase_key(p: str) -> str:
    p = p.lower().replace("-", " ")
    p = re.sub(r"\b(the|a|an|its|repository's|current|list|repository|user's)\b", " ", p)
    p = re.sub(r"\b(page|pages|overview)\b", " ", p)
    return re.sub(r"\s+", " ", p).strip()

def _dedupe_occ(occ):
    seen, out = set(), []
    for o in occ:
        k = (o["file"], o["line"], o["kind"], o["role"])
        if k not in seen:
            seen.add(k)
            out.append(o)
    return out

class Verifier:
    def __init__(self, app: App):
        self.app = app
        self._occ: dict[str, list[dict]] = {}

    def occ(self, name):
        if name not in self._occ:
            self._occ[name] = _dedupe_occ(self.app.occurrences(name))
        return self._occ[name]

    def link_targets(self, name: str, within: set[str] | None = None) -> list[dict] | None:
        rs = []
        for o in self.occ(name):
            if o["role"] in ("link", "unknown") and o["exact"] and o["kind"] in ("jsx-text", "aria-label", "prop", "literal", "literal-navitem"):
                if within is not None and o["file"] not in within:
                    continue
                if o["role"] == "unknown":
                    return None
                to = o["to"] or ""
                to = to.strip().strip("{}").strip()
                if re.fullmatch(r"`[^`]*`|'[^']*'|\"[^\"]*\"", to):
                    to = to[1:-1]
                r = self.app.match_routes(to) if to and not re.search(r"[+()?]", to) else []
                if not r:
                    return None
                rs.extend(r)
        return rs

    def resolve_source(self, phrase: str | None, parent_target: list[dict] | None) -> tuple[set[str] | None, str]:
        app = self.app
        if parent_target is not None:
            if not parent_target:
                return None, "chain: parent link target not resolved"
            return self._union(parent_target), "target of parent link: " + ", ".join(sorted({r["path"] for r in parent_target}))
        if not phrase:
            return None, "no source stated"
        pl = phrase.lower()
        root = next((r for r in app.routes if r["path"] == "/"), None)
        if re.search(r"\b(home ?page|home)\b", pl) and "navigation" not in pl:
            return (app.route_closure(root), "route /") if root else (None, "no / route")
        if "navigation" in pl or pl in ("sidebar",) or pl.endswith(" bar"):

            if root:
                return app.route_closure(root), "route / (+layouts) for " + phrase
            return None, "no / route"
        if "menu" in pl:

            key = phrase.strip().replace("-", " ")
            cands = []
            if re.match(r"(?i)^[\w ]+ menu$", key):
                for nm in {key, key[0].upper() + key[1:]}:
                    for o in self.occ(nm):
                        if o["exact"] and o["kind"] in ("aria-label", "jsx-text") and o["role"] in INTERACTIVE_ROLES | {"unknown"}:
                            cands.append(o["file"])
            if cands:
                return app.closure([(c, None) for c in set(cands)]), "files with control " + repr(key)
            return None, "menu not located"

        key = _phrase_key(phrase)
        if key:
            for nm in dict.fromkeys([key, key.capitalize(), key.title()]):
                tg = self.link_targets(nm)
                if tg:
                    return self._union(tg), f"target of link {nm!r}: " + ", ".join(sorted({r['path'] for r in tg}))

            words = key.split()
            if len(words) == 1 and len(words[0]) >= 4 and re.search(r"\b(page|overview)\b", phrase.lower()):
                stem = words[0][:3]
                det = [r for r in app.routes
                       if re.fullmatch(r"/" + re.escape(stem) + r"[a-z-]*/:[\w]+", r["path"])]
                if len(det) == 1:
                    return self._union(det), f"detail route for {words[0]!r}: {det[0]['path']}"
        return None, "source phrase not resolved"

    def _union(self, routes: list[dict]) -> set[str] | None:
        files: set[str] = set()
        for r in routes:
            c = self.app.route_closure(r)
            if c is None:
                return None
            files |= c
        return files

    def verify(self, edges: list[dict]) -> list[dict]:
        results: list[dict] = []
        targets: dict[int, list[dict] | None] = {}
        for e in edges:
            res = dict(e)
            name, role = e["name"], e["role"]
            parent_t = None
            if e["parent"] is not None:
                parent_t = targets.get(e["parent"])
                if parent_t is None:
                    parent_t = []
            src_files, src_note = self.resolve_source(e["source"], parent_t)
            res["source_resolved"] = src_note
            if e.get("data"):
                v, have = self._verify_data_link(src_files, (name.split() or ["name"])[-1])
                res.update(verdict=v, have=have, confidence="high" if v != "ok" else None)
                targets[e["id"]] = None
                results.append(res)
                continue
            occ = self.occ(name)
            ctrl_kinds = ("jsx-text", "aria-label", "prop", "literal-navitem")

            def role_ok(o):
                return o["role"] in INTERACTIVE_ROLES if role is None else o["role"] == role

            good = [o for o in occ if o["kind"] in ctrl_kinds and o["exact"] and role_ok(o)]

            near = [o for o in occ if o["kind"] in ctrl_kinds + ("jsx-text-partial",) and role_ok(o) and o not in good]
            unsure = [o for o in occ if o["role"] == "unknown"]
            other_ctrl = [o for o in occ if o["kind"] in ctrl_kinds and o["exact"] and o["role"] in INTERACTIVE_ROLES
                          and not role_ok(o)]
            texts = [o for o in occ if o["kind"] in ctrl_kinds and o["role"] == "text"]
            any_interactive = [o for o in occ if o["role"] in INTERACTIVE_ROLES]
            literals = [o for o in occ if o["kind"] == "literal"]
            verdict, conf = "unsure", None
            if good:
                if src_files is None:
                    verdict = "ok"
                elif any(o["file"] in src_files for o in good + near + unsure):
                    verdict = "ok"
                else:
                    verdict, conf = "NOT_ON_SOURCE", "medium"
            elif not occ:
                verdict, conf = "ABSENT", "high"
            elif unsure or near:
                verdict = "unsure"
            elif role and other_ctrl:
                verdict, conf = "WRONG_ROLE", "high"
            elif role and texts and not any_interactive and not literals:
                verdict, conf = "NOT_A_CONTROL", "high"
            else:
                verdict = "unsure"
            res["verdict"], res["confidence"] = verdict, conf
            shown = [o for o in occ if o["kind"] in ctrl_kinds + ("jsx-text-partial", "literal")] or occ
            res["have"] = [f"{o['file']}:{o['line']} {o['kind']} role={o['role']}" + (f" to={o['to']}" if o['to'] else "")
                           + ("" if o["exact"] else " (case/partial)") for o in shown][:12] or ["name occurs nowhere in frontend/src"]

            if role in ("link", None):
                within = src_files if src_files else None
                t = self.link_targets(name, within) if within else None
                if not t:
                    t = self.link_targets(name)
                targets[e["id"]] = t
            else:
                targets[e["id"]] = []
            results.append(res)
        return results

    def _verify_data_link(self, src_files, field: str = "name"):
        if src_files is None:
            return "unsure", ["source not resolved"]
        roles: dict[str, list[str]] = {}
        rx = re.compile(r"\{\s*(?:[\w?]+\.)*" + re.escape(field) + r"\s*\}")
        for rel in sorted(src_files):
            f = self.app.files[rel]
            for m in rx.finditer(f.text):
                tg = f.tag_containing(m.start())
                if tg is not None:
                    if not re.search(r"aria-label\s*=\s*\{[^}]*" + re.escape(field), tg.attrs):
                        continue
                    r = self.app.tag_role(rel, tg)
                    if r in ("text", "wrapper"):
                        r = self.app._ancestor_role(rel, f, tg.start) or "text"
                else:
                    r = self.app._ancestor_role(rel, f, m.start()) or "text"
                roles.setdefault(r, []).append(f"{rel}:{f.line(m.start())} role={r}")
        if "link" in roles:
            return "ok", roles["link"][:5]
        if "unknown" in roles:
            return "unsure", roles["unknown"][:5]
        if "button" in roles:
            return "WRONG_ROLE", ["data-named buttons, no data-named link: " + ", ".join(roles["button"][:5])]
        return "unsure", ["no data-named control found"] + [x for v in roles.values() for x in v][:3]

REPORTED = ("WRONG_ROLE", "ABSENT", "NOT_A_CONTROL", "NOT_ON_SOURCE")

def check(requirement_text: str, project_dir) -> list[dict]:
    edges = extract_edges(requirement_text)
    app = App(Path(project_dir))
    if not app.files:
        return []
    return Verifier(app).verify(edges)

def missing_stated_edges(requirement_text: str, project_dir) -> list[dict]:
    out = []
    for r in check(requirement_text, project_dir):
        if r["verdict"] in REPORTED:
            out.append({
                "req": r["req"], "verdict": r["verdict"], "confidence": r["confidence"],
                "quote": r["quote"], "source": r["source"] or ("(chained) " + r["source_resolved"]),
                "source_resolved": r["source_resolved"],
                "expected": f"{r['role'] or 'control'} named {r['name']!r}",
                "have": r["have"],
            })
    return out
