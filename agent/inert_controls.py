from __future__ import annotations

import atexit
import html
import importlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

PARSER_PACKAGES = ["tree-sitter>=0.23,<0.27", "tree-sitter-typescript==0.23.2"]
PARSER_MODULES = ("tree_sitter", "tree_sitter_typescript")
_STATE: dict = {"parser": None, "skip_logged": False, "pydeps": None}
_PYDEPS_LOCK = threading.Lock()

def pydeps_dir(create: bool = False) -> Path | None:
    with _PYDEPS_LOCK:
        if _STATE.get("pydeps") is None and create:
            path = Path(tempfile.mkdtemp(prefix="arc-agent-pydeps-"))
            _STATE["pydeps"] = str(path)
            atexit.register(shutil.rmtree, str(path), True)
        return Path(_STATE["pydeps"]) if _STATE.get("pydeps") else None

def _import_light_pair():
    importlib.invalidate_caches()
    import tree_sitter
    import tree_sitter_typescript
    return tree_sitter.Parser(tree_sitter.Language(tree_sitter_typescript.language_tsx()))

def _load_parser():
    try:
        from tree_sitter_language_pack import get_parser
        return get_parser("tsx")
    except Exception:
        pass
    try:
        return _import_light_pair()
    except Exception:
        pass
    target = pydeps_dir()
    if target is None or not target.is_dir():
        return None
    if str(target) not in sys.path:
        sys.path.insert(0, str(target))
    for module in [m for m in list(sys.modules) if m.split(".")[0] in PARSER_MODULES]:
        if not str(getattr(sys.modules[module], "__file__", "") or "").startswith(str(target)):
            del sys.modules[module]
    try:
        return _import_light_pair()
    except Exception:
        return None

def tsx_parser(log=None):
    if _STATE["parser"] is None:
        _STATE["parser"] = _load_parser()
        if _STATE["parser"] is None and log is not None and not _STATE["skip_logged"]:
            _STATE["skip_logged"] = True
            log("[audit] inert-control check skipped: no TSX parser")
    return _STATE["parser"]

def install_parser_in_background(log=print) -> threading.Thread | None:
    if _load_parser() is not None:
        return None

    def work() -> None:
        try:
            target = pydeps_dir(create=True)
            done = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-input",
                 "--timeout", "20", "--retries", "1", "--target", str(target), *PARSER_PACKAGES],
                capture_output=True, text=True, timeout=120)
            tail = (done.stderr or done.stdout or "").strip()[-200:]
            log(f"[deps] TSX parser install exited {done.returncode}" + (f": {tail!r}" if done.returncode else ""))
        except Exception as exc:
            log(f"[deps] TSX parser install failed: {exc!r}")

    thread = threading.Thread(target=work, name="tsx-parser-install", daemon=True)
    thread.start()
    return thread

QUOTE_RES = [re.compile(r"“([^“”]{1,60})”"), re.compile(r'"([^"]{1,60})"'), re.compile(r"`([^`]{1,60})`")]
CLOSING_NAME = re.compile(r"\b(close|cancel|done|dismiss|back|ok|okay|got it|no|hide|keep editing|never mind|exit|"
                          r"toggle|collapse|minimi[sz]e|expand|menu|sidebar|navigation|drawer|clear|reset)\b", re.I)
CLOSE_VALUES = {"false", "null", "undefined", "''", '""', "``"}
CLICK_HANDLERS = {"onClick", "onClickCapture", "onMouseDown", "onMouseUp", "onPointerDown", "onPointerUp",
                  "onSelect", "onSubmit", "onTouchStart", "onTouchEnd"}
PRIMARY_HANDLERS = {"onClick", "onSelect", "onSubmit"}
HTML_CONTROLS = {"button", "a", "div", "li", "span", "td", "tr", "p", "img", "svg", "section", "article", "label"}
PASS_THROUGH = {"Button", "IconButton", "MenuItem", "DropdownMenuItem", "ContextMenuItem", "MenubarItem",
                "ListItemButton", "DropdownMenu.Item", "Menu.Item", "ContextMenu.Item", "Menubar.Item"}
INTRINSIC_ANCESTOR = re.compile(r"(Trigger|Close|Link|Action|Cancel|Anchor)$|^(Link|NavLink|a|label|summary)$")
CONTROL_ROLES = {"button", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "option", "link", "switch"}

