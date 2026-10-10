from __future__ import annotations

import re
from typing import Any

MAX_PER_CHECK = 2

ISO = re.compile(r"(?<![\w-])\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?(?![\w:])")

FLOAT_NOISE = re.compile(r"(?<![\w.])-?\d+\.\d*?(?:0{6,}|9{6,})\d{1,3}(?![\w.])")
LEAKS = (
    ("[object Object]", re.compile(r"\[object Object\]")),
    ("Invalid Date", re.compile(r"\bInvalid Date\b")),
    ("NaN", re.compile(r"(?<![A-Za-z])NaN(?![A-Za-z])")),

    ("undefined", re.compile(r"^undefined$|[:=]\s*undefined\s*$")),
)

ISO_STATED = re.compile(r"\bISO[ -]?(?:8601|format|date|time|timestamp|string)|\b8601\b|toISOString|\bUTC\b|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")

def _pages(snapshot: list[dict[str, Any]] | None):
    for page in snapshot or []:
        if isinstance(page, dict) and isinstance(page.get("visual"), dict):
            yield page, page["visual"]

def _where(page: dict[str, Any]) -> str:
    via = f" (reached by clicking “{page.get('via')}” on /)" if page.get("via") else ""
    return f"route {page.get('route')}{via}"

def _around(context: str, value: str, before: int = 40) -> str:
    index = context.find(value)
    if index < 0:
        return value
    start = 0
    if index > before:
        space = context.find(" ", index - before)
        start = space + 1 if 0 <= space < index else index
    return context[start:index + len(value)].strip()

def _significant(number: str) -> int:
    return len(number.lstrip("-").replace(".", "").lstrip("0"))

def raw_value_findings(requirement_text: str, snapshot: list[dict[str, Any]] | None, log=print) -> list[dict[str, str]]:
    iso: list[tuple[str, str, str]] = []
    leaks: dict[str, list[tuple[str, str]]] = {}
    iso_stated = bool(ISO_STATED.search(requirement_text or ""))
    for page, visual in _pages(snapshot):
        for item in visual.get("texts") or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "")
            context = " ".join(str(item.get("context") or text).split())[:160]
            route = str(page.get("route"))
            for match in ISO.finditer(text):
                if item.get("mono") or iso_stated:
                    why = "monospace text" if item.get("mono") else "the requirement names ISO / UTC or shows such a timestamp"
                    log(f"[audit] (diagnostic, not reported) route {route}: raw ISO timestamp {match.group(0)!r} ({why})")
                else:
                    iso.append((route, match.group(0), _around(context, match.group(0))))
            for match in FLOAT_NOISE.finditer(text):

                value = match.group(0)
                why = "looks like binary float noise" if _significant(value) >= 15 else "long decimal"
                log(f"[audit] (diagnostic, not reported) route {route}: {value!r} {why}; its digits do not prove it was "
                    "computed rather than entered")
            for token, pattern in LEAKS:
                if pattern.search(text.strip()):
                    if token in (requirement_text or ""):
                        log(f"[audit] (diagnostic, not reported) route {route}: {token!r} shown, but the requirement uses it")
                    else:
                        leaks.setdefault(token, []).append((route, context))
    out: list[dict[str, str]] = []
    if iso:
        routes = list(dict.fromkeys(r for r, _, _ in iso))
        examples = list(dict.fromkeys(f"“{c}”" for _, _, c in iso))[:3]
        out.append({
            "where": "route " + ", ".join(routes[:4]),
            "requirement": "(dates and times are shown readable for a person)",
            "problem": (f"Raw machine timestamps are shown as visible text on {len(routes)} page(s), e.g. {', '.join(examples)}: "
                        "the ISO form with “T”, milliseconds and “Z” is what the API stores, not what a person reads."),
            "fix": ("Format the value where it is displayed, e.g. as local `YYYY-MM-DD HH:mm:ss` (keep the seconds so a changed "
                    "value still shows as changed) -- unless the requirement states another display format, then use that. "
                    "Keep the stored / API value and every label text around it as they are."),
        })
    for token, hits in leaks.items():
        routes = list(dict.fromkeys(r for r, _ in hits))
        examples = list(dict.fromkeys(f"“{c}”" for _, c in hits))[:3]
        out.append({
            "where": "route " + ", ".join(routes[:4]),
            "requirement": "(visible values are real values)",
            "problem": f"A missing or unconverted value is shown as the text “{token}”: {', '.join(examples)}.",
            "fix": (f"Find the value rendered as “{token}” and render the real value (convert the object / date / number "
                    "first), or leave the place empty when there is none; keep labels, roles and names as they are."),
        })
    return out

