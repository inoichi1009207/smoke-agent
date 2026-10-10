from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

NL = chr(10)
CASES = []

SPEC = "backend/test-e2e/REQ-5-1-1.spec.js"
PATH_PROBLEM = ('PROBLEM: PATH | the spec of "REQ-5-1-1: S1" clicks "acme-docs" before "Issues", but the scenario never '
                'names a click on "acme-docs" | follow the scenario\'s steps exactly')
ROLE_PROBLEM = 'PROBLEM: ROLE | STEP | "Sign in" is a link on the home page, the spec clicks a button | use the stated role'
HINT_PROBLEM = 'PROBLEM: LATER | uses "Wiki", a control of a later requirement'

def case(function):
    CASES.append(function)
    return function

def bare_factory(root: Path):
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.project_dir = root
    f.report, f.open_findings, f.spec_defects, f.revised_specs = {}, {}, {}, set()
    return f

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

@case
def the_b_switches_are_registered_with_their_defaults():
    from agent import settings

    d = settings._DEFAULTS
    assert (d["spec_plan_roles"], d["later_controls_plan"], d["spec_plan_block"], d["spec_defect_outcome"],
            d["det_plan_walk"], d["det_plan_walk_sec"]) == (True, True, False, True, True, 300)
    assert "seed_absence_check" in d and d["seed_absence_check"] is False

@case
def certain_defects_left_after_the_rewrites_are_booked_and_hints_are_not():
    with tempfile.TemporaryDirectory() as tmp:
        f = bare_factory(Path(tmp))
        node = {"id": "REQ-5-1-1"}
        with Logs() as lines:
            f.settle_spec_defect(node, SPEC, [HINT_PROBLEM])
            assert f.spec_defects == {} and f.open_findings == {} and SPEC not in f.revised_specs
            f.settle_spec_defect(node, SPEC, [HINT_PROBLEM, PATH_PROBLEM, ROLE_PROBLEM])
        entry = f.spec_defects[SPEC]
        assert entry["batch"] == "REQ-5-1-1" and entry["repair_done"] is False
        assert entry["problems"] == [PATH_PROBLEM, ROLE_PROBLEM], entry
        assert SPEC in f.revised_specs
        assert f.open_findings["spec-defect:" + SPEC]["class"] == "spec defect"
        assert f.open_findings["spec-defect:" + SPEC]["where"].startswith(SPEC + ": PROBLEM: PATH")
        assert any("one SPEC repair in the batch's test-defect budget" in line for line in lines), lines

        with Logs():
            f.settle_spec_defect(node, SPEC, [])
        assert f.spec_defects == {} and f.open_findings == {} and SPEC in f.revised_specs

@case
def the_switch_off_books_nothing():
    from agent import settings

    saved = dict(settings.SETTINGS)
    try:
        settings.SETTINGS["spec_defect_outcome"] = False
        with tempfile.TemporaryDirectory() as tmp:
            f = bare_factory(Path(tmp))
            with Logs():
                f.settle_spec_defect({"id": "X"}, SPEC, [PATH_PROBLEM])
                files, tests, used = f.repair_spec_defects("X", {"id": "X"}, "X", [], {SPEC: "x"}, "")
            assert f.spec_defects == {} and used == 0
    finally:
        settings.SETTINGS.clear()
        settings.SETTINGS.update(saved)

def fake_review(answers: list[list[str]]):
    calls = []

    def review(node, spec, prefix, log=print):
        calls.append(spec.content)
        return answers[min(len(calls) - 1, len(answers) - 1)]
    return review, calls

