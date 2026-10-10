from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

CASES = []
T0 = 1_000_000.0
FILES = 43
FAILING = 17

def case(function):
    CASES.append(function)
    return function

class FakeTrace:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None

class Sim:

    def __init__(self, start: float = T0):
        self.t = start
        self.calls: list[tuple[str, float, float]] = []
        self.lines: list[str] = []
        self.failing: set[str] = set()
        self.repairs_pass = False
        self.build_sec: float | None = None
        self.per_file_sec: float | None = None

    def now(self) -> float:
        return self.t

    def spend(self, name: str, seconds: float) -> None:
        start = self.t
        self.t += seconds
        self.calls.append((name, start, self.t))

    def names(self, prefix: str) -> list[tuple[str, float, float]]:
        return [c for c in self.calls if c[0].startswith(prefix)]

class installed:

    def __init__(self, sim: Sim, settings: dict | None = None):
        self.sim, self.settings = sim, dict(settings or {})

    def __enter__(self):
        import main as m
        from agent import clock, verify
        from agent.codegen import ImplementResult
        from agent.llm import LLM
        from agent.settings import SETTINGS
        from agent.verify import OrderedRun, StepResult

        sim = self.sim
        self.saved = (clock.now, m.log, m.build_frontend, m.probe_server, m.run_e2e, m.Implementer, verify.run_e2e_in_order,
                      LLM.quota_exhausted)
        self.saved_settings = {k: SETTINGS.get(k, KeyError) for k in self.settings}
        SETTINGS.update(self.settings)
        clock.now = sim.now
        m.log = sim.lines.append
        LLM.quota_exhausted = False

        def build_frontend(project_dir, log=print):
            sim.spend("build", m.BUILD_WORST_SEC if sim.build_sec is None else sim.build_sec)
            return StepResult(True, "built")

        def probe_server(project_dir, log=print, port=None):
            sim.spend("probe", m.PROBE_WORST_SEC)
            return StepResult(True, "ok"), 3000

        def run_e2e(project_dir, spec_files, log=print, shared_data=False):
            specs = [s for s in spec_files if s.startswith("backend/test-e2e/")]
            sim.spend(f"e2e:{len(specs)}", m.e2e_worst(len(specs)) if sim.per_file_sec is None else 60.0 + sim.per_file_sec * len(specs))
            red = [Path(s).name for s in specs if Path(s).name in sim.failing]
            return StepResult(not red, ("FAILED SPEC FILES: " + ", ".join(red) + "\n" if red else "") + "E2E output")

        class FakeImplementer:
            made: list = []

            def __init__(self, llm, project_dir, check, *, max_rounds=4, extra_rounds=3, deadline=None, **kwargs):
                self.max_rounds, self.extra_rounds, self.deadline = max_rounds, extra_rounds, deadline
                FakeImplementer.made.append(self)

            def implement(self, node, test_files, first, extra_context=""):
                rounds = 0
                while rounds <= self.max_rounds + self.extra_rounds:
                    if self.deadline is not None and sim.t > self.deadline:
                        return ImplementResult("budget", [], "node deadline reached", rounds)
                    sim.spend("round", m.HARD_END_ROUND_SEC)
                    rounds += 1
                    if sim.repairs_pass:
                        return ImplementResult("passed", [], "green", rounds, last_check="E2E PASSED")
                return ImplementResult("gave_up", [], "still red", rounds)

        def run_e2e_in_order(project_dir, order, log=print, time_limit=1800.0, per_file_timeout=300):
            sim.spend("shared-sweep", time_limit + 60 + 30)
            return OrderedRun({s: "passed" for s in order}, {}, "")

        m.build_frontend, m.probe_server, m.run_e2e, m.Implementer = build_frontend, probe_server, run_e2e, FakeImplementer
        verify.run_e2e_in_order = run_e2e_in_order
        self.Implementer = FakeImplementer
        return self

    def __exit__(self, *exc):
        import main as m
        from agent import clock, verify
        from agent.llm import LLM
        from agent.settings import SETTINGS

        (clock.now, m.log, m.build_frontend, m.probe_server, m.run_e2e, m.Implementer, verify.run_e2e_in_order,
         LLM.quota_exhausted) = self.saved
        for key, value in self.saved_settings.items():
            if value is KeyError:
                SETTINGS.pop(key, None)
            else:
                SETTINGS[key] = value

