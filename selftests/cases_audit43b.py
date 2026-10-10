from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_closing as cl
import cases_rollback as rb

NL = chr(10)
CASES = []

def case(function):
    CASES.append(function)
    return function

@case
def a_cached_started_state_that_does_not_build_now_is_not_put_back():
    import main as factory_module
    from agent.verify import APP_DOWN_MARKS, StepResult

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = rb.make_factory(root, "before")
        before = f.snapshot_sources()
        state = {"before_builds": True}
        lines: list[str] = []

        def build_frontend(project_dir, log=print):
            text = (root / rb.APP).read_text(encoding="utf-8")
            return StepResult(state["before_builds"] or text != "before", "")

        def probe_server(project_dir, log=print, port=None):
            text = (root / rb.APP).read_text(encoding="utf-8")
            return StepResult(True, "", started=text == "before"), 0

        saved = {n: getattr(factory_module, n) for n in ("build_frontend", "probe_server", "log")}
        try:
            factory_module.build_frontend, factory_module.probe_server, factory_module.log = build_frontend, probe_server, lines.append
            down = APP_DOWN_MARKS[0]

            (root / rb.APP).write_text("current", encoding="utf-8")
            assert f.dead_app_put_back("REQ-1", "budget", before, down) is True
            assert (root / rb.APP).read_text(encoding="utf-8") == "before"

            (root / rb.APP).write_text("current", encoding="utf-8")
            state["before_builds"] = False
            assert f.dead_app_put_back("REQ-1", "budget", before, down) is False, lines
            assert (root / rb.APP).read_text(encoding="utf-8") == "current", "the existing restore-current path"

            state["before_builds"] = True
            assert f.dead_app_put_back("REQ-1", "budget", before, down) is True
        finally:
            for n, v in saved.items():
                setattr(factory_module, n, v)

def closing_repair(guard_result, target_result=None):
    import main as factory_module
    from agent.llm import LLM
    from agent.verify import StepResult

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = rb.make_factory(root, "base")
        target = "backend/test-e2e/REQ-5.spec.js"
        f.e2e_specs, f.green_specs = [rb.F1, rb.F2, target], [rb.F1, rb.F2]
        f.spec_context = {target: {"node": {"id": "REQ-5"}, "contract": "", "relevant": [], "tests": {}, "written": []}}
        calls = {"target": 0}

        def run_e2e(project_dir, specs, log=print, shared_data=False):
            text = (root / rb.APP).read_text(encoding="utf-8")
            if specs == [target]:
                calls["target"] += 1
                if calls["target"] > 1 and target_result is not None:
                    return target_result
                return StepResult("FIXED" in text, "1 passed" if "FIXED" in text else
                                  "FAILED SPEC FILES: REQ-5.spec.js" + NL + "  1) test-e2e\\REQ-5.spec.js:3:1 › REQ-5 ───")
            return guard_result
        script = [{"write": {rb.APP: "base FIXED"}, "check": True}]
        lines: list[str] = []
        names = ("run_e2e", "build_frontend", "Implementer", "log")
        saved = {name: getattr(factory_module, name) for name in names}
        saved_quota = LLM.quota_exhausted
        try:
            factory_module.run_e2e = run_e2e
            factory_module.build_frontend = lambda project_dir, log=print: StepResult(True, "")
            factory_module.Implementer = rb.implementer_class(script)
            factory_module.log = lines.append
            LLM.quota_exhausted = False
            repaired = f.repair_failing_specs("FAILED SPEC FILES: REQ-5.spec.js" + NL)
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
            LLM.quota_exhausted = saved_quota
        assert not script
        return repaired, (root / rb.APP).read_text(encoding="utf-8"), lines

@case
def a_closing_repair_whose_guard_was_skipped_is_not_kept():
    from agent.verify import StepResult

    repaired, app, lines = closing_repair(StepResult(True, "playwright browsers unavailable here", skipped=True))
    assert repaired == 0 and app == "base", (repaired, app, lines)

    repaired, app, _ = closing_repair(StepResult(True, "NOT RUN SPEC FILES: REQ-1-1-3.spec.js" + NL + "1 of 2 spec files passed"))
    assert repaired == 0 and app == "base"
    repaired, app, _ = closing_repair(StepResult(True, "0 passed", ran=0))
    assert repaired == 0 and app == "base"

    repaired, app, _ = closing_repair(StepResult(True, "2 of 2 spec files passed"), StepResult(True, "skipped", skipped=True))
    assert repaired == 0 and app == "base"

    repaired, app, _ = closing_repair(StepResult(True, "2 of 2 spec files passed, each run against fresh data"))
    assert repaired == 1 and app == "base FIXED"

