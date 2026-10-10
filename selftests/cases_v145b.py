from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NL = chr(10)
CASES = []
PAGE = "frontend/src/pages/EditorPage.tsx"
SEED = "backend/src/database/init_db.js"
SPEC = "backend/test-e2e/REQ-9.spec.js"
API = "backend/tests/REQ-9.test.js"
PLAN = PAGE + " -- render what the spec looks for"
HEADING = "A previous attempt at this batch ended red"

def case(function):
    CASES.append(function)
    return function

def block(path: str, body: str) -> str:
    return "=== FILE: " + path + " ===" + NL + body + NL + "=== END FILE ==="

def outcome(passed: bool, text: str):
    from agent.testrun import TestOutcome

    return TestOutcome(passed, text)

def own_red(error: str = "Error: expect(locator).toHaveAttribute(expected) failed") -> object:
    return outcome(False, "E2E FAILED against the real application" + NL + "FAILED SPEC FILES: REQ-9.spec.js" + NL
                   + "  1) test-e2e/REQ-9.spec.js:62:1 › REQ-9: failed addition shows an error and no new tab" + NL
                   + "    " + error + NL
                   + '    > 74 |   await expect(tab("Sheet1")).toHaveAttribute("aria-selected", "false");' + NL)

def earlier_red() -> object:
    return outcome(False, "E2E FAILED against the real application" + NL
                   + "This batch's own spec PASSES. What fails are specs of EARLIER requirements, which passed before this batch "
                   "changed the files they depend on: backend/test-e2e/REQ-1.spec.js. This batch broke that behaviour." + NL
                   + "FAILED SPEC FILES: REQ-1.spec.js" + NL
                   + "  1) test-e2e/REQ-1.spec.js:5:1 › REQ-1: open workbook, editor state" + NL
                   + "    Error: expect(locator).toHaveAttribute(expected) failed" + NL
                   + '    > 13 |   await expect(tab("Sheet1")).toHaveAttribute("aria-selected", "true");' + NL)

def green():
    return outcome(True, "E2E PASSED")

class Model:

    def __init__(self, answers):
        self.answers = list(answers)
        self.writes = 0
        self.calls: list[tuple[str, dict, str]] = []
        self.last_finish_reason, self.model = "stop", "glm-test"

    def chat(self, messages, **options):
        tag = str(options.get("tag", ""))
        step = tag.split(":", 2)[2] if tag.count(":") >= 2 else tag
        self.calls.append((step, dict(options), str(messages[-1]["content"])))
        assert len(self.calls) <= 60, [c[0] for c in self.calls]
        self.last_finish_reason = "stop"
        if ":diagnose" in tag or tag.endswith(":decide"):
            return {"content": PLAN}
        answer = self.answers[min(self.writes, len(self.answers) - 1)]
        self.writes += 1
        return {"content": answer}

    def prompts(self, prefix: str) -> list[str]:
        return [prompt for step, _options, prompt in self.calls if step.startswith(prefix)]

class State:

    def __enter__(self):
        from agent.codegen import CodegenImplementer
        from agent.settings import SETTINGS

        self.batches = set(CodegenImplementer.all_thinking_batches)
        self.broken = CodegenImplementer.repair_mode_broken
        self.settings = {k: SETTINGS.get(k) for k in ("implementer_thinking", "repair_thinking", "repair_diagnosis")}
        CodegenImplementer.repair_mode_broken = False
        SETTINGS.update(implementer_thinking=True, repair_thinking=False, repair_diagnosis=True)
        return self

    def __exit__(self, *exc):
        from agent.codegen import CodegenImplementer
        from agent.settings import SETTINGS

        CodegenImplementer.all_thinking_batches.clear()
        CodegenImplementer.all_thinking_batches.update(self.batches)
        CodegenImplementer.repair_mode_broken = self.broken
        for k, v in self.settings.items():
            if v is None:
                SETTINGS.pop(k, None)
            else:
                SETTINGS[k] = v
        return False

NODE = {"id": "REQ-9", "name": "Export CSV",
        "description": "Users can export the active worksheet. The exported CSV preserves empty cells within the used range "
                       "according to the grid’s actual row and column order and correctly escapes text containing commas, "
                       "quotes, or line breaks."}
