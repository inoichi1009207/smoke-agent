from __future__ import annotations

import json
import sys
import traceback
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import contract as contract_module
from agent import outsider as outsider_module
from agent.audit import routes_from_contract
from agent.batching import build_batches
from agent.codegen import failure_signature, parse_output
from agent.contract import _guarded, contract_slice, dedupe_alternatives, relevant_files
from agent.detaudit import alternative_pairs, wanted_role
from agent.outsider import _parse as parse_outsider_spec
from agent.test_author import split_file_blocks
from agent.testrun import framework_api_misuse
from agent.visual import _interface_texts, mandatory_texts, script_of, structure_only

NL = chr(10)
CR = chr(13)
CASES = []

def case(function):
    CASES.append(function)
    return function

def block(path: str, *lines: str, end: bool = True) -> str:
    return NL.join([f"=== FILE: {path} ===", *lines] + (["=== END FILE ==="] if end else []))

@case
def valid_contract_survives_the_cleanup():
    assert dedupe_alternatives('{"pages":[]}', "") == '{"pages":[]}'

@case
def cleanup_crash_keeps_the_contract():
    original = contract_module.dedupe_alternatives

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    contract_module.dedupe_alternatives = boom
    try:
        assert _guarded('{"pages":[1]}', "", lambda message: None) == '{"pages":[1]}'
    finally:
        contract_module.dedupe_alternatives = original

@case
def two_choices_are_not_one_element():
    text = json.dumps({"pages": [{"route": "/", "controls": [{"label": "Grid"}, {"label": "List"}]}]})
    out = json.loads(dedupe_alternatives(text, "Click `Grid` or `List` to switch the view."))
    assert [c["label"] for c in out["pages"][0]["controls"]] == ["Grid", "List"]

@case
def translation_pair_is_one_element():
    text = json.dumps({"pages": [{"route": "/", "links": [{"text": "登录", "href": "/login"}, {"text": "Login", "href": "/login"}]}]}, ensure_ascii=False)
    out = json.loads(dedupe_alternatives(text, "首页提供 `登录` 或 `Login` 入口", log=lambda message: None))
    assert [link["text"] for link in out["pages"][0]["links"]] == ["登录"]

@case
def contains_a_or_b_is_not_a_pair():
    assert alternative_pairs("accessible name 包含 `服务条款` 或 `Terms` 的复选框") == []

@case
def a_language_switch_is_two_options():
    assert alternative_pairs("语言选择提供 `中文` 或 `English` 两个按钮") == []
    assert alternative_pairs("Choose `中文` or `English` to switch the language.") == []

@case
def translation_pair_in_controls_is_one_element():
    text = json.dumps({"pages": [{"route": "/", "controls": [{"label": "提交"}, {"label": "Submit"}]}]}, ensure_ascii=False)
    out = json.loads(dedupe_alternatives(text, "点击 `提交` 或 `Submit` 按钮", log=lambda message: None))
    assert [c["label"] for c in out["pages"][0]["controls"]] == ["提交"]

@case
def control_kind_is_read_next_to_the_name():
    assert wanted_role("Sign out", "The header shows a link named `Sign out`.") == "link"
    assert wanted_role("退出", "点击 `退出` 链接后回到首页") == "link"
    assert wanted_role("Save", "Click the `Save` button.") == "button"
    assert wanted_role("Save", "`Save` is shown.") == ""

@case
def relevant_files_keep_dot_names():
    text = json.dumps({"pages": [{"route": "/", "file": "./frontend/.env", "requirements": ["REQ-1"]}], "shared": [{"file": "frontend/src/api/index.ts"}]})
    assert relevant_files(text, ["REQ-1"]) == ["frontend/.env", "frontend/src/api/index.ts"]

@case
def contract_slice_keeps_own_pages_and_stubs_the_rest():
    pages = [
        {"route": "/a", "file": "frontend/src/pages/A.tsx", "requirements": ["REQ-1"], "purpose": "x" * 4000},
        {"route": "/b", "file": "frontend/src/pages/B.tsx", "requirements": ["REQ-2"], "purpose": "y" * 4000},
    ]
    seeds = [{"entity": "note", "requirements": ["REQ-1"], "fields": {"title": "A"}}, {"entity": "note", "requirements": ["REQ-2"], "fields": {"title": "B"}}]
    sliced = json.loads(contract_slice(json.dumps({"pages": pages, "api": [], "seed": seeds}), ["REQ-1.2"]))
    assert "purpose" in sliced["pages"][0] and "purpose" not in sliced["pages"][1]
    assert [s["fields"]["title"] for s in sliced["seed"]] == ["A"] and "seed_note" in sliced

    api = [{"method": "GET", "path": "/api/repos/:o/:r", "requirements": ["REQ-2"], "purpose": "z" * 8000,
            "response": {"status": 200, "body": "{repository, branches: string[]}"}}]
    sliced = json.loads(contract_slice(json.dumps({"pages": pages, "api": api, "seed": seeds}), ["REQ-1.2"]))
    assert sliced["api"][0]["response"]["body"] == "{repository, branches: string[]}" and "purpose" not in sliced["api"][0]

@case
def audit_routes_put_own_pages_first():
    text = json.dumps({"pages": [
        {"route": "/other", "requirements": ["REQ-9"]}, {"route": "/mine", "requirements": ["REQ-1"]},
        {"route": "/mine", "requirements": ["REQ-1"]}, {"route": "/item/:id", "requirements": ["REQ-1"]}]})
    assert routes_from_contract(text, ["REQ-1.1"]) == ["/", "/mine", "/other"]

@case
def dot_files_keep_their_name():
    files, _ = parse_output(block(".env.example", "A=1"))
    assert [p for p, _ in files] == [".env.example"]

@case
def paths_leaving_the_project_are_dropped():
    files, _ = parse_output(block("../../x.js", "1"))
    assert files == []

@case
def equivalent_paths_are_canonical():
    files, _ = parse_output(block("backend/src/database/../database/index.js", "1"))
    assert [p for p, _ in files] == ["backend/src/database/index.js"]

@case
def same_path_twice_keeps_the_last():
    files, _ = parse_output(block("a.js", "first") + NL + block("a.js", "second"))
    assert files == [("a.js", "second" + NL)]

@case
def stray_think_tag_does_not_hide_the_first_file():
    from agent.llm import strip_reasoning_markup

    spec_body = 'const { see } = require("../e2e-support/outsider");' + NL + 'await see(page, "Notes");'
    raw = "</think>" + block("backend/test-e2e/R.spec.js", spec_body)
    assert split_file_blocks(raw) == [("backend/test-e2e/R.spec.js", spec_body)]
    assert strip_reasoning_markup(raw).startswith("=== FILE:")
    assert strip_reasoning_markup("<think>plan" + NL + "more</think>" + NL + "=== FILE: a.js ===").lstrip().startswith("=== FILE:")
    assert parse_outsider_spec(raw, "R") is not None
    leaked = "Issues: notes not visible, likely the fetch fails.</think>" + block("a.js", "1")
    assert strip_reasoning_markup(leaked).startswith("=== FILE:") and split_file_blocks(leaked) == [("a.js", "1")]
    files, _ = parse_output("</think>" + block("a.js", "1"))
    assert [p for p, _ in files] == ["a.js"]

@case
def crlf_answers_parse():
    assert split_file_blocks((CR + NL).join(["=== FILE: a.js ===", "x", "=== END FILE ===", ""])) == [("a.js", "x")]

@case
def forgotten_end_marker_does_not_glue_files():
    text = NL.join(["=== FILE: a.js ===", "A", "=== FILE: b.js ===", "B", "=== END FILE ==="])
    assert split_file_blocks(text) == [("a.js", "A"), ("b.js", "B")]

@case
def end_of_file_spelling_closes_a_block():
    text = NL.join(["=== FILE: a.js ===", "A", "=== END OF FILE ===", "=== FILE: b.js ===", "B", "=== END FILE ==="])
    assert split_file_blocks(text) == [("a.js", "A"), ("b.js", "B")]

@case
def truncated_last_block_is_dropped():
    assert split_file_blocks(block("a.js", "A") + NL + block("b.js", "half", end=False)) == [("a.js", "A")]

@case
def api_paths_stay_distinct_in_signatures():
    assert failure_signature(["Error: GET /api/notes/5 returned 404"]) != failure_signature(["Error: GET /api/users/5 returned 404"])

@case
def run_to_run_noise_is_ignored():
    first = ["  1) a.spec.js:4 > create (5.2s)", "Error: Outsider 1789883827427 is shown, but nothing visible"]
    second = ["  1) a.spec.js:4 > create (7.9s)", "Error: Outsider 1789883979386 is shown, but nothing visible"]
    assert failure_signature(first) == failure_signature(second)

@case
def timeouts_and_counts_are_part_of_the_signature():
    assert failure_signature(["Timeout 4000ms exceeded"]) != ""
    assert failure_signature(["× item failed"] * 3) != failure_signature(["× item failed"])

@case
def a_dropped_connection_reported_as_400_is_retried():
    from agent.llm import is_transport_error

    assert is_transport_error('{"error":{"code":"proxy_error","message":"Post ...: read: connection reset by peer"}}')
    assert not is_transport_error('{"error":{"message":"max_tokens is too large","type":"invalid_request_error"}}')

@case
def rewriting_a_shared_file_brings_back_the_specs_that_depend_on_it():
    import os
    import tempfile
    import time

    import main as factory_module

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        factory = factory_module.Factory.__new__(factory_module.Factory)
        factory.project_dir, factory.green_specs, factory.file_specs = root, [], {}
        for name in ("Header.tsx", "Orders.tsx", "Untracked.tsx"):
            (root / name).write_text("x", encoding="utf-8")
        for index in range(1, 5):
            factory.remember_green_batch([], [f"e2e/early{index}.spec.js"])
        factory.remember_green_batch(["Orders.tsx"], ["e2e/orders.spec.js"])
        before = {name: (root / name).read_bytes() for name in ("Header.tsx", "Orders.tsx", "Untracked.tsx")}
        os.utime(root / "Orders.tsx", (time.time() + 60, time.time() + 60))
        (root / "New.tsx").write_text("new", encoding="utf-8")
        assert factory.regression_specs(before, ["e2e/new.spec.js"]) == []
        (root / "Orders.tsx").write_text("y", encoding="utf-8")

        assert factory.regression_specs(before, ["e2e/new.spec.js"]) == ["e2e/orders.spec.js", "e2e/early1.spec.js", "e2e/early2.spec.js", "e2e/early3.spec.js", "e2e/early4.spec.js"]
        (root / "Orders.tsx").write_text("x", encoding="utf-8")
        (root / "Untracked.tsx").write_text("z", encoding="utf-8")
        assert factory.regression_specs(before, ["e2e/early1.spec.js"]) == ["e2e/early2.spec.js", "e2e/early3.spec.js", "e2e/early4.spec.js", "e2e/orders.spec.js"]

        from agent.settings import SETTINGS
        assert SETTINGS["audit_llm"] == "always"

@case
def a_red_batch_that_broke_the_foundation_is_put_back():
    import tempfile

    import main as factory_module
    from agent.verify import StepResult

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "src").mkdir(parents=True)
        app = root / "backend" / "src" / "app.js"
        app.write_text("app.use('/api', viewTracking());", encoding="utf-8")
        factory = factory_module.Factory.__new__(factory_module.Factory)
        factory.project_dir, factory.green_specs, factory.file_specs, factory.restore_failures = root, ["backend/test-e2e/REQ-1.spec.js"], {}, 0
        factory.record_run = lambda specs, ok, output: None
        before = factory.snapshot_sources()
        app.write_text("app.use('/api', viewTracking);", encoding="utf-8")
        saved = (factory_module.build_frontend, factory_module.run_e2e)
        runs = []
        try:
            factory_module.build_frontend = lambda project_dir, log=print: StepResult(True, "")
            factory_module.run_e2e = lambda project_dir, specs, log=print: runs.append(specs) or StepResult(False, "  1) test-e2e/REQ-1.spec.js › hangs")
            assert factory.red_batch_broke_foundation("REQ-7", "test_defect", before) is True

            assert app.read_text(encoding="utf-8") == "app.use('/api', viewTracking());" and runs == [["backend/test-e2e/REQ-1.spec.js"]] * 2

            app.write_text("app.use('/api', viewTracking);", encoding="utf-8")
            factory_module.run_e2e = lambda project_dir, specs, log=print: (
                StepResult(False, "  1) test-e2e/REQ-1.spec.js › interference") if len(runs.append(specs) or runs) % 2 else StepResult(True, "E2E PASSED"))
            runs.clear()
            assert factory.red_batch_broke_foundation("REQ-7", "budget", before) is False and len(runs) == 2
            assert app.read_text(encoding="utf-8") == "app.use('/api', viewTracking);"

            factory_module.build_frontend = lambda project_dir, log=print: StepResult(False, "error TS2304")
            assert factory.red_batch_broke_foundation("REQ-7", "budget", before) is True
            assert app.read_text(encoding="utf-8") == "app.use('/api', viewTracking());"
            factory_module.build_frontend = lambda project_dir, log=print: StepResult(True, "")

            app.write_text("app.use('/api', other());", encoding="utf-8")
            factory_module.run_e2e = lambda project_dir, specs, log=print: StepResult(True, "E2E PASSED")
            assert factory.red_batch_broke_foundation("REQ-7", "budget", before) is False
            assert app.read_text(encoding="utf-8") == "app.use('/api', other());"

            app.write_text("app.use('/api', viewTracking());", encoding="utf-8")
            runs.clear()
            factory_module.run_e2e = lambda project_dir, specs, log=print: runs.append(specs) or StepResult(False, "")
            assert factory.red_batch_broke_foundation("REQ-7", "budget", before) is False and runs == []
        finally:
            factory_module.build_frontend, factory_module.run_e2e = saved

@case
def failing_specs_are_read_from_the_failure_lines_only():
    import main as factory_module

    output = NL.join([
        "Running 5 tests using 1 worker",
        "  ✓  1 test-e2e/REQ-1.1-2.spec.js:4:1 › REQ-1.1: home (1.2s)",
        "  ✘  2 test-e2e/REQ-2.3.spec.js:9:1 › REQ-2.3.1: Delete (10.0s)",
        "  1) test-e2e/REQ-2.3.spec.js:9:1 › REQ-2.3.1: Delete ───────",
        "    Error: An outsider who only read the requirement could not find anything to click: Trash.",
    ])
    assert factory_module.failed_spec_names(output) == {"REQ-2.3.spec.js"}
    assert factory_module.failed_spec_names("Error: browserType.launch: crashed") == set()

@case
def the_servers_own_errors_reach_the_implementer():
    from agent.verify import server_errors_note

    quiet = NL.join(["> backend@1.0.0 start", "> node src/index.js", "Backend listening at http://127.0.0.1:3101"])
    assert server_errors_note(quiet) == ""
    assert server_errors_note(NL.join(["vite build: 0 errors, 2 warnings", "undefined is fine here"])) == ""
    trace = ["TypeError: run is not a function", "    at login (/app/backend/src/routes/auth.js:41:11)"]
    other = ["TypeError: run is not a function", "    at logout (/app/backend/src/routes/auth.js:87:5)"]
    noisy = NL.join(["Backend listening at http://127.0.0.1:3101"] + trace * 30 + ["GET /api/me 401"] + other)
    note = server_errors_note(noisy)
    assert "APPLICATION SERVER ERRORS" in note and "listening" not in note and "GET /api/me" not in note
    assert note.count("auth.js:41:11") == 1 and "occurred 30 times" in note
    assert "auth.js:87:5" in note
    many = NL.join(f"Error: failure in module {i}{NL}    at f (/app/m{i}.js:{i}:1)" for i in range(400))
    capped = server_errors_note(many)
    assert len(capped) < 9600 and "m399.js:399:1" in capped and "m0.js:0:1" not in capped

@case
def failing_specs_are_recognised_in_output_as_the_platform_delivers_it():
    import main as factory_module
    from agent.fsutil import strip_terminal_codes

    captured = (ROOT / "selftests" / "fixtures" / "playwright-line-reporter-platform.txt").read_text(encoding="utf-8")
    assert "\x1b[" in captured
    assert factory_module.failed_spec_names(captured) == {"REQ-6.spec.js"}
    assert "\x1b" not in strip_terminal_codes(captured) and "1) test-e2e/REQ-6.spec.js" in strip_terminal_codes(captured)

@case
def the_spec_reviewer_sees_the_requirement_only_and_reports_quoted_problems():
    from agent.outsider import review_acceptance
    from agent.test_author import AuthoredFile

    class Reviewer:
        def __init__(self, answer):
            self.answer, self.asked = answer, None

        def chat(self, messages, **options):
            self.asked = (messages, options)
            return {"content": self.answer}

    node = {"id": "REQ-7", "name": "Restore", "description": "Archive and restore records. Every record starts as an active record in the list. Seed data: active record \"Invoice 1042\".", "scenarios": []}
    spec = AuthoredFile(path="backend/test-e2e/REQ-7.spec.js", content='await click(page, "Archive"); await see(page, "Invoice 1042");')
    reviewer = Reviewer("Some thinking first.\nPROBLEM: SEED STATE | requirement: \"starts as an active record in the list\" | spec: \"await click(page, \\\"Archive\\\")\" | fix: archive it first\nfine otherwise")
    problems = review_acceptance(reviewer, node, spec, "REQ-7", log=lambda message: None)
    assert problems.startswith("PROBLEM: SEED STATE") and "fine otherwise" not in problems
    messages, options = reviewer.asked
    assert "active record" in messages[1]["content"] and "Invoice 1042" in messages[1]["content"]
    assert "contract" not in messages[1]["content"].lower() and options["thinking"] is True
    assert review_acceptance(Reviewer("OK"), node, spec, "REQ-7", log=lambda message: None) == ""
    short = Reviewer("PROBLEM: SEED STATE | requirement: \"active record\" | spec: \"await click(page, \\\"Archive\\\")\" | fix: x")
    assert review_acceptance(short, node, spec, "REQ-7", log=lambda message: None) == ""

    invented = Reviewer("PROBLEM: ROLE / NAME | requirement: \"the Approve link\" | spec: \"await click(page, \\\"Archive\\\")\" | fix: use a link")
    assert review_acceptance(invented, node, spec, "REQ-7", log=lambda message: None) == ""

    node["reference_requirements"] = "[REQ-2.2] Log In: activates the `Login` button inside the `Login form`."
    other_leaf = Reviewer("PROBLEM: NAME | requirement: \"the `Login` button inside the `Login form`\" | spec: \"await see(page, \\\"Invoice 1042\\\")\" | fix: x")
    assert review_acceptance(other_leaf, node, spec, "REQ-7", log=lambda message: None).startswith("PROBLEM: NAME")
    ghost_line = Reviewer("PROBLEM: NAME | requirement: \"active record\" | spec: \"await click(page, \\\"Log in\\\")\" | fix: x")
    assert review_acceptance(ghost_line, node, spec, "REQ-7", log=lambda message: None) == ""

@case
def a_red_run_is_booked_per_case_on_output_as_the_platform_delivers_it():
    import main as factory_module

    captured = (ROOT / "selftests" / "fixtures" / "playwright-line-reporter-platform.txt").read_text(encoding="utf-8")
    booked = {}
    factory = factory_module.Factory.__new__(factory_module.Factory)
    factory.test_ids_by_file = {"backend/test-e2e/REQ-6.spec.js": ["T-6.1", "T-6.2"], "backend/test-e2e/REQ-5.spec.js": ["T-5.1"]}
    factory.test_titles = {"T-6.1": "REQ-6.1: Items and styling", "T-6.2": "REQ-6.2: Collapsible Sidebar", "T-5.1": "REQ-5.1: Toggle between list and grid views"}
    factory.trace = type("Trace", (), {"set_test_status": staticmethod(lambda test_id, passed: booked.__setitem__(test_id, passed))})()
    factory.record_run(["backend/test-e2e/REQ-6.spec.js", "backend/test-e2e/REQ-5.spec.js"], False, captured)
    assert booked.get("T-6.1") is False and booked.get("T-5.1") is True

@case
def a_spec_calling_a_helper_that_does_not_exist_is_stopped_before_implementation():
    from agent.testrun import outsider_undefined_calls

    head = 'const { test } = require("@playwright/test");' + NL + 'const { click, fill } = require("../e2e-support/outsider");' + NL
    broken = head + 'test("REQ-1: search", async ({ page }) => { await fill(page, "Search", "x"); await press(page, "Enter"); });'
    problems = outsider_undefined_calls("backend/test-e2e/REQ-1.spec.js", broken)
    assert problems and "press" in problems[0] and "import it" in problems[0]
    wrong_import = 'const { click, typeInto } = require("../e2e-support/outsider");' + NL + 'await click(page, "Go");'
    assert "typeInto" in outsider_undefined_calls("backend/test-e2e/REQ-1.spec.js", wrong_import)[0]
    good = head.replace("click, fill", "click, fill, press") + 'async function login(page) { await click(page, "Login"); }' + NL + 'await login(page); await press(page, "Enter");'
    assert outsider_undefined_calls("backend/test-e2e/REQ-1.spec.js", good) == []
    assert outsider_undefined_calls("frontend/tests/REQ-1.test.tsx", broken) == []

    spec = "backend/test-e2e/REQ-1.spec.js"
    pw = "const { test, expect, devices } = require('@playwright/test');" + NL
    helper = lambda names: "const { " + names + " } = require('../e2e-support/outsider');" + NL
    assert outsider_undefined_calls(spec, pw + helper("item: findItem") + "await findItem(page, 'x');") == []
    assert "click" in outsider_undefined_calls(spec, pw + helper("click: tap") + "await click(page, 'x');")[0]
    assert outsider_undefined_calls(spec, pw + helper("see") + "await see(page, 'x'); await page.goto('/');") == []
    assert "press" in outsider_undefined_calls(spec, pw + helper("see") + "await Promise.all([press(page, 'Enter')]);")[0]
    assert "ghost" in outsider_undefined_calls(spec, pw + helper("see") + "// const ghost = 1;" + NL + "await ghost();")[0]
    assert outsider_undefined_calls(spec, pw + helper("see") + "throw new Error('await ghost(');") == []
    assert outsider_undefined_calls(spec, pw + helper("see") + "const run = async (page, step) => { await step(page); };") == []

@case
def a_later_implementer_of_the_same_batch_sees_the_batchs_own_files():
    import tempfile

    from agent.codegen import CodegenImplementer

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "src" / "routes").mkdir(parents=True)
        own = root / "backend" / "src" / "routes" / "catalogue.js"
        own.write_text("module.exports = 1;" + "x" * 200, encoding="utf-8")
        for index in range(40):
            (root / "backend" / "src" / f"filler{index}.js").write_text("y" * 3000, encoding="utf-8")
        blind = CodegenImplementer(None, root, lambda: None, log=lambda message: None)
        blind.project_files([], repair=True)
        seeing = CodegenImplementer(None, root, lambda: None, log=lambda message: None, show=["backend/src/routes/catalogue.js"])
        seeing.project_files([], repair=True)
        assert "backend/src/routes/catalogue.js" not in seeing._omitted and "catalogue.js ---" in seeing._volatile_sources

