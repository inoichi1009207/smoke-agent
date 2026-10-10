from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.testrun import (exact_name_contains, static_test_defects, unreloaded_writes, unscoped_account_names,
                           vacuous_absence_checks, whole_page_text_checks, V144L_HINT_HEAD)

CASES: list = []

def case(function):
    CASES.append(function)
    return function

REL = "backend/test-e2e/REQ-5-1-2.spec.js"
REQUIREMENT = '''id: ROOT
name: Tracker
children:
  - id: REQ-5
    name: Issues
    description: Issues of a repository.
    children:
      - id: REQ-5-1-2
        name: Comment on an issue
        description: >-
          A signed-in user writes in the "Comment" box and clicks "Comment". The list shows the link "Improve onboarding".
          After a push the page shows the message `Branch <branch name>`, and an upload shows `Add <file name>`.
          Every header of the table has a button "Filter <header text>"; the seed table has headers `Region/Sales/Status`.
        scenarios:
          - name: Scenario 1
            steps:
              - content: GIVEN a visitor opens issue "Improve onboarding" WHEN the page loads THEN no "Comment" box is shown
'''
BATCH = {"requirement_text": REQUIREMENT, "members": ["REQ-5-1-2"]}
HEAD = '''const { test } = require("@playwright/test");
const { expect, click, fill, see, notSee, locate, item, region } = require("../e2e-support/outsider");
'''

def spec(*bodies: str) -> str:
    return HEAD + "".join(f'test("REQ-5-1-2: t{i}", async ({{ page }}) => {{\n{b}\n}});\n' for i, b in enumerate(bodies))

def found(text: str) -> list[str]:
    return vacuous_absence_checks(REL, text, BATCH)

@case
def v144l_vacuous_not_see_is_reported():
    hits = found(spec('  await notSee(page, { role: "textbox", name: "Add a comment" });',
                      '  await notSee(page, "Edit everything");'))
    assert len(hits) == 2, hits
    assert '"Add a comment" is NOT shown' in hits[0] and "REQ-5-1-2.spec.js:4:" in hits[0], hits[0]
    assert "proves nothing" in hits[0] and '"Edit everything"' in hits[1], hits

    assert found(spec('  await notSee(page, "Add a comment");')), "prose filling of a template"

@case
def v144l_vacuous_not_see_silent_where_unsure():
    silent = [
        '  await notSee(page, { role: "textbox", name: "Comment" });',
        '  await notSee(page, "comment");',
        '  await fill(page, "Title", "Draft note");\n  await notSee(page, "Draft note");',
        '  await notSee(page, "Issue 42");',
        '  const gone = "Obsolete thing";\n  await notSee(page, gone);',
        '  const s = Date.now();\n  await notSee(page, `Gone ${s}`);',
        '  await notSee(page, /nothing here/i);',
        '  await notSee(page, { role: "dialog" });',
        '  await fill(page, "Branch", "release");\n  await notSee(page, "Branch release");',
        '  await notSee(page, "Improve onboarding/Comment");',
        '  await notSee(page, "Filter Status");',
    ]
    for body in silent:
        assert found(spec(body)) == [], body
    assert vacuous_absence_checks(REL, spec('  await notSee(page, "Add a comment");'), {"members": ["REQ-5-1-2"]}) == []
    assert vacuous_absence_checks(REL, spec('  await notSee(page, "Add a comment");'), None) == []
    assert vacuous_absence_checks("backend/tests/x.test.js", spec('  await notSee(page, "Add a comment");'), BATCH) == []

WHOLE_COMPARED = [
    '  const a = await page.textContent("body");\n  await click(page, "Improve onboarding");\n'
    '  const b = await page.textContent("body");\n  expect(b).not.toBe(a);',
    '  expect(await page.locator("body").innerText()).toBe("Improve onboarding");',
    "  const html = await page.content();\n  expect(html).toMatchSnapshot();",
    '  const t = await page.evaluate(() => document.body.innerText);\n  if (t === "x") throw new Error("same");',
]

@case
def v144l_whole_page_comparison_is_a_defect():
    for body in WHOLE_COMPARED:
        problems, hints = whole_page_text_checks(REL, spec(body))
        assert problems and "WHOLE page" in problems[0] and hints == [], (body, problems, hints)
    problems, _ = whole_page_text_checks(REL, spec(WHOLE_COMPARED[0]))
    assert len(problems) == 2 and "REQ-5-1-2.spec.js:4:" in problems[0], problems

@case
def v144l_whole_page_search_is_advice_and_element_text_silent():
    problems, hints = whole_page_text_checks(REL, spec('  expect(await page.textContent("body")).toContain("Comment");'))
    assert problems == [] and len(hints) == 1 and "whole page" in hints[0], (problems, hints)
    for body in ('  const row = item(page, "Improve onboarding");\n  expect(await row.textContent()).toBe("x");',
                 '  expect(await page.textContent("main")).toBe("x");',
                 "  await see(page, \"page.textContent('body') is not used\");",
                 '  // const a = await page.textContent("body"); expect(a).toBe(b);'):
        assert whole_page_text_checks(REL, spec(body)) == ([], []), body

@case
def v144l_exact_name_contains_is_advice_only_where_the_name_is_quoted():
    hint = exact_name_contains(REL, spec('  await expect(locate(page, "Improve onboarding")).toContainText("#");'), BATCH)
    assert len(hint) == 1 and 'locate("Improve onboarding")' in hint[0] and "item(page" in hint[0], hint
    assert exact_name_contains(REL, spec('  await expect(locate(page, { role: "link", name: "Improve onboarding" })).toContainText("#");'), BATCH)
    for body in ('  await expect(locate(page, { role: "gridcell", name: "B1" })).toContainText("5");',
                 '  await expect(locate(page, { role: "button", name: "Improve onboarding" })).toContainText("alice");',
                 '  await expect(locate(page, "Improve onboarding")).toContainText("onboarding");',
                 '  await expect(locate(page, "Signed-in user")).toContainText("alice");'):
        assert exact_name_contains(REL, spec(body), BATCH) == [], body

