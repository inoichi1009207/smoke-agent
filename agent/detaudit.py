from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .breadcrumbs import breadcrumb_findings
from .display_forms import inconsistent_display_forms
from .activation_probe import activation_findings, activation_statements, enabled as activation_enabled
from .activation_probe import probe as activation_probe, probe_routes as activation_routes
from .commit_probe import commit_findings, commit_statements, enabled as commit_enabled
from .commit_probe import probe as commit_probe, probe_job as commit_job
from .commit_probe import key_statements, keys_enabled, keys_findings, keys_job, keys_probe
from .structure_probe import enabled as structure_enabled, probe as structure_probe, probe_job as structure_job
from .structure_probe import structure_findings, structure_statement
from .interaction_probes import interaction_findings
from .visual_checks import visual_findings
from .entry_checks import aria_nodes, covers_item_list, entry_findings, link_list_cover, statements as entry_statements
from .inert_controls import inert_named_controls
from .nav_edges import missing_stated_edges
from .response_shapes import response_shape_findings
from .stated_controls import CONTROL_ROLES, extract_stated_controls, fam, requirement_chunks, seed_values, stated_control_problems
from .test_author import describe_node
from .visual import mandatory_texts, script_of

QUOTED = re.compile(r"`([^`\n]{1,60})`|\"([^\"\n]{1,60})\"|“([^”\n]{1,60})”|「([^」\n]{1,60})」|『([^』\n]{1,60})』")

LINK_WORDS = re.compile(r"链接|\blinks?\b", re.IGNORECASE)
BUTTON_WORDS = re.compile(r"按钮|\bbuttons?\b", re.IGNORECASE)
UI_CONTEXT = re.compile(
    r"名称|显示|文案|文本|链接|按钮|标题|提示|消息|选项|入口|标签|占位|已存在|预置|系统中"
    r"|\b(?:names?|named|labell?ed|labels?|shows?|displays?|text|links?|buttons?|headings?|titles?|messages?|options?|placeholder|test ?id|contains?|exists?|existing|verified)\b",
    re.IGNORECASE,
)
ARIA_LINE =re.compile(r"^\s*-\s+(?P<role>[a-z]+)\s+\"(?P<name>(?:[^\"\\]|\\.)*)\"")

DUPLICATE_ROLES = {"textbox", "combobox", "checkbox", "radio", "spinbutton", "searchbox"}
SOURCE_SUFFIXES = {".tsx", ".ts", ".jsx", ".js", ".json", ".html", ".sql"}
MAX_FINDINGS_PER_CHECK = 8

def _per_check() -> int:
    from .batch_ui_checks import RECHECK_LEDGER

    return MAX_FINDINGS_PER_CHECK if RECHECK_LEDGER.get() is None else 1_000_000

def _capped(found: list[dict[str, str]], cap: int) -> list[dict[str, str]]:
    from .settings import setting_on

    if len(found) <= cap or not setting_on("detaudit_rotate_cap", True) or not all(isinstance(f, dict) for f in found):
        return found[:cap]
    from . import audit

    audit.book_cut(found[cap:], "over its check's cap")
    return found[:cap]

_ITEM_TITLES = (r"(?:(?:each|every|all|the)\s+)?(?:list|item|entry|record|row)s?(?:'s|s')?\s+(?:titles?|names?)"
                r"|(?:each|every)\s+(?:item|entry|record|row)|(?:titles?|names?)\s+of\s+(?:the\s+)?(?:items|entries|records|rows|list)")
TITLES_AS_BUTTONS = re.compile(r"(?<![\w-])(?:" + _ITEM_TITLES + r")\s+(?:is|are)\s+(?:(?:a|an|one)\s+)?(?:\w+\s+)?buttons?\b"
                               r"|(?<![\w-])(?:" + _ITEM_TITLES + r")\s+(?:is|are)\s+not\s+links?\b", re.I)

def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()

def requirement_text(node: dict[str, Any]) -> str:
    parts = [str(node.get("name") or ""), str(node.get("description") or "")]
    for scenario in node.get("scenarios") or []:
        parts.append(str(scenario.get("name") or ""))
        for step in scenario.get("steps") or []:
            parts.append(str(step.get("content") or ""))
    text = "\n".join(parts)
    return re.sub(r"Visual reference notes for .*?(?=\n### |\Z)", "", text, flags=re.DOTALL)

def quoted_literals(text: str) -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    for match in QUOTED.finditer(text):
        literal = next(g for g in match.groups() if g is not None).strip()
        if len(literal) < 2 or literal in found:
            continue
        if not re.search(r"[A-Za-z一-鿿]", literal):
            continue
        if re.search(r"[/\\{}<>=]|^\w+\(.*\)$|^getBy|^data-", literal):
            continue
        around = text[max(0, match.start() - 18) : match.end() + 10]
        context = around.replace(literal, " ")
        if not UI_CONTEXT.search(context):

            continue
        found[literal] = text[max(0, match.start() - 14) : match.end() + 14]
    return list(found.items())

ALTERNATIVE_GAP = re.compile(r"^\s*(?:或者?|or|/)\s*$", re.IGNORECASE)
CHOICE_WORDS = re.compile(r"两个|二选一|选项|方式|切换|选择|\b(?:two|both|options?|choices?|choose|select|switch|toggle|methods?|modes?)\b", re.IGNORECASE)
CONTAINS_BEFORE = re.compile(r"包含|含有|contain|includ", re.IGNORECASE)

def alternative_pairs(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []

    literals = list(re.finditer(r"`([^`\n]{1,40})`", text))
    for left, right in zip(literals, literals[1:]):
        if not ALTERNATIVE_GAP.match(text[left.end() : right.start()]):
            continue
        if CONTAINS_BEFORE.search(text[max(0, left.start() - 12) : left.start()]):
            continue
        a, b = left.group(1).strip(), right.group(1).strip()
        if not (a and b) or a == b or re.search(r"[()=<>{}]|^getBy", a + b) or (a, b) in pairs:
            continue

        if _cjk(a) == _cjk(b):
            continue

        around = text[max(0, left.start() - 40) : right.end() + 24]
        if CHOICE_WORDS.search(around.replace(a, " ").replace(b, " ")):
            continue
        pairs.append((a, b))
    return pairs

def _cjk(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", text))

def interface_alternative(first: str, second: str, text: str) -> tuple[str, str]:
    quoted = [next(g for g in groups if g) for groups in re.findall(r"`([^`\n]{2,40})`|\"([^\"\n]{2,40})\"|“([^”\n]{2,40})”", text)]
    cjk = script_of("\n".join(quoted) if len(quoted) >= 10 else text) == "cjk"
    return (second, first) if _cjk(second) == cjk and _cjk(first) != cjk else (first, second)

def wanted_role(literal: str, text: str) -> str:
    verdicts: set[str] = set()
    for match in re.finditer(r"[`\"“「『]" + re.escape(literal) + r"[`\"”」』]", text):
        after = text[match.end() : match.end() + 8]
        before = text[max(0, match.start() - 14) : match.start()]

        wide = text[max(0, match.start() - 40) : match.end() + 24]
        if LINK_WORDS.search(wide) and BUTTON_WORDS.search(wide):
            verdicts.update({"link", "button"})
            continue
        for window in (after, before):
            link, button = LINK_WORDS.search(window), BUTTON_WORDS.search(window)
            if bool(link) != bool(button):
                verdicts.add("link" if link else "button")
                break
    return verdicts.pop() if len(verdicts) == 1 else ""

def application_sources(project_dir: Path, limit: int = 2_000_000) -> str:
    chunks: list[str] = []
    total = 0
    for root in ("frontend/src", "backend/src", "frontend/index.html"):
        base = project_dir / root
        paths = [base] if base.is_file() else (sorted(base.rglob("*")) if base.is_dir() else [])
        for path in paths:
            if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES or "node_modules" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            chunks.append(text)
            total += len(text)
            if total > limit:
                return "\n".join(chunks)
    return "\n".join(chunks)

REPEATED_CONTAINERS = {"listitem", "row", "article", "cell", "gridcell", "option", "menuitem", "treeitem"}

def _aria_entries(snapshot: list[dict[str, Any]], skip_repeated: bool = False) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    for page in snapshot:
        container_indent: int | None = None
        for line in str(page.get("aria") or "").splitlines():
            indent = len(line) - len(line.lstrip())
            if container_indent is not None and indent <= container_indent:
                container_indent = None
            role_only = re.match(r"^\s*-\s+([a-z]+)\b", line)
            inside = container_indent is not None
            if role_only and role_only.group(1) in REPEATED_CONTAINERS and container_indent is None:
                container_indent = indent
            match = ARIA_LINE.match(line)
            if match and not (skip_repeated and inside):
                entries.append((str(page.get("route")), match.group("role"), match.group("name").replace('\\"', '"')))
    return entries

UNIQUE_ROLES = r"(link|button|heading|searchbox|textbox|combobox|checkbox|tab|navigation|region|dialog)"
UNIQUE_NAME = r"[“\"`]([^”\"`\n]{1,60})[”\"`]"
UNIQUE_STATED = [
    re.compile(r"\b(?:the\s+)?(?:unique|only)\s+" + UNIQUE_NAME + r"\s+" + UNIQUE_ROLES, re.IGNORECASE),
    re.compile(r"\b(?:unique|only)\s+" + UNIQUE_ROLES + r"\s+(?:named|called|labell?ed)\s+" + UNIQUE_NAME, re.IGNORECASE),
    re.compile(r"\bexactly\s+one\s+" + UNIQUE_ROLES + r"\s+(?:named|called|labell?ed)\s+" + UNIQUE_NAME, re.IGNORECASE),
]

def stated_unique(text: str) -> list[tuple[str, str]]:
    from .stated_controls import unquoted_unique_controls
    found: list[tuple[str, str]] = []
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text):
        if re.search(r"\b(each|every|per)\b", sentence, re.IGNORECASE):
            continue
        for number, pattern in enumerate(UNIQUE_STATED):
            for match in pattern.finditer(sentence):
                name, role = (match.group(1), match.group(2)) if number == 0 else (match.group(2), match.group(1))
                found.append((role.lower(), name.strip()))

        found.extend(unquoted_unique_controls(sentence))
    return list(dict.fromkeys(found))

def unique_scope(text: str) -> tuple[dict[tuple[str, str], str], set[tuple[str, str]]]:
    from .stated_controls import unquoted_unique_controls
    scope: dict[tuple[str, str], str] = {}
    quoted: set[tuple[str, str]] = set()
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text):
        if re.search(r"\b(each|every|per)\b", sentence, re.IGNORECASE):
            continue
        home = "home" if re.search(r"\bhome\s*page\b", sentence, re.IGNORECASE) else ""
        keys = [((m.group(2) if n == 0 else m.group(1)).lower(), (m.group(1) if n == 0 else m.group(2)).strip())
                for n, pattern in enumerate(UNIQUE_STATED) for m in pattern.finditer(sentence)]
        quoted.update(keys)
        keys += [(role, name) for role, name in unquoted_unique_controls(sentence)]
        for key in keys:
            scope[key] = scope.get(key) or home
    return scope, quoted

