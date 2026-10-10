from __future__ import annotations

import re
from typing import Any

from .batch_ui_checks import MAX_PER_CHECK, Budget, _element_span, _labelled, _line, _prepare, _sentences

SETTING_KEYS = ("stated_textbox_check", "menu_role_check")

Q_OPEN = "\"“'"
Q_CLOSE = "\"”'"
QUOTED = re.compile(r"[\"“]([^\"“”\n]{1,80})[\"”]")

def _brace_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    stack: list[int] = []
    i, n, quote = 0, len(text), ""
    while i < n:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "'\"`" and stack and not (i > 0 and (text[i - 1].isalnum())):
            quote = c
        elif c == "{":
            stack.append(i)
        elif c == "}" and stack:
            spans.append((stack.pop(), i))
        i += 1
    return spans

def _skip_jsx(text: str, i: int, end: int) -> int:
    if text.startswith("<>", i):
        depth, j = 0, i
        while j < end:
            if text.startswith("</>", j):
                depth -= 1
                j += 3
                if depth == 0:
                    return j
                continue
            if text.startswith("<>", j):
                depth += 1
                j += 2
                continue
            j += 1
        return -1
    span = _element_span(text, i)
    return span[2] if span and span[2] <= end else -1

def _top_level(text: str, start: int, end: int) -> list[tuple[str, int]] | None:
    ops: list[tuple[str, int]] = []
    depth, i, quote = 0, start, ""
    while i < end:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
            i += 1
            continue
        if c in "'\"`" and not (i > start and text[i - 1].isalnum()):
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c == "<" and (text[i + 1 : i + 2].isalpha() or text.startswith("<>", i)):
            nxt = _skip_jsx(text, i, end)
            if nxt < 0:
                return None
            i = nxt
            continue
        elif depth == 0 and c == ";":
            return None
        elif depth == 0 and text.startswith("&&", i):
            ops.append(("&&", i))
            i += 2
            continue
        elif depth == 0 and c == "?" and text[i + 1 : i + 2] not in ("?", ".") and text[i - 1 : i] != "?":
            ops.append(("?", i))
        elif depth == 0 and c == ":":
            ops.append((":", i))
        i += 1
    return ops

