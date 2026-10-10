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
FSEED = "frontend/src/data/seed.ts"
F3 = "backend/test-e2e/REQ-2-1.spec.js"
F3_S1, F3_S2 = rb.brk("REQ-2-1: Scenario 1"), rb.brk("REQ-2-1: Scenario 2")
F2_S1 = rb.brk("REQ-1-1-3: Scenario 1")

def case(function):
    CASES.append(function)
    return function

def with_f3(f):
    f.green_specs = [rb.F1, rb.F2, "backend/test-e2e/REQ-1-2.spec.js", F3]
    f.e2e_specs = list(f.green_specs)

def run_world(script: list[dict], seed_before: str = "rows alice", settings: dict | None = None, prep=None, own_ran: int = -1):
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
        (root / FSEED).write_text(seed_before, encoding="utf-8")
        if prep:
            prep(f)

        def run_e2e(project_dir, specs, log=print):
            got = world.run_e2e(project_dir, specs, log=log)
            if list(specs) == [rb.OWN] and got.ok:
                got.ran = own_ran(world.sources()) if callable(own_ran) else own_ran
            return got

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
def a_put_back_that_breaks_another_earlier_spec_is_no_longer_kept_unseen():

    script = _break("base OWN-OK", "rows " + rb.brk(rb.S1)) + [{"do": []}]
    lines, after, world, results, f = run_world(script, seed_before="rows alice " + F3_S1, prep=with_f3)
    salvaged = "base OWN-OK" + NL + "rows alice " + F3_S1
    rest = [specs for specs in world.runs_on(salvaged) if F3 in specs]
    assert rest and rb.F2 in rest[0] and rb.F1 not in rest[0] and rb.OWN not in rest[0], world.runs
    kept = [l for l in lines if "salvage KEPT: with frontend/src/data/seed.ts put back" in l]
    assert len(kept) == 1 and "names non-foundation spec(s) red ['REQ-2-1.spec.js'] (booked as regression debt)" in kept[0], lines

    assert results == [True] and f.regression_debts == ["REQ-9"], (results, lines)
    assert F3 not in f.green_specs and rb.F1 in f.green_specs, f.green_specs
    assert any("owes a regression ['REQ-2-1.spec.js']; one targeted repair" in l for l in lines), lines
    assert after == {"App.tsx": "base OWN-OK", "data/seed.ts": "rows alice " + F3_S1}, after

@case
def a_kept_salvage_whose_own_run_shows_a_net_loss_is_put_back_by_the_net_loss_rule():

    script = _break("base OWN-OK", "rows " + rb.brk(rb.S1)) + [{"do": []}]

    lines, after, world, results, f = run_world(script, seed_before="rows alice " + F3_S1 + " " + F3_S2, prep=with_f3,
                                                own_ran=lambda text: 5 if rb.brk(rb.S1) in text else 1)
    assert any("salvage KEPT: with frontend/src/data/seed.ts put back" in l for l in lines), lines
    assert any("put back: its own spec(s) pass 1 test(s) but it broke 2 test(s) of earlier specs ['REQ-2-1.spec.js']" in l
               for l in lines), lines
    assert after == {"App.tsx": "base", "data/seed.ts": "rows alice " + F3_S1 + " " + F3_S2} and results == [False], after
    assert F3 in f.green_specs and "REQ-9" not in f.regression_debts, (f.green_specs, f.regression_debts)

    script = _break("base OWN-OK", "rows " + rb.brk(rb.S1)) + [{"do": []}]
    lines, after, world, results, f = run_world(script, seed_before="rows alice " + F3_S1 + " " + F3_S2, prep=with_f3, own_ran=2)
    assert results == [True] and after["data/seed.ts"] == "rows alice " + F3_S1 + " " + F3_S2 and F3 not in f.green_specs, lines

@case
def a_foundation_spec_red_in_the_regression_run_is_not_kept_the_next_file_is_tried():

    lines, after, world, results, f = run_world(_break("base OWN-OK", "rows " + rb.brk(rb.S1)), seed_before="rows alice " + F2_S1)
    assert any("with frontend/src/data/seed.ts put back the regression run of the earlier specs fails foundation spec(s) "
               "['REQ-1-1-3.spec.js']" in l for l in lines), lines
    assert any("with frontend/src/App.tsx put back the foundation spec(s) still fail" in l for l in lines), lines
    assert not any("salvage KEPT" in l for l in lines), lines
    assert any("ends red on regressions in the FOUNDATION specs; 2 file(s) put back" in l for l in lines), lines
    assert after == {"App.tsx": "base", "data/seed.ts": "rows alice " + F2_S1} and results == [False], after

@case
def a_green_regression_run_keeps_the_salvage_as_v146_books_it():

    lines, after, world, results, f = run_world(_break("base OWN-OK", "rows " + rb.brk(rb.S1)))
    kept = [l for l in lines if "salvage KEPT" in l]
    assert len(kept) == 1 and "the regression run of 1 earlier spec(s) is green" in kept[0], lines
    assert [rb.F2] in world.runs_on("base OWN-OK" + NL + "rows alice"), world.runs
    assert results == [True] and after == {"App.tsx": "base OWN-OK", "data/seed.ts": "rows alice"}, (results, after)

def direct(before_seed: str, durations: dict, deadline_in: float, partial=None, advance: float = 0.0, build_advance: float = 0.0):
    import main as m
    from agent import clock

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = rb.World(root)
        f = rb.make_factory(root, "base")
        (root / FSEED).parent.mkdir(parents=True, exist_ok=True)
        before = {rb.APP: b"base", "backend/src/server.js": b"// server", FSEED: before_seed.encode()}
        (root / rb.APP).write_text("base " + rb.brk(rb.S1), encoding="utf-8")
        (root / FSEED).write_text(before_seed, encoding="utf-8")
        (root / OWNF).write_text("OWN-OK", encoding="utf-8")
        f.durations = {kind: list(runs) for kind, runs in durations.items()}
        if partial is not None:
            f.partial_green_groups = lambda before_state, own: list(partial)
            f.run_partial_tests = lambda spec, groups: world.run_e2e(root, [spec])
        offset = [0.0]
        saved = (m.run_e2e, m.build_frontend, m.log, clock.now)
        lines: list[str] = []
        frozen = time.time()

        def run_e2e(project_dir, specs, log=print):
            offset[0] += advance
            return world.run_e2e(project_dir, specs, log=log)

        try:
            clock.now = lambda: frozen + offset[0]
            def build_frontend(project_dir, log=print):
                offset[0] += build_advance
                return world.build_frontend(project_dir, log=log)

            m.run_e2e, m.build_frontend, m.log = run_e2e, build_frontend, lines.append
            got = f.salvage_foundation_break("REQ-9", before, [rb.OWN], {"failed": ["REQ-1-1-1.spec.js"], "foundation": True},
                                             frozen + deadline_in)
        finally:
            m.run_e2e, m.build_frontend, m.log, clock.now = saved
        return got, lines, [specs for specs, _ in world.runs], offset[0]

