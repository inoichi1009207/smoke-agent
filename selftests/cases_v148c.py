from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if "--r55-probe-root" in sys.argv:
    ROOT = Path(sys.argv[sys.argv.index("--r55-probe-root") + 1]).resolve()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent.clock
import agent.verify
import cases_isolation as iso

NL = chr(10)
CASES = []
STEP = 100.0

def case(function):
    CASES.append(function)
    return function

class Clock:

    def __init__(self, world) -> None:
        self.world, self.t = world, 1.0e9
        original = world.run_cmd

        def run_cmd(args, cwd, timeout=600, env=None):
            out = original(args, cwd, timeout, env)
            if "--list" not in args:
                self.t += STEP
            return out
        world.run_cmd = run_cmd

    def __enter__(self):
        from agent import clock
        self.module, self.saved = clock, clock.now
        clock.now = lambda: self.t
        return self

    def __exit__(self, *exc):
        self.module.now = self.saved
        return False

def one(title):
    return [(10, title, iso.passes)]

def three_files(root):
    names = ["REQ-1.spec.js", "REQ-2.spec.js", "REQ-3.spec.js"]
    specs = [iso.spec_file(root, n) for n in names]
    return names, specs

def quiet(message):
    pass

@case
def the_file_loop_starts_no_file_past_until_and_lists_the_rest_as_not_run():
    import main as factory_module
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        names, specs = three_files(root)
        world = iso.World(root, {n: one(f"{n}: only") for n in names})
        with Clock(world) as fake, iso.installed(world) as verify:
            until = fake.t + 1.5 * STEP
            result = verify.run_e2e(root, specs, log=quiet, until=until)
        assert world.playwright == [["test-e2e/REQ-1.spec.js"], ["test-e2e/REQ-2.spec.js"]], world.playwright
        assert not result.ok and not result.skipped and result.infra == "", (result.ok, result.infra)
        assert result.output.startswith("NOT RUN SPEC FILES: REQ-3.spec.js" + NL), result.output[:200]
        assert "FAILED SPEC FILES" not in result.output
        assert factory_module.failed_spec_names(result.output) == set()
        assert factory_module.not_run_spec_names(result.output) == {"REQ-3.spec.js"}
        assert "1 spec file(s) not run: the phase's end has passed" in result.output
        assert "2 of 3 spec files passed" in result.output and result.ran == 2

@case
def until_already_passed_starts_nothing_on_any_path():
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        names, specs = three_files(root)
        world = iso.World(root, {n: iso.PASSWORD for n in names})
        with Clock(world) as fake, iso.installed(world) as verify:
            past = fake.t
            several = verify.run_e2e(root, specs, log=quiet, until=past)
            single = verify.run_e2e(root, specs[:1], log=quiet, until=past)
            shared = verify.run_e2e(root, specs, log=quiet, shared_data=True, until=past)
            groups = verify.test_groups(root, specs[0], log=quiet)
            alone = verify.run_tests_isolated(root, specs[0], groups, log=quiet, until=past)

            verify.PER_FILE_ISOLATION_SEC = 0.0
            fallback = verify.run_tests_isolated(root, specs[0], groups, log=quiet, until=past)
        assert world.playwright == [] and world.started == [], (world.playwright, world.started)
        for result, listed in ((several, "REQ-1.spec.js, REQ-2.spec.js, REQ-3.spec.js"), (single, "REQ-1.spec.js"),
                               (shared, "REQ-1.spec.js, REQ-2.spec.js, REQ-3.spec.js"), (alone, "REQ-1.spec.js"),
                               (fallback, "REQ-1.spec.js")):
            assert not result.ok and not result.skipped and result.infra == "", result.output
            assert result.output.startswith("NOT RUN SPEC FILES: " + listed + NL), result.output[:200]
            assert "FAILED SPEC FILES" not in result.output