@case
def v144l_unreloaded_write_is_advice():
    hint = unreloaded_writes(REL, spec('  await click(page, "Comment");\n  await click(page, { role: "button", name: "Save" });\n'
                                       '  await see(page, "Saved");'))
    assert len(hint) == 1 and '(clicks "Save")' in hint[0] and "page.reload()" in hint[0], hint
    assert unreloaded_writes(REL, spec('  await click(page, "Save");\n  await page.reload();\n  await see(page, "x");')) == []
    assert unreloaded_writes(REL, spec('  await click(page, "Close");\n  await notSee(page, "x");')) == []

    rejected = HEAD + 'test("REQ-5-1-2: an empty title is rejected", async ({ page }) => {\n  await click(page, "Save");\n});\n'
    assert unreloaded_writes(REL, rejected) == []
    assert unreloaded_writes(REL, spec('  await click(page, "Save");\n  await click(page, "Issues");\n  await see(page, "x");')) == []
    assert unreloaded_writes(REL, spec('  await click(page, "Create");\n  await page.reload();\n  await see(page, "x");\n'
                                       '  await click(page, "Delete");')) == []
    assert unreloaded_writes(REL, spec('  await click(page, "Archive");\n  await see(page, "x");')) == []

    assert unreloaded_writes(REL, spec('  await click(page, "Create account");\n  await see(page, "Username already exists");')) == []
    helper = 'async function openIssue(page) {\n  await page.goto("/issues/1");\n}\n'
    assert unreloaded_writes(REL, helper + spec('  await click(page, "Save");\n  await openIssue(page);\n  await see(page, "x");')) == []
    assert unreloaded_writes(REL, helper + spec('  await openIssue(page);\n  await click(page, "Save");\n  await see(page, "x");'))

    assert unreloaded_writes(REL, spec('  await click(page, "Save");\n  await click(page, "Cancel");\n  await see(page, "x");'))

@case
def v144l_unscoped_account_name_is_advice():
    signs_in = '''async function signIn(page, who) {
  await fill(page, "Username or email", who);
  await fill(page, "Password", "pw-123456");
  await click(page, "Sign in");
  await page.waitForLoadState();
  await see(page, "bob-reviewer");
}
'''
    opened = '''  await click(page, "Improve onboarding");
'''
    hint = unscoped_account_names(REL, signs_in + spec('''  await signIn(page, "bob-reviewer");
''' + opened + '  await see(page, "bob-reviewer");'))
    assert len(hint) == 1 and 'line 13 "bob-reviewer"' in hint[0] and "line 5 " not in hint[0], hint
    form = '''  await fill(page, "Username", "alice-dev");
  await fill(page, "Password", "pw-123456");
  await click(page, "Sign in");
'''
    assert unscoped_account_names(REL, spec(form + opened + '  await see(page, "alice-dev");'))

    for body in (form + '  await see(page, "alice-dev");',
                 form + opened + '  await see(page, "alice-dev", item(page, "Improve onboarding"));',
                 form + opened + '  await see(page, "bob-reviewer");',
                 '''  await fill(page, "Username", "bob-reviewer");
  await click(page, "Add member");
''' + opened + '  await see(page, "bob-reviewer");',
                 '''  await fill(page, "Title", "alice-dev");
''' + opened + '  await see(page, "alice-dev");'):
        assert unscoped_account_names(REL, spec(body)) == [], body
    assert unscoped_account_names(REL, signs_in + spec('  await signIn(page, "bob-reviewer");')) == []

    persisted = form + '  await see(page, { role: "button", name: "Account menu" });\n  await page.reload();\n  await see(page, "alice-dev");'
    assert unscoped_account_names(REL, spec(persisted)) == []
    registration = form.replace('"Sign in"', '"Create account"') + opened + '  await see(page, "alice-dev");'
    assert unscoped_account_names(REL, spec(registration)) == []
    visitor = spec(form + opened + '  await see(page, "alice-dev", item(page, "x"));', opened + '  await see(page, "alice-dev");')
    assert unscoped_account_names(REL, visitor) == [], visitor

@case
def v144l_wired_into_e1_advice_only_with_a_defect():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "backend" / "test-e2e"
        folder.mkdir(parents=True)
        (folder / "REQ-5-1-2.spec.js").write_text(spec('  await click(page, "Save");\n  await see(page, "Comment");',
                                                       '  await notSee(page, "Add a comment");'), encoding="utf-8")
        (folder / "REQ-5-1-3.spec.js").write_text(spec('  await click(page, "Save");\n  await see(page, "Comment");'),
                                                  encoding="utf-8")
        report = static_test_defects(Path(tmp), [REL], batch=BATCH)
        assert '"Add a comment" is NOT shown' in report and V144L_HINT_HEAD in report and '(clicks "Save")' in report, report

        assert static_test_defects(Path(tmp), ["backend/test-e2e/REQ-5-1-3.spec.js"], batch=BATCH) == ""

        (folder / "REQ-5-1-4.spec.js").write_text(spec(WHOLE_COMPARED[1]), encoding="utf-8")
        assert "WHOLE page" in static_test_defects(Path(tmp), ["backend/test-e2e/REQ-5-1-4.spec.js"])

        assert "NOT shown" not in static_test_defects(Path(tmp), [REL])
