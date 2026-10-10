from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

HANDOFF_DIR = ".arc-handoff"
JSON_NAME = "handoff.json"
MD_NAME = "HANDOFF.md"
FORMAT = "arc-handoff/1"
PASSWORD_TEXT = "password stated in the requirement"
HANDOFF_BUDGET_SEC = 10.0
NOTES_CAP = 2600
ARCHITECT_CAP = 14000
DEBT_NOTE_CAP = 700

SECRET_KEY = re.compile(r"(?i)password|passwd|passcode|^pwd$|secret|(?:^|_)token$|credential|api_?key")

SECRET_AFTER = re.compile(r"(?i)\b(pass(?:word|wd|code)?|secret)\b(\s*(?:is|:|=|of|as|set to)?\s*)([`\"'])([^`\"'\n,]{1,80})\3")
ABS_PATH = re.compile(r"(?:\b[A-Za-z]:[\\/]|(?<![\w.])/(?:tmp|workspace|home|Users|root|var|mnt|opt|private|srv|data)/)[^\s\"'`<>|,;)\]]*")
NAME_KEY = re.compile(r"(?i)^(?:user(?:name)?|login|name|display_?name|full_?name|email|title|slug|owner|label|handle|code|number)$")
INJECTED_HEADS = ("EXISTING APPLICATION",)
GREEN = "green"

def secrets_of(requirement_text: str, contract: Any) -> set[str]:

    found = {m.group(4).strip() for m in SECRET_AFTER.finditer(requirement_text or "")
             if re.search(r"[0-9]", m.group(4)) and len(m.group(4).strip()) >= 6}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if SECRET_KEY.search(str(key)) and isinstance(item, (str, int, float)) and str(item).strip():
                    found.add(str(item).strip())
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(contract)
    return {s for s in found if len(s) >= 4}

def scrub_text(text: str, secrets: set[str], roots: list[str]) -> str:
    out = str(text)
    for root in roots:
        if root:
            out = out.replace(root, "").replace(root.replace("\\", "/"), "")
    out = ABS_PATH.sub("<local path>", out)
    for secret in sorted(secrets, key=len, reverse=True):
        out = out.replace(secret, PASSWORD_TEXT)
    return SECRET_AFTER.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{PASSWORD_TEXT}{m.group(3)}", out)

def scrub(value: Any, secrets: set[str], roots: list[str]) -> Any:
    if isinstance(value, dict):
        return {str(k): (PASSWORD_TEXT if SECRET_KEY.search(str(k)) and isinstance(v, (str, int, float)) else scrub(v, secrets, roots))
                for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, secrets, roots) for v in value]
    if isinstance(value, str):
        return scrub_text(value, secrets, roots)
    return value

def _walk_tree(tree: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, str]]:
    leaves: list[dict[str, Any]] = []
    nodes: dict[str, dict[str, Any]] = {}
    parents: dict[str, str] = {}

    def walk(node: Any, parent: str) -> None:
        if not isinstance(node, dict):
            return
        node_id = str(node.get("id") or "")
        children = [c for c in node.get("children") or [] if isinstance(c, dict)]
        if node_id and node_id != "ROOT":
            nodes[node_id] = node
            parents[node_id] = parent
            if not children:
                leaves.append(node)
        for child in children:
            walk(child, node_id)

    walk(tree, "")
    return leaves, nodes, parents

INHERITED = "inherited"
INHERITED_FAILING = "inherited, failing tests"
NOT_DEBT = (GREEN, INHERITED)

def _evolution_of(factory: Any) -> dict[str, Any]:
    state = getattr(factory, "evolution", None)
    return state if isinstance(state, dict) and state.get("active") else {}

def _final_of(factory: Any) -> dict[str, Any]:
    final = getattr(factory, "final_suite", None)
    return final if isinstance(final, dict) and final.get("judged") else {}

