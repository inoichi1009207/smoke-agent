from __future__ import annotations

import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FIX = HERE / "fixtures" / "v168s_req512"
SAVE = "frontend/src/components/SaveFilterViewDialog.tsx"
CASES: list = []

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

def node_for(root: Path, written: list[str] | None = None) -> dict:
    node = json.loads((FIX / "node.json").read_text(encoding="utf-8"))
    node["audit_written"] = written if written is not None else sorted(
        p.relative_to(root).as_posix() for p in (root / "frontend").rglob("*.tsx"))
    return node

def initial_findings(root: Path = FIX, written: list[str] | None = None) -> list[dict]:
    from agent import batch_ui_checks as b
    from agent.detaudit import requirement_text

    node = node_for(root, written)
    found = b.batch_ui_findings(root, node, requirement_text(node), log=lambda *_: None, settings=None)
    for finding in found:
        finding.setdefault("layer", "scripted")
    return found

def recheck(root: Path, repaired: list[dict], written: list[str] | None = None, time_cap: float = 5.0, det=None,
            build_ok: bool = True):
    from agent import audit, verify
    from agent import batch_ui_checks as b
    from agent.detaudit import requirement_text

    node = node_for(root, written)

    def real_batch_ui(project_dir, node_, snap, log=print):
        return b.batch_ui_findings(root, node_, requirement_text(node_), log=log, settings=None, time_cap=time_cap)

    class Step:
        ok = build_ok

    saved = (audit.take_snapshot, audit.deterministic_findings, verify.build_frontend)
    logs: list[str] = []
    try:
        verify.build_frontend = lambda *a, **k: Step()
        audit.take_snapshot = lambda *a, **k: [{"route": "/"}]
        audit.deterministic_findings = det or real_batch_ui
        result = audit.recheck_repaired(root, node, "{}", [dict(f) for f in repaired], log=logs.append)
    finally:
        audit.take_snapshot, audit.deterministic_findings, verify.build_frontend = saved
    return result, logs

def save_finding() -> dict:
    found = [f for f in initial_findings() if f.get("check") == "dialog_close" and "SaveFilterViewDialog" in f["where"]]
    assert len(found) == 1, found
    return found[0]

def copy_fixture() -> Path:
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(FIX / "frontend", tmp / "frontend")
    shutil.copy(FIX / "node.json", tmp / "node.json")
    return tmp

@case
def every_replay_finding_has_a_line_free_identity_per_named_target():
    from agent.audit import finding_identities
    from agent.batch_ui_checks import finding_targets, status_word_findings

    found = initial_findings()
    assert {f["check"] for f in found} >= {"dialog_close", "dialog_entries", "cell_glyph", "stated_select", "header_button"}, found
    for finding in found:
        targets = finding_targets(finding)
        assert targets and all(t.startswith(finding["check"] + ":frontend/") for t in targets), (finding, targets)
        assert not any(t.rsplit(":", 1)[-1].isdigit() for t in targets), targets
    assert finding_targets(save_finding()) == [f"dialog_close:{SAVE}:Save filter view"]
    glyph = [f for f in found if f["check"] == "cell_glyph"][0]
    assert any(t.endswith(":U+25BE") for t in finding_targets(glyph)), finding_targets(glyph)
    status = status_word_findings({"frontend/src/A.tsx": "x\n{archived ? <span>Archived</span> : null}\n"},
                                  "The Archived status and the Active status are shown.")
    assert finding_targets(status[0]) == ["status_word:frontend/src/A.tsx:Archived"], status

    other = {"where": "route /x", "problem": "blank page", "layer": "scripted"}
    from agent.audit import finding_fingerprint
    assert finding_identities(other) == {finding_fingerprint(other)}
    assert finding_targets({"check": "dialog_close", "where": "dialog_close a.tsx:3", "problem": "reworded"}) is None

@case
def a_skipped_recheck_keeps_the_finding_open():
    finding = save_finding()
    with Switch(audit_settle_rechecked=True):
        result, logs = recheck(FIX, [finding], time_cap=0.0)
    assert result["ran"] and result["gone"] == [] and result["still"] == [], result
    assert [f["where"] for f in result["not_rechecked"]] == [finding["where"]], result
    assert any("not re-checked, their check did not run completely (dialog_close_check skipped)" in l for l in logs), logs
    with Switch(audit_settle_rechecked=False):
        finding = save_finding()
        assert "audit_target" not in finding
        result, _ = recheck(FIX, [finding], time_cap=0.0)
    assert [f["where"] for f in result["gone"]] == [finding["where"]] and "not_rechecked" not in result, result

