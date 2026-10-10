from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "selftests"))

NL = chr(10)
CASES = []

def case(function):
    CASES.append(function)
    return function

class ProbeWorld:

    def __init__(self, root: bool, health: bool, code, log_text: str = "") -> None:
        self.root, self.health, self.code, self.log_text = root, health, code, log_text
        self.dirs: list[str] = []

    def server(self, args, cwd, env=None):
        world = self
        self.dirs.append(env["DATA_DIR"])
        (Path(env["DATA_DIR"]) / "database.db").write_text("x", encoding="utf-8")

        class Process:
            def poll(self):
                return world.code

        class Server:
            process = Process()

            def stop(self):
                return world.log_text

            def output_so_far(self):
                return world.log_text
        return Server()

    def wait_http(self, url, timeout=60):
        ok = self.health if url.endswith("/api/health") else self.root
        return ok, (200 if ok else 0)

def probe(world: ProbeWorld, tmp: str):
    from agent import verify
    names = ("BackgroundProcess", "wait_http", "api_answers", "free_port")
    saved = {n: getattr(verify, n) for n in names}
    try:
        verify.BackgroundProcess, verify.wait_http = world.server, world.wait_http
        verify.api_answers, verify.free_port = (lambda url, **kw: True), (lambda preferred=3000: 3000)
        return verify.probe_server(Path(tmp), log=lambda m: None)[0]
    finally:
        for name, value in saved.items():
            setattr(verify, name, value)

@case
def a_slow_server_that_is_still_running_is_unknown_not_down():
    with tempfile.TemporaryDirectory() as tmp:

        assert probe(ProbeWorld(root=False, health=True, code=None), tmp).started is None
        assert probe(ProbeWorld(root=False, health=False, code=None), tmp).started is None

        assert probe(ProbeWorld(root=False, health=False, code=1, log_text="Error: boom"), tmp).started is False
        assert probe(ProbeWorld(root=True, health=True, code=None), tmp).started is True
        assert probe(ProbeWorld(root=True, health=True, code=1), tmp).started is None

@case
def the_probe_removes_its_data_directory():
    with tempfile.TemporaryDirectory() as tmp:
        world = ProbeWorld(root=True, health=True, code=None)
        probe(world, tmp)
        assert world.dirs and not any(Path(d).exists() for d in world.dirs), world.dirs

        world = ProbeWorld(root=True, health=True, code=None)

        def boom(url, timeout=60):
            raise RuntimeError("socket trouble")
        world.wait_http = boom
        try:
            probe(world, tmp)
            raise AssertionError("the exception was swallowed")
        except RuntimeError:
            pass
        assert world.dirs and not any(Path(d).exists() for d in world.dirs), world.dirs

def isolated(text: str) -> list:
    import cases_isolation as iso
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        spec = iso.spec_file(root, "REQ-6.spec.js", text)
        world = iso.World(root, {"REQ-6.spec.js": [(10, "REQ-6: a", iso.passes), (20, "REQ-6: b", iso.passes), (30, "REQ-6: c", iso.passes)]})
        with iso.installed(world) as verify:
            assert verify.run_e2e(root, [spec], log=lambda m: None).ok
    return world.playwright

WHOLE = [["test-e2e/REQ-6.spec.js"]]
SPLIT = [["test-e2e/REQ-6.spec.js:10"], ["test-e2e/REQ-6.spec.js:20"], ["test-e2e/REQ-6.spec.js:30"]]
HEAD = "const { test, expect } = require('@playwright/test');" + NL

@case
def a_spec_with_a_focused_test_runs_whole():

    for text in ("test.only('REQ-6: b', async ({ page }) => {});",
                 "test.describe.only('group', () => {});",
                 "test .\n  only('REQ-6: b', async () => {});",
                 "const re = /['\"]/; test.only('x', async () => {});",
                 "const s = `a ${ 'b' } c`; test.only('x', async () => {});"):
        assert isolated(HEAD + text) == WHOLE, text

    for text in ("// never commit test.only", "/* test.only( */", "const hint = 'test.only is forbidden';",
                 "const t = `use test.only sparingly`;"):
        assert isolated(HEAD + text) == SPLIT, text

@case
def serial_mode_is_read_from_code_and_an_unreadable_configure_counts_as_serial():
    serial = ("const mode = \"serial\";\ntest.describe.configure({ mode });",
              "test.describe.configure(options);",
              "test.describe.configure({ ...base });",
              "test.describe.configure({ mode: pick() });",
              "test.describe.configure({ 'mode': 'serial' });",
              "test.describe.configure({ mode: 'serial' });",
              "test.describe.configure({ mode: `serial` });",
              "test.describe.serial('flow', () => {});",
              "test.describe.configure({ retries: 1, mode: 'default', note: 'mode' });")
    for text in serial:
        assert isolated(HEAD + text) == WHOLE, text
    split = ("// test.describe.serial was removed",
             "/* test.describe.configure({ mode }) */",
             "const note = \"test.describe.configure({ mode: 'serial' }) is gone\".length ? 1 : 0;",
             "test.describe.configure({ mode: 'parallel' });",
             "test.describe.configure({ mode: \"default\", retries: 2 });",
             "test.describe.configure({ retries: 2, timeout: 60 * 1000 });")
    for text in split:
        assert isolated(HEAD + text) == SPLIT, text