def _norm(s: str) -> str:
    s = " ".join(s.replace("\u00a0", " ").split()).strip().casefold()
    return s.rstrip(":").rstrip("…").rstrip(".").strip() if s.endswith(("…", "...", ":")) else s

ACTIVATE = re.compile(r"\b(click|clicks|clicked|clicking|choose|chooses|choosing|select|selects|selecting|activate|activates|"
                      r"activating|press|presses|pressing|tap|taps|open|opens|opening|use|uses|using|trigger|triggers|"
                      r"invoke|invokes|through|via|from)\b((?:\s+[^\s“”\"`.;:]+){0,4}?)\s+[“\"`]([^“”\"`]{1,60})[”\"`]", re.I)

def activated_names(requirement_text: str) -> set[str]:
    flat = " ".join(requirement_text.split())
    return {_norm(m.group(3)) for m in ACTIVATE.finditer(flat)}

_Q = r"[“\"`]([^“”\"`]{1,60})[”\"`]"
_Q_LIST = r"((?:\s*(?:,|and|or|, and|, or)\s*[“\"`][^“”\"`]{1,60}[”\"`])*)"

BUTTON_BEFORE = re.compile(r"\bbuttons?\b((?:\s+[^\s“”\"`.;:]+){0,5}?)\s+" + _Q + _Q_LIST, re.I)
BUTTON_AFTER = re.compile(_Q + r"\s+buttons?\b", re.I)

def button_names(requirement_text: str) -> set[str]:
    flat = " ".join(requirement_text.split())
    out = set()
    for m in BUTTON_BEFORE.finditer(flat):
        out.add(_norm(m.group(2)))
        out.update(_norm(x) for x in re.findall(_Q, m.group(3) or ""))
    out.update(_norm(m.group(1)) for m in BUTTON_AFTER.finditer(flat))
    return out

def quoted_names(requirement_text: str) -> set[str]:
    flat = " ".join(requirement_text.split())
    out = set()
    for rx in QUOTE_RES:
        for m in rx.finditer(flat):
            n = _norm(m.group(1))
            if len(n) >= 2 and re.search(r"[a-z]", n):
                out.add(n)
    return out

def _txt(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")

def _walk(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))

def _tag_name(el, src) -> str | None:
    head = el if el.type == "jsx_self_closing_element" else el.child_by_field_name("open_tag")
    if head is None:
        return None
    name = head.child_by_field_name("name")
    return _txt(name, src) if name is not None else None

def _attrs(el, src) -> dict:
    head = el if el.type == "jsx_self_closing_element" else el.child_by_field_name("open_tag")
    out = {}
    if head is None:
        return out
    for a in head.children:
        if a.type == "jsx_expression" and a.children and a.children[1].type == "spread_element":
            out["...spread"] = a
        if a.type != "jsx_attribute":
            continue
        key = _txt(a.children[0], src)
        val = a.children[-1] if len(a.children) > 1 else None
        out[key] = val
    return out

def _attr_literal(val, src) -> str | None:
    if val is None:
        return None
    if val.type == "string":
        return _txt(val, src)[1:-1]
    if val.type == "jsx_expression":
        inner = [c for c in val.children if c.type not in ("{", "}", "comment")]
        if len(inner) == 1 and inner[0].type in ("string", "template_string") and "${" not in _txt(inner[0], src):
            return _txt(inner[0], src)[1:-1]
    return None

def _static_text(el, src) -> str | None:
    parts = []
    for c in el.children:
        if c.type in ("jsx_opening_element", "jsx_closing_element"):
            continue
        if c.type == "jsx_text":
            parts.append(_txt(c, src))
        elif c.type == "html_character_reference":
            parts.append(html.unescape(_txt(c, src)))
        elif c.type == "jsx_expression":
            inner = [x for x in c.children if x.type not in ("{", "}", "comment")]
            if not inner:
                continue
            lit = _attr_literal(c, src)
            if lit is None:
                return None
            parts.append(lit)
        elif c.type == "jsx_element":
            sub = _static_text(c, src)
            if sub is None:
                return None
            parts.append(" " + sub + " ")
        elif c.type == "jsx_self_closing_element":
            continue
        else:
            return None
    return " ".join("".join(parts).split())

