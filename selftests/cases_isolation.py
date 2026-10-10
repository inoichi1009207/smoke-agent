from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NL = chr(10)
CASES = []

def case(function):
    CASES.append(function)
    return function

SEED = {"password": "Valid-password-123!", "notes": []}
RULE = "─" * 20

class SpecError(Exception):

    def __init__(self, message: str, frame: int = 0) -> None:
        super().__init__(message)
        self.frame = frame

def change_password(db):
    db["password"] = "New-password-456!"

def seeded_password_signs_in(db):
    if db["password"] != SEED["password"]:
        raise SpecError("Error: expect(locator).toBeVisible() failed" + NL + "Locator: getByText('Signed in as alice-dev')")

def create_note(db):
    db["notes"].append("Groceries")

def note_of_the_test_before(db):
    if "Groceries" not in db["notes"]:
        raise SpecError("Error: expect(locator).toBeVisible() failed" + NL + "Locator: getByText('Groceries')")

def passes(db):
    pass

def throws_goto(db):
    raise SpecError("ReferenceError: goto is not defined", frame=12)

def always_red(db):
    raise SpecError("Error: expect(locator).toBeVisible() failed" + NL + "Locator: getByText('Nothing')")

class World:

    def __init__(self, root: Path, specs: dict[str, list[tuple[int, str, object]]]) -> None:
        self.root = root
        self.specs = specs
        self.servers: dict[int, dict] = {}
        self.data: dict[str, dict] = {}
        self.started: list[str] = []
        self.playwright: list[list[str]] = []
        self.listings = 0
        self.list_fails = False
        self.port = 9400
        self.dead_after = None
        self.skip_after = None

    def free_port(self, preferred=3000):
        self.port += 1
        return self.port

    def server(self, args, cwd, env=None):
        world = self
        data_dir = env["DATA_DIR"]
        self.data.setdefault(data_dir, json.loads(json.dumps(SEED)))
        self.started.append(data_dir)
        up = self.dead_after is None or len(self.started) <= self.dead_after
        port = int(env["PORT"])
        if up:
            self.servers[port] = {"dir": data_dir}

        class Process:
            def poll(self):
                return None if up else 1

        class Server:
            process = Process()

            def stop(self):
                world.servers.pop(port, None)
                return "server log"

            def output_so_far(self):
                return ""
        return Server()

    def wait_http(self, url, timeout=60):
        port = int(url.split(":")[2].split("/")[0])
        return (port in self.servers), (200 if port in self.servers else 0)

    def run_cmd(self, args, cwd, timeout=600, env=None):
        env = env or {}
        report = Path(env.get("PLAYWRIGHT_JSON_OUTPUT_FILE", str(self.root / "unused.json")))
        selection = [a for a in args[3:] if not a.startswith("--")]
        if "--list" in args:
            self.listings += 1
            if self.list_fails:
                return 1, "SyntaxError: Unexpected token"
            suites = []
            for name, tests in self.specs.items():
                if any(name in s for s in selection):
                    suites.append({"title": name, "file": name, "specs": [
                        {"title": title, "file": name, "line": line, "column": 1, "tests": [{"expectedStatus": "passed", "status": "skipped", "results": []}]}
                        for line, title, _ in tests], "suites": []})
            report.write_text(json.dumps({"suites": suites, "errors": []}), encoding="utf-8")
            return 0, ""
        self.playwright.append(selection)
        if self.skip_after is not None and len(self.playwright) > self.skip_after:
            return 1, "Error: browserType.launch: Executable doesn't exist at C:/ms-playwright/chromium"
        port = int(env["PORT"])
        db = self.data[self.servers[port]["dir"]]
        chosen = []
        for item in selection:
            file_part, _, line_part = item.partition(".spec.js")
            name = Path(file_part + ".spec.js").name
            lines = {int(x) for x in line_part.split(":")[1:] if x}
            for line, title, function in self.specs[name]:
                if not lines or line in lines:
                    chosen.append((name, line, title, function))
        out = [f"Running {len(chosen)} test{'s' if len(chosen) != 1 else ''} using 1 worker", ""]
        failed, passed, suites = [], 0, {}
        for index, (name, line, title, function) in enumerate(chosen, 1):
            where = f"test-e2e\\{name}:{line}:1 › {title}"
            out.append(f"[{index}/{len(chosen)}] {where}")
            status = "passed"
            try:
                function(db)
                passed += 1
            except SpecError as exc:
                status = "failed"
                failed.append(where)
                out += [f"  {len(failed)}) {where} {RULE}", ""] + ["    " + part for part in str(exc).split(NL)]
                out += ["", f"        at C:\\work\\backend\\test-e2e\\{name}:{exc.frame or line + 2}:5", ""]
            suites.setdefault(name, []).append({"title": title, "file": name, "line": line, "column": 1,
                                                "tests": [{"status": "expected" if status == "passed" else "unexpected", "results": [{"status": status}]}]})
        if failed:
            out.append(f"  {len(failed)} failed")
            out += [f"    {where} {RULE}" for where in failed]
        if passed:
            out.append(f"  {passed} passed (1.2s)")
        report.write_text(json.dumps({"suites": [{"title": n, "file": n, "specs": s, "suites": []} for n, s in suites.items()],
                                      "stats": {"expected": passed, "unexpected": len(failed), "flaky": 0}}), encoding="utf-8")
        return (1 if failed else 0), NL.join(out)

