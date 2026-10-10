from __future__ import annotations

import re
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import outsider
from agent.testrun import (SINGLE_ELEMENT_HELPERS, single_element_count_findings, single_element_count_hints,
                           single_element_counts, static_test_defects)

NL = chr(10)
CASES: list = []

def case(function):
    CASES.append(function)
    return function

REL = "backend/test-e2e/REQ-1-1-1-1.spec.js"
HEAD = ('const { test } = require("@playwright/test");' + NL
        + 'const { click, see, locate, locateAll, item, region, tagged, expect } = require("../e2e-support/outsider");' + NL)

def spec(*body: str) -> str:
    return HEAD + NL + 'test("REQ-1-2-1: reopening", async ({ page }) => {' + NL + NL.join("  " + b for b in body) + NL + "});" + NL

def lint(*body: str) -> list[str]:
    return single_element_counts(REL, spec(*body))

def hints(*body: str) -> list[str]:
    return single_element_count_hints(REL, spec(*body))

def helper_body(name: str) -> str:
    return outsider.HELPER_JS.split(f"function {name}(")[1].split("\n}")[0]

@case
def the_single_element_helpers_are_the_ones_that_end_in_first_or_last():
    assert set(SINGLE_ELEMENT_HELPERS) == {"locate", "item", "region"}
    for name in SINGLE_ELEMENT_HELPERS:
        body = helper_body(name)
        assert ".first()" in body or ".last()" in body, name
    for name in ("locateAll", "tagged"):
        body = helper_body(name)
        assert ".first()" not in body and ".last()" not in body, name

    assert "function locate(page, name, within) { return locateAll(page, name, within).first(); }" in outsider.HELPER_JS
    assert "filter({ visible: true });" in helper_body("locateAll")

@case
def locate_all_is_exported_imported_by_the_prompt_and_taught_next_to_locate():
    exported = set(re.search(r"module\.exports = \{([^}]*)\}", outsider.HELPER_JS).group(1).replace(" ", "").split(","))
    taught = set(re.search(r"const \{([^}]*)\} = require\(\"\.\./e2e-support/outsider\"\)", outsider.SYSTEM_PROMPT)
                 .group(1).replace(" ", "").split(","))
    assert "locateAll" in exported and "locateAll" in taught and taught <= exported, sorted(taught - exported)
    prompt = outsider.SYSTEM_PROMPT
    at = prompt.index('await expect(locate(page, { role: "tab", name: "Overdue" }))')
    near = prompt[at:at + 500]
    assert "locate / item / region are ONE element" in near and "await expect(locateAll(page, " in near and ".toHaveCount(3)" in near

@case
def the_sheet_v144_spec_is_rejected():

    body = ['const links = locate(page, { role: "link" });', "const before = await links.count();",
            'await click(page, "New workbook");', 'await page.goto("/");', "await expect(links).toHaveCount(before + 1);"]
    assert lint(*body) == [], lint(*body)
    advice = hints(*body)
    assert len(advice) == 1, advice
    assert advice[0].startswith(REL + ":9: `await expect(links).toHaveCount(before + 1);` counts locate(...)"), advice[0]
    found = lint(*body[:-1], "await expect(links).toHaveCount(2);")
    assert len(found) == 1, found
    assert found[0].startswith(REL + ":9: `await expect(links).toHaveCount(2);` counts locate(...)"), found[0]
    assert "locateAll(page, " in found[0] and "0 or 1" in found[0]
    assert "If you mean to count, use locateAll(page, " in found[0], found[0]

