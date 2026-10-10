from __future__ import annotations

import os
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from agent import codegen, conventions, settings

CASES = []

def case(function):
    CASES.append(function)
    return function

class Switch:
    def __init__(self, on: bool):
        self.on = on

    def __enter__(self):
        self.saved = settings.SETTINGS.get("conventions_v166")
        settings.SETTINGS["conventions_v166"] = self.on
        return self

    def __exit__(self, *exc):
        if self.saved is None:
            settings.SETTINGS.pop("conventions_v166", None)
        else:
            settings.SETTINGS["conventions_v166"] = self.saved
        return False

class EditBlocks:
    def __init__(self, value: str):
        self.value = value

    def __enter__(self):
        self.saved = os.environ.get("AGENT_EDIT_BLOCKS")
        os.environ["AGENT_EDIT_BLOCKS"] = self.value
        return self

    def __exit__(self, *exc):
        if self.saved is None:
            os.environ.pop("AGENT_EDIT_BLOCKS", None)
        else:
            os.environ["AGENT_EDIT_BLOCKS"] = self.saved
        return False

@case
def the_switch_is_registered_and_on():
    assert settings._DEFAULTS["conventions_v166"] is True

@case
def four_generic_lines_without_backticks_or_quoted_names():
    lines = conventions.V166_LINES
    assert len(lines) == 4 and conventions.V166_CONVENTIONS == "".join(x + "\n" for x in lines)
    for line in lines:
        assert line.startswith("- ") and line.isascii() and "\n" not in line, line
        assert not re.search("[`\"‘’“”]", line), line
        assert not re.search(r"\b(evo|acme|REQ-|github|sheet\b|workbook|repository)", line, re.I), line
    for words in ("INSIDE that element", "text content is exactly its value", "confirmed that the record does not exist",
                  "is a link", "inside the main landmark"):
        assert any(words in x for x in lines), words

@case
def on_adds_exactly_the_four_lines_behind_the_project_conventions():
    for edit_env, plain in (("true", codegen.SYSTEM_PROMPT), ("false", codegen.SYSTEM_PROMPT_WHOLE_FILES)):
        with EditBlocks(edit_env):
            with Switch(True):
                text = codegen.system_prompt()
            assert text == plain.replace(conventions.PROJECT_CONVENTIONS,
                                         conventions.PROJECT_CONVENTIONS + conventions.V166_CONVENTIONS, 1)
            assert all(text.count(line) == 1 for line in conventions.V166_LINES)

            conventions.set_evolution_round(True)
            try:
                with Switch(True):
                    evo = codegen.system_prompt()
            finally:
                conventions.set_evolution_round(False)
            assert evo == plain.replace(conventions.PROJECT_CONVENTIONS, conventions.PROJECT_CONVENTIONS
                                        + "- " + conventions.EVOLUTION_DATA_LIFECYCLE + "\n"
                                        + conventions.V166_CONVENTIONS, 1)

@case
def off_is_v163_byte_for_byte():
    for edit_env, plain in (("true", codegen.SYSTEM_PROMPT), ("false", codegen.SYSTEM_PROMPT_WHOLE_FILES)):
        with EditBlocks(edit_env), Switch(False):
            assert codegen.system_prompt() == plain
            assert not any(line in codegen.system_prompt() for line in conventions.V166_LINES)
    with Switch(False):
        assert conventions.v166_conventions() == ""

@case
def never_in_the_shared_project_conventions():
    assert not any(line in conventions.PROJECT_CONVENTIONS for line in conventions.V166_LINES)
    from agent import contract, test_author

    for module in (contract, test_author):
        for name, value in vars(module).items():
            if isinstance(value, str):
                assert conventions.V166_LINES[0] not in value, (module.__name__, name)

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
    print(f"{len(CASES) - failed}/{len(CASES)} v166c convention cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