@case
def main_v147_reads_such_a_run_as_unfinished_never_as_a_regression():
    import main as factory_module
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        names, specs = three_files(root)
        world = iso.World(root, {n: one(f"{n}: only") for n in names})
        with Clock(world) as fake, iso.installed(world) as verify:
            until = fake.t + 0.5 * STEP
            run = verify.run_e2e(root, specs, log=quiet, until=until)
            factory = factory_module.Factory.__new__(factory_module.Factory)
            factory.project_dir = root
            saved_log, saved_run = factory_module.log, factory_module.run_e2e
            started = []
            factory_module.log = quiet
            factory_module.run_e2e = lambda *a, **k: started.append(a) or verify.StepResult(True, "")
            try:
                phase = {"end": until, "name": "audited repair"}
                again, inconclusive = factory.settle_unnamed_regression("REQ-9", specs, run, phase_end=phase)
                unfinished = factory.phase_unfinished("REQ-9", phase, len(specs))
            finally:
                factory_module.log, factory_module.run_e2e = saved_log, saved_run
        assert world.playwright == [["test-e2e/REQ-1.spec.js"]], world.playwright
        assert inconclusive is None and again is run and started == []
        assert unfinished is not None and unfinished.output.startswith(factory_module.Factory.UNFINISHED_MARK)

        booked = []

        class Trace:
            def set_test_status(self, test_id, passed):
                booked.append((test_id, passed))
        factory.trace = Trace()
        factory.test_ids_by_file = {spec: [f"T.{Path(spec).stem}"] for spec in specs}
        factory.test_titles = {f"T.{Path(spec).stem}": f"{Path(spec).name}: only" for spec in specs}
        factory.record_run(specs, run.ok, run.output)
        assert booked == [("T.REQ-1.spec", None)], booked

def green_four():
    return [(10, "REQ-5: one", iso.passes), (20, "REQ-5: two", iso.passes), (30, "REQ-5: three", iso.passes),
            (40, "REQ-5: four", iso.passes)]

@case
def an_isolated_file_stopped_before_a_failure_is_not_run_never_failed():
    import main as factory_module
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        spec = iso.spec_file(root, "REQ-5.spec.js")
        for entry in ("direct", "run_e2e"):
            world = iso.World(root, {"REQ-5.spec.js": green_four()})
            with Clock(world) as fake, iso.installed(world) as verify:
                until = fake.t + 1.5 * STEP
                if entry == "direct":
                    groups = verify.test_groups(root, spec, log=quiet)
                    result = verify.run_tests_isolated(root, spec, groups, log=quiet, until=until)
                else:
                    result = verify.run_e2e(root, [spec], log=quiet, until=until)
            assert world.playwright == [["test-e2e/REQ-5.spec.js:10"], ["test-e2e/REQ-5.spec.js:20"]], (entry, world.playwright)
            assert not result.ok and not result.skipped and result.infra == "", (entry, result.output)
            assert result.output.startswith("NOT RUN SPEC FILES: REQ-5.spec.js" + NL), (entry, result.output[:200])
            assert "FAILED SPEC FILES" not in result.output and result.failed_tests == [], entry
            assert factory_module.failed_spec_names(result.output) == set(), entry
            assert "2 test(s) of REQ-5.spec.js not run: the phase's end has passed" in result.output, entry
            assert result.ran == 2, (entry, result.ran)

@case
def a_failure_before_the_stop_keeps_the_file_red_and_says_the_rest_were_not_run():
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        spec = iso.spec_file(root, "REQ-5.spec.js")
        tests = green_four()
        tests[0] = (10, "REQ-5: one", iso.always_red)
        world = iso.World(root, {"REQ-5.spec.js": tests})
        with Clock(world) as fake, iso.installed(world) as verify:
            result = verify.run_e2e(root, [spec], log=quiet, until=fake.t + 1.5 * STEP)
        assert world.playwright == [["test-e2e/REQ-5.spec.js:10"], ["test-e2e/REQ-5.spec.js:20"]], world.playwright
        assert result.output.startswith("FAILED SPEC FILES: REQ-5.spec.js" + NL), result.output[:200]
        assert "NOT RUN SPEC FILES" not in result.output
        assert iso.titles(result.output) == {"REQ-5.spec.js": {"REQ-5: one"}}, iso.titles(result.output)
        assert "2 test(s) of REQ-5.spec.js not run: the phase's end has passed" in result.output

