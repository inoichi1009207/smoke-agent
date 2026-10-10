from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import outsider
from agent.test_author import AuthoredFile

NL = chr(10)
CASES = []

def case(function):
    CASES.append(function)
    return function

def node_of(description: str, *steps: tuple[str, str], whole: str = "") -> dict:
    node = {"id": "REQ-9", "name": "Feature", "description": description,
            "scenarios": [{"name": "S1", "steps": [{"keyword": k, "content": c} for k, c in steps]}] if steps else []}
    if whole:
        node["requirement_text"] = whole
    return node

class Reviewer:

    def chat(self, messages, **options):
        return {"content": "OK"}

EXPORT = node_of('The toolbar provides a button with the accessible name "Export CSV". It downloads the sheet.',
                 ("WHEN", 'The user activates "Export CSV"'))

@case
def a_bare_word_for_a_name_stated_with_one_role_is_reported():
    found = outsider.bare_stated_controls('const file = await download(page, "Export CSV");', EXPORT)
    assert found == [("download", "Export CSV", "button")], found
    assert outsider.bare_stated_controls('await click(page, "Export CSV");', EXPORT) == [("click", "Export CSV", "button")]

@case
def the_stated_role_pair_passes():
    assert outsider.bare_stated_controls('await download(page, { role: "button", name: "Export CSV" });', EXPORT) == []

    assert outsider.bare_stated_controls('// await download(page, "Export CSV");' + NL + 'await see(page, { role: "button", name: "Export CSV" });', EXPORT) == []

@case
def a_name_with_two_roles_or_another_thing_or_a_seed_is_not_reported():
    either = node_of('The header has a unique link or button named "Export CSV".')
    assert outsider.bare_stated_controls('await click(page, "Export CSV");', either) == []

    marker = node_of('The confirmation flow provides a "Public" radio. The overview displays a visible "Public" marker for a public repository.')
    assert outsider.bare_stated_controls('await see(page, "Public");', marker) == []
    assert outsider.bare_stated_controls('await click(page, "Public");', marker) == []
    seeded = node_of('Each record provides a button named "Q3 Sales". Seed data: the seeded workbook "Q3 Sales" exists.')
    assert outsider.bare_stated_controls('await click(page, "Q3 Sales");', seeded) == []

@case
def a_looking_call_is_reported_only_when_every_mention_states_the_role():
    always = node_of('The page contains exactly one button named "Account menu".', ("THEN", 'The button named "Account menu" is shown'))
    assert outsider.bare_stated_controls('await see(page, "Account menu");', always) == [("see", "Account menu", "button")]

    silent = node_of('The page contains exactly one button named "Account menu".', ("THEN", 'The page shows "Account menu"'))
    assert outsider.bare_stated_controls('await see(page, "Account menu");', silent) == []
    assert outsider.bare_stated_controls('await click(page, "Account menu");', silent) == [("click", "Account menu", "button")]

@case
def fill_and_verbs_that_do_not_take_the_role_are_not_judged():
    field = node_of('The form contains a textbox labeled "Email" and a button named "Save".')
    assert outsider.bare_stated_controls('await fill(page, "Email", "a@b.example");', field) == []
    assert outsider.bare_stated_controls('await check(page, "Save");', field) == []

@case
def the_whole_requirement_file_is_read_when_the_factory_attached_it():
    whole = ("id: ROOT" + NL + "children:" + NL + "- id: REQ-1" + NL + "  description: 'The home page provides a button with the accessible"
             + NL + "    name \"New blank workbook\".'" + NL)
    node = node_of("Rename a workbook.", whole=whole)
    assert outsider.bare_stated_controls('await click(page, "New blank workbook");', node) == [("click", "New blank workbook", "button")]

@case
def a_short_value_seen_on_the_whole_page_is_reported():
    spec = 'await click(page, { role: "gridcell", name: "C2" });' + NL + 'await see(page, "20");' + NL + "await see(page, '3');"
    assert outsider.page_wide_values(spec) == ["20", "3"]
    assert outsider.page_wide_values('await see(page, "ago");') == ["ago"]

@case
def a_scoped_long_variable_or_non_ascii_value_is_not_reported():
    assert outsider.page_wide_values('await see(page, "20", cell);') == []
    assert outsider.page_wide_values('await see(page, "3", item(page, "Invoice 1042"));') == []
    assert outsider.page_wide_values('await see(page, "1200"); await see(page, "123456");') == []
    assert outsider.page_wide_values('await see(page, "Quarterly report"); await see(page, total);') == []
    assert outsider.page_wide_values('await see(page, "华东");') == []
    assert outsider.page_wide_values('await seeOnce(page, "3"); await notSee(page, "3");') == []
    assert outsider.page_wide_values('await expect(locate(page, { role: "gridcell", name: "C2" })).toHaveText("20");') == []