@case
def a_crashed_recheck_keeps_the_finding_open():
    from agent import audit
    from agent import batch_ui_checks as b

    finding = save_finding()
    real = b.dialog_close_findings

    def boom(*a, **k):
        raise RuntimeError("parser")

    b.dialog_close_findings = boom
    try:
        with Switch(audit_settle_rechecked=True):
            result, logs = recheck(FIX, [finding])
    finally:
        b.dialog_close_findings = real

    assert not result["ran"] and result["gone"] == [] and result["still"] == [], result

    for status in ("crashed", "skipped"):
        assert audit.unrechecked_reason(finding, {"batch_ui": {"dialog_close_check": status}}) == f"dialog_close_check {status}"
    assert audit.unrechecked_reason(finding, {}) == "dialog_close_check not run"
    assert audit.unrechecked_reason(finding, {"batch_ui": {"dialog_close_check": "ran"}}) == ""
    other = {"where": "route /x", "problem": "blank page", "layer": "scripted"}
    assert audit.unrechecked_reason(other, {"incomplete": ["plan walk", "plan walk"]}) == "incomplete live check(s): plan walk"
    assert audit.unrechecked_reason(other, {"batch_ui": {}}) == ""

@case
def a_genuinely_fixed_finding_settles():
    finding = save_finding()
    tmp = copy_fixture()
    try:
        path = tmp / SAVE
        text = path.read_text(encoding="utf-8")
        anchor = '        <div className="flex justify-end gap-2">\n'
        assert text.count(anchor) == 1
        path.write_text(text.replace(anchor, anchor + '          <button type="button" onClick={onClose}>Close</button>\n'),
                        encoding="utf-8")
        with Switch(audit_settle_rechecked=True):
            result, logs = recheck(tmp, [finding])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert result["ran"] and [f["where"] for f in result["gone"]] == [finding["where"]], result
    assert result["still"] == [] and result["not_rechecked"] == [], result

@case
def a_finding_that_moved_lines_is_the_same_target_and_not_fixed():
    finding = save_finding()
    tmp = copy_fixture()
    try:
        path = tmp / SAVE
        path.write_text("// moved\n" * 7 + path.read_text(encoding="utf-8"), encoding="utf-8")
        with Switch(audit_settle_rechecked=True):
            result, _ = recheck(tmp, [finding])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert result["ran"] and [f["where"] for f in result["still"]] == [finding["where"]] and result["gone"] == [], result

@case
def one_of_two_named_dialogs_left_is_still_there():
    from agent.audit import finding_fingerprint

    both = {"where": f"dialog_close {SAVE}:16", "check": "dialog_close", "layer": "scripted", "requirement": "(x)",
            "problem": f"{SAVE} renders the dialog `Save filter view`, `Rename view` without a button whose accessible "
                       "name is exactly `Close`; ..."}
    left = dict(both, where=f"dialog_close {SAVE}:40",
                problem=f"{SAVE} renders the dialog `Rename view` without a button whose accessible name is exactly `Close`; ...")
    assert finding_fingerprint(both) != finding_fingerprint(left)

    def det(project_dir, node, snap, log=print):
        from agent.batch_ui_checks import RECHECK_LEDGER
        ledger = RECHECK_LEDGER.get()
        if ledger is not None:
            ledger.setdefault("batch_ui", {})["dialog_close_check"] = "ran"
        return [dict(left)]

    with Switch(audit_settle_rechecked=True):
        result, _ = recheck(FIX, [both], det=det)
    assert [f["where"] for f in result["still"]] == [both["where"]], result
    with Switch(audit_settle_rechecked=False):
        result, _ = recheck(FIX, [both], det=det)
    assert [f["where"] for f in result["gone"]] == [both["where"]], result

