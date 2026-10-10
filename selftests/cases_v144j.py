from __future__ import annotations

import inspect
import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "selftests"))

from agent import verify

CASES = []
OFFSET = 50_000.0
E404 = "npm error code E404\nnpm error 404 Not Found - GET https://mirror.example/x/-/x-1.0.0.tgz\n"

def case(function):
    CASES.append(function)
    return function

def quiet(_line: str) -> None:
    pass

class Fake:

    def __init__(self, root: Path, installs=((1.0, False),), build: float = 10.0):
        self.root, self.installs, self.build = root, list(installs), build
        self.t = 1_000.0
        self.calls: list[dict] = []
        for package in ("frontend", "backend"):
            (root / package).mkdir(parents=True, exist_ok=True)
            deps = {"vite": "^7"} if package == "frontend" else {"express": "^5"}
            (root / package / "package.json").write_text(json.dumps({"name": package, "dependencies": deps}), encoding="utf-8")

    def run_cmd(self, args, cwd=None, timeout=None, **_kw):
        cwd = Path(cwd)
        if args[1] == "install":
            duration, ok = self.installs.pop(0) if len(self.installs) > 1 else self.installs[0]
            self.calls.append({"kind": "install", "package": cwd.name, "start": self.t, "timeout": timeout})
            if duration > timeout:
                self.t += timeout
                return 124, "[timeout]"
            self.t += duration
            if not ok:
                return 1, E404
            manifest = json.loads((cwd / "package.json").read_text(encoding="utf-8"))
            for name in manifest.get("dependencies", {}):
                (cwd / "node_modules" / name).mkdir(parents=True, exist_ok=True)
                (cwd / "node_modules" / name / "package.json").write_text("{}", encoding="utf-8")
            return 0, "added 1 package"
        if args[1:3] == ["run", "build"]:
            self.calls.append({"kind": "build", "package": cwd.name, "start": self.t, "timeout": timeout})
            self.t += min(self.build, timeout)
            return 0, "built"
        raise AssertionError(f"unexpected command {args}")

    def installs_made(self, package: str = "backend") -> list[dict]:
        return [c for c in self.calls if c["kind"] == "install" and c["package"] == package]

    def clock_now(self) -> float:
        return self.t + OFFSET

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

def used_up(fake: Fake, package: str = "backend", ago: float = 0.0) -> dict:
    state = verify._install_state(fake.root / package)
    state["tries"] = verify.INSTALL_RETRY_LIMIT
    state["last"] = fake.t - ago
    return state

@case
def a_late_mirror_recovery_after_the_cap_still_installs():

    with tempfile.TemporaryDirectory() as tmp, Fake(Path(tmp), installs=[(5.0, False)] * 8 + [(5.0, True)]) as fake:
        lines: list[str] = []
        while len(fake.installs_made()) < verify.INSTALL_RETRY_LIMIT:
            fake.t += verify.INSTALL_RETRY_INTERVAL
            verify.ensure_installed(fake.root, "backend", log=lines.append)
        note = ""
        for _ in range(24):
            fake.t += 600
            note = verify.ensure_installed(fake.root, "backend", log=lines.append)
            if note == "":
                break
        assert note == "" and verify.missing_dependencies(fake.root, "backend") == [], (note, len(fake.installs_made()))
        made = fake.installs_made()
        assert len(made) == 9, len(made)
        gaps = [b["start"] - a["start"] for a, b in zip(made[verify.INSTALL_RETRY_LIMIT - 1:], made[verify.INSTALL_RETRY_LIMIT:])]
        assert all(gap >= verify.INSTALL_SLOW_INTERVAL for gap in gaps), gaps
        assert sum("used up" in line for line in lines) == 1 and any("slow try 1" in line for line in lines), lines[-6:]

@case
def the_slow_pace_is_thirty_minutes_whatever_the_caller_asks():
    assert verify.INSTALL_SLOW_INTERVAL == 1800.0
    for interval in (verify.INSTALL_RETRY_AFTER_127, verify.INSTALL_RETRY_INTERVAL, 0.0):
        with tempfile.TemporaryDirectory() as tmp, Fake(Path(tmp)) as fake:
            used_up(fake, ago=verify.INSTALL_SLOW_INTERVAL - 1)
            assert verify.retry_install(fake.root, "backend", log=quiet, min_interval=interval) is None, interval
            fake.t += 1
            step = verify.retry_install(fake.root, "backend", log=quiet, min_interval=interval)
            assert step is not None and not step.ok and len(fake.installs_made()) == 1, interval

@case
def before_the_cap_the_ordinary_pace_is_unchanged():
    with tempfile.TemporaryDirectory() as tmp, Fake(Path(tmp)) as fake:
        state = verify._install_state(fake.root / "backend")
        state["tries"], state["last"] = verify.INSTALL_RETRY_LIMIT - 1, fake.t - verify.INSTALL_RETRY_INTERVAL
        assert verify.retry_install(fake.root, "backend", log=quiet) is not None and len(fake.installs_made()) == 1
        fake.t += verify.INSTALL_RETRY_INTERVAL
        assert verify.retry_install(fake.root, "backend", log=quiet) is None and len(fake.installs_made()) == 1

