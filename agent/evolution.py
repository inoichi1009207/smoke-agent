from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

MODIFIED_HEAD = "Modified Feature Description"
ORIGINAL_HEAD = "Original Feature Description"
EVOLUTION_KEY = "evolution"
EFFECTIVE_RULES_KEY = "effective_rules"
EVO_SUFFIX = ".evo"
SPEC_DIR = "backend/test-e2e"
INHERITED_YAML = "requirements/requirements.yaml"
CONTEXT_CAP = 3000
DEPENDENCY_CAP = 1200
NOTE_CAP = 3600

TITLE_ID = re.compile(r"^\s*(?:DESC-)?(REQ(?:-\d+)+)\b")
TEST_CALL = re.compile(r"""(?m)^[ \t]*test(?:\.(?:only|fixme|slow))?\(\s*(["'`])((?:\\.|(?!\1).){1,300})\1""")
FILE_ID = re.compile(r"^(?:DESC-)?(REQ(?:-\d+)+)")

def tree_index(tree: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, str]]:
    leaves: dict[str, dict[str, Any]] = {}
    nodes: dict[str, dict[str, Any]] = {}
    parents: dict[str, str] = {}

    def walk(node: Any, parent: str) -> None:
        if not isinstance(node, dict):
            return
        node_id = str(node.get("id") or "").strip()
        children = [c for c in node.get("children") or [] if isinstance(c, dict)]
        if node_id and node_id != "ROOT":
            nodes[node_id] = node
            parents[node_id] = parent
            if not children:
                leaves[node_id] = node
        for child in children:
            walk(child, node_id)

    walk(tree, "")
    return leaves, nodes, parents

def split_modified(description: str) -> tuple[str, str] | None:
    text = str(description or "")
    if MODIFIED_HEAD not in text:
        return None
    original, modified = text.split(MODIFIED_HEAD, 1)
    original = original.replace(ORIGINAL_HEAD, "", 1)
    return original.strip(), modified.strip()

def leaf_content(node: dict[str, Any]) -> str:
    return json.dumps({"name": node.get("name"), "description": str(node.get("description") or ""),
                       "scenarios": node.get("scenarios") or []}, sort_keys=True, ensure_ascii=False, default=str)

def content_sha(node: dict[str, Any]) -> str:
    return hashlib.sha256(leaf_content(node).encode("utf-8")).hexdigest()[:16]

def inherited_spec_files(project_dir: Path) -> list[str]:
    folder = Path(project_dir) / SPEC_DIR
    if not folder.is_dir():
        return []
    return sorted(p.relative_to(project_dir).as_posix() for p in folder.glob("*.spec.js") if p.is_file())

def preservation(project_dir: Path, template_dir: Path) -> dict[str, Any]:
    project_dir, template_dir = Path(project_dir), Path(template_dir)
    signals: dict[str, Any] = {}
    try:
        from .continuation import sources_beyond_template
        signals["handoff"] = (project_dir / ".arc-handoff" / "handoff.json").is_file()
        signals["specs"] = len(inherited_spec_files(project_dir))
        signals["summary"] = (project_dir / ".arc" / "agent-summary.json").is_file()
        signals["inherited_yaml"] = (project_dir / INHERITED_YAML).is_file()
        beyond = sources_beyond_template(project_dir, template_dir)
        signals["beyond"] = len(beyond)
        signals["beyond_sample"] = beyond[:8]

        preserve = bool(beyond) and (bool(signals["handoff"]) or signals["specs"] > 0 or bool(signals["summary"]))
        why = (f"{len(beyond)} source file(s) beyond the template"
               + (", a handoff of an earlier run" if signals["handoff"] else "")
               + (f", {signals['specs']} spec file(s)" if signals["specs"] else "")
               + (", an earlier run's summary" if signals["summary"] else "")) if preserve else (
               "no earlier run's application (no sources beyond the template next to a handoff, specs or a summary)")
        return {"preserve": preserve, "signals": signals, "reason": why}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    try:
        cheap = (project_dir / ".arc-handoff" / "handoff.json").is_file() or bool(inherited_spec_files(project_dir))
        if not cheap and not any((project_dir / top).is_dir() for top in ("frontend/src", "backend/src")):
            return {"preserve": False, "signals": signals, "reason": f"scan failed ({error}); the folder holds no sources"}
    except Exception:
        pass
    return {"preserve": True, "signals": signals, "reason": f"scan failed ({error}); preserved (fail-safe)"}

