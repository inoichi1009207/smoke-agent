from __future__ import annotations

import inspect
import json
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_engine142 as e142
import cases_p141s3 as s3
import cases_rollback as rb

NL = chr(10)
CASES = []
Settings = e142.Settings

F4 = s3.F4
F4_S1, F4_S2 = "REQ-4-2-3: Scenario 1", "REQ-4-2-3: Scenario 2"
BREAK_BOTH = rb.brk(F4_S1) + " " + rb.brk(F4_S2)

def case(function):
    CASES.append(function)
    return function

def counting_run_e2e(project_dir, specs, log=print, **kwargs):
    result = rb.World(Path(project_dir)).run_e2e(project_dir, specs, log=log)
    if list(specs) == [rb.OWN]:
        result.ran = 1
    return result

def two_test_f4(f):
    (f.project_dir / F4).parent.mkdir(parents=True, exist_ok=True)
    (f.project_dir / F4).write_text(f"test('{F4_S1}', async () => {{}});{NL}test('{F4_S2}', async () => {{}});{NL}", encoding="utf-8")

@case
def a_batch_that_broke_more_earlier_tests_than_its_own_is_put_back_after_the_targeted_repair_fails():
    f, lines, after, passed, calls = s3.run_debt_batch(
        [{"write": {rb.APP: "OWN-OK " + BREAK_BOTH}}, {"write": {rb.APP: "OWN-OK other " + BREAK_BOTH}}],
        tweak=two_test_f4, patch={"run_e2e": counting_run_e2e})
    assert any("the targeted regression repair is not kept" in l for l in lines), lines
    assert after == "APP" and passed is False, (after, lines)
    assert any("put back: its own spec(s) pass 1 test(s) but it broke 2 test(s) of earlier specs" in l for l in lines), lines
    assert F4 in f.green_specs and f.regression_debts == [], (f.green_specs, f.regression_debts)
    assert not (f.__dict__.get("debt_specs") or {}), f.__dict__.get("debt_specs")
    assert f.report["nodes"]["REQ-9"]["status"] == "budget" and f.report["nodes"]["REQ-9"]["e2e"] == "failed"

@case
def an_even_or_unknown_cost_keeps_the_batch_with_its_debt_booked():

    f, lines, after, passed, calls = s3.run_debt_batch([dict(s3.KEPT), {"write": {rb.APP: "OWN-OK other " + s3.F4_BREAK}}],
                                                       patch={"run_e2e": counting_run_e2e})
    assert passed and after == "OWN-OK " + s3.F4_BREAK and f.regression_debts == ["REQ-9"], (after, lines)
    assert f.debt_specs[F4] == {"by": "REQ-9", "failed": [F4_S1]}, f.debt_specs
    assert F4 not in f.green_specs

    f, lines, after, passed, calls = s3.run_debt_batch(
        [{"write": {rb.APP: "OWN-OK " + BREAK_BOTH}}, {"write": {rb.APP: "OWN-OK other " + BREAK_BOTH}}], tweak=two_test_f4)
    assert passed and after == "OWN-OK " + BREAK_BOTH and f.regression_debts == ["REQ-9"], (after, lines)
    assert f.debt_specs[F4]["failed"] == [F4_S1, F4_S2]

    with Settings(regression_net_rollback=False):
        f, lines, after, passed, calls = s3.run_debt_batch(
            [{"write": {rb.APP: "OWN-OK " + BREAK_BOTH}}, {"write": {rb.APP: "OWN-OK other " + BREAK_BOTH}}],
            tweak=two_test_f4, patch={"run_e2e": counting_run_e2e})
    assert passed and f.regression_debts == ["REQ-9"] and not any("put back: its own spec" in l for l in lines), lines

@case
def a_green_targeted_repair_books_no_debt():
    f, lines, after, passed, calls = s3.run_debt_batch([dict(s3.KEPT), {"write": {rb.APP: "OWN-OK fixed"}}],
                                                       patch={"run_e2e": counting_run_e2e})
    assert passed and after == "OWN-OK fixed" and f.regression_debts == [], lines
    assert not (f.__dict__.get("debt_specs") or {}) and F4 not in (f.__dict__.get("partial_green") or {})

