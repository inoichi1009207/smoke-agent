from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from agent.outsider import (unstated_navigation, plan_role_conflicts, later_controls, certain_spec_defects,
                            plan_block, deterministic_review)
from agent.test_author import AuthoredFile

HEAD = 'const { test } = require("@playwright/test");\nconst { click, fill, see, press, freshSession } = require("../e2e-support/outsider");\n'
SIGN_IN = ('  await page.goto("/");\n  await click(page, { role: "link", name: "Sign in" });\n'
           '  await fill(page, "Username or email", "dev-one");\n  await fill(page, "Password", "Pw-1!a");\n'
           '  await click(page, { role: "button", name: "Sign in" });\n')
DESC = ("Signed-in scenarios begin with the unique “Sign in” link on the home page, fields labeled “Username or email” "
        "and “Password”, and the “Sign in” button. Repository pages offer `Issues` and `Pull requests` links.")

def _node(scenarios: list[tuple[str, str, str]], leaf_id: str = "REQ-7", description: str = DESC) -> dict:
    import yaml
    leaf = {"id": leaf_id, "name": "Demo", "description": description, "scenarios": [
        {"name": name, "steps": [{"keyword": "GIVEN", "content": g}, {"keyword": "WHEN", "content": w},
                                 {"keyword": "THEN", "content": "The page shows the result."}]} for name, g, w in scenarios]}
    return {"id": leaf_id, "members": [leaf_id], "requirement_text": yaml.safe_dump({"id": "ROOT", "children": [leaf]}, allow_unicode=True, sort_keys=False)}

def _spec(title: str, body: str) -> str:
    return HEAD + f'test("{title}", async ({{ page }}) => {{\n{body}}});\n'

T1 = "REQ-7: Demo - Scenario 1"
GIVEN_REPO = ("The visitor starts at the application home page in a fresh unauthenticated browser session. The system "
              "pre-provisions public repository `demo-repo` with an Open issue titled `Fix login`.")

def a_given_mentioned_record_is_no_licence():
    node = _node([(T1, GIVEN_REPO, "The visitor opens the Issues page and clicks the “Open” link.")])
    spec = _spec(T1, '  await page.goto("/");\n  await click(page, "demo-repo");\n  await see(page, "Public");\n'
                     '  await click(page, "Issues");\n  await click(page, { role: "link", name: "Open" });\n')
    p = unstated_navigation(spec, node)
    assert len(p) == 1 and '"demo-repo" before "Issues"' in p[0] and "only mentions it as a record" in p[0], p

def a_page_link_counts_not_only_menus():
    node = _node([(T1, "The system pre-provisions account `dev-one` with email `dev-one@demo.test` and password `Pw-1!a`.",
                   "The visitor signs in as `dev-one`, opens Settings and clicks “Update password”.")])
    spec = _spec(T1, SIGN_IN + '  await click(page, "Your profile");\n  await click(page, "Settings");\n')
    p = unstated_navigation(spec, node)
    assert len(p) == 1 and '"Your profile" before "Settings"' in p[0], p

def a_hop_to_an_implied_subject_is_reported():
    node = _node([(T1, "The system pre-provisions public repository `demo-repo`, the separate Open PR `Closable demo PR` "
                       "and the author account `dev-one` with email `dev-one@demo.test`, password `Pw-1!a`.",
                   "The author clicks `Close pull request`.")])
    spec = _spec(T1, SIGN_IN + '  await click(page, "demo-repo");\n  await click(page, "Pull requests");\n'
                     '  await click(page, "Closable demo PR");\n  await click(page, "Close pull request");\n')
    p = unstated_navigation(spec, node)
    assert len(p) == 1 and '"demo-repo" before "Closable demo PR"' in p[0] and "implied step" in p[0], p

def a_spec_that_never_tries_the_first_entry_is_reported():
    node = _node([(T1, "Public repository `demo-repo` has a valid `Compare` entry.",
                   "The visitor opens `Compare` and clicks `Create pull request`.")])
    spec = _spec(T1, '  await page.goto("/");\n  await click(page, "demo-repo");\n  await click(page, "New pull request");\n'
                     '  await click(page, "Create pull request");\n')
    p = unstated_navigation(spec, node)
    assert len(p) == 1 and 'never clicks "Compare"' in p[0], p