@case
def a_finding_beyond_the_reporting_cap_keeps_its_identity():
    from agent import batch_ui_checks as b
    from agent.batch_ui_checks import RECHECK_LEDGER

    dialog = ('export function D{i}() {{\n  return (\n    <div role="dialog" aria-label="Dialog {i}">\n'
              '      <button type="button">Cancel</button>\n    </div>\n  );\n}}\n')
    tmp = Path(tempfile.mkdtemp())
    try:
        src = tmp / "frontend" / "src"
        src.mkdir(parents=True)
        for i in range(2, 10):
            (src / f"D{i}.tsx").write_text(dialog.format(i=i), encoding="utf-8")
        written = sorted(f"frontend/src/D{i}.tsx" for i in range(2, 10))
        with Switch(audit_settle_rechecked=True):
            first = [f for f in initial_findings(tmp, written) if f["check"] == "dialog_close"]
        assert len(first) == 8 and not any(f.get("over_check_cap") for f in first), first
        target = [f for f in first if "D9.tsx" in f["where"]][0]
        with Switch(audit_settle_rechecked=False):
            target_v180 = [f for f in initial_findings(tmp, written) if "D9.tsx" in f["where"]][0]
        for name in ("A0", "A1"):
            (src / f"{name}.tsx").write_text(dialog.format(i=name), encoding="utf-8")
        written = sorted(written + ["frontend/src/A0.tsx", "frontend/src/A1.tsx"])
        with Switch(audit_settle_rechecked=False):
            assert len([f for f in initial_findings(tmp, written) if f["check"] == "dialog_close"]) == b.MAX_PER_CHECK
        with Switch(audit_settle_rechecked=True):

            scan = [f for f in initial_findings(tmp, written) if f["check"] == "dialog_close"]
            assert len(scan) == 10 and [f.get("over_check_cap", False) for f in scan] == [False] * 8 + [True] * 2, scan
            from agent.audit import select_for_repair
            now, _, later = select_for_repair(scan, [], limit=20)
            assert len(now) == 8 and len(later) == 2 and all(f.get("over_check_cap") for f in later), (now, later)
            result, _ = recheck(tmp, [target], written=written)
            assert result["ran"] and [f["where"] for f in result["still"]] == [target["where"]], result
        with Switch(audit_settle_rechecked=False):
            result, _ = recheck(tmp, [target_v180], written=written)
            assert [f["where"] for f in result["gone"]] == [target_v180["where"]], result

        from agent.detaudit import MAX_FINDINGS_PER_CHECK, _per_check
        assert _per_check() == MAX_FINDINGS_PER_CHECK
        token = RECHECK_LEDGER.set({})
        try:
            assert _per_check() > 1000
        finally:
            RECHECK_LEDGER.reset(token)
        assert RECHECK_LEDGER.get() is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

TWO = ('export function Two() {\n  return (\n    <>\n      <div role="dialog" aria-label="Alpha">\n'
       '        <button type="button">Cancel</button>\n      </div>\n      <div role="dialog" aria-label="Beta">\n'
       '        <button type="button">Cancel</button>\n      </div>\n    </>\n  );\n}\n')
TWO_REL = "frontend/src/Two.tsx"

def two_dialogs(alpha_close: bool = False) -> Path:
    tmp = Path(tempfile.mkdtemp())
    (tmp / "frontend" / "src").mkdir(parents=True)
    text = TWO.replace('aria-label="Alpha">\n', 'aria-label="Alpha">\n        <button type="button">Close</button>\n') \
        if alpha_close else TWO
    (tmp / TWO_REL).write_text(text, encoding="utf-8")
    return tmp

@case
def two_targets_of_one_file_never_collide_and_the_books_follow_the_target():
    from agent.audit import finding_fingerprint, legacy_fingerprint, select_for_repair

    tmp = two_dialogs()
    try:
        with Switch(audit_settle_rechecked=True):
            found = [f for f in initial_findings(tmp, [TWO_REL]) if f["check"] == "dialog_close"]
            assert [f["audit_target"] for f in found] == [f"dialog_close:{TWO_REL}:Alpha", f"dialog_close:{TWO_REL}:Beta"], found
            alpha, beta = found
            assert finding_fingerprint(alpha) != finding_fingerprint(beta)
            moved = dict(alpha, where=alpha["where"].rsplit(":", 1)[0] + ":99", problem=alpha["problem"] + " (reworded)")
            assert finding_fingerprint(moved) == finding_fingerprint(alpha)

            now, repeated, _ = select_for_repair([alpha, beta], [legacy_fingerprint(alpha)])
            assert repeated == [alpha] and now == [beta], (now, repeated)
            fixed = two_dialogs(alpha_close=True)
            try:
                result, _ = recheck(fixed, [alpha, beta], written=[TWO_REL])
            finally:
                shutil.rmtree(fixed, ignore_errors=True)
            assert [f["audit_target"] for f in result["gone"]] == [alpha["audit_target"]], result
            assert [f["audit_target"] for f in result["still"]] == [beta["audit_target"]], result
        with Switch(audit_settle_rechecked=False):
            v180 = [f for f in initial_findings(tmp, [TWO_REL]) if f["check"] == "dialog_close"]
            assert len(v180) == 1 and "`Alpha`, `Beta`" in v180[0]["problem"] and "audit_target" not in v180[0], v180
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