def _inherited_tests(leaf_id: str, evo: dict[str, Any], final: dict[str, Any]) -> tuple[list[str], dict[str, int]]:
    files = ((evo.get("registry") or {}).get("files") or {})
    books = evo.get("tests") or {}
    final_specs = dict(final.get("specs") or {})
    final_titles = {k: set(v) for k, v in (final.get("failed_titles") or {}).items()}
    names: list[str] = []
    counts = {"passed": 0, "failed": 0, "not judged": 0, "superseded": 0}
    for rel, entry in files.items():
        name = Path(rel).name
        mine = [t for t in entry.get("tests") or [] if leaf_id in str(t.get("leaf") or "").split("+")]
        if not mine:
            continue
        names.append(name)
        for test in mine:
            title = str(test.get("title"))
            if test.get("class") == "superseded":
                counts["superseded"] += 1
            elif name in final_specs and final_specs[name] in ("green", "red"):
                failed = final_specs[name] == "red" and (title in final_titles.get(name, set()) or not final_titles.get(name))
                counts["failed" if failed else "passed"] += 1
            else:
                state = str((books.get(rel) or {}).get(title) or "not judged")
                counts["passed" if state == "passed" else "failed" if state == "failed" else "not judged"] += 1
    return names, counts

def leaf_records(tree: dict[str, Any], factory: Any) -> list[dict[str, Any]]:
    leaves, _, _ = _walk_tree(tree)
    report = dict(getattr(factory, "report", None) or {})
    entries = dict(report.get("nodes") or {})
    batch_of = dict(getattr(factory, "batch_members", None) or {})
    failed = {str(m) for m in list(getattr(factory, "failed_members", None) or [])}
    unattempted = {str(m) for m in list(getattr(factory, "unattempted_members", None) or [])}
    desc = [str(s) for s in list(getattr(factory, "description_specs", None) or [])]
    leaf_batch = {str(m): batch for batch, members in batch_of.items() for m in members}
    evo = _evolution_of(factory)
    classes = dict((evo.get("classes") or {}).get("classes") or {})
    earlier_status = dict((evo.get("classes") or {}).get("earlier_status") or {})
    final = _final_of(factory)
    final_specs = dict(final.get("specs") or {})
    records = []

    def inherited_record(leaf_id: str, name: str, outside: bool) -> dict[str, Any]:
        specs, counts = _inherited_tests(leaf_id, evo, final)
        record = {"id": leaf_id, "name": name[:120], "batch": None,
                  "status": INHERITED_FAILING if counts["failed"] else INHERITED, "specs": specs,
                  "origin": "an earlier round", "earlier_status": earlier_status.get(leaf_id), "tests": counts}
        if outside:
            record["outside_requirement"] = True
        return record

    for leaf in leaves:
        leaf_id = str(leaf.get("id"))
        if classes.get(leaf_id) == INHERITED and leaf_id not in leaf_batch:
            records.append(inherited_record(leaf_id, str(leaf.get("name") or ""), False))
            continue
        batch = leaf_batch.get(leaf_id, leaf_id)
        entry = entries.get(batch) if isinstance(entries.get(batch), dict) else None
        if leaf_id in unattempted or entry is None:
            status = "not attempted"
        elif leaf_id in {str(m) for m in entry.get("put_back_members") or []}:
            status = "put back"
        elif str(entry.get("status")) == "passed" and leaf_id not in failed:
            status = GREEN
        elif str(entry.get("status")) == "budget":
            status = "budget"
        else:
            status = "red"
        specs = [Path(str(t)).name for t in entry.get("tests") or [] if str(t).startswith("backend/test-e2e/")] if entry else []
        specs += [Path(s).name for s in desc if Path(s).name == f"DESC-{leaf_id}.spec.js"]
        specs = list(dict.fromkeys(specs))
        record = {"id": leaf_id, "name": str(leaf.get("name") or "")[:120], "batch": batch, "status": status, "specs": specs}
        judged = [final_specs.get(s) for s in specs if final_specs.get(s) in ("green", "red")]
        if specs and len(judged) == len(specs) and status != "not attempted":
            final_status = GREEN if all(j == "green" for j in judged) else "red"
            if final_status != status:
                record["batch_status"] = status
                record["status"] = final_status
            record["status_from"] = "final suite"
        if classes.get(leaf_id):
            record["round_class"] = classes[leaf_id]
        records.append(record)
    names = dict((evo.get("classes") or {}).get("names") or {})
    for leaf_id in (evo.get("classes") or {}).get("out_of_tree") or []:
        records.append(inherited_record(str(leaf_id), str(names.get(leaf_id) or ""), True))
    return records