class FileIndex:
    def __init__(self, src: bytes, root):
        self.src = src
        self.root = root
        self.bindings: dict[str, list] = {}
        self.ident_count: dict[str, int] = {}
        self.ident_nodes: dict[str, list] = {}
        for n in _walk(root):
            if n.type in ("identifier", "shorthand_property_identifier", "shorthand_property_identifier_pattern"):
                k = _txt(n, src)
                self.ident_count[k] = self.ident_count.get(k, 0) + 1
                self.ident_nodes.setdefault(k, []).append(n)
            if n.type == "variable_declarator":
                name = n.child_by_field_name("name")
                value = n.child_by_field_name("value")
                if name is not None:
                    self._bind_pattern(name, ("var", n, value))
            elif n.type in ("function_declaration", "generator_function_declaration"):
                name = n.child_by_field_name("name")
                if name is not None:
                    self._add(_txt(name, src), ("fn", n, n))
            elif n.type in ("required_parameter", "optional_parameter"):
                p = n.child_by_field_name("pattern")
                if p is not None:
                    self._bind_pattern(p, ("param", n, None))
            elif n.type == "arrow_function":
                p = n.child_by_field_name("parameter")
                if p is not None:
                    self._add(_txt(p, src), ("param", n, None))
            elif n.type in ("import_specifier", "namespace_import", "import_clause"):
                for c in n.children:
                    if c.type == "identifier":
                        self._add(_txt(c, src), ("import", n, None))
            elif n.type == "catch_clause":
                p = n.child_by_field_name("parameter")
                if p is not None:
                    self._bind_pattern(p, ("param", n, None))

    def _add(self, name, info):
        self.bindings.setdefault(name, []).append(info)

    def _bind_pattern(self, pat, info):
        if pat.type == "identifier":
            self._add(_txt(pat, self.src), info)
            return
        for c in _walk(pat):
            if c.type in ("identifier", "shorthand_property_identifier_pattern"):

                self._add(_txt(c, self.src), info + (pat,))

    def unique(self, name):
        b = self.bindings.get(name, [])
        return b[0] if len(b) == 1 else None

    def function_body(self, name):
        b = self.unique(name)
        if b is None:
            return None
        kind, node, value = b[0], b[1], b[2]
        if kind == "fn":
            return node
        if kind == "var" and value is not None and len(b) == 3:
            return _unwrap_fn(value, self.src)
        return None

    def presentational(self, state: str, setter: str) -> bool:
        src = self.src
        if self.ident_count.get(state, 0) != len(self.ident_nodes.get(state, [])):
            return False
        for n in self.ident_nodes.get(state, []):
            p = n.parent
            if p.type == "array_pattern":
                continue
            child, q = n, p
            ok = False
            while q is not None:
                op = _txt(q.child_by_field_name("operator"), src) if q.type == "binary_expression" else None
                if op == "&&" and _same(q.child_by_field_name("left"), child):
                    ok = True
                    break
                if q.type in ("unary_expression", "parenthesized_expression") or op in ("&&", "||", "===", "!==", "==", "!="):
                    child, q = q, q.parent
                    continue
                break
            if q is None:
                return False
            if ok:
                pass
            elif q.type == "ternary_expression" and _same(q.child_by_field_name("condition"), child):
                ok = True
            elif q.type == "jsx_expression":
                gp = q.parent
                if gp.type == "jsx_element":
                    ok = _same(child, n)
                elif gp.type == "jsx_attribute":
                    ok = _txt(gp.children[0], src) in ("aria-expanded", "aria-hidden", "hidden", "open", "isOpen",
                                                       "show", "visible", "opened", "data-state", "aria-pressed")
            elif q.type == "arguments" and q.parent is not None and \
                    _txt(q.parent.child_by_field_name("function"), src) == setter and child.type == "unary_expression":
                ok = True
            elif q.type == "array" and q.parent is not None and q.parent.type == "arguments" and \
                    _txt(q.parent.parent.child_by_field_name("function"), src) in ("useEffect", "React.useEffect"):
                ok = True
            elif q.type == "if_statement" and _inside_effect(q, src):
                ok = True
            if not ok:
                return False
        return True

    def read_by_effect(self, state: str) -> bool:
        return any(_inside_effect(n, self.src) for n in self.ident_nodes.get(state, []))

    def state_setter(self, name):
        b = self.unique(name)
        if b is None or b[0] != "var" or len(b) != 4:
            return None
        decl, value, pat = b[1], b[2], b[3]
        if pat.type != "array_pattern" or value is None or value.type != "call_expression":
            return None
        fn = _txt(value.child_by_field_name("function"), self.src)
        if fn not in ("useState", "React.useState"):
            return None
        elems = [c for c in pat.children if c.type not in ("[", "]")]

        names, cur = [], None
        for c in elems:
            if c.type == ",":
                names.append(cur)
                cur = None
            else:
                cur = _txt(c, self.src)
        names.append(cur)
        if len(names) < 2 or names[1] != name:
            return None
        return names[0] or ""