class installed:

    def __init__(self, world: World, **flags) -> None:
        self.world, self.flags = world, flags

    def __enter__(self):
        from agent import verify
        self.verify = verify
        names = ("run_cmd", "BackgroundProcess", "wait_http", "api_answers", "free_port")
        flags = ("PER_TEST_ISOLATION", "PER_TEST_UNTIL", "PER_FILE_ISOLATION_SEC", "ISOLATED_RUN_EST_SEC")
        self.saved = {n: getattr(verify, n) for n in names + flags}
        verify.run_cmd, verify.BackgroundProcess, verify.wait_http = self.world.run_cmd, self.world.server, self.world.wait_http
        verify.api_answers, verify.free_port = (lambda url, **kw: True), self.world.free_port
        for key, value in self.flags.items():
            setattr(verify, key, value)
        verify._groups_cache.clear()
        return verify

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            setattr(self.verify, key, value)
        self.verify._groups_cache.clear()
        return False

def project(tmp: str) -> Path:
    root = Path(tmp)
    (root / "backend" / "node_modules" / "@playwright" / "test").mkdir(parents=True)
    (root / "backend" / "test-e2e").mkdir(parents=True)
    return root

def spec_file(root: Path, name: str, text: str = "const { test } = require('@playwright/test');\n") -> str:
    (root / "backend" / "test-e2e" / name).write_text(text, encoding="utf-8")
    return "backend/test-e2e/" + name

PASSWORD = [(10, "REQ-1-3: Change Account Password - Scenario 1", change_password),
            (30, "REQ-1-3: Change Account Password - Scenario 2", seeded_password_signs_in)]
DEPENDENT = [(10, "REQ-2: Create a note", create_note), (20, "REQ-2: The note is listed", note_of_the_test_before)]

def titles(output: str) -> dict:
    import main as factory_module
    return factory_module.failed_test_titles(output)

@case
def each_test_of_a_file_runs_on_fresh_data_of_its_own():
    with tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        spec = spec_file(root, "REQ-1-3.spec.js")
        world = World(root, {"REQ-1-3.spec.js": PASSWORD})
        with installed(world) as verify:
            result = verify.run_e2e(root, [spec], log=lambda m: None)

        assert result.ok, result.output
        assert len(world.started) == 2 and len(set(world.started)) == 2, world.started
        assert world.playwright == [["test-e2e/REQ-1-3.spec.js:10"], ["test-e2e/REQ-1-3.spec.js:30"]], world.playwright
        assert result.ran == 2 and "2 passed" in result.output

        assert not any((root / "backend" / ".arc-test-db").glob("*")) and not list((root / "backend").glob(".arc-e2e-*.json"))

