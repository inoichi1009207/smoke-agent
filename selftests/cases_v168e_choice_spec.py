from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.dont_write_bytecode = True

from agent import settings, test_author

CASES = []

ON = {"spec_choice_both_ways": True, "conventions_v166": True, "conventions_v167": True, "choice_select_hybrid": True}

def case(fn):
    CASES.append(fn)
    return fn

class Switches:
    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        self.saved = {key: (key in settings.SETTINGS, settings.SETTINGS.get(key)) for key in self.values}
        settings.SETTINGS.update(self.values)
        return self

    def __exit__(self, *exc):
        for key, (present, value) in self.saved.items():
            if present:
                settings.SETTINGS[key] = value
            else:
                settings.SETTINGS.pop(key, None)
        return False

class FakeLLM:

    last_finish_reason = "stop"

    def __init__(self):
        self.systems = []

    def chat(self, messages, **kwargs):
        self.systems.append(messages[0]["content"])
        return {"content": "=== FILE: backend/test-e2e/REQ-1.spec.js ===\nconst a = 1;\n=== END FILE ==="}

def sent_systems(**flags) -> list:
    llm = FakeLLM()
    author = test_author.TestAuthor(llm, log=lambda *_: None)
    node = {"id": "REQ-1", "name": "n", "scenarios": []}
    files = [test_author.AuthoredFile("backend/test-e2e/REQ-1.spec.js", "const a = 1;\n")]
    with Switches(**flags):
        author.author(node, "REQ-1")
        author.repair(node, "REQ-1", files, "p")
        author.reconcile(node, "REQ-1", files, "f", "s")
    return llm.systems

@case
def the_key_is_registered_and_shipped_off():

    assert settings._DEFAULTS.get("spec_choice_both_ways") is False
    assert settings.setting_on("spec_choice_both_ways") is False

@case
def off_is_v167_byte_for_byte():
    base = test_author.SYSTEM_PROMPT
    assert test_author.CHOICE_SPEC_RULE not in base
    for key in ON:
        with Switches(**{**ON, key: False}):
            assert test_author.system_prompt() == base, key
    systems = sent_systems(**{**ON, "spec_choice_both_ways": False})
    assert len(systems) == 3 and all(s == base for s in systems)

@case
def on_inserts_the_rule_once_before_the_real_time_rule():
    base = test_author.SYSTEM_PROMPT
    rule = test_author.CHOICE_SPEC_RULE
    with Switches(**ON):
        text = test_author.system_prompt()
    assert text.count(rule) == 1
    assert text.count("Choose-one controls in the Playwright spec") == 1
    assert len(text) == len(base) + len(rule)
    assert text.replace(rule, "", 1) == base
    assert text.index(rule) + len(rule) == text.index("- Never wait for real time (auto-dismiss")
    systems = sent_systems(**ON)
    assert len(systems) == 3 and all(s == text for s in systems)

@case
def the_rule_names_both_ways_and_its_scope():
    flat = re.sub(r"\s+", " ", test_author.CHOICE_SPEC_RULE)
    for phrase in ("clicking that combobox (located by its label), then getByRole with role option",
                   "the exact option text from the requirement (exact: true)",
                   "a later choice in the same control uses selectOption({ label })",
                   "states no other interaction for it, such as a named button that opens a list",
                   "only once and the requirement does not say that choosing opens another page",
                   "read its inputValue(), choose the same option again with selectOption({ label }) and assert toHaveValue"):
        assert phrase in flat, phrase
    assert test_author.CHOICE_SPEC_RULE.endswith("\n") and test_author.CHOICE_SPEC_RULE.startswith("- ")

@case
def no_quoted_name_and_no_test_literal():
    rule = test_author.CHOICE_SPEC_RULE
    for quote in ('"', "'", "`", "“", "”", "‘", "’", "「", "」", "『", "』"):
        assert quote not in rule, repr(quote)
    assert all(ord(ch) < 128 for ch in rule)
    for literal in ("Condition", "Style", "Member", "Role", "Green fill", "Default branch", "Parent team", "Admin",
                    "Viewer", "Editor", "Owner"):
        assert not re.search(r"(?<![A-Za-z])" + re.escape(literal) + r"(?![A-Za-z])", rule), literal

def main() -> int:
    failed = 0
    for fn in CASES:
        try:
            fn()
            print(f"ok    {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc!r}")
    print(f"{len(CASES) - failed}/{len(CASES)} v168e choice-spec cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
