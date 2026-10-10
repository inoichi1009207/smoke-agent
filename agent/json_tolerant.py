from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

MAX_REPAIRS = 60
MAX_CUTS = 400

def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    return raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

def _object_span(text: str) -> str:
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    return text[min(starts):] if starts else text

def strip_comments_and_trailing_commas(text: str) -> str:
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if c in "]}":

            j = len(out) - 1
            while j >= 0 and out[j] in " \t\r\n":
                j -= 1
            if j >= 0 and out[j] == ",":
                del out[j]
        out.append(c)
        i += 1
    return "".join(out)

def _error(text: str) -> tuple[Any, json.JSONDecodeError | None]:
    try:
        value, end = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError as error:
        return None, error
    rest = text[end:]
    if rest.strip():
        return None, json.JSONDecodeError("Extra data", text, end + len(rest) - len(rest.lstrip()))
    return value, None

def _prose_tail(text: str) -> tuple[Any, bool]:
    try:
        value, end = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        return None, False
    rest = text[end:]
    return value, bool(rest.strip()) and not re.search(r'[{}\[\]"]', rest)

def _progress(text: str) -> int:
    try:
        _value, end = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError as error:
        return error.pos
    return len(text) + 1 if not text[end:].strip() else end

def _prev_nonspace(text: str, pos: int) -> int:
    j = pos - 1
    while j >= 0 and text[j] in " \t\r\n":
        j -= 1
    return j

def _structural(text: str, p: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    k = _prev_nonspace(text, p)
    q = k
    while q >= 0 and text[q] in "}],\t\r\n ":
        q -= 1
    if q > 0 and text[q] == '"':
        for m in range(1, 5):
            if q - m < 0 or text[q - m] not in "}]":
                break
            out.append((f"moved a closing quote left over {m} closer(s) at char {q}", text[:q - m] + '"' + text[q - m:q] + text[q + 1:]))
    if k > 0 and text[k] == '"':
        opening = text.rfind('"', 0, k)
        if opening >= 0 and opening + 1 < k and text[opening + 1] in "{[":
            out.append((f"swapped an opening quote and bracket at char {opening}",
                        text[:opening] + text[opening + 1] + '"' + text[opening + 2:]))
    out.append((f"inserted a missing '}}' at char {p}", text[:p] + "}" + text[p:]))
    out.append((f"inserted a missing ']' at char {p}", text[:p] + "]" + text[p:]))
    return out

def _candidates(text: str, error: json.JSONDecodeError) -> list[tuple[str, str]]:
    p, msg = error.pos, error.msg
    out: list[tuple[str, str]] = []
    if msg.startswith(("Expecting ',' delimiter", "Expecting property name")):
        out += _structural(text, p)
    if msg.startswith("Invalid control character"):

        j = p
        while j > 0 and text[j - 1] in " \t\r":
            j -= 1
        run_start = j
        while run_start > 0 and text[run_start - 1] in "}],":
            run_start -= 1
        for q in range(j, run_start - 1, -1):
            out.append((f"closed an unterminated string at char {q}", text[:q] + '"' + text[q:]))
        escaped = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}.get(text[p], "\\u%04x" % ord(text[p]))
        out.append((f"escaped a raw control character at char {p}", text[:p] + escaped + text[p + 1:]))
    elif msg.startswith("Expecting ',' delimiter") or msg.startswith("Expecting ':' delimiter"):
        k = _prev_nonspace(text, p)
        if msg.startswith("Expecting ','"):
            out.append((f"inserted a missing comma at char {p}", text[:p] + "," + text[p:]))
        if k >= 0 and text[k] == '"':

            out.append((f"escaped an inner quote at char {k}", text[:k] + '\\"' + text[k + 1:]))
    elif msg.startswith("Expecting property name enclosed in double quotes"):
        bare = re.match(r"([A-Za-z_$][\w$-]*)(\s*:)", text[p:])
        if bare:
            out.append((f"quoted a bare key at char {p}", text[:p] + '"' + bare.group(1) + '"' + text[p + len(bare.group(1)):]))
        single = re.match(r"'([^'\n]*)'(\s*:)", text[p:])
        if single:
            out.append((f"double-quoted a single-quoted key at char {p}", text[:p] + '"' + single.group(1) + '"' + text[p + len(single.group(0)) - len(single.group(2)):]))
        k = _prev_nonspace(text, p)
        if k >= 0 and text[k] == ",":
            out.append((f"dropped a trailing comma at char {k}", text[:k] + text[k + 1:]))
    elif msg.startswith("Expecting value"):
        k = _prev_nonspace(text, p)
        if k >= 0 and text[k] == "," and p < len(text) and text[p] in "]}":
            out.append((f"dropped a trailing comma at char {k}", text[:k] + text[k + 1:]))
        single = re.match(r"'([^'\n\"]*)'", text[p:])
        if single:
            out.append((f"double-quoted a single-quoted value at char {p}", text[:p] + '"' + single.group(1) + '"' + text[p + len(single.group(0)):]))
    if msg.startswith("Expecting ',' delimiter"):

        run = re.match(r"[^,}\]\n\"]+", text[p:])
        if run:
            out.append((f"dropped a non-JSON token {run.group(0).strip()[:20]!r} at char {p}", text[:p] + text[p + len(run.group(0)):]))
    if p < len(text):
        out.append((f"dropped char {p} ({text[p]!r})", text[:p] + text[p + 1:]))
    return out