@case
def a_fix_plan_brings_the_files_it_names_into_view():
    import tempfile

    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "src" / "routes").mkdir(parents=True)
        (root / "backend" / "src" / "routes" / "cart.js").write_text("module.exports = 1;", encoding="utf-8")
        model = _ScriptedModel("backend/src/routes/cart.js -- return the item that was added")
        outcomes = iter([TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 > REQ-9: add"), TestOutcome(True, "E2E PASSED")])
        implementer = CodegenImplementer(model, root, lambda: next(outcomes), log=lambda message: None)
        implementer.implement({"id": "REQ-9", "name": "n"}, {}, TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 > REQ-9: add"))
        assert "backend/src/routes/cart.js" in implementer._force_include

@case
def a_card_opens_from_anywhere_not_only_its_title():
    from agent import conventions
    en = " ".join(str(v) for v in vars(conventions).values() if isinstance(v, str))
    assert "The REST of such an item opens it too" in en and "IGNORE a click" in en and ".closest(" in en
    assert "stopPropagation" not in en.split("The REST of such an item", 1)[1][:600]

@case
def a_batch_that_is_already_green_is_not_given_up():
    import tempfile

    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome

    class Model:
        last_finish_reason, model = "stop", "glm-test"
        calls = 0

        def chat(self, messages, **options):
            Model.calls += 1
            return {"content": "All tests already pass; no changes needed."}

    with tempfile.TemporaryDirectory() as tmp:
        green = CodegenImplementer(Model(), Path(tmp), lambda: TestOutcome(True, "PASSED"), log=lambda message: None)
        result = green.implement({"id": "REQ-5", "name": "n"}, {}, TestOutcome(True, "PASSED"))
        assert result.status == "passed" and result.last_check == "" and Model.calls == 1

        Model.calls = 0
        red = CodegenImplementer(Model(), Path(tmp), lambda: TestOutcome(False, "E2E FAILED"), log=lambda message: None, max_rounds=1)
        assert red.implement({"id": "REQ-5", "name": "n"}, {}, TestOutcome(False, "E2E FAILED")).status != "passed"

@case
def a_test_defect_that_only_asks_for_files_does_not_leave_the_implementer():
    import tempfile

    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome

    class Model:
        last_finish_reason, model = "stop", "glm-test"

        def __init__(self):
            self.answers = []

        def chat(self, messages, **options):
            tag = options.get("tag", "")
            if ":diagnose" in tag:
                return {"content": "frontend/src/Page.tsx -- render the list"}
            self.answers.append(tag)
            if len(self.answers) == 2:
                return {"content": "=== TEST DEFECT ===" + NL + "Not a test defect: please show backend/src/routes/big.js in full next round." + NL + "=== END TEST DEFECT ==="}
            return {"content": block("frontend/src/Page.tsx", "export default 1;")}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "src" / "routes").mkdir(parents=True)
        (root / "backend" / "src" / "routes" / "big.js").write_text("z" * (__import__("agent.codegen", fromlist=["x"]).MAX_FILE_CHARS + 6000), encoding="utf-8")
        outcomes = iter([TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 > REQ-9: x"), TestOutcome(True, "E2E PASSED")])
        implementer = CodegenImplementer(Model(), root, lambda: next(outcomes), log=lambda message: None)
        result = implementer.implement({"id": "REQ-9", "name": "n"}, {}, TestOutcome(False, "red"))
        assert result.status == "passed" and "backend/src/routes/big.js" in implementer._force_include

@case
def the_outsider_reads_the_other_requirements_when_it_repeats_their_steps():
    import main
    from agent import outsider

    tree = {"id": "ROOT", "children": [
        {"id": "REQ-2", "children": [{"id": "REQ-2.2", "name": "Log In", "description": "Log in.", "scenarios": [
            {"name": "s", "steps": [{"keyword": "WHEN", "content": "The user activates the `Login` button inside the `Login form`."}]}]}]},
        {"id": "REQ-7", "children": [{"id": "REQ-7.1", "name": "Recent", "description": "Recently viewed.", "scenarios": [
            {"name": "s", "steps": [{"keyword": "GIVEN", "content": "The user has logged in."}]}]}]},
    ]}
    digest = main.requirement_digest(tree)
    assert "[REQ-2.2]" in digest and "`Login form`" in digest and "[REQ-7.1]" in digest

    class Model:
        last_finish_reason = "stop"

        def __init__(self):
            self.prompts = []

        def chat(self, messages, **options):
            self.prompts.append(messages[1]["content"])
            return {"content": ""}

    model = Model()
    node = {"id": "REQ-7.1", "name": "Recent", "description": "Recently viewed.", "reference_requirements": digest}
    outsider.author_acceptance(model, node, "REQ-7.1", log=lambda message: None)
    prompt = model.prompts[0]
    assert prompt.index("`Login form`") < prompt.index("Requirement REQ-7.1")

@case
def plan_paths_are_normalised_not_stripped_character_by_character():
    from agent.codegen import plan_paths

    found = plan_paths("edit ./frontend/src/pages/A.tsx, backend\\src\\app.js and ./.env.json; not ../x.js nor /etc/y.js")
    assert found == ["frontend/src/pages/A.tsx", "backend/src/app.js", ".env.json"]

@case
def a_plan_that_names_fixed_files_is_told_so_and_does_not_force_them():
    import tempfile

    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "src" / "database").mkdir(parents=True)
        (root / "backend" / "test-e2e").mkdir(parents=True)
        (root / "backend" / "src" / "database" / "seed_db.js").write_text("// plumbing", encoding="utf-8")
        (root / "backend" / "test-e2e" / "REQ-9.spec.js").write_text("// spec", encoding="utf-8")
        (root / "backend" / "src" / "routes").mkdir(parents=True)
        (root / "backend" / "src" / "routes" / "cart.js").write_text("module.exports = 1;", encoding="utf-8")
        model = _ScriptedModel("backend/src/database/seed_db.js -- seed the item" + NL + "backend/test-e2e/REQ-9.spec.js -- wait longer"
                               + NL + "backend/src/routes/cart.js -- return it")
        red = TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 > REQ-9: add")
        outcomes = iter([red, TestOutcome(True, "E2E PASSED")])
        implementer = CodegenImplementer(model, root, lambda: next(outcomes), log=lambda message: None)
        implementer.implement({"id": "REQ-9", "name": "n"}, {"backend/test-e2e/REQ-9.spec.js": "// spec"}, red)
        assert implementer._force_include == ["backend/src/routes/cart.js"]
        write_prompt = [text for tag, _, text in model.calls if ":repair" in tag][0]
        assert "cannot be changed" in write_prompt and "seed_db.js" in write_prompt.split("cannot be changed")[0].rsplit("The plan names", 1)[1]

@case
def files_shown_whole_share_one_budget_and_appear_once():
    import tempfile

    from agent.codegen import FORCED_CHARS, MAX_FILE_CHARS, CodegenImplementer

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "src" / "routes").mkdir(parents=True)
        (root / "backend" / "src" / "app.js").write_text("// core app" + NL + "a" * 20000, encoding="utf-8")
        size = MAX_FILE_CHARS + 1
        names = [f"backend/src/routes/r{index}.js" for index in range(FORCED_CHARS // size + 4)]
        for name in names:
            (root / name).write_text("b" * size, encoding="utf-8")
        implementer = CodegenImplementer(None, root, lambda: None, log=lambda message: None, show=["backend/src/app.js"] + names)
        stable = implementer.project_files([], repair=True)
        prompt = stable + implementer._volatile_sources
        assert prompt.count("--- backend/src/app.js ---") == 1
        whole = [name for name in names if name in implementer._shown]
        assert whole and len(whole) < len(names) and size * (len(whole) + 1) <= FORCED_CHARS + 20100 + size
        assert all(name in implementer._omitted for name in names if name not in whole)

    from agent import conventions
    assert MAX_FILE_CHARS >= 27209 and FORCED_CHARS >= 27209 + 26527 + 19591
    assert "keep every source file under about 12000 characters" in conventions.PROJECT_CONVENTIONS

    assert "A named seed record is ONE record" in conventions.PROJECT_CONVENTIONS and "that account holds the role in the seed" in conventions.PROJECT_CONVENTIONS

@case
def a_file_outside_src_is_not_rewritten_blind_and_is_shown_next_round():
    import tempfile

    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome

    class Model:
        last_finish_reason, model = "stop", "glm-test"

        def chat(self, messages, **options):
            if ":diagnose" in options.get("tag", ""):
                return {"content": "frontend/src/Page.tsx -- render the list"}
            return {"content": block("frontend/index.html", "<title>guess</title>") + NL + block("frontend/src/Page.tsx", "export default 1;")}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "frontend" / "src").mkdir(parents=True)
        page = root / "frontend" / "index.html"
        page.write_text("<div id=root></div><script src=/src/main.tsx></script>", encoding="utf-8")
        outcomes = iter([TestOutcome(True, "E2E PASSED")])
        implementer = CodegenImplementer(Model(), root, lambda: next(outcomes), log=lambda message: None)
        implementer.implement({"id": "REQ-9", "name": "n"}, {}, TestOutcome(False, "red"))
        assert "guess" not in page.read_text(encoding="utf-8") and "frontend/index.html" in implementer._force_include
        implementer.project_files([], repair=True)
        assert "frontend/index.html" in implementer._shown

@case
def a_real_test_defect_that_mentions_a_source_file_reaches_the_author():
    import tempfile

    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome

    class Model:
        last_finish_reason, model = "stop", "glm-test"

        def chat(self, messages, **options):
            return {"content": "=== TEST DEFECT ===" + NL + "The spec expects `see(page, ` + `Saved` although big.js answers 201 and the requirement says \"it shows Created\"." + NL + "=== END TEST DEFECT ==="}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "src" / "routes").mkdir(parents=True)
        (root / "backend" / "src" / "routes" / "big.js").write_text("z" * (__import__("agent.codegen", fromlist=["x"]).MAX_FILE_CHARS + 6000), encoding="utf-8")
        implementer = CodegenImplementer(Model(), root, lambda: TestOutcome(False, "red"), log=lambda message: None)
        result = implementer.implement({"id": "REQ-9", "name": "n", "description": "After saving it shows Created."},
                                       {"backend/test-e2e/REQ-9.spec.js": 'await see(page, "Saved");'}, TestOutcome(False, "red"))
        assert result.status == "test_defect"

@case
def unchanged_failures_ask_for_evidence_before_blaming_the_spec():
    from agent.testrun import TestOutcome

    red = TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 > REQ-9: opens" + NL + "Error: GET /api/items 500")

    model = _ScriptedModel("backend/src/database/init_db.js -- add the missing column")
    result = _implement_with(model, [red] * 12)
    assert result.status != "test_defect"
    demanded = [content for tag, _, content in model.calls if ":diagnose" in tag and "Two repair rounds have left" in content]
    assert demanded, "after two unchanged rounds the next diagnosis must be asked for evidence"

    model = _ScriptedModel(["backend/src/pages/Books.tsx -- first idea"] * 3 + ['SPEC: "unique link or button named" but the spec demands `role: ` only'])
    result = _implement_with(model, [red] * 12)
    assert result.status == "test_defect" and "unique link or button" in result.detail

@case
def an_api_chain_that_never_answers_is_caught():
    import http.server
    import threading

    from agent.fsutil import api_answers

    class Answers(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    class Hangs(Answers):
        def do_GET(self):
            time.sleep(3)

    class WarmsUp(Answers):
        calls = 0

        def do_GET(self):
            WarmsUp.calls += 1
            if WarmsUp.calls == 1:
                time.sleep(3)
            super().do_GET()

    import time
    servers = []
    for handler in (Answers, Hangs, WarmsUp):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
    try:
        assert api_answers(f"http://127.0.0.1:{servers[0].server_port}", seconds=1.0) is True
        assert api_answers(f"http://127.0.0.1:{servers[1].server_port}", seconds=1.0, attempts=2) is False
        assert api_answers(f"http://127.0.0.1:{servers[2].server_port}", seconds=1.0, attempts=3) is True
    finally:
        for server in servers:
            server.shutdown()
    from main import failed_spec_names
    from agent.fsutil import API_HANG_NOTE

    report = "E2E FAILED" + NL + "No spec was run." + NL + API_HANG_NOTE.format(path="/api/x", attempts=3, seconds=5)
    assert failed_spec_names(report) == set()

@case
def a_spec_that_only_presses_keys_still_uses_the_helper():
    body = 'const { press } = require("../e2e-support/outsider");' + NL + 'await press(page, "Enter");'
    assert parse_outsider_spec(block("backend/test-e2e/R.spec.js", body), "R") is not None

@case
def a_page_must_be_complete_when_it_opens():
    from agent import codegen, outsider
    from agent.conventions import PROJECT_CONVENTIONS

    helper = outsider.HELPER_JS

    for verb in ("click", "fill", "check", "choose", "hover", "see", "seeOnce"):
        body = helper.split(f"async function {verb}(")[1].split("\nasync function ")[0]
        assert "takePending(page)" in body and "presentNow(" in body and "judgeLate(" in body, verb
    assert "noteNavigation(page, before, clickedAt, loadsBefore)" in helper.split("async function click(")[1].split("\nasync function ")[0]
    assert "GRACE_MS" not in helper
    assert "new WeakMap()" in helper and "if (pathOf(after) === pathOf(before)) return;" in helper
    assert "takePending" not in helper.split("async function press(")[1].split("\n}")[0]
    store = (ROOT / "template" / "frontend" / "src" / "api" / "store.ts").read_text(encoding="utf-8")
    for name in ("export function peek", "export function put", "export function load", "export async function refresh",
                 "export function preload", "export function useData", "export function reset"):
        assert name in store, name

    assert "status === 401 && path === SESSION_PATH" in store and "load(path, true)" in store.split("export async function refresh")[1]
    assert "if (mine !== generation)" in store and "throw new Superseded(path)" in store
    assert "running.clear();" in store.split("export function reset")[1].split("\n}")[0]
    assert "const mine = generation;" in store.split("export function load")[1]
    assert "request.open('GET', `/api${path}`, false)" in store
    assert "frontend/src/api/store.ts" in codegen.READ_ONLY_FILES and "frontend/src/api/store.ts" in codegen.CORE_FILES

    main_tsx = (ROOT / "template" / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
    assert "unstable_useTransitions={false}" in main_tsx and "useTransitions: false" in main_tsx
    assert "frontend/src/main.tsx" in codegen.READ_ONLY_FILES and "frontend/src/main.tsx" in codegen.CORE_FILES
    config = (ROOT / "template" / "backend" / "playwright.config.js").read_text(encoding="utf-8")
    assert "timeout: 10000" in config
    click_body = helper.split("async function click(")[1].split("\nasync function ")[0]
    assert "SHORT_EXCLUSIVE_MS = 300" in click_body and "presentNow(clickable(p), 'enabled')" in click_body
    for text in (PROJECT_CONVENTIONS,):
        assert "useData" in text and "preload(" in text and "put('/me'" in text

@case
def the_outsider_can_tick_a_checkbox():
    from agent import outsider

    assert "check," in outsider.HELPER_JS.split("module.exports")[1] and "async function check(" in outsider.HELPER_JS
    assert "await setState(page, item, value" in outsider.HELPER_JS.split("async function fill(")[1].split("async function check(")[0]
    for prompt in (outsider.SYSTEM_PROMPT,):
        assert "fill, check, choose" in prompt and 'await check(page, { role: "checkbox"' in prompt
    body = 'const { check } = require("../e2e-support/outsider");' + NL + 'await check(page, { role: "checkbox", name: "Urgent" });'
    assert parse_outsider_spec(block("backend/test-e2e/R.spec.js", body), "R") is not None

@case
def helper_use_is_read_from_the_code_not_from_comments():
    head = 'const { press, click } = require("../e2e-support/outsider");' + NL
    nested = head + 'await Promise.all([press(page, "Enter")]);'
    assert parse_outsider_spec(block("backend/test-e2e/R.spec.js", nested), "R") is not None
    commented = head + '// await click(page, "Go")' + NL + 'const note = "await click(page, x)";' + NL + 'await page.goto("/");'
    assert parse_outsider_spec(block("backend/test-e2e/R.spec.js", commented), "R") is None

@case
def a_huge_requirement_keeps_every_leaf_in_the_digest():
    import main

    leaves = [{"id": f"REQ-{index}", "name": f"Feature {index}", "description": "x" * 400} for index in range(1, 401)]
    digest = main.requirement_digest({"id": "ROOT", "children": [{"id": "REQ", "children": leaves}]}, max_chars=30000)
    assert len(digest) <= 30000 and "[REQ-1]" in digest and "[REQ-400]" in digest

@case
def an_answer_that_only_rewrites_tests_is_not_a_hand_over():
    from agent.testrun import TestOutcome

    class Model(_ScriptedModel):
        def chat(self, messages, **options):
            if ":gen0" in options.get("tag", ""):
                self.calls.append((options.get("tag", ""), options.get("thinking"), messages[1]["content"]))
                return {"content": block("backend/test-e2e/REQ-9.spec.js", "// loosened")}
            return super().chat(messages, **options)

    model = Model("frontend/src/pages/HomePage.tsx -- add the link")
    result = _implement_with(model, [TestOutcome(True, "E2E PASSED")])
    assert result.status == "passed"

    assert [tag for tag, _, _ in model.calls][:2] == ["codegen:REQ-9:gen0", "codegen:REQ-9:gen0"]

@case
def a_different_spec_claim_on_the_same_failures_is_challenged_again():
    from agent.testrun import TestOutcome

    first = 'SPEC: "Open the catalogue" does not ask for `await click(page, { role` at all'
    second = 'SPEC: "unique link or button named" but the spec demands `role: ` only'
    model = _ScriptedModel([first, "frontend/src/pages/HomePage.tsx -- add the button", second, "frontend/src/pages/HomePage.tsx -- rename it"])
    red = TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 > REQ-9: opens")
    result = _implement_with(model, [red, red, TestOutcome(True, "E2E PASSED")])
    assert result.status == "passed"
    challenges = [content for tag, _, content in model.calls if ":diagnose" in tag and "You answered that the acceptance spec is wrong" in content]
    assert len(challenges) == 2, len(challenges)

@case
def a_word_that_merely_occurs_in_the_spec_is_no_evidence():
    from agent.codegen import spec_evidence

    specs = {"backend/test-e2e/REQ-9.spec.js": 'await click(page, "Login"); await see(page, "Welcome back");'}
    requirement = "The user activates the `Log in` button."
    assert not spec_evidence('SPEC: "Login" is wrong', requirement, specs, "")
    assert not spec_evidence('SPEC: "Login" vs "Login"', requirement, specs, "")
    assert spec_evidence('SPEC: the spec clicks "Login" but the requirement says "the `Log in` button"'.replace("`", ""),
                         requirement.replace("`", ""), specs, "")
    assert spec_evidence('SPEC: "Welcome back" raises "strict mode violation"', requirement, specs, "Error: strict mode violation: 2 elements")

@case
def an_unproven_test_defect_from_the_writing_call_is_not_a_hand_over():
    from agent.testrun import TestOutcome

    class Model(_ScriptedModel):
        def chat(self, messages, **options):
            if ":gen0" in options.get("tag", ""):
                self.calls.append((options.get("tag", ""), options.get("thinking"), messages[1]["content"]))
                return {"content": "=== TEST DEFECT ===" + NL + "The spec is ambiguous." + NL + "=== END TEST DEFECT ==="}
            return super().chat(messages, **options)

    model = Model("frontend/src/pages/HomePage.tsx -- add the link")
    result = _implement_with(model, [TestOutcome(True, "E2E PASSED")])
    assert result.status == "passed"
    assert any("was not accepted" in content for tag, _, content in model.calls if ":repair" in tag)

@case
def a_spec_verdict_that_quotes_nothing_verifiable_is_not_a_hand_over():
    from agent.testrun import TestOutcome

    model = _ScriptedModel(["SPEC: the spec is wrong about the button", "frontend/src/pages/HomePage.tsx -- add it"])
    red = TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 > REQ-9: opens")
    result = _implement_with(model, [red, TestOutcome(True, "E2E PASSED")])
    assert result.status == "passed"

@case
def a_test_repair_may_only_change_the_files_it_was_given():
    from agent.test_author import AuthoredFile, merge_repaired

    mine = [AuthoredFile(path="frontend/tests/REQ-2.5.test.tsx", content="old")]
    repaired = [AuthoredFile(path="frontend/tests/REQ-2.5.test.tsx", content="new"), AuthoredFile(path="frontend/tests/REQ-2.3.test.tsx", content="foreign")]
    merged = merge_repaired(mine, repaired, log=lambda message: None)
    assert [(f.path, f.content) for f in merged] == [("frontend/tests/REQ-2.5.test.tsx", "new")]

@case
def a_cut_off_that_fires_all_the_time_switches_itself_off():
    from agent.llm import LLM

    saved = (LLM.streamed, LLM.cutoffs, LLM.cutoff_disabled)
    LLM.streamed, LLM.cutoffs, LLM.cutoff_disabled = 10, 9, False
    try:
        _FakeStream.read = _FakeStream.read_all
        message, client, fake = _stream_through_client(["The issue is that " * 10] * 50, limit=600)
        assert LLM.cutoff_disabled and message["content"] == "whole answer" and client.last_finish_reason == "stop"
    finally:
        del _FakeStream.read
        LLM.streamed, LLM.cutoffs, LLM.cutoff_disabled = saved

@case
def common_runaway_answers_do_not_blow_the_fuse():
    from agent.llm import LLM

    saved = (LLM.streamed, LLM.cutoffs, LLM.cutoff_disabled)
    LLM.streamed, LLM.cutoffs, LLM.cutoff_disabled = 20, 8, False
    try:
        message, client, fake = _stream_through_client(["The issue is that " * 10] * 50, limit=600)
        assert not LLM.cutoff_disabled and client.last_finish_reason == "aborted"
    finally:
        LLM.streamed, LLM.cutoffs, LLM.cutoff_disabled = saved

@case
def the_limit_leaves_room_for_an_ordinary_analysis():
    from agent.codegen import PROSE_LIMIT_CHARS

    assert PROSE_LIMIT_CHARS >= 24000

@case
def mentioning_the_file_marker_in_an_analysis_does_not_stop_the_cut_off():
    message, client, fake = _stream_through_client(["I should output `=== FILE:` blocks but first let me think. "] + ["The issue is that " * 10] * 50, limit=600)
    assert client.last_finish_reason == "aborted"

class _FakeStream:

    def __init__(self, pieces):
        events = [json.dumps({"choices": [{"delta": {"content": piece}}]}) for piece in pieces]
        events.append(json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 7, "completion_tokens": 9}}))
        self.lines = [("data: " + event + NL).encode("utf-8") for event in events] + [b"data: [DONE]" + NL.encode()]
        self.lines_read = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        for line in self.lines:
            self.lines_read += 1
            yield line

    def read_all(self):
        body = {"choices": [{"message": {"role": "assistant", "content": "whole answer"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        return json.dumps(body).encode("utf-8")

@case
def a_request_that_times_out_twice_is_not_waited_for_again():
    import time as time_module
    import urllib.error
    import urllib.request

    from agent.llm import LLM, LLMError

    calls = []

    def hanging(request, timeout=None):
        calls.append(timeout)
        raise TimeoutError("The read operation timed out")

    original, original_sleep = urllib.request.urlopen, time_module.sleep
    urllib.request.urlopen, time_module.sleep = hanging, lambda seconds: None
    try:
        client = LLM("http://gateway.invalid/v1", "key", "glm-test", log=lambda message: None)
        try:
            client._post_with_retry({"model": "glm-test", "messages": []})
            raise AssertionError("a request that never answers must fail")
        except LLMError as exc:
            assert "2 read timeouts" in str(exc) and len(calls) == 2

        calls.clear()

        def refused(request, timeout=None):
            calls.append(timeout)
            raise ConnectionResetError("connection reset by peer")

        urllib.request.urlopen = refused
        try:
            client._post_with_retry({"model": "glm-test", "messages": []})
        except LLMError:
            pass
        assert len(calls) == 5

        calls.clear()

        def slow_connect(request, timeout=None):
            calls.append(timeout)
            raise urllib.error.URLError(TimeoutError("timed out"))

        urllib.request.urlopen = slow_connect
        try:
            client._post_with_retry({"model": "glm-test", "messages": []})
        except LLMError:
            pass
        assert len(calls) == 5
    finally:
        urllib.request.urlopen, time_module.sleep = original, original_sleep

def _stream_through_client(pieces, limit):
    import urllib.request

    from agent.llm import LLM

    fake = _FakeStream(pieces)
    original = urllib.request.urlopen
    urllib.request.urlopen = lambda request, timeout=None: fake
    try:
        client = LLM("http://gateway.invalid/v1", "key", "glm-test", log=lambda message: None)
        message = client.chat([{"role": "user", "content": "fix it"}], thinking=False, expect_marker=(("=== FILE:",), limit))
        return message, client, fake
    finally:
        urllib.request.urlopen = original

@case
def an_answer_that_is_only_analysis_is_cut_off_early():
    message, client, fake = _stream_through_client(["The issue is that " * 10] * 50, limit=600)
    assert client.last_finish_reason == "aborted" and fake.lines_read < 10 and "The issue" in message["content"]

@case
def an_answer_with_a_file_header_is_read_to_the_end():
    pieces = ["</think>=== FILE: a.js ===" + NL] + ["const x = 1;" + NL] * 200 + ["=== END FILE ==="]
    message, client, fake = _stream_through_client(pieces, limit=600)
    assert client.last_finish_reason == "stop" and fake.lines_read == len(fake.lines)
    assert split_file_blocks(message["content"])[0][0] == "a.js" and client.completion_tokens == 9

@case
def an_analysis_without_files_becomes_a_plan():
    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome

    class Decider:
        def __init__(self, answer):
            self.answer, self.asked = answer, []

        def chat(self, messages, **options):
            self.asked.append((messages, options))
            return {"content": self.answer}

    decider = Decider("backend/src/routes/notes.js -- archive sets archived=1 instead of toggling")
    note = CodegenImplementer(decider, Path("."), lambda: None, log=lambda message: None)._decision_note("R", "Hmm. Wait, maybe... " * 900, TestOutcome(False, "E2E FAILED"))
    assert "Fix plan" in note and "notes.js -- archive" in note and "=== FILE:" in note and "Wait, maybe" not in note
    prompt = decider.asked[0][0][1]["content"]
    assert len(prompt) < 18500 and decider.asked[0][1]["thinking"] is False and decider.asked[0][1]["max_tokens"] <= 1500
    fallback = CodegenImplementer(Decider(""), Path("."), lambda: None, log=lambda message: None)._decision_note("R", "the tail of the analysis", TestOutcome(False, "x"))
    assert "the tail of the analysis" in fallback and "=== FILE:" in fallback

class _ScriptedModel:

    def __init__(self, diagnosis):
        self.diagnosis, self.calls = diagnosis, []
        self.last_finish_reason, self.model = "stop", "glm-test"

    def chat(self, messages, **options):
        self.calls.append((options.get("tag", ""), options.get("thinking"), messages[1]["content"]))
        if ":diagnose" in options.get("tag", ""):
            if isinstance(self.diagnosis, list):
                return {"content": self.diagnosis.pop(0) if len(self.diagnosis) > 1 else self.diagnosis[0]}
            return {"content": self.diagnosis}
        return {"content": block("frontend/src/pages/HomePage.tsx", "export default function HomePage() { return null; }")}

REQ_TEXT = "Open the catalogue through the unique link or button named `Books`."
SPEC_TEXT = 'await click(page, { role: "link", name: "Books" });'

def _implement_with(model, outcomes):
    import tempfile

    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome

    with tempfile.TemporaryDirectory() as tmp:
        results = iter(outcomes)
        implementer = CodegenImplementer(model, Path(tmp), lambda: next(results), log=lambda message: None)
        node = {"id": "REQ-9", "name": "n", "description": REQ_TEXT}
        return implementer.implement(node, {"backend/test-e2e/REQ-9.spec.js": SPEC_TEXT}, TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 > REQ-9: opens"))

@case
def a_repair_round_thinks_first_and_writes_second():
    from agent.testrun import TestOutcome

    model = _ScriptedModel("frontend/src/pages/HomePage.tsx -- render the heading the spec looks for")
    red = TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 > REQ-9: opens" + NL + "Error: heading not found")
    result = _implement_with(model, [red, TestOutcome(True, "E2E PASSED")])
    tags = [tag for tag, _, _ in model.calls]
    assert result.status == "passed" and tags == ["codegen:REQ-9:gen0", "codegen:REQ-9:diagnose1", "codegen:REQ-9:repair1"], tags
    diagnose, write = model.calls[1], model.calls[2]
    assert diagnose[1] is True and "DIAGNOSIS ROUND" in diagnose[2] and "Fix plan" not in diagnose[2]
    assert write[1] is False and "render the heading the spec looks for" in write[2] and write[2].rstrip().endswith("`=== FILE:`.")

    shared = len(diagnose[2]) - len(diagnose[2].split("DIAGNOSIS ROUND")[1]) - len("DIAGNOSIS ROUND")
    assert write[2][:shared] == diagnose[2][:shared]

@case
def a_diagnosis_that_only_blames_the_spec_goes_to_the_test_author():
    from agent.testrun import TestOutcome

    model = _ScriptedModel('SPEC: the requirement says "unique link or button named" (a link OR button) but the spec step "role: \"link\"" accepts a link only')
    red = TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 > REQ-9: opens")
    result = _implement_with(model, [red])
    assert result.status == "test_defect" and "link OR button" in result.detail

    assert [tag for tag, _, _ in model.calls] == ["codegen:REQ-9:gen0", "codegen:REQ-9:diagnose1", "codegen:REQ-9:diagnose1"]
    assert "quote" in model.calls[2][2] and "link OR button" in model.calls[2][2]

@case
def a_challenged_spec_verdict_that_is_withdrawn_repairs_the_application():
    from agent.testrun import TestOutcome

    model = _ScriptedModel(['SPEC: "Open the catalogue" does not ask for `await click(page, { role` at all', "frontend/src/pages/HomePage.tsx -- add the missing button"])
    red = TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 > REQ-9: opens")
    result = _implement_with(model, [red, TestOutcome(True, "E2E PASSED")])
    assert result.status == "passed"
    assert [tag for tag, _, _ in model.calls][-1] == "codegen:REQ-9:repair1" and "add the missing button" in model.calls[-1][2]

@case
def the_build_contract_cannot_be_rewritten_blind():
    import tempfile

    from agent.codegen import BUILD_CONTRACT, CodegenImplementer
    from agent.testrun import TestOutcome

    class Model:
        last_finish_reason, model = "stop", "glm-test"

        def __init__(self):
            self.calls = 0

        def chat(self, messages, **options):
            self.calls += 1
            if ":diagnose" in options.get("tag", ""):
                return {"content": "frontend/src/App.tsx -- render the page"}
            if self.calls == 1:
                return {"content": block("frontend/package.json", "{}")}
            return {"content": block("frontend/src/App.tsx", "export default function App() { return null; }")}

    assert "frontend/package.json" in BUILD_CONTRACT and "backend/package.json" in BUILD_CONTRACT
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "frontend").mkdir()
        (root / "frontend" / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
        checks = []

        def check():
            checks.append(1)
            return TestOutcome(True, "E2E PASSED")

        result = CodegenImplementer(Model(), root, check, log=lambda message: None).implement({"id": "R", "name": "n"}, {}, TestOutcome(False, "red"))
        assert (root / "frontend" / "package.json").read_text(encoding="utf-8") == '{"scripts":{"build":"vite build"}}'
        assert result.status == "passed" and len(checks) == 1

@case
def a_plan_that_changed_nothing_is_reported_to_the_next_diagnosis():
    from agent.testrun import TestOutcome

    model = _ScriptedModel("frontend/src/pages/HomePage.tsx -- first idea")
    red = TestOutcome(False, "E2E FAILED" + NL + "  1) a.spec.js:3 > REQ-9: opens" + NL + "Error: heading not found")
    _implement_with(model, [red, red, TestOutcome(True, "E2E PASSED")])
    second = [content for tag, _, content in model.calls if tag == "codegen:REQ-9:diagnose2"][0]
    assert "did not change" in second and "first idea" in second

@case
def the_closing_repair_works_spec_by_spec():
    import tempfile
    import time
    import types

    import main as factory_module
    from agent.codegen import ImplementResult

    suite = NL.join(["  1) test-e2e/A.spec.js:3 > REQ-1: a", "  2) test-e2e/B.spec.js:3 > REQ-2: b"])
    runs, built = [], []

    def fake_run_e2e(project_dir, specs, log=None):
        runs.append(list(specs))
        if specs == ["backend/test-e2e/A.spec.js"]:
            return types.SimpleNamespace(ok=True, skipped=False, output="1 passed")
        if specs == ["backend/test-e2e/B.spec.js"]:
            return types.SimpleNamespace(ok=False, skipped=False, output="  1) test-e2e/B.spec.js:3 > REQ-2: b")
        return types.SimpleNamespace(ok=True, skipped=False, output="all passed")

    class FakeImplementer:
        def __init__(self, llm, project_dir, check, **options):
            built.append(options)

        def implement(self, node, tests, outcome, extra_context=""):
            assert node["id"] == "REQ-2" and "CLOSING REPAIR" in extra_context and "B.spec.js" in outcome.output
            return ImplementResult("passed", ["frontend/src/B.tsx"], "checks green", 2)

    saved = (factory_module.run_e2e, factory_module.Implementer)
    factory_module.run_e2e, factory_module.Implementer = fake_run_e2e, FakeImplementer
    try:
        with tempfile.TemporaryDirectory() as tmp:
            factory = factory_module.Factory.__new__(factory_module.Factory)
            factory.project_dir, factory.llm, factory.started, factory.time_budget, factory.large_tree = Path(tmp), None, time.time(), 10800.0, True
            factory.e2e_specs = ["backend/test-e2e/A.spec.js", "backend/test-e2e/B.spec.js"]
            factory.green_specs, factory.verify_repairs, factory.restore_failures = [], 0, 0
            factory.record_run = lambda files, ok, output: None
            factory.spec_context = {
                "backend/test-e2e/A.spec.js": {"node": {"id": "REQ-1"}, "contract": "{}", "relevant": [], "tests": {}, "members": ["REQ-1"]},
                "backend/test-e2e/B.spec.js": {"node": {"id": "REQ-2"}, "contract": "{\"b\":1}", "relevant": ["frontend/src/B.tsx"], "tests": {}, "members": ["REQ-2"]},
            }
            assert factory.repair_failing_specs(suite) == 1
    finally:
        factory_module.run_e2e, factory_module.Implementer = saved
    assert len(built) == 1 and built[0]["contract"] == "{\"b\":1}" and built[0]["relevant"] == ["frontend/src/B.tsx"]
    assert "max_rounds" not in built[0]
    assert runs[:2] == [["backend/test-e2e/A.spec.js"], ["backend/test-e2e/B.spec.js"]] and runs[-1] == factory.e2e_specs

@case
def an_interrupted_round_is_put_back():
    import tempfile

    from agent.codegen import CodegenImplementer

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        changed, created = root / "changed.js", root / "created.js"
        changed.write_bytes(b"before")
        undo = {changed: b"before", created: None}
        changed.write_bytes(b"half an answer")
        created.write_bytes(b"new file")
        CodegenImplementer(None, root, lambda: None, log=lambda message: None)._roll_back(undo, "R", 1)
        assert changed.read_bytes() == b"before" and not created.exists()

@case
def optional_repairs_do_not_earn_extra_rounds():
    from agent.codegen import EXTRA_ROUNDS, CodegenImplementer

    assert CodegenImplementer(None, Path("."), lambda: None).extra_rounds == EXTRA_ROUNDS
    assert CodegenImplementer(None, Path("."), lambda: None, max_rounds=2, extra_rounds=0).extra_rounds == 0

@case
def jest_matcher_in_a_playwright_spec_is_caught():
    problems = framework_api_misuse("backend/test-e2e/R.spec.js", 'await expect(page.getByRole("alert")).toBeInTheDocument();')
    assert problems and "jest-dom" in problems[0]

@case
def outsider_spec_must_use_the_helper():
    free = block("backend/test-e2e/R.spec.js", 'const { test } = require("@playwright/test");')
    imported_only = block("backend/test-e2e/R.spec.js", 'const { click } = require("../e2e-support/outsider");', 'await page.getByRole("button").click();')
    helper = block("backend/test-e2e/R.spec.js", 'const { click } = require("../e2e-support/outsider");', 'await click(page, "Archive");')
    assert parse_outsider_spec(free, "R") is None
    assert parse_outsider_spec(imported_only, "R") is None
    assert parse_outsider_spec(helper, "R") is not None
    by_role = block("backend/test-e2e/R.spec.js", 'const { seeOnce } = require("../e2e-support/outsider");', 'await seeOnce(page, { role: "heading", name: "Reports" });')
    assert parse_outsider_spec(by_role, "R") is not None

@case
def the_helper_teaches_and_exports_the_role_aware_calls():
    from agent import outsider

    for name in ("seeOnce", "region", "roleOf", "byRole"):
        assert name in outsider.HELPER_JS
    for prompt in (outsider.SYSTEM_PROMPT,):
        assert 'role: "button"' in prompt and "seeOnce" in prompt and "region(" in prompt

@case
def the_requirement_outranks_the_conventions():
    from agent import conventions

    english = conventions.PROJECT_CONVENTIONS
    assert "THE REQUIREMENT OUTRANKS EVERYTHING BELOW" in english and "in front as a trigger" not in english

def _leaf(identifier: str) -> dict:
    return {"id": identifier, "name": identifier, "description": "short", "scenarios": [{"name": "s", "steps": [{"keyword": "WHEN", "content": "x"}]}]}

@case
def batches_hold_at_most_three_leaves():
    tree = {"id": "ROOT", "children": [{"id": "REQ-1", "name": "folder", "children": [_leaf(f"REQ-1.{i}") for i in range(1, 5)]}]}
    batches = build_batches(tree)
    assert max(len(b["members"]) for b in batches) <= 3 and sum(len(b["members"]) for b in batches) == 4

@case
def every_leaf_lands_in_exactly_one_batch():
    tree = {"id": "ROOT", "children": [{"id": "REQ-1", "name": "folder", "children": [_leaf(f"REQ-1.{i}") for i in range(1, 8)]}]}
    members = [m for b in build_batches(tree) for m in b["members"]]
    assert members == [f"REQ-1.{i}" for i in range(1, 8)]

@case
def folding_the_tail_obeys_the_step_limit():
    def leaf(identifier: str) -> dict:
        steps = [{"keyword": "WHEN", "content": f"step {n}"} for n in range(4)]
        return {"id": identifier, "name": identifier, "description": "short", "scenarios": [{"name": "s", "steps": steps}]}

    tree = {"id": "ROOT", "children": [{"id": "REQ-1", "name": "folder", "children": [leaf("REQ-1.1"), leaf("REQ-1.2")]}]}
    assert [b["members"] for b in build_batches(tree, max_steps=6)] == [["REQ-1.1"], ["REQ-1.2"]]

@case
def neighbouring_small_folders_travel_together():
    first, second = _leaf("REQ-1.1"), _leaf("REQ-2.1")
    first["ancestor_context"], second["ancestor_context"] = "about folder one", "about folder two"
    tree = {"id": "ROOT", "children": [{"id": "REQ-1", "name": "one", "children": [first]}, {"id": "REQ-2", "name": "two", "children": [second]}]}
    batches = build_batches(tree)
    assert len(batches) == 1 and batches[0]["members"] == ["REQ-1.1", "REQ-2.1"]
    assert "folder one" in batches[0]["ancestor_context"] and "folder two" in batches[0]["ancestor_context"]

@case
def headings_of_a_foreign_language_screenshot_are_not_binding():
    notes = NL.join([
        "Visual reference notes for a.png (extracted from the screenshot). MANDATORY: ...", "HEADINGS:", "- Account",
        "Visual reference notes for b.png (extracted from the screenshot). LANGUAGE MISMATCH: ...", "HEADINGS:", "- 最近的书"])
    assert mandatory_texts(notes) == ["Account"]

@case
def a_mismatching_screenshot_does_not_silence_the_next_one():
    notes = NL.join([
        "Visual reference notes for a.png (extracted from the screenshot). MANDATORY: ...", "HEADINGS:", "- Account",
        "Visual reference notes for b.png (extracted from the screenshot). LANGUAGE MISMATCH: ...", "HEADINGS:", "- 最近的书",
        "Visual reference notes for c.png (extracted from the screenshot). MANDATORY: ...", "HEADINGS:", "- Shelves"])
    assert mandatory_texts(notes) == ["Account", "Shelves"]

@case
def script_is_told_from_the_words():
    assert script_of("Login Shelves Books Settings") == "latin"
    assert script_of("登录 书架 图书 设置") == "cjk"

@case
def only_interface_sections_say_which_language_the_ui_speaks():
    notes = NL.join([
        "HEADINGS:", "- 最近的书", "NAVIGATION:", "- 书架", "- 图书",
        "OTHER TEXTS:", "- The Pragmatic Programmer", "- Structure and Interpretation of Computer Programs",
        "LAYOUT:", "A header with a search field, a grid of cards below it."])
    kept = _interface_texts(notes)
    assert "书架" in kept and "最近的书" in kept and "Pragmatic" not in kept and "header" not in kept
    assert script_of(kept) == "cjk"

@case
def texts_of_a_foreign_language_screenshot_are_not_handed_over():
    notes = NL.join(["HEADINGS:", "- 最近的书", "NAVIGATION:", "- 书架", "- Books", "LAYOUT:", "A search field with the placeholder 搜索 on top."])
    cleaned = structure_only(notes, "latin")
    assert not any("一" <= ch <= "鿿" for ch in cleaned)
    assert cleaned.count("[text]") == 3 and "- Books" in cleaned and "HEADINGS:" in cleaned and "A search field" in cleaned
    other_way = structure_only(NL.join(["BUTTONS:", "- Save", "- 取消", "LAYOUT:", "- two buttons side by side"]), "cjk")
    assert "Save" not in other_way and "取消" in other_way and "two buttons" in other_way

@case
def playwright_matcher_in_a_component_test_is_caught():
    problems = framework_api_misuse("frontend/tests/R.test.tsx", 'expect(screen.getByRole("alert")).toHaveText("Saved");')
    assert problems and "Playwright" in problems[0]
    assert framework_api_misuse("frontend/tests/R.test.tsx", 'expect(screen.getByRole("alert")).toHaveTextContent("Saved");') == []

@case
def temporary_roots_do_not_change_the_signature():
    first = ["Error: ENOENT: no such file, open 'C:/Users/a/AppData/Local/Temp/tmp8f3k2a1x/backend/.arc-test-db/1789883827427/database.db'"]
    second = ["Error: ENOENT: no such file, open 'C:/Users/a/AppData/Local/Temp/tmpq91zzb7c/backend/.arc-test-db/1789883979386/database.db'"]
    assert failure_signature(first) == failure_signature(second)

@case
def a_resumed_run_keeps_the_work_of_the_batches_done():
    import inspect
    import tempfile
    import main as factory_module
    with tempfile.TemporaryDirectory() as tmp:
        template, out = Path(tmp) / "template", Path(tmp) / "app"
        for rel, text in {"frontend/src/App.tsx": "starter app", "backend/src/database/init_db.js": "starter seed",
                          "backend/package.json": "{}", "template.yaml": "x"}.items():
            (template / rel).parent.mkdir(parents=True, exist_ok=True)
            (template / rel).write_text(text, encoding="utf-8")
        factory_module.copy_template(template, out)
        assert (out / "frontend/src/App.tsx").read_text(encoding="utf-8") == "starter app" and not (out / "template.yaml").exists()

        (out / ".arc").mkdir()
        first = factory_module.Factory.__new__(factory_module.Factory)
        first.project_dir, first.revised_specs, first.restore_failures = out, set(), 0
        for field in factory_module.Factory.CHECKPOINT_FIELDS:
            setattr(first, field, {} if field in ("spec_context", "file_specs", "test_snapshot", "test_ids_by_file", "test_titles", "report") else [])
        first.started, first.spent_before = __import__("time").time(), 0.0
        (out / "frontend/src/App.tsx").write_text("app of batches 1-2", encoding="utf-8")
        (out / "backend/src/database/init_db.js").write_text("seed of batches 1-2", encoding="utf-8")
        (out / "frontend/src/Home.tsx").write_text("page of batch 2", encoding="utf-8")
        first.save_checkpoint("digest-1", ["REQ-1", "REQ-2"], "")
        done_state = first.snapshot_sources()
        (out / "frontend/src/App.tsx").write_text("half of batch 3", encoding="utf-8")
        (out / "frontend/src/Half.tsx").write_text("half of batch 3", encoding="utf-8")
        (out / "backend/package.json").unlink()

        second = factory_module.Factory.__new__(factory_module.Factory)
        second.project_dir, second.revised_specs, second.resumed_description, second.restore_failures = out, set(), "", 0
        second.resumed_sources = {}
        assert second.load_checkpoint("digest-1") == ["REQ-1", "REQ-2"]
        factory_module.copy_template(template, out, overwrite=False)
        assert (out / "backend/src/database/init_db.js").read_text(encoding="utf-8") == "seed of batches 1-2"
        second.restore_sources(second.resumed_sources)
        assert second.snapshot_sources() == done_state and second.restore_failures == 0

        body = inspect.getsource(factory_module.Factory.run)
        assert body.index("load_checkpoint(") < body.index("copy_template(") and "overwrite=not done_ids" in body

@case
def a_seed_the_requirement_names_must_exist():
    import main as factory_module
    text = 'Requirement REQ-4.3.1: Create Shelf\nCreate a shelf. Seed data: shelf "Shelf 4.3.1".\nSeed data: book “Book 9”.'
    assert factory_module.seed_names(text) == ["Shelf 4.3.1", "Book 9"]
    sources = {"backend/src/database/init_db.js": b"insert('Shelf 4.1'); insert('Book 9');",
               "frontend/src/App.tsx": b"'Shelf 4.3.1'"}
    assert factory_module.missing_seed_names(text, sources) == ["Shelf 4.3.1"]
    sources["backend/src/database/seed.json"] = b'{"shelves": ["Shelf 4.3.1"]}'
    assert factory_module.missing_seed_names(text, sources) == []

    assert factory_module.missing_seed_names('Seed data: note "Bob\'s list".', {"backend/a.js": b"'Bob\\'s list'"}) == []
    assert factory_module.missing_seed_names('The system contains "X".', {}) == []

    platform = ("GIVEN: The evaluation seed contains the seeded workbook `Q3 Sales`, worksheet `Sheet1`, and cell A1 value "
                "`Region`. Seed values: account `alice-dev`, email `alice.dev@example.test`, password `Valid-password-123!`. "
                "The visitor clicks “Sign in”.")

    assert factory_module.seed_names(platform) == ["Q3 Sales", "Sheet1", "alice-dev"], factory_module.seed_names(platform)
    listed = "GIVEN: Seed values: labels `bug` and `documentation`, branches `main` and `feature-search`, pull requests `Fix search`."
    assert factory_module.seed_names(listed) == ["bug", "documentation", "main", "feature-search", "Fix search"], factory_module.seed_names(listed)
    assert factory_module.missing_seed_names(platform, {"backend/src/database/init_db.js": b"'Q3 Sales' 'Sheet1' alice-dev alice.dev@example.test Valid-password-123!"}) == []

    tree = {"id": "ROOT", "children": [
        {"id": "REQ-1", "name": "Sign in", "description": "Sign in (see reference/a.png) Seed data: shelf \"Shelf 4.3.1\".",
         "scenarios": [{"name": "s", "steps": [{"keyword": "GIVEN", "content": "Seed values: account `alice-dev`, password `Valid-password-123!`. The user opens it."}]}]},
        {"id": "REQ-2", "name": "Org", "description": "x",
         "scenarios": [{"name": "s", "steps": [{"keyword": "GIVEN", "content": "Seed values: account `alice-dev`, password `Valid-password-123!`."}]}]}]}
    seeds = factory_module.seed_digest(tree)
    assert seeds.splitlines()[1:] == ['- Seed data: shelf "Shelf 4.3.1".',
                                      "- Seed values: account `alice-dev`, password `Valid-password-123!`."], seeds
    assert 'batch["seed_sentences"] = seeds' in __import__("inspect").getsource(factory_module.Factory)

    from agent.outsider import reference_block
    block = reference_block({"reference_requirements": "[REQ-1] x", "seed_sentences": "SEED SENTENCES ...\n- Seed values: account `alice-dev`."})
    assert "alice-dev" in block and "[REQ-1] x" in block

    from agent import conventions, outsider
    assert "always seeded under exactly that name" in conventions.PROJECT_CONVENTIONS

    assert "it never edits the shared" in conventions.PROJECT_CONVENTIONS
    assert "a test that edits the shared seed changes what later tests see there" in " ".join(outsider.SYSTEM_PROMPT.split())

    assert factory_module.missing_seed_names('Seed data: shelf "Shelf 4.3.1".', {"backend/a.js": b"'shelf 4.3.1'"}) == []
    import inspect
    assert "self.seed_checks.get(req_id, 0) >= 1" in inspect.getsource(factory_module.Factory.process_node)

@case
def sign_in_is_asked_for_only_where_the_requirement_asks():
    import inspect
    import main as factory_module
    from agent import contract, conventions, outsider
    places = {
        "architect": contract.ARCHITECT_PROMPT if hasattr(contract, "ARCHITECT_PROMPT") else inspect.getsource(contract),
        "conventions": inspect.getsource(conventions),
        "outsider": inspect.getsource(outsider),
    }
    for where, text in places.items():
        assert "signed out" in text, where
    assert "THIS requirement's own seed" in inspect.getsource(outsider)

    assert "missing_seed_names(" in inspect.getsource(factory_module.Factory.process_node)

@case
def the_decision_sees_the_batch_own_failure_first():
    from agent.codegen import FOUNDATION_MARK, check_excerpt
    own = "E2E FAILED\n  1) REQ-5.spec.js: OWN FAILURE\n"
    foundation = FOUNDATION_MARK + " the FOUNDATION specs ... " + "x" * 9000
    excerpt = check_excerpt(own + foundation)
    assert "OWN FAILURE" in excerpt and FOUNDATION_MARK.strip() in excerpt and len(excerpt) <= 3000
    assert check_excerpt(own) == own

    from agent.codegen import CodegenImplementer, TestOutcome
    seen = []

    class FakeLLM:
        def chat(self, messages, **kwargs):
            seen.append(messages[-1]["content"])
            return {"content": "frontend/src/App.tsx -- fix it"}

    implementer = CodegenImplementer.__new__(CodegenImplementer)
    implementer.llm, implementer.log = FakeLLM(), lambda *a, **k: None
    assert implementer._conclude("REQ-5", "analysis", TestOutcome(False, own + foundation)) == "frontend/src/App.tsx -- fix it"
    assert "OWN FAILURE" in seen[0]

@case
def a_checkpoint_is_reused_only_for_the_same_requirements():
    import tempfile
    import main as factory_module
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "app"
        (out / ".arc").mkdir(parents=True)
        first = factory_module.Factory.__new__(factory_module.Factory)
        first.project_dir = out
        first.revised_specs = {"REQ-1.spec.js"}
        for field in factory_module.Factory.CHECKPOINT_FIELDS:
            setattr(first, field, {} if field in ("spec_context", "file_specs", "test_snapshot", "test_ids_by_file", "test_titles", "report") else [])
        first.contract = "CONTRACT"
        first.started = __import__("time").time() - 600
        first.spent_before = 300.0
        first.green_specs = ["REQ-1.spec.js"]
        first.save_checkpoint("digest-1", ["REQ-1", "REQ-2"], "the requirement, with its image notes")

        second = factory_module.Factory.__new__(factory_module.Factory)
        second.project_dir = out
        second.revised_specs = set()
        second.resumed_description = ""
        assert second.load_checkpoint("digest-1") == ["REQ-1", "REQ-2"]
        assert second.contract == "CONTRACT" and second.green_specs == ["REQ-1.spec.js"]
        assert second.revised_specs == {"REQ-1.spec.js"}
        assert second.resumed_description.startswith("the requirement")
        assert 890 <= second.spent_before <= 910

        import json as json_module
        broken = json_module.loads((out / ".arc" / "resume.json").read_text(encoding="utf-8"))
        broken["sources"] = {"frontend/src/App.tsx": "!!! not base64 !!!"}
        (out / ".arc" / "resume.json").write_text(json_module.dumps(broken), encoding="utf-8")
        fresh = factory_module.Factory.__new__(factory_module.Factory)
        fresh.project_dir, fresh.revised_specs, fresh.resumed_description = out, set(), ""
        fresh.contract, fresh.green_specs = "", []
        assert fresh.load_checkpoint("digest-1") == [] and fresh.contract == "" and fresh.green_specs == []

        other = factory_module.Factory.__new__(factory_module.Factory)
        other.project_dir = out
        other.revised_specs = set()
        other.resumed_description = ""
        assert other.load_checkpoint("digest-2") == []

@case
def the_files_only_demand_is_added_once():
    from agent.codegen import files_only_demand
    first = files_only_demand("Earlier context.")
    assert "=== FILE:" in first and "exactly ONE file" in first and first.startswith("Earlier context.")
    assert files_only_demand(first) == first

@case
def acceptance_never_runs_against_another_process():
    import http.server
    import tempfile
    import threading
    from agent import verify

    class Quiet(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"someone else's app")

        def log_message(self, *args):
            pass

    other = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    taken = other.server_address[1]

    class Dead:
        def poll(self):
            return 1

    class FakeServer:
        def __init__(self, *args, **kwargs):
            self.process = Dead()

        def stop(self):
            return ""

    saved = (verify.free_port, verify.BackgroundProcess)
    verify.free_port = lambda preferred=3000: taken
    verify.BackgroundProcess = FakeServer
    lines = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "backend" / "node_modules" / "@playwright" / "test").mkdir(parents=True)
            result = verify.run_e2e(root, ["backend/test-e2e/REQ-1.spec.js"], log=lines.append)
    finally:
        verify.free_port, verify.BackgroundProcess = saved
        other.shutdown()
    assert result.skipped and result.ok, result.output
    assert any("another process" in line for line in lines)
    assert not any("running playwright" in line for line in lines)

@case
def each_local_run_gets_its_own_ports():
    import os
    from agent.fsutil import free_port
    os.environ["AGENT_PORT_BASE"] = "5000"
    try:
        port = free_port(3100)
    finally:
        del os.environ["AGENT_PORT_BASE"]
    assert port in (5100, 5200, 5300) or port > 10000, port

@case
def an_entry_leaves_the_kind_open():
    assert wanted_role("书架", "用户点击「书架」入口进入书架列表") == ""
    assert wanted_role("Shelf 4.1", "a unique shelf entry named `Shelf 4.1`") == ""
    assert wanted_role("退出", "点击「退出」链接") == "link"

@case
def a_red_batch_is_told_when_it_breaks_the_foundation():
    import tempfile
    import main as factory_module
    from agent.codegen import FOUNDATION_MARK, own_output, spec_evidence
    from agent.verify import StepResult

    calls = []
    behaviour = {}

    def fake_e2e(project_dir, specs, log=print):
        calls.append(list(specs))
        key = "group" if len(specs) > 1 else "alone"
        ok, named = behaviour[key]
        lines = "".join(f"  1) test-e2e/{name}:4:1 > {name}: step\n    Error: not found\n" for name in named)
        return StepResult(ok, "" if ok else lines)

    saved = factory_module.run_e2e
    factory_module.run_e2e = fake_e2e
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "frontend" / "src").mkdir(parents=True)
            page = root / "frontend" / "src" / "HomePage.tsx"
            page.write_text("before", encoding="utf-8")

            def factory():
                made = factory_module.Factory.__new__(factory_module.Factory)
                made.project_dir = root
                made.green_specs = ["backend/test-e2e/REQ-1.spec.js", "backend/test-e2e/REQ-2.spec.js"]
                made.file_specs = {"frontend/src/HomePage.tsx": ["backend/test-e2e/REQ-1.spec.js"]}
                made.record_run = lambda specs, ok, output: None
                made.foundation_checks = {}
                return made

            before = {"frontend/src/HomePage.tsx": b"before"}
            own = ["backend/test-e2e/REQ-5.spec.js"]

            behaviour.update(group=(False, ["REQ-1.spec.js"]), alone=(False, ["REQ-1.spec.js"]))
            f = factory()
            assert f.foundation_note_while_red("REQ-5", own, before) == ""
            assert calls == []
            page.write_text("after", encoding="utf-8")

            note = f.foundation_note_while_red("REQ-5", own, before)
            assert note.startswith(FOUNDATION_MARK) and "REQ-1.spec.js" in note and "rolled back" in note
            assert calls == [["backend/test-e2e/REQ-1.spec.js", "backend/test-e2e/REQ-2.spec.js"], ["backend/test-e2e/REQ-1.spec.js"]]

            behaviour.update(group=(False, ["REQ-1.spec.js"]), alone=(True, []))
            assert factory().foundation_note_while_red("REQ-5", own, before) == ""

            behaviour.update(group=(False, []), alone=(False, []))
            assert factory().foundation_note_while_red("REQ-5", own, before) == ""

            behaviour.update(group=(True, []), alone=(True, []))
            assert factory().foundation_note_while_red("REQ-5", own, before) == ""

            behaviour.update(group=(False, ["REQ-1.spec.js"]), alone=(False, ["REQ-1.spec.js"]))
            f = factory()
            for _ in range(f.FOUNDATION_CHECKS_MAX):
                assert f.foundation_note_while_red("REQ-5", own, before)
            calls.clear()
            assert f.foundation_note_while_red("REQ-5", own, before) == "" and calls == []

            output = "E2E FAILED\n  1) REQ-5 step\n    Error: own failure" + note
            assert "own failure" in own_output(output) and "REQ-1.spec.js" not in own_output(output)
            spec = {"backend/test-e2e/REQ-5.spec.js": 'await click(page, "Open dashboard")'}
            claim = '"Open dashboard" contradicts "Error: not found"'
            assert spec_evidence(claim, "The dashboard opens.", spec, output)
            assert not spec_evidence(claim, "The dashboard opens.", spec, own_output(output))
    finally:
        factory_module.run_e2e = saved

@case
def a_deletable_seed_is_never_seeded_deleted():
    from agent import contract
    from agent.conventions import PROJECT_CONVENTIONS
    english = {"architect": contract.SYSTEM_PROMPT, "contract review": contract.REVIEW_PROMPT, "conventions": PROJECT_CONVENTIONS}
    for name, prompt in english.items():
        assert "deletable" in prompt and "ADR-097" in prompt or "deletable" in prompt and "trashed" in prompt, name

@case
def a_unique_name_is_built_once():
    from agent.testrun import rebuilt_unique_names
    rel = "backend/test-e2e/REQ-2.7.spec.js"
    twice = ('test("REQ-2.7.4: a", async ({ page }) => {\n  await fill(page, "Title", "Outsider " + Date.now());\n'
             '  const note = item(page, "Outsider " + Date.now());\n});\n')
    once = ('test("REQ-2.7.4: a", async ({ page }) => {\n  const name = "Outsider " + Date.now();\n'
            '  await fill(page, "Title", name);\n  const note = item(page, name);\n});\n')
    two_names = ('test("REQ-2.7.4: a", async ({ page }) => {\n  const a = "Outsider " + Date.now();\n'
                 '  const b = "Label " + Date.now();\n});\n')
    assert rebuilt_unique_names(rel, "\n" + twice) and "ONCE" in rebuilt_unique_names(rel, "\n" + twice)[0]
    assert rebuilt_unique_names(rel, "\n" + once) == []
    assert rebuilt_unique_names(rel, "\n" + two_names) == []
    assert rebuilt_unique_names("backend/tests/x.test.js", "\n" + twice) == []

    assert rebuilt_unique_names(rel, "\n" + once + once) == []

    template = ('test("REQ-2.7.4: a", async ({ page }) => {\n  await fill(page, "Title", `Outsider ${Date.now()}`);\n'
                '  await see(page, `Outsider ${Date.now()}`);\n});\n')
    assert rebuilt_unique_names(rel, "\n" + template)
    mixed = ('test("REQ-2.7.4: a", async ({ page }) => {\n  const name = `Outsider ${Date.now()}`;\n'
             '  await fill(page, "Title", name);\n  await see(page, "Outsider " + Date.now());\n});\n')
    assert rebuilt_unique_names(rel, "\n" + mixed)
    assert rebuilt_unique_names(rel, "\n" + twice.replace("test(", "test.only(", 1))

    two_records = ('test("REQ-2.7.4: a", async ({ page }) => {\n  const a = "Note " + Date.now();\n  await create(a);\n'
                   '  const b = "Note " + Date.now();\n  await create(b);\n});\n')
    assert rebuilt_unique_names(rel, "\n" + two_records) == []

    wrapped = two_records.replace('= "Note " + Date.now();', '= ("Note " + Date.now());')
    typed = two_records.replace("const a =", "const a: string =").replace("const b =", "const b: string =")
    assert rebuilt_unique_names(rel, "\n" + wrapped) == [] and rebuilt_unique_names(rel, "\n" + typed) == []

@case
def an_item_title_that_is_a_bare_link_is_reported():
    from agent.detaudit import bare_item_titles
    bare = ('- list:\n  - listitem:\n    - link "Shelf 4.1":\n      - /url: /shelves/1\n    - paragraph: Seeded\n'
            '  - listitem:\n    - link "Shelf 4.2":\n      - /url: /shelves/2\n')
    nested = '- list:\n  - listitem:\n    - heading "Shelf 4.1" [level=3]:\n      - link "Shelf 4.1":\n        - /url: /shelves/1\n'
    menu = '- navigation:\n  - list:\n    - listitem:\n      - link "Book 1":\n        - /url: /books/1\n'
    generic = '- list:\n  - listitem:\n    - link "Help":\n      - /url: /help\n'
    assert bare_item_titles(bare) == ["Shelf 4.1", "Shelf 4.2"]
    assert bare_item_titles(nested) == [] and bare_item_titles(menu) == [] and bare_item_titles(generic) == []

@case
def a_card_whose_middle_is_an_action_is_reported():
    import tempfile

    from agent.audit import SNAPSHOT_SCRIPT
    from agent.detaudit import deterministic_findings

    assert "entry.covered" in SNAPSHOT_SCRIPT and "elementFromPoint" in SNAPSHOT_SCRIPT
    covered = [{"route": "/", "aria": "", "covered": [{"title": "Project ideas", "hit": "Change color"}]}]
    clear = [{"route": "/", "aria": "", "covered": []}]
    with tempfile.TemporaryDirectory() as tmp:
        node = {"id": "REQ-2.4", "name": "Update Note", "description": "Open the note."}
        found = deterministic_findings(Path(tmp), node, covered, log=lambda message: None)
        assert any("Change color" in f["problem"] and "bottom edge" in f["fix"] for f in found)
        assert not any("covered by" in f["problem"] for f in deterministic_findings(Path(tmp), node, clear, log=lambda message: None))

@case
def a_spec_that_signs_in_unasked_goes_back():
    from agent.outsider import unasked_sign_ins
    node = {"id": "REQ-8+9", "name": "b", "description": (
        "### REQ-9.1 Quick Navigation\nOpen a page.\nScenario 1: x\n  GIVEN: The homepage shows `Recently Updated Pages`.\n\n"
        "### REQ-8.2 Favorites\nScenario 1: y\n  GIVEN: The authenticated homepage shows a favorite.\n\n"
        "### REQ-7.1 Recently Viewed\nScenario 1: z\n  GIVEN: The user browses pages.\n  THEN: `My Recently Viewed` lists it.")}
    spec = ('\ntest("REQ-9.1: Quick Navigation", async ({ page }) => {\n  await login(page);\n  await see(page, "x");\n});\n'
            'test("REQ-8.2: Favorites", async ({ page }) => {\n  await login(page);\n});\n'
            'test("REQ-7.1: Recently Viewed", async ({ page }) => {\n  await click(page, "Login");\n});\n'
            'test("REQ-9.1: signed out", async ({ page }) => {\n  await page.goto("/");\n});\n')
    assert unasked_sign_ins(spec, node) == ["REQ-9.1: Quick Navigation"]

@case
def an_item_title_that_is_a_bare_button_is_reported():
    from agent.detaudit import bare_item_titles
    bare = '- list:\n  - listitem:\n    - button "Page Created 6.1.1"\n  - listitem:\n    - button "Page 2"\n'
    actions = '- list:\n  - listitem:\n    - button "Edit"\n  - listitem:\n    - button "Delete note"\n'
    nested = '- list:\n  - listitem:\n    - heading "Page 2" [level=3]:\n      - button "Page 2"\n'
    assert bare_item_titles(bare) == ["Page Created 6.1.1", "Page 2"]
    assert bare_item_titles(actions) == [] and bare_item_titles(nested) == []

@case
def an_example_name_is_not_a_fixed_tail_for_every_record():
    from agent.detaudit import fixed_seed_tails
    requirement = ('Each label row is a named group, for example `Label Work editable`, containing a textbox. '
                   'Seed data: editable label "Work editable".')
    wrong = "<div role=\"group\" aria-label={`Label ${name} editable`}>"
    right = "<div role=\"group\" aria-label={`Label ${label.name}`}>"
    assert fixed_seed_tails(requirement, wrong) == [("Label Work editable", "Work editable", "Label ${name} editable")]
    assert fixed_seed_tails(requirement, right) == []

    assert fixed_seed_tails('For example `Label Work`. Seed data: label "Work".', "aria-label={`Label ${name}`}") == []

    assert fixed_seed_tails("Every row is named `Item <name> (editable)`.", "`Item ${name} (editable)`") == []

@case
def audit_round_23_narrowed_the_new_checks():
    from agent import clock
    from agent.detaudit import bare_item_titles, fixed_seed_tails
    from agent.outsider import unasked_sign_ins

    before = clock.paused_sec()
    clock.note_gap(clock.MAX_PAUSED_SEC * 2, log=lambda message: None)
    assert clock.paused_sec() <= clock.MAX_PAUSED_SEC and clock.GAP_SEC >= 300
    clock._paused = before

    node = {"id": "B", "name": "b", "description": (
        "### REQ-1.1 Profile\nScenario 1: a\n  GIVEN: The user opens the account page.\n\n"
        "### REQ-2.1 Signed in\nScenario 1: b\n  GIVEN: The signed-in user is on the home page.\n\n"
        "### REQ-3.1 Public list\nScenario 1: c\n  GIVEN: The home page shows the list.")}
    spec = ('\ntest("REQ-1.1: account", async ({ page }) => {\n  await login(page);\n});\n'
            'test("REQ-3.1 + REQ-2.1: flow", async ({ page }) => {\n  await login(page);\n});\n'
            'test("REQ-3.1: list", async ({ page }) => {\n  await login(page);\n});\n'
            'test("REQ-3.1 + REQ-9.9: other batch", async ({ page }) => {\n  await login(page);\n});\n')

    assert unasked_sign_ins(spec, node) == ["REQ-3.1: list"]

    menu = '- menu:\n  - listitem:\n    - button "Settings"\n  - listitem:\n    - button "Profile page"\n'
    pages = '- list:\n  - listitem:\n    - button "2"\n  - listitem:\n    - button "3"\n'
    lone = '- list:\n  - listitem:\n    - button "Page Created 6.1.1"\n'
    paging = '- list:\n  - listitem:\n    - link "Next set":\n      - /url: /page/2\n'
    assert bare_item_titles(menu) == [] and bare_item_titles(pages) == [] and bare_item_titles(lone) == []
    assert bare_item_titles(paging) == []

    note_seed = 'For example `Label Work editable`. Seed data: note "Work editable" with the "Work" label; label "Work".'
    assert fixed_seed_tails(note_seed, "aria-label={`Label ${name} editable`}") == []

@case
def item_links_titles_outside_and_mixed_texts_are_reported():
    import tempfile

    from agent.audit import SNAPSHOT_SCRIPT, routes_from_contract
    from agent.detaudit import deterministic_findings, item_title_links, requirement_says_link, text_beside_controls

    page = ('- main:\n  - article:\n    - heading "Book 6.1.3" [level=1]\n    - button "New Page"\n    - list:\n'
            '      - listitem:\n        - link "Draft 6.1.3":\n          - /url: /pages/1\n'
            '      - listitem:\n        - heading "Page 9.1" [level=3]:\n          - link "Page 9.1":\n            - /url: /pages/4\n')
    assert item_title_links(page) == ["Draft 6.1.3", "Page 9.1"]
    assert item_title_links(page.replace('link "Draft', 'button "Draft').replace('link "Page', 'button "Page')) == []
    assert not requirement_says_link("activates the unique link or button named after the page")
    assert requirement_says_link("the sign-out link")

    toast = '- status:\n  - text: Note trashed\n  - button "Undo"\n'
    assert text_beside_controls(toast, ["Note trashed"]) == ["Note trashed"]
    assert text_beside_controls('- status:\n  - text: Note trashed\n', ["Note trashed"]) == []

    assert text_beside_controls('- status:\n  - generic:\n    - text: Note trashed\n    - button "Undo"\n', ["Note trashed"]) == ["Note trashed"]
    assert text_beside_controls('- form:\n  - checkbox "Remember Me"\n  - text: Remember Me\n', ["Remember Me"]) == []

    import inspect
    from agent import codegen, contract
    assert "the KIND of an element" in inspect.getsource(codegen)
    assert "never state the element KIND" in inspect.getsource(contract)

    assert "entry.titleOutside" in SNAPSHOT_SCRIPT
    with tempfile.TemporaryDirectory() as tmp:
        node = {"id": "REQ-2.8.1", "name": "Pin", "description": "The note shows `Note trashed`."}
        snap = [{"route": "/", "aria": toast, "titleOutside": [{"title": "Pinned", "item": "Meeting agenda"}]}]
        problems = " ".join(f["problem"] for f in deterministic_findings(Path(tmp), node, snap, log=lambda message: None))
        assert "title/tooltip only on the item's own container" in problems and "bare text node" in problems

        assert "entry.titleNotControl" in SNAPSHOT_SCRIPT and "getComputedStyle(el).cursor === 'pointer'" in SNAPSHOT_SCRIPT
        snap = [{"route": "/", "aria": "", "titleNotControl": [{"title": "Sprint goals"}, {"title": "Groceries"}]}]
        problems = " ".join(f["problem"] for f in deterministic_findings(Path(tmp), node, snap, log=lambda message: None))
        assert "heading holds no button or link: 'Sprint goals', 'Groceries'" in problems
        snap = [{"route": "/", "aria": "", "titleNotControl": []}]
        assert "heading holds no button" not in " ".join(f["problem"] for f in deterministic_findings(Path(tmp), node, snap, log=lambda message: None))

    assert routes_from_contract('{"pages": [{"route": "/shelves", "requirements": ["REQ-4.1"]}, {"route": "/books"} ,, }',
                                ["REQ-4.1"]) == ["/", "/shelves", "/books"]

@case
def a_spec_may_not_demand_a_link_the_requirement_leaves_open():
    from agent.outsider import unstated_kinds
    node = {"id": "REQ-4.1+1", "name": "Shelves", "description": (
        'The region "Shelf list" contains the seeded shelf "Shelf 4.1" as a unique link or button with that name. '
        'The header shows the sign-out link "Sign out". The card "Book 9" opens the book.')}
    spec = ('await seeOnce(page, { role: "link", name: "Shelf 4.1" }, list);\n'
            'await click(page, { role: "link", name: "Sign out" });\n'
            'await click(page, { role: "link", name: "Book 9" });\n'
            'await click(page, { role: "button", name: "Book 9" });\n')

    assert unstated_kinds(spec, node) == ['{ role: "link", name: "Shelf 4.1" }']
    signin = {"id": "REQ-2-1-1", "name": "Org", "description": "The visitor signs in and opens the organization.",
              "requirement_text": "All authenticated scenarios use the home-page “Sign in” link and the “Sign in” button."}
    assert unstated_kinds('await click(page, { role: "link", name: "Sign in" });\n', signin) == []

    sheet = {"id": "REQ-1-1-1", "name": "Home", "description": "Each record provides a link whose accessible name is the workbook name.",
             "requirement_text": "GIVEN: The evaluation seed contains the seeded workbook `Q3\n  Sales`, worksheet `Sheet1`."}
    assert unstated_kinds('await click(page, { role: "link", name: "Q3 Sales" });\n', sheet) == []
    other = dict(sheet, description="Each record provides a button whose accessible name is the workbook name.")
    assert unstated_kinds('await click(page, { role: "link", name: "Q3 Sales" });\n', other) == ['{ role: "link", name: "Q3 Sales" }']

@case
def a_spec_may_not_use_a_control_of_a_later_requirement():
    import inspect
    import main as factory_module
    from agent.outsider import later_controls
    node = {"id": "REQ-1-1-1", "later_names": ["New blank workbook", "Create"]}
    spec = ('await click(page, "New blank workbook");\nawait fill(page, { name: "Create" }, "x");\n'
            'await click(page, { role: "link", name: "new sheet" });\nawait see(page, "New blank workbook");\n')
    assert later_controls(spec, node) == ["New blank workbook", "Create"]
    assert later_controls(spec, {"id": "REQ-1-1-1"}) == []
    assert factory_module.quoted_names('a button “New blank workbook” and "Create" and `Save`') == ["New blank workbook", "Create", "Save"]
    work = [{"id": "REQ-1", "name": "open", "description": 'Open the "Docs" link.'},
            {"id": "REQ-2", "name": "new", "description": 'Click "New blank workbook", then "Docs".'}]
    factory_module.stamp_batch_names(work)
    assert work[0]["later_names"] == ["New blank workbook"] and work[1]["later_names"] == []
    assert "stamp_batch_names(work)" in inspect.getsource(factory_module.Factory.run)

@case
def a_first_answer_that_thinking_ate_is_asked_again_without_thinking():
    import tempfile

    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome

    class Model:
        model, last_finish_reason = "glm-test", "stop"
        asked = []

        def chat(self, messages, **options):
            Model.asked.append(options.get("thinking"))
            if len(Model.asked) == 1:
                Model.last_finish_reason = "length"
                return {"content": ""}
            Model.last_finish_reason = "stop"
            return {"content": block("frontend/src/pages/HomePage.tsx", "export default function HomePage() { return null; }")}

    with tempfile.TemporaryDirectory() as tmp:
        implementer = CodegenImplementer(Model(), Path(tmp), lambda: TestOutcome(True, "E2E PASSED"), log=lambda message: None)
        result = implementer.implement({"id": "REQ-1", "name": "n"}, {}, TestOutcome(False, "E2E FAILED"))
        assert Model.asked[:2] == [True, False] and result.status == "passed", (Model.asked, result.status)

@case
def hyphen_ids_and_dependency_order():
    from agent.batching import build_batches
    from agent.contract import related
    from agent.outsider import unasked_sign_ins
    assert related(["REQ-1"], ["REQ-1-1-1"]) and related(["REQ-1-1"], ["REQ-1-1-1"]) and related(["REQ-1"], ["REQ-1.1.1"])
    assert not related(["REQ-1"], ["REQ-10-1"]) and not related(["REQ-1-2"], ["REQ-1-1-1"])
    node = {"id": "B", "description": "### REQ-1-1-1 Home\nScenario 1: a\n  GIVEN: The home page shows the list.\n\n"
                                      "### REQ-1-1-2 Sign in\nScenario 1: b\n  GIVEN: The user signs in."}
    spec = ('\ntest("REQ-1-1-1: list", async ({ page }) => {\n  await login(page);\n});\n'
            'test("REQ-1-1-2: in", async ({ page }) => {\n  await login(page);\n});\n')
    assert unasked_sign_ins(spec, node) == ["REQ-1-1-1: list"]
    big = "x" * 1600

    def leaf(i, deps=()):
        return {"id": f"REQ-{i}", "name": f"n{i}", "description": big, "dependencies": list(deps), "scenarios": []}

    tree = {"id": "ROOT", "children": [{"id": "REQ-1", "name": "f", "children": [leaf("1-1", ["REQ-3"]), leaf("1-2")]},
                                       {"id": "REQ-2", "name": "g", "children": [leaf("2-1")]},
                                       {"id": "REQ-3", "name": "h", "children": [leaf("3-1", ["REQ-1-1"])]}]}
    order = [b["id"] for b in build_batches(tree)]

    assert order.index("REQ-1-2") < order.index("REQ-1-1") or order[0] == "REQ-1-1", order
    tree["children"][2]["children"][0]["dependencies"] = []
    order = [b["id"] for b in build_batches(tree)]
    assert order.index("REQ-3-1") < order.index("REQ-1-1") and order.index("REQ-1-2") < order.index("REQ-2-1"), order

@case
def audit_r25_batching_rules_and_parse():
    import main as factory_module
    from agent.batching import build_batches
    from agent.outsider import _parse

    def leaf(i, deps=()):
        return {"id": f"REQ-{i}", "name": f"n{i}", "description": "x" * 1300, "dependencies": list(deps), "scenarios": []}

    tree = {"id": "ROOT", "children": [{"id": "REQ-4", "name": "f", "children": [
        {"id": "REQ-4-1", "name": "g", "children": [leaf("4-1-1"), leaf("4-1-2", ["REQ-4-2-2"])]},
        {"id": "REQ-4-2", "name": "h", "children": [leaf("4-2-2", ["REQ-4-1-1"])]}]}]}
    order = [m for b in build_batches(tree) for m in b["members"]]
    assert order == ["REQ-4-1-1", "REQ-4-2-2", "REQ-4-1-2"], order
    tree["children"][0]["children"][1]["children"][0]["dependencies"] = []
    assert any(len(b["members"]) > 1 for b in build_batches(tree))

    intro = "Each operation completely updates the target, or shows an error while the rest keeps its state."
    rules = factory_module.shared_module_rules({"id": "ROOT", "children": [{"id": "REQ-3", "name": "e", "description": intro, "children": [leaf("3-1")]}]})
    assert intro in rules["REQ-3"]

    spec = ('=== FILE: backend/test-e2e/REQ-1.spec.js ===\nconst { test } = require("@playwright/test");\n'
            'const { locate, drag, expect } = require("../e2e-support/outsider");\n'
            'test("REQ-1: s", async ({ page }) => {\n  await page.goto("/");\n  await drag(page, "A1", "B2");\n'
            '  await expect(locate(page, { role: "tab", name: "Sheet1" })).toHaveAttribute("aria-selected", "true");\n});\n'
            '=== END FILE ===\n')
    assert _parse(spec, "REQ-1") is not None
    assert _parse(spec.replace("drag(page", "dragg(page").replace("locate(page", "locat(page"), "REQ-1") is None

@case
def spec_imports_are_put_in_order_and_complete_answers_kept():
    from agent.codegen import parse_output
    from agent.outsider import HELPER_JS, _parse
    from agent.test_author import parse_file_blocks, split_file_blocks
    from agent.testrun import normalize_spec_imports, outsider_undefined_calls
    rel = "backend/test-e2e/REQ-5.spec.js"
    body = 'test("REQ-5: a", async ({ page }) => {\n  await click(page, "Books");\n  expect(1).toBe(1);\n});\n'

    for head in ('const { test, click, expect } = require("../e2e-support/outsider");\n',
                 'const { test, click, expect } = require("@playwright/test");\nconst { click, see, expect } = require("../e2e-support/outsider");\n'):
        fixed = normalize_spec_imports(head + "\n" + body)
        assert fixed.count("require(") == 2 and 'const { test } = require("@playwright/test");' in fixed, fixed
        assert "click" in fixed.split("\n")[1] and fixed.count("expect,") + fixed.count("expect }") == 1, fixed
        assert outsider_undefined_calls(rel, fixed) == [], outsider_undefined_calls(rel, fixed)
        assert fixed.endswith(body)
    renamed = 'const { click: press2 } = require("../e2e-support/outsider");\n' + body
    assert normalize_spec_imports(renamed) == renamed
    assert "module.exports = { test," in HELPER_JS

    answer = "=== FILE: backend/test-e2e/REQ-5.spec.js ===\n" + 'const { test } = require("@playwright/test");\nconst { click, expect } = require("../e2e-support/outsider");\n' + body
    assert split_file_blocks(answer) == [] and len(split_file_blocks(answer, complete=True)) == 1
    assert _parse(answer, "REQ-5") is None and _parse(answer, "REQ-5", complete=True) is not None
    assert parse_output(answer)[0] == [] and len(parse_output(answer, complete=True)[0]) == 1
    assert len(parse_file_blocks(answer, complete=True)) == 1

    bad = answer.replace('const { test } = require("@playwright/test");\nconst { click,', 'const { test, click,')
    assert 'const { test } = require("@playwright/test")' in _parse(bad, "REQ-5", complete=True).content
    assert 'const { test } = require("@playwright/test")' in parse_file_blocks(bad, complete=True)[0].content

@case
def keep_v88_root_causes_are_caught():
    import inspect
    import tempfile

    from agent import outsider
    from agent.audit import SNAPSHOT_SCRIPT
    from agent.conventions import PROJECT_CONVENTIONS
    from agent.detaudit import deterministic_findings, seeded_in_a_later_state

    requirement = 'Restore an archived note. Seed data: active note "Travel plans 2.5.4".'
    line = "      { title: 'Travel plans 2.5.4', content: '', pinned: 0, archived: 1 },"
    assert seeded_in_a_later_state(requirement, line) == [("Travel plans 2.5.4", line.strip())]
    assert seeded_in_a_later_state(requirement, line.replace("archived: 1", "archived: 0")) == []
    assert seeded_in_a_later_state('Seed data: archived note "Old trip".', "{ title: 'Old trip', archived: 1 }") == []
    with tempfile.TemporaryDirectory() as tmp:
        seed = Path(tmp) / "backend" / "src" / "database" / "init_db.js"
        seed.parent.mkdir(parents=True)
        seed.write_text("const notes = [\n" + line + "\n];\n", encoding="utf-8")
        node = {"id": "REQ-2.5.4", "name": "Unarchive", "description": requirement}
        problems = " ".join(f["problem"] for f in deterministic_findings(Path(tmp), node, [], log=lambda message: None))
        assert "is seeded in a later state" in problems, problems

    assert "item.style.minHeight" in SNAPSHOT_SCRIPT and "getComputedStyle(item).cursor === 'pointer'" in SNAPSHOT_SCRIPT
    assert 'flex min-h-28 flex-col' in PROJECT_CONVENTIONS

    assert "ANOTHER requirement places in a named dialog" in inspect.getsource(outsider)

@case
def audit_r26_rules_agree_with_each_other():
    import inspect
    import tempfile

    import main as factory_module
    from agent import codegen, outsider, test_author
    from agent.testrun import NATIVE_VALIDATION, lint_application

    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "frontend" / "src" / "pages" / "Login.tsx"
        page.parent.mkdir(parents=True)
        page.write_text("export default () => <form onSubmit={save}><input type=\"email\" required /></form>;\n", encoding="utf-8")
        assert "noValidate" in lint_application(Path(tmp))
        assert "noValidate" not in lint_application(Path(tmp), native_validation=True)
    assert NATIVE_VALIDATION.search("The form uses the browser's own validation bubbles.") and not NATIVE_VALIDATION.search("The form validates the e-mail.")
    assert "self.native_validation = bool(NATIVE_VALIDATION.search(digest))" in inspect.getsource(factory_module.Factory.run)

    source = inspect.getsource(codegen)
    assert "A TEST DEFECT is exactly one of" in source and "A TEST DEFECT is only a step" not in source
    assert "If another test's locator is ambiguous" not in source and "role and name the requirement STATES" in source

    assert "then there is no file 2" in inspect.getsource(test_author)

    assert 'Date.now() + "-" + Math.random()' in inspect.getsource(outsider)

@case
def spec_api_misuse_is_a_spec_defect():
    import tempfile

    from agent.outsider import HELPER_JS
    from agent.testrun import spec_api_misuse, static_test_defects
    rel = "backend/test-e2e/REQ-1.spec.js"
    assert "region(page, \"Account menu\")" in spec_api_misuse(rel, 'const menu = region(page, "Account menu");')[0]
    assert spec_api_misuse(rel, 'const menu = region(page, "dialog", "Account menu");') == []
    assert "page.back() does not exist" in spec_api_misuse(rel, "await page.back();")[0]
    assert spec_api_misuse(rel, "await page.goBack(); await page.reload(); page.url();") == []
    assert "takes whichever link comes first" in spec_api_misuse(rel, 'const first = locate(page, { role: "link" }).first();')[0]
    assert spec_api_misuse(rel, 'await expect(locate(page, { role: "tab", name: "Sheet1" })).toBeVisible();') == []
    assert spec_api_misuse(rel, 'await see(page, { role: "grid" });') == []
    assert spec_api_misuse("backend/tests/x.test.js", "await page.back();") == []
    assert "SPEC DEFECT: region() takes a ROLE first" in HELPER_JS

    import inspect
    from agent import outsider
    assert "Never invent a name or a password for a seed" in inspect.getsource(outsider)
    assert "met by the seed: use a record the application seeds" not in inspect.getsource(outsider)
    with tempfile.TemporaryDirectory() as tmp:
        spec = Path(tmp) / rel
        spec.parent.mkdir(parents=True)
        spec.write_text('const { test } = require("@playwright/test");\nconst { see, region } = require("../e2e-support/outsider");\n'
                        'test("REQ-1: a", async ({ page }) => {\n  await see(page, "octocat", region(page, "Account menu"));\n});\n', encoding="utf-8")
        assert "the second argument is a ROLE" in static_test_defects(Path(tmp), [rel])

@case
def a_gateway_crash_runs_the_batch_once_more():
    import main as factory_module
    from agent.llm import LLM, LLMError

    class Trace:
        def __init__(self):
            self.states = []

        def node_state(self, member, state, phase):
            self.states.append((member, state))

    def factory(outcomes):
        f = factory_module.Factory.__new__(factory_module.Factory)
        f.trace = Trace()
        f.over_budget = lambda: False
        calls = []

        def process_node(node, nodes_left):
            calls.append(node["id"])
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        f.process_node = process_node
        return f, calls

    node = {"id": "REQ-1-1-1", "members": ["REQ-1-1-1"]}
    saved = LLM.quota_exhausted
    try:
        LLM.quota_exhausted = False
        f, calls = factory([LLMError("LLM request failed after 2 read timeouts"), True])
        assert f.attempt_node(node, 3, pause_sec=0) is True and calls == ["REQ-1-1-1", "REQ-1-1-1"] and f.trace.states == []
        f, calls = factory([LLMError("timeout"), LLMError("timeout")])
        assert f.attempt_node(node, 3, pause_sec=0) is False and len(calls) == 2 and f.trace.states == [("REQ-1-1-1", "FAILED")]
        f, calls = factory([RuntimeError("a bug in the batch"), True])
        assert f.attempt_node(node, 3, pause_sec=0) is False and len(calls) == 1
        LLM.quota_exhausted = True
        f, calls = factory([LLMError("quota"), True])
        assert f.attempt_node(node, 3, pause_sec=0) is False and len(calls) == 1
    finally:
        LLM.quota_exhausted = saved

@case
def a_control_of_a_named_dialog_is_looked_for_in_it():
    from agent.outsider import dialog_controls, unscoped_dialog_controls
    reference = ("Activating it opens exactly one dialog named `Note editor` containing uniquely labelled textboxes `Title` "
                 "and `Note content`, plus a button named `Close`. Each note article has a `Change color` button. "
                 "The `Note editor` dialog contains a button named `Change color` and a button named `Close`. "
                 "The “Rename” menu item opens a dialog named “Rename worksheet”, containing a text box labeled "
                 "“Worksheet name”.")
    placed = dialog_controls(reference)
    assert placed.get("Close") == "Note editor" and placed.get("Worksheet name") == "Rename worksheet", placed
    assert "Change color" not in placed
    assert "Rename" not in placed
    node = {"id": "REQ-2.7.1", "name": "Assign label", "reference_requirements": reference,
            "description": "In the `Team retro` article, activate `More options`, activate `Change labels`, check the `Work` checkbox, and activate `Close`."}
    spec = ('await click(page, "Change labels", note);\nawait check(page, { role: "checkbox", name: "Work" });\n'
            'await click(page, { role: "button", name: "Close" });\n')
    assert unscoped_dialog_controls(spec, node) == [("Close", "Note editor")]
    scoped = 'const editor = region(page, "dialog", "Note editor");\n' + spec.replace('name: "Close" })', 'name: "Close" }, editor)')
    assert unscoped_dialog_controls(scoped, node) == []

    any_dialog = scoped.replace('region(page, "dialog", "Note editor")', 'region(page, "dialog")')
    assert unscoped_dialog_controls(any_dialog, node) == [("Close", "Note editor")]
    inline = spec.replace('name: "Close" })', 'name: "Close" }, region(page, "dialog", "Labels"))')
    assert unscoped_dialog_controls(inline, node) == [("Close", "Note editor")]
    later = dict(node, later_names=["Note editor"])
    assert unscoped_dialog_controls(spec, later) == []
    node["description"] = "Change the colour of a note."
    assert unscoped_dialog_controls(spec, node) == []
    import inspect
    from agent.outsider import review_acceptance
    assert "unscoped_dialog_controls(spec.content, node)" in inspect.getsource(review_acceptance) + inspect.getsource(outsider_module.deterministic_review)

@case
def a_resume_attaches_the_visual_notes_again():
    import inspect
    import re as re_module

    import main as factory_module
    source = inspect.getsource(factory_module.Factory.run)

    assert 'tree["description"] = self.resumed_description' not in source
    call = source.index("attach_visual_notes(tree, self.requirements_dir")
    before = source[max(0, call - 400):call]
    assert not re_module.search(r"\n\s+else:\s*\n\s+try:\s*$", before), "attach_visual_notes still sits in an else branch"

@case
def an_invented_record_is_a_spec_problem():
    import inspect

    from agent.outsider import invented_records, review_acceptance
    node = {"id": "REQ-2-1-1", "name": "Browse", "description": "The organization page lists its repositories.",
            "reference_requirements": "Visitors open an organization's page."}
    spec = ('const org = "seed-org";\nconst publicRepo = "public-repo";\n'
            'test("REQ-2-1-1: a", async ({ page }) => {\n  await click(page, org);\n  await see(page, { role: "heading", name: publicRepo });\n});\n')
    assert invented_records(spec, node) == ["seed-org", "public-repo"]
    created = spec.replace('await click(page, org);', 'await fill(page, "Organization name", org);\n  await click(page, org);')
    assert invented_records(created, node) == ["public-repo"]
    named = dict(node, description='The organization "seed-org" lists its repositories.')
    assert invented_records(spec, named) == ["public-repo"]
    inline = 'test("x", async ({ page }) => {\n  await see(page, "Note archived");\n});\n'
    assert invented_records(inline, node) == []
    assert "invented_records(spec.content, node)" in inspect.getsource(review_acceptance) + inspect.getsource(outsider_module.deterministic_review)

@case
def an_inline_invented_record_or_account_is_a_spec_problem():
    import inspect

    from agent.outsider import invented_accounts, invented_records, review_acceptance
    node = {"id": "REQ-2-1-1", "name": "Browse", "description": 'Each cell has "Open dropdown for <cell coordinate>".',
            "reference_requirements": "Visitors open an organization's page and sign in with a username or email."}
    spec = ('test("REQ-2-1-1: a", async ({ page }) => {\n  await page.goto("/Acme Demo/acme-docs");\n'
            '  await click(page, "Acme Demo");\n  await see(page, { role: "heading", name: "Acme Demo/acme-docs" });\n'
            '  await click(page, "B2");\n  await click(page, "Open dropdown for A2");\n});\n')

    assert invented_records(spec, node) == ["Acme Demo"], invented_records(spec, node)
    made = spec.replace('  await click(page, "Acme Demo");', '  await fill(page, "Organization name", "Acme Demo");\n  await click(page, "Acme Demo");')
    assert invented_records(made, node) == []
    sign_in = ('async function signIn(page, who, pw) {\n  await click(page, "Sign in");\n  await fill(page, "Username or email", who);\n'
               '  await fill(page, "Password", pw);\n  await click(page, { role: "button", name: "Sign in" });\n}\n'
               'test("REQ-1-3: a", async ({ page }) => {\n  await signIn(page, "alice-dev", "Valid-password-123!");\n'
               '  await fill(page, "Username or email", "no-such-user");\n  await fill(page, "Password", "x");\n'
               '  await click(page, { role: "button", name: "Sign in" });\n});\n')
    assert invented_accounts(sign_in, node) == ["alice-dev"], invented_accounts(sign_in, node)
    registered = sign_in.replace('test("REQ-1-3: a", async ({ page }) => {\n',
                                 'test("REQ-1-3: a", async ({ page }) => {\n  await fill(page, "Username", "alice-dev");\n'
                                 '  await click(page, { role: "button", name: "Create account" });\n')
    assert invented_accounts(registered, node) == []

    composite = dict(node, requirement_text="Seed values: repository `acme-docs`, owner `alice-dev`. Titled owner/repository name.")
    heading = 'const title = "alice-dev/acme-docs";\ntest("x", async ({ page }) => {\n  await see(page, title);\n});\n'
    assert invented_records(heading, composite) == []
    assert invented_records(heading.replace("acme-docs", "made-up-repo"), composite) == ["alice-dev/made-up-repo"]
    from agent.outsider import later_controls
    later = {"later_names": ["Improve onboarding", "Close issue"], "seed_sentences": "- Seed values: issue `Improve onboarding`."}
    assert later_controls('await click(page, "Improve onboarding");\nawait click(page, "Close issue");\n', later) == ["Close issue"]

    wrapped = dict(node, requirement_text="GIVEN: The evaluation seed contains organization `Acme\n            Demo`.")
    assert invented_records('await click(page, "Acme Demo");\n', wrapped) == []
    stated = dict(node, requirement_text="GIVEN: The evaluation seed contains user `alice-dev` and organization `Acme Demo`.")
    assert invented_records(spec, stated) == [] and invented_accounts(sign_in, stated) == []
    import main as agent_main
    assert 'batch["requirement_text"] = whole' in inspect.getsource(agent_main)
    assert "requirement = norm(requirement_text(node))" in inspect.getsource(review_acceptance)
    source = inspect.getsource(review_acceptance) + inspect.getsource(outsider_module.deterministic_review)
    assert "invented_records(spec.content, node)" in source and "invented_accounts(spec.content, node)" in source

@case
def screenshot_data_is_not_handed_over():
    import inspect

    from agent import visual
    notes = ("HEADINGS:\n- first-repo\n- About\n\nBUTTONS:\n- Add file\n- Fork 0\n\nOTHER TEXTS:\n- weiyukong0034-byte\n- 3db48ae\n- main\n\n"
             "LAYOUT:\n- Left: file browser for first-repo with Add file.\n")
    requirement = "The repository page offers `Add file` and a branch selector showing `main`. The `About` panel ..."
    out = visual.requirement_texts_only(notes, requirement)
    for data in ("first-repo", "weiyukong0034-byte", "3db48ae", "Fork 0"):
        assert data not in out, (data, out)
    for kept in ("Add file", "About", "main", "HEADINGS:", "LAYOUT:"):
        assert kept in out, (kept, out)
    assert out.count("[screenshot text]") >= 3
    assert "requirement_texts_only(seen[key], whole)" in inspect.getsource(visual.attach_visual_notes)

@case
def a_stated_unique_control_found_twice_is_reported():
    import tempfile

    from agent.detaudit import deterministic_findings, stated_unique
    text = ("Visitors use the unique “Sign in” link on the home page. The header has exactly one button named "
            "“Account menu”. Each label row contains a unique “Save” button.")
    assert stated_unique(text) == [("link", "Sign in"), ("button", "Account menu")]
    aria = ('- banner:\n  - link "Sign in":\n    - /url: /signin\n- navigation:\n  - link "Sign in":\n    - /url: /signin\n'
            '- main:\n  - list:\n    - listitem:\n      - button "Account menu"\n    - listitem:\n      - button "Account menu"\n')
    with tempfile.TemporaryDirectory() as tmp:
        node = {"id": "REQ-1-1-1", "name": "Register", "description": text}
        problems = " ".join(f["problem"] for f in deterministic_findings(Path(tmp), node, [{"route": "/", "aria": aria}], log=lambda m: None))
        assert "2 link elements named `Sign in`" in problems, problems
        assert "Account menu" not in problems

@case
def api_tests_await_the_db_and_a_crashed_run_books_nothing():
    import inspect

    import main as factory_module
    from agent import test_author
    from agent.testrun import framework_api_misuse
    rel = "backend/tests/REQ-2.7.test.js"
    head = 'const { all, get } = require("../src/database/db_runtime");\n'
    assert any("await all(...)" in p for p in framework_api_misuse(rel, head + "const rows = all('SELECT 1');\n"))
    assert not any("db_runtime" in p for p in framework_api_misuse(rel, head + "const rows = await all('SELECT 1');\n"))
    assert not any("db_runtime" in p for p in framework_api_misuse(rel, "const rows = all('SELECT 1');\n"))
    assert "are ASYNC" in inspect.getsource(test_author)

    booked = []

    class Trace:
        def set_test_status(self, test_id, passed):
            booked.append((test_id, passed))
    factory = factory_module.Factory.__new__(factory_module.Factory)
    factory.trace = Trace()
    factory.test_ids_by_file = {"backend/test-e2e/REQ-1.spec.js": ["REQ-1.TEST.a"], "backend/test-e2e/REQ-2.spec.js": ["REQ-2.TEST.b"]}
    factory.test_titles = {"REQ-1.TEST.a": "REQ-1: a", "REQ-2.TEST.b": "REQ-2: b"}
    factory.record_run(list(factory.test_ids_by_file), False, "TypeError: test is not a function\n")
    assert booked == [("REQ-1.TEST.a", None), ("REQ-2.TEST.b", None)], booked
    booked.clear()
    factory.record_run(list(factory.test_ids_by_file), False, "  1) test-e2e\\REQ-1.spec.js:3:1 › REQ-1: a ───\n")
    assert booked == [("REQ-1.TEST.a", False), ("REQ-2.TEST.b", True)], booked

@case
def api_tests_are_aligned_with_the_application_but_keep_the_requirement():
    import tempfile

    import main as factory_module
    from agent.test_author import AuthoredFile, lost_requirement_facts
    from agent.testrun import TestOutcome
    requirement = "A duplicate username displays “Username already exists” and the server answers 409."
    before = 'expect(res.status).toBe(409);\nexpect(res.body.error).toBe("Username already exists");\nawait request(app).post("/api/signup");\n'
    good = 'expect(res.status).toBe(409);\nexpect(res.body.message).toBe("Username already exists");\nawait request(app).post("/api/register");\n'
    assert lost_requirement_facts(before, good, requirement) == []
    assert lost_requirement_facts(before, good.replace("409", "500"), requirement) == ["409"]
    assert lost_requirement_facts(before, good.replace("Username already exists", "Registration failed"), requirement) == ["Username already exists"]

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        rel = "backend/tests/REQ-1.test.js"
        (project / "backend" / "tests").mkdir(parents=True)
        (project / rel).write_text(before, encoding="utf-8")
        (project / "backend" / "src" / "routes").mkdir(parents=True)
        (project / "backend" / "src" / "routes" / "auth.js").write_text("router.post('/register', ...)\n", encoding="utf-8")
        factory = factory_module.Factory.__new__(factory_module.Factory)
        factory.project_dir, factory.llm = project, None
        factory.over_budget = lambda: False
        written, runs = [], []
        factory.record_run = lambda files, ok, output: runs.append(ok)
        factory.write_tests = lambda req_id, files: [written.append(f.path) or (project / f.path).write_text(f.content, encoding="utf-8") for f in files]
        outcomes = []
        saved = (factory_module.run_node_tests, factory_module.TestAuthor, factory_module.static_test_defects)
        try:
            factory_module.run_node_tests = lambda project_dir, files: outcomes.pop(0)
            factory_module.static_test_defects = lambda project_dir, files: ""
            answer = {}

            class Author:
                def __init__(self, llm, log=print):
                    pass

                def reconcile(self, node, prefix, files, failures, sources):
                    assert "router.post('/register'" in sources
                    return [AuthoredFile(path=rel, content=answer["text"])]
            factory_module.TestAuthor = Author
            node = {"id": "REQ-1", "name": "Sign up", "description": requirement}

            answer["text"] = good
            outcomes[:] = [TestOutcome(False, "FAIL tests/REQ-1.test.js > x"), TestOutcome(True, "")]
            factory.reconcile_api_tests(node, "REQ-1", [rel, "backend/test-e2e/REQ-1.spec.js"])
            assert written == [rel] and runs == [False, True], (written, runs)

            (project / rel).write_text(before, encoding="utf-8")
            written.clear(); runs.clear()
            answer["text"] = good.replace("409", "500")
            outcomes[:] = [TestOutcome(False, "FAIL tests/REQ-1.test.js > x")]
            factory.reconcile_api_tests(node, "REQ-1", [rel])
            assert written == [] and (project / rel).read_text(encoding="utf-8") == before

            factory_module.static_test_defects = lambda project_dir, files: "does not parse"
            answer["text"] = good
            outcomes[:] = [TestOutcome(False, "FAIL tests/REQ-1.test.js > x")]
            factory.reconcile_api_tests(node, "REQ-1", [rel])
            assert (project / rel).read_text(encoding="utf-8") == before
        finally:
            factory_module.run_node_tests, factory_module.TestAuthor, factory_module.static_test_defects = saved

@case
def one_title_twice_inside_an_item_is_reported():
    import tempfile

    from agent.audit import SNAPSHOT_SCRIPT
    from agent.conventions import PROJECT_CONVENTIONS
    from agent.detaudit import deterministic_findings
    assert "entry.titleTwice" in SNAPSHOT_SCRIPT
    assert "exactly one inside" in PROJECT_CONVENTIONS
    with tempfile.TemporaryDirectory() as tmp:
        node = {"id": "REQ-2.8", "name": "Pin", "description": "A pinned note exposes the title `Pinned`."}
        snap = [{"route": "/", "aria": "", "titleTwice": [{"title": "Pinned", "count": 2, "item": "Sprint goals"}]}]
        problems = " ".join(f["problem"] for f in deterministic_findings(Path(tmp), node, snap, log=lambda m: None))
        assert "same title on two or more elements inside them" in problems, problems
        snap = [{"route": "/", "aria": "", "titleTwice": []}]
        assert "same title on two" not in " ".join(f["problem"] for f in deterministic_findings(Path(tmp), node, snap, log=lambda m: None))

@case
def repeated_sign_ins_and_named_cells_are_spec_defects():
    import inspect

    from agent import outsider
    from agent.outsider import HELPER_JS, non_coordinate_cells
    from agent.testrun import repeated_sign_ins
    rel = "backend/test-e2e/REQ-1-1-3.spec.js"
    submit = 'await click(page, { role: "button", name: "Sign in" });\n'
    twice = 'const { test } = require("@playwright/test");\n\ntest("REQ-1-1-3: a", async ({ page }) => {\n' + submit + submit + "});\n"
    assert repeated_sign_ins(rel, twice)
    fresh = twice.replace(submit + submit, submit + "await freshSession(page);\n" + submit)
    assert repeated_sign_ins(rel, fresh) == []
    signed_out = twice.replace(submit + submit, submit + 'await click(page, { role: "link", name: "Sign out" });\n' + submit)
    assert repeated_sign_ins(rel, signed_out) == []
    assert repeated_sign_ins(rel, twice.replace(submit + submit, submit)) == []
    assert "async function freshSession" in HELPER_JS and "module.exports = { test, freshSession," in HELPER_JS
    assert "freshSession(page)" in inspect.getsource(outsider)
    node = {"id": "REQ-1-3-1", "name": "Import", "description": "Imported values appear in the grid.",
            "reference_requirements": "Grid cells use the ARIA gridcell role with their cell coordinates as accessible names (for example, A1)."}
    spec = 'await see(page, { role: "gridcell", name: "Name" });\nawait see(page, { role: "gridcell", name: "B2" });\n'
    assert non_coordinate_cells(spec, node) == ["Name"]
    assert non_coordinate_cells(spec, dict(node, reference_requirements="A data table lists rows.")) == []

@case
def html_tags_are_looked_up_as_tags():
    from agent.outsider import HELPER_JS
    from agent.testrun import tags_as_names
    rel = "backend/test-e2e/REQ-3.spec.js"
    assert tags_as_names(rel, 'const marks = locate(page, "mark");')
    assert tags_as_names(rel, 'const marks = tagged(page, "mark");') == []
    assert tags_as_names(rel, 'await expect(locate(page, "Sheet1")).toBeVisible();') == []
    assert "function tagged(page, tag, within)" in HELPER_JS and "module.exports = { test, freshSession, tagged," in HELPER_JS

@case
def the_helper_offers_the_named_gestures():
    import inspect
    import re as re_module
    from agent import outsider
    exported = set(re_module.search(r"module\.exports\s*=\s*\{([^}]*)\}", outsider.HELPER_JS).group(1).replace(" ", "").split(","))
    gestures = {"doubleClick", "rightClick", "drag", "upload", "download", "allowClipboard", "clipboardText"}
    assert gestures <= exported
    for source in (inspect.getsource(outsider),):
        for name in gestures:
            assert f"{name}(page" in source and f" {name}," in source, name

    assert '"name,age\\nAda,36\\n"' in " ".join(v for v in vars(outsider).values() if isinstance(v, str))

@case
def the_rules_of_other_modules_reach_every_batch():
    import main as factory_module
    tree = {"id": "ROOT", "children": [
        {"id": "REQ-1", "name": "Access", "description": "Opens workbooks. Grid cells use the ARIA gridcell role. The team likes blue.",
         "children": [{"id": "REQ-1-1", "name": "a", "description": "x", "children": []}]},
        {"id": "REQ-3", "name": "Editing", "description": "Edit cells.", "children": [{"id": "REQ-3-1", "name": "b", "description": "y", "children": []}]},
    ]}
    rules = factory_module.shared_module_rules(tree)
    assert "gridcell" in rules["REQ-1"] and "Edit cells." in rules["REQ-3"]
    tree["children"][0]["description"] += " The team likes green." * 80
    long_rules = factory_module.shared_module_rules(tree)["REQ-1"]
    assert "gridcell" in long_rules and "blue" not in long_rules and "green" not in long_rules
    work = [{"id": "REQ-1-1", "ancestor_context": "[REQ-1] Access: Opens workbooks. Grid cells use the ARIA gridcell role."},
            {"id": "REQ-3-1", "ancestor_context": "[REQ-3] Editing: Edit cells."}]
    factory_module.attach_shared_rules(work, rules)
    assert work[0]["ancestor_context"].count("gridcell") == 1
    assert "gridcell" in work[1]["ancestor_context"]
    import inspect
    assert "attach_shared_rules(work, shared_module_rules(tree))" in inspect.getsource(factory_module.Factory.run)

@case
def a_notification_message_beside_its_button_is_found_in_the_sources():
    import tempfile

    from agent.conventions import PROJECT_CONVENTIONS
    from agent.detaudit import deterministic_findings, mixed_notifications
    mixed = ('<div role="status" aria-live="polite">\n  {toast && (\n    <span className="x">\n      {toast.message}\n'
             '      {toast.undo && (\n        <button type="button" onClick={undo}>Undo</button>\n      )}\n    </span>\n  )}\n</div>')
    wrapped = mixed.replace("{toast.message}", "<span>{toast.message}</span>")
    assert mixed_notifications(mixed) and mixed_notifications(wrapped) == []
    assert mixed_notifications('<div role="alert">{error}</div>') == []
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "frontend" / "src").mkdir(parents=True)
        (Path(tmp) / "frontend" / "src" / "Toast.tsx").write_text(mixed, encoding="utf-8")
        found = deterministic_findings(Path(tmp), {"id": "REQ-2.3.1", "name": "Delete", "description": "x"}, [], log=lambda m: None)
        assert any("bare text node next to its button" in f["problem"] for f in found)
    assert "<span>{message}</span><button>Undo</button>" in PROJECT_CONVENTIONS

@case
def a_lookup_for_a_blank_text_is_a_spec_defect():
    import tempfile

    from agent.outsider import HELPER_JS
    from agent.testrun import blank_lookups, static_test_defects
    rel = "backend/test-e2e/REQ-1-2.spec.js"
    assert len(blank_lookups(rel, 'await notSee(page, "   ");\nawait seeOnce(page, \'\');\nawait see(page, "Sheet1");')) == 2
    assert blank_lookups(rel, 'await notSee(page, " Old name ");') == []
    assert blank_lookups("backend/tests/x.test.js", 'await notSee(page, "  ");') == []
    assert "SPEC DEFECT: notSee() was given an empty text" in HELPER_JS
    with tempfile.TemporaryDirectory() as tmp:
        spec = Path(tmp) / rel
        spec.parent.mkdir(parents=True)
        spec.write_text('const { test } = require("@playwright/test");\nconst { notSee } = require("../e2e-support/outsider");\n'
                        'test("REQ-1-2: blank", async ({ page }) => {\n  await notSee(page, "   ");\n});\n', encoding="utf-8")
        assert "looks for an empty text" in static_test_defects(Path(tmp), [rel])

@case
def vitest_tests_have_no_jest_global():
    import tempfile

    from agent.testrun import framework_api_misuse, static_test_defects
    for rel in ("backend/tests/REQ-1-1-1.test.js", "frontend/tests/REQ-1-1-1.test.tsx"):
        assert any("no `jest` global" in p for p in framework_api_misuse(rel, "beforeEach(() => { jest?.resetModules?.(); });"))
        assert any("no `jest` global" in p for p in framework_api_misuse(rel, "const f = jest.fn();"))
        assert framework_api_misuse(rel, 'import "@testing-library/jest-dom";\nconst f = vi.fn();') == []
    assert not any("jest" in p for p in framework_api_misuse("backend/test-e2e/x.spec.js", "// jest.fn() is not used here"))
    with tempfile.TemporaryDirectory() as tmp:
        rel = "backend/tests/REQ-1-1-1.test.js"
        test = Path(tmp) / rel
        test.parent.mkdir(parents=True)
        test.write_text('import { test } from "vitest";\ntest("x", () => { jest?.resetModules?.(); });\n', encoding="utf-8")
        assert "no `jest` global" in static_test_defects(Path(tmp), [rel])

@case
def time_asleep_is_not_budget_spent():
    import inspect
    import time as time_module

    import main as factory_module
    from agent import clock, codegen

    before = clock.paused_sec()
    start = clock.now()
    clock.note_gap(3600.0, log=lambda message: None)
    assert clock.paused_sec() - before == 3600.0
    assert clock.now() - start < 5 and abs((time_module.time() - clock.now()) - clock.paused_sec()) < 1

    for source in (inspect.getsource(factory_module.Factory), inspect.getsource(codegen.CodegenImplementer.implement)):
        assert "time.time()" not in source and "clock.now()" in source
    clock._paused -= 3600.0

@case
def a_batch_may_not_remove_stated_seed_values():
    import inspect

    import main as factory_module
    seeds = ("SEED SENTENCES ...\n- The evaluation seed contains the seeded worksheet range `A1:C6` with headers "
             "`Region/Sales/Status` and rows `East/1200/Open`, `North/800/Closed`, `South/700/Open`.\n"
             "- The evaluation seed contains the seeded workbook `Q3 Sales`, range `A1:B2` containing `Item/Qty` and `Pen/4`.")
    before = {"backend/src/database/init_db.js": b"['A1','Region'],['A2','East'],['B2','1200'],['C2','Open'],['A3','North'],['B3','800'],"
                                                  b"['C3','Closed'],['A4','South'],['B4','700'],'Q3 Sales'"}
    after = {"backend/src/database/init_db.js": b"['A1','Region'],['A2','West'],['B2','500'],['C2','Open'],['A3','East'],['B3','1200'],"
                                                 b"['A4','South'],['B4','1500'],['C4','Closed'],'Q3 Sales'"}
    assert factory_module.lost_seed_values(seeds, before, after) == ["North", "800", "700"], factory_module.lost_seed_values(seeds, before, after)

    assert factory_module.lost_seed_values(seeds, before, before) == []
    source = inspect.getsource(factory_module.Factory)
    assert "lost_seed_values(" in source and "the batch removed stated seed values" in source

@case
def filling_a_grid_cell_is_a_spec_defect():
    from agent.testrun import spec_api_misuse
    rel = "backend/test-e2e/REQ-2-2-1.spec.js"
    helper = ('async function setCell(page, coord, value) {\n  await click(page, { role: "gridcell", name: coord });\n'
              '  await fill(page, coord, value);\n  await press(page, "Enter");\n}\n')
    assert any("grid cell" in p for p in spec_api_misuse(rel, helper))
    assert any("grid cell" in p for p in spec_api_misuse(rel, 'await fill(page, "B2", "1200");\n'))
    assert any("grid cell" in p for p in spec_api_misuse(rel, 'await fill(page, { role: "gridcell", name: "B2" }, "1200");\n'))
    good = helper.replace("await fill(page, coord, value);", 'await fill(page, { role: "textbox", name: "Formula bar" }, value);')
    assert not any("grid cell" in p for p in spec_api_misuse(rel, good))
    assert not any("grid cell" in p for p in spec_api_misuse(rel, 'await fill(page, "Q3 Sales", "x");\n'))

@case
def every_failing_spec_of_a_cut_suite_run_is_found():
    import json
    import tempfile

    import main as factory_module
    from agent import verify

    cut = ("Running 110 tests using 1 worker\n  1) test-e2e/REQ-1-1-3.spec.js:47:1 › REQ-1-1-3: Scenario 1 ──\n    Error: x\n"
           "... [middle of the output omitted] ...\n"
           "  14) test-e2e/REQ-6-6.spec.js:18:1 › REQ-6-6: Scenario 1 ──\n    Error: y\n"
           "  3 failed\n    test-e2e/REQ-3.part2.spec.js:4:1 › REQ-3-3: View a Public Repository Overview - Scenario 1 ─\n"
           "    test-e2e/REQ-4-3-3.spec.js:12:1 › REQ-4-3-3: Change the Repository Default Branch - Scenario 1 ─\n"
           "    test-e2e/REQ-6-6.spec.js:18:1 › REQ-6-6: Scenario 1 ─\n  96 passed (7.5m)\n")
    assert factory_module.failed_spec_names(cut) == {"REQ-1-1-3.spec.js", "REQ-3.part2.spec.js", "REQ-4-3-3.spec.js", "REQ-6-6.spec.js"}

    assert factory_module.failed_spec_names("FAILED SPEC FILES: REQ-3-1.spec.js, REQ-3.part2.spec.js\n" + cut) == {"REQ-3-1.spec.js", "REQ-3.part2.spec.js"}
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "r.json"
        report.write_text(json.dumps({"suites": [{"file": "REQ-3.part2.spec.js", "specs": [
            {"file": "REQ-3.part2.spec.js", "tests": [{"status": "unexpected", "results": [{"status": "failed"}]}]}]},
            {"file": "REQ-1-1-2.spec.js", "specs": [{"file": "REQ-1-1-2.spec.js", "tests": [{"status": "expected", "results": [{"status": "passed"}]}]}]}]}),
            encoding="utf-8")
        assert verify.failing_spec_files(report) == ["REQ-3.part2.spec.js"]
    assert '"--reporter=line,json"' in __import__("inspect").getsource(verify._run_e2e_once)

    calls = []

    def fake_once(project_dir, spec_files, log=print):
        calls.append(list(spec_files))
        name = Path(spec_files[0]).name
        ok = name != "REQ-3-1.spec.js"
        return verify.StepResult(ok, "" if ok else "FAILED SPEC FILES: REQ-3-1.spec.js\n  1) test-e2e/REQ-3-1.spec.js:4:1 x")

    original = verify._run_e2e_once
    verify._run_e2e_once = fake_once
    try:
        result = verify.run_e2e(Path("."), ["backend/test-e2e/REQ-1-2-2.spec.js", "backend/test-e2e/REQ-3-1.spec.js",
                                            "backend/test-e2e/REQ-4-1.spec.js"], log=lambda m: None)
    finally:
        verify._run_e2e_once = original
    assert calls == [["backend/test-e2e/REQ-1-2-2.spec.js"], ["backend/test-e2e/REQ-3-1.spec.js"], ["backend/test-e2e/REQ-4-1.spec.js"]]
    assert not result.ok and factory_module.failed_spec_names(result.output) == {"REQ-3-1.spec.js"}

@case
def a_per_file_e2e_check_keeps_the_meaning_of_its_results():
    import inspect

    import main as factory_module
    from agent import verify
    specs = ["backend/test-e2e/REQ-1.spec.js", "backend/test-e2e/REQ-2.spec.js", "backend/test-e2e/REQ-3.spec.js"]
    calls = []

    def run(answers, **kw):
        calls.clear()

        def fake_once(project_dir, spec_files, log=print):
            calls.append([Path(s).name for s in spec_files])
            return answers.get(Path(spec_files[0]).name, verify.StepResult(True, "ok"))
        original = verify._run_e2e_once
        verify._run_e2e_once = fake_once
        try:
            return verify.run_e2e(Path("."), specs, log=lambda m: None, **kw)
        finally:
            verify._run_e2e_once = original

    hang = verify.StepResult(False, "E2E FAILED against the real application\nNo spec was run.\nthe /api chain hangs")
    result = run({"REQ-1.spec.js": verify.StepResult(False, "FAILED SPEC FILES: REQ-1.spec.js\n1) x"), "REQ-2.spec.js": hang})
    assert not result.ok and factory_module.failed_spec_names(result.output) == set() and "REQ-1.spec.js" in result.output
    assert calls == [["REQ-1.spec.js"], ["REQ-2.spec.js"]]
    result = run({"REQ-1.spec.js": verify.StepResult(False, "backend did not become ready for e2e")})
    assert not result.ok and factory_module.failed_spec_names(result.output) == set() and len(calls) == 1
    result = run({"REQ-1.spec.js": verify.StepResult(False, "command not found: [WinError 2] npx")})
    assert not result.ok and factory_module.failed_spec_names(result.output) == set() and len(calls) == 1

    skip = verify.StepResult(True, "e2e skipped: the port was taken by another process", skipped=True)
    result = run({"REQ-1.spec.js": verify.StepResult(False, "FAILED SPEC FILES: REQ-1.spec.js\n1) x"), "REQ-2.spec.js": skip})
    assert not result.skipped and not result.ok and factory_module.failed_spec_names(result.output) == {"REQ-1.spec.js"}
    assert "1 spec file(s) not run" not in result.output and "2 spec file(s) not run" in result.output
    assert run({"REQ-1.spec.js": skip}).skipped

    result = run({"REQ-3.spec.js": verify.StepResult(False, "Error: SyntaxError in REQ-3.spec.js")})
    assert factory_module.failed_spec_names(result.output) == {"REQ-3.spec.js"} and "Passed: REQ-1.spec.js, REQ-2.spec.js" in result.output

    run({}, shared_data=True)
    assert calls == [["REQ-1.spec.js", "REQ-2.spec.js", "REQ-3.spec.js"]]
    source = inspect.getsource(factory_module.Factory.repair_failing_specs)
    assert source.count("shared_data=True") == 2

    once = inspect.getsource(verify._run_e2e_once)
    assert once.index("finally:") < once.rindex("report.unlink(missing_ok=True)") and "remove_dirs(data_dirs)" in once
    assert ".arc-e2e-report-*.json" in inspect.getsource(factory_module.Factory.clean_runtime_state)

    booked = []

    class Trace:
        def set_test_status(self, test_id, passed):
            booked.append((test_id, passed))
    factory = factory_module.Factory.__new__(factory_module.Factory)
    factory.trace = Trace()
    factory.test_ids_by_file = {specs[0]: ["REQ-1.TEST.a"], specs[1]: ["REQ-2.TEST.b"]}
    factory.test_titles = {"REQ-1.TEST.a": "REQ-1: a", "REQ-2.TEST.b": "REQ-2: b"}
    factory.record_run(specs[:2], False, "FAILED SPEC FILES: REQ-2.spec.js\n1 of 2 spec files passed\n... [middle of the output omitted] ...\n")
    assert booked == [("REQ-1.TEST.a", True), ("REQ-2.TEST.b", False)], booked

    seeds = ("SEED ...\n- The evaluation seed contains user `alice-dev` with email `a@x.test` and password `Valid-password-123!`.\n"
             "- The evaluation seed contains the seeded workbook `Q3 Sales`, range `A1:B2` with `A1=2` and `=A1+B1`.")
    before = {"backend/src/seed.js": b"'Valid-password-123!' 'a@x.test' 'Q3 Sales' 'A1:B2' 'A1=2' '=A1+B1'"}
    assert factory_module.lost_seed_values(seeds, before, {"backend/src/seed.js": b""}) == ["Q3 Sales"]
    assert factory_module.lost_seed_values(seeds, before, {"frontend/src/seed.js": b"'Q3 Sales'"}) == []
    assert factory_module.lost_seed_values(seeds, {"backend/tests/x.test.js": b"'Q3 Sales'"}, {}) == []
    assert factory_module.lost_seed_values(seeds, {"backend/src/testing.js": b"'Q3 Sales'"}, {}) == ["Q3 Sales"]

@case
def unrun_spec_files_are_no_result_and_budgets_hold():
    import main as factory_module
    from agent import clock, verify
    specs = ["backend/test-e2e/REQ-1.spec.js", "backend/test-e2e/REQ-2.spec.js", "backend/test-e2e/REQ-3.spec.js"]

    def run(answers, files=specs, **kw):
        def fake_once(project_dir, spec_files, log=print):
            return answers.get(Path(spec_files[0]).name, verify.StepResult(True, "ok", ran=2))
        original = verify._run_e2e_once
        verify._run_e2e_once = fake_once
        try:
            return verify.run_e2e(Path("."), files, log=lambda m: None, **kw)
        finally:
            verify._run_e2e_once = original

    red = verify.StepResult(False, "SyntaxError: Unexpected token", ran=0)
    assert factory_module.failed_spec_names(run({"REQ-1.spec.js": red}, specs[:1]).output) == {"REQ-1.spec.js"}
    assert factory_module.failed_spec_names(run({"REQ-1.spec.js": verify.StepResult(False, "backend did not become ready for e2e")}, specs[:1]).output) == set()

    broken = verify.StepResult(False, "Error: playwright.config.js: Unexpected token", ran=-1)
    result = run({"REQ-1.spec.js": broken, "REQ-2.spec.js": broken})
    assert not result.ok and factory_module.failed_spec_names(result.output) == set()

    assert factory_module.failed_spec_names(run({"REQ-2.spec.js": red}).output) == {"REQ-2.spec.js"}

    skip = verify.StepResult(True, "e2e skipped: the port was taken by another process", skipped=True)
    result = run({"REQ-1.spec.js": verify.StepResult(False, "FAILED SPEC FILES: REQ-1.spec.js\n1) x", ran=1), "REQ-2.spec.js": skip})
    assert "NOT RUN SPEC FILES: REQ-2.spec.js, REQ-3.spec.js" in result.output
    booked = []

    class Trace:
        def set_test_status(self, test_id, passed):
            booked.append((test_id, passed))
    factory = factory_module.Factory.__new__(factory_module.Factory)
    factory.trace = Trace()
    factory.test_ids_by_file = {s: [f"{Path(s).stem}.T"] for s in specs}
    factory.test_titles = {f"{Path(s).stem}.T": Path(s).stem for s in specs}
    factory.record_run(specs, result.ok, result.output)
    assert booked == [("REQ-1.spec.T", False)], booked

    assert factory_module.final_judgement_reserve(43) == 5815 and factory_module.final_judgement_reserve(0) >= 1800
    small = factory_module.Factory.__new__(factory_module.Factory)
    small.started, small.large_tree, small.time_budget, small.e2e_specs = clock.now(), False, 7200.0, specs
    assert abs(small.closing_budget() - min(small.CLOSING_MAX_SEC, 7200.0 + 3600)) < 5

    seeds = ("SEED ...\n- The evaluation seed contains account `alice-dev` with email `a@x.test` and password `Valid-password-123!` in team `frontend-team`.")
    before = {"backend/src/seed.js": b"'alice-dev' 'a@x.test' 'Valid-password-123!' 'frontend-team'"}
    assert factory_module.lost_seed_values(seeds, before, {"backend/src/seed.js": b"'alice-dev'"}) == ["frontend-team"]
    assert factory_module.lost_seed_values(seeds, {"frontend/src/Seed.spec.tsx": b"'frontend-team'"}, {}) == []
    assert factory_module.lost_seed_values(seeds, before, {"frontend/public/seed.json": b"'alice-dev' 'frontend-team'"}) == []

@case
def a_batch_may_not_rename_what_the_requirement_names():
    import inspect

    import main as factory_module
    requirement = ("The selector is a unique button named \u201cBranch <current branch name>\u201d; it opens a textbox named "
                   "\u201cFind branch\u201d. The creation form has a \u201cSubmit new issue\u201d button. "
                   "Clicking it displays \u201cTitle is required\u201d.")
    before = {"frontend/src/components/BranchSelector.tsx": b'<input aria-label="Find branch" />',
              "frontend/src/pages/NewIssuePage.tsx": b"<button>Submit new issue</button><p>Title is required</p>"}
    after = {"frontend/src/components/BranchSelector.tsx": b'<input aria-label="Find or create a branch" />',
             "frontend/src/pages/NewIssuePage.tsx": b"<button>Create issue</button><p>Title is required</p>"}
    specs = 'await fill(page, "Find branch", "feature");\nawait click(page, "Submit new issue");\nawait see(page, "Title is required");'
    assert factory_module.lost_stated_names(requirement, before, after, specs) == ["Find branch", "Submit new issue"]

    assert factory_module.lost_stated_names(requirement, before, after, 'await fill(page, "Find branch", "x");') == ["Find branch"]
    assert factory_module.lost_stated_names(requirement, before, after, "") == []

    assert factory_module.lost_stated_names(requirement, {}, after, specs) == []
    moved = {"backend/src/routes/issues.js": b"'Submit new issue' 'Find branch'", **after}
    assert factory_module.lost_stated_names(requirement, before, moved, specs) == []

    assert factory_module.lost_stated_names(requirement, {"frontend/tests/x.test.tsx": b"Find branch"}, {}, specs) == []
    assert "Branch <current branch name>" not in factory_module.lost_stated_names(requirement, {"f/x": b"Branch <current branch name>"}, {}, "Branch <current branch name>")

    jsx = {"frontend/src/pages/NewIssuePage.tsx": b"<button type=\"submit\">\n  Submit new issue\n</button>"}
    assert factory_module.lost_stated_names(requirement, jsx, after, specs) == ["Submit new issue"]
    comment = {"backend/src/routes/pulls.js": b"// the Find branch box and branch protection rules"}
    assert factory_module.lost_stated_names(requirement, comment, {}, specs) == []
    assert factory_module.lost_stated_names(requirement, before, after, 'await click(page, "Submit new issue now");') == []
    source = inspect.getsource(factory_module.Factory)
    assert "lost_stated_names(" in source and 'key = f"{req_id}:names"' in source and "STATED NAME CHECK FAILED" in source
    assert "for spec in self.e2e_specs if spec not in e2e_specs" in source

@case
def a_control_name_the_requirement_never_writes_is_a_spec_defect():
    import inspect

    from agent import outsider
    requirement = ("\u201cFind a repository\u201d filters the list. Users create issues from the Issues page. \u201cNew issue\u201d is a link on the Issues page. The creation "
                   "form has fields labeled \u201cTitle\u201d and \u201cDescription\u201d and a \u201cSubmit new issue\u201d button. "
                   "The selector is a unique button named \u201cBranch <current branch name>\u201d; it opens a textbox named "
                   "\u201cFind branch\u201d.")
    node = {"id": "REQ-5-4", "requirement_text": requirement}
    spec = ('await click(page, { role: "button", name: "Create issue" });\n'
            'await fill(page, "Find or create a branch", "draft-feature");\n')
    found = dict(outsider.unstated_control_names(spec, node))
    assert set(found) == {"Create issue", "Find or create a branch"}, found
    assert "Submit new issue" in found["Create issue"] and found["Find or create a branch"][0] == "Find branch"

    fine = ('await click(page, "New issue");\nawait click(page, { role: "button", name: "submit new issue" });\n'
            'await click(page, "Branch main");\nconst renamed = "Issue Tracker Renamed";\nawait fill(page, "Title", renamed);\n'
            'await click(page, "Issue Tracker Renamed");\nawait click(page, "B2");\n')
    assert outsider.unstated_control_names(fine, node) == []

    assert "create issue" in requirement.lower() and "Create issue" in found

    assert outsider.unstated_control_names('await click(page, "Add reaction");\n', node) == []
    assert "unstated_control_names(spec.content, node)" in inspect.getsource(outsider.deterministic_review)

@case
def the_auditor_walks_the_links_and_reports_broken_pages():
    import inspect

    from agent import audit, detaudit
    from agent.settings import SETTINGS
    script = audit.SNAPSHOT_SCRIPT
    assert "crawled < 30" in script and "crawled: true" in script and "blank page: nothing is rendered" in script
    assert "if (entry.console.length || entry.error || (entry.relabeled || []).length) out.push(entry);" in script and ".slice(0, 200))" in script and "(log|sign)[-_ ]?out" in script

    requirement = "The selector is a unique button named “Branch <current branch name>”. A “Fork” button."
    relabeled = [{"route": "/alice-dev/acme-docs", "crawled": True, "console": [], "relabeled": [
        {"tag": "button", "label": "main", "text": "Branch main"},
        {"tag": "button", "label": "Fork", "text": "Fork 3"},
        {"tag": "a", "label": "Open profile", "text": "alice-dev"}]}]
    found = detaudit.deterministic_findings(Path("."), {"id": "REQ-4-3-1", "requirement_text": requirement}, relabeled, log=lambda m: None)
    hits = [f for f in found if "aria-label" in f["problem"]]
    assert len(hits) == 1 and 'aria-label="main"' in hits[0]["problem"] and '"Branch main"' in hits[0]["fix"], hits
    assert script.count("entry.relabeled = ") == 2

    findings = detaudit.deterministic_findings(Path("."), {"id": "REQ-1"}, [
        {"route": "/alice-dev/acme-docs/settings", "crawled": True,
         "console": ["uncaught: Error: Minified React error #31; object with keys {name, head_commit_sha}"]}], log=lambda m: None)
    assert any("React error #31" in f["problem"] for f in findings), findings
    assert "reached by following the pages' links" in inspect.getsource(audit.audit_node)
    assert SETTINGS["audit_llm"] == "always"

@case
def a_request_no_backend_route_answers_is_a_finding():
    import tempfile

    from agent import detaudit
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "src" / "routes").mkdir(parents=True)
        (root / "frontend" / "src" / "pages").mkdir(parents=True)
        (root / "backend" / "src" / "app.js").write_text(
            "const sheets = require('./routes/sheets');\napp.use('/api/workbooks', require('./routes/workbooks'));\n"
            "app.use('/api/workbooks', sheets);\napp.get('/api/health', h);\n", encoding="utf-8")
        (root / "backend" / "src" / "routes" / "workbooks.js").write_text(
            "router.get('/', a);\nrouter.post('/', b);\nrouter.get('/:id', c);\nrouter.patch('/:id', d);\n", encoding="utf-8")
        (root / "backend" / "src" / "routes" / "sheets.js").write_text("router.post('/:id/sheets', e);\n", encoding="utf-8")
        (root / "frontend" / "src" / "api").mkdir(parents=True)
        (root / "frontend" / "src" / "api" / "index.ts").write_text("const apiClient = axios.create({ baseURL: '/api' });\n", encoding="utf-8")
        (root / "frontend" / "src" / "pages" / "Editor.tsx").write_text(
            "import apiClient from '../api';\n"
            "function insert(kind: 'row' | 'col') { return apiClient.post(`/workbooks/${id}/${kind}`, {}); }\n"
            "async function handleUndoRedo(kind: 'undo' | 'redo') {\n  await apiClient.post(`/workbooks/${id}/${kind}`, {});\n}\n"
            "apiClient.get(`/workbooks/${id}`);\napiClient.post('/workbooks', {});\napiClient.post(`/workbooks/${id}/sheets`, {});\n"
            "apiClient.get('/health');\napiClient.get(url);\n", encoding="utf-8")
        found = detaudit.unanswered_api_calls(root)

        assert [(m, p) for m, p, _ in found] == [("POST", "/api/workbooks/*/row"), ("POST", "/api/workbooks/*/col"),
                                                 ("POST", "/api/workbooks/*/undo"), ("POST", "/api/workbooks/*/redo")], found
        findings = detaudit.deterministic_findings(root, {"id": "REQ-3-2-2", "requirement_text": ""}, [], log=lambda m: None)
        assert sum("no route of the backend answers it" in f["problem"] for f in findings) == 4

        (root / "backend" / "src" / "routes" / "pivots.js").write_text("router.get('/:id/pivots', f);\n", encoding="utf-8")
        assert detaudit.unanswered_api_calls(root) == []
        (root / "backend" / "src" / "routes" / "pivots.js").unlink()
        assert len(detaudit.unanswered_api_calls(root)) == 4

        app_js = root / "backend" / "src" / "app.js"
        base = app_js.read_text(encoding="utf-8")
        for extra in ("const r = express.Router();\nr.get('/x', g);\n", "router.use('/sub', sub);\n",
                      "app.use('/api/more', require('./lib/more')(db));\n"):
            app_js.write_text(base + extra, encoding="utf-8")
            assert detaudit.unanswered_api_calls(root) == [], extra
        app_js.write_text(base + "// router.post('/api/workbooks/:id/undo', u);\n/* app.post('/api/workbooks/:id/redo', r); */\n", encoding="utf-8")
        assert len(detaudit.unanswered_api_calls(root)) == 4
        app_js.write_text(base, encoding="utf-8")
        api_ts = root / "frontend" / "src" / "api" / "index.ts"
        api_ts.write_text("const apiClient = axios.create({ baseURL: '/v1' });\n", encoding="utf-8")
        assert detaudit.unanswered_api_calls(root) == []
        api_ts.write_text("const apiClient = axios.create({ baseURL: '/api' });\n", encoding="utf-8")

        editor = root / "frontend" / "src" / "pages" / "Editor.tsx"
        editor.write_text("import apiClient from '../api';\napiClient.patch(`/workbooks/${location.split('/')[2]}/sheets/${sid}`, {});\n", encoding="utf-8")
        assert detaudit.unanswered_api_calls(root) == [("PATCH", "/api/workbooks/*/sheets/*", "frontend/src/pages/Editor.tsx:2")]

@case
def a_seed_record_given_different_contents_is_not_relied_on():
    import inspect

    import main as factory_module
    seeds = ("SEED SENTENCES ...\n"
             "- The evaluation seed contains the seeded workbook `Q3 Sales`, worksheet `Sheet1`, and cell A1 value `Region`.\n"
             "- The evaluation seed contains the seeded workbook `Q3 Sales`, range `A1:B2` containing `Item/Qty` and `Pen/4`.\n"
             "- Seed values: repository `acme-docs`, issue `Improve onboarding`, milestone `Q3 launch`.\n"
             "- Seed values: public repository `acme-docs`, private repository `secret-research`, owner `alice-dev`.")
    note = factory_module.seed_content_differences(seeds)
    assert "`Q3 Sales` is given different contents" in note and "never relies on the cell contents of `Q3 Sales`" in note
    assert "acme-docs" not in note
    assert factory_module.seed_content_differences(seeds.replace("range `A1:B2` containing `Item/Qty` and `Pen/4`", "worksheet `Sheet1`")) == ""
    source = inspect.getsource(factory_module.Factory)
    assert "differences = seed_content_differences(seeds" in source
    assert 'seeds = (seeds + "\\n\\n" + differences).strip()' in source and 'batch["seed_sentences"] = seeds' in source

@case
def a_helper_that_uses_page_without_receiving_it_is_a_spec_defect():
    import inspect

    from agent import testrun
    rel = "backend/test-e2e/REQ-4-2-1.spec.js"
    broken = ('const { test } = require("@playwright/test");\nconst grid = () => region(page, "grid", "Worksheet grid");\n'
              'async function openWorkbook() {\n  await page.goto("/");\n}\n'
              'test("REQ-4-2-1: a", async ({ page }) => {\n  await openWorkbook();\n});\n')
    found = testrun.unbound_page(rel, broken)
    assert len(found) == 2 and "`grid`" in found[0] and "`openWorkbook`" in found[1], found
    fixed = broken.replace("const grid = () =>", "const grid = (page) =>").replace("async function openWorkbook()", "async function openWorkbook(page)")
    assert testrun.unbound_page(rel, fixed) == []
    assert testrun.unbound_page(rel, 'const x = () => "the page is empty";\n') == []
    assert "unbound_page(rel" in inspect.getsource(testrun.static_test_defects)

@case
def a_cut_off_answer_with_a_big_plan_writes_two_files_first():
    import tempfile

    from agent.codegen import CodegenImplementer
    from agent.testrun import TestOutcome
    with tempfile.TemporaryDirectory() as tmp:
        implementer = CodegenImplementer(None, Path(tmp), lambda: None, log=lambda message: None)
        big = ("backend/src/routes/pivots.js -- add the pivot routes\nfrontend/src/pages/EditorPage.tsx -- add the menuitem\n"
               "- `frontend/src/components/PivotDialog.tsx` -- new dialog\nbackend/src/app.js -- register the route")
        implementer._conclude = lambda node_id, analysis, outcome: big
        note = implementer._decision_note("REQ-3-2-2", "x" * 30000, TestOutcome(False, "red"))
        assert "ONLY these files: backend/src/routes/pivots.js, frontend/src/pages/EditorPage.tsx." in note, note
        implementer._conclude = lambda node_id, analysis, outcome: "backend/src/app.js -- register the route"
        assert "ONLY these files" not in implementer._decision_note("REQ-3-2-2", "x", TestOutcome(False, "red"))

@case
def the_architect_reads_every_requirement():
    from agent import contract
    seed = "The evaluation seed contains the seeded workbook `Q3 Sales`."
    long_step = "The user " + "x" * 600 + " ends here."
    leaves = [{"id": f"REQ-{i}", "name": f"Leaf {i}", "description": "d" * 3000,
               "scenarios": [{"name": "s", "steps": [{"keyword": "GIVEN", "content": seed},
                                                     {"keyword": "THEN", "content": long_step if i == 0 else f"step {i}"}]}]}
              for i in range(40)]
    tree = {"id": "ROOT", "children": leaves}
    text = contract._tree_digest(tree, limit=250000)
    assert all(f"- REQ-{i}: Leaf {i}" in text for i in range(40))
    assert text.count(seed) == 1 and "ends here." in text and "d" * 3000 in text
    same = "The user completes the requested workflow."
    for leaf in leaves[:2]:
        leaf["scenarios"][0]["steps"].append({"keyword": "WHEN", "content": same})
    assert contract._tree_digest(tree, limit=250000).count(same) == 2
    small = contract._tree_digest(tree, limit=60000)
    assert all(f"- REQ-{i}: Leaf {i}" in small for i in range(40)) and len(small) <= 60000
    assert "limit=300000 if large" in __import__("inspect").getsource(contract.build_contract)

def _s68_requirement(*leaves: tuple[str, str, list[tuple[str, str]]]) -> str:
    import yaml
    return yaml.safe_dump({"id": "ROOT", "description": "root", "children": [
        {"id": i, "name": i, "description": d, "scenarios": [{"name": "s", "steps": [{"keyword": k, "content": c} for k, c in steps]}]}
        for i, d, steps in leaves]}, sort_keys=False)

@case
def a_stated_control_in_another_role_or_missing_from_its_own_batch_is_a_finding():
    import tempfile

    from agent import detaudit
    own = ("REQ-2", 'The editor toolbar has a button named "Import CSV".', [("WHEN", 'The user activates the "Import CSV" button.')])
    whole = _s68_requirement(
        ("REQ-1", 'Users export the sheet using the button with the accessible name "Export CSV".', []),
        own,
        ("REQ-9", 'A later page has a button named "Later feature".', []))
    node = {"id": "REQ-2", "name": "REQ-2", "description": own[1], "scenarios": [{"name": "s", "steps": [{"keyword": "WHEN", "content": own[2][0][1]}]}],
            "requirement_text": whole}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "frontend" / "src").mkdir(parents=True)
        (root / "frontend" / "src" / "Toolbar.tsx").write_text(
            "export function Toolbar() {\n  return <nav>\n"
            "    <a href=\"/export.csv\">Export CSV</a>\n  </nav>;\n}\n", encoding="utf-8")
        found = detaudit.deterministic_findings(root, node, [], log=lambda m: None)
        role = [f for f in found if "gives it another role" in f["problem"]]
        missing = [f for f in found if "the control does not exist" in f["problem"]]

        assert len(role) == 1 and "Export CSV" in role[0]["requirement"] and role[0]["where"] == "frontend/src/Toolbar.tsx:3", role
        assert "accessible name exactly “Export CSV”" in role[0]["fix"] and "<button" in role[0]["fix"]
        assert [m["requirement"].split("”")[0] for m in missing] == ["(this batch's requirement: a button named “Import CSV"], missing
        assert "accessible name exactly “Import CSV”" in missing[0]["fix"]
        assert not any("Later feature" in f["requirement"] for f in found)

        node["requirement_text"] = whole.replace("export the sheet using", "may also use the link named \"Export CSV\" or export using")
        again = [f for f in detaudit.deterministic_findings(root, node, [], log=lambda m: None) if "gives it another role" in f["problem"]]
        assert len(again) == 1 and "ALSO names a link “Export CSV”: keep that element" in again[0]["fix"], again
        node["requirement_text"] = whole.replace("export the sheet using", "see the dialog named \"Export CSV\" after using")
        again = [f for f in detaudit.deterministic_findings(root, node, [], log=lambda m: None) if "gives it another role" in f["problem"]]
        assert len(again) == 1 and "in place of the element it is now" in " ".join(again[0]["fix"].split()), again

@case
def a_control_the_own_requirement_activates_whose_handler_does_nothing_is_a_finding():
    import tempfile

    from agent import detaudit, inert_controls
    node = {"id": "REQ-5", "name": "Sort", "description": 'Users choose "Sort range" from the "Data" menu.', "scenarios": [],
            "requirement_text": _s68_requirement(("REQ-5", 'Users choose "Sort range" from the "Data" menu.', []),
                                                 ("REQ-6", 'Users click "Create pivot table" in the "Data" menu.', []))}
    app = ("import { useState } from 'react';\nexport default function App() {\n  const [open, setOpen] = useState(false);\n"
           "  return <div><button type=\"button\" aria-expanded={open} onClick={() => setOpen(!open)}>Data</button>\n"
           "  {open && (<div role=\"menu\">\n"
           "    <button type=\"button\" role=\"menuitem\" onClick={() => setOpen(false)}>Sort range</button>\n"
           "    <button type=\"button\" role=\"menuitem\" onClick={() => setOpen(false)}>Create pivot table</button>\n"
           "  </div>)}</div>;\n}\n")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "frontend" / "src").mkdir(parents=True)
        (root / "frontend" / "src" / "App.tsx").write_text(app, encoding="utf-8")
        saved_state, saved_loader = dict(inert_controls._STATE), inert_controls._load_parser
        try:
            if inert_controls.tsx_parser() is not None:
                inert = [f for f in detaudit.deterministic_findings(root, node, [], log=lambda m: None) if "Activating it cannot" in f["problem"]]

                assert [f["where"] for f in inert] == ["frontend/src/App.tsx:6"] and "Sort range" in inert[0]["problem"], inert
                assert "closing a menu is not enough" in inert[0]["fix"]

            inert_controls._STATE.update(parser=None, skip_logged=False)
            inert_controls._load_parser = lambda: None
            lines: list[str] = []
            for _ in range(2):
                assert not any("Activating it cannot" in f["problem"] for f in detaudit.deterministic_findings(root, node, [], log=lines.append))
            assert sum("inert-control check skipped: no TSX parser" in line for line in lines) == 1, lines
        finally:
            inert_controls._STATE.clear()
            inert_controls._STATE.update(saved_state)
            inert_controls._load_parser = saved_loader
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "install_parser_in_background(log=log)" in source
    assert "tree" not in (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "timeout=120" in __import__("inspect").getsource(inert_controls.install_parser_in_background)

@case
def a_stated_name_with_text_run_straight_on_is_a_finding():
    import tempfile

    from agent import detaudit
    whole = _s68_requirement(
        ("REQ-1-1-1", "Each record provides a link whose accessible name is the workbook name.",
         [("GIVEN", "The user opens the workbook home page, clicks the visible `Q3 Sales` workbook"),
          ("GIVEN", 'The seeded book "Book" and the "Sheet1" tab exist; the "Git" link opens GitHub.')]))
    node = {"id": "REQ-4", "name": "Later", "description": "Something else.", "scenarios": [], "requirement_text": whole}
    aria = "\n".join([
        "- main:",
        "  - list:",
        "    - listitem:",
        "      - 'link \"Q3 SalesLast updated: 2026-09-27T15:41:08.865Z\"':",
        "        - /url: /workbooks/1",
        "  - link \"Q3 Sales\"",
        "  - link \"Q3 Sales Last updated\"",
        "  - button \"Books\"",
        "  - button \"Sheet12\"",
        "  - link \"GitHub\"",
    ])
    with tempfile.TemporaryDirectory() as tmp:
        found = detaudit.deterministic_findings(Path(tmp), node, [{"route": "/", "aria": aria}], log=lambda m: None)
    glued = [f for f in found if "run straight" in f["problem"]]

    assert len(glued) == 1 and "“Q3 Sales”" in glued[0]["requirement"] and glued[0]["where"] == "route /", glued
    assert 'Keep the accessible name exactly "Q3 Sales"' in glued[0]["fix"] and "outside the element" in glued[0]["fix"]

@case
def a_stated_button_with_no_handler_at_all_is_a_finding():
    import tempfile

    from agent import detaudit, inert_controls
    own = ('The toolbar provides buttons with the accessible names "Undo" and "Redo"; Ctrl+Z performs the same. '
           'Activate the "Save" button, the "Submit" button, the "Help" option, the "Refresh" button, the "Archive" '
           'button and the "Menu" button. The list contains "Pin". Click "Data validation" in the "Data" menu, '
           'then click "Filter" and click "Sort". Click "Help".')
    node = {"id": "REQ-3", "name": "Undo", "description": own, "scenarios": [],
            "requirement_text": _s68_requirement(("REQ-3", own, []), ("REQ-9", 'A later batch: click the "Export" button.', []))}
    toolbar = "\n".join([
        "import { useState } from 'react';",
        "import { Link } from 'react-router-dom';",
        "export default function Toolbar({ onRefresh }: { onRefresh: () => void }) {",
        "  const [dirty] = useState(false);",
        "  return <div>",
        '    <button type="button" className="b">Undo</button>',
        '    <button type="button"><UndoIcon /> Redo</button>',
        '    <button type="button" disabled={!dirty}>Save</button>',
        '    <form><button>Submit</button></form>',
        '    <div role="menuitemcheckbox"><input type="checkbox" /> Help</div>',
        '    <div onClick={() => onRefresh()}><span>Tools</span><button type="button">Refresh</button></div>',
        '    <button type="button">Archive</button>',
        '    <Link to="/archive">Archive</Link>',
        '    <button type="button" aria-expanded={false}>Menu</button>',
        '    <button type="button">Pin</button>',
        '    <button type="button">Export</button>',
        '    <button type="button" role="menuitem" disabled>Data validation</button>',
        '    <button type="button" aria-disabled="true" onClick={() => onRefresh()}>Filter</button>',
        '    <button type="button" disabled={!dirty} onClick={() => onRefresh()}>Sort</button>',
        "  </div>;",
        "}",
    ])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "frontend" / "src").mkdir(parents=True)
        (root / "frontend" / "src" / "Toolbar.tsx").write_text(toolbar + "\n", encoding="utf-8")
        (root / "frontend" / "src" / "App.tsx").write_text("import Toolbar from './Toolbar';\nexport default function App() { return <Toolbar onRefresh={() => 1} />; }\n", encoding="utf-8")
        if inert_controls.tsx_parser() is None:
            return
        all_found = detaudit.deterministic_findings(root, node, [], log=lambda m: None)
        found = [f for f in all_found if "no event handler at all" in f["problem"]]
        assert [f["where"] for f in found] == ["frontend/src/Toolbar.tsx:6", "frontend/src/Toolbar.tsx:7"], found
        assert "“Undo”" in found[0]["problem"] and "“Redo”" in found[1]["problem"] and "an onClick" in found[0]["fix"]
        assert "keyboard shortcut" in found[0]["fix"]

        at = [f"frontend/src/Toolbar.tsx:{i + 1}" for i, line in enumerate(toolbar.split(NL)) if "Data validation" in line or ">Filter<" in line]
        disabled = [f for f in all_found if "literal disabled attribute" in f["problem"]]
        assert len(at) == 2 and [f["where"] for f in disabled] == at and "Remove the hard-coded disabled" in disabled[0]["fix"], disabled
        assert inert_controls.literally_disabled({"disabled": None}, b"") and not any("“Sort”" in f["problem"] for f in all_found)

        (root / "frontend" / "src" / "Toolbar.tsx").write_text(toolbar.replace('className="b">Undo', 'onClick={() => onRefresh()}>Undo') + "\n", encoding="utf-8")
        again = [f["where"] for f in detaudit.deterministic_findings(root, node, [], log=lambda m: None) if "no event handler at all" in f["problem"]]
        assert again == ["frontend/src/Toolbar.tsx:7"], again
    assert inert_controls.button_names('buttons with the accessible names "Undo" and "Redo"; the "Close" button') == {"undo", "redo", "close"}