def evolution_summary(factory: Any) -> dict[str, Any]:
    evo = _evolution_of(factory)
    if not evo:
        return {}
    classes = evo.get("classes") or {}
    files = ((evo.get("registry") or {}).get("files") or {})
    superseded = [{"file": Path(rel).name, "title": str(t.get("title"))[:160], "leaf": t.get("leaf"), "why": str(t.get("why"))[:200]}
                  for rel, e in files.items() for t in e.get("tests") or [] if t.get("class") == "superseded"]
    unverified = sum(1 for e in files.values() for t in e.get("tests") or [] if t.get("class") == "unverified")
    continuation = getattr(factory, "continuation", None)
    continuation = continuation if isinstance(continuation, dict) else {}
    return {
        "modified": list(classes.get("modified") or []), "new": list(classes.get("new") or []),
        "inherited": len(classes.get("inherited") or []), "outside_requirement": list(classes.get("out_of_tree") or []),
        "unverified_classes": dict(classes.get("unverified") or {}),
        "registry": dict((evo.get("registry") or {}).get("counts") or {}), "unverified_tests_left": unverified,
        "superseded_tests": superseded[:60],
        "inherited_tests_broken": {Path(rel).name: v for rel, v in (evo.get("inherited_debt") or {}).items()},
        "baseline": {"state": continuation.get("baseline"), "when": continuation.get("baseline_when"),
                     "open": bool(continuation.get("baseline_open")), "counts": evo.get("baseline_counts")},
        "spec_names": dict(evo.get("spec_prefix") or {}),
        "data_lifecycle": dict(evo.get("lifecycle") or {}),
        "spec_updates": list(evo.get("spec_updates") or []),
    }

def contract_object(contract: str) -> dict[str, Any] | None:
    try:
        value = json.loads(contract)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None

def promised(contract: dict[str, Any] | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"routes": [], "pages": [], "api": []}
    if not contract:
        return out
    for page in contract.get("pages") or []:
        if isinstance(page, dict):
            if page.get("route") and str(page["route"]) not in out["routes"]:
                out["routes"].append(str(page["route"]))
            if page.get("file") and str(page["file"]) not in out["pages"]:
                out["pages"].append(str(page["file"]))
    for api in contract.get("api") or []:
        if isinstance(api, dict) and api.get("path"):
            line = f"{str(api.get('method') or 'GET').upper()} {api['path']}"
            if line not in out["api"]:
                out["api"].append(line)
    return out

def seed_records(contract: dict[str, Any] | None) -> list[dict[str, Any]]:
    records = []
    for item in (contract or {}).get("seed") or []:
        if not isinstance(item, dict):
            continue
        fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        names = {str(k): str(v)[:120] for k, v in fields.items() if NAME_KEY.match(str(k)) and isinstance(v, (str, int))}
        if any(SECRET_KEY.search(str(k)) for k in fields):
            names["password"] = PASSWORD_TEXT
        records.append({"entity": str(item.get("entity") or "")[:60], "names": names})
    return records