def link_targets(aria: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    pending: tuple[str, int] | None = None
    for line in aria.splitlines():
        indent = len(line) - len(line.lstrip())
        match = re.match(r"^\s*-\s+link(?:\s+\"((?:[^\"\\]|\\.)*)\")?", line)
        if match:
            if pending:
                links.append((pending[0], ""))
            pending = (match.group(1) or "", indent)
            continue
        url = re.match(r"^\s*-\s+/url:\s*(\S+)", line)
        if url and pending and indent > pending[1]:
            links.append((pending[0], url.group(1).strip("\"'")))
            pending = None
    if pending:
        links.append((pending[0], ""))
    return links

ITEM_CONTAINERS = {"listitem", "article", "row"}

CONTROL_GROUPS = {"navigation", "menu", "menubar", "toolbar", "tablist", "listbox", "radiogroup", "group"}
DETAILS_URL = re.compile(r"/(?!page/)[\w-]+/\d+(?:/|$)")

ACTION_WORDS = re.compile(r"(?:edit|delete|remove|add|new|create|save|cancel|close|open|more|view|show|hide|favou?rite|"
                          r"unfavou?rite|archive|unarchive|restore|pin|unpin|move|copy|share|download|upload|select|clear|"
                          r"reset|log ?in|log ?out|sign ?in|sign ?out|back|next|previous|confirm|submit|change|toggle|filter|sort)\b", re.IGNORECASE)

TOGGLE_STATE = re.compile(r"\[(?:pressed|selected|checked)(?:=[^\]]*)?\]")
FILTER_KIND = r"(?:filters?|tags?|labels?|categor(?:y|ies)|chips?|statuse?s?|筛选|过滤|标签|分类)"

def _filter_named(name: str, requirement: str) -> bool:
    quoted = r"[`\"“「『]" + re.escape(name) + r"[`\"”」』]"
    return bool(re.search(r"(?<![\w-])" + FILTER_KIND + r"\s*(?:named\s+|called\s+)?" + quoted, requirement, re.IGNORECASE)
                or re.search(quoted + r"\s*" + FILTER_KIND + r"(?![\w-])", requirement, re.IGNORECASE))

def bare_item_titles(aria: str, requirement: str = "") -> list[str]:
    lines = aria.splitlines()
    titles: list[str] = []
    buttons: list[str] = []
    toggles: set[int] = set()
    button_parent: dict[str, int] = {}
    nav_indent = None
    index = 0
    while index < len(lines):
        line = lines[index]
        indent = len(line) - len(line.lstrip())
        if nav_indent is not None and indent <= nav_indent:
            nav_indent = None
        role = re.match(r"^\s*-\s+([a-z]+)\b", line)
        if role and role.group(1) in CONTROL_GROUPS and nav_indent is None:
            nav_indent = indent
        if role and role.group(1) in ITEM_CONTAINERS and nav_indent is None:
            block = []
            index += 1
            end = index
            while end < len(lines) and len(lines[end]) - len(lines[end].lstrip()) > indent:
                block.append(lines[end])
                end += 1
            if any(re.match(r"^\s*-\s+heading\b", b) for b in block):
                continue
            for name, href in link_targets("\n".join(block)):
                if name and DETAILS_URL.search(href):
                    titles.append(name)
                    break
            else:

                first = next((m for m in (re.match(r'^\s*-\s+(button|link)\s+"((?:[^"\\]|\\.)*)"(.*)$', b) for b in block) if m), None)
                name = first.group(2).strip() if first else ""
                if first and first.group(1) == "button" and not ACTION_WORDS.match(name) and not re.fullmatch(r"[\d\W]+", name):
                    parent = next((above for above in range(index - 2, -1, -1)
                                   if len(lines[above]) - len(lines[above].lstrip()) < indent), -1)
                    if TOGGLE_STATE.search(first.group(3)):
                        toggles.add(parent)
                    elif not (requirement and _filter_named(name, requirement)):
                        buttons.append(first.group(2))
                        button_parent.setdefault(first.group(2), parent)
            continue
        index += 1
    buttons = [b for b in buttons if button_parent.get(b) not in toggles]
    if len(set(buttons)) >= 2:
        titles.extend(buttons)
    return list(dict.fromkeys(titles))

SEED_QUOTES = re.compile(r"Seed data:([^\n]*)", re.IGNORECASE)

def fixed_seed_tails(requirement: str, sources: str) -> list[tuple[str, str, str]]:
    seeds = [a or b for sentence in SEED_QUOTES.findall(requirement)
             for a, b in re.findall(r"\"([^\"\n]{1,60})\"|“([^”\n]{1,60})”", sentence)]
    found: list[tuple[str, str, str]] = []
    for example in dict.fromkeys(m.group(1).strip() for m in re.finditer(r"`([^`\n]{2,80})`", requirement)):
        for seed in seeds:
            words = seed.split()
            if len(words) < 2 or example == seed or not example.endswith(seed):
                continue
            prefix, tail = example[: -len(seed)], " ".join(words[1:])
            entity = prefix.strip().split()[-1].lower() if prefix.strip() else ""

            if not entity or not re.search(re.escape(entity) + r"\W{0,3}[\"“]" + re.escape(seed), requirement, re.IGNORECASE):
                continue
            code = re.search(re.escape(prefix) + r"\$\{[^}]+\}\s*" + re.escape(tail), sources)
            if code:
                found.append((example, seed, code.group(0)))
    return found

KIND_OPEN = re.compile(r"\blinks?\s+or\s+buttons?\b|\bbuttons?\s+or\s+links?\b|链接或按钮|按钮或链接|链接/按钮|按钮/链接", re.IGNORECASE)

def requirement_says_link(requirement: str) -> bool:
    return bool(LINK_WORDS.search(KIND_OPEN.sub(" ", requirement)))

TITLES_ARE_LINKS = re.compile(r"\btitles?\b[^.;:!?]{0,40}?\b(?:are|is|as)\s+(?:(?:a|the|real)\s+)?links?\b"
                              r"|\btitles?\s+links?\s+to\b|标题[^。；，,\n]{0,12}链接", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+|(?<=[。！？；])")
_ID_KIND = re.compile(r"/([A-Za-z][\w-]*)/\d+(?:[/?#]|$)")

def link_sentences(whole: str) -> list[str]:
    flat = " ".join(str(whole or "").split())
    return [s for s in _SENTENCE_END.split(flat) if LINK_WORDS.search(KIND_OPEN.sub(" ", s))]

def _kind_words(href: str) -> set[str]:
    match = None
    for match in _ID_KIND.finditer(str(href or "").split("?")[0]):
        pass
    if not match:
        return set()
    kind = match.group(1).lower()
    words = {kind, kind + "s"}
    if kind.endswith("ies"):
        words.add(kind[:-3] + "y")
    if kind.endswith("ves"):
        words.add(kind[:-3] + "f")
    if kind.endswith("es"):
        words.add(kind[:-2])
    if kind.endswith("s"):
        words.add(kind[:-1])
    return {w.replace("_", "-") for w in words if len(w) >= 3} | {w.replace("-", " ").replace("_", " ") for w in words if len(w) >= 3}

def list_worded_as_links(sentences: list[str], items: list[tuple[str, str]]) -> str:
    titles = [t.strip() for t, _h in items if len(t.strip()) >= 3 and not re.fullmatch(r"[\d\W]+", t.strip())]
    kinds = set().union(*(_kind_words(h) for _t, h in items)) if items else set()
    for sentence in sentences:
        if TITLES_ARE_LINKS.search(sentence):
            return sentence
        if any(re.search(r"(?<![\w-])" + re.escape(t) + r"(?![\w-])", sentence, re.IGNORECASE) for t in titles):
            return sentence
        prose = QUOTED.sub(" ", sentence)
        for kind in kinds:
            for match in re.finditer(r"(?<![\w-])" + re.escape(kind) + r"(?![\w-])", prose, re.IGNORECASE):
                if match.group(0).islower() or not prose[: match.start()].strip():
                    return sentence
    return ""

def item_title_links(aria: str) -> list[str]:
    return list(dict.fromkeys(name for group in item_title_link_groups(aria) for name, _href in group))

def item_title_link_groups(aria: str) -> list[list[tuple[str, str]]]:
    lines = aria.splitlines()
    groups: dict[int, list[tuple[str, str]]] = {}
    group_indent = None
    index = 0
    while index < len(lines):
        line = lines[index]
        indent = len(line) - len(line.lstrip())
        if group_indent is not None and indent <= group_indent:
            group_indent = None
        role = re.match(r"^\s*-\s+([a-z]+)\b", line)
        if role and role.group(1) in CONTROL_GROUPS and group_indent is None:
            group_indent = indent
        if role and role.group(1) in ITEM_CONTAINERS and group_indent is None:
            parent = next((above for above in range(index - 1, -1, -1)
                           if len(lines[above]) - len(lines[above].lstrip()) < indent), -1)
            block = []
            index += 1
            end = index
            while end < len(lines) and len(lines[end]) - len(lines[end].lstrip()) > indent:
                block.append(lines[end])
                end += 1
            first = next((m for m in (re.match(r'^\s*-\s+(button|link)\s+"((?:[^"\\]|\\.)*)"', b) for b in block) if m), None)
            if first and first.group(1) == "link":
                for name, href in link_targets("\n".join(block)):
                    if name == first.group(2) and DETAILS_URL.search(href):
                        groups.setdefault(parent, []).append((name, href))
                        break
            continue
        index += 1
    return list(groups.values())

def text_beside_controls(aria: str, literals: list[str]) -> list[str]:
    lines = aria.splitlines()
    found: list[str] = []
    for index, line in enumerate(lines):
        text = re.match(r"^(\s*)-\s+text:\s*(.+?)\s*$", line)
        if not text or text.group(2).strip("\"'") not in literals:
            continue
        indent = len(text.group(1))

        ancestors, depth = [], indent
        for above in reversed(lines[:index]):
            above_depth = len(above) - len(above.lstrip())
            if above_depth < depth:
                ancestors.append(above)
                depth = above_depth
        if not any(re.match(r"^\s*-\s+(status|alert)\b", a) for a in ancestors):
            continue
        siblings = []
        for other in lines[index + 1:]:
            depth = len(other) - len(other.lstrip())
            if depth < indent:
                break
            if depth == indent:
                siblings.append(other)
        for other in reversed(lines[:index]):
            depth = len(other) - len(other.lstrip())
            if depth < indent:
                break
            if depth == indent:
                siblings.append(other)
        if any(re.match(r"^\s*-\s+(button|link|checkbox|menuitem)\b", s) for s in siblings):
            found.append(text.group(2).strip("\"'"))
    return list(dict.fromkeys(found))

NOTIFICATION = re.compile(r"""role=["'](?:status|alert)["']""")

MESSAGE_BESIDE_BUTTON = re.compile(r"<(\w+)\b[^>]*>\s*\{[^{}<>]{1,80}\}\s*(?:\{[^{}<>]{0,80}&&\s*\(?\s*)?<button\b")

SEED_ACTIVE = re.compile(r"\b(active|regular|unarchived|not archived|not deleted)\s+(?:[\w-]+\s+){0,2}[\"\u201c]([^\"\u201d\n]{2,80})[\"\u201d]", re.I)
LATER_STATE_FLAG = re.compile(r"\b(?:is_)?(archived|trashed|deleted|in_trash)\s*[:=]\s*(?:1|true)\b", re.I)

def seeded_in_a_later_state(requirement: str, sources: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    rows = sql_insert_rows(sources)
    for match in SEED_ACTIVE.finditer(requirement):
        name = match.group(2).strip()
        for line in sources.splitlines():
            if (f"'{name}'" in line or f'"{name}"' in line) and LATER_STATE_FLAG.search(line):
                found.append((name, line.strip()[:160]))
                break
        else:

            for columns, values, text in rows:
                if len(columns) == len(values) and name in values and any(
                        LATER_STATE_COLUMN.fullmatch(col) and val.lower() in ("1", "true") for col, val in zip(columns, values)):
                    found.append((name, text[:160]))
                    break
    return found

LATER_STATE_COLUMN = re.compile(r"(?:is_)?(?:archived|trashed|deleted|in_trash)", re.I)
SQL_INSERT = re.compile(r"\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+[\w\"`\[\].]+\s*\(([^()]*)\)\s*VALUES\s*", re.I)

def _sql_tuple(text: str, start: int) -> tuple[list[str], int] | None:
    if start >= len(text) or text[start] != "(":
        return None
    values, current, i = [], "", start + 1
    while i < len(text):
        ch = text[i]
        if ch in "'\"":
            j = i + 1
            while j < len(text) and not (text[j] == ch and text[j + 1: j + 2] != ch):
                j += 2 if text[j] == ch else 1
            current += text[i + 1: j].replace(ch * 2, ch)
            i = j + 1
            continue
        if ch == "(":
            return None
        if ch in ",)":
            values.append(current.strip())
            current = ""
            if ch == ")":
                return values, i + 1
        else:
            current += ch
        i += 1
    return None

def sql_insert_rows(sources: str) -> list[tuple[list[str], list[str], str]]:
    rows: list[tuple[list[str], list[str], str]] = []
    for match in SQL_INSERT.finditer(sources):
        columns = [c.strip().strip("\"`[]") for c in match.group(1).split(",")]
        i = match.end()
        while True:
            parsed = _sql_tuple(sources, i)
            if parsed is None:
                break
            values, i = parsed
            rows.append((columns, values, " ".join(sources[match.start(): i].split())))
            rest = re.match(r"\s*,\s*", sources[i:])
            if not rest:
                break
            i += rest.end()
    return rows

def mixed_notifications(sources: str) -> list[str]:
    found: list[str] = []
    for match in NOTIFICATION.finditer(sources):
        window = sources[match.start(): match.start() + 900]
        hit = MESSAGE_BESIDE_BUTTON.search(window)
        if hit:
            found.append(re.sub(r"\s+", " ", hit.group(0))[:120])
    return list(dict.fromkeys(found))

def _finding(where: str, requirement: str, problem: str, fix: str) -> dict[str, str]:
    return {"where": where, "requirement": requirement, "problem": problem, "fix": fix}

CLIENT_CALL = re.compile(r"""(?<![\w$.])([A-Za-z_$][\w$]*)\.(get|post|put|patch|delete)\s*(?:<[^>()]*>)?\(\s*(`[^`]*`|'[^']*'|"[^"]*")""")
ROUTE_DEF = re.compile(r"""\b(?:router|app)\.(get|post|put|patch|delete|all)\s*\(\s*(['"`])([^'"`]+)\2""")
MOUNT = re.compile(r"""\bapp\.use\s*\(\s*(['"`])([^'"`]+)\1\s*,\s*require\(\s*['"]\./routes/([\w.-]+?)(?:\.js)?['"]\s*\)""")
AXIOS_CREATE = re.compile(r"\baxios\s*\.\s*create\s*\(")
IMPORT_FROM = re.compile(r"""\bimport\s+(?!type\b)([^'";]+?)\s+from\s+['"]([^'"]+)['"]""")

def strip_js_comments(text: str) -> str:
    out: list[str] = []
    i, n, quote = 0, len(text), ""
    while i < n:
        c = text[i]
        if quote:
            if c == "\\" and i + 1 < n:
                out.append(text[i : i + 2])
                i += 2
                continue
            out.append(c)
            if c == quote or (c == "\n" and quote != "`"):
                quote = ""
            i += 1
            continue
        if c in "'\"`":
            quote = c
            out.append(c)
            i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append(" " + "\n" * text.count("\n", i, end))
            i = end
        else:
            out.append(c)
            i += 1
    return "".join(out)

def _resolve_module(spec: str, importer: Path, frontend: Path) -> Path | None:
    if spec.startswith("@/"):
        base = frontend / spec[2:]
    elif spec.startswith("."):
        base = importer.parent / spec
    else:
        return None
    candidates = [base] + [base.with_name(base.name + ext) for ext in (".ts", ".tsx", ".js", ".jsx")] \
        + [base / f"index{ext}" for ext in (".ts", ".tsx", ".js", ".jsx")]
    return next((c for c in candidates if c.is_file()), None)

def axios_receivers(path: Path, text: str, frontend: Path, creates: dict[Path, bool]) -> set[str]:
    def has_create(module: Path) -> bool:
        if module not in creates:
            creates[module] = bool(AXIOS_CREATE.search(strip_js_comments(module.read_text(encoding="utf-8", errors="replace"))))
        return creates[module]

    names = set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*axios\s*\.\s*create\s*\(", text))
    for match in IMPORT_FROM.finditer(text):
        module = _resolve_module(match.group(2), path, frontend)
        if module is None or not has_create(module):
            continue
        clause = match.group(1).strip()
        if clause.startswith("*"):
            continue
        default = re.match(r"([A-Za-z_$][\w$]*)\s*(?:,|$)", clause)
        if default:
            names.add(default.group(1))
        braces = re.search(r"\{([^}]*)\}", clause)
        for part in (braces.group(1).split(",") if braces else []):
            part = re.sub(r"^\s*type\s+", "", part).strip()
            if part:
                names.add(re.split(r"\s+as\s+", part)[-1].strip())
    return names

def unanswered_api_calls(project_dir: Path) -> list[tuple[str, str, str]]:
    backend = project_dir / "backend" / "src"
    frontend = project_dir / "frontend" / "src"
    if not backend.is_dir() or not frontend.is_dir():
        return []
    files = {p: strip_js_comments(p.read_text(encoding="utf-8", errors="replace"))
             for p in backend.rglob("*.js") if "node_modules" not in p.parts}

    for text in files.values():
        if re.search(r"\.route\s*\(", text) or re.search(r"require\([^)]*\)\s*\(", text):
            return []
        if any(var != "router" for var in re.findall(r"\b(\w+)\s*=\s*(?:express\s*\.\s*)?Router\s*\(", text)):
            return []
        if re.search(r"\b(?!app\b)\w+\s*\.\s*use\s*\(\s*['\"`]", text):
            return []

    client = "\n".join(strip_js_comments(p.read_text(encoding="utf-8", errors="replace")) for p in frontend.rglob("*.ts*"))
    bases = re.findall(r"axios\s*\.\s*create\s*\(\s*\{[^}]*baseURL\s*:\s*['\"`]([^'\"`]*)['\"`]", client)
    if not bases or any(base.rstrip("/") != "/api" for base in bases):
        return []
    mounts: dict[str, list[str]] = {}
    for text in files.values():
        for m in MOUNT.finditer(text):
            mounts.setdefault(m.group(3), []).append(m.group(2).rstrip("/"))

        names = {m.group(1): m.group(2) for m in re.finditer(r"""\b(?:const|let|var)\s+(\w+)\s*=\s*require\(\s*['"]\./routes/([\w.-]+?)(?:\.js)?['"]\s*\)""", text)}
        for m in re.finditer(r"""\bapp\.use\s*\(\s*(['"`])([^'"`]+)\1\s*,\s*(\w+)\s*\)""", text):
            if m.group(3) in names:
                mounts.setdefault(names[m.group(3)], []).append(m.group(2).rstrip("/"))
    routes: list[tuple[str, list[str]]] = []
    for path, text in files.items():
        prefixes = mounts.get(path.stem, [""] if path.parent == backend else [])
        if not prefixes and ROUTE_DEF.search(text):
            return []
        for m in ROUTE_DEF.finditer(text):
            for prefix in prefixes:
                routes.append((m.group(1).upper(), [s for s in (prefix + "/" + m.group(3).lstrip("/")).split("/") if s]))
    if len(routes) < 3:
        return []

    def answered(method: str, segments: list[str]) -> bool:
        for route_method, route in routes:
            if route_method not in (method, "ALL") or len(route) != len(segments):
                continue
            if all(r.startswith(":") or s == "*" or r == s for r, s in zip(route, segments)):
                return True
        return False

    found: list[tuple[str, str, str]] = []
    creates: dict[Path, bool] = {}
    for path in sorted(frontend.rglob("*.ts*")):
        text = strip_js_comments(path.read_text(encoding="utf-8", errors="replace"))
        receivers = axios_receivers(path, text, frontend, creates)
        for m in CLIENT_CALL.finditer(text):
            if m.group(1) not in receivers:
                continue
            raw = m.group(3)[1:-1].split("?")[0]
            if not raw.startswith("/") or "${" in raw.split("/")[1] if raw.count("/") >= 1 else True:
                continue

            variants = [raw]
            for var in set(re.findall(r"\$\{\s*(\w+)\s*\}", raw)):
                unions = [u for u in re.finditer(rf"\b{var}\s*:\s*((?:'[^']+'\s*\|\s*)+'[^']+')", text) if u.start() < m.start()]
                union = unions[-1] if unions else None
                if union:
                    options = re.findall(r"'([^']+)'", union.group(1))
                    variants = [v.replace("${" + var + "}", o) for v in variants for o in options]
            for variant in variants:
                full = variant if variant.startswith("/api/") else "/api" + variant

                full = re.sub(r"\$\{[^}]*\}", "*", full)
                if re.search(r"['\"()\[\]{}]", full):
                    continue
                segments = [s for s in full.split("/") if s]
                if not answered(m.group(2).upper(), segments):
                    line = text.count("\n", 0, m.start()) + 1
                    found.append((m.group(2).upper(), "/" + "/".join(segments), f"{path.relative_to(project_dir).as_posix()}:{line}"))
    return list(dict.fromkeys(found))

ELEMENT_OF_ROLE = {
    "button": '<button type="button">', "link": "<a href> / router <Link>", "menuitem": 'an element with role="menuitem"',
    "tab": 'an element with role="tab"', "checkbox": '<input type="checkbox"> with a <label>',
    "radio": '<input type="radio"> with a <label>', "textbox": "<input> / <textarea> with a <label>",
    "searchbox": '<input type="search"> with a <label>', "combobox": "<select> with a <label>", "option": "<option>",
    "switch": 'a <button role="switch">', "dialog": 'role="dialog" with aria-labelledby its title',
    "region": "<section aria-label>", "heading": "<h2>", "form": "<form aria-label>", "grid": 'role="grid"',
    "listbox": 'role="listbox"', "group": 'role="group" with aria-label',
}

def owner_lookup(node: dict[str, Any]):
    from . import finding_owner

    whole = str(node.get("requirement_text") or "").strip()
    if not whole or not finding_owner.enabled():
        return None, None

    if node.get("inherited_names_complete") is not True or not isinstance(node.get("inherited_names_all"), list):
        return None, None
    inherited = [str(n) for n in node.get("inherited_names_all") or []]

    def owner_of(name: str) -> list[str]:
        return finding_owner.name_owners(whole, [name], inherited)

    def source_owner_of(name: str) -> list[str]:
        return finding_owner.stated_name_owners(whole, name, inherited)

    return owner_of, source_owner_of

def _owned(finding: dict[str, str], owner_of, name: str) -> dict[str, str]:
    if owner_of is not None:
        from .finding_owner import tag

        owners = owner_of(name)
        if owners:
            tag(finding, owners)
    return finding

def stated_control_findings(node: dict[str, Any], project_dir: Path, owner_of=None) -> tuple[list[dict[str, str]], dict[str, int]]:
    whole = str(node.get("requirement_text") or "").strip()
    own = describe_node(node)
    problems = stated_control_problems(whole or own, project_dir)
    own_names = {str(stated["name"]).strip().lower() for stated in extract_stated_controls(own)[0]}

    kinds: dict[str, set[str]] = {}
    for stated in extract_stated_controls(whole or own)[0]:
        kinds.setdefault(str(stated["name"]).strip().lower(), set()).update(
            fam(r) for r in stated["roles"] if r in CONTROL_ROLES)
    findings: list[dict[str, str]] = []
    counts = {"stated_role": 0, "stated_missing": 0}
    owner_of = owner_of if whole else None
    for problem in problems:
        name, role = str(problem["name"]), str(problem["role"])
        context = str(problem["evidence"]).split("requirement: ", 1)[-1].strip()
        first = role.split("/")[0]
        element = ELEMENT_OF_ROLE.get(first, f'an element with role="{first}"')
        if problem["kind"] in ("stated role differs", "stated control not a control"):
            if counts["stated_role"] >= _per_check():
                continue
            counts["stated_role"] += 1
            rendered = str(problem["evidence"]).split(" | requirement:", 1)[0]
            others = sorted(kinds.get(name.lower(), set()) - {fam(r) for r in role.split("/")})
            fix = (f"Render “{name}” as {element} -- role {role}, accessible name exactly “{name}” -- in "
                   "place of the element it is now; keep what it does.")
            if others:
                fix = (f"The requirement ALSO names a {'/'.join(others)} “{name}”: keep that element as it is, and ADD "
                       f"{element} -- role {role}, accessible name exactly “{name}” -- where the requirement places it.")
            if problem.get("rendered"):
                fix = (f"Keep that {problem['rendered']} as it is -- it is not the control -- and ADD "
                       f"{element} -- role {role}, accessible name exactly “{name}” -- where the requirement places it.")
            findings.append(_owned(_finding(
                rendered.split(" ", 1)[0],
                f"(the requirement: a {role} named “{name}”) {context[:200]}",
                f"The requirement states a {role} named “{name}”, but every place the sources render that name "
                f"gives it another role: {rendered[:240]}. Whoever looks for the {role} by its name finds none.",
                fix,
            ), owner_of, name))
        elif problem["kind"] == "stated control missing":
            if name.strip().lower() not in own_names or counts["stated_missing"] >= _per_check():
                continue
            counts["stated_missing"] += 1
            findings.append(_owned(_finding(
                "application sources",
                f"(this batch's requirement: a {role} named “{name}”) {context[:200]}",
                f"This batch's requirement states a {role} named “{name}”, and that name occurs nowhere in "
                "frontend/src or backend/src: the control does not exist.",
                f"Build it: {element} with the role {role} and the accessible name exactly “{name}”, where and "
                "as the requirement describes it.",
            ), owner_of, name))
    return findings, counts

def inert_control_findings(node: dict[str, Any], project_dir: Path, log=print) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for item in inert_named_controls(describe_node(node), project_dir, log=log)[:_per_check()]:
        elsewhere = item.get("same_name_elsewhere") or []
        if item.get("kind") == "no-handler":

            findings.append(_finding(
                f"{item['file']}:{item['line']}",
                f"(this batch's requirement: “{item['name']}” is a control the user activates)",
                f"<{item['tag']}> “{item['name']}” has no event handler at all (no onClick, nothing around it handles the "
                "click): activating it does nothing, so it cannot do what the requirement says.",
                f"Give “{item['name']}” an onClick that does what the requirement says activating it does (and the "
                "keyboard shortcut the requirement names, if any, must run the same code). Keep its name and role.",
            ))
            continue
        if item.get("kind") == "disabled":

            findings.append(_finding(
                f"{item['file']}:{item['line']}",
                f"(this batch's requirement: the user activates “{item['name']}”)",
                f"<{item['tag']}> “{item['name']}” carries a literal disabled attribute (not bound to any state): it can "
                "never be activated, so it cannot do what the requirement says.",
                f"Remove the hard-coded disabled from “{item['name']}” and make activating it do what the requirement "
                "says. If it must be disabled in some state, bind disabled to that state only.",
            ))
            continue
        findings.append(_finding(
            f"{item['file']}:{item['line']}",
            f"(this batch's requirement: activating “{item['name']}” does something)",
            f"<{item['tag']}> “{item['name']}” has {item['handler'][:160]}, which does nothing but: "
            f"{item['reason'][:160] or 'nothing'}. Activating it cannot do what the requirement says."
            + (f" Other controls with this name: {', '.join(elsewhere[:3])}." if elsewhere else ""),
            f"Make activating “{item['name']}” do what the requirement says (open its dialog, run its action); "
            "closing a menu is not enough. If another control of that name already does it, remove this inert copy.",
        ))
    return findings

def display_names(requirement: str) -> list[str]:
    chunks = requirement_chunks(requirement)
    names: set[str] = set()
    for text, _given in chunks:
        for match in re.finditer(r"[“\"]([^“”\"]{3,60})[”\"]", text):
            names.add(match.group(1).strip())
    seeds = {value.strip() for value in seed_values(chunks)}
    for text, _given in chunks:
        for match in re.finditer(r"`([^`]{3,60})`", text):
            if match.group(1).strip().lower() in seeds:
                names.add(match.group(1).strip())
    return sorted(n for n in names if len(n) >= 3 and re.search(r"[A-Za-z]", n) and not re.search(r"[/\\{}<>=()]", n))

CONTROL_LINE = re.compile(r"^\s*-\s+(link|button)\s+\"((?:[^\"\\]|\\.)*)\"")

def named_controls(snapshot: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for page in snapshot:
        for line in str(page.get("aria") or "").splitlines():
            quoted = re.match(r"^(\s*-\s+)'(.*)'\s*:?\s*$", line)
            if quoted:
                line = quoted.group(1) + quoted.group(2).replace("''", "'")
            match = CONTROL_LINE.match(line)
            if match:
                found.append((str(page.get("route")), match.group(1), match.group(2).replace('\\"', '"')))
    return found

def _glued(prev: str, nxt: str) -> bool:
    return nxt.isupper() or (nxt.isdigit() and prev.isalpha()) or (nxt.isalpha() and prev.isdigit())

def glued_names(entries: list[tuple[str, str, str]], names: list[str], requirement: str) -> list[tuple[str, str, str, str]]:
    flat = " ".join(requirement.split()).lower()
    found: list[tuple[str, str, str, str]] = []
    for route, role, name in entries:
        name = name.strip()
        if role not in ("link", "button"):
            continue
        prefixes = [n for n in names if len(name) > len(n) and name.startswith(n)]
        if not prefixes:
            continue
        stated = max(prefixes, key=len)
        if not _glued(stated[-1], name[len(stated)]):
            continue
        token = stated + re.match(r"[A-Za-z0-9]*", name[len(stated):]).group(0)
        if token.lower() in flat:
            continue
        if not UI_METADATA.search(name[len(stated):]):
            continue
        found.append((route, role, name, stated))
    return list(dict.fromkeys(found))

UI_METADATA = re.compile(r"[:：]|\d{4}-\d{2}-\d{2}|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{1,2}:\d{2}\b")

RELABEL_CONTROL_ROLES = CONTROL_ROLES | {"menuitemcheckbox", "menuitemradio", "slider", "spinbutton", "treeitem"}
STRUCTURE_ROLES = {"grid", "treegrid", "table", "rowgroup", "row", "gridcell", "cell", "rowheader", "columnheader"}
CONTROL_TAGS = {"a", "button", "input", "select", "textarea", "summary", "option"}

def relabeled_control(item: dict[str, Any], aria: str) -> bool:
    tag = str(item.get("tag") or "").lower()
    label = str(item.get("label") or "").strip()
    roles = {role for _indent, role, name in aria_nodes(aria) if name.strip() == label}
    if roles & STRUCTURE_ROLES or tag in {"td", "th", "tr"}:
        return False
    if roles:
        return bool(roles & RELABEL_CONTROL_ROLES)
    return tag in CONTROL_TAGS

def deterministic_findings(project_dir: Path, node: dict[str, Any], snapshot: list[dict[str, Any]] | None, log=print) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []

    MAX_FINDINGS_PER_CHECK = _per_check()
    requirement = requirement_text(node)
    sources = application_sources(project_dir)
    try:
        owner_of, source_owner_of = owner_lookup(node)
    except Exception as exc:
        log(f"[audit] finding owners not read: {exc!r}")
        owner_of, source_owner_of = None, None
    rendered = "\n".join(str(p.get("text") or "") + "\n" + str(p.get("aria") or "") for p in snapshot or [])
    haystack = _norm(sources + "\n" + rendered)
    entries = _aria_entries(snapshot or [])
    counts = {"literals": 0, "headings": 0, "roles": 0, "duplicates": 0, "unlabeled": 0, "console": 0}

    try:
        entry_parsed = entry_statements(str(node.get("requirement_text") or "").strip() or describe_node(node))
    except Exception as exc:
        log(f"[audit] entry-statement reading crashed: {exc!r}")
        entry_parsed = {"unique": [], "exact": []}
    entry_cover = link_list_cover(entry_parsed)
    try:
        whole_links = link_sentences((str(node.get("requirement_text") or "").strip() or describe_node(node)) + "\n" + requirement)
    except Exception as exc:
        log(f"[audit] link-sentence reading crashed: {exc!r}")
        whole_links = link_sentences(requirement)

    literals = quoted_literals(requirement)
    missing = [literal for literal, _ in literals if _norm(literal) not in haystack]
    counts["literals"] = len(missing)
    if missing:
        log(f"[audit] (diagnostic, not reported) quoted literals absent from the application: {missing[:12]}")

    requirement_norm = _norm(requirement)
    for heading in dict.fromkeys(mandatory_texts(str(node.get("description") or ""))):
        if _norm(heading) in requirement_norm:
            continue
        if _norm(heading) in haystack or counts["headings"] >= MAX_FINDINGS_PER_CHECK:
            continue
        counts["headings"] += 1

        log(f"[audit] (diagnostic, not reported) heading of a reference screenshot absent from the application: {heading!r}")

    paired = {name for pair in alternative_pairs(requirement) for name in pair}
    for literal, around in literals:
        wanted = "" if literal in paired else wanted_role(literal, requirement)
        if not wanted or counts["roles"] >= MAX_FINDINGS_PER_CHECK:
            continue
        wants_link = wanted == "link"
        other = "button" if wants_link else "link"
        roles = {role for _, role, name in entries if name.strip() == literal}
        escaped = re.escape(literal)
        in_button = re.search(r"<button\b[^>]*>\s*(?:\{\s*['\"])?" + escaped + r"(?:['\"]\s*\})?\s*</button>", sources)
        in_link = re.search(r"<(?:a|Link|NavLink)\b[^>]*>\s*(?:\{\s*['\"])?" + escaped + r"(?:['\"]\s*\})?\s*</(?:a|Link|NavLink)>", sources)
        has_wanted = wanted in roles or bool(in_link if wants_link else in_button)
        has_other = other in roles or bool(in_button if wants_link else in_link)
        if has_other and not has_wanted:
            counts["roles"] += 1
            findings.append(
                _finding(
                    "control role",
                    literal,
                    f"The requirement calls `{literal}` a {wanted} (context: …{around.strip()}…) but the application only renders it as a {other}.",
                    "Render a real <a href> / router <Link> (ARIA role link)." if wants_link else "Render a real <button> (ARIA role button).",
                )
            )

    counts["alternatives"] = 0
    language_text = str(node.get("requirement_text") or "").strip() or requirement
    for first, second in alternative_pairs(requirement):
        keep, drop = interface_alternative(first, second, language_text)
        for route in dict.fromkeys(r for r, _, _ in entries):
            roles_first = {role for r, role, name in entries if r == route and name.strip() == first}
            roles_second = {role for r, role, name in entries if r == route and name.strip() == second}
            if roles_first & roles_second and counts["alternatives"] < MAX_FINDINGS_PER_CHECK:
                counts["alternatives"] += 1
                findings.append(
                    _finding(
                        f"route {route}",
                        f"`{first}` 或 `{second}`",
                        f"The requirement offers `{first}` and `{second}` as ALTERNATIVE names of one element, but the page renders both as separate {sorted(roles_first & roles_second)[0]} elements.",
                        f"Keep exactly one element, named `{keep}` (the interface language); remove the `{drop}` one "
                        "everywhere (header, page body).",
                    )
                )

    counts["same_href"] = 0
    for page in snapshot or []:
        by_href: dict[str, list[str]] = {}
        listed = [(str(l.get("name") or ""), str(l.get("href") or "")) for l in page.get("links") or [] if isinstance(l, dict)]
        for name, href in listed or link_targets(str(page.get("aria") or "")):
            if href and href != "/" and not href.startswith(("http", "#", "mailto:")):
                by_href.setdefault(href, []).append(name)
        for href, names in by_href.items():
            if len({n.strip().lower() for n in names}) >= 2:
                counts["same_href"] += 1
                log(f"[audit] (diagnostic, not reported) route {page.get('route')}: {names} all lead to {href}")

    seen: dict[tuple[str, str, str], int] = {}
    for route, role, name in _aria_entries(snapshot or [], skip_repeated=True):
        if role in DUPLICATE_ROLES and name.strip():
            seen[(route, role, name.strip())] = seen.get((route, role, name.strip()), 0) + 1
    for (route, role, name), count in seen.items():
        if count < 2 or counts["duplicates"] >= MAX_FINDINGS_PER_CHECK:
            continue
        counts["duplicates"] += 1
        findings.append(
            _finding(
                f"route {route}",
                "(accessible names must be unique per page)",
                f"{count} {role} elements share the accessible name `{name}` on this page, so neither users of assistive technology nor exact locators can tell them apart.",
                "If the requirement names two such controls in different parts of the page, leave them. Otherwise keep one, or give each its own name as worded by the requirement.",
            )
        )

    counts["stated_unique"] = 0
    unique_text = requirement + "\n" + str(node.get("reference_requirements") or "")
    unique = stated_unique(unique_text)
    if unique:

        scope, quoted = unique_scope(unique_text)
        unscoped_logged: set[tuple[str, str]] = set()
        tally: dict[tuple[str, str, str], int] = {}
        for route, role, name in _aria_entries(snapshot or [], skip_repeated=True):
            key = (role, name.strip())
            if key not in unique:
                continue
            if scope.get(key) == "home" and str(route).split("?")[0].rstrip("/") not in ("", "/"):
                continue
            if not scope.get(key) and key not in quoted:
                if key not in unscoped_logged:
                    unscoped_logged.add(key)
                    log(f"[audit] stated unique without quotes and without a page, not counted: {role} {name.strip()}")
                continue
            if key in unique:
                tally[(route, role, name.strip())] = tally.get((route, role, name.strip()), 0) + 1
        for (route, role, name), count in tally.items():
            if count >= 2 and counts["stated_unique"] < MAX_FINDINGS_PER_CHECK:
                counts["stated_unique"] += 1
                findings.append(
                    _finding(
                        f"route {route}",
                        f"(the requirement: the unique {role} `{name}`)",
                        f"{count} {role} elements named `{name}` are on this page (for example one in the header and one in "
                        "the navigation); the requirement says there is exactly one, and a locator that finds two stops.",
                        f"Keep ONE {role} named `{name}` on the page and remove the other copies (or give them other names "
                        "only if the requirement names them).",
                    )
                )

    from .settings import setting_on as _setting_on
    titles_said_buttons = bool(TITLES_AS_BUTTONS.search(str(node.get("requirement_text") or "") + "\n" + requirement))
    links_advisory = _setting_on("item_links_advisory") and not titles_said_buttons

    counts["items"] = 0
    item_text = str(node.get("requirement_text") or "") + "\n" + requirement
    for page in snapshot or []:
        bare = bare_item_titles(str(page.get("aria") or ""), item_text)
        if bare and counts["items"] < MAX_FINDINGS_PER_CHECK:
            counts["items"] += 1

            stated = {name for group in item_title_link_groups(str(page.get("aria") or ""))
                      if covers_item_list(entry_cover, group) or list_worded_as_links(whole_links, group)
                      for name, _href in group}
            fix = ("Nest the title control inside a heading, one text node: `<li><h3><button type=\"button\" "
                   "onClick={open}>Title</button></h3></li>` -- a link (`<h3><Link>`) only where the requirement calls it a "
                   "link. Do not write the title twice.")
            if set(bare) <= stated or links_advisory:
                fix = ("Nest the title link inside a heading, one text node: `<li><h3><a href=...>Title</a></h3></li>` -- "
                       "the requirement states these titles as links: keep each one a link with the same target. Do not "
                       "write the title twice.")
            findings.append(
                _finding(
                    f"route {page.get('route')}",
                    "(repeated items: the title is a heading AND the control that opens the item)",
                    f"{len(bare)} item(s) on this page show their title only as a bare link, with no heading in the item "
                    f"(e.g. {', '.join(repr(n) for n in bare[:3])}).",
                    fix,
                )
            )

    counts["item_links"] = 0

    if not requirement_says_link(requirement):
        for page in snapshot or []:
            groups = [group for group in item_title_link_groups(str(page.get("aria") or ""))
                      if not covers_item_list(entry_cover, group)]
            kept = []
            for group in groups:
                said = list_worded_as_links(whole_links, group)
                if said:
                    log(f"[audit] (diagnostic, not reported) route {page.get('route')}: item titles "
                        f"{[n for n, _h in group][:3]} are links, as the whole requirement words them: {said[:160]!r}")
                else:
                    kept.append(group)
            links = list(dict.fromkeys(name for group in kept for name, _href in group))

            if links and links_advisory:
                log(f"[audit] (diagnostic, not reported) route {page.get('route')}: item titles {links[:3]} are links "
                    "where the requirement leaves the kind open (a record that opens its page is a link)")
                links = []
            if links and counts["item_links"] < MAX_FINDINGS_PER_CHECK:
                counts["item_links"] += 1
                findings.append(
                    _finding(
                        f"route {page.get('route')}",
                        "(the requirement does not call these controls links: the kind is open, so a button)",
                        f"{len(links)} item(s) on this page open their details page through a LINK "
                        f"(e.g. {', '.join(repr(n) for n in links[:3])}); the requirement never calls them links.",
                        "Render each item's title as ONE button nested in the item's heading: "
                        "`<li><h3><button type=\"button\" onClick={() => navigate(`/x/${id}`)}>Title</button></h3></li>`; "
                        "keep the name exactly the item's title. Do not write the title twice.",
                    )
                )

    counts["pattern"] = 0
    for example, seed, code in fixed_seed_tails(requirement, sources)[:MAX_FINDINGS_PER_CHECK]:
        counts["pattern"] += 1
        findings.append(
            _finding(
                "application sources",
                f"`{example}`",
                f"`{example}` is the name of the seeded record \"{seed}\" (the pattern is `{example[: -len(seed)]}<the record's name>`), "
                f"but the code builds every record's name as `{code}`: another record then gets the SAME name as the "
                f"example (e.g. a record named \"{seed.split()[0]}\"), and an action meant for \"{seed}\" hits it.",
                f"Build the name as `{example[: -len(seed)]}${{name}}` -- nothing after the record's own name.",
            )
        )

    counts["seed_state"] = 0
    for name, line in seeded_in_a_later_state(requirement, sources)[:MAX_FINDINGS_PER_CHECK]:
        counts["seed_state"] += 1
        findings.append(
            _finding(
                "seed data",
                f"(the requirement's seed: {name!r} is active)",
                f"{name!r} is seeded in a later state: `{line}`. Someone who expects it where active records are "
                "(and performs the scenario's own steps -- archive it, delete it -- first) does not find it.",
                "Seed it exactly as the requirement says (active). A scenario whose GIVEN is a later state reaches it "
                "through the interface; the seed never skips those steps.",
            )
        )

    counts["mixed_status"] = 0
    for snippet in mixed_notifications(sources)[:MAX_FINDINGS_PER_CHECK]:
        counts["mixed_status"] += 1
        findings.append(
            _finding(
                "application sources",
                "(a notification's message is its own element)",
                f"A notification (role status/alert) renders its message as a bare text node next to its button: `{snippet}` -- "
                "the element's text becomes the message PLUS the button's name.",
                "Wrap the message in an element of its own beside the button: "
                "`<div role=\"status\"><span>{message}</span>{undo && <button type=\"button\">Undo</button>}</div>`.",
            )
        )

    counts["text_alone"] = 0
    quoted = [literal for literal, _ in literals]
    for page in snapshot or []:
        mixed = text_beside_controls(str(page.get("aria") or ""), quoted)
        if mixed and counts["text_alone"] < MAX_FINDINGS_PER_CHECK:
            counts["text_alone"] += 1
            findings.append(
                _finding(
                    f"route {page.get('route')}",
                    ", ".join(f"`{m}`" for m in mixed[:3]),
                    f"The page shows {', '.join(repr(m) for m in mixed[:3])} as a bare text node in the same element as a "
                    "control, so the element's text is the message PLUS the control's name.",
                    "Put the message in an element of its own next to the control: "
                    "`<div role=\"status\"><span>Note trashed</span><button>Undo</button></div>`.",
                )
            )

    counts["title_outside"] = 0
    for page in snapshot or []:
        outside = [t for t in page.get("titleOutside") or [] if isinstance(t, dict)]
        if outside and counts["title_outside"] < MAX_FINDINGS_PER_CHECK:
            counts["title_outside"] += 1
            examples = ", ".join(f"{t.get('item')!r} (title {t.get('title')!r})" for t in outside[:3])
            findings.append(
                _finding(
                    f"route {page.get('route')}",
                    "(a mark the requirement gives an item is found inside the item)",
                    f"{len(outside)} item(s) carry their title/tooltip only on the item's own container, nothing inside "
                    f"them has it: {examples}.",
                    "Keep the title on the container AND put the same title on a visible element that already exists "
                    "inside the item (its heading, or the icon/control that shows the state). Add no empty element for it.",
                )
            )

    counts["title_twice"] = 0
    for page in snapshot or []:
        twice = [t for t in page.get("titleTwice") or [] if isinstance(t, dict)]
        if twice and counts["title_twice"] < MAX_FINDINGS_PER_CHECK:
            counts["title_twice"] += 1
            examples = ", ".join(f"{t.get('item')!r} ({t.get('count')}x title {t.get('title')!r})" for t in twice[:3])
            findings.append(
                _finding(
                    f"route {page.get('route')}",
                    "(a mark the requirement gives an item is found inside the item, once)",
                    f"{len(twice)} item(s) carry the same title on two or more elements inside them: {examples}. A locator "
                    "scoped to the item finds several and stops.",
                    "Keep that title on the item's container and on exactly ONE visible element inside it; take it off the others.",
                )
            )

    counts["title_not_control"] = 0
    for page in snapshot or []:
        bare = [t for t in page.get("titleNotControl") or [] if isinstance(t, dict)]
        if bare and counts["title_not_control"] < MAX_FINDINGS_PER_CHECK:
            counts["title_not_control"] += 1
            examples = ", ".join(repr(t.get("title")) for t in bare[:3])
            findings.append(
                _finding(
                    f"route {page.get('route')}",
                    "(an item the user opens carries its title as a control inside its heading)",
                    f"{len(bare)} item(s) open on a click but their heading holds no button or link: {examples}. "
                    "Someone who opens the item by its title finds nothing to press.",
                    "Put the title control inside the heading -- `<h3><button type=\"button\" onClick={open}>Title</button></h3>` "
                    "(a link only where the requirement says link) -- and keep the card's own click handler.",
                )
            )

    counts["covered"] = 0
    for page in snapshot or []:
        route = str(page.get("route"))
        covered = [c for c in page.get("covered") or [] if isinstance(c, dict)]
        if covered and counts["covered"] < MAX_FINDINGS_PER_CHECK:
            counts["covered"] += 1
            examples = ", ".join(f"{c.get('title')!r} -> {c.get('hit')!r}" for c in covered[:3])
            findings.append(
                _finding(
                    f"route {route}",
                    "(an item opens when a person clicks it)",
                    f"The middle of {len(covered)} openable item(s) is covered by one of the item's OTHER controls "
                    f"({examples}): a person who clicks the card to open it presses that control instead.",
                    "Move the item's other controls off its middle -- for example ONE compact row along the bottom "
                    "edge (icon buttons with aria-labels, no wrapping), unless the requirement or its reference image "
                    "places them otherwise; the middle of the item is its body, which opens it. A card that a grid row "
                    "may stretch is a column -- `<article className=\"flex flex-col\">`, the title and content in a "
                    "`flex-1` body, the action row last with `mt-auto` -- so the body, not the actions, fills its middle.",
                )
            )
        for item in (page.get("unlabeled") or [])[:MAX_FINDINGS_PER_CHECK]:
            counts["unlabeled"] += 1
            findings.append(
                _finding(
                    f"route {route}",
                    "(form controls need an accessible name)",
                    f"This form control has no label: {item}",
                    "Associate a <label htmlFor> (or aria-label) carrying the field name from the requirement.",
                )
            )
        for message in (page.get("console") or [])[:3]:
            counts["console"] += 1
            findings.append(
                _finding(
                    f"route {route}",
                    "(pages must load without errors)",
                    f"Error in the browser console while loading the page: {message}",
                    "Fix the cause (failed request on load, exception during render, missing asset).",
                )
            )

    whole = " ".join((str(node.get("requirement_text") or "") + "\n" + requirement).split())
    stated = {q.strip().lower() for q in re.findall(r"[\u201c\"]([^\u201c\u201d\"\n<>]{2,80})[\u201d\"]", whole)}
    templates = [re.compile(re.sub(r"<[^>]+>", ".+", re.escape(q).replace(r"\<", "<").replace(r"\>", ">")), re.IGNORECASE)
                 for q in re.findall(r"[\u201c\"]([^\u201c\u201d\"\n]{3,80})[\u201d\"]", whole)
                 if "<" in q and ">" in q and len(re.sub(r"<[^>]+>|\s", "", q)) >= 3]

    def named(value: str) -> bool:
        return value.strip().lower() in stated or any(t.fullmatch(value.strip()) for t in templates)

    counts["unanswered"] = 0
    for method, path, where in unanswered_api_calls(project_dir)[:MAX_FINDINGS_PER_CHECK]:
        counts["unanswered"] += 1
        findings.append(
            _finding(
                where,
                "(every request the pages send must reach a backend route)",
                f"The frontend sends {method} {path} ({where}), and no route of the backend answers it: the control behind "
                "it can only fail.",
                f"Add the {method} route the page calls (and what it must do per the requirement), or call the route that exists.",
            )
        )
    findings.extend(response_shape_findings(project_dir, log=log))

    counts["relabeled"] = 0
    for page in snapshot or []:
        for item in (page.get("relabeled") or [])[:MAX_FINDINGS_PER_CHECK]:
            text, label = str(item.get("text") or ""), str(item.get("label") or "")
            if not named(text) or named(label):
                continue
            if not relabeled_control(item, str(page.get("aria") or "")):
                log(f"[audit] (diagnostic, not reported) route {page.get('route')}: <{item.get('tag')}> aria-label={label!r} "
                    f"shows {text!r}, but it is no control (a grid cell is named by its coordinate)")
                continue
            counts["relabeled"] += 1
            findings.append(
                _finding(
                    f"route {page.get('route')}",
                    f"(the requirement names this element \u201c{text}\u201d)",
                    f'This <{item.get("tag")}> shows the stated name "{text}", but aria-label="{label}" replaces it: its '
                    f'accessible name is "{label}", and nothing that looks the element up by its stated name finds it.',
                    f'Remove the aria-label, or set it to exactly "{text}" -- the accessible name must be the stated name.',
                )
            )

    try:
        stated_findings, stated_counts = stated_control_findings(node, project_dir, owner_of=owner_of)
    except Exception as exc:
        log(f"[audit] stated-control check crashed: {exc!r}")
        stated_findings, stated_counts = [], {"stated_role": 0, "stated_missing": 0}
    already = {f["requirement"] for f in findings if f["where"] == "control role"}
    findings.extend(f for f in stated_findings if not any(f"“{literal}”" in f["requirement"] for literal in already))
    counts.update(stated_counts)

    try:
        inert = inert_control_findings(node, project_dir, log=log)
    except Exception as exc:
        log(f"[audit] inert-control check crashed: {exc!r}")
        inert = []
    counts["inert"] = len(inert)
    findings.extend(inert)

    counts["glued"] = 0
    whole = str(node.get("requirement_text") or "").strip() or describe_node(node)
    for route, role, name, stated in glued_names(named_controls(snapshot or []), display_names(whole), whole + "\n" + requirement)[:MAX_FINDINGS_PER_CHECK]:
        counts["glued"] += 1
        rest = name[len(stated):]
        findings.append(
            _finding(
                f"route {route}",
                f"(the requirement names this {role} “{stated}”)",
                f'This {role}\'s accessible name is "{name[:90]}": the stated name "{stated}" with "{rest[:40]}" run straight '
                f"on inside the same element. Nothing that looks for the {role} named exactly “{stated}” finds it.",
                f'Keep the accessible name exactly "{stated}": only the name goes inside the <a>/<button>; move the extra text '
                f'("{rest[:30]}") outside the element, e.g. `<li><a href=...>{{name}}</a> <span>...</span></li>`.',
            )
        )

    try:
        entry_found, entry_counts = entry_findings(entry_parsed, snapshot, findings, log=log)
    except Exception as exc:
        log(f"[audit] entry check crashed: {exc!r}")
        entry_found, entry_counts = [], {}
    counts.update(entry_counts)
    findings.extend(entry_found)

    counts["activation"] = 0
    try:
        if activation_enabled():
            whole = str(node.get("requirement_text") or "").strip() or describe_node(node)
            said = activation_statements(whole)
            jobs = [{"route": route, "role": s["role"]} for s in said for route in activation_routes(s, snapshot)][:2]
            if jobs:
                activation_found = activation_findings(said, activation_probe(project_dir, jobs, log=log), log=log)
                counts["activation"] = len(activation_found)
                findings.extend(_capped(activation_found, MAX_FINDINGS_PER_CHECK))
    except Exception as exc:
        log(f"[audit] activation probe crashed: {exc!r}")

    counts["commit"] = 0
    try:
        if commit_enabled():
            whole = str(node.get("requirement_text") or "").strip() or describe_node(node)
            for said in commit_statements(whole)[:1]:
                job = commit_job(said, snapshot)
                if job is None:
                    log(f"[audit] (diagnostic, not reported) commit statement about a {said['thing']!r}: no known role, not probed")
                    continue
                commit_found = commit_findings(said, commit_probe(project_dir, job, log=log), log=log)
                counts["commit"] = len(commit_found)
                findings.extend(_capped(commit_found, MAX_FINDINGS_PER_CHECK))
    except Exception as exc:
        log(f"[audit] commit probe crashed: {exc!r}")

    counts["commit_keys"] = 0
    try:
        if keys_enabled():
            whole = str(node.get("requirement_text") or "").strip() or describe_node(node)
            said_keys = key_statements(whole)
            job = keys_job(said_keys, snapshot)
            if job:
                keys_found = keys_findings(said_keys, keys_probe(project_dir, job, log=log), log=log)
                counts["commit_keys"] = len(keys_found)
                findings.extend(_capped(keys_found, MAX_FINDINGS_PER_CHECK))
    except Exception as exc:
        log(f"[audit] commit keys probe crashed: {exc!r}")

    counts["structure"] = 0
    try:
        if structure_enabled():
            whole = str(node.get("requirement_text") or "").strip() or describe_node(node)
            said_structure = structure_statement(whole)
            job = structure_job(said_structure, snapshot)
            if job:
                structure_found = structure_findings(said_structure, job, structure_probe(project_dir, job, log=log), log=log)
                counts["structure"] = len(structure_found)
                findings.extend(_capped(structure_found, MAX_FINDINGS_PER_CHECK))
    except Exception as exc:
        log(f"[audit] structure probe crashed: {exc!r}")

    counts["interaction"] = 0
    try:
        whole = str(node.get("requirement_text") or "").strip() or describe_node(node)
        interaction_found = interaction_findings(whole, project_dir, snapshot, log=log)
        counts["interaction"] = len(interaction_found)
        findings.extend(_capped(interaction_found, MAX_FINDINGS_PER_CHECK))
    except Exception as exc:
        log(f"[audit] interaction probes crashed: {exc!r}")

    counts["grid_layout"] = 0
    for page in snapshot or []:
        layout = page.get("gridLayout") if isinstance(page.get("gridLayout"), dict) else {}
        for item in layout.get("found") or []:
            if not isinstance(item, dict):
                continue
            rows, stacked = int(item.get("rowsChecked") or 0), int(item.get("stackedRows") or 0)
            if not (rows and stacked * 2 > rows):
                if item.get("sameLineRowPairs"):
                    log(f"[audit] (diagnostic, not reported) route {page.get('route')}: rows of {item.get('grid')!r} side by side "
                        f"on one line ({item.get('sameLineRowPairs')} of {item.get('rowPairs')} row pairs)")
                continue
            if counts["grid_layout"] >= MAX_FINDINGS_PER_CHECK:
                break
            counts["grid_layout"] += 1
            example = item.get("example") if isinstance(item.get("example"), dict) else {}
            row_text = " ".join(str(example.get("row") or "").split())[:60]
            via = f" (reached by clicking “{page.get('via')}” on /)" if page.get("via") else ""
            findings.append(_finding(
                f"route {page.get('route')}",
                "(a grid / table shows each row as one line of cells)",
                f"The cells of a grid row are stacked vertically: {stacked} of {rows} rows of the {item.get('grid')!r} "
                f"grid{via} show their cells one below the other instead of side by side"
                + (f" (e.g. the row {row_text!r})" if row_text else "") + ". Whoever reads a cell by its row and column, "
                "or clicks the cell to the right of another, does not find it there.",
                "Give every role=row a horizontal layout (flex or CSS grid, e.g. `display: flex` / "
                "`grid-template-columns`) so its cells sit side by side, and the rows one below the other; keep the "
                "roles, names and order of the cells as they are.",
            ))

    counts["visual"] = 0
    try:
        visual_text = str(node.get("requirement_text") or "").strip() or describe_node(node)
        visual = visual_findings(visual_text, snapshot or [], log=log)
        counts["visual"] = len(visual)
        findings.extend(_capped(visual, MAX_FINDINGS_PER_CHECK))
    except Exception as exc:
        log(f"[audit] visual checks crashed: {exc!r}")

    try:
        display = display_form_findings(whole, project_dir)
    except Exception as exc:
        log(f"[audit] display-form check crashed: {exc!r}")
        display = []
    counts["display_form"] = len(display)
    findings.extend(display)

    try:
        nav, nav_logged = nav_edge_findings(whole, project_dir, findings, owner_of=owner_of)
    except Exception as exc:
        log(f"[audit] nav-edge check crashed: {exc!r}")
        nav, nav_logged = [], []
    counts["nav_edge"] = len(nav)
    findings.extend(nav)
    if nav_logged:
        log(f"[audit] (diagnostic, not reported) stated navigation edges absent / not on their source page: {nav_logged[:10]}")

    try:
        from .batch_ui_checks import batch_ui_findings
        findings.extend(batch_ui_findings(project_dir, node, requirement, log=log))
    except Exception as exc:
        log(f"[audit] batch UI checks crashed: {exc!r}")

    from .seed_presence import seed_presence_findings; findings.extend(seed_presence_findings(requirement, whole, project_dir, log=log))
    findings.extend(breadcrumb_findings(whole, project_dir)[:MAX_FINDINGS_PER_CHECK])
    v149 = v149_findings(project_dir, whole, snapshot, log=log,
                         leaf_ids=[str(m) for m in (node.get("members") or [node.get("id")]) if m],
                         source_owner_of=source_owner_of)
    counts["v149"] = len(v149)
    findings.extend(v149)
    log("[audit] deterministic layer: " + ", ".join(f"{k}={v}" for k, v in counts.items()) + f" ({len(literals)} quoted literal(s) checked)")
    return findings

def display_form_findings(whole: str, project_dir: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for item in inconsistent_display_forms(whole, project_dir)[:_per_check()]:
        where = f"{item['file']}:{item['line']}"
        if item["kind"] == "raw-stored-value":
            words = " / ".join(f"“{w}”" for w in str(item["stated"]).split("/"))
            findings.append(_finding(
                where,
                f"(the requirement shows these values as {words})",
                f"{where} renders {item['app_form']} raw, and {item['evidence'][:200]}: the page shows the stored "
                f"lower-case word where the requirement states {words}.",
                f"Show the stated form: map the stored value to exactly {words} where it is rendered (one small display "
                "helper used everywhere this field is shown). Keep the stored values and the API as they are.",
            ))
        elif item["kind"] == "template-part-only":
            template = str(item["stated"]).split(" | ")[-1]
            findings.append(_finding(
                where,
                f"(the requirement states the heading “{template}”)",
                f"{where}: {item['app_form']} renders only the last part of the stated heading “{template}”.",
                f"Render the whole stated heading “{template}” as the <h1> text (both parts joined by “/”), not the "
                "last part alone.",
            ))
    return findings

def nav_edge_findings(whole: str, project_dir: Path, earlier: list[dict[str, str]],
                      owner_of=None) -> tuple[list[dict[str, str]], list[str]]:
    from .finding_owner import tag

    findings: list[dict[str, str]] = []
    logged: list[str] = []
    for item in missing_stated_edges(whole, project_dir):
        match = re.fullmatch(r"(\w+) named '(.*)'|(\w+) named \"(.*)\"", str(item["expected"]))
        role = (match.group(1) or match.group(3)) if match else "control"
        name = (match.group(2) or match.group(4)) if match else str(item["expected"])
        source = str(item.get("source") or "")
        if source.startswith("(chained)"):
            source = "the page the requirement's previous step opens"
        if item["verdict"] not in ("WRONG_ROLE", "NOT_A_CONTROL"):
            logged.append(f"{item['verdict']} {item['req']} {role} “{name}”")
            continue
        if len(findings) >= _per_check():
            continue
        if any(f"“{name}”" in (f["requirement"] + f["problem"]) and "role" in f["problem"] for f in earlier):
            continue
        have = "; ".join(str(h) for h in (item.get("have") or [])[:3])[:220]
        if item["verdict"] == "WRONG_ROLE":
            problem = (f"The requirement states a {role} named “{name}” ({source}), but every control of that name "
                       f"has another role: {have}. Whoever looks for the {role} by its name finds none.")
        else:
            problem = (f"The requirement states a {role} named “{name}” ({source}), but the name only occurs as plain "
                       f"text (a heading or paragraph), never on a control: {have}.")
        finding = _finding(
            f"{item['req']}: {role} “{name}”",
            f"(the requirement: {str(item['quote'])[:200]})",
            problem,
            f"Where the requirement places it ({source}), render a {role} whose accessible name is exactly “{name}” and "
            "that goes where the requirement says" + (" (<Link to=...>)" if role == "link" else "")
            + (". It replaces the control of that name with the other role, unless the requirement names that one too"
               if item["verdict"] == "WRONG_ROLE" else ". Keep the heading or text of that name as it is") + ".",
        )
        owners = owner_of(name) if owner_of is not None else []
        if owners and str(item.get("req")) in owners:
            tag(finding, owners)
        findings.append(finding)
    return findings, logged

_RESTART_CACHE: dict[str, list[dict[str, str]]] = {}
_RESTART_COMPLETE: dict[str, bool] = {}

def _inputs_digest(project_dir: Path, parts: tuple[str, ...] = ("backend",)) -> str:
    import hashlib
    digest = hashlib.sha256()
    skip = {"node_modules", "dist", "build", "test-e2e", "tests", "test", "__tests__", "test-results",
            "playwright-report", ".arc-test-db", ".git", "coverage"}
    database = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".sqlite-wal", ".sqlite-shm", ".log")
    for part in parts:
        root = Path(project_dir) / part
        for path in sorted(root.rglob("*")) if root.is_dir() else []:
            rel = path.relative_to(root)
            if any(seg in skip for seg in rel.parts) or not path.is_file() or path.name.lower().endswith(database):
                continue
            digest.update(f"{part}/{rel}".encode("utf-8", "replace"))
            try:
                digest.update(path.read_bytes())
            except OSError:
                continue
        for package in ("sqlite3", "express", "better-sqlite3"):
            digest.update(f"{part}:{package}:{(root / 'node_modules' / package).is_dir()}".encode())
    return digest.hexdigest()

def _cached_live_check(key: str, label: str, run, log, retry_incomplete: bool = True) -> list[dict[str, str]]:
    from .batch_ui_checks import RECHECK_LEDGER

    ledger = RECHECK_LEDGER.get()
    if key in _RESTART_CACHE:
        log(f"[audit] {label}: inputs unchanged, {len(_RESTART_CACHE[key])} finding(s) reused")
        if ledger is not None and not _RESTART_COMPLETE.get(key, True):
            ledger.setdefault("incomplete", []).append(label)
        return _RESTART_CACHE[key]
    result = run()
    findings, completed = result if isinstance(result, tuple) else (result, bool(result))
    if completed or not retry_incomplete:
        _RESTART_CACHE[key] = findings
        _RESTART_COMPLETE[key] = bool(completed)
    if ledger is not None and not completed:
        ledger.setdefault("incomplete", []).append(label)
    return findings

def v149_findings(project_dir: Path, whole: str, snapshot: list[dict[str, Any]] | None, log=print,
                  leaf_ids: list[str] | None = None, source_owner_of=None) -> list[dict[str, str]]:
    from .settings import SETTINGS, setting_seconds
    found: list[dict[str, str]] = []
    if SETTINGS.get("det_code_checks", True):
        try:
            from .code_checks import code_check_findings
            found.extend(code_check_findings(Path(project_dir), whole, log=log))
        except Exception as exc:
            log(f"[audit] code checks crashed: {exc!r}")
    if SETTINGS.get("det_nested_names", True):
        try:
            from .nested_names import nested_control_findings, nested_control_source_findings

            source = nested_control_source_findings(Path(project_dir), requirement=whole)
            found.extend(source)
            named = " ".join(f.get("problem", "") for f in source)
            for item in nested_control_findings(snapshot or [], requirement=whole):
                outer = re.match(r"On page \S+, (\w+) ", item.get("problem", ""))
                if outer and (f'role="{outer.group(1)}"' in named or f"<{outer.group(1)}" in named):
                    continue
                found.append(item)
        except Exception as exc:
            log(f"[audit] nested-name check crashed: {exc!r}")
    if SETTINGS.get("det_stated_messages", True):

        try:
            from .stated_messages import stated_message_findings
            found.extend(stated_message_findings(whole, Path(project_dir), log=log))
        except Exception as exc:
            log(f"[audit] stated-message check crashed: {exc!r}")
    if SETTINGS.get("det_router_links", True):

        try:
            from .router_links import router_link_findings
            found.extend(router_link_findings(Path(project_dir), log=log))
        except Exception as exc:
            log(f"[audit] router-link check crashed: {exc!r}")
    if SETTINGS.get("det_stage_controls", True):

        try:
            from .stage_controls import entry_link_findings, fix_entry_buttons, stage_control_findings
            if SETTINGS.get("det_entry_link_rewrite", True):
                fix_entry_buttons(Path(project_dir), whole, log=log)
            found.extend(stage_control_findings(Path(project_dir), log=log))
            found.extend(entry_link_findings(Path(project_dir), whole, log=log))
        except Exception as exc:
            log(f"[audit] stage-control check crashed: {exc!r}")
    if SETTINGS.get("det_duplicate_names", True):

        try:
            from .duplicate_names import duplicate_name_source_findings
            found.extend(duplicate_name_source_findings(Path(project_dir), log=log, owner_of=source_owner_of))
        except Exception as exc:
            log(f"[audit] duplicate-name check crashed: {exc!r}")
    if SETTINGS.get("det_role_ladder", True):
        try:
            from .role_ladder import role_ladder_findings
            found.extend(role_ladder_findings(whole, Path(project_dir), log=log))
        except Exception as exc:
            log(f"[audit] role-ladder check crashed: {exc!r}")
    if SETTINGS.get("det_session_probe", True):

        static_refusal = any(str(f.get("where", "")).startswith("login_session_refusal") for f in found)
        try:
            from .session_probe import session_check
            import hashlib
            key = ("session:" + str(Path(project_dir).resolve()) + ":" + _inputs_digest(Path(project_dir), ("backend", "frontend"))
                   + ":" + hashlib.sha256(whole.encode("utf-8", "replace")).hexdigest())
            live = _cached_live_check(key, "session probe", lambda: session_check(
                whole, Path(project_dir), time_limit=setting_seconds("det_session_probe_sec", 60.0), log=log), log)
            found.extend(f for f in live if not (static_refusal and "provisioned accounts" not in str(f.get("where", ""))))
        except Exception as exc:
            log(f"[audit] session probe crashed: {exc!r}")
    if SETTINGS.get("det_outsider_walk", True):

        try:
            from .outsider_walk import outsider_walk_check
            import hashlib
            key = ("walk:" + str(Path(project_dir).resolve()) + ":" + _inputs_digest(Path(project_dir), ("backend", "frontend"))
                   + ":" + hashlib.sha256(whole.encode("utf-8", "replace")).hexdigest())
            found.extend(_cached_live_check(key, "outsider walk", lambda: outsider_walk_check(
                whole, Path(project_dir), time_limit=setting_seconds("det_outsider_walk_sec", 90.0), log=log), log,
                retry_incomplete=False))
        except Exception as exc:
            log(f"[audit] outsider walk crashed: {exc!r}")
    if SETTINGS.get("det_plan_walk", True) and leaf_ids:

        try:
            from .outsider_walk import plan_walk_check
            import hashlib
            wanted = sorted({str(leaf) for leaf in leaf_ids})
            key = ("planwalk:" + str(Path(project_dir).resolve()) + ":" + _inputs_digest(Path(project_dir), ("backend", "frontend"))
                   + ":" + hashlib.sha256((whole + "|" + ",".join(wanted)).encode("utf-8", "replace")).hexdigest())
            found.extend(_cached_live_check(key, "plan walk", lambda: plan_walk_check(
                whole, Path(project_dir), time_limit=setting_seconds("det_plan_walk_sec", 300.0), log=log, leaf_ids=wanted), log))
        except Exception as exc:
            log(f"[audit] plan walk crashed: {exc!r}")
    if SETTINGS.get("det_seed_restart", True):
        try:
            from .seed_restart import restart_check
            import hashlib
            key = ("restart:" + str(Path(project_dir).resolve()) + ":" + _inputs_digest(Path(project_dir))
                   + ":" + hashlib.sha256(whole.encode("utf-8", "replace")).hexdigest())
            found.extend(_cached_live_check(key, "restart check", lambda: restart_check(
                Path(project_dir), whole, time_limit=setting_seconds("det_seed_restart_sec", 60.0), log=log), log))
        except Exception as exc:
            log(f"[audit] restart check crashed: {exc!r}")
    return found
