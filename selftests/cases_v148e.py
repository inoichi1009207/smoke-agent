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
import cases_v146a as v146a
import cases_v147a as v147a

NL = chr(10)
ABSENT = object()
CASES = []
T0 = 1.0e9
SPEC = "backend/test-e2e/REQ-5.spec.js"
FOUND = ["backend/test-e2e/REQ-1.spec.js", "backend/test-e2e/REQ-2.spec.js"]
NOT_RUN_NOTE = "the phase's end has passed"

def case(function):
    CASES.append(function)
    return function

def not_run(names: list[str], passed: int = 0):
    from agent.verify import NOT_RUN_HEAD, StepResult

    text = ", ".join(Path(n).name for n in names)
    return StepResult(False, f"{NOT_RUN_HEAD}{text}\n0 of {len(names)} spec files passed\n{len(names)} spec file(s) not run: "
                             f"{NOT_RUN_NOTE}\n" + (f"  {passed} passed\n" if passed else ""), ran=passed, failed_tests=[])

def red_named(name: str):
    from agent.verify import FAILED_FILES_HEAD, StepResult

    return StepResult(False, f"{FAILED_FILES_HEAD}{name}\n  1) test-e2e/{name}:3:1 › t\n  1 failed\n", ran=1)

class Patched:

    def __init__(self, **main_attrs) -> None:
        self.main_attrs, self.saved, self.t = main_attrs, [], T0

    def set(self, owner, name, value) -> None:
        self.saved.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    def __enter__(self):
        import main as m
        from agent import clock, verify

        self.m, self.verify = m, verify
        self.set(clock, "now", lambda: self.t)
        self.set(m, "log", lambda *a, **k: None)
        self.set(verify, "isolation_wanted", lambda: True)
        for name, value in self.main_attrs.items():
            self.set(m, name, value)
        return self

    def __exit__(self, *exc) -> None:
        for owner, name, value in reversed(self.saved):
            setattr(owner, name, value)

def factory(m):
    f = m.Factory.__new__(m.Factory)
    f.project_dir = ROOT
    f.recorded = []
    f.record_run = lambda files, ok, output: f.recorded.append((list(files), ok))
    f.green_specs = list(FOUND)
    return f

class Recorder:

    def __init__(self, env: Patched, answer, takes_until: bool = True, step: float = 100.0) -> None:
        self.env, self.answer, self.step, self.calls, self.takes_until = env, answer, step, [], takes_until

    def fake(self):
        if self.takes_until:
            def run(*args, until=None, **kwargs):
                return self.hit(args, kwargs if until is None else {**kwargs, "until": until})
        else:
            def run(project_dir, spec_files, log=print):
                return self.hit((project_dir, spec_files), {"log": log})
        return run

    def hit(self, args, kwargs):
        self.calls.append((args, kwargs))
        result = self.answer(len(self.calls))
        self.env.t += self.step
        return result

@case
def a_partial_green_run_stopped_at_the_end_is_unfinished_never_failing():
    from agent.verify import StepResult

    groups = [(10, [(10, 1, "a")]), (20, [(20, 1, "b")])]
    for base in (StepResult(True, "(all spec files passed)", ran=3), red_named("REQ-3.spec.js")):
        with Patched() as env:
            rec = Recorder(env, lambda n: not_run([SPEC], passed=1))
            env.set(env.verify, "run_tests_isolated", rec.fake())
            f = factory(env.m)
            out = f.with_partial_green(base, [(SPEC, groups)], phase_end={"end": T0 + 50, "name": "x"})
            assert out is None, out and out.output
            assert len(rec.calls) == 1 and rec.calls[0][1].get("until") == T0 + 50, rec.calls
            assert rec.calls[0][0][1:3] == (SPEC, groups)

@case
def a_partial_green_failure_before_the_end_stays_booked_to_its_spec():
    from agent.verify import StepResult

    groups = [(10, [(10, 1, "a")])]
    with Patched() as env:
        rec = Recorder(env, lambda n: red_named("REQ-5.spec.js"))
        env.set(env.verify, "run_tests_isolated", rec.fake())
        f = factory(env.m)
        out = f.with_partial_green(StepResult(True, "ok", ran=1), [(SPEC, groups)], phase_end={"end": T0 + 50, "name": "x"})
        assert out is not None and not out.ok and env.m.failed_spec_names(out.output) == {"REQ-5.spec.js"}, out