def inherited_requirement(project_dir: Path, this_sha: str) -> tuple[dict[str, Any] | None, str]:
    path = Path(project_dir) / INHERITED_YAML
    if not path.is_file():
        return None, f"no {INHERITED_YAML} in the inherited folder"
    try:
        raw = path.read_bytes()
        if this_sha and hashlib.sha256(raw).hexdigest() == this_sha:
            return None, f"{INHERITED_YAML} is this run's own requirement"
        import yaml
        data = yaml.safe_load(raw.decode("utf-8", errors="replace"))
    except Exception as exc:
        return None, f"{INHERITED_YAML} unreadable ({type(exc).__name__})"
    if not isinstance(data, dict) or str(data.get("id") or "").strip() != "ROOT":
        return None, f"{INHERITED_YAML} is not a requirement tree"
    return data, f"{INHERITED_YAML} (sha256 {hashlib.sha256(raw).hexdigest()[:12]})"

def spec_tests(text: str) -> list[dict[str, Any]]:
    found = []
    for match in TEST_CALL.finditer(text or ""):
        title = match.group(2).replace("\\" + match.group(1), match.group(1))
        found.append({"title": title, "line": text.count("\n", 0, match.start()) + 1})
    return found

def _read(path: Path, limit: int = 600_000) -> str:
    try:
        return path.read_bytes()[:limit].decode("utf-8", errors="replace")
    except OSError:
        return ""

def historical_inventory(tree: dict[str, Any], handoff: dict[str, Any] | None, inherited_tree: dict[str, Any] | None,
                         project_dir: Path | None = None, specs: list[str] | None = None) -> dict[str, Any]:
    leaves: dict[str, dict[str, Any]] = {}

    def add(leaf_id: str, source: str, name: str = "", status: str | None = None, content: str | None = None) -> None:
        entry = leaves.setdefault(leaf_id, {"name": "", "sources": [], "status": None, "content": None})
        if source not in entry["sources"]:
            entry["sources"].append(source)
        entry["name"] = entry["name"] or name[:160]
        if status and not entry["status"]:
            entry["status"] = status
        if content and not entry["content"]:
            entry["content"] = content

    sources = {"handoff": False, "handoff_inventory": 0, "inherited_yaml": False, "spec_titles": 0}
    if isinstance(handoff, dict):
        sources["handoff"] = True
        for record in handoff.get("leaves") or []:
            if isinstance(record, dict) and record.get("id"):
                add(str(record["id"]), "handoff", str(record.get("name") or ""), str(record.get("status") or "") or None)
        for leaf_id in handoff.get("leaf_ids") or []:
            add(str(leaf_id), "handoff")
        for record in handoff.get("inventory") or []:
            if isinstance(record, dict) and record.get("id"):
                sources["handoff_inventory"] += 1
                add(str(record["id"]), "handoff inventory", str(record.get("name") or ""), str(record.get("status") or "") or None)
    if isinstance(inherited_tree, dict):
        sources["inherited_yaml"] = True
        for leaf_id, node in tree_index(inherited_tree)[0].items():
            add(leaf_id, "inherited requirement", str(node.get("name") or ""), content=content_sha(node))
    known = set(leaves)
    complete = bool(sources["handoff"] or sources["inherited_yaml"])
    this_leaves, this_nodes, _ = tree_index(tree)
    spec_only: list[str] = []
    for rel in specs or []:
        text = _read(Path(project_dir or ".") / rel) if project_dir is not None else ""
        for test in spec_tests(text):
            match = TITLE_ID.match(test["title"])
            if not match:
                continue
            sources["spec_titles"] += 1
            leaf_id = match.group(1)
            if leaf_id in known:
                add(leaf_id, "spec titles")
            elif not complete and (leaf_id in this_leaves or leaf_id not in this_nodes):
                add(leaf_id, "spec titles")
            elif leaf_id not in spec_only:
                spec_only.append(leaf_id)
    return {"leaves": leaves, "complete": complete, "sources": sources, "spec_only_ids": sorted(spec_only)}

