from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NL = chr(10)
CASES = []
CHECK_OUTPUTS: list[str] = []

F1 = "backend/test-e2e/REQ-1-1-1.spec.js"
F2 = "backend/test-e2e/REQ-1-1-3.spec.js"
OWN = "backend/test-e2e/REQ-9.spec.js"
APP = "frontend/src/App.tsx"
S1 = "REQ-1-1-1: Register a New GitHub Account - Scenario 1"
S2 = "REQ-1-1-1: Register a New GitHub Account - Scenario 2"
OWN_TITLE = "REQ-9: works"

def case(function):
    CASES.append(function)
    return function

def brk(title: str) -> str:
    return f"BREAK<{title}>"

def spec_output(name: str, titles: list[str], frames: bool = True) -> str:
    lines = [f"FAILED SPEC FILES: {name}"]
    for index, title in enumerate(titles, 1):
        lines += [f"[{index}/3] test-e2e\\{name}:{10 * index}:1 › {title}",
                  f"  {index}) test-e2e\\{name}:{10 * index}:1 › {title} ───────",
                  "    Error: The requirement says Username format is invalid is shown, but nothing visible matches."]
        if frames:
            lines += ["        at fail (D:\\w\\backend\\e2e-support\\outsider.js:43:9)",
                      f"        at D:\\w\\backend\\test-e2e\\{name}:{10 * index + 3}:5"]
    lines += [f"  {len(titles)} failed"] + [f"    test-e2e\\{name}:{10 * i}:1 › {t} ────────" for i, t in enumerate(titles, 1)]
    return NL.join(lines + ["  1 passed"])

class World:

    def __init__(self, root: Path):
        self.root = root
        self.runs: list[tuple[list[str], str]] = []
        self.builds = 0

    def sources(self) -> str:
        base = self.root / "frontend" / "src"
        return NL.join(p.read_text(encoding="utf-8") for p in sorted(base.rglob("*")) if p.is_file())

    def failing(self, spec: str, text: str) -> list[str]:
        if spec == OWN:
            return [] if "OWN-OK" in text else [OWN_TITLE]
        prefix = Path(spec).name.replace(".spec.js", "")
        return [t for t in (S1, S2, f"{prefix}: Scenario 1", f"{prefix}: Scenario 2") if t.startswith(prefix) and brk(t) in text]

    def run_e2e(self, project_dir, specs, log=print):
        from agent.verify import StepResult

        text = self.sources()
        self.runs.append((list(specs), text))
        red = [(spec, self.failing(spec, text)) for spec in specs]
        red = [(spec, titles) for spec, titles in red if titles]
        if len(specs) == 1:
            return StepResult(not red, spec_output(Path(specs[0]).name, red[0][1]) if red else "1 passed")
        if not red:
            return StepResult(True, f"{len(specs)} of {len(specs)} spec files passed, each run against fresh data")
        head = "FAILED SPEC FILES: " + ", ".join(Path(s).name for s, _ in red)
        parts = [f"## {Path(s).name}{NL}{spec_output(Path(s).name, t)}" for s, t in red]
        return StepResult(False, NL.join([head, f"{len(specs) - len(red)} of {len(specs)} spec files passed, each run against fresh data"] + parts))

    def build_frontend(self, project_dir, log=print):
        from agent.verify import StepResult

        self.builds += 1
        return StepResult(True, "")

    def runs_on(self, text: str) -> list[list[str]]:
        return [specs for specs, seen in self.runs if seen == text]

def implementer_class(script: list[dict]):
    from agent.codegen import ImplementResult

    class ScriptedImplementer:
        def __init__(self, llm, project_dir, check, **options):
            self.project_dir, self.check = project_dir, check

        def implement(self, node, tests, outcome, extra_context=""):
            step = script.pop(0)
            for rel, content in step.get("write", {}).items():
                path = self.project_dir / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            written = list(step.get("write", {}))
            if not step.get("check"):
                return ImplementResult(step["status"], written, "scripted", 0)
            result = self.check()
            CHECK_OUTPUTS.append(result.output)
            status = step.get("status") or ("passed" if result.passed else "budget")
            return ImplementResult(status, written, "scripted", 1, last_check=result.output if status == "passed" else "")

    return ScriptedImplementer

