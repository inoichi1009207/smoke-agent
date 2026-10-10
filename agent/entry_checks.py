from __future__ import annotations

import re
from typing import Any

from .stated_controls import requirement_chunks, unquoted_unique_controls

MAX_FINDINGS_PER_CHECK = 8

_ROLE = r"link|button|heading|tab|checkbox|textbox|searchbox|combobox|menuitem"
_OPEN = r"link or button|button or link"
_Q = r"[`\"\u201c\u300c]([^`\"\u201d\u300d\n]{1,60})[`\"\u201d\u300d]"
_NAMED = r"(?:named|called|labell?ed|with\s+(?:the\s+)?accessible\s+name)"
_LEAD = r"(?:the\s+unique|unique|the\s+only|exactly\s+one)"
_QN = _Q.replace("(", "(?P<name>", 1)
UNIQUE_PATTERNS = [

    re.compile(r"\b" + _LEAD + r"\s+" + _QN + r"\s+(?P<role>" + _OPEN + "|" + _ROLE + r")\b", re.IGNORECASE),

    re.compile(r"\b" + _LEAD + r"\s+(?:visible\s+)?(?P<role>" + _OPEN + "|" + _ROLE + r")\s+" + _NAMED + r"\s+" + _QN, re.IGNORECASE),
]
NEGATED = re.compile(r"\bnot\s+(?:the\s+)?(?:unique|only|exactly)\b|\bmore than one\b|\bneed not\b", re.IGNORECASE)

PER_ITEM = re.compile(r"\b(?:each|every|per)\s+(?!page\b|pages\b)", re.IGNORECASE)
BLANKET = re.compile(r"\b(?:each|every)\s+named\s+(?:control|action|element|link|button)[\w ,]*?\bis\s+unique\s+on\s+"
                     r"(?:the|its)\s+(?:active|current)\s+page\b", re.IGNORECASE)
HOME = re.compile(r"\bhome[- ]?page\b|\bhome\s+screen\b", re.IGNORECASE)
PAGE_NAME = re.compile(r"\b(?:the|its|their|a|an)\s+((?:[\w-]+\s+){0,2}?[\w-]+)\s+page\b", re.IGNORECASE)
EVERY_PAGE = re.compile(r"\bglobal\b|\b(?:every|each|all)\s+pages?\b", re.IGNORECASE)

CONTAINER = re.compile(r"\b(?:in|inside|within)\s+(?:the|its|a|an|each|every)?\s*(?:[\w-]+\s+){0,3}?"
                       r"(navigation\s+bar|nav\s*bar|navigation|nav|header|toolbar|sidebar|menu\s*bar|menu|banner|dialog|"
                       r"modal|form|footer|table|grid|list|row|card|panel|section|region)\b", re.IGNORECASE)
CONTAINER_ROLES = {
    "navigation bar": {"navigation"}, "nav bar": {"navigation"}, "navbar": {"navigation"}, "navigation": {"navigation"},
    "nav": {"navigation"}, "header": {"banner"}, "banner": {"banner"}, "footer": {"contentinfo"}, "toolbar": {"toolbar"},
    "sidebar": {"complementary"}, "menu bar": {"menubar"}, "menubar": {"menubar"}, "menu": {"menu"},
    "dialog": {"dialog", "alertdialog"}, "modal": {"dialog", "alertdialog"}, "form": {"form"}, "table": {"table", "grid"},
    "grid": {"grid", "table", "treegrid"}, "list": {"list"}, "row": {"row"},
}

EXACT_NAME = re.compile(r"\b(?P<role>links?|buttons?)\s+(?:whose|with\s+(?:an?\s+)?)\s*(?:exact\s+)?accessible\s+name\s+"
                        r"(?:is|equals|matches)\s+(?:exactly\s+)?(?:the|its|their)\s+(?P<thing>(?:[a-z][\w-]*\s+){0,2}?[a-z][\w-]*)"
                        r"\s+name\b", re.IGNORECASE)
UI_METADATA = re.compile(r"[:\uff1a]|\d{4}-\d{2}-\d{2}|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{1,2}:\d{2}\b")
ACTION_PREFIX = re.compile(r"^(?:open|view|edit|show|go to|visit|select|launch)\b", re.IGNORECASE)
CONTROL = {"link", "button"}

def _norm(name: str) -> str:
    return " ".join(str(name).split()).casefold()

