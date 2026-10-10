from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.testrun import (single_element_count_findings, single_element_count_hints,
                           single_element_counts, static_test_defects)

NL = chr(10)
CASES: list = []

def case(function):
    CASES.append(function)
    return function

REL = "backend/test-e2e/REQ-2-2-3.spec.js"
HEAD = ('const { test } = require("@playwright/test");' + NL
        + 'const { click, see, locate, locateAll, expect } = require("../e2e-support/outsider");' + NL)
PLAIN_HEAD = 'const { test, expect } = require("@playwright/test");' + NL

def spec(*body: str, head: str = HEAD) -> str:
    return head + NL + 'test("REQ-2-2-3: roles", async ({ page }) => {' + NL + NL.join("  " + b for b in body) + NL + "});" + NL

def lint(*body: str, head: str = HEAD) -> list[str]:
    return single_element_counts(REL, spec(*body, head=head))

def hints(*body: str) -> list[str]:
    return single_element_count_hints(REL, spec(*body))

GITHUB_V144 = ['const buttons = page.getByRole("button", { name: /^\\s*Add\\s+member\\s*$/i }).filter({ visible: true }).first();',
               'await click(page, "Add member");',
               "await expect(buttons).toHaveCount(2);"]

@case
def the_github_v144_spec_is_rejected():
    for head in (HEAD, PLAIN_HEAD):
        body = GITHUB_V144 if head == HEAD else [GITHUB_V144[0], GITHUB_V144[2]]
        found = lint(*body, head=head)
        assert len(found) == 1, (head, found)
        line = spec(*body, head=head).splitlines().index("  await expect(buttons).toHaveCount(2);") + 1
        assert found[0].startswith(f"{REL}:{line}: `await expect(buttons).toHaveCount(2);` counts a locator whose last "
                                   "narrowing step is .first() -- one element, so its count is only ever 0 or 1"), found[0]
        assert "toHaveCount(2) never measures the number the requirement states" in found[0], found[0]
        assert "count the lookup without .first()" in found[0] and "may stay on the .first() locator" in found[0]

PROVABLE = {
    "toHaveCount(2) on .first()": ['await expect(page.getByRole("row").first()).toHaveCount(2);'],
    "toHaveCount(3) on .last()": ['await expect(page.locator("li").last()).toHaveCount(3);'],
    "toHaveCount(2) on .nth(0)": ['await expect(page.locator("li").nth(0)).toHaveCount(2);'],
    "toHaveCount(2) on .nth(4)": ['await expect(page.getByRole("listitem").nth(4)).toHaveCount(2);'],
    "not.toHaveCount(3)": ['await expect(page.getByRole("row").first()).not.toHaveCount(3);'],
    "narrowed, filtered": ['await expect(page.getByRole("row").first().filter({ hasText: "a" })).toHaveCount(2);'],
    "widened, then narrowed": ['await expect(page.locator("ul").first().locator("li").first()).toHaveCount(2);'],
    "count > 1": ['const n = await page.getByRole("row").last().count();', "if (n > 1) return;"],
    "count >= 2 inline": ['if ((await page.locator("tr").first().count()) >= 2) return;'],
    "count toBe(2)": ['expect(await page.getByText("Invoice").first().count()).toBe(2);'],
    "toBeGreaterThan(1)": ['expect(await page.locator("tr").nth(0).count()).toBeGreaterThan(1);'],
    "nth(1) after first()": ['await page.getByRole("link").first().nth(1).click();'],
    "nth(1) through a variable": ['const link = page.getByRole("link").first();', "await link.nth(1).click();"],
    "a root declared as a locator": ['const rows = page.locator("tr");', "await expect(rows.first()).toHaveCount(2);"],
    "a frame's chain": ['await expect(page.frameLocator("#f").getByRole("row").first()).toHaveCount(2);'],
    "over several lines": ["await expect(", '  page', '    .getByRole("row")', "    .first()", ").toHaveCount(2);"],
}

@case
def every_provable_miscount_of_a_raw_chain_is_rejected():
    for label, body in PROVABLE.items():
        assert lint(*body), label
        assert lint(*body, head=PLAIN_HEAD), label
    assert "last narrowing step is .last()" in lint(*PROVABLE["toHaveCount(3) on .last()"])[0]
    assert "last narrowing step is .nth(4)" in lint(*PROVABLE["toHaveCount(2) on .nth(4)"])[0]
    assert "comparing it (`count > 1`)" in lint(*PROVABLE["count > 1"])[0]
    assert ".nth(1) (there is no element after the first)" in lint(*PROVABLE["nth(1) after first()"])[0]
    assert len(lint(*PROVABLE["widened, then narrowed"])) == 1

