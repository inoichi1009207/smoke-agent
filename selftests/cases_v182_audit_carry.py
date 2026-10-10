from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_rollback as rb
import cases_v146a as v146a

CASES = []
LAST_SEEN: list = []
GREEN_UNIT = {"do": [("write", {rb.APP: "base OWN-OK"}), ("check",)]}

def case(function):
    CASES.append(function)
    return function

def run(audit_do: list, on: bool = True):
    from agent import audit

    calls: list[str] = []
    after: dict = {}

    def fake_recheck(project_dir, node, contract, repaired, log=print):
        text = (Path(project_dir) / rb.APP).read_text(encoding="utf-8")
        calls.append(text)
        if "FIXED" in text:
            return {"ran": True, "gone": [dict(f) for f in repaired], "still": []}
        return {"ran": True, "gone": [], "still": [dict(f) for f in repaired]}

    def prep(f, offset, runs):
        real = f.process_node

        def wrapped(*a, **k):
            try:
                return real(*a, **k)
            finally:
                after["app"] = (f.project_dir / rb.APP).read_text(encoding="utf-8")
                after["f"] = f
        f.process_node = wrapped

    saved = audit.recheck_repaired
    audit.recheck_repaired = fake_recheck
    try:
        _, _, seen, _, _ = v146a.run_world([dict(GREEN_UNIT), {"do": audit_do}], nodes_left=1, audit=True, prep=prep,
                                           settings={"audit_carry_still": on})
        LAST_SEEN[:] = seen
    finally:
        audit.recheck_repaired = saved
    return after["app"], after["f"], v146a.rb.CHECK_OUTPUTS, calls

@case
def a_green_check_with_findings_still_there_carries_them_into_the_next_round():
    from cases_v145a import CHECKS
    CHECKS.clear()
    app, f, _, calls = run([("write", {rb.APP: "base OWN-OK a1"}), ("check",), ("write", {rb.APP: "base OWN-OK FIXED"}), ("check",)])
    assert app == "base OWN-OK FIXED", app
    assert calls == ["base OWN-OK a1", "base OWN-OK FIXED"], calls
    audit_checks = [c for c in CHECKS if "AUDIT FINDINGS STILL THERE" in c]
    assert len(audit_checks) == 1 and "AUDIT BRIEF" in audit_checks[0], CHECKS
    assert f.audit_sent_fingerprints, "the finding is settled once its re-check says gone"

@case
def a_next_round_that_ends_red_keeps_the_earlier_green_state():
    from cases_v145a import CHECKS
    CHECKS.clear()
    app, f, _, calls = run([("write", {rb.APP: "base OWN-OK a1"}), ("check",), ("write", {rb.APP: "base broken"}), ("check",)])
    assert app == "base OWN-OK a1", app
    assert calls == ["base OWN-OK a1"], calls
    assert not f.audit_sent_fingerprints and f.audit_rolled_back_fingerprints, (f.audit_sent_fingerprints, f.audit_rolled_back_fingerprints)

@case
def nothing_still_there_ends_as_before_with_one_recheck():
    from cases_v145a import CHECKS
    CHECKS.clear()
    app, f, _, calls = run([("write", {rb.APP: "base OWN-OK FIXED"}), ("check",)])
    assert app == "base OWN-OK FIXED" and calls == ["base OWN-OK FIXED"], (app, calls)
    assert not any("AUDIT FINDINGS STILL THERE" in c for c in CHECKS), CHECKS

@case
def off_the_green_check_ends_the_repair_as_v181():
    from cases_v145a import CHECKS
    CHECKS.clear()
    app, f, _, calls = run([("write", {rb.APP: "base OWN-OK a1"}), ("check",)], on=False)
    assert app == "base OWN-OK a1" and calls == ["base OWN-OK a1"], (app, calls)
    assert not any("AUDIT FINDINGS STILL THERE" in c for c in CHECKS), CHECKS

    assert not f.audit_sent_fingerprints, f.audit_sent_fingerprints

@case
def the_carry_happens_at_most_once_and_adds_no_round():

    from cases_v145a import CHECKS
    CHECKS.clear()
    script = [("write", {rb.APP: "base OWN-OK a1"}), ("check",), ("write", {rb.APP: "base OWN-OK a2"}), ("check",)]
    app, f, _, calls = run(script)
    repair = [i for i in LAST_SEEN if "AUDIT BRIEF" in str(getattr(i, "saw_context", "") or "")]
    assert len(repair) == 1, [getattr(i, "options", None) for i in LAST_SEEN]
    options = repair[0].options
    assert options.get("max_rounds") == 2 and options.get("extra_rounds") == 0 and not options.get("progress_rounds"), options
    assert len([c for c in CHECKS if "AUDIT FINDINGS STILL THERE" in c]) == 1, CHECKS
    assert app == "base OWN-OK a2" and len(calls) == 2 and calls[0] == "base OWN-OK a1", (app, calls)
    run([("write", {rb.APP: "base OWN-OK a1"}), ("check",)], on=False)
    off = [i for i in LAST_SEEN if "AUDIT BRIEF" in str(getattr(i, "saw_context", "") or "")]
    assert off and {k: off[0].options.get(k) for k in ("max_rounds", "extra_rounds", "progress_rounds")} == \
        {k: options.get(k) for k in ("max_rounds", "extra_rounds", "progress_rounds")}, (off[0].options, options)

    import tempfile

    import main as factory_module
    from agent import audit
    from agent.testrun import TestOutcome

    rechecks: list[int] = []
    saved = audit.recheck_repaired
    audit.recheck_repaired = lambda project_dir, node, contract, repaired, log=print: (
        rechecks.append(1) or {"ran": True, "gone": [], "still": [dict(x) for x in repaired]})
    try:
        with tempfile.TemporaryDirectory() as tmp:
            g = rb.make_factory(Path(tmp), "base OWN-OK")
            g.over_budget = lambda: False
            carry: dict = {}
            check = g.audit_carry_check("REQ-9", {"id": "REQ-9"}, lambda: TestOutcome(True, "E2E PASSED"),
                                        [{"kind": "x", "file": "a.js", "message": "m"}], carry,
                                        factory_module.clock.now() + 10 ** 6)
            verdicts = [check().passed for _ in range(4)]
    finally:
        audit.recheck_repaired = saved
    assert verdicts == [False, True, True, True] and len(rechecks) == 1, (verdicts, rechecks)

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
