from __future__ import annotations

import json
import re
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_closing as cl
import cases_rollback as rb

NL = chr(10)
CASES = []

APP = rb.APP
A = "backend/test-e2e/REQ-3-1-1.spec.js"
B = "backend/test-e2e/REQ-3-1-2.spec.js"
F = "backend/test-e2e/REQ-1-1-1.spec.js"
EDIT = "REQ-3-1-1: Edit A1 through the inline editor"
SEED = "REQ-3-1-1: seeded B2 shows 4"
SORT = "REQ-3-1-2: sort by column"
HOME = "REQ-1-1-1: home page"

NEEDS = {EDIT: "EDITOR", SEED: "SEED-B2", SORT: "SORT", HOME: "HOME", "REQ-3-1-1: loop one": "LOOP1",
         "REQ-3-1-1: loop two": "LOOP2"}
HEADER = "const { test } = require('@playwright/test');" + NL

def case(function):
    CASES.append(function)
    return function

def spec_text(*titles: str, loop: tuple[str, str] | None = None) -> str:
    lines = [HEADER.rstrip()]
    for title in titles:
        lines += ["", f"test('{title}', async ({{ page }}) => {{}});"]
    if loop:
        lines += ["", f"for (const t of ['{loop[0]}', '{loop[1]}']) test(t, async () => {{}}); // test('{loop[0]}') test('{loop[1]}')"]
    return NL.join(lines) + NL

A_TEXT = spec_text(EDIT, SEED)
B_TEXT = spec_text(SORT)
F_TEXT = spec_text(HOME)

class World:
    def __init__(self, root: Path):
        self.root = root
        self.runs: list[list[str]] = []
        self.partial_runs: list[tuple[str, list[int]]] = []
        self.listings = 0

    def sources(self) -> str:
        base = self.root / "frontend" / "src"
        return NL.join(p.read_text(encoding="utf-8") for p in sorted(base.rglob("*")) if p.is_file())

    def test_groups(self, project_dir, spec, log=print, serial=True):
        self.listings += 1
        text = (self.root / spec).read_text(encoding="utf-8")
        if serial and "serial" in text:
            return None
        groups = []
        for number, line in enumerate(text.splitlines(), 1):
            titles = re.findall(r"test\('([^']+)'", line)
            if titles:
                groups.append((number, [(number, 1, t) for t in dict.fromkeys(titles)]))
        return groups

    def result(self, spec: str, groups) -> "object":
        from agent.verify import StepResult

        text = self.sources()
        name = Path(spec).name
        tests = [t for _, group in groups for t in group]
        failed = [(name, line, col, title) for line, col, title in tests if NEEDS.get(title, "?") not in text]
        if not failed:
            return StepResult(True, f"{name}: every test ran alone, on a fresh server with fresh data of its own{NL}  {len(tests)} passed",
                              ran=len(tests), failed_tests=[])
        blocks = []
        for index, (_, line, col, title) in enumerate(failed, 1):
            blocks += [f"## {name} line {line}: alone on a fresh server with fresh data",
                       f"  {index}) test-e2e/{name}:{line}:{col} › {title} ───",
                       f"    Error: The requirement says {title} works, but nothing visible matches.",
                       f"        at D:\\w\\backend\\test-e2e\\{name}:{line + 1}:5"]
        summary = [f"  {len(failed)} failed"] + [f"    test-e2e/{name}:{l}:{c} › {t} ───" for _, l, c, t in failed]
        if len(tests) > len(failed):
            summary.append(f"  {len(tests) - len(failed)} passed")
        output = NL.join([f"FAILED SPEC FILES: {name}", f"{name}: every test ran alone"] + blocks + [""] + summary)
        return StepResult(False, output, ran=len(tests), failed_tests=failed)

    def run_e2e(self, project_dir, specs, log=print):
        from agent.verify import StepResult

        self.runs.append(list(specs))
        results = [(spec, self.result(spec, self.test_groups(project_dir, spec))) for spec in specs]
        if len(results) == 1:
            return results[0][1]
        red = [(spec, r) for spec, r in results if not r.ok]
        ran = sum(r.ran for _, r in results)
        if not red:
            return StepResult(True, f"{len(specs)} of {len(specs)} spec files passed, each run against fresh data", ran=ran)
        head = "FAILED SPEC FILES: " + ", ".join(Path(s).name for s, _ in red)
        parts = [f"## {Path(s).name}{NL}{r.output.split(NL, 1)[1]}" for s, r in red]
        return StepResult(False, NL.join([head, f"{len(specs) - len(red)} of {len(specs)} spec files passed"] + parts), ran=ran)

    def run_tests_isolated(self, project_dir, spec, groups, log=print):
        self.partial_runs.append((spec, [line for line, _ in groups]))
        return self.result(spec, groups)

    def build_frontend(self, project_dir, log=print):
        from agent.verify import StepResult

        return StepResult(True, "")

