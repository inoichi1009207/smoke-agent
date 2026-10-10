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
sys.path.insert(0, str(Path(__file__).resolve().parent))

CASES = []

def case(function):
    CASES.append(function)
    return function

class Clock:

    def __init__(self, mono: float = 500.0, wall: float = 1_700_000_000.0):
        self.mono, self.wall_t = mono, wall
        self.waits: list[float] = []

    def monotonic(self) -> float:
        return self.mono

    def wall(self) -> float:
        return self.wall_t

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall_t += seconds

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        self.advance(seconds)
        return False

class Exited(Exception):
    pass

class Recorder:
    def __init__(self, clock: Clock | None = None):
        self.clock = clock
        self.events: list[tuple] = []
        self.lines: list[str] = []

    def exit(self, code: int) -> None:
        self.events.append(("exit", code, self.clock.mono if self.clock else None))

    def stop_children(self, log=print) -> int:
        self.events.append(("children",))
        return 2

    def log(self, line: str) -> None:
        self.lines.append(line)

def fresh():
    from agent import fsutil, watchdog
    from agent.llm import LLM

    watchdog._ACTIVE = None
    LLM.global_end = None
    fsutil.STOPPING.clear()

def make_dog(clock: Clock, rec: Recorder, seconds: float = 83700.0, **kwargs):
    from agent.watchdog import Watchdog

    return Watchdog(seconds, started_mono=clock.mono, started_wall=clock.wall_t, log=rec.log, exit=rec.exit,
                    monotonic=clock.monotonic, wall=clock.wall, wait=clock.wait, stop_children=rec.stop_children, **kwargs)

@case
def the_watchdog_fires_at_its_end_and_exits_0():
    fresh()
    clock = Clock()
    rec = Recorder(clock)
    dog = make_dog(clock, rec)
    start = clock.mono
    dog.run()
    assert [e[0] for e in rec.events] == ["children", "exit"], rec.events
    assert rec.events[-1][1] == 0 and rec.events[-1][2] == start + 83700.0, rec.events
    assert rec.lines and rec.lines[0].startswith("[watchdog] 83700 s after the process start"), rec.lines
    assert max(clock.waits) <= 30.0

@case
def a_disarmed_watchdog_never_fires():
    fresh()
    clock = Clock()
    rec = Recorder(clock)
    dog = make_dog(clock, rec, seconds=100.0)
    dog.disarm()
    dog.run()
    dog.fire()
    assert rec.events == [], rec.events

@case
def a_wall_clock_set_back_does_not_delay_it_and_one_set_forward_brings_it_on():
    fresh()

    clock = Clock()
    rec = Recorder(clock)
    dog = make_dog(clock, rec, seconds=1000.0)
    start = clock.mono
    clock.wall_t -= 7200
    dog.run()
    assert rec.events[-1][:2] == ("exit", 0) and rec.events[-1][2] == start + 1000.0, rec.events

    clock = Clock()
    rec = Recorder(clock)
    dog = make_dog(clock, rec, seconds=1000.0)
    start = clock.mono
    clock.wall_t += 600
    dog.run()
    assert rec.events[-1][:2] == ("exit", 0) and rec.events[-1][2] == start + 400.0, rec.events

@case
def the_real_thread_fires_and_calls_the_injected_exit():
    fresh()
    from agent import watchdog

    rec = Recorder()
    done = threading.Event()

    def exit(code):
        rec.events.append(("exit", code))
        done.set()

    dog = watchdog.arm(0.3, started_mono=time.monotonic(), started_wall=time.time(), log=rec.log, exit=exit,
                       stop_children=rec.stop_children)
    try:
        assert dog is not None and dog.thread is not None and dog.thread.daemon
        assert done.wait(10.0), "the watchdog thread did not fire"
        assert rec.events == [("children",), ("exit", 0)], rec.events
    finally:
        fresh()

@case
def fire_calls_the_restore_callback_once_after_the_children_and_before_the_exit():
    fresh()
    clock = Clock()
    rec = Recorder(clock)
    dog = make_dog(clock, rec)
    from agent import fsutil

    token = dog.begin("batch REQ-7", lambda: rec.events.append(("restore", fsutil.STOPPING.is_set())) or 5)
    dog.finish = lambda: rec.events.append(("finish",))
    dog.fire()
    dog.fire()
    assert [e[0] for e in rec.events] == ["children", "restore", "finish", "exit"], rec.events
    assert rec.events[1] == ("restore", True), rec.events
    assert dog.firing.is_set()
    fsutil.STOPPING.clear()
    assert any("state before 'batch REQ-7' put back (5 file(s))" in line for line in rec.lines), rec.lines
    assert token is not None