TESTS = {
    SPEC: 'const { test } = require("@playwright/test");' + NL + 'test("REQ-9: failed addition shows an error and no new tab", async ({ page }) => {'
          + NL + '  await expect(tab("Sheet1")).toHaveAttribute("aria-selected", "false");' + NL + "});" + NL,

    API: "const res = await request(app).get('/api/export');" + NL + r"const firstLine = res.text.split(/\r?\n/)[0];" + NL
         + r"""expect(firstLine).toContain('"line1\nline2"');""" + NL,
}

def make(model, project: Path, outcomes, logs=None, **kw):
    from agent.codegen import CodegenImplementer

    pending = list(outcomes)

    def check():
        return pending.pop(0) if len(pending) > 1 else pending[0]

    kw.setdefault("max_rounds", 6)
    kw.setdefault("extra_rounds", 0)
    return CodegenImplementer(model, project, check, log=(logs.append if logs is not None else (lambda m: None)), **kw)

@case
def the_attempt_note_is_shown_once_in_every_generation_repair_and_diagnosis_prompt():
    with State(), tempfile.TemporaryDirectory() as tmp:
        model = Model([block(PAGE, "export default function P() { return null; }")])
        implementer = make(model, Path(tmp), [own_red(), green()])
        implementer.attempt_note = "The first attempt rewrote frontend/src/components/Layout.tsx (1476 chars) and the home spec went red."
        result = implementer.implement(NODE, TESTS, own_red())
        assert result.status == "passed", result
        for prefix in ("gen0", "diagnose1", "repair1"):
            prompts = model.prompts(prefix)
            assert prompts, (prefix, [c[0] for c in model.calls])
            for prompt in prompts:
                assert prompt.count(HEADING) == 1, (prefix, prompt.count(HEADING))
                assert "Do not repeat what failed" in prompt and "Layout.tsx (1476 chars)" in prompt

@case
def without_an_attempt_note_the_prompts_have_no_heading():
    with State(), tempfile.TemporaryDirectory() as tmp:
        model = Model([block(PAGE, "x")])
        implementer = make(model, Path(tmp), [own_red(), green()])
        assert implementer.attempt_note == "" and implementer.temperature is None
        assert implementer.implement(NODE, TESTS, own_red()).status == "passed"
        assert model.calls and all(HEADING not in prompt for _s, _o, prompt in model.calls)

@case
def a_long_attempt_note_is_capped():
    from agent.codegen import ATTEMPT_NOTE_CHARS

    with State(), tempfile.TemporaryDirectory() as tmp:
        model = Model([block(PAGE, "x")])
        implementer = make(model, Path(tmp), [green()])
        implementer.attempt_note = "A" * (ATTEMPT_NOTE_CHARS * 3)
        assert implementer.implement(NODE, TESTS, own_red()).status == "passed"
        prompt = model.prompts("gen0")[0]
        assert ATTEMPT_NOTE_CHARS <= prompt.count("A" * 10) * 10 + 10 and "A" * (ATTEMPT_NOTE_CHARS + 1) not in prompt
        assert "rest of the note omitted" in prompt

@case
def a_set_temperature_reaches_every_model_call_of_that_implementer():
    with State(), tempfile.TemporaryDirectory() as tmp:
        model = Model([block(PAGE, "x")])
        implementer = make(model, Path(tmp), [own_red(), green()])
        implementer.temperature = 0.7
        assert implementer.implement(NODE, TESTS, own_red()).status == "passed"
        implementer._conclude("REQ-9", "analysis " * 20, own_red())
        steps = [step for step, _o, _p in model.calls]
        assert {"gen0", "diagnose1", "repair1", "decide"} <= set(steps), steps
        assert all(options.get("temperature") == 0.7 for _s, options, _p in model.calls), [(s, o.get("temperature")) for s, o, _p in model.calls]

@case
def without_a_temperature_no_call_passes_one_and_the_client_default_stays_zero():
    import inspect

    from agent.llm import LLM

    assert inspect.signature(LLM.chat).parameters["temperature"].default == 0.0
    with State(), tempfile.TemporaryDirectory() as tmp:
        model = Model([block(PAGE, "x")])
        implementer = make(model, Path(tmp), [own_red(), green()])
        assert implementer.implement(NODE, TESTS, own_red()).status == "passed"
        implementer._conclude("REQ-9", "analysis " * 20, own_red())
        assert all("temperature" not in options for _s, options, _p in model.calls), [c[0] for c in model.calls]

