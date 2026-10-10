from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .commit_probe import COMMIT, THING_ROLES, commit_statements

BUDGET_MS = 10000
CRAWL_MS = 30000
CRAWL_PAGES = 40
TIMEOUT_S = 240
MAX_PER_PROBE = 2

QN = r"[\"“]([^\"”\n]{1,80})[\"”]"

def _qn(group: str) -> str:
    return r"[\"“](?P<" + group + r">[^\"”\n]{1,80})[\"”]"

def norm(text: str) -> str:
    return " ".join(str(text or "").split())

def chunks(text: str) -> list[str]:
    parts = re.split(r"(?=\s-\s+id:\s)", " " + norm(text))
    return [p.strip() for p in parts if p.strip()]

def description(chunk: str) -> str:
    start = chunk.find("description:")
    body = chunk if start < 0 else chunk[start + len("description:"):]
    index = body.find(" scenarios:")
    return body if index < 0 else body[:index]

YAML_KEYS = re.compile(r"(?:^|\s)(?:-\s+)?(?:id|name|type|dependencies|description|children|scenarios|steps|keyword|content):\s+(?:[|>]-?\s+)?")

def sentences(text: str) -> list[str]:
    out: list[str] = []
    for part in YAML_KEYS.split(text):
        out += [s.strip(" '") for s in re.split(r"(?<=[.!?])\s+(?=[A-Z“\"`'(])", part) if s.strip(" '")]
    return out

def name_pattern(name: str) -> str:
    parts = re.split(r"<[^<>]{1,40}>", name.strip())
    return "^" + ".+".join(re.sub(r"[.*+?^${}()|\[\]\\/]", lambda m: "\\" + m.group(0), p) for p in parts) + "$"

def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in {"the", "and", "any", "its", "for", "with"}}

_NEG = re.compile(r"\b(?:not|never|no\s+longer|without)\b", re.I)
OPENER_NAMED = re.compile(
    r"\b(?:button|link)\s+(?:named|labell?ed|called|with\s+the\s+accessible\s+name)(?:\s+exactly)?\s+" + _qn("opener")
    + r"(?P<gap>[^.“”\"]{0,40}?)\b(?P<verb>opens|reveals|expands|displays|shows)\b(?P<rest>[^.]{0,160})", re.I)
CLICK_GAP = re.compile(r"\b(?:click|activat|press|select|tap)", re.I)
OPENER_BEFORE = re.compile(_qn("opener") + r"\s+(?:(?:button|link)\s+)?(?:opens|reveals|expands)\b(?P<rest>[^.]{0,160})", re.I)
TARGETS: list[tuple[re.Pattern[str], str | None]] = [
    (re.compile(r"\b(?:textbox|text\s+box|search\s*box|field|input)\s+(?:named|labell?ed|called)(?:\s+exactly)?\s+" + _qn("name"), re.I), "textbox"),
    (re.compile(r"\b(?:dialog|modal)\s+(?:named|called|titled|labell?ed)\s+" + _qn("name"), re.I), "dialog"),
    (re.compile(_qn("name") + r"\s+(?P<noun>menuitem|menu\s+item|dialog|option|textbox)\b", re.I), None),
    (re.compile(r"\b(?:a|the)\s+(?:dropdown\s+)?menu\b", re.I), "menuitem"),
    (re.compile(r"\b(?:a|the)\s+listbox\b", re.I), "option"),
]
ESC_CLOSES = re.compile(r"\b(?:Esc|Escape)(?:\s+key)?\s+(?:also\s+|then\s+)?(?:closes|dismisses|hides|collapses)\b(?P<what>[^.;,]{0,40})", re.I)
OUTSIDE_CLOSES = re.compile(r"\bclick(?:s|ing)?\s+(?:anywhere\s+)?outside\b(?P<what>[^.;]{0,40}?)\b(?:closes|dismisses|hides|collapses)\b(?P<what2>[^.;,]{0,40})", re.I)
ENTER_SUBMITS = re.compile(r"\b(?:press(?:es|ing)?|hit(?:s|ting)?)\s+Enter\s+(?:in|inside|within|while\s+in)\s+(?P<what>[^.;]{0,50}?)\b(?:submits|saves|confirms|applies)\b", re.I)
SUBMIT_NAME = re.compile(r"^(?:save|submit|confirm|apply|ok|create|rename|update|done)\b", re.I)

def _target(rest: str) -> dict[str, str] | None:
    best: tuple[int, dict[str, str]] | None = None
    for pattern, role in TARGETS:
        match = pattern.search(rest)
        if not match:
            continue
        name = match.groupdict().get("name") or ""
        if role is None:
            noun = " ".join(match.group("noun").lower().split())
            role = {"menu item": "menuitem"}.get(noun, noun)
        found = {"role": role, "name": name.strip()}
        if best is None or match.start() < best[0]:
            best = (match.start(), found)
    return best[1] if best else None

def popover_statements(whole: str) -> list[dict[str, Any]]:
    found: dict[tuple[str, str, str], dict[str, Any]] = {}
    for chunk in chunks(whole):
        text = description(chunk)
        local: list[dict[str, Any]] = []
        for sentence in sentences(text):
            for pattern in (OPENER_NAMED, OPENER_BEFORE):
                for match in pattern.finditer(sentence):
                    gap = match.groupdict().get("gap") or ""
                    if _NEG.search(gap):
                        continue
                    if (match.groupdict().get("verb") or "").lower() in ("displays", "shows") and not CLICK_GAP.search(gap):
                        continue
                    target = _target(match.group("rest"))
                    if not target:
                        continue
                    opener = match.group("opener").strip()
                    key = (name_pattern(opener), target["role"], target["name"])
                    statement = found.get(key) or {
                        "opener": key[0], "opener_name": opener, "target": target, "sentence": sentence[:240],
                        "escape": False, "outside": False, "enter": False, "submit": "", "said": []}
                    found[key] = statement
                    if statement not in local:
                        local.append(statement)
        if not local:
            continue
        buttons = re.findall(QN + r"\s+button", text) + re.findall(r"button\s+(?:named\s+|labell?ed\s+)?" + QN, text)
        for kind, pattern in (("escape", ESC_CLOSES), ("outside", OUTSIDE_CLOSES), ("enter", ENTER_SUBMITS)):
            for sentence in sentences(text):
                match = pattern.search(sentence)
                if not match:
                    continue
                what = _words(" ".join(v for v in match.groupdict().values() if v))

                linked = [s for s in local if what & (_words(s["sentence"]) | {s["target"]["role"]})]
                for statement in linked:
                    if kind == "enter" and statement["target"]["role"] != "dialog":
                        continue
                    statement[kind] = True
                    statement["said"].append(sentence[:200])
                    if kind == "enter":
                        own = {statement["opener_name"].casefold(), statement["target"]["name"].casefold()}
                        statement["submit"] = next((b.strip() for b in buttons if SUBMIT_NAME.match(b.strip())
                                                    and b.strip().casefold() not in own), "")
    jobs = []
    for index, statement in enumerate(found.values()):
        if kind_enabled("P1"):
            jobs.append(dict(statement, id=f"p1-{index}"))
    return jobs

TAB_WORD = re.compile(r"\btabs?\b", re.I)
TAB_KEY = re.compile(r"\b(?:navigation\s+(?:entries|links)|are\s+(?:navigation\s+)?(?:links|tabs))\b", re.I)
TAB_LIST = re.compile(r"(?P<list>(?:[A-Z][\w-]*(?:\s[a-z][\w-]*)?,\s+){1,6}(?:and|or)\s+[A-Z][\w-]*(?:\s[a-z][\w-]*)?)\s+are\s+(?:navigation\s+)?(?:links|tabs)\b")

def tab_statements_all(whole: str) -> list[dict[str, Any]]:
    text = norm(whole)
    found: dict[frozenset[str], dict[str, Any]] = {}
    for sentence in sentences(text):
        if not TAB_WORD.search(sentence):
            continue
        key = TAB_KEY.search(sentence)
        names = re.findall(QN, sentence[: key.start()]) if key else []
        if len(names) < 2:
            listed = TAB_LIST.search(sentence)
            names = [n for n in re.split(r",\s+(?:and\s+|or\s+)?|\s+(?:and|or)\s+", listed.group("list"))] if listed else []
        names = [n.strip() for n in names if len(n.strip()) >= 2]
        if len(names) < 2:
            continue
        shows = [n for n in names if re.search(r"\b" + re.escape(n) + r"(?:\s+(?:tab|view|page))?\s+(?:displays|lists|shows|contains)\b", text)]
        if len(shows) < 2:
            continue
        found.setdefault(frozenset(names), {"names": list(dict.fromkeys(names)), "sentence": sentence[:240]})
    return [dict(s, id=f"p2-{i}") for i, s in enumerate(found.values())]

def tab_statements(whole: str) -> list[dict[str, Any]]:
    return tab_statements_all(whole) if kind_enabled("P2") else []

ROLE_WORDS = r"Read|Triage|Write|Maintain|Admin|Owner|Member|Viewer|Editor|Reader|Guest|Contributor|Moderator"
ONLY = re.compile(r"\bOnly\s+(?P<who>[^.;]{1,120}?)\s+(?:may|can)\s+(?P<act>[^.;]{1,200})")
EVERYONE = re.compile(r"\b(?:visitors?|anyone|everyone|any\s+user|all\s+users|public)\b", re.I)
CONTROL_NAMED = re.compile(
    r"\b(?P<noun>buttons?|links?|textbox(?:es)?|text\s+box(?:es)?|fields?|editor|box|checkbox(?:es)?|combo\s*box(?:es)?)\s+"
    r"(?:is\s+|are\s+)?(?:named|labell?ed|called|with\s+the\s+accessible\s+names?)(?:\s+exactly)?\s+"
    r"(?P<names>" + QN + r"(?:\s*,?\s*(?:and|or)?\s*" + QN + r")*)", re.I)
CONTROL_BEFORE = re.compile(QN + r"\s+(?P<noun>button|textbox|text\s+box|field|checkbox)\b", re.I)

CONTROL_DIRECT = re.compile(r"\b(?P<noun>button|textbox|text\s+box|checkbox)\s+" + QN, re.I)
ACT_STOP = {"enter", "empty", "issue", "detail", "pages", "their", "which", "other", "where", "there", "about", "these",
            "those", "using", "signed", "users", "unique", "named", "button", "buttons", "repository", "create", "value",
            "values", "through", "after", "before", "within", "exactly", "status", "organization"}
ACCOUNT_PW = [
    re.compile(r"signs?\s+in\s+as\s+`(?P<u>[^`]+)`\s+(?:with|using)\s+(?:password\s+)?`(?P<p>[^`]+)`", re.I),
    re.compile(r"account\s+`(?P<u>[^`]+)`[^.`]{0,80}?(?:`[^`]*`[^.`]{0,40}?){0,2}?password\s+`(?P<p>[^`]+)`", re.I),
]
PRIVATE_SEED = re.compile(r"\bprivate\s+(?P<kind>[a-z]+)\s+`(?P<name>[^`]+)`\s*,?\s+owner\s+`(?P<owner>[^`]+)`", re.I)
PRIVATE_RULE = re.compile(r"\bprivate\b[^.;]{0,100}?\b(?:inaccessible\s+to\s+visitors|allows?\s+access\s+only\s+to|cannot\s+(?:enter|view|open|access)|denies\s+access|requires?\s+authentication)", re.I)

