from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_engine142 as e142
import cases_nofiles as nf
import cases_rollback as rb

NL = chr(10)
CASES = []
TARGET = "backend/test-e2e/REQ-2-2-2.spec.js"
G3 = "backend/test-e2e/REQ-2-1-1.spec.js"
G4 = "backend/test-e2e/REQ-2-1-2.spec.js"
R311 = "backend/test-e2e/REQ-3-1-1.spec.js"
RED_ALREADY = "backend/test-e2e/REQ-4-1-2.spec.js"
NEW = "frontend/src/NewPanel.tsx"

def case(function):
    CASES.append(function)
    return function

def name(spec: str) -> str:
    return Path(spec).name

def fixed(spec: str) -> str:
    return f"FIXED<{name(spec)}>"

def broken(spec: str) -> str:
    return f"BREAK<{name(spec)}>"

def closing(steps_per_spec: dict[str, list[dict]], e2e: list[str], green: list[str], suite_red: list[str],
            always_red: tuple[str, ...] = (), file_specs: dict[str, list[str]] | None = None, settings: dict | None = None,
            also_fixed_by: dict[str, str] | None = None, unfinished: dict[str, str] | None = None):
    import main as factory_module
    from agent.codegen import ImplementResult
    from agent.llm import LLM
    from agent.verify import StepResult

    outputs: dict[str, list[str]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = rb.make_factory(root, "base")
        f.e2e_specs, f.green_specs = list(e2e), list(green)
        f.file_specs = dict(file_specs or {rb.APP: [R311]})
        f.spec_context = {s: {"node": {"id": name(s)[:-8]}, "contract": "", "relevant": [], "tests": {}, "written": []} for s in steps_per_spec}
        runs: list[list[str]] = []

        def run_e2e(project_dir, specs, log=print, shared_data=False):
            text = "".join(p.read_text(encoding="utf-8") for p in (root / rb.APP, root / NEW) if p.is_file())
            runs.append(list(specs))
            red = [name(s) for s in specs if name(s) in always_red or broken(s) in text
                   or (s in suite_red and fixed(s) not in text and (also_fixed_by or {}).get(s, "@none@") not in text)]
            late = [s for s in specs if s in (unfinished or {})]
            if not red and late and len(specs) > 1:
                kind = unfinished[late[0]]
                if kind == "ran0":
                    return StepResult(True, f"{len(specs)} of {len(specs)} spec files passed", ran=0)
                return StepResult(True, "NOT RUN SPEC FILES: " + name(late[0]) + NL + f"{len(specs) - 1} of {len(specs)} spec files passed")
            if not red:
                return StepResult(True, f"{len(specs)} of {len(specs)} spec files passed")
            return StepResult(False, "FAILED SPEC FILES: " + ", ".join(red) + NL + NL.join(f"  1) test-e2e\\{n}:3:1 › {n} ───" for n in red))

        class Scripted:
            def __init__(self, llm, project_dir, check, **options):
                self.check = check

            def implement(self, node, tests, outcome, extra_context=""):
                spec = next(s for s in steps_per_spec if name(s)[:-8] == node["id"])
                written: list[str] = []
                for index, step in enumerate(steps_per_spec[spec]):
                    path = root / step.get("file", rb.APP)
                    text = path.read_text(encoding="utf-8") if path.is_file() else ""
                    for marker in step.get("remove", []):
                        text = text.replace(marker, "")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text + "".join(step.get("add", [])), encoding="utf-8")
                    written.append(step.get("file", rb.APP))
                    result = self.check()
                    outputs.setdefault(spec, []).append(result.output)
                    if result.passed:
                        return ImplementResult("passed", written, "checks green", index + 1, last_check=result.output)
                return ImplementResult("budget", written, "scripted rounds used up", len(steps_per_spec[spec]))

        lines: list[str] = []
        saved = {n: getattr(factory_module, n) for n in ("run_e2e", "build_frontend", "Implementer", "log")}
        saved_quota = LLM.quota_exhausted
        try:
            factory_module.run_e2e = run_e2e
            factory_module.build_frontend = lambda project_dir, log=print: StepResult(True, "")
            factory_module.Implementer = Scripted
            factory_module.log = lines.append
            LLM.quota_exhausted = False
            with e142.Settings(**(settings or {})):
                repaired = f.repair_failing_specs("FAILED SPEC FILES: " + ", ".join(name(s) for s in suite_red) + NL)
        finally:
            for n, value in saved.items():
                setattr(factory_module, n, value)
            LLM.quota_exhausted = saved_quota
        return repaired, (root / rb.APP).read_text(encoding="utf-8"), lines, runs, outputs