UNPROVABLE = {
    "a constant": ["const expected = 1;", 'await expect(page.getByRole("row").first()).toHaveCount(expected);'],
    "presence kept": ['const shown = await page.getByText("Save").first().count();', 'await click(page, "Reload");',
                      'expect(await page.getByText("Save").first().count()).toBe(shown);'],
    "a loop bound": ['const tabs = page.getByRole("tab").first();', "const n = await tabs.count();",
                     "for (let i = 0; i < n; i += 1) {", "  await tabs.nth(i).click();", "}"],
    "nth(i) after first()": ["const i = 2;", 'await page.getByRole("link").first().nth(i).click();'],
    "all() of a first()": ['for (const b of await page.getByText("Dismiss").first().all()) await b.click();'],
    "before + 1 of the whole lookup": ['const before = await page.getByRole("row").count();',
                                       'await expect(page.getByRole("row").first()).toHaveCount(before + 1);'],

    "before + 1 of a one-element count": ['const before = await page.getByRole("article").first().count();',
                                          'await click(page, "New");',
                                          'await expect(page.getByRole("article").first()).toHaveCount(before + 1);'],
    "a count equal to before + 1": ['const a = await page.getByRole("article").first().count();', "await page.reload();",
                                    'const b = await page.getByRole("article").first().count();', "expect(b).toBe(a + 1);"],
}

@case
def what_the_check_cannot_evaluate_is_a_hint():
    for label, body in UNPROVABLE.items():
        rejects, advice = single_element_count_findings(REL, spec(*body))
        assert rejects == [], (label, rejects)
        assert advice and "is right only as a present / absent check (0 or 1)" in advice[0], (label, advice)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "test-e2e").mkdir(parents=True)
        for label, body in UNPROVABLE.items():
            (root / REL).write_text(spec(*body), encoding="utf-8")
            assert static_test_defects(root, [REL]) == "", label

@case
def widened_present_absent_and_foreign_chains_are_silent():
    fine = {
        "widened by .locator()": ['await expect(page.locator("ul").first().locator("li")).toHaveCount(3);'],
        "widened by .getByRole()": ['await expect(page.getByRole("list").first().getByRole("listitem")).toHaveCount(3);'],
        "widened by .or()": ['await expect(page.getByRole("row").first().or(page.getByRole("cell"))).toHaveCount(2);'],
        "widened through a variable": ['const list = page.getByRole("list").first();',
                                       'await expect(list.getByRole("listitem")).toHaveCount(4);',
                                       'expect(await list.locator("li").count()).toBe(4);'],
        "toHaveCount(0)": ['await expect(page.getByRole("dialog").first()).toHaveCount(0);'],
        "toHaveCount(1)": ['await expect(page.getByRole("row").last()).toHaveCount(1);'],
        "count === 0": ['if ((await page.locator("tr").first().count()) === 0) return;'],
        "count truthy": ['if (await page.getByText("x").first().count()) await click(page, "x");'],
        "nth(0) click": ['await page.getByRole("link").nth(0).click();'],
        "nth(i) is no narrowing here": ["const i = 1;", 'await expect(page.locator("li").nth(i)).toHaveCount(2);'],
        "the whole lookup": ['await expect(page.getByRole("row")).toHaveCount(3);',
                             'expect(await page.locator("tr").count()).toBeGreaterThan(1);'],
        "locateAll": ['await expect(locateAll(page, { role: "row" })).toHaveCount(3);'],
        "no locator by text or declaration": ["const x = list.first();", "expect(x.count()).toBe(2);",
                                              "expect(_.first(values)).toBe(2);"],
        "a narrowed value inside a filter": ['const rows = page.getByRole("row").filter({ has: page.getByText("a").first() });',
                                             "await expect(rows).toHaveCount(2);"],
        "strings and comments": ['// await expect(page.getByRole("row").first()).toHaveCount(2);',
                                 'await see(page, "page.locator(x).first().count() > 2");'],
        "first() returned by a local function": ['const row = (n) => page.getByRole("row", { name: n }).first();',
                                                 'await expect(row("a")).toBeVisible();'],
    }
    for label, body in fine.items():
        assert single_element_count_findings(REL, spec(*body)) == ([], []), (label, single_element_count_findings(REL, spec(*body)))
    assert single_element_counts("backend/tests/REQ-1.test.js", spec(*PROVABLE["toHaveCount(2) on .first()"])) == []

