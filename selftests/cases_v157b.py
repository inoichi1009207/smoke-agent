from __future__ import annotations

import sys
import tempfile
import time
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_v157a as a
import cases_v145a as v145a

rb = a.rb
NL = chr(10)
CASES = []
FSEED = a.FSEED
NOTSEE_EAST = a.NOTSEE_EAST
WORLD_ERROR = a.WORLD_ERROR
INCIDENT_SEED = a.INCIDENT_SEED
QUARANTINE_LINE = a.QUARANTINE_LINE
quarantined = a.quarantined

SEEDS = "Seed data:" + NL + "- Workbook `Q3 Sales`: header `Region/Amount` and rows `East/1200` and `North/800`"
NODE = dict(a.NODE, seed_sentences=SEEDS)

NO_NAME_NODE = dict(a.NODE, seed_sentences="Seed data:" + NL + "- rows `East/1200` and `North/800`")
LINT = "LINT: <form> without noValidate"
NAME = "REQ-1-1-1.spec.js"
CONSOLE_500 = "    console.error Failed to load resource: the server responded with a status of 500"
SQLITE = "    SQLITE_CONSTRAINT: UNIQUE constraint failed: rows.id"
TEARDOWN = '    Tearing down "context" exceeded the test timeout of 30000ms.'
FOREIGN_LINES = (CONSOLE_500, SQLITE, TEARDOWN)

def case(function):
    CASES.append(function)
    return function

def spec_text(tests: list[tuple[str, str]]) -> str:
    head = ['const { test } = require("@playwright/test");',
            'const { click, see, notSee, fill, region } = require("../e2e-support/outsider");', ""]
    for title, body in tests:
        head += [f'test("{title}", async ({{ page }}) => {{', body, "});", ""]
    return NL.join(head)

OPEN = ('  await page.goto("/");' + NL + '  await click(page, { role: "link", name: "Q3 Sales" });' + NL
        + '  const grid = region(page, "grid", "Worksheet grid");')
FLAGGED_BODY = OPEN + NL + '  await notSee(page, "East", grid);'
FILTERED_BODY = OPEN + NL + '  await fill(page, "Filter", "North");' + NL + '  await notSee(page, "East", grid);'
NORTH_BODY = OPEN + NL + '  await notSee(page, "North", grid);'
FLAGGED = spec_text([(rb.S1, FLAGGED_BODY)])
FILTERED = spec_text([(rb.S1, FILTERED_BODY)])
OTHER_TITLE = spec_text([(rb.S2, FLAGGED_BODY)])
NORTH_ONLY = spec_text([(rb.S1, NORTH_BODY)])
BOTH = spec_text([(rb.S1, FLAGGED_BODY), (rb.S2, FLAGGED_BODY)])

def incident_output() -> str:
    return rb.spec_output(NAME, [rb.S1]).replace(WORLD_ERROR, NOTSEE_EAST)

def insert_after_error(output: str, line: str) -> str:
    return output.replace(NOTSEE_EAST, NOTSEE_EAST + NL + line, 1)

@case
def the_spec_texts_of_these_tests_are_flagged_as_intended():
    from agent.outsider import seed_absence_assertions

    head = f'test "{rb.S1}": notSee("East")'
    assert any(p.startswith(head) for p in seed_absence_assertions(FLAGGED, NODE)), seed_absence_assertions(FLAGGED, NODE)
    assert not seed_absence_assertions(FILTERED, NODE)
    assert not any(p.startswith(head) for p in seed_absence_assertions(OTHER_TITLE, NODE))
    assert not any(p.startswith(head) for p in seed_absence_assertions(NORTH_ONLY, NODE))
    assert not seed_absence_assertions(FLAGGED, NO_NAME_NODE)