@case
def the_regression_runs_estimate_is_part_of_every_trys_need():

    durations = {"e2e": [(10.0, 1)], "build": [(1.0, 0)]}
    got, lines, runs, _ = direct("rows alice", durations, 284.0)
    assert got == ([], "", "") and not lines and not runs, (got, lines, runs)
    got, lines, runs, _ = direct("rows alice", durations, 285.0)
    assert got[1] == "frontend/src/App.tsx" and runs == [[rb.F1], [rb.OWN], [rb.F2]], (got, runs, lines)
    assert got[2]["failed"] == [] and got[2]["unknown"] == [] and got[2]["own"].ok, got

    groups = [(F3, [(10, [("x", 10, "REQ-2-1: Scenario 1")])])]
    got, lines, runs, _ = direct("rows alice", durations, 359.0, partial=groups)
    assert got == ([], "", "") and not runs, (got, runs)
    got, lines, runs, _ = direct("rows alice", durations, 360.0, partial=groups)
    assert got[1] == "frontend/src/App.tsx" and runs[-1] == [F3], (got, runs)

@case
def a_red_partial_green_test_on_the_salvaged_state_is_owed_like_a_red_spec():
    durations = {"e2e": [(10.0, 1)], "build": [(1.0, 0)]}
    groups = [(F3, [(10, [("x", 10, "REQ-2-1: Scenario 1")])])]

    got, lines, runs, _ = direct("rows " + F3_S1, durations, 10000.0, partial=groups)
    assert got[0] == ["REQ-1-1-1.spec.js"] and got[2]["failed"] == ["REQ-2-1.spec.js"], (got, lines)
    assert "REQ-2-1.spec.js" in got[2]["output"], got

@case
def a_red_regression_run_naming_no_spec_or_without_verdict_keeps_nothing():
    import main as m
    from agent.verify import StepResult

    durations = {"e2e": [(10.0, 1)], "build": [(1.0, 0)]}
    for answer, expect in ((StepResult(False, "the server did not come up"), "without naming a spec"),
                           (StepResult(False, "", skipped=True), None)):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            world = rb.World(root)
            f = rb.make_factory(root, "base")
            (root / FSEED).parent.mkdir(parents=True, exist_ok=True)
            before = {rb.APP: b"base", "backend/src/server.js": b"// server", FSEED: b"rows alice"}
            (root / rb.APP).write_text("base " + rb.brk(rb.S1), encoding="utf-8")
            (root / FSEED).write_text("rows alice", encoding="utf-8")
            (root / OWNF).write_text("OWN-OK", encoding="utf-8")
            f.durations = durations
            saved = (m.run_e2e, m.build_frontend, m.log)
            lines: list[str] = []

            def run_e2e(project_dir, specs, log=print):
                return answer if list(specs) == [rb.F2] else world.run_e2e(project_dir, specs, log=log)

            try:
                m.run_e2e, m.build_frontend, m.log = run_e2e, world.build_frontend, lines.append
                got = f.salvage_foundation_break("REQ-9", before, [rb.OWN], {"failed": ["REQ-1-1-1.spec.js"]}, time.time() + 10000.0)
            finally:
                m.run_e2e, m.build_frontend, m.log = saved
            assert got == ([], "", ""), (got, lines)
            if expect:
                assert any("the regression run of the earlier specs fails without naming a spec" in l for l in lines), lines
            else:
                assert not any("Own.tsx" in l for l in lines), lines

            assert (root / rb.APP).read_text(encoding="utf-8") == "base " + rb.brk(rb.S1), lines
            assert (root / OWNF).read_text(encoding="utf-8") == "OWN-OK", lines

def _e2e_repair_script(seed: str) -> list[dict]:

    return [{"do": [("write", {rb.APP: "base OWN-OK", FSEED: seed})], "status": "passed"}, {"do": [("check",)]}]

@case
def the_e2e_repairs_foundation_put_back_tries_the_salvage_first():
    lines, after, world, results, f = run_world(_e2e_repair_script("rows " + rb.brk(rb.S1)))
    assert any("e2e gate FAILED" in l for l in lines), lines
    kept = [l for l in lines if "salvage KEPT: with frontend/src/data/seed.ts put back" in l]
    assert len(kept) == 1, lines
    assert not any("e2e repair ends red on regressions in the FOUNDATION specs" in l for l in lines), lines
    assert after == {"App.tsx": "base OWN-OK", "data/seed.ts": "rows alice"} and results == [True], (after, results)

    assert f.regression_debts == ["REQ-9"] and FSEED not in f.file_specs and rb.OWN in f.file_specs.get(rb.APP, []), f.file_specs
    assert any("its own spec is green, earlier non-foundation specs fail; the batch is kept" in l for l in lines), lines

@case
def switched_off_or_without_time_the_e2e_repairs_put_back_is_as_v146():
    off = run_world(_e2e_repair_script("rows " + rb.brk(rb.S1)), settings={"foundation_salvage": False})
    assert any("e2e repair ends red on regressions in the FOUNDATION specs; 2 file(s) put back" in l for l in off[0]), off[0]
    assert off[1] == {"App.tsx": "base", "data/seed.ts": "rows alice"} and off[3] == [False], off[1]
    assert not any("salvage" in l for l in off[0]), off[0]

    script = [{"do": [("write", {rb.APP: "base OWN-OK", FSEED: "rows " + rb.brk(rb.S1)})], "status": "passed"},
              {"do": [("check",), ("advance", 20000)]}]
    late = run_world(script)
    assert not any("salvage" in l for l in late[0]) and late[1] == off[1] and late[3] == [False], late[0]
    assert any("e2e repair ends red on regressions in the FOUNDATION specs; 2 file(s) put back" in l for l in late[0]), late[0]

AUTH = "frontend/src/routes/auth.js"
ABOUT = "frontend/src/AboutPage.tsx"
OWNF = "frontend/src/Own.tsx"

