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

NL = chr(10)
CASES = []
MISSING = object()
CHECKS: list[str] = []
NODE = {"id": "REQ-9", "name": "Batch nine", "members": ["REQ-9"]}
REJECT_LINE = "[codegen] REQ-9 round 1: a test defect without verifiable quotes is not accepted: 'The spec expects Save but the requirement says Store'"

def case(function):
    CASES.append(function)
    return function

class Settings:
    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        from agent.settings import SETTINGS

        self.saved = {key: SETTINGS.get(key, MISSING) for key in self.values}
        SETTINGS.update(self.values)

    def __exit__(self, *exc):
        from agent.settings import SETTINGS

        for key, value in self.saved.items():
            if value is MISSING:
                SETTINGS.pop(key, None)
            else:
                SETTINGS[key] = value

def fake_implementer(script: list[dict], seen: list, offset: list[float]):
    from agent.codegen import ImplementResult

    class Fake:
        def __init__(self, llm, project_dir, check, **options):
            self.project_dir, self.check, self.options = project_dir, check, options
            seen.append(self)

        def implement(self, node, tests, outcome, extra_context=""):
            step = script.pop(0)
            app = self.project_dir / rb.APP
            self.saw_app = app.read_text(encoding="utf-8") if app.is_file() else None
            self.saw_files = sorted(p.name for p in (self.project_dir / "frontend" / "src").iterdir())
            self.saw = {k: getattr(self, k) for k in ("attempt_note", "temperature") if hasattr(self, k)}
            written: list[str] = []
            last = None
            for action in step.get("do", []):
                if action[0] == "write":
                    for rel, text in action[1].items():
                        path = self.project_dir / rel
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(text, encoding="utf-8")
                        written.append(rel)
                elif action[0] == "check":
                    last = self.check()
                    CHECKS.append(last.output)
                elif action[0] == "log":
                    self.options["log"](action[1])
                elif action[0] == "advance":
                    offset[0] += action[1]
                elif action[0] == "call":
                    action[1](self)
            status = step.get("status") or ("passed" if last is not None and last.passed else "budget")
            result = ImplementResult(status, list(dict.fromkeys(written)), step.get("detail", "scripted"), 1,
                                     last_check=last.output if (last is not None and status == "passed") else "")
            for key in ("stop_reason", "defect_kind", "defect_files"):
                if key in step:
                    setattr(result, key, step[key])
            return result

    return Fake

def run(app_before: str, script: list[dict], calls=({},), settings: dict | None = None, time_budget: float = 10800.0, prep=None):
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
    with Settings(audit_enabled=False, **(settings or {})), tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = rb.World(root)
        f = rb.make_factory(root, app_before, time_budget)
        if prep:
            prep(f)
        try:
            clock.now = lambda: time.time() + offset[0]
            factory_module.run_e2e, factory_module.build_frontend = world.run_e2e, world.build_frontend
            factory_module.Implementer = fake_implementer(script, seen, offset)
            factory_module.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
            factory_module.static_test_defects = lambda project_dir, files, **kw: ""
            factory_module.parse_defects_in_output = lambda output, files: ""
            factory_module.lint_application = lambda project_dir, native: ""
            factory_module.log = lines.append
            results = [f.process_node(dict(NODE), 1, **call) for call in calls]
            app = (root / rb.APP).read_text(encoding="utf-8") if (root / rb.APP).is_file() else None
        finally:
            for name, value in saved.items():
                setattr(factory_module, name, value)
            clock.now = saved_now
    assert not script, f"scripted steps left over: {script}"
    return f, lines, app, results, seen

def with_tests_booked(f):
    content = f"test('{rb.OWN_TITLE}', async () => {{}});" + NL
    f.report["nodes"]["REQ-9"] = {"status": "budget", "tests": [rb.OWN]}
    f.test_snapshot[rb.OWN] = content
    (f.project_dir / rb.OWN).parent.mkdir(parents=True, exist_ok=True)
    (f.project_dir / rb.OWN).write_text(content, encoding="utf-8")

