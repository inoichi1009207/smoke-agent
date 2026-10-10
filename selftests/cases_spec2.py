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

class Reviewer:

    def chat(self, messages, **options):
        return {"content": "OK"}

def leaf(req_id: str, description: str, *scenarios: list[tuple[str, str]], whole: str = "") -> dict:
    node = {"id": req_id, "name": "Feature", "description": description,
            "scenarios": [{"name": f"{req_id}: Scenario {i + 1}", "steps": [{"keyword": k, "content": c} for k, c in steps]}
                          for i, steps in enumerate(scenarios)]}
    node["requirement_text"] = whole or description
    return node

def batch(req_id: str, *members: dict, whole: str = "") -> dict:
    return {"id": req_id, "name": "Batch", "description": "This batch implements these sibling requirements together.",
            "scenarios": [], "_leaves": list(members), "requirement_text": whole}

def review(node: dict, spec: str) -> tuple[str, list[str]]:
    lines: list[str] = []
    problems = outsider.review_acceptance(Reviewer(), node, AuthoredFile(path="backend/test-e2e/X.spec.js", content=spec),
                                          "X", log=lines.append)
    return problems, lines

SEED_TEAM = ("The seeded data is organization `Acme Demo`, repository `acme-docs`, member `bob-reviewer`, team "
             "`frontend-team`.")

REQ_2_2_3 = ("The organization overview opens “People” through a link. Its button “Add member” opens the labeled field "
             "“Username or email”, a combobox “Role” defaulting to Member, and a submission button “Add member”. Opening "
             "Role exposes clickable options “Member” and “Owner”.")
REQ_2_3 = ("From the repository's “Settings” link, open the “Manage access” link and activate “Add people or teams”. The "
           "picker presents a textbox “Search”, a matching selectable team option whose name contains the team name, a "
           "combobox “Role” with clickable role options including “Write”, and an “Add” button. While the picker is "
           "active, hide the opening Add people or teams button from the active view so the submit action is "
           "unambiguous. After saving, the authorization list visibly shows the exact team name and Write role and "
           "retains the team after reload. Existing grants appear as rows whose accessible names include the subject "
           "name; each row contains a native select labeled “Role” and a “Save” button.")

TEAM_RULE = ("A team name contains 1–50 lowercase ASCII letters, digits, or hyphens, may not begin or end with a hyphen, "
             "and must be unique within the same organization; a team must belong to an organization.")
REQ_2_2_1_WHEN = ("The Owner clicks “New team”, enters team name `mobile-team`, which is 1 to 50 characters long containing "
                  "only lowercase ASCII letters, digits, or hyphens and not beginning or ending with a hyphen, optionally "
                  "enters a description and a parent team from the same organization, then clicks “Create team”.")

REQ_5_3_2 = ("Only users with Triage, Maintain, or Admin may apply or remove labels that already exist in the current "
             "repository; the system stores the issue-label relationship and activity record. The sidebar has a button "
             "named “Labels”, opening options whose exact accessible names are the current repository's label names. "
             "Seed data provides an existing label not yet applied to the target issue. Clicking its option immediately "
             "saves the association and closes the picker, without requiring a separate Save button; the label name is "
             "visible on the detail page and remains after reload.")
REQ_5_3_2_GIVEN = ("The seeded data is repository `acme-docs`, issue `Improve onboarding`, labels `bug` and `documentation`, "
                   "milestone `Q3 launch`.")
REQ_5_3_2_WHEN = ("The user opens the selector under “Labels” on the right, checks `bug`, saves, then reopens the selector "
                  "and unchecks `bug`.")

REQ_3_4 = ("Open the repository's “Settings” link and then its “General” link to reach Danger Zone. The visibility action is "
           "a button “Change visibility”; the confirmation flow provides a “Public” radio and a button “Confirm "
           "visibility”. Selecting Public and activating Confirm visibility completes the change without requiring the "
           "repository name to be retyped or any additional mandatory field. A non-Admin collaborator must not see the "
           "Change visibility button, including when a Settings link is available.")
