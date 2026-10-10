from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

IDENT = r"[A-Za-z_$][\w$]*"

ARRAY_MEMBERS = frozenset(
    "length map filter find findIndex findLast findLastIndex forEach some every reduce reduceRight slice splice includes "
    "indexOf lastIndexOf join flatMap flat sort concat at entries keys values push pop shift unshift reverse fill "
    "copyWithin toSorted toReversed toSpliced with".split()
)

ARRAY_CALLS = frozenset("map filter find findIndex forEach some every reduce flatMap slice includes indexOf join sort at".split())
NEUTRAL = frozenset("toString valueOf hasOwnProperty constructor toJSON then catch finally".split())
STORE_FUNCTIONS = {"useData": "GET", "peek": "GET", "load": "GET", "postNow": "POST"}
KEYWORDS = frozenset("if for while switch catch return typeof function await new do else".split())

@dataclass
class Shape:
    kind: str
    keys: frozenset = frozenset()
    open: bool = False
    where: str = ""
    expr: str = ""

@dataclass
class Route:
    method: str
    segments: list
    file: str
    shape: Any = None
    pattern: bool = False

@dataclass
class Read:
    method: str
    path: str
    file: str
    line: int
    name: str
    call: bool = False

@dataclass
class Mismatch:
    method: str
    route: str
    shape: Shape
    file: str
    reads: list = field(default_factory=list)
    kind: str = ""

def callback_body(code: str, start: int) -> str:
    params_end = code.find(")", start)
    if params_end < 0:
        return ""
    after = re.match(r"\s*(=>)?\s*(\{)?", code[params_end + 1:])
    if not after or not after.group(2):

        depth, index = 0, params_end + 1
        while index < len(code):
            if code[index] == "(":
                depth += 1
            elif code[index] == ")":
                if depth == 0:
                    return code[params_end + 1:index]
                depth -= 1
            index += 1
        return code[params_end + 1:]
    brace = params_end + 1 + after.start(2)
    depth = 0
    for index in range(brace, len(code)):
        if code[index] == "{":
            depth += 1
        elif code[index] == "}":
            depth -= 1
            if depth == 0:
                return code[brace:index + 1]
    return code[brace:]

def blank_strings(code: str) -> str:
    out = list(code)
    n = len(code)
    stack: list = []
    depth = 0
    i = 0

    def blank(k: int) -> None:
        if 0 <= k < n and out[k] != "\n":
            out[k] = " "

    while i < n:
        c = code[i]
        if stack and stack[-1] == "tpl":
            if c == "\\":
                blank(i)
                blank(i + 1)
                i += 2
            elif c == "`":
                stack.pop()
                i += 1
            elif code.startswith("${", i):
                stack.append(depth)
                depth += 1
                i += 2
            else:
                blank(i)
                i += 1
            continue
        if c in "'\"":
            j = i + 1
            while j < n and code[j] != c and code[j] != "\n":
                if code[j] == "\\":
                    blank(j)
                    j += 1
                blank(j)
                j += 1
            i = j + 1
            continue
        if c == "/" and _regex_may_start(code, i):
            j, in_class = i + 1, False
            while j < n and code[j] != "\n":
                if code[j] == "\\":
                    j += 2
                    continue
                if code[j] == "[":
                    in_class = True
                elif code[j] == "]":
                    in_class = False
                elif code[j] == "/" and not in_class:
                    break
                j += 1
            if j < n and code[j] == "/":
                for k in range(i + 1, j):
                    blank(k)
                i = j + 1
                continue
        if c == "`":
            stack.append("tpl")
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if stack and stack[-1] == depth:
                stack.pop()
        i += 1
    return "".join(out)

def _regex_may_start(code: str, i: int) -> bool:
    if code.startswith(("//", "/*"), i):
        return False
    k = i - 1
    while k >= 0 and code[k] in " \t\r\n":
        k -= 1
    if k < 0 or code[k] in "(,=:[!&|?{;+-*%~^":
        return True
    word = re.search(r"([A-Za-z_$][\w$]*)$", code[max(0, k - 12) : k + 1])
    return bool(word and word.group(1) in ("return", "typeof", "case", "in", "of", "delete", "void", "yield", "await"))