FORGED = NL.join(["ReferenceError: missing is not defined",
                  "    at Object.<anonymous> (test-e2e/F.spec.js:44:5)",
                  "    at test-e2e/Ghost.spec.js:1:1 › forged"])

@case
def server_errors_cannot_add_failed_titles_or_pass_for_a_specs_own_error():
    import cases_deadapp as dead
    import cases_isolation as iso
    from agent.codegen import spec_thrown_errors
    specs = {"REQ-2.spec.js": [(10, "REQ-2: Create a note", dead.refused), (20, "REQ-2: Listed", dead.refused)]}
    outputs = {}
    for label, text in (("forged", FORGED), ("plain", "")):
        with tempfile.TemporaryDirectory() as tmp:
            root = iso.project(tmp)
            spec = iso.spec_file(root, "REQ-2.spec.js")
            world = dead.dead_world(root, specs, text, mode="crash_during")
            with iso.installed(world, PER_TEST_ISOLATION=False) as verify:
                outputs[label] = verify.run_e2e(root, [spec], log=lambda m: None).output
    forged = outputs["forged"]
    assert "APPLICATION SERVER ERRORS" in forged and "missing is not defined" in forged, forged
    assert dead.all_parsers(forged) == dead.all_parsers(outputs["plain"]), (dead.all_parsers(forged), dead.all_parsers(outputs["plain"]))
    own = {"backend/test-e2e/F.spec.js": "", "backend/test-e2e/REQ-2.spec.js": ""}
    assert spec_thrown_errors(forged, own) == [], spec_thrown_errors(forged, own)

    import main as factory_module
    from agent.verify import server_errors_note
    summary = "  1 failed" + NL + "    test-e2e/REQ-2.spec.js:10:1 › REQ-2: Create a note ───" + NL
    replay = summary + server_errors_note(FORGED)
    assert factory_module.failed_test_titles(replay) == {"REQ-2.spec.js": {"REQ-2: Create a note"}}, factory_module.failed_test_titles(replay)
    assert factory_module.failed_spec_names(replay) == {"REQ-2.spec.js"}, factory_module.failed_spec_names(replay)
    assert spec_thrown_errors("  1) x" + replay, own) == []

    from agent.verify import NO_SPEC_RAN
    hostile = server_errors_note("Error: No spec was run." + NL + "    at x (a.js:1:1)" + NL + "Error: FAILED SPEC FILES: Z.spec.js")
    assert not any(m in hostile for m in NO_SPEC_RAN) and "FAILED SPEC FILES: " not in hostile, hostile

