from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import coverage

CASES: list = []

def case(function):
    CASES.append(function)
    return function

BASE = ("The folder page has a button named “Rename”. Renaming shows the message “Folder renamed”. The new name remains "
        "listed after a reload of the page. An empty name is rejected with “Name is required”. The list shows the "
        "owner of each folder. ")
ROLES = ("Every account holds one of the roles Viewer, Commenter, Editor, or Manager. ")

def _leaf(leaf_id, description, scenarios=()):
    return {"id": leaf_id, "name": "Folders", "description": description, "scenarios": list(scenarios)}

def _node(*leaves):
    return {"id": leaves[0]["id"], "name": "x", "members": [l["id"] for l in leaves], "_leaves": list(leaves)}

def _texts(node):
    return [t for _leaf, numbered in coverage.ledger(node) for _n, t in numbered]

def _derived(node):
    return [t for t in _texts(node) if coverage.TAG.match(t)]

@case
def refusals_name_each_excluded_role_highest_first():
    node = _node(_leaf("REQ-1", ROLES + BASE + "Only an Editor or Manager may rename a folder; Viewer and Commenter may only view it."))
    refusals = [t for t in _derived(node) if coverage._kind(t) == "refusal"]
    assert [t.split("]")[0] for t in refusals] == ["[refusal: Commenter", "[refusal: Viewer"], refusals
    assert all("only Editor, Manager may rename a folder" in t for t in refusals), refusals

@case
def a_role_below_the_list_is_excluded_one_above_is_not():
    node = _node(_leaf("REQ-1", ROLES + BASE + "Only Editor may archive a folder."))
    kinds = [t.split("]")[0] for t in _derived(node) if coverage._kind(t) == "refusal"]
    assert kinds == ["[refusal: Commenter", "[refusal: Viewer"], kinds

@case
def refusals_are_bounded_per_leaf():
    text = ROLES + BASE + "Only Manager may delete a folder. Only Manager may move a folder. Only Manager may share a folder."
    refusals = [t for t in _derived(_node(_leaf("REQ-1", text))) if coverage._kind(t) == "refusal"]
    assert len(refusals) == coverage.MAX_REFUSALS, refusals

    assert [t.split("]")[0] for t in refusals] == ["[refusal: Editor"] * 3, refusals

@case
def without_a_role_list_one_refusal_for_an_account_without_the_role():
    node = _node(_leaf("REQ-1", BASE + "Only an organization Owner can click “Invite” on the members page."))
    refusals = [t for t in _derived(node) if coverage._kind(t) == "refusal"]
    assert refusals and refusals[0].startswith("[refusal: not Owner] only Owner may click"), refusals

@case
def a_visibility_rule_or_a_plain_sentence_gives_no_refusal():
    for extra in ("Only the owner can see the button “Delete”.", "Only one folder can be open at a time.",
                  "Viewer and Commenter may only view folders.", "The Editor may rename a folder."):
        node = _node(_leaf("REQ-1", ROLES + BASE + extra))
        assert not [t for t in _derived(node) if coverage._kind(t) == "refusal"], (extra, _derived(node))

@case
def a_qualified_target_gets_one_counter_example():

    node = _node(_leaf("REQ-1", ROLES + BASE + "A Manager or Editor may assign a folder only to a current team member or group."))
    counter = [t for t in _derived(node) if coverage._kind(t) == "counter-example"]
    assert len(counter) == 1 and "not current team member or group" in counter[0], counter
    plain = _node(_leaf("REQ-1", ROLES + BASE + "A Manager or Editor may assign a folder on the page of the current project."))
    assert not [t for t in _derived(plain) if coverage._kind(t) == "counter-example"], _derived(plain)

REFUSAL_LEAF = _leaf("REQ-1", ROLES + BASE + "Only Editor or Manager may rename a folder; Viewer and Commenter may only view it.")

def _spec(comment: str, body: str) -> str:
    return (f'const {{ test }} = require("@playwright/test");\n{comment}\ntest("REQ-1: x", async ({{ page }}) => {{\n'
            f'  await page.goto("/");\n{body}\n}});\n')

@case
def a_refusal_is_covered_only_by_a_test_that_asserts_an_absence():
    node = _node(REFUSAL_LEAF)
    found = coverage.ledger(node)
    refusals = [n for _l, numbered in found for n, t in numbered if coverage._kind(t) == "refusal"]
    every = ", ".join(f"S{n}" for _l, numbered in found for n, _t in numbered)
    positive = _spec(f"// covers {every}", '  await see(page, "Rename");')
    assert len(refusals) == 2 and [n for _l, n, _t in coverage.uncovered(node, positive)] == refusals, coverage.uncovered(node, positive)
    negative = _spec(f"// covers {every}", '  await notSee(page, { role: "button", name: "Rename" });')
    assert coverage.uncovered(node, negative) == []

@case
def a_refusal_may_be_marked_not_testable_within_the_cap():
    node = _node(REFUSAL_LEAF)
    found = coverage.ledger(node)
    number = next(n for _l, numbered in found for n, t in numbered if t.startswith("[refusal: Viewer"))
    others = ", ".join(f"S{n}" for _l, numbered in found for n, _t in numbered if n != number)
    spec = f"// not testable: S{number} -- no stated account\n" + _spec(f"// covers {others}", '  await notSee(page, "Rename");')
    assert coverage.refused_marks(node, spec) == [] and coverage.uncovered(node, spec) == []
    assert not coverage.testable_by_construction(f"[refusal: Viewer] only Editor may rename “Folder”")
    assert coverage.testable_by_construction("[THEN Scenario 1] the message “Saved” is shown")

