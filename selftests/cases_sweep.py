from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NL = chr(10)
CASES = []

def case(function):
    CASES.append(function)
    return function

E2E = "backend/test-e2e/"

def _s(name: str) -> str:
    return E2E + name + ".spec.js"

@case
def the_sweep_runs_the_specs_in_dependency_order():
    from agent.shared_sweep import sweep_order

    a, b, c, d = _s("REQ-1-1"), _s("REQ-1-2"), _s("REQ-2-1"), _s("REQ-3")
    members = {a: ["REQ-1-1"], b: ["REQ-1-2"], c: ["REQ-2-1"], d: ["REQ-3-1", "REQ-3-2"]}

    assert sweep_order([a, b, c, d], members, {}) == [a, b, c, d]

    order = sweep_order([b, c, a], members, {"REQ-1-2": ["REQ-1-1"]})
    assert order == [c, a, b], order

    order = sweep_order([d, a], {a: ["REQ-1-1"], d: ["REQ-3-1", "REQ-3-2"]}, {"REQ-1-1": ["REQ-3"]})
    assert order == [d, a], order

    order = sweep_order([a, b], members, {"REQ-1-1": ["REQ-1-2"], "REQ-1-2": ["REQ-1-1"]})
    assert sorted(order) == sorted([a, b]) and len(order) == 2

    assert sweep_order([a, _s("X"), a, b], members, {}) == [a, _s("X"), b]

@case
def fresh_green_comes_only_from_a_run_that_names_its_files():
    from agent.shared_sweep import fresh_green_names

    specs = [_s("A"), _s("B"), _s("C"), _s("D")]
    assert fresh_green_names(specs, True, "") == {"A.spec.js", "B.spec.js", "C.spec.js", "D.spec.js"}
    out = "FAILED SPEC FILES: B.spec.js" + NL + "NOT RUN SPEC FILES: D.spec.js" + NL + "2 of 4 spec files passed"
    assert fresh_green_names(specs, False, out) == {"A.spec.js", "C.spec.js"}

    assert fresh_green_names(specs, False, "E2E FAILED against the real application" + NL + "No spec was run.") is None

@case
def only_a_recognised_failure_of_a_fresh_green_spec_is_reported():
    from agent.shared_sweep import red_only_in_shared

    a, b, c, d, e = _s("A"), _s("B"), _s("C"), _s("D"), _s("E")
    order = [a, b, c, d, e]
    green = {"A.spec.js", "C.spec.js", "D.spec.js", "E.spec.js"}
    statuses = {a: "passed", b: "failed", c: "failed", d: "unknown", e: "not_run"}
    found = red_only_in_shared(order, green, statuses)

    assert found == [(c, [a, b])], found
    statuses = {a: "passed", b: "passed", c: "unknown", d: "failed", e: "passed"}
    assert red_only_in_shared(order, green, statuses) == [(d, [a, b])]
    assert red_only_in_shared(order, green, {s: "passed" for s in order}) == []

@case
def shared_quoted_texts_name_the_earlier_spec_that_touches_the_same_record():
    from agent.shared_sweep import quoted_literals, shared_literals

    chrome = 'await click(page, "Sign in"); await fill(page, "Username", "alice-dev");'
    renamer = chrome + ' await fill(page, "Workbook name", "Budget 2026"); await notSee(page, "Q3 Sales");'
    reader = chrome + ' await click(page, { role: "link", name: "Q3 Sales" });'
    other = chrome + ' await see(page, "Settings");'
    every = {_s("A"): renamer, _s("B"): other, _s("C"): reader, _s("D"): chrome}
    shared = shared_literals(reader, {_s("A"): renamer, _s("B"): other}, every)

    assert shared == {_s("A"): ["Q3 Sales"]}, shared
    assert "link" not in quoted_literals(reader) and "../e2e-support/outsider" not in quoted_literals("require('../e2e-support/outsider')")
    assert shared_literals(other, {_s("D"): chrome}, every) == {}