def popup_findings(snapshot: list[dict[str, Any]] | None, log=print) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    reported: set[str] = set()
    for page, visual in _pages(snapshot):
        for pop in visual.get("popups") or []:
            if not isinstance(pop, dict):
                continue
            trigger = pop.get("trigger") if isinstance(pop.get("trigger"), dict) else None
            label = pop.get("label") or ", ".join(f"“{x}”" for x in (pop.get("first") or [])[:3])
            head = f"route {page.get('route')}: visible {pop.get('role')} {label}"
            if not trigger:
                log(f"[audit] (diagnostic, not reported) {head}: no opener declares it (a list shown on purpose?)")
                continue
            expanded = trigger.get("expanded")
            haspopup = str(trigger.get("haspopup") or "")
            nearby = trigger.get("via") != "aria-controls"
            doubt = ""
            if not trigger.get("shown"):
                doubt = "its opener is not visible"
            elif pop.get("focused"):
                doubt = "focus is in it or on its opener"
            elif nearby:

                doubt = "only a nearby control, no aria-controls / aria-owns names it as the opener"
            elif expanded == "true":
                doubt = "its opener says it is expanded (open on purpose)"
            elif expanded is None and haspopup.lower() in ("", "false"):
                doubt = "its opener declares no popup"
            elif int(pop.get("onTop") or 0) == 0:
                doubt = "something else is painted over it"
            if doubt:
                log(f"[audit] (diagnostic, not reported) {head}: {doubt}")
                continue
            key = f"{page.get('route')}|{trigger.get('name')}"
            if key in reported or len(out) >= MAX_PER_CHECK:
                continue
            reported.add(key)
            state = 'aria-expanded="false"' if expanded == "false" else f'aria-haspopup="{haspopup}" and no aria-expanded'
            covers = [str(c) for c in pop.get("covers") or []][:3]
            if pop.get("overlay") and covers:
                effect = f" It floats over the page and hides {', '.join(f'“{c}”' for c in covers)}."
            elif not pop.get("overlay"):
                effect = " It sits in the page flow and pushes the content below it down."
            else:
                effect = ""
            out.append({
                "where": _where(page),
                "requirement": "(a menu or list opens only when its opener is used)",
                "problem": (f"The {pop.get('role')} {label} ({pop.get('items')} option(s)) is open as soon as the page loads, "
                            f"before anyone used its opener “{trigger.get('name')}”, whose state says closed ({state}).{effect} "
                            "Whoever clicks the opener to open it closes it instead."),
                "fix": ("Render it closed on load: show it only after its opener is activated (opener aria-expanded=\"true\" "
                        "while open, \"false\" and the list not rendered or hidden while closed), close it on Escape and on a "
                        "click outside. Only if the requirement states that it is shown on load: keep it and set "
                        "aria-expanded=\"true\" on the opener instead. Keep roles and names as they are."),
            })
    return out

def unmarked_findings(snapshot: list[dict[str, Any]] | None, log=print) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    reported: set[str] = set()
    for page, visual in _pages(snapshot):
        for item in visual.get("selected") or []:
            if not isinstance(item, dict):
                continue
            attr, value = str(item.get("attr") or ""), str(item.get("value") or "")
            head = f"route {page.get('route')}: {attr}={value!r} on “{item.get('text')}”"
            if item.get("mark") or item.get("indicator"):
                log(f"[audit] (diagnostic, not reported) {head}: marked by a symbol or a decoration beside it")
                continue
            if item.get("opaque"):

                log(f"[audit] (diagnostic, not reported) {head}: holds a canvas / video / frame whose drawing is not compared")
                continue
            if int(item.get("siblings") or 0) < 1 or not str(item.get("text") or "").strip():
                log(f"[audit] (diagnostic, not reported) {head}: no siblings / no text to name it by")
                continue

            key = f"{item.get('group')}|{attr}|{item.get('role')}|{item.get('tag')}"
            if key in reported:
                log(f"[audit] (diagnostic, not reported) {head}: same group already reported")
                continue
            if len(out) >= MAX_PER_CHECK + 1:
                continue
            reported.add(key)
            siblings = ", ".join(f"“{s}”" for s in (item.get("siblingTexts") or [])[:3])
            kind = item.get("role") or item.get("tag")
            out.append({
                "where": _where(page),
                "requirement": "(the selected / current item is visibly marked)",
                "problem": (f"The {kind} “{item.get('text')}” carries {attr}=\"{value}\", yet it looks exactly like its "
                            f"{item.get('siblings')} unmarked sibling(s) ({siblings}): same colours, weight, borders and "
                            "background. A person cannot see which one is selected / current."),
                "fix": (f"Add a visible style for the marked item only, keyed on the attribute the app already sets (e.g. "
                        f"`[{attr}=\"{value}\"] {{ font-weight: 600; border-bottom: 2px solid currentColor; }}` or a "
                        "background colour). Keep the attribute, roles, names and texts as they are."),
            })
    return out

