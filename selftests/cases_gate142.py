from __future__ import annotations

import inspect
import os
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml

from agent import testrun
from agent.testrun import spec_api_misuse, static_test_defects, stated_cell_editors, unsupported_assertions

CASES: list = []
SKIPPED: list[str] = []
REL = "backend/test-e2e/REQ-X.spec.js"

def case(function):
    CASES.append(function)
    return function

HEAD = ('const { test } = require("@playwright/test");\n'
        'const { expect, click, choose, fill, press, see, notSee, locate, item, region } = require("../e2e-support/outsider");\n\n')

def requirement(*leaves: dict, folder_description: str = "Repository issues and settings.") -> str:
    tree = {"id": "ROOT", "name": "Root", "type": "FOLDER", "description": "Root.",
            "children": [{"id": "REQ-9", "name": "Folder", "type": "FOLDER", "description": folder_description,
                          "children": list(leaves)}]}
    return yaml.safe_dump(tree, allow_unicode=True, sort_keys=False)

def leaf(leaf_id: str, description: str, *scenarios: list[str], name: str = "Leaf") -> dict:
    return {"id": leaf_id, "name": name, "type": "ATOMIC", "dependencies": [], "description": description,
            "scenarios": [{"name": f"{leaf_id}: {name} - Scenario {i}",
                           "steps": [{"keyword": k, "content": c} for k, c in zip(("GIVEN", "WHEN", "THEN", "THEN"), steps)]}
                          for i, steps in enumerate(scenarios, start=1)]}

def batch(text: str, *members: str, **extra) -> dict:
    return dict({"id": members[0], "members": list(members), "requirement_text": text}, **extra)

ISSUES = leaf("REQ-5-1-1",
              "Users with repository-view permission can read issue rows for the current repository on the Issues list "
              "page; each row references a persisted issue number and displays the title, status, author, labels, and "
              "update time. “Open” and “Closed” are links, not buttons or tabs. A unique searchbox "
              "named “Search issues” filters as the user types.\n\nThe seeded closed issue title is `Legacy welcome text`.",
              ["The seeded data is repository `acme-docs`, issue `Improve onboarding`, labels `bug` and `documentation`. "
               "Seed values: repository `acme-docs`, issue `Improve onboarding`, labels `bug` and `documentation`.",
               "The user selects “Open”, enters the unique word, and selects `bug` in the label filter.",
               "The list displays only the matching Open issue, and each row displays its number, title, label, author, and status."],
              name="List and Filter Repository Issues")
SIGN_IN = leaf("REQ-1-1-2", "A registered user signs in with the seeded account.",
               ["Seed values: account `alice-dev`, password `Valid-password-123!`.", "The user signs in.", "The header shows the user."],
               name="Sign In")
DEFAULT_BRANCH = leaf(
    "REQ-4-3-3",
    "A repository administrator enters “Settings” → “Branches”, selects an existing branch from the "
    "“Default branch” dropdown, clicks “Update”, and then confirms the change. Opening the repository page "
    "entry without a branch then shows “Branch <new default branch name>”; opening that selector still offers an "
    "option named exactly after the old default branch.",
    ["A repository Admin is signed in; the repository already has `main` and `release` branches, the current default "
     "branch is `main`. Seed values: repository `acme-docs`, branches `main` and `release`.",
     "The Admin selects `release` from the “Default branch” dropdown, clicks “Update”, and confirms.",
     "The system saves `release` as the default branch; newly opening the repository overview displays `release` by default."],
    ["A signed-in user without repository-administration permission opens the Branches page. Seed values: repository "
     "`acme-docs`, branches `main` and `feature-search`.",
     "The user attempts to view or update the default-branch setting.",
     "The default-branch update operation is unavailable to the non-administrator."],
    name="Change the Repository Default Branch")