@case
def the_summary_is_marked_advisory_and_names_the_files():
    from agent.shared_sweep import sweep_summary

    line = sweep_summary(3, {_s("A"): "passed", _s("B"): "failed", _s("C"): "not_run"}, [(_s("B"), [_s("A")])], "1 spec file(s) not run: the time for the sweep is used up")
    assert line.startswith("advisory, not in the verdict") and "B.spec.js" in line and "1 red only after earlier specs" in line
    assert "1 not run" in line and "time for the sweep" in line

def _fake_verify(verify, calls, *, reports, alive_after=99, codes=None):
    saved = {name: getattr(verify, name) for name in ("BackgroundProcess", "wait_http", "server_alive", "api_answers", "run_cmd",
                                                      "failing_spec_files", "tests_ran", "free_port", "fresh_data_env", "remove_dirs")}
    state = {"runs": 0, "servers": 0}

    class Server:
        def __init__(self, args, cwd, env):
            state["servers"] += 1
            calls.append(("server", env.get("DATA_DIR")))

        def stop(self):
            calls.append(("stop",))
            return "TypeError: Cannot read properties of undefined (reading 'id')" + NL + "    at list (routes.js:12:3)"

    def run_cmd(args, cwd, timeout, env):
        spec = args[3]
        calls.append(("run", spec, timeout))
        state["runs"] += 1
        state["last"] = spec
        return (codes or {}).get(spec, 0 if reports.get(spec) == "passed" else 1), "1) " + spec + NL + "Error: expect(locator).toBeVisible() failed"

    verify.BackgroundProcess = Server
    verify.wait_http = lambda url, timeout=60: (True, 200)
    verify.server_alive = lambda server: state["runs"] < alive_after
    verify.api_answers = lambda base: True
    verify.run_cmd = run_cmd
    verify.failing_spec_files = lambda report: [Path(state["last"]).name] if reports.get(state["last"]) == "failed" else []
    verify.tests_ran = lambda report: 0 if reports.get(state["last"]) == "none" else 3
    verify.free_port = lambda preferred=3000: 43210
    verify.fresh_data_env = lambda project: {"DATA_DIR": "data-one", "ARC_DB_FILE": "data-one/database.db", "DATABASE_FILE": "data-one/database.db"}
    verify.remove_dirs = lambda paths: calls.append(("rm", tuple(paths)))

    def restore():
        for name, value in saved.items():
            setattr(verify, name, value)
    return restore

@case
def the_ordered_run_keeps_the_order_on_one_server_and_one_database():
    from agent import verify

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)

        run = verify.run_e2e_in_order(project, [_s("A"), _s("B")], log=lambda *_: None)
        assert run.skipped and set(run.statuses.values()) == {"not_run"}
        (project / "backend" / "node_modules" / "@playwright" / "test").mkdir(parents=True)
        calls: list = []
        restore = _fake_verify(verify, calls, reports={"test-e2e/C.spec.js": "passed", "test-e2e/A.spec.js": "failed",
                                                       "test-e2e/B.spec.js": "none", "test-e2e/D.spec.js": "passed"})
        try:
            order = [_s("C"), _s("A"), _s("B"), _s("D")]
            run = verify.run_e2e_in_order(project, order, log=lambda *_: None)
        finally:
            restore()
        runs = [c[1] for c in calls if c[0] == "run"]

        assert runs == ["test-e2e/C.spec.js", "test-e2e/A.spec.js", "test-e2e/B.spec.js", "test-e2e/D.spec.js"], runs
        assert [c for c in calls if c[0] == "server"] == [("server", "data-one")] and ("rm", ("data-one",)) in calls
        assert run.statuses == {_s("C"): "passed", _s("A"): "failed", _s("B"): "unknown", _s("D"): "passed"}, run.statuses
        assert not run.skipped and _s("A") in run.outputs and _s("C") not in run.outputs
        assert "APPLICATION SERVER ERRORS" in run.server_errors

        calls = []
        restore = _fake_verify(verify, calls, reports={"test-e2e/C.spec.js": "passed", "test-e2e/A.spec.js": "passed"}, alive_after=2)
        try:
            run = verify.run_e2e_in_order(project, order, log=lambda *_: None)
        finally:
            restore()
        assert run.statuses[_s("B")] == "not_run" and run.statuses[_s("D")] == "not_run" and "server exited after A.spec.js" in run.note

        calls = []
        restore = _fake_verify(verify, calls, reports={})
        try:
            run = verify.run_e2e_in_order(project, order, log=lambda *_: None, time_limit=10)
        finally:
            restore()
        assert [c for c in calls if c[0] == "run"] == [] and set(run.statuses.values()) == {"not_run"} and "used up" in run.note

