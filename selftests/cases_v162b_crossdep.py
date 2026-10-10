from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import outsider, settings
from agent.testrun import cross_test_dependencies, static_test_defects

NL = chr(10)
CASES: list = []
REL = "backend/test-e2e/REQ-1-2-2.evo.spec.js"

def case(fn):
    CASES.append(fn)
    return fn

def requirement(scenario2: str = "", leaf_description: str = "The visitor renames a workbook.") -> str:
    s1 = ("GIVEN the system pre-provisions workbook `EVO-M01-RENAME-OK` WHEN the visitor opens `EVO-M01-RENAME-OK`, "
          "clicks \"Rename workbook\", enters `  FY26 Procurement Ledger  ` and saves THEN the editor displays exactly "
          "`FY26 Procurement Ledger`")
    s2 = scenario2 or ("GIVEN the system pre-provisions workbook `EVO-M01-RENAME-OK` WHEN the visitor opens it, clicks "
                       "\"Rename workbook\", clears the name and saves THEN the message \"Workbook name cannot be empty\" "
                       "is shown and the name is unchanged")
    tree = {"id": "ROOT", "name": "Sheet", "description": "A spreadsheet application.", "children": [
        {"id": "REQ-1", "name": "Workbooks", "description": "Workbook management.", "children": [
            {"id": "REQ-1-2-2", "name": "Rename workbook", "description": leaf_description, "scenarios": [
                {"name": "Scenario 1: trimmed rename", "steps": [{"content": s1}]},
                {"name": "Scenario 2: reject an empty workbook name", "steps": [{"content": s2}]},
            ]},
        ]},
    ]}
    return yaml.safe_dump(tree, allow_unicode=True, sort_keys=False)

def batch(seeds: str = "", **kw) -> dict:
    return {"id": "REQ-1-2-2", "members": ["REQ-1-2-2"], "requirement_text": requirement(**kw),
            "seed_sentences": seeds or ("SEED SENTENCES OF THE WHOLE REQUIREMENT:" + NL
                                        + "- the system pre-provisions workbook `EVO-M01-RENAME-OK`"),
            "ancestor_context": ""}

HEAD = ('const { test } = require("@playwright/test");' + NL
        + 'const { click, fill, see } = require("../e2e-support/outsider");' + NL + NL)
T1 = ('test("REQ-1-2-2: trimmed rename - Scenario 1", async ({ page }) => {' + NL
      + '  await page.goto("/");' + NL
      + '  await click(page, { role: "link", name: "EVO-M01-RENAME-OK" });' + NL
      + '  await click(page, { role: "button", name: "Rename workbook" });' + NL
      + '  await fill(page, { role: "textbox", name: "Workbook name" }, "  FY26 Procurement Ledger  ");' + NL
      + '  await click(page, { role: "button", name: "Save" });' + NL
      + '  await see(page, "FY26 Procurement Ledger");' + NL
      + '});' + NL + NL)
T2_BODY = ('  await page.goto("/");' + NL
           + '  await click(page, { role: "link", name: "EVO-M01-RENAME-OK" });' + NL
           + '  await click(page, { role: "button", name: "Rename workbook" });' + NL
           + '  await fill(page, { role: "textbox", name: "Workbook name" }, "");' + NL
           + '  await click(page, { role: "button", name: "Save" });' + NL
           + '  await see(page, "Workbook name cannot be empty");' + NL
           + '  await see(page, "FY26 Procurement Ledger");' + NL)
T2 = 'test("REQ-1-2-2: reject an empty workbook name - Scenario 2", async ({ page }) => {' + NL + T2_BODY + '});' + NL
FY26 = HEAD + T1 + T2

@case
def fy26_shape_reported():
    found = cross_test_dependencies(REL, FY26, batch())
    assert len(found) == 1, found
    assert "reject an empty workbook name" in found[0] and "FY26 Procurement Ledger" in found[0], found
    assert "trimmed rename - Scenario 1" in found[0] and "ALONE" in found[0], found

@case
def fy26_through_gate_e1():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / REL
        path.parent.mkdir(parents=True)
        path.write_text(FY26, encoding="utf-8")
        report = static_test_defects(Path(tmp), [REL], batch())
    assert "only test" in report and "FY26 Procurement Ledger" in report, report