@case
def stop_reason_deadline():
    from agent import clock

    with State(), tempfile.TemporaryDirectory() as tmp:
        result = make(Model([block(PAGE, "x")]), Path(tmp), [own_red()], deadline=clock.now() - 5).implement(NODE, TESTS, own_red())
        assert (result.status, result.stop_reason) == ("budget", "deadline"), result

@case
def stop_reason_gave_up():
    with State(), tempfile.TemporaryDirectory() as tmp:
        result = make(Model(["no files, sorry"]), Path(tmp), [own_red()], max_rounds=0).implement(NODE, TESTS, own_red())
        assert (result.status, result.stop_reason) == ("gave_up", "gave_up"), result

@case
def stop_reason_rounds_when_the_failures_keep_changing():
    with State(), tempfile.TemporaryDirectory() as tmp:
        errors = [own_red(f"Error: step {n} failed") for n in range(6)]
        result = make(Model([block(PAGE, "x")]), Path(tmp), errors, max_rounds=2).implement(NODE, TESTS, own_red())
        assert (result.status, result.stop_reason) == ("budget", "rounds"), result

@case
def stop_reason_stuck_when_one_test_keeps_its_first_error():
    with State(), tempfile.TemporaryDirectory() as tmp:
        logs: list[str] = []
        result = make(Model([block(PAGE, "x")]), Path(tmp), [own_red()], logs=logs, max_rounds=2).implement(NODE, TESTS, own_red())
        assert (result.status, result.stop_reason) == ("budget", "stuck"), (result, logs[-5:])

@case
def a_green_or_test_defect_result_has_no_stop_reason():
    with State(), tempfile.TemporaryDirectory() as tmp:
        assert make(Model([block(PAGE, "x")]), Path(tmp), [green()]).implement(NODE, TESTS, own_red()).stop_reason == ""

API_CLAIM = (r'"const firstLine = res.text.split(/\r?\n/)[0];" cuts the exported CSV at the line break inside a quoted cell, '
             'while the requirement says the export "correctly escapes text containing commas, quotes, or line breaks"')

def defect(text: str) -> str:
    return "=== TEST DEFECT ===" + NL + text + NL + "=== END TEST DEFECT ==="

@case
def a_test_defect_quoting_the_batchs_api_test_is_accepted_for_that_file_only():
    from agent.codegen import api_test_evidence, spec_evidence
    from agent.test_author import describe_node

    assert not spec_evidence(API_CLAIM, describe_node(NODE), TESTS, own_red().output)
    assert api_test_evidence(API_CLAIM, describe_node(NODE), TESTS, own_red().output) == [API]
    with State(), tempfile.TemporaryDirectory() as tmp:
        logs: list[str] = []
        result = make(Model([defect(API_CLAIM + NL + "SPEC: REQ-9.spec.js is fine")]), Path(tmp), [own_red()], logs=logs).implement(NODE, TESTS, own_red())
        assert (result.status, result.defect_kind, result.defect_files) == ("test_defect", "api", [API]), result

        import re

        assert "REQ-9.test.js" in result.detail and "REQ-9.spec.js" not in result.detail, result.detail
        assert re.search(r"(?m)^(?:The acceptance spec contradicts the requirement text:|\s*SPEC:)", result.detail) is None, result.detail
        assert all("contradict" not in line for line in logs), logs

