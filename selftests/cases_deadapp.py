from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "selftests"))

NL = chr(10)
ESC = chr(27)
CASES = []

def case(function):
    CASES.append(function)
    return function

REJECTION = [f"{ESC}[31mError: SQLITE_ERROR: no such table: users{ESC}[39m",
             "    at initDatabase (C:\\work\\backend\\src\\db.js:41:11)",
             "    at async start (C:\\work\\backend\\src\\index.js:12:3)",
             "Node.js v22.12.0"]

def dead_world(root, specs, log_text, mode="exits", dead_from=0):
    import cases_isolation as iso

    class World(iso.World):
        def server(self, args, cwd, env=None):
            world = self
            data_dir = env["DATA_DIR"]
            self.data.setdefault(data_dir, {"password": "x", "notes": []})
            self.started.append(data_dir)
            port = int(env["PORT"])
            broken = len(self.started) > dead_from
            state = {"code": 1 if broken and mode == "exits" else None}
            if not broken or mode == "crash_during":
                self.servers[port] = {"dir": data_dir}
            self.last_state, self.last_broken = state, broken

            class Process:
                def poll(self):
                    return state["code"]

            class Server:
                process = Process()

                def stop(self):
                    world.servers.pop(port, None)
                    return log_text if broken else "listening"

                def output_so_far(self):
                    return log_text if broken else "listening"
            return Server()

    world = World(root, specs)
    if mode == "crash_during":
        original = world.run_cmd

        def run_cmd(args, cwd, timeout=600, env=None):
            result = original(args, cwd, timeout, env)
            if "--list" not in args and world.last_broken:
                world.last_state["code"] = 1
            return result
        world.run_cmd = run_cmd
    return world

def refused(db):
    import cases_isolation as iso
    raise iso.SpecError("Error: page.goto: net::ERR_CONNECTION_REFUSED at http://127.0.0.1:9401/")

def all_parsers(output: str):
    import main as factory_module
    from agent.shared_sweep import named_failures
    from agent.verify import NO_SPEC_RAN
    return (factory_module.failed_spec_names(output), named_failures(output), any(m in output for m in NO_SPEC_RAN),
            "FAILED SPEC FILES: " not in output and any(m in output for m in NO_SPEC_RAN),
            factory_module.failed_test_titles(output))

@case
def a_server_that_does_not_start_shows_its_own_output_under_the_old_marker():
    import cases_isolation as iso
    from agent.verify import SERVER_DOWN_HEAD
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        spec = iso.spec_file(root, "REQ-1-1-1.spec.js")
        world = dead_world(root, {"REQ-1-1-1.spec.js": [(10, "REQ-1-1-1: one", iso.passes)]}, NL.join(REJECTION))
        with iso.installed(world) as verify:
            result = verify.run_e2e(root, [spec], log=lambda m: None)
    output = result.output
    assert not result.ok and output.startswith("backend did not become ready for e2e" + NL), output[:200]
    assert SERVER_DOWN_HEAD in output and "no such table: users" in output and "db.js:41:11" in output, output
    assert "exited (exit code 1)" in output and ESC not in output

    assert all_parsers(output) == (set(), [], True, True, {}), all_parsers(output)

    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        spec = iso.spec_file(root, "REQ-1.spec.js")
        world = dead_world(root, {"REQ-1.spec.js": [(10, "REQ-1: one", iso.passes)]}, "listening on port 3000", mode="hangs")
        with iso.installed(world) as verify:
            result = verify.run_e2e(root, [spec], log=lambda m: None)
    assert "still running" in result.output and "listening on port 3000" in result.output, result.output

