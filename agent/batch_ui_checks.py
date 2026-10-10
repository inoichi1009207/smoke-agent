from __future__ import annotations

import contextvars
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

MAX_PER_CHECK = 8

RECHECK_LEDGER: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("audit_recheck_ledger", default=None)
TIME_CAP_SEC = 5.0
SOURCE_SUFFIXES = (".tsx", ".jsx", ".ts", ".js")
SKIP_PARTS = {"node_modules", "dist", "build", ".vite"}
CHOICE_SELECT = "ChoiceSelect"

SETTING_KEYS = ("dialog_close_check", "dialog_entries_buttons_check", "cell_glyph_check", "stated_select_check",
                "header_button_check", "status_word_check", "count_text_check",
                "stated_textbox_check", "menu_role_check")

def _strip(text: str) -> str:
    from .detaudit import strip_js_comments

    return strip_js_comments(text)

def _open_tag_end(text: str, start: int) -> tuple[int, bool]:
    depth, quote, i, n = 0, "", start + 1, len(text)
    while i < n:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "'\"`":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
        elif c == ">" and depth == 0:
            return i + 1, text[i - 1] == "/"
        elif c == "<" and depth == 0 and i > start + 1 and text[i + 1 : i + 2].isalpha():
            return -1, False
        i += 1
    return -1, False

def _element_span(text: str, start: int) -> tuple[int, int, int] | None:
    match = re.match(r"<([A-Za-z][\w.]*)", text[start:])
    if not match:
        return None
    tag = match.group(1)
    open_end, self_closing = _open_tag_end(text, start)
    if open_end < 0:
        return None
    if self_closing:
        return start, open_end, open_end
    depth = 1
    pattern = re.compile(r"<(/?)" + re.escape(tag) + r"(?=[\s>/])")
    pos = open_end
    while True:
        found = pattern.search(text, pos)
        if not found:
            return None
        if found.group(1):
            depth -= 1
            close = text.find(">", found.end())
            if close < 0:
                return None
            if depth == 0:
                return start, open_end, close + 1
            pos = close + 1
        else:
            inner_end, inner_self = _open_tag_end(text, found.start())
            if inner_end < 0:
                return None
            if not inner_self:
                depth += 1
            pos = inner_end

def _tag_start(text: str, pos: int) -> int:
    i = pos
    while i >= 0:
        i = text.rfind("<", 0, i)
        if i < 0:
            return -1
        if re.match(r"<[A-Za-z]", text[i : i + 2]):
            end, _ = _open_tag_end(text, i)
            if end > pos:
                return i
            return -1
    return -1

def _attr(open_tag: str, name: str) -> str | None:
    match = re.search(r"(?<![\w-])" + re.escape(name) + r"""\s*=\s*(?:"([^"]*)"|'([^']*)'|\{\s*(?:'([^']*)'|"([^"]*)"|`([^`]*)`)\s*\}|\{([^}]*)\})""",
                      open_tag)
    if not match:
        return None
    groups = match.groups()
    for value in groups[:4]:
        if value is not None:
            return value
    if groups[4] is not None:
        return re.sub(r"\$\{([^}]*)\}", r"{\1}", groups[4])
    return "{" + groups[5].strip() + "}"

def _text_of(inner: str) -> str:
    out = re.sub(r"\{\s*(['\"])(.*?)\1\s*\}", r"\2", inner)
    out = re.sub(r"<[^<>]*>", " ", out)
    return " ".join(out.split())

def _tags_with(text: str, attr_pattern: str, tags: tuple[str, ...] = ()) -> list[int]:
    starts: set[int] = set()
    for match in re.finditer(attr_pattern, text):
        start = _tag_start(text, match.start())
        if start >= 0:
            starts.add(start)
    for tag in tags:
        for match in re.finditer(r"<" + re.escape(tag) + r"(?=[\s>/])", text):
            starts.add(match.start())
    return sorted(starts)

def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1

def _source_file(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if any(part in SKIP_PARTS for part in parts) or not rel.endswith(SOURCE_SUFFIXES):
        return False
    if Path(rel).stem == CHOICE_SELECT:
        return False
    return parts[0] == "frontend"

def batch_files(project_dir: Path, written: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in dict.fromkeys(str(r).replace("\\", "/") for r in written or []):
        if not _source_file(rel):
            continue
        try:
            out[rel] = (Path(project_dir) / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return out

def _all_frontend(project_dir: Path) -> dict[str, str]:
    import os

    out: dict[str, str] = {}
    base = Path(project_dir) / "frontend"
    if not base.is_dir():
        return out
    for root, dirs, names in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_PARTS and not d.startswith(".")]
        for name in names:
            path = Path(root) / name
            rel = path.relative_to(project_dir).as_posix()
            if _source_file(rel):
                try:
                    out[rel] = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
    return out

def _without_choice_select(text: str) -> str:
    match = re.search(r"(?:function\s+ChoiceSelect\b|const\s+ChoiceSelect\s*=)", text)
    if not match:
        return text
    nxt = re.search(r"\n(?:export\s+)?(?:default\s+)?(?:function|const|class)\s", text[match.end():])
    end = len(text) if not nxt else match.end() + nxt.start()
    return text[: match.start()] + re.sub(r"[^\n]", " ", text[match.start():end]) + text[end:]

def _prepare(text: str) -> str:
    return _without_choice_select(_strip(text))

def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.;!?])\s+|\n+", text or "") if s.strip()]

def _quoted(sentence: str) -> list[str]:
    return [m.group(2) for m in re.finditer(r"([\"“`'])(.+?)([\"”`'])", sentence) if m.group(2).strip()]