@case
def first_error_lines_name_each_failed_test_and_its_first_error():
    import main as factory_module

    out = rb.spec_output("REQ-1-1-1.spec.js", [rb.S1, rb.S2])
    got = factory_module.first_error_lines(out)
    assert got == [f"REQ-1-1-1.spec.js › {t}: Error: The requirement says Username format is invalid is shown, but nothing visible matches."
                   for t in (rb.S1, rb.S2)], got
    assert len(factory_module.first_error_lines(out, limit=1)) == 1

    assert factory_module.first_error_lines("x\nTypeError: a is undefined\nTypeError: a is undefined\nok") == ["TypeError: a is undefined"]
    assert factory_module.first_error_lines("FRONTEND BUILD FAILED\nsrc/App.tsx(3,1): bad") == ["FRONTEND BUILD FAILED", "src/App.tsx(3,1): bad"]
    assert factory_module.first_error_lines("") == []

@case
def change_digest_names_every_changed_file_and_shows_removed_lines_first_when_cut():
    import main as factory_module

    before = {"a.js": b"one\ntwo\nthree\n", "b.js": b"x\n", "gone.js": b"bye\n", "same.js": b"s\n"}
    now = {"a.js": b"one\nTWO\nthree\n", "b.js": b"y\n", "new.js": b"1\n2\n", "same.js": b"s\n"}
    changed, text = factory_module.change_digest(before, now, first=["b.js"])
    assert changed == ["a.js", "b.js", "gone.js", "new.js"], changed
    assert text.index("--- b.js") < text.index("--- a.js") < text.index("+++ new.js (new file, 2 lines)") < text.index("--- gone.js (deleted)"), text
    assert "-two" in text and "+TWO" in text and "same.js" not in text

    old = "".join(f"old line {i}\n" for i in range(200)).encode()
    new = "".join(f"new line {i}\n" for i in range(200)).encode()
    _, text = factory_module.change_digest({"big.js": old}, {"big.js": new}, per_file=300)
    body = text.splitlines()[1:]
    assert body[0].startswith("-old line 0") and all(l.startswith("-") for l in body[:-1]) and "more changed line(s) not shown" in body[-1], body[:3]
    assert len(text) < 400

    many = {f"f{i}.js": b"a\n" for i in range(30)}
    _, text = factory_module.change_digest(many, {k: b"b\n" for k in many}, cap=100)
    assert "(also changed, not shown:" in text and len(text) < 400
    assert factory_module.change_digest({"a": b"1"}, {"a": b"1"}) == ([], "")

@case
def a_rejected_claim_is_read_from_the_implementers_log_line():
    import main as factory_module

    defect = "The spec expects Save but the requirement says Store"
    line = f"[codegen] REQ-9 round 1: a test defect without verifiable quotes is not accepted: {defect[:160]!r}"
    claim, why = factory_module.rejected_claim_of(line)
    assert claim == defect and "no verifiable quote" in why, (claim, why)
    claim, why = factory_module.rejected_claim_of("[codegen] REQ-9 round 2: the answer only rewrote tests ['backend/test-e2e/A.spec.js']; not a hand-over without evidence")
    assert "A.spec.js" in claim and "rewrote test files" in why
    assert factory_module.rejected_claim_of("[codegen] REQ-9 round 2: wrote 1 file(s)") is None

@case
def the_retry_temperature_setting_reads_text_and_zero_is_off():
    from agent.settings import retry_temperature

    with Settings(retry_temperature=0.3):
        assert retry_temperature() == 0.3
    with Settings(retry_temperature="0.5"):
        assert retry_temperature() == 0.5
    with Settings(retry_temperature="0"):
        assert retry_temperature() == 0.0
    with Settings(retry_temperature="warm"):
        assert retry_temperature() == 0.3

@case
def a_foundation_break_note_names_the_changed_file_its_diff_and_the_first_errors():
    from agent.codegen import FOUNDATION_MARK, own_output

    CHECKS.clear()
    broken = "base " + rb.brk(rb.S1)
    run("base", [{"do": [("write", {rb.APP: broken}), ("check",)]}])
    notes = [out for out in CHECKS if FOUNDATION_MARK in out]
    assert notes, CHECKS
    note = notes[0][notes[0].index(FOUNDATION_MARK):]
    assert "FILES THIS BATCH CHANGED SINCE THOSE SPECS LAST PASSED (when this batch began (they passed then)): frontend/src/App.tsx" in note, note
    assert "-base" + NL + "+" + broken in note, note
    assert f"FIRST ERRORS OF THE BROKEN SPECS:{NL}  REQ-1-1-1.spec.js › {rb.S1}: Error: The requirement says Username" in note, note
    assert "your changes broke them" in note

    assert "FILES THIS BATCH CHANGED" not in own_output(notes[0])

    CHECKS.clear()
    with Settings(break_evidence=False):
        run("base", [{"do": [("write", {rb.APP: broken}), ("check",)]}])
    notes = [out for out in CHECKS if FOUNDATION_MARK in out]
    assert notes and not any("FILES THIS BATCH CHANGED" in out or "FIRST ERRORS OF THE BROKEN" in out for out in notes), notes

