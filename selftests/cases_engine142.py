from __future__ import annotations

import ast
import inspect
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_rollback as rb

NL = chr(10)
CASES = []
MISSING = object()

def case(function):
    CASES.append(function)
    return function

class Settings:

    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        from agent.settings import SETTINGS

        self.saved = {key: SETTINGS.get(key, MISSING) for key in self.values}
        SETTINGS.update(self.values)

    def __exit__(self, *exc):
        from agent.settings import SETTINGS

        for key, value in self.saved.items():
            if value is MISSING:
                SETTINGS.pop(key, None)
            else:
                SETTINGS[key] = value

S551_FILE = "backend/test-e2e/REQ-5-1-1.spec.js"
S551 = {S551_FILE: "// the batch's spec"}
A = "Error: An outsider who only read the requirement could not find anything to click: acme-docs."
B1 = "Error: The requirement says Improve onboarding is shown, but nothing visible matches."
B2 = "Error: The requirement says Legacy welcome text is shown, but nothing visible matches."
C = "Error: expect(locator).toContainText(expected) failed"
D = ("Error: heading named Improve onboarding was not on the page when it opened: it was found 182 ms after the click that "
     "opened this page, not at the first look. Someone who looks right away sees it missing.")
E = "Error: An outsider who only read the requirement could not find anything to click: link named Improve onboarding."

def pw_block(file: str, line: int, title: str, error: str) -> str:
    name = Path(file).name
    return NL.join([f"[1/1] test-e2e/{name}:{line}:1 › {title}",
                    f"  1) test-e2e/{name}:{line}:1 › {title} ────",
                    f"    {error}",
                    "    > 45 |   throw new Error(`${what}\\n  url: ${page.url()}`);",
                    f"    Error Context: test-results/{name}-x/error-context.md",
                    f"    test-e2e/{name}:{line}:1 › {title} ─────"])

def red_output(*blocks: str, files: str = "") -> str:
    return NL.join(["E2E FAILED against the real application", f"FAILED SPEC FILES: {files}"] + list(blocks))

def s551_check(s1: str, s2: str | None = None) -> str:
    t1 = "REQ-5-1-1: List and Filter Repository Issues - Scenario 1"
    t2 = "REQ-5-1-1: List and Filter Repository Issues - Scenario 2"
    blocks = [pw_block(S551_FILE, 4, t1, s1)] + ([pw_block(S551_FILE, 27, t2, s2)] if s2 else [])
    return red_output(*blocks, files="REQ-5-1-1.spec.js")

REQ_5_1_1 = [s551_check(A, A), s551_check(B1, B2), s551_check(C), s551_check(D), s551_check(C), s551_check(E),
             s551_check(C), s551_check(E)]

S433_FILE = "backend/test-e2e/REQ-4-3-3.spec.js"
S433 = {S433_FILE: "// the batch's spec"}
X433 = "Error: locator.selectOption: Target page, context or browser has been closed"
B433 = "Error: The requirement says Branch release is shown, but nothing visible matches."

def s433_check(s1: str | None, s2: str | None) -> str:
    t1 = "REQ-4-3-3: Change the Repository Default Branch - Scenario 1"
    t2 = "REQ-4-3-3: Change the Repository Default Branch - Scenario 2"
    blocks = ([pw_block(S433_FILE, 17, t1, s1)] if s1 else []) + ([pw_block(S433_FILE, 39, t2, s2)] if s2 else [])
    return red_output(*blocks, files="REQ-4-3-3.spec.js")

S433_REGRESSION = NL.join([
    "E2E FAILED against the real application",
    "This batch's own spec PASSES. What fails are specs of EARLIER requirements, which passed before this batch changed "
    "the files they depend on: backend/test-e2e/REQ-4-2-3.spec.js. This batch broke that behaviour. Those specs are correct "
    "and frozen: restore the earlier behaviour in the application AND keep this batch's own.",
    "FAILED SPEC FILES: REQ-4-2-3.spec.js",
    pw_block("backend/test-e2e/REQ-4-2-3.spec.js", 12, "REQ-4-2-3: code search finds README.md and opens the file",
             "Error: The requirement says main is shown, but nothing visible matches."),
])

REQ_4_3_3 = [s433_check(X433, B433), s433_check(X433, B433), s433_check(None, B433), S433_REGRESSION,
             s433_check(None, B433), s433_check(None, B433)]