@case
def a_stated_display_form_rendered_raw_or_in_part_is_a_finding():
    import tempfile

    from agent import detaudit, display_forms
    whole = _s68_requirement(
        ("REQ-1", 'Each repository row shows a "Public" or "Private" badge. A member shows an "Owner" role.', []),
        ("REQ-2", 'The repository page heading shows "owner/repository name".', []))
    node = {"id": "REQ-7", "name": "Other", "description": "Something else entirely.", "scenarios": [], "requirement_text": whole}
    schema = ("CREATE TABLE repos (id INTEGER, name TEXT, visibility TEXT NOT NULL DEFAULT 'public' CHECK (visibility IN ('public','private')));\n"
              "CREATE TABLE members (id INTEGER, role TEXT NOT NULL DEFAULT 'owner');\n")
    page = "\n".join([
        "export default function RepoPage({ repo, member, repository, user }: any) {",
        "  return <main>",
        "    <span className=\"badge\">{repo.visibility}</span>",
        "    <span>{member.role}</span>",
        "    <h1>{repository.name}</h1>",
        "    <h1>{user.name}</h1>",
        "  </main>;",
        "}",
    ])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "src" / "database").mkdir(parents=True)
        (root / "backend" / "src" / "database" / "schema.sql").write_text(schema, encoding="utf-8")
        (root / "frontend" / "src" / "pages").mkdir(parents=True)
        (root / "frontend" / "src" / "pages" / "RepoPage.tsx").write_text(page + "\n", encoding="utf-8")
        found = [f for f in detaudit.deterministic_findings(root, node, [], log=lambda m: None)
                 if "renders only the last part" in f["problem"] or "stored lower-case word" in f["problem"]]

        assert [f["where"] for f in found] == ["frontend/src/pages/RepoPage.tsx:3", "frontend/src/pages/RepoPage.tsx:5"], found
        assert "“Private” / “Public”" in found[0]["fix"] and "Keep the stored values" in found[0]["fix"]
        assert "“owner/repository name”" in found[1]["fix"]

        (root / "backend" / "src" / "database" / "seed.js").write_text("const repo = { visibility: 'Public' };\n", encoding="utf-8")
        (root / "frontend" / "src" / "pages" / "RepoPage.tsx").write_text(
            page.replace("<h1>{repository.name}</h1>", "<h1>{repository.owner}/{repository.name}</h1>") + "\n", encoding="utf-8")
        assert display_forms.inconsistent_display_forms(whole, root) == []

