from __future__ import annotations

import difflib
import posixpath
import re
from dataclasses import dataclass, field

EDIT_MIN_CHARS = 8000

GOD_FILE_LINES = 800

HEADER = re.compile(r"^[ \t]*=== (?P<kind>FILE|EDIT): (?P<path>[^\n=]+?) ===[ \t]*$", re.MULTILINE)

FILE_END = re.compile(r"^[ \t]*=== END(?: OF)? FILE\b[^\n]*$", re.MULTILINE)
EDIT_END = re.compile(r"^[ \t]*=== END(?: OF)? EDIT\b[^\n]*$", re.MULTILINE)
SEARCH_MARK = re.compile(r"^[ \t]*<{5,9} ?SEARCH[ \t]*$")
DIVIDER_MARK = re.compile(r"^[ \t]*={5,9}[ \t]*$")
REPLACE_MARK = re.compile(r"^[ \t]*>{5,9} ?REPLACE[ \t]*$")
SNIPPET_CHARS = 3500
SNIPPET_LINES = 60

GUTTER = re.compile(r"^(?= {0,4}\d)[ \d]{5}\| ")

SUBSTRING_MIN = 8

@dataclass
class Hunk:
    search: str
    replace: str
    number: int

@dataclass
class Block:
    kind: str
    path: str
    body: str = ""
    hunks: list[Hunk] = field(default_factory=list)
    cut: bool = False
    lost_hunk: bool = False

@dataclass
class ParsedAnswer:
    blocks: list[Block]
    dropped: list[tuple[str, str]]
    complete_end: int
    cut_path: str | None = None
    text: str = ""
    edit_headers: int = 0

    @property
    def has_edits(self) -> bool:
        return any(b.kind == "EDIT" for b in self.blocks)

@dataclass
class HunkResult:
    number: int
    status: str
    line: int = 0
    reason: str = ""
    snippet: str = ""

    @property
    def applied(self) -> bool:
        return self.status != "refused"

def norm_path(raw: str) -> str | None:
    path = posixpath.normpath(raw.replace("\\", "/").strip().lstrip("/"))
    if path.startswith("..") or path in ("", "."):
        return None
    return path

def _strip_fences(body: str) -> str:
    body = re.sub(r"^```[a-zA-Z]*\n", "", body)
    return re.sub(r"\n```\s*$", "", body)

def _hunks(segment: str, path: str) -> tuple[list[Hunk], bool, int, list[str]]:
    hunks: list[Hunk] = []
    notes: list[str] = []
    state, search, replace, done_at = "out", [], [], 0
    stray: list[str] = []
    position = 0
    for line in segment.split("\n"):
        position += len(line) + 1
        if state == "out":
            if SEARCH_MARK.match(line):
                state, search, replace = "search", [], []
            elif line.strip() and not line.strip().startswith("```"):
                stray.append(line.strip())
        elif state == "search":
            if DIVIDER_MARK.match(line):
                state = "replace"
            elif SEARCH_MARK.match(line):
                if any(x.strip() for x in search):
                    notes.append(f"the EDIT block of {path}: a `<<<<<<< SEARCH` part without its `=======` line (it starts "
                                 f"{search[0].strip()[:60]!r}) was dropped")
                search = []
            else:
                search.append(line)
        else:
            if REPLACE_MARK.match(line):
                hunks.append(Hunk("\n".join(search), "\n".join(replace), len(hunks) + 1))
                done_at = min(position, len(segment))
                state = "out"
            elif SEARCH_MARK.match(line):

                notes.append(f"the EDIT block of {path}: a pair (SEARCH starting {search[0].strip()[:60] if search else ''!r}) "
                             "had no `>>>>>>> REPLACE` line before the next `<<<<<<< SEARCH`; it was NOT applied")
                state, search, replace = "search", [], []
            else:
                replace.append(line)
    if stray:
        notes.append(f"the EDIT block of {path}: {len(stray)} line(s) outside any SEARCH/REPLACE pair were ignored (the first: "
                     f"{stray[0][:80]!r}); every change must be a `<<<<<<< SEARCH` / `=======` / `>>>>>>> REPLACE` pair")
    return hunks, state != "out", done_at, notes