@case
def the_innermost_open_registration_is_put_back_and_end_clears_what_it_opened():
    fresh()
    clock = Clock()
    rec = Recorder(clock)
    dog = make_dog(clock, rec)
    outer = dog.begin("batch", lambda: rec.events.append(("outer",)))
    inner = dog.begin("retry", lambda: rec.events.append(("inner",)))
    assert dog.current()[0] == "retry"
    dog.end(inner)
    assert dog.current()[0] == "batch"
    dog.begin("leaked inner", lambda: rec.events.append(("leaked",)))
    dog.end(outer)
    assert dog.current() is None
    dog.fire()
    assert [e[0] for e in rec.events] == ["children", "exit"], rec.events

@case
def a_restore_that_hangs_is_abandoned_after_its_bound():
    fresh()
    clock = Clock()
    rec = Recorder(clock)
    dog = make_dog(clock, rec, restore_bound=0.3)
    release = threading.Event()
    dog.begin("closing repair", lambda: release.wait(20))
    began = time.monotonic()
    dog.fire()
    release.set()
    assert time.monotonic() - began < 5.0
    assert rec.events[-1][:2] == ("exit", 0) and any("did not finish in 0 s" in l or "did not finish" in l for l in rec.lines), rec.lines

@case
def a_failing_step_never_keeps_the_process_alive():
    fresh()
    clock = Clock()
    rec = Recorder(clock)

    def broken(log=print):
        raise OSError("taskkill missing")

    dog = make_dog(clock, rec)
    dog.stop_children = broken
    dog.begin("batch", lambda: 1 / 0)
    dog.fire()
    assert rec.events[-1][:2] == ("exit", 0), rec.events
    assert any("failed" in l for l in rec.lines)