@case
def a_test_that_needs_an_earlier_tests_writes_now_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        spec = spec_file(root, "REQ-2.spec.js")
        world = World(root, {"REQ-2.spec.js": DEPENDENT})
        with installed(world) as verify:
            result = verify.run_e2e(root, [spec], log=lambda m: None)
        assert not result.ok
        assert titles(result.output) == {"REQ-2.spec.js": {"REQ-2: The note is listed"}}, titles(result.output)

        world = World(root, {"REQ-2.spec.js": DEPENDENT})
        with installed(world, PER_TEST_ISOLATION=False) as verify:
            assert verify.run_e2e(root, [spec], log=lambda m: None).ok
        assert len(world.started) == 1 and world.playwright == [["test-e2e/REQ-2.spec.js"]]

@case
def the_output_keeps_the_shape_every_caller_parses():
    import main as factory_module
    from agent.codegen import spec_thrown_errors
    from agent.shared_sweep import named_failures
    with tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        red = spec_file(root, "REQ-2.spec.js")
        green = spec_file(root, "REQ-1-3.spec.js")
        world = World(root, {"REQ-2.spec.js": DEPENDENT + [(40, "REQ-2: Open the editor", throws_goto), (50, "REQ-2: Archive", passes)],
                             "REQ-1-3.spec.js": PASSWORD})
        with installed(world) as verify:
            single = verify.run_e2e(root, [red], log=lambda m: None)
            suite = verify.run_e2e(root, [green, red], log=lambda m: None)

            assert verify._run_e2e_once(root, [red], log=lambda m: None).output.startswith("FAILED SPEC FILES: REQ-2.spec.js" + NL)
    for output in (single.output, suite.output):
        assert output.startswith("FAILED SPEC FILES: REQ-2.spec.js" + NL), output[:200]
        assert factory_module.failed_spec_names(output) == {"REQ-2.spec.js"} and named_failures(output) == ["REQ-2.spec.js"]

        assert titles(output) == {"REQ-2.spec.js": {"REQ-2: The note is listed", "REQ-2: Open the editor"}}, titles(output)
        assert "[middle of the output omitted]" not in output
        assert spec_thrown_errors(output, {red: ""}) == ["REQ-2.spec.js: ReferenceError: goto is not defined"]
    assert "1 of 2 spec files passed, each run against fresh data" in suite.output and "Passed: REQ-1-3.spec.js" in suite.output
    assert "  2 failed" in single.output and "  2 passed" in single.output and single.ran == 4

    booked = []

    class Trace:
        def set_test_status(self, test_id, passed):
            booked.append((test_id, passed))
    factory = factory_module.Factory.__new__(factory_module.Factory)
    factory.trace = Trace()
    factory.test_ids_by_file = {red: ["T.create", "T.listed", "T.editor", "T.archive"]}
    factory.test_titles = {"T.create": "REQ-2: Create a note", "T.listed": "REQ-2: The note is listed",
                           "T.editor": "REQ-2: Open the editor", "T.archive": "REQ-2: Archive"}
    factory.record_run([red], False, single.output)
    assert booked == [("T.create", True), ("T.listed", False), ("T.editor", False), ("T.archive", True)], booked

@case
def tests_made_in_a_loop_share_a_run_and_a_passing_one_is_never_read_as_failed():
    with tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        spec = spec_file(root, "REQ-4.spec.js")

        world = World(root, {"REQ-4.spec.js": [(10, "REQ-4: First", always_red), (20, "REQ-4: Loop green", passes), (20, "REQ-4: Loop red", always_red)]})
        with installed(world) as verify:
            result = verify.run_e2e(root, [spec], log=lambda m: None)
        assert world.playwright == [["test-e2e/REQ-4.spec.js:10"], ["test-e2e/REQ-4.spec.js:20"]], world.playwright
        assert len(set(world.started)) == 2

    assert titles(result.output) == {"REQ-4.spec.js": {"REQ-4: First", "REQ-4: Loop red"}}, titles(result.output)
    assert "  2 failed" in result.output and "  1 passed" in result.output