def _same(a, b) -> bool:
    return a is not None and b is not None and (a.start_byte, a.end_byte, a.type) == (b.start_byte, b.end_byte, b.type)

EFFECT_HOOKS = ("useEffect", "React.useEffect", "useLayoutEffect", "React.useLayoutEffect")

def _inside_effect(node, src) -> bool:
    p = node.parent
    while p is not None:
        if p.type == "call_expression" and _txt(p.child_by_field_name("function"), src) in EFFECT_HOOKS:
            return True
        p = p.parent
    return False

def _unwrap_fn(node, src):
    if node is None:
        return None
    if node.type in ("arrow_function", "function_expression", "function"):
        return node
    if node.type == "call_expression":
        fn = _txt(node.child_by_field_name("function"), src)
        if fn in ("useCallback", "React.useCallback"):
            args = [c for c in node.child_by_field_name("arguments").children if c.type not in ("(", ")", ",", "comment")]
            if args:
                return _unwrap_fn(args[0], src)
    if node.type == "parenthesized_expression":
        inner = [c for c in node.children if c.type not in ("(", ")")]
        return _unwrap_fn(inner[0], src) if len(inner) == 1 else None
    return None

def _conjuncts(e, src):
    while e.type == "parenthesized_expression":
        inner = [c for c in e.children if c.type not in ("(", ")")]
        if len(inner) != 1:
            return [e]
        e = inner[0]
    if e.type == "binary_expression" and _txt(e.child_by_field_name("operator"), src) == "&&":
        return _conjuncts(e.child_by_field_name("left"), src) + _conjuncts(e.child_by_field_name("right"), src)
    return [e]

def _truthy_test_of(c, state, src) -> bool:
    t = " ".join(_txt(c, src).split())
    if t in (state, f"!!{state}", f"Boolean({state})"):
        return True
    m = re.fullmatch(re.escape(state) + r"\s*(===|==)\s*(.+)", t) or re.fullmatch(r"(.+?)\s*(===|==)\s*" + re.escape(state), t)
    if m:
        lit = m.group(2) if m.group(0).startswith(state) else m.group(1)
        return bool(re.fullmatch(r"'[^']+'|\"[^\"]+\"|[1-9][0-9]*|true", lit.strip()))
    m = re.fullmatch(re.escape(state) + r"\s*(!==|!=)\s*(null|undefined|false)", t)
    return bool(m)

def _gated_by(el, state, src) -> bool:
    child, p = el, el.parent
    while p is not None:
        if p.type == "binary_expression" and _txt(p.child_by_field_name("operator"), src) == "&&":
            right = p.child_by_field_name("right")
            if child.start_byte >= right.start_byte:
                if any(_truthy_test_of(c, state, src) for c in _conjuncts(p.child_by_field_name("left"), src)):
                    return True
        elif p.type == "ternary_expression":
            cons = p.child_by_field_name("consequence")
            if cons is not None and cons.start_byte <= child.start_byte and child.end_byte <= cons.end_byte:
                if any(_truthy_test_of(c, state, src) for c in _conjuncts(p.child_by_field_name("condition"), src)):
                    return True
        elif p.type in ("jsx_element", "jsx_self_closing_element") and not _same(p, el):
            for k, v in _attrs(p, src).items():
                if k in ("open", "isOpen", "show", "visible", "opened") and v is not None and v.type == "jsx_expression"                         and " ".join(_txt(v, src).split()) == "{" + state + "}":
                    return True
        child, p = p, p.parent
    return False