def direct2(spec: str | None = FLAGGED, lint: str | None = "", output_error: str = NOTSEE_EAST, node: dict | None = None,
            second_error: str | None = None, insert: str | None = None, change=None):
    from agent.verify import StepResult

    with v145a.Settings(), tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = rb.make_factory(root, "base")
        (root / FSEED).parent.mkdir(parents=True, exist_ok=True)
        (root / FSEED).write_text("rows alice", encoding="utf-8")
        if spec is not None:
            (root / rb.F1).parent.mkdir(parents=True, exist_ok=True)
            (root / rb.F1).write_text(spec, encoding="utf-8")
        before = f.snapshot_sources()
        (root / FSEED).write_text(INCIDENT_SEED, encoding="utf-8")
        if change:
            change(root)
        check_state = {"own": f.own_run_state(StepResult(True, "1 passed"), [rb.OWN])}
        if lint is not None:
            check_state["last_lint"] = lint
        if second_error is None:
            output = rb.spec_output(NAME, [rb.S1]).replace(WORLD_ERROR, output_error)
        else:
            output = rb.spec_output(NAME, [rb.S1, rb.S2]).replace(WORLD_ERROR, output_error, 1).replace(WORLD_ERROR, second_error)
        if insert is not None:
            output = output.replace(output_error, output_error + NL + insert, 1)
        state = {"red": True, "foundation": True, "failed": [NAME], "output": output, "unknown": []}
        return f.seed_conflict_excuse(node or NODE, before, state, check_state)

EXCUSED = ([NAME], ["East"])

@case
def direct2_all_conditions_hold_with_a_flagged_spec_excused():
    assert direct2() == EXCUSED, direct2()

@case
def direct2_spec_file_missing_not_excused():
    assert direct2(spec=None) == ([], [])

@case
def direct2_spec_not_flagged_not_excused():
    assert direct2(spec=FILTERED) == ([], [])
    assert direct2(spec=OTHER_TITLE) == ([], [])
    assert direct2(spec=NORTH_ONLY) == ([], [])
    assert direct2(spec="") == ([], [])
    assert direct2(node=NO_NAME_NODE) == ([], [])

@case
def direct2_every_failing_test_must_be_flagged():
    assert direct2(spec=FLAGGED, second_error=NOTSEE_EAST) == ([], [])
    assert direct2(spec=BOTH, second_error=NOTSEE_EAST) == EXCUSED

@case
def direct2_the_helper_raising_excuses_nothing():
    import agent.outsider as outsider
    import main as factory_module

    def boom(*args, **kwargs):
        raise RuntimeError("helper broke")

    saved = outsider.seed_absence_assertions
    saved_main = factory_module.__dict__.get("seed_absence_assertions")
    try:
        outsider.seed_absence_assertions = boom
        if saved_main is not None:
            factory_module.seed_absence_assertions = boom
        assert direct2() == ([], [])
    finally:
        outsider.seed_absence_assertions = saved
        if saved_main is not None:
            factory_module.seed_absence_assertions = saved_main

@case
def direct2_lint_of_the_last_check_not_excused():
    assert direct2(lint=LINT) == ([], [])
    assert direct2(lint="") == EXCUSED

@case
def direct2_foreign_error_lines_in_the_failing_block_not_excused():
    for line in FOREIGN_LINES:
        assert direct2(insert=line) == ([], []), line

@case
def output_whitelist_the_incident_output_is_clean():
    import main as factory_module

    assert factory_module.seed_conflict_output_clean(incident_output()) is True
    with_context = insert_after_error(incident_output(), "    Error Context: test-results\\REQ-1-1-1-Scenario-1\\error-context.md")
    assert factory_module.seed_conflict_output_clean(with_context) is True
    framed = insert_after_error(incident_output(), "      12 |   await click(page, { role: \"link\", name: \"Q3 Sales\" });"
                                + NL + "    > 13 |   await notSee(page, \"East\", \"grid\");")
    assert factory_module.seed_conflict_output_clean(framed) is True

@case
def output_whitelist_foreign_lines_are_not_clean():
    import main as factory_module

    for line in FOREIGN_LINES:
        assert factory_module.seed_conflict_output_clean(insert_after_error(incident_output(), line)) is False, line
        assert factory_module.seed_conflict_output_clean(incident_output() + NL + line.strip()) is False, line

