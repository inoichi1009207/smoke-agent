from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_rollback as rb
import cases_v145a as v145a

NL = chr(10)
CASES = []
FSEED = "frontend/src/data/seed.ts"
SEEDS = "Seed data:\n- Workbook `Q3 Sales`: header `Region/Amount` and rows `East/1200` and `North/800`"

F1_TEST = ("test(\"{title}\", async ({ page }) => {" + chr(10)
           + "  await page.goto(\"/\");" + chr(10)
           + "  await click(page, { role: \"link\", name: \"Q3 Sales\" });" + chr(10)
           + "  const grid = region(page, \"grid\", \"Worksheet grid\");" + chr(10)
           + "  await notSee(page, \"East\", grid);" + chr(10) + "});" + chr(10))
F1_TEXT = ("const { test } = require(\"@playwright/test\");" + chr(10)
           + "const { click, notSee, region } = require(\"../e2e-support/outsider\");" + chr(10) + chr(10)
           + F1_TEST.replace("{title}", rb.S1) + chr(10) + F1_TEST.replace("{title}", rb.S2))

def write_f1(root: Path, text: str = F1_TEXT) -> None:
    path = root / rb.F1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
NODE = dict(v145a.NODE, seed_sentences=SEEDS)
WORLD_ERROR = "Error: The requirement says Username format is invalid is shown, but nothing visible matches."
NOTSEE_EAST = "Error: The requirement says East is no longer shown, but it is still visible."
QUARANTINE_LINE = ("[tdd] REQ-9: earlier spec(s) ['REQ-1-1-1.spec.js'] fail only on notSee of stated seed values ['East'] "
                   "after a seed-only change: the spec contradicts the requirement's seed (union); quarantined, the batch is kept")

def case(function):
    CASES.append(function)
    return function

def run_batches(batches: list[tuple[dict, list[dict]]], error: str = NOTSEE_EAST, settings: dict | None = None,
                seed_before: str = "rows alice", llm=None):
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
        write_f1(root)
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
            factory_module.lint_application = lambda project_dir, native: ""
            factory_module.log = lines.append
            results = [f.process_node(dict(node), 1) for node, _ in batches]
            after = {p.relative_to(root / "frontend" / "src").as_posix(): p.read_text(encoding="utf-8")
                     for p in sorted((root / "frontend" / "src").rglob("*")) if p.is_file()}
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
            clock.now = saved_now
    assert not scripts, f"scripted steps left over: {scripts}"
    return lines, after, world, results, f

def seed_only(seed: str) -> list[dict]:
    return [{"do": [("write", {FSEED: seed}), ("check",)]}]

INCIDENT_SEED = "rows OWN-OK East " + rb.brk(rb.S1)

def quarantined(lines: list[str]) -> bool:
    return any("quarantined, the batch is kept" in line for line in lines)

@case
def the_incident_shape_is_excused_the_batch_kept_and_the_spec_quarantined():
    lines, after, world, results, f = run_batches([(NODE, seed_only(INCIDENT_SEED))])
    assert QUARANTINE_LINE in lines, lines
    assert results == [True], (results, lines)
    assert after["data/seed.ts"] == INCIDENT_SEED and after["App.tsx"] == "base", after
    assert rb.F1 not in f.green_specs and rb.F2 in f.green_specs and rb.OWN in f.green_specs, f.green_specs
    assert f.seed_quarantine == {"REQ-1-1-1.spec.js": ["East"]} and "REQ-9" not in f.regression_debts, f.regression_debts
    assert not any("put back to the state before this batch" in line for line in lines), lines

    assert not any("REQ-1-1-1.spec.js" in line and ("deleted" in line or "rewritten" in line) for line in lines), lines

@case
def switched_off_the_incident_is_put_back_as_v156():
    lines, after, world, results, f = run_batches([(NODE, seed_only(INCIDENT_SEED))], settings={"seed_conflict_quarantine": False})
    assert not quarantined(lines), lines
    assert any("ends red on regressions in the FOUNDATION specs" in line for line in lines), lines
    assert results == [False] and after["data/seed.ts"] == "rows alice" and rb.F1 in f.green_specs, (results, after)

@case
def a_quarantined_spec_no_longer_drags_a_later_batch_red():
    later = dict(NODE, id="REQ-10", name="Batch ten", members=["REQ-10"])
    lines, after, world, results, f = run_batches([(NODE, seed_only(INCIDENT_SEED)),
                                                    (later, seed_only(INCIDENT_SEED + " more"))])
    assert results == [True, True], (results, lines)
    second = INCIDENT_SEED + " more"
    runs = world.runs_on("base" + NL + second)
    assert runs and not any(rb.F1 in specs for specs in runs), world.runs
    assert any(rb.F2 in specs for specs in runs), world.runs
    assert not any("REQ-10" in line and "FOUNDATION" in line for line in lines), lines
    assert sum("quarantined, the batch is kept" in line for line in lines) == 1, lines

