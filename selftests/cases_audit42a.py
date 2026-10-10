from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import cases_boilerplate as cb

CASES = []

def case(function):
    CASES.append(function)
    return function

def census(*names: str):
    return cb.census(*names, cases=CASES)

def _same_named_tree(leaves: int = 5) -> dict:
    children = []
    for i in range(leaves):
        rec = f"Record {i}"
        children.append({
            "id": f"REQ-{i + 1}", "name": f"Feature {i}", "description": f"Users approve invoice {i}.",
            "scenarios": [
                {"name": "same", "steps": [
                    {"keyword": "GIVEN", "content": f"The evaluation seed contains the seeded record `{rec}` with `v{i}`."},
                    {"keyword": "WHEN", "content": cb.GENERATED_WHEN.format(rec=rec, phrase="the requested workflow", i=i)},
                    {"keyword": "THEN", "content": cb.GENERATED_THEN.format(rec=rec, phrase="the requested workflow", i=i)},
                ]},
                {"name": "same", "steps": [
                    {"keyword": "GIVEN", "content": f"Invoice INV-{1040 + i} for {300 + i}.00 euros waits for approval."},
                    {"keyword": "WHEN", "content": f"The approver opens invoice INV-{1040 + i}, checks the {300 + i}.00 total and presses Approve."},
                    {"keyword": "THEN", "content": f"Invoice INV-{1040 + i} shows the status Approved and leaves the pending list of approver {i}."},
                ]},
            ],
        })
    return {"id": "ROOT", "name": "R", "children": children}

@case
def a_concrete_scenario_sharing_its_name_with_a_generated_one_stays_concrete():
    import yaml
    from agent.boilerplate import batch_report, boilerplate_of_text, boilerplate_scenarios, leaf_is_all_boilerplate
    tree = _same_named_tree()
    found = boilerplate_scenarios(tree)
    assert found == {(f"REQ-{i + 1}", 0) for i in range(5)}, found
    assert not any(leaf_is_all_boilerplate(leaf, tree) for leaf in tree["children"])
    text = yaml.safe_dump(tree, allow_unicode=True, sort_keys=False)
    assert boilerplate_of_text(text) == found
    report = batch_report(text, ["REQ-1"])
    assert len(report) == 1 and report[0].generated == 1 and report[0].total == 2 and not report[0].all_generated, report
    assert report[0].labels == ["same (scenario 1)"] and report[0].indexes == [0], report[0]
    assert not any("INV-" in step for step in report[0].steps), report[0].steps

@case
def the_prompt_names_which_of_two_same_named_scenarios_is_generated():
    import yaml
    from agent import outsider
    text = yaml.safe_dump(_same_named_tree(), allow_unicode=True, sort_keys=False)
    node = {"id": "REQ-1", "name": "Feature 0", "members": ["REQ-1"], "requirement_text": text}
    block = outsider.boilerplate_block(node)
    assert '"same" (scenario 1)' in block and "all 2 scenario" not in block and "(scenario 2)" not in block, block
    note = outsider.review_boilerplate_note(node)
    assert '"same" (scenario 1)' in note and "all scenarios" not in note, note

    assert outsider.quotes_only_boilerplate("failures are shown beside the named control", node)
    assert not outsider.quotes_only_boilerplate("shows the status Approved and leaves the pending list", node)

@census("hackathon-sheet-platform.yaml")
def the_sheet_leaf_with_repeated_names_is_counted_per_instance():
    from agent.boilerplate import batch_report
    text = cb._sample("hackathon-sheet-platform.yaml")
    report = batch_report(text, ["REQ-1-2-2"])

    assert len(report) == 1 and report[0].all_generated and report[0].generated == report[0].total, report
    assert len(report[0].indexes) == report[0].total > len(report[0].names), report[0]

def _generated_node() -> dict:
    import yaml
    text = yaml.safe_dump(cb._generated_tree(6), allow_unicode=True, sort_keys=False)
    return {"id": "REQ-1", "name": "Feature 0", "description": "Users do thing 0.", "members": ["REQ-1"],
            "requirement_text": text}

TYPE_SEARCH = '''const { test } = require("@playwright/test");
const { click, fill, see } = require("../e2e-support/outsider");

async function typeSearch(page, text) {
  await fill(page, { role: "searchbox", name: "Search" }, text);
}

const org = "ghost-org";
'''

