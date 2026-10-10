from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

CASES = []

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
def both_keys_are_registered_and_on():
    from agent.settings import _DEFAULTS

    assert _DEFAULTS.get("nav_entry_data_driven") is True and _DEFAULTS.get("item_links_advisory") is True

@case
def the_nav_line_is_appended_last_and_only_when_on():
    from agent import conventions as c

    with _Flags(conventions_v166=True, conventions_v167=True, choice_select_hybrid=True, nav_entry_data_driven=True, state_disables_everywhere=False, conventions_v168x=False):
        on = c.v166_conventions()
    with _Flags(conventions_v166=True, conventions_v167=True, choice_select_hybrid=True, nav_entry_data_driven=False, state_disables_everywhere=False, conventions_v168x=False):
        off = c.v166_conventions()
    assert on == off + c.NAV_ENTRY_CONVENTION and on.count(c.NAV_ENTRY_CONVENTION) == 1
    assert off == c.V166_CONVENTIONS + c.V167_CONVENTIONS + c.CHOICE_SELECT_CONVENTION

@case
def the_nav_line_is_additive_and_generic():
    from agent.conventions import NAV_ENTRY_CONVENTION as text

    assert text.isascii() and "`" not in text and '"' not in text
    for word in ("Releases", "release", "reaction", "evo-", "acme", "workbook", "repository", "REQ-"):
        assert not re.search(re.escape(word), text, re.I), word
    assert "in addition to every entry a scenario reaches from its start page (keep those)" in text
    assert "hard-coded" not in text and "never" not in text

@case
def the_path_finding_carries_the_reminder_only_when_on():
    source = (ROOT / "agent" / "outsider.py").read_text(encoding="utf-8")
    assert "the record's own navigation offers the entry too" in source
    assert 'if _S.get("nav_entry_data_driven", True) else ""' in source

@case
def the_button_statement_detector():
    from agent.detaudit import TITLES_AS_BUTTONS as rx

    for said in ("Each item title is a button", "List titles are not links", "Item titles are buttons",
                 "The titles of the items are buttons"):
        assert rx.search(said), said
    for not_said in ("Next to the editor title is a button with the accessible name", "The workbook title is a link",
                     "Each header provides a button", "the editor title and the home-page link display the new name"):
        assert not rx.search(not_said), not_said
    for task in ("github", "sheet"):
        path = Path(f"local-data/evolution/req/hackathon-evolution--{task}/requirements.yaml")
        if path.exists():
            assert not rx.search(path.read_text(encoding="utf-8")), task

@case
def item_links_yields_where_the_kind_is_open_and_reports_where_buttons_are_stated():
    import cases_consist1 as c1

    with _Flags(item_links_advisory=True):
        assert c1._item_links(c1.DASHBOARD, c1.DASHBOARD, c1.GITHUB_LIST) == []
        stated = c1.DASHBOARD + c1.NL + "Each item title is a button that opens it."
        assert len(c1._item_links(c1.DASHBOARD, stated, c1.GITHUB_LIST)) == 1
        not_links = c1.DASHBOARD + c1.NL + "List titles are not links; a link opens the settings."
        assert len(c1._item_links(c1.DASHBOARD, not_links, c1.GITHUB_LIST)) == 1
    with _Flags(item_links_advisory=False):
        assert len(c1._item_links(c1.DASHBOARD, c1.DASHBOARD, c1.GITHUB_LIST)) == 1

@case
def the_state_line_is_appended_last_generic_and_switchable():
    from agent import conventions as c

    with _Flags(conventions_v166=True, conventions_v167=True, choice_select_hybrid=True, nav_entry_data_driven=True,
                state_disables_everywhere=True, conventions_v168x=False):
        on = c.v166_conventions()
    with _Flags(conventions_v166=True, conventions_v167=True, choice_select_hybrid=True, nav_entry_data_driven=True,
                state_disables_everywhere=False, conventions_v168x=False):
        off = c.v166_conventions()
    assert on == off + c.STATE_DISABLES_CONVENTION
    text = c.STATE_DISABLES_CONVENTION
    assert text.isascii() and "`" not in text and '"' not in text
    for word in ("archive", "issue", "repository", "pull", "New ", "REQ-"):
        assert word.lower() not in text.lower(), word

def main() -> int:
    failed = 0
    for fn in CASES:
        try:
            fn()
            print(f"ok    {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc!r}")
    print(f"{len(CASES) - failed}/{len(CASES)} v167 r8 cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