@case
def the_recheck_books_what_survives_the_second_rewrite():
    import main as m
    from agent import outsider
    from agent.test_author import AuthoredFile

    saved_review, saved_author = outsider.deterministic_review, m.author_acceptance
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for answers, booked in (([[PATH_PROBLEM], [PATH_PROBLEM]], True),
                                    ([[PATH_PROBLEM], [HINT_PROBLEM]], False),
                                    ([[PATH_PROBLEM], []], False)):
                f = bare_factory(Path(tmp))
                f.llm = None
                review, calls = fake_review(answers)
                outsider.deterministic_review = review
                m.author_acceptance = lambda llm, node, prefix, problem="", current="", log=print: AuthoredFile(SPEC, "v2")
                with Logs():
                    got = f.recheck_rewritten_spec({"id": "REQ-5-1-1"}, "REQ-5-1-1", AuthoredFile(SPEC, "v1"))
                assert got.content == "v2" and calls == ["v1", "v2"]
                assert (SPEC in f.spec_defects) is booked, (answers, f.spec_defects)

            f = bare_factory(Path(tmp))
            f.llm = None
            outsider.deterministic_review, _ = fake_review([[ROLE_PROBLEM]])
            m.author_acceptance = lambda *a, **k: None
            with Logs():
                got = f.recheck_rewritten_spec({"id": "REQ-5-1-1"}, "REQ-5-1-1", AuthoredFile(SPEC, "v1"))
            assert got.content == "v1" and f.spec_defects[SPEC]["problems"] == [ROLE_PROBLEM]
    finally:
        outsider.deterministic_review, m.author_acceptance = saved_review, saved_author

@case
def a_booked_spec_gets_one_spec_repair_in_the_test_defect_budget():
    import main as m
    from agent.test_author import AuthoredFile

    with tempfile.TemporaryDirectory() as tmp:
        f = bare_factory(Path(tmp))
        node = {"id": "REQ-5-1-1"}
        with Logs():
            f.settle_spec_defect(node, SPEC, [PATH_PROBLEM])
        seen = []

        def fake_within(req_id, node_, prefix, files, test_files, problem, contract, allowed):
            seen.append((problem, allowed))

            f.settle_spec_defect(node_, SPEC, [PATH_PROBLEM])
            return [AuthoredFile(SPEC, "v3")], {SPEC: "v3"}

        f.repair_tests_within = fake_within
        saved_static = m.static_test_defects
        m.static_test_defects = lambda project_dir, paths, **kw: ""
        try:
            with Logs() as lines:
                files, tests, used = f.repair_spec_defects("REQ-5-1-1", node, "REQ-5-1-1", [AuthoredFile(SPEC, "v1")],
                                                           {SPEC: "v1"}, "contract")
                again = f.repair_spec_defects("REQ-5-1-1", node, "REQ-5-1-1", files, tests, "contract")
        finally:
            m.static_test_defects = saved_static
        assert used == 1 and tests == {SPEC: "v3"} and again[2] == 0 and len(seen) == 1
        problem, allowed = seen[0]
        assert allowed is None and problem.startswith("The acceptance spec contradicts the requirement text:")
        assert "REQ-5-1-1.spec.js: PROBLEM: PATH" in problem

        import re
        assert re.search(r"(?m)^(?:The acceptance spec contradicts the requirement text:|\s*SPEC:)", problem)

        assert f.spec_defects[SPEC]["repair_done"] is True and "spec-defect:" + SPEC in f.open_findings
        assert any("booked open (spec defect)" in line for line in lines), lines

        g = bare_factory(Path(tmp))
        assert g.repair_spec_defects("X", node, "X", [], {SPEC: "v"}, "")[2] == 0

@case
def a_repaired_spec_that_does_not_parse_gets_one_more_repair():
    import main as m
    from agent.test_author import AuthoredFile

    with tempfile.TemporaryDirectory() as tmp:
        f = bare_factory(Path(tmp))
        with Logs():
            f.settle_spec_defect({"id": "R"}, SPEC, [PATH_PROBLEM])
        seen = []
        f.repair_tests_within = lambda req_id, node, prefix, files, tests, problem, contract, allowed: (
            seen.append(problem) or ([AuthoredFile(SPEC, "x")], {SPEC: "x"}))
        saved_static = m.static_test_defects
        m.static_test_defects = lambda project_dir, paths, **kw: "SyntaxError in REQ-5-1-1.spec.js"
        try:
            with Logs():
                used = f.repair_spec_defects("R", {"id": "R"}, "R", [], {SPEC: "v"}, "")[2]
        finally:
            m.static_test_defects = saved_static
        assert used == 1 and len(seen) == 2 and seen[1].startswith("These test files do not parse / load:")