def _conditionals(text: str, pos: int, spans: list[tuple[int, int]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for start, end in sorted((s for s in spans if s[0] < pos < s[1]), key=lambda s: -s[0]):
        ops = _top_level(text, start + 1, end)
        if not ops:
            continue
        qs = [p for k, p in ops if k == "?"]
        if qs:
            q = qs[0]
            colon = next((p for k, p in ops if k == ":" and p > q), -1)
            if colon < 0:
                continue
            branch = "then" if pos < colon else "else"
            other = text[colon + 1 : end] if branch == "then" else text[q + 1 : colon]
            out.append({"kind": "ternary", "cond": text[start + 1 : q], "branch": branch, "other": other,
                        "start": start})
            continue
        ands = [p for k, p in ops if k == "&&" and p < pos]
        if ands:
            out.append({"kind": "and", "cond": text[start + 1 : ands[-1]], "branch": "and", "other": "",
                        "start": start})
    return out

TEXTBOX = re.compile(r"\btext\s*(?:box|field|area)\s+labell?ed\s+[\"“]([^\"“”\n]{1,60})[\"”]", re.I)
CONTAINER = re.compile(r"\b(?:dialog|panel|popover|drawer|modal)\b", re.I)

STATE = re.compile(r"(?i)(?:view|edit|exist|readonly|read_only|saved|stored|persist)|\bmode\b")
TEXTBOX_TAGS = ("input", "textarea")

def stated_textboxes(requirement: str) -> list[str]:
    labels: list[str] = []
    for sentence in _sentences(requirement or ""):
        if not CONTAINER.search(sentence):
            continue
        labels += [m.group(1).strip() for m in TEXTBOX.finditer(sentence)]
    return [label for label in dict.fromkeys(labels) if label]

def _textbox_hits(text: str, label: str) -> list[int]:
    out: list[int] = []
    for kind, pos in _labelled(text, label):
        tag = re.match(r"<([\w.]+)", text[pos:])
        if tag and tag.group(1) == "label":
            span = _element_span(text, pos)
            inner = re.search(r"<(?:input|textarea)\b", text[span[1] : span[2]]) if span else None
            if not inner:
                continue
            pos = span[1] + inner.start()
            tag = re.match(r"<([\w.]+)", text[pos:])
        if tag and tag.group(1) in TEXTBOX_TAGS:
            if re.search(r"""type\s*=\s*["'{]\s*['"]?(?:checkbox|radio|hidden|submit|button)""",
                         text[pos : pos + 400].split(">")[0]):
                continue
            out.append(pos)
    return out

def _one_state(text: str, pos: int, spans: list[tuple[int, int]]) -> dict[str, Any] | None:
    for cond in _conditionals(text, pos, spans):
        if not STATE.search(cond["cond"]):
            continue
        if cond["kind"] == "ternary":
            other = cond["other"]
            if re.search(r"<(?:input|textarea)\b", other):
                continue
            if not re.search(r"<[A-Za-z]", other):
                continue
        return cond
    return None

def _textbox_shapes(text: str, label: str) -> list[int]:
    hits = _textbox_hits(text, label)
    if not hits:
        return []
    spans = _brace_spans(text)
    shaped = [pos for pos in hits if _one_state(text, pos, spans)]
    return shaped if len(shaped) == len(hits) else []

def stated_textbox_findings(files: dict[str, str], before: dict[str, str], requirement: str,
                            budget: Budget | None = None, unbounded: bool = False) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for label in stated_textboxes(requirement):
        for rel, raw in files.items():
            if budget:
                budget.check()
            text = _prepare(raw)
            if not _textbox_hits(text, label):
                continue
            shaped = _textbox_shapes(text, label)
            if not shaped:
                continue
            if rel in before and _textbox_shapes(_prepare(before[rel]), label):
                continue
            found.append({
                "where": f"stated_textbox {rel}:{_line(text, shaped[0])}",
                "requirement": f"(a dialog / panel contains a text box labelled `{label}`)",
                "problem": f"The requirement says the dialog / panel contains a text box labelled `{label}`, but {rel} "
                           f"renders that text box only in one branch of a view / edit state; in the other state "
                           "(an existing item) the dialog shows plain text, so finding the text box by its label "
                           "and reading its value fails there.",
                "fix": f"Render the text box labelled `{label}` in every state, holding the stored value as its value "
                       "(the existing item's text prefilled); keep the other controls of each state as they are.",
                "check": "stated_textbox",
            })
            break
    return found if unbounded else found[:MAX_PER_CHECK]

MENU_WORD = re.compile(r"(?<![\w-])menu\b", re.I)

def _is_menu_name(sentence: str, start: int, end: int, value: str) -> bool:
    if MENU_WORD.search(value):
        return True
    after = sentence[end : end + 12]
    return bool(re.match(r"\s*(?:menu|button|tab|dialog|page)\b", after, re.I))

def stated_menu_entries(requirement: str) -> list[str]:
    entries: list[str] = []
    for sentence in _sentences(requirement or ""):
        if not MENU_WORD.search(sentence):
            continue
        for match in QUOTED.finditer(sentence):
            value = match.group(1).strip()
            if value and not _is_menu_name(sentence, match.start(), match.end(), value):
                entries.append(value)
    return list(dict.fromkeys(entries))

ENTRY_TAGS = ("Link", "NavLink", "a", "button", "li", "div", "span")

def _entry_hits(text: str, entry: str) -> list[int]:
    out: list[int] = []
    for match in re.finditer(r">\s*" + re.escape(entry) + r"\s*</([\w.]+)>", text):
        tag = match.group(1)
        if tag not in ENTRY_TAGS:
            continue
        start = text.rfind("<" + tag, 0, match.start() + 1)
        if start < 0:
            continue
        span = _element_span(text, start)
        if span and span[1] == match.start() + 1:
            out.append(start)
    return out

def _menu_shape(text: str, pos: int, spans: list[tuple[int, int]]) -> bool:
    conds = _conditionals(text, pos, spans)
    if not conds:
        return False
    floor = conds[0]["start"]
    around: list[str] = []
    for match in re.finditer(r"<([A-Za-z][\w.]*)", text[floor:pos]):
        start = floor + match.start()
        span = _element_span(text, start)
        if span and span[2] > pos:
            around.append(text[start : span[1]])
    if not around:
        return False
    for open_tag in around:
        tag = re.match(r"<([\w.]+)", open_tag).group(1)
        if tag[0].isupper() or tag in ("select", "menu", "dialog", "nav"):
            return False
        if re.search(r"""role\s*=\s*\{?\s*["'](?:menu|menubar|listbox|dialog|alertdialog|navigation)["']""", open_tag):
            return False
        if re.search(r"aria-modal|role\s*=\s*\{(?!\s*['\"])", open_tag):
            return False
    return True

def _menu_shapes(text: str, entry: str) -> list[int]:
    hits = _entry_hits(text, entry)
    if not hits:
        return []
    spans = _brace_spans(text)
    return [pos for pos in hits if _menu_shape(text, pos, spans)]

def menu_role_findings(files: dict[str, str], before: dict[str, str], requirement: str,
                       budget: Budget | None = None, unbounded: bool = False) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[tuple[str, int]] = set()
    for entry in stated_menu_entries(requirement):
        for rel, raw in files.items():
            if budget:
                budget.check()
            text = _prepare(raw)
            shaped = _menu_shapes(text, entry)
            if not shaped:
                continue
            if rel in before and _menu_shapes(_prepare(before[rel]), entry):
                continue
            line = _line(text, shaped[0])
            if (rel, line) in seen:
                continue
            seen.add((rel, line))
            found.append({
                "where": f"menu_role {rel}:{line}",
                "requirement": f"(a menu containing `{entry}`)",
                "problem": f"The requirement calls the list holding `{entry}` a menu, but {rel} renders `{entry}` in a "
                           "dropdown with no element of role menu around it; locating the entry inside the menu by "
                           "its role fails.",
                "fix": "Give the open dropdown role=menu; keep entries' kinds (a link stays a link, a button stays a "
                       "button) and their names, handlers and the trigger as they are.",
                "check": "menu_role",
            })
    return found if unbounded else found[:MAX_PER_CHECK]
