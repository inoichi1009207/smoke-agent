from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_rollback as rb
import cases_v145a as v145a

NL = chr(10)
CASES = []
MISSING = object()
FINDINGS = [{"where": "route /", "problem": "the grid shows no result", "layer": "scripted"}]

def case(function):
    CASES.append(function)
    return function

def fake_class(script: list[dict], seen: list, offset: list[float], supports: bool = True):
    from agent import clock

    base = v145a.fake_implementer(script, seen, offset)

    class Fake(base):
        def implement(self, node, tests, outcome, extra_context=""):
            self.saw_call_deadline = getattr(self, "call_deadline", MISSING)
            self.saw_now = clock.now()
            self.saw_context = extra_context
            return super().implement(node, tests, outcome, extra_context=extra_context)

    if supports:
        Fake.call_deadline = None
    return Fake

def run(script: list[dict], nodes_left: int = 1, time_budget: float = 10800.0, audit: bool = False, supports: bool = True,
        settings: dict | None = None):
    import main as factory_module
    from agent import clock
    from agent.testrun import TestOutcome

    lines: list[str] = []
    seen: list = []
    offset = [0.0]
    names = ("run_e2e", "build_frontend", "Implementer", "run_node_tests", "static_test_defects", "parse_defects_in_output",
             "lint_application", "log", "audit_node", "findings_brief")
    saved = {name: getattr(factory_module, name) for name in names}
    saved_now = clock.now
    with v145a.Settings(audit_enabled=audit, **(settings or {})), tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = rb.World(root)
        f = rb.make_factory(root, "base", time_budget)
        try:
            frozen = time.time()
            clock.now = lambda: frozen + offset[0]
            f.started = frozen
            factory_module.run_e2e, factory_module.build_frontend = world.run_e2e, world.build_frontend
            factory_module.Implementer = fake_class(script, seen, offset, supports)
            factory_module.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
            factory_module.static_test_defects = lambda project_dir, files, **kw: ""
            factory_module.parse_defects_in_output = lambda output, files: ""
            factory_module.lint_application = lambda project_dir, native: ""
            factory_module.log = lines.append
            factory_module.audit_node = lambda llm, project_dir, node, contract, log=print, use_llm=True: [dict(a) for a in FINDINGS]
            factory_module.findings_brief = lambda findings: "AUDIT BRIEF"
            f.process_node(dict(v145a.NODE), nodes_left)
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
            clock.now = saved_now
    assert not script, f"scripted steps left over: {script}"
    return f, lines, seen

RED_UNIT = {"do": [("write", {rb.APP: "base feature"}), ("check",), ("advance", 1300)]}
AUDIT_RED = {"do": [("write", {rb.APP: "base audit try"}), ("check",)]}

def fixer_of(seen: list):
    fixers = [s for s in seen if s.saw_context == "AUDIT BRIEF"]
    assert len(fixers) == 1, [s.saw_context for s in seen]
    return fixers[0]

@case
def short_of_time_the_late_audit_repair_gets_480_s_every_call_included():

    f, lines, seen = run([dict(RED_UNIT), dict(AUDIT_RED)], nodes_left=10, audit=True)
    fixer = fixer_of(seen)
    assert fixer.saw_call_deadline is not MISSING and fixer.saw_call_deadline is not None, fixer.saw_call_deadline
    assert abs(fixer.saw_call_deadline - (fixer.saw_now + 480)) < 1.0, (fixer.saw_call_deadline - fixer.saw_now)
    assert fixer.options["deadline"] == fixer.saw_call_deadline, (fixer.options["deadline"], fixer.saw_call_deadline)
    bound = [l for l in lines if "the run is short of time" in l and "audited repair gets" in l]
    assert len(bound) == 1 and "844 s for each of the 9 batch(es) still to start, under 1200 s" in bound[0], bound
    assert "gets 480 s" in bound[0] and "ended budget" in bound[0], bound

    assert seen[0].saw_call_deadline is None, seen[0].saw_call_deadline

@case
def the_window_is_at_most_one_later_batchs_fair_share():

    f, lines, seen = run([dict(RED_UNIT), dict(AUDIT_RED)], nodes_left=10, time_budget=4000.0, audit=True)
    fixer = fixer_of(seen)
    assert abs(fixer.saw_call_deadline - (fixer.saw_now + 240)) < 1.0, (fixer.saw_call_deadline - fixer.saw_now)
    assert any("gets 240 s" in l for l in lines), lines

@case
def with_time_to_spare_the_late_audit_repair_stays_as_v145():

    late = {"do": [("write", {rb.APP: "base feature"}), ("check",), ("advance", 4400)]}
    f, lines, seen = run([late, dict(AUDIT_RED)], nodes_left=2, audit=True)
    fixer = fixer_of(seen)
    assert fixer.saw_call_deadline is None, fixer.saw_call_deadline
    assert abs(fixer.options["deadline"] - (fixer.saw_now + 480)) < 1.0
    assert not any("the run is short of time" in l for l in lines), lines

    for nodes_left, settings in ((1, {}), (10, {"audit_repair_bound": False})):
        f, lines, seen = run([dict(RED_UNIT), dict(AUDIT_RED)], nodes_left=nodes_left, audit=True, settings=settings)
        assert fixer_of(seen).saw_call_deadline is None and not any("short of time" in l and "[audit]" in l for l in lines), (nodes_left, lines)

