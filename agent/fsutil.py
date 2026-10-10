from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

IGNORED_DIRS = {"node_modules", "dist", "dist-ssr", ".git", ".arc", "coverage", ".arc-test-db", "test-results", "playwright-report"}
IS_WINDOWS = os.name == "nt"

_CHILDREN: dict[int, tuple[subprocess.Popen, int | None]] = {}
_CHILDREN_LOCK = threading.Lock()
STOPPING = threading.Event()

def register_child(process: subprocess.Popen, own_group: bool = False) -> None:
    pgid = process.pid if own_group else None
    with _CHILDREN_LOCK:
        if not STOPPING.is_set():
            _CHILDREN[process.pid] = (process, pgid)
            return
    _kill_tree(process, own_group)

def unregister_child(process: subprocess.Popen) -> None:
    with _CHILDREN_LOCK:
        known = _CHILDREN.get(process.pid)
        if known is not None and known[0] is process:
            del _CHILDREN[process.pid]

def registered_children() -> list[subprocess.Popen]:
    with _CHILDREN_LOCK:
        return [process for process, _ in _CHILDREN.values()]

def begin_stop() -> None:
    with _CHILDREN_LOCK:
        STOPPING.set()

def is_watchdog_thread() -> bool:
    return threading.current_thread().name.startswith(("platform-watchdog", "watchdog-"))

def park_if_stopping() -> None:
    if STOPPING.is_set() and not is_watchdog_thread():
        threading.Event().wait()

_park_if_stopping = park_if_stopping

WRITE_LOCK = threading.RLock()
REPLACE_RETRY_SEC = (0.05, 0.1, 0.25, 0.5)
_TEMP_COUNTER = iter(range(1, 1 << 62))
_TEMP_COUNTER_LOCK = threading.Lock()

def temporary_name(path: Path, suffix: str) -> Path:
    with _TEMP_COUNTER_LOCK:
        number = next(_TEMP_COUNTER)
    return path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}-{number}{suffix}")

def holds_write_lock() -> bool:
    owned = getattr(WRITE_LOCK, "_is_owned", None)
    return bool(owned is not None and owned())

def guarded(action, abandon=None):
    if holds_write_lock():
        return action()
    if not (STOPPING.is_set() and not is_watchdog_thread()):
        with WRITE_LOCK:
            if not (STOPPING.is_set() and not is_watchdog_thread()):
                return action()
    if abandon is not None:
        try:
            abandon()
        except Exception:
            pass
    park_if_stopping()
    return None

def replace_or_leave(temporary: Path, path: Path) -> None:
    error: OSError | None = None
    for pause in (0.0,) + tuple(REPLACE_RETRY_SEC):
        if pause:
            time.sleep(pause)
        try:
            os.replace(temporary, path)
            return
        except OSError as exc:
            error = exc
    try:
        temporary.unlink()
    except OSError:
        pass
    raise OSError(f"{path}: the new content could not be moved over the file ({error}); the file is left as it was")