@case
def a_stated_navigation_edge_with_another_role_or_as_text_is_a_finding():
    import tempfile

    from agent import detaudit
    whole = "\n".join([
        "id: ROOT", "type: FOLDER", "children:",
        "- id: REQ-A", "  description: From the repository's “Settings” link, open the “Manage access” link and activate “Add people or teams”.",
        "- id: REQ-C", "  description: “New issue” is a link on the Issues page.",
        "- id: REQ-D", "  description: “Code” is a link on the repository page.",
        "- id: REQ-E", "  description: “Pull requests” is a link on the repository page.", ""])
    node = {"id": "REQ-Z", "name": "Other", "description": "Something else entirely.", "scenarios": [], "requirement_text": whole}
    files = {
        "App.tsx": ("import { Route, Routes } from 'react-router-dom';\nimport HomePage from './pages/HomePage';\n"
                    "import RepoPage from './pages/RepoPage';\nimport SettingsPage from './pages/SettingsPage';\n"
                    "import IssuesPage from './pages/IssuesPage';\nexport default function App() {\n  return (<Routes>\n"
                    "    <Route path=\"/\" element={<HomePage />} />\n    <Route path=\"/:owner/:repo\" element={<RepoPage />} />\n"
                    "    <Route path=\"/:owner/:repo/settings\" element={<SettingsPage />} />\n"
                    "    <Route path=\"/:owner/:repo/issues\" element={<IssuesPage />} />\n  </Routes>);\n}\n"),
        "components/RepoNav.tsx": ("import { Link, useParams } from 'react-router-dom';\nexport default function RepoNav() {\n"
                                   "  const { owner, repo } = useParams();\n  return <nav><Link to={`/${owner}/${repo}/issues`}>Issues</Link>"
                                   "<Link to={`/${owner}/${repo}/settings`}>Settings</Link></nav>;\n}\n"),
        "pages/HomePage.tsx": "export default function HomePage() { return <main><h1>Home</h1></main>; }\n",
        "pages/IssuesPage.tsx": ("import { Link } from 'react-router-dom';\nimport RepoNav from '../components/RepoNav';\n"
                                 "export default function IssuesPage() { return <main><RepoNav /><Link to=\"new\">New issue</Link></main>; }\n"),
        "pages/SettingsPage.tsx": ("import RepoNav from '../components/RepoNav';\nexport default function SettingsPage() {\n"
                                   "  return (<main><RepoNav /><button type=\"button\" onClick={() => undefined}>Manage access</button></main>);\n}\n"),
        "pages/RepoPage.tsx": ("import RepoNav from '../components/RepoNav';\n"
                               "export default function RepoPage() { return <main><RepoNav /><h2>Code</h2></main>; }\n"),
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, text in files.items():
            (root / "frontend" / "src" / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / "frontend" / "src" / rel).write_text(text, encoding="utf-8")
        lines: list[str] = []
        found = detaudit.deterministic_findings(root, node, [], log=lines.append)
        nav = [f for f in found if f["where"].startswith("REQ-")]

        assert [f["where"] for f in nav] == ["REQ-D: link “Code”"], nav
        assert sum("“Manage access”" in f["requirement"] + f["problem"] for f in found) == 1, found
        assert "only occurs as plain text" in nav[0]["problem"] and "Keep the heading" in nav[0]["fix"]
        assert not any("Pull requests" in f["where"] + f["problem"] for f in found)
        assert any("not reported" in line and "ABSENT REQ-E link “Pull requests”" in line for line in lines), lines
        assert any("nav_edge=1" in line for line in lines), lines
        alone, logged = detaudit.nav_edge_findings(whole, root, [])
        assert [f["where"] for f in alone] == ["REQ-A: link “Manage access”", "REQ-D: link “Code”"], alone
        assert "has another role" in alone[0]["problem"] and "It replaces the control" in alone[0]["fix"] and "<Link to=" in alone[0]["fix"]
        assert logged == ["ABSENT REQ-E link “Pull requests”"], logged

        assert detaudit.nav_edge_findings(detaudit.describe_node(node), root, []) == ([], [])

@case
def seed_identity_only_logs_and_never_hangs_or_raises():
    import os
    import shutil
    import sqlite3
    import tempfile
    import time

    from agent import seed_identity
    req = _s68_requirement(("REQ-2-2-1", "An organization Owner is signed in. Seed data: repository `acme-docs`, organization "
                            "`Acme Demo`, account `alice-dev`, password `pw-123`.", []))
    names = seed_identity.parse_seed_names(req)
    assert ("repository", "acme-docs") in names and ("organization", "Acme Demo") in names and ("account", "alice-dev") in names
    assert seed_identity.credential_accounts(req) == {"alice-dev"}
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        fixture = tmp_path / "fixture.bin"
        con = sqlite3.connect(fixture)
        con.executescript(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT);"
            "CREATE TABLE organizations (id INTEGER PRIMARY KEY, name TEXT);"
            "CREATE TABLE repositories (id INTEGER PRIMARY KEY, name TEXT, owner_id INTEGER);"
            "CREATE TABLE org_members (organization_id INTEGER, user_id INTEGER, role TEXT);"
            "INSERT INTO users VALUES (1, 'alice-dev'), (2, 'bob');"
            "INSERT INTO organizations VALUES (1, 'Acme Demo');"
            "INSERT INTO repositories VALUES (1, 'acme-docs', 1), (2, 'acme-docs', 2);"
            "INSERT INTO org_members VALUES (1, 1, 'Member');")
        con.commit()
        con.close()
        problems, _info = seed_identity.find_duplicates(str(fixture), names)
        assert [(p["kind"], p["name"], p["rows"]) for p in problems] == [("seed_duplicate", "acme-docs", 2)], problems
        roles, _ = seed_identity.check_roles(str(fixture), req)
        assert [(p["account"], p["required_role"], p["data_roles"]) for p in roles] == [("alice-dev", "owner", ["org_members:Member"])]
        if shutil.which("node"):

            project = tmp_path / "app"
            db = project / "backend" / "src" / "database"
            db.mkdir(parents=True)
            (project / "backend" / "node_modules" / "sqlite3").mkdir(parents=True)
            shutil.copy(fixture, db / "fixture.bin")
            (project / "backend" / "app.db").write_text("the real database: must not be copied or touched", encoding="utf-8")
            probe = tmp_path / "probe.json"
            (db / "init_db.js").write_text(
                "const fs = require('fs');\nconst path = require('path');\nmodule.exports = { initializeDatabase: async () => {\n"
                "  fs.writeFileSync(process.env.ARC_SEED_PROBE, JSON.stringify({ dir: __dirname,"
                " db: fs.existsSync(path.join(__dirname, '..', '..', 'app.db')) }));\n} };\n", encoding="utf-8")
            (db / "db_runtime.js").write_text(
                "const fs = require('fs');\nconst path = require('path');\nmodule.exports = {\n"
                "  exec: async (sql) => { const m = /VACUUM INTO '(.*)'/.exec(sql); fs.copyFileSync(path.join(__dirname, 'fixture.bin'), m[1]); },\n"
                "  get: async () => ({}),\n};\n", encoding="utf-8")
            before = sorted((p.relative_to(project).as_posix(), p.stat().st_mtime_ns) for p in project.rglob("*"))
            lines: list[str] = []
            os.environ["ARC_SEED_PROBE"] = str(probe)
            try:
                logged = seed_identity.log_seed_identity(req, project, log=lines.append, time_limit=60)
            finally:
                os.environ.pop("ARC_SEED_PROBE", None)
            seen = json.loads(probe.read_text(encoding="utf-8"))

            assert not Path(seen["dir"]).resolve().is_relative_to(project.resolve()) and seen["db"] is False, seen
            assert len(logged) == 2 and "`acme-docs` exists in 2 rows" in logged[0] and "alice-dev" in logged[1], lines
            assert all(line.startswith("[seed-identity]") for line in lines) and "not reported" in lines[0], lines
            assert sorted((p.relative_to(project).as_posix(), p.stat().st_mtime_ns) for p in project.rglob("*")) == before

            (db / "init_db.js").write_text("setInterval(() => {}, 1000);\nmodule.exports = { initializeDatabase: () => new Promise(() => {}) };\n", encoding="utf-8")
            started, lines = time.time(), []
            assert seed_identity.log_seed_identity(req, project, log=lines.append, time_limit=6) == []
            assert time.time() - started < 20 and any("skipped" in line for line in lines), lines

    saved = seed_identity.check
    try:
        seed_identity.check = lambda *a, **k: 1 / 0
        lines = []
        assert seed_identity.log_seed_identity(req, ".", log=lines.append) == [] and "check failed" in lines[0]
    finally:
        seed_identity.check = saved

    source = (ROOT / "main.py").read_text(encoding="utf-8")
    hook = source.index("log_seed_identity(whole, self.project_dir")
    batches = source.index("nodes = [(node, []) for node in work]")
    assert batches < hook < source.index("self.verify()\n", hook)
    assert "all_passed" in source[batches:hook]
    assert "seed_identity" not in (ROOT / "agent" / "detaudit.py").read_text(encoding="utf-8")

