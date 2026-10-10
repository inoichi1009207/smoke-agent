from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CASES = []

def case(fn):
    CASES.append(fn)
    return fn

def _conventions(**flags) -> str:
    from agent import conventions, settings

    old = settings.setting_on
    settings.setting_on = lambda key, default=True: flags.get(key, old(key, default))
    try:
        return conventions.v166_conventions()
    finally:
        settings.setting_on = old

@case
def the_key_is_registered_and_on_by_default():
    from agent.settings import _DEFAULTS

    assert _DEFAULTS.get("choice_select_hybrid") is True

@case
def on_appends_the_component_after_the_v167_lines():
    from agent import conventions as c

    text = _conventions(conventions_v166=True, conventions_v167=True, choice_select_hybrid=True, nav_entry_data_driven=False, state_disables_everywhere=False, conventions_v168x=False)
    assert text == c.V166_CONVENTIONS + c.V167_CONVENTIONS + c.CHOICE_SELECT_CONVENTION
    assert "```tsx\nfunction ChoiceSelect(" in text and text.endswith("```\n")

@case
def off_is_v167c_word_for_word():
    from agent import conventions as c

    assert _conventions(conventions_v166=True, conventions_v167=True, choice_select_hybrid=False, nav_entry_data_driven=False, state_disables_everywhere=False, conventions_v168x=False) == (
        c.V166_CONVENTIONS + c.V167_CONVENTIONS)
    assert _conventions(conventions_v166=True, conventions_v167=False, choice_select_hybrid=True) == c.V166_CONVENTIONS
    assert _conventions(conventions_v166=False, choice_select_hybrid=True) == ""

@case
def the_component_keeps_what_the_probe_proved():
    from agent.conventions import CHOICE_SELECT_TSX as tsx

    assert '<option key={o.value} value={o.value} disabled={o.disabled} aria-hidden="true">' in tsx
    assert 'role="listbox"' in tsx and 'role="option"' in tsx
    assert "aria-label" not in tsx and "aria-labelledby" not in tsx
    assert "e.preventDefault(); e.currentTarget.focus(); setOpen" in tsx
    assert "size" not in tsx
    assert "onBlur={() => setOpen(false)}" in tsx and "setTimeout" not in tsx
    assert "if (e.key === 'Escape') setOpen(false)" in tsx
    assert "onChange(e.target.value); setOpen(false);" in tsx
    assert "aria-controls={`${id}-list`}" in tsx and "id={`${id}-list`}" in tsx
    assert tsx.count("{") == tsx.count("}") and tsx.count("(") == tsx.count(")")

@case
def no_test_literal_in_the_convention():
    from agent.conventions import CHOICE_SELECT_CONVENTION as text

    for literal in ("Condition", "Style", "Member", "Role", "Green fill", "Default branch", "Parent team"):
        assert not re.search(r"(?<![A-Za-z])" + re.escape(literal) + r"(?![A-Za-z])", text), literal

def main() -> int:
    failed = 0
    for fn in CASES:
        try:
            fn()
            print(f"ok    {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc!r}")
    print(f"{len(CASES) - failed}/{len(CASES)} v167d choice-select cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
