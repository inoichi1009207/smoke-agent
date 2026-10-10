from __future__ import annotations

import inspect
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_engine142 as e142
import cases_p141s3 as s3
import cases_rollback as rb
import cases_v144m as vm

NL = chr(10)
CASES = []
Settings = e142.Settings
F3, F4 = s3.F3, s3.F4
F1_NAME = Path(rb.F1).name
F4_NAME = Path(F4).name

def case(function):
    CASES.append(function)
    return function

@case
def old_foundation_failures_the_baseline_let_off_are_no_net_loss():

    both = rb.brk(rb.S1) + " " + rb.brk(rb.S2)
    f, lines, after, passed, calls = s3.run_debt_batch(
        [{"write": {rb.APP: "OWN-OK " + both}}, {"write": {rb.APP: "OWN-OK other " + both}}],
        patch={"run_e2e": vm.counting_run_e2e}, app_before="APP " + both)
    assert any("pre-existing foundation failure" in l for l in lines), lines
    assert not any("put back: its own spec" in l for l in lines), lines
    assert passed and after == "OWN-OK " + both, (after, lines)

@case
def the_net_loss_counts_only_tests_not_let_off():
    with tempfile.TemporaryDirectory() as tmp:
        f = rb.make_factory(Path(tmp), "APP")
        evidence = f.debt_evidence(rb.spec_output(F1_NAME, [rb.S1, rb.S2]))
        assert f.debt_net_loss(1, {F1_NAME}, evidence) == (2, 1)
        assert f.debt_net_loss(1, {F1_NAME}, evidence, {F1_NAME: [rb.S1, rb.S2]}) == (0, 1)
        assert f.debt_net_loss(1, {F1_NAME}, evidence, {F1_NAME: [rb.S1]}) == (1, 1)

        state = {"failed": [F1_NAME], "output": rb.spec_output(F1_NAME, [rb.S1, rb.S2])}
        f.green_specs = [rb.F1, rb.F2]
        f.new_foundation_failures = lambda req_id, before, failing, let_off=None: (
            let_off.update({F1_NAME: [rb.S1]}) if let_off is not None else None) or [F1_NAME]
        assert f.foundation_regression_is_new("REQ-9", {"x": b""}, state) is True and state["preexisting"] == {F1_NAME: [rb.S1]}

@case
def new_foundation_failures_reports_the_tests_it_let_off():
    with tempfile.TemporaryDirectory() as tmp:
        f = rb.make_factory(Path(tmp), "APP " + rb.brk(rb.S1))
        before = f.snapshot_sources()
        (Path(tmp) / rb.APP).write_text("APP changed " + rb.brk(rb.S1) + " " + rb.brk(rb.S2), encoding="utf-8")
        import main as m

        saved = (m.run_e2e, m.build_frontend, m.log)
        world = rb.World(Path(tmp))
        try:
            m.run_e2e, m.build_frontend, m.log = world.run_e2e, world.build_frontend, (lambda *a, **k: None)
            let_off: dict = {}
            new = f.new_foundation_failures("REQ-9", before, {F1_NAME: rb.spec_output(F1_NAME, [rb.S1, rb.S2])}, let_off)
        finally:
            m.run_e2e, m.build_frontend, m.log = saved
        assert new == [F1_NAME] and let_off == {F1_NAME: [rb.S1]}, (new, let_off)

def three_test_f4(root: Path) -> None:
    (root / F4).parent.mkdir(parents=True, exist_ok=True)
    (root / F4).write_text("".join(f"test('REQ-4-2-3: {t}', async () => {{}});{NL}" for t in ("A", "B", "C")), encoding="utf-8")