SHEET_GREEN = [rb.F1, rb.F2, G3, G4, R311]
SHEET_E2E = SHEET_GREEN + [TARGET]

@case
def a_closing_repair_that_breaks_another_green_req_spec_is_put_back():
    steps = {TARGET: [{"add": [fixed(TARGET), broken(R311)]}]}
    repaired, app, lines, runs, outputs = closing(steps, SHEET_E2E, SHEET_GREEN, [TARGET])
    assert repaired == 0 and app == "base", (repaired, app, lines)
    assert "REQ-3-1-1.spec.js" in outputs[TARGET][0] and "PASSES now" in outputs[TARGET][0], outputs
    guard_runs = [r for r in runs if len(r) > 1]

    assert len(guard_runs) >= 2 and R311 not in guard_runs[0] and set(guard_runs[1]) == {R311, G4}, guard_runs
    assert any("other green spec(s) guard it too" in l for l in lines), lines

@case
def a_repair_that_restores_what_it_broke_is_kept():
    steps = {TARGET: [{"add": [fixed(TARGET), broken(R311)]}, {"remove": [broken(R311)]}]}
    repaired, app, lines, _, outputs = closing(steps, SHEET_E2E, SHEET_GREEN, [TARGET])
    assert repaired == 1 and app == "base" + fixed(TARGET), (repaired, app, lines)
    assert len(outputs[TARGET]) == 2 and "REQ-3-1-1.spec.js" in outputs[TARGET][0] and outputs[TARGET][1].startswith("E2E PASSED")

@case
def a_spec_red_in_the_suite_does_not_guard():
    green = SHEET_GREEN[:3] + [RED_ALREADY] + SHEET_GREEN[3:]
    steps = {TARGET: [{"add": [fixed(TARGET)]}]}
    repaired, app, lines, runs, _ = closing(steps, green + [TARGET], green, [RED_ALREADY, TARGET], always_red=(name(RED_ALREADY),),
                                            file_specs={rb.APP: [RED_ALREADY, R311]})
    assert repaired == 1 and app == "base" + fixed(TARGET), (repaired, app, lines)
    guard_runs = [r for r in runs if len(r) > 1]
    assert len(guard_runs) >= 2 and R311 in guard_runs[1] and not any(RED_ALREADY in r for r in guard_runs[:2]), guard_runs

@case
def a_repair_that_changed_no_existing_file_adds_no_guard():
    import main as factory_module

    with tempfile.TemporaryDirectory() as tmp:
        f = rb.make_factory(Path(tmp), "base")
        f.green_specs, f.file_specs = list(SHEET_GREEN), {rb.APP: [R311]}
        before = f.snapshot_sources()
        (Path(tmp) / "frontend/src/New.tsx").write_text("x", encoding="utf-8")
        assert f.regression_specs(before, [TARGET], pool=list(SHEET_GREEN)) == []
        (Path(tmp) / rb.APP).write_text("changed", encoding="utf-8")
        assert f.regression_specs(before, [TARGET], pool=list(SHEET_GREEN))[0] == R311
    assert factory_module.Factory.REGRESSION_SPECS_MAX >= 1