@case
def the_note_is_bounded_and_nothing_in_it_passes_for_a_parsed_line():
    import cases_isolation as iso
    from agent.verify import SERVER_LINE_PREFIX, server_output_note, SERVER_DOWN_HEAD
    noise = [f"request {i}: GET /api/items 200" for i in range(500)]
    hostile = ["FAILED SPEC FILES: REQ-9.spec.js", "No spec was run.", "backend did not become ready", "command not found: x",
               "  1) test-e2e/REQ-9.spec.js:3:1 › REQ-9: fake ───", "  2 failed", "## REQ-9.spec.js",
               "ReferenceError: goto is not defined", "    at C:\\work\\backend\\test-e2e\\REQ-9.spec.js:3:5",
               "... [middle of the output omitted] ..."]
    note = server_output_note(NL.join(noise + hostile), SERVER_DOWN_HEAD, "state")
    body = note.split(SERVER_DOWN_HEAD, 1)[1]
    shown = [line for line in body.splitlines() if line.startswith(SERVER_LINE_PREFIX)]
    assert len(shown) <= 61 and len(body) <= 4000 + 61 * len(SERVER_LINE_PREFIX) + 200, (len(shown), len(body))
    assert "earlier line(s) omitted" in note and "request 499" in note and "request 400" not in note

    specs = {"REQ-2.spec.js": [(10, "REQ-2: Create a note", refused), (20, "REQ-2: Listed", refused)]}
    outputs = {}
    for label, text in (("hostile", NL.join(REJECTION + hostile)), ("plain", "")):
        with tempfile.TemporaryDirectory() as tmp:
            root = iso.project(tmp)
            spec = iso.spec_file(root, "REQ-2.spec.js")
            world = dead_world(root, specs, text, mode="crash_during")
            with iso.installed(world, PER_TEST_ISOLATION=False) as verify:
                outputs[label] = verify.run_e2e(root, [spec], log=lambda m: None).output
    from agent.codegen import spec_thrown_errors
    from agent.verify import SERVER_EXITED_HEAD
    assert SERVER_EXITED_HEAD in outputs["hostile"] and "no such table: users" in outputs["hostile"], outputs["hostile"]
    assert all_parsers(outputs["hostile"]) == all_parsers(outputs["plain"]), (all_parsers(outputs["hostile"]), all_parsers(outputs["plain"]))
    assert all_parsers(outputs["hostile"])[0] == {"REQ-2.spec.js"} and not all_parsers(outputs["hostile"])[2]

    assert spec_thrown_errors("  1) x" + note, {"backend/test-e2e/REQ-9.spec.js": ""}) == []
    assert spec_thrown_errors("  1) x" + NL + NL.join(hostile[7:9]), {"backend/test-e2e/REQ-9.spec.js": ""}) != []

@case
def the_per_test_isolation_path_carries_the_server_output_too():
    import cases_isolation as iso
    from agent.verify import SERVER_DOWN_HEAD, SERVER_EXITED_HEAD
    specs = {"REQ-2.spec.js": [(10, "REQ-2: Create a note", iso.passes), (20, "REQ-2: Listed", iso.passes)]}
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        spec = iso.spec_file(root, "REQ-2.spec.js")
        world = dead_world(root, specs, NL.join(REJECTION), dead_from=1)
        with iso.installed(world) as verify:
            result = verify.run_e2e(root, [spec], log=lambda m: None)
    assert len(world.started) == 2 and "backend did not become ready" in result.output, result.output
    assert SERVER_DOWN_HEAD in result.output and "no such table: users" in result.output
    assert all_parsers(result.output)[:4] == (set(), [], True, True)

    specs = {"REQ-2.spec.js": [(10, "REQ-2: Create a note", iso.passes), (20, "REQ-2: Listed", refused)]}
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        spec = iso.spec_file(root, "REQ-2.spec.js")
        world = dead_world(root, specs, NL.join(REJECTION), mode="crash_during", dead_from=1)
        with iso.installed(world) as verify:
            result = verify.run_e2e(root, [spec], log=lambda m: None)
    assert result.output.startswith("FAILED SPEC FILES: REQ-2.spec.js" + NL), result.output[:200]
    assert SERVER_EXITED_HEAD in result.output and "no such table: users" in result.output, result.output
    assert all_parsers(result.output)[4] == {"REQ-2.spec.js": {"REQ-2: Listed"}}, all_parsers(result.output)

