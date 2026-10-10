from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CASES = []

def case(fn):
    CASES.append(fn)
    return fn

def _tsx() -> str:
    from agent.conventions import CHOICE_SELECT_TSX

    return CHOICE_SELECT_TSX

@case
def disabled_is_an_optional_prop_forwarded_to_the_native_select():
    tsx = _tsx()
    assert tsx.startswith("function ChoiceSelect({ id, label, value, options, onChange, disabled }: {")
    assert "onChange: (value: string) => void; disabled?: boolean;" in tsx
    assert "<select id={id} value={value} disabled={disabled} " in tsx

@case
def disabled_never_opens_and_never_accepts():
    tsx = _tsx()
    assert "onMouseDown={(e) => { if (disabled) return; e.preventDefault(); e.currentTarget.focus(); setOpen((o) => !o); }}" in tsx
    assert "{open && !disabled && (" in tsx
    assert "aria-expanded={open && !disabled}" in tsx
    assert "onChange={(e) => { if (!disabled) onChange(e.target.value); setOpen(false); }}" in tsx

@case
def a_disabled_option_is_native_disabled_and_not_choosable_in_the_popup():
    tsx = _tsx()
    assert "options: { value: string; label: string; disabled?: boolean }[];" in tsx
    assert 'disabled={o.disabled} aria-hidden="true">' in tsx
    assert "aria-disabled={o.disabled || undefined}" in tsx
    assert "onClick={() => { if (!o.disabled) { onChange(o.value); setOpen(false); } }}" in tsx

@case
def the_two_way_driving_is_kept():
    tsx = _tsx()
    assert 'role="listbox"' in tsx and 'role="option"' in tsx and "aria-selected={o.value === value}" in tsx
    assert "onMouseDown={(e) => e.preventDefault()}" in tsx
    assert "onBlur={() => setOpen(false)}" in tsx and "if (e.key === 'Escape') setOpen(false)" in tsx
    assert "aria-controls={`${id}-list`}" in tsx and "id={`${id}-list`}" in tsx
    assert "aria-label" not in tsx and "aria-labelledby" not in tsx and "size" not in tsx and "setTimeout" not in tsx

@case
def no_new_hooks_imports_or_observers():
    tsx = _tsx()
    hooks = set(re.findall(r"\buse[A-Z]\w*", tsx))
    assert hooks == {"useState"}, hooks
    for word in ("import", "MutationObserver", "fieldset", "FIELDSET", "useRef", "useEffect", "SelectHTMLAttributes",
                 ":disabled", "...", "ref="):
        assert word not in tsx, word
    assert tsx.count("{") == tsx.count("}") and tsx.count("(") == tsx.count(")") and tsx.count("[") == tsx.count("]")
    assert len(tsx.splitlines()) <= 30

@case
def the_convention_names_disabled_and_stays_ascii_and_task_free():
    from agent.conventions import CHOICE_SELECT_CONVENTION as text, CHOICE_SELECT_TSX

    assert text.isascii()
    assert "pass disabled when the requirement makes the control unavailable" in text
    assert "and disabled on an option the requirement makes unavailable" in text
    assert text.endswith("```tsx\n" + CHOICE_SELECT_TSX + "\n```\n")
    for literal in ("Condition", "Style", "Member", "Role", "Owner", "Viewer", "Plan", "Pro", "Locked", "Green fill",
                    "Default branch", "Parent team", "Priority", "Status"):
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
    print(f"{len(CASES) - failed}/{len(CASES)} v181 choice-select disabled cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