@case
def routing_guard_filters_red_titles():
    from agent.testrun import cross_test_dependency_defects
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / REL
        path.parent.mkdir(parents=True)
        path.write_text(FY26, encoding="utf-8")
        red = "REQ-1-2-2: reject an empty workbook name - Scenario 2"
        assert "FY26 Procurement Ledger" in cross_test_dependency_defects(Path(tmp), [REL], batch(), [red])
        assert "FY26" in cross_test_dependency_defects(Path(tmp), [REL], batch(), ["describe › " + red])
        assert cross_test_dependency_defects(Path(tmp), [REL], batch(), ["REQ-1-2-2: trimmed rename - Scenario 1"]) == ""
        assert cross_test_dependency_defects(Path(tmp), ["backend/test-e2e/missing.spec.js"], batch()) == ""

@case
def stated_in_own_scenario_not_reported():
    s2 = ("GIVEN workbook `EVO-M01-RENAME-OK` WHEN the visitor clears the name and saves THEN the message \"Workbook "
          "name cannot be empty\" is shown and the editor still displays `FY26 Procurement Ledger`")
    assert cross_test_dependencies(REL, FY26, batch(scenario2=s2)) == []

@case
def stated_in_pre_provision_not_reported():
    seeds = ("SEED SENTENCES OF THE WHOLE REQUIREMENT:" + NL
             + "- the system pre-provisions workbook `EVO-M01-RENAME-OK` and workbook `FY26 Procurement Ledger`")
    assert cross_test_dependencies(REL, FY26, batch(seeds=seeds)) == []

@case
def stated_in_description_not_reported():
    assert cross_test_dependencies(REL, FY26, batch(leaf_description="Names like FY26 Procurement Ledger are kept.")) == []

@case
def typed_by_same_test_not_reported():
    t2 = T2.replace('  await see(page, "Workbook name cannot be empty");',
                    '  await fill(page, { role: "textbox", name: "Workbook name" }, "FY26 Procurement Ledger");' + NL
                    + '  await see(page, "Workbook name cannot be empty");')
    assert cross_test_dependencies(REL, HEAD + T1 + t2, batch()) == []

HELPER = ('async function renameTo(page, name) {' + NL
          + '  await click(page, { role: "button", name: "Rename workbook" });' + NL
          + '  await fill(page, { role: "textbox", name: "Workbook name" }, "FY26 Procurement Ledger");' + NL
          + '  await click(page, { role: "button", name: "Save" });' + NL
          + '}' + NL + NL)

@case
def helper_of_other_test_reported():
    t1 = ('test("REQ-1-2-2: trimmed rename - Scenario 1", async ({ page }) => {' + NL
          + '  await page.goto("/");' + NL
          + '  await click(page, { role: "link", name: "EVO-M01-RENAME-OK" });' + NL
          + '  await renameTo(page);' + NL
          + '});' + NL + NL)
    found = cross_test_dependencies(REL, HEAD + HELPER + t1 + T2, batch())
    assert len(found) == 1 and "trimmed rename - Scenario 1" in found[0], found

@case
def helper_of_same_test_not_reported():
    t2 = T2.replace('  await see(page, "Workbook name cannot be empty");',
                    '  await renameTo(page);' + NL + '  await see(page, "Workbook name cannot be empty");')
    assert cross_test_dependencies(REL, HEAD + HELPER + T1 + t2, batch()) == []

@case
def setting_off_not_reported():
    old = settings.SETTINGS.get("v162b_crossdep", None)
    had = "v162b_crossdep" in settings.SETTINGS
    settings.SETTINGS["v162b_crossdep"] = False
    try:
        assert cross_test_dependencies(REL, FY26, batch()) == []
    finally:
        if had:
            settings.SETTINGS["v162b_crossdep"] = old
        else:
            settings.SETTINGS.pop("v162b_crossdep", None)
    assert cross_test_dependencies(REL, FY26, batch()) != []

@case
def covers_comment_maps_scenario():
    t1 = T1.replace(" - Scenario 1", "").replace("trimmed rename", "rename keeps the trimmed name")
    t2 = ("// covers S2" + NL + T2.replace(" - Scenario 2", "").replace("reject an empty workbook name", "empty name"))
    found = cross_test_dependencies(REL, HEAD + t1 + t2, batch())
    assert len(found) == 1 and "empty name" in found[0], found

@case
def scenario_name_maps_scenario():
    t1 = T1.replace(" - Scenario 1", "")
    t2 = T2.replace(" - Scenario 2", "")
    found = cross_test_dependencies(REL, HEAD + t1 + t2, batch())
    assert len(found) == 1, found