@case
def no_rest_run_starts_past_until():
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        spec = iso.spec_file(root, "REQ-5.spec.js")
        runs = {}
        for label, offset in (("v147", None), ("until", 0.5 * STEP)):
            world = iso.World(root, {"REQ-5.spec.js": green_four()})
            with Clock(world) as fake, iso.installed(world) as verify:
                fits = iter([True, True])
                saved = verify._isolation_fits
                verify._isolation_fits = lambda started, done, left: next(fits, False)
                try:
                    groups = verify.test_groups(root, spec, log=quiet)
                    until = None if offset is None else fake.t + offset
                    result = verify.run_tests_isolated(root, spec, groups, log=quiet, until=until)
                finally:
                    verify._isolation_fits = saved
            runs[label] = (world.playwright, result)

        assert runs["v147"][0] == [["test-e2e/REQ-5.spec.js:10"], ["test-e2e/REQ-5.spec.js:20", "test-e2e/REQ-5.spec.js:30",
                                                                   "test-e2e/REQ-5.spec.js:40"]], runs["v147"][0]
        assert runs["v147"][1].ok
        playwright, result = runs["until"]
        assert playwright == [["test-e2e/REQ-5.spec.js:10"]], playwright
        assert result.output.startswith("NOT RUN SPEC FILES: REQ-5.spec.js" + NL) and not result.ok

@case
def a_file_stopped_half_way_in_the_loop_joins_the_not_run_list():
    import main as factory_module
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        first = iso.spec_file(root, "REQ-1.spec.js")
        middle = iso.spec_file(root, "REQ-5.spec.js")
        last = iso.spec_file(root, "REQ-6.spec.js")
        world = iso.World(root, {"REQ-1.spec.js": one("REQ-1: only"), "REQ-5.spec.js": green_four(), "REQ-6.spec.js": one("REQ-6: only")})
        with Clock(world) as fake, iso.installed(world) as verify:
            result = verify.run_e2e(root, [first, middle, last], log=quiet, until=fake.t + 2.5 * STEP)
        assert world.playwright == [["test-e2e/REQ-1.spec.js"], ["test-e2e/REQ-5.spec.js:10"], ["test-e2e/REQ-5.spec.js:20"]], world.playwright
        assert result.output.startswith("NOT RUN SPEC FILES: REQ-5.spec.js, REQ-6.spec.js" + NL), result.output[:200]
        assert factory_module.failed_spec_names(result.output) == set() and not result.ok and result.infra == ""
        assert "1 of 3 spec files passed" in result.output

@case
def without_until_or_with_a_far_end_the_runs_and_outputs_are_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        first = iso.spec_file(root, "REQ-1.spec.js")
        middle = iso.spec_file(root, "REQ-5.spec.js")
        last = iso.spec_file(root, "REQ-2.spec.js")
        tests = green_four()
        tests[2] = (30, "REQ-5: three", iso.always_red)
        outcomes = []
        for until in ("absent", None, 10.0 ** 12):
            world = iso.World(root, {"REQ-1.spec.js": one("REQ-1: only"), "REQ-5.spec.js": tests, "REQ-2.spec.js": iso.DEPENDENT})
            with Clock(world), iso.installed(world) as verify:
                kw = {} if until == "absent" else {"until": until}
                several = verify.run_e2e(root, [first, middle, last], log=quiet, **kw)
                single = verify.run_e2e(root, [middle], log=quiet, **kw)
            outcomes.append((world.playwright, [(r.ok, r.output, r.ran, r.infra, r.failed_tests) for r in (several, single)]))
        assert outcomes[0] == outcomes[1] == outcomes[2], outcomes
        assert len(outcomes[0][0]) == 1 + 4 + 2 + 4