def _sentences(chunk: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?;])\s+|\s+-\s+(?=\*\*)|\s+#{2,6}\s+", chunk) if s.strip()]

MOVES = re.compile(r"\b(?:and\s+)?then\b|\bafter\b|\bopen(?:s|ed)?\b|\breached\b|\bleads?\s+to\b|\bnavigates?\s+to\b|\breturns?\s+to\b|"
                   r"\bgoes\s+to\b|\bfrom\b|\bredirect(?:s|ed)?\s+to\b", re.IGNORECASE)

def _page_at(sentence: str, start: int, end: int) -> tuple[str, str]:
    cuts = [0] + [m.start() for m in MOVES.finditer(sentence)] + [len(sentence)]
    left = max(c for c in cuts if c <= start)
    right = min(c for c in cuts if c >= end)
    return _page_of(sentence[left:right])

def _roles(word: str) -> frozenset[str]:
    word = word.lower()
    if " or " in word:
        return frozenset({"link", "button"})
    return frozenset({word.rstrip("s") if word in ("links", "buttons") else word})

def _page_of(sentence: str) -> tuple[str, str]:
    pages = []
    if HOME.search(sentence):
        pages.append(("home", ""))
    for match in PAGE_NAME.finditer(HOME.sub(" ", sentence)):
        words = match.group(1).lower().split()
        if words[-1] in ("active", "current", "same", "next", "previous", "target", "this", "that", "whole", "entire"):
            continue
        pages.append(("slug", "-".join(words)))
    pages = list(dict.fromkeys(pages))
    if len(pages) == 1:
        return pages[0]
    if not pages and EVERY_PAGE.search(sentence):
        return ("every", "")
    return ("", "")

def _container_of(sentence: str) -> tuple[bool, frozenset[str] | None]:
    match = CONTAINER.search(sentence)
    if not match:
        return False, frozenset()
    word = " ".join(match.group(1).lower().split())
    roles = CONTAINER_ROLES.get(word)
    return True, (frozenset(roles) if roles else None)

def statements(whole: str) -> dict[str, list[dict[str, Any]]]:
    unique: dict[tuple, dict[str, Any]] = {}
    exact: dict[tuple, dict[str, Any]] = {}

    def add_unique(roles: frozenset[str], name: str, sentence: str, page: tuple[str, str], scoped: bool,
                   container: frozenset[str] | None, how: str) -> None:
        name = name.strip()
        if not name or len(name) > 60 or re.search(r"[<>{}/\\=]", name):
            return
        key = (roles, _norm(name), page, container)
        unique.setdefault(key, {"roles": roles, "name": name, "sentence": sentence.strip()[:240], "page": page,
                                "scoped": scoped, "container": container, "how": how})

    for chunk, _given in requirement_chunks(whole):
        sentences = _sentences(chunk)
        blanket = any(BLANKET.search(s) for s in sentences)
        for index, sentence in enumerate(sentences):
            scoped, container = _container_of(sentence)
            if not NEGATED.search(sentence) and not PER_ITEM.search(sentence):
                for pattern in UNIQUE_PATTERNS:
                    for match in pattern.finditer(sentence):
                        add_unique(_roles(match.group("role")), match.group("name"), sentence,
                                   _page_at(sentence, match.start(), match.end()), scoped, container, "stated")
                for role, name in unquoted_unique_controls(sentence):
                    at = sentence.find(f"unique {name} ")
                    page = _page_at(sentence, at, at + len(name) + 8) if at >= 0 else ("", "")
                    add_unique(frozenset({role}), name, sentence, page, scoped, container, "stated")
            if blanket:

                for match in re.finditer(r"\bhome[- ]?page\s+" + _Q + r"\s+(link|button)\b", sentence, re.IGNORECASE):
                    add_unique(_roles(match.group(2)), match.group(1), sentence, ("home", ""), False, frozenset(), "blanket")
                for match in re.finditer(_Q + r"\s+(link|button)\s+on\s+the\s+home[- ]?page\b", sentence, re.IGNORECASE):
                    add_unique(_roles(match.group(2)), match.group(1), sentence, ("home", ""), False, frozenset(), "blanket")
            for match in EXACT_NAME.finditer(sentence):
                if re.search(r"\b(?:links?|buttons?)\s+(?:or|and/or|/)\s*$", sentence[:match.start()], re.IGNORECASE):
                    continue
                page = _page_at(sentence, match.start(), match.end())

                names_page = bool(HOME.search(sentence) or PAGE_NAME.search(sentence))
                where = page if page[0] else _page_of(sentences[index - 1]) if index and not names_page else ("", "")
                thing = match.group("thing").lower().split()[-1]
                key = (_roles(match.group("role")), thing, where)
                exact.setdefault(key, {"roles": _roles(match.group("role")), "thing": thing, "page": where,
                                       "sentence": sentence.strip()[:240]})
    seeds = _seed_names(whole, {s["thing"] for s in exact.values()})
    for item in exact.values():
        item["seeds"] = seeds.get(item["thing"], [])
    return {"unique": list(unique.values()), "exact": list(exact.values())}

