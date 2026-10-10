from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True

import cases_v144m as v144m
import cases_rollback as rb
from agent import clock

CASES = []

def case(fn):
    CASES.append(fn)
    return fn

class TickingClock:

    def __enter__(self):
        self.saved = clock.now
        base = self.saved()
        self.calls = 0

        def now() -> float:
            self.calls += 1
            return base + self.calls * 0.001

        clock.now = now
        return self

    def __exit__(self, *exc):
        clock.now = self.saved
        return False

@case
def one_owed_spec_starts_its_closing_repair_on_a_fine_clock():
    lines: list[str] = []
    calls: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = v144m.closing_factory(root, "APP " + v144m.s3.F4_BREAK)
        v144m.measured(f)
        with TickingClock() as tick, v144m.Patched([{"app": "APP fixed"}], calls, lines):
            repaired = f.repair_debt_specs()
        assert tick.calls > 1, tick.calls
        assert not any("closing debt repair not started" in l for l in lines), lines
        assert [c["node"] for c in calls] == ["REQ-4-2-3"] and repaired == 1, (calls, lines)
        assert (root / rb.APP).read_text(encoding="utf-8") == "APP fixed"

@case
def a_large_clock_value_does_not_cost_the_float_ulp():
    lines: list[str] = []
    calls: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = v144m.closing_factory(root, "APP " + v144m.s3.F4_BREAK)

        f.durations = {"build": [(4.0, 0)], "e2e": [(10.52, 1)]}
        saved = clock.now
        clock.now = lambda: 1790000000.123
        try:
            with v144m.Patched([{"app": "APP fixed"}], calls, lines):
                repaired = f.repair_debt_specs()
        finally:
            clock.now = saved
        assert not any("closing debt repair not started" in l for l in lines), lines
        assert repaired == 1, (calls, lines)

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
    print(f"{len(CASES) - failed}/{len(CASES)} v168z clock cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