@case
def a_run_alone_stopped_at_the_end_blames_nothing_and_books_nothing():
    with Patched() as env:
        rec = Recorder(env, lambda n: not_run([FOUND[0]]))
        env.set(env.m, "run_e2e", rec.fake())
        f = factory(env.m)

        out = f.failing_alone("REQ-9", list(FOUND), {"REQ-1.spec.js"}, phase_end={"end": T0 + 50, "name": "x"})
        assert out is None, out
        assert len(rec.calls) == 1 and rec.calls[0][1].get("until") == T0 + 50, rec.calls
        assert f.recorded == [], f.recorded

@case
def a_run_alone_that_failed_before_the_end_is_still_blamed():
    with Patched() as env:
        rec = Recorder(env, lambda n: red_named("REQ-1.spec.js"))
        env.set(env.m, "run_e2e", rec.fake())
        f = factory(env.m)
        out = f.failing_alone("REQ-9", list(FOUND), {"REQ-1.spec.js"}, phase_end={"end": T0 + 500, "name": "x"})
        assert [name for name, _ in out] == ["REQ-1.spec.js"], out

def baseline_factory(env):
    f = factory(env.m)
    f.time_left = lambda: 1.0e6
    f.restore_failures = 0
    f.snapshot_sources = lambda: {"app.js": b"now"}
    f.restore_sources = lambda state: None
    env.set(env.m, "build_frontend", lambda *a, **k: env.verify.StepResult(True, "built"))
    return f

@case
def a_baseline_run_stopped_at_the_end_is_unknown_and_not_cached():

    for specs, end, answer in (([FOUND[0]], T0 + 50, lambda env, n: not_run([FOUND[0]])),
                               (list(FOUND), T0 + 150, lambda env, n: env.verify.StepResult(True, "ok", ran=1) if n == 1
                                else not_run([FOUND[1]]))):
        with Patched() as env:
            rec = Recorder(env, lambda n, env=env, answer=answer: answer(env, n))
            env.set(env.m, "run_e2e", rec.fake())
            f = baseline_factory(env)
            f.green_specs = list(specs)
            out = f.foundation_baseline("REQ-9", {"app.js": b"before"}, phase_end={"end": end, "name": "x"})
            assert out is None, out
            assert len(rec.calls) == len(specs) and all(c[1].get("until") == end for c in rec.calls), rec.calls
            assert f.__dict__.get("foundation_baselines") == {}, "a cut baseline is not booked (a later caller may take it)"

@case
def a_baseline_whose_runs_finish_before_the_end_is_booked_as_before():
    with Patched() as env:
        rec = Recorder(env, lambda n: red_named("REQ-1.spec.js") if n == 1 else env.verify.StepResult(True, "ok", ran=1))
        env.set(env.m, "run_e2e", rec.fake())
        f = baseline_factory(env)
        out = f.foundation_baseline("REQ-9", {"app.js": b"before"}, phase_end={"end": T0 + 5000, "name": "x"})
        assert out is not None and set(out) == {"REQ-1.spec.js"}, out
        assert len(rec.calls) == 2 and all(c[1].get("until") == T0 + 5000 for c in rec.calls), rec.calls

@case
def the_red_checks_group_run_gets_the_end_and_a_not_run_group_blames_nothing():
    with Patched() as env:
        rec = Recorder(env, lambda n: not_run(FOUND))
        env.set(env.m, "run_e2e", rec.fake())
        f = factory(env.m)
        f.regression_specs = lambda before, own, pool=None: list(FOUND)
        f.foundation_checks = {}
        out = f.foundation_note_while_red("REQ-9", [SPEC], {"app.js": b"before"}, phase_end={"end": T0 + 50, "name": "x"})
        assert out == "", out
        assert len(rec.calls) == 1 and rec.calls[0][1].get("until") == T0 + 50, rec.calls
        assert rec.calls[0][0][1] == list(FOUND)

