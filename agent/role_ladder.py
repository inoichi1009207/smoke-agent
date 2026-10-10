from __future__ import annotations

import bisect
import os
import re
from pathlib import Path
from typing import Any

CHECK = "role_ladder"
MAX_FINDINGS = 8
SKIP_DIRS = {"node_modules", "dist", "build", "test-e2e", "tests", "test", "__tests__", "coverage", ".git",
             ".arc-test-db", "test-results", "playwright-report", "e2e-support"}
SUFFIXES = (".js", ".cjs", ".mjs", ".ts")
KEYWORDS = {"if", "for", "while", "switch", "catch", "with", "return", "typeof", "function", "await", "new", "delete",
            "void", "in", "of", "instanceof", "else", "do", "try", "case", "throw", "yield"}

def _finding(where: str, requirement: str, problem: str, fix: str) -> dict[str, str]:
    return {"where": where, "requirement": requirement, "problem": problem, "fix": fix}

class JsFile:

    def __init__(self, rel: str, path: Path, text: str) -> None:
        self.rel, self.path, self.text = rel, path, text
        self.kept, self.masked = lex(text)
        self._lines = [i for i, ch in enumerate(text) if ch == "\n"]
        self._functions: list[dict[str, Any]] | None = None
        self._routes: list[dict[str, Any]] | None = None

    def line(self, pos: int) -> int:
        return bisect.bisect_left(self._lines, pos) + 1

    def line_text(self, pos: int) -> str:
        start = self.text.rfind("\n", 0, pos) + 1
        end = self.text.find("\n", pos)
        return self.text[start:end if end >= 0 else len(self.text)].strip()

    @property
    def functions(self) -> list[dict[str, Any]]:
        if self._functions is None:
            self._functions = find_functions(self.masked)
        return self._functions

    @property
    def routes(self) -> list[dict[str, Any]]:
        if self._routes is None:
            self._routes = find_routes(self)
        return self._routes

def lex(text: str) -> tuple[str, str]:
    kept: list[str] = []
    masked: list[str] = []
    i, n = 0, len(text)
    last_sig = ""
    last_word = ""

    def blank(segment: str) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in segment)

    while i < n:
        c = text[i]
        if c in "'\"`":
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == c or (c != "`" and text[j] == "\n"):
                    break
                j += 1
            j = min(j, n - 1)
            seg = text[i:j + 1]
            kept.append(seg)
            masked.append(c + blank(seg[1:-1]) + (seg[-1] if len(seg) > 1 else ""))
            i = j + 1
            last_sig, last_word = c, ""
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
            kept.append(" " * (end - i))
            masked.append(" " * (end - i))
            i = end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            seg = blank(text[i:end])
            kept.append(seg)
            masked.append(seg)
            i = end
            continue
        if c == "/" and (last_sig == "" or last_sig in "(,=:[!&|?{};+-*%<>~^" or last_word in KEYWORDS):
            j, in_class = i + 1, False
            while j < n and text[j] != "\n":
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "[":
                    in_class = True
                elif text[j] == "]":
                    in_class = False
                elif text[j] == "/" and not in_class:
                    break
                j += 1
            if j < n and text[j] == "/":
                k = j + 1
                while k < n and (text[k].isalpha()):
                    k += 1
                seg = text[i:k]
                kept.append(seg)
                masked.append("/" + blank(text[i + 1:j]) + text[j:k])
                i = k
                last_sig, last_word = ")", ""
                continue
        kept.append(c)
        masked.append(c)
        if not c.isspace():
            if c.isalnum() or c in "_$":
                start = i
                while i + 1 < n and (text[i + 1].isalnum() or text[i + 1] in "_$"):
                    i += 1
                    kept.append(text[i])
                    masked.append(text[i])
                last_word = text[start:i + 1]
                last_sig = "a"
            else:
                last_sig, last_word = c, ""
        i += 1
    return "".join(kept), "".join(masked)

PAIRS = {"(": ")", "[": "]", "{": "}"}

def match_close(masked: str, open_pos: int) -> int:
    stack: list[str] = []
    for i in range(open_pos, len(masked)):
        ch = masked[i]
        if ch in PAIRS:
            stack.append(PAIRS[ch])
        elif ch in ")]}":
            if not stack or stack.pop() != ch:
                return -1
            if not stack:
                return i
    return -1

