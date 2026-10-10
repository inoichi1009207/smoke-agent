from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "selftests"))

from agent import testrun, verify

CASES = []
E404 = ("npm error code E404\nnpm error 404 Not Found - GET "
        "https://repo.huaweicloud.com/repository/npm/std-env/-/std-env-4.3.0.tgz\n")
VITE_127 = "> frontend@0.0.0 build\n> vite build\n\nsh: 1: vite: not found\n"
ROLLUP = "error during build:\n[vite]: Rollup failed to resolve import \"./x\" from \"src/App.tsx\".\n"
OFFSET = 5_000_000.0
BINS = {"vite": {"vite": "bin/vite.js"}, "vitest": {"vitest": "vitest.mjs"}}
PROGRAM_NOTE = "frontend: a program the command runs was not found"

def case(function):
    CASES.append(function)
    return function

def quiet(_message: str) -> None:
    pass

def make_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifests = {
        "frontend": {"name": "frontend", "scripts": {"build": "vite build", "test": "vitest run"},
                     "devDependencies": {"vite": "^7.2.4", "vitest": "^4.0.16"}},
        "backend": {"name": "backend", "scripts": {"start": "node server.js"}, "dependencies": {"express": "^5.2.1"}},
    }
    for package, manifest in manifests.items():
        (root / package).mkdir(parents=True, exist_ok=True)
        (root / package / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root

def lay_packages(package_dir: Path, link: bool = True) -> None:
    manifest = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    for name in [*manifest.get("dependencies", {}), *manifest.get("devDependencies", {})]:
        (package_dir / "node_modules" / name).mkdir(parents=True, exist_ok=True)
        body = {"name": name, **({"bin": BINS[name]} if name in BINS else {})}
        (package_dir / "node_modules" / name / "package.json").write_text(json.dumps(body), encoding="utf-8")
        if link:
            for program in BINS.get(name, {}):
                (package_dir / "node_modules" / ".bin").mkdir(parents=True, exist_ok=True)
                (package_dir / "node_modules" / ".bin" / program).write_text("#!/bin/sh\n", encoding="utf-8")

def complete(package_dir: Path) -> None:
    lay_packages(package_dir)
    verify.mark_install_complete(package_dir)

class Timed:

    def __init__(self, root: Path, installs=(), builds=(), leave: bool = False) -> None:
        self.root, self.installs, self.builds, self.leave = root, list(installs), list(builds), leave
        self.t = 1000.0
        self.calls: list[dict] = []
        self.slept: list[float] = []

    def run_cmd(self, args, *, cwd, timeout=600, env=None):
        cwd = Path(cwd)
        if args[1] == "install":
            kind = "install"
        elif list(args[1:3]) == ["run", "build"]:
            kind = "build"
        else:
            raise AssertionError(f"unexpected command {args}")
        self.calls.append({"kind": kind, "package": cwd.name, "timeout": timeout, "at": self.t, "args": list(args[1:])})
        if kind == "install":
            duration, ok = self.installs.pop(0) if self.installs else (1.0, False)
            code, output = (0, "added 365 packages") if ok else (1, E404)
        else:
            duration, code, output = self.builds.pop(0) if self.builds else (1.0, 0, "built")
        if duration > timeout:
            self.t += timeout
            return 124, f"[timeout after {timeout}s]"
        self.t += duration
        if kind == "install" and (ok or self.leave):
            lay_packages(cwd, link=ok)
        return code, output

    def clock_now(self) -> float:
        return self.t + OFFSET

    def kinds(self) -> list[str]:
        return [call["kind"] for call in self.calls]

    def installs_made(self, package: str = "frontend") -> int:
        return sum(1 for call in self.calls if call["kind"] == "install" and call["package"] == package)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds

    def __enter__(self):
        self.saved = (verify.run_cmd, verify._now, verify.clock.now, verify._GLOBAL_DEADLINE[0])
        verify.run_cmd = self.run_cmd
        verify._now = lambda: self.t
        verify.clock.now = self.clock_now
        verify._INSTALL_STATE.clear()
        verify.set_global_deadline(None)
        return self

    def __exit__(self, *exc):
        verify.run_cmd, verify._now, verify.clock.now = self.saved[:3]
        verify.set_global_deadline(None)
        verify._INSTALL_STATE.clear()
        return False

def failed_start(fake: Timed, ago: float, leave: bool = False) -> None:
    fake.installs[0:0] = [(1.0, False)] * 3
    saved, fake.leave = fake.leave, leave
    step = verify.install_with_retries(fake.root, "frontend", log=quiet, sleep=lambda s: None)
    fake.leave = saved
    assert not step.ok
    fake.t += ago
    fake.calls.clear()

def within_budget(fake: Timed, t0: float, budget: float, label: str = "") -> None:
    end = t0 + budget
    assert fake.t - t0 <= budget + 1, f"{label}: build_frontend took {fake.t - t0} s > {budget} s; calls={fake.calls}"
    for call in fake.calls:
        left = end - call["at"]
        assert call["timeout"] <= left + 1e-6, f"{label}: timeout {call['timeout']} > {left} s left; calls={fake.calls}"

@case
def worst_path_build_127_slow_install_slow_rebuild_fits_in_600():

    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp)), installs=[(400.0, True)],
                                                      builds=[(1.0, 127, VITE_127), (10_000.0, 0, "built")]) as fake:
        failed_start(fake, 200)
        t0 = fake.t
        step = verify.build_frontend(fake.root, log=quiet)
        assert fake.kinds() == ["build", "install", "build"], fake.calls
        within_budget(fake, t0, verify.BUILD_FRONTEND_WORST_SEC, "worst path")
        assert fake.calls[0]["timeout"] == 600 and fake.calls[1]["timeout"] <= 600 - 1 - verify.BUILD_ROOM_SEC
        assert not step.ok and step.infra.startswith("frontend: "), step.infra