def progress_of(checks: list[str], files: dict[str, str]) -> list[bool]:
    from agent.codegen import TestProgress, blocking_output, failure_signature

    tracker = TestProgress()
    marks = ("×", "FAIL", "Error", "expect(", "Locator:", "Timeout", "›")
    out = []
    for output in checks:
        blocking = blocking_output(output)
        signature = failure_signature([l for l in blocking.splitlines() if any(k in l for k in marks)])
        out.append(tracker.observe(blocking, files, signature)[0])
    return out

def implement_checks(checks: list[str], files: dict[str, str], max_rounds: int = 4, extra_rounds: int = 3, **options):
    import cases_engine as ce
    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome

    class Model:
        def __init__(self):
            self.calls, self.last_finish_reason, self.model = [], "stop", "glm-test"

        def chat(self, messages, **opts):
            self.calls.append(opts.get("tag", ""))
            if ":diagnose" in opts.get("tag", ""):
                return {"content": "frontend/src/pages/HomePage.tsx -- render it"}
            return {"content": ce.block("frontend/src/pages/HomePage.tsx", f"export default function HomePage() {{ return {len(self.calls)}; }}")}

    lines: list[str] = []
    outcomes = iter([TestOutcome(False, text) for text in checks])
    delay = float(options.pop("check_delay", 0.0))

    def check():
        time.sleep(delay)
        return next(outcomes)
    with tempfile.TemporaryDirectory() as tmp:
        model = Model()
        implementer = CodegenImplementer(model, Path(tmp), check, max_rounds=max_rounds,
                                         extra_rounds=extra_rounds, log=lines.append, **options)
        result = implementer.implement({"id": "REQ-X", "name": "n", "description": "d"}, files, TestOutcome(False, "RED as expected"))
    return result, lines, model

@case
def github_v141_req_5_1_1_swinging_between_old_errors_earns_no_extra_round():

    assert progress_of(REQ_5_1_1, S551) == [True, True, True, True, False, True, False, False], progress_of(REQ_5_1_1, S551)

    result, lines, _ = implement_checks(REQ_5_1_1, S551)
    assert result.status == "budget" and result.test_runs == 5, (result.status, result.test_runs, lines)
    assert not any("granting extra round" in line for line in lines), lines
    assert any("no extra round: every failing test fails the way it did before" in line for line in lines), lines

@case
def github_v141_req_4_3_3_a_stuck_scenario_earns_no_extra_round():

    assert progress_of(REQ_4_3_3, S433) == [True, False, True, True, False, False], progress_of(REQ_4_3_3, S433)
    result, lines, _ = implement_checks(REQ_4_3_3, S433)
    assert result.status == "budget" and result.test_runs == 5, (result.status, result.test_runs, lines)
    assert any("no extra round" in line and "is stuck" in line for line in lines), lines

T1, T2 = "REQ-X: one", "REQ-X: two"
SX_FILE = "backend/test-e2e/REQ-X.spec.js"
SX = {SX_FILE: "// spec"}

def two_tests(e1: str | None, e2: str | None) -> str:
    blocks = ([pw_block(SX_FILE, 3, T1, e1)] if e1 else []) + ([pw_block(SX_FILE, 9, T2, e2)] if e2 else [])
    return red_output(*blocks, files="REQ-X.spec.js")

@case
def a_converging_batch_keeps_its_extra_rounds():

    moving = [two_tests(f"Error: one-{i}", f"Error: two-{i}") for i in range(8)]
    result, lines, _ = implement_checks(moving, SX)
    assert result.test_runs == 8 and sum("granting extra round" in line for line in lines) == 3, (result.test_runs, lines)

    stuck_then_moved = [two_tests("Error: b", None)] * 4 + [two_tests("Error: y", None)] * 4
    result, lines, _ = implement_checks(stuck_then_moved, SX)
    assert result.test_runs == 6, (result.test_runs, lines)
    assert any("the stuck test REQ-X: one moved on" in line for line in lines), lines

    first_green = [two_tests("Error: b", "Error: x")] * 4 + [two_tests("Error: b", None)] * 4
    result, lines, _ = implement_checks(first_green, SX)
    assert result.test_runs == 6 and any("REQ-X: two turned green" in line for line in lines), (result.test_runs, lines)

