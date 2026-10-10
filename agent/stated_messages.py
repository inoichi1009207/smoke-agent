from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

CHECK = "stated_messages"
MAX_FINDINGS = 8
SKIP_DIRS = {"node_modules", "dist", "build", "test-e2e", "tests", "test", "__tests__", "coverage", ".git",
             ".arc-test-db", "test-results", "playwright-report", "e2e-support", "__mocks__"}
FRONT_SUFFIXES = (".tsx", ".ts", ".jsx", ".js", ".json")
BACK_SUFFIXES = (".js", ".cjs", ".mjs", ".ts", ".json")

QUOTE = re.compile(r"`([^`\n]{1,200})`|\"([^\"\n]{1,200})\"|“([^”\n]{1,200})”|「([^」\n]{1,200})」|『([^』\n]{1,200})』")

FILL = (r"(?:the|a|an|both|all|exact|exactly|generic|visible|visibly|application|error|errors|validation|inline|field|"
        r"success|successful|confirmation|following|same|corresponding|fixed|clear|clearly|specific|readable|warning|"
        r"warnings|status|message|messages|reason|notice|alert|toast|banner|feedback|text|explicit|single|one|also|"
        r"then|again|only|still|immediately|failure|rejection|plain|short|informative|new|two|three|these|its|their|"
        r"literal|verbatim|wording|server|stated|appropriate|applicable|corresponding|prominent|red|green|"
        r"error-message|success-message|in-line|non-sensitive|human-readable|user-facing|on-screen)")
MSG_NOUN = r"(?:message|messages|error|errors|reason|notice|warning|alert|toast|banner|feedback|text)"
VERB = r"(?:display|displays|displayed|displaying|show|shows|shown|showed|showing|see|sees|seen|prompt|prompts|prompted|report|reports|reported|receive|receives|received)"
CONTAINS = r"(?:containing|that\s+contains|which\s+contains|contains|including|that\s+includes|includes)"
TAIL_VERB = re.compile(r"\b" + VERB + r"\s+(?:" + FILL + r"\s+){0,6}(?P<c>" + CONTAINS + r"\s+)?$", re.I)
TAIL_WITH = re.compile(r"\b(?:rejected|refused|fails?|failed|denied|blocked|aborted|stops?|stopped|errors?\s+out|responds?|answers?)"
                       r"\s+with\s+(?:" + FILL + r"\s+){0,5}$", re.I)
TAIL_MSG_WITH = re.compile(r"\bwith\s+(?:" + FILL + r"\s+){0,4}" + MSG_NOUN + r"\s*:?\s*$", re.I)
TAIL_NOUN_IS = re.compile(r"\b" + MSG_NOUN + r"\s+(?:(?P<c>contains|containing|includes|including)|is|reads|reading|says|saying|states|stating)\s+(?:the\s+)?$", re.I)
TAIL_PASSIVE_START = re.compile(r"(?:^|[,:]\s*|\b(?:and|then|until|that|so|where|when)\s+)(?:" + FILL + r"\s+){0,4}$", re.I)
TAIL_PASSIVE_NOUN = re.compile(r"\b(?:the|an?)\s+(?:" + FILL + r"\s+){0,3}" + MSG_NOUN + r"\s*$", re.I)
AFTER_PASSIVE = re.compile(r"^\s*(?:(?:is|are)\s+(?:then\s+|also\s+|immediately\s+|visibly\s+)?(?:displayed|shown|visible|reported|rendered)\b|appears?\b)", re.I)
NEGATION = re.compile(r"\b(?:not|no|never|without|neither|nor|cannot|can't|doesn't|don't|isn't|won't|instead\s+of|rather\s+than|no\s+longer|absent|hidden|hides?)\b", re.I)
EXAMPLE = re.compile(r"\bfor\s+example\b|\be\.g\.|\bsuch\s+as\b|\bfor\s+instance\b|\bexamples?\b|\bconcrete\s+values?\b|\bsample\s+values?\b"

                     r"|\beither\b|\bmay\b|\bmight\b|\boptional(?:ly)?\b|\bif\s+(?:it\s+)?chooses?\b", re.I)