class Inert:
    def __init__(self, idx: FileIndex, control_name: str, el=None):
        self.idx = idx
        self.el = el
        self.src = idx.src
        self.closing_ok = bool(CLOSING_NAME.search(control_name))
        self.notes: list[str] = []

    def handler(self, val, depth=0) -> bool:
        if val is None or val.type != "jsx_expression":
            return False
        inner = [c for c in val.children if c.type not in ("{", "}", "comment")]
        if len(inner) != 1:
            return False
        return self.expr_as_handler(inner[0], depth)

    def expr_as_handler(self, e, depth) -> bool:
        if depth > 4:
            return False
        fn = _unwrap_fn(e, self.src)
        if fn is not None:
            return self.fn_body(fn, depth)
        if e.type == "identifier":
            f = self.idx.function_body(_txt(e, self.src))
            if f is None:
                return False
            self.notes.append(f"-> {_txt(e, self.src)}()")
            return self.fn_body(f, depth + 1)
        return False

    def fn_body(self, fn, depth) -> bool:
        body = fn.child_by_field_name("body")
        if body is None:
            return False
        if fn.type != "arrow_function" or body.type == "statement_block":
            stmts = [c for c in body.children if c.type not in ("{", "}", "comment")]
            if not stmts:
                self.notes.append("empty body" + (" (comment only)" if any(c.type == "comment" for c in body.children) else ""))
            return all(self.stmt(s, depth) for s in stmts)
        return self.expr(body, depth)

    def stmt(self, s, depth) -> bool:
        if s.type == "empty_statement":
            return True
        if s.type == "expression_statement":
            inner = [c for c in s.children if c.type not in (";", "comment")]
            return len(inner) == 1 and self.expr(inner[0], depth)
        if s.type == "return_statement":
            inner = [c for c in s.children if c.type not in ("return", ";", "comment")]
            if not inner:
                return True
            return len(inner) == 1 and inner[0].type in ("true", "false", "null", "undefined", "number", "string")
        return False

    def expr(self, e, depth) -> bool:
        t = self.src
        if e.type in ("undefined", "null", "true", "false"):
            self.notes.append("returns literal")
            return True
        if e.type == "parenthesized_expression":
            inner = [c for c in e.children if c.type not in ("(", ")")]
            return len(inner) == 1 and self.expr(inner[0], depth)
        if e.type != "call_expression":
            return False
        f = e.child_by_field_name("function")
        args_node = e.child_by_field_name("arguments")
        if f is None or args_node is None or args_node.type != "arguments":
            return False
        args = [c for c in args_node.children if c.type not in ("(", ")", ",", "comment")]
        ftxt = _txt(f, t)
        if f.type == "member_expression":
            obj = _txt(f.child_by_field_name("object"), t)
            prop = _txt(f.child_by_field_name("property"), t)
            if obj == "console":
                if all(a.type in ("string", "template_string", "identifier", "number", "member_expression") for a in args):
                    self.notes.append(f"console.{prop}")
                    return True
                return False
            if prop in ("preventDefault", "stopPropagation") and not args and f.child_by_field_name("object").type == "identifier":
                self.notes.append(f"{prop}()")
                return True
            return False
        if f.type != "identifier":
            return False
        state = self.idx.state_setter(ftxt)
        if state is not None:
            if len(args) != 1:
                return False
            if state and self.idx.read_by_effect(state):

                return False
            a = args[0]
            if state == "" or self.idx.ident_count.get(state, 0) <= 1:
                if a.type in ("string", "number", "true", "false", "null", "undefined", "identifier", "member_expression"):
                    self.notes.append(f"{ftxt}(..) writes state `{state or '<unnamed>'}` that is never read")
                    return True
                return False
            if _txt(a, t) in CLOSE_VALUES and not self.closing_ok and self.el is not None                     and _gated_by(self.el, state, t) and self.idx.presentational(state, ftxt):
                self.notes.append(f"{ftxt}({_txt(a, t)}) only hides the container `{state}` that renders this control")
                return True
            return False

        if all(a.type in ("string", "number", "true", "false", "null", "undefined", "identifier", "member_expression") for a in args):
            fn = self.idx.function_body(ftxt)
            if fn is not None and depth < 4:
                self.notes.append(f"-> {ftxt}()")
                return self.fn_body(fn, depth + 1)
        return False

