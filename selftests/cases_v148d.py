from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_closing as cl

NL = chr(10)
CASES = []

def case(function):
    CASES.append(function)
    return function

def _model(answers):
    class Model:
        def __init__(self):
            self.calls, self.last_finish_reason, self.model = [], "stop", "glm-test"

        def chat(self, messages, **options):
            self.calls.append(dict(options))
            tag = str(options.get("tag", ""))
            for key, value in answers.items():
                if key in tag:
                    return value
            return {"content": ""}
    return Model()

@case
def the_diagnose_call_keeps_its_16000_token_cap():
    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome

    model = _model({":diagnose": {"content": "frontend/src/pages/HomePage.tsx -- render the heading"}})
    with tempfile.TemporaryDirectory() as tmp:
        implementer = CodegenImplementer(model, Path(tmp), lambda: TestOutcome(True, "E2E PASSED"), log=lambda m: None)
        node = {"id": "REQ-9", "name": "n", "description": "Open the catalogue."}
        plan = implementer._diagnose(node, {"backend/test-e2e/REQ-9.spec.js": "await goto(page, '/');"},
                                     TestOutcome(False, "E2E FAILED\n  1) REQ-9.spec.js:3 > opens\nError: heading"), [], "", 1, "")
    diagnose = [c for c in model.calls if ":diagnose" in str(c.get("tag"))]
    assert plan.startswith("frontend/src/pages/HomePage.tsx"), plan
    assert len(diagnose) == 1 and diagnose[0]["max_tokens"] == 16000 and diagnose[0]["thinking"] is True, diagnose

PLAN3 = NL.join(["backend/src/lib/undostack.js -- keep the undo history per workbook",
                 "backend/src/routes/workbooks.js -- record every operation on the stack",
                 "frontend/src/pages/EditorPage.tsx -- wire the Undo button to the endpoint"])

@case
def the_first_two_files_are_the_plans_own_first_two_whatever_the_failing_tier():
    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome

    model = _model({":decide": {"content": PLAN3}})
    lines = []
    with tempfile.TemporaryDirectory() as tmp:
        implementer = CodegenImplementer(model, Path(tmp), lambda: TestOutcome(True, "E2E PASSED"), log=lines.append)

        implementer._tiers = {"failing": ["frontend/src/pages/EditorPage.tsx", "backend/src/routes/workbooks.js"]}
        note = implementer._decision_note("REQ-3-2-2", "analysis " * 50, TestOutcome(False, "E2E FAILED\n  1) x"))
    assert "This answer writes ONLY these files: backend/src/lib/undostack.js, backend/src/routes/workbooks.js." in note, note
    assert any("the plan names 3 files" in line for line in lines), lines

FINDING = "frontend/src/App.tsx:1: `useEffect` is used here but never imported or defined in this file"

def lint_implementer(world):
    from agent.codegen import ImplementResult
    from agent.testrun import lint_only_failure

    class LintReadingImplementer:
        def __init__(self, llm, project_dir, check, **options):
            self.project_dir, self.check = project_dir, check

        def implement(self, node, tests, outcome, extra_context=""):
            world.firsts.append(outcome.output)
            step = world.steps.pop(0)
            world.tokens += 5000
            for rel, content in step.get("write", {}).items():
                (self.project_dir / rel).write_text(content, encoding="utf-8")
            result = self.check()
            world.check_outputs.append(result.output)
            green = result.passed or lint_only_failure(result.output) is not None
            return ImplementResult("passed" if green else "budget", list(step.get("write", {})), "scripted", 1,
                                   last_check=result.output if green else "")
    return LintReadingImplementer

def lint_world(findings="", raises=None):
    tmp, root, world, f = cl.with_world()
    world.titles["REQ-3"] = [cl.T3A]
    world.firsts = []
    calls = []

    def lint(project_dir, native):
        calls.append((Path(project_dir), native))
        if raises is not None:
            raise raises
        return findings
    return tmp, root, world, f, calls, {"Implementer": lint_implementer(world), "lint": lint}

