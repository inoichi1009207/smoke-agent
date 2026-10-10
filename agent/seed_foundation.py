from __future__ import annotations

import re
from typing import Any

PRE = re.compile(r"(?i)\bpre-?provision(?:s|ed|ing)?\b")
LITERAL = re.compile(r"`([^`\n]{1,120})`")
SECRET_BEFORE = re.compile(r"(?i)pass(?:word|code|phrase)?\b[^`]{0,24}$")

RECORD_NOUN = (r"workbooks?|worksheets?|sheets?|accounts?|users?|usernames?|logins?|organi[sz]ations?|orgs?|teams?|"
               r"repositor(?:y|ies)|repos?|branch(?:es)?|issues?|milestones?|pull\s+requests?|PRs?|releases?|labels?|tags?")
RECORD_BEFORE = re.compile(r"(?i)\b(?:" + RECORD_NOUN + r")(?:\s+(?:named|called|titled|with\s+(?:the\s+)?(?:user)?name))?\s*$")
VERB_BEFORE = re.compile(r"(?i)\bpre-?provision(?:s|ed)?\s+(?:the\s+|an?\s+)?$")
LIST_CONTINUES = re.compile(r"(?i)^\s*(?:,\s*(?:and\s+)?|\s+and\s+|\s*/\s*)$")
CELLISH = re.compile(r"^=|^\$?[A-Z]{1,3}\$?\d{1,5}(?::\$?[A-Z]{1,3}\$?\d{1,5})?$")
NUMBERISH = re.compile(r"^[\d.,\s%$+-]+$")
ROOT_RULE = re.compile(r"(?i)pre-?provision|seed data|seed configuration|evaluation seed|predefined")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

def _flat(text: Any) -> str:
    return " ".join(str(text or "").split())

def _sentences(text: str) -> list[str]:
    masked = LITERAL.sub(lambda m: "`" + m.group(1).replace(".", "\x00") + "`", text)
    return [s.replace("\x00", ".").strip() for s in SENTENCE_END.split(masked) if s.strip()]

def classify_literals(sentence: str) -> tuple[list[str], list[str]]:
    hard: list[str] = []
    soft: list[str] = []
    prev_end, prev_hard = 0, False
    for m in LITERAL.finditer(sentence):
        value = m.group(1).strip()
        between = sentence[prev_end:m.start()]
        before = between[-70:]
        is_hard = False
        if not value or SECRET_BEFORE.search(sentence[max(0, m.start() - 40):m.start()]):
            prev_end, prev_hard = m.end(), False
            continue
        if "@" in value or CELLISH.search(value) or NUMBERISH.fullmatch(value):
            is_hard = False
        elif RECORD_BEFORE.search(before) or VERB_BEFORE.search(before):
            is_hard = True
        elif prev_hard and prev_end and LIST_CONTINUES.match(between):
            is_hard = True
        target = hard if is_hard else soft
        if value not in hard and value not in target:
            target.append(value)
        prev_end, prev_hard = m.end(), is_hard
    soft = [v for v in soft if v not in hard]
    return hard, soft

NOUN_AT_END = re.compile(r"(?i)\b(" + RECORD_NOUN + r")(?:\s+(?:named|called|titled|with\s+(?:the\s+)?(?:user)?name))?\s*$")
KIND_OF_NOUN = ((re.compile(r"(?i)^workbooks?$"), "workbook"),
                (re.compile(r"(?i)^(?:organi[sz]ations?|orgs?)$"), "organization"),
                (re.compile(r"(?i)^(?:repositor(?:y|ies)|repos?)$"), "repository"),
                (re.compile(r"(?i)^(?:accounts?|users?|usernames?|logins?)$"), "account"),
                (re.compile(r"(?i)^teams?$"), "team"))
GLOBAL_KINDS = frozenset(kind for _, kind in KIND_OF_NOUN)

def _noun_kind(noun: str) -> str:
    noun = " ".join(noun.split())
    return next((kind for rx, kind in KIND_OF_NOUN if rx.match(noun)), "per-parent")

CONFLICT = "conflict"

def _merge_kind(a: str, b: str) -> str:
    if CONFLICT in (a, b):
        return CONFLICT
    if not a or not b:
        return a or b
    return a if a == b else CONFLICT

def sentence_kinds(sentence: str) -> dict[str, str]:
    hard = set(classify_literals(sentence)[0])
    out: dict[str, str] = {}
    prev_end, prev_kind = 0, None
    for m in LITERAL.finditer(sentence):
        value = m.group(1).strip()
        between = sentence[prev_end:m.start()]
        kind = None
        if value in hard:
            noun = NOUN_AT_END.search(between[-70:])
            if noun:
                kind = _noun_kind(noun.group(1))
            elif prev_kind is not None and prev_end and LIST_CONTINUES.match(between):
                kind = prev_kind
            else:
                kind = ""
            out[value] = _merge_kind(out[value], kind) if value in out else kind
        prev_end, prev_kind = m.end(), kind
    return out

