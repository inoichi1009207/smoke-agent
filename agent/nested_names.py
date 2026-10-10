from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MAX_FINDINGS = 8
CHECK = "nested_controls"

OUTER_ROLES = {"columnheader", "rowheader", "gridcell", "tab", "link", "button", "menuitem"}
INNER_ROLES = {"button", "link", "checkbox", "textbox", "combobox", "switch", "menuitem"}
VALUE_ROLES = {"textbox", "searchbox", "spinbutton", "slider"}

_LINE = re.compile(r"^(?P<indent>\s*)-\s+(?P<body>.*?)\s*$")
_BODY = re.compile(
    r'^(?P<role>[a-z]+)(?:\s+"(?P<name>(?:[^"\\]|\\.)*)")?(?P<attrs>(?:\s+\[[^\]]*\])*)\s*(?::(?:\s+(?P<value>.*))?)?$'
)

def _unquote_value(value: str | None) -> str:
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value

def _split_quoted_key(body: str) -> str:
    if not body.startswith("'"):
        return body
    i, out = 1, []
    while i < len(body):
        if body[i] == "'":
            if body.startswith("''", i):
                out.append("'")
                i += 2
                continue
            return "".join(out) + body[i + 1:]
        out.append(body[i])
        i += 1
    return body

def parse_aria(aria: str) -> list[dict[str, Any]]:
    root: dict[str, Any] = {"role": "#root", "name": None, "value": "", "attrs": "", "children": [], "indent": -1}
    stack = [root]
    for line in str(aria or "").splitlines():
        match = _LINE.match(line)
        if not match:
            continue
        body = _split_quoted_key(match.group("body"))
        if body.startswith("/"):
            continue
        node_match = _BODY.match(body)
        if not node_match:
            continue
        indent = len(match.group("indent"))
        name = node_match.group("name")
        node = {
            "role": node_match.group("role"),
            "name": None if name is None else name.replace('\\"', '"').replace("\\\\", "\\"),
            "value": _unquote_value(node_match.group("value")),
            "attrs": node_match.group("attrs") or "",
            "children": [],
            "indent": indent,
        }
        while len(stack) > 1 and stack[-1]["indent"] >= indent:
            stack.pop()
        stack[-1]["children"].append(node)
        stack.append(node)
    return root["children"]

def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")

def _contribution(node: dict[str, Any]) -> str:
    role = node["role"]
    if role == "text":
        return node["value"]
    if role in VALUE_ROLES:
        return node["value"]
    if role == "combobox":
        for child in node["children"]:
            if child["role"] == "option" and "[selected" in child["attrs"]:
                return child["name"] or child["value"]
        return node["value"]
    if node["name"]:
        return node["name"]
    if node["value"]:
        return node["value"]
    return " ".join(_contribution(child) for child in node["children"])