LIST_WORDS = re.compile(r"\b(?:list|lists|listbox|list box|options?)\b", re.I)

def _requirement_names_list(requirement: str, names: list[str]) -> bool:
    wanted = {n.strip().lower() for n in names if n and n.strip()}
    for sentence in _sentences(requirement):
        if {q.strip().lower() for q in _quoted(sentence)} & wanted and LIST_WORDS.search(sentence):
            return True
    return False

class TimeUp(Exception):
    pass

class Budget:

    def __init__(self, seconds: float):
        self.end = time.monotonic() + max(0.0, float(seconds))

    def check(self) -> None:
        if time.monotonic() >= self.end:
            raise TimeUp()

DEF = re.compile(r"^(?:export\s+)?(?:default\s+)?(?:function\s+([A-Z]\w*)\s*[(<]|(?:const|let)\s+([A-Z]\w*)\s*(?::[^=\n]+)?=)",
                 re.M)
IMPORT = re.compile(r"""\bimport\s+(?!type\b)([^;'"]+?)\s+from\s+['"]([^'"]+)['"]""")

class Project:

    def __init__(self, project_dir: Path, written: dict[str, str], all_sources: dict[str, str] | None = None):
        self.dir = Path(project_dir)
        self.written = written
        self._all = all_sources
        self._text: dict[str, str] = {}
        self._defs: dict[str, list[tuple[str, int, int]]] | None = None
        self._by_name: dict[str, list[tuple[str, int, int]]] | None = None

    def sources(self) -> dict[str, str]:
        if self._all is None:
            self._all = _all_frontend(self.dir)
        merged = dict(self._all)
        merged.update(self.written)
        self._all = merged
        return self._all

    def text(self, rel: str) -> str:
        if rel not in self._text:
            raw = self.written.get(rel)
            if raw is None:
                raw = self.sources().get(rel, "")
            self._text[rel] = _prepare(raw)
        return self._text[rel]

    def defs(self, rel: str) -> list[tuple[str, int, int]]:
        self._index()
        return self._defs.get(rel, [])

    def _index(self) -> None:
        if self._defs is not None:
            return
        self._defs, self._by_name = {}, {}
        for rel in self.sources():
            text = self.text(rel)
            found = [(m.group(1) or m.group(2), m.start()) for m in DEF.finditer(text)]
            spans = [(name, start, found[i + 1][1] if i + 1 < len(found) else len(text)) for i, (name, start) in enumerate(found)]
            self._defs[rel] = spans
            for name, start, end in spans:
                self._by_name.setdefault(name, []).append((rel, start, end))

    def _module(self, rel: str, spec: str) -> str | None:
        if not spec.startswith("."):
            return None
        base = (Path(rel).parent / spec).as_posix()
        parts: list[str] = []
        for part in base.split("/"):
            if part == "..":
                if parts:
                    parts.pop()
            elif part not in (".", ""):
                parts.append(part)
        base = "/".join(parts)
        for candidate in [base] + [base + s for s in SOURCE_SUFFIXES] + [base + "/index" + s for s in SOURCE_SUFFIXES]:
            if candidate in self.sources():
                return candidate
        return None

    def resolve(self, rel: str, name: str) -> tuple[str, int, int] | None:
        self._index()
        for d_name, start, end in self.defs(rel):
            if d_name == name:
                return rel, start, end
        text = self.text(rel)
        for match in IMPORT.finditer(text):
            clause, spec = match.group(1), match.group(2)
            default = re.match(r"\s*([A-Za-z_$][\w$]*)", clause)
            named = {}
            braces = re.search(r"\{([^}]*)\}", clause)
            for item in (braces.group(1).split(",") if braces else []):
                bits = item.strip().split(" as ")
                if bits[0].strip():
                    named[bits[-1].strip()] = bits[0].strip()
            target = None
            if default and default.group(1) == name and not clause.strip().startswith("{"):
                target = "default"
            elif name in named:
                target = named[name]
            if target is None:
                continue
            module = self._module(rel, spec)
            if not module:
                break
            spans = self.defs(module)
            if target == "default":
                mtext = self.text(module)
                exported = re.search(r"export\s+default\s+(?:function\s+)?([A-Z]\w*)", mtext)
                wanted = exported.group(1) if exported else name
                hit = [s for s in spans if s[0] == wanted] or spans[:1]
            else:
                hit = [s for s in spans if s[0] == target]
            return (module, hit[0][1], hit[0][2]) if hit else None
        unique = (self._by_name or {}).get(name) or []
        return unique[0] if len(unique) == 1 else None

    def children(self, rel: str, start: int, end: int) -> list[tuple[str, int, int]]:
        text = self.text(rel)
        out: list[tuple[str, int, int]] = []
        for name in dict.fromkeys(re.findall(r"<([A-Z]\w*)(?=[\s>/])", text[start:end])):
            if name == CHOICE_SELECT:
                continue
            hit = self.resolve(rel, name)
            if hit:
                out.append(hit)
        return out

    def reaches(self, rel: str, start: int, end: int, test: Callable[[str], bool], depth: int = 3) -> bool:
        seen: set[tuple[str, int]] = set()
        frontier = [(rel, start, end)]
        for level in range(depth + 1):
            nxt: list[tuple[str, int, int]] = []
            for f_rel, f_start, f_end in frontier:
                if (f_rel, f_start) in seen:
                    continue
                seen.add((f_rel, f_start))
                if test(self.text(f_rel)[f_start:f_end]):
                    return True
                if level < depth:
                    nxt.extend(self.children(f_rel, f_start, f_end))
            frontier = nxt
        return False

