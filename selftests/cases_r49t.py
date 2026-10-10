from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

ROOT = Path.cwd() if (Path.cwd() / "main.py").is_file() else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = []
REAL_REPLACE = os.replace
REAL_WRITE_TEXT = Path.write_text
REAL_WRITE_BYTES = Path.write_bytes

def case(function):
    CASES.append(function)
    return function

def fresh():
    from agent import fsutil

    fsutil.STOPPING.clear()
    os.replace = REAL_REPLACE
    Path.write_text = REAL_WRITE_TEXT
    Path.write_bytes = REAL_WRITE_BYTES

def leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.rglob("*") if p.name.endswith((".arc-tmp", ".arc-restore")))

@case
def a_write_that_passed_the_check_never_replaces_the_watchdogs_restore():
    fresh()
    from agent import fsutil, source_io

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "App.tsx"
        target.write_text("accepted", encoding="utf-8")
        reached, go = threading.Event(), threading.Event()

        def gated(src, dst):
            if threading.current_thread().name == "main-like" and Path(dst) == target:
                reached.set()
                go.wait(5)
            return REAL_REPLACE(src, dst)

        os.replace = gated
        writer = threading.Thread(target=lambda: fsutil.write_text(target, "unaccepted"), name="main-like", daemon=True)
        writer.start()
        assert reached.wait(5)
        fsutil.STOPPING.set()
        dog = threading.Thread(target=lambda: source_io.put_back(target, b"accepted"), name="watchdog-restore", daemon=True)
        dog.start()
        time.sleep(0.3)
        go.set()
        dog.join(5)
        writer.join(2)
        time.sleep(0.2)
        assert target.read_text(encoding="utf-8") == "accepted", "the parked write replaced the restored file"
        assert not leftovers(Path(tmp)), leftovers(Path(tmp))

@case
def a_write_staged_before_the_stop_is_discarded_and_the_writer_parks():
    fresh()
    from agent import fsutil, source_io

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "server.js"
        target.write_text("accepted", encoding="utf-8")
        reached, go = threading.Event(), threading.Event()

        def gated(self, *args, **kwargs):
            result = REAL_WRITE_TEXT(self, *args, **kwargs)
            if threading.current_thread().name == "main-like" and self.name.endswith(".arc-tmp"):
                reached.set()
                go.wait(5)
            return result

        Path.write_text = gated
        writer = threading.Thread(target=lambda: fsutil.write_text(target, "unaccepted"), name="main-like", daemon=True)
        writer.start()
        assert reached.wait(5)
        fsutil.STOPPING.set()
        dog = threading.Thread(target=lambda: source_io.put_back(target, b"restored"), name="watchdog-restore", daemon=True)
        dog.start()
        dog.join(5)
        go.set()
        time.sleep(0.4)
        assert target.read_text(encoding="utf-8") == "restored", "the staged write landed after the stop"
        assert writer.is_alive(), "the writer must park, not return"
        assert not leftovers(Path(tmp)), leftovers(Path(tmp))

@case
def a_write_waiting_for_the_lock_rechecks_the_stop_while_holding_it():
    fresh()
    from agent import fsutil

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "App.tsx"
        target.write_text("restored", encoding="utf-8")
        held, release = threading.Event(), threading.Event()

        def watchdog_put_back():
            with fsutil.WRITE_LOCK:
                held.set()
                release.wait(5)

        dog = threading.Thread(target=watchdog_put_back, name="watchdog-restore", daemon=True)
        dog.start()
        assert held.wait(5)
        writer = threading.Thread(target=lambda: fsutil.write_text(target, "late"), name="main-like", daemon=True)
        writer.start()
        time.sleep(0.3)
        assert target.read_text(encoding="utf-8") == "restored", "write_text wrote while another thread held WRITE_LOCK"
        fsutil.STOPPING.set()
        release.set()
        dog.join(5)
        time.sleep(0.3)
        assert target.read_text(encoding="utf-8") == "restored", "the write landed after the stop"
        assert writer.is_alive()
        assert not leftovers(Path(tmp)), leftovers(Path(tmp))

    fresh()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "App.tsx"
        with fsutil.WRITE_LOCK:
            writer = threading.Thread(target=lambda: fsutil.write_text(target, "new"), daemon=True)
            writer.start()
            time.sleep(0.3)
            assert not target.exists()
        writer.join(5)
        assert target.read_text(encoding="utf-8") == "new"