def with_auth(f):
    for rel, text in ((AUTH, "auth"), (ABOUT, "about")):
        (f.project_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (f.project_dir / rel).write_text(text, encoding="utf-8")
    f.file_specs[AUTH] = [rb.F1]

@case
def shared_data_first_then_the_foundation_batches_files_then_the_rest():
    script = [{"do": [("write", {ABOUT: "about new", AUTH: "auth " + rb.brk(rb.S1), OWNF: "OWN-OK", FSEED: "rows bob"}),
                      ("check",)]}]
    lines, after, world, results, f = run_world(script, prep=with_auth)
    tries = [l for l in lines if "salvage: with" in l or "salvage KEPT" in l]
    assert len(tries) == 2, tries
    assert "with frontend/src/data/seed.ts put back the foundation spec(s) still fail" in tries[0], tries
    assert "salvage KEPT: with frontend/src/routes/auth.js put back" in tries[1], tries
    assert after["routes/auth.js"] == "auth" and after["AboutPage.tsx"] == "about new" and results == [True], after

    script = [{"do": [("write", {ABOUT: "about new", AUTH: "auth " + rb.brk(rb.S1), OWNF: "OWN-OK"}), ("check",)]}]
    lines, after, world, results, f = run_world(script, prep=with_auth)
    tries = [l for l in lines if "salvage: with" in l or "salvage KEPT" in l]
    assert len(tries) == 1 and "salvage KEPT: with frontend/src/routes/auth.js put back" in tries[0], tries

@case
def source_shape_both_branches_go_through_salvage_first_and_keep_owing():
    import inspect

    import main as m

    body = inspect.getsource(m.Factory.process_node)
    assert body.count("salvage_first(deadline)") == 2, "the unit phase and the e2e repair"
    assert body.count("keep_owing()") == 3, "defined once, called by the unit phase and the e2e repair"
    assert body.count("self.salvage_foundation_break(") == 1
    salvage = inspect.getsource(m.Factory.salvage_foundation_break)
    assert "+ rest_need" in salvage and "self.regression_specs(batch_before, own_specs)" in salvage, salvage

@case
def a_try_whose_runs_pass_the_deadline_stops_and_keeps_nothing():

    durations = {"e2e": [(10.0, 1)], "build": [(1.0, 0)]}
    for advance, runs_expected, what in (
            (100.0, [[rb.F1], [rb.OWN], [rb.F2]], "(not kept: its checks ended past it)"),
            (250.0, [[rb.F1], [rb.OWN]], "(the regression run of the earlier specs does not start)"),
            (310.0, [[rb.F1]], "(the run of the batch's own spec(s) does not start)")):
        got, lines, runs, clock_end = direct("rows alice", durations, 300.0, advance=advance)
        assert got == ([], "", ""), (advance, got, lines)
        assert runs == runs_expected, (advance, runs)
        assert any("salvage stopped: the batch's deadline passed during the try with frontend/src/App.tsx put back "
                   + what in l for l in lines), (advance, lines)
        assert any("salvage found no state to keep (1 tried)" in l for l in lines), lines

    got, lines, runs, _ = direct("rows alice", durations, 300.0, build_advance=310.0)
    assert got == ([], "", "") and runs == [], (got, runs)
    assert any("(the run of the failing foundation spec(s) does not start)" in l for l in lines), lines

    groups = [(F3, [(10, [("x", 10, "REQ-2-1: Scenario 1")])])]
    got, lines, runs, _ = direct("rows alice", durations, 360.0, partial=groups, advance=120.0)
    assert got == ([], "", "") and runs == [[rb.F1], [rb.OWN], [rb.F2]], (got, runs)
    assert any("(the partial-green tests' runs do not start)" in l for l in lines), lines
    got, lines, runs, _ = direct("rows alice", durations, 361.0, partial=groups, advance=120.0)
    assert got[0] == ["REQ-1-1-1.spec.js"] and runs[-1] == [F3], (got, runs)

def _restart(advance: float, sources: str) -> list[dict]:
    import cases_v146a as v146a

    return v146a.restart_script(advance, sources)

def _phase_world(script, world_run, prep_more=None):
    import cases_v146a as v146a

    original = rb.World.run_e2e
    box: dict = {}

    def prep(f, offset, runs):
        box["offset"] = offset
        with_f3(f)
        if prep_more:
            prep_more(f, offset, runs)

    rb.World.run_e2e = lambda self, project_dir, specs, log=print: world_run(original, box["offset"], self, project_dir, specs)
    try:
        return v146a.run_world(script, nodes_left=10, prep=prep, green_run_advance=200)
    finally:
        rb.World.run_e2e = original

@case
def past_the_phase_end_the_unnamed_regression_is_not_run_again_the_check_is_unfinished():
    import cases_v146a as v146a
    from agent.verify import StepResult

    def world_run(original, offset, world, project_dir, specs):
        got = original(world, project_dir, specs)
        if len(specs) > 1 and "OWN-OK" in world.sources():
            offset[0] += 300.0
            return StepResult(False, "the server did not come up")
        return got

    v146a.CHECKED.clear()
    f, lines, seen, runs, passed = _phase_world(_restart(800, "base OWN-OK"), world_run)
    assert len(seen) == 2, lines
    deadline = v146a.offset_of(f, seen[1].options["deadline"])
    late = [(t, specs) for t, specs, text in runs if t >= deadline and "OWN-OK" in text]
    assert late == [], (deadline, late)
    assert not any("running 4 of its spec(s) once more" in l for l in lines), lines
    assert v146a.CHECKED and v146a.CHECKED[0].output.startswith("CHECK UNFINISHED"), v146a.CHECKED[0].output[:200]
    assert f.regression_debts == [] and any("in-batch restart ended budget; not kept" in l for l in lines), lines

@case
def past_the_phase_end_no_partial_green_run_starts_the_check_is_unfinished():
    import cases_v146a as v146a

    partial_runs: list = []
    P = "backend/test-e2e/REQ-3.spec.js"

    def prep_more(f, offset, runs):
        f.partial_green_groups = lambda before, own: [(P, [(10, [("x", 10, "REQ-3: works")])])]
        f.run_partial_tests = lambda spec, groups: partial_runs.append(offset[0]) or StepResultTrue()

    def world_run(original, offset, world, project_dir, specs):
        got = original(world, project_dir, specs)
        if len(specs) > 1 and "OWN-OK" in world.sources():
            offset[0] += 300.0
        return got

    v146a.CHECKED.clear()
    f, lines, seen, runs, passed = _phase_world(_restart(800, "base OWN-OK"), world_run, prep_more)
    assert len(seen) == 2, lines
    deadline = v146a.offset_of(f, seen[1].options["deadline"])
    assert all(t < deadline for t in partial_runs), (partial_runs, deadline)
    assert v146a.CHECKED and v146a.CHECKED[0].output.startswith("CHECK UNFINISHED"), v146a.CHECKED[0].output[:200]

    def quick(original, offset, world, project_dir, specs):
        return original(world, project_dir, specs)

    partial_runs.clear()
    v146a.CHECKED.clear()
    f, lines, seen, runs, passed = _phase_world(_restart(800, "base OWN-OK"), quick, prep_more)
    assert partial_runs and v146a.CHECKED and v146a.CHECKED[0].passed, (partial_runs, v146a.CHECKED[0].output[:200])

def StepResultTrue():
    from agent.verify import StepResult

    return StepResult(True, "1 passed")

@case
def past_the_phase_end_a_red_checks_foundation_runs_do_not_start():
    import cases_v146a as v146a

    def world_run(original, offset, world, project_dir, specs):
        got = original(world, project_dir, specs)
        if list(specs) == [rb.OWN] and not got.ok and "restart red" in world.sources():
            offset[0] += 250.0
        return got

    v146a.CHECKED.clear()
    f, lines, seen, runs, passed = _phase_world(_restart(1000, "base restart red " + rb.brk(rb.S1)), world_run)
    assert len(seen) == 2, lines
    deadline = v146a.offset_of(f, seen[1].options["deadline"])

    late = [(t, specs) for t, specs, text in runs if t >= deadline and "restart red" in text]
    assert late == [], (deadline, late)
    assert any("the in-batch restart's end has passed; its foundation run does not start past it" in l for l in lines), lines
    assert v146a.CHECKED and not v146a.CHECKED[0].passed and "FOUNDATION" not in v146a.CHECKED[0].output, v146a.CHECKED[0].output[-400:]

@case
def direct_the_follow_up_entries_honour_a_passed_phase_end_and_nothing_else_changes():
    import main as m
    from agent import clock
    from agent.verify import StepResult

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = rb.make_factory(root, "base")
        calls: list = []
        saved = (m.run_e2e, m.log)
        try:
            m.run_e2e = lambda project_dir, specs, log=print: calls.append(list(specs)) or StepResult(True, "ok")
            m.log = lambda line: None
            run = StepResult(False, "the server did not come up")
            past, future = {"end": clock.now() - 1, "name": "x"}, {"end": clock.now() + 1000, "name": "x"}
            assert f.settle_unnamed_regression("R", [rb.F1], run, phase_end=past) == (run, None) and calls == []
            assert f.settle_unnamed_regression("R", [rb.F1], run, phase_end=future)[1] is False and calls == [[rb.F1]]
            assert f.settle_unnamed_regression("R", [rb.F1], run)[1] is False and len(calls) == 2
            f.run_partial_tests = lambda spec, groups: calls.append(["partial", spec]) or StepResult(True, "ok")
            groups = [(F3, [(10, [])])]
            assert f.with_partial_green(StepResult(True, "ok"), groups, phase_end=past) is None and len(calls) == 2
            assert f.with_partial_green(StepResult(True, "ok"), groups, phase_end=future).ok and calls[-1] == ["partial", F3]
            assert f.with_partial_green(StepResult(True, "ok"), groups).ok and len(calls) == 4
        finally:
            m.run_e2e, m.log = saved

@case
def source_shape_the_check_returns_unfinished_after_each_follow_up_entry():
    import inspect

    import main as m

    body = inspect.getsource(m.Factory.process_node)
    assert "self.settle_unnamed_regression(req_id, regression, again, phase_end=phase_end)" in body
    assert "if inconclusive is None:" in body and "if with_partial is None:" in body
    assert body.count("return self.phase_unfinished(req_id, phase_end,") == 3
    assert "e2e.output, phase_end=phase_end)" in body
    note = inspect.getsource(m.Factory.foundation_note_while_red)
    assert note.count("self.foundation_run_past_end(req_id, phase_end,") == 4, note

import cases_v146a as v146a

I5_HEAD = "PREREQUISITE NOT DELIVERED"
I5_DEP = {"id": "REQ-5", "name": "Create a team", "members": ["REQ-5"]}
I5_OTHER = {"id": "REQ-7", "name": "Unrelated page", "members": ["REQ-7"]}
I5_NINE = dict(v145a.NODE)
I5_LEAVES = [{"id": "REQ-5", "name": "Create a team"},
          {"id": "REQ-7", "name": "Unrelated page"},
          {"id": "REQ-9", "name": "Batch nine", "dependencies": ["REQ-5"]}]
I5_PUT_BACK = {"do": [("write", {rb.APP: "base " + rb.brk(rb.S1)}), ("check",)]}
I5_KEPT_RED = {"do": [("write", {rb.APP: "base feature"}), ("check",)]}
I5_GREEN = {"do": [("write", {rb.APP: "base OWN-OK"}), ("check",)]}
I5_RED = {"do": [("check",)], "status": "budget"}
I5_DEFECT = "The spec expects 'Save' but the requirement says 'Store' (quoted)"

def i5_run(batches: list, script: list[dict], leaves=I5_LEAVES, settings: dict | None = None, prep=None,
           time_budget: float = 10800.0):
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
    base = v146a.fake_class(script, seen, offset)

    class Fake(base):
        def implement(self, node, tests, outcome, extra_context=""):
            self.node_id = str(node.get("id"))
            return super().implement(node, tests, outcome, extra_context=extra_context)

    results = []
    with v145a.Settings(audit_enabled=False, **(settings or {})), tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = rb.World(root)
        f = rb.make_factory(root, "base", time_budget)
        try:
            frozen = time.time()
            clock.now = lambda: frozen + offset[0]
            f.started = frozen
            factory_module.run_e2e, factory_module.build_frontend = world.run_e2e, world.build_frontend
            factory_module.Implementer = Fake
            factory_module.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
            factory_module.static_test_defects = lambda project_dir, files, **kw: ""
            factory_module.parse_defects_in_output = lambda output, files: ""
            factory_module.lint_application = lambda project_dir, native: ""
            factory_module.log = lines.append
            if leaves is not None:
                f.index_leaves([dict(leaf) for leaf in leaves])
            if prep:
                prep(f)
            for node, kwargs in batches:
                ok = f.process_node(dict(node), 1, **kwargs)
                results.append(ok)
                if not ok:
                    f.failed_members.extend(m for m in node["members"] if m not in f.failed_members)
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
            clock.now = saved_now
    assert not script, f"scripted steps left over: {script}"
    return f, lines, seen, results

def i5_of(seen: list, node_id: str) -> list:
    return [s for s in seen if getattr(s, "node_id", "") == node_id]

def i5_inputs(implementer) -> tuple:
    return (implementer.saw_context, dict(implementer.saw), implementer.saw_app)

@case
def a_put_back_dependency_reaches_the_first_implementer_as_extra_context():
    f, lines, seen, results = i5_run([(I5_DEP, {}), (I5_NINE, {})], [dict(I5_PUT_BACK), dict(I5_RED)],
                                 settings={"in_batch_restart": False})
    assert results == [False, False] and rb.put_back(lines), lines
    entry = f.report["nodes"]["REQ-5"]
    assert entry["put_back_members"] == ["REQ-5"] and entry["written"] == [] and entry["status"] != "passed", entry
    assert "put_back_members" not in f.report["nodes"]["REQ-9"], f.report["nodes"]["REQ-9"]
    (dep,), (nine,) = i5_of(seen, "REQ-5"), i5_of(seen, "REQ-9")
    assert dep.saw_context == "", dep.saw_context
    context = nine.saw_context
    assert context.startswith(I5_HEAD + ": this batch's requirement depends on REQ-5 (Create a team)."), context
    assert "put back to the state before it; none of its changes were kept" in context, context
    assert "Build now, minimally, only the parts this batch's tests need and the sources lack" in context, context
    assert "rely on in the files their batches wrote (frontend/src/App.tsx)" in context, context
    assert nine.saw == {}, nine.saw
    assert any("REQ-9: prerequisite(s) put back with nothing kept: ['REQ-5']" in l for l in lines), lines

@case
def a_retry_implementer_gets_the_note_after_its_attempt_note():
    f, lines, seen, results = i5_run([(I5_DEP, {}), (I5_NINE, {}), (I5_NINE, {"retry": True})],
                                 [dict(I5_PUT_BACK), dict(I5_KEPT_RED), dict(I5_RED)], settings={"in_batch_restart": False})
    first, retry = i5_of(seen, "REQ-9")
    assert I5_HEAD in first.saw_context and "attempt_note" not in first.saw, (first.saw_context, first.saw)
    note = retry.saw.get("attempt_note") or ""
    assert note.startswith("PREVIOUS ATTEMPT AT THIS BATCH"), note
    assert (NL + NL + I5_HEAD + ": this batch's requirement depends on REQ-5 (Create a team).") in note, note
    assert retry.saw_context == "", retry.saw_context

@case
def an_in_batch_restart_implementer_gets_the_note_after_its_attempt_note():
    stuck = {"do": [("write", {rb.APP: "base a"}), ("check",)], "stop_reason": "stuck"}
    f, lines, seen, results = i5_run([(I5_DEP, {}), (I5_NINE, {})], [dict(I5_PUT_BACK), stuck, dict(I5_RED)])
    assert any("(in-batch restart)" in l for l in lines), lines
    first, restart = i5_of(seen, "REQ-9")
    assert I5_HEAD in first.saw_context, first.saw_context
    note = restart.saw.get("attempt_note") or ""
    assert note.startswith("PREVIOUS ATTEMPT AT THIS BATCH") and "How it ended: budget (stuck)" in note, note
    assert (NL + NL + I5_HEAD + ": this batch's requirement depends on REQ-5 (Create a team).") in note, note

@case
def the_implementer_after_a_test_repair_keeps_the_note_in_its_context():
    calls: list = []
    defect = {"do": [("write", {rb.APP: "base x"}), ("check",)], "status": "test_defect", "detail": I5_DEFECT}

    def prep(f):
        def repair(node, prefix, files, problem, contract=""):
            calls.append(problem)
            return files
        f.repair_tests = repair
    f, lines, seen, results = i5_run([(I5_DEP, {}), (I5_NINE, {})], [dict(I5_PUT_BACK), defect, dict(I5_RED)],
                                 settings={"in_batch_restart": False}, prep=prep)
    assert calls == [I5_DEFECT], calls
    first, after = i5_of(seen, "REQ-9")
    assert after.saw_context.startswith("(The test author just repaired the tests;"), after.saw_context
    assert after.saw_context.endswith(NL + NL + first.saw_context) and first.saw_context.startswith(I5_HEAD), after.saw_context

@case
def a_delivered_dependency_gives_no_note_and_the_same_inputs_as_without_dependencies():

    runs = []
    for leaves in (I5_LEAVES, None):
        f, lines, seen, results = i5_run([(I5_DEP, {}), (I5_NINE, {}), (I5_NINE, {"retry": True})],
                                     [dict(I5_GREEN), dict(I5_KEPT_RED), dict(I5_RED)], leaves=leaves,
                                     settings={"in_batch_restart": False})
        assert results == [True, False, False], results
        assert not any("prerequisite" in l for l in lines), lines
        runs.append([(s.node_id,) + i5_inputs(s) for s in seen])
    assert runs[0] == runs[1], runs
    nine_first = [r for r in runs[0] if r[0] == "REQ-9"][0]
    assert nine_first[1] == "" and nine_first[2] == {}, nine_first

@case
def a_dependency_kept_red_gives_no_note():

    f, lines, seen, results = i5_run([(I5_DEP, {}), (I5_NINE, {})], [dict(I5_KEPT_RED), dict(I5_RED)],
                                 settings={"in_batch_restart": False})
    assert results == [False, False] and not rb.put_back(lines), lines
    assert "put_back_members" not in f.report["nodes"]["REQ-5"] and f.report["nodes"]["REQ-5"]["written"], f.report["nodes"]
    assert i5_of(seen, "REQ-9")[0].saw_context == "" and i5_of(seen, "REQ-9")[0].saw == {}

@case
def a_put_back_batch_that_is_no_dependency_gives_no_note():
    f, lines, seen, results = i5_run([(I5_OTHER, {}), (I5_NINE, {})], [dict(I5_PUT_BACK), dict(I5_RED)],
                                 settings={"in_batch_restart": False})
    assert f.report["nodes"]["REQ-7"].get("put_back_members") == ["REQ-7"], f.report["nodes"]["REQ-7"]
    (nine,) = i5_of(seen, "REQ-9")
    assert nine.saw_context == "" and nine.saw == {}, (nine.saw_context, nine.saw)

@case
def a_put_back_leaf_not_booked_failed_or_an_entry_green_or_with_files_gives_no_note():
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.index_leaves([dict(leaf) for leaf in I5_LEAVES])
    f.green_specs, f.file_specs = [], {}
    put_back = {"status": "budget", "written": [], "put_back_members": ["REQ-5"]}
    for entry, failed, noted in ((put_back, ["REQ-5"], True),
                                 (put_back, [], False),
                                 ({**put_back, "status": "passed"}, ["REQ-5"], False),
                                 ({**put_back, "written": [rb.APP]}, ["REQ-5"], False),
                                 ({"status": "budget", "written": []}, ["REQ-5"], False)):
        f.report, f.failed_members = {"nodes": {"REQ-5": dict(entry)}}, list(failed)
        note = f.prerequisite_note(dict(I5_NINE))
        assert bool(note) is noted, (entry, failed, note)
        assert f.undelivered_prerequisites(dict(I5_NINE)) == (["REQ-5"] if noted else []), (entry, failed)

@case
def a_prerequisite_is_found_transitively_and_the_batchs_own_members_are_never_named():
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.index_leaves([{"id": "REQ-1", "name": "Base"}, {"id": "REQ-2", "name": "Middle", "dependencies": ["REQ-1"]},
                    {"id": "REQ-3-1", "name": "Top a", "dependencies": ["REQ-2"]},
                    {"id": "REQ-3-2", "name": "Top b", "dependencies": ["REQ-3-1"]}])
    assert f.leaf_prerequisites["REQ-3-2"] == ["REQ-1", "REQ-2", "REQ-3-1"], f.leaf_prerequisites
    assert f.leaf_dependents["REQ-1"] == 3 and f.leaf_names["REQ-2"] == "Middle", (f.leaf_dependents, f.leaf_names)
    f.green_specs, f.file_specs = [], {}

    f.report = {"nodes": {"REQ-1": {"status": "budget", "written": [], "put_back_members": ["REQ-1"]},
                          "REQ-2": {"status": "passed", "written": [rb.APP]},
                          "REQ-3": {"status": "budget", "written": [], "put_back_members": ["REQ-3-1", "REQ-3-2"]}}}
    f.failed_members = ["REQ-1", "REQ-3-1", "REQ-3-2"]
    batch = {"id": "REQ-3", "name": "Top", "members": ["REQ-3-1", "REQ-3-2"]}
    assert f.undelivered_prerequisites(batch) == ["REQ-1"], f.undelivered_prerequisites(batch)
    note = f.prerequisite_note(batch)
    assert note.startswith(I5_HEAD + ": this batch's requirement depends on REQ-1 (Base).") and "REQ-3-1" not in note, note
    assert note.endswith("without changing what the specs of earlier requirements rely on."), note

@case
def at_most_three_prerequisites_are_named_in_run_order_and_the_note_is_capped():
    import main as m

    f = m.Factory.__new__(m.Factory)
    deps = [f"REQ-{i}" for i in (6, 2, 4, 1, 3)]
    f.index_leaves([{"id": d, "name": "N" * 300} for d in deps] + [{"id": "REQ-9", "name": "Nine", "dependencies": deps}])
    f.report = {"nodes": {d: {"status": "gave_up", "written": [], "put_back_members": [d]} for d in deps}}
    f.failed_members = list(deps)
    f.green_specs = [rb.F1, rb.F2, "backend/test-e2e/REQ-1-2.spec.js"]
    f.file_specs = {f"frontend/src/F{i:02d}.tsx": [rb.F1] for i in range(12)}
    f.file_specs["frontend/src/Late.tsx"] = ["backend/test-e2e/REQ-5-5.spec.js"]
    note = f.prerequisite_note(dict(I5_NINE))
    assert note.startswith("PREREQUISITES NOT DELIVERED: this batch's requirement depends on REQ-6 (" + "N" * 80 + "), REQ-2 ("), note
    assert "REQ-4 (" in note and "REQ-1 (" not in note and " and 2 more. The batches that implemented them" in note, note
    assert "frontend/src/F07.tsx, ...)" in note and "F08" not in note and "Late.tsx" not in note, note
    assert len(note) <= m.Factory.PREREQUISITE_NOTE_CAP, len(note)

    f.file_specs = {f"frontend/src/pages/{'Deep' * 25}{i:02d}.tsx": [rb.F1] for i in range(12)}
    note = f.prerequisite_note(dict(I5_NINE))
    assert len(note) <= m.Factory.PREREQUISITE_NOTE_CAP and note.startswith("PREREQUISITES NOT DELIVERED"), len(note)
    assert note.endswith("): add to those files; do not rewrite or remove what is there.") and "Deep" in note, note[-300:]

@case
def a_long_attempt_note_is_cut_so_the_prerequisite_note_survives_the_implementers_cap():
    import main as m
    from agent.codegen import ATTEMPT_NOTE_CHARS

    prerequisite = I5_HEAD + ": this batch's requirement depends on REQ-5."
    joined = m.Factory.with_prerequisite_note("PREVIOUS ATTEMPT " + "x" * 6000, prerequisite)
    assert len(joined) <= ATTEMPT_NOTE_CHARS and joined.endswith(NL + NL + prerequisite), len(joined)
    assert m.Factory.with_prerequisite_note("note", "") == "note" and m.Factory.with_prerequisite_note("", prerequisite) == prerequisite

R54_R12 = "backend/test-e2e/REQ-1-2.spec.js"
R54_F4 = "backend/test-e2e/REQ-2-2.spec.js"

def r54_red(failed: list[str], not_run: list[str]):
    from agent.verify import StepResult

    body = NL.join(f"## {name}{NL}{rb.spec_output(name, [name.replace('.spec.js', '') + ': Scenario 1'])}" for name in failed)
    return StepResult(False, "FAILED SPEC FILES: " + ", ".join(failed) + NL + "NOT RUN SPEC FILES: " + ", ".join(not_run) + NL
                      + f"1 of 3 spec files passed, each run against fresh data{NL}{len(not_run)} spec file(s) not run: the time "
                      "for this check is used up" + NL + body)

def r54_salvage(green: list[str], rest_answer, alone_red: bool = False, rest_advance: float = 0.0, deadline_in: float = 10000.0):
    import main as m
    from agent import clock
    from agent.verify import StepResult

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = rb.World(root)
        f = rb.make_factory(root, "base")
        f.green_specs = list(green)
        (root / FSEED).parent.mkdir(parents=True, exist_ok=True)
        before = {rb.APP: b"base", "backend/src/server.js": b"// server", FSEED: b"rows alice"}
        (root / rb.APP).write_text("base " + rb.brk(rb.S1), encoding="utf-8")
        (root / FSEED).write_text("rows alice", encoding="utf-8")
        (root / OWNF).write_text("OWN-OK", encoding="utf-8")
        f.durations = {"e2e": [(10.0, 1)], "build": [(1.0, 0)]}
        offset = [0.0]
        runs: list = []
        lines: list[str] = []
        saved = (m.run_e2e, m.build_frontend, m.log, clock.now)
        frozen = time.time()

        def run_e2e(project_dir, specs, log=print):
            runs.append(list(specs))
            if len(specs) > 1 and F3 in specs:
                offset[0] += rest_advance
                return rest_answer
            if list(specs) == [rb.F2] and alone_red:
                return StepResult(False, rb.spec_output("REQ-1-1-3.spec.js", ["REQ-1-1-3: Scenario 1"]))
            return world.run_e2e(project_dir, specs, log=log)

        try:
            clock.now = lambda: frozen + offset[0]
            m.run_e2e, m.build_frontend, m.log = run_e2e, world.build_frontend, lines.append
            got = f.salvage_foundation_break("REQ-9", before, [rb.OWN], {"failed": ["REQ-1-1-1.spec.js"]}, frozen + deadline_in)
            on_disk = (root / rb.APP).read_text(encoding="utf-8")
        finally:
            m.run_e2e, m.build_frontend, m.log, clock.now = saved
        return got, lines, runs, on_disk

@case
def r54_1_a_foundation_spec_the_regression_run_did_not_run_runs_before_a_salvage_is_kept():
    green = [rb.F1, rb.F2, R54_R12, F3]
    answer = r54_red(["REQ-2-1.spec.js"], ["REQ-1-1-3.spec.js"])
    got, lines, runs, _ = r54_salvage(green, answer)

    assert [rb.F2] in runs and runs.index([rb.F2]) > max(i for i, r in enumerate(runs) if F3 in r), runs
    assert got[0] == ["REQ-1-1-1.spec.js"] and got[2]["failed"] == ["REQ-2-1.spec.js"] and got[2]["unknown"] == [], (got, lines)
    kept = [l for l in lines if "salvage KEPT" in l]
    assert kept and "1 foundation spec(s) it did not run passed on their own run" in kept[0], lines

    got, lines, runs, on_disk = r54_salvage(green, answer, alone_red=True)
    assert got == ([], "", "") and not any("salvage KEPT" in l for l in lines), (got, lines)
    assert any("the foundation spec(s) the regression run did not run ['REQ-1-1-3.spec.js'] do not pass" in l for l in lines), lines
    assert on_disk == "base " + rb.brk(rb.S1), on_disk

    got, lines, runs, on_disk = r54_salvage(green, answer, rest_advance=9950.0)
    assert got == ([], "", "") and [rb.F2] not in runs, (got, runs)
    assert any("did not run foundation spec(s) ['REQ-1-1-3.spec.js'] and there is no time to run them" in l for l in lines), lines
    assert on_disk == "base " + rb.brk(rb.S1), on_disk

@case
def r54_1_non_foundation_specs_the_salvages_run_did_not_run_are_owed_as_unknown():
    green = [rb.F1, rb.F2, R54_R12, F3, R54_F4]
    got, lines, runs, _ = r54_salvage(green, r54_red(["REQ-2-1.spec.js"], ["REQ-2-2.spec.js"]))
    assert got[0] == ["REQ-1-1-1.spec.js"] and got[2]["unknown"] == [R54_F4], (got, lines)
    assert got[2]["failed"] == ["REQ-2-1.spec.js"] and [R54_F4] not in runs, runs
    assert any("1 spec(s) it did not run are owed as an unknown debt" in l for l in lines), lines

@case
def r54_1_the_ordinary_check_keeps_no_best_state_on_an_unrun_foundation_spec_and_owes_the_unrun_specs():

    original = rb.World.run_e2e
    unrun_answer = r54_red(["REQ-2-1.spec.js"], ["REQ-1-1-1.spec.js"])

    def patched(self, project_dir, specs, log=print):
        if len(specs) > 1 and "OWN-OK" in self.sources():
            self.runs.append((list(specs), self.sources()))
            return unrun_answer
        return original(self, project_dir, specs, log=log)

    rb.World.run_e2e = patched
    try:
        lines, after, world, results, f = run_world([{"do": [("write", {rb.APP: "base OWN-OK"}), ("check",)]}, {"do": []}],
                                                    prep=with_f3)
    finally:
        rb.World.run_e2e = original
    assert not any("is the batch's best state so far" in l for l in lines), [l for l in lines if "best state" in l]
    assert results == [True] and f.regression_debts == ["REQ-9"], (results, lines)
    assert (f.__dict__.get("unknown_debts") or {}).get("REQ-9") == [rb.F1], (f.__dict__.get("unknown_debts"), lines)
    assert any("owes an UNKNOWN regression (specs its regression run did not run); 1 candidate" in l for l in lines), lines
    assert F3 not in f.green_specs and rb.F1 in f.green_specs, f.green_specs

def r54_phase_factory(root: Path):
    f = rb.make_factory(root, "base")
    (root / rb.APP).write_text("base now", encoding="utf-8")
    return f, {rb.APP: b"base", "backend/src/server.js": b"// server"}

@case
def r54_2_no_run_alone_starts_past_the_phase_end():
    import main as m
    from agent import clock
    from agent.verify import StepResult

    with tempfile.TemporaryDirectory() as tmp:
        f, _ = r54_phase_factory(Path(tmp))
        offset, calls, lines = [0.0], [], []
        saved = (m.run_e2e, m.log, clock.now)
        frozen = time.time()

        def run_e2e(project_dir, specs, log=print):
            calls.append(list(specs))
            offset[0] += 500.0
            return StepResult(False, rb.spec_output(Path(specs[0]).name, ["x"]))

        try:
            clock.now = lambda: frozen + offset[0]
            m.run_e2e, m.log = run_e2e, lines.append
            named = {"REQ-1-1-1.spec.js", "REQ-1-1-3.spec.js"}
            got = f.failing_alone("R", [rb.F1, rb.F2], named, phase_end={"end": frozen + 100.0, "name": "unit phase"})
            assert got is None and calls == [[rb.F1]], (got, calls)
            assert any("the unit phase's end has passed; the run alone of REQ-1-1-3.spec.js does not start" in l for l in lines), lines
            calls.clear()
            got = f.failing_alone("R", [rb.F1, rb.F2], named)
            assert calls == [[rb.F1], [rb.F2]] and [name for name, _ in got] == ["REQ-1-1-1.spec.js", "REQ-1-1-3.spec.js"], got
        finally:
            m.run_e2e, m.log, clock.now = saved

@case
def r54_2_the_foundation_baseline_stops_at_the_phase_end_is_unknown_and_still_puts_the_tree_back():
    import main as m
    from agent import clock
    from agent.verify import StepResult

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f, before = r54_phase_factory(root)
        offset, calls, lines = [0.0], [], []
        saved = (m.run_e2e, m.build_frontend, m.log, clock.now)
        frozen = time.time()
        f.started = frozen

        def run_e2e(project_dir, specs, log=print):
            calls.append(list(specs))
            offset[0] += 500.0
            return StepResult(True, "1 passed")

        try:
            clock.now = lambda: frozen + offset[0]
            m.run_e2e, m.build_frontend, m.log = run_e2e, lambda project_dir, log=print: StepResult(True, ""), lines.append
            got = f.foundation_baseline("R", before, phase_end={"end": frozen + 100.0, "name": "unit phase"})
            assert got is None and calls == [[rb.F1]], (got, calls)
            assert (root / rb.APP).read_text(encoding="utf-8") == "base now", "the batch's state is back"
            assert not f.foundation_baselines, f.foundation_baselines
            calls.clear()
            got = f.foundation_baseline("R", before)
            assert got == {} and calls == [[rb.F1], [rb.F2]] and len(f.foundation_baselines) == 1, (got, calls)
        finally:
            m.run_e2e, m.build_frontend, m.log, clock.now = saved

@case
def source_shape_r54_2_the_phase_end_reaches_the_runs_alone_and_the_baseline():
    import inspect

    import main as m

    note = inspect.getsource(m.Factory.foundation_note_while_red)
    assert "self.failing_alone(req_id, foundation, named, phase_end=phase_end) if phase_end is not None" in note
    assert "self.new_foundation_failures(req_id, before, dict(confirmed), phase_end=phase_end) if phase_end is not None" in note
    body = inspect.getsource(m.Factory.new_foundation_failures)
    assert "self.foundation_baseline(req_id, batch_before, phase_end=phase_end) if phase_end is not None" in body

@case
def r54_5_an_incomplete_put_back_keeps_its_files_booked_and_gives_no_not_delivered_note():
    import main as m

    team = "frontend/src/Team.tsx"
    original = m.source_io.put_back_all

    def failing_delete(items, late_deletes=None):
        items = list(items)
        stuck = [path for path, content in items if content is None and Path(path).name == "Team.tsx"]
        late = (lambda: [p for p in late_deletes() if Path(p).name != "Team.tsx"]) if late_deletes else None
        changed, failed = original([(p, c) for p, c in items if not (c is None and Path(p).name == "Team.tsx")], late_deletes=late)
        return changed, list(failed) + stuck

    put_back = {"do": [("write", {rb.APP: "base " + rb.brk(rb.S1), team: "team"}), ("check",)]}
    m.source_io.put_back_all = failing_delete
    try:
        f, lines, seen, results = i5_run([(I5_DEP, {}), (I5_NINE, {})], [put_back, dict(I5_RED)], settings={"in_batch_restart": False})
    finally:
        m.source_io.put_back_all = original
    entry = f.report["nodes"]["REQ-5"]
    assert results == [False, False] and any("did not reproduce the snapshot" in l for l in lines), lines
    assert "put_back_members" not in entry and entry["put_back_incomplete"] == [team] and entry["written"] == [team], entry
    assert any("REQ-5: the put-back was INCOMPLETE -- 1 file(s) still differ" in l for l in lines), lines
    (nine,) = i5_of(seen, "REQ-9")
    assert I5_HEAD not in nine.saw_context and nine.saw_context == "", nine.saw_context
    assert not any("prerequisite(s) put back with nothing kept" in l for l in lines), lines

@case
def r54_5_a_red_retry_whose_put_back_is_incomplete_unmarks_the_first_attempts_put_back():
    import cases_closing as cl
    import main as m

    extra = "frontend/src/Extra.tsx"
    original = m.source_io.put_back_all

    def failing_delete(items, late_deletes=None):
        items = list(items)
        stuck = [path for path, content in items if content is None and Path(path).name == "Extra.tsx"]
        late = (lambda: [p for p in late_deletes() if Path(p).name != "Extra.tsx"]) if late_deletes else None
        changed, failed = original([(p, c) for p, c in items if not (c is None and Path(p).name == "Extra.tsx")], late_deletes=late)
        return changed, list(failed) + stuck

    for broken in (False, True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = rb.make_factory(root, "template")
            f.green_specs, f.e2e_specs, f.file_specs = [], [], {}
            f.description_pass, f.description_specs, f.llm = {}, [], None
            f.over_budget = lambda: False
            f.time_left = lambda: 10 ** 6
            f.GATEWAY_PAUSE_SEC = 0.0

            def process_node(node, nodes_left, retry=False):
                req = node["id"]
                f.current_members = [req]
                if retry:
                    (root / rb.APP).write_text("worse", encoding="utf-8")
                    (root / extra).write_text("extra", encoding="utf-8")
                    f.report["nodes"][req] = {"status": "budget", "e2e": "failed", "tests": ["backend/test-e2e/A.spec.js"], "written": [rb.APP, extra], "test_runs": 1}
                else:
                    f.report["nodes"][req] = {"status": "budget", "e2e": "failed", "tests": ["backend/test-e2e/A.spec.js"], "written": [], "test_runs": 1,
                                              "put_back_members": [req]}
                return False

            f.process_node = process_node
            if broken:
                m.source_io.put_back_all = failing_delete
            try:
                result, lines = cl._queue(f, "A")
            finally:
                m.source_io.put_back_all = original
            entry = f.report["nodes"]["A"]
            assert result is False and f.foundation_retried == ["A"], lines
            if not broken:
                assert entry["put_back_members"] == ["A"] and entry["written"] == [] and "put_back_incomplete" not in entry, entry
                continue
            assert (root / extra).is_file() and (root / rb.APP).read_text(encoding="utf-8") == "template", lines
            assert "put_back_members" not in entry and entry["put_back_incomplete"] == [extra] and entry["written"] == [extra], entry

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
    print(f"{len(CASES) - failed}/{len(CASES)} v147a cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