def collect() -> list:

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cases_engine
    import cases_spec
    extra = cases_engine.CASES + cases_spec.CASES
    if (Path(__file__).resolve().parent / "cases_audit.py").is_file():
        import cases_audit
        extra += cases_audit.CASES
    import cases_sweep
    extra += cases_sweep.CASES

    for name in ("cases_seed2", "cases_spec2", "cases_impl2", "cases_shapes", "cases_unquoted", "cases_rollback", "cases_boilerplate", "cases_closing", "cases_isolation", "cases_nofiles", "cases_audit42b", "cases_audit42a", "cases_deadapp", "cases_helpers141", "cases_hubs", "cases_relations", "cases_partialgreen", "cases_entry", "cases_coverage", "cases_pass142", "cases_entry142", "cases_gate142", "cases_engine142", "cases_helper142", "cases_contract142", "cases_context142", "cases_layout142", "cases_commitprobe", "cases_iprobe", "cases_iwire", "cases_tx142", "cases_visual142", "cases_r45a", "cases_r45b", "cases_r45c", "cases_iprobe2", "cases_p141s4", "cases_p141s1", "cases_p141s2", "cases_r46v", "cases_navstrip", "cases_p141s3", "cases_auditparam", "cases_consist2", "cases_statedrole", "cases_r46p", "cases_closeguard", "cases_gridtype", "cases_consist1", "cases_audit43a", "cases_audit43b", "cases_lockfile", "cases_install", "cases_hardend", "cases_newred", "cases_undefid", "cases_p3groups", "cases_timesave", "cases_watchdog", "cases_install2", "cases_install_hook", "cases_v144a", "cases_v144b", "cases_v144b_llm", "cases_v144c", "cases_v144c_visual", "cases_v144e", "cases_v144f", "cases_v144j", "cases_v144wire", "cases_v144g", "cases_v144h", "cases_v144i", "cases_v144k", "cases_v144m", "cases_v144l", "cases_v144q", "cases_v144n", "cases_v144o", "cases_v144p", "cases_r49s", "cases_r49t", "cases_r49u", "cases_r49v", "cases_r49w", "cases_r49x", "cases_r49y", "cases_r49z", "cases_r50s", "cases_r50t", "cases_r50u", "cases_r50x", "cases_r50v", "cases_r50w", "cases_r50y", "cases_v145a", "cases_v145b", "cases_v145c", "cases_v145d", "cases_v145e", "cases_v146a", "cases_v146b", "cases_v146c", "cases_v146d", "cases_v146e", "cases_v147a", "cases_v147b", "cases_v147c", "cases_v148a", "cases_v148b", "cases_v148c", "cases_v148d", "cases_v148e", "cases_v149a", "cases_v149b", "cases_v149c", "cases_v149d", "cases_v149e", "cases_v150a", "cases_v150b", "cases_v150c", "cases_v150d", "cases_v150e", "cases_v151a", "cases_v152a", "cases_v155a", "cases_v155b", "cases_v156a", "cases_v156b", "cases_v157a", "cases_v157b", "cases_v159a", "cases_v157d", "cases_v160a_evolution", "cases_v160a_factory", "cases_v160a_lifecycle", "cases_v160a_route", "cases_v160c_edits", "cases_v160c_rules", "cases_v160b_plan", "cases_v160b_nav", "cases_v160b_walk", "cases_v160c_single", "cases_v160d_wiring", "cases_v160d_bwiring", "cases_v161a_startup", "cases_v161a_grid", "cases_v161a_session", "cases_v162a_seed", "cases_v162b_crossdep", "cases_v162b_probe", "cases_v162c_route", "cases_v163a_llm", "cases_v163c_session", "cases_v163d_staged", "cases_v163e_fixes", "cases_v166_trailing", "cases_v166_visible", "cases_v166_conventions", "cases_v167_dup", "cases_v167_consistency", "cases_v167_choice", "cases_v167_r8", "cases_v168b_strict", "cases_v168c_hold", "cases_v168d_owner", "cases_v168e_choice_spec", "cases_v168r_off", "cases_v168f_prefix", "cases_v168s_dialogs", "cases_v168w_misuse", "cases_v168x_conventions", "cases_v168z_clock", "cases_v168z_status", "cases_v168z_display", "cases_v181_choice_disabled", "cases_v181_audit_settle", "cases_v182_desc_partial_keep", "cases_v182_audit_rotate", "cases_v182_lifecycle_undo", "cases_v182_seed_lock_partial", "cases_v182_seed_identity", "cases_v182_audit_partial", "cases_v182_regression_partial", "cases_v182_audit_carry", "cases_v182_retry_partial", "cases_v182_seed_pending", "cases_v182_quarantine_tests", "cases_v183_slot_recheck", "cases_v183_outsider_review_retry", "cases_v184_count_text", "cases_v184_stated_select", "cases_v184_stated_ui") + tuple(sorted(p.stem for p in Path(__file__).resolve().parent.glob("cases_v163b_*.py"))):
        if (Path(__file__).resolve().parent / f"{name}.py").is_file():
            extra += __import__(name).CASES
    return CASES + [c for c in extra if c not in CASES]

