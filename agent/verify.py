from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from . import clock
from .fsutil import API_HANG_NOTE, API_LIVENESS_PATH, BackgroundProcess, api_answers, free_port, fresh_data_env, npm_bin, run_cmd, strip_terminal_codes, tail, wait_http
from .testrun import read_error_contexts, remember_error_contexts

@dataclass
class StepResult:
    ok: bool
    output: str
    skipped: bool = False
    ran: int = -1

    failed_tests: list | None = None

    started: bool | None = None

    infra: str = ""

    not_run_tests: list | None = None

    error_contexts: dict | None = None

INSTALL_ARGS = ["install", "--include=optional", "--no-audit", "--no-fund"]

INSTALL_ARGS_ALT = ["install", "--prefer-offline", "--no-audit", "--no-fund"]
INSTALL_RETRY_INTERVAL = 600.0
INSTALL_RETRY_AFTER_127 = 120.0
INSTALL_RETRY_LIMIT = 6

INSTALL_SLOW_INTERVAL = 1800.0
_INSTALL_STATE: dict[str, dict] = {}
MISSING_PROGRAM = re.compile(r"(?:^|\n)[^\n]*?(?::\s*(?:command\s+)?not found\s*(?:\n|$)|is not recognized as an internal or external command)")
DEPS_MISSING_NOTE = ("[verify] INSTALL PROBLEM, NOT A CODE PROBLEM: the {package} dependencies are not installed ({missing}); "
                     "`npm install` failed or has not been repeated yet. Nothing in the application's code causes or fixes this; "
                     "the factory retries the install at most every {minutes} minutes.")

INSTALL_MARKER = ".arc-install-complete"
NOT_COMPLETED = "the last npm install did not complete"

INSTALL_TIMEOUT_SEC = 900.0
BUILD_TIMEOUT_SEC = 600.0
BUILD_FRONTEND_WORST_SEC = BUILD_TIMEOUT_SEC
BUILD_ROOM_SEC = 180.0
INSTALL_MIN_SEC = 60.0
BUILD_MIN_SEC = 5.0
BUILD_CUT_SLACK_SEC = 30.0
_GLOBAL_DEADLINE: list[float | None] = [None]

_RUN_DEADLINE: list[float | None] = [None]
INSTALL_CLOSING_RESERVE_SEC = BUILD_FRONTEND_WORST_SEC + 300.0 + 120.0

INFRA_WAIT_MAX_SEC = 1200.0
INFRA_RECHECKS_MAX = 3

NPM_OPTIONAL_BUG = "npm has a bug related to optional dependencies"
MODULE_MISSING = re.compile(r"Cannot find (?:module|package) ['\"]([^'\"]+)['\"](?:\s*imported from ([^\n]+)|\s*\n\s*Require stack:\s*\n\s*-\s*([^\n]+))?")

def _now() -> float:
    return time.monotonic()

def _install_state(package_dir: Path) -> dict:
    return _INSTALL_STATE.setdefault(str(package_dir.resolve()), {"last": None, "tries": 0})

def set_global_deadline(deadline: float | None) -> None:
    _GLOBAL_DEADLINE[0] = deadline
    _RUN_DEADLINE[0] = deadline

def install_room(reserve: float = INSTALL_CLOSING_RESERVE_SEC) -> float | None:
    rooms = []
    if _RUN_DEADLINE[0] is not None:
        rooms.append(_RUN_DEADLINE[0] - clock.now() - reserve)
    if _GLOBAL_DEADLINE[0] is not None:
        rooms.append(_GLOBAL_DEADLINE[0] - clock.now())
    return min(rooms) if rooms else None

def _manifest(package_dir: Path) -> dict | None:
    try:
        manifest = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None

def _declared(manifest: dict) -> list[str]:
    try:
        return [*dict(manifest.get("dependencies") or {}), *dict(manifest.get("devDependencies") or {})]
    except (TypeError, ValueError):
        return []

def install_complete(package_dir: Path) -> bool:
    return (package_dir / "node_modules" / INSTALL_MARKER).is_file()

def mark_install_complete(package_dir: Path) -> None:
    try:
        (package_dir / "node_modules").mkdir(parents=True, exist_ok=True)
        (package_dir / "node_modules" / INSTALL_MARKER).write_text("npm install exited 0\n", encoding="utf-8")
    except OSError:
        pass

def _unmark(package_dir: Path) -> bool:
    marker = package_dir / "node_modules" / INSTALL_MARKER
    try:
        marker.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return not marker.exists()
    return True

def _script_programs(manifest: dict) -> list[str]:
    scripts = manifest.get("scripts") if isinstance(manifest.get("scripts"), dict) else {}
    programs: list[str] = []
    for name in ("build", "start"):
        for segment in re.split(r"&&|\|\||[;|]", str(scripts.get(name) or "")):
            words = [w for w in segment.split() if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w)]
            if words and words[0] not in programs:
                programs.append(words[0])
    return programs

def _bins(dependency_dir: Path) -> list[str]:
    manifest = _manifest(dependency_dir) or {}
    bin_field = manifest.get("bin")
    if isinstance(bin_field, str):
        return [str(manifest.get("name") or dependency_dir.name).split("/")[-1]]
    return [str(name) for name in bin_field] if isinstance(bin_field, dict) else []

def _unlinked_programs(package_dir: Path, manifest: dict, installed: list[str]) -> list[str]:
    wanted = _script_programs(manifest)
    if not wanted:
        return []
    bin_dir = package_dir / "node_modules" / ".bin"
    missing = []
    for name in installed:
        for program in _bins(package_dir / "node_modules" / name):
            if program in wanted and not any((bin_dir / f"{program}{ext}").exists() for ext in ("", ".cmd")):
                missing.append(f"program {program} of {name} not linked")
    return missing

def missing_dependencies(project_dir: Path, package: str) -> list[str]:
    package_dir = project_dir / package
    manifest = _manifest(package_dir)
    if manifest is None:
        return []
    names = _declared(manifest)
    missing = [name for name in names if not (package_dir / "node_modules" / name / "package.json").is_file()]
    if not (package_dir / "node_modules").is_dir():
        return missing
    if not install_complete(package_dir):
        missing.append(NOT_COMPLETED)
    installed = [name for name in names if name not in missing]
    return missing + _unlinked_programs(package_dir, manifest, installed)

def program_missing(code: int, output: str) -> bool:
    return code == 127 or bool(MISSING_PROGRAM.search(output or ""))

def _package_root(specifier: str) -> str:
    parts = specifier.split("/")
    return "/".join(parts[:2]) if specifier.startswith("@") else parts[0]

SOURCE_ERROR = re.compile(r"\b(?:Syntax|Reference|Type|Range)Error\b")

def server_install_fault(project_dir: Path, code: int | None, server_text: str, install_note: str = "") -> str:
    text = server_text or ""
    if code == 127:
        return "backend: a program the start script runs was not found"
    fault = install_fault(project_dir, "backend", 1, text, programs=False)
    if fault:
        return f"backend: {fault}"
    if install_note and not SOURCE_ERROR.search(text):
        return "backend: the dependencies are not installed"
    return ""

def install_fault(project_dir: Path, package: str, code: int, output: str, programs: bool = True) -> str:
    if code == 0:
        return ""
    if programs and program_missing(code, output):
        return "a program the command runs was not found"
    if NPM_OPTIONAL_BUG in (output or ""):
        return "an optional native package is missing (npm's optional-dependency bug)"
    declared = set(_declared(_manifest(project_dir / package) or {}))
    for match in MODULE_MISSING.finditer(output or ""):
        specifier, importer = match.group(1), (match.group(2) or match.group(3) or "")
        if specifier.startswith("."):
            continue
        if specifier.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", specifier):

            if "node_modules" in specifier.replace("\\", "/").split("/"):
                from_app = bool(importer.strip()) and "node_modules" not in importer.replace("\\", "/").split("/")
                if from_app and not missing_dependencies(project_dir, package):
                    continue
                return f"module {specifier} not found (a file inside node_modules)"
            continue
        if "node_modules" in importer.replace("\\", "/").split("/"):
            return f"module {specifier} not found (imported from inside node_modules)"

        if specifier == _package_root(specifier) and specifier in declared:
            return f"module {specifier} not found (a declared dependency)"
    return ""