@case
def without_a_phase_end_every_call_is_the_v147_call():
    from agent.verify import StepResult

    def check(env, rec, expect_args, expect_log):
        assert len(rec.calls) >= 1, rec.calls
        for args, kwargs in rec.calls:
            assert "until" not in kwargs and kwargs == {"log": expect_log}, kwargs
        assert rec.calls[0][0][1:] == expect_args, rec.calls[0][0]

    for phase_end in (None, {"end": None, "name": ""}):
        kw = {} if phase_end is None else {"phase_end": phase_end}
        with Patched() as env:
            rec = Recorder(env, lambda n: StepResult(True, "ok", ran=1))
            env.set(env.verify, "run_tests_isolated", rec.fake())
            groups = [(10, [(10, 1, "a")])]
            assert factory(env.m).with_partial_green(StepResult(True, "ok"), [(SPEC, groups)], **kw).ok
            check(env, rec, (SPEC, groups), env.m.log)
        with Patched() as env:
            rec = Recorder(env, lambda n: red_named("REQ-1.spec.js"))
            env.set(env.m, "run_e2e", rec.fake())
            assert factory(env.m).failing_alone("REQ-9", list(FOUND), {"REQ-1.spec.js"}, **kw)
            check(env, rec, ([FOUND[0]],), env.m.log)
        with Patched() as env:
            rec = Recorder(env, lambda n: StepResult(True, "ok", ran=1))
            env.set(env.m, "run_e2e", rec.fake())
            assert baseline_factory(env).foundation_baseline("REQ-9", {"app.js": b"x"}, **kw) == {}
            check(env, rec, ([FOUND[0]],), env.m.log)
        with Patched() as env:
            rec = Recorder(env, lambda n: StepResult(True, "ok", ran=2))
            env.set(env.m, "run_e2e", rec.fake())
            f = factory(env.m)
            f.regression_specs = lambda before, own, pool=None: list(FOUND)
            f.foundation_checks = {}
            assert f.foundation_note_while_red("REQ-9", [SPEC], {"app.js": b"x"}, **kw) == ""
            check(env, rec, (list(FOUND),), env.m.log)

@case
def a_callee_without_until_gets_the_v147_call_even_with_an_end():
    from agent.verify import StepResult

    with Patched() as env:
        rec = Recorder(env, lambda n: red_named("REQ-1.spec.js"), takes_until=False)
        env.set(env.m, "run_e2e", rec.fake())
        assert factory(env.m).failing_alone("REQ-9", list(FOUND), {"REQ-1.spec.js"}, phase_end={"end": T0 + 500, "name": "x"})
        assert rec.calls == [((ROOT, [FOUND[0]]), {"log": env.m.log})], rec.calls
    with Patched() as env:
        f = factory(env.m)
        seen = []
        f.run_partial_tests = lambda spec, groups: seen.append(spec) or StepResult(True, "ok")
        assert f.with_partial_green(StepResult(True, "ok"), [(SPEC, [(1, [(1, 1, "t")])])], phase_end={"end": T0 + 9, "name": "x"}).ok
        assert seen == [SPEC]

@case
def the_real_verify_functions_take_until_so_production_passes_it():
    import main as m
    from agent import verify

    assert m.takes_until(verify.run_e2e) and m.takes_until(verify.run_tests_isolated)
    assert m.until_kw(5, verify.run_e2e) == {"until": 5.0} and m.until_kw(None, verify.run_e2e) == {}
    assert m.until_kw(7, lambda project_dir, specs, log=print: None) == {}
    assert m.until_kw(7, lambda *a, **k: None) == {"until": 7.0}
    saved = m.run_e2e
    try:
        m.run_e2e = verify.run_e2e
        assert m.until_kw(3) == {"until": 3.0}
        m.run_e2e = lambda project_dir, specs, log=print: None
        assert m.until_kw(3) == {}
    finally:
        m.run_e2e = saved

@case
def stopped_unrun_reads_only_the_unnamed_not_run_shape():
    import main as m
    from agent.verify import StepResult

    assert m.stopped_unrun(not_run([SPEC]))
    assert not m.stopped_unrun(red_named("REQ-5.spec.js"))
    both = StepResult(False, "FAILED SPEC FILES: REQ-1.spec.js\nNOT RUN SPEC FILES: REQ-2.spec.js\n1 of 2 spec files passed\n")
    assert not m.stopped_unrun(both)
    assert not m.stopped_unrun(StepResult(True, "NOT RUN SPEC FILES: REQ-2.spec.js\n"))
    assert not m.stopped_unrun(StepResult(True, "NOT RUN SPEC FILES: REQ-2.spec.js\n", skipped=True))
    assert not m.stopped_unrun(StepResult(False, "E2E FAILED\nno spec ran\n"))

@case
def source_shape_every_named_site_passes_the_end_and_the_untouched_ones_do_not():
    import inspect

    import main as m

    def src(name):
        return inspect.getsource(getattr(m.Factory, name))

    assert src("salvage_foundation_break").count("**until_kw(salvage_end())") == 4
    assert "return min(deadline, clock.now() + self.hard_left(HARD_END_FINISH_SEC))" in src("salvage_foundation_break")
    assert src("settle_unnamed_regression").count('**until_kw((phase_end or {}).get("end"))') == 1
    assert src("foundation_note_while_red").count('**until_kw((phase_end or {}).get("end"))') == 1
    assert src("failing_alone").count("**until_kw(end)") == 1 and src("foundation_baseline").count("**until_kw(end)") == 1
    assert "**until_kw(until, verify.run_tests_isolated)" in src("run_partial_tests")
    body = src("process_node")
    assert body.count('**until_kw(phase_end.get("end"))') == 1
    assert "e2e = run_e2e(self.project_dir, all_e2e, log=log)\n" in body.replace("\r\n", "\n")
    assert body.index("if stopped_unrun(again) and self.phase_passed(phase_end):") < body.index("self.settle_unnamed_regression(")