SERIAL_MODULES: tuple = ("cases_v146d", "cases_v144m")

def _module_of(function) -> str:

    return "run" if function.__module__ in ("__main__", "__mp_main__") else function.__module__

def _key(function) -> str:
    return f"{_module_of(function)}.{function.__name__}"

def _serial(function) -> bool:
    module = sys.modules.get(function.__module__)
    return _module_of(function) in SERIAL_MODULES or bool(getattr(module, "SERIAL", False))

def _skip_list(function):
    listed = getattr(sys.modules.get(function.__module__), "SKIPPED", None)
    return listed if isinstance(listed, list) else None

V162_PINNED: dict = {
    "cases_nofiles.a_cut_off_analysis_that_names_an_unshown_file_costs_no_round": {"cutoff_reask": False},
    "cases_nofiles.an_answer_with_files_after_a_cut_off_ends_the_retries": {"cutoff_reask": False},
    "cases_closeguard.cut_answers_and_cut_off_prose_have_separate_allowances": {"cutoff_reask": False},
}

V162_PINNED.update({f"cases_v163d_staged.{name}": {"fixture_visible_name_prompt": False,
                                                     "seed_slot_keep_leftovers": False,
                                                     "slot_protected_recheck": False}
                    for name in ("without_an_extent_finding_staged_on_is_v163", "off_with_an_extent_finding_is_the_v163_one_task")})