@case
def slow_failing_pre_build_install_is_cut_to_leave_the_build_its_room():
    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp)), installs=[(10_000.0, False)],
                                                      builds=[(1.0, 127, VITE_127)]) as fake:
        failed_start(fake, 700)
        t0 = fake.t
        step = verify.build_frontend(fake.root, log=quiet)
        assert fake.kinds() == ["install", "build"], fake.calls
        assert fake.calls[0]["timeout"] == int(verify.BUILD_FRONTEND_WORST_SEC - verify.BUILD_ROOM_SEC)
        assert fake.calls[1]["timeout"] <= verify.BUILD_ROOM_SEC
        within_budget(fake, t0, verify.BUILD_FRONTEND_WORST_SEC, "slow pre-build install")
        assert not step.ok and step.infra == PROGRAM_NOTE, step.infra

@case
def build_frontend_budget_sweep():
    states = ("complete", "failed_due", "failed_recent")
    pre_installs = [(300.0, False), (10_000.0, False), (100.0, True)]
    firsts = [(1.0, 127, VITE_127), (130.0, 127, VITE_127), (1.0, 0, "built"), (10_000.0, 0, "built")]
    retries = [(100.0, True), (400.0, True), (10_000.0, True), (1.0, False)]
    rebuilds = [(1.0, 0, "built"), (10_000.0, 0, "built"), (1.0, 127, VITE_127)]
    limits = [(None, None), (300.0, None), (None, 250.0), (450.0, 200.0), (130.0, None)]
    seen: set[tuple[str, ...]] = set()
    count = 0
    with tempfile.TemporaryDirectory() as tmp:
        for state in states:
            for pre in (pre_installs if state == "failed_due" else [None]):
                for first in firsts:
                    for retry in retries:
                        for rebuild in rebuilds:
                            for own, overall in limits:
                                count += 1
                                root = make_project(Path(tmp) / f"c{count}")
                                installs = ([pre] if pre else []) + [retry]
                                with Timed(root, installs=installs, builds=[first, rebuild]) as fake:
                                    try:
                                        if state == "complete":
                                            complete(root / "frontend")
                                        else:
                                            failed_start(fake, 700 if state == "failed_due" else 200)
                                        t0 = fake.t
                                        deadline = fake.clock_now() + own if own is not None else None
                                        if overall is not None:
                                            verify.set_global_deadline(fake.clock_now() + overall)
                                        step = verify.build_frontend(root, log=quiet, deadline=deadline)
                                        budget = min(x for x in (verify.BUILD_FRONTEND_WORST_SEC, own, overall) if x is not None)
                                        label = f"{state} pre={pre} first={first[:2]} retry={retry} rebuild={rebuild[:2]} limits={own},{overall}"
                                        within_budget(fake, t0, budget, label)
                                        assert not (step.ok and step.infra), label
                                        seen.add(tuple(fake.kinds()))
                                    finally:
                                        verify.set_global_deadline(None)

    for path in (("build", "install", "build"), ("install", "build"), ("build",), ("install", "build", "install", "build")):
        assert path in seen, (path, seen)