def _repair_loop(text: str, notes: list[str]) -> str:
    for _ in range(MAX_REPAIRS):
        _value, error = _error(text)
        if error is None:
            return text
        if error.pos >= len(text.rstrip()):
            return text
        best: tuple[int, str, str] | None = None
        for description, candidate in _candidates(text, error):
            score = _progress(candidate)

            if score > error.pos + 1 and (best is None or score > best[0]):
                best = (score, description, candidate)
        if best is None:
            return text
        notes.append(best[1])
        text = best[2]
    return text

def _cut_and_close(text: str, limit: int) -> tuple[Any, str] | None:
    stack: list[str] = []
    cuts: list[tuple[int, str, int]] = []
    in_string = False
    i = 0
    limit = min(limit, len(text))
    while i < limit:
        c = text[i]
        if in_string:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
        elif c in "{[":
            stack.append("}" if c == "{" else "]")
        elif c in "}]":
            if not stack or stack[-1] != c:
                break
            stack.pop()
            cuts.append((i + 1, "".join(reversed(stack)), len(stack)))
        i += 1
    if not cuts:
        return None
    ordered = sorted(cuts[-MAX_CUTS:], key=lambda cut: (cut[2] > 2, -cut[0]))
    for position, closers, _depth in ordered:
        head = text[:position].rstrip()
        candidate = head + closers
        candidate = strip_comments_and_trailing_commas(candidate)
        value, error = _error(candidate)
        if error is None:
            return value, f"cut after char {position} and closed {len(closers)} open bracket(s)"
    return None

def _loads_whole(text: str, notes: list[str]) -> Any:
    value, error = _error(text)
    if error is None:
        return value
    value, prose = _prose_tail(text)
    if prose:
        notes.append("ignored prose after the document")
        return value
    cleaned = strip_comments_and_trailing_commas(text)
    try:
        value, end = json.JSONDecoder(strict=False).raw_decode(cleaned)
        if cleaned[end:].strip():

            raise json.JSONDecodeError("ended early", cleaned, end)
        if cleaned != text:
            notes.append("removed comments / trailing commas")
        if _error(cleaned)[1] is not None:
            notes.append("accepted raw control characters inside strings")
        return value
    except json.JSONDecodeError:
        pass
    repaired = _repair_loop(cleaned, notes)
    value, error = _error(repaired)
    if error is None:
        return value
    closed = _cut_and_close(repaired, error.pos)
    if closed is not None:
        notes.append(closed[1])
        return closed[0]
    return None

TOP_KEY = re.compile(r'^[ \t]{0,4}"([A-Za-z_][\w-]*)"\s*:\s*', re.MULTILINE)

def _top_level_starts(text: str) -> set[int] | None:
    if not text.startswith("{"):
        return None
    starts: set[int] = set()
    stack: list[str] = []
    in_string = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if in_string:
            if c == "\\":
                i += 2
                continue
            if c == "\n":
                break
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            if len(stack) == 1:
                starts.add(i)
            in_string = True
        elif c in "{[":
            stack.append("}" if c == "{" else "]")
        elif c in "}]":
            if not stack or stack[-1] != c:
                break
            stack.pop()
            if not stack:
                break
        i += 1
    return starts

def _per_key(text: str, keys: tuple[str, ...], notes: list[str]) -> dict[str, Any]:
    starts = _top_level_starts(text) or set()
    top = [m for m in TOP_KEY.finditer(text) if m.start(1) - 1 in starts]
    found: dict[str, Any] = {}
    for index, match in enumerate(top):
        if keys and match.group(1) not in keys:
            continue

        end = top[index + 1].start() if index + 1 < len(top) else len(text)
        segment = text[match.end():end].rstrip()
        if index + 1 == len(top):
            segment = segment.removesuffix("}").rstrip()
        segment = segment.removesuffix(",").rstrip()
        own: list[str] = []
        value = _loads_whole(segment, own)
        if value is None:
            notes.append(f"key {match.group(1)!r} unreadable")
            continue
        if match.group(1) not in found:
            found[match.group(1)] = value
            if own:
                notes.append(f"key {match.group(1)!r}: " + "; ".join(own[:5]))
    return found

def tolerant_loads(raw: str, keys: tuple[str, ...] = ()) -> tuple[Any, list[str]]:
    notes: list[str] = []
    text = _object_span(_strip_fences(str(raw or "")))
    if not text:
        return None, ["empty"]
    value = _loads_whole(text, notes)
    if isinstance(value, dict):
        if keys and notes:

            missing = tuple(k for k in keys if k not in value)
            if missing:
                extra = _per_key(text, missing, [])
                for key, item in extra.items():
                    value[key] = item
                if extra:
                    notes.append(f"recovered key(s) {sorted(extra)} after the defect one by one")
        return value, notes
    if value is not None:
        return value, notes
    if text.lstrip().startswith("{"):
        found = _per_key(text, keys, notes)
        if found:
            notes.append(f"read {len(found)} top-level key(s) one by one")
            return found, notes
    return None, notes + ["unreadable"]

@lru_cache(maxsize=16)
def _cached(raw: str, keys: tuple[str, ...]) -> tuple[str | None, tuple[str, ...]]:
    value, notes = tolerant_loads(raw, keys)
    return (None if value is None else json.dumps(value, ensure_ascii=False)), tuple(notes)

def cached_tolerant_loads(raw: str, keys: tuple[str, ...] = ()) -> tuple[Any, list[str]]:
    text, notes = _cached(raw, keys)
    return (None if text is None else json.loads(text)), list(notes)