@case
def the_watchdogs_own_threads_still_write_while_stopping():
    fresh()
    from agent import fsutil, source_io

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fsutil.STOPPING.set()
        done = []

        def restore():
            fsutil.write_text(root / "a.txt", "a")
            source_io.put_back(root / "b.txt", b"b")
            done.append(source_io.put_back_all([(root / "c.txt", b"c"), (root / "a.txt", None)]))

        worker = threading.Thread(target=restore, name="watchdog-restore", daemon=True)
        worker.start()
        worker.join(5)
        assert done == [(2, [])], done
        assert not (root / "a.txt").exists() and (root / "b.txt").read_bytes() == b"b" and (root / "c.txt").read_bytes() == b"c"

@case
def one_lock_for_every_writer_and_codegen_writes_through_it():
    fresh()
    from agent import codegen, description_pass, fsutil, source_io

    assert source_io.WRITE_LOCK is fsutil.WRITE_LOCK
    assert codegen.write_text is fsutil.write_text, "codegen's answers must be written through fsutil.write_text"
    assert description_pass.source_io is source_io
    names = {fsutil.temporary_name(Path("x/App.tsx"), ".arc-restore").name for _ in range(50)}
    assert len(names) == 50 and all(name.startswith("App.tsx.") and name.endswith(".arc-restore") for name in names)

def failing_world(which: str, failures: int):
    fresh()
    from agent import fsutil, source_io

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "server.js"
        target.write_text("old", encoding="utf-8")
        plain, calls = [], []

        def spy_text(self, *args, **kwargs):
            if self == target:
                plain.append("write_text")
            return REAL_WRITE_TEXT(self, *args, **kwargs)

        def spy_bytes(self, *args, **kwargs):
            if self == target:
                plain.append("write_bytes")
            return REAL_WRITE_BYTES(self, *args, **kwargs)

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) <= failures:
                raise PermissionError(13, "the target is in use", str(dst))
            return REAL_REPLACE(src, dst)

        os.replace, Path.write_text, Path.write_bytes = flaky, spy_text, spy_bytes
        error = None
        try:
            if which == "write_text":
                fsutil.write_text(target, "new")
            elif which == "put_back":
                source_io.put_back(target, b"new")
            else:
                changed, failed = source_io.put_back_all([(target, b"new")])
                if failed:
                    error = OSError(f"failed: {failed}")
        except OSError as exc:
            error = exc
        finally:
            fresh()
        return target.read_text(encoding="utf-8"), error, plain, leftovers(Path(tmp))

@case
def a_replace_that_keeps_failing_leaves_the_old_file_and_says_so():
    for which in ("write_text", "put_back", "put_back_all"):
        content, error, plain, left = failing_world(which, 1000)
        assert content == "old", (which, content)
        assert error is not None, f"{which}: a failed replace must be reported"
        assert plain == [], f"{which}: plain write of the target as a fallback: {plain}"
        assert left == [], (which, left)

@case
def a_replace_that_fails_for_a_moment_is_tried_again():
    for which in ("write_text", "put_back", "put_back_all"):
        content, error, plain, left = failing_world(which, 2)
        assert (content, error, plain, left) == ("new", None, [], []), (which, content, error, plain, left)

@case
def a_restore_hanging_while_staging_leaves_the_tree_as_it_was():
    fresh()
    from agent import source_io

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "entry.js").write_text("newApi entry", encoding="utf-8")
        (root / "impl.js").write_text("newApi impl", encoding="utf-8")
        hang = threading.Event()

        def hanging(self, data):
            if self.name.startswith("impl.js"):
                hang.wait(5)
            return REAL_WRITE_BYTES(self, data)

        Path.write_bytes = hanging
        items = [(root / "entry.js", b"oldApi entry"), (root / "impl.js", b"oldApi impl")]
        worker = threading.Thread(target=lambda: source_io.put_back_all(items), name="watchdog-restore", daemon=True)
        worker.start()
        worker.join(0.5)
        state = ((root / "entry.js").read_text(encoding="utf-8"), (root / "impl.js").read_text(encoding="utf-8"))
        hang.set()
        worker.join(5)
        Path.write_bytes = REAL_WRITE_BYTES
        assert state == ("newApi entry", "newApi impl"), f"mixed tree at the bound: {state}"
        assert ((root / "entry.js").read_text(encoding="utf-8"), (root / "impl.js").read_text(encoding="utf-8")) == \
            ("oldApi entry", "oldApi impl")
        assert not leftovers(root), leftovers(root)

