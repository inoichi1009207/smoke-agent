from __future__ import annotations

import re

import json
from pathlib import Path
from typing import Any

from .conventions import PROJECT_CONVENTIONS
from .fsutil import write_text
from .json_tolerant import cached_tolerant_loads, tolerant_loads
from .llm import LLM
from .settings import SETTINGS

CONTRACT_KEYS = ("pages", "api", "session", "shared", "seed", "naming", "notes")

LOSSLESS_REPAIRS = ("removed comments", "accepted raw control", "closed an unterminated string", "escaped a raw control",
                    "inserted a missing comma", "escaped an inner quote", "quoted a bare key", "double-quoted",
                    "dropped a trailing comma", "moved a closing quote", "swapped an opening quote", "inserted a missing",
                    "ignored prose after the document")

def parse_contract(contract: str) -> dict[str, Any] | None:
    if not contract or not contract.strip():
        return None
    value, _notes = cached_tolerant_loads(contract, CONTRACT_KEYS)
    return value if isinstance(value, dict) else None

def _lossless(notes: list[str]) -> bool:
    return all(note.startswith(LOSSLESS_REPAIRS) for note in notes)

SYSTEM_PROMPT = f"""You are the ARCHITECT of a test-driven software factory. Before any test or code is written you fix the
interfaces the whole team will use, so that tests written from the requirements and code written from the
requirements meet in the middle.

{PROJECT_CONVENTIONS}
Output ONE JSON object and nothing else, with exactly these keys:
{{
  "pages": [{{"route": "/register", "file": "frontend/src/pages/RegisterPage.tsx", "requirements": ["REQ-1"],
             "purpose": "...", "links": [{{"text": "Register", "href": "/register", "where": "home page header"}}],
             "controls": [{{"label": "<the field's label, verbatim from the requirement>", "kind": "textbox|password|combobox|checkbox|button|link", "note": "..."}}]}}],
  "api": [{{"method": "POST", "path": "/api/register", "requirements": ["REQ-1"], "purpose": "...",
           "request": {{"field": "type / rule"}}, "response": {{"status": 201, "body": {{"user": {{"id": "string", "username": "string"}}}}}},
           "errors": [{{"status": 400, "body": {{"message": "<error message in the requirement's language>"}}}}]}}],
  "session": {{"mechanism": "httpOnly cookie `session_token`", "restore": "GET /api/me -> {{user}} or 401", "logout": "POST /api/logout"}},
  "shared": [{{"file": "frontend/src/api/index.ts", "exports": ["register(payload)", "login(id, pw)", "getCurrentUser()", "logout()"]}}],
  "seed": [{{"entity": "user", "requirements": ["REQ-2.2"], "fields": {{"username": "registered_user", "password": "Password123!", "email": "..."}},
            "related": "orders / passengers this record must own, in one line"}}],
  "naming": ["exact accessible names, ids and data-testids that every page must use, quoted verbatim from the requirements"],
  "notes": ["decisions that avoid ambiguity, e.g. which label text is unique per page"]
}}
Rules:
- Quote every user-visible name (link text, button name, label, error message, test id) VERBATIM from the requirement
  text; when the requirement gives ONE control two names (typically the same word in two languages: `登录` or
  `Login`), pick the FIRST one, state it, and never list the other: one element, one name. Two different choices
  (`Grid` or `List`, `Email` or `Phone`) are two controls and BOTH belong in the contract.
- The control KIND is part of the contract and must follow the requirement's own word: 链接/link -> kind "link"
  (a real anchor), 按钮/button -> "button", 复选框 -> "checkbox", 下拉选择框 -> "combobox". Do not reinterpret
  (e.g. never turn a required sign-out LINK into a button). Where the words leave the kind open -- "a link or
  button", "an entry", 入口 -- the kind is decided in this order: (1) the kind this requirement names for that control,
  also in another sentence that means the same control (same page, container and purpose), not merely the same word;
  (2) the kind the control already has when the existing application (an earlier stage) renders it; (3) these
  defaults: a pure navigation entry -- its only effect is to open another page: the entries that take a signed-out
  visitor to the sign-in and sign-up pages, the shared header's navigation entries, the entries of a navigation bar (a
  repository's / organization's sections, a record's tabs that open their own view), a record shown on a page that
  opens that record's page -- is kind "link"; any other control is kind "button" (for a list item's own title that
  opens the item in place: a button inside its heading).
  Error messages get the role the requirement names for them; role="alert" only when it names none.
- Every label on a page must be unique on that page (no two labels containing the same substring such as 密码/登录密码);
  say how they differ. Where the requirement ITSELF puts the same name in two named containers (two forms, two
  dialogs, two sections it names), both keep that name verbatim and the note says which container each belongs to:
  uniqueness is then per container, and a name the requirement quotes is never rewritten to make it unique.
- `seed` lists EVERY record the requirements say already exists in the system (accounts with their exact usernames,
  emails, ids and passwords; orders, passengers and other data they own). The application must create them on a fresh
  start. Scan the whole tree for phrases like "The system contains", "A verified account named", "has an order".
- A seed record's STATE is the one the requirement's own words give it, never a later one: a "deletable" / "archivable"
  / "pinnable" record is seeded BEFORE that action (not deleted, not archived, not pinned), even when a scenario's
  outcome shows it deleted or archived ("the Trash view displays the deleted note X"): that later state is reached by
  the user's own steps -- the test deletes it first -- and a record seeded already deleted is missing from the page the
  user starts on. A state a scenario's GIVEN or the requirement's seed sentences state as ALREADY true ("a Closed
  issue", "a Draft PR") is seeded as stated, and so is a state no user of this application can bring about (time has
  passed, an administrator acted); only a later state that a scenario's OUTCOME alone shows is left to the user's steps. (ADR-097: a note seeded "already in the trash" broke the trash flow in every later batch.)
- Sign-in: lock behind sign-in only what the requirement ties to a signed-in user (a scenario whose GIVEN says the
  user is logged in / authenticated, or the current user's own "My ..." data). Everything else -- reading, creating,
  editing, the actions offered on a content page -- works signed out as well. When one requirement shows something
  after login and another shows the same thing with no word about login, it is shown in both states. A control that a
  scenario's GIVEN places on a page ("the user is on the page edit page") is on THAT page. (ADR-099)
- One owner per rule (v160c): for every rule several requirements rely on, `shared` names the ONE file that owns it and
  `notes` says so ("formula evaluation: one backend module; the grid shows the server's results"): computation
  (the server computes, the page shows its results or imports the same module -- never a second engine), lookup of a
  record unique per owner (one helper, by owner AND name, never by the name alone), who the request is (one
  authentication middleware that every protected route uses and that rejects a session the requirement lets end), and
  one router file per resource, mounted once. Never plan two modules for one rule.
- Keep the design minimal: only what the requirements need.
"""

