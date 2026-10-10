from __future__ import annotations

import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import shared_handle as sh
from agent import stated_route as sr

CASES = []
NL = chr(10)

def case(function):
    CASES.append(function)
    return function

ROUTE_FINDING = {"where": "route /", "problem": "\u201cSign in\u201d is rendered here as a button, and no link of that name exists on "
                 "the page; the requirement states a link, so whoever looks for the link named \u201cSign in\u201d finds nothing"}
STAGE_FINDING = {"where": "components/AppHeader", "problem": "components/AppHeader.tsx:70 renders 'Sign in' as a <button> whose "
                 "click only navigates; it is not a link, so following the link 'Sign in' finds nothing"}
OTHER_FINDING = {"where": "frontend/src/components", "problem": "Page component CommitPage renders two searchbox controls with the "
                 "same accessible name \"Search\" at the same time"}
TEXT_FINDING = {"where": "route /x", "problem": "the requirement states the message \u201cName already exists\u201d, the page shows \u201cDuplicate\u201d"}

HELPER_SPEC = NL.join([
    'const { click, fill, see, locateAll, expect } = require("../e2e-support/outsider");',
    "async function signIn(page) {",
    '  await page.goto("/");',
    '  await click(page, { role: "button", name: "Sign in" });',
    '  await fill(page, "Username or email", "file-contributor");',
    '  await fill(page, "Password", "Valid-password-123!");',
    '  await click(page, { role: "button", name: "Sign in" });',
    "}",
    'test("REQ-4-4: x - Scenario 1", async ({ page }) => {',
    '  await expect(locateAll(page, { role: "button", name: "Sign in" })).toHaveCount(1);',
    "  await signIn(page);",
    "});", ""])
ROLLBACK_OUTPUT = NL.join([
    "FAILED SPEC FILES: REQ-4-4.spec.js",
    "  1) [chromium] \u203a REQ-4-4.spec.js:9:1 \u203a REQ-4-4: x - Scenario 1 \u2500\u2500\u2500",
    "    Error: An outsider who only read the requirement could not find anything to click: button named Sign in. Near: no button has it",
    "      at signIn (C:/app/backend/test-e2e/REQ-4-4.spec.js:4:3)", ""])

@case
def stated_control_findings_are_read_and_others_are_not():
    assert sr.stated_control(ROUTE_FINDING) == ("Sign in", "link", "button")
    assert sr.stated_control(STAGE_FINDING) == ("Sign in", "link", "button")
    assert sr.stated_control(OTHER_FINDING) is None and sr.stated_control(TEXT_FINDING) is None
    assert sr.stated_control({"problem": "\u201cSave\u201d is rendered here as a button; the requirement states a button"}) is None
    assert sr.quotes_requirement(TEXT_FINDING) and not sr.quotes_requirement(OTHER_FINDING)

@case
def only_the_header_lines_are_routed_never_the_form_submit():
    failing = sr.failing_lines(ROLLBACK_OUTPUT, "REQ-4-4.spec.js")
    assert failing == {9, 4}, failing
    assert sr.names_control(ROLLBACK_OUTPUT, "Sign in", "button")
    assert sr.contradicting_lines(HELPER_SPEC, "Sign in", "button") == [4, 7, 10]
    lines = sr.route_lines(HELPER_SPEC, "Sign in", "button", failing)
    assert lines == [4, 10], lines
    new = sr.rewrite_role(HELPER_SPEC, lines, "Sign in", "button", "link").split(NL)
    old = HELPER_SPEC.split(NL)
    assert [i + 1 for i, (a, b) in enumerate(zip(old, new)) if a != b] == [4, 10]
    assert 'role: "link", name: "Sign in"' in new[3] and 'role: "button", name: "Sign in"' in new[6]
    other = 'await page.getByRole("button", { name: "Sign in" }).click();'
    assert sr.rewrite_role(other, [1], "Sign in", "button", "link") == 'await page.getByRole("link", { name: "Sign in" }).click();'

def factory(root: Path):
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.project_dir, f.revised_specs, f.test_snapshot, f.open_findings = root, set(), {}, {}
    f.audit_sent_fingerprints, f.audit_rolled_back_fingerprints = [], []
    return f, m

