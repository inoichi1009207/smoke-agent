from __future__ import annotations

import os
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from agent import audit, codegen, conventions, settings, test_author

CASES = []

def case(function):
    CASES.append(function)
    return function

def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)

class Switches:
    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        self.saved = {key: settings.SETTINGS.get(key) for key in self.values}
        settings.SETTINGS.update(self.values)
        return self

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            if value is None:
                settings.SETTINGS.pop(key, None)
            else:
                settings.SETTINGS[key] = value
        return False

def implementer_prompts() -> list:
    out = [codegen.SYSTEM_PROMPT, codegen.SYSTEM_PROMPT_WHOLE_FILES]
    saved = os.environ.get("AGENT_EDIT_BLOCKS")
    try:
        for value in ("true", "false"):
            os.environ["AGENT_EDIT_BLOCKS"] = value
            with Switches(conventions_v166=True, conventions_v167=True):
                out.append(codegen.system_prompt())
    finally:
        if saved is None:
            os.environ.pop("AGENT_EDIT_BLOCKS", None)
        else:
            os.environ["AGENT_EDIT_BLOCKS"] = saved
    return out

def convention_prompts() -> list:
    return implementer_prompts() + [test_author.SYSTEM_PROMPT]

@case
def a1_staying_on_the_page_is_for_refused_submissions_only():
    for prompt in implementer_prompts():
        text = flat(prompt)
        assert "After a create or submit the page shows either the values just entered or the server's reason" \
               not in text
        assert "A refused create or submit stays on the same page and shows the server's reason for refusing" in text
        assert "a successful one ends where the requirement says it goes" in text

        assert "says opens the new record ends ON that record's page" in text

@case
def a2_no_session_and_no_account_are_separate_checks():
    text = flat(test_author.SYSTEM_PROMPT)
    assert "must not create an account / session" not in text
    assert "and the same data can still be registered successfully" not in text
    assert "must not create a session -> afterwards still signed out" in text
    assert ("must not create an account -> no new session (the checks above); a later registration of the same data"
            " is asserted to succeed only when the requirement says nothing else about that data") in text

@case
def c2_a_landing_page_is_proven_by_its_stated_content_not_an_unstated_url():
    text = flat(test_author.SYSTEM_PROMPT)
    assert "navigates to / returns to / enters (导航, 返回, 进入) -> toHaveURL(...)" not in text
    assert "-> what the requirement states that page shows is visible" in text
    assert "toHaveURL(...) only for a URL the requirement states" in text

@case
def a3_a_choose_one_control_has_one_precedence_and_native_select_is_the_default():
    old = "is a combobox the user CLICKS"
    for prompt in convention_prompts():
        text = flat(prompt)
        assert old not in text and "outranks the general allowance of a native <select>" not in text
        assert ("A choose-one control (the requirement says the user selects or chooses an option in a labelled control"
                " or in a combobox) takes, in this order: the role or interaction the requirement states for it") in text
        assert "the kind the existing application's control already has; otherwise a native <select>" in text
    v167 = flat(conventions.V167_LINES[0])
    assert "or the existing application already renders that control as another kind (it keeps it)" in v167
    assert "is a native select element" in v167

@case
def a4_c1_open_kinds_follow_one_order_in_conventions_and_audit():
    for prompt in convention_prompts():
        text = flat(prompt)
        assert "A bare <Link> is right only where the requirement says link" not in text
        assert "render ONE <button type=\"button\"> -- and when X is a list item's own title," not in text
        assert ("the kind is decided in this order: (1) the kind this requirement names for that control") in text
        assert "(2) the kind the control already has when the existing application (an earlier stage) renders it" in text
        assert "(3) these defaults: a pure navigation entry" in text
        assert "any other control is ONE <button type=\"button\">" in text
        assert "are links to their pages unless the order of kinds above gives another kind" in text
    nav = flat(conventions.V166_LINES[3])
    assert nav.startswith("- Unless the requirement or the kind an existing control already has decides otherwise")
    auditor = flat(audit.SYSTEM_PROMPT)
    assert "this application's rule renders such a control as ONE <button" not in auditor
    assert "(fix: render it as one <button type=\"button\">, same name)" not in auditor
    assert ("this application decides it in this order: (1) the kind another sentence of the requirement names for the"
            " same control; (2) the kind a control an earlier stage of the application already delivered has;"
            " (3) the defaults: a link for a pure navigation entry") in auditor
    assert "(fix: render it as the kind the order gives, same name)" in auditor

@case
def a5_the_unnamed_menu_interface_default_states_itself_as_the_one_exception():
    line = flat(conventions.V167_LINES[1])
    assert ("the one exception to the rules above that a dialog name is never invented and that a describing phrase"
            " gives a panel no dialog") in line
    assert "places it in a container it names" in line

    assert "Never invent a name for a dialog, region or form." in conventions.PROJECT_CONVENTIONS
    for prompt in implementer_prompts()[2:]:
        assert conventions.V167_LINES[1] in prompt

@case
def the_changed_default_lines_stay_generic():
    for line in conventions.V166_LINES + conventions.V167_LINES:
        assert line.startswith("- ") and line.isascii() and "\n" not in line, line
        assert not re.search(r"\b(evo|acme|REQ-)", line, re.I), line

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
    print(f"{len(CASES) - failed}/{len(CASES)} v167c consistency cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