def _ancestors(el):
    p = el.parent
    while p is not None:
        if p.type in ("jsx_element",):
            yield p
        p = p.parent

EXTERNAL_HOOKS = ("href", "to", "ref", "id", "asChild", "form", "formAction", "aria-expanded", "aria-controls",
                  "aria-haspopup", "popoverTarget", "popovertarget", "popoverTargetAction", "commandfor", "commandFor",
                  "command", "htmlFor", "name", "value")

def literally_disabled(attrs: dict, src: bytes) -> bool:
    for key in ("disabled", "aria-disabled"):
        if key not in attrs:
            continue
        val = attrs[key]
        if val is None:
            if key == "disabled":
                return True
            continue
        text = " ".join(_txt(val, src).split())
        if text in ("{true}", '"true"', "'true'", '{"true"}', "{'true'}") or (key == "disabled" and text in ('"disabled"', '""')):
            return True
    return False

def bare_control_problem(el, tag: str, attrs: dict, src: bytes, disabled_ok: bool = False) -> str | None:
    if tag not in HTML_CONTROLS or tag == "a":
        return "not a plain control"
    if not disabled_ok and any(k in attrs for k in ("disabled", "aria-disabled")):
        return "disabled"
    if any(k in attrs for k in EXTERNAL_HOOKS):
        return "hook attribute"
    if any(k.startswith("data-") and k != "data-testid" for k in attrs):
        return "data attribute"
    btype = _attr_literal(attrs.get("type"), src)
    if tag == "button" and ("type" in attrs and btype not in ("button",)):
        return "submit/reset or computed type"
    for d in _walk(el):
        if d is el or d.type not in ("jsx_element", "jsx_self_closing_element"):
            continue
        dtag = _tag_name(d, src) or ""
        dattrs = _attrs(d, src)
        if dtag in ("button", "a", "input", "select", "textarea", "label") \
                or (d.type == "jsx_element" and dtag[:1].isupper()) \
                or any(re.match(r"on[A-Z]", k) or k in ("href", "to", "...spread") for k in dattrs):

            return "wraps another control"
    anc = list(_ancestors(el))
    anc_tags = [_tag_name(a, src) or "" for a in anc]
    if "form" in anc_tags or any(t.endswith("Form") for t in anc_tags):
        return "inside a form"
    if any(INTRINSIC_ANCESTOR.search(t) for t in anc_tags):
        return "trigger/link ancestor"
    for a in anc:
        aa = _attrs(a, src)
        if "asChild" in aa or "...spread" in aa or any(k in CLICK_HANDLERS for k in aa):
            return "ancestor may handle the click"
    return None

