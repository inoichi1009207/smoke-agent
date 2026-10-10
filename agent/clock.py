from __future__ import annotations

import threading
import time

TICK_SEC = 15.0
GAP_SEC = 300.0

MAX_PAUSED_SEC = 12 * 3600.0

_paused = 0.0
_lock = threading.Lock()
_started = False

def now() -> float:
    return time.time() - _paused

def paused_sec() -> float:
    return _paused

def note_gap(gap: float, log=print) -> None:
    global _paused
    with _lock:
        gap = max(0.0, min(gap, MAX_PAUSED_SEC - _paused))
        _paused += gap
    if gap <= 0:
        return
    log(f"[clock] the machine was paused for {int(gap)} s (asleep?); that time is not counted against the budget")

def _watch(log) -> None:
    last = time.time()
    while True:
        time.sleep(TICK_SEC)
        current = time.time()
        gap = current - last - TICK_SEC
        if gap > GAP_SEC:
            note_gap(gap, log)
        last = current

def start(log=print) -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_watch, args=(log,), daemon=True, name="pause-clock").start()
