from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import outsider
from agent.outsider import seed_absence_assertions
from agent.test_author import AuthoredFile

SEEDS = ("SEED SENTENCES OF THE WHOLE REQUIREMENT (records that exist before any test; use them by these names):\n"
         "- The evaluation seed contains the seeded workbook `Q3 Sales` with `Sheet1` and `Sheet2`, rows `East/1200` and `North/800`.\n"
         "- The evaluation seed contains the seeded workbook `Q3 Sales`, worksheet `Sheet1`, and cell A1 value `Region`.\n"
         "- Seed values: user `alice-dev` and repository `secret-repo`.")

def _node(seeds: str = SEEDS, then: str = "The grid shows the header `Region` in A1.") -> dict:
    return {"id": "REQ-1-1-1", "name": "Open workbook", "description": "", "seed_sentences": seeds,
            "scenarios": [{"name": "S1", "steps": [{"keyword": "GIVEN", "content": "The seeded workbook `Q3 Sales` exists."},
                                                   {"keyword": "WHEN", "content": "The user opens `Q3 Sales`."},
                                                   {"keyword": "THEN", "content": then}]}]}

SAMPLE = r'''const { test } = require("@playwright/test");
const { expect, click, locate, locateAll, freshSession, see, notSee, region, item } = require("../e2e-support/outsider");

// covers S1
test("REQ-1-1-1: home page shows last updated and workbook link", async ({ page }) => {
  await page.goto("/");
  await see(page, "Last updated:");
  await see(page, { role: "link", name: "Q3 Sales" });
});

// covers S2, S4
test("REQ-1-1-1: opening a workbook shows its editor state and survives refresh", async ({ page }) => {
  await page.goto("/");
  await click(page, { role: "link", name: "Q3 Sales" });
  await see(page, "Q3 Sales");
  await see(page, "Last updated:");
  await see(page, { role: "tab", name: "Sheet1" });
  await expect(locate(page, { role: "tab", name: "Sheet1" })).toHaveAttribute("aria-selected", "true");
  const grid = region(page, "grid", "Worksheet grid");
  await see(page, "Region", region(page, "gridcell", "A1", grid));
  await page.reload();
  await see(page, "Q3 Sales");
  await see(page, { role: "tab", name: "Sheet1" });
  await see(page, "Region", region(page, "gridcell", "A1", grid));
});

// covers S3, S5
test("REQ-1-1-1: editor URL restores the workbook in a later browser session", async ({ page }) => {
  await page.goto("/");
  await click(page, { role: "link", name: "Q3 Sales" });
  const url = page.url();
  await freshSession(page);
  await page.goto(url);
  await see(page, "Q3 Sales");
  await see(page, { role: "tab", name: "Sheet1" });
  const grid = region(page, "grid", "Worksheet grid");
  await see(page, "Region", region(page, "gridcell", "A1", grid));
  await notSee(page, "East", grid);
});

// covers S6, S7, S8, S9, S10
test("REQ-1-2-1: create a blank workbook", async ({ page }) => {
  await page.goto("/");
  await click(page, { role: "button", name: "New blank workbook" });
  await click(page, { role: "button", name: "Create" });
  await see(page, { role: "tab", name: "Sheet1" });
  const grid = region(page, "grid", "Worksheet grid");
  await notSee(page, "Region", grid);
  await page.reload();
});
'''

HEAD = 'const { test } = require("@playwright/test");\nconst { click, fill, see, notSee, region } = require("../e2e-support/outsider");\n'
OPEN = '  await page.goto("/");\n  await click(page, { role: "link", name: "Q3 Sales" });\n  const grid = region(page, "grid", "Worksheet grid");\n'

def _test(body: str, title: str = "REQ-1-1-1: t") -> str:
    return f'test("{title}", async ({{ page }}) => {{\n{body}}});\n'

def sample_is_flagged():
    p = seed_absence_assertions(SAMPLE, _node())
    assert len(p) == 1, p
    assert 'notSee("East")' in p[0] and "editor URL restores the workbook" in p[0] and "UNION" in p[0], p

    assert not any("Region" in x for x in p), p

def opened_then_notsee_content_is_flagged():
    p = seed_absence_assertions(HEAD + _test(OPEN + '  await notSee(page, "North", grid);\n'), _node())
    assert len(p) == 1 and 'notSee("North")' in p[0], p

    p = seed_absence_assertions(HEAD + _test('  await page.goto("/");\n  await click(page, "Q3 Sales");\n  await page.reload();\n'
                                             '  await notSee(page, "1200");\n'), _node())
    assert len(p) == 1 and 'notSee("1200")' in p[0], p