def split_top(text: str, start: int, end: int, masked: str) -> list[tuple[int, int]]:
    spans, depth, s = [], 0, start
    for i in range(start, end):
        ch = masked[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            spans.append((s, i))
            s = i + 1
    spans.append((s, end))
    return [(a, b) for a, b in spans if masked[a:b].strip()]

IDENT = r"[A-Za-z_$][\w$]*"

def _params(masked: str, kept: str, open_pos: int, close_pos: int) -> list[str | None]:
    out: list[str | None] = []
    for a, b in split_top(kept, open_pos + 1, close_pos, masked):
        m = re.match(r"\s*(?:\.\.\.)?(" + IDENT + r")\s*(?:=|$)", masked[a:b])
        out.append(m.group(1) if m else None)
    return out

def find_functions(masked: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in re.finditer(r"\(", masked):
        open_pos = m.start()
        close = match_close(masked, open_pos)
        if close < 0:
            continue
        after = masked[close + 1:close + 40]
        before = masked[max(0, open_pos - 120):open_pos]
        arrow = re.match(r"\s*=>\s*", after)
        name = None
        if arrow:
            body_at = close + 1 + arrow.end()
            nm = re.search(r"(?:(?:const|let|var)\s+(" + IDENT + r")\s*=|(" + IDENT + r")\s*:)\s*(?:async\s*)?$", before)
            if nm:
                name = nm.group(1) or nm.group(2)
        else:
            brace = re.match(r"\s*\{", after)
            if not brace:
                continue
            body_at = close + 1 + brace.end() - 1
            fn = re.search(r"\bfunction\s*\*?\s*(" + IDENT + r")?\s*$", before)
            if fn:
                name = fn.group(1)
                if not name:
                    nm = re.search(r"(?:(?:const|let|var)\s+(" + IDENT + r")\s*=|(" + IDENT + r")\s*:)\s*(?:async\s*)?function\s*\*?\s*$", before)
                    if nm:
                        name = nm.group(1) or nm.group(2)
            else:
                meth = re.search(r"(?:^|[\s;{},])(?:async\s+|static\s+|get\s+|set\s+)*(" + IDENT + r")\s*$", before)
                if not meth or meth.group(1) in KEYWORDS:
                    continue
                name = meth.group(1)
        params = _params(masked, masked, open_pos, close)
        if masked[body_at:body_at + 1] == "{":
            end = match_close(masked, body_at)
            if end < 0:
                continue
        else:
            depth, end = 0, len(masked)
            for i in range(body_at, len(masked)):
                ch = masked[i]
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    if depth == 0:
                        end = i
                        break
                    depth -= 1
                elif depth == 0 and (ch == ";" or ch == "\n" or ch == ","):
                    end = i
                    break
        out.append({"name": name, "params": params, "start": body_at, "end": end, "paren": open_pos})

    for m in re.finditer(r"(?<![\w$.])(" + IDENT + r")\s*=>\s*", masked):
        if m.group(1) in KEYWORDS:
            continue
        body_at = m.end()
        if masked[body_at:body_at + 1] != "{":
            continue
        end = match_close(masked, body_at)
        if end > 0:
            before = masked[max(0, m.start() - 80):m.start()]
            nm = re.search(r"(?:const|let|var)\s+(" + IDENT + r")\s*=\s*(?:async\s*)?$", before)
            out.append({"name": nm.group(1) if nm else None, "params": [m.group(1)], "start": body_at, "end": end,
                        "paren": m.start()})
    return out

def enclosing(functions: list[dict[str, Any]], pos: int) -> list[dict[str, Any]]:
    hits = [f for f in functions if f["start"] <= pos <= f["end"]]
    return sorted(hits, key=lambda f: f["end"] - f["start"])

ROUTE = re.compile(r"(?<![\w$])(" + IDENT + r")\s*\.\s*(get|post|put|patch|delete|all)\s*\(\s*(['\"`])(/[^'\"`\n]*)\3")
NOT_ROUTERS = {"axios", "request", "http", "https", "client", "api", "agent", "supertest", "res", "req", "map", "cache",
               "db", "store", "params", "query", "headers", "searchParams", "url", "fetch", "this", "window", "session"}

def find_routes(f: JsFile) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in ROUTE.finditer(f.kept):
        if m.group(1) in NOT_ROUTERS:
            continue
        open_pos = f.kept.index("(", m.start(2))
        close = match_close(f.masked, open_pos)
        if close < 0:
            continue
        spans = split_top(f.kept, open_pos + 1, close, f.masked)
        handlers = [f.masked[a:b].strip() for a, b in spans[1:]]
        names = [h for h in handlers if re.fullmatch(IDENT, h)]
        out.append({"receiver": m.group(1), "method": m.group(2).lower(), "path": m.group(4), "pos": m.start(),
                    "line": f.line(m.start()), "args": (open_pos, close), "handler_names": names,
                    "last_arg": spans[-1] if spans else None})
    return out

def routes_at(f: JsFile, pos: int) -> list[dict[str, Any]]:
    inline = [r for r in f.routes if r["args"][0] < pos < r["args"][1]]
    if inline:
        return inline
    names = {fn["name"] for fn in enclosing(f.functions, pos) if fn["name"]}

    return [r for r in f.routes if names & set(r["handler_names"])]

def backend_files(project_dir: Path, limit: int = 4_000_000) -> list[JsFile]:
    root = Path(project_dir) / "backend"
    out: list[JsFile] = []
    used = 0
    if not root.is_dir():
        return out
    for folder, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(names):
            if not name.endswith(SUFFIXES) or re.search(r"\.(?:test|spec)\.", name) or name.endswith(".d.ts"):
                continue
            path = Path(folder, name)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            used += len(text)
            if used > limit:
                return out
            out.append(JsFile(path.relative_to(project_dir).as_posix(), path, text))
    return out

def resolve_module(spec: str, importer: Path, by_path: dict[str, JsFile]) -> JsFile | None:
    if not spec.startswith("."):
        return None
    base = (importer.parent / spec).resolve()
    for cand in (base, Path(str(base) + ".js"), Path(str(base) + ".cjs"), Path(str(base) + ".mjs"),
                 Path(str(base) + ".ts"), base / "index.js", base / "index.ts"):
        hit = by_path.get(os.path.normcase(str(cand)))
        if hit:
            return hit
    return None

REQUIRE_BIND = re.compile(r"(?:const|let|var)\s+(" + IDENT + r")\s*=\s*require\(\s*(['\"])([^'\"]+)\2\s*\)"
                          r"|import\s+(" + IDENT + r")\s+from\s+(['\"])([^'\"]+)\5")
USE_CALL = re.compile(r"(?<![\w$])(" + IDENT + r")\s*\.\s*use\s*\(")

def mount_prefixes(files: list[JsFile]) -> dict[str, list[str]]:
    by_path = {os.path.normcase(str(f.path.resolve())): f for f in files}
    edges: dict[str, list[tuple[str, str]]] = {}
    for f in files:
        binds = {}
        for m in REQUIRE_BIND.finditer(f.kept):
            name, spec = (m.group(1), m.group(3)) if m.group(1) else (m.group(4), m.group(6))
            target = resolve_module(spec, f.path, by_path)
            if target:
                binds[name] = target.rel
        for m in USE_CALL.finditer(f.kept):
            open_pos = m.end() - 1
            close = match_close(f.masked, open_pos)
            if close < 0:
                continue
            spans = split_top(f.kept, open_pos + 1, close, f.masked)
            if not spans:
                continue
            first = f.kept[spans[0][0]:spans[0][1]].strip()
            prefix, rest = "", spans
            pm = re.fullmatch(r"(['\"`])(/[^'\"`]*)\1", first)
            if pm:
                prefix, rest = pm.group(2), spans[1:]
            for a, b in rest:
                arg = f.kept[a:b].strip()
                rm = re.fullmatch(r"require\(\s*(['\"])([^'\"]+)\1\s*\)(?:\s*\.\s*\w+)?", arg)
                child = None
                if rm:
                    target = resolve_module(rm.group(2), f.path, by_path)
                    child = target.rel if target else None
                elif re.fullmatch(IDENT, arg) and arg in binds:
                    child = binds[arg]
                if child:
                    edges.setdefault(f.rel, []).append((prefix, child))
    roots = [f.rel for f in files if re.search(r"\bexpress\s*\(\s*\)|\.listen\s*\(", f.masked)]
    prefixes: dict[str, list[str]] = {r: [""] for r in roots}
    queue = list(roots)
    seen_pairs = set()
    while queue:
        parent = queue.pop(0)
        for prefix, child in edges.get(parent, []):
            for base in prefixes.get(parent, []):
                full = join_path(base, prefix)
                if (child, full) in seen_pairs or len(seen_pairs) > 2000:
                    continue
                seen_pairs.add((child, full))
                prefixes.setdefault(child, [])
                if full not in prefixes[child]:
                    prefixes[child].append(full)
                    queue.append(child)
    return prefixes

def join_path(base: str, path: str) -> str:
    joined = (base.rstrip("/") + "/" + path.lstrip("/")) if path else base
    return joined if joined.startswith("/") else "/" + joined

NOT_LADDER = re.compile(r"(?i)\bnot\s+(?:an?\s+)?(?:automatic(?:ally)?\s+)?(?:cumulative\s+)?(?:ladder|hierarch\w*)\b|"
                        r"\b(?:roles?|permissions?)\b[^.]{0,80}\bnot\s+(?:automatically\s+)?cumulative\b|"
                        r"\bno\s+(?:automatic\s+)?(?:cumulative\s+)?(?:role\s+)?(?:ladder|hierarchy)\b")
MAY = re.compile(r"\b(may|can)\b(?!\s+not\b)(?!\s+only\b)")
FILLER = {"only", "a", "an", "the", "signed-in", "signed", "in", "user", "users", "with", "who", "have", "has", "hold",
          "holds", "holding", "permission", "permissions", "role", "roles", "status", "organization", "org", "owner",
          "author", "authors", "repository", "repo", "member", "members", "account", "accounts", "pr", "of", "or",
          "and", "any", "current", "level", "access", "s", "nor"}

def requirement_sentences(requirement: str) -> list[str]:
    from .seed_identity import requirement_texts
    from .seed_presence import split_sentences

    out: list[str] = []
    seen = set()
    for text in requirement_texts(requirement):
        for sentence in split_sentences(text):
            if sentence not in seen:
                seen.add(sentence)
                out.append(sentence)
    return out

def role_lists(sentences: list[str], vocab: dict[str, str]) -> list[dict[str, Any]]:
    words_re = re.compile(r"[A-Za-z][\w-]*|[,/]")
    lists = []
    for sentence in sentences:
        for clause in re.split(r";|:\s", sentence):
            for m in MAY.finditer(clause):
                tokens = list(words_re.finditer(clause[:m.start()]))
                roles, only = [], False
                for tok in reversed(tokens):
                    word = tok.group(0)
                    low = word.lower()
                    if low in vocab and word[:1].isupper():
                        roles.append(low)
                    elif word in ",/" or low in FILLER or (word[:1].isupper() and low not in vocab and len(roles) > 0
                                                          and word.isalpha() and len(word) <= 12):
                        if low == "only":
                            only = True
                    else:
                        break
                if not roles:
                    continue
                rest = clause[m.end():]
                nxt = MAY.search(rest)
                action = rest[:nxt.start()] if nxt else rest
                if re.match(r"\s*(?:only|not|also only)\b", action, re.I):
                    continue

                action = re.split(r"(?i)\b(?:but\s+)?(?:cannot|can't|may\s+not|must\s+not|are\s+not\s+allowed)\b", action)[0]
                lists.append({"roles": frozenset(roles), "action": action.strip(), "only": only or
                              bool(re.match(r"\s*only\b", clause, re.I)), "sentence": sentence})
    return lists

def find_ranks(files: list[JsFile], requirement_words: set[str]) -> dict[tuple[str, str], dict[str, Any]]:
    ranks: dict[tuple[str, str], dict[str, Any]] = {}
    obj = re.compile(r"(?:const|let|var)\s+(" + IDENT + r")\s*=\s*(?:Object\.freeze\(\s*)?\{([^{}]*)\}")
    arr = re.compile(r"(?:const|let|var)\s+(" + IDENT + r")\s*=\s*(?:Object\.freeze\(\s*)?\[([^\[\]]*)\]")
    for f in files:
        for m in obj.finditer(f.kept):
            entries = [e.strip() for e in m.group(2).split(",") if e.strip()]
            order, names = {}, {}
            for e in entries:
                em = re.fullmatch(r"(['\"]?)([A-Za-z][\w-]*)\1\s*:\s*(-?\d+)", e)
                if not em:
                    order = {}
                    break
                order[em.group(2).lower()] = int(em.group(3))
                names[em.group(2).lower()] = em.group(2)
            if len(order) >= 3 and len(set(order) & requirement_words) >= 3:
                ranks[(f.rel, m.group(1))] = {"order": order, "names": names, "line": f.line(m.start()),
                                              "kind": "object", "text": f.line_text(m.start())[:160], "file": f}
        for m in arr.finditer(f.kept):
            items = [e.strip() for e in m.group(2).split(",") if e.strip()]
            vals = [re.fullmatch(r"(['\"])([A-Za-z][\w-]*)\1", e) for e in items]
            if len(vals) < 3 or not all(vals):
                continue
            order = {v.group(2).lower(): i for i, v in enumerate(vals)}
            if len(order) == len(vals) and len(set(order) & requirement_words) >= 3:
                ranks[(f.rel, m.group(1))] = {"order": order, "names": {v.group(2).lower(): v.group(2) for v in vals},
                                              "line": f.line(m.start()), "kind": "array",
                                              "text": f.line_text(m.start())[:160], "file": f}
    return ranks

def visible_ranks(f: JsFile, ranks: dict, by_path: dict[str, JsFile]) -> dict[str, dict[str, Any]]:
    out = {var: info for (rel, var), info in ranks.items() if rel == f.rel}
    for m in re.finditer(r"(?:const|let|var)\s*\{([^{}]*)\}\s*=\s*require\(\s*(['\"])([^'\"]+)\2\s*\)"
                         r"|import\s*\{([^{}]*)\}\s*from\s*(['\"])([^'\"]+)\5", f.kept):
        inner, spec = (m.group(1), m.group(3)) if m.group(1) is not None else (m.group(4), m.group(6))
        target = resolve_module(spec, f.path, by_path)
        if not target:
            continue
        for part in inner.split(","):
            pm = re.fullmatch(r"\s*(" + IDENT + r")(?:\s*(?::|\bas\b)\s*(" + IDENT + r"))?\s*", part)
            if pm and (target.rel, pm.group(1)) in ranks:
                out[pm.group(2) or pm.group(1)] = ranks[(target.rel, pm.group(1))]
    return out

def _unwrap(t: str) -> str:
    t = t.strip()
    while t.startswith("(") and match_close(t, 0) == len(t) - 1:
        t = t[1:-1].strip()
    return t

def _operand(text: str, var: str, kind: str) -> tuple[str, str] | None:
    t = re.sub(r"^(?:return|await|typeof)\s+", "", text.strip())
    t = _unwrap(t)
    t = re.sub(r"\s*(?:\|\||\?\?)\s*-?\d+\s*$", "", t).strip()
    t = _unwrap(t)
    if re.fullmatch(r"-?\d+", t):
        return ("num", t)
    v = re.escape(var)
    if kind == "object":
        m = re.fullmatch(v + r"\s*\.\s*(" + IDENT + r")", t)
        if m:
            return ("const", m.group(1))
        m = re.fullmatch(v + r"\s*\[\s*(['\"])([A-Za-z][\w-]*)\1\s*\]", t)
        if m:
            return ("const", m.group(2))
        m = re.fullmatch(v + r"\s*\[\s*(.+?)\s*\]", t)
        if m:
            return ("expr", m.group(1))
    else:
        m = re.fullmatch(v + r"\s*\.\s*indexOf\(\s*(['\"])([A-Za-z][\w-]*)\1\s*\)", t)
        if m:
            return ("const", m.group(2))
        m = re.fullmatch(v + r"\s*\.\s*indexOf\(\s*(.+?)\s*\)", t)
        if m:
            return ("expr", m.group(1))
    return None

def _ident_of(expr: str) -> str | None:
    e = expr.strip()
    for _ in range(3):
        m = re.fullmatch(r"(?:" + IDENT + r"\s*\(\s*)(.+?)\s*\)(?:\s*\.\s*(?:toLowerCase|toUpperCase|trim)\(\s*\))*", e)
        if m:
            e = m.group(1).strip()
            continue
        m = re.fullmatch(r"(" + IDENT + r")(?:\s*\.\s*(?:toLowerCase|toUpperCase|trim)\(\s*\))+", e)
        if m:
            e = m.group(1)
            continue
        break
    return e if re.fullmatch(IDENT, e) else None

OPS = {">=": ">=", ">": ">", "<=": "<=", "<": "<"}
FLIP = {">=": "<=", "<=": ">=", ">": "<", "<": ">"}

def _side_spans(masked: str, op_start: int, op_end: int) -> tuple[int, int, int, int]:
    stops = re.compile(r"&&|\|\||[?:,;]|(?<![=!<>])=(?!=)")
    depth, i = 0, op_start - 1
    while i >= 0:
        ch = masked[i]
        if ch in ")]}":
            depth += 1
        elif ch in "([{":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and (stops.match(masked, i) or masked[i - 1:i + 1] in ("&&", "||", "=>") if i > 0 else stops.match(masked, i)):
            break
        elif depth == 0 and ch == "!" and masked[i + 1:i + 2] != "=":
            break
        i -= 1
    left = (i + 1, op_start)
    depth, j = 0, op_end
    while j < len(masked):
        ch = masked[j]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and stops.match(masked, j):
            break
        j += 1
    return left[0], left[1], op_end, j

def _deny_context(f: JsFile, pos: int) -> bool:
    ifs = list(re.finditer(r"\bif\s*\(", f.masked[max(0, pos - 300):pos]))
    if not ifs:
        return False
    paren = max(0, pos - 300) + ifs[-1].end() - 1
    if paren < 0 or paren > pos:
        return False
    close = match_close(f.masked, paren)
    if close < pos:
        return False
    tail = f.kept[close + 1:close + 220]
    return bool(re.match(r"\s*\{?\s*(?:return\s+)?(?:res\s*\.\s*status\s*\(\s*4\d\d|false\b|null\b|next\s*\(\s*\w*\s*\(?\s*4\d\d|"
                         r"throw\b|res\s*\.\s*sendStatus\s*\(\s*4\d\d)", tail))

def threshold_sites(files: list[JsFile], ranks: dict, log) -> list[dict[str, Any]]:
    by_path = {os.path.normcase(str(f.path.resolve())): f for f in files}
    sites: list[dict[str, Any]] = []
    param_rules: list[dict[str, Any]] = []
    for f in files:
        visible = visible_ranks(f, ranks, by_path)
        if not visible:
            continue
        for m in re.finditer(r"(?<![=<>!])(>=|<=|>|<)(?![=>])", f.masked):
            op = m.group(1)
            if f.masked[m.start() - 1:m.start()] == "=" or f.masked[m.end():m.end() + 1] in ("<", ">"):
                continue
            ls, le, rs, re_ = _side_spans(f.masked, m.start(), m.end())
            left_txt, right_txt = f.kept[ls:le], f.kept[rs:re_]
            for var, info in visible.items():
                if var not in left_txt and var not in right_txt:
                    continue
                lo = _operand(left_txt, var, info["kind"])
                ro = _operand(right_txt, var, info["kind"])
                if lo is None and ro is None:
                    continue
                if lo is None or ro is None:

                    other = right_txt if lo else left_txt
                    if not re.fullmatch(r"\s*\(?\s*-?\d+\s*\)?\s*", other):
                        continue
                    num = ("num", re.sub(r"[()\s]", "", other))
                    lo, ro = (lo, num) if lo else (num, ro)
                order = info["order"]

                def const_name(o):
                    if o[0] == "const":
                        return o[1].lower() if o[1].lower() in order else None
                    if o[0] == "num":
                        hits = [k for k, v in order.items() if v == int(o[1])]
                        return hits[0] if len(hits) == 1 else None
                    return None

                if info["kind"] == "array" and "num" in (lo[0], ro[0]):
                    continue
                lc, rc = const_name(lo), const_name(ro)
                if (lo[0] in ("const", "num") and lc is None) or (ro[0] in ("const", "num") and rc is None):
                    continue
                if lc and rc:
                    continue
                if rc or lc:
                    subject, thr, rule = (lo, rc, op) if rc else (ro, lc, FLIP[op])
                    if subject[0] != "expr":
                        continue
                    allowed_rule = _allowed_rule(rule, f, m.start())
                    if allowed_rule:
                        sites.append({"file": f, "pos": m.start(), "rank": info, "threshold": thr, "op": allowed_rule,
                                      "call": None, "compare": f.line_text(m.start())[:160], "var": var})
                    continue

                fns = enclosing(f.functions, m.start())
                if not fns:
                    continue
                fn = fns[0]
                li, ri = _ident_of(lo[1]), _ident_of(ro[1])
                params = fn["params"]
                lp = li in params if li else False
                rp = ri in params if ri else False
                if not (lp or rp) or not fn["name"]:
                    continue
                param_rules.append({"file": f, "pos": m.start(), "rank": info, "fn": fn["name"], "params": params,
                                    "left": li if lp else None, "right": ri if rp else None, "op": op,
                                    "compare": f.line_text(m.start())[:160], "var": var})

    for rule in param_rules:
        definers = [g for g in files if any(fn["name"] == rule["fn"] for fn in g.functions)]
        if len(definers) != 1:
            log(f"[{CHECK}] (diagnostic) {rule['fn']} defined in {len(definers)} files: skipped")
            continue
        call_re = re.compile(r"(?<![\w$.])" + re.escape(rule["fn"]) + r"\s*\(")
        for g in files:
            for cm in call_re.finditer(g.masked):
                before = g.masked[max(0, cm.start() - 30):cm.start()]
                if re.search(r"function\s*\*?\s*$", before):
                    continue
                open_pos = cm.end() - 1
                close = match_close(g.masked, open_pos)
                if close < 0:
                    continue
                spans = split_top(g.kept, open_pos + 1, close, g.masked)
                if len(spans) != len(rule["params"]):
                    continue
                args = [g.kept[a:b].strip() for a, b in spans]
                lits = {}
                for idx, pname in enumerate(rule["params"]):
                    lm = re.fullmatch(r"(['\"`])([A-Za-z][\w-]*)\1", args[idx])
                    if pname and lm:
                        lits[pname] = lm.group(2).lower()
                order = rule["rank"]["order"]
                lthr = lits.get(rule["left"]) if rule["left"] else None
                rthr = lits.get(rule["right"]) if rule["right"] else None
                if bool(lthr) == bool(rthr):
                    continue
                thr = rthr or lthr
                if thr not in order:
                    continue
                op = rule["op"] if rthr else FLIP[rule["op"]]
                allowed_rule = _allowed_rule(op, rule["file"], rule["pos"])
                if allowed_rule:
                    sites.append({"file": g, "pos": cm.start(), "rank": rule["rank"], "threshold": thr, "op": allowed_rule,
                                  "call": g.line_text(cm.start())[:160], "compare": rule["compare"],
                                  "compare_at": f"{rule['file'].rel}:{rule['file'].line(rule['pos'])}", "var": rule["var"]})
    return sites

def _allowed_rule(op: str, f: JsFile, pos: int) -> str | None:
    if op in (">=", ">"):
        return op
    if not _deny_context(f, pos):
        return None
    return ">=" if op == "<" else ">"

def allowed_set(order: dict[str, int], threshold: str, rule: str) -> set[str]:
    t = order[threshold]
    return {r for r, v in order.items() if (v >= t if rule == ">=" else v > t)}

GENERIC_SEGMENTS = {"api", "repos", "repo", "repositories", "repository", "orgs", "org", "organizations", "organization",
                    "users", "user", "issues", "issue", "pulls", "pull", "options", "search", "list", "all", "new",
                    "edit", "detail", "details", "id", "items", "item", "me", "v1", "v2", "data", "admin", "public",
                    "private", "status", "state", "info", "settings", "candidates", "available", "count", "page",
                    "pages", "projects", "project", "workspaces", "workspace", "teams", "team", "members", "member"}

def _stem(segment: str) -> str | None:
    word = re.split(r"[-_]", segment.lower())[-1]
    if not word.isalpha():
        return None
    if word in GENERIC_SEGMENTS:
        return None
    for suffix in ("ees", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            word = word[: -len(suffix)] if suffix != "ees" else word[:-3]
            break
    if word.endswith("ee") and len(word) > 5:
        word = word[:-2]
    return word if len(word) >= 4 else None

def action_stem(path: str) -> str | None:
    segments = [s for s in path.split("/") if s and not s.startswith(":") and "*" not in s and "(" not in s]
    for seg in reversed(segments):
        stem = _stem(seg)
        if stem:
            return stem
        if seg.lower() not in GENERIC_SEGMENTS:
            return None
    return None

VIEW_VERBS = {"view", "views", "see", "sees", "list", "lists", "open", "opens", "browse", "browses", "read", "reads",
              "access", "display", "displays", "visit", "visits", "search", "inspect", "download", "export", "watch",
              "show", "shows", "find", "look"}
CHANGE_VERBS = {"create", "add", "assign", "unassign", "edit", "change", "update", "remove", "delete", "close", "reopen",
                "merge", "apply", "set", "submit", "grant", "revoke", "comment", "request", "transition", "mark", "rename",
                "move", "upload", "invite", "archive", "approve", "reject", "dismiss", "publish", "lock", "unlock",
                "transfer", "rerun", "enable", "disable", "save", "post", "write", "manage", "convert", "resolve", "pin",
                "unpin", "label", "unlabel", "fork", "push", "commit", "configure", "restore", "accept", "decline"}
READ_METHODS = {"get"}
CHANGE_METHODS = {"post", "put", "patch", "delete"}

def action_kind(action: str) -> str | None:
    words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z-]*", action)}
    view, change = bool(words & VIEW_VERBS), bool(words & CHANGE_VERBS)
    if change and not view:
        return "change"
    if view and not change:
        return "view"
    return None

AUTH_ONLY = re.compile(r"(?i)^(?:require|ensure|need)s?(?:Auth\w*|Login|Signed\w*|User|Session|SignIn)$|^(?:auth\w*|authenticate\w*)$")
NULL_TERM = re.compile(r"^!?\s*\(?\s*(?:req\s*\.\s*)?(?:user|role|session|currentUser|account|me)(?:\s*\.\s*\w+)?\s*\)?"
                       r"(?:\s*(?:===?|!==?)\s*(?:null|undefined))?$")

def _condition_of(f: JsFile, pos: int) -> tuple[int, int] | None:
    window_start = max(0, pos - 300)
    for m in reversed(list(re.finditer(r"\bif\s*\(", f.masked[window_start:pos]))):
        paren = window_start + m.end() - 1
        close = match_close(f.masked, paren)
        if close > pos:
            return paren, close
    return None

def sole_guard(f: JsFile, route: dict[str, Any], site: dict[str, Any]) -> str | None:
    pos = site["pos"]
    cond = _condition_of(f, pos)
    if not cond or not _deny_context(f, pos):
        return "the comparison is not a deny condition"
    terms = []
    depth, start = 0, cond[0] + 1
    text = f.masked
    i = start
    while i < cond[1]:
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and text[i:i + 2] in ("&&", "||"):
            terms.append((start, i))
            start = i + 2
            i += 1
        i += 1
    terms.append((start, cond[1]))
    for a, b in terms:
        if a <= pos < b:
            continue
        if not NULL_TERM.match(f.kept[a:b].strip()):
            return f"compound condition `{f.kept[a:b].strip()[:60]}`"
    open_pos, close = route["args"]
    spans = split_top(f.kept, open_pos + 1, close, f.masked)[1:]
    for a, b in spans:
        if a <= pos <= b:
            continue
        arg = f.masked[a:b].strip()
        if re.fullmatch(IDENT, arg):
            if arg in route["handler_names"] and any(fn["name"] == arg and fn["start"] <= pos <= fn["end"] for fn in f.functions):
                continue
            if AUTH_ONLY.match(arg):
                continue
        return f"another middleware `{f.kept[a:b].strip()[:60]}`"

    body = None
    if open_pos < pos < close:
        body = (open_pos, close)
    else:
        for fn in enclosing(f.functions, pos):
            if fn["name"] in route["handler_names"]:
                body = (fn["start"], fn["end"])
        if body is None:
            return "handler not found"
    kept = f.kept
    for m in re.finditer(r"status\s*\(\s*403|sendStatus\s*\(\s*403|\.includes\s*\(|\brole\w*\s*[!=]==?|"
                         r"(?<![\w$.])(?:is|can|has|require|ensure|assert|check)[A-Z]\w*\s*\(|"
                         + re.escape(site["var"]) + r"\b", kept[body[0]:body[1]]):
        at = body[0] + m.start()
        if cond[0] <= at <= cond[1] + 260 and at < _deny_end(f, cond[1]):
            continue
        token = m.group(0)
        if token.startswith(".includes"):
            line = f.line_text(at)
            if not re.search(r"(?i)role|permission|grant", line):
                continue
        if re.match(r"(?:require|ensure)", token) and AUTH_ONLY.match(token.rstrip("( ")):
            continue
        return f"another guard at {f.rel}:{f.line(at)} `{f.line_text(at)[:80]}`"
    return None

def _deny_end(f: JsFile, close: int) -> int:
    m = re.match(r"\s*\{", f.masked[close + 1:])
    if m:
        end = match_close(f.masked, close + m.end())
        return end if end > 0 else close
    semi = f.masked.find(";", close)
    return semi if semi > 0 else close

def role_ladder_findings(requirement: str, project_dir: Path, log=None) -> list[dict[str, str]]:
    log = log or (lambda message: None)
    try:
        return _findings(requirement, Path(project_dir), log)
    except Exception as exc:
        log(f"[{CHECK}] (diagnostic) crashed: {type(exc).__name__}: {exc}")
        return []

def _findings(requirement: str, project_dir: Path, log) -> list[dict[str, str]]:
    sentences = requirement_sentences(requirement)
    gate = next((s for s in sentences if NOT_LADDER.search(s)), None)
    files = backend_files(project_dir)
    if not files:
        return []
    capitalised = set()
    for s in sentences:
        capitalised.update(w.lower() for w in re.findall(r"\b[A-Z][a-z]+\b", s))
    ranks = find_ranks(files, capitalised)
    if not ranks:
        return []
    vocab: dict[str, str] = {}
    for info in ranks.values():
        for low in info["order"]:
            if low in capitalised:
                vocab[low] = low.capitalize()

    lists = sorted(role_lists(sentences, vocab), key=lambda item: not item["only"])
    if gate is None and not lists:
        return []
    sites = threshold_sites(files, ranks, log)
    if not sites:
        return []
    findings: dict[tuple, dict[str, Any]] = {}
    linked_any = False
    for site in sites:
        f, order = site["file"], site["rank"]["order"]
        code_allowed = allowed_set(order, site["threshold"], site["op"]) & set(vocab)
        at = f"{f.rel}:{f.line(site['pos'])}"
        for route in routes_at(f, site["pos"]):
            stem = action_stem(route["path"])
            if not stem:
                continue
            for item in lists:
                if not re.search(r"(?i)\b" + re.escape(stem), item["action"]):
                    continue
                if not (item["only"] or gate):
                    continue
                kind = action_kind(item["action"])
                method = route["method"]
                if kind is None or (kind == "view") != (method in READ_METHODS) or method not in READ_METHODS | CHANGE_METHODS:
                    log(f"[{CHECK}] (diagnostic) {at} {method.upper()} {route['path']}: the list's action "
                        f"({kind or 'unknown kind'}) is not this method's: not linked")
                    continue
                stated = set(item["roles"]) & set(order)
                extra = code_allowed - stated
                if site["threshold"] not in stated or not extra:
                    continue
                why = sole_guard(f, route, site)
                if why:
                    log(f"[{CHECK}] (diagnostic) {at} {method.upper()} {route['path']}: {why}: not reported")
                    continue
                linked_any = True
                key = (site["rank"]["file"].rel, site["rank"]["line"], site["threshold"], site["op"], item["roles"])
                entry = findings.setdefault(key, {"site": site, "item": item, "extra": extra, "where": [], "routes": []})
                if at not in entry["where"]:
                    entry["where"].append(at)
                sig = f"{method.upper()} {route['path']}"
                if sig not in entry["routes"]:
                    entry["routes"].append(sig)
    out: list[dict[str, str]] = []
    for entry in findings.values():
        site, item = entry["site"], entry["item"]
        rank, order = site["rank"], site["rank"]["order"]
        names = rank["names"]
        extra = sorted(entry["extra"], key=lambda r: order[r])
        thr = names.get(site["threshold"], site["threshold"])
        ranking = ", ".join(f"{names[r]} {order[r]}" for r in sorted(order, key=lambda r: order[r]))
        stated = ", ".join(vocab.get(r, r) for r in sorted(item["roles"], key=lambda r: order.get(r, 99)))
        allowed_js = ", ".join("'" + names.get(r, r) + "'" for r in sorted(item["roles"], key=lambda r: order.get(r, 99)))
        check_txt = (f"`{site['call']}` (whose comparison {site.get('compare_at', '')} is `{site['compare']}`)"
                     if site["call"] else f"`{site['compare']}`")
        out.append(_finding(
            f"{CHECK}: {entry['where'][0]}",
            item["sentence"][:300],
            f"{check_txt} is the only role check of {', '.join(entry['routes'][:3])} and authorises by rank: "
            f"{rank['file'].rel}:{rank['line']} ranks {ranking}, so everyone at or above {thr} passes -- also "
            f"{' and '.join(vocab.get(r, r) for r in extra)}, which the requirement does not give this action "
            f"(it names only {stated}). Sites: {', '.join(entry['where'][:6])}.",
            "For THIS route, check the requirement's own list for this action instead of a rank comparison: an "
            f"explicit set such as `[{allowed_js}].includes(role)` (roles are not a ladder). Change only the routes "
            "named here; other actions keep their own stated lists, and the server answers 403 to the roles left out.",
        ))
        if len(out) >= MAX_FINDINGS:
            break
    if not out and gate:

        for site in sorted(sites, key=lambda s: (s["file"].rel, s["pos"]))[:3]:
            log(f"[{CHECK}] (diagnostic) rank threshold {site['threshold']} at {site['file'].rel}:"
                f"{site['file'].line(site['pos'])} not linked to a stated action: not reported")
    return out