@case
def the_net_loss_needs_positive_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        f = rb.make_factory(Path(tmp), "APP")
        output = rb.spec_output("REQ-4-2-3.spec.js", [F4_S1, F4_S2])
        evidence = f.debt_evidence(output)
        assert f.debt_net_loss(1, {"REQ-4-2-3.spec.js"}, evidence) == (2, 1)
        assert f.debt_net_loss(-1, {"REQ-4-2-3.spec.js"}, evidence) is None
        assert f.debt_net_loss(1, {"REQ-4-2-3.spec.js", "REQ-5.spec.js"}, evidence) is None
        cut = f.debt_evidence(output[:200] + NL + "[middle of the output omitted]" + NL + output[-200:])
        assert f.debt_net_loss(1, {"REQ-4-2-3.spec.js"}, cut) is None
        (Path(tmp) / rb.APP).write_text("APP changed", encoding="utf-8")
        assert f.debt_net_loss(1, {"REQ-4-2-3.spec.js"}, evidence) is None

def fake_groups(project_dir, spec, log=print, **kwargs):
    lines = (Path(project_dir) / spec).read_text(encoding="utf-8").splitlines()
    return [(n, [(n, 1, line.split("'")[1])]) for n, line in enumerate(lines, 1) if line.startswith("test('")]

@case
def a_broken_spec_keeps_its_passing_tests_in_the_regression_checks():
    from agent import verify

    saved = verify.test_groups
    try:
        verify.test_groups = fake_groups
        with tempfile.TemporaryDirectory() as tmp:
            f = rb.make_factory(Path(tmp), "APP")
            f.e2e_specs, f.partial_green = [rb.F1, rb.F2, F4], {}
            f.file_specs = {rb.APP: [rb.F1, F4]}
            two_test_f4(f)
            f.track_debt_specs("REQ-9", {"REQ-4-2-3.spec.js"}, f.debt_evidence(rb.spec_output("REQ-4-2-3.spec.js", [F4_S2])))
            entry = f.partial_green[F4]
            assert entry["red"] == [F4_S2] and entry["titles"] is None and entry["debt"] == "REQ-9" and rb.APP in entry["written"], entry

            before = f.snapshot_sources()
            (Path(tmp) / rb.APP).write_text("APP later", encoding="utf-8")
            chosen = f.partial_green_groups(before, [rb.OWN])
            assert chosen == [(F4, [(1, [(1, 1, F4_S1)])])], chosen

            f.partial_green = {}
            prior = {F4: {"batch": "REQ-4-2-3", "titles": [F4_S1, F4_S2], "sha1": "x", "written": [rb.APP]}}
            f.track_debt_specs("REQ-10", {"REQ-4-2-3.spec.js"}, f.debt_evidence(rb.spec_output("REQ-4-2-3.spec.js", [F4_S2])), prior)
            assert f.partial_green[F4]["titles"] == [F4_S1] and f.partial_green[F4]["batch"] == "REQ-4-2-3", f.partial_green

            f.partial_green, f.debt_specs = {}, {}
            evidence = f.debt_evidence(rb.spec_output("REQ-4-2-3.spec.js", [F4_S2]))
            (Path(tmp) / rb.APP).write_text("APP again", encoding="utf-8")
            f.track_debt_specs("REQ-11", {"REQ-4-2-3.spec.js"}, evidence)
            assert f.debt_specs[F4] == {"by": "REQ-11", "failed": []} and F4 not in f.partial_green
    finally:
        verify.test_groups = saved

@case
def the_debt_ledger_survives_a_resume():
    with tempfile.TemporaryDirectory() as tmp:
        f = rb.make_factory(Path(tmp), "APP")
        for name in f.CHECKPOINT_FIELDS:
            if not hasattr(f, name):
                setattr(f, name, [] if name.endswith("s") else {})
        f.description_pass, f.contract, f.partial_green = {}, "", {}
        f.debt_specs = {F4: {"by": "REQ-9", "failed": [F4_S1]}}
        f.llm = None
        f.save_checkpoint("digest", ["REQ-9"], "")
        g = rb.make_factory(Path(tmp), "APP")
        import os

        os.environ.pop("AGENT_RESUME", None)
        done = g.load_checkpoint("digest")
        assert done == ["REQ-9"] and g.debt_specs == {F4: {"by": "REQ-9", "failed": [F4_S1]}}, g.__dict__.get("debt_specs")