@case
def a_helper_value_typed_in_another_test_does_not_make_the_record():
    from agent import outsider
    node = _generated_node()
    assert outsider.batch_boilerplate(node)
    spec = TYPE_SEARCH + '''
test("REQ-1: search", async ({ page }) => {
  await page.goto("/");
  await typeSearch(page, org);
});

test("REQ-1: open", async ({ page }) => {
  await page.goto("/");
  await click(page, org);
});
'''
    assert outsider.invented_records(spec, node) == ["ghost-org"], outsider.invented_records(spec, node)

@case
def a_helper_value_typed_after_the_lookup_does_not_make_the_record():
    from agent import outsider
    spec = TYPE_SEARCH + '''
test("REQ-1: open then search", async ({ page }) => {
  await page.goto("/");
  await click(page, org);
  await typeSearch(page, org);
});
'''
    assert outsider.invented_records(spec, _generated_node()) == ["ghost-org"]

@case
def a_helper_value_typed_before_the_lookup_in_the_same_test_is_made():
    from agent import outsider
    spec = TYPE_SEARCH + '''
test("REQ-1: enter then find", async ({ page }) => {
  await page.goto("/");
  await typeSearch(page, org);
  await see(page, org);
});
'''
    node = _generated_node()
    assert outsider.invented_records(spec, node) == []

    concrete = dict(node, requirement_text="")
    assert outsider.invented_records(spec, concrete) == ["ghost-org"]

@census("hackathon-sheet-platform.yaml")
def the_sheet_description_spec_that_enters_cells_then_asserts_them_stays_accepted():
    from agent import outsider
    from agent.test_author import AuthoredFile
    node = cb._batch_of("hackathon-sheet-platform.yaml", "REQ-4-1-1")
    assert outsider.invented_records(cb.SHEET_DESCRIPTION_SPEC, node) == []
    spec = AuthoredFile("backend/test-e2e/REQ-4-1-1.spec.js", cb.SHEET_DESCRIPTION_SPEC)
    assert outsider.deterministic_review(node, spec, "REQ-4-1-1", log=lambda *_: None) == []

    moved = cb.SHEET_DESCRIPTION_SPEC.replace(
        '  await enter(page, "G1", label);\n', '').replace(
        '  await see(page, label, locate(page, { role: "gridcell", name: "G1" }));\n',
        '  await see(page, label, locate(page, { role: "gridcell", name: "G1" }));\n  await enter(page, "G1", label);\n')
    assert moved != cb.SHEET_DESCRIPTION_SPEC and outsider.invented_records(moved, node) == ["Apples"], outsider.invented_records(moved, node)

@case
def a_control_the_own_description_names_is_not_a_later_control():
    from agent import outsider
    node = {"id": "REQ-9", "name": "Export", "later_names": ["Formula bar", "Create filter", "Values"],
            "ancestor_context": "[ROOT] covers sorting, create filter dialogs and more.",
            "description": "Before and after export, the grid values and formula bar content remain unchanged.",
            "scenarios": [{"name": "s", "steps": [{"keyword": "WHEN", "content": "The user presses \"Export CSV\"."}]}]}
    spec = ('test("REQ-9: x", async ({ page }) => {\n'
            '  await click(page, { role: "textbox", name: "Formula bar" });\n'
            '  await click(page, { role: "button", name: "Create filter" });\n'
            '  await click(page, "Values");\n});\n')

    assert outsider.later_controls(spec, node) == ["Create filter", "Values"], outsider.later_controls(spec, node)

    quoted = dict(node, description=node["description"] + ' The "values" panel stays closed.')
    assert outsider.later_controls(spec, quoted) == ["Create filter"]

    other = dict(node, description="The formula barometer stays unchanged.")
    assert "Formula bar" in outsider.later_controls(spec, other)

    branch = {"id": "REQ-7", "later_names": ["Default branch"],
              "description": "The repository stores its owner, visibility and default branch."}
    uses = 'test("REQ-7: x", async ({ page }) => {\n  await click(page, "Default branch");\n});\n'
    assert outsider.later_controls(uses, branch) == ["Default branch"]
    selector = dict(branch, description="The Default branch selector lists every branch.")
    assert outsider.later_controls(uses, selector) == []

    panel = dict(node, description="The values panel and the formula bar content stay unchanged.")
    assert outsider.later_controls(spec, panel) == ["Create filter", "Values"]

