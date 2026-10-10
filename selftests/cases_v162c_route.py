from __future__ import annotations

import re
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
NAME = Path(rb.OWN).name
T1, T2, T3 = "REQ-9: first", "REQ-9: second relies", "REQ-9: app part"
STATIC_MESSAGE = (f'{rb.OWN}: test "{T2}" expects "FY26 Ledger" SEEN, which only test "{T1}" enters' + NL
                  + "Each test runs ALONE on a fresh server and fresh data.")
PROBE_MESSAGE = f'Test "{T2}" ({NAME} line 20) fails when it runs alone but passes after the earlier tests of its file ran'
SPEC_ROUTE = re.compile(r"(?m)^(?:The acceptance spec contradicts the requirement text:|\s*SPEC:)")
NOTE = "NOT AN APPLICATION FAILURE"

def case(function):
    CASES.append(function)
    return function

def spec_text(markers: str, titles=(T1, T2, T3)) -> str:
    return "".join(f"test('{t}', async () => {{}});" + NL for t in titles) + "// " + markers + NL

def red_titles(spec: str, sources: str) -> list[str]:
    red = []
    titles = re.findall(r"test\('([^']+)'", spec)
    for title in titles:
        if title == T2 and "DEPENDS" in spec and "SEEDFIX" not in sources:
            red.append(title)
        elif title == T3 and "OWN-OK" not in sources:
            red.append(title)
        elif title.startswith("REQ-9: many") and "SEEDFIX" not in sources:
            red.append(title)
    return red

def run(spec: str, script: list[dict], settings: dict | None = None, repaired: str | None = None, static=None, probe=None,
        prep=None):
    import main as factory_module
    from agent import clock, testrun, verify
    from agent.test_author import AuthoredFile
    from agent.testrun import TestOutcome
    from agent.verify import StepResult

    lines: list[str] = []
    seen: list = []
    offset = [0.0]
    repairs: list[str] = []
    static_calls: list = []
    probe_calls: list = []
    v145a.CHECKS.clear()
    names = ("run_e2e", "build_frontend", "Implementer", "run_node_tests", "static_test_defects", "parse_defects_in_output",
             "lint_application", "log")
    saved = {name: getattr(factory_module, name) for name in names}
    saved_static, saved_probe = testrun.cross_test_dependency_defects, verify.dependency_probe
    saved_now = clock.now
    base = {"audit_enabled": False, "in_batch_restart": False}
    with v145a.Settings(**dict(base, **(settings or {}))), tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = rb.World(root)
        f = rb.make_factory(root, "base")
        f.author_tests = lambda node, prefix, contract: [AuthoredFile(path=rb.OWN, content=spec)]

        def repair_tests(node, prefix, files, problem, contract=""):
            repairs.append(problem)
            return [AuthoredFile(path=rb.OWN, content=repaired if repaired is not None else spec_text("repaired"))]
        f.repair_tests = repair_tests
        if prep:
            prep(f)

        def run_e2e(project_dir, specs, log=print):
            if list(specs) != [rb.OWN]:
                return world.run_e2e(project_dir, specs, log=log)
            text = (root / rb.OWN).read_text(encoding="utf-8")
            red = red_titles(text, world.sources())
            ran = len(re.findall(r"test\('", text))
            if not red:
                return StepResult(True, f"  {ran} passed", ran=ran, failed_tests=[])
            numbered = re.findall(r"test\('([^']+)'", text)

            return StepResult(False, rb.spec_output(NAME, red), ran=ran,
                              failed_tests=[(NAME, 10 * i, 1, t) for i, t in enumerate(red, 1)] if numbered else [])

        def fake_static(project_dir, test_files, batch, red_titles=None):
            text = "".join((Path(project_dir) / p).read_text(encoding="utf-8") for p in test_files)
            static_calls.append((list(test_files), list(red_titles or []), batch.get("id") if batch else None))
            return static(text, list(red_titles or [])) if static else ""

        def fake_probe(project_dir, spec_path, red_location, log=print, time_limit=120.0, confirm_alone=True, until=None):
            text = (Path(project_dir) / spec_path).read_text(encoding="utf-8")
            call = {"spec": spec_path, "entry": tuple(red_location), "time_limit": time_limit, "until": until,
                    "offset": offset}
            probe_calls.append(call)
            return probe(text, tuple(red_location), call) if probe else None

        try:
            frozen = time.time()
            clock.now = lambda: frozen + offset[0]
            f.started = frozen
            factory_module.run_e2e, factory_module.build_frontend = run_e2e, world.build_frontend
            factory_module.Implementer = v145a.fake_implementer(script, seen, offset)
            factory_module.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
            factory_module.static_test_defects = lambda project_dir, files, **kw: ""
            factory_module.parse_defects_in_output = lambda output, files: ""
            factory_module.lint_application = lambda project_dir, native: ""
            factory_module.log = lines.append
            testrun.cross_test_dependency_defects, verify.dependency_probe = fake_static, fake_probe
            results = [f.process_node(dict(v145a.NODE), 1)]
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
            testrun.cross_test_dependency_defects, verify.dependency_probe = saved_static, saved_probe
            clock.now = saved_now
    assert not script, f"scripted steps left over: {script}"
    return {"results": results, "lines": lines, "checks": list(v145a.CHECKS), "repairs": repairs, "static_calls": static_calls,
            "probe_calls": probe_calls, "seen": seen, "f": f}

