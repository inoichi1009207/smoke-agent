from __future__ import annotations

import inspect
import os
import sys
import tempfile
import time
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True

CASES = []

def case(function):
    CASES.append(function)
    return function

def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

class Patch:

    def __init__(self):
        self.saved = []

    def set(self, obj, name, value):
        self.saved.append((obj, name, getattr(obj, name, _MISSING)))
        setattr(obj, name, value)

    def module(self, name, module):
        self.saved.append((sys.modules, name, sys.modules.get(name, _MISSING)))
        sys.modules[name] = module

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for obj, name, old in reversed(self.saved):
            if obj is sys.modules:
                if old is _MISSING:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = old
            elif old is _MISSING:
                delattr(obj, name)
            else:
                setattr(obj, name, old)

_MISSING = object()

def settings_with(**values):
    from agent import settings

    class Ctx:
        def __enter__(self):
            self.saved = dict(settings.SETTINGS)
            settings.SETTINGS.update(values)

        def __exit__(self, *exc):
            settings.SETTINGS.clear()
            settings.SETTINGS.update(self.saved)
    return Ctx()

def factory(project: Path, implement: list[str], classes: dict[str, str], req_text: str = "id: ROOT\n"):
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.project_dir = project
    f.report, f.open_findings = {}, {}
    req = project.parent / (project.name + "-req")
    req.mkdir(parents=True, exist_ok=True)
    (req / "requirements.yaml").write_text(req_text, encoding="utf-8")
    f.requirements_dir = req
    f.evolution = {"active": True, "implement": list(implement),
                   "classes": {"classes": dict(classes), "modified": [k for k, v in classes.items() if v == "modified"],
                               "new": [k for k, v in classes.items() if v == "new"]}}
    f.note_duration = lambda *a, **k: None
    return f

def leaf(leaf_id: str, name: str, description: str, deps: list[str] | None = None, quoted: str = "") -> dict:
    node = {"id": leaf_id, "name": name, "description": description, "scenarios": [
        {"name": f"{leaf_id} s", "steps": [{"keyword": "WHEN", "content": f"The user clicks {quoted or 'Go'}."}]}]}
    if deps:
        node["dependencies"] = deps
    return node

def tree() -> dict:

    return {"id": "ROOT", "children": [
        leaf("REQ-1", "Sign in", 'The home page shows a "Sign in" link.', quoted='"Sign in"'),
        leaf("REQ-2", "Issues", 'The repository page has an "Issues" link.', quoted='"Issues"'),
        leaf("REQ-3", "Sessions", "Original Feature Description\n\nA user signs in.\n\nModified Feature Description\n\n"
             'A user can revoke a session on the "Active sessions" page.', deps=["REQ-1"], quoted='"Active sessions"'),
        leaf("REQ-4", "Formulas", "A formula recalculates when a referenced cell is cleared; the grid has columns and rows.",
             deps=["REQ-2"], quoted='"Formula bar"')]}

CLASSES = {"REQ-1": "inherited", "REQ-2": "inherited", "REQ-3": "modified", "REQ-4": "new"}

class Logs:
    def __init__(self):
        import main as m
        self.m, self.lines = m, []

    def __enter__(self):
        self.saved = self.m.log
        self.m.log = self.lines.append
        return self.lines

    def __exit__(self, *exc):
        self.m.log = self.saved

def fake_probe_modules(p: Patch, grid=None, session=None):
    grid_mod = types.ModuleType("agent.grid_probes")
    grid_mod.grid_probes = grid or (lambda *a, **k: [])
    session_mod = types.ModuleType("agent.session_chain")
    session_mod.session_chain_probe = session or (lambda *a, **k: {})
    p.module("agent.grid_probes", grid_mod)
    p.module("agent.session_chain", session_mod)
    import agent
    p.set(agent, "grid_probes", grid_mod)
    p.set(agent, "session_chain", session_mod)

@case
def the_v161a_switches_are_registered_with_their_defaults():
    from agent import settings

    for key in ("evolution_health_build", "evolution_health_probes", "evolution_startup_debt", "evolution_entry_debt_slot"):
        assert settings._DEFAULTS.get(key) is True, key
    assert settings._DEFAULTS["evolution_health_cap_sec"] == 900
    assert settings._DEFAULTS["evolution_entry_debt_sec"] == 900
    saved = os.environ.get("AGENT_EVOLUTION_HEALTH_PROBES")
    try:
        os.environ["AGENT_EVOLUTION_HEALTH_PROBES"] = "false"
        assert settings._load()["evolution_health_probes"] is False
    finally:
        if saved is None:
            os.environ.pop("AGENT_EVOLUTION_HEALTH_PROBES", None)
        else:
            os.environ["AGENT_EVOLUTION_HEALTH_PROBES"] = saved