def red_first(n: int = 2):
    tests = [(10 * (i + 1), f"REQ-5: t{i + 1}", iso.passes) for i in range(n)]
    tests[0] = (10, "REQ-5: t1", iso.always_red)
    return tests

class Ledger:

    def __init__(self, root: Path, spec: str, n: int) -> None:
        import main as m
        self.booked = []
        booked = self.booked

        class Trace:
            def set_test_status(self, test_id, passed):
                booked.append((test_id, passed))
        self.factory = m.Factory.__new__(m.Factory)
        self.factory.project_dir, self.factory.trace = root, Trace()
        self.factory.test_ids_by_file = {spec: [f"T{i + 1}" for i in range(n)]}
        self.factory.test_titles = {f"T{i + 1}": f"REQ-5: t{i + 1}" for i in range(n)}

    def record(self, files, result) -> list:
        import main as m
        self.factory.record_run(files, result.ok, result.output, **m.unrun_kw(result, self.factory.record_run))
        return self.booked

@case
def r55_2_a_failure_then_the_end_names_the_tests_not_run_and_books_none_of_them_green():
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        spec = iso.spec_file(root, "REQ-5.spec.js")
        for entry in ("direct", "run_e2e", "several"):
            other = iso.spec_file(root, "REQ-1.spec.js")
            world = iso.World(root, {"REQ-5.spec.js": red_first(3), "REQ-1.spec.js": one("REQ-1: only")})
            with Clock(world) as fake, iso.installed(world) as verify:
                until = fake.t + (1.5 if entry == "several" else 0.5) * STEP
                if entry == "direct":
                    result = verify.run_tests_isolated(root, spec, verify.test_groups(root, spec, log=quiet), log=quiet, until=until)
                elif entry == "run_e2e":
                    result = verify.run_e2e(root, [spec], log=quiet, until=until)
                else:
                    result = verify.run_e2e(root, [other, spec], log=quiet, until=until)
            assert world.playwright[-1] == ["test-e2e/REQ-5.spec.js:10"], (entry, world.playwright)
            assert "FAILED SPEC FILES: REQ-5.spec.js" in result.output and not result.ok, (entry, result.output[:200])
            assert "2 test(s) of REQ-5.spec.js not run: the phase's end has passed" in result.output, entry
            assert result.not_run_tests == [("REQ-5.spec.js", 20, 1, "REQ-5: t2"), ("REQ-5.spec.js", 30, 1, "REQ-5: t3")], \
                (entry, result.not_run_tests)

            booked = Ledger(root, spec, 3).record([spec], result)
            assert booked == [("T1", False)], (entry, booked)

@case
def r55_2_a_late_skip_after_a_failure_names_its_tests_not_run_too():
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        spec = iso.spec_file(root, "REQ-5.spec.js")
        world = iso.World(root, {"REQ-5.spec.js": red_first(3)})
        world.skip_after = 1
        with iso.installed(world) as verify:
            result = verify.run_e2e(root, [spec], log=quiet)
        assert not result.ok and not result.skipped and "2 test(s) of REQ-5.spec.js not run" in result.output
        assert result.not_run_tests == [("REQ-5.spec.js", 20, 1, "REQ-5: t2"), ("REQ-5.spec.js", 30, 1, "REQ-5: t3")], \
            result.not_run_tests
        assert Ledger(root, spec, 3).record([spec], result) == [("T1", False)]

@case
def r55_2_a_completed_run_names_no_test_not_run_and_books_as_before():
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        spec = iso.spec_file(root, "REQ-5.spec.js")
        for until in (None, 10.0 ** 12):
            world = iso.World(root, {"REQ-5.spec.js": red_first(3)})
            with Clock(world), iso.installed(world) as verify:
                result = verify.run_e2e(root, [spec], log=quiet, **({} if until is None else {"until": until}))
            assert result.not_run_tests is None and len(world.playwright) == 3, (result.not_run_tests, world.playwright)
            assert Ledger(root, spec, 3).record([spec], result) == [("T1", False), ("T2", True), ("T3", True)]

