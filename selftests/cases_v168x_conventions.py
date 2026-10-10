from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

CASES = []

ALL_ON = dict(conventions_v166=True, conventions_v167=True, choice_select_hybrid=True, nav_entry_data_driven=True,
              state_disables_everywhere=True)

def case(fn):
    CASES.append(fn)
    return fn

class _Flags:
    def __init__(self, **flags):
        self.flags = flags

    def __enter__(self):
        from agent import settings

        self.settings, self.old = settings, settings.setting_on
        settings.setting_on = lambda key, default=True: self.flags.get(key, self.old(key, default))
        return self

    def __exit__(self, *exc):
        self.settings.setting_on = self.old

@case
def the_key_is_registered_and_on():
    from agent.settings import _DEFAULTS

    assert _DEFAULTS.get("conventions_v168x") is True

@case
def the_lines_are_appended_last_and_only_when_on():
    from agent import conventions as c

    with _Flags(**ALL_ON, conventions_v168x=True):
        on = c.v166_conventions()
    with _Flags(**ALL_ON, conventions_v168x=False):
        off = c.v166_conventions()
    assert on == off + c.V168X_CONVENTIONS
    assert len(c.V168X_LINES) == 7
    assert c.V168X_CONVENTIONS == "".join(line + "\n" for line in c.V168X_LINES) + c.V168X_TAIL
    assert "not added features" in c.V168X_TAIL and c.V168X_TAIL.isascii()

@case
def nothing_when_the_parent_switches_are_off():
    from agent import conventions as c

    with _Flags(conventions_v166=False, conventions_v168x=True):
        assert c.v166_conventions() == ""
    with _Flags(conventions_v166=True, conventions_v167=False, conventions_v168x=True):
        assert c.v166_conventions() == c.V166_CONVENTIONS

@case
def the_lines_are_generic():
    from agent import conventions as c

    text = c.V168X_CONVENTIONS
    assert text.isascii() and "`" not in text and '"' not in text
    for line in c.V168X_LINES:
        assert line.startswith("- ") and "\n" not in line

    for word in ("REQ-", "Sign in", "Sign up", "Forgot", "Releases", "Note", "Active", "Archived", "reaction",
                 "issue", "repository", "workbook", "spreadsheet", "cell"):
        assert word not in text, word

@case
def the_menu_line_keeps_the_kind_order():
    from agent import conventions as c

    menu = c.V168X_LINES[0]

    assert "stays a link" in menu and "existing entry already has" in menu and "takes the visitor to another page" in menu and "only an entry that acts in place" in menu

def main() -> int:
    failed = 0
    for fn in CASES:
        try:
            fn()
            print(f"ok    {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc!r}")
    print(f"{len(CASES) - failed}/{len(CASES)} v168x cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
