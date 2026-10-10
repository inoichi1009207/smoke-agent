from __future__ import annotations

import re
from typing import Any, Iterable

SETTING_KEY = "findings_owner_batch_v2"
OWNER_KEY = "owner_leaf"
NOTE_ITEM_MIN_CHARS = 240
NOTE_BUDGET_CHARS = 3200

def enabled() -> bool:
    try:
        from .settings import setting_on

        return setting_on(SETTING_KEY, True)
    except Exception:
        return True

def tag(finding: dict[str, Any], leaves: Iterable[Any]) -> dict[str, Any]:
    try:
        if isinstance(finding, dict) and enabled():
            ids = sorted({str(leaf).strip() for leaf in leaves or [] if str(leaf or "").strip()})
            if ids:
                finding[OWNER_KEY] = ids
    except Exception:
        pass
    return finding

def owner_leaves(finding: Any) -> list[str]:
    if not isinstance(finding, dict):
        return []
    raw = finding.get(OWNER_KEY)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return []
    return sorted({str(x).strip() for x in raw if str(x or "").strip()})

def split_for_owner_batches(findings: list[dict[str, Any]], future: Iterable[str]
                            ) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], list[str]]]]:
    future_set = {str(x) for x in future or []}
    send: list[dict[str, Any]] = []
    held: list[tuple[dict[str, Any], list[str]]] = []
    for finding in findings or []:
        owners = owner_leaves(finding)
        if future_set and owners and set(owners) <= future_set:
            held.append((finding, owners))
        else:
            send.append(finding)
    return send, held

def _fingerprint(finding: dict[str, Any]) -> str:
    from .audit import finding_fingerprint

    return finding_fingerprint(finding)

def book_held(book: dict[str, list[dict[str, Any]]], held: list[tuple[dict[str, Any], list[str]]]) -> dict[str, int]:
    added: dict[str, int] = {}
    for finding, owners in held:
        fp = _fingerprint(finding)
        for leaf in owners:
            rows = book.setdefault(leaf, [])
            if fp not in {_fingerprint(r) for r in rows}:
                rows.append(finding)
                added[leaf] = added.get(leaf, 0) + 1
    return added

