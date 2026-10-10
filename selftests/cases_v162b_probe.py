from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import settings, verify
from agent.verify import StepResult

CASES = []
SPEC = "backend/test-e2e/REQ-1.spec.js"
NAME = "REQ-1.spec.js"
GROUPS = [(10, [(10, 5, "REQ-1 › add a note")]), (20, [(20, 5, "REQ-1 › edit the note")]),
          (30, [(30, 5, "REQ-1 › the edited note is listed")])]
RED = (NAME, 30, 5, "REQ-1 › the edited note is listed")

def case(function):
    CASES.append(function)
    return function

class Fake:

    def __init__(self, passes, groups=GROUPS, fault=None, ran_delta=0) -> None:
        self.passes = passes
        self.groups = groups
        self.fault = fault
        self.ran_delta = ran_delta
        self.runs: list[list[str]] = []

    def run(self, project_dir, spec_files, log=print, only=None, errors_limit=9000, timeout=600, until=None):
        assert spec_files == [SPEC] and only, (spec_files, only)
        self.runs.append(list(only))
        if self.fault is not None:
            return self.fault(len(self.runs))
        lines = [int(item.rsplit(":", 1)[1]) for item in only]
        tests = [t for line, group in self.groups if line in lines for t in group]
        failed = [(NAME, l, c, t) for l, c, t in tests if not self.passes(lines, l)]
        output = (verify.FAILED_FILES_HEAD + NAME + "\n" if failed else "") + f"  {len(tests) - len(failed)} passed\n"
        return StepResult(not failed, output, ran=len(tests) + self.ran_delta, failed_tests=failed)

def depends(lines, line):
    return line != 30 or 20 in lines

def probe(fake: Fake, red=RED, setting=True, **kw):
    saved = (verify._run_e2e_once, verify.test_groups, settings.setting_on, verify.PER_TEST_ISOLATION)
    verify._run_e2e_once = fake.run
    verify.test_groups = lambda project_dir, spec, log=print: fake.groups
    settings.setting_on = (lambda key, default=True: setting if key == verify.DEPENDENCY_PROBE_SETTING else saved[2](key, default))
    verify.PER_TEST_ISOLATION = True
    try:
        return verify.dependency_probe(Path("."), SPEC, red, log=lambda *_: None, **kw)
    finally:
        verify._run_e2e_once, verify.test_groups, settings.setting_on, verify.PER_TEST_ISOLATION = saved

@case
def verdict_when_green_after_predecessors_and_red_alone():
    fake = Fake(depends)
    found = probe(fake)
    assert found is not None, found
    assert found["title"] == RED[3] and found["line"] == 30, found
    assert found["predecessors"] == ["REQ-1 › add a note", "REQ-1 › edit the note"], found
    assert "fails when it runs alone" in found["message"] and "fresh server and fresh data" in found["message"], found
    assert "GIVEN state" in found["message"], found

    assert fake.runs == [["test-e2e/REQ-1.spec.js:10", "test-e2e/REQ-1.spec.js:20", "test-e2e/REQ-1.spec.js:30"],
                         ["test-e2e/REQ-1.spec.js:30"]], fake.runs

@case
def summary_line_form_is_accepted():
    found = probe(Fake(depends), red="    test-e2e/REQ-1.spec.js:30:5 › REQ-1 › the edited note is listed ───")
    assert found is not None and found["line"] == 30, found

@case
def still_red_with_predecessors_gives_none():
    fake = Fake(lambda lines, line: line != 30)
    assert probe(fake) is None
    assert len(fake.runs) == 1, fake.runs

@case
def predecessor_red_gives_none():
    assert probe(Fake(lambda lines, line: line != 10 and depends(lines, line))) is None

@case
def crash_timeout_infra_skip_give_none():
    faults = [
        lambda n: StepResult(False, "backend did not become ready for e2e"),
        lambda n: StepResult(False, "Timed out after 120 s", ran=-1, failed_tests=None),
        lambda n: StepResult(False, "E2E FAILED", infra="backend: npm install failed"),
        lambda n: StepResult(True, "@playwright/test not installed", skipped=True),
        lambda n: StepResult(True, "passed", ran=3, failed_tests=[], infra="backend: install slow"),
    ]
    for fault in faults:
        assert probe(Fake(depends, fault=fault)) is None, fault(1).output

@case
def fewer_tests_ran_than_selected_gives_none():
    assert probe(Fake(depends, ran_delta=-1)) is None

@case
def run_that_raises_gives_none():
    def boom(n):
        raise RuntimeError("playwright exploded")
    assert probe(Fake(depends, fault=boom)) is None

@case
def red_is_first_test_gives_none_and_no_run():
    fake = Fake(lambda lines, line: line != 10)
    assert probe(fake, red=(NAME, 10, 5, "REQ-1 › add a note")) is None
    assert fake.runs == [], fake.runs

@case
def unknown_test_or_other_file_gives_none_and_no_run():
    fake = Fake(depends)
    assert probe(fake, red=(NAME, 99, 5, "REQ-1 › nobody")) is None
    assert probe(fake, red=("OTHER.spec.js", 30, 5, RED[3])) is None
    assert probe(fake, red=None) is None
    assert fake.runs == [], fake.runs

@case
def unsplittable_file_gives_none():
    fake = Fake(depends, groups=None)
    assert probe(fake) is None and fake.runs == []

@case
def setting_off_gives_none_and_no_run():
    fake = Fake(depends)
    assert probe(fake, setting=False) is None
    assert fake.runs == [], fake.runs

@case
def flake_green_alone_on_confirm_gives_none():
    fake = Fake(lambda lines, line: True)
    assert probe(fake) is None
    assert len(fake.runs) == 2, fake.runs

@case
def confirm_must_name_the_red_test():

    def fault(n):
        if n == 1:
            return StepResult(True, "  3 passed", ran=3, failed_tests=[])
        return StepResult(False, "Timed out", ran=-1, failed_tests=[(NAME, 20, 5, "REQ-1 › edit the note")])
    assert probe(Fake(depends, fault=fault)) is None

@case
def no_confirm_when_disabled():
    fake = Fake(depends)
    found = probe(fake, confirm_alone=False)
    assert found is not None and len(fake.runs) == 1, (found, fake.runs)

@case
def no_time_gives_none_and_no_run():
    fake = Fake(depends)
    assert probe(fake, time_limit=5) is None
    assert fake.runs == [], fake.runs

@case
def located_by_unique_title_when_line_unknown():
    found = probe(Fake(depends), red=(NAME, 0, 0, RED[3]))
    assert found is not None and found["line"] == 30, found

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=3)
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
