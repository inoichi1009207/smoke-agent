from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_p141s3 as p3
import cases_rollback as rb

NL = chr(10)
CASES = []
F4, F4_BREAK = p3.F4, p3.F4_BREAK
F5 = "backend/test-e2e/REQ-4-2-4.spec.js"
F5_BREAK = rb.brk("REQ-4-2-4: Scenario 1")
F5_BREAK2 = rb.brk("REQ-4-2-4: Scenario 2")
KEPT = {"write": {rb.APP: "OWN-OK " + F4_BREAK + F5_BREAK}}

def case(function):
    CASES.append(function)
    return function

def five(f):
    f.green_specs = [rb.F1, rb.F2, p3.F3, F4, F5]
    f.e2e_specs = list(f.green_specs)
    f.file_specs = {rb.APP: [rb.F1, F4, F5]}
    (f.project_dir / F5).write_text("test('REQ-4-2-4: Scenario 1', async () => {});" + NL, encoding="utf-8")

class CountingWorld(rb.World):

    def run_e2e(self, project_dir, specs, log=print):
        result = super().run_e2e(project_dir, specs, log=log)
        text = self.sources()
        counts = [0 if f"SKIPALL<{Path(s).name}>" in text else 2 - text.count(f"SKIPONE<{Path(s).name}>") for s in specs]
        result.ran = 0 if any(n == 0 for n in counts) else sum(counts)
        return result

    def failing(self, spec, text):
        return [] if f"SKIPALL<{Path(spec).name}>" in text else super().failing(spec, text)

def run(repair, on: bool = True, tweak=five, node_extra=None, patch=None):
    writes = repair if isinstance(repair, dict) else {rb.APP: repair}
    saved = rb.World
    rb.World = CountingWorld
    try:
        return p3.run_debt_batch([dict(KEPT), {"write": writes}], tweak=tweak, node_extra=node_extra, patch=patch,
                                 settings={"regression_partial_keep": on, "partial_repair_evidence": on})
    finally:
        rb.World = saved

def kept_line(lines):
    return [l for l in lines if "the targeted regression repair ended" in l and "kept, the batch still owes" in l]

@case
def a_repair_of_one_of_two_owed_specs_is_kept_and_only_that_debt_is_settled():
    repair = "OWN-OK half " + F5_BREAK
    f, lines, after, passed, calls = run(repair)
    assert after == repair and kept_line(lines), (after, [l for l in lines if "[tdd]" in l])
    assert "['REQ-4-2-3.spec.js']" in kept_line(lines)[0] and "owes ['REQ-4-2-4.spec.js']" in kept_line(lines)[0], kept_line(lines)
    assert F4 in f.green_specs and F5 not in f.green_specs, f.green_specs
    assert f.regression_debts == ["REQ-9"], f.regression_debts

@case
def a_new_failing_test_in_a_still_owed_spec_goes_back():
    f, lines, after, passed, calls = run("OWN-OK half " + F5_BREAK + F5_BREAK2)
    assert not kept_line(lines) and any("the targeted regression repair is not kept" in l for l in lines), lines
    assert F4 not in f.green_specs or "put back to the state before this batch" in " ".join(lines), f.green_specs

@case
def a_repair_that_breaks_a_foundation_spec_goes_back():
    f, lines, after, passed, calls = run("OWN-OK half " + F5_BREAK + rb.brk(rb.S1))
    assert not kept_line(lines) and any("the targeted regression repair is not kept" in l for l in lines), lines

@case
def a_repair_that_breaks_the_own_spec_goes_back():
    f, lines, after, passed, calls = run("half " + F5_BREAK)
    assert not kept_line(lines) and any("the targeted regression repair is not kept" in l for l in lines), lines

@case
def a_repair_that_repairs_nothing_goes_back():
    f, lines, after, passed, calls = run("OWN-OK other " + F4_BREAK + F5_BREAK)
    assert not kept_line(lines) and any("the targeted regression repair is not kept" in l for l in lines), lines

@case
def codex_v182_r1_2_a_foundation_spec_all_skipped_after_the_repair_goes_back():

    f, lines, after, passed, calls = run("OWN-OK half " + F5_BREAK + rb.brk(rb.S1) + f"SKIPALL<{Path(rb.F1).name}>")
    assert not kept_line(lines) and any("the targeted regression repair is not kept" in l for l in lines), lines
    assert any("not kept partly (" in l for l in lines), [l for l in lines if "[tdd]" in l]

@case
def one_foundation_test_skipped_after_the_repair_goes_back():
    f, lines, after, passed, calls = run("OWN-OK half " + F5_BREAK + f"SKIPONE<{Path(rb.F1).name}>")
    assert not kept_line(lines) and any("not kept partly (fewer tests ran" in l for l in lines), [l for l in lines if "[tdd]" in l]

DATA = "backend/src/routes/data.js"
SEEDS = "SEED SENTENCES ..." + NL + "- The evaluation seed contains rows `East/1200/Open`, `North/800/Closed`."

def five_with_seed(f):
    five(f)
    (f.project_dir / DATA).parent.mkdir(parents=True, exist_ok=True)
    (f.project_dir / DATA).write_text("['East','1200','Open'],['North','800','Closed']", encoding="utf-8")

    f.__dict__.setdefault("seed_checks", {}).update({"REQ-9:values": 1, "REQ-9:names": 1, "REQ-9": 1})

@case
def codex_v182_r1_2_a_repair_that_drops_a_stated_seed_value_goes_back():
    repair = {rb.APP: "OWN-OK half " + F5_BREAK, DATA: "['East','1200','Open'],['North','','Closed']"}
    f, lines, after, passed, calls = run(repair, tweak=five_with_seed, node_extra={"seed_sentences": SEEDS})
    assert not kept_line(lines) and any("not kept partly (stated seed values gone" in l for l in lines), \
        [l for l in lines if "[tdd]" in l]
    assert "800" in (f.project_dir / DATA).read_text(encoding="utf-8") if (f.project_dir / DATA).is_file() else True

@case
def the_same_seed_kept_repair_is_still_kept():

    f, lines, after, passed, calls = run("OWN-OK half " + F5_BREAK, tweak=five_with_seed, node_extra={"seed_sentences": SEEDS})
    assert kept_line(lines), [l for l in lines if "[tdd]" in l]

@case
def a_crash_in_the_keep_decision_puts_the_state_back_as_v181():
    import main

    def boom(*a, **k):
        raise RuntimeError("boom")
    f, lines, after, passed, calls = run("OWN-OK half " + F5_BREAK, patch={"spec_skipped_counts": boom})
    assert not kept_line(lines) and any("partial-keep decision of the targeted regression repair crashed" in l for l in lines), lines
    assert any("the targeted regression repair is not kept" in l for l in lines), lines
    assert after != "OWN-OK half " + F5_BREAK, after
    assert main.spec_skipped_counts is not boom

@case
def off_the_same_half_repair_goes_back_as_v181():
    f, lines, after, passed, calls = run("OWN-OK half " + F5_BREAK, on=False)
    assert not kept_line(lines) and any("the targeted regression repair is not kept (it ended budget)" in l for l in lines), lines
    assert after != "OWN-OK half " + F5_BREAK, after

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok   {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {function.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