def _seed_names(whole: str, things: set[str]) -> dict[str, list[str]]:
    text = " ".join(whole.split())
    found: dict[str, list[str]] = {}
    for thing in things:
        names = []
        for match in re.finditer(r"\b" + re.escape(thing) + r"s?\s+(?:named\s+|called\s+)?" + _Q, text, re.IGNORECASE):
            name = match.group(1).strip()
            if len(name) >= 2 and re.search(r"[A-Za-z0-9]", name) and not re.search(r"[<>{}=\\]|^/", name):
                names.append(name)
        found[thing] = list(dict.fromkeys(names))[:20]
    return found

def stated_link_names(parsed: dict[str, list[dict[str, Any]]]) -> set[str]:
    names = {_norm(s["name"]) for s in parsed.get("unique", []) if s["roles"] == frozenset({"link"})}
    for item in parsed.get("exact", []):
        if item["roles"] == frozenset({"link"}):
            names.update(_norm(n) for n in item["seeds"])
    return names

_ID_SEGMENT = re.compile(r"/([\w-]+)/\d+(?:[/?#]|$)")

def link_list_cover(parsed: dict[str, list[dict[str, Any]]]) -> dict[str, set[str]]:
    seeds = {_norm(n) for item in parsed.get("exact", []) if item["roles"] == frozenset({"link"}) for n in item["seeds"]}
    things = {item["thing"].lower() for item in parsed.get("exact", []) if item["roles"] == frozenset({"link"})}
    return {"names": stated_link_names(parsed), "seeds": {s for s in seeds if s}, "things": things}

def covers_item_list(cover: dict[str, set[str]], items: list[tuple[str, str]]) -> bool:
    kinds = set()
    for thing in cover.get("things", set()):
        kinds |= {thing, thing + "s", thing + "es", thing[:-1] + "ies" if thing.endswith("y") else thing}
    for name, href in items:
        flat = _norm(name)
        if flat in cover.get("names", set()) or any(seed in flat for seed in cover.get("seeds", set())):
            return True
        segment = _ID_SEGMENT.search(str(href or "").split("?")[0])
        if segment and segment.group(1).lower() in kinds:
            return True
    return False

_ARIA = re.compile(r"^(?P<indent>\s*)-\s+(?P<body>.*?)\s*$")
_NODE = re.compile(r'^(?P<role>[a-z]+)(?:\s+"(?P<name>(?:[^"\\]|\\.)*)")?')

def aria_nodes(aria: str) -> list[tuple[int, str, str]]:
    nodes = []
    for line in str(aria or "").splitlines():
        match = _ARIA.match(line)
        if not match:
            continue
        body = match.group("body")
        quoted = re.match(r"^'(.*)'\s*:?$", body)
        if quoted:
            body = quoted.group(1).replace("''", "'")
        node = _NODE.match(body)
        if not node or node.group("role") in ("text",):
            continue
        name = (node.group("name") or "").replace('\\"', '"').replace("\\\\", "\\")
        nodes.append((len(match.group("indent")), node.group("role"), name))
    return nodes

def _path(url: str) -> str:
    path = re.sub(r"^[a-z]+://[^/]+", "", str(url or ""), flags=re.IGNORECASE).split("?")[0].split("#")[0]
    return path.rstrip("/") or "/"

