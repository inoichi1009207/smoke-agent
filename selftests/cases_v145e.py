from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import outsider
from agent.testrun import _seed_contents, seeded_value_absence, static_test_defects, value_from_nowhere

NL = chr(10)
CASES: list = []

def case(function):
    CASES.append(function)
    return function

REL = "backend/test-e2e/REQ-1-1-1-1.spec.js"
HEAD = ('const { test } = require("@playwright/test");' + NL
        + 'const { click, see, notSee, fill, press, locate, region, expect } = require("../e2e-support/outsider");' + NL)
SEEDS = ("SEED SENTENCES OF THE WHOLE REQUIREMENT (records that exist before any test; use them by these names):" + NL
         + "- The evaluation seed contains the seeded workbook `Q3 Sales`, worksheet `Sheet1`, and cell A1 value `Region`." + NL
         + "- The evaluation seed contains the seeded worksheet range `A1:C6` with headers `Region/Sales/Status`." + NL
         + "- The seeded workbook `Budget` has worksheets `Jan`, `Feb` and cell A1 value `Total`." + NL + NL
         + "SEED RECORDS WITH DIFFERENT CONTENTS" + NL + "- never relies on the cell contents of `Q3 Sales` value `Q3 Sales`")
REQUIREMENT = "id: REQ-1\nname: Workbooks\ndescription: The user creates a workbook and names it.\n"
BATCH = {"seed_sentences": SEEDS, "requirement_text": REQUIREMENT, "ancestor_context": ""}

def test(title: str, *body: str) -> str:
    return f'test("{title}", async ({{ page }}) => {{' + NL + NL.join("  " + b for b in body) + NL + "});" + NL

OPEN = ('await page.goto("/");', 'await click(page, { role: "link", name: "Q3 Sales" });')
SEES = test("REQ-1-1-1: opening shows the data", *OPEN, 'await see(page, "Region", region(page, "grid", "Worksheet grid"));')

def absent(*extra: str, scope: str = 'region(page, "grid", "Worksheet grid")', pair: bool = True, before: tuple = ()) -> str:
    look = f'await notSee(page, "Region", {scope});' if scope else 'await notSee(page, "Region");'
    return HEAD + (SEES if pair else "") + test("REQ-1-1-1: no data from another workbook", *before, *OPEN, *extra, look)

def a_lint(text: str) -> tuple[list[str], list[str]]:
    return seeded_value_absence(REL, text, BATCH)

@case
def a_the_platform_pair_is_a_defect_scoped_and_unscoped():
    problems, _hints = a_lint(absent())
    assert len(problems) == 1 and "REQ-1-1-1-1.spec.js:" in problems[0] and '"Region" is NOT shown' in problems[0], problems
    unscoped = HEAD + test("t1", *OPEN, 'await see(page, "Region");') + test("t2", *OPEN, 'await notSee(page, "Region");')
    assert len(a_lint(unscoped)[0]) == 1
    line = next(i for i, l in enumerate(absent().splitlines(), 1) if "await notSee" in l)
    assert f"REQ-1-1-1-1.spec.js:{line}:" in problems[0]

@case
def a_without_the_see_unscoped_is_advice_and_scoped_is_nothing():
    problems, hints = a_lint(absent(scope="", pair=False))
    assert problems == [] and len(hints) == 1, (problems, hints)
    assert a_lint(absent(pair=False)) == ([], [])

@case
def a_steps_that_may_explain_the_absence_silence_it():
    for extra in ('await press(page, "Delete");', 'await fill(page, "A1", "");', 'await click(page, "Rename");',
                  'await click(page, { role: "tab", name: "Sheet2" });', 'await page.goto("/settings");',
                  'await clearCell(page);', 'await page.keyboard.press("Delete");', 'await locate(page, "A1").fill("");'):
        assert a_lint(absent(extra)) == ([], []), extra
    assert a_lint(absent(before=('await click(page, "New blank workbook");',))) == ([], [])
    helper = "async function wipe(page) { await fill(page, \"A1\", \"\"); }" + NL
    assert a_lint(helper + absent("await wipe(page);")) == ([], [])
    for hook in ('test.beforeEach(async ({ page }) => { await page.goto("/"); });' + NL,
                 'test.describe.configure({ mode: "serial" });' + NL):
        assert a_lint(hook + absent()) == ([], []), hook