@case
def the_api_quote_must_be_in_one_file_and_the_other_side_unchanged():
    from agent.codegen import api_test_evidence
    from agent.test_author import describe_node

    requirement = describe_node(NODE)

    split = {SPEC: TESTS[SPEC], "backend/tests/a.test.js": "const firstLine =", "backend/tests/b.test.js": r"res.text.split(/\r?\n/)[0];"}
    claim = '"const firstLine = res.text" cuts the cell, the requirement says "correctly escapes text containing commas"'
    assert api_test_evidence(claim, requirement, split, "") == []
    assert api_test_evidence(claim, requirement, {**split, "backend/tests/c.test.js": "const firstLine = res.text"}, "") == ["backend/tests/c.test.js"]

    assert api_test_evidence('"expect(firstLine).toContain(" is wrong because "the grid is square"', requirement, TESTS, "") == []

    moved = {SPEC: TESTS[SPEC], "backend/src/REQ-9.test.js": TESTS[API]}
    assert api_test_evidence(API_CLAIM, requirement, moved, "") == []
    with State(), tempfile.TemporaryDirectory() as tmp:
        logs: list[str] = []
        result = make(Model([defect('"const nothing = 1" contradicts "the grid"'), block(PAGE, "x")]), Path(tmp), [green()], logs=logs).implement(NODE, TESTS, own_red())
        assert result.status == "passed" and any("without verifiable quotes is not accepted" in line for line in logs), (result, logs)

@case
def a_defect_proven_against_the_acceptance_spec_keeps_its_v144_path():
    claim = ('"await expect(tab(" contradicts "The exported CSV preserves empty cells within the used range"')
    with State(), tempfile.TemporaryDirectory() as tmp:
        result = make(Model([defect(claim)]), Path(tmp), [own_red()]).implement(NODE, TESTS, own_red())
        assert (result.status, result.defect_kind, result.defect_files, result.detail) == ("test_defect", "", [], claim), result

def seed_answer(n: int) -> str:
    return block(SEED, f"// seed version {n}" + NL + "module.exports = {};")

SHEET_ANSWERS = [block("backend/src/routes/sheets.js", "x"), seed_answer(1), seed_answer(0), block(PAGE, "x"), seed_answer(1), seed_answer(0), seed_answer(1)]
SHEET_CHECKS = [own_red(), earlier_red(), own_red(), own_red(), earlier_red(), own_red(), earlier_red()]

def seeded(tmp: str) -> Path:
    project = Path(tmp)
    (project / SEED).parent.mkdir(parents=True, exist_ok=True)
    (project / SEED).write_text(f"// seed version 0{NL}module.exports = {{}};{NL}", encoding="utf-8")
    return project

@case
def the_third_shared_data_flip_gives_one_note_and_a_further_change_stops_seed_conflict():
    with State(), tempfile.TemporaryDirectory() as tmp:
        logs: list[str] = []
        model = Model(SHEET_ANSWERS)
        result = make(model, seeded(tmp), SHEET_CHECKS, logs=logs).implement(NODE, TESTS, own_red())
        assert (result.status, result.stop_reason) == ("budget", "seed-conflict"), (result, logs[-6:])
        assert result.test_runs == 6, result
        noted = [step for step, _o, prompt in model.calls if "SHARED DATA:" in prompt]
        assert noted == ["diagnose5", "repair5"], noted
        prompt = model.prompts("repair5")[0]
        assert prompt.count("SHARED DATA:") == 1 and SEED in prompt and "TEST DEFECT" in prompt and "contradicts" in prompt
        assert sum("(shared-data flip " in line for line in logs) == 4, logs
        assert sum("back to an earlier content" in line for line in logs if "(shared-data flip " in line) == 3, logs
        assert all("contradict" not in line for line in logs), [line for line in logs if "contradict" in line]

@case
def a_test_defect_answered_under_the_note_goes_to_the_spec_author_marked_seed_conflict():
    claim = '"toHaveAttribute(" in my spec and the earlier spec\'s "await expect(tab(" contradicts it; "aria-selected" too'
    with State(), tempfile.TemporaryDirectory() as tmp:
        answers = SHEET_ANSWERS[:5] + [defect(claim)]
        result = make(Model(answers), seeded(tmp), SHEET_CHECKS).implement(NODE, TESTS, own_red())
        assert (result.status, result.defect_kind) == ("test_defect", "seed-conflict"), result
        assert "REQ-9.spec.js" in result.detail and SEED in result.detail and "contradict" not in result.detail.split(claim)[0], result.detail

@case
def the_chance_round_that_leaves_the_data_alone_keeps_its_rounds():
    with State(), tempfile.TemporaryDirectory() as tmp:
        answers = SHEET_ANSWERS[:5] + [block(PAGE, "y"), block(PAGE, "z")]
        checks = SHEET_CHECKS[:5] + [own_red("Error: another step"), green()]
        result = make(Model(answers), seeded(tmp), checks).implement(NODE, TESTS, own_red())
        assert (result.status, result.stop_reason) == ("passed", ""), result

