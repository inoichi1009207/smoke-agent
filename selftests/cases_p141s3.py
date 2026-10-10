from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_engine142 as e142
import cases_rollback as rb

NL = chr(10)
CASES = []
Settings = e142.Settings

def case(function):
    CASES.append(function)
    return function

class FakeClock:

    def __init__(self, step: float = 100.0):
        self.now, self.step = 1000.0, step

    def __enter__(self):
        from agent import clock

        self.saved = clock.now
        clock.now = lambda: self.now
        return self

    def __exit__(self, *exc):
        from agent import clock

        clock.now = self.saved

def run_implementer(checks: list[str], files: dict[str, str] | None = None, max_rounds: int = 4, extra_rounds: int = 3,
                    step: float = 100.0, deadline_after: float | None = 100000.0, **options):
    import cases_engine as ce
    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome

    class Model:
        def __init__(self):
            self.calls, self.last_finish_reason, self.model = [], "stop", "glm-test"

        def chat(self, messages, **opts):
            self.calls.append(opts.get("tag", ""))
            if ":diagnose" in opts.get("tag", ""):
                return {"content": "frontend/src/pages/HomePage.tsx -- render it"}
            return {"content": ce.block("frontend/src/pages/HomePage.tsx", f"export default function HomePage() {{ return {len(self.calls)}; }}")}

    lines: list[str] = []
    made: list[str] = []
    pending = list(checks)
    with FakeClock(step) as fake, tempfile.TemporaryDirectory() as tmp:
        def check():
            fake.now += step
            made.append(pending[0])
            return TestOutcome(False, pending.pop(0))

        deadline = None if deadline_after is None else fake.now + deadline_after
        implementer = CodegenImplementer(Model(), Path(tmp), check, max_rounds=max_rounds, extra_rounds=extra_rounds,
                                         deadline=deadline, log=lines.append, **options)
        result = implementer.implement({"id": "REQ-X", "name": "n", "description": "d"}, dict(files or e142.SX),
                                       TestOutcome(False, "RED as expected"))
    return result, lines, len(made)

def moving(count: int) -> list[str]:
    return [e142.two_tests(f"Error: one-{i}", f"Error: two-{i}") for i in range(count)]

def dead_check(error: str = "Database initialization failed: Error: SQLITE_ERROR: 3 values for 4 columns") -> str:
    from agent import verify

    note = verify.server_output_note(error + NL + error.split(": ", 1)[1] + NL, verify.SERVER_DOWN_HEAD,
                                     "The server process exited (exit code 1) before GET / answered.")
    return "E2E FAILED against the real application" + NL + "backend did not become ready for e2e" + note

@case
def a_batch_that_still_makes_progress_gets_rounds_past_the_cap():
    from agent.codegen import PROGRESS_ROUNDS_MAX

    assert PROGRESS_ROUNDS_MAX == 3

    result, lines, made = run_implementer(moving(20))
    assert result.status == "budget" and made == 8 and not any("progress round" in l for l in lines), (made, lines)

    result, lines, made = run_implementer(moving(20), progress_rounds=3, round_room=lambda: "")
    assert result.status == "budget" and made == 11, (made, result.status)
    granted = [l for l in lines if "the round cap is reached and the tests still make progress" in l]
    assert len(granted) == 3 and "progress round 3 of 3 (round 10)" in granted[-1], granted

    assert sum("granting extra round" in l for l in lines) == 3

@case
def a_stuck_batch_gets_no_progress_round():

    checks = moving(7) + [moving(7)[-1]] * 5
    result, lines, made = run_implementer(checks, progress_rounds=3, round_room=lambda: "")
    assert made == 8 and result.status == "budget", (made, lines)
    assert any("no progress round: every failing test fails the way it did before" in l for l in lines), lines

    result, lines, made = run_implementer(e142.REQ_5_1_1, e142.S551, progress_rounds=3, round_room=lambda: "")
    assert made == 5 and not any("progress round" in l for l in lines), (made, lines)

