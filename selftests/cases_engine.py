from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NL = chr(10)
CASES = []

def case(function):
    CASES.append(function)
    return function

def block(path: str, *lines: str) -> str:
    return NL.join([f"=== FILE: {path} ===", *lines, "=== END FILE ==="])

class _Trace:
    def __init__(self):
        self.states = []

    def node_state(self, member, state, phase):
        self.states.append((member, state))

def _factory(outcomes: dict, budget_after: str = ""):
    import main as factory_module

    f = factory_module.Factory.__new__(factory_module.Factory)
    f.trace = _Trace()
    f.failed_members, f.unattempted_members = [], []
    f.GATEWAY_PAUSE_SEC = 0.0
    state = {"over": False}
    f.over_budget = lambda: state["over"]
    calls, checkpoints = [], []

    def process_node(node, nodes_left):
        calls.append(node["id"])
        outcome = outcomes[node["id"]].pop(0) if len(outcomes[node["id"]]) > 1 else outcomes[node["id"]][0]
        if node["id"] == budget_after:
            state["over"] = True
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    f.process_node = process_node
    f.save_checkpoint = lambda digest, done, description: checkpoints.append(list(done))
    return f, calls, checkpoints

def _nodes(*ids):
    return [({"id": i, "members": [i]}, []) for i in ids]

@case
def a_batch_whose_gateway_fails_twice_goes_to_the_end_of_the_queue():
    from agent.llm import LLM, LLMError

    timeout = LLMError("LLM request failed after 2 read timeouts")
    saved = LLM.quota_exhausted
    try:
        LLM.quota_exhausted = False

        f, calls, checkpoints = _factory({"A": [timeout, timeout, True], "B": [True], "C": [True]})
        assert f.run_queue(_nodes("A", "B", "C"), [], "d", "") is True
        assert calls == ["A", "A", "B", "C", "A"], calls
        assert checkpoints == [["B"], ["B", "C"], ["B", "C", "A"]], checkpoints
        assert f.failed_members == [] and f.trace.states == []

        f, calls, checkpoints = _factory({"A": [timeout], "B": [True]})
        assert f.run_queue(_nodes("A", "B"), [], "d", "") is False
        assert calls == ["A", "A", "B", "A", "A", "A", "A"], calls
        assert f.failed_members == ["A"] and f.trace.states == [("A", "FAILED")] and checkpoints[-1] == ["B", "A"]

        f, calls, _ = _factory({"A": [RuntimeError("a bug in the batch")], "B": [True]})
        assert f.run_queue(_nodes("A", "B"), [], "d", "") is False and calls == ["A", "B"] and f.failed_members == ["A"]

        f, calls, checkpoints = _factory({"A": [timeout], "B": [True], "C": [True]}, budget_after="B")
        assert f.run_queue(_nodes("A", "B", "C"), [], "d", "") is False and calls == ["A", "A", "B"], calls
        assert f.failed_members == ["A"] and f.unattempted_members == ["C"] and f.trace.states == [("A", "FAILED")]
        assert checkpoints == [["B"]]

        f, calls, _ = _factory({"A": [True], "B": [True]})
        assert f.run_queue(_nodes("A", "B"), ["A"], "d", "") is True and calls == ["B"]

        f, calls, _ = _factory({"A": [LLMError("quota")], "B": [True]})
        LLM.quota_exhausted = True
        assert f.attempt_node({"id": "A", "members": ["A"]}, 2, pause_sec=0, requeue=True) is False and calls == ["A"]
    finally:
        LLM.quota_exhausted = saved

def _lint(files: dict[str, str], native: bool = False) -> str:
    from agent.testrun import lint_application

    with tempfile.TemporaryDirectory() as tmp:
        for rel, text in files.items():
            path = Path(tmp) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (Path(tmp) / "backend").mkdir(exist_ok=True)
        (Path(tmp) / "backend" / "package.json").write_text('{"dependencies": {"express": "5"}}', encoding="utf-8")
        return lint_application(Path(tmp), native)

