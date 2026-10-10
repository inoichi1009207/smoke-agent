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
REAL_WRITE_BYTES = Path.write_bytes

def case(function):
    CASES.append(function)
    return function

def fresh():
    from agent import fsutil, source_io

    fsutil.STOPPING.clear()
    os.replace = REAL_REPLACE
    Path.write_bytes = REAL_WRITE_BYTES
    source_io._same = REAL_SAME

def _real_same():
    from agent import source_io

    return source_io._same

REAL_SAME = _real_same()

def leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.rglob("*") if p.name.endswith((".arc-tmp", ".arc-restore")))

def files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}

def new_version(root: Path) -> dict[str, bytes]:
    tree = {"entry.js": b"newApi entry", "impl.js": b"newApi impl", "util.js": b"newApi util", "added.js": b"newApi helper"}
    for rel, data in tree.items():
        (root / rel).write_bytes(data)
    return tree

def snapshot_items(root: Path):
    return [(root / "entry.js", b"oldApi entry"), (root / "impl.js", b"oldApi impl"), (root / "util.js", b"oldApi util"),
            (root / "added.js", None)]

@case
def a_file_that_cannot_be_staged_cancels_the_whole_put_back():
    fresh()
    from agent import source_io

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        before = new_version(root)

        def failing(self, data):
            if self.name.startswith("impl.js."):
                raise OSError(28, "no space left on device")
            return REAL_WRITE_BYTES(self, data)

        Path.write_bytes = failing
        try:
            changed, failed = source_io.put_back_all(snapshot_items(root))
        finally:
            fresh()
        assert files(root) == before, f"a staging failure still moved / deleted files: {files(root)}"
        assert changed == 0, changed
        assert failed and failed[0] == root / "impl.js", failed
        assert set(failed) == {root / rel for rel in ("entry.js", "impl.js", "util.js", "added.js")}, failed
        assert not leftovers(root), leftovers(root)

@case
def a_move_that_keeps_failing_stops_the_pass_and_reports_the_rest():
    fresh()
    from agent import fsutil, source_io

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        new_version(root)
        tried: list[str] = []

        def flaky(src, dst):
            tried.append(Path(dst).name)
            if Path(dst).name == "impl.js":
                raise PermissionError(13, "the file is in use", str(dst))
            return REAL_REPLACE(src, dst)

        pauses, fsutil.REPLACE_RETRY_SEC = fsutil.REPLACE_RETRY_SEC, (0.0,)
        os.replace = flaky
        try:
            changed, failed = source_io.put_back_all(snapshot_items(root))
        finally:
            fsutil.REPLACE_RETRY_SEC = pauses
            fresh()
        assert "util.js" not in tried, f"the pass went on after a move that kept failing: {tried}"
        assert (root / "added.js").exists(), "a delete ran after a move that kept failing"
        assert (root / "util.js").read_bytes() == b"newApi util" and (root / "impl.js").read_bytes() == b"newApi impl"
        assert changed == 1 and (root / "entry.js").read_bytes() == b"oldApi entry", (changed, files(root))
        assert failed == [root / "impl.js", root / "util.js", root / "added.js"], failed
        assert not leftovers(root), leftovers(root)

@case
def a_failing_delete_is_reported_and_the_other_deletes_go_on():
    fresh()
    from agent import source_io

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel in ("a.ts", "b.ts"):
            (root / rel).write_bytes(b"added")
        real_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):
            if self.name == "a.ts":
                raise PermissionError(13, "in use", str(self))
            return real_unlink(self, *args, **kwargs)

        Path.unlink = failing_unlink
        try:
            result = source_io.put_back_all([(root / "a.ts", None), (root / "b.ts", None)])
        finally:
            Path.unlink = real_unlink
        assert result == (1, [root / "a.ts"]), result
        assert (root / "a.ts").exists() and not (root / "b.ts").exists()

def race_before_the_lock(root: Path, name: str):
    from agent import fsutil, source_io

    held, go = threading.Event(), threading.Event()
    writes: list[tuple[str, bool]] = []
    passes: list[int] = []
    real_guarded = fsutil.guarded

    def holder():
        with fsutil.WRITE_LOCK:
            held.set()
            go.wait(5)
            REAL_WRITE_BYTES(root / name, b"late write")

    def spy(self, data):
        if threading.current_thread().name == "restore-like":
            writes.append((self.name.split(".")[0], fsutil.WRITE_LOCK._is_owned()))
        return REAL_WRITE_BYTES(self, data)

    def counting(action, abandon=None):
        passes.append(1)
        return real_guarded(action, abandon)

    keeper = threading.Thread(target=holder, daemon=True)
    keeper.start()
    assert held.wait(5)
    Path.write_bytes, fsutil.guarded = spy, counting
    out: list = []
    try:
        worker = threading.Thread(target=lambda: out.append(source_io.put_back_all([(root / "a", b"old a"), (root / name, b"old b")])),
                                  name="restore-like", daemon=True)
        worker.start()
        time.sleep(0.3)
        go.set()
        worker.join(5)
        keeper.join(5)
    finally:
        fsutil.guarded = real_guarded
        fresh()
    return (out[0] if out else None), writes, len(passes)