REVIEW_PROMPT = """You audit an interface contract against the requirement text it was derived from. The requirement
is the source of truth. Find every place where the contract contradicts or weakens it, in particular:
- control kind / ARIA role: the requirement's word decides (链接/link -> "link", 按钮/button -> "button",
  复选框 -> "checkbox", 下拉选择框/select -> "combobox"); a "link-styled button" is a contradiction; where the
  requirement leaves the kind open ("a link or button", "an entry", 入口) the kind is decided in this order and a
  kind that follows it is NOT a finding: (1) the kind the requirement names for that control, also in another sentence
  that means the same control; (2) the kind an existing control (an earlier stage) already has; (3) defaults: "link"
  for a pure navigation entry (the signed-out visitor's sign-in / sign-up entries, the shared header's navigation
  entries, a navigation bar's entries, a record's tabs, a record shown on a page that opens that record's page),
  "button" for any other control;
- accessible names, link texts, labels, option texts, default values, error-message wording, routes, redirects;
- two names the requirement gives to ONE control (the same word in two languages, A 或 B): the contract must contain
  exactly one of them; if it already has one, that is correct, NEVER add the other next to it. Two different choices
  (`Grid` or `List`) are two controls and must both stay;
- seed STATES: a record the requirement calls "deletable" / "archivable" / "pinnable" that the contract seeds already
  deleted, archived or pinned (e.g. `trashed: 1`) is a contradiction, even when a scenario's outcome shows it in that
  state -- that is its state AFTER the user's action. Put it back to the state before the action;
- anything the requirement demands that the contract omits (pages, controls, endpoints, persistence, session restore);
- a rule with two owners: two files for one computation (a server engine and a page engine of their own), two lookups
  of one record, a router listed twice -- name the one owner, the other uses it.
Output ONE JSON object and nothing else: the full corrected contract with the same keys as the input, plus a key
"corrections": a list of short strings describing each change (empty list when nothing had to change)."""

