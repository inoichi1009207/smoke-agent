from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(os.environ.get("ARC_OFF_ROOT") or Path(__file__).resolve().parent.parent)
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "selftests"))

KEY = "findings_owner_batch_v2"
REF = HERE / "ref_v167_off.json"
CASES = []
MISSING = object()
NL = chr(10)

def case(function):
    CASES.append(function)
    return function

class Settings:
    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        from agent.settings import SETTINGS

        self.saved = {key: SETTINGS.get(key, MISSING) for key in self.values}
        SETTINGS.update(self.values)

    def __exit__(self, *exc):
        from agent.settings import SETTINGS

        for key, value in self.saved.items():
            if value is MISSING:
                SETTINGS.pop(key, None)
            else:
                SETTINGS[key] = value

def tree(leaves) -> str:
    lines = ["id: ROOT", "name: Fixture", "type: FOLDER", "description: \"A fixture application.\"", "children:"]
    for leaf_id, description, steps in leaves:
        lines += [f"- id: {leaf_id}", f"  name: {leaf_id}", "  type: ATOMIC",
                  f"  description: {json.dumps(description, ensure_ascii=False)}"]
        if steps:
            lines += ["  scenarios:", f"  - name: {leaf_id} - Scenario 1", "    steps:"]
            for keyword, content in steps:
                lines += [f"    - keyword: {keyword}", f"      content: {json.dumps(content, ensure_ascii=False)}"]
    return NL.join(lines) + NL

WHOLE = tree([
    ("L-A", "The home page lists the workbooks.", [("THEN", "the page shows the workbook list")]),
    ("L-B", "The toolbar has an “Export CSV” button.", [("WHEN", "the user clicks the “Export CSV” button")]),
    ("L-C", "The sheet menu has a “Sort range” button.", []),
    ("L-D", "The settings page has a “Save” button and a “Profile” link.",
     [("WHEN", "the user clicks the “Profile” link"), ("THEN", "the page displays “Name is required”")]),
    ("L-E", "The access page has a “Role” select per grant.", []),
])
FILES = {
    "frontend/src/components/Toolbar.tsx": 'export const Toolbar = () => <nav><a href="/export">Export CSV</a></nav>;\n',
    "frontend/src/pages/Settings.tsx": (
        "export const Settings = () => (\n  <form>\n    <h2>Profile</h2>\n"
        "    <button type=\"submit\">Save</button>\n  </form>\n);\n"),
    "frontend/src/pages/Access.tsx": (
        "export function Access() {\n  return (<form>\n    <select aria-label=\"Role\"><option>Read</option></select>\n"
        "    <select aria-label=\"Role\"><option>Write</option></select>\n"
        "    <input aria-label=\"Team\" />\n    <input aria-label=\"Team\" />\n  </form>);\n}\n"),
}

MESSAGES = tree([
    ("L-A", "The form has a “Create thing A” button.", [("THEN", "the page displays “Name is required”")]),
    ("L-C", "The editor has a “Save thing C” button.", [("THEN", "the page displays “Name is required”")]),
    ("L-B1", "The form has a “Rename thing B” button.", [("THEN", "the page displays “Name must be 50 characters or fewer”")])])
MESSAGE_PAGE = "export const P = () => <div><button>Create thing A</button><button>Rename thing B</button></div>;\n"

EDGES = [{"req": "L-B", "verdict": "WRONG_ROLE", "confidence": "high", "quote": "the “Export CSV” button opens the export",
          "source": "the toolbar", "source_resolved": "/", "expected": "button named 'Export CSV'", "have": ["link"]},
         {"req": "L-A", "verdict": "NOT_A_CONTROL", "confidence": "high", "quote": "q", "source": "s",
          "source_resolved": "/", "expected": "link named 'Sort range'", "have": ["h1"]}]
WALK = {"walks": [{"account": "owner", "paths": [
    {"key": "0:0", "status": "missing", "route": "/settings", "done": ["Account menu", "Settings"],
     "missing": "Active sessions", "visible": ["Profile"], "late": [], "leaf": "L-D", "scenario": "S1",
     "quote": "Account menu → Settings → Active sessions", "steps": []},
    {"key": "1:0", "status": "missing", "route": "/", "done": [], "missing": "Sort range", "visible": [], "late": [],
     "leaf": "L-C", "scenario": "S2", "quote": "Sort range", "steps": []}]}]}
HELD = {"where": "outsider-walk: rendered app, frontend/src (route /w)", "requirement": "(stated path)",
        "problem": "a claim held for REQ-9", "fix": "fix it", "layer": "scripted", "owner_leaf": ["REQ-9"]}