@case
def a_closing_repair_that_only_adds_a_file_runs_no_other_green_spec():
    steps = {TARGET: [{"file": NEW, "add": [fixed(TARGET), broken(R311)]}]}
    repaired, _, lines, runs, _ = closing(steps, SHEET_E2E, SHEET_GREEN, [TARGET])
    assert repaired == 1, (repaired, lines)
    assert all(R311 not in r for r in runs if 1 < len(r) < len(SHEET_E2E)), runs
    assert not any("other green spec(s) guard it too" in l for l in lines), lines

@case
def an_unfinished_run_of_the_other_green_specs_holds_nothing():
    for kind in ("ran0", "notrun"):
        steps = {TARGET: [{"add": [fixed(TARGET)]}]}
        repaired, app, lines, runs, _ = closing(steps, SHEET_E2E, SHEET_GREEN, [TARGET], unfinished={R311: kind})
        assert repaired == 1 and app == "base" + fixed(TARGET), (kind, repaired, lines)
        assert any("did not finish or named no file" in l for l in lines), lines
        assert any(R311 in r and rb.F1 not in r for r in runs), runs

@case
def the_green_pool_is_taken_again_for_the_second_sweep():
    s1, t, s3 = (f"backend/test-e2e/REQ-6-{i}.spec.js" for i in (1, 2, 3))
    e2e = SHEET_GREEN + [s1, t, s3]
    steps = {s1: [{"file": NEW, "add": [fixed(s1), broken(t)]}],
             t: [{"file": NEW, "remove": [broken(t)], "add": [broken(s3)]}]}
    repaired, app, lines, _, outputs = closing(steps, e2e, SHEET_GREEN, [s1, s3], also_fixed_by={s3: fixed(s1)},
                                               file_specs={NEW: [s1]})
    assert repaired == 1, (repaired, lines, outputs)
    assert any("sweep 2" in l for l in lines) and name(s3) in outputs[t][0], (lines, outputs)

@case
def the_pool_replaces_green_specs_only_when_given():
    import main as factory_module

    with tempfile.TemporaryDirectory() as tmp:
        f = rb.make_factory(Path(tmp), "base")
        f.green_specs = [rb.F1, rb.F2, G3, G4, R311]
        f.file_specs = {rb.APP: [R311, G4]}
        before = f.snapshot_sources()
        (Path(tmp) / rb.APP).write_text("changed", encoding="utf-8")
        old = [s for s in dict.fromkeys([s for s in f.green_specs if s in {R311, G4}] + f.green_specs[:3] + f.green_specs) if s != TARGET]
        assert f.regression_specs(before, [TARGET]) == old == [G4, R311, rb.F1, rb.F2, G3]
        pool = [G3, R311, RED_ALREADY]
        assert f.regression_specs(before, [TARGET], pool=pool) == [R311, G3, RED_ALREADY]
        assert f.regression_specs(before, [R311], pool=pool) == [G3, RED_ALREADY]
        saved = factory_module.Factory.REGRESSION_SPECS_MAX
        try:
            factory_module.Factory.REGRESSION_SPECS_MAX = 1
            assert f.regression_specs(before, [TARGET], pool=pool) == [R311]
        finally:
            factory_module.Factory.REGRESSION_SPECS_MAX = saved

@case
def a_spec_repaired_earlier_in_the_sweep_guards_beyond_the_cap_of_eight():
    reds = [f"backend/test-e2e/REQ-6-{i}.spec.js" for i in range(1, 7)]
    e2e = SHEET_GREEN + reds + [TARGET]
    steps = {spec: [{"add": [fixed(spec)]}] for spec in reds}
    steps[TARGET] = [{"add": [fixed(TARGET), broken(reds[-1])]}]
    repaired, app, lines, _, _ = closing(steps, e2e, SHEET_GREEN, reds + [TARGET], file_specs={rb.APP: [reds[-1]]},
                                         settings={})
    assert repaired == 6 and fixed(TARGET) not in app and broken(reds[-1]) not in app, (repaired, app, lines)

