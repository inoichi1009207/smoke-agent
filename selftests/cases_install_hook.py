from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

NL = chr(10)
CASES = []
NODE = {"id": "REQ-X", "name": "n", "description": "d"}
INFRA_MARK = "vite: not found"
INFRA_TEXT = "FRONTEND BUILD FAILED" + NL + "> frontend@0.0.0 build" + NL + "> vite build" + NL + NL + "sh: 1: vite: not found" + NL
INFRA_WHY = "frontend: a program the command runs was not found"
WAIT = 30.0

def case(function):
    CASES.append(function)
    return function

def infra_outcome():
    from agent.testrun import TestOutcome

    return TestOutcome(False, INFRA_TEXT, infra=INFRA_WHY)

def plain_outcome(text: str, passed: bool = False):
    from agent.testrun import TestOutcome

    return TestOutcome(passed, text)

def ordinary_text() -> str:
    import cases_engine142 as e142

    return e142.two_tests("Error: one-0", "Error: two-0")

class Run:

    def __init__(self, checks, install=None, max_rounds: int = 3, extra_rounds: int = 0, first=None):
        import cases_engine as ce
        import cases_engine142 as e142
        from agent import verify
        from agent.codegen import CodegenImplementer

        self.calls: list[str] = []
        self.prompts: list[str] = []
        self.checks = 0
        self.slept: list[float] = []
        self.real_sleeps: list[float] = []
        self.lines: list[str] = []
        pending = list(checks)
        run = self

        class Model:
            last_finish_reason, model = "stop", "glm-test"

            def chat(self, messages, **opts):
                tag = str(opts.get("tag", ""))
                run.calls.append(tag)
                run.prompts.append(NL.join(str(m.get("content", "")) for m in messages))
                if ":diagnose" in tag:
                    return {"content": "frontend/src/pages/Page1.tsx -- render the page the test expects"}
                n = len(run.calls)
                return {"content": ce.block(f"frontend/src/pages/Page{n}.tsx", f"export default function Page{n}() {{ return {n}; }}")}

        def check():
            run.checks += 1
            return pending.pop(0) if len(pending) > 1 else pending[0]

        settle = getattr(verify, "settle_install_failure", None)
        saved_kw = dict(settle.__kwdefaults__ or {}) if settle is not None else None
        saved_wait = verify.install_retry_wait
        saved_sleep = time.sleep
        saved_broken = CodegenImplementer.repair_mode_broken
        with tempfile.TemporaryDirectory() as tmp, e142.Settings(repair_thinking=False, repair_diagnosis=True):
            project = Path(tmp)
            key = str((project / "frontend").resolve())
            verify._INSTALL_STATE.pop(key, None)
            try:
                if settle is not None:
                    settle.__kwdefaults__["sleep"] = lambda seconds: run.slept.append(seconds)
                time.sleep = lambda seconds: run.real_sleeps.append(seconds)
                CodegenImplementer.repair_mode_broken = False
                if install == "used_up":
                    verify._install_state(project / "frontend")["tries"] = verify.INSTALL_RETRY_LIMIT
                    assert verify.install_retry_wait(project, "frontend") is None
                elif install == "due":
                    verify.install_retry_wait = lambda project_dir, package: WAIT
                implementer = CodegenImplementer(Model(), project, check, max_rounds=max_rounds, extra_rounds=extra_rounds,
                                                 log=self.lines.append)
                self.result = implementer.implement(dict(NODE), dict(e142.SX), first or plain_outcome("RED as expected"))
                self.on_disk = {p: (project / p).is_file() for p in self.result.written}
            finally:
                if settle is not None:
                    settle.__kwdefaults__.clear()
                    settle.__kwdefaults__.update(saved_kw)
                verify.install_retry_wait = saved_wait
                time.sleep = saved_sleep
                CodegenImplementer.repair_mode_broken = saved_broken
                verify._INSTALL_STATE.pop(key, None)

    def seen(self, text: str) -> bool:
        return any(text in prompt for prompt in self.prompts)

    def why(self) -> str:
        r = self.result
        return (f"status={r.status!r} detail={r.detail[:120]!r} test_runs={r.test_runs} checks={self.checks} calls={self.calls} "
                f"slept={self.slept} log_tail={self.lines[-4:]}")

