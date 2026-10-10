from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import fsutil, verify, watchdog

CASES = []

def case(function):
    CASES.append(function)
    return function

def fresh() -> None:
    dog = watchdog._ACTIVE
    if dog is not None:
        dog.disarm()
    watchdog._ACTIVE = None
    fsutil.STOPPING.clear()

class FakeProcess:
    def __init__(self, pid: int, running: bool = True):
        self.pid, self.running = pid, running

    def poll(self):
        return None if self.running else 0

@case
def arm_early_arms_without_writing_and_start_for_keeps_it_and_attaches():
    fresh()
    import io

    written: list[str] = []
    saved_stdout = sys.stdout
    try:
        sys.stdout = captured = io.StringIO()
        try:
            dog = watchdog.arm_early(started_mono=time.monotonic(), started_wall=time.time())
        finally:
            sys.stdout = saved_stdout
        assert captured.getvalue() == "", "arm_early must write nothing"
        assert dog is not None and watchdog.active() is dog and dog.thread is not None and dog.thread.is_alive()
        assert dog.finish is None

        class Factory:
            hard_end_at = None

            def clean_runtime_state(self):
                return "cleaned"

        factory = Factory()
        again = watchdog.start_for(factory, started_mono=time.monotonic(), started_wall=time.time(), log=written.append)
        assert again is dog, "start_for must keep the early watchdog"
        assert dog.log == written.append and dog.finish == factory.clean_runtime_state
        assert written and written[0].startswith("[watchdog] armed")

        other: list[str] = []
        assert watchdog.attach(log=other.append) is dog and dog.log == other.append
    finally:
        sys.stdout = saved_stdout
        fresh()

@case
def arm_early_is_none_when_off_and_never_raises():
    fresh()
    saved = watchdog.watchdog_seconds
    try:
        watchdog.watchdog_seconds = lambda: None
        assert watchdog.arm_early(started_mono=0.0, started_wall=0.0) is None and watchdog.active() is None

        def broken():
            raise RuntimeError("settings unreadable")

        watchdog.watchdog_seconds = broken
        assert watchdog.arm_early(started_mono=0.0, started_wall=0.0) is None
        assert watchdog.attach(log=print) is None
    finally:
        watchdog.watchdog_seconds = saved
        fresh()

@case
def fire_stops_the_children_before_any_log_line():
    fresh()
    events: list[str] = []
    release = threading.Event()

    def blocked_log(line):
        events.append("log")
        release.wait(20)

    exited = threading.Event()
    dog = watchdog.Watchdog(10.0, started_mono=0.0, started_wall=0.0, log=blocked_log,
                            exit=lambda code: (events.append(f"exit {code}"), exited.set()),
                            monotonic=lambda: 100.0, wall=lambda: 100.0,
                            stop_children=lambda log=None: (events.append("children"), 2)[1])
    dog.finish = lambda: events.append("finish")
    dog.begin("batch B1", lambda: events.append("restore") or 1)
    saved = watchdog.LOG_BOUND_SEC
    worker = threading.Thread(target=dog.fire, daemon=True)
    try:
        watchdog.LOG_BOUND_SEC = 0.3
        worker.start()
        assert exited.wait(10.0), "the exit was held"
        first_log = events.index("log") if "log" in events else len(events)
        assert events.index("children") < first_log and events.index("restore") < first_log and events.index("finish") < first_log, events
    finally:
        watchdog.LOG_BOUND_SEC = saved
        release.set()
        worker.join(10)
        fsutil.STOPPING.clear()
        fresh()

