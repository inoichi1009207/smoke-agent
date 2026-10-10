from __future__ import annotations

import dataclasses
import itertools
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_closing as cl

CASES = []

def case(function):
    CASES.append(function)
    return function

def varying(world, errors):
    counter = itertools.count()

    def run_e2e(project_dir, specs, log=print, shared_data=False):
        result = world.run_e2e(project_dir, specs, log=log, shared_data=shared_data)
        if result.ok or result.skipped:
            return result
        text = "\n".join(line.replace("Error: The requirement says X is shown, but nothing visible matches.", errors(next(counter)))
                         if "Error: The requirement says X is shown" in line else line for line in result.output.split("\n"))
        return dataclasses.replace(result, output=text)
    return run_e2e

def generated_name(n: int) -> str:
    suffix = ["t5ar", "ubgr", "mmxi", "2p2v", "yg7h", "6wcq"][n % 6]
    return f"Error: The requirement says option named Create branch: pw-branch-{1790680142446 + 997 * n}-{suffix} is no longer shown, but it is still visible."

def other_way(n: int) -> str:
    return ("Error: An outsider who only read the requirement could not find anything to click: undefined." if n == 0 else
            f"Error: link named README.md was not on the page when it opened: it was found {100 + n} ms after the click")

def red_at_baseline_world(errors):
    tmp, root, world, f = cl.with_world(app="base " + cl.brk(cl.T2A))
    world.titles["REQ-3"] = [cl.T3A, cl.T3B]
    world.steps = [{"write": {cl.APP: "base " + cl.brk(cl.T2A) + " " + cl.ok(cl.T3A) + " " + cl.ok(cl.T3B)}, "check": True}]
    return tmp, root, world, f, {"run_e2e": varying(world, errors)}

@case
def a_test_red_at_the_baseline_with_a_generated_name_in_its_error_is_not_new():
    tmp, root, world, f, patch = red_at_baseline_world(generated_name)
    with tmp:
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        entry = cl.ledger(f)[0]
        assert not (entry.get("rollback") or {}).get("done"), (entry, [line for line in lines if "new-red" in line])
        assert cl.ok(cl.T3A) in cl.app_text(root) and cl.D3 in f.e2e_specs, (cl.app_text(root), entry)
        assert summary["repairs_kept"] == 1 and summary["repairs_rolled_back"] == 0, summary
        assert any("repair kept" in line for line in lines), lines

@case
def a_test_red_at_the_baseline_that_now_fails_another_way_is_not_new():
    tmp, root, world, f, patch = red_at_baseline_world(other_way)
    with tmp:
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        assert summary["repairs_kept"] == 1 and summary["repairs_rolled_back"] == 0, (summary, cl.ledger(f))

@case
def a_test_green_at_the_baseline_that_goes_red_still_puts_the_repair_back():

    tmp, root, world, f = cl.with_world(app="base " + cl.brk(cl.T1A))
    with tmp:
        world.titles["REQ-3"] = [cl.T3A, cl.T3B]
        world.steps = [{"write": {cl.APP: "base " + cl.brk(cl.T1A) + " " + cl.brk(cl.T1B) + " " + cl.ok(cl.T3A) + " "
                                  + cl.ok(cl.T3B)}, "check": True}]
        p, summary, lines = cl.run_pass(world, f, patch={"run_e2e": varying(world, generated_name)})
        entry = cl.ledger(f)[0]
        reason = entry["rollback"]["reason"]
        assert entry["rollback"]["done"] and reason.startswith("new-red: REQ-1.spec.js: " + cl.T1B), entry
        assert cl.T1A not in reason, reason
        assert cl.app_text(root) == "base " + cl.brk(cl.T1A) and summary["repairs_kept"] == 0, cl.app_text(root)

@case
def a_file_green_at_the_baseline_that_goes_red_still_puts_the_repair_back():
    tmp, root, world, f = cl.with_world()
    with tmp:
        world.titles["REQ-3"] = [cl.T3A, cl.T3B]
        world.steps = [{"write": {cl.APP: "base " + cl.brk(cl.T2A) + " " + cl.ok(cl.T3A) + " " + cl.ok(cl.T3B)}, "check": True}]
        p, summary, lines = cl.run_pass(world, f)
        entry = cl.ledger(f)[0]
        assert entry["rollback"]["done"] and entry["rollback"]["reason"].startswith("new-red: REQ-2.spec.js"), entry
        assert cl.app_text(root) == "base", cl.app_text(root)

@case
def the_gate_function_by_title():
    from agent.description_pass import WHOLE, new_red

    base = {"F.spec.js": ["a"], "U.spec.js": WHOLE}
    assert new_red({"F.spec.js": ["a"]}, base) == {}
    assert new_red({"F.spec.js": ["a", "b"]}, base) == {"F.spec.js": ["b"]}
    assert new_red({"G.spec.js": ["x"]}, base) == {"G.spec.js": ["x"]}
    assert new_red({"U.spec.js": ["x"]}, base) == {"U.spec.js": ["x"]}
    assert new_red({"F.spec.js": WHOLE}, base) == {"F.spec.js": WHOLE}

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
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed}/{len(CASES)} newred cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