def make_factory(root: Path, app: str, green: list[str] | None = None):
    f = rb.make_factory(root, app)
    f.green_specs, f.e2e_specs, f.file_specs = list(green or []), list(green or []), {}
    f.partial_green = {}
    for spec, text in ((F, F_TEXT),):
        (root / spec).parent.mkdir(parents=True, exist_ok=True)
        (root / spec).write_text(text, encoding="utf-8")
    return f

class Patched:

    def __init__(self, world: World, script: list[dict], lines: list[str], settings: dict | None = None):
        self.world, self.script, self.lines, self.settings = world, script, lines, settings or {}

    def __enter__(self):
        import main as factory_module
        from agent import verify
        from agent.settings import SETTINGS
        from agent.testrun import TestOutcome

        self.names = ("run_e2e", "build_frontend", "Implementer", "run_node_tests", "static_test_defects",
                      "parse_defects_in_output", "lint_application", "log")
        self.saved = {name: getattr(factory_module, name) for name in self.names}
        self.saved_verify = {name: getattr(verify, name) for name in ("test_groups", "run_tests_isolated")}
        self.saved_settings = {key: SETTINGS.get(key, cl_missing) for key in ["audit_enabled", *self.settings]}
        factory_module.run_e2e, factory_module.build_frontend = self.world.run_e2e, self.world.build_frontend
        factory_module.Implementer = rb.implementer_class(self.script)
        factory_module.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
        factory_module.static_test_defects = lambda project_dir, files: ""
        factory_module.parse_defects_in_output = lambda output, files: ""
        factory_module.lint_application = lambda project_dir, native: ""
        factory_module.log = self.lines.append
        verify.test_groups, verify.run_tests_isolated = self.world.test_groups, self.world.run_tests_isolated
        SETTINGS["audit_enabled"] = False
        SETTINGS.update(self.settings)
        return self

    def __exit__(self, *exc):
        import main as factory_module
        from agent import verify
        from agent.settings import SETTINGS

        for name, value in self.saved.items():
            setattr(factory_module, name, value)
        for name, value in self.saved_verify.items():
            setattr(verify, name, value)
        for key, value in self.saved_settings.items():
            if value is cl_missing:
                SETTINGS.pop(key, None)
            else:
                SETTINGS[key] = value

cl_missing = object()

def looping_implementer(script: list[dict]):
    from agent.codegen import ImplementResult

    class LoopingImplementer:
        def __init__(self, llm, project_dir, check, **options):
            self.project_dir, self.check = project_dir, check

        def implement(self, node, tests, outcome, extra_context=""):
            written: list[str] = []
            rounds = 0
            while script:
                step = script.pop(0)
                for rel, content in step.get("write", {}).items():
                    (self.project_dir / rel).write_text(content, encoding="utf-8")
                    written.append(rel)
                result = self.check()
                rb.CHECK_OUTPUTS.append(result.output)
                rounds += 1
                if result.passed:
                    return ImplementResult("passed", written, "scripted", rounds, last_check=result.output)
            return ImplementResult("budget", written, "scripted", rounds)

    return LoopingImplementer

def batch(f, world: World, req: str, spec: str, text: str, script: list[dict], settings: dict | None = None, loop: bool = False):
    from agent.test_author import AuthoredFile

    lines: list[str] = []
    f.author_tests = lambda node, prefix, contract: [AuthoredFile(path=spec, content=text)]
    with Patched(world, script, lines, settings):
        if loop:
            import main as factory_module

            factory_module.Implementer = looping_implementer(script)
        passed = f.process_node({"id": req, "name": req, "members": [req]}, 1)
    assert not script, f"scripted steps left over: {script}"
    return passed, lines

def batch_a(f, world, app_after: str = "base EDITOR", settings: dict | None = None):
    return batch(f, world, "REQ-3-1-1", A, A_TEXT, [{"write": {APP: app_after}, "check": True}], settings)

def app_text(root: Path) -> str:
    return (root / APP).read_text(encoding="utf-8")