def classify(tree: dict[str, Any], inventory: dict[str, Any], inherited_tree: dict[str, Any] | None = None) -> dict[str, Any]:
    leaves, nodes, _ = tree_index(tree)
    history = dict(inventory.get("leaves") or {})
    old_leaves = tree_index(inherited_tree)[0] if isinstance(inherited_tree, dict) else {}
    classes: dict[str, str] = {}
    unverified: dict[str, str] = {}
    unmarked: list[str] = []
    for leaf_id, node in leaves.items():
        if split_modified(str(node.get("description") or "")) is not None:
            classes[leaf_id] = "modified"
            if leaf_id not in history:
                unverified[leaf_id] = "marked modified, but the id is in no record of the inherited application"
            continue
        if leaf_id in history:
            old = old_leaves.get(leaf_id)
            if old is not None and leaf_content(old) != leaf_content(node):
                classes[leaf_id] = "modified"
                unmarked.append(leaf_id)
                continue
            classes[leaf_id] = "inherited"
            if old is None:
                unverified[leaf_id] = ("in the inherited application's records ("
                                       + ", ".join(history[leaf_id]["sources"]) + "); its text could not be compared")
            continue
        classes[leaf_id] = "new"
        if not inventory.get("complete"):
            unverified[leaf_id] = "no handoff and no inherited requirement: the inventory is the spec titles only"
    out_of_tree = sorted((i for i in history if i not in nodes), key=_natural_id)
    folders_now = [i for i in history if i in nodes and i not in leaves]
    order = list(leaves)
    return {
        "classes": classes,
        "inherited": [i for i in order if classes[i] == "inherited"],
        "modified": [i for i in order if classes[i] == "modified"],
        "new": [i for i in order if classes[i] == "new"],
        "out_of_tree": out_of_tree,
        "earlier_status": {i: history[i].get("status") for i in history if history[i].get("status")},
        "names": {i: history[i].get("name") or "" for i in history},
        "unverified": unverified,
        "unmarked_changes": unmarked,
        "split_into_folders": sorted(folders_now),
        "inventory_complete": bool(inventory.get("complete")),
        "inventory_sources": dict(inventory.get("sources") or {}),
    }

def evolution_round(classes: dict[str, Any]) -> bool:
    return bool(classes.get("inherited") or classes.get("modified"))

def to_implement(classes: dict[str, Any]) -> list[str]:
    return list(classes.get("modified") or []) + [i for i in classes.get("new") or [] if i not in (classes.get("modified") or [])]

def implementation_tree(tree: dict[str, Any], ids: list[str] | set[str]) -> dict[str, Any]:
    wanted = set(ids)

    def prune(node: dict[str, Any]) -> dict[str, Any] | None:
        children = [c for c in node.get("children") or [] if isinstance(c, dict)]
        if not children:
            return node if str(node.get("id") or "") in wanted else None
        kept = [p for p in (prune(c) for c in children) if p is not None]
        if not kept and str(node.get("id") or "") != "ROOT":
            return None
        copy = dict(node)
        copy["children"] = kept
        return copy

    return prune(tree) or {**tree, "children": []}

def _natural_id(text: str) -> list[Any]:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(text))]

def inherited_context(leaf_id: str, leaf: dict[str, Any], tree: dict[str, Any], inherited_tree: dict[str, Any] | None,
                      cap: int = CONTEXT_CAP) -> str:
    if not isinstance(inherited_tree, dict):
        return ""
    _, old_nodes, _ = tree_index(inherited_tree)
    _, nodes, parents = tree_index(tree)
    parts: list[str] = []
    current = parents.get(leaf_id, "")
    chain = []
    while current:
        chain.append(current)
        current = parents.get(current, "")
    for ancestor in reversed(chain):
        old = old_nodes.get(ancestor)
        now = " ".join(str((nodes.get(ancestor) or {}).get("description") or "").split())
        then = " ".join(str((old or {}).get("description") or "").split())
        if old is not None and len(then) > len(now) + 40 and then != now:
            parts.append(f"[{ancestor}] {old.get('name', '')} (the original round's description; still in force): "
                         + (then if len(then) <= DEPENDENCY_CAP else then[:DEPENDENCY_CAP] + " ..."))
    for dep in [str(d) for d in leaf.get("dependencies") or []]:
        if dep in nodes:
            continue
        old = old_nodes.get(dep)
        if old is None:
            continue
        text = " ".join(str(old.get("description") or "").split())
        parts.append(f"[{dep}] {old.get('name', '')} (a dependency built in the original round, not in this round's "
                     "requirement; it exists in the inherited application -- reuse it, do not rebuild it): "
                     + (text if len(text) <= DEPENDENCY_CAP else text[:DEPENDENCY_CAP] + " ..."))
    if not parts:
        return ""
    out = "INHERITED REQUIREMENT CONTEXT (evolution round):\n" + "\n".join(parts)
    return out if len(out) <= cap else out[:cap] + " ..."

