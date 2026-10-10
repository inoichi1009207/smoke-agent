from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STORE = ROOT / "template" / "frontend" / "src" / "api" / "store.ts"
CASES = []

TASK_LITERALS = ("Team name", "team name", "Find branch", "No matching branch", "Branch main", "Improve onboarding",
                 "feature-search", "Q3 Sales", "Comment is required", "pivot", "Pivot", "Filter Region", "alice-dev",
                 "Merge pull request", "Confirm merge", "issue", "label")

def case(function):
    CASES.append(function)
    return function

def _sentence(text: str, start: str, length: int = 700) -> str:
    flat = re.sub(r"\s+", " ", text)
    assert start in flat, start
    return flat[flat.index(start):][:length]

def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)

def _function_body(source: str, head: str) -> str:
    return source.split(head, 1)[1].split("\n}\n", 1)[0]

@case
def a_test_value_that_breaks_a_stated_format_is_a_test_defect():
    from agent import codegen
    prompt = _flat(codegen.SYSTEM_PROMPT)
    assert "A value the test itself types that breaks a format the requirement states for that field" in prompt
    assert "is kind (b) ONLY when the test then treats it as ACCEPTED" in prompt

    assert "A test that types an invalid value and expects it to be refused" in prompt and "is correct" in prompt

    assert "That is never a test defect -- unless the missing text is a value the test itself typed" in prompt
    diagnose = _flat(codegen.USER_TEXT["ask_diagnose"])
    assert "never loosen the application's check to accept it" in diagnose and "`SPEC:`" in diagnose
    assert "a spec that expects such a value to be REFUSED is correct" in diagnose

@case
def a_repair_never_loosens_a_validation_or_a_stated_message():
    from agent import codegen
    prompt = _flat(codegen.SYSTEM_PROMPT)
    rule = _sentence(prompt, "- A repair never weakens what the requirement states")
    assert "never loosen a validation, a format or the condition under which a stated message appears" in rule

    assert "never turn a refusal into a navigation to another existing record" in rule
    assert "A refused create or submit stays on the same page and shows the server's reason for refusing" in rule

@case
def a_refused_create_shows_the_reason_and_stays():
    from agent.conventions import PROJECT_CONVENTIONS
    rule = _sentence(PROJECT_CONVENTIONS, "A create or submit the server rejects")
    assert "shows the server's reason and stays on its page with the input kept" in rule
    assert "never navigates to another, already existing record" in rule

    assert _flat(PROJECT_CONVENTIONS).count("an action that is refused shows why") == 1

@case
def a_structural_edit_shifts_every_stored_range():
    from agent.conventions import PROJECT_CONVENTIONS
    rule = _sentence(PROJECT_CONVENTIONS, "A structural edit (insert or delete rows or columns, move)")
    assert "shifts EVERY stored reference to cells" in rule and "the ranges held by derived objects" in rule
    assert "uses the adjusted range" in rule
    flat = _flat(PROJECT_CONVENTIONS)
    assert flat.index("A grid (a spreadsheet") < flat.index("A structural edit") < flat.index("- A page appears complete")

@case
def state_that_survives_a_switch_is_saved_when_created():
    from agent.conventions import PROJECT_CONVENTIONS
    rule = _sentence(PROJECT_CONVENTIONS, "- State the requirement says survives a reload or a switch")
    assert "is saved to the server the moment it is created or changed, not only when it is later applied" in rule
    assert "every view reads it back from there" in rule