def conventions(contract: dict[str, Any] | None, limit: int = 24, width: int = 300) -> list[str]:
    out: list[str] = []
    for key in ("naming", "notes"):
        value = (contract or {}).get(key)
        for item in (value if isinstance(value, list) else [value] if value else []):
            text = " ".join(str(item).split())
            if text and not text.startswith(INJECTED_HEADS) and text[:width] not in out:
                out.append(text[:width])
    return out[:limit]

def finding_class(finding: dict[str, Any]) -> str:
    for key in ("kind", "check", "class"):
        if finding.get(key):
            return str(finding[key])[:60]
    problem = " ".join(str(finding.get("problem") or "").split())
    return (re.split(r"(?<=[.:;])\s", problem)[0] or "finding")[:90]

def build_handoff(factory: Any, tree: dict[str, Any], requirement_sha: str, requirement_text: str, project_dir: Path,
                  started: float | None = None) -> dict[str, Any]:
    started = time.monotonic() if started is None else started
    contract_text = str(getattr(factory, "contract", "") or "")
    contract = contract_object(contract_text)
    secrets = secrets_of(requirement_text, contract)
    roots = [str(Path(project_dir).resolve()), str(project_dir)]
    leaves = leaf_records(tree, factory)
    debt_leaves = [{"id": r["id"], "status": r["status"], "specs": r["specs"]} for r in leaves if r["status"] not in NOT_DEBT]
    open_findings = [dict(v) for v in dict(getattr(factory, "open_findings", None) or {}).values() if isinstance(v, dict)]
    continuation = getattr(factory, "continuation", None)
    continuation = continuation if isinstance(continuation, dict) else {}
    handoff: dict[str, Any] = {
        "format": FORMAT,
        "requirement_sha256": requirement_sha,
        "leaf_ids": [r["id"] for r in leaves if not r.get("outside_requirement")],
        "leaves": leaves,

        "inventory": [{"id": r["id"], "name": r["name"], "status": r["status"], "origin": r.get("origin") or "this run",
                       **({"outside_requirement": True} if r.get("outside_requirement") else {})} for r in leaves],
        "contract": contract if contract is not None else (contract_text[:40000] or None),
        "contract_saved_at": ".arc/design/contract.json" if (Path(project_dir) / ".arc" / "design" / "contract.json").is_file() else None,
        "promised": promised(contract),
        "seed": seed_records(contract),
        "conventions": conventions(contract),
        "debt": {
            "leaves": debt_leaves,
            "open_findings": [{"class": finding_class(f), "where": str(f.get("where") or "")[:160], "batch": str(f.get("batch") or ""),
                               **({"route": str(f["route"])[:120]} if f.get("route") else {})}
                              for f in open_findings][:60],

            "routed_findings": [{k: v for k, v in dict(r).items() if k in ("kind", "status", "where", "problem", "control", "specs", "spec_fixes")}
                                for r in dict(getattr(factory, "audit_routed", None) or {}).values() if isinstance(r, dict)][:30],
            "spec_fixes": list(getattr(factory, "spec_fixes", None) or [])[:30],
            "earlier_specs_red_at_start": sorted((continuation.get("baseline_red") or {}).keys()),
        },
        "earlier": {"conventions": list(continuation.get("handoff_conventions") or [])[:24]} if continuation.get("handoff_conventions") else {},
    }
    evolution = evolution_summary(factory)
    if evolution:
        handoff["evolution"] = evolution
        handoff["debt"]["inherited_tests_broken"] = evolution.get("inherited_tests_broken") or {}
        handoff["debt"]["baseline_open"] = bool((evolution.get("baseline") or {}).get("open"))
    final = _final_of(factory)
    if final:
        handoff["final_suite"] = {"ok": bool(final.get("ok")), "specs": dict(final.get("specs") or {})}
    if time.monotonic() - started > HANDOFF_BUDGET_SEC:
        raise TimeoutError("handoff budget used up")
    return scrub(handoff, secrets, roots)