def a_mutating_step_before_is_not_flagged():
    for step in ('  await click(page, { role: "button", name: "Delete row" });\n',
                 '  await click(page, { role: "tab", name: "Sheet2" });\n',
                 '  await fill(page, "Filter", "North");\n',
                 '  await page.keyboard.press("Delete");\n',
                 '  await page.getByRole("button", { name: "Clear" }).click();\n',
                 '  await removeRow(page);\n',
                 '  await click(page, { role: "link", name: "Home" });\n',
                 '  await page.goto("/");\n',
                 '  await freshSession(page);\n'):
        spec = HEAD + _test(OPEN + step + '  await notSee(page, "East", grid);\n')
        assert seed_absence_assertions(spec, _node()) == [], (step, seed_absence_assertions(spec, _node()))

def non_seed_value_is_not_flagged():
    assert seed_absence_assertions(HEAD + _test(OPEN + '  await notSee(page, "West", grid);\n'), _node()) == []

    assert seed_absence_assertions(HEAD + _test(OPEN + '  await notSee(page, "east", grid);\n'), _node()) == []

    assert seed_absence_assertions(HEAD + _test(OPEN + '  await notSee(page, "Eas", grid);\n'), _node()) == []

def object_and_template_targets_are_not_flagged():
    for target in ('{ role: "gridcell", name: "East" }', '`${"East"}`', '`East ${x}`', 'value'):
        spec = HEAD + _test(OPEN + f'  await notSee(page, {target}, grid);\n')
        assert seed_absence_assertions(spec, _node()) == [], target

    spec = HEAD + _test(OPEN + '  await notSee(page, `East`, grid);\n')
    assert len(seed_absence_assertions(spec, _node())) == 1

def notsee_in_comment_or_string_is_not_flagged():
    for line in ('  // await notSee(page, "East", grid);\n',
                 '  /* await notSee(page, "East", grid); */\n',
                 '  const note = "await notSee(page, \\"East\\", grid)";\n',
                 "  const note2 = 'notSee(page, \"East\")';\n"):
        spec = HEAD + _test(OPEN + line)
        assert seed_absence_assertions(spec, _node()) == [], line

    spec = HEAD + _test(OPEN + '  const s = "await click(page, \\"Delete\\")";\n  await notSee(page, "East", grid);\n')
    assert len(seed_absence_assertions(spec, _node())) == 1

def only_the_test_without_action_is_flagged():
    spec = (HEAD + _test(OPEN + '  await notSee(page, "East", grid);\n', "REQ-1-1-1: plain open")
            + _test(OPEN + '  await click(page, { role: "button", name: "Delete row" });\n  await notSee(page, "East", grid);\n',
                    "REQ-1-1-1: delete then check"))
    p = seed_absence_assertions(spec, _node())
    assert len(p) == 1 and "plain open" in p[0], p

    spec = HEAD + _test(OPEN + '  await notSee(page, "East", grid);\n  await click(page, { role: "button", name: "Delete row" });\n')
    assert len(seed_absence_assertions(spec, _node())) == 1

def access_checks_on_record_names_are_not_flagged():

    spec = HEAD + _test('  await page.goto("/");\n  await notSee(page, "secret-repo");\n  await notSee(page, "Q3 Sales");\n')
    assert seed_absence_assertions(spec, _node()) == []
    spec = HEAD + _test(OPEN + '  await notSee(page, "secret-repo");\n  await notSee(page, "alice-dev");\n')
    assert seed_absence_assertions(spec, _node()) == []

def content_value_without_opening_a_record_is_not_flagged():
    spec = HEAD + _test('  await page.goto("/");\n  await notSee(page, "East");\n')
    assert seed_absence_assertions(spec, _node()) == []

    spec = HEAD + _test('  await page.goto("/");\n  await see(page, { role: "link", name: "Q3 Sales" });\n  await notSee(page, "East");\n')
    assert seed_absence_assertions(spec, _node()) == []

def negated_sentences_and_hooks_silence():

    node = _node(then="The grid does not show `East` until the sheet is switched.")
    assert seed_absence_assertions(HEAD + _test(OPEN + '  await notSee(page, "East", grid);\n'), node) == []

    seeds = SEEDS + "\n- The evaluation seed contains the seeded workbook `Q3 Sales` with no rows `Archived`."
    assert seed_absence_assertions(HEAD + _test(OPEN + '  await notSee(page, "Archived", grid);\n'), _node(seeds)) == []

    spec = (HEAD + 'test.beforeEach(async ({ page }) => {\n  await click(page, { role: "button", name: "Reset" });\n});\n'
            + _test(OPEN + '  await notSee(page, "East", grid);\n'))
    assert seed_absence_assertions(spec, _node()) == []

CLOSED_SEEDS = ("SEED SENTENCES OF THE WHOLE REQUIREMENT (records that exist before any test; use them by these names):\n"
                "- The evaluation seed contains the seeded workbook `Q3 Sales` with worksheet `Sheet1` and rows "
                "`East/1200/Open`, `North/800/Closed`, `South/700/Open`.")

def a_quoted_value_never_negates_its_line():

    _names, values = outsider._seed_names_and_contents(_node(CLOSED_SEEDS))
    assert {"East", "North", "1200", "Closed"} <= values, values
    p = seed_absence_assertions(HEAD + _test(OPEN + '  await notSee(page, "East", grid);\n'), _node(CLOSED_SEEDS))
    assert len(p) == 1 and 'notSee("East")' in p[0], p

    seeds = CLOSED_SEEDS + "\n- The evaluation seed contains the seeded workbook `Q3 Sales` without rows `West/1/Open`."
    assert "West" not in outsider._seed_names_and_contents(_node(seeds))[1]

