from __future__ import annotations

import inspect
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_rollback as rb

NL = chr(10)
CASES = []
SEEDS = ("SEED SENTENCES ...\n- The evaluation seed contains the seeded worksheet range `A1:C6` with headers "
         "`Region/Sales/Status` and rows `East/1200/Open`, `North/800/Closed`, `South/700/Open`.")
BEFORE = (b"['A1','Region'],['A2','East'],['B2','1200'],['C2','Open'],['A3','North'],['B3','800'],"
          b"['C3','Closed'],['A4','South'],['B4','700']")
AFTER = b"['A1','Region'],['A2','West'],['B2','500'],['C2','Open'],['A3','East'],['B3','1200'],['A4','South'],['B4','1500']"
DB = "backend/src/database/init_db.js"

def case(function):
    CASES.append(function)
    return function

def note(held: dict, now: bytes = AFTER) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = rb.make_factory(root, "x")
        (root / DB).parent.mkdir(parents=True, exist_ok=True)
        (root / DB).write_bytes(now)
        f.seed_checks = dict(held)
        return f.seed_pending_note("REQ-9", {"id": "REQ-9", "seed_sentences": SEEDS}, {DB: BEFORE}, [rb.OWN])

@case
def values_held_once_and_still_lost_ride_along():
    text = note({"REQ-9:values": 1})
    assert text.startswith(NL + NL + "STILL OPEN from an earlier check") and '"North"' in text and '"800"' in text, text

@case
def nothing_held_or_nothing_lost_gives_no_note():
    assert note({}) == ""
    assert note({"REQ-9:values": 1}, now=BEFORE) == ""

@case
def wired_into_red_checks_before_the_foundation_note_and_off_is_v181():
    import main

    source = inspect.getsource(main.Factory.process_node)
    assert 'if not e2e.ok and setting_on("seed_pending_carry", True) else "")' in source
    assert "unit_note + api_note + seed_note + foundation_note)" in source

    assert source.index('seed_note = (self.seed_pending_note(') < source.index("final = TestOutcome(e2e.ok,")

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok   {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {function.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