def run_items(f, t, findings=None):
    state = f.evolution_state()
    with Logs() as lines:
        rows = f.evolution_health_items(t, state, findings)
    return rows, lines

@case
def the_items_run_in_order_with_real_executors_and_map_their_status():
    import main as m
    from agent import inherited_health, outsider_walk

    with tempfile.TemporaryDirectory() as tmp, Patch() as p:
        project = Path(tmp) / "app"
        write(project, "frontend/package.json", "{}")
        write(project, "frontend/src/main.tsx", "x")
        time.sleep(0.05)
        write(project, "frontend/dist/index.html", "<html></html>")
        p.set(m, "build_frontend", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no build expected")))
        p.set(outsider_walk, "plan_walk_report", lambda *a, **k: {"completed": True, "notes": [], "scenarios": [
            {"leaf": "REQ-3", "scenario": "s", "status": "clean", "findings": []}]})
        grid_calls = []

        def grid(req, project_dir, *, time_limit, log, node_modules, items):
            grid_calls.append(items)
            return [{"item": "recalculation", "status": "finding", "result": "B shows #REF! after clearing A",
                     "findings": ["dependent shows #REF!"]},
                    {"item": "extent", "status": "clean", "result": "far cell rendered", "findings": []}]

        def session(req, project_dir, *, time_limit, log, node_modules):
            return {"item": "session", "status": "clean", "result": "clean (revoke not run: no revoke route)",
                    "findings": [], "parts": {"signout_isolation": "clean", "revoke": "not run"}}
        fake_probe_modules(p, grid, session)
        f = factory(project, ["REQ-3", "REQ-4"], CLASSES)
        t = tree()
        rows, lines = run_items(f, t, findings=[])
        text = "\n\n".join(m.Factory.leaf_requirement_text(x) for x in t["children"][2:])
        assert [r["item"] for r in rows] == [k for k, _ in inherited_health.health_check_items(text)], rows
        by = {r["item"]: r for r in rows}
        assert by["recalculation"]["status"] == "findings" and by["recalculation"]["findings"] == ["dependent shows #REF!"]
        assert by["extent"]["status"] == "clean" and by["session"]["status"] == "clean"
        assert by["session"]["parts"]["revoke"] == "not run"
        assert by["entry"]["status"] == "clean" and by["entry"]["build"] == "present"
        assert grid_calls == [("recalculation", "extent")], grid_calls
        assert by["modified"]["status"] == "not run" and by["modified"]["why"], by["modified"]
        assert all(r["status"] in ("findings", "clean", "not run") for r in rows)
        assert all(r.get("why") and r.get("result") for r in rows if r["status"] == "not run"), rows
        assert f.evolution["health"]["seconds"] >= 0
        assert any(line.startswith("[evolution] start-up health check took") for line in lines)

@case
def nothing_that_did_not_execute_is_clean():
    import main as m
    from agent import outsider_walk

    with tempfile.TemporaryDirectory() as tmp, Patch() as p:
        project = Path(tmp) / "app"
        write(project, "frontend/package.json", "{}")
        write(project, "frontend/src/main.tsx", "x")

        p.set(m, "build_frontend", lambda *a, **k: m.StepResult(True, "ok"))
        p.set(outsider_walk, "plan_walk_report", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no walk")))
        fake_probe_modules(p, lambda *a, **k: (_ for _ in ()).throw(AssertionError("no grid")),
                           lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        f = factory(project, ["REQ-3", "REQ-4"], CLASSES)
        rows, _ = run_items(f, tree(), findings=[])
        by = {r["item"]: r for r in rows}
        for key in ("entry", "recalculation", "extent"):
            assert by[key]["status"] == "not run" and "no built frontend" in by[key]["why"], by[key]
        assert by["session"]["status"] == "not run" and "RuntimeError" in by["session"]["why"], by["session"]

        fake_probe_modules(p, lambda *a, **k: None, lambda *a, **k: "garbage")
        write(project, "frontend/dist/index.html", "<html></html>")
        p.set(outsider_walk, "plan_walk_report", lambda *a, **k: {"completed": False, "notes": ["x"], "scenarios": []})
        f = factory(project, ["REQ-3", "REQ-4"], CLASSES)
        rows, _ = run_items(f, tree(), findings=[])
        by = {r["item"]: r for r in rows}
        assert {by[k]["status"] for k in ("entry", "recalculation", "extent", "session")} == {"not run"}, rows

        with settings_with(evolution_health_cap_sec=5):
            f = factory(project, ["REQ-3", "REQ-4"], CLASSES)
            rows, _ = run_items(f, tree(), findings=[])
        by = {r["item"]: r for r in rows}
        for key in ("entry", "recalculation", "extent", "session"):
            assert by[key]["status"] == "not run" and "used up" in by[key]["why"], by[key]

        with settings_with(evolution_health_probes=False):
            f = factory(project, ["REQ-3", "REQ-4"], CLASSES)
            rows, _ = run_items(f, tree(), findings=[])
        by = {r["item"]: r for r in rows}
        for key in ("recalculation", "extent", "session"):
            assert by[key]["status"] == "not run" and "evolution_health_probes is off" in by[key]["why"], by[key]
        assert not any(r["status"] == "passed" for r in rows)

@case
def the_build_runs_only_when_the_dist_is_missing_or_stale():
    import main as m
    from agent import clock

    with tempfile.TemporaryDirectory() as tmp, Patch() as p:
        project = Path(tmp) / "app"
        write(project, "frontend/package.json", "{}")
        write(project, "frontend/src/main.tsx", "x")
        calls = []

        def build(project_dir, log=print, deadline=None):
            calls.append(deadline)
            write(project_dir, "frontend/dist/index.html", "<html></html>")
            return m.StepResult(True, "built")
        p.set(m, "build_frontend", build)
        f = factory(project, ["REQ-3"], CLASSES)
        end = clock.now() + 600
        got = f.evolution_health_build(end)
        assert got["status"] == "built" and got["dist"] and len(calls) == 1 and calls[0] <= end, (got, calls)
        got = f.evolution_health_build(end)
        assert got["status"] == "present" and len(calls) == 1, got
        time.sleep(0.05)
        write(project, "frontend/src/main.tsx", "y")
        assert f.evolution_health_build(end)["status"] == "built" and len(calls) == 2
        p.set(m, "build_frontend", lambda *a, **k: m.StepResult(False, "error TS2304: Cannot find name"))
        time.sleep(0.05)
        write(project, "frontend/src/main.tsx", "z")
        got = f.evolution_health_build(end)
        assert got["status"] == "build failed" and not got["dist"] and "TS2304" in got["detail"], got
        assert f.evolution_health_build(clock.now() + 30)["status"] == "not built"
        with settings_with(evolution_health_build=False):
            assert f.evolution_health_build(end)["detail"] == "evolution_health_build is off"

def walk_row(leaf_id, name, outcome="missing", route="/"):
    return {"leaf": leaf_id, "scenario": f"{leaf_id} s", "status": "finding",
            "findings": [{"kind": "click", "name": name, "outcome": outcome, "route": route, "detail": "", "visible": ["Hub"]}]}

@case
def entry_findings_are_classed_inherited_or_own():
    with tempfile.TemporaryDirectory() as tmp:
        f = factory(Path(tmp) / "app", ["REQ-3", "REQ-4"], CLASSES)
        walk = {"completed": True, "scenarios": [
            walk_row("REQ-2", "Issues"),
            walk_row("REQ-3", "Sign in"),
            walk_row("REQ-4", "sign in", ),
            walk_row("REQ-3", "Active sessions"),
            walk_row("REQ-4", "Formula bar"),
            walk_row("REQ-3", "Sign in", outcome="sign-in-failed"),
            {"leaf": "REQ-4", "scenario": "x", "findings": [{"name": "Issues", "outcome": "ambiguous", "route": "/"}]}]}
        got = f.entry_findings_classed(walk, tree(), {"REQ-3", "REQ-4"})
        cls = {(g["key"][2], g["key"][0]): g["class"] for g in got}
        assert cls[("Issues", "missing")] == "inherited" and cls[("Sign in", "missing")] == "inherited"
        assert cls[("sign in", "missing")] == "inherited"
        assert cls[("Active sessions", "missing")] == "own" and cls[("Formula bar", "missing")] == "own"
        assert cls[("Sign in", "sign-in-failed")] == "inherited"
        assert not any(g["key"][0] == "ambiguous" for g in got)
        assert [g["class"] for g in got] == sorted([g["class"] for g in got], key=lambda c: c != "inherited")
        issues = next(g for g in got if g["key"][2] == "Issues")
        assert issues["leaves"] == ["REQ-2"] and "prerequisite" in issues["why"] and issues["fix"]

@case
def the_entry_item_hands_only_inherited_findings_as_debt():
    from agent import outsider_walk

    with tempfile.TemporaryDirectory() as tmp, Patch() as p:
        project = Path(tmp) / "app"
        p.set(outsider_walk, "plan_walk_report", lambda *a, **k: {"completed": True, "notes": [], "scenarios": [
            walk_row("REQ-2", "Issues"), walk_row("REQ-3", "Active sessions")]})
        f = factory(project, ["REQ-3", "REQ-4"], CLASSES)
        got = f.evolution_entry_walk(tree(), ["REQ-3", "REQ-4"], time_limit=60)
        assert got["status"] == "findings" and len(got["debt"]) == 1 and got["debt"][0]["key"][2] == "Issues", got
        assert len(got["own"]) == 1 and "1 finding(s) on inherited entry paths" in got["result"], got

def health_book(entry_debt=None, probes=None):
    rows = [{"item": "owners", "status": "clean"}]
    if entry_debt is not None:
        rows.append({"item": "entry", "status": "findings" if entry_debt else "clean", "debt": entry_debt})
    for key, findings in (probes or {}).items():
        rows.append({"item": key, "status": "findings" if findings else "clean", "findings": findings})
    return {"health_check": rows}

def debt(name, leaves):
    return {"key": ["missing", "/", name], "problem": f"Step “{name}”: missing on /.", "fix": f"Expose “{name}”.",
            "leaves": leaves, "class": "inherited", "scenarios": 1}

@case
def debt_goes_to_the_batch_that_first_needs_it():
    with tempfile.TemporaryDirectory() as tmp:
        f = factory(Path(tmp) / "app", ["REQ-3", "REQ-4"], CLASSES)
        f.evolution["health"] = health_book([debt("Issues", ["REQ-2"]), debt("Sign in", ["REQ-3", "REQ-4"])],
                                            {"recalculation": ["B shows #REF! after A is cleared."], "session": []})
        work = [{"id": "REQ-3", "members": ["REQ-3"]}, {"id": "REQ-4", "members": ["REQ-4"]}]
        with Logs() as lines:
            f.evolution_debt_attach(work, tree())

        first, second = work
        assert "Sign in" in first["evolution_startup_debt"] and "Issues" not in first["evolution_startup_debt"], first
        assert "Issues" in second["evolution_startup_debt"] and "[recalculation]" in second["evolution_startup_debt"]
        assert [d["key"][2] for d in first["evolution_entry_debt"]] == ["Sign in"]
        assert [d["key"][2] for d in second["evolution_entry_debt"]] == ["Issues"]
        assert "[recalculation]" not in first["evolution_startup_debt"]
        assert f.evolution["health"]["startup_debt"] == {"REQ-3": {"findings": 1, "entry": 1}, "REQ-4": {"findings": 2, "entry": 1}}
        assert any("handed as inherited debt" in line for line in lines)

        again = [{"id": "REQ-3", "members": ["REQ-3"]}, {"id": "REQ-4", "members": ["REQ-4"]}]
        f.evolution_debt_attach(again, tree())
        assert again == work

@case
def debt_is_bounded_falls_back_to_the_first_batch_and_can_be_switched_off():
    with tempfile.TemporaryDirectory() as tmp:
        f = factory(Path(tmp) / "app", ["REQ-3", "REQ-4"], CLASSES)
        many = [debt(f"Control {n} " + "x" * 200, ["REQ-3"]) for n in range(30)]
        f.evolution["health"] = health_book(many, {"extent": ["the grid renders only A-J x 20"]})
        work = [{"id": "REQ-3", "members": ["REQ-3"]}]
        f.evolution_debt_attach(work, tree())
        note = work[0]["evolution_startup_debt"]
        assert len(note) <= f.STARTUP_DEBT_CAP and "more not shown" in note, len(note)
        f.evolution["health"] = health_book([], {"extent": ["the grid renders only A-J x 20"]})
        work = [{"id": "REQ-3", "members": ["REQ-3"]}]
        f.evolution_debt_attach(work, tree())
        assert "[extent]" in work[0]["evolution_startup_debt"] and "evolution_entry_debt" not in work[0]
        with settings_with(evolution_startup_debt=False):
            work = [{"id": "REQ-3", "members": ["REQ-3"]}]
            f.evolution_debt_attach(work, tree())
            assert work == [{"id": "REQ-3", "members": ["REQ-3"]}]
        with settings_with(evolution_entry_debt_slot=False):
            f.evolution["health"] = health_book([debt("Issues", ["REQ-2"])])
            work = [{"id": "REQ-4", "members": ["REQ-4"]}]
            f.evolution_debt_attach(work, tree())
            assert "Issues" in work[0]["evolution_startup_debt"] and "evolution_entry_debt" not in work[0]

        f.evolution = {}
        work = [{"id": "REQ-3"}]
        f.evolution_debt_attach(work, tree())
        assert work == [{"id": "REQ-3"}]

def slot_factory(tmp: Path, walk_answers, repair_ok=True, foundation=None):
    import main as m
    from agent import outsider_walk

    f = factory(tmp / "app", ["REQ-3", "REQ-4"], CLASSES)
    calls = {"repair": [], "restore": 0, "walks": 0, "e2e": []}
    answers = list(walk_answers)

    def walk(*a, **k):
        calls["walks"] += 1
        return answers.pop(0) if answers else {"completed": True, "scenarios": []}

    def repair(label, check, text, test_files=None, deadline=None):
        calls["repair"].append((label, deadline, text))
        outcome = check()
        return bool(repair_ok and outcome.ok)
    f.repair_round = repair
    f.snapshot_sources = lambda: {"a": b"1"}

    def restore(snapshot):
        calls["restore"] += 1
        return 1
    f.restore_sources = restore
    f.protected_specs = lambda: ["backend/test-e2e/REQ-1.spec.js"] if foundation is not None else []
    f.foundation_run_list = lambda *a, **k: ["backend/test-e2e/REQ-1.spec.js"]
    f.continuation = {"baseline": "taken", "baseline_red": {"REQ-9.spec.js": {}}}
    patches = Patch()
    patches.set(outsider_walk, "plan_walk_report", walk)
    patches.set(m, "build_frontend", lambda *a, **k: m.StepResult(True, "built"))
    patches.set(m, "run_e2e", lambda project_dir, specs, log=print, **k: (calls["e2e"].append(specs) or foundation))
    return f, calls, patches

@case
def the_entry_debt_slot_is_bounded_kept_only_when_clean_and_never_repeated():
    import main as m
    from agent import clock

    with tempfile.TemporaryDirectory() as tmp:
        clean = {"completed": True, "scenarios": [{"leaf": "REQ-2", "findings": []}]}
        f, calls, patches = slot_factory(Path(tmp), [clean], foundation=m.StepResult(True, "1 passed"))
        node = {"id": "REQ-4", "members": ["REQ-4"], "evolution_entry_debt": [debt("Issues", ["REQ-2"])],
                "evolution_startup_debt": "INHERITED DEFECTS ..."}
        with patches, Logs() as lines:
            began = clock.now()
            f.evolution_entry_debt_slot(node)
            f.evolution_entry_debt_slot(node)
        assert len(calls["repair"]) == 1 and calls["walks"] == 1, calls
        label, deadline, text = calls["repair"][0]
        assert label == "inherited-entry" and deadline <= began + 900 + 1 and "“Issues”" in text, calls["repair"]
        assert calls["e2e"] == [["backend/test-e2e/REQ-1.spec.js"]] and calls["restore"] == 0
        assert f.evolution["health"]["entry_slots"]["REQ-4"]["status"] == "kept"
        assert "repaired in a slot" in node["evolution_startup_debt"]
        assert any("entry-debt repair slot kept" in line for line in lines), lines
    with tempfile.TemporaryDirectory() as tmp:

        still = {"completed": True, "scenarios": [walk_row("REQ-2", "Issues")]}
        f, calls, patches = slot_factory(Path(tmp), [still], foundation=m.StepResult(True, "ok"))
        node = {"id": "REQ-4", "evolution_entry_debt": [debt("Issues", ["REQ-2"])]}
        with patches, Logs():
            f.evolution_entry_debt_slot(node)
        assert f.evolution["health"]["entry_slots"]["REQ-4"]["status"] == "not kept" and calls["e2e"] == []
    with tempfile.TemporaryDirectory() as tmp:

        clean = {"completed": True, "scenarios": []}
        red = m.StepResult(False, "  1) REQ-1.spec.js:3:1 › signs in\n  1 failed")
        f, calls, patches = slot_factory(Path(tmp), [clean], foundation=red)
        node = {"id": "REQ-4", "evolution_entry_debt": [debt("Issues", ["REQ-2"])]}
        with patches, Logs():
            f.evolution_entry_debt_slot(node)
        assert calls["restore"] == 1 and f.evolution["health"]["entry_slots"]["REQ-4"]["status"] == "put back", calls
        assert "repaired in a slot" not in str(node.get("evolution_startup_debt") or "")
    with tempfile.TemporaryDirectory() as tmp:

        f, calls, patches = slot_factory(Path(tmp), [{"completed": False, "notes": ["deadline"], "scenarios": []}])
        node = {"id": "REQ-4", "evolution_entry_debt": [debt("Issues", ["REQ-2"])]}
        with patches, Logs():
            f.evolution_entry_debt_slot(node)
        assert f.evolution["health"]["entry_slots"]["REQ-4"]["status"] == "not kept"

@case
def repair_round_honours_a_slot_deadline():
    import main as m
    from agent import clock

    with tempfile.TemporaryDirectory() as tmp:
        f = factory(Path(tmp) / "app", [], {})
        f.over_budget = lambda: False
        f.started, f.time_budget = clock.now(), 99999.0
        f.hard_deadline = lambda end, what, round_sec=0.0: end
        f.hard_end = lambda: None
        with Logs() as lines:
            assert f.repair_round("inherited-entry", lambda: m.StepResult(False, "x"), "text", deadline=clock.now() + 60) is False
        assert any("of its slot left, no repair round" in line for line in lines), lines
    source = inspect.getsource(m.Factory.repair_round)
    assert "deadline = min(deadline, slot_end)" in source

@case
def the_budget_grows_by_the_health_check_and_its_slots():
    with tempfile.TemporaryDirectory() as tmp:
        f = factory(Path(tmp) / "app", ["REQ-3"], CLASSES)
        f.evolution["health"] = {"seconds": 120.5}
        work = [{"id": "A", "evolution_entry_debt": [1]}, {"id": "B"}, {"id": "C", "evolution_entry_debt": [1]}]
        assert f.evolution_extra_budget(work) == 120.5 + 2 * 900
        with settings_with(evolution_entry_debt_slot=False):
            assert f.evolution_extra_budget(work) == 120.5
        f.evolution = {}
        assert f.evolution_extra_budget(work) == 0.0

@case
def modified_reads_the_baseline_verdicts_of_the_inherited_tests():
    with tempfile.TemporaryDirectory() as tmp:
        f = factory(Path(tmp) / "app", ["REQ-3", "REQ-4"], CLASSES)
        f.evolution["registry"] = {"files": {"backend/test-e2e/REQ-3.spec.js": {"tests": [
            {"title": "REQ-3 a", "leaf": "REQ-3", "class": "unverified"},
            {"title": "REQ-3 b", "leaf": "REQ-3", "class": "unverified"},
            {"title": "REQ-1 c", "leaf": "REQ-1", "class": "valid"}]}}}
        f.continuation = {"baseline": "skipped"}
        f.evolution["tests"] = {"backend/test-e2e/REQ-3.spec.js": {"REQ-3 a": "not judged", "REQ-3 b": "not judged"}}
        got = f.evolution_modified_item(f.evolution)
        assert got["status"] == "not run" and "baseline was not taken (skipped)" in got["why"], got
        f.continuation = {"baseline": "taken"}
        f.evolution["tests"] = {"backend/test-e2e/REQ-3.spec.js": {"REQ-3 a": "passed", "REQ-3 b": "failed", "REQ-1 c": "failed"}}
        got = f.evolution_modified_item(f.evolution)
        assert got["status"] == "findings" and "REQ-3 1 passed / 1 failed / 0 not judged" in got["result"], got
        assert got["findings"] and "REQ-3 b" in got["findings"][0] and "REQ-1" not in got["findings"][0]
        f.evolution["tests"] = {"backend/test-e2e/REQ-3.spec.js": {"REQ-3 a": "passed", "REQ-3 b": "passed"}}
        assert f.evolution_modified_item(f.evolution)["status"] == "clean"
        f.evolution["implement"] = ["REQ-4"]
        assert f.evolution_modified_item(f.evolution)["why"] == "no modified leaf"

@case
def the_wiring_sits_where_it_should():
    import main as m

    run = inspect.getsource(m.Factory.run)
    stamp = run.index("self.stamp_inherited_names(work, tree)")
    attach = run.index("self.evolution_debt_attach(work, tree)")
    budget = run.index("extra = self.evolution_extra_budget(work) if evo else 0.0")
    assert stamp < attach < budget < run.index("all_passed = self.run_queue(")
    assert "LARGE_TREE_BASE_SEC + per_batch * len(work) + extra" in run
    queue = inspect.getsource(m.Factory.run_queue)
    slot = queue.index("self.evolution_entry_debt_slot(node)")
    assert queue.index('if not requeued and node.get("evolution_entry_debt"):') < slot < queue.index("ok = self.attempt_node(node, nodes_left=len(queue) + 1")
    node = inspect.getsource(m.Factory.process_node)
    inherited = node.index('inherited_debt = str(node.get("evolution_inherited_debt") or "")')
    startup = node.index('startup_debt = str(node.get("evolution_startup_debt") or "")')
    assert inherited < startup < node.index("initial_first = first")
    assert "retry_note = self.with_prerequisite_note(retry_note, startup_debt) if retry else retry_note" in node
    health = inspect.getsource(m.Factory.evolution_startup_health)
    assert "results = self.evolution_health_items(tree, state, findings)" in health

def deadline_factory(own_end):
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.report = {}
    f.hard_end_at = own_end
    return f

@case
def the_contest_deadline_is_read_as_utc_and_can_be_switched_off():
    import calendar
    import main as m

    with settings_with(contest_deadline_utc="2026-10-08T10:00:00Z", contest_deadline_margin_sec=3600):
        end, note = m.contest_deadline_end(now_wall=0.0)
        assert end == calendar.timegm((2026, 10, 8, 10, 0, 0)) - 3600, (end, note)
    with settings_with(contest_deadline_utc="2026-10-08T18:00:00+08:00", contest_deadline_margin_sec=0):
        assert m.contest_deadline_end()[0] == calendar.timegm((2026, 10, 8, 10, 0, 0))
    with settings_with(contest_deadline_utc="2026-10-08T10:00:00", contest_deadline_margin_sec=0):
        assert m.contest_deadline_end()[0] == calendar.timegm((2026, 10, 8, 10, 0, 0))
    for off in ("", "off", None):
        with settings_with(contest_deadline_utc=off):
            assert m.contest_deadline_end() == (None, "contest deadline: off")
    with settings_with(contest_deadline_utc="next tuesday"):
        end, note = m.contest_deadline_end()
        assert end is None and "unreadable" in note

@case
def the_earlier_end_wins_and_a_run_too_close_does_nothing():
    import main as m

    now = time.time()
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    with Patch() as p, Logs() as lines:
        moved = []
        p.set(m.watchdog, "_ACTIVE", types.SimpleNamespace(move_end=lambda t: moved.append(t)))
        p.set(m.watchdog, "set_llm_end", lambda factory: None)

        with settings_with(contest_deadline_utc=iso(now + 10 * 3600), contest_deadline_margin_sec=3600, contest_min_run_sec=7200):
            f = deadline_factory(now + 20 * 3600)
            assert f.apply_contest_deadline() is False
            assert abs(f.hard_end_at - (now + 9 * 3600)) < 2 and f.hard_end_mono is not None, f.hard_end_at - now
            assert moved and abs(moved[-1] - (now + 9.5 * 3600)) < 2
            assert "the contest deadline wins" in lines[-1], lines

        moved.clear()
        with settings_with(contest_deadline_utc=iso(now + 40 * 3600), contest_deadline_margin_sec=3600):
            f = deadline_factory(now + 20 * 3600)
            assert f.apply_contest_deadline() is False and f.hard_end_at == now + 20 * 3600 and not moved
            assert "the platform limit wins" in lines[-1], lines

        for left in (-3600, 3600):
            with settings_with(contest_deadline_utc=iso(now + left + 3600), contest_deadline_margin_sec=3600, contest_min_run_sec=7200):
                f = deadline_factory(now + 20 * 3600)
                assert f.apply_contest_deadline() is True and f.report["contest_deadline"]["skipped"] is True
                assert "nothing is done" in lines[-1]

        with settings_with(contest_deadline_utc=""):
            f = deadline_factory(now + 20 * 3600)
            assert f.apply_contest_deadline() is False and f.hard_end_at == now + 20 * 3600
    run = inspect.getsource(m.Factory.run)
    assert run.index("if self.apply_contest_deadline():") < run.index("clock.start(log=log)") < run.index("tree = load_tree(")
    assert "contest_end, _note = contest_deadline_end()" in inspect.getsource(m.main)

@case
def the_evolution_run_target_the_platform_limit_and_the_contest_deadline_each_win_in_turn():
    import main as m

    def run_with(target, platform_limit, contest_in_h):
        now = time.time()
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(m.PROCESS_STARTED + contest_in_h * 3600)) if contest_in_h else ""
        values = dict(evolution_run_target_sec=target, platform_time_limit_sec=platform_limit, platform_time_margin_sec=5400,
                      contest_deadline_utc=iso, contest_deadline_margin_sec=3600, contest_min_run_sec=0)
        with settings_with(**values), Patch() as p, Logs() as lines:
            moved = []
            p.set(m.watchdog, "_ACTIVE", types.SimpleNamespace(move_end=lambda t: moved.append(t)))
            p.set(m.watchdog, "set_llm_end", lambda factory: None)
            f = deadline_factory(m.platform_hard_end(m.PROCESS_STARTED))
            f.apply_contest_deadline()
            f.apply_evolution_run_target()
        del now
        return f, lines, moved
    h = lambda f: (f.hard_end_at - m.PROCESS_STARTED) / 3600.0

    f, lines, moved = run_with(54000, 86400, 40)
    assert abs(h(f) - 15.0) < 0.01 and f.stage_target == 54000 and "the run target wins" in lines[-1], lines
    assert "platform limit 22.50 h" in lines[-1] and "contest deadline 39.00 h" in lines[-1]
    assert moved and abs(moved[-1] - (m.PROCESS_STARTED + 54000 + m.setting_seconds("stage_watchdog_slack_sec", 2700.0))) < 1

    f, lines, _ = run_with(30 * 3600, 86400, 40)
    assert abs(h(f) - 22.5) < 0.01 and "the platform limit wins" in lines[-1], lines

    f, lines, _ = run_with(54000, 86400, 10)
    assert abs(h(f) - 9.0) < 0.01 and "the contest deadline wins" in lines[-1], lines

    f, lines, _ = run_with(0, 86400, 40)
    assert abs(h(f) - 22.5) < 0.01 and getattr(f, "stage_target", None) is None and "off" in lines[-1]

    with settings_with(evolution_run_target_sec=54000), Logs() as lines:
        g = deadline_factory(m.PROCESS_STARTED + 14400)
        g.stage_target = 14400.0
        g.apply_evolution_run_target()
        assert g.stage_target == 14400.0 and g.hard_end_at == m.PROCESS_STARTED + 14400 and "kept" in lines[-1]

    run = inspect.getsource(m.Factory.run)
    assert "if evo:\n            self.apply_evolution_run_target()" in run
    assert run.index("self.apply_evolution_run_target()") < run.index("if getattr(self, \"stage_target\", None):")
    from agent import settings
    assert settings._DEFAULTS["evolution_run_target_sec"] == 57600

def regression_factory(tmp: Path, protected: list[str], green: list[str]):
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.project_dir = tmp
    f.green_specs = list(green)
    f.continuation = {"protected": list(protected)}
    f.file_specs = {}
    f.durations = {"e2e": [(30.0, 1)]}
    f._inherited_spec_files = {}
    return f

@case
def the_regression_runs_a_window_first_and_the_full_set_before_green():
    import main as m

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write(root, "backend/src/app.js", "new")
        protected = [f"backend/test-e2e/REQ-{n}.spec.js" for n in range(1, 21)]
        own_green = ["backend/test-e2e/REQ-30.evo.spec.js"]
        f = regression_factory(root, protected, protected + own_green)
        f._inherited_spec_files = {"REQ-12.spec.js": {"backend/src/app.js"}}
        before = {"backend/src/app.js": b"old"}
        regression = protected + own_green
        with Logs():
            first, rest = f.regression_split("REQ-30", regression, before)
        assert set(first) | set(rest) == set(regression) and not set(first) & set(rest)
        for must in protected[:3] + own_green + ["backend/test-e2e/REQ-12.spec.js"]:
            assert must in first, (must, first)
        assert len(rest) > 0 and len(first) < len(regression)

        with Logs():
            first2, _ = f.regression_split("REQ-30", regression, before)
        assert set(first2) != set(first)
        calls = []

        def fake_run(project_dir, specs, log=print, **k):
            calls.append(list(specs))
            if fake_run.red and specs == calls[0]:
                return m.StepResult(False, "FAILED SPEC FILES: REQ-1.spec.js\n  1) REQ-1.spec.js:3:1 › x")
            return m.StepResult(True, f"{len(specs)} of {len(specs)} spec files passed", ran=len(specs))
        with Patch() as p, Logs():
            p.set(m, "run_e2e", fake_run)
            fake_run.red = False
            got = f.run_regression("REQ-30", regression, before)
            assert got.ok and len(calls) == 2 and set(calls[0]) | set(calls[1]) == set(regression), calls
            assert got.ran == len(regression)
            calls.clear()
            fake_run.red = True
            got = f.run_regression("REQ-30", regression, before)
            assert not got.ok and len(calls) == 1, calls
            unrun = m.not_run_spec_names(got.output)
            assert unrun and unrun <= {Path(s).name for s in regression} and "REQ-1.spec.js" in m.failed_spec_names(got.output)
            assert not m.run_unfinished(got) and m.not_run_spec_names(got.output) == {Path(s).name for s in regression if s not in calls[0]}

            calls.clear()
            p.set(m, "run_e2e", lambda project_dir, specs, log=print, **k: (calls.append(specs) or m.StepResult(True, "x", ran=0)))
            got = f.run_regression("REQ-30", regression, before)
            assert not got.ok and len(calls) == 1 and m.not_run_spec_names(got.output)

        with settings_with(regression_window=False):
            assert f.regression_split("REQ-30", regression, before) is None
        g = regression_factory(root, [], own_green)
        assert g.regression_split("REQ-30", own_green, before) is None
        small = regression_factory(root, protected[:3], protected[:3] + own_green)
        assert small.regression_split("REQ-30", protected[:3] + own_green, before) is None
    node = inspect.getsource(m.Factory.process_node) if "run_regression" in inspect.getsource(m.Factory.process_node) else ""
    source = inspect.getsource(m.Factory)
    assert "again = (self.run_regression(req_id, regression, batch_before, **until_kw(phase_end.get(\"end\"))) if regression" in source
    del node

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