@case
def the_diff_starts_at_the_broken_specs_latest_green_run_in_the_batch():
    from agent.codegen import FOUNDATION_MARK

    CHECKS.clear()
    first = "base feature"
    broken = first + " " + rb.brk(rb.S1)
    run("base", [{"do": [("write", {rb.APP: first}), ("check",), ("write", {rb.APP: broken}), ("check",)]}])

    assert not any(FOUNDATION_MARK in out for out in CHECKS[:1]), CHECKS[0]
    note = CHECKS[1][CHECKS[1].index(FOUNDATION_MARK):]
    assert "SINCE THOSE SPECS LAST PASSED (at check 1 of this batch, where they last passed)" in note, note
    assert "-" + first + NL + "+" + broken in note and "-base" + NL not in note, note

@case
def a_regression_break_with_the_own_spec_green_carries_the_same_evidence():
    CHECKS.clear()
    broken = "base OWN-OK " + rb.brk(rb.S1)
    run("base", [{"do": [("write", {rb.APP: broken}), ("check",)]}])
    red = [out for out in CHECKS if "This batch's own spec PASSES" in out]
    assert red, CHECKS
    assert "FILES THIS BATCH CHANGED SINCE THOSE SPECS LAST PASSED (when this batch began (they passed then)): frontend/src/App.tsx" in red[0], red[0]
    assert "+" + broken in red[0] and "FIRST ERRORS OF THE BROKEN SPECS:" in red[0], red[0]
    CHECKS.clear()
    with Settings(break_evidence=False):
        run("base", [{"do": [("write", {rb.APP: broken}), ("check",)]}])
    assert CHECKS and not any("FILES THIS BATCH CHANGED" in out for out in CHECKS), CHECKS

@case
def a_foundation_retry_gets_the_first_attempts_note_and_the_retry_temperature():
    script = [{"do": [("write", {rb.APP: "base feature"}), ("log", REJECT_LINE), ("check",)], "detail": "repair rounds exhausted"},
              {"do": [("write", {rb.APP: "base OWN-OK"}), ("check",)]}]
    f, lines, app, results, seen = run("base", script, calls=({}, {"retry": True}), settings={"in_batch_restart": False})
    assert results == [False, True] and app == "base OWN-OK", (results, app)
    first, retry = seen
    assert first.saw == {}, first.saw
    assert retry.saw.get("temperature") == 0.3, retry.saw
    note = retry.saw.get("attempt_note") or ""
    assert note.startswith("PREVIOUS ATTEMPT AT THIS BATCH"), note
    assert "How it ended: budget -- repair rounds exhausted" in note and "Its changes are still on disk" in note, note
    assert f"REQ-9.spec.js › {rb.OWN_TITLE}: Error: The requirement says" in note, note
    assert "Files it changed: frontend/src/App.tsx" in note, note
    assert "'The spec expects Save but the requirement says Store' -- rejected: no verifiable quote" in note, note

    assert REJECT_LINE in lines

@case
def the_retry_note_says_when_the_first_attempt_was_put_back_and_carries_its_break():
    script = [{"do": [("write", {rb.APP: "base " + rb.brk(rb.S1)}), ("check",)]},
              {"do": [("check",)], "status": "budget"}]
    f, lines, app, results, seen = run("base", script, calls=({}, {"retry": True}), settings={"in_batch_restart": False})
    assert rb.put_back(lines) and app == "base", lines
    note = seen[1].saw.get("attempt_note") or ""
    assert "Its changes were put back: you start from the sources as they were before it." in note, note
    assert "It broke the specs of EARLIER requirements" in note and "+base " + rb.brk(rb.S1) in note, note