@case
def the_probe_tells_started_from_down_from_unknown():
    import cases_isolation as iso
    from agent import verify
    saved = {n: getattr(verify, n) for n in ("BackgroundProcess", "wait_http", "api_answers", "free_port", "server_alive")}
    answers = {}

    class Server:
        def __init__(self, args, cwd, env=None):
            self.process = type("P", (), {"poll": lambda s: answers["code"]})()

        def stop(self):
            return answers["log"]
    try:
        verify.BackgroundProcess, verify.api_answers, verify.free_port = Server, (lambda url, **kw: True), (lambda preferred=3000: 3000)
        verify.wait_http = lambda url, timeout=60: (answers["root"], 200 if answers["root"] else 0)
        for root_ok, code, log_text, expected in ((True, None, "", True), (False, 1, "Error: boom", False),
                                                  (False, None, "", None),
                                                  (True, 1, "", None),
                                                  (False, 1, "Error: listen EADDRINUSE: address already in use :::3000", None)):
            answers.update(root=root_ok, code=code, log=log_text)

            with tempfile.TemporaryDirectory() as tmp:
                result, _ = verify.probe_server(Path(tmp), log=lambda m: None)
            assert result.started is expected, (root_ok, code, log_text, result.started)
    finally:
        for name, value in saved.items():
            setattr(verify, name, value)
    assert iso

APP = "frontend/src/App.tsx"
OWN = "backend/test-e2e/REQ-1-1-1.spec.js"

class AppWorld:

    def __init__(self, root: Path):
        self.root = root
        self.probes: list[str] = []
        self.checks: list[str] = []
        self.port_taken = False

    def sources(self) -> str:
        base = self.root / "frontend" / "src"
        return NL.join(p.read_text(encoding="utf-8") for p in sorted(base.rglob("*")) if p.is_file())

    def run_e2e(self, project_dir, specs, log=print):
        from agent.verify import SERVER_DOWN_HEAD, StepResult, server_output_note
        text = self.sources()
        self.checks.append(text)
        if "DEAD" in text:
            return StepResult(False, "backend did not become ready for e2e" + server_output_note(NL.join(REJECTION), SERVER_DOWN_HEAD, "exited"))
        if "OWN-OK" in text:
            return StepResult(True, "1 passed")
        name = Path(specs[0]).name
        return StepResult(False, NL.join([f"FAILED SPEC FILES: {name}", f"  1) test-e2e\\{name}:10:1 › REQ-1-1-1: one ───",
                                          "    Error: nothing visible matches.", "  1 failed", f"    test-e2e\\{name}:10:1 › REQ-1-1-1: one ───"]))

    def build_frontend(self, project_dir, log=print):
        from agent.verify import StepResult
        return StepResult("NOBUILD" not in self.sources(), "")

    def probe_server(self, project_dir, log=print, port=None):
        from agent.verify import StepResult
        text = self.sources()
        self.probes.append(text)
        started = None if self.port_taken else "DEAD" not in text
        return StepResult(bool(started), "probe", started=started), 3000

def implementer_class(script: list[dict]):
    from agent.codegen import ImplementResult

    class ScriptedImplementer:
        def __init__(self, llm, project_dir, check, **options):
            self.project_dir, self.check = project_dir, check

        def write(self, files):
            for rel, content in files.items():
                path = self.project_dir / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

        def implement(self, node, tests, outcome, extra_context=""):
            step = script.pop(0)
            self.write(step.get("write", {}))
            written = list(step.get("write", {})) + list(step.get("after", {}))
            if not step.get("check"):
                self.write(step.get("after", {}))
                return ImplementResult(step["status"], written, "scripted", 0)
            result = self.check()
            self.write(step.get("after", {}))
            status = step.get("status") or ("passed" if result.passed else "budget")
            return ImplementResult(status, written, "scripted", 1, last_check=result.output if status == "passed" else "")

    return ScriptedImplementer