@case
def the_setting_off_keeps_the_old_guard():
    steps = {TARGET: [{"add": [fixed(TARGET), broken(R311)]}]}
    repaired, app, lines, runs, _ = closing(steps, SHEET_E2E, SHEET_GREEN, [TARGET], settings={"closing_regression_guard": False})
    assert repaired == 1 and broken(R311) in app, (repaired, app, lines)

    assert all(R311 not in r for r in runs if 1 < len(r) < len(SHEET_E2E)), runs
    assert not any("other green spec(s) guard it too" in l for l in lines), lines

@case
def the_guard_is_chosen_inside_the_check_from_the_state_before_the_repair():
    import inspect

    import main as factory_module

    source = inspect.getsource(factory_module.Factory.repair_failing_specs)
    assert source.count("before = self.snapshot_sources()") == 1
    assert source.index("before = self.snapshot_sources()") < source.index("def check(")
    assert "self.regression_specs(before, target, pool=pool)" in source
    assert "fresh_green_names(self.e2e_specs, False, suite_output)" in source
    assert "self.restore_sources(before)" in source

HALF = ("=== FILE: " + nf.PAGE + " ===" + NL + "export default function HomePage() {" + NL + ("  // step" + NL) * 600, "length")
DROPPED = (HALF[0], "")

def writes(model) -> list[str]:
    return [t for t in model.tags() if t.startswith(("gen", "repair"))]

@case
def a_cut_answer_without_a_complete_file_costs_no_round():
    from agent.codegen import CUT_FREE

    assert CUT_FREE == 2
    model = nf.ScriptedModel([nf.FIRST, HALF, nf.FIX])
    result, lines = nf.implement(model, [nf.red(), nf.green()], max_rounds=1)
    assert writes(model) == ["gen0", "repair1", "repair1"], model.tags()
    assert result.status == "passed" and result.test_runs == 2, (result.status, result.test_runs, lines)
    assert nf.DECISION in model.prompts("repair1")[-1]
    assert any("cut off (finish=length) before any file was complete" in l for l in lines), lines

@case
def a_broken_stream_without_a_complete_file_costs_no_round():
    model = nf.ScriptedModel([nf.FIRST, DROPPED, nf.FIX])
    result, lines = nf.implement(model, [nf.red(), nf.green()], max_rounds=1)
    assert writes(model) == ["gen0", "repair1", "repair1"] and result.status == "passed", (model.tags(), lines)
    assert any("cut off (finish=none)" in l for l in lines), lines

@case
def free_cut_retries_are_capped():
    from agent.codegen import CUT_FREE

    model = nf.ScriptedModel([nf.FIRST, HALF])
    result, _ = nf.implement(model, [nf.red()] * 10, max_rounds=2)
    assert writes(model) == ["gen0"] + ["repair1"] * (CUT_FREE + 1) + ["repair2"], writes(model)
    assert result.status == "gave_up", result.status
    model = nf.ScriptedModel([nf.FIRST, DROPPED])
    result, _ = nf.implement(model, [nf.red()] * 10, max_rounds=1)
    assert len(writes(model)) == 1 + 1 + CUT_FREE and result.status == "gave_up", (writes(model), result.status)

@case
def the_complete_files_of_a_cut_answer_are_written_and_the_half_one_is_not():
    from agent.codegen import CodegenImplementer

    other = "frontend/src/Other.tsx"
    cut = (nf.FIX[0] + NL + "=== FILE: " + other + " ===" + NL + "export const x = 1;" + NL + "// the rest was cut", "length")
    model = nf.ScriptedModel([nf.FIRST, cut])
    seen: list[tuple[bool, str]] = []
    CodegenImplementer.repair_mode_broken = False
    lines: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = nf.project(tmp)
        results = iter([nf.red(), nf.green()])

        def check():
            page = root / nf.PAGE
            seen.append(((root / other).exists(), page.read_text(encoding="utf-8") if page.is_file() else ""))
            return next(results)
        implementer = CodegenImplementer(model, root, check, max_rounds=1, extra_rounds=0, log=lines.append)
        node = {"id": "REQ-9", "name": "n", "description": "Open the catalogue through the link named `Books`."}
        result = implementer.implement(node, {nf.SPEC: "await click(page, { role: 'link', name: 'Books' });"}, nf.red())
    assert result.status == "passed" and writes(model) == ["gen0", "repair1"], (result.status, model.tags())
    assert seen[-1][0] is False and "<h1>Books</h1>" in seen[-1][1], seen
    assert not any("before any file was complete" in l for l in lines), lines