@case
def a_progress_round_needs_an_average_round_before_the_batch_deadline():

    result, lines, made = run_implementer(moving(20), progress_rounds=3, round_room=lambda: "", deadline_after=850.0)
    assert made == 8, (made, lines)
    assert any("no progress round: 50 s left before the batch deadline, a round of this batch takes 100 s" in l for l in lines), lines

    result, lines, made = run_implementer(moving(20), progress_rounds=3, round_room=lambda: "", deadline_after=1000.0)
    assert made == 10, (made, lines)

    result, lines, made = run_implementer(moving(20), progress_rounds=3, round_room=lambda: "", deadline_after=None)
    assert made == 8 and any("no progress round: no batch deadline" in l for l in lines), (made, lines)

@case
def a_progress_round_needs_the_runs_budget():
    result, lines, made = run_implementer(moving(20), progress_rounds=3, round_room=lambda: "the run is short of time (x)")
    assert made == 8 and any("no progress round: the run is short of time (x)" in l for l in lines), (made, lines)

    def broken():
        raise RuntimeError("boom")

    result, lines, made = run_implementer(moving(20), progress_rounds=3, round_room=broken)
    assert made == 8 and any("no progress round: the run's budget could not be read" in l for l in lines), (made, lines)

@case
def the_factory_measures_the_runs_budget_for_a_progress_round():
    import main as factory_module

    f = rb.make_factory(Path(tempfile.gettempdir()), "x", time_budget=10000.0)
    saved = factory_module.LLM.quota_exhausted
    try:
        factory_module.LLM.quota_exhausted = False
        assert f.progress_round_room(3)() == ""
        assert "short of time" in f.progress_round_room(8)()
        factory_module.LLM.quota_exhausted = True
        assert "out of balance" in f.progress_round_room(3)()
        factory_module.LLM.quota_exhausted = False
        f.time_budget = 0.0
        assert "used up" in f.progress_round_room(1)()
    finally:
        factory_module.LLM.quota_exhausted = saved

@case
def only_the_unit_phase_gets_the_new_options():
    import inspect

    import cases_hubs as ch
    import main as factory_module

    seen, _lines = ch._process({}, 3)
    assert seen[0].get("progress_rounds") == 3 and seen[0].get("start_failure_free") == 2 and callable(seen[0].get("round_room")), seen[0]

    seen, _lines = ch._process({}, 3, settings={"progress_rounds": False, "start_failure_free": False})
    assert not ({"progress_rounds", "round_room", "start_failure_free"} & set(seen[0])), seen[0]
    source = inspect.getsource(factory_module.Factory.process_node)

    assert source.count("**self.unit_round_options(nodes_left)") == 3

    for line in source.splitlines():
        if "Implementer(" in line and ("max_rounds=3" in line or "max_rounds=2" in line):
            assert "unit_round_options" not in line, line
    assert "unit_round_options" not in inspect.getsource(factory_module.Factory.repair_regression_debt)

@case
def a_check_without_a_started_application_is_recognised():
    from agent import verify
    from agent.codegen import APP_NOT_STARTED, app_did_not_start

    assert APP_NOT_STARTED == verify.NO_SPEC_RAN[0] == "backend did not become ready"
    assert app_did_not_start(dead_check())
    assert app_did_not_start("APPLICATION LINT FAILED (fix these too, in the same answer):\nx\n\n" + dead_check())

    ran = e142.two_tests("Error: one", None) + NL + verify.SERVER_EXITED_HEAD + NL + "  | boom"
    assert not app_did_not_start(ran)

    assert not app_did_not_start(e142.two_tests("Error: one", None) + NL + "backend did not become ready for e2e")

    assert not app_did_not_start(dead_check() + NL + "Spec files that failed before this: REQ-1.spec.js")

    from agent.codegen import ADVISORY_MARK, FOUNDATION_MARK

    assert not app_did_not_start(e142.two_tests("Error: one", None) + FOUNDATION_MARK + " " + dead_check())
    assert not app_did_not_start(e142.two_tests("Error: one", None) + ADVISORY_MARK + "API " + dead_check())

    own_passed = ("E2E FAILED against the real application" + NL + "This batch's own spec PASSES. What fails are specs of EARLIER "
                  "requirements, which passed before this batch changed the files they depend on: x.spec.js." + NL
                  + "backend did not become ready for e2e" + NL + verify.SERVER_DOWN_HEAD)
    assert not app_did_not_start(own_passed)
    built_not = "FRONTEND BUILD FAILED" + NL + "Error: x"
    assert not app_did_not_start(built_not + FOUNDATION_MARK + " " + dead_check())
    assert not app_did_not_start(built_not + ADVISORY_MARK + "API " + dead_check())
    assert not app_did_not_start("FRONTEND BUILD FAILED\nError: x")

