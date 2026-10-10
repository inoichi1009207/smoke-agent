from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import role_ladder as js

CHECK = "session"
LOGIN_SEG = re.compile(r"^(?:login|log-in|signin|sign-in|sign_in|sessions?|authenticate)$", re.I)
LOGOUT_SEG = re.compile(r"^(?:logout|log-out|signout|sign-out|sign_out)$", re.I)
SINGLE_SESSION = re.compile(
    r"(?i)\b(?:only\s+one\s+(?:active\s+)?session|single\s+(?:active\s+)?session|one\s+active\s+session\s+per|"
    r"already\s+(?:has|holds|have)\s+an?\s+(?:active\s+|open\s+)?session[^.]{0,80}\b(?:refus|reject|den|block|fail)\w*|"
    r"(?:concurrent|simultaneous|parallel)\s+(?:sessions|sign-?ins|logins)\s+(?:are|is)\s+(?:not\s+allowed|refused|rejected|forbidden))")
QV = r"[`\"“]([^`\"”\n]{1,120})[`\"”]"
NOT_IDENTITY = re.compile(r"(?i)pass|remember|confirm|terms|agree|captcha|otp|code|token|redirect|next|csrf")

def _finding(where: str, requirement: str, problem: str, fix: str) -> dict[str, str]:
    return {"where": where, "requirement": requirement, "problem": problem, "fix": fix}

def credentials(requirement: str) -> list[dict[str, str | None]]:
    from .seed_identity import _seed_segments, credential_accounts, requirement_texts

    named = credential_accounts(requirement)
    counts: dict[tuple, int] = {}
    for text in requirement_texts(requirement):
        for seg in _seed_segments(text):
            pw = re.search(r"(?i)\bpassword\s+" + QV, seg)
            if not pw:
                continue
            acct = re.search(r"(?i)\b(?:account|user|username|login)\s+" + QV, seg)
            mail = re.search(r"(?i)\be-?mail\s+" + QV, seg)
            account = acct.group(1).strip() if acct else None
            if account is not None and account not in named:
                account = None
            email = mail.group(1).strip() if mail and "@" in mail.group(1) else None
            if not account and not email:
                continue
            key = (account, email, pw.group(1))
            counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])
    return [{"account": a, "email": e, "password": p} for (a, e, p), _ in ordered]

def session_sentence(requirement: str) -> str:
    sentences = js.requirement_sentences(requirement)
    pick = [s for s in sentences if re.search(r"(?i)\bsessions?\b", s)
            and re.search(r"(?i)\bsign(?:s|ed)?[- ]?in|\blog(?:s|ged)?[- ]?in|(?<!un)authenticat", s) and len(s) <= 700]

    pick.sort(key=lambda s: (0 if re.search(r"(?i)\bsession\W{0,2}\s+(?:is|means)\b", s) else 1,
                             0 if re.search(r"(?i)\bbrowser\b", s) else 1, len(s)))
    return pick[0][:300] if pick else ""

def _handler_text(f: js.JsFile, route: dict[str, Any]) -> str:
    a, b = route["args"]
    parts = [f.kept[a:b]]
    for name in route["handler_names"]:
        for fn in f.functions:
            if fn["name"] == name:
                parts.append(f.kept[fn["start"]:fn["end"]])
    return "\n".join(parts)

def body_keys(text: str) -> list[str]:
    keys: list[str] = []
    aliases = {"body"}
    for m in re.finditer(r"(?:const|let|var)\s+(" + js.IDENT + r")\s*=\s*(?:\(?\s*)?req\s*\.\s*body\b", text):
        aliases.add(m.group(1))
    alias = "|".join(re.escape(a) for a in aliases)
    for m in re.finditer(r"req\s*\.\s*body\s*\??\.\s*(" + js.IDENT + r")|(?<![\w$.])(?:" + alias + r")\s*\??\.\s*("
                         + js.IDENT + r")", text):
        key = m.group(1) or m.group(2)
        if key not in keys:
            keys.append(key)
    for m in re.finditer(r"(?:const|let|var)\s*\{([^{}]*)\}\s*=\s*(?:\(?\s*)?(?:req\s*\.\s*body|" + alias + r")\b", text):
        for part in m.group(1).split(","):
            pm = re.match(r"\s*(" + js.IDENT + r")", part)
            if pm and pm.group(1) not in keys:
                keys.append(pm.group(1))
    return [k for k in keys if k not in ("length", "toString")]