def run_world_until(script: list[dict], answer, nodes_left: int = 10, settings: dict | None = None,
                    green_run_advance: float = 200.0, prep=None):
    import main as factory_module
    from agent import clock
    from agent.testrun import TestOutcome

    lines: list[str] = []
    seen: list = []
    offset = [0.0]
    calls: list = []
    names = ("run_e2e", "build_frontend", "Implementer", "run_node_tests", "static_test_defects", "parse_defects_in_output",
             "lint_application", "log", "audit_node", "findings_brief")
    saved = {name: getattr(factory_module, name) for name in names}
    saved_now = clock.now
    with v145a.Settings(audit_enabled=False, **(settings or {})), tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = rb.World(root)
        f = rb.make_factory(root, "base")

        def run_e2e(project_dir, specs, log=print, until=ABSENT):
            kwargs = {"log": log} if until is ABSENT else {"log": log, "until": until}
            calls.append((offset[0], (project_dir, list(specs)), kwargs, world.sources()))
            if green_run_advance and list(specs) == [rb.OWN] and "OWN-OK" in world.sources():
                offset[0] += green_run_advance
            return answer(world, offset, list(specs), lambda: world.run_e2e(project_dir, specs, log=log))

        try:
            frozen = time.time()
            clock.now = lambda: frozen + offset[0]
            f.started = frozen
            v145a._with_f3(f)
            if prep:
                prep(f, offset, calls)
            factory_module.run_e2e, factory_module.build_frontend = run_e2e, world.build_frontend
            factory_module.Implementer = v146a.fake_class(script, seen, offset)
            factory_module.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
            factory_module.static_test_defects = lambda project_dir, files, **kw: ""
            factory_module.parse_defects_in_output = lambda output, files: ""
            factory_module.lint_application = lambda project_dir, native: ""
            factory_module.log = lines.append
            factory_module.audit_node = lambda llm, project_dir, node, contract, log=print, use_llm=True: []
            factory_module.findings_brief = lambda findings: "AUDIT BRIEF"
            passed = f.process_node(dict(v145a.NODE), nodes_left)
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
            clock.now = saved_now
    assert not script, f"scripted steps left over: {script}"
    return f, lines, seen, calls, passed

def regression_calls(calls, marker: str = "OWN-OK") -> list:
    return [c for c in calls if marker in c[3] and set(c[1][1]) & v146a.EARLIER]

@case
def check_regression_run_gets_the_phase_end_and_its_not_run_result_is_unfinished_even_with_inconclusive_off():

    def answer(world, offset, specs, original):
        if len(specs) > 1 and "OWN-OK" in world.sources():
            offset[0] = 1300.0
            return not_run(specs)
        return original()

    foundation_seen: list = []

    def prep(f, offset, calls):
        real = f.foundation_regression_is_new
        f.foundation_regression_is_new = lambda req_id, before, state: foundation_seen.append(dict(state)) or real(req_id, before, state)

    v146a.CHECKED.clear()
    f, lines, seen, calls, passed = run_world_until(v146a.restart_script(800, "base OWN-OK"), answer,
                                                    settings={"timesave_unnamed_inconclusive": False}, prep=prep)
    assert len(seen) == 2 and any("(in-batch restart)" in l for l in lines), lines
    deadline = seen[1].options["deadline"]
    ran = regression_calls(calls)
    assert len(ran) == 1, f"precondition / no second run: {[(t, a[1]) for t, a, k, s in ran]}"
    t, (project_dir, specs), kwargs, _ = ran[0]
    assert t < v146a.offset_of(f, deadline), (t, v146a.offset_of(f, deadline))
    import main as m
    from agent.settings import SETTINGS

    assert "timesave_unnamed_inconclusive" in SETTINGS

    assert v146a.CHECKED, lines
    out = v146a.CHECKED[0].output
    assert out.startswith(m.Factory.UNFINISHED_MARK) and not v146a.CHECKED[0].passed, out[:400]
    assert "FOUNDATION" not in out, out[:400]
    assert not any(state.get("foundation") for state in foundation_seen), foundation_seen
    assert not any("running " in l and "of its spec(s) once more" in l for l in lines), lines
    assert any("no regression run starts past it" in l for l in lines), [l for l in lines if "[tdd]" in l]
    assert f.regression_debts == [] and any("in-batch restart ended budget; not kept" in l for l in lines), lines

    assert kwargs.get("until", ABSENT) == float(deadline), (kwargs, deadline)