@case
def the_watchdog_puts_a_half_written_batch_back_on_disk():
    fresh()
    import main as m
    from agent import fsutil, watchdog

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "frontend" / "src").mkdir(parents=True)
        (root / "frontend" / "src" / "App.tsx").write_text("accepted", encoding="utf-8")
        f = m.Factory.__new__(m.Factory)
        f.project_dir, f.restore_failures = root, 0
        clock = Clock()
        rec = Recorder(clock)
        dog = make_dog(clock, rec)
        watchdog._ACTIVE = dog
        try:
            token = watchdog.begin_snapshot(f, "batch REQ-3")
            (root / "frontend" / "src" / "App.tsx").write_text("half written", encoding="utf-8")
            (root / "frontend" / "src" / "New.tsx").write_text("new", encoding="utf-8")
            dog.fire()
            assert (root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8") == "accepted"
            assert not (root / "frontend" / "src" / "New.tsx").exists()
            assert rec.events[-1][:2] == ("exit", 0) and token is not None
        finally:
            fresh()
            fsutil.STOPPING.clear()

@case
def once_it_began_to_stop_nothing_new_starts():
    fresh()
    from agent import fsutil

    with tempfile.TemporaryDirectory() as tmp:
        fsutil.begin_stop()
        try:
            code, output = fsutil.run_cmd([sys.executable, "-c", "print('ran')"], cwd=Path(tmp), timeout=30)
            assert code == 124 and "ran" not in output and "stopping" in output, (code, output)
            try:
                fsutil.BackgroundProcess([sys.executable, "-c", "print(1)"], cwd=Path(tmp))
                raise AssertionError("a server must not start while the agent stops")
            except RuntimeError:
                pass
        finally:
            fsutil.STOPPING.clear()
        code, output = fsutil.run_cmd([sys.executable, "-c", "print('ran')"], cwd=Path(tmp), timeout=60)
        assert code == 0 and "ran" in output, (code, output)
        began = time.monotonic()
        code, output = fsutil.run_cmd([sys.executable, "-c", "import time; time.sleep(30)"], cwd=Path(tmp), timeout=1)
        assert code == 124 and "[timeout after 1s]" in output, (code, output)
        assert time.monotonic() - began < 15, "a timed-out command's tree must go with it (not only the shell at its top)"
        assert not fsutil.registered_children()

class FakeProcess:
    def __init__(self, pid: int, running: bool = True):
        self.pid, self.running = pid, running

    def poll(self):
        return None if self.running else 0

@case
def only_registered_children_are_stopped_on_windows():
    fresh()
    from agent import fsutil

    saved = (fsutil.IS_WINDOWS, fsutil._taskkill, dict(fsutil._CHILDREN))
    killed: list[int] = []
    try:
        fsutil.IS_WINDOWS = True
        fsutil._taskkill = lambda pid, timeout: killed.append(pid)
        fsutil._CHILDREN.clear()
        a, b, c = FakeProcess(101), FakeProcess(102, running=False), FakeProcess(103)
        fsutil.register_child(a)
        fsutil.register_child(b)

        assert fsutil.stop_children(log=lambda line: None) == 1
        assert killed == [101], killed
        fsutil.unregister_child(a)
        assert [p.pid for p in fsutil.registered_children()] == [102]
    finally:
        fsutil.IS_WINDOWS, fsutil._taskkill = saved[0], saved[1]
        fsutil._CHILDREN.clear()
        fsutil._CHILDREN.update(saved[2])

@case
def on_linux_the_registered_trees_and_this_process_descendants_are_stopped_nothing_else():
    fresh()
    from agent import fsutil

    saved = (fsutil.IS_WINDOWS, fsutil._kill_pid, fsutil._kill_group, fsutil._descendants, dict(fsutil._CHILDREN))
    pids: list[int] = []
    groups: list[int] = []
    try:
        fsutil.IS_WINDOWS = False
        fsutil._kill_pid = pids.append
        fsutil._kill_group = groups.append
        me = os.getpid()

        fsutil._descendants = lambda root: {201, 301, 202} if root == me else set()
        fsutil._CHILDREN.clear()
        fsutil.register_child(FakeProcess(201), own_group=True)
        fsutil.register_child(FakeProcess(204, running=False))
        assert fsutil.stop_children(log=lambda line: None) == 1
        assert sorted(set(pids)) == [201, 202, 301] and 204 not in pids and me not in pids, pids
        assert groups == [201], groups
    finally:
        fsutil.IS_WINDOWS, fsutil._kill_pid, fsutil._kill_group, fsutil._descendants = saved[:4]
        fsutil._CHILDREN.clear()
        fsutil._CHILDREN.update(saved[4])

@case
def real_children_run_cmd_and_a_server_are_stopped_an_unregistered_process_is_not():
    fresh()
    import subprocess
    from agent import fsutil

    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
    saved_descendants = fsutil._descendants
    if not fsutil.IS_WINDOWS:

        fsutil._descendants = lambda root: set()
    with tempfile.TemporaryDirectory() as tmp:
        stranger = subprocess.Popen(sleeper)
        server = fsutil.BackgroundProcess(sleeper, cwd=Path(tmp))
        result: list = []
        worker = threading.Thread(target=lambda: result.append(fsutil.run_cmd(sleeper, cwd=Path(tmp), timeout=120)), daemon=True)
        worker.start()
        try:
            for _ in range(100):
                if len(fsutil.registered_children()) >= 2:
                    break
                time.sleep(0.1)
            assert len(fsutil.registered_children()) == 2, fsutil.registered_children()
            began = time.monotonic()
            assert fsutil.stop_children(log=lambda line: None) == 2
            worker.join(30)
            assert not worker.is_alive() and result and result[0][0] != 0, result
            for _ in range(100):
                if server.process.poll() is not None:
                    break
                time.sleep(0.1)
            assert server.process.poll() is not None, "the registered server still runs"
            assert time.monotonic() - began < 30
            assert stranger.poll() is None, "a process the registry does not know was stopped"
        finally:
            fsutil._descendants = saved_descendants
            stranger.kill()
            stranger.wait(10)
            server.stop()
            assert not fsutil.registered_children()

def checkpoint_factory(root: Path, hard_end_at: float | None, hard_end_mono: float | None):
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.project_dir, f.restore_failures, f.started, f.spent_before = root, 0, time.time(), 0.0
    f.revised_specs, f.partial_green, f.llm = set(), {}, None
    for field in m.Factory.CHECKPOINT_FIELDS:
        setattr(f, field, {} if field in ("spec_context", "file_specs", "test_snapshot", "test_ids_by_file", "test_titles",
                                          "report", "seed_checks", "description_pass") else ("" if field == "contract" else []))
    f.hard_end_at, f.hard_end_mono = hard_end_at, hard_end_mono
    return f

@case
def a_resume_keeps_the_hard_end_of_its_first_start():
    fresh()
    from agent.llm import LLM

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".arc").mkdir()
        now = time.time()
        first = checkpoint_factory(root, now + 81000 - 72000, time.monotonic() + 81000 - 72000)
        first.save_checkpoint("digest-1", ["B1"], "desc")
        state = json.loads((root / ".arc" / "resume.json").read_text(encoding="utf-8"))
        assert abs(state["hard_end_wall"] - (now + 9000)) < 1, state.get("hard_end_wall")

        second = checkpoint_factory(root, now + 81000, time.monotonic() + 81000)
        saved_env = os.environ.pop("AGENT_RESUME", None)
        try:
            assert second.load_checkpoint("digest-1") == ["B1"]
        finally:
            if saved_env is not None:
                os.environ["AGENT_RESUME"] = saved_env

        assert abs(second.hard_end_at - (now + 9000)) < 1
        assert abs(second.hard_left() - 9000) < 5, second.hard_left()
        assert LLM.global_end is not None and abs(LLM.global_end - (time.monotonic() + 9000)) < 5

        third = checkpoint_factory(root, now + 5000, time.monotonic() + 5000)
        from agent import watchdog
        watchdog.resume_ends(third, {"hard_end_wall": now + 50000}, log=lambda line: None)
        assert abs(third.hard_end_at - (now + 5000)) < 1
    fresh()

