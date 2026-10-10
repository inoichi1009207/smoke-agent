from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NL = chr(10)
CASES = []

def case(function):
    CASES.append(function)
    return function

CSV_CLAIM = ('"await expect(locate(page, { role: "gridcell", name: "B1" }, grid)).toHaveText("");" — the spec\'s own upload '
             'content is `name,note,qty\\n"East, side",,1200\\n...`, and the requirement states "Sheet1 opens with the '
             'complete CSV rows, columns, and original text", so B1 must show `note`; expecting an empty B1 (while C1 is '
             'expected to be `qty`) contradicts the requirement and the very CSV the test uploa')
CSV_SENTENCE = "Sheet1 opens with the complete CSV rows, columns, and original text"
CSV_REQUIREMENT = "Requirement REQ-1-3-1: CSV import" + NL + CSV_SENTENCE + "; the first row remains ordinary data."
CSV_SPEC = {"backend/test-e2e/REQ-1-3-1.spec.js": NL.join([
    'test("REQ-1-3-1: import valid CSV", async ({ page }) => {',
    '  await expect(locate(page, { role: "gridcell", name: "A1" }, grid)).toHaveText("name");',
    '  await expect(locate(page, { role: "gridcell", name: "B1" }, grid)).toHaveText("");',
    '  await expect(locate(page, { role: "gridcell", name: "C1" }, grid)).toHaveText("qty");',
    "});", ""])}
CSV_OUTPUT = NL.join([
    "E2E FAILED against the real application",
    "FAILED SPEC FILES: REQ-1-3-1.spec.js",
    "  1) test-e2e/REQ-1-3-1.spec.js:15:1 › REQ-1-3-1: import valid CSV with quotes, empty fields, Chinese text and line breaks ",
    "    Error: expect(locator).toHaveText(expected) failed",
    "    Locator: getByRole('grid', { name: /^\\s*Worksheet\\s+grid\\s*$/i }).first().getByRole('gridcell', { name: /^\\s*B1\\s*$/i }).filter({ visible: true }).first()",
    "    Timeout: 5000ms",
    '        9 × locator resolved to <div role="gridcell" aria-label="B1" aria-selected="false">note</div>',
    '      25 |   await expect(locate(page, { role: "gridcell", name: "A1" }, grid)).toHaveText("name");',
    '    > 26 |   await expect(locate(page, { role: "gridcell", name: "B1" }, grid)).toHaveText("");',
    '      27 |   await expect(locate(page, { role: "gridcell", name: "C1" }, grid)).toHaveText("qty");', ""])

PASTE_CLAIM = ("The acceptance spec contradicts the requirement text:" + NL
               + 'SPEC: The spec step `await expect(locate(page, { role: "gridcell", name: "H1" })).toHaveText("14");` '
               '(and the following `I1` = `"8"`) contradicts REQ-3-1-2\'s "The system applies the entire rectangle, '
               'preserves empty fields, and overwrites only the target rectangle; formulas within the target are replaced '
               'by the new content" — the pasted rectang')
PASTE_SENTENCE = ("The system applies the entire rectangle, preserves empty fields, and overwrites only the target rectangle; "
                  "formulas within the target are replaced by the new content")
PASTE_REQUIREMENT = "Requirement REQ-3-1-2: Paste" + NL + PASTE_SENTENCE + "."
PASTE_SPEC = {"backend/test-e2e/REQ-3-1-2.spec.js": NL.join([
    'test("REQ-3-1-2: pasting over a cell", async ({ page }) => {',
    '  await expect(locate(page, { role: "gridcell", name: "G1" })).toHaveText("7");',
    '  await expect(locate(page, { role: "gridcell", name: "H1" })).toHaveText("14");',
    '  await expect(locate(page, { role: "gridcell", name: "I1" })).toHaveText("8");',
    "});", ""])}
PASTE_OUTPUT = NL.join([
    "E2E FAILED against the real application",
    "FAILED SPEC FILES: REQ-3-1-2.spec.js",
    "  1) test-e2e/REQ-3-1-2.spec.js:52:1 › REQ-3-1-2: pasting over a cell replaces its content and dependent formulas recalculate ",
    "    Error: expect(locator).toHaveText(expected) failed",
    "    Locator:  getByRole('gridcell', { name: /^\\s*H1\\s*$/i }).filter({ visible: true }).first()",
    "    Timeout:  5000ms",
    '        9 × locator resolved to <div tabindex="0" role="gridcell" aria-label="H1" aria-selected="false">8</div>',
    '      60 |   await expect(locate(page, { role: "gridcell", name: "G1" })).toHaveText("7");',
    '    > 61 |   await expect(locate(page, { role: "gridcell", name: "H1" })).toHaveText("14");',
    '      62 |   await expect(locate(page, { role: "gridcell", name: "I1" })).toHaveText("8");', ""])