LARGE_NOTE = (
    "\n\nThis is a LARGE system. Keep the contract a compact skeleton (aim for < 9000 characters): for `pages` give "
    "route, file, requirements (ids) and a one-line purpose, plus only the GLOBAL navigation links in `links`; "
    "leave `controls` as an empty list. For `api` give method, path, requirements, a one-line purpose and the "
    "response shape in one line; omit `errors`. `shared` must list the files several requirements build on "
    "(API client, auth/session helpers, layout/header component, DB tables with their columns in `notes`). "
    "In a page's purpose never state the element KIND of list entries or other controls (link, button) unless the "
    "requirement itself names it: write what they are called (\"shelf entries named by the shelf\"), not what they "
    "are -- the kind follows the requirement and the conventions (ADR-099: \"shelf entries are links\" in the "
    "contract beat the conventions' button in every bookstack run)."
)

def related(requirements: Any, members: list[str]) -> bool:
    ids = [str(r).strip() for r in (requirements if isinstance(requirements, list) else [requirements]) if r]
    for req in ids:
        for member in members:

            if req == member or any(member.startswith(req + s) or req.startswith(member + s) for s in ".-"):
                return True
    return False

SLICE_THRESHOLD_CHARS = 7000

def contract_slice(contract: str, members: list[str]) -> str:
    if len(contract) <= SLICE_THRESHOLD_CHARS or not members:
        return contract
    try:
        data = json.loads(contract)
    except json.JSONDecodeError:

        if not contract.rstrip().endswith("}"):
            return contract
        data = parse_contract(contract)
    if not isinstance(data, dict):
        return contract
    sliced: dict[str, Any] = {}
    for key, value in data.items():
        if key == "pages" and isinstance(value, list):
            sliced[key] = [
                page if not isinstance(page, dict) or related(page.get("requirements"), members)
                else {"route": page.get("route"), "file": page.get("file"), "requirements": page.get("requirements")}
                for page in value
            ]
        elif key == "api" and isinstance(value, list):
            sliced[key] = [
                api if not isinstance(api, dict) or related(api.get("requirements"), members)

                else {"method": api.get("method"), "path": api.get("path"), "requirements": api.get("requirements"), "response": api.get("response")}
                for api in value
            ]
        elif key == "seed" and isinstance(value, list):

            own = [s for s in value if not isinstance(s, dict) or not s.get("requirements") or related(s.get("requirements"), members)]
            sliced[key] = own
            if len(own) < len(value):
                sliced["seed_note"] = f"{len(value) - len(own)} more seed records belong to other requirements; they live in the seed routine, never remove them."
        else:
            sliced[key] = value
    text = json.dumps(sliced, ensure_ascii=False, indent=1)
    return text if len(text) < len(contract) else contract