@case
def a_red_batch_books_the_tests_green_at_its_last_check_and_not_its_red_ones():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f, world = make_factory(root, "base"), World(root)
        passed, lines = batch_a(f, world)
        assert passed is False and f.report["nodes"]["REQ-3-1-1"]["status"] == "budget"
        assert list(f.partial_green) == [A], f.partial_green
        entry = f.partial_green[A]
        assert entry["titles"] == [EDIT] and entry["batch"] == "REQ-3-1-1" and entry["written"] == [APP], entry
        assert SEED not in entry["titles"]
        assert any("1 test(s) of REQ-3-1-1.spec.js were green at its last check" in line for line in lines), lines

        assert A not in f.green_specs

@case
def a_later_batch_runs_only_the_partial_green_tests_and_is_told_when_it_breaks_them():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f, world = make_factory(root, "base"), World(root)
        batch_a(f, world)
        world.partial_runs.clear()
        rb.CHECK_OUTPUTS.clear()

        script = [{"write": {APP: "base SORT"}, "check": True}, {"write": {APP: "base SORT EDITOR"}, "check": True}]
        passed, lines = batch(f, world, "REQ-3-1-2", B, B_TEXT, script, loop=True)
        first, second = rb.CHECK_OUTPUTS[0], rb.CHECK_OUTPUTS[1]

        assert "This batch's own spec PASSES" in first and "REQ-3-1-1.spec.js" in first, first
        assert "only its tests that were green when its batch ended" in first and EDIT in first, first
        assert "FAILED SPEC FILES: REQ-3-1-1.spec.js" in first.splitlines(), first[:400]

        assert world.partial_runs == [(A, [3]), (A, [3])], world.partial_runs
        assert SEED not in first
        assert "E2E PASSED" in second and passed is True
        assert any("running the tests of red batches that were green at their end again: REQ-3-1-1.spec.js (1)" in line for line in lines), lines
        assert B in f.green_specs and A in f.partial_green

@case
def a_later_batch_that_leaves_a_partial_green_test_broken_is_a_regression_kept_with_debt():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f, world = make_factory(root, "base"), World(root)
        batch_a(f, world)
        passed, lines = batch(f, world, "REQ-3-1-2", B, B_TEXT, [{"write": {APP: "base SORT"}, "check": True}])

        assert passed is True and f.regression_debts == ["REQ-3-1-2"], (passed, f.regression_debts, lines)
        assert app_text(root) == "base SORT" and not rb.put_back(lines)
        assert any("earlier non-foundation specs fail; the batch is kept" in line for line in lines), lines

        assert A not in f.partial_green and any("leave the regression checks" in line for line in lines), lines
        assert B in f.green_specs

@case
def a_partial_green_test_is_never_a_foundation_break():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f, world = make_factory(root, "base HOME", green=[F]), World(root)
        batch_a(f, world, "base HOME EDITOR")
        passed, lines = batch(f, world, "REQ-3-1-2", B, B_TEXT, [{"write": {APP: "base HOME SORT"}, "check": True}])
        assert passed is True and not rb.put_back(lines) and app_text(root) == "base HOME SORT", lines
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f, world = make_factory(root, "base HOME", green=[F]), World(root)
        batch_a(f, world, "base HOME EDITOR")
        passed, lines = batch(f, world, "REQ-3-1-2", B, B_TEXT, [{"write": {APP: "base SORT"}, "check": True}])
        assert passed is False and rb.put_back(lines) and app_text(root) == "base HOME EDITOR", lines

@case
def a_rolled_back_batch_contributes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f, world = make_factory(root, "base HOME", green=[F]), World(root)

        passed, lines = batch_a(f, world, "base EDITOR")
        assert passed is False and rb.put_back(lines) and app_text(root) == "base HOME", lines
        assert f.partial_green == {}, f.partial_green
        assert any("put back; no test of its own spec joins" in line for line in lines), lines

@case
def a_green_batch_and_a_small_tree_and_the_setting_off_book_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f, world = make_factory(root, "base"), World(root)
        passed, _ = batch_a(f, world, "base EDITOR SEED-B2")
        assert passed is True and f.partial_green == {} and A in f.green_specs
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f, world = make_factory(root, "base"), World(root)
        batch_a(f, world)
        assert A in f.partial_green
        passed, _ = batch_a(f, world, "base EDITOR SEED-B2")
        assert passed is True and f.partial_green == {} and A in f.green_specs, f.partial_green
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f, world = make_factory(root, "base"), World(root)
        f.large_tree = False
        batch_a(f, world)
        assert f.partial_green == {}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f, world = make_factory(root, "base"), World(root)
        batch_a(f, world, settings={"partial_green_guard": False})
        assert f.partial_green == {}
        f.partial_green = {A: {"batch": "REQ-3-1-1", "titles": [EDIT], "sha1": f.spec_digest((root / A).read_bytes()), "written": [APP]}}
        world.partial_runs.clear()
        batch(f, world, "REQ-3-1-2", B, B_TEXT, [{"write": {APP: "base SORT"}, "check": True}], settings={"partial_green_guard": False})
        assert world.partial_runs == [], world.partial_runs