def spec(i: int) -> str:
    return f"backend/test-e2e/REQ-{i}.spec.js"

def make_factory(sim: Sim, tmp: Path, files: int = FILES, hard: bool = True):
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.started, f.spent_before = sim.t, 0.0
    f.hard_end_at = m.platform_hard_end(sim.t) if hard else None
    f.large_tree = True
    f.time_budget = float(min(m.LARGE_TREE_CAP_SEC - m.final_judgement_reserve(files),
                              m.LARGE_TREE_BASE_SEC + m.LARGE_TREE_PER_BATCH_SEC * files))
    f.project_dir = tmp
    specs = [spec(i) for i in range(1, files + 1)]
    for s in specs:
        (tmp / s).parent.mkdir(parents=True, exist_ok=True)
        (tmp / s).write_text("test('x', async () => {});", encoding="utf-8")
    sim.failing = {Path(s).name for s in specs[:FAILING]}
    f.e2e_specs, f.all_test_files = list(specs), list(specs)
    f.spec_context = {s: {"node": {"id": f"REQ-{i}", "name": "n"}, "contract": "", "relevant": [], "tests": {s: "x"},
                          "members": [f"REQ-{i}"], "written": []} for i, s in enumerate(specs, 1)}
    f.green_specs, f.description_specs, f.revised_specs = [], [], set()
    f.failed_members = [f"REQ-{i}" for i in range(1, FAILING + 1)]
    f.no_evidence_members, f.unattempted_members, f.regression_debts = [], [], []
    f.report = {"nodes": {}, "verification": {}}
    f.verify_repairs, f.restore_failures, f.install_failed = 0, 0, False
    f.trace = FakeTrace()
    f.record_run = lambda *args, **kwargs: None
    f.restore_tests = lambda: None
    f.save_checkpoint = lambda *args, **kwargs: None
    f.requirements_dir = tmp / "no-requirements-here"
    f.contract, f.llm, f.current_trace_id = "", None, ""
    return f

def run_batches(sim: Sim, f, batches: int = FILES, each: float | None = None) -> list[float]:
    deadlines: list[float] = []

    def process_node(node, nodes_left, retry=False):
        deadline = f.node_deadline(nodes_left)
        deadlines.append(deadline)
        start = sim.t
        sim.t = max(sim.t, deadline) + __import__("main").HARD_END_ROUND_SEC if each is None else sim.t + each
        sim.calls.append(("batch", start, sim.t))
        f.report["nodes"][str(node["id"])] = {"status": "passed", "tests": []}
        return True

    f.process_node = process_node
    f.run_queue([({"id": f"B{i}", "members": [f"B{i}"]}, []) for i in range(batches)], [], "digest", "")
    return deadlines

def pass_tools(sim: Sim, leaf_ids: list[str]):
    import main as m
    from agent.description_pass import Tools
    from agent.verify import StepResult

    def run_e2e(project_dir, specs, log=print):
        sim.spend("desc-e2e", m.e2e_worst(1) if sim.per_file_sec is None else 60.0 + sim.per_file_sec)
        return StepResult(True, "E2E PASSED", ran=1)

    def build_frontend(project_dir, log=print):
        sim.spend("desc-build", m.BUILD_WORST_SEC if sim.build_sec is None else sim.build_sec)
        return StepResult(True, "built")

    return Tools(run_e2e=run_e2e, build_frontend=build_frontend, Implementer=m.Implementer,
                 author=lambda *a, **k: None, review=lambda *a, **k: "", deterministic_review=lambda *a, **k: [],
                 static_defects=lambda *a, **k: "", now=sim.now, tokens=lambda: 10 ** 6, quota_exhausted=lambda: False,
                 boilerplate_of_text=lambda whole: [(leaf, "scenario") for leaf in leaf_ids],
                 failed_test_titles=m.failed_test_titles, lost_stated_names=lambda *a, **k: [],
                 lost_seed_values=lambda *a, **k: [], final_judgement_reserve=m.final_judgement_reserve,
                 large_tree_cap_sec=float(m.LARGE_TREE_CAP_SEC))