@case
def a_long_output_is_trimmed_without_losing_a_failed_title():
    many = [(10 * i, f"REQ-9: Scenario {i}", always_red) for i in range(1, 9)]
    with tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        spec = spec_file(root, "REQ-9.spec.js")
        world = World(root, {"REQ-9.spec.js": many + [(95, "REQ-9: Green", passes)]})
        original = world.run_cmd

        def noisy(args, cwd, timeout=600, env=None):
            code, out = original(args, cwd, timeout, env)
            if "--list" in args:
                return code, out

            return code, out.replace("Locator:", "Call log:" + NL + (("      - waiting for getByText('Nothing')" + NL) * 400) + "    Locator:")
        world.run_cmd = noisy
        with installed(world) as verify:
            result = verify.run_e2e(root, [spec], log=lambda m: None)
    assert not result.ok and len(result.output) < 20000, len(result.output)
    assert "[middle of the output omitted]" not in result.output
    assert titles(result.output) == {"REQ-9.spec.js": {t for _, t, _ in many}}

@case
def a_file_with_one_test_or_in_serial_mode_or_unlisted_runs_whole():
    with tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        one = spec_file(root, "REQ-1.spec.js")
        serial = spec_file(root, "REQ-2.spec.js", "test.describe.configure({ mode: 'serial' });\n")
        world = World(root, {"REQ-1.spec.js": [(10, "REQ-1: only", passes)], "REQ-2.spec.js": DEPENDENT})
        with installed(world) as verify:
            assert verify.run_e2e(root, [one], log=lambda m: None).ok
            assert verify.run_e2e(root, [serial], log=lambda m: None).ok
        assert world.playwright == [["test-e2e/REQ-1.spec.js"], ["test-e2e/REQ-2.spec.js"]], world.playwright
        assert world.listings == 1

        world = World(root, {"REQ-2.spec.js": PASSWORD})
        world.list_fails = True
        with installed(world) as verify:
            verify.run_e2e(root, [spec_file(root, "REQ-2.spec.js")], log=lambda m: None)
        assert world.playwright == [["test-e2e/REQ-2.spec.js"]]

@case
def short_time_falls_back_to_whole_files():
    from agent import clock
    with tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        spec = spec_file(root, "REQ-2.spec.js")

        world = World(root, {"REQ-2.spec.js": DEPENDENT})
        with installed(world, PER_TEST_UNTIL=clock.now() - 1) as verify:
            assert verify.run_e2e(root, [spec], log=lambda m: None).ok
        assert world.playwright == [["test-e2e/REQ-2.spec.js"]]

        three = DEPENDENT + [(30, "REQ-2: Third", note_of_the_test_before)]
        world = World(root, {"REQ-2.spec.js": [(5, "REQ-2: First", passes)] + three})
        lines = []
        original = world.run_cmd

        def slow(args, cwd, timeout=600, env=None):
            if "--list" not in args:
                time.sleep(0.6)
            return original(args, cwd, timeout, env)
        world.run_cmd = slow
        with installed(world, PER_FILE_ISOLATION_SEC=0.5, ISOLATED_RUN_EST_SEC=0.0) as verify:
            result = verify.run_e2e(root, [spec], log=lines.append)
        assert world.playwright == [["test-e2e/REQ-2.spec.js:5"], ["test-e2e/REQ-2.spec.js:10", "test-e2e/REQ-2.spec.js:20", "test-e2e/REQ-2.spec.js:30"]], world.playwright
        assert result.ok and "ran together" in result.output and any("ran together" in line for line in lines)

        world = World(root, {"REQ-2.spec.js": DEPENDENT})
        with installed(world, PER_FILE_ISOLATION_SEC=0.0) as verify:
            assert verify.run_e2e(root, [spec], log=lambda m: None).ok
        assert world.playwright == [["test-e2e/REQ-2.spec.js"]]