RIGHT_HAND_OVERS = (
    ("sheet v144 REQ-1-3-1", CSV_CLAIM, CSV_SENTENCE, CSV_REQUIREMENT, CSV_SPEC, CSV_OUTPUT),
    ("sheet v141 REQ-3-1-2", PASTE_CLAIM, PASTE_SENTENCE, PASTE_REQUIREMENT, PASTE_SPEC, PASTE_OUTPUT),
)

def without_code_frame(output: str) -> str:
    return NL.join(line for line in output.split(NL) if not re.match(r"^\s*>?\s*\d+ \|", line))

@case
def the_right_frame_carried_hand_overs_stay_accepted():
    from agent.codegen import spec_evidence

    for name, claim, _, requirement, spec, output in RIGHT_HAND_OVERS:
        assert spec_evidence(claim, requirement, spec, output), name

@case
def their_requirement_side_is_met_by_the_echoed_code_frame_alone():
    from agent.codegen import evidence_norm, spec_evidence

    for name, claim, sentence, requirement, spec, output in RIGHT_HAND_OVERS:
        stripped = without_code_frame(output)
        assert "| " not in stripped and "Locator:" in stripped, (name, stripped)
        assert not spec_evidence(claim, requirement, spec, stripped), name
        assert not spec_evidence(claim, requirement, spec, ""), name
        assert evidence_norm(sentence) in evidence_norm(claim) and evidence_norm(sentence) in evidence_norm(requirement), name

@case
def the_quoted_requirement_sentence_is_no_quote_today():
    from agent.codegen import claim_quotes, evidence_norm

    for name, claim, sentence, *_ in RIGHT_HAND_OVERS:
        quotes = claim_quotes(claim)
        assert evidence_norm(sentence) not in quotes, (name, quotes)
        assert not any(evidence_norm(sentence)[:40] in q for q in quotes), (name, quotes)

@case
def the_api_test_path_applies_the_same_rule_to_its_echoed_code_frame():
    from agent.codegen import api_test_evidence

    tests = {"backend/tests/REQ-7.test.js": 'test("x", async () => {' + NL
             + '  expect(find(res.body.rows, "East").qty).toBe("800");' + NL + "});" + NL}
    claim = ('"expect(find(res.body.rows, "East").qty).toBe("800");" — the requirement states '
             '"Imported values keep their original text".')
    requirement = "Requirement REQ-7: Import" + NL + "Imported values keep their original text."
    output = ("  ● x" + NL + "    expect(received).toEqual(expected)" + NL
              + '    > 2 |   expect(find(res.body.rows, "East").qty).toBe("800");' + NL)
    assert api_test_evidence(claim, requirement, tests, output) == ["backend/tests/REQ-7.test.js"]
    assert api_test_evidence(claim, requirement, tests, without_code_frame(output)) == []

EXTRA = "frontend/src/lib/lateHelper.ts"

class slow_writes:

    def __init__(self, clock, seconds: float, marker: str):
        self.clock, self.seconds, self.marker = clock, seconds, marker

    def __enter__(self):
        from agent import codegen

        self.real = codegen.write_text

        def write(target, content, *args, **kw):
            if self.marker in content:
                self.clock.advance(self.seconds)
            return self.real(target, content, *args, **kw)

        codegen.write_text = write
        return self

    def __exit__(self, *exc):
        from agent import codegen

        codegen.write_text = self.real
        return False

def late_writer(call_deadline_after: float | None, write_sec: float, logs: list | None = None):
    import tempfile
    from agent.codegen import CodegenImplementer
    import cases_v146b as b

    clock = b.Clock()
    with b.patched(clock), b.State(), slow_writes(clock, write_sec, "late"), tempfile.TemporaryDirectory() as tmp:
        checks: list = []
        model = b.Model([b.block(b.PAGE, "first"), b.block(b.PAGE, "late") + NL + b.block(EXTRA, "export const extra = 1;")],
                        clock=clock, step_sec=100.0)
        implementer = CodegenImplementer(model, Path(tmp), b.recording_check([b.own_red()], checks),
                                         log=(logs.append if logs is not None else (lambda m: None)), max_rounds=1, extra_rounds=0)
        if call_deadline_after is not None:
            implementer.call_deadline = clock.run + call_deadline_after
        result = implementer.implement(b.NODE, b.TESTS, b.own_red())
        page = (Path(tmp) / b.PAGE).read_text(encoding="utf-8").strip() if (Path(tmp) / b.PAGE).is_file() else None
        return result, checks, model.steps(), page, (Path(tmp) / EXTRA).exists()

@case
def r53_8_an_answer_whose_writing_crosses_the_call_deadline_is_put_back_and_not_checked():
    logs: list = []

    result, checks, steps, page, extra = late_writer(350.0, 100.0, logs)
    assert steps == ["gen0", "diagnose1", "repair1"], steps
    assert len(checks) == 1, checks
    assert page == "first" and not extra, (page, extra)
    assert (result.status, result.stop_reason) == ("budget", "deadline"), result
    assert result.written == ["frontend/src/pages/EditorPage.tsx"], result.written
    assert any("the call deadline passed while the answer was written" in line for line in logs), logs