CONTROL_AFTER = re.compile(
    r"^\s*(?:(?:" + FILL + r")\s+)?(?:fields?|buttons?|links?|tabs?|headings?|textbox(?:es)?|combobox(?:es)?|checkbox(?:es)?|"
    r"radios?|switch(?:es)?|menus?|menu\s?items?|items?|options?|dialogs?|columns?|pages?|sections?|labels?|entr(?:y|ies)|"
    r"roles?|values?|rows?|cards?|panels?|inputs?|controls?|icons?|badges?|titles?|states?|placeholders?|tooltips?|cells?|"
    r"sheets?|workbooks?|names?|accounts?|organizations?|repositor(?:y|ies)|teams?|users?|areas?|regions?|lists?|"
    r"tables?|toolbars?|navigation|nav|breadcrumbs?|menubar|filters?|selectors?|pickers?|keys?)\b", re.I)
INPUT_TAIL = re.compile(
    r"\b(?:enters?|entered|entering|types?|typed|typing|fills?|filled|filling|inputs?|pastes?|pasted|submits?|submitted|"
    r"submitting|search(?:es)?|searching|searched|filters?|chooses?|chose|selects?|selected|selecting|picks?|picked|"
    r"clicks?|clicked|clicking|press(?:es)?|pressed|activates?|activated|opens?|opened|named|names?|called|labell?ed|"
    r"labels?|titled|titles?|buttons?|links?|tabs?|fields?|headings?|options?|checkbox(?:es)?|textbox(?:es)?|"
    r"combobox(?:es)?|menu\s?items?|menus?|columns?|rows?|roles?|items?|entry|entries|placeholder|header|values?|"
    r"seed(?:ed)?|seeds|account|accounts|users?|username|usernames|email|e-mail|password|passwords|organizations?|org|"
    r"repositor(?:y|ies)|repo|teams?|branch(?:es)?|files?|issues?|milestones?|workbooks?|sheets?|cells?|formulas?|"
    r"identifiers?|names|members?|owners?|admins?|reviewers?|commits?|tags?|projects?|records?|value|as|to|from|into|"
    r"by|for|called|code|candidate|keyword|query|term|string)\s+(?:" + FILL + r"\s+){0,3}$", re.I)
AFTER_INPUT = re.compile(r"^\s*(?:in|into|as\s+the|for\s+the)\s", re.I)

def _finding(where: str, requirement: str, problem: str, fix: str) -> dict[str, str]:
    return {"where": where, "requirement": requirement, "problem": problem, "fix": fix}

def _norm(text: str) -> str:
    text = (text.replace("\\'", "'").replace('\\"', '"').replace("\\n", " ").replace("&apos;", "'").replace("&#39;", "'")
            .replace("&quot;", '"').replace("&amp;", "&").replace("&nbsp;", " ").replace(" ", " "))
    text = text.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "′": "'"}))
    return re.sub(r"\s+", " ", text).strip()

def _compact(text: str) -> str:
    return re.sub(r"\s+", "", _norm(text)).lower()

def _message_shape(text: str) -> bool:
    text = text.strip()
    if len(text) < 4 or len(text) > 160:
        return False
    cjk = len(re.findall(r"[一-鿿]", text))
    if " " not in text and cjk < 4:
        return False
    first = re.search(r"[A-Za-z一-鿿]", text)
    if not first or first.group(0).islower():
        return False
    if re.search(r"[<>{}=$\\|]|https?:|\S@\S|(?:^|\s)/|/\S|\.\w{2,4}$|^\W", text):
        return False
    return True