FILTER = node_of('The condition dialog supports condition options named "Text contains", "Greater than", "Before", "Is empty", and "Is not empty".',
                 ("GIVEN", 'The sheet has values "Alpha", "Beta", and "Gamma" in column A'),
                 ("WHEN", 'The user chooses "Greater than" and enters 5'))

@case
def a_list_of_options_covered_by_a_sample_is_reported():
    found = outsider.sampled_lists('await choose(page, "Condition", "Greater than");', FILTER)
    assert len(found) == 1 and found[0][1] == ["Text contains", "Before", "Is empty", "Is not empty"], found

@case
def a_list_covered_whole_or_not_at_all_is_not_reported():
    every = NL.join(f'await choose(page, "Condition", "{o}");' for o in ("Text contains", "Greater than", "Before", "Is empty", "Is not empty"))
    assert outsider.sampled_lists(every, FILTER) == []
    assert outsider.sampled_lists('await click(page, "Apply");', FILTER) == []

@case
def data_sentences_and_mere_quotations_are_not_lists_of_options():

    given = node_of("Filter rows.", ("GIVEN", 'The menu provides options "Alpha", "Beta", and "Gamma"'))
    assert outsider.sampled_lists('await see(page, "Alpha");', given) == []
    seed = node_of('Seed data: the menu provides options "Alpha", "Beta", and "Gamma".')
    assert outsider.sampled_lists('await see(page, "Alpha");', seed) == []
    labels = node_of('The form labels are "Owner", "Repository name", and "Description".')
    assert outsider.sampled_lists('await fill(page, "Repository name", "x");', labels) == []
    values = node_of('The user opens the workbook and edits it with concrete values `East`, `1200`, `North`, and `800`.')
    assert outsider.sampled_lists('await see(page, "East");', values) == []

    seeded = dict(node_of("The region filter offers `East`, `West`, and `North`."), seed_sentences="Seed values: regions `East` and `West`.")
    assert outsider.sampled_lists('await click(page, "North");', seeded) == []

    menu = node_of('The row menu provides "Insert row above", "Insert row below", and "Delete row".')
    titled = 'test("REQ-9: Insert row below and Delete row", async ({ page }) => {' + NL + '  await click(page, "Insert row above");'
    assert [m for _i, m, _s in outsider.sampled_lists(titled, menu)] == [["Insert row below", "Delete row"]]

BRANCH = node_of("Switch branches.", whole=(
    'The selector is a unique button named "Branch <current branch name>"; it opens a textbox named "Find branch". '
    "Seed values: repository `acme-docs`, branches `main` and `feature-search`. "
    'The user selects the base and compare branches and clicks "Create pull request".'))

@case
def the_variable_part_of_a_templated_name_is_reported():
    found = outsider.untemplated_names('await click(page, { role: "button", name: "main" });', BRANCH)
    assert found == [("button", "main", "Branch <current branch name>", "Branch main")], found
    assert outsider.untemplated_names('await click(page, { role: "button", name: "feature-search" });', BRANCH)[0][3] == "Branch feature-search"

@case
def the_full_template_another_role_or_an_unrelated_name_is_not_reported():
    assert outsider.untemplated_names('await click(page, { role: "button", name: "Branch main" });', BRANCH) == []
    assert outsider.untemplated_names('await click(page, { role: "option", name: "main" });', BRANCH) == []

    assert outsider.untemplated_names('await click(page, { role: "button", name: "Create pull request" });', BRANCH) == []

    assert outsider.untemplated_names('await click(page, { role: "button", name: "feature/api-v2" });', BRANCH) == []

@case
def the_review_hands_every_new_finding_to_the_outsider():
    whole = (EXPORT["description"] + " " + FILTER["description"] + " " + BRANCH["requirement_text"])
    node = dict(FILTER, requirement_text=whole)
    spec = AuthoredFile(path="backend/test-e2e/REQ-9.spec.js", content=NL.join([
        'await download(page, "Export CSV");',
        'await click(page, { role: "button", name: "main" });',
        'await choose(page, "Condition", "Greater than");',
        'await see(page, "3");',
    ]))
    lines: list[str] = []
    problems = outsider.review_acceptance(Reviewer(), node, spec, "REQ-9", log=lines.append)
    assert '{ role: "button", name: "Export CSV" }' in problems, problems
    assert '{ role: "button", name: "Branch main" }' in problems

    assert "SCOPE" not in problems and 'see(page, "3")' not in problems, problems
    assert any("hint only" in line and "'3'" in line for line in lines), lines
    assert "PROBLEM: MISSING" in problems and '"Is not empty"' in problems
    clean = AuthoredFile(path="backend/test-e2e/REQ-9.spec.js", content=NL.join(
        ['await download(page, { role: "button", name: "Export CSV" });',
         'await click(page, { role: "button", name: "Branch main" });',
         *[f'await choose(page, "Condition", "{o}");' for o in ("Text contains", "Greater than", "Before", "Is empty", "Is not empty")],
         'await expect(locate(page, { role: "gridcell", name: "C2" })).toHaveText("3");']))
    assert outsider.review_acceptance(Reviewer(), node, clean, "REQ-9", log=lambda message: None) == ""