def closing_world(root: Path, script: list[dict], calls: list[dict]):
    from agent.codegen import ImplementResult

    class Scripted:
        def __init__(self, llm, project_dir, check, **options):
            self.project_dir, self.check, self.options = project_dir, check, options

        def implement(self, node, tests, outcome, extra_context=""):
            calls.append({"node": node.get("id"), "first": outcome.output, "context": extra_context, "options": self.options})
            step = script.pop(0)
            (self.project_dir / rb.APP).write_text(step["app"], encoding="utf-8")
            result = self.check()
            return ImplementResult("passed" if result.passed else "budget", [rb.APP], "scripted", 1)

    return Scripted

def closing_factory(root: Path, app: str):
    f = rb.make_factory(root, app)
    f.e2e_specs = [rb.F1, rb.F2, F4]
    f.green_specs = [rb.F1, rb.F2]
    f.file_specs = {rb.APP: [rb.F1]}
    for spec in f.e2e_specs:
        (root / spec).parent.mkdir(parents=True, exist_ok=True)
        (root / spec).write_text("test('x', async () => {});" + NL, encoding="utf-8")
    f.spec_context = {s: {"node": {"id": Path(s).name[:-8]}, "contract": "", "relevant": [], "tests": {}, "written": [], "members": []}
                      for s in f.e2e_specs}
    f.debt_specs = {F4: {"by": "REQ-9", "failed": [F4_S1]}}
    return f

class Patched:
    def __init__(self, script, calls, lines):
        self.script, self.calls, self.lines = script, calls, lines

    def __enter__(self):
        import main as m

        self.saved = {n: getattr(m, n) for n in ("run_e2e", "build_frontend", "Implementer", "log")}
        self.quota = m.LLM.quota_exhausted
        m.run_e2e = lambda project_dir, specs, log=print, **kw: rb.World(Path(project_dir)).run_e2e(project_dir, specs, log=log)
        m.build_frontend = lambda project_dir, log=print: rb.World(Path(project_dir)).build_frontend(project_dir)
        m.Implementer = closing_world(None, self.script, self.calls)
        m.log = self.lines.append
        m.LLM.quota_exhausted = False
        return self

    def __exit__(self, *exc):
        import main as m

        for name, value in self.saved.items():
            setattr(m, name, value)
        m.LLM.quota_exhausted = self.quota

def m_failed(output: str) -> set[str]:
    import main as m

    return m.failed_spec_names(output)

def measured(f):

    f.durations = {"build": [(4.0, 0)], "e2e": [(20.0, 1)]}

@case
def the_closing_repairs_an_owed_spec_first_with_its_story_and_outside_the_description_pass():
    from agent import description_pass

    lines: list[str] = []
    calls: list[dict] = []
    saved_gate = description_pass.DescriptionPass.quote_gate
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = closing_factory(root, "APP " + s3.F4_BREAK)
        measured(f)
        try:
            description_pass.DescriptionPass.quote_gate = lambda *a, **k: (_ for _ in ()).throw(AssertionError("quote gate used"))
            with Patched([{"app": "APP fixed"}], calls, lines):
                repaired = f.repair_debt_specs()
        finally:
            description_pass.DescriptionPass.quote_gate = saved_gate
        assert repaired == 1 and (root / rb.APP).read_text(encoding="utf-8") == "APP fixed", lines
        assert calls[0]["node"] == "REQ-4-2-3" and "the later requirement REQ-9 changed files it depends on" in calls[0]["context"], calls
        assert "CLOSING REPAIR" in calls[0]["context"] and f.debt_tried == [], f.__dict__.get("debt_tried")
        assert f.report["debt_repair"] == {"owed": ["REQ-4-2-3.spec.js"], "red": ["REQ-4-2-3.spec.js"], "repaired": 1}
        assert any("closing, first job: 1 spec(s)" in l for l in lines), lines

    import main as m

    source = inspect.getsource(m.Factory.run)
    assert 0 < source.index("self.repair_debt_specs()") < source.index("run_description_pass(self"), "debt repair after the description pass"