def backend_routes(project_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    files = js.backend_files(project_dir)
    prefixes = js.mount_prefixes(files)
    logins, logouts = [], []
    for f in files:
        for route in f.routes:
            last = [s for s in route["path"].split("/") if s]
            if not last or last[-1].startswith(":"):
                continue
            seg = last[-1]
            is_login = LOGIN_SEG.match(seg) and route["method"] == "post"
            is_logout = LOGOUT_SEG.match(seg) and route["method"] in ("post", "get", "delete")
            if not (is_login or is_logout):
                continue
            bases = prefixes.get(f.rel)
            if bases is None:
                continue
            for base in bases:
                entry = {"path": js.join_path(base, route["path"]), "method": route["method"],
                         "where": f"{f.rel}:{route['line']}", "file": f, "route": route}
                if is_login:
                    entry["keys"] = body_keys(_handler_text(f, route))
                    logins.append(entry)
                else:
                    logouts.append(entry)
    return logins, logouts

def frontend_login_keys(project_dir: Path) -> list[tuple[str, list[str]]]:
    root = Path(project_dir) / "frontend" / "src"
    out: list[tuple[str, list[str]]] = []
    if not root.is_dir():
        return out
    call = re.compile(r"\b(?:post\w*|request|fetch|send\w*|api\w*)\s*(?:<[^>()]*>)?\(\s*(['\"`])([^'\"`\n]*?/(?:login|log-in|signin|sign-in|sign_in|sessions?|authenticate))\1",
                      re.I)
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in js.SKIP_DIRS]
        for name in names:
            if not name.endswith((".ts", ".tsx", ".js", ".jsx")) or re.search(r"\.(?:test|spec)\.", name):
                continue
            try:
                text = Path(folder, name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            kept, masked = js.lex(text)
            for m in call.finditer(kept):
                tail = kept[m.end():m.end() + 400]
                obj = re.match(r"\s*,\s*(?:JSON\.stringify\(\s*)?\{", tail) or \
                    re.search(r"body\s*:\s*JSON\.stringify\(\s*\{", tail)
                if not obj:
                    continue
                open_pos = m.end() + obj.end() - 1
                close = js.match_close(masked, open_pos)
                if close < 0:
                    continue
                keys = []
                for a, b in js.split_top(kept, open_pos + 1, close, masked):
                    km = re.match(r"\s*(['\"]?)(" + js.IDENT + r")\1\s*(?::|$)", kept[a:b])
                    if km:
                        keys.append(km.group(2))
                if keys:
                    out.append((m.group(2), keys))
    return out

def body_variants(cred: dict[str, str | None], front: list[list[str]], back: list[str]) -> list[dict[str, str]]:
    user, email, password = cred["account"], cred["email"], cred["password"]
    variants: list[dict[str, str]] = []

    def build(keys: list[str], prefer_email: bool) -> dict[str, str] | None:
        pw_keys = [k for k in keys if re.search(r"(?i)pass", k) and not re.search(r"(?i)confirm|new|current|old", k)]
        id_keys = [k for k in keys if not NOT_IDENTITY.search(k)]
        if len(pw_keys) != 1 or not id_keys:
            return None
        body = {pw_keys[0]: password}
        for k in id_keys:
            if re.search(r"(?i)mail", k) and not re.search(r"(?i)user|name|login|ident", k):
                value = email or user
            else:
                value = (email or user) if prefer_email else (user or email)
            body[k] = value
        return body

    for keys in front:
        for prefer in (False, True):
            b = build(keys, prefer)
            if b and b not in variants:
                variants.append(b)
    if back:
        b = build(back, False)
        if b and b not in variants:
            variants.append(b)
    return variants[:3]

def _start_file(backend: Path) -> str | None:
    try:
        pkg = json.loads((backend / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pkg = {}
    start = str((pkg.get("scripts") or {}).get("start") or "")
    m = re.fullmatch(r"\s*node\s+([\w./\\-]+\.(?:c?js|mjs))\s*", start)
    if m and (backend / m.group(1)).is_file():
        return m.group(1)
    for cand in [str(pkg.get("main") or ""), "src/index.js", "src/server.js", "server.js", "index.js"]:
        if cand and (backend / cand).is_file():
            return cand
    return None

class _Answer:
    def __init__(self, status: int, cookies: dict[str, str], body: str) -> None:
        self.status, self.cookies, self.body = status, cookies, body

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def _request(base: str, method: str, path: str, body: dict | None, cookies: dict[str, str], timeout: float) -> _Answer:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method.upper())
    if data is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if cookies:
        req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()))
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(req, timeout=max(1.0, timeout))
        status, headers, text = resp.status, resp.headers, resp.read(4000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status, headers = exc.code, exc.headers
        try:
            text = exc.read(4000).decode("utf-8", "replace")
        except Exception:
            text = ""
    got: dict[str, str] = {}
    for raw in (headers.get_all("Set-Cookie") or []) if headers else []:
        m = re.match(r"\s*([^=;\s]+)=([^;]*)", raw)
        if not m:
            continue
        expired = re.search(r"(?i)max-age=0\b|expires=Thu, 01 Jan 1970", raw)
        if m.group(2) and not expired:
            got[m.group(1)] = m.group(2)
    return _Answer(status, got, " ".join(text.split())[:200])

ENDS_OTHERS = re.compile(
    r"(?i)(?<!not )(?<!n't )\b(?:sign(?:ing|s)?[- ]?in|log(?:ging|s)?[- ]?in)\b[^.]{0,100}?(?<!not )(?<!n't )"
    r"\b(?:ends|terminates|invalidates|revokes|signs\s+out|logs\s+out|closes|replaces)\b[^.]{0,40}?"
    r"\b(?:other|previous|existing|earlier|older|any\s+other)\s+(?:active\s+)?sessions?\b")
ME_SEG = re.compile(r"^(?:me|whoami|current-?user|current_user|session|user|account|profile)$", re.I)
CLEANUP_RESERVE = 8.0
MIN_START = 5.0

def whoami_routes(project_dir: Path) -> list[dict[str, Any]]:
    files = js.backend_files(project_dir)
    prefixes = js.mount_prefixes(files)
    front_text = ""
    root = Path(project_dir) / "frontend" / "src"
    if root.is_dir():
        for folder, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in js.SKIP_DIRS]
            for name in names:
                if name.endswith((".ts", ".tsx", ".js", ".jsx")) and len(front_text) < 3_000_000:
                    try:
                        front_text += Path(folder, name).read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        pass
    out = []
    for f in files:
        for route in f.routes:
            segs = [s for s in route["path"].split("/") if s]
            if route["method"] != "get" or not segs or not ME_SEG.match(segs[-1]) or len(segs) > 3:
                continue
            for base in prefixes.get(f.rel, []):
                path = js.join_path(base, route["path"])
                named = ("'" + route["path"] in front_text or '"' + route["path"] in front_text
                         or "`" + route["path"] in front_text or path in front_text)
                out.append({"path": path, "where": f"{f.rel}:{route['line']}", "named": named})
    out.sort(key=lambda r: (not r["named"], len(r["path"])))
    return out[:3]

