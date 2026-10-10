from __future__ import annotations

import html
import os
import re
from pathlib import Path
from typing import Any

CHECK = "duplicate_names"
MAX_FINDINGS = 8
SKIP_DIRS = {"node_modules", "dist", "build", "test-e2e", "tests", "test", "__tests__", "coverage", ".git",
             "test-results", "playwright-report", "e2e-support"}
CONTROL_ROLES = {"combobox", "textbox", "checkbox", "searchbox", "spinbutton", "radio", "switch"}
FIX = ("Make the accessible name unique on the page at any one time: if the requirement states different names for "
       "these controls, use them; if it states this same name for both, render only one of them at a time (e.g. leave "
       "the other out while this form is open). Never rename a control to a name the requirement does not state.")

_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_NUM = re.compile(r"\d[\w.]*")
_TAG = re.compile(r"[A-Za-z_$][\w$.:-]*")
_ATTR = re.compile(r"[A-Za-z_$][\w$:.-]*")
_PUNCT3 = ("===", "!==", "...", "**=", "<<=", ">>=", "&&=", "||=", "??=")
_PUNCT2 = ("=>", "==", "!=", "<=", ">=", "&&", "||", "??", "?.", "++", "--", "+=", "-=", "*=", "/=", "%=", "&=",
           "|=", "^=", "<<", "**")