@case
def the_group_of_an_exited_leader_is_still_stopped_by_the_watchdog():
    saved = (fsutil.IS_WINDOWS, fsutil._kill_pid, fsutil._kill_group, fsutil._descendants, dict(fsutil._CHILDREN))
    pids: list[int] = []
    groups: list[int] = []
    try:
        fsutil.IS_WINDOWS = False
        fsutil._kill_pid = pids.append
        fsutil._kill_group = groups.append
        fsutil._descendants = lambda root: set()
        fsutil._CHILDREN.clear()
        fsutil.register_child(FakeProcess(501, running=False), own_group=True)
        fsutil.register_child(FakeProcess(502, running=False))
        assert fsutil.stop_children(log=lambda line: None) == 0
        assert groups == [501] and pids == [], (groups, pids)
    finally:
        fsutil.IS_WINDOWS, fsutil._kill_pid, fsutil._kill_group, fsutil._descendants = saved[:4]
        fsutil._CHILDREN.clear()
        fsutil._CHILDREN.update(saved[4])

@case
def server_stop_uses_the_recorded_group_and_kills_what_ignores_sigterm():
    saved = (fsutil.IS_WINDOWS, fsutil._kill_group, fsutil._group_alive, getattr(os, "killpg", None), fsutil.stop_group)
    sent: list[tuple[int, int]] = []
    killed: list[int] = []
    try:
        fsutil.IS_WINDOWS = False
        os.killpg = lambda pgid, sig: sent.append((pgid, sig))
        fsutil._kill_group = killed.append
        fsutil._group_alive = lambda pgid: True
        leader = FakeProcess(601, running=False)
        fsutil.stop_group(leader, 601, sleep=lambda s: None)
        assert sent and sent[0][0] == 601 and killed == [601], (sent, killed)

        seen: list[int] = []
        fsutil.stop_group = lambda process, pgid, **kw: seen.append(pgid)
        with tempfile.TemporaryDirectory() as tmp:
            server = fsutil.BackgroundProcess.__new__(fsutil.BackgroundProcess)
            server.process, server.pgid = leader, 601
            server._log_path = Path(tmp) / "server.log"
            server._log = open(server._log_path, "w", encoding="utf-8")
            server.stop()
        assert seen == [601], seen

        killed.clear()
        fsutil._group_alive = lambda pgid: False
        saved[4](leader, 602, sleep=lambda s: None)
        assert killed == [], killed
    finally:
        fsutil.IS_WINDOWS, fsutil._kill_group, fsutil._group_alive = saved[:3]
        fsutil.stop_group = saved[4]
        if saved[3] is None:
            del os.killpg
        else:
            os.killpg = saved[3]