def session_findings(requirement: str, project_dir: Path, time_limit: float = 60.0, log=print,
                     node_modules: str | None = None) -> list[dict[str, str]]:
    return session_check(requirement, project_dir, time_limit=time_limit, log=log, node_modules=node_modules)[0]

def session_check(requirement: str, project_dir: Path, time_limit: float = 60.0, log=print,
                  node_modules: str | None = None) -> tuple[list[dict[str, str]], bool]:
    deadline = time.time() + max(0.0, time_limit)
    try:
        return _run(requirement, Path(project_dir), deadline, log, node_modules)
    except Exception as exc:
        log(f"[{CHECK}] (diagnostic) crashed: {type(exc).__name__}: {exc}")
        return [], False

class _OutOfTime(Exception):
    pass

def _copy_backend(backend: Path, dest: Path, deadline: float) -> None:
    ignore = shutil.ignore_patterns("node_modules", "*.db", "*.db-wal", "*.db-shm", "*.sqlite", "*.sqlite3",
                                    ".arc-test-db", "test-results", "playwright-report", ".git", "test-e2e")

    def copy(src, dst, *, follow_symlinks=True):
        if time.time() > deadline:
            raise _OutOfTime()
        return shutil.copy2(src, dst, follow_symlinks=follow_symlinks)

    shutil.copytree(backend, dest, ignore=ignore, copy_function=copy)