@case
def a_resume_keeps_the_watchdog_end_of_its_first_start():
    fresh()
    from agent import watchdog

    clock = Clock()
    rec = Recorder(clock)
    dog = make_dog(clock, rec)
    watchdog._ACTIVE = dog
    try:
        stored = clock.wall_t + 3600.0
        f = checkpoint_factory(Path("."), None, None)
        assert watchdog.checkpoint_ends(f) == {"watchdog_end_wall": dog.end_wall}
        watchdog.resume_ends(f, {"watchdog_end_wall": stored}, log=rec.log, monotonic=clock.monotonic, wall=clock.wall)
        assert abs(dog.left() - 3600.0) < 1e-6, dog.left()
        assert not dog.move_end(stored + 10)
        start = clock.mono
        dog.run()
        assert rec.events[-1][:2] == ("exit", 0) and rec.events[-1][2] == start + 3600.0, rec.events
    finally:
        fresh()

@case
def the_hard_end_follows_the_monotonic_clock_when_the_wall_clock_goes_back():
    fresh()
    import main as m
    from agent import clock

    f = m.Factory.__new__(m.Factory)
    real = time.time()
    f.hard_end_at, f.hard_end_mono = real + 1000.0, time.monotonic() + 1000.0
    saved = clock.now
    try:
        clock.now = lambda: time.time() - 7200.0
        left = f.hard_left()
        assert 990 < left < 1001, left
        clock.now = lambda: time.time() + 500.0
        left = f.hard_left()
        assert 490 < left < 501, left
    finally:
        clock.now = saved
    g = m.Factory.__new__(m.Factory)
    g.hard_end_at = 12345.0
    assert g.hard_end() == 12345.0 - clock.paused_sec()

class HeartbeatStream:

    def __init__(self, clock: Clock, step: float):
        self.clock, self.step, self.lines = clock, step, 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        while self.lines < 100000:
            self.clock.advance(self.step)
            self.lines += 1
            yield b": keep-alive\n" if self.lines % 2 else b'data: {"choices": [{"delta": {}}]}\n'

class patched_llm:
    def __init__(self, clock: Clock, urlopen):
        self.clock, self.urlopen = clock, urlopen

    def __enter__(self):
        import urllib.request
        from agent import llm

        self.saved = (llm._clock, urllib.request.urlopen, time.sleep)
        llm._clock = self.clock.monotonic
        urllib.request.urlopen = self.urlopen
        time.sleep = self.clock.advance
        return self

    def __exit__(self, *exc):
        import urllib.request
        from agent import llm
        from agent.llm import LLM

        llm._clock, urllib.request.urlopen, time.sleep = self.saved
        LLM.global_end = None

def client():
    from agent.llm import LLM

    return LLM("http://gateway.invalid/v1", "key", "glm-test", log=lambda line: None)