REQ_3_4_WHEN = ("The Admin enters “Settings” → “General” → “Danger Zone”, selects changing visibility to Public, clicks "
                "“Change visibility”, enters the full repository name as required by the page in the confirmation dialog, "
                "and confirms.")

REQ_4_3_1 = ("Matching branch options or the “No matching branch” state update as the user types in the branch textbox; "
             "pressing Enter or activating a separate search button is not required. The selector is a unique button "
             "named “Branch <current branch name>”; it opens a textbox named “Find branch” and selectable items with role "
             "option whose exact accessible names are the branch names. Seed data includes active branch `main`, target "
             "branch `feature-search` containing the file `main-only.md` absent from `main`, and an unknown search term "
             "matching no branch. Selecting the target immediately updates the button's branch name and shows a link with "
             "the exact target-only file name. Escape closes the selector; for an unmatched search it preserves the "
             "original active branch before and after reload.")

SPEC_2_2_3 = NL.join([
    'test("REQ-2-2-3: Directly Add a User as an Organization Member - Scenario 2", async ({ page }) => {',
    '  const form = region(page, "form");',
    '  await click(page, { role: "button", name: "Add member" });',
    '  await fill(page, "Username or email", "bob-reviewer", form);',
    '  await click(page, "Role", form);',
    '  await see(page, { role: "option", name: "Member" });',
    '});'])
SPEC_2_3 = NL.join([
    'const teamName = "Outsider " + Date.now() + "-" + Math.random().toString(36).slice(2, 6);',
    '',
    'test("REQ-2-3: Grant Repository Access to People and Teams - Scenario 1", async ({ page }) => {',
    '  await click(page, "Add people or teams");',
    '  await fill(page, "Search", "frontend-team");',
    '  await click(page, "frontend-team");',
    '  await choose(page, "Role", "Write");',
    '  await click(page, "Add");',
    '});',
    '',
    'test("REQ-2-3: Grant Repository Access to People and Teams - Scenario 2", async ({ page }) => {',
    '  await click(page, "New team");',
    '  await fill(page, "Team name", teamName);',
    '  await click(page, "Create team");',
    '  await click(page, "Add people or teams");',
    '  await fill(page, "Search", teamName);',
    '  await click(page, teamName);',
    '  await choose(page, "Role", "Write");',
    '});'])
SPEC_5_3_2 = NL.join([
    'test("REQ-5-3-2: Apply Labels to an Issue - Scenario 1", async ({ page }) => {',
    '  await click(page, { role: "button", name: "Labels" });',
    '  await check(page, { role: "checkbox", name: "bug" });',
    '  await notSee(page, { role: "button", name: "Labels" });',
    '  await see(page, "bug");',
    '  await click(page, { role: "button", name: "Labels" });',
    '  await check(page, { role: "checkbox", name: "bug" }, false);',
    '});'])
SPEC_3_4 = NL.join([
    'test("REQ-3-4: Change Repository Visibility with Permission Checks - Scenario 1", async ({ page }) => {',
    '  await check(page, { role: "radio", name: "Public" });',
    '  await click(page, { role: "button", name: "Change visibility" });',
    '  await fill(page, "secret-research-wrong", "not-the-name");',
    '  await click(page, { role: "button", name: "Confirm visibility" });',
    '  await page.goto("/alice-dev/secret-research");',
    '  await see(page, "Private");',
    '});'])
SPEC_4_3_1 = NL.join([
    'test("REQ-4-3-1: List and Switch Repository Branches - Scenario 2", async ({ page }) => {',
    '  await click(page, { role: "button", name: "Branch main" });',
    '  await fill(page, { role: "textbox", name: "Find branch" }, "no-such-branch-xyz");',
    '  await see(page, "No matching branch");',
    '  await press(page, "Escape");',
    '  await see(page, { role: "button", name: "Branch main" });',
    '  await page.reload();',
    '  await see(page, { role: "button", name: "Branch main" });',
    '});'])