@case
def only_owed_specs_one_sweep_and_an_unrepaired_one_is_not_tried_again():
    lines: list[str] = []
    calls: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = closing_factory(root, "APP " + s3.F4_BREAK + " " + rb.brk(rb.S2))
        measured(f)
        with Patched([{"app": "APP still " + s3.F4_BREAK + " " + rb.brk(rb.S2)}], calls, lines):
            repaired = f.repair_debt_specs()
            assert repaired == 0 and [c["node"] for c in calls] == ["REQ-4-2-3"], calls
            assert f.debt_tried == [F4] and (root / rb.APP).read_text(encoding="utf-8") == "APP " + s3.F4_BREAK + " " + rb.brk(rb.S2)

            suite = rb.World(root).run_e2e(root, f.e2e_specs)
            with Patched([{"app": "APP " + s3.F4_BREAK}], calls, lines):
                f.repair_failing_specs(suite.output)
            assert [c["node"] for c in calls] == ["REQ-4-2-3", "REQ-1-1-1"], calls
            assert any("owed spec(s) the first job could not repair are not tried again" in l for l in lines), lines

            calls.clear()
            f.debt_tried = []
            (root / rb.APP).write_text("APP " + s3.F4_BREAK + " " + rb.brk(rb.S2), encoding="utf-8")
            suite = rb.World(root).run_e2e(root, f.e2e_specs)
            assert {"REQ-1-1-1.spec.js", "REQ-4-2-3.spec.js"} <= m_failed(suite.output), suite.output
            with Patched([{"app": "APP " + rb.brk(rb.S2)}], calls, lines):
                assert f.repair_failing_specs(suite.output, only=[F4]) == 1
            assert [c["node"] for c in calls] == ["REQ-4-2-3"], calls

@case
def the_first_job_is_bounded():
    import main as m

    lines: list[str] = []
    calls: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        f = closing_factory(root, "APP " + s3.F4_BREAK)
        measured(f)
        f.debt_specs = {}
        with Patched([], calls, lines):
            assert f.repair_debt_specs() == 0
        f.debt_specs = {F4: {"by": "REQ-9", "failed": []}}
        with Settings(closing_debt_first=False), Patched([], calls, lines):
            assert f.repair_debt_specs() == 0

        need = f.build_est() + f.suite_est(1) + f.closing_repair_min_sec()
        f.hard_end_at = m.clock.now() + f.closing_chain_sec(len(f.e2e_specs)) + need - 1000.0
        assert f.closing_budget() >= need, (f.closing_budget(), need)
        with Patched([], calls, lines):
            assert f.repair_debt_specs() == 0 and not calls
        assert any("closing debt repair not started" in l for l in lines), lines

        f.hard_end_at = m.clock.now() + 86400.0
        window = f.build_est() + f.suite_est(1) + (f.CLOSING_PER_SPEC_SEC[0] + 2 * f.suite_est(1)) + m.HARD_END_ROUND_SEC
        began = m.clock.now()
        with Patched([{"app": "APP fixed"}], calls, lines):
            assert f.repair_debt_specs() == 1
        assert calls[-1]["options"]["deadline"] <= began + window - m.HARD_END_ROUND_SEC + 1.0, (calls[-1]["options"], window)

        g = closing_factory(root, "APP " + s3.F4_BREAK)
        with Patched([{"app": "APP fixed"}], calls, lines):
            g.repair_debt_specs()

PAGE_ERROR_EVENT = {"type": "event", "time": 1397.056, "class": "BrowserContext", "method": "pageError",
                    "params": {"error": {"error": {"message": "notDefinedAtLoad is not defined",
                                                   "stack": "ReferenceError: notDefinedAtLoad is not defined\n    at <anonymous>:2:140",
                                                   "name": "ReferenceError"}}}, "pageId": "page@05e4"}
