from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = []

def case(function):
    CASES.append(function)
    return function

REQ_6 = ("Each named action, field, and navigation link is unique on its active page unless explicitly repeated per "
         "changed line or reviewer. PR titles are headings whose accessible name is exactly the persisted title; list "
         "titles are links with that same exact name. Conversation, Commits, Files changed, and Checks are navigation "
         "links, even when visually styled as tabs. The current PR status is visible text. Signed-in scenarios begin "
         "with the unique Sign in link on the home page, labeled Username or email and Password fields, a Sign in "
         "button, and the account username visible after authentication; each role account has a username, verified "
         "email, and password.")

REQ_6_2_2 = ("The system validates the role and rejects unauthorized requests. New pull request is a link. The "
             "comparison page has native select controls with combobox roles named base and compare; each option's "
             "visible label is its exact branch name. Compare changes is a button. For a valid selection it displays "
             "the known changed file path verbatim, a Commit summary with the comparable commit count, and an enabled "
             "Create pull request button. Selecting the same branch in both fields immediately displays No changes and "
             "disables the creation button, without requiring Compare changes to be clicked first. Clicking Compare "
             "changes retains that result.")

REQ_6_3 = ("The PR detail page is the unified read view for the same PR number. A seeded public Open PR is directly "
           "viewable without sign-in and displays its exact title in a heading. Commits and Files changed are links; the "
           "Commits view displays a Commit summary, and the Files changed view displays a Changed files summary.")

REQ_6_5 = ("On an eligible PR, Merge pull request and then Confirm merge are buttons; confirming displays Merged, and "
           "that state remains after reload. A blocked PR keeps a visible disabled Merge pull request button and explains "
           "its unmet review or protection condition before any click. Seed data supplies a Maintain account and "
           "separate Open PRs for success and refusal.")

SEEDED_CONFIRM = ("The administrator selects a different seeded branch, activates the “Update” button, and then the "
                  "“Confirm” button in the confirmation dialog.")

def _statements(text):
    from agent.stated_controls import unquoted_statements, uq_normalise
    return [(role, name) for role, name, _s, _e, _n in unquoted_statements(uq_normalise(text))]

