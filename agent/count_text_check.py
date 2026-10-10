from __future__ import annotations

import re

from .batch_ui_checks import MAX_PER_CHECK, Budget, _brace_end, _line, _prepare, _sentences

CHECK = "count_text"
SETTING_KEY = "count_text_check"

NOT_NOUNS = {
    "the", "a", "an", "and", "or", "of", "to", "its", "their", "his", "her", "this", "that", "these", "those", "same",
    "new", "old", "updated", "current", "total", "exact", "correct", "visible", "final", "initial", "whose", "each",
    "every", "no", "any", "with", "without", "by", "in", "on", "at", "for", "from", "as", "is", "are", "be", "was",
    "item", "items", "row", "rows", "line", "lines", "word", "words", "character", "characters", "page", "pages",
    "retry", "max", "maximum", "minimum", "min", "head", "body", "view", "views", "result", "results", "match",
    "matches", "unread", "selected", "displayed", "shown", "remaining", "per", "its", "own",
}
PHRASE = re.compile(r"\b([A-Za-z][a-z]{2,})\s+counts?\b")
TYPE_AND_COUNT = re.compile(r"\bthe\s+([a-z]{3,})\s+(?:type|kind|name)\s+and\s+(?:its\s+)?count\b", re.I)
OPTIONAL = re.compile(r"\bmay\b|\boptional(?:ly)?\b|\bif any\b", re.I)

def stated_counted_nouns(requirement: str) -> list[str]:
    nouns: list[str] = []
    for sentence in _sentences(requirement or ""):
        if OPTIONAL.search(sentence):
            continue
        for match in list(PHRASE.finditer(sentence)) + list(TYPE_AND_COUNT.finditer(sentence)):
            noun = match.group(1).lower()
            if noun in NOT_NOUNS:
                continue
            if noun.endswith("s") and not noun.endswith("ss"):
                noun = noun[:-1]
            nouns.append(noun)
    return list(dict.fromkeys(nouns))

def plural(noun: str) -> str:
    if re.search(r"(?:s|x|z|ch|sh)$", noun):
        return noun + "es"
    if re.search(r"[^aeiou]y$", noun):
        return noun[:-1] + "ies"
    return noun + "s"

_PATH = r"[A-Za-z_$][\w$]*(?:\??\.[A-Za-z_$][\w$]*|\??\.?\[[^\[\]{}]{1,40}\])*"
_COUNT_EXPR = re.compile(rf"(?:!!\s*)?({_PATH})(?:\s*\?\?\s*0)?$")

def _segments(path: str) -> list[str]:
    return [s for s in re.split(r"\??\.|\[|\]", path) if s]

def _about(path: str, noun: str, others: list[str]) -> bool:
    segs = _segments(path)
    if not segs:
        return False
    last = segs[-1]
    low = path.lower()
    if any(o != noun and o in low for o in others):
        return False
    if last == "length":
        return len(segs) >= 2 and noun in segs[-2].lower()
    if last.lower() in ("count", "counts", "total", "num", "number"):
        return last.lower() in ("count", "counts") or noun in low
    words = [w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", last)]
    if not words or words[-1] not in ("count", "counts"):
        return False
    head = "".join(words[:-1]).replace("_", "")
    return head in (noun, plural(noun), "num" + noun, "num" + plural(noun))

def _expr_path(expr: str) -> str | None:
    match = _COUNT_EXPR.match(expr.strip())
    return match.group(1) if match else None

BARE = re.compile(r">\s*\{\s*([^{}<>]{1,80}?)\s*\}\s*</")
GUARD = re.compile(r"\{\s*(!!\s*)?(" + _PATH + r")\s*(?:(>|!==?|>=)\s*(\d+)\s*)?(&&|\?)")
EMPTY_TAIL = re.compile(r":\s*(?:null|undefined|false|''|\"\"|``|<>\s*</>)\s*\)?\s*$")

def _bare_spots(text: str, noun: str, others: list[str]) -> list[int]:
    spots = []
    for match in BARE.finditer(text):
        path = _expr_path(match.group(1))
        if path and _about(path, noun, others):
            spots.append(match.start())
    return spots

def _zero_hidden_spots(text: str, noun: str, others: list[str]) -> list[int]:
    spots = []
    for match in GUARD.finditer(text):
        op, value = match.group(3), match.group(4)
        if op and not ((op == ">" and value == "0") or (op in ("!=", "!==") and value == "0") or (op == ">=" and value == "1")):
            continue
        if not _about(match.group(2), noun, others) or _segments(match.group(2))[-1] == "length":
            continue
        if match.group(5) == "?":
            end = _brace_end(text, match.start(), min(len(text), match.start() + 4000))
            if end >= len(text) or not EMPTY_TAIL.search(text[match.start() + 1 : end]):
                continue
        spots.append(match.start())
    return spots

def _had_before(before_text: str | None, text: str, pos: int) -> bool:
    if not before_text:
        return False
    line_start = text.rfind("\n", 0, pos) + 1
    line_end = text.find("\n", pos)
    snippet = " ".join(text[line_start : line_end if line_end >= 0 else len(text)].split())
    return bool(snippet) and snippet in " ".join(_prepare(before_text).split())

def count_text_findings(files: dict[str, str], before: dict[str, str], requirement: str,
                        budget: Budget | None = None, unbounded: bool = False) -> list[dict[str, str]]:
    nouns = stated_counted_nouns(requirement)
    if not nouns:
        return []
    found: list[dict[str, str]] = []
    for rel, raw in files.items():
        if budget:
            budget.check()
        text = _prepare(raw)
        low = text.lower() + " " + rel.lower()
        for noun in nouns:
            if noun not in low:
                continue
            bare = [p for p in _bare_spots(text, noun, nouns) if not _had_before(before.get(rel), text, p)]
            hidden = [p for p in _zero_hidden_spots(text, noun, nouns) if not _had_before(before.get(rel), text, p)]
            if not bare and not hidden:
                continue
            first = min(bare + hidden)
            shapes = []
            if bare:
                shapes.append(f"renders the {noun} count as a bare number (line {', '.join(str(_line(text, p)) for p in bare[:4])})")
            if hidden:
                shapes.append(f"hides the {noun} count at zero (line {', '.join(str(_line(text, p)) for p in hidden[:4])})")
            found.append({
                "where": f"{CHECK} {rel}:{_line(text, first)}",
                "requirement": f"({noun} count)",
                "problem": f"{rel} " + " and ".join(shapes) + f", while the requirement names the {noun} count: the "
                           f"number alone (or nothing at zero) is no readable count of {plural(noun)}.",
                "fix": f"Render the {noun} count as one text '<n> {noun}' / '<n> {plural(noun)}' (singular for one) in a "
                       f"single element, e.g. {{`${{n}} ${{n === 1 ? '{noun}' : '{plural(noun)}'}}`}} -- also at "
                       f"zero ('0 {plural(noun)}'); keep the type label and every existing control, name and test hook "
                       f"exactly as they are.",
                "check": CHECK,
            })
    return found if unbounded else found[:MAX_PER_CHECK]