def render_md(handoff: dict[str, Any]) -> str:
    leaves = handoff.get("leaves") or []
    counts: dict[str, int] = {}
    for leaf in leaves:
        counts[leaf["status"]] = counts.get(leaf["status"], 0) + 1
    debt = handoff.get("debt") or {}
    lines = ["# Handoff to the next stage", "",
             f"Requirement sha256: `{handoff.get('requirement_sha256', '')}`", "",
             "Leaves: " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())), "",
             "Full notes: `.arc-handoff/handoff.json` (contract, promised routes / pages / API, seed names, conventions).", ""]
    if debt.get("leaves"):
        lines += ["## Known debt", ""] + [f"* {d['id']}: {d['status']}" + (f" ({', '.join(d['specs'])})" if d.get("specs") else "")
                                          for d in debt["leaves"][:60]] + [""]
    evolution = handoff.get("evolution") or {}
    if evolution:
        lines += ["## Evolution round", "",
                  f"* modified: {', '.join(evolution.get('modified') or []) or 'none'}; new: {', '.join(evolution.get('new') or []) or 'none'}; "
                  f"inherited: {evolution.get('inherited', 0)} in this requirement, {len(evolution.get('outside_requirement') or [])} outside it",
                  f"* inherited tests: {evolution.get('registry') or {}}; superseded: {len(evolution.get('superseded_tests') or [])}",
                  f"* baseline: {evolution.get('baseline') or {}}", ""]
    if debt.get("open_findings"):
        lines += ["## Audit findings still open", ""] + [f"* {f['class']} at {f['where']}" for f in debt["open_findings"][:30]] + [""]
    if handoff.get("conventions"):
        lines += ["## Conventions", ""] + [f"* {c}" for c in handoff["conventions"][:16]] + [""]
    return "\n".join(lines)

def write_handoff(factory: Any, tree: dict[str, Any] | None, requirement_sha: str, requirement_text: str, project_dir: Path,
                  log: Callable[[str], Any] = print) -> bool:
    began = time.monotonic()
    try:
        if not isinstance(tree, dict):
            return False
        from .fsutil import write_text

        handoff = build_handoff(factory, tree, requirement_sha, requirement_text, project_dir, began)
        folder = Path(project_dir) / HANDOFF_DIR
        write_text(folder / JSON_NAME, json.dumps(handoff, ensure_ascii=False, indent=1))
        write_text(folder / MD_NAME, render_md(handoff))
        debt = handoff.get("debt") or {}
        log(f"[handoff] written to {HANDOFF_DIR}/ ({len(handoff.get('leaves') or [])} leaves, {len(debt.get('leaves') or [])} in debt, "
            f"{len(debt.get('open_findings') or [])} open finding(s); {time.monotonic() - began:.1f} s)")
        return True
    except Exception as exc:
        try:
            log(f"[handoff] not written: {exc!r}")
        except Exception:
            pass
        return False

def load_handoff(project_dir: Path, requirement_sha: str, log: Callable[[str], Any] = print) -> dict[str, Any] | None:
    path = Path(project_dir) / HANDOFF_DIR / JSON_NAME
    if not path.is_file():
        log(f"[handoff] no {HANDOFF_DIR}/{JSON_NAME} from an earlier stage; the existing application is read from its files only")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log(f"[handoff] {HANDOFF_DIR}/{JSON_NAME} unreadable ({type(exc).__name__}); not used")
        return None
    if not isinstance(data, dict) or data.get("format") != FORMAT or not isinstance(data.get("leaves"), list):
        log(f"[handoff] {HANDOFF_DIR}/{JSON_NAME} is not a handoff of this agent; not used")
        return None
    if not data.get("requirement_sha256") or data.get("requirement_sha256") == requirement_sha:
        log(f"[handoff] {HANDOFF_DIR}/{JSON_NAME} was written for this same requirement; not an earlier stage's, not used")
        return None
    return data

