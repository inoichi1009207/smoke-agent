from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_engine142 as e142
import cases_rollback as rb

NL = chr(10)
CASES = []
Settings = e142.Settings

F3 = "backend/test-e2e/REQ-1-2.spec.js"
F4 = "backend/test-e2e/REQ-4-2-3.spec.js"
F5 = "backend/test-e2e/REQ-5-5.spec.js"
OTHER = "frontend/src/Other.tsx"
SERVER = "backend/src/server.js"
OFF = {"timesave_foundation_filter": False, "timesave_down_no_foundation": False, "timesave_chase_stop": False,
       "timesave_api_mute": False, "timesave_unnamed_inconclusive": False}

ON_FOR_CASES = ("timesave_foundation_filter", "timesave_chase_stop", "timesave_unnamed_inconclusive")

def case(function):

    import functools

    @functools.wraps(function)
    def with_switches_on():
        from agent.settings import SETTINGS
        saved = {key: SETTINGS.get(key) for key in ON_FOR_CASES}
        SETTINGS.update({key: True for key in ON_FOR_CASES})
        try:
            return function()
        finally:
            SETTINGS.update(saved)

    CASES.append(with_switches_on)
    return with_switches_on

def loop_implementer(script: list[dict], calls: list[dict]):
    from agent.codegen import ImplementResult

    class Loop:
        def __init__(self, llm, project_dir, check, **options):
            self.project_dir, self.check, self.options = project_dir, check, options

        def implement(self, node, tests, outcome, extra_context=""):
            record: dict = {"checks": [], "first": outcome.output}
            calls.append(record)
            if not outcome.passed and getattr(outcome, "stop", ""):
                return ImplementResult("budget", [], outcome.stop, 0)
            written: list[str] = []
            while script:
                step = script.pop(0)
                if "end" in step:
                    return ImplementResult(step["end"], written, "scripted", len(record["checks"]))
                for rel, content in step.get("write", {}).items():
                    path = self.project_dir / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
                    written.append(rel)
                result = self.check()
                record["checks"].append(result)
                if result.passed:
                    return ImplementResult("passed", written, "scripted", len(record["checks"]), last_check=result.output)
                if getattr(result, "stop", ""):
                    return ImplementResult("budget", written, result.stop, len(record["checks"]))
            return ImplementResult("budget", written, "script ended", len(record["checks"]))

    return Loop

def run_batch(script: list[dict], settings: dict | None = None, app_before: str = "APP", tweak=None, wrap=None,
              unit_tests=None, patch: dict | None = None, leftover_ok: bool = False):
    import main as factory_module
    from agent.testrun import TestOutcome

    lines: list[str] = []
    calls: list[dict] = []
    names = ("run_e2e", "build_frontend", "Implementer", "run_node_tests", "static_test_defects", "parse_defects_in_output",
             "lint_application", "log", "probe_server") + tuple(patch or {})
    saved = {name: getattr(factory_module, name) for name in names}
    with tempfile.TemporaryDirectory() as tmp, Settings(**{"audit_enabled": False, "regression_repair": False, **(settings or {})}):
        root = Path(tmp)
        world = rb.World(root)
        f = rb.make_factory(root, app_before, 100000.0)
        f.green_specs = [rb.F1, rb.F2, F3, F4, F5]
        f.e2e_specs = list(f.green_specs)
        f.file_specs = {rb.APP: [rb.F1, F4]}
        for spec in (F4, F5):
            (root / spec).parent.mkdir(parents=True, exist_ok=True)
            (root / spec).write_text("test('x', async () => {});" + NL, encoding="utf-8")
        if tweak:
            tweak(f)
        try:
            factory_module.run_e2e = wrap(world.run_e2e) if wrap else world.run_e2e
            factory_module.build_frontend = world.build_frontend
            factory_module.Implementer = loop_implementer(script, calls)
            factory_module.run_node_tests = unit_tests or (lambda project_dir, files: TestOutcome(True, "", ran=list(files)))
            factory_module.static_test_defects = lambda project_dir, files: ""
            factory_module.parse_defects_in_output = lambda output, files: ""
            factory_module.lint_application = lambda project_dir, native: ""
            factory_module.log = lines.append
            factory_module.probe_server = lambda project_dir, log=print: (SimpleNamespace(started=None, ok=False, output=""), 0)
            for name, value in (patch or {}).items():
                setattr(factory_module, name, value)
            passed = f.process_node({"id": "REQ-9", "name": "Batch nine", "members": ["REQ-9"]}, 1)
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
    if not leftover_ok:
        assert not script, f"scripted steps left over: {script}"
    return f, world, lines, calls, passed, list(script)

