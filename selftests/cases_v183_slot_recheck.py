from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True

import main as m
from agent import settings

CASES = []
SPECS = ["test-e2e/REQ-1-1-1.spec.js", "test-e2e/REQ-2-1-1.spec.js", "test-e2e/REQ-2-2-2.spec.js"]
LATE = ("    Error: button named Add worksheet was not on the page when it opened: it was found 225 ms after the click "
        "that opened this page, not at the first look.")
OTHER = "    Error: expect(locator).toHaveText(expected) failed"

def case(fn):
    CASES.append(fn)
    return fn

class Run:
    def __init__(self, ok, output, skipped=False):
        self.ok, self.output, self.skipped = ok, output, skipped

def red_output(name: str, error: str) -> str:
    return (f"  1) test-e2e/{name}:40:1 › X: no error\n\n{error}\n\n    Error Context: test-results/x/error-context.md\n\n"
            f"  1 failed\n    test-e2e/{name}:40:1 › X: no error\n")

def factory(runs):
    f = m.Factory.__new__(m.Factory)
    f.project_dir = Path(tempfile.mkdtemp())
    f.continuation = {"protected": list(SPECS), "baseline_red": {}}
    calls = []

    def fake_run(project_dir, specs, log=print):
        calls.append(list(specs))
        return runs.pop(0)

    return f, calls, fake_run

def check(runs, newly=("REQ-2-1-1.spec.js",), off=False):
    f, calls, fake = factory(list(runs))
    saved = m.run_e2e
    m.run_e2e = fake
    if off:
        settings.SETTINGS["slot_protected_recheck"] = False
    try:
        got = f.protected_red_after_slot(list(newly), SPECS, "seed-foundation")
    finally:
        m.run_e2e = saved
        settings.SETTINGS.pop("slot_protected_recheck", None)
    return got, calls, f.continuation

@case
def a_flake_keeps_the_slot():
    got, calls, cont = check([Run(True, "1 passed")])
    assert got == [] and calls == [["test-e2e/REQ-2-1-1.spec.js"]], (got, calls)
    assert cont["baseline_red"] == {} and "test-e2e/REQ-2-1-1.spec.js" in cont["protected"]

@case
def red_again_only_by_the_first_look_rule_becomes_baseline_red():
    got, calls, cont = check([Run(False, red_output("REQ-2-1-1.spec.js", LATE))])
    assert got == [], got
    assert "REQ-2-1-1.spec.js" in cont["baseline_red"], cont
    assert "test-e2e/REQ-2-1-1.spec.js" not in cont["protected"] and "test-e2e/REQ-1-1-1.spec.js" in cont["protected"]

@case
def any_other_error_rolls_back_as_before():
    got, _, cont = check([Run(False, red_output("REQ-2-1-1.spec.js", LATE + "\n" + OTHER))])
    assert got == ["REQ-2-1-1.spec.js"] and cont["baseline_red"] == {}, (got, cont)
    got, _, _ = check([Run(False, red_output("REQ-2-1-1.spec.js", OTHER))])
    assert got == ["REQ-2-1-1.spec.js"], got

@case
def no_verdict_rolls_back_as_before():

    assert check([Run(False, "", skipped=True)])[0] == ["REQ-2-1-1.spec.js"]
    assert check([Run(False, "something broke")])[0] == ["REQ-2-1-1.spec.js"]
    assert check([], newly=("REQ-9-9-9.spec.js",))[0] == ["REQ-9-9-9.spec.js"]

    f, _, _ = factory([])
    saved = m.run_e2e

    def boom(*a, **k):
        raise RuntimeError("boom")

    m.run_e2e = boom
    try:
        assert f.protected_red_after_slot(["REQ-2-1-1.spec.js"], SPECS, "seed-foundation") == ["REQ-2-1-1.spec.js"]
    finally:
        m.run_e2e = saved

@case
def off_and_nothing_red_are_unchanged():
    got, calls, _ = check([Run(True, "1 passed")], off=True)
    assert got == ["REQ-2-1-1.spec.js"] and calls == [], (got, calls)
    got, calls, _ = check([], newly=())
    assert got == [] and calls == []

@case
def both_slots_call_it_and_the_key_is_registered():
    import inspect

    src = inspect.getsource(m)
    assert src.count("self.protected_red_after_slot(newly, specs,") == 2
    assert settings._DEFAULTS.get("slot_protected_recheck") is True

def main() -> int:
    failed = 0
    for fn in CASES:
        try:
            fn()
            print(f"ok    {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed}/{len(CASES)} v183 slot re-check cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