def scan_file(path: Path, names: set[str], rel: str, matched: list | None = None,
              bare_names: set[str] | None = None, live: list | None = None) -> list[dict]:
    bare_names = bare_names or set()
    src = path.read_bytes()
    parser = tsx_parser()
    if parser is None:
        return []
    tree = parser.parse(src)
    idx = None
    out = []
    for el in _walk(tree.root_node):
        if el.type not in ("jsx_element", "jsx_self_closing_element"):
            continue
        tag = _tag_name(el, src)
        if tag is None:
            continue
        attrs = _attrs(el, src)
        if live is not None and bare_names and not literally_disabled(attrs, src) \
                and any(re.match(r"on[A-Z]", k) or k in ("href", "to", "...spread") for k in attrs):

            lab = attrs.get("aria-label")
            lname = _attr_literal(lab, src) if lab is not None else (_static_text(el, src) if el.type == "jsx_element" else None)
            if lname and _norm(lname) in bare_names:
                live.append((_norm(lname), rel, el.start_point[0] + 1))
        if "...spread" in attrs:
            continue
        if tag not in HTML_CONTROLS and tag not in PASS_THROUGH:
            continue
        role = _attr_literal(attrs.get("role"), src)
        if tag in HTML_CONTROLS and tag not in ("button", "a") and (role or "") not in CONTROL_ROLES:
            continue
        label = attrs.get("aria-label")
        if label is not None:
            name = _attr_literal(label, src)
        elif "aria-labelledby" in attrs:
            name = None
        else:
            name = _static_text(el, src) if el.type == "jsx_element" else None
        if not name or (_norm(name) not in names and _norm(name) not in bare_names):
            continue
        if matched is not None:
            matched.append((_norm(name), rel, el.start_point[0] + 1))
        handlers = {k: v for k, v in attrs.items() if re.match(r"on[A-Z]", k)}
        if _norm(name) in names and literally_disabled(attrs, src):

            if bare_control_problem(el, tag, attrs, src, disabled_ok=True) is None:
                out.append({"name": name, "file": rel, "line": el.start_point[0] + 1, "tag": tag,
                            "handler": "literal disabled", "reason": "it carries a literal disabled attribute", "kind": "disabled"})
            continue
        if not handlers:
            if _norm(name) in bare_names and bare_control_problem(el, tag, attrs, src) is None:
                out.append({"name": name, "file": rel, "line": el.start_point[0] + 1, "tag": tag,
                            "handler": "no event handler", "reason": "it has no on* handler at all", "kind": "no-handler"})
            continue
        if _norm(name) not in names:
            continue
        if not (PRIMARY_HANDLERS & handlers.keys()):
            continue

        if any(k in attrs for k in ("href", "to", "ref", "id", "asChild", "form", "formAction", "aria-expanded",
                                    "aria-controls", "aria-haspopup")):
            continue
        if any(k.startswith("data-") and k != "data-testid" for k in attrs):
            continue
        btype = _attr_literal(attrs.get("type"), src)
        if tag in ("button", "Button") and (btype == "submit" or ("type" in attrs and btype is None)):
            continue
        anc = list(_ancestors(el))
        anc_tags = [_tag_name(a, src) or "" for a in anc]
        if tag in ("button", "Button") and btype is None and "form" in anc_tags:
            continue
        if any(INTRINSIC_ANCESTOR.search(t) for t in anc_tags):
            continue
        if any("asChild" in _attrs(a, src) for a in anc):
            continue
        if idx is None:
            idx = FileIndex(src, tree.root_node)
        checker = Inert(idx, name, el)
        if not all(checker.handler(v) for v in handlers.values()):
            continue

        bubble_ok = True
        for a in anc:
            aa = _attrs(a, src)
            for k, v in aa.items():
                if k in CLICK_HANDLERS and not Inert(idx, name, el).handler(v):
                    bubble_ok = False
        if not bubble_ok:
            continue
        hsum = "; ".join(f"{k}={' '.join(_txt(v, src).split())[:90]}" for k, v in handlers.items())
        out.append({"name": name, "file": rel, "line": el.start_point[0] + 1, "tag": tag,
                    "handler": hsum, "reason": "; ".join(dict.fromkeys(checker.notes)), "kind": "inert"})
    return out

def inert_named_controls(requirement_text: str, project_dir: Path, log=print) -> list[dict]:
    project_dir = Path(project_dir)
    if tsx_parser(log) is None:
        return []
    quoted = quoted_names(requirement_text)
    names = quoted & activated_names(requirement_text)
    bare_names = names | (quoted & button_names(requirement_text))
    src_root = project_dir / "frontend" / "src"
    if not src_root.is_dir():
        src_root = project_dir
    files = [p for p in sorted(src_root.rglob("*"))
             if p.suffix in (".tsx", ".jsx", ".ts", ".js") and "node_modules" not in p.parts
             and not re.search(r"\.(test|spec|stories)\.", p.name) and "__tests__" not in p.parts]
    texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in files}
    out, matched, live = [], [], []
    for p in files:
        if p.suffix not in (".tsx", ".jsx"):
            continue

        if p.stem not in ("main", "index", "App") and not any(
                re.search(r"""(?:from\s*|import\s*\(\s*)['"][^'"]*/""" + re.escape(p.stem) + r"""(?:\.[jt]sx?)?['"]""", txt)
                for q, txt in texts.items() if q != p):
            continue
        out.extend(scan_file(p, names, p.relative_to(project_dir).as_posix(), matched, bare_names, live))

    acting = {n for n, _f, _l in live}
    out = [r for r in out if r["kind"] not in ("no-handler", "disabled") or _norm(r["name"]) not in acting]
    for r in out:

        r["same_name_elsewhere"] = [f"{f}:{ln}" for n, f, ln in matched
                                    if n == _norm(r["name"]) and (f, ln) != (r["file"], r["line"])
                                    and not any((f, ln) == (o["file"], o["line"]) for o in out)]
    return out