def regression_runs(world) -> list[list[str]]:
    return [specs for specs, _ in world.runs if rb.OWN not in specs and len(specs) > 1]

def names(specs: list[str]) -> list[str]:
    return [Path(s).name for s in specs]

@case
def a_foundation_spec_whose_files_did_not_change_stays_out_of_the_regression_run():

    f, world, lines, calls, passed, _ = run_batch([{"write": {SERVER: "// changed"}}], app_before="OWN-OK")
    runs = regression_runs(world)
    assert passed and runs and rb.F1 not in runs[0] and {rb.F2, F3, F4, F5} <= set(runs[0]), runs
    assert any("foundation spec(s) ['REQ-1-1-1.spec.js'] not run: none of the files they cover changed" in l for l in lines), lines

    f, world, lines, calls, passed, _ = run_batch([{"write": {SERVER: "// changed"}}], app_before="OWN-OK", settings=OFF)
    assert passed and rb.F1 in regression_runs(world)[0], regression_runs(world)

@case
def a_foundation_spec_runs_when_its_file_changed_and_not_again_until_it_changes():
    brk4 = rb.brk("REQ-4-2-3: Scenario 1")
    script = [{"write": {rb.APP: "OWN-OK", OTHER: brk4}},
              {"write": {OTHER: "fixed"}}]
    f, world, lines, calls, passed, _ = run_batch(script)
    runs = regression_runs(world)
    assert passed and len(runs) == 2, runs
    assert rb.F1 in runs[0] and rb.F1 not in runs[1] and F4 in runs[1], runs

    script = [{"write": {rb.APP: "OWN-OK", OTHER: brk4}}, {"write": {rb.APP: "OWN-OK v2", OTHER: "fixed"}}]
    f, world, lines, calls, passed, _ = run_batch(script)
    assert passed and rb.F1 in regression_runs(world)[1], regression_runs(world)

@case
def a_foundation_spec_that_was_red_runs_until_it_is_green():

    script = [{"write": {rb.APP: "OWN-OK " + rb.brk(rb.S1)}}, {"write": {OTHER: "unrelated"}}, {"end": "budget"}]
    f, world, lines, calls, passed, left = run_batch(script, leftover_ok=True)
    runs = regression_runs(world)
    assert rb.F1 in runs[0] and rb.F1 in runs[1], runs

    assert left == [{"end": "budget"}] and any("['REQ-1-1-1.spec.js'] red in 2 regression runs" in l for l in lines), lines
    assert not passed and any("regressions in the FOUNDATION specs" in l for l in lines), lines

@case
def the_red_check_foundation_run_leaves_out_unchanged_foundation_specs():

    f, world, lines, calls, passed, _ = run_batch([{"write": {SERVER: "// changed"}}, {"end": "budget"}])
    first = regression_runs(world)[0]
    assert rb.F1 not in first and set(first) == {rb.F2, F3}, regression_runs(world)
    f, world, lines, calls, passed, _ = run_batch([{"write": {SERVER: "// changed"}}, {"end": "budget"}], settings=OFF)
    assert set(regression_runs(world)[0]) == {rb.F1, rb.F2, F3}, regression_runs(world)

    only_f1 = lambda factory: setattr(factory, "green_specs", [rb.F1])
    f, world, lines, calls, passed, _ = run_batch([{"write": {SERVER: "// changed"}}, {"end": "budget"}], tweak=only_f1)
    assert "REQ-9" not in f.foundation_checks, f.foundation_checks

@case
def unknown_coverage_or_no_book_keeps_every_foundation_spec():
    import main as factory_module

    f = rb.make_factory(Path(tempfile.gettempdir()), "x")
    saved = factory_module.log
    factory_module.log = lambda line: None
    try:
        f.file_specs = {}
        assert f.foundation_unchanged("REQ-9", [rb.F1], {rb.APP: b"x"}, {}) == []
        f.file_specs = {rb.APP: [rb.F1]}
        state: dict = {}
        before = {rb.APP: (f.project_dir / rb.APP).read_bytes()}
        assert f.foundation_unchanged("REQ-9", [rb.F1], before, state) == [rb.F1]
        f.note_foundation_run(state, [rb.F1], SimpleNamespace(ok=False, output="no spec could run"))
        assert f.foundation_unchanged("REQ-9", [rb.F1], before, state) == []
        f.note_foundation_run(state, [rb.F1, rb.F2], SimpleNamespace(ok=False, output="FAILED SPEC FILES: REQ-1-1-3.spec.js"))
        assert f.foundation_unchanged("REQ-9", [rb.F1], before, state) == [rb.F1]
        with Settings(timesave_foundation_filter=False):
            assert f.foundation_unchanged("REQ-9", [rb.F1], before, state) == []
        f.large_tree = False
        assert f.foundation_unchanged("REQ-9", [rb.F1], before, state) == []
    finally:
        factory_module.log = saved

