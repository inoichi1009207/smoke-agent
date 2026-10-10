from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.testrun import seed_renames, static_test_defects

CASES: list = []

def case(function):
    CASES.append(function)
    return function

REL = "backend/test-e2e/REQ-1-2-2.spec.js"
BATCH = {"seed_sentences": "SEED SENTENCES OF THE WHOLE REQUIREMENT:\n- The evaluation seed contains workbook `Q3 Sales` with worksheet `Sheet1`."}
HEAD = '''const { test } = require("@playwright/test");
const { expect, click, fill, see, notSee, region, press } = require("../e2e-support/outsider");
const newName = "outsider-" + Date.now() + "-" + Math.random().toString(36).slice(2, 6);
async function openQ3(page) {
  await page.goto("/");
  await click(page, "Q3 Sales");
  await see(page, { role: "tab", name: "Sheet1" });
}
'''

def spec(*bodies: str) -> str:
    return HEAD + "".join(f'test("REQ-1-2-2: t{i}", async ({{ page }}) => {{\n{b}\n}});\n' for i, b in enumerate(bodies))

RENAME = '''  await click(page, { role: "button", name: "Rename workbook" });
  const dialog = region(page, "dialog", "Rename workbook");
  await fill(page, { role: "textbox", name: "Workbook name" }, newName, dialog);
  await click(page, { role: "button", name: "Save" }, dialog);'''

@case
def v144c_seed_rename_is_reported_also_through_a_helper():
    text = spec("  await openQ3(page);\n" + RENAME + "\n  await see(page, newName);",
                '  await openQ3(page);\n  await click(page, { role: "button", name: "Rename workbook" });\n'
                '  await fill(page, { role: "textbox", name: "Workbook name" }, "   ");\n  await click(page, "Save");\n'
                '  await see(page, "Workbook name cannot be empty");',
                '  await page.goto("/");\n  await click(page, "Q3 Sales");\n' + RENAME)
    found = seed_renames(REL, text, BATCH)
    assert len(found) == 2, found
    assert '"REQ-1-2-2: t0"' in found[0] and '"REQ-1-2-2: t2"' in found[1], found
    assert all('shared seed record "Q3 Sales"' in f and "Create a record of your own" in f for f in found), found

    inline = spec('  await openQ3(page);\n  await click(page, "Rename");\n  await fill(page, "Workbook name", `w-${Date.now()}`);\n  await click(page, "Save");')
    assert len(seed_renames(REL, inline, BATCH)) == 1

@case
def v144c_uncertain_or_undone_renames_are_not_reported():
    created = spec('  await openQ3(page);\n  await click(page, { role: "button", name: "New worksheet" });\n' + RENAME)
    typed_first = spec('  await page.goto("/");\n  await fill(page, "Workbook name", newName);\n  await press(page, "Enter");\n'
                       '  await click(page, "Q3 Sales");\n' + RENAME)
    duplicate = spec('  await openQ3(page);\n  await click(page, "Rename workbook");\n  await fill(page, "Workbook name", "Budget");\n  await click(page, "Save");')
    cancelled = spec('  await openQ3(page);\n  await click(page, "Rename workbook");\n  await fill(page, "Workbook name", newName);\n'
                     '  await click(page, "Cancel");\n  await see(page, "Q3 Sales");')
    escaped = spec('  await openQ3(page);\n  await click(page, "Rename workbook");\n  await fill(page, "Workbook name", newName);\n'
                   '  await press(page, "Escape");')
    restored = spec("  await openQ3(page);\n" + RENAME + '\n  await click(page, "Rename workbook");\n'
                    '  await fill(page, "Workbook name", "Q3 Sales");\n  await click(page, "Save");')
    other = spec('  await page.goto("/");\n  await click(page, "Budget 2024");\n' + RENAME)
    for text in (created, typed_first, duplicate, cancelled, escaped, restored, other):
        assert seed_renames(REL, text, BATCH) == [], (text, seed_renames(REL, text, BATCH))
    positive = spec("  await openQ3(page);\n" + RENAME)
    assert seed_renames(REL, positive, {}) == [] and seed_renames(REL, positive, None) == []
    assert seed_renames("backend/tests/REQ-1-2-2.test.js", positive, BATCH) == []
    skipped = positive.replace('test("REQ', 'test.skip("REQ')
    assert seed_renames(REL, skipped, BATCH) == []

@case
def v144c_e1_runs_the_check_with_the_batch_only():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / REL
        path.parent.mkdir(parents=True)
        path.write_text(spec("  await openQ3(page);\n" + RENAME), encoding="utf-8")
        with_batch = static_test_defects(Path(tmp), [REL], batch=BATCH)
        assert 'shared seed record "Q3 Sales"' in with_batch, with_batch
        assert 'shared seed record' not in static_test_defects(Path(tmp), [REL])

@case
def v144c_author_prompts_state_the_shared_seed_rule():
    from agent.outsider import SYSTEM_PROMPT as OUTSIDER
    from agent.test_author import SYSTEM_PROMPT as AUTHOR
    assert "Shared seed records stay as they are" in OUTSIDER
    assert "changes it back through the interface" in OUTSIDER and "never depend on each other's order" in OUTSIDER
    assert "record the requirement dedicates to that scenario" in OUTSIDER
    assert "without a reset" in AUTHOR and "shared" in AUTHOR