@case
def an_own_deadline_bounds_the_whole_call():
    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp)), installs=[(100.0, True)],
                                                      builds=[(1.0, 127, VITE_127), (10_000.0, 0, "built")]) as fake:
        failed_start(fake, 200)
        t0 = fake.t
        step = verify.build_frontend(fake.root, log=quiet, deadline=fake.clock_now() + 300)
        assert fake.kinds() == ["build", "install", "build"], fake.calls
        within_budget(fake, t0, 300, "deadline")
        assert fake.calls[0]["timeout"] == 300 and not step.ok and step.infra.startswith("frontend: ")

@case
def the_global_deadline_bounds_the_whole_call():
    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp)), installs=[(50.0, True)],
                                                      builds=[(1.0, 127, VITE_127), (10_000.0, 0, "built")]) as fake:
        try:
            failed_start(fake, 200)
            t0 = fake.t
            verify.set_global_deadline(fake.clock_now() + 280)
            step = verify.build_frontend(fake.root, log=quiet)
            assert fake.kinds() == ["build", "install", "build"], fake.calls
            within_budget(fake, t0, 280, "global deadline")
            assert fake.calls[0]["timeout"] == 280 and not step.ok and step.infra.startswith("frontend: ")
        finally:
            verify.set_global_deadline(None)
    assert verify._GLOBAL_DEADLINE[0] is None

@case
def a_deadline_already_past_runs_nothing():
    for state in ("complete", "failed_due"):
        for which in ("own", "global"):
            with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp)), installs=[(1.0, True)] * 3) as fake:
                try:
                    if state == "complete":
                        complete(fake.root / "frontend")
                    else:
                        failed_start(fake, 700)
                    past = fake.clock_now() - 10
                    if which == "global":
                        verify.set_global_deadline(past)
                    step = verify.build_frontend(fake.root, log=quiet, deadline=past if which == "own" else None)
                    assert fake.calls == [], (state, which, fake.calls)
                    assert not step.ok and step.infra, (state, which, step.infra)
                finally:
                    verify.set_global_deadline(None)

@case
def an_install_is_not_started_with_less_than_its_minimum():
    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp)), installs=[(1.0, True)],
                                                      builds=[(1.0, 1, "some error")]) as fake:
        failed_start(fake, 700)
        verify.build_frontend(fake.root, log=quiet, deadline=fake.clock_now() + 200)
        assert fake.kinds() == ["build"] and fake.calls[0]["timeout"] == 200, fake.calls

@case
def a_normal_build_with_a_complete_install_is_one_command_of_600_s():
    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp)), builds=[(30.0, 0, "built")]) as fake:
        complete(fake.root / "frontend")
        step = verify.build_frontend(fake.root, log=quiet)
        assert step.ok and step.infra == "" and len(fake.calls) == 1, fake.calls
        assert fake.calls[0]["kind"] == "build" and fake.calls[0]["timeout"] == 600

@case
def a_build_still_127_after_its_retry_is_infra():
    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp)), installs=[(5.0, True)],
                                                      builds=[(1.0, 127, VITE_127), (1.0, 127, VITE_127)]) as fake:
        complete(fake.root / "frontend")
        step = verify.build_frontend(fake.root, log=quiet)
        assert fake.kinds() == ["build", "install", "build"], fake.calls
        assert not step.ok and step.infra == PROGRAM_NOTE, step.infra