def seed_and_page(seed_version: int, n: int) -> str:
    return seed_answer(seed_version) + NL + block(PAGE, f"page {n}")

PROBE_CHECKS = [earlier_red(), own_red(), earlier_red(), own_red("Error: a new assertion"), own_red("Error: another new one"),
                own_red("Error: a third one"), green()]

@case
def the_same_seed_content_every_round_is_no_flip_and_never_stops():
    with State(), tempfile.TemporaryDirectory() as tmp:
        logs: list[str] = []
        model = Model([seed_and_page(7, n) for n in range(8)])
        result = make(model, seeded(tmp), PROBE_CHECKS, logs=logs).implement(NODE, TESTS, own_red())
        assert (result.status, result.stop_reason) == ("passed", ""), (result, logs[-6:])
        assert not any("SHARED DATA:" in p for _s, _o, p in model.calls)
        assert sum("(shared-data flip " in line for line in logs) == 1, logs

@case
def the_same_seed_content_from_an_empty_project_is_no_flip_either():
    with State(), tempfile.TemporaryDirectory() as tmp:
        model = Model([seed_and_page(7, n) for n in range(8)])
        result = make(model, Path(tmp), PROBE_CHECKS).implement(NODE, TESTS, own_red())
        assert (result.status, result.stop_reason) == ("passed", ""), result
        assert not any("SHARED DATA:" in p for _s, _o, p in model.calls)

@case
def a_reindented_copy_of_the_seed_is_the_same_content():
    from agent.codegen import seed_hash

    assert seed_hash("a  = 1;\r\n\r\n  b = 2;\n") == seed_hash("a = 1;\nb = 2;") != seed_hash("a = 1; b = 3;")
    assert seed_hash(None) == "" and seed_hash(b"a = 1;") == seed_hash("a = 1;")

@case
def flips_with_new_seed_contents_every_time_get_the_hint_once_and_no_stop():

    answers = [block("backend/src/routes/sheets.js", "x"), seed_answer(1), seed_answer(2), block(PAGE, "x"), seed_answer(4),
               seed_answer(5), seed_answer(6), seed_answer(8)]
    checks = SHEET_CHECKS + [green()]
    with State(), tempfile.TemporaryDirectory() as tmp:
        logs: list[str] = []
        model = Model(answers)
        result = make(model, seeded(tmp), checks, logs=logs, max_rounds=8).implement(NODE, TESTS, own_red())
        assert result.stop_reason != "seed-conflict" and result.status == "passed", (result, logs[-6:])
        noted = [step for step, _o, prompt in model.calls if "SHARED DATA:" in prompt]
        assert noted == ["diagnose5", "repair5"], noted
        prompt = model.prompts("repair5")[0]
        assert "add what this batch needs next to it" in prompt and "Do not change" not in prompt and "TEST DEFECT block" not in prompt
    claim = '"toHaveAttribute(" in my spec and the earlier spec\'s "await expect(tab(" contradicts it; "aria-selected" too'
    with State(), tempfile.TemporaryDirectory() as tmp:
        result = make(Model(answers[:5] + [defect(claim)]), seeded(tmp), checks).implement(NODE, TESTS, own_red())
        assert result.status == "test_defect" and result.defect_kind == "" and "SHARED DATA:" not in result.detail, result

@case
def a_real_back_and_forth_whose_failures_move_on_is_not_stopped():

    checks = SHEET_CHECKS[:5] + [own_red("Error: a new assertion after the note"), green()]
    with State(), tempfile.TemporaryDirectory() as tmp:
        logs: list[str] = []
        model = Model(SHEET_ANSWERS)
        result = make(model, seeded(tmp), checks, logs=logs).implement(NODE, TESTS, own_red())
        assert (result.status, result.stop_reason) == ("passed", ""), (result, logs[-6:])
        assert [step for step, _o, prompt in model.calls if "SHARED DATA:" in prompt] == ["diagnose5", "repair5"]