def _inner_controls(node: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    for child in node["children"]:
        if child["role"] in INNER_ROLES:
            found.append(child)
        else:
            found.extend(_inner_controls(child))
    return found

def _aria_own_label(node: dict[str, Any], skip: list[dict[str, Any]]) -> str:
    parts = []
    for child in node["children"]:
        if any(child is s for s in skip):
            continue
        if child["role"] in INNER_ROLES:
            continue
        if child["name"] or child["value"] or child["role"] == "text" or child["role"] in VALUE_ROLES:
            parts.append(_contribution(child))
        else:
            parts.append(_aria_own_label(child, skip))
    return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()

def _walk(nodes: list[dict[str, Any]]):
    for node in nodes:
        yield node
        yield from _walk(node["children"])

def _page_key(page: dict[str, Any]) -> str:
    route = page.get("route")
    if route:
        return str(route)
    url = str(page.get("url") or page.get("path") or "")
    return re.sub(r"^[a-z]+://[^/]+", "", url, flags=re.IGNORECASE) or "/"

def _short(text: str, limit: int = 60) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."

def nested_control_findings(snapshot: list[dict[str, Any]] | None, diagnostics: list[str] | None = None,
                            requirement: str = "") -> list[dict[str, str]]:
    try:
        return _nested_control_findings(snapshot or [], str(requirement or ""))
    except Exception as exc:
        if diagnostics is not None:
            diagnostics.append(f"[{CHECK}] snapshot check crashed: {exc!r}")
        return []

def _nested_control_findings(snapshot: list[dict[str, Any]], requirement: str = "") -> list[dict[str, str]]:
    requirement_norm = _norm_text(requirement)
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for page in snapshot:
        if not isinstance(page, dict) or not page.get("aria"):
            continue
        key = _page_key(page)
        outers: dict[tuple[str, str, str], set[int]] = {}
        for node in _walk(parse_aria(str(page["aria"]))):
            if node["role"] not in OUTER_ROLES or not node["name"]:
                continue
            content = " ".join(_contribution(child) for child in node["children"])
            if not _compact(content) or _compact(content) != _compact(node["name"]):
                continue
            inner = [c for c in _inner_controls(node) if _compact(_contribution(c))]
            if node["role"] == "gridcell":
                inner = [c for c in inner if c["role"] not in VALUE_ROLES]
            if not inner:
                continue
            own = _aria_own_label(node, inner)
            if not _compact(own):
                continue
            if requirement_norm and _norm_text(node["name"]) in requirement_norm:
                continue
            for control in inner:
                group_key = (key, node["role"], control["role"])
                groups.setdefault(group_key, {"count": 0, "outer": node, "inner": control, "own": own})
                outers.setdefault(group_key, set()).add(id(node))
        for group_key, nodes in outers.items():
            groups[group_key]["count"] = max(groups[group_key]["count"], len(nodes))
    findings = []
    for (page, outer_role, inner_role), group in groups.items():
        if len(findings) >= MAX_FINDINGS:
            break
        name, own = _short(group["outer"]["name"]), _short(group["own"])
        inner_text = _short(_contribution(group["inner"]))
        count = group["count"]
        more = f" (and the same in {count - 1} other {outer_role} element{'s' if count > 2 else ''} of the page)" if count > 1 else ""
        problem = (
            f'On page {page}, {outer_role} "{name}" has a {inner_role} "{inner_text}" inside it{more}: the {inner_role}\'s '
            f'text becomes part of the {outer_role}\'s accessible name, so looking the {outer_role} up by its own label '
            f'"{own}" with an exact name match finds nothing.'
        )
        fix = (
            f"Render the {inner_role} beside the {outer_role} (a sibling, not a child), or give the {outer_role} "
            f'aria-label equal to its own visible label (here "{own}") so the nested control adds nothing to its name.'
        )
        findings.append({"where": f"{CHECK}: page {page}", "requirement": "", "problem": problem, "fix": fix})
    return findings

class JsxError(Exception):
    pass

_IDENT_CHAR = re.compile(r"[\w$]")
_TAG = re.compile(r"[A-Za-z_$][\w$.:-]*")
_ATTR = re.compile(r"[A-Za-z_$][\w$:.-]*")
_JSX_BEFORE = set("(,=:[!&|?{};>")
_REGEX_BEFORE = set("(,=:[!&|?{};")
_KEYWORDS_BEFORE = {"return", "yield", "default", "case", "await", "typeof", "void", "in", "of", "else", "do"}

class _Reader:

    def __init__(self, text: str) -> None:
        self.t, self.n = text, len(text)

    def line(self, pos: int) -> int:
        return self.t.count("\n", 0, pos) + 1

    def _prev_token(self, out_start: int, i: int) -> tuple[str, str]:
        j = i - 1
        while j >= out_start and self.t[j] in " \t\r\n":
            j -= 1
        if j < out_start:
            return "", ""
        if _IDENT_CHAR.match(self.t[j]):
            k = j
            while k >= out_start and _IDENT_CHAR.match(self.t[k]):
                k -= 1
            return self.t[j], self.t[k + 1 : j + 1]
        return self.t[j], ""

    def skip_string(self, i: int) -> int:
        quote = self.t[i]
        i += 1
        while i < self.n:
            c = self.t[i]
            if c == "\\":
                i += 2
                continue
            if c == quote:
                return i + 1
            if c == "\n":
                raise JsxError(f"unterminated string at line {self.line(i)}")
            i += 1
        raise JsxError("unterminated string")

    def skip_template(self, i: int, found: list) -> int:
        i += 1
        while i < self.n:
            c = self.t[i]
            if c == "\\":
                i += 2
                continue
            if c == "`":
                return i + 1
            if self.t.startswith("${", i):
                i = self.scan_js(i + 2, "}", found) + 1
                continue
            i += 1
        raise JsxError("unterminated template literal")

    def skip_regex(self, i: int) -> int:
        i += 1
        in_class = False
        while i < self.n:
            c = self.t[i]
            if c == "\\":
                i += 2
                continue
            if c == "\n":
                raise JsxError(f"unterminated regex at line {self.line(i)}")
            if in_class:
                if c == "]":
                    in_class = False
            elif c == "[":
                in_class = True
            elif c == "/":
                i += 1
                while i < self.n and _IDENT_CHAR.match(self.t[i]):
                    i += 1
                return i
            i += 1
        raise JsxError("unterminated regex")

    def scan_js(self, i: int, closer: str | None, found: list) -> int:
        start = i
        depth: list[str] = []
        pairs = {"(": ")", "[": "]", "{": "}"}
        while i < self.n:
            c = self.t[i]
            if c in "'\"":
                i = self.skip_string(i)
            elif c == "`":
                i = self.skip_template(i, found)
            elif self.t.startswith("//", i):
                end = self.t.find("\n", i)
                i = self.n if end < 0 else end
            elif self.t.startswith("/*", i):
                end = self.t.find("*/", i + 2)
                if end < 0:
                    raise JsxError("unterminated comment")
                i = end + 2
            elif c == "/":
                prev, word = self._prev_token(start, i)
                if word in _KEYWORDS_BEFORE or (not word and (prev == "" or prev in _REGEX_BEFORE)):
                    i = self.skip_regex(i)
                else:
                    i += 1
            elif c == "<":
                prev, word = self._prev_token(start, i)
                nxt = self.t[i + 1] if i + 1 < self.n else ""
                plausible = (prev == "" or (prev in _JSX_BEFORE and not word) or word in _KEYWORDS_BEFORE)
                if plausible and (nxt.isalpha() or nxt == ">") and not self.t.startswith("<=", i):
                    element, i = self.parse_element(i)
                    found.append(element)
                else:
                    i += 1
            elif c in pairs:
                depth.append(pairs[c])
                i += 1
            elif c in ")]}":
                if not depth:
                    if closer is not None and c == closer:
                        return i
                    raise JsxError(f"unbalanced {c!r} at line {self.line(i)}")
                if depth.pop() != c:
                    raise JsxError(f"mismatched {c!r} at line {self.line(i)}")
                i += 1
            else:
                i += 1
        if closer is not None:
            raise JsxError(f"missing {closer!r}")
        return i

    def _ws(self, i: int) -> int:
        while i < self.n and self.t[i] in " \t\r\n":
            i += 1
        return i

    def parse_element(self, i: int) -> tuple[dict[str, Any], int]:
        pos = i
        i += 1
        if self.t[i] == ">":
            tag = ""
            i += 1
            attrs: dict[str, tuple[str, str]] = {}
            spread = False
        else:
            match = _TAG.match(self.t, i)
            if not match:
                raise JsxError(f"bad tag at line {self.line(i)}")
            tag = match.group(0)
            i = match.end()
            attrs, spread = {}, False
            while True:
                i = self._ws(i)
                if i >= self.n:
                    raise JsxError("unterminated tag")
                if self.t.startswith("/>", i):
                    return self._node(tag, attrs, spread, [], pos), i + 2
                if self.t[i] == ">":
                    i += 1
                    break
                if self.t[i] == "{":
                    inner: list = []
                    end = self.scan_js(i + 1, "}", inner)
                    if self.t[i + 1 : end].strip().startswith("..."):
                        spread = True
                    i = end + 1
                    continue
                amatch = _ATTR.match(self.t, i)
                if not amatch:
                    raise JsxError(f"bad attribute at line {self.line(i)}")
                name = amatch.group(0)
                i = self._ws(amatch.end())
                if i < self.n and self.t[i] == "=":
                    i = self._ws(i + 1)
                    c = self.t[i]
                    if c in "'\"":
                        end = self.t.find(c, i + 1)
                        if end < 0:
                            raise JsxError("unterminated attribute string")
                        attrs[name] = ("str", self.t[i + 1 : end])
                        i = end + 1
                    elif c == "{":
                        inner = []
                        end = self.scan_js(i + 1, "}", inner)
                        attrs[name] = ("expr", self.t[i + 1 : end].strip())
                        self._attr_jsx.extend(inner)
                        i = end + 1
                    elif c == "<":
                        element, i = self.parse_element(i)
                        self._attr_jsx.append(element)
                        attrs[name] = ("expr", "<jsx>")
                    else:
                        raise JsxError(f"bad attribute value at line {self.line(i)}")
                else:
                    attrs[name] = ("bool", "")
        children: list[dict[str, Any]] = []
        while True:
            if i >= self.n:
                raise JsxError(f"unclosed <{tag}> from line {self.line(pos)}")
            if self.t.startswith("</", i):
                j = self._ws(i + 2)
                match = _TAG.match(self.t, j)
                closing = match.group(0) if match else ""
                j = self._ws(match.end() if match else j)
                if closing != tag or j >= self.n or self.t[j] != ">":
                    raise JsxError(f"</{closing}> closes <{tag}> (line {self.line(pos)})")
                return self._node(tag, attrs, spread, children, pos), j + 1
            c = self.t[i]
            if c == "<":
                element, i = self.parse_element(i)
                children.append(element)
            elif c == "{":
                inner = []
                end = self.scan_js(i + 1, "}", inner)
                children.append({"kind": "expr", "src": self.t[i + 1 : end].strip(), "jsx": inner, "pos": i})
                i = end + 1
            else:
                j = i
                while j < self.n and self.t[j] not in "<{":
                    j += 1
                text = self.t[i:j]
                if text.strip():
                    children.append({"kind": "text", "text": text.strip(), "pos": i})
                i = j

    def _node(self, tag, attrs, spread, children, pos) -> dict[str, Any]:
        return {"kind": "el", "tag": tag, "attrs": attrs, "spread": spread, "children": children, "pos": pos}

    def parse(self) -> list[dict[str, Any]]:
        self._attr_jsx: list[dict[str, Any]] = []
        roots: list[dict[str, Any]] = []
        self.scan_js(0, None, roots)
        return roots + self._attr_jsx

def parse_jsx(text: str) -> list[dict[str, Any]]:
    return _Reader(text).parse()

def _elements(nodes: list[dict[str, Any]]):
    stack = [(node, ()) for node in reversed(nodes)]
    while stack:
        node, ancestors = stack.pop()
        if node["kind"] == "el":
            yield node, ancestors
            below = ancestors + (node["tag"],)
            for child in reversed(node["children"]):
                stack.append((child, below))
        elif node["kind"] == "expr":
            for child in reversed(node["jsx"]):
                stack.append((child, ancestors))

def _intrinsic(tag: str) -> bool:
    return bool(tag) and tag[0].islower() and "." not in tag

def _attr_str(node: dict[str, Any], name: str) -> str | None:
    value = node["attrs"].get(name)
    return value[1] if value and value[0] == "str" else None

_ROUTER_IMPORT = re.compile(r"""import\s*(?:type\s+)?\{([^}]*)\}\s*from\s*['"]react-router(?:-dom)?['"]""")

def _router_links(text: str) -> set[str]:
    names = set()
    for match in _ROUTER_IMPORT.finditer(text):
        for part in match.group(1).split(","):
            bits = part.strip().split()
            if not bits:
                continue
            original, local = bits[0], bits[-1]
            if original in ("Link", "NavLink"):
                names.add(local)
    return names

def _component_forwards_role(name: str, defs: list[tuple[str, int, int, list[dict[str, Any]]]]) -> bool:
    found = [d for d in defs if d[0] == name]
    if len(found) != 1:
        return False
    _name, start, end, trees = found[0]
    for element, _anc in _elements(trees):
        if not (start <= element["pos"] < end) or not _intrinsic(element["tag"]):
            continue
        role = element["attrs"].get("role")
        if not role or role[0] != "expr" or role[1] not in ("role", "props.role"):
            continue
        for sub, _a in _elements([element]):
            for child in sub["children"]:
                if child["kind"] == "expr" and child["src"] in ("children", "props.children"):
                    return True
    return False

_DEF = re.compile(r"^(?:export\s+)?(?:default\s+)?(?:function\s+([A-Z]\w*)\s*[(<]|(?:const|let)\s+([A-Z]\w*)\s*(?::[^=\n]+)?=)", re.M)

def _definitions(text: str, trees: list[dict[str, Any]]) -> list[tuple[str, int, int, list[dict[str, Any]]]]:
    starts = [(m.group(1) or m.group(2), m.start()) for m in _DEF.finditer(text)]
    defs = []
    for index, (name, start) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else len(text)
        defs.append((name, start, end, trees))
    return defs

OUTER_SRC_ROLES = {"columnheader", "rowheader", "gridcell", "tab", "button", "link", "menuitem"}
INNER_SRC_ROLES = {"button", "link", "checkbox", "switch", "menuitem", "combobox"}
_CONDITIONAL = re.compile(r"\?|&&|\|\||<|=>|\bfunction\b")

def _outer_role(node, ancestors, links: set[str], forwards) -> str | None:
    tag = node["tag"]
    role = node["attrs"].get("role")
    if role is not None:
        if role[0] != "str" or role[1].strip() not in OUTER_SRC_ROLES:
            return None
        if _intrinsic(tag) or forwards(tag):
            return role[1].strip()
        return None
    if tag == "button":
        return "button"
    if tag == "a" and "href" in node["attrs"]:
        return "link"
    if tag in links:
        return "link"
    if tag == "th":
        scope = _attr_str(node, "scope")
        if scope == "row":
            return "rowheader"
        if scope == "col" or "thead" in ancestors:
            return "columnheader"
    return None

def _labelled(node) -> bool:
    attrs = node["attrs"]
    return node["spread"] or "aria-label" in attrs or "aria-labelledby" in attrs or "aria-hidden" in attrs

_NOT_SET = (("str", "false"), ("expr", "false"))

def _hidden(node) -> bool:
    attrs = node["attrs"]
    for name in ("aria-hidden", "hidden"):
        value = attrs.get(name)
        if value is not None and value not in _NOT_SET:
            return True
    style = attrs.get("style")
    if style is not None and re.search(r"display|visibility", style[1], re.IGNORECASE):
        return True
    for name in ("className", "class"):
        value = attrs.get(name)
        if value is not None and re.search(r"(?<![\w-])(?:[\w-]+:)*(?:hidden|invisible)(?![\w-])", value[1]):
            return True
    return False

def _inner_kind(node, links: set[str]) -> str | None:
    tag, attrs = node["tag"], node["attrs"]
    if _hidden(node):
        return None
    role = attrs.get("role")
    if role is not None:
        if role[0] == "str" and role[1].strip() in INNER_SRC_ROLES and _intrinsic(tag):
            return role[1].strip()
        return None
    if tag == "button":
        return "button"
    if tag == "a" and "href" in attrs:
        return "link"
    if tag in links:
        return "link"
    if tag == "select":
        return "select"
    if tag == "input":
        kind = (_attr_str(node, "type") or "").lower()
        if kind in ("checkbox", "radio") and ("aria-label" in attrs or "aria-labelledby" in attrs):
            return kind
    return None

def _has_text(node) -> bool:
    if node["attrs"].get("aria-label") or node["attrs"].get("aria-labelledby"):
        return True
    for child in node["children"]:
        if child["kind"] == "text" and child["text"].strip():
            return True
        if child["kind"] == "expr" and child["src"] and not re.fullmatch(r"""\s*(['"`])\s*\1\s*""", child["src"]):
            return True
        if child["kind"] == "el" and not _hidden(child) and _has_text(child):
            return True
    return False

def _simple_expr(src: str) -> bool:
    src = src.strip()
    if not src or src in ("children", "props.children") or src.startswith("/*"):
        return False
    if re.fullmatch(r"""(['"`])\s*\1""", src):
        return False
    return not _CONDITIONAL.search(src)

def _own_label(node, links: set[str]) -> list[str]:
    parts: list[str] = []
    for child in node["children"]:
        if child["kind"] == "text":
            parts.append(child["text"])
        elif child["kind"] == "expr":
            if _simple_expr(child["src"]):
                parts.append("{" + child["src"] + "}")
        elif child["kind"] == "el":
            tag = child["tag"]
            if _hidden(child) or "role" in child["attrs"] or tag in ("svg", "img", "script", "style"):
                continue
            if _inner_kind(child, links) or tag in ("input", "textarea", "select", "button", "a"):
                continue
            if tag == "" or _intrinsic(tag):
                parts.extend(_own_label(child, links))
    return parts

def _nested_controls(node, links: set[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for child in node["children"]:
        if child["kind"] == "expr":
            if "createPortal" not in child["src"]:
                found.extend(_nested_controls({"children": child["jsx"]}, links))
        elif child["kind"] == "el":
            if _hidden(child):
                continue
            kind = _inner_kind(child, links)
            if kind:
                if _has_text(child) or kind == "select":
                    found.append(child)
                continue
            if child["tag"] == "" or _intrinsic(child["tag"]):
                found.extend(_nested_controls(child, links))
    return found

def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()

_QUOTED = re.compile("|".join(
    re.escape(a) + "([^" + re.escape(b) + "\\n]{1,200})" + re.escape(b)
    for a, b in (("\u201c", "\u201d"), ('"', '"'), ("`", "`"), ("\u300c", "\u300d"), ("\u2018", "\u2019"))
))

def _quoted_texts(requirement: str) -> list[str]:
    return [_norm_text(next(g for g in m.groups() if g is not None)) for m in _QUOTED.finditer(requirement or "")]

def _pieces(parts: list[str]) -> list[tuple[str, str]]:
    pieces: list[tuple[str, str]] = []
    for part in parts:
        for chunk in re.split(r"(\{[^{}]*\})", part):
            if not chunk.strip():
                continue
            pieces.append(("expr", "") if chunk.startswith("{") and chunk.endswith("}") else ("lit", chunk))
    return pieces

def _requirement_names_whole(own: list[str], control: dict[str, Any], quoted: list[str]) -> bool:
    if not quoted:
        return False
    pieces = _pieces(own) + _pieces([_source_text(control, 0)])
    if not any(kind == "lit" for kind, _ in pieces):
        return False
    pattern = r"\s*".join(r".+?" if kind == "expr" else re.escape(_norm_text(text)) for kind, text in pieces)
    regex = re.compile(r"^\s*" + pattern + r"\s*$")
    return any(regex.match(text) for text in quoted)

def _skip_path(path: Path, root: Path) -> bool:
    parts = set(path.relative_to(root).parts)
    if parts & {"node_modules", "dist", "test-e2e", "tests", "test", "build"}:
        return True
    return bool(re.search(r"\.(spec|test)\.", path.name))

def _source_text(node, limit: int = 50) -> str:
    label = node["attrs"].get("aria-label")
    if label:
        return label[1] if label[0] == "str" else "{" + label[1] + "}"
    parts = []
    for child in node["children"]:
        if child["kind"] == "text":
            parts.append(child["text"])
        elif child["kind"] == "expr":
            parts.append("{" + child["src"] + "}")
        elif child["kind"] == "el" and not _hidden(child):
            parts.append(_source_text(child, 0))
    text = " ".join(p for p in parts if p)
    return _short(text, limit) if limit else text

def nested_control_source_findings(project_dir: Path | str, diagnostics: list[str] | None = None,
                                   requirement: str = "") -> list[dict[str, str]]:
    try:
        return _nested_control_source_findings(Path(project_dir), diagnostics, str(requirement or ""))
    except Exception as exc:
        if diagnostics is not None:
            diagnostics.append(f"[{CHECK}] source check crashed: {exc!r}")
        return []

def _nested_control_source_findings(project_dir: Path, diagnostics: list[str] | None,
                                    requirement: str = "") -> list[dict[str, str]]:
    quoted = _quoted_texts(requirement)
    src = project_dir / "frontend" / "src"
    if not src.is_dir():
        return []
    files = []
    for path in sorted(list(src.rglob("*.tsx")) + list(src.rglob("*.jsx"))):
        if _skip_path(path, src):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            trees = parse_jsx(text)
        except (JsxError, OSError, RecursionError) as exc:
            if diagnostics is not None:
                diagnostics.append(f"[{CHECK}] skipped {path.relative_to(project_dir).as_posix()}: {exc}")
            continue
        files.append((path, text, trees))
    defs: list[tuple[str, int, int, list[dict[str, Any]]]] = []
    for path, text, trees in files:
        defs.extend(_definitions(text, trees))
    cache: dict[str, bool] = {}

    def forwards(tag: str) -> bool:
        if tag not in cache:
            cache[tag] = bool(tag) and tag[0].isupper() and _component_forwards_role(tag, defs)
        return cache[tag]

    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path, text, trees in files:
        links = _router_links(text)
        rel = path.relative_to(project_dir).as_posix()
        reader = _Reader(text)
        for node, ancestors in _elements(trees):
            role = _outer_role(node, ancestors, links, forwards)
            if not role or _labelled(node):
                continue
            controls = _nested_controls(node, links)
            if role == "gridcell":
                controls = [c for c in controls if c["tag"] not in ("select", "input", "textarea")]
            if not controls:
                continue
            own = _own_label(node, links)
            if not own:
                continue
            for control in controls:
                if _requirement_names_whole(own, control, quoted):
                    if diagnostics is not None:
                        diagnostics.append(f"[{CHECK}] {rel}:{reader.line(node['pos'])}: the requirement quotes the whole name")
                    continue
                key = (rel, role, control["tag"])
                group = groups.setdefault(key, {"count": 0, "node": node, "control": control, "own": own,
                                                "line": reader.line(node["pos"]), "cline": reader.line(control["pos"])})
                group["count"] += 1
    findings = []
    for (rel, role, kind), group in groups.items():
        if len(findings) >= MAX_FINDINGS:
            break
        node, control = group["node"], group["control"]
        outer_tag = node["tag"] + (f' role="{role}"' if "role" in node["attrs"] else "")
        own = _short(" ".join(group["own"]), 40)
        inner = _source_text(control)
        more = f" (and {group['count'] - 1} more in this file)" if group["count"] > 1 else ""
        problem = (
            f'<{outer_tag}> (line {group["line"]}) shows its own label {own} and also holds a nested <{control["tag"]}>'
            f' "{inner}" (line {group["cline"]}){more}: whenever that control is rendered, its text becomes part of the '
            f"{role}'s accessible name (e.g. a column header \"A\" becomes \"A Filter Region\"), so the {role} is not "
            f"found by its own label with an exact name match."
        )
        fix = (
            f"Render the <{control['tag']}> beside the {role} element (a sibling, not a child), or give the {role} "
            f"element aria-label equal to its own visible label ({own}) so the nested control adds nothing to its name."
        )
        findings.append({"where": f"{CHECK}: {rel}:{group['line']}", "requirement": "", "problem": problem, "fix": fix})
    return findings