@case
def a_stuck_test_is_not_released_by_another_tests_changing_error():

    changing = [two_tests("Error: b", f"Error: other-{i}") for i in range(8)]
    result, lines, _ = implement_checks(changing, SX)
    assert result.test_runs == 5 and not any("granting extra round" in line for line in lines), (result.test_runs, lines)

    moved = [two_tests("Error: b", None)] * 4 + [two_tests("Error: new", None)] + [two_tests("Error: newer", None)] * 3
    result, lines, _ = implement_checks(moved, SX)
    assert result.test_runs >= 6, (result.test_runs, lines)

@case
def a_check_whose_tests_cannot_be_read_counts_as_one_whole():

    builds = [f"FRONTEND BUILD FAILED{NL}Error: Cannot find module './x{i}'" for i in range(8)]
    result, lines, _ = implement_checks(builds, SX)
    assert result.test_runs == 8, (result.test_runs, lines)

    same = [f"FRONTEND BUILD FAILED{NL}Error: Cannot find module './x'"] * 8
    result, lines, _ = implement_checks(same, SX)
    assert result.test_runs == 5, (result.test_runs, lines)

@case
def a_test_missing_from_a_cut_or_partly_run_output_is_not_green():
    from agent.codegen import own_test_failures

    both = two_tests("Error: b", "Error: x")
    failures, greens = own_test_failures(both, SX)
    assert set(failures) == {("REQ-X.spec.js", T1), ("REQ-X.spec.js", T2)} and greens
    cut = two_tests("Error: b", None) + NL + "... [middle of the output omitted] ..."
    assert own_test_failures(cut, SX)[1] is False
    not_run = two_tests("Error: b", None) + NL + "  1 did not run"
    assert own_test_failures(not_run, SX)[1] is False

    assert progress_of([both, both, both, cut], SX)[-1] is False

    assert own_test_failures(S433_REGRESSION, S433) == ({}, True)

    at = two_tests("Error: expect(received).toBe(expected)", None)
    first = own_test_failures(at.replace("    > 45", "        at D:\\w\\backend\\test-e2e\\REQ-X.spec.js:5:9" + NL + "    > 45"), SX)[0]
    second = own_test_failures(at.replace("    > 45", "        at D:\\w\\backend\\test-e2e\\REQ-X.spec.js:7:9" + NL + "    > 45"), SX)[0]
    assert first != second and "REQ-X.spec.js:5:9" in list(first.values())[0], (first, second)

SPEC_2_FILE = "backend/test-e2e/REQ-4-1-2.spec.js"
SPEC_2 = {SPEC_2_FILE: "test('REQ-4-1-2: copying keeps absolute references unchanged', async ({ page }) => {\n"
                       "  await expect(locate(page, { role: 'gridcell', name: 'D2' })).toHaveText('0');\n});\n"}
NODE_2 = {"id": "REQ-4-1-2", "name": "Copy formulas",
          "description": "When a formula is copied, the target grid displays the result based on the new references."}
SPEC_LINE = ("SPEC: \"await expect(locate(page, { role: 'gridcell', name: 'D2' })).toHaveText('0');\" contradicts "
             "\"the target grid displays the result based on the new references\"")
APP_LINES = ["backend/src/lib/formula.js -- shift relative references when a formula is copied",
             "backend/src/routes/sheets.js -- recompute the target cells after the paste"]
CHECK_2 = red_output(pw_block(SPEC_2_FILE, 44, "REQ-4-1-2: copying keeps absolute references unchanged",
                              "Error: expect(locator).toHaveText(expected) failed"), files="REQ-4-1-2.spec.js")

def implement_with_diagnoses(diagnoses: list[str], checks: list[str], files=SPEC_2, node=NODE_2):
    import cases_engine as ce
    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome

    class Model:
        def __init__(self):
            self.calls, self.users, self.last_finish_reason, self.model = [], [], "stop", "glm-test"

        def chat(self, messages, **opts):
            self.calls.append(opts.get("tag", ""))
            self.users.append(messages[-1]["content"])
            if ":diagnose" in opts.get("tag", ""):
                return {"content": diagnoses.pop(0) if diagnoses else "backend/src/lib/formula.js -- fix it"}
            return {"content": ce.block("backend/src/lib/formula.js", f"module.exports = {len(self.calls)};")}

    lines: list[str] = []
    outcomes = iter([TestOutcome(False, text) for text in checks])
    with tempfile.TemporaryDirectory() as tmp:
        model = Model()
        implementer = CodegenImplementer(model, Path(tmp), lambda: next(outcomes), max_rounds=3, extra_rounds=0, log=lines.append)
        result = implementer.implement(node, files, TestOutcome(False, "RED as expected"))
    return result, lines, model