def bracket_pairs(blank: str) -> dict[int, int]:
    pairs: dict[int, int] = {}
    stack: list[int] = []
    for i, c in enumerate(blank):
        if c in "([{":
            stack.append(i)
        elif c in ")]}" and stack:
            pairs[stack.pop()] = i
    return pairs

def split_top(blank: str, start: int, end: int, pairs: dict[int, int]) -> list[tuple[int, int]]:
    spans, i, begin = [], start, start
    while i < end:
        c = blank[i]
        if c in "([{" and i in pairs:
            i = pairs[i] + 1
            continue
        if c == ",":
            spans.append((begin, i))
            begin = i + 1
        i += 1
    spans.append((begin, end))
    return [(a, b) for a, b in spans if blank[a:b].strip()]

def skeleton(blank: str, pairs: dict[int, int], start: int = 0, end: int | None = None) -> str:
    end = len(blank) if end is None else end
    out, i = [], start
    while i < end:
        c = blank[i]
        if c in "([{" and i in pairs and pairs[i] < end:
            out.append(c + {"(": ")", "[": "]", "{": "}"}[c])
            i = pairs[i] + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)

def statement_end(blank: str, i: int, limit: int, pairs: dict[int, int]) -> int:
    j = i
    while j < limit:
        c = blank[j]
        if c in "([{" and j in pairs:
            j = pairs[j] + 1
            continue
        if c in ")]};":
            return j
        if c == "\n":
            before = blank[i:j].rstrip()
            after = blank[j + 1 : limit].lstrip()
            if (before and before[-1] in "=([{,?:|&+-*/.") or after[:1] in (".", "?", ":", "|", "&", "+", "*", "/", ","):
                j += 1
                continue
            return j
        j += 1
    return limit