def run_tail(sim: Sim, f, leaves: int = 23) -> dict:
    import main as m
    from agent.description_pass import DescriptionPass

    if f.seed_identity_due():
        sim.spend("seed-identity", m.SEED_IDENTITY_SEC)
    leaf_ids = [f"REQ-D{i}" for i in range(1, leaves + 1)]
    tree = {"id": "ROOT", "children": [{"id": leaf, "name": leaf, "description": "d", "scenarios": []} for leaf in leaf_ids]}
    work = [{"id": leaf, "members": [leaf]} for leaf in leaf_ids]
    summary = DescriptionPass(f, tree, work, "whole", [], "digest", "", sim.lines.append, pass_tools(sim, leaf_ids)).run()
    f.verify()
    sim.spend("finish", m.HARD_END_TAIL_SEC)
    return summary

GUARD_SETTINGS = {"closing_regression_guard": False, "description_pass": True, "shared_sweep": True}

@case
def a_github_tree_whose_batches_use_21_25_h_ends_before_the_hard_end():
    import main as m

    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim, GUARD_SETTINGS):
        f = make_factory(sim, Path(tmp))
        hard = f.hard_end()
        assert hard == T0 + 86400 - 5400
        run_batches(sim, f, each=21.25 * 3600 / FILES)
        assert len(sim.names("batch")) == FILES, "every batch of 21.25 h fits the batch window"
        assert abs(sim.t - (T0 + 21.25 * 3600)) < 1
        summary = run_tail(sim, f)
    assert sim.t <= hard, (sim.t - hard, sim.calls[-12:])

    assert summary.get("status") == "time", summary
    assert not sim.names("seed-identity") and not sim.names("shared-sweep") and not sim.names("desc-")
    assert [c[0] for c in sim.calls if c[0] != "batch"] == ["build", "probe", "build", "finish"], [c[0] for c in sim.calls]
    assert f.report["verification"]["e2e"] is False
    assert any("[hard-end] closing suite run and closing repair not started" in l for l in sim.lines), sim.lines
    assert any("[hard-end] description pass: window cut" in l for l in sim.lines), sim.lines
    assert any("[hard-end] seed identity" in l for l in sim.lines)

@case
def a_github_tree_with_18_h_of_batches_shrinks_the_closing_repair_and_skips_the_report_only_phases():
    import main as m

    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim, GUARD_SETTINGS) as inst:
        f = make_factory(sim, Path(tmp))
        hard = f.hard_end()
        run_batches(sim, f, each=18 * 3600 / FILES)
        run_tail(sim, f)
    assert sim.t <= hard, sim.t - hard
    closing = [c for c in sim.calls if c[0] == "round"]
    assert closing, "the closing repair ran"
    finish_start = sim.names("finish")[0][1]

    assert max(c[2] for c in closing) <= hard - m.HARD_END_FINISH_SEC, (max(c[2] for c in closing) - hard)
    assert sim.names("build")[-1][2] <= finish_start

    assert 0 < len({id(i) for i in inst.Implementer.made}) < FAILING

    assert not sim.names("desc-") and any("[hard-end] description pass: window cut" in l for l in sim.lines)

    assert any("final judgement" in l and "not started" in l for l in sim.lines), [l for l in sim.lines if "hard-end" in l]
    for _name, start, end in sim.names("shared-sweep"):
        assert start >= max(c[2] for c in closing) and end <= hard - m.HARD_END_TAIL_SEC
    assert any("closing repair stops: the hard end is near" in l for l in sim.lines)

@case
def a_description_pass_with_time_never_eats_the_closing_chain():
    import main as m

    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim, GUARD_SETTINGS):
        f = make_factory(sim, Path(tmp))
        hard = f.hard_end()
        run_batches(sim, f, each=12 * 3600 / FILES)
        chain = f.closing_want_sec(len(f.e2e_specs))
        run_tail(sim, f)
    assert sim.t <= hard
    desc = sim.names("desc-")
    assert desc and max(c[2] for c in desc) <= hard - chain, (max(c[2] for c in desc) - hard, chain)
    assert sim.names("e2e:43") and sim.names("round")
    assert any("[hard-end] description pass run of" in l for l in sim.lines)

@case
def batches_that_run_to_their_deadlines_stop_starting_before_the_hard_end_and_the_run_still_ends_before_it():
    import main as m

    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim, GUARD_SETTINGS):
        f = make_factory(sim, Path(tmp))
        hard = f.hard_end()
        deadlines = run_batches(sim, f, each=None)
        batches = sim.names("batch")
        assert len(batches) < FILES, "not every batch can start"
        after = m.HARD_END_FINISH_SEC + m.HARD_END_BATCH_AFTER_SEC
        assert all(d <= hard - after - m.HARD_END_ROUND_SEC + 1e-6 for d in deadlines)
        assert max(c[2] for c in batches) <= hard - after
        assert max(c[1] for c in batches) <= hard - m.HARD_END_BATCH_TAIL_SEC
        assert any("[hard-end] batches stop before" in l for l in sim.lines)
        run_tail(sim, f)
    assert sim.t <= hard, sim.t - hard