def _clean(value, root: Path):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    for form in {str(root), root.as_posix(), str(root).replace(chr(92), chr(92) * 2)}:
        text = text.replace(form, "<ROOT>")
    return json.loads(text)

def _project(tmp: str) -> Path:
    root = Path(tmp)
    (root / "backend" / "src").mkdir(parents=True, exist_ok=True)
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root

def findings_part() -> dict:
    from agent import detaudit, outsider_walk, stated_messages

    node = {"id": "L-D", "name": "L-D", "description": "The settings page has a “Save” button and a “Profile” link.",
            "requirement_text": WHOLE, "members": ["L-D"],
            "inherited_names_all": [], "inherited_names_complete": True}
    snapshot = [{"route": "/", "aria": "- navigation:\n  - link \"Export CSV\":\n    - /url: /export\n", "text": "Profile",
                 "console": ["Failed to load resource: 404"]}]
    out = {}
    with tempfile.TemporaryDirectory() as tmp, Settings(det_outsider_walk=False, det_session_probe=False, det_plan_walk=False):
        root = _project(tmp)
        out["layer"] = _clean(detaudit.deterministic_findings(root, node, snapshot, log=lambda *_: None), root)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "frontend" / "src" / "pages").mkdir(parents=True)
        (root / "backend" / "src").mkdir(parents=True)
        (root / "frontend" / "src" / "pages" / "P.tsx").write_text(MESSAGE_PAGE, encoding="utf-8")
        out["messages"] = _clean(stated_messages.stated_message_findings(MESSAGES, root, log=lambda *_: None), root)
    original = detaudit.missing_stated_edges
    detaudit.missing_stated_edges = lambda whole, project_dir: [dict(i) for i in EDGES]
    try:
        if hasattr(detaudit, "owner_lookup"):
            out["nav"] = detaudit.nav_edge_findings(WHOLE, Path("."), [], owner_of=detaudit.owner_lookup(node)[0])[0]
        else:
            out["nav"] = detaudit.nav_edge_findings(WHOLE, Path("."), [])[0]
    finally:
        detaudit.missing_stated_edges = original
    if hasattr(outsider_walk, "_stating_leaves"):
        try:
            stating = outsider_walk._stating_leaves(WHOLE, outsider_walk._credentials(WHOLE))
        except Exception:
            stating = None
        out["walk"] = outsider_walk._path_findings(WALK, stating)
    else:
        out["walk"] = outsider_walk._path_findings(WALK)
    return out

def process_node_part() -> dict:
    import cases_rollback as rb
    import main as factory_module
    from agent.settings import SETTINGS
    from agent.testrun import TestOutcome

    contexts: list[str] = []
    script = [{"write": {rb.APP: "export const App = () => null; // OWN-OK"}, "check": True}]
    base = rb.implementer_class(script)

    class Recording(base):
        def implement(self, node, tests, outcome, extra_context=""):
            contexts.append(str(extra_context))
            return super().implement(node, tests, outcome, extra_context=extra_context)

    names = ("run_e2e", "build_frontend", "Implementer", "run_node_tests", "static_test_defects", "parse_defects_in_output",
             "lint_application", "log")
    saved = {name: getattr(factory_module, name) for name in names}
    saved_audit = SETTINGS.get("audit_enabled", True)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = rb.World(root)
        f = rb.make_factory(root, "export const App = () => null;")
        f.held_findings = {"REQ-9": [dict(HELD)]}
        try:
            factory_module.run_e2e, factory_module.build_frontend = world.run_e2e, world.build_frontend
            factory_module.Implementer = Recording
            factory_module.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
            factory_module.static_test_defects = lambda project_dir, files: ""
            factory_module.parse_defects_in_output = lambda output, files: ""
            factory_module.lint_application = lambda project_dir, native: ""
            factory_module.log = lambda *_: None
            SETTINGS["audit_enabled"] = False
            passed = f.process_node({"id": "REQ-9", "name": "Batch nine", "members": ["REQ-9"]}, 1)
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
            SETTINGS["audit_enabled"] = saved_audit
        return _clean({"passed": bool(passed), "extra_context": contexts}, root)

