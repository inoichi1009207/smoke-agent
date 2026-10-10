from __future__ import annotations

import functools
import re
from typing import Any

QUOTE = re.compile(r"“([^”\n]{1,120})”|`([^`\n]{1,120})`|\"([^\"\n]{1,120})\"|‘([^’\n]{1,120})’")
PH = re.compile(r"\x01(\d+)\x02")
CELL = re.compile(r"\b([A-Z]{1,3}[1-9][0-9]{0,4})(?::([A-Z]{1,3}[1-9][0-9]{0,4}))?\b")
EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$")

ROLE_WORDS = {
    "link": "link", "links": "link", "button": "button", "buttons": "button", "tab": "tab", "tabs": "tab",
    "menuitem": "menuitem", "menu item": "menuitem", "option": "option", "options": "option", "checkbox": "checkbox",
    "textbox": "textbox", "searchbox": "searchbox", "combobox": "combobox", "field": "textbox", "radio": "radio",
    "heading": "heading", "menu": "menu", "dialog": "dialog", "row": "row", "gridcell": "gridcell", "cell": "gridcell",
}
FILLER = {"the", "a", "an", "its", "their", "that", "this", "same", "visible", "exact", "exactly", "existing", "seeded",
          "top", "first", "named", "called", "labeled", "labelled", "visibly", "matching", "unique", "displayed", "one",
          "new", "current", "only", "available", "public", "separate"}

ENTITY_KINDS = [
    ("PR", r"(?:draft\s+|open\s+|closed\s+|merged\s+|reviewable\s+|unmerged\s+|separate\s+)*(?:PRs?|pull\s+requests?)"),
    ("issue", r"(?:open\s+|closed\s+|public\s+|separate\s+)*issues?"),
    ("release", r"(?:published\s+)?(?:releases?|tags?)"),
    ("commit", r"commits?(?:\s+message)?"),
    ("filter view", r"(?:saved\s+)?filter\s+views?"),
    ("workbook", r"workbooks?"),
    ("worksheet", r"(?:active\s+)?worksheets?"),
    ("repository", r"(?:public\s+|private\s+|archived\s+|active\s+|internal\s+)*repositor(?:y|ies)"),
    ("organization", r"organi[sz]ations?(?:\s+identifier)?"),
    ("team", r"teams?"),
    ("branch", r"(?:active\s+|target\s+|default\s+)?branch(?:es)?"),
    ("label", r"(?:repository\s+)?labels?"),
    ("milestone", r"(?:repository\s+)?milestones?"),
    ("entry", None),
]
SUBJECT_RANK = ["PR", "issue", "release", "commit", "filter view", "workbook", "repository", "organization"]
COLLECTION = {"PR": {"pull requests"}, "issue": {"issues"}, "release": {"releases"}, "commit": {"commits", "history"},
              "repository": {"repositories"}, "organization": {"organizations", "your organizations"},
              "workbook": {"workbooks"}}
REFERENT_NOUNS = {
    "pr": "PR", "prs": "PR", "pull request": "PR", "draft pr": "PR", "issue": "issue", "release": "release",
    "commit": "commit", "commit entry": "commit", "repository": "repository", "archived repository": "repository",
    "workbook": "workbook", "organization": "organization", "comparison": "entry", "worksheet": "worksheet",
}
ACTOR_ROLES = {"owner", "admin", "administrator", "member", "maintainer", "maintain", "author", "reviewer", "viewer",
               "contributor", "editor", "commenter", "collaborator", "creator"}
SYSTEM_SUBJECT = re.compile(r"(?i)^\s*(?:(?:and|then|after\s+that)\s+)?(?:the\s+|a\s+|an\s+|its\s+)?(?:[\w-]+\s+){0,2}?"
                            r"(?:system|application|app|page|server|site|editor|dialog|form|workspace|overview|list|"
                            r"grid|selector|browser|repository|detail|results?|field|button|link|message|row|tab)\b[^,;]{0,30}$")
ACTOR_SUBJECT = re.compile(r"(?i)\b(?:the\s+)?(visitor|user|admin|owner|member|maintainer|author|reviewer|viewer|"
                           r"contributor|editor|commenter|collaborator|[a-z]+-[a-z]+)\b")
NEGATED = re.compile(r"(?i)\b(?:not|cannot|can't|never|no\s+longer|unable\s+to|without|nor|instead\s+of)\s+(?:\w+\s+){0,2}$")
CONDITIONAL = re.compile(r"(?i)\b(?:if|when|whenever|once)\s+(?:it|they|those|these|the|a|an|shown|available|enabled|"
                         r"visible|present|offered|displayed)\b|\bif\s+shown\b|\bif\s+enabled\b|\bif\s+available\b")

_VERB_TABLE = [
    ("start", r"starts?\s+(?=at\b|from\b|in\b|on\b|unauthenticated\b|signed\b|fresh\b)"),
    ("capability", r"can\s+(?:open|see|access|reach|view|sign\s+in)\b"),
    ("observe", r"(?:verif(?:y|ies)|confirms?\s+that|checks?\s+(?:that|whether|for|if)|views?|looks?\s+for|locates?|observes?|"
                r"waits?\s+(?:for|until)|sees?|records?|notes?\s+that|reads?|inspects?)\b"),
    ("sign_out", r"(?:signs?|logs?)\s+out\b"),
    ("sign_in", r"(?:signs?|logs?)\s+(?:back\s+)?in\b(?!\s*(?:page|link|button|form|step|control|entry))"),
    ("reload", r"(?:reloads?|refresh(?:es)?)\b|[Aa]fter\s+(?:a\s+|the\s+)?(?:page\s+)?(?:reload|refresh)\b"),
    ("home", r"(?:returns?|goes|navigates?)\s+(?:back\s+)?(?:to\s+)?(?:the\s+)?(?:application\s+)?home(?:\s+page)?\b"),
    ("back", r"(?:goes|navigates?)\s+back\b|presses\s+the\s+browser\s+back\b"),
    ("open", r"(?:re-?opens?|opens?|visits?|navigates?\s+to|goes\s+to|returns?\s+to|follows?|switch(?:es)?\s+to|"
             r"enters?\s+(?=the\s+[\w-]+\s+page\b))\b"),
    ("click", r"(?:clicks?|activates?|taps?|presses|press|hits?)\b"),
    ("select", r"(?:selects?|chooses?|picks?|filters?\s+by)\b"),
    ("check", r"(?:un)?(?:checks?|ticks?|toggles?)\b"),
    ("input", r"(?:enters?|types?|fills?|replaces?|clears?|submits?|applies|apply|edits?|renames?|adds?|creates?|"
              r"deletes?|removes?|revokes?|drags?|uploads?|imports?|copies|pastes?|starts?|attempts?\s+to|repeats?|"
              r"scrolls?|hovers?|expands?|collapses?|sorts?|changes?|sets?|writes?|leaves?|search(?:es)?(?:\s+for)?)\b"),
]
VERB = re.compile("|".join(f"(?P<{name}>(?<![\\w-]){pattern})" for name, pattern in _VERB_TABLE))
KEYS = re.compile(r"^\s*(?:the\s+)?(Enter|Escape|Esc|Delete|Backspace|Tab|Space|ArrowUp|ArrowDown|ArrowLeft|ArrowRight|"
                  r"Home|End|PageUp|PageDown|(?:Control|Ctrl|Meta|Cmd|Shift|Alt)\+\w+)\b")