@case
def unmapped_falls_back_to_whole_leaf():

    t1 = T1.replace(" - Scenario 1", "").replace("trimmed rename", "rename A")
    t2 = T2.replace(" - Scenario 2", "").replace("reject an empty workbook name", "rename B")
    found = cross_test_dependencies(REL, HEAD + t1 + t2, batch())
    assert len(found) == 1 and "rename B" in found[0], found

    s2 = ("GIVEN workbook `EVO-M01-RENAME-OK` and a second workbook `FY26 Procurement Ledger` WHEN the visitor clears "
          "the name and saves THEN \"Workbook name cannot be empty\" is shown")
    assert cross_test_dependencies(REL, HEAD + t1 + t2, batch(scenario2=s2)) == []

    other = (HEAD + t1.replace("FY26 Procurement Ledger", "Ledger Copy Alpha")
             + t2.replace("FY26 Procurement Ledger", "Ledger Copy Alpha"))
    found = cross_test_dependencies(REL, other, batch())
    assert len(found) == 1 and "Ledger Copy Alpha" in found[0], found

REAL_LEAF = {
    "id": "REQ-1-2-2", "name": "Rename a Workbook", "type": "ATOMIC", "dependencies": ["REQ-1-1-1"],
    "description": (
        "Original Feature Description\n\nUsers can change the workbook name on the workbook editor page. Next to the "
        "editor title is a button with the accessible name \"Rename workbook\"; clicking it displays a text box labeled "
        "\"Workbook name\", prefilled with the last saved name, and a \"Save\" button. After leading and trailing spaces "
        "are trimmed, the name must not be empty; an empty name must be rejected with \"Workbook name cannot be empty\". "
        "After a successful save, both the editor title and the home-page link display the new name; if saving fails, "
        "an error is shown and the original name remains displayed. Reopening the workbook shows the most recently "
        "saved name.\n\nModified Feature Description\nWorkbook names retain the original behavior and must additionally "
        "contain at most 80 characters after trimming. Names are unique across all workbooks without regard to letter "
        "case. A name longer than 80 characters is rejected with \"Workbook name must be 80 characters or fewer\". A "
        "case-insensitive duplicate is rejected with \"Workbook name already exists\". In either rejection case, the "
        "editor title, home-page link, and prefilled \"Workbook name\" field retain the last successful name.\n"),
    "scenarios": [
        {"name": "REQ-1-2-2 - trim and persist an available workbook name", "steps": [
            {"keyword": "GIVEN", "content": "The visitor is unauthenticated and the system pre-provisions workbook "
             "`EVO-M01-RENAME-OK` with active worksheet `IdentityLog` and cell F3 value `unreviewed`."},
            {"keyword": "WHEN", "content": "The visitor opens `EVO-M01-RENAME-OK`, clicks \"Rename workbook\", enters "
             "`  FY26 Procurement Ledger  ` in \"Workbook name\", and clicks \"Save\"."},
            {"keyword": "THEN", "content": "The editor displays exactly `FY26 Procurement Ledger`; after refresh, "
             "\"Workbook name\" is prefilled with exactly `FY26 Procurement Ledger` and cell F3 still displays "
             "`unreviewed`."}]},
        {"name": "REQ-1-2-2 - reject a case-insensitive duplicate workbook name", "steps": [
            {"keyword": "GIVEN", "content": "The visitor is unauthenticated and the system pre-provisions workbook "
             "`EVO-M01-RENAME-DUP` with active worksheet `IdentityLog`, plus a separate workbook named "
             "`EVO-M01-ARCHIVE-RESERVED`."},
            {"keyword": "WHEN", "content": "The visitor opens `EVO-M01-RENAME-DUP`, clicks \"Rename workbook\", enters "
             "`evo-m01-archive-reserved` in \"Workbook name\", and clicks \"Save\"."},
            {"keyword": "THEN", "content": "The exact message \"Workbook name already exists\" is visible; the editor "
             "title and the \"Workbook name\" field still display `EVO-M01-RENAME-DUP`, including after refresh."}]},
        {"name": "REQ-1-2-2 - reject a workbook name longer than 80 characters", "steps": [
            {"keyword": "GIVEN", "content": "The visitor is unauthenticated and the system pre-provisions workbook "
             "`EVO-M01-RENAME-LIMIT` with active worksheet `LimitProbe` and cell C2 value `limit sentinel`."},
            {"keyword": "WHEN", "content": "The visitor opens `EVO-M01-RENAME-LIMIT`, clicks \"Rename workbook\", "
             "enters the 81-character value `EVO-" + "A" * 77 + "` in \"Workbook name\", and clicks \"Save\"."},
            {"keyword": "THEN", "content": "The exact message \"Workbook name must be 80 characters or fewer\" is "
             "visible; after refresh the title remains `EVO-M01-RENAME-LIMIT` and cell C2 displays `limit sentinel`."}]},
    ],
}
REAL_SPEC = (HEAD
             + 'test("REQ-1-2-2: trim and persist an available workbook name", async ({ page }) => {' + NL
             + '  await page.goto("/");' + NL
             + '  await click(page, { role: "link", name: "EVO-M01-RENAME-OK" });' + NL
             + '  await click(page, { role: "button", name: "Rename workbook" });' + NL
             + '  await fill(page, { role: "textbox", name: "Workbook name" }, "  FY26 Procurement Ledger  ");' + NL
             + '  await click(page, { role: "button", name: "Save" });' + NL
             + '  await see(page, "FY26 Procurement Ledger");' + NL
             + '  await page.reload();' + NL
             + '  await see(page, "unreviewed");' + NL
             + '});' + NL + NL
             + 'test("REQ-1-2-2: reject an empty workbook name", async ({ page }) => {' + NL
             + '  await page.goto("/");' + NL
             + '  await click(page, { role: "link", name: "EVO-M01-RENAME-OK" });' + NL
             + '  await click(page, { role: "button", name: "Rename workbook" });' + NL
             + '  await fill(page, { role: "textbox", name: "Workbook name" }, "   ");' + NL
             + '  await click(page, { role: "button", name: "Save" });' + NL
             + '  await see(page, "Workbook name cannot be empty");' + NL
             + '  await see(page, "FY26 Procurement Ledger");' + NL
             + '});' + NL)