@case
def retry_temperature_zero_and_attempt_note_off_leave_the_retry_as_v144():
    for settings, want in (({"retry_temperature": 0}, {"attempt_note"}), ({"attempt_note": False}, {"temperature"}),
                           ({"retry_temperature": "0", "attempt_note": False}, set())):
        script = [{"do": [("write", {rb.APP: "base feature"}), ("check",)]}, {"do": [("check",)], "status": "budget"}]
        f, lines, app, results, seen = run("base", script, calls=({}, {"retry": True}), settings={"in_batch_restart": False, **settings})
        assert set(seen[1].saw) == want, (settings, seen[1].saw)

@case
def a_stuck_implementer_is_restarted_once_from_the_state_before_the_batch_and_a_green_restart_is_kept():
    script = [{"do": [("write", {rb.APP: "base bad", "frontend/src/Extra.tsx": "x"}), ("log", REJECT_LINE), ("check",)],
               "stop_reason": "stuck", "detail": "the same failure 3 checks in a row"},
              {"do": [("write", {rb.APP: "base OWN-OK"}), ("check",)]}]
    f, lines, app, results, seen = run("base", script)
    assert results == [True] and app == "base OWN-OK", (results, app, lines)
    first, restart = seen
    assert restart.saw_app == "base", restart.saw_app
    assert restart.options["deadline"] == first.options["deadline"], (restart.options["deadline"], first.options["deadline"])
    assert restart.saw.get("temperature") == 0.3 and first.saw == {}
    note = restart.saw.get("attempt_note") or ""
    assert "How it ended: budget (stuck) -- the same failure 3 checks in a row" in note and "Its changes were put back" in note, note
    assert "Files it changed: frontend/src/App.tsx, frontend/src/Extra.tsx" in note, note
    assert "rejected: no verifiable quote" in note, note
    started = [l for l in lines if "(in-batch restart)" in l]
    assert len(started) == 1 and "stopped red early (stuck)" in started[0], started
    assert any("the in-batch restart ended GREEN; it is kept" in l for l in lines)
    assert f.report["nodes"]["REQ-9"]["written"] == [rb.APP] and f.report["nodes"]["REQ-9"]["status"] == "passed"
    assert restart.saw_files == ["App.tsx"], restart.saw_files

@case
def a_red_restart_is_not_kept_the_first_attempts_end_state_goes_back():
    script = [{"do": [("write", {rb.APP: "base first"}), ("check",)], "stop_reason": "seed-conflict"},
              {"do": [("write", {rb.APP: "base second", "frontend/src/Other.tsx": "o"}), ("check",)]}]
    f, lines, app, results, seen = run("base", script)
    assert results == [False] and app == "base first", (app, lines)
    assert any("the in-batch restart ended budget; not kept" in l for l in lines), lines
    entry = f.report["nodes"]["REQ-9"]
    assert entry["status"] == "budget" and entry["written"] == [rb.APP], entry

    note = f.attempt_notes["REQ-9"]
    assert "An in-batch restart from the state before the batch was tried as well and ended budget" in note, note

@case
def gave_up_restarts_too_other_ends_do_not():
    script = [{"do": [("write", {rb.APP: "base a"})], "status": "gave_up"},
              {"do": [("write", {rb.APP: "base OWN-OK"}), ("check",)]}]
    f, lines, app, results, seen = run("base", script)
    assert results == [True] and len(seen) == 2, lines
    for end in ({"stop_reason": "rounds"}, {"stop_reason": ""}, {}):
        script = [{"do": [("write", {rb.APP: "base a"}), ("check",)], **end}]
        f, lines, app, results, seen = run("base", script)
        assert len(seen) == 1 and not any("in-batch restart" in l for l in lines), (end, lines)