@case
def a_plain_code_failure_is_no_infra_even_without_the_marker():
    for variant in ("install fails", "tries used up", "marker present"):
        with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp)), installs=[(1.0, False)],
                                                          builds=[(1.0, 1, ROLLUP)]) as fake:
            lay_packages(fake.root / "frontend")
            if variant == "tries used up":
                verify._install_state(fake.root / "frontend")["tries"] = verify.INSTALL_RETRY_LIMIT
            if variant == "marker present":
                verify.mark_install_complete(fake.root / "frontend")
            step = verify.build_frontend(fake.root, log=quiet)
            assert fake.kinds()[-1] == "build" and fake.kinds().count("build") == 1, (variant, fake.calls)
            assert not step.ok and step.infra == "", (variant, step.infra)
            assert "Rollup failed to resolve import" in step.output

@case
def a_build_that_hangs_past_its_own_timeout_is_the_codes_fault():

    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp)), builds=[(10_000.0, 0, "built")]) as fake:
        complete(fake.root / "frontend")
        step = verify.build_frontend(fake.root, log=quiet)
        assert fake.kinds() == ["build"] and fake.calls[0]["timeout"] == 600
        assert not step.ok and step.infra == "", f"infra={step.infra!r}"

@case
def a_timeout_is_infra_only_when_the_build_was_cut_by_more_than_the_slack():

    assert verify.BUILD_CUT_SLACK_SEC == 30.0

    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp)), builds=[(10_000.0, 0, "built")]) as fake:
        complete(fake.root / "frontend")
        step = verify.build_frontend(fake.root, log=quiet, deadline=fake.clock_now() + 590)
        assert fake.calls[0]["timeout"] == 590 and not step.ok and step.infra == "", (fake.calls, step.infra)

    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp)), installs=[(200.0, True)],
                                                      builds=[(10_000.0, 0, "built")]) as fake:
        failed_start(fake, 700)
        step = verify.build_frontend(fake.root, log=quiet)
        assert fake.kinds() == ["install", "build"] and fake.calls[1]["timeout"] == 400, fake.calls
        assert not step.ok and step.infra.startswith("frontend: "), step.infra

    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp)), builds=[(10_000.0, 0, "built")]) as fake:
        complete(fake.root / "frontend")
        step = verify.build_frontend(fake.root, log=quiet, deadline=fake.clock_now() + 400)
        assert fake.calls[0]["timeout"] == 400 and not step.ok and step.infra.startswith("frontend: "), step.infra

@case
def test_outcome_carries_infra():
    assert testrun.TestOutcome(False, "x").infra == ""
    assert testrun.TestOutcome(False, "x", infra="frontend: y").infra == "frontend: y"
    assert verify.StepResult(False, "x").infra == ""

class Checks:
    def __init__(self, answers):
        self.answers, self.made = list(answers), 0

    def __call__(self):
        self.made += 1
        return self.answers.pop(0) if self.answers else testrun.TestOutcome(False, "still red", infra="frontend: again")

def red(infra: str = "frontend: a program the command runs was not found"):
    return testrun.TestOutcome(False, "red", infra=infra)

@case
def settle_returns_the_green_recheck_after_one_due_wait():
    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp))) as fake:
        verify._install_state(fake.root / "frontend")["last"] = fake.t - 500
        green = testrun.TestOutcome(True, "green")
        check = Checks([green])
        result = verify.settle_install_failure(check, red(), fake.root, log=quiet, sleep=fake.sleep)
        assert result is green and check.made == 1 and fake.slept == [100.0], (check.made, fake.slept)