class patched:

    def __init__(self, world: AppWorld, script: list[dict], lines: list[str]):
        self.world, self.script, self.lines = world, script, lines

    def __enter__(self):
        import main as factory_module
        from agent.settings import SETTINGS
        from agent.testrun import TestOutcome
        self.module, self.settings = factory_module, SETTINGS
        names = ("run_e2e", "build_frontend", "probe_server", "Implementer", "run_node_tests", "static_test_defects",
                 "parse_defects_in_output", "lint_application", "log")
        self.saved = {name: getattr(factory_module, name) for name in names}
        self.saved_audit = SETTINGS.get("audit_enabled", True)
        factory_module.run_e2e, factory_module.build_frontend = self.world.run_e2e, self.world.build_frontend
        factory_module.probe_server = self.world.probe_server
        factory_module.Implementer = implementer_class(self.script)
        factory_module.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
        factory_module.static_test_defects = lambda project_dir, files: ""
        factory_module.parse_defects_in_output = lambda output, files: ""
        factory_module.lint_application = lambda project_dir, native: ""
        factory_module.log = self.lines.append
        SETTINGS["audit_enabled"] = False
        return factory_module

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(self.module, name, value)
        self.settings["audit_enabled"] = self.saved_audit
        return False

def make_factory(root: Path, app_before: str, large_tree: bool = True, time_budget: float = 10800.0):
    import cases_rollback as rb
    from agent.test_author import AuthoredFile
    f = rb.make_factory(root, app_before, time_budget)
    f.green_specs, f.e2e_specs, f.file_specs = [], [], {}
    f.large_tree = large_tree
    f.foundation_retried = []
    f.author_tests = lambda node, prefix, contract: [AuthoredFile(path=OWN, content="test('REQ-1-1-1: one', async () => {});" + NL)]
    f.save_checkpoint = lambda *args, **kwargs: None
    return f

def run_batch(app_before: str, script: list[dict], large_tree: bool = True, time_budget: float = 10800.0, port_taken: bool = False):
    lines: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = AppWorld(root)
        world.port_taken = port_taken
        f = make_factory(root, app_before, large_tree, time_budget)
        with patched(world, script, lines):
            passed = f.process_node({"id": "REQ-1-1-1", "name": "Register", "members": ["REQ-1-1-1"]}, 1)
        after = (root / APP).read_text(encoding="utf-8")
    assert not script, f"scripted steps left over: {script}"
    return f, world, lines, after, passed

def put_back(lines: list[str]) -> bool:
    return any("with an application that does not start (it started before this batch)" in line for line in lines)

@case
def a_first_batch_that_leaves_a_dead_application_is_put_back():
    f, world, lines, app, passed = run_batch("base", [{"write": {APP: "base DEAD"}, "check": True}])
    assert app == "base" and put_back(lines) and not passed, lines
    assert f.report["nodes"]["REQ-1-1-1"]["written"] == [] and f.report["nodes"]["REQ-1-1-1"]["status"] == "budget"
    assert world.probes == ["base DEAD", "base"], world.probes

    f, world, lines, app, passed = run_batch("base", [{"write": {APP: "base DEAD"}, "check": True}], large_tree=False)
    assert app == "base" and put_back(lines), lines