def _own(f, world, spec: str, text: str, app: str):
    (f.project_dir / spec).parent.mkdir(parents=True, exist_ok=True)
    (f.project_dir / spec).write_text(text, encoding="utf-8")
    (f.project_dir / APP).write_text(app, encoding="utf-8")
    run = world.result(spec, world.test_groups(f.project_dir, spec, serial=False))
    return f.own_run_state(run, [spec])

@case
def only_positive_evidence_books_a_test():
    from agent import verify
    from agent.verify import StepResult

    saved = (verify.test_groups,)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f, world = make_factory(root, "base"), World(root)
        verify.test_groups = world.test_groups
        try:
            own = _own(f, world, A, A_TEXT, "base EDITOR")
            assert f.partial_green_titles(own, [A]) == ({A: [EDIT]}, "")

            assert f.partial_green_titles(None, [A])[0] == {}

            short = dict(own, run=StepResult(False, own["run"].output, ran=1, failed_tests=own["run"].failed_tests))
            assert f.partial_green_titles(short, [A])[0] == {}

            odd = dict(own, run=StepResult(False, own["run"].output, ran=2, failed_tests=[("REQ-3-1-1.spec.js", 9, 1, "renamed")]))
            assert f.partial_green_titles(odd, [A])[0] == {}

            blind = dict(own, run=StepResult(False, "E2E FAILED", ran=2, failed_tests=None))
            assert f.partial_green_titles(blind, [A])[0] == {}

            parsed = dict(own, run=StepResult(False, own["run"].output, ran=2, failed_tests=None))
            assert f.partial_green_titles(parsed, [A])[0] == {A: [EDIT]}

            (root / APP).write_text("base EDITOR changed", encoding="utf-8")
            assert f.partial_green_titles(own, [A])[0] == {}

            own = _own(f, world, A, A_TEXT, "base EDITOR")
            (root / A).write_text(A_TEXT + "// repaired" + NL, encoding="utf-8")
            assert f.partial_green_titles(own, [A])[0] == {}

            own = _own(f, world, A, A_TEXT + "// serial" + NL, "base EDITOR")
            assert f.partial_green_titles(own, [A])[0] == {}

            own = _own(f, world, A, A_TEXT, "base EDITOR")
            unrun = dict(own, run=StepResult(True, "NOT RUN SPEC FILES: REQ-3-1-1.spec.js", ran=2))
            assert f.partial_green_titles(unrun, [A])[0] == {}

            own = _own(f, world, A, A_TEXT, "base EDITOR SEED-B2")
            assert f.partial_green_titles(own, [A])[0] == {A: sorted([EDIT, SEED])}
        finally:
            verify.test_groups = saved[0]

@case
def tests_made_on_one_line_count_as_one_group():
    from agent import verify

    saved = verify.test_groups
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f, world = make_factory(root, "base"), World(root)
        verify.test_groups = world.test_groups
        try:
            text = spec_text(EDIT, loop=("REQ-3-1-1: loop one", "REQ-3-1-1: loop two"))
            own = _own(f, world, A, text, "base EDITOR LOOP1")
            assert f.partial_green_titles(own, [A])[0] == {A: [EDIT]}
            own = _own(f, world, A, text, "base EDITOR LOOP1 LOOP2")
            assert f.partial_green_titles(own, [A])[0] == {A: sorted([EDIT, "REQ-3-1-1: loop one", "REQ-3-1-1: loop two"])}
        finally:
            verify.test_groups = saved