HASH_SPEC = HEAD + '''test("REQ-5-1-1: List and Filter Repository Issues - Scenario 1", async ({ page }) => {
  await click(page, "Open");
  await fill(page, { role: "searchbox", name: "Search issues" }, "onboarding");
  await click(page, "bug");

  await notSee(page, "Legacy welcome text");
  const row = item(page, "Improve onboarding");
  await see(page, "Improve onboarding", row);
  await see(page, "bug", row);
  await see(page, "Open", row);
  await see(page, "alice-dev", row);
  await expect(row).toContainText("#");
});
'''

BRANCH_SCENARIO_1 = '''test("REQ-4-3-3: Change the Repository Default Branch - Scenario 1", async ({ page }) => {
  await click(page, { role: "link", name: "Settings" });
  await click(page, { role: "link", name: "Branches" });
  await choose(page, { role: "combobox", name: "Default branch" }, "release");
  await click(page, { role: "button", name: "Update" });
  await click(page, { role: "button", name: "Confirm" });
  await see(page, "Branch release");
  await click(page, "release");
  await see(page, { role: "option", name: "main" });
  await page.reload();
  await expect(locate(page, { role: "combobox", name: "Default branch" })).toHaveValue("release");
  await see(page, { role: "option", name: "release" });
});
'''
BRANCH_SCENARIO_2 = '''test("REQ-4-3-3: Change the Repository Default Branch - Scenario 2", async ({ page }) => {
  await click(page, { role: "link", name: "Settings" });
  await click(page, { role: "link", name: "Branches" });
  await notSee(page, { role: "combobox", name: "Default branch" });
  await see(page, "Branch main");
  await see(page, { role: "option", name: "feature-search" });
});
'''
GITHUB = requirement(SIGN_IN, DEFAULT_BRANCH, ISSUES)

def found(spec: str, node: dict) -> list[str]:
    return unsupported_assertions(REL, spec, node)

@case
def a1_hash_the_requirement_never_states():
    problems = found(HASH_SPEC, batch(GITHUB, "REQ-5-1-1"))
    assert len(problems) == 1, problems
    assert problems[0].startswith(f"{REL}:15: `await expect(row).toContainText(\"#\");` asserts \"#\""), problems

    assert not any(v in problems[0] for v in ('"bug"', '"alice-dev"', '"Open"'))

@case
def a2_release_belongs_to_scenario_1_only():
    node = batch(GITHUB, "REQ-4-3-3")
    assert found(HEAD + BRANCH_SCENARIO_1 + "\n" + BRANCH_SCENARIO_2, node) == []

    moved = BRANCH_SCENARIO_2.replace('await see(page, "Branch main");', 'await see(page, "Branch release");\n  await see(page, "release");')
    problems = found(HEAD + BRANCH_SCENARIO_1 + "\n" + moved, node)
    assert [p.split(" asserts ")[1].split(",")[0] for p in problems] == ['"release"'], problems
    assert all("the scenario the test implements" in p for p in problems), problems

@case
def a3_typed_or_created_earlier_is_supported():
    node = batch(GITHUB, "REQ-5-1-1")
    body = ('test("REQ-5-1-1: new issue shows in the list", async ({ page }) => {\n'
            '  await fill(page, "Title", "Crash on save");\n  await click(page, "Submit new issue");\n'
            '  await see(page, "Crash on save");\n});\n')
    assert found(HEAD + body, node) == []

    early = body.replace('  await see(page, "Crash on save");\n', "").replace(
        '  await fill(page, "Title", "Crash on save");', '  await see(page, "Crash on save");\n  await fill(page, "Title", "Crash on save");')
    assert len(found(HEAD + early, node)) == 1

    csv = ('test("REQ-5-1-1: import", async ({ page }) => {\n'
           '  await upload(page, "Import", "a.csv", \'Title\\nsay ""hi""\');\n  await see(page, \'say "hi"\');\n});\n')
    assert found(HEAD + csv, node) == []

