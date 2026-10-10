from __future__ import annotations

import functools
import math
import re
from typing import Any, NamedTuple

MIN_LEAVES = 5
MIN_SHARE = 0.10

BACKTICKED = re.compile(r"`[^`]*`")
QUOTED = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”")
REQ_ID = re.compile(r"\bREQ[-_.]?\d+(?:[.-]\d+)*\b", re.IGNORECASE)
NON_WORD = re.compile(r"[\W_]+")

MIN_STEP_CHARS = 40
SLOT = "\x00"
RESTATING_PARENS = re.compile(r"\([^()]*`[^()]*\)")
SENTENCE_END = re.compile(r"(?<=[.;!?])\s+")
SEED_SENTENCE = re.compile(r"\bseed values\b|\bseeded data\b|\bseed data\b|\bevaluation seed\b|\bseed contains\b")
LABELLED_SLOT = re.compile(r"\b[a-z][a-z-]*\s+" + SLOT)
SLOT_RUN = re.compile(SLOT + r"(?:[\s,;:/]*(?:\b(?:and|or|with|containing)\b)?[\s,;:/]*" + SLOT + r")+")

def threshold(leaf_count: int) -> int:
    return max(MIN_LEAVES, math.ceil(MIN_SHARE * leaf_count))

def leaves_of(tree: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        children = [c for c in node.get("children") or [] if isinstance(c, dict)] if isinstance(node.get("children"), list) else []
        if children:
            for child in children:
                walk(child)
        elif str(node.get("id") or "").strip() not in ("", "ROOT"):
            found.append(node)

    walk(tree)
    return found

def _flat(text: Any) -> str:
    return " ".join(str(text or "").split())

def _phrases(leaf: dict[str, Any], scenario: dict[str, Any]) -> list[str]:
    name = REQ_ID.sub(" ", _flat(scenario.get("name")))
    name = re.sub(r"^[\s:.\-–—]+", "", name).strip()
    phrases = [_flat(leaf.get("name")), name]
    for step in _steps(scenario):
        for quoted in QUOTED.findall(_flat(step[1])):
            inner = quoted[1:-1].strip()
            if len(inner.split()) >= 3:
                phrases.append(inner)

    return sorted({p.lower() for p in phrases if len(p) >= 4}, key=len, reverse=True)

def _steps(scenario: dict[str, Any]) -> list[tuple[str, str]]:
    raw = scenario.get("steps") if isinstance(scenario, dict) else None
    out: list[tuple[str, str]] = []
    kind = ""
    for step in raw if isinstance(raw, list) else []:
        if not isinstance(step, dict):
            continue
        keyword = _flat(step.get("keyword")).upper()
        for main in ("GIVEN", "WHEN", "THEN"):
            if main in keyword.split():
                kind = main
                break
        out.append((kind or keyword, str(step.get("content") or "")))
    return out

def normalise(text: str, leaf: dict[str, Any], phrases: list[str]) -> str:
    flat = _flat(text).lower()
    flat = REQ_ID.sub(" ", flat)
    leaf_id = _flat(leaf.get("id")).lower()
    if leaf_id:
        flat = flat.replace(leaf_id, " ")
    for phrase in phrases:
        flat = flat.replace(phrase, " ")
    flat = RESTATING_PARENS.sub(" ", flat)
    flat = BACKTICKED.sub(SLOT, flat)
    flat = QUOTED.sub(SLOT, flat)
    flat = " ".join(s for s in SENTENCE_END.split(flat) if not SEED_SENTENCE.search(s))
    flat = LABELLED_SLOT.sub(SLOT, flat)
    flat = SLOT_RUN.sub(SLOT, flat)
    return " ".join(NON_WORD.sub(" ", flat.replace(SLOT, " value ")).split())

def scenarios_of(leaf: Any) -> list[tuple[int, dict[str, Any]]]:
    scenarios = leaf.get("scenarios") if isinstance(leaf, dict) else None
    return [(i, s) for i, s in enumerate(scenarios) if isinstance(s, dict)] if isinstance(scenarios, list) else []

def _table(tree: dict[str, Any]) -> tuple[list[tuple[dict[str, Any], int, list[tuple[str, str]]]], dict[str, set[str]], int]:
    leaves = leaves_of(tree)
    rows = []
    spread: dict[str, set[str]] = {}
    for leaf in leaves:
        for index, scenario in scenarios_of(leaf):
            phrases = _phrases(leaf, scenario)
            steps = [(kind, normalise(content, leaf, phrases)) for kind, content in _steps(scenario)]
            rows.append((leaf, index, steps))
            for _kind, text in steps:
                if text:
                    spread.setdefault(text, set()).add(str(leaf.get("id")))
    return rows, spread, len(leaves)

def boilerplate_scenarios(tree: dict[str, Any]) -> set[tuple[str, int]]:
    if not isinstance(tree, dict):
        return set()
    rows, spread, leaf_count = _table(tree)
    k = threshold(leaf_count)
    found: set[tuple[str, int]] = set()
    for leaf, index, steps in rows:
        acting = [(kind, text) for kind, text in steps if kind in ("WHEN", "THEN")]
        if not any(kind == "WHEN" for kind, _ in acting) or not any(kind == "THEN" for kind, _ in acting):
            continue
        if all(len(text) >= MIN_STEP_CHARS and len(spread.get(text, ())) >= k for _kind, text in acting):
            found.add((str(leaf.get("id")), index))
    return found

def leaf_is_all_boilerplate(node: dict[str, Any], tree: dict[str, Any]) -> bool:
    scenarios = scenarios_of(node)
    if not scenarios:
        return False
    found = boilerplate_scenarios(tree)
    leaf_id = str(node.get("id"))
    return all((leaf_id, index) in found for index, _s in scenarios)

def _labels(scenarios: list[tuple[int, dict[str, Any]]]) -> tuple[str, ...]:
    names = [str(s.get("name") or "") for _i, s in scenarios]
    return tuple(f"{name} (scenario {index + 1})" if names.count(name) > 1 else name
                 for name, (index, _s) in zip(names, scenarios))

@functools.lru_cache(maxsize=4)
def _analysis(text: str) -> tuple[frozenset[tuple[str, int]], tuple[tuple[str, tuple[int, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...]]:
    empty: tuple = (frozenset(), ())
    try:
        import yaml
        tree = yaml.safe_load(text)
    except Exception:
        return empty
    if not isinstance(tree, dict):
        return empty
    try:
        found = frozenset(boilerplate_scenarios(tree))
        leaves = []
        for leaf in leaves_of(tree):
            leaf_id = str(leaf.get("id"))
            scenarios = scenarios_of(leaf)
            indexes = tuple(i for i, _s in scenarios)
            names = tuple(str(s.get("name") or "") for _i, s in scenarios)
            steps = tuple(_flat(content) for i, s in scenarios if (leaf_id, i) in found for _kind, content in _steps(s))
            leaves.append((leaf_id, indexes, names, _labels(scenarios), steps))
        return found, tuple(leaves)
    except Exception:
        return empty

def boilerplate_of_text(text: str) -> set[tuple[str, int]]:
    return set(_analysis(text)[0]) if text and text.strip() else set()

class LeafReport(NamedTuple):
    leaf_id: str
    names: list[str]
    generated: int
    total: int
    steps: list[str]
    labels: list[str] = []
    indexes: list[int] = []

    @property
    def all_generated(self) -> bool:
        return self.total > 0 and self.generated == self.total

def batch_report(text: str, leaf_ids: list[str]) -> list[LeafReport]:
    if not text or not text.strip():
        return []
    found, leaves = _analysis(text)
    if not found:
        return []
    by_id = {leaf_id: rest for leaf_id, *rest in leaves}
    report = []
    for leaf_id in dict.fromkeys(str(i) for i in leaf_ids):
        indexes, names, labels, steps = by_id.get(leaf_id, ((), (), (), ()))
        flags = [(leaf_id, i) in found for i in indexes]
        if any(flags):
            report.append(LeafReport(leaf_id, list(dict.fromkeys(n for n, f in zip(names, flags) if f)),
                                     sum(flags), len(indexes), list(steps),
                                     [lab for lab, f in zip(labels, flags) if f], [i for i, f in zip(indexes, flags) if f]))
    return report