class Slow:

    def __init__(self, fake: Clock, verify, listing: float = 0.0, install: float = 0.0, install_from: int = 1) -> None:
        self.fake, self.verify, self.installs = fake, verify, 0
        saved_cmd, self.saved_install = verify.run_cmd, verify.ensure_installed

        def run_cmd(args, cwd, timeout=600, env=None):
            if "--list" in args:
                fake.t += listing
            return saved_cmd(args, cwd, timeout, env)

        def ensure_installed(*args, **kwargs):
            self.installs += 1
            if self.installs >= install_from:
                fake.t += install
            return self.saved_install(*args, **kwargs)
        verify.run_cmd, verify.ensure_installed = run_cmd, ensure_installed

    def restore(self) -> None:
        self.verify.ensure_installed = self.saved_install

@case
def r55_3_a_listing_that_ends_past_until_starts_no_server_and_no_test():
    import main as factory_module
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        spec = iso.spec_file(root, "REQ-5.spec.js")
        for until_in in (60.0, None):
            world = iso.World(root, {"REQ-5.spec.js": one("REQ-5: only")})
            with Clock(world) as fake, iso.installed(world) as verify:
                slow = Slow(fake, verify, listing=120.0)
                try:
                    kw = {} if until_in is None else {"until": fake.t + until_in}
                    result = verify.run_e2e(root, [spec], log=quiet, **kw)
                finally:
                    slow.restore()
            assert world.listings == 1, world.listings
            if until_in is None:
                assert world.playwright == [["test-e2e/REQ-5.spec.js"]] and result.ok
                continue
            assert world.playwright == [] and world.started == [], (world.playwright, world.started)
            assert not result.ok and not result.skipped and result.infra == "", result.output
            assert result.output.startswith("NOT RUN SPEC FILES: REQ-5.spec.js" + NL), result.output[:200]
            assert factory_module.stopped_unrun(result) and factory_module.failed_spec_names(result.output) == set()

@case
def r55_3_an_install_that_ends_past_until_starts_no_run_on_any_path():
    import main as factory_module
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        spec = iso.spec_file(root, "REQ-5.spec.js")
        other = iso.spec_file(root, "REQ-1.spec.js")
        specs = {"REQ-5.spec.js": green_four(), "REQ-1.spec.js": one("REQ-1: only")}

        paths = (("shared data", lambda v, u: v.run_e2e(root, [other, spec], log=quiet, shared_data=True, until=u), 1),
                 ("the first isolated test", lambda v, u: v.run_e2e(root, [spec], log=quiet, until=u), 1),
                 ("the whole-file fallback", "fallback", 1),
                 ("only= (a partial run's groups together)",
                  lambda v, u: v._run_e2e_once(root, [spec], log=quiet, only=["test-e2e/REQ-5.spec.js:10"], until=u), 1))
        for label, call, start in paths:
            world = iso.World(root, specs)
            with Clock(world) as fake, iso.installed(world) as verify:
                slow = Slow(fake, verify, install=10.0, install_from=start)
                saved = verify.PER_FILE_ISOLATION_SEC
                try:
                    if call == "fallback":
                        verify.PER_FILE_ISOLATION_SEC = 0.0
                        groups = verify.test_groups(root, spec, log=quiet)
                        result = verify.run_tests_isolated(root, spec, groups, log=quiet, until=fake.t + 5.0)
                    else:
                        result = call(verify, fake.t + 5.0)
                finally:
                    verify.PER_FILE_ISOLATION_SEC = saved
                    slow.restore()
            assert world.playwright == [] and world.started == [], (label, world.playwright)
            assert not result.ok and not result.skipped and result.output.startswith("NOT RUN SPEC FILES: "), (label, result.output[:200])
            assert factory_module.stopped_unrun(result), label