@case
def no_restart_in_a_retry_or_when_off_or_after_a_green_end():
    script = [{"do": [("write", {rb.APP: "base a"}), ("check",)], "stop_reason": "stuck"}]
    f, lines, app, results, seen = run("base", script, calls=({"retry": True},), prep=with_tests_booked)
    assert len(seen) == 1 and not any("in-batch restart" in l for l in lines), lines

    for books in ({"foundation_retried": ["REQ-9"]}, {"hub_retried": ["REQ-9"]}):
        script = [{"do": [("write", {rb.APP: "base a"}), ("check",)], "stop_reason": "stuck"}]
        f, lines, app, results, seen = run("base", script, prep=lambda f, books=books: f.__dict__.update(books))
        assert len(seen) == 1, (books, lines)
    with Settings(in_batch_restart=False):
        script = [{"do": [("write", {rb.APP: "base a"}), ("check",)], "stop_reason": "stuck"}]
        f, lines, app, results, seen = run("base", script)
        assert len(seen) == 1
    script = [{"do": [("write", {rb.APP: "base OWN-OK"}), ("check",)], "stop_reason": "stuck"}]
    f, lines, app, results, seen = run("base", script)
    assert results == [True] and len(seen) == 1

@case
def the_restart_needs_forty_percent_of_the_share_or_half_the_minimum_left():

    from agent import llm as llm_module

    assert llm_module.CALL_LIMIT_SEC == 1800.0, llm_module.CALL_LIMIT_SEC

    for spent, restarts in ((3300, True), (3500, False)):
        script = [{"do": [("write", {rb.APP: "base a"}), ("check",), ("advance", spent)], "stop_reason": "stuck"}]
        if restarts:
            script.append({"do": [("check",)], "status": "budget"})
        f, lines, app, results, seen = run("base", script)
        assert (len(seen) == 2) is restarts, (spent, lines)
        if not restarts:
            assert any("no in-batch restart" in l and ("5255 s needed" in l or "5256 s needed" in l) for l in lines), lines

    script = [{"do": [("write", {rb.APP: "base a"}), ("check",)], "stop_reason": "stuck"}]
    f, lines, app, results, seen = run("base", script, time_budget=1000.0)
    assert len(seen) == 1 and any("2400 s needed" in l for l in lines), lines
    saved = llm_module.CALL_LIMIT_SEC
    try:
        llm_module.CALL_LIMIT_SEC = 100.0
        for spent, restarts in ((400, True), (600, False)):
            script = [{"do": [("write", {rb.APP: "base a"}), ("check",), ("advance", spent)], "stop_reason": "stuck"}]
            if restarts:
                script.append({"do": [("check",)], "status": "budget"})
            f, lines, app, results, seen = run("base", script, time_budget=1000.0)
            assert (len(seen) == 2) is restarts, (spent, lines)
        llm_module.CALL_LIMIT_SEC = 0.0
        script = [{"do": [("write", {rb.APP: "base a"}), ("check",)], "stop_reason": "stuck"}]
        f, lines, app, results, seen = run("base", script)
        assert len(seen) == 1 and any("has no time limit of its own" in l for l in lines), lines
    finally:
        llm_module.CALL_LIMIT_SEC = saved

@case
def the_restarts_books_start_fresh_and_the_first_attempts_come_back_when_it_is_not_kept():

    CHECKS.clear()
    script = [{"do": [("write", {rb.APP: "base first"}), ("check",)], "stop_reason": "stuck"},
              {"do": [("write", {rb.APP: "base " + rb.brk(rb.S1)}), ("check",)]}]
    f, lines, app, results, seen = run("base", script)
    from agent.codegen import FOUNDATION_MARK

    notes = [out for out in CHECKS if FOUNDATION_MARK in out]
    assert notes and "(when this batch began (they passed then))" in notes[-1], notes
    assert app == "base first" and results == [False], (app, lines)

    assert not rb.put_back([l for l in lines if "(in-batch restart)" not in l]), lines

@case
def the_diff_reference_is_the_earliest_green_run_over_all_broken_specs_and_lock_files_are_only_named():
    import main as factory_module

    with tempfile.TemporaryDirectory() as tmp:
        f = rb.make_factory(Path(tmp), "now")
        saved = factory_module.log
        factory_module.log = lambda line: None
        try:
            early = {rb.APP: b"early", "backend/package-lock.json": b'{"a": 1}'}
            late = {rb.APP: b"late", "backend/package-lock.json": b'{"a": 1}'}
            (f.project_dir / "backend" / "package-lock.json").write_text('{"a": 2}', encoding="utf-8")
            books = {"green_ref": {rb.F1: (3, late), rb.F2: (1, early)}}
            text = f.break_evidence([rb.F1, rb.F2], {rb.APP: b"begin"}, books, [])
        finally:
            factory_module.log = saved
    assert "(at check 1 of this batch, where they last passed)" in text and "-early" + NL + "+now" in text, text
    assert "backend/package-lock.json (a lock file: diff not shown)" in text and '"a": 2' not in text, text
    assert books["break_evidence"] == text