@case
def cut_answers_and_cut_off_prose_have_separate_allowances():
    prose = (nf.analysis(4000), "aborted")
    model = nf.ScriptedModel([nf.FIRST, prose, prose, HALF, HALF, nf.FIX])
    result, _ = nf.implement(model, [nf.red(), nf.green()], max_rounds=1)
    assert writes(model) == ["gen0"] + ["repair1"] * 5 and result.status == "passed", (model.tags(), result.status)

@case
def a_cut_first_answer_is_asked_again_without_thinking():
    class Recording(nf.ScriptedModel):
        def chat(self, messages, **options):
            self.thinking = getattr(self, "thinking", []) + [(options.get("tag", ""), options.get("thinking"))]
            return super().chat(messages, **options)
    model = Recording([HALF, nf.FIRST, nf.FIX])
    result, _ = nf.implement(model, [nf.red(), nf.green()], max_rounds=1)
    assert writes(model) == ["gen0", "gen0", "repair1"] and result.status == "passed", (model.tags(), result.status)
    gen = [flag for tag, flag in model.thinking if tag.endswith(":gen0")]
    assert gen == [True, False], model.thinking

@case
def an_empty_cut_repair_answer_in_the_last_round_is_asked_again():
    from agent.codegen import CodegenImplementer

    try:
        model = nf.ScriptedModel([nf.FIRST, ("", "length"), nf.FIX])
        result, _ = nf.implement(model, [nf.red(), nf.green()], max_rounds=1)
        assert CodegenImplementer.repair_mode_broken and result.status == "passed", (model.tags(), result.status)
        assert writes(model) == ["gen0", "repair1", "repair1"], model.tags()
    finally:
        CodegenImplementer.repair_mode_broken = False

@case
def a_finished_answer_without_files_still_costs_its_round():
    model = nf.ScriptedModel([nf.FIRST, ("I think the heading is fine.", "stop")])
    result, _ = nf.implement(model, [nf.red()] * 5, max_rounds=1)
    assert writes(model) == ["gen0", "repair1"] and result.status == "gave_up", (model.tags(), result.status)

@case
def the_batch_deadline_still_ends_free_retries():
    from agent import clock
    from agent.codegen import CodegenImplementer

    now = {"t": 1000.0}

    class Timed(nf.ScriptedModel):
        def chat(self, messages, **options):
            now["t"] += 400.0
            return super().chat(messages, **options)
    model = Timed([nf.FIRST, HALF, nf.FIX])
    saved = clock.now
    clock.now = lambda: now["t"]
    CodegenImplementer.repair_mode_broken = False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = nf.project(tmp)
            results = iter([nf.red(), nf.green()])
            implementer = CodegenImplementer(model, root, lambda: next(results), max_rounds=4, extra_rounds=0, deadline=1500.0,
                                             log=lambda line: None)
            node = {"id": "REQ-9", "name": "n", "description": "d"}
            result = implementer.implement(node, {nf.SPEC: "x"}, nf.red())
    finally:
        clock.now = saved
    assert result.status == "budget" and "deadline" in result.detail, (result.status, result.detail)
    assert writes(model) == ["gen0", "repair1"], model.tags()

def main() -> int:
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    started = time.time()
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed}/{len(CASES)} closeguard cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())

@case
def a_guard_spec_red_in_this_sweep_does_not_hold_every_repair():

    import inspect
    import main

    source = inspect.getsource(main)
    assert "red_guard = [s for s in guard if Path(s).name in names and s not in fixed_now]" in source
    assert "guard = [s for s in guard if s not in red_guard]" in source