def green_baseline() -> Run:
    return Run([plain_outcome("E2E PASSED", passed=True)])

@case
def a_infra_with_installs_used_up_ends_the_batch_without_a_model_call():
    base = green_baseline()
    assert base.result.status == "passed" and base.calls == ["codegen:REQ-X:gen0"], base.why()
    run = Run([infra_outcome()], install="used_up")
    assert run.result.status == "budget", run.why()
    assert run.result.detail.startswith("infrastructure failure, not the code"), run.why()
    assert INFRA_WHY in run.result.detail, run.why()
    assert run.calls == base.calls, run.why()
    assert not run.seen(INFRA_MARK) and not run.seen("FRONTEND BUILD FAILED"), run.why()
    assert run.checks == 1 and run.slept == [] and run.real_sleeps == [], run.why()
    assert run.result.test_runs == 1, run.why()

    assert run.result.written and all(run.on_disk.values()), (run.result.written, run.on_disk)

@case
def a2_infra_with_the_install_due_is_rechecked_a_bounded_number_of_times_then_ends_the_same():
    from agent import verify

    run = Run([infra_outcome()], install="due", max_rounds=4)
    assert run.result.status == "budget" and run.result.detail.startswith("infrastructure failure, not the code"), run.why()
    assert run.calls == ["codegen:REQ-X:gen0"], run.why()
    assert not run.seen(INFRA_MARK), run.why()
    assert run.checks == 1 + verify.INFRA_RECHECKS_MAX, run.why()
    assert run.slept == [WAIT] * verify.INFRA_RECHECKS_MAX and run.real_sleeps == [], run.why()

@case
def b_infra_then_green_after_the_install_is_a_pass_without_a_round():
    base = green_baseline()
    run = Run([infra_outcome(), plain_outcome("E2E PASSED", passed=True)], install="due")
    assert run.result.status == "passed", run.why()
    assert run.calls == base.calls, (run.why(), base.why())
    assert run.result.test_runs == base.result.test_runs == 1, (run.why(), base.why())
    assert run.checks == 2 and base.checks == 1, (run.why(), base.why())
    assert run.slept == [WAIT] and run.real_sleeps == [], run.why()
    assert not run.seen(INFRA_MARK), run.why()

@case
def c_infra_then_an_ordinary_failure_goes_on_like_an_ordinary_failure():
    text = ordinary_text()
    green = plain_outcome("E2E PASSED", passed=True)
    run = Run([infra_outcome(), plain_outcome(text), green], install="due")
    ordinary = Run([plain_outcome(text), green])
    assert run.slept == [WAIT] and run.real_sleeps == [], run.why()

    assert run.calls == ordinary.calls, (run.why(), ordinary.why())
    assert run.result.status == ordinary.result.status == "passed", (run.why(), ordinary.why())
    assert run.result.test_runs == ordinary.result.test_runs == 2, (run.why(), ordinary.why())

    after = run.calls[1:]
    assert after and all(":repair" in tag or ":diagnose" in tag for tag in after), run.why()
    repair = run.prompts[run.calls.index("codegen:REQ-X:repair1")]
    assert "Error: one-0" in repair, repair[-1500:]
    assert not run.seen(INFRA_MARK) and not run.seen(INFRA_WHY), run.why()

@case
def d_control_the_same_failure_without_infra_is_sent_to_the_model():
    run = Run([plain_outcome(INFRA_TEXT)], max_rounds=1)
    assert len(run.calls) > 1 and any(":repair" in tag for tag in run.calls[1:]), run.why()
    assert run.seen(INFRA_MARK), run.why()
    assert not run.result.detail.startswith("infrastructure failure"), run.why()

@case
def e_an_infra_first_check_is_not_shown_to_the_generation():
    base = green_baseline()
    run = Run([plain_outcome("E2E PASSED", passed=True)], first=infra_outcome())
    assert run.result.status == "passed" and run.calls == base.calls, (run.why(), base.why())
    assert not run.seen(INFRA_MARK) and not run.seen("FRONTEND BUILD FAILED"), run.why()
    assert run.seen("The checks could not run yet"), run.why()
    assert any("not shown to the model" in line for line in run.lines), run.why()

def main() -> int:
    import agent.codegen as codegen

    if not hasattr(codegen, "settle_install_failure"):
        print("skipped  codegen has no install hook")
        return 0
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