def _sweep_factory(tmp: Path, specs: list[str]):
    import main as factory_module
    from agent.verify import StepResult

    f = factory_module.Factory.__new__(factory_module.Factory)
    f.project_dir = tmp
    for spec in specs:
        (tmp / spec).parent.mkdir(parents=True, exist_ok=True)
        (tmp / spec).write_text('await see(page, "Shared record");' if "B" in spec else 'await see(page, "Other");', encoding="utf-8")
    f.e2e_specs = list(specs)
    f.spec_context = {spec: {"members": [Path(spec).stem.replace(".spec", "")]} for spec in specs}
    f.requirements_dir = tmp / "no-requirements-here"
    f.report = {"verification": {"frontend_build": True, "server_probe": True, "e2e": True}}
    f.failed_members = ["REQ-X"]
    f.install_failed = False
    f.large_tree = True
    f.time_budget = 3600.0

    def no_booking(*args, **kwargs):
        raise AssertionError("the shared sweep must not book a result")

    f.record_run = no_booking
    f._set_e2e_status = no_booking
    f.set_file_tests_status = no_booking
    return f, factory_module, StepResult

@case
def the_sweep_reports_red_only_after_earlier_specs_and_books_nothing():
    from agent import clock, verify

    with tempfile.TemporaryDirectory() as tmp:
        specs = [_s("A"), _s("B"), _s("C")]
        f, factory_module, StepResult = _sweep_factory(Path(tmp), specs)
        f.started = clock.now()
        lines: list[str] = []
        saved_log, saved_run = factory_module.log, verify.run_e2e_in_order
        seen: dict = {}

        def fake_run(project, order, log=print, time_limit=0, per_file_timeout=300):
            seen["order"], seen["limit"] = list(order), time_limit
            return verify.OrderedRun({_s("A"): "passed", _s("B"): "failed", _s("C"): "passed"}, {_s("B"): "Error: expect(locator) failed"}, "")

        factory_module.log, verify.run_e2e_in_order = lines.append, fake_run
        try:
            summary = f.shared_context_sweep(StepResult(True, "E2E PASSED"))
        finally:
            factory_module.log, verify.run_e2e_in_order = saved_log, saved_run
        assert seen["order"] == specs and seen["limit"] == 600 + 90 * 3
        assert "1 red only after earlier specs" in summary and "B.spec.js" in summary and summary.startswith("advisory")

        assert f.report["verification"] == {"frontend_build": True, "server_probe": True, "e2e": True}
        assert f.failed_members == ["REQ-X"]
        detail = f.report["shared_sweep"]["red_only_after_earlier"]
        assert detail == [{"spec": "B.spec.js", "earlier": ["A.spec.js"], "shared_texts": {}}], detail
        assert any("B.spec.js is green on fresh data but red only after earlier specs (A.spec.js)" in line for line in lines), lines