SPEC_4_1_1 = NL.join([
    'async function setCell(page, coord, value) {',
    '  await click(page, { role: "gridcell", name: coord });',
    '  await fill(page, { role: "textbox", name: "Formula bar" }, value);',
    '  await press(page, "Enter");',
    '}',
    '',
    'test("REQ-4-1-1: aggregate functions ignore blanks and count only numbers", async ({ page }) => {',
    '  await setCell(page, "A1", "10");',
    '  await setCell(page, "A2", "20");',
    '  await setCell(page, "A4", "30");',
    '  await setCell(page, "B1", "x");',
    '  await setCell(page, "C2", "=AVERAGE(A1:A4)");',
    '  await setCell(page, "C3", "=COUNT(A1:B4)");',
    '  await click(page, { role: "gridcell", name: "C2" });',
    '  await see(page, "20");',
    '  await click(page, { role: "gridcell", name: "C3" });',
    '  await see(page, "3");',
    '});',
    '',
    'test("REQ-4-1-1: dependent formulas recalculate when source changes", async ({ page }) => {',
    '  await setCell(page, "A1", "2");',
    '  await setCell(page, "B1", "3");',
    '  await setCell(page, "C1", "=A1+B1");',
    '  await setCell(page, "D1", "=C1*2");',
    '  await see(page, "10");',
    '  await setCell(page, "A1", "10");',
    '});'])

NODE_2_2_3 = leaf("REQ-2-2-3", REQ_2_2_3, [("GIVEN", SEED_TEAM)], whole=REQ_2_2_3 + " " + REQ_2_3 + " " + SEED_TEAM)

@case
def item4_the_delivered_bare_click_on_the_stated_combobox_is_reported():

    assert ("click", "Role", "combobox") in outsider.bare_stated_controls(SPEC_2_2_3, NODE_2_2_3)
    flat = " ".join(NODE_2_2_3["requirement_text"].split())
    assert outsider._mentions("Role", "combobox", flat) == (False, False)
    problems, _lines = review(NODE_2_2_3, SPEC_2_2_3)
    assert 'PROBLEM: ROLE' in problems and '{ role: "combobox", name: "Role" }' in problems, problems

@case
def item4_a_bare_choose_is_judged_and_the_role_pair_passes():
    assert ("choose", "Role", "combobox") in outsider.bare_stated_controls('await choose(page, "Role", "Write");', NODE_2_2_3)
    assert outsider.bare_stated_controls('await choose(page, { role: "combobox", name: "Role" }, "Write");', NODE_2_2_3) == []
    assert outsider.bare_stated_controls('await click(page, { role: "combobox", name: "Role" }, form);', NODE_2_2_3) == []

    column = leaf("REQ-9", "The table has a combobox “Role”. The “Role” column lists each member's role.")
    assert outsider.bare_stated_controls('await click(page, "Role");', column) == []
    verb = leaf("REQ-9", "The dialog has a combobox “Role”. The owner selects “Role” and saves.")
    assert outsider.bare_stated_controls('await see(page, "Role");', verb) == []

@case
def item4_the_helper_honours_a_role_given_to_choose():
    body = outsider.HELPER_JS.split("async function choose(")[1].split("\nasync function ")[0]
    assert "roleOf(name) ? [byRole(root, name)]" in body and "list.map(describe)" in body

NODE_2_3 = leaf("REQ-2-3", REQ_2_3, [("GIVEN", SEED_TEAM)], [("GIVEN", SEED_TEAM)], whole=TEAM_RULE + " " + REQ_2_3 + " " + SEED_TEAM)
NODE_5_3 = batch("REQ-5-3.part2",
                 leaf("REQ-5-3-2", REQ_5_3_2, [("GIVEN", REQ_5_3_2_GIVEN), ("WHEN", REQ_5_3_2_WHEN)]),
                 leaf("REQ-5-3-3", "The sidebar has a button named “Milestone”.", [("GIVEN", REQ_5_3_2_GIVEN)]),
                 whole=REQ_5_3_2 + " " + REQ_5_3_2_GIVEN)