def _stop(server, timeout: float) -> None:
    import subprocess

    from .fsutil import IS_WINDOWS, stop_group, unregister_child

    stopped = False
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(server.process.pid), "/T", "/F"], capture_output=True,
                           timeout=max(3.0, timeout))
        else:
            pgid = getattr(server, "pgid", None)
            stop_group(server.process, pgid if pgid is not None else server.process.pid, grace=min(5.0, max(0.5, timeout / 2)))
        stopped = True
    except Exception:
        pass
    if not stopped and IS_WINDOWS:

        import threading

        pid = str(server.process.pid)
        threading.Thread(target=lambda: subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True),
                         name="session-probe-stop", daemon=True).start()
    if stopped or not IS_WINDOWS:
        unregister_child(server.process)
    try:
        server._log.close()
    except Exception:
        pass

def _run(requirement: str, project_dir: Path, deadline: float, log, node_modules: str | None
         ) -> tuple[list[dict[str, str]], bool]:
    creds = credentials(requirement)
    if not creds:
        return [], True
    flat = " ".join(requirement.split())
    if SINGLE_SESSION.search(flat):
        log(f"[{CHECK}] (diagnostic) the requirement itself limits sessions: no probe")
        return [], True
    kick_check = not ENDS_OTHERS.search(flat)
    if not kick_check:
        log(f"[{CHECK}] (diagnostic) the requirement says a new sign-in ends other sessions: no kick-out check")
    backend = project_dir / "backend"
    nm = node_modules or str(backend / "node_modules")
    if not os.path.isdir(os.path.join(nm, "express")):
        log(f"[{CHECK}] (diagnostic) skipped: no node_modules/express")
        return [], False
    node = shutil.which("node")
    start = _start_file(backend)
    if not node or not start:
        log(f"[{CHECK}] (diagnostic) skipped: no node or no start file")
        return [], False
    logins, logouts = backend_routes(project_dir)
    front = frontend_login_keys(project_dir)
    candidates: list[dict[str, Any]] = []
    for entry in logins:
        fkeys = [k for p, k in front if entry["path"].endswith(p.split("?")[0]) or p.endswith(entry["path"])]
        candidates.append({**entry, "front": fkeys})
    if not candidates:
        log(f"[{CHECK}] (diagnostic) no sign-in route found in the backend sources")
        return [], False
    candidates.sort(key=lambda c: (not c["front"], len(c["path"])))
    candidates = candidates[:2]
    whoami = whoami_routes(project_dir) if kick_check else []
    from .fsutil import BackgroundProcess, api_answers

    port = pick_port()
    if not port:
        log(f"[{CHECK}] (diagnostic) skipped: no free port")
        return [], False
    reserve = min(CLEANUP_RESERVE, max(0.0, deadline - time.time()) * 0.25)
    work_end = deadline - reserve
    if work_end - time.time() < MIN_START:

        log(f"[{CHECK}] (diagnostic) too little time left to start the app: not run")
        return [], False
    work = Path(tempfile.mkdtemp(prefix="arc-session-probe-"))
    server = None
    try:
        copy = work / "backend"
        try:
            _copy_backend(backend, copy, work_end)
        except _OutOfTime:
            log(f"[{CHECK}] (diagnostic) out of time while copying the backend")
            return [], False
        data = work / "data"
        data.mkdir()
        env = {"PORT": str(port), "NODE_PATH": nm, "DATA_DIR": str(data), "ARC_DB_FILE": str(data / "database.db"),
               "DATABASE_FILE": str(data / "database.db")}
        server = BackgroundProcess([node, start], cwd=copy, env=env)
        base = f"http://127.0.0.1:{port}"
        while True:
            left = work_end - time.time()
            if left < 2 or not server.alive():
                log(f"[{CHECK}] (diagnostic) the app did not answer in time ({server.output_so_far()[-200:]!r})")
                return [], False
            if api_answers(base, seconds=min(2.0, left - 1), attempts=1):
                break
            time.sleep(0.5)

        time.sleep(0.5)
        if not server.alive() or "EADDRINUSE" in server.output_so_far():
            log(f"[{CHECK}] (diagnostic) port {port} is held by another process: no probe")
            return [], False
        return _probe(base, candidates, logouts, whoami, creds, requirement, work_end, log)
    except _OutOfTime:
        log(f"[{CHECK}] (diagnostic) out of time")
        return [], False
    finally:
        if server is not None:
            _stop(server, max(1.0, deadline - time.time() - 1))
        while True:
            shutil.rmtree(work, ignore_errors=True)
            if not work.exists() or time.time() > deadline:
                break
            time.sleep(0.3)
        if work.exists():
            _remove_later(work)