def npm_install(project_dir: Path, package: str, log=print, args: list[str] | None = None, force: bool = False,
                timeout: float = INSTALL_TIMEOUT_SEC) -> StepResult:
    package_dir = project_dir / package
    if not force and install_complete(package_dir):
        log(f"[verify] {package}: the last npm install completed, skipping npm install")
        return StepResult(True, "node_modules already installed", skipped=True)
    if not force and (package_dir / "node_modules").is_dir():

        log(f"[verify] {package}: node_modules present but {NOT_COMPLETED}; installing")
    if not _unmark(package_dir):
        log(f"[verify] {package}: the install marker could not be removed; this install is not trusted")
    log(f"[verify] npm install in {package} ...")
    started = time.time()
    code, output = run_cmd([npm_bin("npm"), *(args or INSTALL_ARGS)], cwd=package_dir, timeout=max(1, int(timeout)))
    log(f"[verify] npm install {package}: exit={code} in {time.time() - started:.0f}s")
    _install_state(package_dir)["last"] = _now()
    if code == 0:
        mark_install_complete(package_dir)
    return StepResult(code == 0, tail(output, 3000))

def install_with_retries(project_dir: Path, package: str, log=print, attempts: int = 3, pause: float = 30.0, sleep=time.sleep) -> StepResult:
    step = npm_install(project_dir, package, log=log)
    for attempt in range(2, attempts + 1):
        if step.ok:
            break

        log(f"[verify] npm install {package} FAILED (attempt {attempt - 1}); trying again in {pause:.0f} s:\n{step.output[-600:]}")
        sleep(pause)
        step = npm_install(project_dir, package, log=log, force=True)
    if not step.ok:
        log(f"[verify] npm install {package} FAILED three times; the checks of this run cannot be trusted until a later "
            f"install succeeds (tried again at most every {INSTALL_RETRY_INTERVAL / 60:.0f} min, {INSTALL_RETRY_LIMIT} times, then at most "
            f"every {INSTALL_SLOW_INTERVAL / 60:.0f} min until the run ends):\n{step.output[-1500:]}")
    return step

def retry_install(project_dir: Path, package: str, log=print, reason: str = "", min_interval: float = INSTALL_RETRY_INTERVAL,
                  timeout: float = INSTALL_TIMEOUT_SEC) -> StepResult | None:
    package_dir = project_dir / package
    state = _install_state(package_dir)
    slow = state["tries"] >= INSTALL_RETRY_LIMIT
    if slow:
        if not state.get("told"):
            state["told"] = True
            log(f"[verify] npm install {package}: the {INSTALL_RETRY_LIMIT} later tries are used up; from now on one slow "
                f"try at most every {INSTALL_SLOW_INTERVAL / 60:.0f} min until the run ends (nobody waits for it)")
        min_interval = max(min_interval, INSTALL_SLOW_INTERVAL)
    if state["last"] is not None and _now() - state["last"] < min_interval:
        return None
    if timeout < INSTALL_MIN_SEC:
        log(f"[verify] npm install {package}: due, but only {max(0, int(timeout))} s are left for it here; not started")
        return None
    state["tries"] += 1
    args = INSTALL_ARGS if state["tries"] % 2 else INSTALL_ARGS_ALT
    which = f"slow try {state['tries'] - INSTALL_RETRY_LIMIT}" if slow else f"later try {state['tries']}/{INSTALL_RETRY_LIMIT}"
    log(f"[verify] npm install {package} again ({which}; {reason}): npm {' '.join(args)}")
    step = npm_install(project_dir, package, log=log, args=args, force=True, timeout=min(INSTALL_TIMEOUT_SEC, timeout))
    if step.ok:
        log(f"[verify] npm install {package}: later try {state['tries']} succeeded")
    else:
        log(f"[verify] npm install {package}: later try {state['tries']} FAILED:\n{step.output[-600:]}")
    return step

def install_retry_wait(project_dir: Path, package: str) -> float | None:
    state = _install_state(project_dir / package)
    if state["tries"] >= INSTALL_RETRY_LIMIT:
        return None
    if state["last"] is None:
        return 0.0
    return max(0.0, state["last"] + INSTALL_RETRY_INTERVAL - _now())

def _names(missing: list[str]) -> str:
    return ", ".join(missing[:6]) + (f" and {len(missing) - 6} more" if len(missing) > 6 else "")

def ensure_installed(project_dir: Path, package: str, log=print, min_interval: float = INSTALL_RETRY_INTERVAL,
                     timeout: float = INSTALL_TIMEOUT_SEC, reserve: float = INSTALL_CLOSING_RESERVE_SEC) -> str:
    missing = missing_dependencies(project_dir, package)
    if not missing:
        return ""
    room = install_room(reserve)
    if room is not None:
        timeout = min(timeout, room)
    retry_install(project_dir, package, log=log, reason=f"not installed: {_names(missing)}", min_interval=min_interval, timeout=timeout)
    missing = missing_dependencies(project_dir, package)
    if not missing:
        return ""
    return DEPS_MISSING_NOTE.format(package=package, missing=_names(missing), minutes=f"{INSTALL_RETRY_INTERVAL / 60:.0f}")

def _build_end(deadline: float | None) -> float:
    end = _now() + BUILD_FRONTEND_WORST_SEC
    for limit in (deadline, _GLOBAL_DEADLINE[0]):
        if limit is not None:
            end = min(end, _now() + (limit - clock.now()))
    return end

def build_frontend(project_dir: Path, log=print, deadline: float | None = None) -> StepResult:
    end = _build_end(deadline)
    frontend = project_dir / "frontend"
    note = ensure_installed(project_dir, "frontend", log=log, timeout=end - _now() - BUILD_ROOM_SEC, reserve=0.0)
    cut = [False]

    def build() -> tuple[int, str]:
        left = end - _now()
        cut[0] = left < BUILD_TIMEOUT_SEC - BUILD_CUT_SLACK_SEC
        if left < BUILD_MIN_SEC:
            log(f"[verify] frontend build not started: {max(0, int(left))} s of its time are left")
            return 124, f"[verify] the frontend build was not started: its time ({int(BUILD_FRONTEND_WORST_SEC)} s, or the deadline) was used up"
        log("[verify] building frontend ...")
        code, output = run_cmd([npm_bin("npm"), "run", "build"], cwd=frontend, timeout=max(1, int(min(BUILD_TIMEOUT_SEC, left))))
        log(f"[verify] frontend build exit={code}")
        return code, output

    code, output = build()
    fault = install_fault(project_dir, "frontend", code, output)
    if fault:

        step = retry_install(project_dir, "frontend", log=log, reason=f"the build exited {code}: {fault}",
                             min_interval=INSTALL_RETRY_AFTER_127, timeout=end - _now() - BUILD_ROOM_SEC)
        if step is not None and step.ok:
            code, output = build()
            log(f"[verify] frontend build after the install exit={code}")
        fault = install_fault(project_dir, "frontend", code, output)
        if fault:
            note = note or DEPS_MISSING_NOTE.format(package="frontend", missing=fault, minutes=f"{INSTALL_RETRY_INTERVAL / 60:.0f}")
    infra = ""
    if code != 0 and fault:
        infra = f"frontend: {fault}"
    elif code == 124 and cut[0]:

        infra = "frontend: the build's time went to the dependency install or the deadline"
    output = tail(output, 4000)
    return StepResult(code == 0, output + ("\n" + note if code != 0 and note else ""), infra=infra)