@case
def a_put_into_a_path_never_loaded_does_not_make_it_fresh():
    store = STORE.read_text(encoding="utf-8")
    put = _function_body(store, "export function put<T>(path: string, value: T): void {")
    assert "if (loadedAt.has(path)) loadedAt.set(path, Date.now());" in put
    assert "store(path, value)" not in put and "cache.set(path, value);" in put
    assert "written.set(path, ++writes);" in put and "emit();" in put

    load_now = _function_body(store, "export function loadNow(paths: string[]): void {")
    assert load_now.index("loadedAt.set(path, Date.now());") < load_now.index("put(path, JSON.parse(request.responseText));")
    signed_out = store.split("if (status === 401 && path === SESSION_PATH) {", 1)[1].split("}", 1)[0]
    assert signed_out.index("loadedAt.set(path, Date.now());") < signed_out.index("put(path, null);")

    assert "const age = Date.now() - (loadedAt.get(path) ?? 0);" in store and "if (age > FRESH_MS)" in store

    for kept in ("const FRESH_MS = 2000;", "if (written.get(path) !== before) return peek<T>(path);", "running.clear();",
                 "const mine = generation;", "request.open('GET', `/api${path}`, false)"):
        assert kept in store, kept

@case
def a_create_refreshes_the_details_path_it_opens():
    from agent.conventions import PROJECT_CONVENTIONS
    flat = _flat(PROJECT_CONVENTIONS)
    assert "awaits `refresh([...affected paths])` -- the lists AND the details path it navigates to -- BEFORE it navigates" in flat
    assert "`put()` holds exactly what a GET of that path returns, never a create's partial answer" in flat

@case
def the_browser_check_pins_both_sides_of_the_put_rule():
    spec = (ROOT / "selftests" / "browser" / "checks.spec.js").read_text(encoding="utf-8")
    app = (ROOT / "selftests" / "browser" / "App.tsx").read_text(encoding="utf-8")
    assert "test('a put into a path never loaded does not make it fresh; into a loaded path it still does'" in spec
    assert "'Put partial then open'" in spec and "'Put edit then reopen'" in spec
    assert "put('/items?view=details'" in app and '<Route path="/details"' in app
    assert ">Put partial then open<" in app and ">Put edit then reopen<" in app

@case
def what_a_control_opens_is_rendered_only_while_open():
    from agent.conventions import PROJECT_CONVENTIONS
    rule = _sentence(PROJECT_CONVENTIONS, "The same holds for anything the requirement says a control OPENS")
    assert "it is rendered only while open" in rule and "absent before the opening action, gone after the closing one" in rule
    assert "together with what it shows" in rule
    flat = _flat(PROJECT_CONVENTIONS)
    assert flat.index("- A menu, dropdown or popover that the requirement says APPEARS") < flat.index("The same holds for anything the requirement says a control OPENS")

@case
def entries_called_options_are_options_in_a_listbox():
    from agent.conventions import PROJECT_CONVENTIONS
    rule = _sentence(PROJECT_CONVENTIONS, "Entries the requirement calls options of a picker")
    assert 'role="option" inside role="listbox"' in rule and "also when they are built from data" in rule
    assert "aria-selected" in rule and "never one button or checkbox per entry" in rule

@case
def the_new_rule_texts_carry_no_task_literal_and_are_english():
    from agent import codegen
    from agent.conventions import PROJECT_CONVENTIONS
    texts = [
        _sentence(codegen.SYSTEM_PROMPT, "A value the test itself types that breaks", 200),
        _sentence(codegen.SYSTEM_PROMPT, "- A repair never weakens what the requirement states", 450),
        _sentence(codegen.USER_TEXT["ask_diagnose"], "(a value the spec types", 160),
        _sentence(PROJECT_CONVENTIONS, "A create or submit the server rejects", 220),
        _sentence(PROJECT_CONVENTIONS, "- State the requirement says survives", 340),
        _sentence(PROJECT_CONVENTIONS, "A structural edit (insert or", 280),
        _sentence(PROJECT_CONVENTIONS, "The same holds for anything the requirement says a control OPENS", 300),
        _sentence(PROJECT_CONVENTIONS, "Entries the requirement calls options", 260),
        _sentence(PROJECT_CONVENTIONS, "`put()` holds exactly what a GET", 120),
    ]
    for text in texts:
        assert text.isascii(), text[:60]
        for literal in TASK_LITERALS:
            assert literal not in text, (literal, text[:60])

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
    print(f"{len(CASES) - failed}/{len(CASES)} implementer cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
