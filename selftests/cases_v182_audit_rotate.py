from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES: list = []
KEY = "audit_rotate_unresolved"

def case(function):
    CASES.append(function)
    return function

class Switch:

    def __init__(self, **values):
        self.values, self.saved = values, {}

    def __enter__(self):
        from agent.settings import SETTINGS

        for key, value in self.values.items():
            self.saved[key] = (key in SETTINGS, SETTINGS.get(key))
            SETTINGS[key] = value
        return self

    def __exit__(self, *exc):
        from agent.settings import SETTINGS

        for key, (had, value) in self.saved.items():
            if had:
                SETTINGS[key] = value
            else:
                SETTINGS.pop(key, None)
        return False

def plain(n: int, layer: str = "scripted") -> list[dict]:
    return [{"where": f"page{i}", "problem": f"problem number {chr(97 + i)}", "layer": layer} for i in range(n)]

def ui(check: str, n: int, in_cap: int) -> list[dict]:
    return [{"where": f"{check} src/F{i}.tsx:{10 + i}", "problem": f"{check} target {chr(97 + i)}", "layer": "scripted",
             "check": check, "audit_target": f"{check}|src/F{i}.tsx|t{i}", **({"over_check_cap": True} if i >= in_cap else {})}
            for i in range(n)]

def wheres(findings: list[dict]) -> list[str]:
    return [f["where"] for f in findings]

@case
def rolled_back_findings_are_not_pushed_out():
    from agent import audit

    audit.reset_rotation()
    findings = plain(8)
    with Switch(**{KEY: True}):
        first, _, cut1 = audit.select_for_repair(findings, [])
        second, _, cut2 = audit.select_for_repair(findings, [])
    assert wheres(first) == [f"page{i}" for i in range(6)], wheres(first)
    assert wheres(second) == wheres(first), wheres(second)
    assert wheres(cut2) == ["page6", "page7"], wheres(cut2)
    ledger = audit.repair_ledger()
    assert {e["where"] for e in ledger.values()} == {"page6", "page7"}, ledger
    assert all(e["why"].startswith("over the cap") for e in ledger.values())

def _v181(findings, sent):
    from agent import audit

    with Switch(**{KEY: False}):
        return audit.select_for_repair(findings, sent)

@case
def codex_case_b_check_cap_rolled_back_a_is_sent_first():
    from agent import audit

    audit.reset_rotation()
    findings = ui("dialog_close", 2, 1)
    with Switch(**{KEY: True}):
        first, _, _ = audit.select_for_repair(findings, [])
        assert wheres(first) == wheres(findings[:1]), wheres(first)
        now, repeated, later = audit.select_for_repair(findings, [])
    old_now, old_rep, _ = _v181(findings, [])
    assert wheres(now)[:len(old_now)] == wheres(old_now) == wheres(findings[:1]), (wheres(now), wheres(old_now))
    assert wheres(now) == wheres(findings[:1]) and wheres(later) == wheres(findings[1:]) and repeated == old_rep == []

@case
def codex_case_a_sent_a_frees_its_slot_for_b():
    from agent import audit

    audit.reset_rotation()
    findings = ui("dialog_close", 2, 1)
    sent = [audit.finding_fingerprint(findings[0])]
    old_now, old_rep, old_later = _v181(findings, sent)
    assert old_now == [] and wheres(old_later) == wheres(findings[1:])
    with Switch(**{KEY: True}):
        now, repeated, later = audit.select_for_repair(findings, sent)
    assert wheres(now) == wheres(findings[1:]) and later == [], wheres(now)
    assert wheres(repeated) == wheres(old_rep)

@case
def v181_selection_is_always_the_prefix():
    import random

    from agent import audit

    rnd = random.Random(183)
    for trial in range(300):
        audit.reset_rotation()
        findings = (plain(rnd.randint(0, 7)) + plain(rnd.randint(0, 3), "model") + ui("dialog_close", rnd.randint(0, 6), rnd.randint(0, 3))
                    + ui("status_word", rnd.randint(0, 5), rnd.randint(0, 2)) + plain(rnd.randint(0, 2)))
        for f in findings:
            if rnd.random() < 0.3:
                audit._TRIED[audit.finding_fingerprint(f)] = rnd.randint(1, 3)
        sent = [audit.finding_fingerprint(f) for f in findings if rnd.random() < 0.3]
        old_now, old_rep, old_later = _v181(findings, sent)
        with Switch(**{KEY: True}):
            now, repeated, later = audit.select_for_repair(findings, sent)
        assert wheres(now)[:len(old_now)] == wheres(old_now), (trial, wheres(now), wheres(old_now))
        assert wheres(repeated) == wheres(old_rep), trial
        assert len(now) <= audit.MAX_REPAIR_FINDINGS, trial
        for check in ("dialog_close", "status_word"):
            mine = {audit.finding_fingerprint(f): f for f in findings if f.get("check") == check}
            if any(f.get("over_check_cap") for f in mine.values()):
                cap = sum(1 for f in mine.values() if not f.get("over_check_cap"))
                assert sum(1 for f in now if f.get("check") == check) <= cap, (trial, check)
        assert sorted(wheres(now) + wheres(later)) == sorted(wheres(old_now) + wheres(old_later)), trial
    audit.reset_rotation()