@case
def r53_8_an_answer_written_before_the_call_deadline_is_checked_as_before():
    result, checks, steps, page, extra = late_writer(450.0, 100.0)
    assert len(checks) == 2 and page == "late" and extra, (checks, page, extra)
    assert EXTRA in result.written, result.written

    result, checks, steps, page, extra = late_writer(None, 100000.0)
    assert len(checks) == 2 and page == "late" and extra, (checks, page, extra)

class refusing_put_back:

    def __init__(self, *suffixes: str):
        self.suffixes = suffixes

    def __enter__(self):
        from agent import source_io

        self.real = source_io.put_back

        def put_back(target, before):
            if not self.suffixes or Path(target).as_posix().endswith(self.suffixes):
                raise OSError("locked")
            return self.real(target, before)

        source_io.put_back = put_back
        return self

    def __exit__(self, *exc):
        from agent import source_io

        source_io.put_back = self.real
        return False

@case
def r53_8_r54_4_a_put_back_that_fails_runs_no_check_past_the_deadline():
    logs: list = []
    with refusing_put_back():
        result, checks, steps, page, extra = late_writer(350.0, 100.0, logs)
    assert len(checks) == 1, checks
    assert page == "late" and extra, (page, extra)
    assert (result.status, result.stop_reason) == ("budget", "deadline"), result
    assert result.written == ["frontend/src/pages/EditorPage.tsx", EXTRA], result.written
    assert "could not be put back and stay unchecked" in result.detail, result.detail

DATA = "backend/src/database/init_db.js"
NEW_FRONT = "frontend/src/lib/lateHelper.ts"
NEW_BACK = "backend/src/lib/lateHelper.ts"

def partial_put_back(*failing: str):
    import tempfile
    from agent.codegen import CodegenImplementer
    import cases_v146b as b

    clock = b.Clock()
    with b.patched(clock), b.State(), slow_writes(clock, 100.0, "late"), refusing_put_back(*failing), \
            tempfile.TemporaryDirectory() as tmp:
        checks: list = []
        model = b.Model([b.block(b.PAGE, "first") + NL + b.block(DATA, "module.exports = ['seed one'];"),
                         b.block(DATA, "module.exports = ['late seed two'];") + NL + b.block(NEW_FRONT, "export const late = 1;")
                         + NL + b.block(NEW_BACK, "module.exports = { late: 2 };")], clock=clock, step_sec=100.0)
        implementer = CodegenImplementer(model, Path(tmp), b.recording_check([b.own_red()], checks), log=lambda m: None,
                                         max_rounds=1, extra_rounds=0)
        implementer.call_deadline = clock.run + 350.0
        result = implementer.implement(b.NODE, b.TESTS, b.own_red())
        disk = {rel: (Path(tmp) / rel).read_text(encoding="utf-8").strip() if (Path(tmp) / rel).is_file() else None
                for rel in (b.PAGE, DATA, NEW_FRONT, NEW_BACK)}
        return result, checks, disk

@case
def r54_4_a_partly_failed_put_back_lists_what_stayed_and_runs_no_check():
    result, checks, disk = partial_put_back(NEW_BACK)
    assert len(checks) == 1, checks
    assert disk[DATA] == "module.exports = ['seed one'];", disk
    assert disk[NEW_FRONT] is None and disk[NEW_BACK] is not None, disk
    assert (result.status, result.stop_reason) == ("budget", "deadline"), result

    assert result.written == ["frontend/src/pages/EditorPage.tsx", DATA, NEW_BACK], result.written

    result, checks, disk = partial_put_back(NEW_FRONT)
    assert disk[NEW_FRONT] is not None and disk[NEW_BACK] is None, disk
    assert result.written == ["frontend/src/pages/EditorPage.tsx", DATA, NEW_FRONT], result.written

@case
def r54_4_roll_back_returns_the_full_paths_it_could_not_restore():
    import tempfile
    from agent.codegen import CodegenImplementer

    with tempfile.TemporaryDirectory() as tmp, refusing_put_back(NEW_BACK):
        root = Path(tmp)
        for rel in (NEW_FRONT, NEW_BACK):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("x", encoding="utf-8")
        undo = {root / NEW_FRONT: None, root / NEW_BACK: None}
        logs: list = []
        failed = CodegenImplementer(None, root, lambda: None, log=logs.append)._roll_back(undo, "N", 1)
        assert failed == [root / NEW_BACK], failed
        assert logs and logs[-1].endswith(f"NOT restored: {[NEW_BACK]}"), logs
        assert not (root / NEW_FRONT).exists() and (root / NEW_BACK).exists()

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
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} v147b cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