@case
def a_the_same_way_means_the_same_steps_and_scope():
    other_scope = absent(scope='region(page, "grid", "Formula bar")')
    assert a_lint(other_scope)[0] == []
    checkbox = HEAD + SEES + test("t2", 'await page.goto("/");', 'await click(page, { role: "checkbox", name: "Q3 Sales" });',
                                  'await notSee(page, "Region", region(page, "grid", "Worksheet grid"));')
    assert a_lint(checkbox)[0] == []
    skipped = HEAD + SEES + "test.skip" + test("t2", *OPEN, 'await notSee(page, "Region", region(page, "grid", "Worksheet grid"));')[4:]
    assert a_lint(skipped) == ([], [])
    failing = HEAD + SEES + "test.fail" + test("t2", *OPEN, 'await notSee(page, "Region", region(page, "grid", "Worksheet grid"));')[4:]
    assert a_lint(failing) == ([], [])

@case
def a_the_seed_reading_takes_only_contents_of_an_opened_record():
    assert [(v, h) for v, h, _s in _seed_contents(BATCH)] == [("Region", ["Q3 Sales"])]
    name = HEAD + test("t1", *OPEN, 'await see(page, "Q3 Sales");') + test("t2", *OPEN, 'await notSee(page, "Q3 Sales");')
    assert a_lint(name) == ([], [])
    several = dict(BATCH, seed_sentences=SEEDS.replace("- The seeded workbook `Budget`",
                                                       "- The seeded workbook `Stock` has cell A1 value `East/1200`." + NL + "- The seeded workbook `Budget`"))
    cells = HEAD + test("t1", 'await click(page, "Stock");', 'await see(page, "East/1200");') + test(
        "t2", 'await click(page, "Stock");', 'await notSee(page, "East/1200");')
    assert seeded_value_absence(REL, cells, several) == ([], [])
    budget = HEAD + test("t1", 'await click(page, "Budget");', 'await see(page, "Total");') + test(
        "t2", 'await click(page, "Budget");', 'await notSee(page, "Total");')
    assert a_lint(budget) == ([], [])
    assert seeded_value_absence("backend/tests/REQ-1.test.js", absent(), BATCH) == ([], [])
    assert seeded_value_absence(REL, absent(), {}) == ([], [])

CREATE = ("async function createWorkbook(page, name) {" + NL
          + '  await click(page, { role: "button", name: "New blank workbook" });' + NL
          + '  await click(page, { role: "button", name: "Create" });' + NL + "}" + NL)
NAMED = ('const name = "WB " + Date.now();', 'await page.goto("/");', "await createWorkbook(page, name);",
         'await expect(locate(page, "Workbook name")).toHaveValue(name);', "await see(page, name);")

def b_lint(text: str) -> tuple[list[str], list[str]]:
    return value_from_nowhere(REL, text, BATCH)

@case
def b_the_platform_shape_is_a_defect():
    text = HEAD + CREATE + test("REQ-1-2-2: rename", *NAMED)
    problems, hints = b_lint(text)
    assert len(problems) == 1 and hints == [] and "never types it" in problems[0], (problems, hints)
    line = next(i for i, l in enumerate(text.splitlines(), 1) if "toHaveValue(name)" in l)
    assert f"REQ-1-1-1-1.spec.js:{line}:" in problems[0]
    template = HEAD + CREATE + test("t", "const name = `WB ${Date.now()}`;", "await createWorkbook(page, name);", "await see(page, name);")
    assert len(b_lint(template)[0]) == 1