def down_world(run):
    from agent.verify import StepResult

    def wrapped(project_dir, specs, log=print):
        text = "\n".join(p.read_text(encoding="utf-8") for p in sorted((Path(project_dir) / "frontend" / "src").rglob("*")) if p.is_file())
        if specs == [rb.OWN] and "DOWN" in text:
            return StepResult(False, "backend did not become ready for e2e\nThe server process exited (exit code 1) before GET / answered.")
        return run(project_dir, specs, log=log)

    return wrapped

@case
def a_check_whose_application_did_not_start_gets_no_foundation_run():
    script = [{"write": {rb.APP: "DOWN"}}, {"end": "budget"}]
    f, world, lines, calls, passed, _ = run_batch(script, wrap=down_world)
    with_f2 = [specs for specs, _ in world.runs if rb.F2 in specs]
    assert len(with_f2) == 1, with_f2
    assert any("the application did not start in this check; no foundation run" in l for l in lines), lines
    script = [{"write": {rb.APP: "DOWN"}}, {"end": "budget"}]
    f, world, lines, calls, passed, _ = run_batch(script, wrap=down_world, settings=OFF)
    assert len([specs for specs, _ in world.runs if rb.F2 in specs]) == 2, world.runs

BRK4 = rb.brk("REQ-4-2-3: Scenario 1")
BRK5 = rb.brk("REQ-5-5: Scenario 1")

@case
def the_same_earlier_spec_red_twice_in_a_row_ends_the_chase():
    script = [{"write": {rb.APP: "OWN-OK", OTHER: BRK4}}, {"write": {OTHER: BRK4 + " v2"}},
              {"write": {OTHER: BRK4 + " v3"}}, {"write": {OTHER: "fixed"}}]
    f, world, lines, calls, passed, left = run_batch(script, leftover_ok=True)
    assert len(calls[0]["checks"]) == 2 and len(left) == 2, (len(calls[0]["checks"]), left)
    assert any("['REQ-4-2-3.spec.js'] red in 2 regression runs in a row with the own spec green; no further round" in l for l in lines), lines

    assert passed and f.regression_debts == ["REQ-9"] and F4 not in f.green_specs, (passed, f.regression_debts)

    script = [{"write": {rb.APP: "OWN-OK", OTHER: BRK4}}, {"write": {OTHER: BRK4 + " v2"}},
              {"write": {OTHER: BRK4 + " v3"}}, {"write": {OTHER: "fixed"}}]
    f, world, lines, calls, passed, left = run_batch(script, settings={"timesave_chase_stop": False})
    assert passed and len(calls[0]["checks"]) == 4 and f.regression_debts == [], (len(calls[0]["checks"]), f.regression_debts)

@case
def different_specs_or_a_green_run_in_between_do_not_stop_it():

    script = [{"write": {rb.APP: "OWN-OK", OTHER: BRK4}}, {"write": {OTHER: BRK5}}, {"write": {OTHER: BRK4 + " again"}},
              {"write": {OTHER: BRK5 + " again"}}, {"write": {OTHER: "fixed"}}]
    f, world, lines, calls, passed, left = run_batch(script)
    assert passed and len(calls[0]["checks"]) == 5 and not any("no further round" in l for l in lines), lines

    script = [{"write": {rb.APP: "OWN-OK", OTHER: BRK4}}, {"write": {rb.APP: "own broken"}},
              {"write": {rb.APP: "OWN-OK", OTHER: BRK4 + " v2"}}, {"write": {OTHER: "fixed"}}]
    f, world, lines, calls, passed, left = run_batch(script, leftover_ok=True)
    assert len(calls[0]["checks"]) == 3 and len(left) == 1, (len(calls[0]["checks"]), left)