@case
def the_sweep_is_skipped_and_logged_when_the_closing_budget_is_short():
    from agent import clock, verify

    with tempfile.TemporaryDirectory() as tmp:
        specs = [_s("A"), _s("B"), _s("C")]
        f, factory_module, StepResult = _sweep_factory(Path(tmp), specs)
        f.started = clock.now() - factory_module.LARGE_TREE_CAP_SEC + 60
        lines: list[str] = []
        saved_log, saved_run = factory_module.log, verify.run_e2e_in_order

        def must_not_run(*args, **kwargs):
            raise AssertionError("the sweep ran on a short budget")

        factory_module.log, verify.run_e2e_in_order = lines.append, must_not_run
        try:
            assert f.shared_context_sweep(StepResult(True, "E2E PASSED")) == ""
            assert any("skipped: the closing budget is short" in line for line in lines), lines

            f.started = clock.now()
            lines.clear()
            assert f.shared_context_sweep(StepResult(False, "E2E FAILED against the real application" + NL + "No spec was run.")) == ""
            assert any("names no spec file" in line for line in lines), lines
            f.report["verification"]["frontend_build"] = False
            assert f.shared_context_sweep(StepResult(True, "E2E PASSED")) == ""
            f.report["verification"]["frontend_build"] = True

            from agent.settings import SETTINGS
            SETTINGS["shared_sweep"] = False
            try:
                assert f.shared_context_sweep(StepResult(True, "E2E PASSED")) == ""
            finally:
                SETTINGS.pop("shared_sweep", None)
        finally:
            factory_module.log, verify.run_e2e_in_order = saved_log, saved_run
        assert f.report["verification"]["e2e"] is True and f.failed_members == ["REQ-X"]

@case
def the_dependencies_come_from_the_requirement_file_and_a_bad_file_costs_only_the_order():
    import main as factory_module

    with tempfile.TemporaryDirectory() as tmp:
        f = factory_module.Factory.__new__(factory_module.Factory)
        f.requirements_dir = Path(tmp)
        f.report = {"verification": {}}
        yaml_text = NL.join([
            "id: ROOT", "name: App", "children:",
            "  - id: REQ-1", "    name: One", "    children:",
            "      - id: REQ-1-1", "        name: Leaf", "        dependencies: [REQ-2]",
            "      - id: REQ-1-2", "        name: Odd", "        dependencies: 7",
            "  - id: REQ-2", "    name: Two",
        ])
        (Path(tmp) / "requirements.yaml").write_text(yaml_text, encoding="utf-8")
        deps = f.leaf_dependencies()
        assert deps.get("REQ-1-1") == ["REQ-2"] and "REQ-1-2" not in deps, deps
        (Path(tmp) / "requirements.yaml").write_text("id: [broken", encoding="utf-8")
        lines: list[str] = []
        saved = factory_module.log
        factory_module.log = lines.append
        try:
            assert f.leaf_dependencies() == {} and "batch order kept" in lines[0]
        finally:
            factory_module.log = saved
        assert f.shared_sweep_line() == "not run"
        f.report["shared_sweep"] = {"skipped": "fewer than two spec files"}
        assert f.shared_sweep_line() == "skipped: fewer than two spec files"

@case
def the_sweep_runs_after_the_final_verdict_and_cannot_take_the_run_down():
    import inspect

    import main as factory_module
    source = inspect.getsource(factory_module.Factory.verify)
    call = source.index("self.shared_context_sweep(last_suite)")

    assert source.rindex('results["e2e"] =') < call and source.rindex("self.failed_members = [") < call
    assert call < source.index('self.trace.git_commit("verification')
    guard = source[:call].rstrip()
    assert guard.endswith("try:"), "the sweep call is not inside a try block"
    after = source[call:call + 300]
    assert "except Exception" in after and "nothing booked" in after
    sweep = inspect.getsource(factory_module.Factory.shared_context_sweep)
    assert "record_run" not in sweep and "failed_members" not in sweep and 'results["e2e"]' not in sweep
    assert "repair" not in sweep
    run = inspect.getsource(factory_module.Factory.run)
    assert "shared-database sweep: {self.shared_sweep_line()}" in run

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=3)
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