@case
def closing_budget_never_reaches_past_the_hard_end():
    import main as m

    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim):
        for large in (True, False):
            f = make_factory(sim, Path(tmp))
            f.large_tree = large
            if not large:
                f.time_budget = 10800.0
            hard = f.hard_end()
            for offset in range(0, 90000, 900):
                sim.t = T0 + offset
                budget = f.closing_budget()
                assert budget >= 0
                assert sim.t + budget <= max(sim.t, hard - m.HARD_END_FINISH_SEC - m.HARD_END_ROUND_SEC) + 1e-6, (large, offset, budget)
                assert sim.t + budget <= hard or budget == 0
            sim.t = T0

@case
def every_implementer_deadline_is_clamped():
    import main as m

    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim) as inst:
        f = make_factory(sim, Path(tmp))
        hard = f.hard_end()
        cap = hard - m.HARD_END_FINISH_SEC - m.HARD_END_ROUND_SEC
        sim.t = hard - 3000
        assert f.node_deadline(1) <= cap - m.HARD_END_BATCH_AFTER_SEC and f.over_budget()
        assert f.hard_deadline(sim.t + 5000, "x") == cap
        assert f.hard_deadline(sim.t + 10, "x") == sim.t + 10

        sim.t = hard - m.HARD_END_BATCH_TAIL_SEC - 600
        assert not f.over_budget()
        assert f.repair_round("playwright-e2e", lambda: None, "broken") is False and not inst.Implementer.made
        assert any("playwright-e2e repair round not started" in l for l in sim.lines), sim.lines
    source_checks = {
        "repair_regression_debt": "self.hard_deadline(clock.now() + self.REGRESSION_REPAIR_SEC",
        "process_node": "self.hard_deadline(max(deadline, clock.now() + 480)",
        "node_deadline": "self.hard_deadline(clock.now() + share",
        "repair_round": "self.hard_deadline(self.started + self.time_budget + 300",
        "repair_failing_specs": "deadline = self.hard_deadline(deadline",
    }
    import inspect
    for method, needle in source_checks.items():
        assert needle in inspect.getsource(getattr(m.Factory, method)), method

    body = inspect.getsource(m.Factory.process_node)
    assert "deadline = self.node_deadline(nodes_left)" in body

    assert body.count("Implementer(") == 5 and body.count("deadline=deadline") == 4
    restart = body[body.index("restarter = Implementer("):]
    assert "deadline=deadline" in restart.splitlines()[0]
    from agent import description_pass
    assert 'clamp(deadline, f"description pass repair of {leaf_id}"' in inspect.getsource(description_pass.DescriptionPass.repair)

@case
def the_description_pass_window_ends_before_the_closing_chain():
    import main as m
    from agent.description_pass import DESC_OVERRUN_SLACK_SEC, DescriptionPass

    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim):
        f = make_factory(sim, Path(tmp))
        p = DescriptionPass(f, {"id": "ROOT"}, [], "", [], "d", "", sim.lines.append, pass_tools(sim, []))
        hard_elapsed = f.hard_end() - f.started
        n = 66
        assert p.window_end(70000.0, n) == hard_elapsed - f.closing_want_sec(n) - DESC_OVERRUN_SLACK_SEC
        assert any("[hard-end] description pass: window cut" in l for l in sim.lines)

        sim.t = f.hard_end() - f.closing_want_sec(len(f.e2e_specs)) - 100
        suite = p.run_suite(f.e2e_specs[:3])
        assert suite.incomplete.startswith("the hard end is near") and not sim.names("desc-e2e")

        sim.t = T0
        assert not p.run_suite(f.e2e_specs[:2]).incomplete and len(sim.names("desc-e2e")) == 2