HELPER_OPEN = '''async function openQ3Sales(page) {
  await page.goto("/");
  await click(page, { role: "link", name: "Q3 Sales" });
  await see(page, { role: "tab", name: "Sheet1" });
}
'''

def a_spec_helper_that_opens_the_record_counts_as_opening():
    spec = HEAD + HELPER_OPEN + _test('  await openQ3Sales(page);\n  const grid = region(page, "grid", "Worksheet grid");\n'
                                      '  await notSee(page, "East", grid);\n')
    p = seed_absence_assertions(spec, _node())
    assert len(p) == 1 and 'notSee("East")' in p[0], p

    arrow = HEAD + 'const open = async (page) => {\n  await page.goto("/");\n  await click(page, "Q3 Sales");\n};\n' \
        + 'async function start(p) {\n  await open(p);\n}\n' + _test('  await start(page);\n  await notSee(page, "North");\n')
    assert len(seed_absence_assertions(arrow, _node())) == 1

def a_spec_helper_that_acts_is_not_opening():
    for extra in ('  await click(page, { role: "button", name: "Sort A-Z" });\n',
                  '  await fill(page, "Filter", "North");\n',
                  '  await unknownStep(page);\n'):
        spec = HEAD + HELPER_OPEN.replace("}\n", extra + "}\n", 1) + _test('  await openQ3Sales(page);\n  await notSee(page, "East");\n')
        assert seed_absence_assertions(spec, _node()) == [], extra

    spec = HEAD + 'async function home(page) {\n  await page.goto("/");\n}\n' + _test('  await home(page);\n  await notSee(page, "East");\n')
    assert seed_absence_assertions(spec, _node()) == []

    spec = HEAD + _test('  await openQ3Sales(page);\n  await notSee(page, "East");\n')
    assert seed_absence_assertions(spec, _node()) == []

def no_seed_sentences_is_a_no_op():
    spec = HEAD + _test(OPEN + '  await notSee(page, "East", grid);\n')
    assert seed_absence_assertions(spec, _node(seeds="")) == []
    assert seed_absence_assertions(spec, {"id": "REQ-1"}) == []

    assert seed_absence_assertions(spec, _node(seeds="SEED\n- The evaluation seed contains the workbook `Q3 Sales`.")) == []

def wiring_reaches_the_author_like_unstated_navigation():
    from agent.settings import SETTINGS, _DEFAULTS

    assert _DEFAULTS["seed_absence_check"] is False
    on_saved = SETTINGS.get("seed_absence_check", None)
    SETTINGS["seed_absence_check"] = True
    try:
        lines: list[str] = []
        problems = outsider.deterministic_review(_node(), AuthoredFile("x", SAMPLE), "REQ-1-1-1", log=lines.append)
    finally:
        if on_saved is None:
            SETTINGS.pop("seed_absence_check", None)
        else:
            SETTINGS["seed_absence_check"] = on_saved
    hits = [p for p in problems if p.startswith("PROBLEM: SEED | ") and 'notSee("East")' in p]
    assert len(hits) == 1 and "drop this assertion, or assert only what your own GIVEN states" in hits[0], problems
    assert any("absence of a stated seed value" in line for line in lines), lines

    import inspect
    assert "deterministic_review(node, spec, prefix, log=log, ledger_hint=False)" in inspect.getsource(outsider.review_acceptance)

    saved = SETTINGS.get("seed_absence_check", None)
    try:
        SETTINGS["seed_absence_check"] = False
        off = outsider.deterministic_review(_node(), AuthoredFile("x", SAMPLE), "REQ-1-1-1", log=lambda *_: None)
        assert not any(p.startswith("PROBLEM: SEED") for p in off), off
    finally:
        if saved is None:
            SETTINGS.pop("seed_absence_check", None)
        else:
            SETTINGS["seed_absence_check"] = saved

CASES = [sample_is_flagged, opened_then_notsee_content_is_flagged, a_mutating_step_before_is_not_flagged,
         non_seed_value_is_not_flagged, object_and_template_targets_are_not_flagged,
         notsee_in_comment_or_string_is_not_flagged, only_the_test_without_action_is_flagged,
         access_checks_on_record_names_are_not_flagged, content_value_without_opening_a_record_is_not_flagged,
         negated_sentences_and_hooks_silence, a_quoted_value_never_negates_its_line,
         a_spec_helper_that_opens_the_record_counts_as_opening, a_spec_helper_that_acts_is_not_opening,
         no_seed_sentences_is_a_no_op, wiring_reaches_the_author_like_unstated_navigation]

def main() -> int:
    failed = 0
    for case in CASES:
        try:
            case()
            print(f"ok    {case.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {case.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} v156b cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
