from __future__ import annotations

import io
import sys
import tempfile
import time
import traceback
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import fsutil, outsider, verify
from agent.verify import FAILED_FILES_HEAD, NOT_RUN_HEAD, StepResult

CASES = []

def case(function):
    CASES.append(function)
    return function

def quiet(_line: str) -> None:
    pass

class Once:

    def __init__(self, *results: StepResult):
        self.results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, project_dir, spec_files, log=print, **kw):
        self.calls.append(list(spec_files))
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]

    def __enter__(self):
        self.saved = (verify._run_e2e_once, verify.isolation_wanted)
        verify._run_e2e_once = self
        verify.isolation_wanted = lambda: True
        return self

    def __exit__(self, *exc):
        verify._run_e2e_once, verify.isolation_wanted = self.saved
        return False

SPECS = ["backend/test-e2e/A.spec.js", "backend/test-e2e/B.spec.js", "backend/test-e2e/C.spec.js"]
INFRA = "backend: express (declared) is not installed"
CODE_A = StepResult(False, FAILED_FILES_HEAD + "A.spec.js\n  1) A.spec.js:4:1 › a\n    expect(received).toBe(expected)\n"
                    "    Expected: 2\n    Received: 3\n  1 failed\n    test-e2e/A.spec.js:4:1 › a ───\n", ran=1,
                    failed_tests=[("A.spec.js", 4, 1, "a")])
DOWN_B = StepResult(False, "backend did not become ready for e2e\n" + verify.SERVER_DOWN_HEAD
                    + "\n  | Error: Cannot find module 'express'\n", infra=INFRA)

def names_on(output: str, head: str) -> list[str]:
    for line in output.splitlines():
        if line.startswith(head):
            return [n.strip() for n in line[len(head):].split(",") if n.strip()]
    return []

@case
def a_code_failure_before_a_server_that_did_not_start_keeps_its_evidence():
    with Once(CODE_A, DOWN_B) as once:
        result = verify.run_e2e(Path("."), SPECS, log=quiet)
    assert len(once.calls) == 2, once.calls
    assert not result.ok and not result.skipped
    assert result.output.startswith(FAILED_FILES_HEAD + "A.spec.js\n"), result.output[:200]
    assert verify.named_failures(result.output) == ["A.spec.js"]
    assert names_on(result.output, NOT_RUN_HEAD) == ["B.spec.js", "C.spec.js"], result.output[:300]
    assert "Received: 3" in result.output and "test-e2e/A.spec.js:4:1 › a" in result.output
    assert "Cannot find module 'express'" in result.output
    assert result.infra == "", result.infra
    assert result.ran == 1, result.ran

@case
def the_callers_read_it_as_a_red_file_and_files_not_run():
    from agent.codegen import app_did_not_start
    from agent.shared_sweep import fresh_green_names
    with Once(CODE_A, DOWN_B):
        result = verify.run_e2e(Path("."), SPECS, log=quiet)
    assert not app_did_not_start(result.output)
    assert fresh_green_names(SPECS, result.ok, result.output) == set()
    assert any(mark in result.output for mark in verify.APP_DOWN_MARKS)

@case
def infra_stays_only_when_every_red_part_carries_it():
    red_infra_a = StepResult(False, FAILED_FILES_HEAD + "A.spec.js\nboom", ran=1, infra=INFRA)
    with Once(red_infra_a, DOWN_B):
        assert verify.run_e2e(Path("."), SPECS, log=quiet).infra == INFRA
    with Once(red_infra_a, CODE_A):
        assert verify.run_e2e(Path("."), SPECS, log=quiet).infra == ""

@case
def a_server_down_for_the_code_stays_one_global_fault_as_before():

    down_code = StepResult(False, "backend did not become ready for e2e\n  | SyntaxError in server.js\n")
    with Once(CODE_A, down_code):
        result = verify.run_e2e(Path("."), SPECS, log=quiet)
    assert result.output == down_code.output + "\nSpec files that failed before this: A.spec.js", result.output
    assert result.infra == "" and not result.ok and verify.named_failures(result.output) == []

@case
def with_no_earlier_red_file_the_not_ready_result_is_returned_as_before():
    green = StepResult(True, "1 passed", ran=1)
    with Once(green, DOWN_B) as once:
        result = verify.run_e2e(Path("."), SPECS, log=quiet)
    assert len(once.calls) == 2
    assert result.output == DOWN_B.output and result.infra == INFRA and not result.ok, result
    with Once(DOWN_B) as once:
        result = verify.run_e2e(Path("."), SPECS, log=quiet)
    assert len(once.calls) == 1 and result.output == DOWN_B.output and result.infra == INFRA

class ReplaceFails:

    def __enter__(self):
        self.saved = (fsutil.os.replace, fsutil.REPLACE_RETRY_SEC)

        def refuse(src, dst):
            raise PermissionError(13, "Access is denied", str(dst))

        fsutil.os.replace = refuse
        fsutil.REPLACE_RETRY_SEC = ()
        return self

    def __exit__(self, *exc):
        fsutil.os.replace, fsutil.REPLACE_RETRY_SEC = self.saved
        return False

@case
def a_helper_that_cannot_be_replaced_is_an_infrastructure_failure_not_a_crash():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / outsider.HELPER_PATH
        target.parent.mkdir(parents=True)
        target.write_text("// the helper of an older version\n", encoding="utf-8")
        buffer = io.StringIO()
        with ReplaceFails(), redirect_stdout(buffer):
            outsider.ensure_helper(root)
        assert target.read_text(encoding="utf-8") == "// the helper of an older version\n"
        assert "infrastructure failure" in buffer.getvalue() and outsider.HELPER_PATH in buffer.getvalue(), buffer.getvalue()
        assert [p.name for p in target.parent.iterdir()] == ["outsider.js"]
        lines: list[str] = []
        with ReplaceFails():
            outsider.ensure_helper(root, log=lines.append)
        assert len(lines) == 1 and "could not be rewritten" in lines[0], lines

@case
def with_no_helper_on_disk_the_failure_is_raised_as_before():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        raised = False
        with ReplaceFails():
            try:
                outsider.ensure_helper(root, log=quiet)
            except OSError:
                raised = True
        assert raised
        assert not (root / outsider.HELPER_PATH).exists()

@case
def the_helper_is_still_written_and_left_alone_when_current():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / outsider.HELPER_PATH
        outsider.ensure_helper(root, log=quiet)
        assert target.read_text(encoding="utf-8") == outsider.HELPER_JS
        with ReplaceFails():
            lines: list[str] = []
            outsider.ensure_helper(root, log=lines.append)
            assert lines == []
        target.write_text("stale", encoding="utf-8")
        outsider.ensure_helper(root, log=quiet)
        assert target.read_text(encoding="utf-8") == outsider.HELPER_JS

def main() -> int:
    started = time.time()
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} r50y cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