def _requirement_yaml(items):
    import json
    lines = ["requirements:"]
    for rid, text in items:
        lines.append(f"  - id: {rid}")
        lines.append(f"    description: {json.dumps(text, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"

def _project(tmp, files):
    root = Path(tmp)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root

V124_PAGE = """import { apiClient } from '../api';
export default function PullRequestPage() {
  const merge = async () => { await apiClient.post('/merge', {}); };
  return (
    <div>
      <button type="button" onClick={merge}>
        Merge pull request
      </button>
    </div>
  );
}
"""
BRANCHES = ("export const Update = () => <button type=\"button\">Update</button>;\n"
            "export const Confirm = () => <button type=\"button\">Confirm</button>;\n")

@case
def the_real_unquoted_sentences_give_exactly_their_control_names():
    assert _statements(REQ_6_5) == [("button", "Merge pull request"), ("button", "Confirm merge")], _statements(REQ_6_5)
    assert _statements(REQ_6) == [("link", "Conversation"), ("link", "Commits"), ("link", "Files changed"),
                                  ("link", "Checks"), ("link", "Sign in")], _statements(REQ_6)
    assert _statements(REQ_6_2_2) == [("link", "New pull request"), ("button", "Compare changes")], _statements(REQ_6_2_2)
    assert _statements(REQ_6_3) == [("link", "Commits"), ("link", "Files changed")], _statements(REQ_6_3)

@case
def other_shapes_in_the_same_sentences_are_not_read():

    names = {name for _role, name in _statements(REQ_6 + " " + REQ_6_2_2 + " " + REQ_6_5)}
    for other in ("Create pull request", "Username or email", "Password", "Titles", "PR", "Merged", "Review required"):
        assert other not in names, other
    assert ("button", "Sign in") not in _statements(REQ_6)
    assert ("button", "Merge pull request") in _statements(REQ_6_5)

@case
def a_subject_that_is_not_wholly_a_name_is_dropped_never_trimmed():
    for text in ("Titles are links.",
                 "Each result title is a link whose exact accessible name is the title.",
                 "The Commits and Files changed are links.",
                 "After review Merge pull request and Confirm merge are buttons.",
                 "Owner and Admin are links.",
                 "Create new pull request now is a button.",
                 "Read, Write, and Triage are tabs.",
                 "PR and Issue are links."):
        assert _statements(text) == [], (text, _statements(text))

@case
def a_negated_clause_or_another_verb_is_not_a_statement():
    for text in ("Merge pull request and Confirm merge are not buttons.",
                 "Compare changes is a button, not a link.",
                 "Compare changes is no longer a button.",
                 "Compare changes was a button.",
                 "Commits and Files changed are shown as links."):
        assert _statements(text) == [], (text, _statements(text))

@case
def a_heading_line_never_joins_the_first_name_and_a_name_never_spans_lines():
    assert _statements("View Tabs\nCommits and Files changed are links.") == [("link", "Commits"), ("link", "Files changed")]
    assert _statements("Requirement REQ-6-5: Merge an Eligible Pull Request\n" + REQ_6_5)[:2] == [
        ("button", "Merge pull request"), ("button", "Confirm merge")]
    assert _statements("Merge pull\nrequest is a button.") == []

@case
def quoted_names_stay_with_the_quoted_patterns():
    assert _statements("Click the “Confirm merge” button. The “Sign in” link is unique.") == []
    from agent.stated_controls import extract_stated_controls
    stated, _seeds = extract_stated_controls("Use the “Sign in” link. " + REQ_6)
    sign_in = [s for s in stated if s["name"] == "Sign in" and s["roles"] == {"link"}]
    assert len(sign_in) == 1 and not sign_in[0].get("unquoted"), sign_in

@case
def extract_stated_controls_marks_unquoted_names_and_can_leave_them_out():
    from agent.stated_controls import extract_stated_controls
    stated, _seeds = extract_stated_controls(REQ_6_5)
    got = {(frozenset(s["roles"]), s["name"]): s for s in stated}
    assert got[(frozenset({"button"}), "Confirm merge")].get("unquoted") is True
    assert got[(frozenset({"button"}), "Confirm merge")]["context"].startswith("On an eligible PR, Merge pull request")
    assert extract_stated_controls(REQ_6_5, unquoted=False)[0] == []

@case
def v124_confirm_merge_missing_is_reported_and_says_it_was_unquoted():
    from agent.stated_controls import stated_control_problems
    requirement = _requirement_yaml([("REQ-4-4", SEEDED_CONFIRM), ("REQ-6-5", REQ_6_5)])
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp, {"frontend/src/pages/PullRequestPage.tsx": V124_PAGE, "frontend/src/pages/Branches.tsx": BRANCHES})
        problems = stated_control_problems(requirement, project)

    assert [(p["kind"], p["name"]) for p in problems] == [("stated control missing", "Confirm merge")], problems
    assert problems[0]["unquoted"] is True and "(stated without quotes) On an eligible PR" in problems[0]["evidence"]

    fixed = V124_PAGE.replace("</div>", '  <button type="button">Confirm merge</button>\n    </div>')
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp, {"frontend/src/pages/PullRequestPage.tsx": fixed, "frontend/src/pages/Branches.tsx": BRANCHES})
        assert stated_control_problems(requirement, project) == []