@case
def process_node_routes_the_repair_before_the_implementer_and_shares_the_budget():
    import inspect

    import main as m

    body = inspect.getsource(m.Factory.process_node)
    route = body.index("files, test_files, spec_defect_rounds = self.repair_spec_defects(req_id, node, prefix, files, test_files, node_contract)")
    assert body.index("static_test_defects(self.project_dir, list(test_files)") < route < body.index("initial run: {")
    assert route < body.index("result = implementer.implement(node, test_files, first")
    assert "defect_rounds = spec_defect_rounds" in body and "defect_rounds = 0" not in body
    save = inspect.getsource(m.Factory.save_checkpoint)
    load = inspect.getsource(m.Factory.load_checkpoint)
    assert '"spec_defects"' in save and 'state.get("spec_defects")' in load

@case
def inherited_leaves_names_are_stamped_on_every_batch_of_an_evolution_round():
    with tempfile.TemporaryDirectory() as tmp:
        f = bare_factory(Path(tmp))
        tree = {"id": "ROOT", "children": [
            {"id": "REQ-1", "name": "Issues", "description": "The repository page has an `Issues` tab.", "scenarios": [],
             "ancestor_context": "The product has a “Marketplace” link."},
            {"id": "REQ-2", "name": "Labels", "description": "The `New label` button opens the label form.", "scenarios": []},
            {"id": "REQ-3", "name": "Pins", "description": "The `Pin issue` button pins it.", "scenarios": []}]}
        old_tree = {"id": "ROOT", "children": [
            {"id": "REQ-1", "name": "Issues", "description": "The `Issues` tab.", "scenarios": []},
            {"id": "REQ-9", "name": "Wiki", "description": "The `Wiki` tab shows the pages.", "scenarios": []}]}
        f._old_tree = old_tree
        f.evolution = {"active": True, "implement": ["REQ-2", "REQ-3"],
                       "classes": {"classes": {"REQ-1": "inherited", "REQ-2": "modified", "REQ-3": "new"}}}
        work = [{"id": "REQ-2", "members": ["REQ-2"]}, {"id": "REQ-3", "members": ["REQ-3"]}]
        with Logs() as lines:
            f.stamp_inherited_names(work, tree)
        for batch in work:
            names = batch["inherited_names"]
            assert "Issues" in names and "Wiki" in names, names
            assert "New label" not in names and "Pin issue" not in names and "Marketplace" not in names, names
        assert any("name(s) of inherited leaves stamped" in line for line in lines)

        g = bare_factory(Path(tmp))
        plain = [{"id": "A"}]
        g.stamp_inherited_names(plain, tree)
        assert plain == [{"id": "A"}]

@case
def run_stamps_the_inherited_names_after_the_batch_names():
    import inspect

    import main as m

    run = inspect.getsource(m.Factory.run)
    stamp = run.index("stamp_batch_names(work)")
    assert stamp < run.index("self.stamp_inherited_names(work, tree)") < run.index("nodes = [(node, []) for node in work]")

@case
def the_outsider_reads_the_stamped_names():
    from agent import outsider
    import inspect

    assert 'node.get("inherited_names")' in inspect.getsource(outsider)