class _Trace:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None

def make_factory(root: Path, app_before: str, time_budget: float = 10800.0):
    import main as factory_module
    from agent.test_author import AuthoredFile

    (root / "frontend" / "src").mkdir(parents=True, exist_ok=True)
    (root / "backend" / "src").mkdir(parents=True, exist_ok=True)
    (root / APP).write_text(app_before, encoding="utf-8")
    (root / "backend" / "src" / "server.js").write_text("// server", encoding="utf-8")
    f = factory_module.Factory.__new__(factory_module.Factory)
    f.project_dir, f.llm, f.contract, f.native_validation = root, None, "", False
    f.started, f.time_budget, f.spent_before = time.time(), time_budget, 0.0
    f.trace = _Trace()
    f.report = {"nodes": {}, "verification": {}}
    f.all_test_files, f.e2e_specs, f.green_specs = [], [F1, F2], [F1, F2]
    f.file_specs = {APP: [F1]}
    f.foundation_checks, f.foundation_baselines, f.regression_debts = {}, {}, []
    f.revised_specs, f.spec_context, f.seed_checks = set(), {}, {}
    f.test_snapshot, f.test_ids_by_file, f.test_titles = {}, {}, {}
    f.large_tree, f.current_trace_id, f.current_members = True, "", []
    f.failed_members, f.no_evidence_members, f.unattempted_members = [], [], []
    f.restore_failures, f.verify_repairs = 0, 0
    f.audit_sent_fingerprints, f.audit_rolled_back_fingerprints = [], []
    f.record_run = lambda files, ok, output: None
    f.author_tests = lambda node, prefix, contract: [AuthoredFile(path=OWN, content=f"test('{OWN_TITLE}', async () => {{}});" + NL)]
    return f

def run_batch(app_before: str, script: list[dict], time_budget: float = 10800.0):
    import main as factory_module
    from agent.settings import SETTINGS
    from agent.testrun import TestOutcome

    lines: list[str] = []
    names = ("run_e2e", "build_frontend", "Implementer", "run_node_tests", "static_test_defects", "parse_defects_in_output", "lint_application", "log")
    saved = {name: getattr(factory_module, name) for name in names}
    saved_audit = SETTINGS.get("audit_enabled", True)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = World(root)
        f = make_factory(root, app_before, time_budget)
        try:
            factory_module.run_e2e, factory_module.build_frontend = world.run_e2e, world.build_frontend
            factory_module.Implementer = implementer_class(script)
            factory_module.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
            factory_module.static_test_defects = lambda project_dir, files: ""
            factory_module.parse_defects_in_output = lambda output, files: ""
            factory_module.lint_application = lambda project_dir, native: ""
            factory_module.log = lines.append
            SETTINGS["audit_enabled"] = False
            passed = f.process_node({"id": "REQ-9", "name": "Batch nine", "members": ["REQ-9"]}, 1)
            after = (root / APP).read_text(encoding="utf-8") if (root / APP).is_file() else None
            extra = sorted(p.name for p in (root / "frontend" / "src").iterdir() if p.name != "App.tsx")
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
            SETTINGS["audit_enabled"] = saved_audit
    assert not script, f"scripted steps left over: {script}"
    return f, world, lines, after, passed, extra

def put_back(lines: list[str]) -> bool:
    return any("put back to the state before this batch" in line for line in lines)

def pre_existing(lines: list[str]) -> bool:
    return any("pre-existing foundation failure" in line for line in lines)