SPEC = "backend/test-e2e/A.spec.js"
T1 = "test('A: shows the first title', async ({ page }) => {});" + NL
T2 = "test('A: shows the repaired title', async ({ page }) => {});" + NL

@case
def a_red_foundation_retry_leaves_one_test_version_everywhere():
    from agent.test_author import AuthoredFile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = rb.make_factory(root, "template")
        f.green_specs, f.e2e_specs, f.file_specs = [], [], {}
        f.description_pass, f.description_specs, f.llm = {}, [], None
        f.over_budget = lambda: False
        f.time_left = lambda: 10 ** 6
        f.GATEWAY_PAUSE_SEC = 0.0

        def process_node(node, nodes_left, retry=False):
            req = node["id"]
            f.current_members = [req]

            content = T2 if retry else T1
            f.write_tests(req, [AuthoredFile(path=SPEC, content=content)])
            (root / rb.APP).write_text("worse" if retry else "first attempt", encoding="utf-8")
            f.spec_context[SPEC] = {"node": node, "contract": "", "relevant": [], "tests": {SPEC: content}, "members": [req], "written": [rb.APP]}
            f.report["nodes"][req] = {"status": "budget", "e2e": "failed", "tests": [SPEC], "written": [rb.APP], "test_runs": 1}
            if SPEC not in f.e2e_specs:
                f.e2e_specs.append(SPEC)
            return False
        f.process_node = process_node
        result, lines = cl._queue(f, "A")
        assert result is False and f.foundation_retried == ["A"], lines
        assert (root / rb.APP).read_text(encoding="utf-8") == "first attempt"

        assert (root / SPEC).read_text(encoding="utf-8") == T2 and f.test_snapshot[SPEC] == T2
        assert f.spec_context[SPEC]["tests"] == {SPEC: T2}, f.spec_context[SPEC]["tests"]
        assert [f.test_titles[t] for t in f.test_ids_by_file[SPEC]] == ["A: shows the repaired title"]
        assert f.report["nodes"]["A"]["tests"] == [SPEC] and f.report["nodes"]["A"]["written"] == [rb.APP]
        state = json.loads((root / ".arc" / "resume.json").read_text(encoding="utf-8"))
        assert state["spec_context"][SPEC]["tests"][SPEC] == state["test_snapshot"][SPEC] == T2, state["spec_context"][SPEC]

def failure(line: int, port: int, helper_first: bool = False) -> str:
    frames = ([f"        at see (C:\\w\\backend\\e2e-support\\outsider.js:{100 + port % 7}:9)"] if helper_first else [])
    frames.append(f"        at C:\\w\\backend\\test-e2e\\F.spec.js:{line}:18")
    return NL.join(["  1) test-e2e\\F.spec.js:10:1 › checkout works ───────", "",
                    "    Error: expect(received).toBe(expected) // Object.is equality", "",
                    "    Expected: 2", "    Received: 1", "",
                    f"      {line - 1} |   // step",
                    f"    > {line} |   expect(value).toBe(2);",
                    "         |                 ^",
                    f"    at http://127.0.0.1:{port}/cart",
                    *frames, "",
                    "  1 failed", "    test-e2e\\F.spec.js:10:1 › checkout works ───", "  2 passed"])

@case
def two_assertions_with_the_same_error_text_have_different_signatures():
    import main as factory_module

    sig = factory_module.failed_test_signatures
    cart, submit = sig(failure(13, 50001)), sig(failure(20, 50002))
    assert cart["F.spec.js"]["checkout works"] != submit["F.spec.js"]["checkout works"], (cart, submit)

    assert sig(failure(13, 50003, helper_first=True)) == sig(failure(13, 50004, helper_first=True))
    assert sig(failure(13, 50003, helper_first=True)) == cart

    assert "F.spec.js:13:18" in cart["F.spec.js"]["checkout works"], cart