@case
def a_finding_at_the_cap_gets_its_spec_fixed_and_one_more_send():
    from agent.audit import finding_fingerprint

    tmp = Path(tempfile.mkdtemp())
    try:
        spec = tmp / "backend" / "test-e2e" / "REQ-4-4.spec.js"
        spec.parent.mkdir(parents=True)
        spec.write_text(HELPER_SPEC, encoding="utf-8")
        f, m = factory(tmp)
        f._frozen_inherited_specs = {"backend/test-e2e/REQ-4-4.spec.js": HELPER_SPEC}
        f.evolution = {"active": True}
        real = m.static_test_defects
        m.static_test_defects = lambda project_dir, files, **k: ""
        try:
            findings = [ROUTE_FINDING, STAGE_FINDING, OTHER_FINDING]
            for _ in range(2):
                f.note_rollback_evidence("REQ-5-1-1", findings, ROLLBACK_OUTPUT)
            prints = [finding_fingerprint(x) for x in findings]
            left = f.route_given_up_findings("REQ-5-1-2", findings, prints)
            assert left == [prints[2]], left
            text = spec.read_text(encoding="utf-8")
            assert text.count('role: "link", name: "Sign in"') == 2 and text.count('role: "button", name: "Sign in"') == 1
            assert f._frozen_inherited_specs["backend/test-e2e/REQ-4-4.spec.js"] == text
            assert f.audit_routed[prints[0]]["status"] == "spec fixed, sent once more"
            again = f.route_given_up_findings("REQ-5-2-1", findings, prints)
            assert again == prints, again
        finally:
            m.static_test_defects = real
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

@case
def a_rewrite_that_does_not_parse_goes_back_and_text_findings_stay_open():
    from agent.audit import finding_fingerprint

    tmp = Path(tempfile.mkdtemp())
    try:
        spec = tmp / "backend" / "test-e2e" / "REQ-4-4.spec.js"
        spec.parent.mkdir(parents=True)
        spec.write_text(HELPER_SPEC, encoding="utf-8")
        f, m = factory(tmp)
        f.test_snapshot = {"backend/test-e2e/REQ-4-4.spec.js": HELPER_SPEC}
        real = m.static_test_defects
        m.static_test_defects = lambda project_dir, files, **k: "SyntaxError"
        try:
            f.note_rollback_evidence("REQ-1", [ROUTE_FINDING, TEXT_FINDING], ROLLBACK_OUTPUT)
            prints = [finding_fingerprint(ROUTE_FINDING), finding_fingerprint(TEXT_FINDING)]
            f.open_findings = {p: {"class": "x", "where": "y", "batch": "REQ-1"} for p in prints}
            left = f.route_given_up_findings("REQ-2", [ROUTE_FINDING, TEXT_FINDING], prints)
            assert left == prints and spec.read_text(encoding="utf-8") == HELPER_SPEC
            assert f.test_snapshot["backend/test-e2e/REQ-4-4.spec.js"] == HELPER_SPEC
            assert f.audit_routed[prints[1]]["status"].startswith("open") and f.open_findings[prints[1]]["route"].startswith("open")
            assert f.audit_routed[prints[0]]["status"] == "open: spec fix put back (does not parse)"
        finally:
            m.static_test_defects = real
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

@case
def a_kept_repair_that_did_not_remove_the_finding_reopens_it():
    from agent.audit import finding_fingerprint

    tmp = Path(tempfile.mkdtemp())
    try:
        f, _ = factory(tmp)
        fp = finding_fingerprint(OTHER_FINDING)
        f.audit_sent_fingerprints = [fp, "other"]
        f.reopen_still_there("REQ-1", {"ran": True, "gone": [], "still": [OTHER_FINDING]})
        assert f.audit_sent_fingerprints == ["other"] and f.audit_rolled_back_fingerprints == [fp] and fp in f.open_findings
        f.reopen_still_there("REQ-1", {"ran": False, "still": [OTHER_FINDING]})
        assert f.audit_rolled_back_fingerprints == [fp]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

RENAMER = NL.join(['test("REQ-1-2-2: rename", async ({ page }) => {', '  await click(page, "Q3 Sales");',
                   '  await click(page, { role: "button", name: "Rename workbook" });', '  await fill(page, "Workbook name", "Q4");', "});"])
READER = NL.join(['test("REQ-2-1-1: sheets", async ({ page }) => {', '  await click(page, "Q3 Sales");',
                  '  await see(page, "Sheet1");', "});"])
COUNTER = NL.join(['test("REQ-9: list", async ({ page }) => {', '  await expect(locateAll(page, "row")).toHaveCount(3);', "});"])
CREATOR = NL.join(['test("REQ-8: other", async ({ page }) => {', '  await click(page, "Projects");', "});"])

@case
def the_classification_follows_the_evidence():
    kind, why = sh.classify("Error: The requirement says Q3 Sales is shown, but nothing visible matches.", READER, RENAMER)
    assert kind == sh.POLLUTION and "Q3 Sales" in why, (kind, why)
    kind, _ = sh.classify("Error: expect(locator).toHaveCount(expected) failed | Expected: 3 | Received: 5", COUNTER, CREATOR)
    assert kind == sh.LEAK
    assert sh.classify("", READER, RENAMER)[0] == sh.UNKNOWN
    assert sh.classify("Error: timeout", READER, CREATOR)[0] == sh.UNKNOWN
    output = NL.join(["  1) [chromium] \u203a REQ-2-1-1.spec.js:1:1 \u203a REQ-2-1-1: sheets", "    Error: The requirement says Sheet1 is shown"])
    assert "Sheet1 is shown" in sh.first_error(output, "REQ-2-1-1.spec.js")
    assert "ONE database" in sh.spec_problem("A", "B", "e", "x") and "Fix the application" in sh.app_problem("A", "B", "e", "x")