def parse_answer(text: str, complete: bool = False) -> ParsedAnswer:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"</?think>", "\n", text)
    headers = list(HEADER.finditer(text))
    blocks: list[Block] = []
    dropped: list[tuple[str, str]] = []
    complete_end = 0
    cut_path: str | None = None
    edit_headers = 0
    for index, header in enumerate(headers):
        last = index + 1 >= len(headers)
        limit = headers[index + 1].start() if not last else len(text)
        segment = text[header.end():limit]
        kind = header.group("kind")
        edit_headers += kind == "EDIT"
        path = norm_path(header.group("path"))
        end = (FILE_END if kind == "FILE" else EDIT_END).search(segment)
        cut = end is None and last and not complete
        body = segment[: end.start()] if end else segment
        if cut:
            cut_path = path or header.group("path").strip()
        if path is None:
            dropped.append((header.group("path").strip(), f"the {kind} block for `{header.group('path').strip()}` (a path outside the project)"))
        elif kind == "FILE":
            if cut:
                dropped.append((path, f"the FILE block of {path} (cut off by the output limit before its end; nothing of it was written)"))
            else:
                blocks.append(Block("FILE", path, body=_strip_fences(body.strip("\n")).rstrip() + "\n"))
        else:
            hunks, open_pair, done_at, notes = _hunks(body, path)
            dropped += [(path, note) for note in notes]
            if not hunks and not open_pair:
                dropped.append((path, f"the EDIT block of {path} (it holds no complete `<<<<<<< SEARCH` / `=======` / "
                                      "`>>>>>>> REPLACE` pair)"))
            if open_pair:
                dropped.append((path, f"SEARCH/REPLACE pair {len(hunks) + 1} of the EDIT block of {path} ("
                                      + ("cut off by the output limit; the pairs before it are applied)" if cut
                                         else "it has no `>>>>>>> REPLACE` line)")))
            if hunks:
                blocks.append(Block("EDIT", path, hunks=hunks, cut=cut, lost_hunk=open_pair))
                if cut:
                    complete_end = header.end() + done_at
        if not cut:
            complete_end = header.end() + (end.end() if end else len(segment))
    return ParsedAnswer(blocks, dropped, complete_end, cut_path, text, edit_headers)

TRAILING_MARKERS = frozenset({"=== END FILE ===", "=== END OF FILE ===", "=== END EDIT ===", "=== END OF EDIT ==="})

TRAILING_MARKER_CODE_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".css", ".json")

def strip_trailing_markers(body: str) -> tuple[str, list[str]]:
    lines = body.split("\n")
    cut = len(lines)
    removed: list[str] = []
    i = len(lines) - 1
    while i >= 0 and (not lines[i].strip() or lines[i].strip() in TRAILING_MARKERS):
        if lines[i].strip():
            removed.insert(0, lines[i].strip())
            cut = i
        i -= 1
    if not removed or not any(line.strip() for line in lines[:cut]):
        return body, []
    return "\n".join(lines[:cut]) + "\n", removed

def _lines(text: str) -> tuple[list[str], bool]:
    if text == "":
        return [], False
    trailing = text.endswith("\n")
    return (text[:-1] if trailing else text).split("\n"), trailing

def _join(lines: list[str], trailing: bool) -> str:
    return "\n".join(lines) + ("\n" if trailing and lines else "")

def _windows(hay: list[str], needle: list[str], key) -> list[tuple[int, int]]:
    if not needle:
        return []
    want = [key(x) for x in needle]
    keyed = [key(x) for x in hay]
    n = len(want)
    first = want[0]
    return [(i, i + n) for i in range(len(keyed) - n + 1) if keyed[i] == first and keyed[i:i + n] == want]

def _lead(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]

def _delta(search: list[str], found: list[str]) -> tuple[str, str] | None:
    pairs = [(s, f) for s, f in zip(search, found) if s.strip()]
    if not pairs:
        return None
    s0, f0 = _lead(pairs[0][0]), _lead(pairs[0][1])
    if f0.startswith(s0):
        add = f0[len(s0):]
        return ("add", add) if all(f.rstrip() == add + s.rstrip() for s, f in pairs) else None
    if s0.startswith(f0):
        cut = s0[len(f0):]
        return ("cut", cut) if all(s.startswith(cut) and f.rstrip() == s[len(cut):].rstrip() for s, f in pairs) else None
    return None

def _reindent(replace: list[str], delta: tuple[str, str]) -> list[str]:
    how, prefix = delta
    if not prefix:
        return replace
    if how == "add":
        return [prefix + x if x.strip() else x for x in replace]
    return [x[len(prefix):] if x.startswith(prefix) else x for x in replace]