@case
def the_regression_evidence_stays_out_of_the_batchs_own_output():
    from agent.codegen import blocking_output, own_output

    CHECKS.clear()
    run("base", [{"do": [("write", {rb.APP: "base OWN-OK " + rb.brk(rb.S1)}), ("check",)]}])
    red = [out for out in CHECKS if "This batch's own spec PASSES" in out]
    assert red and "FILES THIS BATCH CHANGED" in red[0], red

    assert "FILES THIS BATCH CHANGED" not in own_output(red[0]) and "FIRST ERRORS OF THE BROKEN" not in blocking_output(red[0])
    assert "Username format is invalid" in own_output(red[0])

def _repairs(f, calls: list):
    def repair(node, prefix, files, problem, contract=""):
        calls.append(problem)
        return files
    f.repair_tests = repair

@case
def a_seed_conflict_defect_reaches_the_spec_author_while_earlier_specs_are_red():
    for kind, repaired in (("seed-conflict", True), ("", False)):
        calls: list = []
        script = [{"do": [("write", {rb.APP: "base OWN-OK " + rb.brk(rb.S1)}), ("check",)], "status": "test_defect",
                   "defect_kind": kind, "detail": "SEED premise differs"}]
        if repaired:
            script.append({"do": [("check",)], "status": "budget"})
        f, lines, app, results, seen = run("base", script, settings={"in_batch_restart": False},
                                           prep=lambda f, calls=calls: _repairs(f, calls))
        assert (calls == ["SEED premise differs"]) is repaired, (kind, calls)
        assert any("no test repair" in l for l in lines) is (not repaired), (kind, lines)

@case
def an_api_test_defect_does_not_mark_the_acceptance_spec_as_revised():
    for kind, revised in (("api", False), ("", True)):
        calls: list = []
        script = [{"do": [("write", {rb.APP: "base x"}), ("check",)], "status": "test_defect", "defect_kind": kind,
                   "defect_files": [API], "detail": "backend/tests/REQ-9.spec.js expects 201"},
                  {"do": [("check",)], "status": "budget"}]

        def prep(f, calls=calls):
            _with_api_test(f)
            _repairs(f, calls)
        f, lines, app, results, seen = run("base", script, settings={"in_batch_restart": False}, prep=prep)
        assert len(calls) == 1 and (rb.OWN in f.revised_specs) is revised, (kind, calls, f.revised_specs)

API = "backend/tests/REQ-9.spec.js"
OWN_TEXT = f"test('{rb.OWN_TITLE}', async () => {{}});" + NL

def _with_api_test(f):
    from agent.test_author import AuthoredFile

    f.author_tests = lambda node, prefix, contract: [AuthoredFile(path=rb.OWN, content=OWN_TEXT),
                                                     AuthoredFile(path=API, content="test('api', () => expect(201))" + NL)]

class RewritingAuthor:

    def __init__(self):
        self.given: list = []

    def repair(self, node, prefix, files, problem, contract=""):
        from agent.test_author import AuthoredFile

        self.given.append(sorted(f.path for f in files))
        return [AuthoredFile(path=f.path, content="REWRITTEN " + f.path + NL) for f in files] + [
            AuthoredFile(path=rb.OWN, content="test('weakened', async () => {});" + NL)]