def _capped(items: list[str], limit: int, sep: str = "; ") -> str:
    shown, used = [], 0
    for item in items:
        if used + len(item) + len(sep) > limit:
            break
        shown.append(item)
        used += len(item) + len(sep)
    rest = len(items) - len(shown)
    return sep.join(shown) + (f"{sep}... ({rest} more)" if rest > 0 else "")

def debt_lines(handoff: dict[str, Any]) -> list[str]:
    debt = handoff.get("debt") or {}
    lines = [f"{d.get('id')} ended {d.get('status')}" for d in debt.get("leaves") or [] if isinstance(d, dict)]
    lines += [f"open finding: {f.get('class')} at {f.get('where')}" for f in debt.get("open_findings") or [] if isinstance(f, dict)]
    return lines

def architect_text(handoff: dict[str, Any]) -> str:
    parts = ["HANDOFF OF THE EARLIER STAGE (.arc-handoff/handoff.json, written by the run that built the existing "
             "application). Keep its contract's routes, pages, endpoints, seed records and conventions; extend them."]
    contract = handoff.get("contract")
    if isinstance(contract, dict):
        compact = {k: contract.get(k) for k in ("pages", "api", "session", "seed", "naming") if contract.get(k)}
        parts.append("Earlier contract: " + json.dumps(compact, ensure_ascii=False, separators=(",", ":"))[:ARCHITECT_CAP - 3000])
    if handoff.get("conventions"):
        parts.append("Earlier conventions: " + _capped([str(c) for c in handoff["conventions"]], 1500))
    debt = debt_lines(handoff)
    if debt:
        parts.append("Known debt of the earlier stage (not delivered or not verified there): " + _capped(debt, 1200))
    return "\n".join(parts)[:ARCHITECT_CAP]

def notes_text(handoff: dict[str, Any]) -> str:
    parts = []
    if handoff.get("conventions"):
        parts.append("Conventions of the earlier stage (its handoff): " + _capped([str(c) for c in handoff["conventions"]], 1500))
    debt = debt_lines(handoff)
    if debt:
        parts.append("Known debt of the earlier stage (its handoff; not delivered or not verified there): " + _capped(debt, 900))
    return "\n".join(parts)[:NOTES_CAP]

def debt_leaves(handoff: dict[str, Any]) -> dict[str, dict[str, str]]:
    names = {str(r.get("id")): str(r.get("name") or "") for r in handoff.get("leaves") or [] if isinstance(r, dict)}
    return {str(d.get("id")): {"status": str(d.get("status")), "name": names.get(str(d.get("id")), "")}
            for d in (handoff.get("debt") or {}).get("leaves") or [] if isinstance(d, dict) and d.get("id")}

def dependencies_of(tree: dict[str, Any], members: list[str]) -> set[str]:
    _, nodes, parents = _walk_tree(tree)
    found: set[str] = set()
    for member in members:
        current = str(member)
        while current:
            for dep in (nodes.get(current) or {}).get("dependencies") or []:
                found.add(str(dep))
            current = parents.get(current, "")
    return found

def debt_note(tree: dict[str, Any], members: list[str], debts: dict[str, dict[str, str]]) -> str:
    if not debts:
        return ""
    deps = dependencies_of(tree, members)
    hit = [leaf for leaf in debts if any(leaf == d or leaf.startswith(d + "-") or leaf.startswith(d + ".") for d in deps)]
    if not hit:
        return ""
    shown = ", ".join(f"{leaf} ({debts[leaf]['name'][:60]}: {debts[leaf]['status']})" if debts[leaf].get("name")
                      else f"{leaf} ({debts[leaf]['status']})" for leaf in hit[:6])
    text = ("EARLIER-STAGE DEBT: this batch's requirement depends on " + shown + (f" and {len(hit) - 6} more" if len(hit) > 6 else "")
            + ", which the earlier stage did not deliver green (its handoff). Do not assume it works: look at the sources, "
            "and build minimally what this batch's tests need of it, without changing what the earlier specs rely on.")
    return text[:DEBT_NOTE_CAP]