MUTATING = re.compile(r"(?i)^(?:sign\s*out|log\s*out|delete|remove|save|submit|confirm|update|create|add|send|cancel|reset|"
                      r"close|reopen|merge|archive|restore|leave|transfer|revoke|approve|reject|block|fork|publish|"
                      r"deactivate|disable|lock|unlock|make\s+(?:private|public)|change\s+visibility|apply|post|comment|"
                      r"ready\s+for\s+review|request|start\s+a\s+review|mark|rename|edit)\b")

def _flat(text: Any) -> str:
    return " ".join(str(text or "").split())

def _qtext(m: re.Match) -> str:
    return next(g for g in m.groups() if g is not None)

def mask(text: str) -> tuple[str, list[str]]:
    quotes: list[str] = []

    def put(m: re.Match) -> str:
        quotes.append(_qtext(m))
        return f"\x01{len(quotes) - 1}\x02"
    return QUOTE.sub(put, text), quotes

def unmask(text: str, quotes: list[str]) -> str:
    return PH.sub(lambda m: "“" + quotes[int(m.group(1))] + "”", text)

class Context:

    def __init__(self, text: str = ""):
        self.text = text or ""
        self.texts = _texts(self.text)
        whole = " ".join(self.texts)
        self.quoted: dict[str, str] = {}
        for m in QUOTE.finditer(whole):
            q = _flat(_qtext(m))
            if q and len(q) <= 80:
                self.quoted.setdefault(q.lower(), q)
        self.accounts = parse_accounts(whole)
        self.roles: dict[str, set[str]] = stated_roles(whole)
        self.signin = sign_in_recipe(whole, self.roles)
        self.commits = [_flat(m.group(1)) for m in re.finditer(r"commit message\s+[`“\"]([^`”\"\n]{1,100})[`”\"]", whole)]

        kinds: dict[str, set[str]] = {}
        for text in self.texts:
            for e in entities_of(text):
                kinds.setdefault(e["name"], set()).add(e["kind"])
        self.kinds = {name: next(iter(k)) for name, k in kinds.items() if len(k) == 1}

def _texts(text: str) -> list[str]:
    try:
        import yaml
        data = yaml.safe_load(text)
    except Exception:
        data = None
    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            out.append(_flat(v))
    if isinstance(data, (dict, list)):
        walk(data)
        return out
    return [_flat(text)]

@functools.lru_cache(maxsize=16)
def requirement_context(text: str) -> Context:
    return Context(text)

def parse_accounts(text: str) -> list[dict[str, Any]]:
    masked, quotes = mask(text)
    found: list[dict[str, Any]] = []
    phs = list(PH.finditer(masked))
    for index, ph in enumerate(phs):
        value = quotes[int(ph.group(1))].strip()
        if not EMAIL.match(value):
            continue

        user = ""
        for back in reversed(phs[max(0, index - 3): index]):
            gap = masked[back.end(): ph.start()]
            cand = quotes[int(back.group(1))].strip()
            if ph.start() - back.end() > 160 or EMAIL.match(cand) or " " in cand or not re.fullmatch(r"[\w.-]{2,60}", cand):
                continue
            if re.search(r"[.;]\s+[A-Z]", gap):
                break
            user = cand
            break
        password = ""
        for nxt in phs[index + 1: index + 3]:
            gap = masked[ph.end(): nxt.start()]
            cand = quotes[int(nxt.group(1))].strip()
            if nxt.start() - ph.end() > 90 or EMAIL.match(cand) or re.search(r"[.;]\s+[A-Z]", gap):
                break
            password = cand
            break
        head_from = phs[index - 1].start() if user and index else ph.start()
        lead = masked[max(0, head_from - 90): head_from]
        lead = lead.rsplit("\x02", 1)[-1]
        lead = re.split(r"[.;]\s+", lead)[-1]
        roles = {w.lower() for w in re.findall(r"[A-Za-z][\w-]*", lead) if w.lower() in ACTOR_ROLES or re.fullmatch(r"[a-z]+-[a-z]+", w)}
        m = re.search(r"([A-Za-z][\w-]*(?:\s+[A-Za-z][\w-]*)?)\s+account\b", lead)
        if m:
            roles |= {w.lower() for w in m.group(1).split() if w.lower() not in {"the", "a", "an", "verified", "available", "and", "signed-in", "ordinary", "non-author"}}
        account = {"account": user, "email": value, "password": password, "roles": sorted(roles)}
        same = [a for a in found if a["email"] == value]
        if not same:
            found.append(account)
        else:
            a = same[0]
            a["account"] = a["account"] or user
            a["password"] = a["password"] or password
            a["roles"] = sorted(set(a["roles"]) | roles)

    for m in re.finditer(r"\b(Owner|Admin|Member|Maintainer|Author|Reviewer|Viewer|Contributor)\s+(?:account\s+)?\x01(\d+)\x02", masked):
        name = quotes[int(m.group(2))].strip()
        for a in found:
            if a["account"] == name and m.group(1).lower() not in a["roles"]:
                a["roles"] = sorted(set(a["roles"]) | {m.group(1).lower()})
    return found

def stated_roles(text: str) -> dict[str, set[str]]:
    masked, quotes = mask(text)
    roles: dict[str, set[str]] = {}
    role_alt = "|".join(sorted((re.escape(r) for r in ROLE_WORDS if r not in {"cell", "field"}), key=len, reverse=True))
    for m in re.finditer(r"\b(" + role_alt + r")\s+(?:(?:named|called|labeled|labelled|whose accessible name is)\s+)?\x01(\d+)\x02", masked):
        roles.setdefault(_flat(quotes[int(m.group(2))]), set()).add(ROLE_WORDS[m.group(1)])
    for m in re.finditer(r"\x01(\d+)\x02\s+(" + role_alt + r")\b", masked):
        roles.setdefault(_flat(quotes[int(m.group(1))]), set()).add(ROLE_WORDS[m.group(2)])
    return roles

def sign_in_recipe(text: str, roles: dict[str, set[str]]) -> dict[str, Any]:
    masked, quotes = mask(text)
    names = [_flat(q) for q in quotes]
    entry = next((n for n in names if re.fullmatch(r"(?i)(?:sign|log)\s*-?\s*in", n)), "")
    identities = [n for n in dict.fromkeys(names) if re.fullmatch(r"(?i)(?:username|user\s*name)(?:\s+or\s+e-?mail(?:\s+address)?)?|"
                                                       r"e-?mail(?:\s+address)?(?:\s+or\s+username)?|login|identifier", n)]

    identity = max(identities, key=lambda n: (" or " in n.lower(), names.count(n)), default="")
    password = next((n for n in names if re.fullmatch(r"(?i)(?:current\s+)?password", n)), "")
    entry_role = submit_role = None
    if entry:
        idx = [i for i, n in enumerate(names) if n == entry]
        for i in idx:
            m = list(PH.finditer(masked))[i] if i < len(list(PH.finditer(masked))) else None
            if m is None:
                continue
            before, after = masked[max(0, m.start() - 60): m.start()], masked[m.end(): m.end() + 60]
            if re.search(r"(?i)\blink\b[^.;\x01]{0,25}$", before) or re.match(r"\s+link\b", after):
                entry_role = "link"
            if re.search(r"(?i)\bbutton\b[^.;\x01]{0,25}$", before) or re.match(r"\s+button\b", after):
                submit_role = "button"
        if entry_role is None and "link" in roles.get(entry, set()):
            entry_role = "link"
        if submit_role is None and "button" in roles.get(entry, set()):
            submit_role = "button"
    home_entry = bool(re.search(r"(?i)home-page\s+\x01\d+\x02\s+link|\bsign\s+in\s+link\s+on\s+the\s+home\s+page|"
                                r"home\s+page[^.;]{0,60}\blink\b[^.;]{0,20}\x01\d+\x02", masked))
    return {"entry": entry, "entry_role": entry_role, "identity": identity, "password": password,
            "submit": entry, "submit_role": submit_role, "home_entry": home_entry}