@case
def the_check_deadline_leaves_time_for_the_files_after():
    with tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        specs = [spec_file(root, f"REQ-{i}.spec.js") for i in range(1, 4)]
        world = World(root, {f"REQ-{i}.spec.js": PASSWORD for i in range(1, 4)})

        with installed(world, ISOLATED_RUN_EST_SEC=400.0, PER_FILE_ISOLATION_SEC=10 ** 6) as verify:
            result = verify.run_e2e(root, specs, log=lambda m: None)
            assert verify._check_deadline is None
        assert not result.ok
        assert world.playwright[:2] == [["test-e2e/REQ-1.spec.js"], ["test-e2e/REQ-2.spec.js"]], world.playwright

@case
def a_server_that_does_not_come_up_is_one_global_fault():
    import main as factory_module
    with tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        spec = spec_file(root, "REQ-2.spec.js")
        world = World(root, {"REQ-2.spec.js": DEPENDENT})
        world.dead_after = 1
        with installed(world) as verify:
            result = verify.run_e2e(root, [spec], log=lambda m: None)
        assert not result.ok and "backend did not become ready" in result.output
        assert factory_module.failed_spec_names(result.output) == set()

@case
def missing_browsers_skip_and_a_late_skip_keeps_earlier_failures():
    with tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        spec = spec_file(root, "REQ-2.spec.js")
        world = World(root, {"REQ-2.spec.js": DEPENDENT})
        world.skip_after = 0
        with installed(world) as verify:
            assert verify.run_e2e(root, [spec], log=lambda m: None).skipped

        world = World(root, {"REQ-2.spec.js": [(10, "REQ-2: The note is listed", note_of_the_test_before), (20, "REQ-2: Create a note", create_note)]})
        world.skip_after = 1
        with installed(world) as verify:
            result = verify.run_e2e(root, [spec], log=lambda m: None)
        assert not result.skipped and not result.ok and "1 test(s) of REQ-2.spec.js not run" in result.output
        assert titles(result.output) == {"REQ-2.spec.js": {"REQ-2: The note is listed"}}

@case
def a_red_run_that_names_no_test_counts_every_test_of_it():
    with tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        spec = spec_file(root, "REQ-2.spec.js")
        world = World(root, {"REQ-2.spec.js": [(10, "REQ-2: Create a note", create_note), (20, "REQ-2: Crashes", passes)]})
        original = world.run_cmd

        def crash(args, cwd, timeout=600, env=None):
            if any(a.endswith(":20") for a in args):
                return 1, "Error: worker process exited unexpectedly (code=3221225477)"
            return original(args, cwd, timeout, env)
        world.run_cmd = crash
        with installed(world) as verify:
            result = verify.run_e2e(root, [spec], log=lambda m: None)
    assert not result.ok and titles(result.output) == {"REQ-2.spec.js": {"REQ-2: Crashes"}}, result.output

@case
def the_predecessor_replay_still_shares_one_data_set():
    with tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        a, b = spec_file(root, "REQ-1.spec.js"), spec_file(root, "REQ-2.spec.js")
        world = World(root, {"REQ-1.spec.js": [(10, "REQ-1: Create", create_note)], "REQ-2.spec.js": [(10, "REQ-2: Listed", note_of_the_test_before)]})
        with installed(world) as verify:
            assert verify.run_e2e(root, [a, b], log=lambda m: None, shared_data=True).ok
        assert world.playwright == [["test-e2e/REQ-1.spec.js", "test-e2e/REQ-2.spec.js"]] and len(world.started) == 1

def _audit43a_cases() -> list:
    if not (Path(__file__).resolve().parent / "cases_audit43a.py").is_file():
        return []
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cases_audit43a
    return [c for c in cases_audit43a.CASES if c not in CASES]

CASES += _audit43a_cases()

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=3)
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