@case
def b_any_way_the_value_can_reach_the_page_silences_it():
    fills = (CREATE.replace('  await click(page, { role: "button", name: "Create" });',
                            '  await fill(page, "Workbook name", name);'),
             CREATE.replace('  await click(page, { role: "button", name: "Create" });',
                            '  await fill(page, "Workbook name", `${name}`);'),
             CREATE.replace('  await click(page, { role: "button", name: "Create" });',
                            "  await page.keyboard.type(name);"),
             "async function createWorkbook(page) { await fill(page, \"Workbook name\", name); }" + NL,
             "async function createWorkbook({ page }, name) { await fill(page, \"Workbook name\", name); }" + NL,
             "async function createWorkbook(page, name) { await request.post(\"/api/workbooks\", { data: { name } }); }" + NL)
    for helper in fills:
        text = HEAD + helper + test("t", *NAMED)
        if "createWorkbook(page)" in helper:
            text = HEAD + helper + 'const name = "WB " + Date.now();' + NL + test("t", 'await createWorkbook(page);', "await see(page, name);")
        if "{ page }" in helper:
            text = text.replace("await createWorkbook(page, name);", "await createWorkbook({ page }, name);")
        assert b_lint(text) == ([], []), helper
    for step in ('await fill(page, "Workbook name", name);', "await typeText(page, name);",
                 'const full = name + " copy"; await fill(page, "Name", full);'):
        assert b_lint(HEAD + CREATE + test("t", NAMED[0], step, "await see(page, name);")) == ([], []), step
    stamp = ('const stamp = Date.now();', 'const name = "WB " + stamp;', 'await fill(page, "Name", "WB " + stamp);', "await see(page, name);")
    assert b_lint(HEAD + test("t", *stamp)) == ([], [])
    built = ("const name = makeName() + Date.now();", "await createWorkbook(page, name);", "await see(page, name);")
    assert b_lint(HEAD + CREATE + test("t", *built)) == ([], [])
    assert b_lint(HEAD + CREATE + "test.skip" + test("t", *NAMED)[4:]) == ([], [])

@case
def b_a_literal_nobody_states_is_advice_and_a_stated_one_nothing():
    invented = HEAD + test("t", 'const repo = "hidden-repo";', "await click(page, repo);", "await see(page, repo);")
    problems, hints = b_lint(invented)
    assert problems == [] and len(hints) == 1, (problems, hints)
    stated = HEAD + test("t", 'const title = "Workbooks";', "await see(page, title);")
    assert b_lint(stated) == ([], [])

@case
def b_each_binding_is_read_in_its_own_test():
    typed = test("types", 'const name = "WB " + Date.now();', 'await fill(page, "Name", name);', "await see(page, name);")
    looked = test("looks", *NAMED)
    problems, _h = b_lint(HEAD + CREATE + typed + looked)
    assert len(problems) == 1 and "toHaveValue(name)" in problems[0], problems

@case
def c_gate_e1_carries_both_and_the_prompt_teaches_both():
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        target = project / REL
        target.parent.mkdir(parents=True)
        target.write_text(absent(), encoding="utf-8")
        report = static_test_defects(project, [REL], batch=BATCH)
        assert '"Region" is NOT shown' in report, report
        target.write_text(HEAD + CREATE + test("REQ-1-2-2: rename", *NAMED), encoding="utf-8")
        assert "never types it" in static_test_defects(project, [REL], batch=BATCH)
        target.write_text(absent(scope="", pair=False), encoding="utf-8")
        assert "Region" not in static_test_defects(project, [REL], batch=BATCH)
    prompt = outsider.HELPER_JS + "".join(v for v in vars(outsider).values() if isinstance(v, str))
    assert "A value the seed puts IN a record" in prompt and "absent (notSee) only after a step of the same test" in prompt
    assert "TYPE it (fill) into its field before any check expects it shown" in prompt

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