@case
def a_heartbeat_stream_is_cut_at_the_global_end_and_nothing_follows_it():
    fresh()
    from agent.llm import LLM, LLMError

    clock = Clock()
    opened: list = []

    def urlopen(request, timeout=None):
        body = json.loads(request.data.decode("utf-8"))
        opened.append((bool(body.get("stream")), timeout))
        return HeartbeatStream(clock, 7.0)

    with patched_llm(clock, urlopen):
        LLM.global_end = clock.mono + 100.0
        c = client()
        c.call_limit_sec = 1800
        began = clock.mono
        try:
            c.chat([{"role": "user", "content": "fix"}], thinking=False, expect_marker=(("=== FILE:",), 30000))
            raise AssertionError("a stream without an end must not be read for ever")
        except LLMError as exc:
            assert "total time limit" in str(exc), exc
        assert clock.mono - began <= 100.0 + 7.0, clock.mono - began
        assert opened == [(True, 100.0)], opened

@case
def a_heartbeat_stream_is_cut_at_the_call_own_limit():
    fresh()
    from agent.llm import LLMError

    clock = Clock()
    with patched_llm(clock, lambda request, timeout=None: HeartbeatStream(clock, 5.0)):
        c = client()
        c.call_limit_sec = 300
        began = clock.mono
        try:
            c.chat([{"role": "user", "content": "fix"}], thinking=False, expect_marker=(("=== FILE:",), 30000))
            raise AssertionError("cut expected")
        except LLMError as exc:
            assert "total time limit" in str(exc), exc
        assert 295.0 <= clock.mono - began <= 305.0, clock.mono - began

@case
def retries_and_backoff_stop_at_the_end():
    fresh()
    from agent.llm import LLM, LLMError

    clock = Clock()
    calls: list = []

    def refused(request, timeout=None):
        calls.append(timeout)
        clock.advance(min(10.0, timeout))
        raise ConnectionResetError("connection reset by peer")

    with patched_llm(clock, refused):
        LLM.global_end = clock.mono + 40.0
        c = client()
        began = clock.mono
        try:
            c.chat([{"role": "user", "content": "x"}])
            raise AssertionError("expected to fail")
        except LLMError as exc:
            assert "total time limit" in str(exc), exc
        assert clock.mono - began <= 40.0, clock.mono - began
        assert 1 <= len(calls) < 5 and all(t <= 40.0 for t in calls), calls

@case
def a_quota_wait_does_not_run_past_the_end():
    fresh()
    import io
    import urllib.error
    from agent.llm import LLM, LLMError

    clock = Clock()

    def dry(request, timeout=None):
        raise urllib.error.HTTPError("u", 429, "quota", {}, io.BytesIO(b"Free quota exhausted and balance too low"))

    saved = (LLM.quota_waited_sec, LLM.quota_exhausted)
    with patched_llm(clock, dry):
        LLM.global_end = clock.mono + 300.0
        began = clock.mono
        try:
            client().chat([{"role": "user", "content": "x"}])
            raise AssertionError("expected to fail")
        except LLMError as exc:
            assert "total time limit" in str(exc), exc
        assert clock.mono - began <= 300.0 and not LLM.quota_exhausted
    LLM.quota_waited_sec, LLM.quota_exhausted = saved

@case
def no_call_starts_past_the_end_and_without_one_a_call_is_as_before():
    fresh()
    from agent.llm import LLM, LLMError

    clock = Clock()
    opened: list = []
    with patched_llm(clock, lambda request, timeout=None: opened.append(timeout)):
        LLM.global_end = clock.mono - 1.0
        try:
            client().chat([{"role": "user", "content": "x"}])
            raise AssertionError("expected to fail")
        except LLMError as exc:
            assert "not started" in str(exc)
        try:
            client()._post_with_retry({"model": "glm-test", "messages": []})
            raise AssertionError("expected to fail")
        except LLMError as exc:
            assert "attempt 1" in str(exc)
        assert opened == []
    c = client()
    c.call_limit_sec = 0
    assert c.call_end() is None and c._timeout_for(None) == 600

