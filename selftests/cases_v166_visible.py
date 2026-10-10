from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True

from agent import seed_foundation as sf
from agent import settings
from agent.testrun import vacuous_absence_checks
from cases_v144l import BATCH, REL, spec

CASES = []
GRID = "- The worksheet grid must render"

def case(function):
    CASES.append(function)
    return function

def synthetic() -> dict:
    sentence = "GIVEN the system pre-provisions organization evo-ops-lab with member evo-ops-user"
    return {"scenarios": [{"leaf": "REQ-5-1-2", "scenario": "Scenario 1", "sentences": [sentence],
                           "names": ["evo-ops-lab", "evo-ops-user"], "advisory": []}],
            "root": [], "names": ["evo-ops-lab", "evo-ops-user", "evo-other-org"], "advisory": [],
            "extent": {"grid": True, "col": 52, "row": 200, "cell": "AZ200", "refs": ["AZ200"]}}

def github() -> dict:
    yaml_path = Path("local-data/evolution/req/hackathon-evolution--github/requirements.yaml")
    if not yaml_path.is_file():
        return {}
    import yaml

    tree = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    ids = ["REQ-1-1-1", "REQ-1-1-2", "REQ-2-1-2", "REQ-3-1", "REQ-4-3-1", "REQ-1-4", "REQ-2-4", "REQ-3-5", "REQ-4-5", "REQ-5-5"]
    return sf.inventory(tree, ids)

def grid_line(text: str) -> str:
    return next((line[2:] for line in text.splitlines() if line.startswith("- The worksheet grid must render")), "")

def batch_like_main(inv: dict, members: list[str], seeds: str) -> dict:
    batch = dict(BATCH, members=members)
    batch["seed_sentences"] = seeds
    block = sf.batch_block(inv, [str(m) for m in (batch.get("members") or [batch.get("id")])])
    if block:
        batch["seed_sentences"] = (seeds + "\n\n" + block).strip()
    return batch

class Switch:
    def __init__(self, on: bool):
        self.on = on

    def __enter__(self):
        self.saved = settings.SETTINGS.get("fixture_visible_name_prompt")
        settings.SETTINGS["fixture_visible_name_prompt"] = self.on
        return self

    def __exit__(self, *exc):
        if self.saved is None:
            settings.SETTINGS.pop("fixture_visible_name_prompt", None)
        else:
            settings.SETTINGS["fixture_visible_name_prompt"] = self.saved
        return False

@case
def the_switch_is_registered_and_on():
    assert settings._DEFAULTS["fixture_visible_name_prompt"] is True

@case
def the_rule_is_generic_english_without_names_backticks_or_quotes():
    rule = sf.VISIBLE_NAME_RULE
    assert rule.startswith("- ") and not rule.startswith(GRID), rule
    assert not re.search("[`'\"\u2018\u2019\u201c\u201d]", rule), rule
    assert all(x not in rule.lower() for x in ("evo-", "acme", "audit", "e.g.")), rule
    assert rule.isascii(), rule

    for part in ("listed and opened", "exactly that identifier", "another display name or a composed title",
                 "seeded state only", "later renames"):
        assert part in rule, part

@case
def foundation_text_adds_exactly_the_rule_line_and_the_grid_line_is_unchanged():
    for inv in (synthetic(), github()):
        if not inv:
            continue
        off, on = sf.foundation_text(inv), sf.foundation_text(inv, visible=True)
        assert sf.VISIBLE_NAME_RULE not in off and sf.VISIBLE_NAME_RULE in on
        extra = [line for line in on.splitlines() if line not in off.splitlines()]
        assert extra == [sf.VISIBLE_NAME_RULE], extra
        assert on.replace(sf.VISIBLE_NAME_RULE + "\n", "", 1) == off
        assert grid_line(on) == grid_line(off)

        stage_a = "\n".join(line for line in on.splitlines() if not line.startswith(GRID))
        assert sf.VISIBLE_NAME_RULE in stage_a.splitlines()
    assert grid_line(sf.foundation_text(synthetic(), visible=True)).startswith("The worksheet grid must render")

@case
def the_batch_seed_block_and_the_stated_text_corpus_are_the_same_on_and_off():
    inv = synthetic()
    seeds = "SEED RECORDS: the requirement's own seed sentences"
    with Switch(False):
        off = batch_like_main(inv, ["REQ-5-1-2"], seeds)
    with Switch(True):
        on = batch_like_main(inv, ["REQ-5-1-2"], seeds)
    assert on == off
    assert not any(part in on["seed_sentences"] for part in ("listed and opened", "composed title", "seeded state only"))

    text = spec('  await notSee(page, "Evo Ops Lab");', '  await notSee(page, "composed title");')
    with Switch(False):
        found_off = vacuous_absence_checks(REL, text, off)
    with Switch(True):
        found_on = vacuous_absence_checks(REL, text, on)
    assert found_on == found_off and len(found_on) == 2, found_on
    leaked = dict(on, seed_sentences=on["seed_sentences"] + "\n  " + sf.VISIBLE_NAME_RULE)
    assert len(vacuous_absence_checks(REL, text, leaked)) == 1

@case
def only_the_foundation_task_reads_the_switch():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    readers = [line.strip() for line in main.splitlines() if "fixture_visible_name_prompt" in line]
    assert len(readers) == 1 and readers[0].startswith(
        'text = seed_foundation.foundation_text(inv, visible=setting_on("fixture_visible_name_prompt"))'), readers
    assert "seed_foundation.foundation_text(inv).splitlines()" in main
    calls = [line.strip() for line in main.splitlines() if "seed_foundation.batch_block(" in line]
    assert calls and all("visible" not in line for line in calls), calls
    import inspect

    assert "visible" not in inspect.signature(sf.batch_block).parameters
    for path in sorted((ROOT / "agent").glob("*.py")):
        if path.name != "settings.py":

            assert not re.search(r"""(setting_on|SETTINGS\.get|SETTINGS\[)\(?\s*["']fixture_visible_name_prompt""",
                                 path.read_text(encoding="utf-8")), path.name

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
    print(f"{len(CASES) - failed}/{len(CASES)} v166b visible-name cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