STOP = set(("the and for with that this from into are any its their them then than when which while will must may can "
            "not all each every one two only also been being have has had was were such per via use uses used user users "
            "visitor visitors page system shows show display displays displayed visible visibly named name names exactly "
            "after before other same does").split())
KEEP_WORDS = re.compile(r"(?i)\b(?:keeps?|retains?|retained|remains? (?:unchanged|the same|available)|unchanged|still|"
                        r"from the original(?: feature)?)\b")
CHANGE_WORDS = re.compile(r"(?i)\b(?:changed?|changes|now|instead|no longer|replac\w*|additionally|normali[sz]ed)\b")
IMAGE_LINE = re.compile(r"(?m)^\s*(?:!\[[^\]]*\]\([^)]*\)|Screenshot reference:)\s*$")
OVERLAP_MIN = 0.3

def sentences(text: str) -> list[str]:
    text = IMAGE_LINE.sub("", str(text or ""))
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = " ".join(para.split())
        if para:
            out += [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z“\"`(])", para) if s.strip()]
    return out

def _tokens(sentence: str) -> set[str]:
    literals = {'"' + m.lower() for m in re.findall(r"[`“\"]([^`”\"]{2,80})[`”\"]", sentence)}
    words = {w for w in re.findall(r"[a-z0-9]+", sentence.lower()) if len(w) >= 3 and w not in STOP}
    return words | literals

def sentence_kind(sentence: str) -> str:
    if CHANGE_WORDS.search(sentence):
        return "change"
    if KEEP_WORDS.search(sentence):
        return "keep"
    return "rule"

def _positions(yaml_text: str, leaf_id: str) -> tuple[list[str], int, int]:
    lines = (yaml_text or "").splitlines()
    start = -1
    indent = 0
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)-?\s*id:\s*['\"]?" + re.escape(leaf_id) + r"['\"]?\s*$", line)
        if match:
            start, indent = index, len(match.group(1))
            break
    if start < 0:
        return lines, -1, -1
    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = re.match(r"^(\s*)-\s*id:\s*", lines[index])
        if match and len(match.group(1)) <= indent:
            end = index
            break
    return lines, start, end

def _line_of(sentence: str, lines: list[str], start: int, end: int) -> int | None:
    if start < 0:
        return None
    probe = " ".join(sentence.split())[:40]
    if len(probe) < 8:
        return None
    joined, owner = [], []
    for index in range(start, end):
        piece = " ".join(lines[index].split()) + " "
        joined.append(piece)
        owner.extend([index] * len(piece))
    text = "".join(joined)
    at = text.find(probe)
    if at < 0:
        at = text.replace("“", '"').replace("”", '"').find(probe.replace("“", '"').replace("”", '"'))
    return owner[at] + 1 if at >= 0 else None