CONSOLE_EVENT = {"type": "console", "messageType": "error", "text": "save failed at http://127.0.0.1:3100/api/x",
                 "args": [], "location": {"url": "", "lineNumber": 1, "columnNumber": 48}, "time": 1396.635, "pageId": "page@05e4"}
WARNING_EVENT = {"type": "console", "messageType": "warning", "text": "just a warning", "time": 1396.0}

def write_trace(path: Path, events: list[dict]) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("test.trace", json.dumps({"type": "before", "method": "pageError"}) + NL)
        archive.writestr("0-trace.trace", NL.join(json.dumps(e) for e in events) + NL)
    return str(path)

def report_with(trace: str, status: str = "failed") -> dict:
    return {"suites": [{"title": "REQ-4.spec.js", "file": "REQ-4.spec.js", "specs": [], "suites": [
        {"title": "editor", "specs": [{"title": "opens a file", "file": "REQ-4.spec.js", "tests": [
            {"results": [{"status": status, "attachments": [{"name": "trace", "contentType": "application/zip", "path": trace}]}]}]}]}]}]}

@case
def a_failed_tests_page_errors_come_from_its_trace():
    from agent import verify

    with tempfile.TemporaryDirectory() as tmp:
        trace = write_trace(Path(tmp) / "trace.zip", [PAGE_ERROR_EVENT, CONSOLE_EVENT, WARNING_EVENT, PAGE_ERROR_EVENT])
        assert verify.page_errors_from_trace(trace) == ["console.error save failed at <url>",
                                                        "uncaught ReferenceError: notDefinedAtLoad is not defined"]
        report = Path(tmp) / "report.json"
        report.write_text(json.dumps(report_with(trace)), encoding="utf-8")
        note = verify.page_errors_note(report)
        assert note.splitlines()[0] == verify.PAGE_ERRORS_HEAD, note
        assert "[page] REQ-4.spec.js › editor › opens a file: uncaught ReferenceError: notDefinedAtLoad is not defined" in note, note

        report.write_text(json.dumps(report_with(trace, status="passed")), encoding="utf-8")
        assert verify.page_errors_note(report) == ""
        (Path(tmp) / "bad.zip").write_text("no zip", encoding="utf-8")
        assert verify.page_errors_from_trace(str(Path(tmp) / "bad.zip")) == [] and verify.page_errors_note(Path(tmp) / "none.json") == ""
        assert verify.page_errors_note(None) == ""

@case
def the_page_error_lines_change_no_verdict_and_no_stall_signature():
    import main as m
    from agent import codegen, verify

    base = rb.spec_output("REQ-4.spec.js", ["editor › opens a file"])
    with tempfile.TemporaryDirectory() as tmp:
        trace = write_trace(Path(tmp) / "trace.zip", [PAGE_ERROR_EVENT, CONSOLE_EVENT])
        report = Path(tmp) / "report.json"
        report.write_text(json.dumps(report_with(trace)), encoding="utf-8")
        note = verify.page_errors_note(report)
    with_note = base + NL + NL + note + NL
    assert m.failed_spec_names(with_note) == m.failed_spec_names(base) and m.failed_test_titles(with_note) == m.failed_test_titles(base)
    no_head = with_note.split(NL, 1)[1]
    assert m.failed_spec_names(no_head) == m.failed_spec_names(base.split(NL, 1)[1])

    assert codegen.spec_thrown_errors(with_note, {"backend/test-e2e/REQ-4.spec.js": "test('x')"}) == \
        codegen.spec_thrown_errors(base, {"backend/test-e2e/REQ-4.spec.js": "test('x')"})

    source = inspect.getsource(codegen.CodegenImplementer)
    assert 'and not l.startswith("[page] ")' in source
    once = inspect.getsource(verify._run_e2e_once)
    assert once.index("page_errors = page_errors_note(report)") < once.index("finally:") < once.index("cleaned += server_errors_note")
    assert once.index("page_errors + ") > once.index("cleaned = output if len(output) <= 14000")

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
    print(f"{len(CASES) - failed}/{len(CASES)} v144m cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