def dedupe_alternatives(contract: str, requirement_text: str, log=print) -> str:
    from .detaudit import alternative_pairs, interface_alternative

    pairs = alternative_pairs(requirement_text)
    try:
        data = json.loads(contract)
    except json.JSONDecodeError:
        data = parse_contract(contract) if contract.rstrip().endswith("}") else None
        if data is None:
            return contract
    removed: list[str] = []
    for page in data.get("pages") or [] if isinstance(data, dict) else []:
        if not isinstance(page, dict):
            continue
        for key, field in (("links", "text"), ("controls", "label")):
            items = page.get(key)
            if not isinstance(items, list):
                continue
            names = {str(i.get(field) or "").strip() for i in items if isinstance(i, dict)}
            for first, second in pairs:
                if first in names and second in names:
                    keep, drop = interface_alternative(first, second, requirement_text)
                    page[key] = [i for i in page[key] if not (isinstance(i, dict) and str(i.get(field) or "").strip() == drop)]
                    removed.append(f"{page.get('route')}: `{drop}` (alternative of `{keep}`)")
    if not removed:
        return contract
    log(f"[architect] contract guard fixed {len(removed)} duplicate(s): {removed[:6]}")
    return json.dumps(data, ensure_ascii=False, indent=2)

def _guarded(text: str, requirement_text: str, log) -> str:
    try:
        return dedupe_alternatives(text, requirement_text, log=log)
    except Exception as exc:
        log(f"[architect] contract guard crashed ({exc!r}); keeping the contract as the architect wrote it")
        return text

def relevant_files(contract: str, members: list[str]) -> list[str]:
    try:
        data = json.loads(contract)
    except json.JSONDecodeError:
        data = parse_contract(contract)
        if data is None:
            return []
    files: list[str] = []
    if isinstance(data, dict):
        for page in data.get("pages") or []:
            if isinstance(page, dict) and page.get("file") and related(page.get("requirements"), members):
                files.append(str(page["file"]))
        for shared in data.get("shared") or []:
            if isinstance(shared, dict) and shared.get("file"):
                files.append(str(shared["file"]))
    import posixpath

    return list(dict.fromkeys(posixpath.normpath(f.replace("\\", "/").strip().lstrip("/")) for f in files if f.strip()))

def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    return raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

SEED_STEP = re.compile(r"Seed data:|evaluation seed contains|Seed values:|seeded data (?:is|includes)", re.IGNORECASE)