_JSX_AFTER_PUNCT = {"(", ",", "=", ":", "?", "&&", "||", "??", "{", "[", "!", "=>", ";", "}"}
_JSX_AFTER_WORD = {"return", "default", "case", "yield", "await"}
_REGEX_AFTER_WORD = {"return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "yield", "await"}

class ParseError(Exception):
    pass

class Tok:
    __slots__ = ("kind", "text", "pos", "extra")

    def __init__(self, kind: str, text: str, pos: int, extra: Any = None) -> None:
        self.kind, self.text, self.pos, self.extra = kind, text, pos, extra

    def __repr__(self) -> str:
        return f"Tok({self.kind},{self.text!r})"

class Node:
    __slots__ = ("tag", "attrs", "children", "pos", "spread")

    def __init__(self, tag: str, pos: int) -> None:
        self.tag, self.pos, self.attrs, self.children, self.spread = tag, pos, {}, [], False

class Reader:
    def __init__(self, src: str) -> None:
        self.s, self.n = src, len(src)

    def js(self, i: int, stop: str | None = None) -> tuple[list[Tok], int]:
        s, n = self.s, self.n
        toks: list[Tok] = []
        depth = 0
        while i < n:
            ch = s[i]
            if ch in " \t\r\n":
                i += 1
                continue
            if s.startswith("//", i):
                j = s.find("\n", i)
                i = n if j < 0 else j
                continue
            if s.startswith("/*", i):
                j = s.find("*/", i + 2)
                if j < 0:
                    raise ParseError("open comment")
                i = j + 2
                continue
            prev = toks[-1] if toks else None
            if ch in "'\"":
                j, val = self._string(i)
                toks.append(Tok("str", s[i:j], i, val))
                i = j
                continue
            if ch == "`":
                j, parts = self._template(i)
                toks.append(Tok("tpl", s[i:j], i, parts))
                i = j
                continue
            if ch == "<" and self._jsx_may_start(prev, i):
                save = i
                try:
                    node, j = self.jsx(i)
                except (ParseError, ValueError, IndexError):
                    node, j = None, save
                if node is not None:
                    toks.append(Tok("jsx", s[save:j], save, node))
                    i = j
                    continue
            if ch == "/" and self._regex_may_start(prev):
                j = self._regex(i)
                toks.append(Tok("re", s[i:j], i))
                i = j
                continue
            m = _IDENT.match(s, i)
            if m:
                toks.append(Tok("id", m.group(0), i))
                i = m.end()
                continue
            m = _NUM.match(s, i)
            if m:
                toks.append(Tok("num", m.group(0), i))
                i = m.end()
                continue
            if ch in "([{":
                depth += 1
                toks.append(Tok("p", ch, i))
                i += 1
                continue
            if ch in ")]}":
                if depth == 0:
                    if stop is not None and ch == stop:
                        return toks, i
                    raise ParseError(f"unbalanced {ch} at {i}")
                depth -= 1
                toks.append(Tok("p", ch, i))
                i += 1
                continue
            for p in _PUNCT3:
                if s.startswith(p, i):
                    break
            else:
                p = ""
                for q in _PUNCT2:
                    if s.startswith(q, i):
                        p = q
                        break
                if p == "?." and i + 2 < n and s[i + 2].isdigit():
                    p = ""
            if not p:
                p = ch
            toks.append(Tok("p", p, i))
            i += len(p)
        if stop is not None:
            raise ParseError("missing " + stop)
        return toks, i

    def _jsx_may_start(self, prev: Tok | None, i: int) -> bool:
        nxt = self.s[i + 1] if i + 1 < self.n else ""
        if not (nxt.isalpha() or nxt == ">" or nxt == "_"):
            return False
        if prev is None:
            return True
        if prev.kind == "p":
            return prev.text in _JSX_AFTER_PUNCT
        if prev.kind == "id":
            return prev.text in _JSX_AFTER_WORD
        return False

    @staticmethod
    def _regex_may_start(prev: Tok | None) -> bool:
        if prev is None:
            return True
        if prev.kind == "p":
            return prev.text not in (")", "]", "}")
        if prev.kind == "id":
            return prev.text in _REGEX_AFTER_WORD
        return False

    def _regex(self, i: int) -> int:
        s, n = self.s, self.n
        j, klass = i + 1, False
        while j < n:
            c = s[j]
            if c == "\\":
                j += 2
                continue
            if c == "\n":
                raise ParseError("regex runs past the line")
            if c == "[":
                klass = True
            elif c == "]":
                klass = False
            elif c == "/" and not klass:
                j += 1
                while j < n and (s[j].isalnum() or s[j] == "_"):
                    j += 1
                return j
            j += 1
        raise ParseError("open regex")

    def _string(self, i: int) -> tuple[int, str]:
        s, n, q = self.s, self.n, self.s[i]
        j, out = i + 1, []
        while j < n:
            c = s[j]
            if c == "\\":
                nxt = s[j + 1] if j + 1 < n else ""
                out.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
                j += 2
                continue
            if c == q:
                return j + 1, "".join(out)
            if c == "\n":
                raise ParseError("open string")
            out.append(c)
            j += 1
        raise ParseError("open string")

    def _template(self, i: int) -> tuple[int, list[Any]]:
        s, n = self.s, self.n
        j, parts, buf = i + 1, [], []
        while j < n:
            c = s[j]
            if c == "\\":
                buf.append(s[j + 1] if j + 1 < n else "")
                j += 2
                continue
            if c == "`":
                parts.append("".join(buf))
                return j + 1, parts
            if s.startswith("${", j):
                parts.append("".join(buf))
                buf = []
                toks, k = self.js(j + 2, "}")
                parts.append(toks)
                j = k + 1
                continue
            buf.append(c)
            j += 1
        raise ParseError("open template")

    def _ws(self, i: int) -> int:
        s, n = self.s, self.n
        while i < n:
            if s[i] in " \t\r\n":
                i += 1
            elif s.startswith("/*", i):
                j = s.find("*/", i + 2)
                if j < 0:
                    raise ParseError("open comment")
                i = j + 2
            elif s.startswith("//", i):
                j = s.find("\n", i)
                i = n if j < 0 else j
            else:
                break
        return i

    def jsx(self, i: int) -> tuple[Node, int]:
        s = self.s
        start = i
        i += 1
        if s[i] == ">":
            node = Node("", start)
            return node, self._children(node, i + 1)
        m = _TAG.match(s, i)
        if not m:
            raise ParseError("tag")
        node = Node(m.group(0), start)
        i = m.end()
        while True:
            i = self._ws(i)
            if s.startswith("/>", i):
                return node, i + 2
            if s[i] == ">":
                return node, self._children(node, i + 1)
            if s[i] == "{":
                toks, j = self.js(i + 1, "}")
                node.spread = True
                node.attrs.setdefault("...", ("spread", toks))
                i = j + 1
                continue
            m = _ATTR.match(s, i)
            if not m:
                raise ParseError("attribute")
            name = m.group(0)
            i = self._ws(m.end())
            if s[i] == "=":
                i = self._ws(i + 1)
                c = s[i]
                if c in "'\"":
                    j = s.index(c, i + 1)
                    node.attrs[name] = ("str", s[i + 1 : j])
                    i = j + 1
                elif c == "{":
                    toks, j = self.js(i + 1, "}")
                    node.attrs[name] = ("expr", (toks, s[i + 1 : j]))
                    i = j + 1
                elif c == "<":
                    sub, j = self.jsx(i)
                    node.attrs[name] = ("jsx", sub)
                    i = j
                else:
                    raise ParseError("attribute value")
            else:
                node.attrs[name] = ("bool", True)

    def _children(self, node: Node, i: int) -> int:
        s, n = self.s, self.n
        while i < n:
            if s.startswith("</", i):
                j = s.index(">", i)
                if s[i + 2 : j].strip() != node.tag:
                    raise ParseError("closing tag")
                return j + 1
            c = s[i]
            if c == "<":
                sub, i = self.jsx(i)
                node.children.append(("el", sub))
            elif c == "{":
                toks, j = self.js(i + 1, "}")
                node.children.append(("expr", toks, s[i + 1 : j]))
                i = j + 1
            else:
                j = i
                while j < n and s[j] not in "<{":
                    j += 1
                node.children.append(("text", s[i:j]))
                i = j
        raise ParseError("open element " + node.tag)

def _matches(toks: list[Tok]) -> dict[int, int]:
    pairs, stack = {}, []
    for k, t in enumerate(toks):
        if t.kind != "p":
            continue
        if t.text in "([{" and len(t.text) == 1:
            stack.append(k)
        elif t.text in ")]}" and len(t.text) == 1 and stack:
            pairs[stack.pop()] = k
    return pairs

def _top_indices(toks: list[Tok], texts: set[str]) -> list[int]:
    out, depth = [], 0
    for k, t in enumerate(toks):
        if t.kind == "p":
            if t.text in ("(", "[", "{"):
                depth += 1
            elif t.text in (")", "]", "}"):
                depth -= 1
            elif depth == 0 and t.text in texts:
                out.append(k)
    return out

def _strip_parens(toks: list[Tok]) -> list[Tok]:
    while len(toks) >= 2 and toks[0].kind == "p" and toks[0].text == "(" and _matches(toks).get(0) == len(toks) - 1:
        toks = toks[1:-1]
    return toks

def _text(toks: list[Tok]) -> str:
    return "".join(t.text for t in toks)

def _cond(toks: list[Tok]) -> tuple[str, bool]:
    toks = _strip_parens(toks)
    polarity = True
    while toks and toks[0].kind == "p" and toks[0].text == "!":
        polarity = not polarity
        toks = _strip_parens(toks[1:])
    return re.sub(r"\s+", "", _text(toks)), polarity

def _literal(value: Any) -> str | None:
    kind, val = value
    if kind == "str":
        return val
    if kind == "expr":
        toks = _strip_parens(val[0])
        if len(toks) == 1 and toks[0].kind == "str":
            return toks[0].extra
        if len(toks) == 1 and toks[0].kind == "tpl" and len(toks[0].extra) == 1:
            return toks[0].extra[0]
    return None

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()

def _all_nodes(toks: list[Tok]):
    for t in toks:
        if t.kind == "jsx":
            yield from _walk(t.extra)
        elif t.kind == "tpl":
            for part in t.extra:
                if isinstance(part, list):
                    yield from _all_nodes(part)

def _walk(node: Node):
    yield node
    for value in node.attrs.values():
        if value[0] == "jsx":
            yield from _walk(value[1])
        elif value[0] == "expr":
            yield from _all_nodes(value[1][0])
        elif value[0] == "spread":
            yield from _all_nodes(value[1])
    for child in node.children:
        if child[0] == "el":
            yield from _walk(child[1])
        elif child[0] == "expr":
            yield from _all_nodes(child[1])

def _role(node: Node) -> str | None:
    tag = node.tag
    if "role" in node.attrs:
        role = _literal(node.attrs["role"])
        return role if role in CONTROL_ROLES else None
    if tag == "input":
        if "type" in node.attrs:
            kind = _literal(node.attrs["type"])
            if kind is None:
                return None
            kind = kind.lower()
        else:
            kind = "text"
        if kind in ("hidden", "submit", "button", "reset", "image", "file", "color", "range"):
            return None
        return {"checkbox": "checkbox", "radio": "radio", "number": "spinbutton", "search": "searchbox",
                "password": "password"}.get(kind, "textbox")
    if tag == "select":
        if "multiple" in node.attrs:
            return "listbox"
        size = _literal(node.attrs["size"]) if "size" in node.attrs else None
        if "size" in node.attrs and (size is None or size.strip() not in ("", "0", "1")):
            return "listbox" if size is not None else None
        return "combobox"
    if tag == "textarea":
        return "textbox"
    return None

def _is_control(node: Node) -> bool:
    return node.tag[:1].islower() and (node.tag in ("input", "select", "textarea") or "role" in node.attrs) \
        and _role(node) is not None

def _label_text(node: Node, exclude: Node | None) -> str | None:
    out: list[str] = []
    for child in node.children:
        if child[0] == "text":
            out.append(child[1])
        elif child[0] == "expr":
            toks = _strip_parens(child[1])
            if not toks:
                continue
            if len(toks) == 1 and toks[0].kind == "str":
                out.append(toks[0].extra)
            elif len(toks) == 1 and toks[0].kind == "tpl" and len(toks[0].extra) == 1:
                out.append(toks[0].extra[0])
            else:
                return None
        else:
            sub = child[1]
            if sub is exclude:
                continue
            if not sub.tag[:1].islower() or sub.spread or "aria-hidden" in sub.attrs or "aria-label" in sub.attrs:
                return None
            if sub.tag in ("input", "select", "textarea"):
                return None
            inner = _label_text(sub, exclude)
            if inner is None:
                return None
            out.append(" " + inner + " " if sub.tag in ("div", "p", "br") else inner)
    return _norm("".join(out))

def _controls_in(node: Node) -> list[Node]:
    found = []
    for child in node.children:
        if child[0] == "el":
            sub = child[1]
            if _is_control(sub):
                found.append(sub)
            found.extend(_controls_in(sub))
        elif child[0] == "expr":
            for sub in _all_nodes(child[1]):
                if _is_control(sub):
                    found.append(sub)
    return found

class FileInfo:
    def __init__(self, path: Path, rel: str, src: str) -> None:
        self.path, self.rel, self.src = path, rel, src
        self.toks, _ = Reader(src).js(0)
        self.lines = [0]
        for k, c in enumerate(src):
            if c == "\n":
                self.lines.append(k + 1)
        nodes = list(_all_nodes(self.toks))

        self.for_labels: dict[str, set] = {}
        self.wrap: dict[int, str | None] = {}
        for node in nodes:
            if node.tag != "label":
                continue
            target = None
            if "htmlFor" in node.attrs or "for" in node.attrs:
                target = _literal(node.attrs.get("htmlFor") or node.attrs.get("for"))
                if target is None:
                    continue
                self.for_labels.setdefault(target, set()).add(_label_text(node, None) if not _controls_in(node) else None)
            inner = _controls_in(node)
            if len(inner) == 1:
                ctl = inner[0]
                ctl_id = _literal(ctl.attrs["id"]) if "id" in ctl.attrs else None
                if target is not None and target != ctl_id:
                    continue
                direct = any(c[0] == "el" and c[1] is ctl for c in node.children) or _nested_static(node, ctl)
                self.wrap[id(ctl)] = _label_text(node, ctl) if direct else None
            elif len(inner) > 1:
                for ctl in inner:
                    self.wrap[id(ctl)] = None
        self.components = _components(self.toks)
        self.imports = _imports(self.toks)
        self.default = _default_export(self.toks)

    def line(self, pos: int) -> int:
        lo, hi = 0, len(self.lines) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.lines[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    def name_of(self, node: Node) -> str | None:
        attrs = node.attrs
        if node.spread or "aria-labelledby" in attrs:
            return None
        if "aria-label" in attrs:
            value = _literal(attrs["aria-label"])
            return _norm(value) or None if value is not None else None
        names: set = set()
        ctl_id = _literal(attrs["id"]) if "id" in attrs else None
        if ctl_id is not None and ctl_id in self.for_labels:
            names |= self.for_labels[ctl_id]
        if id(node) in self.wrap:
            names.add(self.wrap[id(node)])
        if len(names) != 1:
            return None
        name = next(iter(names))
        return name or None

def _nested_static(label: Node, ctl: Node) -> bool:
    for child in label.children:
        if child[0] == "el":
            sub = child[1]
            if sub is ctl:
                return True
            if sub.tag[:1].islower() and _nested_static(sub, ctl):
                return True
    return False

def _components(toks: list[Tok]) -> dict[str, list[list[Tok]]]:
    found: dict[str, list[list[Tok]]] = {}
    pairs = _matches(toks)
    depth, k, n = 0, 0, len(toks)
    while k < n:
        t = toks[k]
        if t.kind == "p" and t.text in ("(", "[", "{"):
            depth += 1
        elif t.kind == "p" and t.text in (")", "]", "}"):
            depth -= 1
        elif depth == 0 and t.kind == "id" and t.text == "function" and k + 1 < n and toks[k + 1].kind == "id" \
                and toks[k + 1].text[:1].isupper():
            name = toks[k + 1].text
            j = k + 2
            while j < n and not (toks[j].kind == "p" and toks[j].text == "("):
                j += 1
            if j < n and j in pairs:
                j = pairs[j] + 1
                while j < n and not (toks[j].kind == "p" and toks[j].text == "{"):
                    j += 1
                if j < n and j in pairs:
                    found.setdefault(name, []).extend(_returns(toks[j + 1 : pairs[j]]))
                    k = pairs[j] + 1
                    continue
        elif depth == 0 and t.kind == "id" and t.text in ("const", "let", "var") and k + 1 < n \
                and toks[k + 1].kind == "id" and toks[k + 1].text[:1].isupper():
            name = toks[k + 1].text
            j, d = k + 2, 0
            arrow = None
            while j < n:
                u = toks[j]
                if u.kind == "p" and u.text in ("(", "[", "{"):
                    d += 1
                elif u.kind == "p" and u.text in (")", "]", "}"):
                    d -= 1
                    if d < 0:
                        break
                elif d == 0 and u.kind == "p" and u.text == ";":
                    break
                elif d == 0 and u.kind == "id" and u.text in ("const", "let", "var", "function", "export", "import"):
                    break
                elif d == 0 and u.kind == "p" and u.text == "=>":
                    arrow = j
                    break
                j += 1
            if arrow is not None and arrow + 1 < n:
                body = toks[arrow + 1]
                if body.kind == "p" and body.text == "{" and (arrow + 1) in pairs:
                    end = pairs[arrow + 1]
                    found.setdefault(name, []).extend(_returns(toks[arrow + 2 : end]))
                    k = end + 1
                    continue
                j, d = arrow + 1, 0
                while j < n:
                    u = toks[j]
                    if u.kind == "p" and u.text in ("(", "[", "{"):
                        d += 1
                    elif u.kind == "p" and u.text in (")", "]", "}"):
                        d -= 1
                        if d < 0:
                            break
                    elif d == 0 and (u.kind == "p" and u.text == ";" or u.kind == "id" and u.text in (
                            "const", "let", "var", "function", "export", "import")):
                        break
                    j += 1
                expr = toks[arrow + 1 : j]
                if any(x.kind == "jsx" for x in expr):
                    found.setdefault(name, []).append(expr)
                k = j
                continue
        k += 1
    return found

def _returns(body: list[Tok]) -> list[list[Tok]]:
    pairs = _matches(body)
    excluded: list[tuple[int, int]] = []
    for k, t in enumerate(body):
        if t.kind == "p" and t.text == "=>" and k + 1 < len(body) and body[k + 1].kind == "p" and body[k + 1].text == "{":
            if k + 1 in pairs:
                excluded.append((k + 1, pairs[k + 1]))
        elif t.kind == "id" and t.text == "function":
            j = k + 1
            while j < len(body) and not (body[j].kind == "p" and body[j].text == "{"):
                j += 1
            if j in pairs:
                excluded.append((j, pairs[j]))
    out = []
    for k, t in enumerate(body):
        if t.kind != "id" or t.text != "return" or any(a < k < b for a, b in excluded):
            continue
        j, d = k + 1, 0
        while j < len(body):
            u = body[j]
            if u.kind == "p" and u.text in ("(", "[", "{"):
                d += 1
            elif u.kind == "p" and u.text in (")", "]", "}"):
                d -= 1
                if d < 0:
                    break
            elif d == 0 and u.kind == "p" and u.text == ";":
                break
            elif d == 0 and u.pos > body[k].pos and u.kind == "id" and u.text in ("if", "return", "const", "let") \
                    and j > k + 1:
                break
            j += 1
        expr = body[k + 1 : j]
        if any(x.kind == "jsx" for x in expr):
            out.append(expr)
    return out

def _imports(toks: list[Tok]) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    n = len(toks)
    for k, t in enumerate(toks):
        if t.kind != "id" or t.text != "import":
            continue
        j = k + 1
        names: list[tuple[str, str]] = []
        while j < n and not (toks[j].kind == "id" and toks[j].text == "from"):
            u = toks[j]
            if u.kind == "p" and u.text == ";":
                break
            if u.kind == "id" and u.text != "type":
                if j + 2 < n and toks[j + 1].kind == "id" and toks[j + 1].text == "as":
                    names.append((toks[j + 2].text, u.text))
                    j += 3
                    continue
                inside = any(x.kind == "p" and x.text == "{" for x in toks[k:j]) and not any(
                    x.kind == "p" and x.text == "}" for x in toks[k:j])
                names.append((u.text, u.text if inside else "default"))
            j += 1
        if j + 1 < n and toks[j].kind == "id" and toks[j].text == "from" and toks[j + 1].kind == "str":
            spec = toks[j + 1].extra
            if spec.startswith("."):
                for local, imported in names:
                    out[local] = (spec, imported)
    return out

def _default_export(toks: list[Tok]) -> str | None:
    for k in range(len(toks) - 2):
        if toks[k].kind == "id" and toks[k].text == "export" and toks[k + 1].kind == "id" and toks[k + 1].text == "default":
            nxt = toks[k + 2]
            if nxt.kind == "id" and nxt.text == "function" and k + 3 < len(toks) and toks[k + 3].kind == "id":
                return toks[k + 3].text
            if nxt.kind == "id":
                return nxt.text
    return None

class Ctx:
    __slots__ = ("chain", "map", "mapscope", "file", "depth", "inst", "slot", "via", "premap")

    def __init__(self, file: FileInfo, chain=(), map_=False, mapscope=None, depth=0, inst="r", slot=None, via=(),
                 premap=()):
        self.chain, self.map, self.mapscope, self.file = chain, map_, mapscope, file
        self.depth, self.inst, self.slot, self.via, self.premap = depth, inst, slot, via, premap

    def but(self, **kw) -> "Ctx":
        c = Ctx(self.file, self.chain, self.map, self.mapscope, self.depth, self.inst, self.slot, self.via, self.premap)
        for key, value in kw.items():
            setattr(c, key, value)
        return c

    def scope(self) -> str:
        return self.inst + ("|" + self.mapscope if self.mapscope else "")

    def plus(self, conds: list[tuple[str, bool]]) -> "Ctx":
        return self.but(chain=self.chain + tuple((self.scope() + ":" + c, p) for c, p in conds))

def _hidden(node: Node) -> bool:
    attrs = node.attrs
    if node.tag in ("details", "dialog") or "hidden" in attrs:
        return True
    hidden_aria = _literal(attrs["aria-hidden"]) if "aria-hidden" in attrs else None
    if "aria-hidden" in attrs and (hidden_aria is None or hidden_aria != "false"):
        if attrs["aria-hidden"][0] == "bool" or hidden_aria in ("true", None):
            return True
    for key in ("className", "class"):
        if key not in attrs:
            continue
        kind, val = attrs[key]
        raw = val if kind == "str" else (val[1] if kind == "expr" else "")
        if kind == "str":
            if any(tok == "hidden" or tok.endswith(":hidden") or tok == "invisible" or tok.endswith(":invisible")
                   for tok in raw.split()):
                return True
        elif re.search(r"hidden|invisible", raw):
            return True
    if "style" in attrs:
        kind, val = attrs["style"]
        raw = val[1] if kind == "expr" else str(val)
        if re.search(r"display\s*:\s*['\"]?none|visibility\s*:\s*['\"]?hidden", raw):
            return True
    return False

class Project:
    def __init__(self, root: Path, log) -> None:
        self.root, self.log = root, log
        self.files: dict[Path, FileInfo] = {}

    def load(self, path: Path) -> FileInfo | None:
        path = path.resolve()
        if path in self.files:
            return self.files[path]
        info = None
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            rel = os.path.relpath(path, self.root).replace("\\", "/")
            info = FileInfo(path, rel, src)
        except Exception as exc:
            self.log(f"[audit] {CHECK}: skipped {path.name}: {type(exc).__name__}: {exc}")
        self.files[path] = info
        return info

    def resolve(self, file: FileInfo, tag: str) -> tuple[FileInfo, list[list[Tok]]] | None:
        if "." in tag:
            return None
        if tag in file.components:
            return file, file.components[tag]
        if tag not in file.imports:
            return None
        spec, imported = file.imports[tag]
        base = (file.path.parent / spec)
        for cand in (Path(str(base) + ".tsx"), base / "index.tsx", Path(str(base) + ".jsx")):
            if cand.is_file():
                other = self.load(cand)
                if other is None:
                    return None
                name = other.default if imported == "default" else imported
                if name and name in other.components:
                    return other, other.components[name]
                return None
        return None

class Walker:
    def __init__(self, project: Project) -> None:
        self.project = project
        self.controls: list[dict[str, Any]] = []
        self.counter = 0

    def expr(self, toks: list[Tok], ctx: Ctx) -> None:
        for node, sub in self.atoms(toks, ctx):
            self.visit(node, sub)

    def atoms(self, toks: list[Tok], ctx: Ctx):
        toks = _strip_parens(toks)
        if not toks:
            return
        if len(toks) == 1:
            if toks[0].kind == "jsx":
                yield toks[0].extra, ctx
            return
        questions = _top_indices(toks, {"?"})
        if questions:
            q = questions[0]
            count, colon = 0, None
            depth = 0
            for k in range(q + 1, len(toks)):
                t = toks[k]
                if t.kind != "p":
                    continue
                if t.text in ("(", "[", "{"):
                    depth += 1
                elif t.text in (")", "]", "}"):
                    depth -= 1
                elif depth == 0 and t.text == "?":
                    count += 1
                elif depth == 0 and t.text == ":":
                    if count == 0:
                        colon = k
                        break
                    count -= 1
            if colon is None or any(t.kind == "jsx" for t in toks[:q]):
                return
            cond = _cond(toks[:q])
            yield from self.atoms(toks[q + 1 : colon], ctx.plus([cond]))
            yield from self.atoms(toks[colon + 1 :], ctx.plus([(cond[0], not cond[1])]))
            return
        if _top_indices(toks, {"||", "??", ",", "=", "=>"}):
            return
        ands = _top_indices(toks, {"&&"})
        if ands:
            parts, last = [], 0
            for k in ands:
                parts.append(toks[last:k])
                last = k + 1
            result = toks[last:]
            if any(t.kind == "jsx" for part in parts for t in part):
                return
            yield from self.atoms(result, ctx.plus([_cond(p) for p in parts]))
            return

        pairs = _matches(toks)
        for k in range(1, len(toks) - 1):
            t = toks[k]
            if t.kind == "id" and t.text == "map" and toks[k - 1].kind == "p" and toks[k - 1].text in (".", "?.") \
                    and toks[k + 1].kind == "p" and toks[k + 1].text == "(" and pairs.get(k + 1) == len(toks) - 1:
                if any(_depth_at(toks, k) != 0 for _ in [0]):
                    return
                args = toks[k + 2 : len(toks) - 1]
                arrows = _top_indices(args, {"=>"})
                if not arrows:
                    return
                body = args[arrows[0] + 1 :]
                inner = ctx.but(map=True, mapscope=f"m{t.pos}", premap=ctx.premap if ctx.map else ctx.chain)
                if body and body[0].kind == "p" and body[0].text == "{" and _matches(body).get(0) == len(body) - 1:
                    rets = _returns(body[1:-1])
                    if len(rets) == 1:
                        yield from self.atoms(rets[0], inner)
                    return
                yield from self.atoms(body, inner)
                return
        return

    def visit(self, node: Node, ctx: Ctx) -> None:
        tag = node.tag
        if tag and not tag[:1].islower():
            self.component(node, ctx)
            return
        if tag and _hidden(node):
            return
        if tag and _is_control(node):
            name = ctx.file.name_of(node)
            if name:
                self.controls.append({"role": _role(node), "name": name, "file": ctx.file.rel,
                                      "line": ctx.file.line(node.pos), "node": id(node), "inst": ctx.inst,
                                      "chain": set(ctx.chain), "map": ctx.map, "mapscope": ctx.mapscope,
                                      "mapconds": ctx.map and set(ctx.chain) != set(ctx.premap),
                                      "via": ctx.via})
        self.children(node.children, ctx)

    def children(self, children: list, ctx: Ctx) -> None:
        for child in children:
            if child[0] == "el":
                self.visit(child[1], ctx)
            elif child[0] == "expr":
                toks = _strip_parens(child[1])
                if ctx.slot is not None and _text(toks) in ("children", "props.children"):
                    slot_children, slot_ctx = ctx.slot

                    self.children(slot_children, slot_ctx.but(chain=ctx.chain, map=ctx.map or slot_ctx.map,
                                                              depth=1, slot=None))
                    continue
                self.expr(child[1], ctx)

    def component(self, node: Node, ctx: Ctx) -> None:
        if node.tag in ("Fragment", "React.Fragment"):
            self.children(node.children, ctx)
            return
        if ctx.depth > 0:
            return
        found = self.project.resolve(ctx.file, node.tag)
        if not found:
            return
        other, roots = found
        if len(roots) != 1:
            return
        self.counter += 1
        inst = f"i{self.counter}"
        sub = Ctx(other, ctx.chain, ctx.map, None, 1, inst, (node.children, ctx),
                  ctx.via + (f"<{node.tag}> at {ctx.file.rel}:{ctx.file.line(node.pos)}",), ctx.premap)
        self.expr(roots[0], sub)

def _depth_at(toks: list[Tok], k: int) -> int:
    depth = 0
    for t in toks[:k]:
        if t.kind == "p" and t.text in ("(", "[", "{"):
            depth += 1
        elif t.kind == "p" and t.text in (")", "]", "}"):
            depth -= 1
    return depth

def _coexist(a: dict, b: dict) -> bool:
    return a["chain"] <= b["chain"] or b["chain"] <= a["chain"]

def _where(c: dict) -> str:
    text = f"{c['file']}:{c['line']}"
    notes = []
    if c["via"]:
        notes.append("rendered via " + ", ".join(c["via"]))
    if c["map"]:
        notes.append("once per item of a .map list")
    return text + (f" ({'; '.join(notes)})" if notes else "")

def _source_files(project_dir: Path) -> list[Path]:
    root = project_dir / "frontend" / "src"
    out: list[Path] = []
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(".tsx") and ".test." not in name and ".spec." not in name:
                out.append(Path(dirpath) / name)
    return out

def duplicate_name_source_findings(project_dir: Path, log=print, report_map_only: bool = False, owner_of=None) -> list[dict]:
    try:
        return _owned(_findings(Path(project_dir), log, report_map_only), owner_of)
    except Exception as exc:
        try:
            log(f"[audit] {CHECK} crashed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return []

def _owned(findings: list[dict], owner_of) -> list[dict]:
    for finding in findings:
        name = finding.pop("_name", None)
        if owner_of is None or not name:
            continue
        try:
            owners = owner_of(name)
        except Exception:
            owners = []
        if owners:
            from .finding_owner import tag

            tag(finding, owners)
    return findings

def _findings(project_dir: Path, log, report_map_only: bool) -> list[dict]:
    project = Project(project_dir, log)
    findings: list[dict] = []
    seen: list[frozenset] = []
    for path in _source_files(project_dir):
        info = project.load(path)
        if info is None:
            continue
        for comp, roots in info.components.items():
            for root in roots:
                walker = Walker(project)
                walker.expr(root, Ctx(info))
                groups: dict[tuple[str, str], list[dict]] = {}
                for c in walker.controls:
                    groups.setdefault((c["role"], c["name"]), []).append(c)
                for (role, name), items in groups.items():
                    items.sort(key=lambda c: (c["map"], bool(c["via"]), c["file"], c["line"]))
                    pair = None
                    for x in range(len(items)):
                        for y in range(x + 1, len(items)):
                            a, b = items[x], items[y]
                            if a["node"] == b["node"] and a["inst"] == b["inst"]:
                                continue
                            if _coexist(a, b):
                                pair = (a, b)
                                break
                        if pair:
                            break
                    if pair:
                        a, b = pair
                        key = frozenset({(a["file"], a["line"], a["inst"] if a["node"] == b["node"] else ""),
                                         (b["file"], b["line"], b["inst"] if a["node"] == b["node"] else "")})
                        if a["node"] != b["node"]:
                            key = frozenset({(a["file"], a["line"]), (b["file"], b["line"])})
                        if key in seen:
                            continue
                        seen.append(key)
                        role_text = "password input" if role == "password" else role
                        findings.append({
                            "where": f"{CHECK}: {a['file']}:{a['line']}",
                            "requirement": name,
                            "problem": (f"Page component {comp} ({info.rel}) renders two {role_text} controls with the "
                                        f"same accessible name \"{name}\" at the same time: {_where(a)} and {_where(b)}. "
                                        f"A lookup by role and exact name finds more than one element, and a user "
                                        f"cannot tell them apart."),
                            "fix": FIX,
                            "_name": name,
                        })
                        continue
                    if report_map_only:
                        for c in items:
                            if not c["map"] or c["mapconds"]:
                                continue
                            key = frozenset({(c["file"], c["line"], "map")})
                            if key in seen:
                                continue
                            seen.append(key)
                            role_text = "password input" if role == "password" else role
                            findings.append({
                                "where": f"{CHECK}: {c['file']}:{c['line']}",
                                "requirement": name,
                                "problem": (f"Page component {comp} ({info.rel}) renders the {role_text} at "
                                            f"{_where(c)} with the same literal accessible name \"{name}\" in every "
                                            f"item, so several controls on the page share that name."),
                                "fix": FIX,
                                "_name": name,
                            })
                            break
    if len(findings) > MAX_FINDINGS:
        log(f"[audit] {CHECK}: {len(findings)} findings, reporting {MAX_FINDINGS}")
    return findings[:MAX_FINDINGS]

if __name__ == "__main__":
    import json
    import sys
    result = duplicate_name_source_findings(Path(sys.argv[1]), report_map_only="--map" in sys.argv)
    print(json.dumps(result, indent=2, ensure_ascii=False))