@case
def the_chase_stop_is_not_armed_after_the_unit_phase():
    import main as factory_module

    f = rb.make_factory(Path(tempfile.gettempdir()), "x")
    saved = factory_module.log
    factory_module.log = lambda line: None
    try:
        state = {"chase_armed": False}

        red = rb.spec_output("REQ-4-2-3.spec.js", ["REQ-4-2-3: Scenario 1"])
        assert f.chase_streak("REQ-9", state, ["REQ-4-2-3.spec.js"], {"REQ-4-2-3.spec.js"}, red) == ""
        assert f.chase_streak("REQ-9", state, ["REQ-4-2-3.spec.js"], {"REQ-4-2-3.spec.js"}, red) == ""
        state["chase_armed"] = True
        assert "REQ-4-2-3.spec.js" in f.chase_streak("REQ-9", state, [], set())
        assert f.chase_streak("REQ-9", state, ["REQ-4-2-3.spec.js"], set()) == ""
    finally:
        factory_module.log = saved
    source = Path(ROOT / "main.py").read_text(encoding="utf-8")
    assert source.index('check_state["chase_armed"] = False') < source.index("repaired = self.repair_regression_debt(")

@case
def the_real_implementer_ends_on_a_check_that_says_stop():
    import cases_engine as ce
    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome

    class Model:
        last_finish_reason, model = "stop", "glm-test"

        def chat(self, messages, **opts):
            if ":diagnose" in opts.get("tag", ""):
                return {"content": "frontend/src/pages/HomePage.tsx -- render it"}
            return {"content": ce.block("frontend/src/pages/HomePage.tsx", "export default function HomePage() { return 1; }")}

    made: list[int] = []

    def check():
        made.append(1)
        outcome = TestOutcome(False, e142.two_tests(f"Error: one-{len(made)}", f"Error: two-{len(made)}"))
        if len(made) == 2:
            outcome.stop = "the same earlier spec(s) ['REQ-4-2-3.spec.js'] failed 2 regression runs in a row"
        return outcome

    lines: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        implementer = CodegenImplementer(Model(), Path(tmp), check, max_rounds=4, extra_rounds=3, deadline=None, log=lines.append)
        result = implementer.implement({"id": "REQ-X", "name": "n", "description": "d"}, dict(e142.SX), TestOutcome(False, "RED"))
        assert result.status == "budget" and len(made) == 2 and "REQ-4-2-3" in result.detail, (result, made)
        assert any("no further round" in l for l in lines), lines

        first = TestOutcome(False, "RED")
        first.stop = "stop"
        made.clear()
        result = CodegenImplementer(Model(), Path(tmp), check, max_rounds=4, extra_rounds=3, log=lines.append).implement(
            {"id": "REQ-X", "name": "n", "description": "d"}, dict(e142.SX), first)
        assert result.status == "budget" and made == [], (result, made)

OLD_API = "backend/tests/old.test.js"
OWN_API = "backend/tests/REQ-9.test.js"

@case
def an_api_file_of_another_batch_red_twice_is_not_run_again():
    import main as factory_module
    from agent.testrun import TestOutcome

    f = rb.make_factory(Path(tempfile.gettempdir()), "x")
    saved = factory_module.log
    logged: list[str] = []
    factory_module.log = logged.append
    try:
        red = TestOutcome(False, "## backend/tests/old.test.js\n FAIL  tests/old.test.js > lists things\nAssertionError: x")
        files = [OLD_API, "backend/tests/b.test.js"]
        f.note_api_run(files, [], red)
        assert f.advisory_api_files("REQ-9", files, []) == files
        f.note_api_run(files, [], TestOutcome(False, "Error: vitest crashed"))
        assert f.api_red_streaks[OLD_API] == 1
        f.note_api_run(files, [], red)
        assert f.advisory_api_files("REQ-9", files, []) == ["backend/tests/b.test.js"]
        assert any("old.test.js" in l and "not run (nor shown) any more" in l for l in logged), logged

        assert f.advisory_api_files("REQ-9", files, [OLD_API]) == files
        f.note_api_run([OLD_API], [], TestOutcome(True, ""))
        assert f.advisory_api_files("REQ-9", files, []) == files

        f.note_api_run([OLD_API], [OLD_API], red)
        f.note_api_run([OLD_API], [OLD_API], red)
        assert f.api_red_streaks[OLD_API] == 0
        with Settings(timesave_api_mute=False):
            f.api_red_streaks[OLD_API] = 5
            assert f.advisory_api_files("REQ-9", files, []) == files
    finally:
        factory_module.log = saved