def run_batches(batches: list[tuple[dict, list[dict]]], error: str = NOTSEE_EAST, settings: dict | None = None,
                seed_before: str = "rows alice", llm=None, prep=None, lint: str = ""):
    import main as factory_module
    from agent import clock
    from agent.testrun import TestOutcome

    lines: list[str] = []
    seen: list = []
    offset = [0.0]
    names = ("run_e2e", "build_frontend", "Implementer", "run_node_tests", "static_test_defects", "parse_defects_in_output",
             "lint_application", "log")
    saved = {name: getattr(factory_module, name) for name in names}
    saved_now = clock.now
    scripts = [step for _, script in batches for step in script]
    with v145a.Settings(audit_enabled=False, **(settings or {})), tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = rb.World(root)
        f = rb.make_factory(root, "base")
        if prep:
            prep(f, root)
        (root / FSEED).parent.mkdir(parents=True, exist_ok=True)
        (root / FSEED).write_text(seed_before, encoding="utf-8")

        def run_e2e(project_dir, specs, log=print):
            got = world.run_e2e(project_dir, specs, log=log)
            got.output = got.output.replace(WORLD_ERROR, error)
            return got

        try:
            frozen = time.time()
            clock.now = lambda: frozen + offset[0]
            f.started = frozen
            factory_module.run_e2e, factory_module.build_frontend = run_e2e, world.build_frontend
            if llm is None:
                factory_module.Implementer = v145a.fake_implementer(scripts, seen, offset)
            else:
                f.llm = llm
            factory_module.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
            factory_module.static_test_defects = lambda project_dir, files, **kw: ""
            factory_module.parse_defects_in_output = lambda output, files: ""
            factory_module.lint_application = lambda project_dir, native: lint
            factory_module.log = lines.append
            results = [f.process_node(dict(node), 1) for node, _ in batches]
            after = {p.relative_to(root / "frontend" / "src").as_posix(): p.read_text(encoding="utf-8")
                     for p in sorted((root / "frontend" / "src").rglob("*")) if p.is_file()}
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
            clock.now = saved_now
    return lines, after, world, results, f, scripts

def write_spec(text: str):
    def prep(f, root):
        (root / rb.F1).parent.mkdir(parents=True, exist_ok=True)
        (root / rb.F1).write_text(text, encoding="utf-8")
    return prep

@case
def world_the_incident_with_a_flagged_spec_is_quarantined():
    lines, after, world, results, f, left = run_batches([(NODE, a.seed_only(INCIDENT_SEED))], prep=write_spec(FLAGGED))
    assert not left, left
    assert QUARANTINE_LINE in lines, [line for line in lines if "[tdd]" in line][-12:]
    assert results == [True] and after["data/seed.ts"] == INCIDENT_SEED, (results, after)
    assert rb.F1 not in f.green_specs and f.seed_quarantine == {NAME: ["East"]}, f.green_specs

@case
def world_the_incident_with_an_unflagged_spec_is_put_back():
    lines, after, world, results, f, left = run_batches([(NODE, a.seed_only(INCIDENT_SEED))], prep=write_spec(FILTERED))
    assert not quarantined(lines), lines
    assert results == [False] and rb.F1 in f.green_specs, (results, f.green_specs)
    assert not getattr(f, "seed_quarantine", {}), f.seed_quarantine

@case
def world_the_incident_with_lint_is_not_excused():
    lines, after, world, results, f, left = run_batches([(NODE, a.seed_only(INCIDENT_SEED))], prep=write_spec(FLAGGED),
                                                        lint=LINT)
    assert not quarantined(lines), lines
    assert rb.F1 in f.green_specs, f.green_specs
    assert not getattr(f, "seed_quarantine", {}), f.seed_quarantine

class _Recorder:
    calls: list = []

    def __init__(self, *args, **kwargs):
        _Recorder.calls.append(("init", args, kwargs))

    def __getattr__(self, name):
        def record(*args, **kwargs):
            _Recorder.calls.append((name, args, kwargs))
            return None
        return record