@case
def a_file_that_cannot_be_read_in_the_recheck_is_unchecked_not_gone():
    tmp = two_dialogs()
    try:
        with Switch(audit_settle_rechecked=True):
            alpha = [f for f in initial_findings(tmp, [TWO_REL]) if f["check"] == "dialog_close"][0]
            (tmp / TWO_REL).unlink()
            result, logs = recheck(tmp, [alpha], written=[TWO_REL])
        assert result["gone"] == [] and result["still"] == [] and result["not_rechecked"] == [alpha], result
        assert any(f"{TWO_REL} not readable" in l for l in logs), logs
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

STATUS_REL = "frontend/src/components/Status.tsx"
STATUS_REQ = "The system stores Archived status. An Admin can return the record to Active status."
STATUS_FORK = 'export const Row = ({ archived }) => (\n  <li>{archived ? <span>Archived</span> : null}</li>\n);\n'

@case
def status_word_findings_are_in_the_ledger_like_the_others():
    from agent import audit
    from agent import batch_ui_checks as b

    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / STATUS_REL).parent.mkdir(parents=True)
        (tmp / STATUS_REL).write_text(STATUS_FORK, encoding="utf-8")
        node = {"id": "REQ-3-5", "audit_written": [STATUS_REL]}

        def det(fixed_text=None, cap=5.0):
            def run(project_dir, node_, snap, log=print):
                if fixed_text is not None:
                    (tmp / STATUS_REL).write_text(fixed_text, encoding="utf-8")
                return b.batch_ui_findings(tmp, node, STATUS_REQ, log=log, settings=None, time_cap=cap)
            return run

        with Switch(audit_settle_rechecked=True):
            found = [f for f in b.batch_ui_findings(tmp, node, STATUS_REQ, log=lambda *_: None)
                     if f["check"] == "status_word"]
            assert [f["audit_target"] for f in found] == [f"status_word:{STATUS_REL}:Archived"], found
            result, _ = recheck(tmp, found, written=[STATUS_REL], det=det(cap=0.0))
            assert result["not_rechecked"] == found and not result["gone"], result
            assert audit.unrechecked_reason(found[0], {"batch_ui": {"status_word_check": "skipped"}}) == "status_word_check skipped"
            result, _ = recheck(tmp, found, written=[STATUS_REL], det=det())
            assert result["still"] == found, result
            result, _ = recheck(tmp, found, written=[STATUS_REL], det=det(
                STATUS_FORK.replace("<span>Archived</span> : null", "<span>Archived</span> : <span>Active</span>")))
            assert result["gone"] == found, result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

@case
def an_incomplete_live_check_is_recorded_only_during_a_recheck():
    from agent import detaudit
    from agent.batch_ui_checks import RECHECK_LEDGER

    key = "v181-selftest-live"
    detaudit._RESTART_CACHE.pop(key, None)
    detaudit._RESTART_COMPLETE.pop(key, None)
    try:
        assert detaudit._cached_live_check(key, "walk", lambda: ([], False), lambda *_: None, retry_incomplete=False) == []
        ledger: dict = {}
        token = RECHECK_LEDGER.set(ledger)
        try:
            detaudit._cached_live_check(key, "walk", lambda: ([], True), lambda *_: None, retry_incomplete=False)
            detaudit._cached_live_check(key + "2", "probe", lambda: ([], True), lambda *_: None)
        finally:
            RECHECK_LEDGER.reset(token)
        assert ledger == {"incomplete": ["walk"]}, ledger
    finally:
        for k in (key, key + "2"):
            detaudit._RESTART_CACHE.pop(k, None)
            detaudit._RESTART_COMPLETE.pop(k, None)

def factory():
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.project_dir, f.open_findings = Path("."), {}
    f.audit_sent_fingerprints, f.audit_rolled_back_fingerprints = [], []
    return f, m