@case
def a4_what_is_left_alone():
    node = batch(GITHUB, "REQ-5-1-1")
    body = ('test("REQ-5-1-1: shapes", async ({ page }) => {\n'
            '  const row = item(page, "Improve onboarding");\n'
            '  await expect(row).not.toHaveText("#");\n'
            '  await notSee(page, "unassigned");\n'
            '  await expect(row).toHaveText(/#\\d+/);\n'
            '  await see(page, title);\n'
            '  await see(page, `Issue ${n}`);\n'
            '  await see(page, "B2");\n'
            '  await expect(row).toHaveValue("=A1+B1");\n'
            '  await see(page, "");\n'
            '});\n'
            'test.skip("REQ-5-1-1: skipped", async ({ page }) => {\n  await see(page, "unassigned");\n});\n')
    assert found(HEAD + body, node) == [], found(HEAD + body, node)

    assert unsupported_assertions(REL, HASH_SPEC, None) == []
    assert unsupported_assertions(REL, HASH_SPEC, {"id": "REQ-5-1-1", "members": ["REQ-5-1-1"]}) == []
    assert unsupported_assertions(REL, HASH_SPEC, batch(GITHUB, "REQ-7-7")) == []
    assert unsupported_assertions("backend/tests/x.test.js", HASH_SPEC, batch(GITHUB, "REQ-5-1-1")) == []
    assert unsupported_assertions(REL, HASH_SPEC, batch("not: [yaml", "REQ-5-1-1")) == []

@case
def a5_stated_elsewhere_joined_and_numbered():
    node = batch(GITHUB, "REQ-5-1-1")
    body = ('test("REQ-5-1-1: header", async ({ page }) => {\n'
            '  await see(page, "alice-dev/acme-docs");\n'
            '  await see(page, "Valid-password-123!");\n'
            '  await see(page, { role: "link", name: "Settings" });\n'
            '});\n')
    assert found(HEAD + body, node) == [], found(HEAD + body, node)

    alone = requirement(SIGN_IN, ISSUES, folder_description="Repository issues.")
    assert [p.split(" asserts ")[1].split(",")[0] for p in found(HEAD + body, batch(alone, "REQ-5-1-1"))] == ['"Settings"']

    sheets = requirement(leaf("REQ-2-1-1", "Adding a worksheet creates the tab “Sheet2” next to “Sheet1”.",
                              ["Seed: workbook `Q3 Sales`.", "The user adds a worksheet twice.", "Tabs are shown."]))
    tabs = 'test("REQ-2-1-1: add", async ({ page }) => {\n  await see(page, { role: "tab", name: "Sheet3" });\n});\n'
    assert found(HEAD + tabs, batch(sheets, "REQ-2-1-1")) == []

@case
def a6_numbers():

    text = requirement(leaf("REQ-4-1-1", "Users enter formulas in the formula bar; the grid displays the result.",
                            ["Seed: workbook `Q3 Sales`, cells `A1=2`, `B1=3`.", "The user enters formulas.", "Results show."]))
    node = batch(text, "REQ-4-1-1")
    spec = HEAD + ('test("REQ-4-1-1: formulas", async ({ page }) => {\n'
                   '  const fb = { role: "textbox", name: "Formula bar" };\n'
                   '  await click(page, { role: "gridcell", name: "E2" });\n'
                   '  await fill(page, fb, "=(2+3)*4/2-1");\n  await press(page, "Enter");\n'
                   '  await see(page, "9", item(page, "E2"));\n});\n')
    assert found(spec, node) == []

    plain = requirement(leaf("REQ-4-1-1", "Users enter values; the grid displays them.",
                             ["Seed: workbook `Q3 Sales`, cells `A1=2`, `B1=3`.", "The user enters values.", "Values show."]))
    bare = spec.replace('  await fill(page, fb, "=(2+3)*4/2-1");\n', "")
    problems = found(bare, batch(plain, "REQ-4-1-1"))
    assert len(problems) == 1 and "a number the requirement never states" in problems[0], problems

    assert found(bare.replace('"9"', '"5"'), batch(plain, "REQ-4-1-1")) == []
    assert found(bare.replace('"9"', '"6"'), batch(plain, "REQ-4-1-1")) == []

    pivot = requirement(leaf("REQ-4-1-1", "The pivot table shows the sum of each region.",
                             ["Seed: workbook `Q3 Sales`, cells `A1=2`, `B1=3`.", "The user builds a pivot.", "Sums show."]))
    assert found(bare, batch(pivot, "REQ-4-1-1")) == []