@case
def install_retry_wait_is_none_after_the_cap_even_when_a_slow_try_is_due():
    for ago in (0.0, verify.INSTALL_SLOW_INTERVAL + 1):
        with tempfile.TemporaryDirectory() as tmp, Fake(Path(tmp)) as fake:
            used_up(fake, package="frontend", ago=ago)
            assert verify.install_retry_wait(fake.root, "frontend") is None, ago

@case
def the_closing_reserve_is_mains_finish_reserve():
    import main

    assert verify.INSTALL_CLOSING_RESERVE_SEC == main.HARD_END_FINISH_SEC, (verify.INSTALL_CLOSING_RESERVE_SEC, main.HARD_END_FINISH_SEC)

@case
def an_install_outside_a_build_ends_before_the_closing_reserve():
    reserve = verify.INSTALL_CLOSING_RESERVE_SEC
    for left, expected in ((None, 900), (reserve + 5000, 900), (reserve + 480, 480), (reserve + 59, None), (reserve - 100, None)):
        with tempfile.TemporaryDirectory() as tmp, Fake(Path(tmp)) as fake:
            lines: list[str] = []
            verify.set_global_deadline(None if left is None else fake.clock_now() + left)
            note = verify.ensure_installed(fake.root, "backend", log=lines.append)
            made = fake.installs_made()
            if expected is None:
                assert made == [] and "INSTALL PROBLEM" in note, (left, made)
                assert any("not started" in line for line in lines), (left, lines)
            else:
                assert len(made) == 1 and made[0]["timeout"] == expected, (left, made)

@case
def slow_tries_too_are_cut_by_the_hard_end():
    with tempfile.TemporaryDirectory() as tmp, Fake(Path(tmp)) as fake:
        used_up(fake, ago=10_000)
        verify.set_global_deadline(fake.clock_now() + verify.INSTALL_CLOSING_RESERVE_SEC + 300)
        verify.ensure_installed(fake.root, "backend", log=quiet)
        assert [c["timeout"] for c in fake.installs_made()] == [300], fake.calls

@case
def the_reserve_counts_from_the_runs_hard_end_not_a_recheck_bound():

    with tempfile.TemporaryDirectory() as tmp, Fake(Path(tmp)) as fake:
        verify.set_global_deadline(fake.clock_now() + 20_000)
        verify._GLOBAL_DEADLINE[0] = fake.clock_now() + 400
        verify.ensure_installed(fake.root, "backend", log=quiet)
        assert [c["timeout"] for c in fake.installs_made()] == [400], fake.calls

@case
def a_build_install_keeps_the_builds_own_bound_not_the_reserve():

    with tempfile.TemporaryDirectory() as tmp, Fake(Path(tmp), installs=[(5.0, True)]) as fake:
        verify.set_global_deadline(fake.clock_now() + 700)
        step = verify.build_frontend(fake.root, log=quiet)
        kinds = [c["kind"] for c in fake.calls]
        assert kinds == ["install", "build"] and step.ok, fake.calls
        assert fake.calls[0]["timeout"] == int(verify.BUILD_FRONTEND_WORST_SEC - verify.BUILD_ROOM_SEC), fake.calls
    with tempfile.TemporaryDirectory() as tmp, Fake(Path(tmp), installs=[(5.0, True)]) as fake:
        verify.set_global_deadline(fake.clock_now() + 500)
        verify.build_frontend(fake.root, log=quiet)
        assert fake.calls[0]["kind"] == "install" and fake.calls[0]["timeout"] == 500 - int(verify.BUILD_ROOM_SEC), fake.calls

@case
def a_whole_run_of_hanging_installs_never_reaches_the_closing_window():

    run = 22.5 * 3600
    with tempfile.TemporaryDirectory() as tmp, Fake(Path(tmp), installs=[(100_000.0, False)]) as fake:
        hard_end = fake.clock_now() + run
        verify.set_global_deadline(hard_end)
        while fake.clock_now() < hard_end - 60:
            verify.ensure_installed(fake.root, "backend", log=quiet)
            fake.t += 300
        made = fake.installs_made()
        ends = [c["start"] + c["timeout"] + OFFSET for c in made]
        assert max(ends) <= hard_end - verify.INSTALL_CLOSING_RESERVE_SEC, (hard_end - max(ends))
        assert len(made) <= verify.INSTALL_RETRY_LIMIT + run / verify.INSTALL_SLOW_INTERVAL + 1, len(made)

        assert max(c["start"] + OFFSET for c in made) >= hard_end - verify.INSTALL_CLOSING_RESERVE_SEC - 7200, made[-1]

@case
def every_install_outside_a_build_takes_the_default_reserve():

    for function in (verify.probe_server, verify._run_e2e_once, verify.run_e2e_in_order):
        source = inspect.getsource(function)
        calls = [line for line in source.splitlines() if "ensure_installed(" in line]
        assert calls and all("reserve" not in line and "timeout" not in line for line in calls), (function.__name__, calls)
    assert inspect.signature(verify.ensure_installed).parameters["reserve"].default == verify.INSTALL_CLOSING_RESERVE_SEC

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
    print(f"{len(CASES) - failed}/{len(CASES)} v144j cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