def _remove_later(work: Path, seconds: float = 60.0) -> None:
    import threading

    def run() -> None:
        end = time.time() + seconds
        while work.exists() and time.time() < end:
            shutil.rmtree(work, ignore_errors=True)
            time.sleep(0.5)

    threading.Thread(target=run, name="session-probe-cleanup", daemon=True).start()

PORTS = range(4880, 4890)

def _port_taken(port: int) -> bool:
    import socket

    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.3)
                if sock.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    for family, host in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.bind((host, port))
        except OSError as exc:
            if family == socket.AF_INET6 and getattr(exc, "errno", None) in (97, 10047, 10049):
                continue
            return True
    return False

def pick_port() -> int | None:
    shift = 0
    base = os.environ.get("AGENT_PORT_BASE", "").strip()
    if base.isdigit():
        shift = int(base) - 3000
    for port in PORTS:
        if not _port_taken(port + shift):
            return port + shift
    return None

def _left(deadline: float) -> float:
    return max(0.0, deadline - time.time())

def _call(base: str, method: str, path: str, body, cookies, deadline: float) -> _Answer:
    left = _left(deadline)
    if left < 1.0:
        raise _OutOfTime()
    return _request(base, method, path, body, cookies, min(10.0, left))

def _probe(base: str, candidates: list[dict[str, Any]], logouts: list[dict[str, Any]], whoami: list[dict[str, Any]],
           creds: list[dict], requirement: str, deadline: float, log) -> tuple[list[dict[str, str]], bool]:
    chosen = None
    for attempt_round in (1, 2):
        for cand in candidates:
            for cred in creds[:2]:
                for body in body_variants(cred, cand["front"], cand["keys"]):
                    ans = _call(base, "POST", cand["path"], body, {}, deadline)
                    if 200 <= ans.status < 300 and ans.cookies:
                        chosen = (cand, body, ans)
                        break
                    log(f"[{CHECK}] (diagnostic) sign-in {cand['path']} keys {sorted(body)} -> {ans.status}"
                        f"{' (no cookie)' if 200 <= ans.status < 300 else ''}")
                if chosen:
                    break
            if chosen:
                break
        if chosen or attempt_round == 2:
            break
        time.sleep(min(2.0, _left(deadline)))
    if not chosen:
        return [], False
    cand, body, first = chosen
    provisioned = _provisioned_accounts(base, cand, body, creds, requirement, deadline, log)

    me = None
    for route in whoami:
        with_cookie = _call(base, "GET", route["path"], None, first.cookies, deadline)
        without = _call(base, "GET", route["path"], None, {}, deadline)
        if 200 <= with_cookie.status < 300 and without.status in (401, 403):
            me = route
            break
    second = _call(base, "POST", cand["path"], body, {}, deadline)
    if 200 <= second.status < 300:
        if me is None:
            log(f"[{CHECK}] (diagnostic) second browser sign-in -> {second.status}; no who-am-I route to check the "
                "first session")
            return provisioned, True
        after1 = _call(base, "GET", me["path"], None, first.cookies, deadline)
        after2 = _call(base, "GET", me["path"], None, second.cookies, deadline)
        if after1.status in (401, 403) and 200 <= after2.status < 300:
            return provisioned + [_kicked_out(cand, me, body, first, second, after1, requirement)], True
        log(f"[{CHECK}] (diagnostic) second browser sign-in -> {second.status}, first session still "
            f"{after1.status}: fine")
        return provisioned, True
    if not (400 <= second.status < 500):
        log(f"[{CHECK}] (diagnostic) second browser sign-in -> {second.status}: not a refusal")
        return provisioned, True

    control = _call(base, "POST", cand["path"], body, {}, deadline)
    if 200 <= control.status < 300:
        log(f"[{CHECK}] (diagnostic) refusal lifted by itself ({second.status} then {control.status}): not reported")
        return provisioned, True
    for out in logouts[:2]:
        bye = _call(base, out["method"], out["path"], {} if out["method"] != "get" else None, first.cookies, deadline)
        if not (200 <= bye.status < 400):
            continue
        third = _call(base, "POST", cand["path"], body, {}, deadline)
        if not (200 <= third.status < 300):
            log(f"[{CHECK}] (diagnostic) refused again after sign-out ({third.status}): not the open session")
            return provisioned, True
        return provisioned + [_refused(cand, body, first, second, control, out, bye, third, requirement)], True
    log(f"[{CHECK}] (diagnostic) second browser sign-in refused ({second.status}) but no sign-out could confirm the "
        "open session as the cause (a rate limit or lockout reads the same): not reported")
    return provisioned, False