def static_t2(text: str, titles: list[str]) -> str:
    return STATIC_MESSAGE if "STATIC" in text and any(T2 in t for t in titles) else ""

def probe_t2(text: str, entry: tuple, call: dict):
    if "PROBE" in text and entry[3] == T2:
        return {"title": T2, "line": entry[1], "predecessors": [T1], "message": PROBE_MESSAGE}
    return None

def crossdep_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if str(line).startswith("[crossdep]")]

def shows_t2_failure(output: str) -> bool:
    return f"› {T2}" in output

@case
def a_static_verdict_goes_to_the_test_author_and_not_to_the_implementer():
    stops: list = []

    script = [{"do": [("write", {rb.APP: "base OWN-OK"}), ("call", lambda impl: stops.append(getattr(impl.check(), "stop", ""))),
                      ("check",)]}]
    out = run(spec_text("DEPENDS STATIC PROBE"), script, static=static_t2, probe=probe_t2)
    assert out["results"] == [True], out["lines"]
    assert len(stops) == 1 and "handed to the test author" in stops[0], stops
    assert len(out["repairs"]) == 1 and SPEC_ROUTE.search(out["repairs"][0]), out["repairs"]
    assert "FY26 Ledger" in out["repairs"][0] and f'{NAME} line 10, test "{T2}" (static check)' in out["repairs"][0], out["repairs"]
    assert f"[crossdep] {NAME}:10 static: -> test author: {STATIC_MESSAGE.splitlines()[0][:300]}" in out["lines"], crossdep_lines(out["lines"])
    assert any("stopped on a cross-test dependency" in str(l) for l in out["lines"]), out["lines"]
    assert any("test defect #1 reported: SPEC:" in str(l) for l in out["lines"]), out["lines"]
    first = out["checks"][0]
    assert not shows_t2_failure(first) and NOTE in first and "goes to the test author now" in first, first
    assert out["static_calls"][0] == ([rb.OWN], [T2], "REQ-9"), out["static_calls"]
    assert out["probe_calls"] == [], out["probe_calls"]
    assert rb.OWN in out["f"].revised_specs

@case
def a_probe_verdict_goes_to_the_test_author():
    script = [{"do": [("write", {rb.APP: "base OWN-OK"}), ("check",)]}]
    out = run(spec_text("DEPENDS PROBE"), script, static=static_t2, probe=probe_t2)
    assert out["results"] == [True], out["lines"]
    assert len(out["repairs"]) == 1 and SPEC_ROUTE.search(out["repairs"][0]) and PROBE_MESSAGE in out["repairs"][0], out["repairs"]
    assert f"[crossdep] {NAME}:10 probe: -> test author: {PROBE_MESSAGE}" in out["lines"], crossdep_lines(out["lines"])
    assert len(out["probe_calls"]) == 1, out["probe_calls"]
    call = out["probe_calls"][0]
    deadline = out["seen"][0].options["deadline"]
    assert call["spec"] == rb.OWN and call["entry"] == (NAME, 10, 1, T2), call
    assert 0 < call["time_limit"] <= 120.0 and call["until"] == deadline, (call, deadline)
    assert not shows_t2_failure(out["checks"][0]) and NOTE in out["checks"][0], out["checks"][0]

SEEDFIX_SCRIPT = [{"do": [("write", {rb.APP: "base OWN-OK"}), ("check",), ("write", {rb.APP: "base OWN-OK SEEDFIX"}), ("check",)]}]

@case
def no_probe_verdict_leaves_the_red_test_to_the_implementer():
    out = run(spec_text("DEPENDS"), [dict(step) for step in SEEDFIX_SCRIPT], static=static_t2, probe=probe_t2)
    assert out["results"] == [True], out["lines"]
    assert out["repairs"] == [] and crossdep_lines(out["lines"]) == [], (out["repairs"], crossdep_lines(out["lines"]))
    assert shows_t2_failure(out["checks"][0]) and NOTE not in out["checks"][0], out["checks"][0]
    assert len(out["static_calls"]) == 1 and len(out["probe_calls"]) == 1, (out["static_calls"], out["probe_calls"])

