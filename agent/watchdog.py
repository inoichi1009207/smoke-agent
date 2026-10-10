from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any, Callable

from agent import fsutil

RESTORE_BOUND_SEC = 60.0
FINISH_BOUND_SEC = 30.0
CHILDREN_BOUND_SEC = 30.0
POLL_SEC = 30.0
BEFORE_LIMIT_SEC = 2700.0
DEFAULT_SEC = 86400.0 - BEFORE_LIMIT_SEC
BACKSTOP_AFTER_SEC = 900.0
LOG_BOUND_SEC = 5.0

def watchdog_seconds() -> float | None:
    from agent.settings import setting_seconds

    limit = setting_seconds("platform_time_limit_sec", 86400.0)
    if limit <= 0:
        return None
    seconds = setting_seconds("platform_watchdog_sec", DEFAULT_SEC)
    if seconds <= 0:
        return None
    return min(seconds, limit - BEFORE_LIMIT_SEC) if limit > BEFORE_LIMIT_SEC else seconds

class Watchdog:
    def __init__(self, seconds: float, *, started_mono: float, started_wall: float, log: Callable[[str], Any] = print,
                 exit: Callable[[int], Any] = os._exit, monotonic: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time, wait: Callable[[float], Any] | None = None,
                 stop_children: Callable[..., int] | None = None, restore_bound: float = RESTORE_BOUND_SEC) -> None:
        self.seconds = float(seconds)
        self.started_mono = float(started_mono)
        self.end_mono = float(started_mono) + self.seconds
        self.end_wall = float(started_wall) + self.seconds
        self.log = log
        self.exit = exit
        self.monotonic = monotonic
        self.wall = wall
        self._stop = threading.Event()
        self.wait = wait or self._stop.wait
        self.stop_children = stop_children or fsutil.stop_children
        self.restore_bound = float(restore_bound)
        self.finish: Callable[[], Any] | None = None
        self.backstop_sec = CHILDREN_BOUND_SEC + self.restore_bound + FINISH_BOUND_SEC + 30.0
        self.fired = False
        self.firing = threading.Event()
        self.thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._inflight: list[tuple[int, str, Callable[[], Any]]] = []
        self._next = 0
        self._notes: list[str] | None = None

    def left(self) -> float:
        return min(self.end_mono - self.monotonic(), self.end_wall - self.wall())

    def move_end(self, wall_end: float) -> bool:
        wall_end = float(wall_end)
        if wall_end >= self.end_wall:
            return False
        remaining = wall_end - self.wall()
        self.end_wall = wall_end
        self.end_mono = min(self.end_mono, self.monotonic() + remaining)
        self.arm_backstop()
        return True

    def begin(self, what: str, restore: Callable[[], Any]) -> int:
        with self._lock:
            self._next += 1
            self._inflight.append((self._next, str(what), restore))
            return self._next

    def end(self, token: int | None) -> None:
        if token is None:
            return
        with self._lock:
            for index, (known, _, _) in enumerate(self._inflight):
                if known == token:
                    del self._inflight[index:]
                    return

    def current(self) -> tuple[str, Callable[[], Any]] | None:
        with self._lock:
            if not self._inflight:
                return None
            _, what, restore = self._inflight[-1]
            return what, restore

    def start(self) -> "Watchdog":
        self.thread = threading.Thread(target=self.run, daemon=True, name="platform-watchdog")
        self.thread.start()
        self.arm_backstop()
        return self

    def arm_backstop(self) -> None:
        if self.exit is not os._exit:
            return
        try:
            import faulthandler

            if getattr(self, "_backstop_file", None) is None:
                self._backstop_file = open(os.devnull, "w")
            faulthandler.dump_traceback_later(max(1.0, self.left()) + BACKSTOP_AFTER_SEC, exit=True, file=self._backstop_file)
        except Exception:
            pass

    def run(self) -> None:
        while not self._stop.is_set():
            left = self.left()
            if left <= 0:
                self.fire()
                return
            self.wait(min(left, POLL_SEC))

    def disarm(self) -> None:
        self._stop.set()
        if self.exit is os._exit:
            try:
                import faulthandler

                faulthandler.cancel_dump_traceback_later()
            except Exception:
                pass

    def fire(self) -> None:
        with self._lock:
            if self.fired or self._stop.is_set():
                return
            self.fired = True
            self.firing.set()

            self._notes = []

        backstop = threading.Timer(self.backstop_sec, self.exit, (0,))
        backstop.daemon = True
        backstop.start()
        try:
            elapsed = self.monotonic() - self.started_mono
            inflight = self.current()
            self._say(f"[watchdog] {int(elapsed)} s after the process start (platform_watchdog_sec {int(self.seconds)}): the run "
                      "did not end in time; stopping the agent's child processes"
                      + (f", putting back the state before '{inflight[0]}'" if inflight else "")
                      + " and exiting with 0 so that the platform runs its tests on the workspace")
            fsutil.begin_stop()
            try:
                stopped = self.stop_children(log=self._say)
                self._say(f"[watchdog] {stopped} child process(es) stopped")
            except Exception as exc:
                self._say(f"[watchdog] stopping the child processes failed: {exc!r}")
            if inflight is not None:
                self._restore(*inflight)
            if self.finish is not None:

                self._bounded("the runtime clean-up", self.finish, FINISH_BOUND_SEC)
        finally:
            self._emit_notes()
            backstop.cancel()
            self.exit(0)

    def _emit_notes(self) -> None:
        with self._lock:
            notes, self._notes = list(self._notes or []), None

        def target() -> None:
            for line in notes:
                try:
                    self.log(line)
                except Exception:
                    pass
            self._flush()

        writer = threading.Thread(target=target, daemon=True, name="watchdog-log")
        writer.start()
        writer.join(LOG_BOUND_SEC)

    def _restore(self, what: str, restore: Callable[[], Any]) -> None:
        done, result = self._bounded(f"putting back the state before '{what}'", restore, self.restore_bound)
        if done:
            self._say(f"[watchdog] state before '{what}' put back ({result} file(s))")

    def _bounded(self, label: str, function: Callable[[], Any], bound: float) -> tuple[bool, Any]:
        outcome: list[Any] = []

        def target() -> None:
            try:
                outcome.append(function())
            except Exception as exc:
                outcome.append(exc)

        worker = threading.Thread(target=target, daemon=True, name="watchdog-restore")
        worker.start()
        worker.join(bound)
        if worker.is_alive():
            self._say(f"[watchdog] {label} did not finish in {int(bound)} s; exiting anyway")
            return False, None
        if outcome and isinstance(outcome[0], Exception):
            self._say(f"[watchdog] {label} failed: {outcome[0]!r}")
            return False, None
        return True, (outcome[0] if outcome else None)

    def _say(self, message: str) -> None:
        notes = self._notes
        if notes is not None:
            notes.append(message)
            return
        try:
            self.log(message)
        except Exception:
            pass

    @staticmethod
    def _flush() -> None:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass

_ACTIVE: Watchdog | None = None

def active() -> Watchdog | None:
    return _ACTIVE

def arm(seconds: float | None, *, started_mono: float, started_wall: float, log: Callable[[str], Any] = print,
        **kwargs: Any) -> Watchdog | None:
    global _ACTIVE
    if seconds is None or seconds <= 0:
        return None
    if _ACTIVE is not None and not _ACTIVE._stop.is_set():
        return _ACTIVE
    _ACTIVE = Watchdog(seconds, started_mono=started_mono, started_wall=started_wall, log=log, **kwargs).start()
    return _ACTIVE

def arm_early(*, started_mono: float, started_wall: float) -> Watchdog | None:
    try:
        return arm(watchdog_seconds(), started_mono=started_mono, started_wall=started_wall, log=print)
    except Exception:
        return None

def attach(*, finish: Callable[[], Any] | None = None, log: Callable[[str], Any] | None = None) -> Watchdog | None:
    dog = _ACTIVE
    if dog is None or dog._stop.is_set():
        return None
    if log is not None:
        dog.log = log
    if finish is not None:
        dog.finish = finish
    return dog

def begin(what: str, restore: Callable[[], Any]) -> int | None:
    dog = _ACTIVE
    return dog.begin(what, restore) if dog is not None else None