@case
def a_file_changed_after_the_compare_is_staged_outside_the_lock():
    fresh()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a").write_bytes(b"new a")
        (root / "b").write_bytes(b"old b")
        result, writes, passes = race_before_the_lock(root, "b")
        assert result == (2, []), result
        assert (root / "a").read_bytes() == b"old a" and (root / "b").read_bytes() == b"old b", files(root)
        assert ("b", False) in writes, writes
        assert not [w for w in writes if w[1]], f"content written inside the locked pass: {writes}"
        assert passes == 2, passes
        assert not leftovers(root), leftovers(root)

@case
def restaging_is_bounded_then_written_inside_the_pass_as_before():
    fresh()
    from agent import fsutil, source_io

    assert source_io.RESTAGE_TRIES == 2
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        names = ["b", "c", "d"]
        (root / "a").write_bytes(b"new a")
        for name in names:
            (root / name).write_bytes(b"late " + name.encode())
        passes: list[int] = []
        writes: list[tuple[str, bool]] = []
        real_guarded = fsutil.guarded

        def counting(action, abandon=None):
            passes.append(1)
            return real_guarded(action, abandon)

        def fickle(path, content):
            name = Path(path).name
            if name in names and not (fsutil.WRITE_LOCK._is_owned() and names.index(name) < len(passes)):
                return True
            return REAL_SAME(path, content)

        def spy(self, data):
            writes.append((self.name.split(".")[0], fsutil.WRITE_LOCK._is_owned()))
            return REAL_WRITE_BYTES(self, data)

        source_io._same, fsutil.guarded, Path.write_bytes = fickle, counting, spy
        try:
            result = source_io.put_back_all([(root / "a", b"old a")] + [(root / n, b"old " + n.encode()) for n in names])
        finally:
            fsutil.guarded = real_guarded
            fresh()
        assert passes == [1] * (source_io.RESTAGE_TRIES + 1), passes
        assert result == (4, []), result
        assert files(root) == {"a": b"old a", "b": b"old b", "c": b"old c", "d": b"old d"}, files(root)
        assert [w for w in writes if w[1]] == [("d", True)], f"only the last change is written inside the pass: {writes}"
        assert not leftovers(root), leftovers(root)

@case
def a_nested_put_back_all_never_parks_holding_the_lock():
    fresh()
    from agent import fsutil, source_io

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.ts").write_bytes(b"in flight")
        result: list = []

        def outer():
            fsutil.STOPPING.set()
            result.append(source_io.put_back_all([(root / "a.ts", b"accepted")]))

        worker = threading.Thread(target=lambda: source_io.guarded(outer), name="main-like", daemon=True)
        worker.start()
        worker.join(3)
        alive = worker.is_alive()
        got = fsutil.WRITE_LOCK.acquire(timeout=1)
        if got:
            fsutil.WRITE_LOCK.release()
        fresh()
        assert not alive, "put_back_all parked holding WRITE_LOCK"
        assert got, "WRITE_LOCK still held"
        assert result == [(1, [])] and (root / "a.ts").read_bytes() == b"accepted", result

@case
def a_factory_restore_whose_staging_fails_leaves_the_tree_and_books_it():
    import main as m
    from agent import watchdog

    fresh()
    watchdog._ACTIVE = None
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
        (root / "backend/src/added.js").write_text("newApi helper", encoding="utf-8")
        before = f.snapshot_sources()

        def failing(self, data):
            if self.name.startswith("impl.js."):
                raise OSError(28, "no space left on device")
            return REAL_WRITE_BYTES(self, data)

        Path.write_bytes = failing
        try:
            f.restore_sources(snapshot)
        finally:
            fresh()
        assert f.snapshot_sources() == before, "a staging failure left a mixed tree"
        assert f.restore_failures == 1, f.restore_failures

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
    print(f"{len(CASES) - failed}/{len(CASES)} r50t cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
