from __future__ import annotations

import bisect
import os
import re
from pathlib import Path
from typing import Callable, Iterable

MAX_FINDINGS_PER_CHECK = 8
SKIP_DIRS = {"node_modules", "dist", "build", "test-e2e", "tests", "test", "__tests__", "coverage", ".git", "e2e-support", "e2e"}
BACKEND_SUFFIXES = {".js", ".cjs", ".mjs", ".ts", ".sql"}
OPAQUE = "__OPAQUE__"

Source = tuple[str, str]

def _finding(where: str, requirement: str, problem: str, fix: str) -> dict[str, str]:
    return {"where": where, "requirement": requirement, "problem": problem, "fix": fix}

def _skipped(rel_parts: Iterable[str], name: str) -> bool:
    if any(part in SKIP_DIRS for part in rel_parts):
        return True
    return ".spec." in name or ".test." in name

def backend_sources(project_dir: Path, limit: int = 4_000_000) -> list[Source]:
    base = Path(project_dir) / "backend"
    out: list[Source] = []
    total = 0
    if not base.is_dir():
        return out
    paths: list[Path] = []
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if Path(name).suffix.lower() in BACKEND_SUFFIXES and not _skipped((), name):
                paths.append(Path(root) / name)
    for path in paths:
        try:
            rel = path.relative_to(project_dir)
        except ValueError:
            continue
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > 1_500_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.append((rel.as_posix(), text))
        total += len(text)
        if total > limit:
            break
    return out