@case
def short_of_time_a_repair_inside_the_batchs_own_share_ends_every_call_by_the_batchs_deadline():

    unit = {"do": [("write", {rb.APP: "base feature"}), ("check",), ("advance", 100)]}
    f, lines, seen = run([unit, dict(AUDIT_RED)], nodes_left=10, audit=True)
    fixer = fixer_of(seen)
    end = seen[0].saw_now + 1200
    assert abs(fixer.options["deadline"] - end) < 1.0, fixer.options["deadline"] - seen[0].saw_now
    assert fixer.saw_call_deadline not in (None, MISSING) and abs(fixer.saw_call_deadline - end) < 1.0, fixer.saw_call_deadline
    bound = [l for l in lines if "the run is short of time" in l and "audited repair gets" in l]
    assert len(bound) == 1 and "ended budget" in bound[0] and "time used up" not in bound[0], bound

@case
def the_bound_follows_the_pace_the_batchs_status_is_only_logged():

    import main as m
    from agent import clock

    f = m.Factory.__new__(m.Factory)
    lines: list[str] = []
    saved_now, saved_log = clock.now, m.log
    try:
        clock.now = lambda: 1000.0
        m.log = lines.append
        with v145a.Settings(audit_repair_bound=True):
            for status in ("passed", "budget", "gave_up"):
                f.time_left = lambda: 800.0 * 9 / 0.8
                assert abs(f.audit_repair_bound("REQ-9", status, 1300.0, 1480.0, 10) - 1480.0) < 1e-6, status
                assert abs(f.audit_repair_bound("REQ-9", status, 1500.0, 1500.0, 10) - 1500.0) < 1e-6, status
                f.time_left = lambda: 1300.0 * 9 / 0.8
                assert f.audit_repair_bound("REQ-9", status, 1500.0, 1500.0, 10) is None, status
    finally:
        clock.now, m.log = saved_now, saved_log
    assert lines and not any("time used up" in l for l in lines), lines
    assert any("the batch ended passed" in l for l in lines), lines

STUCK = {"do": [("write", {rb.APP: "base a"}), ("check",)], "stop_reason": "stuck"}

def stuck_after(spent: float) -> dict:
    return {"do": [("write", {rb.APP: "base a"}), ("check",), ("advance", spent)], "stop_reason": "stuck"}

@case
def the_restart_needs_max_600_or_40_percent_left_without_the_call_limit_term():

    for spent, restarts in ((590, True), (610, False)):
        script = [stuck_after(spent)] + ([{"do": [("check",)], "status": "budget"}] if restarts else [])
        f, lines, seen = run(script, time_budget=1000.0)
        assert (len(seen) == 2) is restarts, (spent, lines)
        if not restarts:
            assert any("no in-batch restart" in l and "600 s needed" in l for l in lines), lines

    for spent, restarts in ((5100, True), (5300, False)):
        script = [stuck_after(spent)] + ([{"do": [("check",)], "status": "budget"}] if restarts else [])
        f, lines, seen = run(script)
        assert (len(seen) == 2) is restarts, (spent, lines)
        if not restarts:
            assert any("no in-batch restart" in l and ("3456 s needed" in l or "3455 s needed" in l) for l in lines), lines

@case
def the_restarts_calls_end_by_the_batchs_deadline():
    f, lines, seen = run([dict(STUCK), {"do": [("check",)], "status": "budget"}], time_budget=1000.0)
    first, restart = seen
    assert restart.options["deadline"] == first.options["deadline"]
    assert restart.saw_call_deadline == restart.options["deadline"], (restart.saw_call_deadline, restart.options["deadline"])
    assert first.saw_call_deadline is None, first.saw_call_deadline

@case
def an_implementer_without_call_deadline_keeps_the_r51a_rule():

    f, lines, seen = run([dict(STUCK)], time_budget=1000.0, supports=False)
    assert len(seen) == 1 and any("2400 s needed" in l for l in lines), lines

@case
def the_platform_v144_shares_now_reach_the_restart():

    f, lines, seen = run([stuck_after(800), {"do": [("check",)], "status": "budget"}], time_budget=2500.0)
    assert len(seen) == 2 and any("(in-batch restart)" in l for l in lines), lines
    share = seen[0].options["deadline"] - seen[0].saw_now
    assert abs(share - 2000) < 1.0, share

@case
def the_real_implementer_is_recognised_as_honouring_call_deadline():

    import main as m
    from agent.codegen import CodegenImplementer

    assert m.Implementer is CodegenImplementer
    f = m.Factory.__new__(m.Factory)
    assert f.calls_bounded() is True
    saved = m.Implementer
    try:
        m.Implementer = type("Old", (), {})
        assert f.calls_bounded() is False
    finally:
        m.Implementer = saved

R21, R22, R23 = (f"REQ-2-{i}: Scenario 1" for i in (1, 2, 3))
F21, F22, F23 = (f"backend/test-e2e/REQ-2-{i}.spec.js" for i in (1, 2, 3))
GAVE_UP_REPAIR = {"do": [], "status": "gave_up"}

def owing(tag: str, *titles: str) -> str:
    return f"base OWN-OK {tag} " + "".join(rb.brk(t) for t in titles)

def debt_attempt(text: str, **end) -> dict:
    return {"do": [("write", {rb.APP: text}), ("check",)], **end}