@case
def short_quoted_business_values_are_not_collapsed():
    import main as factory_module

    sig = factory_module.error_signature
    a1 = "    Locator: getByRole('gridcell', { name: 'A1' })"
    b2 = "    Locator: getByRole('gridcell', { name: 'B2' })"
    assert sig(a1) != sig(b2) and "'A1'" in sig(a1), (sig(a1), sig(b2))
    assert sig(a1) != sig(a1.replace("A1", "A2"))

    assert sig("Error: no row 'user-1695' shown") == sig("Error: no row 'user-2203' shown")
    assert sig("Error: no row 'n-3f2a9c1e-77b0-4c1d-9e8d-7c6b5a41aa00'") == sig("Error: no row 'n-9e8d7c6b-5a41-4c1d-9e8d-3f2a9c1e77b0'")
    assert sig("Error: saved at '2026-09-28 10:15'") == sig("Error: saved at '2026-09-29 11:40'")
    assert sig("Error: saved at '10:15'") == sig("Error: saved at '11:40'")

    assert sig("Error: Timeout 5000ms exceeded, row 17") == sig("Error: Timeout 30000ms exceeded, row 4")

HELPER_HEAD = 'const { test } = require("@playwright/test");' + NL + 'const { fill, see } = require("../e2e-support/outsider");' + NL

@case
def a_parameter_or_local_binding_named_like_the_helper_is_not_reported():
    from agent.testrun import helper_arity_defects

    rel = "backend/test-e2e/REQ-1.spec.js"
    shadowed = [
        "async function invoke(page, fill) { await fill(page); }" + NL,
        "const invoke = async (page, fill) => { await fill(page); };" + NL,
        "const invoke = async (page, { fill }) => { await fill(page); };" + NL,
        "const invoke = fill => fill(page);" + NL,
        "const { fill: _f, ...rest } = other; const [fill] = rest.list; await fill(page);" + NL,
        "try { x(); } catch (fill) { fill(page); }" + NL,
    ]
    for body in shadowed:
        assert helper_arity_defects(rel, HELPER_HEAD + body) == [], body

    assert helper_arity_defects(rel, HELPER_HEAD + 'test("t", async ({ page }) => { await fill(page, "alice"); });' + NL)

    assert helper_arity_defects(rel, HELPER_HEAD + 'async function go(page, value) { await fill(page, value); }' + NL)

@case
def comments_and_strings_do_not_drop_a_candidate():
    import agent.description_pass as dp

    head = 'const { test } = require("@playwright/test");' + NL + 'const { see } = require("../e2e-support/outsider");' + NL
    tmp, root, world, f = cl.with_world()
    with tmp:
        p = dp.DescriptionPass(f, {}, [], "", [], "d", "", lambda line: None, dp.default_tools(f, lambda line: None))

        def block(title, body=""):
            return f'test("{title}", async ({{ page }}) => {{{NL}{body}  await see(page, "x");{NL}}});{NL}{NL}'
        variants = [
            (head + "// Never use test.skip here" + NL + NL, "", ""),
            (head + 'const hint = "test.skip";' + NL + "/* test.only is forbidden */" + NL + NL, "", ""),
            (head + NL, "  // test.fixme would hide it" + NL + "  const why = 'test.fail()';" + NL, ""),
        ]
        for header, body_a, _ in variants:
            tests: dict = {}
            out = p.quote_gate(header + block(cl.T3A, body_a) + block(cl.T3B), cl.DESC3, tests)
            assert cl.T3A in out and cl.T3B in out, (header, body_a, tests)

        tests = {}
        assert p.quote_gate(head + "test.skip(true);" + NL + block(cl.T3A) + block(cl.T3B), cl.DESC3, tests) == ""
        tests = {}
        out = p.quote_gate(head + block(cl.T3A, "  test.skip(); // later" + NL) + block(cl.T3B), cl.DESC3, tests)
        assert cl.T3A not in out and cl.T3B in out and tests[cl.T3A]["reason"] == "not-run-modifier"
        tests = {}
        out = p.quote_gate(head + block(cl.T3A, "  const t = `${test.skip()}`;" + NL) + block(cl.T3B), cl.DESC3, tests)
        assert cl.T3A not in out, "an expression inside a template literal is code"

@case
def the_no_reset_rule_leaves_described_behaviour_alone():
    from agent.conventions import PROJECT_CONVENTIONS

    flat = " ".join(PROJECT_CONVENTIONS.split())
    assert "only users' actions change data" not in flat
    around = flat[flat.index("never restores, resets or re-seeds data"):][:500]
    assert "requirement itself describes" in around and "automatic status change" in around, around
    assert all(ord(ch) < 128 for ch in around)

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
    print(f"{len(CASES) - failed}/{len(CASES)} audit-43b cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
