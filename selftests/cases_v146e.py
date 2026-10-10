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

BASE = ("The search page has a searchbox named “Search”. Pressing Enter shows the matching items directly. Each result "
        "has a link named exactly with the item name. A query with no match displays “No results”. The opened item "
        "remains shown after a reload of the page. ")
FILTER_WHEN = ("The user enters the unique keyword in the top search box and presses Enter, selects the “Kinds” type "
               "filter on the results page, then clicks the public item's name.")
GIVEN = "The visitor starts at the home page. The seeded data is item `alpha-doc`, label `urgent`."

def _leaf(when, description=BASE, given=GIVEN, then=None, scenarios=None):
    steps = [{"keyword": "GIVEN", "content": given}, {"keyword": "WHEN", "content": when}]
    if then:
        steps.append({"keyword": "THEN", "content": then})
    return {"id": "REQ-1", "name": "Search", "description": description,
            "scenarios": scenarios if scenarios is not None else [{"name": "s1", "steps": steps}]}

def _node(leaf):
    return {"id": "REQ-1", "name": "x", "members": ["REQ-1"], "_leaves": [leaf]}

def _lines(leaf):
    return [t for _l, numbered in coverage.ledger(_node(leaf)) for _n, t in numbered]

def _controls(leaf):
    return [t for t in _lines(leaf) if t.startswith("[WHEN") and ", control]" in t]

@case
def a_filter_named_only_in_the_step_gets_one_obligation():
    leaf = _leaf(FILTER_WHEN)
    assert _controls(leaf) == ["[WHEN Scenario 1, control] the scenario step uses control “Kinds”: a test must operate it"], \
        _lines(leaf)
    plain = [t for t in _lines(_leaf(FILTER_WHEN, scenarios=[])) if not coverage.TAG.match(t)]
    lines = _lines(leaf)
    assert lines[: len(plain)] == plain and coverage._kind(lines[len(plain)]) == "WHEN", lines
    number = next(n for _l, numbered in coverage.ledger(_node(leaf)) for n, t in numbered if ", control]" in t)
    spec = f"// not testable: S{number} -- no filter\ntest('x', async () => {{}});\n"
    assert number in {n for n, _r in coverage.refused_marks(_node(leaf), spec)}, "a quoted control is testable"
    assert number in {n for _l, n, _t in coverage.uncovered(_node(leaf), spec)}
    covered = f"// covers S{number}\ntest('x', async () => {{}});\n"
    assert number not in {n for _l, n, _t in coverage.uncovered(_node(leaf), covered)}

@case
def a_name_a_statement_names_gets_none():
    named = BASE + "The results page offers a “Kinds” type filter. "
    assert not _controls(_leaf(FILTER_WHEN, description=named)), _lines(_leaf(FILTER_WHEN, description=named))
    unquoted = BASE + "The Kinds filter narrows the results page. "
    assert not _controls(_leaf(FILTER_WHEN, description=unquoted))
    lower = BASE + "The system accepts a query and displays matching kinds of items. "
    assert _controls(_leaf(FILTER_WHEN, description=lower)), "a lower-case word does not name the control"

@case
def names_that_are_no_operated_control_get_none():
    for when in ("The user enters “alpha report” in the search box and presses Enter.",
                 "The user types “Kinds” into the query field.",
                 "The user opens “Settings” and reads the list.",
                 "The user presses “Enter” in the search box.",
                 "The user checks “Kinds” shows three entries.",
                 "The user clicks “Create” or “Save changes” to store the rule.",
                 "The user clicks “Create”, or “Save changes”, to store the rule.",
                 "The user selects `urgent` from the label filter.",
                 "The user selects the “Kinds” filter of the seeded item list.",

                 "The user clicks the short hash of the commit, or selects both refs and clicks “Compare”.",
                 "In the “Airline” dropdown, select “North Air” and search.",
                 "The user selects “North Air” from the airline dropdown.",
                 "The user selects the “Reminders” label in the side panel."):
        assert not _controls(_leaf(when)), (when, _controls(_leaf(when)))
    seeded = BASE + "Seed data: labels “Work” and “Travel”. "
    assert not _controls(_leaf("The user unchecks “Work” in the panel.", description=seeded)), "seeded in the description"
    assert _controls(_leaf("The user unchecks “Work” in the panel.")), "the same step without the seed sentence"

    for when in ("The user selects the “Kinds” filter. Seed values: item `alpha-doc`.",
                 "Seed values: item `alpha-doc`; the user selects “Kinds” on the results page."):
        assert _controls(_leaf(when)), when
    given = GIVEN + " The user has opened the “Kinds” filter panel."
    assert not _controls(_leaf(FILTER_WHEN, given=given)), "a name the GIVEN step quotes"

@case
def several_controls_each_once_in_step_order():
    when = ("The user clicks “Advanced”, selects the “Kinds” filter, then clicks “Advanced” again and chooses "
            "“Newest first”.")
    got = _controls(_leaf(when))

    assert [t.split("control ")[1].split(":")[0] for t in got] == ["“Advanced”", "“Kinds”"], got
    assert [line for _leaf_id, line in coverage.dropped_controls(_node(_leaf(when)))] == [
        "[WHEN Scenario 1, control] the scenario step uses control “Newest first”: a test must operate it"]
    assert coverage.control_note(_node(_leaf(when))) == \
        "1 step-control obligation(s) left out for room (REQ-1 “Newest first”)"

    steps = [{"keyword": "GIVEN", "content": GIVEN}, {"keyword": "WHEN", "content": FILTER_WHEN}]
    twice = _leaf(FILTER_WHEN, scenarios=[{"name": "s1", "steps": steps}, {"name": "s2", "steps": steps}])
    assert _controls(twice) == [_controls(_leaf(FILTER_WHEN))[0]], _controls(twice)

