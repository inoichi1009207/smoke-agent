from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = []

def case(function):
    CASES.append(function)
    return function

def flat(text: str) -> str:
    return " ".join(text.split())

@case
def the_conventions_name_one_owner_per_rule():
    from agent.conventions import PROJECT_CONVENTIONS

    text = flat(PROJECT_CONVENTIONS)
    assert "One owner per rule." in text
    assert "by owner AND name through ONE helper, never a fallback to the name alone" in text
    assert "each router is mounted once, and one method and path has exactly one handler" in text
    assert "the middleware rejects an inactive session on EVERY request" in text
    assert "it imports the same shared module -- never a copy with rules of its own" in text

    assert "keep every source file under about 12000 characters" in text
    assert "Every repair rewrites a file WHOLE" not in text and "never re-sent whole" in text and "more than about 800 lines is split" in text

@case
def the_conventions_recalculate_a_cleared_cell_and_keep_the_counting_rule():
    from agent.conventions import PROJECT_CONVENTIONS

    text = flat(PROJECT_CONVENTIONS)
    assert "an empty input is never silently turned into zero or into text a function then counts" in text
    assert "a cleared cell is empty, never a broken reference" in text
    assert "A grid works over its whole extent" in text and "Delete clears every cell of the selected range" in text

@case
def the_architect_plans_one_owner_and_the_review_flags_two():
    from agent import contract

    assert "One owner per rule (v160c)" in contract.SYSTEM_PROMPT and "Never plan two modules for one rule." in contract.SYSTEM_PROMPT
    assert "a rule with two owners" in contract.REVIEW_PROMPT

@case
def the_prerequisites_follow_the_requirement_wording():
    from agent.inherited_health import health_check_items, health_rules, prerequisites_for

    sheet = ("Pressing Delete clears every cell in the current selected rectangle. Clearing a formula removes its original "
             "formula and recalculates dependent cells.")
    keys = prerequisites_for(sheet)
    assert keys[:2] == ["recalculation", "extent"] and "session" not in keys and "modified" not in keys, keys
    assert {"entry", "owners"} <= set(keys)
    github = ("Original Feature Description ... Modified Feature Description ... The page lists the current browser session "
              "and other active sessions. Revoking a session immediately marks it inactive.")
    keys = prerequisites_for(github)
    assert "session" in keys and "modified" in keys and "recalculation" not in keys, keys
    text = health_rules(github)
    assert "Session chain" in text and "two separate browser contexts" in text and "Recalculation" not in text
    assert [k for k, _item in health_check_items(github)] == ["owners", "entry", "session", "modified"]
    assert "Recalculation" in health_rules("") and len(health_check_items("")) == 6

@case
def the_prerequisite_texts_hold_no_test_literals():

    import re

    from agent import inherited_health

    texts = inherited_health.HEALTH_RULES + " ".join(item for _k, item in inherited_health.HEALTH_CHECK_ITEMS)
    assert not re.search(r"\b[A-Z]{1,2}[0-9]{1,3}\b", texts), re.findall(r"\b[A-Z]{1,2}[0-9]{1,3}\b", texts)
    assert "EVO" not in texts and "@" not in texts and "acme" not in texts.lower()

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
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} v160c rule cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