@case
def put_back_all_swaps_in_one_locked_pass_kept_files_before_deletes():
    fresh()
    from agent import fsutil, source_io

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, text in {"a.ts": "changed", "b.ts": "same", "added1.ts": "x", "sub/added2.ts": "y"}.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        events, passes = [], []
        real_guarded = fsutil.guarded

        def counting(action, abandon=None):
            passes.append(1)
            return real_guarded(action, abandon)

        def spy_replace(src, dst):
            events.append(("replace", Path(dst).name, fsutil.WRITE_LOCK._is_owned()))
            return REAL_REPLACE(src, dst)

        real_unlink = Path.unlink

        def spy_unlink(self, *args, **kwargs):
            if not self.name.endswith((".arc-restore", ".arc-tmp")):
                events.append(("delete", self.name, fsutil.WRITE_LOCK._is_owned()))
            return real_unlink(self, *args, **kwargs)

        fsutil.guarded, os.replace, Path.unlink = counting, spy_replace, spy_unlink
        try:
            result = source_io.put_back_all([(root / "a.ts", b"original"), (root / "b.ts", b"same"), (root / "new.ts", b"back"),
                                             (root / "added1.ts", None), (root / "sub/added2.ts", None), (root / "gone.ts", None)])
        finally:
            fsutil.guarded, Path.unlink = real_guarded, real_unlink
            fresh()
        assert result == (4, []), result
        assert len(passes) == 1, f"one locked pass, not one per file: {len(passes)}"
        assert [kind for kind, _, _ in events] == ["replace", "replace", "delete", "delete"], events
        assert all(locked for _, _, locked in events), f"a move or delete outside WRITE_LOCK: {events}"
        assert (root / "a.ts").read_bytes() == b"original" and (root / "new.ts").read_bytes() == b"back"
        assert not (root / "added1.ts").exists() and not (root / "sub/added2.ts").exists()
        assert not leftovers(root), leftovers(root)

@case
def a_main_thread_restore_that_finds_the_stop_while_staging_discards_and_parks():
    fresh()
    from agent import fsutil, source_io

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.ts").write_text("in flight a", encoding="utf-8")
        (root / "b.ts").write_text("in flight b", encoding="utf-8")
        reached, go = threading.Event(), threading.Event()
        after_stop: list = []

        def gated(self, data):
            if fsutil.STOPPING.is_set():
                after_stop.append(self.name)
            result = REAL_WRITE_BYTES(self, data)
            if threading.current_thread().name == "main-like" and self.name.startswith("a.ts."):
                reached.set()
                go.wait(5)
            return result

        Path.write_bytes = gated
        writer = threading.Thread(target=lambda: source_io.put_back_all([(root / "a.ts", b"old a"), (root / "b.ts", b"old b")]),
                                  name="main-like", daemon=True)
        writer.start()
        assert reached.wait(5)
        fsutil.STOPPING.set()
        go.set()
        time.sleep(0.4)
        Path.write_bytes = REAL_WRITE_BYTES
        assert writer.is_alive(), "the main thread must park"
        assert (root / "a.ts").read_text(encoding="utf-8") == "in flight a" and (root / "b.ts").read_text(encoding="utf-8") == "in flight b"
        assert not leftovers(root), f"staged files left behind: {leftovers(root)}"
        assert after_stop == [], f"staging went on after the stop (I/O racing the watchdog's restore): {after_stop}"