@case
def an_unquoted_link_rendered_as_a_button_is_a_role_difference():
    from agent.stated_controls import stated_control_problems
    requirement = _requirement_yaml([("REQ-6-2-2", REQ_6_2_2)])
    page = ("export default function PullsPage() {\n  return (\n    <div>\n"
            "      <button type=\"button\" onClick={() => go('/compare')}>\n        New pull request\n      </button>\n"
            "      <button type=\"button\">Compare changes</button>\n    </div>\n  );\n}\n")
    with tempfile.TemporaryDirectory() as tmp:
        problems = stated_control_problems(requirement, _project(tmp, {"frontend/src/pages/PullsPage.tsx": page}))
    assert [(p["kind"], p["role"], p["name"]) for p in problems] == [("stated role differs", "link", "New pull request")], problems
    assert problems[0]["unquoted"] is True and "(stated without quotes)" in problems[0]["evidence"]

@case
def missing_is_still_limited_to_the_batchs_own_requirement():
    from agent.detaudit import stated_control_findings
    whole = _requirement_yaml([("REQ-4-4", SEEDED_CONFIRM), ("REQ-6-5", REQ_6_5)])
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp, {"frontend/src/pages/PullRequestPage.tsx": V124_PAGE, "frontend/src/pages/Branches.tsx": BRANCHES})
        own = {"id": "REQ-6-5", "name": "Merge an Eligible Pull Request", "description": REQ_6_5, "requirement_text": whole}
        findings, counts = stated_control_findings(own, project)
        assert counts["stated_missing"] == 1 and "Confirm merge" in findings[0]["problem"], findings
        assert "(stated without quotes)" in findings[0]["requirement"], findings[0]
        other = {"id": "REQ-4-4", "name": "Default branch", "description": SEEDED_CONFIRM, "requirement_text": whole}
        assert stated_control_findings(other, project)[1]["stated_missing"] == 0

@case
def the_unique_sign_in_link_without_quotes_feeds_stated_unique():
    from agent.detaudit import deterministic_findings, stated_unique
    assert ("link", "Sign in") in stated_unique(REQ_6)
    assert ("button", "Sign in") not in stated_unique(REQ_6)
    assert stated_unique("Each row has the unique Edit link.") == []
    assert stated_unique("Use the unique Sign\nin link.") == []

    aria = ('- banner:\n  - link "Sign in":\n    - /url: /login\n- main:\n  - paragraph:\n    - link "Sign in":\n'
            '      - /url: /login\n')
    with tempfile.TemporaryDirectory() as tmp:
        node = {"id": "REQ-6", "name": "Pull Requests", "description": REQ_6}
        problems = " ".join(f["problem"] for f in deterministic_findings(Path(tmp), node, [{"route": "/", "aria": aria}],
                                                                         log=lambda m: None))
    assert "2 link elements named `Sign in`" in problems, problems

@case
def census_of_the_sample_requirements_is_exactly_the_eleven_github_names():

    samples = ROOT.parent / "codex-audit" / "requirements-samples"
    if not (samples / "hackathon-github-platform.yaml").is_file():
        print("      (skipped: no requirement samples next to the agent)")
        return
    from agent.stated_controls import _uq_chunks, extract_stated_controls, unquoted_statements
    expected = {("link", "Conversation"), ("link", "Commits"), ("link", "Files changed"), ("link", "Checks"),
                ("link", "Sign in"), ("link", "New pull request"), ("button", "Compare changes"),
                ("button", "Merge pull request"), ("button", "Confirm merge")}
    for name, wanted in (("hackathon-github-platform.yaml", expected), ("hackathon-sheet-platform.yaml", set()),
                         ("keep.yaml", set()), ("bookstack.yaml", set()), ("ticket-booking.yaml", set())):
        path = samples / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        got = {(role, n) for chunk in _uq_chunks(text) for role, n, _s, _e, _k in unquoted_statements(chunk)}
        assert got == wanted, (name, got ^ wanted)

        if wanted:
            new = {(next(iter(s["roles"])), s["name"]) for s in extract_stated_controls(text)[0] if s.get("unquoted")}
            assert new == expected - {("link", "Commits"), ("link", "Sign in")}, new

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
    print(f"{len(CASES) - failed}/{len(CASES)} unquoted-name cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