def blank_findings(snapshot: list[dict[str, Any]] | None, log=print) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for page, visual in _pages(snapshot):
        if not visual.get("blank"):
            if visual.get("drawn"):

                log(f"[audit] (diagnostic, not reported) route {page.get('route')}: no text, but {visual.get('drawn')}")
            continue
        if page.get("crawled") or page.get("error"):

            log(f"[audit] (diagnostic, not reported) route {page.get('route')}: blank, left to the console check")
            continue
        if len(out) >= MAX_PER_CHECK:
            break
        out.append({
            "where": _where(page),
            "requirement": "(every page shows something)",
            "problem": (f"The page {page.get('route')} renders nothing at all: no text, no image, no control -- a person "
                        "sees a white page and has no way on."),
            "fix": ("Render this route: its page, or -- when the address names nothing that exists -- a visible “not found” "
                    "message with a link back to the start page. Keep every other route as it is."),
        })
    return out

def heading_findings(snapshot: list[dict[str, Any]] | None, log=print) -> list[dict[str, str]]:
    pages: list[tuple[str, str, float, int]] = []
    for page, visual in _pages(snapshot):
        heading = visual.get("heading") if isinstance(visual.get("heading"), dict) else None
        if not heading or not str(heading.get("text") or "").strip():
            continue
        ref = heading.get("ref") if isinstance(heading.get("ref"), dict) else None
        size, weight = float(heading.get("size") or 0), int(heading.get("weight") or 400)
        body = float((ref or {}).get("size") or heading.get("body") or 16)
        if not (0 < size <= 1.15 * body and weight < 600):
            continue
        head = f"route {page.get('route')}: h1 “{heading.get('text')}” is {size:g}px / {weight}"
        if not ref or heading.get("type") is None or heading.get("backdrop") is None:
            log(f"[audit] (diagnostic, not reported) {head}: colour / font / background were not measured")
            continue
        apart = []
        if heading.get("type") != ref.get("type"):
            apart.append(f"its colour / font / case / spacing ({heading.get('type')}) differs from the body text ({ref.get('type')})")
        if heading.get("backdrop") != ref.get("backdrop"):
            apart.append(f"it sits on another background ({heading.get('backdrop')} vs {ref.get('backdrop')})")
        if heading.get("edge"):
            apart.append("it has its own border / shadow")
        if apart:
            log(f"[audit] (diagnostic, not reported) {head}, but {'; '.join(apart)}")
            continue
        pages.append((str(page.get("route")), str(heading.get("text")), size, weight))
    if not pages:
        return []
    routes = list(dict.fromkeys(r for r, _, _, _ in pages))
    route, text, size, weight = pages[0]
    return [{
        "where": "route " + ", ".join(routes[:4]),
        "requirement": "(a page title looks like a title)",
        "problem": (f"The page heading (h1) is set like body text on {len(routes)} page(s): e.g. “{text[:60]}” on {route} is "
                    f"{size:g}px at weight {weight} in the same colour, font and background as the body text around it, so "
                    "nothing tells a person it is the page's title."),
        "fix": ("Style the h1 as a title (e.g. `font-size: 1.5rem; font-weight: 600`), the same on every page -- unless the "
                "requirement states how the title looks, then follow it; keep its text, level and every other element as they are."),
    }]

def diagnostics(snapshot: list[dict[str, Any]] | None, log=print) -> None:
    for page, visual in _pages(snapshot):
        for field in visual.get("fields") or []:
            if isinstance(field, dict):
                log(f"[audit] (diagnostic, not reported) route {page.get('route')}: field “{field.get('name')}” has no border, "
                    "shadow or own background")

def visual_findings(requirement_text: str, snapshot: list[dict[str, Any]], log=print) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for name, run in (
        ("raw values", lambda: raw_value_findings(requirement_text, snapshot, log=log)),
        ("popups open on load", lambda: popup_findings(snapshot, log=log)),
        ("unmarked selection", lambda: unmarked_findings(snapshot, log=log)),
        ("blank pages", lambda: blank_findings(snapshot, log=log)),
        ("unstyled page headings", lambda: heading_findings(snapshot, log=log)),
    ):
        try:
            found = run()
        except Exception as exc:
            log(f"[audit] visual check {name} crashed: {exc!r}")
            continue
        if found:
            log(f"[audit] visual check {name}: {len(found)} finding(s)")
        out.extend(found)
    try:
        diagnostics(snapshot, log=log)
    except Exception as exc:
        log(f"[audit] visual diagnostics crashed: {exc!r}")
    return out