def choice_run(first: dict, restart: dict, bounded: bool = False):
    original = v145a.fake_implementer

    def with_call_deadline(*args, **kwargs):
        cls = original(*args, **kwargs)
        cls.call_deadline = None
        return cls

    if bounded:
        v145a.fake_implementer = with_call_deadline
    try:
        f, lines, app, results, seen = v145a.run("base", [first, restart, dict(GAVE_UP_REPAIR)], prep=v145a._with_f345)
    finally:
        v145a.fake_implementer = original
    assert len(seen) == 3, lines
    assert any("(in-batch restart)" in l for l in lines), lines
    return f, lines, app, results

def owes_only(f, specs: set[str], lines):
    assert f.regression_debts == ["REQ-9"], (f.regression_debts, lines)
    assert set(getattr(f, "debt_specs", {}) or {}) == specs, (sorted(getattr(f, "debt_specs", {}) or {}), lines)
    for spec in (F21, F22, F23):
        assert (spec in f.green_specs) is (spec not in specs), (spec, f.green_specs)

@case
def a_restart_that_owes_fewer_earlier_specs_beats_a_first_attempt_already_in_debt():

    three, one = owing("first", R21, R22, R23), owing("restart", R21)
    f, lines, app, results = choice_run(debt_attempt(three, stop_reason="seed-conflict"),
                                        debt_attempt(one, status="budget", stop_reason="deadline"))
    assert app == one and results == [True], (app, lines)
    owes_only(f, {F21}, lines)
    assert any("in-batch restart choice" in l and "-> restart end" in l for l in lines), lines

@case
def a_restart_that_owes_more_or_as_many_leaves_the_first_attempts_end():

    for restart_titles, tag in (((R21, R22, R23), "more"), ((R22,), "tie")):
        one = owing("first", R21)
        f, lines, app, results = choice_run(debt_attempt(one, stop_reason="seed-conflict"),
                                            debt_attempt(owing("restart", *restart_titles), status="budget", stop_reason="deadline"))
        assert app == one and results == [True], (tag, app, lines)
        owes_only(f, {F21}, lines)
        assert any("in-batch restart choice" in l and "-> first end" in l for l in lines), (tag, lines)

@case
def the_first_attempts_best_check_beats_both_ends_when_it_owes_least():

    one, three, two = owing("best", R21), owing("first-end", R21, R22, R23), owing("restart", R21, R22)
    first = {"do": [("write", {rb.APP: one}), ("check",), ("write", {rb.APP: three}), ("check",)], "stop_reason": "seed-conflict"}
    f, lines, app, results = choice_run(first, debt_attempt(two, status="budget", stop_reason="deadline"))
    assert app == one and results == [True], (app, lines)
    owes_only(f, {F21}, lines)

@case
def the_restarts_best_check_is_a_candidate_too():

    two, one, three = owing("first", R21, R22), owing("restart-best", R23), owing("restart-end", R21, R22, R23)
    restart = {"do": [("write", {rb.APP: one}), ("check",), ("write", {rb.APP: three}), ("check",)], "status": "budget",
               "stop_reason": "deadline"}
    f, lines, app, results = choice_run(debt_attempt(two, stop_reason="seed-conflict"), restart)
    assert app == one and results == [True], (app, lines)
    owes_only(f, {F23}, lines)
    assert any("-> restart best" in l for l in lines), lines

@case
def a_restart_end_whose_last_files_were_never_checked_is_no_debt_state():

    two, one, unchecked = owing("first", R21, R22), owing("restart-checked", R21), owing("restart-unchecked", R21) + " more"
    restart = {"do": [("write", {rb.APP: one}), ("check",), ("write", {rb.APP: unchecked})], "status": "budget",
               "stop_reason": "deadline"}
    f, lines, app, results = choice_run(debt_attempt(two, stop_reason="seed-conflict"), restart, bounded=True)
    assert app == one and results == [True], (app, lines)
    owes_only(f, {F21}, lines)
    assert any("last files were never checked" in l for l in lines), lines

EARLIER = {rb.F1, rb.F2, "backend/test-e2e/REQ-1-2.spec.js", v145a.F3}
F3_BREAK = rb.brk("REQ-2-1: Scenario 1")
DEFECT = "The spec expects 'Save' but the requirement says 'Store' (quoted)"

def run_world(script: list[dict], nodes_left: int = 1, time_budget: float = 10800.0, audit: bool = False,
              settings: dict | None = None, prep=None, green_run_advance: float = 0.0, static=None):
    import main as factory_module
    from agent import clock
    from agent.testrun import TestOutcome

    lines: list[str] = []
    seen: list = []
    offset = [0.0]
    runs: list = []
    names = ("run_e2e", "build_frontend", "Implementer", "run_node_tests", "static_test_defects", "parse_defects_in_output",
             "lint_application", "log", "audit_node", "findings_brief")
    saved = {name: getattr(factory_module, name) for name in names}
    saved_now = clock.now
    with v145a.Settings(audit_enabled=audit, **(settings or {})), tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = rb.World(root)
        f = rb.make_factory(root, "base", time_budget)

        def run_e2e(project_dir, specs, log=print):
            text = world.sources()
            runs.append((offset[0], list(specs), text))
            if green_run_advance and list(specs) == [rb.OWN] and "OWN-OK" in text:
                offset[0] += green_run_advance
            return world.run_e2e(project_dir, specs, log=log)

        try:
            frozen = time.time()
            clock.now = lambda: frozen + offset[0]
            f.started = frozen
            if prep:
                prep(f, offset, runs)
            factory_module.run_e2e, factory_module.build_frontend = run_e2e, world.build_frontend
            factory_module.Implementer = fake_class(script, seen, offset)
            factory_module.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
            factory_module.static_test_defects = static or (lambda project_dir, files, **kw: "")
            factory_module.parse_defects_in_output = lambda output, files: ""
            factory_module.lint_application = lambda project_dir, native: ""
            factory_module.log = lines.append
            factory_module.audit_node = lambda llm, project_dir, node, contract, log=print, use_llm=True: [dict(a) for a in FINDINGS]
            factory_module.findings_brief = lambda findings: "AUDIT BRIEF"
            passed = f.process_node(dict(v145a.NODE), nodes_left)
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
            clock.now = saved_now
    assert not script, f"scripted steps left over: {script}"
    return f, lines, seen, runs, passed

