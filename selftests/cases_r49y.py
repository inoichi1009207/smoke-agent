from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import verify
from agent.verify import StepResult

CASES = []
OFFSET = 50_000.0
E404 = "npm error code E404\n"

def case(function):
    CASES.append(function)
    return function

def quiet(_line: str) -> None:
    pass

class Fake:

    def __init__(self, root: Path, install_sec: float = 100_000.0):
        self.root, self.install_sec = root, install_sec
        self.t = 1_000.0
        self.calls: list[dict] = []
        for package in ("frontend", "backend"):
            (root / package).mkdir(parents=True, exist_ok=True)
            deps = {"vite": "^7"} if package == "frontend" else {"express": "^5"}
            (root / package / "package.json").write_text(json.dumps({"name": package, "dependencies": deps}), encoding="utf-8")

    def run_cmd(self, args, cwd=None, timeout=None, **_kw):
        if args[1] != "install":
            raise AssertionError(f"unexpected command {args}")
        self.calls.append({"package": Path(cwd).name, "start": self.t, "timeout": timeout})
        if self.install_sec > timeout:
            self.t += timeout
            return 124, "[timeout]"
        self.t += self.install_sec
        return 1, E404

    def clock_now(self) -> float:
        return self.t + OFFSET

    def sleep(self, seconds: float) -> None:
        self.t += seconds

    def __enter__(self):
        self.saved = (verify.run_cmd, verify._now, verify.clock.now, verify._GLOBAL_DEADLINE[0], verify._RUN_DEADLINE[0])
        verify.run_cmd = self.run_cmd
        verify._now = lambda: self.t
        verify.clock.now = self.clock_now
        verify._INSTALL_STATE.clear()
        verify.set_global_deadline(None)
        return self

    def __exit__(self, *exc):
        verify.run_cmd, verify._now, verify.clock.now = self.saved[:3]
        verify._GLOBAL_DEADLINE[0], verify._RUN_DEADLINE[0] = self.saved[3:]
        verify._INSTALL_STATE.clear()
        return False

def due(fake: Fake, package: str = "backend", tries: int = 1) -> dict:
    state = verify._install_state(fake.root / package)
    state["tries"], state["last"] = tries, fake.t - verify.INSTALL_RETRY_INTERVAL
    return state

def backend_check(fake: Fake, lines: list):
    def check():
        note = verify.ensure_installed(fake.root, "backend", log=lines.append)
        return SimpleNamespace(passed=False, output="backend did not become ready for e2e\n" + note,
                               infra="backend: the dependencies are not installed")
    return check

def red_infra():
    return SimpleNamespace(passed=False, output="backend did not become ready for e2e", infra="backend: the dependencies are not installed")

@case
def a_recheck_backend_install_ends_by_the_rechecks_own_end():

    with tempfile.TemporaryDirectory() as tmp, Fake(Path(tmp)) as fake:
        verify.set_global_deadline(fake.clock_now() + 20_000)
        due(fake)
        lines: list[str] = []
        end = fake.clock_now() + 100
        verify.settle_install_failure(backend_check(fake, lines), red_infra(), fake.root, deadline=end, log=quiet, sleep=fake.sleep)
        assert fake.calls, lines
        first = fake.calls[0]
        assert first["start"] + OFFSET + first["timeout"] <= end + 1e-6, (first, end - OFFSET)
        assert first["timeout"] == 100, fake.calls

        room = verify.install_room()
        assert room is not None and abs(room - (20_000 - (fake.t - 1_000.0) - verify.INSTALL_CLOSING_RESERVE_SEC)) < 1e-6, room

@case
def too_little_recheck_time_starts_no_install_and_spends_no_try():
    with tempfile.TemporaryDirectory() as tmp, Fake(Path(tmp)) as fake:
        verify.set_global_deadline(fake.clock_now() + 20_000)
        state = due(fake)
        lines: list[str] = []
        verify.settle_install_failure(backend_check(fake, lines), red_infra(), fake.root, deadline=fake.clock_now() + 30,
                                      log=quiet, sleep=fake.sleep)
        assert fake.calls == [], fake.calls
        assert state["tries"] == 1 and any("not started" in line for line in lines), (state, lines)

@case
def the_runs_budget_still_binds_inside_a_recheck():

    with tempfile.TemporaryDirectory() as tmp, Fake(Path(tmp)) as fake:
        verify.set_global_deadline(fake.clock_now() + verify.INSTALL_CLOSING_RESERVE_SEC + 300)
        due(fake)
        verify.settle_install_failure(backend_check(fake, []), red_infra(), fake.root, log=quiet, sleep=fake.sleep, max_wait=1200)
        assert [c["timeout"] for c in fake.calls][:1] == [300], fake.calls

@case
def install_room_is_the_tighter_of_the_two_bounds():
    with tempfile.TemporaryDirectory() as tmp, Fake(Path(tmp)) as fake:
        reserve = verify.INSTALL_CLOSING_RESERVE_SEC
        assert verify.install_room() is None
        verify.set_global_deadline(fake.clock_now() + 5_000)
        assert verify.install_room() == 5_000 - reserve and verify.install_room(0.0) == 5_000
        verify._GLOBAL_DEADLINE[0] = fake.clock_now() + 400
        assert verify.install_room() == 400 and verify.install_room(0.0) == 400
        verify._GLOBAL_DEADLINE[0] = fake.clock_now() + 5_000
        verify._RUN_DEADLINE[0] = None
        verify._GLOBAL_DEADLINE[0] = fake.clock_now() + 250
        assert verify.install_room() == 250