@case
def an_api_test_defect_repair_is_given_and_writes_only_its_defect_files():

    author = RewritingAuthor()
    script = [{"do": [("write", {rb.APP: "base x"}), ("check",)], "status": "test_defect", "defect_kind": "api",
               "defect_files": [API], "detail": "REQ-9.spec.js expects 201; the requirement says 200"},
              {"do": [("check",)], "status": "budget"}]
    disk: dict = {}

    def prep(f):
        _with_api_test(f)
        f.author = author
        HOLDER[:] = [f]

    script[1]["do"].insert(0, ("call", lambda impl: disk.update({p: (HOLDER[0].project_dir / p).read_bytes() for p in (rb.OWN, API)})))
    f, lines, app, results, seen = run("base", script, settings={"in_batch_restart": False}, prep=prep)
    assert author.given == [[API]], author.given
    assert disk[rb.OWN] == OWN_TEXT.encode("utf-8"), disk[rb.OWN]
    assert disk[API] == ("REWRITTEN " + API + NL).encode("utf-8"), disk[API]
    assert f.test_snapshot[rb.OWN] == OWN_TEXT and rb.OWN not in f.revised_specs
    assert any("outside the defect's files; ignored" in l for l in lines), lines

    import main as factory_module
    from agent.test_author import AuthoredFile

    with tempfile.TemporaryDirectory() as tmp:
        f = rb.make_factory(Path(tmp), "x")
        f.author = RewritingAuthor()
        own, api = AuthoredFile(path=rb.OWN, content=OWN_TEXT), AuthoredFile(path=API, content="old" + NL)
        saved_log = factory_module.log
        factory_module.log = lambda line: None
        try:
            merged, written = f.repair_tests_within("REQ-9", dict(NODE), "REQ-9", [own, api], {rb.OWN: own.content, API: api.content},
                                                    "x", "", {API})
        finally:
            factory_module.log = saved_log
    assert merged[0] is own and merged[1].content == "REWRITTEN " + API + NL and written[rb.OWN] == OWN_TEXT, (merged, written)

    author2 = RewritingAuthor()
    script = [{"do": [("write", {rb.APP: "base x"}), ("check",)], "status": "test_defect", "defect_kind": "api",
               "defect_files": ["backend/tests/OTHER.test.js"], "detail": "x"},
              {"do": [("check",)], "status": "budget"}]
    f, lines, app, results, seen = run("base", script, settings={"in_batch_restart": False},
                                       prep=lambda f: (_with_api_test(f), setattr(f, "author", author2)))
    assert author2.given == [] and any("names no test file of this batch" in l for l in lines), (author2.given, lines)

F4, F5 = "backend/test-e2e/REQ-2-2.spec.js", "backend/test-e2e/REQ-2-3.spec.js"

def _with_f345(f):
    f.green_specs = [rb.F1, rb.F2, "backend/test-e2e/REQ-1-2.spec.js", F3, F4, F5]
    f.e2e_specs = list(f.green_specs)

@case
def the_first_attempts_better_best_state_beats_a_restart_that_ends_owing_more():

    one = "base OWN-OK " + rb.brk("REQ-2-1: Scenario 1")
    three = "base OWN-OK again " + rb.brk("REQ-2-1: Scenario 1") + rb.brk("REQ-2-2: Scenario 1") + rb.brk("REQ-2-3: Scenario 1")
    script = [{"do": [("write", {rb.APP: one}), ("check",), ("write", {rb.APP: "base OWN-OK " + rb.brk(rb.S1)}), ("check",)],
               "stop_reason": "stuck"},
              {"do": [("write", {rb.APP: three}), ("check",)], "stop_reason": "stuck"},
              {"do": [], "status": "gave_up"}]
    f, lines, app, results, seen = run("base", script, prep=_with_f345)
    assert len(seen) == 3, lines
    assert app == one and results == [True] and f.regression_debts == ["REQ-9"], (app, lines)
    assert any("beats the restart's end (3 red)" in l for l in lines), lines

    other = "base OWN-OK other " + rb.brk("REQ-2-2: Scenario 1")
    script = [{"do": [("write", {rb.APP: one}), ("check",), ("write", {rb.APP: "base OWN-OK " + rb.brk(rb.S1)}), ("check",)],
               "stop_reason": "stuck"},
              {"do": [("write", {rb.APP: other}), ("check",)], "stop_reason": "stuck"},
              {"do": [], "status": "gave_up"}]
    f, lines, app, results, seen = run("base", script, prep=_with_f345)
    assert app == other and any("it is kept" in l for l in lines), (app, lines)

@case
def a_batch_with_a_foundation_or_hub_retry_gets_no_in_batch_restart():

    for prep, restarts in ((lambda f: setattr(f, "foundation_ids", {"REQ-9"}), False),
                           (lambda f: setattr(f, "hub_leaf", lambda node: {"id": "REQ-10"}), False),
                           (lambda f: setattr(f, "foundation_ids", {"REQ-1"}), True)):
        script = [{"do": [("write", {rb.APP: "base a"}), ("check",)], "stop_reason": "stuck"}]
        if restarts:
            script.append({"do": [("check",)], "status": "budget"})
        f, lines, app, results, seen = run("base", script, prep=prep)
        assert (len(seen) == 2) is restarts, (restarts, lines)