def recording_author(calls: list, after_first=None, marks: list | None = None):
    def prep(f, offset, runs):
        def repair(node, prefix, files, problem, contract=""):
            calls.append(problem)
            if marks is not None:
                marks.append(len(runs))
            if len(calls) == 1 and after_first:
                after_first(offset)
            return files
        f.repair_tests = repair
    return prep

def fixer_defect(advance: float) -> dict:
    return {"do": [("write", {rb.APP: "base audit try"}), ("check",), ("advance", advance)], "status": "test_defect",
            "detail": DEFECT}

def offset_of(f, when: float) -> float:
    return when - f.started

@case
def short_of_time_a_test_defect_at_the_repairs_end_is_not_handed_to_the_author():

    calls: list = []
    f, lines, seen, runs, passed = run_world([dict(RED_UNIT), fixer_defect(500)], nodes_left=10, audit=True,
                                             prep=recording_author(calls))
    fixer = fixer_of(seen)
    assert abs(fixer.saw_call_deadline - (fixer.saw_now + 480)) < 1.0, fixer.saw_call_deadline - fixer.saw_now
    assert calls == [], f"repair_tests was called past the audited repair's end: {calls}"
    assert any("hand-over is not done" in l for l in lines), [l for l in lines if "[audit]" in l]
    assert not any("the test author gets it" in l for l in lines), lines
    assert rb.OWN not in f.revised_specs, f.revised_specs

    assert not passed and f.report["nodes"]["REQ-9"]["status"] != "passed", f.report["nodes"]["REQ-9"]
    assert f.regression_debts == [], f.regression_debts

@case
def short_of_time_the_hand_over_starts_before_the_end_but_nothing_after_it_starts_past_it():

    calls: list = []
    authored: list = []

    def author_takes_200(offset):
        offset[0] += 200
        authored.append(True)

    def static(project_dir, files, **kw):
        return "SyntaxError: Unexpected token (REQ-9.spec.js:3:1)" if authored else ""

    marks: list = []
    f, lines, seen, runs, passed = run_world([dict(RED_UNIT), fixer_defect(400)], nodes_left=10, audit=True, static=static,
                                             prep=recording_author(calls, after_first=author_takes_200, marks=marks))
    fixer = fixer_of(seen)
    assert abs(fixer.saw_call_deadline - (fixer.saw_now + 480)) < 1.0, fixer.saw_call_deadline - fixer.saw_now
    assert calls == [DEFECT], f"expected ONE test repair (the hand-over before the end), got: {calls}"
    own_after = [(t, specs) for t, specs, _ in runs[marks[0]:] if rb.OWN in specs]
    assert own_after == [], f"a check ran after the author's repair, past the audited repair's end: {own_after}"
    assert not any("after the test author's repair the batch's check is" in l for l in lines), lines
    assert not passed and f.regression_debts == [], (passed, f.regression_debts)

@case
def with_time_to_spare_the_audited_repairs_test_defect_reaches_the_author_as_v145():

    calls: list = []
    late = {"do": [("write", {rb.APP: "base feature"}), ("check",), ("advance", 4400)]}
    f, lines, seen, runs, passed = run_world([late, fixer_defect(500)], nodes_left=2, audit=True, prep=recording_author(calls))
    assert fixer_of(seen).saw_call_deadline is None
    assert calls == [DEFECT], calls
    assert any("the test author gets it (test defect #1)" in l for l in lines), lines
    assert any("after the test author's repair the batch's check is still red" in l for l in lines), lines
    assert not any("hand-over is not done" in l for l in lines), lines

def restart_script(advance: float, sources: str) -> list[dict]:
    return [{"do": [("write", {rb.APP: "base a"}), ("check",)], "stop_reason": "stuck"},
            {"do": [("write", {rb.APP: sources}), ("advance", advance), ("call", lambda impl: CHECKED.append(impl.check()))],
             "status": "budget"}]

CHECKED: list = []

def regression_runs(runs, marker: str = "OWN-OK") -> list:
    return [(t, specs) for t, specs, text in runs if marker in text and set(specs) & EARLIER]

@case
def a_restart_whose_own_run_ends_past_the_batch_deadline_starts_no_regression_run():

    CHECKED.clear()
    f, lines, seen, runs, passed = run_world(restart_script(1100, "base OWN-OK " + F3_BREAK), nodes_left=10,
                                             prep=lambda f, *rest: v145a._with_f3(f), green_run_advance=200)
    assert len(seen) == 2 and any("(in-batch restart)" in l for l in lines), lines
    deadline = offset_of(f, seen[1].options["deadline"])
    own = [t for t, specs, text in runs if specs == [rb.OWN] and "OWN-OK" in text]
    assert own and own[0] < deadline <= own[0] + 200, (own, deadline)
    late = regression_runs(runs)
    assert late == [], f"a regression run started past the in-batch restart's end ({deadline:.0f} s): {late}"
    assert any("no regression run starts past" in l for l in lines), [l for l in lines if "[tdd]" in l]
    assert CHECKED and CHECKED[0].output.startswith("CHECK UNFINISHED") and not CHECKED[0].passed, CHECKED[0].output[:200]
    assert getattr(CHECKED[0], "stop", ""), "the unfinished check carries no stop for the implementer"

    assert not any("best state so far" in l for l in lines), lines
    assert f.regression_debts == [], f.regression_debts
    assert any("in-batch restart ended budget; not kept" in l for l in lines), lines