@case
def a_second_booking_of_the_same_spec_keeps_the_first_ones_red_tests_out():
    from agent import verify

    saved = verify.test_groups
    try:
        verify.test_groups = vm.fake_groups
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = rb.make_factory(root, "APP")
            f.e2e_specs, f.partial_green = [rb.F1, rb.F2, F4], {}
            f.file_specs = {rb.APP: [F4]}
            three_test_f4(root)
            A, B, C = "REQ-4-2-3: A", "REQ-4-2-3: B", "REQ-4-2-3: C"
            f.track_debt_specs("REQ-9", {F4_NAME}, f.debt_evidence(rb.spec_output(F4_NAME, [A])))
            assert f.partial_green[F4]["red"] == [A], f.partial_green

            at_start = dict(f.partial_green)
            f.drop_partial_green({F4_NAME}, "REQ-10")
            f.track_debt_specs("REQ-10", {F4_NAME}, f.debt_evidence(rb.spec_output(F4_NAME, [B])), at_start)
            assert f.partial_green[F4]["red"] == [A, B], f.partial_green
            before = f.snapshot_sources()
            (root / rb.APP).write_text("APP later", encoding="utf-8")
            assert f.partial_green_groups(before, [rb.OWN]) == [(F4, [(3, [(3, 1, C)])])]

            at_start = dict(f.partial_green)
            f.drop_partial_green({F4_NAME}, "REQ-11")
            (root / F4).write_text((root / F4).read_text(encoding="utf-8") + "// v2" + NL, encoding="utf-8")
            f.track_debt_specs("REQ-11", {F4_NAME}, f.debt_evidence(rb.spec_output(F4_NAME, [C])), at_start)
            assert f.partial_green[F4]["red"] == [C], f.partial_green
    finally:
        verify.test_groups = saved

class StopAfterFirst:
    factory = None
    calls: list = []

    def __init__(self, llm, project_dir, check, **options):
        self.check = check

    def implement(self, node, tests, outcome, extra_context=""):
        from agent.codegen import ImplementResult

        StopAfterFirst.calls.append(node.get("id"))
        StopAfterFirst.factory.closing_repair_min_sec = lambda: 1e9
        return ImplementResult("budget", [], "scripted", 1)

@case
def an_owed_spec_the_first_job_never_reached_is_not_booked_as_tried():
    lines: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = vm.closing_factory(root, "APP " + s3.F4_BREAK + " " + rb.brk("REQ-1-2: Scenario 1"))
        f.e2e_specs = [rb.F1, rb.F2, F3, F4]
        (root / F3).write_text("test('x', async () => {});" + NL, encoding="utf-8")
        f.spec_context[F3] = {"node": {"id": "REQ-1-2"}, "contract": "", "relevant": [], "tests": {}, "written": [], "members": []}
        f.debt_specs = {F3: {"by": "REQ-9", "failed": []}, F4: {"by": "REQ-9", "failed": []}}
        vm.measured(f)
        StopAfterFirst.factory, StopAfterFirst.calls = f, []
        with vm.Patched([], [], lines):
            import main as m

            m.Implementer = StopAfterFirst
            f.repair_debt_specs()
        assert StopAfterFirst.calls == ["REQ-1-2"], StopAfterFirst.calls
        assert f.debt_tried == [F3], f.debt_tried

        tried: list = []
        f.__dict__.pop("closing_repair_min_sec", None)
        with vm.Patched([], [], lines):
            m.Implementer = StopAfterFirst
            StopAfterFirst.calls = []
            suite = rb.World(root).run_e2e(root, f.e2e_specs)
            f.debt_tried = []
            f.repair_failing_specs(suite.output, only=[F3, F4], until=time.time() + 86400, tried=tried)
        assert tried == [F3], tried

@case
def an_unnamed_regression_keeps_an_unknown_debt_with_its_candidates():
    from agent.verify import StepResult

    def unnamed(project_dir, specs, log=print, **kwargs):
        if list(specs) == [rb.OWN]:
            return vm.counting_run_e2e(project_dir, specs, log=log)
        return StepResult(False, "Error: the web server did not come up in time")

    f, lines, after, passed, calls = s3.run_debt_batch([{"write": {rb.APP: "OWN-OK"}}], patch={"run_e2e": unnamed})
    assert any("owes an UNKNOWN regression" in l for l in lines), lines
    assert passed and f.regression_debts == ["REQ-9"], lines
    assert not (f.__dict__.get("debt_specs") or {}), f.debt_specs
    unknown = f.__dict__.get("unknown_debts") or {}
    assert list(unknown) == ["REQ-9"] and F4 in unknown["REQ-9"] and rb.F1 in unknown["REQ-9"], unknown
    assert F4 in f.green_specs and rb.F1 in f.green_specs