def a_helper_with_bound_arguments_is_followed():
    node = _node([(T1, GIVEN_REPO, "The visitor opens the `Fix login` issue entry.")])
    spec = HEAD + ('async function openRepo(page, name) {\n  await page.goto("/");\n  await click(page, { role: "link", name });\n}\n'
                   f'test("{T1}", async ({{ page }}) => {{\n  await openRepo(page, "demo-repo");\n'
                   '  const TITLE = "Fix login";\n  await click(page, TITLE);\n});\n')
    p = unstated_navigation(spec, node)
    assert len(p) == 1 and '"demo-repo" before "Fix login"' in p[0], p

def role_conflicts_on_a_stated_entry():
    node = _node([(T1, "The system pre-provisions account `dev-one` with email `dev-one@demo.test` and password `Pw-1!a`.",
                   "The visitor signs in as `dev-one` and opens the Issues page.")])
    swapped = SIGN_IN.replace('{ role: "link", name: "Sign in" }', '{ role: "button", name: "Sign in" }')
    p = plan_role_conflicts(_spec(T1, swapped + '  await click(page, "Issues");\n'), node)
    assert len(p) == 1 and 'states a link named "Sign in"' in p[0] and not p[0].startswith("ENTRY"), p
    bare = SIGN_IN.replace('{ role: "link", name: "Sign in" }', '"Sign in"')
    p2 = plan_role_conflicts(_spec(T1, bare + '  await click(page, "Issues");\n'), node)
    assert len(p2) == 1 and p2[0].startswith("ENTRY | ") and "bare word" in p2[0], p2

def certain_defects_are_path_and_role_only():
    lines = ["PROBLEM: PATH | the spec clicks x before y", "PROBLEM: ROLE | STEP | test t clicks ...",
             "PROBLEM: ROLE | ENTRY | 3 test(s) ...", 'PROBLEM: ROLE | the requirement states a button named "B", and ...',
             'PROBLEM: the spec operates "Z", which only a LATER requirement names', "PROBLEM: MISSING | ..."]
    assert certain_spec_defects(lines) == [lines[0], lines[1], lines[3]], certain_spec_defects(lines)

def deterministic_review_sends_path_and_role_problems():
    node = _node([(T1, GIVEN_REPO, "The visitor opens the Issues page and clicks the “Open” link.")])
    spec = AuthoredFile("backend/test-e2e/REQ-7.spec.js", _spec(T1, '  await page.goto("/");\n  await click(page, "demo-repo");\n'
                                                                  '  await click(page, "Issues");\n'))
    kept = deterministic_review(node, spec, "REQ-7", log=lambda *_: None)
    assert any(k.startswith("PROBLEM: PATH | ") for k in kept), kept
    assert certain_spec_defects(kept), kept

def own_plan_names_are_never_later_controls():
    node = _node([(T1, GIVEN_REPO, "The visitor opens the Issues page and clicks the “Open” link.")])
    node["later_names"] = ["Issues", "New issue"]
    spec = _spec(T1, '  await click(page, "Issues");\n  await click(page, "New issue");\n')
    assert later_controls(spec, node) == ["New issue"], later_controls(spec, node)
    node["inherited_names"] = ["New issue"]
    assert later_controls(spec, node) == [], later_controls(spec, node)

def the_author_gets_the_plan():
    from agent.settings import SETTINGS
    node = _node([(T1, GIVEN_REPO, "The visitor opens the Issues page and clicks the “Open” link.")])
    before = SETTINGS.get("spec_plan_block")
    try:
        SETTINGS.pop("spec_plan_block", None)
        assert plan_block(node) == ""
        SETTINGS["spec_plan_block"] = True
        text = plan_block(node)
        assert text.startswith("STEP PLAN.") and 'open "Issues"' in text and 'repository "demo-repo"' in text, text
    finally:
        if before is None:
            SETTINGS.pop("spec_plan_block", None)
        else:
            SETTINGS["spec_plan_block"] = before