@case
def a_form_in_a_comment_is_no_form():
    commented = ("/**" + NL + " * AddMemberPanel" + NL + " * The panel is a `<form>` with one field." + NL + " */" + NL
                 + "// old: <form onSubmit={x}>" + NL + "export default function P() {" + NL
                 + "  return (<div>{/* <form> was here */}<p>Don't use http://x.org</p></div>);" + NL + "}" + NL)
    assert "noValidate" not in _lint({"frontend/src/components/AddMemberPanel.tsx": commented})
    real = commented.replace("<div>{/*", '<form onSubmit={save}>{/*').replace("</div>);", "</form>);")
    lint = _lint({"frontend/src/components/AddMemberPanel.tsx": real})
    assert "AddMemberPanel.tsx:7: <form> without `noValidate`" in lint, lint
    assert "noValidate" not in _lint({"frontend/src/components/AddMemberPanel.tsx": real}, native=True)

    assert "noValidate" in _lint({"frontend/src/A.tsx": 'const u = "http://x"; const f = <form onSubmit={s}></form>;' + NL})

    assert _lint({"backend/src/app.js": "// app.get('*', spa) was replaced" + NL + "/* require('bcrypt') */" + NL}) == ""
    backend = _lint({"backend/src/app.js": "app.get('*', spa);" + NL + "const b = require('bcrypt');" + NL})
    assert "bare '*' route" in backend and "`bcrypt`" in backend

def _implement(outcomes, first, test_files=None, max_rounds=4, diagnosis="frontend/src/pages/HomePage.tsx -- render it"):
    from agent.codegen import CodegenImplementer

    class Model:
        def __init__(self):
            self.calls, self.last_finish_reason, self.model = [], "stop", "glm-test"

        def chat(self, messages, **options):
            self.calls.append(options.get("tag", ""))
            if ":diagnose" in options.get("tag", ""):
                return {"content": diagnosis}
            return {"content": block("frontend/src/pages/HomePage.tsx", "export default function HomePage() { return null; }")}

    model = Model()
    with tempfile.TemporaryDirectory() as tmp:
        results = iter(outcomes)
        implementer = CodegenImplementer(model, Path(tmp), lambda: next(results), max_rounds=max_rounds, log=lambda message: None)
        node = {"id": "REQ-9", "name": "n", "description": "Open the catalogue."}
        files = test_files if test_files is not None else {"backend/test-e2e/REQ-9.spec.js": "await goto(page, '/');"}
        return implementer.implement(node, files, first), model

LINT = ("APPLICATION LINT FAILED (fix these too, in the same answer):" + NL
        + "frontend/src/components/AddMemberPanel.tsx:6: <form> without `noValidate`. Browser-native validation blocks ...")

@case
def lint_alone_never_blocks_a_green_e2e():
    from agent.testrun import TestOutcome, lint_only_failure

    green = LINT + NL + NL + "E2E PASSED" + NL + "  6 passed (41.2s)"
    red = LINT + NL + NL + "E2E FAILED against the real application" + NL + "  1) a.spec.js:3 > REQ-9: opens" + NL + "Error: x"
    assert lint_only_failure(green) is not None and "AddMemberPanel" in lint_only_failure(green)
    assert lint_only_failure(red) is None and lint_only_failure("E2E PASSED" + NL) is None

    first = TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 > REQ-9: opens" + NL + "Error: heading not found")
    result, model = _implement([TestOutcome(False, green)], first)
    assert result.status == "passed" and "E2E PASSED" in result.last_check and "noValidate" in result.last_check

    result, model = _implement([], TestOutcome(False, green))
    assert result.status == "passed" and model.calls == []

    result, model = _implement([TestOutcome(False, red)] * 12, first)
    assert result.status != "passed"

@case
def the_same_failure_gets_the_same_signature():
    from agent.codegen import blocking_output, failure_signature

    head = "  1) test-e2e/REQ-1-1-1+1.spec.js:4:1 › REQ-1-1-1+1: create a workbook "
    timing = ("    Error: tab named Sheet1 was not on the page when it opened: it was found {ms} ms after the click that opened "
              "this page, not at the first look. Someone who looks right away sees it missing.")

    assert failure_signature([head, timing.format(ms=208)]) == failure_signature([head, timing.format(ms=205)]) \
        == failure_signature([head, timing.format(ms=197)])

    shown = "    Error: The requirement says Outsider {name} is shown, but nothing visible matches."
    assert failure_signature([shown.format(name="1790470779686-7ew3")]) == failure_signature([shown.format(name="1790470786784-ue4e")])

    stated = "    Error: The requirement says {name} is shown, but nothing visible matches."
    assert failure_signature([stated.format(name="alice-dev4")]) != failure_signature([stated.format(name="alice-dev5")])

    resolved = '        {n} × locator resolved to <div role="gridcell" aria-label="B4" aria-selected="false">700</div>'
    assert failure_signature([resolved.format(n=9)]) == failure_signature([resolved.format(n=8)])

    assert failure_signature([head, timing.format(ms=208)]) != failure_signature([head, timing.replace("Sheet1", "Sheet2").format(ms=208)])

    e2e = "E2E FAILED against the real application" + NL + head + NL + timing.format(ms=208)
    api_1 = NL + NL + "ADVISORY backend API test failures (fix them if they point at a real bug; they do not block):" + NL \
        + " FAIL  tests/REQ-1-1-2.test.js > REQ-1-1-2: Sign In" + NL + 'TypeError: Invalid value "undefined" for header "Cookie"'
    api_2 = NL + NL + "ADVISORY backend API test failures (fix them if they point at a real bug; they do not block):" + NL \
        + " FAIL  tests/REQ-1-1-2.test.js > REQ-1-1-2: Sign In" + NL + "AssertionError: expected 401 to be 200"

    def signature(output):
        return failure_signature([line for line in blocking_output(output).splitlines() if "Error" in line or "›" in line or "FAIL" in line])

    assert signature(e2e + api_1) == signature(e2e + api_2) == signature(e2e)
    assert blocking_output(e2e + api_1 + NL + NL + "ALSO BROKEN: x") == e2e
    assert failure_signature([head, "ADVISORY component test failures (jsdom)", "Error: jsdom only"]) == failure_signature([head])