@case
def without_a_phase_end_the_same_regression_run_still_runs_as_v145():

    script = [{"do": [("write", {rb.APP: "base OWN-OK " + F3_BREAK}), ("advance", 1100), ("check",)]}]
    f, lines, seen, runs, passed = run_world(script, nodes_left=10, prep=lambda f, *rest: v145a._with_f3(f),
                                             green_run_advance=200)
    deadline = offset_of(f, seen[0].options["deadline"])
    ran = regression_runs(runs)
    assert ran and ran[0][0] >= deadline, (ran, deadline)
    assert v145a.F3 in ran[0][1], ran
    assert f.regression_debts == ["REQ-9"], (f.regression_debts, lines)
    assert not any("no regression run starts past" in l for l in lines), lines

    CHECKED.clear()
    f, lines, seen, runs, passed = run_world(restart_script(800, "base OWN-OK"), nodes_left=10,
                                             prep=lambda f, *rest: v145a._with_f3(f), green_run_advance=200)
    assert len(seen) == 2, lines
    assert regression_runs(runs), runs
    assert CHECKED and CHECKED[0].passed, CHECKED[0].output[:300]
    assert not any("no regression run starts past" in l for l in lines), lines

@case
def short_of_time_the_audited_repairs_check_starts_no_regression_run_past_its_bound():

    fixer = {"do": [("write", {rb.APP: "base OWN-OK"}), ("advance", 400), ("check",)]}
    f, lines, seen, runs, passed = run_world([dict(RED_UNIT), fixer], nodes_left=10, audit=True, green_run_advance=200)
    fx = fixer_of(seen)
    bound = offset_of(f, fx.saw_call_deadline)
    own = [t for t, specs, text in runs if specs == [rb.OWN] and "OWN-OK" in text]
    assert own and own[0] < bound <= own[0] + 200, (own, bound)
    late = regression_runs(runs)
    assert late == [], f"a regression run started past the audited repair's end ({bound:.0f} s): {late}"
    assert any("no regression run starts past" in l for l in lines), [l for l in lines if "[tdd]" in l or "[audit]" in l]

    assert not passed and f.report["nodes"]["REQ-9"]["status"] != "passed", f.report["nodes"]["REQ-9"]
    assert f.regression_debts == [], f.regression_debts

@case
def the_test_authors_calls_end_by_the_phase_end_inside_calls_end_by():

    import main as m
    from agent import clock
    from agent import llm as llm_module

    class Client:
        def __init__(self):
            self.ends = []

        def chat(self, messages, tools=None, end=None, **kw):
            self.ends.append(end)
            return "answer"

    class OldClient:
        def chat(self, messages, **kw):
            return kw

    class Author:
        def __init__(self, llm):
            self.llm = llm

    f = m.Factory.__new__(m.Factory)
    client = Client()
    f.llm, f.author = client, Author(client)
    saved_now = clock.now
    try:
        clock.now = lambda: 5000.0
        with f.calls_end_by(5300.0):
            assert f.author.llm is f.llm and f.llm is not client
            f.author.llm.chat([], tag="author")
        assert f.llm is client and f.author.llm is client
        client.chat([])
        with f.calls_end_by(None):
            assert f.llm is client
        old = OldClient()
        f.llm, f.author = old, Author(old)
        saved_log = m.log
        m.log = lambda line: None
        try:
            with f.calls_end_by(5300.0):
                assert f.llm is old and f.author.llm is old
        finally:
            m.log = saved_log
    finally:
        clock.now = saved_now
    assert len(client.ends) == 2 and client.ends[1] is None, client.ends
    assert abs(client.ends[0] - (llm_module._clock() + 300.0)) < 5.0, (client.ends[0], llm_module._clock())

@case
def source_shape_the_audit_repair_keeps_its_v145_end_and_the_restart_sets_call_deadline():
    import inspect

    import main as m

    body = inspect.getsource(m.Factory.process_node)
    assert "repair_end = self.hard_deadline(max(deadline, clock.now() + 480)" in body
    restart = body[body.index("restarter = Implementer("):]
    assert "self.set_call_deadline(restarter, deadline)" in restart.split("again = restarter.implement(")[0]
    assert m.Factory.AUDIT_BOUND_SHARE_SEC == m.MIN_BATCH_SEC == 1200.0 and m.Factory.AUDIT_LATE_WINDOW_SEC == 480.0
    from agent.settings import _DEFAULTS
    assert _DEFAULTS["audit_repair_bound"] is True

SEED = "backend/src/database/init_db.js"
AUTH = "backend/src/routes/auth.js"
SHARED_SENTENCE = ("SHARED DATA: backend/src/database/init_db.js holds data that the tests of earlier requirements rely on "
                   "(seed / init data / fixtures): check that the rows / accounts / values those tests use are unchanged "
                   "before changing other code.")