SCRIPTED = {"where": "route /x", "requirement": "(r)", "problem": "blank page", "fix": "f", "layer": "scripted"}
MODEL = {"where": "/x", "requirement": "r", "problem": "claim", "fix": "f", "layer": "model"}

@case
def main_reopens_a_not_rechecked_finding_once_and_never_more():
    from agent.audit import finding_fingerprint

    f, m = factory()
    lines: list[str] = []
    saved = m.log
    m.log = lines.append
    fp, model_fp = finding_fingerprint(SCRIPTED), finding_fingerprint(MODEL)
    try:
        for rechecked in (None, {"ran": False, "gone": [], "still": []},
                          {"ran": True, "gone": [], "still": [], "not_rechecked": [SCRIPTED]}):
            f.audit_sent_fingerprints, f.open_findings, f.audit_unrechecked_fingerprints = [fp, model_fp], {}, []
            f.reopen_unrechecked("REQ-1", [SCRIPTED, MODEL], rechecked)
            assert f.audit_sent_fingerprints == [model_fp] and fp in f.open_findings, (rechecked, f.audit_sent_fingerprints)
            assert f.audit_unrechecked_fingerprints == [fp] and f.audit_rolled_back_fingerprints == []
        assert any("not settled, open again, may be sent once more" in l for l in lines), lines

        f.audit_sent_fingerprints, f.open_findings = [fp, model_fp], {}
        f.reopen_unrechecked("REQ-2", [SCRIPTED], None)
        assert f.audit_sent_fingerprints == [fp, model_fp] and fp in f.open_findings
        assert any("not re-checked a second time" in l for l in lines), lines

        f.audit_sent_fingerprints, f.open_findings, f.audit_unrechecked_fingerprints = [fp], {}, []
        f.reopen_unrechecked("REQ-3", [SCRIPTED], {"ran": True, "gone": [SCRIPTED], "still": []})
        assert f.audit_sent_fingerprints == [fp] and f.open_findings == {} and f.audit_unrechecked_fingerprints == []
    finally:
        m.log = saved

@case
def a_still_there_finding_reopens_even_with_route_stated_off():
    from agent.audit import finding_fingerprint

    f, m = factory()
    saved = m.log
    m.log = lambda *_: None
    fp = finding_fingerprint(SCRIPTED)
    try:
        with Switch(audit_route_stated=False, audit_settle_rechecked=True):
            f.audit_sent_fingerprints = [fp]
            f.reopen_still_there("REQ-1", {"ran": True, "gone": [], "still": [SCRIPTED]})
            assert f.audit_sent_fingerprints == [], f.audit_sent_fingerprints
        with Switch(audit_route_stated=False, audit_settle_rechecked=False):
            f.audit_sent_fingerprints = [fp]
            f.reopen_still_there("REQ-1", {"ran": True, "gone": [], "still": [SCRIPTED]})
            assert f.audit_sent_fingerprints == [fp]
    finally:
        m.log = saved

@case
def wired_into_the_batch_and_off_is_v180():
    import cases_p141s3 as p3
    from agent.audit import finding_fingerprint

    fp = finding_fingerprint(p3.FINDING)
    for on, sent in ((True, False), (False, True)):
        patch, undo = p3.audit_patch([p3.FINDING], lambda *a, **k: {"ran": False, "gone": [], "still": [], "unchecked": []})
        try:
            f, lines, after, passed, calls = p3.run_debt_batch(
                [{"write": {p3.rb.APP: "OWN-OK"}}, {"write": {p3.rb.APP: "OWN-OK audited"}}],
                settings={"audit_enabled": True, "audit_llm": "never", "audit_settle_rechecked": on}, patch=patch)
        finally:
            undo()
        assert passed and after == "OWN-OK audited", (after, lines)
        assert (fp in f.audit_sent_fingerprints) is sent, (on, f.audit_sent_fingerprints, lines)
        assert any("open again, may be sent once more" in l for l in lines) is on, lines
        assert f.audit_rolled_back_fingerprints == [], f.audit_rolled_back_fingerprints

@case
def the_switch_is_registered_and_on():
    from agent.settings import _DEFAULTS, setting_on

    assert _DEFAULTS.get("audit_settle_rechecked") is True and setting_on("audit_settle_rechecked")

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
    print(f"{len(CASES) - failed}/{len(CASES)} v181 audit-settle cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