def effective_rules(leaf: dict[str, Any], yaml_text: str = "") -> tuple[str, dict[str, Any]]:
    split = split_modified(str(leaf.get("description") or ""))
    if split is None:
        return "", {}
    original, modified = split
    leaf_id = str(leaf.get("id") or "")
    lines, start, end = _positions(yaml_text, leaf_id)
    mods = []
    for index, sentence in enumerate(sentences(modified), 1):
        mods.append({"key": f"M{index}", "text": sentence, "kind": sentence_kind(sentence), "line": _line_of(sentence, lines, start, end),
                     "tokens": _tokens(sentence)})
    origs = []
    for index, sentence in enumerate(sentences(original), 1):
        tokens = _tokens(sentence)
        overlaps = []
        for mod in mods:
            if mod["kind"] == "keep":
                continue
            common = tokens & mod["tokens"]
            score = len(common) / max(1, min(len(tokens), len(mod["tokens"])))
            if len(common) >= 2 and score >= OVERLAP_MIN:
                overlaps.append(mod["key"])
        origs.append({"key": f"O{index}", "text": sentence, "line": _line_of(sentence, lines, start, end), "overlaps": overlaps,
                      "state": "overlapped" if overlaps else "in force"})
    scenarios = [str(s.get("name") or "") for s in leaf.get("scenarios") or [] if isinstance(s, dict)]

    def where(item: dict[str, Any]) -> str:
        return f"{item['key']}, requirements.yaml line {item['line']}" if item.get("line") else item["key"]

    out = [f"CURRENTLY EFFECTIVE RULES of {leaf_id} \"{leaf.get('name', '')}\" (evolution round). Precedence: this round's "
           "scenarios decide; the Modified Feature Description replaces the Original where they overlap; every other "
           "Original rule stays in force (the inherited application already implements it -- keep it).",
           "Modified Feature Description (the change):"]
    out += [f"  [{where(m)}; {m['kind']}] {m['text']}" for m in mods]
    out.append("Original Feature Description (in force unless marked):")
    for item in origs:
        mark = f"  <- overlaps {', '.join(item['overlaps'])}: where they differ, {', '.join(item['overlaps'])} applies" if item["overlaps"] else ""
        out.append(f"  [{where(item)}] {item['text']}{mark}")
    if scenarios:
        out.append("This round's scenarios (authoritative): " + "; ".join(scenarios))
    struct = {"leaf": leaf_id, "precedence": "scenarios > Modified > Original (where they overlap)",
              "modified": [{k: v for k, v in m.items() if k != "tokens"} for m in mods], "original": origs,
              "scenarios": scenarios, "yaml_lines": [start + 1, end] if start >= 0 else None}
    return "\n".join(out), struct

def _handoff_spec_leaves(handoff: dict[str, Any] | None) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for record in (handoff or {}).get("leaves") or []:
        if not isinstance(record, dict):
            continue
        for name in record.get("specs") or []:
            found.setdefault(str(name), [])
            if str(record.get("id")) not in found[str(name)]:
                found[str(name)].append(str(record.get("id")))
    return found

def register_specs(project_dir: Path, specs: list[str], classes: dict[str, Any], handoff: dict[str, Any] | None = None) -> dict[str, Any]:
    by_class = dict(classes.get("classes") or {})
    out_of_tree = set(classes.get("out_of_tree") or [])
    by_name = _handoff_spec_leaves(handoff)

    def class_of(leaf: str) -> str:
        if leaf in out_of_tree or by_class.get(leaf) == "inherited":
            return "valid"
        if by_class.get(leaf) == "modified":
            return "unverified"
        if by_class.get(leaf) == "new":
            return "unverified"
        return ""

    files: dict[str, Any] = {}
    counts = {"files": 0, "tests": 0, "valid": 0, "unverified": 0, "superseded": 0, "unmapped_files": 0}
    for rel in specs:
        name = Path(rel).name
        text = _read(Path(project_dir) / rel)
        tests = spec_tests(text)
        file_leaves = list(by_name.get(name) or [])
        if not file_leaves:
            match = FILE_ID.match(name)
            if match and (match.group(1) in by_class or match.group(1) in out_of_tree):
                file_leaves = [match.group(1)]
        entries = []
        for test in tests:
            match = TITLE_ID.match(test["title"])
            leaf = match.group(1) if match else ""
            why = ""
            if leaf and not (leaf in by_class or leaf in out_of_tree):
                why = f"its title names {leaf}, a leaf of no record"
                leaf = ""
            if not leaf:
                if len(file_leaves) == 1:
                    leaf = file_leaves[0]
                elif file_leaves:
                    kinds = {class_of(f) for f in file_leaves}
                    if kinds == {"valid"}:
                        leaf = "+".join(file_leaves)
                        entries.append({**test, "leaf": leaf, "class": "valid", "why": "a file of inherited leaves only"})
                        continue
                    why = why or f"the title names no leaf and the file serves {', '.join(file_leaves)}"
            kind = class_of(leaf) if leaf else ""
            if not kind:
                kind, why = "unverified", why or "no leaf could be told for it"
            elif kind == "unverified" and not why:
                why = (f"{leaf} is modified in this round: this test may assert an Original rule the Modified text replaced "
                       "(settled after that leaf's own batch)")
            entries.append({**test, "leaf": leaf, "class": kind, "why": why})
        leaves = sorted({part for e in entries if e["leaf"] for part in e["leaf"].split("+")} | set(file_leaves), key=_natural_id)
        mapped = bool(leaves)
        kinds = {e["class"] for e in entries}
        files[rel] = {"leaves": leaves, "tests": entries, "mapped": mapped,
                      "why": "" if mapped else "no REQ id in the file name or the test titles, and no handoff entry",
                      "kind": ("valid" if kinds == {"valid"} else "unverified" if kinds == {"unverified"} else
                               "mixed" if kinds else "empty"),

                      "modified_leaves": sorted({e["leaf"] for e in entries if e["class"] == "unverified" and e["leaf"]
                                                 and by_class.get(e["leaf"]) in ("modified", "new")}, key=_natural_id)}
        counts["files"] += 1
        counts["tests"] += len(entries)
        counts["valid"] += sum(1 for e in entries if e["class"] == "valid")
        counts["unverified"] += sum(1 for e in entries if e["class"] == "unverified")
        counts["unmapped_files"] += 0 if mapped else 1
    return {"files": files, "counts": counts}