@case
def the_guard_skips_a_changed_spec_the_own_spec_and_unchanged_trees_and_is_capped():
    from agent import verify

    saved = verify.test_groups
    lines: list[str] = []
    import main as factory_module

    saved_log = factory_module.log
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f, world = make_factory(root, "base"), World(root)
        verify.test_groups, factory_module.log = world.test_groups, lines.append
        try:
            (root / A).parent.mkdir(parents=True, exist_ok=True)
            (root / A).write_text(A_TEXT, encoding="utf-8")
            f.partial_green = {A: {"batch": "REQ-3-1-1", "titles": [EDIT], "sha1": f.spec_digest((root / A).read_bytes()), "written": [APP]}}
            before = f.snapshot_sources()
            assert f.partial_green_groups(before, [B]) == []
            (root / APP).write_text("base changed", encoding="utf-8")
            groups = f.partial_green_groups(before, [B])
            assert groups == [(A, [(3, [(3, 1, EDIT)])])], groups
            assert f.partial_green_groups(before, [A]) == []
            (root / A).write_text(A_TEXT + "// rewritten" + NL, encoding="utf-8")
            assert f.partial_green_groups(before, [B]) == []

            looped = spec_text(EDIT, loop=("REQ-3-1-1: loop one", "REQ-3-1-1: loop two"))
            (root / A).write_text(looped, encoding="utf-8")
            f.partial_green = {A: {"batch": "REQ-3-1-1", "titles": [EDIT, "REQ-3-1-1: loop one"],
                                   "sha1": f.spec_digest((root / A).read_bytes()), "written": [APP]}}
            assert [line for line, _ in f.partial_green_groups(before, [B])[0][1]] == [3], f.partial_green_groups(before, [B])

            many = spec_text(*[f"REQ-9: t{i}" for i in range(12)])
            store = {}
            for index in range(3):
                spec = f"backend/test-e2e/REQ-9-{index}.spec.js"
                (root / spec).write_text(many, encoding="utf-8")
                store[spec] = {"batch": f"REQ-9-{index}", "titles": [f"REQ-9: t{i}" for i in range(12)],
                               "sha1": f.spec_digest((root / spec).read_bytes()), "written": [APP] if index == 2 else ["frontend/src/Other.tsx"]}
            f.partial_green = store
            groups = f.partial_green_groups(before, [B])
            assert sum(len(g) for _, g in groups) == f.PARTIAL_GROUPS_MAX == 16, groups
            assert [s for s, _ in groups] == ["backend/test-e2e/REQ-9-2.spec.js", "backend/test-e2e/REQ-9-0.spec.js"], [s for s, _ in groups]
            assert any("over the cap of 16" in line and "REQ-9-1.spec.js" in line for line in lines), lines
        finally:
            verify.test_groups, factory_module.log = saved, saved_log

@case
def the_merged_result_names_the_partial_spec_and_keeps_the_unnamed_worst_case():
    from agent.verify import StepResult
    import main as factory_module

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = make_factory(root, "base")
        red = StepResult(False, "FAILED SPEC FILES: REQ-3-1-1.spec.js" + NL + "  1 failed" + NL + f"    test-e2e/REQ-3-1-1.spec.js:3:1 › {EDIT} ───", ran=1)
        f.run_partial_tests = lambda spec, groups: red
        saved = factory_module.log
        factory_module.log = lambda line: None
        try:

            merged = f.with_partial_green(StepResult(True, "2 of 2 spec files passed"), [(A, [(3, [])])])
            assert not merged.ok and factory_module.failed_spec_names(merged.output) == {"REQ-3-1-1.spec.js"}, merged.output
            assert factory_module.failed_test_titles(merged.output) == {"REQ-3-1-1.spec.js": {EDIT}}

            base = StepResult(False, rb.spec_output("REQ-1-1-1.spec.js", [rb.S1]))
            merged = f.with_partial_green(base, [(A, [(3, [])])])
            assert factory_module.failed_spec_names(merged.output) == {"REQ-1-1-1.spec.js", "REQ-3-1-1.spec.js"}
            assert factory_module.failed_test_signatures(merged.output)["REQ-1-1-1.spec.js"][rb.S1]

            dead = StepResult(False, "backend did not become ready for e2e")
            assert f.with_partial_green(dead, [(A, [(3, [])])]) is dead

            f.run_partial_tests = lambda spec, groups: StepResult(False, "backend did not become ready for e2e")
            merged = f.with_partial_green(StepResult(True, "(no earlier green spec)"), [(A, [(3, [])])])
            assert factory_module.failed_spec_names(merged.output) == {"REQ-3-1-1.spec.js"}

            f.run_partial_tests = lambda spec, groups: StepResult(True, "no browser", skipped=True)
            ok = StepResult(True, "1 passed")
            assert f.with_partial_green(ok, [(A, [(3, [])])]) is ok
        finally:
            factory_module.log = saved