@case
def failing_titles_are_read_from_failure_lines_only():
    import main as factory_module

    output = NL.join([
        "FAILED SPEC FILES: REQ-1-1-1.spec.js",
        "[1/3] test-e2e\\REQ-1-1-1.spec.js:12:1 › REQ-1-1-1: Register a New GitHub Account - Scenario 1",
        "[2/3] test-e2e\\REQ-1-1-1.spec.js:43:1 › REQ-1-1-1: Register a New GitHub Account - Scenario 2",
        "  1) test-e2e\\REQ-1-1-1.spec.js:43:1 › REQ-1-1-1: Register a New GitHub Account - Scenario 2 ───────",
        "    Error: The requirement says Username format is invalid is shown, but nothing visible matches.",
        "[3/3] test-e2e\\REQ-1-1-1.spec.js:72:1 › REQ-1-1-1: Register a New GitHub Account - Scenario 3",
        "  1 failed",
        "    test-e2e\\REQ-1-1-1.spec.js:43:1 › REQ-1-1-1: Register a New GitHub Account - Scenario 2 ────────",
        "  2 passed",
    ])

    assert factory_module.failed_test_titles(output) == {"REQ-1-1-1.spec.js": {S2}}

    assert factory_module.failed_test_titles("  1) test-e2e/REQ-1.spec.js › hangs") == {}

    assert factory_module.failed_test_titles(output + NL + "... [middle of the output omitted] ...") == {}

    assert factory_module.failed_test_titles("  ✘  2 [chromium] › test-e2e/A.spec.js:9:1 › A: Delete (10.0s)") == {"A.spec.js": {"A: Delete"}}

@case
def a_foundation_red_before_the_batch_is_not_blamed_on_a_red_batch():
    before = "base " + brk(S1)
    after = before + " feature-nine"
    f, world, lines, app, passed, _ = run_batch(before, [{"write": {APP: after}, "check": True}])
    assert app == after and not put_back(lines), lines
    assert pre_existing(lines) and not passed
    assert f.report["nodes"]["REQ-9"]["written"] == [APP]

    assert world.runs_on(before) == [[F1], [F2]], world.runs_on(before)

@case
def a_foundation_red_before_the_batch_is_not_blamed_on_a_batch_with_its_own_spec_green():
    before = "base " + brk(S1)
    after = before + " OWN-OK"
    f, world, lines, app, passed, _ = run_batch(before, [{"write": {APP: after}, "check": True}])
    assert app == after and not put_back(lines), lines
    assert pre_existing(lines)

    assert passed and f.regression_debts == ["REQ-9"] and f.report["nodes"]["REQ-9"]["status"] == "passed"
    assert world.runs_on(before) == [[F1], [F2]]

@case
def a_foundation_the_batch_broke_is_still_put_back():
    before = "base"

    f, world, lines, app, passed, _ = run_batch(before, [{"write": {APP: before + " " + brk(S1)}, "check": True}])
    assert app == before and put_back(lines) and not pre_existing(lines), lines
    assert f.report["nodes"]["REQ-9"]["written"] == []

    f, world, lines, app, passed, _ = run_batch(before, [{"write": {APP: before + " OWN-OK " + brk(S1)}, "check": True}])
    assert app == before and put_back(lines) and not passed, lines

@case
def a_new_failing_test_in_an_already_red_foundation_spec_is_still_blamed():
    before = "base " + brk(S1)
    f, world, lines, app, passed, _ = run_batch(before, [{"write": {APP: before + " OWN-OK " + brk(S2)}, "check": True}])
    assert app == before and put_back(lines) and not passed, lines

@case
def the_implementer_is_told_only_of_foundation_breaks_this_batch_caused():
    from agent.codegen import FOUNDATION_MARK

    CHECK_OUTPUTS.clear()
    before = "base " + brk(S1)
    f, world, lines, app, passed, _ = run_batch(before, [{"write": {APP: before + " feature-nine"}, "check": True}])
    assert CHECK_OUTPUTS and not any(FOUNDATION_MARK in out for out in CHECK_OUTPUTS), CHECK_OUTPUTS
    assert world.runs_on(before) == [[F1], [F2]], world.runs_on(before)

    CHECK_OUTPUTS.clear()
    f, world, lines, app, passed, _ = run_batch("base", [{"write": {APP: "base " + brk(S1)}, "check": True}])
    assert any(FOUNDATION_MARK in out for out in CHECK_OUTPUTS), CHECK_OUTPUTS

