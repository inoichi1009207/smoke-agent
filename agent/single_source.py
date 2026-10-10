from __future__ import annotations

import bisect
import functools
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

KINDS = ("double-mount", "shadowed-route", "name-only-lookup", "lookup-owners", "duplicate-engine", "duplicate-helper")
SKIP_DIRS = {"node_modules", "dist", "build", "test", "tests", "__tests__", "test-e2e", "e2e", "e2e-support",
             "coverage", ".git", "test-results", "playwright-report"}
TEST_NAME = re.compile(r"(?:^|[._-])(?:test|tests|spec|e2e)(?:[._-]|$)", re.I)
BACKEND_EXT = {".js", ".cjs", ".mjs", ".ts"}
FRONTEND_EXT = {".js", ".jsx", ".ts", ".tsx"}
MODULE_EXT = (".js", ".ts", ".cjs", ".mjs", ".jsx", ".tsx")
MAX_FILE_BYTES = 400_000
MAX_FILES = 1500
NAME_COLUMNS = ("name", "slug")
SETUP_MODULE = re.compile(r"seed|init_?db|migrat|fixture", re.I)

OWNER_COLUMNS = {"owner", "owner_id", "owner_type", "owner_name", "org_id", "organization_id", "organisation_id",
                 "user_id", "account_id", "tenant_id", "workspace_id", "project_id", "namespace", "namespace_id",
                 "team_id", "workbook_id", "board_id", "repo_id", "repository_id", "folder_id"}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "del", "all", "options", "head"}
FIX = {
    "double-mount": "Mount each router once, at the one prefix the frontend calls; delete the other app.use line.",
    "shadowed-route": ("Keep ONE handler for this method and path: delete the copy that never runs, or move the "
                       "behaviour you need into the handler that runs (do not reorder mounts to switch behaviour)."),
    "name-only-lookup": ("Look the record up by owner AND name through ONE helper; a name alone can match the record "
                         "of another owner."),
    "lookup-owners": ("Keep ONE helper that looks this record up by owner AND name, and call it from every module; "
                      "delete the private copies."),
    "duplicate-engine": ("One module owns formula evaluation; the other calls it or shows its results (e.g. the "
                         "frontend displays the values the backend computed) -- two engines drift apart."),
    "duplicate-helper": "Keep one definition of this helper and import it everywhere else.",
}

@dataclass
class Finding:
    kind: str
    files: list[str] = field(default_factory=list)
    lines: list[int] = field(default_factory=list)
    message: str = ""

_KEYWORDS_BEFORE_REGEX = {"return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "yield", "await",
                          "else", "do"}
_STRING_END = {"'": re.compile(r"(?:[^'\\\n]|\\.)*'", re.S), '"': re.compile(r'(?:[^"\\\n]|\\.)*"', re.S)}
_INTERESTING = re.compile(r"[\"'`/]")
_BRACKETS = re.compile(r"[()\[\]{}]")