@case
def report_only_phases_are_skipped_first():
    import main as m

    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim, GUARD_SETTINGS):
        f = make_factory(sim, Path(tmp))
        hard = f.hard_end()
        need = m.BUILD_WORST_SEC + m.PROBE_WORST_SEC + m.e2e_worst(FILES) + f.closing_repair_min_sec() + m.HARD_END_FINISH_SEC
        sim.t = hard - need - 10
        f.verify()
        sim.spend("finish", m.HARD_END_TAIL_SEC)
    assert sim.t <= hard
    rounds = sim.names("round")
    assert rounds, "the closing repair ran"

    for _name, start, end in sim.names("shared-sweep"):
        assert start >= max(r[2] for r in rounds) and end <= hard - m.HARD_END_TAIL_SEC
    assert f.verify_repairs >= 1 and any("final judgement" in l and "not started" in l for l in sim.lines)
    assert f.report["verification"]["e2e"] is False and f.report["verification"]["server_probe"] is False

@case
def near_the_hard_end_nothing_starts_that_could_cross_it():
    import main as m

    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim, GUARD_SETTINGS):
        f = make_factory(sim, Path(tmp))
        hard = f.hard_end()
        sim.t = hard - 700
        f.verify()
        sim.spend("finish", m.HARD_END_TAIL_SEC)
    assert not sim.names("build") and not sim.names("probe") and not sim.names("e2e"), sim.calls
    assert sim.t <= hard
    assert any("[hard-end] final build not started" in l for l in sim.lines)

@case
def a_second_closing_sweep_starts_only_when_a_repair_can_follow_it():
    import main as m

    sim = Sim()
    sim.repairs_pass = True
    with tempfile.TemporaryDirectory() as tmp, installed(sim, GUARD_SETTINGS):
        f = make_factory(sim, Path(tmp))
        hard = f.hard_end()
        sim.failing = {"REQ-1.spec.js"}
        sim.t = hard - m.HARD_END_FINISH_SEC - f.closing_repair_min_sec() - 10
        repaired = f.repair_failing_specs("FAILED SPEC FILES: REQ-1.spec.js\n")
    assert repaired == 1 and not sim.names("e2e:43"), sim.calls
    assert sim.t <= hard - m.HARD_END_FINISH_SEC
    assert any("second sweep" in l and "not started" in l for l in sim.lines)

@case
def the_shared_sweep_only_reports_in_what_is_left_before_the_tail():
    import main as m
    from agent.verify import StepResult

    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim, GUARD_SETTINGS):
        f = make_factory(sim, Path(tmp))
        hard = f.hard_end()
        f.report["verification"] = {"frontend_build": True, "server_probe": True, "e2e": True}
        sim.t = hard - 1500
        assert f.shared_context_sweep(StepResult(True, "E2E PASSED")) == ""
        assert not sim.names("shared-sweep")
        sim.t = hard - 5000
        f.shared_context_sweep(StepResult(True, "E2E PASSED"))
    sweeps = sim.names("shared-sweep")
    assert len(sweeps) == 1 and sweeps[0][2] <= hard - m.HARD_END_TAIL_SEC

@case
def time_the_pause_clock_credited_comes_off_the_hard_end():
    from agent import clock

    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim):
        f = make_factory(sim, Path(tmp))
        before, saved = f.hard_end(), clock._paused
        try:
            clock._paused = saved + 1800.0
            assert f.hard_end() == before - 1800.0
        finally:
            clock._paused = saved
        assert f.hard_end() == before

@case
def a_suite_cut_for_the_repair_is_still_judged_in_time_left_over():
    import main as m

    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim, GUARD_SETTINGS):
        f = make_factory(sim, Path(tmp))
        hard = f.hard_end()
        sim.t = hard - 7000
        f.verify()
        sim.spend("finish", m.HARD_END_TAIL_SEC)
    assert [c[0] for c in sim.calls] == ["build", "probe", "build", "probe", "e2e:43", "shared-sweep", "finish"] or \
        [c[0] for c in sim.calls][:5] == ["build", "probe", "build", "probe", "e2e:43"], sim.calls
    assert not sim.names("round") and sim.t <= hard
    assert any("judging the delivered state once more" in l for l in sim.lines)
    assert f.report["verification"]["e2e"] is False