@case
def a_restore_that_compared_while_a_write_held_the_lock_judges_again_under_it():
    fresh()
    from agent import fsutil, source_io

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "App.tsx"
        target.write_text("accepted", encoding="utf-8")
        reached, go = threading.Event(), threading.Event()

        def gated(src, dst):
            if threading.current_thread().name == "main-like" and Path(dst) == target:
                reached.set()
                go.wait(5)
            return REAL_REPLACE(src, dst)

        os.replace = gated
        writer = threading.Thread(target=lambda: fsutil.write_text(target, "unaccepted"), name="main-like", daemon=True)
        writer.start()
        assert reached.wait(5)
        fsutil.STOPPING.set()
        result: list = []
        dog = threading.Thread(target=lambda: result.append(source_io.put_back_all([(target, b"accepted")])),
                               name="watchdog-restore", daemon=True)
        dog.start()
        time.sleep(0.3)
        go.set()
        dog.join(5)
        writer.join(2)
        assert target.read_text(encoding="utf-8") == "accepted", "the write that held the lock stayed after the restore"
        assert result == [(1, [])], result

@case
def late_deletes_are_named_under_the_lock_and_deleted():
    fresh()
    from agent import fsutil, source_io

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "kept.ts").write_text("new", encoding="utf-8")
        seen: list = []

        def late():
            seen.append(fsutil.WRITE_LOCK._is_owned())
            (root / "created-meanwhile.ts").write_text("x", encoding="utf-8")
            return [root / "created-meanwhile.ts"]

        assert source_io.put_back_all([(root / "kept.ts", b"old")], late_deletes=late) == (2, [])
        assert seen == [True], seen
        assert not (root / "created-meanwhile.ts").exists() and (root / "kept.ts").read_bytes() == b"old"

@case
def a_nested_write_never_parks_while_holding_the_lock():
    fresh()
    from agent import fsutil, source_io

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "a.ts"

        def outer():
            fsutil.STOPPING.set()
            fsutil.write_text(target, "inner")

        worker = threading.Thread(target=lambda: source_io.guarded(outer), name="main-like", daemon=True)
        worker.start()
        worker.join(3)
        assert not worker.is_alive(), "the nested write parked holding WRITE_LOCK"
        got = fsutil.WRITE_LOCK.acquire(timeout=1)
        assert got, "WRITE_LOCK still held"
        fsutil.WRITE_LOCK.release()

@case
def a_write_that_cannot_be_encoded_leaves_no_temporary_file():
    fresh()
    from agent import fsutil

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "App.jsx"
        target.write_text("old", encoding="utf-8")
        for _ in range(2):
            try:
                fsutil.write_text(target, "bad \ud800 surrogate")
            except UnicodeEncodeError:
                pass
        assert target.read_text(encoding="utf-8") == "old"
        assert not leftovers(Path(tmp)), leftovers(Path(tmp))

@case
def a_factory_restore_hanging_while_staging_leaves_the_tree_as_it_was():
    import main as m
    from agent import fsutil, watchdog

    watchdog._ACTIVE = None
    fsutil.STOPPING.clear()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, text in {"backend/src/entry.js": "oldApi entry", "backend/src/impl.js": "oldApi impl"}.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        f = m.Factory.__new__(m.Factory)
        f.project_dir, f.restore_failures = root, 0
        snapshot = f.snapshot_sources()
        (root / "backend/src/entry.js").write_text("newApi entry", encoding="utf-8")
        (root / "backend/src/impl.js").write_text("newApi impl", encoding="utf-8")
        hang = threading.Event()

        def hanging(self, data):
            if self.name.startswith("impl.js"):
                hang.wait(5)
            return REAL_WRITE_BYTES(self, data)

        Path.write_bytes = hanging
        try:
            worker = threading.Thread(target=lambda: f.restore_sources(snapshot), name="watchdog-restore", daemon=True)
            worker.start()
            worker.join(0.5)
            state = tuple((root / rel).read_text(encoding="utf-8") for rel in ("backend/src/entry.js", "backend/src/impl.js"))
            hang.set()
            worker.join(5)
        finally:
            Path.write_bytes = REAL_WRITE_BYTES
        assert state == ("newApi entry", "newApi impl"), f"mixed tree at the bound: {state}"
        assert f.snapshot_sources() == snapshot

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
    print(f"{len(CASES) - failed}/{len(CASES)} r49t cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