@case
def settle_books_the_unknown_debt_only_when_the_batch_stays():
    with tempfile.TemporaryDirectory() as tmp:
        f = rb.make_factory(Path(tmp), "APP")
        f.e2e_specs = [rb.F1, rb.F2, F4]
        f.log = None
        assert f.settle_regression_debt("REQ-9", [rb.OWN], {"broken": set(), "evidence": {}, "own_ran": 1, "unknown": [rb.F2, F4]},
                                        {}, [], {}) is None
        assert f.unknown_debts == {"REQ-9": [rb.F2, F4]} and not (f.__dict__.get("debt_specs") or {})

        for name in f.CHECKPOINT_FIELDS:
            if not hasattr(f, name):
                setattr(f, name, [] if name.endswith("s") else {})
        f.description_pass, f.contract, f.partial_green = {}, "", {}
        f.save_checkpoint("digest", ["REQ-9"], "")
        g = rb.make_factory(Path(tmp), "APP")
        os.environ.pop("AGENT_RESUME", None)
        g.load_checkpoint("digest")
        assert g.unknown_debts == {"REQ-9": [rb.F2, F4]}, g.__dict__.get("unknown_debts")

@case
def the_first_job_confirms_unknown_candidates_and_repairs_only_the_named():
    lines: list[str] = []
    calls: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = vm.closing_factory(root, "APP " + s3.F4_BREAK)
        vm.measured(f)
        f.debt_specs = {}
        f.unknown_debts = {"REQ-9": [rb.F2, F4]}
        with vm.Patched([{"app": "APP fixed"}], calls, lines):
            repaired = f.repair_debt_specs()
        assert repaired == 1 and [c["node"] for c in calls] == ["REQ-4-2-3"], (calls, lines)
        assert "REQ-9" in calls[0]["context"] and "named no failing spec" in calls[0]["context"], calls[0]["context"]
        assert any("closing, first job" in l and "unknown" in l for l in lines), lines

        calls.clear()
        f.unknown_debts = {"REQ-9": [rb.F2, F4]}
        with vm.Patched([], calls, lines):
            assert f.repair_debt_specs() == 0 and not calls

        with Settings(closing_debt_first=False), vm.Patched([], calls, lines):
            assert f.repair_debt_specs() == 0

@case
def unknown_candidates_never_crowd_the_owed_specs_out_of_the_first_job():
    import main as m

    lines: list[str] = []
    calls: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = vm.closing_factory(root, "APP " + s3.F4_BREAK)
        vm.measured(f)
        f.unknown_debts = {"REQ-10": [rb.F1, rb.F2]}
        one = f.build_est() + f.suite_est(1) + f.closing_repair_min_sec()
        three = f.build_est() + f.suite_est(3) + f.closing_repair_min_sec()
        assert three > one + 20.0, (one, three)

        f.hard_end_at = m.clock.now() + f.closing_chain_sec(len(f.e2e_specs)) + one + (three - one) / 2
        with vm.Patched([{"app": "APP fixed"}], calls, lines):
            repaired = f.repair_debt_specs()
        assert repaired == 1 and [c["node"] for c in calls] == ["REQ-4-2-3"], (calls, lines)
        assert not any("not started" in l for l in lines) and not any("candidate(s) of unknown debts" in l for l in lines), lines

@case
def the_closing_repairs_check_keeps_infra_of_own_and_guard_runs():
    from agent.verify import StepResult

    seen: list = []

    class Probe:
        def __init__(self, llm, project_dir, check, **options):
            self.check = check

        def implement(self, node, tests, outcome, extra_context=""):
            from agent.codegen import ImplementResult

            seen.append(self.check())
            return ImplementResult("budget", [], "scripted", 1)

    for failing_run in ("own", "guard"):
        seen.clear()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = vm.closing_factory(root, "APP " + s3.F4_BREAK)
            vm.measured(f)
            world = rb.World(root)
            runs = {"n": 0}

            def fake_run(project_dir, specs, log=print, failing_run=failing_run, **kwargs):
                runs["n"] += 1
                if runs["n"] == 1:
                    return world.run_e2e(project_dir, specs, log=log)
                if failing_run == "own" and F4 in specs:
                    return StepResult(False, "E2E NOT RUN (install)", infra="backend: npm install failed")
                if failing_run == "guard" and F4 not in specs:
                    return StepResult(False, "E2E NOT RUN (install)", infra="backend: npm install failed")
                return StepResult(True, "1 passed", ran=1)

            lines: list[str] = []
            with vm.Patched([], [], lines):
                import main as m

                m.run_e2e, m.Implementer = fake_run, Probe
                suite = world.run_e2e(root, f.e2e_specs)
                f.repair_failing_specs(suite.output, only=[F4])
            assert seen and seen[0].passed is False and seen[0].infra == "backend: npm install failed", (failing_run, seen)