def real_batch() -> dict:
    tree = {"id": "ROOT", "name": "Sheet", "description": "", "children": [
        {"id": "REQ-1", "name": "Workbooks", "description": "", "children": [
            {"id": "REQ-1-2", "name": "Workbook editing", "description": "", "children": [REAL_LEAF]}]}]}
    seeds = ("SEED SENTENCES OF THE WHOLE REQUIREMENT:" + NL
             + "- The visitor is unauthenticated and the system pre-provisions workbook `EVO-M01-RENAME-OK` with active "
               "worksheet `IdentityLog` and cell F3 value `unreviewed`." + NL
             + "- The visitor is unauthenticated and the system pre-provisions workbook `EVO-M01-RENAME-DUP` with active "
               "worksheet `IdentityLog`, plus a separate workbook named `EVO-M01-ARCHIVE-RESERVED`.")
    return {"id": "REQ-1-2-2", "members": ["REQ-1-2-2"], "seed_sentences": seeds, "ancestor_context": "",
            "requirement_text": yaml.safe_dump(tree, allow_unicode=True, sort_keys=False)}

@case
def real_fy26_reported():
    found = cross_test_dependencies(REL, REAL_SPEC, real_batch())
    assert len(found) == 1, found
    assert '(test "REQ-1-2-2: reject an empty workbook name")' in found[0] and "FY26 Procurement Ledger" in found[0], found
    assert 'only test "REQ-1-2-2: trim and persist an available workbook name"' in found[0], found

@case
def unsure_files_not_reported():
    serial = FY26.replace(HEAD, HEAD + 'test.describe.configure({ mode: "serial" });' + NL)
    assert cross_test_dependencies(REL, serial, batch()) == []
    extra = FY26.replace(HEAD, HEAD + 'const data = require("./data.json");' + NL)
    assert cross_test_dependencies(REL, extra, batch()) == []
    shared = FY26.replace(HEAD, HEAD + 'const LABEL = "FY26 Procurement Ledger";' + NL)
    assert cross_test_dependencies(REL, shared, batch()) == []
    hook = FY26.replace(HEAD, HEAD + 'test.beforeEach(async ({ page }) => { await fill(page, "Name", '
                        '"FY26 Procurement Ledger"); });' + NL)
    assert cross_test_dependencies(REL, hook, batch()) == []
    assert cross_test_dependencies("backend/tests/x.test.js", FY26, batch()) == []
    assert cross_test_dependencies(REL, FY26, None) == []
    assert cross_test_dependencies(REL, HEAD + T2, batch()) == []

@case
def outsider_prompt_has_isolation_sentence():
    text = outsider.OUTSIDER_SYSTEM if hasattr(outsider, "OUTSIDER_SYSTEM") else ""
    blob = text or "\n".join(str(v) for v in vars(outsider).values() if isinstance(v, str))
    assert "runs alone on a fresh server and fresh data" in " ".join(blob.split()), "isolation sentence missing"

if __name__ == "__main__":
    failed = 0
    for test in CASES:
        try:
            test()
            print("PASS", test.__name__)
        except Exception:
            failed += 1
            traceback.print_exc()
            print("FAIL", test.__name__)
    print(f"{len(CASES) - failed}/{len(CASES)} v162b crossdep cases passed")
    raise SystemExit(bool(failed))