@case
def item19_the_delivered_team_pick_by_bare_word_is_reported():
    found = outsider.option_entries_not_as_options(SPEC_2_3, NODE_2_3)
    calls = [call for _title, call, _pair, _sentence in found]

    assert calls == ['click(page, "frontend-team")', "click(page, teamName)"], found
    assert "selectable team option" in found[0][3]

@case
def item19_the_delivered_label_checkbox_is_reported_once():
    found = outsider.option_entries_not_as_options(SPEC_5_3_2, NODE_5_3)
    assert [(call, pair) for _t, call, pair, _s in found] == [
        ('check(page, { role: "checkbox", name: "bug" })', '{ role: "option", name: "bug" }')], found

@case
def item19_options_other_leaves_and_looks_are_left_alone():
    as_options = SPEC_5_3_2.replace('check(page, { role: "checkbox", name: "bug" })', 'click(page, { role: "option", name: "bug" })') \
        .replace('check(page, { role: "checkbox", name: "bug" }, false)', 'click(page, { role: "option", name: "bug" })')
    assert outsider.option_entries_not_as_options(as_options, NODE_5_3) == []

    other = ('test("REQ-5-3-3: Assign a milestone - Scenario 1", async ({ page }) => {' + NL
             + '  await click(page, "bug");' + NL + '});')
    assert outsider.option_entries_not_as_options(other, NODE_5_3) == []

    looks = ('test("REQ-2-3: x", async ({ page }) => {' + NL + '  await see(page, "frontend-team");' + NL
             + '  await fill(page, "Team name", teamName);' + NL + '  await click(page, "Create team");' + NL
             + '  await click(page, teamName);' + NL + '});')
    assert outsider.option_entries_not_as_options(looks, NODE_2_3) == []

@case
def item6_the_delivered_team_name_template_is_reported():
    found = outsider.misformatted_generated_values(SPEC_2_3, NODE_2_3)
    assert len(found) == 1 and found[0][0] == "Team name" and found[0][1] == "teamName", found
    assert "upper-case letters" in found[0][3] and "spaces" in found[0][3]

    node = leaf("REQ-2-3", REQ_2_3, [("WHEN", REQ_2_2_1_WHEN)], whole=REQ_2_3 + " " + REQ_2_2_1_WHEN)
    assert [f[0] for f in outsider.misformatted_generated_values(SPEC_2_3, node)] == ["Team name"]
    problems, _lines = review(NODE_2_3, SPEC_2_3)
    assert "PROBLEM: DATA" in problems and '"outsider-" + Date.now()' in problems, problems

@case
def item6_a_value_in_the_format_a_lowered_one_or_a_rejected_one_is_not_reported():
    ok = SPEC_2_3.replace('"Outsider " + Date.now()', '"outsider-" + Date.now()')
    assert outsider.misformatted_generated_values(ok, NODE_2_3) == []
    lowered = SPEC_2_3.replace('const teamName = "Outsider " + Date.now()', 'const teamName = ("Outsider " + Date.now()).toLowerCase()')
    assert outsider.misformatted_generated_values(lowered, NODE_2_3) == []

    rejected = NL.join(['const other = "Outsider Team " + Date.now();',
                        'test("REQ-2-3: Scenario 2", async ({ page }) => {',
                        '  await fill(page, "Team name", other);', '  await click(page, "Create team");',
                        '  await see(page, "Team name format is invalid");', '});'])
    assert outsider.misformatted_generated_values(rejected, NODE_2_3) == []

    free = leaf("REQ-9", "The form has a textbox “Description”.", [("GIVEN", "x")], whole="The form has a textbox “Description”.")
    spec = NL.join(['const d = "Outsider " + Date.now();', 'test("REQ-9: a", async ({ page }) => {',
                    '  await fill(page, "Description", d);', '  await see(page, d);', '});'])
    assert outsider.misformatted_generated_values(spec, free) == []