def _evidence(reference: dict[str, bytes], now: dict[str, str], covered: dict[str, list[str]]):
    import main as factory_module

    with tempfile.TemporaryDirectory() as tmp:
        f = rb.make_factory(Path(tmp), reference[rb.APP].decode())
        f.file_specs = covered
        for rel, text in list((k, v.decode()) for k, v in reference.items()) + list(now.items()):
            path = f.project_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        books: dict = {}
        saved = factory_module.log
        factory_module.log = lambda line: None
        try:
            text = f.break_evidence([rb.F1], reference, books, [])
            snapshot = f.snapshot_sources()
        finally:
            factory_module.log = saved
    return text, books, snapshot

def _v145_text(reference: dict[str, bytes], now: dict[str, bytes], covered: list[str]) -> str:
    import main as factory_module

    changed, diff = factory_module.change_digest(reference, now, first=covered)
    if not changed:
        return ""
    label = "when this batch began (they passed then)"
    return (f"FILES THIS BATCH CHANGED SINCE THOSE SPECS LAST PASSED ({label}): {', '.join(changed[:20])}"
            + (" ..." if len(changed) > 20 else "") + "\n"
            "Their diff (files the broken specs' own batches wrote come first; '-' = a line removed or "
            "replaced, '+' = a line added):\n" + diff + "\n")

def _big(tag: str) -> str:
    return NL.join(f"const {tag}{i} = '{tag} line {i} " + "x" * 60 + "';" for i in range(40))

@case
def a_changed_seed_file_comes_first_in_the_break_evidence_with_the_shared_data_sentence():
    covered = ["backend/src/routes/a.js", "backend/src/routes/b.js", "backend/src/routes/c.js", "backend/src/routes/d.js", AUTH]
    reference = {rb.APP: b"base", "backend/src/server.js": b"// server", SEED: b"insert user alice-dev active" + NL.encode(),
                 AUTH: b"auth v1"}
    for rel in covered[:4]:
        reference[rel] = _big("old").encode()
    now = {SEED: "insert user alice-dev suspended" + NL, AUTH: "auth v2"}
    now.update({rel: _big("new") for rel in covered[:4]})
    text, books, _ = _evidence(reference, now, {rel: [rb.F1] for rel in covered})
    head = "FILES THIS BATCH CHANGED SINCE THOSE SPECS LAST PASSED (when this batch began (they passed then)): "
    assert head + SEED + ", backend/src/routes/a.js, " + AUTH + ", backend/src/routes/b.js, backend/src/routes/c.js, backend/src/routes/d.js" + NL + SHARED_SENTENCE + NL in text, text
    assert "Their diff (shared data files first, then the files the broken specs' own batches wrote; '-' = a line removed" in text, text

    assert text.index("--- " + SEED) < text.index("--- backend/src/routes/a.js"), text
    assert "-insert user alice-dev active" + NL + "+insert user alice-dev suspended" in text, text
    assert "(also changed, not shown: backend/src/routes/d.js)" in text and len(text) < 4500, (len(text), text)
    assert text.count("SHARED DATA:") == 1 and books["break_evidence"] == text

@case
def without_a_changed_shared_data_file_the_evidence_is_byte_identical_to_v145():
    covered = ["backend/src/routes/a.js", AUTH]
    base = {rb.APP: b"base", "backend/src/server.js": b"// server", SEED: b"insert user alice-dev active", AUTH: b"auth v1",
            "backend/src/routes/a.js": _big("old").encode(), "backend/src/database/.seed.env": b"SEED_TOKEN=OLD"}
    for now in ({AUTH: "auth v2", "backend/src/routes/a.js": _big("new")},
                {AUTH: "auth v2", "backend/src/database/.seed.env": "SEED_TOKEN=NEW"},
                {rb.APP: "base feature"}):
        text, books, snapshot = _evidence(dict(base), now, {rel: [rb.F1] for rel in covered + [rb.APP]})
        expected = _v145_text(base, snapshot, sorted(covered + [rb.APP]))
        assert expected and text == expected, (now, text, expected)
        assert "SHARED DATA" not in text and "SEED_TOKEN" not in text and ".seed.env" not in text, text

@case
def a_foundation_break_and_a_regression_break_through_a_seed_file_carry_the_sentence():
    from agent.codegen import FOUNDATION_MARK

    seed = "frontend/src/data/seed.ts"
    sentence = (f"SHARED DATA: {seed} holds data that the tests of earlier requirements rely on (seed / init data / "
                "fixtures): check that the rows / accounts / values those tests use are unchanged before changing other code.")

    def prep(f):
        (f.project_dir / seed).parent.mkdir(parents=True, exist_ok=True)
        (f.project_dir / seed).write_text("rows alice", encoding="utf-8")

    for app, where in (("base feature", "foundation"), ("base OWN-OK", "regression")):
        v145a.CHECKS.clear()
        v145a.run("base", [{"do": [("write", {rb.APP: app, seed: "rows " + rb.brk(rb.S1)}), ("check",)]}], prep=prep)
        if where == "foundation":
            notes = [out[out.index(FOUNDATION_MARK):] for out in v145a.CHECKS if FOUNDATION_MARK in out]
        else:
            notes = [out for out in v145a.CHECKS if "This batch's own spec PASSES" in out]
        assert notes, (where, v145a.CHECKS)
        note = notes[0]

        assert f"(when this batch began (they passed then)): {seed}, {rb.APP}" + NL + sentence + NL in note, (where, note)
        assert note.index("--- " + seed) < note.index("--- " + rb.APP), (where, note)

    v145a.CHECKS.clear()
    v145a.run("base", [{"do": [("write", {rb.APP: "base feature " + rb.brk(rb.S1)}), ("check",)]}], prep=prep)
    notes = [out for out in v145a.CHECKS if FOUNDATION_MARK in out]
    assert notes and not any("SHARED DATA" in out for out in notes), notes
    assert any("Their diff (files the broken specs' own batches wrote come first;" in out for out in notes), notes