@census("hackathon-sheet-platform.yaml")
def sheet_req_1_3_2_may_check_its_formula_bar_but_not_a_later_control():
    from agent import outsider
    from agent.test_author import AuthoredFile
    node = cb._batch_of("hackathon-sheet-platform.yaml", "REQ-1-3-2")
    assert "formula bar content remain unchanged" in " ".join(str(node.get("description")).split())
    assert {"Formula bar", "Create filter", "Values"} <= set(node["later_names"]), node["later_names"]
    spec = ('const { test } = require("@playwright/test");\n'
            'test("REQ-1-3-2: export keeps the formula bar", async ({ page }) => {\n'
            '  await page.goto("/");\n'
            '  await click(page, { role: "gridcell", name: "A1" });\n'
            '  await click(page, { role: "textbox", name: "Formula bar" });\n'
            '  await click(page, { role: "button", name: "Create filter" });\n'
            '  await click(page, "Values");\n});\n')
    assert outsider.later_controls(spec, node) == ["Create filter", "Values"], outsider.later_controls(spec, node)
    lines = []
    problems = outsider.deterministic_review(node, AuthoredFile("x", spec), "REQ-1-3-2", log=lines.append)
    assert not any("'Formula bar', a control of a later requirement" in line for line in lines), lines
    assert any("'Create filter', a control of a later requirement" in line for line in lines), lines
    assert any('"Create filter", which only a LATER requirement names' in p for p in problems)

@case
def missing_samples_are_a_visible_skip_not_a_pass():
    listed: list = []
    missing = Path(tempfile.gettempdir()) / "no-such-requirement-samples-42a"
    before = len(cb.SKIPPED)

    shown: list = []

    @cb.census("hackathon-sheet-platform.yaml", cases=listed, samples=missing, environ={}, log=shown.append)
    def needs_samples():
        raise AssertionError("never run")

    assert listed == [] and len(cb.SKIPPED) == before + 1 and cb.SKIPPED[-1].startswith("SKIP  needs_samples"), cb.SKIPPED[-1:]
    assert shown == [cb.SKIPPED[-1]]
    cb.SKIPPED.pop()

    cb.census("hackathon-sheet-platform.yaml", cases=listed, samples=missing, environ={cb.REQUIRE_ENV: "1"})(needs_samples)
    assert listed == [needs_samples]

@case
def the_samples_folder_is_found_from_the_repo_root_and_the_env_var():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        samples = base / "codex-audit" / "requirements-samples"
        samples.mkdir(parents=True)
        root = base / "codex-audit" / "agent-src"
        root.mkdir()
        assert cb.find_samples(root, environ={}) == samples

        sibling = base / "smoke-agent"
        sibling.mkdir()
        assert cb.find_samples(sibling, environ={}) == samples

        own = base / "elsewhere"
        own.mkdir()
        assert cb.find_samples(root, environ={cb.SAMPLES_ENV: str(own)}) == own
        assert cb.find_samples(root, environ={cb.SAMPLES_ENV: str(base / "absent")}) is None

    real = cb.find_samples()
    if real is not None:
        assert (real / "hackathon-sheet-platform.yaml").is_file(), real

@case
def a_generated_batch_never_relies_on_seed_sentence_contents():

    from agent import outsider

    rules = " ".join(outsider.BOILERPLATE_RULES.split())
    assert "serves only to find the named record the test opens" in rules
    assert "It is never the source of the contents the test relies on" in rules
    assert "seeded contents" not in rules

@case
def generated_rules_and_the_closing_note_do_not_contradict_the_general_rules():
    from agent import outsider
    from agent.description_pass import DESCRIPTION_PASS_NOTE

    rules = " ".join(outsider.BOILERPLATE_RULES.split())

    assert "are for concrete scenarios only" in rules

    note = " ".join(DESCRIPTION_PASS_NOTE.split())
    assert "replace those of GENERATED SCENARIOS" in note
    assert '"<requirement id>: <short behaviour>"' in rules

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
            traceback.print_exc(limit=3)
    print(f"{len(CASES) - failed}/{len(CASES)} audit-42a cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