def rows_for(book: dict[str, list[dict[str, Any]]], members: Iterable[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for member in members or []:
        for row in book.get(str(member)) or []:
            if isinstance(row, dict) and _fingerprint(row) not in seen:
                seen.add(_fingerprint(row))
                out.append(row)
    return out

def drop_rows(book: dict[str, list[dict[str, Any]]], rows: list[dict[str, Any]]) -> None:
    prints = {_fingerprint(r) for r in rows if isinstance(r, dict)}
    for leaf in list(book):
        kept = [r for r in book.get(leaf) or [] if isinstance(r, dict) and _fingerprint(r) not in prints]
        if kept:
            book[leaf] = kept
        else:
            book.pop(leaf, None)

def all_rows(book: dict[str, list[dict[str, Any]]]) -> list[tuple[dict[str, Any], str]]:
    out: list[tuple[dict[str, Any], str]] = []
    seen: set[str] = set()
    for leaf in sorted(book):
        for row in book.get(leaf) or []:
            if isinstance(row, dict) and _fingerprint(row) not in seen:
                seen.add(_fingerprint(row))
                out.append((row, leaf))
    return out

def _cut(text: Any, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: max(0, limit - 3)].rstrip() + "..."

def held_note(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    total = len(rows)
    share = max(NOTE_ITEM_MIN_CHARS, NOTE_BUDGET_CHARS // total)
    lines = [f"AUDIT CLAIMS HELD FOR THIS BATCH ({total}, all listed): while earlier batches ran, scripted checks reported "
             "these against this batch's requirement leaves before this batch was built. Build the requirement as stated; "
             "where a claim still applies to what you build, satisfy it. They are claims, not verdicts, and this batch's "
             "own audit checks them again after it is built:"]
    for i, row in enumerate(rows, start=1):
        where = _cut(row.get("where"), max(60, share // 5))
        requirement = _cut(row.get("requirement"), max(60, share // 4))
        problem = " ".join(str(row.get("problem") or "").split())
        lines.append(f"{i}/{total}. [{where}] requirement: \"{requirement}\" -> problem: {problem}")
    return "\n".join(lines)

INDEX_CACHE_MAX = 4
_INDEX_CACHE: dict[str, Any] = {}
_STATED_CACHE: dict[str, set[str]] = {}

def _flat(text: Any) -> str:
    return " ".join(str(text or "").split())

def _strings(value: Any, out: list[str]) -> None:
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key != "children":
                _strings(item, out)
    elif isinstance(value, list):
        for item in value:
            _strings(item, out)

def _build_index(requirement: str) -> list[tuple[str, list[str], str]] | None:
    try:
        import yaml

        loader = getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader
        tree = yaml.load(str(requirement or ""), Loader=loader)
    except Exception:
        return None
    if not isinstance(tree, dict):
        return None
    index: list[tuple[str, list[str], str]] = []

    def walk(node: dict[str, Any]) -> list[str]:
        raw = node.get("children")
        children = [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []
        node_id = str(node.get("id") or "").strip()
        if children:
            leaves: list[str] = []
            for child in children:
                leaves += walk(child)
        else:
            leaves = [node_id] if node_id and node_id != "ROOT" else []
        texts: list[str] = []
        _strings(node, texts)
        index.append((node_id, sorted(set(leaves)), _flat("\n".join(texts))))
        return leaves

    walk(tree)
    return index

def requirement_index(requirement: str) -> list[tuple[str, list[str], str]] | None:
    import hashlib

    key = hashlib.sha1(str(requirement or "").encode("utf-8", "replace")).hexdigest()
    if key not in _INDEX_CACHE:
        if len(_INDEX_CACHE) >= INDEX_CACHE_MAX:
            _INDEX_CACHE.pop(next(iter(_INDEX_CACHE)))
        _INDEX_CACHE[key] = _build_index(requirement)
    return _INDEX_CACHE[key]

def _name_body(name: str) -> str:
    return r"\s+".join(re.escape(word) for word in _flat(name).split(" "))

def name_owners(requirement: str, names: Iterable[Any], inherited: Iterable[Any] = ()) -> list[str]:
    try:
        if not enabled():
            return []
        wanted = [_flat(n) for n in names or [] if _flat(n)]
        if not wanted:
            return []
        inherited_set = {_flat(n).lower() for n in inherited or [] if _flat(n)}
        if any(n.lower() in inherited_set for n in wanted):
            return []
        index = requirement_index(requirement)
        if not index:
            return []
        owners: set[str] = set()
        for name in wanted:
            pattern = re.compile(r"(?<![A-Za-z0-9])" + _name_body(name) + r"(?![A-Za-z0-9])", re.IGNORECASE)
            hits = [leaves for _node, leaves, text in index if pattern.search(text)]
            if not hits or any(not leaves for leaves in hits):
                return []
            for leaves in hits:
                owners.update(leaves)
        return sorted(owners)
    except Exception:
        return []

def _stated_names(requirement: str) -> set[str]:
    import hashlib

    key = hashlib.sha1(str(requirement or "").encode("utf-8", "replace")).hexdigest()
    if key not in _STATED_CACHE:
        if len(_STATED_CACHE) >= INDEX_CACHE_MAX:
            _STATED_CACHE.pop(next(iter(_STATED_CACHE)))
        try:
            from .stated_controls import extract_stated_controls

            _STATED_CACHE[key] = {_flat(s.get("name")).lower() for s in extract_stated_controls(requirement)[0]}
        except Exception:
            _STATED_CACHE[key] = set()
    return _STATED_CACHE[key]

def stated_name_owners(requirement: str, name: Any, inherited: Iterable[Any] = ()) -> list[str]:
    try:
        if not enabled() or not _flat(name):
            return []
        index = requirement_index(requirement)
        if not index:
            return []
        quoted = re.compile(r"[`\"“「『]\s*" + _name_body(str(name)) + r"\s*[`\"”」』]", re.IGNORECASE)
        if not any(quoted.search(text) for _node, _leaves, text in index) and _flat(name).lower() not in _stated_names(requirement):
            return []
        return name_owners(requirement, [name], inherited)
    except Exception:
        return []