SPEC_THROWS = NL.join([
    "E2E FAILED against the real application",
    "  1) test-e2e/REQ-9.spec.js:4:1 › reference error in spec ─────────────",
    "",
    "    ReferenceError: goto is not defined",
    "",
    "      4 | test(\"reference error in spec\", async () => {",
    "    > 5 |   await goto(null, \"/\");",
    "        |   ^",
    "        at C:\\work\\backend\\test-e2e\\REQ-9.spec.js:5:3",
    "",
    "  2) test-e2e/REQ-9.spec.js:7:1 › matcher misuse ──────────────────────",
    "",
    "    Error: toHaveValue can be only used with Locator object",
    "",
    "    >  8 |   await expect(formulaBar()).toHaveValue(\"=A2+B2\");",
    "        at /workspace/backend/test-e2e/REQ-9.spec.js:8:30",
])
HELPER_THROWS = NL.join([
    "E2E FAILED against the real application",
    "  3) test-e2e/REQ-9.spec.js:10:1 › helper throws ──────────────────────",
    "",
    "    Error: The requirement says Q3 Sales is shown, but nothing visible matches.",
    "",
    "       at ..\\e2e-support\\outsider.js:1",
    "",
    "    > 1 | async function see(page, what) { throw new Error(`...`); }",
    "        at see (C:\\work\\backend\\e2e-support\\outsider.js:1:40)",
    "        at C:\\work\\backend\\test-e2e\\REQ-9.spec.js:11:9",
    "  4) test-e2e/REQ-9.spec.js:13:1 › app data not array ─────────────────",
    "    TypeError: body.find is not a function",
    "        at C:\\work\\backend\\test-e2e\\REQ-9.spec.js:15:8",
    "  5) test-e2e/REQ-9.spec.js:17:1 › helper bug ─────────────────────────",
    "    ReferenceError: inventory is not defined",
    "        at see (C:\\work\\backend\\e2e-support\\outsider.js:3:5)",
    "        at C:\\work\\backend\\test-e2e\\REQ-9.spec.js:18:9",
])

@case
def an_error_the_spec_raises_by_itself_is_recognised_by_its_stack():
    from agent.codegen import spec_thrown_errors

    own = {"backend/test-e2e/REQ-9.spec.js": ""}
    assert spec_thrown_errors(SPEC_THROWS, own) == ["REQ-9.spec.js: ReferenceError: goto is not defined",
                                                    "REQ-9.spec.js: Error: toHaveValue can be only used with Locator object"]

    assert spec_thrown_errors(HELPER_THROWS, own) == []

    assert spec_thrown_errors(SPEC_THROWS, {"backend/test-e2e/REQ-1.spec.js": ""}) == []