@case
def settle_unnamed_regressions_second_run_gets_the_phase_end():
    import main as m
    from agent import clock
    from agent.verify import StepResult

    calls: list = []

    def fake(project_dir, specs, **kwargs):
        calls.append((list(specs), dict(kwargs)))
        return StepResult(True, "1 passed")

    saved = (m.run_e2e, m.log)
    try:
        m.run_e2e = fake
        m.log = lambda *a, **k: None
        f = m.Factory.__new__(m.Factory)
        f.project_dir = ROOT
        f.record_run = lambda files, ok, output: None
        first = StepResult(False, "the server did not come up")
        end = clock.now() + 1000.0
        with v145a.Settings(timesave_unnamed_inconclusive=True):
            got = f.settle_unnamed_regression("R", [rb.F1], first, phase_end={"end": end, "name": "in-batch restart"})
            assert got[1] is False and got[0].ok, got
            assert len(calls) == 1 and calls[0][0] == [rb.F1], calls
            assert calls[0][1].get("until", ABSENT) == float(end), calls
            for phase_end in (None, {"end": None, "name": ""}):
                calls.clear()
                got = f.settle_unnamed_regression("R", [rb.F1], first, phase_end=phase_end)
                assert got[1] is False and len(calls) == 1, (phase_end, calls)
                assert "until" not in calls[0][1], (phase_end, calls)
            calls.clear()
            f.settle_unnamed_regression("R", [rb.F1], first)
            assert len(calls) == 1 and "until" not in calls[0][1], calls
    finally:
        m.run_e2e, m.log = saved

@case
def without_a_phase_end_the_regression_run_call_is_exactly_v147s():

    def answer(world, offset, specs, original):
        return original()

    script = [{"do": [("write", {rb.APP: "base OWN-OK"}), ("check",)]}]
    f, lines, seen, calls, passed = run_world_until(script, answer)
    ran = regression_calls(calls)
    assert ran, [(t, a[1]) for t, a, k, s in calls]
    for _, args, kwargs, _ in ran:
        assert len(args) == 2 and args[0] == f.project_dir and set(args[1]) <= v146a.EARLIER, args
        assert kwargs == {"log": lines.append}, kwargs

    assert ran[0][1] == (f.project_dir, [s for s in f.green_specs if s in set(ran[0][1][1])]), ran[0][1]

F3, FSEED, OWNF, R12 = v147a.F3, v147a.FSEED, v147a.OWNF, v147a.R54_R12
GREEN = [rb.F1, rb.F2, R12, F3]
SALVAGE_STEP = 5.0

def stage_of(specs: list[str]) -> str:
    if list(specs) == [rb.F1]:
        return "base"
    if list(specs) == [rb.OWN]:
        return "own"
    if len(specs) > 1 and F3 in specs:
        return "rest"
    if list(specs) == [rb.F2]:
        return "unrun"
    return "other"

def drive(answer, deadline_in: float = 10000.0, hard_in: float | None = None):
    import main as m
    from agent import clock

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = rb.World(root)
        f = rb.make_factory(root, "base")
        f.green_specs = list(GREEN)
        (root / FSEED).parent.mkdir(parents=True, exist_ok=True)
        before = {rb.APP: b"base", "backend/src/server.js": b"// server", FSEED: b"rows alice"}
        (root / rb.APP).write_text("base " + rb.brk(rb.S1), encoding="utf-8")
        (root / FSEED).write_text("rows alice", encoding="utf-8")
        (root / OWNF).write_text("OWN-OK", encoding="utf-8")
        f.durations = {"e2e": [(10.0, 1)], "build": [(1.0, 0)]}
        records: list = []
        f.record_run = lambda files, ok, output: records.append((list(files), ok, output))
        offset = [0.0]
        calls: list = []
        lines: list[str] = []
        saved = (m.run_e2e, m.build_frontend, m.log, clock.now)
        frozen = time.time()
        deadline = frozen + deadline_in
        if hard_in is not None:
            f.hard_end_at = frozen + hard_in

        def run_e2e(project_dir, specs, log=print, until=None):
            now = clock.now()

            expected = min(deadline, now + f.hard_left(m.HARD_END_FINISH_SEC))
            calls.append((stage_of(specs), list(specs), until, expected, now))
            offset[0] += SALVAGE_STEP
            return answer(stage_of(specs), list(specs), offset, deadline, lambda: world.run_e2e(project_dir, specs, log=log))

        try:
            clock.now = lambda: frozen + offset[0]
            m.run_e2e, m.build_frontend, m.log = run_e2e, world.build_frontend, lines.append
            got = f.salvage_foundation_break("REQ-9", before, [rb.OWN], {"failed": ["REQ-1-1-1.spec.js"]}, deadline)
            app = (root / rb.APP).read_text(encoding="utf-8")
            ownf = (root / OWNF).read_text(encoding="utf-8") if (root / OWNF).is_file() else None
        finally:
            m.run_e2e, m.build_frontend, m.log, clock.now = saved
        return {"got": got, "lines": lines, "calls": calls, "records": records, "app": app, "ownf": ownf,
                "frozen": frozen, "deadline": deadline, "f": f}