@case
def every_other_miscount_is_rejected():
    shapes = {
        "direct toHaveCount(2)": ['await expect(locate(page, { role: "row" })).toHaveCount(2);'],
        "not.toHaveCount(3)": ['await expect(item(page, "Invoice 1042")).not.toHaveCount(3);'],
        "through a filter": ['await expect(locate(page, { role: "row" }).filter({ hasText: "a" })).toHaveCount(2);'],
        "count >= 2": ['const shown = await item(page, "Invoice").count();', "if (shown >= 2) return;"],
        "2 <= count": ['if (2 <= (await locate(page, "Row").count())) return;'],
        "toBeGreaterThanOrEqual(2)": ['expect(await locate(page, "Row").count()).toBeGreaterThanOrEqual(2);'],
        "more than one": ['const buttons = locate(page, { role: "button", name: "Add member" });',
                          "const count = await buttons.count();", "if (count > 1) {", '  await see(page, "x");', "}"],
        "nth(1)": ['await locate(page, { role: "link" }).nth(1).click();'],
        "count toBe(2)": ['expect(await region(page, "list").count()).toBe(2);'],
        "count toBeGreaterThan(1)": ['expect(await locate(page, "Row").count()).toBeGreaterThan(1);'],
        "count === 2 in parentheses": ['if ((await locate(page, "Row").count()) === 2) return;'],
    }
    for label, body in shapes.items():
        found = lint(*body)
        assert found, label

    assert "counts item(...)" in lint(*shapes["count >= 2"])[0] and "counts region(...)" in lint(*shapes["count toBe(2)"])[0]

UNPROVABLE = {
    "a constant": ["const expected = 1;", 'await expect(locate(page, "Save")).toHaveCount(expected);'],
    "presence kept": ['const shown = await locate(page, "Save").count();', 'await click(page, "Reload");',
                      'expect(await locate(page, "Save").count()).toBe(shown);'],
    "before / after equal": ['const a = await locate(page, { role: "article" }).count();', "await page.reload();",
                             'const b = await locate(page, { role: "article" }).count();', "expect(b).toBe(a);"],
    "all() over an optional one": ['for (const b of await locate(page, "Dismiss").all()) await b.click();'],
    "all() of item": ['for (const row of await item(page, "Invoice").all()) await row.click();'],
    "a loop bound": ['const tabs = locate(page, { role: "tab" });', "const n = await tabs.count();",
                     "for (let i = 0; i < n; i += 1) {", "  await tabs.nth(i).click();", "}"],
    "toHaveCount(before + 1) of another lookup": ['const before = await locateAll(page, "Row").count();',
                                                  'await expect(locate(page, "Row")).toHaveCount(before + 1);'],
    "a sum compared with 2": ['const s1 = await locate(page, "East").count();',
                              'const s2 = await locate(page, "West").count();', "expect(s1 + s2).toBe(2);"],
    "not.toHaveCount(before + 1)": ['const before = await locate(page, "Row").count();',
                                    'await expect(locate(page, "Row")).not.toHaveCount(before + 1);'],
    "before + 1 compared with <": ['const a = await locate(page, "Row").count();',
                                   'if ((await locate(page, "Row").count()) < a + 1) return;'],

    "before + 1 of two counts": ['const a = await locate(page, { role: "article" }).count();', "await page.reload();",
                                 'const b = await locate(page, { role: "article" }).count();', "expect(b).toBe(a + 1);"],
    "toHaveCount(1 + before)": ['const before = await locate(page, "Row").count();',
                                'await expect(locate(page, "Row")).toHaveCount(1 + before);'],
}

@case
def what_the_check_cannot_evaluate_is_a_hint_not_a_rejection():
    for label, body in UNPROVABLE.items():
        rejects, advice = single_element_count_findings(REL, spec(*body))
        assert rejects == [], (label, rejects)
        assert advice, label
        assert "If you mean to count, use locateAll(page, " in advice[0] and "present / absent" in advice[0], advice[0]
    assert "counts item(...)" in hints(*UNPROVABLE["all() of item"])[0]

    both = ['const a = await locate(page, "Row").count();', "if (a > 1 || a === later) return;"]
    assert len(lint(*both)) == 1 and hints(*both) == [], (lint(*both), hints(*both))
    later = ['const n = await locate(page, "A").count();', 'const m = await locate(page, "B").count();',
             "if (n === later && m > 1) return;"]
    assert len(lint(*later)) == 1 and hints(*later) == [], (lint(*later), hints(*later))