@case
def an_unknown_regression_round_resets_the_chase_streak_of_its_specs():
    import main as m

    with tempfile.TemporaryDirectory() as tmp:
        f = rb.make_factory(Path(tmp), "APP")
        state = {"chase_armed": True}
        red = rb.spec_output(F4_NAME, ["REQ-4-2-3: Scenario 1"])
        saved = m.log
        m.log = lambda *a, **k: None
        try:
            assert f.chase_streak("REQ-9", state, [F4_NAME], {F4_NAME}, red) == ""
            f.chase_unknown(state, [F4_NAME])
            assert f.chase_streak("REQ-9", state, [F4_NAME], {F4_NAME}, red) == ""
            assert f.chase_streak("REQ-9", state, [F4_NAME], {F4_NAME}, red) != ""
        finally:
            m.log = saved

    source = inspect.getsource(m.Factory.process_node)
    assert "self.chase_unknown(check_state, " in source

@case
def the_api_mute_spares_once_green_files_and_lifts_on_a_backend_change():
    import main as m
    from agent.testrun import TestOutcome

    old, partly, other = "backend/tests/old.test.js", "backend/tests/partly.test.js", "backend/tests/b.test.js"
    red = TestOutcome(False, "## backend/tests/old.test.js\n FAIL  tests/old.test.js > lists things\n ❯ tests/partly.test.js (3 tests | 1 failed) 20ms\n"
                             "   × updates a note\nAssertionError: x")
    saved = m.log
    m.log = lambda *a, **k: None
    try:
        with tempfile.TemporaryDirectory() as tmp, Settings(timesave_api_mute=True):
            root = Path(tmp)
            f = rb.make_factory(root, "APP")
            (root / "backend" / "tests").mkdir(parents=True, exist_ok=True)
            (root / old).write_text("const app = require('../src/app');" + NL, encoding="utf-8")
            (root / "backend" / "src" / "app.js").write_text("const notes = require('./routes/notes');" + NL, encoding="utf-8")
            (root / "backend" / "src" / "routes").mkdir(parents=True, exist_ok=True)
            (root / "backend" / "src" / "routes" / "notes.js").write_text("// notes" + NL, encoding="utf-8")
            files = [old, partly, other]
            f.note_api_run(files, [], red)
            f.note_api_run(files, [], red)

            assert f.advisory_api_files("REQ-9", files, []) == [partly, other], f.api_red_streaks

            (root / rb.APP).write_text("APP 2", encoding="utf-8")
            (root / "backend" / "src" / "server.js").write_text("// server 2", encoding="utf-8")
            assert f.advisory_api_files("REQ-10", files, []) == [partly, other]

            (root / "backend" / "src" / "routes" / "notes.js").write_text("// notes 2" + NL, encoding="utf-8")
            assert f.advisory_api_files("REQ-11", files, []) == files
            f.note_api_run(files, [], red)
            assert f.advisory_api_files("REQ-11", files, []) == [partly, other]

            (root / old).unlink()
            (root / "backend" / "src" / "routes" / "notes.js").write_text("// notes 3" + NL, encoding="utf-8")
            assert f.advisory_api_files("REQ-12", files, []) == [partly, other]
    finally:
        m.log = saved

@case
def error_signature_keeps_the_business_prefix_of_a_generated_name():
    import main as m

    a = m.error_signature("Error: expected invoice-1790680142446-x to be listed")
    b = m.error_signature("Error: expected payment-1790680142446-y to be listed")
    assert a != b and "invoice-" in a and "payment-" in b, (a, b)
    assert m.error_signature("Error: pw-branch-1790680142446-t5ar missing") == m.error_signature("Error: pw-branch-1790680199999-qxq5 missing")
    assert "1790680142446" not in a

@case
def the_two_v144m_switches_are_registered_settings():
    from agent import settings

    for key in ("regression_net_rollback", "closing_debt_first"):
        assert settings._DEFAULTS.get(key) is True, key
        saved = os.environ.get("AGENT_" + key.upper())
        os.environ["AGENT_" + key.upper()] = "0"
        try:
            assert settings._load()[key] is False, key
        finally:
            if saved is None:
                os.environ.pop("AGENT_" + key.upper(), None)
            else:
                os.environ["AGENT_" + key.upper()] = saved

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
    print(f"{len(CASES) - failed}/{len(CASES)} r49s cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