SCENARIOS = [{"name": "REQ-1: Scenario 1", "steps": [
    {"keyword": "GIVEN", "content": "An Editor is signed in on the folder page."},
    {"keyword": "WHEN", "content": "The user opens the move dialog, confirms the displayed target folder, and clicks “Move”."},
    {"keyword": "THEN", "content": "The folder is listed under the target folder; the old parent no longer lists it, and "
                                   "the activity list shows the move. Seed values: folder `Alpha`, folder `Beta`."}]},
    {"name": "REQ-1: Scenario 2", "steps": [
        {"keyword": "WHEN", "content": "The user clicks the short name of a folder or chooses it from the folder picker."},
        {"keyword": "THEN", "content": "The folder page opens."}]}]

@case
def when_alternatives_confirmation_steps_and_then_sub_clauses_are_listed():
    derived = _derived(_node(_leaf("REQ-1", BASE + "Moving a folder keeps its contents.", SCENARIOS)))
    whens = [t for t in derived if coverage._kind(t) == "WHEN"]
    thens = [t for t in derived if coverage._kind(t) == "THEN"]
    assert any(t.startswith("[WHEN Scenario 1, confirmation step] confirms the displayed target folder") for t in whens), whens
    assert [t.split("]")[0] for t in whens if "alternative" in t] == ["[WHEN Scenario 2, alternative 1", "[WHEN Scenario 2, alternative 2"], whens
    assert any("chooses it from the folder picker" in t for t in whens), whens
    assert len(thens) == 3 and not any("Seed values" in t for t in thens), thens
    assert any("the old parent no longer lists it" in t for t in thens), thens

TREE = """
id: ROOT
children:
- id: REQ-1
  name: Folders
  description: A folder name must be at most 40 characters and unique within its parent folder. The folder list shows every folder.
  children:
  - id: REQ-1-1
    name: Create a folder
    description: %s
    scenarios: []
  - id: REQ-1-2
    name: Rename a folder
    description: %s
    scenarios: []
""" % ("The page has a button named “New folder”. Creating shows the message “Folder created”. The new folder remains listed "
       "after a reload of the page. An empty name is rejected with “Name is required”. The list shows the creator of each "
       "folder.", "The folder page has a button named “Rename”. Renaming shows the message “Folder renamed”. The folder "
       "remains listed after a reload of the page. The renamed folder shows its owner.")

@case
def a_folder_rule_goes_to_the_one_leaf_that_shares_most_words():
    node = {"id": "REQ-1-1", "name": "x", "members": ["REQ-1-1"], "requirement_text": TREE}
    derived = _derived(node)
    assert any(t.startswith("[folder REQ-1] A folder name must be at most 40 characters") for t in derived), derived
    other = {"id": "REQ-1-2", "name": "x", "members": ["REQ-1-2"], "requirement_text": TREE}
    assert not [t for t in _derived(other) if coverage._kind(t) == "folder"], _derived(other)

@case
def description_numbers_are_unchanged_and_short_leaves_get_nothing():
    leaf = _leaf("REQ-1", ROLES + BASE + "Only Editor or Manager may rename a folder; Viewer and Commenter may only view it.")
    second = _leaf("REQ-2", BASE.replace("folder", "file") + "Only Manager may delete a file.")
    numbered = coverage.ledger(_node(leaf, second))
    described = [(n, t) for _l, items in numbered for n, t in items if not coverage.TAG.match(t)]
    plain = [s.text for s in coverage.statements_of(leaf["description"])] + [s.text for s in coverage.statements_of(second["description"])]
    assert [t for _n, t in described] == plain and [n for n, _t in described] == list(range(1, len(plain) + 1)), described
    assert min(n for _l, items in numbered for n, t in items if coverage.TAG.match(t)) == len(plain) + 1
    assert all(len(items) <= coverage.CAP for _l, items in numbered)
    short = _node(_leaf("REQ-3", "Only Manager may delete a file. The list shows files."))
    assert coverage.ledger(short) == []

@case
def the_outsider_block_and_the_test_author_prompt_carry_them():
    from agent import outsider, test_author
    node = _node(REFUSAL_LEAF)
    block = outsider.coverage_block(node, "REQ-1")
    assert "[refusal: Commenter]" in block and coverage.DERIVED_RULES in block, block
    note = test_author.refusal_note(node)
    assert note.startswith(coverage.REFUSAL_API_HEAD) and "[refusal: Viewer]" in note, note
    plain = _node(_leaf("REQ-1", BASE))
    assert coverage.DERIVED_RULES not in outsider.coverage_block(plain, "REQ-1") and test_author.refusal_note(plain) == ""

    class _LLM:
        last_finish_reason = "stop"

        def __init__(self):
            self.calls = []

        def chat(self, messages, **kwargs):
            self.calls.append(messages)
            return {"content": '=== FILE: backend/tests/REQ-1.test.js ===\nit("x", () => {});\n=== END FILE ==='}

    llm = _LLM()
    test_author.TestAuthor(llm, log=lambda *_: None).author(node, "REQ-1", with_e2e=False)
    assert coverage.REFUSAL_API_HEAD in llm.calls[0][1]["content"]