@case
def settle_does_not_wait_when_the_retries_are_used_up():

    for last in (None, 0.0, -10_000.0):
        with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp))) as fake:
            state = verify._install_state(fake.root / "frontend")
            state["tries"] = verify.INSTALL_RETRY_LIMIT
            state["last"] = None if last is None else fake.t + last
            first, check = red(), Checks([testrun.TestOutcome(True, "green")])
            result = verify.settle_install_failure(check, first, fake.root, log=quiet, sleep=fake.sleep)
            assert result is first and check.made == 0 and fake.slept == [], (last, check.made, fake.slept)
    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp))) as fake:
        check = Checks([testrun.TestOutcome(True, "green")])

        verify._install_state(fake.root / "backend")["tries"] = verify.INSTALL_RETRY_LIMIT
        first = red("backend: module express not found (a declared dependency)")
        result = verify.settle_install_failure(check, first, fake.root, log=quiet, sleep=fake.sleep)
        assert result is first and check.made == 0 and fake.slept == []

@case
def settle_does_not_wait_past_the_deadline():
    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp))) as fake:
        verify._install_state(fake.root / "frontend")["last"] = fake.t - 500
        first, check = red(), Checks([testrun.TestOutcome(True, "green")])
        result = verify.settle_install_failure(check, first, fake.root, deadline=fake.clock_now() + 50, log=quiet,
                                               sleep=fake.sleep, max_wait=10_000)
        assert result is first and check.made == 0 and fake.slept == [], (check.made, fake.slept)

        verify._INSTALL_STATE.clear()
        state = verify._install_state(fake.root / "frontend")
        state["last"] = fake.t - 500
        check = Checks([])

        def install_then_check():
            state["last"] = fake.t - 500
            return check()

        result = verify.settle_install_failure(install_then_check, red(), fake.root, deadline=fake.clock_now() + 120,
                                               log=quiet, sleep=fake.sleep, max_wait=10_000)
        assert check.made == 1 and fake.slept == [100.0] and not result.passed, (check.made, fake.slept)

@case
def settle_is_bounded_in_checks_and_in_wait():

    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp))) as fake:
        state = verify._install_state(fake.root / "frontend")
        state["last"] = fake.t
        check = Checks([])

        def install_then_check():
            state["last"], state["tries"] = fake.t, state["tries"] + 1
            return check()

        result = verify.settle_install_failure(install_then_check, red(), fake.root, log=quiet, sleep=fake.sleep, max_wait=1200)
        assert sum(fake.slept) <= 1200 and check.made == 2 and not result.passed, (check.made, fake.slept)

    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp))) as fake:
        check = Checks([])
        result = verify.settle_install_failure(check, red(), fake.root, log=quiet, sleep=fake.sleep, max_wait=1200)
        assert check.made == verify.INFRA_RECHECKS_MAX and not result.passed and result.infra, check.made

    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp))) as fake:
        state = verify._install_state(fake.root / "frontend")
        check = Checks([])

        def due_in_100():
            state["last"] = fake.t - 500
            return check()

        state["last"] = fake.t - 500
        verify.settle_install_failure(due_in_100, red(), fake.root, log=quiet, sleep=fake.sleep, max_wait=250)
        assert sum(fake.slept) <= 250 and fake.slept == [100.0, 100.0] and check.made == 2, (fake.slept, check.made)

@case
def settle_returns_a_code_failure_untouched():
    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp))) as fake:
        first, check = testrun.TestOutcome(False, "AssertionError: expected 3"), Checks([testrun.TestOutcome(True, "g")])
        result = verify.settle_install_failure(check, first, fake.root, log=quiet, sleep=fake.sleep)
        assert result is first and check.made == 0 and fake.slept == []
        passed = testrun.TestOutcome(True, "green")
        assert verify.settle_install_failure(check, passed, fake.root, log=quiet, sleep=fake.sleep) is passed
        assert check.made == 0 and fake.slept == []