@case
def the_classifier_is_codegens_seed_conflict_one():
    import main as m
    from agent import codegen

    import inspect
    import re

    assert m.SHARED_DATA_FILE is codegen.SHARED_DATA_FILE

    source = inspect.getsource(m)
    assert re.search(r"(?m)^from agent\.codegen import [^\n]*\bSHARED_DATA_FILE\b", source), "not imported from agent.codegen"
    assert not re.search(r"(?m)^\s*SHARED_DATA_FILE\s*=", source), "a local copy of the classifier"

FSEED = "frontend/src/data/seed.ts"

def run_salvage(script: list[dict], settings: dict | None = None, advance_on_run: float = 0.0, prep=None):
    import main as factory_module
    from agent import clock
    from agent.testrun import TestOutcome

    lines: list[str] = []
    seen: list = []
    offset = [0.0]
    names = ("run_e2e", "build_frontend", "Implementer", "run_node_tests", "static_test_defects", "parse_defects_in_output",
             "lint_application", "log")
    saved = {name: getattr(factory_module, name) for name in names}
    saved_now = clock.now
    with v145a.Settings(audit_enabled=False, **(settings or {})), tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = rb.World(root)
        f = rb.make_factory(root, "base")
        (root / FSEED).parent.mkdir(parents=True, exist_ok=True)
        (root / FSEED).write_text("rows alice", encoding="utf-8")
        if prep:
            prep(f)

        def run_e2e(project_dir, specs, log=print):
            offset[0] += advance_on_run
            return world.run_e2e(project_dir, specs, log=log)

        try:
            frozen = time.time()
            clock.now = lambda: frozen + offset[0]
            f.started = frozen
            factory_module.run_e2e, factory_module.build_frontend = run_e2e, world.build_frontend
            factory_module.Implementer = v145a.fake_implementer(script, seen, offset)
            factory_module.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
            factory_module.static_test_defects = lambda project_dir, files, **kw: ""
            factory_module.parse_defects_in_output = lambda output, files: ""
            factory_module.lint_application = lambda project_dir, native: ""
            factory_module.log = lines.append
            results = [f.process_node(dict(v145a.NODE), 1)]
            after = {p.relative_to(root / "frontend" / "src").as_posix(): p.read_text(encoding="utf-8")
                     for p in sorted((root / "frontend" / "src").rglob("*")) if p.is_file()}
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
            clock.now = saved_now
    assert not script, f"scripted steps left over: {script}"
    return lines, after, world, results, f

def _break(app: str, seed: str) -> list[dict]:
    return [{"do": [("write", {rb.APP: app, FSEED: seed}), ("check",)]}]

@case
def a_seed_put_back_that_heals_the_foundation_with_the_own_spec_green_is_kept():
    lines, after, world, results, f = run_salvage(_break("base OWN-OK", "rows " + rb.brk(rb.S1)))
    assert after == {"App.tsx": "base OWN-OK", "data/seed.ts": "rows alice"}, (after, lines)
    kept = [l for l in lines if "salvage KEPT: with frontend/src/data/seed.ts put back" in l]
    assert len(kept) == 1 and "REQ-1-1-1.spec.js" in kept[0], lines
    assert not any("FOUNDATION specs;" in l and "put back to the state before this batch" in l for l in lines), lines

    text = "base OWN-OK" + NL + "rows alice"
    assert [rb.F1] in world.runs_on(text) and [rb.OWN] in world.runs_on(text), world.runs
    assert results == [True], (results, lines)

    assert FSEED not in f.file_specs and rb.OWN in f.file_specs.get(rb.APP, []), f.file_specs
    assert "REQ-9" in f.regression_debts, f.regression_debts

@case
def a_put_back_that_breaks_the_own_spec_is_not_kept_the_next_file_is_tried():

    lines, after, world, results, f = run_salvage(_break("base feature", "rows OWN-OK " + rb.brk(rb.S1)))
    assert any("with frontend/src/data/seed.ts put back the foundation spec(s) pass but the batch's own spec fails" in l for l in lines), lines
    assert any("with frontend/src/App.tsx put back the foundation spec(s) still fail" in l for l in lines), lines
    assert any("salvage found no state to keep (2 tried)" in l for l in lines), lines
    assert any("ends red on regressions in the FOUNDATION specs; 2 file(s) put back" in l for l in lines), lines
    assert after == {"App.tsx": "base", "data/seed.ts": "rows alice"} and results == [False], (after, results)

    lines, after, world, results, f = run_salvage(_break("base " + rb.brk(rb.S1), "rows OWN-OK"))
    tries = [l for l in lines if "salvage: with" in l or "salvage KEPT" in l]
    assert len(tries) == 2 and "seed.ts put back the foundation spec(s) still fail" in tries[0], tries
    assert "salvage KEPT: with frontend/src/App.tsx put back" in tries[1], tries
    assert after == {"App.tsx": "base", "data/seed.ts": "rows OWN-OK"} and results == [True], (after, results)

