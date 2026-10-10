from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.testrun import (static_test_defects, vacuous_absence_checks, vacuous_absence_hints,
                           whole_page_text_checks, V144L_HINT_HEAD)
from cases_v144l import BATCH, REL, spec

CASES: list = []

def case(function):
    CASES.append(function)
    return function

CREATE_DELETE = "  await fill(page, 'Title', name);\n  await click(page, 'Create');\n  await click(page, 'Delete');\n"
BUILT = [
    "  const name = ['Maple', 'Grove'].join('');\n" + CREATE_DELETE + "  await notSee(page, 'MapleGrove');",
    "  const name = 'Maple' + 'Grove';\n" + CREATE_DELETE + "  await notSee(page, 'MapleGrove');",
    "  const first = 'Maple';\n  const name = `${first}Grove`;\n" + CREATE_DELETE + "  await notSee(page, 'MapleGrove');",
    "  const first = 'Maple';\n  const last = 'Grove';\n  const name = first + ' ' + last;\n" + CREATE_DELETE
    + "  await notSee(page, 'Maple Grove');",
]

@case
def r49x_runtime_built_value_is_advice_not_a_defect():
    for body in BUILT:
        text = spec(body)
        assert vacuous_absence_checks(REL, text, BATCH) == [], body
        hints = vacuous_absence_hints(REL, text, BATCH)
        assert len(hints) == 1 and "is NOT shown" in hints[0] and "join of this file's own strings" in hints[0], (body, hints)
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "backend" / "test-e2e"
        folder.mkdir(parents=True)
        (folder / "REQ-5-1-2.spec.js").write_text(spec(BUILT[0]), encoding="utf-8")
        assert static_test_defects(Path(tmp), [REL], batch=BATCH) == ""

@case
def r49x_no_possible_source_stays_a_defect():

    parts_only = "  await fill(page, 'First', 'Maple');\n  await fill(page, 'Last', 'Grove');\n  await notSee(page, 'MapleGrove');"

    joins_other = "  const name = `Note ${Date.now()}`;\n" + CREATE_DELETE + "  await notSee(page, 'Add a comment');"

    arithmetic = "  const n = 1 + 2;\n" + parts_only
    for body in (parts_only, joins_other, arithmetic):
        text = spec(body)
        problems = vacuous_absence_checks(REL, text, BATCH)
        assert len(problems) == 1 and "proves nothing" in problems[0], (body, problems)
        assert vacuous_absence_hints(REL, text, BATCH) == [], body
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "backend" / "test-e2e"
        folder.mkdir(parents=True)
        (folder / "REQ-5-1-2.spec.js").write_text(spec(joins_other, BUILT[0]), encoding="utf-8")
        report = static_test_defects(Path(tmp), [REL], batch=BATCH)
        assert '"Add a comment" is NOT shown' in report and V144L_HINT_HEAD in report and '"MapleGrove"' in report, report

BODY_READ = "  const text = await page.textContent('body');\n  expect(text).toContain('Comment');"

@case
def r49x_whole_page_comparison_tracks_the_binding():
    other_test = "  const text = await page.locator('#status').textContent();\n  expect(text).toBe('Saved');"
    nested = BODY_READ + "\n  {\n    const text = await page.locator('#status').textContent();\n    expect(text).toBe('Saved');\n  }"
    parameter = BODY_READ + "\n  const check = (text) => expect(text).toBe('Saved');\n  await check(await page.locator('#s').textContent());"
    function = BODY_READ + "\n  function same(text) {\n    return text === 'Saved';\n  }"
    reassigned = ("  let text = await page.textContent('body');\n  expect(text).toContain('Comment');\n"
                  "  text = await page.locator('#status').textContent();\n  expect(text).toBe('Saved');")
    for bodies in ((BODY_READ, other_test), (nested,), (parameter,), (function,), (reassigned,)):
        problems, hints = whole_page_text_checks(REL, spec(*bodies))
        assert problems == [] and len(hints) == 1, (bodies, problems, hints)

    same_test = BODY_READ + "\n  await click(page, 'Save');\n  expect(text).toBe('Saved');"
    problems, _ = whole_page_text_checks(REL, spec(same_test, other_test))
    assert len(problems) == 1 and "REQ-5-1-2.spec.js:4:" in problems[0], problems

    problems, _ = whole_page_text_checks(REL, spec(nested + "\n  expect(text).toBe('x');"))
    assert len(problems) == 1, problems
    reread = ("  let t = await page.textContent('body');\n  const first = t;\n  await click(page, 'Save');\n"
              "  t = await page.textContent('body');\n  expect(t).not.toBe(first);")
    problems, hints = whole_page_text_checks(REL, spec(reread))
    assert len(problems) == 1 and "REQ-5-1-2.spec.js:7:" in problems[0] and len(hints) == 1, (problems, hints)

    top = "let shared;\n"
    problems, hints = whole_page_text_checks(REL, top + spec("  shared = await page.textContent('body');", "  expect(shared).toBe('x');"))
    assert problems == [] and len(hints) == 1, (problems, hints)