def exempt_files(registry: dict[str, Any], members: list[str]) -> list[str]:
    wanted = set(members)
    return [rel for rel, entry in (registry.get("files") or {}).items() if wanted & set(entry.get("modified_leaves") or [])]

def leaf_of_title(registry: dict[str, Any], rel: str) -> dict[str, dict[str, str]]:
    entry = (registry.get("files") or {}).get(rel) or {}
    return {t["title"]: {"leaf": t.get("leaf", ""), "class": t.get("class", "")} for t in entry.get("tests") or []}

def batch_spec_prefix(batch_id: str, members: list[str], classes: dict[str, Any], existing: set[str], safe=None) -> str:
    base = safe(batch_id) if safe else re.sub(r"[^A-Za-z0-9._-]+", "-", batch_id).strip("-") or "REQ"
    modified = set(classes.get("modified") or [])
    prefix = base
    if modified & set(members) or f"{base}.spec.js" in existing:
        prefix = base + EVO_SUFFIX
        n = 2
        while f"{prefix}.spec.js" in existing:
            prefix = f"{base}{EVO_SUFFIX}{n}"
            n += 1
    return prefix

def _capped(items: list[str], limit: int, sep: str = "; ") -> str:
    shown, used = [], 0
    for item in items:
        if used + len(item) + len(sep) > limit:
            break
        shown.append(item)
        used += len(item) + len(sep)
    rest = len(items) - len(shown)
    return sep.join(shown) + (f"{sep}... ({rest} more)" if rest > 0 else "")

def weak_spots(classes: dict[str, Any], handoff: dict[str, Any] | None) -> list[str]:
    names = dict(classes.get("names") or {})
    lines = []
    for leaf_id, status in sorted((classes.get("earlier_status") or {}).items(), key=lambda kv: _natural_id(kv[0])):
        if status and status != "green":
            lines.append(f"{leaf_id} {names.get(leaf_id, '')[:60]} ended {status}".replace("  ", " "))
    debt = (handoff or {}).get("debt") or {}
    for finding in debt.get("open_findings") or []:
        if isinstance(finding, dict):
            lines.append(f"open finding: {finding.get('class')} at {finding.get('where')}")
    red = debt.get("earlier_specs_red_at_start") or []
    if red:
        lines.append("specs red at the original round's start: " + ", ".join(map(str, red[:8])))
    return lines

LEAD = ("{head}: the project folder holds the application the original round of this task delivered; its {specs} "
        "acceptance spec file(s) stay in backend/test-e2e and keep guarding every batch. Keep its behaviour: every route, "
        "page, API endpoint, table, column, seed record and accessible name listed here keeps working as it does -- do not "
        "re-create, rename, move or drop any of them, and do not replace the existing sign-up / sign-in / navigation. "
        "This round builds only the leaves named below. A MODIFIED leaf changes an existing feature: edit the existing "
        "code where its stated change needs it -- the Modified Feature Description replaces the Original where they "
        "overlap, the rest of the Original stays in force, this round's scenarios decide. A NEW leaf adds what it needs "
        "(new routes, pages, endpoints, tables, columns with defaults, seed records). Shared code (a middleware, a query, a "
        "component) is changed where a stated change needs it -- and everything else it does is protected.")