def run_queue_part() -> dict:
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.open_findings = {}
    f.report = {"nodes": {}, "verification": {}}
    f.failed_members, f.unattempted_members = [], []
    queue = [{"id": "A", "members": ["L-A"]}, {"id": "B", "members": ["L-B"]}, {"id": "C", "members": ["L-C"]}]
    popped = iter(["A", "B", "C"])
    f.over_budget = lambda: next(popped, None) == "C"
    f.hard_end = lambda: None
    f.foundation_retry_due = lambda *a, **k: False
    f.evolution_after_batch = lambda node, ok: None
    f.save_checkpoint = lambda *a, **k: None
    f.attempt_node = lambda node, nodes_left, pause_sec=60.0, requeue=False, retry=False: True

    f.snapshot_sources = lambda: {}
    f.restore_sources = lambda snapshot: 0
    saved_log = m.log
    m.log = lambda *_: None
    try:
        before = set(f.__dict__)
        passed = f.run_queue([(n, []) for n in queue], [], "digest", "")
    finally:
        m.log = saved_log
    return {"passed": bool(passed), "new_attributes": sorted(set(f.__dict__) - before),
            "unattempted": list(f.unattempted_members), "open_findings": len(f.open_findings)}

def checkpoint_part() -> dict:
    import cases_rollback as rb

    with tempfile.TemporaryDirectory() as tmp:
        f = rb.make_factory(Path(tmp), "APP")
        for name in f.CHECKPOINT_FIELDS:
            if not hasattr(f, name):
                setattr(f, name, [] if name.endswith("s") else {})
        f.description_pass, f.contract, f.partial_green = {}, "", {}
        f.held_findings = {"L-B": [dict(HELD, owner_leaf=["L-B"])]}
        f.save_checkpoint("digest", ["A"], "")
        state = json.loads((Path(tmp) / ".arc" / "resume.json").read_text(encoding="utf-8"))
    return {"keys": sorted(state)}

def produce() -> dict:
    from agent import watchdog

    saved = watchdog._ACTIVE
    watchdog._ACTIVE = None
    try:
        return {"findings": findings_part(), "process_node": process_node_part(), "run_queue": run_queue_part(),
                "checkpoint": checkpoint_part()}
    finally:
        watchdog._ACTIVE = saved

def _off() -> dict:
    with Settings(**{KEY: False}):
        return json.loads(json.dumps(produce(), ensure_ascii=False, sort_keys=True, default=str))

def _differences(a, b, path="") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in sorted(set(a) | set(b)):
            out += _differences(a.get(key, "<absent>"), b.get(key, "<absent>"), f"{path}/{key}")
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += _differences(x, y, f"{path}[{i}]")
        return out
    return [] if a == b else [f"{path}: {str(a)[:300]} != {str(b)[:300]}"]

@case
def the_reference_was_made_by_v167_and_is_not_empty():
    ref = json.loads(REF.read_text(encoding="utf-8"))
    for part in ("layer", "messages", "walk", "nav"):
        assert ref["findings"][part], part
    problems = " ".join(f["problem"] for f in ref["findings"]["layer"])
    assert "Export CSV" in problems and "Role" in problems, problems
    assert ref["process_node"]["extra_context"], ref["process_node"]
    assert "held_findings" not in ref["checkpoint"]["keys"]

@case
def off_equals_v167_on_every_touched_check_the_prompts_the_queue_and_the_checkpoint():
    ref = json.loads(REF.read_text(encoding="utf-8"))
    differences = _differences(ref, _off())
    assert not differences, NL.join(differences[:12])

@case
def on_differs_on_the_same_fixtures():
    with Settings(**{KEY: True}):
        on = json.loads(json.dumps(produce(), ensure_ascii=False, sort_keys=True, default=str))
    assert any(f.get("owner_leaf") for f in on["findings"]["layer"]), on["findings"]["layer"]
    assert any(f.get("owner_leaf") for f in on["findings"]["messages"]), on["findings"]["messages"]
    assert any(f.get("owner_leaf") for f in on["findings"]["walk"]), on["findings"]["walk"]
    assert any(f.get("owner_leaf") for f in on["findings"]["nav"]), on["findings"]["nav"]
    assert any(f.get("owner_leaf") == ["L-E"] and "Role" in f["problem"] for f in on["findings"]["layer"]), on["findings"]["layer"]
    assert any("AUDIT CLAIMS HELD FOR THIS BATCH" in c for c in on["process_node"]["extra_context"]), on["process_node"]
    assert "held_findings" in on["checkpoint"]["keys"]
    assert "held_ahead" in on["run_queue"]["new_attributes"]

def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--dump":
        Path(sys.argv[2]).write_text(json.dumps(_off(), ensure_ascii=False, indent=1, sort_keys=True) + NL, encoding="utf-8")
        print(f"wrote {sys.argv[2]} from {ROOT}")
        return 0
    passed = 0
    started = time.time()
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
            passed += 1
        except Exception:
            print(f"FAIL  {function.__name__}: {traceback.format_exc(limit=4)}")
    print(f"{passed}/{len(CASES)} v168 switch-off = v167 cases passed in {time.time() - started:.1f}s")
    return 0 if passed == len(CASES) else 1

if __name__ == "__main__":
    sys.exit(main())