@case
def a_non_seed_file_changed_is_not_excused():
    script = [{"do": [("write", {rb.APP: "base OWN-OK", FSEED: "rows East " + rb.brk(rb.S1)}), ("check",)]}]
    lines, after, world, results, f = run_batches([(NODE, script)])
    assert not quarantined(lines) and rb.F1 in f.green_specs, lines
    assert not getattr(f, "seed_quarantine", {}), f.seed_quarantine

@case
def another_error_kind_in_the_run_is_not_excused():
    other = NOTSEE_EAST + NL + "    Error: expect(locator).toBeVisible() failed"
    lines, after, world, results, f = run_batches([(NODE, seed_only(INCIDENT_SEED))], error=other)
    assert not quarantined(lines) and results == [False] and rb.F1 in f.green_specs, lines

@case
def a_notsee_value_that_is_no_stated_seed_value_is_not_excused():
    west = NOTSEE_EAST.replace("East", "West")
    lines, after, world, results, f = run_batches([(NODE, seed_only(INCIDENT_SEED))], error=west)
    assert not quarantined(lines) and results == [False] and rb.F1 in f.green_specs, lines

@case
def an_own_spec_red_at_the_last_check_is_not_excused():
    script = [{"do": [("write", {FSEED: INCIDENT_SEED}), ("check",), ("write", {FSEED: "rows East " + rb.brk(rb.S1)}), ("check",)]}]
    lines, after, world, results, f = run_batches([(NODE, script)])
    assert not quarantined(lines) and results == [False] and rb.F1 in f.green_specs, lines

@case
def source_shape_both_ends_ask_the_excuse_before_any_put_back():
    import inspect

    import main as factory_module

    text = inspect.getsource(factory_module.Factory.process_node)
    unit = text.index("excuse_seed_conflict()\n")
    assert unit < text.index("restart_why = self.restart_reason(") < text.index("ends red on regressions in the FOUNDATION specs"), "order"
    repair = text.index('if regression_state["red"] and excuse_seed_conflict():')
    assert repair < text.index("e2e repair ends red on regressions in the FOUNDATION specs"), "e2e repair order"

@case
def with_the_real_seed_lock_a_reverted_answer_then_the_east_rows_end_kept_and_quarantined():

    import cases_v145b as v145b
    import cases_v156a as v156a

    seeds = SEEDS
    node = dict(NODE, seed_sentences=seeds)
    before = "rows Region Amount North 800"
    removed = "rows Item Qty North 800"
    kept = "rows Region Amount North 800 East 1200 OWN-OK " + rb.brk(rb.S1)
    model = v145b.Model([v145b.block(FSEED, removed), v145b.block(FSEED, kept)])
    with v156a.Lock():
        lines, after, world, results, f = run_batches([(node, [])], seed_before=before, llm=model)
    assert any(l.startswith("[seed-lock] REQ-9 round ") and ": reverted ['" + FSEED + "']" in l and "'Region'" in l for l in lines), \
        [l for l in lines if "seed-lock" in l]
    assert not world.runs_on("base" + NL + removed), world.runs
    line = QUARANTINE_LINE
    assert line in lines, [l for l in lines if "[tdd]" in l][-12:]
    assert results == [True] and after["data/seed.ts"].rstrip(NL) == kept, (results, after)
    assert rb.F1 not in f.green_specs and f.seed_quarantine == {"REQ-1-1-1.spec.js": ["East"]}, f.green_specs

@case
def a_conflict_that_first_appears_after_the_in_batch_restart_is_excused_and_quarantined():

    script = [{"do": [("write", {rb.APP: "base X", FSEED: INCIDENT_SEED}), ("check",)], "stop_reason": "stuck"},
              {"do": [("write", {FSEED: INCIDENT_SEED}), ("check",)], "stop_reason": "stuck"}]
    lines, after, world, results, f = run_batches([(NODE, script)])
    assert any("(in-batch restart)" in line for line in lines), lines
    assert any("the in-batch restart ended with its own spec green and earlier spec(s) ['REQ-1-1-1.spec.js'] red only on a "
               "seed conflict; it is kept" in line for line in lines), lines
    assert sum(QUARANTINE_LINE == line for line in lines) == 1, lines
    assert results == [True] and after == {"App.tsx": "base", "data/seed.ts": INCIDENT_SEED}, (results, after)
    assert rb.F1 not in f.green_specs and f.seed_quarantine == {"REQ-1-1-1.spec.js": ["East"]}, f.green_specs