def _line(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1

def _template_pattern(inner: str) -> str:
    out, i, n = [], 0, len(inner)
    while i < n:
        if inner.startswith("${", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if inner[j] == "{":
                    depth += 1
                elif inner[j] == "}":
                    depth -= 1
                j += 1
            if depth:
                return ""
            out.append("*")
            i = j
            continue
        out.append(inner[i])
        i += 1
    return "".join(out)

def is_parameter(blank: str, name: str) -> bool:
    word = re.compile(rf"(?<![\w$.]){re.escape(name)}(?![\w$])")
    if re.search(rf"(?<![\w$.]){re.escape(name)}\s*=>", blank):
        return True
    for m in re.finditer(r"\(([^()]*)\)\s*(?::\s*[^=;{}()]+?)?\s*=>", blank):
        if word.search(m.group(1)):
            return True
    for m in re.finditer(rf"\bfunction\b\s*{IDENT}?\s*(?:<[^>()]*>)?\s*\(([^()]*)\)", blank):
        if word.search(m.group(1)):
            return True
    for m in re.finditer(rf"(?<![\w$.])({IDENT})\s*\(([^()]*)\)\s*(?::\s*[^=;{{}}()]+?)?\s*\{{", blank):
        if (m.group(1) not in KEYWORDS or m.group(1) == "catch") and word.search(m.group(2)):
            return True
    return False

def declarations(blank: str, name: str) -> list[int]:
    found = []
    for m in re.finditer(rf"\b(?:const|let|var)\s+(\{{[^{{}}]*\}}|\[[^\[\]]*\]|{IDENT})", blank):
        if re.search(rf"(?<![\w$.]){re.escape(name)}(?![\w$])", m.group(1)):
            found.append(m.start())
    for m in re.finditer(rf"\bfunction\s+{re.escape(name)}(?![\w$])", blank):
        found.append(m.start())
    for m in re.finditer(r"\bimport\s+([^;]+?)\s+from\b", blank):
        if re.search(rf"(?<![\w$.]){re.escape(name)}(?![\w$])", m.group(1)):
            found.append(m.start())
    return found

def _assignments(blank: str, name: str) -> bool:
    for m in re.finditer(rf"(?<![\w$.]){re.escape(name)}\s*(?:=(?![=>])|\+\+|--|[-+*/|&?]{{1,2}}=)", blank):
        before = blank[max(0, m.start() - 12) : m.start()]
        if re.search(r"\b(?:const|let|var)\s+$", before):
            continue
        return True
    return False

def _status_class(code_args: str) -> str:
    numbers = [int(x) for x in re.findall(r"(?<![\w.])(\d{3})(?![\w.])", code_args)]
    if not numbers:
        return "unknown"
    if all(x >= 300 for x in numbers):
        return "error"
    if all(200 <= x < 300 for x in numbers):
        return "success"
    return "unknown"

def _object_shape(code: str, blank: str, start: int, end: int, pairs: dict[int, int]) -> Shape:
    keys: set[str] = set()
    open_ = False
    for a, b in split_top(blank, start + 1, end, pairs):
        prop = code[a:b].strip()
        if prop.startswith("...") or prop.startswith("["):
            open_ = True
            continue
        quoted = re.match(r"(['\"])([^'\"]*)\1\s*:", prop)
        if quoted:
            keys.add(quoted.group(2))
            continue
        m = (re.match(rf"({IDENT}|\d+)\s*:", prop) or re.match(rf"(?:async\s+|get\s+|set\s+)?({IDENT})\s*\(", prop)
             or re.fullmatch(rf"({IDENT})", prop))
        if not m:
            open_ = True
            continue
        keys.add(m.group(1))
    return Shape("object", frozenset(keys), open_)

def _classify(code: str, blank: str, start: int, end: int, pairs: dict[int, int], body: tuple[int, int] | None,
              depth: int = 0) -> Any:
    while start < end and code[start].isspace():
        start += 1
    while end > start and code[end - 1].isspace():
        end -= 1
    text = code[start:end]
    if not text:
        return None
    if text == "null":
        return "null"
    if text[0] in "[{" and pairs.get(start) == end - 1:
        return Shape("array") if text[0] == "[" else _object_shape(code, blank, start, end - 1, pairs)
    skel = skeleton(blank, pairs, start, end).strip()
    if re.fullmatch(rf"await\s+(?:{IDENT}\s*\.\s*)*all\s*\(\)", skel):
        return Shape("array")
    if re.fullmatch(rf"Array\s*\.\s*from\s*\(\)", skel):
        return Shape("array")
    if re.fullmatch(rf"{IDENT}(?:\s*\.\s*{IDENT}|\s*\(\)|\s*\[\]|!)*?\s*\.\s*(?:map|filter)\s*\(\)", skel):
        return Shape("array")
    if re.fullmatch(IDENT, text) and body is not None and depth == 0:
        return _identifier_shape(code, blank, text, body, pairs)
    return None

def _identifier_shape(code: str, blank: str, name: str, body: tuple[int, int], pairs: dict[int, int]) -> Any:
    lo, hi = body
    region = blank[lo:hi]
    decls = [m for m in re.finditer(rf"(?<![\w$.])(const|let|var)\s+{re.escape(name)}\s*(?::[^=;]*)?=(?![=>])", region)]
    if len(decls) != 1 or len(declarations(region, name)) != 1 or is_parameter(region, name) or _assignments(region, name):
        return None
    begin = lo + decls[0].end()
    stop = statement_end(blank, begin, hi, pairs)
    shape = _classify(code, blank, begin, stop, pairs, None, depth=1)
    if isinstance(shape, Shape) and shape.kind == "object" and not shape.open:

        w = re.escape(name)
        rest = region[decls[0].end() + (stop - begin) :]
        if (re.search(rf"(?<![\w$.]){w}\s*(?:\?\.|\.)\s*{IDENT}\s*=(?![=>])", rest)
                or re.search(rf"(?<![\w$.]){w}\s*\[[^\]]*\]\s*=(?![=>])", rest)
                or re.search(rf"[(,]\s*{w}\s*[,)]", rest.replace(f"json({name})", ""))):
            shape = Shape("object", shape.keys, True)
    return shape

def _handler_shape(code: str, blank: str, pairs: dict[int, int], arg: tuple[int, int], rel: str) -> Any:
    a, b = arg
    while a < b and blank[a].isspace():
        a += 1
    m = re.compile(rf"(?:async\s+)?(?:function\b\s*{IDENT}?\s*)?\(").match(blank, a)
    if not m or m.end() > b:
        return None
    params_open = m.end() - 1
    params_close = pairs.get(params_open)
    if params_close is None or params_close >= b:
        return None
    params = [p.strip().split("=")[0].strip() for p in code[params_open + 1 : params_close].split(",")]
    if len(params) < 2 or not re.fullmatch(IDENT, params[1]):
        return None
    res, nxt = params[1], (params[2] if len(params) > 2 and re.fullmatch(IDENT, params[2]) else "")
    after = params_close + 1
    arrow = re.compile(r"\s*=>\s*").match(blank, after)
    if arrow:
        after = arrow.end()
    else:
        brace = re.compile(r"\s*\{").match(blank, after)
        if not brace or "function" not in blank[a:params_open]:
            return None
        after = brace.end() - 1
    if blank[after] == "{" and after in pairs and pairs[after] < b:
        lo, hi = after + 1, pairs[after]
    else:
        lo, hi = after, b
    region = blank[lo:hi]
    if is_parameter(region, res) or (nxt and is_parameter(region, nxt)):
        return None
    if nxt and re.search(rf"(?<![\w$.]){re.escape(nxt)}\s*\(\s*(?:\)|['\"`]route)", region):
        return None
    shapes: list = []
    for occ in re.finditer(rf"(?<![\w$.]){re.escape(res)}(?![\w$])", region):
        i = lo + occ.end()
        chain: list[tuple[str, int, int]] = []
        while True:
            member = re.compile(rf"\s*\.\s*({IDENT})\s*").match(blank, i)
            if not member:
                break
            j = member.end()
            if j < hi and blank[j] == "(" and j in pairs:
                chain.append((member.group(1), j + 1, pairs[j]))
                i = pairs[j] + 1
            else:
                chain.append((member.group(1), -1, -1))
                break
        if not chain:
            before = blank[lo : lo + occ.start()].rstrip()
            if before.endswith(("(", ",")):
                return None
            continue
        responders = {"json", "jsonp", "send", "end", "sendStatus", "redirect", "render", "sendFile", "download", "write"}
        terminal = next((c for c in chain if c[0] in responders), None)
        if terminal is None:
            continue
        status = "success"
        for name, s, e in chain:
            if name == "status" and s >= 0:
                status = _status_class(code[s:e])
        if terminal[0] == "sendStatus":
            continue
        if status == "error":
            continue
        if status == "unknown":
            return None
        if terminal[0] in ("end",) and not code[terminal[1] : terminal[2]].strip():
            continue
        if terminal[0] == "send" and not code[terminal[1] : terminal[2]].strip():
            continue
        if terminal[0] not in ("json", "jsonp") or terminal[1] < 0:
            return None
        if len(split_top(blank, terminal[1], terminal[2], pairs)) != 1:
            return None
        shape = _classify(code, blank, terminal[1], terminal[2], pairs, (lo, hi))
        if shape is None:
            return None
        if shape == "null":
            continue
        shape.where = f"{rel}:{_line(code, terminal[1])}"
        shape.expr = re.sub(r"\s+", " ", code[terminal[1] : terminal[2]].strip())[:60]
        shapes.append(shape)
    if not shapes:
        return None
    kinds = {s.kind for s in shapes}
    if len(kinds) != 1:
        return None
    first = shapes[0]
    if first.kind == "array":
        return first
    return Shape("object", frozenset().union(*(s.keys for s in shapes)), any(s.open for s in shapes), first.where, first.expr)

ROUTE_CALL = re.compile(r"\b(router|app)\s*\.\s*(get|post|put|patch|delete|all)\s*\(")

def backend_routes(project_dir: Path, why: list[str] | None = None) -> list[Route] | None:
    from .detaudit import MOUNT, strip_js_comments

    why = why if why is not None else []
    backend = project_dir / "backend" / "src"
    files = {p: strip_js_comments(p.read_text(encoding="utf-8", errors="replace"))
             for p in sorted(backend.rglob("*.js")) if "node_modules" not in p.parts}
    for text in files.values():
        if re.search(r"\.route\s*\(", text) or re.search(r"require\([^)]*\)\s*\(", text):
            why.append("route() / routes factory")
            return None
        if any(var != "router" for var in re.findall(r"\b(\w+)\s*=\s*(?:express\s*\.\s*)?Router\s*\(", text)):
            why.append("a router under another name")
            return None
        if re.search(r"\b(?!app\b)\w+\s*\.\s*use\s*\(\s*['\"`]", text):
            why.append("a nested mount")
            return None
        if re.search(r"\.\s*json\s*=(?![=>])", text):
            why.append("res.json replaced")
            return None

        blanked = blank_strings(text)
        for middleware in re.finditer(r"\b\w+\s*\.\s*use\s*\(\s*(?:async\s*)?(?:function\s*\w*\s*)?\(\s*\w+\s*,\s*(\w+)\s*,", blanked):
            res_name = middleware.group(1)
            body = callback_body(blanked, middleware.end())
            if re.search(rf"\b(?!{re.escape(res_name)}\b)[A-Za-z_$][\w$]*\s*\([^)]*\b{re.escape(res_name)}\b", body) \
                    or re.search(rf"\bObject\.setPrototypeOf\s*\(\s*{re.escape(res_name)}\b|\b{re.escape(res_name)}\.__proto__", body):
                why.append("a middleware hands the response to another function")
                return None
    mounts: dict[str, list[str]] = {}
    for text in files.values():
        for m in MOUNT.finditer(text):
            mounts.setdefault(m.group(3), []).append(m.group(2).rstrip("/"))
        names = {m.group(1): m.group(2) for m in re.finditer(
            r"""\b(?:const|let|var)\s+(\w+)\s*=\s*require\(\s*['"]\./routes/([\w.-]+?)(?:\.js)?['"]\s*\)""", text)}
        for m in re.finditer(r"""\bapp\.use\s*\(\s*(['"`])([^'"`]+)\1\s*,\s*(\w+)\s*\)""", text):
            if m.group(3) in names:
                mounts.setdefault(names[m.group(3)], []).append(m.group(2).rstrip("/"))
    routes: list[Route] = []
    for path, code in files.items():
        blank = blank_strings(code)
        pairs = bracket_pairs(blank)
        rel = path.relative_to(project_dir).as_posix()
        calls = list(ROUTE_CALL.finditer(blank))
        prefixes = mounts.get(path.stem, [""] if path.parent == backend else [])
        for m in calls:
            open_ = m.end() - 1
            close = pairs.get(open_)
            if close is None:
                why.append(f"unbalanced route call in {rel}")
                return None
            args = split_top(blank, open_ + 1, close, pairs)
            if m.group(1) == "app" and m.group(2) == "get" and len(args) == 1:
                continue
            first = code[args[0][0] : args[0][1]].strip() if args else ""
            literal = re.fullmatch(r"'([^'\\]*)'|\"([^\"\\]*)\"|`([^`$\\]*)`", first)
            if not literal:
                if first.startswith("/") and "?!\\/api" in first:
                    continue
                why.append(f"a route path this reader does not follow in {rel}: {first[:40]}")
                return None
            if not prefixes:
                why.append(f"no mount found for {rel}")
                return None
            route_path = next(g for g in literal.groups() if g is not None)
            shape = _handler_shape(code, blank, pairs, args[-1], rel) if len(args) >= 2 else None
            for prefix in prefixes:
                segments = [s for s in (prefix + "/" + route_path.lstrip("/")).split("/") if s]
                pattern = any(re.search(r"[*?+()]", s) for s in segments)
                routes.append(Route(m.group(2).upper(), segments, rel, shape, pattern))
    return routes

def _store_module(frontend: Path, texts: dict[Path, str]) -> Path | None:
    found = [p for p, t in texts.items() if re.search(r"export\s+function\s+useData\b", t) and "store(path, response.data)" in t]
    return found[0] if len(found) == 1 else None

def _path_of(code: str, blank: str, a: int, b: int) -> str | None:
    text = code[a:b].strip()
    lit = re.fullmatch(r"'([^'\\\n]*)'|\"([^\"\\\n]*)\"", text)
    if lit:
        return lit.group(1) if lit.group(1) is not None else lit.group(2)
    if len(text) >= 2 and text[0] == "`" and text[-1] == "`":
        return _template_pattern(text[1:-1]) or None
    if re.fullmatch(IDENT, text):
        values = set()
        decls = declarations(blank, text)
        consts = list(re.finditer(rf"\bconst\s+{re.escape(text)}\s*(?::\s*string\s*)?=\s*", blank))
        if not consts or len(consts) != len(decls) or is_parameter(blank, text) or _assignments(blank, text):
            return None
        for m in consts:
            rest = code[m.end() :]
            one = re.match(r"('[^'\\\n]*'|\"[^\"\\\n]*\"|`[^`]*`)\s*(?:;|\n)", rest)
            if not one:
                return None
            v = one.group(1)
            values.add(v[1:-1] if v[0] != "`" else _template_pattern(v[1:-1]))
        return values.pop() if len(values) == 1 and "" not in values else None
    return None

def _normalise(path: str) -> str | None:
    if not path.startswith("/") or path.startswith("/api/") or path == "/api":
        return None
    path = path.split("?")[0].split("#")[0]
    segments = [s for s in path.split("/") if s]
    for s in segments:
        if "*" in s and s != "*":
            return None
        if re.search(r"[^\w.*~:@-]", s):
            return None
    return "/api/" + "/".join(segments) if segments else "/api"

MEMBER = re.compile(rf"\s*!?\s*(?:\?\.|\.)\s*({IDENT})")
INDEX = re.compile(r"\s*!?\s*(?:\?\.)?\s*\[\s*\d+\s*\]")

def _member_at(blank: str, i: int) -> tuple[str, bool, int] | None:
    m = MEMBER.match(blank, i)
    if m:
        called = bool(re.compile(r"\s*(?:<[^<>()]*>)?\s*\(").match(blank, m.end()))
        return m.group(1), called, m.end()
    m = INDEX.match(blank, i)
    if m:
        return "[index]", False, m.end()
    return None

def _reads_through(blank: str, i: int, axios: bool) -> tuple[str, bool] | None:
    got = _member_at(blank, i)
    if got is None:
        return None
    if axios:
        if got[0] != "data" or got[1]:
            return None
        got = _member_at(blank, got[2])
        if got is None:
            return None
    return got[0], got[1]

def frontend_reads(project_dir: Path, why: list[str] | None = None) -> list[Read] | None:
    from .detaudit import IMPORT_FROM, _resolve_module, axios_receivers, strip_js_comments

    why = why if why is not None else []
    frontend = project_dir / "frontend" / "src"
    texts = {p: strip_js_comments(p.read_text(encoding="utf-8", errors="replace"))
             for p in sorted(frontend.rglob("*.ts*")) if "node_modules" not in p.parts}
    joined = "\n".join(texts.values())
    bases = re.findall(r"axios\s*\.\s*create\s*\(\s*\{[^}]*baseURL\s*:\s*['\"`]([^'\"`]*)['\"`]", joined)
    if not bases or any(base.rstrip("/") != "/api" for base in bases):
        why.append("no axios instance on /api")
        return None
    if re.search(r"interceptors\s*\.\s*response", joined):
        why.append("a response interceptor")
        return None
    store = _store_module(frontend, texts)
    reads: list[Read] = []
    creates: dict[Path, bool] = {}
    for path, code in texts.items():
        if path == store or re.search(r"(?:\.test\.|\.spec\.|__tests__|[\\/]tests?[\\/])", str(path)):
            continue
        blank = blank_strings(code)
        pairs = bracket_pairs(blank)
        rel = path.relative_to(project_dir).as_posix()
        callers: dict[str, str] = {}
        if store is not None:
            for m in IMPORT_FROM.finditer(code):
                module = _resolve_module(m.group(2), path, frontend)
                if module is None or module.resolve() != store.resolve():
                    continue
                braces = re.search(r"\{([^}]*)\}", m.group(1))
                for part in (braces.group(1).split(",") if braces else []):
                    part = re.sub(r"^\s*type\s+", "", part).strip()
                    pieces = re.split(r"\s+as\s+", part)
                    if pieces[0] in STORE_FUNCTIONS:
                        callers[pieces[-1].strip()] = pieces[0]
        receivers = axios_receivers(path, code, frontend, creates)
        found: list[tuple[str, str, int, int]] = []
        flags: list[tuple[bool, bool]] = []
        for local, exported in callers.items():
            method = STORE_FUNCTIONS[exported]
            for m in re.finditer(rf"(?<![\w$.]){re.escape(local)}\s*(?=[<(])", blank):
                j = m.end()
                if blank[j] == "<":
                    g = re.compile(r"<(?:[^<>()]|<[^<>()]*>|\([^()]*\))*>\s*").match(blank, j)
                    if not g:
                        continue
                    j = g.end()
                if j >= len(blank) or blank[j] != "(" or j not in pairs:
                    continue
                found.append((method, local, m.start(), j))
                flags.append((False, exported == "load"))
        for m in re.finditer(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\.\s*(get|post|put|patch|delete)\s*(?=[<(])", blank):
            if m.group(1) not in receivers:
                continue
            j = m.end()
            if blank[j] == "<":
                g = re.compile(r"<(?:[^<>()]|<[^<>()]*>|\([^()]*\))*>\s*").match(blank, j)
                if not g:
                    continue
                j = g.end()
            if j >= len(blank) or blank[j] != "(" or j not in pairs:
                continue
            found.append((m.group(2).upper(), m.group(1), m.start(), j))
            flags.append((True, True))
        bound: dict[str, set] = {}
        bound_at: dict[str, list] = {}
        for (method, _local, start, open_), (axios, needs_await) in zip(found, flags):
            close = pairs[open_]
            args = split_top(blank, open_ + 1, close, pairs)
            if not args:
                continue
            raw = _path_of(code, blank, *args[0])
            full = _normalise(raw) if raw else None
            if not full:
                continue
            before = blank[max(0, start - 200) : start]
            value_end = close + 1
            awaited = bool(re.search(r"\bawait\s+$", before))
            paren = re.search(r"\(\s*await\s+$", before)
            if paren:
                closing = re.compile(r"\s*\)").match(blank, value_end)
                if not closing:
                    continue
                value_end = closing.end()
            elif awaited and _member_at(blank, value_end):
                continue
            if needs_await and not awaited:
                continue
            line = _line(code, start)
            direct = _reads_through(blank, value_end, axios)
            if direct is not None:
                reads.append(Read(method, full, rel, line, direct[0], direct[1]))
                continue
            if _member_at(blank, value_end):
                continue
            decl = re.search(rf"\bconst\s+({IDENT}|\{{[^{{}}]*\}}|\[[^\[\]]*\])\s*(?::[^=;]*?)?=\s*(?:await\s+)?\(?\s*(?:await\s+)?$", before)
            if not decl:
                continue
            tail = re.compile(r"\s*(?:as\s+[^;\n]+?)?\s*(?:;|\n|$|\?\?|\|\||\))").match(blank, value_end)
            if not tail:
                continue
            pattern = decl.group(1)
            decl_at = max(0, start - 200) + decl.start()
            if pattern.startswith("["):
                reads.append(Read(method, full, rel, line, "[index]"))
                continue
            if pattern.startswith("{"):
                for part in pattern[1:-1].split(","):
                    key = re.match(rf"\s*({IDENT})\s*(?::\s*({IDENT}))?\s*(?:=.*)?$", part)
                    if not key:
                        continue
                    if axios:
                        if key.group(1) == "data":
                            var = key.group(2) or "data"
                            bound.setdefault(var, set()).add((method, full, False))
                            bound_at.setdefault(var, []).append(decl_at)
                        continue
                    reads.append(Read(method, full, rel, line, key.group(1)))
                continue
            bound.setdefault(pattern, set()).add((method, full, axios))
            bound_at.setdefault(pattern, []).append(decl_at)
        for var, targets in bound.items():
            if len(targets) != 1:
                continue
            (method, full, axios), = targets
            if (sorted(declarations(blank, var)) != sorted(set(bound_at[var])) or is_parameter(blank, var)
                    or _assignments(blank, var)):
                continue
            w = re.escape(var)
            if re.search(rf"Array\s*\.\s*isArray\s*\(\s*{w}(?![\w$])|\bin\s+{w}(?![\w$])|\btypeof\s+{w}(?![\w$])|"
                         rf"(?<![\w$.]){w}\s+instanceof\b", blank):
                continue
            decl_ends = set()
            for at in bound_at[var]:
                d = re.compile(rf"\bconst\s+[^=]*?=").match(blank, at)
                decl_ends.add((at, d.end() if d else at))
            for occ in re.finditer(rf"(?<![\w$.]){re.escape(var)}(?![\w$])", blank):
                if any(a <= occ.start() < e for a, e in decl_ends):
                    continue
                got = _reads_through(blank, occ.end(), axios)
                if got is not None:
                    reads.append(Read(method, full, rel, _line(code, occ.start()), got[0], got[1]))
            for m in re.finditer(rf"\bconst\s+\{{([^{{}}]*)\}}\s*=\s*{re.escape(var)}\s*(?:;|\n|\?\?|\|\|)", blank):
                if axios:
                    continue
                for part in m.group(1).split(","):
                    key = re.match(rf"\s*({IDENT})", part)
                    if key:
                        reads.append(Read(method, full, rel, _line(code, m.start()), key.group(1)))
    return reads

def _candidates(routes: list[Route], method: str, segments: list[str]) -> list[Route]:
    out = []
    for route in routes:
        if route.method not in (method, "ALL"):
            continue
        if route.pattern:
            out.append(route)
            continue
        if len(route.segments) != len(segments):
            continue
        if all(r.startswith(":") or s == "*" or r == s for r, s in zip(route.segments, segments)):
            out.append(route)
    return out

def _disagrees(shape: Shape, read: Read) -> str:
    if read.name in NEUTRAL:
        return ""
    if shape.kind == "array":
        return "" if read.name in ARRAY_MEMBERS or read.name == "[index]" else "key"
    if read.name in shape.keys:
        return ""
    if read.name in ARRAY_CALLS and read.call:
        return "array"
    if read.name in ("length", "[index]"):
        return "" if shape.open else "array"
    return "" if shape.open else "key"

def response_shape_mismatches(project_dir: Path, why: list[str] | None = None) -> list[Mismatch]:
    why = why if why is not None else []
    if not (project_dir / "backend" / "src").is_dir() or not (project_dir / "frontend" / "src").is_dir():
        why.append("no backend/src or frontend/src")
        return []
    routes = backend_routes(project_dir, why)
    if not routes:
        return []
    reads = frontend_reads(project_dir, why)
    if not reads:
        return []
    grouped: dict[tuple[str, str], Mismatch] = {}
    for read in reads:
        segments = [s for s in read.path.split("/") if s]
        candidates = _candidates(routes, read.method, segments)
        if len(candidates) != 1 or candidates[0].shape is None:
            continue
        route = candidates[0]
        kind = _disagrees(route.shape, read)
        if not kind:
            continue
        pattern = "/" + "/".join(route.segments)
        item = grouped.setdefault((read.method, pattern), Mismatch(read.method, pattern, route.shape, read.file, [], kind))
        item.reads.append(read)
    return list(grouped.values())

def _describe(shape: Shape) -> str:
    if shape.kind == "array":
        return f"an array (`res.json({shape.expr})`)"
    keys = ", ".join(sorted(shape.keys)) or "none"
    if shape.open:
        return f"an object with at least the keys {keys} (`res.json({shape.expr})`)"
    return f"an object with exactly the keys {keys} (`res.json({shape.expr})`)"

def response_shape_findings(project_dir: Path, log=print) -> list[dict[str, str]]:
    try:
        from .detaudit import MAX_FINDINGS_PER_CHECK, _finding

        why: list[str] = []
        mismatches = response_shape_mismatches(project_dir, why)
        findings = []
        for item in mismatches[:MAX_FINDINGS_PER_CHECK]:
            first = item.reads[0]
            places: dict[str, list[str]] = {}
            for r in item.reads:
                shown = "[0]" if r.name == "[index]" else "." + r.name + ("(...)" if r.call else "")
                places.setdefault(r.file, [])
                if shown not in places[r.file]:
                    places[r.file].append(shown)
            where_read = "; ".join(f"{file}:{next(r.line for r in item.reads if r.file == file)} reads {', '.join(shown[:4])}"
                                   for file, shown in list(places.items())[:5])
            more = f" (and {len(places) - 5} more file(s))" if len(places) > 5 else ""
            read_as = ("reads it by a named key" if item.kind == "key" and item.shape.kind == "array"
                       else "uses it as an array" if item.kind == "array" else "reads keys it does not have")
            findings.append(_finding(
                f"{first.file}:{first.line}",
                "(every page reads a response in the shape its route returns)",
                f"{item.method} {item.route} returns {_describe(item.shape)} at {item.shape.where}, and the frontend "
                f"{read_as}: {where_read}{more}. Whenever the value comes from this route, what the page reads there is "
                "undefined, so whatever it shows or decides from it is empty or wrong.",
                "Return the shape the pages read, or read the shape the route returns; one shape per route (every page "
                f"that reads {item.method} {item.route} reads it the same way).",
            ))
        log(f"[audit] response shapes: {len(mismatches)} mismatch(es)" + (f" (no verdict: {why[0]})" if why else ""))
        return findings
    except Exception as exc:
        log(f"[audit] response-shape check crashed: {exc!r}")
        return []