@case
def settings_switch_it_off():
    fresh()
    from agent import watchdog
    from agent.settings import SETTINGS

    saved = {k: SETTINGS.get(k) for k in ("platform_watchdog_sec", "platform_time_limit_sec")}
    try:
        assert watchdog.watchdog_seconds() == 83700.0
        SETTINGS["platform_watchdog_sec"] = "0"
        assert watchdog.watchdog_seconds() is None
        SETTINGS["platform_watchdog_sec"] = 83700
        SETTINGS["platform_time_limit_sec"] = "0"
        assert watchdog.watchdog_seconds() is None

        class NoSnapshots:
            def snapshot_sources(self):
                raise AssertionError("no snapshot without a watchdog")

        assert watchdog.start_for(NoSnapshots(), started_mono=time.monotonic(), started_wall=time.time(), log=lambda l: None) is None
        assert watchdog.begin_snapshot(NoSnapshots(), "batch") is None and watchdog.begin("x", lambda: 0) is None
        watchdog.end(None)
        watchdog.shutdown(log=lambda line: (_ for _ in ()).throw(AssertionError("nothing to say")))
    finally:
        SETTINGS.update(saved)
        fresh()

@case
def the_ordinary_end_stops_leftovers_and_stays_armed_for_a_hang_after_it():
    fresh()
    from agent import fsutil, watchdog

    rec = Recorder()
    saved = fsutil.stop_children
    dog = watchdog.arm(10000.0, started_mono=time.monotonic(), started_wall=time.time(), log=rec.log, exit=rec.exit)
    try:
        fsutil.stop_children = rec.stop_children
        watchdog.shutdown(log=rec.log)
        assert ("children",) in rec.events and not dog._stop.is_set()
        assert not [e for e in rec.events if e[0] == "exit"]
    finally:
        fsutil.stop_children = saved
        dog.disarm()
        fresh()

@case
def a_blocked_log_does_not_hold_the_exit():
    fresh()
    clock = Clock()
    rec = Recorder(clock)
    release = threading.Event()
    dog = make_dog(clock, rec)
    dog.log = lambda line: release.wait(30)
    dog.backstop_sec = 0.5
    exited = threading.Event()
    dog.exit = lambda code: (rec.events.append(("exit", code)), exited.set())
    worker = threading.Thread(target=dog.fire, daemon=True)
    worker.start()
    try:
        assert exited.wait(10.0), "a blocked print kept the process alive"

        assert rec.events[0] == ("children",) and ("exit", 0) in rec.events, rec.events
    finally:
        release.set()
        worker.join(10)
        from agent import fsutil
        fsutil.STOPPING.clear()

@case
def a_put_back_of_the_main_thread_waits_once_the_watchdog_began():
    fresh()
    import main as m
    from agent import fsutil, watchdog

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend").mkdir()
        (root / "backend" / "server.js").write_text("broken", encoding="utf-8")
        f = m.Factory.__new__(m.Factory)
        f.project_dir, f.restore_failures = root, 0
        clock = Clock()
        dog = make_dog(clock, Recorder(clock))
        watchdog._ACTIVE = dog
        try:

            seen: list = []
            original = f.snapshot_sources
            f.snapshot_sources = lambda: (seen.append(dog.current()[0] if dog.current() else None), original())[1]
            f.restore_sources({"backend/server.js": b"accepted"})
            assert "putting back the sources" in seen and dog.current() is None, seen
            f.snapshot_sources = original
            fsutil.begin_stop()
            main_thread = threading.Thread(target=f.restore_sources, args=({"backend/server.js": b"other"},), daemon=True)
            main_thread.start()
            main_thread.join(0.5)
            assert main_thread.is_alive(), "the main thread's put-back must wait"
            assert (root / "backend" / "server.js").read_text(encoding="utf-8") == "accepted"
            done = threading.Thread(target=f.restore_sources, args=({"backend/server.js": b"by the watchdog"},),
                                    name="watchdog-restore", daemon=True)
            done.start()
            done.join(10)
            assert not done.is_alive() and (root / "backend" / "server.js").read_text(encoding="utf-8") == "by the watchdog"
        finally:
            fsutil.STOPPING.clear()
            fresh()

@case
def a_checkpoint_of_a_task_that_is_over_is_not_taken_over():
    fresh()
    from agent import watchdog

    clock = Clock()
    rec = Recorder(clock)
    dog = make_dog(clock, rec)
    watchdog._ACTIVE = dog
    try:
        f = checkpoint_factory(Path("."), clock.wall_t + 81000.0, time.monotonic() + 81000.0)
        old = {"hard_end_wall": clock.wall_t - 30 * 3600.0, "watchdog_end_wall": clock.wall_t - 29.25 * 3600.0}
        watchdog.resume_ends(f, old, log=rec.log, monotonic=clock.monotonic, wall=clock.wall)
        assert f.hard_end_at == clock.wall_t + 81000.0 and abs(dog.left() - 83700.0) < 1e-6
        assert any("that task is over" in line for line in rec.lines), rec.lines
    finally:
        fresh()