DATA_LIFECYCLE_NOTE = (
    "DATA LIFECYCLE (evolution round): the delivered package starts on an EMPTY database (the database file is deleted at "
    "delivery) and must also start on the inherited application's existing database, any number of times. Schema: new "
    "tables with CREATE TABLE IF NOT EXISTS; a new column of an existing table by a migration that checks PRAGMA "
    "table_info, runs ALTER TABLE ... ADD COLUMN ... with a DEFAULT, then backfills, all in one transaction; before a "
    "case-insensitive UNIQUE index, look for existing rows that differ only by letter case and resolve them without "
    "deleting user data. Seed: the records this round's scenarios state as pre-provisioned (one independent set per "
    "scenario) are added NEXT TO the existing seed on every database that lacks them -- not only when a table was just "
    "created; this round's records go in as ONE versioned data migration (e.g. evo_v1), run once inside one transaction and "
    "recorded as applied under its stable key (a row of a migrations / seed-marker table written in the same transaction), "
    "never re-detected by a display name a user can rename; records several scenarios share (an account, an organization) "
    "are created once and referenced; a restart must not re-create or reset a seeded record a user renamed, changed or "
    "deleted. Every existing seed record stays exactly as it is.")

def data_lifecycle_note() -> str:
    from .seed_foundation import idempotent_head

    from .seed_foundation import SEED_EXCEPTIONS

    head = idempotent_head()
    if not head:
        return DATA_LIFECYCLE_NOTE
    keep = "Every existing seed record stays exactly as it is."
    return DATA_LIFECYCLE_NOTE.replace(keep, keep[:-1] + ", " + SEED_EXCEPTIONS + ".", 1) + head

def contract_lead(classes: dict[str, Any], specs: int, handoff: dict[str, Any] | None, head: str) -> str:
    parts = [LEAD.format(head=head, specs=specs)]
    modified, new = list(classes.get("modified") or []), list(classes.get("new") or [])
    inherited, outside = list(classes.get("inherited") or []), list(classes.get("out_of_tree") or [])
    parts.append(f"Leaves of this round: modified ({len(modified)}): {', '.join(modified) or 'none'}; new ({len(new)}): "
                 f"{', '.join(new) or 'none'}. Inherited, not built again: {len(inherited)} leaf/leaves of this requirement"
                 + (f" and {len(outside)} of the original round outside it ({_capped(outside, 300, ', ')})" if outside else "")
                 + ".")
    spots = weak_spots(classes, handoff)
    if spots:
        parts.append("Known weak spots of the inherited application (its handoff; inherited means origin, not "
                     "correctness): " + _capped(spots, 900))
    return "\n".join(parts)[:NOTE_CAP]

PROVISION = re.compile(r"(?i)pre-?provision|seeded|seed data|already (?:exists?|registered)|existing")
LITERAL = re.compile(r"`([^`\n]{2,80})`")
ENTITY_BEFORE = re.compile(r"(?i)\b(?:workbooks?|worksheets?|sheets?|accounts?|users?|repositor(?:y|ies)|repos?|"
                           r"organi[sz]ations?|orgs?|teams?|branch(?:es)?|issues?|milestones?|pull requests?|PRs?|releases?|"
                           r"tags?|labels?|named ranges?|filter views?|views?|rules?|notes?|emails?|members?|owners?|"
                           r"collaborators?|reviewers?|commits?|files?)\b[^`.;]{0,40}$")
SECRET_BEFORE = re.compile(r"(?i)pass(?:word|code)?\b[^`]{0,20}$")

def scenario_seed_names(tree: dict[str, Any], leaf_ids: list[str]) -> dict[str, list[str]]:
    leaves, _, _ = tree_index(tree)
    out: dict[str, list[str]] = {}
    for leaf_id in leaf_ids:
        for scenario in (leaves.get(leaf_id) or {}).get("scenarios") or []:
            if not isinstance(scenario, dict):
                continue
            names: list[str] = []
            for step in scenario.get("steps") or []:
                if not isinstance(step, dict) or str(step.get("keyword") or "").upper() != "GIVEN":
                    continue
                text = " ".join(str(step.get("content") or "").split())
                if not PROVISION.search(text):
                    continue
                for match in LITERAL.finditer(text):
                    before = text[max(0, match.start() - 60):match.start()]
                    value = match.group(1).strip()
                    if SECRET_BEFORE.search(before) or not ENTITY_BEFORE.search(before):
                        continue
                    if value.startswith("=") or re.fullmatch(r"[\d.,\s-]+", value) or value in names:
                        continue
                    names.append(value)
            if names:
                out[str(scenario.get("name") or leaf_id)] = names
    return out