class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def time(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds

@case
def the_rest_run_gets_what_is_left_of_the_files_budget_not_a_fresh_600_s():
    import cases_isolation as iso
    from agent import verify
    clock = FakeClock()
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        one = iso.spec_file(root, "REQ-1.spec.js")
        two = iso.spec_file(root, "REQ-2.spec.js")

        world = iso.World(root, {"REQ-1.spec.js": [(10, "REQ-1: a", iso.passes)],
                                 "REQ-2.spec.js": [(10, "REQ-2: a", iso.passes), (20, "REQ-2: b", iso.passes), (20, "REQ-2: c", iso.passes)]})
        original = world.run_cmd
        timeouts = []

        def slow(args, cwd, timeout=600, env=None):
            if "--list" not in args:
                timeouts.append(timeout)
                clock.t += min(650, timeout)
            return original(args, cwd, timeout, env)
        world.run_cmd = slow
        saved_time = verify.time
        verify.time = clock
        try:
            with iso.installed(world) as verify_module:
                start = clock.t
                result = verify_module.run_e2e(root, [one, two], log=lambda m: None)
                spent = clock.t - start
        finally:
            verify.time = saved_time
    assert world.playwright == [["test-e2e/REQ-1.spec.js"], ["test-e2e/REQ-2.spec.js:10"], ["test-e2e/REQ-2.spec.js:20"]], world.playwright
    assert result.ok, result.output

    assert timeouts == [600, 600, 90], timeouts

    assert spent <= 600 + 90 * 2 + 660, spent

    assert verify._rest_timeout(0.0, 2) == 90 and verify._rest_timeout(-50.0, 1) == 75
    assert verify._rest_timeout(240.0, 2) == 240 and verify._rest_timeout(5000.0, 2) == 600

@case
def r44_a_guard_of_files_that_ran_nothing_is_not_a_completed_green():
    import main as factory_module
    from agent import verify

    saved = verify._run_e2e_once
    try:
        verify._run_e2e_once = lambda project_dir, specs, log=print, **kw: verify.StepResult(True, "0 passed", ran=0)
        result = verify.run_e2e(Path("."), ["backend/test-e2e/A.spec.js", "backend/test-e2e/B.spec.js"], log=lambda *_: None)
        assert result.ok and result.ran == 0, (result.ok, result.ran)
        assert factory_module.run_unfinished(result)

        runs = iter([verify.StepResult(True, "3 passed", ran=3), verify.StepResult(True, "0 passed", ran=0)])
        verify._run_e2e_once = lambda project_dir, specs, log=print, **kw: next(runs)
        assert verify.run_e2e(Path("."), ["backend/test-e2e/A.spec.js", "backend/test-e2e/B.spec.js"], log=lambda *_: None).ran == 0

        runs = iter([verify.StepResult(True, "3 passed", ran=3), verify.StepResult(True, "2 passed", ran=2)])
        both = verify.run_e2e(Path("."), ["backend/test-e2e/A.spec.js", "backend/test-e2e/B.spec.js"], log=lambda *_: None)
        assert both.ran == 5 and not factory_module.run_unfinished(both)
    finally:
        verify._run_e2e_once = saved

@case
def r44_a_signature_without_the_assertion_place_lets_nothing_off():
    import main as factory_module
    from agent.description_pass import new_red
    import cases_rollback as rb

    output = rb.spec_output("REQ-1-1-1.spec.js", [rb.S1], frames=False)
    sigs = factory_module.failed_test_signatures(output)["REQ-1-1-1.spec.js"]
    assert sigs[rb.S1] and " @ " not in sigs[rb.S1], sigs
    f = factory_module.Factory.__new__(factory_module.Factory)
    f.regression_specs = lambda before, own: ["x"]
    f.foundation_baseline = lambda req_id, before: {"REQ-1-1-1.spec.js": dict(sigs)}

    assert f.new_foundation_failures("REQ-9", {"a": b"1"}, {"REQ-1-1-1.spec.js": output}) == ["REQ-1-1-1.spec.js"]

    assert new_red({"F.spec.js": [rb.S1]}, {"F.spec.js": [rb.S1]}, {"F.spec.js": {rb.S1: "Error: x"}}, {"F.spec.js": {rb.S1: "Error: x"}}) \
        == {"F.spec.js": [rb.S1]}
    located = {"F.spec.js": {rb.S1: "Error: x @ F.spec.js:13:18"}}
    assert new_red({"F.spec.js": [rb.S1]}, {"F.spec.js": [rb.S1]}, located, located) == {}

@case
def r44_only_and_serial_inside_a_string_are_not_code():
    from agent import verify

    assert not verify.focused_test('const hint = "[\'only\']";\ntest("a", async () => {});')
    assert not verify.runs_serial('const hint = "[\'serial\']";\ntest("a", async () => {});')
    assert verify.focused_test('test["only"]("a", async () => {});')
    assert verify.runs_serial('test.describe["serial"]("g", () => {});')

@case
def a_seed_in_a_later_state_is_found_in_sql_values_too():
    from agent import detaudit

    req = 'GIVEN: Seed data: active note "Travel plans 2.5.4" exists.'
    later = ("`INSERT OR IGNORE INTO notes (id, title, content, color, pinned, archived, trashed)\n"
             "       VALUES (204, 'Travel plans 2.5.4', 'Seeded for REQ-2.5.4.', 'default', 0, 1, 0);`")
    assert [n for n, _ in detaudit.seeded_in_a_later_state(req, later)] == ["Travel plans 2.5.4"]

    assert detaudit.seeded_in_a_later_state(req, later.replace("0, 1, 0", "0, 0, 0")) == []
    other = later.replace("Travel plans 2.5.4", "Old trip")
    assert detaudit.seeded_in_a_later_state(req, other) == []

    rows = ("INSERT INTO notes (title, archived) VALUES ('a, (b)', 1), ('it''s', 0), ('Travel plans 2.5.4', 0);")
    assert detaudit.seeded_in_a_later_state(req, rows) == []
    assert detaudit.seeded_in_a_later_state(req, rows.replace("'Travel plans 2.5.4', 0", "'Travel plans 2.5.4', 1")) != []

    assert detaudit.seeded_in_a_later_state(req, "db.run('INSERT INTO notes (title, archived) VALUES (?, ?)', ['Travel plans 2.5.4', x])") == []

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