@case
def the_batch_audit_walks_the_batchs_own_plans_once_per_input_state():
    from agent import detaudit, outsider_walk, settings

    saved_settings, saved_walk = dict(settings.SETTINGS), outsider_walk.plan_walk_check
    calls = []

    def fake_walk(requirement, project_dir, time_limit=240.0, log=print, node_modules=None, leaf_ids=None):
        calls.append((list(leaf_ids or []), time_limit))
        return [{"where": "plan-walk: rendered app", "requirement": "REQ-2: S1", "problem": "missing", "fix": "add"}], True

    try:
        for key in list(settings.SETTINGS):
            if key.startswith("det_") and key not in ("det_plan_walk", "det_plan_walk_sec"):
                settings.SETTINGS[key] = False
        settings.SETTINGS["det_plan_walk"] = True
        outsider_walk.plan_walk_check = fake_walk
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "backend").mkdir()
            lines: list[str] = []
            assert detaudit.v149_findings(project, "WHOLE", None, log=lines.append) == []
            found = detaudit.v149_findings(project, "WHOLE", None, log=lines.append, leaf_ids=["REQ-2", "REQ-1"])
            again = detaudit.v149_findings(project, "WHOLE", None, log=lines.append, leaf_ids=["REQ-1", "REQ-2"])
            assert [f["problem"] for f in found] == ["missing"] and again == found
            assert calls == [(["REQ-1", "REQ-2"], 300.0)], calls
            assert any("plan walk: inputs unchanged" in line for line in lines), lines
            settings.SETTINGS["det_plan_walk"] = False
            assert detaudit.v149_findings(project, "WHOLE2", None, log=lines.append, leaf_ids=["REQ-3"]) == []
            assert len(calls) == 1
    finally:
        settings.SETTINGS.clear()
        settings.SETTINGS.update(saved_settings)
        outsider_walk.plan_walk_check = saved_walk
    import inspect
    source = inspect.getsource(detaudit.deterministic_findings)
    assert 'leaf_ids=[str(m) for m in (node.get("members") or [node.get("id")]) if m]' in source

@case
def the_entry_item_walks_the_round_and_its_prerequisites():
    import main as m
    from agent import outsider_walk

    tree = {"id": "ROOT", "children": [
        {"id": "REQ-1", "name": "Sign in", "description": "d", "scenarios": []},
        {"id": "REQ-2", "name": "Repos", "description": "d", "scenarios": [], "dependencies": ["REQ-1"]},
        {"id": "REQ-3", "name": "Stars", "description": "d", "scenarios": [], "dependencies": ["REQ-2"]},
        {"id": "REQ-4", "name": "Other", "description": "d", "scenarios": []}]}

    saved = outsider_walk.plan_walk_report
    calls = []

    def walk(rows, completed):
        return {"completed": completed, "notes": [], "scenarios": [
            {"leaf": leaf, "scenario": "s", "status": "finding", "findings": [
                {"kind": "click", "name": name, "outcome": "missing", "route": "/repos/x", "detail": "", "visible": []}]}
            for leaf, name in rows]}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "req").mkdir()
            (root / "req" / "requirements.yaml").write_text("id: ROOT", encoding="utf-8")
            f = bare_factory(root / "out")
            f.requirements_dir = root / "req"
            for answer, status in ((walk([("REQ-3", "Stars")], True), "findings"), (walk([], True), "clean"),
                                   (walk([], False), "not run"), (walk([("REQ-3", "p")], False), "findings")):
                outsider_walk.plan_walk_report = lambda *a, _answer=answer, **k: (calls.append(k.get("leaf_ids")) or _answer)
                got = f.evolution_entry_walk(tree, ["REQ-3"])
                assert got["item"] == "entry" and got["status"] == status, (answer, got)
                assert status != "findings" or answer["completed"] or "not run" in got["result"], got
            assert calls[0] == ["REQ-1", "REQ-2", "REQ-3"], calls
            outsider_walk.plan_walk_report = lambda *a, **k: 1 / 0
            assert f.evolution_entry_walk(tree, ["REQ-3"])["status"] == "not run"
    finally:
        outsider_walk.plan_walk_report = saved
    import inspect
    items = inspect.getsource(m.Factory.evolution_health_items)
    assert 'elif key == "entry" and not setting_on("det_plan_walk"):' in items

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