@case
def a_red_repair_check_carries_the_lint_findings_in_front_of_its_own_output():
    from agent.testrun import LINT_HEADER, lint_only_failure

    tmp, root, world, f, calls, patch = lint_world(FINDING)
    with tmp:
        world.steps = [{"write": {cl.APP: "base still not fixed"}}]
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        out = world.check_outputs[0]
        assert out.startswith(LINT_HEADER + FINDING + NL + NL + "E2E FAILED against the real application"), out[:300]
        assert lint_only_failure(out) is None

        assert world.firsts[0].startswith(LINT_HEADER + FINDING + NL + NL + "E2E FAILED"), world.firsts[0][:200]
        assert cl.app_text(root) == "base" and summary["repairs_kept"] == 0, (cl.app_text(root), summary)
        assert calls and all(path == root and native is False for path, native in calls), calls

@case
def lint_alone_does_not_put_back_a_repair_the_gate_accepts():
    from agent.testrun import LINT_HEADER, lint_only_failure

    tmp, root, world, f, calls, patch = lint_world(FINDING)
    with tmp:
        world.steps = [{"write": {cl.APP: "base " + cl.ok(cl.T3A)}}]
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        out = world.check_outputs[0]
        assert out.startswith(LINT_HEADER) and lint_only_failure(out) == FINDING, out[:300]
        assert summary["repairs_kept"] == 1 and summary["repairs_rolled_back"] == 0, (summary, cl.ledger(f))
        assert cl.app_text(root) == "base " + cl.ok(cl.T3A) and cl.D3 in f.e2e_specs

@case
def without_findings_the_check_output_is_unchanged():
    tmp, root, world, f, calls, patch = lint_world("")
    with tmp:
        world.steps = [{"write": {cl.APP: "base " + cl.ok(cl.T3A)}}]
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        assert world.check_outputs[0].startswith("E2E PASSED" + NL), world.check_outputs[0][:200]
        assert world.firsts[0].startswith("E2E FAILED against the real application" + NL), world.firsts[0][:200]
        assert summary["repairs_kept"] == 1 and calls

@case
def a_lint_that_crashes_gives_no_finding_and_the_gate_stands():
    tmp, root, world, f, calls, patch = lint_world(raises=RuntimeError("node gone"))
    with tmp:
        world.steps = [{"write": {cl.APP: "base " + cl.ok(cl.T3A)}}]
        p, summary, lines = cl.run_pass(world, f, patch=patch)
        assert world.check_outputs[0].startswith("E2E PASSED" + NL) and summary["repairs_kept"] == 1, summary
        assert any("application lint failed" in line for line in lines), lines

@case
def the_pass_uses_the_batch_checks_lint_function_and_the_factorys_native_validation():
    import main as factory_module
    import agent.description_pass as dp

    tmp, root, world, f = cl.with_world()
    with tmp:
        assert dp.default_tools(f, lambda m: None).lint is factory_module.lint_application
        world.titles["REQ-3"] = [cl.T3A]
        world.firsts = []
        world.steps = [{"write": {cl.APP: "base still not fixed <form onSubmit={save}>"}}]
        p, summary, lines = cl.run_pass(world, f, patch={"Implementer": lint_implementer(world)})
        assert "<form> without `noValidate`" in world.check_outputs[0], world.check_outputs[0][:400]
    tmp, root, world, f = cl.with_world()
    with tmp:
        f.native_validation = True
        world.titles["REQ-3"] = [cl.T3A]
        world.firsts = []
        world.steps = [{"write": {cl.APP: "base still not fixed <form onSubmit={save}>"}}]
        p, summary, lines = cl.run_pass(world, f, patch={"Implementer": lint_implementer(world)})
        assert "noValidate" not in world.check_outputs[0], world.check_outputs[0][:400]