@case
def check_cap_slots_go_to_unsent_findings():
    from agent import audit

    audit.reset_rotation()
    findings = ui("dialog_close", 5, 3)
    sent = [audit.finding_fingerprint(f) for f in findings[:3]]
    with Switch(**{KEY: True}):
        now, repeated, later = audit.select_for_repair(findings, sent)
    assert wheres(now) == wheres(findings[3:]), wheres(now)
    assert len(repeated) == 3 and later == []
    audit.reset_rotation()
    findings = ui("dialog_close", 7, 3)
    sent = [audit.finding_fingerprint(f) for f in findings[:1]]
    with Switch(**{KEY: True}):
        now, _, later = audit.select_for_repair(findings, sent)
    assert len(now) == 3 and wheres(now) == wheres(findings[1:4]), wheres(now)
    assert len(later) == 3
    assert all(e["why"] == "over its check's cap" for e in audit.repair_ledger().values())

@case
def nothing_tried_no_cap_reached_is_the_old_selection():
    from agent import audit

    findings = plain(3, "model") + plain(5) + ui("cell_glyph", 6, 3) + plain(2)
    sent = [audit.finding_fingerprint(findings[4])]
    audit.reset_rotation()
    with Switch(**{KEY: False}):
        old = audit.select_for_repair(findings, sent)
    with Switch(**{KEY: True}):
        new = audit.select_for_repair(findings, sent)
    assert [wheres(part) for part in new] == [wheres(part) for part in old], ([wheres(p) for p in new], [wheres(p) for p in old])

@case
def never_more_than_today():
    from agent import audit

    audit.reset_rotation()
    findings = ui("status_word", 9, 4) + plain(10)
    with Switch(**{KEY: True}):
        for _ in range(5):
            now, _, later = audit.select_for_repair(findings, [])
            assert len(now) <= audit.MAX_REPAIR_FINDINGS, len(now)
            assert sum(1 for f in now if f.get("check") == "status_word") <= 4
            assert len(now) + len(later) == len(findings)

@case
def off_is_v181():
    from agent import audit

    audit.reset_rotation()
    findings = plain(8)
    with Switch(**{KEY: False}):
        a, _, _ = audit.select_for_repair(findings, [])
        b, _, _ = audit.select_for_repair(findings, [])
        assert wheres(a) == wheres(b) == [f"page{i}" for i in range(6)]
        ui_findings = ui("dialog_close", 5, 3)
        now, repeated, later = audit.select_for_repair(ui_findings, [audit.finding_fingerprint(f) for f in ui_findings[:3]])
        assert now == [] and len(repeated) == 3 and wheres(later) == wheres(ui_findings[3:])
    assert audit.repair_ledger() == {} and audit._TRIED == {}

@case
def detaudit_cap_never_pushes_out_found_cap():
    from agent import audit, detaudit

    audit.reset_rotation()
    found = plain(10)
    with Switch(**{KEY: True, "detaudit_rotate_cap": True}):
        audit.select_for_repair([found[2]], [audit.finding_fingerprint(found[2])])
        assert detaudit._capped(found, 8) == found[:8]
        audit.reset_rotation()
        audit._TRIED[audit.finding_fingerprint(found[2])] = 1
        kept = detaudit._capped(found, 8)
    assert kept == found[:8] and found[2] in kept, wheres(kept)
    assert {e["where"] for e in audit.repair_ledger().values()} == {"page8", "page9"}
    audit.reset_rotation()

@case
def detaudit_cap_unchanged_when_nothing_was_sent_or_off():
    import inspect

    from agent import audit, detaudit

    audit.reset_rotation()
    found = plain(10)
    with Switch(**{"detaudit_rotate_cap": True}):
        assert detaudit._capped(found, 8) == found[:8]
        assert detaudit._capped(found[:8], 8) == found[:8]
        assert detaudit._capped(found, 1_000_000) == found
    audit._SENT_SEEN.add(audit.finding_fingerprint(found[0]))
    with Switch(**{"detaudit_rotate_cap": False}):
        assert detaudit._capped(found, 8) == found[:8]
    assert {e["where"] for e in audit.repair_ledger().values()} == {"page8", "page9"}
    source = inspect.getsource(detaudit.deterministic_findings)
    for name in ("activation_found", "commit_found", "keys_found", "structure_found", "interaction_found", "visual"):
        assert f"_capped({name}, MAX_FINDINGS_PER_CHECK)" in source, name
    audit.reset_rotation()

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed} passed, {failed} failed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