NODE_3 = batch("REQ-3.part2", leaf("REQ-3-3", "View a public repository overview."),
               leaf("REQ-3-4", REQ_3_4, [("WHEN", REQ_3_4_WHEN)]), whole=REQ_3_4)

@case
def item7_the_delivered_retyped_name_is_reported():
    found = outsider.typed_despite_limit(SPEC_3_4, NODE_3)
    assert [(f[1], f[2]) for f in found] == [('fill(page, "secret-research-wrong", "not-the-name")', "Confirm visibility")], found
    assert "without requiring the repository name to be retyped" in found[0][3]
    problems, _lines = review(NODE_3, SPEC_3_4)
    assert "PROBLEM: LIMIT" in problems and "description outranks" in problems

@case
def item7_no_typing_or_a_limit_about_something_else_is_not_reported():
    plain = SPEC_3_4.replace('  await fill(page, "secret-research-wrong", "not-the-name");' + NL, "")
    assert outsider.typed_despite_limit(plain, NODE_3) == []

    earlier = SPEC_3_4.replace('  await fill(page, "secret-research-wrong", "not-the-name");' + NL, "") \
        .replace('  await check(page, { role: "radio", name: "Public" });',
                 '  await fill(page, "Description", "ready");' + NL + '  await click(page, "Save");' + NL
                 + '  await check(page, { role: "radio", name: "Public" });')
    assert outsider.typed_despite_limit(earlier, NODE_3) == []

    node = leaf("REQ-5-3-2", REQ_5_3_2)
    spec = ('test("REQ-5-3-2: a", async ({ page }) => {' + NL + '  await fill(page, "Comment", "looks good");' + NL
            + '  await click(page, "Save");' + NL + '});')
    assert outsider.typed_despite_limit(spec, node) == []

    node = leaf("REQ-9", "Clicking “Post” publishes it without requiring the comment to be approved by an owner.")
    spec = ('test("REQ-9: a", async ({ page }) => {' + NL + '  await fill(page, "Comment", "looks good");' + NL
            + '  await click(page, "Post");' + NL + '});')
    assert outsider.typed_despite_limit(spec, node) == []

    node = leaf("REQ-4-3-1", REQ_4_3_1)
    spec = ('test("REQ-4-3-1: a", async ({ page }) => {' + NL + '  await fill(page, "Find branch", "feature-search");' + NL
            + '  await click(page, { role: "option", name: "feature-search" });' + NL + '});')
    assert outsider.typed_despite_limit(spec, node) == []

NODE_4_3_1 = leaf("REQ-4-3-1", REQ_4_3_1, [("GIVEN", "x")], [("GIVEN", "x")])

@case
def item8_the_delivered_escape_without_a_disappearance_check_is_reported():
    found = outsider.unasserted_closes(SPEC_4_3_1, NODE_4_3_1)
    assert len(found) == 1 and 'notSee(page, { role: "textbox", name: "Find branch" })' in found[0][1], found
    problems, _lines = review(NODE_4_3_1, SPEC_4_3_1)
    assert "PROBLEM: MISSING" in problems and "Escape" in problems

@case
def item8_a_checked_close_or_no_close_statement_is_not_reported():
    checked = SPEC_4_3_1.replace('  await press(page, "Escape");', '  await press(page, "Escape");' + NL
                                 + '  await notSee(page, { role: "textbox", name: "Find branch" });')
    assert outsider.unasserted_closes(checked, NODE_4_3_1) == []
    silent = leaf("REQ-4-3-1", REQ_4_3_1.replace("Escape closes the selector; for", "For"))
    assert outsider.unasserted_closes(SPEC_4_3_1, silent) == []

SHEET = leaf("REQ-4-1-1", "Aggregate functions ignore empty cells.", [("GIVEN", "x")])