def _descendants(root: int) -> set[int]:
    children: dict[int, list[int]] = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return set()
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as handle:
                data = handle.read()
            ppid = int(data.rsplit(b")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(int(name))
    found: set[int] = set()
    todo = [root]
    while todo:
        for kid in children.get(todo.pop(), []):
            if kid not in found:
                found.add(kid)
                todo.append(kid)
    return found

def _kill_pid(pid: int) -> None:
    import signal

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass

def _kill_group(pgid: int) -> None:
    import signal

    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        pass

def _taskkill(pid: int, timeout: float) -> None:
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=max(1.0, timeout))
    except Exception:
        pass

def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except (OSError, AttributeError):
        return True
    return True

def stop_group(process: subprocess.Popen, pgid: int, grace: float = 5.0, sleep=time.sleep) -> None:
    import signal

    try:
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        pass
    waited = 0.0
    while waited < grace and process.poll() is None:
        sleep(0.25)
        waited += 0.25
    extra = 0.0
    while extra < 1.0 and _group_alive(pgid):
        sleep(0.25)
        extra += 0.25
    if process.poll() is None or _group_alive(pgid):
        _kill_group(pgid)

def _kill_tree(process: subprocess.Popen, own_group: bool, timeout: float = 20.0) -> None:
    if process.poll() is not None:
        if own_group and not IS_WINDOWS:
            _kill_group(process.pid)
        return
    if IS_WINDOWS:
        _taskkill(process.pid, timeout)
        return
    victims = _descendants(process.pid)
    _kill_pid(process.pid)
    for pid in victims:
        _kill_pid(pid)
    if own_group:
        _kill_group(process.pid)

def stop_children(log=print, bound: float = 30.0) -> int:
    with _CHILDREN_LOCK:
        entries = list(_CHILDREN.values())
    started = time.monotonic()
    stopped = 0
    if IS_WINDOWS:
        for process, _ in entries:
            if process.poll() is not None:
                continue
            left = bound - (time.monotonic() - started)
            if left <= 0:
                log(f"[watchdog] no time left to stop pid {process.pid}")
                continue
            _taskkill(process.pid, min(20.0, left))
            stopped += 1
        return stopped

    tree = _descendants(os.getpid()) if os.getpid() != 1 else set()
    for process, pgid in entries:
        if process.poll() is None:
            stopped += 1
            _kill_pid(process.pid)
        if pgid is not None:

            _kill_group(pgid)
    for pid in tree - {os.getpid()}:
        _kill_pid(pid)
    return stopped

class PathError(ValueError):
    pass

def safe_path(root: Path, relative: str) -> Path:
    candidate = (root / str(relative or "").strip().replace("\\", "/")).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise PathError(f"path escapes the project: {relative}")
    for part in candidate.relative_to(root_resolved).parts:
        if part in IGNORED_DIRS:
            raise PathError(f"path inside a protected directory ({part}): {relative}")
    return candidate

def read_text(path: Path, limit: int = 16000) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > limit:
        return text[:limit] + f"\n... [truncated, {len(text) - limit} more chars]"
    return text

def write_text(path: Path, content: str) -> None:
    if not holds_write_lock():
        _park_if_stopping()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = temporary_name(path, ".arc-tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    try:
        guarded(lambda: replace_or_leave(temporary, path), abandon=lambda: temporary.unlink(missing_ok=True))
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass

def list_tree(root: Path, start: str = "", max_entries: int = 200) -> str:
    base = safe_path(root, start) if start else root
    if not base.exists():
        return f"(missing) {start}"
    if base.is_file():
        return str(base.relative_to(root)).replace("\\", "/")
    lines: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not d.startswith("."))
        rel_dir = Path(dirpath).relative_to(root)
        for name in sorted(filenames):
            if name.startswith(".") or name.endswith(".lock") or name == "package-lock.json":
                continue
            rel = (rel_dir / name).as_posix() if str(rel_dir) != "." else name
            lines.append(rel)
            if len(lines) >= max_entries:
                lines.append("... [more files omitted]")
                return "\n".join(lines)
    return "\n".join(lines) or "(empty)"

def tail(text: str, limit: int = 6000) -> str:
    if len(text) <= limit:
        return text
    return f"... [first {len(text) - limit} chars omitted]\n" + text[-limit:]

def run_cmd(
    args: list[str],
    *,
    cwd: Path,
    timeout: int = 600,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    merged_env = os.environ.copy()
    merged_env.setdefault("CI", "1")
    merged_env["NO_COLOR"] = "1"
    merged_env["FORCE_COLOR"] = "0"
    if env:
        merged_env.update(env)
    popen_kwargs: dict = {
        "cwd": str(cwd),
        "env": merged_env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if IS_WINDOWS:
        command: list[str] | str = subprocess.list2cmdline(args)
        popen_kwargs["shell"] = True
    else:
        command = args
    if STOPPING.is_set():
        return 124, "[not started: the agent is stopping before the platform's time limit]"

    try:
        process = subprocess.Popen(command, **popen_kwargs)
    except FileNotFoundError as exc:
        return 127, f"command not found: {exc}"
    register_child(process)
    try:
        with process:
            try:
                stdout, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as exc:

                _kill_tree(process, False)
                process.kill()
                if IS_WINDOWS:
                    exc.stdout, _ = process.communicate()
                else:
                    process.wait()
                output = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
                return 124, strip_terminal_codes(output or "") + f"\n[timeout after {timeout}s]"
            except BaseException:
                process.kill()
                raise
    finally:
        unregister_child(process)
    return process.returncode, strip_terminal_codes(stdout or "")

TERMINAL_CODES = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\r(?!\n)")

def strip_terminal_codes(text: str) -> str:
    return TERMINAL_CODES.sub("", text)

def npm_bin(name: str) -> str:
    return f"{name}.cmd" if IS_WINDOWS else name

class BackgroundProcess:

    def __init__(self, args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
        if STOPPING.is_set():
            raise RuntimeError("not started: the agent is stopping before the platform's time limit")
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        self._log_path = cwd / ".arc-agent-server.log"
        self._log = open(self._log_path, "w", encoding="utf-8")
        kwargs: dict = {"cwd": str(cwd), "env": merged_env, "stdout": self._log, "stderr": subprocess.STDOUT}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            self.process = subprocess.Popen(subprocess.list2cmdline(args), shell=True, **kwargs)
        else:
            kwargs["start_new_session"] = True
            self.process = subprocess.Popen(args, **kwargs)
        self.pgid: int | None = None if IS_WINDOWS else self.process.pid
        register_child(self.process, own_group=not IS_WINDOWS)

    def output_so_far(self) -> str:
        try:
            self._log.flush()
            return self._log_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            return ""

    def stop(self) -> str:
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True)
            else:
                pgid = getattr(self, "pgid", None)
                stop_group(self.process, pgid if pgid is not None else self.process.pid)
        except Exception:
            pass
        unregister_child(self.process)
        try:
            self._log.close()
            output = self._log_path.read_text(encoding="utf-8", errors="replace")
            self._log_path.unlink(missing_ok=True)
        except OSError:
            output = ""

        return strip_terminal_codes(output)

    def alive(self) -> bool:
        return self.process.poll() is None

def fresh_data_env(project_dir: Path) -> dict[str, str]:
    import uuid

    root = project_dir / "backend" / ".arc-test-db" / uuid.uuid4().hex[:12]
    root.mkdir(parents=True, exist_ok=True)
    database = str(root / "database.db")
    return {"DATA_DIR": str(root), "ARC_DB_FILE": database, "DATABASE_FILE": database}

def free_port(preferred: int = 3000) -> int:
    import socket

    shift = 0
    base = os.environ.get("AGENT_PORT_BASE", "").strip()
    if base.isdigit():
        shift = int(base) - 3000
    for port in [p + shift for p in (preferred, 3100, 3200, 3300)] + [0]:
        if port in CHROMIUM_UNSAFE_PORTS:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                found = sock.getsockname()[1]
            except OSError:
                continue
        if found not in CHROMIUM_UNSAFE_PORTS:
            return found
    return preferred

CHROMIUM_UNSAFE_PORTS = frozenset({1719, 1720, 1723, 2049, 3659, 4045, 4190, 5060, 5061, 6000, 6566, 6665, 6666, 6667,
                                   6668, 6669, 6679, 6697, 10080})

def wait_http(url: str, timeout: int = 60) -> tuple[bool, int]:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    status = 0
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                status = response.status
                if 200 <= status < 400:
                    return True, status
        except urllib.error.HTTPError as exc:
            status = exc.code
        except Exception:
            pass
        time.sleep(1)
    return False, status

API_LIVENESS_PATH = "/api/__arc_liveness_probe__"

def api_answers(base_url: str, seconds: float = 5.0, attempts: int = 3) -> bool:
    import urllib.error
    import urllib.request

    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(base_url.rstrip("/") + API_LIVENESS_PATH, timeout=seconds):
                return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            if attempt + 1 < attempts:
                time.sleep(1)
    return False

API_HANG_NOTE = (
    "THE /api CHAIN DOES NOT ANSWER: GET {path} (a path no route defines) got no HTTP answer at all, not even a 404, in "
    "{attempts} attempts of {seconds:.0f} s, while the server is up. Most likely a middleware on the /api chain never calls "
    "next() and never responds -- typically `app.use(path, factory)` where `factory` RETURNS the middleware and must be "
    "called: `app.use(path, factory())` -- or an async middleware whose promise never settles; any request that passes "
    "through it waits forever. Fix this before anything else."
)

def log(message: str) -> None:
    print(message, flush=True)
    sys.stderr.flush()