REST_RED = v147a.r54_red(["REQ-2-1.spec.js"], ["REQ-1-1-3.spec.js"])

@case
def every_salvage_run_gets_the_salvage_end_as_until():
    import main as m
    from agent import clock

    finish = m.HARD_END_FINISH_SEC
    for label, hard_in, end_in in (
            ("no hard end: the deadline", None, 10000.0),
            ("hard end earlier: hard end - finish reserve", finish + 3000.0, 3000.0),
            ("hard end later: the deadline", finish + 50000.0, 10000.0)):

        def answer(stage, specs, offset, deadline, world_answer):
            return REST_RED if stage == "rest" else world_answer()

        r = drive(answer, deadline_in=10000.0, hard_in=hard_in)

        stages = [c[0] for c in r["calls"]]
        assert stages == ["base", "own", "rest", "unrun"], (label, stages, r["lines"])
        assert r["got"][0] == ["REQ-1-1-1.spec.js"] and r["got"][1] == rb.APP, (label, r["got"], r["lines"])

        for stage, specs, until, expected, now in r["calls"]:
            assert until is not None, (label, stage, "run_e2e got no until")
            assert abs(until - expected) < 1e-6, (label, stage, until - r["frozen"], expected - r["frozen"])

            want = r["frozen"] + end_in - (clock.paused_sec() if hard_in is not None and end_in < 10000.0 else 0.0)
            assert abs(until - want) < 1e-6, (label, stage, until - r["frozen"], want - r["frozen"])
            assert now < until, (label, stage)
        if hard_in is not None and end_in < 10000.0:
            assert all(c[2] < r["deadline"] for c in r["calls"]), (label, "the hard end must be the earlier end here")

@case
def a_not_run_result_at_each_salvage_stage_keeps_nothing():
    import main as m

    finish = m.HARD_END_FINISH_SEC
    reading = {"base": "the foundation spec(s) still fail",
               "own": "the foundation spec(s) pass but the batch's own spec fails",
               "rest": "the regression run of the earlier specs fails without naming a spec",
               "unrun": "do not pass on their own run; not kept"}
    for stage in ("base", "own", "rest", "unrun"):

        for advance, hard_in in (("deadline", None), ("hard end", finish + 3000.0), ("none", None)):
            served: list = []

            def answer(got_stage, specs, offset, deadline, world_answer, stage=stage, advance=advance, served=served):
                if got_stage == stage:
                    served.append(list(specs))
                    if advance == "deadline":
                        offset[0] = 10000.0 + 1.0
                    elif advance == "hard end":
                        offset[0] = 3000.0 + 1.0
                    return not_run([Path(s).name for s in specs])
                if got_stage == "rest" and stage == "unrun":
                    return REST_RED
                return world_answer()

            r = drive(answer, deadline_in=10000.0, hard_in=hard_in)
            what = (stage, advance)

            assert served, (what, [c[0] for c in r["calls"]], r["lines"])
            shape = not_run([Path(s).name for s in served[0]])
            if hasattr(m, "stopped_unrun"):
                assert m.stopped_unrun(shape), "the shape must be what v148e calls a stopped, unrun result"
            assert r["got"] == ([], "", ""), (what, r["got"], r["lines"])
            assert not any("salvage KEPT" in l for l in r["lines"]), (what, r["lines"])
            assert r["records"] == [], (what, "nothing may be booked on a salvage not kept", r["records"])
            assert any("salvage found no state to keep" in l for l in r["lines"]), (what, r["lines"])

            assert r["app"] == "base " + rb.brk(rb.S1) and r["ownf"] == "OWN-OK", (what, r["app"], r["ownf"])
            if advance == "none":
                assert any(reading[stage] in l for l in r["lines"]), (what, r["lines"])
            else:

                first = next(i for i, c in enumerate(r["calls"]) if c[0] == stage)
                assert len(r["calls"]) == first + 1, (what, [c[0] for c in r["calls"]], r["lines"])