def _leaves(tree: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        children = node.get("children") if isinstance(node.get("children"), list) else []
        children = [c for c in children if isinstance(c, dict)]
        if children:
            for child in children:
                walk(child)
        elif str(node.get("id") or "").strip() not in ("", "ROOT"):
            found.append(node)

    walk(tree)
    return found

def _all_nodes(tree: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            out.append(node)
            stack.extend(c for c in (node.get("children") or []) if isinstance(c, dict))
    return out

def _steps(leaf: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for scenario in leaf.get("scenarios") or []:
        if not isinstance(scenario, dict):
            continue
        last = "GIVEN"
        for step in scenario.get("steps") or []:
            if not isinstance(step, dict):
                continue
            keyword = str(step.get("keyword") or step.get("type") or "").strip().upper()
            if keyword in ("GIVEN", "WHEN", "THEN"):
                last = keyword
            out.append((last, str(step.get("content") or "")))
    return out

def _sentences(text: str) -> list[tuple[str, list[str]]]:
    quotes: list[str] = []

    def keep(match: re.Match) -> str:
        quotes.append(next(g for g in match.groups() if g is not None))
        return f"\x01{len(quotes) - 1}\x02"

    masked = QUOTE.sub(keep, text)
    out: list[tuple[str, list[str]]] = []
    start = 0
    for match in re.finditer(r"[.!?;](?=\s)|\n\s*\n|\n(?=\s*[-*#])", masked):
        before = masked[max(0, match.start() - 5): match.end()]
        if re.search(r"(?:\be\.g|\bi\.e|\betc|\bvs|\bNo|\b[A-Z])\.$", before):
            continue
        out.append((masked[start: match.end()], quotes))
        start = match.end()
    out.append((masked[start:], quotes))
    return [(s, q) for s, q in out if "\x01" in s]

def _quote_spans(sentence: str) -> list[tuple[int, int, int]]:
    return [(m.start(), m.end(), int(m.group(1))) for m in re.finditer(r"\x01(\d+)\x02", sentence)]

def _plain(text: str) -> str:
    return re.sub(r"\x01\d+\x02", " Q ", text)

def _clause(before: str) -> str:
    return re.split(r"[,;:(]", before)[-1]

def _context_exclusions(text: str, action_step: bool) -> set[str]:
    out: set[str] = set()
    for sentence, quotes in _sentences(text):
        for start, end, k in _quote_spans(sentence):
            literal = _norm(quotes[k])
            if action_step:
                out.add(literal)
                continue
            before = _plain(sentence[:start]).lower()
            after = _plain(sentence[end:])
            if INPUT_TAIL.search(before) or CONTROL_AFTER.match(after) or AFTER_INPUT.match(after):
                out.add(literal)
            elif re.search(r"\bseed|pre-?provision|pre-?defined|pre-?existing|already\s+exists?\b", sentence, re.I) and not TAIL_VERB.search(before):
                out.add(literal)
    return out

def _messages_in(text: str) -> list[tuple[str, bool, str]]:
    found: list[tuple[str, bool, str]] = []
    for sentence, quotes in _sentences(text):
        plain_sentence = _plain(sentence)
        if EXAMPLE.search(plain_sentence):
            continue
        spans = _quote_spans(sentence)

        runs: list[tuple[list[tuple[int, int, int]], bool]] = []
        for span in spans:
            if runs:
                gap = sentence[runs[-1][0][-1][1]: span[0]]
                if re.fullmatch(r"\s*,?\s*(?:or|and/or)\s+", gap, re.I):
                    runs[-1] = (runs[-1][0] + [span], True)
                    continue
                if re.fullmatch(r"\s*(?:,\s*(?:and\s+)?|and\s+|as\s+well\s+as\s+|plus\s+)", gap, re.I):
                    runs[-1] = (runs[-1][0] + [span], runs[-1][1])
                    continue
            runs.append(([span], False))
        for run, alternative in runs:
            if alternative:
                continue
            first, last = run[0], run[-1]
            before = _plain(sentence[: first[0]])
            after = _plain(sentence[last[1]:])
            if re.search(r"\beither\s+$", before, re.I):
                continue
            contains = False
            verb = TAIL_VERB.search(before)
            noun_is = TAIL_NOUN_IS.search(before)
            governing = verb or noun_is or TAIL_WITH.search(before) or TAIL_MSG_WITH.search(before)
            if verb:
                contains = bool(verb.group("c"))
            elif noun_is:
                contains = bool(noun_is.group("c"))
            elif governing:
                pass
            elif AFTER_PASSIVE.match(after) and (TAIL_PASSIVE_START.search(before) or TAIL_PASSIVE_NOUN.search(before)):

                if re.match(r"^\s*(?:is|are)\s+(?:not|never)\b|^\s*(?:never|no\s+longer)\b", after, re.I):
                    continue
            else:
                continue

            start = governing.start() if governing else len(before)
            if NEGATION.search(" ".join(_clause(before[:start]).split()[-2:])):
                continue
            if CONTROL_AFTER.match(after) or AFTER_INPUT.match(after):
                continue
            for _, _, k in run:
                literal = _norm(quotes[k])
                if _message_shape(literal):
                    found.append((literal, contains, _norm(plain_sentence.replace(" Q ", " … "))[:200]))
    return found

def stated_messages(requirement: str, leaf_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
    import yaml
    tree = yaml.safe_load(requirement)
    if not isinstance(tree, dict):
        return []
    wanted = {str(i) for i in leaf_ids} if leaf_ids is not None else None
    excluded: set[str] = set()
    for node in _all_nodes(tree):
        excluded |= _context_exclusions(str(node.get("description") or ""), False)
        for keyword, content in _steps(node):
            excluded |= _context_exclusions(content, keyword in ("GIVEN", "WHEN"))
    leaves = _leaves(tree)
    quoted_in: dict[str, set[str]] = {}
    for leaf in leaves:
        for text in [str(leaf.get("description") or "")] + [c for _, c in _steps(leaf)]:
            for match in QUOTE.finditer(text):
                quoted_in.setdefault(_norm(next(g for g in match.groups() if g is not None)), set()).add(str(leaf.get("id")))
    out: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for leaf in leaves:
        leaf_id = str(leaf.get("id") or "")
        if wanted is not None and leaf_id not in wanted:
            continue
        texts = [(str(leaf.get("description") or ""), False)] + [(c, True) for k, c in _steps(leaf) if k == "THEN"]

        evidence = sorted(q for q, ids in quoted_in.items() if ids == {leaf_id} and len(q) >= 6
                          and (" " in q or len(re.findall(r"[一-鿿]", q)) >= 3) and re.search(r"[A-Za-z一-鿿]", q))
        for text, then in texts:
            for message, contains, sentence in _messages_in(text):
                if message in excluded:
                    continue
                if message in seen:
                    seen[message]["then"] = seen[message]["then"] or (then and seen[message]["leaf"] == leaf_id)
                    if leaf_id not in seen[message]["leaves"]:
                        seen[message]["leaves"].append(leaf_id)
                    continue
                item = {"leaf": leaf_id, "message": message, "contains": contains, "sentence": sentence, "then": then,
                        "evidence": [e for e in evidence if e != message], "leaves": [leaf_id]}
                seen[message] = item
                out.append(item)
    return out

class Atom:
    __slots__ = ("text", "rel", "line", "side", "kind", "keys", "pattern", "joined")

    def __init__(self, text: str, rel: str, line: int, side: str, kind: str, keys=None, pattern=None,
                 joined: bool = False) -> None:
        self.text, self.rel, self.line, self.side, self.kind = text, rel, line, side, kind
        self.keys, self.pattern, self.joined = keys, pattern, joined

def _source_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    if not root.is_dir():
        return out
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if name.endswith(suffixes) and not re.search(r"\.(?:test|spec)\.", name) and not name.endswith(".d.ts"):
                out.append(Path(folder) / name)
    return out

def _scan(code: str) -> tuple[str, list[tuple[int, int, str, list[str] | None]]]:
    out = list(code)
    literals: list[tuple[int, int, str, list[str] | None]] = []
    i, n = 0, len(code)
    prev = ""

    def template(j: int) -> tuple[int, list[str]]:
        parts, buf = [], []
        while j < n:
            ch = code[j]
            if ch == "\\":
                buf.append(code[j: j + 2])
                j += 2
                continue
            if ch == "`":
                parts.append("".join(buf))
                return j + 1, parts
            if ch == "$" and j + 1 < n and code[j + 1] == "{":
                parts.append("".join(buf))
                buf = []
                depth, j = 1, j + 2
                while j < n and depth:
                    c = code[j]
                    if c in "'\"":
                        k = j + 1
                        while k < n and code[k] != c and code[k] != "\n":
                            k += 2 if code[k] == "\\" else 1
                        j = k + 1
                        continue
                    if c == "`":
                        j, _ = template(j + 1)
                        continue
                    depth += 1 if c == "{" else -1 if c == "}" else 0
                    j += 1
                continue
            buf.append(ch)
            j += 1
        return n, parts + ["".join(buf)]

    while i < n:
        ch = code[i]
        nxt = code[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/" and not (i > 0 and code[i - 1] == ":"):
            j = code.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if ch == "/" and nxt == "*":
            j = code.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        if ch == "/" and (prev == "" or prev in "(,=:[!&|?{};+-*%<>~^" or re.search(r"\b(?:return|typeof|case|in|of)\s*$", code[max(0, i - 8): i])):
            j, klass = i + 1, False
            while j < n and code[j] != "\n":
                c = code[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "[":
                    klass = True
                elif c == "]":
                    klass = False
                elif c == "/" and not klass:
                    break
                j += 1
            if j < n and code[j] == "/":
                i = j + 1
                prev = "a"
                continue
        if ch in "'\"":
            j = i + 1
            while j < n and code[j] != ch and code[j] != "\n":
                j += 2 if code[j] == "\\" else 1
            if j < n and code[j] == ch:
                literals.append((i + 1, j, "str", None))
                i = j + 1
                prev = "a"
                continue
            i += 1
            continue
        if ch == "`":
            j, parts = template(i + 1)
            literals.append((i + 1, j - 1, "tpl", parts))
            i = j
            prev = "a"
            continue
        if not ch.isspace():
            prev = ch
        i += 1
    return "".join(out), literals

def _template_regex(parts: list[str]) -> re.Pattern | None:
    statics = [_norm(p) for p in parts]
    if len(parts) < 2 or len(re.findall(r"[A-Za-z一-鿿]", "".join(statics))) < 6:
        return None
    return re.compile(r"(?:.+?)".join(re.escape(s) for s in statics), re.DOTALL)

def _backend_keys(code: str, start: int) -> list[str] | None:
    head = code[max(0, start - 300): start - 1].rstrip()
    match = re.search(r"([A-Za-z_$][\w$]*)\s*:\s*$", head)
    if match:
        return [match.group(1)]
    match = re.search(r"([A-Za-z_$][\w$]*)\s*:\s*\[[^\[\]]*$", head)
    if match:
        return [match.group(1)]
    match = re.search(r"([A-Za-z_$][\w$]*)\.(?:push|unshift)\(\s*$", head)
    if match:
        var = match.group(1)
        keys = {var}
        keys.update(re.findall(r"([A-Za-z_$][\w$]*)\s*:\s*" + re.escape(var) + r"\b", code))
        return sorted(keys)
    return None

def _collect(project: Path) -> tuple[list[Atom], str, str]:
    atoms: list[Atom] = []
    front_code: list[str] = []
    all_code: list[str] = []
    for side, root, suffixes in (("front", project / "frontend" / "src", FRONT_SUFFIXES),
                                 ("back", project / "backend" / "src", BACK_SUFFIXES)):
        for path in _source_files(root, suffixes):
            try:
                code = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if len(code) > 1_500_000:
                continue
            rel = path.relative_to(project).as_posix()
            if path.suffix == ".json":
                for match in re.finditer(r'"((?:[^"\\\n]|\\.)*)"', code):
                    atoms.append(Atom(match.group(1), rel, code.count("\n", 0, match.start()) + 1, side, "str"))
                all_code.append(code)
                if side == "front":
                    front_code.append(code)
                continue
            stripped, literals = _scan(code)
            all_code.append(stripped)
            if side == "front":
                front_code.append(stripped)
            for start, end, kind, parts in literals:
                line = code.count("\n", 0, start) + 1
                if kind == "str":
                    keys = _backend_keys(stripped, start) if side == "back" else None

                    joined = bool(re.search(r"\+\s*$", stripped[max(0, start - 21): start - 1])
                                  or re.match(r"\s*\+", stripped[end + 1: end + 21]))
                    atoms.append(Atom(code[start:end], rel, line, side, "str", keys=keys, joined=joined))
                else:
                    pattern = _template_regex(parts or [])
                    atoms.append(Atom("${}".join(parts or []), rel, line, side, "tpl", pattern=pattern))
                    if parts and len(parts) == 1:
                        atoms.append(Atom(parts[0], rel, line, side, "str",
                                          keys=_backend_keys(stripped, start) if side == "back" else None))
            if path.suffix in (".tsx", ".jsx"):
                for match in re.finditer(r"(?<=[>}])([^<>{}]+)(?=[<{])", stripped):
                    text = match.group(1)
                    if re.search(r"[A-Za-z一-鿿]", text) and "=>" not in text and ";" not in text:

                        joined = stripped[match.start() - 1] == "}" or stripped[match.end(): match.end() + 1] == "{"
                        atoms.append(Atom(text, rel, code.count("\n", 0, match.start()) + 1, side, "jsx", joined=joined))
    return atoms, "\n".join(front_code), "\n".join(all_code)

def _dynamic_rendering(front: str) -> bool:
    return bool(re.search(r"Object\.(?:values|entries)\(\s*(?:[\w$]+\??\.)*(?:err\w*|error\w*|data|body|response|res|json|payload|result|problem)\b"
                          r"|JSON\.stringify\(\s*(?:[\w$]+\??\.)*(?:err\w*|error\w*|data|body|response|payload)\b", front))

def _strips_full_stop(front: str) -> bool:
    return bool(re.search(r"replace\(\s*/[^/\n]*\\\.[^/\n]*\$/", front))

FIX_PUNCT = ("Make the text the page shows for this case exactly the stated message, character for character (no added or "
             "dropped full stop); change the literal at its source (backend answer or frontend), and keep the page "
             "showing that text for this case.")
FIX_UNREAD = ("Show the stated message on the page for this case: read the response field that carries it (or send it in the "
              "field the frontend already shows) and render it visibly next to the form.")
FIX_MISSING = ("Implement the stated failure/notice case so the page visibly shows exactly this text when it happens (the "
               "server's 4xx answer may carry it; the page must render it verbatim).")

def _loc(atom: Atom) -> str:
    return f"{atom.rel}:{atom.line}"

def _judge(item: dict[str, Any], atoms: list[Atom], front: str, everything: str,
           diagnostics: list[str]) -> tuple[int, dict[str, str]] | None:
    message, leaf = item["message"], item["leaf"]
    target = _norm(message)
    requirement = f'{leaf}: "{message}"'
    exact = [a for a in atoms if a.kind != "tpl" and _norm(a.text) == target]
    if any(a.side == "front" for a in exact):
        return None
    if any(a.kind == "tpl" and a.pattern is not None and a.pattern.fullmatch(target) for a in atoms):
        return None
    if exact:
        if _dynamic_rendering(front):
            return None
        unread = []
        for atom in exact:
            if atom.keys is None:
                return None
            if any(re.search(r"(?<![\w$])" + re.escape(key) + r"(?![\w$])", front) for key in atom.keys):
                return None
            unread.append(atom)
        keys = sorted({k for a in unread for k in a.keys or []})
        return 1, _finding(
            f"{CHECK}: {_loc(unread[0])}", requirement,
            f'The requirement ({leaf}) states the page displays "{message}". The text exists only in the backend '
            f"({', '.join(_loc(a) for a in unread[:3])}) as the response field {', '.join('`' + k + '`' for k in keys)}, "
            f"and no frontend file reads that field, so the page cannot show it.", FIX_UNREAD)

    variants = {target + p for p in (".", "!", "。")}
    if target[-1:] in (".", "!", "。"):
        variants.add(target[:-1].rstrip())
    punct = [a for a in atoms if a.kind != "tpl" and _norm(a.text) in variants]
    if punct:
        if item["contains"] or _strips_full_stop(front):
            diagnostics.append(f"{leaf}: '{message}' only as '{_norm(punct[0].text)}' (contains-statement or stripped): not reported")
            return None
        shown = _norm(punct[0].text)
        return 0, _finding(
            f"{CHECK}: {_loc(punct[0])}", requirement,
            f'The requirement ({leaf}) states the exact text "{message}" ({item["sentence"][:120]}). No source holds that '
            f'text; the literal at {", ".join(_loc(a) for a in punct[:3])} is "{shown}", so the page shows a different '
            f"text than the stated one.", FIX_PUNCT)
    lowered = target.lower()
    if any(_norm(a.text).lower() == lowered for a in atoms if a.kind != "tpl"):
        diagnostics.append(f"{leaf}: '{message}' only with another letter case: not reported")
        return None
    holder = [a for a in atoms if a.kind != "tpl" and lowered in _norm(a.text).lower()]
    if holder:
        diagnostics.append(f"{leaf}: '{message}' only inside a longer literal ({_loc(holder[0])}): not reported")
        return None
    pieces = [a for a in atoms if a.joined and len(re.sub(r"\W", "", a.text)) >= 4 and _norm(a.text).lower() in lowered]
    if pieces:
        diagnostics.append(f"{leaf}: '{message}' could be joined from pieces ({_loc(pieces[0])}): not reported")
        return None
    if _compact(message) in _compact(everything):
        diagnostics.append(f"{leaf}: '{message}' occurs in the code in a form the reader does not follow: not reported")
        return None
    built = None
    for other in item["evidence"]:
        if other in item.get("messages_of_leaf", ()):
            continue
        hits = [a for a in atoms if a.side == "front" and _norm(a.text) == other]
        if hits:
            built = hits[0]
            break
    if built is None:
        diagnostics.append(f"{leaf}: '{message}' absent, but no text only this leaf quotes is in the frontend (not built?): not reported")
        return None

    siblings = [m for m in item.get("messages_of_leaf", ()) if m != message]
    if not item.get("then") and not any(_norm(a.text) == _norm(m) for m in siblings for a in atoms if a.kind != "tpl"):
        diagnostics.append(f"{leaf}: '{message}' absent; stated in the description only and no other message of the leaf exists: not reported")
        return None
    return 2, _finding(
        f"{CHECK}: {_loc(built)}", requirement,
        f'The requirement ({leaf}) states the page displays "{message}" ({item["sentence"][:120]}). This text occurs '
        f'nowhere in the frontend or backend sources, although the leaf\'s page exists (it shows "{_norm(built.text)}" '
        f"at {_loc(built)}).", FIX_MISSING)

def stated_message_findings(requirement: str, project_dir: Path, log=print,
                            leaf_ids: Iterable[str] | None = None) -> list[dict[str, str]]:
    log = log or (lambda *_: None)
    try:
        project = Path(project_dir)
        items = stated_messages(requirement or "", leaf_ids)
        if not items:
            log(f"[audit] {CHECK}: no stated messages in the requirement")
            return []
        by_leaf: dict[str, set[str]] = {}
        for item in items:
            by_leaf.setdefault(item["leaf"], set()).add(item["message"])
        atoms, front, everything = _collect(project)
        if not atoms:
            log(f"[audit] {CHECK}: no sources under {project}")
            return []
        diagnostics: list[str] = []
        ranked: list[tuple[int, int, dict[str, str]]] = []
        for index, item in enumerate(items):
            item["messages_of_leaf"] = by_leaf.get(item["leaf"], set())
            verdict = _judge(item, atoms, front, everything, diagnostics)
            if verdict and leaf_ids is None and all(item.get("leaves") or [""]):
                from .finding_owner import tag
                tag(verdict[1], item["leaves"])
            if verdict:
                ranked.append((verdict[0], index, verdict[1]))
        ranked.sort(key=lambda r: (r[0], r[1]))
        found = [f for _, _, f in ranked[:MAX_FINDINGS]]
        log(f"[audit] {CHECK}: {len(items)} stated message(s) checked, {len(ranked)} finding(s)"
            + (f", {len(ranked) - len(found)} over the cap" if len(ranked) > len(found) else ""))
        for line in diagnostics[:20]:
            log(f"[audit] {CHECK} (diagnostic) {line}")
        return found
    except Exception as exc:
        log(f"[audit] {CHECK} crashed: {exc!r}")
        return []