@case
def the_watchdog_is_never_later_than_the_platform_limit_allows():
    fresh()
    from agent import watchdog
    from agent.settings import SETTINGS

    saved = SETTINGS.get("platform_time_limit_sec")
    try:
        SETTINGS["platform_time_limit_sec"] = 43200
        assert watchdog.watchdog_seconds() == 43200 - 2700
    finally:
        SETTINGS["platform_time_limit_sec"] = saved

@case
def main_arms_it_first_and_registers_every_in_flight_change():
    import inspect
    import main as m

    run = inspect.getsource(m.Factory.run)
    first = run.index("watchdog.start_for(self")
    assert first < run.index("clock.start(") < run.index("self.load_checkpoint(") < run.index("attach_visual_notes(")
    assert run.index("attach_visual_notes(") < run.index("build_contract(") < run.index("install_with_retries(")
    desc = run.index("run_description_pass(self")

    assert run.rindex('self.begin_restore_point("description pass")', 0, desc) < desc < run.index("watchdog.end(in_flight)", desc)
    queue = inspect.getsource(m.Factory.run_queue)
    attempt = queue.index("ok = self.attempt_node(")
    assert queue.rindex("watchdog.begin_snapshot(self", 0, attempt) < attempt < queue.index("watchdog.end(in_flight)", attempt)
    retry = queue.index("requeue=False, retry=True)")
    assert queue.rindex("watchdog.begin(", 0, retry) < retry < queue.index("watchdog.end(in_flight)", retry)
    for method in (m.Factory.repair_round, m.Factory.repair_failing_specs):
        source = inspect.getsource(method)
        implement = source.index("implementer.implement(")
        assert source.rindex("watchdog.begin(", 0, implement) < implement
        assert source.index("self.restore_sources(before)", implement) < source.index("watchdog.end(in_flight)", implement)
    assert "watchdog.shutdown(" in inspect.getsource(m.main).split("finally:", 1)[1]
    save, load = inspect.getsource(m.Factory.save_checkpoint), inspect.getsource(m.Factory.load_checkpoint)
    assert "watchdog.checkpoint_ends(self)" in save and "watchdog.resume_ends(self, state" in load
    init = inspect.getsource(m.Factory.__init__)
    assert "self.hard_end_mono: float | None = platform_hard_end(MONO_STARTED)" in init

@case
def start_for_bounds_every_model_call_by_the_hard_end():
    fresh()
    from agent import watchdog
    from agent.llm import LLM
    from agent.settings import SETTINGS

    class F:
        pass

    f = F()
    f.hard_end_at, f.hard_end_mono = time.time() + 500.0, time.monotonic() + 500.0
    saved = SETTINGS.get("platform_watchdog_sec")
    try:
        SETTINGS["platform_watchdog_sec"] = 0
        assert watchdog.start_for(f, started_mono=time.monotonic(), started_wall=time.time(), log=lambda l: None) is None
        assert LLM.global_end == f.hard_end_mono
        f.hard_end_at = None
        watchdog.set_llm_end(f)
        assert LLM.global_end is None
    finally:
        SETTINGS["platform_watchdog_sec"] = saved
        fresh()

@case
def start_for_arms_it_with_the_factory_clean_up():
    fresh()
    from agent import watchdog
    from agent.llm import LLM

    class F:
        def clean_runtime_state(self):
            return None

    f = F()
    f.hard_end_at, f.hard_end_mono = time.time() + 81000.0, time.monotonic() + 81000.0
    lines: list[str] = []
    dog = watchdog.start_for(f, started_mono=time.monotonic(), started_wall=time.time(), log=lines.append)
    try:
        assert dog is not None and watchdog.active() is dog and dog.finish == f.clean_runtime_state
        assert 83690 < dog.left() <= 83700 and LLM.global_end == f.hard_end_mono
        assert lines and lines[0].startswith("[watchdog] armed: the agent exits by itself 23.25 h after the process start"), lines
    finally:
        dog.disarm()
        fresh()

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
    print(f"{len(CASES) - failed}/{len(CASES)} watchdog cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