@case
def a_muted_api_file_leaves_the_check_and_its_prompt():
    from agent.testrun import TestOutcome

    seen: list[list[str]] = []

    def unit_tests(project_dir, files):
        seen.append(list(files))
        if any(p.startswith("backend/") for p in files):
            return TestOutcome(False, " FAIL  tests/old.test.js > lists things\nAssertionError: old", ran=list(files))
        return TestOutcome(True, "", ran=list(files))

    def with_old(factory):
        factory.all_test_files = [OLD_API]

    script = [{"write": {SERVER: "// a"}}, {"write": {SERVER: "// b"}}, {"write": {SERVER: "// c"}}, {"end": "budget"}]
    f, world, lines, calls, passed, _ = run_batch(script, tweak=with_old, unit_tests=unit_tests)
    api_runs = [files for files in seen if OLD_API in files]
    assert len(api_runs) == 2, seen
    outputs = [c.output for c in calls[0]["checks"]]
    assert "ADVISORY backend API test failures" in outputs[1] and "ADVISORY backend API test failures" not in outputs[2], outputs
    seen.clear()
    script = [{"write": {SERVER: "// a"}}, {"write": {SERVER: "// b"}}, {"write": {SERVER: "// c"}}, {"end": "budget"}]
    f, world, lines, calls, passed, _ = run_batch(script, tweak=with_old, unit_tests=unit_tests, settings=OFF)
    assert len([files for files in seen if OLD_API in files]) == 3, seen

def unnamed_world(kind: str, times: int):
    from agent.verify import StepResult

    left = {"n": times}
    reruns: list[list[str]] = []

    def wrap(run):
        def wrapped(project_dir, specs, log=print):
            if rb.OWN not in specs and len(specs) > 1 and left["n"] > 0:
                left["n"] -= 1
                if kind == "unrun":
                    return StepResult(False, "NOT RUN SPEC FILES: REQ-4-2-3.spec.js\n4 of 5 spec files passed, each run against fresh "
                                             "data\n1 spec file(s) not run: the time for this check is used up\n(all spec files passed)")
                return StepResult(False, "E2E FAILED against the real application\nNo spec was run.\n")
            if rb.OWN not in specs and left["n"] == 0:
                reruns.append(list(specs))
            return run(project_dir, specs, log=log)
        return wrapped

    return wrap, reruns

@case
def an_unnamed_regression_run_is_run_once_more_and_read_as_that_run_says():
    wrap, reruns = unnamed_world("unrun", 1)
    f, world, lines, calls, passed, _ = run_batch([{"write": {rb.APP: "OWN-OK"}}], wrap=wrap)
    assert passed and reruns and reruns[0] == [F4], reruns
    assert any("failed without naming a spec" in l and "running 1 of its spec(s) once more" in l for l in lines), lines

    wrap, reruns = unnamed_world("unrun", 1)
    script = [{"write": {rb.APP: "OWN-OK", OTHER: BRK4}}, {"write": {OTHER: "fixed"}}]
    f, world, lines, calls, passed, _ = run_batch(script, wrap=wrap)
    first = calls[0]["checks"][0]
    assert not first.passed and "REQ-4-2-3.spec.js" in first.output and "This batch's own spec PASSES" in first.output, first.output[:300]
    assert passed

@case
def unnamed_twice_is_inconclusive_and_never_a_rollback():
    wrap, reruns = unnamed_world("down", 2)
    f, world, lines, calls, passed, _ = run_batch([{"write": {rb.APP: "OWN-OK"}}], wrap=wrap)
    assert passed and not any("put back" in l for l in lines), lines
    assert any("named no failing spec twice; inconclusive" in l for l in lines), lines

    wrap, reruns = unnamed_world("down", 1)
    f, world, lines, calls, passed, _ = run_batch([{"write": {rb.APP: "OWN-OK"}}, {"end": "budget"}], wrap=wrap, settings=OFF)
    assert not passed and any("regressions in the FOUNDATION specs" in l for l in lines), lines

@case
def unquoted_generated_names_do_not_change_the_signature():
    import main as factory_module

    sig = factory_module.error_signature
    a = "Error: expected branch pw-branch-1790680142446-t5ar to be listed"
    b = "Error: expected branch pw-branch-1790682839944-qxq5 to be listed"
    assert sig(a) == sig(b), (sig(a), sig(b))
    assert sig("Error: outsider-pr-1790680142446 still visible") == sig("Error: outsider-pr-1790682839944 still visible")

    assert sig(a) != sig("Error: expected branch pw-branch-1790680142446-t5ar to be hidden")
    assert sig("Error: Username format is invalid") != sig("Error: Signed in as alice")

    assert sig("Error: cell A1 shows 12") != sig("Error: cell B2 shows 12")

def main() -> int:
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    started = time.time()
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed}/{len(CASES)} timesave cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