@case
def a_start_failure_hands_the_error_back_without_counting_the_round():
    checks = [dead_check()] + moving(10)

    result, lines, made = run_implementer(checks, max_rounds=2, extra_rounds=0)
    assert made == 3, made
    result, lines, made = run_implementer(checks, max_rounds=2, extra_rounds=0, start_failure_free=2)
    assert made == 4 and result.status == "budget", (made, lines)
    assert any("round 0: the application did not start (no test ran); the start error goes back and this round is not counted (1 such left)" in l
               for l in lines), lines

    assert any("REQ-X round 1: check failing" in l for l in lines), lines

@case
def a_free_start_round_does_not_eat_an_extra_round():

    result, lines, made = run_implementer([dead_check()] + moving(20), start_failure_free=2)
    assert made == 9 and sum("granting extra round" in l for l in lines) == 3, (made, lines)

@case
def the_free_start_rounds_are_capped():
    result, lines, made = run_implementer([dead_check()] * 10, max_rounds=2, extra_rounds=0, start_failure_free=2)
    assert made == 5 and result.status == "budget", (made, lines)
    assert sum("is not counted" in l for l in lines) == 2

@case
def a_start_failure_neither_unlocks_a_stall_nor_buys_an_extra_round():

    same = e142.two_tests("Error: b", "Error: x")
    checks = [same, same, same, dead_check(), same, same, same, same]
    result, lines, made = run_implementer(checks, start_failure_free=2)
    assert made == 6 and not any("failures changed after the stall" in l for l in lines), (made, lines)
    assert not any("granting extra round" in l for l in lines), lines

F3 = "backend/test-e2e/REQ-1-2.spec.js"
F4 = "backend/test-e2e/REQ-4-2-3.spec.js"
F4_BREAK = rb.brk("REQ-4-2-3: Scenario 1")

def debt_implementer(script: list[dict], calls: list[dict]):
    from agent.codegen import ImplementResult

    class Scripted:
        def __init__(self, llm, project_dir, check, **options):
            self.project_dir, self.check, self.options = project_dir, check, options

        def implement(self, node, tests, outcome, extra_context=""):
            calls.append({"options": self.options, "tests": dict(tests), "first": outcome.output, "context": extra_context})
            step = script.pop(0)
            for rel, content in step.get("write", {}).items():
                path = self.project_dir / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            if step.get("before"):
                step["before"]()
            written = list(step.get("write", {}))
            result = self.check()
            if step.get("then"):
                for rel, content in step["then"].items():
                    (self.project_dir / rel).write_text(content, encoding="utf-8")
                written += [rel for rel in step["then"] if rel not in written]
                result = self.check()
            calls[-1]["check"] = result.output
            status = step.get("status") or ("passed" if result.passed else "budget")
            return ImplementResult(status, written, "scripted", 1, last_check=result.output if status == "passed" else "")

    return Scripted

def run_debt_batch(script: list[dict], time_budget: float = 100000.0, settings: dict | None = None, tweak=None, patch: dict | None = None,
                   node_extra: dict | None = None, app_before: str = "APP"):
    import main as factory_module
    from agent.testrun import TestOutcome

    lines: list[str] = []
    calls: list[dict] = []
    names = ("run_e2e", "build_frontend", "Implementer", "run_node_tests", "static_test_defects", "parse_defects_in_output", "lint_application", "log") \
        + tuple(patch or {})
    saved = {name: getattr(factory_module, name) for name in names}
    with tempfile.TemporaryDirectory() as tmp, Settings(**{"audit_enabled": False, **(settings or {})}):
        root = Path(tmp)
        world = rb.World(root)
        f = rb.make_factory(root, app_before, time_budget)
        f.green_specs = [rb.F1, rb.F2, F3, F4]
        f.e2e_specs = list(f.green_specs)
        f.file_specs = {rb.APP: [rb.F1, F4]}
        (root / F4).parent.mkdir(parents=True, exist_ok=True)
        (root / F4).write_text("test('REQ-4-2-3: Scenario 1', async () => {});" + NL, encoding="utf-8")
        if tweak:
            tweak(f)
        try:
            factory_module.run_e2e, factory_module.build_frontend = world.run_e2e, world.build_frontend
            factory_module.Implementer = debt_implementer(script, calls)
            factory_module.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
            factory_module.static_test_defects = lambda project_dir, files: ""
            factory_module.parse_defects_in_output = lambda output, files: ""
            factory_module.lint_application = lambda project_dir, native: ""
            factory_module.log = lines.append
            for name, value in (patch or {}).items():
                setattr(factory_module, name, value)
            passed = f.process_node({"id": "REQ-9", "name": "Batch nine", "members": ["REQ-9"], **(node_extra or {})}, 1)
            after = (root / rb.APP).read_text(encoding="utf-8")
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
    assert not script, f"scripted steps left over: {script}"
    return f, lines, after, passed, calls