def project(root: Path, *, complete: bool, backend_deps: dict | None = None) -> Path:
    for package, deps in (("frontend", {"pkg": "1.0.0"}), ("backend", backend_deps or {"express": "4.0.0"})):
        base = root / package
        (base / "node_modules").mkdir(parents=True)
        (base / "package.json").write_text(json.dumps({"name": package, "dependencies": deps}), encoding="utf-8")
        for name in deps:
            (base / "node_modules" / name).mkdir(parents=True, exist_ok=True)
            (base / "node_modules" / name / "package.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        if complete:
            verify.mark_install_complete(base)
    return root

@case
def an_app_import_of_a_missing_node_modules_file_is_the_codes_when_the_install_is_complete():
    line = "Cannot find module '/app/frontend/node_modules/pkg/not-there.js' imported from /app/frontend/postcss.config.js"
    with tempfile.TemporaryDirectory() as tmp:
        root = project(Path(tmp), complete=True)
        assert verify.install_fault(root, "frontend", 1, line) == ""

        inner = "Cannot find module '/app/frontend/node_modules/pkg/lib/x.js' imported from /app/frontend/node_modules/pkg/index.js"
        assert verify.install_fault(root, "frontend", 1, inner)
    with tempfile.TemporaryDirectory() as tmp:
        root = project(Path(tmp), complete=False)
        assert "inside node_modules" in verify.install_fault(root, "frontend", 1, line)

@case
def a_backend_that_cannot_start_for_the_install_carries_infra_a_source_error_does_not():
    with tempfile.TemporaryDirectory() as tmp:
        root = project(Path(tmp), complete=True)
        assert verify.server_install_fault(root, 127, "sh: 1: nodemon: not found").startswith("backend: ")
        declared = "Error: Cannot find module 'express'\nRequire stack:\n- /app/backend/server.js"
        assert "declared dependency" in verify.server_install_fault(root, 1, declared)
        syntax = "/app/backend/server.js:12\n  const x = ;\nSyntaxError: Unexpected token ';'"
        assert verify.server_install_fault(root, 1, syntax) == ""
        assert verify.server_install_fault(root, 1, syntax, install_note="[verify] INSTALL PROBLEM ...") == ""
        assert verify.server_install_fault(root, 1, "npm ERR! missing script: start", install_note="[verify] INSTALL PROBLEM ...")
        assert verify.server_install_fault(root, 1, "Error: listen on a closed socket") == ""

        assert verify.server_install_fault(root, 1, "GET /api/x: not found\n") == ""

@case
def a_declared_playwright_test_that_is_missing_is_infra_not_green():
    saved = verify.ensure_installed
    try:
        verify.ensure_installed = lambda *a, **k: ""
        with tempfile.TemporaryDirectory() as tmp:
            root = project(Path(tmp), complete=True, backend_deps={"express": "4.0.0", "@playwright/test": "1.57.0"})
            (root / "backend" / "node_modules" / "@playwright" / "test" / "package.json").unlink()
            (root / "backend" / "node_modules" / "@playwright" / "test").rmdir()
            result = verify._run_e2e_once(root, ["backend/test-e2e/A.spec.js"], log=lambda line: None)
            assert result.skipped and result.infra.startswith("backend: @playwright/test"), result
        with tempfile.TemporaryDirectory() as tmp:
            root = project(Path(tmp), complete=True)
            result = verify._run_e2e_once(root, ["backend/test-e2e/A.spec.js"], log=lambda line: None)
            assert result.skipped and result.infra == "", result
    finally:
        verify.ensure_installed = saved

class Outcome:
    def __init__(self, passed: bool, infra: str = ""):
        self.passed, self.infra = passed, infra

@case
def the_install_wait_and_its_rechecks_share_one_absolute_end():
    now = [1000.0]
    saved = (verify._now, verify.install_retry_wait, verify._GLOBAL_DEADLINE[0])
    checks: list[float | None] = []
    try:
        verify._now = lambda: now[0]
        verify.install_retry_wait = lambda project_dir, package: 250.0
        verify._GLOBAL_DEADLINE[0] = None

        def slow_check():
            checks.append(verify._GLOBAL_DEADLINE[0])
            now[0] += 500.0
            return Outcome(False, "frontend: a program the command runs was not found")

        def sleep(seconds):
            now[0] += seconds

        start = now[0]
        verify.settle_install_failure(slow_check, Outcome(False, "frontend: x"), Path("."), log=lambda line: None,
                                      sleep=sleep, max_wait=1200)
        assert now[0] - start <= 1200, now[0] - start
        assert len(checks) == 1, checks

        assert checks[0] is not None and verify._GLOBAL_DEADLINE[0] is None
    finally:
        verify._now, verify.install_retry_wait = saved[0], saved[1]
        verify._GLOBAL_DEADLINE[0] = saved[2]

@case
def a_recheck_does_not_start_after_a_late_wake_up():
    now = [1000.0]
    saved = (verify._now, verify.install_retry_wait)
    made: list[int] = []
    try:
        verify._now = lambda: now[0]
        verify.install_retry_wait = lambda project_dir, package: 100.0

        def sleep(seconds):
            now[0] += seconds + 500.0

        verify.settle_install_failure(lambda: made.append(1) or Outcome(False, "frontend: x"), Outcome(False, "frontend: x"),
                                      Path("."), log=lambda line: None, sleep=sleep, max_wait=300)
        assert made == [], made
    finally:
        verify._now, verify.install_retry_wait = saved

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
        finally:
            fresh()
    print(f"{len(CASES) - failed}/{len(CASES)} v144b cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