R55_OUT = ("FAILED SPEC FILES: REQ-5.spec.js" + NL + "REQ-5.spec.js: every test ran alone, on a fresh server with fresh data of its own"
           + NL + "1 test(s) of REQ-5.spec.js not run: the phase's end has passed" + NL
           + "## REQ-5.spec.js line 10: alone on a fresh server with fresh data" + NL
           + "  1) test-e2e/REQ-5.spec.js:10:1 › REQ-5: t1 ───" + NL + NL
           + "  1 failed" + NL + "    test-e2e/REQ-5.spec.js:10:1 › REQ-5: t1 ───" + NL + "  1 passed" + NL)
T2_GONE = [("REQ-5.spec.js", 20, 1, "REQ-5: t2")]

def ledger(m, n: int = 3):
    f = m.Factory.__new__(m.Factory)
    f.booked = []

    class Trace:
        def set_test_status(self, test_id, passed):
            f.booked.append((test_id, passed))
    f.trace = Trace()
    f.test_ids_by_file = {SPEC: [f"T{i + 1}" for i in range(n)]}
    f.test_titles = {f"T{i + 1}": f"REQ-5: t{i + 1}" for i in range(n)}
    return f

@case
def r55_2_record_run_books_only_the_tests_that_ran():
    import main as m

    f = ledger(m)
    f.record_run([SPEC], False, R55_OUT, not_run_tests=T2_GONE)
    assert f.booked == [("T1", False), ("T3", True)], f.booked

    f = ledger(m)
    f.record_run([SPEC], False, R55_OUT.replace("› REQ-5: t1", "› REQ-5: `t1`"), not_run_tests=T2_GONE)
    assert f.booked == [("T1", False), ("T3", False)], f.booked

    f = ledger(m)
    f.record_run([SPEC], False, R55_OUT)
    assert f.booked == [("T1", False)], f.booked

    f = ledger(m)
    f.record_run([SPEC], False, R55_OUT, not_run_tests=[("REQ-5.spec.js", 20, 1, "REQ-5: t${n}")])
    assert f.booked == [("T1", False)], f.booked

    f = ledger(m)
    f.record_run([SPEC], False, R55_OUT.replace("1 test(s) of REQ-5", "1 test(s) of REQ-6"),
                 not_run_tests=[("REQ-6.spec.js", 20, 1, "REQ-5: t2")])
    assert f.booked == [("T1", False), ("T2", True), ("T3", True)], f.booked

@case
def r55_2_record_run_books_every_other_shape_as_v147():
    import main as m

    full = R55_OUT.replace("1 test(s) of REQ-5.spec.js not run: the phase's end has passed" + NL, "").replace("1 passed", "2 passed")
    for kw in ({}, {"not_run_tests": None}, {"not_run_tests": []}):
        f = ledger(m)
        f.record_run([SPEC], False, full, **kw)
        assert f.booked == [("T1", False), ("T2", True), ("T3", True)], (kw, f.booked)
        f = ledger(m)
        f.record_run([SPEC], True, "  3 passed" + NL, **kw)
        assert f.booked == [("T1", True), ("T2", True), ("T3", True)], (kw, f.booked)
        f = ledger(m)
        f.record_run([SPEC], False, "TypeError: test is not a function" + NL, **kw)
        assert f.booked == [("T1", None), ("T2", None), ("T3", None)], (kw, f.booked)
        f = ledger(m)
        f.record_run([SPEC], False, not_run([SPEC], passed=1).output, **kw)
        assert f.booked == [], (kw, f.booked)
    assert m.partly_run_spec_names(full) == set() and m.partly_run_spec_names(R55_OUT) == {"REQ-5.spec.js"}

@case
def r55_2_unrun_kw_hands_the_identities_only_to_a_record_run_that_takes_them():
    import main as m
    from agent.verify import StepResult

    named = StepResult(False, R55_OUT, not_run_tests=T2_GONE)
    f = ledger(m)
    assert m.unrun_kw(named, f.record_run) == {"not_run_tests": T2_GONE}
    assert m.unrun_kw(named, lambda files, ok, output: None) == {}
    assert m.unrun_kw(named, lambda *a, **k: None) == {"not_run_tests": T2_GONE}
    for plain in (StepResult(False, R55_OUT), StepResult(True, "ok"), object()):
        assert m.unrun_kw(plain, f.record_run) == {}