KEPT = {"write": {rb.APP: "OWN-OK " + F4_BREAK}}

@case
def a_kept_batch_repairs_its_regression_once_and_keeps_an_all_green_repair():
    f, lines, after, passed, calls = run_debt_batch([dict(KEPT), {"write": {rb.APP: "OWN-OK fixed"}}])
    assert passed and after == "OWN-OK fixed", (passed, after, lines)
    assert f.regression_debts == [], f.regression_debts
    assert F4 in f.green_specs and f.green_specs[:4] == [rb.F1, rb.F2, F3, F4], f.green_specs
    assert any("the targeted regression repair is GREEN" in l for l in lines), lines
    assert f.report["nodes"]["REQ-9"]["status"] == "passed" and f.report["nodes"]["REQ-9"]["e2e"] == "passed"

    repair = calls[1]
    assert repair["options"].get("max_rounds") == 1 and repair["options"].get("extra_rounds") == 0, repair["options"]
    assert repair["options"].get("deadline") is not None and "progress_rounds" not in repair["options"]

    assert F4 in repair["tests"] and "REQ-4-2-3.spec.js" in repair["first"] and "This batch's own spec PASSES" in repair["first"]
    assert "TARGETED REGRESSION REPAIR" in repair["context"]

@case
def a_repair_that_leaves_the_regression_red_is_put_back():
    f, lines, after, passed, calls = run_debt_batch([dict(KEPT), {"write": {rb.APP: "OWN-OK other " + F4_BREAK}}])
    assert passed and after == "OWN-OK " + F4_BREAK, (after, lines)
    assert f.regression_debts == ["REQ-9"] and F4 not in f.green_specs, (f.regression_debts, f.green_specs)
    assert any("the targeted regression repair is not kept (it ended budget)" in l for l in lines), lines

@case
def a_repair_that_breaks_the_own_spec_is_put_back():
    f, lines, after, passed, calls = run_debt_batch([dict(KEPT), {"write": {rb.APP: "fixed but own spec gone"}}])
    assert passed and after == "OWN-OK " + F4_BREAK and f.regression_debts == ["REQ-9"], (after, lines)
    assert F4 not in f.green_specs

@case
def a_green_check_that_did_not_run_the_broken_spec_proves_nothing():

    def drop_guard():
        import main as factory_module

        original = factory_module.Factory.regression_specs
        f_holder["f"].regression_specs = lambda before, own: [s for s in original(f_holder["f"], before, own) if s != F4]

    f_holder: dict = {}
    f, lines, after, passed, calls = run_debt_batch([dict(KEPT), {"write": {rb.APP: "OWN-OK " + F4_BREAK + " still"}, "before": drop_guard}],
                                                    tweak=lambda factory: f_holder.update(f=factory))
    assert after == "OWN-OK " + F4_BREAK and f.regression_debts == ["REQ-9"], (after, lines)
    assert any("not kept (its check did not run REQ-4-2-3.spec.js)" in l for l in lines), lines

@case
def a_green_check_whose_regression_run_was_skipped_proves_nothing():

    import main as factory_module
    from agent.verify import StepResult

    world_run: dict = {}

    def skipping_run(project_dir, specs, log=print):
        if rb.OWN not in specs:
            return StepResult(True, "e2e skipped: the port was taken by another process", skipped=True)
        return world_run["run"](project_dir, specs, log=log)

    def turn_on():
        world_run["run"] = factory_module.run_e2e
        factory_module.run_e2e = skipping_run

    f, lines, after, passed, calls = run_debt_batch([dict(KEPT), {"write": {rb.APP: "OWN-OK " + F4_BREAK + " again"}, "before": turn_on}])
    assert calls[1]["check"].startswith("E2E PASSED"), calls[1]["check"]
    assert passed and after == "OWN-OK " + F4_BREAK and f.regression_debts == ["REQ-9"], (after, lines)
    assert any("not kept (its check did not run REQ-4-2-3.spec.js)" in l for l in lines), lines