@case
def a_name_that_may_mean_another_variable_never_rejects():
    row = 'await expect(locate(page, "Row")).toHaveCount(before + 1);'
    first = 'const before = await locate(page, "Row").count();'
    shapes = {
        "a block redeclares it": [first, "if (page) {", "  const before = 0;", "  " + row, "}"],
        "a test.step redeclares it": [first, 'await test.step("again", async () => {', "  const before = 0;", "  " + row, "});"],
        "an arrow parameter": [first, "const check = async (before) => {", "  " + row, "};"],
        "a function parameter": [first, "async function check(before) {", "  " + row, "}"],
        "a loop header": [first, "for (const before of [0]) {", "  " + row, "}"],
        "a loop counter of the same name": ['const n = await locate(page, "Row").count();', "expect(n).toBe(1);",
                                            "for (let n = 0; n < 3; n++) {", "  if (n >= 2) break;", "}"],
        "a destructuring loop": ['const n = await locate(page, "Row").count();',
                                 "for (const [i, n] of [[0, 2]].entries()) {", "  if (n > 1) break;", "}"],
        "a method parameter": ['const rows = locate(page, "Row");', "const pick = { second(rows) { return rows.nth(1); } };"],
        "before -= 1": ["let" + first[5:], "before -= 1;", row],
        "before--": ["let" + first[5:], "before--;", row],
        "[before] = [0]": ["let" + first[5:], "[before] = [0];", row],
        "toHaveCount(1) with options": ['await expect(locate(page, "Row")).toHaveCount(1, { timeout: 5000 });'],
        "toHaveCount(0) with options": ['await expect(locate(page, "Row")).toHaveCount(0, { timeout: 5000 });'],
    }
    for label, body in shapes.items():
        assert lint(*body) == [], (label, lint(*body))
    assert hints(*shapes["toHaveCount(1) with options"]) == []
    assert lint('await expect(locate(page, "Row")).toHaveCount(2, { timeout: 5000 });')

    assert lint(first, row) == [] and hints(first, row)
    assert lint('const rows = locate(page, "Row");', "await rows.nth(1).click();")

@case
def gate_e1_never_sends_a_spec_back_for_a_hint_alone():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "test-e2e").mkdir(parents=True)
        for label, body in UNPROVABLE.items():
            (root / REL).write_text(spec(*body), encoding="utf-8")
            assert static_test_defects(root, [REL]) == "", label

        (root / REL).write_text(spec(*UNPROVABLE["a constant"], 'await expect(locate(page, "Row")).toHaveCount(2);'),
                                encoding="utf-8")
        report = static_test_defects(root, [REL])
        assert "toHaveCount(2);` counts locate(...)" in report and "toHaveCount(expected);` counts locate(...)" in report
        assert report.index("toHaveCount(2);`") < report.index("toHaveCount(expected);`"), report

@case
def present_or_absent_and_real_counts_are_silent():
    fine = [
        ['await expect(locate(page, { role: "textbox", name: "Edit A1" })).toHaveCount(0);'],
        ['await expect(locate(page, { role: "row" })).toHaveCount(1);'],
        ['await expect(locate(page, { role: "row" })).not.toHaveCount(0);'],
        ['if ((await locate(page, { role: "tab", name: "Sheet2" }).count()) === 0) return;'],
        ['if (await locate(page, "secret-research").count()) {', '  await see(page, "x");', "}"],
        ['const s1 = await locate(page, "East").count();', 'await click(page, "Sheet2");',
         'const s2 = await locate(page, "East").count();', "expect(s1 + s2).toBeGreaterThan(0);"],
        ['expect(await locate(page, "x").count()).toBe(1);'],
        ['expect(await locate(page, "x").count()).toBeGreaterThanOrEqual(1);'],
        ['await locate(page, { role: "link" }).nth(0).click();'],
        ['const links = locateAll(page, { role: "link" });', "const before = await links.count();",
         "await expect(links).toHaveCount(before + 1);"],
        ['expect(await tagged(page, "article").count()).toBe(3);'],
        ['await expect(region(page, "list").locator("li")).toHaveCount(4);'],
        ['await expect(item(page, "Invoice 1042").getByRole("button")).toHaveCount(2);'],
        ['// await expect(locate(page, { role: "row" })).toHaveCount(2);', 'await see(page, "locate(page, x).count() > 2");'],
    ]
    for body in fine:
        assert lint(*body) == [], body

