from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from agent.when_plan import Context, scenario_plan, plans_for, licensed_names, nav_targets, render_plan

REQ = """
- id: REQ-1
  description: >-
    Signed-in scenarios begin with the unique “Sign in” link on the home page, fields labeled “Username or email”
    and “Password”, and the “Sign in” button. The registration form has a “Username” field. The “Account menu”
    holds “Settings”. Repository pages offer `Issues` and `Pull requests` links and a `Files changed` tab.
    The selector is a unique button named “Branch <current branch name>”.
  scenarios: []
"""

def _scn(given: str, when: str, then: str = "The page shows the result.", name: str = "REQ-1: Demo - Scenario 1") -> dict:
    return {"name": name, "steps": [{"keyword": "GIVEN", "content": given}, {"keyword": "WHEN", "content": when},
                                    {"keyword": "THEN", "content": then}]}

CTX = Context(REQ)

def _plan(given: str, when: str, then: str = "The page shows the result.", leaf: dict | None = None) -> dict:
    return scenario_plan(_scn(given, when, then), leaf or {"id": "REQ-1", "description": REQ}, CTX)

def _steps(plan: dict) -> list[tuple]:
    return [(s["part"], s["action"], s.get("target"), s["status"]) for s in plan["steps"] if s["action"] != "observe"]

def unquoted_page_entry_is_a_step():
    p = _plan("The system pre-provisions public repository `demo-repo` with an Open issue titled `Fix login`; the "
              "visitor can open its Issues page without signing in.",
              "The visitor opens the Issues page, clicks the “Open” link, and enters `Fix login` in the `Search issues` searchbox.")
    st = _steps(p)
    assert st[0] == ("WHEN", "open", "Issues", "resolved"), st
    assert ("WHEN", "click", "Open", "resolved") in st, st

    assert not any(s[2] == "demo-repo" for s in st), st
    assert p["unresolved"] == 0, p

def referent_binds_the_scenario_record():
    p = _plan("Public repository `demo-repo` has an Open PR titled `Tidy docs PR` with changed files.",
              "The visitor opens the PR and clicks `Files changed`.")
    st = _steps(p)
    assert st[0][1:] == ("open", "Tidy docs PR", "resolved") and p["steps"][0]["entity"]["kind"] == "PR", st
    assert st[1][1:3] == ("click", "Files changed"), st

def two_records_of_one_kind_leave_the_referent_unresolved():
    p = _plan("Public repository `demo-repo` has Open PRs titled `First PR` and `Second PR`.", "The visitor opens the PR.")
    assert p["unresolved"] == 1 and p["steps"][0]["status"] == "unresolved" and "2 PR candidates" in p["steps"][0]["why"], p["steps"]

def given_stated_clicks_are_preparation_steps():
    p = _plan("The visitor starts at the application home page and opens the account-access page. The visitor clicks "
              "the visible link “Sign in” and then the visible link “Create an account”.",
              "The visitor enters `ann-demo` in Username and clicks “Create account”.")
    st = _steps(p)
    assert ("GIVEN", "click", "Sign in", "resolved") in st and ("GIVEN", "click", "Create an account", "resolved") in st, st
    assert [s for s in p["steps"] if s.get("target") == "Sign in"][0]["role"] == "link"

def menu_scope_orders_the_menu_first():
    p = _plan("The visitor is unauthenticated and the system pre-provisions workbook `WB-ONE` with active worksheet `Data1`.",
              "The visitor opens `WB-ONE`, opens “Filter views” in the “View” menu, and clicks `Saved lanes`.")
    st = _steps(p)
    i, j = st.index(("WHEN", "menu", "View", "resolved")), [k for k, s in enumerate(st) if s[2] == "Filter views"][0]
    assert i < j and p["steps"][j]["scope"] == {"kind": "menu", "name": "View"}, st

def reload_home_and_reopen_keep_their_order():
    p = _plan("Public repository `demo-repo` has an Open PR titled `Tidy docs PR`.",
              "The visitor opens the PR, returns to the application home page, reopens the same PR, and clicks `Files changed`.")
    st = [s[1:3] for s in _steps(p)]
    assert st[:4] == [("open", "Tidy docs PR"), ("home", "/"), ("open", "Tidy docs PR"), ("click", "Files changed")], st

def implied_sign_in_and_subject():
    p = _plan("The system pre-provisions the separate Open PR `Closable demo PR` and the author account `pr-writer` with "
              "email `pr-writer@demo.test`, password `Demo-pass-1!`. The visitor starts at the application home page.",
              "The author clicks `Close pull request`.")
    st = _steps(p)
    assert st[0][1] == "sign_in" and st[0][3] == "implied" and p["steps"][0]["account"]["account"] == "pr-writer", st
    assert st[1] == ("WHEN", "open", "Closable demo PR", "implied"), st
    assert st[2][1:3] == ("click", "Close pull request"), st