def settle_install_failure(check, outcome, project_dir: Path, *, deadline: float | None = None, log=print, sleep=time.sleep,
                           max_wait: float = INFRA_WAIT_MAX_SEC):
    end = _now() + max_wait
    if deadline is not None:
        end = min(end, _now() + (deadline - clock.now()))
    check_sec = 0.0
    for _ in range(INFRA_RECHECKS_MAX):
        infra = str(getattr(outcome, "infra", "") or "")
        if outcome.passed or not infra:
            return outcome
        package = infra.split(":", 1)[0].strip()
        package = package if package in ("frontend", "backend") else "frontend"
        wait = install_retry_wait(project_dir, package)
        if wait is None:
            log(f"[verify] infrastructure failure ({infra}); the later installs of {package} are used up: not waiting")
            return outcome
        room = end - _now() - check_sec
        if wait > room:
            log(f"[verify] infrastructure failure ({infra}); the next install is due in {int(wait)} s, only {max(0, int(room))} s to wait: not waiting")
            return outcome
        log(f"[verify] infrastructure failure ({infra}), not the code: waiting {int(wait)} s for the next npm install of "
            f"{package}, then checking again (no round counted, nothing sent to the model)")
        sleep(wait)
        if _now() > end:
            log(f"[verify] infrastructure failure ({infra}); the time to wait for the install is used up: not checking again")
            return outcome
        saved = _GLOBAL_DEADLINE[0]
        limit = clock.now() + (end - _now())
        _GLOBAL_DEADLINE[0] = limit if saved is None else min(saved, limit)
        began = _now()
        try:
            outcome = check()
        finally:
            _GLOBAL_DEADLINE[0] = saved
        check_sec = max(0.0, _now() - began)
    return outcome

def server_alive(server: BackgroundProcess) -> bool:
    return server.process.poll() is None

def probe_server(project_dir: Path, log=print, port: int | None = None) -> tuple[StepResult, int]:
    install_note = ensure_installed(project_dir, "backend", log=log)
    port = port or free_port(3000)
    log(f"[verify] starting backend on port {port} ...")

    data = fresh_data_env(project_dir)
    server = None
    foreign = False
    try:
        server = BackgroundProcess([npm_bin("npm"), "run", "start"], cwd=project_dir / "backend",
                                   env={"PORT": str(port), "ARC_WEB_PORT": str(port), **data})
        ok_root, status_root = wait_http(f"http://127.0.0.1:{port}/", timeout=60)
        if ok_root and not server_alive(server):
            ok_root, status_root = False, f"port {port} answered by another process; this project's server exited"
            foreign = True
        ok_health, status_health = wait_http(f"http://127.0.0.1:{port}/api/health", timeout=10)

        api_alive = api_answers(f"http://127.0.0.1:{port}") if ok_root else True

        running = server_alive(server)
    finally:
        server_log = server.stop() if server is not None else ""
        remove_dirs([data["DATA_DIR"]])
    detail = f"GET / -> {status_root}; GET /api/health -> {status_health}\n"
    if not api_alive:
        detail += API_HANG_NOTE.format(path=API_LIVENESS_PATH, attempts=3, seconds=5) + "\n"
    detail += tail(server_log, 2000)
    log(f"[verify] server probe: root={status_root} health={status_health} api={'answers' if api_alive else 'HANGS'}")

    clash = not ok_root and re.search(rf"EADDRINUSE[^\n]*\b{port}\b", server_log or "")

    started = True if ok_root else (None if foreign or clash or running or install_note else False)
    if install_note and not ok_root:
        detail += "\n" + install_note

    infra = "" if ok_root or foreign or clash else server_install_fault(project_dir, _exit_code(server) if server is not None else None,
                                                                        server_log, install_note)
    return StepResult(ok_root and ok_health and api_alive, detail, started=started, infra=infra), port