@case
def nothing_changes_unless_the_pair_is_clear():

    f, world, lines, app, passed = run_batch("base DEAD", [{"write": {APP: "base DEAD more"}, "check": True}])
    assert app == "base DEAD more" and not put_back(lines) and f.report["nodes"]["REQ-1-1-1"]["written"] == [APP], lines
    assert world.probes == ["base DEAD more", "base DEAD"] and any("did not start before this batch either" in l for l in lines)

    f, world, lines, app, passed = run_batch("base", [{"write": {APP: "base DEAD"}, "check": True, "after": {APP: "base fixed"}}])
    assert app == "base fixed" and not put_back(lines) and world.probes == ["base fixed"], (lines, world.probes)

    f, world, lines, app, passed = run_batch("base", [{"write": {APP: "base wrong"}, "check": True}])
    assert app == "base wrong" and world.probes == [] and not put_back(lines)

    f, world, lines, app, passed = run_batch("base", [{"write": {APP: "base DEAD"}, "check": True, "status": "passed", "after": {APP: "base OWN-OK"}}])
    assert world.probes == [] and app == "base OWN-OK"

    f, world, lines, app, passed = run_batch("base", [{"write": {APP: "base DEAD"}, "check": True}], port_taken=True)
    assert app == "base DEAD" and not put_back(lines) and world.probes == ["base DEAD"]

    f, world, lines, app, passed = run_batch("base", [{"write": {APP: "base DEAD"}, "check": True, "after": {APP: "base DEAD NOBUILD"}}])
    assert app == "base DEAD NOBUILD" and world.probes == [] and not put_back(lines)

    f, world, lines, app, passed = run_batch("base NOBUILD", [{"write": {APP: "base DEAD"}, "check": True}])
    assert app == "base DEAD" and not put_back(lines) and world.probes == ["base DEAD"], (lines, world.probes)

    f, world, lines, app, passed = run_batch("base", [{"write": {APP: "base DEAD"}, "check": True}], time_budget=200.0)
    assert app == "base DEAD" and world.probes == [] and any("too little time" in l for l in lines)

@case
def the_foundation_retry_starts_from_the_restored_state_and_reuses_the_probe():
    lines: list[str] = []
    script = [{"write": {APP: "base DEAD"}, "check": True},
              {"write": {APP: "base DEAD again"}, "check": True}]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = AppWorld(root)
        f = make_factory(root, "base")
        node = {"id": "REQ-1-1-1", "name": "Register", "members": ["REQ-1-1-1"]}
        with patched(world, script, lines):
            ok = f.run_queue([(node, [])], [], "digest", "description")
        after = (root / APP).read_text(encoding="utf-8")
    assert not script and not ok and f.foundation_retried == ["REQ-1-1-1"], lines
    assert after == "base", after

    assert world.checks == ["base DEAD", "base DEAD again"], world.checks

    assert world.probes == ["base DEAD", "base", "base DEAD again"], world.probes
    assert sum(put_back([l]) for l in lines) == 2, lines

@case
def a_retry_that_turns_green_after_the_put_back_is_kept():
    lines: list[str] = []
    script = [{"write": {APP: "base DEAD"}, "check": True}, {"write": {APP: "base OWN-OK"}, "check": True}]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = AppWorld(root)
        f = make_factory(root, "base")
        with patched(world, script, lines):
            ok = f.run_queue([({"id": "REQ-1-1-1", "name": "Register", "members": ["REQ-1-1-1"]}, [])], [], "d", "x")
        after = (root / APP).read_text(encoding="utf-8")
    assert ok and after == "base OWN-OK" and f.green_specs == [OWN], (lines, after)

V129_SPEC = NL.join([
    'const { test } = require("@playwright/test");',
    'const { expect, click, fill, press, see, notSee, item, region, freshSession, locate } = require("../e2e-support/outsider");',
    '',
    'async function openPullRequests(page) {',
    '  await page.goto("/");',
    '  await fill(page, { role: "searchbox", name: "Search" }, "acme-docs");',
    '  await press(page, "Enter");',
    '  await click(page, { role: "link", name: "acme-docs" });',
    '  await click(page, { role: "link", name: "Pull requests" });',
    '}',
    '',
    'async function signIn(page) {',
    '  await page.goto("/");',
    '  await click(page, { role: "link", name: "Sign in" });',
    '  await fill(page, { role: "textbox", name: "Username or email" }, "alice-dev");',
    '  await fill(page, { role: "textbox", name: "Password" }, "Valid-password-123!");',
    '  await click(page, { role: "button", name: "Sign in" });',
    '  await see(page, "alice-dev");',
    '}',
    '',
    'test("REQ-6-2-1: List and Filter Repository Pull Requests - Scenario 1", async ({ page }) => {',
    '  await openPullRequests(page);',
    '  await click(page, { role: "link", name: "Open" });',
    '  await see(page, "Improve onboarding");',
    '  await notSee(page, "Fix search");',
    '  await fill(page, "alice");',
    '  await press(page, "Enter");',
    '  await see(page, "Improve onboarding");',
    '  const row = item(page, "Improve onboarding");',
    '  await see(page, "alice", row);',
    '});',
]) + NL
SPEC = "backend/test-e2e/REQ-6-2-1.spec.js"
HEAD = 'const { test } = require("@playwright/test");' + NL + \
       'const { click, fill, press, choose, see } = require("../e2e-support/outsider");' + NL