@case
def the_first_output_is_not_a_check_of_a_state_that_was_put_back():

    step = {"write": {rb.APP: "OWN-OK " + F4_BREAK}, "then": {rb.APP: "OWN-OK " + F4_BREAK + " " + rb.brk(rb.S1)}}
    f, lines, after, passed, calls = run_debt_batch([step, {"write": {rb.APP: "OWN-OK fixed"}}], settings={"best_batch_state": True})
    assert any("put back to its best state" in l for l in lines), lines
    assert rb.S1 in calls[0]["check"], calls[0]["check"]
    first = calls[1]["first"]
    assert "This batch's own spec PASSES" in first and "REQ-4-2-3.spec.js" in first, first
    assert rb.S1 not in first, first
    assert passed and after == "OWN-OK fixed" and f.regression_debts == [], (after, lines)

@case
def a_repair_that_is_put_back_does_not_use_up_the_seed_checks_once():

    seeds = {"seed_sentences": "SEED\n- The evaluation seed contains the workbook `Zebracorn`."}
    f, lines, after, passed, calls = run_debt_batch([{"write": {rb.APP: "OWN-OK Zebracorn " + F4_BREAK}}, {"write": {rb.APP: "OWN-OK fixed"}}],
                                                    node_extra=seeds, app_before="APP Zebracorn")
    assert after == "OWN-OK Zebracorn " + F4_BREAK and "SEED DATA CHECK FAILED" in calls[1]["check"], (after, calls[1].get("check", "")[:300])
    assert f.seed_checks.get("REQ-9:values", 0) == 0, f.seed_checks

@case
def the_partial_green_runs_that_really_ran_are_reported():
    import main as factory_module
    from agent.verify import StepResult

    spec = "backend/test-e2e/REQ-3-1-1.spec.js"
    f = rb.make_factory(Path(tempfile.gettempdir()), "x")
    saved = factory_module.log
    factory_module.log = lambda line: None
    try:
        f.run_partial_tests = lambda s, groups: StepResult(True, "1 passed")
        ran: list[str] = []
        f.with_partial_green(StepResult(True, "(no earlier green spec)"), [(spec, [(3, [])])], ran=ran)
        assert ran == ["REQ-3-1-1.spec.js"], ran
        f.run_partial_tests = lambda s, groups: StepResult(False, "FAILED SPEC FILES: REQ-3-1-1.spec.js" + NL + "  1 failed")
        ran = []
        f.with_partial_green(StepResult(True, "ok"), [(spec, [(3, [])])], ran=ran)
        assert ran == ["REQ-3-1-1.spec.js"], ran
        f.run_partial_tests = lambda s, groups: StepResult(True, "no browser", skipped=True)
        ran = []
        f.with_partial_green(StepResult(True, "ok"), [(spec, [(3, [])])], ran=ran)
        assert ran == [], ran
    finally:
        factory_module.log = saved

@case
def a_crashing_repair_is_put_back():
    def crash():
        raise RuntimeError("gateway")

    f, lines, after, passed, calls = run_debt_batch([dict(KEPT), {"write": {rb.APP: "half written"}, "before": crash}])
    assert passed and after == "OWN-OK " + F4_BREAK and f.regression_debts == ["REQ-9"], (after, lines)
    assert any("the targeted regression repair crashed" in l for l in lines), lines

@case
def no_targeted_repair_without_time_or_with_the_setting_off():

    f, lines, after, passed, calls = run_debt_batch([dict(KEPT)], time_budget=800.0)
    assert len(calls) == 1 and f.regression_debts == ["REQ-9"], (calls, lines)
    assert any("no targeted repair --" in l and "1800 s needed" in l for l in lines), lines
    f, lines, after, passed, calls = run_debt_batch([dict(KEPT)], time_budget=1700.0)
    assert len(calls) == 1 and any("1800 s needed" in l for l in lines), lines
    f, lines, after, passed, calls = run_debt_batch([dict(KEPT)], settings={"regression_repair": False})
    assert len(calls) == 1 and f.regression_debts == ["REQ-9"] and not any("targeted" in l for l in lines), lines