class _Run:
    def __init__(self, ok, output="", skipped=False):
        self.ok, self.output, self.skipped = ok, output, skipped

@case
def the_sweep_reds_are_replayed_classified_and_repaired_or_booked():
    import main as m

    tmp = Path(tempfile.mkdtemp())
    saved = {k: getattr(m, k) for k in ("run_e2e",)}
    try:
        folder = tmp / "backend" / "test-e2e"
        folder.mkdir(parents=True)
        (folder / "REQ-1-2-2.spec.js").write_text(RENAMER, encoding="utf-8")
        (folder / "REQ-2-1-1.spec.js").write_text(READER, encoding="utf-8")
        (folder / "REQ-7.spec.js").write_text(READER.replace("REQ-2-1-1", "REQ-7"), encoding="utf-8")
        f, _ = factory(tmp)
        S, P, I = ("backend/test-e2e/REQ-2-1-1.spec.js", "backend/test-e2e/REQ-1-2-2.spec.js", "backend/test-e2e/REQ-7.spec.js")
        f.e2e_specs, f.green_specs, f.verify_repairs, f.report = [P, S, I], [P], 0, {"verification": {}}
        f.spec_context = {S: {"node": {"id": "REQ-2-1-1"}, "tests": {S: READER}, "members": ["REQ-2-1-1"], "contract": ""}}
        f._frozen_inherited_specs = {I: READER}
        f.hard_end = lambda: None
        f.suite_est = lambda n: 10.0
        f.shared_context_sweep = lambda last: f.report.__setitem__("shared_sweep", {"red_only_after_earlier": [
            {"spec": "REQ-2-1-1.spec.js", "earlier": ["REQ-1-2-2.spec.js"]}, {"spec": "REQ-7.spec.js", "earlier": ["REQ-1-2-2.spec.js"]}]})
        red = ("  1) [chromium] \u203a {n}:1:1 \u203a x" + NL + "    Error: The requirement says Q3 Sales is shown, but nothing visible matches." + NL
               + "FAILED SPEC FILES: {n}")
        calls = []

        def fake_e2e(project_dir, specs, log=print, shared_data=False, until=None):
            calls.append((tuple(Path(s).name for s in specs), shared_data))
            name = Path(specs[-1]).name
            fixed = "own records" in (project_dir / specs[-1]).read_text(encoding="utf-8")
            if len(specs) == 2 and shared_data and not fixed:
                return _Run(False, red.format(n=name))
            return _Run(True, "passed")

        m.run_e2e = fake_e2e
        f.repair_tests = lambda node, prefix, files, problem, contract="": [m.AuthoredFile(path=S, content=READER + NL + "// own records")]
        f.calls_end_by = lambda end: __import__("contextlib").nullcontext()
        kept = f.shared_sweep_handle(_Run(True))
        handled = {h["spec"]: h for h in f.report["shared_sweep"]["handled"]}
        assert kept == 1 and f.verify_repairs == 1, (kept, handled)
        assert handled["REQ-2-1-1.spec.js"]["kind"] == sh.POLLUTION and handled["REQ-2-1-1.spec.js"]["disposition"] == "repaired (spec)"
        assert handled["REQ-2-1-1.spec.js"]["culprit"] == "REQ-1-2-2.spec.js" and S in f.revised_specs
        assert handled["REQ-7.spec.js"]["disposition"] == "booked" and "inherited" in handled["REQ-7.spec.js"]["why"]
        assert (folder / "REQ-7.spec.js").read_text(encoding="utf-8") == READER.replace("REQ-2-1-1", "REQ-7")
        assert (("REQ-1-2-2.spec.js", "REQ-2-1-1.spec.js"), True) in calls
    finally:
        for k, v in saved.items():
            setattr(m, k, v)
        shutil.rmtree(tmp, ignore_errors=True)

@case
def the_handled_sweep_runs_before_the_final_judgement():
    import inspect

    import main as m

    verify = inspect.getsource(m.Factory.verify)
    assert verify.index("self.shared_sweep_handle(e2e)") < verify.index("judging the delivered state once more")
    assert verify.index("self.shared_sweep_handle(e2e)") > verify.index("closing repair finished")
    gate = inspect.getsource(m.Factory.process_node)
    assert gate.index("self.route_given_up_findings(") < gate.index("select_for_repair(audit_findings")

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            traceback.print_exc()
            print(f"FAIL  {function.__name__}")
    print(f"{len(CASES) - failed}/{len(CASES)} v160b route/sweep cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