@case
def a7_static_test_defects_is_backward_compatible():
    params = inspect.signature(static_test_defects).parameters
    assert list(params)[:2] == ["project_dir", "test_files"] and params["batch"].default is None
    assert inspect.signature(spec_api_misuse).parameters["batch"].default is None
    with tempfile.TemporaryDirectory() as tmp:
        spec = Path(tmp) / REL
        spec.parent.mkdir(parents=True)
        spec.write_text(HASH_SPEC, encoding="utf-8")
        before = static_test_defects(Path(tmp), [REL])
        assert "asserts \"#\"" not in before, before
        after = static_test_defects(Path(tmp), [REL], batch=batch(GITHUB, "REQ-5-1-1"))
        assert "REQ-X.spec.js:15: `await expect(row).toContainText(\"#\");` asserts \"#\"" in after, after

        saved = testrun.unsupported_assertions
        testrun.unsupported_assertions = lambda *a, **k: 1 / 0
        try:
            assert "asserts" not in static_test_defects(Path(tmp), [REL], batch=batch(GITHUB, "REQ-5-1-1"))
        finally:
            testrun.unsupported_assertions = saved

GRID_FILL = HEAD + ('test("REQ-2-2-1: insert a row", async ({ page }) => {\n'
                    '  const coord = "A2";\n  await fill(page, { role: "gridcell", name: coord }, "7");\n});\n')
SHEET_ROWS = ("Users insert blank rows above or below a target row, or delete the target row, through the row-number menu. "
              "The row-number menu provides \"Insert 1 row above\", \"Insert 1 row below\", and \"Delete row\".")

@case
def b1_a_later_field_is_never_suggested():
    node = {"id": "REQ-2-2-1", "members": ["REQ-2-2-1"], "description": SHEET_ROWS,
            "earlier_names": ["Delete row", "Insert 1 row above", "Insert 1 row below", "Q3 Sales"],
            "later_names": ["Formula bar", "Refresh pivot table"]}
    assert stated_cell_editors(node) == []
    for text in (spec_api_misuse(REL, GRID_FILL, node), spec_api_misuse(REL, GRID_FILL)):
        assert len(text) == 1 and "formula bar" not in text[0].lower(), text
        assert "ONLY if this requirement or an earlier one names one" in text[0] and "type into the selected cell: typeText(page," in text[0]

    from agent.outsider import later_controls
    assert later_controls('await fill(page, "Formula bar", "7");', node) == ["Formula bar"]

@case
def b2_an_own_or_earlier_field_is_named():
    from agent.outsider import later_controls
    earlier = {"id": "REQ-3-1-1", "members": ["REQ-3-1-1"], "description": "Users edit a cell.",
               "earlier_names": ["Formula bar", "Q3 Sales"], "later_names": ["Create filter"]}
    text = spec_api_misuse(REL, GRID_FILL, earlier)
    assert len(text) == 1 and 'fill the "Formula bar" field (this requirement or an earlier one names it)' in text[0], text
    assert later_controls('await fill(page, "Formula bar", "7");', earlier) == []

    own = {"id": "REQ-1-3-2", "members": ["REQ-1-3-2"], "later_names": ["Formula bar"],
           "description": "After the import, the grid and the Formula bar content stay unchanged."}
    assert stated_cell_editors(own) == ["Formula bar"] and later_controls('await fill(page, "Formula bar", "7");', own) == []

    assert stated_cell_editors({"id": "R", "description": "Type into the “Cell editor” and press Enter."}) == ["Cell editor"]

    with tempfile.TemporaryDirectory() as tmp:
        spec = Path(tmp) / REL
        spec.parent.mkdir(parents=True)
        spec.write_text(GRID_FILL, encoding="utf-8")
        assert 'fill the "Formula bar" field' in static_test_defects(Path(tmp), [REL], batch=earlier)
        assert "formula bar" not in static_test_defects(Path(tmp), [REL]).lower()