DIALOG_ATTR = r"""role\s*=\s*(?:"dialog"|'dialog'|\{\s*['"]dialog['"]\s*\})|aria-modal\b"""

def _dialogs(text: str) -> list[tuple[str, int, int, int]]:
    out: list[tuple[str, int, int, int]] = []
    for start in _tags_with(text, DIALOG_ATTR, ("dialog",)):
        if any(s <= start < e for _, s, _, e in out):
            continue
        span = _element_span(text, start)
        if not span:
            continue
        _, open_end, end = span
        open_tag = text[start:open_end]
        name = _attr(open_tag, "aria-label")
        if name is None:
            ref = _attr(open_tag, "aria-labelledby")
            if ref:
                target = re.search(r"""\bid\s*=\s*["']""" + re.escape(ref) + r"""["']""", text)
                if target:
                    tag = _tag_start(text, target.start())
                    inner = _element_span(text, tag) if tag >= 0 else None
                    if inner:
                        name = _text_of(text[inner[1] : inner[2]])
        if name is None:
            heading = re.search(r"<h[1-6][^>]*>(.*?)</h[1-6]>", text[open_end:end], re.S)
            name = _text_of(heading.group(1)) if heading else ""
        out.append((name.strip(), start, open_end, end))
    return out

def _has_close(body: str) -> bool:
    if re.search(r"""aria-label\s*=\s*(?:"Close"|'Close'|\{\s*['"`]Close['"`]\s*\})""", body):
        return True
    if re.search(r""">\s*(?:\{\s*['"`])?Close(?:['"`]\s*\})?\s*<""", body):
        return True
    if re.search(r"""\bvalue\s*=\s*["']Close["']""", body):
        return True
    return False

def _names(dialogs: list[tuple[str, int, int, int]]) -> set[str]:
    return {name for name, *_ in dialogs}

def dialog_close_findings(files: dict[str, str], before: dict[str, str], project: Project | None = None,
                          budget: Budget | None = None, unbounded: bool = False,
                          per_target: bool = False) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    project = project or Project(Path("."), files, all_sources=dict(files))
    for rel in files:
        if budget:
            budget.check()
        text = project.text(rel)
        old = _names(_dialogs(_prepare(before[rel]))) if rel in before else set()
        missing = []
        for name, start, open_end, end in _dialogs(text):
            if rel in before and name in old:
                continue

            if _has_close(text[open_end:end]) or project.reaches(rel, open_end, end, _has_close, depth=3):
                continue
            missing.append((name, start))
        for group in ([[m] for m in missing] if per_target else [missing] if missing else []):
            names = ", ".join(f"`{n}`" if n else "(unnamed)" for n, _ in group)
            found.append({
                "where": f"dialog_close {rel}:{_line(text, group[0][1])}",
                "requirement": "(a dialog also offers a button named exactly Close)",
                "problem": f"{rel} renders the dialog {names} without a button whose accessible name is exactly `Close`; a "
                           "visitor (or a test) that closes the dialog by its Close button finds none. A Cancel button does "
                           "not replace it.",
                "fix": "Add a <button type=\"button\"> named exactly Close (visible text Close, no other name) inside the "
                       "dialog, next to the actions the requirement states, that closes the dialog the way its other "
                       "close paths do; keep every stated action (Cancel, Save, ...) as it is.",
                "check": "dialog_close",
            })
    return found if unbounded else found[:MAX_PER_CHECK]

CONTROLLER = re.compile(r"""aria-haspopup|aria-expanded|role\s*=\s*["'{]\s*['"]?combobox""")
TOGGLED = re.compile(r"""(?<![\w-])hidden\b|style\s*=\s*\{[^>]*\b(?:display|visibility)\b|className\s*=\s*\{[^>]*\?[^>]*\b(?:hidden|invisible|block|flex)\b""")
CONDITION = re.compile(r"&&|\|\||\?|:")

def _open_containers(text: str, floor: int, pos: int) -> list[int]:
    stack: list[int] = []
    i, quote = floor, ""
    while i < pos:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c == '"' or (stack and c in "'`"):
            quote = c
        elif c == "{":
            stack.append(i)
        elif c == "}" and stack:
            stack.pop()
        i += 1
    return stack

def _popup(text: str, open_end: int, lb_start: int, lb_open: int, lb_end: int) -> bool:
    if "createPortal" in text:
        return True
    if CONTROLLER.search(text[max(open_end, lb_start - 800):lb_start]):
        return True
    open_tag = text[lb_start:lb_open]
    ident = _attr(open_tag, "id")
    if ident and re.search(r"aria-(?:controls|owns)\s*=\s*[\"'{][^>]*" + re.escape(ident.split("{")[0] or "\0"), text):
        return True
    if TOGGLED.search(open_tag):
        return True
    for container in _open_containers(text, open_end, lb_start):
        head_end = text.find("<", container)
        head = text[container + 1 : head_end if 0 <= head_end < lb_start else lb_start]
        if CONDITION.search(head):
            return True
    for match in re.finditer(r"<[A-Za-z][\w.]*", text[open_end:lb_start]):
        start = open_end + match.start()
        span = _element_span(text, start)
        if span and span[2] > lb_end and TOGGLED.search(text[start : span[1]]):
            return True
    return False