def _trim_blank(needle: list[str]) -> tuple[list[str], int, int]:
    head = 0
    while head < len(needle) and not needle[head].strip():
        head += 1
    tail = 0
    while tail < len(needle) - head and not needle[len(needle) - 1 - tail].strip():
        tail += 1
    return needle[head:len(needle) - tail], head, tail

def locate(lines: list[str], search: str) -> tuple[str, list[tuple[int, int]]]:
    needle, _ = _lines(search)
    if not any(x.strip() for x in needle):
        return "empty", []
    found = _windows(lines, needle, lambda x: x)
    if found:
        return "exact", found
    core, _head, _tail = _trim_blank(needle)
    for level, key in (("exact", lambda x: x), ("whitespace", lambda x: x.rstrip()), ("indent", lambda x: x.strip())):
        found = _windows(lines, core, key)
        if found:
            return level, found
    return "none", []

def _number(lines: list[str], start: int, end: int) -> str:
    out = [f"{i + 1:>5}| {lines[i]}" for i in range(max(0, start), min(len(lines), end))]
    text = "\n".join(out[:SNIPPET_LINES])
    if len(out) > SNIPPET_LINES:
        text += f"\n      ... ({len(out) - SNIPPET_LINES} more lines)"
    return text[:SNIPPET_CHARS]

WORD = re.compile(r"[A-Za-z_$][\w$]*|\d+|[^\s\w]")

def best_window(lines: list[str], search: str) -> tuple[int, int, float]:
    needle = [x.strip() for x in _lines(search)[0] if x.strip()]
    if not needle or not lines:
        return 0, min(len(lines), 12), 0.0
    n = len(needle)
    tokens = [set(WORD.findall(x)) for x in lines]
    wanted: set[str] = set()
    for x in needle:
        wanted |= set(WORD.findall(x))
    if not wanted:
        return 0, min(len(lines), 12), 0.0
    scored = []
    for i in range(0, max(1, len(lines) - n + 1)):
        seen: set[str] = set()
        for t in tokens[i:i + n]:
            seen |= t
        shared = len(wanted & seen)
        if shared:
            scored.append((shared / len(wanted | seen), i))
    if not scored:
        return 0, min(len(lines), 12), 0.0
    scored.sort(reverse=True)
    target = "\n".join(needle)
    best = (0, min(len(lines), n), 0.0)
    for _score, i in scored[:8]:
        window = "\n".join(x.strip() for x in lines[i:i + n] if x.strip())
        ratio = difflib.SequenceMatcher(None, target, window, autojunk=False).ratio()
        if ratio > best[2]:
            best = (i, min(len(lines), i + n), ratio)
    return best

WORD_CHAR = re.compile(r"[\w$]")

def _substring(lines: list[str], part: str) -> tuple[int, int] | None:
    if "\n" in part or len(part.strip()) < SUBSTRING_MIN:
        return None
    pattern = re.compile("(?=" + re.escape(part) + ")")
    places = [(index, m.start()) for index, line in enumerate(lines) for m in pattern.finditer(line)]
    if len(places) != 1:
        return None
    index, column = places[0]
    line = lines[index]
    before = line[column - 1] if column else ""
    after = line[column + len(part)] if column + len(part) < len(line) else ""
    if WORD_CHAR.match(part[0]) and before and WORD_CHAR.match(before):
        return None
    if WORD_CHAR.match(part[-1]) and after and WORD_CHAR.match(after):
        return None
    return index, column

RANK = {"exact": 0, "whitespace": 1, "indent": 2, "substring": 3}

def _refuse_changed(hunk: Hunk, lines: list[str], baseline: list[str], level: str,
                    consumed: list[tuple[int, int, int]]) -> HunkResult | None:
    base_level, spots = locate(baseline, hunk.search)
    if not spots or len(spots) > 1 or base_level not in RANK:
        return None
    if level in RANK and RANK[level] <= RANK[base_level]:
        return None
    s, e = spots[0]
    hit = next((n for (a, b, n) in consumed if a < e and s < b), None)
    who = f"pair {hit} of this block" if hit is not None else "an earlier pair of this answer"
    return HunkResult(hunk.number, "refused", line=s + 1, reason=(
        f"it overlaps {who}, which already changed these lines; put both changes into ONE pair, or write this one against "
        "the text the earlier pair left. Current lines:"), snippet=_number(lines, s - 3, e + 3))

