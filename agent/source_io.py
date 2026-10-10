from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Iterable

from agent import fsutil

WRITE_LOCK = fsutil.WRITE_LOCK
TEMP_SUFFIX = ".arc-restore"

def _watchdog_thread() -> bool:
    return fsutil.is_watchdog_thread()

def guarded(action: Callable[[], Any], abandon: Callable[[], Any] | None = None) -> Any:
    return fsutil.guarded(action, abandon)

def replace_bytes(path: Path, content: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = fsutil.temporary_name(path, TEMP_SUFFIX)
    try:
        temporary.write_bytes(content)
    except OSError:
        _discard([temporary])
        raise
    fsutil.replace_or_leave(temporary, path)

def put_back(path: Path, content: bytes | None) -> None:
    path = Path(path)
    if content is None:
        guarded(lambda: path.unlink(missing_ok=True))
    else:
        guarded(lambda: replace_bytes(path, content))

def _discard(temporaries: Iterable[Path]) -> None:
    for temporary in temporaries:
        try:
            Path(temporary).unlink(missing_ok=True)
        except OSError:
            pass

def _same(path: Path, content: bytes) -> bool:
    try:
        return path.is_file() and path.read_bytes() == content
    except OSError:
        return False

RESTAGE_TRIES = 2

def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()

def put_back_all(items: Iterable[tuple[Path, bytes | None]],
                 late_deletes: Callable[[], Iterable[Path]] | None = None) -> tuple[int, list[Path]]:
    items = [(Path(path), content) for path, content in items]
    writes: list[tuple[Path, bytes]] = []
    deletes: list[Path] = []
    unchanged: list[tuple[Path, bytes | None]] = []
    for path, content in items:
        if content is None:
            if _exists(path):
                deletes.append(path)
            else:
                unchanged.append((path, None))
        elif not _same(path, content):
            writes.append((path, content))
        else:
            unchanged.append((path, content))
    staged: list[tuple[Path, Path]] = []

    def discard_staged() -> None:
        _discard(temporary for temporary, _ in staged)

    def stage(batch: list[tuple[Path, bytes]]) -> Path | None:
        for path, content in batch:
            if fsutil.STOPPING.is_set() and not _watchdog_thread() and not fsutil.holds_write_lock():
                discard_staged()
                fsutil.park_if_stopping()
                raise _Parked()
            temporary = fsutil.temporary_name(path, TEMP_SUFFIX)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_bytes(content)
                staged.append((temporary, path))
            except OSError:
                _discard([temporary])
                return path
        return None

    def cancelled(first: Path) -> tuple[int, list[Path]]:
        rest = [target for _, target in staged] + [path for path, _ in writes] + deletes
        return 0, list(dict.fromkeys([first] + rest))

    def swap(final: bool) -> Any:
        moved = [(path, content) for path, content in unchanged if content is not None and not _same(path, content)]
        if moved and not final:
            return moved
        changed = 0
        failed: list[Path] = []
        steps: list[tuple[Path, Callable[[], None]]] = [
            (path, lambda temporary=temporary, path=path: fsutil.replace_or_leave(temporary, path))
            for temporary, path in staged]
        steps += [(path, lambda path=path, content=content: replace_bytes(path, content)) for path, content in moved]
        for index, (path, step) in enumerate(steps):
            try:
                step()
                changed += 1
            except OSError:

                left = [target for target, _ in steps[index:]] + [target for target in deletes if _exists(target)]
                return changed, list(dict.fromkeys(left))
        late: list[Path] = [path for path, content in unchanged if content is None and _exists(path)]
        if late_deletes is not None:
            try:
                late.extend(Path(path) for path in late_deletes())
            except OSError:
                pass
        for path in list(dict.fromkeys(deletes + late)):
            try:
                if _exists(path):
                    path.unlink()
                    changed += 1
            except OSError:
                failed.append(path)
        return changed, failed

    try:
        pending: list[tuple[Path, bytes]] = writes
        for attempt in range(RESTAGE_TRIES + 1):
            bad = stage(pending)
            if bad is not None:
                return cancelled(bad)
            result = guarded(lambda: swap(attempt == RESTAGE_TRIES), abandon=discard_staged)
            if result is None:
                return 0, []
            if isinstance(result, tuple):
                return int(result[0]), result[1]
            pending = result
            writes = writes + pending
            unchanged = [entry for entry in unchanged if entry not in pending]
        return 0, []
    except _Parked:
        return 0, []
    finally:
        _discard(temporary for temporary, _ in staged if temporary.exists())

class _Parked(Exception):
    pass