_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_WORDS = {"return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "else", "do", "yield", "await"}

class Lexed:

    def __init__(self, text: str) -> None:
        self.text = text
        self.code, self.strings = _lex(text)
        self._newlines = [i for i, c in enumerate(text) if c == "\n"]
        self.string_at = {s[0]: s for s in self.strings}

    def line(self, pos: int) -> int:
        return bisect.bisect_left(self._newlines, pos) + 1

def _regex_allowed(out: list[str], i: int) -> bool:
    k = i - 1
    while k >= 0 and out[k] in " \t\r\n":
        k -= 1
    if k < 0:
        return True
    if out[k] in _REGEX_PREV:
        return True
    if out[k].isalnum() or out[k] in "_$":
        j = k
        while j >= 0 and (out[j].isalnum() or out[j] in "_$"):
            j -= 1
        return "".join(out[j + 1: k + 1]) in _REGEX_WORDS
    return False

def _lex(text: str) -> tuple[str, list[tuple[int, int, str, str]]]:
    n = len(text)
    out = list(text)
    strings: list[tuple[int, int, str, str]] = []

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    stack: list[list] = []
    i = 0
    while i < n:
        top = stack[-1] if stack else None
        c = text[i]
        if top is not None and top[0] == "tpl":
            if c == "\\":
                i += 2
                continue
            if c == "`":
                top[2].append(text[top[3]: i])
                strings.append((top[1], i + 1, "`", "".join(top[2])))
                stack.pop()
                if not stack:
                    blank(top[1] + 1, i)
                i += 1
                continue
            if text.startswith("${", i):
                top[2].append(text[top[3]: i])
                top[2].append(" " + OPAQUE + " ")
                stack.append(["expr", 0])
                i += 2
                continue
            i += 1
            continue
        if top is not None and top[0] == "expr":
            if c == "{":
                top[1] += 1
            elif c == "}":
                if top[1] == 0:
                    stack.pop()
                    stack[-1][3] = i + 1
                    i += 1
                    continue
                top[1] -= 1
        if c in "'\"":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            end = min(j + 1, n)
            strings.append((i, end, c, text[i + 1: min(j, n)]))
            if not stack:
                blank(i + 1, j)
            i = end
            continue
        if c == "`":
            stack.append(["tpl", i, [], i + 1])
            i += 1
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            if not stack:
                blank(i, j)
            i = j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            if not stack:
                blank(i, j)
            i = j
            continue
        if c == "/" and _regex_allowed(out, i):
            j, in_class = i + 1, False
            while j < n and text[j] != "\n":
                ch = text[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    break
                j += 1
            if j < n and text[j] == "/":
                if not stack:
                    blank(i + 1, j)
                i = j + 1
                continue
        i += 1
    if stack:
        start = next((e[1] for e in stack if e[0] == "tpl"), None)
        if start is not None:
            blank(start + 1, n)
    return "".join(out), strings

def _match_close(code: str, open_pos: int) -> int:
    pairs = {"(": ")", "[": "]", "{": "}"}
    opener = code[open_pos]
    closer = pairs[opener]
    depth = 0
    for k in range(open_pos, len(code)):
        ch = code[k]
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return k
    return -1

def _enclosing_open(code: str, pos: int) -> int:
    depth = 0
    for k in range(pos - 1, -1, -1):
        ch = code[k]
        if ch in ")]}":
            depth += 1
        elif ch in "([{":
            if depth == 0:
                return k
            depth -= 1
    return -1

_OPERATORS = ["===", "!==", "&&", "||", "??", "?.", "==", "!=", "<=", ">=", "=>", "+=", "-=", "++", "--"]

def _prev_token(code: str, pos: int) -> tuple[str, int]:
    k = pos - 1
    while k >= 0 and code[k] in " \t\r\n":
        k -= 1
    if k < 0:
        return "", -1
    if code[k].isalnum() or code[k] in "_$":
        j = k
        while j >= 0 and (code[j].isalnum() or code[j] in "_$"):
            j -= 1
        return code[j + 1: k + 1], j + 1
    for op in _OPERATORS:
        if code[max(0, k - len(op) + 1): k + 1] == op:
            return op, k - len(op) + 1
    return code[k], k

def _next_token(code: str, pos: int) -> tuple[str, int]:
    k = pos
    while k < len(code) and code[k] in " \t\r\n":
        k += 1
    if k >= len(code):
        return "", len(code)
    if code[k].isalnum() or code[k] in "_$":
        j = k
        while j < len(code) and (code[j].isalnum() or code[j] in "_$"):
            j += 1
        return code[k:j], k
    for op in _OPERATORS:
        if code.startswith(op, k):
            return op, k
    return code[k], k

_SQL_START = re.compile(r"^\s*\(?\s*(SELECT|INSERT|REPLACE|UPDATE|DELETE|CREATE|ALTER|WITH|DROP|PRAGMA)\b", re.I)
IDENT = r'(?:"[^"\n]+"|`[^`\n]+`|\[[^\]\n]+\]|[A-Za-z_][\w$]*)'
SQL_KEYWORDS = set("""
abort action add after all alter always analyze and as asc attach autoincrement before begin between by cascade case cast
check collate column commit conflict constraint create cross current current_date current_time current_timestamp database
default deferrable deferred delete desc detach distinct do drop each else end escape except exclude exclusive exists explain
fail filter first following for foreign from full generated glob group groups having if ignore immediate in index indexed
initially inner insert instead intersect into is isnull join key last left like limit match materialized natural no not
nothing notnull null nulls of offset on or order others outer over partition plan pragma preceding primary query raise
range recursive references regexp reindex release rename replace restrict returning right rollback row rows savepoint
select set table temp temporary then ties to transaction trigger true false unbounded union unique update using vacuum
values view virtual when where window with without new old excluded
""".split())
ALWAYS_COLUMNS = {"rowid", "oid", "_rowid_"}

def _unquote(name: str) -> str:
    name = name.strip()
    if len(name) >= 2 and ((name[0] == name[-1] and name[0] in "\"`") or (name[0] == "[" and name[-1] == "]")):
        name = name[1:-1]
    return name.strip().lower()

def _js_unescape(content: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": " ", "r": " "}.get(m.group(1), m.group(1)), content)

def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)

def _split_sql(sql: str) -> list[tuple[int, str]]:
    out, start, quote = [], 0, ""
    for k, ch in enumerate(sql):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
        elif ch == ";":
            out.append((start, sql[start:k]))
            start = k + 1
    out.append((start, sql[start:]))
    return [(o, s) for o, s in out if s.strip()]

def _mask_sql_strings(sql: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", sql)

def _split_top(text: str, sep: str = ",") -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts

def _top_level_keyword(text: str, pattern: str, start: int = 0) -> re.Match | None:
    depth_at = []
    depth = 0
    for ch in text:
        depth_at.append(depth)
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
    for m in re.finditer(pattern, text[start:], re.I):
        if depth_at[start + m.start()] == 0:
            return m
    return None

def sql_literals(lexed: Lexed) -> list[tuple[int, str, bool]]:
    out = []
    code = lexed.code
    for start, end, quote, content in lexed.strings:
        sql = _js_unescape(content) if quote != "`" else content.replace("\\`", "`")
        if not _SQL_START.match(sql):
            continue
        standalone = True
        prev, prev_at = _prev_token(code, start)
        nxt, _ = _next_token(code, end)
        if prev in {"+", "+="} or nxt in {"+", ".", "["} or nxt.startswith("+"):
            standalone = False
        if prev == "[":
            standalone = False
        if prev == ",":
            opener = _enclosing_open(code, start)
            if opener >= 0 and code[opener] == "[":
                standalone = False
        if standalone and prev == "=":
            name, _ = _prev_token(code, prev_at)
            if re.fullmatch(r"[A-Za-z_$][\w$]*", name or ""):
                n = re.escape(name)
                uses = re.compile(r"(?<![\w$.])" + n + r"\s*(?:\+|\.\s*(?:concat|replace|replaceAll)\b)|\+\s*" + n
                                  + r"(?![\w$])|\$\{\s*" + n + r"\s*\}")
                if uses.search(code) or any(re.search(r"\$\{\s*" + n + r"\s*\}", s[3].replace(OPAQUE, "")) for s in lexed.strings):
                    standalone = False

                if re.search(r"\$\{\s*" + n + r"\s*\}", lexed.text):
                    standalone = False
        out.append((start, sql, standalone))
    return out

class Schema:
    def __init__(self) -> None:
        self.tables: dict[str, set[str]] = {}
        self.skip: set[str] = set()
        self.anywhere: set[str] = set()
        self.dynamic_both = False
        self.off = False
        self.notes: list[str] = []

    def columns(self, table: str) -> set[str] | None:
        if table in self.skip or table not in self.tables:
            return None
        return self.tables[table]

_CREATE = re.compile(r"\bCREATE\s+(?:(TEMP|TEMPORARY)\s+)?(VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(" + IDENT
                     + r"(?:\." + IDENT + r")?|" + OPAQUE + r")?", re.I)
_CREATE_VIEW = re.compile(r"\bCREATE\s+(?:TEMP\w*\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?(" + IDENT + r")", re.I)
_ALTER = re.compile(r"\bALTER\s+TABLE\s+(" + IDENT + r"|" + OPAQUE + r")?\s*(.*)", re.I | re.S)
_CONSTRAINT_WORDS = {"constraint", "primary", "unique", "check", "foreign"}

def _schema_from_sql(sql: str, schema: Schema) -> None:
    sql = _strip_sql_comments(sql)
    for m in _CREATE_VIEW.finditer(sql):
        schema.skip.add(_unquote(m.group(1)))
    for m in _CREATE.finditer(sql):
        raw = m.group(3)
        if raw is None or raw == OPAQUE:
            rest = sql[m.end(): m.end() + 2000]
            body = rest[rest.find("("):] if "(" in rest else ""
            close = _match_close(body, 0) if body else -1
            if close < 0 or OPAQUE in body[:close]:
                schema.off = True
                schema.notes.append("CREATE TABLE with a run-time name and unknown columns: the check is off")
            else:
                for col in _column_defs(body[1:close]):
                    schema.anywhere.add(col)
            continue
        name = raw.split(".")[-1] if "." in raw and not raw.startswith(("\"", "`", "[")) else raw
        table = _unquote(name)
        if m.group(2):
            schema.skip.add(table)
            continue
        rest = sql[m.end():]
        stripped = rest.lstrip()
        if not stripped.startswith("("):
            schema.skip.add(table)
            continue
        open_at = len(rest) - len(stripped)
        close = _match_close(rest, open_at)
        if close < 0 or OPAQUE in rest[open_at:close]:
            schema.skip.add(table)
            continue
        cols = _column_defs(rest[open_at + 1: close])
        if not cols:
            schema.skip.add(table)
            continue
        schema.tables.setdefault(table, set()).update(cols)
    for m in _ALTER.finditer(sql):
        raw_table, tail = m.group(1), m.group(2)
        table = None if raw_table in (None, OPAQUE) else _unquote(raw_table)
        rename = re.match(r"\s*RENAME\s+(?:COLUMN\s+)?(" + IDENT + r"|" + OPAQUE + r")?\s*(?:TO\s+(" + IDENT + r"|" + OPAQUE + r"))?",
                          tail, re.I)
        add = re.match(r"\s*ADD\s+(?:COLUMN\s+)?(" + IDENT + r"|" + OPAQUE + r")?", tail, re.I)
        if add and not re.match(r"\s*ADD\s+(?:CONSTRAINT|PRIMARY|UNIQUE|CHECK|FOREIGN)\b", tail, re.I):
            col_raw = add.group(1)
            col = None if col_raw in (None, OPAQUE) else _unquote(col_raw)
            if table and col:
                schema.tables.setdefault(table, set()).add(col)
            elif table:
                schema.skip.add(table)
                schema.notes.append(f"ALTER TABLE {table} ADD COLUMN with a run-time column name: {table} skipped")
            elif col:
                schema.anywhere.add(col)
            else:
                schema.dynamic_both = True
                schema.notes.append("ALTER TABLE <run-time> ADD COLUMN <run-time>: columns named in string literals are trusted")
        elif rename:
            if table:
                schema.skip.add(table)
            new = rename.group(2)
            if new and new != OPAQUE:
                schema.skip.add(_unquote(new))
            if not table:
                schema.dynamic_both = True
        elif table is None and not tail.strip():
            schema.dynamic_both = True

def _column_defs(body: str) -> list[str]:
    cols = []
    for item in _split_top(body):
        item = item.strip()
        if not item:
            continue
        m = re.match(IDENT, item)
        if not m:
            return []
        word = _unquote(m.group(0))
        if word in _CONSTRAINT_WORDS and not m.group(0)[0] in "\"`[":
            continue
        cols.append(word)
    return cols

def build_schema(sources: list[Source]) -> Schema:
    schema = Schema()
    for rel, text in sources:
        if rel.lower().endswith(".sql"):
            _schema_from_sql(text, schema)
            continue
        lexed = Lexed(text)
        for _start, _end, quote, content in lexed.strings:
            if re.search(r"\b(CREATE|ALTER)\s", content, re.I):
                sql = _js_unescape(content) if quote != "`" else content
                if re.search(r"\b(?:CREATE\s+(?:TEMP\w*\s+)?(?:VIRTUAL\s+)?(?:TABLE|VIEW)|ALTER\s+TABLE)\b", sql, re.I):
                    _schema_from_sql(sql, schema)
    for table in schema.skip:
        schema.tables.pop(table, None)
    return schema

def _literal_words(sources: list[Source]) -> set[str]:
    words: set[str] = set()
    for rel, text in sources:
        if rel.lower().endswith(".sql"):
            continue
        for _s, _e, _q, content in Lexed(text).strings:
            if _SQL_START.match(content):
                continue
            m = re.match(r"\s*([A-Za-z_]\w*)", content)
            if m:
                words.add(m.group(1).lower())
    return words

_VALUE_PREV = {"=", "==", "<", ">", "<=", ">=", "!=", "<>", "like", "glob", "limit", "offset", "is", "values", "in",
               "between", "and", "then", "else", "when", "+", "-", "*", "/", "||"}

def _opaque_only_values(sql: str) -> bool:
    for m in re.finditer(OPAQUE, sql):
        before = sql[: m.start()].rstrip()
        tok = re.search(r"(<>|<=|>=|!=|==|\|\||[=<>(,+\-*/]|[A-Za-z_]+)\s*$", before)
        if not tok:
            return False
        word = tok.group(1).lower()
        if word in _VALUE_PREV:
            if word == "and" and not re.search(r"\bBETWEEN\b[^()]*$", before, re.I):
                return False
            continue
        if word in {"(", ","}:
            opener = _enclosing_open(before + " ", len(before) + 1)
            head = before[:opener].rstrip() if opener >= 0 else ""
            if opener >= 0 and re.search(r"(\bVALUES|\bIN|,|\bVALUES\s*\([^()]*\)\s*,)$", head, re.I):
                if head.endswith(",") and not re.search(r"\bVALUES\b", head, re.I):
                    return False
                continue
            return False
        return False
    return True

_MULTI = re.compile(r"\b(JOIN|UNION|INTERSECT|EXCEPT)\b|\(\s*SELECT\b|^\s*WITH\b|\bCREATE\s+TRIGGER\b", re.I)

def _identifiers_before_ops(clause: str) -> list[tuple[str | None, str]]:
    tokens = [t for t in re.findall(
        r"''|[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?|\d+(?:\.\d+)?|<>|<=|>=|!=|==|\|\||[$:@?][\w$]*|[^\s\w]", clause)]
    upper = [t.upper() for t in tokens]
    comparison = {"=", "==", "!=", "<>", "<", "<=", ">", ">=", "IS", "IN", "LIKE", "GLOB", "BETWEEN"}
    opening = {None, "(", "AND", "OR", "NOT"}
    found = []
    for k, tok in enumerate(tokens):
        if not re.fullmatch(r"[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?", tok):
            continue
        qual, _, name = tok.rpartition(".")
        if name.lower() in SQL_KEYWORDS or (qual and qual.lower() in SQL_KEYWORDS):
            continue
        before = upper[k - 1] if k > 0 else None
        after = upper[k + 1] if k + 1 < len(tokens) else ""
        if after == "NOT" and k + 2 < len(tokens) and upper[k + 2] in {"IN", "LIKE", "GLOB", "BETWEEN", "NULL"}:
            after = "IN"
        if after not in comparison or before not in opening:
            continue
        if before == "AND" and _between_and(upper, k - 1):
            continue
        if before == "(" and k >= 2 and re.fullmatch(r"[A-Z_][\w$]*", upper[k - 2]) and upper[k - 2] not in {"AND", "OR", "NOT", "WHERE", "IN"}:
            continue
        found.append((qual.lower() or None, name.lower()))
    return found

def _between_and(upper: list[str], and_at: int) -> bool:
    depth = 0
    for j in range(and_at - 1, -1, -1):
        t = upper[j]
        if t == ")":
            depth += 1
        elif t == "(":
            if depth == 0:
                return False
            depth -= 1
        elif depth == 0 and t in {"AND", "OR"}:
            return False
        elif depth == 0 and t == "BETWEEN":
            return True
    return False

def _order_items(clause: str) -> list[tuple[str | None, str]]:
    found = []
    for item in _split_top(clause):
        m = re.fullmatch(r"\s*(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)(?:\s+COLLATE\s+\w+)?(?:\s+(?:ASC|DESC))?"
                         r"(?:\s+NULLS\s+(?:FIRST|LAST))?\s*", item, re.I)
        if m and m.group(2).lower() not in SQL_KEYWORDS:
            found.append((m.group(1).lower() if m.group(1) else None, m.group(2).lower()))
    return found

def _clauses(rest: str) -> dict[str, str]:
    keys = r"\bWHERE\b|\bGROUP\s+BY\b|\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|\bOFFSET\b|\bRETURNING\b|\bWINDOW\b|\bON\s+CONFLICT\b"
    marks = []
    depth = 0
    depth_at = []
    for ch in rest:
        depth_at.append(depth)
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
    for m in re.finditer(keys, rest, re.I):
        if depth_at[m.start()] == 0:
            marks.append((m.start(), m.end(), re.sub(r"\s+", " ", m.group(0).upper())))
    out = {}
    for k, (s, e, key) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(rest)
        out.setdefault(key, rest[e:end])
    return out

def statement_column_uses(sql: str) -> tuple[str, list[str]] | None:
    sql = _strip_sql_comments(sql).strip()
    if _MULTI.search(sql):
        return None
    if OPAQUE in sql and not _opaque_only_values(sql):
        return None
    masked = _mask_sql_strings(sql)
    if re.search(r'"', masked):
        masked = re.sub(r'"[^"]*"', " \"\" ", masked)
    table_pat = r"(" + IDENT + r")"
    m = re.match(r"(?:INSERT|REPLACE)(?:\s+OR\s+\w+)?\s+INTO\s+" + table_pat + r"(?:\s+AS\s+\w+)?\s*\(([^()]*)\)\s*(\w+)",
                 masked, re.I | re.S)
    if m:
        if m.group(3).upper() not in {"VALUES", "DEFAULT"} and OPAQUE not in m.group(3):
            return None
        cols = [_unquote(c) for c in m.group(2).split(",")]
        if any(not re.fullmatch(r"[A-Za-z_][\w$]*", c) for c in cols):
            return None
        return _unquote(m.group(1)), cols
    if re.match(r"(?:INSERT|REPLACE)\b", masked, re.I):
        return None
    m = re.match(r"UPDATE(?:\s+OR\s+\w+)?\s+" + table_pat + r"\s+SET\s+(.*)$", masked, re.I | re.S)
    if m:
        table, body = _unquote(m.group(1)), m.group(2)
        if _top_level_keyword(body, r"\bFROM\b"):
            return None
        clauses = _clauses(body)
        first = _top_level_keyword(body, r"\bWHERE\b|\bRETURNING\b|\bORDER\s+BY\b|\bLIMIT\b")
        assignments = body[: first.start()] if first else body
        cols = []
        for part in _split_top(assignments):
            a = re.match(r"\s*(" + IDENT + r")\s*=", part)
            if not a:
                return None
            cols.append(_unquote(a.group(1)))
        cols += [n for q, n in _identifiers_before_ops(clauses.get("WHERE", "")) if q in (None, table)]
        return table, cols
    m = re.match(r"DELETE\s+FROM\s+" + table_pat + r"(\s.*)?$", masked, re.I | re.S)
    if m:
        table, rest = _unquote(m.group(1)), m.group(2) or ""
        if rest.strip() and not re.match(r"\s*(WHERE|RETURNING|ORDER|LIMIT)\b", rest, re.I):
            return None
        clauses = _clauses(rest)
        return table, [n for q, n in _identifiers_before_ops(clauses.get("WHERE", "")) if q in (None, table)]
    m = re.match(r"SELECT\s+(?:(?:DISTINCT|ALL)\s+)?", masked, re.I)
    if not m:
        return None
    body = masked[m.end():]
    frm = _top_level_keyword(body, r"\bFROM\b")
    if not frm:
        return None
    select_list = body[: frm.start()]
    after = body[frm.end():]
    t = re.match(r"\s*" + table_pat, after, re.I)
    if not t:
        return None
    table = _unquote(t.group(1))
    rest = after[t.end():]
    if re.match(r"\s*[(.,]", rest):
        return None
    a = re.match(r"\s+(?:AS\s+)?([A-Za-z_]\w*)", rest, re.I)
    alias = ""
    if a and a.group(1).lower() not in SQL_KEYWORDS:
        alias = a.group(1).lower()
        rest = rest[a.end():]
    if re.match(r"\s*[,(]", rest):
        return None
    if rest.strip() and not re.match(r"\s*(WHERE|GROUP|ORDER|LIMIT|HAVING|WINDOW|OFFSET)\b", rest, re.I):
        return None
    owners = {table} | ({alias} if alias else set())
    aliases: set[str] = set()
    cols: list[str] = []
    for item in _split_top(select_list):
        item = item.strip()
        tail = re.search(r"(?:\bAS\s+|\s)([A-Za-z_]\w*)\s*$", item, re.I)
        plain = re.fullmatch(r"(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)(?:\s+(?:AS\s+)?([A-Za-z_]\w*))?", item, re.I)
        if plain:
            qual = (plain.group(1) or "").lower()
            name = plain.group(2).lower()
            if plain.group(3):
                aliases.add(plain.group(3).lower())
            if name in SQL_KEYWORDS or (qual and qual not in owners):
                continue
            cols.append(name)
        elif tail and tail.group(1).lower() not in SQL_KEYWORDS:
            aliases.add(tail.group(1).lower())
    clauses = _clauses(rest)
    for key in ("WHERE", "ORDER BY"):
        if key not in clauses:
            continue
        uses = _identifiers_before_ops(clauses[key]) if key == "WHERE" else _order_items(clauses[key])
        for qual, name in uses:
            if qual is not None and qual not in owners:
                continue
            if qual is None and name in aliases:
                continue
            cols.append(name)
    return table, cols

def sql_column_findings(sources: list[Source], diagnostics: list[str] | None = None) -> list[dict[str, str]]:
    diagnostics = diagnostics if diagnostics is not None else []
    schema = build_schema(sources)
    diagnostics.extend(schema.notes)
    if not schema.tables or schema.off:
        return []
    trusted = _literal_words(sources) if schema.dynamic_both else set()
    findings: list[dict[str, str]] = []
    seen: set[tuple] = set()
    for rel, text in sources:
        if rel.lower().endswith(".sql"):
            continue
        lexed = Lexed(text)
        for pos, sql, standalone in sql_literals(lexed):
            if not standalone:
                continue
            for offset, statement in _split_sql(sql):
                if re.match(r"\s*(CREATE|ALTER|DROP|PRAGMA)\b", statement, re.I):
                    continue
                try:
                    parsed = statement_column_uses(statement)
                except Exception as exc:
                    diagnostics.append(f"sql_column: {rel}: unreadable statement ({exc!r})")
                    continue
                if not parsed:
                    continue
                table, cols = parsed
                known = schema.columns(table)
                if known is None:
                    continue
                missing = [c for c in dict.fromkeys(cols)
                           if c not in known and c not in ALWAYS_COLUMNS and c not in schema.anywhere and c not in trusted]
                if not missing:
                    continue
                lead = len(statement) - len(statement.lstrip())
                multiline = lexed.string_at.get(pos, (0, 0, "'", ""))[2] == "`"
                line = lexed.line(pos) + (sql[: offset + lead].count("\n") if multiline else 0)
                key = (rel, line, table, tuple(missing))
                if key in seen:
                    continue
                seen.add(key)
                shown = " ".join(statement.split()).replace(OPAQUE, "${...}")
                if len(shown) > 160:
                    shown = shown[:157] + "..."
                named = ", ".join(f"{table}.{c}" for c in missing)
                findings.append(_finding(
                    f"sql_column: {rel}:{line}", "",
                    f"{named} {'is' if len(missing) == 1 else 'are'} used by `{shown}` but the table declares no such "
                    f"column (columns: {', '.join(sorted(known))}). SQLite fails this statement with "
                    f"'no such column: {missing[0]}'.",
                    "Use a column the table declares (see its CREATE TABLE), e.g. rowid or the key columns in the "
                    "WHERE clause; or add the column to the CREATE TABLE and to existing databases (ALTER TABLE ... "
                    "ADD COLUMN) if the data needs it."))
    return findings[:MAX_FINDINGS_PER_CHECK]

_ASYNC_DEFS = [
    re.compile(r"\basync\s+function\s+([A-Za-z_$][\w$]*)\s*\("),
    re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*async\b"),
    re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*:\s*async\b"),
    re.compile(r"\b(?:module\.)?exports\.([A-Za-z_$][\w$]*)\s*=\s*async\b"),
    re.compile(r"(?:^|[\n;{},])\s*(?:static\s+)?async\s+([A-Za-z_$][\w$]*)\s*\([^()]*\)\s*\{"),
]
_SYNC_DEFS = [
    re.compile(r"(?<!async\s)(?<!async)\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)\s*\("),
    re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=(?![=>])(?!\s*(?:async|await)\b)"),
    re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*:\s*(?:function\b|\([^()]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"),
    re.compile(r"\b(?:module\.)?exports\.([A-Za-z_$][\w$]*)\s*=\s*(?:function\b|\([^()]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"),
    re.compile(r"(?:^|\n)[ \t]*(?:static\s+|get\s+|set\s+)?(?!async\b)([A-Za-z_$][\w$]*)\s*\([^()]*\)\s*\{"),
]
_NOT_NAMES = {"if", "for", "while", "switch", "catch", "function", "return", "with", "else", "do", "try", "async", "await",
              "constructor", "super", "typeof", "new", "delete", "void"}
_PARAMS = [
    re.compile(r"\bfunction\s*\*?\s*[\w$]*\s*\(([^()]*)\)"),
    re.compile(r"\(([^()]*)\)\s*=>"),
    re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*=>"),
    re.compile(r"\bcatch\s*\(([^()]*)\)"),
]

def _async_names(lexes: list[tuple[str, Lexed]]) -> tuple[set[str], set[str]]:
    asynchronous: set[str] = set()
    other: set[str] = set()
    for _rel, lexed in lexes:
        code = lexed.code
        for pat in _ASYNC_DEFS:
            asynchronous.update(m.group(1) for m in pat.finditer(code))
        for pat in _SYNC_DEFS:
            for m in pat.finditer(code):
                if pat is _SYNC_DEFS[0] and re.search(r"\basync\s*$", code[max(0, m.start() - 12): m.start()]):
                    continue
                other.add(m.group(1))
        for pat in _PARAMS:
            for m in pat.finditer(code):
                other.update(re.findall(r"[A-Za-z_$][\w$]*", re.sub(r"=[^,]*", "", m.group(1))))
    other -= _NOT_NAMES
    return asynchronous - other - _NOT_NAMES, other

def _visible_names(lexed: Lexed, names: set[str]) -> tuple[dict[str, str], set[str]]:
    code = lexed.code
    bare: dict[str, str] = {}
    receivers: set[str] = set()
    for pat in _ASYNC_DEFS:
        bare.update((m.group(1), m.group(1)) for m in pat.finditer(code) if m.group(1) in names)
    for m in re.finditer(r"\b(?:const|let|var)\s+(\{[^{}]*\}|[A-Za-z_$][\w$]*)\s*=\s*require\(\s*(['\"])", code):
        spec = lexed.string_at.get(m.end() - 1)
        if not spec or not spec[3].startswith("."):
            continue
        target = m.group(1)
        if target.startswith("{"):
            for part in target[1:-1].split(","):
                pair = re.fullmatch(r"\s*([A-Za-z_$][\w$]*)\s*(?::\s*([A-Za-z_$][\w$]*))?\s*", part)
                if pair and pair.group(1) in names:
                    bare[pair.group(2) or pair.group(1)] = pair.group(1)
        else:
            receivers.add(target)
    for m in re.finditer(r"\bimport\s+\{([^{}]*)\}\s+from\s+(['\"])", code):
        spec = lexed.string_at.get(m.end() - 1)
        if spec and spec[3].startswith("."):
            for part in m.group(1).split(","):
                pair = re.fullmatch(r"\s*([A-Za-z_$][\w$]*)\s*(?:\bas\s+([A-Za-z_$][\w$]*))?\s*", part)
                if pair and pair.group(1) in names:
                    bare[pair.group(2) or pair.group(1)] = pair.group(1)
    return bare, receivers

def _condition_paren(code: str, open_pos: int) -> bool:
    word, _ = _prev_token(code, open_pos)
    return word in {"if", "while"}

def _boolean_use(code: str, start: int, end: int) -> str:
    prev, prev_at = _prev_token(code, start)
    nxt, _ = _next_token(code, end)
    if prev in {"await", "void", "typeof", "new", "function", "."}:
        return ""
    if nxt in {".", "[", "(", "?.", "=", "=>", "??"}:
        return ""
    if prev == "!":
        return "negated"
    comparison_prev = {"===", "!==", "==", "!=", "<=", ">=", "<", ">", "+", "-", "*", "/", "%", "??", "in", "instanceof"}
    if prev in comparison_prev:
        return ""
    lead_ok = {"(", "&&", "||", "=", ",", "?", ":", "return", "{", ";", "=>", ""}
    if nxt in {"&&", "||"} and prev in lead_ok:
        return "operand"
    if nxt == "?" and prev in lead_ok:
        return "operand"
    if nxt == ")" and prev == "(" and _condition_paren(code, prev_at):
        return "condition"
    if prev in {"&&", "||"} and nxt == ")":
        opener = _enclosing_open(code, start)
        if opener >= 0 and code[opener] == "(":
            if _condition_paren(code, opener):
                return "condition"
            close = _match_close(code, opener)
            before, _ = _prev_token(code, opener)
            after, _ = _next_token(code, close + 1) if close >= 0 else ("", 0)
            if before == "!" or after == "?":
                return "condition"
    return ""

def _in_condition(code: str, start: int, end: int) -> bool:
    pos, stop = start, end
    while True:
        k, depth = stop, 0
        while k < len(code):
            ch = code[k]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and ch in ";,:":
                return False
            elif depth == 0 and ch == "=" and code[k - 1] not in "=!<>" and code[k + 1: k + 2] not in {"=", ">"}:
                return False
            elif depth == 0 and ch == "?" and code[k + 1: k + 2] not in {".", "?"} and code[k - 1] != "?":
                return True
            k += 1
        opener = _enclosing_open(code, pos)
        if opener < 0 or code[opener] != "(":
            return False
        if _condition_paren(code, opener):
            return True
        word, _ = _prev_token(code, opener)
        if word and (word[0].isalnum() or word[0] in "_$") and word not in {"return"}:
            return False
        pos = opener
        stop = _match_close(code, opener) + 1
        if stop <= 0:
            return False

def unawaited_async_findings(sources: list[Source], diagnostics: list[str] | None = None) -> list[dict[str, str]]:
    diagnostics = diagnostics if diagnostics is not None else []
    lexes = [(rel, Lexed(text)) for rel, text in sources if not rel.lower().endswith(".sql")]
    names, other = _async_names(lexes)
    if not names:
        return []
    findings: list[dict[str, str]] = []
    seen: set[tuple] = set()
    for rel, lexed in lexes:
        code = lexed.code
        bare, receivers = _visible_names(lexed, names)
        if not bare and not receivers:
            continue
        call = re.compile(r"(?<![\w$.])(?:([A-Za-z_$][\w$]*)\s*\.\s*)?([A-Za-z_$][\w$]*)\s*\(")
        for m in call.finditer(code):
            recv, name = m.group(1), m.group(2)
            if recv is not None and (name not in names or recv not in receivers):
                continue
            if recv is None and (name not in bare or (bare[name] != name and name in other)):
                continue
            defined = name if recv is not None else bare[name]
            open_at = m.end() - 1
            close = _match_close(code, open_at)
            if close < 0:
                continue
            nxt, _ = _next_token(code, close + 1)
            if nxt == "{":
                continue
            start = m.start()
            if not _boolean_use(code, start, close + 1) or not _in_condition(code, start, close + 1):
                continue
            shown_call = " ".join(lexed.text[start: close + 1].split())
            if len(shown_call) > 100:
                shown_call = shown_call[:97] + "...)"
            line = lexed.line(start)
            if (rel, line, name) in seen:
                continue
            seen.add((rel, line, name))
            context = " ".join(lexed.text[lexed.text.rfind("\n", 0, start) + 1: lexed.text.find("\n", close)].split())
            alias = f" (`{defined}`, imported as `{name}`)" if defined != name else ""
            findings.append(_finding(
                f"unawaited_async: {rel}:{line}", "",
                f"`{name}` is an async function{alias} (it returns a Promise), but `{shown_call}` is used as a "
                f"condition without await in `{context[:160]}`. A Promise is always truthy, so this check never takes "
                f"effect.",
                f"Await the call where its result decides the branch: `(await {name}(...))`, e.g. "
                f"`if (!(await {name}(...)))`; make the enclosing function async if needed."))
    return findings[:MAX_FINDINGS_PER_CHECK]

_LOGIN_PATH = re.compile(r"(?:^|/)(?:log-?in|sign-?in|sign_in|sessions?)/?$", re.I)
_USER_KEY = re.compile(r"^(?:user_?id|account_?id|userid|accountid)$", re.I)
_STATE_COLUMN = re.compile(r"^(?:is_)?(?:active|revoked\w*|valid\w*|status|expire\w*|ended\w*|logged_out\w*|"
                           r"signed_out\w*|deleted\w*|closed\w*|invalidated\w*|terminated\w*)$", re.I)
_OTHER_SESSION = re.compile(r"^(?:id|token|sid|session_id|session_token)$", re.I)

_SINGLE_SESSION = re.compile(
    r"\bone\s+(?:active\s+|concurrent\s+)?session\s+at\s+a\s+time\b|"
    r"\b(?:only\s+one|a\s+single|single|at\s+most\s+one|one\s+active)\s+(?:active\s+|concurrent\s+|signed-in\s+)?sessions?\b|"
    r"\b(?:refus|reject|den(?:y|ies|ied)|block|fail|forbid|prevent|disallow)\w*\b[^.;]*\balready\s+(?:signed|logged)\s+in\b|"
    r"\balready\s+(?:signed|logged)\s+in\b[^.;]*\b(?:refus|reject|den(?:y|ies|ied)|block|fail|forbid|prevent|disallow|cannot)\w*|"
    r"\balready\s+(?:has|holds|have|owns)\s+an?\s+(?:active\s+|open\s+)?session\b|"
    r"\bmore\s+than\s+one\s+(?:active\s+|concurrent\s+)?session|\bconcurrent\s+sessions?\b|"
    r"单一会话|同一时间只(?:能|允许)|只(?:能|允许)(?:有|保持)?一个(?:活跃|有效)?会话",
    re.I)
_ENDS_ON_SIGN_IN = re.compile(
    r"\b(?:sign|log)(?:s|ing|ging|ged|ed)?[\s-]*(?:in|on)\b[^.;]*\b(?:end|ends|ending|terminat\w*|invalidat\w*|revok\w*|"
    r"(?:signs?|logs?|signing|logging)\s+out|close[sd]?|kick\w*)\b[^.;]*\b(?:other|previous|existing|older|prior)\s+"
    r"(?:active\s+)?(?:sessions?|devices?|browsers?)\b", re.I)
_OTHER_SESSIONS = re.compile(r"\b(?:other|previous|existing|older|prior)\s+(?:active\s+)?(?:sessions?|devices?|browsers?)\b|"
                             r"其他(?:设备|会话|浏览器)", re.I)
_NEGATION = re.compile(r"\b(?:not|never|no|cannot|without|neither|nor)\b|n't\b|不|无需|无须|勿|禁止", re.I)

def session_policy(requirement: str) -> tuple[str, list[str]]:
    verdict, why = "plain", []
    for sentence in re.split(r"(?<=[.!?;。！？；])\s*|\n+", requirement or ""):
        if not sentence.strip():
            continue
        negated = bool(_NEGATION.search(sentence))
        if _SINGLE_SESSION.search(sentence) or (_ENDS_ON_SIGN_IN.search(sentence) and not negated):
            return "single", [sentence.strip()[:160]]
        if _OTHER_SESSIONS.search(sentence) and not negated and not _ENDS_ON_SIGN_IN.search(sentence):
            verdict, why = "unsure_ending", why + [sentence.strip()[:160]]
    return verdict, why

def _where_conjuncts(where: str) -> list[str] | None:
    if re.search(r"\bOR\b", where, re.I):
        return None
    return [c.strip() for c in re.split(r"\bAND\b", where, flags=re.I) if c.strip()]

def _session_lookup(sql: str) -> str | None:
    m = re.match(r"\s*SELECT\b(.*?)\bFROM\s+(" + IDENT + r")\s+(?:AS\s+\w+\s+)?WHERE\s+(.*?)\s*(?:\bLIMIT\b.*|\bORDER\s+BY\b.*)?$",
                 _mask_sql_strings(sql), re.I | re.S)
    if not m or "session" not in _unquote(m.group(2)):
        return None
    if _MULTI.search(sql):
        return None
    conj = _where_conjuncts(m.group(3))
    if not conj:
        return None
    has_user = False
    for c in conj:
        col = re.match(r"(?:\w+\.)?(\w+)", c)
        if not col:
            return None
        name = col.group(1)
        if _USER_KEY.match(name) and re.fullmatch(r"(?:\w+\.)?\w+\s*=\s*(?:\?\d*|[:@$]\w+|\$\d+|" + OPAQUE + r")", c):
            has_user = True
        elif not _STATE_COLUMN.match(name):
            return None
    return _unquote(m.group(2)) if has_user else None

def _ends_other_sessions(sql: str) -> str | None:
    masked = _mask_sql_strings(sql)
    m = re.match(r"\s*DELETE\s+FROM\s+(" + IDENT + r")\s+WHERE\s+(.*?)\s*$", masked, re.I | re.S)
    u = re.match(r"\s*UPDATE\s+(" + IDENT + r")\s+SET\s+(.*?)\s+WHERE\s+(.*?)\s*$", masked, re.I | re.S)
    if m:
        table, where = _unquote(m.group(1)), m.group(2)
    elif u:
        table, where = _unquote(u.group(1)), u.group(3)
        raw = re.match(r"\s*UPDATE\s+(" + IDENT + r")\s+SET\s+(.*?)\s+WHERE\s+", sql, re.I | re.S)
        sets = [re.fullmatch(r"\s*(\w+)\s*=\s*(.*?)\s*", p, re.S) for p in _split_top(raw.group(2) if raw else "")]
        if not sets or any(s is None for s in sets) or not all(_STATE_COLUMN.match(s.group(1)) for s in sets):
            return None
        if not any(_invalidates(s.group(1), s.group(2)) for s in sets):
            return None
    else:
        return None
    if "session" not in table:
        return None
    conj = _where_conjuncts(where)
    if not conj:
        return None
    has_user = False
    for c in conj:
        if re.fullmatch(r"(?:\w+\.)?(\w+)\s*=\s*(?:\?\d*|[:@$]\w+|\$\d+|" + OPAQUE + r")", c) and \
                _USER_KEY.match(re.match(r"(?:\w+\.)?(\w+)", c).group(1)):
            has_user = True
        elif re.fullmatch(r"(?:\w+\.)?(\w+)\s*(?:!=|<>|NOT\s+IN\b).*", c, re.I | re.S) and \
                _OTHER_SESSION.match(re.match(r"(?:\w+\.)?(\w+)", c).group(1)):
            continue
        else:
            return None
    return table if has_user else None

_NOW = r"(?:CURRENT_TIMESTAMP|datetime\(\s*'now'\s*(?:,\s*'-[^']*'\s*)?\)|strftime\(\s*'[^']*'\s*,\s*'now'\s*\)|unixepoch\(\s*\)|0)"

def _invalidates(column: str, value: str) -> bool:
    col, val = column.lower(), value.strip()
    if re.fullmatch(r"(?:is_)?(?:active|valid)", col):
        return bool(re.fullmatch(r"0|false|'false'", val, re.I))
    if re.fullmatch(r"(?:is_)?(?:revoked|ended|logged_out|signed_out|invalidated|terminated|closed|deleted)", col):
        return bool(re.fullmatch(r"1|true|'true'", val, re.I))
    if re.fullmatch(r"(?:revoked|ended|logged_out|signed_out|invalidated|terminated|closed|deleted)_at", col):
        return bool(re.fullmatch(_NOW, val, re.I)) and val != "0"
    if re.fullmatch(r"expire\w*", col):
        return bool(re.fullmatch(_NOW + r"|'(?:19\d\d|1[0-8]\d\d)-[^']*'", val, re.I))
    if col == "status":
        return bool(re.fullmatch(r"'(?:revoked|ended|inactive|expired|logged_out|signed_out|closed|invalidated|terminated)'",
                                 val, re.I))
    return False

def _function_bodies(lexes: list[tuple[str, Lexed]]) -> dict[str, list[tuple[str, Lexed, int, int]]]:
    out: dict[str, list] = {}
    pat = re.compile(r"(?:\bfunction\s+([A-Za-z_$][\w$]*)\s*\(|\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
                     r"(?:function\b[^(]*)?\()")
    for rel, lexed in lexes:
        code = lexed.code
        for m in pat.finditer(code):
            name = m.group(1) or m.group(2)
            close = _match_close(code, m.end() - 1)
            if close < 0:
                continue
            k = _next_token(code, close + 1)
            brace = k[1]
            if k[0] == "=>":
                brace = _next_token(code, k[1] + 2)[1]
            if brace >= len(code) or code[brace] != "{":
                continue
            end = _match_close(code, brace)
            if end > 0:
                out.setdefault(name, []).append((rel, lexed, brace, end))
    return out

def _refusal_after(code: str, text: str, stmt_end: int, names: list[str], body_end: int) -> tuple[int, str] | None:
    for m in re.finditer(r"\bif\s*\(", code[stmt_end:body_end]):
        open_at = stmt_end + m.end() - 1
        close = _match_close(code, open_at)
        if close < 0:
            return None
        cond = code[open_at + 1: close].strip()
        if not any(re.search(r"(?<![\w$.])" + re.escape(n) + r"(?![\w$])", cond) for n in names):
            continue
        found_form = False
        conj = [c.strip() for c in re.split(r"&&", cond)]
        if "||" in cond:
            return None
        for c in conj:
            ok = any(re.fullmatch(
                re.escape(n) + r"(?:\s*(?:!==?|!=)\s*(?:null|undefined))?|" + re.escape(n) + r"(?:\.\w+|\[\s*['\"]\s*\w+\s*['\"]\s*\])?"
                r"(?:\s*(?:>\s*0|>=\s*1|!==?\s*0))?|" + re.escape(n) + r"\.length(?:\s*>\s*0)?", c) for n in names)
            if not ok:
                return None
            found_form = True
        if not found_form:
            return None
        nxt, at = _next_token(code, close + 1)
        if nxt == "{":
            end = _match_close(code, at)
            then = text[at: end + 1] if end > 0 else ""
        else:
            semi = code.find(";", at)
            then = text[at: semi + 1] if semi > 0 else ""
        if re.search(r"\.(?:status|sendStatus)\s*\(\s*4\d\d\b", then) or re.search(r"\bthrow\b[^;]*\b4\d\d\b", then):
            return open_at, " ".join(text[m.start() + stmt_end: close + 1].split())
        return None
    return None

def login_session_findings(sources: list[Source], requirement: str = "",
                           diagnostics: list[str] | None = None) -> list[dict[str, str]]:
    diagnostics = diagnostics if diagnostics is not None else []
    lexes = [(rel, Lexed(text)) for rel, text in sources if not rel.lower().endswith(".sql")]
    policy, why = session_policy(requirement)
    if policy == "single":
        diagnostics.append(f"login_session_refusal: off, the requirement states a session rule: {why[0]!r}")
        return []
    ending_allowed = policy == "unsure_ending"
    if ending_allowed:
        diagnostics.append(f"login_session_refusal: ending-sessions branch off, the requirement mentions: {why[0]!r}")
    bodies = _function_bodies(lexes)
    findings: list[dict[str, str]] = []
    seen: set[tuple] = set()
    route = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\.\s*post\s*\(\s*(['\"`])")
    for rel, lexed in lexes:
        code, text = lexed.code, lexed.text
        for m in route.finditer(code):
            literal = lexed.string_at.get(m.end() - 1)
            if not literal or OPAQUE in literal[3] or not _LOGIN_PATH.search(literal[3]):
                continue
            open_at = code.find("(", m.start() + len(m.group(1)))
            close = _match_close(code, open_at)
            if close < 0:
                continue
            path = literal[3]
            in_body = [s for s in lexed.strings if open_at < s[0] < close]
            for start, end, quote, content in in_body:
                sql = _js_unescape(content) if quote != "`" else content
                table = _session_lookup(sql)
                if table:
                    decl = re.search(r"(?:^|[;{}\n])\s*(?:const|let|var)\s+(\{[^{}]*\}|\[[^\[\]]*\]|[A-Za-z_$][\w$]*)\s*=\s*"
                                     r"(?:await\s+)?[\w$.]*\s*\(\s*$", code[max(0, start - 300):start])
                    if not decl:
                        continue
                    target = decl.group(1)
                    names = re.findall(r"[A-Za-z_$][\w$]*", re.sub(r"\w+\s*:", "", target)) if target[0] in "{[" else [target]
                    semi = code.find(";", end)
                    stmt_end = semi if 0 < semi < close else end
                    hit = _refusal_after(code, text, stmt_end, names, close)
                    if hit:
                        line = lexed.line(hit[0])
                        if (rel, line) not in seen:
                            seen.add((rel, line))
                            shown = " ".join(sql.split())
                            findings.append(_finding(
                                f"login_session_refusal: {rel}:{line}", "",
                                f"The sign-in route POST {path} looks up the account's existing session (`{shown[:140]}`) "
                                f"and answers 4xx when one is found (`{hit[1][:120]}`): an account that is signed in in one "
                                f"browser cannot sign in from another (a browser closed without signing out locks it).",
                                "Let one account hold several sessions: on a successful credential check always create a "
                                "new session; do not refuse sign-in because the account already has an active session."))
                    continue
            if ending_allowed:
                continue

            places = [(rel, lexed, open_at, close)]
            for call in re.finditer(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(", code[open_at:close]):
                defs = bodies.get(call.group(1), [])
                if len(defs) == 1:
                    places.append(defs[0])
            for prel, plexed, pstart, pend in places:
                for start, end, quote, content in plexed.strings:
                    if not pstart < start < pend:
                        continue
                    sql = _js_unescape(content) if quote != "`" else content
                    table = _ends_other_sessions(sql)
                    if not table:
                        continue
                    line = plexed.line(start)
                    if (prel, line) in seen:
                        continue
                    seen.add((prel, line))
                    shown = " ".join(sql.split())
                    via = "" if plexed is lexed and pstart == open_at else " (called from that route)"
                    findings.append(_finding(
                        f"login_session_refusal: {prel}:{line}", "",
                        f"Signing in through POST {path} ends every other session of the account{via}: `{shown[:140]}`. "
                        f"The requirement does not ask for one session at a time, so a second browser signing the same "
                        f"account in signs the first one out.",
                        "Keep the account's other sessions on sign-in: only create the new session (end sessions only on "
                        "sign-out, or where the requirement says so)."))
    return findings[:MAX_FINDINGS_PER_CHECK]

CHECKS: list[tuple[str, Callable]] = [
    ("sql_column", lambda src, req, diag: sql_column_findings(src, diag)),
    ("unawaited_async", lambda src, req, diag: unawaited_async_findings(src, diag)),
    ("login_session_refusal", lambda src, req, diag: login_session_findings(src, req, diag)),
]

def code_check_findings(project_dir: Path, requirement: str = "", log=print,
                        diagnostics: list[str] | None = None) -> list[dict[str, str]]:
    diagnostics = diagnostics if diagnostics is not None else []
    try:
        sources = backend_sources(Path(project_dir))
    except Exception as exc:
        diagnostics.append(f"code_checks: cannot read the backend sources ({exc!r})")
        sources = []
    found: list[dict[str, str]] = []
    for name, check in CHECKS:
        try:
            got = check(sources, requirement or "", diagnostics)
        except Exception as exc:
            diagnostics.append(f"{name}: crashed ({exc!r}); no findings")
            got = []
        found.extend(got)
    try:
        counts = {name: sum(1 for f in found if f["where"].startswith(name + ":")) for name, _ in CHECKS}
        log(f"code checks: {counts}" + (f"; {len(diagnostics)} diagnostic(s)" if diagnostics else ""))
        for note in diagnostics[:10]:
            log(f"  code checks: {note}")
    except Exception:
        pass
    return found