def _apply_one(lines: list[str], hunk: Hunk, consumed: list[tuple[int, int, int]], baseline: list[str]) -> tuple[list[str] | None, HunkResult]:
    level, found = locate(lines, hunk.search)
    if level == "empty":
        return None, HunkResult(hunk.number, "refused", reason="its SEARCH part is empty: quote the existing lines the new code goes "
                                "next to (and repeat them in the REPLACE part)")
    if len(found) > 1:
        shown = "\n".join(_number(lines, s - 2, e + 2) + "\n      ..." for s, e in found[:3])
        return None, HunkResult(hunk.number, "refused", line=found[0][0] + 1,
                                reason=f"its SEARCH lines match {len(found)} places (lines {', '.join(str(s + 1) for s, _e in found[:6])}); "
                                       "add neighbouring lines until they match exactly one place",
                                snippet=shown)
    place = None
    if not found and "\n" not in hunk.replace:
        place = _substring(lines, hunk.search.strip("\n"))
        if place is not None:
            level = "substring"
    if level != "exact" or not found:
        refusal = _refuse_changed(hunk, lines, baseline, level if (found or place) else "none", consumed)
        if refusal is not None:
            return None, refusal
    if place is not None:
        index, column = place
        part = hunk.search.strip("\n")
        new = list(lines)
        new[index] = lines[index][:column] + hunk.replace + lines[index][column + len(part):]
        return new, HunkResult(hunk.number, "noop" if new == lines else "substring", line=index + 1)
    if not found:
        start, end, ratio = best_window(lines, hunk.search)
        hint = (f"the most similar lines of the current file are {start + 1}-{end} (similarity {ratio:.0%}):"
                if ratio >= 0.3 else "no similar lines were found; the current file starts like this:")
        return None, HunkResult(hunk.number, "refused", line=start + 1,
                                reason="its SEARCH lines are not in the current file; " + hint,
                                snippet=_number(lines, start - 3, end + 3) if ratio >= 0.3 else _number(lines, 0, 15))
    start, end = found[0]
    needle, _ = _lines(hunk.search)
    replacement = _lines(hunk.replace)[0] if hunk.replace != "" else []
    if lines[start:end] == needle:
        core = needle
    else:

        core, head, tail = _trim_blank(needle)
        take_head = 0
        while take_head < head and start - take_head - 1 >= 0 and not lines[start - take_head - 1].strip():
            take_head += 1
        take_tail = 0
        while take_tail < tail and end + take_tail < len(lines) and not lines[end + take_tail].strip():
            take_tail += 1
        _r, rhead, rtail = _trim_blank(replacement)
        drop_head, drop_tail = min(head - take_head, rhead), min(tail - take_tail, rtail)
        replacement = replacement[drop_head:len(replacement) - drop_tail]
        start, end = start - take_head, end + take_tail
        core = [""] * take_head + core + [""] * take_tail
    if level == "indent":
        delta = _delta(core, lines[start:end])
        if delta is None:
            return None, HunkResult(hunk.number, "refused", line=start + 1,
                                    reason="its SEARCH lines match only with a different indentation on each line; copy them "
                                           "exactly. Current lines:", snippet=_number(lines, start - 2, end + 2))
        replacement = _reindent(replacement, delta)
    new = lines[:start] + replacement + lines[end:]
    return new, HunkResult(hunk.number, "noop" if new == lines else level, line=start + 1)

def _unnumber(text: str) -> tuple[str, list[int]] | None:
    lines = text.split("\n")
    filled = [x for x in lines if x.strip()]
    if not filled or not all(GUTTER.match(x) for x in filled):
        return None
    numbers = [int(GUTTER.match(x).group(0).strip(" |")) for x in filled]
    return "\n".join(GUTTER.sub("", x, count=1) for x in lines), numbers

def _with_line_ends(original: str, text: str) -> str:
    old = original.split("\n")
    new = text.split("\n")
    crlf_old = [x.endswith("\r") for x in old]
    plain_old = [x[:-1] if x.endswith("\r") else x for x in old]
    majority = sum(crlf_old[:-1]) * 2 > max(1, len(old) - 1)
    ends = [majority] * len(new)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, plain_old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                ends[j1 + k] = crlf_old[i1 + k]
        elif tag == "replace":

            for k in range(j2 - j1):
                ends[j1 + k] = crlf_old[min(i1 + k, i2 - 1)]
    out = [line + ("\r" if ends[k] and k < len(new) - 1 else "") for k, line in enumerate(new)]
    return "\n".join(out)