def _parts(scenario: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for step in scenario.get("steps") or []:
        if not isinstance(step, dict):
            continue
        kw = str(step.get("keyword") or "").strip().upper()
        content = _flat(step.get("content"))
        if kw in ("AND", "BUT") and out:
            kw = out[-1][0]
        if kw not in ("GIVEN", "WHEN", "THEN"):
            kw = out[-1][0] if out else "GIVEN"
        out.append((kw, content))
    return out

def _sentences(masked: str) -> list[tuple[int, str]]:
    out, start = [], 0
    for m in re.finditer(r"(?<=[.;!?])\s+(?=[A-Z\x01(])", masked):
        out.append((start, masked[start: m.start()]))
        start = m.end()
    out.append((start, masked[start:]))
    return [(s, t) for s, t in out if t.strip()]

def entities_of(text: str, extra_commits: list[str] | None = None) -> list[dict[str, str]]:
    masked, quotes = mask(text)

    masked = re.split(r"(?i)\bseed values:", masked)[0]
    found: list[dict[str, str]] = []

    def add(kind: str, name: str) -> None:
        name = _flat(name)
        if name and not EMAIL.match(name) and not any(e["kind"] == kind and e["name"] == name for e in found):
            found.append({"kind": kind, "name": name})
    for kind, noun in ENTITY_KINDS:
        if noun is None:
            continue
        pattern = r"(?i)\b" + noun + r"\s+(?:(?:titled|named|called|identifier|with\s+title)\s+)?((?:\x01\d+\x02(?:\s*,\s*|\s+and\s+|\s+or\s+)?)+)"
        for m in re.finditer(pattern, masked):
            for ph in PH.finditer(m.group(1)):
                add(kind, quotes[int(ph.group(1))])
    for m in re.finditer(r"\x01(\d+)\x02\s+entry\b", masked):
        add("entry", quotes[int(m.group(1))])

    order = {e["name"]: masked.find(e["name"]) for e in found}
    found.sort(key=lambda e: (SUBJECT_RANK.index(e["kind"]) if e["kind"] in SUBJECT_RANK else 99, order.get(e["name"], 0)))
    if extra_commits and not any(e["kind"] == "commit" for e in found):
        for c in extra_commits[:1]:
            found.append({"kind": "commit", "name": c, "from": "description"})
    return found

def _role_near(obj_masked: str, at: int, end: int) -> str | None:
    before = obj_masked[max(0, at - 40): at]
    after = obj_masked[end: end + 20]
    m = re.search(r"\b(link|links|button|buttons|tab|tabs|menuitem|menu item|option|options|checkbox|textbox|searchbox|"
                  r"combobox|radio|menu)\s+(?:named\s+|called\s+|labeled\s+|labelled\s+)?$", before, re.I)
    if m:
        return ROLE_WORDS[m.group(1).lower()]
    m = re.match(r"\s+(link|links|button|buttons|tab|tabs|option|options|checkbox|textbox|searchbox|combobox|radio|menu)\b", after, re.I)
    if m:
        return ROLE_WORDS[m.group(1).lower()]
    return None

def _clean_phrase(phrase: str) -> str:
    words = phrase.strip(" ,.;:").split()
    while words and words[0].lower() in FILLER:
        words.pop(0)
    return " ".join(words)

class _Builder:
    def __init__(self, ctx: Context, given: str, scenario_text: str):
        self.ctx = ctx
        self.steps: list[dict[str, Any]] = []

        self.entities = entities_of(given, ctx.commits if re.search(r"(?i)\bcommits?\b", scenario_text) else None)
        known = {e["name"] for e in self.entities}
        for m in QUOTE.finditer(re.split(r"(?i)\bseed values:", given)[0]):
            name = _flat(_qtext(m))
            kind = getattr(ctx, "kinds", {}).get(name)
            if kind and name not in known and not re.search(r"(?i)\b(?:account|owner|admin|member|user|email|password)\W*$",
                                                             given[max(0, m.start() - 30): m.start()]):
                self.entities.append({"kind": kind, "name": name, "from": "requirement-kind"})
                known.add(name)
        self.scenario_text = scenario_text
        self.given = given
        self.leaf_description = ""
        self.last_target: dict[str, Any] | None = None

    def entity(self, kind: str, prefer: str | None = None) -> tuple[dict[str, str] | None, str]:
        cands = [e for e in self.entities if e["kind"] == kind]
        if kind == "entry" and not cands:
            cands = [{"kind": "entry", "name": self.ctx.quoted.get("compare", "")}] if "compare" in self.ctx.quoted else []
        if prefer:
            same = [s for s in reversed(self.steps) if s.get("entity") and s["entity"]["kind"] == kind]
            if same:
                return same[0]["entity"], ""
        if len(cands) == 1:
            return cands[0], ""
        if not cands:
            return None, f"no {kind} named in the scenario"
        return None, f"{len(cands)} {kind} candidates: " + ", ".join(c["name"] for c in cands[:4])

    def known_name(self, phrase: str) -> str | None:
        p = _flat(phrase).lower()
        return self.ctx.quoted.get(p)

    def add(self, **step: Any) -> dict[str, Any]:
        step.setdefault("status", "resolved")
        step.setdefault("role", None)
        step.setdefault("scope", None)
        step.setdefault("entity", None)
        step.setdefault("session", 1)
        step.setdefault("basis", "quoted")
        step.setdefault("alternatives", [])
        step["n"] = len(self.steps) + 1
        self.steps.append(step)
        if step.get("target") and step["action"] in ("open", "click", "menu", "choose", "check"):
            self.last_target = step
        return step

ENTITY_NOUNS = {"pr", "prs", "pull", "request", "issue", "release", "tag", "commit", "repository", "workbook", "worksheet",
                "organization", "team", "branch", "label", "milestone", "file", "directory", "folder", "record", "entry",
                "account", "user", "member", "view"}
NAV_ACTIONS = ("open", "click", "menu", "sign_in", "home", "back", "reload")

def _session_of(text: str, current: int) -> int:
    if re.search(r"(?i)\b(?:in|from|using|with)\s+(?:the\s+|a\s+)?(?:second|other|another)\s+(?:browser|context|tab)\b", text):
        return 2
    if re.search(r"(?i)\b(?:in|from|using|with)\s+(?:the\s+)?(?:first|one)\s+(?:browser|context|tab)\b", text):
        return 1
    return current

def scenario_plan(scenario: dict[str, Any], leaf: dict[str, Any] | None = None, ctx: Context | None = None) -> dict[str, Any]:
    leaf = leaf or {}
    ctx = ctx or Context("")
    parts = _parts(scenario)
    given = " ".join(c for k, c in parts if k == "GIVEN")
    when = " ".join(c for k, c in parts if k == "WHEN")
    b = _Builder(ctx, given, " ".join(c for _k, c in parts))
    b.leaf_description = _flat(leaf.get("description"))
    plan: dict[str, Any] = {"leaf": str(leaf.get("id") or ""), "scenario": _flat(scenario.get("name")),
                            "entities": b.entities, "notes": []}

    auth = "unknown"
    if re.search(r"(?i)\bunauthenticated\b|\bsigned[- ]out\b|\bnot\s+(?:authenticated|signed\s+in)\b|\bvisitor\s+is\s+unauthenticated\b", given):
        auth = "anonymous"
    sessions = 2 if re.search(r"(?i)\btwo\s+(?:active\s+)?browser\s+sessions\b|\bsecond\s+browser\b|\bother\s+browser\b", plan["scenario"] + " " + given + " " + when) else 1
    plan["start"] = {"page": "/", "auth": auth}
    plan["sessions"] = sessions
    deferred: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    for part, content in parts:
        masked, quotes = mask(content)
        session = 1
        for s_at, sentence in _sentences(masked):
            if re.match(r"(?i)\s*seed values:", sentence):
                continue
            if part == "GIVEN":

                st = re.search(r"(?i)\b(?:has|have)\s+(?:already\s+)?(?:entered|opened|navigated\s+to|visited|reached|"
                               r"selected|filtered|expanded)\b[^.;]*|\bis\s+(?:already\s+)?(?:on|viewing|inside)\s+the\s+[^.;]*", sentence)
                if st and not re.search(r"(?i)home\s+page|account-access", st.group(0)):
                    states.append({"action": "state", "target": None, "role": None, "entity": None, "part": "GIVEN",
                                   "verb": "", "text": unmask(st.group(0), quotes), "session": session, "basis": "text",
                                   "status": "unresolved", "scope": None, "alternatives": [],
                                   "why": "GIVEN state reached by unstated steps: " + unmask(st.group(0), quotes)[:80]})
            verbs = []
            for m in VERB.finditer(sentence):

                if verbs and verbs[-1].lastgroup == "observe" and not re.search(r"[,;]", sentence[verbs[-1].end(): m.start()]):
                    continue
                verbs.append(m)
            for vi, vm in enumerate(verbs):
                kind = vm.lastgroup
                end = verbs[vi + 1].start() if vi + 1 < len(verbs) else len(sentence)
                obj = sentence[vm.end(): end]
                head = sentence[:vm.start()]
                clause_head = re.split(r"[,;:]", head)[-1]
                text = unmask(sentence[vm.start(): end], quotes).strip(" ,;")
                if kind in ("start",):
                    continue
                if SYSTEM_SUBJECT.search(clause_head) and not ACTOR_SUBJECT.match(clause_head.strip()):
                    continue
                if NEGATED.search(head[-40:]):
                    continue
                if kind != "capability" and re.search(r"(?i)\b(?:to|can|may|could|must|should|will|would)\s+(?:\w+\s+(?:and|or)\s+)?$", head[-30:]):
                    continue
                since = head.split(";")[-1]
                conditional = bool(CONDITIONAL.search(obj.split(",")[0])) or \
                    bool(re.search(r"(?i)\bif\b[^,]*,\s*(?:the\s+\w+\s+)?(?:\w+\s+)?$", since))
                b.mentioned = [quotes[int(p.group(1))] for p in PH.finditer(head)]
                b.mention_head = head
                session = _session_of(obj, session)
                chunks = re.split(r",?\s+(?:and\s+)?then\s+", obj) if kind in ("open", "click", "select") else [obj]
                for ci, chunk in enumerate(chunks):
                    if ci and not chunk.strip():
                        continue
                    _interpret(b, part, kind, vm.group(0), chunk, quotes, text, session, conditional, deferred, clause_head)

    _place_deferred(b, deferred)
    _implied_sign_in(b, plan, given, when)
    if states:
        at = next((i for i, s in enumerate(b.steps) if s["part"] == "WHEN" and s["action"] != "sign_in"), len(b.steps))
        b.steps[at:at] = states
    _implied_subject(b, plan, when)
    _dedupe(b)
    for i, s in enumerate(b.steps, 1):
        s["n"] = i
    plan["steps"] = b.steps
    plan["actor"] = next((s.get("account") for s in b.steps if s["action"] == "sign_in" and s.get("account")), None)
    plan["unresolved"] = sum(1 for s in b.steps if s["status"] == "unresolved" and s["part"] != "THEN")
    plan["then_unresolved"] = sum(1 for s in b.steps if s["status"] == "unresolved" and s["part"] == "THEN")
    plan["implied"] = sum(1 for s in b.steps if s["status"] == "implied")
    return plan

def _interpret(b: _Builder, part: str, kind: str, verb: str, obj: str, quotes: list[str], text: str, session: int,
               conditional: bool, deferred: list, clause_head: str) -> None:
    status = "conditional" if conditional else "resolved"
    common = {"part": part, "verb": verb.strip(), "text": text, "session": session}
    phs = list(PH.finditer(obj))
    first = phs[0] if phs else None
    lead = obj[: first.start()] if first else obj
    if kind == "observe":
        b.add(action="observe", target=None, status="n/a", basis="text", **common)
        return
    if kind == "capability":
        if part != "GIVEN" or not re.search(r"open|access|reach|view", verb):
            return
        before = len(b.steps)
        ranked = [e for e in b.entities if e["kind"] in SUBJECT_RANK]
        single = ranked[0] if ranked and len([e for e in ranked if e["kind"] == ranked[0]["kind"]]) == 1 else None
        possessive = re.match(r"\s*(?:its|their)\s+(.*)$", obj, re.S)
        if re.match(r"\s*(?:it|them)\b", obj):
            if single:
                b.add(action="open", target=single["name"], entity=single, basis="referent", status="resolved", **common)
        elif possessive and single:

            b.add(action="open", target=single["name"], entity=single, basis="referent", status="resolved", **common)
            rest = possessive.group(1)
            if not re.match(r"\s*(?:overview|detail(?:\s+page)?|page)\b", rest):
                _interpret(b, part, "open", "opens", " the " + rest, quotes, text, session, conditional, deferred, clause_head)
        else:
            _interpret(b, part, "open", "opens", obj, quotes, text, session, conditional, deferred, clause_head)
        group = len(deferred) + 1
        for s in b.steps[before:]:
            s["status"] = "implied" if s["status"] == "resolved" else s["status"]
            s["basis"] = "given-capability:" + s["basis"]
            s["group"] = group
            deferred.append(s)
        del b.steps[before:]
        return
    if kind == "sign_out":
        b.add(action="sign_out", target=None, basis="text", status="resolved", **common)
        return
    if kind == "reload":
        b.add(action="reload", target=None, basis="text", status=status, **common)
        return
    if kind == "home":
        b.add(action="home", target="/", basis="text", status=status, **common)
        return
    if kind == "back":
        b.add(action="back", target=None, basis="text", status=status, **common)
        return
    if kind == "sign_in":
        _sign_in(b, obj, quotes, clause_head, status, common)
        return
    if kind in ("open", "click", "select", "check"):
        if kind == "click" and verb.startswith("press"):
            km = KEYS.match(PH.sub(lambda m: quotes[int(m.group(1))], obj))
            if km:
                b.add(action="press", target=km.group(1), basis="text", status="n/a", **common)
                return
        if kind == "select":
            cell = CELL.match(obj.strip().removeprefix("cell ").removeprefix("cells ").strip()) if not phs else None
            if cell or re.match(r"\s*(?:cell|cells|range)\s+[A-Z]{1,3}\d", obj):
                ref = CELL.search(obj)
                b.add(action="select_cell", target=ref.group(0) if ref else None, role="gridcell", basis="text", status="n/a", **common)
                return
            field_noun = r"(?:(?:combobox|dropdown|select|listbox|field)\s+(?:named\s+|labell?ed\s+)?)?"
            if phs and re.search(r"\b(?:as|for|in|from)\s+(?:the\s+)?" + field_noun + r"(?:\x01\d+\x02|[A-Z]\w*)", obj[first.end():first.end() + 60]):
                value = quotes[int(first.group(1))]
                fm = re.search(r"\b(?:as|for|in|from)\s+(?:the\s+)?" + field_noun + r"(?:\x01(\d+)\x02|([A-Z][\w ]{0,30}?))(?=\s|,|$)", obj[first.end():])
                b.add(action="choose", target=value, field=(quotes[int(fm.group(1))] if fm and fm.group(1) else (fm.group(2) if fm else None)),
                      basis="quoted", status="n/a" if status == "resolved" else status, **common)
                return
            head_words = [w for w in re.findall(r"[A-Za-z][\w-]*", obj[: first.start()]) if w.lower() not in FILLER
                          and w.lower() not in ROLE_WORDS] if phs else []
            if head_words:

                b.add(action="choose", target=quotes[int(first.group(1))], field=" ".join(head_words), basis="quoted",
                      status="n/a" if status == "resolved" else status, **common)
                return
        if kind == "check" and phs:
            for ph in phs:
                b.add(action="check", target=quotes[int(ph.group(1))], role="checkbox", basis="quoted", status="n/a", **common)
            return
        _open_or_click(b, kind, verb, obj, quotes, phs, lead, status, common)
        return
    if kind == "input":
        _input(b, verb, obj, quotes, phs, status, common)

def _sign_in(b: _Builder, obj: str, quotes: list[str], clause_head: str, status: str, common: dict) -> None:
    ctx = b.ctx
    who = None
    why = ""
    m = re.match(r"\s+(?:again\s+)?as\s+(?:\x01(\d+)\x02|(this account|[\w-]+))", obj)
    w = re.match(r"\s+(?:again\s+)?with\s+(?:\x01(\d+)\x02|the\s+same\s+(?:email|account))", obj)
    local = parse_accounts(b.scenario_text)
    pool = local + [a for a in ctx.accounts if not any(a["account"] == x["account"] and a["email"] == x["email"] for x in local)]

    def by_name(name: str) -> dict | None:
        hits = [a for a in pool if name in (a["account"], a["email"])]
        return hits[0] if hits else None
    if m and m.group(1) is not None:
        name = quotes[int(m.group(1))]
        who = by_name(name) or {"account": name, "email": "", "password": "", "roles": []}
    elif m and m.group(2):
        word = m.group(2).lower()
        if word == "this account" and len(local) == 1:
            who = local[0]
        else:
            hits = [a for a in local if a["account"].lower() == word or word in a["roles"]] or \
                   [a for a in pool if a["account"].lower() == word or word in a["roles"]]
            who = hits[0] if len({h["account"] for h in hits}) == 1 else None
            why = "" if who else f"account '{word}' not bound"
    elif w and w.group(1) is not None:
        who = by_name(quotes[int(w.group(1))])
    elif w and len({a["email"] for a in local}) == 1:
        who = local[0]
    if who is None and not why:
        subject = ACTOR_SUBJECT.findall(clause_head)
        role = subject[-1].lower() if subject else ""
        hits = [a for a in local if role and (role in a["roles"] or role in a["account"].lower().split("-"))]
        if len({h["account"] for h in hits}) == 1:
            who = hits[0]
        elif len({a["account"] for a in local}) == 1:
            who = local[0]
        else:
            why = "signer not bound: " + (f"{len(local)} accounts in the scenario" if local else "no account in the scenario")
    if w and who is not None:
        pw = PH.search(obj)
        if pw and not EMAIL.match(quotes[int(pw.group(1))]) and quotes[int(pw.group(1))] != who.get("account"):
            who = {**who, "password": quotes[int(pw.group(1))]}
    b.add(action="sign_in", target=ctx.signin.get("entry") or None, role=ctx.signin.get("entry_role"),
          account=who, basis="recipe", status=("unresolved" if who is None else status), why=why, **common)

REGION_LANDMARKS = {
    "main content": ("main", 'main,[role="main"]', "<main>"),
    "header": ("banner", 'header,[role="banner"]', "<header>"),
    "navigation": ("navigation", 'nav,[role="navigation"]', "<nav>"),
    "sidebar": ("complementary", 'aside,[role="complementary"]', "<aside>"),
    "toolbar": ("toolbar", '[role="toolbar"]', 'role="toolbar"'),
}

def region_key(name: str | None) -> str | None:
    key = " ".join(str(name or "").lower().split())
    return key if key in REGION_LANDMARKS else None

def stated_region_on() -> bool:
    try:
        from .settings import setting_on
        return setting_on("stated_region_scope", True)
    except Exception:
        return True

def _located_mention(b: _Builder, recent: str, quotes: list[str]) -> tuple[str | None, dict | None] | None:
    if not stated_region_on():
        return None
    head = getattr(b, "mention_head", "") or ""
    spots = [p for p in PH.finditer(head) if quotes[int(p.group(1))] == recent]
    if not spots:
        return None
    ph = spots[-1]
    role = _role_near(head, ph.start(), ph.end())
    after = re.split(r"[,;]", head[ph.end():], maxsplit=1)[0]
    m = re.match(r"\s*(?:(?:link|button|tab)\s+)?in\s+the\s+(main\s+content|header|navigation|sidebar|toolbar)\b", after)
    scope = {"kind": "region", "name": m.group(1)} if m else None
    if scope is None:
        return None
    return role, scope

def _open_or_click(b: _Builder, kind: str, verb: str, obj: str, quotes: list[str], phs: list, lead: str, status: str, common: dict) -> None:
    plain = PH.sub(lambda m: quotes[int(m.group(1))], obj)

    if re.match(r"\s*(?:it|them)\b", obj):
        mentioned = getattr(b, "mentioned", [])
        recent = mentioned[-1] if mentioned else None
        if recent and (b.last_target is None or b.last_target.get("target") != recent):
            stated = b.ctx.roles.get(recent, set())
            role = next(iter(stated)) if len(stated) == 1 else None
            located = _located_mention(b, recent, quotes)
            if located is None:
                b.add(action="click" if kind != "open" else "open", target=recent,
                      role=role, basis="pronoun", status=status, **common)
            else:
                b.add(action="click" if kind != "open" else "open", target=recent, role=located[0] or role,
                      scope=located[1], basis="pronoun", status=status, **common)
        elif b.last_target is not None:
            prev = b.last_target
            b.add(action="click", target=prev["target"], role=prev.get("role"), scope=prev.get("scope"), basis="pronoun",
                  status=status, **common)
        else:
            b.add(action="click", target=None, basis="pronoun", status="unresolved", why="pronoun without a target", **common)
        return

    thr = re.search(r"\b(?:through|via|using)\s+(?:the\s+)?(?:visible\s+)?((?:\x01\d+\x02(?:\s*,\s*|\s+and\s+(?:then\s+)?)?)+)\s*(links?|buttons?|tabs?)?", obj)
    if thr:
        role = ROLE_WORDS.get((thr.group(2) or "").lower())
        for ph in PH.finditer(thr.group(1)):
            b.add(action="click", target=quotes[int(ph.group(1))], role=role, basis="quoted", status=status, **common)
        return

    menu = re.search(r"\b(?:in|from|inside|under)\s+(?:the\s+)?(?:\x01(\d+)\x02|([\w ]{1,30}?))\s+menu\b", obj)
    scope = None
    if menu:
        mname = quotes[int(menu.group(1))] if menu.group(1) else (b.known_name(menu.group(2) + " menu") or b.known_name(menu.group(2)) or None)
        if mname:
            b.add(action="menu", target=mname, role=None, basis="quoted" if menu.group(1) else "unquoted", status=status, **common)
            scope = {"kind": "menu", "name": mname}
        else:
            b.add(action="menu", target=None, basis="text", status="unresolved", why="menu not named: " + menu.group(0), **common)
            scope = {"kind": "menu", "name": None}
        obj_for_targets = obj[: menu.start()]
        phs = [p for p in phs if p.start() < menu.start()]
    else:
        obj_for_targets = obj
    region = re.search(r"\bin\s+the\s+(main\s+content|header|navigation|sidebar|toolbar)\b", obj_for_targets)
    if region and scope is None:
        scope = {"kind": "region", "name": region.group(1)}
    if phs:
        head = obj_for_targets[: phs[0].start()]
        headwords = [w.lower() for w in re.findall(r"[A-Za-z][\w-]*", head)]
        noun_kinds = [REFERENT_NOUNS.get(w) for w in headwords if REFERENT_NOUNS.get(w)]

        joined = obj_for_targets[phs[0].start():]
        alt = re.fullmatch(r"\s*(?:\x01\d+\x02\s*(?:,\s*|\s+or\s+))+\x01\d+\x02.*", joined, re.S) and " or " in joined[:200]
        seq = re.match(r"\s*\x01\d+\x02(?:\s*,\s*|\s+and\s+(?:then\s+)?)\x01\d+\x02(?:(?:\s*,\s*|\s+and\s+(?:then\s+)?)\x01\d+\x02)*\s+(links|tabs|buttons)\b", joined)
        if re.search(r"\b(?:for|of|with|to|from|by|about)\s*$", head.strip()) and not any(w in ENTITY_NOUNS for w in headwords):

            if kind == "open":
                b.add(action="open", target=None, basis="text", status="unresolved", why="target phrase: " + _flat(PH.sub(lambda m: quotes[int(m.group(1))], obj_for_targets))[:80], **common)
                return
        targets = [phs[0]]
        if seq:
            targets = list(PH.finditer(seq.group(0)))
        for i, ph in enumerate(targets):
            name = quotes[int(ph.group(1))]
            role = _role_near(obj_for_targets, ph.start(), ph.end()) if not seq else ROLE_WORDS[seq.group(1)]
            entity = next((e for e in b.entities if e["name"] == name), None)
            if entity is None and noun_kinds:
                entity = {"kind": noun_kinds[0], "name": name}
            alternatives = [quotes[int(p.group(1))] for p in PH.finditer(joined[:200])] if alt else []
            action = "click" if kind in ("click", "select") else "open"
            if kind == "select" and role is None and re.search(r"\boption\b", obj_for_targets):
                role = "option"
            b.add(action=action, target=name, role=role, scope=scope, entity=entity, alternatives=alternatives[1:],
                  basis="quoted", status=status, **common)
        return

    if re.match(r"\s*(?:the\s+)?(?:exact|matching|same)\s+option\b", plain):
        typed = next((s for s in reversed(b.steps) if s["action"] == "input" and s.get("values")), None)
        if typed is not None:
            b.add(action="click", target=typed["values"][-1], role="option", basis="referent", status=status, **common)
            return
    separators = r"\s+(?:then|to|so|while|until|after|before|with|without|for|from|on|in|when|if|as|using|by)\b|[,;.]"
    whole = _clean_phrase(_flat(re.split(separators, " " + plain.strip())[0]))
    if re.search(r"\s(?:and|or)\s", whole) and kind in ("open", "click") and not b.known_name(whole):

        parts = [p.strip() for p in re.split(r"\s+(?:and|or)\s+", whole)]
        names = [b.known_name(p) or (p if re.fullmatch(r"[A-Z][\w-]*(?:\s+[a-z][\w-]*){0,2}", p) else None) for p in parts]
        if all(names):
            if re.search(r"\sor\s", whole):
                b.add(action=kind, target=names[0], alternatives=names[1:], basis="unquoted", status=status, **common)
            else:
                for n in names:
                    b.add(action=kind, target=n, basis="unquoted", status=status, **common)
            return
    phrase = whole if b.known_name(whole) else _flat(re.split(r"\s+(?:and|or)\b|" + separators, " " + plain.strip())[0])
    if kind == "open" and re.match(r"\s*(?:the\s+)?(?:application\s+)?home\s+page\b|\s*(?:the\s+)?workbook\s+home\s+page\b", plain):
        b.add(action="home", target="/", basis="text", status=status, **common)
        return
    core = _clean_phrase(phrase)
    low = core.lower()

    ref = re.fullmatch(r"(?:same\s+|draft\s+|archived\s+|seeded\s+)*(pr|prs|pull request|issue|release|commit entry|commit|repository|"
                       r"workbook|organization|comparison|worksheet)(?:\s+(?:entry|detail page|detail|page|overview|"
                       r"again|code page|link))*", low.replace("draft pr", "pr"))
    if ref and kind in ("open", "click", "select"):
        noun = REFERENT_NOUNS.get(ref.group(1), ref.group(1))
        same = "same" in low or "again" in plain[:40]
        ent, why = b.entity(noun, prefer="same" if same else None)
        if ent is None and noun == "commit" and b.ctx.commits:
            ent, why = {"kind": "commit", "name": b.ctx.commits[0], "from": "description"}, ""
        if ent is None:
            b.add(action="open", target=None, basis="referent", status="unresolved", why=f"'{core}': {why}", **common)
        else:
            b.add(action="open", target=ent["name"], entity=ent, basis="referent", status=status, **common)
        return

    if re.fullmatch(r"(?:its|their)\s+(?:detail\s+page|overview|page)", phrase.lower()):
        b.add(action="observe", target=None, basis="text", status="n/a", **common)
        return

    pm = re.fullmatch(r"(?:(?:the|its|their|repository|repository's|organization)\s+)*(.+?)\s+(page|tab|link|menu|section|view|dialog)", core, re.I)
    name_phrase = pm.group(1) if pm else core
    noun = pm.group(2).lower() if pm else ""
    if noun == "menu":
        known = b.known_name(name_phrase + " menu") or b.known_name(name_phrase)
        if known:
            b.add(action="menu", target=known, basis="unquoted", status=status, **common)
            return
    known = b.known_name(name_phrase) or b.known_name(core)
    if known:
        b.add(action="open" if kind == "open" else "click", target=known, role=("tab" if noun == "tab" else ("link" if noun == "link" else None)),
              basis="unquoted", status=status, **common)
        return
    if name_phrase and re.fullmatch(r"[A-Z][\w-]*(?:\s+(?:[a-z][\w-]*|[A-Z][\w-]*)){0,3}", name_phrase) and name_phrase.split()[0].lower() not in FILLER:
        b.add(action="open" if kind == "open" else "click", target=name_phrase, basis="unquoted", status=status, **common)
        return
    if noun == "page" and kind == "open":

        b.add(action="destination", target=None, basis="text", status="described", why=core, **common)
        return
    defined = _defined_control(b, core)
    if defined is not None:
        name, role, why = defined
        b.add(action=("open" if kind == "open" else "click"), target=name, role=role, basis="description-definition",
              status=status if name else "unresolved", why=why, **common)
        return
    b.add(action=("open" if kind == "open" else "click"), target=None, basis="text", status="unresolved",
          why=("unnamed: " + core)[:90], **common)

def _defined_control(b: _Builder, phrase: str) -> tuple[str | None, str, str] | None:
    words = phrase.lower().split()
    if not words:
        return None
    masked, quotes = mask(b.leaf_description)
    for m in re.finditer(r"(?i)\bthe\s+([a-z][\w -]{1,40}?)\s+is\s+(?:a|an|the)\s+(?:unique\s+|single\s+|visible\s+)?"
                         r"(button|link|tab|textbox|combobox|searchbox|menu|menuitem)\s+(?:named|called|labell?ed)\s+\x01(\d+)\x02", masked):
        noun = m.group(1).lower().split()
        if noun[-1] != words[-1] or not (noun == words[-len(noun):] or words == noun[-len(words):]):
            continue
        template = quotes[int(m.group(3))]
        role = m.group(2).lower()
        slots = re.findall(r"<([^<>]{1,40})>", template)
        if not slots:
            return template, role, ""
        name = template
        for slot in slots:
            kind = next((k for k in ("branch", "repository", "workbook", "worksheet", "organization") if k in slot.lower()), None)
            values = re.findall(r"(?i)\b(?:active|current|default)\s+" + (kind or "\x00") + r"\s+[`“\"]([^`”\"]{1,60})[`”\"]", b.given) if kind else []
            if len(set(values)) != 1:
                return None, role, f"template {template!r}: no single value for <{slot}>"
            name = name.replace(f"<{slot}>", values[0])
        return name, role, ""
    return None

def _input(b: _Builder, verb: str, obj: str, quotes: list[str], phs: list, status: str, common: dict) -> None:
    fields: list[str] = []
    values: list[str] = []
    for m in re.finditer(r"\x01(\d+)\x02\s+(?:in|into|to)\s+(?:both\s+)?(?:the\s+)?(?:visibly\s+labell?ed\s+)?(?:\x01(\d+)\x02|([A-Z][\w-]*(?:\s+(?:and\s+)?[A-Za-z][\w-]*){0,3}?))(?=[\s,.;]|$)", obj):
        values.append(quotes[int(m.group(1))])
        fields.append(quotes[int(m.group(2))] if m.group(2) else m.group(3))
    for m in re.finditer(r"^\s*(?:the\s+)?(?:top\s+)?(?:\w+\s+)?(?:(?:searchbox|textbox|field|box)\s+named\s+)?\x01(\d+)\x02(?:\s+(?:field|textbox|searchbox|box))?\s+with\b", obj):
        fields.append(quotes[int(m.group(1))])
    for m in re.finditer(r"\bwith\s+\x01(\d+)\x02", obj):
        values.append(quotes[int(m.group(1))])
    named = [quotes[int(p.group(1))] for p in phs]
    b.add(action="input", target=None, fields=fields, values=values, names=named, basis="text", status="n/a", **common)

def _place_deferred(b: _Builder, deferred: list[dict[str, Any]]) -> None:
    if not deferred:
        return
    steps = b.steps

    explicit = {(s.get("target") or "").lower() for s in steps if s["status"] == "resolved" and s.get("target")}
    dropped = {d.get("group") for d in deferred if (d.get("target") or "").lower() in explicit}
    keep = [d for d in deferred if d.get("group") not in dropped]
    if not keep:
        return
    first_when = next((i for i, s in enumerate(steps) if s["part"] == "WHEN"), len(steps))
    at = first_when
    if at < len(steps) and steps[at]["action"] == "sign_in":
        at += 1
    elif any(s["action"] == "sign_in" for s in steps[:first_when]):
        at = first_when
    for offset, d in enumerate(keep):
        steps.insert(at + offset, d)

def _implied_sign_in(b: _Builder, plan: dict[str, Any], given: str, when: str) -> None:
    if any(s["action"] == "sign_in" for s in b.steps):
        return
    local = parse_accounts(b.scenario_text)
    subject = ACTOR_SUBJECT.search(when)
    role = subject.group(1).lower() if subject else ""
    signed_given = re.search(r"(?i)\bsigned[- ]in\b|\bactive\s+(?:browser\s+)?sessions?\b|\bsessions?\s+for\s+that\s+account\b", given)
    if role in ("visitor",) and not signed_given:
        return
    if role == "user" and not local and not signed_given:
        return
    if not local and plan["start"]["auth"] == "anonymous" and not signed_given:
        return
    who = None
    hits = [a for a in local if role and (role in a["roles"] or role in a["account"].lower().split("-"))]
    if len({h["account"] for h in hits}) == 1:
        who = hits[0]
    elif len({a["account"] for a in local}) == 1 and (role not in ("visitor", "user") or signed_given):
        who = local[0]
    else:
        named = re.findall(r"\b(?:Owner|Admin|Member|Maintainer|Author|Reviewer|Viewer)\s+[`“]([^`”]+)[`”]", given)
        pool = [a for a in b.ctx.accounts if a["account"] in named]
        if len(pool) == 1:
            who = pool[0]
    if who is None and not (role and role not in ("visitor", "user")) and not signed_given:
        return
    first_when = next((i for i, s in enumerate(b.steps) if s["part"] == "WHEN"), len(b.steps))
    step = {"action": "sign_in", "target": b.ctx.signin.get("entry") or None, "role": b.ctx.signin.get("entry_role"),
            "account": who, "part": "WHEN", "verb": "", "text": f"(implied: the {role or 'actor'} acts signed in)",
            "session": 1, "basis": "implied-actor", "status": "implied" if who else "unresolved",
            "why": "" if who else f"signer '{role}' not bound", "scope": None, "entity": None, "alternatives": []}
    b.steps.insert(first_when, step)
    if plan["sessions"] == 2:
        b.steps.insert(first_when + 1, {**step, "session": 2, "text": "(implied: the second browser session is signed in too)"})

def _implied_subject(b: _Builder, plan: dict[str, Any], when: str) -> None:
    steps = b.steps
    when_steps = [s for s in steps if s["part"] == "WHEN" and s["action"] != "sign_in"]
    if not when_steps:
        return

    for i, s in enumerate(steps):
        if s["part"] == "WHEN" and s["action"] == "open" and s.get("target") and re.match(r"re-?open", s.get("verb", "")):
            if not any(t.get("target") == s["target"] for t in steps[:i]):
                at = next((j for j, t in enumerate(steps) if t["part"] == "WHEN" and t["action"] != "sign_in"), i)
                steps.insert(at, {**s, "verb": "", "status": "implied", "basis": "implied-reopen",
                                  "text": f"(implied: \"{s['verb']} {s['target']}\" means it was open before)"})
                return

    if any(s["part"] == "GIVEN" and s["action"] in ("open", "menu", "click", "destination", "state") for s in steps):
        return
    first = next((s for s in when_steps if s["action"] != "observe"), when_steps[0])
    if first["action"] in ("home", "destination") or (first["action"] == "open" and (
            first.get("entity") or first.get("basis") == "referent" or first["status"] == "unresolved")):
        return
    ranked = [e for e in b.entities if e["kind"] in SUBJECT_RANK and e.get("from") != "description"]
    if not ranked:
        return
    top = ranked[0]["kind"]
    if top in ("repository", "organization") and first["action"] not in ("click", "choose", "check", "press", "select_cell"):

        return
    same = [e for e in ranked if e["kind"] == top]
    when_text = re.split(r"(?i)\bseed values:", when)[0]
    used = [e for e in same if e["name"] in when_text]
    if used:
        return
    first_target = (first.get("target") or "").lower()
    if first_target and first_target in COLLECTION.get(top, set()):
        return
    at = next(i for i, s in enumerate(steps) if s is first)
    if len(same) == 1:
        steps.insert(at, {"action": "open", "target": same[0]["name"], "role": None, "entity": same[0], "part": "WHEN",
                          "verb": "", "text": f"(implied: the WHEN acts on the {top} `{same[0]['name']}` without opening it)",
                          "session": 1, "basis": "implied-subject", "status": "implied", "scope": None, "alternatives": []})
    else:
        steps.insert(at, {"action": "open", "target": None, "role": None, "entity": None, "part": "WHEN", "verb": "",
                          "text": "(implied: the WHEN acts on a record it never opens)", "session": 1,
                          "basis": "implied-subject", "status": "unresolved", "scope": None, "alternatives": [],
                          "why": f"{len(same)} {top} candidates: " + ", ".join(e["name"] for e in same[:4])})

def _dedupe(b: _Builder) -> None:
    out: list[dict[str, Any]] = []
    for s in b.steps:
        if out and s["action"] in ("open", "click", "menu") and s.get("target") and out[-1].get("target") == s["target"] \
                and out[-1]["action"] in ("open", "click", "menu") and s["session"] == out[-1]["session"]:
            if out[-1]["status"] == "implied" and s["status"] == "resolved":
                out[-1] = s
            continue
        out.append(s)
    b.steps[:] = out

def _leaves(text: str) -> list[dict[str, Any]]:
    try:
        import yaml
        data = yaml.safe_load(text)
    except Exception:
        return []
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            children = node.get("children") if isinstance(node.get("children"), list) else []
            if children:
                for c in children:
                    walk(c)
            elif str(node.get("id") or "").strip() not in ("", "ROOT"):
                found.append(node)
    walk(data)
    return found

def plans_for(requirement_text: str, leaf_ids: list[str] | None = None, leaves: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    try:
        ctx = requirement_context(requirement_text or "")
        pool = leaves if leaves is not None else _leaves(requirement_text or "")
        wanted = set(map(str, leaf_ids)) if leaf_ids else None
        out = []
        generated: set[tuple[str, int]] = set()
        try:
            from .boilerplate import boilerplate_of_text
            generated = boilerplate_of_text(requirement_text or "")
        except Exception:
            pass
        for leaf in pool:
            if wanted is not None and str(leaf.get("id")) not in wanted:
                continue
            for index, scenario in enumerate(leaf.get("scenarios") or []):
                if not isinstance(scenario, dict):
                    continue
                try:
                    plan = scenario_plan(scenario, leaf, ctx)
                except Exception as exc:
                    plan = {"leaf": str(leaf.get("id")), "scenario": _flat(scenario.get("name")), "steps": [],
                            "entities": [], "unresolved": 1, "implied": 0, "start": {"page": "/", "auth": "unknown"},
                            "sessions": 1, "actor": None, "notes": [f"plan failed: {exc!r}"]}
                plan["index"] = index
                plan["generated"] = (str(leaf.get("id")), index) in generated
                out.append(plan)
        return out
    except Exception:
        return []

def nav_targets(plan: dict[str, Any], statuses: tuple[str, ...] = ("resolved", "implied", "conditional")) -> list[dict[str, Any]]:
    return [s for s in plan.get("steps") or [] if s["action"] in ("open", "click", "menu") and s.get("target") and s["status"] in statuses]

def licensed_names(plan: dict[str, Any], ctx: Context | None = None) -> set[str]:
    names: set[str] = set()
    for s in plan.get("steps") or []:
        for n in [s.get("target")] + list(s.get("alternatives") or []) + list(s.get("names") or []) + list(s.get("fields") or []):
            if n and n != "/":
                names.add(_flat(n).lower())
        if s["action"] == "sign_in":
            names.add("__sign_in__")
    if ctx is not None and any(s["action"] == "sign_in" for s in plan.get("steps") or []):
        for k in ("entry", "identity", "password", "submit"):
            if ctx.signin.get(k):
                names.add(ctx.signin[k].lower())
    return names

def render_plan(plan: dict[str, Any]) -> str:
    head = f'{plan.get("scenario")}: start {plan.get("start", {}).get("page", "/")} ({plan.get("start", {}).get("auth", "unknown")})'
    if plan.get("sessions", 1) > 1:
        head += f", {plan['sessions']} browser sessions"
    lines = [head]
    if plan.get("entities"):
        lines.append("  records: " + ", ".join(f'{e["kind"]} "{e["name"]}"' for e in plan["entities"][:8]))
    for s in plan.get("steps") or []:
        if s["action"] in ("observe",):
            continue
        what = s["action"]
        if s["action"] == "sign_in":
            acc = s.get("account") or {}
            target = f'sign in as {acc.get("account") or acc.get("email") or "?"}'
            if s.get("target"):
                target += f' (entry: {s.get("role") or "control"} "{s["target"]}")'
        elif s["action"] == "input":
            target = "input " + ", ".join(f'"{f}"' for f in s.get("fields") or []) if s.get("fields") else "input (" + _flat(s.get("text"))[:60] + ")"
        elif s["action"] in ("press", "select_cell"):
            target = f'{what} {s.get("target")}'
        elif s.get("target"):
            target = f'{what} {(s.get("role") + " ") if s.get("role") else ""}"{s["target"]}"'
            if s.get("scope") and s["scope"].get("name"):
                target += f' in the "{s["scope"]["name"]}" {s["scope"]["kind"]}'
            if s.get("entity"):
                target += f' ({s["entity"]["kind"]})'
            if s.get("alternatives"):
                target += " or " + " or ".join(f'"{a}"' for a in s["alternatives"])
        else:
            target = f'{what} ?'
        mark = {"resolved": "", "n/a": "", "implied": " [implied]", "conditional": " [if shown]",
                "unresolved": f' [UNRESOLVED: {s.get("why") or "target unknown"}]', "described": " [destination]"}.get(s["status"], "")
        session = f' [browser {s["session"]}]' if plan.get("sessions", 1) > 1 else ""
        lines.append(f'  {s["n"]}. {s["part"]}: {target}{mark}{session}')
    return "\n".join(lines)

def render_plans(plans: list[dict[str, Any]]) -> str:
    return "\n".join(render_plan(p) for p in plans if not p.get("generated"))

if __name__ == "__main__":
    import sys
    from pathlib import Path
    txt = Path(sys.argv[1]).read_text(encoding="utf-8")
    for p in plans_for(txt, sys.argv[2:] or None):
        print(render_plan(p))