def _normal(lines: list[str]) -> list[str]:
    import re

    return [re.sub(r"\d+(\.\d+)?", "#", l) for l in lines]

@case
def without_time_or_switched_off_the_put_back_is_as_v145():
    script = _break("base OWN-OK", "rows " + rb.brk(rb.S1))
    off_lines, off_after, off_world, off_results, _ = run_salvage([dict(s) for s in script], settings={"foundation_salvage": False})
    assert off_after == {"App.tsx": "base", "data/seed.ts": "rows alice"} and off_results == [False], off_after
    assert not any("salvage" in l for l in off_lines), off_lines

    late = [{"do": [("write", {rb.APP: "base OWN-OK", FSEED: "rows " + rb.brk(rb.S1)}), ("check",), ("advance", 20000)]}]
    lines, after, world, results, _ = run_salvage(late)
    off2 = run_salvage([{"do": [("write", {rb.APP: "base OWN-OK", FSEED: "rows " + rb.brk(rb.S1)}), ("check",), ("advance", 20000)]}],
                     settings={"foundation_salvage": False})
    assert (after, results) == (off2[1], off2[3]) and after["data/seed.ts"] == "rows alice", (after, off2[1])
    assert _normal(lines) == _normal(off2[0]) and not any("salvage" in l for l in lines), [l for l in lines if l not in off2[0]]
    assert any("ends red on regressions in the FOUNDATION specs; 2 file(s) put back" in l for l in lines), lines
    assert len(world.runs) == len(off2[2].runs), (world.runs, off2[2].runs)

@case
def direct_the_salvage_never_passes_the_deadline():
    import main as m
    from agent import clock

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = rb.World(root)
        f = rb.make_factory(root, "base")
        (root / FSEED).parent.mkdir(parents=True, exist_ok=True)
        before = {rb.APP: b"base", "backend/src/server.js": b"// server", FSEED: b"rows OWN-OK"}
        (root / rb.APP).write_text("base " + rb.brk(rb.S1), encoding="utf-8")
        (root / FSEED).write_text("rows OWN-OK changed", encoding="utf-8")
        f.durations = {"e2e": [(10.0, 1)], "build": [(1.0, 0)]}
        offset = [0.0]
        saved = (m.run_e2e, m.build_frontend, m.log, clock.now)
        lines: list[str] = []
        frozen = time.time()

        def run_e2e(project_dir, specs, log=print):
            offset[0] += 100.0
            return world.run_e2e(project_dir, specs, log=log)

        try:
            clock.now = lambda: frozen + offset[0]
            m.run_e2e, m.build_frontend, m.log = run_e2e, world.build_frontend, lines.append
            state = {"failed": ["REQ-1-1-1.spec.js"], "foundation": True}

            got = f.salvage_foundation_break("REQ-9", before, [rb.OWN], state, frozen + 325.0)
            assert got == ([], "", ""), got
            assert any("salvage stopped: no time for the next try (" in l for l in lines), lines
            assert sum("salvage: with" in l for l in lines) == 1, lines
            assert clock.now() <= frozen + 325.0, offset
            assert (root / rb.APP).read_text(encoding="utf-8") == "base " + rb.brk(rb.S1)
            assert (root / FSEED).read_text(encoding="utf-8") == "rows OWN-OK changed"

            lines.clear()
            offset[0] = 0.0
            runs = len(world.runs)
            assert f.salvage_foundation_break("REQ-9", before, [rb.OWN], state, frozen + 200.0) == ([], "", "")
            assert not lines and len(world.runs) == runs and offset[0] == 0.0, lines

            f.hard_end_at = frozen + m.HARD_END_FINISH_SEC + 100.0
            assert f.salvage_foundation_break("REQ-9", before, [rb.OWN], state, frozen + 10000.0) == ([], "", "")
            assert not lines and len(world.runs) == runs, lines
        finally:
            m.run_e2e, m.build_frontend, m.log, clock.now = saved

F3_TWO = F3_BREAK + " " + rb.brk("REQ-2-1: Scenario 2")

@case
def a_salvaged_batch_still_owing_a_non_foundation_spec_goes_the_kept_owing_way():

    script = [{"do": [("write", {rb.APP: "base OWN-OK " + F3_TWO, FSEED: "rows " + rb.brk(rb.S1)}), ("check",)]},
              {"do": []}]
    lines, after, world, results, f = run_salvage(script, prep=v145a._with_f3)
    assert any("salvage KEPT: with frontend/src/data/seed.ts put back" in l for l in lines), lines
    assert after == {"App.tsx": "base OWN-OK " + F3_TWO, "data/seed.ts": "rows alice"}, (after, lines)
    assert not any("put back: its own spec(s) pass" in l for l in lines), [l for l in lines if "put back" in l]
    assert results == [True] and "REQ-9" in f.regression_debts, (results, lines)

@case
def a_usable_best_state_is_taken_instead_of_a_salvage():
    import inspect

    import main as m

    body = inspect.getsource(m.Factory.process_node)

    gate = body[body.index("def salvage_first("):body.index("def implementer_log(")]
    assert 'if best_state and setting_on("best_batch_state") and best_state.get("tests") == dict(test_files):' in gate, gate
    assert "return [], \"\", \"\"" in gate.split("salvaged = self.salvage_foundation_break")[0], gate
    assert "check_state[\"kept_at_best\"] = True" in gate and 'debt_seen["evidence"]["sources"]' not in body, gate

def main() -> int:
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} v146a cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