def server_errors_note(server_log: str, limit: int = 9000) -> str:

    head = re.compile(r"(?:\b\w*(?:Error|Exception)\b\s*[:(]|SQLITE_\w+|\bE[A-Z]{4,}\b:|Unhandled|Cannot find module|is not a function|is not defined|Cannot read propert)")
    frame = re.compile(r"^\s+(?:at\s|\^|\{|\}|\w+:\s|code:|errno:|\[cause\])")
    blocks: list[list[str]] = []
    current: list[str] | None = None

    for raw in strip_terminal_codes(server_log or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            current = None
            continue
        if current is not None and frame.search(line):
            current.append(line[:400])
        elif head.search(line):
            current = [line[:400]]
            blocks.append(current)
        else:
            current = None
    if not blocks:
        return ""

    def key(block: list[str]) -> str:
        text = "\n".join(block[:6])
        text = re.sub(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?Z?|127\.0\.0\.1:\d+|localhost:\d+|0x[0-9a-f]+|\b[0-9a-f]{16,}\b", "~", text)
        return text

    counts: dict[str, int] = {}
    unique: list[list[str]] = []
    for block in blocks:
        k = key(block)
        counts[k] = counts.get(k, 0) + 1
        if counts[k] == 1:
            unique.append(block)

    parts: list[str] = []
    used = 0
    for block in reversed(unique):
        lines = block[:14] + ([f"    (this error occurred {counts[key(block)]} times)"] if counts[key(block)] > 1 else [])
        text = "\n".join(SERVER_LINE_PREFIX + line for line in _defuse("\n".join(lines)).split("\n"))
        if used + len(text) > limit and parts:
            break
        parts.append(text)
        used += len(text)
    body = "\n\n".join(reversed(parts))
    return "\n\nAPPLICATION SERVER ERRORS during this run (from the backend's own stderr; read them before guessing at the cause):\n" + body

SERVER_DOWN_HEAD = "SERVER OUTPUT (the application did not start):"
SERVER_EXITED_HEAD = "SERVER OUTPUT (the application exited while the tests ran):"
SERVER_GONE_HEAD = "SERVER OUTPUT (the application exited before the tests ran):"

APP_DOWN_MARKS = ("backend did not become ready", SERVER_EXITED_HEAD, SERVER_GONE_HEAD)
SERVER_TAIL_LINES = 60
SERVER_TAIL_CHARS = 4000
SERVER_LINE_PREFIX = "  | "

def _server_text(server) -> str:
    try:
        return str(server.output_so_far() or "")
    except Exception:
        return ""

def _exit_code(server):
    try:
        return server.process.poll()
    except Exception:
        return None

def _defuse(text: str) -> str:
    text = text.replace("›", ">")
    for marker in (FAILED_FILES_HEAD, NOT_RUN_HEAD, *NO_SPEC_RAN, "[middle of the output omitted]", ISOLATION_TRIM,
                   SERVER_DOWN_HEAD, SERVER_EXITED_HEAD, SERVER_GONE_HEAD):
        text = text.replace(marker, marker.replace(" ", "_", 1))
    return re.sub(r"(\.spec\.js):(?=\d)", r"\1 line ", text)

def server_output_note(server_log: str, head: str, state: str = "", lines: int = SERVER_TAIL_LINES,
                       chars: int = SERVER_TAIL_CHARS) -> str:
    text = strip_terminal_codes(server_log or "").replace("\r\n", "\n").replace("›", ">")
    kept = [line.rstrip() for line in text.splitlines()]
    while kept and not kept[-1].strip():
        kept.pop()
    omitted = max(0, len(kept) - lines)
    kept = kept[-lines:]
    body = "\n".join(kept)
    if len(body) > chars:
        body = body[-chars:]
        body = body.split("\n", 1)[1] if "\n" in body else body
        omitted = -1
    body = _defuse(body)
    shown ="\n".join(SERVER_LINE_PREFIX + line for line in body.splitlines()) if body.strip() else SERVER_LINE_PREFIX + "(the server printed nothing)"
    note = "\n\n" + head + "\n"
    if state:
        note += state + "\n"
    if omitted:
        note += SERVER_LINE_PREFIX + ("(earlier lines omitted)" if omitted < 0 else f"({omitted} earlier line(s) omitted)") + "\n"
    return note + shown

FAILED_FILES_HEAD = "FAILED SPEC FILES: "

def failing_spec_files(report: Path) -> list[str]:
    import json
    try:
        data = json.loads(report.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return []
    found: list[str] = []

    def walk(suite: dict) -> None:
        for spec in suite.get("specs") or []:
            for test in spec.get("tests") or []:
                results = test.get("results") or []
                if test.get("status") == "unexpected" or (results and results[-1].get("status") in ("failed", "timedOut", "interrupted")):
                    name = Path(str(spec.get("file") or suite.get("file") or "")).name
                    if name and name not in found:
                        found.append(name)
        for child in suite.get("suites") or []:
            walk(child)

    for suite in data.get("suites") or []:
        walk(suite)
    return found

def failing_tests(report: Path) -> list[tuple[str, int, int, str]] | None:
    import json
    try:
        data = json.loads(report.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    found: list[tuple[str, int, int, str]] = []

    def walk(suite: dict, path: list[str]) -> None:
        for spec in suite.get("specs") or []:
            for test in spec.get("tests") or []:
                results = test.get("results") or []
                if test.get("status") == "unexpected" or (results and results[-1].get("status") in ("failed", "timedOut", "interrupted")):
                    item = (Path(str(spec.get("file") or suite.get("file") or "")).name, int(spec.get("line") or 0),
                            int(spec.get("column") or 0), " › ".join(path + [str(spec.get("title") or "")]))
                    if item not in found:
                        found.append(item)
        for child in suite.get("suites") or []:
            walk(child, path + [str(child.get("title") or "")])

    for suite in data.get("suites") or []:
        walk(suite, [])
    return found

PAGE_ERRORS_HEAD = "PAGE ERRORS of the failed tests (the application's own uncaught exceptions and console errors, from the browser):"
PAGE_ERROR_MARK = "[page] "
PAGE_ERRORS_PER_TEST = 4
PAGE_ERRORS_TESTS = 6
_PAGE_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+|/*\b(?:127\.0\.0\.1|localhost|0\.0\.0\.0)(?::\d+)?\S*", re.IGNORECASE)
_CONTEXT_TRACE = re.compile(r"^\d+-trace\.trace$")

def _page_line(text: str) -> str:
    return " ".join(_PAGE_URL.sub("<url>", str(text)).split())[:200]

def page_errors_from_trace(zip_path: str, limit: int = PAGE_ERRORS_PER_TEST) -> list[str]:
    import json
    import zipfile

    try:
        found: list[tuple[float, str]] = []
        with zipfile.ZipFile(zip_path) as archive:
            for name in archive.namelist():
                if not _CONTEXT_TRACE.match(name.rsplit("/", 1)[-1]):
                    continue
                for raw in archive.read(name).decode("utf-8", "replace").splitlines():
                    if '"pageError"' not in raw and '"console"' not in raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "event" and event.get("method") == "pageError":
                        error = (event.get("params") or {}).get("error") or {}
                        inner = error.get("error") if isinstance(error.get("error"), dict) else None
                        if inner is not None:
                            label = str(inner.get("name") or "").strip()
                            message = str(inner.get("message") or "").strip() or str(inner.get("stack") or "").strip().split("\n")[0]
                            text = f"{label}: {message}" if label and not message.startswith(label) else (message or label)
                        else:
                            value = error.get("value")
                            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                        line = _page_line("uncaught " + (text or "(no message)"))
                    elif event.get("type") == "console" and event.get("messageType") == "error":
                        line = _page_line("console.error " + str(event.get("text") or ""))
                    else:
                        continue
                    found.append((float(event.get("time") or 0.0), line))
        out: list[str] = []
        for _, line in sorted(found, key=lambda item: item[0]):
            if line not in out:
                out.append(line)
        return out[:max(0, limit)]
    except Exception:
        return []

def page_errors_note(report: Path | None) -> str:
    import json

    if report is None:
        return ""
    try:
        data = json.loads(Path(report).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return ""
    lines: list[str] = []
    tests = 0
    try:
        def walk(suite: dict, path: list[str]) -> None:
            nonlocal tests
            for spec in suite.get("specs") or []:
                name = Path(str(spec.get("file") or suite.get("file") or "")).name
                title = " › ".join(path + [str(spec.get("title") or "")])
                for test in spec.get("tests") or []:
                    for result in test.get("results") or []:
                        if result.get("status") not in ("failed", "timedOut", "interrupted") or tests >= PAGE_ERRORS_TESTS:
                            continue
                        errors: list[str] = []
                        for attachment in result.get("attachments") or []:
                            if attachment.get("name") == "trace" and attachment.get("path"):
                                errors += [e for e in page_errors_from_trace(str(attachment["path"])) if e not in errors]
                        if errors:
                            tests += 1
                            lines.extend(f"{PAGE_ERROR_MARK}{name} › {title}: {error}" for error in errors[:PAGE_ERRORS_PER_TEST])
            for child in suite.get("suites") or []:
                walk(child, path + [str(child.get("title") or "")])

        for suite in data.get("suites") or []:
            walk(suite, [])
    except Exception:
        pass
    return (PAGE_ERRORS_HEAD + "\n" + "\n".join(lines)) if lines else ""

def remove_dirs(paths: list[str], attempts: int = 8) -> None:
    for path in paths:
        for attempt in range(attempts):
            shutil.rmtree(path, ignore_errors=True)
            if not Path(path).exists():
                break
            time.sleep(0.25)

def tests_ran(report: Path) -> int:
    import json
    try:
        stats = json.loads(report.read_text(encoding="utf-8", errors="replace")).get("stats") or {}
    except (OSError, ValueError, AttributeError):
        return -1
    return sum(int(stats.get(key) or 0) for key in ("expected", "unexpected", "flaky"))

PER_TEST_ISOLATION = True
PER_TEST_UNTIL: float | None = None
PER_FILE_ISOLATION_SEC = 300.0
ISOLATED_RUN_EST_SEC = 15.0
REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^")
REGEX_AFTER_WORDS = {"return", "typeof", "case", "do", "else", "in", "of", "void", "delete", "new", "throw", "yield", "await"}

def _js_blank(text: str) -> tuple[str, str]:
    n = len(text)
    comments = list(text)
    code = list(text)

    def blank(chars: list[str], start: int, end: int) -> None:
        for k in range(start, min(end, n)):
            if chars[k] != "\n":
                chars[k] = " "

    def template(start: int) -> int:
        j = start
        while j < n and text[j] != "`" and not text.startswith("${", j):
            j += 2 if text[j] == "\\" else 1
        blank(code, start, j)
        return j

    i = 0
    depth = 0
    resume: list[int] = []
    prev, word = "", ""
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(comments, i, j)
            blank(code, i, j)
            i = j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(comments, i, j)
            blank(code, i, j)
            i = j
            continue
        if c in "'\"":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            blank(code, i + 1, j)
            i, prev, word = j + 1, c, ""
            continue
        if c == "`" or (c == "}" and resume and depth - 1 == resume[-1]):
            if c == "}":
                depth -= 1
                resume.pop()
            j = template(i + 1)
            if j < n and text[j] == "$":
                resume.append(depth)
                depth += 1
                i, prev, word = j + 2, "{", ""
            else:
                i, prev, word = j + 1, "`", ""
            continue
        if c == "/" and (prev in REGEX_BEFORE or prev == "" or word in REGEX_AFTER_WORDS):
            j, in_class = i + 1, False
            while j < n and text[j] != "\n":
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "[":
                    in_class = True
                elif text[j] == "]":
                    in_class = False
                elif text[j] == "/" and not in_class:
                    break
                j += 1
            if j < n and text[j] == "/":
                blank(code, i + 1, j)
                i, prev, word = j + 1, ")", ""
                continue
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            i, prev, word = j, "a", text[i:j]
            continue
        if not c.isspace():
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            prev, word = c, ""
        i += 1
    return "".join(comments), "".join(code)

FOCUSED = re.compile(r"\.\s*only\b")
CONFIGURE_CALL = re.compile(r"\bdescribe\s*\.\s*configure\s*\(")

def focused_test(text: str) -> bool:
    comments, code = _js_blank(text)

    return bool(FOCUSED.search(code) or any(code[m.start()] == "[" for m in re.finditer(r"\[\s*[\"'`]only[\"'`]\s*\]", comments)))

def runs_serial(text: str) -> bool:
    comments, code = _js_blank(text)
    if re.search(r"\bdescribe\s*\.\s*serial\b", code) or any(
            code[m.start()] == "[" for m in re.finditer(r"\[\s*[\"'`]serial[\"'`]\s*\]", comments)):
        return True
    for match in re.finditer(r"\bmode\s*:\s*[\"'`]serial[\"'`]", comments):
        if code[match.start()] == "m":
            return True
    for match in CONFIGURE_CALL.finditer(code):
        start, j, depth = match.end(), match.end(), 1
        while j < len(code) and depth:
            depth += {"(": 1, ")": -1}.get(code[j], 0)
            j += 1
        if depth:
            return True
        argument, argument_text = code[start:j - 1].strip(), comments[start:j - 1].strip()
        if not (argument.startswith("{") and argument.endswith("}")) or "..." in argument:
            return True
        mentions = re.findall(r"\bmode\b", argument_text)
        if not mentions:
            continue
        mode = re.search(r"(?:^|[{,\s])mode\s*:\s*(['\"])([\w-]*)\1\s*(?=[,}])", argument_text)
        if len(mentions) != 1 or not mode or mode.group(2) == "serial":
            return True
    return False

ISOLATION_TRIM = "[part of this test's output omitted; every failed test is listed at the end]"
_check_deadline: float | None = None
_groups_cache: dict[str, list] = {}

def isolation_wanted() -> bool:
    if not PER_TEST_ISOLATION:
        return False
    if PER_TEST_UNTIL is not None:
        from . import clock
        return clock.now() < PER_TEST_UNTIL
    return True

def test_groups(project_dir: Path, spec: str, log=print) -> list[tuple[int, list[tuple[int, int, str]]]] | None:
    import hashlib
    import json
    import uuid

    path = project_dir / spec
    try:
        text = path.read_bytes()
    except OSError:
        return None
    source = text.decode("utf-8", errors="replace")
    if runs_serial(source):
        log(f"[verify] {path.name} runs its tests in serial mode; the file runs whole")
        return None
    if focused_test(source):
        log(f"[verify] {path.name} focuses a test (.only); the file runs whole, as Playwright runs it")
        return None
    key = hashlib.sha1(spec.encode("utf-8") + b"\0" + text).hexdigest()
    if key in _groups_cache:
        return _groups_cache[key]
    backend = project_dir / "backend"
    listing = backend / f".arc-e2e-list-{uuid.uuid4().hex[:12]}.json"
    try:
        code, _ = run_cmd([npm_bin("npx"), "playwright", "test", spec.split("/", 1)[1], "--list", "--reporter=json"],
                          cwd=backend, timeout=120,
                          env={"PLAYWRIGHT_JSON_OUTPUT_NAME": str(listing), "PLAYWRIGHT_JSON_OUTPUT_FILE": str(listing)})
        data = json.loads(listing.read_text(encoding="utf-8", errors="replace")) if code == 0 else None
    except (OSError, ValueError):
        data = None
    finally:
        listing.unlink(missing_ok=True)
    if not isinstance(data, dict) or data.get("errors"):
        return None
    tests: list[tuple[int, int, str]] = []

    def walk(suite: dict, titles: list[str]) -> None:
        for item in suite.get("specs") or []:
            if Path(str(item.get("file") or "")).name != path.name:
                continue
            runs = item.get("tests") or []
            if runs and all(t.get("expectedStatus") == "skipped" for t in runs):
                continue
            entry = (int(item.get("line") or 0), int(item.get("column") or 0), " › ".join(titles + [str(item.get("title") or "")]))
            if entry not in tests:
                tests.append(entry)
        for child in suite.get("suites") or []:
            walk(child, titles + [str(child.get("title") or "")])

    for suite in data.get("suites") or []:
        walk(suite, [])
    groups: dict[int, list[tuple[int, int, str]]] = {}
    for entry in sorted(tests):
        groups.setdefault(entry[0], []).append(entry)
    result = sorted(groups.items())
    _groups_cache[key] = result
    return result

def _isolation_fits(started: float, runs: int, left: int) -> bool:
    now = time.monotonic()
    each = (now - started) / runs if runs else ISOLATED_RUN_EST_SEC
    if now - started + each * left > PER_FILE_ISOLATION_SEC:
        return False
    if _check_deadline is not None and now + each * left > _check_deadline:
        return False
    return isolation_wanted()

def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = max(500, limit * 2 // 5)
    return text[:head] + "\n... " + ISOLATION_TRIM + " ...\n" + text[-max(500, limit - head):]

UNTIL_NOTE = "the phase's end has passed"

def _until_passed(until: float | None) -> bool:
    return until is not None and clock.now() >= until

def _until_kw(until: float | None) -> dict:
    return {} if until is None else {"until": until}

def _stopped_by_until(result: StepResult, until: float | None) -> bool:
    return until is not None and not result.ok and not result.skipped and result.output.startswith(NOT_RUN_HEAD)

def _not_run_result(names: list[str]) -> StepResult:
    return StepResult(False, f"{NOT_RUN_HEAD}{', '.join(names)}\n0 of {len(names)} spec files passed\n"
                             f"{len(names)} spec file(s) not run: {UNTIL_NOTE}\n")

def run_tests_isolated(project_dir: Path, spec: str, groups: list, log=print, until: float | None = None) -> StepResult:
    name = Path(spec).name
    rel = spec.split("/", 1)[1]
    folder = Path(rel).parent.as_posix()
    started = time.monotonic()
    done: list[tuple[list, StepResult]] = []
    notes: list[str] = []
    position = 0
    cut = False
    log(f"[verify] {name}: {sum(len(t) for _, t in groups)} tests, each alone on a fresh server with fresh data")
    if _until_passed(until):
        position, cut = len(groups), True
    elif not _isolation_fits(started, 0, len(groups)):
        log(f"[verify] {name}: too little time to run its tests one by one; the file runs whole")
        return _run_e2e_once(project_dir, [spec], log=log, only=[rel], **_until_kw(until))
    while position < len(groups):
        if _until_passed(until):
            cut = True
            break
        rest = groups[position:]
        if not _time_fits_or_log(started, position, len(rest), name, notes, log):
            tests = [t for _, group in rest for t in group]

            now = time.monotonic()
            left = PER_FILE_ISOLATION_SEC - (now - started)
            if _check_deadline is not None:
                left = min(left, _check_deadline - now)
            result = _run_e2e_once(project_dir, [spec], log=log, only=[f"{rel}:{line}" for line, _ in rest], errors_limit=3000,
                                   timeout=_rest_timeout(left, len(tests)), **_until_kw(until))
            position = len(groups)
        else:
            line, tests = groups[position]
            result = _run_e2e_once(project_dir, [spec], log=log, only=[f"{rel}:{line}"], errors_limit=3000, **_until_kw(until))
            position += 1
        if _stopped_by_until(result, until):
            cut = True
            break
        if result.skipped:
            if all(r.ok for _, r in done):
                return result
            left = sum(len(g) for _, g in groups) - sum(len(t) for t, _ in done)
            notes.append(f"{left} test(s) of {name} not run: {result.output.strip()[:200]}")
            break
        if not result.ok and not named_failures(result.output) and any(m in result.output for m in NO_SPEC_RAN):
            return result
        done.append((tests, result))
    failed = [(tests, r) for tests, r in done if not r.ok]
    counts = [r.ran for _, r in done if r.ran >= 0]
    ran = sum(counts) if counts else -1
    total = sum(len(t) for t, _ in done)

    started_tests = {tuple(entry) for t, _ in done for entry in t}
    not_run = [(name, l, c, t) for _, group in groups for l, c, t in group if (l, c, t) not in started_tests] or None
    if cut:
        left = sum(len(g) for _, g in groups) - total
        note = f"{left} test(s) of {name} not run: {UNTIL_NOTE}"
        notes.append(note)
        log(f"[verify] {note}")
    head = f"{name}: every test ran alone, on a fresh server with fresh data of its own" + "".join(f"\n{n}" for n in notes)
    if cut and not failed:

        passed = f"\n  {ran if ran >= 0 else total} passed" if total else ""
        return StepResult(False, f"{NOT_RUN_HEAD}{name}\n{head}{passed}", ran=ran, failed_tests=[], not_run_tests=not_run)
    if not failed:
        return StepResult(True, f"{head}\n  {ran if ran >= 0 else total} passed", ran=ran, failed_tests=[])
    entries: list[tuple[str, int, int, str]] = []
    for tests, r in failed:
        named = [e for e in (r.failed_tests or []) if e[0] == name]

        for entry in named or [(name, l, c, t) for l, c, t in tests]:
            if entry not in entries:
                entries.append(entry)
    share = max(1500, 12000 // max(1, len(failed)))
    sections = []
    for tests, r in failed:
        text = r.output[len(FAILED_FILES_HEAD):].split("\n", 1)[1] if r.output.startswith(FAILED_FILES_HEAD) else r.output

        text = text.replace("[middle of the output omitted]", ISOLATION_TRIM)
        where = ", ".join(f"line {l}" for l, _, _ in tests[:4]) + (" ..." if len(tests) > 4 else "")
        sections.append(f"## {name} {where}: alone on a fresh server with fresh data\n{_trim(text.strip(), share)}")
    summary = f"  {len(entries)} failed\n" + "".join(f"    {folder}/{f}:{l}:{c} › {t} ───\n" for f, l, c, t in entries)
    passed = (ran if ran >= 0 else total) - len(entries)
    if passed > 0:
        summary += f"  {passed} passed\n"
    output = FAILED_FILES_HEAD + name + "\n" + head + "\n" + "\n\n".join(sections) + "\n\n" + summary
    return StepResult(False, output, ran=ran, failed_tests=entries, infra=_common_infra([r for _, r in failed]),
                      not_run_tests=not_run, error_contexts=_merged_contexts([r for _, r in failed]))

def _merged_contexts(parts: list) -> dict | None:
    read = [part.error_contexts for part in parts if isinstance(getattr(part, "error_contexts", None), dict)]
    merged: dict = {}
    for contexts in read:
        merged.update(contexts)
    return merged if read else None

def _common_infra(parts: list) -> str:
    red = [p for p in parts if not p.ok]
    return red[0].infra if red and all(getattr(p, "infra", "") for p in red) else ""

REST_RUN_FLOOR_SEC = 60.0
REST_RUN_PER_TEST_SEC = 15.0
E2E_RUN_TIMEOUT_SEC = 600

def _rest_timeout(left: float, tests: int) -> int:
    floor = REST_RUN_FLOOR_SEC + REST_RUN_PER_TEST_SEC * max(1, tests)
    return int(min(E2E_RUN_TIMEOUT_SEC, max(floor, left)))

def _time_fits_or_log(started: float, runs: int, left: int, name: str, notes: list[str], log) -> bool:
    if _isolation_fits(started, runs, left):
        return True
    note = f"the last {left} test(s) of {name} ran together on one fresh server and data set: the time to run each alone was used up"
    notes.append(note)
    log(f"[verify] {note}")
    return False

def _run_e2e_once(project_dir: Path, spec_files: list[str], log=print, only: list[str] | None = None,
                  errors_limit: int = 9000, timeout: int = E2E_RUN_TIMEOUT_SEC, until: float | None = None) -> StepResult:
    specs = [s.split("/", 1)[1] for s in spec_files if s.startswith("backend/test-e2e/")]
    if not specs:
        return StepResult(True, "(no e2e specs)", skipped=True)
    backend = project_dir / "backend"
    install_note = ensure_installed(project_dir, "backend", log=log)
    if not (backend / "node_modules" / "@playwright" / "test").is_dir():

        declared = "@playwright/test" in _declared(_manifest(backend) or {})
        infra = "backend: @playwright/test (declared) is not installed" if declared else ""
        return StepResult(True, "@playwright/test not installed" + ("\n" + install_note if install_note else ""), skipped=True, infra=infra)
    if only is None and len(specs) == 1 and isolation_wanted():
        groups = test_groups(project_dir, "backend/" + specs[0], log=log)
        if groups is not None and len(groups) >= 2:
            if until is None:
                return run_tests_isolated(project_dir, "backend/" + specs[0], groups, log=log)
            return run_tests_isolated(project_dir, "backend/" + specs[0], groups, log=log, until=until)
    if _until_passed(until):

        log(f"[verify] {len(specs)} spec file(s) not run: {UNTIL_NOTE}")
        return _not_run_result([Path(s).name for s in specs])
    port = free_port(3100)

    data_dirs: list[str] = []
    page_errors = ""

    def data_env() -> dict[str, str]:
        env = fresh_data_env(project_dir)
        data_dirs.append(env["DATA_DIR"])
        return env

    server = BackgroundProcess([npm_bin("npm"), "run", "start"], cwd=backend, env={"PORT": str(port), **data_env()})
    ready, _ = wait_http(f"http://127.0.0.1:{port}/", timeout=60)
    if ready and not server_alive(server):

        log(f"[verify] port {port} is answered by another process, not by this project's server; retrying on a free port")
        server.stop()
        port = free_port(0)
        server = BackgroundProcess([npm_bin("npm"), "run", "start"], cwd=backend, env={"PORT": str(port), **data_env()})
        ready, _ = wait_http(f"http://127.0.0.1:{port}/", timeout=60)
        if ready and not server_alive(server):
            server.stop()
            remove_dirs(data_dirs)
            log("[verify] no port of its own for the e2e server; e2e skipped (another process answers)")
            return StepResult(True, "e2e skipped: this project's server could not get a port of its own", skipped=True)
    report: Path | None = None
    ran = -1
    contexts = None
    try:
        if not ready:
            if not server_alive(server) and re.search(rf"EADDRINUSE[^\n]*\b{port}\b", server.output_so_far()):

                log(f"[verify] port {port} is held by another process that does not answer; e2e skipped")
                return StepResult(True, "e2e skipped: the port was taken by another process", skipped=True)

            code = _exit_code(server)
            state = (f"The server process exited (exit code {code}) before GET / answered." if code is not None else
                     "The server process was still running, but GET / gave no 2xx/3xx answer within 60 s.")
            log(f"[verify] backend did not become ready for e2e ({'exited, code ' + str(code) if code is not None else 'no answer'})")
            text = _server_text(server)

            infra = server_install_fault(project_dir, code, text, install_note)
            if infra:
                log(f"[verify] the backend did not start for an infrastructure reason ({infra})")
            return StepResult(False, "backend did not become ready for e2e"
                              + server_output_note(text, SERVER_DOWN_HEAD, state, chars=min(SERVER_TAIL_CHARS, errors_limit))
                              + ("\n" + install_note if infra and install_note else ""), infra=infra)
        if not api_answers(f"http://127.0.0.1:{port}"):

            log("[verify] the /api chain does not answer; playwright not started")
            gone = ""
            if not server_alive(server):

                code = _exit_code(server)
                gone = server_output_note(_server_text(server), SERVER_GONE_HEAD, f"The server process exited (exit code {code}).",
                                          chars=min(SERVER_TAIL_CHARS, errors_limit))
            return StepResult(False, "E2E FAILED against the real application\nNo spec was run.\n"
                              + API_HANG_NOTE.format(path=API_LIVENESS_PATH, attempts=3, seconds=5) + gone)
        log(f"[verify] running playwright specs {only or specs} against port {port} ...")

        report = backend / f".arc-e2e-report-{port}.json"
        report.unlink(missing_ok=True)
        code, output = run_cmd(
            [npm_bin("npx"), "playwright", "test", *(only or specs), "--reporter=line,json", "--workers=1"],
            cwd=backend,
            timeout=timeout,
            env={"PLAYWRIGHT_BASE_URL": f"http://127.0.0.1:{port}", "PORT": str(port),
                 "PLAYWRIGHT_JSON_OUTPUT_NAME": str(report), "PLAYWRIGHT_JSON_OUTPUT_FILE": str(report)},
        )
        failing_files = failing_spec_files(report)
        failed_tests = failing_tests(report)
        ran = tests_ran(report)
        if code != 0:
            page_errors = page_errors_note(report)
            remember_error_contexts(report, backend)
            contexts = read_error_contexts(report, backend)
        exited = _exit_code(server)
    finally:
        server_log = server.stop()
        shutil.rmtree(backend / "test-results", ignore_errors=True)
        if report is not None:

            report.unlink(missing_ok=True)
        remove_dirs(data_dirs)

    cleaned = output if len(output) <= 14000 else output[:6000] + "\n... [middle of the output omitted] ...\n" + tail(output, 8000)
    if failing_files:
        cleaned = FAILED_FILES_HEAD + ", ".join(failing_files) + "\n" + cleaned
    if code != 0 and page_errors:
        cleaned = cleaned.rstrip("\n") + "\n\n" + page_errors + "\n"
    if code != 0:
        cleaned += server_errors_note(server_log, limit=errors_limit)
        if exited is not None:

            cleaned += server_output_note(server_log, SERVER_EXITED_HEAD, f"The server process exited (exit code {exited}) while the tests ran.",
                                          chars=min(SERVER_TAIL_CHARS, errors_limit))
    env_markers =("Executable doesn't exist", "browserType.launch", "Please run the following command to download new browsers", "Host system is missing dependencies")
    if code != 0 and any(marker in output for marker in env_markers):
        log("[verify] playwright browsers unavailable here; e2e skipped")
        return StepResult(True, cleaned, skipped=True)
    log(f"[verify] playwright exit={code}")
    if code != 0:
        failure_lines = [line for line in cleaned.splitlines() if any(k in line for k in ("Error", "expect(", "Locator:", "Expected", "Received", "Timeout", "›"))]
        log("[verify] playwright failures:\n" + "\n".join(failure_lines[-25:]))
    return StepResult(code == 0, cleaned, ran=ran, failed_tests=failed_tests, error_contexts=contexts)

NOT_RUN_HEAD = "NOT RUN SPEC FILES: "
NO_SPEC_RAN = ("backend did not become ready", "No spec was run.", "command not found: ")

def named_failures(output: str) -> list[str]:
    match = re.match(re.escape(FAILED_FILES_HEAD) + r"(.+)", output)
    return [name.strip() for name in match.group(1).split(",") if name.strip()] if match else []

def run_e2e(project_dir: Path, spec_files: list[str], log=print, shared_data: bool = False,
            until: float | None = None) -> StepResult:
    specs = [s for s in spec_files if s.startswith("backend/test-e2e/")]
    if specs and _until_passed(until):
        names = ", ".join(Path(s).name for s in specs)
        log(f"[verify] {len(specs)} spec file(s) not run: {UNTIL_NOTE}")
        return StepResult(False, f"{NOT_RUN_HEAD}{names}\n0 of {len(specs)} spec files passed\n"
                                 f"{len(specs)} spec file(s) not run: {UNTIL_NOTE}\n")
    if shared_data or len(specs) <= 1:
        result = (_run_e2e_once(project_dir, spec_files, log=log) if until is None
                  else _run_e2e_once(project_dir, spec_files, log=log, until=until))
        if until is not None and not result.ok and result.output.startswith(NOT_RUN_HEAD):
            return result
        if len(specs) == 1 and not result.ok and not result.skipped and not named_failures(result.output) \
                and not any(m in result.output for m in NO_SPEC_RAN):

            return StepResult(False, FAILED_FILES_HEAD + Path(specs[0]).name + "\n" + result.output, ran=result.ran,
                              infra=result.infra, not_run_tests=result.not_run_tests,
                              error_contexts=result.error_contexts)
        return result

    deadline = time.monotonic() + 600 + 90 * len(specs)
    results: list[tuple[str, StepResult]] = []
    stopped = ""
    unrun: list[str] = []
    down: StepResult | None = None
    global _check_deadline
    for position, spec in enumerate(specs):
        if time.monotonic() > deadline:
            unrun = specs[position:]
            stopped = f"{len(unrun)} spec file(s) not run: the time for this check is used up"
            break
        if _until_passed(until):
            unrun = specs[position:]
            stopped = f"{len(unrun)} spec file(s) not run: {UNTIL_NOTE}"
            break

        _check_deadline = deadline - ISOLATED_RUN_EST_SEC * (len(specs) - position - 1)
        try:
            result = (_run_e2e_once(project_dir, [spec], log=log) if until is None
                      else _run_e2e_once(project_dir, [spec], log=log, until=until))
        finally:
            _check_deadline = None
        if until is not None and not result.ok and result.output.startswith(NOT_RUN_HEAD):

            unrun = specs[position:]
            stopped = f"{len(unrun)} spec file(s) not run: {UNTIL_NOTE}"
            break
        if result.skipped:
            if all(r.ok for _, r in results):
                return result

            unrun = specs[position:]
            stopped = f"{len(unrun)} spec file(s) not run: {result.output.strip()[:200]}"
            break
        if not result.ok and not named_failures(result.output) and any(m in result.output for m in NO_SPEC_RAN):
            earlier = [Path(s).name for s, r in results if not r.ok]
            log(f"[verify] no spec could run against a fresh server ({Path(spec).name}); the check stops here")
            if not earlier or not result.infra:

                note = ("\nSpec files that failed before this: " + ", ".join(earlier)) if earlier else ""
                return StepResult(False, result.output + note, infra=result.infra)

            unrun = specs[position:]
            stopped = f"{len(unrun)} spec file(s) not run: this project's server did not start for {Path(spec).name}"
            down = result
            break
        results.append((spec, result))
        if len(results) == 2 and all(not r.ok and r.ran <= 0 and not named_failures(r.output) for _, r in results):

            log("[verify] the first two spec files ran no test at all; one fault of the run, the check stops here")
            return StepResult(False, "E2E FAILED against the real application\nNo spec was run: the first two spec files "
                              "ran no test at all.\n" + result.output, ran=0, infra=_common_infra([r for _, r in results]))
    failing: list[str] = []
    for spec, result in results:
        if not result.ok:
            for name in named_failures(result.output) or [Path(spec).name]:
                if name not in failing:
                    failing.append(name)
    parts = [f"## {Path(spec).name}\n{result.output}" for spec, result in results if not result.ok]
    body = "\n\n".join(parts) if parts else "(all spec files passed)"
    if len(body) > 14000:
        body = body[:6000] + "\n... [middle of the output omitted] ...\n" + tail(body, 8000)
    if down is not None:
        body += f"\n\n## {Path(unrun[0]).name} (not run: the server did not start)\n{down.output}"
    passed =[Path(spec).name for spec, result in results if result.ok]
    head = (FAILED_FILES_HEAD + ", ".join(failing) + "\n") if failing else ""

    head += (NOT_RUN_HEAD + ", ".join(Path(s).name for s in unrun) + "\n") if unrun else ""
    summary = f"{len(passed)} of {len(specs)} spec files passed, each run against fresh data"
    if passed and failing:
        summary += "\nPassed: " + ", ".join(passed)
    if stopped:
        summary += "\n" + stopped
    log(f"[verify] {summary.splitlines()[0]}" + (f"; failing: {failing}" if failing else "") + (f"; {stopped}" if stopped else ""))

    counts = [result.ran for _, result in results]
    ran = 0 if any(n == 0 for n in counts) else (sum(counts) if counts and all(n > 0 for n in counts) else -1)
    unrun_tests = [entry for _, result in results for entry in (result.not_run_tests or [])]
    return StepResult(not failing and not stopped, head + summary + "\n" + body, ran=ran,
                      infra=_common_infra([r for _, r in results]),
                      not_run_tests=unrun_tests or None, error_contexts=_merged_contexts([r for _, r in results]))

BROWSER_MISSING = ("Executable doesn't exist", "browserType.launch", "Please run the following command to download new browsers", "Host system is missing dependencies")

@dataclass
class OrderedRun:
    statuses: dict
    outputs: dict
    note: str = ""
    skipped: bool = False
    server_errors: str = ""

def run_e2e_in_order(project_dir: Path, spec_files: list[str], log=print, time_limit: float = 1800.0, per_file_timeout: int = 300) -> OrderedRun:
    specs = [s for s in spec_files if s.startswith("backend/test-e2e/")]
    statuses = {s: "not_run" for s in specs}
    outputs: dict = {}
    if not specs:
        return OrderedRun(statuses, outputs, "no e2e specs", skipped=True)
    backend = project_dir / "backend"
    ensure_installed(project_dir, "backend", log=log)
    if not (backend / "node_modules" / "@playwright" / "test").is_dir():
        return OrderedRun(statuses, outputs, "@playwright/test not installed", skipped=True)
    deadline = time.monotonic() + time_limit
    note = ""
    skipped = False
    server_log = ""

    env: dict = {}
    server = None
    report = None
    try:
        env = fresh_data_env(project_dir)
        port = free_port(3100)
        report = backend / f".arc-e2e-report-{port}.json"
        server = BackgroundProcess([npm_bin("npm"), "run", "start"], cwd=backend, env={"PORT": str(port), **env})
        ready, _ = wait_http(f"http://127.0.0.1:{port}/", timeout=60)
        if not ready or not server_alive(server):
            return OrderedRun(statuses, outputs, "the server did not come up on a port of its own", skipped=True)
        if not api_answers(f"http://127.0.0.1:{port}"):
            return OrderedRun(statuses, outputs, "the /api chain does not answer", skipped=True)
        for position, spec in enumerate(specs):
            left = deadline - time.monotonic()
            if left < 30:
                note = f"{len(specs) - position} spec file(s) not run: the time for the sweep is used up"
                break
            if not server_alive(server):
                note = f"the server exited after {Path(specs[position - 1]).name}; {len(specs) - position} spec file(s) not run"
                break
            report.unlink(missing_ok=True)
            code, output = run_cmd(
                [npm_bin("npx"), "playwright", "test", spec.split("/", 1)[1], "--reporter=line,json", "--workers=1"],
                cwd=backend,
                timeout=int(max(30, min(per_file_timeout, left))),
                env={"PLAYWRIGHT_BASE_URL": f"http://127.0.0.1:{port}", "PORT": str(port),
                     "PLAYWRIGHT_JSON_OUTPUT_NAME": str(report), "PLAYWRIGHT_JSON_OUTPUT_FILE": str(report)},
            )
            if code != 0 and any(marker in output for marker in BROWSER_MISSING):
                skipped, note = True, "playwright browsers unavailable"
                break
            failing = failing_spec_files(report)
            ran = tests_ran(report)
            if Path(spec).name in failing:
                statuses[spec] = "failed"
            elif code == 0 and ran > 0:
                statuses[spec] = "passed"
            else:
                statuses[spec] = "unknown"
            if statuses[spec] != "passed":
                outputs[spec] = tail(output, 3000)
    finally:
        if server is not None:
            server_log = server.stop()
        shutil.rmtree(backend / "test-results", ignore_errors=True)
        if report is not None:
            report.unlink(missing_ok=True)
        if env.get("DATA_DIR"):
            remove_dirs([env["DATA_DIR"]])
    errors = server_errors_note(server_log, limit=3000) if any(s == "failed" for s in statuses.values()) else ""
    return OrderedRun(statuses, outputs, note, skipped=skipped, server_errors=errors)

DEPENDENCY_PROBE_SETTING = "v162b_dependency_probe"
DEPENDENCY_PROBE_MIN_RUN_SEC = 30.0
DEPENDENCY_PROBE_TITLES = 6
_LOCATION_LINE = re.compile(r"([^\s/\\:]+\.spec\.[cm]?[jt]s):(\d+):(\d+)\s*›\s*(.+?)\s*(?:─+\s*)?$")

def _probe_location(red_location) -> tuple[str, int, str] | None:
    if isinstance(red_location, str):
        match = _LOCATION_LINE.search(red_location.strip())
        if not match:
            return None
        return match.group(1), int(match.group(2)), match.group(4).strip()
    if isinstance(red_location, (tuple, list)) and len(red_location) >= 4:
        return Path(str(red_location[0])).name, int(red_location[1] or 0), str(red_location[3]).strip()
    return None

def _probe_index(groups: list, line: int, title: str) -> int | None:
    for index, (group_line, tests) in enumerate(groups):
        if line and group_line == line and (not title or any(t == title for _, _, t in tests) or len(tests) == 1):
            return index
    hits = [index for index, (_, tests) in enumerate(groups) for _, _, t in tests if title and t == title]
    return hits[0] if len(hits) == 1 else None

def _red_alone(result: StepResult, name: str, line: int, title: str) -> bool:
    if result.ok or result.skipped or result.infra or not result.failed_tests:
        return False
    return any(entry[0] == name and int(entry[1]) == line and (not title or entry[3] == title)
               for entry in result.failed_tests)

def dependency_probe(project_dir: Path, spec: str, red_location, log=print, time_limit: float = 120.0,
                     confirm_alone: bool = True, until: float | None = None) -> dict | None:
    try:
        from . import settings
        if not settings.setting_on(DEPENDENCY_PROBE_SETTING, True):
            return None
        if not isolation_wanted() or not str(spec).startswith("backend/test-e2e/"):
            return None
        located = _probe_location(red_location)
        name = Path(spec).name
        if located is None or located[0] != name:
            return None
        _, line, title = located
        started = time.monotonic()
        groups = test_groups(project_dir, spec, log=log)
        if not groups or len(groups) < 2:
            return None
        index = _probe_index(groups, line, title)
        if not index:
            return None
        red_line, red_tests = groups[index]
        if not title:
            if len(red_tests) != 1:
                return None
            title = red_tests[0][2]
        rel = spec.split("/", 1)[1]
        chosen = groups[: index + 1]
        expected = sum(len(tests) for _, tests in chosen)

        def room() -> float:
            return time_limit - (time.monotonic() - started)

        if room() < DEPENDENCY_PROBE_MIN_RUN_SEC or _until_passed(until):
            return None
        log(f"[verify] dependency probe: {name} line {red_line} with the {index} test group(s) before it, on one fresh server and data set")
        together = _run_e2e_once(project_dir, [spec], log=log, only=[f"{rel}:{group_line}" for group_line, _ in chosen],
                                 errors_limit=3000, timeout=int(min(E2E_RUN_TIMEOUT_SEC, room())), **_until_kw(until))
        if not together.ok or together.skipped or together.infra or together.ran != expected:
            log(f"[verify] dependency probe: no verdict (ok={together.ok}, skipped={together.skipped}, ran={together.ran} of {expected})")
            return None
        if confirm_alone:
            if room() < DEPENDENCY_PROBE_MIN_RUN_SEC or _until_passed(until):
                log("[verify] dependency probe: no time left to re-run the test alone; no verdict")
                return None
            alone = _run_e2e_once(project_dir, [spec], log=log, only=[f"{rel}:{red_line}"], errors_limit=3000,
                                  timeout=int(min(E2E_RUN_TIMEOUT_SEC, room())), **_until_kw(until))
            if not _red_alone(alone, name, red_line, title):
                log("[verify] dependency probe: the test was not red again alone (a flake?); no verdict")
                return None
        predecessors = [t for _, tests in groups[:index] for _, _, t in tests]
        shown = ", ".join(f'"{t}"' for t in predecessors[:DEPENDENCY_PROBE_TITLES])
        if len(predecessors) > DEPENDENCY_PROBE_TITLES:
            shown += f" and {len(predecessors) - DEPENDENCY_PROBE_TITLES} more"
        message = (f'Test "{title}" ({name} line {red_line}) fails when it runs alone but passes after the earlier tests '
                   f"of its file ran ({shown}): it relies on data they created. Each test runs alone on a fresh server "
                   f"and fresh data -- start from the scenario's own GIVEN state, or create the record inside the test.")
        log(f"[verify] dependency probe: {name} line {red_line} depends on its predecessors (spec defect)")
        return {"title": title, "line": red_line, "predecessors": predecessors, "message": message}
    except Exception as error:
        try:
            log(f"[verify] dependency probe failed ({type(error).__name__}); no verdict")
        except Exception:
            pass
        return None