def _stem(word: str) -> str:
    word = word.lower()
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("sses", "xes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word

def _stems(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z]{5,}", text.lower())}

_ACT_STOP = {_stem(w) for w in ACT_STOP}

READ_VERBS = {"search", "find", "filter", "view", "open", "browse", "sort", "show", "preview", "copy", "download",
              "export", "print", "read", "see", "list", "go", "back", "cancel", "clear", "refresh", "select", "choose"}
WRITE_VERBS = {"add", "create", "new", "remove", "delete", "edit", "rename", "update", "invite", "change", "save", "set",
               "assign", "unassign", "move", "archive", "transfer", "submit", "post", "merge", "close", "reopen",
               "approve", "reject", "apply", "publish", "upload", "import", "convert", "lock", "unlock", "pin", "grant",
               "revoke", "promote", "demote", "manage", "maintain", "modify", "kick", "ban", "restore", "reset"}
UMBRELLA_VERBS = {"maintain", "manage", "administer", "administrate", "modify", "change", "edit", "update", "control",
                  "configure"}

OPEN_TO_ALL = re.compile(r"\b(?:visitors?|anyone|everyone|everybody|any\s+(?:user|visitor|one)|all\s+(?:users|visitors)|"
                         r"(?:signed-out|anonymous)\s+(?:users?|visitors?)|the\s+public)\s+(?:may|can|is\s+allowed\s+to|are\s+allowed\s+to)\b", re.I)

def _act_verb_fits(name: str, act: str) -> bool:
    words = re.findall(r"[a-z]+", name.lower())
    verb = words[0] if words else ""
    if verb not in READ_VERBS | WRITE_VERBS:
        return True
    said = {_stem(w) for w in re.findall(r"[a-z]{2,}", act.lower())}
    if _stem(verb) in said:
        return True
    return verb in WRITE_VERBS and bool(said & UMBRELLA_VERBS)

def _role_of(noun: str) -> str | None:
    noun = " ".join(noun.lower().split())
    if noun.startswith("button"):
        return "button"
    if noun.startswith("checkbox"):
        return "checkbox"
    if noun.startswith("combo"):
        return "combobox"
    if noun.startswith("link"):
        return None
    return "textbox"

def _controls(text: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for match in CONTROL_NAMED.finditer(text):
        role = _role_of(match.group("noun"))
        if role:
            out += [{"role": role, "name": n.strip()} for n in re.findall(QN, match.group("names"))]
    for match in CONTROL_BEFORE.finditer(text):
        role = _role_of(match.group("noun"))
        if role:
            out.append({"role": role, "name": match.group(1).strip()})
    for match in CONTROL_DIRECT.finditer(text):
        role = _role_of(match.group("noun"))
        if role:
            out.append({"role": role, "name": match.group(2).strip()})
    return [dict(t) for t in dict.fromkeys(tuple(sorted(c.items())) for c in out)]

def accounts(whole: str) -> dict[str, str]:
    text = norm(whole)
    found: dict[str, str] = {}
    for pattern in ACCOUNT_PW:
        for match in pattern.finditer(text):
            found.setdefault(match.group("u").strip(), match.group("p"))
    return found

def account_roles(whole: str, user: str) -> set[str]:
    text = norm(whole)
    roles = set(re.findall(r"`" + re.escape(user) + r"`[^.;`]{0,60}?\b(" + ROLE_WORDS + r")\s+(?:permission|access|role)\b", text))
    roles |= set(re.findall(r"\b(" + ROLE_WORDS + r")\s+(?:permission|access|role)\b[^.;`]{0,20}?`" + re.escape(user) + r"`", text))
    return roles

GROUPISH = re.compile(r"\b(?:teams?|groups?|members?|membership|belongs?\s+to|join(?:s|ed)?)\b", re.I)
GROUP_NAMED = re.compile(r"\b(?:teams?|groups?|organi[sz]ations?|orgs?)\s+(?:named\s+|called\s+)?`(?P<a>[^`]+)`|`(?P<b>[^`]+)`\s+(?:teams?|groups?|organi[sz]ations?|orgs?)\b", re.I)
GRANT_VERB = r"(?:grant(?:s|ed)?|give(?:s|n)?|assign(?:s|ed)?)"
ROLE_IMPLIES = re.compile(r"\b(?P<r>" + ROLE_WORDS + r")s?\b[^.;]{0,40}?\b(?:acts?\s+(?:with|as)|inherits?|includes?|impl(?:y|ies)|counts?\s+as)\b"
                          r"[^.;]{0,30}?\b(?P<a>" + ROLE_WORDS + r")\b")

def _group_granted(text: str, group: str) -> bool | None:
    if account_roles(text, group):
        return True
    g = "`" + re.escape(group) + "`"
    said = re.search(g + r"[^.;`]{0,60}?\b(?:" + GRANT_VERB + r"|ha(?:s|ve)|holds?)\b[^.;`]{0,30}?\b(?:roles?|permissions?|access)\b", text, re.I)
    said = said or re.search(r"\b" + GRANT_VERB + r"\b[^.;`]{0,40}?" + g, text, re.I)
    return None if said else False

def other_grant_path(whole: str, user: str, allowed: list[str]) -> str:
    text = norm(whole)
    own = account_roles(whole, user)
    for match in ROLE_IMPLIES.finditer(text):
        if match.group("r") in own and match.group("a") in allowed:
            return f"stated: {match.group(0)[:80]}"
    for sentence in sentences(text):
        if "`" + user + "`" not in sentence or not GROUPISH.search(sentence):
            continue
        groups = [m.group("a") or m.group("b") for m in GROUP_NAMED.finditer(sentence)]
        groups = [g for g in groups if g != user]
        if not groups:
            return f"a membership that names no group: {sentence[:80]}"
        for group in groups:
            granted = _group_granted(text, group)
            if granted is None:
                return f"group `{group}` is granted something no role names"
            if granted and account_roles(text, group) & set(allowed):
                return f"group `{group}` holds {sorted(account_roles(text, group) & set(allowed))}"
    return ""

def role_statements(whole: str) -> list[dict[str, Any]]:
    if not kind_enabled("P3"):
        return []
    found: dict[tuple, dict[str, Any]] = {}
    known = accounts(whole)
    for chunk in chunks(whole):
        text = description(chunk)
        for sentence in sentences(text):
            match = ONLY.search(sentence)
            if not match or EVERYONE.search(match.group("who")):
                continue
            controls = _controls(sentence)
            controls += [{"role": "button", "name": n.strip()} for n in re.findall(r"\b(?:click|use|press|activate)\s+" + QN, sentence)]
            if not controls:
                words = {w for w in _stems(match.group("act")) if w not in _ACT_STOP}

                for other in sentences(text):
                    if other != sentence and words & _stems(other) and not OPEN_TO_ALL.search(other):
                        controls += [c for c in _controls(other) if words & _stems(c["name"])
                                     and _act_verb_fits(c["name"], match.group("act"))]
            controls = [dict(t) for t in dict.fromkeys(tuple(sorted(c.items())) for c in controls)]
            if not controls:
                continue
            allowed = sorted(set(re.findall(ROLE_WORDS, match.group("who"))))
            restricted, skipped = [], []
            if allowed:
                for user, password in known.items():
                    roles = account_roles(whole, user)
                    if roles and not roles & set(allowed):
                        why = other_grant_path(whole, user, allowed)
                        if why:
                            skipped.append({"user": user, "why": why})
                        else:
                            restricted.append({"user": user, "password": password, "roles": sorted(roles)})
            key = tuple(sorted((c["role"], c["name"]) for c in controls))
            statement = {"controls": controls[:4], "allowed": allowed or ["signed-in"], "who": match.group("who"),
                         "sentence": sentence[:240], "restricted": restricted[:2]}
            if skipped:
                statement["skipped"] = skipped[:4]
            found.setdefault(key, statement)
    return [dict(s, id=f"p3-{i}") for i, s in enumerate(found.values())]

def private_statements(whole: str) -> list[dict[str, str]]:
    if not kind_enabled("P3"):
        return []
    return [dict(s, id=f"p3b-{i}") for i, s in enumerate(_private_seeds(whole))][:1]

def _private_seeds(whole: str) -> list[dict[str, str]]:
    text = norm(whole)
    rule = PRIVATE_RULE.search(text)
    if not rule:
        return []
    known = accounts(whole)
    found: dict[tuple[str, str], dict[str, str]] = {}
    for match in PRIVATE_SEED.finditer(text):
        owner = match.group("owner").strip()
        if owner in known:
            found.setdefault((owner, match.group("name")), {
                "kind": match.group("kind").lower(), "name": match.group("name").strip(), "owner": owner,
                "password": known[owner], "sentence": rule.group(0)[:200]})
    return list(found.values())

def _pick_submit(controls: list[dict[str, str]]) -> str:
    buttons = [c["name"] for c in controls if c["role"] == "button"]
    for name in buttons:
        words = re.findall(r"[a-z]+", name.lower())
        if SUBMIT_NAME.match(name) or CREATE_SUBMIT.match(name) or (words and words[0] in WRITE_VERBS):
            return name
    return buttons[0] if buttons else ""

def _capture_account(whole: str, allowed: list[str], known: dict[str, str]) -> dict[str, Any] | None:
    for user, password in known.items():
        roles = account_roles(whole, user)
        if roles & set(allowed):
            return {"user": user, "password": password, "roles": sorted(roles)}
    return None

def server_role_statements(whole: str) -> list[dict[str, Any]]:
    if not p10_enabled():
        return []
    known = accounts(whole)
    if not known:
        return []
    found: dict[tuple, dict[str, Any]] = {}
    for chunk in chunks(whole):
        text = description(chunk)
        for sentence in sentences(text):
            match = ONLY.search(sentence)
            if not match or EVERYONE.search(match.group("who")):
                continue
            controls = _controls(sentence)
            controls += [{"role": "button", "name": n.strip()} for n in re.findall(r"\b(?:click|use|press|activate)\s+" + QN, sentence)]
            if not controls:
                words = {w for w in _stems(match.group("act")) if w not in _ACT_STOP}
                for other in sentences(text):
                    if other != sentence and words & _stems(other) and not OPEN_TO_ALL.search(other):
                        controls += [c for c in _controls(other) if words & _stems(c["name"])
                                     and _act_verb_fits(c["name"], match.group("act"))]
            controls = [dict(t) for t in dict.fromkeys(tuple(sorted(c.items())) for c in controls)]
            field = next((c["name"] for c in controls if c["role"] == "textbox"), "")
            submit = _pick_submit(controls)
            if not field or not submit:
                continue
            allowed = sorted(set(re.findall(ROLE_WORDS, match.group("who"))))
            if not allowed:
                continue
            capture = _capture_account(whole, allowed, known)
            if not capture:
                continue
            restricted, skipped = [], []
            for user, password in known.items():
                roles = account_roles(whole, user)
                if roles and not roles & set(allowed):
                    why = other_grant_path(whole, user, allowed)
                    (skipped if why else restricted).append(
                        {"user": user, "why": why} if why else {"user": user, "password": password, "roles": sorted(roles)})
            if not restricted:
                continue
            key = (field, submit, tuple(sorted(a["user"] for a in restricted)))
            statement = {"field": field, "submit": submit, "controls": controls[:4], "allowed": allowed,
                         "who": match.group("who"), "sentence": sentence[:240], "capture": capture,
                         "restricted": restricted[:2]}
            if skipped:
                statement["skipped"] = skipped[:4]
            found.setdefault(key, statement)
    return [dict(s, id=f"p10-{i}") for i, s in enumerate(found.values())][:2]

_ROW_FIELD = r"(?:number|title|name|id|subject)"
ROW_NUMBER = {"number", "id"}
ROW_TITLE = {"title", "name", "subject"}
CLICK_OPENS = re.compile(
    r"\bclick(?:s|ing)?\s+(?:on\s+)?(?:the|a|an|its|each|any)\s+(?P<alts>(?:[a-z]+\s+)?" + _ROW_FIELD
    + r"(?:\s+(?:or|and)\s+(?:(?:the|its|a)\s+)?(?:[a-z]+\s+)?" + _ROW_FIELD + r")+)\s+"
    r"(?:opens|navigates\s+to|leads\s+to|goes\s+to|takes\s+(?:the\s+)?[a-z]+\s+to)\s+(?P<target>[^.;]{3,80})", re.I)
ROW_LIST = re.compile(r"\b(?:rows?|lists?|items?|entries|results?|tables?)\b", re.I)
NOT_A_PAGE = re.compile(r"\b(?:dialog|modal|popover|popup|menu|panel|drawer|tooltip|preview)\b", re.I)

def p11_enabled() -> bool:
    return kind_enabled("P11")

ROW_GENERIC = {"the", "a", "an", "its", "each", "any", "that", "this", "their", "detail", "details", "page", "view",
               "record", "records", "screen", "corresponding", "respective", "own", "full", "single", "item", "items",
               "row", "rows", "entry", "entries", "or", "and", "same", "new", "s"}

def _row_stems(alts: str, target: str) -> list[str]:
    words = [m.group(1) for m in re.finditer(r"\b([a-z]+)\s+" + _ROW_FIELD + r"\b", alts, re.I)]
    words += re.findall(r"[a-z]+", re.split(r"\b(?:detail|details|page|view|screen)\b", target, maxsplit=1, flags=re.I)[0].lower())
    stems = []
    for word in words:
        word = word.lower()
        if word in ROW_GENERIC or len(word) < 3 or re.fullmatch(_ROW_FIELD, word):
            continue

        stems.append(_singular(word))
    return list(dict.fromkeys(stems))[:3]

def row_click_statements(whole: str) -> list[dict[str, Any]]:
    if not p11_enabled():
        return []
    found: dict[tuple[str, ...], dict[str, Any]] = {}
    for sentence in sentences(norm(whole)):
        match = CLICK_OPENS.search(sentence)
        if not match or not ROW_LIST.search(sentence) or NOT_A_PAGE.search(match.group("target")):
            continue
        fields = list(dict.fromkeys(f.lower() for f in re.findall(r"\b" + _ROW_FIELD + r"\b", match.group("alts"), re.I)))
        if not (set(fields) & ROW_NUMBER and set(fields) & ROW_TITLE):
            continue
        stems = _row_stems(match.group("alts"), match.group("target"))
        if not stems:
            continue
        found.setdefault(tuple(fields), {"fields": fields, "target": match.group("target").strip()[:80],
                                         "stems": stems, "sentence": sentence[:240]})
    return [dict(s, id=f"p11-{i}") for i, s in enumerate(found.values())][:1]

EDIT_OPENER = re.compile(r"^(?:edit|rename|change|modify)\b", re.I)
SAVE_WORD = re.compile(r"^(?:save|update|apply|done|confirm|rename)\b", re.I)
EDIT_OPENS = re.compile(_qn("opener") + r"\s+(?:button\s+)?(?:opens|reveals|shows|displays)\b(?P<rest>[^;]{0,220})", re.I)
FORM_FROM = re.compile(r"\b(?i:form)\s+(?i:opens|is\s+opened)\s+(?i:from)\s+(?i:the\s+)?[\"“]?(?P<opener>[A-Z][\w-]*(?:\s+[\w-]+){0,5}?)[\"”]?\s+(?i:button)\b")
FIELD_LABELED = re.compile(r"\b(?i:field|text\s*box|textbox)\s+(?i:labell?ed|named)\s+[\"“]?(?P<field>[A-Z][\w-]*(?:\s+[a-z][\w-]*){0,4})[\"”]?")
CREATE_BUTTON = re.compile(r"\b(?i:an?|the)\s+[\"“]?(?P<create>(?:Create|Add|Submit)(?:\s+[a-z]+){0,2})[\"”]?\s+(?i:button)\b")
EXISTING_USES = re.compile(r"\b(?i:an?\s+|the\s+)?(?i:existing)\s+(?P<thing>[a-z]+)\s+(?i:uses|shows|has|offers)\s+(?i:a\s+|the\s+)?[\"“]?(?P<save>(?:Save|Update|Apply)(?:\s+[a-z]+){0,2})[\"”]?")

def p12_enabled() -> bool:
    return kind_enabled("P12")

def _create_forms(whole: str) -> list[dict[str, Any]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for chunk in chunks(whole):
        text = description(chunk)
        controls = _controls(text)
        submits = [c["name"] for c in controls if c["role"] == "button" and CREATE_SUBMIT.match(c["name"]) and not CREDENTIAL.search(c["name"])]
        fields = [c["name"] for c in controls if c["role"] == "textbox" and UNIQUE_FIELD.match(c["name"]) and not CREDENTIAL.search(c["name"])]
        if not submits or not fields:
            continue
        field = sorted(fields, key=lambda f: (f.lower() != "title", f.lower() != "name"))[0]
        openers = [n.strip() for n in re.findall(QN, text) if n.strip() not in (field, submits[0]) and CREATE_OPENER.match(n.strip())]
        found.setdefault((submits[0], field), {"submit": submits[0], "field": field, "openers": list(dict.fromkeys(openers))[:3]})
    return sorted(found.values(), key=lambda s: s["field"].lower() != "title")[:2]

def _edit_account(whole: str, names: list[str], known: dict[str, str], node: str = "") -> tuple[dict[str, Any] | None, bool, str]:
    allowed: set[str] = set()
    local = set(sentences(norm(node))) if node else set()
    for sentence in sentences(norm(whole)):
        match = ONLY.search(sentence)
        if match and not EVERYONE.search(match.group("who")) and (sentence in local or any(n and n in sentence for n in names)):
            allowed |= set(re.findall(ROLE_WORDS, match.group("who")))
    if not known:
        return None, False, "no stated account with a password"
    if not allowed:
        user = next(iter(known))
        return {"user": user, "password": known[user]}, False, ""
    for user, password in known.items():
        if account_roles(whole, user) & allowed:
            return {"user": user, "password": password}, True, ""
    for user, password in known.items():
        roles = account_roles(whole, user)
        if not roles or other_grant_path(whole, user, sorted(allowed)):
            return {"user": user, "password": password}, False, ""
    return None, False, f"every stated account has a stated role outside {sorted(allowed)}"

def edit_statements(whole: str) -> list[dict[str, Any]]:
    if not p12_enabled():
        return []
    known = accounts(whole)
    if not known:
        return []
    jobs: list[dict[str, Any]] = []
    creates = _create_forms(whole)
    for sentence, node in ((s, description(c)) for c in chunks(whole) for s in sentences(description(c))):
        for piece in sentence.split(";"):
            for match in EDIT_OPENS.finditer(piece):
                opener = match.group("opener").strip()
                if not EDIT_OPENER.match(opener):
                    continue
                controls = _controls(match.group("rest"))
                boxes = [c["name"] for c in controls if c["role"] == "textbox" and not CREDENTIAL.search(c["name"])]
                saves = [c["name"] for c in controls if c["role"] == "button" and SAVE_WORD.match(c["name"])]
                if boxes and saves and creates:
                    jobs.append({"shape": "A", "opener": opener, "field": boxes[0], "save": saves[0], "creates": creates,
                                 "thing": "record", "sentence": piece.strip()[:240], "node": node})
    for chunk in chunks(whole):
        text = description(chunk)
        uses = EXISTING_USES.search(text)
        form = FORM_FROM.search(text)
        field = FIELD_LABELED.search(text)
        create = CREATE_BUTTON.search(text[form.end():] if form else "")
        if not (uses and form and field and create) or CREDENTIAL.search(field.group("field")):
            continue
        start = max(text.rfind(". ", 0, form.start()), 0)
        jobs.append({"shape": "B", "opener": "", "field": field.group("field").strip(), "save": uses.group("save").strip(),
                     "creates": [{"submit": create.group("create").strip(), "field": field.group("field").strip(),
                                  "openers": [form.group("opener").strip()]}],
                     "thing": uses.group("thing").lower(), "sentence": text[start: uses.end()].strip(" .")[:240],

                     "hints": list(dict.fromkeys(re.findall(r"\b[A-Z][a-z]{2,20}\b", text)))[:40], "node": text})
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for job in jobs:
        key = (job["opener"], job["field"], job["save"])
        if key in seen:
            continue
        seen.add(key)
        account, proven, why = _edit_account(whole, [job["opener"], job["save"]], known, job.pop("node", ""))
        if not account:
            continue
        out.append(dict(job, account=account, roleProven=proven))

    out.sort(key=lambda j: not UNIQUE_FIELD.match(j["field"]))
    picked = [next(j for j in out if j["shape"] == shape) for shape in ("A", "B") if any(j["shape"] == shape for j in out)]
    return [dict(j, id=f"p12-{i}") for i, j in enumerate(picked)]

VIEW_KIND = re.compile(r"\b(?:tabs?|navigation\s+(?:entries|links)|filters?)\b", re.I)
VIEW_EVIDENCE = re.compile(r"\b(?:navigation\s+(?:entries|links)|filter(?:s|ed|ing)?|refresh(?:es|ed|ing)?|reload(?:s|ed|ing)?)\b", re.I)
REFRESH = re.compile(r"\b(?:refresh(?:es|ed|ing)?|reload(?:s|ed|ing)?)\b", re.I)

_QLIST = r"[\"“][^\"”\n]{1,80}[\"”](?:\s*,\s*[\"“][^\"”\n]{1,80}[\"”])*,?\s+(?:and|or)\s+[\"“][^\"”\n]{1,80}[\"”]"
VIEW_LIST = re.compile(r"\b(?:tabs?|filters?)\s+(?:named\s+|called\s+|labell?ed\s+)?(?P<a>" + _QLIST + r")|(?P<b>" + _QLIST
                       + r")\s+(?:tabs|filters|navigation\s+(?:entries|links))\b", re.I)

def _view_names(sentence: str) -> list[str]:
    key = TAB_KEY.search(sentence)
    names = re.findall(QN, sentence[: key.start()]) if key else []
    if len(names) < 2:
        listed = VIEW_LIST.search(sentence)
        names = re.findall(QN, listed.group("a") or listed.group("b")) if listed else []
    if len(names) < 2:
        listed = TAB_LIST.search(sentence)
        names = [n for n in re.split(r",\s+(?:and\s+|or\s+)?|\s+(?:and|or)\s+", listed.group("list"))] if listed else []
    return list(dict.fromkeys(n.strip() for n in names if len(n.strip()) >= 2))

def view_statements(whole: str, switch: bool = True) -> list[dict[str, Any]]:
    text = norm(whole)
    found: dict[frozenset[str], dict[str, Any]] = {}
    for chunk in chunks(text):
        body = description(chunk)
        for sentence in sentences(body):
            if not VIEW_KIND.search(sentence):
                continue
            names = _view_names(sentence)
            if len(names) < 2 or len(names) > 8:
                continue
            shows = any(re.search(r"\b" + re.escape(n) + r"(?:\s+(?:tab|view|page|filter))?\s+(?:displays|lists|shows|contains)\b", text)
                        for n in names)
            if not (shows or VIEW_EVIDENCE.search(sentence) or VIEW_EVIDENCE.search(body)):
                continue
            refresh = next((s for s in sentences(body) if REFRESH.search(s)), "")
            found.setdefault(frozenset(names), {"names": names, "sentence": sentence[:240], "refresh": refresh[:240]})
    jobs = [dict(s, id=f"p13-{i}") for i, s in enumerate(found.values())]
    return jobs if (not switch or kind_enabled("P13")) else []

def tab_entries(whole: str) -> list[str]:
    if not kind_enabled("TABCRAWL"):
        return []
    names: list[str] = []
    for job in tab_statements_all(whole) + view_statements(whole, switch=False):
        names += [n for n in job["names"] if n not in names]
    return names[:16]

LABEL_SHOWN = re.compile(r"\b(?:displays?|shows?)\s+(?:the\s+same\s+)?[\"“](?P<label>[A-Z][^\"”:<]{2,40}):\s*<[^>\"”]{1,40}>[\"”]")
CHANGING_LABEL = re.compile(r"updated|modified|changed|edited|saved", re.I)
ENTER_COMMITS = re.compile(r"\bpress(?:es|ing)?\s+Enter\b[^.;]{0,60}?\bcommits\b", re.I)

def _commit_things(whole: str) -> list[tuple[str, list[str], str]]:
    text = norm(whole)
    out = []
    for match in COMMIT.finditer(text):
        words = match.group("thing").lower().split()
        thing = " ".join(words[-2:]) if " ".join(words[-2:]) in THING_ROLES else words[-1]
        if THING_ROLES.get(thing):
            out.append((thing, THING_ROLES[thing], match.group(0)))
    return out

def updated_label_statements(whole: str) -> list[dict[str, Any]]:
    if not kind_enabled("P4"):
        return []
    text = norm(whole)
    enter = ENTER_COMMITS.search(text)
    things = _commit_things(text)
    if not enter or not things:
        return []
    labels = [m for m in LABEL_SHOWN.finditer(text) if CHANGING_LABEL.search(m.group("label"))]
    if not labels:
        return []
    start = max(text.rfind(". ", 0, labels[0].start()), 0)
    return [{"id": "p4-0", "label": labels[0].group("label").strip(), "roles": things[0][1],
             "sentence": text[start: labels[0].end() + 1].strip(". ")[:240], "enter_sentence": enter.group(0)[:200]}]

FIELD_ALT = re.compile(r"\b(?:in|through|via)\s+(?:the\s+)?(?:[a-z]+\s+){0,2}?(?:grid|table|list|cells?)\s+or\s+(?:the\s+)?(?P<field>[a-z]+\s+(?:bar|field|box))\b", re.I)

def field_statements(whole: str) -> list[dict[str, Any]]:
    if not kind_enabled("P5"):
        return []
    text = norm(whole)
    for chunk in chunks(text):
        body = description(chunk)
        commit = COMMIT.search(body)
        alt = FIELD_ALT.search(body)
        if not commit or not alt:
            continue
        things = _commit_things(body)
        if not things:
            continue
        field = alt.group("field").strip()
        exact = next((m.group(1) for m in re.finditer(r"text\s?box(?:es)?\s+(?:labell?ed|named)\s+" + QN, text)
                      if m.group(1).strip().casefold() == field.casefold()), "")

        bound = next((s for s in commit_statements(body) if s["thing"] == things[0][0]), {})
        return [{"id": "p5-0", "field": exact.strip(), "field_re": "" if exact else "^" + re.escape(field) + "$",
                 "roles": things[0][1], "thing": things[0][0], "escape": bool(bound.get("escape")),
                 "sentence": (alt.group(0) + " ... " + commit.group(0))[:240],
                 "escape_sentence": str(bound.get("escape_sentence") or "")[:160] if bound.get("escape") else ""}]
    return []

OWNED = re.compile(r"\blists?\s+(?:the\s+|all\s+)?(?P<noun>[a-z]+)\s+owned\s+by\s+(?:the\s+|an?\s+)?(?P<scope>[a-z]+)", re.I)

def _singular(noun: str) -> str:
    noun = noun.lower()
    return noun[:-3] + "y" if noun.endswith("ies") else noun[:-1] if noun.endswith("s") else noun

def scope_statements(whole: str) -> list[dict[str, str]]:
    if not kind_enabled("P6"):
        return []
    text = norm(whole)
    for match in OWNED.finditer(text):
        noun = _singular(match.group("noun"))
        opens = re.search(r"\bclick(?:s|ing)?\s+(?:a|the|each)\s+" + noun + r"\s+name\b[^.;]{0,30}?\bto\s+open\b", text, re.I) or \
            re.search(r"\bEach\s+(?:result|record|item|entry|row)\s+has\s+a\s+link\s+named\s+(?:exactly\s+)?(?:with\s+)?the\s+" + noun + r"\s+name\b", text, re.I)
        if opens:
            return [{"id": "p6-0", "noun": noun, "scope": match.group("scope").lower(),
                     "sentence": (match.group(0) + " ... " + opens.group(0))[:240]}]
    return []

def listed_private_statements(whole: str) -> list[dict[str, str]]:
    if not kind_enabled("P7"):
        return []
    return [dict(s, id=f"p7-{i}") for i, s in enumerate(_private_seeds(whole))][:1]

CREATE_SUBMIT = re.compile(r"^(?:create|submit|add|new|post|save\s+new)\b", re.I)
UNIQUE_FIELD = re.compile(r"^(?:title|name|[a-z]+(?:\s[a-z]+)?\s(?:name|title))$", re.I)
CREDENTIAL = re.compile(r"password|user\s*name|e-?mail|account|sign|log\s*in|token|key", re.I)
CREATE_OPENER = re.compile(r"^(?:new|create|add)\b", re.I)

def create_statements(whole: str) -> list[dict[str, Any]]:
    if not kind_enabled("P8") or not accounts(whole):
        return []
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for chunk in chunks(whole):
        text = description(chunk)
        controls = _controls(text)
        submits = [c["name"] for c in controls if c["role"] == "button" and CREATE_SUBMIT.match(c["name"]) and not CREDENTIAL.search(c["name"])]
        fields = [c["name"] for c in controls if c["role"] == "textbox" and UNIQUE_FIELD.match(c["name"]) and not CREDENTIAL.search(c["name"])]
        if not submits or not fields:
            continue
        field = sorted(fields, key=lambda f: (f.lower() != "title", f.lower() != "name"))[0]
        submit = submits[0]
        openers = [n.strip() for n in re.findall(QN, text) if n.strip() not in (field, submit) and CREATE_OPENER.match(n.strip())]
        sentence = next((s for s in sentences(text) if submit in s and field in s), "") or \
            next((s for s in sentences(text) if submit in s), text[:240])
        found.setdefault((submit, field), {"submit": submit, "field": field, "openers": list(dict.fromkeys(openers))[:3],
                                           "sentence": sentence[:240]})
    ranked = sorted(found.values(), key=lambda s: s["field"].lower() != "title")
    return [dict(s, id=f"p8-{i}") for i, s in enumerate(ranked)][:2]

DIRECT_OPEN = re.compile(r"\bdirect(?:ly)?\s+(?:links?|address(?:es)?|URLs?|navigation)\b"
                         r"|\bdirectly\s+(?:opens?|openable|visits?|visiting)\s+(?:the\s+|a\s+|an\s+)?(?:[a-z]+\s+){0,3}?(?:address|URL|link|pages?)\b", re.I)
DIRECT_ANY = re.compile(r"\bdirect(?:ly)?\s+(?:accessible|accessing|access)\b", re.I)

def notfound_statements(whole: str) -> list[dict[str, str]]:
    if not kind_enabled("P9"):
        return []
    said = sentences(norm(whole))
    for pattern in (DIRECT_OPEN, DIRECT_ANY):
        sentence = next((s for s in said if pattern.search(s) and not re.search(r"\bdenied|refused|private\b", s, re.I)), "")
        if sentence:
            return [{"id": "p9-0", "sentence": sentence[:240]}]
    return []

def enabled() -> bool:
    return os.environ.get("ARC_IPROBE", "1").strip().lower() not in ("0", "false", "off")

def kind_enabled(kind: str) -> bool:
    return enabled() and os.environ.get(f"ARC_IPROBE_{kind}", "1").strip().lower() not in ("0", "false", "off")

def p10_enabled() -> bool:
    return enabled() and os.environ.get("ARC_IPROBE_P10", "0").strip().lower() in ("1", "true", "on")

def build_jobs(requirement_text: str) -> dict[str, Any]:
    whole = str(requirement_text or "")
    jobs: dict[str, Any] = {
        "p1": popover_statements(whole), "p2": tab_statements(whole), "p3": role_statements(whole),
        "p3b": private_statements(whole), "p4": updated_label_statements(whole), "p5": field_statements(whole),
        "p6": scope_statements(whole), "p7": listed_private_statements(whole), "p8": create_statements(whole),
        "p9": notfound_statements(whole), "p10": server_role_statements(whole),
        "p11": row_click_statements(whole), "p12": edit_statements(whole), "p13": view_statements(whole),
        "tabs": tab_entries(whole)}
    owner = None
    private = jobs["p3b"] or jobs["p7"]
    if private:
        owner = {"user": private[0]["owner"], "password": private[0]["password"]}

    elif (jobs["p6"] or jobs["p2"] or jobs["p3"] or jobs["p8"] or jobs["p9"] or jobs["p1"] or jobs["p4"] or jobs["p5"]
          or jobs["p10"] or jobs["p11"] or jobs["p12"] or jobs["p13"]):
        known = accounts(whole)
        if known:
            user = next(iter(known))
            owner = {"user": user, "password": known[user]}
    jobs["owner"] = owner
    return jobs

KINDS = ("p1", "p2", "p3", "p3b", "p4", "p5", "p6", "p7", "p8", "p9")

ALL_KINDS = KINDS + ("p10",)

PROBE_KINDS = ALL_KINDS + ("p11", "p12")

VIEW_KINDS = PROBE_KINDS + ("p13",)

def triggered(jobs: dict[str, Any]) -> bool:
    return any(jobs.get(k) for k in VIEW_KINDS)

PROBE_SCRIPT = r"""
const { chromium } = require('@playwright/test');
const fs = require('fs');
const [base, specFile] = process.argv.slice(2);
const spec = JSON.parse(fs.readFileSync(specFile, 'utf8'));
const BUDGET = Number(spec.budgetMs || 10000);
const VIEW = { width: 1280, height: 800 };
const ORIGIN = new URL(base).origin;
const RISKY = /delete|remove|trash|archive|discard|destroy|reset|leave|unsubscribe|(log|sign)[-_ ]?(out|off)/i;
const DENIAL = /access denied|permission denied|not authori[sz]ed|unauthori[sz]ed|forbidden|not found|\b40[134]\b|(sign|log) ?in to (view|continue|access|see)|please (sign|log) ?in|must (sign|log) ?in|no access|(don't|do not) have (access|permission)|cannot (access|view)|can't (access|view)|requires? (authentication|signing in)/i;
const SIGN_IN = /^(sign in|log in|login|sign-in|log-in)$/i;
const SIGN_OUT = /^(sign out|log out|logout|sign-out|log-out)$/i;
const INTERACTIVE = 'button,a[href],input,select,textarea,label,summary,[role=button],[role=link],[role=checkbox],' +
  '[role=switch],[role=menuitem],[role=combobox],[contenteditable=true],[contenteditable=""]';
const EDITABLE = 'input,textarea,[contenteditable=true],[contenteditable=""],[contenteditable=plaintext-only]';
const out = { crawl: {}, p1: [], p2: [], p3: [], p3b: [], p4: [], p5: [], p6: [], p7: [], p8: [], p9: [], p10: [], p11: [], p12: [], p13: [] };
const token = (n) => 'zq' + String(Date.now() % 100000).padStart(5, '0') + 'p' + n;
const unq = (s) => { try { return JSON.parse('"' + s + '"'); } catch (e) { return s; } };
const strip = (s) => String(s || '').replace(/^"(.*)"$/, '$1').trim();

function parseAria(aria) {
  const nodes = [];
  const stack = [];
  for (const raw of String(aria || '').split('\n')) {
    const m = raw.match(/^(\s*)-\s+(.*)$/);
    if (!m) continue;
    const depth = m[1].length;
    let body = m[2];
    const q = body.match(/^'(.*)'\s*:?\s*$/);
    if (q) body = q[1].replace(/''/g, "'");
    while (stack.length && stack[stack.length - 1].depth >= depth) stack.pop();
    const parent = stack.length ? stack[stack.length - 1] : null;
    if (body.startsWith('/url:')) { if (parent) parent.url = strip(body.slice(5)); continue; }
    const r = body.match(/^([a-z]+)(?:\s+"((?:[^"\\]|\\.)*)")?((?:\s*\[[^\]]*\])*)\s*(?::\s*(.*))?$/);
    if (!r) continue;
    const node = { i: nodes.length, role: r[1], name: unq(r[2] || ''), attrs: r[3] || '', text: strip(r[4] || ''), depth,
      parent: parent ? parent.i : -1 };
    nodes.push(node);
    stack.push(node);
  }
  return nodes;
}
function ancestors(nodes, n) { const out = []; let p = n.parent; while (p >= 0) { out.push(nodes[p]); p = nodes[p].parent; } return out; }
function track(page) {
  page._inflight = 0;
  page.on('request', () => { page._inflight += 1; });
  page.on('requestfinished', () => { page._inflight = Math.max(0, page._inflight - 1); });
  page.on('requestfailed', () => { page._inflight = Math.max(0, page._inflight - 1); });
  return page;
}
const newPage = async (ctx) => track(await ctx.newPage());
async function settle(page, first) {
  await page.waitForTimeout(first === undefined ? 600 : first);
  const s = Date.now();
  while ((page._inflight || 0) > 0 && Date.now() - s < 3000) await page.waitForTimeout(100);
  await page.waitForTimeout(250);
}
// v144n: a crawled view shown only after a stated tab / navigation entry was activated has the path
// "<page>#arc-tab=<entry>": the marker never reaches the app -- goto opens the page and activates the entry again
const TAB_MARK = '#arc-tab=';
// a stated entry may carry a count in its name ("Open (1)", "Closed 3"; github v141-2): matched by its stated name
const baseName = (s) => String(s || '').replace(/\s+/g, ' ').trim().replace(/\s*\(?\d+\)?$/, '');
async function activateEntry(page, name) {
  for (const nameArg of [name, new RegExp('^\\s*' + escRe(name) + '\\s*\\(?\\d+\\)?\\s*$')]) {  // exact first
    for (const r of ['tab', 'link', 'button']) {
      const all = page.getByRole(r, typeof nameArg === 'string' ? { name: nameArg, exact: true } : { name: nameArg });
      const n = Math.min(await all.count().catch(() => 0), 3);
      for (let i = 0; i < n; i++) {  // the first match may be a hidden copy (a collapsed menu)
        const el = all.nth(i);
        if (await el.isVisible().catch(() => false)) { await el.click({ timeout: 2000 }).catch(() => {}); await settle(page, 300); return true; }
      }
    }
  }
  return false;
}
async function goto(page, path) {
  const at = String(path).indexOf(TAB_MARK);
  const entry = at >= 0 ? decodeURIComponent(String(path).slice(at + TAB_MARK.length)) : '';
  if (at >= 0) path = String(path).slice(0, at);
  try { await page.goto(new URL(path, base).href, { waitUntil: 'load', timeout: 8000 }); }
  catch (e) { if (!String(page.url()).startsWith(ORIGIN)) return false; }
  await settle(page, 150);
  if (entry) await activateEntry(page, entry);
  return true;
}
async function waitUntil(fn, ms) { const s = Date.now(); while (Date.now() - s < ms) { if (await fn()) return true; await new Promise((r) => setTimeout(r, 100)); } return !!(await fn()); }
const mainText = (page) => page.evaluate(() => ((document.querySelector('main') || document.body).innerText || '')).catch(() => '');
const ariaOf = (page) => page.locator('body').ariaSnapshot({ timeout: 5000 }).catch(() => '');
const pathOf = (page) => { const u = new URL(page.url()); return u.pathname + u.search; };

async function record(page) {
  const url = new URL(page.url());
  const nodes = parseAria(await ariaOf(page));
  const text = await mainText(page);
  const links = [];
  for (const n of nodes) {
    if (n.role !== 'link' || !n.url || n.url.startsWith('#') || n.url.includes('"')) continue;
    let u; try { u = new URL(n.url, url.href); } catch (e) { continue; }
    if (u.origin !== ORIGIN) continue;
    const anc = ancestors(nodes, n);
    const item = anc.find((a) => ['listitem', 'row', 'article'].includes(a.role));
    const inNav = anc.some((a) => a.role === 'navigation');
    const itemTexts = [];
    if (item && !inNav) {
      for (let j = item.i + 1; j < nodes.length && nodes[j].depth > item.depth; j++) {
        const t = nodes[j].name || nodes[j].text;
        if (t && nodes[j] !== n && t !== n.name) itemTexts.push(t);
      }
    }
    links.push({ name: n.name, path: u.pathname + u.search, inList: !!item && !inNav, itemTexts: itemTexts.slice(0, 8) });
  }
  return { path: url.pathname + url.search, nodes, text: text.slice(0, 8000), links };
}
const signedOut = (rec) => rec.nodes.some((n) => (n.role === 'link' || n.role === 'button') && SIGN_IN.test(n.name))
  && !rec.nodes.some((n) => SIGN_OUT.test(n.name));
function shape(path) {
  const segs = path.split('?')[0].split('/').filter(Boolean);
  if (!segs.length) return '/';
  const last = segs[segs.length - 1];
  const fixed = /^[a-z][a-z-]{0,23}$/.test(last);
  return segs.length + '|' + (fixed ? last : (segs[segs.length - 2] || '') + '/*');
}
async function expandMenus(page, rec) {
  const found = [];
  const menus = rec.nodes.filter((n) => n.role === 'button' && /menu|account|profile|avatar|user/i.test(n.name) && !RISKY.test(n.name)).slice(0, 2);
  for (const m of menus) {
    const b = page.getByRole('button', { name: m.name, exact: true }).first();
    if (!(await b.isVisible().catch(() => false))) continue;
    await b.click({ timeout: 2000 }).catch(() => {});
    await page.waitForTimeout(400);
    const after = await record(page);
    const known = new Set(rec.links.map((l) => l.path));
    for (const l of after.links) if (!known.has(l.path)) found.push(Object.assign({}, l, { inList: false }));
    await page.keyboard.press('Escape').catch(() => {});
    await goto(page, rec.path);
  }
  return found;
}
// P7: JSON the visitor's pages fetched that holds an object with a string value equal to a watched name
const apiHits = [];
function holders(data, name, depth) {
  // arrays (lists) of objects one of whose own string values is exactly the name
  const out = [];
  const walk = (v, d, inArray) => {
    if (d > 6 || out.length > 5 || v === null || typeof v !== 'object') return;
    if (Array.isArray(v)) { for (const x of v.slice(0, 500)) walk(x, d + 1, true); return; }
    if (inArray && Object.values(v).some((x) => typeof x === 'string' && x === name)) {
      const brief = {};
      for (const [k, x] of Object.entries(v)) if (typeof x !== 'object' && String(x).length < 60 && Object.keys(brief).length < 6) brief[k] = x;
      out.push(brief);
      return;
    }
    for (const x of Object.values(v)) walk(x, d + 1, false);
  };
  walk(data, depth || 0, false);
  return out;
}
function watchJson(page, names) {
  if (!names || !names.length) return;
  page.on('response', async (resp) => {
    try {
      const req = resp.request();
      if (req.method() !== 'GET' || resp.status() < 200 || resp.status() >= 300) return;
      if (!/json/i.test(resp.headers()['content-type'] || '')) return;
      const u = new URL(resp.url());
      if (u.origin !== ORIGIN) return;
      let decoded = u.pathname + u.search; try { decoded = decodeURIComponent(decoded); } catch (e) {}
      const body = await resp.text();
      if (body.length > 2000000) return;
      for (const name of names) {
        if (decoded.toLowerCase().includes(name.toLowerCase()) || !body.includes(JSON.stringify(name))) continue;
        const items = holders(JSON.parse(body), name);
        if (items.length && apiHits.length < 12) apiHits.push({ name, api: u.pathname + u.search, page: pathOf(page), items: items.slice(0, 2) });
      }
    } catch (e) { /* a body that cannot be read is no evidence */ }
  });
}
// a record title whose kind the requirement leaves open is a <button> (conventions: `<li><h3><button>Book 5</button>`),
// so following <a href> alone never reaches those record pages (consistency check C8): per crawled page, the first
// button inside a heading of a list item / row / article (outside navigation, not a risky name) is clicked once; the
// address it navigates to is kept as a list link. A button that opens no other address adds nothing.
async function titleButtonLinks(page, rec) {
  const title = rec.nodes.find((x) => {
    if (x.role !== 'button' || !x.name || RISKY.test(x.name)) return false;
    const anc = ancestors(rec.nodes, x);
    return anc.some((a) => a.role === 'heading') && anc.some((a) => ['listitem', 'row', 'article'].includes(a.role))
      && !anc.some((a) => a.role === 'navigation');
  });
  if (!title) return [];
  const b = page.getByRole('button', { name: title.name, exact: true }).first();
  if (!(await b.isVisible().catch(() => false))) return [];
  const before = pathOf(page);
  await b.click({ timeout: 2000 }).catch(() => {});
  await settle(page, 300);
  const after = new URL(page.url());
  const found = [];
  if (after.origin === ORIGIN && after.pathname + after.search !== before) {
    found.push({ name: title.name, path: after.pathname + after.search, inList: true, itemTexts: [], viaButton: true });
  }
  await goto(page, rec.path);
  return found;
}
// v144n item 2: the crawl never clicked tabs, so a control shown only after one (an “Add member” behind a People tab)
// was on no crawled page. On a page that shows >= 2 of the stated tab / navigation entries (spec.tabs), the entries
// WITHOUT an address of their own (a tab, a button, a link to "#..." -- a link with an address is followed by the crawl
// or keeps its view in the address anyway) that are not the active one are activated one after another, once per set
// of entries (the same strip on several pages is done once) and once per page shape, <= 4 entries, and in all at most
// a quarter of the crawl's own budget (<= 8 s: live REQ-1, clicking every entry of every strip cost the crawl pages
// and two kept findings). An entry that keeps the pathname and shows new controls or new text is recorded as the view
// "<page>#arc-tab=<entry>" (goto re-activates the entry); one that opens another pathname is kept as a link of the page.
const CONTROL_ROLES_SEEN = /^(button|link|textbox|searchbox|combobox|checkbox|radio|switch|menuitem|tab|spinbutton|slider)$/;
const TABS_LOG = [];
async function tabViews(page, rec, t0, budget, state) {
  const names = spec.tabs || [];
  const stated = [];
  for (const nm of names) {
    const n = rec.nodes.find((x) => ['tab', 'link', 'button'].includes(x.role) && baseName(x.name) === nm);
    if (n) stated.push(n);
  }
  const res = { views: [], links: [], tried: false };
  if (stated.length < 2) return res;
  const strip = stated.map((n) => baseName(n.name)).sort().join('|');
  if (state.strips.has(strip)) return res;
  res.tried = true;
  state.strips.add(strip);
  const hrefOf = (n) => String(n.url || '').trim().replace(/^"(.*)"$/, '$1');  // the snapshot keeps quotes around "#x"
  const entries = stated.filter((n) => !RISKY.test(n.name) && !(n.role === 'link' && hrefOf(n) && !hrefOf(n).startsWith('#'))
    && !/\[(?:selected|current|pressed)[^\]]*\]/.test(n.attrs));  // the active entry: its view is the page itself
  TABS_LOG.push({ page: rec.path, stated: stated.map((n) => [n.role, n.name, n.url || '', n.attrs].join(' ')).slice(0, 6),
    clicks: entries.length });
  const ctlKey = (n) => n.role + '|' + n.name;
  const before = new Set(rec.nodes.filter((n) => CONTROL_ROLES_SEEN.test(n.role)).map(ctlKey));
  const own = new URL(rec.path, base);
  const cap = Math.min(8000, budget / 4);
  let moved = false;
  for (const n of entries.slice(0, 4)) {
    if (Date.now() - t0 > budget || state.spent > cap) break;
    const s0 = Date.now();
    try {
      if (!(await activateEntry(page, n.name))) continue;
      moved = true;
      const u = new URL(page.url());
      if (u.origin !== ORIGIN || u.pathname !== own.pathname) {
        if (u.origin === ORIGIN) res.links.push({ name: n.name, path: u.pathname + u.search, inList: false, itemTexts: [] });
        TABS_LOG.push({ page: rec.path, entry: n.name, opens: u.origin === ORIGIN ? u.pathname + u.search : 'elsewhere' });
        await goto(page, rec.path);
        moved = false;
        continue;
      }
      const v = await record(page);
      const fresh = [...new Set(v.nodes.filter((x) => CONTROL_ROLES_SEEN.test(x.role) && x.name && !before.has(ctlKey(x))).map(ctlKey))];
      TABS_LOG.push({ page: rec.path, entry: n.name, address: u.pathname + u.search + u.hash, newControls: fresh.length,
        sample: fresh.slice(0, 6), sameText: v.text === rec.text });
      if (fresh.length || v.text !== rec.text) {
        v.address = v.path;
        v.path = own.pathname + own.search + TAB_MARK + encodeURIComponent(baseName(n.name));
        v.tab = baseName(n.name);
        v.depth = rec.depth;
        res.views.push(v);
      }
    } finally { state.spent += Date.now() - s0; }
  }
  if (moved) await goto(page, rec.path);  // the crawl's next steps (title buttons) expect the page as loaded
  return res;
}
async function crawl(ctx, starts, maxPages, budget, maxDepth, expand, watch) {
  const page = await newPage(ctx);
  watchJson(page, watch);
  const seen = new Set();
  const shapes = {};
  const tabbed = new Set();
  const tabState = { strips: new Set(), spent: 0 };
  const pages = [];
  let loaded = 0;  // the views behind a tab do not count as pages
  const t0 = Date.now();
  const queue = starts.map((s) => (typeof s === 'string' ? { path: s, depth: 0 } : s));
  const queued = new Set(queue.map((q) => q.path.split('#')[0].split('?')[0]));
  while (queue.length && loaded < maxPages && Date.now() - t0 < budget) {
    const { path, depth } = queue.shift();
    const key = path.split('#')[0].split('?')[0];
    if (seen.has(key) || RISKY.test(key)) continue;
    const sh = shape(key);
    shapes[sh] = (shapes[sh] || 0) + 1;
    if (depth > 0 && shapes[sh] > (sh.endsWith('/*') ? 2 : 1)) continue;
    seen.add(key);
    if (!(await goto(page, path))) continue;
    const rec = await record(page);
    const finalKey = rec.path.split('?')[0];
    if (finalKey !== key && seen.has(finalKey)) continue;
    seen.add(finalKey);
    rec.depth = depth;
    if (expand && depth === 0) rec.links = rec.links.concat(await expandMenus(page, rec));
    if (depth < maxDepth && Date.now() - t0 < budget) rec.links = rec.links.concat(await titleButtonLinks(page, rec));
    let tabbedViews = [];
    if ((spec.tabs || []).length && !tabbed.has(sh) && Date.now() - t0 < budget) {
      const tv = await tabViews(page, rec, t0, budget, tabState);
      if (tv.tried) tabbed.add(sh);
      rec.links = rec.links.concat(tv.links);
      tabbedViews = tv.views;
    }
    pages.push(rec);
    loaded += 1;
    for (const v of tabbedViews) pages.push(v);
    if (depth >= maxDepth) continue;
    const lists = {};
    for (const l of rec.links.concat(...tabbedViews.map((v) => v.links))) {
      const k = l.path.split('#')[0].split('?')[0];
      if (queued.has(k) || RISKY.test(l.path) || RISKY.test(l.name)) continue;
      if (l.inList) { const dir = k.replace(/\/[^/]*$/, ''); lists[dir] = (lists[dir] || 0) + 1; if (lists[dir] > 2) continue; }
      queued.add(k);
      queue.push({ path: l.path, depth: depth + 1 });
    }
  }
  await page.close().catch(() => {});
  return pages;
}
async function signIn(browser, user, pass) {
  const context = await browser.newContext({ viewport: VIEW });
  const page = await newPage(context);
  const res = { context, page, ok: false, why: '' };
  await goto(page, '/');
  let entry = page.getByRole('link', { name: SIGN_IN }).first();
  if (!(await entry.isVisible().catch(() => false))) entry = page.getByRole('button', { name: SIGN_IN }).first();
  if (await entry.isVisible().catch(() => false)) { await entry.click({ timeout: 3000 }).catch(() => {}); await settle(page, 400); }
  const pw = page.locator('input[type=password]').first();
  if (!(await pw.isVisible().catch(() => false))) {
    for (const r of ['/login', '/signin', '/sign-in']) { await goto(page, r); if (await pw.isVisible().catch(() => false)) break; }
  }
  if (!(await pw.isVisible().catch(() => false))) { res.why = 'no password field'; return res; }
  let field = page.getByRole('textbox', { name: /user|email|login|account/i }).first();
  if (!(await field.isVisible().catch(() => false))) field = page.locator('input[type=text],input[type=email],input:not([type])').first();
  await field.fill(user).catch(() => {});
  await pw.fill(pass).catch(() => {});
  const submit = page.getByRole('button', { name: /^(sign in|log in|login|continue|submit)$/i }).first();
  if (await submit.isVisible().catch(() => false)) await submit.click({ timeout: 3000 }).catch(() => {}); else await pw.press('Enter').catch(() => {});
  await settle(page, 800);
  const stillPw = await page.locator('input[type=password]').first().isVisible().catch(() => false);
  const rec = await record(page);
  // signed in: no password field, and no sign-in entry -- or, when a page keeps a static "Sign in" link (github p141
  // home: an "Account access" navigation beside the account menu), the page names the account outside any text box
  // or offers a sign-out (v144i)
  const named = rec.nodes.some((n) => !['textbox', 'searchbox', 'combobox'].includes(n.role)
    && (n.name === user || n.text === user || SIGN_OUT.test(n.name)));
  res.ok = !stillPw && (named || !rec.nodes.some((n) => (n.role === 'link' || n.role === 'button') && SIGN_IN.test(n.name)));
  res.why = res.ok ? '' : (stillPw ? 'password field still shown' : 'a sign-in entry is still shown');
  return res;
}

// ---------------------------------------------------------------- P1
async function p1(ctx, job, pages) {
  const res = { id: job.id, route: null };
  const re = new RegExp(job.opener);
  let hit = null;
  let role = null;
  for (const p of pages) {
    const n = p.nodes.find((x) => (x.role === 'button' || x.role === 'link') && re.test(x.name));
    if (n) { hit = p; role = n.role; res.openerName = n.name; break; }
  }
  if (!hit) { res.skip = 'no crawled page shows the opener'; return res; }
  res.route = hit.path;
  const page = await newPage(ctx);
  const t0 = Date.now();
  try {
    await goto(page, hit.path);
    const opener = page.getByRole(role, { name: re }).first();
    if (!(await opener.isVisible().catch(() => false))) { res.skip = 'opener not visible'; return res; }
    const tgt = job.target;
    let loc;
    if (tgt.name) loc = page.getByRole(tgt.role, { name: tgt.name, exact: true });
    else if (tgt.role === 'dialog') loc = page.locator('[role=dialog],[role=alertdialog],dialog[open]');
    else loc = page.getByRole(tgt.role);
    const count = async () => { const n = Math.min(await loc.count().catch(() => 0), 20); let c = 0; for (let i = 0; i < n; i++) if (await loc.nth(i).isVisible().catch(() => false)) c++; return c; };
    const before = await count();
    // a named target already shown on load: the popover's own control, or a same-named control elsewhere (a header
    // “Search” beside a filter popover's own “Search”, audit r46 #1)? Activating the opener tells: when a new instance
    // appears, the one shown on load is not the popover's (then it is judged like an unnamed target: by the count)
    let twin = false;
    if (tgt.name && before > 0) {
      await opener.click({ timeout: 3000 }).catch(() => {});
      twin = await waitUntil(async () => (await count()) > before, 1500);
      res.afterOpener = await count();
      await goto(page, hit.path);
    }
    if (twin) res.twin = true;
    res.onLoad = before > 0 && !twin;
    const floor = tgt.name && !twin ? 0 : before;
    const isOpen = async () => (await count()) > floor;
    const open = async () => { if (await isOpen()) return true; await opener.click({ timeout: 3000 }).catch(() => {}); return waitUntil(isOpen, 1500); };
    if (!(tgt.name && res.onLoad)) {
      res.opened = await open();
      if (!res.opened) { res.skip = 'activating the opener showed nothing new'; return res; }
    }
    if (job.escape) {
      await page.keyboard.press('Escape');
      await page.waitForTimeout(500);
      res.escapeClosed = !(await isOpen());
      if (!res.escapeClosed) {
        const first = loc.first();
        const inner = tgt.role === 'textbox' ? first : first.locator('input,textarea,button,[role=menuitem],[role=option]').first();
        await inner.focus().catch(() => {});
        await page.keyboard.press('Escape');
        await page.waitForTimeout(500);
        res.escapeClosed = !(await isOpen());
        res.escapeInside = true;
      }
    }
    if (job.outside && Date.now() - t0 < BUDGET && (await open())) {
      const pt = await page.evaluate(() => {
        const sel = 'a,button,input,select,textarea,label,dialog,[role=button],[role=link],[role=dialog],[role=menu],[role=listbox],[role=option],[role=menuitem],[tabindex]';
        for (const [x, y] of [[10, innerHeight - 10], [innerWidth - 10, innerHeight - 10], [innerWidth / 2, innerHeight - 10], [10, innerHeight / 2]]) {
          const el = document.elementFromPoint(x, y);
          if (el && !el.closest(sel)) return { x, y };
        }
        return null;
      });
      if (pt) { await page.mouse.click(pt.x, pt.y); await page.waitForTimeout(500); res.outsideClosed = !(await isOpen()); }
    }
    if (job.enter && tgt.role === 'dialog' && Date.now() - t0 < BUDGET && (await open())) {
      const dlg = loc.first();
      const box = dlg.getByRole('textbox').first();
      const value = (await box.count()) ? await box.inputValue().catch(() => '') : '';
      if (!value.trim()) res.enterSkip = 'no prefilled text box in the dialog';
      else {
        // audit r46 #2: Enter's own request (1.6 s) finished while the button was being tried in the same dialog, and
        // the close was credited to the button. Now: the requests Enter started are waited for and counted (a write
        // means Enter did submit), and the button is tried in a fresh, equal state (reloaded, opened again, same value)
        const writes = [];
        const onReq = (r) => { if (!/^(GET|HEAD|OPTIONS)$/.test(r.method())) writes.push(r.method()); };
        page.on('request', onReq);
        await box.focus();
        await page.keyboard.press('Enter');
        await settle(page, 1200);
        page.off('request', onReq);
        res.enterWrites = writes.length;
        res.enterClosed = !(await isOpen());
        if (!res.enterClosed && !writes.length && job.submit && Date.now() - t0 < BUDGET) {
          await goto(page, hit.path);
          if (await open()) {
            const dlg2 = loc.first();
            const box2 = dlg2.getByRole('textbox').first();
            res.sameValue = ((await box2.count()) ? await box2.inputValue().catch(() => '') : '') === value;
            const b = dlg2.getByRole('button', { name: job.submit, exact: true }).first();
            if (res.sameValue && (await b.isVisible().catch(() => false))) {
              await b.click({ timeout: 3000 });
              await settle(page, 1500);
              res.buttonClosed = !(await isOpen());
            }
          }
        }
      }
    }
  } finally { await page.close().catch(() => {}); }
  return res;
}

// ---------------------------------------------------------------- P2
async function p2(ctx, job, pages) {
  const res = { id: job.id, route: null, views: [] };
  const score = (p) => job.names.filter((nm) => p.nodes.some((n) => ['link', 'tab', 'button'].includes(n.role) && n.name === nm)).length;
  const cands = pages.filter((p) => score(p) >= 2).sort((a, b) => score(b) - score(a));
  if (!cands.length) { res.skip = 'no crawled page shows two of the entries'; return res; }
  res.route = cands[0].path;
  const page = await newPage(ctx);
  const t0 = Date.now();
  const mark = () => page.evaluate((names) => {
    document.querySelectorAll('[data-arc-tabs]').forEach((e) => e.removeAttribute('data-arc-tabs'));
    const nameOf = (e) => (e.getAttribute('aria-label') || e.innerText || '').trim();
    const els = Array.from(document.querySelectorAll('a,button,[role=tab],[role=link],[role=button]')).filter((e) => names.includes(nameOf(e)));
    // the SMALLEST element holding the most entries: a page-wide wrapper that also holds a same-named link of
    // another navigation (github v129: “Commits” of the repository navigation) must not be taken for the strip
    let best = null;
    let bestN = 0;
    let bestSize = Infinity;
    for (const e of els) {
      let a = e.parentElement;
      for (let i = 0; a && i < 4; i++, a = a.parentElement) {
        const n = new Set(els.filter((x) => a.contains(x)).map(nameOf)).size;
        if (n >= 2) {
          const size = a.querySelectorAll('*').length;
          if (n > bestN || (n === bestN && size < bestSize)) { best = a; bestN = n; bestSize = size; }
          break;
        }
      }
    }
    if (best) best.setAttribute('data-arc-tabs', '1');
    return bestN;
  }, job.names);
  try {
    await goto(page, res.route);
    for (const nm of job.names) {
      if (Date.now() - t0 > BUDGET) { res.budget = true; break; }
      if ((await mark()) < 2) { res.skip = 'the entries are not grouped'; break; }
      let el = null;
      for (const r of ['tab', 'link', 'button']) {
        const l = page.locator('[data-arc-tabs]').getByRole(r, { name: nm, exact: true }).first();
        if (await l.isVisible().catch(() => false)) { el = l; break; }
      }
      if (!el) continue;
      const before = new URL(page.url());
      await el.click({ timeout: 3000 }).catch(() => {});
      await settle(page, 600);
      await mark();
      const info = await page.evaluate(() => {
        const root = document.querySelector('main') || document.body;
        let text = root.innerText || '';
        const strip = document.querySelector('[data-arc-tabs]');
        const outside = (e) => !(strip && strip.contains(e));
        if (strip && strip !== root && root.contains(strip)) text = text.replace(strip.innerText || '', ' ');
        const h = decodeURIComponent(location.hash.slice(1));
        const anchor = !!h && !!(document.getElementById(h) || document.getElementsByName(h).length);
        const shown = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; };
        // the headings shown outside the entries: what view the content says it is (audit r46 #3)
        const headings = Array.from(root.querySelectorAll('h1,h2,h3,h4,h5,h6,[role=heading]')).filter((e) => outside(e) && shown(e))
          .map((e) => (e.innerText || '').replace(/\s+/g, ' ').trim()).filter(Boolean).slice(0, 20);
        // the whole content, not its first 3000 characters, and its images / canvases / svgs (audit r46 #3)
        const media = Array.from(root.querySelectorAll('img,canvas,svg,video')).filter((e) => outside(e) && shown(e)).slice(0, 12).map((e) => {
          if (e.tagName === 'IMG' || e.tagName === 'VIDEO') return e.tagName + (e.currentSrc || e.src || '');
          if (e.tagName === 'CANVAS') { try { return 'C' + e.toDataURL(); } catch (x) { return 'C' + e.width + 'x' + e.height; } }
          return 'S' + e.outerHTML;
        }).join('|');
        const full = text.replace(/\s+/g, ' ').trim();
        let hash = 5381;
        const all = full + '\u0000' + media;
        for (let i = 0; i < all.length; i++) hash = ((hash * 33) ^ all.charCodeAt(i)) >>> 0;
        return { text: full, anchor, url: location.href, headings, sig: all.length + ':' + hash };
      });
      const after = new URL(info.url);
      res.views.push({ name: nm, hash: after.hash, samePage: before.pathname === after.pathname && before.search === after.search,
        anchor: info.anchor, text: info.text.slice(0, 3000), length: info.text.length, sig: info.sig, headings: info.headings });
    }
  } finally { await page.close().catch(() => {}); }
  return res;
}

// ---------------------------------------------------------------- P3
function controlSeen(rec, c) {
  return rec.nodes.find((n) => n.role === c.role && n.name === c.name && !/\bdisabled\b/.test(n.attrs));
}
async function p3(browser, job, vpages, owner) {
  const res = { id: job.id, visitor: [], accounts: [] };
  const where = {};
  for (const c of job.controls) {
    for (const p of vpages) {
      if (!signedOut(p) || !controlSeen(p, c)) continue;
      res.visitor.push({ route: p.path, role: c.role, name: c.name });
      where[c.name] = where[c.name] || p.path;
      break;
    }
    if (!where[c.name] && owner && owner.pages) {
      const p = owner.pages.find((x) => controlSeen(x, c));
      if (p) where[c.name] = p.path;
    }
  }
  const t0 = Date.now();
  for (const acc of job.restricted || []) {
    if (Date.now() - t0 > BUDGET) break;
    const routes = [...new Set(Object.values(where))];
    if (!routes.length) break;
    const s = await signIn(browser, acc.user, acc.password);
    const entry = { user: acc.user, roles: acc.roles, signedIn: s.ok, why: s.why, seen: [] };
    if (s.ok) {
      for (const r of routes) {
        await goto(s.page, r);
        const rec = await record(s.page);
        for (const c of job.controls) if (controlSeen(rec, c)) entry.seen.push({ route: r, role: c.role, name: c.name });
      }
    }
    await s.context.close().catch(() => {});
    res.accounts.push(entry);
  }
  return res;
}
async function p3b(browser, job, owner) {
  const res = { id: job.id, resource: job.name, subs: [] };
  if (!owner || !owner.ok) { res.skip = 'owner sign-in failed' + (owner ? ': ' + owner.why : ''); return res; }
  const op = owner.page;
  const t0 = Date.now();
  let root = null;
  for (const path of ['/', ...(owner.paths || []).slice(0, 5)]) {
    await goto(op, path);
    const nodes = parseAria(await ariaOf(op));
    const l = nodes.find((n) => n.role === 'link' && n.url && (n.name === job.name || n.name.endsWith('/' + job.name)));
    if (l) { root = new URL(l.url, op.url()).pathname; break; }
  }
  if (!root) { res.skip = 'the owner sees no link to ' + job.name; return res; }
  res.root = root;
  await goto(op, root);
  const oText = await mainText(op);
  res.ownerRoot = { denied: DENIAL.test(oText), hasName: oText.includes(job.name) };
  const onodes = parseAria(await ariaOf(op));
  const subs = [...new Set(onodes.filter((n) => n.role === 'link' && n.url).map((n) => {
    try { const u = new URL(n.url, op.url()); return u.origin === ORIGIN ? u.pathname : null; } catch (e) { return null; }
  }).filter((p) => p && p.startsWith(root + '/') && !RISKY.test(p)))].slice(0, 6);
  const vctx = await browser.newContext({ viewport: VIEW });
  const vp = await newPage(vctx);
  // audit r46 #5: a refusal worded otherwise ("Members only", "Request access") is no evidence of a leak; the evidence
  // is the owner's own content of that sub-page (its lines found on no other page of the record, nor on the visitor's
  // refused root) shown to the visitor as well
  const lines = (t) => [...new Set(String(t || '').split('\n').map((s) => s.replace(/\s+/g, ' ').trim())
    .filter((s) => s.length >= 2 && s.length <= 200 && !s.includes(job.name)))];
  const ownerLines = {};
  const visitorLines = {};
  try {
    await goto(vp, root);
    const vText = await mainText(vp);
    res.visitorRoot = { denied: DENIAL.test(vText) || /login|sign-?in/i.test(new URL(vp.url()).pathname), hasName: vText.includes(job.name), text: vText.trim().replace(/\s+/g, ' ').slice(0, 120) };
    const common = new Set([...lines(oText), ...lines(vText)]);
    for (const s of subs) {
      if (Date.now() - t0 > BUDGET) { res.budget = true; break; }
      await goto(op, s);
      const ot = await mainText(op);
      await goto(vp, s);
      const vt = await mainText(vp);
      const vpath = new URL(vp.url()).pathname;
      ownerLines[s] = lines(ot);
      visitorLines[s] = new Set(lines(vt));
      res.subs.push({ path: s, ownerOk: !DENIAL.test(ot) && ot.includes(job.name), visitorDenied: DENIAL.test(vt) || /login|sign-?in/i.test(vpath),
        visitorHasName: vt.includes(job.name), visitorText: vt.trim().replace(/\s+/g, ' ').slice(0, 160) });
    }
    for (const sub of res.subs) {
      const elsewhere = new Set(common);
      for (const [p, ls] of Object.entries(ownerLines)) if (p !== sub.path) ls.forEach((l) => elsewhere.add(l));
      const own = ownerLines[sub.path].filter((l) => !elsewhere.has(l));
      const shared = own.filter((l) => visitorLines[sub.path].has(l));
      Object.assign(sub, { ownLines: own.length, shared: shared.length, sharedSample: shared.slice(0, 4).map((l) => l.slice(0, 60)) });
    }
  } finally { await vctx.close().catch(() => {}); }
  return res;
}

// ---------------------------------------------------------------- cells (P4, P5): as agent/commit_probe.py
const scan = (page, role) => page.getByRole(role).evaluateAll((els, interactive) => els.map((el, index) => {
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  const visible = r.width >= 16 && r.height >= 10 && s.visibility !== 'hidden' && s.display !== 'none'
    && r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && r.right <= innerWidth;
  const x = r.left + r.width / 2;
  const y = r.top + r.height / 2;
  const hit = visible ? document.elementFromPoint(x, y) : null;
  const control = hit && hit.closest(interactive);
  const free = !!hit && el.contains(hit) && (!control || control === el || !el.contains(control));
  const values = Array.from(el.querySelectorAll('input,textarea')).map((i) => i.value).join(' ');
  return { index, name: (el.getAttribute('aria-label') || '').trim(), text: ((el.innerText || '') + ' ' + values).trim(), x, y,
    top: Math.round(r.top), visible, free };
}), INTERACTIVE).catch(() => []);
async function cellRole(page, roles) {
  for (const role of roles) if ((await scan(page, role)).filter((c) => c.visible && c.free).length >= 3) return role;
  return null;
}
function cellTools(page, role, all) {
  const names = all.map((c) => c.name);
  const key = (c) => ({ index: c.index, name: c.name && names.filter((n) => n === c.name).length === 1 ? c.name : '' });
  const find = async (k) => { const now = await scan(page, role); return (k.name && now.find((c) => c.name === k.name)) || now[k.index] || null; };
  const holds = async (k, v) => { const c = await find(k); return !!c && c.text.includes(v); };
  const label = (c) => c.name || ('#' + c.index);
  return { key, find, holds, label };
}
async function reloadCells(page, path, role) {
  await goto(page, path);
  for (let i = 0; i < 20 && (await scan(page, role)).filter((c) => c.visible && c.free).length < 3; i++) await page.waitForTimeout(150);
}
async function editCell(page, role, tools, mode, k, value) {
  const c = await tools.find(k);
  if (!c) return false;
  await page.evaluate((sel) => document.querySelectorAll(sel).forEach((e) => e.setAttribute('data-arc-pre', '1')), EDITABLE);
  if (mode === 'dblclick') await page.mouse.dblclick(c.x, c.y); else await page.mouse.click(c.x, c.y);
  await page.waitForTimeout(250);
  if (mode === 'dblclick') await page.keyboard.press('Control+A');
  await page.keyboard.type(value, { delay: 15 });
  await page.waitForTimeout(200);
  return page.getByRole(role).nth(c.index).evaluate((el, [v, sel]) => {
    const a = document.activeElement;
    const editor = a && a.matches && a.matches(sel) && (el.contains(a) || !a.hasAttribute('data-arc-pre'));
    const values = Array.from(el.querySelectorAll('input,textarea')).map((i) => i.value).join(' ');
    return ((el.innerText || '') + ' ' + values).includes(v) || (!!editor && ((a.value || '') + ' ' + (a.innerText || '')).includes(v));
  }, [value, EDITABLE]).catch(() => false);
}

// ---------------------------------------------------------------- P4
async function p4(ctx, job, pages) {
  const res = { id: job.id, route: null };
  const hit = pages.find((p) => p.text.includes(job.label + ':') && p.nodes.filter((n) => job.roles.includes(n.role)).length >= 3);
  if (!hit) { res.skip = 'no crawled page shows the label and the cells'; return res; }
  res.route = hit.path;
  const page = await newPage(ctx);
  const read = () => page.evaluate((label) => {
    const t = (document.querySelector('main') || document.body).innerText || '';
    const esc = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const m = t.match(new RegExp(esc + '\\s*:\\s*([^\\n]{1,60})'));
    return m ? m[1].trim() : null;
  }, job.label).catch(() => null);
  try {
    await goto(page, hit.path);
    const role = await cellRole(page, job.roles);
    if (!role) { res.skip = 'fewer than 3 free cells'; return res; }
    res.v0 = await read();
    if (!res.v0) { res.skip = 'label value not read'; return res; }
    const all = (await scan(page, role)).filter((c) => c.visible && c.free);
    const tools = cellTools(page, role, all);
    const empties = all.filter((c) => !c.text);
    if (empties.length < 2) { res.skip = 'no empty cell'; return res; }
    const a = empties[Math.floor(empties.length / 2)];
    const value = token(4);
    let typed = false;
    for (const mode of ['dblclick', 'type']) {
      typed = await editCell(page, role, tools, mode, tools.key(a), value);
      if (typed) { res.mode = mode; break; }
      await reloadCells(page, hit.path, role);
    }
    res.cell = tools.label(a);
    res.typed = typed;
    if (!typed) return res;
    await page.keyboard.press('Enter');
    await settle(page, 900);
    await page.waitForTimeout(600);
    res.v1 = await read();
    await reloadCells(page, hit.path, role);
    res.v2 = await read();
    res.persisted = await tools.holds(tools.key(a), value);
  } finally { await page.close().catch(() => {}); }
  return res;
}

// ---------------------------------------------------------------- P5
async function p5(ctx, job, pages) {
  const res = { id: job.id, route: null };
  const fieldRe = job.field ? null : new RegExp(job.field_re, 'i');
  const isField = (n) => n.role === 'textbox' && (job.field ? n.name === job.field : fieldRe.test(n.name));
  const hit = pages.find((p) => p.nodes.filter((n) => job.roles.includes(n.role)).length >= 3 && p.nodes.some(isField));
  if (!hit) { res.skip = 'no crawled page shows the field and the cells'; return res; }
  res.route = hit.path;
  const page = await newPage(ctx);
  const t0 = Date.now();
  try {
    await goto(page, hit.path);
    const role = await cellRole(page, job.roles);
    if (!role) { res.skip = 'fewer than 3 free cells'; return res; }
    const field = page.getByRole('textbox', job.field ? { name: job.field, exact: true } : { name: fieldRe }).first();
    const all = (await scan(page, role)).filter((c) => c.visible && c.free);
    const tools = cellTools(page, role, all);
    const empties = all.filter((c) => !c.text);
    const pool = empties.filter((c, i) => i % 3 === 1);
    if (pool.length < 3) { res.skip = 'too few empty cells'; return res; }
    const other = [...empties].reverse().find((c) => c.top !== pool[0].top && !pool.slice(0, 3).includes(c));
    if (!other) { res.skip = 'no other empty cell'; return res; }
    const select = async (c) => { const now = await tools.find(tools.key(c)); if (now) await page.mouse.click(now.x, now.y); await page.waitForTimeout(250); };
    const typeField = async (v) => {
      await field.click({ timeout: 3000 });
      await page.keyboard.press('Control+A');
      await page.keyboard.type(v, { delay: 15 });
      await page.waitForTimeout(200);
      const now = await field.inputValue().catch(async () => (await field.innerText().catch(() => '')));
      return String(now).includes(v);
    };
    const [a, d, e] = pool;
    const v1 = token(1);
    res.click = { cell: tools.label(a), other: tools.label(other) };
    await select(a);
    res.click.typed = await typeField(v1);
    if (res.click.typed) {
      const b = await tools.find(tools.key(other));
      if (b) {
        await page.mouse.click(b.x, b.y);
        await settle(page);
        res.click.afterClick = await tools.holds(tools.key(a), v1);
        await reloadCells(page, hit.path, role);
        res.click.persisted = await tools.holds(tools.key(a), v1);
      }
    } else await reloadCells(page, hit.path, role);
    if (res.click.persisted === false && Date.now() - t0 < BUDGET) {
      const v2 = token(2);
      await select(d);
      res.enter = { cell: tools.label(d), typed: await typeField(v2) };
      if (res.enter.typed) {
        await page.keyboard.press('Enter');
        await settle(page);
        await reloadCells(page, hit.path, role);
        res.enter.persisted = await tools.holds(tools.key(d), v2);
      }
    }
    if (job.escape && Date.now() - t0 < BUDGET) {
      await reloadCells(page, hit.path, role);
      const v3 = token(3);
      await select(e);
      res.escape = { cell: tools.label(e), typed: await typeField(v3) };
      if (res.escape.typed) {
        await page.keyboard.press('Escape');
        await page.waitForTimeout(400);
        await settle(page);
        await reloadCells(page, hit.path, role);
        res.escape.persisted = await tools.holds(tools.key(e), v3);
      }
    }
  } finally { await page.close().catch(() => {}); }
  return res;
}

// ---------------------------------------------------------------- P6
async function p6(ctx, job, pages) {
  const res = { id: job.id, items: [] };
  const byName = {};
  for (const p of pages) for (const l of p.links) if (l.name) (byName[l.name] = byName[l.name] || new Set()).add(l.path.split('#')[0]);
  const cands = [];
  const seen = new Set();
  for (const p of pages) for (const l of p.links) {
    const k = p.path + ' ' + l.path;
    // a link to the page itself with another query (a branch switch) opens no other record
    if (l.path.split('?')[0] === p.path.split('?')[0]) continue;
    if (!l.inList || l.name.length < 2 || !byName[l.name] || byName[l.name].size < 2 || seen.has(k)) continue;
    // same-name records in different scopes: another link of this name whose path has the same length and the same
    // last segment but another owner/scope segment (/acme-demo/acme-docs vs /alice-dev/acme-docs)
    const segs = (q) => q.split('?')[0].split('/').filter(Boolean);
    const mine = segs(l.path);
    // the path names the record by its name (/acme-demo/acme-docs for “acme-docs”); /issues/1 vs /pull/1 do not
    const lastName = decodeURIComponent(mine[mine.length - 1] || '').toLowerCase();
    if (!lastName || !(l.name.toLowerCase() === lastName || l.name.toLowerCase().endsWith('/' + lastName))) continue;
    const twins = [...byName[l.name]].filter((q) => { const s = segs(q); return s.length === mine.length && s.length >= 2
      && s[s.length - 1] === mine[mine.length - 1] && s.slice(0, -1).join('/') !== mine.slice(0, -1).join('/'); });
    if (!twins.length) continue;
    seen.add(k);
    const facts = (l.itemTexts || []).map((s) => String(s).trim()).filter((s) => s.length >= 12 && !/\d{4}-\d{2}-\d{2}/.test(s));
    cands.push({ route: p.path, name: l.name, path: l.path.split('#')[0], itemTexts: l.itemTexts, rich: facts.length, twins });
  }
  cands.sort((a, b) => b.rich - a.rich);  // items that show what record they are first
  const page = await newPage(ctx);
  const cache = {};
  const visit = async (path) => {
    if (cache[path]) return cache[path];
    await goto(page, path);
    const t = await mainText(page);
    return (cache[path] = { denied: DENIAL.test(t), text: t.slice(0, 6000), first: t.trim().replace(/\s+/g, ' ').slice(0, 100) });
  };
  const t0 = Date.now();
  try {
    for (const c of cands.slice(0, 8)) {
      if (Date.now() - t0 > BUDGET) { res.budget = true; break; }
      const facts = (c.itemTexts || []).map((s) => String(s).trim()).filter((s) => s.length >= 12 && !/\d{4}-\d{2}-\d{2}/.test(s));
      const target = await visit(c.path);
      const partners = [];
      for (const q of c.twins.slice(0, 3)) {
        const v = await visit(q);
        partners.push({ path: q, denied: v.denied, hasName: v.text.includes(c.name), facts: facts.filter((f) => v.text.includes(f)).length });
      }
      res.items.push({ route: c.route, name: c.name, path: c.path, facts, texts: (c.itemTexts || []).slice(0, 8), target: { denied: target.denied, first: target.first,
        facts: facts.filter((f) => target.text.includes(f)).length }, partners });
    }
  } finally { await page.close().catch(() => {}); }
  return res;
}

// ---------------------------------------------------------------- P7
const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const decodedPath = (p) => { try { return decodeURIComponent(p); } catch (e) { return p; } };
const keyOf = (p) => String(p || '').split('#')[0].split('?')[0];
async function p7(browser, job, vpages, owner) {
  const res = { id: job.id, resource: job.name, pages: [], apis: [], twins: [] };
  if (!owner || !owner.ok || !owner.pages) { res.skip = 'owner sign-in failed' + (owner ? ': ' + owner.why : ''); return res; }
  const lname = job.name.toLowerCase();
  const nameRe = new RegExp('(^|[^\\w-])' + escRe(job.name) + '($|[^\\w-])', 'i');
  // the record's own page: the owner's link named after it
  let root = null;
  for (const p of owner.pages) {
    const l = p.links.find((x) => x.name === job.name || x.name.endsWith('/' + job.name));
    if (l) { root = keyOf(l.path); break; }
  }
  if (!root) { res.skip = 'the owner sees no link to ' + job.name; return res; }
  res.root = root;
  const vctx = await browser.newContext({ viewport: VIEW });
  const vp = await newPage(vctx);
  try {
    await goto(owner.page, root);
    const oText = await mainText(owner.page);
    res.ownerRoot = { denied: DENIAL.test(oText), hasName: oText.includes(job.name) };
    await goto(vp, root);
    const vText = await mainText(vp);
    res.visitorRoot = { denied: DENIAL.test(vText) || /login|sign-?in/i.test(new URL(vp.url()).pathname),
      text: vText.trim().replace(/\s+/g, ' ').slice(0, 120) };
  } finally { await vctx.close().catch(() => {}); }
  const under = (k) => k === root || k.startsWith(root + '/');
  for (const p of vpages) {
    // another record of the same name the visitor may open (a public one elsewhere): its name is no leak
    for (const l of p.links) {
      const k = keyOf(l.path);
      if (!under(k) && decodedPath(k).toLowerCase().endsWith('/' + lname) && !res.twins.includes(k)) res.twins.push(k);
    }
  }
  for (const p of vpages) {
    const k = keyOf(p.path);
    // excluded: the refused page and its sub-pages, pages whose address carries the name (their text may echo it),
    // pages that refuse the visitor
    if (under(k) || decodedPath(p.path).toLowerCase().includes(lname) || !signedOut(p) || DENIAL.test(p.text)) continue;
    const links = p.links.filter((l) => under(keyOf(l.path))).map((l) => ({ name: l.name, path: keyOf(l.path) }));
    const at = p.text.search(nameRe);
    const named = p.nodes.filter((n) => nameRe.test(n.name)).map((n) => n.role + ' "' + n.name.slice(0, 60) + '"');
    if (!links.length && (res.twins.length || (at < 0 && !named.length))) continue;
    res.pages.push({ path: p.path, links: links.slice(0, 3), named: named.slice(0, 3),
      snippet: at < 0 ? '' : p.text.slice(Math.max(0, at - 60), at + job.name.length + 80).replace(/\s+/g, ' ').trim() });
  }
  if (!res.twins.length) res.apis = apiHits.filter((h) => h.name === job.name && !under(keyOf(h.page))
    && !decodedPath(h.page).toLowerCase().includes(lname)).slice(0, 3);
  return res;
}

// ---------------------------------------------------------------- P8
async function p8(owner, job) {
  const res = { id: job.id, submit: job.submit, field: job.field, lists: [], signedInAs: (spec.owner || {}).user || '' };
  if (!owner || !owner.ok || !owner.pages) { res.skip = 'no signed-in account' + (owner && owner.why ? ': ' + owner.why : ''); return res; }
  const page = await newPage(owner.context);
  const t0 = Date.now();
  const hasForm = (rec) => rec.nodes.some((n) => n.role === 'textbox' && n.name === job.field)
    && rec.nodes.some((n) => n.role === 'button' && n.name === job.submit && !/\bdisabled\b/.test(n.attrs));
  try {
    let formPath = null;
    let from = null;
    const hit = owner.pages.find(hasForm);
    if (hit) { await goto(page, hit.path); if (hasForm(await record(page))) formPath = hit.path; }
    outer: for (const name of formPath ? [] : job.openers || []) {
      for (const p of owner.pages) {
        const n = p.nodes.find((x) => (x.role === 'link' || x.role === 'button') && x.name === name);
        if (!n || RISKY.test(p.path)) continue;
        if (Date.now() - t0 > BUDGET) break outer;
        await goto(page, p.path);
        const el = page.getByRole(n.role, { name, exact: true }).first();
        if (!(await el.isVisible().catch(() => false))) continue;
        await el.click({ timeout: 3000 }).catch(() => {});
        await settle(page, 500);
        const rec = await record(page);
        if (hasForm(rec)) { formPath = rec.path; from = keyOf(p.path); }
        break outer;
      }
    }
    if (!formPath) { res.skip = 'no page shows the text box and the submit button'; return res; }
    res.route = formPath;
    res.from = from;
    const value = token(8);
    res.value = value;
    const writes = [];
    page.on('request', (r) => { if (/^(POST|PUT|PATCH)$/.test(r.method()) && String(r.postData() || '').includes(value)) writes.push(r); });
    const field = page.getByRole('textbox', { name: job.field, exact: true }).first();
    await field.fill(value, { timeout: 3000 });
    const button = page.getByRole('button', { name: job.submit, exact: true }).first();
    await button.dblclick({ timeout: 3000 });
    await settle(page, 1500);
    await waitUntil(async () => (page._inflight || 0) === 0, 3000);
    res.writes = [];
    for (const r of writes) { const resp = await r.response().catch(() => null); res.writes.push(resp ? resp.status() : 0); }
    res.after = pathOf(page);
    const parent = keyOf(res.after).replace(/\/[^/]+$/, '') || '/';
    const lists = [...new Set([from, keyOf(formPath), parent].filter((x) => x && !RISKY.test(x)))].slice(0, 3);
    for (const lp of lists) {
      if (Date.now() - t0 > 2 * BUDGET) { res.budget = true; break; }
      const pending = [];
      const onResp = (resp) => pending.push((async () => {
        try {
          if (resp.request().method() !== 'GET' || !/json/i.test(resp.headers()['content-type'] || '')) return null;
          const body = await resp.text();
          if (!body.includes(value)) return null;
          let best = 0;
          const walk = (v, d) => { if (d > 6 || v === null || typeof v !== 'object') return;
            if (Array.isArray(v)) { best = Math.max(best, v.filter((x) => JSON.stringify(x).includes(value)).length); v.slice(0, 500).forEach((x) => walk(x, d + 1)); return; }
            Object.values(v).forEach((x) => walk(x, d + 1)); };
          walk(JSON.parse(body), 0);
          return { api: new URL(resp.url()).pathname, items: best };
        } catch (e) { return null; }
      })());
      page.on('response', onResp);
      await goto(page, lp);
      await settle(page, 600);
      const rec = await record(page);
      page.off('response', onResp);
      const json = (await Promise.all(pending)).filter(Boolean);
      res.lists.push({ path: lp, links: rec.nodes.filter((n) => n.role === 'link' && n.name.includes(value)).length,
        texts: (rec.text.match(new RegExp(value, 'g')) || []).length, json });
    }
  } finally { await page.close().catch(() => {}); }
  return res;
}

// ---------------------------------------------------------------- P9
const NOT_FOUND = /not found|no such|does ?n[o']t exist|doesn't exist|not exist|no longer exists|could ?n[o']t (?:be )?(?:found|load)|can(?:no|')t (?:be )?(?:found|load)|unable to (?:find|load)|failed to load|\berror\b|\b404\b|unknown|nothing here|invalid/i;
const CONTROL_ROLES = ['button', 'textbox', 'searchbox', 'combobox', 'checkbox', 'radio', 'tab', 'switch', 'spinbutton', 'slider', 'link'];
async function p9(ctx, job, pages, user) {
  const res = { id: job.id, signedInAs: user, top: null, records: [] };
  const page = await newPage(ctx);
  const t0 = Date.now();
  const bodyText = () => page.evaluate(() => ((document.body && document.body.innerText) || '').trim()).catch(() => '');
  const open = async (path) => {
    let status = 0;
    try { const r = await page.goto(new URL(path, base).href, { waitUntil: 'load', timeout: 8000 }); status = r ? r.status() : 0; } catch (e) { /* judged by the page */ }
    await settle(page, 600);
    let text = await bodyText();
    if (!text || !(NOT_FOUND.test(text) || DENIAL.test(text))) { await page.waitForTimeout(1200); text = await bodyText(); }
    return { status, text, finalPath: keyOf(pathOf(page)) };
  };
  const controls = (nodes, drop) => new Set(nodes.filter((n) => CONTROL_ROLES.includes(n.role) && n.name
    && !drop.some((d) => d && n.name.toLowerCase().includes(d))).map((n) => n.role + '|' + n.name));
  try {
    const unknown = '/zq-no-such-page-' + token(9);
    const top = await open(unknown);
    res.top = { path: unknown, status: top.status, blank: !top.text, finalPath: top.finalPath, text: top.text.replace(/\s+/g, ' ').slice(0, 120) };
    // record pages: pages a list item links to, one per address shape, the one with the most own controls first
    await goto(page, '/');
    const home = parseAria(await ariaOf(page));
    const listed = new Set();
    for (const p of pages) for (const l of p.links) if (l.inList) listed.add(keyOf(l.path));
    const cands = [];
    const shapes = new Set();
    for (const p of pages) {
      const k = keyOf(p.path);
      const segs = k.split('/').filter(Boolean);
      const pattern = segs.length + '|' + segs.slice(0, -1).join('/');  // the address pattern: all but the identifier
      if (!listed.has(k) || segs.length < 2 || RISKY.test(k) || DENIAL.test(p.text) || NOT_FOUND.test(p.text) || shapes.has(pattern)) continue;
      shapes.add(pattern);
      const id = decodedPath(segs[segs.length - 1]).toLowerCase();
      const own = [...controls(p.nodes, [id])].filter((c) => !controls(home, [id]).has(c) && !c.startsWith('link|'));
      cands.push({ path: k, id, own: own.length });
    }
    cands.sort((a, b) => b.own - a.own);
    for (const c of cands.slice(0, 3)) {
      if (Date.now() - t0 > 2 * BUDGET) { res.budget = true; break; }
      await goto(page, c.path);
      const realNodes = parseAria(await ariaOf(page));
      const realText = await bodyText();
      const segs = c.path.split('/').filter(Boolean);
      const bad = /^\d+$/.test(segs[segs.length - 1]) ? '987654' + String(Date.now() % 1000) : 'zq-no-such-' + token(9);
      const upath = '/' + segs.slice(0, -1).concat(bad).join('/');
      const failed = [];
      const onResp = (r) => { try { const u = new URL(r.url()); if (u.origin === ORIGIN && r.status() >= 400 && r.request().resourceType() !== 'document') failed.push(r.request().method() + ' ' + u.pathname + ' ' + r.status()); } catch (e) {} };
      page.on('response', onResp);
      const u = await open(upath);
      page.off('response', onResp);
      const unodes = parseAria(await ariaOf(page));
      const chrome = controls(home, [c.id, bad]);
      const own = [...controls(realNodes, [c.id, bad])].filter((x) => !chrome.has(x));
      const theirs = controls(unodes, [c.id, bad]);
      const shared = own.filter((x) => theirs.has(x));
      res.records.push({ real: c.path, path: upath, status: u.status, finalPath: u.finalPath, blank: !u.text,
        errorText: NOT_FOUND.test(u.text) || DENIAL.test(u.text), realHasErrorWords: NOT_FOUND.test(realText) || DENIAL.test(realText), own: own.length,
        ownControls: own.filter((x) => !x.startsWith('link|')).length, shared: shared.slice(0, 12),
        sharedControls: shared.filter((x) => !x.startsWith('link|')).length, failed: failed.slice(0, 4),
        text: u.text.replace(/\s+/g, ' ').slice(0, 160) });
    }
  } finally { await page.close().catch(() => {}); }
  return res;
}

// ---------------------------------------------------------------- P10 server-side role check (writes; OFF by default)
// The act's control is a free-text box + a submit button. A unique value makes the write's effect its own: the value a
// reload shows was written by whoever sent it. The capture (allowed) account performs the act once to learn the write
// request and prove the app does it; the restricted account then does the act through the shown control, or -- if the
// control is correctly hidden -- replays the captured request with ITS OWN session (its cookies, a fresh value, no
// capture-account credentials). Reported only when the fresh value is visible after a reload.
async function p10ShowsToken(page, paths, tok) {
  for (const p of paths) { if (!p) continue; await goto(page, p); const t = await mainText(page); if (t.includes(tok)) return true; }
  return false;
}
async function p10FindForm(page, candidates, hasForm) {
  const res = { path: null, tabRevealed: false };
  for (const path of [...new Set(candidates)].slice(0, 6)) {
    if (!path) continue;
    await goto(page, path);
    if (hasForm(await record(page))) { res.path = path; return res; }
    // cheap bounded tab reveal: a control shown only after an in-page tab is clicked
    const tabs = page.getByRole('tab');
    const n = Math.min(await tabs.count().catch(() => 0), 4);
    for (let i = 0; i < n; i++) {
      await tabs.nth(i).click({ timeout: 1500 }).catch(() => {});
      await page.waitForTimeout(300);
      if (hasForm(await record(page))) { res.path = path; res.tabRevealed = true; return res; }
    }
  }
  return res;
}
// r49 (audit #12): the replay must PROVABLY carry the restricted account's own authentication. Only headers known to
// carry none are kept from the capture; every other header of the captured write (Authorization, a custom auth
// header ...) is taken from the restricted session's OWN requests -- never sent there: skip. The capture's credential
// values must not sit in the address / body, and the restricted session must hold a credential of its own (a header
// or a cookie present in both sessions with a DIFFERENT value); otherwise skip -- never replay as someone else.
const P10_PLAIN = new Set(['content-type', 'accept', 'accept-language', 'x-requested-with']);
const P10_TRANSPORT = /^(?:host|content-length|origin|referer|user-agent|connection|accept-encoding|cookie|sec-[\w-]+|pragma|cache-control|priority|dnt|upgrade-insecure-requests)$/i;
function p10ReplayHeaders(template, own, capCookies, ownCookies) {
  const headers = {};
  const secrets = Object.values(capCookies || {}).map(String);
  let ownDiffers = false;
  for (const [k0, v] of Object.entries((template && template.headers) || {})) {
    const k = k0.toLowerCase();
    if (P10_PLAIN.has(k)) { headers[k] = v; continue; }
    if (P10_TRANSPORT.test(k)) continue;  // the cookie: the restricted context sends its own
    secrets.push(String(v));
    if (!own || !(k in own)) return { skip: 'the captured write carries “' + k + '”, which the restricted session never sends' };
    headers[k] = own[k];
    if (String(own[k]) !== String(v)) ownDiffers = true;
  }
  const where = String((template && template.url) || '') + '\n' + String((template && template.data) || '');
  if (secrets.some((s) => s.length >= 8 && where.includes(s))) return { skip: 'the captured write carries a capture-account credential in its address or body' };
  const cookieDiffers = Object.keys(ownCookies || {}).some((n) => capCookies && n in capCookies && String(capCookies[n]) !== String(ownCookies[n]));
  if (!ownDiffers && !cookieDiffers) return { skip: 'no credential of the restricted account itself (no own auth header / session cookie differing from the capture account\'s)' };
  return { headers };
}
const cookieMap = async (ctx) => Object.fromEntries(((await ctx.cookies(base).catch(() => [])) || []).map((c) => [c.name, c.value]));
async function p10(browser, job, vpages, owner) {
  const res = { id: job.id, accounts: [] };
  const submit = job.submit;
  const fieldName = job.field;
  const hasForm = (rec) => rec.nodes.some((n) => n.role === 'textbox' && n.name === fieldName)
    && rec.nodes.some((n) => n.role === 'button' && n.name === submit && !/\bdisabled\b/.test(n.attrs));
  const already = ((owner && owner.pages) || []).concat(vpages || []).filter(hasForm).map((p) => p.path);
  const candidates = already.concat(((owner && owner.pages) || []).map((p) => p.path), (vpages || []).map((p) => p.path));
  // 1) capture: an allowed account performs the act with a unique value, so we learn the write request and its parent
  const cap = await signIn(browser, job.capture.user, job.capture.password);
  res.capture = { user: job.capture.user, signedIn: cap.ok, why: cap.why };
  if (!cap.ok) { res.skip = 'capture account sign-in failed: ' + cap.why; await cap.context.close().catch(() => {}); return res; }
  let template = null;
  let formPath = null;
  const parent = (p) => { const k = keyOf(p || ''); const up = k.replace(/\/[^/]+$/, ''); return up || '/'; };
  try {
    const find = await p10FindForm(cap.page, candidates, hasForm);
    formPath = find.path;
    res.route = formPath;
    res.tabRevealed = find.tabRevealed;
    if (!formPath) { res.skip = 'no page (crawled or a bounded tab click) shows the form (' + submit + ' + ' + fieldName + ')'; }
    else {
      const ownerTok = token(0) + 'cap';
      const reqs = [];
      const onReq = (r) => { if (/^(POST|PUT|PATCH)$/.test(r.method()) && String(r.postData() || '').includes(ownerTok)) reqs.push({ method: r.method(), url: r.url(), headers: r.headers(), data: r.postData() }); };
      cap.page.on('request', onReq);
      await cap.page.getByRole('textbox', { name: fieldName, exact: true }).first().fill(ownerTok, { timeout: 3000 }).catch(() => {});
      await cap.page.getByRole('button', { name: submit, exact: true }).first().click({ timeout: 3000 }).catch(() => {});
      await settle(cap.page, 1200);
      const afterText = await mainText(cap.page);
      cap.page.off('request', onReq);
      res.capture.tookEffect = afterText.includes(ownerTok) || await p10ShowsToken(cap.page, [formPath, parent(formPath)], ownerTok);
      if (reqs.length && res.capture.tookEffect) template = reqs[reqs.length - 1];
    }
  } catch (e) { res.capture.error = String(e).slice(0, 150); }
  const capCookies = await cookieMap(cap.context);
  await cap.context.close().catch(() => {});
  if (!template) { res.skip = res.skip || 'the capture account could not perform the act (no write took effect)'; return res; }
  const t0 = Date.now();
  for (const acc of job.restricted || []) {
    if (Date.now() - t0 > BUDGET) break;
    const s = await signIn(browser, acc.user, acc.password);
    const entry = { user: acc.user, roles: acc.roles, signedIn: s.ok, why: s.why };
    if (s.ok) {
      const restTok = token(0) + 'r' + (job.restricted.indexOf(acc));
      entry.value = restTok;
      // the restricted session's OWN request headers (same origin), for a replay that must carry its own credentials
      const ownHeaders = {};
      const onOwn = (r) => { if (String(r.url()).startsWith(ORIGIN)) for (const [k, v] of Object.entries(r.headers() || {})) ownHeaders[k.toLowerCase()] = v; };
      s.page.on('request', onOwn);
      await goto(s.page, formPath);
      s.page.off('request', onOwn);
      const uiVisible = hasForm(await record(s.page));
      entry.controlHidden = !uiVisible;
      let status = null;
      if (uiVisible) {
        entry.via = 'ui';
        const writes = [];
        const onReq = (r) => { if (/^(POST|PUT|PATCH)$/.test(r.method()) && String(r.postData() || '').includes(restTok)) writes.push(r); };
        s.page.on('request', onReq);
        await s.page.getByRole('textbox', { name: fieldName, exact: true }).first().fill(restTok, { timeout: 3000 }).catch(() => {});
        await s.page.getByRole('button', { name: submit, exact: true }).first().click({ timeout: 3000 }).catch(() => {});
        await settle(s.page, 1200);
        s.page.off('request', onReq);
        for (const w of writes) { const resp = await w.response().catch(() => null); if (resp) status = resp.status(); }
      } else {
        entry.via = 'replay';
        const plan = p10ReplayHeaders(template, ownHeaders, capCookies, await cookieMap(s.context));
        if (plan.skip) entry.replaySkip = plan.skip;
        else {
          entry.authOwn = true;
          const data = String(template.data || '').split(ownerTokOf(template, restTok)).join(restTok);
          try { const resp = await s.context.request.fetch(template.url, { method: template.method, headers: plan.headers, data }); status = resp.status(); }
          catch (e) { entry.replayError = String(e).slice(0, 150); }
        }
      }
      entry.status = status;
      // the write took effect only when the fresh value is visible after a reload (a skipped replay wrote nothing)
      entry.tookEffect = entry.replaySkip ? false : await p10ShowsToken(s.page, [formPath, parent(formPath)], restTok);
    }
    await s.context.close().catch(() => {});
    res.accounts.push(entry);
  }
  return res;
}
// the capture token embedded in the template's body, swapped for the restricted account's own value on replay
function ownerTokOf(template, restTok) {
  const m = String(template.data || '').match(/zq\d+p0cap/);
  return m ? m[0] : '\u0000no-token';
}

// ---------------------------------------------------------------- P11 a stated row field that must open the record
// "clicking the number or title opens the detail page": in a list row showing a "#<n>" and a title link, both are
// clicked (from the same fresh page); reported only when the title opens another page and the number -- no link,
// no button, no handler -- leaves the page where it was and opens no dialog.
const NUM_RE_SRC = '^#\\d{1,7}$';
// r49 (audit #8): a numbered row is bound to the rule only when the page is the stated list (a segment of its address
// starts with the stated record stem, or a heading names it) and exactly ONE record address among the row's links is
// the row's own (the stem in a segment, the row's number as a segment); that link is the title. Never "the longest link".
// (r50 narrows both segment tests below: the whole stem segment, the number right after it.)
function p11Segs(p) {
  return String(p || '').split(/[?#]/)[0].split('/').filter(Boolean).map((s) => { try { return decodeURIComponent(s).toLowerCase(); } catch (e) { return s.toLowerCase(); } });
}
// r50 (audit #5): the stem is a WHOLE segment (its singular or plural form, never a prefix: "/issue-reporters/7" is no
// issue), and the row's number is the segment right after THAT one ("/projects/7/issues/23" is issue 23, not 7).
function p11Bind(num, links, pagePath, headings, stems) {
  const isStem = (seg, st) => seg === st || seg === st + 's' || seg === st + 'es' || (st.endsWith('y') && seg === st.slice(0, -1) + 'ies');
  stems = (stems || []).map((s) => String(s).toLowerCase()).filter(Boolean);
  if (!stems.length) return { skip: 'no stated record kind' };
  const digits = String(num || '').replace(/^#/, '');
  const hasStem = (p) => p11Segs(p).some((s) => stems.some((st) => isStem(s, st)));
  const listBound = hasStem(pagePath) || (headings || []).some((h) => stems.some((st) => new RegExp('\\b' + st.replace(/y$/, ''), 'i').test(String(h))));
  if (!listBound) return { skip: 'the page is not the stated list' };
  const idAfterStem = (p) => { const s = p11Segs(p); return s.some((x, i) => i + 1 < s.length && s[i + 1] === digits && stems.some((st) => isStem(x, st))); };
  const own = (links || []).filter((l) => idAfterStem(l.path));
  const paths = [...new Set(own.map((l) => p11Segs(l.path).join('/')))];
  if (paths.length !== 1) return { skip: own.length ? 'more than one record address in the row' : 'no link of the row is the record’s own address' };
  return { link: own[0].j };
}
async function p11Mark(page, stems) {
  const got = await page.evaluate((src) => {
    const num = new RegExp(src);
    document.querySelectorAll('[data-arc-num],[data-arc-l]').forEach((e) => { e.removeAttribute('data-arc-num'); e.removeAttribute('data-arc-l'); });
    const ACT = 'a[href],button,[role=link],[role=button],[onclick],summary,label,[tabindex]';
    const shown = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; };
    const own = (e) => (e.textContent || '').trim();
    const els = Array.from(document.querySelectorAll('body *')).filter((e) => num.test(own(e)) && !Array.from(e.children).some((c) => num.test(own(c))));
    const rows = [];
    for (const el of els) {
      if (rows.length >= 12) break;
      if (!shown(el) || el.closest('nav,header,[role=navigation],[role=banner]')) continue;
      const row = el.closest('li,tr,article,[role=row],[role=listitem]');
      if (!row) continue;
      const links = Array.from(row.querySelectorAll('a[href]')).filter((a) => a.origin === location.origin && shown(a) && !a.contains(el)
        && !el.contains(a) && own(a).length >= 2 && !num.test(own(a)));
      if (!links.length) continue;
      const i = rows.length;
      el.setAttribute('data-arc-num', String(i));
      links.forEach((a, j) => a.setAttribute('data-arc-l', i + '-' + j));
      rows.push({ i, num: own(el), activatable: !!el.closest(ACT), cursor: getComputedStyle(el).cursor,
        rowText: (row.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 160),
        links: links.map((a, j) => ({ j, path: a.pathname + a.search, text: own(a).replace(/\s+/g, ' ').slice(0, 120) })) });
    }
    const headings = Array.from(document.querySelectorAll('h1,h2')).filter(shown).map((e) => own(e).replace(/\s+/g, ' ').slice(0, 80)).slice(0, 4);
    return { rows, headings };
  }, NUM_RE_SRC).catch(() => null);
  if (!got || !got.rows.length) return null;
  const unbound = [];
  for (const r of got.rows) {
    const b = p11Bind(r.num, r.links, pathOf(page), got.headings, stems);
    if (b.skip) { unbound.push(r.num + ': ' + b.skip); continue; }
    const l = r.links.find((x) => x.j === b.link);
    return { num: r.num, activatable: r.activatable, cursor: r.cursor, title: l.text.slice(0, 80), href: l.path, rowText: r.rowText,
      numSel: '[data-arc-num="' + r.i + '"]', titleSel: '[data-arc-l="' + r.i + '-' + l.j + '"]' };
  }
  return { unbound: unbound.slice(0, 4) };
}
const squash = (s) => String(s || '').replace(/\s+/g, ' ').trim();
async function p11(ctx, job, pages) {
  const res = { id: job.id, route: null, rows: [] };
  const cands = pages.filter((p) => /#\d{1,7}\b/.test(p.text) && p.links.some((l) => l.inList) && !DENIAL.test(p.text)).slice(0, 3);
  if (!cands.length) { res.skip = 'no crawled page shows a list row with a #number'; return res; }
  const page = await newPage(ctx);
  const t0 = Date.now();
  const dialogs = () => page.locator('[role=dialog],[role=alertdialog],dialog[open]').count().catch(() => 0);
  try {
    for (const p of cands) {
      if (Date.now() - t0 > BUDGET) { res.budget = true; break; }
      await goto(page, p.path);
      const m = await p11Mark(page, job.stems);
      if (!m) continue;
      if (m.unbound) { res.unbound = (res.unbound || []).concat(p.path + ' ' + m.unbound.join('; ')).slice(0, 4); continue; }
      const row = Object.assign({ route: p.path }, m);
      delete row.numSel; delete row.titleSel;
      const d0 = await dialogs();
      await page.locator(m.numSel).first().click({ timeout: 3000 }).catch(() => {});
      await settle(page, 700);
      row.numberPath = pathOf(page);
      row.numberDialog = (await dialogs()) > d0;
      await goto(page, p.path);
      const again = await p11Mark(page, job.stems);
      if (!again || again.unbound || again.num !== m.num || again.href !== m.href) { row.skip = 'the row changed on reload'; res.rows.push(row); continue; }
      await page.locator(again.titleSel).first().click({ timeout: 3000 }).catch(() => {});
      await settle(page, 700);
      row.titlePath = pathOf(page);
      const landed = await mainText(page);
      row.titleDenied = DENIAL.test(landed);
      // the stated target: the title opened ITS OWN record address, and that page shows the record's title
      row.titleLanded = keyOf(row.titlePath) === keyOf(m.href);
      row.targetShowsTitle = !!squash(m.title) && squash(landed).includes(squash(m.title));
      if (!row.titleLanded || !row.targetShowsTitle) row.skip = 'the title did not open its own record page showing that title';
      res.rows.push(row);
      res.route = res.route || p.path;
      if (!row.skip && row.titlePath !== keyOf(p.path) && keyOf(row.titlePath) !== keyOf(p.path)) break;  // one clear row is enough
    }
  } finally { await page.close().catch(() => {}); }
  if (!res.rows.length) res.skip = res.skip || (res.unbound ? 'no numbered row could be bound to the stated list and its record' : 'no list row with a #number and a title link');
  return res;
}

// ---------------------------------------------------------------- P12 edit + save of the probe's OWN record
// The record is created through the stated create flow with a unique value; the edit form must come up prefilled
// with that value (so the probe never edits a seeded record); the stated field gets a second unique value, the stated
// save control is activated, the page is reloaded. The findings side reports only when a write carrying the new value
// was sent and the value is not there after the reload.
const ERRORISH = /fail|error|could ?n[o']t|can ?n[o']t|unable|went wrong|not saved|invalid/i;
const nameRe = (s) => new RegExp('^\\s*' + escRe(String(s).trim()) + '\\s*$', 'i');
const textLines = (t) => new Set(String(t || '').split('\n').map((s) => s.replace(/\s+/g, ' ').trim()).filter(Boolean));
// r49 (audit #7): is the opened edit form the probe's OWN record? The edited field is the create field: its prefill
// must hold the probe's value. Another field: the form's own scope (the form / dialog around the box, else its row)
// must carry the value -- another of its inputs or its text -- and hold no OTHER row that carries it. The value
// anywhere else on the page proves nothing (a seeded row's inline form beside the probe's row).
// r50 (audit #4): the form's TEXT carrying the value binds it only through a row: the box's own row (no form around it)
// or an edit control taken from the probe's own row (info.rowBound). One big form around a list and the editor, opened by
// the page's only Edit, may be a seeded record's form beside the probe's row: skip.
function p12Own(info, tok, sameField) {
  if (String((info && info.prefill) || '').includes(tok)) return 'prefill';
  if (sameField || !info || !info.scope || info.rowHasTok) return '';
  if (info.inputHasTok) return 'form-input';
  if (info.textHasTok && (info.scope === 'row' || info.rowBound === true)) return 'form-text';
  return '';
}
// r49 (audit #7): the new value must be valid for the EDITED field -- the create accepted the token only in its own
// field. An e-mail / address field gets that form; a field that is not free text (a number, a date, a phone ...) or
// too short for the token gets none: the probe skips.
const P12_NOT_TEXT = /phone|\btel\b|date|time|number|amount|price|count|\bage\b|zip|postal|year|\bport\b|size|quantity|percent|colou?r/i;
function p12Value(info, field, tok, sameField) {
  const type = String((info && info.type) || 'text').toLowerCase();
  const max = Number((info && info.maxLength) || 0);
  let v = null;
  if (sameField) v = tok;
  else if (type === 'email' || /e-?mail/i.test(field)) v = tok + '@example.com';
  else if (type === 'url' || /\b(?:url|website|web site|homepage|link)\b/i.test(field)) v = 'https://example.com/' + tok;
  else if (['text', 'search', 'textarea'].includes(type) && !P12_NOT_TEXT.test(field)) v = tok;
  if (v && max > 0 && max < v.length) v = null;
  return v;
}
async function p12(browser, job, owner) {
  const res = { id: job.id, shape: job.shape, signedInAs: job.account.user };
  let s = null;
  let own = false;
  if (owner && owner.ok && owner.pages && spec.owner && spec.owner.user === job.account.user) s = owner;
  else { s = await signIn(browser, job.account.user, job.account.password); own = true; }
  if (!s.ok) { res.skip = 'sign-in failed: ' + s.why; if (own) await s.context.close().catch(() => {}); return res; }
  const pages = (s.pages || (owner && owner.pages) || []);
  const page = await newPage(s.context);
  const t0 = Date.now();
  try {
    // 1) the create form: on a crawled page, behind its stated opener, or one level down a link the node names
    let create = null;
    let createPath = null;
    const hasCreate = (rec, c) => rec.nodes.some((n) => n.role === 'textbox' && nameRe(c.field).test(n.name))
      && rec.nodes.some((n) => n.role === 'button' && nameRe(c.submit).test(n.name) && !/\bdisabled\b/.test(n.attrs));
    const openerOf = (rec, c) => { for (const o of c.openers || []) { const n = rec.nodes.find((x) => (x.role === 'link' || x.role === 'button') && nameRe(o).test(x.name)); if (n) return n; } return null; };
    const hint = new Set((job.hints || []).map((h) => h.toLowerCase()));
    // an in-page section the node names ("Settings → Branches": a link to #branches, a tab): links and tabs only,
    // never a button (a button may write)
    const hinted = (rec) => rec.nodes.filter((x) => (x.role === 'link' || x.role === 'tab') && hint.has(String(x.name || '').trim().toLowerCase())
      && !RISKY.test(x.name));
    const tryPath = async (path, c) => {
      await goto(page, path);
      let rec = await record(page);
      if (hasCreate(rec, c)) return true;
      let n = openerOf(rec, c);
      if (!n) {
        for (const h of hinted(rec).slice(0, 3)) {
          await page.getByRole(h.role, { name: nameRe(h.name) }).first().click({ timeout: 2000 }).catch(() => {});
          await settle(page, 400);
          rec = await record(page);
          if (hasCreate(rec, c)) return true;
          n = openerOf(rec, c);
          if (n) break;
          if (keyOf(pathOf(page)) !== keyOf(path)) await goto(page, path);
        }
      }
      if (!n) return false;
      await page.getByRole(n.role, { name: nameRe(n.name) }).first().click({ timeout: 3000 }).catch(() => {});
      await settle(page, 500);
      rec = await record(page);
      return hasCreate(rec, c);
    };
    const visited = new Set();
    outer: for (const c of job.creates || []) {
      const direct = pages.filter((p) => hasCreate(p, c) || openerOf(p, c)).map((p) => p.path);
      const sections = pages.filter((p) => hinted(p).length).sort((a, b) => hinted(b).length - hinted(a).length).map((p) => p.path);
      const deeper = [];
      for (const p of pages) for (const l of p.links) if (hint.has(String(l.name || '').trim().toLowerCase()) && !RISKY.test(l.path)) deeper.push(l.path);
      for (const path of [...new Set(direct.concat(sections, deeper))].slice(0, 8)) {
        if (Date.now() - t0 > 2 * BUDGET) { res.budget = true; break outer; }
        if (visited.has(path + '|' + c.submit)) continue;
        visited.add(path + '|' + c.submit);
        if (await tryPath(path, c)) { create = c; createPath = path; break outer; }
      }
    }
    if (!create) { if (!res.budget) res.skip = 'no page shows the stated create form'; return res; }
    res.createRoute = createPath;
    // 2) create the probe's own record
    const tok1 = token(12) + 'a';
    res.value1 = tok1;
    const cWrites = [];
    const onC = (r) => { if (/^(POST|PUT|PATCH)$/.test(r.method()) && String(r.postData() || '').includes(tok1)) cWrites.push(r); };
    page.on('request', onC);
    await page.getByRole('textbox', { name: nameRe(create.field) }).first().fill(tok1, { timeout: 3000 }).catch(() => {});
    await page.getByRole('button', { name: nameRe(create.submit) }).first().click({ timeout: 3000 }).catch(() => {});
    await settle(page, 1200);
    page.off('request', onC);
    res.createStatus = [];
    for (const r of cWrites) { const resp = await r.response().catch(() => null); res.createStatus.push(resp ? resp.status() : 0); }
    res.created = res.createStatus.some((x) => x >= 200 && x < 300);
    if (!res.created) { res.skip = 'the create flow wrote nothing accepted'; return res; }
    // 3) its edit control: the stated opener on the page the create landed on / the record's own page, or the Edit
    // control of the list row / item that shows the probe's value
    // r50: how the edit control was bound -- 'row' (taken from the probe's own row), 'page' (the page's only one)
    let editBy = '';
    const findEdit = async () => {
      if (job.opener) {
        // r49: the stated opener of the probe's OWN row / item first (one opener per row: the first may be a seeded one)
        const inRow = await page.evaluate(([tok, name]) => {
          document.querySelectorAll('[data-arc-edit]').forEach((e) => e.removeAttribute('data-arc-edit'));
          const nm = (x) => (x.getAttribute('aria-label') || x.innerText || x.getAttribute('title') || '').replace(/\s+/g, ' ').trim().toLowerCase();
          const rows = Array.from(document.querySelectorAll('li,tr,article,[role=row],[role=listitem]')).filter((r) => (r.innerText || '').includes(tok));
          rows.sort((a, b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length);
          for (const r of rows) {
            const es = Array.from(r.querySelectorAll('button,a[href],[role=button],[role=link]')).filter((x) => nm(x) === name.toLowerCase());
            if (es.length === 1) { es[0].setAttribute('data-arc-edit', '1'); return true; }
            if (es.length > 1) return false;
          }
          return false;
        }, [tok1, job.opener]).catch(() => false);
        if (inRow) { editBy = 'row'; return page.locator('[data-arc-edit]').first(); }
        // else the record's own page: only ONE such control shown (several: whose would it be?)
        const shown = [];
        for (const role of ['button', 'link']) {
          const all = page.getByRole(role, { name: nameRe(job.opener) });
          const n = Math.min(await all.count().catch(() => 0), 6);
          for (let i = 0; i < n; i++) if (await all.nth(i).isVisible().catch(() => false)) shown.push(all.nth(i));
        }
        if (shown.length === 1) editBy = 'page';
        return shown.length === 1 ? shown[0] : null;
      }
      const marked = await page.evaluate((tok) => {
        document.querySelectorAll('[data-arc-edit]').forEach((e) => e.removeAttribute('data-arc-edit'));
        const rows = Array.from(document.querySelectorAll('li,tr,article,[role=row],[role=listitem]')).filter((r) => (r.innerText || '').includes(tok));
        rows.sort((a, b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length);
        for (const r of rows) {
          const e = Array.from(r.querySelectorAll('button,a[href],[role=button]')).find((x) => /^edit\b/i.test(((x.getAttribute('aria-label') || x.innerText || '')).trim()));
          if (e) { e.setAttribute('data-arc-edit', '1'); return true; }
        }
        return false;
      }, tok1).catch(() => false);
      if (marked) editBy = 'row';
      return marked ? page.locator('[data-arc-edit]').first() : null;
    };
    let edit = await findEdit();
    const back = [pathOf(page), createPath];
    if (!edit) {
      const link = page.getByRole('link', { name: new RegExp(escRe(tok1)) }).first();
      if (await link.isVisible().catch(() => false)) { await link.click({ timeout: 3000 }).catch(() => {}); await settle(page, 500); edit = await findEdit(); }
    }
    if (!edit) { await goto(page, createPath); edit = await findEdit(); }
    if (!edit) { res.skip = 'no edit control for the probe\'s own record'; return res; }
    const recordPath = pathOf(page);
    res.route = recordPath;
    await edit.click({ timeout: 3000 }).catch(() => {});
    await settle(page, 500);
    const box = page.getByRole('textbox', { name: nameRe(job.field) }).first();
    if (!(await box.isVisible().catch(() => false))) { res.skip = 'the edit control showed no “' + job.field + '” text box'; return res; }
    // r49: the form must be the probe's own record -- bound through the form itself (p12Own), never the page's text
    const info = await box.evaluate((el, tok) => {
      const ROWS = 'li,tr,article,[role=row],[role=listitem]';
      const val = (e) => String(e.value || '');
      const form = el.closest('form,[role=dialog],[role=alertdialog],dialog');
      const scope = form || el.closest(ROWS);
      const carries = (e) => (e.innerText || '').includes(tok) || Array.from(e.querySelectorAll('input,textarea')).some((i) => i !== el && val(i).includes(tok));
      const out = { prefill: val(el), scope: form ? 'form' : (scope ? 'row' : ''),
        type: (el.tagName === 'TEXTAREA' ? 'textarea' : (el.getAttribute('type') || 'text')).toLowerCase(),
        maxLength: Number(el.getAttribute('maxlength') || 0) };
      if (!scope) return out;
      out.inputHasTok = Array.from(scope.querySelectorAll('input,textarea')).some((i) => i !== el && val(i).includes(tok));
      out.textHasTok = (scope.innerText || '').includes(tok);
      out.rowHasTok = Array.from(scope.querySelectorAll(ROWS)).some((r) => !r.contains(el) && carries(r));
      return out;
    }, tok1).catch(() => ({ prefill: '' }));
    res.prefill = String(info.prefill || '').slice(0, 80);
    const sameField = nameRe(create.field).test(job.field);
    res.sameField = sameField;
    res.editBy = editBy;
    info.rowBound = editBy === 'row';
    res.ownBy = p12Own(info, tok1, sameField);
    res.own = !!res.ownBy;
    if (!res.own) { res.skip = 'the edit form is not bound to the probe\'s own record (' + (sameField ? 'its prefill is not the probe\'s value' : 'the form itself does not carry the probe\'s value') + ')'; return res; }
    const tok2 = token(12) + 'b';
    const value2 = p12Value(info, job.field, tok2, sameField);
    if (!value2) { res.skip = 'no value known to be valid for “' + job.field + '” (' + (info.type || 'text') + ')'; return res; }
    res.value2 = value2;
    await box.fill(value2, { timeout: 3000 }).catch(() => {});
    res.typed = String(await box.inputValue().catch(() => '')).includes(tok2);
    res.valueValid = await box.evaluate((e) => typeof e.checkValidity !== 'function' || e.checkValidity()).catch(() => false);
    if (res.typed && !res.valueValid) { res.skip = 'the new value is not valid for “' + job.field + '” (the browser says so)'; return res; }
    const save = page.getByRole('button', { name: nameRe(job.save) }).first();
    if (!res.typed || !(await save.isVisible().catch(() => false))) { res.skip = 'the new value was not typed or “' + job.save + '” is not shown'; return res; }
    const before = textLines(await mainText(page));
    const sWrites = [];
    const onS = (r) => { if (/^(POST|PUT|PATCH)$/.test(r.method()) && String(r.postData() || '').includes(tok2)) sWrites.push(r); };
    page.on('request', onS);
    await save.click({ timeout: 3000 }).catch(() => {});
    await settle(page, 1500);
    await waitUntil(async () => (page._inflight || 0) === 0, 3000);
    page.off('request', onS);
    res.saveStatus = [];
    for (const r of sWrites) { const resp = await r.response().catch(() => null); res.saveStatus.push(resp ? resp.status() : 0); }
    const afterText = await mainText(page);
    res.errorLines = [...textLines(afterText)].filter((l) => !before.has(l) && ERRORISH.test(l) && l.length <= 200).slice(0, 3);
    res.shownAfterSave = afterText.includes(tok2);
    // 4) reload the record's page and the page the save left: is the new value there?
    res.reloaded = [];
    for (const path of [...new Set([pathOf(page), recordPath])]) {
      await goto(page, path);
      await settle(page, 400);
      let t = await mainText(page);
      // a section the address does not keep (#branches): shown again through the node's links / tabs
      if (!t.includes(tok1) && !t.includes(tok2)) {
        for (const h of hinted(await record(page)).slice(0, 3)) {
          await page.getByRole(h.role, { name: nameRe(h.name) }).first().click({ timeout: 2000 }).catch(() => {});
          await settle(page, 400);
          t = await mainText(page);
          if (t.includes(tok1) || t.includes(tok2)) break;
          if (keyOf(pathOf(page)) !== keyOf(path)) { await goto(page, path); t = await mainText(page); }
        }
      }
      let has = t.includes(tok2);
      if (!has && !job.opener) {  // an edit form in a row: the value may show only in the form or the row's name
        has = (await page.evaluate((tok) => Array.from(document.querySelectorAll('input,textarea')).some((i) => (i.value || '').includes(tok)), tok2).catch(() => false));
      }
      res.reloaded.push({ path, hasNew: has, hasOld: t.includes(tok1) });
    }
    res.persisted = res.reloaded.some((r) => r.hasNew);
  } finally {
    await page.close().catch(() => {});
    if (own) await s.context.close().catch(() => {});
  }
  return res;
}

// ---------------------------------------------------------------- P13 a switched view survives a reload (v144n)
// convention: a view the user switched to (a tab, a filter) is addressable -- the URL reflects it and a reload shows
// the same view. On a crawled page showing >= 2 stated entries: the view on load (the default), then up to 2 entries
// that are not the active one, each from a fresh load: activate, record the view; when it differs from the default,
// reload and record again. The judgement (Python) reports only a reload whose text is exactly the default's.
async function p13(ctx, job, pages) {
  const res = { id: job.id, route: null, tried: [] };
  const score = (p) => job.names.filter((nm) => p.nodes.some((n) => ['link', 'tab', 'button'].includes(n.role) && baseName(n.name) === nm)).length;
  const cands = pages.filter((p) => !p.tab && score(p) >= 2).sort((a, b) => score(b) - score(a));
  if (!cands.length) { res.skip = 'no crawled page shows two of the entries'; return res; }
  res.route = cands[0].path;
  const page = await newPage(ctx);
  const t0 = Date.now();
  const view = () => page.evaluate((names) => {
    const root = document.querySelector('main') || document.body;
    const nameOf = (e) => (e.getAttribute('aria-label') || e.innerText || '').replace(/\s+/g, ' ').trim().replace(/\s*\(?\d+\)?$/, '');
    const shown = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; };
    const entries = Array.from(document.querySelectorAll('a,button,[role=tab],[role=link],[role=button]')).filter((e) => names.includes(nameOf(e)) && shown(e));
    const on = (e) => e.getAttribute('aria-selected') === 'true' || e.getAttribute('aria-pressed') === 'true'
      || (!!e.getAttribute('aria-current') && e.getAttribute('aria-current') !== 'false');
    const headings = Array.from(root.querySelectorAll('h1,h2,h3,h4,h5,h6,[role=heading]')).filter(shown)
      .map((e) => (e.innerText || '').replace(/\s+/g, ' ').trim()).filter(Boolean).slice(0, 8);
    const text = (root.innerText || '').replace(/\s+/g, ' ').trim();
    return { text, headings, active: [...new Set(entries.filter(on).map(nameOf))], url: location.pathname + location.search + location.hash };
  }, job.names).catch(() => ({ text: '', headings: [], active: [], url: '' }));
  const brief = (v) => ({ url: v.url, active: v.active, headings: v.headings, length: v.text.length });
  try {
    await goto(page, res.route);
    const d = await view();
    res.defaultView = brief(d);
    if (!d.text) { res.skip = 'the page shows no text'; return res; }
    for (const nm of job.names) {
      if (res.tried.length >= 2) break;
      if (Date.now() - t0 > BUDGET) { res.budget = true; break; }
      if (d.active.includes(nm)) continue;
      if (res.tried.length) await goto(page, res.route);
      if (!(await activateEntry(page, nm))) continue;
      await settle(page, 600);
      const x = await view();
      const t = Object.assign({ name: nm, changed: x.text !== d.text }, brief(x));
      res.tried.push(t);
      if (!t.changed) continue;
      await page.reload({ waitUntil: 'load', timeout: 8000 }).catch(() => {});
      await settle(page, 600);
      let r = await view();
      if (r.text === d.text) { await page.waitForTimeout(1500); await settle(page, 200); r = await view(); }  // a late render
      t.reload = Object.assign({ sameAsDefault: r.text === d.text, sameAsView: r.text === x.text }, brief(r));
      if (t.reload.sameAsDefault) {
        // what the switched view showed and the default does not: its own content, gone after the reload
        t.ownText = [...new Set(x.text.slice(0, 4000).split(' ').filter((w) => w.length > 3 && !d.text.includes(w)))].slice(0, 12);
        break;  // one clear case is enough
      }
    }
  } finally { await page.close().catch(() => {}); }
  return res;
}

async function guard(fn) { try { return await fn(); } catch (e) { return { error: String(e).slice(0, 200) }; } }
(async () => {
  const browser = await chromium.launch();
  const t0 = Date.now();
  try {
    let owner = null;
    if (spec.owner) {
      owner = await signIn(browser, spec.owner.user, spec.owner.password);
      out.owner = { user: spec.owner.user, ok: owner.ok, why: owner.why };
      if (owner.ok) {
        owner.pages = await crawl(owner.context, ['/'], 20, 15000, 2, true);
        owner.paths = owner.pages.filter((p) => !p.tab).map((p) => p.path);
        out.crawl.owner = owner.paths;
      }
    }
    const visitor = await browser.newContext({ viewport: VIEW });
    const starts = [{ path: '/', depth: 0 }, ...(spec.hints || []).map((p) => ({ path: p, depth: 0 })),
      ...((owner && owner.paths) || []).slice(0, 20).map((p) => ({ path: p, depth: 1 }))];
    const vpages = await crawl(visitor, starts, spec.maxPages || 40, spec.crawlMs || 30000, 3, false, (spec.p7 || []).map((j) => j.name));
    out.crawl.visitor = vpages.map((p) => p.path);
    out.crawl.tabs = TABS_LOG.slice(0, 30);
    // the views behind a tab (v144n) serve P1 / P3 / P11 / P12 (and P8 / P10, which read owner.pages); every other
    // probe keeps the pages as loaded
    const vbase = vpages.filter((p) => !p.tab);
    const baseOf = (o) => (o && o.pages ? Object.assign({}, o, { pages: o.pages.filter((p) => !p.tab) }) : o);
    out.crawl.ms = Date.now() - t0;
    // a control only a signed-in user gets (github v129: “Add member”, “Edit issue title”) is on no visitor page:
    // when the visitor's pages lack what a probe needs, the same probe runs once more on the owner's pages, signed in
    // the owner's own crawl is small (20 pages, 15 s): once, the owner also records the visitor's pages it lacks
    // (github v129: the issue page with “Edit issue title” was reached by the visitor crawl only), bounded like it
    let widened = false;
    const widen = async () => {
      if (widened) return;
      widened = true;
      const have = new Set(owner.pages.map((p) => p.path.split('?')[0]));
      const more = vbase.map((p) => p.path).filter((p) => !have.has(p.split('?')[0])).map((p) => ({ path: p, depth: 0 }));
      if (!more.length) return;
      const extra = await crawl(owner.context, more, 20, 12000, 0, false);
      owner.pages = owner.pages.concat(extra);
      out.crawl.ownerMore = extra.map((p) => p.path);
    };
    const reach = async (fn, job) => {
      const asLoaded = fn !== p1 && fn !== p11;  // the views behind a tab serve P1 / P11 here (P3 / P12 read them directly)
      const res = await guard(() => fn(visitor, job, asLoaded ? vbase : vpages));
      if (!(res && /^no crawled page/.test(res.skip || '') && owner && owner.ok && owner.pages)) return res;
      await guard(widen);
      if (!owner.pages.length) return res;
      const again = await guard(() => fn(owner.context, job, asLoaded ? owner.pages.filter((p) => !p.tab) : owner.pages));
      return Object.assign(again, { signedInAs: spec.owner.user, visitorSkip: res.skip });
    };
    for (const job of spec.p1 || []) out.p1.push(await reach(p1, job));
    for (const job of spec.p2 || []) out.p2.push(await reach(p2, job));
    for (const job of spec.p6 || []) out.p6.push(await guard(() => p6(visitor, job, vbase)));
    for (const job of spec.p3 || []) out.p3.push(await guard(() => p3(browser, job, vpages, owner)));
    for (const job of spec.p3b || []) out.p3b.push(await guard(() => p3b(browser, job, baseOf(owner))));
    for (const job of spec.p4 || []) out.p4.push(await reach(p4, job));
    for (const job of spec.p5 || []) out.p5.push(await reach(p5, job));
    for (const job of spec.p7 || []) out.p7.push(await guard(() => p7(browser, job, vbase, baseOf(owner))));
    for (const job of spec.p11 || []) out.p11.push(await reach(p11, job));
    for (const job of spec.p9 || []) out.p9.push(await guard(() => (owner && owner.ok && owner.pages
      ? p9(owner.context, job, owner.pages.filter((p) => !p.tab), spec.owner.user) : p9(visitor, job, vbase, ''))));
    // P8 and P10 write records: last, so that no other probe sees them
    for (const job of spec.p8 || []) {
      if (owner && owner.ok && owner.pages) await guard(widen);
      out.p8.push(await guard(() => p8(owner, job)));
    }
    for (const job of spec.p10 || []) {
      if (owner && owner.ok && owner.pages) await guard(widen);
      out.p10.push(await guard(() => p10(browser, job, vpages, owner)));
    }
    // P12 edits a record it creates itself: after every other probe
    for (const job of spec.p12 || []) {
      if (owner && owner.ok && owner.pages) await guard(widen);
      out.p12.push(await guard(() => p12(browser, job, owner)));
    }
    // P13 reads only, but runs last so that it never takes time from the probes before it; late in the run (the whole
    // Node run is killed at TIMEOUT_S) it skips -- logged, never guessed
    for (const job of spec.p13 || []) {
      if (Date.now() - t0 > Number(spec.lateMs || 170000)) { out.p13.push({ id: job.id, skip: 'late in the run' }); continue; }
      out.p13.push(await reach(p13, job));
    }
    if (owner) await owner.context.close().catch(() => {});
  } catch (error) {
    out.error = String(error).slice(0, 300);
  }
  out.ms = Date.now() - t0;
  await browser.close().catch(() => {});
  console.log('ARC_IPROBE_JSON' + JSON.stringify(out));
})().catch((error) => { console.log('ARC_IPROBE_ERROR' + String(error).slice(0, 300)); process.exit(2); });
"""

def spec_of(jobs: dict[str, Any], snapshot: list[dict[str, Any]] | None) -> dict[str, Any]:
    hints = []
    for entry in snapshot or []:
        route = str((entry or {}).get("route") or "")
        if route.startswith("/") and ":" not in route and "*" not in route and not (entry or {}).get("error"):
            hints.append(route)
    return {
        "budgetMs": int(os.environ.get("ARC_IPROBE_BUDGET_MS", BUDGET_MS)),
        "crawlMs": int(os.environ.get("ARC_IPROBE_CRAWL_MS", CRAWL_MS)),
        "maxPages": int(os.environ.get("ARC_IPROBE_PAGES", CRAWL_PAGES)),
        "hints": list(dict.fromkeys(hints))[:8], "owner": jobs.get("owner"), "tabs": jobs.get("tabs") or [],
        "lateMs": max(0, TIMEOUT_S - 70) * 1000,
        **{k: jobs.get(k) or [] for k in VIEW_KINDS},
    }

def run_probe(backend: Path, base: str, spec: dict[str, Any], log=print) -> dict[str, Any] | None:
    from .fsutil import run_cmd

    tmp = Path(tempfile.mkdtemp(prefix="arc-iprobe-"))
    try:
        (tmp / "probe.cjs").write_text(PROBE_SCRIPT, encoding="utf-8")
        (tmp / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        _code, output = run_cmd(["node", str(tmp / "probe.cjs"), base, str(tmp / "spec.json")], cwd=backend,
                                timeout=TIMEOUT_S, env={"NODE_PATH": str(backend / "node_modules")})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    match = re.search(r"ARC_IPROBE_JSON(.*)", output)
    if not match:
        log(f"[audit] interaction probes unavailable ({output.strip()[-200:]!r})")
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

PORT_RANGE = (20000, 44999)

def own_port() -> int:
    import random
    import socket

    rng = random.SystemRandom()
    for _ in range(40):
        port = rng.randint(*PORT_RANGE)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    from .fsutil import free_port

    return free_port(3500)

BIND_FAILED = re.compile(r"EADDRINUSE|address already in use|EACCES[^\n]{0,80}\b(?:listen|bind)\b", re.I)

def own_server_answered(alive: bool, output: str) -> bool:
    return bool(alive) and not BIND_FAILED.search(output or "")

def probe(project_dir: Path, spec: dict[str, Any], log=print) -> dict[str, Any] | None:
    project_dir = Path(project_dir).resolve()
    backend = project_dir / "backend"
    if not (backend / "node_modules" / "@playwright" / "test").is_dir():
        return None
    from .fsutil import BackgroundProcess, fresh_data_env, npm_bin, wait_http

    port = own_port()
    server = BackgroundProcess([npm_bin("npm"), "run", "start"], cwd=backend, env={"PORT": str(port), **fresh_data_env(project_dir)})
    try:
        ready, _ = wait_http(f"http://127.0.0.1:{port}/", timeout=60)
        if not ready:
            log("[audit] interaction probes: application did not start")
            return None
        if not own_server_answered(server.alive(), server.output_so_far()):
            log(f"[audit] interaction probes not run: port {port} answered, but the server started for them is gone "
                f"({server.output_so_far().strip()[-160:]!r})")
            return None
        result = run_probe(backend, f"http://127.0.0.1:{port}", spec, log=log)

        if result is not None and not own_server_answered(server.alive(), server.output_so_far()):
            log(f"[audit] interaction probes dropped: the server started for them exited while another application answered "
                f"on port {port} ({server.output_so_far().strip()[-160:]!r})")
            return None
        return result
    finally:
        server.stop()

def _f(where: str, requirement: str, problem: str, fix: str) -> dict[str, str]:
    return {"where": where, "requirement": requirement, "problem": problem, "fix": fix}

def _on(res: dict[str, Any]) -> str:
    user = str((res or {}).get("signedInAs") or "")
    return f"On {res.get('route')}" + (f" (signed in as `{user}`)" if user else "")

def _target_text(target: dict[str, str]) -> str:
    role = {"menuitem": "menu", "option": "option list"}.get(target["role"], target["role"])
    return f"the {role} “{target['name']}”" if target.get("name") else f"its {role}"

def p1_findings(jobs: list[dict[str, Any]], results: list[dict[str, Any]], log=print) -> list[dict[str, str]]:
    out = []
    by_id = {j["id"]: j for j in jobs}
    for res in results or []:
        job = by_id.get(str((res or {}).get("id")))
        if not job or res.get("error") or res.get("skip"):
            log(f"[audit] (diagnostic, not reported) interaction P1 {json.dumps(res)[:300]}")
            continue
        opener = res.get("openerName") or job["opener_name"]
        target = _target_text(job["target"])
        facts, fixes = [], []
        if res.get("onLoad") and not res.get("twin") and job["target"].get("name"):
            facts.append(f"{target} is already shown when the page loads, before anyone activates “{opener}”")
            fixes.append(f"keep an `open` state that starts false and render {target} only while it is true; “{opener}” sets it")
        if job.get("escape") and res.get("escapeClosed") is False:
            facts.append(f"pressing Escape (with the focus on the page, then inside {target}) leaves it open")
            fixes.append("while open, a keydown listener on document closes it on Escape (and returns focus to the opener)")
        if job.get("outside") and res.get("outsideClosed") is False:
            facts.append("a click outside it (on empty page space) leaves it open")
            fixes.append("while open, a mousedown listener on document closes it when the target is outside the opener and the panel")

        if job.get("enter") and res.get("enterClosed") is False and res.get("enterWrites") == 0 and res.get("buttonClosed") is True:
            facts.append(f"pressing Enter in its prefilled text box does nothing, while “{job['submit']}” with the same value submits it")
            fixes.append(f"put the fields and “{job['submit']}” in a <form onSubmit> (button type=submit) so Enter submits")
        if not facts:
            log(f"[audit] (diagnostic, not reported) interaction P1 {res.get('route')} {opener}: {json.dumps(res)[:300]}")
            continue
        said = " / ".join([job["sentence"]] + job.get("said", []))[:300]
        out.append(_f(f"route {res.get('route')}", f"(the requirement: “{opener}” opens {target}) {said}",
                      f"{_on(res)}: " + "; ".join(facts) + ".",
                      "Make it a real popover: " + "; ".join(fixes) + ". Keep every accessible name and role as it is."))
    return out[:MAX_PER_PROBE]

def p2_findings(jobs: list[dict[str, Any]], results: list[dict[str, Any]], log=print) -> list[dict[str, str]]:
    out = []
    by_id = {j["id"]: j for j in jobs}
    for res in results or []:
        job = by_id.get(str((res or {}).get("id")))
        views = [v for v in (res or {}).get("views") or [] if isinstance(v, dict)]
        texts = {v.get("sig") or v.get("text") for v in views}

        dead = [v for v in views[1:] if not v.get("anchor")]

        names = {norm(v.get("name")).casefold() for v in views}
        def other_view(v: dict[str, Any]) -> bool:
            heads = {norm(h).casefold() for h in v.get("headings") or []}
            own = norm(v.get("name")).casefold()
            return own not in heads and bool(heads & (names - {own}))
        clear = (job and not res.get("error") and len(views) >= 2 and all(v.get("samePage") for v in views)
                 and dead and len(texts) == 1 and all(v.get("length") for v in views) and all(other_view(v) for v in dead))
        if not clear:
            log(f"[audit] (diagnostic, not reported) interaction P2 {json.dumps({k: res.get(k) for k in ('id', 'route', 'skip', 'error')})} "
                f"views={[(v.get('name'), v.get('hash'), v.get('samePage'), v.get('length')) for v in views]}")
            continue
        shown = next((h for h in dead[-1].get("headings") or [] if norm(h).casefold() in names), views[0]["name"])
        names = ", ".join(f"“{v['name']}”" for v in views)
        hashes = ", ".join(v.get("hash") or "(unchanged)" for v in views)
        out.append(_f(f"route {res.get('route')}", f"(the requirement: these entries switch between views that show different content) {job['sentence']}",
                      f"{_on(res)} activating {names} one after another changes only the address ({hashes}); the "
                      f"content outside the entries stays exactly the same text after every click, and no element exists "
                      f"for the anchor of {', '.join('“' + v['name'] + '”' for v in dead)}. Whoever opens "
                      f"“{dead[-1]['name']}” still sees the view of “{shown}” (its heading “{shown}” is shown, none named "
                      f"“{dead[-1]['name']}”).",
                      "Render one view per entry: derive the active view from the URL (the hash with a `hashchange` listener, or "
                      "a sub-route such as `/<page>/commits`) and render that view's own content (its heading and data) in place "

                      "of the others; keep the entries' roles and names as they are."))
    return out[:MAX_PER_PROBE]

def p3_findings(jobs: list[dict[str, Any]], results: list[dict[str, Any]], log=print) -> list[dict[str, str]]:
    out = []
    by_id = {j["id"]: j for j in jobs}
    for job in jobs:
        for skip in job.get("skipped") or []:
            log(f"[audit] (diagnostic, not reported) interaction P3 {job['id']}: `{skip['user']}` not probed as "
                f"restricted ({skip['why']})")
    for res in results or []:
        job = by_id.get(str((res or {}).get("id")))
        if not job or res.get("error"):
            log(f"[audit] (diagnostic, not reported) interaction P3 {json.dumps(res)[:300]}")
            continue
        allowed = ", ".join(job["allowed"])
        seen = res.get("visitor") or []
        if seen:
            shown = ", ".join(f"the {s['role']} “{s['name']}”" for s in seen)
            out.append(_f(f"route {seen[0]['route']}", f"(the requirement: only {job['who']} may do this) {job['sentence']}",
                          f"A signed-out visitor (fresh session, the page offers “Sign in”) sees {shown} enabled on {seen[0]['route']}.",
                          f"Render these controls only when the signed-in user has the stated role ({allowed}); a signed-out visitor "
                          "gets no control (at most a “Sign in to …” link). Keep the server-side check as well."))
        for acc in res.get("accounts") or []:
            if acc.get("signedIn") and acc.get("seen"):
                s = acc["seen"][0]
                out.append(_f(f"route {s['route']}", f"(the requirement: only {job['who']} may do this) {job['sentence']}",
                              f"Signed in as `{acc['user']}` (stated role: {', '.join(acc.get('roles') or [])}) the {s['role']} "
                              f"“{s['name']}” is shown enabled on {s['route']}.",
                              f"Decide from the signed-in user's effective role on this record: render the control only for {allowed}."))
        if not seen and not any(a.get("seen") for a in res.get("accounts") or []):
            log(f"[audit] (diagnostic, not reported) interaction P3 {job['id']}: {json.dumps(res)[:300]}")
    return out[:MAX_PER_PROBE]

def p3b_findings(jobs: list[dict[str, Any]], results: list[dict[str, Any]], log=print) -> list[dict[str, str]]:
    out = []
    by_id = {j["id"]: j for j in jobs}
    for res in results or []:
        job = by_id.get(str((res or {}).get("id")))
        root = (res or {}).get("ownerRoot") or {}
        vroot = (res or {}).get("visitorRoot") or {}

        leaks = [s for s in (res or {}).get("subs") or [] if s.get("ownerOk") and not s.get("visitorDenied") and s.get("visitorHasName")
                 and int(s.get("shared") or 0) >= 2 and 3 * int(s.get("shared") or 0) >= int(s.get("ownLines") or 0)]
        if not job or res.get("error") or not (root.get("hasName") and not root.get("denied") and vroot.get("denied")) or not leaks:
            log(f"[audit] (diagnostic, not reported) interaction P3b {json.dumps(res)[:400]}")
            continue
        paths = ", ".join(s["path"] for s in leaks[:3])
        out.append(_f(f"route {leaks[0]['path']}", f"(the requirement: a private {job['kind']} is not accessible to visitors) {job['sentence']}",
                      f"A signed-out visitor is refused at {res.get('root')} (“{vroot.get('text', '')[:60]}”), but the sub-page(s) "
                      f"{paths} are not refused the same way: they show a page for the private {job['kind']} `{job['name']}` "
                      f"(its name, no refusal), e.g. “{leaks[0]['visitorText'][:100]}”, with {leaks[0]['shared']} of the "
                      f"{leaks[0]['ownLines']} lines that only the owner `{job['owner']}`'s view of {leaks[0]['path']} shows ("
                      + ", ".join(f"“{x}”" for x in leaks[0].get("sharedSample") or []) + ").",
                      f"Apply the root's access check to every page under {res.get('root')}/…: each sub-route page loads the "
                      f"{job['kind']} first and renders the same refusal when that load is refused, and the API routes of the "
                      "sub-resources check read permission too (keep them as they are where they already refuse)."))
    return out[:MAX_PER_PROBE]

_UNITS = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400, "week": 604800,
          "month": 2592000, "year": 31536000}
RELATIVE = re.compile(r"\bago\b|\bjust\s+now\b|^\s*now\s*$|\bin\s+(?:a|an|\d+)\s+[a-z]+|\b(?:a|an|\d+)\s+(?:sec|min|hr|second|"
                      r"minute|hour|day|week|month|year)s?\b|\b(?:yesterday|today|tomorrow)\b", re.I)

def relative_age(value: Any) -> float | None:
    text = norm(value).lower()
    if re.fullmatch(r"(?:updated\s+)?(?:just\s+now|now|a\s+moment\s+ago|moments\s+ago)", text):
        return 0.0
    match = re.fullmatch(r"(?:updated\s+)?(?P<n>a|an|one|\d+)\s+(?P<u>sec|min|hr|second|minute|hour|day|week|month|year)s?\s+ago", text)
    if not match:
        return None
    n = 1 if match.group("n") in ("a", "an", "one") else int(match.group("n"))
    return float(n * _UNITS[match.group("u")])

def _fresher_after_reload(v1: Any, v2: Any) -> bool:
    if not (RELATIVE.search(norm(v1)) or RELATIVE.search(norm(v2))):
        return v2 != v1
    a1, a2 = relative_age(v1), relative_age(v2)
    return a1 is not None and a2 is not None and a2 < a1

def p4_findings(jobs: list[dict[str, Any]], results: list[dict[str, Any]], log=print) -> list[dict[str, str]]:
    out = []
    by_id = {j["id"]: j for j in jobs}
    for res in results or []:
        job = by_id.get(str((res or {}).get("id")))
        if (not job or res.get("error") or not res.get("persisted") or res.get("v1") != res.get("v0") or res.get("v2") in (None, res.get("v0"))
                or not _fresher_after_reload(res.get("v1"), res.get("v2"))):
            log(f"[audit] (diagnostic, not reported) interaction P4 {json.dumps(res)[:300]}")
            continue
        label = job["label"]
        out.append(_f(f"route {res.get('route')}", f"(the requirement: the page displays “{label}: …” of the record) {job['sentence']}",
                      f"{_on(res)}, after a saved write the page still shows the old value: the probe typed a value into the cell "
                      f"“{res.get('cell')}” and pressed Enter (it was saved: still there after a reload), yet “{label}: "
                      f"{res.get('v0')}” did not change; only the reload showed “{label}: {res.get('v2')}”.",
                      f"After every successful write, set the displayed record from the server's response (or refetch it) so "
                      f"“{label}” shows the new value at once, without a reload."))
    return out[:MAX_PER_PROBE]

def p5_findings(jobs: list[dict[str, Any]], results: list[dict[str, Any]], log=print) -> list[dict[str, str]]:
    out = []
    by_id = {j["id"]: j for j in jobs}
    for res in results or []:
        job = by_id.get(str((res or {}).get("id")))
        if not job or res.get("error"):
            log(f"[audit] (diagnostic, not reported) interaction P5 {json.dumps(res)[:300]}")
            continue
        field = job["field"] or job["field_re"].strip("^$").replace("\\", "")
        thing = job["thing"]
        click = res.get("click") or {}
        enter = res.get("enter") or {}
        reported = False
        if click.get("typed") and click.get("persisted") is False and enter.get("persisted") is True:
            reported = True
            out.append(_f(f"route {res.get('route')}", f"(the requirement: content typed in the {field} is committed by clicking another {thing}) {job['sentence']}",
                          f"{_on(res)}, a typed value is lost: the probe selected the {thing} “{click.get('cell')}”, typed a value into the text box "
                          f"“{field}” (it showed the value), then clicked the middle of the {thing} “{click.get('other')}”; after "
                          f"the requests settled and a reload “{click.get('cell')}” did not hold it"
                          + (" (it was already gone before the reload)" if click.get("afterClick") is False else "")
                          + f". The same typing committed with Enter (“{enter.get('cell')}”) was kept.",
                          f"Commit the {field} draft to the selected {thing} when the mouse goes down on another {thing} (before "
                          f"the selection changes, e.g. a capture-phase onMouseDown on the grid that commits a pending draft "
                          f"first) and on the {field}'s blur; Enter and Escape keep working as they do."))
        esc = res.get("escape") or {}
        if job.get("escape") and esc.get("typed") and esc.get("persisted") is True:
            reported = True
            out.append(_f(f"route {res.get('route')}", f"(the requirement: Escape cancels an uncommitted change) {job['escape_sentence']}",
                          f"{_on(res)}, Escape in the {field} did not cancel: the probe selected “{esc.get('cell')}”, typed a value into "
                          f"“{field}”, pressed Escape and reloaded -- the value was saved.",
                          f"On Escape in the {field}: discard the draft, show the selected {thing}'s committed value again and "
                          "make the blur that follows NOT save."))
        if not reported:
            log(f"[audit] (diagnostic, not reported) interaction P5 {json.dumps(res)[:300]}")
    return out[:MAX_PER_PROBE]

PUBLIC_BADGE = re.compile(r"(?:visibility:\s*)?public", re.I)

def _scope_segments(path: str) -> list[str]:
    return [s for s in str(path or "").split("#")[0].split("?")[0].split("/") if s][:-1]

def _names_scope(item: dict[str, Any], partner: dict[str, Any]) -> str:
    texts = [norm(t) for t in list(item.get("texts") or []) + list(item.get("facts") or [])]
    mine = _scope_segments(item.get("path", ""))
    theirs = [s for s in _scope_segments(partner.get("path", "")) if s not in mine]
    said = lambda seg: any(re.search(r"(?<![\w-])" + re.escape(seg) + r"(?![\w-])", t, re.I) for t in texts)
    if any(said(s) for s in mine if s not in _scope_segments(partner.get("path", ""))):
        return ""
    return next((s for s in theirs if said(s)), "")

def p6_findings(jobs: list[dict[str, Any]], results: list[dict[str, Any]], log=print) -> list[dict[str, str]]:
    out = []
    by_id = {j["id"]: j for j in jobs}
    for res in results or []:
        job = by_id.get(str((res or {}).get("id")))
        if not job or res.get("error"):
            log(f"[audit] (diagnostic, not reported) interaction P6 {json.dumps(res)[:300]}")
            continue
        for item in res.get("items") or []:
            target = item.get("target") or {}
            facts = item.get("facts") or []
            good = [p for p in item.get("partners") or [] if not p.get("denied") and p.get("hasName")
                    and (not facts or p.get("facts") == len(facts))]
            shows = f" (the item shows “{facts[0][:60]}”)" if facts else ""

            named = next(((p, seg) for p in good for seg in [_names_scope(item, p)] if seg), None)
            public = any(PUBLIC_BADGE.fullmatch(norm(t)) for t in item.get("texts") or [])
            if target.get("denied") and good and (public or named):
                partner = named[0] if named else good[0]
                because = f"the item names “{named[1]}”" if named else "the item marks it public"
                problem = (f"The list on {item['route']} shows the {job['noun']} “{item['name']}”{shows}, but its link opens "
                           f"{item['path']}, which refuses the same visitor (“{target.get('first', '')[:60]}”), while {because}. "
                           f"The {job['noun']} named “{item['name']}” that this visitor can open is {partner['path']}: the item "
                           f"shows one record and links to another one with the same name in a different owner/scope.")
            elif not target.get("denied") and facts and target.get("facts") == 0 and named:
                problem = (f"The list on {item['route']} shows the {job['noun']} “{item['name']}”{shows} and names "
                           f"“{named[1]}”, but its link opens {item['path']}, which shows none of that; {named[0]['path']} is the "
                           f"{job['noun']} the item shows.")
            else:
                continue
            out.append(_f(f"route {item['route']}", f"(the requirement: the list shows the {job['noun']}s of this {job['scope']} and "
                          f"each name opens that {job['noun']}) {job['sentence']}", problem,
                          f"Build each item's link from the same record the item displays (its own owner/{job['scope']} "
                          f"identifier + name), never by looking the name up again; and list only {job['noun']}s of this "
                          f"{job['scope']} that the viewer may open."))
        if not out:
            log(f"[audit] (diagnostic, not reported) interaction P6 {json.dumps(res)[:400]}")
    return out[:MAX_PER_PROBE]

def p7_findings(jobs: list[dict[str, Any]], results: list[dict[str, Any]], log=print) -> list[dict[str, str]]:
    out = []
    by_id = {j["id"]: j for j in jobs}
    for res in results or []:
        job = by_id.get(str((res or {}).get("id")))
        root = (res or {}).get("ownerRoot") or {}
        vroot = (res or {}).get("visitorRoot") or {}
        pages = [p for p in (res or {}).get("pages") or [] if isinstance(p, dict)]
        apis = [a for a in (res or {}).get("apis") or [] if isinstance(a, dict)]
        refused = root.get("hasName") and not root.get("denied") and vroot.get("denied")
        if not job or res.get("error") or not refused or not (pages or apis):
            log(f"[audit] (diagnostic, not reported) interaction P7 {json.dumps(res)[:400]}")
            continue
        facts = []
        for p in pages[:2]:
            shown = []
            if p.get("links"):
                shown.append(f"a link “{p['links'][0]['name']}” to {p['links'][0]['path']}")
            if p.get("snippet"):
                shown.append(f"the text “{p['snippet'][:120]}”")
            elif p.get("named") and not p.get("links"):
                shown.append(f"the {p['named'][0]}")
            facts.append(f"{p['path']} shows " + " and ".join(shown))
        for a in apis[:1]:
            facts.append(f"GET {a['api']} (fetched by {a['page']}) returns it in its list ({json.dumps(a['items'][0], ensure_ascii=False)[:140]})")
        where = pages[0]["path"] if pages else apis[0]["page"]
        out.append(_f(f"route {where}", f"(the requirement: a private {job['kind']} is not accessible to visitors) {job['sentence']}",
                      f"A signed-out visitor is refused at the private {job['kind']}'s own page {res.get('root')} "
                      f"(“{vroot.get('text', '')[:60]}”), yet its name `{job['name']}` is listed to the same visitor: "
                      + "; ".join(facts) + ". The pages counted here do not carry the name in their address.",
                      f"Apply the same read rule as {res.get('root')} to every list and search that names {job['kind']} "
                      f"records (the page and the API it calls): filter out private {job['kind']} records the current "
                      "viewer may not open, on the server; the owner and authorised accounts keep seeing them."))
    return out[:MAX_PER_PROBE]

def p8_findings(jobs: list[dict[str, Any]], results: list[dict[str, Any]], log=print) -> list[dict[str, str]]:
    out = []
    by_id = {j["id"]: j for j in jobs}
    for res in results or []:
        job = by_id.get(str((res or {}).get("id")))
        ok = [s for s in (res or {}).get("writes") or [] if isinstance(s, int) and 200 <= s < 300]
        best: dict[str, Any] = {}
        for item in (res or {}).get("lists") or []:
            json_items = max([int(j.get("items") or 0) for j in item.get("json") or [] if isinstance(j, dict)] or [0])

            count = json_items if item.get("json") else int(item.get("links") or 0)
            if count > int(best.get("count") or 0):
                best = {"count": count, "path": item.get("path"), "json": json_items, "links": item.get("links")}
        if not job or res.get("error") or res.get("skip") or len(ok) < 2 or int(best.get("count") or 0) < 2:
            log(f"[audit] (diagnostic, not reported) interaction P8 {json.dumps(res)[:400]}")
            continue
        seen = (f"its API list holds {best['json']} records with that value" if best.get("json") else
                f"{best['links']} links named with that value")
        out.append(_f(f"route {res.get('route')}", f"(the requirement: submitting the form creates the record) {job['sentence']}",
                      f"{_on(res)}, the probe typed a unique value into "
                      f"“{job['field']}” and double-clicked “{job['submit']}” once: {len(ok)} write requests carrying the "
                      f"value were accepted ({', '.join(str(s) for s in ok)}), and {best['path']} then shows {seen} -- one "
                      "double-click created two records.",
                      f"Make one submission create one record: disable “{job['submit']}” and ignore further submits while "
                      "the request is pending (a submitting flag checked at the top of the submit handler, reset when the "
                      "request ends), and on the server allocate numbers/identifiers atomically (one transaction, a unique "
                      "constraint) so that two concurrent requests cannot share one."))
    return out[:MAX_PER_PROBE]

def p9_findings(jobs: list[dict[str, Any]], results: list[dict[str, Any]], log=print) -> list[dict[str, str]]:
    out = []
    by_id = {j["id"]: j for j in jobs}
    for res in results or []:
        job = by_id.get(str((res or {}).get("id")))
        if not job or res.get("error"):
            log(f"[audit] (diagnostic, not reported) interaction P9 {json.dumps(res)[:400]}")
            continue
        user = str(res.get("signedInAs") or "")
        who = f"signed in as `{user}`" if user else "in a fresh browser session"
        top = res.get("top") or {}
        reported = False
        if top.get("blank"):
            reported = True
            out.append(_f(f"route {top.get('path')}", f"(the requirement: pages are opened by their address) {job['sentence']}",
                          f"Opening an address that names no page ({top.get('path')}, {who}) shows a completely blank "
                          f"page: no text at all -- no heading, no navigation, no message, no way back (HTTP {top.get('status')}).",
                          "Add a catch-all route (path `*`, last) inside the normal layout that renders a visible not-found "
                          "state, e.g. a heading “Page not found” and a link to the home page."))
        for rec in res.get("records") or []:
            if not isinstance(rec, dict) or rec.get("errorText") or rec.get("realHasErrorWords") or rec.get("finalPath") != rec.get("path"):
                continue
            own, shared = int(rec.get("ownControls") or 0), int(rec.get("sharedControls") or 0)
            if rec.get("blank"):
                problem = (f"{rec['path']} -- the address pattern of {rec['real']} with an identifier that names no record -- "
                           f"shows a completely blank page ({who}).")
            elif shared >= 2 and shared * 10 >= own * 6:
                controls = ", ".join("the " + c.replace("|", " “", 1) + "”" for c in rec.get("shared") or [] if not c.startswith("link|"))
                problem = (f"{rec['path']} -- the address pattern of {rec['real']} with an identifier that names no record -- "
                           f"renders that record page's controls ({controls}) and no not-found or error text ({who}); "
                           + (f"its data requests failed ({', '.join(rec.get('failed') or [])}) " if rec.get("failed") else "")
                           + f"the page reads “{str(rec.get('text') or '')[:100]}”.")
            else:
                continue
            reported = True
            out.append(_f(f"route {rec['path']}", f"(the requirement: records are opened by their address) {job['sentence']}",
                          problem,
                          "When the record named by the address cannot be loaded (the API answers 404 or fails), render a "
                          "not-found state instead of the record page: a message such as “<Thing> not found” and a link "
                          "back, without the record's tabs and forms; the page of an existing record stays as it is."))
        if not reported:
            log(f"[audit] (diagnostic, not reported) interaction P9 {json.dumps(res)[:500]}")
    return out[:MAX_PER_PROBE]

def p10_findings(jobs: list[dict[str, Any]], results: list[dict[str, Any]], log=print) -> list[dict[str, str]]:
    out = []
    by_id = {j["id"]: j for j in jobs}
    for job in jobs:
        for skip in job.get("skipped") or []:
            log(f"[audit] (diagnostic, not reported) interaction P10 {job['id']}: `{skip['user']}` not probed as "
                f"restricted ({skip['why']})")
    for res in results or []:
        job = by_id.get(str((res or {}).get("id")))
        if not job or res.get("error"):
            log(f"[audit] (diagnostic, not reported) interaction P10 {json.dumps(res)[:300]}")
            continue
        allowed = ", ".join(job["allowed"])
        reported = False
        for acc in res.get("accounts") or []:

            if not (acc.get("signedIn") and acc.get("tookEffect")) or acc.get("replaySkip") or acc.get("authOwn") is False:
                continue
            roles = ", ".join(acc.get("roles") or [])
            if acc.get("controlHidden") and acc.get("via") == "replay":
                problem = (f"Signed in as `{acc['user']}` (stated role: {roles}) the control for this act is correctly not "
                           f"shown, yet replaying the very write request the allowed UI sends -- with `{acc['user']}`'s own "
                           f"session (its cookies, a fresh unique value in “{job['field']}”, no other account's credentials) -- "
                           f"was accepted (HTTP {acc.get('status')}) and the value is visible after a reload of {res.get('route')}. "
                           "The server does not check the role for this write.")
            elif acc.get("via") == "ui":
                problem = (f"Signed in as `{acc['user']}` (stated role: {roles}) the act was performed through the control the "
                           f"page still shows: a unique value typed into “{job['field']}”, then “{job['submit']}”. The write was "
                           f"accepted (HTTP {acc.get('status')}) and the value is visible after a reload of {res.get('route')} -- "
                           "neither the UI nor the server refuses this role.")
            else:
                continue
            reported = True
            out.append(_f(f"route {res.get('route')}", f"(the requirement: only {job['who']} may do this) {job['sentence']}",
                          problem,
                          f"Enforce the stated role on the server for this write: reject it unless the signed-in user's effective "
                          f"role is one of {allowed} (compute the effective role the same way everywhere, group grants included); "
                          f"return the stated refusal. Keep the control visible only to {allowed}."))
        if not reported:
            log(f"[audit] (diagnostic, not reported) interaction P10 {json.dumps(res)[:400]}")
    return out[:MAX_PER_PROBE]

def _key(path: Any) -> str:
    return str(path or "").split("#")[0].split("?")[0]

def p11_findings(jobs: list[dict[str, Any]], results: list[dict[str, Any]], log=print) -> list[dict[str, str]]:
    out = []
    by_id = {j["id"]: j for j in jobs}
    for res in results or []:
        job = by_id.get(str((res or {}).get("id")))
        rows = [r for r in (res or {}).get("rows") or [] if isinstance(r, dict) and not r.get("skip")]

        bad = [r for r in rows if not r.get("activatable") and not r.get("numberDialog")
               and _key(r.get("numberPath")) == _key(r.get("route")) and r.get("titlePath")
               and _key(r.get("titlePath")) != _key(r.get("route")) and not r.get("titleDenied")
               and r.get("titleLanded") is not False and r.get("targetShowsTitle") is not False]
        if not job or res.get("error") or not bad:
            log(f"[audit] (diagnostic, not reported) interaction P11 {json.dumps(res)[:400]}")
            continue
        r = bad[0]
        number = "number" if "number" in job["fields"] else "id"
        out.append(_f(f"route {r['route']}", f"(the requirement: clicking the {' or the '.join(job['fields'])} opens "
                      f"{job['target']}) {job['sentence']}",
                      f"{_on(dict(res, route=r['route']))}, the list row “{r.get('rowText', '')[:100]}” shows the {number} "
                      f"“{r.get('num')}” as plain text: it is no link or button, and clicking it leaves the page at "
                      f"{r['route']} (no dialog either). Clicking the row's title “{r.get('title')}” opens "
                      f"{r.get('titlePath')}.",
                      f"Make the {number} open the same page as the title: render “{r.get('num')}” as a link to the "
                      f"record's page (the title's own address, e.g. {r.get('href')}), with the {number} as its text; keep "
                      "the title link and every other name as they are."))
    return out[:MAX_PER_PROBE]

AUTH_REFUSED = {401, 403}
VALIDATION_REFUSED = {400, 409, 422}

def p12_findings(jobs: list[dict[str, Any]], results: list[dict[str, Any]], log=print) -> list[dict[str, str]]:
    out = []
    by_id = {j["id"]: j for j in jobs}
    for res in results or []:
        job = by_id.get(str((res or {}).get("id")))
        statuses = [s for s in (res or {}).get("saveStatus") or [] if isinstance(s, int)]
        failed = [s for s in statuses if s >= 400 or s == 0]
        errors = [str(e) for e in (res or {}).get("errorLines") or []]
        reloaded = [r for r in (res or {}).get("reloaded") or [] if isinstance(r, dict)]

        old_back = any(r.get("hasOld") for r in reloaded) and not any(r.get("hasNew") for r in reloaded)
        clear = (job and not res.get("error") and not res.get("skip") and res.get("created") and res.get("own")
                 and res.get("typed") and res.get("valueValid") is not False and statuses and res.get("persisted") is False and old_back
                 and (failed or errors or res.get("shownAfterSave")))

        if clear and any(s in VALIDATION_REFUSED for s in failed):
            log(f"[audit] (diagnostic, not reported) interaction P12 save answered {failed} (a validation refusal of the probe's value): {json.dumps(res)[:300]}")
            continue
        if clear and failed and all(s in AUTH_REFUSED for s in failed) and not job.get("roleProven"):
            log(f"[audit] (diagnostic, not reported) interaction P12 refused {failed} for an account whose role is not stated: {json.dumps(res)[:300]}")
            continue
        if not clear:
            log(f"[audit] (diagnostic, not reported) interaction P12 {json.dumps(res)[:500]}")
            continue
        opener = f"“{job['opener']}”" if job.get("opener") else "the Edit control of its own row"
        seen = []
        if failed:
            seen.append(f"the save request was answered HTTP {', '.join(str(s) for s in failed)}")
        if errors:
            seen.append(f"the page showed “{errors[0][:100]}”")
        if res.get("shownAfterSave") and not failed and not errors:
            seen.append("the page showed the new value")
        where = ", ".join(r.get("path", "") for r in res.get("reloaded") or [])
        out.append(_f(f"route {res.get('route')}", f"(the requirement: an existing {job['thing']} is edited and saved with "
                      f"“{job['save']}”) {job['sentence']}",
                      f"Signed in as `{job['account']['user']}`, the probe created its own {job['thing']} through the stated "
                      f"create flow (“{res.get('value1')}” in “{(job.get('creates') or [{}])[0].get('field', job['field'])}”, "
                      f"accepted), opened its edit form with {opener} (prefilled with that {job['thing']}), changed "
                      f"“{job['field']}” to “{res.get('value2')}” and activated “{job['save']}”: " + " and ".join(seen)
                      + f"; after a reload ({where}) the {job['thing']} shows its old value “{res.get('value1')}” and not the "
                      "new one -- the edit was not saved "
                      + ("(the new value was made for that field and passed the browser's own validity check)."
                         if res.get("sameField") is False else
                         "(the new value has the same form as the one the create accepted)."),
                      f"Make saving an existing {job['thing']} work end to end: the request the form sends on “{job['save']}” "
                      "must reach an update route that exists on the server with the same method and path (check both "
                      "sides: method, path, record identifier, field names), validate like the create, persist the change "
                      "and answer with the saved record; after success show the saved values; keep the create path as it is."))
    return out[:MAX_PER_PROBE]

def p13_findings(jobs: list[dict[str, Any]], results: list[dict[str, Any]], log=print) -> list[dict[str, str]]:
    out = []
    by_id = {j["id"]: j for j in jobs}
    for res in results or []:
        job = by_id.get(str((res or {}).get("id")))
        tried = [t for t in (res or {}).get("tried") or [] if isinstance(t, dict)]
        default = (res or {}).get("defaultView") or {}
        bad = [t for t in tried if t.get("changed") and isinstance(t.get("reload"), dict) and t["reload"].get("sameAsDefault")
               and not t["reload"].get("sameAsView") and t.get("name") not in (t["reload"].get("active") or [])
               and default.get("length")]
        if not job or res.get("error") or not bad:
            log(f"[audit] (diagnostic, not reported) interaction P13 {json.dumps(res)[:500]}")
            continue
        t = bad[0]
        kept = t.get("url") != default.get("url")
        heads = lambda v: ", ".join(f"“{h}”" for h in (v.get("headings") or [])[:3]) or "(no heading)"
        own = ", ".join(t.get("ownText") or [])
        requirement = ("(convention: a view the user switched to -- a tab, a filter -- is reflected in the URL and a reload "
                       f"shows the same view) {job['sentence']}" + (f" / {job['refresh']}" if job.get("refresh") else ""))
        out.append(_f(f"route {res.get('route')}", requirement,
                      f"{_on(res)} activating “{t['name']}” shows its view (address {t.get('url')}, headings {heads(t)}"
                      + (f"; it shows {own}" if own else "") + "). "
                      + (f"The address changed to {t.get('url')}, but reloading it" if kept else
                         f"The address stays {t.get('url')}, and reloading it")
                      + f" shows the default view again: the page text is exactly the text shown on load (headings "
                      f"{heads(t['reload'])}), and “{t['name']}”'s view is gone"
                      + (f" (the active entry is “{t['reload']['active'][0]}”)" if t["reload"].get("active") else "") + ".",
                      f"Make the “{t['name']}” view addressable: activating the entry puts the view in the address (a "
                      "query parameter such as `?tab=<entry>` / `?state=<value>`, or a sub-route of the page) and the "
                      "page derives the active view from the address on load, so a reload or an opened link shows the "
                      "same view; keep the entries' roles, names and the default view as they are."))
    return out[:MAX_PER_PROBE]

TAB_ROUTE = re.compile(r"#arc-tab=([^\s,;)]+)")

def _tab_route_text(text: str) -> str:
    from urllib.parse import unquote
    return TAB_ROUTE.sub(lambda m: f" (after activating its “{unquote(m.group(1))}” entry)", str(text))

def findings_from(jobs: dict[str, Any], result: dict[str, Any] | None, log=print) -> list[dict[str, str]]:
    if not isinstance(result, dict):
        return []
    found: list[dict[str, str]] = []
    for kind, convert in (("p1", p1_findings), ("p2", p2_findings), ("p3", p3_findings), ("p3b", p3b_findings),
                          ("p4", p4_findings), ("p5", p5_findings), ("p6", p6_findings), ("p7", p7_findings),
                          ("p8", p8_findings), ("p9", p9_findings), ("p10", p10_findings), ("p11", p11_findings),
                          ("p12", p12_findings), ("p13", p13_findings)):
        try:
            found += convert(jobs.get(kind) or [], result.get(kind) or [], log=log)
        except Exception as exc:
            log(f"[audit] interaction {kind} findings crashed: {exc!r}")
    return [{k: _tab_route_text(v) if isinstance(v, str) else v for k, v in f.items()} for f in found]

_CACHE: dict[str, list[dict[str, str]]] = {}
_TRIES: dict[str, int] = {}
INCOMPLETE_TRIES = 2

FINGERPRINT_DIRS = ("frontend/src", "backend/src", "frontend/dist", "frontend/public")

FINGERPRINT_FILES = re.compile(r"^(?:package(?:-lock)?\.json|index\.html|tsconfig[\w.-]*\.json|[\w-]+\.config\.[cm]?[jt]s|\.env[\w.-]*|\.npmrc)$")

def source_fingerprint(project_dir: Path) -> str:
    digest = hashlib.sha1()
    files: list[Path] = []
    for sub in FINGERPRINT_DIRS:
        base = project_dir / sub
        if base.is_dir():
            files += [p for p in base.rglob("*") if p.is_file()]
    for sub in ("frontend", "backend"):
        base = project_dir / sub
        if base.is_dir():
            files += [p for p in base.iterdir() if p.is_file() and FINGERPRINT_FILES.match(p.name)]
    for path in sorted(set(files)):
        try:
            content = hashlib.sha1(path.read_bytes()).hexdigest()
        except OSError:
            content = "unreadable"
        digest.update(f"{path.relative_to(project_dir).as_posix()}|{content}\n".encode())
    return digest.hexdigest()

def _complete(result: dict[str, Any]) -> bool:
    if result.get("error"):
        return False
    return not any(isinstance(r, dict) and (r.get("error") or r.get("budget")) for k in VIEW_KINDS for r in result.get(k) or [])

def interaction_findings(requirement_text: str, project_dir: Path, snapshot: list[dict[str, Any]] | None, log=print) -> list[dict[str, str]]:
    if not enabled():
        return []
    jobs = build_jobs(requirement_text)
    if not triggered(jobs):
        log("[audit] interaction probes: no stated interaction recognised")
        return []
    spec = spec_of(jobs, snapshot)
    key = hashlib.sha1((str(Path(project_dir).resolve()) + source_fingerprint(Path(project_dir))
                        + json.dumps(spec, sort_keys=True)).encode()).hexdigest()
    if key in _CACHE:
        return [dict(f) for f in _CACHE[key]]
    counts = {k: len(jobs.get(k) or []) for k in VIEW_KINDS}
    log(f"[audit] interaction probes: {counts}")
    result = probe(Path(project_dir), spec, log=log)
    if result is None or not _complete(result):

        _TRIES[key] = _TRIES.get(key, 0) + 1
        if _TRIES[key] >= INCOMPLETE_TRIES:
            log(f"[audit] interaction probes: incomplete {_TRIES[key]} times; this result is kept for the round")
    if result is None:
        if _TRIES.get(key, 0) >= INCOMPLETE_TRIES:
            _CACHE[key] = []
        return []
    if result.get("error"):
        log(f"[audit] interaction probes error: {result['error']}")
    crawled = (result.get("crawl") or {}).get("visitor") or []
    owner = result.get("owner") or {}
    log(f"[audit] interaction probes crawled {len(crawled)} page(s) in {result.get('ms')} ms"
        + (f"; owner `{owner.get('user')}` signed in: {owner.get('ok')}{(' (' + owner['why'] + ')') if owner.get('why') else ''}"
           if owner else "")
        + (f"; pages: {crawled}" if len(crawled) < 5 else ""))
    found = findings_from(jobs, result, log=log)
    if _complete(result) or _TRIES.get(key, 0) >= INCOMPLETE_TRIES:
        _CACHE[key] = [dict(f) for f in found]
    else:
        log("[audit] interaction probes: result not cached (an error or a probe cut short); the next call probes again")
    return found