def _listboxes(text: str, open_end: int, end: int) -> list[tuple[str, int, bool]]:
    out: list[tuple[str, int, bool]] = []
    for start in _tags_with(text, r"""role\s*=\s*(?:"listbox"|'listbox'|\{\s*['"]listbox['"]\s*\})"""):
        if not open_end <= start < end:
            continue
        span = _element_span(text, start)
        if not span:
            continue
        _, lb_open, lb_end = span
        if not re.search(r"""role\s*=\s*(?:"option"|'option'|\{\s*['"]option['"]\s*\})""", text[lb_open:lb_end]):
            continue
        label = _attr(text[start:lb_open], "aria-label") or ""
        out.append((label, start, not _popup(text, open_end, start, lb_open, lb_end)))
    return out

def dialog_entries_findings(files: dict[str, str], before: dict[str, str], requirement: str,
                            budget: Budget | None = None, unbounded: bool = False) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for rel, raw in files.items():
        if budget:
            budget.check()
        text = _prepare(raw)
        old_labels: set[str] = set()
        if rel in before:
            old_text = _prepare(before[rel])
            for _, _, o_open, o_end in _dialogs(old_text):
                old_labels |= {label for label, _, _ in _listboxes(old_text, o_open, o_end)}
        for name, _start, open_end, end in _dialogs(text):
            for label, lb_start, always in _listboxes(text, open_end, end):
                if not always or (rel in before and label in old_labels):
                    continue
                if _requirement_names_list(requirement, [name, label]):
                    continue
                found.append({
                    "where": f"dialog_entries {rel}:{_line(text, lb_start)}",
                    "requirement": f"(entries the visitor clicks in the `{name or 'unnamed'}` dialog are buttons)",
                    "problem": f"{rel}: the dialog `{name or '(unnamed)'}` shows its entries as role-option items of an "
                               f"always-visible listbox `{label or '(unnamed)'}`, so each entry's role is option, not "
                               "button; a visitor (or a test) that clicks an entry as a button by its name finds none.",
                    "fix": "Render each entry as a plain <button type=\"button\"> named by the entry (its text only), "
                           "without role option and without the surrounding role listbox (a plain list container or "
                           "div); show the chosen entry with aria-pressed or aria-current, keep the click behaviour.",
                    "check": "dialog_entries",
                })
    return found if unbounded else found[:MAX_PER_CHECK]

GLYPH_RANGES = ((0x2190, 0x21FF), (0x2300, 0x23FF), (0x25A0, 0x25FF), (0x2600, 0x26FF), (0x2700, 0x27BF),
                (0x2B00, 0x2BFF), (0x1F300, 0x1FAFF))
GLYPH_SINGLES = {0x2026, 0x22EE, 0x22EF, 0x2022, 0x00B7, 0x2261, 0x2630}
CELL_ATTR = r"""role\s*=\s*(?:"gridcell"|'gridcell'|\{\s*['"]gridcell['"]\s*\})"""
JSX_AFTER = re.compile(r"(?:\(|\?|:|&&|\|\||=>|,|\breturn)\s*$")

def _glyph(code: int) -> bool:
    if code in GLYPH_SINGLES or any(lo <= code <= hi for lo, hi in GLYPH_RANGES):
        return True
    return code > 0x7F and unicodedata.category(chr(code)) == "So"

def _brace_end(text: str, start: int, limit: int) -> int:
    depth, i, quote = 0, start, ""
    while i < limit:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "'\"`":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return limit

def _jsx_texts(text: str, start: int, end: int, depth: int = 0) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    i = start
    while i < end and depth < 40:
        c = text[i]
        if c == "<":
            if text.startswith("</", i):
                close = text.find(">", i)
                i = close + 1 if 0 <= close < end else end
                continue
            if text.startswith("<>", i):
                i += 2
                continue
            if re.match(r"<[A-Za-z]", text[i : i + 2]):
                tag_end, _ = _open_tag_end(text, i)
                i = tag_end if tag_end > i else i + 1
                continue
            i += 1
            continue
        if c == "{":
            close = _brace_end(text, i, end)
            out.extend(_expr_texts(text, i + 1, close, depth + 1))
            i = close + 1
            continue
        j = i
        while j < end and text[j] not in "<{":
            j += 1
        out.append(("text", text[i:j]))
        i = j
    return out