def name_kinds(inv: dict[str, Any] | None) -> dict[str, str]:
    try:
        merged: dict[str, str] = {}
        for row in (inv or {}).get("scenarios") or []:
            for sentence in row.get("sentences") or []:
                for name, kind in sentence_kinds(str(sentence)).items():
                    merged[name] = _merge_kind(merged[name], kind) if name in merged else kind
        return {name: ("" if kind == CONFLICT else kind) for name, kind in merged.items()}
    except Exception:
        return {}

def global_names(inv: dict[str, Any] | None, names=None) -> dict[str, str]:
    kinds = name_kinds(inv)
    wanted = None if names is None else {str(n).strip() for n in names}
    return {n: k for n, k in kinds.items() if k in GLOBAL_KINDS and (wanted is None or n in wanted)}

def _leaves(tree: dict[str, Any]) -> dict[str, dict[str, Any]]:
    from .evolution import tree_index
    return tree_index(tree)[0] if isinstance(tree, dict) else {}

def inventory(tree: dict[str, Any] | None, leaf_ids) -> dict[str, Any]:
    out: dict[str, Any] = {"scenarios": [], "root": [], "names": [], "advisory": [], "extent": {"grid": False, "col": 0, "row": 0, "cell": ""}}
    try:
        tree = tree if isinstance(tree, dict) else {}
        out["root"] = [s for s in _sentences(_flat(tree.get("description"))) if ROOT_RULE.search(s)]
        leaves = _leaves(tree)
        texts: list[str] = []
        for leaf_id in [str(x) for x in leaf_ids or []]:
            leaf = leaves.get(leaf_id)
            if not isinstance(leaf, dict):
                continue
            texts.append(_flat(leaf.get("description")))
            for scenario in leaf.get("scenarios") or []:
                if not isinstance(scenario, dict):
                    continue
                sentences: list[str] = []
                names: list[str] = []
                advisory: list[str] = []
                for step in scenario.get("steps") or []:
                    if not isinstance(step, dict):
                        continue
                    content = _flat(step.get("content"))
                    texts.append(content)
                    if str(step.get("keyword") or "").strip().upper() != "GIVEN":
                        continue
                    for sentence in _sentences(content):
                        if not PRE.search(LITERAL.sub(" ", sentence)):
                            continue
                        sentences.append(sentence)
                        hard, soft = classify_literals(sentence)
                        names += [n for n in hard if n not in names]
                        advisory += [n for n in soft if n not in advisory]
                if sentences:
                    advisory = [n for n in advisory if n not in names]
                    out["scenarios"].append({"leaf": leaf_id, "scenario": _flat(scenario.get("name")) or leaf_id,
                                             "sentences": sentences, "names": names, "advisory": advisory})
        for row in out["scenarios"]:
            out["names"] += [n for n in row["names"] if n not in out["names"]]
        for row in out["scenarios"]:
            out["advisory"] += [n for n in row["advisory"] if n not in out["advisory"] and n not in out["names"]]
        from .grid_probes import col_letters, requirement_extent

        ext = requirement_extent("\n".join(texts))
        if ext.get("grid") and ext.get("col") and ext.get("row"):
            out["extent"] = {"grid": True, "col": int(ext["col"]), "row": int(ext["row"]),
                             "cell": f"{col_letters(int(ext['col']))}{int(ext['row'])}", "refs": list(ext.get("refs") or [])[:30],
                             "phrases": list(ext.get("phrases") or [])[:10]}
    except Exception:
        return {"scenarios": [], "root": [], "names": [], "advisory": [], "extent": {"grid": False, "col": 0, "row": 0, "cell": ""}}
    return out

def scenario_names(inv: dict[str, Any], leaf_ids=None) -> dict[str, list[str]]:
    wanted = None if leaf_ids is None else {str(x) for x in leaf_ids}
    out: dict[str, list[str]] = {}
    for row in (inv or {}).get("scenarios") or []:
        if wanted is not None and row.get("leaf") not in wanted or not row.get("names"):
            continue
        key, i = str(row.get("scenario")), 2
        while key in out:
            key, i = f"{row.get('scenario')} ({i})", i + 1
        out[key] = list(row["names"])
    return out

BLOCK_HEAD = ("EVOLUTION PRE-PROVISIONED RECORDS (the requirement's own GIVEN sentences; each scenario uses its own set; "
              "these records exist before any test, under exactly these names -- the stated name is the record's own "
              "name / title, never an alias -- and are never removed or renamed by a batch):")