@case
def a_gateway_pause_that_uses_up_the_budget_starts_nothing_more():
    import main as m
    from agent.llm import LLMError

    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim):
        f = make_factory(sim, Path(tmp))
        calls = []

        def process_node(node, nodes_left, retry=False):
            calls.append(node["id"])
            raise LLMError("LLM request failed after 2 read timeouts")

        def pause(seconds):
            sim.t = f.hard_end() - m.HARD_END_BATCH_TAIL_SEC + 1

        f.process_node = process_node
        saved = m.time.sleep
        m.time.sleep = pause
        try:
            sim.t = f.hard_end() - m.HARD_END_BATCH_TAIL_SEC - 30
            result = f.attempt_node({"id": "B1", "members": ["B1"]}, nodes_left=1, pause_sec=60.0, requeue=True)
        finally:
            m.time.sleep = saved

    assert calls == ["B1"] and result is None, (calls, result)
    assert any("ran out during the pause" in l for l in sim.lines)

@case
def the_dead_app_probes_are_gated_by_their_own_cost_under_a_hard_end():
    import main as m
    from agent.verify import APP_DOWN_MARKS

    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim):
        f = make_factory(sim, Path(tmp))
        hard = f.hard_end()
        before = {"frontend/src/Old.tsx": b"old"}

        sim.t = hard - m.HARD_END_BATCH_TAIL_SEC + 100
        assert f.time_left() < f.DEAD_APP_MIN_LEFT_SEC
        f.dead_app_put_back("B1", "budget", before, APP_DOWN_MARKS[0])
        assert sim.names("build")

        sim.calls.clear()
        sim.t = hard - m.HARD_END_FINISH_SEC - 1000
        assert f.dead_app_put_back("B1", "budget", before, APP_DOWN_MARKS[0]) is False and not sim.names("build")
        assert any("dead-app probes not started" in l for l in sim.lines)

@case
def a_400_s_clock_jump_does_not_move_the_hard_end_in_wall_time():
    import time as wall

    import main as m
    from agent import clock

    f = m.Factory.__new__(m.Factory)
    f.hard_end_at = m.platform_hard_end(clock.now())
    saved = clock._paused
    try:
        wall_end = f.hard_end() + (wall.time() - clock.now())
        clock.note_gap(400.0, log=lambda message: None)
        assert clock.paused_sec() >= saved + 399
        assert abs((f.hard_end() + (wall.time() - clock.now())) - wall_end) < 1.0
    finally:
        clock._paused = saved

@case
def the_closing_slots_are_sized_from_measured_durations():
    import main as m

    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim):
        f = make_factory(sim, Path(tmp))
        assert f.build_est() == m.BUILD_WORST_SEC and f.suite_est(43) == m.e2e_worst(43)
        for _ in range(3):
            f.note_duration("build", 4.0)
        f.note_duration("e2e", 781.0, 43)
        assert f.build_est() == f.BUILD_EST_FLOOR_SEC
        assert abs(f.suite_est(43) - (1.5 * 781.0 + 60.0)) < 1e-6
        assert abs(f.suite_est(1) - (1.5 * 781.0 / 43 + 60.0)) < 1e-6
        g = make_factory(sim, Path(tmp))
        g.note_duration("e2e", 43.0, 43)
        assert g.suite_est(1) == 1.5 * g.E2E_EST_FLOOR_PER_FILE_SEC + 60.0
        f.note_duration("e2e", 10 ** 6, 1)
        assert f.suite_est(43) == m.e2e_worst(43) and f.build_est() <= m.BUILD_WORST_SEC

        for _ in range(f.EST_KEEP):
            f.note_duration("e2e", 20.0, 1)
        assert f.suite_est(1) == 1.5 * 20.0 + 60.0

@case
def measured_slots_give_the_closing_repair_the_time():
    import main as m

    def run(hours: float, measured: bool):
        sim = Sim()
        with tempfile.TemporaryDirectory() as tmp, installed(sim, GUARD_SETTINGS) as inst:
            f = make_factory(sim, Path(tmp))
            hard = f.hard_end()
            if measured:
                sim.build_sec, sim.per_file_sec = 4.0, (781.0 - 60.0) / FILES
                for _ in range(6):
                    f.note_duration("build", 4.0)
                f.note_duration("e2e", 781.0, FILES)
            run_batches(sim, f, each=hours * 3600 / FILES)
            run_tail(sim, f)
        return sim, hard, len(inst.Implementer.made)

    sim_w, hard_w, worst = run(18.0, False)
    sim_m, hard_m, measured = run(18.0, True)
    assert sim_w.t <= hard_w and sim_m.t <= hard_m
    assert measured >= 3 * worst and measured >= 6, (worst, measured)

    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim, GUARD_SETTINGS):
        f = make_factory(sim, Path(tmp))
        hard = f.hard_end()
        sim.build_sec, sim.per_file_sec = 4.0, (781.0 - 60.0) / FILES
        f.note_duration("build", 4.0)
        f.note_duration("e2e", 781.0, FILES)
        sim.t = hard - 6000
        f.verify()
        sim.spend("finish", m.HARD_END_TAIL_SEC)
    assert sim.names("e2e:43") and sim.names("round") and sim.t <= hard, sim.calls
    sim, hard, repairs = run(77371 / 3600, True)
    assert sim.t <= hard and (sim.t - T0) / 3600 < 21.7, (sim.t - T0) / 3600