def reached_pages(snapshot: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    pages = []
    for entry in snapshot or []:
        if entry.get("crawled") or entry.get("error") or not entry.get("aria") or not entry.get("url"):
            continue
        route = str(entry.get("route") or "")
        if ":" in route or _path(entry["url"]) != _path(route):
            continue
        pages.append(entry)
    return pages

def _pages_for(page: tuple[str, str], pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kind, slug = page
    if kind == "home":
        return [p for p in pages if _path(p["route"]) == "/"]
    if kind == "slug":
        return [p for p in pages if _path(p["route"]).strip("/").lower() == slug]
    if kind == "every":
        return pages
    return []

def _visible_links(entry: dict[str, Any], name: str) -> int | None:
    links = entry.get("links")
    if not isinstance(links, list):
        return None
    return sum(1 for link in links if isinstance(link, dict) and _norm(link.get("name") or "") == _norm(name))

def _groups(nodes: list[tuple[int, str, str]], container: frozenset[str]) -> list[list[tuple[int, str, str]]]:
    if not container:
        return [nodes]
    groups = []
    for index, (indent, role, _name) in enumerate(nodes):
        if role not in container:
            continue
        inside = []
        for other in nodes[index + 1:]:
            if other[0] <= indent:
                break
            inside.append(other)
        groups.append(inside)
    return groups

def _mentioned(earlier: list[dict[str, str]], *needles: str) -> bool:
    return any(all(n in (f.get("where", "") + " " + f.get("requirement", "") + " " + f.get("problem", "")) for n in needles)
               for f in earlier)

def entry_findings(parsed: dict[str, list[dict[str, Any]]], snapshot: list[dict[str, Any]] | None,
                   earlier: list[dict[str, str]], log=print) -> tuple[list[dict[str, str]], dict[str, int]]:
    findings: list[dict[str, str]] = []
    counts = {"entry_unique": 0, "entry_exact_name": 0, "entry_role": 0}
    pages = reached_pages(snapshot)
    logged: list[str] = []

    for item in parsed.get("unique", []):
        if not item["page"][0] or item["container"] is None:
            logged.append(f"{'/'.join(sorted(item['roles']))} {item['name']!r} (no page or unknown container)")
            continue
        for entry in _pages_for(item["page"], pages):
            route = str(entry["route"])
            nodes = aria_nodes(entry["aria"])
            worst = max((sum(1 for _i, r, n in group if r in item["roles"] and _norm(n) == _norm(item["name"]))
                         for group in _groups(nodes, item["container"] or frozenset())), default=0)
            roles = "/".join(sorted(item["roles"]))
            if worst >= 2:
                if item["roles"] == frozenset({"link"}):
                    visible = _visible_links(entry, item["name"])
                    if visible is None or visible < 2:
                        logged.append(f"{route}: {worst} links {item['name']!r} in the ARIA tree, {visible} visible")
                        continue
                if counts["entry_unique"] >= MAX_FINDINGS_PER_CHECK:
                    continue
                if _mentioned(earlier + findings, f"route {route}", f"`{item['name']}`", "unique"):
                    continue
                counts["entry_unique"] += 1
                where = " inside one " + "/".join(sorted(item["container"])) if item["container"] else ""
                findings.append({
                    "where": f"route {route}",
                    "requirement": f"(the requirement: the unique {roles} `{item['name']}`) {item['sentence'][:200]}",
                    "problem": f"{worst} {roles} elements with the accessible name `{item['name']}` are shown on this "
                               f"page{where}; the requirement says there is exactly one, and a locator that finds two stops.",
                    "fix": f"Keep ONE {roles} named `{item['name']}` here and remove the other copies (or rename a copy only "
                           "if the requirement names it). A copy kept for another screen size must be display:none, so "
                           "that it leaves the accessibility tree.",
                })
                continue

            if item["roles"] <= CONTROL and len(item["roles"]) == 1 and not item["container"]:
                wanted = next(iter(item["roles"]))
                other = "button" if wanted == "link" else "link"
                have_wanted = any(r == wanted and _norm(n) == _norm(item["name"]) for _i, r, n in nodes)
                have_other = any(r == other and _norm(n) == _norm(item["name"]) for _i, r, n in nodes)
                if have_other and not have_wanted:
                    _role_finding(findings, counts, earlier, route, item["name"], wanted, other, item["sentence"])

    declared = {_norm(n) for it in parsed.get("exact", []) for n in it.get("seeds") or []}
    for item in parsed.get("exact", []):
        if not item["page"][0] or not item["seeds"]:
            logged.append(f"{'/'.join(sorted(item['roles']))} named after the {item['thing']} "
                          f"({'no page' if not item['page'][0] else 'no seeded name'})")
            continue
        wanted = next(iter(item["roles"]))
        other = "button" if wanted == "link" else "link"
        for entry in _pages_for(item["page"], pages):
            route = str(entry["route"])
            controls = [(r, n.strip()) for _i, r, n in aria_nodes(entry["aria"]) if r in CONTROL and n.strip()]
            for seed in item["seeds"]:
                exact = [r for r, n in controls if _norm(n) == _norm(seed)]
                if wanted in exact:
                    continue
                longer = [n for r, n in controls if r == wanted and _norm(seed) in _norm(n) and _norm(n) != _norm(seed)]
                others = [d for d in declared if d != _norm(seed) and _norm(seed) in d]
                theirs = [n for n in longer if any(d in _norm(n) for d in others)]
                if theirs:
                    logged.append(f"{route}: {wanted} {theirs[0][:60]!r} holds another declared {item['thing']} name, "
                                  f"not {seed!r} plus more")
                    longer = [n for n in longer if n not in theirs]
                extra = [n for n in longer if _extra_is_not_a_name(n, seed)]
                if extra:
                    name = extra[0]

                    if counts["entry_exact_name"] >= MAX_FINDINGS_PER_CHECK or _mentioned(
                            earlier + findings, f"route {route}", name[:40], "accessible name"):
                        continue
                    counts["entry_exact_name"] += 1
                    rest = name.replace(seed, "", 1).strip()
                    findings.append({
                        "where": f"route {route}",
                        "requirement": f"(the requirement: a {wanted} whose accessible name is the {item['thing']} name) "
                                       f"{item['sentence'][:200]}",
                        "problem": f"The {item['thing']} “{seed}” is shown through a {wanted} whose accessible name is "
                                   f"\"{name[:90]}\": the name plus \"{rest[:40]}\" inside the same element. No {wanted} "
                                   f"here is named exactly “{seed}”, so whoever looks for it by that name finds none.",
                        "fix": f"Put only the {item['thing']} name inside the <{'a' if wanted == 'link' else 'button'}>; move "
                               f"the other text (\"{rest[:30]}\") next to it, outside the element -- e.g. `<li><h3><a href=...>"
                               "{name}</a></h3><span>...</span></li>`. Keep the element's role and target.",
                    })
                elif longer:
                    logged.append(f"{route}: {wanted} {longer[0][:60]!r} holds the {item['thing']} name {seed!r} plus more")
                elif other in exact:
                    _role_finding(findings, counts, earlier, route, seed, wanted, other, item["sentence"],
                                  thing=item["thing"])
    if logged:
        log(f"[audit] (diagnostic, not reported) stated unique / exact-name controls not checked: {logged[:10]}")
    return findings, counts

def _extra_is_not_a_name(name: str, seed: str) -> bool:
    flat, stated = " ".join(name.split()), " ".join(seed.split())
    at = flat.casefold().find(stated.casefold())
    if at < 0:
        return False
    before, after = flat[:at], flat[at + len(stated):]
    if UI_METADATA.search(before + " " + after) or ACTION_PREFIX.match(before.strip()):
        return True
    if not after or not after[0].isalnum():
        return False

    last = stated[-1]
    return after[0].isupper() or (after[0].isdigit() and last.isalpha()) or (after[0].isalpha() and last.isdigit())

def _role_finding(findings: list[dict[str, str]], counts: dict[str, int], earlier: list[dict[str, str]], route: str,
                  name: str, wanted: str, other: str, sentence: str, thing: str = "") -> None:
    if counts["entry_role"] >= MAX_FINDINGS_PER_CHECK:
        return
    if _mentioned(earlier + findings, f"“{name}”", "role") or _mentioned(earlier + findings, f"`{name}`", wanted):
        return
    counts["entry_role"] += 1
    element = "<a href> / router <Link>" if wanted == "link" else '<button type="button">'
    what = f"a {wanted} whose accessible name is the {thing} name" if thing else f"a {wanted} named “{name}”"
    findings.append({
        "where": f"route {route}",
        "requirement": f"(the requirement: {what}) {sentence[:200]}",
        "problem": f"“{name}” is rendered here as a {other}, and no {wanted} of that name exists on the page; the "
                   f"requirement states a {wanted}, so whoever looks for the {wanted} named “{name}” finds none.",
        "fix": f"Render “{name}” as {element} -- role {wanted}, accessible name exactly “{name}” -- in place of the "
               f"{other}; keep where it leads / what it does and keep it inside the item's heading if it is there.",
    })