V162_PINNED.update({
    "cases_v160c_edits.the_switch_off_restores_the_whole_file_prompts": {"conventions_v166": False},
    "cases_v160d_wiring.the_data_lifecycle_convention_is_evolution_only_and_word_for_word": {"conventions_v166": False, "fixture_idempotent": False},
})

V162_PINNED.update({
    "cases_v166_conventions.on_adds_exactly_the_four_lines_behind_the_project_conventions": {"conventions_v167": False,
                                                                                             "fixture_idempotent": False},
})

V162_PINNED.update({key: {"item_links_advisory": False} for key in (
    "run.item_links_titles_outside_and_mixed_texts_are_reported",
    "cases_consist1.the_check_still_speaks_where_nothing_calls_the_list_links",
    "cases_entry.a_title_the_requirement_states_as_a_link_is_not_turned_into_a_button",
    "cases_entry142.the_record_kind_of_the_statement_covers_a_list_without_the_seeded_name",
    "cases_entry142.item_links_still_fires_without_any_link_statement",
)})

V162_PINNED.update({"cases_v150b.c03_punct": {"findings_owner_batch_v2": False}})

def _run_one(function) -> str:
    pinned = V162_PINNED.get(_key(function))
    if pinned:
        from agent.settings import SETTINGS

        saved = {key: SETTINGS.get(key) for key in pinned}
        SETTINGS.update(pinned)
        try:
            return _run_one_as_is(function)
        finally:
            SETTINGS.update(saved)
    return _run_one_as_is(function)