@case
def settings_off_behave_as_before():
    import main as m
    from agent.settings import SETTINGS
    from agent.description_pass import DescriptionPass

    saved = SETTINGS.get("platform_time_limit_sec", KeyError)
    try:
        SETTINGS["platform_time_limit_sec"] = "0"
        assert m.platform_hard_end(T0) is None
        SETTINGS["platform_time_limit_sec"] = "3600"
        assert m.platform_hard_end(T0) == T0 + 3600 - 5400
    finally:
        if saved is KeyError:
            SETTINGS.pop("platform_time_limit_sec", None)
        else:
            SETTINGS["platform_time_limit_sec"] = saved
    assert m.platform_hard_end(T0) == T0 + 81000.0
    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim, GUARD_SETTINGS):
        f = make_factory(sim, Path(tmp), hard=False)
        assert f.hard_end() is None and f.hard_left() == float("inf")
        for offset in (0, 50000, 86000, 120000):
            sim.t = T0 + offset
            reserve = m.final_judgement_reserve(len(f.e2e_specs))
            old_closing = max(3600.0, min(f.CLOSING_MAX_SEC, f.started + m.LARGE_TREE_CAP_SEC - sim.t - reserve))
            assert f.closing_budget() == old_closing
            assert f.time_left() == f.time_budget - (sim.t - f.started)
            assert f.node_deadline(3) == sim.t + max(m.MIN_BATCH_SEC, f.time_left() * 0.8 / 3)
            assert f.hard_deadline(sim.t + 99999, "x") == sim.t + 99999
        p = DescriptionPass(f, {"id": "ROOT"}, [], "", [], "d", "", sim.lines.append, pass_tools(sim, []))
        assert p.window_end(1000.0, 60) == min(m.LARGE_TREE_CAP_SEC - p.verify_reserve(60), 1000.0 + 10800.0)

        sim.t = T0 + 23 * 3600
        f.verify()
    assert sim.names("e2e:43") and sim.names("round") and sim.names("shared-sweep")
    assert not [l for l in sim.lines if "[hard-end]" in l], [l for l in sim.lines if "[hard-end]" in l]

@case
def a_fake_built_with_new_has_no_hard_end():
    import main as m

    f = m.Factory.__new__(m.Factory)
    assert f.hard_end() is None and f.hard_fits("x", 10 ** 9) and f.hard_deadline(5.0, "x") == 5.0

@case
def the_start_line_names_the_hard_end_and_each_phase_window():
    sim = Sim()
    with tempfile.TemporaryDirectory() as tmp, installed(sim):
        f = make_factory(sim, Path(tmp))
        import main as m
        m.PROCESS_STARTED, saved = T0, m.PROCESS_STARTED
        try:
            line = f.hard_plan_line(FILES)
        finally:
            m.PROCESS_STARTED = saved
    assert "hard end at 22.50 h" in line and "batches start until 21.13 h, their rounds until 21.47 h" in line, line
    assert "description pass ends by" in line and "closing repair rounds until 21.72 h" in line and "final judgement" in line

@case
def a_run_calls_the_tail_in_this_order():
    import inspect
    import main as m

    source = inspect.getsource(m.Factory.run)
    plan = source.index("self.hard_plan_line(len(work))")
    assert plan < source.index("self.run_queue(") < source.index("self.seed_identity_due()") < source.index("log_seed_identity(whole")
    assert source.index("log_seed_identity(whole") < source.index("run_description_pass(self") < source.index("self.verify()\n")
    assert 'self.report["hard_end"]' in source

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
    print(f"{len(CASES) - failed}/{len(CASES)} hardend cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