class Source:

    def __init__(self, path: Path, rel: str, raw: str) -> None:
        self.path, self.rel, self.raw = path, rel, raw
        self.strings: dict[int, tuple[int, str]] = {}
        self.regexes: list[tuple[int, str]] = []
        self.code = self._lex(raw)
        self._nl = [i for i, ch in enumerate(raw) if ch == "\n"]
        self._functions: list[Function] | None = None

    def line(self, pos: int) -> int:
        return bisect.bisect_left(self._nl, pos) + 1

    def string_at(self, pos: int) -> str | None:
        hit = self.strings.get(pos)
        return hit[1] if hit else None

    @property
    def functions(self) -> list["Function"]:
        if self._functions is None:
            self._functions = _functions(self)
        return self._functions

    def _lex(self, s: str) -> str:
        out: list[str] = []
        n, i, last = len(s), 0, 0

        def blank(a: int, b: int) -> None:
            out.append(re.sub(r"[^\n]", " ", s[a:b]))

        while True:
            m = _INTERESTING.search(s, i)
            if not m:
                break
            j = m.start()
            ch = s[j]
            if ch in "'\"":
                e = _STRING_END[ch].match(s, j + 1)
                if not e:
                    i = j + 1
                    continue
                end = e.end()
                out.append(s[last:j + 1])
                blank(j + 1, end - 1)
                out.append(ch)
                self.strings[j] = (end, s[j + 1:end - 1])
                i = last = end
                continue
            if ch == "`":
                end = self._template_end(s, j)
                out.append(s[last:j + 1])
                blank(j + 1, max(j + 1, end - 1))
                out.append("`" if end <= n and s[end - 1:end] == "`" else "")
                self.strings[j] = (end, s[j + 1:end - 1])
                i = last = end
                continue

            if s.startswith("//", j):
                k = s.find("\n", j)
                k = n if k < 0 else k
                out.append(s[last:j])
                blank(j, k)
                i = last = k
                continue
            if s.startswith("/*", j):
                k = s.find("*/", j + 2)
                k = n if k < 0 else k + 2
                out.append(s[last:j])
                blank(j, k)
                i = last = k
                continue
            if self._regex_allowed(s, j):
                end = self._regex_end(s, j)
                if end:
                    out.append(s[last:j + 1])
                    body_end = s.rfind("/", j + 1, end)
                    blank(j + 1, body_end)
                    out.append(s[body_end:end])
                    self.regexes.append((j, s[j + 1:body_end]))
                    i = last = end
                    continue
            i = j + 1
        out.append(s[last:])
        return "".join(out)

    @staticmethod
    def _regex_allowed(s: str, j: int) -> bool:
        k = j - 1
        while k >= 0 and s[k] in " \t\r\n":
            k -= 1
        if k < 0:
            return True
        prev = s[k]
        if prev in "(,=:[!&|?{};+-*%>~^":
            return True
        if prev.isalnum() or prev in "_$":
            w = k
            while w >= 0 and (s[w].isalnum() or s[w] in "_$"):
                w -= 1
            return s[w + 1:k + 1] in _KEYWORDS_BEFORE_REGEX
        return False

    @staticmethod
    def _regex_end(s: str, j: int) -> int | None:
        i, n, in_class = j + 1, len(s), False
        while i < n:
            ch = s[i]
            if ch == "\n":
                return None
            if ch == "\\":
                i += 2
                continue
            if in_class:
                if ch == "]":
                    in_class = False
            elif ch == "[":
                in_class = True
            elif ch == "/":
                i += 1
                while i < n and (s[i].isalpha()):
                    i += 1
                return i
            i += 1
        return None

    @staticmethod
    def _template_end(s: str, j: int) -> int:
        i, n = j + 1, len(s)
        while i < n:
            ch = s[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "`":
                return i + 1
            if ch == "$" and s.startswith("${", i):
                depth, i = 1, i + 2
                while i < n and depth:
                    c = s[i]
                    if c in "'\"":
                        e = _STRING_END[c].match(s, i + 1)
                        i = e.end() if e else i + 1
                        continue
                    if c == "`":
                        i = Source._template_end(s, i)
                        continue
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                    i += 1
                continue
            i += 1
        return n

@functools.lru_cache(maxsize=16)
def _pairs(code: str) -> dict[int, int]:
    pairs: dict[int, int] = {}
    stack: list[tuple[str, int]] = []
    want = {")": "(", "]": "[", "}": "{"}
    for m in _BRACKETS.finditer(code):
        ch, pos = m.group(0), m.start()
        if ch in "([{":
            stack.append((ch, pos))
            continue
        for k in range(len(stack) - 1, -1, -1):
            if stack[k][0] == want[ch]:
                pairs[stack[k][1]] = pos
                del stack[k:]
                break
    return pairs

def _match_close(code: str, open_pos: int) -> int:
    return _pairs(code).get(open_pos, -1)

def _split_args(code: str, start: int, end: int) -> list[tuple[int, int]]:
    spans, depth, a = [], 0, start
    for i in range(start, end):
        ch = code[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            spans.append((a, i))
            a = i + 1
    spans.append((a, end))
    out = []
    for a, b in spans:
        while a < b and code[a].isspace():
            a += 1
        while b > a and code[b - 1].isspace():
            b -= 1
        if a < b:
            out.append((a, b))
    return out

@dataclass
class Function:
    name: str | None
    start: int
    params: list[str]
    body: tuple[int, int]

_FUNC_DECL = re.compile(r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)?\s*\(")

_BINDING = re.compile(r"(?:\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)(?:\s*:\s*[^=;\n]{1,120}?)?\s*=|"
                      r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*:)\s*(?:async\s+)?(?:function\b\s*\*?\s*[\w$]*\s*)?"
                      r"(?=\(|[A-Za-z_$][\w$]*\s*=>)")
_METHOD = re.compile(r"(?m)^[ \t]*(?:static\s+|public\s+|private\s+|protected\s+)*(?:async\s+)?([A-Za-z_$][\w$]*)\s*\(")
_BODY_BRACE = re.compile(r"\s*(?::[^={;]{0,160}?)?\s*(?:=>)?\s*\{")
_BODY_ARROW = re.compile(r"\s*(?::[^={;]{0,160}?)?\s*=>\s*")
_NOT_METHOD = {"if", "for", "while", "switch", "catch", "function", "return", "with", "else", "do", "try", "typeof",
               "await", "new", "throw", "super", "import", "require"}

def _param_names(text: str) -> list[str]:
    names = []
    for part in _split_args(text, 0, len(text)):
        piece = text[part[0]:part[1]].strip()
        piece = piece.split("=", 1)[0].split(":", 1)[0].strip().lstrip(".")
        names.append(piece if re.fullmatch(r"[A-Za-z_$][\w$]*", piece) else "?")
    return names

def _function_at(code: str, params_open: int) -> tuple[list[str], tuple[int, int]] | None:
    close = _match_close(code, params_open)
    if close < 0:
        return None
    params = _param_names(code[params_open + 1:close])
    m = _BODY_BRACE.match(code, close + 1)
    if m:
        k = m.end() - 1
        end = _match_close(code, k)
        if end < 0:
            return None
        return params, (k, end + 1)
    m = _BODY_ARROW.match(code, close + 1)
    if m:
        k = m.end()
        stop = re.compile(r"[;\n]").search(code, k)
        return params, (k, stop.start() if stop else len(code))
    return None

def _functions(src: Source) -> list[Function]:
    code = src.code
    found: dict[int, Function] = {}
    for m in _FUNC_DECL.finditer(code):
        got = _function_at(code, m.end() - 1)
        if got:
            name = m.group(1)
            if not name:
                b = _BINDING.search(code, max(0, m.start() - 80), m.start() + 1)
                if b and b.end() >= m.start() - 1 and code[b.end():m.start()].strip() in ("", "async"):
                    name = b.group(1) or b.group(2)
            found[m.start()] = Function(name, m.start(), *got)
    for m in _BINDING.finditer(code):
        name = m.group(1) or m.group(2)
        k = m.end()
        if code[k] == "(":
            got = _function_at(code, k)
            if got:
                tail = code[_match_close(code, k) + 1:got[1][0]]
                if "=>" not in tail:
                    continue
                found.setdefault(m.start(), Function(name, m.start(), *got))
        else:
            single = re.compile(r"([A-Za-z_$][\w$]*)\s*=>\s*").match(code, k)
            if single:
                j = single.end()
                if j < len(code) and code[j] == "{":
                    end = _match_close(code, j)
                    if end > 0:
                        found.setdefault(m.start(), Function(name, m.start(), [single.group(1)], (j, end + 1)))
    for m in _METHOD.finditer(code):
        name = m.group(1)
        if name in _NOT_METHOD:
            continue
        got = _function_at(code, m.end() - 1)
        if got and code[got[1][0]] == "{" and "=>" not in code[m.end():got[1][0]]:
            found.setdefault(m.start(), Function(name, m.start(), *got))

    for m in re.finditer(r"\(", code):
        if m.start() in found:
            continue
        close = _match_close(code, m.start())
        if close < 0 or not re.match(r"\s*=>", code[close + 1:close + 8]):
            continue
        got = _function_at(code, m.start())
        if got and not any(f.body == got[1] for f in found.values()):
            found[m.start()] = Function(None, m.start(), *got)
    return sorted(found.values(), key=lambda f: f.start)

def _is_handler(fn: Function) -> bool:
    return len(fn.params) >= 2 and fn.params[0] in ("req", "request") and fn.params[1] in ("res", "response")

def _enclosing_named(src: Source, pos: int) -> Function | None:
    best = None
    for f in src.functions:
        if f.name and f.body[0] <= pos < f.body[1] and (best is None or f.body[0] >= best.body[0]):
            best = f
    return best

def _key(path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(str(path)))

def _collect(project: Path) -> tuple[list[Source], list[Source]]:
    backend: list[Source] = []
    frontend: list[Source] = []
    count = 0
    for part, exts, bucket in (("backend", BACKEND_EXT, backend), ("frontend", FRONTEND_EXT, frontend)):
        root = project / part / "src"
        if not root.is_dir():
            continue
        for folder, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                path = Path(folder) / name
                try:
                    if (path.suffix not in exts or name.endswith(".d.ts") or TEST_NAME.search(path.stem)
                            or path.stat().st_size > MAX_FILE_BYTES):
                        continue
                    count += 1
                    if count > MAX_FILES:
                        return backend, frontend
                    raw = path.read_text(encoding="utf-8", errors="replace")
                    bucket.append(Source(path, path.relative_to(project).as_posix(), raw))
                except Exception:
                    continue
    return backend, frontend

def _resolve_module(src: Source, spec: str, by_path: dict[str, Source]) -> Source | None:
    if not spec.startswith("."):
        return None
    base = os.path.join(str(src.path.parent), spec)
    for cand in (base, *(base + e for e in MODULE_EXT), *(os.path.join(base, "index" + e) for e in MODULE_EXT)):
        hit = by_path.get(_key(cand))
        if hit:
            return hit
    return None

def _imports(src: Source, by_path: dict[str, Source]) -> dict[str, tuple[Source, str]]:
    code, out = src.code, {}
    for m in re.finditer(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*require\s*\(\s*(['\"`])", code):
        spec = src.string_at(m.start(2))
        mod = _resolve_module(src, spec or "", by_path)
        if mod:
            end = src.strings[m.start(2)][0]
            member = re.match(r"\s*\)\s*\.\s*([A-Za-z_$][\w$]*)", code[end:end + 80])
            out[m.group(1)] = (mod, member.group(1) if member else "*")
    for m in re.finditer(r"\b(?:const|let|var)\s*\{([^}]*)\}\s*=\s*require\s*\(\s*(['\"`])", code):
        mod = _resolve_module(src, src.string_at(m.start(2)) or "", by_path)
        if mod:
            for part in m.group(1).split(","):
                orig, _, alias = part.partition(":")
                orig, alias = orig.strip(), (alias.strip() or orig.strip()).split("=")[0].strip()
                if orig and alias:
                    out[alias] = (mod, orig)
    for m in re.finditer(r"\bimport\s+(?:type\s+)?([^'\"`;]*?)\s+from\s*(['\"])", code):
        mod = _resolve_module(src, src.string_at(m.start(2)) or "", by_path)
        if not mod:
            continue
        clause = m.group(1)
        default = re.match(r"\s*([A-Za-z_$][\w$]*)", clause)
        if default and not clause.lstrip().startswith("{"):
            out[default.group(1)] = (mod, "*")
        braces = re.search(r"\{([^}]*)\}", clause)
        if braces:
            for part in braces.group(1).split(","):
                orig, _, alias = part.strip().removeprefix("type ").partition(" as ")
                if orig.strip():
                    out[(alias or orig).strip()] = (mod, orig.strip())
    return out

def _exports(src: Source) -> dict[str, str]:
    code, out = src.code, {}
    m = None
    for m in re.finditer(r"\bmodule\.exports\s*=\s*([A-Za-z_$][\w$]*)\s*[;\n]", code):
        out["*"] = m.group(1)
    for m in re.finditer(r"\bmodule\.exports\s*=\s*\{", code):
        close = _match_close(code, m.end() - 1)
        if close > 0:
            for a, b in _split_args(code, m.end(), close):
                piece = code[a:b]
                key, _, val = piece.partition(":")
                key, val = key.strip(), (val.strip() or key.strip())
                if re.fullmatch(r"[A-Za-z_$][\w$]*", key) and re.fullmatch(r"[A-Za-z_$][\w$]*", val):
                    out[key] = val
    for m in re.finditer(r"(?<![\w$.])(?:module\.)?exports\.([A-Za-z_$][\w$]*)\s*=\s*([A-Za-z_$][\w$]*)\s*[;\n]", code):
        out[m.group(1)] = m.group(2)
    for m in re.finditer(r"\bexport\s+default\s+([A-Za-z_$][\w$]*)\s*[;\n]", code):
        out["*"] = m.group(1)
    for m in re.finditer(r"\bexport\s+(?:async\s+)?(?:function\s*\*?|const|let|var)\s+([A-Za-z_$][\w$]*)", code):
        out[m.group(1)] = m.group(1)
    for m in re.finditer(r"\bexport\s*\{([^}]*)\}\s*(?!from)", code):
        for part in m.group(1).split(","):
            orig, _, alias = part.strip().partition(" as ")
            if orig.strip():
                out[(alias or orig).strip()] = orig.strip()
    return out

_ROUTER_RHS = re.compile(r"\s*(?:(?:express\s*\.\s*)?Router\s*\(|require\s*\(\s*['\"]express['\"]\s*\)\s*\.\s*Router\s*\()")
_APP_RHS = re.compile(r"\s*(?:express|require\s*\(\s*['\"]express['\"]\s*\))\s*\(\s*\)")
_SEGMENT_LITERAL = re.compile(r"[A-Za-z0-9._~%@!$&'=;,-]+")
_SEGMENT_PARAM = re.compile(r":[A-Za-z_$][\w$]*")

@dataclass
class _Route:
    method: str
    path: str | None
    src: Source
    pos: int
    status: str

@dataclass
class _Mount:
    prefix: str | None
    targets: list[tuple[Source, str]]
    src: Source
    pos: int

class _Routers:
    def __init__(self, backend: list[Source], by_path: dict[str, Source]) -> None:
        self.by_path = by_path
        self.routers: dict[str, dict[str, str]] = {}
        self.stacks: dict[tuple[str, str], list] = {}
        self.sources = {s.rel: s for s in backend}
        self.imports = {s.rel: _imports(s, by_path) for s in backend}
        self.exported = {s.rel: _exports(s) for s in backend}
        for s in backend:
            kinds = {}
            for m in re.finditer(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::\s*[\w.<>]+\s*)?=", s.code):
                rhs = s.raw[m.end():m.end() + 80]
                if _ROUTER_RHS.match(rhs):
                    kinds[m.group(1)] = "router"
                elif _APP_RHS.match(rhs):
                    kinds[m.group(1)] = "app"
            self.routers[s.rel] = kinds
        for s in backend:
            try:
                self._registrations(s)
            except Exception:
                continue

    def resolve(self, mod: Source, member: str) -> tuple[Source, str] | None:
        kinds = self.routers.get(mod.rel, {})
        exported = self.exported.get(mod.rel, {})
        local = exported.get("*" if member in ("*", "default") else member)
        if local is None and member != "*" and member in kinds:
            local = member
        if local is None and member in ("*", "default"):
            routers = [v for v, k in kinds.items() if k == "router"]
            if len(routers) == 1 and not exported.get("*"):
                local = routers[0]
        if local and kinds.get(local) == "router":
            return mod, local
        return None

    def _target(self, src: Source, a: int, b: int) -> tuple[Source, str] | None:
        text = src.code[a:b].strip()
        if re.fullmatch(r"[A-Za-z_$][\w$]*", text):
            if self.routers[src.rel].get(text) == "router":
                return src, text
            imp = self.imports[src.rel].get(text)
            return self.resolve(*imp) if imp else None
        m = re.fullmatch(r"require\s*\(\s*(['\"`]).*?\1\s*\)(?:\s*\.\s*([A-Za-z_$][\w$]*))?", src.code[a:b].strip(), re.S)
        if m:
            q = src.code.index(m.group(1), a)
            mod = _resolve_module(src, src.string_at(q) or "", self.by_path)
            return self.resolve(mod, m.group(2) or "*") if mod else None
        return None

    def _registrations(self, src: Source) -> None:
        code, kinds = src.code, self.routers[src.rel]
        if not kinds:
            return
        names = "|".join(re.escape(v) for v in kinds)
        call = re.compile(r"(?<![\w$.])(" + names + r")\s*\.\s*(route|use|" + "|".join(HTTP_METHODS) + r")\s*\(")
        for m in call.finditer(code):
            var, method, open_ = m.group(1), m.group(2), m.end() - 1
            close = _match_close(code, open_)
            if close < 0:
                continue
            args = _split_args(code, open_ + 1, close)
            stack = self.stacks.setdefault((src.rel, var), [])
            if method == "route":
                path = self._path(src, args[0]) if len(args) == 1 else None
                k = close + 1
                while True:
                    c = re.compile(r"\s*\.\s*(" + "|".join(HTTP_METHODS) + r")\s*\(").match(code, k)
                    if not c:
                        break
                    o2 = c.end() - 1
                    c2 = _match_close(code, o2)
                    if c2 < 0:
                        break
                    hargs = _split_args(code, o2 + 1, c2)
                    if hargs:
                        stack.append(_Route(c.group(1), path, src, m.start(), self._status(src, hargs[-1])))
                    k = c2 + 1
                continue
            if not args:
                continue
            first = self._path(src, args[0])
            first_is_path = first is not None or self._is_pathlike(src, args[0])
            if method == "use":
                rest = args[1:] if first_is_path else args
                targets = [t for t in (self._target(src, a, b) for a, b in rest) if t]
                if targets:
                    stack.append(_Mount(first if first_is_path else "", targets, src, m.start()))
                continue
            if len(args) < 2 or not first_is_path:
                continue
            stack.append(_Route("delete" if method == "del" else method, first, src, m.start(),
                                self._status(src, args[-1])))

    @staticmethod
    def _path(src: Source, span: tuple[int, int]) -> str | None:
        a, b = span
        hit = src.strings.get(a)
        if hit and hit[0] == b and "${" not in hit[1] and src.raw[a] in "'\"`":
            return hit[1]
        return None

    @staticmethod
    def _is_pathlike(src: Source, span: tuple[int, int]) -> bool:
        a, b = span
        return src.code[a] in "/[`'\""

    def _status(self, src: Source, span: tuple[int, int]) -> str:
        code = src.code
        a, b = span
        text = code[a:b]
        fn = None
        if re.fullmatch(r"[A-Za-z_$][\w$]*", text.strip()):
            name = text.strip()
            local = [f for f in src.functions if f.name == name]
            if len(local) != 1:
                return "unknown"
            fn = local[0]
        else:
            if not re.match(r"(?:async\s*)?(?:function\b|\(|[A-Za-z_$][\w$]*\s*=>)", text):
                return "unknown"
            inside = [f for f in src.functions if a <= f.start < b]
            if not inside:
                return "unknown"
            fn = inside[0]
        if len(fn.params) < 3:
            return "no-next"
        nxt = fn.params[2]
        if nxt == "?":
            return "unknown"
        body = code[fn.body[0]:fn.body[1]]
        if re.search(r"(?<![\w$.])" + re.escape(nxt) + r"\s*\(\s*(?:\)|['\"`])", body):
            return "calls-next"
        for use in re.finditer(r"(?<![\w$.])" + re.escape(nxt) + r"(?![\w$])", body):
            after = body[use.end():use.end() + 3].lstrip()
            if after.startswith("("):
                continue
            before = body[max(0, use.start() - 12):use.start()]
            if re.search(r"\.catch\s*\(\s*$", before):
                continue
            return "unknown"
        return "no-next"

    def double_mounts(self) -> list[Finding]:
        sites: dict[tuple[str, str], list[_Mount]] = {}
        for stack in self.stacks.values():
            for item in stack:
                if isinstance(item, _Mount):
                    for t in item.targets:
                        sites.setdefault((t[0].rel, t[1]), []).append(item)
        out = []
        for (rel, var), mounts in sorted(sites.items()):
            uniq = sorted({(m.src.rel, m.pos): m for m in mounts}.values(), key=lambda m: (m.src.rel, m.pos))
            if len(uniq) < 2:
                continue
            where = ", ".join(f"{m.src.rel}:{m.src.line(m.pos)} at '{m.prefix or '/'}'" for m in uniq)
            out.append(Finding(
                "double-mount", [m.src.rel for m in uniq], [m.src.line(m.pos) for m in uniq],
                f"The router of {rel} is mounted {len(uniq)} times ({where}), so its handlers answer under every "
                f"prefix and shadow each other. {FIX['double-mount']}"))
        return out

    def flatten(self) -> list[tuple[str, str | None, _Route]]:
        out: list[tuple[str, str | None, _Route]] = []

        def walk(key: tuple[str, str], prefix: str | None, depth: int, seen: tuple) -> None:
            if depth > 8 or key in seen:
                return
            for item in self.stacks.get(key, []):
                if isinstance(item, _Route):
                    out.append((item.method, _join(prefix, item.path), item))
                else:
                    for t in item.targets:
                        walk((t[0].rel, t[1]), _join(prefix, item.prefix), depth + 1, seen + (key,))

        for rel, kinds in self.routers.items():
            for var, kind in kinds.items():
                if kind == "app":
                    walk((rel, var), "", 0, ())
        return out

    def shadowed(self) -> list[Finding]:
        routes = []
        for method, path, route in self.flatten():
            segs = _segments(path)
            if segs is not None:
                routes.append((method, segs, path, route))
        out, reported = [], set()
        for i, (method, segs, path, late) in enumerate(routes):
            covers = []
            for method_e, segs_e, path_e, early in routes[:i]:
                if (early.src.rel, early.pos) == (late.src.rel, late.pos):
                    continue
                if method_e != method and method_e != "all":
                    continue
                if _covers(segs_e, segs):
                    covers.append((early, segs_e, path_e))
            definite = [c for c in covers if c[0].status == "no-next"]
            unknown = [c for c in covers if c[0].status == "unknown"]
            if not definite and not unknown:
                continue
            key = (late.src.rel, late.pos, method)
            if key in reported:
                continue
            reported.add(key)
            early, segs_e, path_e = (definite or unknown)[0]
            line_e, line_l = early.src.line(early.pos), late.src.line(late.pos)
            same = "the same pattern" if segs_e == segs else "a parameter pattern that also matches it"
            reason = ("probably never runs (the earlier handler is defined in another module or wrapped, so whether "
                      "it calls next() is unknown)" if not definite else "never runs")
            out.append(Finding(
                "shadowed-route", [late.src.rel, early.src.rel], [line_l, line_e],
                f"{method.upper()} {path} at {late.src.rel}:{line_l} {reason}: {early.src.rel}:{line_e} registers "
                f"{same} ({early.method.upper()} {path_e}) earlier and Express answers with the first match. "
                f"{FIX['shadowed-route']}"))
        return out

def _join(prefix: str | None, path: str | None) -> str | None:
    if prefix is None or path is None:
        return None
    return "/" + "/".join(p for p in (prefix + "/" + path).split("/") if p)

def _segments(path: str | None) -> list[str] | None:
    if path is None:
        return None
    out = []
    for seg in [p for p in path.split("/") if p]:
        if _SEGMENT_PARAM.fullmatch(seg):
            out.append(":")
        elif _SEGMENT_LITERAL.fullmatch(seg) and not re.search(r"[*?+()]", seg):
            out.append(seg.lower())
        else:
            return None
    return out

def _covers(early: list[str], late: list[str]) -> bool:
    if len(early) != len(late):
        return False
    for e, l_ in zip(early, late):
        if e == ":":
            continue
        if l_ == ":" or e != l_:
            return False
    return True

@dataclass
class _Table:
    name: str
    columns: set[str] = field(default_factory=set)
    unique_sets: list[set[str]] = field(default_factory=list)

def _schema(backend: list[Source], project: Path) -> dict[str, _Table]:
    tables: dict[str, _Table] = {}
    texts = [s.raw for s in backend if s.rel.startswith("backend/src/database/")]
    if not any(re.search(r"\bCREATE\s+TABLE\b", t, re.I) for t in texts):
        texts = [s.raw for s in backend]
    db_dir = project / "backend" / "src" / "database"
    try:
        for p in sorted(db_dir.glob("*.sql")) if db_dir.is_dir() else []:
            texts.append(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    create = re.compile(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`\[]?(\w+)[\"`\]]?\s*\(", re.I)
    for text in texts:
        for m in create.finditer(text):
            close = _sql_close(text, m.end() - 1)
            if close < 0:
                continue
            table = tables.setdefault(m.group(1).lower(), _Table(m.group(1).lower()))
            for part in _sql_split(text[m.end():close]):
                words = part.split()
                if not words:
                    continue
                head = words[0].upper()
                if head in ("UNIQUE", "CONSTRAINT", "PRIMARY", "FOREIGN", "CHECK"):
                    u = re.search(r"\b(?:UNIQUE|PRIMARY\s+KEY)\s*\(([^)]*)\)", part, re.I)
                    if u:
                        table.unique_sets.append({c.strip().strip('"`').lower() for c in u.group(1).split(",")})
                    continue
                col = words[0].strip('"`[]').lower()
                table.columns.add(col)
                if re.search(r"\bUNIQUE\b|\bPRIMARY\s+KEY\b", part, re.I):
                    table.unique_sets.append({col})
        for m in re.finditer(r"\bCREATE\s+UNIQUE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+ON\s+(\w+)\s*\(([^)]*)\)", text, re.I):
            t = tables.setdefault(m.group(1).lower(), _Table(m.group(1).lower()))
            t.unique_sets.append({c.strip().split()[0].strip('"`').lower() for c in m.group(2).split(",") if c.strip()})
        for m in re.finditer(r"\bALTER\s+TABLE\s+(\w+)\s+ADD\s+(?:COLUMN\s+)?[\"`]?(\w+)", text, re.I):
            tables.setdefault(m.group(1).lower(), _Table(m.group(1).lower())).columns.add(m.group(2).lower())
    return tables

def _sql_close(text: str, open_pos: int) -> int:
    depth = 0
    for i in range(open_pos, min(len(text), open_pos + 20000)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1

def _sql_split(body: str) -> list[str]:
    parts, depth, a = [], 0, 0
    for i, ch in enumerate(body):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(body[a:i])
            a = i + 1
    parts.append(body[a:])
    return [p.strip() for p in parts]

def _owned(tables: dict[str, _Table]) -> dict[str, tuple[str, set[str]]]:
    out = {}
    for t in tables.values():
        col = next((c for c in NAME_COLUMNS if c in t.columns), None)
        if not col or {col} in t.unique_sets:
            continue
        composite = [u for u in t.unique_sets if col in u and len(u) > 1]
        if composite:
            scope = set().union(*composite) - {col}
        else:
            scope = {c for c in t.columns if c in OWNER_COLUMNS}
        if any(not c.endswith("_type") for c in scope):
            out[t.name] = (col, scope)
    return out

_SQL_REF = re.compile(r"\b(FROM|JOIN)\s+[\"`]?(\w+)[\"`]?(?:\s+(?:AS\s+)?(\w+))?", re.I)
_SQL_STOP = {"where", "join", "inner", "left", "right", "cross", "on", "order", "group", "limit", "having", "union",
             "natural", "outer", "full", "using", "set", "values"}

@dataclass
class _Lookup:
    table: str
    src: Source
    pos: int
    scoped: bool

def _lookups(src: Source, owned: dict[str, tuple[str, set[str]]], tables: dict[str, _Table]) -> list[_Lookup]:
    out = []
    for pos, (end, sql) in src.strings.items():
        if not re.search(r"\bSELECT\b", sql, re.I) or not re.search(r"\bWHERE\b", sql, re.I):
            continue
        if len(re.findall(r"\bSELECT\b", sql, re.I)) != 1:
            continue
        before = src.code[max(0, pos - 40):pos].rstrip()
        after = src.code[end:end + 40].lstrip()
        if before.endswith("+") or after.startswith("+"):
            continue
        where_at = re.search(r"\bWHERE\b", sql, re.I).start()
        if "${" in sql[where_at:] or re.search(r"\bFROM\s+[\"`]?\w+[\"`]?(?:\s+(?:AS\s+)?\w+)?\s*,", sql, re.I):
            continue
        refs = []
        for m in _SQL_REF.finditer(sql[:where_at]):
            alias = m.group(3)
            if alias and alias.lower() in _SQL_STOP:
                alias = None
            refs.append((m.group(2).lower(), (alias or m.group(2)).lower()))
        tail = sql[where_at:]
        stop = re.search(r"\b(?:ORDER\s+BY|GROUP\s+BY|LIMIT|HAVING)\b", tail, re.I)
        where = tail[:stop.start()] if stop else tail
        on_text = " ".join(re.findall(r"\bON\b(.*?)(?=\bJOIN\b|\bWHERE\b|$)", sql[:where_at], re.I | re.S))
        for table, alias in refs:
            if table not in owned:
                continue
            col, scope = owned[table]
            others_with_col = [t for t, _a in refs if t != table and col in tables.get(t, _Table(t)).columns]
            if others_with_col:
                prefix = r"\b" + re.escape(alias) + r"\s*\.\s*"
            else:
                prefix = r"(?:\b" + re.escape(alias) + r"\s*\.\s*|(?<![\w.]))"
            eq = re.compile(r"(?:\b(?:lower|upper|trim)\s*\(\s*)?" + prefix + re.escape(col)
                            + r"\b\s*\)?\s*(?:COLLATE\s+\w+\s*)?(?:==?|\bIN\b|\bIS\b)", re.I)
            if not eq.search(where):
                continue
            id_col = re.compile(r"(?:\b" + re.escape(alias) + r"\s*\.\s*|(?<![\w.]))id\b\s*(?:=|\bIN\b)", re.I)
            if id_col.search(where):
                continue
            scope_cols = [c for c in scope if not c.endswith("_type")]
            scoped = any(re.search(r"(?<![\w])" + re.escape(c) + r"\b", where + " " + on_text, re.I)
                         for c in scope_cols)
            out.append(_Lookup(table, src, pos, scoped))
    return out

def _lookup_findings(backend: list[Source], project: Path) -> tuple[list[Finding], set[str]]:
    tables = _schema(backend, project)
    owned = _owned(tables)
    if not owned:
        return [], set()
    sites: list[_Lookup] = []
    for src in backend:
        try:
            sites.extend(_lookups(src, owned, tables))
        except Exception:
            continue
    out: list[Finding] = []
    grouped: dict[tuple[str, str], list[_Lookup]] = {}
    for s in sites:
        if not s.scoped:
            grouped.setdefault((s.table, s.src.rel), []).append(s)
    for (table, rel), group in sorted(grouped.items()):
        col, scope = owned[table]
        lines = sorted({g.src.line(g.pos) for g in group})
        owners = ", ".join(sorted(c for c in scope if not c.endswith("_type")))
        out.append(Finding(
            "name-only-lookup", [rel], lines,
            f"{rel}:{','.join(map(str, lines))} selects from `{table}` by `{col}` without its owner ({owners}); "
            f"`{table}`.{col} is unique only per owner, so another owner's record of that name can come back. "
            f"{FIX['name-only-lookup']}"))
    helper_names: set[str] = set()
    by_table: dict[str, dict[tuple[str, str], int]] = {}
    name_only_helper: set[str] = set()
    for s in sites:
        if s.src.rel.startswith("backend/src/database/") or SETUP_MODULE.search(s.src.path.stem):
            continue
        fn = _enclosing_named(s.src, s.pos)
        if fn is None or _is_handler(fn) or SETUP_MODULE.match(fn.name or ""):
            continue
        by_table.setdefault(s.table, {}).setdefault((s.src.rel, fn.name or "?"), s.src.line(fn.start))
        if not s.scoped:
            name_only_helper.add(s.table)
    for table, helpers in sorted(by_table.items()):
        modules = {rel for rel, _n in helpers}
        if len(modules) < 2 or table not in name_only_helper:
            continue
        items = sorted(helpers.items())
        helper_names.update(name for (_rel, name), _l in items)
        listing = ", ".join(f"{rel}:{line} {name}" for (rel, name), line in items)
        out.append(Finding(
            "lookup-owners", [rel for (rel, _n), _l in items], [line for _k, line in items],
            f"{len(items)} functions in {len(modules)} modules each look `{table}` up by `{owned[table][0]}` "
            f"({listing}); they can disagree on the owner rule. {FIX['lookup-owners']}"))
    return out, helper_names

_EVAL_NAME = re.compile(r"(?:evaluate|evaluat|eval|compute|calc|calculate|recalc|recompute)(?:[A-Z_]\w*)?$")
_TOKENIZER_NAME = re.compile(r"(?:tokeni[sz]e|lex|parse(?:Expression|Formula|Primary|Additive|Multiplicative|Term|"
                             r"Factor|Unary))\w*$")
_CELL_REF = re.compile(r"\[(?:A-Z|A-Za-z|a-zA-Z)\](?:\+|\{\d,?\d*\}).{0,24}?(?:\\d|\[0-9\]|\[1-9\])")

def _engine_findings(sources: list[Source]) -> list[Finding]:
    engines: dict[str, Source] = {}
    evaluators: dict[str, set[str]] = {}
    for src in sources:
        try:
            if not re.search(r"[A-Z]\]", src.raw) or not re.search(r"(?:eval|comput|calc)", src.raw, re.I):
                continue
            names = {f.name for f in src.functions if f.name}
            ev = {n for n in names if _EVAL_NAME.match(n)}
            if not ev or not any(_TOKENIZER_NAME.match(n) for n in names):
                continue
            pats = [r for _p, r in src.regexes] + [v for _p, (_e, v) in src.strings.items()]
            if not any(_CELL_REF.search(p) for p in pats):
                continue
            engines[src.rel] = src
            evaluators[src.rel] = ev
        except Exception:
            continue
    by_path = {_key(s.path): s for s in sources}
    owners = []
    for rel, src in engines.items():
        imported = _imports(src, by_path)
        caller = any(mod.rel in engines and mod.rel != rel and (member in evaluators[mod.rel] or local in
                     evaluators[mod.rel]) for local, (mod, member) in imported.items())
        if not caller:
            owners.append(src)
    if len(owners) < 2:
        return []
    owners.sort(key=lambda s: s.rel)
    lines = []
    for s in owners:
        first = min((f.start for f in s.functions if f.name in evaluators[s.rel]), default=0)
        lines.append(s.line(first))
    listing = ", ".join(f"{s.rel}:{ln} ({', '.join(sorted(evaluators[s.rel]))})" for s, ln in zip(owners, lines))
    return [Finding(
        "duplicate-engine", [s.rel for s in owners], lines,
        f"Advisory: {len(owners)} modules each implement a formula engine (evaluator, tokenizer, cell references): "
        f"{listing}; they can compute different results for the same cell. {FIX['duplicate-engine']}")]

def _helper_findings(backend: list[Source], skip_names: set[str]) -> list[Finding]:
    defined: dict[str, list[tuple[Source, int]]] = {}
    for src in backend:
        try:
            exported = _exports(src)
            funcs = {}
            for f in src.functions:
                if f.name and f.name not in funcs and _top_level(src, f):
                    funcs[f.name] = f
            for key, local in exported.items():
                if key == "*" or local not in funcs or key in skip_names:
                    continue
                defined.setdefault(key, []).append((src, src.line(funcs[local].start)))
        except Exception:
            continue
    groups: dict[tuple[str, ...], list[tuple[str, list[tuple[Source, int]]]]] = {}
    for name, sites in sorted(defined.items()):
        mods = tuple(sorted({s.rel for s, _l in sites}))
        if len(mods) >= 2:
            groups.setdefault(mods, []).append((name, sites))
    out = []
    for mods, named in sorted(groups.items()):
        if len(named) == 1:
            name, sites = named[0]
            listing = ", ".join(f"{s.rel}:{ln}" for s, ln in sites)
            out.append(Finding("duplicate-helper", [s.rel for s, _l in sites], [ln for _s, ln in sites],
                               f"`{name}` is defined and exported by {len(mods)} modules ({listing}); a fix in one "
                               f"copy never reaches the others. {FIX['duplicate-helper']}"))
            continue
        first = {}
        for _name, sites in named:
            for s, ln in sites:
                first[s.rel] = min(ln, first.get(s.rel, ln))
        names = ", ".join(f"`{n}`" for n, _s in named)
        out.append(Finding("duplicate-helper", list(mods), [first[m] for m in mods],
                           f"{' and '.join(mods)} define and export the same {len(named)} functions ({names}): one "
                           f"module is a copy of the other, and a fix in one never reaches the other. Keep one module "
                           f"(the one the app imports) and delete or re-export the other."))
    return out

def _top_level(src: Source, f: Function) -> bool:
    return not any(g is not f and g.body[0] < f.start < g.body[1] for g in src.functions)

def audit(project_dir: Path) -> list[Finding]:
    project = Path(project_dir)
    try:
        backend, frontend = _collect(project)
    except Exception:
        return []
    findings: list[Finding] = []
    helper_names: set[str] = set()
    by_path = {_key(s.path): s for s in backend}
    steps = []
    try:
        routers = _Routers(backend, by_path)
        steps += [routers.double_mounts, routers.shadowed]
    except Exception:
        pass
    for step in steps:
        try:
            findings.extend(step())
        except Exception:
            continue
    try:
        lookups, helper_names = _lookup_findings(backend, project)
        findings.extend(lookups)
    except Exception:
        pass
    try:
        findings.extend(_engine_findings(backend + frontend))
    except Exception:
        pass
    try:
        findings.extend(_helper_findings(backend, helper_names))
    except Exception:
        pass
    return findings

def render(findings: list[Finding], limit_chars: int = 4000) -> str:
    if not findings:
        return ""
    order = {k: i for i, k in enumerate(KINDS)}
    by_kind: dict[str, list[Finding]] = {}
    for f in sorted(findings, key=lambda f: (order.get(f.kind, 99), f.files, f.lines)):
        by_kind.setdefault(f.kind, []).append(f)

    items: list[Finding] = []
    queues = list(by_kind.values())
    while any(queues):
        for q in queues:
            if q:
                items.append(q.pop(0))
    fixes = [f"  fix [{k}]: {FIX[k]}" for k in by_kind if k in FIX]
    head = (f"Single-source audit: {len(items)} finding(s). Each rule must have ONE owner in the code; these places "
            "own the same rule twice.")
    budget = limit_chars - sum(len(x) + 1 for x in fixes)
    shown, used = [], len(head)
    for n, f in enumerate(items):
        text = f.message
        fix = FIX.get(f.kind, "")
        if fix and text.endswith(fix):
            text = text[:-len(fix)].rstrip()
        line = f"- [{f.kind}] {text}"
        if used + len(line) + 1 > budget:
            shown.append(f"- ... {len(items) - n} more finding(s) not shown")
            break
        shown.append(line)
        used += len(line) + 1
    return "\n".join([head] + shown + fixes)