def _provisioned_accounts(base: str, cand: dict[str, Any], body: dict[str, str], creds: list[dict], requirement: str,
                          deadline: float, log) -> list[dict[str, str]]:
    used = creds[0] if creds else {}
    for c in creds[:2]:
        if c.get("password") in body.values() and ((c.get("account") or "") in body.values() or (c.get("email") or "") in body.values()):
            used = c
            break
    refused: list[str] = []
    tried = 0
    for cred in creds:
        if cred is used or _left(deadline) < 3:
            continue
        other: dict[str, str] = {}
        for key, value in body.items():
            if value == used.get("password"):
                other[key] = cred.get("password") or ""
            elif used.get("email") and value == used.get("email"):
                other[key] = cred.get("email") or cred.get("account") or ""
            elif used.get("account") and value == used.get("account"):
                other[key] = cred.get("account") or cred.get("email") or ""
            else:
                other[key] = value
        if not all(other.values()):
            continue
        tried += 1
        ans = _call(base, "POST", cand["path"], other, {}, deadline)
        if ans.status in (400, 401, 403):
            refused.append(f"`{cred.get('account') or cred.get('email')}` -> {ans.status}")
        elif not (200 <= ans.status < 300):
            log(f"[{CHECK}] (diagnostic) provisioned account {cred.get('account') or cred.get('email')} -> {ans.status}")
    if tried:
        log(f"[{CHECK}] provisioned accounts: {tried} tried, {len(refused)} refused")
    if not refused:
        return []
    return [_finding(
        f"{CHECK}: provisioned accounts ({cand['path']})", "(the accounts the requirement says the system pre-provisions)",
        f"The requirement states these accounts with their passwords as already existing, but signing in with the stated "
        f"password is refused ({len(refused)} of {tried}): " + ", ".join(refused[:12]) + ". The same request signs "
        f"`{used.get('account') or used.get('email')}` in, so the accounts themselves are missing or differ from the "
        "statement (name, email, password, verified state, role). Every scenario that starts signed in as one of them fails "
        "at its first step.",
        "Seed every account the requirement states (\"pre-provisions the verified account `x` with email `..` and password "
        "`..`\", \"Seed values: account ..\") exactly as stated -- name, email, password hashed like registration does, "
        "verified, and the stated role / membership -- idempotently at start-up; check each one signs in.")]