@case
def a_green_batch_without_debt_gets_no_targeted_repair():
    f, lines, after, passed, calls = run_debt_batch([{"write": {rb.APP: "OWN-OK"}}])
    assert passed and len(calls) == 1 and not any("targeted" in l for l in lines), lines

@case
def a_red_checks_route_note_goes_into_the_blocking_part_of_the_check():
    import main as factory_module
    from agent import testrun
    from agent.codegen import blocking_output

    assert factory_module.unrouted_page_note is testrun.unrouted_page_note
    seen: list[str] = []

    def note(project_dir, output):
        seen.append(output)
        return "WHERE THE RED CHECKS STOOD vs THE FRONTEND ROUTES (fake)"

    f, lines, after, passed, calls = run_debt_batch([{"write": {rb.APP: "own spec not built yet"}}], patch={"unrouted_page_note": note})
    check = calls[0]["check"]
    assert not passed and "(fake)" in check, check
    assert "(fake)" in blocking_output(check) and check.index("(fake)") > check.index("E2E FAILED"), check
    assert seen and all("OWN-OK" not in s for s in seen)

    from agent.codegen import FOUNDATION_MARK

    f, lines, after, passed, calls = run_debt_batch([{"write": {rb.APP: "red " + rb.brk(rb.S1)}}], patch={"unrouted_page_note": note})
    check = calls[0]["check"]
    assert FOUNDATION_MARK in check and check.index("(fake)") < check.index(FOUNDATION_MARK), check

    seen.clear()
    f, lines, after, passed, calls = run_debt_batch([{"write": {rb.APP: "OWN-OK"}}], patch={"unrouted_page_note": note})
    assert passed and "(fake)" not in calls[0]["check"] and not seen, (passed, seen)

def audit_patch(findings: list[dict], recheck):
    from agent import audit

    saved = audit.recheck_repaired
    audit.recheck_repaired = recheck
    return {"audit_node": lambda llm, project_dir, node, contract, log=print, use_llm=False: [dict(f) for f in findings]}, \
        (lambda: setattr(audit, "recheck_repaired", saved))

FINDING = {"where": "route /", "requirement": "The home page shows Explore.", "problem": "Explore is not shown", "fix": "show it", "layer": "scripted"}

@case
def a_kept_audited_repair_is_re_checked_once_and_only_logged():
    rechecks: list[tuple] = []
    patch, undo = audit_patch([FINDING], lambda project_dir, node, contract, repaired, log=print: rechecks.append((node.get("id"), list(repaired))))
    try:
        f, lines, after, passed, calls = run_debt_batch([{"write": {rb.APP: "OWN-OK"}}, {"write": {rb.APP: "OWN-OK audited"}}],
                                                        settings={"audit_enabled": True, "audit_llm": "never"}, patch=patch)
    finally:
        undo()
    assert passed and after == "OWN-OK audited", (after, lines)
    assert len(rechecks) == 1 and rechecks[0][0] == "REQ-9" and rechecks[0][1][0]["problem"] == FINDING["problem"], rechecks

    rechecks.clear()
    patch, undo = audit_patch([FINDING], lambda *a, **k: rechecks.append(a))
    try:
        f, lines, after, passed, calls = run_debt_batch([{"write": {rb.APP: "OWN-OK"}}, {"write": {rb.APP: "own spec broken by audit"}}],
                                                        settings={"audit_enabled": True, "audit_llm": "never"}, patch=patch)
    finally:
        undo()
    assert passed and after == "OWN-OK" and not rechecks, (after, rechecks)

@case
def a_crashing_re_check_never_changes_the_batch():
    def crash(*args, **kwargs):
        raise RuntimeError("no browser")

    patch, undo = audit_patch([FINDING], crash)
    try:
        f, lines, after, passed, calls = run_debt_batch([{"write": {rb.APP: "OWN-OK"}}, {"write": {rb.APP: "OWN-OK audited"}}],
                                                        settings={"audit_enabled": True, "audit_llm": "never"}, patch=patch)
    finally:
        undo()
    assert passed and after == "OWN-OK audited", (after, lines)
    assert any("re-check of the audited repair crashed" in l for l in lines), lines

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
    print(f"{len(CASES) - failed}/{len(CASES)} p141s3 cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