@case
def a_helper_chain_is_reported_once_as_the_helper():
    found = lint('await expect(locate(page, { role: "row" }).first()).toHaveCount(2);')
    assert len(found) == 1 and "counts locate(...)" in found[0], found

    widened = lint('await expect(locate(page, { role: "list" }).locator("li").first()).toHaveCount(2);')
    assert len(widened) == 1 and "last narrowing step is .first()" in widened[0], widened
    assert lint('await expect(locate(page, { role: "list" }).locator("li")).toHaveCount(2);') == []

@case
def gate_e1_carries_it_and_the_rewrite_passes():
    wrong = spec(*GITHUB_V144)
    right = wrong.replace(".filter({ visible: true }).first();", ".filter({ visible: true });")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "test-e2e").mkdir(parents=True)
        (root / REL).write_text(wrong, encoding="utf-8")
        report = static_test_defects(root, [REL])
        assert "toHaveCount(2);` counts a locator whose last narrowing step is .first()" in report, report
        (root / REL).write_text(right, encoding="utf-8")
        assert static_test_defects(root, [REL]) == ""

COLLECTIONS = {
    "the audit shape": ["const groups = makeGroups();", "expect(groups.filter(Boolean).first().count()).toBe(3);"],
    "last() after an arrow filter": ["const groups = makeGroups();", "expect(groups.filter((x) => x).last().count()).toBe(2);"],
    "nth(0) of a filtered array": ["const items = [1, 2, 3].filter(Boolean);", "expect(items.nth(0).count()).toBe(2);"],
    "toHaveCount on a filtered call result": ["const items = makeItems().filter(Boolean);",
                                              "await expect(items.first()).toHaveCount(2);"],
    "a parameter": ["const check = (list) => expect(list.filter(Boolean).first().count()).toBe(3);"],
    "through a variable": ["const groups = makeGroups();", "const one = groups.filter(Boolean).first();",
                           "expect(await one.count()).toBe(3);"],
    "an array from the page": ['const cells = (await page.$$("td")).filter(Boolean);', "expect(cells.first().count()).toBe(2);"],
}
LOCATORS = {
    "declared through a filter of a locator": ['const rows = page.locator("tr");', "const shown = rows.filter({ visible: true });",
                                               "expect(await shown.first().count()).toBe(2);"],
    "three hops": ['const a = page.getByRole("row");', "const b = a.filter({ hasText: 'x' });", "const c = b.filter({ visible: true });",
                   "await expect(c.first()).toHaveCount(2);"],
    "a frame's locator": ['const frame = page.frame("main");', "await expect(frame.locator('tr').first()).toHaveCount(2);"],
    "a helper lookup below": ['const all = locateAll(page, { role: "row" });', "await expect(all.filter({ visible: true }).first()).toHaveCount(2);"],
}

@case
def r52_an_ordinary_collection_is_at_most_a_hint():
    for label, body in COLLECTIONS.items():
        for head in (HEAD, PLAIN_HEAD):
            rejects, advice = single_element_count_findings(REL, spec(*body, head=head))
            assert rejects == [], (label, rejects)
            assert len(advice) == 1 and "last narrowing step is" in advice[0], (label, advice)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "test-e2e").mkdir(parents=True)
        (root / REL).write_text(spec(*COLLECTIONS["the audit shape"]), encoding="utf-8")
        assert static_test_defects(root, [REL]) == ""

@case
def r52_a_locator_root_through_declarations_still_rejects():
    for label, body in LOCATORS.items():
        found = lint(*body)
        assert len(found) == 1 and "last narrowing step is .first()" in found[0], (label, found)

    assert lint(*GITHUB_V144)
    assert all(lint(*body, head=PLAIN_HEAD) for body in PROVABLE.values())

    far = ['const a = page.getByRole("row");', "const b = a.filter({ hasText: 'x' });", "const c = b.filter({ visible: true });",
           "const d = c.filter({ hasText: 'y' });", "await expect(d.first()).toHaveCount(2);"]
    rejects, advice = single_element_count_findings(REL, spec(*far))
    assert rejects == [] and len(advice) == 1, (rejects, advice)

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