@case
def a_slow_try_inside_a_recheck_is_cut_to_the_rechecks_end_too():

    with tempfile.TemporaryDirectory() as tmp, Fake(Path(tmp)) as fake:
        verify.set_global_deadline(fake.clock_now() + 20_000)
        state = verify._install_state(fake.root / "backend")
        state["tries"], state["last"] = verify.INSTALL_RETRY_LIMIT, fake.t - verify.INSTALL_SLOW_INTERVAL
        assert verify.install_retry_wait(fake.root, "backend") is None
        verify._GLOBAL_DEADLINE[0] = fake.clock_now() + 200
        verify.ensure_installed(fake.root, "backend", log=quiet)
        assert [c["timeout"] for c in fake.calls] == [200], fake.calls

@case
def outside_a_recheck_nothing_changes():
    with tempfile.TemporaryDirectory() as tmp, Fake(Path(tmp)) as fake:
        verify.set_global_deadline(fake.clock_now() + 20_000)
        due(fake)
        verify.ensure_installed(fake.root, "backend", log=quiet)
        assert [c["timeout"] for c in fake.calls] == [900], fake.calls

class Once:

    def __init__(self, *results: StepResult):
        self.results = list(results)

    def __call__(self, project_dir, spec_files, log=print, **kw):
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]

    def __enter__(self):
        self.saved = (verify._run_e2e_once, verify.isolation_wanted)
        verify._run_e2e_once = self
        verify.isolation_wanted = lambda: True
        return self

    def __exit__(self, *exc):
        verify._run_e2e_once, verify.isolation_wanted = self.saved
        return False

SPEC_A, SPEC_B = "backend/test-e2e/A.spec.js", "backend/test-e2e/B.spec.js"
INFRA = "backend: module x not found (a declared dependency)"

@case
def run_e2e_one_file_wrapper_keeps_infra():
    with Once(StepResult(False, "a crash that names no test", ran=0, infra=INFRA)):
        result = verify.run_e2e(Path("."), [SPEC_A], log=quiet)
    assert not result.ok and result.output.startswith(verify.FAILED_FILES_HEAD) and result.infra == INFRA, result
    with Once(StepResult(False, "a crash that names no test", ran=0)):
        assert verify.run_e2e(Path("."), [SPEC_A], log=quiet).infra == ""

@case
def two_files_that_ran_nothing_keep_infra_only_when_both_carry_it():
    with Once(StepResult(False, "nothing ran", ran=0, infra=INFRA)):
        result = verify.run_e2e(Path("."), [SPEC_A, SPEC_B], log=quiet)
    assert "first two spec files ran no test" in result.output and result.infra == INFRA, result
    with Once(StepResult(False, "nothing ran", ran=0, infra=INFRA), StepResult(False, "nothing ran", ran=0)):
        result = verify.run_e2e(Path("."), [SPEC_A, SPEC_B], log=quiet)
    assert "first two spec files ran no test" in result.output and result.infra == "", result

@case
def the_files_aggregate_is_infra_only_when_every_red_file_is():
    red = StepResult(False, verify.FAILED_FILES_HEAD + "A.spec.js\nboom", ran=1, infra=INFRA)
    red_b = StepResult(False, verify.FAILED_FILES_HEAD + "B.spec.js\nboom", ran=1, infra=INFRA)
    code = StepResult(False, verify.FAILED_FILES_HEAD + "B.spec.js\nexpect(received)", ran=1)
    green = StepResult(True, "1 passed", ran=1)
    with Once(red, red_b):
        assert verify.run_e2e(Path("."), [SPEC_A, SPEC_B], log=quiet).infra == INFRA
    with Once(red, code):
        assert verify.run_e2e(Path("."), [SPEC_A, SPEC_B], log=quiet).infra == ""
    with Once(red, green):
        assert verify.run_e2e(Path("."), [SPEC_A, SPEC_B], log=quiet).infra == INFRA
    with Once(green, green):
        result = verify.run_e2e(Path("."), [SPEC_A, SPEC_B], log=quiet)
        assert result.ok and result.infra == "", result

@case
def the_isolated_aggregate_is_infra_only_when_every_red_test_is():
    groups = [(3, [(3, 1, "a")]), (9, [(9, 1, "b")])]
    red = StepResult(False, "boom", ran=1, failed_tests=[], infra=INFRA)
    code = StepResult(False, "expect(received)", ran=1, failed_tests=[])
    with Once(red, red):
        assert verify.run_tests_isolated(Path("."), SPEC_A, groups, log=quiet).infra == INFRA
    with Once(red, code):
        assert verify.run_tests_isolated(Path("."), SPEC_A, groups, log=quiet).infra == ""
    with Once(code, StepResult(True, "1 passed", ran=1)):
        assert verify.run_tests_isolated(Path("."), SPEC_A, groups, log=quiet).infra == ""

@case
def common_infra_never_turns_a_code_failure_into_an_install_fault():
    ok = StepResult(True, "")
    assert verify._common_infra([]) == "" and verify._common_infra([ok]) == ""
    assert verify._common_infra([ok, StepResult(False, "", infra=INFRA)]) == INFRA
    assert verify._common_infra([StepResult(False, "", infra=INFRA), StepResult(False, "")]) == ""

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
    print(f"{len(CASES) - failed}/{len(CASES)} r49y cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