def _run_one_as_is(function) -> str:
    listed = _skip_list(function)
    before = len(listed) if listed is not None else 0
    try:
        function()
    except unittest.SkipTest as why:
        print(f"SKIP  {function.__name__}: {why}")
        return "skip"
    except Exception:
        print(f"FAIL  {function.__name__}")
        traceback.print_exc(limit=3)
        return "fail"
    if listed is not None and len(listed) > before:
        why = str(listed[-1])
        print(f"SKIP  {function.__name__}: {why[6:] if why.startswith('SKIP  ') else why}")
        return "skip"
    print(f"ok    {function.__name__}")
    return "ok"

def registration_skips(cases: list) -> list:
    seen, lines = set(), []
    names = {c.__module__ for c in cases} | {n for n in list(sys.modules) if n.startswith("cases_")}
    for name in sorted(names):
        listed = getattr(sys.modules.get(name), "SKIPPED", None)
        if isinstance(listed, list) and id(listed) not in seen:
            seen.add(id(listed))
            lines += [str(line) for line in listed if str(line).startswith("SKIP")]
    return lines

def summary(statuses: list, unregistered: list, tail: str = "") -> str:
    passed, skipped, failed = statuses.count("ok"), statuses.count("skip") + len(unregistered), statuses.count("fail")
    return f"{passed}/{len(statuses) + len(unregistered)} self-tests passed, {skipped} skipped, {failed} failed{tail}"

def _print_skips(skip_lines: list) -> None:
    if skip_lines:
        print(f"-- {len(skip_lines)} SKIPPED (not counted as passed):")
        for line in skip_lines:
            print(f"   {line}")

def scratch_cwd():
    import contextlib
    import os
    import shutil
    import tempfile

    @contextlib.contextmanager
    def inside():
        previous, scratch = os.getcwd(), tempfile.mkdtemp(prefix="arc-selftest-cwd-")
        os.chdir(scratch)
        try:
            yield Path(scratch)
        finally:
            os.chdir(previous)
            shutil.rmtree(scratch, ignore_errors=True)
    return inside()

_WORKER_CASES: list = []

def _worker_init(names: list) -> None:
    import io
    from contextlib import redirect_stdout
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    with redirect_stdout(io.StringIO()):
        _WORKER_CASES[:] = collect()

    assert [_key(c) for c in _WORKER_CASES] == names, "worker collected another case list"

def _run_captured(function) -> tuple:
    import io
    import time
    from contextlib import redirect_stderr, redirect_stdout
    buffer, started = io.StringIO(), time.time()
    with redirect_stdout(buffer), redirect_stderr(buffer):
        try:
            status = _run_one(function)
        except BaseException:
            status = "fail"
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=3)
    return status, buffer.getvalue(), time.time() - started

def _worker_run(indices: list) -> list:
    import os
    import time
    progress = os.environ.get("ARC_SELFTEST_PROGRESS")
    results = []
    for index in indices:
        if progress:
            with open(progress, "a", encoding="utf-8") as handle:
                handle.write(f"{os.getpid()} {time.time():.1f} {_key(_WORKER_CASES[index])}" + NL)
        results.append((index, *_run_captured(_WORKER_CASES[index])))
    return results

def _durations_file() -> Path:
    import tempfile
    return Path(tempfile.gettempdir()) / "arc-agent-selftest-durations.json"

def run_parallel(cases: list, jobs: int, unregistered: list | None = None) -> int:
    import os
    import time
    from concurrent.futures import ProcessPoolExecutor, as_completed
    names = [_key(c) for c in cases]
    groups: dict = {}
    serial = []
    for index, function in enumerate(cases):
        (serial if _serial(function) else groups.setdefault(_module_of(function), [])).append(index)
    try:
        known = json.loads(_durations_file().read_text(encoding="utf-8"))
    except Exception:
        known = {}
    order = sorted(groups.values(), key=lambda g: -sum(float(known.get(names[i], 1.0)) for i in g))
    results: dict = {}
    started = time.time()
    limit = float(os.environ.get("ARC_SELFTEST_TIMEOUT", "1200"))
    pool = ProcessPoolExecutor(max_workers=jobs, initializer=_worker_init, initargs=(names,), max_tasks_per_child=1)
    try:
        futures = [pool.submit(_worker_run, group) for group in order]
        for future in as_completed(futures, timeout=limit):
            for index, status, text, seconds in future.result():
                results[index] = (status, text, seconds)
        pool.shutdown(wait=True)
    except Exception:
        traceback.print_exc(limit=3)
        for process in list((getattr(pool, "_processes", None) or {}).values()):
            process.kill()
        pool.shutdown(wait=False, cancel_futures=True)
    for index in serial:
        results[index] = _run_captured(cases[index])
    statuses, skip_lines = [], list(unregistered or [])
    for index, function in enumerate(cases):
        status, text, _ = results.get(index, ("fail", f"FAIL  {function.__name__}" + NL + "  (no result: its worker process died or timed out)" + NL, 0))
        sys.stdout.write(text)
        statuses.append(status if status in ("ok", "skip") else "fail")
        skip_lines += [line for line in text.splitlines() if status == "skip" and line.startswith("SKIP  ")][-1:]
    try:
        known.update({names[i]: round(r[2], 2) for i, r in results.items()})
        _durations_file().write_text(json.dumps(known), encoding="utf-8")
    except Exception:
        pass
    _print_skips(skip_lines)
    print(summary(statuses, list(unregistered or []), f" ({jobs} jobs, {len(order)} modules in fresh processes, "
                  f"{len(serial)} serial cases, {time.time() - started:.0f}s)"))
    return 1 if "fail" in statuses else 0

def main() -> int:
    if not __debug__:

        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    with scratch_cwd():
        cases = collect()
        unregistered = registration_skips(cases)
        if "--jobs" in sys.argv:
            import os
            jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) or (os.cpu_count() or 2)
            if jobs > 1:
                return run_parallel(cases, jobs, unregistered)
        statuses, skip_lines = [], list(unregistered)
        for function in cases:
            statuses.append(_run_one(function))
            if statuses[-1] == "skip":
                skip_lines.append(f"{_key(function)} (skipped at run time)")
        _print_skips(skip_lines)
        print(summary(statuses, unregistered))
        return 1 if "fail" in statuses else 0

if __name__ == "__main__":
    raise SystemExit(main())
