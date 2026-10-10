from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

CASES = []

def case(function):
    CASES.append(function)
    return function

QUIET = lambda message: None

@case
def a_batch_kept_owing_a_regression_books_its_own_spec_as_green():
    import cases_engine142 as e
    import cases_rollback as rb

    out = e.run_node("base", [{"checks": [{rb.APP: "base OWN-OK " + e.brk5()}]}], green=e.GREEN_4)
    f = out["f"]
    assert out["passed"] and f.regression_debts == ["REQ-9"], out["lines"]
    assert rb.OWN in f.green_specs, f.green_specs
    assert e.F5 not in f.green_specs
    assert rb.OWN in f.file_specs.get(rb.APP, []), f.file_specs
    assert f.green_specs[:3] == e.GREEN_4[:3]

@case
def a_batch_put_back_to_its_best_state_books_its_own_spec_as_green():
    import cases_engine142 as e
    import cases_rollback as rb

    best = "base OWN-OK " + e.brk5()
    out = e.run_node("base", [{"checks": [{rb.APP: best}, {rb.APP: "base " + rb.brk(rb.S1)}]}], green=e.GREEN_4)
    f = out["f"]
    assert out["app"] == best and out["passed"] and f.regression_debts == ["REQ-9"], out["lines"]
    assert rb.OWN in f.green_specs and e.F5 not in f.green_specs, f.green_specs
    assert rb.OWN in f.file_specs.get(rb.APP, []), f.file_specs

@case
def a_red_or_put_back_batch_still_books_nothing():
    import cases_engine142 as e
    import cases_rollback as rb

    out = e.run_node("base", [dict(e.RED_BUDGET_UNIT)], green=e.GREEN_4)
    assert not out["passed"] and rb.OWN not in out["f"].green_specs, out["lines"]

    out = e.run_node("base", [{"checks": [{rb.APP: "base OWN-OK " + rb.brk(rb.S1)}, {rb.APP: "base " + rb.brk(rb.S1) + " x"}]}],
                     green=e.GREEN_4)
    assert out["app"] == "base" and not out["passed"] and rb.OWN not in out["f"].green_specs, out["lines"]

WORKBOOKS = ("Users view available workbooks on the workbook home page. Each record displays the workbook name and provides "
             "a link whose accessible name is the workbook name.\nThe seeded workbook `Plan` is archived. The seeded "
             "workbook `Plan: Final` is not archived.")

def _page(aria: str) -> dict:
    return {"route": "/", "url": "http://127.0.0.1:3300/", "aria": aria, "links": []}

def _run(whole: str, aria: str):
    from agent.entry_checks import entry_findings, statements
    lines: list[str] = []
    found, counts = entry_findings(statements(whole), [_page(aria)], [], log=lines.append)
    return found, counts, lines

@case
def a_link_named_with_another_declared_record_name_is_not_reported():
    from agent.entry_checks import statements
    assert statements(WORKBOOKS)["exact"][0]["seeds"] == ["Plan", "Plan: Final"]
    aria = '- list:\n  - listitem:\n    - link "Plan: Final":\n      - /url: /workbooks/2\n'
    found, counts, lines = _run(WORKBOOKS, aria)
    assert counts["entry_exact_name"] == 0 and found == [], found
    assert any("holds another declared workbook name" in line for line in lines), lines

@case
def the_longer_declared_name_is_still_checked_for_its_own_metadata():

    aria = "- list:\n  - listitem:\n    - 'link \"Plan: FinalLast updated: 2026-09-29\"':\n      - /url: /workbooks/2\n"
    found, counts, _lines = _run(WORKBOOKS, aria)
    assert counts["entry_exact_name"] == 1, found
    assert "“Plan: Final”" in found[0]["problem"] and "“Plan”" not in found[0]["problem"], found

    alone = WORKBOOKS.replace(" The seeded workbook `Plan: Final` is not archived.", "")
    aria = '- list:\n  - listitem:\n    - link "Plan: Final":\n      - /url: /workbooks/2\n'
    assert _run(alone, aria)[1]["entry_exact_name"] == 1

@case
def a_covers_range_expands_to_every_number_in_it():
    from agent import coverage
    assert coverage._refs("covers S1-S16") == set(range(1, 17))
    assert coverage._refs("covers S3 to S40") == set(range(3, 41))
    assert coverage._refs("covers S1-S5, S9") == {1, 2, 3, 4, 5, 9}
    assert coverage._refs("covers S9-S2") == {9}

    text = " ".join(f"The list keeps “Entry {n}” after a reload." for n in range(1, coverage.CAP + 1))
    leaf = {"id": "REQ-9", "name": "Drafts", "description": text, "scenarios": []}
    node = dict(leaf, members=["REQ-9"], _leaves=[leaf])
    assert len(coverage.ledger(node)[0][1]) == coverage.CAP == 16
    spec = ('const { test } = require("@playwright/test");\n// covers S1-S16\n'
            'test("REQ-9: all", async ({ page }) => { await page.goto("/"); });\n')
    assert coverage.uncovered(node, spec) == [], coverage.uncovered(node, spec)[:3]

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
    print(f"{len(CASES) - failed}/{len(CASES)} r45a cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