def named_signer_and_role_signer():
    p = _plan("The system pre-provisions Admin account `rep-admin` with email `rep-admin@demo.test`, password `Pw-1!a` "
              "and Member account `rep-member` with email `rep-member@demo.test`, password `Pw-2!b`.",
              "The Admin signs in and opens `demo-repo`.")
    assert p["steps"][0]["account"]["account"] == "rep-admin", p["steps"][0]
    p2 = _plan("The system pre-provisions Admin account `rep-admin` with email `rep-admin@demo.test`, password `Pw-1!a`.",
               "The visitor signs in as `rep-admin` and opens `demo-repo`.")
    assert p2["steps"][0]["account"]["account"] == "rep-admin"

def two_browser_sessions():
    p = _plan("The system pre-provisions `sess-owner` with email `sess-owner@demo.test` and password `Pw-3!c`. Two browser "
              "sessions for that account are active.",
              "The visitor opens “Account menu” in one browser and clicks “Settings” for the other browser session.")
    assert p["sessions"] == 2 and [s["session"] for s in p["steps"] if s["action"] == "sign_in"] == [1, 2], p["steps"]
    assert all(s["session"] == 1 for s in p["steps"] if s.get("target") in ("Account menu", "Settings")), p["steps"]

def description_defined_control_is_resolved():
    p = _plan("The visitor can open the repository `demo-repo` without signing in. The repository has active branch "
              "`trunk` and branch `topic`.", "The visitor opens the branch selector and clicks the exact `topic` option.")
    names = [s.get("target") for s in p["steps"]]
    assert "Branch trunk" in names and [s for s in p["steps"] if s.get("target") == "Branch trunk"][0]["role"] == "button", p["steps"]

def possessive_capability_opens_the_record_then_its_page():
    p = _plan("The system pre-provisions public repository `demo-repo`; the visitor can open its Files changed page "
              "without signing in.", "The visitor clicks `src`.")
    st = [s[1:3] for s in _steps(p)]
    assert st[:3] == [("open", "demo-repo"), ("open", "Files changed"), ("click", "src")], st

def unresolved_steps_are_counted_never_dropped():
    p = _plan("The visitor is unauthenticated.", "The visitor opens the reset step for `x@demo.test` and enters `000000` in “Code”.")
    assert p["unresolved"] == 1 and p["steps"][0]["status"] == "unresolved", p["steps"]
    p2 = _plan("A signed-in user has entered an organization page from the account menu.", "The user opens “Account menu”.")
    assert any(s["action"] == "state" and s["status"] == "unresolved" for s in p2["steps"]), p2["steps"]

def a_given_mention_is_no_step():
    p = _plan("The system pre-provisions public repository `demo-repo` with an Open issue titled `Fix login`.",
              "The visitor enters `Fix login` in the `Search` searchbox and presses Enter.")
    assert not nav_targets(p) and "demo-repo" not in licensed_names(p), p["steps"]
    assert {e["name"] for e in p["entities"]} == {"demo-repo", "Fix login"}

def system_sentences_and_negations_are_no_steps():
    p = _plan("The visitor is unauthenticated and the account can create organizations and has permission to close and "
              "reopen issues.", "The visitor cannot open “Settings”.",
              then="The system opens the sign-in page and the page does not expose “Delete”.")
    assert not [s for s in p["steps"] if s["action"] in ("open", "click", "input")], p["steps"]

def observation_clause_holds_no_action():
    p = _plan("The visitor is unauthenticated.", "The viewer looks for close or reopen operations.")
    assert not [s for s in p["steps"] if s["action"] != "observe"], p["steps"]

def quoted_values_are_no_click_targets():
    p = _plan("The visitor is unauthenticated.", "The visitor enters `Open settings` in “Title” and presses Enter.")
    assert not nav_targets(p), p["steps"]
    assert [s["action"] for s in p["steps"]] == ["input", "press"], p["steps"]

def conditional_step_is_marked():
    p = _plan("The visitor is unauthenticated.", "The visitor opens `demo-repo`, and, if a “Settings” link is visible, opens it.")
    st = _steps(p)
    assert st[0][3] == "resolved" and st[1][2] == "Settings" and st[1][3] == "conditional", st

def whole_requirement_never_raises():
    assert plans_for("::: not yaml [") == []
    assert plans_for(REQ) == []
    text = render_plan(_plan("The visitor is unauthenticated.", "The visitor opens the Issues page."))
    assert 'open "Issues"' in text, text

CASES = [unquoted_page_entry_is_a_step, referent_binds_the_scenario_record, two_records_of_one_kind_leave_the_referent_unresolved,
         given_stated_clicks_are_preparation_steps, menu_scope_orders_the_menu_first, reload_home_and_reopen_keep_their_order,
         implied_sign_in_and_subject, named_signer_and_role_signer, two_browser_sessions, description_defined_control_is_resolved,
         possessive_capability_opens_the_record_then_its_page, unresolved_steps_are_counted_never_dropped,
         a_given_mention_is_no_step, system_sentences_and_negations_are_no_steps, observation_clause_holds_no_action,
         quoted_values_are_no_click_targets, conditional_step_is_marked, whole_requirement_never_raises]

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
    print(f"{len(CASES) - failed}/{len(CASES)} v160b plan cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