def _expr_texts(text: str, start: int, end: int, depth: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    i, question = start, False
    while i < end:
        c = text[i]
        if c in "'\"`":
            j = i + 1
            while j < end and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            head = text[start:i]
            if (not head.strip() or re.search(r"(?:\?|&&|\|\||(?<![\w$.\])])\()\s*$", head)
                    or (question and re.search(r":\s*$", head))):
                out.append(("string", text[i + 1 : j]))
            i = j + 1
            continue
        if c == "?" and text[i + 1 : i + 2] not in (".", "?"):
            question = True
        if c == "<" and (JSX_AFTER.search(text[start:i]) or not text[start:i].strip()):
            if text.startswith("<>", i):
                close = text.find("</>", i)
                close = end if close < 0 or close > end else close
                out.extend(_jsx_texts(text, i + 2, close, depth + 1))
                i = close + 3
                continue
            if re.match(r"<[A-Za-z]", text[i : i + 2]):
                span = _element_span(text, i)
                if span and span[2] <= end:
                    out.extend(_jsx_texts(text, i, span[2], depth + 1))
                    i = span[2]
                    continue
        i += 1
    return out

def _glyphs_of(pieces: list[tuple[str, str]]) -> list[str]:
    found: list[str] = []
    for kind, piece in pieces:
        for ch in piece:
            if ord(ch) > 0x7F and _glyph(ord(ch)):
                found.append(ch)
        pattern = r"&#x([0-9a-fA-F]{2,5});|&#(\d{2,6});" if kind == "text" else r"\\u\{?([0-9a-fA-F]{4,5})\}?()"
        for match in re.finditer(pattern, piece):
            code = int(match.group(1), 16) if match.group(1) else int(match.group(2))
            if _glyph(code):
                found.append(chr(code))
    return list(dict.fromkeys(found))

def _cell_glyphs(text: str) -> list[tuple[str, int]]:
    tags = ("td",) if re.search(r"""role\s*=\s*["'{]\s*['"]?(?:grid|treegrid)\b""", text) else ()
    out: list[tuple[str, int]] = []
    for start in _tags_with(text, CELL_ATTR, tags):
        span = _element_span(text, start)
        if not span:
            continue
        for ch in _glyphs_of(_jsx_texts(text, span[1], span[2])):
            out.append((ch, start))
    return out

def cell_glyph_findings(files: dict[str, str], before: dict[str, str], budget: Budget | None = None,
                        unbounded: bool = False, per_target: bool = False) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for rel, raw in files.items():
        if budget:
            budget.check()
        text = _prepare(raw)
        old = {ch for ch, _ in _cell_glyphs(_prepare(before[rel]))} if rel in before else set()
        hits = [(ch, start) for ch, start in _cell_glyphs(text) if ch not in old]
        if not hits:
            continue
        first = {ch: pos for ch, pos in reversed(hits)}
        for group in ([[ch] for ch in dict.fromkeys(ch for ch, _ in hits)] if per_target
                      else [list(dict.fromkeys(ch for ch, _ in hits))]):
            shown = ", ".join(f"{ch} (U+{ord(ch):04X})" for ch in group)
            found.append({
                "where": f"cell_glyph {rel}:{_line(text, first[group[0]] if per_target else hits[0][1])}",
                "requirement": "(a grid cell's text is exactly its value)",
                "problem": f"{rel} puts decoration glyph(s) {shown} as text inside a grid cell element, so the cell's text "
                           "is its value plus the glyph; a visitor (or a test) reading the cell text finds a different "
                           "value. aria-hidden does not remove text content.",
                "fix": "Keep the cell's text exactly the value: draw the decoration with CSS (a background image, a "
                       "border triangle or a ::after rule) or move the control that carries it outside the cell element; "
                       "a control inside the cell may keep its aria-label but gets no glyph text.",
                "check": "cell_glyph",
            })
    return found if unbounded else found[:MAX_PER_CHECK]

SELECT_SENTENCE = (
    r"combo\s*box(?:es)?\s+(?:labell?ed|named|called)\s+[\"“]([^\"”]+)[\"”]",
    r"[\"“]([^\"”]+)[\"”]\s+combo\s*box",
    r"\bchoos(?:e|es|ing)\s+(?:a|an|the|one)\s+[\w -]{0,30}?\bfrom\s+[\"“]([^\"”]+)[\"”]",
    r"\bchoos(?:e|es|ing)\s+(?:a|an|the)\s+[\"“]([^\"”]+)[\"”]\s+option",
    r"\b(?:choos(?:e|es|ing)|select(?:s|ing)?)\s+[\"“`][^\"”`]+[\"”`]\s+(?:in|from)\s+(?:the\s+)?[\"“]([^\"”]+)[\"”]",
    r"\bselect(?:s|ing)?\s+an?\s+option\s+(?:in|from)\s+(?:the\s+)?[\"“]([^\"”]+)[\"”]",
)

def _other_interaction(requirement: str, label: str) -> bool:
    q = r"[\"“]" + re.escape(label) + r"[\"”]"
    patterns = (
        r"\b(?:click(?:s|ed|ing)?|press(?:es|ed|ing)?|open(?:s|ed|ing)?|tap(?:s|ped)?|expand(?:s|ed|ing)?|toggle(?:s|d)?)\s+(?:on\s+)?(?:the\s+)?" + q,
        q + r"\s+(?:button|toggle|trigger|link|menu|disclosure)",
        r"\b(?:button|toggle|trigger|control)\s+(?:with\s+(?:the\s+)?(?:accessible\s+)?name|named|labell?ed|called|titled)?\s*" + q,
        q + r"\s+(?:opens|shows|reveals|expands|displays|lists)\b",
        q + r"[^.;\n]{0,60}\b(?:opens|reveals|expands)\s+(?:a|an|the|its)\s+(?:list|menu|popup|popover|options)",
    )
    return any(re.search(p, requirement, re.I) for p in patterns)

def stated_selects(requirement: str) -> list[str]:
    labels: list[str] = []
    for pattern in SELECT_SENTENCE:
        for match in re.finditer(pattern, requirement or "", re.I):
            labels.append(match.group(1).strip())
    return [label for label in dict.fromkeys(l for l in labels if l) if not _other_interaction(requirement or "", label)]

def _labelled(text: str, label: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    esc = re.escape(label)
    for match in re.finditer(r"""<ChoiceSelect\b[^>]*?\blabel\s*=\s*(?:"|'|\{\s*['"`])""" + esc + r"""(?:"|'|['"`]\s*\})""", text, re.S):
        out.append(("choice", match.start()))
    for match in re.finditer(r"""aria-label\s*=\s*(?:"|'|\{\s*['"`])""" + esc + r"""(?:"|'|['"`]\s*\})""", text):
        start = _tag_start(text, match.start())
        if start < 0:
            continue
        tag = re.match(r"<([\w.]+)", text[start:]).group(1)
        if re.search(r"""role\s*=\s*["'{]\s*['"]?listbox""", text[start : _open_tag_end(text, start)[0]]):

            out.append(("listbox", start))
            continue
        out.append(("select" if tag == "select" else "choice" if tag == CHOICE_SELECT else "other", start))
    for match in re.finditer(r"<label\b([^>]*)>\s*" + esc + r"\s*</label>", text):
        target = _attr(match.group(1), "htmlFor")
        if target and not target.startswith("{"):
            ref = re.search(r"""\bid\s*=\s*(?:"|'|\{\s*['"])""" + re.escape(target) + r"""(?:"|'|['"]\s*\})""", text)
            if ref:
                start = _tag_start(text, ref.start())
                if start >= 0:
                    tag = re.match(r"<([\w.]+)", text[start:]).group(1)
                    out.append(("select" if tag == "select" else "other", start))
                    continue
        span = _element_span(text, match.start())
        if span and re.search(r"<select\b", text[span[1] : span[2]]):
            out.append(("select", match.start()))
    return out

def stated_select_findings(files: dict[str, str], before: dict[str, str], requirement: str,
                           budget: Budget | None = None, unbounded: bool = False) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for label in stated_selects(requirement):
        if budget:
            budget.check()
        hits: list[tuple[str, str, int]] = []
        kinds: set[str] = set()
        for rel, raw in files.items():
            text = _prepare(raw)
            for kind, pos in _labelled(text, label):
                kinds.add(kind)
                hits.append((rel, kind, pos))

        clear = {rel for rel, kind, _ in hits if kind in ("select", "choice")}
        hits = [h for h in hits if h[0] not in clear]
        if not hits:
            continue

        hits = [h for h in hits if h[1] == "other"] or hits
        rel, _, pos = hits[0]
        if rel in before and _prepare(before[rel]) == _prepare(files[rel]) and any(
                kind in ("other", "listbox") for kind, _ in _labelled(_prepare(before[rel]), label)):
            continue

        text = _prepare(files[rel])
        found.append({
            "where": f"stated_select {rel}:{_line(text, pos)}",
            "requirement": f"(a choose-one control labelled `{label}`)",
            "problem": f"The requirement describes `{label}` as a combo box / a control the visitor chooses an option "
                       f"from, but {rel} labels a custom control with it (not a native <select>); choosing the option "
                       "the way a select is driven fails on it.",
            "fix": f"Render `{label}` as a native <select> with a visible <label htmlFor> reading `{label}` (its "
                   "options as <option> elements with the stated texts), or the shared ChoiceSelect component of the "
                   "conventions; keep the value handling as it is.",
            "check": "stated_select",
        })
    return found if unbounded else found[:MAX_PER_CHECK]

def header_button_patterns(requirement: str) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for sentence in _sentences(requirement):
        if not re.search(r"\b(?:each|every)\s+(?:column\s+)?header\b", sentence, re.I) or not re.search(r"\bbutton", sentence, re.I):
            continue
        for match in re.finditer(r"[\"“]([^\"”<]*)<[^>\"”]+>([^\"”]*)[\"”]", sentence):
            prefix, suffix = match.group(1), match.group(2)
            if prefix.strip():
                out.append((match.group(0).strip("\"“”"), prefix, suffix))
    return list(dict.fromkeys(out))

HEADER_ATTR = r"""role\s*=\s*(?:"columnheader"|'columnheader'|\{\s*['"]columnheader['"]\s*\})"""

def _header_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for start in _tags_with(text, HEADER_ATTR, ("th",)):
        span = _element_span(text, start)
        if span:
            spans.append((span[0], span[2]))
    return spans

def _pattern_buttons(text: str, prefix: str) -> list[int]:
    starts: set[int] = set()
    esc = re.escape(prefix.strip())
    for match in re.finditer(r"<button\b", text):
        span = _element_span(text, match.start())
        if not span:
            continue
        label = _attr(text[span[0] : span[1]], "aria-label")
        name = label if label is not None else _text_of(text[span[1] : span[2]])
        if re.match(esc + r"\s*\{", name or ""):
            starts.add(match.start())
    return sorted(starts)

def _in_header_components(project: Project, depth: int = 3, budget: Budget | None = None) -> set[tuple[str, int]]:
    frontier: list[tuple[str, int, int]] = []
    for rel in project.sources():
        if budget:
            budget.check()
        text = project.text(rel)
        if "columnheader" not in text and "<th" not in text:
            continue
        for start, end in _header_spans(text):
            frontier.extend(project.children(rel, start, end))
    seen: set[tuple[str, int]] = set()
    for _level in range(depth):
        nxt: list[tuple[str, int, int]] = []
        for rel, start, end in frontier:
            if (rel, start) in seen:
                continue
            seen.add((rel, start))
            nxt.extend(project.children(rel, start, end))
        frontier = nxt
    return seen

def header_button_findings(project_dir: Path, files: dict[str, str], before: dict[str, str], requirement: str,
                           all_sources: dict[str, str] | None = None, project: Project | None = None,
                           budget: Budget | None = None, unbounded: bool = False) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    patterns = header_button_patterns(requirement)
    if not patterns:
        return found
    project = project or Project(Path(project_dir), files, all_sources)
    inside: set[tuple[str, int]] | None = None
    for stated, prefix, _suffix in patterns:
        for rel in files:
            if budget:
                budget.check()
            text = project.text(rel)
            if "createPortal" in text:
                continue
            starts = _pattern_buttons(text, prefix)
            if not starts:
                continue
            headers = _header_spans(text)
            outside = [s for s in starts if not any(a <= s < b for a, b in headers)]
            if outside and inside is None:
                inside = _in_header_components(project, budget=budget)
            defs = project.defs(rel)
            outside = [s for s in outside
                       if not any(d_start <= s < d_end and (rel, d_start) in (inside or set()) for _, d_start, d_end in defs)]
            if not outside:
                continue
            if rel in before:
                old = _prepare(before[rel])
                old_headers = _header_spans(old)
                if any(not any(a <= s < b for a, b in old_headers) for s in _pattern_buttons(old, prefix)):
                    continue
            component = next((name for name, d_start, d_end in defs if d_start <= outside[0] < d_end), "") or rel
            found.append({
                "where": f"header_button {rel}:{_line(text, outside[0])}",
                "requirement": f"(each header provides a button `{stated}`)",
                "problem": f"The requirement says each header provides a button named `{stated}`, but {rel} "
                           f"({component}) renders these buttons outside every column header cell (role columnheader / "
                           "th); a visitor (or a test) looking for the button in the header finds none there.",
                "fix": f"Render the `{stated}` button inside its column's header cell (the role=columnheader / th "
                       "element of that column in the grid), one per header, keeping its name and behaviour; remove "
                       "the copy outside the grid.",
                "check": "header_button",
            })
    return found if unbounded else found[:MAX_PER_CHECK]

STATUS_WORD = re.compile(r"\b([A-Z][a-z]{2,})\s+status\b(?!\s+checks?\b)")

I18N = re.compile(r"(?<![\w$.])t\(|\$t\(|\bi18n\b|\bintl\b|\buseTranslation\b|\bFormattedMessage\b")

def stated_statuses(requirement: str) -> list[str]:
    words = list(dict.fromkeys(STATUS_WORD.findall(requirement or "")))
    return words if len(words) >= 2 else []

def _one_sided(text: str, word: str) -> list[int]:
    w = re.escape(word)
    shown = rf"""(?:<[A-Za-z][^<>]*>\s*{w}\s*</[A-Za-z][\w.]*>|['"`]{w}['"`])"""
    empty = r"""(?:null|undefined|false|''|""|``|<>\s*</>)"""
    patterns = (
        rf"\?\s*\(?\s*{shown}\s*\)?\s*:\s*{empty}",
        rf"&&\s*\(?\s*{shown}",
        rf"\?\s*{empty}\s*:\s*\(?\s*{shown}",
    )
    return sorted({m.start() for p in patterns for m in re.finditer(p, text)})

def _says(text: str, word: str) -> bool:
    w = re.escape(word)
    return bool(re.search(rf""">\s*{w}\s*<|['"`]{w}['"`]""", text))

def _imported_names(text: str) -> list[str]:
    names: list[str] = []
    for match in IMPORT.finditer(text):
        names += [n for n in re.findall(r"[A-Za-z_$][\w$]*", match.group(1)) if n not in ("as", "type")]
    return names

def _unresolved_status(text: str, others: list[str]) -> bool:
    if I18N.search(text):
        return True
    lowered = [o.lower() for o in others]
    return any("status" in n.lower() or any(o in n.lower() for o in lowered) for n in _imported_names(text))

def status_word_findings(files: dict[str, str], requirement: str, budget: Budget | None = None,
                         unbounded: bool = False) -> list[dict[str, str]]:
    statuses = stated_statuses(requirement)
    if not statuses:
        return []
    found: list[dict[str, str]] = []

    prepared = {rel: _prepare(raw) for rel, raw in files.items()}
    said = {o for o in statuses if any(_says(t, o) for t in prepared.values())}
    for rel, raw in files.items():
        if budget:
            budget.check()
        text = prepared[rel]
        for word in statuses:
            spots = _one_sided(text, word)
            others = [o for o in statuses if o != word]
            if not spots or any(o in said for o in others) or _unresolved_status(text, others):
                continue
            found.append({
                "where": f"status_word {rel}:{_line(text, spots[0])}",
                "requirement": "(" + ", ".join(f"{s} status" for s in statuses) + ")",
                "problem": f"{rel} shows the word {word} in one state only and nothing in the other state, while the "
                           f"requirement names the states {', '.join(statuses)}: a record in state "
                           f"{' / '.join(others)} shows no status word at all.",
                "fix": f"Where the record shows its status, render the word of the state it is in -- {word} in that "
                       f"state, {' / '.join(others)} otherwise (one visible marker in every state), keeping the existing "
                       f"{word} text exactly as it is.",
                "check": "status_word",
            })
    return found if unbounded else found[:MAX_PER_CHECK]

CHECK_SETTING = {"dialog_close": "dialog_close_check", "dialog_entries": "dialog_entries_buttons_check",
                 "cell_glyph": "cell_glyph_check", "stated_select": "stated_select_check",
                 "header_button": "header_button_check", "status_word": "status_word_check",
                 "count_text": "count_text_check", "stated_textbox": "stated_textbox_check",
                 "menu_role": "menu_role_check"}

def finding_targets(finding: Any) -> list[str] | None:
    if not isinstance(finding, dict):
        return None
    if finding.get("audit_target"):
        return [str(finding["audit_target"])]
    check = str(finding.get("check") or "")
    head = re.match(r"(\w+) (.+):\d+$", str(finding.get("where") or ""))
    if check not in CHECK_SETTING or not head or head.group(1) != check:
        return None
    rel, problem, stated = head.group(2), str(finding.get("problem") or ""), str(finding.get("requirement") or "")
    targets: list[str] = []
    if check == "dialog_close":
        named = re.search(r" renders the dialog (.*) without a button whose accessible name", problem, re.S)
        if named:
            targets = [n or u for n, u in re.findall(r"`([^`]*)`|(\(unnamed\))", named.group(1))]
    elif check == "dialog_entries":
        named = re.search(r"the dialog `(.*?)` shows its entries .*? listbox `(.*?)`", problem, re.S)
        if named:
            targets = [f"{named.group(1)}:{named.group(2)}"]
    elif check == "cell_glyph":
        targets = re.findall(r"\((U\+[0-9A-F]{4,6})\)", problem)
    elif check == "stated_select":
        named = re.search(r"labelled `(.*)`\)$", stated)
        if named:
            targets = [named.group(1)]
    elif check == "header_button":
        named = re.search(r"button `(.*)`\)$", stated)
        if named:
            targets = [named.group(1)]
    elif check == "status_word":
        named = re.search(r" shows the word (.+?) in one state only", problem)
        if named:
            targets = [named.group(1)]
    return [f"{check}:{rel}:{t}" for t in dict.fromkeys(targets)] or None

def _stated_ui(name, files, before, requirement, budget, wide):
    from . import stated_ui_checks

    return getattr(stated_ui_checks, name)(files, before, requirement, budget=budget, unbounded=wide)

def _count_text(files, before, requirement, budget, wide):
    from .count_text_check import count_text_findings

    return count_text_findings(files, before, requirement, budget=budget, unbounded=wide)

def any_check_on(settings: dict[str, Any] | None = None) -> bool:
    if settings is None:
        from .settings import SETTINGS as settings
    return any(bool(settings.get(key, True)) for key in SETTING_KEYS)

def batch_ui_findings(project_dir: Path, node: dict[str, Any], requirement: str, log: Callable[[str], None] = print,
                      settings: dict[str, Any] | None = None, time_cap: float = TIME_CAP_SEC) -> list[dict[str, str]]:
    if settings is None:
        from .settings import SETTINGS as settings
    on = {key: bool(settings.get(key, True)) for key in SETTING_KEYS}
    written = node.get("audit_written")
    if not any(on.values()) or not isinstance(written, list):
        return []
    files = batch_files(Path(project_dir), [str(w) for w in written])
    ledger = RECHECK_LEDGER.get()
    if ledger is not None:
        ledger.setdefault("unreadable", []).extend(
            rel for rel in dict.fromkeys(str(w).replace("\\", "/") for w in written) if _source_file(rel) and rel not in files)
    if not files:
        return []
    raw_before = node.get("audit_written_before")
    before = {str(k).replace("\\", "/"): str(v) for k, v in (raw_before or {}).items()} if isinstance(raw_before, dict) else {}
    budget = Budget(time_cap)
    project = Project(Path(project_dir), files)
    found: list[dict[str, str]] = []
    status: dict[str, str] = ledger.setdefault("batch_ui", {}) if ledger is not None else {}

    value = settings.get("audit_settle_rechecked", True)
    per_target = value.strip().lower() in ("1", "true", "yes", "on") if isinstance(value, str) else bool(value)
    wide = ledger is not None or per_target
    runs = (
        ("dialog_close_check", lambda: dialog_close_findings(files, before, project=project, budget=budget, unbounded=wide,
                                                            per_target=per_target)),
        ("dialog_entries_buttons_check", lambda: dialog_entries_findings(files, before, requirement, budget=budget, unbounded=wide)),
        ("cell_glyph_check", lambda: cell_glyph_findings(files, before, budget=budget, unbounded=wide, per_target=per_target)),
        ("stated_select_check", lambda: stated_select_findings(files, before, requirement, budget=budget, unbounded=wide)),
        ("header_button_check", lambda: header_button_findings(Path(project_dir), files, before, requirement,
                                                               project=project, budget=budget, unbounded=wide)),
        ("status_word_check", lambda: status_word_findings(files, requirement, budget=budget, unbounded=wide)),
        ("count_text_check", lambda: _count_text(files, before, requirement, budget, wide)),
        ("stated_textbox_check", lambda: _stated_ui("stated_textbox_findings", files, before, requirement, budget, wide)),
        ("menu_role_check", lambda: _stated_ui("menu_role_findings", files, before, requirement, budget, wide)),
    )
    for index, (key, run) in enumerate(runs):
        if not on[key]:
            continue
        try:
            got = run()
        except TimeUp:
            skipped = [k for k, _ in runs[index:] if on[k]]
            status.update({k: "skipped" for k in skipped})
            log(f"[audit] batch UI checks: time cap of {time_cap:.0f} s reached -- skipped {skipped}")
            break
        except Exception as exc:
            status[key] = "crashed"
            log(f"[audit] {key} crashed: {exc!r}")
            continue
        status[key] = "ran"
        if per_target:
            seen: dict[str, int] = {}
            for index_in_check, finding in enumerate(got):
                target = (finding_targets(finding) or [""])[0]
                if target:
                    seen[target] = seen.get(target, 0) + 1
                    finding["audit_target"] = target + (f"#{seen[target]}" if seen[target] > 1 else "")
                if ledger is None and index_in_check >= MAX_PER_CHECK:
                    finding["over_check_cap"] = True
        found.extend(got)
    if found:
        log(f"[audit] batch UI checks (v168s): {len(found)} finding(s) on {len(files)} written file(s): "
            + ", ".join(str(f.get('where'))[:80] for f in found[:8]))
    return found