def _shown(body: dict[str, str]) -> str:
    return json.dumps({k: ("<password>" if re.search(r"(?i)pass", k) else v) for k, v in body.items()})

def _refused(cand, body, first, second, control, out, bye, third, requirement) -> dict[str, str]:
    refusal = _refusal_line(cand)
    problem = (f"The same account cannot sign in from a second browser while its first session is open: POST "
               f"{cand['path']} {_shown(body)} from a fresh cookie jar answered {first.status} with a session cookie "
               f"({', '.join(sorted(first.cookies))}); the identical request from a second fresh cookie jar, without "
               f"signing out, answered {second.status} {second.body[:80]!r} (and {control.status} again). After jar 1 "
               f"signed out ({out['method'].upper()} {out['path']} -> {bye.status}) the same request from a third fresh "
               f"jar got {third.status}: the refusal comes from the first session still being open."
               + (f" The handler refuses at {refusal}." if refusal else ""))
    return _finding(
        f"{CHECK}: {cand['where']}",
        session_sentence(requirement),
        problem,
        "Every successful sign-in creates a NEW session for that browser, whatever other sessions the account holds: "
        "remove the refusal of an account that already has an active session (and do not end the other sessions "
        "either); signing out ends only the session of the browser that signs out.",
    )

def _kicked_out(cand, me, body, first, second, after1, requirement) -> dict[str, str]:
    return _finding(
        f"{CHECK}: {cand['where']}",
        session_sentence(requirement),
        f"Signing the same account in from a second browser ends the first browser's session: POST {cand['path']} "
        f"{_shown(body)} from cookie jar 1 answered {first.status} and GET {me['path']} with jar 1 answered 2xx; the "
        f"identical sign-in from a fresh jar 2 answered {second.status}, and afterwards GET {me['path']} with jar 1 "
        f"answered {after1.status} while jar 2 is signed in. A browser (a test) that is still working is signed out "
        f"by another one signing in.",
        "A sign-in creates its own session and leaves the account's other sessions alone: do not delete, deactivate "
        "or replace the other sessions (or a per-account token) when an account signs in; only Sign out ends a "
        "session, and only that browser's.",
    )

def _refusal_line(cand: dict[str, Any]) -> str:
    f, route = cand["file"], cand["route"]
    spans = [route["args"]] + [(fn["start"], fn["end"]) for fn in f.functions if fn["name"] in route["handler_names"]]
    for a, b in spans:
        body = f.kept[a:b]
        for m in re.finditer(r"(?i)session", body):
            line_start = body.rfind("\n", 0, m.start()) + 1
            line = body[line_start:body.find("\n", m.start())]
            if re.search(r"(?i)create|insert|clear|destroy|cookie|set", line):
                continue
            window = body[m.start():m.start() + 400]
            if re.search(r"status\s*\(\s*4\d\d", window) and re.search(r"(?i)active|exist|already|SELECT", line):
                return f"{f.rel}:{f.line(a + m.start())} `{line.strip()[:120]}`"
    return ""
