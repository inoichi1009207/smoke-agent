from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_closing as cl
import cases_rollback as rb
import cases_v145a as v145a

NL = chr(10)
CASES = []
SPECS = ["backend/test-e2e/A.spec.js"]

def case(function):
    CASES.append(function)
    return function

def red_output(titles: list[str]) -> str:
    out = rb.spec_output("A.spec.js", titles)
    return "E2E FAILED against the real application" + NL + "FAILED SPEC FILES: A.spec.js" + NL + "## A.spec.js" + NL + out

def run(first: list[str], second: list[str], on: bool = True, large: bool = False, second_specs=None, lint: str = "",
        ran=(3, 3), second_tail: str = "", crash: bool = False):
    import main as factory_module

    with tempfile.TemporaryDirectory() as tmp, v145a.Settings(retry_partial_keep=on):
        root = Path(tmp)
        f = rb.make_factory(root, "template")
        f.green_specs, f.e2e_specs, f.file_specs = [], [], {}
        f.description_pass, f.description_specs, f.llm = {}, [], None
        f.over_budget = lambda: False
        f.time_left = lambda: 10 ** 6
        f.GATEWAY_PAUSE_SEC = 0.0
        f.large_tree = large

        def process_node(node, nodes_left, retry=False):
            req = node["id"]
            f.current_members = [req]
            (root / rb.APP).write_text("retry" if retry else "first attempt", encoding="utf-8")
            titles = second if retry else first
            f.__dict__["last_check_evidence"] = {
                "digest": factory_module.sources_digest(f.snapshot_sources()),
                "output": red_output(titles) + (second_tail if retry else ""), "passed": False,
                "lint": lint if retry else "", "large": large, "specs": list(second_specs or SPECS) if retry else list(SPECS),
                "ran": ran[1] if retry else ran[0]}
            f.report["nodes"][req] = {"status": "budget", "e2e": "failed", "tests": list(SPECS), "written": [rb.APP], "test_runs": 1}
            return False
        f.process_node = process_node
        saved = factory_module.partial_repair_keepable
        if crash:
            def boom(*a, **k):
                raise RuntimeError("boom")
            factory_module.partial_repair_keepable = boom
        try:
            result, lines = cl._queue(f, "A")
        finally:
            factory_module.partial_repair_keepable = saved
        app = (root / rb.APP).read_text(encoding="utf-8")
    assert any("foundation retry ended red" in l for l in lines), lines
    return app, lines

@case
def a_retry_that_fails_fewer_tests_and_no_new_one_is_kept():
    app, lines = run(["A: one", "A: two", "A: three"], ["A: three"])
    assert app == "retry" and any("the red retry is kept: 2 failure(s) gone" in l for l in lines), lines
    assert not any("the retry is not kept" in l for l in lines), lines

@case
def a_retry_with_a_new_failure_goes_back():
    app, lines = run(["A: one", "A: two"], ["A: four"])
    assert app == "first attempt" and any("the retry is not kept" in l for l in lines), lines
    assert any("is not better per test (new failure(s)" in l for l in lines), lines

@case
def equal_failures_go_back():
    app, lines = run(["A: one"], ["A: one"])
    assert app == "first attempt" and any("the retry is not kept" in l for l in lines), lines

@case
def different_specs_a_new_lint_or_a_large_tree_go_back():
    for kwargs in ({"second_specs": SPECS + ["backend/test-e2e/B.spec.js"]}, {"lint": "missing noValidate"}, {"large": True}):
        app, lines = run(["A: one", "A: two"], ["A: two"], **kwargs)
        assert app == "first attempt" and any("the retry is not kept" in l for l in lines), (kwargs, lines)

@case
def codex_v182_r1_1_tests_skipped_after_the_retry_block_the_keep():

    for kwargs in ({"ran": (3, 2)}, {"ran": (3, 0)}, {"ran": (3, -1)}, {"ran": (None, 3)}, {"ran": (3, 3), "second_tail": NL + "  1 skipped"}):
        app, lines = run(["A: one", "A: two", "A: three"], ["A: three"], **kwargs)
        assert app == "first attempt" and any("the retry is not kept" in l for l in lines), (kwargs, lines)
        assert any("is not better per test" in l and ("fewer tests ran" in l or "not known" in l or "more tests skipped" in l)
                   for l in lines), (kwargs, lines)

@case
def a_crash_in_the_keep_decision_puts_the_first_attempt_back():
    app, lines = run(["A: one", "A: two", "A: three"], ["A: three"], crash=True)
    assert app == "first attempt" and any("the retry is not kept" in l for l in lines), lines
    assert any("the keep decision crashed" in l for l in lines), lines

@case
def off_a_better_red_retry_goes_back_as_v181():
    app, lines = run(["A: one", "A: two", "A: three"], ["A: three"], on=False)
    assert app == "first attempt" and any("the retry is not kept" in l for l in lines), lines
    assert not any("the red retry" in l for l in lines), lines

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