@case
def r55_3_an_install_past_until_inside_the_loop_stops_the_file_after_its_failure():
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        spec = iso.spec_file(root, "REQ-5.spec.js")
        world = iso.World(root, {"REQ-5.spec.js": red_first(3)})
        with Clock(world) as fake, iso.installed(world) as verify:

            slow = Slow(fake, verify, install=10.0, install_from=3)
            try:
                result = verify.run_e2e(root, [spec], log=quiet, until=fake.t + STEP + 5.0)
            finally:
                slow.restore()
        assert world.playwright == [["test-e2e/REQ-5.spec.js:10"]], world.playwright
        assert result.output.startswith("FAILED SPEC FILES: REQ-5.spec.js" + NL) and "NOT RUN SPEC FILES" not in result.output
        assert iso.titles(result.output) == {"REQ-5.spec.js": {"REQ-5: t1"}}, iso.titles(result.output)
        assert "2 test(s) of REQ-5.spec.js not run: the phase's end has passed" in result.output
        assert [t for _, _, _, t in result.not_run_tests] == ["REQ-5: t2", "REQ-5: t3"], result.not_run_tests

class Snapshots:

    def __init__(self, world, verify, words: list[str]) -> None:
        import json
        self.words = list(words)
        original = verify.run_cmd

        def run_cmd(args, cwd, timeout=600, env=None):
            code, out = original(args, cwd, timeout, env)
            if "--list" in args or code == 0:
                return code, out
            report = Path(env["PLAYWRIGHT_JSON_OUTPUT_FILE"])
            data = json.loads(report.read_text(encoding="utf-8"))
            word = self.words.pop(0)
            for suite in data["suites"]:
                for spec in suite["specs"]:
                    for test in spec["tests"]:
                        for result in test["results"]:
                            if result["status"] != "failed":
                                continue
                            folder = "REQ-5-" + spec["title"].replace(":", "").replace(" ", "-")
                            target = Path(cwd) / "test-results" / folder / "error-context.md"
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_text("# Page snapshot" + NL + NL + "```yaml" + NL + f"- heading \"{word} marker\" [ref=e1]"
                                              + NL + "```", encoding="utf-8")
                            result["errors"] = [{"message": "Error: expect(locator).toBeVisible() failed"}]
                            result["attachments"] = [{"name": "error-context", "contentType": "text/markdown", "path": str(target)}]
                            out += NL + f"    Error Context: test-results/{folder}/error-context.md" + NL
            report.write_text(json.dumps(data), encoding="utf-8")
            return code, out
        verify.run_cmd = run_cmd

@case
def b2_two_runs_with_the_same_first_error_keep_their_own_snapshots():
    import re

    from agent import testrun
    with tempfile.TemporaryDirectory() as tmp:
        root = iso.project(tmp)
        spec = iso.spec_file(root, "REQ-5.spec.js")
        several = [spec, iso.spec_file(root, "REQ-6.spec.js")]
        world = iso.World(root, {"REQ-5.spec.js": [(10, "REQ-5: t1", iso.always_red)], "REQ-6.spec.js": one("REQ-6: only")})
        with iso.installed(world) as verify:
            Snapshots(world, verify, ["Alpha", "Beta", "Gamma"])
            first = verify.run_e2e(root, [spec], log=quiet)
            second = verify.run_e2e(root, [spec], log=quiet)
            merged = verify.run_e2e(root, several, log=quiet)
            passed = verify.run_e2e(root, several[1:], log=quiet)

    def marks(result):
        outcome = testrun.TestOutcome(False, "E2E FAILED against the real application" + NL + result.output,
                                      error_contexts=result.error_contexts)
        return re.findall(r"(\w+) marker", outcome.summary)
    assert [list(r.error_contexts or {}) for r in (first, second, merged)] == [["REQ-5-REQ-5-t1"]] * 3, first.error_contexts

    assert marks(first) == ["Alpha"] and marks(second) == ["Beta"] and marks(merged) == ["Gamma"], \
        (marks(first), marks(second), marks(merged))
    assert passed.ok and passed.error_contexts is None

    parts = [verify.StepResult(False, "", error_contexts={"k": ("e", "A"), "j": ("e", "J")}), verify.StepResult(True, ""),
             verify.StepResult(False, "", error_contexts={"k": ("e", "B")})]
    assert verify._merged_contexts(parts) == {"k": ("e", "B"), "j": ("e", "J")}, verify._merged_contexts(parts)
    assert verify._merged_contexts(parts[1:2]) is None and verify._merged_contexts([verify.StepResult(False, "", error_contexts={})]) == {}

    import inspect

    import main as factory_module
    assert inspect.getsource(factory_module).count('error_contexts=getattr(') == 9