@case
def partial_runs_are_one_by_one_or_limited_to_their_lines_never_the_whole_file():
    from agent import verify
    from agent.verify import StepResult

    calls: list[tuple] = []
    saved = (verify.run_tests_isolated, verify._run_e2e_once, verify.PER_TEST_ISOLATION)
    with tempfile.TemporaryDirectory() as tmp:
        f = make_factory(Path(tmp), "base")
        verify.run_tests_isolated = lambda project_dir, spec, groups, log=print: calls.append(("isolated", [g[0] for g in groups])) or StepResult(True, "")
        verify._run_e2e_once = lambda project_dir, specs, log=print, only=None, **kw: calls.append(("together", only)) or StepResult(True, "")
        try:
            f.run_partial_tests(A, [(3, []), (5, [])])
            verify.PER_TEST_ISOLATION = False
            f.run_partial_tests(A, [(3, []), (5, [])])
            verify.PER_TEST_ISOLATION = True
            f.run_partial_tests(A, [(i, []) for i in range(1, 30)])
        finally:
            verify.run_tests_isolated, verify._run_e2e_once, verify.PER_TEST_ISOLATION = saved
    assert calls[0] == ("isolated", [3, 5]), calls
    assert calls[1] == ("together", ["test-e2e/REQ-3-1-1.spec.js:3", "test-e2e/REQ-3-1-1.spec.js:5"]), calls
    assert calls[2][0] == "together" and len(calls[2][1]) == 29, calls

def _retry_engine(second: bool, restore_fails: bool = False):
    f, calls = cl._engine({"A": [False, second], "B": [True]})
    first_entry = {"batch": "A", "titles": ["A: first"], "sha1": "1", "written": []}
    retry_entry = {"batch": "A", "titles": ["A: retry"], "sha1": "2", "written": []}
    inner = f.process_node

    def process_node(node, nodes_left, retry=False):
        ok = inner(node, nodes_left, retry=retry)
        spec = f"backend/test-e2e/{node['id']}.spec.js"
        store = f.__dict__.setdefault("partial_green", {})
        store.pop(spec, None)
        if not ok and node["id"] == "A":
            store[spec] = retry_entry if retry else first_entry
        return ok
    f.process_node = process_node
    if restore_fails:
        def failing_restore(snapshot):
            f.restore_failures += 1
            return 0
        f.restore_sources = failing_restore
    return f, first_entry

@case
def a_red_retry_puts_the_first_attempts_partial_green_tests_back():
    spec = "backend/test-e2e/A.spec.js"
    f, first_entry = _retry_engine(False)
    result, lines = cl._queue(f, "A", "B")
    assert result is False and f.foundation_retried == ["A"], lines
    assert f.partial_green == {spec: first_entry}, f.partial_green

    f, _ = _retry_engine(False, restore_fails=True)
    cl._queue(f, "A", "B")
    assert f.partial_green == {}, f.partial_green

    f, _ = _retry_engine(True)
    assert cl._queue(f, "A", "B")[0] is True and f.partial_green == {}, f.partial_green

@case
def the_checkpoint_keeps_the_partial_green_tests():
    import main as factory_module

    saved = factory_module.log
    factory_module.log = lambda line: None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = make_factory(root, "base")
            f.description_pass, f.description_specs, f.llm = {}, [], None
            f.partial_green = {A: {"batch": "REQ-3-1-1", "titles": [EDIT], "sha1": "x", "written": [APP]}}
            f.save_checkpoint("digest", ["REQ-3-1-1"], "")
            state = json.loads((root / ".arc" / "resume.json").read_text(encoding="utf-8"))
            assert state["partial_green"] == f.partial_green
            g = make_factory(root, "base")
            g.partial_green = {}
            assert g.load_checkpoint("digest") == ["REQ-3-1-1"] and g.partial_green == f.partial_green

            del state["partial_green"]
            (root / ".arc" / "resume.json").write_text(json.dumps(state), encoding="utf-8")
            h = make_factory(root, "base")
            h.partial_green = {}
            h.load_checkpoint("digest")
            assert h.partial_green == {}
    finally:
        factory_module.log = saved

@case
def factories_without_the_new_field_still_run():
    import main as factory_module

    f = factory_module.Factory.__new__(factory_module.Factory)
    f.large_tree = True
    assert f.partial_green_groups({}, []) == []
    f.drop_partial_green({"x.spec.js"}, "R")

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
    print(f"{len(CASES) - failed}/{len(CASES)} partial-green cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