@case
def the_real_implementer_keeps_a_repair_that_is_green_but_for_lint():
    from agent.codegen import CodegenImplementer

    tmp, root, world, f = cl.with_world()
    with tmp:
        world.titles["REQ-3"] = [cl.T3A]
        fixed = "base " + cl.ok(cl.T3A)
        model = _model({"": {"content": cl.NL.join([f"=== FILE: {cl.APP} ===", fixed, "=== END FILE ==="])}})
        f.llm = model
        outputs = []

        class Recording(CodegenImplementer):
            def __init__(self, llm, project_dir, check, **options):
                def recorded():
                    result = check()
                    outputs.append(result.output)
                    return result
                super().__init__(llm, project_dir, recorded, **options)

        p, summary, lines = cl.run_pass(world, f, patch={"Implementer": Recording, "lint": lambda project_dir, native: FINDING})
        assert outputs and outputs[-1].startswith("APPLICATION LINT FAILED") and "E2E PASSED" in outputs[-1], outputs
        assert summary["repairs_kept"] == 1, (summary, cl.ledger(f), [line for line in lines if "desc-pass" in line][-6:])
        assert cl.app_text(root).strip() == fixed, repr(cl.app_text(root))

def lint_stub(left: float, hard: bool = False):
    import agent.description_pass as dp

    calls, lines = [], []

    class Stub:
        dir, f = Path("."), type("F", (), {"native_validation": False})()
        tools = type("T", (), {"lint": staticmethod(lambda project_dir, native: calls.append(1) or FINDING)})()
        log = staticmethod(lines.append)

        def left(self):
            return left

        def hard_stop(self, what, worst):
            return hard

    return (lambda outcome: dp.DescriptionPass.with_lint(Stub(), outcome)), calls, lines

@case
def the_lint_is_not_started_when_less_than_its_worst_case_is_left():
    import agent.description_pass as dp
    from agent import testrun
    from agent.testrun import LINT_HEADER, TestOutcome

    worst = dp.lint_worst_sec()
    assert worst == testrun.UNDEFINED_NAMES_TIMEOUT_SEC + testrun.HOOK_ORDER_TIMEOUT_SEC + dp.LINT_SCAN_SEC == 180.0, worst
    green = TestOutcome(True, "E2E PASSED" + NL + "3 passed")
    for left, hard, linted in ((worst + 1, False, True), (worst - 1, False, False), (0.0, False, False),
                               (worst + 1000, True, False)):
        run, calls, lines = lint_stub(left, hard)
        got = run(green)
        if linted:
            assert calls == [1] and got.output.startswith(LINT_HEADER + FINDING), (left, hard, got.output[:120])
            own = {"folder": ("Error: first line", "-- excerpt")}
            red = run(TestOutcome(False, "E2E FAILED" + NL + "x", error_contexts=own))
            assert red.output.startswith(LINT_HEADER) and red.error_contexts is own
        else:
            assert calls == [] and got is green, (left, hard)
            assert any("application lint skipped" in line for line in lines), lines

@case
def a_skipped_lint_keeps_the_repair_the_gate_accepts():
    import agent.description_pass as dp

    tmp, root, world, f, calls, patch = lint_world(FINDING)
    saved = dp.lint_worst_sec
    dp.lint_worst_sec = lambda: 1e12
    try:
        with tmp:
            world.steps = [{"write": {cl.APP: "base " + cl.ok(cl.T3A)}}]
            p, summary, lines = cl.run_pass(world, f, patch=patch)
            assert calls == [], calls
            assert world.check_outputs[0].startswith("E2E PASSED" + NL), world.check_outputs[0][:200]
            assert world.firsts[0].startswith("E2E FAILED against the real application" + NL), world.firsts[0][:200]
            assert summary["repairs_kept"] == 1 and summary["repairs_rolled_back"] == 0, summary
            assert cl.app_text(root) == "base " + cl.ok(cl.T3A)
            assert any("application lint skipped" in line for line in lines), [l for l in lines if "lint" in l]
    finally:
        dp.lint_worst_sec = saved

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
    print(f"{len(CASES) - failed}/{len(CASES)} v148d cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