def begin_snapshot(factory: Any, what: str) -> int | None:
    dog = _ACTIVE
    if dog is None:
        return None
    snapshot = factory.snapshot_sources()
    return dog.begin(what, lambda: factory.restore_sources(snapshot))

def end(token: int | None) -> None:
    dog = _ACTIVE
    if dog is not None:
        dog.end(token)

def park() -> None:
    fsutil.park_if_stopping()

def shutdown(log: Callable[[str], Any] = print) -> None:
    dog = _ACTIVE
    if dog is not None and dog.firing.is_set():
        if dog.thread is not None and dog.thread is not threading.current_thread():
            dog.thread.join(dog.restore_bound + CHILDREN_BOUND_SEC + FINISH_BOUND_SEC + 30.0)
        return
    if dog is None:
        return
    try:
        stopped = fsutil.stop_children(log=log)
        if stopped:
            log(f"[watchdog] run ended; {stopped} child process(es) still running were stopped")
    except Exception as exc:
        log(f"[watchdog] stopping the child processes at the end failed: {exc!r}")

def start_for(factory: Any, *, started_mono: float, started_wall: float, log: Callable[[str], Any] = print) -> Watchdog | None:
    set_llm_end(factory)
    seconds = watchdog_seconds()
    dog = arm(seconds, started_mono=started_mono, started_wall=started_wall, log=log)
    if dog is not None:
        dog.log = log
        dog.finish = getattr(factory, "clean_runtime_state", None)
        log(f"[watchdog] armed: the agent exits by itself {dog.seconds / 3600.0:.2f} h after the process start "
            f"({int(dog.left())} s from now) if the run has not ended")
    return dog

def set_llm_end(factory: Any) -> None:
    from agent.llm import LLM

    mono = getattr(factory, "__dict__", {}).get("hard_end_mono")
    LLM.global_end = None if mono is None or getattr(factory, "hard_end_at", None) is None else float(mono)

def checkpoint_ends(factory: Any) -> dict[str, float]:
    ends: dict[str, float] = {}
    hard = getattr(factory, "hard_end_at", None)
    if hard is not None:
        ends["hard_end_wall"] = float(hard)
    dog = _ACTIVE
    if dog is not None:
        ends["watchdog_end_wall"] = float(dog.end_wall)
    return ends

def resume_ends(factory: Any, state: dict[str, Any], *, log: Callable[[str], Any] = print,
                monotonic: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
    from agent.settings import setting_seconds

    stored = state.get("hard_end_wall")
    stored_dog = state.get("watchdog_end_wall")
    limits = []
    if isinstance(stored_dog, (int, float)):
        limits.append(float(stored_dog) + BEFORE_LIMIT_SEC)
    if isinstance(stored, (int, float)):
        limits.append(float(stored) + max(0.0, setting_seconds("platform_time_margin_sec", 5400.0)))
    if limits and wall() > min(limits):
        log(f"[resume] the checkpoint's time limit passed {int(wall() - min(limits))} s ago: that task is over; "
            "this start keeps its own ends")
        set_llm_end(factory)
        return
    current = getattr(factory, "hard_end_at", None)
    if current is not None and isinstance(stored, (int, float)) and float(stored) < float(current):
        factory.hard_end_at = float(stored)
        factory.hard_end_mono = monotonic() + (float(stored) - wall())
        log(f"[resume] the hard end of the first start is kept: {int(float(stored) - wall())} s from now")
    set_llm_end(factory)
    dog = _ACTIVE
    if dog is not None and isinstance(stored_dog, (int, float)) and dog.move_end(float(stored_dog)):
        log(f"[resume] the watchdog end of the first start is kept: {int(dog.left())} s from now")