@case
def a_spec_of_its_own_locate_or_not_the_helper_is_silent():
    own = spec("const locate = (p, x) => p.getByRole(x.role);", 'await expect(locate(page, { role: "row" })).toHaveCount(3);')
    assert single_element_counts(REL, own) == []
    plain = ('const { test, expect } = require("@playwright/test");' + NL + 'const { see } = require("../e2e-support/outsider");' + NL
             + 'const region = (page) => page.locator("main li");' + NL
             + 'test("t", async ({ page }) => { await expect(region(page)).toHaveCount(3); });' + NL)
    assert single_element_counts(REL, plain) == []
    elsewhere = plain.replace('const region = (page) => page.locator("main li");', 'const { region } = require("./support/lists");')
    assert single_element_counts(REL, elsewhere) == []
    assert single_element_counts("backend/tests/REQ-1.test.js", spec('await expect(locate(page, "Row")).toHaveCount(3);')) == []

@case
def another_tests_variable_of_the_same_name_is_not_the_same_count():
    text = (HEAD + 'test("a", async ({ page }) => {' + NL + '  const rows = locate(page, { role: "row" });' + NL
            + "  await expect(rows).toHaveCount(1);" + NL + "});" + NL
            + 'test("b", async ({ page }) => {' + NL + '  const rows = locateAll(page, { role: "row" });' + NL
            + "  await expect(rows).toHaveCount(3);" + NL + "});" + NL)
    assert single_element_counts(REL, text) == []
    counts = (HEAD + 'test("a", async ({ page }) => {' + NL + '  const n = await locate(page, "Row").count();' + NL
              + "  expect(n).toBe(1);" + NL + "});" + NL
              + 'test("b", async ({ page }) => {' + NL + '  const n = await locateAll(page, { role: "row" }).count();' + NL
              + "  expect(n).toBe(3);" + NL + "});" + NL)
    assert single_element_counts(REL, counts) == []

    assert single_element_count_hints(REL, counts.replace("expect(n).toBe(1);", "expect(n).toBe(1);" + NL + "  if (n !== later) return;"))
    assert single_element_counts(REL, counts.replace("expect(n).toBe(1);", "expect(n).toBe(1);" + NL + "  if (n > 1) return;"))
    assert single_element_count_findings(REL, counts) == ([], [])

@case
def gate_e1_carries_it_and_the_locate_all_rewrite_passes():

    wrong = spec('const links = locate(page, { role: "link" });', 'await click(page, "New workbook");',
                 "await expect(links).toHaveCount(2);")
    right = wrong.replace('const links = locate(page, { role: "link" });', 'const links = locateAll(page, { role: "link" });')
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "test-e2e").mkdir(parents=True)
        (root / REL).write_text(wrong, encoding="utf-8")
        report = static_test_defects(root, [REL])
        assert "toHaveCount(2);` counts locate(...)" in report, report
        (root / REL).write_text(right, encoding="utf-8")
        assert static_test_defects(root, [REL]) == ""

@case
def the_outsiders_checks_treat_locate_all_like_locate():
    body = 'const { locateAll, expect } = require("../e2e-support/outsider");' + NL + \
           'await expect(locateAll(page, { role: "row" })).toHaveCount(3);'
    raw = f"=== FILE: backend/test-e2e/R.spec.js ===\n{body}\n=== END FILE ===\n"
    assert outsider._parse(raw, "R") is not None
    node = {"id": "REQ-9", "name": "Feature", "scenarios": [],
            "description": 'The toolbar provides a button with the accessible name "Export CSV". It downloads the sheet.'}
    single = outsider.bare_stated_controls('await expect(locate(page, "Export CSV")).toBeVisible();', node)
    every = outsider.bare_stated_controls('await expect(locateAll(page, "Export CSV")).toHaveCount(1);', node)
    assert [(v.replace("locateAll", "locate"), n, r) for v, n, r in every] == single

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