@case
def after_the_note_a_new_seed_content_does_not_stop_but_a_return_does():

    answers = SHEET_ANSWERS[:5] + [seed_answer(9), seed_answer(1), seed_answer(0)]
    checks = SHEET_CHECKS[:5] + [own_red(), earlier_red(), own_red()]
    with State(), tempfile.TemporaryDirectory() as tmp:
        logs: list[str] = []
        result = make(Model(answers), seeded(tmp), checks, logs=logs).implement(NODE, TESTS, own_red())
        assert (result.status, result.stop_reason, result.test_runs) == ("budget", "seed-conflict", 7), (result, logs[-6:])

@case
def flips_by_rounds_that_write_no_shared_data_file_arm_nothing():
    with State(), tempfile.TemporaryDirectory() as tmp:
        answers = [block(PAGE, f"v{n}") for n in range(8)]
        model = Model(answers)
        result = make(model, Path(tmp), SHEET_CHECKS).implement(NODE, TESTS, own_red())
        assert result.stop_reason != "seed-conflict" and not any("SHARED DATA:" in p for _s, _o, p in model.calls), result

@case
def two_flips_are_not_enough():
    with State(), tempfile.TemporaryDirectory() as tmp:
        answers = [seed_answer(1), seed_answer(2), block(PAGE, "a"), block(PAGE, "b"), block(PAGE, "c")]
        checks = [earlier_red(), own_red(), own_red("Error: b"), own_red("Error: c"), green()]
        model = Model(answers)
        result = make(model, Path(tmp), checks).implement(NODE, TESTS, own_red())
        assert result.status == "passed" and not any("SHARED DATA:" in p for _s, _o, p in model.calls), result

@case
def the_shared_data_pattern_is_generic():
    from agent.codegen import SHARED_DATA_FILE

    for path in (SEED, "backend/src/database/seeds/workbooks.js", "backend/src/database/seed_sheets.js", "backend/data/store.json",
                 "backend/src/fixtures/users.json", "backend/src/lib/seedData.js"):
        assert SHARED_DATA_FILE.search(path), path
    for path in (PAGE, "backend/src/routes/sheets.js", "backend/src/app.js", "frontend/src/api/index.ts"):
        assert not SHARED_DATA_FILE.search(path), path

@case
def after_the_hint_one_return_is_not_yet_a_stop():

    answers = [block("backend/src/routes/sheets.js", "x"), seed_answer(1), seed_answer(2), seed_answer(3), seed_answer(2), block(PAGE, "x")]
    checks = [own_red(), earlier_red(), own_red(), earlier_red(), own_red(), green()]
    with State(), tempfile.TemporaryDirectory() as tmp:
        logs: list[str] = []
        model = Model(answers)
        result = make(model, seeded(tmp), checks, logs=logs).implement(NODE, TESTS, own_red())
        assert (result.status, result.stop_reason) == ("passed", ""), (result, logs[-6:])
        assert [step for step, _o, prompt in model.calls if "SHARED DATA:" in prompt] == ["diagnose4", "repair4"]
        assert "add what this batch needs next to it" in model.prompts("repair4")[0]

class SpecUnderNote(Model):

    CLAIM = 'SPEC: "await expect(tab(" contradicts "The exported CSV preserves empty cells within the used range"'

    def chat(self, messages, **options):
        answer = super().chat(messages, **options)
        if ":diagnose" in str(options.get("tag", "")) and "SHARED DATA:" in str(messages[-1]["content"]):
            return {"content": self.CLAIM}
        return answer

@case
def a_spec_verdict_under_the_note_is_marked_seed_conflict_but_not_under_the_hint():
    with State(), tempfile.TemporaryDirectory() as tmp:
        result = make(SpecUnderNote(SHEET_ANSWERS), seeded(tmp), SHEET_CHECKS).implement(NODE, TESTS, own_red())
        assert (result.status, result.defect_kind) == ("test_defect", "seed-conflict"), result
    weak = [block("backend/src/routes/sheets.js", "x"), seed_answer(1), seed_answer(2), block(PAGE, "x"), seed_answer(4), seed_answer(5)]
    with State(), tempfile.TemporaryDirectory() as tmp:
        result = make(SpecUnderNote(weak), seeded(tmp), SHEET_CHECKS).implement(NODE, TESTS, own_red())
        assert (result.status, result.defect_kind) == ("test_defect", ""), result

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
    print(f"{len(CASES) - failed}/{len(CASES)} v145b cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