@case
def the_e2e_gate_repair_path_puts_back_a_batch_that_broke_the_foundation():
    before = "base"
    broken = before + " " + brk(S1)
    script = [
        {"write": {APP: broken}, "status": "passed"},
        {"write": {APP: broken + " still"}, "check": True},
    ]
    f, world, lines, app, passed, _ = run_batch(before, script)
    assert any("e2e gate FAILED" in line for line in lines), lines
    assert app == before and put_back(lines) and not passed, lines
    assert f.report["nodes"]["REQ-9"]["written"] == [] and f.report["nodes"]["REQ-9"]["e2e"] == "failed"

    script = [
        {"write": {APP: before + " OWN-OK " + brk(S1)}, "status": "passed"},
        {"write": {APP: before + " OWN-OK " + brk(S1) + " again"}, "check": True},
    ]
    f, world, lines, app, passed, _ = run_batch(before, script)
    assert app == before and any("e2e repair ends red on regressions in the FOUNDATION" in line for line in lines), lines
    assert f.report["nodes"]["REQ-9"]["written"] == []

@case
def the_e2e_gate_repair_path_lets_off_a_foundation_that_was_red_before():
    before = "base " + brk(S1)
    script = [{"write": {APP: before + " x"}, "status": "passed"}, {"write": {APP: before + " y"}, "check": True}]
    f, world, lines, app, passed, _ = run_batch(before, script)
    assert app == before + " y" and not put_back(lines) and pre_existing(lines), lines
    assert not passed and f.report["nodes"]["REQ-9"]["written"] == [APP]

@case
def the_baseline_is_run_once_per_batch_and_not_when_time_is_short():
    import main as factory_module

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        before_text = "base " + brk(S1)
        f = make_factory(root, before_text)
        world = World(root)
        lines: list[str] = []
        saved = (factory_module.run_e2e, factory_module.build_frontend, factory_module.log)
        factory_module.run_e2e, factory_module.build_frontend, factory_module.log = world.run_e2e, world.build_frontend, lines.append
        try:
            before = f.snapshot_sources()
            (root / APP).write_text(before_text + " change", encoding="utf-8")
            assert f.red_batch_broke_foundation("REQ-9", "budget", before) is False
            assert f.red_batch_broke_foundation("REQ-9", "budget", before) is False
            assert world.runs_on(before_text) == [[F1], [F2]], world.runs_on(before_text)
            assert any("reused" in line for line in lines)
            assert (root / APP).read_text(encoding="utf-8") == before_text + " change"

            world.runs.clear()
            f.foundation_baselines = {}
            f.time_budget = f.BASELINE_MIN_LEFT_SEC / 2
            assert f.red_batch_broke_foundation("REQ-9", "budget", before) is True
            assert world.runs_on(before_text) == [] and any("too little time" in line for line in lines)
            assert (root / APP).read_text(encoding="utf-8") == before_text
        finally:
            factory_module.run_e2e, factory_module.build_frontend, factory_module.log = saved

@case
def no_baseline_when_the_batch_changed_no_existing_file():
    before = "base " + brk(S1)
    new_file = "frontend/src/NewPage.tsx"
    f, world, lines, app, passed, extra = run_batch(before, [{"write": {new_file: "export const NewPage = 1;"}, "check": True}])

    assert not any("foundation baseline: running" in line for line in lines), lines
    assert put_back(lines) and extra == [] and app == before, (lines, extra)
    baseline_like = [specs for specs, seen in world.runs if "NewPage" not in seen]
    assert baseline_like == [], baseline_like

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
    print(f"{len(CASES) - failed}/{len(CASES)} rollback cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