_BUDGET_A = "The evaluation seed contains the seeded workbook `Budget`, cells `A1=1` and `B1=5`."
_BUDGET_B = "The evaluation seed contains the seeded workbook `Budget`, cells `A1=2` and `C1=7`."
_UNNAMED = "The evaluation seed contains the seeded worksheet range `A1:C6` with headers `Region/Sales/Status`."

def _seed_tree(unnamed_when: str) -> dict:
    def leaf(i, description, scenarios):
        return {"id": f"REQ-{i}", "name": f"L{i}", "description": description,
                "scenarios": [{"name": f"S{n}", "steps": [{"keyword": "GIVEN", "content": given},
                                                          {"keyword": "WHEN", "content": when},
                                                          {"keyword": "THEN", "content": "x"}]}
                              for n, (given, when) in enumerate(scenarios)]}

    return {"id": "ROOT", "children": [
        leaf(1, f"{_BUDGET_A} Again: {_BUDGET_A}", [(_BUDGET_A, "The user opens `Budget`.")]),
        leaf(2, "Formulas.", [(_BUDGET_B, "The user opens `Budget`.")] * 3),
        leaf(3, "Filters.", [(_UNNAMED, unnamed_when)] * 2),
    ]}

@case
def seed_sentences_are_weighted_by_the_scenarios_that_state_them():
    import main as factory_module
    tree = _seed_tree("The user opens `Budget` and filters column A.")
    seeds = factory_module.seed_digest(tree)
    note = factory_module.seed_content_differences(seeds, tree)
    assert f"(3 scenario(s)) {_BUDGET_B}" in note and f"(1 scenario(s)) {_BUDGET_A}" in note, note
    assert note.index(_BUDGET_B) < note.index(_BUDGET_A)

    assert f"(2 scenario(s)) {_UNNAMED}" in note, note

    other = _seed_tree("The user opens the workbook `Forecast` and filters column A.")
    note = factory_module.seed_content_differences(factory_module.seed_digest(other), other)
    assert _UNNAMED not in note and _BUDGET_B in note, note
    mixed = _seed_tree("")
    mixed["children"][2]["scenarios"][0]["steps"][1]["content"] = "The user opens `Budget`."
    note = factory_module.seed_content_differences(factory_module.seed_digest(mixed), mixed)
    assert _UNNAMED not in note, note

    note = factory_module.seed_content_differences(seeds)
    assert "scenario(s)" not in note and _UNNAMED not in note and _BUDGET_A in note
    source = __import__("inspect").getsource(factory_module.Factory)
    assert "seed_content_differences(seeds, tree)" in source and "whole_requirement" not in source

@case
def the_outsider_prompt_follows_the_seed_union_rule():
    prompt = outsider.SYSTEM_PROMPT
    assert "also when the requirement NAMES the seeded record" not in prompt
    rule = " ".join(prompt[prompt.index("When the requirement NAMES that record"):].split())[:600]
    assert "SEED RECORDS WITH DIFFERENT CONTENTS" in rule and "union" in rule
    assert "relies on the seeded record exactly as its GIVEN says" in rule and "only a scenario whose sentence LOST a cell" in rule

@case
def the_prompt_teaches_the_role_pair_for_file_calls_without_task_literals():
    prompt = outsider.SYSTEM_PROMPT
    assert 'download(page, "Export CSV")' not in prompt and "Export CSV" not in prompt
    assert 'await download(page, { role: "button", name:' in prompt and 'await upload(page, { role: "button", name:' in prompt
    for literal in ("Q3 Sales", "Note editor", "Delete me 2.3.1", "Sheet2", "Import CSV", "Email address", "Approve", "Trash"):
        assert literal not in prompt, literal
    assert "A list is not satisfied by a sample" in prompt and "TEMPLATE" in prompt and "whole page also passes" in prompt

def main() -> int:
    if not __debug__:
        print("FAIL  spec cases need assertions: run without -O and without PYTHONOPTIMIZE")
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
    print(f"{len(CASES) - failed}/{len(CASES)} spec cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