V147 = ROOT.parent / "smoke-agent-v147-shipped"
PROBE = "--r55-probe-root"

def probe_scenarios() -> list:
    from agent import verify
    out = []
    scenarios = ("several", "isolated red", "late skip", "not isolated", "slow listing", "shared data", "rest-run")
    for label in scenarios:
        with tempfile.TemporaryDirectory() as tmp:
            root = iso.project(tmp)
            first, middle, last = (iso.spec_file(root, n) for n in ("REQ-1.spec.js", "REQ-5.spec.js", "REQ-2.spec.js"))
            tests = red_first(4)
            world = iso.World(root, {"REQ-1.spec.js": one("REQ-1: only"), "REQ-5.spec.js": tests, "REQ-2.spec.js": iso.DEPENDENT})
            flags = {"PER_TEST_ISOLATION": False} if label == "not isolated" else {}
            if label == "late skip":
                world.skip_after = 1
            with Clock(world) as fake, iso.installed(world, **flags) as v:
                if label == "slow listing":
                    original = v.run_cmd

                    def run_cmd(args, cwd, timeout=600, env=None, original=original, fake=fake):
                        if "--list" in args:
                            fake.t += 120.0
                        return original(args, cwd, timeout, env)
                    v.run_cmd = run_cmd
                saved = v._isolation_fits
                if label == "rest-run":
                    fits = iter([True, True])
                    v._isolation_fits = lambda started, done, left: next(fits, False)
                try:
                    files = {"several": [first, middle, last], "shared data": [first, middle]}.get(label, [middle])
                    result = v.run_e2e(root, files, log=quiet, shared_data=label == "shared data")
                finally:
                    v._isolation_fits = saved
            text = result.output.replace(str(root), "<root>")
            out.append([label, world.playwright, len(world.started), result.ok, result.skipped, text, result.ran,
                        result.infra, [list(e) for e in result.failed_tests] if result.failed_tests is not None else None])
    assert verify.__file__.startswith(str(ROOT)), (verify.__file__, ROOT)
    return out

@case
def r55_without_until_the_runs_and_results_are_v147s_byte_for_byte():
    import json
    import subprocess
    assert (V147 / "agent" / "verify.py").is_file(), f"the v147 reference tree {V147} is missing: no differential"
    records = {}
    for tree in (V147, ROOT):
        done = subprocess.run([sys.executable, str(Path(__file__).resolve()), PROBE, str(tree)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=600)
        assert done.returncode == 0, (tree, done.stderr[-2000:])
        records[tree] = json.loads(done.stdout.strip().splitlines()[-1])
    mine, theirs = records[ROOT], records[V147]
    assert len(mine) == len(theirs) == 7
    for a, b in zip(mine, theirs):
        assert a == b, (a[0], a, b)
    assert any("not run" in r[5] for r in mine if r[0] == "late skip"), "the late-skip scenario lost its subject"

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed")
    return 1 if failed else 0

if __name__ == "__main__":
    if PROBE in sys.argv:
        import json
        print(json.dumps(probe_scenarios()))
        raise SystemExit(0)
    raise SystemExit(main())