@case
def not_a_function_counts_only_for_a_name_the_spec_never_binds():
    from agent.codegen import binds_name, spec_thrown_errors

    def thrown(name):
        return NL.join(["E2E FAILED against the real application", "  1) test-e2e/REQ-9.spec.js:4:1 › x ─────",
                        f"    TypeError: {name} is not a function", "        at C:\\work\\backend\\test-e2e\\REQ-9.spec.js:20:5"])
    spec = "backend/test-e2e/REQ-9.spec.js"
    free = 'const { test } = require("@playwright/test");\ntest("a", async ({ page }) => { await action(page); });\n'
    assert spec_thrown_errors(thrown("action"), {spec: free}) == ["REQ-9.spec.js: TypeError: action is not a function"]

    for binding in ("const action = handlers[await status.textContent()];",
                    "let action;\naction = pick();",
                    "var action = null;",
                    "const { action } = handlers;",
                    "const [action] = list;",
                    "async function run(page, action) { await action(); }",
                    "const run = async ({ page, action }) => { await action(); };",
                    "items.forEach(action => action());"):
        assert binds_name(binding, "action"), binding
        assert spec_thrown_errors(thrown("action"), {spec: binding + "\n" + free}) == [], binding

    for imported in ("const { test, expect, goto } = require('@playwright/test');",
                     "const { action } = require('../e2e-support/outsider');",
                     "import { test, action } from '@playwright/test';",
                     "const action = require('./helpers');"):
        assert not binds_name(imported, "goto" if "goto" in imported else "action"), imported
    goto_spec = "const { test, expect, goto } = require('@playwright/test');\ntest('a', async ({ page }) => { await goto(page, '/'); });\n"
    assert spec_thrown_errors(thrown("goto"), {spec: goto_spec}) == ["REQ-9.spec.js: TypeError: goto is not a function"]

    for text in ("const reaction = 1;", "obj.action = f;", "if (action == null) {}", "const actions = [];", "fn(action);"):
        assert not binds_name(text, "action"), text

    assert spec_thrown_errors(SPEC_THROWS, {"backend/test-e2e/REQ-9.spec.js": "const goto = 1;"})[0].endswith("goto is not defined")

@case
def a_requeued_batch_checks_the_budget_again_after_its_pause():
    import main as factory_module
    from agent.llm import LLM, LLMError

    timeout = LLMError("LLM request failed after 2 read timeouts")
    saved_quota, saved_sleep = LLM.quota_exhausted, factory_module.time.sleep
    try:
        LLM.quota_exhausted = False
        for flag in ("budget", "quota"):
            f, calls, checkpoints = _factory({"A": [timeout, timeout, True], "B": [True]})
            state = {"over": False}
            f.over_budget = lambda: state["over"]
            sleeps = []

            def sleep(seconds, flag=flag):
                sleeps.append(seconds)
                if len(sleeps) == 2:
                    if flag == "budget":
                        state["over"] = True
                    else:
                        LLM.quota_exhausted = True
            factory_module.time.sleep = sleep
            assert f.run_queue(_nodes("A", "B"), [], "d", "") is False
            assert calls == ["A", "A", "B"], (flag, calls)
            assert f.failed_members == ["A"] and checkpoints == [["B"]], (flag, f.failed_members, checkpoints)
            LLM.quota_exhausted = False

        f, calls, _ = _factory({"A": [timeout, timeout, True], "B": [True]}, budget_after="B")
        sleeps.clear()
        factory_module.time.sleep = lambda seconds: sleeps.append(seconds)
        assert f.run_queue(_nodes("A", "B"), [], "d", "") is False and calls == ["A", "A", "B"] and len(sleeps) == 1
    finally:
        LLM.quota_exhausted = saved_quota
        factory_module.time.sleep = saved_sleep

@case
def the_same_failure_three_times_changes_the_strategy():
    from agent.testrun import TestOutcome

    first = TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 > REQ-9: opens")
    thrown = TestOutcome(False, SPEC_THROWS)

    result, model = _implement([thrown] * 12, first)
    assert result.status == "test_defect" and result.test_runs == 3, (result.status, result.test_runs)
    assert "REQ-9.spec.js: ReferenceError: goto is not defined" in result.detail
    assert model.calls.count("codegen:REQ-9:diagnose1") == 1 and "codegen:REQ-9:diagnose3" not in model.calls

    green = TestOutcome(True, "E2E PASSED")
    result, _ = _implement([thrown, thrown, green], first)
    assert result.status == "passed"

    result, _ = _implement([TestOutcome(False, HELPER_THROWS)] * 12, first)
    assert result.status == "budget", result.status

    def red(text):
        return TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 > REQ-9: opens" + NL + "Error: " + text)

    moving = [red("a"), red("b"), red("c"), red("d"), red("e"), red("f"), red("g"), red("h")]
    result, _ = _implement(moving, first)
    assert result.test_runs > 5, result.test_runs

    stuck = [red("a")] * 12
    result, _ = _implement(stuck, first)
    assert result.status == "budget" and result.test_runs == 5, (result.status, result.test_runs)

    moved_on = [red("a"), red("a"), red("a"), red("d"), red("e"), red("f"), red("g"), red("h")]
    result, _ = _implement(moved_on, first)
    assert result.test_runs > 5, (result.status, result.test_runs)

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
            traceback.print_exc(limit=3)
    print(f"{len(CASES) - failed}/{len(CASES)} engine cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