def apply_hunks(original: str | None, hunks: list[Hunk], baseline: str | None = None) -> tuple[str | None, list[HunkResult]]:
    results: list[HunkResult] = []
    if original is None:
        if hunks and not hunks[0].search.strip():
            created = hunks[0].replace.replace("\r\n", "\n").rstrip("\n") + "\n"
            results.append(HunkResult(hunks[0].number, "created", line=1))
            rest_text, rest = apply_hunks(created, hunks[1:])
            return rest_text, results + rest
        for hunk in hunks:
            results.append(HunkResult(hunk.number, "refused", reason="the file does not exist: write a new file whole, in a "
                                      "`=== FILE:` block"))
        return None, results
    text = original.replace("\r\n", "\n")
    lines, trailing = _lines(text)
    base_lines = _lines((baseline if baseline is not None else original).replace("\r\n", "\n"))[0]
    consumed: list[tuple[int, int, int]] = []
    changed = False
    for raw in hunks:
        hunk = Hunk(raw.search.replace("\r\n", "\n"), raw.replace.replace("\r\n", "\n"), raw.number)
        new, result = _apply_one(lines, hunk, consumed, base_lines)
        if new is None and result.reason.startswith("its SEARCH lines are not in"):
            plain = _unnumber(hunk.search)
            if plain is not None:
                search, numbers = plain
                level, spots = locate(lines, search)

                if len(spots) == 1 and numbers == list(range(numbers[0], numbers[0] + len(numbers))) \
                        and spots[0][0] <= numbers[0] - 1 < spots[0][1]:
                    replace = _unnumber(hunk.replace)
                    retry = Hunk(search, replace[0] if replace is not None else hunk.replace, hunk.number)
                    again, second = _apply_one(lines, retry, consumed, base_lines)
                    if again is not None:
                        new, result, hunk = again, second, retry
        if new is not None:
            level, spots = locate(base_lines, hunk.search)
            if level == "exact" and len(spots) == 1:
                consumed.append((spots[0][0], spots[0][1], hunk.number))
            changed = changed or new != lines
            lines = new
        results.append(result)
    if not changed:
        return original, results
    out = _join(lines, trailing or not lines)
    if "\r\n" in original:
        out = _with_line_ends(original, out)
    return out, results

def refusal_text(path: str, results: list[HunkResult]) -> str:
    refused = [r for r in results if not r.applied]
    if not refused:
        return ""
    applied = [r.number for r in results if r.applied]
    parts = [f"EDIT {path}: pair(s) {', '.join(str(r.number) for r in refused)} NOT applied"
             + (f" (pair(s) {', '.join(str(n) for n in applied)} applied)" if applied else " (nothing of this block was applied)") + "."]
    for r in refused:
        parts.append(f"- pair {r.number}: {r.reason}" + (("\n" + r.snippet) if r.snippet else ""))
    return "\n".join(parts)

def size_note(text: str) -> str:
    if len(text) <= EDIT_MIN_CHARS:
        return ""
    lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    note = f" [{lines} lines, {len(text)} chars: change it with EDIT blocks"
    if lines > GOD_FILE_LINES:
        note += f"; over {GOD_FILE_LINES} lines: move a self-contained part into a new file before adding to it"
    return note + "]"

def pending_text(path: str, results: list[HunkResult]) -> str:
    if not results:
        return ""
    parts = [f"EDIT {path}: {len(results)} change(s) NOT applied -- redo them as {len(results)} pair(s), in this order:"]
    for index, r in enumerate(results, 1):
        parts.append(f"- change {index}: {r.reason}" + (("\n" + r.snippet) if r.snippet else ""))
    return "\n".join(parts)

def clip(items: list[str], limit: int) -> str:
    out: list[str] = []
    used = 0
    rest: list[str] = []
    for item in items:
        if not rest and used + len(item) + 1 <= limit:
            out.append(item)
            used += len(item) + 1
        else:
            rest.append(item.split("\n", 1)[0][:160])
    if rest:
        out.append(f"... and {len(rest)} more not shown in full: " + " | ".join(rest))
    return "\n".join(out)