@case
def quota_cap_order_and_generated_scenarios():
    then = ("The results page shows the item title and its owner, and the private item is absent from the list; the "
            "opened item page shows its heading after a reload.")
    lines = _lines(_leaf(FILTER_WHEN, then=then))
    kinds = [coverage._kind(t) for t in lines if coverage.TAG.match(t)]
    assert kinds.index("THEN") < kinds.index("WHEN"), lines
    assert coverage.control_note(_node(_leaf(FILTER_WHEN, then=then))) == ""

    full = "".join(f"The page shows the “Field {i}” label after a reload. " for i in range(coverage.CAP))
    assert len(_lines(_leaf(FILTER_WHEN, description=full))) == coverage.CAP
    assert not _controls(_leaf(FILTER_WHEN, description=full))
    assert "“Kinds”" in coverage.control_note(_node(_leaf(FILTER_WHEN, description=full)))

    many = " ".join(f"The user clicks “Button {i}”." for i in range(coverage.MAX_CLAUSES + 3))
    got = coverage.clause_obligations(_leaf(many), set(), BASE)
    assert len(got) == coverage.MAX_CONTROLS, got
    long_then = "; ".join(f"the page shows the saved value number {i} in the list" for i in range(coverage.MAX_CLAUSES + 2))
    got = coverage.clause_obligations(_leaf(FILTER_WHEN, then=long_then), set(), BASE)
    assert len(got) == coverage.MAX_CLAUSES and not [c for c in got if ", control]" in c], got

    assert not [c for c in coverage.clause_obligations(_leaf(FILTER_WHEN), {0}, BASE) if ", control]" in c]
    assert not _lines(_leaf(FILTER_WHEN, description="The search page has a searchbox named “Search”."))

@case
def platform_github_copy_gets_exactly_two():
    path = Path(r"local-data\hackathon-logs\gh103src\requirements.yaml")
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    import yaml
    from agent.boilerplate import leaves_of
    found = []
    for leaf in leaves_of(yaml.safe_load(text)):
        lid = str(leaf.get("id"))
        node = {"id": lid, "members": [lid], "requirement_text": text}
        found += [(lid, t) for _l, numbered in coverage.ledger(node) for _n, t in numbered if ", control]" in t]
    assert found == [("REQ-3-1", "[WHEN Scenario 1, control] the scenario step uses control “Repositories”: a test must "
                                 "operate it")], found
    node = {"id": "REQ-2-1-1", "members": ["REQ-2-1-1"], "requirement_text": text}
    assert "REQ-2-1-1 “Public”" in coverage.control_note(node), coverage.control_note(node)
    assert any(t.startswith("[THEN Scenario 1] the visitor cannot see the private repository name")
               for _l, numbered in coverage.ledger(node) for _n, t in numbered), "the THEN item keeps its room"

@case
def r52_hedged_steps_give_no_control_obligation():
    for when in ("The user edits the form and does not click “Save”.",
                 "The user edits the form and never clicks “Save”.",
                 "The user optionally clicks “Save”.",
                 "The user clicks “Save” only if the form is dirty.",
                 "The user edits the title; the form saves itself without the user clicking “Save”.",
                 "The user may click “Save” to keep the draft.",
                 "The user can select the “Kinds” filter.",
                 "The user tries to click “Delete” on a foreign item.",
                 "The user attempts to select “Kinds” while signed out.",
                 "If the dialog opens, the user clicks “Save”.",
                 "The user clicks “Save” when the title is filled.",
                 "The user doesn't click “Save”.",
                 "The user clicks “Save” unless a dialog is open."):
        assert not _controls(_leaf(when)), (when, _controls(_leaf(when)))
        assert not [c for c in coverage.clause_obligations(_leaf(when), set(), BASE) if ", control]" in c], when

    assert _controls(_leaf("The user clicks “Do not disturb” in the header.")), "a quoted name is not read for hedges"

    assert _controls(_leaf(FILTER_WHEN))

@case
def r52_then_items_keep_their_room():

    then = "The results page shows the item title in the list, and the item page shows its owner after a reload."
    when = " ".join(f"The user clicks “Control {i}”." for i in range(10))
    leaf = _leaf(when, then=then)
    lines = _lines(leaf)
    assert len([t for t in lines if coverage._kind(t) == "THEN"]) == 2, lines
    assert len(_controls(leaf)) == coverage.MAX_CONTROLS, lines
    assert len(coverage.dropped_controls(_node(leaf))) == 10 - coverage.MAX_CONTROLS
    assert coverage.control_note(_node(leaf)).startswith(f"{10 - coverage.MAX_CONTROLS} step-control obligation(s) left out")

    description = "".join(f"The page shows the “Field {i}” label after a reload. " for i in range(coverage.CAP - 3))
    tight = _leaf(when, description=description, then=then)
    lines = _lines(tight)
    assert len(lines) == coverage.CAP and len(_controls(tight)) == 1 and coverage._kind(lines[-2]) == "THEN", lines

    description = "".join(f"The page shows the “Field {i}” label after a reload. " for i in range(coverage.CAP - 2))
    full = _leaf(when, description=description, then=then)
    assert not _controls(full) and len([t for t in _lines(full) if coverage._kind(t) == "THEN"]) == 2, _lines(full)
