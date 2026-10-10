from __future__ import annotations

import copy
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "selftests"))

import cases_v144g as g

CASES = []
QUIET = lambda message: None
COMMANDS = ('The row-number menu provides "Insert 1 row above" and "Delete row". The column-header menu provides '
            '"Insert 1 column left" and "Delete column". ')

def case(function):
    CASES.append(function)
    return function

def said(text):
    from agent.commit_probe import key_statements
    return key_statements(text)

def findings(statement, result, logs=None):
    from agent.commit_probe import keys_findings
    return keys_findings(statement, result, log=(logs.append if logs is not None else QUIET))

def structure(text):
    from agent.structure_probe import structure_statement
    return structure_statement(text)

def with_tab(base, **tab):
    result = copy.deepcopy(base)
    result["keys"]["tab"] = {**result["keys"]["tab"], **tab}
    return result

@case
def a_noun_or_a_setting_condition_is_not_the_structural_edit():

    assert structure(COMMANDS + "Formula references shift when automatic row adjustment is enabled.") is None
    for text in ("Formula references shift when automatic row insertion is enabled.",
                 "When column adjustment mode is on, formula references shift with the columns.",
                 "Formula references shift when the row option is checked.",
                 "Formula references shift when a row is frozen."):
        assert structure(COMMANDS + text) is None, (text, structure(COMMANDS + text))

    for text, axis in (("When a row is inserted, formula references shift downward together.", "row"),
                       ("Formula references shift when rows are added above them.", "row"),
                       ("When inserting or deleting columns, formula references adjust to the new positions.", "column"),
                       ("If a row is inserted above a referenced cell, the formula references shift down.", "row")):
        got = structure(COMMANDS + text)
        assert got and got["axis"] == axis and got["shift_sentence"] == text, (text, got)

    got = structure(COMMANDS + "On insertion, formula references shift together. Formula references shift when "
                               "automatic row adjustment is enabled.")
    assert got and got["axis"] == "column" and got["other_axis"] is None, got

@case
def a_negation_in_another_coordinated_part_keeps_the_statement():

    got = said("Pressing Enter commits the cell value, and the selection does not move.")
    assert got and got["said"] == {"enter_commits": "Pressing Enter commits the cell value, and the selection does not move."}, got
    got = said("Pressing Enter saves the value of the cell, but the grid does not scroll.")
    assert got and "enter_commits" in got["said"], got

    stated = said("Pressing Enter commits the cell value, and the selection does not move.")
    broken = copy.deepcopy(g.LOCAL)
    broken["keys"]["enter"] = {**broken["keys"]["enter"], "stored": False}
    assert [f["problem"][:22] for f in findings(stated, broken)] == ["Enter did not commit: "], findings(stated, broken)

    for text in ("Pressing Enter does not commit the cell value, and the selection moves down.",
                 "Arrow keys move the caret, not the cell selection.",
                 "While editing, arrow keys never move the selection to another cell.",
                 "The selection does not move, and pressing Enter never commits the cell value.",
                 "Typing on a selected cell replaces its content only after a double-click, never directly."):
        assert said(text) is None, (text, said(text))

    got = said("When no edit is in progress, arrow keys move the cell selection, and the editor does not open.")
    assert got and got["said"].get("arrow_moves") and got.get("modes") == {"arrow_moves": "idle"}, got

@case
def a_stated_tab_semantic_or_mode_keeps_the_tab_contrast_off():

    assert g.kinds(findings(said(g.SHEET), g.PLATFORM)) == ["arrow", "tab-merged"]
    assert "tab_stated" not in said(g.SHEET)

    for extra in (" While editing a cell, pressing Tab inserts a tab character into the value.",
                  " When no edit is in progress, Tab moves the selection to the next cell on the right.",
                  " Pressing Tab does not commit the cell."):
        stated = said(g.SHEET + extra)
        found = g.kinds(findings(stated, g.PLATFORM))
        assert found == ["arrow"], (extra, found, stated)
        lost = with_tab(g.PLATFORM, merged=False, stored=False)
        assert g.kinds(findings(stated, lost)) == ["arrow"], (extra, g.kinds(findings(stated, lost)))

    for extra in (" In the cell format dialog, pressing Tab moves focus to the next field.",
                  " On the sign-in page, pressing Tab moves focus to the next form field."):
        stated = said(g.SHEET + extra)
        assert "tab_stated" not in stated, stated
        assert g.kinds(findings(stated, g.PLATFORM)) == ["arrow", "tab-merged"]

    stated = said(g.SHEET + " Pressing Tab moves to the next cell on the right.")
    tab = [f for f in findings(stated, g.PLATFORM) if f["problem"].startswith("Tab merged")]
    assert len(tab) == 1 and tab[0]["requirement"].startswith("(the requirement: Tab)"), tab
    lost = with_tab(g.PLATFORM, merged=False, stored=False)
    assert "tab-lost" in g.kinds(findings(stated, lost)), g.kinds(findings(stated, lost))

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
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed}/{len(CASES)} r50w cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