def _tree_digest(tree: dict[str, Any], limit: int = 20000) -> str:

    def render(steps: str, cap: int | None = None, step_cap: int | None = None) -> str:
        lines: list[str] = []
        seen: set[str] = set()

        def walk(node: dict[str, Any], depth: int) -> None:
            node_id = str(node.get("id") or "")
            name = str(node.get("name") or "")
            desc = str(node.get("description") or "").strip()
            lines.append(f"{'  ' * depth}- {node_id}: {name}")
            if desc:
                lines.append(f"{'  ' * depth}  {desc[:cap]}")
            for scenario in node.get("scenarios") or []:
                if steps == "none":
                    continue
                lines.append(f"{'  ' * depth}  scenario: {scenario.get('name', '')}")
                for step in scenario.get("steps") or []:

                    content = str(step.get("content", "")).strip()
                    seedy = bool(SEED_STEP.search(content))
                    if (steps == "distinct" or (steps == "seeds" and seedy)) and content in seen:
                        continue
                    seen.add(content)
                    lines.append(f"{'  ' * depth}    {step.get('keyword', '')}: {content[:step_cap]}")
            for child in node.get("children") or []:
                if isinstance(child, dict):
                    walk(child, depth + 1)

        walk(tree, 0)
        return "\n".join(lines)

    for steps, cap, step_cap in (("seeds", None, None), ("distinct", None, None), ("distinct", 2500, 400), ("none", 2500, None)):
        text = render(steps, cap, step_cap)
        if len(text) <= limit:
            return text

    nodes = len(re.findall(r"^\s*- ", text, re.MULTILINE))
    text = render("none", cap=max(300, limit // max(1, nodes) - 60))
    return text if len(text) <= limit else text[:limit] + "\n... [tree truncated]"

def _leaf_count(node: dict[str, Any]) -> int:
    children = [c for c in node.get("children") or [] if isinstance(c, dict)]
    return 1 if not children else sum(_leaf_count(c) for c in children)

ROUTE_IN_TEXT = re.compile(r"[`'\"“‘](/[A-Za-z0-9_\-./:{}]*)[`'\"”’]")
PAGE_NAME = re.compile(r"\b[Tt]he ((?:[A-Za-z][\w'’-]* ){1,3})page\b")

PAGE_CLAUSE = {"is", "are", "was", "were", "be", "enters", "enter", "opens", "open", "allows", "shows", "sees", "any",
               "visits", "returns", "remains", "stays", "has", "with", "for", "from", "by"}
PAGE_STOP = {"a", "an", "the", "same", "new", "current", "following", "this", "that", "respective", "corresponding",
             "application", "resulting", "next", "previous", "its", "their", "whole", "entire", "first", "second",
             "target", "relevant", "user's", "users'", "own", "above", "below", "original", "old", "other", "one",
             "which", "whose", "and", "or", "of", "to", "on", "in"}
SOURCE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css")
FALLBACK_NOTE = ("FALLBACK contract (contract142): the architect's contract could not be read as JSON. `pages` and `shared` "
                 "are derived by script from the requirement wording and the template; the requirement text wins over "
                 "any route or file name here, and where `architect_draft` names a file for a page, that file wins.")

def _leaves(node: dict[str, Any]) -> list[dict[str, Any]]:
    children = [c for c in node.get("children") or [] if isinstance(c, dict)]
    return [node] if not children else [leaf for child in children for leaf in _leaves(child)]

def _leaf_text(leaf: dict[str, Any]) -> str:
    parts = [str(leaf.get("name") or ""), str(leaf.get("description") or "")]
    for scenario in leaf.get("scenarios") or []:
        if isinstance(scenario, dict):
            parts.append(str(scenario.get("name") or ""))
            parts += [str(step.get("content") or "") for step in scenario.get("steps") or [] if isinstance(step, dict)]
    return "\n".join(parts)

def _pascal(words: list[str]) -> str:
    return "".join(w[:1].upper() + w[1:].lower() for word in words for w in re.split(r"[^A-Za-z0-9]+", word) if w)

def _page_of_route(route: str) -> str:
    segments = [s for s in route.split("/") if s and not s.startswith((":", "{"))]
    return f"frontend/src/pages/{_pascal(segments) or 'Home'}Page.tsx"

def _page_of_name(name: str) -> str | None:
    raw_words = [re.sub(r"['’]s?$", "", w) for w in name.lower().split()]
    if any(w in PAGE_CLAUSE for w in raw_words):
        return None
    words = [w for w in raw_words if w and w not in PAGE_STOP]
    if not words or len(words) > 3:
        return None
    if words == ["home"]:
        return "frontend/src/pages/HomePage.tsx"
    return f"frontend/src/pages/{_pascal(words)}Page.tsx"

def fallback_pages(tree: dict[str, Any], project_dir: Path | None = None) -> list[dict[str, Any]]:
    pages: dict[str, dict[str, Any]] = {}

    def add(file: str, route: str | None, leaf_id: str) -> None:
        page = pages.setdefault(file, {"route": route, "file": file, "requirements": [],
                                       "purpose": "derived from the requirement wording (fallback)"})
        if route and not page.get("route"):
            page["route"] = route
        if leaf_id and leaf_id not in page["requirements"]:
            page["requirements"].append(leaf_id)

    for leaf in _leaves(tree):
        leaf_id = str(leaf.get("id") or "")
        text = _leaf_text(leaf)
        for match in ROUTE_IN_TEXT.finditer(text):
            route = match.group(1).rstrip("/.") or "/"
            if route.startswith(("/api", "//")) or len(route) > 80 or "." in route.rsplit("/", 1)[-1]:
                continue
            add(_page_of_route(route), route, leaf_id)
        for match in PAGE_NAME.finditer(text):
            file = _page_of_name(match.group(1))
            if file:
                add(file, "/" if file.endswith("/HomePage.tsx") else None, leaf_id)
    if project_dir is not None:
        base = project_dir / "frontend" / "src" / "pages"
        if base.is_dir():
            for path in sorted(base.glob("*.tsx")):
                rel = path.relative_to(project_dir).as_posix()
                if rel not in pages:
                    pages[rel] = {"route": "/" if path.stem == "HomePage" else None, "file": rel, "requirements": [],
                                  "purpose": "page of the template"}
    return [{k: v for k, v in page.items() if v is not None} for page in pages.values()]

def fallback_shared(project_dir: Path | None) -> list[dict[str, Any]]:
    if project_dir is None:
        return []
    try:
        from .codegen import TEMPLATE_PLUMBING
    except Exception:
        TEMPLATE_PLUMBING = ()
    shared: list[str] = []
    for root in ("frontend/src", "backend/src"):
        base = project_dir / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            rel = path.relative_to(project_dir).as_posix()
            if (path.is_file() and path.suffix in SOURCE_SUFFIXES and rel not in TEMPLATE_PLUMBING
                    and not rel.startswith("frontend/src/pages/")
                    and not any(part in ("node_modules", "tests", "test", "__tests__") for part in path.parts)):
                shared.append(rel)
    return [{"file": rel, "exports": []} for rel in shared[:40]]

def fallback_contract(tree: dict[str, Any], project_dir: Path | None, draft: str = "") -> dict[str, Any]:
    contract: dict[str, Any] = {"pages": fallback_pages(tree, project_dir), "api": [], "shared": fallback_shared(project_dir),
                                "notes": [FALLBACK_NOTE]}
    if draft.strip():
        contract["architect_draft"] = draft[:60000]
    return contract

def _complete(value: dict[str, Any], tree: dict[str, Any], project_dir: Path | None, log) -> str:
    added: list[str] = []
    if not isinstance(value.get("pages"), list) or not value.get("pages"):
        value["pages"] = fallback_pages(tree, project_dir)
        added.append(f"{len(value['pages'])} derived page(s)")
    if not isinstance(value.get("shared"), list):
        value["shared"] = fallback_shared(project_dir)
        added.append(f"{len(value['shared'])} template shared file(s)")
    if added:
        notes = value.get("notes") if isinstance(value.get("notes"), list) else []
        value["notes"] = notes + [FALLBACK_NOTE]
        log(f"[architect] FALLBACK: the recovered contract lacked parts; added {', '.join(added)} (derived by script)")
    return json.dumps(value, ensure_ascii=False, indent=2)

def recover_contract(raw: str, repaired: str, tree: dict[str, Any], project_dir: Path | None, log=print) -> str:
    best: tuple[int, str, dict[str, Any], list[str]] | None = None
    for source, text in (("architect answer", raw), ("repair answer", repaired)):
        if not text.strip():
            continue
        value, notes = tolerant_loads(text, CONTRACT_KEYS)
        if not isinstance(value, dict):
            continue
        size = sum(len(value.get(key) or []) for key in ("pages", "api") if isinstance(value.get(key), list))
        if best is None or size > best[0]:
            best = (size, source, value, notes)
    if best is not None and best[0] > 0:
        size, source, value, notes = best
        log(f"[architect] contract recovered by the tolerant parser from the {source}: {len(value.get('pages') or [])} "
            f"page(s), {len(value.get('api') or [])} endpoint(s), {len(value.get('shared') or [])} shared file(s); "
            f"repairs: {'; '.join(notes[:6])}")
        return _complete(value, tree, project_dir, log)
    contract = fallback_contract(tree, project_dir, draft=raw)
    log(f"[architect] FALLBACK contract in use: the architect's contract is unreadable; {len(contract['pages'])} page(s) "
        f"derived from the requirement leaves and {len(contract['shared'])} shared file(s) from the template keep the "
        "relevant-file selection alive (architect's text kept as `architect_draft`)")
    return json.dumps(contract, ensure_ascii=False, indent=2)

def build_contract(llm: LLM, tree: dict[str, Any], project_dir: Path, log=print, existing: str = "") -> str:
    large = _leaf_count(tree) > 12

    digest = _tree_digest(tree, limit=300000 if large else 20000)
    user = "Requirement tree:\n" + digest + "\n\nWrite the contract now."
    if large:

        user += LARGE_NOTE
    if existing:
        user += "\n\n" + existing
    message = llm.chat(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
        max_tokens=24000 if large else 16000,
        tag="architect:contract",
        thinking=bool(SETTINGS.get("architect_thinking", True)),
    )
    raw = _strip_fences(str(message.get("content") or ""))
    try:
        text = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    except json.JSONDecodeError as error:

        value, notes = tolerant_loads(raw, CONTRACT_KEYS)
        if isinstance(value, dict) and value.get("pages") and _lossless(notes):
            text = _complete(value, tree, project_dir, log)
            log(f"[architect] contract is not valid JSON ({error}); repaired by script without loss "
                f"({len(notes)} repair(s): {'; '.join(notes[:6])})")
        else:
            log(f"[architect] contract is not valid JSON ({error}); asking once for the same contract as valid JSON")
            text = raw
            repaired = ""
            try:
                fixed = llm.chat(
                    [{"role": "system", "content": "You repair JSON. Output the SAME document as valid JSON: same keys, same "
                      "values, only the syntax fixed. No commentary, no code fences."},
                     {"role": "user", "content": f"Parser error: {error}\n\nDocument:\n{raw}"}],
                    max_tokens=24000 if large else 16000,
                    tag="architect:contract-json",
                    thinking=False,
                )
                repaired = _strip_fences(str(fixed.get("content") or ""))
                text = json.dumps(json.loads(repaired), ensure_ascii=False, indent=2)
                log("[architect] contract repaired into valid JSON")
            except Exception as repair_error:
                log(f"[architect] WARNING: contract still not valid JSON after the repair call ({str(repair_error)[:120]})")
                text = recover_contract(raw, repaired, tree, project_dir, log)

    if large:

        text = _guarded(text, digest, log)
        write_text(project_dir / ".arc" / "design" / "contract.json", text)
        log(f"[architect] contract written ({len(text)} chars, large-tree skeleton, audit pass skipped)")
        return text
    try:
        review = llm.chat(
            [
                {"role": "system", "content": REVIEW_PROMPT},
                {"role": "user", "content": "Requirement tree:\n" + _tree_digest(tree) + "\n\nContract to audit:\n" + text},
            ],
            max_tokens=16000,
            tag="architect:audit",
            thinking=False,
        )
        reviewed = _strip_fences(str(review.get("content") or ""))
        parsed = json.loads(reviewed)
        corrections = parsed.pop("corrections", [])
        if isinstance(parsed, dict) and parsed.get("pages"):
            text = json.dumps(parsed, ensure_ascii=False, indent=2)
            log(f"[architect] audit applied {len(corrections)} correction(s): {json.dumps(corrections, ensure_ascii=False)[:600]}")
    except Exception as exc:
        log(f"[architect] audit skipped: {exc!r}")
    text = _guarded(text, digest, log)
    write_text(project_dir / ".arc" / "design" / "contract.json", text)
    log(f"[architect] contract written ({len(text)} chars)")
    return text