@case
def both_settings_off_change_nothing():
    off = {"v162b_crossdep": False, "v162b_dependency_probe": False}
    out = run(spec_text("DEPENDS STATIC PROBE"), [dict(step) for step in SEEDFIX_SCRIPT], settings=off, static=static_t2, probe=probe_t2)
    assert out["results"] == [True] and out["repairs"] == [], (out["results"], out["repairs"])
    assert out["static_calls"] == [] and out["probe_calls"] == [] and crossdep_lines(out["lines"]) == [], out["lines"]
    assert shows_t2_failure(out["checks"][0]) and NOTE not in out["checks"][0], out["checks"][0]

    plain = run(spec_text("DEPENDS STATIC PROBE"), [dict(step) for step in SEEDFIX_SCRIPT], static=None, probe=None)
    assert out["checks"] == plain["checks"], (out["checks"], plain["checks"])
    one_off = run(spec_text("DEPENDS STATIC PROBE"), [dict(step) for step in SEEDFIX_SCRIPT],
                  settings={"v162b_crossdep": False}, static=static_t2, probe=probe_t2)

    assert one_off["static_calls"] == [] and len(one_off["probe_calls"]) == 1, one_off["static_calls"]
    assert [l for l in crossdep_lines(one_off["lines"]) if " probe: -> test author: " in l], one_off["lines"]

MANY = [T1] + [f"REQ-9: many {i}" for i in range(1, 6)]

@case
def at_most_three_probes_per_check_and_an_unchanged_state_is_not_probed_again():
    marks: list[int] = []
    calls: list = []
    script = [{"do": [("write", {rb.APP: "base OWN-OK"}), ("check",), ("call", lambda impl: marks.append(len(calls))),
                      ("check",), ("call", lambda impl: marks.append(len(calls))),
                      ("write", {rb.APP: "base OWN-OK v2"}), ("check",), ("call", lambda impl: marks.append(len(calls))),
                      ("write", {rb.APP: "base OWN-OK SEEDFIX"}), ("check",), ("call", lambda impl: marks.append(len(calls)))]}]

    def probe(text, entry, call):
        calls.append(call)
        return None
    out = run(spec_text("MANY", titles=MANY), script, probe=probe)
    assert out["results"] == [True], out["lines"]

    assert marks == [3, 5, 8, 8], marks
    deadline = out["seen"][0].options["deadline"]
    assert all(0 < c["time_limit"] <= 120.0 and c["until"] == deadline for c in calls), calls
    assert out["repairs"] == [] and crossdep_lines(out["lines"]) == [], out["repairs"]

@case
def the_probe_time_of_one_check_is_capped():
    calls: list = []

    def probe(text, entry, call):
        calls.append(call)
        call["offset"][0] += 25.0
        return None

    def prep(f):
        f.CROSSDEP_CHECK_SEC = 50.0
    script = [{"do": [("write", {rb.APP: "base OWN-OK"}), ("check",), ("write", {rb.APP: "base OWN-OK SEEDFIX"}), ("check",)]}]
    out = run(spec_text("MANY", titles=MANY), script, probe=probe, prep=prep)
    assert out["results"] == [True], out["lines"]
    assert [c["time_limit"] for c in calls] == [50.0], calls

@case
def an_all_red_run_is_not_probed():
    calls: list = []
    titles = ["REQ-9: many 1", "REQ-9: many 2"]

    def probe(text, entry, call):
        calls.append(call)
        return None
    script = [{"do": [("write", {rb.APP: "base OWN-OK"}), ("check",), ("write", {rb.APP: "base OWN-OK SEEDFIX"}), ("check",)]}]
    out = run(spec_text("MANY", titles=titles), script, probe=probe)
    assert out["results"] == [True] and calls == [], (out["results"], calls)

@case
def with_no_test_repair_left_the_verdict_stays_out_of_the_implementers_text():
    script = [{"do": [("write", {rb.APP: "base OWN-OK"}), ("check",)]},
              {"do": [("check",)]},
              {"do": [("check",)], "status": "budget"}]
    out = run(spec_text("DEPENDS STATIC"), script, static=static_t2, repaired=spec_text("DEPENDS STATIC again"))
    assert len(out["repairs"]) == 2, out["repairs"]
    assert out["results"] == [False], out["lines"]
    assert not any("unit phase: test_defect" in str(l) for l in out["lines"]), out["lines"]
    kept = [l for l in crossdep_lines(out["lines"]) if "no test repair left; kept from the implementer" in l]
    assert kept, crossdep_lines(out["lines"])
    last = out["checks"][-1]
    assert not shows_t2_failure(last) and NOTE in last and "test author now" not in last, last