def given_stated_clicks_are_licensed():
    node = _node([(T1, "The visitor starts at the application home page in a fresh unauthenticated browser session and "
                       "opens the account-access page. The visitor clicks the visible link “Sign in” and then the visible "
                       "link “Create an account”.", "The visitor enters `ann-demo` in Username and clicks “Create account”.")])
    spec = _spec(T1, '  await page.goto("/");\n  await click(page, { role: "link", name: "Sign in" });\n'
                     '  await click(page, { role: "link", name: "Create an account" });\n  await fill(page, "Username", "ann-demo");\n'
                     '  await click(page, "Create account");\n')
    assert unstated_navigation(spec, node) == [] and plan_role_conflicts(spec, node) == []

def an_unresolved_step_licenses_its_hop():
    node = _node([(T1, GIVEN_REPO, "The visitor opens the issue toolbar and clicks the “Open” link.")])
    spec = _spec(T1, '  await page.goto("/");\n  await click(page, "Tools");\n  await click(page, { role: "link", name: "Open" });\n')
    assert unstated_navigation(spec, node) == []

def a_given_state_reached_unstated_is_not_judged():
    node = _node([(T1, "A signed-in user has entered an organization page from the account menu.",
                   "The user opens the “Account menu” and clicks “Sign out”.")])
    spec = _spec(T1, SIGN_IN + '  await click(page, "Account menu");\n  await click(page, "Your organizations");\n'
                     '  await click(page, "Account menu");\n  await click(page, "Sign out");\n')
    assert unstated_navigation(spec, node) == []

def possessive_capability_licenses_the_record():
    node = _node([(T1, "The system pre-provisions public repository `demo-repo`; the visitor can open its Files changed "
                       "page without signing in.", "The visitor clicks `src`.")])
    spec = _spec(T1, '  await page.goto("/");\n  await click(page, "demo-repo");\n  await click(page, "Files changed");\n'
                     '  await click(page, "src");\n')
    assert unstated_navigation(spec, node) == []

def a_click_after_an_input_is_no_hop():
    node = _node([(T1, GIVEN_REPO, "The visitor fills `Search` with `demo` and presses Enter, then opens the Issues page.")])
    spec = _spec(T1, '  await page.goto("/");\n  await fill(page, "Search", "demo");\n  await press(page, "Enter");\n'
                     '  await click(page, "Issues");\n')
    assert unstated_navigation(spec, node) == []

def unmatched_titles_and_submit_buttons_stay_quiet():
    node = _node([(T1, "The system pre-provisions account `dev-one` with email `dev-one@demo.test` and password `Pw-1!a`.",
                   "The visitor signs in as `dev-one` and opens the Issues page.")])
    other = _spec("REQ-7: Something else - Scenario 9", '  await click(page, "demo-repo");\n  await click(page, "Issues");\n')
    assert unstated_navigation(other, node) == [] and plan_role_conflicts(other, node) == []
    good = _spec(T1, SIGN_IN + '  await click(page, "Issues");\n')
    assert plan_role_conflicts(good, node) == [] and unstated_navigation(good, node) == []

CASES = [a_given_mentioned_record_is_no_licence, a_page_link_counts_not_only_menus, a_hop_to_an_implied_subject_is_reported,
         a_spec_that_never_tries_the_first_entry_is_reported, a_helper_with_bound_arguments_is_followed,
         role_conflicts_on_a_stated_entry, certain_defects_are_path_and_role_only, deterministic_review_sends_path_and_role_problems,
         own_plan_names_are_never_later_controls, the_author_gets_the_plan,
         given_stated_clicks_are_licensed, an_unresolved_step_licenses_its_hop, a_given_state_reached_unstated_is_not_judged,
         possessive_capability_licenses_the_record, a_click_after_an_input_is_no_hop, unmatched_titles_and_submit_buttons_stay_quiet]

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
    print(f"{len(CASES) - failed}/{len(CASES)} v160b nav cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