@case
def a_diagnosis_mixing_spec_and_app_lines_is_split():
    mixed = NL.join(["FIX PLAN:", SPEC_LINE] + APP_LINES)

    result, lines, model = implement_with_diagnoses([mixed, mixed], [CHECK_2] * 4)
    assert result.status == "test_defect", (result.status, lines)
    assert model.calls.count("codegen:REQ-4-1-2:diagnose1") == 2
    assert SPEC_LINE in result.detail and not any(line in result.detail for line in APP_LINES), result.detail
    assert result.detail.startswith("The acceptance spec contradicts the requirement text:")
    assert result.app_plan.splitlines() == APP_LINES and "SPEC:" not in result.app_plan, result.app_plan
    assert any("the spec goes to its author, the changes to the next implementer" in line for line in lines), lines

    result, lines, model = implement_with_diagnoses([mixed, SPEC_LINE], [CHECK_2] * 4)
    assert result.status == "test_defect" and result.app_plan.splitlines() == APP_LINES, (result.status, result.app_plan)

    result, lines, model = implement_with_diagnoses([SPEC_LINE, SPEC_LINE], [CHECK_2] * 4)
    assert result.status == "test_defect" and result.app_plan == ""

@case
def unproven_spec_lines_next_to_app_lines_leave_only_the_app_plan():
    unproven = "SPEC: the spec is wrong about \"something the requirement never says\""
    mixed = NL.join([unproven] + APP_LINES)
    result, lines, model = implement_with_diagnoses([mixed], [CHECK_2] * 4)
    assert result.status != "test_defect", (result.status, lines)
    assert any("a SPEC verdict without verifiable quotes is not accepted" in line for line in lines), lines
    writing = model.users[model.calls.index("codegen:REQ-4-1-2:repair1")]
    assert all(line in writing for line in APP_LINES) and "SPEC: the spec is wrong" not in writing, writing[-1500:]
    assert "does not count. The application is what has to change" in writing

def e2e_world_classes():

    class World(rb.World):
        def failing(self, spec, text):
            if spec == rb.OWN:
                own = (self.root / rb.OWN).read_text(encoding="utf-8") if (self.root / rb.OWN).is_file() else ""
                needs = "EXTRA" in own
                ok = ("OWN-OK" in text or "FIXED" in own) and (not needs or "EXTRA" in text)
                return [] if ok else [rb.OWN_TITLE]
            return super().failing(spec, text)
    return World

F4 = "backend/test-e2e/REQ-1-2.spec.js"
F5 = "backend/test-e2e/REQ-5.spec.js"
F6 = "backend/test-e2e/REQ-6.spec.js"