def closing_factory(root: Path):
    f = rb.make_factory(root, "base")
    for spec in (rb.F1, rb.F2):
        (root / spec).parent.mkdir(parents=True, exist_ok=True)
        (root / spec).write_text(FLAGGED, encoding="utf-8")
    f.seed_quarantine = {NAME: ["East"]}
    f.spec_context = {rb.F1: {"node": dict(NODE, id="REQ-1-1-1")}, rb.F2: {"node": dict(NODE, id="REQ-1-1-3")}}
    f.hard_fits = lambda *args, **kwargs: True
    f.hard_end = lambda: None
    f.closing_budget = lambda: 100000.0
    f.closing_repair_min_sec = lambda: 0.0
    f.note_duration = lambda *args, **kwargs: None
    return f

def with_patches(body):
    import main as factory_module

    lines: list[str] = []
    runs: list[list[str]] = []
    names = ("run_e2e", "build_frontend", "Implementer", "log")
    saved = {name: getattr(factory_module, name) for name in names}
    _Recorder.calls = []

    def run_e2e(project_dir, specs, log=print, **kwargs):
        runs.append(list(specs))
        return types.SimpleNamespace(ok=False, skipped=True, output="", passed=False)

    try:
        factory_module.run_e2e = run_e2e
        factory_module.build_frontend = lambda *args, **kwargs: types.SimpleNamespace(ok=True, output="", skipped=False)
        factory_module.Implementer = _Recorder
        factory_module.log = lines.append
        with v145a.Settings(), tempfile.TemporaryDirectory() as tmp:
            f = closing_factory(Path(tmp))
            return body(f, lines, runs)
    finally:
        for name, value in saved.items():
            setattr(factory_module, name, value)

@case
def closing_repair_failing_specs_skips_a_seed_quarantined_spec():
    output = ("FAILED SPEC FILES: REQ-1-1-1.spec.js, REQ-1-1-3.spec.js" + NL
              + rb.spec_output(NAME, [rb.S1]).replace(WORLD_ERROR, NOTSEE_EAST).split(NL, 1)[1])

    def body(f, lines, runs):
        tried: list[str] = []
        repaired = f.repair_failing_specs(output, only=[rb.F1, rb.F2], until=time.time() + 100000, tried=tried)
        assert repaired == 0, repaired
        assert rb.F1 not in tried and tried == [rb.F2], tried
        assert not any(rb.F1 in specs for specs in runs), runs
        assert any(rb.F2 in specs for specs in runs), runs
        assert not _Recorder.calls, _Recorder.calls
        assert sum("seed-quarantined" in line for line in lines) == 1, lines
    with_patches(body)

@case
def closing_repair_debt_specs_skips_a_seed_quarantined_spec():
    def body(f, lines, runs):
        f.debt_specs = {rb.F1: "REQ-9"}
        assert f.repair_debt_specs() == 0
        assert not any(rb.F1 in specs for specs in runs), runs
        assert not _Recorder.calls, _Recorder.calls
        assert any("seed-quarantined" in line for line in lines), lines
    with_patches(body)

@case
def checkpoint_keeps_the_seed_quarantine():
    import main as factory_module

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "app"
        (out / ".arc").mkdir(parents=True)
        dict_fields = ("spec_context", "file_specs", "test_snapshot", "test_ids_by_file", "test_titles", "report", "seed_checks")
        first = factory_module.Factory.__new__(factory_module.Factory)
        first.project_dir, first.revised_specs = out, set()
        for field in factory_module.Factory.CHECKPOINT_FIELDS:
            setattr(first, field, {} if field in dict_fields else [])
        first.started, first.spent_before = time.time(), 0.0
        first.seed_quarantine = {NAME: ["East"]}
        first.save_checkpoint("digest-1", ["REQ-9"], "")
        import json
        assert json.loads((out / ".arc" / "resume.json").read_text(encoding="utf-8")).get("seed_quarantine") == {NAME: ["East"]}
        second = factory_module.Factory.__new__(factory_module.Factory)
        second.project_dir, second.revised_specs = out, set()
        assert second.load_checkpoint("digest-1") == ["REQ-9"]
        assert second.__dict__.get("seed_quarantine") == {NAME: ["East"]}, second.__dict__.get("seed_quarantine")

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
    print(f"{len(CASES) - failed}/{len(CASES)} v157b cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