def batch_block(inv: dict[str, Any], members, cap: int = 9000) -> str:
    rows = list((inv or {}).get("scenarios") or [])
    if not rows:
        return ""
    mine = {str(m) for m in members or []}
    lines: list[str] = []
    used = 0
    own_names: list[str] = []
    for row in rows:
        if row.get("leaf") not in mine:
            continue
        for sentence in row.get("sentences") or []:
            line = f"- ({row.get('leaf')} / {row.get('scenario')}) {sentence}"
            if used + len(line) > cap:
                break
            if line not in lines:
                lines.append(line)
                used += len(line)
        own_names += [n for n in row.get("names") or [] if n not in own_names]
    others = [n for n in (inv or {}).get("names") or [] if n not in own_names]
    if others:
        lines.append("- Records other scenarios of this round pre-provision (keep them exactly, never rename or remove): "
                     + ", ".join(f"`{n}`" for n in others))
    root = list((inv or {}).get("root") or [])
    head = BLOCK_HEAD + ("\n  Requirement rule: " + " ".join(root) if root else "")
    return head + "\n" + "\n".join(lines) if lines else ""

IDEMPOTENT_SENTENCE = ("The seed foundation may already have created the pre-provisioned records of this round. Only "
                       "inside the first run of a migration that is not yet applied, before it adds such a record, it looks "
                       "for a record of the same kind under the same owner or parent with the stated name and reuses it; it "
                       "never creates a second record of the same kind with the same name under the same owner or parent. "
                       "Records under different owners or parents (such as worksheets of different workbooks) may share a "
                       "name. Whether a migration was applied is always decided by its stable version key, never afterwards "
                       "by a name a user can rename; once applied, it never runs again.")

SEED_EXCEPTIONS = ("except where a later requirement explicitly updates the same record or grant (a grant stated for the "
                   "same subject and resource updates it), and except removing a second copy named in a duplicate-fixture "
                   "finding once you have confirmed it has the same kind, name and owner or parent")
KEEP_SEED_RULE = "- Never change, rename or remove any existing seed record; add next to it."

def keep_seed_rule() -> str:
    from .settings import setting_on

    if not setting_on("fixture_idempotent"):
        return KEEP_SEED_RULE
    return KEEP_SEED_RULE[:-1] + ", " + SEED_EXCEPTIONS + "."

def idempotent_head() -> str:
    from .settings import setting_on

    return (" " + IDEMPOTENT_SENTENCE) if setting_on("fixture_idempotent") else ""

VISIBLE_NAME_RULE = ("- A pre-provisioned record the requirement names only by an identifier: the name under which it is "
                     "listed and opened (list entries, links, page headings that name the record) is exactly that "
                     "identifier, unless the requirement states another display name or a composed title (such as "
                     "owner/name) for it. This applies to the seeded state only -- later renames by users follow their "
                     "own requirements.")

def foundation_text(inv: dict[str, Any], cap: int = 60000, visible: bool = False) -> str:
    parts = ["SEED FOUNDATION (before any feature of this round is built): add ALL of the pre-provisioned records listed "
             "below as ONE versioned, idempotent data migration next to the existing seed (e.g. key `evo_v1`, run once "
             "inside one transaction, recorded under that stable key; it runs on an empty database AND on the inherited "
             "application's existing database). Rules:",
             keep_seed_rule(),
             "- The stated name is the record's OWN name / title / username exactly as written (keep its letter case and "
             "spacing) -- never an alias, code or slug column next to another display name.",
             *([VISIBLE_NAME_RULE] if visible else []),
             "- Roles, memberships, ownership, visibility, branches, state and contents exactly as the sentence states; "
             "records several scenarios share are created once and referenced; each scenario's records stay independent "
             "of the others.",
             "- Do NOT implement any feature, page, route or control here; only data. A record whose kind the "
             "application has no table for yet is left out (the batch that builds that feature adds it).",
             "- A user who renames or deletes a seeded record must not see it come back after a restart."]
    ext = (inv or {}).get("extent") or {}
    if ext.get("grid") and ext.get("cell"):
        parts.append(f"- The worksheet grid must render, scroll to and accept input in every cell up to {ext['cell']} "
                     f"(column {ext['cell'].rstrip('0123456789')}, row {ext['row']}): the scenarios address cells that far "
                     "(e.g. " + ", ".join((ext.get("refs") or [])[-6:]) + "). Keep the grid's existing behaviour otherwise.")
    root = list((inv or {}).get("root") or [])
    if root:
        parts.append("Requirement rule: " + " ".join(root))
    parts.append("Pre-provisioned records, by scenario (the requirement's own sentences):")
    used = sum(len(p) for p in parts) - (len(VISIBLE_NAME_RULE) if visible else 0)
    for row in (inv or {}).get("scenarios") or []:
        line = f"- {row.get('leaf')} / {row.get('scenario')}: " + " ".join(row.get("sentences") or [])
        if used + len(line) > cap:
            parts.append("- ... (more scenarios: see the requirement's GIVEN steps that say 'pre-provisions')")
            break
        parts.append(line)
        used += len(line)
    return "\n".join(parts)

def summary(inv: dict[str, Any]) -> str:
    ext = (inv or {}).get("extent") or {}
    return (f"[seed-foundation] {len((inv or {}).get('scenarios') or [])} scenario(s), {len((inv or {}).get('names') or [])} "
            f"record name(s) ({len((inv or {}).get('advisory') or [])} advisory literal(s)), sheet extent "
            + (str(ext.get("cell")) if ext.get("grid") else "none"))
