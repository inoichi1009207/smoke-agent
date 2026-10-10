from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "selftests"))

CASES: list = []

def case(function):
    CASES.append(function)
    return function

def _runner():
    for name in ("__main__", "__mp_main__"):
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "_run_one") and hasattr(module, "registration_skips"):
            return module
    import run
    return run

def _quiet(call, *args):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    buffer = io.StringIO()
    with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
        result = call(*args)
    return result, buffer.getvalue()

@case
def a_skip_is_reported_and_never_counted_as_passed():
    run = _runner()
    fake = types.ModuleType("cases_v144q_fake")
    fake.SKIPPED = []
    sys.modules[fake.__name__] = fake
    try:
        def raises():
            raise unittest.SkipTest("no node")

        def records():
            fake.SKIPPED.append("SKIP  records: no samples")

        def passes():
            pass

        def fails():
            raise AssertionError("red")
        for function in (raises, records, passes, fails):
            function.__module__ = fake.__name__
        verdicts = {}
        for function in (raises, records, passes, fails):
            verdicts[function.__name__], shown = _quiet(run._run_one, function)
            assert shown.split()[0] == {"raises": "SKIP", "records": "SKIP", "passes": "ok", "fails": "FAIL"}[function.__name__], shown
        assert verdicts == {"raises": "skip", "records": "skip", "passes": "ok", "fails": "fail"}, verdicts

        fake.SKIPPED[:] = ["SKIP  census_case: sample(s) x.yaml not found"]
        lines = run.registration_skips([passes])
        assert lines.count("SKIP  census_case: sample(s) x.yaml not found") == 1, lines
    finally:
        sys.modules.pop(fake.__name__, None)
    assert run.summary(["ok", "ok", "skip", "fail"], ["SKIP  c"]) == "2/5 self-tests passed, 2 skipped, 1 failed"
    assert run.summary(["ok"], []) == "1/1 self-tests passed, 0 skipped, 0 failed"

@case
def the_transaction_case_without_node_or_sqlite3_is_a_skip():
    run = _runner()
    import cases_tx142
    saved = cases_tx142._node_modules
    cases_tx142._node_modules = lambda: None
    try:
        status, shown = _quiet(run._run_one, cases_tx142.overlapping_transactions_all_commit_and_rollbacks_stay_their_own)
    finally:
        cases_tx142._node_modules = saved
    assert status == "skip" and shown.startswith("SKIP  overlapping_transactions"), (status, shown)

@case
def browser_cases_without_a_browser_are_skips_every_time():
    run = _runner()
    import cases_r46v
    import cases_visual142
    for module, names in ((cases_visual142, ("browser_texts", "browser_popups", "browser_selection", "browser_blank_and_heading")),
                          (cases_r46v, ("r46_browser_raw_and_popup", "r46_browser_selection_graphics", "r46_browser_blank",
                                        "r46_browser_heading"))):
        saved = module._BROWSER
        module._BROWSER = {}
        try:
            for name in names:
                status, shown = _quiet(run._run_one, getattr(module, name))
                assert status == "skip" and shown.startswith(f"SKIP  {name}"), (name, status, shown)
        finally:
            module._BROWSER = saved

@case
def the_suite_runs_in_an_empty_scratch_directory():
    run = _runner()
    import inspect
    before = Path.cwd()
    with run.scratch_cwd() as scratch:
        here = Path.cwd().resolve()
        assert here == Path(scratch).resolve() and not any(here.iterdir()), here
        assert here != ROOT.resolve() and ROOT.resolve() not in here.parents
        Path("left-behind.txt").write_text("x", encoding="utf-8")
    assert Path.cwd() == before and not Path(scratch).exists()
    main = inspect.getsource(run.main)
    assert main.index("with scratch_cwd():") < main.index("collect()"), "collect and every case run inside the scratch cwd"

@case
def an_empty_project_folder_given_as_dot_reads_nothing_outside():
    run = _runner()
    from agent import detaudit, inert_controls
    with tempfile.TemporaryDirectory() as outer:
        bait = Path(outer) / "deep" / "tree"
        bait.mkdir(parents=True)
        (bait / "Bait.tsx").write_text("export default function Bait() { return <button>Sort range</button>; }", encoding="utf-8")
        seen: list = []
        original = inert_controls.tsx_parser
        read_text = Path.read_text

        def spy(self, *args, **kwargs):
            seen.append(str(self))
            return read_text(self, *args, **kwargs)
        previous = os.getcwd()
        os.chdir(outer)
        inert_controls.tsx_parser = lambda log=print: object()
        Path.read_text = spy
        try:
            with run.scratch_cwd():
                started = time.time()
                detaudit.deterministic_findings(Path("."), {"id": "REQ-1", "requirement_text": "Click “Sort range”."}, [],
                                                log=lambda m: None)
                assert time.time() - started < 30
        except Exception:
            pass
        finally:
            Path.read_text = read_text
            inert_controls.tsx_parser = original
            os.chdir(previous)
        assert not [p for p in seen if "Bait.tsx" in p], seen

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
    print(f"{len(CASES) - failed}/{len(CASES)} v144q cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