@case
def item10_the_delivered_typed_then_seen_values_are_reported():
    assert outsider.typed_values_seen_page_wide(SPEC_4_1_1) == [
        ("REQ-4-1-1: aggregate functions ignore blanks and count only numbers", "20")]

    problems, lines = review(SHEET, SPEC_4_1_1)

    assert 'PROBLEM: SCOPE' not in problems, problems
    assert any("hint only" in line and "'20' it typed itself" in line for line in lines), lines

    assert '"3"' not in problems and any("hint only" in line and "'3'" in line for line in lines), lines

@case
def item10_scoped_or_direct_typed_values():
    direct = ('test("REQ-9: a", async ({ page }) => {' + NL + '  await fill(page, "Amount", "42");' + NL
              + '  await see(page, "42");' + NL + '});')
    assert outsider.typed_values_seen_page_wide(direct) == [("REQ-9: a", "42")]
    scoped = direct.replace('see(page, "42")', 'see(page, "42", row)')
    assert outsider.typed_values_seen_page_wide(scoped) == []
    other = direct.replace('see(page, "42")', 'see(page, "84")')
    assert outsider.typed_values_seen_page_wide(other) == []

@case
def item11_drag_presses_moves_in_steps_with_pauses_and_releases():
    helper = outsider.HELPER_JS
    body = helper.split("async function drag(")[1].split("\nfunction ")[0]
    assert "async function drag(page, from, to, within)" in helper and "drag," in helper.split("module.exports")[1]
    assert "dragTo" not in body
    down, loop, up = body.index("page.mouse.down()"), body.index("for (let i = 1; i <= DRAG_STEPS"), body.index("page.mouse.up()")
    assert down < loop < up and "setTimeout(r, DRAG_PAUSE_MS)" in body[loop:up]
    assert "const DRAG_STEPS = 8;" in helper and "const DRAG_PAUSE_MS = 40;" in helper

@case
def the_prompt_rules_are_there_short_and_without_task_literals():
    prompt = outsider.SYSTEM_PROMPT
    flat = " ".join(prompt.split())
    for rule in ('click, choose, download, upload', 'a stated combobox too', '{ role: "option", name: <entry> }',
                 'the seed or the scenario SUPPLIES', '"outsider-" + Date.now()', 'the generated value keeps to it',
                 '"without requiring X"', 'Every clause of a THEN gets its own assertion', 'CLOSES it',
                 'on the very input the test typed it into'):
        assert rule in flat, rule
    assert '"Outsider " + Date.now()' not in prompt
    for literal in ("Confirm visibility", "Find branch", "frontend-team", "Team name", "“Role”", '"Role"', "secret-research",
                    "Manage access", "Add people or teams", "label names", "team option", "mobile-team", "Escape closes the selector"):
        assert literal not in prompt, literal
    review_prompt = " ".join(outsider.REVIEW_PROMPT.split())
    assert "SEED SUPPLY:" in review_prompt and "LIMIT:" in review_prompt and "a clause of one of its THENs" in review_prompt

@case
def the_review_hands_the_new_findings_back_and_a_clean_spec_passes():

    fixed = SPEC_2_3.replace('"Outsider " + Date.now()', '"outsider-" + Date.now()') \
        .replace('click(page, "frontend-team")', 'click(page, { role: "option", name: "frontend-team" })') \
        .replace('click(page, teamName)', 'click(page, { role: "option", name: teamName })') \
        .replace('choose(page, "Role", "Write")', 'choose(page, { role: "combobox", name: "Role" }, "Write")') \
        .replace('click(page, "Add")', 'click(page, { role: "button", name: "Add" })') \
        .replace('click(page, "New team")', 'click(page, { role: "link", name: "New team" })')
    assert outsider.option_entries_not_as_options(fixed, NODE_2_3) == []
    assert outsider.misformatted_generated_values(fixed, NODE_2_3) == []
    assert [x for x in outsider.bare_stated_controls(fixed, NODE_2_3) if x[1] in ("Role", "Add")] == []

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
    print(f"{len(CASES) - failed}/{len(CASES)} spec2 cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
