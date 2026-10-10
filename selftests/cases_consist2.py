from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = []

def case(function):
    CASES.append(function)
    return function

BASE = ("The page has a button named “Upload”. Uploading shows the message “Upload done”. The file remains listed "
        "after a reload of the page. A file over the limit is rejected with “File too large”. Only the owner can "
        "see the button “Delete”. ")
TEST = 'test("REQ-9: upload", async ({ page }) => { await page.goto("/"); });\n'

def _node(leaf_id: str, description: str) -> dict:
    leaf = {"id": leaf_id, "name": "Uploads", "description": description, "scenarios": []}
    return {"id": leaf_id, "name": "Uploads", "members": [leaf_id], "_leaves": [leaf]}

def _numbers(found) -> list[int]:
    return [number for _leaf, number, _text in found]

@case
def a_duration_or_a_look_does_not_make_persistence_testable():
    from agent import coverage
    for text in ("The toast remains visible for 5 seconds.", "The success banner stays on screen for 3 seconds.",
                 "The active tab stays highlighted while its panel is open.",
                 "The selected cell keeps its border after the grid scrolls.", "The hint is shown briefly and then no longer shown.",
                 "选中的单元格保持高亮直到用户点击其他单元格。"):
        assert not coverage.testable_by_construction(text), text

    for text in ("The list keeps its order.", "The import still runs in the background.",
                 "The red badge still shows the count.", "The highlighted row remains selected after a reload of the page.",
                 "The “Total” row is shown in bold.", "The “Saved” message disappears after 3 seconds.",
                 "The toast remains visible for 5 seconds and reads “Saved”.", "Row 2 stays in place when a second tab opens."):
        assert coverage.testable_by_construction(text), text

@case
def a_timing_statement_can_be_marked_and_a_real_one_cannot():
    from agent import coverage
    node = _node("REQ-9", BASE + "The confirmation toast remains visible for 5 seconds.")
    texts = [t for _leaf, numbered in coverage.ledger(node) for _n, t in numbered]
    assert len(texts) == 6 and texts[-1] == "The confirmation toast remains visible for 5 seconds.", texts
    spec = "// covers S1-S5\n" + TEST + "// not testable: S6 -- a duration; no timing assertions\n"
    assert coverage.refused_marks(node, spec) == [] and coverage.uncovered(node, spec) == []
    assert coverage.not_testable(node, spec) == [6]

    real = _node("REQ-9", BASE + "The confirmation toast remains visible until the user closes it.")
    assert [n for n, _r in coverage.refused_marks(real, spec)] == [6] and _numbers(coverage.uncovered(real, spec)) == [6]

    three = "// covers S1-S3\n" + TEST + "// not testable: S4-S6 -- x\n"
    assert _numbers(coverage.uncovered(node, three)) == [4, 5, 6]
    assert "3 of 6 marked, at most 2" in coverage.refused_marks(node, three)[0][1]

@case
def the_split_alternatives_of_one_clause_spend_one_mark():
    from agent import coverage
    clause = "Pressing Escape, clicking outside, scrolling or tabbing away keeps the cell highlighted."
    parts = coverage.alternatives(clause)
    assert len(parts) == 4 and all(not coverage.testable_by_construction(p) for p in parts), parts
    node = _node("REQ-8", BASE + "The server validates every upload before it opens the preview. " + clause)
    texts = [t for _leaf, numbered in coverage.ledger(node) for _n, t in numbered]
    assert len(texts) == 10 and texts[6:] == parts, texts

    spec = "// covers S1-S5\n" + TEST + "// not testable: S6 -- server side\n// not testable: S7-S10 -- a look\n"
    assert coverage.refused_marks(node, spec) == [] and coverage.uncovered(node, spec) == []

    over = "// covers S2-S5\n" + TEST + "// not testable: S1 -- x\n// not testable: S6 -- server side\n// not testable: S7-S10 -- a look\n"
    refused = coverage.refused_marks(node, over)
    assert [n for n, _r in refused] == [1, 6, 7, 8, 9, 10] and "3 of 7 marked, at most 2" in refused[0][1], refused

    other = _node("REQ-7", BASE + "Pressing Escape keeps the cell highlighted. Clicking outside keeps the row highlighted. "
                  "The server validates every upload before it opens the preview.")
    both = "// covers S1-S5\n" + TEST + "// not testable: S6, S7, S8 -- a look / server side\n"
    assert "3 of 8 marked, at most 2" in coverage.refused_marks(other, both)[0][1]

@case
def the_rule_text_names_the_exception_and_stays_short():
    from agent import coverage
    rules = coverage.LEDGER_HEAD + coverage.LEDGER_RULES
    assert "unless it persists only for a time or a look" in coverage.LEDGER_RULES and len(rules) < 700, len(rules)

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
    print(f"{len(CASES) - failed}/{len(CASES)} consist2 cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