@case
def r55_2_with_partial_green_keeps_who_did_not_run():
    from agent.verify import StepResult

    groups = [(10, [(10, 1, "REQ-5: t1")]), (20, [(20, 1, "REQ-5: t2")])]
    base = StepResult(False, "FAILED SPEC FILES: REQ-3.spec.js" + NL + "x" + NL,
                      not_run_tests=[("REQ-3.spec.js", 40, 1, "REQ-3: d")])
    partial = StepResult(False, R55_OUT, ran=1, failed_tests=[("REQ-5.spec.js", 10, 1, "REQ-5: t1")], not_run_tests=T2_GONE)
    with Patched() as env:
        rec = Recorder(env, lambda n: partial)
        env.set(env.verify, "run_tests_isolated", rec.fake())
        out = factory(env.m).with_partial_green(base, [(SPEC, groups)], phase_end={"end": T0 + 500, "name": "x"})
    assert out is not None and env.m.failed_spec_names(out.output) == {"REQ-3.spec.js", "REQ-5.spec.js"}, out
    assert out.not_run_tests == [("REQ-3.spec.js", 40, 1, "REQ-3: d")] + T2_GONE, out.not_run_tests
    with Patched() as env:
        rec = Recorder(env, lambda n: red_named("REQ-5.spec.js"))
        env.set(env.verify, "run_tests_isolated", rec.fake())
        out = factory(env.m).with_partial_green(StepResult(True, "ok"), [(SPEC, groups)])
    assert out is not None and out.not_run_tests is None

@case
def source_shape_r55_every_e2e_ledger_booking_hands_over_who_did_not_run():
    import inspect
    import re

    import main as m

    source = inspect.getsource(m.Factory)
    calls = re.findall(r"self\.record_run\(([^\n]*)\)\n", source.replace("\r\n", "\n"))
    handed = [c for c in calls if "**unrun_kw(" in c]
    assert len(handed) == 15, (len(handed), calls)
    for c in handed:
        var = re.search(r"\*\*unrun_kw\((\w+), self\.record_run\)", c).group(1)
        assert c.split(", ")[2] == f"{var}.output", c
    plain = [c for c in calls if c not in handed]

    assert all(".passed, " in c or ", True, " in c for c in plain), plain

@case
def r55_3_run_partial_tests_hands_the_end_to_the_run_of_its_groups_together():
    import main as m

    groups = [(10, [(10, 1, "a")]), (20, [(20, 1, "b")])]
    for until in (None, T0 + 50):
        with Patched() as env:
            env.set(env.verify, "isolation_wanted", lambda: False)
            rec = Recorder(env, lambda n: env.verify.StepResult(True, "ok", ran=2))
            env.set(env.verify, "_run_e2e_once", rec.fake())
            f = factory(env.m)
            f.run_partial_tests(SPEC, groups, **({} if until is None else {"until": until}))
        assert len(rec.calls) == 1, rec.calls
        args, kwargs = rec.calls[0]
        assert kwargs.get("only") == ["test-e2e/REQ-5.spec.js:10", "test-e2e/REQ-5.spec.js:20"], kwargs
        if until is None:
            assert "until" not in kwargs, kwargs
        else:
            assert kwargs["until"] == until, kwargs

    with Patched() as env:
        env.set(env.verify, "isolation_wanted", lambda: False)
        seen = []
        env.set(env.verify, "_run_e2e_once", lambda project_dir, specs, log=print, only=None: seen.append(only) or env.verify.StepResult(True, "ok"))
        factory(env.m).run_partial_tests(SPEC, groups, until=T0 + 50)
    assert seen == [["test-e2e/REQ-5.spec.js:10", "test-e2e/REQ-5.spec.js:20"]], seen

@case
def r55_3_a_partial_run_together_past_the_end_starts_nothing_and_is_unfinished():
    import cases_isolation as iso
    import cases_v148c as c

    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        spec = iso.spec_file(root, "REQ-5.spec.js")
        world = iso.World(root, {"REQ-5.spec.js": c.green_four()})
        with c.Clock(world) as fake, iso.installed(world, PER_TEST_ISOLATION=False) as verify:
            import main as m
            from agent.verify import StepResult
            groups = verify.test_groups(root, spec, log=c.quiet)
            f = factory(m)
            f.project_dir = root
            saved = m.log
            m.log = c.quiet
            try:
                out = f.with_partial_green(StepResult(True, "ok"), [(spec, groups)], phase_end={"end": fake.t - 1.0, "name": "x"},)
                direct = f.run_partial_tests(spec, groups, until=fake.t - 1.0)
            finally:
                m.log = saved
    assert out is None
    assert world.playwright == [] and world.started == [], world.playwright
    assert m.stopped_unrun(direct) and direct.output.startswith("NOT RUN SPEC FILES: REQ-5.spec.js" + NL), direct.output

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
    print(f"{len(CASES) - failed}/{len(CASES)} v148e cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