@case
def an_excusable_restart_state_that_is_not_the_chosen_end_commits_nothing():

    script = [{"do": [("write", {rb.APP: "base X", FSEED: INCIDENT_SEED}), ("check",)], "stop_reason": "stuck"},
              {"do": [("write", {FSEED: INCIDENT_SEED}), ("check",), ("write", {rb.APP: "base Y"}), ("check",)],
               "stop_reason": "stuck"}]
    lines, after, world, results, f = run_batches([(NODE, script)])
    assert any("(in-batch restart)" in line for line in lines), lines
    assert not quarantined(lines) and not getattr(f, "seed_quarantine", {}), lines
    assert rb.F1 in f.green_specs and results == [False], (results, lines)

def direct(change=None, output_error: str = NOTSEE_EAST, own_ok: bool = True, red: bool = True, after_check=None,
           settings: dict | None = None, node: dict | None = None, failed=None, cut: bool = False, unknown=None,
           second_error: str | None = None, unlisted: bool = False):
    from agent.verify import StepResult

    with v145a.Settings(**(settings or {})), tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = rb.make_factory(root, "base")
        write_f1(root)
        (root / FSEED).parent.mkdir(parents=True, exist_ok=True)
        (root / FSEED).write_text("rows alice", encoding="utf-8")
        before = f.snapshot_sources()
        (root / FSEED).write_text(INCIDENT_SEED, encoding="utf-8")
        if change:
            change(root)
        check_state = {"own": f.own_run_state(StepResult(own_ok, "1 passed" if own_ok else "1 failed"), [rb.OWN])}
        if after_check:
            after_check(root)
        if second_error is None:
            output = rb.spec_output("REQ-1-1-1.spec.js", [rb.S1]).replace(WORLD_ERROR, output_error)
        else:
            output = rb.spec_output("REQ-1-1-1.spec.js", [rb.S1, rb.S2]).replace(WORLD_ERROR, output_error, 1).replace(WORLD_ERROR, second_error)
        if cut:
            output = output.replace("  1 failed", "[middle of the output omitted]\n  1 failed")
        state = {"red": red, "foundation": True, "failed": list(failed or ["REQ-1-1-1.spec.js"]), "output": output,
                 "unknown": list(unknown or [])}
        if unlisted:
            f.green_specs = [spec for spec in f.green_specs if spec != rb.F1]
            f.e2e_specs = [spec for spec in f.e2e_specs if spec != rb.F1]
        return f.seed_conflict_excuse(node or NODE, before, state, check_state)

@case
def direct_all_conditions_hold_excused():
    assert direct() == (["REQ-1-1-1.spec.js"], ["East"]), direct()

@case
def direct_condition_1_a_non_seed_file_changed():
    assert direct(change=lambda root: (root / rb.APP).write_text("base 2", encoding="utf-8")) == ([], [])

    def init_db(root):
        path = root / "frontend" / "src" / "database" / "init_db.js"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("rows", encoding="utf-8")
    assert direct(change=init_db)[0] == ["REQ-1-1-1.spec.js"]

@case
def direct_condition_2_own_spec_not_green_on_the_sources_on_disk():
    assert direct(own_ok=False) == ([], [])
    assert direct(red=False) == ([], [])
    assert direct(after_check=lambda root: (root / FSEED).write_text(INCIDENT_SEED + " later", encoding="utf-8")) == ([], [])

@case
def direct_condition_3_only_notsee_errors_in_the_regressed_specs():
    assert direct(output_error=NOTSEE_EAST + NL + "    TimeoutError: page.click: Timeout 5000ms exceeded.") == ([], [])
    assert direct(output_error="Error: The requirement says East is shown, but nothing visible matches.") == ([], [])
    assert direct(output_error="Test timeout of 30000ms exceeded.") == ([], [])

    assert direct(second_error=NOTSEE_EAST)[0] == ["REQ-1-1-1.spec.js"]
    assert direct(second_error="Test timeout of 30000ms exceeded.") == ([], [])

    assert direct(second_error="expect(received).toBe(expected)") == ([], [])
    assert direct(unlisted=True) == ([], [])
    assert direct(cut=True) == ([], [])
    assert direct(failed=["REQ-1-1-1.spec.js", "REQ-1-1-3.spec.js"]) == ([], [])
    assert direct(unknown=[rb.F2]) == ([], [])

@case
def direct_condition_4_values_must_be_stated_seed_values_exactly():
    assert direct(output_error=NOTSEE_EAST.replace("East", "West")) == ([], [])
    assert direct(output_error=NOTSEE_EAST.replace("East", "row named East")) == ([], [])
    assert direct(output_error=NOTSEE_EAST.replace("East", "east")) == ([], [])
    assert direct(node=dict(NODE, seed_sentences="")) == ([], [])

@case
def direct_condition_5_the_switch():
    assert direct(settings={"seed_conflict_quarantine": False}) == ([], [])

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
    print(f"{len(CASES) - failed}/{len(CASES)} v157a cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