@case
def install_fault_table():
    with tempfile.TemporaryDirectory() as tmp:
        root = make_project(Path(tmp))
        manifest = json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8"))
        manifest["dependencies"] = {"@vitejs/plugin-react": "^5.0.0", "react": "^19.0.0"}
        (root / "frontend" / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
        faults = {
            "esm from node_modules": "Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'rollup' imported from /app/frontend/node_modules/vite/dist/node/chunks/dep-abc.js\n",
            "cjs require stack": "Error: Cannot find module '@rollup/rollup-linux-x64-gnu'\nRequire stack:\n- /app/frontend/node_modules/rollup/dist/native.js\n- /app/frontend/node_modules/rollup/dist/shared/rollup.js\n",
            "declared from src": "Error: Cannot find package 'react' imported from /app/frontend/src/main.tsx\n",
            "declared scoped": "failed to load config: Cannot find package '@vitejs/plugin-react' imported from /app/frontend/vite.config.ts\n",
            "windows esm": "Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'rollup' imported from C:\\app\\frontend\\node_modules\\vite\\dist\\node\\chunks\\dep.js\n",
            "windows cjs": "Error: Cannot find module 'esbuild'\nRequire stack:\n- C:\\app\\frontend\\node_modules\\vite\\dist\\node\\index.cjs\n",
            "exit 127": VITE_127,
            "absolute file in node_modules": "Error: Cannot find module '/app/frontend/node_modules/rollup/dist/native.js'\n",
            "npm optional bug": "Error: Cannot find module @rollup/rollup-linux-x64-gnu. npm has a bug related to optional dependencies (https://github.com/npm/cli/issues/4828).\n",
        }
        for label, output in faults.items():
            code = 127 if label == "exit 127" else 1
            assert verify.install_fault(root, "frontend", code, output), label
        not_faults = {
            "undeclared from src": "Error: Cannot find package 'lodash' imported from /app/frontend/src/App.tsx\n",
            "relative from src": "Error: Cannot find module './x' imported from /app/frontend/src/App.tsx\n",
            "relative from node_modules": "Error: Cannot find module './chunk.js' imported from /app/frontend/node_modules/vite/index.js\n",
            "absolute path": "Error: Cannot find module '/app/frontend/src/x' imported from /app/frontend/src/App.tsx\n",
            "windows absolute": "Error: Cannot find module 'C:\\app\\frontend\\src\\x' imported from C:\\app\\frontend\\src\\App.tsx\n",
            "undeclared cjs from src": "Error: Cannot find module 'lodash'\nRequire stack:\n- /app/frontend/src/util.js\n",
            "rollup relative": ROLLUP,
            "plain error": "TypeError: x is not a function\n",
        }
        for label, output in not_faults.items():
            assert verify.install_fault(root, "frontend", 1, output) == "", (label, verify.install_fault(root, "frontend", 1, output))

        assert verify.install_fault(root, "frontend", 0, faults["esm from node_modules"]) == ""

        assert verify.install_fault(root, "frontend", 1, "Error: Cannot find package 'lodash' imported from /app/frontend/src/node_modules_old/a.js\n") == ""

@case
def install_fault_a_subpath_of_a_declared_package_from_app_code_is_the_codes():

    with tempfile.TemporaryDirectory() as tmp:
        root = make_project(Path(tmp))
        manifest = json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8"))
        manifest["dependencies"] = {"react-router-dom": "^7.0.0", "@scope/pkg": "^1.0.0"}
        (root / "frontend" / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
        for specifier in ("react-router-dom/server", "vitest/config", "@scope/pkg/sub"):
            output = f"Error: Cannot find module '{specifier}' imported from /app/frontend/src/entry.tsx\n"
            assert verify.install_fault(root, "frontend", 1, output) == "", specifier
        assert verify.install_fault(root, "frontend", 1, "Error: Cannot find package 'react-router-dom' imported from /app/frontend/src/entry.tsx\n")
        assert verify.install_fault(root, "frontend", 1, "Error: Cannot find package '@scope/pkg' imported from /app/frontend/src/entry.tsx\n")
        assert verify.install_fault(root, "frontend", 1, "Error: Cannot find module 'react-router-dom/server' imported from /app/frontend/node_modules/x/y.js\n")

@case
def missing_dependencies_lists_an_incomplete_install():
    with tempfile.TemporaryDirectory() as tmp:
        root = make_project(Path(tmp))
        assert verify.missing_dependencies(root, "frontend") == ["vite", "vitest"]
        lay_packages(root / "frontend")
        assert verify.missing_dependencies(root, "frontend") == [verify.NOT_COMPLETED]
        verify.mark_install_complete(root / "frontend")
        assert verify.missing_dependencies(root, "frontend") == []

@case
def an_unlinked_program_of_the_build_script_is_missing():
    with tempfile.TemporaryDirectory() as tmp:
        root = make_project(Path(tmp))
        frontend = root / "frontend"
        lay_packages(frontend, link=False)
        verify.mark_install_complete(frontend)
        assert verify.missing_dependencies(root, "frontend") == ["program vite of vite not linked"]
        (frontend / "node_modules" / ".bin").mkdir()
        (frontend / "node_modules" / ".bin" / "vite.cmd").write_text("@echo off\n", encoding="utf-8")
        assert verify.missing_dependencies(root, "frontend") == []
        (frontend / "node_modules" / ".bin" / "vite.cmd").unlink()
        (frontend / "node_modules" / ".bin" / "vite").write_text("#!/bin/sh\n", encoding="utf-8")
        assert verify.missing_dependencies(root, "frontend") == []

        manifest = json.loads((frontend / "package.json").read_text(encoding="utf-8"))
        manifest["scripts"] = {"build": "tsc -b && NODE_ENV=production vite build", "start": "vite preview"}
        manifest["devDependencies"]["typescript"] = "^5.9.0"
        (frontend / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
        (frontend / "node_modules" / "typescript").mkdir()
        (frontend / "node_modules" / "typescript" / "package.json").write_text(
            json.dumps({"name": "typescript", "bin": {"tsc": "bin/tsc", "tsserver": "bin/tsserver"}}), encoding="utf-8")
        (frontend / "node_modules" / ".bin" / "vite").unlink()
        (frontend / "node_modules" / "vite" / "package.json").write_text(json.dumps({"name": "vite", "bin": "bin/vite.js"}), encoding="utf-8")
        missing = verify.missing_dependencies(root, "frontend")
        assert sorted(missing) == ["program tsc of typescript not linked", "program vite of vite not linked"], missing

@case
def a_failed_install_that_leaves_node_modules_is_never_skipped():
    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp)), installs=[(1.0, False)] * 20, leave=True) as fake:
        lines: list[str] = []
        step = verify.install_with_retries(fake.root, "frontend", log=lines.append, sleep=lambda s: None)
        assert not step.ok and fake.installs_made() == 3
        assert (fake.root / "frontend" / "node_modules" / "vite" / "package.json").is_file()
        assert verify.NOT_COMPLETED in verify.missing_dependencies(fake.root, "frontend")

        verify.install_with_retries(fake.root, "frontend", log=lines.append, sleep=lambda s: None)
        assert fake.installs_made() == 6
        step = verify.npm_install(fake.root, "frontend", log=lines.append)
        assert not step.skipped and fake.installs_made() == 7
        fake.t += 700
        assert verify.retry_install(fake.root, "frontend", log=lines.append) is not None and fake.installs_made() == 8
        fake.t += 700
        verify.build_frontend(fake.root, log=lines.append)
        assert fake.installs_made() == 9
        assert not any("skipping npm install" in line for line in lines)

@case
def start_retries_are_forced():
    seen: list[bool] = []
    real = verify.npm_install

    def spy(*args, **kwargs):
        seen.append(bool(kwargs.get("force", False)))
        return real(*args, **kwargs)

    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp)), installs=[(1.0, False)] * 3, leave=True) as fake:
        verify.npm_install = spy
        try:
            verify.install_with_retries(fake.root, "frontend", log=quiet, sleep=lambda s: None)
        finally:
            verify.npm_install = real
        assert seen == [False, True, True] and fake.installs_made() == 3, seen

@case
def a_completed_install_is_skipped_and_a_later_success_links_its_programs():
    with tempfile.TemporaryDirectory() as tmp, Timed(make_project(Path(tmp)), installs=[(1.0, False), (1.0, True)], leave=True) as fake:
        step = verify.install_with_retries(fake.root, "frontend", log=quiet, sleep=lambda s: None)
        assert step.ok and fake.installs_made() == 2 and verify.missing_dependencies(fake.root, "frontend") == []
        assert verify.npm_install(fake.root, "frontend", log=quiet).skipped and fake.installs_made() == 2

def main() -> int:
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