def scripted_class(script: list[dict], seen: list):
    from agent.codegen import ImplementResult

    class Scripted:
        def __init__(self, llm, project_dir, check, **options):
            self.project_dir, self.check = project_dir, check
            seen.append(("init", dict(options)))

        def implement(self, node, tests, outcome, extra_context=""):
            step = script.pop(0)
            seen.append(("implement", extra_context))
            written: list[str] = []
            runs = 0
            for rel, content in step.get("write", {}).items():
                (self.project_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                (self.project_dir / rel).write_text(content, encoding="utf-8")
                written.append(rel)
            for writes in step.get("checks", []):
                for rel, content in writes.items():
                    (self.project_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                    (self.project_dir / rel).write_text(content, encoding="utf-8")
                    written.append(rel)
                result = self.check()
                runs += 1
                if result.passed:
                    return ImplementResult("passed", list(dict.fromkeys(written)), "scripted", runs, last_check=result.output)
            return ImplementResult(step.get("status", "budget"), list(dict.fromkeys(written)), step.get("detail", "scripted"), runs,
                                   app_plan=step.get("app_plan", ""), routed=list(step.get("routed", [])))
    return Scripted

def run_node(app_before: str, script: list[dict], green=None, e1=None, retry=False, hub_retried=(), audit=None,
             repaired_spec: str = "FIXED", settings=None):
    import main as factory_module
    from agent.settings import SETTINGS
    from agent.test_author import AuthoredFile
    from agent.testrun import TestOutcome

    lines: list[str] = []
    seen: list = []
    repairs: list[str] = []
    e1_calls: list = []
    names = ("run_e2e", "build_frontend", "Implementer", "run_node_tests", "static_test_defects", "parse_defects_in_output",
             "lint_application", "log", "audit_node", "findings_brief")
    saved = {name: getattr(factory_module, name) for name in names}
    overrides = {"audit_enabled": bool(audit), **(settings or {})}
    with tempfile.TemporaryDirectory() as tmp, Settings(**overrides):
        root = Path(tmp)
        world = e2e_world_classes()(root)
        f = rb.make_factory(root, app_before)
        f.green_specs = list(green or [rb.F1, rb.F2])
        f.e2e_specs = list(f.green_specs)
        if hub_retried:
            f.hub_retried = list(hub_retried)
        if retry:
            content = f"test('{rb.OWN_TITLE}', async () => {{}});" + NL
            (root / rb.OWN).parent.mkdir(parents=True, exist_ok=True)
            (root / rb.OWN).write_text(content, encoding="utf-8")
            f.test_snapshot[rb.OWN] = content
            f.report["nodes"]["REQ-9"] = {"tests": [rb.OWN], "status": "budget"}

        def repair_tests(node, prefix, files, problem, contract=""):
            repairs.append(problem)
            return [AuthoredFile(path=rb.OWN, content=f"test('{rb.OWN_TITLE}', async () => {{}}); // {repaired_spec} {len(repairs)}" + NL)]
        f.repair_tests = repair_tests

        def fake_e1(project_dir, files, batch=None):
            e1_calls.append(batch)
            return ""
        try:
            factory_module.run_e2e, factory_module.build_frontend = world.run_e2e, world.build_frontend
            factory_module.Implementer = scripted_class(script, seen)
            factory_module.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
            factory_module.static_test_defects = e1 or fake_e1
            factory_module.parse_defects_in_output = lambda output, files: ""
            factory_module.lint_application = lambda project_dir, native: ""
            factory_module.log = lines.append
            if audit:
                factory_module.audit_node = lambda llm, project_dir, node, contract, log=print, use_llm=True: [dict(a) for a in audit]
                factory_module.findings_brief = lambda findings: "AUDIT BRIEF"
            passed = f.process_node({"id": "REQ-9", "name": "Batch nine", "members": ["REQ-9"]}, 1, **({"retry": True} if retry else {}))
            app = (root / rb.APP).read_text(encoding="utf-8") if (root / rb.APP).is_file() else None
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
    assert not script, f"scripted steps left over: {script}"
    return {"f": f, "lines": lines, "app": app, "passed": passed, "seen": seen, "repairs": repairs, "e1": e1_calls, "world": world}

@case
def process_node_hands_the_app_part_to_the_implementer_after_the_test_repair():
    script = [{"status": "test_defect", "detail": "The acceptance spec contradicts the requirement text:\n" + SPEC_LINE,
               "app_plan": NL.join(APP_LINES)},
              {"checks": [{rb.APP: "base OWN-OK"}]}]
    out = run_node("base", script, repaired_spec="unchanged")
    assert out["repairs"] and SPEC_LINE in out["repairs"][0] and not any(l in out["repairs"][0] for l in APP_LINES)
    contexts = [event[1] for event in out["seen"] if event[0] == "implement"]
    assert all(line in contexts[1] for line in APP_LINES) and "they are not made yet" in contexts[1], contexts
    assert out["passed"], out["lines"]

RED_BUDGET_UNIT = {"checks": [{rb.APP: "base feature-nine"}]}
AUDIT_FINDINGS = [{"where": "route /", "problem": "the grid shows no result", "layer": "scripted"}]

@case
def a_spec_defect_proven_by_the_audited_repair_of_a_red_batch_reaches_the_author():
    detail = "The acceptance spec contradicts the requirement text:\n" + SPEC_LINE
    script = [RED_BUDGET_UNIT, {"status": "test_defect", "detail": detail}]
    out = run_node("base", script, audit=AUDIT_FINDINGS)
    assert out["repairs"] == [detail], out["repairs"]
    assert out["passed"] and out["f"].report["nodes"]["REQ-9"]["status"] == "passed", out["lines"]
    assert out["f"].report["nodes"]["REQ-9"]["e2e"] == "passed"
    assert any("the audited repair proved a spec defect; the test author gets it" in line for line in out["lines"]), out["lines"]
    assert any("after the test author's repair the batch's check is GREEN" in line for line in out["lines"])

    script = [RED_BUDGET_UNIT, {"status": "test_defect", "detail": detail}]
    out = run_node("base", script, audit=AUDIT_FINDINGS, repaired_spec="NEEDS-EXTRA")
    assert out["repairs"] == [detail] and not out["passed"], out["lines"]

    script = [{"checks": [{rb.APP: "base OWN-OK"}]}, {"status": "test_defect", "detail": detail}]
    out = run_node("base", script, audit=AUDIT_FINDINGS)
    assert out["repairs"] == [] and out["passed"], out["lines"]

    script = [RED_BUDGET_UNIT, {"status": "test_defect", "detail": detail}]
    out = run_node("base", script, audit=AUDIT_FINDINGS, settings={"audit_spec_to_author": False})
    assert out["repairs"] == [] and not out["passed"]

L11223_REPR = ("'\"await press(page, \"\");\"\\n\"press() takes a key such as \\\\\"Enter\\\\\" or \\\\\"Control+Z\\\\\"; "
               "to type text use fill()/type -- got \\\\\"\\\\\"\"'")
L11214 = '    Error: SPEC DEFECT: press() takes a key such as "Enter" or "Control+Z"; to type text use fill()/type -- got ""'

@case
def the_l11223_claim_is_accepted_once_its_escapes_are_undone():
    from agent.codegen import spec_evidence, unescape_quotes

    claim = ast.literal_eval(L11223_REPR)
    assert '\\"Enter\\"' in claim
    spec = {"backend/test-e2e/DESC-REQ-2-2-2.spec.js": "async function setCell(page, cell, value) {\n  await press(page, \"\");\n}\n"}
    output = NL.join(["E2E FAILED against the real application", "  1) test-e2e/DESC-REQ-2-2-2.spec.js:20:1 › REQ-2-2-2: x ───", L11214])
    assert spec_evidence(claim, "Requirement REQ-2-2-2: references", spec, output)

    assert unescape_quotes('a \\"b\\" \\\\n c\\td\\ne \\` \\\'') == 'a "b" \\n c d e ` \''

    escaped_spec = {"backend/test-e2e/X.spec.js": "await see(page, 'It\\'s done');"}
    claim = "SPEC: \"await see(page, 'It's done');\" contradicts \"the message reads Finished\""
    assert spec_evidence(claim, "Requirement R: the message reads Finished.", escaped_spec, "")

    output = 'E2E FAILED\n    Received string: "Total\\n42"'
    claim = "SPEC: \"await see(page, 'It's done');\" contradicts the error \"Received string: 'Total 42'\""
    assert spec_evidence(claim, "Requirement R", escaped_spec, output)

    assert not spec_evidence('SPEC: "await press(page, " is wrong', "r", spec, output)

def brk5(n: int = 1) -> str:
    return rb.brk(f"REQ-5: Scenario {n}")

def brk6(n: int = 1) -> str:
    return rb.brk(f"REQ-6: Scenario {n}")

GREEN_4 = [rb.F1, rb.F2, F4, F5, F6]

@case
def a_batch_put_back_gets_its_best_in_batch_state_instead():
    best = "base OWN-OK " + brk5()
    script = [{"checks": [{rb.APP: best}, {rb.APP: "base " + rb.brk(rb.S1)}]}]
    out = run_node("base", script, green=GREEN_4)
    f = out["f"]
    assert out["app"] == best and out["passed"], (out["app"], out["lines"])
    assert any("put back to its best state instead" in line for line in out["lines"]), out["lines"]
    assert f.regression_debts == ["REQ-9"] and F5 not in f.green_specs and rb.F1 in f.green_specs
    assert f.report["nodes"]["REQ-9"]["status"] == "passed" and f.report["nodes"]["REQ-9"]["e2e"] == "passed"
    assert f.report["nodes"]["REQ-9"]["written"] == [rb.APP]

    assert any("put back to the state before this batch" in line for line in out["lines"])

@case
def only_a_check_without_a_foundation_failure_qualifies_and_the_fewest_regressions_win():

    script = [{"checks": [{rb.APP: "base OWN-OK " + rb.brk(rb.S1)}, {rb.APP: "base " + rb.brk(rb.S1) + " more"}]}]
    out = run_node("base", script, green=GREEN_4)
    assert out["app"] == "base" and not out["passed"], out["lines"]

    one, two = "base OWN-OK " + brk6() + " v1", "base OWN-OK " + brk5() + " " + brk6()
    script = [{"checks": [{rb.APP: one}, {rb.APP: two}, {rb.APP: "base " + rb.brk(rb.S1)}]}]
    out = run_node("base", script, green=GREEN_4)
    assert out["app"] == one and F6 not in out["f"].green_specs and F5 in out["f"].green_specs, out["lines"]

    first, later = "base OWN-OK " + brk5() + " a", "base OWN-OK " + brk6() + " b"
    script = [{"checks": [{rb.APP: first}, {rb.APP: later}, {rb.APP: "base " + rb.brk(rb.S1)}]}]
    out = run_node("base", script, green=GREEN_4)
    assert out["app"] == later, out["lines"]

    script = [{"checks": [{rb.APP: "base OWN-OK " + brk5()}, {rb.APP: "base " + rb.brk(rb.S1)}]}]
    out = run_node("base", script, green=GREEN_4, settings={"best_batch_state": False})
    assert out["app"] == "base" and not out["passed"], out["lines"]

@case
def a_best_state_judged_by_tests_repaired_since_is_not_used():

    script = [{"checks": [{rb.APP: "base OWN-OK " + brk5()}, {rb.APP: "base feature"}], "status": "test_defect", "detail": "SPEC: x"},
              {"checks": [{rb.APP: "base " + rb.brk(rb.S1)}]}]
    out = run_node("base", script, green=GREEN_4, repaired_spec="NEEDS-EXTRA")
    assert out["app"] == "base" and not out["passed"], out["lines"]
    assert any("judged by tests that were repaired since" in line for line in out["lines"]), out["lines"]

@case
def the_e2e_gate_repair_path_gets_the_best_state_too():

    best = "base OWN-OK " + brk5()
    script = [{"status": "passed", "write": {rb.APP: best}}, {"checks": [{rb.APP: "base " + rb.brk(rb.S1)}]}]
    out = run_node("base", script, green=GREEN_4)
    assert any("e2e gate FAILED" in line for line in out["lines"]), out["lines"]
    assert out["app"] == best and out["passed"], out["lines"]
    assert any("the e2e repair is put back to its best state" in line for line in out["lines"]), out["lines"]
    assert out["f"].report["nodes"]["REQ-9"]["e2e"] == "passed" and out["f"].regression_debts == ["REQ-9"]

@case
def gate_e1_gets_the_batch_when_it_takes_it_and_is_called_as_before_otherwise():
    import main as factory_module

    script = [{"status": "test_defect", "detail": "SPEC: y"}, {"checks": [{rb.APP: "base OWN-OK"}]}]
    out = run_node("base", script, repaired_spec="unchanged")
    batches = out["e1"]
    assert len(batches) >= 2 and all(isinstance(b, dict) and b.get("id") == "REQ-9" for b in batches), batches

    plain_calls: list = []

    def plain(project_dir, files):
        plain_calls.append(list(files))
        return ""
    script = [{"status": "test_defect", "detail": "SPEC: y"}, {"checks": [{rb.APP: "base OWN-OK"}]}]
    out = run_node("base", script, e1=plain, repaired_spec="unchanged")
    assert len(plain_calls) >= 2 and out["passed"], out["lines"]
    saved = factory_module.static_test_defects
    try:
        factory_module.static_test_defects = lambda project_dir, files, **kw: ""
        assert factory_module.e1_takes_batch()
        factory_module.static_test_defects = lambda project_dir, files: ""
        assert not factory_module.e1_takes_batch()
    finally:
        factory_module.static_test_defects = saved

    source = inspect.getsource(factory_module.Factory.reconcile_api_tests)
    assert 'static_test_defects(self.project_dir, [f.path for f in accepted], **({"batch": node} if e1_takes_batch() else {}))' in source

@case
def a_hub_retry_is_logged_as_a_hub_retry():
    out = run_node("base", [{"checks": [{rb.APP: "base OWN-OK"}]}], retry=True, hub_retried=["REQ-9"])
    assert any("(hub retry)" in line for line in out["lines"]) and not any("(foundation retry)" in line for line in out["lines"])
    out = run_node("base", [{"checks": [{rb.APP: "base OWN-OK"}]}], retry=True)
    assert any("(foundation retry)" in line for line in out["lines"]), out["lines"]

def raw_wait_fixtures():
    import cases_hubs as ch

    return ch.with_frames(ch.V141_LOGGED), ch.V141_FILES, ch.V141_SPEC

@case
def the_raw_wait_channel_only_where_the_caller_hands_the_spec_over():
    from agent.codegen import CodegenImplementer

    check, files, spec = raw_wait_fixtures()

    result, lines, _ = implement_checks([check] * 8, files, max_rounds=3, extra_rounds=0)
    assert result.status != "test_defect" and not any("handing it to the test author" in l for l in lines), lines

    before = set(CodegenImplementer.raw_wait_routed)
    result, lines, _ = implement_checks([check] * 8, files, max_rounds=3, extra_rounds=0, route_raw_waits=True)
    assert result.status == "test_defect" and result.test_runs == 2, (result.status, lines)
    assert len(result.routed) == 1 and result.routed[0][1] == Path(spec).name, result.routed
    assert set(CodegenImplementer.raw_wait_routed) == before

    result, lines, _ = implement_checks([check] * 8, files, max_rounds=3, extra_rounds=0, route_raw_waits=True,
                                        deadline=time.time() + 1.0, check_delay=0.6)
    assert result.status == "budget" and result.test_runs == 2, (result.status, result.test_runs, lines)

@case
def a_routed_check_names_the_specs_own_errors_too():
    check, files, spec = raw_wait_fixtures()
    thrown = NL.join([f"  9) test-e2e/{Path(spec).name}:90:1 › REQ-4-1: thrown ─────",
                      "    ReferenceError: goto is not defined",
                      f"        at D:\\w\\backend\\test-e2e\\{Path(spec).name}:91:3"])
    result, lines, _ = implement_checks([check + NL + thrown] * 8, files, max_rounds=3, extra_rounds=0, route_raw_waits=True)
    assert result.status == "test_defect", (result.status, lines)
    assert "ReferenceError: goto is not defined" in result.detail.split("Latest check output:")[0], result.detail[:1500]

@case
def process_node_turns_the_channel_on_only_while_a_test_repair_is_left_and_books_after_the_repair():
    from agent.codegen import CodegenImplementer

    key = ("engine142-test", "REQ-9.spec.js")
    CodegenImplementer.raw_wait_routed.discard(key)
    try:
        script = [{"status": "test_defect", "detail": "raw wait", "routed": [key]},
                  {"status": "test_defect", "detail": "raw wait again"},
                  {"checks": [{rb.APP: "base feature"}]}]
        out = run_node("base", script, repaired_spec="unchanged")
        inits = [event[1] for event in out["seen"] if event[0] == "init"]

        assert [o.get("route_raw_waits") for o in inits[:3]] == [True, True, False], inits

        assert key in CodegenImplementer.raw_wait_routed and len(out["repairs"]) == 2
    finally:
        CodegenImplementer.raw_wait_routed.discard(key)

    CodegenImplementer.raw_wait_routed.discard(key)
    try:
        script = [{"status": "test_defect", "detail": "1"}, {"status": "test_defect", "detail": "2"},
                  {"status": "test_defect", "detail": "3", "routed": [key]}]
        out = run_node("base", script, repaired_spec="unchanged")
        assert key not in CodegenImplementer.raw_wait_routed and len(out["repairs"]) == 2, out["repairs"]
    finally:
        CodegenImplementer.raw_wait_routed.discard(key)

    script = [{"checks": [{rb.APP: "base feature"}]}, {"status": "budget"}]
    out = run_node("base", script, audit=AUDIT_FINDINGS)
    inits = [event[1] for event in out["seen"] if event[0] == "init"]
    assert inits[0].get("route_raw_waits") is True and not inits[1].get("route_raw_waits"), inits

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
    print(f"{len(CASES) - failed}/{len(CASES)} engine142 cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