@case
def the_signatures_are_read_from_the_helper():
    from agent.testrun import helper_signatures
    found = helper_signatures()
    assert found["fill"][0] == 3 and found["choose"][0] == 3 and found["press"][0] == 2, found
    assert "type" not in found and "select" not in found

@case
def the_v129_call_is_flagged_and_the_correct_calls_of_that_spec_are_not():
    from agent.testrun import helper_arity_defects
    problems = helper_arity_defects(SPEC, V129_SPEC)
    assert len(problems) == 1, problems
    assert problems[0].startswith(SPEC + ":26: `await fill(page, \"alice\");` passes 2 argument(s)"), problems[0]
    assert "fill(page, names, value, within)" in problems[0] and "needs at least 3" in problems[0]

@case
def only_calls_that_are_certainly_short_are_flagged():
    from agent.testrun import helper_arity_defects
    flagged = {
        'await choose(page, "Sort");': "choose",
        'await press(page);': "press",
        'await fill(\n    page,\n    "Title",\n  );': "fill",
        'await Promise.all([fill(page, "Search")]);': "fill",
    }
    for body, name in flagged.items():
        problems = helper_arity_defects(SPEC, HEAD + "test('t', async ({ page }) => {\n" + body + "\n});\n")
        assert len(problems) == 1 and f"helper's {name}(" in problems[0], (body, problems)
    assert helper_arity_defects(SPEC, HEAD + "test('t', async ({ page }) => {\nawait fill(\n    page,\n    \"Title\",\n  );\n});\n")[0].startswith(SPEC + ":4:")
    not_flagged = [
        'await fill(page, "Title", "Groceries");',
        'await fill(page, { role: "textbox", name: "A, B" }, "x");',
        'await fill(page, field, value, dialog);',
        'await fill(page, ["Title", "Name"], `note ${Date.now()}`);',
        'await choose(page, "Sort", "Newest");',
        'await press(page, "Enter");',
        'await fill(page, ...args);',
        'await fill(page, /title/i);',
        'await fill(other, "x");',
        'await page.fill("#title", "x");',
        'await row.fill("x");',
        '// await fill(page, "alice");',
        'const note = "fill(page, \\"alice\\")";',
        'await click(page, "Save");',
    ]
    for body in not_flagged:
        problems = helper_arity_defects(SPEC, HEAD + "test('t', async ({ page }) => {\n" + body + "\n});\n")
        assert problems == [], (body, problems)

    own = HEAD.replace("fill, ", "") + "async function fill(page, name) { await click(page, name); }\ntest('t', async ({ page }) => {\nawait fill(page, \"x\");\n});\n"
    assert helper_arity_defects(SPEC, own) == []
    renamed = HEAD.replace("fill, ", "fill: typeInto, ") + "test('t', async ({ page }) => {\nawait typeInto(page, \"x\");\n});\n"
    assert helper_arity_defects(SPEC, renamed) == []
    assert helper_arity_defects(SPEC, 'const { test } = require("@playwright/test");\nawait fill(page, "x");\n') == []
    assert helper_arity_defects("frontend/tests/App.test.tsx", V129_SPEC) == []

@case
def gate_e1_reports_it():
    from agent.testrun import static_test_defects
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "test-e2e").mkdir(parents=True)
        (root / SPEC).write_text(V129_SPEC, encoding="utf-8")
        report = static_test_defects(root, [SPEC])
    assert SPEC + ":26:" in report and "needs at least 3" in report, report

def main() -> int:
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    failed = 0
    started = time.time()
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} dead-app cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