@case
def the_real_static_check_finds_the_fy26_shape_through_crossdep_verdicts():
    import cases_v162b_crossdep as c162b
    import main as factory_module
    from agent.verify import StepResult

    red = "REQ-1-2-2: reject an empty workbook name - Scenario 2"
    with tempfile.TemporaryDirectory() as tmp, v145a.Settings(v162b_dependency_probe=False):
        root = Path(tmp)
        path = root / c162b.REL
        path.parent.mkdir(parents=True)
        path.write_text(c162b.FY26, encoding="utf-8")
        f = rb.make_factory(root, "base")
        run = StepResult(False, "red", ran=2, failed_tests=[(Path(c162b.REL).name, 12, 1, red)])
        found = f.crossdep_verdicts(c162b.batch(), run, [c162b.REL], None)
        other = f.crossdep_verdicts(c162b.batch(), run, ["backend/test-e2e/OTHER.spec.js"], None)
    assert len(found) == 1 and found[0]["kind"] == "static" and "FY26 Procurement Ledger" in found[0]["message"], found
    assert other == [], other
    assert factory_module.crossdep_problem(found, "out").startswith("SPEC: ")

ISOLATED = NL.join([
    "FAILED SPEC FILES: REQ-9.spec.js",
    "REQ-9.spec.js: every test ran alone, on a fresh server with fresh data of its own",
    "## REQ-9.spec.js line 20: alone on a fresh server with fresh data",
    "  1) [chromium] › test-e2e/REQ-9.spec.js:20:3 › REQ-9 › second relies ───",
    "    Error: expected FY26 Ledger",
    "",
    "  1 failed",
    "    [chromium] › test-e2e/REQ-9.spec.js:20:3 › REQ-9 › second relies ───",
    "",
    "## REQ-9.spec.js line 30: alone on a fresh server with fresh data",
    "  1) [chromium] › test-e2e/REQ-9.spec.js:30:3 › REQ-9 › app part ───",
    "    Error: app part missing",
    "",
    "  1 failed",
    "    [chromium] › test-e2e/REQ-9.spec.js:30:3 › REQ-9 › app part ───",
    "",
    "  2 failed",
    "    test-e2e/REQ-9.spec.js:20:3 › REQ-9 › second relies ───",
    "    test-e2e/REQ-9.spec.js:30:3 › REQ-9 › app part ───",
    "  1 passed",
])

@case
def drop_red_tests_takes_out_only_the_named_tests():
    import main as factory_module

    shown = factory_module.drop_red_tests(ISOLATED, [{"name": NAME, "line": 20}])
    assert "second relies" not in shown and "FY26" not in shown, shown
    assert "app part missing" in shown and "REQ-9.spec.js:30:3" in shown and "## REQ-9.spec.js line 30" in shown, shown
    assert factory_module.drop_red_tests(ISOLATED, []) == ISOLATED
    reporter = rb.spec_output(NAME, [T1, T2])
    shown = factory_module.drop_red_tests(reporter, [{"name": NAME, "line": 20}])
    assert T2 not in shown and T1 in shown and "Username format" in shown, shown

@case
def repair_tests_sends_the_spec_problem_to_the_acceptance_author():
    import main as factory_module
    from agent.test_author import AuthoredFile

    asked: list = []
    spec = AuthoredFile(path=rb.OWN, content="const h = require('../e2e-support/outsider');" + NL)
    unit = AuthoredFile(path="backend/tests/REQ-9.test.js", content="test" + NL)
    problem = factory_module.crossdep_problem([{"name": NAME, "line": 20, "title": T2, "kind": "probe", "message": PROBE_MESSAGE}],
                                              "backend/tests/REQ-9.test.js advisory line")
    saved = factory_module.author_acceptance
    with tempfile.TemporaryDirectory() as tmp:
        f = rb.make_factory(Path(tmp), "base")

        class Author:
            def repair(self, node, prefix, files, problem, contract=""):
                return files
        f.author = Author()
        f.recheck_rewritten_spec = lambda node, prefix, rewritten: rewritten
        try:
            factory_module.author_acceptance = lambda llm, node, prefix, problem="", current="", log=print: (
                asked.append(problem) or AuthoredFile(path=rb.OWN, content=current + "// fixed" + NL))
            out = f.repair_tests({"id": "REQ-9"}, "REQ-9", [unit, spec], problem)
        finally:
            factory_module.author_acceptance = saved
    assert asked == [problem], asked
    assert [x.content.endswith("// fixed" + NL) for x in out if x.path == rb.OWN] == [True], out

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
    print(f"{len(CASES) - failed}/{len(CASES)} v162c cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