@case
def the_evidence_diff_never_shows_hidden_or_key_files_nor_lock_or_contract_contents():
    import main as factory_module

    reference = {rb.APP: b"old line", "backend/.env": b"TOKEN=SECRET-OLD", "backend/certs/server.pem": b"PEM-OLD",
                 "backend/package.json": b'{"CONTRACT-OLD": 1}'}
    now = {rb.APP: b"new line", "backend/.env": b"TOKEN=SECRET-NEW", "backend/certs/server.pem": b"PEM-NEW",
           "backend/package.json": b'{"CONTRACT-NEW": 1}', "frontend/package-lock.json": b'{"LOCK-NEW": 1}',
           "frontend/.npmrc": b"TOKEN=SECRET-ADDED"}
    changed, text = factory_module.change_digest(reference, now)
    for secret in ("SECRET", "PEM-", "CONTRACT-", "LOCK-NEW", ".env", ".pem", ".npmrc"):
        assert secret not in text and not any(secret in name for name in changed), (secret, changed, text)
    assert "-old line" + NL + "+new line" in text, text
    assert "--- backend/package.json (content not shown)" in text and "--- frontend/package-lock.json (new; content not shown)" in text, text
    assert "(3 hidden or key file(s) also changed; not shown)" in text, text
    assert changed == ["backend/package.json", "frontend/package-lock.json", rb.APP], changed

    assert factory_module.change_digest({"backend/.env": b"a"}, {"backend/.env": b"b"}) == \
        ([], "(1 hidden or key file(s) also changed; not shown)")

    with tempfile.TemporaryDirectory() as tmp:
        f = rb.make_factory(Path(tmp), "x")
        (f.project_dir / "backend" / ".env").write_text("TOKEN=1", encoding="utf-8")
        assert "backend/.env" in f.snapshot_sources()

F3 = "backend/test-e2e/REQ-2-1.spec.js"

def _with_f3(f):
    f.green_specs = [rb.F1, rb.F2, "backend/test-e2e/REQ-1-2.spec.js", F3]
    f.e2e_specs = list(f.green_specs)

@case
def a_restart_that_ends_as_v144_keeps_a_batch_owing_a_regression_is_kept():

    broken = "base OWN-OK " + rb.brk("REQ-2-1: Scenario 1")
    script = [{"do": [("write", {rb.APP: "base bad"}), ("check",)], "stop_reason": "stuck"},
              {"do": [("write", {rb.APP: broken}), ("check",)], "stop_reason": "stuck"}]
    f, lines, app, results, seen = run("base", script, prep=_with_f3)
    assert app == broken and results == [True] and f.regression_debts == ["REQ-9"], (app, lines)
    assert any("only earlier non-foundation specs red" in l and "it is kept" in l for l in lines), lines

    script = [{"do": [("write", {rb.APP: broken}), ("check",)], "stop_reason": "stuck"},
              {"do": [("write", {rb.APP: broken + " other"}), ("check",)], "stop_reason": "stuck"}]
    f, lines, app, results, seen = run("base", script, prep=_with_f3)
    assert app == broken and any("in-batch restart ended budget; not kept" in l for l in lines), (app, lines)

@case
def the_restart_has_its_own_seed_holds_and_the_first_attempts_come_back():
    seen_holds: list = []
    script = [{"do": [("write", {rb.APP: "base a"}), ("check",)], "stop_reason": "stuck"},
              {"do": [("call", lambda impl: seen_holds.append(dict(HOLDER[0].seed_checks))), ("check",)], "status": "budget"}]

    def prep(f):
        HOLDER[:] = [f]
        f.seed_checks = {"REQ-9": 1, "REQ-9:values": 1, "REQ-8": 1}

    f, lines, app, results, seen = run("base", script, prep=prep)
    assert seen_holds == [{"REQ-8": 1}], seen_holds
    assert f.seed_checks == {"REQ-9": 1, "REQ-9:values": 1, "REQ-8": 1}, f.seed_checks

HOLDER: list = []

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
    print(f"{len(CASES) - failed}/{len(CASES)} v145a cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