SCRATCH_FREE_SETS = {
    "github-v129": ("platform-runs/github-v129/workspace.zip", "platform-runs/github-v129/workspace.zip"),
    "sheet-v134": ("platform-runs/sheet-v134/workspace.zip", "platform-runs/sheet-v134/workspace.zip"),
    "github-v141-2": ("local-eval/runs/v141-github-2/requirements-source/requirements.yaml",
                      "local-eval/runs/v141-github-2/template/backend/test-e2e"),
    "sheet-v141-2": ("local-eval/runs/v141-sheet-2/requirements-source/requirements.yaml",
                     "local-eval/runs/v141-sheet-2/template/backend/test-e2e"),
}

EXPECTED = {
    ("github-v129", "REQ-5-1-1.spec.js:16", "#1"), ("github-v129", "REQ-5-2-1.spec.js:43", "#"),
    ("github-v129", "REQ-5-3-1.spec.js:54", "unassigned"), ("github-v129", "REQ-6-2-1.spec.js:34", "#"),
    ("github-v141-2", "REQ-5-1-1.spec.js:25", "#"),
}

def _base() -> Path:
    explicit = str(os.environ.get("ARC_GATE142_RUNS") or "").strip()
    return Path(explicit) if explicit else ROOT.parent

def _read_set(requirement_path: str, specs_path: str) -> tuple[str, dict[str, str]]:
    base = _base()
    if requirement_path.endswith(".zip"):
        with zipfile.ZipFile(base / requirement_path) as archive:
            text = archive.read("template/requirements/requirements.yaml").decode("utf-8")
            specs = {n.rsplit("/", 1)[1]: archive.read(n).decode("utf-8", errors="replace") for n in archive.namelist()
                     if n.startswith("template/backend/test-e2e/") and n.endswith(".spec.js")}
        return text, specs
    folder = base / specs_path
    return (base / requirement_path).read_text(encoding="utf-8"), {p.name: p.read_text(encoding="utf-8", errors="replace")
                                                                   for p in sorted(folder.glob("*.spec.js"))}

def census(function):
    missing = [n for n, (r, s) in SCRATCH_FREE_SETS.items() if not (_base() / r).exists() or not (_base() / s).exists()]
    if missing and str(os.environ.get("ARC_REQUIRE_SAMPLES") or "") != "1":
        line = f"SKIP  {function.__name__}: delivered spec set(s) {', '.join(missing)} not found under {_base()}"
        SKIPPED.append(line)
        print(line)
        return function
    CASES.append(function)
    return function

@census
def c1_census_on_delivered_specs():
    import re

    import main as factory
    from agent.batching import build_batches
    got = set()
    for name, (requirement_path, specs_path) in SCRATCH_FREE_SETS.items():
        text, specs = _read_set(requirement_path, specs_path)
        tree = yaml.safe_load(text)
        leaf_ids = [str(x.get("id")) for x in __import__("agent.boilerplate", fromlist=["leaves_of"]).leaves_of(tree)]
        work = build_batches(tree)
        factory.stamp_batch_names(work)
        by_file = {factory.safe_prefix(str(b["id"])): b for b in work}
        for file_name, body in specs.items():
            spec_id = file_name[: -len(".spec.js")]
            node = by_file.get(spec_id)
            if node is None:
                members = [i for i in leaf_ids if re.search(r"^test\([\"'`]" + re.escape(i) + r"(?![\w-])", body, re.M)]
                node = {"id": spec_id, "members": members}
            for line in unsupported_assertions(f"backend/test-e2e/{file_name}", body, dict(node, requirement_text=text)):
                where, rest = line.split(": `", 1)
                got.add((name, where.rsplit("/", 1)[1], rest.split(" asserts \"", 1)[1].split("\", ", 1)[0]))
    assert got == EXPECTED, sorted(got ^ EXPECTED)

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
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed" + (f"; {len(SKIPPED)} skipped (no delivered specs)" if SKIPPED else ""))
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
