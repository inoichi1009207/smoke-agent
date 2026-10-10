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
import cases_timesave as ts

CASES = []
F4 = ts.F4
OTHER = ts.OTHER
BRK4 = rb.brk("REQ-4-2-3: Scenario 1")
BRK4_2 = rb.brk("REQ-4-2-3: Scenario 2")
STOP_LINE = "no further round chases it"
ON = {"timesave_chase_stop": True}

def case(function):
    CASES.append(function)
    return function

def second_assertion(run):
    world = run.__self__

    def inner(project_dir, specs, log=print):
        result = run(project_dir, specs, log=log)
        if not result.ok and "ASSERT-B" in world.sources():
            result.output = result.output.replace("Username format is invalid is shown", "Saved 3 items is shown")
        return result
    return inner

def cut_output(run):
    import main as factory_module

    def inner(project_dir, specs, log=print):
        result = run(project_dir, specs, log=log)
        if not result.ok and len(specs) > 1:
            lines = result.output.splitlines()
            result.output = "\n".join(lines[:3] + [factory_module.OUTPUT_CUT_MARK] + lines[-3:])
        return result
    return inner

@case
def a_later_assertion_failing_after_the_first_was_fixed_is_progress():

    script = [{"write": {rb.APP: "OWN-OK", OTHER: BRK4}}, {"write": {OTHER: BRK4 + " ASSERT-B"}},
              {"write": {OTHER: BRK4 + " ASSERT-B v3"}}, {"write": {OTHER: "fixed"}}]
    f, world, lines, calls, passed, left = ts.run_batch(script, settings=ON, wrap=second_assertion, leftover_ok=True)
    assert len(calls[0]["checks"]) == 3 and len(left) == 1, (len(calls[0]["checks"]), left)
    assert sum(STOP_LINE in l for l in lines) == 1, [l for l in lines if STOP_LINE in l]

    script = [{"write": {rb.APP: "OWN-OK", OTHER: BRK4}}, {"write": {OTHER: BRK4 + " ASSERT-B"}}, {"write": {OTHER: "fixed"}}]
    f, world, lines, calls, passed, left = ts.run_batch(script, settings=ON, wrap=second_assertion)
    assert passed and len(calls[0]["checks"]) == 3 and f.regression_debts == [], (passed, len(calls[0]["checks"]), f.regression_debts)
    assert not any(STOP_LINE in l for l in lines), lines

@case
def another_test_of_the_spec_failing_is_progress():

    script = [{"write": {rb.APP: "OWN-OK", OTHER: BRK4}}, {"write": {OTHER: BRK4_2}},
              {"write": {OTHER: BRK4_2 + " v3"}}, {"write": {OTHER: "fixed"}}]
    f, world, lines, calls, passed, left = ts.run_batch(script, settings=ON, leftover_ok=True)
    assert len(calls[0]["checks"]) == 3 and len(left) == 1, (len(calls[0]["checks"]), left)

    script = [{"write": {rb.APP: "OWN-OK", OTHER: BRK4 + BRK4_2}}, {"write": {OTHER: BRK4_2}}, {"write": {OTHER: "fixed"}}]
    f, world, lines, calls, passed, left = ts.run_batch(script, settings=ON)
    assert passed and len(calls[0]["checks"]) == 3 and not any(STOP_LINE in l for l in lines), (passed, lines)

@case
def a_foundation_break_that_makes_progress_is_not_stopped_into_a_rollback():

    script = [{"write": {rb.APP: "OWN-OK " + rb.brk(rb.S1)}}, {"write": {rb.APP: "OWN-OK ASSERT-B " + rb.brk(rb.S1)}},
              {"write": {rb.APP: "OWN-OK fixed"}}]
    f, world, lines, calls, passed, left = ts.run_batch(script, settings=ON, wrap=second_assertion)
    assert passed and len(calls[0]["checks"]) == 3, (passed, len(calls[0]["checks"]))
    assert f.regression_debts == [] and rb.OWN in f.green_specs, (f.regression_debts, f.green_specs)
    assert not any(STOP_LINE in l for l in lines), lines

@case
def the_same_tests_with_the_same_errors_twice_still_stop():

    script = [{"write": {rb.APP: "OWN-OK", OTHER: BRK4}}, {"write": {OTHER: BRK4 + " v2"}},
              {"write": {OTHER: BRK4 + " v3"}}, {"write": {OTHER: "fixed"}}]
    f, world, lines, calls, passed, left = ts.run_batch(script, settings=ON, leftover_ok=True)
    assert len(calls[0]["checks"]) == 2 and len(left) == 2, (len(calls[0]["checks"]), left)
    assert passed and f.regression_debts == ["REQ-9"], (passed, f.regression_debts)

@case
def unreadable_titles_or_errors_never_stop_a_chase():
    script = [{"write": {rb.APP: "OWN-OK", OTHER: BRK4 + BRK5}}, {"write": {OTHER: BRK4 + BRK5 + " v2"}},
              {"write": {OTHER: BRK4 + BRK5 + " v3"}}, {"write": {OTHER: "fixed"}}]
    f, world, lines, calls, passed, left = ts.run_batch(script, settings=ON, wrap=cut_output)
    assert passed and len(calls[0]["checks"]) == 4 and not any(STOP_LINE in l for l in lines), (passed, lines)

    import main as factory_module

    f = rb.make_factory(Path(tempfile.gettempdir()), "x")
    saved = factory_module.log
    factory_module.log = lambda line: None
    name = "REQ-4-2-3.spec.js"
    no_error = "\n".join(line for line in rb.spec_output(name, ["REQ-4-2-3: Scenario 1"]).splitlines() if "Error:" not in line)
    try:
        for output in ("", no_error):
            state = {"chase_armed": True}
            for _ in range(3):
                assert f.chase_streak("REQ-9", state, [name], {name}, output) == "", output

        red = rb.spec_output(name, ["REQ-4-2-3: Scenario 1"])
        state = {"chase_armed": True}
        assert f.chase_streak("REQ-9", state, [name], {name}, red) == ""
        assert f.chase_streak("REQ-9", state, [name], {name}, "") == ""
        assert f.chase_streak("REQ-9", state, [name], {name}, red) == ""
        assert name in f.chase_streak("REQ-9", state, [name], {name}, red)
    finally:
        factory_module.log = saved

BRK5 = rb.brk("REQ-5-5: Scenario 1")

@case
def the_regression_call_hands_its_output_to_the_chase():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "self.chase_streak(req_id, check_state, ran_names, failed_names, again.output)" in source

@case
def shipped_switches():
    import json
    from agent.settings import _DEFAULTS
    assert _DEFAULTS["timesave_chase_stop"] is True
    assert _DEFAULTS["timesave_foundation_filter"] is False
    shipped = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
    assert "timesave_chase_stop" not in shipped and "timesave_foundation_filter" not in shipped

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
    print(f"{len(CASES) - failed}/{len(CASES)} v144f cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
